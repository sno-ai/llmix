use super::*;

pub fn validate_module(module: &str) -> LlmixResult<()> {
    validate_name(module, NameKind::Module)
}

pub fn validate_preset(preset: &str) -> LlmixResult<()> {
    validate_name(preset, NameKind::Preset)
}

pub fn validate_version(version: u32) -> LlmixResult<()> {
    if !(MIN_VERSION..=MAX_VERSION).contains(&version) {
        return Err(InvalidConfigError {
            message: format!("Version {version} out of valid range [{MIN_VERSION}, {MAX_VERSION}]"),
        }
        .into());
    }

    Ok(())
}

pub(super) fn normalize_preset_name(name: &str) -> String {
    let file_name = Path::new(name)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(name);
    if let Some(stripped) = file_name.strip_suffix(".mda") {
        return stripped.to_string();
    }

    file_name.to_string()
}

fn validate_name(value: &str, kind: NameKind) -> LlmixResult<()> {
    if value.is_empty() {
        return Err(InvalidConfigError {
            message: format!("{} name cannot be empty", kind.label()),
        }
        .into());
    }

    if value.len() > MAX_NAME_LEN {
        return Err(InvalidConfigError {
            message: format!(
                "{} name too long: {} > {}",
                kind.label(),
                value.len(),
                MAX_NAME_LEN
            ),
        }
        .into());
    }

    if DANGEROUS_PARTS.iter().any(|part| value.contains(part)) {
        return Err(SecurityError {
            message: format!(
                "Invalid characters in {}: {value}",
                kind.label().to_lowercase()
            ),
        }
        .into());
    }

    let valid = match kind {
        NameKind::Module => value == "_default" || is_lowercase_identifier(value),
        NameKind::Preset => {
            value.starts_with("_base") && value[5..].chars().all(is_lowercase_alnum_or_underscore)
                || is_lowercase_identifier(value)
        }
    };

    if valid {
        Ok(())
    } else {
        Err(InvalidConfigError {
            message: match kind {
                NameKind::Module => format!(
                    "Invalid module format: {value}. Must be '_default' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
                ),
                NameKind::Preset => format!(
                    "Invalid preset format: {value}. Must be '_base*' or start with lowercase letter and contain only lowercase letters, numbers, and underscores"
                ),
            },
        }
        .into())
    }
}

fn is_lowercase_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(first) if first.is_ascii_lowercase())
        && chars.all(is_lowercase_alnum_or_underscore)
        && value.len() <= MAX_NAME_LEN
}

fn is_lowercase_alnum_or_underscore(value: char) -> bool {
    value.is_ascii_lowercase() || value.is_ascii_digit() || value == '_'
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NameKind {
    Module,
    Preset,
}

impl NameKind {
    fn label(self) -> &'static str {
        match self {
            NameKind::Module => "Module",
            NameKind::Preset => "Preset",
        }
    }
}
