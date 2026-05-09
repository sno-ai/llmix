# LLMix Coding Reference

LLMix is a cross-runtime LLM orchestration library. The stable runtime center
is the neutral pipeline API. The preferred production config path is the
Config Registry. Python and TypeScript config authoring now use MDA Source
Mode through installed packages: Python uses `snoai-mda-config` from PyPI and
TypeScript uses `@snoai/mda-config` from NPM. Rust uses the same MDA Source
Mode authoring contract through its crate-local loader.

## Public Contract

### Python exports

- `CallPipeline`
- `PipelineConfig`
- `CallInput`
- `CallResponse`
- `ProviderResult`
- `ProviderError`
- `LLMUsage`
- `ConfigRegistryManager`
- `ConfigRegistryPublisher`
- `ConfigRegistryPublishOptions`
- `PublishedRevision`
- `MdaConfigLoadOptions`
- `build_mda_config_file_path(config_dir, module, preset)` path helper
- `load_mda_config(path)` low-level helper
- `load_mda_config_preset(name, base_dir)` low-level helper
- `load_mda_config_from_file(config_dir, module, preset)` low-level helper
- `openai_dispatch()`, `anthropic_dispatch()`, `gemini_dispatch()`,
  `novita_dispatch()`, `openrouter_dispatch()`, `sno_gpu_dispatch()`

### TypeScript exports

- `CallPipeline`
- `PipelineConfig`
- `CallInput`
- `CallResponse`
- `ProviderResult`
- `ProviderError`
- `ConfigRegistryManager`
- `ConfigRegistryPublisher`
- `PublishedRevision`
- `loadMdaConfig(path)` low-level helper
- `loadMdaConfigPreset(name, baseDir)` low-level helper
- `loadMdaConfigFromFile(configDir, module, preset)` low-level helper
- `buildMdaConfigFilePath(configDir, module, preset)` path helper
- `openaiDispatch()`, `anthropicDispatch()`, `geminiDispatch()`,
  `openrouterDispatch()`, `snoGpuDispatch()`

### Rust exports

- `CallPipeline`
- `PipelineConfig`
- `CallInput`
- `ProviderResult`
- `LlmUsage`
- `ConfigRegistryManager`
- `ConfigRegistryPublisher`
- `resolve_config_dir(options)`
- `load_config(path)` low-level MDA helper
- `load_config_preset(name, base_dir)` low-level MDA helper

## Quick Start

### Python

```python
from llmix import CallInput, CallPipeline, KeyPool, PipelineConfig, openai_dispatch

pipeline = CallPipeline(PipelineConfig(dispatch=openai_dispatch()))
pipeline.set_key_pool("openai", KeyPool(["sk-live-1", "sk-live-2"]))

response = await pipeline.call(
    CallInput(
        config={
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "common": {"temperature": 0.2, "max_output_tokens": 512},
            "caching": {"strategy": "memory"},
        },
        messages=[{"role": "user", "content": "Summarize this report."}],
    )
)
```

### TypeScript

```typescript
import { CallPipeline, KeyPool, TwoTierCache, openaiDispatch } from "@snoai/llmix";

const pipeline = new CallPipeline({
  dispatch: openaiDispatch(),
  responseCache: new TwoTierCache("redis-or-memory", process.env.REDIS_URL),
});
pipeline.setKeyPool("openai", new KeyPool(["sk-live-1", "sk-live-2"]));

const response = await pipeline.call({
  config: {
    provider: "openai",
    model: "gpt-4.1-mini",
    common: { temperature: 0.2, maxOutputTokens: 512 },
    caching: { strategy: "redis-or-memory" },
  },
  messages: [{ role: "user", content: "Summarize this report." }],
});
```

## Config Registry

The production config path is the LLMix Config Registry:

- TypeScript authoring uses `.mda` Source Mode presets
- publishing creates immutable snapshot revisions
- `current.json` is the only live switch
- runtime reads resolved JSON snapshot artifacts, not mutable authoring MDA

That design gives the runtime a single source of truth and removes editable
MDA parsing from the hot path.

### Python

```python
from llmix import ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir

root = resolve_config_dir().config_dir
ConfigRegistryPublisher(root).publish()

manager = ConfigRegistryManager.open(root)
config = manager.get_preset("search", "summary")
```

### TypeScript

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  resolveConfigDir,
} from "@snoai/llmix";

const { configDir } = resolveConfigDir();
await new ConfigRegistryPublisher(configDir).publish();

const manager = await ConfigRegistryManager.open(configDir);
const config = await manager.getPreset("search", "summary");
```

### Rust

```rust
use llmix_rs::{ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir};

let root = resolve_config_dir(None)?.config_dir;
ConfigRegistryPublisher::new(&root)?.publish()?;

let mut manager = ConfigRegistryManager::open(&root)?;
let config = manager.get_preset("search", "summary")?;
```

Managers expose the active revision and reload health metadata, so runtime
services can report which snapshot is live and whether a reload failed.

## Low-Level MDA Config Loading

Python, TypeScript, and Rust expose direct MDA config helpers. They are useful
for authoring, tests, and migration tools, but the registry remains the
recommended production config path.

### Python

```python
from llmix import load_mda_config, load_mda_config_preset

config = load_mda_config("./config/llm/search/summary.mda")
preset = load_mda_config_preset("summary", "./config/llm/search")
```

### TypeScript

```typescript
import { loadMdaConfig, loadMdaConfigPreset } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/search/summary.mda");
const preset = await loadMdaConfigPreset("summary", "./config/llm/search");
```

### Rust

```rust
use llmix_rs::{load_config, load_config_preset};

let config = load_config("./config/llm/search/summary.mda")?;
let preset = load_config_preset("summary", "./config/llm/search")?;
```

Loader behavior:

- Uses installed MDA parser packages for Source Mode parsing: `snoai-mda-config`
  in Python and `@snoai/mda-config` in TypeScript. Rust uses crate-local
  Source Mode frontmatter parsing for the same `metadata.snoai-llmix`
  contract.
- Reads LLMix-specific settings from `metadata.snoai-llmix`.
- Rejects `.yaml` and `.yml` preset paths.
- Preserves each runtime's public naming convention:
  - Python returns snake_case fields such as `max_output_tokens`,
    `keep_thinking_output`, `provider_options`.
  - TypeScript returns camelCase fields such as `maxOutputTokens`,
    `keepThinkingOutput`, `providerOptions`.
  - Rust returns snake_case fields such as `max_output_tokens`,
    `keep_thinking_output`, `provider_options`.
- Preset helpers resolve `{baseDir}/{preset}.mda` and then delegate to the same
  primary loader path.

Current recommendation:

- use `ConfigRegistryManager` for runtime preset lookup in new service code
- keep direct MDA helpers for authoring, tests, migration, or one-off tools
- avoid coupling production runtime behavior to mutable live authoring files

## Pipeline Flow

Each `CallPipeline.call()` runs the same 19-step orchestration flow:

1. Kill switch check
2. Caller-supplied config validation boundary
3. L1 cache lookup
4. L2 cache lookup
5. Circuit breaker gate
6. Singleflight deduplication
7. Cross-process lock
8. Adaptive semaphore
9. API key selection
10. Provider kwargs transform
11. Provider dispatch
12. Semaphore feedback
13. Lock release
14. Key-pool feedback
15. Circuit-breaker feedback
16. Retry decision
17. Thinking strip
18. Cache write
19. Telemetry hook

Ordering guarantees:

- Cache hits bypass resilience.
- OPEN circuit breakers fail before singleflight admission.
- Cache stores raw provider output; thinking stripping is a read-time transform.
- `keepThinkingOutput` does not affect cache key generation.

## Core Types

### Python

```python
@dataclass
class CallInput:
    config: dict[str, Any]
    messages: list[dict[str, Any]]
    singleflight_key: str | None = None

@dataclass
class CallResponse:
    content: str
    model: str
    provider: str
    usage: LLMUsage
    success: bool
    error: str | None = None
    thinking_content: str | None = None
    cache_hit: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
```

### TypeScript

```typescript
interface CallInput {
  config: LLMConfig;
  messages: unknown[];
  singleflightKey?: string;
}

interface CallResponse {
  content: string;
  model: string;
  provider: string;
  usage: LLMUsage;
  success: boolean;
  error?: string;
  thinkingContent?: string;
  cacheHit?: "l1" | "l2";
  toolCalls?: unknown[];
}
```

## Config Shape

Required fields:

- `provider`
- `model`

Common fields by runtime:

- Python `common`: `temperature`, `top_p`, `max_output_tokens`, `seed`,
  `response_format`, `enable_thinking`, `keep_thinking_output`
- TypeScript `common`: `temperature`, `topP`, `maxOutputTokens`, `seed`,
  `enableThinking`, `keepThinkingOutput`

Provider options:

- Python uses `provider_options`
- TypeScript uses `providerOptions`

Built-in dispatcher coverage:

- OpenAI
- Anthropic
- Gemini / Google
- OpenRouter for DeepSeek-family routing
- Sno GPU

## Cache Contract

- Cache key prefix: `llmix:resp:`
- Key material is canonical JSON plus SHA-256 hashing
- Python and TypeScript must generate identical keys for equivalent requests
- L1 cache is in-memory
- L2 cache is Redis when configured
- `redis-or-memory` keeps L2 intent even when Redis is unavailable, but the
  runtime degrades to L1-only behavior

## Resilience Contract

- Circuit breaker scope: `(provider, base_url)`
- Auth failures (`401`, `403`) do not trip the circuit breaker
- Retryable failures include rate limits, `5xx`, and network errors
- Key pools rotate on retry and mark keys dead on auth failures
- Adaptive semaphore applies AIMD-style feedback on provider throughput

Kill switch state directory resolution:

1. `LLMIX_STATE_DIR`
2. `XDG_STATE_HOME/llmix`
3. `~/.local/state/llmix`

## HTTP/2

- Python ships a real provider transport registry in `python/llmix/http2.py`
  using `httpx[http2]`.
- TypeScript ships provider transport intent metadata in
  `typescript/src/http2.ts`.
- OpenAI TypeScript transport remains a documented stub until upstream AI SDK
  transport hooks are sufficient; that gap is not a blocker for the direct-loader cleanup.

## Environment Variables

Authentication:

- `OPENAI_API_KEY` / `OPENAI_KEYS`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `OPENROUTER_API_KEY`
- `SNO_LLM_API_KEY`

Runtime:

- `REDIS_URL`
- `GPU_BASE_URL`
- `LLMIX_STATE_DIR`
- `XDG_STATE_HOME`
- `LLM_GLOBAL_CONCURRENCY`

## Verification

Useful commands:

```bash
bun test
uv run pytest tests/python/test_pipeline.py tests/python/test_config_loader.py
bunx tsc -p tsconfig.check.json
uv run pyright
```
