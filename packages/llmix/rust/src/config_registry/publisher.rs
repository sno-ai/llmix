use super::fs_ops::{
    absolutize_user_path, atomic_write_json, canonical_json_bytes, current_unix_millis, fsync_dir,
    is_legacy_preset_filename, parse_preset_filename, read_authoring_file_bytes, read_json_object,
    safe_join_relative, sha256_bytes, sha256_file, staging_attempt_path,
    validate_authoring_directory, validate_resolved_config, validate_revision, write_bytes,
    write_json,
};
use super::root::{build_registry_root_payload, create_registry_root_envelope};
use super::*;
use chrono::{DateTime, SecondsFormat, Utc};

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
        self.publish_with_mda_options(revision, activate, &MdaConfigLoadOptions::default())
    }

    pub fn publish_with_mda_options(
        &self,
        revision: Option<&str>,
        activate: bool,
        mda_options: &MdaConfigLoadOptions<'_>,
    ) -> LlmixResult<PublishedRevision> {
        self.publish_with_registry_options(&ConfigRegistryPublishOptions {
            revision,
            activate,
            mda_options: mda_options.clone(),
            registry_root: None,
        })
    }

    pub fn publish_with_registry_options(
        &self,
        options: &ConfigRegistryPublishOptions<'_>,
    ) -> LlmixResult<PublishedRevision> {
        let presets = self.discover_presets()?;
        if presets.is_empty() {
            return Err(ConfigNotFoundError {
                path: self.authoring_dir.display().to_string(),
            }
            .into());
        }

        let published_at = current_unix_millis()?;
        let published_at_text = unix_millis_to_rfc3339(published_at)?;

        let revision_id = match options.revision {
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
                &options.mda_options,
            )?;
            self.verify_staged_snapshot(&stage_path, &manifest)?;
            let manifest_path = stage_path.join("manifest.json");
            let manifest_sha256 = sha256_file(&manifest_path)?;
            let registry_root_sha256 = self.write_registry_root_if_requested(
                &stage_path,
                &manifest,
                &manifest_sha256,
                options.registry_root.as_ref(),
            )?;

            fs::create_dir_all(&self.snapshots_dir)?;
            fs::create_dir_all(&self.staging_dir)?;
            fs::rename(&stage_path, &snapshot_path)?;
            fsync_dir(&self.snapshots_dir);

            if options.activate {
                let pointer = CurrentPointer {
                    revision: revision_id.clone(),
                    manifest_sha256: Some(manifest_sha256.clone()),
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
                manifest_sha256,
                registry_root_path: registry_root_sha256
                    .as_ref()
                    .map(|_| snapshot_path.join(REGISTRY_ROOT_FILENAME)),
                registry_root_sha256,
                activated: options.activate,
                preset_ids: manifest.presets.keys().cloned().collect(),
            })
        })();

        if publish_result.is_err() && stage_path.exists() {
            let _ = fs::remove_dir_all(&stage_path);
        }

        publish_result
    }

    fn write_registry_root_if_requested(
        &self,
        stage_path: &Path,
        manifest: &RegistryManifest,
        manifest_sha256: &str,
        options: Option<&RegistryRootSigningOptions<'_>>,
    ) -> LlmixResult<Option<String>> {
        let Some(options) = options else {
            return Ok(None);
        };
        let payload = build_registry_root_payload(manifest, manifest_sha256)?;
        let envelope = create_registry_root_envelope(payload, options)?;
        let root_path = stage_path.join(REGISTRY_ROOT_FILENAME);
        write_json(
            &root_path,
            &serde_json::to_value(envelope).map_err(LlmixError::from)?,
        )?;
        Ok(Some(sha256_file(&root_path)?))
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
        mda_options: &MdaConfigLoadOptions<'_>,
    ) -> LlmixResult<RegistryManifest> {
        let mut manifest_presets = BTreeMap::new();
        fs::create_dir_all(stage_path)?;

        for preset in presets {
            let authoring_rel = format!("authoring/{}/{}.mda", preset.module, preset.preset);
            let resolved_rel = format!("resolved/{}/{}.json", preset.module, preset.preset);

            let authoring_bytes = read_authoring_file_bytes(&preset.authoring_path)?;
            let staged_authoring_path = stage_path.join(&authoring_rel);
            write_bytes(&staged_authoring_path, &authoring_bytes)?;

            let resolved = load_config_with_options(&staged_authoring_path, mda_options)?;
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

fn unix_millis_to_rfc3339(value: u128) -> LlmixResult<String> {
    let millis = i64::try_from(value).map_err(|_| InvalidConfigError {
        message: format!("Registry published_at value is too large: {value}"),
    })?;
    let timestamp =
        DateTime::<Utc>::from_timestamp_millis(millis).ok_or_else(|| InvalidConfigError {
            message: format!("Registry published_at value is out of range: {value}"),
        })?;
    Ok(timestamp.to_rfc3339_opts(SecondsFormat::Millis, true))
}
