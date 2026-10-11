"""What the web interface reports per wireless zone."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Level:
    """A signal strength level from 0 to 9, as the panel shows it.

    current is the level of the last received message; lowest the lowest
    since the readings were last reset (installer manual), unknown if the
    panel shows none.
    """

    current: int
    lowest: int | None


@dataclass(frozen=True, slots=True)
class WebZoneStatus:
    """One wireless zone's row of the web interface's zone list.

    None means the panel's value is unknown or couldn't be read.
    """

    zone_id: str
    # received by the panel directly, and through the RF repeater (None if no
    # repeater repeats the zone)
    signal: Level | None
    repeater_signal: Level | None
    battery_low: bool | None
    supervision_fault: bool | None
    tamper: bool | None
