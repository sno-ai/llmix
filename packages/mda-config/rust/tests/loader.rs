use std::{cell::Cell, path::PathBuf};

use serde::Deserialize;
use serde_json::{json, Value};
use snoai_mda_config::{
    load_mda_source, load_mda_source_from_bytes, validate_trust_policy, verify_signatures,
    DidWebVerificationInput, DidWebVerifier, DsseEnvelope, DsseSignature, ErrorCategory,
    HashAlgorithm, IntegrityField, LoadMdaSourceOptions, MdaConfigError, RekorClient, RekorEntry,
    RekorPolicy, Result, SignatureEntry, SigstoreVerificationResult, SigstoreVerifier, TrustPolicy,
    TrustedSigner,
};

fn fixture(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../fixtures/mda")
        .join(rel)
}

#[derive(Debug, Deserialize)]
struct MinimalConfig {
    name: String,
    description: String,
    metadata: Option<Value>,
    requires: Option<Value>,
    integrity: Option<IntegrityField>,
    signatures: Option<Vec<Value>>,
}

#[derive(Debug, Deserialize)]
struct NarrowConfig {
    _must_exist: String,
}

struct MockRekor {
    entry: Option<RekorEntry>,
    url: &'static str,
}

impl RekorClient for MockRekor {
    fn rekor_url(&self) -> Option<&str> {
        Some(self.url)
    }

    fn fetch_entry(
        &self,
        _rekor_url: &str,
        _log_id: &str,
        _log_index: u64,
    ) -> Result<Option<RekorEntry>> {
        Ok(self.entry.clone())
    }
}

struct OneCallRekor {
    entry: RekorEntry,
    url: &'static str,
    called: Cell<bool>,
}

impl RekorClient for OneCallRekor {
    fn rekor_url(&self) -> Option<&str> {
        Some(self.url)
    }

    fn fetch_entry(
        &self,
        _rekor_url: &str,
        _log_id: &str,
        _log_index: u64,
    ) -> Result<Option<RekorEntry>> {
        if self.called.replace(true) {
            panic!("signature quorum should stop before trailing Rekor fetch");
        }
        Ok(Some(self.entry.clone()))
    }
}

struct PanicRekor;

impl RekorClient for PanicRekor {
    fn rekor_url(&self) -> Option<&str> {
        Some("https://rekor.sigstore.dev")
    }

    fn fetch_entry(
        &self,
        _rekor_url: &str,
        _log_id: &str,
        _log_index: u64,
    ) -> Result<Option<RekorEntry>> {
        panic!("untrusted Sigstore issuer must not reach Rekor");
    }
}

struct MockVerifier {
    issuer: &'static str,
    identity: &'static str,
}

impl SigstoreVerifier for MockVerifier {
    fn verify(
        &self,
        _entry: &RekorEntry,
        _signature: &SignatureEntry,
        _pae_bytes: &[u8],
    ) -> Result<SigstoreVerificationResult> {
        Ok(SigstoreVerificationResult {
            certificate_pem: None,
            issuer: Some(self.issuer.to_string()),
            subject: Some(self.identity.to_string()),
            subject_alternative_name: Some(self.identity.to_string()),
        })
    }
}

struct IncompleteVerifier;

impl SigstoreVerifier for IncompleteVerifier {
    fn verify(
        &self,
        _entry: &RekorEntry,
        _signature: &SignatureEntry,
        _pae_bytes: &[u8],
    ) -> Result<SigstoreVerificationResult> {
        Ok(SigstoreVerificationResult {
            certificate_pem: None,
            issuer: None,
            subject: None,
            subject_alternative_name: None,
        })
    }
}

struct IssuerOnlyVerifier;

impl SigstoreVerifier for IssuerOnlyVerifier {
    fn verify(
        &self,
        _entry: &RekorEntry,
        _signature: &SignatureEntry,
        _pae_bytes: &[u8],
    ) -> Result<SigstoreVerificationResult> {
        Ok(SigstoreVerificationResult {
            certificate_pem: Some("email:releases@snoai.com".to_string()),
            issuer: Some("https://accounts.google.com".to_string()),
            subject: None,
            subject_alternative_name: None,
        })
    }
}

struct PanicSigstoreVerifier;

impl SigstoreVerifier for PanicSigstoreVerifier {
    fn verify(
        &self,
        _entry: &RekorEntry,
        _signature: &SignatureEntry,
        _pae_bytes: &[u8],
    ) -> Result<SigstoreVerificationResult> {
        panic!("untrusted Sigstore issuer must not reach verifier");
    }
}

struct MockDidWebVerifier {
    trusted: bool,
}

impl DidWebVerifier for MockDidWebVerifier {
    fn verify(&self, input: DidWebVerificationInput<'_>) -> Result<bool> {
        assert_eq!(input.domain, "tools.example.com");
        assert!(!input.pae_bytes.is_empty());
        Ok(self.trusted)
    }
}

struct PanicDidWebVerifier;

impl DidWebVerifier for PanicDidWebVerifier {
    fn verify(&self, _input: DidWebVerificationInput<'_>) -> Result<bool> {
        panic!("untrusted did:web domain must not reach the verifier");
    }
}

const FIXTURE_PAYLOAD_TYPE: &str = "application/vnd.snoai-llmix.preset+json";
const FIXTURE_PAYLOAD_B64: &str =
    "eyJhbGdvcml0aG0iOiJzaGEyNTYiLCJkaWdlc3QiOiJzaGEyNTY6OTY5NzQ0OGI2ZjNmODhiNzE4NzBkZDViNjA4OTk5YWRlNzE3ZjczZDRlZWJmNjdmMDJhYzAzZGZlMTc3YTM3ZSJ9";
const FIXTURE_SIGNATURE: &str = "MEUCIQDkXFIXTUREONLYBASE64==";
const FIXTURE_KEY_ID: &str =
    "fulcio:9c4e7b2f1a05c3b9e2d6c2b1e7f0a8d4c3b9e2f1a05c3b9e2d6c2b1e7f0a8d4c";
const FIXTURE_SIGNER: &str = "sigstore-oidc:https://accounts.google.com";

fn dsse_entry(kind: &str) -> RekorEntry {
    RekorEntry {
        kind: kind.to_string(),
        log_id: Some(
            "c0d23b6c4f200000000000000000000000000000000000000000000000000000".to_string(),
        ),
        log_index: Some(87654321),
        inclusion_verified: Some(true),
        certificate_pem: Some(
            "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n".to_string(),
        ),
        dsse_envelope: DsseEnvelope {
            payload_type: FIXTURE_PAYLOAD_TYPE.to_string(),
            payload: FIXTURE_PAYLOAD_B64.to_string(),
            signatures: vec![DsseSignature {
                sig: FIXTURE_SIGNATURE.to_string(),
                keyid: Some(FIXTURE_KEY_ID.to_string()),
            }],
        },
    }
}

fn wrong_payload_dsse_entry() -> RekorEntry {
    let mut entry = dsse_entry("dsse-v0.0.1");
    entry.dsse_envelope.payload = "e30=".to_string();
    entry
}

fn trust_policy(subject: &str) -> TrustPolicy {
    TrustPolicy {
        version: 1,
        trusted_signers: vec![TrustedSigner::SigstoreOidc {
            issuer: "https://accounts.google.com".to_string(),
            subject: subject.to_string(),
        }],
        min_signatures: None,
        rekor: Some(RekorPolicy {
            url: "https://rekor.sigstore.dev".to_string(),
        }),
    }
}

fn did_web_policy(min_signatures: Option<usize>) -> TrustPolicy {
    TrustPolicy {
        version: 1,
        trusted_signers: vec![TrustedSigner::DidWeb {
            domain: "tools.example.com".to_string(),
        }],
        min_signatures,
        rekor: None,
    }
}

fn sigstore_signed_fixture_with_bound_identity() -> String {
    include_str!("../../../../fixtures/mda/valid/03-sigstore-signed.mda").to_string()
}

fn sigstore_signed_fixture_with_invalid_extra_signature() -> String {
    sigstore_signed_fixture_with_bound_identity().replace(
        "  - signer:",
        r#"  - signer: "sigstore-oidc:https://accounts.google.com"
    key-id: "fulcio:attacker"
    payload-digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
    algorithm: ecdsa-p256
    signature: "BADFIXTURE=="
    rekor-log-id: "c0d23b6c4f200000000000000000000000000000000000000000000000000"
    rekor-log-index: 87654321
    payload-type: "application/vnd.snoai-llmix.preset+json"
  - signer:"#,
    )
}

fn sigstore_signed_fixture_with_trailing_sigstore_signature() -> String {
    sigstore_signed_fixture_with_bound_identity().replace(
        "metadata:",
        r#"  - signer: "sigstore-oidc:https://accounts.google.com"
    key-id: "fulcio:trailing"
    payload-digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
    algorithm: ecdsa-p256
    signature: "TRAILINGFIXTUREONLY=="
    rekor-log-id: "c0d23b6c4f200000000000000000000000000000000000000000000000000000"
    rekor-log-index: 87654322
    payload-type: "application/vnd.snoai-llmix.preset+json"
metadata:"#,
    )
}

fn sigstore_signed_fixture_with_untrusted_issuer() -> String {
    sigstore_signed_fixture_with_bound_identity()
        .replace(FIXTURE_SIGNER, "sigstore-oidc:https://issuer.example.com")
}

fn sigstore_signed_fixture_with_did_web_extra_signature() -> String {
    sigstore_signed_fixture_with_bound_identity().replace(
        "  - signer:",
        r#"  - signer: "did-web:tools.example.com"
    key-id: "did:web:tools.example.com#key-1"
    payload-digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
    algorithm: ed25519
    signature: "DIDWEBFIXTUREONLY=="
    payload-type: "application/vnd.snoai-llmix.preset+json"
  - signer:"#,
    )
}

fn did_web_signed_fixture() -> String {
    sigstore_signed_fixture_with_bound_identity()
        .replace(FIXTURE_SIGNER, "did-web:tools.example.com")
        .lines()
        .filter(|line| {
            !line.trim_start().starts_with("rekor-log-id:")
                && !line.trim_start().starts_with("rekor-log-index:")
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn did_web_signed_fixture_with_untrusted_domain() -> String {
    did_web_signed_fixture().replace("did-web:tools.example.com", "did-web:evil.example.com")
}

fn did_web_signed_fixture_with_sigstore_extra_signature() -> String {
    did_web_signed_fixture().replace(
        "  - signer:",
        r#"  - signer: "sigstore-oidc:https://accounts.google.com"
    key-id: "fulcio:extra"
    payload-digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
    algorithm: ecdsa-p256
    signature: "SIGSTOREFIXTUREONLY=="
    rekor-log-id: "c0d23b6c4f200000000000000000000000000000000000000000000000000"
    rekor-log-index: 87654321
    payload-type: "application/vnd.snoai-llmix.preset+json"
  - signer:"#,
    )
}

fn expect_category<T: std::fmt::Debug>(result: Result<T>, category: ErrorCategory) {
    let err = result.expect_err("expected MDA config error");
    assert_eq!(err.category, category, "{err}");
}

#[test]
fn loads_minimal_source_mode_file() {
    let cfg: MinimalConfig = load_mda_source(
        fixture("valid/01-minimal.mda"),
        LoadMdaSourceOptions::default(),
    )
    .expect("minimal fixture should load");
    assert_eq!(cfg.name, "minimal-config");
    assert!(cfg.description.contains("Minimal"));
    assert!(cfg.metadata.is_none());
}

#[test]
fn rejects_invalid_utf8() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("invalid/13-non-utf8.mda"),
            LoadMdaSourceOptions::default(),
        ),
        ErrorCategory::InvalidEncoding,
    );
}

#[test]
fn rejects_yaml_parse_errors() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("invalid/10-yaml-parse-error.mda"),
            LoadMdaSourceOptions::default(),
        ),
        ErrorCategory::FrontmatterYamlParseError,
    );
}

#[test]
fn verifies_matching_integrity() {
    let cfg: MinimalConfig = load_mda_source(
        fixture("valid/02-with-integrity.mda"),
        LoadMdaSourceOptions {
            verify_integrity: true,
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("integrity fixture should verify");
    assert_eq!(
        cfg.integrity.expect("integrity").digest,
        "sha256:2259366cf445b4ca014780a0f2b8ee4aa9b8ebea92c1f7eda0c2903159f6ca48",
    );
}

#[test]
fn rejects_integrity_mismatch() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("invalid/11-integrity-mismatch.mda"),
            LoadMdaSourceOptions {
                verify_integrity: true,
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::IntegrityMismatch,
    );
}

#[test]
fn rejects_missing_integrity_when_integrity_verification_is_required() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("valid/01-minimal.mda"),
            LoadMdaSourceOptions {
                verify_integrity: true,
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::SchemaViolation,
    );
}

#[test]
fn rejects_signature_digest_mismatch_during_cross_field_check() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("invalid/12-signature-digest-mismatch.mda"),
            LoadMdaSourceOptions::default(),
        ),
        ErrorCategory::SignatureDigestMismatch,
    );
}

#[test]
fn surfaces_consumer_schema_failure() {
    expect_category(
        load_mda_source::<NarrowConfig>(
            fixture("valid/01-minimal.mda"),
            LoadMdaSourceOptions::default(),
        ),
        ErrorCategory::ProjectSchemaViolation,
    );
}

#[test]
fn accepts_requires_network_allowlist_and_glob() {
    let exact: MinimalConfig = load_mda_source(
        fixture("valid/04-with-requires-network.mda"),
        LoadMdaSourceOptions {
            enforce_requires: true,
            allowed_networks: vec!["api.openai.com".to_string()],
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("exact allowed host should pass");
    assert!(exact.requires.is_some());

    let glob: MinimalConfig = load_mda_source(
        fixture("valid/04-with-requires-network.mda"),
        LoadMdaSourceOptions {
            enforce_requires: true,
            allowed_networks: vec!["*.openai.com".to_string()],
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("glob host should pass");
    assert!(glob.requires.is_some());
}

#[test]
fn rejects_requires_network_outside_allowlist() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("invalid/15-network-violation.mda"),
            LoadMdaSourceOptions {
                enforce_requires: true,
                allowed_networks: vec!["api.openai.com".to_string()],
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::RequiresNotSatisfied,
    );
}

#[test]
fn accepts_sigstore_signed_fixture_with_injected_verifier() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_bound_identity();
    let cfg: MinimalConfig = load_mda_source_from_bytes(
        src.as_bytes(),
        LoadMdaSourceOptions {
            verify_integrity: true,
            verify_signatures: true,
            trust_policy: Some(trust_policy("releases@snoai.com")),
            rekor_client: Some(&rekor),
            sigstore_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("mock Sigstore verification should pass");
    assert!(cfg.signatures.expect("signatures").len() == 1);
}

#[test]
fn stops_verification_after_signature_quorum_is_met() {
    let rekor = OneCallRekor {
        entry: dsse_entry("dsse-v0.0.1"),
        url: "https://rekor.sigstore.dev",
        called: Cell::new(false),
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_trailing_sigstore_signature();
    load_mda_source_from_bytes::<MinimalConfig>(
        src.as_bytes(),
        LoadMdaSourceOptions {
            trusted_runtime: true,
            trust_policy: Some(trust_policy("releases@snoai.com")),
            rekor_client: Some(&rekor),
            sigstore_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("first trusted identity should satisfy the default threshold");
    assert!(rekor.called.get());
}

#[test]
fn rejects_untrusted_sigstore_issuer_without_invoking_rekor_or_verifier() {
    let rekor = PanicRekor;
    let verifier = PanicSigstoreVerifier;
    let src = sigstore_signed_fixture_with_untrusted_issuer();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                trusted_runtime: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::NoTrustedSignature,
    );
}

#[test]
fn accepts_valid_signature_when_extra_candidate_is_invalid() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_invalid_extra_signature();
    let cfg: MinimalConfig = load_mda_source_from_bytes(
        src.as_bytes(),
        LoadMdaSourceOptions {
            verify_signatures: true,
            trust_policy: Some(trust_policy("releases@snoai.com")),
            rekor_client: Some(&rekor),
            sigstore_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("one trusted signature should be enough to verify");
    assert_eq!(cfg.signatures.expect("signatures").len(), 2);
}

#[test]
fn accepts_sigstore_policy_when_extra_did_web_candidate_has_no_hook() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_did_web_extra_signature();
    load_mda_source_from_bytes::<MinimalConfig>(
        src.as_bytes(),
        LoadMdaSourceOptions {
            trusted_runtime: true,
            trust_policy: Some(trust_policy("releases@snoai.com")),
            rekor_client: Some(&rekor),
            sigstore_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("untrusted did:web candidate should not panic or block trusted Sigstore identity");
}

#[test]
fn accepts_did_web_policy_when_extra_sigstore_candidate_has_no_hooks() {
    let verifier = MockDidWebVerifier { trusted: true };
    let src = did_web_signed_fixture_with_sigstore_extra_signature();
    load_mda_source_from_bytes::<MinimalConfig>(
        src.as_bytes(),
        LoadMdaSourceOptions {
            trusted_runtime: true,
            trust_policy: Some(did_web_policy(None)),
            did_web_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("untrusted Sigstore candidate should not panic or block trusted did:web identity");
}

#[test]
fn signature_verification_implies_integrity_check() {
    let src = sigstore_signed_fixture_with_bound_identity()
        .replace("# Sigstore signed", "# Tampered after signing");
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::IntegrityMismatch,
    );
}

#[test]
fn direct_signature_verifier_rejects_empty_signature_list() {
    let rekor = MockRekor {
        entry: None,
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let integrity = IntegrityField {
        algorithm: HashAlgorithm::Sha256,
        digest: "sha256:9697448b6f3f88b71870dd5b608999ade717f73d4eebf67f02ac03dfe177a37e"
            .to_string(),
    };
    expect_category(
        verify_signatures(
            &[],
            &integrity,
            &trust_policy("releases@snoai.com"),
            Some(&rekor),
            Some(&verifier),
            None,
        ),
        ErrorCategory::MissingRequiredSignature,
    );
}

#[test]
fn rejects_unsigned_source_when_signature_verification_is_required() {
    expect_category(
        load_mda_source::<MinimalConfig>(
            fixture("valid/01-minimal.mda"),
            LoadMdaSourceOptions {
                verify_signatures: true,
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::TrustPolicyViolation,
    );
}

#[test]
fn rejects_rekor_entry_with_unbound_dsse_payload() {
    let rekor = MockRekor {
        entry: Some(wrong_payload_dsse_entry()),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_bound_identity();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::RekorInclusionFailure,
    );
}

#[test]
fn rejects_signature_verification_without_configured_verifier() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let src = sigstore_signed_fixture_with_bound_identity();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: None,
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::TrustPolicyViolation,
    );
}

#[test]
fn rejects_sigstore_verifier_without_verified_identity_claims() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = IncompleteVerifier;
    let src = sigstore_signed_fixture_with_bound_identity();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::SignatureVerificationFailure,
    );
}

#[test]
fn rejects_sigstore_verifier_without_verified_subject_even_when_cert_text_has_identity() {
    let mut entry = dsse_entry("dsse-v0.0.1");
    entry.certificate_pem = Some("email:releases@snoai.com".to_string());
    let rekor = MockRekor {
        entry: Some(entry),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = IssuerOnlyVerifier;
    let src = sigstore_signed_fixture_with_bound_identity();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::SignatureVerificationFailure,
    );
}

#[test]
fn rejects_rekor_entry_without_explicit_coordinate_and_inclusion_binding() {
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_bound_identity();
    for missing_field in ["log_id", "log_index", "inclusion_verified"] {
        let mut entry = dsse_entry("dsse-v0.0.1");
        match missing_field {
            "log_id" => entry.log_id = None,
            "log_index" => entry.log_index = None,
            "inclusion_verified" => entry.inclusion_verified = None,
            _ => unreachable!(),
        }
        let rekor = MockRekor {
            entry: Some(entry),
            url: "https://rekor.sigstore.dev",
        };
        expect_category(
            load_mda_source_from_bytes::<MinimalConfig>(
                src.as_bytes(),
                LoadMdaSourceOptions {
                    verify_signatures: true,
                    trust_policy: Some(trust_policy("releases@snoai.com")),
                    rekor_client: Some(&rekor),
                    sigstore_verifier: Some(&verifier),
                    ..LoadMdaSourceOptions::default()
                },
            ),
            ErrorCategory::RekorInclusionFailure,
        );
    }
}

#[test]
fn rejects_rekor_entry_with_wrong_kind() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("hashedrekord-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src =
        include_str!("../../../../fixtures/mda/invalid/14-rekor-entry-type-wrong.mda").to_string();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_integrity: true,
                verify_signatures: true,
                trust_policy: Some(trust_policy("releases@snoai.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::RekorEntryTypeMismatch,
    );
}

#[test]
fn rejects_untrusted_sigstore_identity() {
    let rekor = MockRekor {
        entry: Some(dsse_entry("dsse-v0.0.1")),
        url: "https://rekor.sigstore.dev",
    };
    let verifier = MockVerifier {
        issuer: "https://accounts.google.com",
        identity: "releases@snoai.com",
    };
    let src = sigstore_signed_fixture_with_bound_identity();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                verify_integrity: true,
                verify_signatures: true,
                trust_policy: Some(trust_policy("someone-else@example.com")),
                rekor_client: Some(&rekor),
                sigstore_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::NoTrustedSignature,
    );
}

#[test]
fn rejects_old_policy_allow_list_fields() {
    expect_category(
        validate_trust_policy(json!({"version": 1, "allowedSigners": []})),
        ErrorCategory::TrustPolicyViolation,
    );
    expect_category(
        validate_trust_policy(json!({"version": 1, "allowedIssuers": []})),
        ErrorCategory::TrustPolicyViolation,
    );
}

#[test]
fn rejects_legacy_sigstore_signer_with_subject_fragment() {
    let src = sigstore_signed_fixture_with_bound_identity().replace(
        FIXTURE_SIGNER,
        "sigstore-oidc:https://accounts.google.com#releases@snoai.com",
    );
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions::default(),
        ),
        ErrorCategory::SchemaViolation,
    );
}

#[test]
fn rejects_did_web_policy_without_verifier() {
    let src = did_web_signed_fixture();
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                trusted_runtime: true,
                trust_policy: Some(did_web_policy(None)),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::TrustPolicyViolation,
    );
}

#[test]
fn rejects_did_web_candidate_when_verifier_returns_false() {
    let src = did_web_signed_fixture();
    let verifier = MockDidWebVerifier { trusted: false };
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                trusted_runtime: true,
                trust_policy: Some(did_web_policy(None)),
                did_web_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::NoTrustedSignature,
    );
}

#[test]
fn rejects_untrusted_did_web_domain_without_invoking_verifier() {
    let src = did_web_signed_fixture_with_untrusted_domain();
    let verifier = PanicDidWebVerifier;
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src.as_bytes(),
            LoadMdaSourceOptions {
                trusted_runtime: true,
                trust_policy: Some(did_web_policy(None)),
                did_web_verifier: Some(&verifier),
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::NoTrustedSignature,
    );
}

#[test]
fn accepts_did_web_candidate_with_matching_verifier_and_policy() {
    let src = did_web_signed_fixture();
    let verifier = MockDidWebVerifier { trusted: true };
    let cfg: MinimalConfig = load_mda_source_from_bytes(
        src.as_bytes(),
        LoadMdaSourceOptions {
            trusted_runtime: true,
            trust_policy: Some(did_web_policy(None)),
            did_web_verifier: Some(&verifier),
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("did:web cryptographic verifier and policy should trust the signature");
    assert_eq!(cfg.signatures.expect("signatures").len(), 1);
}

#[test]
fn rejects_invalid_requires_network_shape_during_requires_enforcement() {
    let src = br#"---
name: bad-network-shape
description: requires.network value is invalid
requires:
  network: 7
---
"#;
    let err = load_mda_source_from_bytes::<MinimalConfig>(
        src,
        LoadMdaSourceOptions {
            enforce_requires: true,
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect_err("invalid requires.network should fail");
    assert_eq!(err.category, ErrorCategory::RequiresNotSatisfied);
    assert_eq!(err.details["reason"], "invalid-shape");
}

#[test]
fn accepts_local_network_requirement_without_explicit_local_grant() {
    let src = br#"---
name: local-network
description: requires local network
requires:
  network: local
---
"#;
    load_mda_source_from_bytes::<MinimalConfig>(
        src,
        LoadMdaSourceOptions {
            enforce_requires: true,
            ..LoadMdaSourceOptions::default()
        },
    )
    .expect("empty operator allow-list should satisfy requires.network=local");
}

#[test]
fn accepts_local_network_requirement_for_local_grants() {
    let src = br#"---
name: local-network
description: requires local network
requires:
  network: local
---
"#;
    for host in [
        "localhost",
        "devbox.localhost",
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.20",
        "::1",
        "*.local",
        "printer.local",
        "api.internal",
    ] {
        load_mda_source_from_bytes::<MinimalConfig>(
            src,
            LoadMdaSourceOptions {
                enforce_requires: true,
                allowed_networks: vec![host.to_string()],
                ..LoadMdaSourceOptions::default()
            },
        )
        .unwrap_or_else(|err| panic!("{host} should satisfy local network requirement: {err}"));
    }
}

#[test]
fn rejects_hostname_that_only_looks_like_private_ip_for_local_network() {
    let src = br#"---
name: local-network
description: requires local network
requires:
  network: local
---
"#;
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src,
            LoadMdaSourceOptions {
                enforce_requires: true,
                allowed_networks: vec!["10.attacker.example".to_string()],
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::RequiresNotSatisfied,
    );
}

#[test]
fn rejects_metadata_service_address_for_local_network() {
    let src = br#"---
name: local-network
description: requires local network
requires:
  network: local
---
"#;
    expect_category(
        load_mda_source_from_bytes::<MinimalConfig>(
            src,
            LoadMdaSourceOptions {
                enforce_requires: true,
                allowed_networks: vec!["169.254.169.254".to_string()],
                ..LoadMdaSourceOptions::default()
            },
        ),
        ErrorCategory::RequiresNotSatisfied,
    );
}

#[test]
fn error_display_includes_category() {
    let err = MdaConfigError::new(ErrorCategory::SchemaViolation, "example");
    assert_eq!(err.to_string(), "[schema-violation] example");
}
