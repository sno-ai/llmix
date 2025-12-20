/**
 * LLMix Path Configuration Utilities
 *
 * Provides flexible path resolution with priority:
 * 1. Explicit configDir override
 * 2. Environment variable (LLMIX_CONFIG_DIR)
 * 3. Default path relative to project root (process.cwd())
 */

import path from "node:path"

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
	const envValue = process.env[envVarName]
	if (envValue) {
		return {
			configDir: path.resolve(envValue),
			source: "env",
		}
	}

	// Priority 3: Default relative to project root
	return {
		configDir: path.resolve(projectRoot, defaultRelativePath),
		source: "default",
	}
}
