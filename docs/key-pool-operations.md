# Key Pool Operations Guide

LLMix's `KeyPool` manages API keys for each provider, rotating on rate
limits and auto-evicting keys that are permanently invalid.

## Basic Setup

```python
from llmix import KeyPool

pool = KeyPool([
    "sk-org-a-key-1",
    "sk-org-a-key-2",
    "sk-org-b-key-1",
])
pipeline.set_key_pool("openai", pool)
```

## Rotation Behavior

When a request returns HTTP 429 (rate limited), the pipeline:

1. Marks the current key as temporarily exhausted
2. Rotates to the next key in the pool
3. Retries the request (honoring `Retry-After` if present)

Keys rotate in round-robin order. A key marked exhausted becomes
eligible again after its cooldown period expires.

## Auto-Eviction

Keys that return HTTP 401 (invalid credentials) or 403 (revoked) are
permanently evicted from the pool. The pipeline logs a warning and
continues with remaining keys.

If all keys are evicted, subsequent calls raise `ProviderError` with
a descriptive message.

## Pool Sizing

A rough heuristic for OpenAI:

| Tier | RPM Limit | Keys Needed for 100 RPM |
|------|-----------|------------------------|
| Free | 3 RPM | 34 |
| Tier 1 | 500 RPM | 1 |
| Tier 3 | 5000 RPM | 1 |

For multi-org setups, pool keys from different organizations to
get independent rate-limit buckets.

## Monitoring

Check `pipeline.key_pool_stats("openai")` for current pool health:

```python
stats = pipeline.key_pool_stats("openai")
print(f"active={stats.active} exhausted={stats.exhausted} evicted={stats.evicted}")
```
