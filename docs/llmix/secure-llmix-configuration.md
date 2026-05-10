# How to Keep LLMix Configuration Safe

This page answers the practical question: if model settings live in `.mda`
files, how do you make sure production only uses configuration your team
approved?

Short version:

1. Put LLMix model settings in `.mda` files.
2. Check or sign those files during release.
3. Publish the LLMix registry.
4. In production, load the published registry instead of reading editable `.mda`
   files on every request.

## Q&A

### Do I need a server or website for this?

No. LLMix does not need a signing server, LLMix cloud service, or website during
normal runtime.

Your release process may use a signing tool. That tool can be local, CI-local,
or connected to whatever signing system your team already uses. After release,
your app can read local registry files and call model providers normally.

### What happens if runtime loads a bad `.mda` file?

It depends on which runtime path you use.

If you directly load a `.mda` file:

- TypeScript `loadMdaConfig(...)` rejects. Missing files become
  `ConfigNotFoundError`, permission failures become `ConfigAccessError`, schema
  failures become `InvalidConfigError`, and MDA parser or verification failures
  are thrown before a config is returned.
- Python `load_mda_config(...)` raises `ConfigNotFoundError` or
  `ConfigAccessError` for file problems. Bad MDA content or a failed integrity
  check becomes `InvalidConfigError`.
- Rust `load_config(...)` returns `Err(...)`. Bad frontmatter, missing
  `metadata.snoai-llmix`, or invalid fields become `InvalidConfigError`.

LLMix does not silently continue after a direct `.mda` load fails. Your app must
catch the error or fail startup.

If you use `ConfigRegistryManager`:

- Runtime reads the published registry files, not the editable `.mda` file on
  every request.
- If startup sees an invalid active registry, `open(...)` fails.
- If the app is already running and a refresh points to a bad new registry,
  LLMix records the reload error, keeps the previous good config, and continues
  serving that previous config.

This is why the registry is the recommended production path.

### Are signatures checked at runtime?

Use signatures as a release gate: check signed `.mda` files before publishing
the registry that production will read.

Current runtime support is language-specific:

- TypeScript can publish only from RC3 trusted `.mda` sources by passing
  `trustedRuntime: true`, a trust policy, and the required Rekor, Sigstore, or
  did:web verifier hooks.
- Python and Rust expose the same MDA trust concepts through their MDA config
  packages; registry-level signed-root parity remains follow-up work.

For production, the clean pattern is: verify signed `.mda` during release,
publish a signed registry root, ship the registry with the app, keep deployed
files read-only, and supply trust anchors from outside the registry bundle.

### What should production code do?

Use this shape:

1. Release builds and checks the registry.
2. Production starts with `ConfigRegistryManager.open(...)`.
3. If startup fails, do not serve traffic.
4. If a later refresh fails, alert on the manager's reload error and keep
   serving the last good config.

Do not wrap editable `.mda` loading into request handling unless you intentionally
want every request path to handle config parsing and verification failures.

### Where do LLMix settings go inside `.mda`?

Put LLMix settings under `metadata.snoai-llmix`.

MDA-owned mechanism fields such as `requires`, `integrity`, and `signatures`
remain top-level MDA fields. LLMix reads its own namespace and leaves those
mechanism fields to the installed MDA parser packages.

## Namespace Shape

```yaml
metadata:
  snoai-llmix:
    common:
      provider: openai
      model: gpt-5-mini
      temperature: 0.7
      maxOutputTokens: 4096
    providerOptions:
      openai:
        reasoningEffort: medium
    caching:
      strategy: memory
```

Objects in this namespace are strict: unknown keys are invalid unless their value
is explicitly described below as a provider-specific pass-through record.

## Registry-Safe Identifiers

LLMix registry module and preset names are intentionally conservative because
they become file-system paths inside published snapshots.

Use lowercase snake case:

```text
authoring/
  claw_storix_extraction_candidate/
    openai_gpt_5_nano.mda
    anthropic_haiku.mda
```

Do not use dots, slashes, hyphens, spaces, tildes, shell variables, or backticks
in module or preset names. A module must be `_default` or match
`[a-z][a-z0-9_]{0,63}`. A preset must be `_base*` or match
`[a-z][a-z0-9_]{0,63}`.

If your product uses public names such as `openai-gpt-5-nano` or
`claw-storix.extraction.candidate`, keep those at your application boundary and
map them to registry-safe names before calling LLMix.

## `common`

Required object. `provider` and `model` are required; every other key is optional.

| Key | Type and constraints |
| --- | -------------------- |
| `provider` | Required string enum: `openai`, `anthropic`, `google`, `deepseek`, `openrouter`, `deepinfra`, `novita`, `together`, `sno-gpu`. |
| `model` | Required non-empty string provider model ID. |
| `maxOutputTokens` | Optional positive integer. |
| `temperature` | Optional number from `0` to `2`. |
| `topP` | Optional number from `0` to `1`. |
| `topK` | Optional positive integer. |
| `presencePenalty` | Optional number. |
| `frequencyPenalty` | Optional number. |
| `stopSequences` | Optional array of strings. |
| `seed` | Optional integer. |
| `maxRetries` | Optional non-negative integer. |
| `enableThinking` | Optional boolean. |
| `keepThinkingOutput` | Optional boolean. |

## `providerOptions`

Optional object. Provider option objects are optional and strict.

### `openai`

| Key | Type and constraints |
| --- | -------------------- |
| `reasoningEffort` | Optional enum: `minimal`, `low`, `medium`, `high`, `xhigh`. |
| `parallelToolCalls` | Optional boolean. |
| `user` | Optional string. |
| `logprobs` | Optional boolean or non-negative integer. |
| `logitBias` | Optional record of integer token ID to number bias. |
| `structuredOutputs` | Optional boolean. |
| `strictJsonSchema` | Optional boolean. |
| `maxCompletionTokens` | Optional positive integer. |
| `store` | Optional boolean. |
| `metadata` | Optional record of string to string. |
| `prediction` | Optional record of string to unknown JSON value. |
| `serviceTier` | Optional enum: `auto`, `flex`, `priority`, `default`. |
| `textVerbosity` | Optional enum: `low`, `medium`, `high`. |
| `promptCacheKey` | Optional string. |
| `promptCacheRetention` | Optional enum: `in_memory`, `24h`. |
| `safetyIdentifier` | Optional string. |

### `anthropic`

| Key | Type and constraints |
| --- | -------------------- |
| `thinking` | Optional object. If present, `type` is required enum `enabled` or `disabled`; `budgetTokens` is an optional positive integer. When the selected provider is `anthropic`, `thinking.type: enabled` requires any supplied `budgetTokens` to be at least `1024`. |
| `cacheControl` | Optional object. If present, `type` is required literal `ephemeral`; `ttl` is an optional string. |
| `disableParallelToolUse` | Optional boolean. |
| `sendReasoning` | Optional boolean. |
| `effort` | Optional enum: `high`, `medium`, `low`. |
| `toolStreaming` | Optional boolean. |
| `structuredOutputMode` | Optional enum: `outputFormat`, `jsonTool`, `auto`. |

### `google`

| Key | Type and constraints |
| --- | -------------------- |
| `thinkingConfig` | Optional object with optional `thinkingLevel` enum `low` or `high`, optional positive integer `thinkingBudget`, and optional boolean `includeThoughts`. |
| `cachedContent` | Optional string. |
| `structuredOutputs` | Optional boolean. |
| `safetySettings` | Optional array of objects with required string `category` and required string `threshold`. |
| `responseModalities` | Optional array of strings. |

### `deepseek`

| Key | Type and constraints |
| --- | -------------------- |
| `thinking` | Optional object. If present, `type` is required enum `enabled` or `disabled`. |

### `openrouter`

| Key | Type and constraints |
| --- | -------------------- |
| `provider` | Optional record of string to unknown JSON value. |
| `reasoning` | Optional record of string to unknown JSON value. |

### `sno-gpu`

| Key | Type and constraints |
| --- | -------------------- |
| `enableThinking` | Optional boolean. |
| `thinkingBudget` | Optional positive integer. |
| `gpuPath` | Optional string. |

### `deepinfra`, `novita`, and `together`

These provider option objects are optional pass-through records. `deepinfra`
and `novita` may include optional `enableThinking` boolean and positive integer
`thinkingBudget` values.

## `caching`

Optional object. If present, `strategy` is required.

| Key | Type and constraints |
| --- | -------------------- |
| `strategy` | Required enum: `native`, `gateway`, `disabled`, `redis`, `redis-or-memory`, `memory`. |
| `key` | Optional string cache key. |
| `ttl` | Optional positive integer number of seconds. |
| `maxItems` | Optional positive integer maximum L1 cache entries. |

## Signing Format

LLMix does not invent a second signing format for `metadata.snoai-llmix`.
The `.mda` file is signed by MDA, and LLMix can ask MDA to check that signature
when loading or publishing a registry in Python, TypeScript, and Rust.

Most application teams only need to know three things:

- put LLMix model settings under `metadata.snoai-llmix`;
- sign the `.mda` file with normal MDA signing tooling;
- publish the LLMix registry with the strongest verification options available
  in your runtime.

The type string used by MDA for an LLMix preset is:

```text
application/vnd.snoai-llmix.preset+json
```

Most users never need to edit this value by hand. It is mainly for tools that
create or verify signed MDA files.

## Recommended Release Flow

For production services, use a plain release flow:

1. Put each model choice in a real `.mda` file, such as
   `authoring/search_summary/openai_fast.mda`.
2. Sign those `.mda` files with the MDA signing workflow your team already uses.
   This is the step that says "this model config is approved."
3. In CI or during release, publish the LLMix registry with verification checks
   turned on. The trust policy says which signer you trust. If a preset is
   unsigned or signed by the wrong identity, the publish step fails.
4. Ship the generated `snapshots/` directory and `current.json` with your app,
   package, or container image.
5. In production, read only the published registry with
   `ConfigRegistryManager`. Do not load editable `.mda` files during request
   handling.

This gives application teams a simple deployment model: no signing service is
needed while handling user requests, and no cloud call is needed before every
model request. The production service reads the files that were already
published with the app.

The layout should look like this:

```text
config/llm/
  authoring/
    search_summary/
      openai_fast.mda
      anthropic_deep.mda
  snapshots/
    2026-05-09T120000Z/
      authoring/
      resolved/
      manifest.json  # LLMix-generated index file
      registry-root.json
  current.json
```

The trust policy, public keys, expected root digest, and high-watermark state
are supplied by application or deployment configuration at runtime. They are not
stored under `config/llm/` as the authority for the same registry bundle.

TypeScript can require MDA integrity and signatures while publishing the
registry files. For signed presets, use `trustedRuntime: true` with a trust
policy plus Rekor, Sigstore, and/or did:web verifier implementations:

```typescript
import { ConfigRegistryPublisher } from "@snoai/llmix";

const publisher = new ConfigRegistryPublisher("config/llm");

await publisher.publish({
  trustedRuntime: true,
  trustPolicy,
  rekorClient,
  sigstoreVerifier,
  registryRoot: { signer: registryRootSigner },
});
```

`verifySignatures` is a lower-level helper for focused diagnostics. It is not
the recommended production anti-tamper boundary by itself.

The default signing profile should be GitHub Actions keyless Sigstore with
Rekor transparency logging, pinned to the exact repository, ref or environment,
and workflow identity you release from. For higher-assurance releases, require
multiple independent signers, such as GitHub OIDC plus a cloud KMS/HSM identity
or did:web signer controlled through separate access.

Runtime verification should open the registry with trust anchors supplied by
application or deployment configuration, not loaded from the registry directory
being verified:

```typescript
import { ConfigRegistryManager } from "@snoai/llmix";

const manager = await ConfigRegistryManager.open("config/llm", {
  signedRoot: {
    trustPolicy: registryRootTrustPolicy,
    rekorClient,
    sigstoreVerifier,
    didWebVerifier,
    expectedRootDigest,
  },
});
```

Rust exposes source `.mda` trust gates through `publish_with_mda_options(...)`;
signed registry-root parity is follow-up work:

```rust
use llmix_rs::{ConfigRegistryPublisher, MdaConfigLoadOptions};

let publisher = ConfigRegistryPublisher::new("config/llm")?;
let options = MdaConfigLoadOptions {
    trusted_runtime: true,
    trust_policy: Some(trust_policy),
    rekor_client: Some(&rekor_client),
    sigstore_verifier: Some(&sigstore_verifier),
    ..Default::default()
};

publisher.publish_with_mda_options(None, true, &options)?;
```

Runtime code should then open the registry and select a module/preset pair:

```typescript
import { ConfigRegistryManager } from "@snoai/llmix";

const manager = await ConfigRegistryManager.open("config/llm");
const config = await manager.getPreset("search_summary", "openai_fast");
```

Use this as the default product pattern:

- Operators may choose between packaged, signed presets.
- Provider keys stay in environment variables or a secret manager.
- Provider, model, retry, timeout, cache, and provider-specific options stay in
  reviewed MDA presets.
- Runtime overrides, if allowed, should be bounded by signed policy. For
  example, an operator may request a shorter timeout, but not a longer timeout
  than the signed preset allows.

## What This Protects

Think of the registry as the folder of approved model settings that ships with
your app.

During release, LLMix can refuse to publish that bundle unless the `.mda` files
were signed by a person, bot, or key your team trusts. This is the important
step: it keeps an unsigned model change from becoming part of a release.

During production, LLMix checks that the registry files it is about to use still
match the files created during release. This catches common mistakes: half-copied
deploys, stale files, and someone editing one of the published config files by
hand.

There is one extra case to understand. If someone has enough access to rewrite
the entire deployed registry directory after release, the current runtime checks
alone are not meant to prove that the whole directory is still the exact release
you approved. If that matters for your product, use one of these simple
controls:

- ship the registry inside a signed package or container image;
- make the deployed registry directory read-only for the service user;
- run a startup check that revalidates the signed `.mda` files before serving
  traffic.

Most teams should start with this baseline: sign MDA files during release, ship
the generated registry with the app, keep deployed files read-only, and keep API
keys outside the MDA files.

## Stability

The `snoai-llmix` namespace is frozen for LLMix v2.0. Breaking changes to the
namespace or to `application/vnd.snoai-llmix.preset+json` require a major LLMix
version bump.
