# 安全使用 LLMix MDA 配置

语言：[English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix 可以从签名过的 MDA preset 产生签名 registry。这样模型行为可以离开应用程式源码，变成可发布、可检查的数据，同时防止下游静默篡改。

最重要的规则只有一条：

registry 可以跟 app 一起发布，但信任锚点必须放在 registry 外面。

如果攻击者可以替换 `config/llm/`，他也可以替换里面的每一个文件。所以 runtime 不能只相信 `config/llm/` 里的东西。它必须从外部拿到 `expectedRootDigest`、trust policy、签名者身份和 freshness/rollback 规则。

## Quick Start

使用目前的 MDA CLI 1.1.x 或更新版本。下面流程用 `mda --version` 为 `1.1.2` 的版本检查过。

1. 用 source `.mda` 写 LLMix preset。
2. 在 CI 或 release 流程里 validate、加 integrity、签名。
3. LLMix publisher 用 trusted runtime 验证这些 `.mda`，再发布签名 registry。
4. MDA CLI finalize 出外部 deployment trust manifest。
5. runtime 用这份外部 manifest 打开 registry。

```bash
mda init --template llmix-preset \
  --module search_summary \
  --preset openai_fast \
  --provider openai \
  --model gpt-5-mini \
  --out authoring/search_summary/openai_fast.mda

mda validate authoring/search_summary/openai_fast.mda --target source --json
mda integrity compute authoring/search_summary/openai_fast.mda --target source --write --json
```

用 release 流程采用的 signer 签名。最简单的本地例子是 did:web：

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

接着产生 source policy 和 registry-root policy、准备 release plan、发布 LLMix
registry，然后 finalize 外部 manifest：

```bash
mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/source-policy.json \
  --json

mda release trust policy \
  --target llmix-registry \
  --profile did-web \
  --domain config.example.com \
  --out release/root-policy.json \
  --json

mda release prepare \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --policy release/source-policy.json \
  --did-document release/did.json \
  --out release/plan.json \
  --json

# 这里执行 LLMix publisher，必须打开 trustedRuntime。
# 它读取 authoring/，验证每个签名 .mda，写入 config/llm/，
# 并签名 config/llm/snapshots/<revision>/registry-root.json。

mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --registry-root config/llm/snapshots/<revision>/registry-root.json \
  --release-plan release/plan.json \
  --policy release/root-policy.json \
  --derive-root-digest \
  --minimum-revision <revision> \
  --out deploy/llmix-trust.json \
  --did-document release/did.json \
  --json

mda doctor release \
  --target llmix-registry \
  --source authoring \
  --registry-dir config/llm \
  --release-plan release/plan.json \
  --manifest deploy/llmix-trust.json \
  --did-document release/did.json \
  --json
```

`deploy/llmix-trust.json` 就是外部锚点。不要把它放在 `config/llm/` 里面。

## 保护什么

| 状况 | 结果 |
| --- | --- |
| 签名 `.mda` 正确，registry root 和外部 manifest 相符。 | LLMix 正常 load preset。 |
| preset、manifest、`current.json` 或 registry root 被改过。 | runtime 拒绝 registry。 |
| 整包 `config/llm/` 被换成另一包内部自洽的 registry。 | runtime 仍然拒绝，因为 `expectedRootDigest`、signer policy 和 freshness 规则来自外部。 |

如果要防止旧版本回滚，release finalize 时加 `minimumRevision`、`minimumPublishedAt` 或 high-watermark。

## 文件结构

推荐结构：

```text
authoring/
  search_summary/
    openai_fast.mda
    openrouter_balanced.mda

config/llm/
  current.json
  snapshots/
    <revision>/
      manifest.json
      registry-root.json
      search_summary/
        openai_fast.json
        openrouter_balanced.json

deploy/
  llmix-trust.json
```

`authoring/` 是人编辑的 source `.mda`。`config/llm/` 是发布后的 LLMix registry，可以跟 app 一起 ship。`deploy/llmix-trust.json` 必须从另一个部署通道提供，例如 app config、secret/config manager、Kubernetes config、编译进 app 的常量，或 release attestation。

`registry-root.json` 是证据。外部 trust manifest 才是锚点。

## 写 Preset

MDA 机制字段放顶层。LLMix 自己的设定放在 `metadata.snoai-llmix`。

```markdown
---
name: openai_fast
description: Fast OpenAI preset for search summaries.
requires:
  network: ["api.openai.com"]
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.2
      maxOutputTokens: 1024
integrity:
  algorithm: sha256
  digest: "sha256:..."
signatures:
  - signer: "did-web:config.example.com"
    key-id: "did:web:config.example.com#release"
    payload-digest: "sha256:..."
    algorithm: ed25519
    signature: "..."
    payload-type: "application/vnd.snoai-llmix.preset+json"
---

# Optional notes for humans
```

module 和 preset 名称建议只用小写字母、数字、`_`、`-`。Provider API key、tenant secret、环境变量不要写进 `.mda`，这些应放在 runtime environment 或 secret manager。

完整 provider config shape 请看 [LLMix usage reference](../llmix-usage-ref.md)。

## Publisher 需要做什么

生产发布时，publisher 应该：

1. 用 `trustedRuntime: true` 读取 source `.mda`。
2. 执行 source trust policy 和 network policy。
3. 写入 immutable resolved JSON snapshots。
4. 写入 active revision 的 `current.json`。
5. 对整个 registry revision 写出并签名 `registry-root.json`。

registry root 会覆盖 active pointer、snapshot manifest、resolved config files、source digests、release revision 和发布时间。单个文件被改会被抓到。整包替换则靠外部 `expectedRootDigest` 和 trust policy 抓到。

## Runtime 怎么打开

runtime 从外部 trust manifest 产生 `signedRoot` options，再打开 `config/llm/`。

TypeScript：

```ts
import {
  ConfigRegistryManager,
  loadLlmixTrustManifest,
  registryRootOptionsFromTrustManifest,
} from "@snoai/llmix";

const manifest = await loadLlmixTrustManifest("/etc/llmix/llmix-trust.json");

const registry = await ConfigRegistryManager.open("./config/llm", {
  signedRoot: registryRootOptionsFromTrustManifest(manifest, {
    didWebVerifier,
    rekorClient,
    sigstoreVerifier,
    highWatermark,
  }),
});

const preset = await registry.getPreset("search_summary", "openai_fast");
```

Python：

```python
from llmix import (
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
    registry_root_options_from_trust_manifest,
)

manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")

registry = ConfigRegistryManager.open(
    "./config/llm",
    ConfigRegistryOpenOptions(
        signed_root=registry_root_options_from_trust_manifest(
            manifest,
            did_web_verifier=did_web_verifier,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
            high_watermark=high_watermark,
        )
    ),
)

preset = registry.get_preset("search_summary", "openai_fast")
```

Rust：

```rust
use llmix_rs::{
    registry_root_options_from_trust_manifest,
    ConfigRegistryManager,
    ConfigRegistryOpenOptions,
    load_llmix_trust_manifest,
};

let manifest = load_llmix_trust_manifest("/etc/llmix/llmix-trust.json")?;
let signed_root = registry_root_options_from_trust_manifest(&manifest)?;

let registry = ConfigRegistryManager::open_with_options(
    "./config/llm",
    ConfigRegistryOpenOptions {
        signed_root: Some(signed_root),
    },
)?;

let preset = registry.get_preset("search_summary", "openai_fast")?;
```

policy 信任 did:web，就要提供 did:web verifier。policy 信任 GitHub Actions/Sigstore，就要提供 Sigstore 和 Rekor verifier。

## 锚点怎么选

选择最符合你部署方式的锚点。

| 锚点 | 适合场景 | 说明 |
| --- | --- | --- |
| 外部 trust manifest 文件 | 大部分服务 | `mda release finalize` 产生，放在 `config/llm/` 外面。默认最简单。 |
| app 常量或 build-time config | CLI、desktop、embedded app | 把 `expectedRootDigest` 和 policy pin 进 app。换 registry 就要更新 app 或 build config。 |
| deployment config / secret manager | server 部署 | 放在 Kubernetes config、cloud config、Secret Manager、SSM、Vault 等外部位置。 |
| GitHub Actions OIDC + Rekor | 常见 CI release | 适合从 GitHub repo workflow 发布。policy pin repo、workflow、ref、issuer、Rekor。 |
| did:web、KMS、HSM | 组织已经有 signing 基础设施 | 适合有自己的 web identity 或 key management 的团队。 |

MDA CLI 可以帮你产生 policy、验证 source、验证签名、准备 release plan、finalize trust manifest、输出部署 snippet。它不能取代 runtime 的最终信任边界。runtime 仍然必须从外部 anchor 得到信任。

## 部署 Snippet

有了 `deploy/llmix-trust.json` 后，可以从同一份 manifest 产生部署片段：

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

支持 `json`、`env`、`kubernetes`、`github-actions`、`terraform`、`typescript`、`python`、`rust`。

## Troubleshooting

| 问题 | 检查 |
| --- | --- |
| 正确 registry 打不开，出现 digest 错误。 | 确认 `expectedRootDigest` 是 `registry-root.json` 文件 bytes 的 SHA-256，不是只算内部 payload。重新跑 `mda release finalize --derive-root-digest`。 |
| runtime 说没有 trusted signature。 | 签名可能 cryptographic verify 通过，但 signer 不符合 `trustedSigners`。检查 signer type、domain、issuer、subject、workflow、ref。 |
| did:web verification 失败。 | 确认 runtime did:web verifier 解析到 release 时使用的 DID document，而且 `key-id` 存在。 |
| Sigstore verification 失败。 | 检查 Rekor policy、issuer、subject、workflow/ref binding，以及 runtime 是否提供 Rekor client 和 Sigstore verifier。 |
| 被改过的文件好像还能 load。 | 确认 app 是用 `signedRoot` options 打开 registry。没有 signed root verification 时只是普通解析。 |
| 整包替换后还能 load。 | trust manifest 很可能也从 `config/llm/` 或同一包里读取。把它移到 registry 外部。 |
| 旧的 signed registry 又生效。 | release finalize 和 runtime open 时使用 `minimumRevision`、`minimumPublishedAt` 或 high-watermark。 |

## 相关文件

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English: Secure LLMix Configuration with MDA](./secure-llmix-configuration.md)
