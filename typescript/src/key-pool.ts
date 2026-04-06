/**
 * Key rotation pool for API key management.
 *
 * Round-robin key selection with dead-key tracking.
 * On 429: rotate() advances to next key.
 * On 401/403: markDead() permanently removes a key from rotation.
 * Single-threaded JS — no lock needed, just atomic index.
 *
 * Env var convention:
 *   {PROVIDER}_KEYS  — comma-separated list (preferred)
 *   {PROVIDER}_API_KEY — single key fallback (pool of 1)
 */

export class KeyPoolExhaustedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "KeyPoolExhaustedError";
  }
}

export class KeyPool {
  private readonly keys: string[];
  private readonly dead: Set<string> = new Set();
  private idx: number = 0;

  constructor(keys: string[]) {
    const cleaned = keys.map((k) => k.trim()).filter((k) => k.length > 0);
    if (cleaned.length === 0) {
      throw new Error("KeyPool requires at least one non-empty key");
    }
    this.keys = cleaned;
  }

  /** Return the current key, skipping dead keys. */
  select(): string {
    if (this.isExhausted()) {
      throw new KeyPoolExhaustedError(
        `All ${this.keys.length} keys are dead (401/403). ` +
          "Replace keys or wait for provider resolution.",
      );
    }
    for (let i = 0; i < this.keys.length; i++) {
      const key = this.keys[this.idx % this.keys.length];
      if (!this.dead.has(key)) {
        return key;
      }
      this.idx = (this.idx + 1) % this.keys.length;
    }
    // Should never reach here if isExhausted() is correct
    throw new KeyPoolExhaustedError("All keys are dead");
  }

  /** Advance to the next key (called on 429). */
  rotate(): void {
    this.idx = (this.idx + 1) % this.keys.length;
  }

  /** Mark a key as permanently failed (called on 401/403). */
  markDead(key: string): void {
    this.dead.add(key);
  }

  /** Return true if all keys are dead. */
  isExhausted(): boolean {
    return this.dead.size >= this.keys.length;
  }

  /** Number of keys still alive. */
  get aliveCount(): number {
    return this.keys.length - this.dead.size;
  }

  /** Total number of keys in the pool. */
  get totalCount(): number {
    return this.keys.length;
  }
}

/**
 * Create a KeyPool from environment variables.
 *
 * Checks {PROVIDER}_KEYS first (comma-separated), then falls back
 * to {PROVIDER}_API_KEY (pool of 1).
 *
 * @param provider - Provider name (e.g. "OPENAI", "ANTHROPIC"). Converted to uppercase.
 * @throws Error if neither env var is set.
 */
export function loadKeysFromEnv(provider: string): KeyPool {
  const providerUpper = provider.toUpperCase();

  // Try {PROVIDER}_KEYS first (comma-separated pool)
  const keysVar = `${providerUpper}_KEYS`;
  const keysRaw = process.env[keysVar] ?? "";
  if (keysRaw.trim()) {
    return new KeyPool(keysRaw.split(","));
  }

  // Fall back to {PROVIDER}_API_KEY (single key)
  const keyVar = `${providerUpper}_API_KEY`;
  const singleKey = process.env[keyVar] ?? "";
  if (singleKey.trim()) {
    return new KeyPool([singleKey]);
  }

  throw new Error(
    `No API keys found for ${providerUpper}. Set ${keysVar} (comma-separated) or ${keyVar}.`,
  );
}
