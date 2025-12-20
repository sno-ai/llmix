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

import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

/** Lockfiles that indicate project root (any package manager) */
const LOCKFILES = ["bun.lock", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"]

/**
 * Check if a package.json file indicates a monorepo root (has workspaces field)
 */
function isMonorepoRoot(packageJsonPath: string): boolean {
	try {
		const content = readFileSync(packageJsonPath, "utf-8")
		const pkg = JSON.parse(content)
		return Array.isArray(pkg.workspaces) || typeof pkg.workspaces === "object"
	} catch {
		return false
	}
}

/**
 * Check if directory contains a lockfile (final fallback for project root detection)
 */
function hasLockfile(dir: string): boolean {
	return LOCKFILES.some((f) => existsSync(path.join(dir, f)))
}

/**
 * Find project root by walking up directory tree.
 * Priority (two-pass to ensure workspaces wins over lockfiles in subdirs):
 * 1. package.json with "workspaces" field (monorepo root) - checked first across ALL dirs
 * 2. Lockfile (bun.lock, pnpm-lock.yaml, yarn.lock, package-lock.json)
 * 3. First package.json found
 * 4. process.cwd()
 */
function findProjectRoot(startDir: string = process.cwd()): string {
	let current = path.resolve(startDir)
	const root = path.parse(current).root
	let firstPackageJson: string | null = null
	let firstLockfileDir: string | null = null

	// First pass: look for workspaces (highest priority) and track fallbacks
	while (current !== root) {
		const packageJsonPath = path.join(current, "package.json")

		// Priority 1: package.json with workspaces - return immediately
		if (existsSync(packageJsonPath) && isMonorepoRoot(packageJsonPath)) {
			return current
		}

		// Track first lockfile dir as fallback
		if (!firstLockfileDir && hasLockfile(current)) {
			firstLockfileDir = current
		}

		// Track first package.json as fallback
		if (!firstPackageJson && existsSync(packageJsonPath)) {
			firstPackageJson = current
		}

		current = path.dirname(current)
	}

	// Fallback priority: lockfile > package.json > cwd
	return firstLockfileDir ?? firstPackageJson ?? process.cwd()
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

	// Priority 2: Environment variable - always resolve from project root
	const envValue = process.env[envVarName]
	if (envValue) {
		return {
			configDir: path.resolve(findProjectRoot(), envValue),
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
