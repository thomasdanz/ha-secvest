"""The panel's certificate as shown in the flows (#149)."""

from __future__ import annotations

from cryptography import x509


def format_fingerprint(fingerprint: str) -> str:
    """Return a hex fingerprint as colon-separated pairs, as browsers show it."""
    upper = fingerprint.upper()
    return ":".join(upper[i : i + 2] for i in range(0, len(upper), 2))


def certificate_details(der: bytes, fingerprint: str) -> dict[str, str]:
    """Return subject, issuer, validity and fingerprint for a form.

    Only displayed; nothing is decided from them (principle 4). A
    certificate that can't be parsed still shows its fingerprint.
    """
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        unknown = "?"
        return {
            "subject": unknown,
            "issuer": unknown,
            "valid_from": unknown,
            "valid_until": unknown,
            "fingerprint": format_fingerprint(fingerprint),
        }
    return {
        "subject": cert.subject.rfc4514_string() or "-",
        "issuer": cert.issuer.rfc4514_string() or "-",
        "valid_from": f"{cert.not_valid_before_utc:%Y-%m-%d}",
        "valid_until": f"{cert.not_valid_after_utc:%Y-%m-%d}",
        "fingerprint": format_fingerprint(fingerprint),
    }
