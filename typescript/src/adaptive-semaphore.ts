/**
 * AIMD Adaptive Semaphore for rate-limit-aware concurrency control.
 *
 * Adjusts concurrency window dynamically:
 * - Additive increase on success (+1)
 * - Multiplicative decrease on 429 (/2)
 * - Preemptive backoff from rate-limit headers
 */

const DEFAULT_INITIAL = 32;
const DEFAULT_MIN_CONCURRENCY = 4;
const HEADER_BACKOFF_THRESHOLD = 0.10;

interface Waiter {
  resolve: () => void;
  reject: (reason: unknown) => void;
}

/**
 * Async semaphore with AIMD concurrency control and header-based early warning.
 *
 * When rate-limit headers are available (OpenAI):
 *   - onHeaderFeedback(remaining, limit): preemptive backoff when remaining < 10%.
 *     Above threshold -> AIMD grow as normal. Below -> scale proportionally.
 * When no headers:
 *   - onSuccess(): window += 1 (additive increase)
 * Always:
 *   - onRateLimit(): window //= 2 (multiplicative decrease)
 */
export class AdaptiveSemaphore {
  private _max: number;
  private _min: number;
  private _window: number;
  private _permits: number;
  private _queue: Waiter[] = [];
  private _hasHeaderSignal = false;
  private _closed = false;

  constructor(
    initial: number = DEFAULT_INITIAL,
    minConcurrency: number = DEFAULT_MIN_CONCURRENCY,
  ) {
    if (initial < 1) throw new Error("initial must be >= 1");
    if (minConcurrency < 1) throw new Error("minConcurrency must be >= 1");
    if (minConcurrency > initial) throw new Error("minConcurrency must be <= initial");
    this._max = initial;
    this._min = minConcurrency;
    this._window = initial;
    this._permits = initial;
  }

  get window(): number {
    return this._window;
  }

  get maxConcurrency(): number {
    return this._max;
  }

  get minConcurrency(): number {
    return this._min;
  }

  async acquire(): Promise<void> {
    if (this._closed) {
      throw new Error("AdaptiveSemaphore is closed");
    }
    if (this._permits > 0) {
      this._permits--;
      return;
    }
    return new Promise<void>((resolve, reject) => {
      this._queue.push({ resolve, reject });
    });
  }

  /**
   * Close the semaphore, rejecting all pending waiters.
   * After close(), acquire() throws immediately.
   */
  close(): void {
    this._closed = true;
    const waiters = this._queue.splice(0);
    for (const w of waiters) {
      w.reject(new Error("AdaptiveSemaphore is closed"));
    }
  }

  get closed(): boolean {
    return this._closed;
  }

  release(): void {
    if (this._queue.length > 0) {
      const waiter = this._queue.shift()!;
      waiter.resolve();
    } else if (this._permits < this._window) {
      this._permits++;
    }
    // else: permit absorbed — window shrank while task was in-flight
  }

  onSuccess(): void {
    if (this._hasHeaderSignal) return;
    if (this._window < this._max) {
      this._window = Math.min(this._window + 1, this._max);
      this._releaseOne();
    }
  }

  onRateLimit(): void {
    const target = Math.max(Math.floor(this._window / 2), this._min);
    this._adjustWindow(target);
  }

  onHeaderFeedback(remaining: number, limit: number): void {
    if (limit <= 0) return;
    this._hasHeaderSignal = true;
    const ratio = remaining / limit;
    if (ratio >= HEADER_BACKOFF_THRESHOLD) {
      if (this._window < this._max) {
        this._window = Math.min(this._window + 1, this._max);
        this._releaseOne();
      }
    } else {
      const scale = ratio / HEADER_BACKOFF_THRESHOLD;
      const target = Math.floor(this._min + scale * (this._max - this._min));
      this._adjustWindow(target);
    }
  }

  private _releaseOne(): void {
    this.release();
  }

  private _adjustWindow(target: number): void {
    target = Math.max(target, this._min);
    target = Math.min(target, this._max);
    if (target === this._window) return;

    if (target > this._window) {
      const grow = target - this._window;
      for (let i = 0; i < grow; i++) {
        this._releaseOne();
      }
    } else {
      const shrink = this._window - target;
      for (let i = 0; i < shrink; i++) {
        if (this._permits > 0) {
          this._permits--;
        }
      }
    }
    this._window = target;
  }
}

/**
 * Extract rate-limit info from OpenAI response headers.
 * Returns { remaining, limit } or null if headers not present/valid.
 */
export function parseOpenAIRatelimitHeaders(
  headers: Record<string, string | undefined>,
): { remaining: number; limit: number } | null {
  const remaining = headers["x-ratelimit-remaining-requests"];
  const limit = headers["x-ratelimit-limit-requests"];
  if (remaining != null && limit != null) {
    const rem = parseInt(remaining, 10);
    const lim = parseInt(limit, 10);
    if (!isNaN(rem) && !isNaN(lim) && lim > 0) {
      return { remaining: rem, limit: lim };
    }
  }
  return null;
}
