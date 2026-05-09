# LLMix Real Integration Test Plan

## Philosophy

Two distinct test scopes with different strategies:

1. **Pipeline integration tests** — Test core pipeline machinery (cache, thinking strip, retry, circuit breaker, key rotation, AIMD, singleflight) using an **instrumented real-dispatch wrapper**: a thin wrapper around the actual provider SDK call that counts invocations, captures headers, and records which API key was used — but makes REAL HTTP calls. This is not mocking; it is observability.

2. **Provider smoke tests** — Test each provider SDK in isolation with REAL calls. No pipeline wrapper — direct provider dispatch to verify message formatting, auth, kwargs, and response structure.

## Language Scope

| Provider | Python | TypeScript | Notes |
|----------|--------|------------|-------|
| OpenAI | Yes | Yes | Both have provider implementations |
| Anthropic | Yes | Yes | Python: direct SDK, TS: @ai-sdk/anthropic |
| Gemini | Yes | Yes* | TS uses the current @ai-sdk/google client path |
| SnoGPU | Yes | **Python-only** | No TS provider implementation exists |
| Novita | Yes | **Python-only** | No TS provider implementation exists |

*TS Gemini tests use the current client path, not the shared pipeline path.

Tests marked "Python-only" have no TS counterpart. All other tests run in BOTH languages.

## Prerequisites

- **CI secrets**: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, NOVITA_API_KEY, GPU_BASE_URL, SNO_LLM_API_KEY, OPENAI_KEYS (multi-key)
- **Redis**: Running instance at REDIS_URL for T3 cache tests
- **SnoGPU network**: CI runner must reach GPU_BASE_URL (Tailscale or Docker network)
- **Test harness**: `conftest.py` with skip decorators, instrumented dispatch wrapper, timing helpers
- **Live-test secrets**: read from the environment (Doppler, CI secret store, or exported env); see the env-var table in the top-level `README.md`

## Execution Tiers

| Tier | What runs | Cost | When |
|------|-----------|------|------|
| T1 — Smoke | 1 call per provider, cache L1, thinking strip | ~$0.05 | Every PR |
| T2 — Core | All suites except batch + concurrency stress | ~$0.30 | Daily CI |
| T3 — Full | Everything including batch + Redis + stress | ~$0.70 | Weekly, pre-release |

## Test Organization

```
tests/integration/
├── conftest.py                    # Skip decorators, env validation, instrumented dispatch
├── helpers.ts                     # TS equivalents of conftest helpers
│
│ # Scope 1: Provider Smoke Tests (direct SDK calls)
├── test_e2e_openai.py             # Suite 1: OpenAI          [Py + TS]
├── e2e-openai.test.ts
├── test_e2e_anthropic.py          # Suite 2: Anthropic        [Py + TS]
├── e2e-anthropic.test.ts
├── test_e2e_gemini.py             # Suite 3: Gemini           [Py + TS]
├── e2e-gemini.test.ts
├── test_e2e_sno_gpu.py            # Suite 4: SnoGPU           [Py only]
├── test_e2e_novita.py             # Suite 5: Novita           [Py only]
│
│ # Scope 2: Pipeline Integration Tests (instrumented real dispatch)
├── test_e2e_cache.py              # Suite 6: L1 + L2 cache    [Py + TS]
├── e2e-cache.test.ts
├── test_e2e_thinking.py           # Suite 7: Thinking strip   [Py + TS]
├── e2e-thinking.test.ts
├── test_e2e_resilience.py         # Suite 8: Resilience       [Py + TS]
├── e2e-resilience.test.ts
├── test_e2e_concurrency.py        # Suite 9: Concurrency      [Py + TS]
├── e2e-concurrency.test.ts
├── test_e2e_batch.py              # Suite 10: Batch API       [Py + TS]
├── e2e-batch.test.ts
│
│ # Scope 3: Cross-cutting
├── test_e2e_cross_provider.py     # Suite 11: Cross-provider  [Py; TS limited to OAI/Ant/Gem]
├── e2e-cross-provider.test.ts
├── test_e2e_security.py           # Suite 12: Security        [Py + TS]
├── e2e-security.test.ts
├── test_e2e_parity.py             # Suite 13: Py↔TS parity   [Both, offline]
├── e2e-parity.test.ts
│
└── fixtures/
    ├── prompts.json               # Deterministic prompts with expected answer patterns
    └── thinking-prompts.json      # Prompts that trigger <think> blocks
```

---

## Suite 1: OpenAI Real Calls [Py + TS]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 1.1 | Simple completion — `"What is 2+2? Reply with just the number."` to gpt-4o-mini | Pipeline returns correct response | content contains "4", success=True, usage.total_tokens > 0 |
| 1.2 | Temperature=0 + seed determinism — same prompt twice with temperature=0, seed=42 | Deterministic output | r1.content == r2.content (seed improves reliability over temp=0 alone) |
| 1.3 | Reasoning model kwargs stripping — o4-mini with temperature=0.7 | openai_transform_kwargs strips temp | success=True (would 400 without stripping) |
| 1.4 | Max tokens enforcement — max_output_tokens=10 | maxTokens flows through | usage.output_tokens ≤ 15, content short |
| 1.5 | System message — system="You are a pirate" + user="Say hello" | System message passed correctly | Content has pirate vocabulary |
| 1.6 | Invalid model — send to `nonexistent-model-xyz` | Error handling works | success=False, error present, no crash |
| 1.7 | Usage accounting | Token counting works | input_tokens > 0, output_tokens > 0, total = input + output |
| 1.8 | JSON mode — responseFormat with json_object | Structured output works | json.loads(content) succeeds |

## Suite 2: Anthropic Real Calls [Py + TS]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 2.1 | Simple completion — claude-haiku-4-5-20251001 | Anthropic direct SDK works | Non-empty content, usage present |
| 2.2 | System message extraction — Anthropic requires system as separate param | System message handling | success=True (would error if system left in messages) |
| 2.3 | Native prompt caching — caching.strategy=native, large system prompt >1024 tokens, call twice | cache_control: ephemeral injection | Second call has cached_input_tokens > 0 |
| 2.4 | Long output — max_tokens=2000 | max_tokens passthrough | output_tokens > 100, content > 500 chars |
| 2.5 | Error classification — invalid API key | 401 classified as non-retryable | Immediate failure, no retry |

## Suite 3: Gemini Real Calls [Py + TS]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 3.1 | Simple completion — gemini-2.5-flash | Gemini provider works | Non-empty content, usage present |
| 3.2 | System message reformatting — Gemini aggregates system messages into system_instruction | Message format conversion | success=True, content relevant to system instruction |
| 3.3 | Conversation must end with user role — multi-turn with assistant last | Gemini adds continuation message | success=True (would error without reformatting) |
| 3.4 | ThinkingConfig disabled — default thinking_budget=0 | kwargs injection sets ThinkingConfig | success=True, no thinking output in response |
| 3.5 | ThinkingConfig enabled — thinking_budget=1024 | Thinking budget passthrough | success=True, potentially longer response |
| 3.6 | Temperature + topP passthrough | Common params flow through | success=True |
| 3.7 | Invalid API key | Error handling | success=False, error present |

## Suite 4: SnoGPU Real Calls [Python-only]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 4.1 | Extract path — qwen3.6-27b-extract via /extract/v1 | GPU path routing works | success=True, model field correct |
| 4.2 | Reason path — qwen3.6-27b-reason via /reason/v1 | Dual-GPU routing | success=True, different model |
| 4.3 | Path traversal — gpuPath="../../etc/passwd" | Path validation rejects | ValueError raised, no request sent |
| 4.4 | Thinking enabled — enableThinking=true on Qwen3.6 | Thinking mode activation | Response contains `<think>` blocks |
| 4.5 | Thinking disabled — enableThinking=false | Default no-thinking mode | No `<think>` tags in raw response |
| 4.6 | Auth header — X-Sno-LLM-Key with SNO_LLM_API_KEY | GPU auth works | success=True |
| 4.7 | Missing auth — no SNO_LLM_API_KEY | Auth failure handling | success=False, error indicates auth |
| 4.8 | Large structured extraction — 2000-word input, JSON output | Real workload test | Valid JSON output, reasonable latency |
| 4.9 | Path traversal (TS kwargs only) — verify TS snoGpuTransformKwargs rejects bad gpuPath | TS validation parity | Error thrown (fixed: now validates in TS too) |

## Suite 5: Novita Real Calls [Python-only]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 5.1 | Simple completion — qwen/qwen3.5-27b | Novita OpenAI-compat works | Non-empty content, usage present |
| 5.2 | Thinking enabled — enableThinking=true | Qwen3.5 thinking via Novita | Response includes `<think>` blocks |
| 5.3 | Base URL correct — https://api.novita.ai/v3/openai | URL construction | success=True |
| 5.4 | Comparison with SnoGPU — same prompt to both | Behavioral parity check | Both produce coherent answers |

## Suite 6: Cache [Py + TS]

Uses instrumented dispatch wrapper: real OpenAI calls + call counter + content capture.

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 6.1 | L1 hit — same prompt twice, strategy=memory | L1 caching works | 2nd call: cache_hit="l1", same content, dispatch_count==1, latency < 5ms |
| 6.2 | L1 miss on different prompt | Cache keys differentiate | dispatch_count==2 |
| 6.3 | L1 miss on different temperature | Temperature in cache key | dispatch_count==2 |
| 6.4 | L1 miss on different responseFormat | responseFormat in cache key | dispatch_count==2 |
| 6.5 | L1 miss on different seed | seed in cache key | dispatch_count==2 |
| 6.6 | L1 miss on different topP | topP in cache key | dispatch_count==2 |
| 6.7 | L1 miss on different provider — same prompt, OpenAI vs Anthropic | Provider in cache key | dispatch_count==2 |
| 6.8 | L2 Redis hit — call, destroy pipeline (clears L1), new pipeline, same prompt [T3] | L2 persistence | cache_hit="l2", content identical |
| 6.9 | L2 backfill — after 6.8, call again [T3] | L1 backfilled from L2 | cache_hit="l1" |
| 6.10 | Cache stores raw, strip on read — thinking model via SnoGPU [Py-only, T2] | Raw storage + strip-on-read | Cache value contains `<think>`, returned content stripped, thinking_content populated |
| 6.11 | Cache + keep_thinking_output toggle — same prompt, first false then true | Same cache key, different read | First: stripped. Second: raw with `<think>` blocks |
| 6.12 | Cache skip for disabled strategy | No caching occurs | dispatch_count==2 for same prompt |
| 6.13 | Cache skip for native strategy | Native caching defers to provider | dispatch_count==2 |
| 6.14 | Redis-or-memory degradation — invalid Redis URL [T3] | Graceful L1 fallback | No crash, 2nd call cache_hit="l1" |
| 6.15 | Cross-provider cache isolation — same prompt to OpenAI and Gemini | Provider in cache key | Different cache entries |
| 6.16 | Cache with SnoGPU thinking — enableThinking=true, cache hit [Py-only, T2] | Real thinking tags in cache | Correctly stripped on read, thinking_content matches |

## Suite 7: Thinking Tag Stripping [Py + TS where noted]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 7.1 | SnoGPU Qwen3.6 stripping — enableThinking=true [Py-only] | Real `<think>` blocks stripped | content has no `<think>`, thinking_content non-empty |
| 7.2 | SnoGPU keep_thinking_output=true [Py-only] | Thinking preserved | content contains `<think>`, thinking_content is None |
| 7.3 | Novita Qwen3.5 stripping — enableThinking=true [Py-only] | Stripping via OpenAI-compat | Same as 7.1 |
| 7.4 | Non-thinking model passthrough — gpt-4o-mini [Py + TS] | No false positives | thinking_content is None, content unchanged |
| 7.5 | Anthropic passthrough — claude-haiku [Py + TS] | No false positives on Anthropic | thinking_content is None |
| 7.6 | Gemini passthrough — gemini-2.5-flash [Py + TS] | No false positives on Gemini | thinking_content is None |
| 7.7 | Thinking + JSON output — SnoGPU thinking=true, ask for JSON [Py-only] | Stripping doesn't corrupt JSON | json.loads(content) succeeds after stripping |
| 7.8 | Thinking + long output — SnoGPU thinking=true, detailed analysis [Py-only] | Multi-block stripping | All `<think>` blocks removed |
| 7.9 | Code output with literal `<think>` — gpt-4o-mini write code with `<think>` tag [Py + TS] | **Known limitation**: stripping regex strips code examples containing `<think>`. Assert: content is stripped (documenting the behavior). | Assert `<think>` absent from content. This IS a false positive — documented and accepted. |

## Suite 8: Resilience [Py + TS]

Uses instrumented dispatch wrapper for observability.

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 8.1 | Retry on real 429 — burst of 20 rapid calls to gpt-4o-mini | Retry with backoff works | Most calls eventually succeed |
| 8.2 | Non-retryable 401 — invalid API key | Fast fail, no retry | dispatch_count==1, circuit stays CLOSED |
| 8.3 | Non-retryable 400 — malformed request | No retry on client error | dispatch_count==1 |
| 8.4 | Kill switch blocks — create file, attempt call | Instant rejection | success=False, dispatch_count==0, timing < 10ms |
| 8.5 | Kill switch recovery — create, verify blocked, delete, verify unblocked | Dynamic check | Calls succeed after deletion |
| 8.6 | Circuit breaker trip — 3 consecutive 5xx errors on same provider | Circuit opens per-provider | 4th call fails fast with "Circuit breaker OPEN", dispatch_count==0 for 4th call |
| 8.7 | Circuit breaker recovery — after 8.6, wait 30s, send valid call | HALF_OPEN probe | State: OPEN → HALF_OPEN → CLOSED |
| 8.8 | Circuit breaker ignores 401 — invalid key 3x | Circuit stays CLOSED | Auth errors don't trip breaker |
| 8.9 | Timeout handling — call with 1ms timeout | Timeout triggers retry | Treated as retryable error |
| 8.10 | Retry-After header — trigger 429, verify delay respects header | Backoff uses Retry-After | Measured delay ≥ Retry-After value (now wired: resilience.ts:377, resilience.py:411) |

## Suite 9: Concurrency [Py + TS]

Uses instrumented dispatch wrapper with call counter + API key tracker.

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 9.1 | Singleflight dedup — 5 identical calls concurrently | Only 1 provider call | dispatch_count==1, all 5 get same content |
| 9.2 | Singleflight no-dedup — 5 different prompts | 5 separate calls | dispatch_count==5, 5 distinct responses |
| 9.3 | Singleflight error propagation — provider errors | All waiters get error | All 5 return success=False |
| 9.4 | AIMD window — 50 concurrent calls | Semaphore manages concurrency | All succeed, semaphore.window inspectable |
| 9.5 | AIMD header backoff — observe via instrumented headers | Preemptive backoff | Window < initial after rate-limit headers |
| 9.6 | Key rotation — 2 keys, trigger 429 | Rotation to second key | instrumented_keys shows both keys used |
| 9.7 | Dead key marking — [invalid-key, valid-key] pool | Dead key permanently skipped | After 401, only valid key in subsequent calls |
| 9.8 | All keys exhausted — pool of [invalid, invalid] | Graceful failure | Error returned, not hang |

## Suite 10: Batch API [Py + TS]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 10.1 | OpenAI batch — submit 3 prompts, poll, retrieve | Full batch lifecycle | 3 results with content |
| 10.2 | Anthropic batch — submit 3 prompts, poll, retrieve | Anthropic batch works | 3 results with content |
| 10.3 | Gemini batch — submit 3 prompts, poll, retrieve | Gemini batch works | 3 results with content |
| 10.4 | Batch ID roundtrip — encode then decode | ID encoding correct | All fields preserved, colon-safe |
| 10.5 | Metadata file lifecycle — submit, check file, retrieve, check deleted | Durable metadata | File exists after submit, gone after results |
| 10.6 | Metadata security — read metadata JSON file | No plaintext API key | File contains keyFingerprint (8 hex chars), NOT raw key |
| 10.7 | Batch with empty prompts | Input validation | Error raised before submission |

## Suite 11: Cross-Provider [Py + TS limited]

TS tests cover OpenAI + Anthropic + Gemini only (no SnoGPU/Novita in TS).

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 11.1 | Same prompt to all available providers | All providers work | All return coherent answers to "What is the capital of France?" |
| 11.2 | Usage structure parity | Consistent response schema | All have input_tokens, output_tokens, total_tokens > 0 |
| 11.3 | Error structure parity — invalid model to each | Consistent error handling | All return success=False with non-empty error string |
| 11.4 | System message parity — same system+user to all | System message works everywhere | All responses influenced by system |
| 11.5 | Temperature=0 + seed parity | Internal determinism | Each provider returns identical content on 2 calls |

## Suite 12: Security & Leakage [Py + TS]

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 12.1 | API key not in cache key INPUT fields | Key excluded from hash input | Inspect CACHE_KEY_FIELDS — no key-like field present. Build canonical JSON from test params, assert no "sk-" substring in the pre-hash JSON string. |
| 12.2 | API key not in cache value | Key excluded from cached response | Read raw L1 cache entry, search for API key substring |
| 12.3 | API key not in batch metadata | Only fingerprint stored | Read metadata file, assert no "sk-" prefix, assert keyFingerprint is 8 hex chars |
| 12.4 | API key not in error messages | Key not leaked on error | Trigger error with known key, search error string for key |
| 12.5 | API key not in singleflight key INPUT | Key excluded from dedup input | Inspect Singleflight.makeKey input — it hashes {provider, model, messages}, none contain keys |
| 12.6 | GPU auth token not in logs | SNO_LLM_API_KEY not logged | Capture log output during SnoGPU call, search for token [Py-only] |
| 12.7 | API key not in response object | Key not in any response field | Inspect all CallResponse fields for key substring |

## Suite 13: Python↔TypeScript Parity [Both, offline]

**Fixture contract**: All parity tests use canonical camelCase format for cache key inputs (matching the intentional camelCase in both languages' CACHE_KEY_FIELDS).

| # | Test | What it proves | Verify |
|---|------|----------------|--------|
| 13.1 | Cache key parity — same camelCase params | Identical SHA-256 | Python hash == TS hash for 10+ vectors |
| 13.2 | Thinking strip parity — same raw output | Identical stripping | Python stripped == TS stripped for all vectors |
| 13.3 | Batch ID parity — same encode inputs | Identical encoding | Python ID == TS ID |
| 13.4 | Response structure parity — same provider call | Compatible field names | Map Python snake_case to TS camelCase |
| 13.5 | Error classification parity — same status codes | Same retryable/non-retryable | Python is_retryable(429)==True == TS isRetryable(429)==true |
| 13.6 | gpuPath validation parity — same invalid inputs | Both languages reject | Python ValueError == TS Error for same bad paths |

---

## Hidden-Bug-Finding Strategies

| Category | Bug | Test(s) |
|----------|-----|---------|
| Cache key collision | Same prompt + different temp → stale response | 6.3 |
| Cache key collision | Same prompt + different responseFormat → wrong response | 6.4 |
| Cache key collision | Same prompt + different seed → wrong response | 6.5 |
| Cache key collision | Same prompt + different provider → wrong response | 6.7, 6.15 |
| Thinking false positive | Code output containing `<think>` gets stripped | 7.9 (known limitation, documented) |
| Thinking corruption | Stripping breaks JSON structure | 7.7 |
| Cache + thinking | Toggle keep_thinking after cache warm → stale | 6.11 |
| Cache stores wrong content | Step 17 (strip) happens before step 18 (write) | 6.10 verifies raw storage via `result.content` |
| Kwargs stripping failure | Temperature sent to reasoning model → 400 | 1.3 |
| Retry masking errors | Non-retryable error retried forever → hang | 8.2, 8.3 |
| Retry-After ignored | Pipeline doesn't pass header to delay calc | 8.10 (now wired) |
| Circuit breaker false trip | Auth error trips circuit for entire provider | 8.8 |
| Circuit breaker scope | Per-endpoint vs per-provider confusion | 8.6 (documented: per-provider by design) |
| Singleflight token inflation | 5 callers share 1 result, each reports full tokens | 9.1 (verify via dispatch_count) |
| Key rotation not working | rotate() called but same key reused | 9.6 (verify via instrumented_keys) |
| Dead key resurrection | Marked-dead key used again | 9.7 |
| Path traversal | SnoGPU gpuPath allows ../../ | 4.3, 4.9, 13.6 |
| Plaintext key leak | API key in cache/metadata/logs | 12.1-12.7 (inspect pre-hash inputs, not digests) |
| Cross-language drift | Python and TS produce different cache keys | 13.1 |
| GPU path routing | Wrong GPU handles request | 4.1, 4.2 |
| Gemini message format | Non-user-last conversation fails | 3.3 |
| Float precision | 0.7 vs 0.7000000000000001 in cache key | 13.1 (cross-language vector includes floats) |

## Prompt Design Principles

1. **Deterministic answers** — math, factual, constrained (`"What is 2+2?"`, `"Name the 4th planet"`)
2. **Cheap models** — gpt-4o-mini, claude-haiku-4-5-20251001, gemini-2.5-flash, qwen3.6-27b-extract
3. **Short outputs** — max_output_tokens=50-100 unless testing long output
4. **Temperature=0 + seed** — for determinism tests (seed=42 alongside temp=0 for OpenAI)
5. **Thinking triggers** — reasoning: `"Solve step by step: If x + 3 = 7, what is x?"`
6. **Instrumented dispatch** — wrapper counts calls, captures headers, records API keys used

## Estimated Cost per Full Run (T3)

| Suite | Calls | Est. cost |
|-------|-------|-----------|
| S1: OpenAI | 12 | $0.01 |
| S2: Anthropic | 8 | $0.01 |
| S3: Gemini | 10 | $0.01 |
| S4: SnoGPU | 12 | $0.00 (on-prem) |
| S5: Novita | 6 | $0.02 |
| S6: Cache | 20 | $0.03 |
| S7: Thinking | 12 | $0.05 |
| S8: Resilience | 30 | $0.10 |
| S9: Concurrency | 70 | $0.15 |
| S10: Batch | 12 | $0.05 |
| S11: Cross-Provider | 20 | $0.05 |
| S12: Security | 12 | $0.01 |
| S13: Parity | 6 | $0.00 (offline) |
| **Total** | **~230** | **~$0.49** |
| **+ Redis overhead, retries, batch polling** | | **~$0.70** |

## Code Fixes Applied Before This Plan

These issues were found by Codex review and fixed before finalizing the plan:

1. **TS gpuPath validation** — `provider-kwargs.ts:158-161`: Added path traversal protection matching Python (regex + `..` check)
2. **Retry-After wiring** — `resilience.ts:377`, `resilience.py:411`: Now extracts `retry-after` header from error and passes to `getDelayMs`/`get_delay_ms`
3. **Circuit breaker scope** — Documented as per-provider (not per-endpoint) in both pipelines. The baseUrl is only known after kwargs transform (step 10), which runs inside the retry loop after the circuit breaker check (step 5).
4. **Cache write ordering confirmed correct** — Step 18 writes `result.content` (raw, pre-strip). Step 17 strips into local `content` variable. The raw `result.content` is never mutated.
