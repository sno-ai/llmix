import { ErrorCategory, MdaConfigError } from "./errors.js";

export interface SigstoreTrustedSigner {
  type: "sigstore-oidc";
  issuer: string;
  subject: string;
}

export interface DidWebTrustedSigner {
  type: "did-web";
  domain: string;
}

export type TrustedSigner = SigstoreTrustedSigner | DidWebTrustedSigner;

export interface TrustPolicy {
  version: 1;
  trustedSigners: readonly TrustedSigner[];
  minSignatures?: number;
  rekor?: { url: string };
}

/** MDA §13 — validate the trust-policy shape before trusted-runtime use. */
export function validateTrustPolicy(input: unknown): TrustPolicy {
  const policy = requireObject(input, "trustPolicy");
  const allowedTop = new Set(["version", "trustedSigners", "minSignatures", "rekor"]);
  rejectUnknownKeys(policy, allowedTop, "trustPolicy");

  if (policy.version !== 1) {
    policyViolation("trustPolicy.version must be 1");
  }
  if (!Array.isArray(policy.trustedSigners) || policy.trustedSigners.length === 0) {
    policyViolation("trustPolicy.trustedSigners must be a non-empty array");
  }
  const minSignatures = policy.minSignatures;
  if (
    minSignatures !== undefined &&
    (!Number.isInteger(minSignatures) || (minSignatures as number) < 1)
  ) {
    policyViolation("trustPolicy.minSignatures must be an integer >= 1");
  }

  const trustedSigners = policy.trustedSigners.map(validateTrustedSigner);
  const hasSigstore = trustedSigners.some((signer) => signer.type === "sigstore-oidc");
  const hasDidWebOnly = trustedSigners.every((signer) => signer.type === "did-web");
  const rekor = validateRekor(policy.rekor);

  if (hasSigstore && !rekor) {
    policyViolation("Sigstore trust policy entries require rekor.url");
  }
  if (hasDidWebOnly && rekor) {
    policyViolation("did-web-only trust policies must not include rekor");
  }

  return {
    version: 1,
    trustedSigners,
    ...(minSignatures === undefined ? {} : { minSignatures: minSignatures as number }),
    ...(rekor ? { rekor } : {}),
  };
}

export function policyContainsDidWeb(policy: TrustPolicy): boolean {
  return policy.trustedSigners.some((signer) => signer.type === "did-web");
}

export function sigstoreSubjectsFor(policy: TrustPolicy, issuer: string): Set<string> {
  return new Set(
    policy.trustedSigners
      .filter((signer): signer is SigstoreTrustedSigner =>
        signer.type === "sigstore-oidc" && signer.issuer === issuer
      )
      .map((signer) => signer.subject),
  );
}

export function trustsDidWebDomain(policy: TrustPolicy, domain: string): boolean {
  return policy.trustedSigners.some(
    (signer) => signer.type === "did-web" && signer.domain === domain,
  );
}

function validateTrustedSigner(input: unknown): TrustedSigner {
  const signer = requireObject(input, "trustedSigners[]");
  if (signer.type === "sigstore-oidc") {
    rejectUnknownKeys(signer, new Set(["type", "issuer", "subject"]), "sigstore signer");
    if (!isNonEmptyString(signer.issuer) || !isNonEmptyString(signer.subject)) {
      policyViolation("Sigstore trusted signer requires non-empty issuer and subject");
    }
    return { type: "sigstore-oidc", issuer: signer.issuer, subject: signer.subject };
  }
  if (signer.type === "did-web") {
    rejectUnknownKeys(signer, new Set(["type", "domain"]), "did-web signer");
    if (!isNonEmptyString(signer.domain)) {
      policyViolation("did-web trusted signer requires non-empty domain");
    }
    return { type: "did-web", domain: signer.domain };
  }
  policyViolation("trusted signer type must be sigstore-oidc or did-web");
}

function validateRekor(input: unknown): { url: string } | undefined {
  if (input === undefined) return undefined;
  const rekor = requireObject(input, "rekor");
  rejectUnknownKeys(rekor, new Set(["url"]), "rekor");
  if (!isNonEmptyString(rekor.url)) {
    policyViolation("rekor.url must be non-empty");
  }
  return { url: rekor.url };
}

function rejectUnknownKeys(
  object: Record<string, unknown>,
  allowed: Set<string>,
  label: string,
): void {
  for (const key of Object.keys(object)) {
    if (!allowed.has(key)) {
      policyViolation(`${label} has unknown field '${key}'`);
    }
  }
}

function requireObject(input: unknown, label: string): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    policyViolation(`${label} must be an object`);
  }
  return input as Record<string, unknown>;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function policyViolation(message: string): never {
  throw new MdaConfigError(ErrorCategory.TrustPolicyViolation, message);
}
