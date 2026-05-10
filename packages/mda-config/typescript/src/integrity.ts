/**
 * MDA §08 — integrity canonicalization, hashing, and verification.
 *
 * Canonical bytes recipe (§08-3):
 *   1. Strip top-level `integrity` and `signatures[]` from the frontmatter.
 *   2. Convert the stripped frontmatter to JCS (RFC 8785).
 *   3. Normalize the body string per §08-3.3 (LF endings already handled by
 *      §02-1.1, then strip trailing spaces/tabs per line, then ensure exactly
 *      one terminating "\n" UNLESS the body is empty).
 *   4. Concatenate as: b"---\n" + jcs + b"\n---\n" + normalized_body.
 *
 * Multi-file artifacts (§08-3.4) are out of scope for v1.0 (single-file source-
 * mode loader); the helper accepts only one file.
 */

import { canonify } from "@truestamp/canonify";
import { createHash } from "node:crypto";
import { ErrorCategory, MdaConfigError } from "./errors.js";

/** Shape of the optional top-level `integrity` field (§08-2). */
export interface IntegrityField {
  algorithm: "sha256" | "sha384" | "sha512";
  digest: string;
}

/** §08-3.1 — strip `integrity` and `signatures` from a shallow frontmatter copy. */
function stripSecurityFields(
  frontmatter: Record<string, unknown>,
): Record<string, unknown> {
  const copy = { ...frontmatter };
  delete copy.integrity;
  delete copy.signatures;
  return copy;
}

/** §08-3.3 — normalize the body string for digest computation. */
export function normalizeBody(bodyStr: string): string {
  if (bodyStr === "") return "";
  // §02-1.1 step 3 already handled CRLF/CR → LF; restate per §08-3.3 step 1.
  // §08-3.3 step 2 — strip trailing spaces/tabs from each line.
  const lines = bodyStr.split("\n");
  const stripped = lines.map((l) => l.replace(/[ \t]+$/u, ""));
  // §08-3.3 step 3 — exactly one terminating "\n".
  // After split("\n"), a string ending in "\n" produces an empty last segment.
  // Drop trailing empty segments and re-append exactly one newline.
  while (stripped.length > 0 && stripped[stripped.length - 1] === "") {
    stripped.pop();
  }
  if (stripped.length === 0) return "";
  return stripped.join("\n") + "\n";
}

/** §08-3 — assemble the canonical artifact bytes for a single-file source. */
export function canonicalizeArtifact(
  frontmatter: Record<string, unknown>,
  bodyStr: string,
): Uint8Array {
  // §08-3.1 + §08-3.2 — strip security fields, JCS-canonicalize.
  const stripped = stripSecurityFields(frontmatter);
  const jcs = canonify(stripped);
  // §08-3.3 — normalize body bytes.
  const normalizedBody = normalizeBody(bodyStr);
  // §08-3.3 — concatenation recipe.
  const head = "---\n" + jcs + "\n---\n";
  return new TextEncoder().encode(head + normalizedBody);
}

/** §08-2 — parse a self-describing digest "<algorithm>:<lowercase-hex>". */
export function parseDigest(digest: string): { algorithm: string; hex: string } {
  const idx = digest.indexOf(":");
  if (idx <= 0) {
    throw new MdaConfigError(
      ErrorCategory.SchemaViolation,
      "integrity.digest is not in '<algorithm>:<hex>' form",
      { digest },
    );
  }
  return { algorithm: digest.slice(0, idx), hex: digest.slice(idx + 1) };
}

/** §08-3.5 — hash canonical bytes with the declared algorithm. */
export function hashCanonical(
  canonicalBytes: Uint8Array,
  algorithm: "sha256" | "sha384" | "sha512",
): string {
  return createHash(algorithm).update(canonicalBytes).digest("hex");
}

/** §08-4 — verify the declared `integrity.digest` matches the recomputed hash. */
export function verifyIntegrity(
  frontmatter: Record<string, unknown>,
  bodyStr: string,
  integrity: IntegrityField,
): void {
  const parsed = parseDigest(integrity.digest);
  if (parsed.algorithm !== integrity.algorithm) {
    throw new MdaConfigError(
      ErrorCategory.SchemaViolation,
      "integrity.digest prefix does not match integrity.algorithm",
      { algorithm: integrity.algorithm, digestPrefix: parsed.algorithm },
    );
  }
  const canonical = canonicalizeArtifact(frontmatter, bodyStr);
  const computed = hashCanonical(canonical, integrity.algorithm);
  if (computed !== parsed.hex) {
    throw new MdaConfigError(
      ErrorCategory.IntegrityMismatch,
      "computed digest does not match integrity.digest",
      { expected: parsed.hex, computed, algorithm: integrity.algorithm },
    );
  }
}
