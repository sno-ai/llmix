# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../../LICENSE)

Read in other languages: [English](../../../README.md) · [中文](README.zh-CN.md) · [Deutsch](README.de.md) · [Español](README.es.md) · **Français** · [Русский](README.ru.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md)

> Appels LLM pilotés par configuration pour Python, TypeScript et Rust.
> Gardez votre SDK. Déplacez le comportement du modèle dans des presets MDA. Placez cache, retries, rotation de clés et contrôle de rollout autour de l'appel.

LLMix est la couche entre votre produit et le SDK du fournisseur.

Il ne vous demande pas de réécrire votre code OpenAI, Anthropic, Gemini, LiteLLM, AI SDK ou vos clients maison. Il enveloppe l'appel. Les parties répétitives vont autour: cache de réponses, circuit breaker, key pools, singleflight, politique de retry, concurrence adaptative, kwargs fournisseur et chargement de configuration MDA.

Le modèle n'est plus une chaîne codée en dur au fond du code applicatif. Il devient une donnée. Modifiez un preset, publiez un compiled registry release de registry, rechargez le service, et la requête suivante peut utiliser un autre fournisseur ou un autre modèle. Pas besoin de redéployer pour le changement de modèle habituel.

C'est tout. Une petite couche. Les angles vifs sont limés.

---

## Pourquoi ce projet existe

Les produits AI en 2026 ne tombent généralement pas en panne parce qu'un appel SDK est difficile.

Ils tombent dans l'espace autour de cet appel. Une clé est rate-limited. Un fournisseur ralentit. Deux cents utilisateurs posent la même question en même temps. Un changement de modèle nécessite un déploiement. Une clé de cache diffère à cause d'un paramètre invisible. Un service est en Python, un autre en TypeScript, et le worker Rust doit suivre le même contrat.

LLMix sert cette partie du système. La chaîne de signal entre votre application et le modèle.

Vous gardez le prompt. Vous gardez le SDK. LLMix prend le harness.

---

## Installation

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python utilise `sno-llmix` sur PyPI parce que `llmix` était déjà pris. Le chemin d'import reste `llmix`.

Les helpers fournisseur utilisent des SDK optionnels. Installez seulement les clients fournisseur que vous appelez.

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

- [TypeScript guide](../llmix-typescript.md)
- [Python guide](../llmix-python.md)
- [Rust guide](../llmix-rust.md)
- [Configuration LLMix MDA sécurisée](../secure-mda/secure-llmix-configuration.fr.md)
- [Key pool operations](../key-pool-operations.md)

---

## Aperçu

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix enveloppe un appel fournisseur à la fois.

Ce n'est pas un router au sens LiteLLM. C'est plutôt le harness que vous finissez par reconstruire autour de chaque agent, coder tool, service d'extraction et workflow AI interne quand le trafic devient réel.

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

Rust expose le même contrat de pipeline. Le helper OpenAI est activé par feature.

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

Voir le [guide Rust](../llmix-rust.md) pour des exemples `main` complets et les feature flags.

---

## Ce que chaque appel reçoit autour de lui

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 mémoire plus Redis L2 optionnel, avec clés de cache canoniques entre runtimes |
| Key pools | Sélection round-robin, rotation sur 429 et éviction des clés mortes sur 401/403 |
| Retries | Backoff exponentiel avec jitter, en respectant `Retry-After` |
| Circuit breaker | Scopé par provider et effective base URL |
| Singleflight | Fusionne le travail concurrent identique en une seule requête upstream |
| Concurrency | Semaphore adaptatif AIMD, piloté par le feedback de rate limit |
| Provider kwargs | La common config devient des champs de requête propres au fournisseur |
| Thinking tokens | Extraction optionnelle de `<think>` dans des objets de réponse normalisés |
| Registry | Signed compiled config registry with one live `current.json` pointer |

Les valeurs par défaut sont faites pour être calmes. Ajustez-les quand le trafic réel donne une raison.

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

## Couverture des fournisseurs

Les dispatch helpers publics couvrent les fournisseurs que nous testons réellement.

| Provider | Python | TypeScript | Notes |
|----------|--------|------------|-------|
| OpenAI | `openai_dispatch` | `openaiDispatch` | OpenAI Responses et chat-style flows |
| Anthropic | `anthropic_dispatch` | `anthropicDispatch` | Messages API, thinking budget validation |
| Gemini | `gemini_dispatch` | `geminiDispatch` | Google GenAI-compatible params |
| OpenRouter | `openrouter_dispatch` | `openrouterDispatch` | OpenAI-compatible |
| DeepInfra | `deepinfra_dispatch` | `deepinfraDispatch` | OpenAI-compatible |
| Novita | `novita_dispatch` | `novitaDispatch` | OpenAI-compatible |
| Together | `together_dispatch` | `togetherDispatch` | OpenAI-compatible |
| Sno GPU | `sno_gpu_dispatch` | `snoGpuDispatch` | On-prem OpenAI-compatible GPU endpoints |

Rust livre aujourd'hui le pipeline neutre plus des helpers feature-gated pour OpenAI, Anthropic, Gemini et Sno GPU. Traitez les helpers fournisseur Rust comme beta. Cache, key pool, registry, retry et contrat de pipeline sont alignés avec Python et TypeScript.

Les fournisseurs OpenAI-compatible réutilisent la forme de requête OpenAI avec une gestion de `base_url` propre à chaque fournisseur. Le contrat reste simple. Simple est utile.

---

## Variables d'environnement

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | Clé OpenAI ou key pool séparé par virgules |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Clé Anthropic ou key pool séparé par virgules |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Clé Gemini ou key pool séparé par virgules |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | Clé OpenRouter ou key pool séparé par virgules |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | Clé DeepInfra ou key pool séparé par virgules |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Clé Together ou key pool séparé par virgules |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Clé Novita ou key pool séparé par virgules |
| `SNO_LLM_API_KEY` | Fallback du dispatcher direct Sno GPU |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | Variables de key pool Sno GPU pour provider id `sno-gpu` |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | URL Redis response-cache |
| `LLMIX_STATE_DIR` | Lock files, batch metadata et kill-switch state |

`load_keys_from_env("provider-name")` vérifie d'abord `PROVIDER_NAME_KEYS`, puis `PROVIDER_NAME_API_KEY`. Les tirets deviennent des underscores.

---

## Ce que ce n'est pas

- Pas un framework de streaming. Le streaming reste dans votre SDK.
- Pas un framework de prompt. Apportez votre propre couche de prompt.
- Pas une marketplace de fournisseurs. Un appel utilise le provider nommé par sa config.
- Pas une raison de cacher chaque décision de modèle derrière une indirection. Certaines choses doivent rester dans le code.

LLMix est utile quand la même forme d'appel modèle revient dans plusieurs services. Si vous avez un script et une clé, vous n'en avez probablement pas encore besoin.

---

## Développement

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
