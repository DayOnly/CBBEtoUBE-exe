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

"""Canonical bodies, so a source-vs-converted comparison is actually fair.

WHY NOT THE BODY THE NIF BUNDLES. Three biases, all observed rather than imagined:
sources bundle DIFFERENT bodies (4,709 / 8,502 / 9,312 verts, different BodySlide
presets), some bundle NONE, and picking "the body" out of a NIF by heuristic gets it
wrong. Measured failures of the obvious rules:

  * z-span picks a full-length ROBE (span 106.8) over a real body (96.5);
  * "largest shape reaching the floor" picks a `Stabilizer` helper whose z of -47.5
    redefines the floor and excludes everything else;
  * bone signature is BACKWARDS -- the injected UBE body has head bones but no foot
    or hand bones (UBE ships hands/feet separately), while a robe has both;
  * "sits on the canonical body's surface" (80% of verts within 2.5u, spans head to
    feet) still calls a fitted thalmor robe a body, because it does.

So pair EVERY source garment with the canonical CBBE/3BA body and EVERY converted
garment with the canonical UBE body. Both sides then differ only in what we changed.

AND DO NOT DETECT THE BUNDLED BODY AT ALL. The converted output already names its
garment shapes; the source's garments are the shapes with the SAME names. That is
exact, needs no threshold, and guarantees both sides measure the same garment --
which is the entire premise of a delta. Verified on the cases that broke every
geometric rule above, including the robe.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402

from pyn import pynifly                                          # noqa: E402
from src.nif_convert import UBE_BODY_INJECT_NAMES                # noqa: E402

# Physics helpers are not visible garment. Counting them as coverage makes the body
# read as protected where nothing renders.
PHYSICS_NAMES = {"collision", "hidecollision", "proxy", "proxy2", "proxy3",
                 "stabilizer"}

_CACHE: dict = {}


def canonical_cbbe(mods_root=r"<MODLIST_ROOT>\mods"):
    """(path, shape_name) of the CBBE/3BA reference body -- the SOURCE-side body."""
    if "cbbe" not in _CACHE:
        cands = [c for c in glob.glob(
            str(Path(mods_root) / "**" / "character assets" / "femalebody_1.nif"),
            recursive=True) if "CBBEtoUBE" not in c and "!UBE" not in c]
        if not cands:
            raise FileNotFoundError("no CBBE femalebody_1.nif found")
        cands.sort(key=lambda p: (0 if ("3BA" in p or "3BBB" in p) else 1, len(p)))
        nif = pynifly.NifFile(cands[0])
        sh = max(nif.shapes, key=lambda s: len(s.verts))
        _CACHE["cbbe"] = (cands[0], sh.name)
    return _CACHE["cbbe"]


def canonical_ube():
    """(path, shape_name) of the UBE reference body -- the CONVERTED-side body."""
    if "ube" not in _CACHE:
        from src import auto_convert as ac
        p = str(ac._find_ube_body_ref())
        nif = pynifly.NifFile(p)
        sh = max(nif.shapes, key=lambda s: len(s.verts))
        _CACHE["ube"] = (p, sh.name)
    return _CACHE["ube"]


def converted_garment_names(conv_path):
    """Garment shape names in our output: everything that is not the injected body
    and not a physics helper."""
    nif = pynifly.NifFile(str(conv_path))
    return [s.name for s in nif.shapes
            if s.name not in UBE_BODY_INJECT_NAMES
            and s.name.strip().lower() not in PHYSICS_NAMES
            and len(s.verts)]


def find_source(rel, garment_names, mods_root):
    """The source mod whose shapes best match the converted GARMENT names.

    Not "the last mod providing this path": two mods can ship one path with different
    geometry, and picking the wrong one inverted a measured delta from +0.7 to +83.3
    -- from "the author did it" to "we did it".
    """
    want = set(garment_names)
    if not want:
        return None
    best, best_score = None, 0
    for d in sorted(Path(mods_root).iterdir()):
        if not d.is_dir() or "CBBEtoUBE" in d.name:
            continue
        p = d / "meshes" / rel
        if not p.is_file():
            continue
        try:
            have = {s.name for s in pynifly.NifFile(str(p)).shapes}
        except Exception:
            continue
        score = len(want & have)
        if score > best_score:
            best, best_score = p, score
    # Require EVERY garment to be present: a partial match means the two sides are
    # not the same outfit, and a delta over different geometry is meaningless.
    return best if best_score == len(want) else None
