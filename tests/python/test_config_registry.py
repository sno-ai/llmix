#!/usr/bin/env python3
"""Coverage for the Python Config Registry publish/load/reload path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from llmix import ConfigRegistryManager, ConfigRegistryPublishOptions, ConfigRegistryPublisher
from llmix.types import ConfigNotFoundError, InvalidConfigError, SecurityError


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_authoring_preset(
    root: Path,
    module: str,
    preset: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
) -> Path:
    path = root / "authoring" / module / f"{preset}.mda"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "name": preset,
        "description": f"{module}/{preset}",
        "metadata": {
            "snoai-llmix": {
                "common": {
                    "provider": provider,
                    "model": model,
                    "temperature": temperature,
                }
            }
        },
    }
    path.write_text(f"---\n{json.dumps(frontmatter, indent=2)}\n---\n# {preset}\n", encoding="utf-8")
    return path


def test_publish_creates_active_revision_and_manager_reads_resolved_json(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-5-mini", temperature=0.7)

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    config = manager.get_preset("search", "summary")

    assert published.activated is True
    assert manager.active_revision == published.revision
    assert config["provider"] == "openai"
    assert config["model"] == "gpt-5-mini"
    assert config["common"]["temperature"] == 0.7
    assert "search/summary" in manager.available_presets()
    assert manager.last_successful_reload_at is not None
    assert manager.last_reload_failure_at is None
    snapshot_dir = root / "snapshots" / published.revision
    assert (snapshot_dir / "authoring" / "search" / "summary.mda").exists()
    assert (snapshot_dir / "resolved" / "search" / "summary.json").exists()


def test_manager_reloads_after_current_revision_changes(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    first = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(root, "search", "summary", model="gpt-5-mini", temperature=0.9)
    second = ConfigRegistryPublisher(root).publish()

    config = manager.get_preset("search", "summary")

    assert first.revision != second.revision
    assert manager.active_revision == second.revision
    assert config["model"] == "gpt-5-mini"
    assert config["common"]["temperature"] == 0.9


def test_available_presets_refreshes_after_current_revision_changes(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")

    ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(root, "rerank", "default")
    ConfigRegistryPublisher(root).publish()

    assert manager.available_presets() == ("rerank/default", "search/summary")


def test_manager_rolls_back_when_current_revision_points_to_older_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    first = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(root, "search", "summary", model="gpt-5-mini", temperature=0.9)
    second = ConfigRegistryPublisher(root).publish()
    assert second.revision != first.revision
    assert manager.get_preset("search", "summary")["model"] == "gpt-5-mini"

    (root / "current.json").write_text(json.dumps({"revision": first.revision}) + "\n", encoding="utf-8")
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == first.revision
    assert config["model"] == "gpt-4.1-mini"
    assert config["common"]["temperature"] == 0.2


def test_manager_ignores_authoring_edits_until_a_new_revision_is_published(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    _write_authoring_preset(root, "search", "summary", model="gpt-5-mini", temperature=0.9)
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == published.revision
    assert config["model"] == "gpt-4.1-mini"
    assert config["common"]["temperature"] == 0.2


def test_manager_reads_resolved_json_without_mda_loader_hot_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")
    ConfigRegistryPublisher(root).publish()

    def fail_loader(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("manager must not parse MDA at runtime")

    monkeypatch.setattr("llmix.config_registry.load_mda_config", fail_loader)

    manager = ConfigRegistryManager.open(root)
    assert manager.get_preset("search", "summary")["provider"] == "openai"


def test_manager_fails_fast_without_active_revision(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    root.mkdir(parents=True)

    with pytest.raises(ConfigNotFoundError):
        ConfigRegistryManager.open(root)


def test_manager_fails_fast_with_malformed_current_pointer(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    root.mkdir(parents=True)
    (root / "current.json").write_text('{"revision":42}\n', encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryManager.open(root)


def test_manager_keeps_last_known_good_config_when_pointer_changes_to_missing_revision(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    published = ConfigRegistryPublisher(root).publish()
    manager = ConfigRegistryManager.open(root)

    (root / "current.json").write_text('{"revision":"missing-revision"}\n', encoding="utf-8")
    config = manager.get_preset("search", "summary")

    assert manager.active_revision == published.revision
    assert config["model"] == "gpt-4.1-mini"
    assert isinstance(manager.last_reload_error, ConfigNotFoundError)
    assert manager.last_successful_reload_at is not None
    assert manager.last_reload_failure_at is not None


def test_publish_failure_leaves_active_revision_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    first = ConfigRegistryPublisher(root).publish()
    broken_path = root / "authoring" / "search" / "summary.mda"
    broken_path.write_text("---\nname: broken\nmetadata: [broken\n---\n", encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryPublisher(root).publish()

    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert pointer["revision"] == first.revision
    assert not any(path.name.endswith(".tmp") for path in (root / "snapshots" / ".staging").glob("*"))


def test_legacy_yaml_authoring_blocks_publish_without_changing_active_revision(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary")
    first = ConfigRegistryPublisher(root).publish()

    legacy_path = root / "authoring" / "search" / "legacy.yaml"
    legacy_path.write_text("provider: openai\nmodel: gpt-4.1-mini\n", encoding="utf-8")

    with pytest.raises(InvalidConfigError, match=r"\.mda"):
        ConfigRegistryPublisher(root).publish()

    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert pointer["revision"] == first.revision


def test_publish_rejects_authoring_module_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    outside_root = tmp_path / "outside"
    _write_authoring_preset(outside_root, "search", "summary")

    authoring_dir = root / "authoring"
    authoring_dir.mkdir(parents=True)
    try:
        (authoring_dir / "search").symlink_to(outside_root / "authoring" / "search", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        ConfigRegistryPublisher(root).publish()


def test_publish_rejects_authoring_file_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    outside_path = _write_authoring_preset(tmp_path / "outside", "search", "summary")
    module_dir = root / "authoring" / "search"
    module_dir.mkdir(parents=True)

    try:
        (module_dir / "summary.mda").symlink_to(outside_path)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")

    with pytest.raises(SecurityError):
        ConfigRegistryPublisher(root).publish()


def test_publish_can_enforce_mda_integrity(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    path = _write_authoring_preset(root, "search", "summary")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"metadata"', '"integrity": {"algorithm": "sha256", "digest": "sha256:bad"},\n  "metadata"'), encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryPublisher(root).publish(options=ConfigRegistryPublishOptions(verify_integrity=True))


def test_manager_rejects_tampered_resolved_snapshot_on_startup(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    published = ConfigRegistryPublisher(root).publish()
    resolved_path = root / "snapshots" / published.revision / "resolved" / "search" / "summary.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["model"] = "tampered-model"
    resolved_path.write_text(json.dumps(resolved), encoding="utf-8")

    with pytest.raises(InvalidConfigError):
        ConfigRegistryManager.open(root)


def test_manager_revalidates_resolved_json_shape_on_startup(tmp_path: Path) -> None:
    root = tmp_path / "config" / "llm"
    _write_authoring_preset(root, "search", "summary", model="gpt-4.1-mini", temperature=0.2)

    published = ConfigRegistryPublisher(root).publish()
    snapshot_dir = root / "snapshots" / published.revision
    manifest_path = snapshot_dir / "manifest.json"
    resolved_path = snapshot_dir / "resolved" / "search" / "summary.json"
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved["common"]["temperature"] = 3
    resolved_bytes = _canonical_json_bytes(resolved)
    resolved_path.write_bytes(resolved_bytes)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presets"]["search/summary"]["resolved_sha256"] = hashlib.sha256(resolved_bytes).hexdigest()
    manifest_path.write_bytes(_canonical_json_bytes(manifest))

    with pytest.raises(InvalidConfigError, match="temperature"):
        ConfigRegistryManager.open(root)
