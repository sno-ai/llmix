# LLMix 的 MDA 签名流程

这份文件写的是我们希望使用者和 AI agent 怎么用。

它不是底层技术 PRD。它是一个正常人会期待的顺序。人把 LLMix model
settings 写在 `.mda`。AI agent 帮他整理。CI 签名和发布。runtime 只在外部
trust anchor 说可以的时候才接受。

registry 不能自己证明自己。这是整个设计的重点。

## 状态标记

- 绿色：已经做得不错。
- 黄色：方向清楚，但工具还要补。
- 红色：缺失，或者如果让使用者手动做会很容易出错。

## 最顺的流程

| 步骤 | 状态 | 使用者期待 | CLI 应该帮什么 | 目前状况 |
| --- | --- | --- | --- | --- |
| 1. 写 `.mda` presets | 绿色 | 人或 AI agent 在 staging 目录里创建和修改 presets。 | scaffold 文件，明确 target，给 agent 稳定 JSON。 | `mda init` 和基本 authoring flow 已经有。 |
| 2. 检查 presets | 绿色 | release 前先抓出 frontmatter、namespace、target 的错误。 | `mda validate --target source --json`。 | 已经不错。 |
| 3. 生成稳定 integrity | 绿色 | 文件有可重复的指纹，之后被改过就看得出来。 | canonicalize、compute integrity、verify integrity。 | 已经不错。 |
| 4. 签每个 `.mda` | 黄色 | release identity 签过这些被批准的 presets。 | 完整 `mda sign`，支持 GitHub OIDC/Sigstore、KMS/HSM、did:web。 | 流程清楚，但完整 signing 还要补。 |
| 5. 验证 signed `.mda` | 黄色 | CI 确认每个 preset 都是可信身份签的。 | 完整 cryptographic `mda verify`，用明确的 trust policy。 | runtime hooks 有方向，CLI verify 还没完整。 |
| 6. 发布 LLMix registry | 绿色 | 验证过的 presets 变成 `config/llm/` registry bundle。 | LLMix publish 时启用 `trustedRuntime: true`。 | TypeScript 已经不错。Python 和 Rust 应该照这个模型补齐。 |
| 7. 签 registry root | 绿色 | 整包 registry snapshot 有一个 signed root。 | publish 后签 registry root。 | TypeScript 已经有 signed registry-root。 |
| 8. 选择并输出外部 anchors | 黄色 | release 产生 registry 外面的 deployment trust manifest。 | 输出 digest、signer policy、Rekor policy、freshness policy、部署片段。 | 模型清楚，CLI/release tooling 要补完。 |
| 9. runtime 打开 registry | 绿色 | app 启动时用外部 anchor 接受或拒绝 registry。 | 从 app code、deployment config、release metadata 读取 anchors。 | TypeScript 已经支持这个模型。 |

## Anchor 的几种选择

多数使用者不应该自己发明这一步。工具应该让他选一个 profile，然后输出正确
的文件或片段。

### A. 把 `expectedRootDigest` 固定在 app 里

这是最简单的 anchor。

release 产出 signed registry root 的 digest。application 把这个 digest 放在
程式码、随 app 打包的 config，或 binary 里。启动时，LLMix 用这个 digest
检查部署出去的 registry。

适合 app 和 registry 一起发版。

代价是 registry 变了就要重发 app。这不是缺点。它就是边界。

CLI 应该帮忙输出：

- `expectedRootDigest`
- TypeScript/Python/Rust config snippet
- 给 AI agent 读的 JSON 结果

### B. 把 `expectedRootDigest` 放在部署配置里

这仍然简单，但比较灵活。

digest 可以放在 `/etc/llmix/trust.json`、Kubernetes config、Terraform
variables、GitHub Actions deployment outputs、secret manager，或其他部署通道。
它不一定要保密。重点是它不能在 `config/llm/` 里面。

适合 service 和 registry 可能分开发版的状况。

CLI 应该帮忙输出常见格式：

- JSON trust manifest
- environment variables
- Kubernetes ConfigMap 或 Secret snippet
- GitHub Actions output
- Terraform variable snippet

### C. GitHub Actions OIDC + Sigstore/Rekor

这个应该是很多团队的默认选择。

不要信任“GitHub repo 里面某个文件”。要信任 release identity：repository、
ref 或 environment、workflow identity、Sigstore signature、Rekor transparency
entry。

这跟 npm provenance 的精神很接近。重点不是“有人上传了这个东西”。重点是
“这个东西来自这个 release workflow”。

适合已经用 GitHub Actions release 的项目。

CLI 应该帮忙输出：

- GitHub Actions signing workflow snippet
- pin 住 repo、ref、environment、workflow 的 Sigstore/Rekor trust policy
- signed `.mda` verification result
- signed registry-root verification result
- deployment trust manifest

这个 profile 可以取代大多数手写 signer policy 的工作。它不能取消外部 anchor
的需要。anchor 会变成 pinned policy 和 freshness state。对于静态 release，
也可以再加一个 `expectedRootDigest`。

### D. KMS/HSM 或 did:web

这是给有更强内部签名要求的团队。

适合公司已经有 cloud KMS、HSM、domain-controlled identity，或 multi-signer
release policy。它在对的环境里更强，但不是最简单的默认选择。

CLI 应该帮忙生成 policy template，并检查签出来的 signatures 可以真的通过
verify。

## 推荐默认值

小项目：

1. 用 GitHub Actions OIDC + Sigstore/Rekor。
2. 同时输出 `expectedRootDigest`。
3. 把 digest 放进 app config 或 deployment config。

打包型 app：

1. 把 `expectedRootDigest` 固定在 app 里。
2. registry 随 app read-only ship。

服务型系统：

1. 用 GitHub Actions OIDC + Sigstore/Rekor。
2. 把 deployment trust manifest 放在 `config/llm/` 外面。
3. 用 freshness checks 防 rollback。

## Deployment Trust Manifest

release 应该产出两份东西。

```text
registry bundle
  config/llm/
    snapshots/
    current.json

deployment trust manifest
  expectedRootDigest
  registryRootTrustPolicy
  rekorPolicy
  minimumRevision
  minimumPublishedAt
  highWatermark
```

第一份可以随 app ship。

第二份 runtime 时必须从别的地方来。app code、deployment config、secret
manager、GitHub release metadata、KMS-backed config、Kubernetes、Terraform
都可以。重点不是保密。重点是隔离。

## 这倒推出来的 CLI 工作

现在 CLI 的 authoring 方向是对的：explicit targets、machine-readable JSON、
validation、compile、canonicalization、integrity checks、conformance checks。

上面的流程倒推出来，下一步要补这些：

1. 完整 `mda sign`。
2. 完整 cryptographic `mda verify`。
3. 加 GitHub Actions OIDC + Sigstore/Rekor profile。
4. 生成 GitHub、KMS/HSM、did:web 的 trust policy templates。
5. 输出 deployment trust manifest。
6. 输出常见部署系统的 snippets。
7. 所有命令都保持 agent-friendly：`--json`、稳定 diagnostics、无法证明 trust
   时 non-zero exit。

CLI 可以自动化 release 工作。它不应该把 policy 藏进同一个 registry bundle，
然后假装自己变成 runtime trust anchor。

那只是把锁装到同一扇门上。
