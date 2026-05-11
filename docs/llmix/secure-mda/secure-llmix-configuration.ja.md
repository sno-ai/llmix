# LLMix MDA 設定を安全に使う

言語: [English](./secure-llmix-configuration.md) | [Deutsch](./secure-llmix-configuration.de.md) | [Español](./secure-llmix-configuration.es.md) | [Français](./secure-llmix-configuration.fr.md) | [हिन्दी](./secure-llmix-configuration.hi.md) | [日本語](./secure-llmix-configuration.ja.md) | [한국어](./secure-llmix-configuration.ko.md) | [Русский](./secure-llmix-configuration.ru.md) | [中文](./secure-llmix-configuration.zh.md)

LLMix は署名済み MDA ファイルからモデル preset を読み込み、署名済み registry として公開できます。これにより、モデルの振る舞いをアプリケーションコードから外に出しつつ、下流の利用者が静かに改ざんすることを防げます。

重要なルールは 1 つだけです。

Registry は app と一緒に配布してよい。ただし trust anchor は registry の外に置く。

攻撃者が `config/llm/` を置き換えられるなら、その中のすべてのファイルも置き換えられます。そのため runtime は `config/llm/` だけを信頼してはいけません。`expectedRootDigest`、trust policy、signer identity、freshness/rollback rules を外部から受け取る必要があります。

## Quick Start

MDA CLI 1.1.x 以降を使ってください。以下の流れは `mda --version` = `1.1.2` で確認済みです。

1. LLMix presets を source `.mda` files として書く。
2. CI または release automation で validate、integrity 追加、sign を行う。
3. LLMix publisher が trusted runtime で `.mda` を検証し、署名済み registry を公開する。
4. MDA CLI が外部 deployment trust manifest を finalize する。
5. Runtime はその外部 manifest で registry を開く。

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

Release process で使う signer で署名します。最も単純な local example は did:web です。

```bash
mda sign authoring/search_summary/openai_fast.mda \
  --profile did-web \
  --did did:web:config.example.com \
  --key-id did:web:config.example.com#release \
  --key-file release/did-web-private-key.pem \
  --in-place \
  --json
```

Source policy と registry-root policy を生成し、release plan を準備し、LLMix registry を公開してから、外部 manifest を finalize します。

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

# ここで trustedRuntime を有効にした LLMix publisher を実行します。
# authoring/ を読み、すべての署名済み .mda を検証し、config/llm/ を書き、
# config/llm/snapshots/<revision>/registry-root.json に署名します。

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

`deploy/llmix-trust.json` が外部 anchor です。`config/llm/` の中に置かないでください。

## 何を守るか

| 状況 | 期待される結果 |
| --- | --- |
| 署名済み `.mda` が正しく、registry root が外部 trust manifest と一致する。 | LLMix が preset を load する。 |
| Publish 後に preset、manifest、`current.json`、registry root が変更される。 | Runtime が registry を拒否する。 |
| `config/llm/` 全体が別の内部的に一貫した registry に置き換えられる。 | `expectedRootDigest`、signer policy、freshness rules が外部由来なので、runtime はそれも拒否する。 |

Rollback protection が必要な場合は、finalize と runtime open で `minimumRevision`、`minimumPublishedAt`、または high-watermark value を使います。

## Files

推奨構成:

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

`authoring/` には人が編集する source `.mda` files を置きます。`config/llm/` には公開済み LLMix registry が入り、app と一緒に ship できます。`deploy/llmix-trust.json` は別の deployment channel から提供してください。例: application config、secret/config manager、Kubernetes config、app に焼き込む constant、release attestation。

`registry-root.json` は証拠です。外部 trust manifest が anchor です。

## Preset を書く

MDA mechanism fields は top level に置きます。LLMix の設定は `metadata.snoai-llmix` に置きます。

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

Module と preset 名には registry-safe な名前を使います。小文字、数字、`_`、`-` が最も安全です。Provider API keys、tenant secrets、environment-specific credentials は `.mda` に入れず、runtime environment または secret manager に置いてください。

完全な provider config shape は [LLMix usage reference](../llmix-usage-ref.md) を参照してください。

## Publisher contract

Production registry を publish するとき、publisher は次を行うべきです。

1. Source `.mda` files を `trustedRuntime: true` で load する。
2. Source trust policy と required network policy を enforce する。
3. Immutable resolved JSON snapshots を書く。
4. Active revision の `current.json` を書く。
5. Registry revision 全体の `registry-root.json` を書いて署名する。

Registry root は active pointer、snapshot manifest、resolved config files、source digests、release revision、publication time を cover します。これにより部分的な編集は検出されます。全体置換は外部 `expectedRootDigest` と trust policy で検出されます。

## Runtime

Runtime は外部 trust manifest から作った `signedRoot` options で `config/llm/` を開きます。

TypeScript:

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

Python:

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

Rust:

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

Policy が did:web を信頼するなら did:web verifier を提供します。Policy が GitHub Actions/Sigstore を信頼するなら Sigstore と Rekor verifier を提供します。

## Anchor の選び方

Deployment に合う最も単純な anchor を選びます。

| Anchor | 向いている場面 | Notes |
| --- | --- | --- |
| External trust manifest file | ほとんどの services | `mda release finalize` が生成し、`config/llm/` の外に保存する。最も簡単な default。 |
| App constant または build-time config | CLI、desktop、embedded app | `expectedRootDigest` と policy を app に pin する。新しい registry を受け入れるには app または build config の更新が必要。 |
| Deployment config または secret manager | Server deployments | Kubernetes config、cloud config、Secret Manager、SSM、Vault などに置く。 |
| GitHub Actions OIDC + Rekor | 一般的な CI release | Repo workflow から release する場合に適している。Policy は repo、workflow、ref、issuer、Rekor を pin する。 |
| did:web、KMS、HSM | 組織管理の signing | 組織が web identity や key management をすでに持っている場合に適している。 |

MDA CLI は policies の生成、sources の validate、signatures の verify、release plans の prepare、trust manifests の finalize、deployment snippets の出力を支援できます。ただし runtime の最終的な trust boundary にはなりません。Runtime trust は、LLMix に渡す外部 anchor から来ます。

## Deployment snippets

`deploy/llmix-trust.json` ができたら、CLI は同じ manifest から deployment snippets を生成できます。

```bash
mda release finalize \
  --target llmix-registry \
  --registry-dir config/llm \
  --manifest deploy/llmix-trust.json \
  --snippet-format kubernetes \
  --snippet-out deploy/llmix-trust.kubernetes.yaml \
  --json
```

Supported formats: `json`, `env`, `kubernetes`, `github-actions`, `terraform`, `typescript`, `python`, `rust`。

## Troubleshooting

| 問題 | 確認すること |
| --- | --- |
| 正しい registry が digest error で開けない。 | `expectedRootDigest` が内部 payload だけでなく、`registry-root.json` file bytes の SHA-256 であることを確認する。`mda release finalize --derive-root-digest` を再実行する。 |
| Runtime が trusted signature がないと言う。 | Signature は cryptographic verify できても `trustedSigners` と一致しない場合がある。Signer type、domain、issuer、subject、workflow、ref を確認する。 |
| did:web verification が失敗する。 | Runtime の did:web verifier が release 時と同じ DID document を解決し、`key-id` が存在することを確認する。 |
| Sigstore verification が失敗する。 | Rekor policy、issuer、subject、workflow/ref binding、runtime の Rekor client と Sigstore verifier を確認する。 |
| 改ざんされた file がまだ load されるように見える。 | App が `signedRoot` options で registry を開いていることを確認する。Signed root verification なしでは registry を parse しているだけです。 |
| 丸ごと置き換えられた registry が load される。 | Trust manifest が `config/llm/` の中、または置き換えられた同じ package から読まれている可能性が高い。Registry の外へ移す。 |
| 古い signed registry が再び active になる。 | Finalize と runtime open で `minimumRevision`、`minimumPublishedAt`、high-watermark を使う。 |

## 関連ドキュメント

- [MDA Config Runtime Guide](../../mda-config/README.md)
- [LLMix usage reference](../llmix-usage-ref.md)
- [English](./secure-llmix-configuration.md)
- [中文](./secure-llmix-configuration.zh.md)
