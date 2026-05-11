use super::root::normalize_sha256_digest;
use super::root_verify::compare_revision;
use super::{RegistryRootHighWatermark, RegistryRootVerificationOptions};
use crate::error::{
    ConfigAccessError, ConfigNotFoundError, InvalidConfigError, LlmixError, LlmixResult,
};
use chrono::DateTime;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use snoai_mda_config::{DidWebVerifier, RekorClient, SigstoreVerifier, TrustPolicy};
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::sync::Arc;

pub const LLMIX_TRUST_MANIFEST_KIND: &str = "llmix-trust-manifest";
pub const LLMIX_TRUST_MANIFEST_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmixTrustManifestRegistryRoot {
    pub path: String,
    pub revision: String,
    pub published_at: String,
    pub high_watermark: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmixTrustManifestReleasePlan {
    pub path: String,
    pub source_count: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmixTrustManifest {
    pub version: u32,
    pub kind: String,
    pub expected_root_digest: String,
    pub source_set_digest: String,
    pub release_plan_digest: String,
    pub registry_root_trust_policy: TrustPolicy,
    pub rekor_policy: Option<Value>,
    pub minimum_revision: Option<String>,
    pub minimum_published_at: Option<String>,
    pub high_watermark: Option<String>,
    pub registry_root_signer_identity: Value,
    pub registry_root: LlmixTrustManifestRegistryRoot,
    pub release_plan: LlmixTrustManifestReleasePlan,
}

pub fn load_llmix_trust_manifest(path: impl AsRef<Path>) -> LlmixResult<LlmixTrustManifest> {
    let path = path.as_ref();
    let content = fs::read_to_string(path).map_err(|error| match error.kind() {
        ErrorKind::NotFound => LlmixError::from(ConfigNotFoundError {
            path: path.display().to_string(),
        }),
        ErrorKind::PermissionDenied => LlmixError::from(ConfigAccessError {
            path: path.display().to_string(),
        }),
        _ => LlmixError::from(error),
    })?;
    let manifest: LlmixTrustManifest =
        serde_json::from_str(&content).map_err(|error| InvalidConfigError {
            message: format!("Invalid LLMix trust manifest {}: {error}", path.display()),
        })?;
    validate_llmix_trust_manifest(manifest, &path.display().to_string())
}

pub fn registry_root_options_from_trust_manifest(
    manifest: &LlmixTrustManifest,
) -> LlmixResult<RegistryRootVerificationOptions> {
    registry_root_options_from_trust_manifest_with_hooks(manifest, None, None, None, None)
}

pub fn registry_root_options_from_trust_manifest_with_hooks(
    manifest: &LlmixTrustManifest,
    rekor_client: Option<Arc<dyn RekorClient + Send + Sync>>,
    sigstore_verifier: Option<Arc<dyn SigstoreVerifier + Send + Sync>>,
    did_web_verifier: Option<Arc<dyn DidWebVerifier + Send + Sync>>,
    high_watermark: Option<Arc<dyn RegistryRootHighWatermark>>,
) -> LlmixResult<RegistryRootVerificationOptions> {
    let minimum_revision = minimum_revision_from_manifest(manifest);
    Ok(RegistryRootVerificationOptions {
        trust_policy: manifest.registry_root_trust_policy.clone(),
        rekor_client,
        sigstore_verifier,
        did_web_verifier,
        expected_revision: Some(manifest.registry_root.revision.clone()),
        expected_root_digest: Some(normalize_sha256_digest(
            &manifest.expected_root_digest,
            "LLMix trust manifest expectedRootDigest",
        )?),
        minimum_revision,
        minimum_published_at: manifest.minimum_published_at.clone(),
        high_watermark,
    })
}

fn minimum_revision_from_manifest(manifest: &LlmixTrustManifest) -> Option<String> {
    match (&manifest.minimum_revision, &manifest.high_watermark) {
        (None, None) => None,
        (Some(revision), None) | (None, Some(revision)) => Some(revision.clone()),
        (Some(minimum_revision), Some(high_watermark)) => {
            if compare_revision(minimum_revision, high_watermark) >= 0 {
                Some(minimum_revision.clone())
            } else {
                Some(high_watermark.clone())
            }
        }
    }
}

fn validate_llmix_trust_manifest(
    manifest: LlmixTrustManifest,
    source_path: &str,
) -> LlmixResult<LlmixTrustManifest> {
    if manifest.kind != LLMIX_TRUST_MANIFEST_KIND {
        return Err(InvalidConfigError {
            message: format!("Invalid LLMix trust manifest kind in {source_path}"),
        }
        .into());
    }
    if manifest.version != LLMIX_TRUST_MANIFEST_VERSION {
        return Err(InvalidConfigError {
            message: format!("Invalid LLMix trust manifest version in {source_path}"),
        }
        .into());
    }
    validate_prefixed_digest(&manifest.expected_root_digest, "expectedRootDigest")?;
    validate_prefixed_digest(&manifest.source_set_digest, "sourceSetDigest")?;
    validate_prefixed_digest(&manifest.release_plan_digest, "releasePlanDigest")?;
    require_non_empty(&manifest.registry_root.path, "registryRoot.path")?;
    require_non_empty(&manifest.registry_root.revision, "registryRoot.revision")?;
    require_iso_timestamp(
        &manifest.registry_root.published_at,
        "registryRoot.publishedAt",
    )?;
    require_non_empty(
        &manifest.registry_root.high_watermark,
        "registryRoot.highWatermark",
    )?;
    require_non_empty(&manifest.release_plan.path, "releasePlan.path")?;
    if let Some(value) = &manifest.minimum_revision {
        require_non_empty(value, "minimumRevision")?;
    }
    if let Some(value) = &manifest.minimum_published_at {
        require_iso_timestamp(value, "minimumPublishedAt")?;
    }
    if let Some(value) = &manifest.high_watermark {
        require_non_empty(value, "highWatermark")?;
    }
    Ok(manifest)
}

fn validate_prefixed_digest(value: &str, label: &str) -> LlmixResult<()> {
    if !value.starts_with("sha256:") {
        return Err(InvalidConfigError {
            message: format!("LLMix trust manifest {label} must be a sha256 digest"),
        }
        .into());
    }
    normalize_sha256_digest(value, label).map(|_| ())
}

fn require_non_empty(value: &str, label: &str) -> LlmixResult<()> {
    if value.is_empty() {
        return Err(InvalidConfigError {
            message: format!("LLMix trust manifest {label} must be a non-empty string"),
        }
        .into());
    }
    Ok(())
}

fn require_iso_timestamp(value: &str, label: &str) -> LlmixResult<()> {
    if value.is_empty() || DateTime::parse_from_rfc3339(value).is_err() {
        return Err(InvalidConfigError {
            message: format!("LLMix trust manifest {label} must be an ISO timestamp"),
        }
        .into());
    }
    Ok(())
}
