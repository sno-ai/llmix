# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

Read in other languages: [English](../../../README.md) · [中文](README.zh-CN.md) · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md) · **Русский** · [한국어](README.ko.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md)

> LLM-вызовы под управлением конфигурации для Python, TypeScript и Rust.
> Оставьте свой SDK. Перенесите поведение модели в MDA-presets. Добавьте вокруг вызова cache, retries, ротацию ключей и контроль rollout.

LLMix находится между вашим продуктом и SDK провайдера.

Он не заставляет переписывать код для OpenAI, Anthropic, Gemini, LiteLLM, AI SDK или собственных клиентов. Он оборачивает вызов. Вокруг него располагаются скучные, но нужные части: response cache, circuit breaker, key pools, singleflight, retry policy, adaptive concurrency, provider kwargs и загрузка MDA-конфигурации.

Модель перестает быть hard-coded строкой внутри приложения. Она становится данными. Измените preset, опубликуйте compiled registry release, перезагрузите сервис, и следующий запрос может пойти к другому провайдеру или другой модели. Обычная смена модели больше не требует redeploy.

В этом вся идея. Небольшой слой. Острые края сглажены.

---

## Зачем это нужно

AI-продукты в 2026 году обычно ломаются не потому, что один SDK-вызов сложен.

Они ломаются вокруг вызова. Один ключ получает rate limit. Провайдер замедляется. Двести пользователей одновременно спрашивают одно и то же. Смена модели требует deploy. Cache key отличается из-за одного невидимого параметра. Один сервис написан на Python, другой на TypeScript, а Rust-worker должен соблюдать тот же контракт.

LLMix нужен для этой части системы. Для сигнальной цепочки между приложением и моделью.

Prompt остается вашим. SDK остается вашим. LLMix берет на себя harness.

---

## Установка

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python использует имя `sno-llmix` на PyPI, потому что `llmix` уже было занято. Import path остается `llmix`.

Provider helpers используют optional SDK. Устанавливайте только клиентов тех провайдеров, которых вызываете.

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## Документация

- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [Безопасная конфигурация LLMix с MDA](../secure-mda/secure-llmix-configuration.ru.md)
- [Key pool operations](../key-pool-operations.md)

---

## Краткий обзор

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix оборачивает один provider-вызов за раз.

Это не router в смысле LiteLLM. Это ближе к harness, который приходится снова и снова строить вокруг каждого agent, coder tool, extraction service и внутреннего AI workflow, когда появляется реальный трафик.

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

Rust предоставляет тот же pipeline contract. OpenAI helper включается через feature.

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

Полные примеры `main` и feature flags см. в [Rust guide](../llmix-rust.md).

---

## Что добавляется вокруг каждого вызова

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory плюс optional Redis L2, с canonical cache keys между runtimes |
| Key pools | Round-robin выбор ключей, rotation при 429 и eviction мертвых ключей при 401/403 |
| Retries | Exponential backoff с jitter, с учетом `Retry-After` |
| Circuit breaker | Scope по provider и effective base URL |
| Singleflight | Схлопывает одинаковую concurrent работу в один upstream request |
| Concurrency | AIMD adaptive semaphore, управляется feedback от rate limit |
| Provider kwargs | Common config превращается в provider-specific request fields |
| Thinking tokens | Optional extraction `<think>` в normalized response objects |
| Registry | Signed compiled config registry with one live `current.json` pointer |

Defaults специально должны быть скучными. Настраивайте их, когда реальный трафик даст причину.

---

## MDA Presets

![LLMix turns editable MDA presets into signed compiled registry releases that runtimes can open through the official flow.](../images/llmix-mda-config.png)

LLMix uses MDA Source Mode for preset source files. Put every human-edited preset under the official source directory:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

`source/` is for people. `current.json` and `compiled/` are generated. Keep the trust anchor outside `config/llm`.

A preset is still a normal MDA file:

```mda
---
name: openai_fast
description: Fast OpenAI preset for search summaries.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 512
    caching:
      strategy: redis-or-memory
    providerOptions:
      openai:
        reasoningEffort: medium
---
# openai_fast

Summarize search results for a research workflow.
```

For editing or tests, direct loaders can read source presets:

```typescript
import { loadMdaConfig } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/source/search_summary/openai_fast.mda");
```

```python
from llmix import load_mda_config

config = load_mda_config("./config/llm/source/search_summary/openai_fast.mda")
```

```rust
use llmix_rs::load_config;

let config = load_config("./config/llm/source/search_summary/openai_fast.mda")?;
```

Production services should use the registry flow below.

---

## Config Registry

MDA is the standard. The MDA CLI validates, computes integrity, signs, verifies, and gates releases. LLMix is the official registry publisher and runtime opener. The app repo owns source presets and runtime wiring; it does not implement a compiler or publisher.

Required flow:

1. Put presets in `config/llm/source/<module>/<preset>.mda`.
2. Run the MDA CLI validation, integrity, signing, verification, and release prepare gates.
3. Run the official LLMix publisher.
4. Let LLMix generate `config/llm/current.json` and `config/llm/compiled/`.
5. Run the MDA CLI release finalize and doctor checks.
6. Deliver the trust anchor from outside `config/llm`.
7. Open `config/llm` at runtime through LLMix with that external trust anchor.

The did:web example assumes `release/did-web-private-key.pem` and `release/did.json` already exist outside `config/llm`.

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

```typescript
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const trust = await loadLlmixTrustManifest(process.env.LLMIX_TRUST_ANCHOR!);
const manager = await ConfigRegistryManager.open("config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(trust, { didWebVerifier }),
});
const config = await manager.getPreset("search_summary", "openai_fast");
```

`didWebVerifier` is the app verifier hook required by this did:web policy. For a command-line runtime proof, use `llmix check-registry --did-document release/did.json`; in app code, pass the verifier hooks required by your trust policy.

The external trust anchor can come from an environment variable, application config, build-time constant, secret/config manager, Kubernetes or cloud config, or release attestation.

---

## Покрытие провайдеров

Public dispatch helpers покрывают тех провайдеров, которых мы реально тестируем.

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

Rust сейчас поставляет neutral pipeline и feature-gated helpers для OpenAI, Anthropic, Gemini и Sno GPU. Считайте Rust provider helpers beta. Cache, key-pool, registry, retry и pipeline contract согласованы с Python и TypeScript.

OpenAI-compatible providers используют форму OpenAI request с provider-specific `base_url` handling. Это сохраняет контракт простым. Простота полезна.

---

## Переменные окружения

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key или comma-separated key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key или comma-separated key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key или comma-separated key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key или comma-separated key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key или comma-separated key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key или comma-separated key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key или comma-separated key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Sno GPU key-pool variables для provider id `sno-gpu` |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata и kill-switch state |

`load_keys_from_env("provider-name")` сначала проверяет `PROVIDER_NAME_KEYS`, затем `PROVIDER_NAME_API_KEY`. Дефисы становятся подчеркиваниями.

---

## Чем это не является

- Не streaming framework. Streaming остается в вашем SDK.
- Не prompt framework. Используйте свой prompt layer.
- Не marketplace провайдеров. Один вызов использует provider, указанный в config.
- Не повод прятать каждое решение о модели за indirection. Некоторые вещи должны оставаться в коде.

LLMix полезен, когда одна и та же форма model-call повторяется в нескольких сервисах. Если у вас один script и один key, скорее всего, он пока не нужен.

---

## Разработка

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
