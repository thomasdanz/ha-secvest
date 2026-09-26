"""Shared fixtures for the tests."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

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
