# MDA के साथ सुरक्षित LLMix configuration

भाषाएं: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix signed MDA files से model presets load कर सकता है और उनसे signed registry publish कर सकता है। इससे model behavior application code से बाहर आ जाता है, लेकिन downstream users उसे चुपचाप बदल नहीं पाते।

सबसे जरूरी नियम सरल है:

Registry app के साथ ship हो सकती है, लेकिन trust anchor registry के बाहर होना चाहिए।

अगर attacker `config/llm/` बदल सकता है, तो वह उसके अंदर की हर file बदल सकता है। इसलिए runtime को केवल `config/llm/` पर भरोसा नहीं करना चाहिए। उसे बाहर से `expectedRootDigest`, trust policy, signer identity और freshness/rollback rules लेने चाहिए।

## Quick Start

MDA CLI 1.1.x या उससे नया version इस्तेमाल करें। नीचे का flow `mda --version` = `1.1.2` के साथ check किया गया है।

1. LLMix presets को source `.mda` files के रूप में लिखें।
2. CI या release process में validate करें, integrity जोड़ें और sign करें।
3. LLMix publisher trusted runtime से इन `.mda` files को verify करके signed registry publish करे।
4. MDA CLI external deployment trust manifest finalize करे।
5. Runtime उस external manifest से registry खोले।

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

अपने release process में इस्तेमाल होने वाले signer से sign करें। सबसे सरल local example did:web है:

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Source policy और registry-root policy बनाएं, release plan तैयार करें, LLMix registry publish करें, फिर external manifest finalize करें:

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

# यहां LLMix publisher को trustedRuntime enabled करके चलाएं।
# यह authoring/ पढ़ता है, हर signed .mda verify करता है, config/llm/ लिखता है,
# और config/llm/snapshots/<revision>/registry-root.json sign करता है।

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

`deploy/llmix-trust.json` external anchor है। इसे `config/llm/` के अंदर न रखें।

## यह क्या protect करता है

| स्थिति | अपेक्षित परिणाम |
| --- | --- |
| Signed `.mda` files सही हैं और registry root external trust manifest से match करता है। | LLMix preset load करता है। |
| Publish के बाद preset, manifest, `current.json` या registry root बदल दिया गया। | Runtime registry reject करता है। |
| कोई पूरा `config/llm/` किसी दूसरी internally consistent registry से बदल देता है। | Runtime फिर भी reject करता है, क्योंकि `expectedRootDigest`, signer policy और freshness rules बाहर से आते हैं। |

Rollback protection के लिए finalize और runtime open करते समय `minimumRevision`, `minimumPublishedAt` या high-watermark value इस्तेमाल करें।

## Files

Recommended layout:

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

`authoring/` में human-edited source `.mda` files रहती हैं। `config/llm/` में published LLMix registry रहती है और यह app के साथ ship हो सकती है। `deploy/llmix-trust.json` किसी अलग deployment channel से आना चाहिए, जैसे application config, secret/config manager, Kubernetes config, app में baked constant या release attestation।

`registry-root.json` evidence है। External trust manifest anchor है।

## Presets लिखना

MDA mechanism fields top level पर रखें। LLMix settings `metadata.snoai-llmix` में रखें।

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

Modules और presets के लिए registry-safe names इस्तेमाल करें। Lowercase letters, numbers, `_` और `-` सबसे सुरक्षित हैं। Provider API keys, tenant secrets और environment-specific credentials को `.mda` में न रखें; उन्हें runtime environment या secret manager में रखें।

पूरे provider config shape के लिए [LLMix usage reference](../llmix-usage-ref.md) देखें।

## Publisher contract

Production registry publish करते समय publisher को:

1. Source `.mda` files को `trustedRuntime: true` के साथ load करना चाहिए।
2. Source trust policy और required network policy enforce करनी चाहिए।
3. Immutable resolved JSON snapshots लिखने चाहिए।
4. Active revision के लिए `current.json` लिखना चाहिए।
5. पूरी registry revision के लिए `registry-root.json` लिखकर sign करना चाहिए।

Registry root active pointer, snapshot manifest, resolved config files, source digests, release revision और publication time cover करता है। इससे partial edit पकड़ा जाता है। Full replacement external `expectedRootDigest` और trust policy से पकड़ा जाता है।

## Runtime

Runtime external trust manifest से बने `signedRoot` options के साथ `config/llm/` खोलता है।

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

अगर policy did:web पर trust करती है, तो did:web verifier दें। अगर policy GitHub Actions/Sigstore पर trust करती है, तो Sigstore और Rekor verifier दें।

## Anchor कैसे चुनें

अपने deployment के लिए सबसे सरल anchor चुनें।

| Anchor | कब उपयोगी | Notes |
| --- | --- | --- |
| External trust manifest file | अधिकतर services | `mda release finalize` से बनता है और `config/llm/` के बाहर रहता है। सबसे सरल default। |
| App constant या build-time config | CLI, desktop, embedded app | `expectedRootDigest` और policy app में pin होते हैं। नई registry के लिए app या build config update करना होगा। |
| Deployment config या secret manager | Server deployments | Kubernetes config, cloud config, Secret Manager, SSM, Vault आदि में रखें। |
| GitHub Actions OIDC + Rekor | Common CI release | जब releases repo workflow से आते हैं। Policy repo, workflow, ref, issuer और Rekor pin करती है। |
| did:web, KMS या HSM | Organization-controlled signing | जब organization के पास web identity या key management पहले से हो। |

MDA CLI policies बना सकती है, sources validate कर सकती है, signatures verify कर सकती है, release plans तैयार कर सकती है, trust manifests finalize कर सकती है और deployment snippets निकाल सकती है। लेकिन यह runtime की final trust boundary नहीं बनती। Runtime trust अभी भी उस external anchor से आता है जिसे आप LLMix को देते हैं।

## Deployment snippets

`deploy/llmix-trust.json` बनने के बाद CLI उसी manifest से deployment snippets बना सकती है:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Supported formats: `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python`, और `rust`.

## Troubleshooting

| समस्या | क्या check करें |
| --- | --- |
| सही registry digest error के साथ नहीं खुलती। | Confirm करें कि `expectedRootDigest` `registry-root.json` file bytes का SHA-256 है, केवल inner payload digest नहीं। `mda release finalize --derive-root-digest` दोबारा चलाएं। |
| Runtime कहता है trusted signature नहीं है। | Signature cryptographically verify हो सकती है, पर `trustedSigners` से match नहीं करती। Signer type, domain, issuer, subject, workflow और ref check करें। |
| did:web verification fail होती है। | Runtime did:web verifier वही DID document resolve करे जो release में इस्तेमाल हुआ था, और `key-id` उसमें मौजूद हो। |
| Sigstore verification fail होती है। | Rekor policy, issuer, subject, workflow/ref binding और runtime में Rekor client/Sigstore verifier check करें। |
| बदली हुई file फिर भी load होती दिखती है। | App registry को `signedRoot` options के साथ open कर रही है यह check करें। Signed root verification के बिना यह सिर्फ registry parse कर रही है। |
| पूरी replaced registry load हो जाती है। | Trust manifest शायद `config/llm/` या उसी replaced package से पढ़ा जा रहा है। उसे registry के बाहर रखें। |
| पुरानी signed registry फिर active हो जाती है। | Release finalize और runtime open में `minimumRevision`, `minimumPublishedAt` या high-watermark इस्तेमाल करें। |

## Related docs

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
