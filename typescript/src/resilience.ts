/**
 * LLMix Resilience Module
 *
 * Circuit breaker, kill switch, singleflight deduplication, and retry with
 * exponential backoff + jitter.
 */

import { createHash } from "node:crypto";
import { statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_FAILURE_THRESHOLD = 3;
const DEFAULT_COOLDOWN_MS = 30_000;
const DEFAULT_BASE_DELAY_MS = 1_000;
const DEFAULT_MAX_DELAY_MS = 30_000;
const DEFAULT_JITTER_MS = 1_000;
const DEFAULT_MAX_RETRY_AFTER_MS = 60_000;
const KILLSWITCH_FILENAME = "killswitch";
const KILLSWITCH_SUBDIR = "llmix2";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isRetryableStatus(statusCode: number): boolean {
  return statusCode === 429 || (statusCode >= 500 && statusCode <= 599);
}

function resolveStateDir(): string {
  const envDir = process.env["LLMIX_STATE_DIR"];
  if (envDir) return envDir;

  const xdg = process.env["XDG_STATE_HOME"];
  if (xdg) return join(xdg, KILLSWITCH_SUBDIR);

  return join(homedir(), ".local", "state", KILLSWITCH_SUBDIR);
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
}

export class CircuitBreaker {
  readonly provider: string;
  readonly baseUrl: string;
  readonly failureThreshold: number;
  readonly cooldownMs: number;

  private _state = CircuitState.CLOSED;
  private _consecutiveFailures = 0;
  private _openedAt = 0;
  private _halfOpenProbeInFlight = false;

  constructor(
    provider: string,
    baseUrl: string,
    options?: CircuitBreakerOptions,
  ) {
    this.provider = provider;
    this.baseUrl = baseUrl;
    this.failureThreshold = options?.failureThreshold ?? DEFAULT_FAILURE_THRESHOLD;
    this.cooldownMs = options?.cooldownMs ?? DEFAULT_COOLDOWN_MS;
  }

  get state(): CircuitState {
    if (this._state === CircuitState.OPEN) {
      const elapsed = Date.now() - this._openedAt;
      if (elapsed >= this.cooldownMs) {
        return CircuitState.HALF_OPEN;
      }
    }
    return this._state;
  }

  check(): void {
    const current = this.state;
    if (current === CircuitState.CLOSED) return;

    if (current === CircuitState.HALF_OPEN) {
      if (this._halfOpenProbeInFlight) {
        throw new CircuitOpenError(this.provider, this.baseUrl);
      }
      this._halfOpenProbeInFlight = true;
      this._state = CircuitState.HALF_OPEN;
      return;
    }

    // OPEN
    throw new CircuitOpenError(this.provider, this.baseUrl);
  }

  onSuccess(): void {
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
    this._halfOpenProbeInFlight = false;
  }

  onFailure(statusCode?: number, networkError = false): void {
    let retryable = networkError;
    if (statusCode !== undefined) {
      // Auth errors do NOT trip the breaker
      if (statusCode === 401 || statusCode === 403) return;
      retryable = retryable || isRetryableStatus(statusCode);
    }

    if (!retryable) return;

    const current = this.state;
    if (current === CircuitState.HALF_OPEN) {
      this._state = CircuitState.OPEN;
      this._openedAt = Date.now();
      this._halfOpenProbeInFlight = false;
      return;
    }

    this._consecutiveFailures++;
    if (this._consecutiveFailures >= this.failureThreshold) {
      this._state = CircuitState.OPEN;
      this._openedAt = Date.now();
    }
  }

  reset(): void {
    this._state = CircuitState.CLOSED;
    this._consecutiveFailures = 0;
    this._openedAt = 0;
    this._halfOpenProbeInFlight = false;
  }
}

// ---------------------------------------------------------------------------
// Kill Switch
// ---------------------------------------------------------------------------

export class KillSwitch {
  readonly path: string;

  constructor(stateDir?: string) {
    const base = stateDir ?? resolveStateDir();
    this.path = join(base, KILLSWITCH_FILENAME);
  }

  check(): void {
    try {
      statSync(this.path);
      throw new KillSwitchActiveError(this.path);
    } catch (err: unknown) {
      if (err instanceof KillSwitchActiveError) throw err;
      // File doesn't exist — all clear
    }
  }

  isActive(): boolean {
    try {
      statSync(this.path);
      return true;
    } catch {
      return false;
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

  const exponential = Math.min(Math.pow(2, attempt) * baseMs, maxDelayMs);
  const jitter = Math.floor(Math.random() * (jitterMs + 1));
  return exponential + jitter;
}

export function parseRetryAfter(
  headerValue: string | undefined | null,
  maxMs = DEFAULT_MAX_RETRY_AFTER_MS,
): number | null {
  if (headerValue == null) return null;
  const seconds = parseInt(headerValue, 10);
  if (isNaN(seconds) || seconds < 0) return null;
  return Math.min(seconds * 1000, maxMs);
}

export function isRetryable(statusCode: number): boolean {
  return isRetryableStatus(statusCode);
}

export interface RetryPolicyOptions {
  maxRetries?: number;
  baseMs?: number;
  maxDelayMs?: number;
  jitterMs?: number;
  maxRetryAfterMs?: number;
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
        const delayMs = this.getDelayMs(attempt);
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
  } catch {
    // Fallback: no-op if proper-lockfile is not installed
    return {
      enabled: true,
      async acquire() { /* proper-lockfile not available */ },
      async release() { /* proper-lockfile not available */ },
    };
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
      } catch {
        // File already exists, that's fine
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
