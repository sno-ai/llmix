#!/usr/bin/env bash
# Check that each cross-language package family publishes a single version.
# Run in CI to prevent version drift across TypeScript, Python, and Rust.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

read_versions() {
    python - "$REPO_ROOT" <<'PY'
import json
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])

def package_json(path: str) -> str:
    return json.loads((root / path).read_text())["version"]

def pyproject(path: str) -> str:
    return tomllib.loads((root / path).read_text())["project"]["version"]

def cargo_toml(path: str) -> str:
    return tomllib.loads((root / path).read_text())["package"]["version"]

versions = {
    "llmix TypeScript": package_json("packages/llmix/typescript/package.json"),
    "llmix Python": pyproject("packages/llmix/python/pyproject.toml"),
    "llmix Rust": cargo_toml("packages/llmix/rust/Cargo.toml"),
    "mda-config TypeScript": package_json("packages/mda-config/typescript/package.json"),
    "mda-config Python": pyproject("packages/mda-config/python/pyproject.toml"),
    "mda-config Rust": cargo_toml("packages/mda-config/rust/Cargo.toml"),
}

for name, version in versions.items():
    print(f"{name}\t{version}")
PY
}

VERSIONS=$(read_versions)
echo "$VERSIONS" | sed $'s/\t/: /'

LLMIX_VERSIONS=$(echo "$VERSIONS" | awk -F '\t' '/^llmix / { print $2 }' | sort -u | wc -l | tr -d ' ')
MDA_CONFIG_VERSIONS=$(echo "$VERSIONS" | awk -F '\t' '/^mda-config / { print $2 }' | sort -u | wc -l | tr -d ' ')

if [ "$LLMIX_VERSIONS" != "1" ]; then
    echo "ERROR: LLMix package versions do not match"
    exit 1
fi

if [ "$MDA_CONFIG_VERSIONS" != "1" ]; then
    echo "ERROR: mda-config package versions do not match"
    exit 1
fi

echo "OK: package family versions match"
