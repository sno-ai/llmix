use super::fs_ops::{
    read_json_object, safe_join_relative, sha256_bytes, sha256_file, validate_revision,
};
use super::root::{
    current_pointer_bytes, normalize_sha256_digest, registry_root_file_digests,
    registry_root_signature_to_mda, registry_root_signing_input, snapshot_registry_path,
    snapshot_relative_path, sort_registry_root_files, validate_registry_root_signature,
    validate_sha256,
};
use super::*;
use chrono::DateTime;

pub(super) fn verify_signed_registry_root_if_needed(
    pointer: &CurrentPointer,
    manifest: &RegistryManifest,
    current_path: &Path,
    snapshot_path: &Path,
    options: Option<&RegistryRootVerificationOptions>,
) -> LlmixResult<()> {
    let Some(options) = options else {
        return Ok(());
    };
    let root_path = snapshot_path.join(REGISTRY_ROOT_FILENAME);
    let root_digest = sha256_file(&root_path)?;
    let current_digest = sha256_file(current_path)?;
    let envelope = parse_registry_root_envelope(&root_path)?;
    verify_registry_root_signatures(&envelope, options)?;
    enforce_registry_root_freshness(&envelope, options, &root_digest)?;
    verify_registry_root_payload(
        &envelope.payload,
        pointer,
        manifest,
        &current_digest,
        snapshot_path,
    )
}

fn parse_registry_root_envelope(path: &Path) -> LlmixResult<RegistryRootEnvelope> {
    let value = Value::Object(read_json_object(path)?);
    let envelope: RegistryRootEnvelope =
        serde_json::from_value(value).map_err(|error| InvalidConfigError {
            message: format!("Invalid registry root envelope {}: {error}", path.display()),
        })?;
    if envelope.schema != REGISTRY_ROOT_ENVELOPE_SCHEMA {
        return Err(InvalidConfigError {
            message: format!(
                "Unsupported registry root envelope schema in {}",
                path.display()
            ),
        }
        .into());
    }
    if envelope.schema_version != REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION {
        return Err(InvalidConfigError {
            message: format!(
                "Unsupported registry root envelope schema version in {}",
                path.display()
            ),
        }
        .into());
    }
    validate_registry_root_payload(&envelope.payload, &path.display().to_string())?;
    validate_registry_root_integrity(&envelope.integrity, &path.display().to_string())?;
    validate_sha256(&envelope.payload_sha256, "registry root payload")?;
    if envelope.integrity.digest != format!("sha256:{}", envelope.payload_sha256) {
        return Err(InvalidConfigError {
            message: "Registry root integrity digest does not match payload_sha256".to_string(),
        }
        .into());
    }
    for signature in &envelope.signatures {
        validate_registry_root_signature(signature)?;
    }
    Ok(envelope)
}

fn verify_registry_root_signatures(
    envelope: &RegistryRootEnvelope,
    options: &RegistryRootVerificationOptions,
) -> LlmixResult<()> {
    let signing_input = registry_root_signing_input(envelope.payload.clone())?;
    if signing_input.payload_sha256 != envelope.payload_sha256
        || signing_input.integrity.digest != envelope.integrity.digest
    {
        return Err(InvalidConfigError {
            message: "Registry root payload digest mismatch".to_string(),
        }
        .into());
    }

    let signatures: Vec<SignatureEntry> = envelope
        .signatures
        .iter()
        .map(registry_root_signature_to_mda)
        .collect();
    verify_signatures_with_payload(
        &signatures,
        &envelope.integrity,
        &options.trust_policy,
        options
            .rekor_client
            .as_deref()
            .map(|value| value as &dyn RekorClient),
        options
            .sigstore_verifier
            .as_deref()
            .map(|value| value as &dyn SigstoreVerifier),
        options
            .did_web_verifier
            .as_deref()
            .map(|value| value as &dyn DidWebVerifier),
        signing_input.canonical_payload.as_bytes(),
    )
    .map_err(|error| {
        SecurityError {
            message: format!("Registry root signature verification failed: {error}"),
        }
        .into()
    })
}

fn enforce_registry_root_freshness(
    envelope: &RegistryRootEnvelope,
    options: &RegistryRootVerificationOptions,
    root_digest: &str,
) -> LlmixResult<()> {
    let payload = &envelope.payload;
    if let Some(expected_revision) = &options.expected_revision {
        if payload.revision != *expected_revision {
            return Err(SecurityError {
                message: format!(
                    "Registry root revision mismatch: expected {expected_revision}, got {}",
                    payload.revision
                ),
            }
            .into());
        }
    }
    if let Some(minimum_revision) = &options.minimum_revision {
        validate_revision(minimum_revision)?;
        if compare_revision(&payload.revision, minimum_revision) < 0 {
            return Err(SecurityError {
                message: format!(
                    "Registry root revision {} is older than minimum {minimum_revision}",
                    payload.revision
                ),
            }
            .into());
        }
    }
    if let Some(minimum_published_at) = &options.minimum_published_at {
        if compare_published_at(&payload.published_at, minimum_published_at)? < 0 {
            return Err(SecurityError {
                message: format!(
                    "Registry root published_at {} is older than minimum {minimum_published_at}",
                    payload.published_at
                ),
            }
            .into());
        }
    }
    if let Some(expected_root_digest) = &options.expected_root_digest {
        let expected_root_digest =
            normalize_sha256_digest(expected_root_digest, "expected registry root digest")?;
        if root_digest != expected_root_digest {
            return Err(SecurityError {
                message: "Registry root digest does not match expected_root_digest".to_string(),
            }
            .into());
        }
    }
    if let Some(high_watermark) = &options.high_watermark {
        let input = registry_root_signing_input(envelope.payload.clone())?;
        if !high_watermark.accept_registry_root(envelope, &input)? {
            return Err(SecurityError {
                message: "Registry root rejected by high-watermark policy".to_string(),
            }
            .into());
        }
    }
    Ok(())
}

fn verify_registry_root_payload(
    payload: &RegistryRootPayload,
    pointer: &CurrentPointer,
    manifest: &RegistryManifest,
    current_digest: &str,
    snapshot_path: &Path,
) -> LlmixResult<()> {
    verify_registry_root_bindings(payload, pointer, manifest, current_digest)?;
    let expected_files = registry_root_file_digests(manifest)?;
    let mut actual_files = payload.files.clone();
    sort_registry_root_files(&mut actual_files);
    if canonical_json::to_string(&serde_json::to_value(&actual_files).map_err(LlmixError::from)?)?
        != canonical_json::to_string(
            &serde_json::to_value(&expected_files).map_err(LlmixError::from)?,
        )?
    {
        return Err(SecurityError {
            message: "Registry root file digest set does not match the selected manifest"
                .to_string(),
        }
        .into());
    }

    for file in actual_files {
        let relative_path = snapshot_relative_path(&pointer.revision, &file.path)?;
        let artifact_path = safe_join_relative(snapshot_path, &relative_path)?;
        let actual_sha = sha256_file(&artifact_path)?;
        if actual_sha != file.sha256 {
            return Err(SecurityError {
                message: format!("Registry root file digest mismatch: {}", file.path),
            }
            .into());
        }
    }
    Ok(())
}

fn verify_registry_root_bindings(
    payload: &RegistryRootPayload,
    pointer: &CurrentPointer,
    manifest: &RegistryManifest,
    current_digest: &str,
) -> LlmixResult<()> {
    let manifest_sha256 = pointer
        .manifest_sha256
        .as_deref()
        .ok_or_else(|| InvalidConfigError {
            message: "Registry current pointer is missing manifest_sha256".to_string(),
        })?;
    if payload.revision != pointer.revision || payload.revision != manifest.revision {
        return Err(SecurityError {
            message: "Registry root revision does not match the active current pointer".to_string(),
        }
        .into());
    }
    if payload.current.revision != pointer.revision
        || payload.current.manifest_sha256 != manifest_sha256
    {
        return Err(SecurityError {
            message: "Registry root current binding does not match current.json".to_string(),
        }
        .into());
    }
    if payload.current.sha256 != current_digest {
        return Err(SecurityError {
            message: "Registry root current binding digest mismatch".to_string(),
        }
        .into());
    }
    if payload.manifest.path != snapshot_registry_path(&pointer.revision, "manifest.json") {
        return Err(SecurityError {
            message: "Registry root manifest path does not match the active snapshot".to_string(),
        }
        .into());
    }
    if payload.manifest.sha256 != manifest_sha256 {
        return Err(SecurityError {
            message: "Registry root manifest digest does not match current.json".to_string(),
        }
        .into());
    }
    Ok(())
}

fn validate_registry_root_payload(payload: &RegistryRootPayload, source: &str) -> LlmixResult<()> {
    if payload.schema != REGISTRY_ROOT_SCHEMA {
        return Err(InvalidConfigError {
            message: format!("Unsupported registry root payload schema in {source}"),
        }
        .into());
    }
    if payload.schema_version != REGISTRY_ROOT_SCHEMA_VERSION {
        return Err(InvalidConfigError {
            message: format!("Unsupported registry root payload schema version in {source}"),
        }
        .into());
    }
    validate_revision(&payload.revision)?;
    if payload.current.path != "current.json" {
        return Err(InvalidConfigError {
            message: format!("Registry root current binding must point to current.json: {source}"),
        }
        .into());
    }
    validate_revision(&payload.current.revision)?;
    validate_sha256(
        &payload.current.manifest_sha256,
        "registry root current manifest",
    )?;
    validate_sha256(&payload.current.sha256, "registry root current binding")?;
    let current_pointer = CurrentPointer {
        revision: payload.current.revision.clone(),
        manifest_sha256: Some(payload.current.manifest_sha256.clone()),
    };
    let current_sha256 = sha256_bytes(&current_pointer_bytes(&current_pointer)?);
    if payload.current.sha256 != current_sha256 {
        return Err(InvalidConfigError {
            message: format!("Registry root current binding digest mismatch: {source}"),
        }
        .into());
    }
    validate_sha256(&payload.manifest.sha256, "registry root manifest")?;
    for file in &payload.files {
        if file.role != "authoring" && file.role != "resolved" {
            return Err(InvalidConfigError {
                message: format!("Registry root file entry has invalid role: {source}"),
            }
            .into());
        }
        validate_sha256(&file.sha256, "registry root file")?;
    }
    Ok(())
}

fn validate_registry_root_integrity(integrity: &IntegrityField, source: &str) -> LlmixResult<()> {
    if integrity.algorithm != HashAlgorithm::Sha256 {
        return Err(InvalidConfigError {
            message: format!("Registry root integrity must use sha256: {source}"),
        }
        .into());
    }
    let Some(digest) = integrity.digest.strip_prefix("sha256:") else {
        return Err(InvalidConfigError {
            message: format!("Registry root integrity digest must be sha256-prefixed: {source}"),
        }
        .into());
    };
    validate_sha256(digest, "registry root integrity")
}

fn compare_published_at(left: &str, right: &str) -> LlmixResult<i8> {
    let left = parse_published_at(left)?;
    let right = parse_published_at(right)?;
    Ok(match left.cmp(&right) {
        std::cmp::Ordering::Less => -1,
        std::cmp::Ordering::Equal => 0,
        std::cmp::Ordering::Greater => 1,
    })
}

fn parse_published_at(value: &str) -> LlmixResult<i64> {
    if let Ok(millis) = value.parse::<i64>() {
        return Ok(millis);
    }
    DateTime::parse_from_rfc3339(value)
        .map(|timestamp| timestamp.timestamp_millis())
        .map_err(|error| {
            InvalidConfigError {
                message: format!("Invalid registry root published_at value {value:?}: {error}"),
            }
            .into()
        })
}

pub(super) fn compare_revision(left: &str, right: &str) -> i8 {
    let left_tokens = revision_tokens(left);
    let right_tokens = revision_tokens(right);
    for (left, right) in left_tokens.iter().zip(right_tokens.iter()) {
        let ordering = match (left, right) {
            (RevisionToken::Number(left), RevisionToken::Number(right)) => left.cmp(right),
            (RevisionToken::Text(left), RevisionToken::Text(right)) => left.cmp(right),
            (RevisionToken::Number(_), RevisionToken::Text(_)) => std::cmp::Ordering::Less,
            (RevisionToken::Text(_), RevisionToken::Number(_)) => std::cmp::Ordering::Greater,
        };
        if ordering != std::cmp::Ordering::Equal {
            return match ordering {
                std::cmp::Ordering::Less => -1,
                std::cmp::Ordering::Equal => 0,
                std::cmp::Ordering::Greater => 1,
            };
        }
    }
    match left_tokens.len().cmp(&right_tokens.len()) {
        std::cmp::Ordering::Less => -1,
        std::cmp::Ordering::Equal => 0,
        std::cmp::Ordering::Greater => 1,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum RevisionToken {
    Number(u128),
    Text(String),
}

fn revision_tokens(value: &str) -> Vec<RevisionToken> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut current_is_digit = None;

    for character in value.chars() {
        let is_digit = character.is_ascii_digit();
        if current_is_digit == Some(is_digit) {
            current.push(character);
            continue;
        }
        if !current.is_empty() {
            tokens.push(revision_token(&current, current_is_digit.unwrap_or(false)));
            current.clear();
        }
        current_is_digit = Some(is_digit);
        current.push(character);
    }
    if !current.is_empty() {
        tokens.push(revision_token(&current, current_is_digit.unwrap_or(false)));
    }
    tokens
}

fn revision_token(value: &str, is_digit: bool) -> RevisionToken {
    if is_digit {
        RevisionToken::Number(value.parse::<u128>().unwrap_or(u128::MAX))
    } else {
        RevisionToken::Text(value.to_string())
    }
}
