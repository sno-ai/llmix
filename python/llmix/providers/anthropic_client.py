"""Asynchronous Anthropic Client Implementation

Provides async Anthropic API support with:
- Messages API via anthropic.AsyncAnthropic
- System message extraction (Anthropic API: system param, not in messages)
- Native prompt caching via cache_control injection
- Extended thinking (budgeted thinking tokens)
- Exponential backoff retry logic
- Error classification (retryable vs non-retryable)
"""

import asyncio
import logging
import random
import threading
from typing import Any

import anthropic
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)

from llmix.providers.base import BaseLLMClient, LLMResponse

_DEFAULT_MAX_OUTPUT_TOKENS = 16384
_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_DEFAULT_TIMEOUT = 300

logger = logging.getLogger(__name__)


def _get_anthropic_api_key() -> str:
    """Return Anthropic API key from environment."""
    import os

    return os.getenv("ANTHROPIC_API_KEY", "")


def _jittered_delay(delay: float) -> float:
    """Add jitter to retry delay."""
    return delay * (0.5 + random.random() * 0.5)


def _build_retry_config() -> dict[str, Any]:
    """Build retry configuration from environment."""
    from llmix.env import (
        get_llm_retry_attempts,
        get_llm_retry_delay_seconds,
        get_llm_retry_max_delay,
        get_llm_retry_multiplier,
    )

    return {
        "attempts": get_llm_retry_attempts(),
        "initial_delay": get_llm_retry_delay_seconds(),
        "multiplier": get_llm_retry_multiplier(),
        "max_delay": get_llm_retry_max_delay(),
    }


def _extract_system_message(
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, str]]]:
    """Extract system message from messages list.

    Anthropic API requires system as a separate parameter, not in the messages array.

    Returns:
        Tuple of (system_text, filtered_messages)
    """
    system_text: str | None = None
    filtered: list[dict[str, str]] = []

    for msg in messages:
        if msg.get("role") == "system":
            # Concatenate multiple system messages
            content = msg.get("content", "")
            system_text = f"{system_text}\n{content}" if system_text else content
        else:
            filtered.append(msg)

    return system_text, filtered


def _extract_usage(response: Any) -> dict[str, int]:
    """Extract usage from Anthropic response, including cache metrics."""
    usage: dict[str, int] = {}

    if not hasattr(response, "usage") or response.usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    resp_usage = response.usage
    input_tokens = getattr(resp_usage, "input_tokens", 0) or 0
    output_tokens = getattr(resp_usage, "output_tokens", 0) or 0

    usage["input_tokens"] = input_tokens
    usage["output_tokens"] = output_tokens
    usage["total_tokens"] = input_tokens + output_tokens

    # Anthropic cache metrics
    cache_creation = getattr(resp_usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(resp_usage, "cache_read_input_tokens", 0) or 0
    if cache_creation > 0:
        usage["cache_creation_input_tokens"] = cache_creation
    if cache_read > 0:
        usage["cached_input_tokens"] = cache_read

    return usage


def _extract_content(response: Any) -> str:
    """Extract text content from Anthropic response."""
    if not hasattr(response, "content") or not response.content:
        return ""

    parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)

    return "".join(parts)


def _inject_cache_control(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inject cache_control on the last user message for native prompt caching.

    Anthropic's prompt caching requires cache_control: {"type": "ephemeral"}
    on the content blocks that should be cached.
    """
    if not messages:
        return messages

    # Find the last user message
    result = [dict(m) for m in messages]
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            content = result[i].get("content", "")
            # If content is a string, convert to content block format
            if isinstance(content, str):
                result[i] = {
                    **result[i],
                    "content": [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            elif isinstance(content, list):
                # Content is already blocks - add cache_control to the last block
                new_content = list(content)
                if new_content:
                    last_block = dict(new_content[-1])
                    last_block["cache_control"] = {"type": "ephemeral"}
                    new_content[-1] = last_block
                result[i] = {**result[i], "content": new_content}
            break

    return result


def classify_anthropic_error(error: Exception) -> bool:
    """Classify whether an Anthropic error is retryable.

    Returns:
        True if retryable (429, 5xx, connection, timeout), False otherwise (4xx).
    """
    if isinstance(error, RateLimitError):
        return True
    if isinstance(error, InternalServerError):
        return True
    if isinstance(error, APIConnectionError):
        return True
    if isinstance(error, APITimeoutError):
        return True

    # Check for HTTP status code on generic API errors
    status = getattr(error, "status_code", None)
    if status is not None:
        if status == 429 or status >= 500:
            return True
        if 400 <= status < 500:
            return False

    return False


class AsyncAnthropicClient(BaseLLMClient):
    """Asynchronous Anthropic Client Implementation

    Provides async Anthropic Messages API support with:
    - System message extraction
    - Native prompt caching via cache_control
    - Extended thinking support
    - Exponential backoff retry logic
    """

    DEFAULT_TIMEOUT = _DEFAULT_TIMEOUT

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        default_headers: dict[str, str] | None = None,
        caching_strategy: str | None = None,
        **kwargs: Any,
    ):
        if model is None:
            model = _DEFAULT_MODEL
        if temperature is None:
            temperature = 0.7

        super().__init__(model=model, **kwargs)
        self.default_temperature: float = float(temperature)
        self.api_key = api_key or _get_anthropic_api_key()
        self.base_url = base_url
        self._default_headers = default_headers
        self._caching_strategy = caching_strategy

        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )

        self._client: AsyncAnthropic | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> AsyncAnthropic:
        """Lazy load AsyncAnthropic client."""
        if self._client is None:
            with self._client_lock:
                if self._client is not None:
                    return self._client

                client_params: dict[str, Any] = {"api_key": self.api_key}
                if self.base_url:
                    client_params["base_url"] = self.base_url
                if self._default_headers:
                    client_params["default_headers"] = self._default_headers

                self._client = AsyncAnthropic(**client_params)

        return self._client

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Execute async Anthropic message completion.

        Handles system message extraction, native caching, and extended thinking.
        """
        model = self.get_model(model)
        resolved_max_tokens = self._get_max_tokens(max_tokens)
        resolved_temperature = temperature if temperature is not None else self.default_temperature

        # Extract system message (Anthropic requires it as separate param)
        system_text, filtered_messages = _extract_system_message(messages)

        # Inject cache_control for native prompt caching
        if self._caching_strategy == "native":
            filtered_messages = _inject_cache_control(filtered_messages)

        # Build API kwargs
        api_kwargs: dict[str, Any] = {
            "model": model,
            "messages": filtered_messages,
            "max_tokens": resolved_max_tokens,
            "temperature": resolved_temperature,
        }

        if system_text is not None:
            # For native caching, wrap system in cache-controlled block
            if self._caching_strategy == "native":
                api_kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                api_kwargs["system"] = system_text

        # Extended thinking support
        thinking_config = kwargs.get("thinking")
        if thinking_config and thinking_config.get("type") == "enabled":
            budget = thinking_config.get("budget_tokens", 10000)
            api_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            # Anthropic requires temperature=1 for extended thinking
            api_kwargs["temperature"] = 1.0

        # Provider options passthrough
        if "max_tokens" in kwargs:
            api_kwargs["max_tokens"] = kwargs["max_tokens"]

        return await self._execute_with_retry(api_kwargs, model)

    async def call(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Simplified call interface matching task spec."""
        return await self.chat_completion(messages, model=model, **kwargs)

    async def _execute_with_retry(
        self, api_kwargs: dict[str, Any], model: str
    ) -> LLMResponse:
        """Execute API call with retry logic and exponential backoff."""
        retry_config = _build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.messages.create(**api_kwargs)

                content = _extract_content(response)
                usage = _extract_usage(response)

                logger.info(
                    "Anthropic %s attempt %d: input=%d output=%d",
                    model,
                    attempt,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

                return LLMResponse(
                    content=content,
                    usage=usage,
                    model=getattr(response, "model", model),
                    success=True,
                )

            except Exception as err:
                is_retryable = classify_anthropic_error(err)
                if not is_retryable:
                    logger.error("Non-retryable Anthropic error: %s", err)
                    return self._handle_error(err, model)

                if attempt == retry_config["attempts"]:
                    logger.error(
                        "Anthropic generation failed after %d attempts: %s",
                        retry_config["attempts"],
                        err,
                    )
                    return self._handle_error(err, model)

                logger.warning(
                    "Anthropic attempt %d failed: %s. Retrying in %.1fs...",
                    attempt,
                    err,
                    delay,
                )
                await asyncio.sleep(_jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        return self._handle_error(Exception("Unexpected error in Anthropic completion"), model)

    async def response_completion(
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
    ) -> LLMResponse:
        """Response API interface (Anthropic uses Messages API internally).

        Maps OpenAI-style response_completion to Anthropic Messages API.
        """
        model = self.get_model(model)
        resolved_temperature = temperature if temperature is not None else self.default_temperature
        max_tokens = max_output_tokens or _DEFAULT_MAX_OUTPUT_TOKENS

        # Build messages from input
        if isinstance(input, str):
            messages: list[dict[str, str]] = [{"role": "user", "content": input}]
        else:
            messages = input

        return await self.chat_completion(
            messages=messages,
            model=model,
            temperature=resolved_temperature,
            max_tokens=max_tokens,
        )

    def _get_default_model(self) -> str:
        return _DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> "AsyncAnthropicClient":
        """Create Anthropic client from environment variables."""
        return cls()

    async def close(self) -> None:
        """Close underlying AsyncAnthropic HTTP client."""
        client_to_close: AsyncAnthropic | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None

        if client_to_close is not None:
            await client_to_close.close()

    def __str__(self) -> str:
        return f"AsyncAnthropicClient(model={self.default_model}, base_url={self.base_url})"
