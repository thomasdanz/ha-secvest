# /// script
# requires-python = ">=3.14"
# dependencies = ["resvg-py==0.5.0"]
# ///
"""Render assets/icon.svg into the integration's brand images.

Run with `uv run scripts/render_icon.py`; uv installs the renderer for this
script only. Home Assistant falls back to icon.png for the logo and the dark
variants, so only the icon is rendered.
"""

from pathlib import Path

import resvg_py

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "assets" / "icon.svg"
TARGET = ROOT / "custom_components" / "secvest" / "brand"


def main() -> None:
    """Write icon.png (256 px) and icon@2x.png (512 px)."""
    svg = SOURCE.read_text()
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
        (TARGET / name).write_bytes(bytes(png))
        print(f"{name}: {size} px")


if __name__ == "__main__":
    main()
