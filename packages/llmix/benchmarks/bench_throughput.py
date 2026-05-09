#!/usr/bin/env python3
"""Benchmark: Throughput test — parallel call() at c=64.

Target: Meet throughput target

Requires: OPENAI_API_KEY
Run: uv run python tests/benchmarks/bench_throughput.py
"""

print("TODO: Throughput benchmark requires API keys and real provider calls.")
print("This benchmark should measure tokens/sec with c=64 concurrent calls,")
print("comparing HTTP/2 vs HTTP/1.1 and pipeline overhead vs direct provider calls.")
