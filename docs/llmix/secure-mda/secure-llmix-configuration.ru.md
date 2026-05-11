# Безопасная конфигурация LLMix с MDA

Языки: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix может загружать model presets из подписанных MDA-файлов и публиковать их как signed registry. Так поведение модели можно вынести из кода приложения, не позволяя downstream-пользователям тихо его менять.

Главное правило простое:

Registry может поставляться вместе с app, но trust anchor должен находиться вне registry.

Если атакующий может заменить `config/llm/`, он может заменить любой файл внутри. Поэтому runtime не должен доверять только `config/llm/`. Он должен получать извне `expectedRootDigest`, trust policy, signer identity и правила freshness/rollback.

## Quick Start

Используйте MDA CLI 1.1.x или новее. Поток ниже проверен с `mda --version` = `1.1.2`.

1. Пишите LLMix presets как source `.mda` files.
2. В CI или release automation выполняйте validation, добавление integrity и подпись.
3. LLMix publisher проверяет эти `.mda` через trusted runtime и публикует signed registry.
4. MDA CLI формирует внешний deployment trust manifest.
5. Runtime открывает registry с этим внешним manifest.

```bash
mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out authoring/search_summary/openai_fast.mda

mda validate authoring/search_summary/openai_fast.mda --target source --json
mda integrity compute authoring/search_summary/openai_fast.mda --target source --write --json
```

Подпишите preset тем signer, который используется в вашем release process. Самый простой локальный пример — did:web:

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Сгенерируйте source policy и registry-root policy, подготовьте release plan, опубликуйте LLMix registry, затем finalize внешний manifest:

```bash
mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/source-policy.json \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/root-policy.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --policy release/source-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json

# Здесь запустите LLMix publisher с enabled trustedRuntime.
# Он читает authoring/, проверяет каждый signed .mda, пишет config/llm/
# и подписывает config/llm/snapshots/<revision>/registry-root.json.

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/snapshots/<revision>/registry-root.json \
  --release-plan release/plan.json \
  --policy release/root-policy.json \
  --derive-root-digest \
  --minimum-revision <revision> \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json
```

`deploy/llmix-trust.json` — это внешний anchor. Не храните его внутри `config/llm/`.

## Что защищается

| Ситуация | Ожидаемый результат |
| --- | --- |
| Signed `.mda` files корректны, и registry root совпадает с внешним trust manifest. | LLMix загружает preset. |
| Preset, manifest, `current.json` или registry root изменили после публикации. | Runtime отклоняет registry. |
| Весь `config/llm/` заменили другой внутренне согласованной registry. | Runtime все равно отклоняет ее, потому что `expectedRootDigest`, signer policy и freshness rules приходят извне. |

Для rollback protection используйте `minimumRevision`, `minimumPublishedAt` или high-watermark value при finalize и runtime open.

## Files

Рекомендуемая структура:

```text
authoring/
  search_summary/
    openai_fast.mda
    openrouter_balanced.mda

config/llm/
  current.json
  snapshots/
    <revision>/
      manifest.json
      registry-root.json
      search_summary/
        openai_fast.json
        openrouter_balanced.json

deploy/
  llmix-trust.json
```

`authoring/` содержит source `.mda` files, которые редактируют люди. `config/llm/` содержит опубликованную LLMix registry и может ship вместе с app. `deploy/llmix-trust.json` должен приходить через отдельный deployment channel: application config, secret/config manager, Kubernetes config, constant, baked в app, или release attestation.

`registry-root.json` — это evidence. Внешний trust manifest — это anchor.

## Как писать presets

MDA mechanism fields находятся на верхнем уровне. Настройки LLMix находятся в `metadata.snoai-llmix`.

```markdown
---
name: openai_fast
description: Fast OpenAI preset for search summaries.
requires:
  network: ["api.openai.com"]
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 1024
integrity:
  algorithm: sha256
  digest: "sha256:..."
signatures:
  - signer: "did-web:config.example.com"
    key-id: "did:web:config.example.com#release"
    payload-digest: "sha256:..."
    algorithm: ed25519
    signature: "..."
    payload-type: "application/vnd.snoai-llmix.preset+json"
---

# Optional notes for humans
```

Используйте registry-safe names для modules и presets. Самый безопасный вариант: lowercase letters, numbers, `_` и `-`. Provider API keys, tenant secrets и environment-specific credentials не должны попадать в `.mda`; храните их в runtime environment или secret manager.

Полная форма provider config описана в [LLMix usage reference](../llmix-usage-ref.md).

## Publisher contract

При публикации production registry publisher должен:

1. Загружать source `.mda` files с `trustedRuntime: true`.
2. Применять source trust policy и required network policy.
3. Писать immutable resolved JSON snapshots.
4. Писать `current.json` для active revision.
5. Писать и подписывать `registry-root.json` для всей registry revision.

Registry root покрывает active pointer, snapshot manifest, resolved config files, source digests, release revision и publication time. Поэтому partial edit обнаруживается. Full replacement обнаруживается через внешний `expectedRootDigest` и trust policy.

## Runtime

Runtime открывает `config/llm/` с options `signedRoot`, полученными из внешнего trust manifest.

TypeScript:

```ts
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const manifest = await loadLlmixTrustManifest("/etc/llmix/llmix-trust.json");

const registry = await ConfigRegistryManager.open("./config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(manifest, {
    didWebVerifier,
    rekorClient,
    sigstoreVerifier,
    highWatermark,
  }),
});

const preset = await registry.getPreset("search_summary", "openai_fast");
```

Python:

```python
from llmix import (
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
    registry_root_options_from_trust_manifest,
)

manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")

registry = ConfigRegistryManager.open(
    "./config/llm",
    ConfigRegistryOpenOptions(
        signed_root=registry_root_options_from_trust_manifest(
            manifest,
            did_web_verifier=did_web_verifier,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
            high_watermark=high_watermark,
        )
    ),
)

preset = registry.get_preset("search_summary", "openai_fast")
```

Rust:

```rust
use llmix_rs::{
    registry_root_options_from_trust_manifest,
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
};

let manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")?;
let signed_root = registry_root_options_from_trust_manifest(&manifest)?;

let registry = ConfigRegistryManager::open_with_options(
    "./config/llm",
    ConfigRegistryOpenOptions {
        signed_root: Some(signed_root),
    },
)?;

let preset = registry.get_preset("search_summary", "openai_fast")?;
```

Если policy доверяет did:web, предоставьте did:web verifier. Если policy доверяет GitHub Actions/Sigstore, предоставьте Sigstore и Rekor verifier.

## Как выбрать anchor

Выберите самый простой anchor, который подходит вашему deployment.

| Anchor | Лучше всего подходит | Notes |
| --- | --- | --- |
| External trust manifest file | Большинство services | Создается `mda release finalize` и хранится вне `config/llm/`. Самый простой default. |
| App constant или build-time config | CLI, desktop, embedded app | `expectedRootDigest` и policy pin в app. Для новой registry нужно обновить app или build config. |
| Deployment config или secret manager | Server deployments | Разместите в Kubernetes config, cloud config, Secret Manager, SSM, Vault или аналоге. |
| GitHub Actions OIDC + Rekor | Обычный CI release flow | Хорошо, когда releases идут из repo workflow. Policy pin repo, workflow, ref, issuer и Rekor. |
| did:web, KMS или HSM | Organization-controlled signing | Подходит, если у организации уже есть web identity или key management. |

MDA CLI может генерировать policies, validate sources, verify signatures, prepare release plans, finalize trust manifests и выдавать deployment snippets. Но она не заменяет финальную trust boundary runtime. Runtime trust все равно приходит из внешнего anchor, который вы передаете LLMix.

## Deployment snippets

После появления `deploy/llmix-trust.json` CLI может создать deployment snippets из того же manifest:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Поддерживаются `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python` и `rust`.

## Troubleshooting

| Проблема | Что проверить |
| --- | --- |
| Корректная registry не открывается с digest error. | Убедитесь, что `expectedRootDigest` — SHA-256 bytes файла `registry-root.json`, а не только внутреннего payload. Запустите `mda release finalize --derive-root-digest` заново. |
| Runtime говорит, что trusted signature отсутствует. | Signature может проходить cryptographic verify, но не совпадать с `trustedSigners`. Проверьте signer type, domain, issuer, subject, workflow и ref. |
| did:web verification fails. | Убедитесь, что runtime did:web verifier resolves тот же DID document, который использовался при release, и что `key-id` существует. |
| Sigstore verification fails. | Проверьте Rekor policy, issuer, subject, workflow/ref binding, а также наличие Rekor client и Sigstore verifier в runtime. |
| Измененный файл все еще загружается. | Убедитесь, что app открывает registry с options `signedRoot`. Без signed root verification это только parsing registry. |
| Полностью замененная registry загружается. | Trust manifest, вероятно, читается из `config/llm/` или из того же замененного package. Переместите его вне registry. |
| Старая signed registry снова становится active. | Используйте `minimumRevision`, `minimumPublishedAt` или high-watermark при finalize и runtime open. |

## Связанные документы

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
