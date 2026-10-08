"""The schema library of Home Assistant's forms and actions.

Home Assistant 2026.10 replaced voluptuous with probatio, which mirrors its
API, and maps every voluptuous import to it at startup; older versions (the
minimum is 2026.8.0) use voluptuous itself. So at runtime voluptuous is
right for every version, while the types are probatio's. Probatio being
installed doesn't tell the version: it may come with other packages. Goes
once the minimum reaches 2026.10.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

__all__ = ["vol"]
