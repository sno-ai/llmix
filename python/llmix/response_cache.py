"""
Two-Tier Response Cache for LLMix

L1: In-memory LRU with TTL (cachetools.TTLCache)
L2: Redis with TTL via SETEX (redis-py, optional)

Cache key: SHA-256 of canonical JSON (sorted keys, deterministic).
Prefix: "llmix2:resp:"

Three strategies:
- "redis": strict -- fail if no REDIS_URL
- "redis-or-memory": best-effort -- degrade to L1 only with warning
- "memory": L1 only, no Redis attempted
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cachetools import TTLCache

if TYPE_CHECKING:
    import redis as redis_types  # noqa: F811

# Re-declare locally to avoid importing llmix.types at runtime
# (llmix.types pulls in lib.infra.validation which may not be available)
CachingStrategy = Literal["native", "gateway", "disabled", "redis", "redis-or-memory", "memory"]
ResponseCacheStrategy = Literal["redis", "redis-or-memory", "memory"]
CacheHitTier = Literal["l1", "l2"]

logger = logging.getLogger("llmix.cache")

# =============================================================================
# CONSTANTS
# =============================================================================

CACHE_KEY_PREFIX = "llmix2:resp:"
DEFAULT_L1_MAX = 1000
DEFAULT_TTL_SECONDS = 3600
DEFAULT_L2_TTL_SECONDS = 3600
HEALTH_PING_INTERVAL_SECONDS = 30

RESPONSE_CACHE_STRATEGIES: frozenset[str] = frozenset({"redis", "redis-or-memory", "memory"})
SKIP_STRATEGIES: frozenset[str] = frozenset({"native", "gateway", "disabled"})

# Fields included in cache key generation (alphabetical = canonical order).
# camelCase names are intentional -- they match the TypeScript cache key format
# for cross-language cache key parity. These are hash inputs, not field lookups.
CACHE_KEY_FIELDS = (
    "baseUrl",
    "maxOutputTokens",
    "messages",
    "model",
    "provider",
    "providerOptions",
    "responseFormat",
    "seed",
    "temperature",
    "topP",
)


# =============================================================================
# STRATEGY HELPERS
# =============================================================================


def is_response_cache_strategy(strategy: CachingStrategy) -> bool:
    """Check if a caching strategy activates the two-tier response cache."""
    return strategy in RESPONSE_CACHE_STRATEGIES


def should_skip_cache(strategy: CachingStrategy) -> bool:
    """Check if a caching strategy should skip the response cache."""
    return strategy in SKIP_STRATEGIES


def resolve_response_cache_strategy(
    strategy: CachingStrategy,
    redis_url: str | None,
) -> ResponseCacheStrategy | None:
    """Resolve the effective response cache strategy.

    Returns None if the strategy doesn't activate the response cache.
    Raises ValueError if "redis" is requested but REDIS_URL is missing.
    """
    if not is_response_cache_strategy(strategy):
        return None

    if strategy == "redis" and not redis_url:
        raise ValueError(
            'Response cache strategy "redis" requires REDIS_URL to be set.'
        )

    if strategy == "redis-or-memory" and not redis_url:
        logger.warning(
            'Response cache strategy "redis-or-memory": REDIS_URL not set, degrading to L1 only.'
        )
        return "memory"

    # At this point strategy is a valid ResponseCacheStrategy
    return strategy  # type: ignore[return-value]


# =============================================================================
# CACHE KEY GENERATION
# =============================================================================


def generate_cache_key(params: dict[str, Any]) -> str:
    """Generate a deterministic SHA-256 cache key from request parameters.

    - Canonical JSON with sorted keys
    - Null/None fields excluded
    - Prefixed with "llmix2:resp:"
    """
    canonical: dict[str, Any] = {}
    for field_name in CACHE_KEY_FIELDS:
        value = params.get(field_name)
        if value is not None:
            canonical[field_name] = value

    json_str = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    hash_hex = hashlib.sha256(json_str.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{hash_hex}"


# =============================================================================
# CACHED VALUE
# =============================================================================


@dataclass
class CachedValue:
    data: str
    cached_at: float


@dataclass
class CacheResult:
    value: str
    tier: CacheHitTier


@dataclass
class ResponseCacheStats:
    l1_size: int
    l1_max: int
    l2_enabled: bool
    l2_healthy: bool
    strategy: ResponseCacheStrategy


# =============================================================================
# TWO-TIER CACHE
# =============================================================================


class TwoTierCache:
    """Two-tier response cache: L1 (in-memory TTLCache) + L2 (Redis, optional)."""

    def __init__(
        self,
        strategy: ResponseCacheStrategy,
        *,
        max_items: int = DEFAULT_L1_MAX,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        redis_url: str | None = None,
    ) -> None:
        self._strategy = strategy
        self._ttl_seconds = ttl_seconds
        self._redis_url = redis_url

        # L1: cachetools.TTLCache (LRU eviction + per-entry TTL)
        self._l1: TTLCache[str, CachedValue] = TTLCache(
            maxsize=max_items, ttl=ttl_seconds
        )

        # L2 state
        self._l2_enabled = strategy != "memory" and bool(redis_url)
        self._l2_healthy = True
        self._redis_client: redis_types.Redis | None = None  # type: ignore[type-arg]
        self._last_ping_time: float = 0.0
        self._l2_last_fail_time: float = 0.0
        self._l2_retry_interval: float = 30.0  # seconds before retrying after connect failure

    def _ensure_redis(self) -> bool:
        """Lazily connect to Redis on first L2 operation."""
        if not self._l2_enabled:
            return False
        if self._redis_client is not None:
            return self._l2_healthy

        try:
            import redis as redis_lib

            client = redis_lib.Redis.from_url(
                self._redis_url,  # type: ignore[arg-type]
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=3,
            )
            client.ping()
            self._redis_client = client
            logger.info("Redis L2 connected for response cache.")
            return True
        except Exception:
            logger.warning("Failed to connect Redis L2, operating L1-only.", exc_info=True)
            self._l2_healthy = False
            self._l2_last_fail_time = time.monotonic()
            return False

    def _check_health(self) -> None:
        """Periodic health check via PING (call before L2 ops)."""
        if not self._redis_client or not self._l2_enabled:
            return

        now = time.monotonic()
        if now - self._last_ping_time < HEALTH_PING_INTERVAL_SECONDS:
            return

        self._last_ping_time = now
        try:
            self._redis_client.ping()
            if not self._l2_healthy:
                self._l2_healthy = True
                logger.info("Redis L2 recovered.")
        except Exception:
            if self._l2_healthy:
                self._l2_healthy = False
                logger.warning("Redis L2 health check failed, disabling L2 temporarily.")

    # -------------------------------------------------------------------------
    # GET
    # -------------------------------------------------------------------------

    def get(self, key: str) -> CacheResult | None:
        """Look up a cache key: L1 first, then L2 with backfill."""
        # L1 lookup
        l1_entry = self._l1.get(key)
        if l1_entry is not None:
            return CacheResult(value=l1_entry.data, tier="l1")

        # L2 lookup
        if not self._l2_enabled:
            return None
        if not self._l2_healthy:
            # Retry connection after backoff period
            if time.monotonic() - self._l2_last_fail_time < self._l2_retry_interval:
                return None
            # Enough time has passed -- attempt reconnect
            self._l2_healthy = True
            self._redis_client = None

        try:
            connected = self._ensure_redis()
            if not connected or not self._redis_client:
                return None

            self._check_health()
            if not self._l2_healthy:
                return None

            raw = self._redis_client.get(key)
            if raw is None:
                return None

            parsed = json.loads(str(raw))
            cached = CachedValue(data=parsed["data"], cached_at=parsed["cached_at"])

            # Backfill L1 with a fresh TTL. This is an accepted tradeoff:
            # L1 entries are evicted by LRU anyway, and backfill exists to
            # prevent repeated L2 lookups -- a fresh TTL is fine here.
            self._l1[key] = cached

            return CacheResult(value=cached.data, tier="l2")
        except Exception:
            logger.warning("L2 GET failed, treating as cache miss.", exc_info=True)
            return None

    # -------------------------------------------------------------------------
    # SET
    # -------------------------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        """Write to L1 (sync) and L2 (best-effort)."""
        entry = CachedValue(data=value, cached_at=time.time())

        # L1 write
        self._l1[key] = entry

        # L2 write (best-effort, not fire-and-forget in sync Python but non-blocking in spirit)
        if self._l2_enabled and self._l2_healthy:
            self._write_l2(key, entry)

    def _write_l2(self, key: str, entry: CachedValue) -> None:
        """Write to Redis with SETEX (TTL)."""
        try:
            connected = self._ensure_redis()
            if not connected or not self._redis_client:
                return

            serialized = json.dumps({"data": entry.data, "cached_at": entry.cached_at})
            ttl = self._ttl_seconds if self._ttl_seconds > 0 else DEFAULT_L2_TTL_SECONDS
            self._redis_client.setex(key, ttl, serialized)
        except Exception:
            logger.warning("L2 SET failed (best-effort).", exc_info=True)

    # -------------------------------------------------------------------------
    # ASYNC VARIANTS (for async callers)
    # -------------------------------------------------------------------------

    async def aget(self, key: str) -> CacheResult | None:
        """Async wrapper for get(). L1 is sync; L2 uses sync Redis under the hood."""
        return self.get(key)

    async def aset(self, key: str, value: str) -> None:
        """Async wrapper for set()."""
        self.set(key, value)

    # -------------------------------------------------------------------------
    # LIFECYCLE
    # -------------------------------------------------------------------------

    def get_stats(self) -> ResponseCacheStats:
        return ResponseCacheStats(
            l1_size=len(self._l1),
            l1_max=int(self._l1.maxsize),
            l2_enabled=self._l2_enabled,
            l2_healthy=self._l2_healthy,
            strategy=self._strategy,  # type: ignore[arg-type]
        )

    def clear(self) -> None:
        """Clear L1 cache. Does not clear L2 (Redis manages its own TTL)."""
        self._l1.clear()

    def close(self) -> None:
        """Shut down: disconnect Redis."""
        if self._redis_client:
            try:
                self._redis_client.close()
            except Exception:
                pass
            self._redis_client = None
