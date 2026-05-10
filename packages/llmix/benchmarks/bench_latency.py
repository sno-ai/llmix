#!/usr/bin/env python3
"""Benchmark: Latency overhead — call() e2e vs raw SDK.

Target: < 5ms overhead

Requires: OPENAI_API_KEY
Run: uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_latency.py
"""

print("TODO: Latency benchmark requires API keys and real provider calls.")
print("This benchmark should measure overhead of the current pipeline layers")
print("(kill switch, cache, circuit breaker, semaphore, etc.) vs raw SDK call.")
