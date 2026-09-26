"""Fixtures for the API client tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from custom_components.secvest.api import parsing

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> bytes:
    """Return a response of the specification (tests/fixtures, ADR 0004)."""
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def reset_unknown_values() -> Iterator[None]:
    """Forget which unknown values were logged, so each test starts fresh."""
    parsing._reported_unknown.clear()
    yield
    parsing._reported_unknown.clear()
