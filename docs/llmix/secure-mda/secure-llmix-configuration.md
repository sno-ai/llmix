# Secure LLMix Configuration with MDA

Languages: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

This is the official secure LLMix registry flow.

Roles are fixed:

| Role | Responsibility |
| --- | --- |
| MDA | The preset source standard. |
| MDA CLI | Validation, integrity, signing, verification, release prepare, release finalize, and doctor checks. |
| LLMix | The official registry publisher API and runtime registry loader. |
| App repository | Owns source presets, release wiring, runtime verifier hooks, and provider credentials. |

Do not write a custom compiler. Do not invent another directory structure. Use
MDA CLI plus LLMix.

## Layout

Use this layout in the app repository:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

Meanings:

| Path | Owner | Meaning |
| --- | --- | --- |
| `config/llm/source/` | People | Human-edited MDA preset sources. |
| `config/llm/current.json` | LLMix | Machine-generated active registry pointer. |
| `config/llm/compiled/` | LLMix | Machine-generated signed and resolved registry output. |
| Trust anchor | App/deployment | Stored outside `config/llm`. |

The trust anchor must never be loaded from `config/llm`. If an attacker can
replace `config/llm`, every file inside that directory is untrusted until the
runtime checks it against an external anchor.

## Required Flow

1. Put source presets in `config/llm/source/<module>/<preset>.mda`.
2. Run MDA CLI validation, integrity, signing, verification, and release
   prepare.
3. Run the official LLMix publisher API.
4. Generate `config/llm/current.json` and `config/llm/compiled/`.
5. Run MDA CLI release finalize and doctor checks.
6. Store the trust anchor outside `config/llm`.
7. Open `config/llm` at runtime through LLMix with the external trust anchor.

## Source Preset

Create one preset:

```bash
mkdir -p config/llm/source/search_summary release deploy

mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out config/llm/source/search_summary/openai_fast.mda
```

The source path is part of the public contract:

```text
config/llm/source/search_summary/openai_fast.mda
```

Use lowercase letters, numbers, `_`, and `-` for module and preset names.

## MDA CLI Gate

Validate, compute integrity, sign, and verify the source preset before
publishing:

```bash
mda validate config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --json

mda integrity compute config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --write \
  --json

mda sign config/llm/source/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/trust-policy.json \
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
```

Use GitHub Actions Sigstore/Rekor policy instead of did:web if that is your
release identity. The layout and LLMix publisher API do not change.

## LLMix Publisher

Use the official LLMix publisher API in a release script. It reads
`config/llm/source`, verifies source presets with the trust options you pass,
writes `config/llm/compiled/<revision>`, writes `config/llm/current.json`, and
signs `config/llm/compiled/<revision>/registry-root.json`.

```typescript
import {
  ConfigRegistryPublisher,
  type RegistryRootSigner,
} from "@snoai/llmix";

import sourceTrustPolicy from "../release/trust-policy.json" with { type: "json" };

const registryRootSigner: RegistryRootSigner = async ({
  canonicalPayload,
  integrity,
  payloadType,
}) => {
  const signature = await signWithReleaseKey(canonicalPayload, payloadType);

  return {
    signer: "did-web:config.example.com",
    "key-id": "did:web:config.example.com#release",
    algorithm: "ed25519",
    "payload-type": payloadType,
    "payload-digest": integrity.digest,
    signature,
  };
};

const published = await new ConfigRegistryPublisher("config/llm").publish({
  revision: process.env.RELEASE_REVISION,
  trustedRuntime: true,
  trustPolicy: sourceTrustPolicy,
  didWebVerifier,
  registryRoot: { signer: registryRootSigner },
});

console.log(JSON.stringify({
  revision: published.revision,
  registryRootPath: published.registryRootPath,
  registryRootSha256: published.registryRootSha256,
}));
```

`signWithReleaseKey` and `didWebVerifier` are release-system adapters. They
should use the same signing identity and verifier policy that the MDA CLI
commands use. Do not replace this publisher with a project-local compiler.

After publishing, the generated registry has this shape:

```text
config/llm/
  source/
    search_summary/
      openai_fast.mda
  current.json
  compiled/
    <revision>/
      manifest.json
      registry-root.json
      source/
        search_summary/
          openai_fast.mda
      resolved/
        search_summary/
          openai_fast.json
```

## Finalize Release

Finalize the external trust anchor after the LLMix publisher writes the signed
registry root:

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/compiled/<revision>/registry-root.json \
  --release-plan release/plan.json \
  --policy release/trust-policy.json \
  --derive-root-digest \
  --minimum-revision <revision> \
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
```

`deploy/llmix-trust.json` is the external trust anchor. Store it outside
`config/llm`.

## Trust Anchor Delivery

Deliver the trust anchor through one of these channels:

| Channel | Use when |
| --- | --- |
| Environment variable | The app can receive a path or JSON blob at process start. |
| Application config | The deployment system already manages config files outside the registry. |
| Build-time constant | The registry is pinned by a rebuilt app or CLI. |
| Secret/config manager | The platform owns trusted runtime configuration. |
| Kubernetes or cloud config | The app is deployed through cluster or cloud configuration. |
| Release attestation | The deployment records the approved release digest and policy. |

The app must not read `deploy/llmix-trust.json` from inside `config/llm`.

## Runtime

Runtime inputs are explicit:

| Input | Example |
| --- | --- |
| Registry directory | `config/llm` |
| Trust anchor | `/etc/llmix/llmix-trust.json` or `process.env.LLMIX_TRUST_ANCHOR` |
| Verifier hooks | did:web, Rekor, Sigstore, or the verifier required by the policy |
| Preset id | `search_summary/openai_fast` |

Open the registry through LLMix:

```typescript
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const trust = await loadLlmixTrustManifest(process.env.LLMIX_TRUST_ANCHOR!);

const registry = await ConfigRegistryManager.open("config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(trust, {
    didWebVerifier,
    rekorClient,
    sigstoreVerifier,
  }),
});

const preset = await registry.getPreset("search_summary", "openai_fast");
console.log({
  activeRevision: registry.activeRevision,
  provider: preset.provider,
  model: preset.model,
});
```

For production services, always pass `signedRoot`. Opening without `signedRoot`
only parses the registry and is not a secure runtime check.

## Runtime Tamper Proof

Every downstream app should keep a runtime proof test. The test must:

1. Open `config/llm` through LLMix with `signedRoot`.
2. Load one expected preset.
3. Modify registry content in a temporary copy.
4. Confirm LLMix rejects the modified registry while the external trust anchor
   still pins the trusted release.

Example:

```typescript
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const trust = await loadLlmixTrustManifest(process.env.LLMIX_TRUST_ANCHOR!);
const signedRoot = registryRootOptionsFromTrustManifest(trust, { didWebVerifier });

const registry = await ConfigRegistryManager.open("config/llm", { signedRoot });
const preset = await registry.getPreset("search_summary", "openai_fast");
assert.equal(preset.model, "gpt-5-mini");

const temp = await mkdtemp(path.join(tmpdir(), "llmix-registry-proof-"));
await copyRegistry("config/llm", temp);

const currentPath = path.join(temp, "current.json");
const current = JSON.parse(await readFile(currentPath, "utf8"));
current.revision = `${current.revision}-tampered`;
await writeFile(currentPath, `${JSON.stringify(current, null, 2)}\n`);

await assert.rejects(
  ConfigRegistryManager.open(temp, { signedRoot }),
  /digest|integrity|registry root|signature/i,
);
```

`copyRegistry` is a test helper that copies the directory recursively. The point
is the behavior: a valid registry loads; a modified registry does not.

## What Not To Do

- Do not put human-edited presets anywhere except
  `config/llm/source/<module>/<preset>.mda`.
- Do not put the trust anchor inside `config/llm`.
- Do not let runtime code read source `.mda` files for production requests.
- Do not generate `current.json` by hand.
- Do not write a project-local compiler.
- Do not skip MDA CLI release finalize and doctor checks.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No presets are published. | Confirm files are under `config/llm/source/<module>/<preset>.mda`. |
| `release prepare` fails. | Run `mda validate`, `mda integrity compute`, `mda sign`, and `mda verify` on the source preset first. |
| Runtime rejects a valid-looking registry. | Confirm the trust anchor points to the generated `registry-root.json` digest for this release. |
| Runtime accepts a modified registry. | Confirm the app passes `signedRoot` and loads the trust anchor from outside `config/llm`. |
| An older release loads. | Use `--minimum-revision`, `--minimum-published-at`, or a runtime high-watermark. |

## Related Docs

- [LLMix usage reference](../llmix-usage-ref.md)
- [LLMix TypeScript guide](../llmix-typescript.md)
- [MDA Config Runtime Guide](../../mda-config/README.md)
