# CLI and Runtime Quick Start

Use two tools together:

- `@markdown-ai/cli` is the authoring and build tool. It creates, validates,
  fingerprints, and compiles MDA files.
- `@snoai/mda-config` is the runtime loader. It reads a source-mode `.mda`
  config inside your app and enforces the MDA checks before your schema runs.

The CLI is not a runtime dependency of this package. Keep it in your shell,
scripts, or CI. Keep `@snoai/mda-config` in the application that consumes the
config.

## Install the Runtime Loader

```bash
npm install @snoai/mda-config zod
```

`zod` is a peer dependency because your project owns the project-specific
frontmatter shape.

You can run the MDA CLI through `npx`:

```bash
npx @markdown-ai/cli --help
```

If you prefer a global binary:

```bash
npm install -g @markdown-ai/cli
mda --help
```

## Create a Source `.mda`

Start with a scaffold:

```bash
npx @markdown-ai/cli init gpt5-mini-fast --out presets/gpt5-mini-fast.mda
```

Then edit the file into your project config. Project-specific data belongs
under `metadata.<your-namespace>.*`.

```markdown
---
name: gpt5-mini-fast
description: Fast cheap calls for everyday agent work.
requires:
  network: ["api.openai.com"]
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.7
      maxOutputTokens: 4096
    providerOptions:
      openai:
        reasoningEffort: medium
    caching:
      strategy: memory
---

# gpt5-mini-fast

Use when latency and cost matter more than deep reasoning.
```

Validate it before committing:

```bash
npx @markdown-ai/cli validate presets/gpt5-mini-fast.mda --target source --json
```

For agent or CI scripts, prefer `--json`. Check `ok: true` and `exitCode: 0`
instead of scraping human output.

## Load It at Runtime

```ts
import { z } from "zod";
import {
  ErrorCategory,
  MdaConfigError,
  loadMdaSource,
} from "@snoai/mda-config";

const LLMixPresetSchema = z.object({
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
    "snoai-llmix": z.object({
      common: z.object({
        provider: z.enum([
          "openai",
          "anthropic",
          "google",
          "deepseek",
          "openrouter",
          "sno-gpu",
        ]),
        model: z.string().min(1),
        temperature: z.number().min(0).max(2).optional(),
        maxOutputTokens: z.number().int().positive().optional(),
      }),
      providerOptions: z.record(z.string(), z.unknown()).optional(),
      caching: z
        .object({
          strategy: z.enum([
            "native",
            "gateway",
            "disabled",
            "redis",
            "redis-or-memory",
            "memory",
          ]),
        })
        .optional(),
    }),
  }),
});

try {
  const preset = await loadMdaSource(
    "./presets/gpt5-mini-fast.mda",
    LLMixPresetSchema,
    {
      enforceRequires: true,
      allowedNetworks: ["api.openai.com"],
    },
  );

  const model = preset.metadata["snoai-llmix"].common.model;
} catch (err) {
  if (err instanceof MdaConfigError) {
    if (err.category === ErrorCategory.RequiresNotSatisfied) {
      // The config requested network access this runtime did not allow.
    }
    throw err;
  }
  throw err;
}
```

That is the normal integration path. Your schema owns
`metadata.<your-namespace>.*`. `@snoai/mda-config` owns extraction, MDA source
validation, optional integrity and signature checks, and `requires.network`
enforcement.

## Add Integrity

Integrity is a fingerprint over MDA canonical bytes. It is not a raw file hash.

Compute the digest:

```bash
npx @markdown-ai/cli integrity compute presets/gpt5-mini-fast.mda \
  --target source \
  --algorithm sha256 \
  --json
```

Add the returned digest to top-level frontmatter:

```yaml
integrity:
  algorithm: sha256
  digest: "sha256:..."
```

Then verify it:

```bash
npx @markdown-ai/cli integrity verify presets/gpt5-mini-fast.mda \
  --target source \
  --json
```

Turn on runtime integrity verification after the file carries a valid
`integrity` block:

```ts
await loadMdaSource(path, schema, {
  verifyIntegrity: true,
});
```

Every meaningful edit changes the digest. That is the point. Recompute it after
changing the frontmatter or body.

## Compile Agent-Facing Markdown

If you also need files for external agent runtimes, compile from the same
source:

```bash
npx @markdown-ai/cli compile presets/gpt5-mini-fast.mda \
  --target SKILL.md AGENTS.md \
  --out-dir out \
  --integrity \
  --json
```

Validate the compiled output:

```bash
npx @markdown-ai/cli validate out/SKILL.md --target SKILL.md --json
npx @markdown-ai/cli validate out/AGENTS.md --target AGENTS.md --json
npx @markdown-ai/cli integrity verify out/SKILL.md --target SKILL.md --json
```

Compiled Markdown is for agent ecosystems that load `SKILL.md`, `AGENTS.md`, or
`MCP-SERVER.md`. `@snoai/mda-config` loads the source `.mda` config.

## Trusted Runtime

For production configs that must be trusted, use `trustedRuntime: true`.

Trusted runtime requires:

- `integrity`
- a non-empty `signatures[]`
- an RC3 trust policy
- a Rekor client for Sigstore signatures
- a Sigstore verifier hook, did:web verifier hook, or both depending on policy

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

See [`trusted-runtime-policy.md`](./trusted-runtime-policy.md) for the full
trust boundary.

## What Gets Returned

`loadMdaSource()` returns the parsed frontmatter after your schema succeeds.
The Markdown body is used for integrity canonicalization but is not returned.
If your app needs body content, keep that as a separate reader path.

Common `MdaConfigError.category` values:

- `schema-violation`: invalid MDA source-mode frontmatter.
- `project-schema-violation`: your Zod schema rejected the frontmatter.
- `integrity-mismatch`: `verifyIntegrity` found a digest mismatch.
- `requires-not-satisfied`: `requires.network` exceeds `allowedNetworks`.
- `missing-required-integrity` / `missing-required-signature`: trusted-runtime
  required fields are absent.
- `signature-verification-failure` / `rekor-inclusion-failure`: a candidate
  signature could not be verified.
- `no-trusted-signature` / `insufficient-trusted-signatures` /
  `trust-policy-violation`: the artifact did not satisfy the trust policy.
