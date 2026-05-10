# Migrate LLMix Presets to MDA

LLMix (`@snoai/llmix`) is a concrete example of how to move from plain YAML to
MDA-backed config.

The old loader owned everything: file IO, YAML parsing, schema validation, and
whatever safety checks were added around the edges. With MDA, the project keeps
its existing schema, but the MDA mechanism layer moves into `mda-config`.

## Before

`tests/fixtures/sample_preset.yaml`:

```yaml
common:
  model: gpt-5-mini
  provider: openai
  temperature: 0.7
  maxOutputTokens: 4096
providerOptions:
  openai:
    reasoningEffort: medium
caching:
  strategy: memory
```

LLMix loaded this with `js-yaml` plus a Zod schema.

## After

Rename the file to `sample_preset.mda`. Put LLMix-owned data under
`metadata.snoai-llmix`.

```markdown
---
name: gpt5-mini-fast
description: Fast cheap multi-tool calls for everyday agent work.
requires:
  network: ["api.openai.com"]
metadata:
  mda:
    doc-id: "38f5a922-81b2-4f1a-8d8c-3a5be4ea7511"
    version: "1.2.0"
    tags: [openai, fast, low-cost]
  snoai-llmix:
    common:
      model: gpt-5-mini
      provider: openai
      temperature: 0.7
      maxOutputTokens: 4096
    providerOptions:
      openai:
        reasoningEffort: medium
    caching:
      strategy: memory
integrity:
  algorithm: sha256
  digest: "sha256:..."
signatures:
  - signer: "sigstore-oidc:https://accounts.google.com"
    key-id: "fulcio:..."
    payload-digest: "sha256:..."
    algorithm: ecdsa-p256
    signature: "MEUCIQ..."
    rekor-log-id: "..."
    rekor-log-index: 12345678
---

# gpt5-mini-fast

Use when:
- multi-tool dispatch should stay cheap;
- latency matters more than deep reasoning.
```

Validate the file while authoring:

```bash
npx @markdown-ai/cli validate sample_preset.mda --target source --json
```

Verify integrity after adding the digest:

```bash
npx @markdown-ai/cli integrity verify sample_preset.mda --target source --json
```

## Loader Migration

Replace the custom YAML parse path with one call:

```ts
import { z } from "zod";
import { loadMdaSource } from "@snoai/mda-config";

const LLMixPresetSchema = z.object({
  name: z.string(),
  description: z.string(),
  requires: z.object({ network: z.array(z.string()).optional() }).optional(),
  metadata: z.object({
    mda: z
      .object({
        version: z.string().optional(),
        "doc-id": z.string().optional(),
        tags: z.array(z.string()).optional(),
      })
      .optional(),
    "snoai-llmix": z.object({
      common: z.object({
        model: z.string(),
        provider: z.string(),
        temperature: z.number().optional(),
        maxOutputTokens: z.number().optional(),
      }),
      providerOptions: z.record(z.string(), z.unknown()).optional(),
      caching: z.object({ strategy: z.string() }).optional(),
    }),
  }),
  integrity: z.object({ algorithm: z.string(), digest: z.string() }).optional(),
  signatures: z.array(z.unknown()).optional(),
});

const cfg = await loadMdaSource(presetPath, LLMixPresetSchema, {
  trustedRuntime: true,
  enforceRequires: true,
  allowedNetworks: ["api.openai.com", "api.anthropic.com"],
  trustPolicy: {
    version: 1,
    trustedSigners: [
      {
        type: "sigstore-oidc",
        issuer: "https://accounts.google.com",
        subject: "releases@snoai.com",
      },
    ],
    rekor: { url: "https://rekor.sigstore.dev" },
  },
  rekorClient,
  sigstoreVerifier,
});
```

LLMix keeps the LLMix schema. `@snoai/mda-config` handles frontmatter
extraction, MDA source validation, integrity, signature policy, and
`requires.network`.

## Migration Checklist

1. Rename `*.yaml` to `*.mda`.
2. Add top-level `name` and `description`.
3. Move project fields under `metadata.snoai-llmix`.
4. Add `requires.network` if the preset needs network access.
5. Validate with the MDA CLI.
6. Add and verify `integrity` when the preset is ready to ship.
7. Replace custom YAML loading with `loadMdaSource()`.
8. Add trusted-runtime policy and verifier hooks for signed release presets.

## Runtime Status

LLMix now uses the same pattern across public runtimes:

- TypeScript uses `@snoai/mda-config` with Zod.
- Python uses `snoai-mda-config` with pydantic.
- Rust uses `snoai-mda-config` with serde.

The shape changes once. The runtime contract stays the same.
