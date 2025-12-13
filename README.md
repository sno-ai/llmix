# LLMix - LLM Config Loader

Config-driven LLM calls with three-tier caching and AI SDK v5 alignment.

## Architecture

```
Request → LRU Cache (0.1ms) → Redis (1-2ms) → File System (5-10ms)
              ↓                    ↓                   ↓
           Instant            Shared across       Cascade resolution
                               instances          with fallbacks
```

**Three-Tier Cache:**
1. **LRU Cache** - In-memory, per-instance (100 entries, 6h TTL default)
2. **Redis** - Shared across instances (24h TTL default, optional)
3. **File System** - YAML configs with cascade resolution

## Installation

```typescript
import { createLLMConfigLoader, createLLMClient } from '@sno-mem/llmix';
```

## Quick Start

```typescript
// 1. Create and initialize loader
const loader = createLLMConfigLoader({
  configDir: '/app/config/llm',
  redisUrl: process.env.REDIS_URL,  // Optional
});
await loader.init();

// 2. Create client
const client = createLLMClient({ loader });

// 3. Make LLM call
const response = await client.call({
  profile: 'hrkg:extraction',
  messages: [{ role: 'user', content: 'Extract entities from: ...' }],
});

if (response.success) {
  console.log(response.content);
  console.log(`Tokens: ${response.usage.totalTokens}`);
}
```

## Naming System

ConfigId format: `{scope}:{module}:{userId}:{profile}:v{version}`

| Component | Description | Examples |
|-----------|-------------|----------|
| `scope` | Deployment environment | `default`, `staging`, `production` |
| `module` | Functional module | `hrkg`, `memobase`, `memu`, `_default` |
| `userId` | User-specific override | `user123`, `_` (global) |
| `profile` | Task profile | `extraction`, `search`, `_base` |
| `version` | Config version | `1`, `2` |

**Profile String Shortcuts:**
- `"hrkg:extraction"` → module=hrkg, profile=extraction
- `"extraction"` → module=_default, profile=extraction

## Cascade Resolution

When loading a config, LLMix tries these paths in order:

1. `{scope}:{module}:{userId}:{profile}:v{version}` - User-specific
2. `{scope}:{module}:_:{profile}:v{version}` - Module profile
3. `{scope}:_default:_:{profile}:v{version}` - Scope-wide fallback
4. `_default:_default:_:{profile}:v{version}` - Global profile
5. `_default:_default:_:_base:v1` - Ultimate base (required)

**Example:** Loading `hrkg:extraction` for `user123`:
```
default:hrkg:user123:extraction:v1  → Not found
default:hrkg:_:extraction:v1        → Found! → config/llm/hrkg/extraction.v1.yaml
```

## API Reference

### LLMConfigLoader

```typescript
const loader = createLLMConfigLoader({
  configDir: string;           // Required: config directory path
  redisUrl?: string;           // Optional: enables Redis caching
  cacheSize?: number;          // Default: 100
  cacheTtlSeconds?: number;    // Default: 21600 (6h)
  redisTtlSeconds?: number;    // Default: 86400 (24h)
  defaultScope?: string;       // Default: "default"
  logger?: LLMConfigLoaderLogger;
});

await loader.init();           // Validates base config, connects Redis
const stats = loader.getStats(); // Cache statistics
await loader.close();          // Cleanup
```

### LLMClient

```typescript
const client = createLLMClient({ loader, defaultScope?: string });

// Make LLM call
const response = await client.call({
  profile: string;              // Required: "module:profile" or "profile"
  messages: unknown[];          // Required: AI SDK message format
  scope?: string;               // Override scope
  userId?: string;              // User-specific config
  version?: number;             // Config version (default: 1)
  overrides?: RuntimeOverrides; // Runtime parameter overrides
  telemetry?: TelemetryContext; // PostHog/Langfuse context
});

// Get config without calling LLM
const { config, capabilities } = await client.getResolvedConfig({
  profile: 'hrkg:topic-analysis',
});

if (capabilities.supportsOpenAIBatch) {
  // Use OpenAI Batch API
}
```

### Response Types

```typescript
interface LLMResponse {
  content: string;           // Generated text
  model: string;             // Model used
  provider: Provider;        // openai | anthropic | google | deepseek
  usage: LLMUsage;           // Token counts
  config: ResolvedLLMConfig; // Resolved config metadata
  success: boolean;
  error?: string;            // If success=false
}

interface ConfigCapabilities {
  provider: Provider;
  isProprietary: boolean;        // Always true for supported providers
  supportsOpenAIBatch: boolean;  // OpenAI + batch-capable model
}
```

## Runtime Overrides

Override config values at call time:

```typescript
const response = await client.call({
  profile: 'hrkg:extraction',
  messages: [...],
  overrides: {
    model: 'gpt-5',                        // Override model
    common: { temperature: 0.3 },          // Override common params
    providerOptions: {                     // Override provider options
      openai: { reasoningEffort: 'high' }
    },
  },
});
```

## YAML Schema (AI SDK v5 Aligned)

Config files map directly to AI SDK v5 `generateText()` parameters.

```yaml
# config/llm/hrkg/extraction.v1.yaml
provider: openai                    # Required: openai | anthropic | google | deepseek
model: gpt-5-mini                   # Required: provider-specific model ID

common:                             # AI SDK v5 common params
  maxOutputTokens: 8192
  temperature: 0.7
  maxRetries: 3
  # topP, topK, presencePenalty, frequencyPenalty, stopSequences, seed

providerOptions:                    # Provider-specific options
  openai:
    reasoningEffort: medium
    structuredOutputs: true

description: "Human-readable description"  # Metadata only
deprecated: false                          # Warn if true
tags: [hrkg, extraction]                   # For filtering
```

### Common Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `maxOutputTokens` | number | Max tokens to generate |
| `temperature` | 0.0-2.0 | Randomness (don't use with topP) |
| `topP` | 0.0-1.0 | Nucleus sampling (don't use with temperature) |
| `topK` | number | Sample from top K options |
| `presencePenalty` | number | Reduce repetition of existing info |
| `frequencyPenalty` | number | Reduce reuse of identical phrases |
| `stopSequences` | string[] | Sequences that halt generation |
| `seed` | number | Deterministic results |
| `maxRetries` | number | Retry attempts (default: 2) |

See: https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text

### Provider Options Reference

**OpenAI** (`providerOptions.openai`)
| Option | Type | Description |
|--------|------|-------------|
| `reasoningEffort` | minimal\|low\|medium\|high\|xhigh | Reasoning depth (GPT-5) |
| `structuredOutputs` | boolean | Enable structured outputs |
| `parallelToolCalls` | boolean | Enable parallel tool calls |
| `logprobs` | boolean\|number | Enable logprobs |
| `serviceTier` | auto\|flex\|priority\|default | Service tier |

See: https://ai-sdk.dev/providers/ai-sdk-providers/openai

**Anthropic** (`providerOptions.anthropic`)
| Option | Type | Description |
|--------|------|-------------|
| `thinking.type` | enabled\|disabled | Extended thinking |
| `thinking.budgetTokens` | number | Thinking budget (min 1024) |
| `cacheControl.type` | ephemeral | Cache type |
| `effort` | high\|medium\|low | Effort level |

See: https://ai-sdk.dev/providers/ai-sdk-providers/anthropic

**Google** (`providerOptions.google`)
| Option | Type | Description |
|--------|------|-------------|
| `thinkingConfig.thinkingLevel` | low\|high | Gemini 3 thinking |
| `thinkingConfig.thinkingBudget` | number | Gemini 2.5 budget |
| `structuredOutputs` | boolean | Enable structured outputs |
| `cachedContent` | string | Cached content ID |

See: https://ai-sdk.dev/providers/ai-sdk-providers/google-generative-ai

**DeepSeek** (`providerOptions.deepseek`)
| Option | Type | Description |
|--------|------|-------------|
| `thinking.type` | enabled\|disabled | Reasoning mode |

See: https://api-docs.deepseek.com/

## Telemetry Integration

LLMix integrates with PostHog and Langfuse:

```typescript
const response = await client.call({
  profile: 'hrkg:extraction',
  messages: [...],
  telemetry: {
    userId: 'user123',
    projectId: 'proj456',
    featureName: 'entity-extraction',
    // Additional context for PostHog tracking
  },
});
```

**Privacy:** Langfuse payload capture is opt-in. Set `LANGFUSE_CAPTURE_PAYLOAD=true` to include messages/output.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key |
| `GOOGLE_GENERATIVE_AI_API_KEY` | For Google | Google AI API key |
| `DEEPSEEK_API_KEY` | For DeepSeek | DeepSeek API key |
| `LANGFUSE_CAPTURE_PAYLOAD` | No | Enable message/output capture |

## Error Handling

```typescript
import { ConfigNotFoundError, InvalidConfigError, SecurityError } from '@sno-mem/llmix';

try {
  const response = await client.call({ profile: 'unknown:profile', messages });
} catch (error) {
  if (error instanceof ConfigNotFoundError) {
    // Config not found in cascade
  } else if (error instanceof InvalidConfigError) {
    // YAML schema validation failed
  } else if (error instanceof SecurityError) {
    // Path traversal or invalid input
  }
}
```

## File Structure

```
config/llm/
├── _default/
│   ├── _base.v1.yaml       # Ultimate fallback (REQUIRED)
│   └── _base_low.v1.yaml   # Low-complexity fallback
├── hrkg/
│   ├── extraction.v1.yaml
│   ├── search.v1.yaml
│   └── trigger.v1.yaml
└── memobase/
    └── summarization.v1.yaml
```

**File naming:** `{profile}.v{version}.yaml`

## Validation

All inputs are validated:
- **module:** `^(_default|[a-z][a-z0-9_]{0,63})$`
- **profile:** `^(_base[a-z0-9_]*|[a-z][a-z0-9_]{0,63})$`
- **scope:** `^(_default|[a-z][a-z0-9_-]{0,63})$`
- **userId:** `^[a-zA-Z0-9_-]{1,64}$`
- **version:** 1-9999

## Exported Utilities

```typescript
// Zod schemas for external validation
export { LLMConfigSchema, CommonParamsSchema, ProviderOptionsSchema };

// Validation functions
export { validateModule, validateProfile, validateScope, validateUserId, validateVersion };

// Path utilities
export { buildConfigFilePath, verifyPathContainment };

// LRU cache (for advanced use)
export { LRUCache };
```
