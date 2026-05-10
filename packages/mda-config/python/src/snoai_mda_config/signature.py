"""DSSE PAE and trusted signature evaluation."""

# pyright: reportUnusedImport=false

from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from typing import NoReturn

import jcs  # type: ignore[reportMissingTypeStubs]

from .errors import ErrorCategory, MdaConfigError
from .integrity import IntegrityField
from .signature_parsing import (  # noqa: F401
    DEFAULT_PAYLOAD_TYPE,
    declared_payload_type,
    parse_did_web_signer,
    parse_sigstore_signer,
)
from .signature_types import (  # noqa: F401
    DidWebVerificationInput,
    DidWebVerifier,
    DsseEnvelope,
    DsseSignature,
    RekorClient,
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
    SigstoreVerifier,
)
from .trust_policy import (
    DidWebTrustedSigner as DidWebTrustedSigner,
)
from .trust_policy import (
    SigstoreTrustedSigner as SigstoreTrustedSigner,
)
from .trust_policy import (
    TrustedSigner as TrustedSigner,
)
from .trust_policy import (
    TrustPolicy,
    policy_contains_did_web,
    sigstore_subjects_for,
    trusts_did_web_domain,
    validate_trust_policy,
)


def construct_dsse_pae(payload_type: str, payload_bytes: bytes) -> bytes:
    """MDA §09 constructs DSSE PAE bytes."""

    head = f"DSSEv1 {len(payload_type)} {payload_type} {len(payload_bytes)} ".encode()
    return head + payload_bytes


def verify_signatures(
    signatures: Sequence[SignatureEntry],
    integrity: IntegrityField,
    trust_policy: object,
    *,
    rekor_client: RekorClient | None = None,
    sigstore_verifier: SigstoreVerifier | None = None,
    did_web_verifier: DidWebVerifier | None = None,
) -> None:
    """Evaluate signatures against a trust policy threshold."""

    policy = (
        trust_policy
        if isinstance(trust_policy, TrustPolicy)
        else validate_trust_policy(trust_policy)
    )
    _assert_verifier_hooks(policy, rekor_client, sigstore_verifier, did_web_verifier)
    if not signatures:
        raise MdaConfigError(
            ErrorCategory.MissingRequiredSignature,
            "trusted-runtime requires a non-empty signatures[] field",
        )

    payload_bytes = _canonical_json(integrity)
    trusted: set[str] = set()
    candidate_errors: list[MdaConfigError] = []
    for sig in signatures:
        _validate_signature_shape(sig)
        _assert_payload_digest(sig, integrity)
        try:
            identity = _verify_candidate(
                sig,
                payload_bytes,
                policy,
                rekor_client=rekor_client,
                sigstore_verifier=sigstore_verifier,
                did_web_verifier=did_web_verifier,
            )
            if identity is not None:
                trusted.add(identity)
        except MdaConfigError as cause:
            candidate_errors.append(cause)
        except Exception as cause:
            candidate_errors.append(
                MdaConfigError(
                    ErrorCategory.SignatureVerificationFailure,
                    "signature candidate verification failed",
                    {"cause": str(cause)},
                )
            )

    required = policy.min_signatures
    if len(trusted) >= required:
        return
    if trusted:
        raise MdaConfigError(
            ErrorCategory.InsufficientTrustedSignatures,
            "trusted signatures did not satisfy minSignatures",
            {"trusted": len(trusted), "required": required},
        )
    raise _most_specific_candidate_error(candidate_errors)


def _assert_verifier_hooks(
    policy: TrustPolicy,
    rekor_client: RekorClient | None,
    sigstore_verifier: SigstoreVerifier | None,
    did_web_verifier: DidWebVerifier | None,
) -> None:
    if any(signer.type == "sigstore-oidc" for signer in policy.trusted_signers):
        if rekor_client is None or sigstore_verifier is None:
            raise MdaConfigError(
                ErrorCategory.TrustPolicyViolation,
                "Sigstore trusted-runtime requires rekor_client and sigstore_verifier hooks",
            )
        client_url = getattr(rekor_client, "rekor_url", None)
        if client_url is not None and client_url != policy.rekor_url:
            raise MdaConfigError(
                ErrorCategory.TrustPolicyViolation,
                "Rekor client URL does not match trustPolicy.rekor.url",
                {"policyUrl": policy.rekor_url, "clientUrl": client_url},
            )
    if policy_contains_did_web(policy) and did_web_verifier is None:
        raise MdaConfigError(
            ErrorCategory.TrustPolicyViolation,
            "did:web trusted-runtime requires a did_web_verifier hook",
        )


def _verify_candidate(
    sig: SignatureEntry,
    payload_bytes: bytes,
    policy: TrustPolicy,
    *,
    rekor_client: RekorClient | None,
    sigstore_verifier: SigstoreVerifier | None,
    did_web_verifier: DidWebVerifier | None,
) -> str | None:
    if sig["signer"].startswith("sigstore-oidc:"):
        return _verify_sigstore_candidate(
            sig,
            payload_bytes,
            policy,
            rekor_client,
            sigstore_verifier,
        )
    if sig["signer"].startswith("did-web:"):
        return _verify_did_web_candidate(sig, payload_bytes, policy, did_web_verifier)
    raise MdaConfigError(
        ErrorCategory.UnknownSignerMethod,
        "unknown signer method",
        {"signer": sig["signer"]},
    )


def _verify_sigstore_candidate(
    sig: SignatureEntry,
    payload_bytes: bytes,
    policy: TrustPolicy,
    rekor_client: RekorClient | None,
    sigstore_verifier: SigstoreVerifier | None,
) -> str | None:
    issuer = parse_sigstore_signer(sig["signer"])
    payload_type = declared_payload_type(sig)
    pae_bytes = construct_dsse_pae(payload_type, payload_bytes)
    entry = _fetch_rekor_entry(sig, policy, rekor_client)
    _validate_rekor_dsse_envelope(entry, sig, payload_type, payload_bytes)
    verification = sigstore_verifier.verify(entry, sig, pae_bytes)  # type: ignore[union-attr]
    verified_issuer = verification.issuer
    verified_subject = (
        verification.subject
        or verification.subject_alternative_name
        or _extract_cert_identity(verification.certificate_pem or "")
    )
    if not verified_issuer or not verified_subject:
        raise MdaConfigError(
            ErrorCategory.SignatureVerificationFailure,
            "Sigstore verifier did not return verified issuer and subject",
            {"signer": sig["signer"]},
        )
    if verified_issuer != issuer:
        return None
    if verified_subject not in sigstore_subjects_for(policy, verified_issuer):
        return None
    return f"sigstore-oidc\0{verified_issuer}\0{verified_subject}"


def _verify_did_web_candidate(
    sig: SignatureEntry,
    payload_bytes: bytes,
    policy: TrustPolicy,
    did_web_verifier: DidWebVerifier | None,
) -> str | None:
    domain = parse_did_web_signer(sig["signer"])
    if not trusts_did_web_domain(policy, domain):
        return None
    payload_type = declared_payload_type(sig)
    ok = did_web_verifier.verify(  # type: ignore[union-attr]
        DidWebVerificationInput(
            domain=domain,
            key_id=sig["key-id"],
            algorithm=sig["algorithm"],
            signature=sig["signature"],
            payload_type=payload_type,
            payload_bytes=payload_bytes,
            pae_bytes=construct_dsse_pae(payload_type, payload_bytes),
        )
    )
    if not ok:
        return None
    return f"did-web\0{domain}"


def _fetch_rekor_entry(
    sig: SignatureEntry,
    policy: TrustPolicy,
    rekor_client: RekorClient | None,
) -> RekorEntry:
    rekor_url = policy.rekor_url
    if rekor_url is None:
        raise MdaConfigError(
            ErrorCategory.TrustPolicyViolation,
            "Sigstore trusted-runtime requires trustPolicy.rekor.url",
        )
    log_id = sig.get("rekor-log-id")
    log_index = sig.get("rekor-log-index")
    if not isinstance(log_id, str) or not isinstance(log_index, int):
        raise MdaConfigError(
            ErrorCategory.RekorInclusionFailure,
            "Sigstore signature requires Rekor coordinates",
        )
    entry = rekor_client.fetch_entry(rekor_url, log_id, log_index)  # type: ignore[union-attr]
    if entry is None:
        raise MdaConfigError(
            ErrorCategory.RekorInclusionFailure,
            "Rekor entry not found for the supplied log coordinates",
            {"logId": log_id, "logIndex": log_index},
        )
    if entry.get("log_id") is not None and entry.get("log_id") != log_id:
        _rekor_inclusion_failure("Rekor entry log ID does not match signature", sig)
    if entry.get("log_index") is not None and entry.get("log_index") != log_index:
        _rekor_inclusion_failure("Rekor entry log index does not match signature", sig)
    if entry.get("inclusion_verified") is False:
        _rekor_inclusion_failure("Rekor inclusion proof did not verify", sig)
    if entry.get("kind") != "dsse-v0.0.1":
        raise MdaConfigError(
            ErrorCategory.RekorEntryTypeMismatch,
            "Rekor entry kind is not dsse-v0.0.1",
            {"logId": log_id, "logIndex": log_index, "kind": entry.get("kind")},
        )
    return entry


def _validate_signature_shape(sig: SignatureEntry) -> None:
    declared_payload_type(sig)
    if sig["signer"].startswith("sigstore-oidc:"):
        parse_sigstore_signer(sig["signer"])
        if not sig.get("rekor-log-id") or sig.get("rekor-log-index") is None:
            raise MdaConfigError(
                ErrorCategory.RekorInclusionFailure,
                "Sigstore signature requires rekor-log-id and rekor-log-index",
                {"signer": sig["signer"]},
            )
        return
    if sig["signer"].startswith("did-web:"):
        parse_did_web_signer(sig["signer"])
        if "rekor-log-id" in sig or "rekor-log-index" in sig:
            raise MdaConfigError(
                ErrorCategory.SchemaViolation,
                "did:web signature must not include Rekor fields",
                {"signer": sig["signer"]},
            )
        return
    raise MdaConfigError(
        ErrorCategory.UnknownSignerMethod,
        "unknown signer method",
        {"signer": sig["signer"]},
    )


def _assert_payload_digest(sig: SignatureEntry, integrity: IntegrityField) -> None:
    if sig["payload-digest"] != integrity["digest"]:
        raise MdaConfigError(
            ErrorCategory.SignatureDigestMismatch,
            "signature payload-digest does not equal integrity.digest",
            {"signer": sig["signer"]},
        )


def _validate_rekor_dsse_envelope(
    entry: RekorEntry,
    sig: SignatureEntry,
    payload_type: str,
    payload_bytes: bytes,
) -> None:
    envelope = entry.get("dsse_envelope")
    if envelope is None:
        _rekor_inclusion_failure("Rekor entry is missing DSSE envelope", sig)
    if envelope["payload_type"] != payload_type:
        _rekor_inclusion_failure("Rekor DSSE envelope payloadType mismatch", sig)
    if envelope["payload"] != base64.b64encode(payload_bytes).decode("ascii"):
        _rekor_inclusion_failure("Rekor DSSE envelope payload mismatch", sig)
    if not any(
        candidate.get("sig") == sig["signature"] and candidate.get("keyid") == sig["key-id"]
        for candidate in envelope["signatures"]
    ):
        _rekor_inclusion_failure("Rekor DSSE envelope does not contain signature/key-id", sig)


def _canonical_json(value: object) -> bytes:
    raw = jcs.canonicalize(value)  # type: ignore[reportUnknownMemberType]
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


def _most_specific_candidate_error(errors: list[MdaConfigError]) -> MdaConfigError:
    for category in (
        ErrorCategory.RekorEntryTypeMismatch,
        ErrorCategory.RekorInclusionFailure,
        ErrorCategory.FulcioChainFailure,
        ErrorCategory.SignatureVerificationFailure,
    ):
        for error in errors:
            if error.category is category:
                return error
    return MdaConfigError(
        ErrorCategory.NoTrustedSignature,
        "no cryptographically verified signature matched the trust policy",
    )


def _rekor_inclusion_failure(message: str, sig: SignatureEntry) -> NoReturn:
    raise MdaConfigError(ErrorCategory.RekorInclusionFailure, message, {"signer": sig["signer"]})


def _extract_cert_identity(cert_pem: str) -> str | None:
    match = re.search(r"(?:email|uri):([^,\n]+)", cert_pem, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None
