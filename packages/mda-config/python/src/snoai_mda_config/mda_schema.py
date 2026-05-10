"""MDA §02 source-mode frontmatter JSON Schema bundled for runtime validation."""

from __future__ import annotations

from typing import Any, Final

MDA_SOURCE_SCHEMA: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://mda.sno.dev/spec/v1.0/schemas/frontmatter-source.schema.json",
    "title": "MDA source frontmatter",
    "type": "object",
    "$defs": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        },
        "description": {"type": "string", "minLength": 1, "maxLength": 1024},
        "iso8601": {"type": "string", "format": "date-time"},
        "requires": {
            "type": "object",
            "propertyNames": {
                "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
                "minLength": 1,
                "maxLength": 64,
            },
            "additionalProperties": True,
        },
        "versionRange": {
            "type": "string",
            "pattern": r"^\^?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$",
        },
        "dependsOn": {
            "type": "object",
            "required": ["name", "version-range"],
            "properties": {
                "name": {"$ref": "#/$defs/name"},
                "version-range": {"$ref": "#/$defs/versionRange"},
                "digest": {
                    "type": "string",
                    "pattern": (
                        "^(sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|"
                        "sha512:[0-9a-f]{128})$"
                    ),
                },
            },
            "additionalProperties": False,
        },
        "integrity": {
            "type": "object",
            "required": ["algorithm", "digest"],
            "properties": {
                "algorithm": {"type": "string", "enum": ["sha256", "sha384", "sha512"]},
                "digest": {"type": "string", "pattern": "^(sha256|sha384|sha512):[0-9a-f]+$"},
            },
            "additionalProperties": False,
            "allOf": [
                {
                    "if": {"properties": {"algorithm": {"const": "sha256"}}},
                    "then": {"properties": {"digest": {"pattern": "^sha256:[0-9a-f]{64}$"}}},
                },
                {
                    "if": {"properties": {"algorithm": {"const": "sha384"}}},
                    "then": {"properties": {"digest": {"pattern": "^sha384:[0-9a-f]{96}$"}}},
                },
                {
                    "if": {"properties": {"algorithm": {"const": "sha512"}}},
                    "then": {"properties": {"digest": {"pattern": "^sha512:[0-9a-f]{128}$"}}},
                },
            ],
        },
        "signature": {
            "type": "object",
            "required": ["signer", "key-id", "payload-digest", "algorithm", "signature"],
            "properties": {
                "signer": {"type": "string", "pattern": r"^(sigstore-oidc:[^#]+|did-web:[^#]+)$"},
                "key-id": {"type": "string", "minLength": 1},
                "payload-digest": {
                    "type": "string",
                    "pattern": (
                        "^(sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|"
                        "sha512:[0-9a-f]{128})$"
                    ),
                },
                "algorithm": {
                    "type": "string",
                    "enum": ["ed25519", "ecdsa-p256", "rsa-pss-sha256"],
                },
                "signature": {"type": "string", "minLength": 1},
                "rekor-log-id": {"type": "string"},
                "rekor-log-index": {"type": "integer", "minimum": 0},
                "payload-type": {
                    "type": "string",
                    "pattern": (
                        r"^application/vnd\.[a-z0-9][a-z0-9-]*"
                        r"(\.[a-z0-9][a-z0-9-]*)+\+json$"
                    ),
                },
            },
            "additionalProperties": False,
            "allOf": [
                {
                    "if": {"properties": {"signer": {"pattern": "^sigstore-oidc:"}}},
                    "then": {"required": ["rekor-log-id", "rekor-log-index"]},
                },
                {
                    "if": {"properties": {"signer": {"pattern": "^did-web:"}}},
                    "then": {
                        "not": {
                            "anyOf": [
                                {"required": ["rekor-log-id"]},
                                {"required": ["rekor-log-index"]},
                            ]
                        }
                    },
                },
            ],
        },
        "metadata": {
            "type": "object",
            "patternProperties": {
                "^[a-z0-9]+(-[a-z0-9]+)*$": {
                    "type": "object",
                    "additionalProperties": True,
                }
            },
            "additionalProperties": False,
        },
    },
    "properties": {
        "name": {"$ref": "#/$defs/name"},
        "description": {"$ref": "#/$defs/description"},
        "license": {"type": "string"},
        "compatibility": {"type": "string", "maxLength": 500},
        "allowed-tools": {"type": "string"},
        "metadata": {"$ref": "#/$defs/metadata"},
        "integrity": {"$ref": "#/$defs/integrity"},
        "signatures": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/signature"},
        },
        "doc-id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{8,}$"},
        "title": {"type": "string"},
        "version": {
            "type": "string",
            "pattern": r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$",
        },
        "requires": {"$ref": "#/$defs/requires"},
        "depends-on": {
            "type": "array",
            "items": {"$ref": "#/$defs/dependsOn"},
        },
        "author": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "created-date": {"$ref": "#/$defs/iso8601"},
        "updated-date": {"$ref": "#/$defs/iso8601"},
        "relationships": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    "dependentRequired": {"signatures": ["integrity"]},
    "additionalProperties": False,
}
