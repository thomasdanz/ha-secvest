"""Tests for the integration setup."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.secvest.const import DOMAIN

ROOT = Path(__file__).parent.parent


async def test_setup(hass: HomeAssistant) -> None:
    """The integration loads in Home Assistant."""
    assert await async_setup_component(hass, DOMAIN, {})


def test_manifest_matches_domain() -> None:
    """Manifest and HACS metadata describe the same integration."""
    manifest = json.loads(
        (ROOT / "custom_components" / DOMAIN / "manifest.json").read_text()
    )
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert hacs["name"] == manifest["name"]
