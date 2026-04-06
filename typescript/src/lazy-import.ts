/**
 * Lazy Import Utility for LLMix
 *
 * Defers heavy SDK imports (AI SDK packages, provider SDKs) to first use
 * via dynamic `import()`. Produces clear error messages when an optional
 * peer dependency is not installed.
 *
 * @example
 * ```typescript
 * const getOpenAI = lazyImport<typeof import("@ai-sdk/openai")>(
 *   "@ai-sdk/openai",
 *   "@ai-sdk/openai",
 * );
 *
 * // First call triggers the real import
 * const { createOpenAI } = await getOpenAI();
 * ```
 */

/** Provider SDK install instructions keyed by package name. */
const PROVIDER_INSTALL_COMMANDS: Record<string, string> = {
  "@ai-sdk/openai": "bun add @ai-sdk/openai",
  "@ai-sdk/anthropic": "bun add @ai-sdk/anthropic",
  "@ai-sdk/google": "bun add @ai-sdk/google",
  ai: "bun add ai",
  ioredis: "bun add ioredis",
};

/**
 * Create an async getter that lazily imports a module on first call.
 *
 * The module is cached after the first successful load so subsequent
 * calls return immediately.
 *
 * @param moduleName - The module specifier passed to `import()`.
 * @param packageName - Human-readable package name for error messages.
 *   Defaults to `moduleName`.
 * @returns An async function that resolves to the module.
 */
export function lazyImport<T>(
  moduleName: string,
  packageName?: string,
): () => Promise<T> {
  const pkg = packageName ?? moduleName;
  let cached: T | undefined;

  return async (): Promise<T> => {
    if (cached !== undefined) {
      return cached;
    }
    try {
      cached = (await import(moduleName)) as T;
      return cached;
    } catch {
      const installCmd =
        PROVIDER_INSTALL_COMMANDS[pkg] ?? `bun add ${pkg}`;
      throw new Error(
        `"${pkg}" is required. Install with: ${installCmd}`,
      );
    }
  };
}
