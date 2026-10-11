"""Reading the web interface's answers.

Structure only: ids and value formats, never labels or other UI texts.
"""

from html import unescape
import re

from ..api.errors import CommunicationError
from .models import Level, WebZoneStatus

# rows of the wireless zone list are counted from 0 for zone 201
FIRST_WIRELESS_ZONE = 201

_SSID_INPUT = re.compile(r"<input\b[^>]*\bid\s*=\s*[\"']ssid[\"'][^>]*>", re.IGNORECASE)
_VALUE = re.compile(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
# one update of the page: <disp><id>rtm8_0</id><value>none</value></disp>;
# matched as text instead of an XML parser, which is open to entity attacks
_UPDATE = re.compile(
    r"<(disp|text|bargraph|vis)>\s*<id>\s*rtm(\d+)_(\d+)\s*</id>\s*"
    r"<value>([^<]*)</value>\s*</\1>"
)
# "9(3)", "9<br/>(3)" (rtm2) or "-(-)" for no value; rtm6 ends with a space
_LEVEL = re.compile(r"(\d)\s*(?:<br\s*/?>)?\s*\((\d|-)\)")
_NO_LEVEL = re.compile(r"-\s*(?:<br\s*/?>)?\s*\(-\)")

# columns of the zone list (see the specification, POST /sec_zones.cgx); the
# bar (1), omitted (9) and open (10) aren't used: the REST API reports those
_SIGNAL_SHOWN = 2
_SUPERVISION = 5
_REPEATER = 6
_SIGNAL = 7
_BATTERY = 8
_TAMPER = 11


def session_id(page: bytes) -> str | None:
    """Return the session id of a login answer, None if the login failed.

    A successful login answers with the login page again, with the session id
    in the hidden input "ssid"; a failed one with "0" there (observed).
    """
    text = page.decode("utf-8", "replace")
    tag = _SSID_INPUT.search(text)
    if tag is None:
        return None
    value = _VALUE.search(tag.group(0))
    if value is None or not value.group(1).isdigit() or int(value.group(1)) == 0:
        return None
    return value.group(1)


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        # other answers of the web interface are ISO-8859-1
        return body.decode("latin-1")


def _level(text: str | None) -> tuple[Level | None, bool]:
    """Return the level and whether the text was understood."""
    text = (text or "").strip()
    if not text or _NO_LEVEL.fullmatch(text):
        return None, True
    match = _LEVEL.fullmatch(text)
    if match is None:
        return None, False
    lowest = None if match[2] == "-" else int(match[2])
    return Level(int(match[1]), lowest), True


def _shown(value: str | None) -> tuple[bool | None, bool]:
    """Return whether a status icon is shown, and whether that is known."""
    if value == "block":
        return True, True
    if value == "none":
        return False, True
    return None, False


def zone_statuses(body: bytes) -> tuple[dict[str, WebZoneStatus], list[str]]:
    """Return the used wireless zones of a zone list answer, by zone id.

    Rows without any signal strength are unused slots and left out. The
    second value lists what couldn't be read (kept unknown), for the log.
    """
    text = _decode(body)
    if not text.lstrip().startswith("<form>"):
        raise CommunicationError("the answer isn't the zone list's data")
    rows: dict[int, dict[int, str]] = {}
    for match in _UPDATE.finditer(text):
        column, row = int(match[2]), int(match[3])
        rows.setdefault(row, {})[column] = unescape(match[4])
    statuses: dict[str, WebZoneStatus] = {}
    unknown: list[str] = []
    for row, values in sorted(rows.items()):
        zone_id = str(FIRST_WIRELESS_ZONE + row)
        shown = values.get(_SIGNAL) or values.get(_SIGNAL_SHOWN)
        if not (shown or "").strip():
            continue
        signal, ok = _level(shown)
        if not ok:
            unknown.append(f"zone {zone_id} signal {shown!r}")
        repeater, ok = _level(values.get(_REPEATER))
        if not ok:
            unknown.append(f"zone {zone_id} repeater {values.get(_REPEATER)!r}")
        flags: dict[int, bool | None] = {}
        for column in (_BATTERY, _SUPERVISION, _TAMPER):
            flags[column], ok = _shown(values.get(column))
            if not ok:
                unknown.append(f"zone {zone_id} rtm{column} {values.get(column)!r}")
        statuses[zone_id] = WebZoneStatus(
            zone_id=zone_id,
            signal=signal,
            repeater_signal=repeater,
            battery_low=flags[_BATTERY],
            supervision_fault=flags[_SUPERVISION],
            tamper=flags[_TAMPER],
        )
    return statuses, unknown
