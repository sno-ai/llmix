/**
 * MDA Registry Client for TypeScript.
 *
 * Type-safe preset loading from local directories with
 * validation and caching.
 *
 * Run with:
 *   bun run examples/mda-config/typescript/registry-client.ts ./fixtures/mda/community/
 */

import { readdir, readFile } from "node:fs/promises";
import { join, extname, basename } from "node:path";
import { parse as parseYaml } from "yaml";

interface PresetFrontmatter {
  name: string;
  version: string;
  provider: "openai" | "anthropic" | "gemini" | "cohere" | "mistral";
  model: string;
  temperature: number;
  max_output_tokens: number;
  description: string;
}

interface ParsedPreset {
  name: string;
  version: string;
  frontmatter: PresetFrontmatter;
  body: string;
  sourcePath: string;
}

interface PipelineConfig {
  provider: string;
  model: string;
  common: { temperature: number; maxOutputTokens: number };
}

class RegistryClient {
  private presets = new Map<string, ParsedPreset>();
  private directory: string;

  constructor(directory: string) {
    this.directory = directory;
  }

  async loadAll(): Promise<void> {
    const files = await readdir(this.directory);
    const mdaFiles = files.filter((f) => extname(f) === ".mda");

    for (const file of mdaFiles) {
      const fullPath = join(this.directory, file);
      const preset = await this.parseFile(fullPath);
      if (preset) {
        this.presets.set(preset.name, preset);
      }
    }
  }

  private async parseFile(path: string): Promise<ParsedPreset | null> {
    const content = await readFile(path, "utf-8");
    const parts = content.split("---");
    if (parts.length < 3) return null;

    try {
      const frontmatter = parseYaml(parts[1]) as PresetFrontmatter;
      return {
        name: frontmatter.name ?? basename(path, ".mda"),
        version: frontmatter.version,
        frontmatter,
        body: parts.slice(2).join("---").trim(),
        sourcePath: path,
      };
    } catch {
      console.warn(`Failed to parse: ${path}`);
      return null;
    }
  }

  get(name: string): PipelineConfig | undefined {
    const preset = this.presets.get(name);
    if (!preset) return undefined;
    return {
      provider: preset.frontmatter.provider,
      model: preset.frontmatter.model,
      common: {
        temperature: preset.frontmatter.temperature,
        maxOutputTokens: preset.frontmatter.max_output_tokens,
      },
    };
  }

  list(): string[] {
    return [...this.presets.keys()];
  }
}

async function main(): Promise<void> {
  const dir = process.argv[2] ?? "./fixtures/mda/community/";
  const registry = new RegistryClient(dir);
  await registry.loadAll();

  console.log(`Loaded presets: ${registry.list().join(", ")}\n`);

  for (const name of registry.list()) {
    const config = registry.get(name)!;
    console.log(`  [${name}] ${config.provider}/${config.model} t=${config.common.temperature}`);
  }
}

main().catch(console.error);
