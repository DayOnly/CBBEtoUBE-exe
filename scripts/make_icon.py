#!/usr/bin/env python3
# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Build `assets/CBBEtoUBE.ico` from a source logo PNG.

    python scripts/make_icon.py <logo.png> [-o assets/CBBEtoUBE.ico]

Kept as a script rather than a one-off because the .ico is a BUILD INPUT: the
spec points at it, so anyone rebuilding the exe needs to be able to regenerate
it from the artwork rather than inherit a binary nobody can reproduce.

Three things it does that a plain `Image.save(".ico")` does not:

  * CROPS TO THE ARTWORK'S OWN ALPHA BOUNDS. The source is a landscape canvas
    with a lot of empty space; saved as-is the emblem occupies a fraction of a
    square icon and reads as a dot in a taskbar.
  * SQUARES IT WITHOUT DISTORTING. Icons are square, the artwork is not, so the
    crop is padded (transparently) to a square rather than stretched.
  * WRITES EVERY SIZE WINDOWS ASKS FOR. Windows picks per context -- 16px in a
    title bar, 32 in a task bar, 256 in a large-icon view -- and an .ico
    missing a size gets a blurry scale of the nearest one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows uses each of these; leaving one out means a resampled, softer icon
# wherever the shell asks for it.
SIZES = (256, 128, 64, 48, 32, 16)
# Breathing room so the emblem does not touch the icon's edge, as a fraction of
# the squared side. Icons are usually drawn hard against neighbours.
MARGIN = 0.04


def build(src: Path, dst: Path) -> None:
    from PIL import Image

    im = Image.open(src).convert("RGBA")
    # The artwork's own alpha is the honest bound -- a luminance test would
    # also trim any legitimately dark edge of the art itself.
    bbox = im.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit(f"{src} is fully transparent -- nothing to make an icon from")
    art = im.crop(bbox)

    side = int(round(max(art.size) * (1.0 + 2 * MARGIN)))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Pillow builds every requested size from THIS image, so hand it the
    # largest square and let it downsample once per size.
    canvas.save(dst, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"source   : {src}  {im.size[0]}x{im.size[1]}")
    print(f"art bbox : {bbox}  -> {art.width}x{art.height}")
    print(f"squared  : {side}x{side}  (margin {MARGIN:.0%})")
    print(f"wrote    : {dst}  sizes {', '.join(str(s) for s in SIZES)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="logo PNG (transparent background)")
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).resolve().parent.parent
                    / "assets" / "CBBEtoUBE.ico")
    a = ap.parse_args(argv)
    if not a.source.is_file():
        raise SystemExit(f"source not found: {a.source}")
    build(a.source, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
