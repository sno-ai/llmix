use llmix_rs::{
    load_config, load_config_preset, resolve_config_dir, validate_module, validate_preset,
    validate_version, ConfigDirSource, LlmixError, LlmixPathConfig,
};
use serde_json::json;
use std::env;
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

fn test_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn unique_temp_dir(prefix: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time should move forward")
        .as_nanos();
    let path = env::temp_dir().join(format!("{prefix}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(&path).expect("temp dir should be created");
    path
}

fn write_file(path: &Path, content: &str) {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).expect("parent dir should exist");
    }
    fs::write(path, content).expect("file should be written");
}

fn mda_source(frontmatter: &str) -> String {
    format!("---\n{}\n---\n# preset\n", frontmatter.trim())
}

#[test]
fn load_config_rejects_missing_required_fields() {
    let temp_dir = unique_temp_dir("llmix-config-invalid");
    let file_path = temp_dir.join("missing-provider.mda");
    write_file(
        &file_path,
        &mda_source(
            r#"
name: missing-provider
description: Invalid preset.
metadata:
  snoai-llmix:
    common:
      model: gpt-4.1-mini
      temperature: 0.2
"#,
        ),
    );

    let error = load_config(&file_path).expect_err("missing provider should fail");
    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error
            .to_string()
            .contains("Missing required field 'provider'"),
        "unexpected error: {error}"
    );
}

#[test]
fn load_config_reports_missing_file() {
    let temp_dir = unique_temp_dir("llmix-config-missing");
    let file_path = temp_dir.join("does-not-exist.mda");

    let error = load_config(&file_path).expect_err("missing file should fail");
    assert!(matches!(error, LlmixError::ConfigNotFound(_)));
}

#[test]
fn load_config_projects_mda_frontmatter_and_normalizes_keys() {
    let temp_dir = unique_temp_dir("llmix-config-camel-case");
    let file_path = temp_dir.join("public-compat.mda");
    write_file(
        &file_path,
        &mda_source(
            r#"
name: public-compat
description: Public compatibility preset.
tags:
  - rust
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
      maxOutputTokens: 123
      keepThinkingOutput: true
    providerOptions:
      openai:
        reasoningEffort: high
    caching:
      strategy: redis-or-memory
      maxItems: 99
"#,
        ),
    );

    let config = load_config(&file_path).expect("camelCase config should normalize");

    assert_eq!(config["common"]["max_output_tokens"], json!(123));
    assert_eq!(config["common"]["keep_thinking_output"], json!(true));
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("high")
    );
    assert_eq!(config["caching"]["strategy"], json!("redis-or-memory"));
    assert_eq!(config["caching"]["max_items"], json!(99));
    assert_eq!(config["description"], json!("Public compatibility preset."));
    assert_eq!(config["tags"], json!(["rust"]));
}

#[test]
fn load_config_rejects_legacy_yaml_paths() {
    let temp_dir = unique_temp_dir("llmix-config-legacy-yaml");
    let file_path = temp_dir.join("legacy.yaml");
    write_file(&file_path, "provider: openai\nmodel: gpt-4.1-mini\n");

    let error = load_config(&file_path).expect_err("legacy YAML should fail");
    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error
            .to_string()
            .contains("YAML configs are no longer supported"),
        "unexpected error: {error}"
    );
}

#[cfg(unix)]
#[test]
fn load_config_permission_denied_maps_to_config_access_error() {
    let temp_dir = unique_temp_dir("llmix-config-denied");
    let file_path = temp_dir.join("denied.mda");
    write_file(
        &file_path,
        &mda_source(
            r#"
name: denied
description: Denied preset.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
"#,
        ),
    );

    let original_permissions = fs::metadata(&file_path)
        .expect("config metadata should exist")
        .permissions();
    let mut denied_permissions = original_permissions.clone();
    denied_permissions.set_mode(0o000);
    fs::set_permissions(&file_path, denied_permissions).expect("permissions should change");

    let error = load_config(&file_path).expect_err("permission denied should fail");

    fs::set_permissions(&file_path, original_permissions).expect("permissions should restore");

    assert!(matches!(error, LlmixError::ConfigAccess(_)));
}

#[cfg(unix)]
#[test]
fn load_config_rejects_symlink_escape() {
    use std::os::unix::fs::symlink;

    let temp_dir = unique_temp_dir("llmix-config-symlink");
    let allowed_dir = temp_dir.join("allowed");
    let outside_dir = temp_dir.join("outside");
    fs::create_dir_all(&allowed_dir).expect("allowed dir should exist");
    fs::create_dir_all(&outside_dir).expect("outside dir should exist");

    let target = outside_dir.join("target.mda");
    write_file(
        &target,
        &mda_source(
            r#"
name: target
description: Escaped preset.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
"#,
        ),
    );
    let link = allowed_dir.join("linked.mda");
    symlink(&target, &link).expect("symlink should be created");

    let error = load_config(&link).expect_err("symlink escape should fail");
    assert!(matches!(error, LlmixError::Security(_)));
}

#[test]
fn load_config_preset_rejects_path_traversal_names() {
    let base_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("tests")
        .join("fixtures");

    let error = load_config_preset("~escape", &base_dir).expect_err("dangerous name should fail");
    assert!(matches!(error, LlmixError::Security(_)));
}

#[test]
fn load_config_preset_ignores_legacy_yaml_when_mda_exists() {
    for suffix in ["yaml", "yml"] {
        let temp_dir = unique_temp_dir(&format!("llmix-config-preset-mixed-{suffix}"));
        let base_dir = temp_dir.join("search");
        write_file(
            &base_dir.join("summary.mda"),
            &mda_source(
                r#"
name: summary
description: MDA preset.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
"#,
            ),
        );
        write_file(
            &base_dir.join(format!("summary.{suffix}")),
            "provider: anthropic\nmodel: claude-sonnet-4-5\n",
        );

        let config = load_config_preset("summary", &base_dir).expect("MDA preset should load");
        assert_eq!(config["provider"], json!("openai"));
        assert_eq!(config["model"], json!("gpt-4.1-mini"));
    }
}

#[test]
fn load_config_preset_does_not_fall_back_to_yaml_only_preset() {
    for suffix in ["yaml", "yml"] {
        let temp_dir = unique_temp_dir(&format!("llmix-config-preset-{suffix}-only"));
        let base_dir = temp_dir.join("search");
        write_file(
            &base_dir.join(format!("summary.{suffix}")),
            "provider: anthropic\nmodel: claude-sonnet-4-5\n",
        );

        let error =
            load_config_preset("summary", &base_dir).expect_err("YAML-only preset should fail");
        assert!(matches!(error, LlmixError::ConfigNotFound(_)));
        assert!(
            error.to_string().contains("summary.mda"),
            "unexpected error for .{suffix}: {error}"
        );
    }
}

#[test]
fn validators_match_current_name_rules() {
    validate_module("_default").expect("special module should be valid");
    validate_module("abc_123").expect("simple module should be valid");
    validate_preset("_base_fast").expect("special preset should be valid");
    validate_preset("agent_fast").expect("simple preset should be valid");
    validate_version(1).expect("version one should be valid");
    validate_version(9999).expect("max version should be valid");

    assert!(matches!(
        validate_module("BadName").expect_err("uppercase should fail"),
        LlmixError::InvalidConfig(_)
    ));
    assert!(matches!(
        validate_preset("../escape").expect_err("traversal should fail"),
        LlmixError::Security(_)
    ));
    assert!(matches!(
        validate_version(0).expect_err("zero should fail"),
        LlmixError::InvalidConfig(_)
    ));
}

#[test]
fn resolve_config_dir_prefers_explicit_override() {
    let temp_dir = unique_temp_dir("llmix-config-explicit");
    let resolved = resolve_config_dir(Some(&LlmixPathConfig {
        config_dir: Some(temp_dir.join("custom")),
        env_var: None,
        default_path: None,
        project_root: None,
    }))
    .expect("explicit path should resolve");

    assert_eq!(resolved.source, ConfigDirSource::Explicit);
    assert_eq!(resolved.config_dir, temp_dir.join("custom"));
}

#[test]
fn resolve_config_dir_uses_project_root_for_default_path() {
    let temp_dir = unique_temp_dir("llmix-config-default");
    let resolved = resolve_config_dir(Some(&LlmixPathConfig {
        config_dir: None,
        env_var: Some("LLMIX_CONFIG_DIR_TEST_DEFAULT".to_string()),
        default_path: Some(PathBuf::from("./presets/llm")),
        project_root: Some(temp_dir.clone()),
    }))
    .expect("default path should resolve");

    assert_eq!(resolved.source, ConfigDirSource::Default);
    assert_eq!(resolved.config_dir, temp_dir.join("presets/llm"));
}

#[test]
fn resolve_config_dir_uses_env_relative_to_detected_project_root() {
    let _guard = test_lock().lock().expect("lock should be acquired");
    let original_cwd = env::current_dir().expect("cwd should resolve");
    let original_env = env::var_os("LLMIX_CONFIG_DIR_TEST_ENV");

    let temp_dir = unique_temp_dir("llmix-config-env");
    let root_dir = temp_dir.join("repo");
    let workspace_dir = root_dir.join("apps/service");
    fs::create_dir_all(&workspace_dir).expect("workspace dir should exist");
    write_file(
        &root_dir.join("package.json"),
        &json!({ "name": "repo", "workspaces": ["apps/*"] }).to_string(),
    );

    env::set_current_dir(&workspace_dir).expect("cwd should change");
    env::set_var("LLMIX_CONFIG_DIR_TEST_ENV", "config/llm");

    let resolved = resolve_config_dir(Some(&LlmixPathConfig {
        config_dir: None,
        env_var: Some("LLMIX_CONFIG_DIR_TEST_ENV".to_string()),
        default_path: None,
        project_root: None,
    }))
    .expect("env path should resolve");

    if let Some(value) = original_env {
        env::set_var("LLMIX_CONFIG_DIR_TEST_ENV", value);
    } else {
        env::remove_var("LLMIX_CONFIG_DIR_TEST_ENV");
    }
    env::set_current_dir(original_cwd).expect("cwd should restore");

    assert_eq!(resolved.source, ConfigDirSource::Env);
    assert_eq!(resolved.config_dir, root_dir.join("config/llm"));
}
