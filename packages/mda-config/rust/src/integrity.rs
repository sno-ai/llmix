use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256, Sha384, Sha512};

use crate::errors::{ErrorCategory, MdaConfigError, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum HashAlgorithm {
    Sha256,
    Sha384,
    Sha512,
}

impl HashAlgorithm {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Sha256 => "sha256",
            Self::Sha384 => "sha384",
            Self::Sha512 => "sha512",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct IntegrityField {
    pub algorithm: HashAlgorithm,
    pub digest: String,
}

pub fn normalize_body(body_str: &str) -> String {
    if body_str.is_empty() {
        return String::new();
    }
    let mut stripped: Vec<String> = body_str
        .split('\n')
        .map(|line| line.trim_end_matches([' ', '\t']).to_string())
        .collect();
    while stripped.last().is_some_and(|line| line.is_empty()) {
        stripped.pop();
    }
    if stripped.is_empty() {
        String::new()
    } else {
        stripped.join("\n") + "\n"
    }
}

pub fn canonicalize_artifact(frontmatter: &Value, body_str: &str) -> Result<Vec<u8>> {
    let object = frontmatter.as_object().ok_or_else(|| {
        MdaConfigError::new(
            ErrorCategory::SchemaViolation,
            "frontmatter MUST be a JSON object",
        )
    })?;
    let mut stripped = object.clone();
    stripped.remove("integrity");
    stripped.remove("signatures");
    let canonical = canonical_json(&Value::Object(stripped))?;
    let head = format!("---\n{canonical}\n---\n");
    Ok([head.as_bytes(), normalize_body(body_str).as_bytes()].concat())
}

pub fn parse_digest(digest: &str) -> Result<(&str, &str)> {
    let Some((algorithm, hex)) = digest.split_once(':') else {
        return Err(MdaConfigError::with_details(
            ErrorCategory::SchemaViolation,
            "integrity.digest is not in '<algorithm>:<hex>' form",
            serde_json::json!({ "digest": digest }),
        ));
    };
    if algorithm.is_empty() {
        return Err(MdaConfigError::with_details(
            ErrorCategory::SchemaViolation,
            "integrity.digest is not in '<algorithm>:<hex>' form",
            serde_json::json!({ "digest": digest }),
        ));
    }
    Ok((algorithm, hex))
}

pub fn hash_canonical(canonical_bytes: &[u8], algorithm: HashAlgorithm) -> String {
    match algorithm {
        HashAlgorithm::Sha256 => hex_lower(Sha256::digest(canonical_bytes).as_slice()),
        HashAlgorithm::Sha384 => hex_lower(Sha384::digest(canonical_bytes).as_slice()),
        HashAlgorithm::Sha512 => hex_lower(Sha512::digest(canonical_bytes).as_slice()),
    }
}

pub fn verify_integrity(
    frontmatter: &Value,
    body_str: &str,
    integrity: &IntegrityField,
) -> Result<()> {
    let (algorithm, expected_hex) = parse_digest(&integrity.digest)?;
    if algorithm != integrity.algorithm.as_str() {
        return Err(MdaConfigError::with_details(
            ErrorCategory::SchemaViolation,
            "integrity.digest prefix does not match integrity.algorithm",
            serde_json::json!({
                "algorithm": integrity.algorithm.as_str(),
                "digestPrefix": algorithm,
            }),
        ));
    }
    let canonical = canonicalize_artifact(frontmatter, body_str)?;
    let computed = hash_canonical(&canonical, integrity.algorithm);
    if computed != expected_hex {
        return Err(MdaConfigError::with_details(
            ErrorCategory::IntegrityMismatch,
            "computed digest does not match integrity.digest",
            serde_json::json!({
                "expected": expected_hex,
                "computed": computed,
                "algorithm": integrity.algorithm.as_str(),
            }),
        ));
    }
    Ok(())
}

pub(crate) fn canonical_json(value: &Value) -> Result<String> {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {
            serde_json::to_string(value).map_err(|cause| {
                MdaConfigError::with_details(
                    ErrorCategory::SchemaViolation,
                    "JSON canonicalization failed",
                    serde_json::json!({ "cause": cause.to_string() }),
                )
            })
        }
        Value::Array(items) => {
            let mut out = String::from("[");
            for (idx, item) in items.iter().enumerate() {
                if idx > 0 {
                    out.push(',');
                }
                out.push_str(&canonical_json(item)?);
            }
            out.push(']');
            Ok(out)
        }
        Value::Object(map) => canonical_object(map),
    }
}

fn canonical_object(map: &Map<String, Value>) -> Result<String> {
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    let mut out = String::from("{");
    for (idx, key) in keys.iter().enumerate() {
        if idx > 0 {
            out.push(',');
        }
        out.push_str(&serde_json::to_string(key).map_err(|cause| {
            MdaConfigError::with_details(
                ErrorCategory::SchemaViolation,
                "JSON canonicalization failed",
                serde_json::json!({ "cause": cause.to_string() }),
            )
        })?);
        out.push(':');
        out.push_str(&canonical_json(&map[*key])?);
    }
    out.push('}');
    Ok(out)
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}
