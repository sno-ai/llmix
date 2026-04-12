"""
LLMix v2 Call Pipeline

Orchestrates the 19-step call flow using feature modules:
  1. Kill Switch -> 2. Config -> 3-4. Cache -> 5. Circuit Breaker ->
  6. Singleflight -> [7-16 Retry Loop] -> 17. Thinking strip ->
  18. Cache write -> 19. Telemetry

The provider dispatch (step 11) is a caller-supplied callback.
This module is pure orchestration -- no direct LLM SDK dependency.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

from llmix.adaptive_semaphore import AdaptiveSemaphore, parse_openai_ratelimit_headers
from llmix.key_pool import KeyPool
from llmix.provider_kwargs import PROVIDER_KWARGS_REGISTRY, TransformKwargsCallback, TransformKwargsContext, apply_transform_kwargs
from llmix.resilience import CircuitBreaker, CircuitOpenError, FileLock, KillSwitch, RetryPolicy, Singleflight, is_retryable
from llmix.response_cache import TwoTierCache, generate_cache_key, should_skip_cache
from llmix.thinking import strip_thinking

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Error with optional HTTP status and headers."""

    def __init__(self, message: str, *, status_code: int | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers


@dataclass
class ProviderResult:
    """Result returned from the provider dispatch callback."""

    content: str
    model: str
    usage: LLMUsage
    headers: dict[str, str] | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class LLMUsage:
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class DispatchContext(Protocol):
    """Context passed into the provider dispatch callback (structural)."""

    provider: str
    model: str
    api_key: str
    messages: list[dict[str, Any]]
    kwargs: dict[str, Any]
    config: dict[str, Any]


@dataclass
class DispatchInput:
    """Concrete dispatch context."""

    provider: str
    model: str
    api_key: str
    messages: list[dict[str, Any]]
    kwargs: dict[str, Any]
    config: dict[str, Any]


ProviderDispatchFn = Callable[[DispatchInput], Awaitable[ProviderResult]]


@dataclass
class V2CallInput:
    """Input for a single call through the pipeline."""

    config: dict[str, Any]
    messages: list[dict[str, Any]]
    singleflight_key: str | None = None


@dataclass
class V2CallResponse:
    """Full response from the v2 pipeline."""

    content: str
    model: str
    provider: str
    usage: LLMUsage
    success: bool
    error: str | None = None
    thinking_content: str | None = None
    cache_hit: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class V2PipelineConfig:
    """Configuration for the v2 pipeline."""

    dispatch: ProviderDispatchFn
    max_retries: int = 3
    retry_base_ms: int = 1_000
    retry_max_delay_ms: int = 30_000
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 30.0
    semaphore_initial: int = 32
    semaphore_min: int = 4
    kill_switch_state_dir: str | None = None
    transform_kwargs_overrides: dict[str, TransformKwargsCallback] | None = None
    response_cache: TwoTierCache | None = None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class V2CallPipeline:
    """Orchestrates the 19-step v2 call flow."""

    def __init__(self, config: V2PipelineConfig) -> None:
        self._dispatch = config.dispatch
        from pathlib import Path

        state_dir = Path(config.kill_switch_state_dir) if config.kill_switch_state_dir else None
        self._kill_switch = KillSwitch(state_dir=state_dir)
        self._singleflight = Singleflight()
        self._retry_policy = RetryPolicy(max_retries=config.max_retries, base_ms=config.retry_base_ms, max_delay_ms=config.retry_max_delay_ms)
        self._file_lock = FileLock()

        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._semaphores: dict[str, AdaptiveSemaphore] = {}
        self._key_pools: dict[str, KeyPool] = {}

        self._cb_threshold = config.circuit_breaker_threshold
        self._cb_cooldown = config.circuit_breaker_cooldown_seconds
        self._sem_initial = config.semaphore_initial
        self._sem_min = config.semaphore_min

        self._transform_kwargs: dict[str, TransformKwargsCallback] = {**PROVIDER_KWARGS_REGISTRY, **(config.transform_kwargs_overrides or {})}

        self._response_cache = config.response_cache

    def set_key_pool(self, provider: str, pool: KeyPool) -> None:
        """Register a key pool for a provider."""
        self._key_pools[provider] = pool

    def close(self) -> None:
        """Release pipeline-owned resources."""
        if self._response_cache is not None:
            self._response_cache.close()

    def _get_circuit_breaker(self, provider: str, base_url: str = "") -> CircuitBreaker:
        key = f"{provider}:{base_url}"
        cb = self._circuit_breakers.get(key)
        if cb is None:
            cb = CircuitBreaker(provider, base_url, failure_threshold=self._cb_threshold, cooldown_seconds=self._cb_cooldown)
            self._circuit_breakers[key] = cb
        return cb

    def _get_semaphore(self, provider: str) -> AdaptiveSemaphore:
        sem = self._semaphores.get(provider)
        if sem is None:
            sem = AdaptiveSemaphore(self._sem_initial, self._sem_min)
            self._semaphores[provider] = sem
        return sem

    def _select_api_key_locked(self, provider: str) -> str:
        """Read key-pool state under the cross-process lock in one blocking call."""
        with self._file_lock:
            pool = self._key_pools.get(provider)
            if not pool:
                raise ValueError(f'No API key pool for provider "{provider}". Set {provider.upper()}_API_KEY or {provider.upper()}_KEYS.')
            return pool.select()

    def _rotate_api_key_locked(self, provider: str) -> None:
        """Advance key-pool state under the cross-process lock."""
        with self._file_lock:
            pool = self._key_pools.get(provider)
            if pool is not None:
                pool.rotate()

    def _mark_api_key_dead_locked(self, provider: str, api_key: str) -> None:
        """Mark a failed key dead under the cross-process lock."""
        with self._file_lock:
            pool = self._key_pools.get(provider)
            if pool is not None:
                pool.mark_dead(api_key)

    def _build_request_kwargs(self, config: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Build provider kwargs before dispatch and derive the effective base URL."""
        provider: str = config.get("provider", "unknown")
        common = config.get("common") or {}
        kwargs: dict[str, Any] = {
            "temperature": common.get("temperature"),
            "top_p": common.get("top_p"),
            "max_tokens": common.get("max_output_tokens"),
            "seed": common.get("seed"),
            "response_format": common.get("response_format"),
        }
        transform_fn = self._transform_kwargs.get(provider)
        if transform_fn is None:
            return kwargs

        ctx: TransformKwargsContext = {
            "model": config.get("model", ""),
            "provider": provider,
            "messages": messages,
            "temperature": common.get("temperature"),
            "top_p": common.get("top_p"),
            "enable_thinking": common.get("enable_thinking"),
            "provider_options": config.get("provider_options") or {},
            "base_url": config.get("baseUrl", ""),
        }
        return apply_transform_kwargs(ctx, kwargs, transform_fn)

    def _resolve_effective_base_url(self, config: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        """Resolve the final base URL shape used by the provider dispatch."""
        kwargs = self._build_request_kwargs(config, messages)
        base_url = kwargs.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            return base_url
        return str(config.get("baseUrl", "") or "")

    async def call(self, call_input: V2CallInput) -> V2CallResponse:
        """Execute the 19-step call flow."""
        config = call_input.config
        messages = call_input.messages
        provider: str = config.get("provider", "unknown")
        model: str = config.get("model", "unknown")
        effective_base_url = self._resolve_effective_base_url(config, messages)

        try:
            # Step 1: Kill Switch
            self._kill_switch.check()

            # Step 2: Config -- already resolved by caller

            # Steps 3-4: Cache lookup (L1/L2)
            common = config.get("common") or {}
            caching_strategy = (config.get("caching") or {}).get("strategy", "disabled")
            cache_key: str | None = None
            if self._response_cache and not should_skip_cache(caching_strategy):
                cache_key = generate_cache_key({
                    "provider": provider,
                    "model": model,
                    "messages": messages,
                    "baseUrl": effective_base_url,
                    "enableThinking": common.get("enable_thinking"),
                    "temperature": common.get("temperature"),
                    "maxOutputTokens": common.get("max_output_tokens"),
                    "responseFormat": common.get("response_format"),
                    "seed": common.get("seed"),
                    "topP": common.get("top_p"),
                    "providerOptions": config.get("provider_options"),
                })
                hit = await self._response_cache.aget(cache_key)
                if hit is not None:
                    raw_content = hit.value
                    if common.get("keep_thinking_output"):
                        content = raw_content
                        thinking_content = None
                    else:
                        content, thinking_content = strip_thinking(raw_content)
                    return V2CallResponse(
                        content=content,
                        model=model,
                        provider=provider,
                        usage=LLMUsage(),
                        success=True,
                        thinking_content=thinking_content,
                        cache_hit=hit.tier,
                    )

            # Step 5: Circuit Breaker check
            cb = self._get_circuit_breaker(provider, effective_base_url)
            cb.check()

            # Step 6: Singleflight deduplication
            # Default key includes all request-shaping params to prevent
            # cross-config result sharing (same identity as cache key).
            sf_key = (
                call_input.singleflight_key
                or cache_key
                or Singleflight.make_key(
                    json.dumps(
                        {
                            "provider": provider,
                            "model": model,
                            "messages": messages,
                            "baseUrl": effective_base_url,
                            "enableThinking": common.get("enable_thinking"),
                            "temperature": common.get("temperature"),
                            "maxOutputTokens": common.get("max_output_tokens"),
                            "responseFormat": common.get("response_format"),
                            "seed": common.get("seed"),
                            "topP": common.get("top_p"),
                            "providerOptions": config.get("provider_options"),
                        },
                        default=str,
                    )
                )
            )

            async def _inner() -> ProviderResult:
                return await self._retry_policy.execute(
                    lambda: self._execute_retry_body(config, messages, cb), is_retryable_fn=self._is_retryable_error
                )

            try:
                result = await self._singleflight.do(sf_key, _inner)
            except Exception:
                # If a HALF_OPEN probe was started by check() but never finalized
                # by on_success/on_failure, cancel it so the breaker doesn't wedge.
                cb.cancel_probe()
                raise

            # Step 17: Thinking stripping
            if common.get("keep_thinking_output"):
                content = result.content
                thinking_content = None
            else:
                content, thinking_content = strip_thinking(result.content)

            # Step 18: Cache write (raw content, pre-strip)
            if self._response_cache and cache_key:
                await self._response_cache.aset(cache_key, result.content)

            # Step 19: Telemetry -- placeholder

            return V2CallResponse(
                content=content,
                model=result.model,
                provider=provider,
                usage=result.usage,
                success=True,
                thinking_content=thinking_content,
                tool_calls=result.tool_calls,
            )

        except Exception as exc:
            logger.exception("V2 pipeline call failed")
            return V2CallResponse(content="", model=model, provider=provider, usage=LLMUsage(), success=False, error=str(exc))

    async def _execute_retry_body(self, config: dict[str, Any], messages: list[dict[str, Any]], cb: CircuitBreaker) -> ProviderResult:
        """Steps 7-14 inside the retry loop."""
        provider: str = config.get("provider", "unknown")
        semaphore = self._get_semaphore(provider)

        # Step 8: AIMD Semaphore acquire (before lock to avoid convoy/deadlock)
        await semaphore.acquire()
        api_key: str | None = None

        try:
            # Step 7: Cross-process lock — only wrap state reads, not the API call
            api_key = await asyncio.to_thread(self._select_api_key_locked, provider)

            # Step 10: Provider kwargs transform
            kwargs = self._build_request_kwargs(config, messages)

            # Step 11: Provider dispatch
            result = await self._dispatch(
                DispatchInput(provider=provider, model=config.get("model", ""), api_key=api_key, messages=messages, kwargs=kwargs, config=config)
            )

            # Step 12: AIMD feedback (success path)
            if result.headers:
                rl = parse_openai_ratelimit_headers(result.headers)
                if rl:
                    semaphore.on_header_feedback(rl["remaining"], rl["limit"])
                else:
                    semaphore.on_success()
            else:
                semaphore.on_success()

            # Step 14: Key Pool feedback (success -- no action)

            # Step 15: Circuit Breaker feedback (success)
            cb.on_success()

            return result

        except Exception as exc:
            # Step 12: AIMD feedback (error path)
            status_code = getattr(exc, "status_code", None)
            if status_code == 429:
                semaphore.on_rate_limit()
                try:
                    await asyncio.to_thread(self._rotate_api_key_locked, provider)
                except Exception:
                    pass

            # Step 14: Key Pool feedback (error) — use captured api_key
            if status_code in (401, 403) and api_key is not None:
                try:
                    await asyncio.to_thread(self._mark_api_key_dead_locked, provider, api_key)
                except Exception:
                    pass

            # Step 15: Circuit Breaker feedback (error)
            cb.on_failure(
                status_code, network_error=(not isinstance(exc, CircuitOpenError) and status_code is None and self._is_retryable_error(exc))
            )

            raise

        finally:
            # Step 12: AIMD semaphore release (always)
            semaphore.release()

    @staticmethod
    def _is_retryable_error(exc: BaseException) -> bool:
        """Step 16: Determine if an error is retryable."""
        if isinstance(exc, CircuitOpenError):
            return False
        # Local config/programming errors are not retryable
        if isinstance(exc, (ValueError, TypeError, KeyError)):
            return False
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            return is_retryable(status_code)
        # Remaining errors without status_code are likely network errors — retryable
        return True

    # -----------------------------------------------------------------------
    # Introspection (for testing)
    # -----------------------------------------------------------------------

    def get_circuit_breaker_state(self, provider: str, base_url: str = "") -> str | None:
        key = f"{provider}:{base_url}"
        cb = self._circuit_breakers.get(key)
        return cb.state.value if cb else None

    def get_semaphore_window(self, provider: str) -> int | None:
        sem = self._semaphores.get(provider)
        return sem.window if sem else None

    @property
    def singleflight_count(self) -> int:
        return self._singleflight.in_flight_count
