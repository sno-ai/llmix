"""Signature verification data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

SignatureEntry = TypedDict(
    "SignatureEntry",
    {
        "signer": str,
        "key-id": str,
        "payload-digest": str,
        "algorithm": str,
        "signature": str,
        "rekor-log-id": NotRequired[str],
        "rekor-log-index": NotRequired[int],
        "payload-type": NotRequired[str],
    },
)


class DsseSignature(TypedDict, total=False):
    """One signature inside a Rekor DSSE envelope."""

    sig: str
    keyid: str


class DsseEnvelope(TypedDict):
    """Rekor-indexed DSSE envelope subset used by MDA verification."""

    payload_type: str
    payload: str
    signatures: list[DsseSignature]


class RekorEntry(TypedDict, total=False):
    """Rekor dsse-v0.0.1 entry subset used by MDA verification."""

    kind: str
    log_id: str
    log_index: int
    inclusion_verified: bool
    certificate_pem: str
    dsse_envelope: DsseEnvelope


@dataclass(frozen=True)
class SigstoreVerificationResult:
    """Verified identity returned by an injected Sigstore verifier."""

    certificate_pem: str | None = None
    issuer: str | None = None
    subject: str | None = None
    subject_alternative_name: str | None = None


@dataclass(frozen=True)
class DidWebVerificationInput:
    """Input passed to a did:web verifier hook."""

    domain: str
    key_id: str
    algorithm: str
    signature: str
    payload_type: str
    payload_bytes: bytes
    pae_bytes: bytes


class RekorClient(Protocol):
    """Pluggable Rekor lookup used by signature verification."""

    def fetch_entry(self, rekor_url: str, log_id: str, log_index: int) -> RekorEntry | None:
        """Fetch a Rekor entry by policy URL and log coordinates."""
        ...


class SigstoreVerifier(Protocol):
    """Injected Sigstore verifier hook for Fulcio, inclusion, and signature crypto."""

    def verify(
        self,
        entry: RekorEntry,
        signature: SignatureEntry,
        pae_bytes: bytes,
    ) -> SigstoreVerificationResult:
        """Verify one Rekor entry/signature/PAE tuple."""
        ...


class DidWebVerifier(Protocol):
    """Injected did:web cryptographic verifier hook."""

    def verify(self, input_value: DidWebVerificationInput) -> bool:
        """Return true only for a cryptographically valid did:web signature."""
        ...
