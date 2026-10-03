"""Codes for arming and disarming, kept in the options (#141).

They are Home Assistant's own: the panel's API can't check a keypad code,
and trying codes against the panel would risk a code tamper alarm. A code
is stored only as a salted hash, which protects against casual reading of
the configuration, not against someone with access to Home Assistant's
storage: four digits are 10,000 candidates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_NAME

from .const import CONF_CODES

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# the panel's codes have four digits
CODE_PATTERN = re.compile(r"[0-9]{4}")

CONF_SALT = "salt"
CONF_HASH = "hash"
# the key derivation and its parameters, stored with each code so they can
# change later; codes stored before 0.3 have none and used these (#139)
CONF_KDF = "kdf"
KDF = "pbkdf2-sha256-100000"  # matches _ITERATIONS
_PBKDF2 = re.compile(r"pbkdf2-sha256-([1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class Code:
    """One user's code as stored in the options."""

    name: str
    salt: str
    hash: str
    kdf: str = KDF


_ITERATIONS = 100_000


def _pbkdf2(code: str, salt: str, iterations: int) -> str:
    # blocking: PBKDF2 takes tens of milliseconds, so callers use the executor
    digest = hashlib.pbkdf2_hmac(
        "sha256", code.encode(), bytes.fromhex(salt), iterations
    )
    return digest.hex()


def _digest(code: str, salt: str, kdf: str) -> str | None:
    """Hash a code with the stored parameters; None if they are unknown."""
    match = _PBKDF2.fullmatch(kdf)
    return None if match is None else _pbkdf2(code, salt, int(match[1]))


def hash_code(code: str, salt: str | None = None) -> dict[str, Any]:
    """Return the stored form of a code: salt, hash and parameters. Blocking."""
    salt = salt or secrets.token_hex(16)
    return {
        CONF_SALT: salt,
        CONF_HASH: _pbkdf2(code, salt, _ITERATIONS),
        CONF_KDF: KDF,
    }


def code_of(stored: Mapping[str, Any]) -> Code:
    """Return a code as stored in the options."""
    return Code(
        stored[CONF_NAME],
        stored[CONF_SALT],
        stored[CONF_HASH],
        stored.get(CONF_KDF, KDF),
    )


def codes(entry: ConfigEntry) -> list[Code]:
    """Return the configured codes."""
    return [code_of(stored) for stored in entry.options.get(CONF_CODES, [])]


def matches(stored: Code, code: str) -> bool:
    """Return whether a code is the stored one. Blocking."""
    digest = _digest(code, stored.salt, stored.kdf)
    return digest is not None and hmac.compare_digest(digest, stored.hash)


def _find(stored: Sequence[Code], code: str) -> Code | None:
    return next((candidate for candidate in stored if matches(candidate, code)), None)


async def async_find(
    hass: HomeAssistant, entry: ConfigEntry, code: str | None
) -> Code | None:
    """Return the user a code belongs to, if any.

    All stored codes are compared in one executor job, so arming and
    disarming don't block the event loop.
    """
    if not code or not CODE_PATTERN.fullmatch(code):
        return None
    return await hass.async_add_executor_job(_find, codes(entry), code)


def _prepare(others: Sequence[Code], code: str) -> dict[str, Any] | None:
    if _find(others, code) is not None:
        return None
    return hash_code(code)


async def async_prepare(
    hass: HomeAssistant, others: Sequence[Code], code: str
) -> dict[str, Any] | None:
    """Return the stored form of a new code, or None if another user has it.

    One executor job for the comparison and the hash.
    """
    return await hass.async_add_executor_job(_prepare, others, code)
