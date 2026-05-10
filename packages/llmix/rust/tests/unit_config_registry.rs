use llmix_rs::{
    ConfigNotFoundError, ConfigRegistryManager, ConfigRegistryOpenOptions,
    ConfigRegistryPublishOptions, ConfigRegistryPublisher, DidWebVerificationInput, DidWebVerifier,
    InvalidConfigError, LlmixError, LlmixResult, MdaConfigLoadOptions, MdaConfigResult,
    RegistryRootSignature, RegistryRootSigner, RegistryRootSigningInput,
    RegistryRootSigningOptions, RegistryRootVerificationOptions, TrustPolicy, TrustedSigner,
};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
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

fn mda_source(frontmatter: &str) -> String {
    format!("---\n{}\n---\n# preset\n", frontmatter.trim())
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
        .join(format!("{preset_name}.mda"));
    write_file(
        &file_path,
        &mda_source(&format!(
            r#"
name: {preset_name}
description: {module_name}/{preset_name} preset.
metadata:
  snoai-llmix:
    common:
      provider: {provider}
      model: {model}
      temperature: 0.2
      maxOutputTokens: {max_output_tokens}
    providerOptions:
      openai:
        reasoningEffort: {reasoning_effort}
"#
        )),
    );
}

struct TestRegistryRootSigner;

impl RegistryRootSigner for TestRegistryRootSigner {
    fn sign_registry_root(
        &self,
        input: &RegistryRootSigningInput,
    ) -> LlmixResult<Vec<RegistryRootSignature>> {
        Ok(vec![RegistryRootSignature {
            signer: "did-web:tools.example.com".to_string(),
            key_id: "did:web:tools.example.com#key-1".to_string(),
            payload_digest: input.integrity.digest.clone(),
            algorithm: "ed25519".to_string(),
            signature: "TEST_SIGNATURE".to_string(),
            rekor_log_id: None,
            rekor_log_index: None,
            payload_type: Some(input.payload_type.clone()),
        }])
    }
}

struct TestDidWebVerifier;

impl DidWebVerifier for TestDidWebVerifier {
    fn verify(&self, input: DidWebVerificationInput<'_>) -> MdaConfigResult<bool> {
        assert_eq!(input.domain, "tools.example.com");
        assert_eq!(input.algorithm, "ed25519");
        assert_eq!(
            input.payload_type,
            "application/vnd.snoai.llmix.registry-root+json"
        );
        assert!(!input.payload_bytes.is_empty());
        assert!(!input.pae_bytes.is_empty());
        Ok(true)
    }
}

fn signed_registry_publish_options<'a>(
    revision: &'a str,
    signer: &'a dyn RegistryRootSigner,
) -> ConfigRegistryPublishOptions<'a> {
    ConfigRegistryPublishOptions {
        revision: Some(revision),
        activate: true,
        mda_options: MdaConfigLoadOptions::default(),
        registry_root: Some(RegistryRootSigningOptions {
            signer,
            min_signatures: Some(1),
        }),
    }
}

fn signed_registry_open_options() -> ConfigRegistryOpenOptions {
    ConfigRegistryOpenOptions {
        signed_root: Some(RegistryRootVerificationOptions {
            trust_policy: TrustPolicy {
                version: 1,
                trusted_signers: vec![TrustedSigner::DidWeb {
                    domain: "tools.example.com".to_string(),
                }],
                min_signatures: Some(1),
                rekor: None,
            },
            rekor_client: None,
            sigstore_verifier: None,
            did_web_verifier: Some(Arc::new(TestDidWebVerifier)),
            expected_revision: None,
            expected_root_digest: None,
            minimum_revision: None,
            minimum_published_at: None,
            high_watermark: None,
        }),
    }
}

#[test]
fn publish_creates_active_revision_and_manager_reads_canonical_resolved_json() {
    let temp_root = unique_temp_dir("llmix-config-registry-publish");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-5-mini", "high", 1024, "openai")),
    );

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
    let resolved: Value = serde_json::from_str(
        &fs::read_to_string(&resolved_path).expect("resolved json should exist"),
    )
    .expect("resolved json should parse");

    assert!(published.activated);
    assert_eq!(publisher.root(), root.as_path());
    assert_eq!(manager.root(), root.as_path());
    assert_eq!(manager.active_revision(), published.revision);
    assert_eq!(manager.current_path(), root.join("current.json").as_path());
    assert_eq!(manager.snapshots_dir(), root.join("snapshots").as_path());
    assert_eq!(
        manager.available_presets(),
        vec!["search/summary".to_string()]
    );
    assert!(root
        .join("snapshots")
        .join(&published.revision)
        .join("authoring/search/summary.mda")
        .is_file());
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
fn signed_registry_root_publish_and_open_use_public_options() {
    let temp_root = unique_temp_dir("llmix-config-registry-signed-root");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-5-mini", "high", 1024, "openai")),
    );

    let signer = TestRegistryRootSigner;
    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let published = publisher
        .publish_with_registry_options(&signed_registry_publish_options("signed-1", &signer))
        .expect("signed publish should succeed");
    let mut manager =
        ConfigRegistryManager::open_with_options(&root, signed_registry_open_options())
            .expect("signed registry should open");
    let config = manager
        .get_preset("search", "summary")
        .expect("signed preset should load");

    assert_eq!(published.revision, "signed-1");
    assert_eq!(
        published.registry_root_path.as_deref(),
        Some(root.join("snapshots/signed-1/registry-root.json").as_path())
    );
    assert!(published.registry_root_sha256.is_some());
    assert_eq!(manager.active_revision(), "signed-1");
    assert_eq!(config["model"], json!("gpt-5-mini"));
}

#[test]
fn signed_registry_root_rejects_tampered_registry_root_payload() {
    let temp_root = unique_temp_dir("llmix-config-registry-signed-root-tamper");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", None);

    let signer = TestRegistryRootSigner;
    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let published = publisher
        .publish_with_registry_options(&signed_registry_publish_options("signed-1", &signer))
        .expect("signed publish should succeed");
    let root_path = published
        .registry_root_path
        .expect("signed publish should write registry-root.json");
    let mut envelope: Value = serde_json::from_str(
        &fs::read_to_string(&root_path).expect("registry root should be readable"),
    )
    .expect("registry root should parse");
    envelope["payload"]["revision"] = json!("signed-1-tampered");
    fs::write(
        &root_path,
        serde_json::to_string(&envelope).expect("tampered root should serialize"),
    )
    .expect("registry root should be overwritten");

    let error = ConfigRegistryManager::open_with_options(&root, signed_registry_open_options())
        .expect_err("tampered signed registry root should fail");

    assert!(
        matches!(
            error,
            LlmixError::InvalidConfig(_) | LlmixError::Security(_)
        ),
        "unexpected error: {error}"
    );
    assert!(
        error.to_string().contains("payload digest mismatch")
            || error.to_string().contains("signature verification failed"),
        "unexpected error: {error}"
    );
}

#[test]
fn publish_uses_unique_staging_dir_for_explicit_revision() {
    let temp_root = unique_temp_dir("llmix-config-registry-unique-stage");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );
    let stale_stage_file = root.join("snapshots/.staging/manual.tmp/sentinel");
    write_file(&stale_stage_file, "keep");

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let published = publisher
        .publish_with_options(Some("manual"), false)
        .expect("publish should succeed");

    assert_eq!(published.revision, "manual");
    assert!(
        stale_stage_file.is_file(),
        "publish should not delete another attempt's staging dir"
    );
}

#[test]
fn manager_reloads_after_current_revision_changes() {
    let temp_root = unique_temp_dir("llmix-config-registry-reload");
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
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let mut manager = ConfigRegistryManager::open(&root).expect("manager should open");

    write_file(
        &root.join("current.json"),
        "{\"revision\":\"missing-revision\"}\n",
    );
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
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let resolved_path = root
        .join("snapshots")
        .join(&published.revision)
        .join("resolved/search/summary.json");
    let mut resolved: Value = serde_json::from_str(
        &fs::read_to_string(&resolved_path).expect("resolved json should exist"),
    )
    .expect("resolved json should parse");
    resolved["model"] = json!("tampered-model");
    fs::write(
        &resolved_path,
        serde_json::to_string(&resolved).expect("json should serialize"),
    )
    .expect("resolved json should be overwritten");

    let error = ConfigRegistryManager::open(&root).expect_err("tampered snapshot should fail");
    assert!(matches!(
        error,
        LlmixError::InvalidConfig(InvalidConfigError { .. })
    ));
}

#[test]
fn manager_rejects_a_tampered_authoring_snapshot_on_startup() {
    let temp_root = unique_temp_dir("llmix-config-registry-authoring-tampered");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", None);

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let authoring_path = root
        .join("snapshots")
        .join(&published.revision)
        .join("authoring/search/summary.mda");
    fs::write(&authoring_path, "---\nname: tampered\n---\n")
        .expect("authoring artifact should be overwritten");

    let error = ConfigRegistryManager::open(&root).expect_err("tampered snapshot should fail");
    assert!(matches!(
        error,
        LlmixError::InvalidConfig(InvalidConfigError { .. })
    ));
}

#[test]
fn manager_rejects_manifest_with_empty_artifact_paths_before_opening_them() {
    let temp_root = unique_temp_dir("llmix-config-registry-empty-manifest-path");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", None);

    let published = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect("publish should succeed");
    let manifest_path = root
        .join("snapshots")
        .join(&published.revision)
        .join("manifest.json");
    let mut manifest: Value =
        serde_json::from_str(&fs::read_to_string(&manifest_path).expect("manifest should exist"))
            .expect("manifest should parse");
    manifest["presets"]["search/summary"]["resolved_path"] = json!("");
    fs::write(
        &manifest_path,
        serde_json::to_string(&manifest).expect("manifest should serialize"),
    )
    .expect("manifest should be overwritten");

    let error = ConfigRegistryManager::open(&root).expect_err("bad manifest should fail");
    assert!(matches!(
        error,
        LlmixError::InvalidConfig(InvalidConfigError { .. })
    ));
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
        &root.join("authoring/search/summary.mda"),
        "---\nname: summary\nmetadata:\n  snoai-llmix:\n    common:\n      provider: openai\n      model: [broken\n---\n",
    );

    let error = publisher
        .publish()
        .expect_err("publishing invalid authoring MDA should fail");
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
    assert!(
        !has_staging_entries,
        "staging dir should be empty after failure"
    );
}

#[test]
fn publish_with_mda_options_uses_mda_verification() {
    let temp_root = unique_temp_dir("llmix-config-registry-verification-options");
    let root = temp_root.join("config/llm");
    write_authoring_preset(&root, "search", "summary", None);

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let error = publisher
        .publish_with_mda_options(
            Some("manual"),
            true,
            &MdaConfigLoadOptions {
                verify_integrity: false,
                verify_signatures: true,
                ..Default::default()
            },
        )
        .expect_err("missing verification material should fail");

    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error.to_string().contains("MDA source validation failed"),
        "unexpected error: {error}"
    );
    assert!(
        !root.join("current.json").exists(),
        "failed publish should not activate a revision"
    );
    assert!(
        !root.join("snapshots/manual").exists(),
        "failed publish should not keep a snapshot"
    );
}

#[test]
fn activation_failure_removes_new_snapshot_so_revision_can_retry() {
    let temp_root = unique_temp_dir("llmix-config-registry-activation-failure");
    let root = temp_root.join("config/llm");
    write_authoring_preset(
        &root,
        "search",
        "summary",
        Some(("gpt-4.1-mini", "medium", 256, "openai")),
    );
    fs::create_dir_all(root.join("current.json")).expect("current path directory should exist");

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let error = publisher
        .publish_with_options(Some("manual"), true)
        .expect_err("activation should fail when current.json is a directory");

    assert!(matches!(error, LlmixError::Io(_)));
    assert!(
        !root.join("snapshots/manual").exists(),
        "failed activation should remove the newly renamed snapshot"
    );

    fs::remove_dir_all(root.join("current.json"))
        .expect("current path directory should be removed");
    let published = publisher
        .publish_with_options(Some("manual"), true)
        .expect("same revision should publish after cleanup");
    assert_eq!(published.revision, "manual");
    assert!(published.activated);
}

#[test]
fn publisher_rejects_legacy_yaml_authoring_files() {
    let temp_root = unique_temp_dir("llmix-config-registry-legacy-yaml");
    let root = temp_root.join("config/llm");
    write_file(
        &root.join("authoring/search/summary.yaml"),
        "provider: openai\nmodel: gpt-4.1-mini\n",
    );

    let error = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect_err("legacy YAML authoring should fail");

    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error
            .to_string()
            .contains("YAML presets are no longer supported"),
        "unexpected error: {error}"
    );
}

#[cfg(unix)]
#[test]
fn publisher_rejects_symlinked_authoring_modules() {
    let temp_root = unique_temp_dir("llmix-config-registry-module-symlink");
    let root = temp_root.join("config/llm");
    let outside_module = temp_root.join("outside-module");
    write_file(
        &outside_module.join("summary.mda"),
        &mda_source(
            r#"
name: summary
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
"#,
        ),
    );
    fs::create_dir_all(root.join("authoring")).expect("authoring dir should exist");
    std::os::unix::fs::symlink(&outside_module, root.join("authoring/search"))
        .expect("module symlink should be created");

    let error = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect_err("symlinked authoring module should fail");

    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error.to_string().contains("must not be symlinks"),
        "unexpected error: {error}"
    );
}

#[cfg(unix)]
#[test]
fn publisher_rejects_symlinked_authoring_presets() {
    let temp_root = unique_temp_dir("llmix-config-registry-preset-symlink");
    let root = temp_root.join("config/llm");
    let outside_preset = temp_root.join("outside-summary.mda");
    write_file(
        &outside_preset,
        &mda_source(
            r#"
name: summary
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4.1-mini
"#,
        ),
    );
    fs::create_dir_all(root.join("authoring/search")).expect("module dir should exist");
    std::os::unix::fs::symlink(&outside_preset, root.join("authoring/search/summary.mda"))
        .expect("preset symlink should be created");

    let error = ConfigRegistryPublisher::new(&root)
        .expect("publisher should open")
        .publish()
        .expect_err("symlinked authoring preset should fail");

    assert!(matches!(error, LlmixError::InvalidConfig(_)));
    assert!(
        error.to_string().contains("must not be symlinks"),
        "unexpected error: {error}"
    );
}
