#!/usr/bin/env python3
"""
Response cache unit tests.
Consumes shared test vectors from fixtures/cache-key-vectors.json.

Tests:
- Cache key determinism (shared vectors)
- Cache key collision avoidance
- L1 hit/miss/eviction
- Strategy resolution
- Cache skip rules

Run with: python tests/python/test_response_cache.py
"""

import json
import sys
import time
from pathlib import Path

# Add python/ to path so llmix is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from llmix.response_cache import (  # noqa: E402
    TwoTierCache,
    generate_cache_key,
    is_response_cache_strategy,
    resolve_response_cache_strategy,
    should_skip_cache,
)

fixture_dir = Path(__file__).parent.parent / "fixtures"
vectors_file = fixture_dir / "cache-key-vectors.json"
vectors_data = json.loads(vectors_file.read_text())

passed = 0
failed = 0


def assert_eq(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"+ {msg}")
    else:
        failed += 1
        print(f"x {msg}")


def assert_equal(actual: object, expected: object, msg: str) -> None:
    assert_eq(actual == expected, f"{msg}: got {actual!r}, expected {expected!r}")


class FakeRedisClient:
    def __init__(self) -> None:
        self.closed = False
        self.storage: dict[str, str] = {}

    def close(self) -> None:
        self.closed = True

    def ping(self) -> None:
        return None

    def get(self, key: str) -> str | None:
        return self.storage.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.storage[key] = value


# =============================================================================
# CACHE KEY TESTS (shared vectors)
# =============================================================================

print("--- Cache Key Determinism (shared vectors) ---")

for vec in vectors_data["vectors"]:
    key = generate_cache_key(vec["input"])
    assert_equal(key, vec["expectedKey"], f"[{vec['name']}] cache key")


# =============================================================================
# CACHE KEY COLLISION AVOIDANCE
# =============================================================================

print("\n--- Cache Key Collision Avoidance ---")

key_map: dict[str, str] = {}
expected_collisions = {"same-params-different-order", "null-and-undefined-fields-excluded"}

for vec in vectors_data["vectors"]:
    key = generate_cache_key(vec["input"])
    if key in key_map:
        other = key_map[key]
        is_expected = vec["name"] in expected_collisions
        assert_eq(is_expected, f"[{vec['name']}] key collision with {other} (expected: {is_expected})")
    else:
        key_map[key] = vec["name"]

# Verify prefix
for vec in vectors_data["vectors"]:
    key = generate_cache_key(vec["input"])
    assert_eq(key.startswith("llmix:resp:"), f"[{vec['name']}] has correct prefix")


# =============================================================================
# L1 HIT / MISS / EVICTION
# =============================================================================

print("\n--- L1 Hit / Miss / Eviction ---")

cache = TwoTierCache("memory", max_items=3, ttl_seconds=60)

# Miss on empty cache
miss = cache.get("key1")
assert_equal(miss, None, "empty cache returns None")

# Set and hit
cache.set("key1", "value1")
hit = cache.get("key1")
assert_eq(hit is not None, "L1 hit after set")
assert_equal(hit.value if hit else None, "value1", "L1 hit returns correct value")
assert_equal(hit.tier if hit else None, "l1", "L1 hit reports tier l1")

# Multiple entries
cache.set("key2", "value2")
cache.set("key3", "value3")

stats = cache.get_stats()
assert_equal(stats.l1_size, 3, "L1 has 3 entries")
assert_equal(stats.l1_max, 3, "L1 max is 3")
assert_equal(stats.l2_enabled, False, "L2 disabled for memory strategy")
assert_equal(stats.strategy, "memory", "strategy is memory")

# Eviction: adding 4th item should evict oldest (LRU via TTLCache)
cache.set("key4", "value4")
evicted_stats = cache.get_stats()
assert_equal(evicted_stats.l1_size, 3, "L1 size stays at max after eviction")

# key1 should have been evicted (LRU - least recently used)
evicted = cache.get("key1")
assert_equal(evicted, None, "evicted key returns None")

# key4 should be present
newest = cache.get("key4")
assert_eq(newest is not None, "newest key is present after eviction")
assert_equal(newest.value if newest else None, "value4", "newest key has correct value")

# Clear
cache.clear()
cleared = cache.get("key4")
assert_equal(cleared, None, "cache clear removes all entries")
assert_equal(cache.get_stats().l1_size, 0, "L1 size is 0 after clear")

cache.close()


# =============================================================================
# STRATEGY RESOLUTION
# =============================================================================

print("\n--- Strategy Resolution ---")

assert_eq(is_response_cache_strategy("redis"), '"redis" is response cache strategy')
assert_eq(is_response_cache_strategy("redis-or-memory"), '"redis-or-memory" is response cache strategy')
assert_eq(is_response_cache_strategy("memory"), '"memory" is response cache strategy')
assert_eq(not is_response_cache_strategy("native"), '"native" is not response cache strategy')
assert_eq(not is_response_cache_strategy("gateway"), '"gateway" is not response cache strategy')
assert_eq(not is_response_cache_strategy("disabled"), '"disabled" is not response cache strategy')

assert_eq(should_skip_cache("native"), '"native" should skip cache')
assert_eq(should_skip_cache("gateway"), '"gateway" should skip cache')
assert_eq(should_skip_cache("disabled"), '"disabled" should skip cache')
assert_eq(not should_skip_cache("redis"), '"redis" should not skip cache')

# resolve with URL
assert_equal(
    resolve_response_cache_strategy("redis", "redis://localhost:6379"),
    "redis",
    'resolve "redis" with URL',
)

# resolve without URL -> error
try:
    resolve_response_cache_strategy("redis", None)
    assert_eq(False, 'resolve "redis" without URL should raise')
except ValueError as e:
    assert_eq("REDIS_URL" in str(e), 'resolve "redis" without URL raises')

# redis-or-memory with URL
assert_equal(
    resolve_response_cache_strategy("redis-or-memory", "redis://localhost:6379"),
    "redis-or-memory",
    'resolve "redis-or-memory" with URL',
)

# redis-or-memory without URL -> degrades
assert_equal(
    resolve_response_cache_strategy("redis-or-memory", None),
    "memory",
    'resolve "redis-or-memory" without URL degrades to memory',
)

# memory
assert_equal(
    resolve_response_cache_strategy("memory", None),
    "memory",
    'resolve "memory" without URL',
)

# non-response-cache strategies return None
assert_equal(
    resolve_response_cache_strategy("native", None),
    None,
    'resolve "native" returns None',
)

assert_equal(
    resolve_response_cache_strategy("disabled", None),
    None,
    'resolve "disabled" returns None',
)


# =============================================================================
# REDIS INTEGRATION TESTS (stubs with TODO)
# =============================================================================

print("\n--- Redis Integration Tests (stubs) ---")

# TODO: Task 134 - Redis integration tests
# These require a running Redis instance.
redis_cache = TwoTierCache("redis", redis_url="redis://localhost:6379", max_items=100, ttl_seconds=60)
stats = redis_cache.get_stats()
assert_equal(stats.l2_enabled, True, "L2 enabled for redis strategy with URL")
assert_equal(stats.l2_healthy, True, "L2 healthy before first use")
redis_cache.close()

print("TODO: Redis L2 hit/miss tests require running Redis")
print("TODO: Redis health monitoring tests require running Redis")
print("TODO: Redis L2 backfill tests require running Redis")


# =============================================================================
# NaN / NONE CACHE KEY COLLISION
# =============================================================================

print("\n--- NaN / None Cache Key Collision ---")

# Both None and NaN are excluded from the canonical hash, so a request that
# provides NaN for a field produces the same key as one that omits the field
# entirely. This is intentional (cross-language parity with JS), but it means
# callers must not rely on NaN as a distinct sentinel.
import math

nan_key = generate_cache_key({"model": "gpt-4o", "temperature": float("nan")})
none_key = generate_cache_key({"model": "gpt-4o", "temperature": None})
absent_key = generate_cache_key({"model": "gpt-4o"})

assert_equal(nan_key, none_key, "NaN temperature produces same key as None temperature")
assert_equal(nan_key, absent_key, "NaN temperature produces same key as absent temperature")

# Infinity is also excluded (non-finite)
inf_key = generate_cache_key({"model": "gpt-4o", "temperature": float("inf")})
assert_equal(inf_key, absent_key, "Infinity temperature produces same key as absent temperature")

# A finite non-None value must produce a DIFFERENT key
finite_key = generate_cache_key({"model": "gpt-4o", "temperature": 0.7})
assert_eq(finite_key != absent_key, "Finite temperature 0.7 produces different key from absent")

lone_surrogate_key = generate_cache_key({"model": "gpt-4o", "messages": [{"role": "user", "content": "\ud800"}]})
assert_eq(
    lone_surrogate_key.startswith("llmix:resp:"),
    "Lone surrogate content does not raise during cache key generation",
)


# =============================================================================
# L2 WRITE FAILURE THRESHOLD
# =============================================================================

print("\n--- L2 Write Failure Threshold ---")


class FailingRedisClient(FakeRedisClient):
    """Redis client that raises on every setex() call."""
    def setex(self, key: str, ttl: int, value: str) -> None:
        raise ConnectionError("Redis write failed")


# After 3 consecutive write failures, L2 must be marked unhealthy.
# Easy-to-trigger bug: if the threshold is off-by-one (>= 3 vs > 3),
# the 3rd failure keeps L2 in a bad state longer than expected.
fail_client = FailingRedisClient()
write_fail_cache = TwoTierCache(
    "redis-or-memory",
    redis_url="redis://localhost:6379",
    max_items=10,
    ttl_seconds=60,
)
write_fail_cache._redis_client = fail_client
write_fail_cache._l2_healthy = True
write_fail_cache._ensure_redis = lambda: True  # type: ignore[method-assign]

# First two write failures should NOT mark unhealthy
write_fail_cache.set("key1", "val1")
assert_equal(write_fail_cache.get_stats().l2_healthy, True, "L2 still healthy after 1st write failure")
write_fail_cache.set("key2", "val2")
assert_equal(write_fail_cache.get_stats().l2_healthy, True, "L2 still healthy after 2nd write failure")

# Third write failure crosses the threshold (>= 3) -> unhealthy
write_fail_cache.set("key3", "val3")
assert_equal(write_fail_cache.get_stats().l2_healthy, False, "L2 marked unhealthy after 3rd write failure")
write_fail_cache.close()


# =============================================================================
# L2 RECOVERY REGRESSION TESTS
# =============================================================================

print("\n--- L2 Recovery Regression Tests ---")

stale_client = FakeRedisClient()
reconnect_cache = TwoTierCache(
    "redis-or-memory",
    redis_url="redis://localhost:6379",
    max_items=10,
    ttl_seconds=60,
)
reconnect_cache._redis_client = stale_client
reconnect_cache._l2_healthy = False
reconnect_cache._l2_last_fail_time = time.monotonic() - reconnect_cache._l2_retry_interval - 1
reconnect_cache._ensure_redis = lambda: False  # type: ignore[method-assign]

reconnect_result = reconnect_cache.get("missing")
assert_equal(reconnect_result, None, "failed reconnect is treated as cache miss")
assert_eq(stale_client.closed, "failed reconnect closes stale Redis client before retry")
assert_equal(reconnect_cache.get_stats().l2_healthy, False, "failed reconnect keeps L2 unhealthy until connect succeeds")
reconnect_cache.close()

write_retry_client = FakeRedisClient()
write_retry_cache = TwoTierCache(
    "redis-or-memory",
    redis_url="redis://localhost:6379",
    max_items=10,
    ttl_seconds=60,
)
write_retry_cache._l2_healthy = False
write_retry_cache._l2_last_fail_time = time.monotonic() - write_retry_cache._l2_retry_interval - 1
write_retry_cache._redis_client = write_retry_client
write_retry_cache._ensure_redis = lambda: True  # type: ignore[method-assign]

write_retry_cache.set("recover-key", "recover-value")
assert_equal(
    json.loads(write_retry_client.storage["recover-key"])["data"],
    "recover-value",
    "set retries L2 writes after unhealthy backoff elapses",
)
write_retry_cache.close()


# =============================================================================
# SUMMARY
# =============================================================================

print(f"\n=== Response Cache Tests: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
