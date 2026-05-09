# LLMix

[![npm version](https://img.shields.io/npm/v/@snoai/llmix.svg?label=npm&labelColor=3b3b3b&color=cb3837)](https://www.npmjs.com/package/@snoai/llmix)
[![PyPI](https://img.shields.io/pypi/v/sno-llmix.svg?label=pypi&labelColor=3b3b3b&color=3775a9)](https://pypi.org/project/sno-llmix/)
[![crates.io](https://img.shields.io/crates/v/llmix-rs.svg?label=crates.io&labelColor=3b3b3b&color=d67b2b)](https://crates.io/crates/llmix-rs)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-ffd43b.svg?labelColor=306998)](https://www.python.org/downloads/)
[![TypeScript 5.0+](https://img.shields.io/badge/TypeScript-5.0%2B-3178c6.svg?labelColor=3b3b3b)](https://www.typescriptlang.org/)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83%2B-b7410e.svg?labelColor=3b3b3b)](https://www.rust-lang.org/)
[![License: Apache--2.0](https://img.shields.io/badge/License-Apache--2.0-97ca00.svg?labelColor=3b3b3b)](../../LICENSE)

Read in other languages: [English](../../README.md) · **中文** · [Deutsch](README.de.md) · [Español](README.es.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md)

> 面向 Python、TypeScript 和 Rust 的配置驱动 LLM 调用层。
> 继续使用你原来的 SDK。把模型行为放进 MDA preset。把缓存、重试、密钥轮换和发布控制包在调用外面。

LLMix 位于你的产品和模型供应商 SDK 之间。

它不要求你重写 OpenAI、Anthropic、Gemini、LiteLLM、AI SDK 或自定义客户端代码。它只包住那次调用。那些重复但必须做好的部分放在外层：响应缓存、熔断器、密钥池、singleflight、重试策略、自适应并发、供应商 kwargs，以及 MDA 配置加载。

模型不再是埋在业务代码里的硬编码字符串。它变成数据。修改一个 preset，发布一份 registry snapshot，重新加载服务，下一次请求就可以使用不同的供应商或模型。常见的模型切换不需要重新部署。

事情就是这样。很小的一层。把容易割手的边缘磨平。

---

## 为什么需要它

2026 年的 AI 产品，通常不是因为一次 SDK 调用太难而出问题。

真正出问题的是调用周围的空间。某个 key 被限流。某个供应商变慢。两百个用户同时问同一个问题。一次模型切换需要部署。缓存 key 因为一个看不见的参数而不一致。一个服务用 Python，另一个服务用 TypeScript，Rust worker 也必须遵守同一份契约。

LLMix 处理的就是这一段。你的应用到模型之间的信号链路。

Prompt 仍然由你掌控。SDK 仍然由你掌控。LLMix 负责 harness。

---

## 安装

| Runtime | Package | Import path |
|---------|---------|-------------|
| TypeScript | `npm install @snoai/llmix` | `@snoai/llmix` |
| Python | `pip install sno-llmix` | `llmix` |
| Rust | `cargo add llmix-rs` | `llmix_rs` |

Python 在 PyPI 上使用 `sno-llmix`，因为 `llmix` 已经被占用。导入路径仍然是 `llmix`。

供应商 helper 使用可选 SDK。只安装你实际调用的供应商客户端。

```bash
# TypeScript OpenAI-compatible helpers
npm install ai @ai-sdk/openai

# Python Redis cache support
pip install "sno-llmix[redis]"

# Rust OpenAI helper and Redis cache
cargo add llmix-rs --features providers-openai,redis
```

---

## 文档

- [使用参考](../llmix-usage-ref.md)
- [TypeScript 指南](../llmix-typescript.md)
- [Python 指南](../llmix-python.md)
- [Rust 指南](../llmix-rust.md)
- [Secure LLMix configuration](../secure-llmix-configuration.md)
- [Key pool operations](../key-pool-operations.md)

---

## 一眼看懂

![LLMix wraps your existing LLM SDK stack with MDA config, cache, resilience, and key-pool primitives.](../images/llmix-wraps-sdk.png)

LLMix 一次包住一个供应商调用。

它不是 LiteLLM 意义上的 router。它更接近你在每个 agent、coder tool、抽取服务和内部 AI workflow 上线后都会反复重建的那层 harness。

---

## Quick Start

### TypeScript

```typescript
import {
  CallPipeline,
  KeyPool,
  TwoTierCache,
  openaiDispatch,
} from "@snoai/llmix";

const pipeline = new CallPipeline({
  dispatch: openaiDispatch(),
  responseCache: new TwoTierCache("memory"),
});

pipeline.setKeyPool("openai", new KeyPool([process.env.OPENAI_API_KEY!]));

const response = await pipeline.call({
  config: {
    provider: "openai",
    model: "gpt-4o-mini",
    common: { temperature: 0.2, maxOutputTokens: 512 },
    caching: { strategy: "memory" },
  },
  messages: [
    { role: "user", content: "Explain LLMix in one sentence." },
  ],
});

console.log(response.content);
await pipeline.close();
```

### Python

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
                "common": {"temperature": 0.2, "max_output_tokens": 512},
                "caching": {"strategy": "memory"},
            },
            messages=[
                {"role": "user", "content": "Explain LLMix in one sentence."}
            ],
        )
    )

    print(response.content)
    await pipeline.close()


asyncio.run(main())
```

### Rust

Rust 暴露同一套 pipeline 契约。OpenAI helper 通过 feature 开启。

```toml
[dependencies]
llmix-rs = { version = "2.0.0", features = ["providers-openai"] }
serde_json = "1"
tokio = { version = "1", features = ["macros", "rt"] }
```

```rust
use llmix_rs::{
    load_keys_from_env, CallInput, CallPipeline, OpenAiChatHelper, PipelineConfig,
};
use serde_json::json;

let pipeline = CallPipeline::new(PipelineConfig::new(OpenAiChatHelper::new()))?;
pipeline.set_key_pool("openai", load_keys_from_env("openai")?);

let response = pipeline
    .call(CallInput {
        config: json!({
            "provider": "openai",
            "model": "gpt-4o-mini",
            "common": { "temperature": 0.2, "max_output_tokens": 512 },
            "caching": { "strategy": "memory" }
        }),
        messages: vec![json!({
            "role": "user",
            "content": "Explain LLMix in one sentence."
        })],
        singleflight_key: None,
    })
    .await;
```

完整的 `main` 示例和 feature flags 见 [Rust 指南](../llmix-rust.md)。

---

## 每次调用外面会得到什么

![LLMix request pipeline from config and cache lookup through circuit breaker, singleflight, key-pool rotation, retry loop, dispatch, and telemetry.](../images/llmix-call-pipeline.png)

| Concern | What LLMix does |
|---------|-----------------|
| Response cache | L1 memory 加可选 Redis L2，使用跨 runtime 一致的规范化缓存 key |
| Key pools | 轮询选 key，遇到 429 轮换，遇到 401/403 自动剔除失效 key |
| Retries | 带 jitter 的指数退避，并遵守 `Retry-After` |
| Circuit breaker | 按 provider 和 effective base URL 作用域隔离 |
| Singleflight | 把相同的并发任务合并成一次上游请求 |
| Concurrency | AIMD 自适应 semaphore，由 rate-limit 反馈驱动 |
| Provider kwargs | 把 common config 转成供应商特定请求字段 |
| Thinking tokens | 可选提取 `<think>` 到规范化 response 对象 |
| Registry | 不可变 config snapshot，加一个 live `current.json` 指针 |

默认值应该保持无聊。等真实流量给你理由时再调。

---

## MDA Presets

![LLMix turns editable MDA presets into immutable registry snapshots that Python, TypeScript, and Rust runtimes can read consistently.](../images/llmix-mda-config.png)

LLMix 使用 MDA Source Mode 编写配置。人读的说明和 runtime 设置放在同一个文件里。runtime 只读取解析后的 JSON。

```mda
---
name: extraction
description: Entity extraction preset.
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-4o-mini
      temperature: 0.2
      maxOutputTokens: 512
    caching:
      strategy: redis-or-memory
    providerOptions:
      openai:
        reasoningEffort: medium
---
# extraction

Extract named entities. Return compact JSON.
```

在编写或测试时可以直接加载：

```typescript
import { loadMdaConfig } from "@snoai/llmix";

const config = await loadMdaConfig("./config/llm/search/extraction.mda");
```

```python
from llmix import load_mda_config

config = load_mda_config("./config/llm/search/extraction.mda")
```

```rust
use llmix_rs::load_config;

let config = load_config("./config/llm/search/extraction.mda")?;
```

生产服务建议使用 registry。

---

## Config Registry

可编辑的 MDA 文件适合人。运行中的服务需要更安静的东西。

LLMix Config Registry 会把 authoring 文件发布成不可变、内容寻址的 snapshot。runtime 代码读取 active snapshot，而不是可变的源码目录。

```text
config/llm/
  authoring/
    search/
      extraction.mda
  snapshots/
    2026-05-09T000000Z-...
  current.json
```

```python
from llmix import ConfigRegistryManager, ConfigRegistryPublisher, resolve_config_dir

root = resolve_config_dir().config_dir
ConfigRegistryPublisher(root).publish()

manager = ConfigRegistryManager.open(root)
config = manager.get_preset("search", "extraction")
```

```typescript
import {
  ConfigRegistryManager,
  ConfigRegistryPublisher,
  resolveConfigDir,
} from "@snoai/llmix";

const { configDir } = resolveConfigDir();
await new ConfigRegistryPublisher(configDir).publish();

const manager = await ConfigRegistryManager.open(configDir);
const config = await manager.getPreset("search", "extraction");
```

Manager 会暴露 active revision 和 reload health metadata。这样你可以准确说明某个服务正在运行哪一份配置。

---

## 供应商覆盖

公开 dispatch helper 覆盖的是我们实际测试的供应商。

| Provider | Python | TypeScript | Notes |
|----------|--------|------------|-------|
| OpenAI | `openai_dispatch` | `openaiDispatch` | OpenAI Responses 和 chat-style flows |
| Anthropic | `anthropic_dispatch` | `anthropicDispatch` | Messages API，thinking budget validation |
| Gemini | `gemini_dispatch` | `geminiDispatch` | Google GenAI-compatible params |
| OpenRouter | `openrouter_dispatch` | `openrouterDispatch` | OpenAI-compatible |
| DeepInfra | `deepinfra_dispatch` | `deepinfraDispatch` | OpenAI-compatible |
| Novita | `novita_dispatch` | `novitaDispatch` | OpenAI-compatible |
| Together | `together_dispatch` | `togetherDispatch` | OpenAI-compatible |
| Sno GPU | `sno_gpu_dispatch` | `snoGpuDispatch` | On-prem OpenAI-compatible GPU endpoints |

Rust 目前提供 neutral pipeline，以及 OpenAI、Anthropic、Gemini 和 Sno GPU 的 feature-gated helpers。Rust provider helpers 仍按 beta 看待。cache、key pool、registry、retry 和 pipeline contract 与 Python、TypeScript 对齐。

OpenAI-compatible providers 复用 OpenAI request shape，并处理各自的 `base_url`。这样契约保持简单。简单很有用。

---

## 环境变量

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` / `OPENAI_KEYS` | OpenAI key 或逗号分隔 key pool |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_KEYS` | Anthropic key 或逗号分隔 key pool |
| `GEMINI_API_KEY` / `GEMINI_KEYS` | Gemini key 或逗号分隔 key pool |
| `OPENROUTER_API_KEY` / `OPENROUTER_KEYS` | OpenRouter key 或逗号分隔 key pool |
| `DEEPINFRA_API_KEY` / `DEEPINFRA_KEYS` | DeepInfra key 或逗号分隔 key pool |
| `TOGETHER_API_KEY` / `TOGETHER_KEYS` | Together key 或逗号分隔 key pool |
| `NOVITA_API_KEY` / `NOVITA_KEYS` | Novita key 或逗号分隔 key pool |
| `SNO_LLM_API_KEY` | Sno GPU direct dispatcher fallback |
| `SNO_GPU_API_KEY` / `SNO_GPU_KEYS` | provider id `sno-gpu` 对应的 Sno GPU key-pool 变量 |
| `GPU_BASE_URL` | Sno GPU base URL |
| `REDIS_URL` | Redis response-cache URL |
| `LLMIX_STATE_DIR` | Lock files、batch metadata 和 kill-switch state |

`load_keys_from_env("provider-name")` 会先检查 `PROVIDER_NAME_KEYS`，再检查 `PROVIDER_NAME_API_KEY`。短横线会变成下划线。

---

## 它不是什么

- 不是 streaming framework。Streaming 仍交给你的 SDK。
- 不是 prompt framework。请带上你自己的 prompt layer。
- 不是 provider marketplace。一次调用使用 config 指定的 provider。
- 不是把每个模型决策都藏到间接层后的理由。有些东西应该留在代码里。

当同一种模型调用形态反复出现在多个服务里时，LLMix 会很有用。如果你只有一个脚本和一个 key，现在可能还不需要它。

---

## 开发

```bash
# TypeScript
bun install
bun test
bunx tsc -p tsconfig.check.json

# Python
uv sync
uv run pytest tests/python/
uv run pyright

# Rust
cargo test --manifest-path rust/llmix-rs/Cargo.toml
cargo clippy --manifest-path rust/llmix-rs/Cargo.toml -- -D warnings
```

---

## License

[Apache-2.0](../../LICENSE)

## Related

- [AI SDK](https://ai-sdk.dev/)
- [Promptix](https://github.com/sno-ai/promptix)
