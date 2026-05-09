# LLMix Benchmarks

Performance benchmarks for LLMix. These require API keys and running services.

## Benchmark Scripts

| Script | What it measures | Target |
|--------|-----------------|--------|
| `bench_throughput.py` | Parallel call() at c=64, tokens/sec | Meets throughput target |
| `bench_latency.py` | call() overhead vs raw SDK | < 5ms |
| `bench_cache.py` | L1 and L2 cache hit latency | L1 < 0.1ms, L2 < 2ms |
| `bench_key_rotation.py` | Key rotation under load | Zero 429s with N keys |
| `bench_memory.py` | L1 cache memory at 1000 entries | < 50MB |
| `bench_batch_cost.py` | Batch vs realtime cost for 100 requests | >= 40% cheaper |
| `bench_import_time.py` | Import time without provider SDKs | Python < 100ms, TS < 50ms |

## Running

```bash
# Requires OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_cache.py
uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_memory.py
uv run --project packages/llmix/python python packages/llmix/benchmarks/bench_import_time.py  # No API key needed
```

## Results

See `docs/llmix/` after running all benchmarks.
