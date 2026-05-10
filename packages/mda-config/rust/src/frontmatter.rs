use serde_json::Value;

use crate::errors::{ErrorCategory, MdaConfigError, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractedFrontmatter {
    pub frontmatter_str: String,
    pub body_str: String,
}

pub fn extract_frontmatter(file_bytes: &[u8]) -> Result<ExtractedFrontmatter> {
    let bytes = if file_bytes.starts_with(&[0xef, 0xbb, 0xbf]) {
        &file_bytes[3..]
    } else {
        file_bytes
    };
    let decoded = std::str::from_utf8(bytes)
        .map_err(|cause| {
            MdaConfigError::with_details(
                ErrorCategory::InvalidEncoding,
                "file bytes are not valid UTF-8",
                serde_json::json!({ "cause": cause.to_string() }),
            )
        })?
        .replace("\r\n", "\n")
        .replace('\r', "\n");

    if !decoded.starts_with("---\n") {
        return Ok(ExtractedFrontmatter {
            frontmatter_str: String::new(),
            body_str: decoded,
        });
    }

    let after_open = 4;
    let mut cursor = after_open;
    while cursor <= decoded.len() {
        let rel_newline = decoded[cursor..].find('\n');
        let line_end = rel_newline.map_or(decoded.len(), |idx| cursor + idx);
        let line = &decoded[cursor..line_end];
        if line == "---" {
            let frontmatter_str = decoded[after_open..cursor].to_string();
            let body_start = rel_newline.map_or(decoded.len(), |_| line_end + 1);
            return Ok(ExtractedFrontmatter {
                frontmatter_str,
                body_str: decoded[body_start..].to_string(),
            });
        }
        let Some(idx) = rel_newline else {
            break;
        };
        cursor += idx + 1;
    }

    Err(MdaConfigError::new(
        ErrorCategory::UnterminatedFrontmatter,
        "opening '---' fence has no matching closing '---' line",
    ))
}

pub fn parse_frontmatter_yaml(frontmatter_str: &str) -> Result<Value> {
    if frontmatter_str.is_empty() {
        return Ok(Value::Object(Default::default()));
    }

    let yaml_value: serde_yaml_ng::Value =
        serde_yaml_ng::from_str(frontmatter_str).map_err(|cause| {
            MdaConfigError::with_details(
                ErrorCategory::FrontmatterYamlParseError,
                "YAML parse failed",
                serde_json::json!({ "cause": cause.to_string() }),
            )
        })?;
    if matches!(yaml_value, serde_yaml_ng::Value::Null) {
        return Ok(Value::Object(Default::default()));
    }

    let json_value = serde_json::to_value(yaml_value).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::FrontmatterYamlParseError,
            "YAML frontmatter could not be converted to JSON-compatible values",
            serde_json::json!({ "cause": cause.to_string() }),
        )
    })?;
    if !json_value.is_object() {
        return Err(MdaConfigError::new(
            ErrorCategory::FrontmatterYamlParseError,
            "frontmatter MUST parse to a YAML mapping (object), not a scalar or sequence",
        ));
    }
    Ok(json_value)
}
