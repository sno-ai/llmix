<!-- Thanks for opening a PR. Please fill the sections below. -->

## What

<!-- One or two sentences on what this PR changes. -->

## Why

<!-- The motivation. Link to any issue this closes. -->

## How

<!-- Architectural notes, tradeoffs, or anything a reviewer should know. -->

## Tests

- [ ] `bun run test:typescript` passes
- [ ] `uv run --project packages/llmix/python pytest -c packages/llmix/python/pyproject.toml packages/llmix/python/tests` passes
- [ ] `cargo test --manifest-path packages/llmix/rust/Cargo.toml` passes
- [ ] `bun run check` clean
- [ ] `uv run --project packages/llmix/python pyright -p packages/llmix/python` clean

## Cross-language parity

<!-- If the change touches the public contract, both Python + TypeScript (and Rust where applicable) must move together. Tick what applies. -->

- [ ] Pure docs / examples / scripts (no parity impact)
- [ ] Python only (justify why TS/Rust is unaffected)
- [ ] TypeScript only (justify why Py/Rust is unaffected)
- [ ] Rust only (justify why Py/TS is unaffected)
- [ ] All bindings updated together
