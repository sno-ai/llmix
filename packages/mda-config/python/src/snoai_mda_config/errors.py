"""MDA §11-3 error vocabulary mirrored from the TypeScript loader."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """MDA §11-3 recommended error category vocabulary."""

    InvalidEncoding = "invalid-encoding"
    UnterminatedFrontmatter = "unterminated-frontmatter"
    MissingRequiredFrontmatter = "missing-required-frontmatter"
    FrontmatterYamlParseError = "frontmatter-yaml-parse-error"
    SchemaViolation = "schema-violation"
    SignatureDigestMismatch = "signature-digest-mismatch"
    SignaturesWithoutIntegrity = "signatures-without-integrity"
    IntegrityMismatch = "integrity-mismatch"
    RekorEntryTypeMismatch = "rekor-entry-type-mismatch"
    RekorInclusionFailure = "rekor-inclusion-failure"
    FulcioChainFailure = "fulcio-chain-failure"
    SignatureVerificationFailure = "signature-verification-failure"
    MissingRequiredIntegrity = "missing-required-integrity"
    MissingRequiredSignature = "missing-required-signature"
    NoTrustedSignature = "no-trusted-signature"
    InsufficientTrustedSignatures = "insufficient-trusted-signatures"
    TrustPolicyViolation = "trust-policy-violation"
    UnknownSignerMethod = "unknown-signer-method"
    RequiresNotSatisfied = "requires-not-satisfied"
    ProjectSchemaViolation = "project-schema-violation"

    INVALID_ENCODING = InvalidEncoding
    UNTERMINATED_FRONTMATTER = UnterminatedFrontmatter
    MISSING_REQUIRED_FRONTMATTER = MissingRequiredFrontmatter
    FRONTMATTER_YAML_PARSE_ERROR = FrontmatterYamlParseError
    SCHEMA_VIOLATION = SchemaViolation
    SIGNATURE_DIGEST_MISMATCH = SignatureDigestMismatch
    SIGNATURES_WITHOUT_INTEGRITY = SignaturesWithoutIntegrity
    INTEGRITY_MISMATCH = IntegrityMismatch
    REKOR_ENTRY_TYPE_MISMATCH = RekorEntryTypeMismatch
    REKOR_INCLUSION_FAILURE = RekorInclusionFailure
    FULCIO_CHAIN_FAILURE = FulcioChainFailure
    SIGNATURE_VERIFICATION_FAILURE = SignatureVerificationFailure
    MISSING_REQUIRED_INTEGRITY = MissingRequiredIntegrity
    MISSING_REQUIRED_SIGNATURE = MissingRequiredSignature
    NO_TRUSTED_SIGNATURE = NoTrustedSignature
    INSUFFICIENT_TRUSTED_SIGNATURES = InsufficientTrustedSignatures
    TRUST_POLICY_VIOLATION = TrustPolicyViolation
    UNKNOWN_SIGNER_METHOD = UnknownSignerMethod
    REQUIRES_NOT_SATISFIED = RequiresNotSatisfied
    PROJECT_SCHEMA_VIOLATION = ProjectSchemaViolation


class MdaConfigError(Exception):
    """MDA §11-3 structured error raised by public loader functions."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{category.value}] {message}")
        self.category = category
        self.details = details or {}
