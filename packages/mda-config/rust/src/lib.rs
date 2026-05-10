//! Rust loader for MDA v1.0 source-mode configuration artifacts.

mod errors;
mod frontmatter;
mod integrity;
mod loader;
mod requires;
mod signature;
mod trust_policy;

pub use errors::{ErrorCategory, MdaConfigError, Result};
pub use frontmatter::{extract_frontmatter, parse_frontmatter_yaml, ExtractedFrontmatter};
pub use integrity::{
    canonicalize_artifact, hash_canonical, normalize_body, parse_digest, verify_integrity,
    HashAlgorithm, IntegrityField,
};
pub use loader::{load_mda_source, load_mda_source_from_bytes, LoadMdaSourceOptions};
pub use requires::{enforce_requires, RequiresEnvironment};
pub use signature::{
    construct_dsse_pae, verify_signatures, DidWebVerificationInput, DidWebVerifier, DsseEnvelope,
    DsseSignature, RekorClient, RekorEntry, SignatureEntry, SigstoreVerificationResult,
    SigstoreVerifier, DEFAULT_PAYLOAD_TYPE,
};
pub use trust_policy::{validate_trust_policy, RekorPolicy, TrustPolicy, TrustedSigner};
