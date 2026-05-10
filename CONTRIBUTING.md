# Contributing to LLMix

This monorepo contains the LLMix product packages and the foundational
mda-config packages across Python, TypeScript, and Rust. Contributions must
maintain runtime parity where a feature exists across languages.

---

## Setup

```bash
# TypeScript
bun install

# Python
uv sync --project packages/llmix/python --extra dev
uv sync --project packages/mda-config/python --all-groups
```

## Running Tests

```bash
# Full monorepo
bun run build
bun run check
bun run test

# Package-specific tests are also available
bun run test:typescript
bun run test:mda-config
bun run test:python
bun run test:mda-config:python
bun run test:rust
bun run test:mda-config:rust
```

## Code Quality

All PRs must pass:

- `bun run build` — TypeScript packages build
- `bun run check` — TypeScript, Python, Rust, and mda-config lint/type/check pass
- `bun run test` — LLMix and mda-config tests pass across all runtimes

## Cross-Language Parity Rules

Changes to core pipeline logic (cache key generation, circuit breaker state,
retry behavior, AIMD semaphore) must be mirrored in every runtime that exposes
the feature.

Changes to mda-config parsing, integrity, signatures, or trusted runtime policy
must be mirrored in TypeScript, Python, and Rust.

The cross-language test fixtures in `fixtures/llmix/` and `fixtures/mda/` are
the contract. A change that passes one runtime but breaks another is a
regression.

### Known parity gap

`response_format` is read from `common` in Python but from the config root
(`responseFormat`) in TypeScript. This inconsistency is documented and should
be unified in a future release. Do not add more gaps of this type.

## Data Files

`packages/llmix/typescript/data/pricing.json`,
`packages/llmix/typescript/data/model-capabilities.json`, and
`packages/llmix/typescript/data/config-schema.json` are the canonical source
for shared data assets.

`packages/llmix/python/llmix/pricing.json` is a copy used by the Python
package distribution. When updating pricing data, update both files.

## Branching

- `main` — stable, always passing CI
- Feature branches off `main`, merged via PR

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add tool-call support to CallPipeline
fix: prevent double-counting in circuit breaker HALF_OPEN probes
docs: document response_format cross-language inconsistency
chore: bump version to 2.1.0
```

## Pull Requests

1. Open a draft PR early for feedback on approach.
2. All CI checks must be green before requesting review.
3. For cross-language changes, link the TypeScript, Python, and Rust changes in
   the same PR.
4. Update `CHANGELOG.md` under `[Unreleased]`.
