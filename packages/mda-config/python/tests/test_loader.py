from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from snoai_mda_config import (
    DEFAULT_PAYLOAD_TYPE,
    DidWebVerificationInput,
    DsseSignature,
    ErrorCategory,
    MdaConfigError,
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
    canonicalize_artifact,
    hash_canonical,
    load_mda_source,
    load_mda_source_from_bytes,
)

from .conftest import FIXTURES


class MinimalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    metadata: dict[str, Any] | None = None
    requires: dict[str, Any] | None = None
    integrity: dict[str, Any] | None = None
    signatures: list[dict[str, Any]] | None = None


SIGNER = "sigstore-oidc:https://accounts.google.com"
KEY_ID = "fulcio:test-key"
SIGNATURE = "MEUCIQDkXFIXTUREONLYBASE64=="
LOG_ID = "test-log"
LOG_INDEX = 42


class FakeRekorClient:
    rekor_url = "https://rekor.sigstore.dev"

    def __init__(self, entry: RekorEntry | None) -> None:
        self.entry = entry

    def fetch_entry(self, rekor_url: str, log_id: str, log_index: int) -> RekorEntry | None:
        assert rekor_url == self.rekor_url
        assert log_id == LOG_ID
        assert log_index == LOG_INDEX
        return self.entry


class FakeSigstoreVerifier:
    def __init__(
        self,
        *,
        issuer: str = "https://accounts.google.com",
        identity: str = "releases@snoai.com",
    ) -> None:
        self.issuer = issuer
        self.identity = identity

    def verify(
        self,
        entry: RekorEntry,
        signature: SignatureEntry,
        pae_bytes: bytes,
    ) -> SigstoreVerificationResult:
        assert entry.get("kind") == "dsse-v0.0.1"
        assert signature["signature"] == SIGNATURE
        assert pae_bytes.startswith(b"DSSEv1 ")
        return SigstoreVerificationResult(
            issuer=self.issuer,
            subject_alternative_name=self.identity,
        )


def _trust_policy(subject: str = "releases@snoai.com") -> dict[str, Any]:
    return {
        "version": 1,
        "trustedSigners": [
            {
                "type": "sigstore-oidc",
                "issuer": "https://accounts.google.com",
                "subject": subject,
            }
        ],
        "rekor": {"url": "https://rekor.sigstore.dev"},
    }


class FakeDidWebVerifier:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def verify(self, input_value: DidWebVerificationInput) -> bool:
        assert input_value.domain == "tools.example.com"
        assert input_value.pae_bytes.startswith(b"DSSEv1 ")
        return self.ok


def _signed_source(
    *,
    payload_digest: str | None = None,
    payload_type: str = DEFAULT_PAYLOAD_TYPE,
) -> tuple[bytes, dict[str, str]]:
    frontmatter: dict[str, Any] = {
        "name": "signed-config",
        "description": "Signed config.",
    }
    digest = "sha256:" + hash_canonical(canonicalize_artifact(frontmatter, ""), "sha256")
    signature_digest = payload_digest or digest
    source = f"""---
name: signed-config
description: Signed config.
integrity:
  algorithm: sha256
  digest: "{digest}"
signatures:
  - signer: "{SIGNER}"
    key-id: "{KEY_ID}"
    payload-digest: "{signature_digest}"
    algorithm: ecdsa-p256
    signature: "{SIGNATURE}"
    rekor-log-id: "{LOG_ID}"
    rekor-log-index: {LOG_INDEX}
    payload-type: "{payload_type}"
---
"""
    return source.encode(), {"algorithm": "sha256", "digest": digest}


def _payload_b64(integrity: dict[str, str]) -> str:
    payload = json.dumps({"integrity": integrity}, separators=(",", ":"), sort_keys=True).encode()
    return base64.b64encode(payload).decode()


def _rekor_entry(
    integrity: dict[str, str],
    *,
    kind: str = "dsse-v0.0.1",
    payload_type: str = DEFAULT_PAYLOAD_TYPE,
    payload: str | None = None,
    signatures: list[DsseSignature] | None = None,
) -> RekorEntry:
    return {
        "kind": kind,
        "certificate_pem": "",
        "dsse_envelope": {
            "payload_type": payload_type,
            "payload": payload if payload is not None else _payload_b64(integrity),
            "signatures": signatures
            if signatures is not None
            else [{"sig": SIGNATURE, "keyid": KEY_ID}],
        },
    }


def _requires_source(network_yaml: str) -> bytes:
    return f"""---
name: requires-config
description: Requires config.
requires:
  network: {network_yaml}
---
""".encode()


EntryFactory = Callable[[dict[str, str]], RekorEntry]
REKOR_ENVELOPE_MISMATCH_CASES: list[EntryFactory] = [
    lambda integrity: _rekor_entry(integrity, payload_type="application/other"),
    lambda integrity: _rekor_entry(integrity, payload="bad"),
    lambda integrity: _rekor_entry(integrity, signatures=[]),
    lambda integrity: _rekor_entry(integrity, signatures=[{"sig": SIGNATURE, "keyid": "bad"}]),
]


def test_loads_minimal_source_mode_file() -> None:
    cfg = load_mda_source(FIXTURES / "valid/01-minimal.mda", schema=MinimalSchema)

    assert cfg.name == "minimal-config"


def test_rejects_yaml_parse_errors() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source(FIXTURES / "invalid/10-yaml-parse-error.mda", schema=MinimalSchema)

    assert exc_info.value.category is ErrorCategory.FrontmatterYamlParseError


def test_rejects_integrity_mismatch_when_enabled() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source(
            FIXTURES / "invalid/11-integrity-mismatch.mda",
            schema=MinimalSchema,
            verify_integrity=True,
        )

    assert exc_info.value.category is ErrorCategory.IntegrityMismatch


def test_verifies_integrity_when_enabled() -> None:
    cfg = load_mda_source(
        FIXTURES / "valid/02-with-integrity.mda",
        schema=MinimalSchema,
        verify_integrity=True,
    )

    assert cfg.integrity is not None
    assert cfg.integrity["algorithm"] == "sha256"


def test_loads_llmix_sample_preset_with_integrity() -> None:
    cfg = load_mda_source(
        FIXTURES / "valid/sample_preset.mda",
        schema=MinimalSchema,
        verify_integrity=True,
    )

    assert cfg.metadata is not None
    llmix = cfg.metadata["snoai-llmix"]
    assert llmix["common"]["model"] == "gpt-5-mini"
    assert llmix["common"]["maxOutputTokens"] == 4096


def test_rejects_signature_digest_mismatch_in_stage_c() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source(FIXTURES / "invalid/12-signature-digest-mismatch.mda", schema=MinimalSchema)

    assert exc_info.value.category is ErrorCategory.SignatureDigestMismatch


def test_project_schema_violations_use_project_category() -> None:
    class NarrowSchema(BaseModel):
        model_config = ConfigDict(extra="forbid")

        name: str

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source(FIXTURES / "valid/01-minimal.mda", schema=NarrowSchema)

    assert exc_info.value.category is ErrorCategory.ProjectSchemaViolation


def test_missing_frontmatter_is_rejected_for_source_mode() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(b"# body only\n", schema=MinimalSchema)

    assert exc_info.value.category is ErrorCategory.MissingRequiredFrontmatter


def test_mda_schema_violation_surfaces_schema_category() -> None:
    source = b"""---
name: Not-Kebab
description: Invalid uppercase name.
---
"""

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(source, schema=MinimalSchema)

    assert exc_info.value.category is ErrorCategory.SchemaViolation


def test_verify_integrity_requires_integrity_field() -> None:
    source = b"""---
name: unsigned-config
description: Missing integrity.
---
"""

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(source, schema=MinimalSchema, verify_integrity=True)

    assert exc_info.value.category is ErrorCategory.SchemaViolation


def test_verify_signatures_requires_non_empty_signatures() -> None:
    source = b"""---
name: unsigned-config
description: Missing signatures.
integrity:
  algorithm: sha256
  digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000"
---
"""

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy(),
        )

    assert exc_info.value.category is ErrorCategory.SignatureVerificationFailure


@pytest.mark.parametrize(
    ("kwargs", "category"),
    [
        (
            {
                "rekor_client": FakeRekorClient(None),
                "sigstore_verifier": FakeSigstoreVerifier(),
            },
            ErrorCategory.TrustPolicyViolation,
        ),
        (
            {
                "trust_policy": _trust_policy(),
                "sigstore_verifier": FakeSigstoreVerifier(),
            },
            ErrorCategory.TrustPolicyViolation,
        ),
        (
            {
                "trust_policy": _trust_policy(),
                "rekor_client": FakeRekorClient(None),
            },
            ErrorCategory.TrustPolicyViolation,
        ),
    ],
)
def test_verify_signatures_requires_all_injected_dependencies(
    kwargs: dict[str, Any],
    category: ErrorCategory,
) -> None:
    source, _integrity = _signed_source()

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            **kwargs,
        )

    assert exc_info.value.category is category


def test_signature_payload_digest_mismatch_fails_closed() -> None:
    source, _integrity = _signed_source(payload_digest=f"sha256:{'0' * 64}")

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy(),
        )

    assert exc_info.value.category is ErrorCategory.SignatureDigestMismatch


def test_rekor_entry_kind_mismatch_fails_closed() -> None:
    source, integrity = _signed_source()

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy(),
            rekor_client=FakeRekorClient(_rekor_entry(integrity, kind="hashedrekord")),
            sigstore_verifier=FakeSigstoreVerifier(),
        )

    assert exc_info.value.category is ErrorCategory.RekorEntryTypeMismatch


@pytest.mark.parametrize(
    "entry",
    REKOR_ENVELOPE_MISMATCH_CASES,
)
def test_rekor_envelope_mismatch_fails_closed(entry: EntryFactory) -> None:
    source, integrity = _signed_source()

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy(),
            rekor_client=FakeRekorClient(entry(integrity)),
            sigstore_verifier=FakeSigstoreVerifier(),
        )

    assert exc_info.value.category is ErrorCategory.RekorInclusionFailure


@pytest.mark.parametrize(
    "verifier",
    [
        FakeSigstoreVerifier(issuer="https://issuer.example"),
        FakeSigstoreVerifier(identity="other@snoai.com"),
    ],
)
def test_verifier_identity_mismatch_fails_closed(verifier: FakeSigstoreVerifier) -> None:
    source, integrity = _signed_source()

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy(),
            rekor_client=FakeRekorClient(_rekor_entry(integrity)),
            sigstore_verifier=verifier,
        )

    assert exc_info.value.category is ErrorCategory.NoTrustedSignature


def test_verifier_identity_outside_trust_policy_fails_closed() -> None:
    source, integrity = _signed_source()

    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            source,
            schema=MinimalSchema,
            verify_signatures=True,
            trust_policy=_trust_policy("ci@snoai.com"),
            rekor_client=FakeRekorClient(_rekor_entry(integrity)),
            sigstore_verifier=FakeSigstoreVerifier(),
        )

    assert exc_info.value.category is ErrorCategory.NoTrustedSignature


def test_signature_verification_happy_path_succeeds() -> None:
    source, integrity = _signed_source()

    cfg = load_mda_source_from_bytes(
        source,
        schema=MinimalSchema,
        verify_signatures=True,
        trust_policy=_trust_policy(),
        rekor_client=FakeRekorClient(_rekor_entry(integrity)),
        sigstore_verifier=FakeSigstoreVerifier(),
    )

    assert cfg.name == "signed-config"


@pytest.mark.parametrize(
    ("network_yaml", "allowed_networks"),
    [
        ('"none"', []),
        ('"local"', []),
        ('"local"', ["localhost", "127.0.0.1", "10.0.0.1", "service.local"]),
        ('"public"', ["*"]),
        ("\n    - api.openai.com", ["api.openai.com"]),
        ("\n    - api.openai.com", ["*.openai.com"]),
    ],
)
def test_requires_network_passes(network_yaml: str, allowed_networks: list[str]) -> None:
    cfg = load_mda_source_from_bytes(
        _requires_source(network_yaml),
        schema=MinimalSchema,
        enforce_requires=True,
        allowed_networks=allowed_networks,
    )

    assert cfg.name == "requires-config"


@pytest.mark.parametrize(
    ("network_yaml", "allowed_networks"),
    [
        ('"local"', ["api.openai.com"]),
        ('"local"', ["169.254.169.254"]),
        ('"public"', []),
        ("\n    - api.openai.com", ["api.anthropic.com"]),
        ("7", []),
        ("\n    - ''", []),
    ],
)
def test_requires_network_failures(network_yaml: str, allowed_networks: list[str]) -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        load_mda_source_from_bytes(
            _requires_source(network_yaml),
            schema=MinimalSchema,
            enforce_requires=True,
            allowed_networks=allowed_networks,
        )

    assert exc_info.value.category is ErrorCategory.RequiresNotSatisfied
