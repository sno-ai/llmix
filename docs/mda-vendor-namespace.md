# MDA Vendor Namespace: snoai-llmix

`metadata.snoai-llmix` is the MDA vendor namespace for the LLMix preset format.
It stores the project-specific preset data consumed by the Python and
TypeScript MDA loaders. MDA-owned mechanism fields such as `requires`,
`integrity`, and `signatures` remain top-level MDA fields and are handled by the
installed MDA parser packages.

## Namespace Shape

```yaml
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.7
      maxOutputTokens: 4096
    providerOptions:
      openai:
        reasoningEffort: medium
    caching:
      strategy: memory
```

Objects in this namespace are strict: unknown keys are invalid unless their value
is explicitly described below as a provider-specific pass-through record.

## `common`

Required object. `provider` and `model` are required; every other key is optional.

| Key | Type and constraints |
| --- | -------------------- |
| `provider` | Required string enum: `openai`, `anthropic`, `google`, `deepseek`, `openrouter`, `deepinfra`, `novita`, `together`, `sno-gpu`. |
| `model` | Required non-empty string provider model ID. |
| `maxOutputTokens` | Optional positive integer. |
| `temperature` | Optional number from `0` to `2`. |
| `topP` | Optional number from `0` to `1`. |
| `topK` | Optional positive integer. |
| `presencePenalty` | Optional number. |
| `frequencyPenalty` | Optional number. |
| `stopSequences` | Optional array of strings. |
| `seed` | Optional integer. |
| `maxRetries` | Optional non-negative integer. |
| `enableThinking` | Optional boolean. |
| `keepThinkingOutput` | Optional boolean. |

## `providerOptions`

Optional object. Provider option objects are optional and strict.

### `openai`

| Key | Type and constraints |
| --- | -------------------- |
| `reasoningEffort` | Optional enum: `minimal`, `low`, `medium`, `high`, `xhigh`. |
| `parallelToolCalls` | Optional boolean. |
| `user` | Optional string. |
| `logprobs` | Optional boolean or non-negative integer. |
| `logitBias` | Optional record of integer token ID to number bias. |
| `structuredOutputs` | Optional boolean. |
| `strictJsonSchema` | Optional boolean. |
| `maxCompletionTokens` | Optional positive integer. |
| `store` | Optional boolean. |
| `metadata` | Optional record of string to string. |
| `prediction` | Optional record of string to unknown JSON value. |
| `serviceTier` | Optional enum: `auto`, `flex`, `priority`, `default`. |
| `textVerbosity` | Optional enum: `low`, `medium`, `high`. |
| `promptCacheKey` | Optional string. |
| `promptCacheRetention` | Optional enum: `in_memory`, `24h`. |
| `safetyIdentifier` | Optional string. |

### `anthropic`

| Key | Type and constraints |
| --- | -------------------- |
| `thinking` | Optional object. If present, `type` is required enum `enabled` or `disabled`; `budgetTokens` is an optional positive integer. When the selected provider is `anthropic`, `thinking.type: enabled` requires any supplied `budgetTokens` to be at least `1024`. |
| `cacheControl` | Optional object. If present, `type` is required literal `ephemeral`; `ttl` is an optional string. |
| `disableParallelToolUse` | Optional boolean. |
| `sendReasoning` | Optional boolean. |
| `effort` | Optional enum: `high`, `medium`, `low`. |
| `toolStreaming` | Optional boolean. |
| `structuredOutputMode` | Optional enum: `outputFormat`, `jsonTool`, `auto`. |

### `google`

| Key | Type and constraints |
| --- | -------------------- |
| `thinkingConfig` | Optional object with optional `thinkingLevel` enum `low` or `high`, optional positive integer `thinkingBudget`, and optional boolean `includeThoughts`. |
| `cachedContent` | Optional string. |
| `structuredOutputs` | Optional boolean. |
| `safetySettings` | Optional array of objects with required string `category` and required string `threshold`. |
| `responseModalities` | Optional array of strings. |

### `deepseek`

| Key | Type and constraints |
| --- | -------------------- |
| `thinking` | Optional object. If present, `type` is required enum `enabled` or `disabled`. |

### `openrouter`

| Key | Type and constraints |
| --- | -------------------- |
| `provider` | Optional record of string to unknown JSON value. |
| `reasoning` | Optional record of string to unknown JSON value. |

### `sno-gpu`

| Key | Type and constraints |
| --- | -------------------- |
| `enableThinking` | Optional boolean. |
| `thinkingBudget` | Optional positive integer. |
| `gpuPath` | Optional string. |

### `deepinfra`, `novita`, and `together`

These provider option objects are optional pass-through records. `deepinfra`
and `novita` may include optional `enableThinking` boolean and positive integer
`thinkingBudget` values.

## `caching`

Optional object. If present, `strategy` is required.

| Key | Type and constraints |
| --- | -------------------- |
| `strategy` | Required enum: `native`, `gateway`, `disabled`, `redis`, `redis-or-memory`, `memory`. |
| `key` | Optional string cache key. |
| `ttl` | Optional positive integer number of seconds. |
| `maxItems` | Optional positive integer maximum L1 cache entries. |

## DSSE Payload Type

LLMix does not define a separate namespace-level signature envelope. Sigstore
signature verification is delegated to the MDA mechanism layer exposed by
`@snoai/mda-config`.

The reserved semantic payload type for an LLMix preset remains:

```text
application/vnd.snoai-llmix.preset+json
```

The media type identifies the semantic payload as an LLMix preset. Actual
signature payload construction, canonicalization, and trust policy checks follow
the public MDA package contract.

## Stability

The `snoai-llmix` namespace is frozen for LLMix v1.0. Breaking changes to the
namespace or to `application/vnd.snoai-llmix.preset+json` require a major LLMix
version bump.
