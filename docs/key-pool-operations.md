# Key Pool Operations

`KeyPool` is for services that have more than one API key for the same
provider. LLMix rotates keys, retries on rate-limit pressure, and marks keys
dead when the provider says the credentials are invalid.

This is a public feature. It is useful when you run agents, batch jobs, evals,
or multi-tenant AI tools where one key is too fragile.

## Behavior

| Event | What LLMix does |
| --- | --- |
| Normal request | Selects the next live key for that provider. |
| HTTP 429 | Rotates to another key and retries when the retry policy allows it. |
| HTTP 401 or 403 | Marks that key dead and continues with remaining keys. |
| All keys dead | Raises a key-pool exhaustion error. |

Keys rotate in round-robin order. Dead keys stay dead for the lifetime of the
pool. Create a fresh pool after replacing credentials.

## Environment Variables

`load_keys_from_env(provider)` checks a multi-key variable first, then a single
key fallback:

| Provider | Multi-key variable | Single-key fallback |
| --- | --- | --- |
| `openai` | `OPENAI_KEYS` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_KEYS` | `ANTHROPIC_API_KEY` |
| `gemini` | `GEMINI_KEYS` | `GEMINI_API_KEY` |
| `openrouter` | `OPENROUTER_KEYS` | `OPENROUTER_API_KEY` |
| `deepinfra` | `DEEPINFRA_KEYS` | `DEEPINFRA_API_KEY` |
| `novita` | `NOVITA_KEYS` | `NOVITA_API_KEY` |
| `together` | `TOGETHER_KEYS` | `TOGETHER_API_KEY` |
| `sno-gpu` | `SNO_GPU_KEYS` | `SNO_GPU_API_KEY` |

`*_KEYS` is comma-separated:

```bash
OPENAI_KEYS=sk-org-a-1,sk-org-a-2,sk-org-b-1
```

## Python

```python
from llmix import CallPipeline, KeyPool, PipelineConfig, load_keys_from_env, openai_dispatch

pipeline = CallPipeline(PipelineConfig(dispatch=openai_dispatch()))
pipeline.set_key_pool("openai", load_keys_from_env("openai"))

# Or explicitly:
pipeline.set_key_pool("openai", KeyPool(["sk-live-1", "sk-live-2"]))
```

Check pool health directly:

```python
pool = load_keys_from_env("openai")
print(pool.alive_count, pool.total_count, pool.is_exhausted())
```

If a dispatch helper was built with a prebuilt `client=...`, skip key-pool
registration for that provider. The client already owns its API key, so the
pool key would not actually authenticate the request.

## TypeScript

```typescript
import {
  CallPipeline,
  KeyPool,
  loadKeysFromEnv,
  openaiDispatch,
} from "@snoai/llmix";

const pipeline = new CallPipeline({ dispatch: openaiDispatch() });
pipeline.setKeyPool("openai", loadKeysFromEnv("openai"));

// Or explicitly:
pipeline.setKeyPool("openai", new KeyPool(["sk-live-1", "sk-live-2"]));
```

Check pool health directly:

```typescript
const pool = loadKeysFromEnv("openai");
console.log(pool.aliveCount, pool.totalCount, pool.isExhausted());
```

## Rust

```rust
use llmix_rs::{load_keys_from_env, CallPipeline, KeyPool, PipelineConfig};

let pipeline = CallPipeline::new(PipelineConfig::new(my_dispatch))?;
pipeline.set_key_pool("openai", load_keys_from_env("openai")?);

// Or explicitly:
pipeline.set_key_pool(
    "openai",
    KeyPool::new(vec!["sk-live-1".to_owned(), "sk-live-2".to_owned()])?,
);
```

Check pool health directly:

```rust
let pool = load_keys_from_env("openai")?;
println!(
    "alive={} total={} exhausted={}",
    pool.alive_count(),
    pool.total_count(),
    pool.is_exhausted()
);
```

## Operating Notes

- Pool keys by provider, not by model. The pipeline uses the config provider
  name to choose the pool.
- Use separate pools for separate provider accounts when you want separate
  failure domains.
- Rotate real credentials outside the process, then construct a fresh
  `KeyPool`.
- Do not use key pools to bypass a provider's terms or account limits. They are
  for reliability and clean credential failover.
