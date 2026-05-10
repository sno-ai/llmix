# `snoai-mda-config`

Rust source-mode loader for MDA v1.0 `.mda` configuration artifacts.

Release 1.1.1 keeps this crate version-aligned with the npm and PyPI loaders
while preserving the same MDA v1.0 source-mode contract.

It mirrors the public contract of `@snoai/mda-config` where Rust can do so
safely without reimplementing Sigstore crypto:

- frontmatter extraction and YAML parsing
- MDA source-schema validation
- integrity canonicalization and digest verification
- `requires.network` enforcement
- RC2 trusted-runtime checks plus explicit Rekor/Sigstore/did:web verifier
  injection

## Install

```toml
[dependencies]
snoai-mda-config = "1.1"
serde = { version = "1", features = ["derive"] }
```

## Quick Start

```rust
use serde::Deserialize;
use snoai_mda_config::{load_mda_source, LoadMdaSourceOptions};

#[derive(Debug, Deserialize)]
struct Config {
    name: String,
    description: String,
}

let cfg: Config = load_mda_source(
    "./preset.mda",
    LoadMdaSourceOptions {
        verify_integrity: true,
        ..Default::default()
    },
)?;
```

For production signed configs, pass `trusted_runtime=true` with a strict RC2
trust policy, `RekorClient`, and verifier hooks. The crate intentionally does
not fake signature validation: if a required verifier is not configured,
loading fails closed.

Sigstore `signer` values are `sigstore-oidc:<issuer>`. The verified subject is
returned by the Sigstore verifier and matched exactly against
`trusted_signers`. did:web is supported through a `DidWebVerifier` hook; without
that hook, a policy that trusts did:web fails closed with
`trust-policy-violation`.
