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
    const cleaned = [...new Set(keys.map((k) => k.trim()).filter((k) => k.length > 0))];
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
    for (let offset = 0; offset < this.keys.length; offset++) {
      const idx = (this.idx + offset) % this.keys.length;
      const key = this.keys[idx] as string;
      if (!this.dead.has(key)) {
        this.idx = idx;
        return key;
      }
    }
    throw new KeyPoolExhaustedError("All keys are dead");
  }

  /** Advance to the next key (called on 429). */
  rotate(): void {
    if (this.isExhausted()) {
      return;
    }
    for (let offset = 1; offset <= this.keys.length; offset++) {
      const idx = (this.idx + offset) % this.keys.length;
      if (!this.dead.has(this.keys[idx] as string)) {
        this.idx = idx;
        return;
      }
    }
  }

  /** Mark a key as permanently failed (called on 401/403). */
  markDead(key: string): void {
    if (this.keys.includes(key)) {
      this.dead.add(key);
    }
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
  // Normalize non-identifier characters (e.g. '-' in "sno-gpu") to '_' so
  // env var names are valid POSIX/Kubernetes identifiers (SNO_GPU_KEYS,
  // SNO_GPU_API_KEY).
  const providerUpper = provider.toUpperCase().replace(/-/g, "_");

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
