"""MDA preset diffing utility.

Compares two .mda preset files (or versions) and produces a
structured diff showing parameter changes.

Run with:
    uv run python examples/mda-config/python/preset_diff.py old.mda new.mda
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ParamChange:
    field: str
    old_value: Any
    new_value: Any

    @property
    def is_model_change(self) -> bool:
        return self.field in ("model", "provider")


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from an .mda file."""
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Malformed .mda file: {path}")
    return yaml.safe_load(parts[1])


def diff_presets(old_path: Path, new_path: Path) -> list[ParamChange]:
    """Compute structured diff between two preset versions."""
    old_fm = parse_frontmatter(old_path)
    new_fm = parse_frontmatter(new_path)

    changes: list[ParamChange] = []
    all_keys = set(old_fm.keys()) | set(new_fm.keys())

    for key in sorted(all_keys):
        old_val = old_fm.get(key)
        new_val = new_fm.get(key)
        if old_val != new_val:
            changes.append(ParamChange(field=key, old_value=old_val, new_value=new_val))

    return changes


def format_diff(changes: list[ParamChange], old_path: Path, new_path: Path) -> str:
    """Format diff output for terminal display."""
    lines = [
        f"Diff: {old_path.name} → {new_path.name}",
        f"{'─' * 50}",
    ]

    if not changes:
        lines.append("  No parameter changes detected.")
        return "\n".join(lines)

    model_changes = [c for c in changes if c.is_model_change]
    param_changes = [c for c in changes if not c.is_model_change]

    if model_changes:
        lines.append("\n  Model changes (requires validation):")
        for c in model_changes:
            lines.append(f"    {c.field}: {c.old_value!r} → {c.new_value!r}")

    if param_changes:
        lines.append("\n  Parameter changes:")
        for c in param_changes:
            lines.append(f"    {c.field}: {c.old_value!r} → {c.new_value!r}")

    lines.append(f"\n  Total: {len(changes)} change(s)")
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python preset_diff.py <old.mda> <new.mda>")
        sys.exit(1)

    old_path = Path(sys.argv[1])
    new_path = Path(sys.argv[2])

    for p in (old_path, new_path):
        if not p.exists():
            print(f"Error: {p} does not exist")
            sys.exit(1)

    changes = diff_presets(old_path, new_path)
    print(format_diff(changes, old_path, new_path))

    # Exit 1 if there are model/provider changes (for CI gates)
    if any(c.is_model_change for c in changes):
        sys.exit(1)


if __name__ == "__main__":
    main()
