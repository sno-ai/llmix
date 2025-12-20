# LLMix

[![npm version](https://img.shields.io/npm/v/llmix.svg)](https://www.npmjs.com/package/llmix)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.0+-blue.svg)](https://www.typescriptlang.org/)
[![AI SDK](https://img.shields.io/badge/AI_SDK-v5-green.svg)](https://ai-sdk.dev/)

**Config-driven LLM client with three-tier caching and AI SDK v5 alignment.**

LLMix decouples LLM configuration from code, enabling runtime model switching, A/B testing, and centralized prompt management without deployments.

## Features

- **YAML-based Configuration** - Define models, parameters, and provider options in version-controlled config files
- **Three-Tier Caching** - LRU (0.1ms) → Redis (1-2ms) → File System (5-10ms)
- **Multi-Provider** - OpenAI, Anthropic, Google, DeepSeek with unified interface
- **AI SDK v5 Native** - Zero translation layer, configs map directly to `generateText()` params
- **Cascade Resolution** - User → Module → Scope → Global fallback chain
- **Telemetry Ready** - Optional PostHog/Langfuse integration via dependency injection
- **Type-Safe** - Full TypeScript support with exported Zod schemas

## Installation

```bash
# npm
npm install llmix

# pnpm
pnpm add llmix

# yarn
yarn add llmix

# bun
bun add llmix
```

### Peer Dependencies

LLMix uses peer dependencies for provider SDKs to avoid version conflicts:

```bash
# Required for LLM calls
npm install ai @ai-sdk/openai @ai-sdk/google

# Optional for Redis caching
npm install ioredis
```

### As Git Submodule

```bash
git submodule add https://github.com/sno-ai/llmix.git packages/llmix
```

## Quick Start

```typescript
import { createLLMConfigLoader, createLLMClient } from 'llmix';

// 1. Create loader
const loader = createLLMConfigLoader({
  configDir: './config/llm',
  redisUrl: process.env.REDIS_KV_URL,  // Optional - use REDIS_KV_URL for consistency
});
await loader.init();

// 2. Create client
const client = createLLMClient({ loader });

// 3. Make LLM call
const response = await client.call({
  profile: 'hrkg:extraction',
  messages: [{ role: 'user', content: 'Extract entities from: Tesla announced Q4 earnings...' }],
});

if (response.success) {
  console.log(response.content);
  console.log(`Tokens: ${response.usage.totalTokens}`);
}
```

## Architecture

```
                           ┌─────────────────────────────────────────────┐
                           │              LLMix Client                   │
                           └─────────────────┬───────────────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    ▼                        ▼                        ▼
            ┌───────────────┐       ┌───────────────┐        ┌───────────────┐
            │   LRU Cache   │       │     Redis     │        │  File System  │
            │    (0.1ms)    │       │    (1-2ms)    │        │   (5-10ms)    │
            │               │       │               │        │               │
            │  • Per-instance│      │  • Shared     │        │  • YAML files │
            │  • 100 entries │      │  • 24h TTL    │        │  • Cascade    │
            │  • 6h TTL      │      │  • Optional   │        │  • Validated  │
            └───────────────┘       └───────────────┘        └───────────────┘
```

## Configuration

### Directory Structure

```
config/llm/
├── _default/
│   ├── _base.v1.yaml         # Ultimate fallback (REQUIRED)
│   └── _base_low.v1.yaml     # Low-cost fallback
├── hrkg/
│   ├── extraction.v1.yaml    # Entity extraction
│   ├── search.v1.yaml        # Semantic search
│   └── topic.v1.yaml         # Topic analysis
└── memobase/
    └── summarize.v1.yaml     # Summarization
```

### YAML Schema

Configs map directly to [AI SDK v5 generateText()](https://ai-sdk.dev/docs/reference/ai-sdk-core/generate-text) parameters:

```yaml
# config/llm/hrkg/extraction.v1.yaml
provider: openai                    # openai | anthropic | google | deepseek
model: gpt-4o                       # Provider-specific model ID

common:                             # AI SDK common params
  maxOutputTokens: 8192
  temperature: 0.7
  maxRetries: 3

providerOptions:                    # Provider-specific options
  openai:
    structuredOutputs: true
    reasoningEffort: medium         # For o1/o3 models

timeout:                            # Per-profile timeout (in minutes)
  totalTime: 2                      # Default: 2 min. Reasoning models: 10+ min

description: "Entity extraction for knowledge graph"
tags: [hrkg, extraction]
```

### Provider Options

<details>
<summary><b>OpenAI</b></summary>

```yaml
providerOptions:
  openai:
    reasoningEffort: medium         # minimal|low|medium|high|xhigh (o1/o3)
    structuredOutputs: true
    parallelToolCalls: true
    serviceTier: auto               # auto|flex|priority|default
    logprobs: true
```
</details>

<details>
<summary><b>Anthropic</b></summary>

```yaml
providerOptions:
  anthropic:
    thinking:
      type: enabled
      budgetTokens: 10000           # Min 1024 for extended thinking
    cacheControl:
      type: ephemeral
    effort: high                    # high|medium|low
```
</details>

<details>
<summary><b>Google</b></summary>

```yaml
providerOptions:
  google:
    thinkingConfig:
      thinkingLevel: high           # low|high (Gemini 3)
      thinkingBudget: 8000          # Gemini 2.5
    structuredOutputs: true
```
</details>

<details>
<summary><b>DeepSeek</b></summary>

```yaml
providerOptions:
  deepseek:
    thinking:
      type: enabled                 # Enables reasoning mode
```
</details>

## Cascade Resolution

LLMix resolves configs through a 5-level cascade:

| Level | Pattern | Use Case |
|-------|---------|----------|
| 1 | `{scope}:{module}:{userId}:{profile}` | User-specific override |
| 2 | `{scope}:{module}:_:{profile}` | Module profile |
| 3 | `{scope}:_default:_:{profile}` | Scope-wide fallback |
| 4 | `_default:_default:_:{profile}` | Global profile |
| 5 | `_default:_default:_:_base` | Ultimate base |

**Example:** `client.call({ profile: 'hrkg:extraction', userId: 'user123' })`

```
1. default:hrkg:user123:extraction:v1  → Not found
2. default:hrkg:_:extraction:v1        → Found! → config/llm/hrkg/extraction.v1.yaml
```

## API Reference

### LLMConfigLoader

```typescript
const loader = createLLMConfigLoader({
  configDir: string;               // Required: config directory
  redisUrl?: string;               // Enables Redis layer
  cacheSize?: number;              // LRU size (default: 100)
  cacheTtlSeconds?: number;        // LRU TTL (default: 21600)
  redisTtlSeconds?: number;        // Redis TTL (default: 86400)
  defaultScope?: string;           // Default: "default"
  logger?: LLMConfigLoaderLogger;  // Custom logger
});

await loader.init();               // Connect Redis, validate base config
const stats = loader.getStats();   // { localCache, redisAvailable }
await loader.close();              // Cleanup connections
```

### LLMClient

```typescript
const client = createLLMClient({
  loader: LLMConfigLoader;
  defaultScope?: string;
  telemetry?: LLMixTelemetryProvider;  // Optional telemetry
});

// Make LLM call
const response = await client.call({
  profile: string;                 // "module:profile" or "profile"
  messages: unknown[];             // AI SDK message format
  scope?: string;
  userId?: string;
  version?: number;
  overrides?: RuntimeOverrides;
  telemetry?: TelemetryContext;
});

// Get config + capabilities without calling LLM
const { config, capabilities } = await client.getResolvedConfig({
  profile: 'hrkg:extraction',
});

if (capabilities.supportsOpenAIBatch) {
  // Use OpenAI Batch API for cost savings
}
```

### Runtime Overrides

Override config values at call time:

```typescript
const response = await client.call({
  profile: 'hrkg:extraction',
  messages: [...],
  overrides: {
    model: 'gpt-4o-mini',                    // Switch model
    common: { temperature: 0.3 },            // Adjust params
    providerOptions: {
      openai: { reasoningEffort: 'high' }
    },
  },
});
```

### Response Types

```typescript
interface LLMResponse {
  content: string;              // Generated text
  model: string;                // Model used
  provider: Provider;           // openai|anthropic|google|deepseek
  usage: LLMUsage;              // { inputTokens, outputTokens, totalTokens }
  config: ResolvedLLMConfig;    // Resolved config metadata
  success: boolean;
  error?: string;
}

interface ConfigCapabilities {
  provider: Provider;
  isProprietary: boolean;
  supportsOpenAIBatch: boolean;
}
```

## Telemetry Integration

LLMix supports optional telemetry via dependency injection:

```typescript
import { createLLMClient, type LLMixTelemetryProvider } from 'llmix';

const telemetryProvider: LLMixTelemetryProvider = {
  async trackLLMCall(event) {
    // Send to PostHog, Langfuse, Datadog, etc.
    await posthog.capture('llm_call', {
      model: event.model,
      provider: event.provider,
      inputTokens: event.inputTokens,
      outputTokens: event.outputTokens,
      latencyMs: event.latencyMs,
      success: event.success,
    });
  },

  calculateCost(model, inputTokens, outputTokens) {
    // Return cost breakdown or null
    return { inputCostUsd: 0.001, outputCostUsd: 0.002, totalCostUsd: 0.003 };
  }
};

const client = createLLMClient({ loader, telemetry: telemetryProvider });
```

## Error Handling

```typescript
import { ConfigNotFoundError, InvalidConfigError, SecurityError } from 'llmix';

try {
  const response = await client.call({ profile: 'unknown:profile', messages });
} catch (error) {
  if (error instanceof ConfigNotFoundError) {
    // Config not found in cascade - check file exists
  } else if (error instanceof InvalidConfigError) {
    // YAML schema validation failed - check config format
  } else if (error instanceof SecurityError) {
    // Path traversal or invalid input detected
  }
}
```

## Validation

All inputs are validated against strict patterns:

| Input | Pattern | Valid Examples |
|-------|---------|----------------|
| module | `^(_default\|[a-z][a-z0-9_]{0,63})$` | `hrkg`, `memobase`, `_default` |
| profile | `^(_base[a-z0-9_]*\|[a-z][a-z0-9_]{0,63})$` | `extraction`, `_base`, `_base_low` |
| scope | `^(_default\|[a-z][a-z0-9_-]{0,63})$` | `default`, `staging`, `production` |
| userId | `^[a-zA-Z0-9_-]{1,64}$` | `user123`, `_`, `user-abc` |
| version | `1-9999` | `1`, `2`, `100` |

## Exported Utilities

```typescript
// Zod schemas for external validation
export { LLMConfigSchema, CommonParamsSchema, ProviderOptionsSchema };

// Validation functions
export { validateModule, validateProfile, validateScope, validateUserId, validateVersion };

// Path utilities (for custom loaders)
export { buildConfigFilePath, verifyPathContainment };

// LRU cache (standalone use)
export { LRUCache };
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key |
| `ANTHROPIC_API_KEY` | For Anthropic | Anthropic API key |
| `GOOGLE_GENERATIVE_AI_API_KEY` | For Google | Google AI API key |
| `DEEPSEEK_API_KEY` | For DeepSeek | DeepSeek API key |

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`bun test`)
4. Run linting (`bun run check`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

[MIT](LICENSE) - see LICENSE file for details.

## Related

- [AI SDK](https://ai-sdk.dev/) - The underlying LLM abstraction layer
- [Promptix](https://github.com/sno-ai/promptix) - Three-tier prompt loading with Redis caching
