# snoai-mda-config

Python source-mode loader for MDA v1.0 configuration artifacts.

This package mirrors the TypeScript `@snoai/mda-config` and Rust
`snoai-mda-config` loader contract. The v1.0 surface covers frontmatter
extraction, MDA source-schema validation, integrity verification,
`requires.network` enforcement, RC2 trusted-runtime verifier hooks, and
consumer pydantic validation.

Python does not perform real Rekor transport or Sigstore cryptography by
itself. When `verify_signatures=True`, callers must provide a trust policy,
Rekor client, and Sigstore verifier hook. Missing verifier pieces fail closed.

```python
from pathlib import Path
from pydantic import BaseModel
from snoai_mda_config import load_mda_source


class Preset(BaseModel, extra="forbid"):
    name: str
    description: str
    metadata: dict | None = None
    integrity: dict | None = None
    signatures: list[dict] | None = None


config = load_mda_source(
    Path("preset.mda"),
    schema=Preset,
    verify_integrity=True,
)
```

For signed presets, also pass `verify_signatures=True`,
`trust_policy=...`, `rekor_client=...`, and verifier hooks. For production
trusted-runtime loading, prefer `trusted_runtime=True` with a strict RC2 policy:

```python
config = load_mda_source(
    Path("preset.mda"),
    schema=Preset,
    trusted_runtime=True,
    trust_policy={
        "version": 1,
        "trustedSigners": [
            {
                "type": "sigstore-oidc",
                "issuer": "https://token.actions.githubusercontent.com",
                "subject": "repo:OWNER/REPO:ref:refs/heads/main",
            }
        ],
        "rekor": {"url": "https://rekor.sigstore.dev"},
    },
    rekor_client=rekor_client,
    sigstore_verifier=sigstore_verifier,
)
```

did:web is supported through a `did_web_verifier` hook. If a policy trusts
did:web and that hook is absent, loading fails closed with
`trust-policy-violation`. For capability enforcement, pass
`enforce_requires=True` with `allowed_networks=[...]`.
