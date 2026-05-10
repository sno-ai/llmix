/**
 * Helper: compute the §08 canonical sha256 digest of a (frontmatter, body) pair.
 * Used to bake fixtures and to dogfood the canonicalization helper.
 *
 * Usage: bun run scripts/compute-digest.ts <path>
 *
 * Reads the file, extracts frontmatter via §02-1.1, parses YAML, and prints
 * the digest the file would have to declare for §08-4 verification to succeed.
 */

import { readFile } from "node:fs/promises";
import { extractFrontmatter, parseFrontmatterYaml } from "../src/frontmatter.js";
import { canonicalizeArtifact, hashCanonical } from "../src/integrity.js";

async function main(): Promise<void> {
  const path = process.argv[2];
  if (!path) {
    process.stderr.write("usage: compute-digest <path>\n");
    process.exit(2);
  }
  const bytes = await readFile(path);
  const { frontmatterStr, bodyStr } = extractFrontmatter(bytes);
  const fm = parseFrontmatterYaml(frontmatterStr);
  const canonical = canonicalizeArtifact(fm, bodyStr);
  const hex = hashCanonical(canonical, "sha256");
  process.stdout.write(`sha256:${hex}\n`);
}

main().catch((err) => {
  process.stderr.write(`${(err as Error).stack ?? String(err)}\n`);
  process.exit(1);
});
