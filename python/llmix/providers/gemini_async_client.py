"""
Asynchronous Gemini Client Implementation (Native Async)

Uses google-genai SDK's native async API (client.aio.models.generate_content())
for non-blocking Gemini calls in FastAPI and async contexts.

Timeout Configuration:
- Inherits DEFAULT_LLM_TIMEOUT from config
- Timeout is applied at client initialization (not per-request)
- google-genai SDK uses milliseconds for timeout (converted internally)
"""

import asyncio
import logging
from typing import Any

from llmix.providers._config import DEFAULT_GEMINI_FLASH_MODEL, DEFAULT_LLM_TIMEOUT
from llmix.providers.base import _DEFAULT_MAX_OUTPUT_TOKENS, BaseLLMClient, LLMResponse
from llmix.providers.gemini_common import (
    GEMINI_GENERATION_CONFIG_FIELDS,
    ToolCall,
    build_retry_config,
    convert_openai_tools_to_gemini,
    extract_function_calls_from_response,
    extract_text_from_response,
    jittered_delay,
    reformat_messages_for_gemini,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
except ImportError as exc:
    raise ImportError("The 'google-genai' library is required. Please install it using 'pip install google-genai'.") from exc


class AsyncGeminiClient(BaseLLMClient):
    """
    Native Async Gemini Client

    Uses google-genai SDK's async API (client.aio.models.generate_content())
    for true non-blocking I/O. Suitable for FastAPI and async contexts.
    """

    # Default model from config
    DEFAULT_MODEL = DEFAULT_GEMINI_FLASH_MODEL

    # Default timeout from centralized config (milliseconds for google-genai SDK)
    DEFAULT_TIMEOUT_MS = DEFAULT_LLM_TIMEOUT * 1000

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        default_headers: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        """
        Initialize async Gemini client.

        Args:
            api_key: Google API key
            base_url: Custom base URL
            model: Default Gemini model
            temperature: Default generation temperature
            default_headers: Custom default headers for all requests
            **kwargs: Other configuration parameters
        """
        if model is None:
            model = self.DEFAULT_MODEL
        super().__init__(model=model, **kwargs)

        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self._default_headers = default_headers

        if not self.api_key:
            raise ValueError("Google API key is required. Set GEMINI_API_KEY environment variable or pass api_key parameter.")

        # Lazy initialization of genai.Client
        self._genai_client: genai.Client | None = None

        logger.info("AsyncGeminiClient initialized with model: %s", model)

    @property
    def client(self) -> genai.Client:
        """Lazy load genai.Client."""
        if self._genai_client is None:
            base_url = self.base_url

            http_opts: dict[str, Any] = {"timeout": self.DEFAULT_TIMEOUT_MS}
            if base_url:
                http_opts["base_url"] = base_url
            if self._default_headers:
                http_opts["headers"] = self._default_headers

            self._genai_client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(**http_opts))
            if base_url:
                logger.info("Initialized async Gemini client with gateway: %s", base_url)
            else:
                logger.info("Initialized async Gemini client with default endpoint")

        return self._genai_client

    async def chat_completion(  # type: ignore[override]  # async override of sync base
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Execute async Gemini chat completion.

        Args:
            messages: List of conversation messages
            model: Model name, uses default_model if None
            temperature: Generation temperature
            max_tokens: Maximum number of tokens
            **kwargs: Other parameters (tools, tool_choice, response_schema, etc.)

        Returns:
            LLMResponse: Response result
        """
        model = self.get_model(model)
        temperature = temperature if temperature is not None else self.temperature
        resolved_max_tokens = self._get_max_tokens(max_tokens)

        try:
            # Reformat messages for Gemini API
            system_instruction, contents = reformat_messages_for_gemini(messages)

            # Build generation config
            config_dict: dict[str, Any] = {}
            if system_instruction:
                config_dict["system_instruction"] = system_instruction

            config_dict["temperature"] = temperature
            config_dict["max_output_tokens"] = resolved_max_tokens

            # Convert OpenAI-style tools to Gemini format
            if "tools" in kwargs:
                gemini_tools = convert_openai_tools_to_gemini(kwargs["tools"])
                if gemini_tools:
                    config_dict["tools"] = gemini_tools
                    logger.info("Converted %d OpenAI tools to Gemini format", len(kwargs["tools"]))

            # Handle tool_choice (Gemini uses tool_config)
            if "tool_choice" in kwargs and kwargs["tool_choice"] == "auto":
                logger.debug("Tool choice 'auto' handled - using Gemini's default behavior")

            # Convert response_schema (Pydantic model) to response_json_schema (dict)
            if "response_schema" in kwargs:
                try:
                    from pydantic import BaseModel  # pylint: disable=import-outside-toplevel

                    from llmix.providers.gemini_common import convert_pydantic_to_gemini_schema  # pylint: disable=import-outside-toplevel

                    response_schema_value = kwargs["response_schema"]
                    if isinstance(response_schema_value, type) and issubclass(response_schema_value, BaseModel):
                        cleaned_schema = convert_pydantic_to_gemini_schema(response_schema_value)
                        kwargs["response_json_schema"] = cleaned_schema
                        del kwargs["response_schema"]
                        logger.info("Converted response_schema (Pydantic) to response_json_schema for Gemini API")
                except ImportError:
                    logger.warning("Could not import schema conversion utilities - response_schema may fail")

            # Add remaining kwargs, filtering to supported config fields
            alias_map = {"maxOutputTokens": "max_output_tokens", "systemInstruction": "system_instruction"}
            for key, value in kwargs.items():
                if key in {"temperature", "max_tokens", "tools", "tool_choice"}:
                    continue
                normalized_key = alias_map.get(key, key)
                if normalized_key in GEMINI_GENERATION_CONFIG_FIELDS:
                    config_dict[normalized_key] = value

            # Prepare API call arguments
            gen_kwargs: dict[str, Any] = {"model": str(model), "contents": contents}
            if config_dict:
                gen_kwargs["config"] = types.GenerateContentConfig(**config_dict)

            # Execute with retry logic
            content_text, tool_calls = await self._execute_with_retry(gen_kwargs, model)

            response_data: dict[str, Any] = {
                "content": content_text,
                "usage": {},  # Gemini doesn't provide token counts
                "model": model,
                "success": True,
            }

            if tool_calls:
                response_data["tool_calls"] = tool_calls

            return LLMResponse(**response_data)

        except Exception as e:
            logger.error("Gemini API call failed: %s", e)
            return self._handle_error(e, model)

    async def response_completion(  # type: ignore[override]  # async override of sync base
        self,
        input: str | list[dict[str, Any]],
        model: str | None = None,
        instructions: str | None = None,
        text: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        parallel_tool_calls: bool | None = None,
        allowed_tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        store: bool | None = None,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Map Response API interface to Chat Completions API for Gemini (async).

        Gemini doesn't support the OpenAI Response API, so this method converts
        response_completion() parameters to chat_completion() format.
        """
        # Log warning for unsupported params
        if tools:
            logger.warning(
                "[Gemini] response_completion: 'tools' parameter ignored (not mapped to chat_completion). "
                "Use chat_completion directly for tool calling."
            )
        if text is not None and not (isinstance(text, dict) and text.get("format") is not None):
            logger.debug("[Gemini] response_completion received 'text' without a mappable 'format' key.")
        _ = tool_choice, parallel_tool_calls, allowed_tools, store, reasoning_effort

        # Build messages from input + instructions
        messages: list[dict[str, str]] = []

        if instructions:
            messages.append({"role": "system", "content": instructions})

        if isinstance(input, str):
            messages.append({"role": "user", "content": input})
        elif isinstance(input, list):
            for msg in input:
                if not isinstance(msg, dict):
                    logger.warning("[Gemini] Skipping non-dict input item")
                    continue
                role = msg.get("role")
                if role not in {"system", "user", "assistant", "tool"}:
                    logger.warning("[Gemini] Skipping unsupported message shape: keys=%s", list(msg.keys()))
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    messages.append({"role": role, "content": content})
                elif isinstance(content, list):
                    text_parts: list[str] = []
                    for part in content:
                        if isinstance(part, str):
                            text_parts.append(part)
                        elif isinstance(part, dict) and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                    if text_parts:
                        messages.append({"role": role, "content": "\n".join(text_parts)})
                    else:
                        logger.warning("[Gemini] response_completion: message with role '%s' has no text content parts", role)

        max_tokens = max_output_tokens if max_output_tokens is not None else _DEFAULT_MAX_OUTPUT_TOKENS
        resolved_temperature = temperature if temperature is not None else self.temperature

        # Guard: require at least one non-system message with actual content
        has_user_text = any(m["role"] != "system" and m.get("content", "").strip() for m in messages)
        if not messages or not has_user_text:
            return self._handle_error(ValueError("No non-system text content after input conversion"), model or self._get_default_model())

        # Map text.format (structured output) to response_format if supported
        if text and isinstance(text, dict) and text.get("format"):
            response_format_value = text.get("format")
            if isinstance(response_format_value, str):
                kwargs.setdefault("response_format", {"type": response_format_value})
            elif isinstance(response_format_value, dict):
                kwargs.setdefault("response_format", response_format_value)

        return await self.chat_completion(messages=messages, model=model, temperature=resolved_temperature, max_tokens=max_tokens, **kwargs)

    async def _execute_with_retry(self, gen_kwargs: dict[str, Any], model: str) -> tuple[str, list[ToolCall] | None]:
        """Execute async API call with retry logic and exponential backoff."""
        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                # Native async call via client.aio.models.generate_content()
                response = await self.client.aio.models.generate_content(**gen_kwargs)

                content_text = extract_text_from_response(response)
                tool_calls = extract_function_calls_from_response(response)

                logger.info("Gemini %s async completion successful (attempt %d)", model, attempt)
                if tool_calls:
                    logger.info("Gemini response includes %d tool calls", len(tool_calls))

                return content_text, tool_calls

            except Exception as err:
                from google.genai.errors import ClientError, ServerError  # pylint: disable=import-outside-toplevel

                if isinstance(err, ClientError):
                    logger.error("Non-retryable Gemini error: %s", err)
                    raise

                is_retryable = isinstance(err, (ServerError, TimeoutError, ConnectionError, OSError))
                if not is_retryable:
                    logger.error("Non-retryable Gemini error: %s", err)
                    raise

                if attempt == retry_config["attempts"]:
                    logger.error("Gemini async generation failed after %d attempts: %s", retry_config["attempts"], err)
                    raise

                logger.warning("Gemini async attempt %d failed: %s. Retrying in %ss...", attempt, err, delay)
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        raise RuntimeError("Unexpected error in async Gemini completion")

    def _get_default_model(self) -> str:
        """Get Gemini default model."""
        return self.DEFAULT_MODEL

    def get_model(self, model: str | None = None) -> str:
        """Get model name with fallback to default."""
        return model or self.default_model or self._get_default_model()

    def __repr__(self) -> str:
        """String representation."""
        return f"AsyncGeminiClient(model={self.get_model()}, temperature={self.temperature})"
