# Secure LLMix Configuration with MDA

Languages: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix can load model presets from signed MDA files and publish them as a signed
registry. That lets you move model behavior out of application code without
letting downstream users silently edit it.

The important rule is simple:

The registry can ship with the app, but the trust anchor must live outside the
registry.

If an attacker can replace `config/llm/`, they can replace every file inside it.
So the runtime must pin something outside that directory: the expected registry
root digest, the trust policy, signer identity, and freshness rules.

## Quick Start

Use the current MDA CLI 1.1.x or newer. These commands were checked with
`mda --version` returning `1.1.2`.

1. Write LLMix presets as source `.mda` files.
2. Validate and sign those files in CI or release automation.
3. Publish a signed LLMix registry from the verified presets.
4. Finalize an external deployment trust manifest.
5. At runtime, open the registry with that manifest.

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

Sign the preset with the signer your release process uses. A did:web key is the
simplest local example:

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Generate the source and registry-root trust policies, prepare the release plan,
publish the LLMix registry, then finalize the external manifest:

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

# Run the LLMix publisher here with trustedRuntime enabled.
# It reads authoring/, verifies every signed .mda, writes config/llm/, and
# signs config/llm/snapshots/<revision>/registry-root.json.

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

`deploy/llmix-trust.json` is the external anchor. Do not store it under
`config/llm/`.

## What This Protects

This setup is designed for three real cases:

| Case | Expected result |
| --- | --- |
| Signed `.mda` files are valid and the registry root matches the external trust manifest. | LLMix loads the preset. |
| A preset, manifest, `current.json`, or registry root is edited after publish. | Runtime rejects the registry. |
| Someone replaces the whole `config/llm/` directory with another internally consistent registry. | Runtime still rejects it because `expectedRootDigest`, signer policy, and freshness rules come from outside that directory. |

It also supports rollback protection. Use `minimumRevision`,
`minimumPublishedAt`, or a high-watermark value when an older valid release must
not become active again.

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

`authoring/` contains human-edited source `.mda` files. `config/llm/` contains
the published LLMix registry and may ship with the app. `deploy/llmix-trust.json`
must come from a separate deployment channel such as application config, a
secret/config manager, Kubernetes config, a baked app constant, or release
attestation.

The signed `registry-root.json` is evidence. The external trust manifest is the
anchor.

## Author Presets

Put MDA mechanism fields at the top level. Put LLMix settings under
`metadata.snoai-llmix`.

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

Use registry-safe names for modules and presets. Lowercase letters, numbers,
`_`, and `-` are the safest choice. Keep provider API keys, tenant secrets, and
environment-specific credentials out of `.mda`; store those in the runtime
environment or secret manager.

For the full provider config shape, see
[LLMix usage reference](../llmix-usage-ref.md).

## Publisher Contract

When publishing a production registry, the publisher should:

1. Load source `.mda` files with `trustedRuntime: true`.
2. Enforce the source trust policy and required network policy.
3. Write immutable resolved JSON snapshots.
4. Write `current.json` for the active revision.
5. Write and sign `registry-root.json` for the whole registry revision.

The registry root covers the active pointer, snapshot manifest, resolved config
files, source digests, release revision, and publication time. A partial edit is
therefore detected. A full replacement is detected by the external
`expectedRootDigest` and trust policy.

## Runtime

The runtime opens `config/llm/` with `signedRoot` options derived from the
external trust manifest.

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

In all runtimes, the app must provide the verifier hooks needed by its policy.
If the policy trusts did:web, provide a did:web verifier. If the policy trusts
Sigstore/GitHub Actions, provide Sigstore and Rekor verification.

## Anchor Choices

Choose the simplest anchor that matches how you deploy.

| Anchor | Best fit | Notes |
| --- | --- | --- |
| External trust manifest file | Most services | Generated by `mda release finalize`; stored outside `config/llm/`; easiest default. |
| App constant or baked config | Static desktop, CLI, embedded app | Pin `expectedRootDigest` and policy at build time. Update the app to accept a new registry. |
| Deployment config or secret manager | Server deployments | Put `llmix-trust.json` in Kubernetes config, cloud config, Secret Manager, SSM, Vault, or similar. |
| GitHub Actions OIDC plus Rekor | Common CI release flow | Good default when releases come from a repo workflow. The policy pins repo, workflow, ref, issuer, and Rekor. |
| did:web, KMS, or HSM | Organization-controlled signing | Best when your org already owns web identity or key management. |

The MDA CLI can generate policies, validate sources, verify signatures, prepare
release plans, finalize trust manifests, and emit deployment snippets. It should
not be the final trust boundary at runtime. Runtime trust still comes from the
external anchor you pass to LLMix.

## Snippets For Deployment

After `deploy/llmix-trust.json` exists, the CLI can generate deployment-specific
snippets from the same manifest:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Supported snippet formats include `json`, `env`, `kubernetes`,
`github-actions`, `terraform`, `typescript`, `python`, and `rust`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| A valid registry fails to open with a digest error. | Confirm `expectedRootDigest` is the SHA-256 of the `registry-root.json` artifact bytes, not only the inner payload digest. Re-run `mda release finalize --derive-root-digest`. |
| Runtime says no trusted signature exists. | The signature may verify cryptographically but not match `trustedSigners`. Check signer type, domain, issuer, subject, workflow, and ref. |
| did:web verification fails. | Make sure the runtime did:web verifier resolves the same DID document used during release, and that `key-id` exists in that document. |
| Sigstore verification fails. | Check Rekor policy, issuer, subject, workflow/ref binding, and whether the runtime has a Rekor client and Sigstore verifier. |
| A tampered file still appears to load. | Make sure the app opens the registry with `signedRoot` options. Loading without signed root verification is only parsing the registry. |
| A whole replaced registry loads. | The trust manifest is probably being loaded from inside `config/llm/` or from the replaced package. Move it outside the registry. |
| An old signed registry loads. | Set `minimumRevision`, `minimumPublishedAt`, or a high-watermark value during release finalize and runtime open. |

## Related Docs

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [中文：安全使用 LLMix MDA 配置](./secure-llmix-configuration.zh.md)
