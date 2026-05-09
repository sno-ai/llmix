# llmix-rs

`llmix-rs` is the Rust binding for the current LLMix orchestration contract.

The crate is usable today, but it should still be treated as beta. The public surface is aligned to the current Python and TypeScript bindings, while downstream production adoption is still earlier-stage in Rust.

## What the crate covers

- Async call orchestration through a user-supplied dispatch callback
- Two-tier response cache with shared cache-key fixture parity
- Circuit breaker, kill switch, retry, singleflight, key-pool rotation, and AIMD semaphore
- Provider kwargs normalization for OpenAI, Anthropic, Gemini, OpenRouter, and `sno-gpu`
- Thinking-token stripping
- Config Registry runtime manager and publisher for committed preset snapshots
- Low-level MDA Source Mode config helpers with `load_config` and `load_config_preset`
- Optional provider modules for OpenAI-compatible, Anthropic, Gemini, and `sno-gpu` HTTP dispatch

## Feature flags

- Default features: core crate only
- `redis`: enable Redis-backed L2 response cache
- `providers-openai`: enable the OpenAI chat provider adapter
- `providers-sno-gpu`: enable the `sno-gpu` provider adapter
- `providers-anthropic`: enable the Anthropic chat provider adapter
- `providers-gemini`: enable the Gemini chat provider adapter
- `helpers-*`: legacy aliases that forward to the matching `providers-*` feature

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

## Config Registry

The Rust crate now exposes `ConfigRegistryPublisher` and
`ConfigRegistryManager` as the preferred preset runtime path.

```rust,no_run
use llmix_rs::{ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir};

# fn main() -> Result<(), Box<dyn std::error::Error>> {
let root = resolve_config_dir(None)?.config_dir;
ConfigRegistryPublisher::new(&root)?.publish()?;

let mut manager = ConfigRegistryManager::open(&root)?;
let config = manager.get_preset("search", "summary")?;
# let _ = config;
# Ok(())
# }
```

The registry contract stays aligned across Python, TypeScript, and Rust:

- `.mda` Source Mode stays as the authoring format
- publishing creates immutable snapshot revisions
- `current.json` is the only live switch
- runtime reads committed snapshot artifacts instead of mutable authoring MDA

That lets Rust stay aligned with Python and TypeScript without putting
cross-language MDA parsing on the runtime hot path.

The manager also exposes the active revision and reload health metadata so
service code can report which snapshot is live and whether a reload failed.

`load_config` and `load_config_preset` remain available as low-level helpers
for authoring tools, tests, and migration work. They now hard-require `.mda`
files and reject legacy `.yaml` / `.yml` preset paths.

## Optional provider modules

The core crate does not require provider SDKs. If you want a batteries-included HTTP adapter, enable one of the `providers-*` features and wire the corresponding helper into `PipelineConfig::new(...)`.

These provider adapters are deliberately optional because the LLMix contract is the orchestration layer, not a mandatory SDK abstraction.

## Shared parity tests

The Rust crate consumes the same fixtures used by the rest of the repo under `tests/fixtures/`.

- `cargo test --all-features` runs the local parity and unit suites
- live provider tests are opt-in and only run when `LLMIX_RUN_LIVE_TESTS=1`
- provider-specific live tests also require the matching provider credentials in the environment

## Monorepo location

The crate lives at `rust/llmix-rs/` in the main LLMix monorepo alongside the Python and TypeScript bindings.
