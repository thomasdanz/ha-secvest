"""Tests for the integration setup."""

import json
from pathlib import Path
import struct
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


def test_brand_images() -> None:
    """The icon ships with the integration as a transparent square PNG."""
    brand = ROOT / "custom_components" / DOMAIN / "brand"
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        data = (brand / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        width, height = struct.unpack(">II", data[16:24])
        # colour type 6: RGBA, so the background is transparent
        assert (width, height, data[25]) == (size, size, 6), name
