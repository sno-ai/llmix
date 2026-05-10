use crate::error::{
    ConfigAccessError, ConfigNotFoundError, InvalidConfigError, LlmixError, LlmixResult,
    SecurityError,
};
use serde_json::{Map, Value};
use snoai_mda_config::{
    load_mda_source_from_bytes, DidWebVerifier, LoadMdaSourceOptions, MdaConfigError, RekorClient,
    SigstoreVerifier, TrustPolicy,
};
use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};

const DEFAULT_ENV_VAR: &str = "LLMIX_CONFIG_DIR";
const DEFAULT_RELATIVE_PATH: &str = "./config/llm";
const MAX_NAME_LEN: usize = 64;
const MIN_VERSION: u32 = 1;
const MAX_VERSION: u32 = 9999;
const LLMIX_MDA_NAMESPACE: &str = "snoai-llmix";
const VALID_PROVIDERS: &[&str] = &[
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "openrouter",
    "sno-gpu",
    "deepinfra",
    "novita",
    "together",
];
const VALID_CACHE_STRATEGIES: &[&str] = &[
    "native",
    "gateway",
    "disabled",
    "redis",
    "redis-or-memory",
    "memory",
];
const OPENAI_REASONING_EFFORTS: &[&str] = &["minimal", "low", "medium", "high", "xhigh"];
const LOCKFILES_TS: &[&str] = &[
    "bun.lock",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
];
const LOCKFILES_PY: &[&str] = &["uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"];
const DANGEROUS_PARTS: &[&str] = &["/", "\\", "..", "~", "$", "`"];

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct LlmixPathConfig {
    pub config_dir: Option<PathBuf>,
    pub env_var: Option<String>,
    pub default_path: Option<PathBuf>,
    pub project_root: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigDirSource {
    Explicit,
    Env,
    Default,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedConfigDir {
    pub config_dir: PathBuf,
    pub source: ConfigDirSource,
}

#[derive(Default, Clone)]
pub struct MdaConfigLoadOptions<'a> {
    pub verify_integrity: bool,
    pub verify_signatures: bool,
    pub trusted_runtime: bool,
    pub enforce_requires: bool,
    pub allowed_networks: Vec<String>,
    pub trust_policy: Option<TrustPolicy>,
    pub rekor_client: Option<&'a dyn RekorClient>,
    pub sigstore_verifier: Option<&'a dyn SigstoreVerifier>,
    pub did_web_verifier: Option<&'a dyn DidWebVerifier>,
}

mod loader;
mod names;
mod paths;
mod validation;

pub use loader::{
    load_config, load_config_preset, load_config_preset_with_options, load_config_with_options,
};
pub use names::{validate_module, validate_preset, validate_version};
pub use paths::resolve_config_dir;
