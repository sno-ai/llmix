# LLMix Rust Guide

Read this after the README if your application runtime is Rust. This page
covers Rust install, runtime calls, config shape, provider coverage, direct MDA
loading, and the shared official registry release flow.

Install the crate from crates.io:

```bash
cargo add llmix-rs
```

The crate name is `llmix-rs`. The Rust import path is `llmix_rs`.

For common async examples:

```toml
[dependencies]
llmix-rs = "2.0.0"
serde_json = "1"
tokio = { version = "1", features = ["macros", "rt"] }
```

Enable optional features when you need them:

```toml
[dependencies]
llmix-rs = { version = "2.0.0", features = ["redis", "providers-openai"] }
```

## Status

The Rust crate is usable today, but the Rust provider helpers should still be
treated as beta. The neutral pipeline, cache, key pool, config shape, and MDA
loaders are aligned with Python and TypeScript. The official registry publisher
and checker commands ship with the TypeScript npm package.

## Mental Model

LLMix has five pieces:

| Piece | What it does |
| --- | --- |
| `CallPipeline` | Runs one LLM call through cache, retries, key rotation, circuit breaker, singleflight, and dispatch. |
| `PipelineConfig` | Wires the dispatch function and runtime knobs. |
| `CallInput` | Carries the resolved model config and chat messages. |
| `KeyPool` | Rotates API keys per provider and marks dead keys on auth failures. |
| `TwoTierCache` | Uses in-process memory as L1 and optional Redis as L2. |

LLMix is not a replacement for OpenAI, Anthropic, LiteLLM, or your own provider
client. It wraps the call site where those SDKs are used.

## Config Shape

Rust uses snake_case fields in direct config objects:

```rust
serde_json::json!({
    "provider": "openai",
    "model": "gpt-4o-mini",
    "common": { "temperature": 0.2, "max_output_tokens": 512 },
    "caching": { "strategy": "memory", "ttl": 3600 },
    "provider_options": {
        "openai": { "reasoning_effort": "medium" }
    }
})
```

MDA source files use camelCase under `metadata.snoai-llmix`; the Rust loader
normalizes known fields into this snake_case runtime shape.

## Provider Coverage

| Provider family | Rust helper |
| --- | --- |
| OpenAI-compatible | `OpenAiChatHelper` with `providers-openai` |
| Anthropic | `AnthropicChatHelper` with `providers-anthropic` |
| Gemini | `GeminiChatHelper` with `providers-gemini` |
| OpenRouter | use OpenAI-compatible helper with OpenRouter base URL |
| DeepInfra | use OpenAI-compatible helper with DeepInfra base URL |
| Novita | use OpenAI-compatible helper with Novita base URL |
| Together | use OpenAI-compatible helper with Together base URL |
| SNO GPU | `SnoGpuChatHelper` with `providers-sno-gpu` |

Provider helpers are feature-gated. Enable only the provider helpers you call.

## Minimal Pipeline

```rust
use llmix_rs::{
    CallInput, CallPipeline, DispatchContext, KeyPool, LlmUsage, PipelineConfig,
    ProviderResult,
};
use serde_json::json;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let pipeline = CallPipeline::new(PipelineConfig::new(|ctx: DispatchContext| async move {
        let prompt = ctx
            .messages
            .last()
            .and_then(|message| message.get("content"))
            .and_then(|value| value.as_str())
            .unwrap_or("hello");

        Ok(ProviderResult {
            content: format!("echo: {prompt}"),
            model: ctx.model,
            usage: LlmUsage {
                input_tokens: 1,
                output_tokens: 2,
                total_tokens: 3,
            },
            headers: None,
            tool_calls: None,
        })
    }))?;

    pipeline.set_key_pool("openai", KeyPool::new(vec!["demo-key".to_owned()])?);

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4o-mini",
                "caching": { "strategy": "memory" }
            }),
            messages: vec![json!({
                "role": "user",
                "content": "hello"
            })],
            singleflight_key: None,
        })
        .await;

    println!("{}", response.content);
    pipeline.close().await;
    Ok(())
}
```

This is the core contract. Replace the inline closure with your own client call
or a feature-gated provider helper.

## OpenAI-Compatible Helper

Enable the feature:

```toml
llmix-rs = { version = "2.0.0", features = ["providers-openai"] }
```

Then wire the helper:

```rust
use llmix_rs::{
    load_keys_from_env, CallInput, CallPipeline, OpenAiChatHelper, PipelineConfig,
};
use serde_json::json;

let pipeline = CallPipeline::new(PipelineConfig::new(OpenAiChatHelper::new()))?;
pipeline.set_key_pool("openai", load_keys_from_env("openai")?);

let response = pipeline
    .call(CallInput {
        config: json!({
            "provider": "openai",
            "model": "gpt-4o-mini",
            "common": { "temperature": 0.2, "max_output_tokens": 256 },
            "caching": { "strategy": "memory" }
        }),
        messages: vec![json!({
            "role": "user",
            "content": "In one sentence, what is LLMix?"
        })],
        singleflight_key: None,
    })
    .await;
```

For OpenAI-compatible providers, set the provider base URL through config or
construct the helper with `with_base_url(...)`.

## Timeouts and Cancellation

Treat `timeout.totalTime` in MDA config as a runtime budget, not as automatic
transport cancellation. The Rust pipeline does not wrap dispatch calls in
`tokio::time::timeout`, and `DispatchContext` does not carry a cancellation token.

The provider helpers use `reqwest`. A default `reqwest::Client::new()` does not
set a request timeout, so services that need a hard provider deadline should
provide a client with transport timeout:

```rust
use llmix_rs::{CallPipeline, OpenAiChatHelper, PipelineConfig};
use std::time::Duration;

let client = reqwest::Client::builder()
    .timeout(Duration::from_secs(120))
    .build()?;

let helper = OpenAiChatHelper::new().with_client(client);
let pipeline = CallPipeline::new(PipelineConfig::new(helper))?;
```

For custom dispatch, put the timeout around the actual request future, and make
sure the timed-out request future is dropped or aborted before the retry path
starts:

```rust
let response = tokio::time::timeout(Duration::from_secs(120), async {
    client
        .post("https://api.example.com/v1/chat/completions")
        .json(&body)
        .send()
        .await
})
.await??;
```

Retry without cancelling the previous provider request can create duplicate
in-flight generations.

## Redis Cache

Enable Redis:

```toml
llmix-rs = { version = "2.0.0", features = ["redis"] }
```

Wire a shared cache into the pipeline:

```rust
use llmix_rs::{
    PipelineConfig, ResponseCacheStrategy, TwoTierCache, TwoTierCacheConfig,
};
use std::sync::Arc;

let cache = Arc::new(TwoTierCache::new(
    ResponseCacheStrategy::RedisOrMemory,
    TwoTierCacheConfig {
        redis_url: std::env::var("REDIS_URL").ok(),
        max_items: 2048,
        ttl_seconds: 3600,
    },
)?);

let mut config = PipelineConfig::new(my_dispatch);
config.response_cache = Some(cache);
```

Use `ResponseCacheStrategy::Redis` when Redis is required.

## Key Pools

```rust
use llmix_rs::{load_keys_from_env, KeyPool};

pipeline.set_key_pool("openai", load_keys_from_env("openai")?);

// Or explicitly:
pipeline.set_key_pool(
    "openai",
    KeyPool::new(vec!["sk-live-1".to_owned(), "sk-live-2".to_owned()])?,
);
```

`load_keys_from_env("openai")` checks `OPENAI_KEYS` first, then
`OPENAI_API_KEY`. `OPENAI_KEYS` is comma-separated.

## Environment Variables

Key pools can be loaded from environment variables. Provider names normalize
hyphens to underscores and uppercase.

| Provider | Multi-key variable | Single-key fallback |
| --- | --- | --- |
| OpenAI | `OPENAI_KEYS` | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_KEYS` | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_KEYS` | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_KEYS` | `OPENROUTER_API_KEY` |
| DeepInfra | `DEEPINFRA_KEYS` | `DEEPINFRA_API_KEY` |
| Novita | `NOVITA_KEYS` | `NOVITA_API_KEY` |
| Together | `TOGETHER_KEYS` | `TOGETHER_API_KEY` |
| SNO GPU | `SNO_GPU_KEYS` | `SNO_GPU_API_KEY` |

`*_KEYS` is comma-separated. If both variables exist, `*_KEYS` wins.

## Config Registry

Production apps should publish MDA presets into the official registry layout:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

`source/` is edited by people. `current.json` and `compiled/` are generated by
LLMix. Keep the trust anchor outside `config/llm`.

The official publisher and checker commands ship with the TypeScript npm
package. A Rust app repo can keep Rust as the service runtime and install
`@snoai/llmix` only in release tooling:

```bash
npm install --save-dev @snoai/llmix
```

The fixed release flow is:

1. Put presets in `config/llm/source/<module>/<preset>.mda`.
2. Run MDA CLI validation, integrity, signing, verification, and release
   prepare.
3. Run `llmix publish-registry`.
4. Generate `config/llm/current.json` and `config/llm/compiled/`.
5. Run MDA CLI release finalize and doctor checks.
6. Store the trust anchor outside `config/llm`.
7. Prove the registry with `llmix check-registry` before deploying the release.

Use one normal example everywhere:

```text
config/llm/source/search_summary/openai_fast.mda
```

Create and gate the source preset with MDA CLI:

```bash
mkdir -p config/llm/source/search_summary release deploy

mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out config/llm/source/search_summary/openai_fast.mda

mda validate config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --json

mda integrity compute config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --write \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/trust-policy.json \
  --json

mda sign config/llm/source/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json

mda verify config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json
```

Publish, finalize, doctor, and prove the registry:

```bash
llmix publish-registry \
  --root config/llm \
  --release-plan release/plan.json \
  --revision 2026-05-14T000000Z \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --root-did did:web:config.example.com \
  --root-key-id did:web:config.example.com#release \
  --root-key-file release/did-web-private-key.pem \
  --json

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/compiled/2026-05-14T000000Z/registry-root.json \
  --release-plan release/plan.json \
  --policy release/trust-policy.json \
  --derive-root-digest \
  --minimum-revision 2026-05-14T000000Z \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

llmix check-registry \
  --root config/llm \
  --trust deploy/llmix-trust.json \
  --preset search_summary/openai_fast \
  --did-document release/did.json \
  --tamper-proof \
  --json
```

Do not build a Rust-local compiler, publisher, or custom directory layout. MDA
CLI gates the release, LLMix publishes and checks the registry, and the app
keeps the trust anchor outside `config/llm`.

## Direct MDA Loading

```rust
use llmix_rs::{load_config, load_config_preset};

let config = load_config("./config/llm/source/search_summary/openai_fast.mda")?;
let preset = load_config_preset("openai_fast", "./config/llm/source/search_summary")?;
```

Enable MDA safety checks when you load or publish `.mda` files:

```rust
use llmix_rs::{load_config_with_options, MdaConfigLoadOptions};

let config = load_config_with_options(
    "./config/llm/source/search_summary/openai_fast.mda",
    &MdaConfigLoadOptions {
        verify_integrity: true,
        enforce_requires: true,
        allowed_networks: vec!["api.openai.com".to_owned()],
        ..Default::default()
    },
)?;
```

For signed presets, also set `verify_signatures: true` and pass a
`TrustPolicy`, `RekorClient`, and `SigstoreVerifier`. LLMix fails the load or
publish step if the file is invalid, unsigned when signatures are required, or
missing verifier pieces.

## MDA Source Presets

MDA is the source format for presets. Use it when model choice, provider
options, cache policy, timeout policy, tags, and rollout metadata should be
reviewed as source files instead of hidden in application code.

The LLMix-specific data lives under `metadata.snoai-llmix`. MDA-owned mechanism
fields such as `requires`, `integrity`, and `signatures` stay at the top level
and are handled by the MDA parser.

```md
---
name: openai_fast
title: OpenAI Fast Search Summary
description: Fast OpenAI preset for search summaries.
tags:
  - search
  - production
requires:
  network: public
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 512
      maxRetries: 2
    providerOptions:
      openai:
        reasoningEffort: medium
        textVerbosity: low
    timeout:
      totalTime: 45
      streamFirstChunkTime: 12
    caching:
      strategy: redis-or-memory
      ttl: 3600
      maxItems: 2000
    tags:
      - search
      - production
---

Summarize search results for a research workflow.
```

`metadata.snoai-llmix` is strict. Unknown keys are rejected unless a field is
documented as a provider-specific pass-through record.

| Key | Required | Purpose |
| --- | --- | --- |
| `common` | yes | Provider, model, and portable generation parameters. |
| `providerOptions` | no | Provider-specific options such as OpenAI reasoning effort or Anthropic thinking. |
| `timeout` | no | Per-call timeout hints in seconds. |
| `description` | no | Overrides the top-level MDA description in the projected runtime config. |
| `deprecated` | no | Marks a preset as deprecated for tooling. |
| `tags` | no | Overrides top-level MDA tags in the projected runtime config. |
| `caching` | no | Response cache strategy and TTL. |
| `bypassGateway` | no | Compatibility flag for deployments that route around a gateway. |

`common.provider` and `common.model` are required. Supported providers are
`openai`, `anthropic`, `google`, `deepseek`, `openrouter`, `deepinfra`,
`novita`, `together`, and `sno-gpu`.

The reserved semantic payload type for signed LLMix presets is:

```text
application/vnd.snoai-llmix.preset+json
```

## Public Runtime Knobs

```rust
use std::time::Duration;

let mut config = PipelineConfig::new(my_dispatch);
config.max_retries = 3;
config.retry_base_ms = 1000;
config.retry_max_delay_ms = 30_000;
config.circuit_breaker_threshold = 3;
config.circuit_breaker_cooldown = Duration::from_secs(30);
config.semaphore_initial = 32;
config.semaphore_min = 4;
```

Most services should start with defaults. Tune only after real traffic shows a
specific pressure point.

## Boundaries

Good fits for LLMix:

- shared model presets
- cache policy
- retry and concurrency defaults
- API key rotation
- provider kwargs normalization

Keep these in product code:

- user authorization
- billing policy
- product-specific prompt branching
- provider account ownership rules
