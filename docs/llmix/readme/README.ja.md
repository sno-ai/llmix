# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

Read in other languages: [English](../../../README.md) · [中文](README.zh-CN.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [한국어](README.ko.md) · **日本語** · [हिन्दी](README.hi.md)

> Python、TypeScript、Rust 向けの設定駆動 LLM 呼び出しレイヤー。
> 既存の SDK はそのまま使います。モデルの振る舞いは MDA preset に移します。cache、retries、key rotation、rollout control を呼び出しの周囲に置きます。

LLMix は、あなたのプロダクトと provider SDK の間にあるレイヤーです。

OpenAI、Anthropic、Gemini、LiteLLM、AI SDK、または自前の client code を書き直す必要はありません。LLMix は呼び出しを包みます。その周囲に、毎回必要になる部分を置きます。response cache、circuit breaker、key pools、singleflight、retry policy、adaptive concurrency、provider kwargs、MDA config loading です。

モデルは、アプリケーションコードに埋め込まれた hard-coded string ではなくなります。データになります。preset を変更し、registry snapshot を publish し、service を reload すれば、次の request は別の provider や model で動かせます。普通の model swap に redeploy はいりません。

それだけです。小さなレイヤーです。扱いづらい角を削ってあります。

---

## なぜ必要か

2026 年の AI プロダクトは、SDK 呼び出しが 1 回難しいから壊れるわけではありません。

壊れるのは呼び出しの周辺です。key が rate limit される。provider が遅くなる。200 人のユーザーが同時に同じ質問をする。model swap に deploy が必要になる。見えない parameter 1 つで cache key が変わる。ある service は Python、別の service は TypeScript、そして Rust worker も同じ契約に従う必要がある。

LLMix はその部分のためのものです。app と model の間にある signal chain です。

Prompt はあなたのものです。SDK もあなたのものです。LLMix は harness を担当します。

---

## インストール

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python は PyPI で `sno-llmix` を使います。`llmix` はすでに使われていたためです。import path はそのまま `llmix` です。

Provider helper は optional SDK を使います。実際に呼び出す provider client だけをインストールしてください。

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## ドキュメント

- [Usage reference](../llmix-usage-ref.md)
- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [LLMix MDA 設定を安全に使う](../secure-mda/secure-llmix-configuration.ja.md)
- [Key pool operations](../key-pool-operations.md)

---

## 概要

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix は一度に 1 つの provider call を包みます。

LiteLLM 的な router ではありません。実トラフィックが来た後に、agent、coder tool、extraction service、社内 AI workflow の周囲に何度も作ることになる harness に近いものです。

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

Rust は同じ pipeline contract を提供します。OpenAI helper は feature-gated です。

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

完全な `main` 例と feature flags は [Rust guide](../llmix-rust.md) を参照してください。

---

## すべての呼び出しの周囲に入るもの

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory と optional Redis L2。runtime 間で同じ canonical cache keys |
| Key pools | Round-robin key selection、429 rotation、401/403 dead-key eviction |
| Retries | Jittered exponential backoff。`Retry-After` を尊重 |
| Circuit breaker | Provider と effective base URL で scope |
| Singleflight | 同一の concurrent work を 1 つの upstream request にまとめる |
| Concurrency | Rate-limit feedback で動く AIMD adaptive semaphore |
| Provider kwargs | Common config を provider-specific request fields に変換 |
| Thinking tokens | Optional `<think>` extraction into normalized response objects |
| Registry | Immutable config snapshots と live `current.json` pointer |

Defaults は退屈であるべきです。実トラフィックが理由をくれたときに調整してください。

---

## MDA Presets

![LLMix turns editable MDA presets into immutable registry snapshots that Python, TypeScript, and Rust runtimes can read consistently.](../images/llmix-mda-config.png)

LLMix は config authoring に MDA Source Mode を使います。人間向けの notes と runtime settings が 1 つのファイルに入ります。runtime が見るのは resolved JSON だけです。

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

Authoring や testing では直接 load できます。

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

Production service では registry を使ってください。

---

## Config Registry

編集可能な MDA files は人間には便利です。実行中の service には、もっと静かなものが必要です。

LLMix Config Registry は authoring files を immutable, content-addressed snapshots として publish します。Runtime code は mutable source tree ではなく active snapshot を読みます。

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

Managers は active revision と reload health metadata を公開します。これにより、service がどの config を実行しているかを正確に言えます。

---

## Provider Coverage

Public dispatch helpers は、実際にテストしている provider をカバーします。

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

Rust は現在、neutral pipeline に加えて OpenAI、Anthropic、Gemini、Sno GPU 用の feature-gated helpers を提供します。Rust provider helpers は beta として扱ってください。Cache、key-pool、registry、retry、pipeline contract は Python と TypeScript に揃えています。

OpenAI-compatible providers は、provider-specific な `base_url` handling とともに OpenAI request shape を再利用します。契約は単純に保たれます。単純さは役に立ちます。

---

## 環境変数

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key または comma-separated key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key または comma-separated key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key または comma-separated key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key または comma-separated key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key または comma-separated key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key または comma-separated key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key または comma-separated key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Provider id `sno-gpu` 用の Sno GPU key-pool variables |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata, kill-switch state |

`load_keys_from_env("provider-name")` はまず `PROVIDER_NAME_KEYS` を確認し、その後 `PROVIDER_NAME_API_KEY` を確認します。Dash は underscore になります。

---

## これは何ではないか

- Streaming framework ではありません。Streaming は SDK 側に残します。
- Prompt framework ではありません。自分の prompt layer を使ってください。
- Provider marketplace ではありません。1 回の呼び出しは config で指定された provider を使います。
- すべての model decision を indirection の裏に隠す理由ではありません。コードに残すべきものもあります。

同じ model-call shape が複数の service に繰り返し現れるとき、LLMix は役に立ちます。script 1 つと key 1 つだけなら、まだ必要ないかもしれません。

---

## 開発

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
