"""
Together AI Client Implementation

Provides async Together AI API support via OpenAI-compatible Chat Completions API.
Together does NOT support the Response API, so response_completion() maps to chat_completion().
"""

import asyncio
import logging
import threading
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

from llmix.provider_urls import TOGETHER_BASE_URL
from llmix.providers.base import BaseLLMClient, LLMResponse
from llmix.providers.openai_common import build_retry_config, jittered_delay

logger = logging.getLogger(__name__)


class TogetherClient(BaseLLMClient):
    """
    Async Together AI Client via OpenAI-compatible Chat Completions API.

    Together does not support OpenAI's Response API, so response_completion()
    maps parameters to chat_completion() format internally.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = TOGETHER_BASE_URL,
        default_headers: dict[str, str] | None = None,
        http_client: Any | None = None,
        **kwargs: Any,
    ):
        super().__init__(model=model, **kwargs)
        if not api_key:
            raise ValueError("Together API key is required. Set TOGETHER_API_KEY environment variable or pass api_key parameter.")
        self.api_key = api_key
        self.base_url = base_url
        self._default_headers = default_headers
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-initialize AsyncOpenAI client for Together (thread-safe)."""
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
        """Execute Together AI chat completion."""
        model = self.get_model(model)

        api_kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}

        # Pass through standard OpenAI-compatible parameters
        for param in ("top_p", "stop", "seed", "response_format", "presence_penalty", "frequency_penalty"):
            if param in kwargs:
                api_kwargs[param] = kwargs[param]

        # Pass top_k via extra_body (not a standard OpenAI param, but supported by Together)
        if "top_k" in kwargs:
            api_kwargs.setdefault("extra_body", {})["top_k"] = kwargs["top_k"]

        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.chat.completions.create(**api_kwargs)

                if not response.choices:
                    return self._handle_error(Exception("Together returned no choices"), model)

                content = response.choices[0].message.content or ""
                usage = self._extract_usage(response)

                return LLMResponse(content=content, usage=usage, model=response.model or model, success=True)

            except Exception as err:
                is_retryable = isinstance(err, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
                if not is_retryable:
                    logger.error("Together non-retryable error: %s", err)
                    return self._handle_error(err, model)

                if attempt == retry_config["attempts"]:
                    logger.error("Together chat completion failed after %d attempts: %s", retry_config["attempts"], err)
                    return self._handle_error(err, model)

                logger.warning("Together attempt %d failed: %s. Retrying in ~%ss...", attempt, err, f"{delay:.0f}")
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        return self._handle_error(Exception("Unexpected error in Together completion"), model)

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
        Map Response API interface to Chat Completions API for Together.

        Together doesn't support the Response API, so this method converts
        response_completion() parameters to chat_completion() format.
        """
        # Log warning for params that Together response_completion doesn't support
        if tools:
            logger.warning(
                "[Together] response_completion: 'tools' parameter ignored (not mapped to chat_completion). Use chat_completion directly for tool calling."
            )
        if text is not None and not (isinstance(text, dict) and text.get("format") is not None):
            logger.debug("[Together] response_completion received 'text' without a mappable 'format' key.")
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
                    logger.warning("[Together] Skipping non-dict input item")
                    continue
                role = msg.get("role")
                if role not in {"system", "user", "assistant", "tool"}:
                    logger.warning("[Together] Skipping unsupported message shape: keys=%s", list(msg.keys()))
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
                            "[Together] response_completion: message with role '%s' has no text content parts — message dropped (Together is text-only)",
                            role,
                        )

        max_tokens = max_output_tokens if max_output_tokens is not None else 4096
        resolved_temperature = temperature if temperature is not None else 0.0

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

    def _extract_usage(self, response: Any) -> dict[str, int]:
        """Extract usage dict from Together response."""
        usage = response.usage
        if not usage:
            return {}
        result = {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        # Extract cached tokens from prompt_tokens_details
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = getattr(details, "cached_tokens", 0) or 0
            if cached:
                result["cached_input_tokens"] = cached
        return result

    def _get_default_model(self) -> str:
        """Default model for Together."""
        return "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"

    async def close(self) -> None:
        """Close underlying AsyncOpenAI HTTP client and release resources."""
        client_to_close: AsyncOpenAI | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None

        if client_to_close is not None:
            await client_to_close.close()
