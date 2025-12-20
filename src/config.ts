/**
 * LLMix Path Configuration Utilities
 *
 * Provides flexible path resolution with priority:
 * 1. Explicit configDir override (absolute path)
 * 2. Environment variable (LLMIX_CONFIG_DIR) - resolved relative to PROJECT ROOT
 * 3. Default path relative to project root
 *
 * PROJECT ROOT: Found by walking up from cwd looking for package.json
 */

import { existsSync } from "node:fs"
import path from "node:path"

/**
 * Find project root by walking up directory tree looking for package.json
 * Falls back to process.cwd() if not found
 */
function findProjectRoot(startDir: string = process.cwd()): string {
	let current = path.resolve(startDir)
	const root = path.parse(current).root

	while (current !== root) {
		if (existsSync(path.join(current, "package.json"))) {
			return current
		}
		current = path.dirname(current)
	}

	// Fallback to cwd if no package.json found
	return process.cwd()
}

export interface LLMixPathConfig {
	/** Explicit config directory path (highest priority) */
	configDir?: string
	/** Custom environment variable name (default: LLMIX_CONFIG_DIR) */
	envVar?: string
	/** Default path relative to project root (default: ./config/llm) */
	defaultPath?: string
	/** Project root directory (default: process.cwd()) */
	projectRoot?: string
}

export interface ResolvedConfigDir {
	configDir: string
	source: "explicit" | "env" | "default"
}

/**
 * Resolve the LLMix config directory path
 *
 * @param options - Optional path configuration overrides
 * @returns Resolved absolute path to config directory and source
 *
 * @example
 * ```typescript
 * // In context/llmix-provider.ts
 * const { configDir, source } = resolveConfigDir()
 * // Returns: { configDir: "/path/to/project/config/llm", source: "default" }
 * ```
 */
export function resolveConfigDir(options?: LLMixPathConfig): ResolvedConfigDir {
	const envVarName = options?.envVar ?? "LLMIX_CONFIG_DIR"
	const defaultRelativePath = options?.defaultPath ?? "./config/llm"
	const projectRoot = options?.projectRoot ?? process.cwd()

	// Priority 1: Explicit override
	if (options?.configDir) {
		return {
			configDir: path.resolve(options.configDir),
			source: "explicit",
		}
	}

	// Priority 2: Environment variable
	// LH: Resolve relative paths from PROJECT ROOT (not cwd) to fix HRKG subdirectory issue
	const envValue = process.env[envVarName]
	if (envValue) {
		// If absolute path, use as-is; if relative, resolve from project root
		const resolvedPath = path.isAbsolute(envValue)
			? envValue
			: path.resolve(findProjectRoot(), envValue)
		return {
			configDir: resolvedPath,
			source: "env",
		}
	}

	// Priority 3: Default relative to project root (use findProjectRoot, not cwd)
	const actualProjectRoot = projectRoot === process.cwd() ? findProjectRoot() : projectRoot
	return {
		configDir: path.resolve(actualProjectRoot, defaultRelativePath),
		source: "default",
	}
}
