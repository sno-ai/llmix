"""
LLMix v2 Call Pipeline

Orchestrates the 19-step call flow using feature modules:
  1. Kill Switch -> 2. Config -> 3-4. Cache -> 5. Circuit Breaker ->
  6. Singleflight -> [7-16 Retry Loop] -> 17. Thinking strip ->
  18. Cache write -> 19. Telemetry

The provider dispatch (step 11) is a caller-supplied callback.
This module is pure orchestration -- no direct LLM SDK dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from llmix.adaptive_semaphore import AdaptiveSemaphore, parse_openai_ratelimit_headers
from llmix.key_pool import KeyPool
from llmix.provider_kwargs import (
    PROVIDER_KWARGS_REGISTRY,
    TransformKwargsCallback,
    TransformKwargsContext,
    apply_transform_kwargs,
)
from llmix.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    FileLock,
    KillSwitch,
    RetryPolicy,
    Singleflight,
    is_retryable,
)
from llmix.thinking import strip_thinking


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Error with optional HTTP status and headers."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
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
        self._retry_policy = RetryPolicy(
            max_retries=config.max_retries,
            base_ms=config.retry_base_ms,
            max_delay_ms=config.retry_max_delay_ms,
        )
        self._file_lock = FileLock()

        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._semaphores: dict[str, AdaptiveSemaphore] = {}
        self._key_pools: dict[str, KeyPool] = {}

        self._cb_threshold = config.circuit_breaker_threshold
        self._cb_cooldown = config.circuit_breaker_cooldown_seconds
        self._sem_initial = config.semaphore_initial
        self._sem_min = config.semaphore_min

        self._transform_kwargs: dict[str, TransformKwargsCallback] = {
            **PROVIDER_KWARGS_REGISTRY,
            **(config.transform_kwargs_overrides or {}),
        }

    def set_key_pool(self, provider: str, pool: KeyPool) -> None:
        """Register a key pool for a provider."""
        self._key_pools[provider] = pool

    def _get_circuit_breaker(self, provider: str, base_url: str = "") -> CircuitBreaker:
        key = f"{provider}:{base_url}"
        cb = self._circuit_breakers.get(key)
        if cb is None:
            cb = CircuitBreaker(
                provider,
                base_url,
                failure_threshold=self._cb_threshold,
                cooldown_seconds=self._cb_cooldown,
            )
            self._circuit_breakers[key] = cb
        return cb

    def _get_semaphore(self, provider: str) -> AdaptiveSemaphore:
        sem = self._semaphores.get(provider)
        if sem is None:
            sem = AdaptiveSemaphore(self._sem_initial, self._sem_min)
            self._semaphores[provider] = sem
        return sem

    async def call(self, input: V2CallInput) -> V2CallResponse:
        """Execute the 19-step call flow."""
        config = input.config
        messages = input.messages
        provider: str = config.get("provider", "unknown")
        model: str = config.get("model", "unknown")
        base_url = ""

        try:
            # Step 1: Kill Switch
            self._kill_switch.check()

            # Step 2: Config -- already resolved by caller

            # Steps 3-4: Cache lookup (L1/L2) -- placeholder
            # TODO: integrate LRU + Redis cache check

            # Step 5: Circuit Breaker check
            cb = self._get_circuit_breaker(provider, base_url)
            cb.check()

            # Step 6: Singleflight deduplication
            sf_key = input.singleflight_key or Singleflight.make_key(
                json.dumps({"provider": provider, "model": model, "messages": messages}, default=str)
            )

            async def _inner() -> ProviderResult:
                return await self._retry_policy.execute(
                    lambda: self._execute_retry_body(config, messages, cb),
                    is_retryable_fn=self._is_retryable_error,
                )

            result = await self._singleflight.do(sf_key, _inner)

            # Step 17: Thinking stripping
            common = config.get("common") or {}
            if common.get("keep_thinking_output"):
                content = result.content
                thinking_content = None
            else:
                content, thinking_content = strip_thinking(result.content)

            # Step 18: Cache write -- placeholder
            # Step 19: Telemetry -- placeholder

            return V2CallResponse(
                content=content,
                model=result.model,
                provider=provider,
                usage=result.usage,
                success=True,
                thinking_content=thinking_content,
            )

        except Exception as exc:
            return V2CallResponse(
                content="",
                model=model,
                provider=provider,
                usage=LLMUsage(),
                success=False,
                error=str(exc),
            )

    async def _execute_retry_body(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        cb: CircuitBreaker,
    ) -> ProviderResult:
        """Steps 7-14 inside the retry loop."""
        provider: str = config.get("provider", "unknown")
        semaphore = self._get_semaphore(provider)

        # Step 7: Cross-process lock acquire
        self._file_lock.acquire()

        # Step 8: AIMD Semaphore acquire
        await semaphore.acquire()

        try:
            # Step 9: Key Pool select
            pool = self._key_pools.get(provider)
            api_key = pool.select() if pool else ""

            # Step 10: Provider kwargs transform
            common = config.get("common") or {}
            kwargs: dict[str, Any] = {
                "temperature": common.get("temperature"),
                "top_p": common.get("top_p"),
                "max_tokens": common.get("max_output_tokens"),
            }
            transform_fn = self._transform_kwargs.get(provider)
            if transform_fn is not None:
                ctx: TransformKwargsContext = {
                    "model": config.get("model", ""),
                    "provider": provider,
                    "messages": messages,
                    "temperature": common.get("temperature"),
                    "top_p": common.get("top_p"),
                    "provider_options": config.get("provider_options") or {},
                }
                kwargs = apply_transform_kwargs(ctx, kwargs, transform_fn)

            # Step 11: Provider dispatch
            result = await self._dispatch(
                DispatchInput(
                    provider=provider,
                    model=config.get("model", ""),
                    api_key=api_key,
                    messages=messages,
                    kwargs=kwargs,
                    config=config,
                )
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

            # Step 14: Key Pool feedback (error)
            pool = self._key_pools.get(provider)
            if pool and status_code is not None:
                if status_code in (401, 403):
                    try:
                        current_key = pool.select()
                        pool.mark_dead(current_key)
                    except Exception:
                        pass
                elif status_code == 429:
                    pool.rotate()

            # Step 15: Circuit Breaker feedback (error)
            cb.on_failure(
                status_code,
                network_error=(not isinstance(exc, CircuitOpenError) and status_code is None),
            )

            raise

        finally:
            # Step 12: AIMD semaphore release (always)
            semaphore.release()

            # Step 13: Cross-process lock release
            self._file_lock.release()

    @staticmethod
    def _is_retryable_error(exc: BaseException) -> bool:
        """Step 16: Determine if an error is retryable."""
        if isinstance(exc, CircuitOpenError):
            return False
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            return is_retryable(status_code)
        # Network errors are retryable
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
