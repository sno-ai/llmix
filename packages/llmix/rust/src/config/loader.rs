use super::names::{normalize_preset_name, validate_module, validate_preset};
use super::paths::{
    absolutize_user_path, ensure_mda_config_path, reject_legacy_config_path,
    verify_path_containment,
};
use super::validation::{
    camel_to_snake_key, json_type_name, normalize_config_keys, require_non_empty_string,
    require_object, validate_runtime_config,
};
use super::*;

pub fn load_config<P>(path: P) -> LlmixResult<Value>
where
    P: AsRef<Path>,
{
    load_config_with_options(path, &MdaConfigLoadOptions::default())
}

pub fn load_config_with_options<P>(
    path: P,
    options: &MdaConfigLoadOptions<'_>,
) -> LlmixResult<Value>
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
    load_mda_file(&file_path, options)
}

pub fn load_config_preset<S, P>(name: S, base_dir: P) -> LlmixResult<Value>
where
    S: AsRef<str>,
    P: AsRef<Path>,
{
    load_config_preset_with_options(name, base_dir, &MdaConfigLoadOptions::default())
}

pub fn load_config_preset_with_options<S, P>(
    name: S,
    base_dir: P,
    options: &MdaConfigLoadOptions<'_>,
) -> LlmixResult<Value>
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
    load_config_with_options(file_path, options)
}

fn load_mda_file(file_path: &Path, options: &MdaConfigLoadOptions<'_>) -> LlmixResult<Value> {
    let file_bytes = match fs::read(file_path) {
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

    let parsed: Value = load_mda_source_from_bytes(&file_bytes, to_mda_source_options(options))
        .map_err(map_mda_config_error)?;
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

fn to_mda_source_options<'a>(options: &MdaConfigLoadOptions<'a>) -> LoadMdaSourceOptions<'a> {
    LoadMdaSourceOptions {
        verify_integrity: options.verify_integrity,
        verify_signatures: options.verify_signatures,
        trusted_runtime: options.trusted_runtime,
        enforce_requires: options.enforce_requires,
        allowed_networks: options.allowed_networks.clone(),
        trust_policy: options.trust_policy.clone(),
        rekor_client: options.rekor_client,
        sigstore_verifier: options.sigstore_verifier,
        did_web_verifier: options.did_web_verifier,
    }
}

fn map_mda_config_error(error: MdaConfigError) -> LlmixError {
    InvalidConfigError {
        message: format!("MDA source validation failed: {error}"),
    }
    .into()
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
