"""MDA hot-reload file watcher example.

Monitors a preset directory for .mda file changes and hot-reloads
updated configs into the live pipeline registry without restart.

Requires: pip install watchfiles pyyaml

Run with:
    uv run python examples/mda-config/python/hot_reload_watcher.py ./fixtures/mda/community/
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from watchfiles import awatch, Change


class PresetRegistry:
    """In-memory preset registry with hot-reload support."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._presets: dict[str, dict[str, Any]] = {}
        self._load_all()

    def _parse_mda(self, path: Path) -> dict[str, Any] | None:
        try:
            content = path.read_text(encoding="utf-8")
            parts = content.split("---", 2)
            if len(parts) < 3:
                return None
            frontmatter = yaml.safe_load(parts[1])
            return {
                "name": frontmatter.get("name", path.stem),
                "config": {
                    "provider": frontmatter["provider"],
                    "model": frontmatter["model"],
                    "common": {
                        "temperature": frontmatter.get("temperature", 0.7),
                        "max_output_tokens": frontmatter.get("max_output_tokens", 1024),
                    },
                },
            }
        except (yaml.YAMLError, KeyError) as e:
            print(f"  [ERROR] Failed to parse {path.name}: {e}")
            return None

    def _load_all(self) -> None:
        for mda_file in self.directory.glob("*.mda"):
            preset = self._parse_mda(mda_file)
            if preset:
                self._presets[preset["name"]] = preset

    def reload_file(self, path: Path) -> None:
        preset = self._parse_mda(path)
        if preset:
            self._presets[preset["name"]] = preset
            print(f"  [RELOAD] Updated preset: {preset['name']}")

    def remove_file(self, path: Path) -> None:
        name = path.stem
        if name in self._presets:
            del self._presets[name]
            print(f"  [REMOVE] Deleted preset: {name}")

    def get(self, name: str) -> dict[str, Any] | None:
        preset = self._presets.get(name)
        return preset["config"] if preset else None

    @property
    def preset_names(self) -> list[str]:
        return list(self._presets.keys())


async def watch_presets(registry: PresetRegistry) -> None:
    """Watch for .mda file changes and hot-reload."""
    print(f"Watching {registry.directory} for changes...")
    print(f"Loaded presets: {registry.preset_names}\n")

    async for changes in awatch(registry.directory):
        for change_type, path_str in changes:
            path = Path(path_str)
            if path.suffix != ".mda":
                continue

            if change_type in (Change.added, Change.modified):
                registry.reload_file(path)
            elif change_type == Change.deleted:
                registry.remove_file(path)

        print(f"  Active presets: {registry.preset_names}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python hot_reload_watcher.py <mda_directory>")
        sys.exit(1)

    directory = Path(sys.argv[1])
    registry = PresetRegistry(directory)

    try:
        asyncio.run(watch_presets(registry))
    except KeyboardInterrupt:
        print("\nStopped watching.")


if __name__ == "__main__":
    main()
