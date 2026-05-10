/**
 * `@snoai/mda-config` — MDA v1.0 source-mode loader (TypeScript).
 *
 * Public surface:
 *   - `loadMdaSource(path, zodSchema, options)` — §11-2 canonical loader.
 *   - `verifyIntegrity(frontmatter, body, integrity)` — §08-4.
 *   - `verifySignatures(signatures, integrity, policy, deps)` — §09-4.2.
 *   - `enforceRequires(requires, env)` — §10-4 (network-only in v1.0).
 *   - `MdaConfigError` + `ErrorCategory` — §11-3 vocabulary.
 */

export {
  loadMdaSource,
  loadMdaSourceFromBytes,
  type LoadMdaSourceOptions,
  type MdaProjectSchema,
} from "./loader.js";
export { verifyIntegrity, type IntegrityField } from "./integrity.js";
export {
  verifySignatures,
  constructDssePae,
  DEFAULT_PAYLOAD_TYPE,
  officialSigstoreVerifier,
  type SignatureEntry,
  type RekorClient,
  type RekorEntry,
  type SigstoreVerifier,
  type DidWebVerifier,
  type DidWebVerificationInput,
  type SigstoreVerificationResult,
} from "./signature.js";
export {
  validateTrustPolicy,
  type TrustPolicy,
  type TrustedSigner,
  type SigstoreTrustedSigner,
  type DidWebTrustedSigner,
} from "./trust-policy.js";
export {
  enforceRequires,
  type RequiresBlock,
  type NetworkRequirement,
  type RequiresEnvironment,
} from "./requires-check.js";
export { MdaConfigError, ErrorCategory } from "./errors.js";
export {
  extractFrontmatter,
  parseFrontmatterYaml,
  type ExtractedFrontmatter,
} from "./frontmatter.js";
