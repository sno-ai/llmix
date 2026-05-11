# MDA Config Runtime Guide

`mda-config` is the runtime loader for source `.mda` files.

Use the MDA CLI while authoring and releasing. Use `mda-config` inside the app
that consumes the file. The split matters. The CLI makes and checks artifacts.
The loader says whether a specific artifact is acceptable at runtime.

## Quick Start

1. Create or update a source `.mda` file.
2. Validate it with the MDA CLI.
3. Add integrity and signatures when the file is for production.
4. Load it in TypeScript, Python, or Rust with your project schema.
5. For production trust, pass `trustedRuntime` and verifier hooks.

```bash
npx @markdown-ai/cli init gpt5-mini-fast --out presets/gpt5-mini-fast.mda
npx @markdown-ai/cli validate presets/gpt5-mini-fast.mda --target source --json
npx @markdown-ai/cli integrity verify presets/gpt5-mini-fast.mda --target source --json
```

For scripts and AI agents, prefer `--json`. Check structured output. Do not
scrape human text.

## What Goes In `.mda`

Put the MDA mechanism fields at the top level. Put product-owned settings under
your own metadata namespace.

```markdown
---
name: gpt5-mini-fast
description: Fast calls for everyday agent work.
requires:
  network: ["api.openai.com"]
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.7
      maxOutputTokens: 4096
integrity:
  algorithm: sha256
  digest: "sha256:..."
signatures:
  - signer: "sigstore-oidc:https://token.actions.githubusercontent.com"
    key-id: "fulcio:..."
    payload-digest: "sha256:..."
    algorithm: ecdsa-p256
    signature: "MEUCIQ..."
    rekor-log-id: "..."
    rekor-log-index: 12345678
    payload-type: "application/vnd.mda.integrity+json"
---

# Optional body
```

The loader returns parsed frontmatter. The body is still part of integrity
verification, but it is not returned as app config.

Use `metadata.<your-namespace>` for product settings:

```yaml
metadata:
  acme-agent:
    model: gpt-5-mini
    retries: 2
```

Avoid generic namespaces such as `config`, `settings`, or `runtime`. They look
fine until another tool needs the same word.

## Runtime Loading

TypeScript:

```bash
npm install @snoai/mda-config zod
```

```ts
import { z } from "zod";
import { loadMdaSource } from "@snoai/mda-config";

const Schema = z.object({
  name: z.string(),
  description: z.string(),
  requires: z
    .object({
      network: z.union([
        z.literal("none"),
        z.literal("local"),
        z.literal("public"),
        z.array(z.string()),
      ]),
    })
    .optional(),
  metadata: z.object({
    "acme-agent": z.object({
      model: z.string().min(1),
      retries: z.number().int().nonnegative().optional(),
    }),
  }),
});

const cfg = await loadMdaSource("./presets/gpt5-mini-fast.mda", Schema, {
  verifyIntegrity: true,
  enforceRequires: true,
  allowedNetworks: ["api.openai.com"],
});
```

Python:

```bash
pip install snoai-mda-config pydantic
```

```python
from pydantic import BaseModel, ConfigDict
from snoai_mda_config import load_mda_source


class AcmeAgent(BaseModel):
    model: str
    retries: int | None = None


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acme_agent: AcmeAgent


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    metadata: Metadata


cfg = load_mda_source("./presets/gpt5-mini-fast.mda", schema=Schema, verify_integrity=True)
```

Rust:

```toml
[dependencies]
snoai-mda-config = "1.1"
serde = { version = "1", features = ["derive"] }
```

```rust
use serde::Deserialize;
use snoai_mda_config::{load_mda_source, LoadMdaSourceOptions};

#[derive(Debug, Deserialize)]
struct Config {
    name: String,
    description: String,
}

let cfg: Config = load_mda_source(
    "./presets/gpt5-mini-fast.mda",
    LoadMdaSourceOptions {
        verify_integrity: true,
        ..Default::default()
    },
)?;
```

## Integrity

Integrity is computed over MDA canonical bytes. It is not a raw file hash.

```bash
npx @markdown-ai/cli integrity compute presets/gpt5-mini-fast.mda \
  --target source \
  --algorithm sha256 \
  --json
```

Add the returned digest to top-level `integrity`, then verify it:

```bash
npx @markdown-ai/cli integrity verify presets/gpt5-mini-fast.mda \
  --target source \
  --json
```

Every meaningful edit changes the digest. That is the point.

## Trusted Runtime

Parsing is not trust. A digest is not trust either. A digest only says the bytes
match the digest stored with the file.

Use trusted runtime when the config must come from a trusted release identity:

```ts
await loadMdaSource(path, schema, {
  trustedRuntime: true,
  trustPolicy: {
    version: 1,
    trustedSigners: [
      {
        type: "sigstore-oidc",
        issuer: "https://token.actions.githubusercontent.com",
        subject: "repo:OWNER/REPO:ref:refs/heads/main",
      },
    ],
    rekor: {
      url: "https://rekor.sigstore.dev",
    },
  },
  rekorClient,
  sigstoreVerifier,
});
```

With `trustedRuntime: true`, the loader:

1. validates the trust policy;
2. requires `integrity`;
3. requires a non-empty `signatures[]`;
4. verifies integrity;
5. checks signature payload digests against `integrity.digest`;
6. asks caller-supplied verifier hooks to verify cryptography;
7. matches verified identities against `trustedSigners`;
8. enforces `minSignatures`.

Sigstore policies require Rekor policy. did:web policies require a
`didWebVerifier` hook. A mixed policy is valid. Signatures from the same
distinct identity count once.

This package does not ship signing, Rekor HTTP transport, or remote refresh.
Those belong in the release process or the application around the loader.

## LLMix Registry Trust

LLMix uses `mda-config` to load signed `.mda` presets, then publishes a signed
registry root for production runtime. If you are deploying LLMix registries, read
the LLMix guide:

- [Secure LLMix configuration](../llmix/secure-mda/secure-llmix-configuration.md)
- [中文：安全使用 LLMix MDA 配置](../llmix/secure-mda/secure-llmix-configuration.zh.md)

## Troubleshooting

| Symptom | What it usually means |
| --- | --- |
| `schema-violation` | The file is not valid MDA source frontmatter. |
| `project-schema-violation` | Your Zod, pydantic, or serde schema rejected the product namespace. |
| `integrity-mismatch` | The file changed after the digest was written, or the digest was copied from another artifact. |
| `requires-not-satisfied` | The file asks for network access this runtime did not allow. |
| `missing-required-integrity` | `trustedRuntime` was enabled, but the file has no top-level `integrity`. |
| `missing-required-signature` | `trustedRuntime` was enabled, but the file has no usable signature. |
| `signature-verification-failure` | A verifier hook rejected a candidate signature. |
| `rekor-inclusion-failure` | Sigstore policy required Rekor evidence and it did not verify. |
| `no-trusted-signature` | Signatures exist, but none match the policy. |
| `insufficient-trusted-signatures` | Fewer distinct trusted identities verified than `minSignatures`. |

## Packages

| Package | Runtime | Path |
| --- | --- | --- |
| `@snoai/mda-config` | TypeScript | [`packages/mda-config/typescript/`](../../packages/mda-config/typescript/) |
| `snoai-mda-config` | Python | [`packages/mda-config/python/`](../../packages/mda-config/python/) |
| `snoai-mda-config` | Rust | [`packages/mda-config/rust/`](../../packages/mda-config/rust/) |

Spec pin: MDA v1.0.
