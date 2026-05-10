"""
DeepInfra Client Implementation

Provides async DeepInfra API support via OpenAI-compatible Chat Completions API.
DeepInfra does NOT support the Response API, so response_completion() maps to chat_completion().

Supports thinking mode control for models like Qwen3 via extra_body parameters.
"""

import asyncio
import logging
import threading
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

from llmix.provider_urls import DEEPINFRA_BASE_URL
from llmix.providers.base import BaseLLMClient, LLMResponse
from llmix.providers.openai_common import build_retry_config, jittered_delay

logger = logging.getLogger(__name__)

# Models that require explicit thinking mode control (thinking enabled by default on DeepInfra)
THINKING_DISABLED_MODELS = {"Qwen/Qwen3-32B", "Qwen/Qwen3-14B", "zai-org/GLM-4.7-Flash"}

# Models that support thinking mode control at all (superset of THINKING_DISABLED_MODELS)
# Only these models accept enable_thinking / thinking_token_budget in extra_body
# WARNING: Do NOT add Qwen/Qwen3-235B-A22B-Instruct-2507 — it is a non-thinking variant;
# sending enable_thinking:false causes garbled output (test-results.md Phase 4 finding).
THINKING_CAPABLE_MODELS = THINKING_DISABLED_MODELS


class DeepInfraClient(BaseLLMClient):
    """
    Async DeepInfra Client via OpenAI-compatible Chat Completions API.

    DeepInfra does not support OpenAI's Response API, so response_completion()
    maps parameters to chat_completion() format internally.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEEPINFRA_BASE_URL,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        default_headers: dict[str, str] | None = None,
        http_client: Any | None = None,
        **kwargs: Any,
    ):
        super().__init__(model=model, **kwargs)
        if not api_key:
            raise ValueError("DeepInfra API key is required. Set DEEPINFRA_API_KEY environment variable or pass api_key parameter.")
        self.api_key = api_key
        self.base_url = base_url
        # Note: enable_thinking is a per-client setting (baked into the instance at construction).
        # Different enable_thinking values get separate cached clients via _get_provider_client().
        # Per-call overrides via call_kwargs are NOT supported — the instance value always wins.
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self._default_headers = default_headers
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-initialize AsyncOpenAI client for DeepInfra (thread-safe)."""
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is None:
                kwargs: dict[str, Any] = {
                    "api_key": self.api_key,
                    "base_url": self.base_url,
                    "max_retries": 0,
                    "default_headers": self._default_headers or {},
                }
                if self._http_client is not None:
                    kwargs["http_client"] = self._http_client
                self._client = AsyncOpenAI(**kwargs)
            return self._client

    async def chat_completion(  # type: ignore[override]  # async override of sync base
        self, messages: list[dict[str, str]], model: str | None = None, temperature: float = 0.0, max_tokens: int = 4096, **kwargs: Any
    ) -> LLMResponse:
        """Execute DeepInfra chat completion."""
        model = self.get_model(model)

        api_kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}

        # Apply thinking mode control for supported models
        extra_body = self._build_extra_body(model)
        if extra_body:
            api_kwargs["extra_body"] = extra_body

        # Pass through standard OpenAI-compatible parameters
        for param in ("top_p", "stop", "seed", "response_format", "presence_penalty", "frequency_penalty"):
            if param in kwargs:
                api_kwargs[param] = kwargs[param]

        # Pass top_k via extra_body (not a standard OpenAI param, but supported by DeepInfra/vLLM)
        if "top_k" in kwargs:
            if "extra_body" not in api_kwargs:
                api_kwargs["extra_body"] = {}
            api_kwargs["extra_body"]["top_k"] = kwargs["top_k"]

        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.chat.completions.create(**api_kwargs)

                if not response.choices:
                    return self._handle_error(Exception("DeepInfra returned no choices"), model)

                content = response.choices[0].message.content or ""
                usage = self._extract_usage(response)

                return LLMResponse(content=content, usage=usage, model=response.model or model, success=True)

            except Exception as err:
                is_retryable = isinstance(err, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
                if not is_retryable:
                    logger.error("DeepInfra non-retryable error: %s", err)
                    return self._handle_error(err, model)

                if attempt == retry_config["attempts"]:
                    logger.error("DeepInfra chat completion failed after %d attempts: %s", retry_config["attempts"], err)
                    return self._handle_error(err, model)

                logger.warning("DeepInfra attempt %d failed: %s. Retrying in ~%ss...", attempt, err, f"{delay:.0f}")
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        return self._handle_error(Exception("Unexpected error in DeepInfra completion"), model)

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
        Map Response API interface to Chat Completions API for DeepInfra.

        DeepInfra doesn't support the Response API, so this method converts
        response_completion() parameters to chat_completion() format.
        """
        # Log warning for params that DeepInfra response_completion doesn't support
        if tools:
            logger.warning(
                "[DeepInfra] response_completion: 'tools' parameter ignored (not mapped to chat_completion). Use chat_completion directly for tool calling."
            )
        if text is not None and not (isinstance(text, dict) and text.get("format") is not None):
            logger.debug("[DeepInfra] response_completion received 'text' without a mappable 'format' key.")
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
                    logger.warning("[DeepInfra] Skipping non-dict input item")
                    continue
                role = msg.get("role")
                if role not in {"system", "user", "assistant", "tool"}:
                    logger.warning("[DeepInfra] Skipping unsupported message shape: keys=%s", list(msg.keys()))
                    continue
                content = msg.get("content", "")
                if isinstance(content, str):
                    messages.append({"role": role, "content": content})
                elif isinstance(content, list):
                    # Multi-part content - extract only text parts, skip non-text (image, etc.)
                    text_parts: list[str] = []
                    for part in content:
                        if isinstance(part, str):
                            text_parts.append(part)
                        elif isinstance(part, dict) and part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                        # Non-text parts (image_url, etc.) are intentionally skipped
                    if text_parts:
                        messages.append({"role": role, "content": "\n".join(text_parts)})
                    else:
                        logger.warning(
                            "[DeepInfra] response_completion: message with role '%s' has no text content parts — message dropped (DeepInfra is text-only)",
                            role,
                        )
                else:
                    logger.warning("[DeepInfra] Unsupported content type for role=%s: %s", role, type(content).__name__)

        max_tokens = max_output_tokens if max_output_tokens is not None else 4096
        resolved_temperature = temperature if temperature is not None else 0.0

        # Guard: require at least one non-system message with actual content
        has_user_text = any(m["role"] != "system" and m.get("content", "").strip() for m in messages)
        if not messages or not has_user_text:
            return self._handle_error(ValueError("No non-system text content after input conversion"), model or self._get_default_model())

        # Map text.format (structured output) to response_format if supported
        if text and isinstance(text, dict) and text.get("format"):
            # Text format mapping: convert to DeepInfra's response_format schema
            response_format_value = text.get("format")
            if isinstance(response_format_value, str):
                # Simple format string (e.g., "json_object")
                kwargs.setdefault("response_format", {"type": response_format_value})
            elif isinstance(response_format_value, dict):
                # Schema object passed directly
                kwargs.setdefault("response_format", response_format_value)

        return await self.chat_completion(messages=messages, model=model, temperature=resolved_temperature, max_tokens=max_tokens, **kwargs)

    def _build_extra_body(self, model: str) -> dict[str, Any] | None:
        """Build extra_body for thinking mode and budget control."""
        if model in THINKING_DISABLED_MODELS and not self.enable_thinking:
            return {"chat_template_kwargs": {"enable_thinking": False}}
        if self.enable_thinking:
            if model not in THINKING_CAPABLE_MODELS:
                logger.warning(
                    "[DeepInfra] enable_thinking=True requested for model %s which is not in THINKING_CAPABLE_MODELS — skipping thinking control",
                    model,
                )
                return None
            body: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": True}}
            if self.thinking_budget is not None:
                body["thinking_token_budget"] = self.thinking_budget
            return body
        return None

    def _extract_usage(self, response: Any) -> dict[str, int]:
        """Extract usage dict from DeepInfra response."""
        usage = response.usage
        if not usage:
            return {}
        result = {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        # Extract cached tokens from vLLM automatic prefix caching (APC)
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = getattr(details, "cached_tokens", 0) or 0
            if cached:
                result["cached_input_tokens"] = cached
        return result

    def _get_default_model(self) -> str:
        """Default model for DeepInfra."""
        return "Qwen/Qwen3-32B"

    async def close(self) -> None:
        """Close underlying AsyncOpenAI HTTP client and release resources."""
        client_to_close: AsyncOpenAI | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None

        if client_to_close is not None:
            await client_to_close.close()
