use super::*;

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

pub(super) fn ensure_mda_config_path(path: &Path) -> LlmixResult<()> {
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

pub(super) fn reject_legacy_config_path(path: &Path) -> LlmixResult<()> {
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

pub(super) fn verify_path_containment(resolved_path: &Path, base_dir: &Path) -> LlmixResult<()> {
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

pub(super) fn absolutize_user_path(path: &Path) -> LlmixResult<PathBuf> {
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
