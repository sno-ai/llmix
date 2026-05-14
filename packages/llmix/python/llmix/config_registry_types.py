"""Config Registry public and internal data types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REGISTRY_ROOT_SCHEMA = "llmix.config-registry.root"
REGISTRY_ROOT_SCHEMA_VERSION = 1
REGISTRY_ROOT_ENVELOPE_SCHEMA = "llmix.config-registry.root-envelope"
REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION = 1
REGISTRY_ROOT_PAYLOAD_TYPE = "application/vnd.snoai.llmix.registry-root+json"
REGISTRY_ROOT_FILENAME = "registry-root.json"


@dataclass(frozen=True)
class PublishedRevision:
    """Result of a successful registry publish."""

    revision: str
    compiled_path: Path
    manifest_path: Path
    manifest_sha256: str
    activated: bool
    preset_ids: tuple[str, ...]
    registry_root_path: Path | None = None
    registry_root_sha256: str | None = None


@dataclass(frozen=True)
class ConfigRegistryPublishOptions:
    """Options for publishing source MDA into an immutable compiled revision."""

    verify_integrity: bool = False
    verify_signatures: bool = False
    trusted_runtime: bool = False
    enforce_requires: bool = False
    allowed_networks: list[str] | None = None
    trust_policy: Any | None = None
    rekor_client: Any | None = None
    sigstore_verifier: Any | None = None
    did_web_verifier: Any | None = None
    registry_root: RegistryRootSigningOptions | None = None


@dataclass(frozen=True)
class _PresetSource:
    module: str
    preset: str
    preset_id: str
    source_path: Path


@dataclass(frozen=True)
class RegistryRootSigningInput:
    payload: dict[str, Any]
    canonical_payload: str
    integrity: dict[str, str]
    payload_type: str
    payload_sha256: str


RegistryRootSigner = Callable[
    [RegistryRootSigningInput],
    dict[str, Any] | list[dict[str, Any]] | tuple[dict[str, Any], ...],
]


@dataclass(frozen=True)
class RegistryRootSigningOptions:
    signer: RegistryRootSigner
    min_signatures: int = 1


@dataclass(frozen=True)
class RegistryRootFreshnessInput(RegistryRootSigningInput):
    envelope: dict[str, Any]


RegistryRootHighWatermark = Callable[[RegistryRootFreshnessInput], bool]


@dataclass(frozen=True)
class RegistryRootVerificationOptions:
    trust_policy: Any
    rekor_client: Any | None = None
    sigstore_verifier: Any | None = None
    did_web_verifier: Any | None = None
    expected_revision: str | None = None
    expected_root_digest: str | None = None
    minimum_revision: str | None = None
    minimum_published_at: str | datetime | None = None
    high_watermark: RegistryRootHighWatermark | None = None


@dataclass(frozen=True)
class ConfigRegistryOpenOptions:
    signed_root: RegistryRootVerificationOptions | None = None
