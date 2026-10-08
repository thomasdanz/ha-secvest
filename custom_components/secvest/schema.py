"""The schema library of Home Assistant's forms and actions.

Home Assistant 2026.10 replaced voluptuous with probatio, which mirrors its
API, and maps every voluptuous import to it at startup. Importing probatio
where it exists keeps the types right; older versions (the minimum is
2026.8.0) still use voluptuous. Goes once the minimum reaches 2026.10.
"""

try:
    import probatio as vol
except ImportError:  # Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef]

__all__ = ["vol"]
