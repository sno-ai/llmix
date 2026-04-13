/**
 * LLMix Resilience Module
 *
 * Circuit breaker, kill switch, singleflight deduplication, and retry with
 * exponential backoff + jitter.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, renameSync, statSync } from "node:fs";
import { stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_FAILURE_THRESHOLD = 3;
const DEFAULT_COOLDOWN_MS = 30_000;
const DEFAULT_PERMITTED_HALF_OPEN_CALLS = 10;
const DEFAULT_BASE_DELAY_MS = 1_000;
const DEFAULT_MAX_DELAY_MS = 30_000;
const DEFAULT_JITTER_MS = 1_000;
const DEFAULT_MAX_RETRY_AFTER_MS = 60_000;
const KILLSWITCH_FILENAME = "killswitch";
const KILLSWITCH_SUBDIR = "llmix";
const LEGACY_KILLSWITCH_SUBDIR = "llmix2";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isRetryableStatus(statusCode: number): boolean {
  return statusCode === 408 || statusCode === 429 || (statusCode >= 500 && statusCode <= 599);
}

function resolveStateDir(): string {
  const envDir = process.env["LLMIX_STATE_DIR"];
  if (envDir) return envDir;

  const xdg = process.env["XDG_STATE_HOME"];
  if (xdg) return join(xdg, KILLSWITCH_SUBDIR);

  return join(homedir(), ".local", "state", KILLSWITCH_SUBDIR);
}

function resolveLegacyStateDir(currentStateDir: string): string | null {
  const parts = currentStateDir.split(/[/\\]+/);
  if (parts.at(-1) !== KILLSWITCH_SUBDIR) {
    return null;
  }
  return join(currentStateDir, "..", LEGACY_KILLSWITCH_SUBDIR);
}

function migrateLegacyKillSwitch(currentStateDir: string): string {
  const currentPath = join(currentStateDir, KILLSWITCH_FILENAME);
  const legacyStateDir = resolveLegacyStateDir(currentStateDir);
  if (legacyStateDir === null || existsSync(currentPath)) {
    return currentPath;
  }

  const legacyPath = join(legacyStateDir, KILLSWITCH_FILENAME);
  if (!existsSync(legacyPath)) {
    return currentPath;
  }

  mkdirSync(currentStateDir, { recursive: true });
  renameSync(legacyPath, currentPath);
  console.warn(`[llmix] migrated legacy kill switch from ${legacyPath} to ${currentPath}`);
  return currentPath;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class CircuitOpenError extends Error {
  readonly provider: string;
  readonly baseUrl: string;

  constructor(provider: string, baseUrl: string) {
    super(`Circuit breaker OPEN for (${provider}, ${baseUrl})`);
    this.name = "CircuitOpenError";
    this.provider = provider;
    this.baseUrl = baseUrl;
    Object.setPrototypeOf(this, CircuitOpenError.prototype);
  }
}

export class KillSwitchActiveError extends Error {
  readonly path: string;

  constructor(path: string) {
    super(`Kill switch active: ${path} exists. All LLMix calls are blocked.`);
    this.name = "KillSwitchActiveError";
    this.path = path;
    Object.setPrototypeOf(this, KillSwitchActiveError.prototype);
  }
}

// ---------------------------------------------------------------------------
// Circuit Breaker
// ---------------------------------------------------------------------------

export enum CircuitState {
  CLOSED = "CLOSED",
  OPEN = "OPEN",
  HALF_OPEN = "HALF_OPEN",
}

export interface CircuitBreakerOptions {
  failureThreshold?: number;
  cooldownMs?: number;
  permittedHalfOpenCalls?: number;
}

export class CircuitBreaker {
  readonly provider: string;
  readonly baseUrl: string;
  readonly failureThreshold: number;
  readonly permittedHalfOpenCalls: number;
  cooldownMs: number;

  private _state = CircuitState.CLOSED;
  private _consecutiveFailures = 0;
  private _openedAt = 0;
  private _baseCooldownMs: number;
  // HALF_OPEN probe tracking (Resilience4j-style)
  private _halfOpenActive = 0;
  private _halfOpenSuccesses = 0;
  private _halfOpenFailures = 0;

  constructor(
    provider: string,
    baseUrl: string,
    options?: CircuitBreakerOptions,
  ) {
    this.provider = provider;
    this.baseUrl = baseUrl;
    this.failureThreshold = options?.failureThreshold ?? DEFAULT_FAILURE_THRESHOLD;
    this.cooldownMs = options?.cooldownMs ?? DEFAULT_COOLDOWN_MS;
    this._baseCooldownMs = this.cooldownMs;
    this.permittedHalfOpenCalls = options?.permittedHalfOpenCalls ?? DEFAULT_PERMITTED_HALF_OPEN_CALLS;
  }

  get state(): CircuitState {
    if (this._state === CircuitState.OPEN) {
      const elapsed = performance.now() - this._openedAt;
      if (elapsed >= this.cooldownMs) {
        this._state = CircuitState.HALF_OPEN;
        this._halfOpenActive = 0;
        this._halfOpenSuccesses = 0;
        this._halfOpenFailures = 0;
        return CircuitState.HALF_OPEN;
      }
    }
    return this._state;
  }

  check(): void {
    const current = this.state;
    if (current === CircuitState.CLOSED) return;

    if (current === CircuitState.HALF_OPEN) {
      if (this._halfOpenActive >= this.permittedHalfOpenCalls) {
        throw new CircuitOpenError(this.provider, this.baseUrl);
      }
      this._halfOpenActive++;
      return;
    }

    // OPEN
    throw new CircuitOpenError(this.provider, this.baseUrl);
  }

  /**
   * Evaluate HALF_OPEN results once enough probes have completed.
   *
   * Uses a fixed window (permittedHalfOpenCalls) so we wait for the full
   * sample before deciding. If a probe is lost (timeout/crash), the failure
   * path in cancelProbe() counts it as a failure so the window always completes.
   */
  private _evaluateHalfOpen(): void {
    const totalCompleted = this._halfOpenSuccesses + this._halfOpenFailures;
    if (totalCompleted < this.permittedHalfOpenCalls) {
      return; // Need more samples before deciding
    }

    if (this._halfOpenSuccesses > this._halfOpenFailures) {
      // Majority success — service recovered
      this._state = CircuitState.CLOSED;
      this._consecutiveFailures = 0;
      this.cooldownMs = this._baseCooldownMs;
    } else {
      // Majority failures — re-open with exponential backoff (cap 5 min)
      this._state = CircuitState.OPEN;
      this._openedAt = performance.now();
      this.cooldownMs = Math.min(this.cooldownMs * 2, 300_000);
    }
  }

  onSuccess(): void {
    if (this._state === CircuitState.HALF_OPEN) {
      this._halfOpenSuccesses++;
      this._evaluateHalfOpen();
      return;
    }
    // CLOSED state — reset failure counter
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
  }

  onFailure(statusCode?: number, networkError = false): void {
    let retryable = networkError;
    if (statusCode !== undefined) {
      retryable = retryable || isRetryableStatus(statusCode);
    }

    if (this._state === CircuitState.HALF_OPEN) {
      if (retryable || networkError) {
        this._halfOpenFailures++;
      } else {
        // Non-retryable (400, 404) = server is reachable, count as success
        this._halfOpenSuccesses++;
      }
      this._evaluateHalfOpen();
      return;
    }

    // Auth errors do NOT trip the breaker in CLOSED state.
    // Server is reachable → reset the consecutive failure counter.
    if (statusCode !== undefined && (statusCode === 401 || statusCode === 403)) {
      this._consecutiveFailures = 0;
      return;
    }

    if (!retryable) {
      // Non-retryable error proves server is reachable — reset counter
      this._consecutiveFailures = 0;
      return;
    }

    this._consecutiveFailures++;
    if (this._consecutiveFailures >= this.failureThreshold) {
      this._state = CircuitState.OPEN;
      this._openedAt = performance.now();
    }
  }

  /**
   * Cancel an in-flight HALF_OPEN probe without recording success or failure.
   *
   * Safety net for when onSuccess/onFailure were never called (e.g. crash).
   * No-op if the probe was already finalized — prevents double-counting when
   * onFailure() and cancelProbe() both fire for the same probe.
   */
  cancelProbe(): void {
    if (this._state !== CircuitState.HALF_OPEN) return;
    const totalFinalized = this._halfOpenSuccesses + this._halfOpenFailures;
    if (totalFinalized >= this._halfOpenActive) {
      return; // All admitted probes already reported — this is a duplicate
    }
    this._halfOpenFailures++;
    this._evaluateHalfOpen();
  }

  reset(): void {
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
    this._openedAt = 0;
    this._halfOpenActive = 0;
    this._halfOpenSuccesses = 0;
    this._halfOpenFailures = 0;
    this.cooldownMs = this._baseCooldownMs;
  }
}

// ---------------------------------------------------------------------------
// Kill Switch
// ---------------------------------------------------------------------------

export class KillSwitch {
  readonly path: string;

  constructor(stateDir?: string) {
    const base = stateDir ?? resolveStateDir();
    this.path = migrateLegacyKillSwitch(base);
  }

  check(): void {
    try {
      statSync(this.path);
      throw new KillSwitchActiveError(this.path);
    } catch (err: unknown) {
      if (err instanceof KillSwitchActiveError) throw err;
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return;
      throw err; // EACCES, EPERM, etc. — don't silently swallow
    }
  }

  isActive(): boolean {
    try {
      statSync(this.path);
      return true;
    } catch (err: unknown) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return false;
      throw err;
    }
  }

  /** Async check — avoids blocking the event loop with statSync. */
  async checkAsync(): Promise<void> {
    try {
      await stat(this.path);
      throw new KillSwitchActiveError(this.path);
    } catch (err: unknown) {
      if (err instanceof KillSwitchActiveError) throw err;
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return;
      throw err;
    }
  }

  /** Async version of isActive(). */
  async isActiveAsync(): Promise<boolean> {
    try {
      await stat(this.path);
      return true;
    } catch (err: unknown) {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") return false;
      throw err;
    }
  }
}

// ---------------------------------------------------------------------------
// Singleflight
// ---------------------------------------------------------------------------

interface FlightEntry<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

export class Singleflight {
  private _inFlight = new Map<string, FlightEntry<unknown>>();

  static makeKey(data: string): string {
    return createHash("sha256").update(data).digest("hex");
  }

  async do<T>(key: string, fn: () => Promise<T>): Promise<T> {
    const existing = this._inFlight.get(key);
    if (existing) {
      // Safety: FlightEntry is stored as <unknown> because the Map can't be
      // generic per-key.  The cast is safe because callers sharing the same key
      // always produce the same T — the singleflight contract guarantees it.
      return existing.promise as Promise<T>;
    }

    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });

    // Prevent unhandled rejection when no second caller awaits this promise
    promise.catch(() => {});

    const entry: FlightEntry<T> = { promise, resolve, reject };
    this._inFlight.set(key, entry as FlightEntry<unknown>);

    try {
      const result = await fn();
      resolve(result);
      return result;
    } catch (err: unknown) {
      reject(err);
      throw err;
    } finally {
      this._inFlight.delete(key);
    }
  }

  get inFlightCount(): number {
    return this._inFlight.size;
  }
}

// ---------------------------------------------------------------------------
// Retry with Exponential Backoff + Jitter
// ---------------------------------------------------------------------------

export function calculateDelay(
  attempt: number,
  options?: {
    baseMs?: number;
    maxDelayMs?: number;
    jitterMs?: number;
  },
): number {
  const baseMs = options?.baseMs ?? DEFAULT_BASE_DELAY_MS;
  const maxDelayMs = options?.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;
  const jitterMs = options?.jitterMs ?? DEFAULT_JITTER_MS;

  const exponential = Math.min(2 ** attempt * baseMs, maxDelayMs);
  const jitter = Math.floor(Math.random() * (jitterMs + 1));
  return exponential + jitter;
}

export function parseRetryAfter(
  headerValue: string | undefined | null,
  maxMs = DEFAULT_MAX_RETRY_AFTER_MS,
): number | null {
  if (headerValue == null) return null;
  const trimmed = headerValue.trim();
  if (/^\d+$/.test(trimmed)) {
    const seconds = parseInt(trimmed, 10);
    return Math.min(seconds * 1000, maxMs);
  }

  // Fallback: try HTTP-date format (RFC 7231 §7.1.1.1)
  const date = new Date(headerValue);
  if (!isNaN(date.getTime())) {
    const deltaMs = date.getTime() - Date.now();
    if (deltaMs > 0) return Math.min(deltaMs, maxMs);
  }

  return null;
}

export function isRetryable(statusCode: number): boolean {
  return isRetryableStatus(statusCode);
}

export interface RetryPolicyOptions {
  maxRetries?: number | undefined;
  baseMs?: number | undefined;
  maxDelayMs?: number | undefined;
  jitterMs?: number | undefined;
  maxRetryAfterMs?: number | undefined;
}

export class RetryPolicy {
  readonly maxRetries: number;
  readonly baseMs: number;
  readonly maxDelayMs: number;
  readonly jitterMs: number;
  readonly maxRetryAfterMs: number;

  constructor(options?: RetryPolicyOptions) {
    this.maxRetries = options?.maxRetries ?? 3;
    this.baseMs = options?.baseMs ?? DEFAULT_BASE_DELAY_MS;
    this.maxDelayMs = options?.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;
    this.jitterMs = options?.jitterMs ?? DEFAULT_JITTER_MS;
    this.maxRetryAfterMs = options?.maxRetryAfterMs ?? DEFAULT_MAX_RETRY_AFTER_MS;

    if (!Number.isInteger(this.maxRetries) || this.maxRetries < 0) {
      throw new RangeError(`maxRetries must be a non-negative integer, got ${this.maxRetries}`);
    }
    if (!Number.isFinite(this.baseMs) || this.baseMs < 0) {
      throw new RangeError(`baseMs must be a non-negative finite number, got ${this.baseMs}`);
    }
    if (!Number.isFinite(this.maxDelayMs) || this.maxDelayMs < 0) {
      throw new RangeError(`maxDelayMs must be a non-negative finite number, got ${this.maxDelayMs}`);
    }
    if (!Number.isFinite(this.jitterMs) || this.jitterMs < 0) {
      throw new RangeError(`jitterMs must be a non-negative finite number, got ${this.jitterMs}`);
    }
    if (!Number.isFinite(this.maxRetryAfterMs) || this.maxRetryAfterMs < 0) {
      throw new RangeError(`maxRetryAfterMs must be a non-negative finite number, got ${this.maxRetryAfterMs}`);
    }
  }

  getDelayMs(attempt: number, retryAfterHeader?: string | null): number {
    const retryAfter = parseRetryAfter(retryAfterHeader, this.maxRetryAfterMs);
    if (retryAfter !== null) return retryAfter;

    return calculateDelay(attempt, {
      baseMs: this.baseMs,
      maxDelayMs: this.maxDelayMs,
      jitterMs: this.jitterMs,
    });
  }

  async execute<T>(
    fn: () => Promise<T>,
    isRetryableFn?: (err: unknown) => boolean,
  ): Promise<T> {
    let lastError: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        return await fn();
      } catch (err: unknown) {
        lastError = err;
        if (attempt >= this.maxRetries) throw err;
        if (isRetryableFn && !isRetryableFn(err)) throw err;
        const retryAfterHeader = (err as { headers?: Record<string, string> }).headers?.["retry-after"] ?? null;
        const delayMs = this.getDelayMs(attempt, retryAfterHeader);
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
    throw lastError;
  }
}

// ---------------------------------------------------------------------------
// Cross-Process File Lock (opt-in via LLM_GLOBAL_CONCURRENCY)
//
// Uses `proper-lockfile` npm package when available.
// No-op when LLM_GLOBAL_CONCURRENCY env var is not set.
// ---------------------------------------------------------------------------

export interface FileLockLike {
  acquire(): Promise<void>;
  release(): Promise<void>;
  readonly enabled: boolean;
}

export async function createFileLock(
  lockPath?: string,
): Promise<FileLockLike> {
  const concurrency = process.env["LLM_GLOBAL_CONCURRENCY"];
  const enabled = concurrency !== undefined && concurrency.trim() !== "";

  if (enabled && (!/^\d+$/.test(concurrency.trim()) || parseInt(concurrency.trim(), 10) < 1)) {
    throw new RangeError(
      `LLM_GLOBAL_CONCURRENCY must be a positive integer, got "${concurrency}"`,
    );
  }

  if (!enabled) {
    return {
      enabled: false,
      async acquire() { /* no-op */ },
      async release() { /* no-op */ },
    };
  }

  const base = resolveStateDir();
  const resolvedPath = lockPath ?? join(base, "llmix.lock");

  // Dynamic import so proper-lockfile is only loaded when needed
  let lockfileMod: { lock: (path: string, options?: Record<string, unknown>) => Promise<() => Promise<void>> };
  try {
    lockfileMod = await import("proper-lockfile") as typeof lockfileMod;
  } catch (cause: unknown) {
    throw new Error(
      "LLMix: LLM_GLOBAL_CONCURRENCY is set but 'proper-lockfile' could not be loaded. " +
      "Install with: bun add proper-lockfile",
      { cause },
    );
  }

  let releaseFn: (() => Promise<void>) | null = null;

  return {
    enabled: true,
    async acquire() {
      // Ensure parent directory exists
      const { mkdir, writeFile } = await import("node:fs/promises");
      const { dirname } = await import("node:path");
      await mkdir(dirname(resolvedPath), { recursive: true });
      // proper-lockfile needs the file to exist
      try {
        await writeFile(resolvedPath, "", { flag: "wx" });
      } catch (err: unknown) {
        if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err;
      }
      releaseFn = await lockfileMod.lock(resolvedPath, { retries: { retries: 10, minTimeout: 100 } });
    },
    async release() {
      if (releaseFn) {
        await releaseFn();
        releaseFn = null;
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Re-export helpers
// ---------------------------------------------------------------------------

export { resolveStateDir };
