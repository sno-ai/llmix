# MDA Signing Flow for LLMix

This document describes the flow we want users and AI agents to have.

It is not a low-level PRD. It is the path that should feel normal. A person has
LLMix model settings in `.mda` files. An agent helps prepare them. CI signs and
publishes them. Runtime only accepts the result when the outside trust anchor
says yes.

The registry cannot prove itself. That is the whole point.

## Status Legend

- Green: already in good shape.
- Yellow: the direction is clear, but tooling still needs work.
- Red: missing or unsafe if users must do it by hand.

## The Happy Path

| Step | Status | User expectation | What the CLI should help with | Current shape |
| --- | --- | --- | --- | --- |
| 1. Write `.mda` presets | Green | A human or AI agent creates and edits presets in a staging directory. | Scaffold files, keep target names explicit, return JSON for agents. | `mda init` and the basic authoring flow exist. |
| 2. Validate the presets | Green | The tool catches bad frontmatter, wrong namespace shape, and target mistakes before release. | `mda validate --target source --json`. | Already good. |
| 3. Add stable integrity | Green | The files get a reproducible fingerprint, so later edits are visible. | Canonicalize, compute integrity, verify integrity. | Already good. |
| 4. Sign each `.mda` | Yellow | The release identity signs the approved presets. | Full `mda sign` for GitHub OIDC/Sigstore, KMS/HSM, and did:web where supported. | Flow is clear. Full signing still needs work. |
| 5. Verify signed `.mda` | Yellow | CI checks that every preset was signed by a trusted identity. | Full cryptographic `mda verify` against an explicit trust policy. | Runtime hooks exist. CLI verification is not complete yet. |
| 6. Publish the LLMix registry | Green | Verified presets become a registry bundle under `config/llm/`. | Let LLMix publish with `trustedRuntime: true`. | TypeScript is in good shape. Python and Rust should follow the same model. |
| 7. Sign the registry root | Green | The whole registry snapshot gets one signed root. | Sign the registry root after publish. | TypeScript has signed registry-root support. |
| 8. Choose and emit outside anchors | Yellow | The release produces a deployment trust manifest outside the registry bundle. | Emit digest, signer policy, Rekor policy, freshness policy, and deployment snippets. | The model is clear. CLI/release tooling should finish it. |
| 9. Open registry at runtime | Green | The app starts only when the outside anchor accepts the registry. | Load anchors from app code, deployment config, or release metadata. | TypeScript supports this model. |

## Anchor Choices

Most users should not have to invent this part. The tool should ask for a
profile, then emit the right files or snippets.

### A. Pin `expectedRootDigest` in the app

This is the simplest anchor.

The release produces one digest for the signed registry root. The application
stores that digest in code, a bundled config file, or the app binary. At startup,
LLMix checks the deployed registry against that digest.

Use this when the app and registry ship together.

Tradeoff: every registry change requires an app release. That is not a bug. It
is the boundary.

CLI should help by emitting:

- `expectedRootDigest`
- a TypeScript/Python/Rust config snippet
- a machine-readable JSON result for agents

### B. Pin `expectedRootDigest` in deployment config

This is still simple, but more flexible.

The digest lives in `/etc/llmix/trust.json`, Kubernetes config, Terraform
variables, GitHub Actions deployment outputs, a secret manager, or another
deployment channel. It does not need to be secret. It needs to be outside
`config/llm/`.

Use this when the service and registry may deploy separately.

CLI should help by emitting common formats:

- JSON trust manifest
- environment variables
- Kubernetes ConfigMap or Secret snippet
- GitHub Actions output
- Terraform variable snippet

### C. GitHub Actions OIDC + Sigstore/Rekor

This should be the default for many teams.

Do not trust "whatever file is in the GitHub repo." Trust the release identity:
the repository, ref or environment, workflow identity, Sigstore signature, and
Rekor transparency entry.

This is close to the npm provenance model. The useful claim is not "someone
uploaded this." The useful claim is "this came from this release workflow."

Use this when a project already releases through GitHub Actions.

CLI should help by emitting:

- a GitHub Actions signing workflow snippet
- a Sigstore/Rekor trust policy pinned to repo, ref, environment, and workflow
- the signed `.mda` verification result
- the signed registry-root verification result
- a deployment trust manifest

This profile can replace most hand-written signer policy work. It does not
remove the need for an outside anchor. The anchor becomes the pinned policy and
freshness state, and optionally an `expectedRootDigest` for static releases.

### D. KMS/HSM or did:web

This is for teams with stronger internal signing rules.

Use it when a company already has cloud KMS, HSM, domain-controlled identity, or
multi-signer release policy. It is stronger in the right environment, but it is
not the easiest default.

CLI should help by generating policy templates and checking that emitted
signatures round-trip through verification.

## Recommended Defaults

For a small project:

1. Use GitHub Actions OIDC + Sigstore/Rekor.
2. Also emit `expectedRootDigest`.
3. Store that digest in app config or deployment config.

For a packaged app:

1. Pin `expectedRootDigest` in the app.
2. Ship the registry read-only with the app.

For a service:

1. Use GitHub Actions OIDC + Sigstore/Rekor.
2. Store the deployment trust manifest outside `config/llm/`.
3. Use freshness checks to reject rollback.

## Deployment Trust Manifest

The release should produce two outputs.

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

The first output can ship with the application.

The second output must come from somewhere else at runtime. App code, deployment
config, secret manager, GitHub release metadata, KMS-backed config, Kubernetes,
and Terraform can all work. The important part is not secrecy. The important
part is separation.

## What CLI Work This Implies

The current CLI already has the right authoring shape: explicit targets,
machine-readable JSON, validation, compile, canonicalization, integrity checks,
and conformance checks.

The flow above asks for these next pieces:

1. Complete `mda sign`.
2. Complete cryptographic `mda verify`.
3. Add a GitHub Actions OIDC + Sigstore/Rekor profile.
4. Generate trust policy templates for GitHub, KMS/HSM, and did:web.
5. Emit deployment trust manifests.
6. Emit deployment snippets for common systems.
7. Keep every command agent-friendly with `--json`, stable diagnostics, and
   non-zero exits when trust cannot be proven.

The CLI can automate the release work. It should not become the runtime trust
anchor by hiding the policy inside the same registry bundle.

That would make the whole thing look secure while moving the lock onto the same
door.
