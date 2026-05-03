/**
 * LLMix Call Pipeline
 *
 * Orchestrates the 19-step call flow using feature modules:
 *   1. Kill Switch → 2. Config → 3-4. Cache → 5. Circuit Breaker →
 *   6. Singleflight → [7-16 Retry Loop] → 17. Thinking strip →
 *   18. Cache write → 19. Telemetry
 *
 * The provider dispatch (step 11) is a caller-supplied callback.
 * This module is pure orchestration — no AI SDK dependency.
 */

import { type StripThinkingResult, stripThinking } from "./thinking.js";
import { type TransformKwargsCallback, applyTransformKwargs, PROVIDER_KWARGS_REGISTRY } from "./provider-kwargs.js";
import { AdaptiveSemaphore, parseOpenAIRatelimitHeaders } from "./adaptive-semaphore.js";
import { type KeyPool, KeyPoolExhaustedError } from "./key-pool.js";
import {
  CircuitBreaker,
  CircuitOpenError,
  CircuitState,
  KillSwitch,
  type FileLockLike,
  RetryPolicy,
  Singleflight,
  createFileLock,
  isRetryable,
} from "./resilience.js";
import type {
  CacheHitTier,
  LLMConfig,
  LLMUsage,
  ProviderOptions,
} from "./types.js";
import { type TwoTierCache, generateCacheKey, shouldSkipCache, sortReplacer } from "./response-cache.js";

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
  toolCalls?: unknown[] | undefined;
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
export interface CallInput {
  config: LLMConfig;
  messages: unknown[];
  singleflightKey?: string;
}

/** Full response from the call pipeline. */
export interface CallResponse {
  content: string;
  model: string;
  provider: string;
  usage: LLMUsage;
  success: boolean;
  error?: string | undefined;
  thinkingContent?: string | undefined;
  cacheHit?: CacheHitTier | undefined;
  toolCalls?: unknown[] | undefined;
}

/** Configuration for the call pipeline. */
export interface PipelineConfig {
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
  responseCache?: TwoTierCache;
  /**
   * When true (default), ``CallPipeline.close()`` also closes
   * ``responseCache``. Set to false when sharing one ``TwoTierCache`` across
   * multiple pipelines so the first ``close()`` does not tear down Redis for
   * the others.
   */
  closeResponseCache?: boolean;
}

// ---------------------------------------------------------------------------
// Pipeline
// ---------------------------------------------------------------------------

export class CallPipeline {
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

  private fileLockPromise: Promise<FileLockLike> | null = null;
  private readonly responseCache: TwoTierCache | undefined;
  private readonly closeResponseCacheOnShutdown: boolean = true;

  constructor(config: PipelineConfig) {
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
    this.responseCache = config.responseCache;
    this.closeResponseCacheOnShutdown = config.closeResponseCache ?? true;
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
    this.fileLockPromise ??= createFileLock();
    return this.fileLockPromise;
  }

  /** Build provider kwargs before dispatch and derive the effective base URL. */
  private buildRequestKwargs(
    config: LLMConfig,
    messages: unknown[],
  ): Record<string, unknown> {
    const provider = config.provider;
    let kwargs: Record<string, unknown> = {
      temperature: config.common?.temperature,
      top_p: config.common?.topP,
      max_tokens: config.common?.maxOutputTokens,
      top_k: config.common?.topK,
      presence_penalty: config.common?.presencePenalty,
      frequency_penalty: config.common?.frequencyPenalty,
      stop: config.common?.stopSequences,
      seed: config.common?.seed,
      response_format: (config as unknown as Record<string, unknown>)["responseFormat"],
    };
    const transformFn = this.transformKwargs[provider];
    if (!transformFn) {
      return kwargs;
    }

    kwargs = applyTransformKwargs(
      {
        model: config.model,
        provider,
        messages,
        temperature: config.common?.temperature,
        topP: config.common?.topP,
        providerOptions: config.providerOptions as ProviderOptions | undefined,
        baseUrl: (config as unknown as Record<string, unknown>)["baseUrl"] as string | undefined,
        enableThinking: config.common?.enableThinking,
      },
      kwargs,
      transformFn,
    );
    return kwargs;
  }

  /** Resolve the final base URL shape used by provider dispatch. */
  private resolveEffectiveBaseUrl(
    config: LLMConfig,
    messages: unknown[],
  ): string {
    const kwargs = this.buildRequestKwargs(config, messages);
    const baseUrl = kwargs["baseUrl"];
    if (typeof baseUrl === "string" && baseUrl.trim()) {
      return baseUrl;
    }
    return ((config as unknown as Record<string, unknown>)["baseUrl"] as string | undefined) ?? "";
  }

  /**
   * Execute the 19-step call flow.
   */
  async call(input: CallInput): Promise<CallResponse> {
    const { config, messages } = input;
    const provider = config.provider;
    const model = config.model;
    const effectiveBaseUrl = this.resolveEffectiveBaseUrl(config, messages);

    try {
      // Step 1: Kill Switch
      await this.killSwitch.checkAsync();

      // Step 2: Config — already resolved by caller (input.config)

      // Steps 3-4: Cache lookup (L1/L2)
      const cachingStrategy = config.caching?.strategy ?? "disabled";
      let cacheKey: string | undefined;
      if (this.responseCache && !shouldSkipCache(cachingStrategy)) {
        cacheKey = generateCacheKey({
          provider,
          model,
          messages,
          baseUrl: effectiveBaseUrl || undefined,
          enableThinking: config.common?.enableThinking,
          temperature: config.common?.temperature,
          maxOutputTokens: config.common?.maxOutputTokens,
          responseFormat: (config as unknown as Record<string, unknown>)["responseFormat"],
          seed: config.common?.seed,
          topP: config.common?.topP,
          topK: config.common?.topK,
          presencePenalty: config.common?.presencePenalty,
          frequencyPenalty: config.common?.frequencyPenalty,
          stopSequences: config.common?.stopSequences,
          providerOptions: config.providerOptions as Record<string, unknown> | undefined,
        });
        const hit = await this.responseCache.get(cacheKey);
        if (hit) {
          const { content, thinkingContent } = this.applyThinkingStrip(hit.value, config);
          return {
            content,
            model,
            provider,
            usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
            success: true,
            thinkingContent: thinkingContent ?? undefined,
            cacheHit: hit.tier,
          };
        }
      }

      // Step 5: Circuit Breaker
      const cb = this.getCircuitBreaker(provider, effectiveBaseUrl);
      // Step 6: Singleflight deduplication
      // Default key includes all request-shaping params to prevent cross-config
      // result sharing (same identity as cache key).
      const sfKey = input.singleflightKey ?? (cacheKey || Singleflight.makeKey(
        JSON.stringify({
          provider,
          model,
          messages,
          baseUrl: effectiveBaseUrl || undefined,
          enableThinking: config.common?.enableThinking,
          temperature: config.common?.temperature,
          maxOutputTokens: config.common?.maxOutputTokens,
          responseFormat: (config as unknown as Record<string, unknown>)["responseFormat"],
          seed: config.common?.seed,
          topP: config.common?.topP,
          topK: config.common?.topK,
          presencePenalty: config.common?.presencePenalty,
          frequencyPenalty: config.common?.frequencyPenalty,
          stopSequences: config.common?.stopSequences,
          providerOptions: config.providerOptions,
        }, sortReplacer),
      ));

      const result = await this.singleflight.do(sfKey, async () => {
        const currentState = cb.state;
        let probeAdmitted = false;

        try {
          cb.check();
          probeAdmitted = currentState === CircuitState.HALF_OPEN;

          const providerResult = await this.retryPolicy.execute(
            () => this.executeRetryBody(config, messages),
            (err) => this.isRetryableError(err),
          );

          // HALF_OPEN recovery decisions are based on the final probe outcome,
          // not intermediate retry attempts within a single admitted execution.
          cb.onSuccess();
          return providerResult;
        } catch (err: unknown) {
          const statusCode = (err as ProviderError).statusCode;
          if (this.isLocalError(err)) {
            if (probeAdmitted) {
              cb.cancelProbe();
            }
          } else if (!(err instanceof CircuitOpenError)) {
            // Count the full retry sequence as one failed probe.
            cb.onFailure(
              statusCode,
              statusCode === undefined,
            );
          }
          throw err;
        }
      });

      // Step 17: Thinking stripping
      const { content, thinkingContent } = this.applyThinkingStrip(
        result.content,
        config,
      );

      // Step 18: Cache write (raw content, pre-strip)
      // Skip cache write when the response contains toolCalls: CachedValue
      // stores only the text body, so a future hit would silently drop the
      // function-call structure. See GH issue #6.
      const hasToolCalls = Array.isArray(result.toolCalls) && result.toolCalls.length > 0;
      if (this.responseCache && cacheKey && !hasToolCalls) {
        await this.responseCache.set(cacheKey, result.content);
      }

      // Step 19: Telemetry — placeholder

      return {
        content,
        model: result.model,
        provider,
        usage: result.usage,
        success: true,
        thinkingContent: thinkingContent ?? undefined,
        toolCalls: result.toolCalls,
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
  ): Promise<ProviderResult> {
    const provider = config.provider;
    const semaphore = this.getSemaphore(provider);
    const lock = await this.getFileLock();

    // Step 8: AIMD Semaphore acquire (before lock to avoid convoy/deadlock)
    await semaphore.acquire();
    let apiKey: string | undefined;
    try {
      // Step 7: Cross-process lock acquire (protects state reads only)
      await lock.acquire();

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

        // Step 10: Provider kwargs transform
        kwargs = this.buildRequestKwargs(config, messages);
      } finally {
        // Release cross-process lock before network call
        await lock.release();
      }

      // Step 11: Provider dispatch (outside lock scope)
      const result = await this.dispatch({
        provider,
        model: config.model,
        apiKey: apiKey!,
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

      return result;
    } catch (err: unknown) {
      // Step 12: AIMD feedback (error path)
      const statusCode = (err as ProviderError).statusCode;
      if (statusCode === 429) {
        semaphore.onRateLimit();
      }

      // Step 14: Key Pool feedback (error)
      const pool = this.keyPools.get(provider);
      if (pool && apiKey && statusCode !== undefined) {
        if (statusCode === 429) {
          pool.rotate();
        } else if (statusCode === 401 || statusCode === 403) {
          pool.markDead(apiKey);
        }
      }

      throw err;
    } finally {
      // Step 12: AIMD semaphore release (always)
      semaphore.release();
    }
  }

  /** Check if an error is a local/config error that never contacted the provider. */
  private isLocalError(err: unknown): boolean {
    if (err instanceof KeyPoolExhaustedError) return true;
    if (
      err instanceof TypeError ||
      err instanceof RangeError ||
      err instanceof SyntaxError
    ) {
      return true;
    }
    if (err instanceof Error && err.message.startsWith("No API key pool")) {
      return true;
    }
    // Transform errors (e.g. sno-gpu missing base_url, invalid gpu_path)
    // and infrastructure errors (semaphore closed, lock setup)
    if (
      err instanceof Error &&
      (err.message.startsWith("Invalid gpu_path") ||
        err.message.includes("requires a non-empty base_url") ||
        err.message === "AdaptiveSemaphore is closed")
    ) {
      return true;
    }
    return false;
  }

  /** Step 16: Determine if an error is retryable. */
  private isRetryableError(err: unknown): boolean {
    if (err instanceof CircuitOpenError) return false;
    if (this.isLocalError(err)) return false;
    const statusCode = (err as ProviderError).statusCode;
    if (statusCode !== undefined) return isRetryable(statusCode);
    // Remaining errors without statusCode are likely network errors — retryable
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

  /** Release all semaphores and clean up resources (including Redis L2). */
  async close(): Promise<void> {
    for (const sem of this.semaphores.values()) {
      sem.close();
    }
    if (this.responseCache && this.closeResponseCacheOnShutdown) {
      await this.responseCache.close();
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
