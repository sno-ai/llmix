/**
 * MDA §02-1.1 — normative frontmatter extraction algorithm.
 *
 * Operates on raw file bytes, returns the frontmatter string (to be YAML-parsed)
 * and the body string (to be used by §08 integrity computation and consumer
 * rendering). Identical for `.mda` source and `.md` output files.
 */

import yaml from "js-yaml";
import { ErrorCategory, MdaConfigError } from "./errors.js";

/** Result of §02-1.1 extraction. */
export interface ExtractedFrontmatter {
  /** Raw frontmatter string (between the fences). Empty when no opening fence. */
  frontmatterStr: string;
  /** Body string after the closing fence, normalized to LF line endings. */
  bodyStr: string;
}

/** §02-1.1 — extract frontmatter and body from raw file bytes (UTF-8). */
export function extractFrontmatter(fileBytes: Uint8Array): ExtractedFrontmatter {
  // §02-1.1 step 1 — UTF-8 BOM strip (0xEF 0xBB 0xBF).
  let bytes = fileBytes;
  if (
    bytes.length >= 3 &&
    bytes[0] === 0xef &&
    bytes[1] === 0xbb &&
    bytes[2] === 0xbf
  ) {
    bytes = bytes.subarray(3);
  }

  // §02-1.1 step 2 — UTF-8 decode (fatal on invalid bytes).
  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (cause) {
    throw new MdaConfigError(
      ErrorCategory.InvalidEncoding,
      "file bytes are not valid UTF-8",
      { cause: (cause as Error).message },
    );
  }

  // §02-1.1 step 3 — line-ending normalization (CRLF/CR → LF).
  const normalized = decoded.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  // §02-1.1 step 4 — locate the opening fence at byte offset 0.
  if (!normalized.startsWith("---\n")) {
    return { frontmatterStr: "", bodyStr: normalized };
  }

  // §02-1.1 step 5 — locate the FIRST closing fence (line "---\n" or "---" at EOF).
  // Scan forward line-by-line from the byte after the opening "---\n".
  const afterOpen = 4; // length of "---\n"
  let cursor = afterOpen;
  while (cursor <= normalized.length) {
    const nlIdx = normalized.indexOf("\n", cursor);
    const lineEnd = nlIdx === -1 ? normalized.length : nlIdx;
    const line = normalized.slice(cursor, lineEnd);
    if (line === "---") {
      // §02-1.1 step 5 — frontmatter is the substring strictly between the fences.
      const frontmatterStr = normalized.slice(afterOpen, cursor);
      // Body starts after the closing fence's terminating "\n", or "" at EOF.
      const bodyStart = nlIdx === -1 ? normalized.length : nlIdx + 1;
      const bodyStr = normalized.slice(bodyStart);
      // §02-1.1 step 6 — only the FIRST "---" closes; later "---" stays in body.
      // §02-1.1 step 7 — empty body is conformant.
      return { frontmatterStr, bodyStr };
    }
    if (nlIdx === -1) break;
    cursor = nlIdx + 1;
  }

  throw new MdaConfigError(
    ErrorCategory.UnterminatedFrontmatter,
    "opening '---' fence has no matching closing '---' line",
  );
}

/** §02-1.1 — parse the extracted frontmatter string as YAML 1.2 'core' schema. */
export function parseFrontmatterYaml(frontmatterStr: string): Record<string, unknown> {
  if (frontmatterStr === "") return {};
  let parsed: unknown;
  try {
    // js-yaml CORE_SCHEMA = YAML 1.2 JSON-superset profile (no Norway booleans).
    parsed = yaml.load(frontmatterStr, { schema: yaml.CORE_SCHEMA });
  } catch (cause) {
    throw new MdaConfigError(
      ErrorCategory.FrontmatterYamlParseError,
      "YAML parse failed",
      { cause: (cause as Error).message },
    );
  }
  if (parsed === null || parsed === undefined) return {};
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new MdaConfigError(
      ErrorCategory.FrontmatterYamlParseError,
      "frontmatter MUST parse to a YAML mapping (object), not a scalar or sequence",
    );
  }
  return parsed as Record<string, unknown>;
}
