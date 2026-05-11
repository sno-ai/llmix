use super::fs_ops::{
    absolutize_user_path, read_json_object, safe_join_relative, sha256_file,
    validate_manifest_entry_string, validate_resolved_config, validate_revision,
};
use super::root::validate_sha256;
use super::root_verify::verify_signed_registry_root_if_needed;
use super::*;

pub struct ConfigRegistryManager {
    root: PathBuf,
    snapshots_dir: PathBuf,
    current_path: PathBuf,
    active_revision: String,
    active_manifest_sha256: String,
    configs: BTreeMap<String, Value>,
    open_options: ConfigRegistryOpenOptions,
    last_reload_error: Option<LlmixError>,
    last_successful_reload_at: Option<SystemTime>,
    last_reload_failure_at: Option<SystemTime>,
}

impl std::fmt::Debug for ConfigRegistryManager {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ConfigRegistryManager")
            .field("root", &self.root)
            .field("snapshots_dir", &self.snapshots_dir)
            .field("current_path", &self.current_path)
            .field("active_revision", &self.active_revision)
            .field("active_manifest_sha256", &self.active_manifest_sha256)
            .field("config_count", &self.configs.len())
            .field("last_reload_error", &self.last_reload_error)
            .field("last_successful_reload_at", &self.last_successful_reload_at)
            .field("last_reload_failure_at", &self.last_reload_failure_at)
            .finish()
    }
}

impl ConfigRegistryManager {
    pub fn open<P>(root: P) -> LlmixResult<Self>
    where
        P: AsRef<Path>,
    {
        Self::open_with_options(root, ConfigRegistryOpenOptions::default())
    }

    pub fn open_with_options<P>(
        root: P,
        open_options: ConfigRegistryOpenOptions,
    ) -> LlmixResult<Self>
    where
        P: AsRef<Path>,
    {
        let root = absolutize_user_path(root.as_ref())?;
        let snapshots_dir = root.join("snapshots");
        let current_path = root.join("current.json");
        let pointer = read_current_pointer(&current_path, &snapshots_dir)?;
        let configs = load_revision(
            &current_path,
            &snapshots_dir,
            &pointer,
            open_options.signed_root.as_ref(),
        )?;

        Ok(Self {
            root,
            snapshots_dir,
            current_path,
            active_revision: pointer.revision,
            active_manifest_sha256: pointer
                .manifest_sha256
                .expect("read_current_pointer fills manifest_sha256"),
            configs,
            open_options,
            last_reload_error: None,
            last_successful_reload_at: Some(SystemTime::now()),
            last_reload_failure_at: None,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn active_revision(&self) -> &str {
        &self.active_revision
    }

    pub fn active_manifest_sha256(&self) -> &str {
        &self.active_manifest_sha256
    }

    pub fn current_path(&self) -> &Path {
        &self.current_path
    }

    pub fn snapshots_dir(&self) -> &Path {
        &self.snapshots_dir
    }

    pub fn last_reload_error(&self) -> Option<&LlmixError> {
        self.last_reload_error.as_ref()
    }

    pub fn last_successful_reload_at(&self) -> Option<SystemTime> {
        self.last_successful_reload_at
    }

    pub fn last_reload_failure_at(&self) -> Option<SystemTime> {
        self.last_reload_failure_at
    }

    pub fn available_presets(&self) -> Vec<String> {
        self.configs.keys().cloned().collect()
    }

    pub fn get_preset<S1, S2>(&mut self, module: S1, preset: S2) -> LlmixResult<Value>
    where
        S1: AsRef<str>,
        S2: AsRef<str>,
    {
        let module = module.as_ref();
        let preset = preset.as_ref();
        validate_module(module)?;
        validate_preset(preset)?;

        self.refresh_if_needed();

        let preset_id = format!("{module}/{preset}");
        self.configs.get(&preset_id).cloned().ok_or_else(|| {
            ConfigNotFoundError {
                path: format!(
                    "Preset not found in active Config Registry revision {}: {preset_id}",
                    self.active_revision
                ),
            }
            .into()
        })
    }

    fn refresh_if_needed(&mut self) {
        let current_pointer = match read_current_pointer(&self.current_path, &self.snapshots_dir) {
            Ok(value) => value,
            Err(error) => {
                self.last_reload_error = Some(error);
                self.last_reload_failure_at = Some(SystemTime::now());
                return;
            }
        };

        let current_manifest_sha256 = current_pointer
            .manifest_sha256
            .clone()
            .expect("read_current_pointer fills manifest_sha256");
        if current_pointer.revision == self.active_revision
            && current_manifest_sha256 == self.active_manifest_sha256
        {
            return;
        }

        match load_revision(
            &self.current_path,
            &self.snapshots_dir,
            &current_pointer,
            self.open_options.signed_root.as_ref(),
        ) {
            Ok(configs) => {
                self.active_revision = current_pointer.revision;
                self.active_manifest_sha256 = current_manifest_sha256;
                self.configs = configs;
                self.last_reload_error = None;
                self.last_successful_reload_at = Some(SystemTime::now());
                self.last_reload_failure_at = None;
            }
            Err(error) => {
                self.last_reload_error = Some(error);
                self.last_reload_failure_at = Some(SystemTime::now());
            }
        }
    }
}

fn load_revision(
    current_path: &Path,
    snapshots_dir: &Path,
    pointer: &CurrentPointer,
    signed_root_options: Option<&RegistryRootVerificationOptions>,
) -> LlmixResult<BTreeMap<String, Value>> {
    let revision = pointer.revision.as_str();
    let manifest_sha256 = pointer
        .manifest_sha256
        .as_deref()
        .ok_or_else(|| InvalidConfigError {
            message: "Registry current pointer is missing manifest_sha256".to_string(),
        })?;
    validate_revision(revision)?;
    let snapshot_path = snapshots_dir.join(revision);
    match fs::metadata(&snapshot_path) {
        Ok(metadata) if metadata.is_dir() => {}
        Ok(_) => {
            return Err(InvalidConfigError {
                message: format!(
                    "Config Registry snapshot is not a directory: {}",
                    snapshot_path.display()
                ),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: snapshot_path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    }

    let manifest_path = snapshot_path.join("manifest.json");
    verify_snapshot_checksum(&manifest_path, manifest_sha256, revision)?;
    let manifest_value = Value::Object(read_json_object(&manifest_path)?);
    let manifest: RegistryManifest =
        serde_json::from_value(manifest_value).map_err(|error| InvalidConfigError {
            message: format!(
                "Invalid Config Registry manifest {}: {error}",
                manifest_path.display()
            ),
        })?;
    if manifest.revision != revision {
        return Err(InvalidConfigError {
            message: format!(
                "Config Registry manifest revision mismatch in {}",
                manifest_path.display()
            ),
        }
        .into());
    }
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION {
        return Err(InvalidConfigError {
            message: format!(
                "Unsupported Config Registry manifest schema version in {}",
                manifest_path.display()
            ),
        }
        .into());
    }
    verify_signed_registry_root_if_needed(
        pointer,
        &manifest,
        current_path,
        &snapshot_path,
        signed_root_options,
    )?;

    let mut configs = BTreeMap::new();
    for (preset_id, entry) in manifest.presets {
        validate_manifest_entry_string(&entry.authoring_path, "authoring_path", &preset_id)?;
        validate_manifest_entry_string(&entry.authoring_sha256, "authoring_sha256", &preset_id)?;
        validate_manifest_entry_string(&entry.resolved_path, "resolved_path", &preset_id)?;
        validate_manifest_entry_string(&entry.resolved_sha256, "resolved_sha256", &preset_id)?;
        let authoring_path = safe_join_relative(&snapshot_path, &entry.authoring_path)?;
        let resolved_path = safe_join_relative(&snapshot_path, &entry.resolved_path)?;
        verify_snapshot_checksum(&authoring_path, &entry.authoring_sha256, &preset_id)?;
        verify_snapshot_checksum(&resolved_path, &entry.resolved_sha256, &preset_id)?;

        let resolved = Value::Object(read_json_object(&resolved_path)?);
        validate_resolved_config(&resolved_path, &resolved)?;
        configs.insert(preset_id, resolved);
    }

    Ok(configs)
}

fn read_current_pointer(current_path: &Path, snapshots_dir: &Path) -> LlmixResult<CurrentPointer> {
    let pointer_value = Value::Object(read_json_object(current_path)?);
    let mut pointer: CurrentPointer =
        serde_json::from_value(pointer_value).map_err(|error| InvalidConfigError {
            message: format!(
                "Invalid Config Registry pointer {}: {error}",
                current_path.display()
            ),
        })?;
    validate_revision(&pointer.revision)?;
    if let Some(manifest_sha256) = pointer.manifest_sha256.as_deref() {
        validate_sha256(manifest_sha256, "registry current pointer manifest")?;
    } else {
        pointer.manifest_sha256 = Some(sha256_file(
            &snapshots_dir.join(&pointer.revision).join("manifest.json"),
        )?);
    }
    Ok(pointer)
}

fn verify_snapshot_checksum(
    artifact_path: &Path,
    expected_sha: &str,
    preset_id: &str,
) -> LlmixResult<()> {
    if expected_sha.is_empty() {
        return Err(InvalidConfigError {
            message: format!("Config Registry manifest entry is missing checksum: {preset_id}"),
        }
        .into());
    }

    match fs::symlink_metadata(artifact_path) {
        Ok(metadata) if metadata.file_type().is_file() => {}
        Ok(metadata) if metadata.file_type().is_symlink() => {
            return Err(InvalidConfigError {
                message: format!(
                    "Config Registry artifact must not be a symlink: {} ({preset_id})",
                    artifact_path.display()
                ),
            }
            .into())
        }
        Ok(_) => {
            return Err(InvalidConfigError {
                message: format!(
                    "Config Registry artifact is not a file: {} ({preset_id})",
                    artifact_path.display()
                ),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Err(ConfigNotFoundError {
                path: artifact_path.display().to_string(),
            }
            .into())
        }
        Err(error) if error.kind() == ErrorKind::PermissionDenied => {
            return Err(ConfigAccessError {
                path: artifact_path.display().to_string(),
            }
            .into())
        }
        Err(error) => return Err(error.into()),
    }

    let actual_sha = sha256_file(artifact_path)?;
    if actual_sha != expected_sha {
        return Err(InvalidConfigError {
            message: format!(
                "Checksum mismatch for Config Registry artifact {}",
                artifact_path.display()
            ),
        }
        .into());
    }

    Ok(())
}
