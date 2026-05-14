# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](LICENSE)

Read in other languages: **English** · [中文](docs/llmix/readme/README.zh-CN.md) · [Deutsch](docs/llmix/readme/README.de.md) · [Español](docs/llmix/readme/README.es.md) · [Français](docs/llmix/readme/README.fr.md) · [Русский](docs/llmix/readme/README.ru.md) · [한국어](docs/llmix/readme/README.ko.md) · [日本語](docs/llmix/readme/README.ja.md) · [हिन्दी](docs/llmix/readme/README.hi.md)

> Config-driven LLM calls for Python, TypeScript, and Rust.
> Keep your SDK. Move model behavior into MDA presets. Put cache, retries, key rotation, and rollout control around the call.

LLMix is the layer between your product and the provider SDK.

It does not ask you to rewrite your OpenAI, Anthropic, Gemini, LiteLLM, AI SDK, or custom client code. It wraps the call. The boring parts go around it: response cache, circuit breaker, key pools, singleflight, retry policy, adaptive concurrency, provider kwargs, and MDA config loading.

The model stops being a hard-coded string buried in application code. It becomes data. Change a preset, publish a compiled registry release, reload the service, and the next request can run a different provider or model. No redeploy for the usual model swap dance.

That is the whole thing. Small layer. Sharp edges filed down.

---

## Why It Exists

AI products in 2026 do not usually fail because one SDK call is hard.

They fail in the spaces around the call. A key gets rate limited. A provider gets slow. Two hundred users ask the same thing at once. A model swap needs a deploy. A cache key differs by one invisible parameter. One service is in Python, another is in TypeScript, and the Rust worker has to follow the same contract.

LLMix is for that part of the system. The signal chain between your app and the model.

You still own the prompt. You still own the SDK. LLMix owns the harness.

---

## Install

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python uses `sno-llmix` on PyPI because `llmix` was already taken. The import path is still `llmix`.

Provider helpers use optional SDKs. Install only the provider clients you call.

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

LLMix uses the MDA config packages for preset loading. They are also published
as standalone runtime loaders for apps that need `.mda` validation, integrity,
or trust-policy enforcement outside LLMix.

---

## Documentation

- [Usage reference](docs/llmix/llmix-usage-ref.md)
- [TypeScript guide](docs/llmix/llmix-typescript.md)
- [Python guide](docs/llmix/llmix-python.md)
- [Rust guide](docs/llmix/llmix-rust.md)
- [Secure LLMix configuration](docs/llmix/secure-mda/secure-llmix-configuration.md) ([de](docs/llmix/secure-mda/secure-llmix-configuration.de.md), [es](docs/llmix/secure-mda/secure-llmix-configuration.es.md), [fr](docs/llmix/secure-mda/secure-llmix-configuration.fr.md), [hi](docs/llmix/secure-mda/secure-llmix-configuration.hi.md), [ja](docs/llmix/secure-mda/secure-llmix-configuration.ja.md), [ko](docs/llmix/secure-mda/secure-llmix-configuration.ko.md), [ru](docs/llmix/secure-mda/secure-llmix-configuration.ru.md), [中文](docs/llmix/secure-mda/secure-llmix-configuration.zh.md))
- [Key pool operations](docs/llmix/key-pool-operations.md)
- [Standalone MDA config loader docs](docs/mda-config/README.md)

---

## At a Glance

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](docs/llmix/images/llmix-wraps-sdk.png)

LLMix wraps one provider call at a time.

It is not a router in the LiteLLM sense. It is closer to the harness you keep rebuilding around every agent, coder tool, extraction service, and internal AI workflow once traffic becomes real.

---

## Quick Start

### TypeScript

```typescript
import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
} from "@snoai/llmix";

const pipeline = new CallPipeline({
  dispatch: openaiDispatch(),
  responseCache: new TwoTierCache("memory"),
});

pipeline.setKeyPool("openai", new KeyPool([process.env.OPENAI_API_KEY!]));

const response = await pipeline.call({
  config: {
    provider: "openai",
    model: "gpt-4o-mini",
    common: { temperature: 0.2, maxOutputTokens: 512 },
    caching: { strategy: "memory" },
  },
  messages: [
    { role: "user", content: "Explain LLMix in one sentence." },
  ],
});

console.log(response.content);
await pipeline.close();
```

### Python

```python
import asyncio
import os

from llmix import (
    CallInput,
    CallPipeline,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    openai_dispatch,
)


async def main() -> None:
    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
        )
    )

    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    response = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "common": {"temperature": 0.2, "max_output_tokens": 512},
                "caching": {"strategy": "memory"},
            },
            messages=[
                {"role": "user", "content": "Explain LLMix in one sentence."}
            ],
        )
    )

    print(response.content)
    await pipeline.close()


asyncio.run(main())
```

### Rust

Rust exposes the same pipeline contract. The OpenAI helper is feature-gated.

```toml
[dependencies]
llmix-rs = { version = "2.0.0", features = ["providers-openai"] }
serde_json = "1"
tokio = { version = "1", features = ["macros", "rt"] }
```

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
            "common": { "temperature": 0.2, "max_output_tokens": 512 },
            "caching": { "strategy": "memory" }
        }),
        messages: vec![json!({
            "role": "user",
            "content": "Explain LLMix in one sentence."
        })],
        singleflight_key: None,
    })
    .await;
```

See the [Rust guide](docs/llmix/llmix-rust.md) for full `main` examples and feature flags.

---

## What You Get Around Every Call

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](docs/llmix/images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory plus optional Redis L2, with cross-runtime canonical cache keys |
| Key pools | Round-robin key selection, 429 rotation, and 401/403 dead-key eviction |
| Retries | Jittered exponential backoff, with `Retry-After` honored |
| Circuit breaker | Scoped by provider and effective base URL |
| Singleflight | Collapses identical concurrent work into one upstream request |
| Concurrency | AIMD adaptive semaphore, driven by rate-limit feedback |
| Provider kwargs | Common config becomes provider-specific request fields |
| Thinking tokens | Optional `<think>` extraction into normalized response objects |
| Registry | Signed compiled config registry with one live `current.json` pointer |

The defaults are meant to be boring. Tune them when real traffic gives you a reason.

---

## MDA Presets

![LLMix turns editable MDA presets into a signed compiled registry release opened through the official flow.](docs/llmix/images/llmix-mda-config.png)

LLMix uses MDA as the source format for model presets. Human notes and runtime
settings live in one file. Production services read the compiled registry, not
the mutable source tree.
Python, TypeScript, and Rust can require MDA integrity, `requires.network`, and
verifier-hook based signatures while loading or publishing registry output.
Real Rekor transport and Sigstore cryptography are supplied by caller-provided
clients/verifiers.

```mda
---
name: extraction
description: Entity extraction preset.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4o-mini
      temperature: 0.2
      maxOutputTokens: 512
    caching:
      strategy: redis-or-memory
    providerOptions:
      openai:
        reasoningEffort: medium
---
# extraction

Extract named entities. Return compact JSON.
```

Load it directly when editing or testing a preset:

```typescript
import { loadMdaConfig } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/source/search/extraction.mda");
```

```python
from llmix import load_mda_config

config = load_mda_config("./config/llm/source/search/extraction.mda")
```

```rust
use llmix_rs::load_config;

let config = load_config("./config/llm/source/search/extraction.mda")?;
```

For production services, use the registry.

---

## Config Registry

Use this layout. Treat it as the public contract:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

`source/` is edited by people. `current.json` and `compiled/` are generated by
LLMix. Store the trust anchor outside `config/llm`.

The release flow is:

1. Put source presets in `config/llm/source/<module>/<preset>.mda`.
2. Run the MDA CLI validation, integrity, signing, verification, and release
   prepare steps.
3. Run the official LLMix publisher.
4. Commit or package generated `config/llm/current.json` and
   `config/llm/compiled/`.
5. Run MDA CLI release finalize and doctor checks.
6. Open `config/llm` at runtime through LLMix with the external trust anchor.

```text
mda validate config/llm/source/search/extraction.mda --target source --json
mda integrity compute config/llm/source/search/extraction.mda --target source --write --json
mda sign config/llm/source/search/extraction.mda ... --in-place --json
mda verify config/llm/source/search/extraction.mda --target source ... --json
mda release prepare --target llmix-registry --source config/llm/source --registry-dir config/llm ... --json
```

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

await new ConfigRegistryPublisher("config/llm").publish({
  trustedRuntime: true,
  trustPolicy: sourceTrustPolicy,
  didWebVerifier,
  registryRoot: { signer: registryRootSigner },
});

// Then run:
// mda release finalize --target llmix-registry --registry-dir config/llm ...
// mda doctor release --target llmix-registry --source config/llm/source --registry-dir config/llm ...

const trust = await loadLlmixTrustManifest(process.env.LLMIX_TRUST_ANCHOR!);
const manager = await ConfigRegistryManager.open("config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(trust, { didWebVerifier }),
});
const config = await manager.getPreset("search", "extraction");
```

Managers expose the active revision and reload health metadata. That makes it
easy to say exactly which config a service is running. See
[Secure LLMix Configuration with MDA](docs/llmix/secure-mda/secure-llmix-configuration.md)
for the complete release flow and runtime tamper-rejection proof.

---

## Provider Coverage

The public dispatch helpers cover the providers we actually test.

| Provider | Python | TypeScript | Notes |
|----------|--------|------------|-------|
| OpenAI | `openai_dispatch` | `openaiDispatch` | OpenAI Responses and chat-style flows |
| Anthropic | `anthropic_dispatch` | `anthropicDispatch` | Messages API, thinking budget validation |
| Gemini | `gemini_dispatch` | `geminiDispatch` | Google GenAI-compatible params |
| OpenRouter | `openrouter_dispatch` | `openrouterDispatch` | OpenAI-compatible |
| DeepInfra | `deepinfra_dispatch` | `deepinfraDispatch` | OpenAI-compatible |
| Novita | `novita_dispatch` | `novitaDispatch` | OpenAI-compatible |
| Together | `together_dispatch` | `togetherDispatch` | OpenAI-compatible |
| Sno GPU | `sno_gpu_dispatch` | `snoGpuDispatch` | On-prem OpenAI-compatible GPU endpoints |

Rust currently ships the neutral pipeline plus feature-gated helpers for OpenAI, Anthropic, Gemini, and Sno GPU. Treat Rust provider helpers as beta. The cache, key-pool, registry, retry, and pipeline contract are aligned with Python and TypeScript.

OpenAI-compatible providers reuse the OpenAI request shape with provider-specific `base_url` handling. That keeps the contract plain. Plain is useful.

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key or comma-separated key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key or comma-separated key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key or comma-separated key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key or comma-separated key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key or comma-separated key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key or comma-separated key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key or comma-separated key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Sno GPU key-pool variables for provider id `sno-gpu` |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata, and kill-switch state |

`load_keys_from_env("provider-name")` checks `PROVIDER_NAME_KEYS` first, then `PROVIDER_NAME_API_KEY`. Dashes become underscores.

---

## What This Is Not

- Not a streaming framework. Streaming stays with your SDK.
- Not a prompt framework. Bring your own prompt layer.
- Not a provider marketplace. One call uses the provider named by its config.
- Not a reason to hide every model decision behind indirection. Some things should stay in code.

LLMix is useful when the same model-call shape keeps showing up across services. If you have one script and one key, you probably do not need it yet.

---

## Development

```bash
# Install TypeScript workspace dependencies
bun install

# Install Python workspace dependencies
uv sync --project packages/llmix/python --extra dev
uv sync --project packages/mda-config/python --all-groups

# Full monorepo checks
bun run build
bun run check
bun run test
```

---

## License

[Apache-2.0](LICENSE)

## Related

- [AI SDK](https://ai-sdk.dev/)
- [Promptix](https://github.com/sno-ai/promptix)
