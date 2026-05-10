import { describe, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  canonicalizeArtifact,
  hashCanonical,
  normalizeBody,
  verifyIntegrity,
} from "../src/integrity.js";
import { extractFrontmatter, parseFrontmatterYaml } from "../src/frontmatter.js";
import { ErrorCategory, MdaConfigError } from "../src/errors.js";

const FIX = (rel: string) =>
  resolve(__dirname, "../../../../fixtures/mda", rel);

describe("§08-3.3 body normalization", () => {
  it("returns empty for empty body (no terminating newline)", () => {
    expect(normalizeBody("")).toBe("");
  });
  it("strips trailing spaces/tabs per line and ensures one terminating newline", () => {
    expect(normalizeBody("foo  \nbar\t\nbaz\n")).toBe("foo\nbar\nbaz\n");
  });
  it("collapses multiple trailing newlines to exactly one", () => {
    expect(normalizeBody("foo\n\n\n")).toBe("foo\n");
  });
});

describe("§08-4 integrity verification", () => {
  it("accepts a fixture whose declared digest matches the canonical bytes", async () => {
    const bytes = await readFile(FIX("valid/02-with-integrity.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    const fm = parseFrontmatterYaml(frontmatterStr);
    const integrity = fm.integrity as { algorithm: "sha256"; digest: string };
    expect(() => verifyIntegrity(fm, bodyStr, integrity)).not.toThrow();
  });

  it("rejects a fixture whose declared digest does NOT match", async () => {
    const bytes = await readFile(FIX("invalid/11-integrity-mismatch.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    const fm = parseFrontmatterYaml(frontmatterStr);
    const integrity = fm.integrity as { algorithm: "sha256"; digest: string };
    try {
      verifyIntegrity(fm, bodyStr, integrity);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.IntegrityMismatch);
    }
  });

  it("canonical bytes are deterministic across reorderings", () => {
    const a = canonicalizeArtifact({ b: 2, a: 1 }, "");
    const b = canonicalizeArtifact({ a: 1, b: 2 }, "");
    expect(hashCanonical(a, "sha256")).toBe(hashCanonical(b, "sha256"));
  });
});
