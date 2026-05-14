use crate::canonical_json;
use crate::config::{
    load_config_with_options, validate_module, validate_preset, MdaConfigLoadOptions,
};
use crate::error::{
    ConfigAccessError, ConfigNotFoundError, InvalidConfigError, LlmixError, LlmixResult,
    SecurityError,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use snoai_mda_config::{
    verify_signatures_with_payload, DidWebVerifier, HashAlgorithm, IntegrityField, RekorClient,
    SignatureEntry, SigstoreVerifier, TrustPolicy,
};
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, File};
use std::io::{ErrorKind, Read};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

const MANIFEST_SCHEMA_VERSION: u32 = 1;
const REGISTRY_ROOT_SCHEMA: &str = "llmix.config-registry.root";
const REGISTRY_ROOT_SCHEMA_VERSION: u32 = 1;
const REGISTRY_ROOT_ENVELOPE_SCHEMA: &str = "llmix.config-registry.root-envelope";
const REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION: u32 = 1;
const REGISTRY_ROOT_PAYLOAD_TYPE: &str = "application/vnd.snoai.llmix.registry-root+json";
const REGISTRY_ROOT_FILENAME: &str = "registry-root.json";
static ATOMIC_WRITE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedRevision {
    pub revision: String,
    pub compiled_path: PathBuf,
    pub manifest_path: PathBuf,
    pub manifest_sha256: String,
    pub registry_root_path: Option<PathBuf>,
    pub registry_root_sha256: Option<String>,
    pub activated: bool,
    pub preset_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootCurrentBinding {
    pub path: String,
    pub revision: String,
    #[serde(rename = "manifest_sha256")]
    pub manifest_sha256: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootManifestBinding {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootFileDigest {
    pub path: String,
    pub sha256: String,
    pub role: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootPayload {
    pub schema: String,
    #[serde(rename = "schema_version")]
    pub schema_version: u32,
    pub revision: String,
    #[serde(rename = "published_at")]
    pub published_at: String,
    pub current: RegistryRootCurrentBinding,
    pub manifest: RegistryRootManifestBinding,
    pub files: Vec<RegistryRootFileDigest>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootSignature {
    pub signer: String,
    #[serde(rename = "key-id")]
    pub key_id: String,
    #[serde(rename = "payload-digest")]
    pub payload_digest: String,
    pub algorithm: String,
    pub signature: String,
    #[serde(rename = "rekor-log-id", skip_serializing_if = "Option::is_none")]
    pub rekor_log_id: Option<String>,
    #[serde(rename = "rekor-log-index", skip_serializing_if = "Option::is_none")]
    pub rekor_log_index: Option<u64>,
    #[serde(rename = "payload-type", skip_serializing_if = "Option::is_none")]
    pub payload_type: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryRootEnvelope {
    pub schema: String,
    #[serde(rename = "schema_version")]
    pub schema_version: u32,
    pub payload: RegistryRootPayload,
    pub integrity: IntegrityField,
    #[serde(rename = "payload_sha256")]
    pub payload_sha256: String,
    pub signatures: Vec<RegistryRootSignature>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegistryRootSigningInput {
    pub payload: RegistryRootPayload,
    pub canonical_payload: String,
    pub integrity: IntegrityField,
    pub payload_type: String,
    pub payload_sha256: String,
}

pub trait RegistryRootSigner {
    fn sign_registry_root(
        &self,
        input: &RegistryRootSigningInput,
    ) -> LlmixResult<Vec<RegistryRootSignature>>;
}

#[derive(Clone)]
pub struct RegistryRootSigningOptions<'a> {
    pub signer: &'a dyn RegistryRootSigner,
    pub min_signatures: Option<usize>,
}

pub trait RegistryRootHighWatermark: Send + Sync {
    fn accept_registry_root(
        &self,
        envelope: &RegistryRootEnvelope,
        input: &RegistryRootSigningInput,
    ) -> LlmixResult<bool>;
}

#[derive(Clone)]
pub struct RegistryRootVerificationOptions {
    pub trust_policy: TrustPolicy,
    pub rekor_client: Option<Arc<dyn RekorClient + Send + Sync>>,
    pub sigstore_verifier: Option<Arc<dyn SigstoreVerifier + Send + Sync>>,
    pub did_web_verifier: Option<Arc<dyn DidWebVerifier + Send + Sync>>,
    pub expected_revision: Option<String>,
    pub expected_root_digest: Option<String>,
    pub minimum_revision: Option<String>,
    pub minimum_published_at: Option<String>,
    pub high_watermark: Option<Arc<dyn RegistryRootHighWatermark>>,
}

#[derive(Default, Clone)]
pub struct ConfigRegistryOpenOptions {
    pub signed_root: Option<RegistryRootVerificationOptions>,
}

pub struct ConfigRegistryPublishOptions<'a> {
    pub revision: Option<&'a str>,
    pub activate: bool,
    pub mda_options: MdaConfigLoadOptions<'a>,
    pub registry_root: Option<RegistryRootSigningOptions<'a>>,
}

impl<'a> Default for ConfigRegistryPublishOptions<'a> {
    fn default() -> Self {
        Self {
            revision: None,
            activate: true,
            mda_options: MdaConfigLoadOptions::default(),
            registry_root: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PresetSource {
    module: String,
    preset: String,
    preset_id: String,
    source_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct ManifestPresetEntry {
    source_path: String,
    source_sha256: String,
    resolved_path: String,
    resolved_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct RegistryManifest {
    revision: String,
    published_at: String,
    schema_version: u32,
    presets: BTreeMap<String, ManifestPresetEntry>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct CurrentPointer {
    revision: String,
    #[serde(rename = "manifest_sha256", skip_serializing_if = "Option::is_none")]
    manifest_sha256: Option<String>,
}

mod fs_ops;
mod manager;
mod publisher;
mod root;
mod root_verify;
mod trust_manifest;

pub use manager::ConfigRegistryManager;
pub use publisher::ConfigRegistryPublisher;
pub use trust_manifest::{
    load_llmix_trust_manifest, registry_root_options_from_trust_manifest,
    registry_root_options_from_trust_manifest_with_hooks, LlmixTrustManifest,
    LlmixTrustManifestRegistryRoot, LlmixTrustManifestReleasePlan, LLMIX_TRUST_MANIFEST_KIND,
    LLMIX_TRUST_MANIFEST_VERSION,
};
