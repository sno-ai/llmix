# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

Read in other languages: [English](../../../README.md) · [中文](README.zh-CN.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md) · [Русский](README.ru.md) · **한국어** · [日本語](README.ja.md) · [हिन्दी](README.hi.md)

> Python, TypeScript, Rust를 위한 설정 기반 LLM 호출 계층.
> 기존 SDK는 그대로 둡니다. 모델 동작은 MDA preset으로 옮깁니다. 호출 주변에 cache, retries, key rotation, rollout control을 둡니다.

LLMix는 제품과 provider SDK 사이에 있는 계층입니다.

OpenAI, Anthropic, Gemini, LiteLLM, AI SDK, 또는 자체 client 코드를 다시 쓰라고 요구하지 않습니다. 호출을 감쌉니다. 반복적으로 필요한 부분을 그 주변에 둡니다: response cache, circuit breaker, key pools, singleflight, retry policy, adaptive concurrency, provider kwargs, MDA config loading.

모델은 더 이상 애플리케이션 코드 안에 박힌 hard-coded string이 아닙니다. 데이터가 됩니다. preset을 바꾸고 registry snapshot을 publish한 뒤 서비스를 reload하면, 다음 요청은 다른 provider나 model을 사용할 수 있습니다. 흔한 model swap에 redeploy가 필요하지 않습니다.

핵심은 그게 전부입니다. 작은 계층입니다. 날카로운 부분을 정리해 둔 계층입니다.

---

## 왜 필요한가

2026년의 AI 제품은 보통 SDK 호출 하나가 어려워서 실패하지 않습니다.

실패는 호출 주변에서 생깁니다. key 하나가 rate limit에 걸립니다. provider가 느려집니다. 사용자 200명이 동시에 같은 질문을 합니다. model swap에 deploy가 필요합니다. cache key가 보이지 않는 parameter 하나 때문에 달라집니다. 한 서비스는 Python이고, 다른 서비스는 TypeScript이며, Rust worker도 같은 계약을 따라야 합니다.

LLMix는 시스템의 그 부분을 위한 것입니다. 앱과 모델 사이의 signal chain입니다.

Prompt는 여전히 당신의 것입니다. SDK도 여전히 당신의 것입니다. LLMix는 harness를 맡습니다.

---

## 설치

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python은 PyPI에서 `sno-llmix` 이름을 사용합니다. `llmix`가 이미 사용 중이기 때문입니다. import path는 여전히 `llmix`입니다.

Provider helper는 optional SDK를 사용합니다. 실제로 호출할 provider client만 설치하세요.

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## 문서

- [Usage reference](../llmix-usage-ref.md)
- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [LLMix MDA 설정을 안전하게 사용하기](../secure-mda/secure-llmix-configuration.ko.md)
- [Key pool operations](../key-pool-operations.md)

---

## 한눈에 보기

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix는 한 번에 하나의 provider call을 감쌉니다.

LiteLLM 같은 의미의 router가 아닙니다. 실제 트래픽이 생긴 뒤 agent, coder tool, extraction service, 내부 AI workflow마다 반복해서 만들게 되는 harness에 더 가깝습니다.

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

Rust는 같은 pipeline contract를 제공합니다. OpenAI helper는 feature로 켭니다.

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

전체 `main` 예제와 feature flags는 [Rust guide](../llmix-rust.md)를 보세요.

---

## 모든 호출 주변에 생기는 것

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory와 optional Redis L2, runtime 간 canonical cache keys |
| Key pools | Round-robin key selection, 429 rotation, 401/403 dead-key eviction |
| Retries | Jittered exponential backoff, `Retry-After` 준수 |
| Circuit breaker | Provider와 effective base URL 기준 scope |
| Singleflight | 동일한 concurrent work를 하나의 upstream request로 합침 |
| Concurrency | Rate-limit feedback으로 조정되는 AIMD adaptive semaphore |
| Provider kwargs | Common config를 provider-specific request fields로 변환 |
| Thinking tokens | Optional `<think>` extraction into normalized response objects |
| Registry | Immutable config snapshots with one live `current.json` pointer |

기본값은 조용해야 합니다. 실제 트래픽이 이유를 줄 때 조정하세요.

---

## MDA Presets

![LLMix turns editable MDA presets into immutable registry snapshots that Python, TypeScript, and Rust runtimes can read consistently.](../images/llmix-mda-config.png)

LLMix는 config authoring에 MDA Source Mode를 사용합니다. 사람이 읽는 notes와 runtime settings가 한 파일에 있습니다. runtime은 resolved JSON만 봅니다.

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

작성하거나 테스트할 때 직접 load할 수 있습니다.

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

Production service에서는 registry를 사용하세요.

---

## Config Registry

Editable MDA files는 사람에게 좋습니다. 실행 중인 service에는 더 조용한 것이 필요합니다.

LLMix Config Registry는 authoring files를 immutable, content-addressed snapshots로 publish합니다. Runtime code는 mutable source tree가 아니라 active snapshot을 읽습니다.

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

Managers는 active revision과 reload health metadata를 노출합니다. 그래서 service가 정확히 어떤 config를 실행 중인지 말할 수 있습니다.

---

## Provider Coverage

Public dispatch helpers는 실제로 테스트하는 provider를 다룹니다.

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

Rust는 현재 neutral pipeline과 OpenAI, Anthropic, Gemini, Sno GPU용 feature-gated helpers를 제공합니다. Rust provider helpers는 beta로 보세요. Cache, key-pool, registry, retry, pipeline contract는 Python 및 TypeScript와 맞춰져 있습니다.

OpenAI-compatible providers는 provider-specific `base_url` handling과 함께 OpenAI request shape를 재사용합니다. 계약이 단순해집니다. 단순함은 유용합니다.

---

## 환경 변수

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key 또는 comma-separated key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key 또는 comma-separated key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key 또는 comma-separated key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key 또는 comma-separated key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key 또는 comma-separated key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key 또는 comma-separated key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key 또는 comma-separated key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Provider id `sno-gpu`를 위한 Sno GPU key-pool variables |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata, kill-switch state |

`load_keys_from_env("provider-name")`는 먼저 `PROVIDER_NAME_KEYS`를 확인하고, 그다음 `PROVIDER_NAME_API_KEY`를 확인합니다. Dash는 underscore로 바뀝니다.

---

## 이것이 아닌 것

- Streaming framework가 아닙니다. Streaming은 SDK에 남겨 둡니다.
- Prompt framework가 아닙니다. 자신의 prompt layer를 사용하세요.
- Provider marketplace가 아닙니다. 한 호출은 config에 지정된 provider를 사용합니다.
- 모든 model decision을 indirection 뒤에 숨기라는 뜻이 아닙니다. 어떤 것은 code에 남아 있어야 합니다.

같은 model-call 형태가 여러 service에 반복해서 나타날 때 LLMix가 유용합니다. script 하나와 key 하나만 있다면 아직 필요하지 않을 수 있습니다.

---

## 개발

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
