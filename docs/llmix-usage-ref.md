# LLMix Usage Reference

LLMix is a config-driven harness around the LLM SDKs you already use.

You still own the provider client and the final dispatch call. LLMix adds the
parts that become annoying once an AI product is real: model presets, retries,
two-tier response cache, key-pool rotation, circuit breakers, singleflight, and
the same runtime contract across Python, TypeScript, and Rust.

This page is the map. Start with the language guide for the runtime you ship:

- [TypeScript guide](llmix-typescript.md)
- [Python guide](llmix-python.md)
- [Rust guide](llmix-rust.md)
- [Key pool operations](key-pool-operations.md)
- [Secure LLMix configuration](secure-llmix-configuration.md)

## Packages

| Runtime | Install | Import path | Runtime floor |
| --- | --- | --- | --- |
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` | Node.js 20+ |
| Python | `pip install sno-llmix` | `llmix` | Python 3.14+ |
| Rust | `cargo add llmix-rs` | `llmix_rs` | Rust 1.83+ |

The Python package is named `sno-llmix` on PyPI because `llmix` was already
taken. The import module is still `llmix`.

## Mental Model

LLMix has five pieces:

| Piece | What it does |
| --- | --- |
| `CallPipeline` | Runs one LLM call through cache, retries, key rotation, circuit breaker, singleflight, and dispatch. |
| `PipelineConfig` | Wires the dispatch function and runtime knobs. |
| `CallInput` | Carries the resolved model config and chat messages. |
| `KeyPool` | Rotates API keys per provider and marks dead keys on auth failures. |
| `TwoTierCache` | Uses in-process memory as L1 and optional Redis as L2. |

There is one important boundary: LLMix is not trying to replace OpenAI,
Anthropic, AI SDK, LiteLLM, or your own provider client. It wraps the call site
where those SDKs are used.

## Config Shape

The runtime config is intentionally small:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "common": {
    "temperature": 0.2,
    "max_output_tokens": 512
  },
  "caching": {
    "strategy": "memory",
    "ttl": 3600
  },
  "provider_options": {
    "openai": {
      "reasoning_effort": "medium"
    }
  }
}
```

Python and Rust use snake_case fields in direct config objects. TypeScript uses
camelCase in code:

```typescript
{
  provider: "openai",
  model: "gpt-4o-mini",
  common: { temperature: 0.2, maxOutputTokens: 512 },
  caching: { strategy: "memory", ttl: 3600 },
  providerOptions: {
    openai: { reasoningEffort: "medium" },
  },
}
```

## Provider Coverage

Built-in dispatch helpers are included where the runtime has the matching
provider support:

| Provider family | Python | TypeScript | Rust |
| --- | --- | --- | --- |
| OpenAI-compatible | `openai_dispatch()` | `openaiDispatch()` | `OpenAiChatHelper` with `providers-openai` |
| Anthropic | `anthropic_dispatch()` | `anthropicDispatch()` | `AnthropicChatHelper` with `providers-anthropic` |
| Gemini | `gemini_dispatch()` | `geminiDispatch()` | `GeminiChatHelper` with `providers-gemini` |
| OpenRouter | `openrouter_dispatch()` | `openrouterDispatch()` | use OpenAI-compatible helper with OpenRouter base URL |
| DeepInfra | `deepinfra_dispatch()` | `deepinfraDispatch()` | use OpenAI-compatible helper with DeepInfra base URL |
| Novita | `novita_dispatch()` | `novitaDispatch()` | use OpenAI-compatible helper with Novita base URL |
| Together | `together_dispatch()` | `togetherDispatch()` | use OpenAI-compatible helper with Together base URL |
| SNO GPU | `sno_gpu_dispatch()` | `snoGpuDispatch()` | `SnoGpuChatHelper` with `providers-sno-gpu` |

The Rust crate is usable today, but the Rust provider helpers are still
earlier-stage than the Python and TypeScript bindings. The neutral pipeline,
cache, key pool, and registry contract are the stable center.

## Config Registry

For production services, prefer the Config Registry over reading mutable MDA
files at request time.

The registry layout is:

```text
config/llm/
  authoring/
    search/
      summary.mda
  snapshots/
    2026-05-09T120000Z/
      search/
        summary.json
  current.json
```

Publishing turns MDA Source Mode presets into immutable JSON snapshots.
Runtime code reads `current.json` and the selected snapshot. That makes model
rollouts a data switch, not a redeploy.

Python:

```python
from llmix import ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir

root = resolve_config_dir().config_dir
ConfigRegistryPublisher(root).publish()

manager = ConfigRegistryManager.open(root)
config = manager.get_preset("search", "summary")
print(manager.active_revision)
```

TypeScript:

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  resolveConfigDir,
} from "@snoai/llmix";

const { configDir } = resolveConfigDir();
await new ConfigRegistryPublisher(configDir).publish();

const manager = await ConfigRegistryManager.open(configDir);
const config = await manager.getPreset("search", "summary");
console.log(manager.activeRevision);
```

Rust:

```rust
use llmix_rs::{ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir};

let root = resolve_config_dir(None)?.config_dir;
ConfigRegistryPublisher::new(&root)?.publish()?;

let mut manager = ConfigRegistryManager::open(&root)?;
let config = manager.get_preset("search", "summary")?;
println!("{:?}", manager.active_revision());
```

## MDA Source Mode

MDA is the authoring format for presets. Use it when you want model choice,
provider options, cache policy, timeout policy, tags, and rollout metadata to be
reviewed as source files instead of hidden in application code.

The LLMix-specific data lives under `metadata.snoai-llmix`. MDA-owned mechanism
fields such as `requires`, `integrity`, and `signatures` stay at the top level
and are handled by the MDA parser.

```md
---
name: search-summary
title: Search Summary
description: Summarize search results for a research workflow.
tags:
  - search
  - production
requires:
  network: public
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4o-mini
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

Use camelCase in `.mda` files. TypeScript keeps that shape. Python and Rust
normalize known fields into their snake_case runtime config shape after loading.

## Secure Configuration Namespace

`metadata.snoai-llmix` is strict. Unknown keys are rejected unless a field is
documented as a provider-specific pass-through record. That is intentional:
presets should fail during publishing, not during a production request.

The namespace shape is:

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

`common.provider` and `common.model` are required. Put these fields inside
`common`, not directly under `metadata.snoai-llmix`.

```yaml
metadata:
  snoai-llmix:
    common:
      provider: anthropic
      model: claude-sonnet-4-5
      temperature: 0.4
      maxOutputTokens: 2048
```

Supported providers are:

- `openai`
- `anthropic`
- `google`
- `deepseek`
- `openrouter`
- `deepinfra`
- `novita`
- `together`
- `sno-gpu`

Portable `common` fields include:

| Field | Notes |
| --- | --- |
| `temperature` | Number from `0` to `2`. |
| `maxOutputTokens` | Positive integer output-token cap. |
| `topP`, `topK` | Sampling controls. |
| `presencePenalty`, `frequencyPenalty` | Penalty controls where providers support them. |
| `stopSequences` | Array of stop strings. |
| `seed` | Integer seed where providers support deterministic sampling. |
| `maxRetries` | Non-negative retry count for this preset. |
| `enableThinking`, `keepThinkingOutput` | Thinking/reasoning controls used by supported providers. |

Provider-specific options stay under `providerOptions.<provider>`. For example:

```yaml
metadata:
  snoai-llmix:
    common:
      provider: anthropic
      model: claude-sonnet-4-5
    providerOptions:
      anthropic:
        thinking:
          type: enabled
          budgetTokens: 2048
        sendReasoning: true
```

Common provider option namespaces:

| Namespace | Examples |
| --- | --- |
| `openai` | `reasoningEffort`, `textVerbosity`, `parallelToolCalls`, `structuredOutputs`, `serviceTier`, `promptCacheKey`. |
| `anthropic` | `thinking`, `cacheControl`, `disableParallelToolUse`, `sendReasoning`, `structuredOutputMode`. |
| `google` | `thinkingConfig`, `cachedContent`, `safetySettings`, `responseModalities`. |
| `deepseek` | `thinking`. |
| `openrouter` | `provider`, `reasoning` pass-through records. |
| `sno-gpu` | `enableThinking`, `thinkingBudget`, `gpuPath`. |
| `deepinfra`, `novita`, `together` | Provider pass-through records, with `enableThinking` and `thinkingBudget` recognized for DeepInfra and Novita. |

Caching is opt-in:

```yaml
caching:
  strategy: redis-or-memory
  ttl: 3600
  maxItems: 2000
```

Valid strategies are `native`, `gateway`, `disabled`, `redis`,
`redis-or-memory`, and `memory`.

When a preset is loaded, LLMix projects it into the normal runtime config:

| MDA field | Runtime config result |
| --- | --- |
| `metadata.snoai-llmix.common.provider` | top-level `provider` |
| `metadata.snoai-llmix.common.model` | top-level `model` |
| other `common` fields | `common` object |
| `providerOptions` | `providerOptions` in TypeScript, `provider_options` in Python/Rust |
| `timeout.totalTime` | `timeout.totalTime` in TypeScript, `timeout.total_time` in Python/Rust |
| `caching.maxItems` | `caching.maxItems` in TypeScript, `caching.max_items` in Python/Rust |

The reserved semantic payload type for signed LLMix presets is:

```text
application/vnd.snoai-llmix.preset+json
```

LLMix does not invent a separate signature envelope. Sigstore signature
verification, integrity fields, and trust policy are delegated to the MDA
mechanism layer.

Direct MDA loading helpers still exist for authoring tools and tests:

| Runtime | Helper |
| --- | --- |
| Python | `load_mda_config(path)`, `load_mda_config_preset(name, base_dir)` |
| TypeScript | `loadMdaConfig(path)`, `loadMdaConfigPreset(name, baseDir)` |
| Rust | `load_config(path)`, `load_config_preset(name, base_dir)` |

For server runtime code, use `ConfigRegistryManager`.

## Runtime Features

| Feature | Default | Notes |
| --- | --- | --- |
| Retries | `maxRetries` / `max_retries` = `3` | Retries provider errors that are safe to retry and honors `Retry-After` when present. |
| Circuit breaker | threshold `3`, cooldown `30s` | Trips per provider and base URL. Auth failures are handled by the key pool instead. |
| Adaptive concurrency | initial `32`, min `4` | AIMD window adjusts after successes and rate-limit pressure. |
| Singleflight | enabled | Deduplicates concurrent requests that share the same singleflight key. |
| Two-tier cache | opt-in | Use `memory`, `redis`, or `redis-or-memory`. |
| Key pool | opt-in | Register per provider with `set_key_pool` / `setKeyPool` / `set_key_pool`. |
| Thinking strip | config-driven | Can remove reasoning/thinking text from returned content while preserving cache identity. |

These knobs are part of the pipeline config. They are public, but most users
should leave the defaults alone until traffic gives them a reason.

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

## What Not To Put In LLMix

Keep provider-specific policy close to your product code when it is product
logic. LLMix is a runtime harness, not a place to hide business rules.

Good fits:

- shared model presets
- cache policy
- retry and concurrency defaults
- API key rotation
- provider kwargs normalization

Bad fits:

- user authorization
- billing policy
- product-specific prompt branching
- provider account ownership rules
