/**
 * LLMix v2 Call Pipeline
 *
 * Orchestrates the 19-step call flow using feature modules:
 *   1. Kill Switch → 2. Config → 3-4. Cache → 5. Circuit Breaker →
 *   6. Singleflight → [7-16 Retry Loop] → 17. Thinking strip →
 *   18. Cache write → 19. Telemetry
 *
 * The provider dispatch (step 11) is a caller-supplied callback.
 * This module is pure orchestration — no AI SDK dependency.
 */

import { type StripThinkingResult, stripThinking } from "./thinking";
import { type TransformKwargsCallback, applyTransformKwargs, PROVIDER_KWARGS_REGISTRY } from "./provider-kwargs";
import { AdaptiveSemaphore, parseOpenAIRatelimitHeaders } from "./adaptive-semaphore";
import { KeyPool } from "./key-pool";
import {
  CircuitBreaker,
  CircuitOpenError,
  KillSwitch,
  type FileLockLike,
  RetryPolicy,
  Singleflight,
  createFileLock,
  isRetryable,
} from "./resilience";
import type {
  LLMConfig,
  LLMUsage,
  ProviderOptions,
} from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Minimal error shape with optional HTTP status and headers. */
export interface ProviderError extends Error {
  statusCode?: number;
  headers?: Record<string, string | undefined>;
}

/** Result returned from the provider dispatch callback. */
export interface ProviderResult {
  content: string;
  model: string;
  usage: LLMUsage;
  headers?: Record<string, string | undefined>;
}

/** Context passed into the provider dispatch callback. */
export interface DispatchContext {
  provider: string;
  model: string;
  apiKey: string;
  messages: unknown[];
  kwargs: Record<string, unknown>;
  config: LLMConfig;
}

/** The function that actually calls the LLM provider (injected). */
export type ProviderDispatchFn = (ctx: DispatchContext) => Promise<ProviderResult>;

/** Input for a single call through the pipeline. */
export interface V2CallInput {
  config: LLMConfig;
  messages: unknown[];
  singleflightKey?: string;
}

/** Full response from the v2 pipeline. */
export interface V2CallResponse {
  content: string;
  model: string;
  provider: string;
  usage: LLMUsage;
  success: boolean;
  error?: string;
  thinkingContent?: string;
}

/** Configuration for the v2 pipeline. */
export interface V2PipelineConfig {
  dispatch: ProviderDispatchFn;
  maxRetries?: number;
  retryBaseMs?: number;
  retryMaxDelayMs?: number;
  circuitBreakerThreshold?: number;
  circuitBreakerCooldownMs?: number;
  semaphoreInitial?: number;
  semaphoreMin?: number;
  killSwitchStateDir?: string;
  transformKwargsOverrides?: Record<string, TransformKwargsCallback>;
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export class V2CallPipeline {
  private readonly dispatch: ProviderDispatchFn;
  private readonly killSwitch: KillSwitch;
  private readonly singleflight = new Singleflight();
  private readonly retryPolicy: RetryPolicy;

  private readonly circuitBreakers = new Map<string, CircuitBreaker>();
  private readonly semaphores = new Map<string, AdaptiveSemaphore>();
  private readonly keyPools = new Map<string, KeyPool>();
  private readonly transformKwargs: Record<string, TransformKwargsCallback>;

  private readonly cbThreshold: number;
  private readonly cbCooldownMs: number;
  private readonly semInitial: number;
  private readonly semMin: number;

  private fileLock: FileLockLike | null = null;

  constructor(config: V2PipelineConfig) {
    this.dispatch = config.dispatch;
    this.killSwitch = new KillSwitch(config.killSwitchStateDir);
    this.retryPolicy = new RetryPolicy({
      maxRetries: config.maxRetries ?? 3,
      baseMs: config.retryBaseMs,
      maxDelayMs: config.retryMaxDelayMs,
    });
    this.cbThreshold = config.circuitBreakerThreshold ?? 3;
    this.cbCooldownMs = config.circuitBreakerCooldownMs ?? 30_000;
    this.semInitial = config.semaphoreInitial ?? 32;
    this.semMin = config.semaphoreMin ?? 4;
    this.transformKwargs = {
      ...PROVIDER_KWARGS_REGISTRY,
      ...(config.transformKwargsOverrides ?? {}),
    };
  }

  /** Register a key pool for a provider. */
  setKeyPool(provider: string, pool: KeyPool): void {
    this.keyPools.set(provider, pool);
  }

  /** Get or create a circuit breaker for a provider+baseUrl pair. */
  private getCircuitBreaker(provider: string, baseUrl: string): CircuitBreaker {
    const key = `${provider}:${baseUrl}`;
    let cb = this.circuitBreakers.get(key);
    if (!cb) {
      cb = new CircuitBreaker(provider, baseUrl, {
        failureThreshold: this.cbThreshold,
        cooldownMs: this.cbCooldownMs,
      });
      this.circuitBreakers.set(key, cb);
    }
    return cb;
  }

  /** Get or create an adaptive semaphore for a provider. */
  private getSemaphore(provider: string): AdaptiveSemaphore {
    let sem = this.semaphores.get(provider);
    if (!sem) {
      sem = new AdaptiveSemaphore(this.semInitial, this.semMin);
      this.semaphores.set(provider, sem);
    }
    return sem;
  }

  /** Get or lazy-init the cross-process file lock. */
  private async getFileLock(): Promise<FileLockLike> {
    if (!this.fileLock) {
      this.fileLock = await createFileLock();
    }
    return this.fileLock;
  }

  /**
   * Execute the 19-step call flow.
   */
  async call(input: V2CallInput): Promise<V2CallResponse> {
    const { config, messages } = input;
    const provider = config.provider;
    const model = config.model;
    const baseUrl = ""; // base URL resolved at provider level

    try {
      // Step 1: Kill Switch
      await this.killSwitch.checkAsync();

      // Step 2: Config — already resolved by caller (input.config)

      // Steps 3-4: Cache lookup (L1/L2) — placeholder for future integration
      // TODO: integrate LRU + Redis cache check

      // Step 5: Circuit Breaker check
      const cb = this.getCircuitBreaker(provider, baseUrl);
      cb.check();

      // Step 6: Singleflight deduplication
      const sfKey = input.singleflightKey ?? Singleflight.makeKey(
        JSON.stringify({ provider, model, messages }),
      );

      const result = await this.singleflight.do(sfKey, async () => {
        // Steps 7-16: Retry loop
        return this.retryPolicy.execute(
          () => this.executeRetryBody(config, messages, cb),
          (err) => this.isRetryableError(err),
        );
      });

      // Step 17: Thinking stripping
      const { content, thinkingContent } = this.applyThinkingStrip(
        result.content,
        config,
      );

      // Step 18: Cache write — placeholder
      // TODO: write raw result to L1/L2 cache

      // Step 19: Telemetry — placeholder
      // TODO: emit telemetry event

      return {
        content,
        model: result.model,
        provider,
        usage: result.usage,
        success: true,
        thinkingContent: thinkingContent ?? undefined,
      };
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      return {
        content: "",
        model,
        provider,
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        success: false,
        error: errorMessage,
      };
    }
  }

  /** Steps 7-14 inside the retry loop. */
  private async executeRetryBody(
    config: LLMConfig,
    messages: unknown[],
    cb: CircuitBreaker,
  ): Promise<ProviderResult> {
    const provider = config.provider;
    const semaphore = this.getSemaphore(provider);
    const lock = await this.getFileLock();

    // Step 8: AIMD Semaphore acquire (before lock to avoid convoy/deadlock)
    await semaphore.acquire();
    try {
      // Step 7: Cross-process lock acquire (protects state reads only)
      await lock.acquire();

      let apiKey: string;
      let kwargs: Record<string, unknown>;
      const pool = this.keyPools.get(provider);

      try {
        // Step 9: Key Pool select (under lock)
        if (!pool) {
          throw new Error(
            `No API key pool for provider "${provider}". Set ${provider.toUpperCase()}_API_KEY or ${provider.toUpperCase()}_KEYS.`,
          );
        }
        apiKey = pool.select();

        // Step 5 (re-check): Circuit breaker check (under lock)
        cb.check();

        // Step 10: Provider kwargs transform
        kwargs = {
          temperature: config.common?.temperature,
          top_p: config.common?.topP,
          max_tokens: config.common?.maxOutputTokens,
        };
        const transformFn = this.transformKwargs[provider];
        if (transformFn) {
          kwargs = applyTransformKwargs(
            {
              model: config.model,
              provider,
              messages: messages as unknown[],
              temperature: config.common?.temperature,
              topP: config.common?.topP,
              providerOptions: config.providerOptions as ProviderOptions | undefined,
            },
            kwargs,
            transformFn,
          );
        }
      } finally {
        // Release cross-process lock before network call
        await lock.release();
      }

      // Step 11: Provider dispatch (outside lock scope)
      const result = await this.dispatch({
        provider,
        model: config.model,
        apiKey,
        messages,
        kwargs,
        config,
      });

      // Step 12: AIMD feedback (success path)
      if (result.headers) {
        const rlHeaders = parseOpenAIRatelimitHeaders(
          result.headers as Record<string, string | undefined>,
        );
        if (rlHeaders) {
          semaphore.onHeaderFeedback(rlHeaders.remaining, rlHeaders.limit);
        } else {
          semaphore.onSuccess();
        }
      } else {
        semaphore.onSuccess();
      }

      // Step 14: Key Pool feedback (success — no action needed)

      // Step 15: Circuit Breaker feedback (success)
      cb.onSuccess();

      return result;
    } catch (err: unknown) {
      // Step 12: AIMD feedback (error path)
      const statusCode = (err as ProviderError).statusCode;
      if (statusCode === 429) {
        semaphore.onRateLimit();
      }

      // Step 14: Key Pool feedback (error)
      const pool = this.keyPools.get(provider);
      if (pool && statusCode !== undefined) {
        // apiKey may not be defined if error occurred before pool.select()
        const errApiKey = (err as { _llmixApiKey?: string })._llmixApiKey;
        if (errApiKey) {
          if (statusCode === 401 || statusCode === 403) {
            pool.markDead(errApiKey);
          } else if (statusCode === 429) {
            pool.rotate();
          }
        }
      }

      // Step 15: Circuit Breaker feedback (error)
      cb.onFailure(
        statusCode,
        !(err instanceof CircuitOpenError) && statusCode === undefined,
      );

      throw err;
    } finally {
      // Step 12: AIMD semaphore release (always)
      semaphore.release();
    }
  }

  /** Step 16: Determine if an error is retryable. */
  private isRetryableError(err: unknown): boolean {
    if (err instanceof CircuitOpenError) return false;
    const statusCode = (err as ProviderError).statusCode;
    if (statusCode !== undefined) return isRetryable(statusCode);
    // Network errors are retryable
    return true;
  }

  /** Step 17: Apply thinking token stripping based on config. */
  private applyThinkingStrip(
    content: string,
    config: LLMConfig,
  ): StripThinkingResult {
    if (config.common?.keepThinkingOutput) {
      return { content, thinkingContent: null };
    }
    return stripThinking(content);
  }

  /** Release all semaphores and clean up resources. */
  close(): void {
    for (const sem of this.semaphores.values()) {
      sem.close();
    }
  }

  // -------------------------------------------------------------------------
  // Introspection (for testing)
  // -------------------------------------------------------------------------

  getCircuitBreakerState(provider: string, baseUrl = ""): string | undefined {
    const key = `${provider}:${baseUrl}`;
    return this.circuitBreakers.get(key)?.state;
  }

  getSemaphoreWindow(provider: string): number | undefined {
    return this.semaphores.get(provider)?.window;
  }

  get singleflightCount(): number {
    return this.singleflight.inFlightCount;
  }
}
