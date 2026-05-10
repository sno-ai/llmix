"""MDA v1.0.0-rc.2 trust policy validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn, cast

from .errors import ErrorCategory, MdaConfigError


@dataclass(frozen=True)
class SigstoreTrustedSigner:
    """RC2 Sigstore OIDC trust-policy signer."""

    type: Literal["sigstore-oidc"]
    issuer: str
    subject: str


@dataclass(frozen=True)
class DidWebTrustedSigner:
    """RC2 did:web trust-policy signer."""

    type: Literal["did-web"]
    domain: str


TrustedSigner = SigstoreTrustedSigner | DidWebTrustedSigner


@dataclass(frozen=True)
class TrustPolicy:
    """Validated RC2 trust policy."""

    version: Literal[1]
    trusted_signers: tuple[TrustedSigner, ...]
    min_signatures: int = 1
    rekor_url: str | None = None


def validate_trust_policy(input_value: object) -> TrustPolicy:
    """Validate and normalize an RC2 trust policy."""

    policy = _require_dict(input_value, "trustPolicy")
    _reject_unknown(policy, {"version", "trustedSigners", "minSignatures", "rekor"}, "trustPolicy")
    if policy.get("version") != 1:
        _violation("trustPolicy.version must be 1")
    trusted_signers_raw = policy.get("trustedSigners")
    if not isinstance(trusted_signers_raw, list):
        _violation("trustPolicy.trustedSigners must be a non-empty array")
    trusted_signers_raw = cast("list[object]", trusted_signers_raw)
    if len(trusted_signers_raw) == 0:
        _violation("trustPolicy.trustedSigners must be a non-empty array")
    min_signatures = policy.get("minSignatures", 1)
    if not isinstance(min_signatures, int) or min_signatures < 1:
        _violation("trustPolicy.minSignatures must be an integer >= 1")

    trusted_signers = tuple(_validate_signer(signer) for signer in trusted_signers_raw)
    rekor_url = _validate_rekor(policy.get("rekor"))
    has_sigstore = any(signer.type == "sigstore-oidc" for signer in trusted_signers)
    has_did_web_only = all(signer.type == "did-web" for signer in trusted_signers)
    if has_sigstore and rekor_url is None:
        _violation("Sigstore trust policy entries require rekor.url")
    if has_did_web_only and rekor_url is not None:
        _violation("did-web-only trust policies must not include rekor")
    return TrustPolicy(
        version=1,
        trusted_signers=trusted_signers,
        min_signatures=min_signatures,
        rekor_url=rekor_url,
    )


def policy_contains_did_web(policy: TrustPolicy) -> bool:
    """Return whether a policy contains did:web signers."""

    return any(signer.type == "did-web" for signer in policy.trusted_signers)


def sigstore_subjects_for(policy: TrustPolicy, issuer: str) -> set[str]:
    """Return exact trusted Sigstore subjects for an issuer."""

    return {
        signer.subject
        for signer in policy.trusted_signers
        if signer.type == "sigstore-oidc" and signer.issuer == issuer
    }


def trusts_did_web_domain(policy: TrustPolicy, domain: str) -> bool:
    """Return whether a did:web domain is trusted by policy."""

    return any(
        signer.type == "did-web" and signer.domain == domain
        for signer in policy.trusted_signers
    )


def _validate_signer(input_value: object) -> TrustedSigner:
    signer = _require_dict(input_value, "trustedSigners[]")
    if signer.get("type") == "sigstore-oidc":
        _reject_unknown(signer, {"type", "issuer", "subject"}, "sigstore signer")
        issuer = signer.get("issuer")
        subject = signer.get("subject")
        if not isinstance(issuer, str) or len(issuer) == 0:
            _violation("Sigstore trusted signer requires non-empty issuer and subject")
        if not isinstance(subject, str) or len(subject) == 0:
            _violation("Sigstore trusted signer requires non-empty issuer and subject")
        return SigstoreTrustedSigner(type="sigstore-oidc", issuer=issuer, subject=subject)
    if signer.get("type") == "did-web":
        _reject_unknown(signer, {"type", "domain"}, "did-web signer")
        domain = signer.get("domain")
        if not isinstance(domain, str) or len(domain) == 0:
            _violation("did-web trusted signer requires non-empty domain")
        return DidWebTrustedSigner(type="did-web", domain=domain)
    _violation("trusted signer type must be sigstore-oidc or did-web")


def _validate_rekor(input_value: object | None) -> str | None:
    if input_value is None:
        return None
    rekor = _require_dict(input_value, "rekor")
    _reject_unknown(rekor, {"url"}, "rekor")
    url = rekor.get("url")
    if not isinstance(url, str) or len(url) == 0:
        _violation("rekor.url must be non-empty")
    return url


def _require_dict(input_value: object, label: str) -> dict[str, object]:
    if not isinstance(input_value, dict):
        _violation(f"{label} must be an object")
    return cast("dict[str, object]", input_value)


def _reject_unknown(value: dict[str, object], allowed: set[str], label: str) -> None:
    for key in value:
        if key not in allowed:
            _violation(f"{label} has unknown field '{key}'")


def _violation(message: str) -> NoReturn:
    raise MdaConfigError(ErrorCategory.TrustPolicyViolation, message)
