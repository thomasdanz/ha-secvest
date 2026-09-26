#!/usr/bin/env python3
"""Copy the fixtures of the specification into tests/fixtures (ADR 0004).

    sync_fixtures.py [SPEC_REPO]

SPEC_REPO defaults to ../secvest-api. The fixtures must be committed there,
so that the commit written to tests/fixtures/SOURCE describes them exactly.
"""

from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "tests" / "fixtures"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    """Copy the fixtures and record the source commit."""
    spec = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "secvest-api")
    source = spec / "fixtures"
    if not source.is_dir():
        print(f"no fixtures directory in {spec}", file=sys.stderr)
        return 1
    if _git(spec, "status", "--porcelain", "--", "fixtures"):
        print("fixtures have uncommitted changes in the specification", file=sys.stderr)
        return 1
    commit = _git(spec, "rev-parse", "HEAD")

    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(source, TARGET)
    (TARGET / "SOURCE").write_text(f"{commit}\n")
    count = sum(
        1
        for path in TARGET.iterdir()
        if path.name != "SOURCE" and not path.name.startswith(".")
    )
    print(f"copied {count} fixtures from secvest-api {commit[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
