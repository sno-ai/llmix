"""
GPU Client Implementation

Provides async on-prem GPU inference via OpenAI-compatible Chat Completions API.
Intended for self-hosted inference servers behind a load balancer.
The API does NOT support the Response API, so response_completion() maps to chat_completion().

Supports thinking mode control via enable_thinking flag, sent explicitly on every
request to ensure deterministic behavior with models that default to thinking mode.
"""

import asyncio
import logging
import threading
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, InternalServerError, RateLimitError

from llmix.provider_urls import SNO_GPU_BASE_URL
from llmix.providers.base import BaseLLMClient, LLMResponse
from llmix.providers.openai_common import build_retry_config, jittered_delay

logger = logging.getLogger(__name__)

GPU_DEFAULT_MODEL = "qwen3.6-27b-extract"
VALID_GPU_PATHS = frozenset({"extract", "reason"})


def build_gpu_base_url(gpu_path: str | None = None) -> str:
    """Build GPU base URL with optional path routing.

    Args:
        gpu_path: 'extract', 'reason', or None for the default path.

    Returns:
        Full base URL for AsyncOpenAI (e.g. https://llm.example.com/extract/v1).
    """
    if not SNO_GPU_BASE_URL:
        raise ValueError("GPU_BASE_URL is required for sno-gpu provider.")
    if gpu_path:
        if len(gpu_path) > 256 or gpu_path not in VALID_GPU_PATHS:
            raise ValueError(f"Invalid gpu_path: {gpu_path!r}. Must be one of {sorted(VALID_GPU_PATHS)!r}")
        return f"{SNO_GPU_BASE_URL}/{gpu_path}/v1"
    return f"{SNO_GPU_BASE_URL}/v1"


class SnoGpuClient(BaseLLMClient):
    """
    Async on-prem GPU Client via OpenAI-compatible Chat Completions API.

    Authenticates with X-Sno-LLM-Key header (SNO_LLM_API_KEY).
    The API does not support OpenAI's Response API, so response_completion()
    maps parameters to chat_completion() format internally.

    Supports optional path-based routing via gpu_path:
    - /extract/v1 → path="extract"
    - /reason/v1  → path="reason"
    - /v1         → no path (default)
    """

    def __init__(
        self,
        service_secret: str,
        model: str = GPU_DEFAULT_MODEL,
        base_url: str | None = None,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        default_headers: dict[str, str] | None = None,
        http_client: Any | None = None,
        **kwargs: Any,
    ):
        super().__init__(model=model, **kwargs)
        if not service_secret:
            raise ValueError("Sno GPU API key is required. Set SNO_LLM_API_KEY or pass service_secret.")
        self._service_secret = service_secret
        self.base_url = base_url or build_gpu_base_url()
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        # Merge auth header AFTER caller-supplied headers so auth always wins
        self._default_headers = {**(default_headers or {}), "X-Sno-LLM-Key": service_secret}
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-initialize AsyncOpenAI client for GPU inference (thread-safe)."""
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is None:
                kwargs: dict[str, Any] = {
                    "api_key": "not-used",
                    "base_url": self.base_url,
                    "max_retries": 0,
                    "default_headers": self._default_headers,
                    # 960s > Caddy hard limit (900s) — transport must never kill before infra does
                    "timeout": httpx.Timeout(960.0, connect=10.0),
                }
                if self._http_client is not None:
                    kwargs["http_client"] = self._http_client
                else:
                    # Bound connection pool so 502 bursts don't exhaust file descriptors
                    # and half-open connections drain within keepalive_expiry on shutdown.
                    kwargs["http_client"] = httpx.AsyncClient(
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=5.0),
                        timeout=httpx.Timeout(960.0, connect=10.0),
                    )
                self._client = AsyncOpenAI(**kwargs)
            return self._client

    async def chat_completion(  # type: ignore[override]  # async override of sync base
        self, messages: list[dict[str, str]], model: str | None = None, temperature: float = 0.0, max_tokens: int = 4096, **kwargs: Any
    ) -> LLMResponse:
        """Execute GPU chat completion."""
        model = self.get_model(model)

        api_kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}

        # Apply thinking mode control (always sent — Qwen3.5 defaults to thinking=True)
        extra_body = self._build_extra_body()
        if extra_body:
            api_kwargs["extra_body"] = extra_body

        # Pass through standard OpenAI-compatible parameters
        for param in ("top_p", "stop", "seed", "response_format", "presence_penalty", "frequency_penalty"):
            if param in kwargs:
                api_kwargs[param] = kwargs[param]
        if "timeout" in kwargs and kwargs["timeout"] is not None:
            timeout = kwargs["timeout"]
            api_kwargs["timeout"] = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(float(timeout))

        # Pass top_k and min_p via extra_body (llama.cpp extensions, not standard OpenAI params)
        for ext_param in ("top_k", "min_p"):
            if ext_param in kwargs:
                api_kwargs.setdefault("extra_body", {})
                api_kwargs["extra_body"][ext_param] = kwargs[ext_param]

        retry_config = build_retry_config()
        delay = retry_config["initial_delay"]

        for attempt in range(1, retry_config["attempts"] + 1):
            try:
                response = await self.client.chat.completions.create(**api_kwargs)

                if not response.choices:
                    return self._handle_error(Exception("GPU returned no choices"), model)

                msg = response.choices[0].message
                content = msg.content or ""
                # vLLM with --reasoning-parser emits thinking in `reasoning`
                # (newer) or `reasoning_content` (legacy). Wrap in <think>...</think>
                # so downstream consumers can strip uniformly.
                reasoning_text: str | None = None
                for attr in ("reasoning_content", "reasoning"):
                    val = getattr(msg, attr, None)
                    if isinstance(val, str) and val.strip():
                        reasoning_text = val
                        break
                if reasoning_text and "<think>" not in content:
                    content = f"<think>{reasoning_text}</think>{content}"

                # Surface budget-exhaustion as retryable error rather than empty success.
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                visible = content
                if visible.startswith("<think>") and "</think>" in visible:
                    visible = visible.split("</think>", 1)[1]
                if not visible.strip() and finish_reason == "length":
                    return self._handle_error(
                        Exception(
                            f"Empty content with finish_reason=length — reasoning exhausted token budget "
                            f"(model={response.model or model}, enable_thinking={self.enable_thinking})"
                        ),
                        model,
                    )

                usage = self._extract_usage(response)

                return LLMResponse(content=content, usage=usage, model=response.model or model, success=True)

            except Exception as err:
                is_retryable = isinstance(err, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError))
                if not is_retryable:
                    logger.error("GPU non-retryable error: %s", err)
                    return self._handle_error(err, model)

                if attempt == retry_config["attempts"]:
                    logger.error("GPU chat completion failed after %d attempts: %s", retry_config["attempts"], err)
                    return self._handle_error(err, model)

                logger.warning("GPU attempt %d failed: %s. Retrying in ~%ss...", attempt, err, f"{delay:.0f}")
                await asyncio.sleep(jittered_delay(delay))
                delay = min(delay * retry_config["multiplier"], retry_config["max_delay"])

        return self._handle_error(Exception("Unexpected error in GPU completion"), model)

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
        Map Response API interface to Chat Completions API for GPU.

        The GPU API doesn't support the Response API, so this method converts
        response_completion() parameters to chat_completion() format.
        """
        if tools or tool_choice is not None or parallel_tool_calls is not None or allowed_tools is not None:
            return self._handle_error(
                ValueError("GPU response_completion does not support tool calling; use chat_completion instead."), model or self._get_default_model()
            )
        if text is not None and not (isinstance(text, dict) and text.get("format") is not None):
            logger.debug("[GPU] response_completion received 'text' without a mappable 'format' key.")
        _ = store, reasoning_effort

        # Build messages from input + instructions
        messages: list[dict[str, str]] = []

        if instructions:
            messages.append({"role": "system", "content": instructions})

        if isinstance(input, str):
            messages.append({"role": "user", "content": input})
        elif isinstance(input, list):
            for msg in input:
                if not isinstance(msg, dict):
                    logger.warning("[GPU] Skipping non-dict input item")
                    continue
                role = msg.get("role")
                if role not in {"system", "user", "assistant", "tool"}:
                    logger.warning("[GPU] Skipping unsupported message shape: keys=%s", list(msg.keys()))
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
                            "[GPU] response_completion: message with role '%s' has no text content parts — message dropped (GPU is text-only)", role
                        )
                else:
                    logger.warning("[GPU] Unsupported content type for role=%s: %s", role, type(content).__name__)

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

    def _build_extra_body(self) -> dict[str, Any]:
        """
        Build extra_body for thinking mode control.

        Send both the legacy top-level keys and the vLLM>=0.8
        chat_template_kwargs form. This keeps older on-prem deployments
        working during rollouts while still using the newer structure.
        """
        body: dict[str, Any] = {"enable_thinking": self.enable_thinking}
        chat_kwargs: dict[str, Any] = {"enable_thinking": self.enable_thinking}
        if self.enable_thinking and self.thinking_budget is not None:
            body["thinking_budget"] = self.thinking_budget
            chat_kwargs["thinking_budget"] = self.thinking_budget
        body["chat_template_kwargs"] = chat_kwargs
        return body

    def _extract_usage(self, response: Any) -> dict[str, int]:
        """Extract usage dict from GPU response."""
        usage = response.usage
        if not usage:
            return {}
        result = {
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        # Extract cached tokens if present (llama.cpp prefix caching)
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = getattr(details, "cached_tokens", 0) or 0
            if cached:
                result["cached_input_tokens"] = cached
        return result

    def _get_default_model(self) -> str:
        """Default model for GPU inference."""
        return GPU_DEFAULT_MODEL

    async def close(self) -> None:
        """Close underlying AsyncOpenAI HTTP client and release resources."""
        client_to_close: AsyncOpenAI | None = None
        with self._client_lock:
            if self._client is not None:
                client_to_close = self._client
                self._client = None

        if client_to_close is not None:
            await client_to_close.close()


GpuClient = SnoGpuClient
