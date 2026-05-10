# Design Project Config Frontmatter

`@snoai/mda-config` validates the MDA mechanism layer. It does not know your
product config. That part belongs to your schema.

The rule is simple:

- MDA fields stay at the top level or under `metadata.mda`.
- Your product fields stay under `metadata.<your-namespace>`.
- Security fields stay at the top level so any MDA-aware tool can find them.

## A Good Shape

```yaml
---
name: gpt5-mini-fast
description: Fast cheap multi-tool calls.
requires:
  network: ["api.openai.com"]
metadata:
  mda:
    doc-id: "38f5a922-81b2-4f1a-8d8c-3a5be4ea7511"
    version: "1.2.0"
    tags: [openai, fast, low-cost]
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.7
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

# Optional Markdown body
```

The body is preserved and participates in integrity verification. The runtime
loader returns the parsed frontmatter, not the body.

## What Goes Where

Use top-level fields for the MDA contract:

- `name`
- `description`
- `requires`
- `integrity`
- `signatures`
- `metadata`
- optional MDA fields such as `version`, `doc-id`, `tags`, `depends-on`,
  `author`, `created-date`, `updated-date`, and `relationships`

Use `metadata.mda` for MDA metadata you want to keep grouped:

```yaml
metadata:
  mda:
    version: "1.2.0"
    tags: [openai, low-cost]
```

Use `metadata.<your-namespace>` for your actual project config:

```yaml
metadata:
  acme-agent:
    model: gpt-5-mini
    retries: 2
```

Then validate that namespace with your own Zod, pydantic, or serde shape. The
MDA loader will not guess what `metadata.acme-agent` means.

## Namespace Rules

Pick a kebab-case namespace:

```yaml
metadata:
  acme-agent: {}
```

Avoid generic names such as `config`, `settings`, or `runtime`. They read well
today and become ambiguous tomorrow.

Before stable adoption, register the namespace in the MDA registry so other
tools know who owns it.

## Integrity

Do not hash the raw file bytes. MDA integrity is computed over canonical bytes.

Use the CLI:

```bash
npx @markdown-ai/cli integrity compute path/to/config.mda \
  --target source \
  --algorithm sha256 \
  --json
```

Add the returned digest:

```yaml
integrity:
  algorithm: sha256
  digest: "sha256:..."
```

Then verify it:

```bash
npx @markdown-ai/cli integrity verify path/to/config.mda --target source --json
```

If the body changes, the digest changes. If a comment changes inside
frontmatter, the digest can change too. Treat the digest like a lock washer.
Small, plain, and easy to forget until it matters.

## Signatures

Signatures stay in top-level `signatures[]`.

For Sigstore, `signer` contains the issuer:

```yaml
signer: "sigstore-oidc:https://token.actions.githubusercontent.com"
```

The verified subject comes from the Sigstore verifier result and must match a
trusted signer in your runtime policy.

For did:web, `signer` uses the domain:

```yaml
signer: "did-web:tools.example.com"
```

did:web signatures must not include Rekor fields.

If `payload-type` is absent, verifiers use
`application/vnd.mda.integrity+json`. That explicit value is also valid. Custom
payload types must look like `application/vnd.<vendor>.<doc-type>+json`.
`+jcs+json` is rejected.

## Capability Declaration

MDA v1.0 enforces `requires.network` in this package. Other standard
`requires` keys pass through to your project schema for advisory use.

Use the narrowest value you can:

```yaml
requires:
  network: "none"
```

or:

```yaml
requires:
  network: ["api.openai.com"]
```

Then enforce it at runtime:

```ts
await loadMdaSource(path, schema, {
  enforceRequires: true,
  allowedNetworks: ["api.openai.com"],
});
```

## Versioning

Use SemVer strings:

```yaml
metadata:
  mda:
    version: "1.2.0"
```

Quote the value. Some YAML stacks treat bare version-looking values in
surprising ways. A version number should not depend on the mood of a parser.
