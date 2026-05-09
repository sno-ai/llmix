#!/usr/bin/env python3
"""Benchmark: L1 and L2 cache hit latency.

Targets: L1 < 0.1ms, L2 < 2ms

Run: uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_cache.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from llmix.response_cache import TwoTierCache, generate_cache_key


def bench_l1(iterations: int = 10000) -> float:
    """Benchmark L1 cache hit latency."""
    cache = TwoTierCache(strategy="memory")

    key = generate_cache_key({
        "provider": "openai", "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "hello"}],
    })
    cache.set(key, '{"content": "response", "usage": {}}')

    start = time.perf_counter()
    for _ in range(iterations):
        cache.get(key)
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / iterations) * 1000
    return avg_ms


def main() -> None:
    l1_ms = bench_l1()
    status = "PASS" if l1_ms < 0.1 else "FAIL"
    print(f"L1 cache hit: {l1_ms:.4f} ms avg [{status}] (target < 0.1ms)")
    print()
    print("L2 (Redis) benchmark requires running Redis — skipped.")
    print("To run: set REDIS_URL and modify this script.")


if __name__ == "__main__":
    main()
