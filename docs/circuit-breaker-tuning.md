# Circuit Breaker & AIMD Tuning Guide

LLMix includes a circuit breaker and an AIMD (Additive Increase /
Multiplicative Decrease) adaptive semaphore. This guide covers how to
tune them for your workload.

## Circuit Breaker States

| State | Behavior |
|-------|----------|
| **CLOSED** | All requests flow through. Failures are counted. |
| **OPEN** | Requests are rejected immediately. A cooldown timer runs. |
| **HALF_OPEN** | One probe request is allowed. Success → CLOSED, failure → OPEN. |

### Key Parameters

- **failure_threshold** — consecutive failures before tripping (default: 5)
- **cooldown_seconds** — how long the breaker stays OPEN (default: 30)
- **timeout_seconds** — per-request timeout; exceeded = counted as failure

### Tuning for Reasoning Models

Models like `o3` or Claude with extended thinking can take 30–60s per
response. Set `timeout_seconds` high enough that healthy slow responses
don't trip the breaker:

```python
pipeline = CallPipeline(
    PipelineConfig(
        dispatch=openai_dispatch(),
        circuit_breaker_timeout=90,   # generous for reasoning models
        circuit_breaker_threshold=3,  # trip faster on real failures
    )
)
```

## AIMD Adaptive Semaphore

The semaphore controls how many concurrent requests are in flight per
provider. It adjusts dynamically:

- **Additive Increase** — on each success, concurrency limit += 1
- **Multiplicative Decrease** — on each 429 or timeout, limit *= 0.5

### Quick Reference

| Workload | Recommended Starting Concurrency | Notes |
|----------|----------------------------------|-------|
| Interactive (chat) | 10–20 | Low latency priority |
| Batch (embeddings) | 50–100 | Throughput priority, 429s expected |
| Reasoning (o3, thinking) | 3–5 | High latency, low parallelism |

The semaphore converges to the provider's effective limit within
~20 requests. If you see sustained 429s after convergence, your
key pool may be undersized for the workload.
