"""
LLMClient - Unified Async LLM Interface with Config-Driven Calls

Provides a unified interface for making LLM calls using config from LLMConfigLoader.
Python async-only implementation mirroring TypeScript package/llmix/src/client.ts.

Features:
- Preset string parsing ("module:preset" or "preset")
- Multi-provider support (OpenAI, Google/Gemini)
- Optional telemetry via dependency injection
- Runtime overrides with config merging
- Capability detection for batch API support
- Native prompt caching via Helicone
- Configurable call timeout

Example:
    >>> loader = create_llm_config_loader(config_dir='/app/config/llm')
    >>> loader.init()
    >>> client = create_llm_client(loader=loader)
    >>> response = await client.call(
    ...     preset='hrkg:extraction',
    ...     messages=[{'role': 'user', 'content': 'Hello'}],
    ... )

Architecture:
    This module is the facade for LLMix client functionality.
    Provider client creation is delegated to provider_router.py.
    Response parsing and helpers live in response_handler.py.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from llmix.circuit_breaker import ProviderCircuitBreaker
from llmix.env import get_call_timeout_ms, get_capture_telemetry_payload, get_config_load_timeout_seconds
from llmix.loader import LLMConfigLoader

# Backward-compat re-exports: callers may import these from llmix.client
from llmix.provider_router import (  # noqa: F401
    _CF_GATEWAY_PROVIDERS,
    MAX_CLIENT_CACHE_SIZE,
    ProviderRouter,
    _map_anthropic_model,
    _map_deepseek_model,
    _resolve_google_api_key,
    _resolve_helicone_environment,
    is_embedding_model,
)
from llmix.response_handler import (  # noqa: F401
    _JSON_RETRY_MESSAGE,
    _VALID_CACHE_KEY_PATTERN,
    ParsedPreset,
    _CallResult,
    _classify_fallback_trigger,
    _is_retryable_error_message,
    _merge_usage,
    _normalize_openai_text_format,
    _sanitize_error_for_telemetry,
    _strip_think_blocks,
    _try_parse_json,
    derive_capabilities,
    extract_usage,
    is_batch_capable,
    parse_preset,
    resolve_caching_strategy,
)
from llmix.types import (
    OPENAI_PROMPT_CACHE_MIN_TOKENS,
    ApiKeysConfig,
    CachingStrategy,
    CallOptions,
    FallbackTrigger,
    HeliconeConfig,
    LLMCallEventData,
    LLMixTelemetryProvider,
    LLMResponse,
    LLMUsage,
    Provider,
    ProviderUrlConfig,
    ResolvedConfigWithCapabilities,
    RuntimeOverrides,
    TelemetryContext,
)

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

# Default call timeout in milliseconds
DEFAULT_CALL_TIMEOUT_MS = 120000
DEFAULT_CONFIG_LOAD_TIMEOUT_SECONDS = 5.0

# Telemetry timeout in milliseconds (prevents slow telemetry from blocking responses)
TELEMETRY_TIMEOUT_SECONDS = 2.0


# =============================================================================
# CLIENT CONFIG
# =============================================================================


@dataclass
class LLMClientConfig:
    """Configuration for LLMClient."""

    loader: LLMConfigLoader
    """LLMConfigLoader instance for loading configs"""

    default_scope: str | None = None
    """Default scope for config resolution (default: uses loader's defaultScope)"""

    telemetry: LLMixTelemetryProvider | None = None
    """Optional telemetry provider for tracking LLM calls"""

    provider_urls: ProviderUrlConfig | None = None
    """Provider URL configuration for CF AI Gateway support"""

    helicone: HeliconeConfig | None = None
    """Helicone configuration for native prompt caching"""

    api_keys: ApiKeysConfig | None = None
    """API keys for LLM providers (falls back to environment variables)"""

    capture_telemetry_payload: bool | None = None
    """Enable telemetry payload capture for debugging. Tri-state: True (enable), False (disable), None (use env/default). Default: None"""

    call_timeout_ms: int | None = None
    """Call timeout in milliseconds. None uses env var or default (120000)."""

    config_load_timeout_seconds: float | None = None
    """Config load timeout in seconds. None uses env var or default."""


@dataclass(slots=True)
class _CallContext:
    """Bundles resolved parameters for a single LLM call attempt."""

    config: dict[str, Any]
    provider: Provider
    effective_model: str
    effective_common: dict[str, Any]
    effective_provider_options: dict[str, Any]
    effective_timeout_ms: int
    preset_str: str
    messages: list[Any]
    options: CallOptions
    start_time: float


# =============================================================================
# LLM CLIENT CLASS
# =============================================================================


class LLMClient:
    """
    LLM Client for making config-driven async LLM calls.

    Uses LLMConfigLoader for configuration resolution and delegates provider
    client creation to ProviderRouter. Response parsing helpers live in
    response_handler module.
    """

    def __init__(self, config: LLMClientConfig) -> None:
        self._loader = config.loader
        self._default_scope = config.default_scope
        self._telemetry = config.telemetry

        # Delegate provider concerns to ProviderRouter
        self._router = ProviderRouter(api_keys=config.api_keys, helicone=config.helicone, provider_urls=config.provider_urls)

        # Config takes precedence, then env var (DEPRECATED), then default (False)
        if config.capture_telemetry_payload is not None:
            self._capture_telemetry_payload = config.capture_telemetry_payload
        else:
            env_capture = get_capture_telemetry_payload()
            self._capture_telemetry_payload = env_capture.lower() == "true"

        # Timeout priority: config.call_timeout_ms > env var (DEPRECATED) > default
        if config.call_timeout_ms is not None:
            if config.call_timeout_ms <= 0:
                logger.warning("Invalid call_timeout_ms=%s (must be positive), using default", config.call_timeout_ms)
                self._call_timeout_ms = DEFAULT_CALL_TIMEOUT_MS
            else:
                self._call_timeout_ms = config.call_timeout_ms
        else:
            env_timeout = get_call_timeout_ms()
            if env_timeout:
                try:
                    self._call_timeout_ms = int(env_timeout)
                    if self._call_timeout_ms <= 0:
                        logger.warning("Invalid LLMIX_CALL_TIMEOUT_MS=%s (must be positive), using default", env_timeout)
                        self._call_timeout_ms = DEFAULT_CALL_TIMEOUT_MS
                except ValueError:
                    logger.warning("Invalid LLMIX_CALL_TIMEOUT_MS=%s (not an integer), using default", env_timeout)
                    self._call_timeout_ms = DEFAULT_CALL_TIMEOUT_MS
            else:
                self._call_timeout_ms = DEFAULT_CALL_TIMEOUT_MS

        # Config load timeout: config > env var (DEPRECATED) > default
        if config.config_load_timeout_seconds is not None:
            if config.config_load_timeout_seconds <= 0:
                logger.warning("Invalid config_load_timeout_seconds=%s (must be positive), using default", config.config_load_timeout_seconds)
                self._config_load_timeout_seconds = DEFAULT_CONFIG_LOAD_TIMEOUT_SECONDS
            else:
                self._config_load_timeout_seconds = config.config_load_timeout_seconds
        else:
            env_config_timeout = get_config_load_timeout_seconds()
            if env_config_timeout:
                try:
                    self._config_load_timeout_seconds = float(env_config_timeout)
                    if self._config_load_timeout_seconds <= 0:
                        logger.warning("Invalid LLMIX_CONFIG_LOAD_TIMEOUT_SECONDS=%s (must be positive), using default", env_config_timeout)
                        self._config_load_timeout_seconds = DEFAULT_CONFIG_LOAD_TIMEOUT_SECONDS
                except ValueError:
                    logger.warning("Invalid LLMIX_CONFIG_LOAD_TIMEOUT_SECONDS=%s (not a float), using default", env_config_timeout)
                    self._config_load_timeout_seconds = DEFAULT_CONFIG_LOAD_TIMEOUT_SECONDS
            else:
                self._config_load_timeout_seconds = DEFAULT_CONFIG_LOAD_TIMEOUT_SECONDS

        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _schedule_background_task(self, coro: Any) -> None:
        """Keep strong references for fire-and-forget telemetry tasks."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        """Close all cached provider clients and release their resources."""
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)
            self._background_tasks.clear()

        await self._router.close()

    # =========================================================================
    # CONFIG & PARAM RESOLUTION
    # =========================================================================

    async def _load_config_async(self, options: CallOptions, preset_str: str) -> dict[str, Any]:
        """Load config via loader in a thread to avoid blocking the event loop."""
        parsed = parse_preset(preset_str)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._loader.load_config,
                module=parsed.module,
                preset=parsed.preset,
                version=options.get("version", 1),
                user_id=options.get("user_id"),
                scope=options.get("scope") or self._default_scope,
            ),
            timeout=self._config_load_timeout_seconds,
        )

    @staticmethod
    def _resolve_timeout_from_config(timeout_config: Any, default_ms: int) -> int:
        """Extract effective timeout (ms) from config timeout dict."""
        if isinstance(timeout_config, dict):
            total_time = timeout_config.get("total_time")
            if total_time and isinstance(total_time, (int, float)) and total_time > 0:
                return int(total_time * 1000)
        return default_ms

    def _resolve_effective_params(
        self, config: dict[str, Any], options: CallOptions, preset_str: str, messages: list[Any], start_time: float
    ) -> _CallContext:
        """Resolve runtime overrides, model mapping, and timeout into a call context."""
        overrides: RuntimeOverrides = options.get("overrides", {})
        effective_model = overrides.get("model") or config.get("model", "")

        provider: Provider = config.get("provider", "openai")
        if provider == "deepseek":
            effective_model = _map_deepseek_model(effective_model)
        elif provider == "anthropic":
            effective_model = _map_anthropic_model(effective_model)

        return _CallContext(
            config=config,
            provider=provider,
            effective_model=effective_model,
            effective_common={**config.get("common", {}), **overrides.get("common", {})},
            effective_provider_options={**config.get("provider_options", {}), **overrides.get("provider_options", {})},
            effective_timeout_ms=self._resolve_timeout_from_config(config.get("timeout", {}), self._call_timeout_ms),
            preset_str=preset_str,
            messages=messages,
            options=options,
            start_time=start_time,
        )

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    async def call(self, options: CallOptions) -> LLMResponse:
        """Make an LLM call using resolved config."""
        start_time = time.time()
        preset_str = options.get("preset", "_default:_base")
        messages = options.get("messages", [])

        try:
            config = await self._load_config_async(options, preset_str)
        except Exception:
            latency_ms = int((time.time() - start_time) * 1000)
            parsed = parse_preset(preset_str)
            logger.exception(
                "[LLMix] Config load failed for preset %s",
                preset_str,
                extra={
                    "llm_module": parsed.module,
                    "llm_preset": parsed.preset,
                    "llm_scope": options.get("scope") or self._default_scope,
                    "latency_ms": latency_ms,
                },
            )
            fallback_overrides = options.get("overrides", {})
            return cast(
                "LLMResponse",
                {
                    "content": "",
                    "model": fallback_overrides.get("model", "unknown") if fallback_overrides else "unknown",
                    "provider": "unknown",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "success": False,
                    "error": "[LLMix] Config load failed (see logs for details)",
                    "retryable": False,
                },
            )

        ctx = self._resolve_effective_params(config, options, preset_str, messages, start_time)

        # Circuit breaker: skip primary if provider is known-down
        fallback_config = config.get("fallback")
        cb = ProviderCircuitBreaker.for_provider(ctx.provider) if fallback_config and fallback_config.get("preset") else None
        circuit_open = cb is not None and not await cb.should_attempt_primary_async()

        result, fallback_trigger_reason = await self._execute_primary_call(ctx, cb, circuit_open, fallback_config)

        # Attempt fallback if needed
        if fallback_config and self._should_fallback(result, circuit_open, fallback_config, fallback_trigger_reason):
            fb_response = await self._execute_fallback_call(ctx, fallback_config, circuit_open, fallback_trigger_reason)
            if fb_response is not None:
                return fb_response

        if result is not None:
            return await self._maybe_parse_json(result.response, ctx)

        return cast(
            "LLMResponse",
            {
                "content": "",
                "model": ctx.effective_model,
                "provider": ctx.provider,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "success": False,
                "error": f"[LLMix] Circuit breaker open for {ctx.provider} and fallback failed",
                "retryable": True,
            },
        )

    # =========================================================================
    # CALL ORCHESTRATION HELPERS
    # =========================================================================

    async def _execute_primary_call(
        self, ctx: _CallContext, cb: ProviderCircuitBreaker | None, circuit_open: bool, fallback_config: dict[str, Any] | None
    ) -> tuple[_CallResult | None, FallbackTrigger]:
        """Execute primary call with circuit breaker tracking. Returns (result, fallback_trigger_reason)."""
        fallback_trigger_reason: FallbackTrigger = "connection_error"

        if circuit_open:
            logger.warning(
                "[LLMix] Circuit breaker OPEN for %s — skipping primary, routing to fallback %s",
                ctx.provider,
                fallback_config["preset"] if fallback_config else "none",  # type: ignore[index]
            )
            return None, fallback_trigger_reason

        result = await self._execute_single_call(ctx)
        if result.trigger is not None:
            fallback_trigger_reason = result.trigger
        if cb is not None:
            if result.trigger is not None:
                await cb.record_failure_async()
            else:
                await cb.record_success_async()

        return result, fallback_trigger_reason

    @staticmethod
    def _should_fallback(
        result: _CallResult | None, circuit_open: bool, fallback_config: dict[str, Any] | None, fallback_trigger_reason: FallbackTrigger
    ) -> bool:
        """Determine if fallback should fire based on primary result."""
        if not fallback_config or not fallback_config.get("preset"):
            return False
        if circuit_open:
            return True
        if result is not None and result.trigger is not None:
            allowed_triggers = fallback_config.get("on", ["timeout", "connection_error", "5xx"])
            return result.trigger in allowed_triggers
        return False

    async def _execute_fallback_call(
        self, primary_ctx: _CallContext, fallback_config: dict[str, Any], circuit_open: bool, fallback_trigger_reason: FallbackTrigger
    ) -> LLMResponse | None:
        """Load fallback config, execute call, decorate response. Returns None if config load fails."""
        logger.warning(
            "[LLMix] %s — attempting fallback to %s",
            "Circuit breaker skip" if circuit_open else f"Primary call failed ({fallback_trigger_reason})",
            fallback_config["preset"],
        )
        try:
            fb_config = await self._load_config_async(primary_ctx.options, fallback_config["preset"])
        except Exception:
            logger.exception("[LLMix] Fallback config load failed for %s, returning primary error", fallback_config["preset"])
            return None

        fb_ctx = self._resolve_effective_params(fb_config, primary_ctx.options, fallback_config["preset"], primary_ctx.messages, time.time())
        runtime_common = primary_ctx.options.get("overrides", {}).get("common", {})
        fb_ctx.effective_common = {
            **primary_ctx.config.get("common", {}),
            **fb_config.get("common", {}),
            **runtime_common,
        }

        fb_cb = ProviderCircuitBreaker.for_provider(fb_ctx.provider)
        if not await fb_cb.should_attempt_primary_async():
            logger.warning(
                "[LLMix] Circuit breaker OPEN for fallback provider %s; skipping fallback preset %s", fb_ctx.provider, fallback_config["preset"]
            )
            return None

        fb_result = await self._execute_single_call(fb_ctx)
        if fb_result.trigger is not None:
            await fb_cb.record_failure_async()
        else:
            await fb_cb.record_success_async()
        fb_result.response["fallback_used"] = True
        fb_result.response["fallback_reason"] = fallback_trigger_reason
        fb_result.response["circuit_breaker_skip"] = circuit_open
        return await self._maybe_parse_json(fb_result.response, fb_ctx)

    async def _maybe_parse_json(self, response: dict[str, Any], ctx: _CallContext) -> LLMResponse:
        """Apply JSON parse + retry when parse_json=True. Pass-through otherwise."""
        if not ctx.options.get("parse_json") or not response.get("success"):
            return cast("LLMResponse", response)

        content = response.get("content", "")
        try:
            response["parsed_data"] = _try_parse_json(content)
            return cast("LLMResponse", response)
        except Exception:
            logger.info("[LLMix] JSON parse failed after repair, retrying LLM call (preset=%s)", ctx.preset_str)

        retry_ctx = _CallContext(
            config=ctx.config,
            provider=ctx.provider,
            effective_model=ctx.effective_model,
            effective_common=ctx.effective_common,
            effective_provider_options=ctx.effective_provider_options,
            effective_timeout_ms=ctx.effective_timeout_ms,
            preset_str=ctx.preset_str,
            messages=[*ctx.messages, dict(_JSON_RETRY_MESSAGE)],
            options=ctx.options,
            start_time=time.time(),
        )
        retry_result = await self._execute_single_call(retry_ctx)

        if not retry_result.response.get("success"):
            response["success"] = False
            response["error"] = f"JSON parse failed and retry LLM call also failed: {retry_result.response.get('error', 'unknown')}"
            response["retryable"] = retry_result.response.get("retryable", False)
            response["parsed_data"] = None
            _merge_usage(response, retry_result.response)
            return cast("LLMResponse", response)

        retry_content = retry_result.response.get("content", "")
        try:
            response["content"] = retry_content
            response["parsed_data"] = _try_parse_json(retry_content)
            _merge_usage(response, retry_result.response)
            logger.info("[LLMix] JSON parse succeeded on retry (preset=%s)", ctx.preset_str)
        except Exception:
            response["success"] = False
            response["error"] = "JSON parse failed after retry"
            response["parsed_data"] = None
            _merge_usage(response, retry_result.response)
        return cast("LLMResponse", response)

    # =========================================================================
    # SINGLE CALL EXECUTION + HELPERS
    # =========================================================================

    async def _execute_single_call(self, ctx: _CallContext) -> _CallResult:
        """Execute a single LLM call attempt (primary or fallback)."""
        if not ctx.config.get("provider"):
            return _CallResult(
                response={
                    "content": "",
                    "model": ctx.effective_model or "unknown",
                    "provider": "unknown",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "success": False,
                    "error": "[LLMix] Config missing required 'provider' field",
                    "retryable": False,
                },
                trigger=None,
            )
        if not ctx.effective_model:
            return _CallResult(
                response={
                    "content": "",
                    "model": "unknown",
                    "provider": ctx.provider,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "success": False,
                    "error": "[LLMix] Config missing required 'model' field",
                    "retryable": False,
                },
                trigger=None,
            )

        try:
            client, helicone_module = self._resolve_provider_client(ctx)
            logger.info(
                "[LLMix] Calling %s/%s (preset: %s, version: %s)", ctx.provider, ctx.effective_model, ctx.preset_str, ctx.config.get("version", 1)
            )

            call_kwargs = self._build_call_kwargs(ctx)

            try:
                result = await asyncio.wait_for(
                    client.response_completion(input=ctx.messages, model=ctx.effective_model, **call_kwargs), timeout=ctx.effective_timeout_ms / 1000
                )
            except TimeoutError:
                return self._handle_call_timeout(ctx)

            return self._handle_call_success(ctx, result, helicone_module)
        except Exception as error:
            return self._handle_call_error(ctx, error)

    def _resolve_provider_client(self, ctx: _CallContext) -> tuple[Any, str]:
        """Resolve caching strategy, thinking params, and get provider client.

        Returns:
            (client, helicone_module)
        """
        overrides: RuntimeOverrides = ctx.options.get("overrides", {})
        caching_config = resolve_caching_strategy(ctx.config, overrides.get("bypass_gateway"))
        caching_strategy: CachingStrategy = caching_config.get("strategy", "gateway")
        effective_cache_key = ctx.options.get("prompt_cache_key") or caching_config.get("key")

        if effective_cache_key and (not isinstance(effective_cache_key, str) or not _VALID_CACHE_KEY_PATTERN.match(effective_cache_key)):
            logger.warning("[LLMix] Invalid prompt_cache_key rejected: %r", str(effective_cache_key)[:64])
            effective_cache_key = None

        cache_key_info = f" (key: {effective_cache_key})" if effective_cache_key else ""
        logger.info("[LLMix] Caching strategy: %s for %s%s", caching_strategy, ctx.preset_str, cache_key_info)

        enable_thinking, thinking_budget, gpu_path = self._extract_thinking_params(ctx)
        helicone_module = "llmix" if ctx.config.get("module") == "_default" else ctx.config.get("module", "llmix")
        helicone_enabled_yaml: bool = ctx.config.get("helicone", True)

        client = self._router.get_provider_client(
            provider=ctx.provider,
            model=ctx.effective_model,
            caching_strategy=caching_strategy,
            cache_key=effective_cache_key,
            helicone_module=helicone_module,
            helicone_enabled_yaml=helicone_enabled_yaml,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            gpu_path=gpu_path,
        )
        return client, helicone_module

    @staticmethod
    def _extract_thinking_params(ctx: _CallContext) -> tuple[bool | None, int | None, str | None]:
        """Extract enable_thinking, thinking_budget, and gpu_path from provider options."""
        enable_thinking: bool | None = None
        thinking_budget: int | None = None
        gpu_path: str | None = None

        if ctx.provider == "deepinfra":
            opts = ctx.effective_provider_options.get("deepinfra", {})
            enable_thinking = opts.get("enable_thinking") if "enable_thinking" in opts else ctx.effective_common.get("enable_thinking")
            thinking_budget = opts.get("thinking_budget")
        elif ctx.provider == "novita":
            opts = ctx.effective_provider_options.get("novita", {})
            enable_thinking = opts.get("enable_thinking") if "enable_thinking" in opts else ctx.effective_common.get("enable_thinking")
            thinking_budget = opts.get("thinking_budget")
        elif ctx.provider == "snogpu":
            opts = ctx.effective_provider_options.get("snogpu", {})
            enable_thinking = opts.get("enable_thinking") if "enable_thinking" in opts else ctx.effective_common.get("enable_thinking")
            thinking_budget = opts.get("thinking_budget")
            gpu_path = opts.get("gpu_path")

        return enable_thinking, thinking_budget, gpu_path

    @staticmethod
    def _build_call_kwargs(ctx: _CallContext) -> dict[str, Any]:
        """Build provider-specific call kwargs from effective parameters."""
        call_kwargs: dict[str, Any] = {}

        if ctx.effective_common.get("temperature") is not None:
            call_kwargs["temperature"] = ctx.effective_common["temperature"]
        if ctx.effective_common.get("max_output_tokens") is not None:
            call_kwargs["max_output_tokens"] = ctx.effective_common["max_output_tokens"]

        if ctx.provider in ("deepinfra", "together", "novita", "snogpu"):
            for param in ("top_p", "top_k", "seed", "presence_penalty", "frequency_penalty"):
                if ctx.effective_common.get(param) is not None:
                    call_kwargs[param] = ctx.effective_common[param]
            if ctx.provider == "snogpu" and ctx.effective_common.get("min_p") is not None:
                call_kwargs["min_p"] = ctx.effective_common["min_p"]
            if ctx.provider == "snogpu":
                call_kwargs["timeout"] = ctx.effective_timeout_ms / 1000
        else:
            unsupported = [
                k
                for k in ("top_p", "top_k", "presence_penalty", "frequency_penalty", "seed", "stop_sequences")
                if ctx.effective_common.get(k) is not None
            ]
            if unsupported:
                logger.debug("[LLMix] Ignored params not supported by response_completion: %s", unsupported)

        response_format = ctx.options.get("response_format")
        if response_format is not None:
            if ctx.provider == "openai":
                call_kwargs["text"] = {"format": _normalize_openai_text_format(response_format)}
            elif ctx.provider in ("deepinfra", "together", "novita", "snogpu"):
                call_kwargs["response_format"] = response_format
            else:
                logger.warning("[LLMix] response_format is not supported for provider=%s (ignored)", ctx.provider)

        if ctx.provider == "openai":
            openai_opts = ctx.effective_provider_options.get("openai", {})
            reasoning = openai_opts.get("reasoning_effort") or openai_opts.get("reasoningEffort")
            if reasoning:
                call_kwargs["reasoning_effort"] = reasoning

        return call_kwargs

    def _handle_call_timeout(self, ctx: _CallContext) -> _CallResult:
        """Build error response for a timed-out LLM call."""
        latency_ms = int((time.time() - ctx.start_time) * 1000)
        error_msg = f"[LLMix] Request timeout after {ctx.effective_timeout_ms}ms"
        logger.error(
            "[LLMix] LLM call timeout for %s (%s/%s)",
            ctx.config.get("config_id", "unknown"),
            ctx.provider,
            ctx.effective_model,
            extra={"preset": ctx.preset_str, "latency_ms": latency_ms, "timeout_ms": ctx.effective_timeout_ms},
        )
        self._schedule_background_task(
            self._track_telemetry_non_blocking(
                config=ctx.config,
                effective_model=ctx.effective_model,
                usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                latency_ms=latency_ms,
                success=False,
                error_message=_sanitize_error_for_telemetry(error_msg),
                messages=ctx.messages,
                telemetry_context=ctx.options.get("telemetry"),
            )
        )
        return _CallResult(
            response={
                "content": "",
                "model": ctx.effective_model,
                "provider": ctx.provider,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "config": ctx.config,
                "success": False,
                "error": error_msg,
                "retryable": True,
            },
            trigger=_classify_fallback_trigger(error=None, error_message=None, is_timeout=True),
        )

    def _handle_call_success(self, ctx: _CallContext, result: Any, helicone_module: str) -> _CallResult:
        """Build response dict from a successful provider result, with logging and telemetry."""
        usage = extract_usage(result.usage if hasattr(result, "usage") else {})
        latency_ms = int((time.time() - ctx.start_time) * 1000)

        try:
            from lib.telemetry.helicone import log_cache_ratio
        except ImportError:
            log_cache_ratio = None

        if log_cache_ratio is not None:
            log_cache_ratio(
                {
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "prompt_tokens_details": {"cached_tokens": usage.get("cached_input_tokens", 0)},
                    }
                },
                helicone_module,
                "client",
            )
        self._log_cache_status(ctx, usage, latency_ms)

        provider_success = bool(getattr(result, "success", True))
        provider_error = getattr(result, "error", None) if not provider_success else None

        self._schedule_background_task(
            self._track_telemetry_non_blocking(
                config=ctx.config,
                effective_model=ctx.effective_model,
                usage=usage,
                latency_ms=latency_ms,
                success=provider_success,
                error_message=str(provider_error) if provider_error else None,
                messages=ctx.messages,
                output=result.content if hasattr(result, "content") else "",
                telemetry_context=ctx.options.get("telemetry"),
            )
        )

        resolved_model = (getattr(result, "model", None) or ctx.effective_model)
        response_dict: dict[str, Any] = {
            "content": result.content if hasattr(result, "content") else "",
            "model": resolved_model,
            "provider": ctx.provider,
            "usage": usage,
            "config": ctx.config,
            "success": provider_success,
        }
        if provider_error:
            response_dict["error"] = "[LLMix] Provider request failed (see logs for details)"
            response_dict["retryable"] = _is_retryable_error_message(str(provider_error))
        if hasattr(result, "output") and result.output:
            response_dict["output"] = result.output
        if hasattr(result, "tool_calls") and result.tool_calls:
            response_dict["tool_calls"] = result.tool_calls

        trigger: FallbackTrigger | None = None
        if not provider_success:
            trigger = _classify_fallback_trigger(error=None, error_message=str(provider_error), is_timeout=False)
        return _CallResult(response=response_dict, trigger=trigger)

    @staticmethod
    def _log_cache_status(ctx: _CallContext, usage: LLMUsage, latency_ms: int) -> None:
        """Log prompt cache hit/miss/done status."""
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cached_input_tokens = usage.get("cached_input_tokens")
        if input_tokens >= OPENAI_PROMPT_CACHE_MIN_TOKENS:
            if cached_input_tokens and cached_input_tokens > 0:
                cache_hit_percent = round((cached_input_tokens / input_tokens) * 100)
                logger.info(
                    "[LLMix] CACHE HIT | preset=%s | model=%s | cached=%d/%d (%d%%) | out=%d | latency=%dms",
                    ctx.preset_str,
                    ctx.effective_model,
                    cached_input_tokens,
                    input_tokens,
                    cache_hit_percent,
                    output_tokens,
                    latency_ms,
                )
            else:
                logger.info(
                    "[LLMix] CACHE MISS | preset=%s | model=%s | in=%d out=%d | latency=%dms",
                    ctx.preset_str,
                    ctx.effective_model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                )
        else:
            logger.info(
                "[LLMix] DONE | preset=%s | model=%s | in=%d out=%d | latency=%dms",
                ctx.preset_str,
                ctx.effective_model,
                input_tokens,
                output_tokens,
                latency_ms,
            )

    def _handle_call_error(self, ctx: _CallContext, error: Exception) -> _CallResult:
        """Build error response for a failed LLM call."""
        latency_ms = int((time.time() - ctx.start_time) * 1000)
        error_message = str(error)

        logger.exception(
            "[LLMix] LLM call failed for %s (%s/%s)",
            ctx.config.get("config_id", "unknown"),
            ctx.provider,
            ctx.effective_model,
            extra={"preset": ctx.preset_str, "latency_ms": latency_ms, "provider": ctx.provider, "model": ctx.effective_model},
        )
        self._schedule_background_task(
            self._track_telemetry_non_blocking(
                config=ctx.config,
                effective_model=ctx.effective_model,
                usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                latency_ms=latency_ms,
                success=False,
                error_message=_sanitize_error_for_telemetry(error_message),
                messages=ctx.messages,
                telemetry_context=ctx.options.get("telemetry"),
            )
        )
        trigger = _classify_fallback_trigger(error=error, error_message=error_message, is_timeout=False)
        return _CallResult(
            response={
                "content": "",
                "model": ctx.effective_model,
                "provider": ctx.provider,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "config": ctx.config,
                "success": False,
                "error": "[LLMix] LLM call failed (see logs for details)",
                "retryable": _is_retryable_error_message(error_message),
            },
            trigger=trigger,
        )

    async def get_resolved_config(self, options: CallOptions) -> ResolvedConfigWithCapabilities:
        """Get resolved config and capabilities without making a call."""

        preset_str = options.get("preset", "_default:_base")
        parsed = parse_preset(preset_str)

        config = await asyncio.wait_for(
            asyncio.to_thread(
                self._loader.load_config,
                module=parsed.module,
                preset=parsed.preset,
                version=options.get("version", 1),
                user_id=options.get("user_id"),
                scope=options.get("scope") or self._default_scope,
            ),
            timeout=self._config_load_timeout_seconds,
        )

        overrides = options.get("overrides", {})
        effective_model = overrides.get("model") or config.get("model", "")

        capabilities = derive_capabilities(config, effective_model)

        return cast("ResolvedConfigWithCapabilities", {"config": config, "capabilities": capabilities})

    # =========================================================================
    # TELEMETRY (stays in client — tightly coupled to LLMClient state)
    # =========================================================================

    async def _track_telemetry_non_blocking(
        self,
        config: dict[str, Any] | None,
        effective_model: str,
        usage: LLMUsage,
        latency_ms: int,
        success: bool,
        error_message: str | None = None,
        messages: list[Any] | None = None,
        output: str | None = None,
        telemetry_context: TelemetryContext | None = None,
    ) -> None:
        """Non-blocking telemetry wrapper with timeout."""
        if not self._telemetry or not config:
            return

        try:
            await asyncio.wait_for(
                self._track_telemetry(
                    config=config,
                    effective_model=effective_model,
                    usage=usage,
                    latency_ms=latency_ms,
                    success=success,
                    error_message=error_message,
                    messages=messages or [],
                    output=output,
                    telemetry_context=telemetry_context,
                ),
                timeout=TELEMETRY_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            config_id = config.get("config_id", "unknown")
            logger.warning("[LLMix] Telemetry timeout for %s", config_id)
        except Exception as error:
            config_id = config.get("config_id", "unknown")
            logger.warning("[LLMix] Telemetry failed for %s: %s", config_id, error)

    async def _track_telemetry(
        self,
        config: dict[str, Any],
        effective_model: str,
        usage: LLMUsage,
        latency_ms: int,
        success: bool,
        error_message: str | None = None,
        messages: list[Any] | None = None,
        output: str | None = None,
        telemetry_context: TelemetryContext | None = None,
    ) -> None:
        """Track telemetry for LLM call via injected provider."""
        if not self._telemetry:
            return

        if self._capture_telemetry_payload:
            redacted_messages = messages or []
            redacted_output = output
        else:
            redacted_messages = [{"redacted": True, "count": len(messages or [])}]
            redacted_output = "[redacted]" if output else None

        event: LLMCallEventData = {
            "config_id": config.get("config_id", ""),
            "provider": config.get("provider", "openai"),
            "model": effective_model,
            "module": config.get("module", ""),
            "preset": config.get("preset", ""),
            "scope": config.get("scope", ""),
            "version": config.get("version", 1),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": latency_ms,
            "success": success,
        }

        if error_message:
            event["error_message"] = error_message

        if telemetry_context:
            event["context"] = telemetry_context

        if redacted_messages:
            event["messages"] = redacted_messages

        if redacted_output:
            event["output"] = redacted_output

        await self._telemetry.track_llm_call(event)


# =============================================================================
# FACTORY FUNCTION
# =============================================================================


def create_llm_client(
    loader: LLMConfigLoader,
    default_scope: str | None = None,
    telemetry: LLMixTelemetryProvider | None = None,
    provider_urls: ProviderUrlConfig | None = None,
    helicone: HeliconeConfig | None = None,
    api_keys: ApiKeysConfig | None = None,
    capture_telemetry_payload: bool | None = None,
    call_timeout_ms: int | None = None,
    config_load_timeout_seconds: float | None = None,
) -> LLMClient:
    """Create a new LLMClient instance."""
    config = LLMClientConfig(
        loader=loader,
        default_scope=default_scope,
        telemetry=telemetry,
        provider_urls=provider_urls,
        helicone=helicone,
        api_keys=api_keys,
        capture_telemetry_payload=capture_telemetry_payload,
        call_timeout_ms=call_timeout_ms,
        config_load_timeout_seconds=config_load_timeout_seconds,
    )
    return LLMClient(config)
