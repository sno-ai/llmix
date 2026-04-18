use llmix_rs::{
    ConfigNotFoundError, ConfigRegistryManager, ConfigRegistryPublisher, InvalidConfigError,
    LlmixError,
};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

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

fn write_authoring_preset(
    root: &Path,
    module_name: &str,
    preset_name: &str,
    options: Option<(&str, &str, u32, &str)>,
) {
    let (model, reasoning_effort, max_output_tokens, provider) =
        options.unwrap_or(("gpt-4.1-mini", "medium", 256, "openai"));
    let file_path = root
        .join("authoring")
        .join(module_name)
        .join(format!("{preset_name}.yaml"));
    write_file(
        &file_path,
        &format!(
            "provider: {provider}\nmodel: {model}\ncommon:\n  temperature: 0.2\n  maxOutputTokens: {max_output_tokens}\nproviderOptions:\n  openai:\n    reasoningEffort: {reasoning_effort}\n"
        ),
    );
}

#[test]
fn publish_creates_active_revision_and_manager_reads_canonical_resolved_json() {
    let temp_root = unique_temp_dir("llmix-config-registry-publish");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", Some(("gpt-5-mini", "high", 1024, "openai")));

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let published = publisher.publish().expect("publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");
    let config = manager
        .get_preset("search", "summary")
        .expect("preset should load");
    let resolved_path = root
        .join("snapshots")
        .join(&published.revision)
        .join("resolved/search/summary.json");
    let resolved: Value =
        serde_json::from_str(&fs::read_to_string(&resolved_path).expect("resolved json should exist"))
            .expect("resolved json should parse");

    assert!(published.activated);
    assert_eq!(publisher.root(), root.as_path());
    assert_eq!(manager.root(), root.as_path());
    assert_eq!(manager.active_revision(), published.revision);
    assert_eq!(manager.current_path(), root.join("current.json").as_path());
    assert_eq!(manager.snapshots_dir(), root.join("snapshots").as_path());
    assert_eq!(manager.available_presets(), vec!["search/summary".to_string()]);
    assert!(manager.last_successful_reload_at().is_some());
    assert!(manager.last_reload_failure_at().is_none());
    assert_eq!(config["provider"], json!("openai"));
    assert_eq!(config["model"], json!("gpt-5-mini"));
    assert_eq!(config["common"]["max_output_tokens"], json!(1024));
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("high")
    );
    assert_eq!(resolved["common"]["max_output_tokens"], json!(1024));
    assert_eq!(
        resolved["provider_options"]["openai"]["reasoning_effort"],
        json!("high")
    );
}

#[test]
fn manager_reloads_after_current_revision_changes() {
    let temp_root = unique_temp_dir("llmix-config-registry-reload");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", Some(("gpt-4.1-mini", "medium", 256, "openai")));

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let first = publisher.publish().expect("first publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");

    write_authoring_preset(&root, "search", "summary", Some(("gpt-5-mini", "high", 2048, "openai")));
    let second = publisher.publish().expect("second publish should succeed");
    let config = manager
        .get_preset("search", "summary")
        .expect("preset should reload");

    assert_ne!(first.revision, second.revision);
    assert_eq!(manager.active_revision(), second.revision);
    assert_eq!(config["model"], json!("gpt-5-mini"));
    assert_eq!(config["common"]["max_output_tokens"], json!(2048));
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("high")
    );
}

#[test]
fn manager_rolls_back_when_current_revision_points_to_older_snapshot() {
    let temp_root = unique_temp_dir("llmix-config-registry-rollback");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let first = publisher.publish().expect("first publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");

    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-5-mini", "high", 2048, "openai")),
    );
    let second = publisher.publish().expect("second publish should succeed");
    assert_ne!(first.revision, second.revision);
    assert_eq!(
        manager
            .get_preset("search", "summary")
            .expect("second revision should load")["model"],
        json!("gpt-5-mini")
    );

    write_file(
        &root.join("current.json"),
        &format!("{{\"revision\":\"{}\"}}\n", first.revision),
    );
    let config = manager
        .get_preset("search", "summary")
        .expect("manager should roll back to the earlier revision");

    assert_eq!(manager.active_revision(), first.revision);
    assert_eq!(config["model"], json!("gpt-4.1-mini"));
    assert_eq!(config["common"]["max_output_tokens"], json!(256));
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("medium")
    );
}

#[test]
fn manager_ignores_authoring_edits_until_a_new_revision_is_published() {
    let temp_root = unique_temp_dir("llmix-config-registry-authoring-edits");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let published = publisher.publish().expect("publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");

    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-5-mini", "high", 2048, "openai")),
    );
    let config = manager
        .get_preset("search", "summary")
        .expect("active revision should stay unchanged");

    assert_eq!(manager.active_revision(), published.revision);
    assert_eq!(config["model"], json!("gpt-4.1-mini"));
    assert_eq!(config["common"]["max_output_tokens"], json!(256));
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("medium")
    );
}

#[test]
fn manager_fails_fast_without_an_active_revision() {
    let temp_root = unique_temp_dir("llmix-config-registry-missing-current");
    let root = temp_root.join("config/llm");
    fs::create_dir_all(&root).expect("root should exist");

    let error = ConfigRegistryManager::open(&root).expect_err("missing current should fail");
    assert!(matches!(error, LlmixError::ConfigNotFound(_)));
}

#[test]
fn manager_fails_fast_with_malformed_current_pointer() {
    let temp_root = unique_temp_dir("llmix-config-registry-malformed-current");
    let root = temp_root.join("config/llm");
    fs::create_dir_all(&root).expect("root should exist");
    write_file(&root.join("current.json"), "{\"revision\":42}\n");

    let error =
        ConfigRegistryManager::open(&root).expect_err("malformed current pointer should fail");
    assert!(matches!(error, LlmixError::InvalidConfig(_)));
}

#[test]
fn manager_keeps_last_known_good_config_when_pointer_changes_to_missing_revision() {
    let temp_root = unique_temp_dir("llmix-config-registry-missing-revision");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", Some(("gpt-4.1-mini", "medium", 256, "openai")));

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");

    write_file(&root.join("current.json"), "{\"revision\":\"missing-revision\"}\n");
    let config = manager
        .get_preset("search", "summary")
        .expect("last known good config should still be served");

    assert_eq!(manager.active_revision(), published.revision);
    assert!(manager.last_successful_reload_at().is_some());
    assert_eq!(config["model"], json!("gpt-4.1-mini"));
    assert!(matches!(
        manager.last_reload_error(),
        Some(LlmixError::ConfigNotFound(ConfigNotFoundError { .. }))
    ));
    assert!(manager.last_reload_failure_at().is_some());
}

#[test]
fn manager_rejects_a_tampered_resolved_snapshot_on_startup() {
    let temp_root = unique_temp_dir("llmix-config-registry-tampered");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", Some(("gpt-4.1-mini", "medium", 256, "openai")));

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let resolved_path = root
        .join("snapshots")
        .join(&published.revision)
        .join("resolved/search/summary.json");
    let mut resolved: Value =
        serde_json::from_str(&fs::read_to_string(&resolved_path).expect("resolved json should exist"))
            .expect("resolved json should parse");
    resolved["model"] = json!("tampered-model");
    fs::write(&resolved_path, serde_json::to_string(&resolved).expect("json should serialize"))
        .expect("resolved json should be overwritten");

    let error = ConfigRegistryManager::open(&root).expect_err("tampered snapshot should fail");
    assert!(matches!(error, LlmixError::InvalidConfig(InvalidConfigError { .. })));
}

#[test]
fn publish_failure_leaves_active_revision_unchanged() {
    let temp_root = unique_temp_dir("llmix-config-registry-publish-failure");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let first = publisher.publish().expect("first publish should succeed");
    write_file(
        &root.join("authoring/search/summary.yaml"),
        "provider: openai\nmodel: [broken\n",
    );

    let error = publisher
        .publish()
        .expect_err("publishing invalid authoring YAML should fail");
    assert!(matches!(error, LlmixError::InvalidConfig(_)));

    let pointer: Value = serde_json::from_str(
        &fs::read_to_string(root.join("current.json")).expect("current pointer should exist"),
    )
    .expect("current pointer should parse");
    assert_eq!(pointer["revision"], json!(first.revision));

    let staging_dir = root.join("snapshots/.staging");
    let has_staging_entries = staging_dir
        .read_dir()
        .map(|mut entries| entries.next().is_some())
        .unwrap_or(false);
    assert!(!has_staging_entries, "staging dir should be empty after failure");
}
