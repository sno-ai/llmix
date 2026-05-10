"""MDA §02-1.1 frontmatter extraction and YAML 1.2 parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .errors import ErrorCategory, MdaConfigError


@dataclass(frozen=True, slots=True)
class ExtractedFrontmatter:
    """MDA §02-1.1 extraction result."""

    frontmatter_str: str
    body_str: str


def extract_frontmatter(file_bytes: bytes) -> ExtractedFrontmatter:
    """MDA §02-1.1 extracts frontmatter and body from raw UTF-8 bytes."""
    if file_bytes.startswith(b"\xef\xbb\xbf"):
        file_bytes = file_bytes[3:]

    try:
        decoded = file_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as cause:
        raise MdaConfigError(
            ErrorCategory.InvalidEncoding,
            "file bytes are not valid UTF-8",
            {"cause": str(cause)},
        ) from cause

    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return ExtractedFrontmatter(frontmatter_str="", body_str=normalized)

    after_open = 4
    cursor = after_open
    while cursor <= len(normalized):
        nl_idx = normalized.find("\n", cursor)
        line_end = len(normalized) if nl_idx == -1 else nl_idx
        line = normalized[cursor:line_end]
        if line == "---":
            body_start = len(normalized) if nl_idx == -1 else nl_idx + 1
            return ExtractedFrontmatter(
                frontmatter_str=normalized[after_open:cursor],
                body_str=normalized[body_start:],
            )
        if nl_idx == -1:
            break
        cursor = nl_idx + 1

    raise MdaConfigError(
        ErrorCategory.UnterminatedFrontmatter,
        "opening '---' fence has no matching closing '---' line",
    )


def parse_frontmatter_yaml(frontmatter_str: str) -> dict[str, Any]:
    """MDA §02-1.1 parses extracted frontmatter as YAML 1.2 core schema."""
    if frontmatter_str == "":
        return {}

    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    try:
        parsed = cast(object, yaml.load(frontmatter_str))  # type: ignore[reportUnknownMemberType]
    except YAMLError as cause:
        raise MdaConfigError(
            ErrorCategory.FrontmatterYamlParseError,
            "YAML parse failed",
            {"cause": str(cause)},
        ) from cause

    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise MdaConfigError(
            ErrorCategory.FrontmatterYamlParseError,
            "frontmatter MUST parse to a YAML mapping (object), not a scalar or sequence",
        )
    parsed_mapping = cast("Mapping[Any, Any]", parsed)
    return {str(key): value for key, value in parsed_mapping.items()}
