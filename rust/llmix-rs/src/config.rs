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
        let project_root = match project_root.as_ref() {
            Some(project_root) => project_root.clone(),
            None => find_project_root(None)?,
        };
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
    ensure_mda_config_path(&file_path)?;
    let base_dir = file_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    verify_path_containment(&file_path, &base_dir)?;
    load_mda_file(&file_path)
}

pub fn load_config_preset<S, P>(name: S, base_dir: P) -> LlmixResult<Value>
where
    S: AsRef<str>,
    P: AsRef<Path>,
{
    reject_legacy_config_path(Path::new(name.as_ref()))?;
    let preset = normalize_preset_name(name.as_ref());
    validate_preset(&preset)?;

    let presets_dir = absolutize_user_path(base_dir.as_ref())?;
    let module_name = presets_dir
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    validate_module(module_name)?;

    let file_path = presets_dir.join(format!("{preset}.mda"));
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

fn load_mda_file(file_path: &Path) -> LlmixResult<Value> {
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

    let frontmatter = extract_mda_frontmatter(&content, file_path)?;
    let parsed: Value =
        serde_yaml_ng::from_str(frontmatter).map_err(|error| InvalidConfigError {
            message: format!(
                "MDA frontmatter parsing failed for {}: {error}",
                file_path.display()
            ),
        })?;

    let Value::Object(object) = parsed else {
        return Err(InvalidConfigError {
            message: format!(
                "MDA frontmatter must be a dictionary, got {}",
                json_type_name(&parsed)
            ),
        }
        .into());
    };

    let config = project_mda_preset_to_config(&object, file_path)?;
    validate_runtime_config(file_path, &config)?;

    Ok(Value::Object(config))
}

fn extract_mda_frontmatter<'a>(content: &'a str, file_path: &Path) -> LlmixResult<&'a str> {
    let content = content.strip_prefix('\u{feff}').unwrap_or(content);
    let (frontmatter_start, after_opening) = if let Some(rest) = content.strip_prefix("---\r\n") {
        (5, rest)
    } else if let Some(rest) = content.strip_prefix("---\n") {
        (4, rest)
    } else {
        return Err(InvalidConfigError {
            message: format!(
                "MDA source file must start with YAML frontmatter: {}",
                file_path.display()
            ),
        }
        .into());
    };

    let mut offset = frontmatter_start;
    for line in after_opening.split_inclusive('\n') {
        let line_without_newline = line.trim_end_matches(['\r', '\n']);
        if line_without_newline == "---" {
            return Ok(&content[frontmatter_start..offset]);
        }
        offset += line.len();
    }

    let final_line = &content[offset..];
    if final_line.trim_end_matches('\r') == "---" {
        return Ok(&content[frontmatter_start..offset]);
    }

    Err(InvalidConfigError {
        message: format!(
            "MDA source file is missing closing frontmatter delimiter: {}",
            file_path.display()
        ),
    }
    .into())
}

fn project_mda_preset_to_config(
    frontmatter: &Map<String, Value>,
    file_path: &Path,
) -> LlmixResult<Map<String, Value>> {
    require_non_empty_string(frontmatter.get("name"), "name", file_path)?;
    let top_level_description =
        require_non_empty_string(frontmatter.get("description"), "description", file_path)?;

    let metadata = require_object(frontmatter.get("metadata"), "metadata", file_path)?;
    let namespace = require_object(
        metadata.get(LLMIX_MDA_NAMESPACE),
        "metadata.snoai-llmix",
        file_path,
    )?;
    let common_raw = require_object(
        namespace.get("common"),
        "metadata.snoai-llmix.common",
        file_path,
    )?;
    let Value::Object(mut common) = normalize_config_keys(Value::Object(common_raw.clone())) else {
        return Err(InvalidConfigError {
            message: format!(
                "metadata.snoai-llmix.common must be an object in {}",
                file_path.display()
            ),
        }
        .into());
    };

    let provider = common
        .remove("provider")
        .ok_or_else(|| InvalidConfigError {
            message: format!(
                "Missing required field 'provider' in {}",
                file_path.display()
            ),
        })?;
    let model = common.remove("model").ok_or_else(|| InvalidConfigError {
        message: format!("Missing required field 'model' in {}", file_path.display()),
    })?;

    let mut config = Map::new();
    config.insert("provider".to_string(), provider);
    config.insert("model".to_string(), model);
    if !common.is_empty() {
        config.insert("common".to_string(), Value::Object(common));
    }

    for key in [
        "providerOptions",
        "timeout",
        "deprecated",
        "caching",
        "bypassGateway",
    ] {
        if let Some(value) = namespace.get(key) {
            config.insert(
                camel_to_snake_key(key).to_string(),
                normalize_config_keys(value.clone()),
            );
        }
    }

    if let Some(value) = namespace.get("description") {
        config.insert(
            "description".to_string(),
            normalize_config_keys(value.clone()),
        );
    } else {
        config.insert(
            "description".to_string(),
            Value::String(top_level_description.to_string()),
        );
    }

    if let Some(value) = namespace.get("tags").or_else(|| frontmatter.get("tags")) {
        config.insert("tags".to_string(), normalize_config_keys(value.clone()));
    }

    Ok(config)
}

fn validate_runtime_config(file_path: &Path, config: &Map<String, Value>) -> LlmixResult<()> {
    let provider = require_non_empty_string(config.get("provider"), "provider", file_path)?;
    if !VALID_PROVIDERS.contains(&provider) {
        return Err(InvalidConfigError {
            message: format!(
                "Invalid provider {provider:?} in {}. Expected one of: {}",
                file_path.display(),
                VALID_PROVIDERS.join(", ")
            ),
        }
        .into());
    }
    require_non_empty_string(config.get("model"), "model", file_path)?;

    if let Some(common) = config.get("common") {
        let common = expect_object(common, "common", file_path)?;
        validate_optional_number_range(common, "temperature", 0.0, 2.0, file_path)?;
        validate_optional_number_range(common, "top_p", 0.0, 1.0, file_path)?;
        validate_optional_positive_integer(common, "max_output_tokens", file_path)?;
        validate_optional_positive_integer(common, "top_k", file_path)?;
        validate_optional_nonnegative_integer(common, "max_retries", file_path)?;
    }

    if let Some(caching) = config.get("caching") {
        let caching = expect_object(caching, "caching", file_path)?;
        if let Some(strategy) = caching.get("strategy") {
            let strategy = expect_non_empty_string(strategy, "caching.strategy", file_path)?;
            if !VALID_CACHE_STRATEGIES.contains(&strategy) {
                return Err(InvalidConfigError {
                    message: format!(
                        "Invalid caching.strategy {strategy:?} in {}. Expected one of: {}",
                        file_path.display(),
                        VALID_CACHE_STRATEGIES.join(", ")
                    ),
                }
                .into());
            }
        }
        validate_optional_positive_integer(caching, "ttl", file_path)?;
        validate_optional_positive_integer(caching, "max_items", file_path)?;
    }

    if let Some(timeout) = config.get("timeout") {
        let timeout = expect_object(timeout, "timeout", file_path)?;
        validate_optional_positive_number(timeout, "total_time", file_path)?;
        validate_optional_positive_number(timeout, "stream_first_chunk_time", file_path)?;
    }

    if let Some(provider_options) = config.get("provider_options") {
        let provider_options = expect_object(provider_options, "provider_options", file_path)?;
        if let Some(openai) = provider_options.get("openai") {
            let openai = expect_object(openai, "provider_options.openai", file_path)?;
            if let Some(reasoning_effort) = openai.get("reasoning_effort") {
                let reasoning_effort = expect_non_empty_string(
                    reasoning_effort,
                    "provider_options.openai.reasoning_effort",
                    file_path,
                )?;
                if !OPENAI_REASONING_EFFORTS.contains(&reasoning_effort) {
                    return Err(InvalidConfigError {
                        message: format!(
                            "Invalid provider_options.openai.reasoning_effort {reasoning_effort:?} in {}",
                            file_path.display()
                        ),
                    }
                    .into());
                }
            }
        }
    }

    Ok(())
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

fn require_object<'a>(
    value: Option<&'a Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a Map<String, Value>> {
    let value = value.ok_or_else(|| InvalidConfigError {
        message: format!(
            "Missing required field '{field}' in {}",
            file_path.display()
        ),
    })?;
    expect_object(value, field, file_path)
}

fn expect_object<'a>(
    value: &'a Value,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a Map<String, Value>> {
    match value {
        Value::Object(object) => Ok(object),
        other => Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be an object, got {}",
                file_path.display(),
                json_type_name(other)
            ),
        }
        .into()),
    }
}

fn require_non_empty_string<'a>(
    value: Option<&'a Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a str> {
    let value = value.ok_or_else(|| InvalidConfigError {
        message: format!(
            "Missing required field '{field}' in {}",
            file_path.display()
        ),
    })?;
    expect_non_empty_string(value, field, file_path)
}

fn expect_non_empty_string<'a>(
    value: &'a Value,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a str> {
    match value.as_str() {
        Some(value) if !value.is_empty() => Ok(value),
        _ => Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a non-empty string",
                file_path.display()
            ),
        }
        .into()),
    }
}

fn validate_optional_number_range(
    object: &Map<String, Value>,
    field: &str,
    min: f64,
    max: f64,
    file_path: &Path,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_f64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a number",
                file_path.display()
            ),
        }
        .into());
    };
    if !(min..=max).contains(&number) {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be between {min} and {max}",
                file_path.display()
            ),
        }
        .into());
    }
    Ok(())
}

fn validate_optional_positive_number(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_f64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a number",
                file_path.display()
            ),
        }
        .into());
    };
    if number <= 0.0 {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be positive",
                file_path.display()
            ),
        }
        .into());
    }
    Ok(())
}

fn validate_optional_positive_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    validate_optional_integer(object, field, file_path, |number| number > 0, "positive")
}

fn validate_optional_nonnegative_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    validate_optional_integer(
        object,
        field,
        file_path,
        |number| number >= 0,
        "non-negative",
    )
}

fn validate_optional_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
    predicate: impl FnOnce(i64) -> bool,
    label: &str,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_i64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be an integer",
                file_path.display()
            ),
        }
        .into());
    };
    if !predicate(number) {
        return Err(InvalidConfigError {
            message: format!("Field '{field}' in {} must be {label}", file_path.display()),
        }
        .into());
    }
    Ok(())
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
        "safetyIdentifier" => "safety_identifier",
        "budgetTokens" => "budget_tokens",
        "disableParallelToolUse" => "disable_parallel_tool_use",
        "sendReasoning" => "send_reasoning",
        "toolStreaming" => "tool_streaming",
        "structuredOutputMode" => "structured_output_mode",
        "thinkingLevel" => "thinking_level",
        "thinkingConfig" => "thinking_config",
        "includeThoughts" => "include_thoughts",
        "cachedContent" => "cached_content",
        "safetySettings" => "safety_settings",
        "responseModalities" => "response_modalities",
        "cacheControl" => "cache_control",
        other => other,
    }
}

fn ensure_mda_config_path(path: &Path) -> LlmixResult<()> {
    reject_legacy_config_path(path)?;
    if path_has_suffix(path, ".mda") {
        return Ok(());
    }

    Err(InvalidConfigError {
        message: format!(
            "LLMix Rust configs must be MDA source files with a .mda suffix: {}",
            path.display()
        ),
    }
    .into())
}

fn reject_legacy_config_path(path: &Path) -> LlmixResult<()> {
    if path_has_suffix(path, ".yaml") || path_has_suffix(path, ".yml") {
        return Err(InvalidConfigError {
            message: format!(
                "LLMix Rust configs use .mda files; YAML configs are no longer supported: {}",
                path.display()
            ),
        }
        .into());
    }

    Ok(())
}

fn path_has_suffix(path: &Path, suffix: &str) -> bool {
    path.to_string_lossy()
        .to_ascii_lowercase()
        .ends_with(suffix)
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
    if let Some(stripped) = file_name.strip_suffix(".mda") {
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
