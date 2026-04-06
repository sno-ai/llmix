"""
LLMConfigLoader - Three-Tier LLM Config Loading with Redis Caching

Architecture:
1. Local LRU cache (fastest, 0.1ms)
2. Shared Redis (fast, 1-2ms)
3. File system with cascade resolution (slower, 5-10ms)

Features:
- Dependency injection (no hardcoded config imports)
- Optional Redis (works file-only if Redis unavailable)
- TTL-based cache invalidation
- Graceful degradation on Redis failure
- A/B experiment switching before cache lookup

ConfigId format: {scope}:{module}:{userId}:{preset}:v{version}
Redis key: llm:{scope}:{module}:{userId}:{preset}:v{version}
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import redis
import yaml

from llmix.types import (
    CacheStats,
    ConfigNotFoundError,
    ExperimentConfig,
    InvalidConfigError,
    LRUCacheStats,
    ResolvedConfigResult,
    SecurityError,
    validate_module,
    validate_preset,
    validate_scope,
    validate_user_id,
    validate_version,
)

logger = logging.getLogger(__name__)

# =============================================================================
# DEFAULT CONFIGURATION
# =============================================================================

DEFAULT_CACHE_SIZE = 100
DEFAULT_CACHE_TTL_SECONDS = 21600  # 6 hours
DEFAULT_REDIS_TTL_SECONDS = DEFAULT_CACHE_TTL_SECONDS  # Align with local cache TTL
DEFAULT_SCOPE = "default"

# Max entries in warned config IDs set (rate-limited warnings)
MAX_WARNED_CONFIG_IDS = 1000

# Redis key prefix for LLM configs
REDIS_KEY_PREFIX = "llm:"

# Redis key prefix for A/B experiments
EXPERIMENT_KEY_PREFIX = "experiment:llm:"

# Circuit breaker cooldown before half-open retry (30 seconds)
CIRCUIT_BREAKER_COOLDOWN_MS = 30000
MAX_HALF_OPEN_PING_FAILURES = 3

# Explicit camelCase → snake_case mapping for config keys.
# Only known keys are normalized (not a generic converter) for safety.
_CAMEL_TO_SNAKE: dict[str, str] = {
    "maxOutputTokens": "max_output_tokens",
    "maxRetries": "max_retries",
    "topP": "top_p",
    "topK": "top_k",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
    "stopSequences": "stop_sequences",
    "totalTime": "total_time",
    "streamFirstChunkTime": "stream_first_chunk_time",
    "providerOptions": "provider_options",
    "bypassGateway": "bypass_gateway",
    "configId": "config_id",
    "enableThinking": "enable_thinking",
    "thinkingBudget": "thinking_budget",
    "reasoningEffort": "reasoning_effort",
    "textVerbosity": "text_verbosity",
    "structuredOutputs": "structured_outputs",
    "parallelToolCalls": "parallel_tool_calls",
    "logitBias": "logit_bias",
    "strictJsonSchema": "strict_json_schema",
    "maxCompletionTokens": "max_completion_tokens",
    "serviceTier": "service_tier",
    "promptCacheKey": "prompt_cache_key",
    "promptCacheRetention": "prompt_cache_retention",
    "gpuPath": "gpu_path",
}


def _normalize_config_keys(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize known camelCase keys to snake_case recursively.

    Only converts keys in the explicit _CAMEL_TO_SNAKE mapping.
    Preserves unknown keys as-is.
    """
    result: dict[str, Any] = {}
    for key, value in config.items():
        normalized_key = _CAMEL_TO_SNAKE.get(key, key)
        if isinstance(value, dict):
            result[normalized_key] = _normalize_config_keys(value)
        elif isinstance(value, list):
            result[normalized_key] = [_normalize_config_keys(item) if isinstance(item, dict) else item for item in value]
        else:
            result[normalized_key] = value
    return result


# =============================================================================
# LRU CACHE
# =============================================================================


class LRUCache:
    """Thread-safe LRU cache with TTL support for LLM config caching."""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600) -> None:
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: OrderedDict[str, tuple[str, datetime]] = OrderedDict()
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> str | None:
        """Get value from cache if exists and not expired."""
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None

            value, timestamp = self.cache[key]

            # Check TTL
            if datetime.now() - timestamp > timedelta(seconds=self.ttl_seconds):
                del self.cache[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self.cache.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: str) -> None:
        """Set value in cache with current timestamp."""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = (value, datetime.now())

            # Evict oldest if over limit
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self.lock:
            self.cache.clear()

    def get_stats(self) -> LRUCacheStats:
        """Get cache statistics."""
        with self.lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total * 100) if total > 0 else 0.0
            return {"size": len(self.cache), "max_size": self.max_size, "hits": self.hits, "misses": self.misses, "hit_rate": hit_rate}


# =============================================================================
# LLM CONFIG LOADER CLASS
# =============================================================================


class LLMConfigLoader:
    """
    LLM Config Loader with three-tier caching and cascade resolution.

    Loading priority:
    1. Local LRU cache (instant, 0.1ms)
    2. Redis (1-2ms)
    3. File system with cascade (5-10ms)

    Cascade resolution (5 levels):
    1. {scope}:{module}:{userId}:{preset}:v{version} - Full specific
    2. {scope}:{module}:_:{preset}:v{version} - Module preset (no user)
    3. {scope}:_default:_:{preset}:v{version} - Scope-wide fallback
    4. _default:_default:_:{preset}:v{version} - Global preset fallback
    5. _default:_default:_:_base:v1 - Ultimate base fallback
    """

    def __init__(
        self,
        config_dir: str,
        redis_url: str | None = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        redis_ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
        default_scope: str = DEFAULT_SCOPE,
    ) -> None:
        """
        Initialize LLMConfigLoader.

        Args:
            config_dir: Base directory for LLM config files (required)
            redis_url: Redis URL - optional, works without Redis
            cache_size: LRU cache max size (default: 100)
            cache_ttl_seconds: Local cache TTL in seconds (default: 21600 = 6 hours)
            redis_ttl_seconds: Redis cache TTL in seconds (default: 21600 = 6 hours, capped to local TTL)
            default_scope: Default scope for config resolution (default: "default")
        """
        self.config_dir = config_dir
        self.redis_url = redis_url
        self.cache_size = cache_size
        self.cache_ttl_seconds = cache_ttl_seconds
        self.redis_ttl_seconds = min(redis_ttl_seconds, cache_ttl_seconds)
        self.default_scope = default_scope

        # Initialize local cache
        self.local_cache = LRUCache(max_size=cache_size, ttl_seconds=cache_ttl_seconds)

        # Rate-limited warnings: track which configIds have already warned
        self.warned_config_ids: OrderedDict[str, None] = OrderedDict()
        self._warned_config_ids_lock = threading.RLock()

        # Redis state (type: ignore for generic - redis-py typing is complex)
        self.redis_client: redis.Redis | None = None  # type: ignore[type-arg]
        self.redis_available = False
        self._redis_url = redis_url

        # Circuit breaker state
        self.redis_command_failures = 0
        self.redis_max_command_failures = 5
        self.circuit_breaker_tripped_at: float | None = None
        self._half_open_ping_failures = 0
        self._circuit_breaker_lock = threading.RLock()

        # Initialize Redis if URL provided
        if redis_url:
            self._init_redis(redis_url)

        logger.info(
            f"LLMConfigLoader created: size={cache_size}, ttl={cache_ttl_seconds}s, redis_ttl={self.redis_ttl_seconds}s, "
            f"redis={'connected' if self.redis_available else 'disabled'}"
        )

    def _mask_redis_url(self, url: str) -> str:
        """Mask password in Redis URL for logging."""
        try:
            parsed = urlparse(url)
            if parsed.password:
                masked = parsed._replace(netloc=f"{parsed.username or ''}:****@{parsed.hostname}:{parsed.port or 6379}")
                return urlunparse(masked)
            return url
        except Exception:
            return url[:20] + "..."

    def _init_redis(self, redis_url: str) -> None:
        """Initialize Redis connection."""
        try:
            self.redis_client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5, retry_on_timeout=True)
            self.redis_client.ping()
            self.redis_available = True
            self.redis_command_failures = 0
            self._half_open_ping_failures = 0
            logger.info(f"Redis connection established: {self._mask_redis_url(redis_url)}")
        except (redis.ConnectionError, redis.TimeoutError) as e:
            logger.warning(f"Redis connection failed: {e}. Will use file-only mode.")
            self.redis_client = None
            self.redis_available = False
            self.circuit_breaker_tripped_at = time.time() * 1000
            self.redis_command_failures = 1
        except Exception as e:
            logger.error(f"Unexpected error initializing Redis: {e}")
            self.redis_client = None
            self.redis_available = False
            self.circuit_breaker_tripped_at = time.time() * 1000
            self.redis_command_failures = 1

    def _drop_redis_client(self, reason: str) -> None:
        """Discard a broken Redis client so recovery can build a fresh one."""
        if self.redis_client is not None:
            try:
                self.redis_client.close()
            except Exception as close_error:
                logger.debug(f"Error closing Redis client during recovery: {close_error}")
        self.redis_client = None
        self.redis_available = False
        logger.debug(f"Dropped Redis client: {reason}")

    def _try_circuit_breaker_recovery(self) -> None:
        """Attempt circuit breaker half-open recovery after cooldown."""
        with self._circuit_breaker_lock:
            if (
                not self.redis_available
                and self.circuit_breaker_tripped_at is not None
                and (time.time() * 1000 - self.circuit_breaker_tripped_at) >= CIRCUIT_BREAKER_COOLDOWN_MS
            ):
                if self.redis_client is None and self._redis_url:
                    self._init_redis(self._redis_url)
                    if self.redis_available:
                        self.circuit_breaker_tripped_at = None
                        logger.info("Redis circuit breaker reset - reconnected after initial failure")
                elif self.redis_client is not None:
                    try:
                        self.redis_client.ping()
                        self.redis_available = True
                        self.redis_command_failures = 0
                        self._half_open_ping_failures = 0
                        self.circuit_breaker_tripped_at = None
                        logger.info("Redis circuit breaker reset - connection restored")
                    except Exception:
                        self._half_open_ping_failures += 1
                        if self._half_open_ping_failures >= MAX_HALF_OPEN_PING_FAILURES:
                            self._drop_redis_client('repeated half-open probe failures')
                            self._half_open_ping_failures = 0
                        self.circuit_breaker_tripped_at = time.time() * 1000
                        logger.debug("Redis circuit breaker half-open probe failed, extending cooldown")

    def _handle_redis_failure(self, error: Exception, context: str) -> None:
        """Handle Redis failure and circuit breaker logic."""
        with self._circuit_breaker_lock:
            self.redis_command_failures += 1
            failure_count = self.redis_command_failures

        logger.warning(f"Redis operation failed for {context}: {error} (failure {failure_count}/{self.redis_max_command_failures})")

        with self._circuit_breaker_lock:
            if self.redis_command_failures >= self.redis_max_command_failures:
                logger.error("Redis circuit breaker triggered - disabling Redis")
                self._half_open_ping_failures = 0
                self._drop_redis_client('circuit breaker triggered')
                self.circuit_breaker_tripped_at = time.time() * 1000

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def init(self) -> None:
        """
        Initialize the loader.

        - Validates that _default/_base.v1.yaml exists and parses

        Raises:
            ConfigNotFoundError: if base config is missing
            InvalidConfigError: if base config fails to parse
        """
        # Validate base config exists
        try:
            self._load_from_file("_default", "_base", 1)
            logger.info("Base config validated: _default/_base.v1.yaml")
        except ConfigNotFoundError:
            raise ConfigNotFoundError(
                f"LLMConfigLoader init failed: base config not found at "
                f"{self.config_dir}/_default/_base.v1.yaml. "
                "This file is required as the ultimate fallback."
            ) from None

        logger.info("LLMConfigLoader initialized successfully")

    def load_config(
        self, module: str, preset: str, version: int = 1, user_id: str | None = None, scope: str | None = None, force_refresh: bool = False
    ) -> dict[str, Any]:
        """
        Load LLM config with cascade resolution.

        Args:
            module: Functional module (e.g., "hrkg", "memobase")
            preset: Config preset name (e.g., "extraction", "search")
            version: Config version (default: 1)
            user_id: User ID for user-specific overrides ("_" for global)
            scope: Deployment scope (default: uses default_scope)
            force_refresh: Bypass cache and load fresh from file

        Returns:
            Resolved LLM config dictionary with metadata

        Raises:
            ConfigNotFoundError: if no config found in cascade
        """
        scope = scope or self.default_scope

        # Normalize userId before building cache key
        raw_user_id = user_id or "_"
        normalized_user_id = raw_user_id if validate_user_id(raw_user_id) else "_"

        # A/B Experiment Check - BEFORE cache lookup
        experiment_result = self._get_experiment_config(module, preset, normalized_user_id)
        if experiment_result is not None:
            version = experiment_result["version"]
            force_refresh = True  # Always bypass cache for experiment configs
            logger.info(f"[AB] Switched llm:{module}:{preset} to v{version}")

        # Build primary ConfigId
        config_id = self._build_config_id(scope, module, normalized_user_id, preset, version)

        # Tier 1: Check LRU cache (skip if forceRefresh)
        if not force_refresh:
            cached = self.local_cache.get(config_id)
            if cached is not None:
                logger.debug(f"LRU hit: {config_id}")
                return _normalize_config_keys(json.loads(cached))

        # Circuit breaker half-open recovery
        self._try_circuit_breaker_recovery()

        # Tier 2: Check Redis (skip if forceRefresh)
        if not force_refresh and self.redis_available and self.redis_client is not None:
            redis_result = self._try_load_from_redis(config_id)
            if redis_result is not None:
                # Store in LRU for faster subsequent access
                self.local_cache.set(config_id, json.dumps(redis_result))
                logger.debug(f"Redis hit: {config_id}")
                return _normalize_config_keys(redis_result)

        # Tier 3: File system with cascade resolution
        result = self._resolve_with_cascade(scope, module, normalized_user_id, preset, version)

        # Cache the result
        serialized = json.dumps(result)
        self.local_cache.set(config_id, serialized)
        self._try_store_in_redis(config_id, serialized)

        # Handle deprecated config warning
        if result.get("deprecated"):
            logger.warning(f"Deprecated config loaded: {result.get('config_id')}")

        return result

    def get_resolved_config(
        self, module: str, preset: str, version: int = 1, user_id: str | None = None, scope: str | None = None
    ) -> ResolvedConfigResult:
        """
        Get resolved config with experiment metadata (dry-run testing).

        Returns config with metadata about version resolution, useful for
        testing A/B experiment behavior without side effects.

        Args:
            module: Functional module (e.g., "hrkg", "memobase")
            preset: Config preset name (e.g., "extraction", "search")
            version: Requested version (default: 1)
            user_id: User ID for user-specific overrides
            scope: Deployment scope

        Returns:
            Dict with config, resolved version, and experiment status
        """
        scope = scope or self.default_scope

        # Normalize userId
        raw_user_id = user_id or "_"
        normalized_user_id = raw_user_id if validate_user_id(raw_user_id) else "_"

        # Check for experiment override
        experiment = self._get_experiment_config(module, preset, normalized_user_id)
        experiment_active = experiment is not None
        resolved_version = experiment["version"] if experiment else version

        # Load config with resolved version (force_refresh when experiment active)
        config = self.load_config(
            module=module, preset=preset, version=resolved_version, user_id=user_id, scope=scope, force_refresh=experiment_active
        )

        return {"config": config, "version": resolved_version, "experiment_active": experiment_active}

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        return {"local_cache": self.local_cache.get_stats(), "redis_available": self.redis_available}

    def close(self) -> None:
        """Close all connections and clean up resources."""
        logger.info("Closing LLMConfigLoader...")

        # Close Redis client
        if self.redis_client is not None:
            try:
                self.redis_client.close()
            except Exception:
                pass
            self.redis_client = None
            self.redis_available = False

        # Clear local cache
        self.local_cache.clear()

        # Clear warned config IDs
        with self._warned_config_ids_lock:
            self.warned_config_ids.clear()

        logger.info("LLMConfigLoader closed")

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _build_config_id(self, scope: str, module: str, user_id: str, preset: str, version: int) -> str:
        """Build ConfigId from components.

        Format: {scope}:{module}:{userId}:{preset}:v{version}
        """
        return f"{scope}:{module}:{user_id}:{preset}:v{version}"

    def _build_redis_key(self, config_id: str) -> str:
        """Build Redis key from ConfigId."""
        return f"{REDIS_KEY_PREFIX}{config_id}"

    def _build_experiment_key(self, module: str, preset: str) -> str:
        """Build Redis key for A/B experiment.

        Format: experiment:llm:{module}:{preset}
        """
        return f"{EXPERIMENT_KEY_PREFIX}{module}:{preset}"

    def _get_experiment_config(self, module: str, preset: str, _user_id: str) -> ExperimentConfig | None:
        """
        Get experiment configuration from Redis (100% toggle behavior).

        Checks if an experiment is active for the given module/preset.
        When enabled, 100% traffic goes to experiment version.

        Args:
            module: Module name (e.g., "hrkg")
            preset: Preset name (e.g., "extraction")
            _user_id: Unused, kept for API compatibility

        Returns:
            ExperimentConfig with resolved version, or None if no active experiment
        """
        if not self.redis_available or self.redis_client is None:
            return None

        try:
            experiment_key = self._build_experiment_key(module, preset)
            content = self.redis_client.get(experiment_key)

            if content is None:
                return None

            # Validate experiment payload before trusting it
            # Note: split field is ignored for backward compatibility with old Redis keys
            parsed = json.loads(str(content))
            if (
                not isinstance(parsed, dict)
                or not isinstance(parsed.get("enabled"), bool)
                or not isinstance(parsed.get("version"), int)
                or parsed["version"] < 1
                or not isinstance(parsed.get("enabledAt"), str)
            ):
                logger.warning("[AB] Invalid experiment payload, ignoring")
                return None

            experiment: ExperimentConfig = {"enabled": parsed["enabled"], "version": parsed["version"], "enabledAt": parsed["enabledAt"]}

            # 100% toggle: return experiment if enabled, None otherwise
            if not experiment["enabled"]:
                return None

            return experiment
        except (redis.ConnectionError, redis.TimeoutError) as e:
            # Graceful degradation
            logger.warning(f"[AB] Failed to fetch experiment config: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"[AB] Invalid JSON in experiment config: {e}")
            return None
        except Exception as e:
            logger.warning(f"[AB] Failed to fetch experiment config: {e}")
            return None

    def _generate_cascade_steps(self, scope: str, module: str, user_id: str, preset: str, version: int) -> list[dict[str, Any]]:
        """
        Generate cascade steps for resolution.

        Order (most specific to least specific):
        1. {scope}:{module}:{userId}:{preset}:v{version} - Full specific (user override)
        2. {scope}:{module}:_:{preset}:v{version} - Module preset (no user)
        3. {scope}:_default:_:{preset}:v{version} - Scope-wide fallback
        4. _default:_default:_:{preset}:v{version} - Global preset fallback
        5. _default:_default:_:_base:v1 - Ultimate base fallback
        """
        steps: list[dict[str, Any]] = []

        # Step 1: Full specific (only if userId is provided and not "_")
        if user_id != "_":
            steps.append({"scope": scope, "module": module, "userId": user_id, "preset": preset, "version": version})

        # Step 2: Module preset (no user)
        steps.append({"scope": scope, "module": module, "userId": "_", "preset": preset, "version": version})

        # Step 3: Scope-wide fallback (only if module is not _default)
        if module != "_default":
            steps.append({"scope": scope, "module": "_default", "userId": "_", "preset": preset, "version": version})

        # Step 4: Global preset fallback (only if scope is not _default)
        if scope != "_default":
            steps.append({"scope": "_default", "module": "_default", "userId": "_", "preset": preset, "version": version})

        # Step 5: Ultimate base fallback (only if preset is not _base or version is not 1)
        if preset != "_base" or version != 1:
            steps.append({"scope": "_default", "module": "_default", "userId": "_", "preset": "_base", "version": 1})

        return steps

    def _resolve_with_cascade(self, scope: str, module: str, user_id: str, preset: str, version: int) -> dict[str, Any]:
        """
        Resolve config using cascade.

        Tries each cascade step in order, returning first successful load.
        """
        # Validate inputs
        validate_scope(scope)
        validate_module(module)
        validate_preset(preset)
        validate_version(version)
        # userId validation is soft - invalid IDs fall back to "_"
        valid_user_id = user_id if validate_user_id(user_id) else "_"

        steps = self._generate_cascade_steps(scope, module, valid_user_id, preset, version)
        original_config_id = self._build_config_id(scope, module, valid_user_id, preset, version)

        for step in steps:
            try:
                # Note: loadConfigFromFile uses module and preset for file path
                # scope is NOT part of file path (used for cascade resolution logic)
                config = self._load_from_file(step["module"], step["preset"], step["version"])

                resolved_config_id = self._build_config_id(step["scope"], step["module"], step["userId"], step["preset"], step["version"])

                # Log rate-limited warning if fell back to _base
                if step["preset"] == "_base" and preset != "_base":
                    self._log_fallback_warning(original_config_id, preset, "_base")

                # Check for _low fallback pattern (HRKG use case)
                if preset.endswith("_low") and step["preset"] != preset:
                    base_preset = preset[:-4]  # Remove "_low"
                    if step["preset"] == base_preset:
                        self._log_fallback_warning(original_config_id, preset, base_preset)

                return _normalize_config_keys({
                    **config,
                    "config_id": resolved_config_id,
                    "scope": step["scope"],
                    "module": step["module"],
                    "preset": step["preset"],
                    "version": step["version"],
                })
            except ConfigNotFoundError:
                # Try next cascade step
                continue

        # Should never reach here if _default/_base.v1.yaml exists
        checked = ", ".join(self._build_config_id(s["scope"], s["module"], s["userId"], s["preset"], s["version"]) for s in steps)
        raise ConfigNotFoundError(f"Config not found after cascade resolution: {original_config_id}. Checked: {checked}")

    def _log_fallback_warning(self, config_id: str, requested_preset: str, fallback_preset: str) -> None:
        """Log rate-limited fallback warning.

        Only logs once per configId to avoid log spam.
        """
        with self._warned_config_ids_lock:
            if config_id in self.warned_config_ids:
                return

            # Evict oldest if at capacity (simple FIFO approximation)
            if len(self.warned_config_ids) >= MAX_WARNED_CONFIG_IDS:
                self.warned_config_ids.popitem(last=False)

            self.warned_config_ids[config_id] = None
        logger.warning(f"LLMConfig fallback: {requested_preset} not found, using {fallback_preset} (configId: {config_id})")

    def _build_config_file_path(self, module: str, preset: str, version: int) -> Path:
        """
        Build config file path from components.

        Path format: {configDir}/{module}/{preset}.v{version}.yaml
        Note: scope is NOT part of the file path (used for cascade resolution)
        """
        filename = f"{preset}.v{version}.yaml"
        return Path(self.config_dir).resolve() / module / filename

    def _verify_path_containment(self, resolved_path: Path, base_dir: str) -> None:
        """
        Verify resolved path is within the allowed base directory.

        Security: Prevents symlink-based path traversal attacks.
        """
        normalized_base = Path(base_dir).resolve()
        normalized_path = resolved_path.resolve()

        # Resolve symlinks to get actual filesystem path
        try:
            real_path = normalized_path.resolve()
        except (OSError, RuntimeError):
            real_path = normalized_path

        try:
            real_base = normalized_base.resolve()
        except (OSError, RuntimeError):
            real_base = normalized_base

        # Check containment using is_relative_to (Python 3.9+)
        try:
            real_path.relative_to(real_base)
        except ValueError:
            raise SecurityError(f"Path traversal detected: {resolved_path} escapes base directory {base_dir}") from None

    def _load_from_file(self, module: str, preset: str, version: int) -> dict[str, Any]:
        """
        Load and validate LLM config from YAML file.

        Security features:
        - Safe YAML parsing (no code execution)
        - Path traversal protection
        - Basic schema validation

        Args:
            module: Module name (e.g., "hrkg", "_default")
            preset: Preset name (e.g., "extraction", "_base")
            version: Config version number

        Returns:
            Parsed and validated config dictionary

        Raises:
            ConfigNotFoundError: if file doesn't exist
            InvalidConfigError: if YAML parsing or validation fails
            SecurityError: if path traversal detected
        """
        # Validate inputs
        validate_module(module)
        validate_preset(preset)
        validate_version(version)

        # Build and verify file path
        file_path = self._build_config_file_path(module, preset, version)
        self._verify_path_containment(file_path, self.config_dir)

        # Read file
        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigNotFoundError(f"Config file not found: {file_path} (module={module}, preset={preset}, version={version})") from None
        except PermissionError:
            raise ConfigNotFoundError(f"Permission denied reading config file: {file_path}") from None

        # Parse YAML with safe loader
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise InvalidConfigError(f"YAML parsing failed for {file_path}: {e}") from e

        if not isinstance(parsed, dict):
            raise InvalidConfigError(f"Config must be a dictionary, got {type(parsed).__name__}")

        # Basic validation - check required fields
        if "provider" not in parsed:
            raise InvalidConfigError(f"Missing required field 'provider' in {file_path}")
        if "model" not in parsed:
            raise InvalidConfigError(f"Missing required field 'model' in {file_path}")

        return parsed

    def _try_load_from_redis(self, config_id: str) -> dict[str, Any] | None:
        """Try to load config from Redis.

        Returns config dict or None if not found/error.
        """
        if self.redis_client is None:
            return None

        try:
            redis_key = self._build_redis_key(config_id)
            content = self.redis_client.get(redis_key)

            if content is not None:
                return json.loads(str(content))

            return None
        except (redis.ConnectionError, redis.TimeoutError) as e:
            self._handle_redis_failure(e, config_id)
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in Redis for {config_id}: {e}")
            return None
        except Exception as e:
            self._handle_redis_failure(e, config_id)
            return None

    def _try_store_in_redis(self, config_id: str, serialized: str) -> None:
        """Try to store config in Redis (best effort)."""
        if not self.redis_available or self.redis_client is None:
            return

        try:
            redis_key = self._build_redis_key(config_id)
            self.redis_client.setex(redis_key, self.redis_ttl_seconds, serialized)
            logger.debug(f"Stored in Redis: {redis_key}")
        except Exception as e:
            logger.debug(f"Failed to store in Redis (non-critical): {e}")


# =============================================================================
# FACTORY FUNCTION
# =============================================================================


def create_llm_config_loader(
    config_dir: str,
    redis_url: str | None = None,
    cache_size: int = DEFAULT_CACHE_SIZE,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    redis_ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
    default_scope: str = DEFAULT_SCOPE,
) -> LLMConfigLoader:
    """
    Create a new LLMConfigLoader instance.

    Convenience factory function for creating LLMConfigLoader instances.

    Args:
        config_dir: Base directory for LLM config files (required)
        redis_url: Redis URL - optional, works without Redis
        cache_size: LRU cache max size (default: 100)
        cache_ttl_seconds: Local cache TTL in seconds (default: 21600 = 6 hours)
        redis_ttl_seconds: Redis cache TTL in seconds (default: 21600 = 6 hours, capped to local TTL)
        default_scope: Default scope for config resolution (default: "default")

    Returns:
        New LLMConfigLoader instance

    Example:
        >>> loader = create_llm_config_loader(
        ...     config_dir='/app/config/llm',
        ...     redis_url=os.environ.get('REDIS_KV_URL'),
        ... )
        >>> loader.init()  # Validates base config
        >>> config = loader.load_config(module='hrkg', preset='extraction')
    """
    return LLMConfigLoader(
        config_dir=config_dir,
        redis_url=redis_url,
        cache_size=cache_size,
        cache_ttl_seconds=cache_ttl_seconds,
        redis_ttl_seconds=redis_ttl_seconds,
        default_scope=default_scope,
    )


# =============================================================================
# SINGLETON ACCESS (optional, for convenience)
# =============================================================================

_llm_config_loader: LLMConfigLoader | None = None
_llm_config_loader_lock = threading.Lock()


def get_llm_config_loader(config_dir: str | None = None, redis_url: str | None = None) -> LLMConfigLoader:
    """
    Get or create the singleton LLMConfigLoader instance.

    Thread-safe lazy initialization. The loader is created on first access.

    Args:
        config_dir: Base directory for LLM config files (required on first call)
        redis_url: Redis URL - optional

    Returns:
        LLMConfigLoader singleton instance

    Raises:
        ValueError: If config_dir not provided on first call
    """
    global _llm_config_loader
    if _llm_config_loader is None:
        with _llm_config_loader_lock:
            if _llm_config_loader is None:
                if config_dir is None:
                    # Try to resolve from environment or default
                    from llmix.config import resolve_config_dir

                    resolved = resolve_config_dir()
                    config_dir = resolved.config_dir
                _llm_config_loader = create_llm_config_loader(config_dir=config_dir, redis_url=redis_url)
                _llm_config_loader.init()
    return _llm_config_loader


def _reset_llm_config_loader() -> None:
    """Reset the singleton for testing purposes only."""
    global _llm_config_loader
    with _llm_config_loader_lock:
        if _llm_config_loader is not None:
            _llm_config_loader.close()
            _llm_config_loader = None
