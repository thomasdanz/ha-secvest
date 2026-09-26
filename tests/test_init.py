"""Tests for the integration setup."""

import json
from pathlib import Path
import tomllib

from custom_components.secvest.const import DOMAIN

ROOT = Path(__file__).parent.parent


def test_manifest_matches_domain() -> None:
    """Manifest and HACS metadata describe the same integration."""
    manifest = json.loads(
        (ROOT / "custom_components" / DOMAIN / "manifest.json").read_text()
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN
    # one version for the integration and the project
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == pyproject["project"]["version"]
    assert hacs["name"] == manifest["name"]
