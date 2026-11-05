"""`plugins` of the installed test helper package (see __init__)."""

try:
    from pytest_homeassistant.plugins import (
        RecorderInstanceContextManager,
    )
except ImportError:  # Home Assistant before 2026.11
    from pytest_homeassistant_custom_component.plugins import (
        RecorderInstanceContextManager,
    )

__all__ = ["RecorderInstanceContextManager"]
