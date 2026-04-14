# llmix-rs

`llmix-rs` is the Rust binding for the current LLMix v2 orchestration contract.

The crate is usable today, but it should still be treated as beta. The public surface is aligned to the current Python and TypeScript bindings, while downstream production adoption is still earlier-stage in Rust.

## What the crate covers

- Async call orchestration through a user-supplied dispatch callback
- Two-tier response cache with shared cache-key fixture parity
- Circuit breaker, kill switch, retry, singleflight, key-pool rotation, and AIMD semaphore
- Provider kwargs normalization for OpenAI, Anthropic, Gemini, OpenRouter, and `sno-gpu`
- Thinking-token stripping
- YAML config loading with `load_config` and `load_config_preset`
- Optional helper modules for OpenAI-compatible, Anthropic, Gemini, and `sno-gpu` HTTP dispatch

## Feature flags

- Default features: core crate only
- `redis`: enable Redis-backed L2 response cache
- `helpers-openai`: enable the OpenAI chat helper
- `helpers-sno-gpu`: enable the `sno-gpu` helper
- `helpers-anthropic`: enable the Anthropic chat helper
- `helpers-gemini`: enable the Gemini chat helper

## Minimal example

```rust
use llmix_rs::{
    CallInput, CallPipeline, DispatchContext, KeyPool, LlmUsage, PipelineConfig, ProviderResult,
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
                "model": "gpt-4o-mini"
            }),
            messages: vec![json!({
                "role": "user",
                "content": "hello"
            })],
            singleflight_key: None,
        })
        .await;

    assert!(response.success);
    println!("{}", response.content);

    pipeline.close().await;
    Ok(())
}
```

The same callback shape works when you replace the inline closure with your own async function or an adapter around a provider-specific client.

## Optional helper modules

The core crate does not require provider SDKs. If you want a batteries-included HTTP adapter, enable one of the helper features and wire the helper into `PipelineConfig::new(...)`.

Helpers are deliberately optional because the LLMix contract is the orchestration layer, not a mandatory SDK abstraction.

## Shared parity tests

The Rust crate consumes the same fixtures used by the rest of the repo under `tests/fixtures/`.

- `cargo test --all-features` runs the local parity and unit suites
- live provider tests are opt-in and only run when `LLMIX_RUN_LIVE_TESTS=1`
- helper-specific live tests also require the matching provider credentials in the environment

## Monorepo location

The crate lives at `rust/llmix-rs/` in the main LLMix monorepo alongside the Python and TypeScript bindings.
