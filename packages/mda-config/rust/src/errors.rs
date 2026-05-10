use serde_json::Value;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, MdaConfigError>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ErrorCategory {
    InvalidEncoding,
    UnterminatedFrontmatter,
    MissingRequiredFrontmatter,
    FrontmatterYamlParseError,
    SchemaViolation,
    SignatureDigestMismatch,
    SignaturesWithoutIntegrity,
    MissingRequiredIntegrity,
    MissingRequiredSignature,
    IntegrityMismatch,
    RekorEntryTypeMismatch,
    RekorInclusionFailure,
    FulcioChainFailure,
    SignatureVerificationFailure,
    NoTrustedSignature,
    InsufficientTrustedSignatures,
    TrustPolicyViolation,
    UnknownSignerMethod,
    RequiresNotSatisfied,
    ProjectSchemaViolation,
}

impl ErrorCategory {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::InvalidEncoding => "invalid-encoding",
            Self::UnterminatedFrontmatter => "unterminated-frontmatter",
            Self::MissingRequiredFrontmatter => "missing-required-frontmatter",
            Self::FrontmatterYamlParseError => "frontmatter-yaml-parse-error",
            Self::SchemaViolation => "schema-violation",
            Self::SignatureDigestMismatch => "signature-digest-mismatch",
            Self::SignaturesWithoutIntegrity => "signatures-without-integrity",
            Self::MissingRequiredIntegrity => "missing-required-integrity",
            Self::MissingRequiredSignature => "missing-required-signature",
            Self::IntegrityMismatch => "integrity-mismatch",
            Self::RekorEntryTypeMismatch => "rekor-entry-type-mismatch",
            Self::RekorInclusionFailure => "rekor-inclusion-failure",
            Self::FulcioChainFailure => "fulcio-chain-failure",
            Self::SignatureVerificationFailure => "signature-verification-failure",
            Self::NoTrustedSignature => "no-trusted-signature",
            Self::InsufficientTrustedSignatures => "insufficient-trusted-signatures",
            Self::TrustPolicyViolation => "trust-policy-violation",
            Self::UnknownSignerMethod => "unknown-signer-method",
            Self::RequiresNotSatisfied => "requires-not-satisfied",
            Self::ProjectSchemaViolation => "project-schema-violation",
        }
    }
}

impl std::fmt::Display for ErrorCategory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Error)]
#[error("[{category}] {message}")]
pub struct MdaConfigError {
    pub category: ErrorCategory,
    pub message: String,
    pub details: Value,
}

impl MdaConfigError {
    pub fn new(category: ErrorCategory, message: impl Into<String>) -> Self {
        Self {
            category,
            message: message.into(),
            details: Value::Object(Default::default()),
        }
    }

    pub fn with_details(
        category: ErrorCategory,
        message: impl Into<String>,
        details: Value,
    ) -> Self {
        Self {
            category,
            message: message.into(),
            details,
        }
    }
}
