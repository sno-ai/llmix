use crate::error::{
    ConfigAccessError, ConfigNotFoundError, InvalidConfigError, LlmixResult, SecurityError,
};
use serde_json::{Map, Value};
use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};

const DEFAULT_ENV_VAR: &str = "LLMIX_CONFIG_DIR";
const DEFAULT_RELATIVE_PATH: &str = "./config/llm";
const MAX_NAME_LEN: usize = 64;
const MIN_VERSION: u32 = 1;
const MAX_VERSION: u32 = 9999;
const LOCKFILES_TS: &[&str] = &[
    "bun.lock",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
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

pub fn resolve_config_dir(options: Option<&LlmixPathConfig>) -> LlmixResult<ResolvedConfigDir> {
    let env_var_name = options
        .and_then(|value| value.env_var.as_deref())
        .unwrap_or(DEFAULT_ENV_VAR);
    let default_relative_path = options
        .and_then(|value| value.default_path.as_deref())
        .unwrap_or(Path::new(DEFAULT_RELATIVE_PATH));
    let project_root = options
        .and_then(|value| value.project_root.as_deref())
        .map(absolutize_path)
        .transpose()?;
    let cwd = env::current_dir()?;

    if let Some(config_dir) = options.and_then(|value| value.config_dir.as_deref()) {
        return Ok(ResolvedConfigDir {
            config_dir: absolutize_path(config_dir)?,
            source: ConfigDirSource::Explicit,
        });
    }

    if let Some(env_value) = env::var_os(env_var_name) {
        let project_root = find_project_root(None)?;
        return Ok(ResolvedConfigDir {
            config_dir: normalize_path(&project_root.join(env_value)),
            source: ConfigDirSource::Env,
        });
    }

    let actual_project_root = match project_root {
        Some(project_root) if project_root != cwd => project_root,
        _ => find_project_root(None)?,
    };

    Ok(ResolvedConfigDir {
        config_dir: normalize_path(&actual_project_root.join(default_relative_path)),
        source: ConfigDirSource::Default,
    })
}

pub fn load_config<P>(path: P) -> LlmixResult<Value>
where
    P: AsRef<Path>,
{
    let file_path = absolutize_user_path(path.as_ref())?;
    let base_dir = file_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    verify_path_containment(&file_path, &base_dir)?;
    load_yaml_file(&file_path)
}

pub fn load_config_preset<S, P>(name: S, base_dir: P) -> LlmixResult<Value>
where
    S: AsRef<str>,
    P: AsRef<Path>,
{
    load_config_preset_with_version(name, base_dir, 1)
}

pub fn load_config_preset_with_version<S, P>(
    name: S,
    base_dir: P,
    version: u32,
) -> LlmixResult<Value>
where
    S: AsRef<str>,
    P: AsRef<Path>,
{
    let preset = normalize_preset_name(name.as_ref());
    validate_preset(&preset)?;
    validate_version(version)?;

    let presets_dir = absolutize_user_path(base_dir.as_ref())?;
    let module_name = presets_dir
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    validate_module(module_name)?;

    let file_path = presets_dir.join(format!("{preset}.v{version}.yaml"));
    verify_path_containment(&file_path, &presets_dir)?;
    load_config(file_path)
}

pub fn validate_module(module: &str) -> LlmixResult<()> {
    validate_name(module, NameKind::Module)
}

pub fn validate_preset(preset: &str) -> LlmixResult<()> {
    validate_name(preset, NameKind::Preset)
}

pub fn validate_version(version: u32) -> LlmixResult<()> {
    if !(MIN_VERSION..=MAX_VERSION).contains(&version) {
        return Err(InvalidConfigError {
            message: format!("Version {version} out of valid range [{MIN_VERSION}, {MAX_VERSION}]"),
        }
        .into());
    }

    Ok(())
}

fn load_yaml_file(file_path: &Path) -> LlmixResult<Value> {
    let content = match fs::read_to_string(file_path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: file_path.display().to_string(),
            }
            .into())
        }
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            return Err(ConfigAccessError {
                path: file_path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    };

    let parsed: Value = serde_yaml::from_str(&content).map_err(|error| InvalidConfigError {
        message: format!("YAML parsing failed for {}: {error}", file_path.display()),
    })?;

    let Value::Object(object) = parsed else {
        return Err(InvalidConfigError {
            message: format!(
                "Config must be a dictionary, got {}",
                json_type_name(&parsed)
            ),
        }
        .into());
    };

    let config = normalize_config_shape(Value::Object(object));
    if config.get("provider").is_none() {
        return Err(InvalidConfigError {
            message: format!(
                "Missing required field 'provider' in {}",
                file_path.display()
            ),
        }
        .into());
    }
    if config.get("model").is_none() {
        return Err(InvalidConfigError {
            message: format!("Missing required field 'model' in {}", file_path.display()),
        }
        .into());
    }

    Ok(Value::Object(config))
}

fn normalize_config_shape(config: Value) -> Map<String, Value> {
    let Value::Object(mut normalized) = normalize_config_keys(config) else {
        return Map::new();
    };

    let Some(Value::Object(mut common)) = normalized.remove("common") else {
        return normalized;
    };

    let provider = common.remove("provider");
    let model = common.remove("model");

    if let Some(provider) = provider {
        normalized.entry("provider".to_string()).or_insert(provider);
    }
    if let Some(model) = model {
        normalized.entry("model".to_string()).or_insert(model);
    }

    if !common.is_empty() {
        normalized.insert("common".to_string(), Value::Object(common));
    }

    normalized
}

fn normalize_config_keys(value: Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .into_iter()
                .map(|(key, value)| {
                    (
                        camel_to_snake_key(&key).to_string(),
                        normalize_config_keys(value),
                    )
                })
                .collect(),
        ),
        Value::Array(values) => {
            Value::Array(values.into_iter().map(normalize_config_keys).collect())
        }
        other => other,
    }
}

fn camel_to_snake_key(key: &str) -> &str {
    match key {
        "maxOutputTokens" => "max_output_tokens",
        "maxRetries" => "max_retries",
        "topP" => "top_p",
        "topK" => "top_k",
        "presencePenalty" => "presence_penalty",
        "frequencyPenalty" => "frequency_penalty",
        "stopSequences" => "stop_sequences",
        "totalTime" => "total_time",
        "streamFirstChunkTime" => "stream_first_chunk_time",
        "providerOptions" => "provider_options",
        "bypassGateway" => "bypass_gateway",
        "configId" => "config_id",
        "enableThinking" => "enable_thinking",
        "keepThinkingOutput" => "keep_thinking_output",
        "thinkingBudget" => "thinking_budget",
        "reasoningEffort" => "reasoning_effort",
        "textVerbosity" => "text_verbosity",
        "structuredOutputs" => "structured_outputs",
        "parallelToolCalls" => "parallel_tool_calls",
        "logitBias" => "logit_bias",
        "strictJsonSchema" => "strict_json_schema",
        "maxCompletionTokens" => "max_completion_tokens",
        "serviceTier" => "service_tier",
        "promptCacheKey" => "prompt_cache_key",
        "promptCacheRetention" => "prompt_cache_retention",
        "gpuPath" => "gpu_path",
        "maxItems" => "max_items",
        other => other,
    }
}

fn verify_path_containment(resolved_path: &Path, base_dir: &Path) -> LlmixResult<()> {
    let normalized_base = normalize_for_containment(base_dir)?;
    let normalized_path = normalize_for_containment(resolved_path)?;

    if normalized_path.starts_with(&normalized_base) {
        return Ok(());
    }

    Err(SecurityError {
        message: format!(
            "Path traversal detected: {} escapes base directory {}",
            resolved_path.display(),
            base_dir.display()
        ),
    }
    .into())
}

fn normalize_for_containment(path: &Path) -> LlmixResult<PathBuf> {
    let absolute = absolutize_path(path)?;
    match fs::canonicalize(&absolute) {
        Ok(real_path) => Ok(real_path),
        Err(_) => Ok(normalize_path(&absolute)),
    }
}

fn absolutize_user_path(path: &Path) -> LlmixResult<PathBuf> {
    let expanded = expand_home(path)?;
    absolutize_path(&expanded)
}

fn absolutize_path(path: &Path) -> LlmixResult<PathBuf> {
    if path.is_absolute() {
        return Ok(normalize_path(path));
    }

    Ok(normalize_path(&env::current_dir()?.join(path)))
}

fn expand_home(path: &Path) -> LlmixResult<PathBuf> {
    let Some(path_str) = path.to_str() else {
        return Ok(path.to_path_buf());
    };

    if path_str == "~" || path_str.starts_with("~/") {
        let home = env::var_os("HOME").ok_or_else(|| InvalidConfigError {
            message: "Cannot expand '~' because HOME is not set".to_string(),
        })?;

        let mut expanded = PathBuf::from(home);
        if path_str.len() > 2 {
            expanded.push(&path_str[2..]);
        }
        return Ok(expanded);
    }

    Ok(path.to_path_buf())
}

fn normalize_preset_name(name: &str) -> String {
    let file_name = Path::new(name)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(name);
    if let Some(stripped) = file_name.strip_suffix(".yaml") {
        return stripped.to_string();
    }
    if let Some(stripped) = file_name.strip_suffix(".yml") {
        return stripped.to_string();
    }

    file_name.to_string()
}

fn validate_name(value: &str, kind: NameKind) -> LlmixResult<()> {
    if value.is_empty() {
        return Err(InvalidConfigError {
            message: format!("{} name cannot be empty", kind.label()),
        }
        .into());
    }

    if value.len() > MAX_NAME_LEN {
        return Err(InvalidConfigError {
            message: format!(
                "{} name too long: {} > {}",
                kind.label(),
                value.len(),
                MAX_NAME_LEN
            ),
        }
        .into());
    }

    if DANGEROUS_PARTS.iter().any(|part| value.contains(part)) {
        return Err(SecurityError {
            message: format!(
                "Invalid characters in {}: {value}",
                kind.label().to_lowercase()
            ),
        }
        .into());
    }

    let valid = match kind {
        NameKind::Module => value == "_default" || is_lowercase_identifier(value),
        NameKind::Preset => {
            value.starts_with("_base") && value[5..].chars().all(is_lowercase_alnum_or_underscore)
                || is_lowercase_identifier(value)
        }
    };

    if valid {
        Ok(())
    } else {
        Err(InvalidConfigError {
            message: match kind {
                NameKind::Module => format!(
                    "Invalid module format: {value}. Must be '_default' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
                ),
                NameKind::Preset => format!(
                    "Invalid preset format: {value}. Must be '_base*' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
                ),
            },
        }
        .into())
    }
}

fn is_lowercase_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(first) if first.is_ascii_lowercase())
        && chars.all(is_lowercase_alnum_or_underscore)
        && value.len() <= MAX_NAME_LEN
}

fn is_lowercase_alnum_or_underscore(value: char) -> bool {
    value.is_ascii_lowercase() || value.is_ascii_digit() || value == '_'
}

fn find_project_root(start_dir: Option<&Path>) -> LlmixResult<PathBuf> {
    let mut current = match start_dir {
        Some(start_dir) => absolutize_path(start_dir)?,
        None => env::current_dir()?,
    };
    let mut first_pkg_dir: Option<PathBuf> = None;
    let mut first_lockfile_dir: Option<PathBuf> = None;

    loop {
        if is_monorepo_root(&current) {
            return Ok(current);
        }

        if first_lockfile_dir.is_none() && has_lockfile(&current) {
            first_lockfile_dir = Some(current.clone());
        }

        if first_pkg_dir.is_none()
            && (current.join("pyproject.toml").exists() || current.join("package.json").exists())
        {
            first_pkg_dir = Some(current.clone());
        }

        let Some(parent) = current.parent() else {
            break;
        };
        if parent == current {
            break;
        }
        current = parent.to_path_buf();
    }

    Ok(first_lockfile_dir
        .or(first_pkg_dir)
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from("."))))
}

fn is_monorepo_root(directory: &Path) -> bool {
    let package_json = directory.join("package.json");
    let Ok(content) = fs::read_to_string(package_json) else {
        return false;
    };
    let Ok(parsed) = serde_json::from_str::<Value>(&content) else {
        return false;
    };

    parsed
        .as_object()
        .is_some_and(|object| object.contains_key("workspaces"))
}

fn has_lockfile(directory: &Path) -> bool {
    LOCKFILES_TS
        .iter()
        .chain(LOCKFILES_PY.iter())
        .any(|file| directory.join(file).exists())
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();

    for component in path.components() {
        match component {
            Component::Prefix(prefix) => normalized.push(prefix.as_os_str()),
            Component::RootDir => normalized.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            Component::Normal(part) => normalized.push(part),
        }
    }

    normalized
}

fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "str",
        Value::Array(_) => "array",
        Value::Object(_) => "dict",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NameKind {
    Module,
    Preset,
}

impl NameKind {
    fn label(self) -> &'static str {
        match self {
            NameKind::Module => "Module",
            NameKind::Preset => "Preset",
        }
    }
}
