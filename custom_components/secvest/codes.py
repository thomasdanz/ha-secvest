"""Codes for arming and disarming, configured as subentries.

They are Home Assistant's own: the panel's API can't check a keypad code,
and trying codes against the panel would risk a code tamper alarm. A code
is stored only as a salted hash.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_NAME

from .const import SUBENTRY_CODE

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

# the panel's codes have four digits
CODE_PATTERN = re.compile(r"[0-9]{4}")

CONF_SALT = "salt"
CONF_HASH = "hash"


@dataclass(frozen=True, slots=True)
class Code:
    """One user's code as stored in its subentry."""

    subentry_id: str
    name: str
    salt: str
    hash: str


def hash_code(code: str, salt: str | None = None) -> dict[str, Any]:
    """Return the stored form of a code: a salt and the hash."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), 100_000)
    return {CONF_SALT: salt, CONF_HASH: digest.hex()}


def codes(entry: ConfigEntry) -> list[Code]:
    """Return the configured codes."""
    return [
        Code(
            subentry.subentry_id,
            subentry.data[CONF_NAME],
            subentry.data[CONF_SALT],
            subentry.data[CONF_HASH],
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_CODE
    ]


def matches(stored: Code, code: str) -> bool:
    """Return whether a code is the stored one."""
    return hmac.compare_digest(hash_code(code, stored.salt)[CONF_HASH], stored.hash)


def find(entry: ConfigEntry, code: str | None) -> Code | None:
    """Return the user a code belongs to, if any."""
    if not code or not CODE_PATTERN.fullmatch(code):
        return None
    return next((stored for stored in codes(entry) if matches(stored, code)), None)
