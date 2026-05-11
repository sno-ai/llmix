# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

अन्य भाषाओं में पढ़ें: [English](../../../README.md) · [中文](README.zh-CN.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · **हिन्दी**

> Python, TypeScript और Rust के लिए config-driven LLM calls.
> अपना SDK बनाए रखें। Model behavior को MDA presets में रखें। Call के आसपास cache, retries, key rotation और rollout control जोड़ें।

LLMix आपके product और provider SDK के बीच की layer है।

यह आपसे OpenAI, Anthropic, Gemini, LiteLLM, AI SDK या custom client code दोबारा लिखने को नहीं कहता। यह call को wrap करता है। उसके आसपास की जरूरी चीजें यह संभालता है: response cache, circuit breaker, key pools, singleflight, retry policy, adaptive concurrency, provider kwargs और MDA config loading.

Model अब application code में छिपी hard-coded string नहीं रहता। वह data बन जाता है। Preset बदलें, registry snapshot publish करें, service reload करें, और अगली request अलग provider या model चला सकती है। सामान्य model swap के लिए redeploy नहीं चाहिए।

बस यही है। छोटी layer. धारदार किनारे कम कर दिए गए हैं।

---

## यह क्यों है

2026 के AI products आम तौर पर इसलिए fail नहीं होते कि एक SDK call कठिन है।

समस्या call के आसपास की जगहों में आती है। कोई key rate limited हो जाती है। कोई provider slow हो जाता है। दो सौ users एक साथ वही बात पूछते हैं। Model swap के लिए deploy करना पड़ता है। Cache key किसी अदृश्य parameter से अलग हो जाती है। एक service Python में है, दूसरी TypeScript में, और Rust worker को भी वही contract follow करना है।

LLMix system के इसी हिस्से के लिए है। आपके app और model के बीच की signal chain.

Prompt आपका है। SDK आपका है। Harness LLMix संभालता है।

---

## Install

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python में PyPI package `sno-llmix` है क्योंकि `llmix` नाम पहले से लिया जा चुका था। Import path फिर भी `llmix` ही है।

Provider helpers optional SDKs का उपयोग करते हैं। केवल वही provider clients install करें जिन्हें आप call करते हैं।

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## Documentation

- [Usage reference](../llmix-usage-ref.md)
- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [MDA के साथ सुरक्षित LLMix configuration](../secure-mda/secure-llmix-configuration.hi.md)
- [Key pool operations](../key-pool-operations.md)

---

## एक नज़र में

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix एक बार में एक provider call को wrap करता है।

यह LiteLLM वाले अर्थ में router नहीं है। यह उस harness के ज्यादा करीब है जिसे real traffic आने के बाद आप हर agent, coder tool, extraction service और internal AI workflow के आसपास बार-बार बनाते हैं।

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

Rust वही pipeline contract expose करता है। OpenAI helper feature-gated है।

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

पूरे `main` examples और feature flags के लिए [Rust guide](../llmix-rust.md) देखें।

---

## हर call के आसपास क्या मिलता है

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory plus optional Redis L2, cross-runtime canonical cache keys के साथ |
| Key pools | Round-robin key selection, 429 rotation, और 401/403 dead-key eviction |
| Retries | Jittered exponential backoff, `Retry-After` का सम्मान करते हुए |
| Circuit breaker | Provider और effective base URL के हिसाब से scoped |
| Singleflight | समान concurrent work को एक upstream request में collapse करता है |
| Concurrency | Rate-limit feedback से driven AIMD adaptive semaphore |
| Provider kwargs | Common config को provider-specific request fields में बदलता है |
| Thinking tokens | Optional `<think>` extraction into normalized response objects |
| Registry | Immutable config snapshots with one live `current.json` pointer |

Defaults boring रहने के लिए बनाए गए हैं। Real traffic कोई ठोस वजह दे तभी tune करें।

---

## MDA Presets

![LLMix turns editable MDA presets into immutable registry snapshots that Python, TypeScript, and Rust runtimes can read consistently.](../images/llmix-mda-config.png)

LLMix config authoring के लिए MDA Source Mode का उपयोग करता है। Human notes और runtime settings एक ही file में रहते हैं। Runtime केवल resolved JSON देखता है।

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

Authoring या testing के दौरान इसे सीधे load करें:

```typescript
import { loadMdaConfig } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/search/extraction.mda");
```

```python
from llmix import load_mda_config

config = load_mda_config("./config/llm/search/extraction.mda")
```

```rust
use llmix_rs::load_config;

let config = load_config("./config/llm/search/extraction.mda")?;
```

Production services के लिए registry का उपयोग करें।

---

## Config Registry

Editable MDA files humans के लिए अच्छी हैं। Running services को कुछ ज्यादा शांत चाहिए।

LLMix Config Registry authoring files को immutable, content-addressed snapshots में publish करती है। Runtime code mutable source tree नहीं, active snapshot पढ़ता है।

```text
config/llm/
  authoring/
    search/
      extraction.mda
  snapshots/
    2026-05-09T000000Z-...
  current.json
```

```python
from llmix import ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir

root = resolve_config_dir().config_dir
ConfigRegistryPublisher(root).publish()

manager = ConfigRegistryManager.open(root)
config = manager.get_preset("search", "extraction")
```

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  resolveConfigDir,
} from "@snoai/llmix";

const { configDir } = resolveConfigDir();
await new ConfigRegistryPublisher(configDir).publish();

const manager = await ConfigRegistryManager.open(configDir);
const config = await manager.getPreset("search", "extraction");
```

Managers active revision और reload health metadata expose करते हैं। इससे यह बताना आसान हो जाता है कि service ठीक कौन सा config चला रही है।

---

## Provider Coverage

Public dispatch helpers उन providers को cover करते हैं जिन्हें हम सच में test करते हैं।

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

Rust अभी neutral pipeline और OpenAI, Anthropic, Gemini, Sno GPU के लिए feature-gated helpers ship करता है। Rust provider helpers को beta मानें। Cache, key-pool, registry, retry और pipeline contract Python और TypeScript के साथ aligned हैं।

OpenAI-compatible providers OpenAI request shape को provider-specific `base_url` handling के साथ reuse करते हैं। इससे contract plain रहता है। Plain उपयोगी होता है।

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key या comma-separated key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key या comma-separated key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key या comma-separated key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key या comma-separated key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key या comma-separated key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key या comma-separated key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key या comma-separated key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Provider id `sno-gpu` के लिए Sno GPU key-pool variables |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata, और kill-switch state |

`load_keys_from_env("provider-name")` पहले `PROVIDER_NAME_KEYS` देखता है, फिर `PROVIDER_NAME_API_KEY`। Dashes underscores बन जाते हैं।

---

## यह क्या नहीं है

- Streaming framework नहीं है। Streaming आपके SDK में रहती है।
- Prompt framework नहीं है। अपना prompt layer लाएँ।
- Provider marketplace नहीं है। एक call वही provider use करता है जिसका नाम config में है।
- हर model decision को indirection के पीछे छिपाने की वजह नहीं है। कुछ चीजें code में ही रहनी चाहिए।

LLMix तब उपयोगी है जब वही model-call shape कई services में बार-बार दिखने लगे। अगर आपके पास एक script और एक key है, तो शायद अभी इसकी जरूरत नहीं है।

---

## Development

```bash
# TypeScript
bun install
bun run test:typescript
bun run check

# Python
uv sync --project packages/llmix/python --extra dev
uv run --project packages/llmix/python pytest
uv run --project packages/llmix/python pyright

# Rust
cargo test --manifest-path packages/llmix/rust/Cargo.toml
cargo clippy --manifest-path packages/llmix/rust/Cargo.toml -- -D warnings
```

---

## License

[Apache-2.0](../../../LICENSE)

## Related

- [AI SDK](https://ai-sdk.dev/)
- [Promptix](https://github.com/sno-ai/promptix)
