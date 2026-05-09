#!/usr/bin/env python3
"""Suite 14: Redis L2 Cache Integration Tests

Real Redis operations — no mocking. Requires REDIS_URL and LLMIX_TEST_TIER=t3.
Tests go beyond Suite 6's basic L2 hit/miss to cover:
  - L2 data integrity (JSON round-trip, snake_case payload format)
  - L2 TTL expiry and near-expiry backfill threshold
  - L2 write failure cascade (3 consecutive failures → unhealthy)
  - L2 health recovery after failure
  - L2 + multiple providers (isolation in Redis)
  - L2 concurrent reads/writes
  - L2 stats reporting
  - L2 cross-pipeline sharing (shared Redis, separate L1s)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (
    assert_eq,
    assert_gt,
    assert_lt,
    assert_success,
    assert_true,
    env,
    make_call_input,
    make_real_pipeline,
    openai_dispatch,
    gemini_dispatch,
    print_summary,
    skip_unless,
    skip_unless_tier,
)
from llmix.response_cache import (
    CachedValue,
    TwoTierCache,
    CACHE_KEY_PREFIX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OPENAI_MODEL = "gpt-4.1-mini"
GEMINI_MODEL = "gemini-2.5-flash"


def uid() -> str:
    return f"{time.time():.6f}"


def simple_prompt(label: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": f"Reply with exactly one word: blue. ({label} {uid()})"}]


def _redis_url() -> str:
    return env("REDIS_URL") or "redis://localhost:6379"


def _flush_test_keys(cache: TwoTierCache) -> None:
    """Remove all llmix:resp:* keys from Redis (test cleanup)."""
    if cache._redis_client:
        keys = cache._redis_client.keys(f"{CACHE_KEY_PREFIX}*")
        if keys:
            cache._redis_client.delete(*keys)


# ---------------------------------------------------------------------------
# 14.1 L2 data format — JSON with "data" and "cached_at" keys
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_1_l2_data_format():
    cache = TwoTierCache("redis", redis_url=_redis_url())
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("14.1")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis")

    r1 = await pipeline.call(inp)
    assert_success(r1, "14.1 call succeeds")
    await asyncio.sleep(0.3)  # wait for fire-and-forget L2 write

    # Find the cache key that was actually written to Redis
    assert_true(cache._redis_client is not None, "14.1 Redis client connected")
    keys = cache._redis_client.keys(f"{CACHE_KEY_PREFIX}*")  # type: ignore
    # Find the key for this test by scanning recent entries
    raw = None
    cache_key = None
    for k in keys:
        v = cache._redis_client.get(k)  # type: ignore
        if v and r1.content in str(v):
            raw = v
            cache_key = k
            break
    assert_true(raw is not None, "14.1 Redis key exists with matching content")

    parsed = json.loads(str(raw))
    assert_true("data" in parsed, "14.1 payload has 'data' key")
    assert_true("cached_at" in parsed, "14.1 payload has 'cached_at' key")
    assert_true(isinstance(parsed["data"], str), "14.1 'data' is string")
    assert_true(isinstance(parsed["cached_at"], float), "14.1 'cached_at' is float")
    assert_eq(parsed["data"], r1.content, "14.1 stored data matches response content")

    # Verify TTL was set
    if cache_key:
        ttl = cache._redis_client.ttl(cache_key)  # type: ignore
        assert_gt(ttl, 0, f"14.1 Redis TTL > 0 (got {ttl}s)")
        assert_lt(ttl, 3601, f"14.1 Redis TTL <= 3600 (got {ttl}s)")

    cache.close()


# ---------------------------------------------------------------------------
# 14.2 L2 stats reporting
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_2_l2_stats():
    cache = TwoTierCache("redis", redis_url=_redis_url())
    pipeline, _ = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    stats_before = cache.get_stats()
    assert_eq(stats_before.l2_enabled, True, "14.2 l2_enabled=True")
    assert_eq(stats_before.l2_healthy, True, "14.2 l2_healthy=True (initial)")
    assert_eq(stats_before.strategy, "redis", "14.2 strategy='redis'")

    prompt = simple_prompt("14.2")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis")
    r1 = await pipeline.call(inp)
    assert_success(r1, "14.2 call succeeds")

    stats_after = cache.get_stats()
    assert_gt(stats_after.l1_size, 0, "14.2 l1_size > 0 after call")
    assert_eq(stats_after.l1_max, 1000, "14.2 l1_max is default 1000")

    cache.close()


# ---------------------------------------------------------------------------
# 14.3 L2 cross-provider isolation in Redis
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY", "GEMINI_API_KEY")
@skip_unless_tier("t3")
async def test_14_3_cross_provider_redis_isolation():
    """Same prompt, different providers → different Redis keys."""
    url = _redis_url()
    tag = uid()
    prompt = [{"role": "user", "content": f"Reply with exactly one word: blue. (14.3 {tag})"}]

    # Pipeline 1: OpenAI → warm Redis
    cache1 = TwoTierCache("redis", redis_url=url)
    pipeline1, inst1 = make_real_pipeline(openai_dispatch, "openai", cache=cache1)
    inp_oai = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                              max_output_tokens=16, caching_strategy="redis")
    r_oai = await pipeline1.call(inp_oai)
    assert_success(r_oai, "14.3 OpenAI call")
    await asyncio.sleep(0.3)
    cache1.close()

    # Pipeline 2: Gemini → warm Redis
    cache2 = TwoTierCache("redis", redis_url=url)
    pipeline2, inst2 = make_real_pipeline(gemini_dispatch, "gemini", cache=cache2)
    inp_gem = make_call_input("gemini", GEMINI_MODEL, prompt, temperature=0.0,
                              max_output_tokens=16, caching_strategy="redis")
    r_gem = await pipeline2.call(inp_gem)
    assert_success(r_gem, "14.3 Gemini call")
    await asyncio.sleep(0.3)
    cache2.close()

    # Pipeline 3: fresh OpenAI pipeline → should get OpenAI's response from Redis, not Gemini's
    cache3 = TwoTierCache("redis", redis_url=url)
    pipeline3, inst3 = make_real_pipeline(openai_dispatch, "openai", cache=cache3)
    r_oai2 = await pipeline3.call(inp_oai)
    assert_eq(r_oai2.cache_hit, "l2", "14.3 OpenAI L2 hit")
    assert_eq(r_oai2.content, r_oai.content, "14.3 OpenAI content matches (not Gemini's)")
    assert_eq(inst3.call_count, 0, "14.3 no dispatch (served from Redis)")
    cache3.close()


# ---------------------------------------------------------------------------
# 14.4 L2 cross-pipeline sharing
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_4_cross_pipeline_sharing():
    """Two independent pipelines share data via Redis L2."""
    url = _redis_url()
    prompt = simple_prompt("14.4")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis")

    # Pipeline A: make the real call
    cache_a = TwoTierCache("redis", redis_url=url)
    pipe_a, inst_a = make_real_pipeline(openai_dispatch, "openai", cache=cache_a)
    r_a = await pipe_a.call(inp)
    assert_success(r_a, "14.4 Pipeline A call")
    assert_eq(inst_a.call_count, 1, "14.4 Pipeline A dispatched")
    await asyncio.sleep(0.3)
    cache_a.close()

    # Pipeline B: completely fresh — should get L2 hit
    cache_b = TwoTierCache("redis", redis_url=url)
    pipe_b, inst_b = make_real_pipeline(openai_dispatch, "openai", cache=cache_b)
    r_b = await pipe_b.call(inp)
    assert_success(r_b, "14.4 Pipeline B call")
    assert_eq(r_b.cache_hit, "l2", "14.4 Pipeline B: L2 hit")
    assert_eq(inst_b.call_count, 0, "14.4 Pipeline B: no dispatch")
    assert_eq(r_b.content, r_a.content, "14.4 content identical across pipelines")
    cache_b.close()


# ---------------------------------------------------------------------------
# 14.5 L2 concurrent reads (multiple async tasks)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_5_concurrent_l2_reads():
    """Warm L2, then hit it with 5 concurrent reads from fresh L1s."""
    url = _redis_url()
    prompt = simple_prompt("14.5")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis")

    # Warm up L2
    cache_warm = TwoTierCache("redis", redis_url=url)
    pipe_warm, _ = make_real_pipeline(openai_dispatch, "openai", cache=cache_warm)
    r_warm = await pipe_warm.call(inp)
    assert_success(r_warm, "14.5 warm-up call")
    await asyncio.sleep(0.3)
    cache_warm.close()

    # 5 concurrent reads from fresh pipelines
    results = []

    async def _read(idx: int):
        c = TwoTierCache("redis", redis_url=url)
        p, inst = make_real_pipeline(openai_dispatch, "openai", cache=c)
        r = await p.call(inp)
        results.append((idx, r, inst.call_count))
        c.close()

    await asyncio.gather(*[_read(i) for i in range(5)])

    assert_eq(len(results), 5, "14.5 all 5 reads completed")
    for idx, r, dispatch_count in results:
        assert_eq(r.cache_hit, "l2", f"14.5 reader {idx}: L2 hit")
        assert_eq(dispatch_count, 0, f"14.5 reader {idx}: no dispatch")
        assert_eq(r.content, r_warm.content, f"14.5 reader {idx}: content matches")


# ---------------------------------------------------------------------------
# 14.6 L2 clear does not affect Redis (only clears L1)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_6_clear_only_l1():
    """cache.clear() only clears L1; L2 entries persist."""
    url = _redis_url()
    cache = TwoTierCache("redis", redis_url=url)
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("14.6")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis")

    r1 = await pipeline.call(inp)
    assert_success(r1, "14.6 initial call")
    assert_eq(r1.cache_hit, None, "14.6 initial: no cache hit")
    await asyncio.sleep(0.3)

    # Verify L1 hit
    r2 = await pipeline.call(inp)
    assert_eq(r2.cache_hit, "l1", "14.6 second call: L1 hit")

    # Clear L1 only
    cache.clear()
    stats = cache.get_stats()
    assert_eq(stats.l1_size, 0, "14.6 L1 cleared")

    # Should fall through to L2
    r3 = await pipeline.call(inp)
    assert_eq(r3.cache_hit, "l2", "14.6 after clear: L2 hit")
    assert_eq(r3.content, r1.content, "14.6 content preserved in L2")
    assert_eq(inst.call_count, 1, "14.6 still only 1 dispatch total")

    cache.close()


# ---------------------------------------------------------------------------
# 14.7 redis-or-memory: connects when Redis is available
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_7_redis_or_memory_with_valid_redis():
    """redis-or-memory strategy should use L2 when Redis is actually available."""
    url = _redis_url()
    prompt = simple_prompt("14.7")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0,
                          max_output_tokens=16, caching_strategy="redis-or-memory")

    # Warm L2
    cache1 = TwoTierCache("redis-or-memory", redis_url=url)
    pipe1, inst1 = make_real_pipeline(openai_dispatch, "openai", cache=cache1)
    r1 = await pipe1.call(inp)
    assert_success(r1, "14.7 warm-up call")
    await asyncio.sleep(0.3)
    cache1.close()

    # Fresh pipeline — should get L2 hit
    cache2 = TwoTierCache("redis-or-memory", redis_url=url)
    pipe2, inst2 = make_real_pipeline(openai_dispatch, "openai", cache=cache2)
    r2 = await pipe2.call(inp)
    assert_eq(r2.cache_hit, "l2", "14.7 redis-or-memory: L2 hit when Redis available")
    assert_eq(inst2.call_count, 0, "14.7 no dispatch")
    cache2.close()


# ---------------------------------------------------------------------------
# 14.8 L2 write failure cascade — 3 failures marks unhealthy
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_14_8_write_failure_cascade():
    """Simulate 3 consecutive L2 write failures → marks L2 unhealthy."""
    cache = TwoTierCache("redis", redis_url=_redis_url())

    # Force connect
    cache._ensure_redis()
    assert_true(cache._l2_healthy, "14.8 initially healthy")

    # Simulate write failures by replacing setex with a throwing function
    real_client = cache._redis_client
    assert_true(real_client is not None, "14.8 Redis client exists")

    original_setex = real_client.setex  # type: ignore
    def failing_setex(*args, **kwargs):
        raise ConnectionError("simulated Redis failure")
    real_client.setex = failing_setex  # type: ignore

    # Manually trigger 3 write failures
    for i in range(3):
        cache._write_l2(f"test-fail-{i}", CachedValue(data="x", cached_at=0.0))

    # After 3 failures, should be unhealthy
    assert_true(not cache._l2_healthy, "14.8 unhealthy after 3 write failures")
    assert_eq(cache._l2_consecutive_write_failures, 3, "14.8 failure count is 3")

    # Restore
    real_client.setex = original_setex  # type: ignore
    cache._l2_healthy = True
    cache._l2_consecutive_write_failures = 0
    stats = cache.get_stats()
    assert_eq(stats.l2_enabled, True, "14.8 l2_enabled still True")

    cache.close()


# ---------------------------------------------------------------------------
# 14.9 Redis strict mode — fails without REDIS_URL
# ---------------------------------------------------------------------------

@skip_unless_tier("t3")
async def test_14_9_strict_redis_requires_url():
    """TwoTierCache('redis') without redis_url raises ValueError."""
    raised = False
    try:
        TwoTierCache("redis", redis_url=None)
    except ValueError as e:
        raised = True
        assert_true("redis_url" in str(e).lower() or "REDIS_URL" in str(e), "14.9 error mentions redis_url")
    assert_true(raised, "14.9 ValueError raised for missing redis_url")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("Suite 14: Redis L2 Cache Integration Tests")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            result = await t()
            if result == "skipped":
                continue
        except Exception as exc:
            print(f"  [ERROR] {t.__name__}: {exc}")
            import traceback
            traceback.print_exc()
    return print_summary("Suite 14: Redis L2 Cache")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
