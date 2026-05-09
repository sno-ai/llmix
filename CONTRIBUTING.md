# Contributing to LLMix

LLMix is a dual-language library (Python + TypeScript). Contributions must
maintain cross-language parity where the feature exists in both languages.

---

## Setup

```bash
# Python
uv sync

# TypeScript
bun install
```

## Running Tests

```bash
# Python unit tests
uv run pytest tests/python/test_pipeline.py
uv run pytest tests/python/test_config_loader.py
uv run pytest tests/python/test_response_cache.py
uv run pytest tests/python/test_resilience.py
uv run pytest tests/python/test_provider_kwargs.py

# TypeScript unit tests
bun test

# Type checking
bunx tsc -p tsconfig.check.json
uv run pyright
```

## Code Quality

Both languages use strict type checking and linting. All PRs must pass:

- `bunx tsc -p tsconfig.check.json` — zero errors
- `uv run pyright` — zero errors
- `bun test` — all tests pass
- `uv run pytest tests/python/` — all tests pass

## Cross-Language Parity Rules

Changes to core pipeline logic (cache key generation, circuit breaker state,
retry behavior, AIMD semaphore) must be mirrored in both languages.

The cross-language test fixtures in `tests/fixtures/` are the contract. A
change that passes Python tests but breaks TypeScript fixtures is a regression.

### Known parity gap

`response_format` is read from `common` in Python but from the config root
(`responseFormat`) in TypeScript. This inconsistency is documented and should
be unified in a future release. Do not add more gaps of this type.

## Data Files

`data/pricing.json`, `data/model-capabilities.json`, and
`data/config-schema.json` are the canonical source for shared data assets.

`python/llmix/pricing.json` is a copy used by the Python package distribution.
When updating pricing data, update both files.

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
