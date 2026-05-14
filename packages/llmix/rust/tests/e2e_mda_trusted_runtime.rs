use llmix_rs::{
    load_config_with_options, ConfigRegistryManager, ConfigRegistryOpenOptions,
    ConfigRegistryPublishOptions, ConfigRegistryPublisher, DidWebVerificationInput, DidWebVerifier,
    LlmixResult, MdaConfigLoadOptions, MdaConfigResult, RegistryRootSignature, RegistryRootSigner,
    RegistryRootSigningInput, RegistryRootSigningOptions, RegistryRootVerificationOptions,
    TrustPolicy, TrustedSigner,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use snoai_mda_config::{
    canonicalize_artifact, construct_dsse_pae, hash_canonical, HashAlgorithm, IntegrityField,
    DEFAULT_PAYLOAD_TYPE,
};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

const SIGNER_DOMAIN: &str = "config.example.com";
const SIGNER_KEY_ID: &str = "did:web:config.example.com#key-1";

struct DeterministicDidWebVerifier;

impl DidWebVerifier for DeterministicDidWebVerifier {
    fn verify(&self, input: DidWebVerificationInput<'_>) -> MdaConfigResult<bool> {
        if input.domain != SIGNER_DOMAIN
            || input.key_id != SIGNER_KEY_ID
            || input.algorithm != "ed25519"
        {
            return Ok(false);
        }
        Ok(input.signature == sha256_hex(input.pae_bytes))
    }
}

struct DeterministicRegistryRootSigner;

impl RegistryRootSigner for DeterministicRegistryRootSigner {
    fn sign_registry_root(
        &self,
        input: &RegistryRootSigningInput,
    ) -> LlmixResult<Vec<RegistryRootSignature>> {
        Ok(vec![RegistryRootSignature {
            signer: format!("did-web:{SIGNER_DOMAIN}"),
            key_id: SIGNER_KEY_ID.to_string(),
            payload_digest: input.integrity.digest.clone(),
            algorithm: "ed25519".to_string(),
            signature: sign_payload(&input.payload_type, input.canonical_payload.as_bytes()),
            rekor_log_id: None,
            rekor_log_index: None,
            payload_type: Some(input.payload_type.clone()),
        }])
    }
}

#[test]
fn e2e_signed_mda_loads_and_tampering_is_rejected() {
    let temp_root = unique_temp_dir("llmix-e2e-mda-trusted-runtime");
    let root = temp_root.join("config/llm");
    let mda_path = root.join("source/search/summary.mda");
    let verifier = DeterministicDidWebVerifier;
    let signer = DeterministicRegistryRootSigner;

    write_file(
        &mda_path,
        &signed_mda_source("gpt-5-mini", 1024, "# signed llmix preset\n"),
    );

    let mda_options = mda_load_options(&verifier);
    let direct_config =
        load_config_with_options(&mda_path, &mda_options).expect("signed MDA should load");
    assert_expected_config(&direct_config, "gpt-5-mini", 1024);

    let publisher = ConfigRegistryPublisher::new(&root).expect("publisher should open");
    let original = publisher
        .publish_with_registry_options(&publish_options(
            "signed-original",
            &signer,
            mda_load_options(&verifier),
        ))
        .expect("signed registry should publish");
    let original_root_digest = original
        .registry_root_sha256
        .clone()
        .expect("signed publish should return registry root digest");

    let mut manager = ConfigRegistryManager::open_with_options(
        &root,
        registry_open_options(Some(original_root_digest.clone())),
    )
    .expect("signed registry should open with the original root digest anchor");
    let registry_config = manager
        .get_preset("search", "summary")
        .expect("signed registry preset should load");
    assert_eq!(registry_config, direct_config);
    println!(
        "E2E_CASE_1_LOADED_CONFIG={}",
        serde_json::to_string_pretty(&registry_config).expect("config should render as JSON")
    );

    let tampered_source = signed_mda_source("gpt-5-mini", 1024, "# signed llmix preset\n")
        .replace("gpt-5-mini", "attacker-model");
    write_file(&mda_path, &tampered_source);
    let partial_error = load_config_with_options(&mda_path, &mda_options)
        .expect_err("partially tampered signed MDA should be rejected");
    println!("E2E_CASE_2_REJECTED={partial_error}");
    assert!(
        partial_error
            .to_string()
            .contains("computed digest does not match integrity.digest"),
        "unexpected partial tamper error: {partial_error}"
    );

    write_file(
        &mda_path,
        &signed_mda_source("attacker-model", 4096, "# forged replacement preset\n"),
    );
    publisher
        .publish_with_registry_options(&publish_options(
            "signed-forged-replacement",
            &signer,
            mda_load_options(&verifier),
        ))
        .expect("internally coherent replacement registry should publish");

    let replacement_error = ConfigRegistryManager::open_with_options(
        &root,
        registry_open_options(Some(original_root_digest)),
    )
    .expect_err("whole-package replacement should be rejected by the root digest anchor");
    println!("E2E_CASE_3_REJECTED={replacement_error}");
    assert!(
        replacement_error
            .to_string()
            .contains("Registry root digest does not match expected_root_digest"),
        "unexpected replacement error: {replacement_error}"
    );
}

fn assert_expected_config(config: &Value, model: &str, max_output_tokens: u32) {
    assert_eq!(config["provider"], json!("openai"));
    assert_eq!(config["model"], json!(model));
    assert_eq!(
        config["description"],
        json!("Signed search summary preset.")
    );
    assert_eq!(config["common"]["temperature"], json!(0.2));
    assert_eq!(
        config["common"]["max_output_tokens"],
        json!(max_output_tokens)
    );
    assert_eq!(
        config["provider_options"]["openai"]["reasoning_effort"],
        json!("high")
    );
    assert_eq!(config["caching"]["strategy"], json!("memory"));
    assert_eq!(config["caching"]["ttl"], json!(600));
    assert_eq!(config["tags"], json!(["production", "signed"]));
}

fn publish_options<'a>(
    revision: &'a str,
    signer: &'a dyn RegistryRootSigner,
    mda_options: MdaConfigLoadOptions<'a>,
) -> ConfigRegistryPublishOptions<'a> {
    ConfigRegistryPublishOptions {
        revision: Some(revision),
        activate: true,
        mda_options,
        registry_root: Some(RegistryRootSigningOptions {
            signer,
            min_signatures: Some(1),
        }),
    }
}

fn mda_load_options(verifier: &dyn DidWebVerifier) -> MdaConfigLoadOptions<'_> {
    MdaConfigLoadOptions {
        verify_integrity: true,
        verify_signatures: true,
        trusted_runtime: true,
        trust_policy: Some(trust_policy()),
        did_web_verifier: Some(verifier),
        ..MdaConfigLoadOptions::default()
    }
}

fn registry_open_options(expected_root_digest: Option<String>) -> ConfigRegistryOpenOptions {
    ConfigRegistryOpenOptions {
        signed_root: Some(RegistryRootVerificationOptions {
            trust_policy: trust_policy(),
            rekor_client: None,
            sigstore_verifier: None,
            did_web_verifier: Some(Arc::new(DeterministicDidWebVerifier)),
            expected_revision: None,
            expected_root_digest,
            minimum_revision: None,
            minimum_published_at: None,
            high_watermark: None,
        }),
    }
}

fn trust_policy() -> TrustPolicy {
    TrustPolicy {
        version: 1,
        trusted_signers: vec![TrustedSigner::DidWeb {
            domain: SIGNER_DOMAIN.to_string(),
        }],
        min_signatures: Some(1),
        rekor: None,
    }
}

fn signed_mda_source(model: &str, max_output_tokens: u32, body: &str) -> String {
    let frontmatter = json!({
        "name": "summary",
        "description": "Signed search summary preset.",
        "tags": ["production", "signed"],
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": "openai",
                    "model": model,
                    "temperature": 0.2,
                    "maxOutputTokens": max_output_tokens,
                },
                "providerOptions": {
                    "openai": {
                        "reasoningEffort": "high",
                    },
                },
                "caching": {
                    "strategy": "memory",
                    "ttl": 600,
                },
            },
        },
    });
    let canonical = canonicalize_artifact(&frontmatter, body).expect("fixture should canonicalize");
    let digest = hash_canonical(&canonical, HashAlgorithm::Sha256);
    let integrity = IntegrityField {
        algorithm: HashAlgorithm::Sha256,
        digest: format!("sha256:{digest}"),
    };
    let signature = sign_integrity(DEFAULT_PAYLOAD_TYPE, &integrity);

    format!(
        r#"---
name: summary
description: Signed search summary preset.
tags:
  - production
  - signed
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: {model}
      temperature: 0.2
      maxOutputTokens: {max_output_tokens}
    providerOptions:
      openai:
        reasoningEffort: high
    caching:
      strategy: memory
      ttl: 600
integrity:
  algorithm: sha256
  digest: sha256:{digest}
signatures:
  - signer: did-web:{SIGNER_DOMAIN}
    key-id: {SIGNER_KEY_ID}
    payload-digest: sha256:{digest}
    algorithm: ed25519
    signature: "{signature}"
    payload-type: {DEFAULT_PAYLOAD_TYPE}
---
{body}"#
    )
}

fn sign_integrity(payload_type: &str, integrity: &IntegrityField) -> String {
    let payload_bytes = format!(
        r#"{{"integrity":{{"algorithm":"{}","digest":"{}"}}}}"#,
        integrity.algorithm.as_str(),
        integrity.digest
    )
    .into_bytes();
    sign_payload(payload_type, &payload_bytes)
}

fn sign_payload(payload_type: &str, payload_bytes: &[u8]) -> String {
    let pae_bytes = construct_dsse_pae(payload_type, payload_bytes);
    sha256_hex(&pae_bytes)
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
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
