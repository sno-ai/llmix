use std::collections::HashSet;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::errors::{ErrorCategory, MdaConfigError, Result};
use crate::integrity::{canonical_json, IntegrityField};
use crate::trust_policy::TrustPolicy;

pub const DEFAULT_PAYLOAD_TYPE: &str = "application/vnd.mda.integrity+json";
const SIGSTORE_OIDC_PREFIX: &str = "sigstore-oidc:";
const DID_WEB_PREFIX: &str = "did-web:";

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct SignatureEntry {
    pub signer: String,
    #[serde(rename = "key-id")]
    pub key_id: String,
    #[serde(rename = "payload-digest")]
    pub payload_digest: String,
    pub algorithm: String,
    pub signature: String,
    #[serde(rename = "rekor-log-id")]
    pub rekor_log_id: Option<String>,
    #[serde(rename = "rekor-log-index")]
    pub rekor_log_index: Option<u64>,
    #[serde(rename = "payload-type")]
    pub payload_type: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RekorEntry {
    pub kind: String,
    pub log_id: Option<String>,
    pub log_index: Option<u64>,
    pub inclusion_verified: Option<bool>,
    pub certificate_pem: Option<String>,
    pub dsse_envelope: DsseEnvelope,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DsseEnvelope {
    pub payload_type: String,
    pub payload: String,
    pub signatures: Vec<DsseSignature>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DsseSignature {
    pub sig: String,
    pub keyid: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SigstoreVerificationResult {
    pub certificate_pem: Option<String>,
    pub issuer: Option<String>,
    pub subject: Option<String>,
    pub subject_alternative_name: Option<String>,
}

pub struct DidWebVerificationInput<'a> {
    pub domain: &'a str,
    pub key_id: &'a str,
    pub algorithm: &'a str,
    pub signature: &'a str,
    pub payload_type: &'a str,
    pub payload_bytes: &'a [u8],
    pub pae_bytes: &'a [u8],
}

pub trait RekorClient {
    fn rekor_url(&self) -> Option<&str> {
        None
    }

    fn fetch_entry(
        &self,
        rekor_url: &str,
        log_id: &str,
        log_index: u64,
    ) -> Result<Option<RekorEntry>>;
}

pub trait SigstoreVerifier {
    fn verify(
        &self,
        entry: &RekorEntry,
        signature: &SignatureEntry,
        pae_bytes: &[u8],
    ) -> Result<SigstoreVerificationResult>;
}

pub trait DidWebVerifier {
    fn verify(&self, input: DidWebVerificationInput<'_>) -> Result<bool>;
}

pub fn construct_dsse_pae(payload_type: &str, payload_bytes: &[u8]) -> Vec<u8> {
    let head = format!(
        "DSSEv1 {} {} {} ",
        payload_type.len(),
        payload_type,
        payload_bytes.len()
    );
    [head.as_bytes(), payload_bytes].concat()
}

pub fn verify_signatures(
    signatures: &[SignatureEntry],
    integrity: &IntegrityField,
    policy: &TrustPolicy,
    rekor_client: Option<&dyn RekorClient>,
    sigstore_verifier: Option<&dyn SigstoreVerifier>,
    did_web_verifier: Option<&dyn DidWebVerifier>,
) -> Result<()> {
    let payload_bytes = pae_payload_bytes(integrity)?;
    verify_signatures_with_payload(
        signatures,
        integrity,
        policy,
        rekor_client,
        sigstore_verifier,
        did_web_verifier,
        &payload_bytes,
    )
}

pub fn verify_signatures_with_payload(
    signatures: &[SignatureEntry],
    integrity: &IntegrityField,
    policy: &TrustPolicy,
    rekor_client: Option<&dyn RekorClient>,
    sigstore_verifier: Option<&dyn SigstoreVerifier>,
    did_web_verifier: Option<&dyn DidWebVerifier>,
    payload_bytes: &[u8],
) -> Result<()> {
    policy.validate()?;
    assert_verifier_hooks(policy, rekor_client, sigstore_verifier, did_web_verifier)?;
    if signatures.is_empty() {
        return Err(MdaConfigError::new(
            ErrorCategory::MissingRequiredSignature,
            "trusted-runtime requires a non-empty signatures[] field",
        ));
    }

    for sig in signatures {
        validate_signature_shape(sig)?;
        assert_payload_digest(sig, integrity)?;
    }

    let required = policy.required_signatures();
    let mut trusted = HashSet::new();
    let mut candidate_errors = Vec::new();
    for sig in signatures {
        match verify_candidate(
            sig,
            payload_bytes,
            policy,
            rekor_client,
            sigstore_verifier,
            did_web_verifier,
        ) {
            Ok(Some(identity)) => {
                trusted.insert(identity);
                if trusted.len() >= required {
                    return Ok(());
                }
            }
            Ok(None) => {}
            Err(err) => candidate_errors.push(err),
        }
    }

    if !trusted.is_empty() {
        return Err(MdaConfigError::with_details(
            ErrorCategory::InsufficientTrustedSignatures,
            "trusted signatures did not satisfy minSignatures",
            serde_json::json!({ "trusted": trusted.len(), "required": required }),
        ));
    }
    Err(most_specific_candidate_error(&candidate_errors))
}

fn assert_verifier_hooks(
    policy: &TrustPolicy,
    rekor_client: Option<&dyn RekorClient>,
    sigstore_verifier: Option<&dyn SigstoreVerifier>,
    did_web_verifier: Option<&dyn DidWebVerifier>,
) -> Result<()> {
    if policy.rekor.is_some() {
        let rekor = policy.rekor.as_ref().expect("checked is_some");
        let Some(client) = rekor_client else {
            return policy_error("Sigstore trusted-runtime requires rekor_client");
        };
        if let Some(client_url) = client.rekor_url() {
            if client_url != rekor.url {
                return Err(MdaConfigError::with_details(
                    ErrorCategory::TrustPolicyViolation,
                    "Rekor client URL does not match trustPolicy.rekor.url",
                    serde_json::json!({ "policyUrl": rekor.url, "clientUrl": client_url }),
                ));
            }
        }
        if sigstore_verifier.is_none() {
            return policy_error("Sigstore trusted-runtime requires sigstore_verifier");
        }
    }
    if policy.contains_did_web() && did_web_verifier.is_none() {
        return policy_error("did:web trusted-runtime requires did_web_verifier");
    }
    Ok(())
}

fn verify_candidate(
    sig: &SignatureEntry,
    payload_bytes: &[u8],
    policy: &TrustPolicy,
    rekor_client: Option<&dyn RekorClient>,
    sigstore_verifier: Option<&dyn SigstoreVerifier>,
    did_web_verifier: Option<&dyn DidWebVerifier>,
) -> Result<Option<String>> {
    if sig.signer.starts_with(SIGSTORE_OIDC_PREFIX) {
        let Some(rekor_client) = rekor_client else {
            return Ok(None);
        };
        let Some(sigstore_verifier) = sigstore_verifier else {
            return Ok(None);
        };
        return verify_sigstore_candidate(
            sig,
            payload_bytes,
            policy,
            rekor_client,
            sigstore_verifier,
        );
    }
    if sig.signer.starts_with(DID_WEB_PREFIX) {
        let Some(did_web_verifier) = did_web_verifier else {
            return Ok(None);
        };
        return verify_did_web_candidate(sig, payload_bytes, policy, did_web_verifier);
    }
    Err(MdaConfigError::with_details(
        ErrorCategory::UnknownSignerMethod,
        "unknown signer method",
        serde_json::json!({ "signer": sig.signer }),
    ))
}

fn verify_sigstore_candidate(
    sig: &SignatureEntry,
    payload_bytes: &[u8],
    policy: &TrustPolicy,
    rekor_client: &dyn RekorClient,
    verifier: &dyn SigstoreVerifier,
) -> Result<Option<String>> {
    let signer_issuer = parse_sigstore_signer(&sig.signer)?;
    if !policy.sigstore_issuer_trusted(signer_issuer) {
        return Ok(None);
    }
    let payload_type = declared_payload_type(sig)?;
    let entry = fetch_rekor_entry(sig, policy, rekor_client)?;
    validate_rekor_dsse_envelope(&entry, sig, payload_type, payload_bytes)?;
    let pae_bytes = construct_dsse_pae(payload_type, payload_bytes);
    let verification = verifier.verify(&entry, sig, &pae_bytes).map_err(|cause| {
        MdaConfigError::with_details(
            ErrorCategory::SignatureVerificationFailure,
            "Sigstore verification failed",
            serde_json::json!({ "signer": sig.signer, "cause": cause.to_string() }),
        )
    })?;
    let issuer = verification.issuer.as_deref().ok_or_else(|| {
        MdaConfigError::with_details(
            ErrorCategory::SignatureVerificationFailure,
            "Sigstore verifier did not return verified issuer",
            serde_json::json!({ "signer": sig.signer }),
        )
    })?;
    let subject = verification
        .subject
        .as_deref()
        .or(verification.subject_alternative_name.as_deref())
        .map(str::to_string)
        .ok_or_else(|| {
            MdaConfigError::with_details(
                ErrorCategory::SignatureVerificationFailure,
                "Sigstore verifier did not return verified subject",
                serde_json::json!({ "signer": sig.signer }),
            )
        })?;

    if issuer != signer_issuer || !policy.sigstore_subject_trusted(issuer, &subject) {
        return Ok(None);
    }
    Ok(Some(format!("sigstore-oidc\0{issuer}\0{subject}")))
}

fn verify_did_web_candidate(
    sig: &SignatureEntry,
    payload_bytes: &[u8],
    policy: &TrustPolicy,
    verifier: &dyn DidWebVerifier,
) -> Result<Option<String>> {
    let domain = parse_did_web_signer(&sig.signer)?;
    if !policy.did_web_domain_trusted(domain) {
        return Ok(None);
    }
    let payload_type = declared_payload_type(sig)?;
    let pae_bytes = construct_dsse_pae(payload_type, payload_bytes);
    let trusted = verifier.verify(DidWebVerificationInput {
        domain,
        key_id: &sig.key_id,
        algorithm: &sig.algorithm,
        signature: &sig.signature,
        payload_type,
        payload_bytes,
        pae_bytes: &pae_bytes,
    })?;
    if trusted {
        return Ok(Some(format!("did-web\0{domain}")));
    }
    Ok(None)
}

fn fetch_rekor_entry(
    sig: &SignatureEntry,
    policy: &TrustPolicy,
    rekor_client: &dyn RekorClient,
) -> Result<RekorEntry> {
    let rekor_url = policy
        .rekor
        .as_ref()
        .map(|rekor| rekor.url.as_str())
        .ok_or_else(|| {
            MdaConfigError::new(
                ErrorCategory::TrustPolicyViolation,
                "Sigstore trusted-runtime requires trustPolicy.rekor.url",
            )
        })?;
    let log_id = sig.rekor_log_id.as_deref().ok_or_else(|| {
        MdaConfigError::with_details(
            ErrorCategory::RekorInclusionFailure,
            "Sigstore signature requires rekor-log-id",
            serde_json::json!({ "signer": sig.signer }),
        )
    })?;
    let log_index = sig.rekor_log_index.ok_or_else(|| {
        MdaConfigError::with_details(
            ErrorCategory::RekorInclusionFailure,
            "Sigstore signature requires rekor-log-index",
            serde_json::json!({ "signer": sig.signer }),
        )
    })?;
    let entry = rekor_client
        .fetch_entry(rekor_url, log_id, log_index)?
        .ok_or_else(|| {
            MdaConfigError::with_details(
                ErrorCategory::RekorInclusionFailure,
                "Rekor entry not found for the supplied log coordinates",
                serde_json::json!({ "logId": log_id, "logIndex": log_index }),
            )
        })?;
    if entry.log_id.as_deref() != Some(log_id)
        || entry.log_index != Some(log_index)
        || entry.inclusion_verified != Some(true)
    {
        return Err(MdaConfigError::with_details(
            ErrorCategory::RekorInclusionFailure,
            "Rekor entry does not bind to signature coordinates",
            serde_json::json!({ "signer": sig.signer }),
        ));
    }
    if entry.kind != "dsse-v0.0.1" {
        return Err(MdaConfigError::with_details(
            ErrorCategory::RekorEntryTypeMismatch,
            "Rekor entry kind is not dsse-v0.0.1",
            serde_json::json!({ "logId": log_id, "logIndex": log_index, "kind": entry.kind }),
        ));
    }
    Ok(entry)
}

fn validate_signature_shape(sig: &SignatureEntry) -> Result<()> {
    declared_payload_type(sig)?;
    if sig.signer.starts_with(SIGSTORE_OIDC_PREFIX) {
        parse_sigstore_signer(&sig.signer)?;
        if sig.rekor_log_id.as_deref().is_none_or(str::is_empty) || sig.rekor_log_index.is_none() {
            return Err(MdaConfigError::with_details(
                ErrorCategory::RekorInclusionFailure,
                "Sigstore signature requires rekor-log-id and rekor-log-index",
                serde_json::json!({ "signer": sig.signer }),
            ));
        }
        return Ok(());
    }
    if sig.signer.starts_with(DID_WEB_PREFIX) {
        parse_did_web_signer(&sig.signer)?;
        if sig.rekor_log_id.is_some() || sig.rekor_log_index.is_some() {
            return Err(MdaConfigError::with_details(
                ErrorCategory::SchemaViolation,
                "did:web signature must not include Rekor fields",
                serde_json::json!({ "signer": sig.signer }),
            ));
        }
        return Ok(());
    }
    Err(MdaConfigError::with_details(
        ErrorCategory::UnknownSignerMethod,
        "unknown signer method",
        serde_json::json!({ "signer": sig.signer }),
    ))
}

fn assert_payload_digest(sig: &SignatureEntry, integrity: &IntegrityField) -> Result<()> {
    if sig.payload_digest == integrity.digest {
        return Ok(());
    }
    Err(MdaConfigError::with_details(
        ErrorCategory::SignatureDigestMismatch,
        "signature payload-digest does not equal integrity.digest",
        serde_json::json!({ "signer": sig.signer }),
    ))
}

fn declared_payload_type(sig: &SignatureEntry) -> Result<&str> {
    let payload_type = sig.payload_type.as_deref().unwrap_or(DEFAULT_PAYLOAD_TYPE);
    if payload_type.contains("+jcs+json")
        || !regex_match(
            r"^application/vnd\.[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+\+json$",
            payload_type,
        )
    {
        return Err(MdaConfigError::with_details(
            ErrorCategory::SchemaViolation,
            "signature payload-type must be application/vnd.<vendor>.<doc-type>+json",
            serde_json::json!({ "signer": sig.signer, "payloadType": payload_type }),
        ));
    }
    Ok(payload_type)
}

fn validate_rekor_dsse_envelope(
    entry: &RekorEntry,
    sig: &SignatureEntry,
    payload_type: &str,
    payload_bytes: &[u8],
) -> Result<()> {
    if entry.dsse_envelope.payload_type != payload_type {
        return rekor_error("Rekor DSSE envelope payloadType mismatch", sig);
    }
    if entry.dsse_envelope.payload != BASE64.encode(payload_bytes) {
        return rekor_error("Rekor DSSE envelope payload mismatch", sig);
    }
    let has_matching_signature = entry.dsse_envelope.signatures.iter().any(|candidate| {
        candidate.sig == sig.signature && candidate.keyid.as_deref() == Some(sig.key_id.as_str())
    });
    if !has_matching_signature {
        return rekor_error("Rekor DSSE envelope does not contain signature/key-id", sig);
    }
    Ok(())
}

fn parse_sigstore_signer(signer: &str) -> Result<&str> {
    if !signer.starts_with(SIGSTORE_OIDC_PREFIX) || signer.contains('#') {
        return Err(MdaConfigError::with_details(
            ErrorCategory::UnknownSignerMethod,
            "Sigstore signer must be sigstore-oidc:<issuer>",
            serde_json::json!({ "signer": signer }),
        ));
    }
    let issuer = &signer[SIGSTORE_OIDC_PREFIX.len()..];
    if issuer.is_empty() {
        return Err(MdaConfigError::with_details(
            ErrorCategory::UnknownSignerMethod,
            "Sigstore signer issuer is empty",
            serde_json::json!({ "signer": signer }),
        ));
    }
    Ok(issuer)
}

fn parse_did_web_signer(signer: &str) -> Result<&str> {
    if !signer.starts_with(DID_WEB_PREFIX) || signer.contains('#') {
        return Err(MdaConfigError::with_details(
            ErrorCategory::UnknownSignerMethod,
            "did:web signer must be did-web:<domain>",
            serde_json::json!({ "signer": signer }),
        ));
    }
    let domain = &signer[DID_WEB_PREFIX.len()..];
    if domain.is_empty() {
        return Err(MdaConfigError::with_details(
            ErrorCategory::UnknownSignerMethod,
            "did:web signer domain is empty",
            serde_json::json!({ "signer": signer }),
        ));
    }
    Ok(domain)
}

fn pae_payload_bytes(integrity: &IntegrityField) -> Result<Vec<u8>> {
    let payload = serde_json::json!({
        "integrity": {
            "algorithm": integrity.algorithm.as_str(),
            "digest": integrity.digest,
        },
    });
    Ok(canonical_json(&payload)?.into_bytes())
}

fn most_specific_candidate_error(errors: &[MdaConfigError]) -> MdaConfigError {
    for category in [
        ErrorCategory::RekorEntryTypeMismatch,
        ErrorCategory::RekorInclusionFailure,
        ErrorCategory::FulcioChainFailure,
        ErrorCategory::SignatureVerificationFailure,
    ] {
        if let Some(err) = errors.iter().find(|err| err.category == category) {
            return MdaConfigError::with_details(
                err.category.clone(),
                err.message.clone(),
                err.details.clone(),
            );
        }
    }
    MdaConfigError::new(
        ErrorCategory::NoTrustedSignature,
        "no cryptographically verified signature matched the trust policy",
    )
}

fn rekor_error<T>(message: &str, sig: &SignatureEntry) -> Result<T> {
    Err(MdaConfigError::with_details(
        ErrorCategory::RekorInclusionFailure,
        message,
        serde_json::json!({ "signer": sig.signer }),
    ))
}

fn policy_error<T>(message: &str) -> Result<T> {
    Err(MdaConfigError::new(
        ErrorCategory::TrustPolicyViolation,
        message,
    ))
}

fn regex_match(pattern: &str, value: &str) -> bool {
    Regex::new(pattern)
        .map(|re| re.is_match(value))
        .unwrap_or(false)
}
