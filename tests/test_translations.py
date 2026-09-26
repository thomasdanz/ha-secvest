"""Tests for the UI texts (#42)."""

import json
from pathlib import Path
import re
from typing import Any

import pytest

TRANSLATIONS = Path(__file__).parent.parent / "custom_components/secvest/translations"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _flatten(data: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(data, dict):
        result: dict[str, str] = {}
        for key, value in data.items():
            result.update(_flatten(value, f"{prefix}{key}."))
        return result
    return {prefix.rstrip("."): data}


def _load(language: str) -> dict[str, str]:
    return _flatten(json.loads((TRANSLATIONS / f"{language}.json").read_text()))


def test_same_texts_in_english_and_german() -> None:
    """Every text exists in both languages."""
    assert sorted(_load("de")) == sorted(_load("en"))


def test_same_placeholders() -> None:
    """Both languages use the same placeholders in each text."""
    english, german = _load("en"), _load("de")
    for key, text in english.items():
        assert set(PLACEHOLDER.findall(german[key])) == set(
            PLACEHOLDER.findall(text)
        ), key


@pytest.mark.parametrize(
    ("english", "german"),
    [("partition", "Teilbereich"), ("installer", "Errichter"), ("fault", "Störung")],
)
def test_glossary_terms(english: str, german: str) -> None:
    """Where a text uses a glossary term in English, German uses its term too."""
    en, de = _load("en"), _load("de")
    word = re.compile(rf"\b{english}", re.IGNORECASE)
    for key, text in en.items():
        if word.search(PLACEHOLDER.sub("", text)):
            assert german.lower() in de[key].lower(), key
