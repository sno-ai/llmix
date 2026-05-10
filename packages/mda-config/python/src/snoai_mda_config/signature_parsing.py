"""Signature parser helpers."""

from __future__ import annotations

import re

from .errors import ErrorCategory, MdaConfigError
from .signature_types import SignatureEntry

DEFAULT_PAYLOAD_TYPE = "application/vnd.mda.integrity+json"
_PAYLOAD_TYPE_RE = re.compile(
    r"^application/vnd\.[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+\+json$"
)


def parse_sigstore_signer(signer: str) -> str:
    prefix = "sigstore-oidc:"
    if not signer.startswith(prefix) or "#" in signer:
        raise MdaConfigError(
            ErrorCategory.UnknownSignerMethod,
            "Sigstore signer must be 'sigstore-oidc:<issuer>' with no subject suffix",
            {"signer": signer},
        )
    issuer = signer[len(prefix) :]
    if not issuer:
        raise MdaConfigError(ErrorCategory.UnknownSignerMethod, "Sigstore signer issuer is empty")
    return issuer


def parse_did_web_signer(signer: str) -> str:
    prefix = "did-web:"
    if not signer.startswith(prefix) or "#" in signer:
        raise MdaConfigError(
            ErrorCategory.UnknownSignerMethod,
            "did:web signer must be 'did-web:<domain>'",
            {"signer": signer},
        )
    domain = signer[len(prefix) :]
    if not domain:
        raise MdaConfigError(ErrorCategory.UnknownSignerMethod, "did:web signer domain is empty")
    return domain


def declared_payload_type(sig: SignatureEntry) -> str:
    payload_type = sig.get("payload-type", DEFAULT_PAYLOAD_TYPE)
    if (
        not isinstance(payload_type, str)
        or "+jcs+json" in payload_type
        or _PAYLOAD_TYPE_RE.fullmatch(payload_type) is None
    ):
        raise MdaConfigError(
            ErrorCategory.SchemaViolation,
            "signature payload-type must be application/vnd.<vendor>.<doc-type>+json",
            {"signer": sig["signer"], "payloadType": payload_type},
        )
    return payload_type
