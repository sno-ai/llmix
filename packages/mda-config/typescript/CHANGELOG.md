# Changelog

All notable changes to `@snoai/mda-config` are documented here. The project follows Semantic Versioning and pins the MDA spec version it targets.

## [1.1.1] — 2026-05-10

- Move the package into the `sno-ai/llmix` monorepo and update repository
  metadata.
- Keep the TypeScript loader schema parameter structural so consumers can use
  Zod v3, Zod v4, or a compatible `safeParse` adapter.
- Align package metadata for the monorepo publish path.

## [1.1.0] — 2026-05-09

- Align TypeScript, Python, and Rust package versions at `1.1.0`.
- Align trusted-runtime behavior with MDA v1.0.0-rc.2: strict
  `trustedSigners` policy validation, Sigstore signer strings without embedded
  subjects, did:web verifier hooks, Rekor URL/entry binding, and distinct
  trusted-identity threshold counting.
- Harden signature verification so `verifySignatures` requires integrity
  metadata, rejects empty signature sets, and fails closed without configured
  verifier hooks.
- Bind Rekor DSSE verification inputs to payload type, payload, signature, and
  key id; require Sigstore verifier results to include the expected issuer and
  identity.

## [1.0.2] — 2026-05-07

- ci: switch to npm Trusted Publishing (GitHub OIDC); no source changes.

(v1.0.1 was tagged but CI failed because the runner shipped npm 10 which predates Trusted Publishing support; the tag was deleted and v1.0.2 ships the same change with `npm install -g npm@latest` added in CI.)

## [1.0.0] — 2026-05-07

- mda-spec: v1.0
- Initial release of `@snoai/mda-config` (TypeScript).
- Implements MDA §02-1.1 frontmatter extraction, §08 integrity, §09 Sigstore signature verification (wraps the official `sigstore` npm client), and §10-3.3 `requires.network` enforcement.
- Public API: `loadMdaSource`, `verifyIntegrity`, `verifySignatures`, `enforceRequires`, `MdaConfigError`, `ErrorCategory`.
- Python package skeleton was reserved for v1.1; no implementation shipped in this release.

### Out of scope (explicit deferrals)

- `did:web` air-gap signature verification (PRD §2; loader rejects with `unknown-signer-method` when encountered).
- `requires.runtime` / `requires.tools` / `requires.packages` / `requires.model` / `requires.cost-hints` enforcement.
- Signing / production of new signatures (verify-only library).
