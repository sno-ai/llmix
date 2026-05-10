use std::path::Path;

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::errors::{ErrorCategory, MdaConfigError, Result};
use crate::frontmatter::{extract_frontmatter, parse_frontmatter_yaml};
use crate::integrity::{verify_integrity as run_integrity_check, IntegrityField};
use crate::requires::{enforce_requires, RequiresEnvironment};
use crate::signature::{
    verify_signatures as run_signature_check, DidWebVerifier, RekorClient, SigstoreVerifier,
};
use crate::trust_policy::TrustPolicy;

#[derive(Default)]
pub struct LoadMdaSourceOptions<'a> {
    pub verify_integrity: bool,
    pub verify_signatures: bool,
    pub trusted_runtime: bool,
    pub enforce_requires: bool,
    pub allowed_networks: Vec<String>,
    pub trust_policy: Option<TrustPolicy>,
    pub rekor_client: Option<&'a dyn RekorClient>,
    pub sigstore_verifier: Option<&'a dyn SigstoreVerifier>,
    pub did_web_verifier: Option<&'a dyn DidWebVerifier>,
}

pub fn load_mda_source<T: DeserializeOwned>(
    path: impl AsRef<Path>,
    options: LoadMdaSourceOptions<'_>,
) -> Result<T> {
    let file_bytes = std::fs::read(path).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::InvalidEncoding,
            "failed to read .mda source file",
            serde_json::json!({ "cause": cause.to_string() }),
        )
    })?;
    load_mda_source_from_bytes(&file_bytes, options)
}

pub fn load_mda_source_from_bytes<T: DeserializeOwned>(
    file_bytes: &[u8],
    options: LoadMdaSourceOptions<'_>,
) -> Result<T> {
    let extracted = extract_frontmatter(file_bytes)?;
    if extracted.frontmatter_str.is_empty() {
        return Err(MdaConfigError::new(
            ErrorCategory::MissingRequiredFrontmatter,
            "source-mode .mda file has no opening '---' fence",
        ));
    }
    let frontmatter = parse_frontmatter_yaml(&extracted.frontmatter_str)?;

    validate_mda_source(&frontmatter)?;
    cross_field_check(&frontmatter)?;
    let trust_policy = if options.trusted_runtime || options.verify_signatures {
        Some(require_valid_trust_policy(options.trust_policy.as_ref())?)
    } else {
        None
    };

    let integrity = frontmatter
        .get("integrity")
        .cloned()
        .map(serde_json::from_value::<IntegrityField>)
        .transpose()
        .map_err(|cause| {
            MdaConfigError::with_details(
                ErrorCategory::SchemaViolation,
                "integrity field could not be decoded",
                serde_json::json!({ "cause": cause.to_string() }),
            )
        })?;
    let signatures = signature_entries(&frontmatter)?;
    if options.trusted_runtime && integrity.is_none() {
        return Err(MdaConfigError::new(
            ErrorCategory::MissingRequiredIntegrity,
            "trusted_runtime=true requires integrity",
        ));
    }

    if (options.trusted_runtime || options.verify_signatures) && signatures.is_empty() {
        return Err(MdaConfigError::new(
            if options.trusted_runtime {
                ErrorCategory::MissingRequiredSignature
            } else {
                ErrorCategory::SignatureVerificationFailure
            },
            "verify_signatures=true requires a non-empty signatures[] field",
        ));
    }

    if options.verify_integrity || options.verify_signatures || options.trusted_runtime {
        let integrity = integrity.as_ref().ok_or_else(|| {
            MdaConfigError::new(
                if options.trusted_runtime {
                    ErrorCategory::MissingRequiredIntegrity
                } else {
                    ErrorCategory::SchemaViolation
                },
                "verification requires an integrity field",
            )
        })?;
        run_integrity_check(&frontmatter, &extracted.body_str, integrity)?;
    }

    if options.verify_signatures || options.trusted_runtime {
        let integrity = integrity.as_ref().ok_or_else(|| {
            MdaConfigError::new(
                ErrorCategory::SignaturesWithoutIntegrity,
                "cannot verify signatures without an integrity anchor",
            )
        })?;
        run_signature_check(
            &signatures,
            integrity,
            trust_policy.expect("validated when signatures are verified"),
            options.rekor_client,
            options.sigstore_verifier,
            options.did_web_verifier,
        )?;
    }

    if options.enforce_requires {
        enforce_requires(
            frontmatter.get("requires"),
            &RequiresEnvironment {
                allowed_networks: options.allowed_networks,
            },
        )?;
    }

    serde_json::from_value(frontmatter).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::ProjectSchemaViolation,
            "consumer serde schema rejected the frontmatter",
            serde_json::json!({ "cause": cause.to_string() }),
        )
    })
}

fn require_valid_trust_policy(input: Option<&TrustPolicy>) -> Result<&TrustPolicy> {
    let Some(policy) = input else {
        return Err(MdaConfigError::new(
            ErrorCategory::TrustPolicyViolation,
            "trusted-runtime requires a valid trustPolicy",
        ));
    };
    policy.validate()?;
    Ok(policy)
}

fn validate_mda_source(frontmatter: &Value) -> Result<()> {
    let Some(object) = frontmatter.as_object() else {
        return schema_error("frontmatter must be an object", "$");
    };

    for key in object.keys() {
        if !TOP_LEVEL_KEYS.contains(&key.as_str()) {
            return schema_error("unknown top-level frontmatter key", key);
        }
    }

    if let Some(value) = object.get("name") {
        let Some(name) = value.as_str() else {
            return schema_error("name must be a string", "name");
        };
        if name.is_empty() || name.len() > 64 || !regex_match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name) {
            return schema_error("name has invalid MDA identifier shape", "name");
        }
    }
    if let Some(value) = object.get("description") {
        let Some(description) = value.as_str() else {
            return schema_error("description must be a string", "description");
        };
        if description.is_empty() || description.len() > 1024 {
            return schema_error("description length is invalid", "description");
        }
    }
    validate_optional_string(object, "license", None)?;
    validate_optional_string(object, "compatibility", Some(500))?;
    validate_optional_string(object, "allowed-tools", None)?;
    validate_optional_string(object, "title", None)?;
    validate_optional_string(object, "author", None)?;
    validate_pattern_string(
        object,
        "doc-id",
        r"^[a-zA-Z0-9_-]{8,}$",
        "doc-id has invalid shape",
    )?;
    validate_pattern_string(
        object,
        "version",
        r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$",
        "version has invalid shape",
    )?;
    validate_optional_string(object, "created-date", None)?;
    validate_optional_string(object, "updated-date", None)?;
    validate_metadata(object.get("metadata"))?;
    validate_requires_shape(object.get("requires"))?;
    validate_integrity_shape(object.get("integrity"))?;
    validate_signatures_shape(object.get("signatures"))?;
    validate_depends_on(object.get("depends-on"))?;
    validate_string_array(object.get("tags"), "tags")?;
    validate_object_array(object.get("relationships"), "relationships")?;
    Ok(())
}

const TOP_LEVEL_KEYS: &[&str] = &[
    "name",
    "description",
    "license",
    "compatibility",
    "allowed-tools",
    "metadata",
    "integrity",
    "signatures",
    "doc-id",
    "title",
    "version",
    "requires",
    "depends-on",
    "author",
    "tags",
    "created-date",
    "updated-date",
    "relationships",
];

fn schema_error<T>(message: &str, path: &str) -> Result<T> {
    Err(MdaConfigError::with_details(
        ErrorCategory::SchemaViolation,
        message,
        serde_json::json!({ "path": path }),
    ))
}

fn regex_match(pattern: &str, value: &str) -> bool {
    regex::Regex::new(pattern)
        .map(|re| re.is_match(value))
        .unwrap_or(false)
}

fn validate_optional_string(
    object: &serde_json::Map<String, Value>,
    key: &str,
    max_len: Option<usize>,
) -> Result<()> {
    let Some(value) = object.get(key) else {
        return Ok(());
    };
    let Some(string) = value.as_str() else {
        return schema_error("field must be a string", key);
    };
    if max_len.is_some_and(|limit| string.len() > limit) {
        return schema_error("field is too long", key);
    }
    Ok(())
}

fn validate_pattern_string(
    object: &serde_json::Map<String, Value>,
    key: &str,
    pattern: &str,
    message: &str,
) -> Result<()> {
    let Some(value) = object.get(key) else {
        return Ok(());
    };
    let Some(string) = value.as_str() else {
        return schema_error("field must be a string", key);
    };
    if !regex_match(pattern, string) {
        return schema_error(message, key);
    }
    Ok(())
}

fn validate_metadata(value: Option<&Value>) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(object) = value.as_object() else {
        return schema_error("metadata must be an object", "metadata");
    };
    for (key, value) in object {
        if !regex_match(r"^[a-z0-9]+(-[a-z0-9]+)*$", key) {
            return schema_error("metadata key has invalid shape", "metadata");
        }
        if !value.is_object() {
            return schema_error("metadata namespace value must be an object", key);
        }
    }
    Ok(())
}

fn validate_requires_shape(value: Option<&Value>) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(object) = value.as_object() else {
        return schema_error("requires must be an object", "requires");
    };
    for key in object.keys() {
        if key.is_empty() || key.len() > 64 || !regex_match(r"^[a-z0-9]+(-[a-z0-9]+)*$", key) {
            return schema_error("requires key has invalid shape", "requires");
        }
    }
    Ok(())
}

fn validate_integrity_shape(value: Option<&Value>) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(object) = value.as_object() else {
        return schema_error("integrity must be an object", "integrity");
    };
    for key in object.keys() {
        if key != "algorithm" && key != "digest" {
            return schema_error("integrity has an unknown key", "integrity");
        }
    }
    let Some(algorithm) = object.get("algorithm").and_then(Value::as_str) else {
        return schema_error("integrity.algorithm is required", "integrity.algorithm");
    };
    let Some(digest) = object.get("digest").and_then(Value::as_str) else {
        return schema_error("integrity.digest is required", "integrity.digest");
    };
    let pattern = match algorithm {
        "sha256" => r"^sha256:[0-9a-f]{64}$",
        "sha384" => r"^sha384:[0-9a-f]{96}$",
        "sha512" => r"^sha512:[0-9a-f]{128}$",
        _ => {
            return schema_error(
                "integrity.algorithm is not supported",
                "integrity.algorithm",
            )
        }
    };
    if !regex_match(pattern, digest) {
        return schema_error(
            "integrity.digest does not match algorithm",
            "integrity.digest",
        );
    }
    Ok(())
}

fn validate_signatures_shape(value: Option<&Value>) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(signatures) = value.as_array() else {
        return schema_error("signatures must be an array", "signatures");
    };
    if signatures.is_empty() {
        return schema_error("signatures must not be empty", "signatures");
    }
    for signature in signatures {
        validate_signature_shape(signature)?;
    }
    Ok(())
}

fn validate_signature_shape(value: &Value) -> Result<()> {
    let Some(object) = value.as_object() else {
        return schema_error("signature entry must be an object", "signatures");
    };
    for key in object.keys() {
        if !SIGNATURE_KEYS.contains(&key.as_str()) {
            return schema_error("signature entry has an unknown key", "signatures");
        }
    }
    for key in [
        "signer",
        "key-id",
        "payload-digest",
        "algorithm",
        "signature",
    ] {
        if !object.contains_key(key) {
            return schema_error("signature entry is missing a required key", key);
        }
    }

    let Some(signer) = object.get("signer").and_then(Value::as_str) else {
        return schema_error("signature signer must be a string", "signatures.signer");
    };
    if !regex_match(r"^(sigstore-oidc:[^#]+|did-web:[^#]+)$", signer) {
        return schema_error("signature signer has invalid shape", "signatures.signer");
    }
    for key in ["key-id", "signature"] {
        if object
            .get(key)
            .and_then(Value::as_str)
            .is_none_or(|value| value.is_empty())
        {
            return schema_error("signature string field must be non-empty", key);
        }
    }
    let Some(payload_digest) = object.get("payload-digest").and_then(Value::as_str) else {
        return schema_error("payload-digest must be a string", "payload-digest");
    };
    if !regex_match(
        r"^(sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|sha512:[0-9a-f]{128})$",
        payload_digest,
    ) {
        return schema_error("payload-digest has invalid shape", "payload-digest");
    }
    let Some(algorithm) = object.get("algorithm").and_then(Value::as_str) else {
        return schema_error(
            "signature algorithm must be a string",
            "signatures.algorithm",
        );
    };
    if !["ed25519", "ecdsa-p256", "rsa-pss-sha256"].contains(&algorithm) {
        return schema_error(
            "signature algorithm is not supported",
            "signatures.algorithm",
        );
    }
    if signer.starts_with("sigstore-oidc:")
        && (!object.contains_key("rekor-log-id") || !object.contains_key("rekor-log-index"))
    {
        return schema_error(
            "Sigstore signature requires Rekor coordinates",
            "signatures",
        );
    }
    if signer.starts_with("did-web:")
        && (object.contains_key("rekor-log-id") || object.contains_key("rekor-log-index"))
    {
        return schema_error(
            "did:web signature must not include Rekor coordinates",
            "signatures",
        );
    }
    if let Some(value) = object.get("rekor-log-id") {
        if !value.is_string() {
            return schema_error("rekor-log-id must be a string", "rekor-log-id");
        }
    }
    if let Some(value) = object.get("rekor-log-index") {
        if value.as_u64().is_none() {
            return schema_error(
                "rekor-log-index must be a non-negative integer",
                "rekor-log-index",
            );
        }
    }
    if let Some(payload_type) = object.get("payload-type") {
        let Some(payload_type) = payload_type.as_str() else {
            return schema_error("payload-type must be a string", "payload-type");
        };
        if payload_type.contains("+jcs+json")
            || !regex_match(
                r"^application/vnd\.[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+\+json$",
                payload_type,
            )
        {
            return schema_error("payload-type has invalid shape", "payload-type");
        }
    }
    Ok(())
}

const SIGNATURE_KEYS: &[&str] = &[
    "signer",
    "key-id",
    "payload-digest",
    "algorithm",
    "signature",
    "rekor-log-id",
    "rekor-log-index",
    "payload-type",
];

fn validate_depends_on(value: Option<&Value>) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(items) = value.as_array() else {
        return schema_error("depends-on must be an array", "depends-on");
    };
    for item in items {
        let Some(object) = item.as_object() else {
            return schema_error("depends-on item must be an object", "depends-on");
        };
        for key in object.keys() {
            if !["name", "version-range", "digest"].contains(&key.as_str()) {
                return schema_error("depends-on item has an unknown key", "depends-on");
            }
        }
        let Some(name) = object.get("name").and_then(Value::as_str) else {
            return schema_error("depends-on.name is required", "depends-on.name");
        };
        if !regex_match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name) {
            return schema_error("depends-on.name has invalid shape", "depends-on.name");
        }
        let Some(version_range) = object.get("version-range").and_then(Value::as_str) else {
            return schema_error(
                "depends-on.version-range is required",
                "depends-on.version-range",
            );
        };
        if !regex_match(r"^\^?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$", version_range) {
            return schema_error(
                "depends-on.version-range has invalid shape",
                "depends-on.version-range",
            );
        }
        if let Some(digest) = object.get("digest").and_then(Value::as_str) {
            if !regex_match(
                r"^(sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|sha512:[0-9a-f]{128})$",
                digest,
            ) {
                return schema_error("depends-on.digest has invalid shape", "depends-on.digest");
            }
        }
    }
    Ok(())
}

fn validate_string_array(value: Option<&Value>, key: &str) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(items) = value.as_array() else {
        return schema_error("field must be an array", key);
    };
    if items.iter().any(|item| !item.is_string()) {
        return schema_error("array items must be strings", key);
    }
    Ok(())
}

fn validate_object_array(value: Option<&Value>, key: &str) -> Result<()> {
    let Some(value) = value else {
        return Ok(());
    };
    let Some(items) = value.as_array() else {
        return schema_error("field must be an array", key);
    };
    if items.iter().any(|item| !item.is_object()) {
        return schema_error("array items must be objects", key);
    }
    Ok(())
}

fn cross_field_check(frontmatter: &Value) -> Result<()> {
    let Some(signatures) = frontmatter.get("signatures").and_then(Value::as_array) else {
        return Ok(());
    };
    if signatures.is_empty() {
        return Ok(());
    }
    let Some(integrity) = frontmatter.get("integrity").and_then(Value::as_object) else {
        return Err(MdaConfigError::new(
            ErrorCategory::SignaturesWithoutIntegrity,
            "signatures[] present without integrity",
        ));
    };
    let digest = integrity.get("digest").and_then(Value::as_str);
    for raw_sig in signatures {
        let sig = raw_sig.as_object();
        let payload_digest = sig
            .and_then(|s| s.get("payload-digest"))
            .and_then(Value::as_str);
        if payload_digest != digest {
            return Err(MdaConfigError::with_details(
                ErrorCategory::SignatureDigestMismatch,
                "signatures[i].payload-digest does not equal integrity.digest",
                serde_json::json!({
                    "signer": sig.and_then(|s| s.get("signer")).cloned(),
                    "expected": digest,
                    "actual": payload_digest,
                }),
            ));
        }
    }
    Ok(())
}

fn signature_entries(frontmatter: &Value) -> Result<Vec<crate::signature::SignatureEntry>> {
    let Some(signatures) = frontmatter.get("signatures") else {
        return Ok(Vec::new());
    };
    serde_json::from_value(signatures.clone()).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::SchemaViolation,
            "signatures field could not be decoded",
            serde_json::json!({ "cause": cause.to_string() }),
        )
    })
}
