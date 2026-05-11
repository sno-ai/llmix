#!/usr/bin/env python3
"""Trusted-runtime coverage for the Python MDA loader."""

from __future__ import annotations

import json
import base64
from pathlib import Path
from typing import Any

import pytest
from snoai_mda_config import (
    DEFAULT_PAYLOAD_TYPE,
    DidWebVerificationInput,
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
)
from snoai_mda_config.integrity import (
    canonicalize_artifact,
    hash_canonical,
)

from llmix import MdaConfigLoadOptions, load_mda_config
from llmix.types import InvalidConfigError

DOMAIN = "runtime.example.com"
ISSUER = "https://issuer.example.com"
SUBJECT = "repo:llmix/llmix:ref:refs/heads/main"
REKOR_URL = "https://rekor.example.com"


class FakeDidWebVerifier:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.seen: DidWebVerificationInput | None = None

    def verify(self, input_value: DidWebVerificationInput) -> bool:
        self.seen = input_value
        return self.ok and input_value.domain == DOMAIN


class FakeRekorClient:
    rekor_url = REKOR_URL

    def __init__(self, entry: RekorEntry) -> None:
        self.entry = entry
        self.seen: tuple[str, str, int] | None = None

    def fetch_entry(
        self, rekor_url: str, log_id: str, log_index: int
    ) -> RekorEntry | None:
        self.seen = (rekor_url, log_id, log_index)
        return self.entry


class FakeSigstoreVerifier:
    def __init__(self) -> None:
        self.seen: tuple[RekorEntry, SignatureEntry, bytes] | None = None

    def verify(
        self,
        entry: RekorEntry,
        signature: SignatureEntry,
        pae_bytes: bytes,
    ) -> SigstoreVerificationResult:
        self.seen = (entry, signature, pae_bytes)
        return SigstoreVerificationResult(issuer=ISSUER, subject=SUBJECT)


def _trust_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "trustedSigners": [{"type": "did-web", "domain": DOMAIN}],
    }


def _sigstore_trust_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "trustedSigners": [
            {"type": "sigstore-oidc", "issuer": ISSUER, "subject": SUBJECT}
        ],
        "rekor": {"url": REKOR_URL},
    }


def _frontmatter() -> dict[str, Any]:
    return {
        "name": "trusted-runtime",
        "description": "Trusted runtime preset.",
        "requires": {"network": ["none"]},
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "temperature": 0.2,
                }
            }
        },
    }


def _write_signed_mda(path: Path) -> Path:
    body = "# trusted runtime\n"
    unsigned = _frontmatter()
    digest = "sha256:" + hash_canonical(
        canonicalize_artifact(unsigned, body), "sha256"
    )
    signed = dict(unsigned)
    signed["integrity"] = {"algorithm": "sha256", "digest": digest}
    signed["signatures"] = [
        {
            "signer": f"did-web:{DOMAIN}",
            "key-id": f"did:web:{DOMAIN}#root",
            "payload-digest": digest,
            "algorithm": "ed25519",
            "signature": "fixture-signature",
            "payload-type": DEFAULT_PAYLOAD_TYPE,
        }
    ]
    path.write_text(
        f"---\n{json.dumps(signed, indent=2)}\n---\n{body}", encoding="utf-8"
    )
    return path


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _write_sigstore_signed_mda(path: Path) -> tuple[Path, RekorEntry]:
    body = "# trusted runtime\n"
    unsigned = _frontmatter()
    digest = "sha256:" + hash_canonical(
        canonicalize_artifact(unsigned, body), "sha256"
    )
    integrity = {"algorithm": "sha256", "digest": digest}
    signature: SignatureEntry = {
        "signer": f"sigstore-oidc:{ISSUER}",
        "key-id": "sigstore-key",
        "payload-digest": digest,
        "algorithm": "ecdsa-p256",
        "signature": "fixture-sigstore-signature",
        "payload-type": DEFAULT_PAYLOAD_TYPE,
        "rekor-log-id": "rekor-log",
        "rekor-log-index": 42,
    }
    signed = dict(unsigned)
    signed["integrity"] = integrity
    signed["signatures"] = [signature]
    entry: RekorEntry = {
        "kind": "dsse-v0.0.1",
        "log_id": "rekor-log",
        "log_index": 42,
        "inclusion_verified": True,
        "dsse_envelope": {
            "payload_type": DEFAULT_PAYLOAD_TYPE,
            "payload": base64.b64encode(_canonical_json({"integrity": integrity})).decode(
                "ascii"
            ),
            "signatures": [{"sig": signature["signature"], "keyid": signature["key-id"]}],
        },
    }
    path.write_text(
        f"---\n{json.dumps(signed, indent=2)}\n---\n{body}", encoding="utf-8"
    )
    return path, entry


def test_load_mda_config_accepts_trusted_runtime_did_web_signature(
    tmp_path: Path,
) -> None:
    path = _write_signed_mda(tmp_path / "trusted.mda")
    verifier = FakeDidWebVerifier()

    config = load_mda_config(
        path,
        MdaConfigLoadOptions(
            verify_integrity=True,
            verify_signatures=True,
            trusted_runtime=True,
            enforce_requires=True,
            allowed_networks=["none"],
            trust_policy=_trust_policy(),
            did_web_verifier=verifier,
        ),
    )

    assert config.get("provider") == "openai"
    assert verifier.seen is not None
    assert verifier.seen.domain == DOMAIN
    assert verifier.seen.payload_type == DEFAULT_PAYLOAD_TYPE


def test_load_mda_config_accepts_trusted_runtime_sigstore_signature(
    tmp_path: Path,
) -> None:
    path, entry = _write_sigstore_signed_mda(tmp_path / "trusted.mda")
    rekor = FakeRekorClient(entry)
    verifier = FakeSigstoreVerifier()

    config = load_mda_config(
        path,
        MdaConfigLoadOptions(
            verify_integrity=True,
            verify_signatures=True,
            trusted_runtime=True,
            trust_policy=_sigstore_trust_policy(),
            rekor_client=rekor,
            sigstore_verifier=verifier,
        ),
    )

    assert config.get("provider") == "openai"
    assert rekor.seen == (REKOR_URL, "rekor-log", 42)
    assert verifier.seen is not None
    assert verifier.seen[1]["signer"] == f"sigstore-oidc:{ISSUER}"


def test_load_mda_config_fails_closed_when_did_web_verifier_rejects(
    tmp_path: Path,
) -> None:
    path = _write_signed_mda(tmp_path / "trusted.mda")

    with pytest.raises(InvalidConfigError):
        load_mda_config(
            path,
            MdaConfigLoadOptions(
                verify_integrity=True,
                verify_signatures=True,
                trusted_runtime=True,
                trust_policy=_trust_policy(),
                did_web_verifier=FakeDidWebVerifier(ok=False),
            ),
        )
