#!/usr/bin/env python3
"""Print the release notes of a version from the changelog (#48).

    release_notes.py vX.Y.Z

Fails if the tag doesn't match the version in the manifest and in
pyproject.toml, or if the changelog has no section for it. Used by the
release workflow when a tag is pushed.
"""

import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def section(changelog: str, version: str) -> str | None:
    """Return the body of a version's changelog section, without its heading."""
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)",
        changelog,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match[1].strip() if match else None


def main() -> int:
    """Check the versions and print the notes."""
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+", sys.argv[1]):
        print("usage: release_notes.py vX.Y.Z", file=sys.stderr)
        return 2
    version = sys.argv[1].removeprefix("v")
    manifest = json.loads(
        (ROOT / "custom_components/secvest/manifest.json").read_text()
    )["version"]
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    if version != manifest or version != pyproject:
        print(
            f"tag {sys.argv[1]} doesn't match the manifest ({manifest}) "
            f"and pyproject.toml ({pyproject})",
            file=sys.stderr,
        )
        return 1
    notes = section((ROOT / "CHANGELOG.md").read_text(), version)
    if not notes:
        print(f"no changelog section for {version}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
