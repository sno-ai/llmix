use crate::canonical_json;
use crate::config::{load_config, validate_module, validate_preset};
use crate::error::{
    ConfigAccessError, ConfigNotFoundError, InvalidConfigError, LlmixError, LlmixResult,
    SecurityError,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, File};
use std::io::{ErrorKind, Read};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const MANIFEST_SCHEMA_VERSION: u32 = 1;
static ATOMIC_WRITE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PublishedRevision {
    pub revision: String,
    pub snapshot_path: PathBuf,
    pub manifest_path: PathBuf,
    pub activated: bool,
    pub preset_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PresetSource {
    module: String,
    preset: String,
    preset_id: String,
    authoring_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct ManifestPresetEntry {
    authoring_path: String,
    authoring_sha256: String,
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
}

#[derive(Debug)]
pub struct ConfigRegistryPublisher {
    root: PathBuf,
    authoring_dir: PathBuf,
    snapshots_dir: PathBuf,
    staging_dir: PathBuf,
    current_path: PathBuf,
}

impl ConfigRegistryPublisher {
    pub fn new<P>(root: P) -> LlmixResult<Self>
    where
        P: AsRef<Path>,
    {
        let root = absolutize_user_path(root.as_ref())?;
        Ok(Self {
            authoring_dir: root.join("authoring"),
            snapshots_dir: root.join("snapshots"),
            staging_dir: root.join("snapshots").join(".staging"),
            current_path: root.join("current.json"),
            root,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn publish(&self) -> LlmixResult<PublishedRevision> {
        self.publish_with_options(None, true)
    }

    pub fn publish_with_options(
        &self,
        revision: Option<&str>,
        activate: bool,
    ) -> LlmixResult<PublishedRevision> {
        let presets = self.discover_presets()?;
        if presets.is_empty() {
            return Err(ConfigNotFoundError {
                path: self.authoring_dir.display().to_string(),
            }
            .into());
        }

        let published_at = current_unix_millis()?;
        let published_at_text = published_at.to_string();

        let revision_id = match revision {
            Some(value) => value.to_string(),
            None => self.build_revision_id(&presets, published_at)?,
        };
        validate_revision(&revision_id)?;

        let snapshot_path = self.snapshots_dir.join(&revision_id);
        if snapshot_path.exists() {
            return Err(InvalidConfigError {
                message: format!("Registry revision already exists: {revision_id}"),
            }
            .into());
        }

        let stage_path = staging_attempt_path(&self.staging_dir, &revision_id, published_at);

        let publish_result = (|| -> LlmixResult<PublishedRevision> {
            let manifest = self.build_staged_snapshot(
                &stage_path,
                &presets,
                &revision_id,
                &published_at_text,
            )?;
            self.verify_staged_snapshot(&stage_path, &manifest)?;

            fs::create_dir_all(&self.snapshots_dir)?;
            fs::create_dir_all(&self.staging_dir)?;
            fs::rename(&stage_path, &snapshot_path)?;
            fsync_dir(&self.snapshots_dir);

            if activate {
                let pointer = CurrentPointer {
                    revision: revision_id.clone(),
                };
                if let Err(error) = atomic_write_json(
                    &self.current_path,
                    &serde_json::to_value(pointer).map_err(LlmixError::from)?,
                ) {
                    let _ = fs::remove_dir_all(&snapshot_path);
                    fsync_dir(&self.snapshots_dir);
                    return Err(error);
                }
            }

            Ok(PublishedRevision {
                revision: revision_id.clone(),
                snapshot_path: snapshot_path.clone(),
                manifest_path: snapshot_path.join("manifest.json"),
                activated: activate,
                preset_ids: manifest.presets.keys().cloned().collect(),
            })
        })();

        if publish_result.is_err() && stage_path.exists() {
            let _ = fs::remove_dir_all(&stage_path);
        }

        publish_result
    }

    fn discover_presets(&self) -> LlmixResult<Vec<PresetSource>> {
        validate_authoring_directory(&self.authoring_dir)?;

        let mut presets = Vec::new();
        for module_entry in fs::read_dir(&self.authoring_dir)? {
            let module_entry = module_entry?;
            let module_path = module_entry.path();
            let module_type = module_entry.file_type()?;
            if module_type.is_symlink() {
                return Err(InvalidConfigError {
                    message: format!(
                        "Config Registry authoring modules must not be symlinks: {}",
                        module_path.display()
                    ),
                }
                .into());
            }
            if !module_type.is_dir() {
                continue;
            }

            let Some(module_name) = module_path.file_name().and_then(|value| value.to_str()) else {
                continue;
            };
            validate_module(module_name)?;

            for preset_entry in fs::read_dir(&module_path)? {
                let preset_entry = preset_entry?;
                let preset_path = preset_entry.path();
                let preset_type = preset_entry.file_type()?;
                if preset_type.is_symlink() {
                    return Err(InvalidConfigError {
                        message: format!(
                            "Config Registry authoring presets must not be symlinks: {}",
                            preset_path.display()
                        ),
                    }
                    .into());
                }
                if !preset_type.is_file() {
                    continue;
                }

                let Some(file_name) = preset_path.file_name().and_then(|value| value.to_str())
                else {
                    continue;
                };
                if is_legacy_preset_filename(file_name) {
                    return Err(InvalidConfigError {
                        message: format!(
                            "Config Registry authoring presets must use .mda files; YAML presets are no longer supported: {}",
                            preset_path.display()
                        ),
                    }
                    .into());
                }
                let Some(preset_name) = parse_preset_filename(file_name) else {
                    continue;
                };

                validate_preset(&preset_name)?;

                presets.push(PresetSource {
                    module: module_name.to_string(),
                    preset_id: format!("{module_name}/{preset_name}"),
                    preset: preset_name,
                    authoring_path: preset_path,
                });
            }
        }

        presets.sort_by(|left, right| left.preset_id.cmp(&right.preset_id));
        Ok(presets)
    }

    fn build_revision_id(
        &self,
        presets: &[PresetSource],
        published_at: u128,
    ) -> LlmixResult<String> {
        let mut digest = Sha256::new();
        for preset in presets {
            let relative_path = preset
                .authoring_path
                .strip_prefix(&self.authoring_dir)
                .map_err(|_| SecurityError {
                    message: format!(
                        "Authoring preset escaped registry root: {}",
                        preset.authoring_path.display()
                    ),
                })?;
            digest.update(relative_path.to_string_lossy().as_bytes());
            digest.update([0u8]);
            digest.update(read_authoring_file_bytes(&preset.authoring_path)?);
            digest.update([0u8]);
        }

        let hash = format!("{:x}", digest.finalize());
        Ok(format!("r{published_at}_{}", &hash[..8]))
    }

    fn build_staged_snapshot(
        &self,
        stage_path: &Path,
        presets: &[PresetSource],
        revision_id: &str,
        published_at: &str,
    ) -> LlmixResult<RegistryManifest> {
        let mut manifest_presets = BTreeMap::new();
        fs::create_dir_all(stage_path)?;

        for preset in presets {
            let authoring_rel = format!("authoring/{}/{}.mda", preset.module, preset.preset);
            let resolved_rel = format!("resolved/{}/{}.json", preset.module, preset.preset);

            let authoring_bytes = read_authoring_file_bytes(&preset.authoring_path)?;
            let staged_authoring_path = stage_path.join(&authoring_rel);
            write_bytes(&staged_authoring_path, &authoring_bytes)?;

            let resolved = load_config(&staged_authoring_path)?;
            validate_resolved_config(&staged_authoring_path, &resolved)?;
            let resolved_bytes = canonical_json_bytes(&resolved)?;

            write_bytes(&stage_path.join(&resolved_rel), &resolved_bytes)?;

            manifest_presets.insert(
                preset.preset_id.clone(),
                ManifestPresetEntry {
                    authoring_path: authoring_rel,
                    authoring_sha256: sha256_bytes(&authoring_bytes),
                    resolved_path: resolved_rel,
                    resolved_sha256: sha256_bytes(&resolved_bytes),
                },
            );
        }

        let manifest = RegistryManifest {
            revision: revision_id.to_string(),
            published_at: published_at.to_string(),
            schema_version: MANIFEST_SCHEMA_VERSION,
            presets: manifest_presets,
        };
        write_json(
            &stage_path.join("manifest.json"),
            &serde_json::to_value(&manifest).map_err(LlmixError::from)?,
        )?;
        Ok(manifest)
    }

    fn verify_staged_snapshot(
        &self,
        stage_path: &Path,
        manifest: &RegistryManifest,
    ) -> LlmixResult<()> {
        let stored_manifest = read_json_object(&stage_path.join("manifest.json"))?;
        let expected_manifest = serde_json::to_value(manifest).map_err(LlmixError::from)?;
        if Value::Object(stored_manifest) != expected_manifest {
            return Err(InvalidConfigError {
                message: "Staged registry manifest changed during verification".to_string(),
            }
            .into());
        }

        for (preset_id, entry) in &manifest.presets {
            for (relative_path, expected_sha) in [
                (&entry.authoring_path, &entry.authoring_sha256),
                (&entry.resolved_path, &entry.resolved_sha256),
            ] {
                let artifact_path = safe_join_relative(stage_path, relative_path)?;
                let actual_sha = sha256_file(&artifact_path)?;
                if actual_sha != *expected_sha {
                    return Err(InvalidConfigError {
                        message: format!(
                            "Checksum mismatch for staged registry artifact {} ({preset_id})",
                            artifact_path.display()
                        ),
                    }
                    .into());
                }
            }
        }

        Ok(())
    }
}

#[derive(Debug)]
pub struct ConfigRegistryManager {
    root: PathBuf,
    snapshots_dir: PathBuf,
    current_path: PathBuf,
    active_revision: String,
    configs: BTreeMap<String, Value>,
    last_reload_error: Option<LlmixError>,
    last_successful_reload_at: Option<SystemTime>,
    last_reload_failure_at: Option<SystemTime>,
}

impl ConfigRegistryManager {
    pub fn open<P>(root: P) -> LlmixResult<Self>
    where
        P: AsRef<Path>,
    {
        let root = absolutize_user_path(root.as_ref())?;
        let snapshots_dir = root.join("snapshots");
        let current_path = root.join("current.json");
        let active_revision = read_current_revision(&current_path)?;
        let configs = load_revision(&snapshots_dir, &active_revision)?;

        Ok(Self {
            root,
            snapshots_dir,
            current_path,
            active_revision,
            configs,
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
        let current_revision = match read_current_revision(&self.current_path) {
            Ok(value) => value,
            Err(error) => {
                self.last_reload_error = Some(error);
                self.last_reload_failure_at = Some(SystemTime::now());
                return;
            }
        };

        if current_revision == self.active_revision {
            return;
        }

        match load_revision(&self.snapshots_dir, &current_revision) {
            Ok(configs) => {
                self.active_revision = current_revision;
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

fn load_revision(snapshots_dir: &Path, revision: &str) -> LlmixResult<BTreeMap<String, Value>> {
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

fn read_current_revision(current_path: &Path) -> LlmixResult<String> {
    let pointer_value = Value::Object(read_json_object(current_path)?);
    let pointer: CurrentPointer =
        serde_json::from_value(pointer_value).map_err(|error| InvalidConfigError {
            message: format!(
                "Invalid Config Registry pointer {}: {error}",
                current_path.display()
            ),
        })?;
    validate_revision(&pointer.revision)?;
    Ok(pointer.revision)
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

fn validate_manifest_entry_string(value: &str, key: &str, preset_id: &str) -> LlmixResult<()> {
    if value.is_empty() {
        return Err(InvalidConfigError {
            message: format!("Config Registry manifest entry is missing {key}: {preset_id}"),
        }
        .into());
    }

    Ok(())
}

fn parse_preset_filename(file_name: &str) -> Option<String> {
    file_name
        .strip_suffix(".mda")
        .map(|preset| preset.to_string())
}

fn is_legacy_preset_filename(file_name: &str) -> bool {
    let lower = file_name.to_ascii_lowercase();
    lower.ends_with(".yaml") || lower.ends_with(".yml")
}

fn validate_authoring_directory(path: &Path) -> LlmixResult<()> {
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

fn validate_revision(revision: &str) -> LlmixResult<()> {
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

fn validate_resolved_config(path: &Path, value: &Value) -> LlmixResult<()> {
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

fn canonical_json_bytes(value: &Value) -> LlmixResult<Vec<u8>> {
    let mut content = canonical_json::to_string(value)?;
    content.push('\n');
    Ok(content.into_bytes())
}

fn read_authoring_file_bytes(path: &Path) -> LlmixResult<Vec<u8>> {
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

fn sha256_bytes(content: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(content);
    format!("{:x}", digest.finalize())
}

fn sha256_file(path: &Path) -> LlmixResult<String> {
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

fn read_json_object(path: &Path) -> LlmixResult<Map<String, Value>> {
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

fn write_bytes(path: &Path, content: &[u8]) -> LlmixResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)?;
    fsync_file(path);
    Ok(())
}

fn write_json(path: &Path, value: &Value) -> LlmixResult<()> {
    write_bytes(path, &canonical_json_bytes(value)?)
}

fn atomic_write_json(path: &Path, value: &Value) -> LlmixResult<()> {
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

fn staging_attempt_path(staging_dir: &Path, revision_id: &str, published_at: u128) -> PathBuf {
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

fn fsync_dir(path: &Path) {
    let Ok(directory) = File::open(path) else {
        return;
    };
    let _ = directory.sync_all();
}

fn safe_join_relative(base: &Path, relative: &str) -> LlmixResult<PathBuf> {
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

fn absolutize_user_path(path: &Path) -> LlmixResult<PathBuf> {
    let expanded = expand_home(path)?;
    if expanded.is_absolute() {
        Ok(expanded)
    } else {
        Ok(env::current_dir()?.join(expanded))
    }
}

fn current_unix_millis() -> LlmixResult<u128> {
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
