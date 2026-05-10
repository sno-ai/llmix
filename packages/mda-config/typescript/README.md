# `@snoai/mda-config` (TypeScript)

Source-mode `.mda` loader implementing the MDA Open Spec v1.0 mechanism layer:

- §02-1.1 frontmatter extraction (BOM strip, CRLF normalization, fence rules, YAML 1.2 core schema)
- §08 integrity (JCS canonicalization + sha256/384/512)
- §09 / §13  trusted-runtime signature verification (DSSE PAE; injectable
  Rekor, Sigstore, and did:web verifier hooks; enforces `dsse-v0.0.1` Rekor
  entry kind for Sigstore)
- §10-3.3 `requires.network` enforcement
- §11-2 canonical loader algorithm (Stages A → G); §11-3 error vocabulary surfaced as `ErrorCategory`

## Install

```bash
npm install @snoai/mda-config zod
# or: pnpm add / bun add
```

## Usage

```ts
import { z } from "zod";
import { loadMdaSource, MdaConfigError, ErrorCategory } from "@snoai/mda-config";

const MyConfigSchema = z.object({
  name: z.string(),
  description: z.string(),
  requires: z.object({ network: z.array(z.string()).optional() }).optional(),
  metadata: z.object({
    "snoai-llmix": z.object({
      common: z.object({ model: z.string(), provider: z.string() }),
    }),
  }),
});

const cfg = await loadMdaSource("./presets/gpt5-mini-fast.mda", MyConfigSchema, {
  verifyIntegrity: true,
  enforceRequires: true,
  allowedNetworks: ["api.openai.com"],
});

`loadMdaSource` accepts any schema object with a Zod-compatible `safeParse`
method. This keeps the loader usable with both Zod v3 and v4, and with small
schema adapters that return `{ success, data }` or `{ success, error }`.

// For production signed release presets, pass trustedRuntime: true with an
//  trust policy, Rekor client, and verifier hooks.
```

See [`../../../docs/mda-config/design-project-config-frontmatter.md`](../../../docs/mda-config/design-project-config-frontmatter.md), [`../../../docs/mda-config/trusted-runtime-policy.md`](../../../docs/mda-config/trusted-runtime-policy.md), and [`../../../docs/mda-config/migrate-llmix-presets-to-mda.md`](../../../docs/mda-config/migrate-llmix-presets-to-mda.md).

## Trusted Runtime

`trustedRuntime: true` requires integrity, a non-empty `signatures[]`, and a
valid trust policy before a config is treated as trusted:

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
    rekor: { url: "https://rekor.sigstore.dev" },
  },
  rekorClient,
  sigstoreVerifier,
});
```

Sigstore `signer` values are `sigstore-oidc:<issuer>`. The subject is taken
from the verified Fulcio result and matched exactly against policy. did:web is
supported through a `didWebVerifier` hook; without that hook, a policy that
trusts did:web fails closed with `trust-policy-violation`.

## API

| Symbol | Spec section |
|--------|--------------|
| `loadMdaSource(path, schema, options)` | §11-2 |
| `verifyIntegrity(frontmatter, body, integrity)` | §08-4 |
| `verifySignatures(signatures, integrity, policy, deps)` | §09-4.2 |
| `enforceRequires(requires, env)` | §10-4 |
| `extractFrontmatter(bytes)` / `parseFrontmatterYaml(str)` | §02-1.1 |
| `MdaConfigError` + `ErrorCategory` | §11-3 |

## Spec pin

- `mda-spec: v1.0`
- License: Apache-2.0

## Out of scope at v1.0 (PRD §2)

- Signing path (verify-only library).
- Built-in Rekor HTTP transport.
- `requires.runtime` / `requires.tools` / `requires.packages` / `requires.model` / `requires.cost-hints` enforcement (passed through to the consumer's Zod schema).
- Language ports publish separately: Python and Rust are available as
  `snoai-mda-config`.
