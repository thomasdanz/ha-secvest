"""The recorder's `common` of the installed test helper package (see __init__)."""

try:
    from pytest_homeassistant.components.recorder.common import (
        async_wait_recording_done,
    )
except ImportError:  # Home Assistant before 2026.11
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

__all__ = ["async_wait_recording_done"]
