use llmix_rs::{load_config, load_config_preset};
use std::path::PathBuf;

fn fixture_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("tests")
        .join("fixtures")
}

#[test]
fn load_config_projects_mda_fixture_shape() {
    let preset_path = fixture_dir().join("sample_preset.mda");
    let config = load_config(&preset_path).expect("fixture should load");

    assert_eq!(config["provider"], "openai");
    assert_eq!(config["model"], "gpt-5-mini");
    assert_eq!(config["common"]["temperature"], 0.7);
    assert_eq!(config["common"]["max_output_tokens"], 4096);
    assert!(config["common"].get("provider").is_none());
    assert!(config["common"].get("model").is_none());
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        "medium"
    );
    assert_eq!(config["caching"]["strategy"], "memory");
}

#[test]
fn load_config_preset_uses_mda_authoring_path() {
    let config = load_config_preset("sample_preset", fixture_dir()).expect("preset should load");

    assert_eq!(config["provider"], "openai");
    assert_eq!(config["model"], "gpt-5-mini");
    assert_eq!(config["common"]["max_output_tokens"], 4096);
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        "medium"
    );
}
