"""Reading the web interface's answers (#56)."""

import ast
from pathlib import Path

import pytest

from custom_components.secvest.api.errors import CommunicationError
from custom_components.secvest.webui.models import Level, PanelInfo
from custom_components.secvest.webui.parsing import (
    panel_info,
    session_id,
    zone_statuses,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _row(row: int, **values: str) -> str:
    """Render a row of the zone list with the given column values."""
    columns = {"rtm8": "none", "rtm5": "none", "rtm11": "none", **values}
    parts = []
    for name, value in columns.items():
        tag = "disp" if name in {"rtm5", "rtm8", "rtm9", "rtm10", "rtm11"} else "text"
        parts.append(f"<{tag}><id>{name}_{row}</id><value>{value}</value></{tag}>")
    return "".join(parts)


def _form(*rows: str) -> bytes:
    return ("<form><read-only>no</read-only>" + "".join(rows) + "</form>").encode()


def test_captured_zone_list() -> None:
    """The captured answer: 19 used zones, 29 empty slots, nothing unknown."""
    body = (FIXTURES / "POST_sec_zones.cgx.xml").read_bytes()
    statuses, unknown = zone_statuses(body)
    assert unknown == []
    assert sorted(statuses) == [str(z) for z in range(201, 220)]
    first = statuses["201"]
    assert first.signal == Level(9, 3)
    assert first.repeater_signal is None
    assert (first.battery_low, first.supervision_fault, first.tamper) == (
        False,
        False,
        False,
    )
    # received through the repeater, hardly directly
    assert statuses["212"].signal == Level(0, 0)
    assert statuses["212"].repeater_signal == Level(6, 0)


def test_captured_zone_list_with_an_omitted_zone() -> None:
    """Omitted (rtm9) is no status the client reads; the row stays the same."""
    statuses, unknown = zone_statuses(
        (FIXTURES / "POST_sec_zones.cgx.example2.xml").read_bytes()
    )
    assert unknown == []
    assert len(statuses) == 19


@pytest.mark.parametrize(
    ("columns", "battery", "supervision", "tamper"),
    [
        ({"rtm8": "block"}, True, False, False),
        ({"rtm5": "block"}, False, True, False),
        ({"rtm11": "block"}, False, False, True),
    ],
)
def test_status_columns(
    columns: dict[str, str], battery: bool, supervision: bool, tamper: bool
) -> None:
    """Battery low is rtm8, supervision rtm5, tamper rtm11 (the page's guide)."""
    body = _form(_row(0, rtm7="9(3)", **columns))
    (status,) = zone_statuses(body)[0].values()
    assert (status.battery_low, status.supervision_fault, status.tamper) == (
        battery,
        supervision,
        tamper,
    )


def test_levels() -> None:
    """Both forms of the level, the repeater's and a missing lowest level."""
    body = _form(
        _row(0, rtm2="7&lt;br/&gt;(1)"),
        _row(1, rtm7="5(0)", rtm6="6(2) "),
        _row(2, rtm7="4(-)"),
    )
    statuses, unknown = zone_statuses(body)
    assert unknown == []
    assert statuses["201"].signal == Level(7, 1)
    assert statuses["202"].repeater_signal == Level(6, 2)
    assert statuses["203"].signal == Level(4, None)


def test_unused_slots_are_left_out() -> None:
    """A row without a signal strength is an empty slot."""
    statuses, _ = zone_statuses(_form(_row(0, rtm2=""), _row(1, rtm7="9(9)")))
    assert list(statuses) == ["202"]


def test_unknown_values_are_kept_unknown() -> None:
    """Values the client doesn't know stay unknown and are reported."""
    body = _form(_row(0, rtm7="strong", rtm6="??", rtm8="visible"))
    statuses, unknown = zone_statuses(body)
    status = statuses["201"]
    assert status.signal is None
    assert status.repeater_signal is None
    assert status.battery_low is None
    assert len(unknown) == 3
    assert all(item.startswith("zone 201 ") for item in unknown)


def test_iso_8859_1() -> None:
    """Non-UTF-8 answers, like the web interface's events, are read too."""
    body = _form(_row(0, rtm7="9(3)")).replace(b"<form>", b"<form><x>\xfc</x>")
    assert "201" in zone_statuses(body)[0]


@pytest.mark.parametrize("body", [b"", b"<html>401</html>", b"[]"])
def test_not_a_zone_list(body: bytes) -> None:
    """Anything but the zone list's data is a communication error."""
    with pytest.raises(CommunicationError):
        zone_statuses(body)


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (b'<input type="hidden" value="123456789" id="ssid" />', "123456789"),
        (b"<input id='ssid' type='hidden' value='123'>", "123"),
        (b'<input type="hidden" value="0" id="ssid" />', None),
        (b'<input type="hidden" value="" id="ssid" />', None),
        (b'<input type="hidden" id="ssid" />', None),
        (b'<input type="hidden" value="12a" id="ssid" />', None),
        (b'<input type="hidden" value="123456789" id="other" />', None),
        (b"", None),
    ],
)
def test_session_id(page: bytes, expected: str | None) -> None:
    """Only a numeric session id other than 0 is a successful login."""
    assert session_id(page) == expected


def test_web_package_does_not_import_home_assistant() -> None:
    """The web interface client stays free of Home Assistant imports."""
    package = Path(__file__).parent.parent.parent / "custom_components/secvest/webui"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(m.split(".")[0] == "homeassistant" for m in modules), (
                path.name
            )


def _info(*items: str) -> bytes:
    lis = "".join(f'<li class="information fullwidth">{item}</li>' for item in items)
    return f'<form name="about_panel"><ol class="controls">{lis}</ol></form>'.encode()


def test_panel_info() -> None:
    """Firmware and language from the first item, the serial from the second."""
    page = _info(
        "Version:&nbsp;<span>v3.01.31</span>&nbsp;"
        "Sprache:&nbsp;<span>Deutsch v1.47</span>",
        "Serien Nr.:&nbsp;<span>SECVEST0000TEST01</span> "
        "Part No.:<span>FUAA50XXX</span>",
        "Teilbereiche:&nbsp;1",
    )
    assert panel_info(page) == PanelInfo(
        "v3.01.31", "Deutsch v1.47", "SECVEST0000TEST01"
    )


def test_panel_info_ignores_labels() -> None:
    """Labels in another language don't matter, only structure and formats."""
    page = _info(
        "Version:<span>v4.0.1</span> Language:<span>English v2.0</span>",
        "Serial no.:<span>ABCD1234EFGH</span> Part no.:<span>X</span>",
    )
    assert panel_info(page) == PanelInfo("v4.0.1", "English v2.0", "ABCD1234EFGH")


@pytest.mark.parametrize(
    "page",
    [
        b"",
        _info("<span>3.01</span><span></span>", "<span>short</span>"),
        _info("no spans"),
    ],
)
def test_panel_info_unknown(page: bytes) -> None:
    """Values in an unexpected format stay unknown."""
    info = panel_info(page)
    assert info.firmware is None
    assert info.serial_number is None
