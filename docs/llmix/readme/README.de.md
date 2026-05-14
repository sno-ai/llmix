# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

Read in other languages: [English](../../../README.md) · [中文](README.zh-CN.md) · **Deutsch** · [Español](README.es.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md)

> Konfigurationsgesteuerte LLM-Aufrufe für Python, TypeScript und Rust.
> Behalte dein SDK. Verschiebe Modellverhalten in MDA-Presets. Lege Cache, Retries, Schlüsselrotation und Rollout-Kontrolle um den Aufruf.

LLMix ist die Schicht zwischen deinem Produkt und dem Provider-SDK.

Es verlangt nicht, dass du deinen OpenAI-, Anthropic-, Gemini-, LiteLLM-, AI-SDK- oder eigenen Client-Code neu schreibst. Es umschließt den Aufruf. Die langweiligen Teile liegen außen: Response-Cache, Circuit Breaker, Key Pools, Singleflight, Retry-Policy, adaptive Concurrency, Provider-Kwargs und MDA-Konfigurationsladen.

Das Modell ist kein hart kodierter String mehr tief im Anwendungscode. Es wird zu Daten. Ändere ein Preset, veröffentliche einen Registry-Snapshot, lade den Service neu, und die nächste Anfrage kann einen anderen Provider oder ein anderes Modell verwenden. Kein Redeploy für den üblichen Modellwechsel.

Das ist der Kern. Eine kleine Schicht. Die scharfen Kanten sind geglättet.

---

## Warum es existiert

AI-Produkte im Jahr 2026 scheitern meistens nicht daran, dass ein einzelner SDK-Aufruf schwer ist.

Sie scheitern in den Bereichen um den Aufruf herum. Ein Key wird rate-limited. Ein Provider wird langsam. Zweihundert Nutzer stellen gleichzeitig dieselbe Frage. Ein Modellwechsel braucht ein Deployment. Ein Cache-Key unterscheidet sich durch einen unsichtbaren Parameter. Ein Service läuft in Python, ein anderer in TypeScript, und der Rust-Worker muss denselben Vertrag einhalten.

LLMix ist für genau diesen Teil des Systems. Die Signalkette zwischen deiner App und dem Modell.

Den Prompt behältst du. Das SDK behältst du. LLMix übernimmt den Harness.

---

## Installation

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python verwendet auf PyPI `sno-llmix`, weil `llmix` bereits vergeben war. Der Import-Pfad bleibt `llmix`.

Provider-Helper verwenden optionale SDKs. Installiere nur die Provider-Clients, die du wirklich aufrufst.

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## Dokumentation

- [Usage reference](../llmix-usage-ref.md)
- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [Sichere LLMix-MDA-Konfiguration](../secure-mda/secure-llmix-configuration.de.md)
- [Key pool operations](../key-pool-operations.md)

---

## Überblick

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix umschließt jeweils einen Provider-Aufruf.

Es ist kein Router im LiteLLM-Sinn. Es ist eher der Harness, den man um jeden Agent, jedes Coder-Tool, jeden Extraction-Service und jeden internen AI-Workflow wieder baut, sobald echter Traffic kommt.

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

Rust stellt denselben Pipeline-Vertrag bereit. Der OpenAI-Helper ist feature-gated.

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

Vollständige `main`-Beispiele und Feature-Flags stehen im [Rust guide](../llmix-rust.md).

---

## Was jeder Aufruf dazubekommt

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 Memory plus optionales Redis L2, mit kanonischen Cache-Keys über Runtimes hinweg |
| Key pools | Round-robin Key-Auswahl, Rotation bei 429 und Eviction toter Keys bei 401/403 |
| Retries | Exponentielles Backoff mit Jitter, `Retry-After` wird beachtet |
| Circuit breaker | Gescoped nach Provider und effective base URL |
| Singleflight | Fasst identische gleichzeitige Arbeit zu einem Upstream-Request zusammen |
| Concurrency | AIMD-adaptives Semaphore, gesteuert durch Rate-Limit-Feedback |
| Provider kwargs | Common config wird zu provider-spezifischen Request-Feldern |
| Thinking tokens | Optionale Extraktion von `<think>` in normalisierte Response-Objekte |
| Registry | Signed compiled config registry with one live `current.json` pointer |

Die Defaults sollen langweilig sein. Tune sie erst, wenn echter Traffic dir einen Grund gibt.

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

For editing or tests, direct loaders can read source presets:

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

```bash
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
  RegistryRootVerificationOptions,
} from "@snoai/llmix";

await new ConfigRegistryPublisher("config/llm").publish({
  trustedRuntime: true,
  trustPolicy: sourceTrustPolicy,
  didWebVerifier,
  registryRoot: { signer: registryRootSigner },
});

const signedRoot: RegistryRootVerificationOptions = {
  trustPolicy: registryRootTrustPolicy,
  didWebVerifier,
  expectedRootDigest: externalTrust.expectedRootDigest,
  minimumRevision: externalTrust.minimumRevision,
};

const manager = await ConfigRegistryManager.open("config/llm", { signedRoot });
const config = await manager.getPreset("search", "extraction");
```

The external trust anchor can come from an environment variable, application config, build-time constant, secret/config manager, Kubernetes or cloud config, or release attestation.

---

## Provider-Abdeckung

Die öffentlichen Dispatch-Helper decken die Provider ab, die wir tatsächlich testen.

| Provider | Python | TypeScript | Notes |
|----------|--------|------------|-------|
| OpenAI | `openai_dispatch` | `openaiDispatch` | OpenAI Responses und chat-style flows |
| Anthropic | `anthropic_dispatch` | `anthropicDispatch` | Messages API, thinking budget validation |
| Gemini | `gemini_dispatch` | `geminiDispatch` | Google GenAI-compatible params |
| OpenRouter | `openrouter_dispatch` | `openrouterDispatch` | OpenAI-compatible |
| DeepInfra | `deepinfra_dispatch` | `deepinfraDispatch` | OpenAI-compatible |
| Novita | `novita_dispatch` | `novitaDispatch` | OpenAI-compatible |
| Together | `together_dispatch` | `togetherDispatch` | OpenAI-compatible |
| Sno GPU | `sno_gpu_dispatch` | `snoGpuDispatch` | On-prem OpenAI-compatible GPU endpoints |

Rust liefert derzeit die neutrale Pipeline plus feature-gated Helper für OpenAI, Anthropic, Gemini und Sno GPU. Behandle Rust-Provider-Helper als beta. Cache, Key Pool, Registry, Retry und Pipeline-Vertrag sind mit Python und TypeScript abgestimmt.

OpenAI-compatible Provider verwenden dieselbe OpenAI-Request-Form mit provider-spezifischem `base_url`-Handling. Das hält den Vertrag einfach. Einfach ist nützlich.

---

## Umgebungsvariablen

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI-Key oder kommaseparierter Key Pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic-Key oder kommaseparierter Key Pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini-Key oder kommaseparierter Key Pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter-Key oder kommaseparierter Key Pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra-Key oder kommaseparierter Key Pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together-Key oder kommaseparierter Key Pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita-Key oder kommaseparierter Key Pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Sno GPU Key-Pool-Variablen für Provider-ID `sno-gpu` |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files, batch metadata und kill-switch state |

`load_keys_from_env("provider-name")` prüft zuerst `PROVIDER_NAME_KEYS`, dann `PROVIDER_NAME_API_KEY`. Bindestriche werden zu Unterstrichen.

---

## Was es nicht ist

- Kein Streaming-Framework. Streaming bleibt bei deinem SDK.
- Kein Prompt-Framework. Bring deine eigene Prompt-Schicht mit.
- Kein Provider-Marketplace. Ein Aufruf verwendet den Provider aus der Config.
- Kein Grund, jede Modellentscheidung hinter Indirektion zu verstecken. Manche Dinge gehören in Code.

LLMix ist nützlich, wenn dieselbe Form von Modellaufruf in mehreren Services wieder auftaucht. Wenn du nur ein Skript und einen Key hast, brauchst du es wahrscheinlich noch nicht.

---

## Entwicklung

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
