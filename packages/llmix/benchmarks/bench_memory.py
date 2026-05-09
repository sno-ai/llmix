#!/usr/bin/env python3
"""Benchmark: L1 cache memory usage at 1000 entries.

Target: < 50MB

Run: uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_memory.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from llmix.response_cache import TwoTierCache, generate_cache_key


def bench_memory(entries: int = 1000) -> None:
    """Fill cache to max and measure memory."""
    import resource

    cache = TwoTierCache(strategy="memory", max_items=entries)

    # Generate realistic-sized cache entries (~2KB each)
    for i in range(entries):
        key = generate_cache_key({
            "provider": "openai", "model": "gpt-5-mini",
            "messages": [{"role": "user", "content": f"message {i}" * 50}],
        })
        value = '{"content": "' + ("x" * 1500) + '", "usage": {"inputTokens": 100, "outputTokens": 50}}'
        cache.set(key, value)

    mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mem_mb = mem_kb / 1024
    status = "PASS" if mem_mb < 50 else "FAIL"
    print(f"Memory with {entries} entries: {mem_mb:.1f} MB [{status}] (target < 50MB)")


if __name__ == "__main__":
    bench_memory()
