# mda-config

Runtime loaders for `.mda` configuration files.

The MDA CLI and these packages do different jobs. Use
[`@markdown-ai/cli`](https://github.com/sno-ai/mda-markdown) while authoring:
create a file, validate it, compute or verify integrity, and compile it into
agent-facing Markdown when you need `SKILL.md`, `AGENTS.md`, or
`CLAUDE.md`, or `MCP-SERVER.md`.

Use `mda-config` at runtime: load an already-authored `.mda` source file,
validate the frontmatter against your product schema, optionally enforce
integrity/signatures/requirements, and hand the typed config to your app.

That split is intentional. The CLI belongs in a developer shell, build step, or
CI job. The loader belongs in the process that actually consumes the config.

The TypeScript, Python, and Rust packages expose the same MDA v1.0 source-mode
contract: frontmatter extraction, source-schema validation, integrity
verification, `requires.network` enforcement, and RC2 trusted-runtime verifier
hooks. Real Rekor transport, Sigstore cryptography, and did:web cryptography are
supplied by the caller through verifier/client hooks.

- **Repo:** `github.com/sno-ai/llmix`
- **License:** Apache-2.0
- **Spec pin:** MDA v1.0

## Packages

| Package | Status | Path |
|---------|--------|------|
| `@snoai/mda-config` (npm, TypeScript) | v1.1.1: frontmatter, source schema, integrity, `requires.network`, schema parser support, verifier hooks | [`packages/mda-config/typescript/`](../../packages/mda-config/typescript/) |
| `snoai-mda-config` (PyPI, Python) | v1.1.1: frontmatter, source schema, integrity, `requires.network`, pydantic, verifier hooks | [`packages/mda-config/python/`](../../packages/mda-config/python/) |
| `snoai-mda-config` (crates.io, Rust) | v1.1.1: frontmatter, source schema, integrity, `requires.network`, serde, verifier hooks | [`packages/mda-config/rust/`](../../packages/mda-config/rust/) |

## Quick Start

Author or check a config file with the MDA CLI:

```bash
npx @markdown-ai/cli init my-config --out my-config.mda
npx @markdown-ai/cli validate my-config.mda --target source --json
npx @markdown-ai/cli integrity verify my-config.mda --target source --json
```

Then load that file at runtime.

TypeScript:

```bash
npm install @snoai/mda-config zod
```

```ts
import { z } from "zod";
import { loadMdaSource } from "@snoai/mda-config";

const Schema = z.object({
  name: z.string(),
  description: z.string(),
  metadata: z.record(z.string(), z.unknown()).optional(),
  integrity: z.record(z.string(), z.unknown()).optional(),
});

const cfg = await loadMdaSource("./preset.mda", Schema, {
  verifyIntegrity: true,
});
```

Python:

```bash
pip install snoai-mda-config pydantic
```

```python
from pydantic import BaseModel
from snoai_mda_config import load_mda_source


class Schema(BaseModel, extra="forbid"):
    name: str
    description: str
    metadata: dict | None = None
    integrity: dict | None = None


cfg = load_mda_source("./preset.mda", schema=Schema, verify_integrity=True)
```

Rust:

```toml
[dependencies]
snoai-mda-config = "1.1"
serde = { version = "1", features = ["derive"] }
```

```rust
use serde::Deserialize;
use snoai_mda_config::{load_mda_source, LoadMdaSourceOptions};

#[derive(Debug, Deserialize)]
struct Config {
    name: String,
    description: String,
}

let cfg: Config = load_mda_source(
    "./preset.mda",
    LoadMdaSourceOptions {
        verify_integrity: true,
        ..Default::default()
    },
)?;
```

## Documentation

- [CLI and runtime quick start](cli-and-runtime-quick-start.md) - complete CLI authoring plus TypeScript runtime flow.
- [Project frontmatter design](design-project-config-frontmatter.md) - how to shape your project-specific `.mda` frontmatter.
- [Trusted runtime policy](trusted-runtime-policy.md) - production trust policy, Rekor, Sigstore, and did:web boundaries.
- [Migrating LLMix presets to MDA](migrate-llmix-presets-to-mda.md) - concrete migration example from YAML presets to MDA configs.
- [Python package README](../../packages/mda-config/python/README.md) - Python usage.
- [Rust package README](../../packages/mda-config/rust/README.md) - Rust usage.
