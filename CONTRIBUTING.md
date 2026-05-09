# Contributing to LLMix

LLMix is a cross-runtime library (Python + TypeScript + Rust). Contributions
must maintain runtime parity where the feature exists across languages.

---

## Setup

```bash
# Python
uv sync --project packages/llmix/python --extra dev

# TypeScript
bun install
```

## Running Tests

```bash
# Python unit tests
uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests/test_pipeline.py
uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests/test_config_loader.py
uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests/test_response_cache.py
uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests/test_resilience.py
uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests/test_provider_kwargs.py

# TypeScript unit tests
bun run test:typescript

# Rust tests
cargo test --manifest-path packages/llmix/rust/Cargo.toml

# Type checking
bun run check
uv run --project packages/llmix/python pyright -p packages/llmix/python
cargo check --manifest-path packages/llmix/rust/Cargo.toml
```

## Code Quality

Both languages use strict type checking and linting. All PRs must pass:

- `bun run check` — zero TypeScript errors
- `uv run --project packages/llmix/python pyright -p packages/llmix/python` — zero Python type errors
- `cargo check --manifest-path packages/llmix/rust/Cargo.toml` — zero Rust check errors
- `bun run test:typescript` — TypeScript tests pass
- `uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests` — Python tests pass
- `cargo test --manifest-path packages/llmix/rust/Cargo.toml` — Rust tests pass

## Cross-Language Parity Rules

Changes to core pipeline logic (cache key generation, circuit breaker state,
retry behavior, AIMD semaphore) must be mirrored in both languages.

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
3. For cross-language changes, link the Python and TypeScript changes in the
   same PR.
4. Update `CHANGELOG.md` under `[Unreleased]`.
