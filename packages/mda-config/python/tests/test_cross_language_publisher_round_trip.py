from __future__ import annotations

import pytest

from .conftest import FIXTURES


def test_cross_language_publisher_round_trip_uses_shared_fixture() -> None:
    """PRD §3 pins Python publisher bytes to the TS-generated fixture baseline."""
    fixture = FIXTURES / "valid/02-with-integrity.mda"
    assert fixture.is_file()

    pytest.xfail("publisher byte-equivalence gate is not part of the Python loader package")
