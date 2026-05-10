use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::errors::{ErrorCategory, MdaConfigError, Result};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TrustPolicy {
    pub version: u8,
    pub trusted_signers: Vec<TrustedSigner>,
    pub min_signatures: Option<usize>,
    pub rekor: Option<RekorPolicy>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RekorPolicy {
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "kebab-case", deny_unknown_fields)]
pub enum TrustedSigner {
    #[serde(rename = "sigstore-oidc")]
    SigstoreOidc { issuer: String, subject: String },
    #[serde(rename = "did-web")]
    DidWeb { domain: String },
}

impl TrustPolicy {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1 {
            return policy_error("trustPolicy.version must be 1");
        }
        if self.trusted_signers.is_empty() {
            return policy_error("trustPolicy.trustedSigners must be non-empty");
        }
        if self.min_signatures.is_some_and(|value| value == 0) {
            return policy_error("trustPolicy.minSignatures must be >= 1");
        }

        let has_sigstore = self
            .trusted_signers
            .iter()
            .any(|signer| matches!(signer, TrustedSigner::SigstoreOidc { .. }));
        let has_did_web = self
            .trusted_signers
            .iter()
            .any(|signer| matches!(signer, TrustedSigner::DidWeb { .. }));

        for signer in &self.trusted_signers {
            match signer {
                TrustedSigner::SigstoreOidc { issuer, subject } => {
                    if issuer.is_empty() || subject.is_empty() {
                        return policy_error(
                            "Sigstore trust policy entries require issuer and subject",
                        );
                    }
                }
                TrustedSigner::DidWeb { domain } => {
                    if domain.is_empty() {
                        return policy_error("did:web trust policy entries require domain");
                    }
                }
            }
        }

        if has_sigstore && self.rekor.as_ref().is_none_or(|rekor| rekor.url.is_empty()) {
            return policy_error("Sigstore trust policy entries require rekor.url");
        }
        if has_did_web && !has_sigstore && self.rekor.is_some() {
            return policy_error("did:web-only trust policy must not include rekor");
        }
        Ok(())
    }

    pub fn required_signatures(&self) -> usize {
        self.min_signatures.unwrap_or(1)
    }

    pub fn contains_did_web(&self) -> bool {
        self.trusted_signers
            .iter()
            .any(|signer| matches!(signer, TrustedSigner::DidWeb { .. }))
    }

    pub fn sigstore_subject_trusted(&self, issuer: &str, subject: &str) -> bool {
        self.trusted_signers.iter().any(|signer| {
            matches!(
                signer,
                TrustedSigner::SigstoreOidc {
                    issuer: trusted_issuer,
                    subject: trusted_subject
                } if trusted_issuer == issuer && trusted_subject == subject
            )
        })
    }

    pub fn sigstore_issuer_trusted(&self, issuer: &str) -> bool {
        self.trusted_signers.iter().any(|signer| {
            matches!(
                signer,
                TrustedSigner::SigstoreOidc {
                    issuer: trusted_issuer,
                    ..
                } if trusted_issuer == issuer
            )
        })
    }

    pub fn did_web_domain_trusted(&self, domain: &str) -> bool {
        self.trusted_signers.iter().any(|signer| {
            matches!(
                signer,
                TrustedSigner::DidWeb {
                    domain: trusted_domain
                } if trusted_domain == domain
            )
        })
    }
}

pub fn validate_trust_policy(value: Value) -> Result<TrustPolicy> {
    let policy: TrustPolicy = serde_json::from_value(value).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::TrustPolicyViolation,
            "trust policy does not match RC2 schema",
            serde_json::json!({ "cause": cause.to_string() }),
        )
    })?;
    policy.validate()?;
    Ok(policy)
}

fn policy_error<T>(message: &str) -> Result<T> {
    Err(MdaConfigError::new(
        ErrorCategory::TrustPolicyViolation,
        message,
    ))
}
