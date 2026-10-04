"""Release notes from the changelog for the release workflow (#48)."""

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_notes", ROOT / "scripts" / "release_notes.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-10-04

### Added

- Something (#2).

## [0.1.0] - 2026-09-27

- First (#1).

[Unreleased]: https://example.org/compare/v0.2.0...HEAD
[0.2.0]: https://example.org/compare/v0.1.0...v0.2.0
"""


def test_section() -> None:
    """The body of a version's section, up to the next one or the links."""
    section = _script().section
    assert section(CHANGELOG, "0.2.0") == "### Added\n\n- Something (#2)."
    assert section(CHANGELOG, "0.1.0") == "- First (#1)."
    assert section(CHANGELOG, "0.3.0") is None


def test_current_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The manifest's version has a changelog section; another tag fails."""
    script = _script()
    version = json.loads(
        (ROOT / "custom_components/secvest/manifest.json").read_text()
    )["version"]
    monkeypatch.setattr(sys, "argv", ["release_notes.py", f"v{version}"])
    assert script.main() == 0
    assert capsys.readouterr().out.strip()
    monkeypatch.setattr(sys, "argv", ["release_notes.py", "v9.9.9"])
    assert script.main() == 1
