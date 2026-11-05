"""`common` of the installed test helper package (see __init__)."""

try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        MockUser,
        async_capture_events,
        async_fire_time_changed,
        flush_store,
        mock_component,
    )
except ImportError:  # Home Assistant before 2026.11
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        MockUser,
        async_capture_events,
        async_fire_time_changed,
        flush_store,
        mock_component,
    )

__all__ = [
    "MockConfigEntry",
    "MockUser",
    "async_capture_events",
    "async_fire_time_changed",
    "flush_store",
    "mock_component",
]
