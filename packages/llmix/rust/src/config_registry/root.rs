use super::fs_ops::{canonical_json_bytes, sha256_bytes, validate_manifest_entry_string};
use super::*;

pub(super) fn build_registry_root_payload(
    manifest: &RegistryManifest,
    manifest_sha256: &str,
) -> LlmixResult<RegistryRootPayload> {
    validate_sha256(manifest_sha256, "registry root manifest")?;
    let current_pointer = CurrentPointer {
        revision: manifest.revision.clone(),
        manifest_sha256: Some(manifest_sha256.to_string()),
    };
    let current_sha256 = sha256_bytes(&current_pointer_bytes(&current_pointer)?);

    Ok(RegistryRootPayload {
        schema: REGISTRY_ROOT_SCHEMA.to_string(),
        schema_version: REGISTRY_ROOT_SCHEMA_VERSION,
        revision: manifest.revision.clone(),
        published_at: manifest.published_at.clone(),
        current: RegistryRootCurrentBinding {
            path: "current.json".to_string(),
            revision: manifest.revision.clone(),
            manifest_sha256: manifest_sha256.to_string(),
            sha256: current_sha256,
        },
        manifest: RegistryRootManifestBinding {
            path: snapshot_registry_path(&manifest.revision, "manifest.json"),
            sha256: manifest_sha256.to_string(),
        },
        files: registry_root_file_digests(manifest)?,
    })
}

pub(super) fn create_registry_root_envelope(
    payload: RegistryRootPayload,
    options: &RegistryRootSigningOptions<'_>,
) -> LlmixResult<RegistryRootEnvelope> {
    let signing_input = registry_root_signing_input(payload)?;
    let signatures = options.signer.sign_registry_root(&signing_input)?;
    let required = required_signature_count(options.min_signatures)?;
    if signatures.len() < required {
        return Err(InvalidConfigError {
            message: format!(
                "Registry root signer returned {} signatures; expected at least {required}",
                signatures.len()
            ),
        }
        .into());
    }
    for signature in &signatures {
        validate_registry_root_signature(signature)?;
        if signature.payload_digest != signing_input.integrity.digest {
            return Err(InvalidConfigError {
                message: "Registry root signature payload-digest mismatch".to_string(),
            }
            .into());
        }
        if signature.payload_type.as_deref() != Some(REGISTRY_ROOT_PAYLOAD_TYPE) {
            return Err(InvalidConfigError {
                message: "Registry root signature payload-type mismatch".to_string(),
            }
            .into());
        }
    }

    Ok(RegistryRootEnvelope {
        schema: REGISTRY_ROOT_ENVELOPE_SCHEMA.to_string(),
        schema_version: REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
        payload: signing_input.payload,
        integrity: signing_input.integrity,
        payload_sha256: signing_input.payload_sha256,
        signatures,
    })
}

pub(super) fn registry_root_signing_input(
    payload: RegistryRootPayload,
) -> LlmixResult<RegistryRootSigningInput> {
    let canonical_payload = canonical_registry_root_payload(&payload)?;
    let payload_sha256 = sha256_bytes(canonical_payload.as_bytes());
    let integrity = IntegrityField {
        algorithm: HashAlgorithm::Sha256,
        digest: format!("sha256:{payload_sha256}"),
    };
    Ok(RegistryRootSigningInput {
        payload,
        canonical_payload,
        integrity,
        payload_type: REGISTRY_ROOT_PAYLOAD_TYPE.to_string(),
        payload_sha256,
    })
}

pub(super) fn canonical_registry_root_payload(
    payload: &RegistryRootPayload,
) -> LlmixResult<String> {
    canonical_json::to_string(&serde_json::to_value(payload).map_err(LlmixError::from)?)
}

pub(super) fn registry_root_file_digests(
    manifest: &RegistryManifest,
) -> LlmixResult<Vec<RegistryRootFileDigest>> {
    let mut files = Vec::new();
    for entry in manifest.presets.values() {
        validate_manifest_entry_string(&entry.authoring_path, "authoring_path", "<registry-root>")?;
        validate_manifest_entry_string(
            &entry.authoring_sha256,
            "authoring_sha256",
            "<registry-root>",
        )?;
        validate_manifest_entry_string(&entry.resolved_path, "resolved_path", "<registry-root>")?;
        validate_manifest_entry_string(
            &entry.resolved_sha256,
            "resolved_sha256",
            "<registry-root>",
        )?;
        validate_sha256(&entry.authoring_sha256, "registry root authoring file")?;
        validate_sha256(&entry.resolved_sha256, "registry root resolved file")?;
        files.push(RegistryRootFileDigest {
            path: snapshot_registry_path(&manifest.revision, &entry.authoring_path),
            sha256: entry.authoring_sha256.clone(),
            role: "authoring".to_string(),
        });
        files.push(RegistryRootFileDigest {
            path: snapshot_registry_path(&manifest.revision, &entry.resolved_path),
            sha256: entry.resolved_sha256.clone(),
            role: "resolved".to_string(),
        });
    }
    files.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then(left.role.cmp(&right.role))
            .then(left.sha256.cmp(&right.sha256))
    });
    Ok(files)
}

pub(super) fn validate_registry_root_signature(
    signature: &RegistryRootSignature,
) -> LlmixResult<()> {
    if signature.signer.is_empty()
        || signature.key_id.is_empty()
        || signature.signature.is_empty()
        || signature.payload_digest.is_empty()
    {
        return Err(InvalidConfigError {
            message: "Registry root signature is missing required fields".to_string(),
        }
        .into());
    }
    if !matches!(
        signature.algorithm.as_str(),
        "ed25519" | "ecdsa-p256" | "rsa-pss-sha256"
    ) {
        return Err(InvalidConfigError {
            message: "Registry root signature has unsupported algorithm".to_string(),
        }
        .into());
    }
    if signature.payload_type.as_deref() != Some(REGISTRY_ROOT_PAYLOAD_TYPE) {
        return Err(InvalidConfigError {
            message: "Registry root signature payload-type mismatch".to_string(),
        }
        .into());
    }
    Ok(())
}

pub(super) fn registry_root_signature_to_mda(signature: &RegistryRootSignature) -> SignatureEntry {
    SignatureEntry {
        signer: signature.signer.clone(),
        key_id: signature.key_id.clone(),
        payload_digest: signature.payload_digest.clone(),
        algorithm: signature.algorithm.clone(),
        signature: signature.signature.clone(),
        rekor_log_id: signature.rekor_log_id.clone(),
        rekor_log_index: signature.rekor_log_index,
        payload_type: signature.payload_type.clone(),
    }
}

pub(super) fn sort_registry_root_files(files: &mut [RegistryRootFileDigest]) {
    files.sort_by(|left, right| {
        left.path
            .cmp(&right.path)
            .then(left.role.cmp(&right.role))
            .then(left.sha256.cmp(&right.sha256))
    });
}

pub(super) fn current_pointer_bytes(pointer: &CurrentPointer) -> LlmixResult<Vec<u8>> {
    let value = serde_json::to_value(pointer).map_err(LlmixError::from)?;
    canonical_json_bytes(&value)
}

pub(super) fn snapshot_registry_path(revision: &str, relative_path: &str) -> String {
    format!("snapshots/{revision}/{relative_path}")
}

pub(super) fn snapshot_relative_path(revision: &str, registry_path: &str) -> LlmixResult<String> {
    let prefix = format!("snapshots/{revision}/");
    registry_path
        .strip_prefix(&prefix)
        .map(str::to_string)
        .ok_or_else(|| {
            SecurityError {
                message: format!(
                    "Registry root file path is outside active snapshot: {registry_path}"
                ),
            }
            .into()
        })
}

pub(super) fn validate_sha256(value: &str, label: &str) -> LlmixResult<()> {
    if value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Ok(());
    }
    Err(InvalidConfigError {
        message: format!("{label} has invalid sha256 digest"),
    }
    .into())
}

pub(super) fn normalize_sha256_digest(value: &str, label: &str) -> LlmixResult<String> {
    let normalized = value
        .strip_prefix("sha256:")
        .unwrap_or(value)
        .to_ascii_lowercase();
    validate_sha256(&normalized, label)?;
    Ok(normalized)
}

pub(super) fn required_signature_count(value: Option<usize>) -> LlmixResult<usize> {
    match value {
        Some(0) => Err(InvalidConfigError {
            message: "Registry root min_signatures must be >= 1".to_string(),
        }
        .into()),
        Some(count) => Ok(count),
        None => Ok(1),
    }
}
