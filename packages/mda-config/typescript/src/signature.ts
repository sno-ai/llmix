/**
 * MDA v1.0.0-rc.2 §09 — DSSE PAE and trusted signature evaluation.
 */

import { canonify } from "@truestamp/canonify";
import { verify as sigstoreVerify } from "sigstore";
import type { Bundle as SerializedBundle } from "sigstore";
import { Buffer } from "node:buffer";
import { ErrorCategory, MdaConfigError } from "./errors.js";
import type { IntegrityField } from "./integrity.js";
import {
  policyContainsDidWeb,
  sigstoreSubjectsFor,
  trustsDidWebDomain,
  type TrustPolicy,
  validateTrustPolicy,
} from "./trust-policy.js";

/** §09 — one entry of top-level `signatures[]`. */
export interface SignatureEntry {
  signer: string;
  "key-id": string;
  "payload-digest": string;
  algorithm: "ed25519" | "ecdsa-p256" | "rsa-pss-sha256";
  signature: string;
  "rekor-log-id"?: string;
  "rekor-log-index"?: number;
  "payload-type"?: string;
}

/** §09 — default DSSE payload-type when omitted. */
export const DEFAULT_PAYLOAD_TYPE = "application/vnd.mda.integrity+json";

const VENDOR_JSON_PAYLOAD_TYPE =
  /^application\/vnd\.[a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+\+json$/u;

/** Rekor dsse-v0.0.1 entry subset needed by verifier hooks. */
export interface RekorEntry {
  kind: "dsse-v0.0.1" | string;
  logId?: string;
  logIndex?: number;
  inclusionVerified?: boolean;
  certificatePem?: string;
  dsseEnvelope: {
    payloadType: string;
    payload: string;
    signatures: { sig: string; keyid?: string }[];
  };
  rawBundle?: SerializedBundle;
}

/** Rekor lookup hook. It must fetch from the supplied policy Rekor URL. */
export interface RekorClient {
  url?: string;
  rekorUrl?: string;
  fetchEntry(rekorUrl: string, logId: string, logIndex: number): Promise<RekorEntry | null>;
}

/** Sigstore verifier hook for Fulcio chain, inclusion proof, and signature crypto. */
export interface SigstoreVerifier {
  verify(
    entry: RekorEntry,
    signature: SignatureEntry,
    paeBytes: Uint8Array,
  ): Promise<SigstoreVerificationResult>;
}

/** Verified identity returned by the Sigstore verifier hook. */
export interface SigstoreVerificationResult {
  certificatePem?: string;
  issuer?: string;
  subject?: string;
  identity?: {
    issuer?: string;
    subject?: string;
    subjectAlternativeName?: string;
  };
}

/** Input passed to a did:web cryptographic verifier hook. */
export interface DidWebVerificationInput {
  domain: string;
  keyId: string;
  algorithm: SignatureEntry["algorithm"];
  signature: string;
  payloadType: string;
  payloadBytes: Uint8Array;
  paeBytes: Uint8Array;
}

/** did:web verifier hook. Domain matching alone is never sufficient. */
export interface DidWebVerifier {
  verify(input: DidWebVerificationInput): Promise<boolean | { trusted?: boolean }>;
}

export interface SignatureVerificationOptions {
  rekorClient?: RekorClient;
  sigstoreVerifier?: SigstoreVerifier;
  didWebVerifier?: DidWebVerifier;
}

async function verifySigstoreBundle(
  bundle: SerializedBundle,
  payload: Buffer,
): Promise<SigstoreVerificationResult> {
  const signer = await sigstoreVerify(bundle, payload);
  return {
    certificatePem: extractCertFromBundle(bundle),
    identity: {
      issuer: signer.identity?.extensions?.issuer,
      subjectAlternativeName: signer.identity?.subjectAlternativeName,
    },
  };
}

/** §09 — construct DSSE PAE bytes. */
export function constructDssePae(
  payloadType: string,
  payloadBytes: Uint8Array,
): Uint8Array {
  const head = `DSSEv1 ${payloadType.length} ${payloadType} ${payloadBytes.length} `;
  const headBytes = new TextEncoder().encode(head);
  const out = new Uint8Array(headBytes.length + payloadBytes.length);
  out.set(headBytes, 0);
  out.set(payloadBytes, headBytes.length);
  return out;
}

/** §09 — JCS-canonicalize the integrity object for PAE payload bytes. */
export function paePayloadBytes(integrity: IntegrityField): Uint8Array {
  return new TextEncoder().encode(canonify(integrity));
}

/** §13 — evaluate signatures against an RC2 trust policy threshold. */
export async function verifySignatures(
  signatures: SignatureEntry[],
  integrity: IntegrityField,
  policyInput: unknown,
  options: SignatureVerificationOptions,
): Promise<void> {
  const policy = validateTrustPolicy(policyInput);
  assertVerifierHooks(policy, options);
  if (signatures.length === 0) {
    throw new MdaConfigError(
      ErrorCategory.MissingRequiredSignature,
      "trusted-runtime requires a non-empty signatures[] field",
    );
  }

  const payloadBytes = paePayloadBytes(integrity);
  const trusted = new Set<string>();
  const candidateErrors: MdaConfigError[] = [];

  for (const sig of signatures) {
    validateSignatureShape(sig);
    assertPayloadDigest(sig, integrity);
    try {
      const identity = await verifyCandidate(sig, integrity, payloadBytes, policy, options);
      if (identity) trusted.add(identity);
    } catch (cause) {
      candidateErrors.push(asMdaError(cause, ErrorCategory.SignatureVerificationFailure));
    }
  }

  const required = policy.minSignatures ?? 1;
  if (trusted.size >= required) return;
  if (trusted.size > 0) {
    throw new MdaConfigError(
      ErrorCategory.InsufficientTrustedSignatures,
      "trusted signatures did not satisfy minSignatures",
      { trusted: trusted.size, required },
    );
  }
  throw mostSpecificCandidateError(candidateErrors);
}

const defaultSigstoreVerifier: SigstoreVerifier = {
  async verify(entry, _sig, paeBytes) {
    if (!entry.rawBundle) {
      throw new Error("officialSigstoreVerifier requires RekorEntry.rawBundle");
    }
    return await verifySigstoreBundle(entry.rawBundle, Buffer.from(paeBytes));
  },
};

/** Default verifier wrapping the official sigstore npm package. */
export function officialSigstoreVerifier(): SigstoreVerifier {
  return defaultSigstoreVerifier;
}

function assertVerifierHooks(
  policy: TrustPolicy,
  options: SignatureVerificationOptions,
): void {
  if (policy.trustedSigners.some((signer) => signer.type === "sigstore-oidc")) {
    if (!options.rekorClient || !options.sigstoreVerifier) {
      throw new MdaConfigError(
        ErrorCategory.TrustPolicyViolation,
        "Sigstore trusted-runtime requires rekorClient and sigstoreVerifier hooks",
      );
    }
    const clientUrl = options.rekorClient.url ?? options.rekorClient.rekorUrl;
    if (clientUrl && clientUrl !== policy.rekor?.url) {
      throw new MdaConfigError(
        ErrorCategory.TrustPolicyViolation,
        "Rekor client URL does not match trustPolicy.rekor.url",
        { policyUrl: policy.rekor?.url, clientUrl },
      );
    }
  }
  if (policyContainsDidWeb(policy) && !options.didWebVerifier) {
    throw new MdaConfigError(
      ErrorCategory.TrustPolicyViolation,
      "did:web trusted-runtime requires a didWebVerifier hook",
    );
  }
}

async function verifyCandidate(
  sig: SignatureEntry,
  integrity: IntegrityField,
  payloadBytes: Uint8Array,
  policy: TrustPolicy,
  options: SignatureVerificationOptions,
): Promise<string | undefined> {
  if (sig.signer.startsWith("sigstore-oidc:")) {
    return await verifySigstoreCandidate(sig, payloadBytes, policy, options);
  }
  if (sig.signer.startsWith("did-web:")) {
    return await verifyDidWebCandidate(sig, payloadBytes, policy, options);
  }
  throw new MdaConfigError(
    ErrorCategory.UnknownSignerMethod,
    "unknown signer method",
    { signer: sig.signer, digest: integrity.digest },
  );
}

async function verifySigstoreCandidate(
  sig: SignatureEntry,
  payloadBytes: Uint8Array,
  policy: TrustPolicy,
  options: SignatureVerificationOptions,
): Promise<string | undefined> {
  const issuer = parseSigstoreSigner(sig.signer);
  if (sigstoreSubjectsFor(policy, issuer).size === 0) return undefined;
  const payloadType = declaredPayloadType(sig);
  const paeBytes = constructDssePae(payloadType, payloadBytes);
  const entry = await fetchRekorEntry(sig, policy, options);
  validateRekorDsseEnvelope(entry, sig, payloadType, payloadBytes);
  const verified = await options.sigstoreVerifier!.verify(entry, sig, paeBytes);
  const verifiedIssuer = verified.identity?.issuer ?? verified.issuer;
  const verifiedSubject =
    verified.identity?.subject ??
    verified.identity?.subjectAlternativeName ??
    verified.subject;
  if (!verifiedIssuer || !verifiedSubject) {
    throw new MdaConfigError(
      ErrorCategory.SignatureVerificationFailure,
      "Sigstore verifier did not return verified issuer and subject",
      { signer: sig.signer },
    );
  }
  if (verifiedIssuer !== issuer) return undefined;
  if (!sigstoreSubjectsFor(policy, verifiedIssuer).has(verifiedSubject)) return undefined;
  return `sigstore-oidc\0${verifiedIssuer}\0${verifiedSubject}`;
}

async function verifyDidWebCandidate(
  sig: SignatureEntry,
  payloadBytes: Uint8Array,
  policy: TrustPolicy,
  options: SignatureVerificationOptions,
): Promise<string | undefined> {
  const domain = parseDidWebSigner(sig.signer);
  if (!trustsDidWebDomain(policy, domain)) return undefined;
  const payloadType = declaredPayloadType(sig);
  const paeBytes = constructDssePae(payloadType, payloadBytes);
  const result = await options.didWebVerifier!.verify({
    domain,
    keyId: sig["key-id"],
    algorithm: sig.algorithm,
    signature: sig.signature,
    payloadType,
    payloadBytes,
    paeBytes,
  });
  const trusted = typeof result === "boolean" ? result : result.trusted === true;
  if (!trusted) return undefined;
  return `did-web\0${domain}`;
}

async function fetchRekorEntry(
  sig: SignatureEntry,
  policy: TrustPolicy,
  options: SignatureVerificationOptions,
): Promise<RekorEntry> {
  const rekorUrl = policy.rekor?.url;
  if (!rekorUrl) {
    throw new MdaConfigError(
      ErrorCategory.TrustPolicyViolation,
      "Sigstore trusted-runtime requires trustPolicy.rekor.url",
    );
  }
  const logId = sig["rekor-log-id"]!;
  const logIndex = sig["rekor-log-index"]!;
  const entry = await options.rekorClient!.fetchEntry(rekorUrl, logId, logIndex);
  if (!entry) {
    throw new MdaConfigError(
      ErrorCategory.RekorInclusionFailure,
      "Rekor entry not found for the supplied log coordinates",
      { logId, logIndex },
    );
  }
  if (
    entry.logId !== logId ||
    entry.logIndex !== logIndex ||
    entry.inclusionVerified !== true
  ) {
    rekorInclusionFailure("Rekor entry does not bind to signature coordinates", sig);
  }
  if (entry.kind !== "dsse-v0.0.1") {
    throw new MdaConfigError(
      ErrorCategory.RekorEntryTypeMismatch,
      "Rekor entry kind is not dsse-v0.0.1",
      { logId, logIndex, kind: entry.kind },
    );
  }
  return entry;
}

function validateSignatureShape(sig: SignatureEntry): void {
  declaredPayloadType(sig);
  if (sig.signer.startsWith("sigstore-oidc:")) {
    parseSigstoreSigner(sig.signer);
    if (!sig["rekor-log-id"] || sig["rekor-log-index"] === undefined) {
      throw new MdaConfigError(
        ErrorCategory.RekorInclusionFailure,
        "Sigstore signature requires rekor-log-id and rekor-log-index",
        { signer: sig.signer },
      );
    }
    return;
  }
  if (sig.signer.startsWith("did-web:")) {
    parseDidWebSigner(sig.signer);
    if (sig["rekor-log-id"] !== undefined || sig["rekor-log-index"] !== undefined) {
      throw new MdaConfigError(
        ErrorCategory.SchemaViolation,
        "did:web signature must not include Rekor fields",
        { signer: sig.signer },
      );
    }
    return;
  }
  throw new MdaConfigError(
    ErrorCategory.UnknownSignerMethod,
    "unknown signer method",
    { signer: sig.signer },
  );
}

function assertPayloadDigest(sig: SignatureEntry, integrity: IntegrityField): void {
  if (sig["payload-digest"] !== integrity.digest) {
    throw new MdaConfigError(
      ErrorCategory.SignatureDigestMismatch,
      "signature payload-digest does not equal integrity.digest",
      { signer: sig.signer },
    );
  }
}

function parseSigstoreSigner(signer: string): string {
  const prefix = "sigstore-oidc:";
  if (!signer.startsWith(prefix) || signer.includes("#")) {
    throw new MdaConfigError(
      ErrorCategory.UnknownSignerMethod,
      "Sigstore signer must be 'sigstore-oidc:<issuer>' with no subject suffix",
      { signer },
    );
  }
  const issuer = signer.slice(prefix.length);
  if (!issuer) {
    throw new MdaConfigError(
      ErrorCategory.UnknownSignerMethod,
      "Sigstore signer issuer is empty",
      { signer },
    );
  }
  return issuer;
}

function parseDidWebSigner(signer: string): string {
  const prefix = "did-web:";
  if (!signer.startsWith(prefix) || signer.includes("#")) {
    throw new MdaConfigError(
      ErrorCategory.UnknownSignerMethod,
      "did:web signer must be 'did-web:<domain>'",
      { signer },
    );
  }
  const domain = signer.slice(prefix.length);
  if (!domain) {
    throw new MdaConfigError(
      ErrorCategory.UnknownSignerMethod,
      "did:web signer domain is empty",
      { signer },
    );
  }
  return domain;
}

function declaredPayloadType(sig: SignatureEntry): string {
  const payloadType = sig["payload-type"] ?? DEFAULT_PAYLOAD_TYPE;
  if (payloadType.includes("+jcs+json") || !VENDOR_JSON_PAYLOAD_TYPE.test(payloadType)) {
    throw new MdaConfigError(
      ErrorCategory.SchemaViolation,
      "signature payload-type must be application/vnd.<vendor>.<doc-type>+json",
      { signer: sig.signer, payloadType },
    );
  }
  return payloadType;
}

function validateRekorDsseEnvelope(
  entry: RekorEntry,
  sig: SignatureEntry,
  payloadType: string,
  payloadBytes: Uint8Array,
): void {
  if (entry.dsseEnvelope.payloadType !== payloadType) {
    rekorInclusionFailure("Rekor DSSE envelope payloadType mismatch", sig);
  }
  if (entry.dsseEnvelope.payload !== Buffer.from(payloadBytes).toString("base64")) {
    rekorInclusionFailure("Rekor DSSE envelope payload mismatch", sig);
  }
  if (
    !entry.dsseEnvelope.signatures.some(
      (candidate) => candidate.sig === sig.signature && candidate.keyid === sig["key-id"],
    )
  ) {
    rekorInclusionFailure("Rekor DSSE envelope does not contain signature/key-id", sig);
  }
}

function rekorInclusionFailure(message: string, sig: SignatureEntry): never {
  throw new MdaConfigError(
    ErrorCategory.RekorInclusionFailure,
    message,
    { signer: sig.signer },
  );
}

function mostSpecificCandidateError(errors: MdaConfigError[]): MdaConfigError {
  return errors.find((err) => err.category === ErrorCategory.RekorEntryTypeMismatch) ??
    errors.find((err) => err.category === ErrorCategory.RekorInclusionFailure) ??
    errors.find((err) => err.category === ErrorCategory.FulcioChainFailure) ??
    errors.find((err) => err.category === ErrorCategory.SignatureVerificationFailure) ??
    new MdaConfigError(
      ErrorCategory.NoTrustedSignature,
      "no cryptographically verified signature matched the trust policy",
    );
}

function asMdaError(cause: unknown, fallback: ErrorCategory): MdaConfigError {
  if (cause instanceof MdaConfigError) return cause;
  return new MdaConfigError(
    fallback,
    "signature candidate verification failed",
    { cause: cause instanceof Error ? cause.message : String(cause) },
  );
}

function extractCertFromBundle(bundle: SerializedBundle): string {
  const chain = bundle.verificationMaterial?.x509CertificateChain?.certificates;
  if (chain?.[0]?.rawBytes) return derToPem(Buffer.from(chain[0].rawBytes, "base64"));
  const cert = (bundle.verificationMaterial as { certificate?: { rawBytes: string } })
    ?.certificate;
  if (cert?.rawBytes) return derToPem(Buffer.from(cert.rawBytes, "base64"));
  return "";
}

function derToPem(der: Buffer): string {
  const b64 = der.toString("base64");
  const lines = b64.match(/.{1,64}/gu) ?? [b64];
  return `-----BEGIN CERTIFICATE-----\n${lines.join("\n")}\n-----END CERTIFICATE-----\n`;
}
