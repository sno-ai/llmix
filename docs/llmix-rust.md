# LLMix Rust Guide

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
treated as beta. The neutral pipeline, cache, key pool, and Config Registry
contract are aligned with Python and TypeScript.

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

## Config Registry

```rust
use llmix_rs::{ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir};

let root = resolve_config_dir(None)?.config_dir;
ConfigRegistryPublisher::new(&root)?.publish()?;

let mut manager = ConfigRegistryManager::open(&root)?;
let config = manager.get_preset("search", "summary")?;
println!("{:?}", manager.available_presets());
```

Use the resolved `config` as the `CallInput.config` value.

## Direct MDA Loading

```rust
use llmix_rs::{load_config, load_config_preset};

let config = load_config("./config/llm/search/summary.mda")?;
let preset = load_config_preset("summary", "./config/llm/search")?;
```

For production runtime code, prefer `ConfigRegistryManager`.

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
