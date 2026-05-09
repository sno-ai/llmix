# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
LLMix uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed

- Retired the remaining legacy public client/config-loader surface from both
  runtimes.
- Standardized the public API on `CallPipeline`, `PipelineConfig`, `CallInput`,
  `CallResponse`, and direct MDA config loading helpers.
- Added built-in dispatcher exports for the common providers in Python and
  TypeScript.
- Standardized response-cache key prefixes on `llmix:resp:`.
- Standardized default runtime state directories on `llmix`.
- Operators upgrading the tool-call cache fix should clear Redis
  `llmix:resp:*` entries once; otherwise pre-fix text-only cache rows can
  persist until the default 1 hour L2 TTL expires.

## [2.0.0] — 2026-01-29

Complete rewrite of the LLM orchestration layer around a full pipeline with
structured resilience, caching, key management, and shared config loading.

### Added — Python & TypeScript

- **19-step call pipeline** (`CallPipeline`) handling kill switch, cache
  lookup, circuit breaker, singleflight dedup, AIMD concurrency, retry loop,
  thinking-token stripping, cache write, and telemetry.
- **Two-tier response cache** (`TwoTierCache`) — L1 in-memory (LRU + TTL) and
  L2 Redis with automatic fallback. Cross-language SHA-256 cache keys
  (camelCase, sorted, `llmix:resp:` prefix).
- **Circuit breaker** — per `(provider, base_url)`, Resilience4j-style
  multi-probe HALF_OPEN recovery. Auth errors (401/403) do not trip the
  breaker.
- **Adaptive semaphore** (AIMD) — increases window on success, decreases on
  rate limit, uses `X-RateLimit-*` headers when available.
- **Key pool** (`KeyPool`) — round-robin rotation, dead-key marking, revive
  after TTL.
- **Singleflight** — deduplicates in-flight requests with identical cache keys.
- **Kill switch** — filesystem-based emergency stop without redeployment.
- **Thinking-token stripping** — extracts `<think>` blocks; respects
  keep-thinking flags. Cache stores raw content pre-strip.
- **Provider kwargs transforms** — per-provider parameter transforms
  (OpenAI reasoning models, Gemini 2.5 thinking budget, Sno GPU path
  construction).

### Added — TypeScript

- AI SDK v6 provider integration (OpenAI, Anthropic, Google).
- `CallPipeline` is a structural mirror of the Python implementation with
  identical cache key generation.

### Added — Shared

- `packages/llmix/typescript/data/pricing.json` — per-model USD/1M token pricing.
- `packages/llmix/typescript/data/model-capabilities.json` — model capability flags.
- `packages/llmix/typescript/data/config-schema.json` — config validation schema.

### Changed

- Pipeline dispatch is a caller-supplied callback (`PipelineConfig.dispatch`).
  The pipeline never imports provider SDKs directly.
- `CallResponse` and `ProviderResult` include tool-call fields for function
  calling support.

---

## Prior Releases

Legacy config-driven client generation. See git history.
