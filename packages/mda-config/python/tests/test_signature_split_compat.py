from __future__ import annotations

import pytest

from snoai_mda_config import ErrorCategory, MdaConfigError, validate_trust_policy
from snoai_mda_config.signature import (
    DidWebTrustedSigner,
    SigstoreTrustedSigner,
    TrustPolicy,
    TrustedSigner,
    verify_signatures,
)


def test_signature_module_reexports_trust_policy_construction_types() -> None:
    sigstore = SigstoreTrustedSigner(
        type="sigstore-oidc",
        issuer="https://issuer.example.com",
        subject="repo:llmix/llmix:ref:refs/heads/main",
    )
    did_web = DidWebTrustedSigner(type="did-web", domain="trusted.example.com")
    signers: tuple[TrustedSigner, ...] = (sigstore, did_web)
    policy = TrustPolicy(version=1, trusted_signers=signers, rekor_url="https://rekor.example.com")

    assert policy.trusted_signers == signers


def test_trust_policy_rejects_boolean_min_signatures() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        validate_trust_policy(
            {
                "version": 1,
                "trustedSigners": [{"type": "did-web", "domain": "trusted.example.com"}],
                "minSignatures": True,
            }
        )

    assert exc_info.value.category is ErrorCategory.TrustPolicyViolation


def test_signature_rejects_non_string_payload_type_as_schema_error() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        verify_signatures(
            [
                {
                    "signer": "did-web:trusted.example.com",
                    "key-id": "did:web:trusted.example.com#key-1",
                    "payload-digest": "sha256:" + "0" * 64,
                    "algorithm": "ed25519",
                    "signature": "signature",
                    "payload-type": 123,
                }
            ],
            {"algorithm": "sha256", "digest": "sha256:" + "0" * 64},
            {
                "version": 1,
                "trustedSigners": [{"type": "did-web", "domain": "trusted.example.com"}],
            },
            did_web_verifier=_RejectingDidWebVerifier(),
        )

    assert exc_info.value.category is ErrorCategory.SchemaViolation


class _RejectingDidWebVerifier:
    def __init__(self) -> None:
        self.called = False

    def verify(self, input_value: object) -> bool:
        self.called = True
        return True


def test_untrusted_did_web_signer_is_filtered_before_verifier_io() -> None:
    verifier = _RejectingDidWebVerifier()

    with pytest.raises(MdaConfigError) as exc_info:
        verify_signatures(
            [
                {
                    "signer": "did-web:evil.example.com",
                    "key-id": "did:web:evil.example.com#key-1",
                    "payload-digest": "sha256:" + "0" * 64,
                    "algorithm": "ed25519",
                    "signature": "attacker-signature",
                }
            ],
            {"algorithm": "sha256", "digest": "sha256:" + "0" * 64},
            {
                "version": 1,
                "trustedSigners": [{"type": "did-web", "domain": "trusted.example.com"}],
            },
            did_web_verifier=verifier,
        )

    assert exc_info.value.category is ErrorCategory.NoTrustedSignature
    assert verifier.called is False
