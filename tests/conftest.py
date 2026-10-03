"""Shared fixtures for the tests."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.codes import hash_code
from custom_components.secvest.const import (
    CONF_CODES,
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    DOMAIN,
)

from .common import Setup
from .fake_panel import FakePanel


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture(scope="session")
def panel_certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Create a self-signed certificate for the fake panel, like the panel's."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "secvest")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        # valid for the fake panel's address, so it can also be trusted in a
        # test with certificate verification on
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    directory = tmp_path_factory.mktemp("fake-panel")
    cert_file = directory / "cert.pem"
    key_file = directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture
def fake_panel(
    panel_certificate: tuple[Path, Path], socket_enabled: None
) -> Iterator[FakePanel]:
    """Run a fake panel; the test fails if the client broke its rules."""
    panel = FakePanel(*panel_certificate)
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()
    panel.assert_no_violations()


@pytest.fixture(autouse=True)
def short_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the minimum spacing between rounds short in tests."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.05)


@pytest.fixture
def log_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the log with the first round instead of a minute later."""
    monkeypatch.setattr(coordinator_module, "FIRST_LOG_DELAY", 0)


@pytest.fixture
async def setup(hass: HomeAssistant, fake_panel: FakePanel) -> AsyncIterator[Setup]:
    """Set up an entry for the fake panel; unload it afterwards."""
    entries: list[MockConfigEntry] = []

    async def _setup(
        password: str | None = None,
        data: dict[str, Any] | None = None,
        code: str | None = None,
        **options: Any,
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=fake_panel.name,
            data={
                CONF_URL: fake_panel.url,
                CONF_USER_CODE: fake_panel.user_code,
                CONF_PASSWORD: password or fake_panel.password,
                CONF_VERIFY_SSL: False,
                CONF_USER_AGENT: "",
                **(data or {}),
            },
            options={
                CONF_PARTITIONS: [1],
                # a user's code for arming and disarming, if the test needs one
                CONF_CODES: [{CONF_NAME: "Tester", **hash_code(code)}] if code else [],
                **options,
            },
        )
        entry.add_to_hass(hass)
        entries.append(entry)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    yield _setup
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
