"""Python loader for MDA v1.0 source-mode configuration artifacts."""

from .errors import ErrorCategory, MdaConfigError
from .frontmatter import ExtractedFrontmatter, extract_frontmatter, parse_frontmatter_yaml
from .integrity import (
    IntegrityField,
    canonicalize_artifact,
    hash_canonical,
    normalize_body,
    parse_digest,
    verify_integrity,
)
from .loader import load_mda_source, load_mda_source_from_bytes
from .requires import RequiresEnvironment, enforce_requires
from .signature import (
    DEFAULT_PAYLOAD_TYPE,
    DidWebVerificationInput,
    DidWebVerifier,
    DsseEnvelope,
    DsseSignature,
    RekorClient,
    RekorEntry,
    SignatureEntry,
    SigstoreVerificationResult,
    SigstoreVerifier,
    construct_dsse_pae,
    verify_signatures,
)
from .trust_policy import (
    DidWebTrustedSigner,
    SigstoreTrustedSigner,
    TrustedSigner,
    TrustPolicy,
    validate_trust_policy,
)

__all__ = [
    "ErrorCategory",
    "ExtractedFrontmatter",
    "IntegrityField",
    "MdaConfigError",
    "DEFAULT_PAYLOAD_TYPE",
    "DidWebTrustedSigner",
    "DidWebVerificationInput",
    "DidWebVerifier",
    "DsseEnvelope",
    "DsseSignature",
    "RekorClient",
    "RekorEntry",
    "RequiresEnvironment",
    "SignatureEntry",
    "SigstoreTrustedSigner",
    "SigstoreVerificationResult",
    "SigstoreVerifier",
    "TrustPolicy",
    "TrustedSigner",
    "canonicalize_artifact",
    "construct_dsse_pae",
    "enforce_requires",
    "extract_frontmatter",
    "hash_canonical",
    "load_mda_source",
    "load_mda_source_from_bytes",
    "normalize_body",
    "parse_digest",
    "parse_frontmatter_yaml",
    "verify_integrity",
    "verify_signatures",
    "validate_trust_policy",
]
