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

_REPO = Path(__file__).resolve().parent.parent.parent
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


def _resolve_mods_root():
    """The mods root, resolved the way the converter itself resolves it.

    Never hardcode a developer's own modlist path here: it makes the harness
    silently useless on every other machine, and it publishes a local directory
    layout to a public repository.
    """
    from src import paths as _p
    root = _p.mods_root()
    if root is None:
        raise FileNotFoundError(
            "could not locate a mods root -- set CBBE2UBE_MODS_ROOT, or "
            "CBBE2UBE_MO2_INI to point at a ModOrganizer.ini")
    return root


def canonical_cbbe(mods_root=None):
    """(path, shape_name) of the CBBE/3BA reference body -- the SOURCE-side body.

    `mods_root` defaults to the SAME discovery the converter uses
    (`paths.mods_root()`: the CBBE2UBE_MODS_ROOT env var, else a fresh MO2
    layout discovery), so this works on any machine.
    """
    if mods_root is None:
        mods_root = _resolve_mods_root()
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
    if best_score == len(want):
        return best
    return _find_source_in_bsa(rel, want)


_BSA_INDEX = ["unset"]          # list-as-cell so a failed build is cached too


def _bsa_index():
    """The pipeline's own BSA resolver, built exactly as auto_convert builds it.

    A loose-file scan misses every BSA-packed source, and those are not a fringe case:
    the three LARGEST real pose defects in the pack all reported "no source" purely
    because their meshes live in archives. A harness that cannot see what the converter
    reads silently drops the worst cases and reports the pack as cleaner than it is.
    """
    if _BSA_INDEX[0] != "unset":
        return _BSA_INDEX[0]
    _BSA_INDEX[0] = None
    try:
        import tempfile
        from src import paths as _p
        from src.auto_convert import _BsaMeshIndex
        lay = _p.discover_layout()
        order = _p.enabled_mods_ordered(lay)
        root = _p.mods_root()
        if root is None or not order:
            return None
        # Mod BSAs first in MO2 priority, game Data dirs LAST -- the same precedence
        # the converter uses, so the harness resolves to the file the pipeline read.
        dirs = [Path(root) / n for n in order]
        dirs += [Path(d) for d in (lay.game_data_dirs or []) if Path(d) not in dirs]
        staging = Path(tempfile.gettempdir()) / "cbbe2ube_harness_bsa"
        staging.mkdir(parents=True, exist_ok=True)
        _BSA_INDEX[0] = _BsaMeshIndex(dirs, staging)
    except Exception:
        _BSA_INDEX[0] = None
    return _BSA_INDEX[0]


def _find_source_in_bsa(rel, want):
    """Resolve `rel` out of the BSAs, keeping the same all-shapes-present rule."""
    idx = _bsa_index()
    if idx is None:
        return None
    # Index keys are relative to meshes/ ("armor/aom/hag/hagrobe_1.nif"), NOT prefixed
    # with it. Prefixing silently misses every archive entry while the index still
    # reports tens of thousands of paths -- a lookup that looks healthy and finds nothing.
    key = str(rel).replace("\\", "/").lower().lstrip("/")
    if key.startswith("meshes/"):
        key = key[len("meshes/"):]
    try:
        if not idx.contains(key):
            return None
        got = idx.extract(key)
    except Exception:
        return None
    if not got:
        return None
    p = Path(got[0] if isinstance(got, (tuple, list)) else got)
    if not p.is_file():
        return None
    try:
        have = {s.name for s in pynifly.NifFile(str(p)).shapes}
    except Exception:
        return None
    return p if want <= have else None
