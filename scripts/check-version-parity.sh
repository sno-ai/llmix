#!/usr/bin/env bash
# Check that Python and TypeScript versions match.
# Run in CI to prevent version drift between dual-language packages.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TS_VERSION=$(node -e "console.log(require('$REPO_ROOT/packages/llmix/typescript/package.json').version)")
PY_VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' "$REPO_ROOT/packages/llmix/python/llmix/__init__.py")

echo "TypeScript version: $TS_VERSION"
echo "Python version:     $PY_VERSION"

if [ "$TS_VERSION" != "$PY_VERSION" ]; then
    echo "ERROR: Version mismatch! TypeScript=$TS_VERSION Python=$PY_VERSION"
    exit 1
fi

echo "OK: Versions match ($TS_VERSION)"
