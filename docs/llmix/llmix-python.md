# LLMix Python Guide

Read this after the README if your application runtime is Python. This page
covers Python install, runtime calls, config shape, provider coverage, direct
MDA loading, and the shared official registry release flow.

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

## Mental Model

LLMix has five pieces:

| Piece | What it does |
| --- | --- |
| `CallPipeline` | Runs one LLM call through cache, retries, key rotation, circuit breaker, singleflight, and dispatch. |
| `PipelineConfig` | Wires the dispatch function and runtime knobs. |
| `CallInput` | Carries the resolved model config and chat messages. |
| `KeyPool` | Rotates API keys per provider and marks dead keys on auth failures. |
| `TwoTierCache` | Uses in-process memory as L1 and optional Redis as L2. |

LLMix is not a replacement for OpenAI, Anthropic, LiteLLM, or your own provider
client. It wraps the call site where those SDKs are used.

## Config Shape

Python uses snake_case fields in direct config objects:

```python
{
    "provider": "openai",
    "model": "gpt-4o-mini",
    "common": {"temperature": 0.2, "max_output_tokens": 512},
    "caching": {"strategy": "memory", "ttl": 3600},
    "provider_options": {
        "openai": {"reasoning_effort": "medium"},
    },
}
```

MDA source files use camelCase under `metadata.snoai-llmix`; the Python loader
normalizes known fields into this snake_case runtime shape.

## Provider Coverage

| Provider family | Python helper |
| --- | --- |
| OpenAI-compatible | `openai_dispatch()` |
| Anthropic | `anthropic_dispatch()` |
| Gemini | `gemini_dispatch()` |
| OpenRouter | `openrouter_dispatch()` |
| DeepInfra | `deepinfra_dispatch()` |
| Novita | `novita_dispatch()` |
| Together | `together_dispatch()` |
| SNO GPU | `sno_gpu_dispatch()` |

Provider helpers use optional SDKs. Install only the provider SDKs you call.

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

## Environment Variables

Key pools can be loaded from environment variables. Provider names normalize
hyphens to underscores and uppercase.

| Provider | Multi-key variable | Single-key fallback |
| --- | --- | --- |
| OpenAI | `OPENAI_KEYS` | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_KEYS` | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_KEYS` | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_KEYS` | `OPENROUTER_API_KEY` |
| DeepInfra | `DEEPINFRA_KEYS` | `DEEPINFRA_API_KEY` |
| Novita | `NOVITA_KEYS` | `NOVITA_API_KEY` |
| Together | `TOGETHER_KEYS` | `TOGETHER_API_KEY` |
| SNO GPU | `SNO_GPU_KEYS` | `SNO_GPU_API_KEY` |

`*_KEYS` is comma-separated. If both variables exist, `*_KEYS` wins.

## Config Registry

Production apps should publish MDA presets into the official registry layout:

```text
config/llm/
  source/
    <module>/
      <preset>.mda
  current.json
  compiled/
```

`source/` is edited by people. `current.json` and `compiled/` are generated by
LLMix. Keep the trust anchor outside `config/llm`.

The official publisher and checker commands ship with the TypeScript npm
package. A Python app repo can keep Python as the service runtime and install
`@snoai/llmix` only in release tooling:

```bash
npm install --save-dev @snoai/llmix
```

The fixed release flow is:

1. Put presets in `config/llm/source/<module>/<preset>.mda`.
2. Run MDA CLI validation, integrity, signing, verification, and release
   prepare.
3. Run `llmix publish-registry`.
4. Generate `config/llm/current.json` and `config/llm/compiled/`.
5. Run MDA CLI release finalize and doctor checks.
6. Store the trust anchor outside `config/llm`.
7. Prove the registry with `llmix check-registry` before deploying the release.

Use one normal example everywhere:

```text
config/llm/source/search_summary/openai_fast.mda
```

Create and gate the source preset with MDA CLI:

```bash
mkdir -p config/llm/source/search_summary release deploy

mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out config/llm/source/search_summary/openai_fast.mda

mda validate config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --json

mda integrity compute config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --write \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/trust-policy.json \
  --json

mda sign config/llm/source/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json

mda verify config/llm/source/search_summary/openai_fast.mda \
  --target source \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json
```

Publish, finalize, doctor, and prove the registry:

```bash
llmix publish-registry \
  --root config/llm \
  --release-plan release/plan.json \
  --revision 2026-05-14T000000Z \
  --policy release/trust-policy.json \
  --did-document release/did.json \
  --root-did did:web:config.example.com \
  --root-key-id did:web:config.example.com#release \
  --root-key-file release/did-web-private-key.pem \
  --json

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/compiled/2026-05-14T000000Z/registry-root.json \
  --release-plan release/plan.json \
  --policy release/trust-policy.json \
  --derive-root-digest \
  --minimum-revision 2026-05-14T000000Z \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source config/llm/source \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

llmix check-registry \
  --root config/llm \
  --trust deploy/llmix-trust.json \
  --preset search_summary/openai_fast \
  --did-document release/did.json \
  --tamper-proof \
  --json
```

Do not build a Python-local compiler, publisher, or custom directory layout.
MDA CLI gates the release, LLMix publishes and checks the registry, and the app
keeps the trust anchor outside `config/llm`.

## Direct MDA Loading

Direct loaders are useful for tests, editing tools, and migrations:

```python
from llmix import load_mda_config, load_mda_config_preset

config = load_mda_config("./config/llm/source/search_summary/openai_fast.mda")
preset = load_mda_config_preset("openai_fast", "./config/llm/source/search_summary")
```

## MDA Source Presets

MDA is the source format for presets. Use it when model choice, provider
options, cache policy, timeout policy, tags, and rollout metadata should be
reviewed as source files instead of hidden in application code.

The LLMix-specific data lives under `metadata.snoai-llmix`. MDA-owned mechanism
fields such as `requires`, `integrity`, and `signatures` stay at the top level
and are handled by the MDA parser.

```md
---
name: openai_fast
title: OpenAI Fast Search Summary
description: Fast OpenAI preset for search summaries.
tags:
  - search
  - production
requires:
  network: public
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 512
      maxRetries: 2
    providerOptions:
      openai:
        reasoningEffort: medium
        textVerbosity: low
    timeout:
      totalTime: 45
      streamFirstChunkTime: 12
    caching:
      strategy: redis-or-memory
      ttl: 3600
      maxItems: 2000
    tags:
      - search
      - production
---

Summarize search results for a research workflow.
```

`metadata.snoai-llmix` is strict. Unknown keys are rejected unless a field is
documented as a provider-specific pass-through record.

| Key | Required | Purpose |
| --- | --- | --- |
| `common` | yes | Provider, model, and portable generation parameters. |
| `providerOptions` | no | Provider-specific options such as OpenAI reasoning effort or Anthropic thinking. |
| `timeout` | no | Per-call timeout hints in seconds. |
| `description` | no | Overrides the top-level MDA description in the projected runtime config. |
| `deprecated` | no | Marks a preset as deprecated for tooling. |
| `tags` | no | Overrides top-level MDA tags in the projected runtime config. |
| `caching` | no | Response cache strategy and TTL. |
| `bypassGateway` | no | Compatibility flag for deployments that route around a gateway. |

`common.provider` and `common.model` are required. Supported providers are
`openai`, `anthropic`, `google`, `deepseek`, `openrouter`, `deepinfra`,
`novita`, `together`, and `sno-gpu`.

The reserved semantic payload type for signed LLMix presets is:

```text
application/vnd.snoai-llmix.preset+json
```

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

## Timeouts and Cancellation

Treat `timeout.total_time` in MDA config as a runtime budget, not as automatic
transport cancellation. The Python pipeline does not wrap `dispatch` in
`asyncio.wait_for`, and it does not force-cancel provider requests before retrying.

The built-in provider dispatchers use provider SDK or HTTP client defaults, but
those defaults are not derived from `config["timeout"]["total_time"]`. If your
service needs a hard timeout, put it at the provider transport layer:

```python
import httpx

from llmix import LLMUsage, ProviderResult


async def my_dispatch(ctx):
    total_time = ctx.config.get("timeout", {}).get("total_time", 120)
    timeout = httpx.Timeout(total_time, connect=min(10.0, total_time))

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.example.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {ctx.api_key}"},
            json={
                "model": ctx.model,
                "messages": ctx.messages,
                **ctx.kwargs,
            },
        )
        response.raise_for_status()

    data = response.json()
    usage = data.get("usage", {})
    return ProviderResult(
        content=data["choices"][0]["message"]["content"],
        model=data.get("model", ctx.model),
        usage=LLMUsage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        ),
    )
```

Do not rely on caller-side timeout wrappers alone unless cancellation reaches the
real network request. Before retrying a timed-out attempt, make sure the previous
provider request has been cancelled, closed, or allowed to finish; otherwise the
service can create duplicate in-flight generations.

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

## Boundaries

Good fits for LLMix:

- shared model presets
- cache policy
- retry and concurrency defaults
- API key rotation
- provider kwargs normalization

Keep these in product code:

- user authorization
- billing policy
- product-specific prompt branching
- provider account ownership rules
