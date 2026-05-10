import { describe, expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { extractFrontmatter, parseFrontmatterYaml } from "../src/frontmatter.js";
import { ErrorCategory, MdaConfigError } from "../src/errors.js";

const FIX = (rel: string) =>
  resolve(__dirname, "../../../../fixtures/mda", rel);

describe("§02-1.1 frontmatter extraction", () => {
  it("strips UTF-8 BOM (step 1)", async () => {
    const bytes = await readFile(FIX("valid/05-bom-prefixed.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    expect(frontmatterStr).toContain("name: bom-prefixed");
    expect(bodyStr).toContain("# BOM");
  });

  it("normalizes CRLF to LF (step 3)", async () => {
    const bytes = await readFile(FIX("valid/06-crlf-line-endings.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    expect(frontmatterStr).not.toContain("\r");
    expect(bodyStr).not.toContain("\r");
    expect(frontmatterStr).toContain("name: crlf-config");
  });

  it("treats body --- horizontal rules as body content (step 6)", async () => {
    const bytes = await readFile(FIX("valid/07-body-contains-hr.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    expect(frontmatterStr).toContain("name: body-with-hr");
    // body must contain BOTH HR lines
    expect(bodyStr.match(/^---$/gm)?.length ?? 0).toBeGreaterThanOrEqual(2);
  });

  it("accepts an empty body (step 7)", async () => {
    const bytes = await readFile(FIX("valid/08-empty-body.mda"));
    const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
    expect(frontmatterStr).toContain("name: empty-body");
    expect(bodyStr).toBe("");
  });

  it("refuses unterminated frontmatter (step 5)", async () => {
    const bytes = await readFile(FIX("invalid/09-unterminated-frontmatter.mda"));
    expect(() => extractFrontmatter(bytes)).toThrow(MdaConfigError);
    try {
      extractFrontmatter(bytes);
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.UnterminatedFrontmatter);
    }
  });

  it("refuses non-UTF-8 bytes (step 2)", async () => {
    const bytes = await readFile(FIX("invalid/13-non-utf8.mda"));
    try {
      extractFrontmatter(bytes);
      throw new Error("expected throw");
    } catch (e) {
      expect((e as MdaConfigError).category).toBe(ErrorCategory.InvalidEncoding);
    }
  });
});

describe("YAML 1.2 core schema parsing", () => {
  it("rejects YAML with malformed input as frontmatter-yaml-parse-error", () => {
    expect(() => parseFrontmatterYaml('description: "unbalanced')).toThrow(MdaConfigError);
  });

  it("does NOT coerce 'no'/'yes' to booleans (Norway problem)", () => {
    const parsed = parseFrontmatterYaml("network: no\nflag: yes");
    expect(parsed.network).toBe("no");
    expect(parsed.flag).toBe("yes");
  });

  it("returns {} for an empty frontmatter string", () => {
    expect(parseFrontmatterYaml("")).toEqual({});
  });
});
