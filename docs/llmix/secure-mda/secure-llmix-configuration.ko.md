# LLMix MDA 설정을 안전하게 사용하기

언어: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix는 서명된 MDA 파일에서 model preset을 load하고, 이를 signed registry로 publish할 수 있습니다. 이렇게 하면 model behavior를 application code 밖으로 옮기면서도 downstream 사용자가 조용히 바꾸는 일을 막을 수 있습니다.

가장 중요한 규칙은 단순합니다.

Registry는 app과 함께 ship할 수 있지만, trust anchor는 registry 밖에 있어야 합니다.

공격자가 `config/llm/`을 바꿀 수 있다면 그 안의 모든 파일도 바꿀 수 있습니다. 그래서 runtime은 `config/llm/`만 믿으면 안 됩니다. 외부에서 `expectedRootDigest`, trust policy, signer identity, freshness/rollback rules를 받아야 합니다.

## Quick Start

MDA CLI 1.1.x 이상을 사용하세요. 아래 흐름은 `mda --version` = `1.1.2`로 확인했습니다.

1. LLMix preset을 source `.mda` file로 작성합니다.
2. CI 또는 release process에서 validate, integrity 추가, sign을 수행합니다.
3. LLMix publisher가 trusted runtime으로 이 `.mda` file들을 verify하고 signed registry를 publish합니다.
4. MDA CLI가 external deployment trust manifest를 finalize합니다.
5. Runtime은 이 external manifest로 registry를 엽니다.

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

Release process에서 사용하는 signer로 sign합니다. 가장 단순한 local example은 did:web입니다.

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Source policy와 registry-root policy를 만들고, release plan을 준비하고, LLMix registry를 publish한 뒤 external manifest를 finalize합니다.

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

# 여기서 trustedRuntime을 켠 LLMix publisher를 실행합니다.
# authoring/을 읽고, 모든 signed .mda를 verify하고, config/llm/을 쓰고,
# config/llm/snapshots/<revision>/registry-root.json에 sign합니다.

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

`deploy/llmix-trust.json`이 external anchor입니다. 이 파일을 `config/llm/` 안에 저장하지 마세요.

## 무엇을 보호하나

| 상황 | 예상 결과 |
| --- | --- |
| Signed `.mda` files가 유효하고 registry root가 external trust manifest와 일치합니다. | LLMix가 preset을 load합니다. |
| Publish 이후 preset, manifest, `current.json`, registry root가 수정됩니다. | Runtime이 registry를 reject합니다. |
| 누군가 `config/llm/` 전체를 내부적으로 일관된 다른 registry로 바꿉니다. | `expectedRootDigest`, signer policy, freshness rules가 외부에서 오기 때문에 runtime이 여전히 reject합니다. |

Rollback protection이 필요하면 finalize와 runtime open에서 `minimumRevision`, `minimumPublishedAt` 또는 high-watermark 값을 사용하세요.

## Files

권장 구조:

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

`authoring/`에는 사람이 편집하는 source `.mda` files가 있습니다. `config/llm/`에는 published LLMix registry가 있으며 app과 함께 ship할 수 있습니다. `deploy/llmix-trust.json`은 별도의 deployment channel에서 제공해야 합니다. 예: application config, secret/config manager, Kubernetes config, app에 baked된 constant, release attestation.

`registry-root.json`은 evidence입니다. External trust manifest가 anchor입니다.

## Preset 작성

MDA mechanism fields는 top level에 둡니다. LLMix settings는 `metadata.snoai-llmix` 아래에 둡니다.

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

Module과 preset 이름은 registry-safe name을 사용하세요. Lowercase letters, numbers, `_`, `-`가 가장 안전합니다. Provider API keys, tenant secrets, environment-specific credentials는 `.mda`에 넣지 말고 runtime environment 또는 secret manager에 저장하세요.

전체 provider config shape는 [LLMix usage reference](../llmix-usage-ref.md)를 보세요.

## Publisher contract

Production registry를 publish할 때 publisher는 다음을 해야 합니다.

1. Source `.mda` files를 `trustedRuntime: true`로 load합니다.
2. Source trust policy와 required network policy를 enforce합니다.
3. Immutable resolved JSON snapshots를 씁니다.
4. Active revision의 `current.json`을 씁니다.
5. 전체 registry revision에 대한 `registry-root.json`을 쓰고 sign합니다.

Registry root는 active pointer, snapshot manifest, resolved config files, source digests, release revision, publication time을 cover합니다. 따라서 partial edit가 감지됩니다. Full replacement는 external `expectedRootDigest`와 trust policy로 감지됩니다.

## Runtime

Runtime은 external trust manifest에서 만든 `signedRoot` options로 `config/llm/`을 엽니다.

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

Policy가 did:web을 trust하면 did:web verifier를 제공해야 합니다. Policy가 GitHub Actions/Sigstore를 trust하면 Sigstore와 Rekor verifier를 제공해야 합니다.

## Anchor 선택

Deployment 방식에 맞는 가장 단순한 anchor를 선택하세요.

| Anchor | 적합한 경우 | Notes |
| --- | --- | --- |
| External trust manifest file | 대부분의 services | `mda release finalize`가 생성하고 `config/llm/` 밖에 저장합니다. 가장 쉬운 default입니다. |
| App constant 또는 build-time config | CLI, desktop, embedded app | `expectedRootDigest`와 policy를 app에 pin합니다. 새 registry를 받으려면 app 또는 build config를 update해야 합니다. |
| Deployment config 또는 secret manager | Server deployments | Kubernetes config, cloud config, Secret Manager, SSM, Vault 등에 둡니다. |
| GitHub Actions OIDC + Rekor | 일반적인 CI release | Repo workflow에서 release할 때 적합합니다. Policy가 repo, workflow, ref, issuer, Rekor를 pin합니다. |
| did:web, KMS, HSM | Organization-controlled signing | 조직이 이미 web identity 또는 key management를 갖고 있을 때 좋습니다. |

MDA CLI는 policies 생성, sources validate, signatures verify, release plans 준비, trust manifests finalize, deployment snippets 출력을 도울 수 있습니다. 하지만 runtime의 최종 trust boundary를 대체하지는 않습니다. Runtime trust는 LLMix에 전달하는 external anchor에서 옵니다.

## Deployment snippets

`deploy/llmix-trust.json`이 있으면 CLI가 같은 manifest에서 deployment snippets를 만들 수 있습니다.

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Supported formats: `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python`, `rust`.

## Troubleshooting

| 문제 | 확인할 것 |
| --- | --- |
| 올바른 registry가 digest error로 열리지 않습니다. | `expectedRootDigest`가 inner payload만이 아니라 `registry-root.json` file bytes의 SHA-256인지 확인하세요. `mda release finalize --derive-root-digest`를 다시 실행하세요. |
| Runtime이 trusted signature가 없다고 합니다. | Signature가 cryptographic verify는 통과하지만 `trustedSigners`와 맞지 않을 수 있습니다. Signer type, domain, issuer, subject, workflow, ref를 확인하세요. |
| did:web verification이 실패합니다. | Runtime did:web verifier가 release 때 사용한 것과 같은 DID document를 resolve하고, 그 안에 `key-id`가 있는지 확인하세요. |
| Sigstore verification이 실패합니다. | Rekor policy, issuer, subject, workflow/ref binding, runtime의 Rekor client와 Sigstore verifier를 확인하세요. |
| Tampered file이 여전히 load되는 것 같습니다. | App이 registry를 `signedRoot` options로 여는지 확인하세요. Signed root verification 없이 load하면 registry를 parse하는 것뿐입니다. |
| 전체 replaced registry가 load됩니다. | Trust manifest가 `config/llm/` 안이나 replaced package 안에서 읽히는 것일 수 있습니다. Registry 밖으로 옮기세요. |
| 오래된 signed registry가 다시 active됩니다. | Release finalize와 runtime open에서 `minimumRevision`, `minimumPublishedAt` 또는 high-watermark를 사용하세요. |

## Related docs

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
