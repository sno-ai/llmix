from __future__ import annotations

import pytest

from snoai_mda_config import ErrorCategory, MdaConfigError
from snoai_mda_config.frontmatter import extract_frontmatter, parse_frontmatter_yaml

from .conftest import FIXTURES


def test_strips_utf8_bom() -> None:
    extracted = extract_frontmatter((FIXTURES / "valid/05-bom-prefixed.mda").read_bytes())

    assert "name: bom-prefixed" in extracted.frontmatter_str
    assert "# BOM" in extracted.body_str


def test_normalizes_crlf_to_lf() -> None:
    extracted = extract_frontmatter((FIXTURES / "valid/06-crlf-line-endings.mda").read_bytes())

    assert "\r" not in extracted.frontmatter_str
    assert "\r" not in extracted.body_str
    assert "name: crlf-config" in extracted.frontmatter_str


def test_body_horizontal_rules_stay_in_body() -> None:
    extracted = extract_frontmatter((FIXTURES / "valid/07-body-contains-hr.mda").read_bytes())

    assert "name: body-with-hr" in extracted.frontmatter_str
    assert extracted.body_str.count("\n---\n") >= 1


def test_accepts_empty_body() -> None:
    extracted = extract_frontmatter((FIXTURES / "valid/08-empty-body.mda").read_bytes())

    assert "name: empty-body" in extracted.frontmatter_str
    assert extracted.body_str == ""


def test_refuses_unterminated_frontmatter() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        extract_frontmatter((FIXTURES / "invalid/09-unterminated-frontmatter.mda").read_bytes())

    assert exc_info.value.category is ErrorCategory.UnterminatedFrontmatter


def test_refuses_non_utf8_bytes() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        extract_frontmatter((FIXTURES / "invalid/13-non-utf8.mda").read_bytes())

    assert exc_info.value.category is ErrorCategory.InvalidEncoding


def test_yaml_1_2_keeps_yes_no_as_strings() -> None:
    parsed = parse_frontmatter_yaml("network: no\nflag: yes")

    assert parsed["network"] == "no"
    assert parsed["flag"] == "yes"


def test_yaml_parse_errors_use_frontmatter_category() -> None:
    with pytest.raises(MdaConfigError) as exc_info:
        parse_frontmatter_yaml('description: "unbalanced')

    assert exc_info.value.category is ErrorCategory.FrontmatterYamlParseError
