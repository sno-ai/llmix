# LLMix Python Guide

Install the package from PyPI:

```bash
pip install sno-llmix
```

The package name is `sno-llmix`. The import path is `llmix`.

For Redis-backed response cache:

```bash
pip install "sno-llmix[redis]"
```

## Quick Start

```python
import asyncio
import os

from llmix import (
    CallInput,
    CallPipeline,
    KeyPool,
    PipelineConfig,
    TwoTierCache,
    openai_dispatch,
)


async def main() -> None:
    pipeline = CallPipeline(
        PipelineConfig(
            dispatch=openai_dispatch(),
            response_cache=TwoTierCache("memory"),
        )
    )
    pipeline.set_key_pool("openai", KeyPool([os.environ["OPENAI_API_KEY"]]))

    response = await pipeline.call(
        CallInput(
            config={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "common": {"temperature": 0.7, "max_output_tokens": 256},
                "caching": {"strategy": "memory"},
            },
            messages=[
                {"role": "user", "content": "In one sentence, what is LLMix?"}
            ],
        )
    )

    print(response.content)
    print(f"cache_hit={response.cache_hit} usage={response.usage}")
    await pipeline.close()


asyncio.run(main())
```

Run it:

```bash
OPENAI_API_KEY=sk-... python quickstart.py
```

## Redis Cache

Use `redis-or-memory` when you want Redis in deployed services but still want a
local fallback when Redis is unavailable.

```python
import os

from llmix import PipelineConfig, TwoTierCache, openai_dispatch

config = PipelineConfig(
    dispatch=openai_dispatch(),
    response_cache=TwoTierCache(
        "redis-or-memory",
        redis_url=os.environ.get("REDIS_URL"),
        max_items=2048,
        ttl_seconds=3600,
    ),
)
```

For strict Redis mode, use `TwoTierCache("redis", redis_url=...)`. It raises if
no Redis URL is configured.

## Key Pools

Register one pool per provider:

```python
from llmix import KeyPool, load_keys_from_env

pipeline.set_key_pool("openai", load_keys_from_env("openai"))

# Or explicitly:
pipeline.set_key_pool("openai", KeyPool(["sk-live-1", "sk-live-2"]))
```

`load_keys_from_env("openai")` checks `OPENAI_KEYS` first, then
`OPENAI_API_KEY`. `OPENAI_KEYS` is comma-separated.

If you build a Python dispatch helper with a prebuilt `client=...`, do not
register a key pool for that provider. The client already owns the API key.
LLMix will reject that combination to avoid marking the wrong key dead.

## Config Registry

Use the registry when presets are part of a running service:

```python
from llmix import ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir

root = resolve_config_dir().config_dir
ConfigRegistryPublisher(root).publish()

manager = ConfigRegistryManager.open(root)
config = manager.get_preset("search", "summary")
print(manager.available_presets())
```

Then pass the resolved config into the pipeline:

```python
response = await pipeline.call(
    CallInput(
        config=config,
        messages=[{"role": "user", "content": "Summarize this."}],
    )
)
```

## Direct MDA Loading

Direct loaders are useful for tests, authoring tools, and migrations:

```python
from llmix import load_mda_config, load_mda_config_preset

config = load_mda_config("./config/llm/search/summary.mda")
preset = load_mda_config_preset("summary", "./config/llm/search")
```

For production runtime code, prefer `ConfigRegistryManager`.

## Custom Dispatch

The dispatch function is just the provider call. That is the design center.

```python
from llmix import LLMUsage, ProviderResult


async def my_dispatch(ctx):
    result = await my_client.chat(
        model=ctx.model,
        messages=ctx.messages,
        api_key=ctx.api_key,
        **ctx.kwargs,
    )
    return ProviderResult(
        content=result.text,
        model=ctx.model,
        usage=LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0),
    )
```

Wire it into the pipeline:

```python
pipeline = CallPipeline(PipelineConfig(dispatch=my_dispatch))
```

## Public Runtime Knobs

```python
PipelineConfig(
    dispatch=openai_dispatch(),
    max_retries=3,
    retry_base_ms=1000,
    retry_max_delay_ms=30000,
    circuit_breaker_threshold=3,
    circuit_breaker_cooldown_seconds=30.0,
    semaphore_initial=32,
    semaphore_min=4,
    response_cache=TwoTierCache("memory"),
)
```

Most services should start with defaults. Tune only after real traffic shows a
specific pressure point.
