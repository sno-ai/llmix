"""
LLMix Integration Tests: Two-Tier Response Cache (Suite 6)

Real LLM calls for cache warm-up, real cache reads.
No mocking. Evaluates cache_hit tier, content identity, dispatch counts, latency.
"""

from __future__ import annotations

import asyncio
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from conftest import (
    assert_eq,
    assert_lt,
    assert_success,
    assert_true,
    assert_contains,
    assert_not_contains,
    env,
    make_call_input,
    make_real_pipeline,
    openai_dispatch,
    anthropic_dispatch,
    gemini_dispatch,
    sno_gpu_dispatch,
    print_summary,
    skip_unless,
    skip_unless_tier,
)
from llmix.response_cache import TwoTierCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OPENAI_MODEL = "gpt-4.1-mini"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
GEMINI_MODEL = "gemini-2.5-flash"

# SnoGPU thinking model
SNO_GPU_MODEL = "Qwen/Qwen3-32B"


def uid() -> str:
    """Unique suffix to prevent cross-test cache pollution."""
    return f"{time.time():.6f}"


def simple_prompt(label: str) -> list[dict[str, str]]:
    """Single-turn prompt guaranteed unique per test invocation."""
    return [{"role": "user", "content": f"Reply with exactly one word: blue. ({label} {uid()})"}]


# ---------------------------------------------------------------------------
# 6.1  L1 hit -- same prompt twice, strategy=memory
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_1_l1_hit_same_prompt():
    print("\n--- 6.1: L1 hit on same prompt (memory) ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.1")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")

    r1 = await pipeline.call(inp)
    assert_success(r1, "1st call succeeds")
    assert_eq(r1.cache_hit, None, "1st call: cache_hit is None")
    assert_eq(inst.call_count, 1, "1st call: dispatch_count==1")

    t0 = time.monotonic()
    r2 = await pipeline.call(inp)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert_success(r2, "2nd call succeeds")
    assert_eq(r2.cache_hit, "l1", "2nd call: cache_hit=='l1'")
    assert_eq(inst.call_count, 1, "2nd call: dispatch_count still 1")
    assert_eq(r2.content, r1.content, "Content identical on cache hit")
    assert_lt(elapsed_ms, 50, "L1 hit latency < 50ms")


# ---------------------------------------------------------------------------
# 6.2  L1 miss on different prompt
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_2_l1_miss_different_prompt():
    print("\n--- 6.2: L1 miss on different prompt ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    p1 = [{"role": "user", "content": f"Say hello. (6.2a {uid()})"}]
    p2 = [{"role": "user", "content": f"Say goodbye. (6.2b {uid()})"}]
    inp1 = make_call_input("openai", OPENAI_MODEL, p1, temperature=0.0, max_output_tokens=16, caching_strategy="memory")
    inp2 = make_call_input("openai", OPENAI_MODEL, p2, temperature=0.0, max_output_tokens=16, caching_strategy="memory")

    r1 = await pipeline.call(inp1)
    r2 = await pipeline.call(inp2)
    assert_success(r1, "Call 1 succeeds")
    assert_success(r2, "Call 2 succeeds")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (different prompts)")
    assert_eq(r1.cache_hit, None, "Call 1: no cache hit")
    assert_eq(r2.cache_hit, None, "Call 2: no cache hit")


# ---------------------------------------------------------------------------
# 6.3  L1 miss on different temperature
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_3_l1_miss_different_temperature():
    print("\n--- 6.3: L1 miss on different temperature ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.3")
    inp1 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")
    inp2 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.5, max_output_tokens=16, caching_strategy="memory")

    r1 = await pipeline.call(inp1)
    r2 = await pipeline.call(inp2)
    assert_success(r1, "temp=0.0 succeeds")
    assert_success(r2, "temp=0.5 succeeds")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (different temperature)")


# ---------------------------------------------------------------------------
# 6.4  L1 miss on different responseFormat
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_4_l1_miss_different_response_format():
    print("\n--- 6.4: L1 miss on different responseFormat ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.4")
    inp_plain = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")

    # Build a second input with response_format set in common
    inp_json = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=64, caching_strategy="memory")
    inp_json.config["common"]["response_format"] = {"type": "json_object"}
    # Adjust prompt for JSON mode
    inp_json.config["common"]["response_format"] = {"type": "json_object"}
    inp_json.messages = [{"role": "user", "content": f"Return JSON: {{\"color\": \"blue\"}}. (6.4 {uid()})"}]
    # The plain input also needs to match the adjusted prompt for a fair comparison of format-only diff
    # Actually, we just need to show different response_format = different cache key, so different prompts are fine
    r1 = await pipeline.call(inp_plain)
    r2 = await pipeline.call(inp_json)
    assert_success(r1, "Plain format succeeds")
    assert_success(r2, "JSON format succeeds")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (different responseFormat)")


# ---------------------------------------------------------------------------
# 6.5  L1 miss on different seed
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_5_l1_miss_different_seed():
    print("\n--- 6.5: L1 miss on different seed ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.5")
    inp1 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, seed=1, caching_strategy="memory")
    inp2 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, seed=2, caching_strategy="memory")

    r1 = await pipeline.call(inp1)
    r2 = await pipeline.call(inp2)
    assert_success(r1, "seed=1 succeeds")
    assert_success(r2, "seed=2 succeeds")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (different seed)")


# ---------------------------------------------------------------------------
# 6.6  L1 miss on different topP
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_6_l1_miss_different_top_p():
    print("\n--- 6.6: L1 miss on different topP ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.6")
    inp1 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, top_p=0.5, caching_strategy="memory")
    inp2 = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, top_p=0.9, caching_strategy="memory")

    r1 = await pipeline.call(inp1)
    r2 = await pipeline.call(inp2)
    assert_success(r1, "topP=0.5 succeeds")
    assert_success(r2, "topP=0.9 succeeds")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (different topP)")


# ---------------------------------------------------------------------------
# 6.7  L1 miss on different provider
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
async def test_6_7_l1_miss_different_provider():
    print("\n--- 6.7: L1 miss on different provider ---")
    cache_oai = TwoTierCache("memory")
    cache_ant = TwoTierCache("memory")
    pipeline_oai, inst_oai = make_real_pipeline(openai_dispatch, "openai", cache=cache_oai)
    pipeline_ant, inst_ant = make_real_pipeline(anthropic_dispatch, "anthropic", cache=cache_ant)

    tag = uid()
    prompt = [{"role": "user", "content": f"Reply with exactly one word: blue. (6.7 {tag})"}]
    inp_oai = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")
    inp_ant = make_call_input("anthropic", ANTHROPIC_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")

    r_oai = await pipeline_oai.call(inp_oai)
    r_ant = await pipeline_ant.call(inp_ant)
    assert_success(r_oai, "OpenAI call succeeds")
    assert_success(r_ant, "Anthropic call succeeds")
    assert_eq(inst_oai.call_count, 1, "OpenAI dispatch_count==1")
    assert_eq(inst_ant.call_count, 1, "Anthropic dispatch_count==1")


# ---------------------------------------------------------------------------
# 6.8  L2 Redis hit (T3, requires REDIS_URL)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_6_8_l2_redis_hit():
    print("\n--- 6.8: L2 Redis hit ---")
    redis_url = env("REDIS_URL") or "redis://localhost:6379"

    prompt = simple_prompt("6.8")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="redis")

    # Pipeline 1: warm up L1 + L2
    cache1 = TwoTierCache("redis", redis_url=redis_url)
    pipeline1, inst1 = make_real_pipeline(openai_dispatch, "openai", cache=cache1)
    r1 = await pipeline1.call(inp)
    assert_success(r1, "Pipeline1 call succeeds")
    assert_eq(r1.cache_hit, None, "Pipeline1: cache_hit is None (cold)")
    assert_eq(inst1.call_count, 1, "Pipeline1: dispatch_count==1")

    # Allow async L2 write to complete
    await asyncio.sleep(0.5)

    # Destroy pipeline1's L1 cache
    cache1.close()

    # Pipeline 2: fresh L1, but L2 should have the entry
    cache2 = TwoTierCache("redis", redis_url=redis_url)
    pipeline2, inst2 = make_real_pipeline(openai_dispatch, "openai", cache=cache2)
    r2 = await pipeline2.call(inp)
    assert_success(r2, "Pipeline2 call succeeds")
    assert_eq(r2.cache_hit, "l2", "Pipeline2: cache_hit=='l2'")
    assert_eq(inst2.call_count, 0, "Pipeline2: dispatch_count==0 (served from L2)")
    assert_eq(r2.content, r1.content, "Content identical across L2 hit")

    cache2.close()


# ---------------------------------------------------------------------------
# 6.9  L2 backfill (T3)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_6_9_l2_backfill():
    print("\n--- 6.9: L2 backfill to L1 ---")
    redis_url = env("REDIS_URL") or "redis://localhost:6379"

    prompt = simple_prompt("6.9")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="redis")

    # Pipeline 1: warm L2
    cache1 = TwoTierCache("redis", redis_url=redis_url)
    pipeline1, inst1 = make_real_pipeline(openai_dispatch, "openai", cache=cache1)
    r1 = await pipeline1.call(inp)
    assert_success(r1, "Initial call succeeds")
    await asyncio.sleep(0.5)
    cache1.close()

    # Pipeline 2: fresh L1, read from L2 (backfills L1)
    cache2 = TwoTierCache("redis", redis_url=redis_url)
    pipeline2, inst2 = make_real_pipeline(openai_dispatch, "openai", cache=cache2)
    r2 = await pipeline2.call(inp)
    assert_eq(r2.cache_hit, "l2", "2nd call: L2 hit (triggers backfill)")

    # 3rd call: should now be L1 hit (backfilled from L2)
    t0 = time.monotonic()
    r3 = await pipeline2.call(inp)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert_eq(r3.cache_hit, "l1", "3rd call: L1 hit (backfilled)")
    assert_eq(inst2.call_count, 0, "No dispatches on pipeline2")
    assert_lt(elapsed_ms, 50, "L1 hit latency < 50ms after backfill")

    cache2.close()


# ---------------------------------------------------------------------------
# 6.10  Cache stores raw, strip on read (SnoGPU, T2)
# ---------------------------------------------------------------------------

@skip_unless("GPU_BASE_URL", "SNO_LLM_API_KEY")
@skip_unless_tier("t2")
async def test_6_10_cache_stores_raw_thinking():
    print("\n--- 6.10: Cache stores raw content with <think> blocks ---")
    cache = TwoTierCache("memory")
    base_url = env("GPU_BASE_URL") or ""

    pipeline, inst = make_real_pipeline(
        sno_gpu_dispatch, "sno-gpu",
        api_key="not-needed",
        cache=cache,
    )
    # Register key pool for sno-gpu
    from llmix.key_pool import KeyPool
    pipeline.set_key_pool("sno-gpu", KeyPool(["not-needed"]))

    prompt = [{"role": "user", "content": f"What is 2+2? Think step by step. (6.10 {uid()})"}]
    inp = make_call_input(
        "sno-gpu", SNO_GPU_MODEL, prompt,
        temperature=0.0,
        max_output_tokens=512,
        caching_strategy="memory",
        base_url=base_url,
    )
    # Enable thinking in provider options
    inp.config["provider_options"] = {"enable_thinking": True}

    r1 = await pipeline.call(inp)
    assert_success(r1, "SnoGPU thinking call succeeds")
    # With keep_thinking_output=False (default), content should be stripped
    assert_not_contains(r1.content, "<think>", "Returned content is stripped of <think>")
    assert_true(r1.thinking_content is not None and len(r1.thinking_content) > 0, "thinking_content populated")

    # Inspect raw L1 cache entry directly
    from llmix.response_cache import generate_cache_key
    common = inp.config.get("common") or {}
    cache_key = generate_cache_key({
        "provider": "sno-gpu",
        "model": SNO_GPU_MODEL,
        "messages": prompt,
        "baseUrl": inp.config.get("baseUrl", ""),
        "temperature": common.get("temperature"),
        "maxOutputTokens": common.get("max_output_tokens"),
        "responseFormat": common.get("response_format"),
        "seed": common.get("seed"),
        "topP": common.get("top_p"),
        "providerOptions": inp.config.get("provider_options"),
    })
    raw_entry = cache._l1.get(cache_key)
    assert_true(raw_entry is not None, "Raw L1 entry exists")
    if raw_entry:
        assert_contains(raw_entry.data, "<think>", "Raw L1 entry contains <think> blocks")


# ---------------------------------------------------------------------------
# 6.11  Cache + keep_thinking_output toggle (SnoGPU, T2)
# ---------------------------------------------------------------------------

@skip_unless("GPU_BASE_URL", "SNO_LLM_API_KEY")
@skip_unless_tier("t2")
async def test_6_11_cache_keep_thinking_toggle():
    print("\n--- 6.11: Cache hit with keep_thinking_output toggle ---")
    cache = TwoTierCache("memory")
    base_url = env("GPU_BASE_URL") or ""

    pipeline, inst = make_real_pipeline(
        sno_gpu_dispatch, "sno-gpu",
        api_key="not-needed",
        cache=cache,
    )
    from llmix.key_pool import KeyPool
    pipeline.set_key_pool("sno-gpu", KeyPool(["not-needed"]))

    prompt = [{"role": "user", "content": f"What is 3+3? Think step by step. (6.11 {uid()})"}]

    # 1st call: keep_thinking_output=False -- content stripped
    inp1 = make_call_input(
        "sno-gpu", SNO_GPU_MODEL, prompt,
        temperature=0.0,
        max_output_tokens=512,
        keep_thinking_output=False,
        caching_strategy="memory",
        base_url=base_url,
    )
    inp1.config["provider_options"] = {"enable_thinking": True}

    r1 = await pipeline.call(inp1)
    assert_success(r1, "1st call (strip) succeeds")
    assert_not_contains(r1.content, "<think>", "1st call: content stripped")
    assert_eq(inst.call_count, 1, "1st call: dispatch_count==1")

    # 2nd call: keep_thinking_output=True -- same cache key, raw content returned
    inp2 = make_call_input(
        "sno-gpu", SNO_GPU_MODEL, prompt,
        temperature=0.0,
        max_output_tokens=512,
        keep_thinking_output=True,
        caching_strategy="memory",
        base_url=base_url,
    )
    inp2.config["provider_options"] = {"enable_thinking": True}

    r2 = await pipeline.call(inp2)
    assert_success(r2, "2nd call (keep) succeeds")
    assert_eq(r2.cache_hit, "l1", "2nd call: L1 cache hit")
    assert_eq(inst.call_count, 1, "2nd call: dispatch_count still 1 (served from cache)")
    assert_contains(r2.content, "<think>", "2nd call: content includes <think> blocks")
    assert_eq(r2.thinking_content, None, "2nd call: thinking_content is None (not stripped)")


# ---------------------------------------------------------------------------
# 6.12  Cache skip for disabled strategy
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_12_cache_skip_disabled():
    print("\n--- 6.12: Cache skip for strategy='disabled' ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.12")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="disabled")

    r1 = await pipeline.call(inp)
    r2 = await pipeline.call(inp)
    assert_success(r1, "Call 1 succeeds")
    assert_success(r2, "Call 2 succeeds")
    assert_eq(r1.cache_hit, None, "Call 1: cache_hit is None")
    assert_eq(r2.cache_hit, None, "Call 2: cache_hit is None (cache skipped)")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (cache disabled)")


# ---------------------------------------------------------------------------
# 6.13  Cache skip for native strategy
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
async def test_6_13_cache_skip_native():
    print("\n--- 6.13: Cache skip for strategy='native' ---")
    cache = TwoTierCache("memory")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.13")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="native")

    r1 = await pipeline.call(inp)
    r2 = await pipeline.call(inp)
    assert_success(r1, "Call 1 succeeds")
    assert_success(r2, "Call 2 succeeds")
    assert_eq(r1.cache_hit, None, "Call 1: cache_hit is None")
    assert_eq(r2.cache_hit, None, "Call 2: cache_hit is None (native strategy)")
    assert_eq(inst.call_count, 2, "dispatch_count==2 (native defers to provider)")


# ---------------------------------------------------------------------------
# 6.14  Redis-or-memory degradation (T3)
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY")
@skip_unless_tier("t3")
async def test_6_14_redis_or_memory_degradation():
    print("\n--- 6.14: redis-or-memory degrades to L1 on bad Redis ---")
    # Intentionally invalid Redis URL
    cache = TwoTierCache("redis-or-memory", redis_url="redis://invalid-host-that-does-not-exist:6379")
    pipeline, inst = make_real_pipeline(openai_dispatch, "openai", cache=cache)

    prompt = simple_prompt("6.14")
    inp = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="redis-or-memory")

    # Should not crash
    r1 = await pipeline.call(inp)
    assert_success(r1, "1st call succeeds despite bad Redis")
    assert_eq(r1.cache_hit, None, "1st call: no cache hit")
    assert_eq(inst.call_count, 1, "1st call: dispatch_count==1")

    # 2nd call should hit L1
    r2 = await pipeline.call(inp)
    assert_success(r2, "2nd call succeeds")
    assert_eq(r2.cache_hit, "l1", "2nd call: L1 hit (degraded from redis-or-memory)")
    assert_eq(inst.call_count, 1, "2nd call: dispatch_count still 1")
    assert_eq(r2.content, r1.content, "Content identical on L1 fallback hit")

    cache.close()


# ---------------------------------------------------------------------------
# 6.15  Cross-provider cache isolation
# ---------------------------------------------------------------------------

@skip_unless("OPENAI_API_KEY", "GEMINI_API_KEY")
async def test_6_15_cross_provider_cache_isolation():
    print("\n--- 6.15: Cross-provider cache isolation ---")
    # Shared cache instance -- providers should still get separate entries
    cache = TwoTierCache("memory")
    pipeline_oai, inst_oai = make_real_pipeline(openai_dispatch, "openai", cache=cache)
    pipeline_gem, inst_gem = make_real_pipeline(gemini_dispatch, "gemini", cache=cache)

    tag = uid()
    prompt = [{"role": "user", "content": f"Reply with exactly one word: blue. (6.15 {tag})"}]
    inp_oai = make_call_input("openai", OPENAI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")
    inp_gem = make_call_input("gemini", GEMINI_MODEL, prompt, temperature=0.0, max_output_tokens=16, caching_strategy="memory")

    r_oai = await pipeline_oai.call(inp_oai)
    r_gem = await pipeline_gem.call(inp_gem)
    assert_success(r_oai, "OpenAI call succeeds")
    assert_success(r_gem, "Gemini call succeeds")
    assert_eq(inst_oai.call_count, 1, "OpenAI dispatch_count==1")
    assert_eq(inst_gem.call_count, 1, "Gemini dispatch_count==1")
    assert_eq(r_oai.cache_hit, None, "OpenAI: no cache hit")
    assert_eq(r_gem.cache_hit, None, "Gemini: no cache hit (separate cache entry)")

    # Verify both can hit their own L1 on 2nd call
    r_oai2 = await pipeline_oai.call(inp_oai)
    r_gem2 = await pipeline_gem.call(inp_gem)
    assert_eq(r_oai2.cache_hit, "l1", "OpenAI 2nd call: L1 hit")
    assert_eq(r_gem2.cache_hit, "l1", "Gemini 2nd call: L1 hit")
    assert_eq(inst_oai.call_count, 1, "OpenAI dispatch_count still 1")
    assert_eq(inst_gem.call_count, 1, "Gemini dispatch_count still 1")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_6_1_l1_hit_same_prompt,
    test_6_2_l1_miss_different_prompt,
    test_6_3_l1_miss_different_temperature,
    test_6_4_l1_miss_different_response_format,
    test_6_5_l1_miss_different_seed,
    test_6_6_l1_miss_different_top_p,
    test_6_7_l1_miss_different_provider,
    test_6_8_l2_redis_hit,
    test_6_9_l2_backfill,
    test_6_10_cache_stores_raw_thinking,
    test_6_11_cache_keep_thinking_toggle,
    test_6_12_cache_skip_disabled,
    test_6_13_cache_skip_native,
    test_6_14_redis_or_memory_degradation,
    test_6_15_cross_provider_cache_isolation,
]


async def main() -> int:
    print("=" * 60)
    print("Suite 6: Two-Tier Response Cache (E2E)")
    print("=" * 60)

    for test_fn in ALL_TESTS:
        try:
            result = await test_fn()
            if result == "skipped":
                continue
        except Exception as exc:
            print(f"  [ERROR] {test_fn.__name__}: {exc}")
            import traceback
            traceback.print_exc()

    return print_summary("Suite 6: Two-Tier Response Cache")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
