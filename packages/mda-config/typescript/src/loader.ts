/**
 * MDA §11-2 — canonical loader algorithm (Stages A → G).
 *
 * v1.0 implements the source-mode path only. Stages:
 *   A. Extract frontmatter + body (§02-1.1).
 *   B. Validate against the MDA source-mode JSON Schema (§02).
 *   C. §09-2 cross-field signature/integrity check.
 *   D. (Optional) §08-4 integrity verification.
 *   E. (Optional) §09-4.2 Sigstore signature verification.
 *   F. (Optional) §10-4 `requires.network` enforcement.
 *   G. (Optional) Consumer Zod schema (§11-4).
 */

import { readFile } from "node:fs/promises";
import { Ajv2020 } from "ajv/dist/2020.js";
import type { ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import { ErrorCategory, MdaConfigError } from "./errors.js";
import {
  extractFrontmatter,
  parseFrontmatterYaml,
} from "./frontmatter.js";
import {
  type IntegrityField,
  verifyIntegrity as runIntegrityCheck,
} from "./integrity.js";
import { MDA_SOURCE_SCHEMA } from "./mda-schema.js";
import {
  enforceRequires,
  type RequiresBlock,
  type RequiresEnvironment,
} from "./requires-check.js";
import {
  type DidWebVerifier,
  type RekorClient,
  type SignatureEntry,
  type SigstoreVerifier,
  verifySignatures as runSignatureCheck,
} from "./signature.js";
import { validateTrustPolicy, type TrustPolicy } from "./trust-policy.js";

/** Options accepted by `loadMdaSource()`. */
export interface LoadMdaSourceOptions extends RequiresEnvironment {
  /** Stage D — verify §08 integrity. */
  verifyIntegrity?: boolean;
  /** Stage E — verify §09 signatures. */
  verifySignatures?: boolean;
  /** 13 production trusted-runtime profile. */
  trustedRuntime?: boolean;
  /** Stage F — enforce §10-3.3 `requires.network`. */
  enforceRequires?: boolean;
  /** Operator trust policy (required when `verifySignatures` is true). */
  trustPolicy?: TrustPolicy;
  /** Pluggable Rekor client (required when `verifySignatures` is true). */
  rekorClient?: RekorClient;
  /** Pluggable Sigstore verifier hook (required when `verifySignatures` is true). */
  sigstoreVerifier?: SigstoreVerifier;
  /** Pluggable did:web verifier hook (required when policy trusts did:web). */
  didWebVerifier?: DidWebVerifier;
}

export interface MdaProjectSchema<T> {
  safeParse(data: unknown): MdaProjectSchemaResult<T>;
}

type MdaProjectSchemaResult<T> =
  | { success: true; data: T }
  | { success: false; error: { issues: unknown[] } };

let cachedValidator: ValidateFunction | null = null;
function mdaSourceValidator(): ValidateFunction {
  if (cachedValidator) return cachedValidator;
  // Ajv 2020-12 mode (PRD §6).
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  // ajv-formats default export type varies across CJS/ESM.
  const addFormatsFn = (addFormats as unknown as { default?: (a: unknown) => void }).default ??
    (addFormats as unknown as (a: unknown) => void);
  addFormatsFn(ajv);
  const validator = ajv.compile(MDA_SOURCE_SCHEMA) as ValidateFunction;
  cachedValidator = validator;
  return validator;
}

function hasSignaturesWithoutIntegrity(frontmatter: unknown): boolean {
  if (!frontmatter || typeof frontmatter !== "object" || Array.isArray(frontmatter)) {
    return false;
  }
  const record = frontmatter as Record<string, unknown>;
  return Array.isArray(record.signatures) && record.signatures.length > 0 &&
    record.integrity === undefined;
}

/** MDA §11-2 — load and verify a `.mda` source-mode file through Stages A → G. */
export async function loadMdaSource<T>(
  path: string,
  projectSchema: MdaProjectSchema<T>,
  options: LoadMdaSourceOptions = {},
): Promise<T> {
  const fileBytes = await readFile(path);
  return await loadMdaSourceFromBytes(fileBytes, projectSchema, options);
}

/** MDA §11-2 — same as `loadMdaSource` but operates on raw bytes for tests. */
export async function loadMdaSourceFromBytes<T>(
  fileBytes: Uint8Array,
  projectSchema: MdaProjectSchema<T>,
  options: LoadMdaSourceOptions = {},
): Promise<T> {
  // === Stage A: §02-1.1 extraction ==========================================
  const { frontmatterStr, bodyStr } = extractFrontmatter(fileBytes);
  if (frontmatterStr === "") {
    // Source-mode `.mda` always requires frontmatter (PRD §2; only AGENTS.md
    // outputs admit body-only, and that is the compile target's domain).
    throw new MdaConfigError(
      ErrorCategory.MissingRequiredFrontmatter,
      "source-mode .mda file has no opening '---' fence",
    );
  }
  const frontmatter = parseFrontmatterYaml(frontmatterStr);
  const trustPolicy = options.trustedRuntime || options.verifySignatures
    ? requireValidTrustPolicy(options.trustPolicy)
    : undefined;
  if (hasSignaturesWithoutIntegrity(frontmatter)) {
    throw new MdaConfigError(
      ErrorCategory.SignaturesWithoutIntegrity,
      "signatures[] present without integrity",
    );
  }

  // === Stage B: MDA source-schema structural validation ====================
  const validate = mdaSourceValidator();
  if (!validate(frontmatter)) {
    throw new MdaConfigError(
      ErrorCategory.SchemaViolation,
      "frontmatter failed MDA source-mode JSON Schema validation",
      { errors: validate.errors ?? [] },
    );
  }

  // === Stage C: §09-2 cross-field semantics =================================
  const integrity = frontmatter.integrity as IntegrityField | undefined;
  const signatures = frontmatter.signatures as SignatureEntry[] | undefined;
  if (signatures && signatures.length > 0) {
    if (!integrity) {
      // Schema's dependentRequired catches this too; Stage C states it loud.
      throw new MdaConfigError(
        ErrorCategory.SignaturesWithoutIntegrity,
        "signatures[] present without integrity",
      );
    }
    for (const sig of signatures) {
      if (sig["payload-digest"] !== integrity.digest) {
        throw new MdaConfigError(
          ErrorCategory.SignatureDigestMismatch,
          "signatures[i].payload-digest does not equal integrity.digest",
          { signer: sig.signer, expected: integrity.digest, actual: sig["payload-digest"] },
        );
      }
    }
  }

  if (options.trustedRuntime && !integrity) {
    throw new MdaConfigError(
      ErrorCategory.MissingRequiredIntegrity,
      "trustedRuntime=true requires integrity",
    );
  }

  if ((options.trustedRuntime || options.verifySignatures) && (!signatures || signatures.length === 0)) {
    throw new MdaConfigError(
      options.trustedRuntime
        ? ErrorCategory.MissingRequiredSignature
        : ErrorCategory.SignatureVerificationFailure,
      "signature verification requires a non-empty signatures[] field",
    );
  }

  // === Stage D: §08-4 integrity verification (gated) ========================
  if (options.verifyIntegrity || options.verifySignatures || options.trustedRuntime) {
    if (!integrity) {
      throw new MdaConfigError(
        options.trustedRuntime
          ? ErrorCategory.MissingRequiredIntegrity
          : ErrorCategory.SchemaViolation,
        "integrity is required when verification is enabled",
      );
    }
    runIntegrityCheck(frontmatter, bodyStr, integrity);
  }

  // === Stage E: §09/§13 signature verification (gated) ======================
  if (options.verifySignatures || options.trustedRuntime) {
    if (!integrity) {
      throw new MdaConfigError(
        ErrorCategory.SchemaViolation,
        "integrity is required when signature verification is enabled",
      );
    }
    await runSignatureCheck(signatures ?? [], integrity, trustPolicy!, {
      rekorClient: options.rekorClient,
      sigstoreVerifier: options.sigstoreVerifier,
      didWebVerifier: options.didWebVerifier,
    });
  }

  // === Stage F: §10-4 requires enforcement (gated, source-mode top-level) ==
  if (options.enforceRequires) {
    const requires = frontmatter.requires as RequiresBlock | undefined;
    enforceRequires(requires, { allowedNetworks: options.allowedNetworks });
  }

  // === Stage G: consumer Zod schema (§11-4 layering) ========================
  const result = projectSchema.safeParse(frontmatter);
  if (!result.success) {
    throw new MdaConfigError(
      ErrorCategory.ProjectSchemaViolation,
      "consumer Zod schema rejected the frontmatter",
      { issues: result.error.issues },
    );
  }
  return result.data;
}

function requireValidTrustPolicy(input: unknown): TrustPolicy {
  if (!input || typeof input !== "object" || Object.keys(input).length === 0) {
    throw new MdaConfigError(
      ErrorCategory.TrustPolicyViolation,
      "trusted-runtime requires a valid trustPolicy",
    );
  }
  return validateTrustPolicy(input);
}
