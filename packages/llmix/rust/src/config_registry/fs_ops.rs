use super::*;

pub(super) fn validate_manifest_entry_string(
    value: &str,
    key: &str,
    preset_id: &str,
) -> LlmixResult<()> {
    if value.is_empty() {
        return Err(InvalidConfigError {
            message: format!("Config Registry manifest entry is missing {key}: {preset_id}"),
        }
        .into());
    }

    Ok(())
}

pub(super) fn parse_preset_filename(file_name: &str) -> Option<String> {
    file_name
        .strip_suffix(".mda")
        .map(|preset| preset.to_string())
}

pub(super) fn is_legacy_preset_filename(file_name: &str) -> bool {
    let lower = file_name.to_ascii_lowercase();
    lower.ends_with(".yaml") || lower.ends_with(".yml")
}

pub(super) fn validate_authoring_directory(path: &Path) -> LlmixResult<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => Ok(()),
        Ok(metadata) if metadata.file_type().is_symlink() => Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring directory must not be a symlink: {}",
                path.display()
            ),
        }
        .into()),
        Ok(_) => Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring path is not a directory: {}",
                path.display()
            ),
        }
        .into()),
        Err(error) if error.kind() == ErrorKind::NotFound => Err(ConfigNotFoundError {
            path: path.display().to_string(),
        }
        .into()),
        Err(error) if error.kind() == ErrorKind::PermissionDenied => Err(ConfigAccessError {
            path: path.display().to_string(),
        }
        .into()),
        Err(error) => Err(error.into()),
    }
}

pub(super) fn validate_revision(revision: &str) -> LlmixResult<()> {
    if revision.is_empty() {
        return Err(InvalidConfigError {
            message: "Registry revision cannot be empty".to_string(),
        }
        .into());
    }
    if revision.contains('/') || revision.contains('\\') || revision.contains("..") {
        return Err(SecurityError {
            message: format!("Invalid registry revision: {revision:?}"),
        }
        .into());
    }
    if revision.len() > 128
        || !revision
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
        || !revision
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
    {
        return Err(InvalidConfigError {
            message: format!("Invalid registry revision format: {revision:?}"),
        }
        .into());
    }

    Ok(())
}

pub(super) fn validate_resolved_config(path: &Path, value: &Value) -> LlmixResult<()> {
    let Value::Object(object) = value else {
        return Err(InvalidConfigError {
            message: format!(
                "Resolved Config Registry artifact must be a JSON object: {}",
                path.display()
            ),
        }
        .into());
    };

    for field in ["provider", "model"] {
        if !object.contains_key(field) {
            return Err(InvalidConfigError {
                message: format!(
                    "Missing required field '{field}' in resolved config {}",
                    path.display()
                ),
            }
            .into());
        }
    }

    Ok(())
}

pub(super) fn canonical_json_bytes(value: &Value) -> LlmixResult<Vec<u8>> {
    let mut content = canonical_json::to_string(value)?;
    content.push('\n');
    Ok(content.into_bytes())
}

pub(super) fn read_authoring_file_bytes(path: &Path) -> LlmixResult<Vec<u8>> {
    let before = validate_regular_authoring_file(path)?;
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::PermissionDenied => {
            return Err(ConfigAccessError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    };
    let after = file.metadata()?;
    validate_same_authoring_file(path, &before, &after)?;

    let mut content = Vec::new();
    file.read_to_end(&mut content)?;
    Ok(content)
}

fn validate_regular_authoring_file(path: &Path) -> LlmixResult<fs::Metadata> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(metadata),
        Ok(metadata) if metadata.file_type().is_symlink() => Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring preset must not be a symlink: {}",
                path.display()
            ),
        }
        .into()),
        Ok(_) => Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring preset is not a file: {}",
                path.display()
            ),
        }
        .into()),
        Err(error) if error.kind() == ErrorKind::NotFound => Err(ConfigNotFoundError {
            path: path.display().to_string(),
        }
        .into()),
        Err(error) if error.kind() == ErrorKind::PermissionDenied => Err(ConfigAccessError {
            path: path.display().to_string(),
        }
        .into()),
        Err(error) => Err(error.into()),
    }
}

fn validate_same_authoring_file(
    path: &Path,
    before: &fs::Metadata,
    after: &fs::Metadata,
) -> LlmixResult<()> {
    if !after.file_type().is_file() {
        return Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring preset is not a file: {}",
                path.display()
            ),
        }
        .into());
    }

    #[cfg(unix)]
    if before.dev() != after.dev() || before.ino() != after.ino() {
        return Err(InvalidConfigError {
            message: format!(
                "Config Registry authoring preset changed while publishing: {}",
                path.display()
            ),
        }
        .into());
    }

    Ok(())
}

pub(super) fn sha256_bytes(content: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(content);
    format!("{:x}", digest.finalize())
}

pub(super) fn sha256_file(path: &Path) -> LlmixResult<String> {
    let mut digest = Sha256::new();
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::PermissionDenied => {
            return Err(ConfigAccessError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    };
    let mut buffer = [0_u8; 8192];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

pub(super) fn read_json_object(path: &Path) -> LlmixResult<Map<String, Value>> {
    let content = match fs::read_to_string(path) {
        Ok(content) => content,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::PermissionDenied => {
            return Err(ConfigAccessError {
                path: path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    };

    let value: Value = serde_json::from_str(&content).map_err(|error| InvalidConfigError {
        message: format!("Invalid JSON in registry file {}: {error}", path.display()),
    })?;
    let Value::Object(object) = value else {
        return Err(InvalidConfigError {
            message: format!(
                "Registry file must contain a JSON object: {}",
                path.display()
            ),
        }
        .into());
    };

    Ok(object)
}

pub(super) fn write_bytes(path: &Path, content: &[u8]) -> LlmixResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)?;
    fsync_file(path);
    Ok(())
}

pub(super) fn write_json(path: &Path, value: &Value) -> LlmixResult<()> {
    write_bytes(path, &canonical_json_bytes(value)?)
}

pub(super) fn atomic_write_json(path: &Path, value: &Value) -> LlmixResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp_path = atomic_temp_path(path);
    write_json(&temp_path, value)?;
    if let Err(error) = fs::rename(&temp_path, path) {
        let _ = fs::remove_file(&temp_path);
        return Err(error.into());
    }
    if let Some(parent) = path.parent() {
        fsync_dir(parent);
    }
    Ok(())
}

fn atomic_temp_path(path: &Path) -> PathBuf {
    let counter = ATOMIC_WRITE_COUNTER.fetch_add(1, Ordering::Relaxed);
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("target");
    path.with_file_name(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        counter
    ))
}

pub(super) fn staging_attempt_path(
    staging_dir: &Path,
    revision_id: &str,
    published_at: u128,
) -> PathBuf {
    let counter = ATOMIC_WRITE_COUNTER.fetch_add(1, Ordering::Relaxed);
    staging_dir.join(format!(
        "{revision_id}.{published_at}.{}.{}.tmp",
        std::process::id(),
        counter
    ))
}

fn fsync_file(path: &Path) {
    let Ok(file) = File::open(path) else {
        return;
    };
    let _ = file.sync_all();
}

pub(super) fn fsync_dir(path: &Path) {
    let Ok(directory) = File::open(path) else {
        return;
    };
    let _ = directory.sync_all();
}

pub(super) fn safe_join_relative(base: &Path, relative: &str) -> LlmixResult<PathBuf> {
    let relative_path = Path::new(relative);
    if relative_path.is_absolute() {
        return Err(SecurityError {
            message: format!("Absolute registry path is not allowed: {relative:?}"),
        }
        .into());
    }

    for component in relative_path.components() {
        match component {
            Component::Normal(_) => {}
            _ => {
                return Err(SecurityError {
                    message: format!("Invalid registry relative path: {relative:?}"),
                }
                .into())
            }
        }
    }

    Ok(base.join(relative_path))
}

pub(super) fn absolutize_user_path(path: &Path) -> LlmixResult<PathBuf> {
    let expanded = expand_home(path)?;
    if expanded.is_absolute() {
        Ok(expanded)
    } else {
        Ok(env::current_dir()?.join(expanded))
    }
}

pub(super) fn current_unix_millis() -> LlmixResult<u128> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .map_err(|error| {
            InvalidConfigError {
                message: format!("failed to read current time: {error}"),
            }
            .into()
        })
}

fn expand_home(path: &Path) -> LlmixResult<PathBuf> {
    let Some(path_str) = path.to_str() else {
        return Ok(path.to_path_buf());
    };
    if path_str == "~" || path_str.starts_with("~/") {
        let Some(home) = env::var_os("HOME") else {
            return Err(InvalidConfigError {
                message: "Cannot expand '~' because HOME is not set".to_string(),
            }
            .into());
        };
        let mut expanded = PathBuf::from(home);
        if path_str.len() > 2 {
            expanded.push(&path_str[2..]);
        }
        return Ok(expanded);
    }

    Ok(path.to_path_buf())
}
