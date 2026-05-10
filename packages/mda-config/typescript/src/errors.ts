/**
 * Error vocabulary for the MDA loader (mirrors MDA spec §11-3).
 *
 * Categories are stable strings; downstream observability MAY pivot on them.
 * Implementations MAY add their own categories; consumers SHOULD recognize at
 * least the values in this enum.
 */

/** §11-3 — recommended error category vocabulary. */
export enum ErrorCategory {
  // §02-1.1 extraction
  InvalidEncoding = "invalid-encoding",
  UnterminatedFrontmatter = "unterminated-frontmatter",
  MissingRequiredFrontmatter = "missing-required-frontmatter",
  FrontmatterYamlParseError = "frontmatter-yaml-parse-error",

  // §02 schema
  SchemaViolation = "schema-violation",

  // §09-2 cross-field
  SignatureDigestMismatch = "signature-digest-mismatch",
  SignaturesWithoutIntegrity = "signatures-without-integrity",

  // §13 trusted-runtime required inputs
  MissingRequiredIntegrity = "missing-required-integrity",
  MissingRequiredSignature = "missing-required-signature",

  // §08-4 integrity
  IntegrityMismatch = "integrity-mismatch",

  // §09-4.2 signatures
  RekorEntryTypeMismatch = "rekor-entry-type-mismatch",
  RekorInclusionFailure = "rekor-inclusion-failure",
  FulcioChainFailure = "fulcio-chain-failure",
  SignatureVerificationFailure = "signature-verification-failure",
  NoTrustedSignature = "no-trusted-signature",
  InsufficientTrustedSignatures = "insufficient-trusted-signatures",
  TrustPolicyViolation = "trust-policy-violation",
  UnknownSignerMethod = "unknown-signer-method",

  // §10-4 capabilities
  RequiresNotSatisfied = "requires-not-satisfied",

  // Stage G — out of MDA scope
  ProjectSchemaViolation = "project-schema-violation",
}

/** Structured error thrown by every public function in this package (§11-3). */
export class MdaConfigError extends Error {
  public readonly category: ErrorCategory;
  public readonly details: Record<string, unknown>;

  constructor(
    category: ErrorCategory,
    message: string,
    details: Record<string, unknown> = {},
  ) {
    super(`[${category}] ${message}`);
    this.name = "MdaConfigError";
    this.category = category;
    this.details = details;
  }
}
