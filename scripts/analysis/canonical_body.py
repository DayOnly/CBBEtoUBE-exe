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

WHICH CANONICAL BODY: BodySlide's ZEROED build, as the game loads it, at the
file's own weight -- `src/zeroed_body.py`, found by what the slider set builds
and verified vertex for vertex, never picked by mod name. It used to be "3BA in
the path, then the shortest path", which on a real modlist chose the 3BA mod
folder's own femalebody: a preset build the game never loads, up to 1.97u off
the zeroed body over 16,061 torso vertices. At weight 1 it grew THROUGH
garments built on the zeroed body, so the source side of source_delta_census
saw a median 83 of 300 breast vertices as covered, against 291 on the body the
game loads (measured 2026-09-21).

AND DO NOT DETECT THE BUNDLED BODY AT ALL. The converted output already names its
garment shapes; the source's garments are the shapes with the SAME names. That is
exact, needs no threshold, and guarantees both sides measure the same garment --
which is the entire premise of a delta. Verified on the cases that broke every
geometric rule above, including the robe.
"""
from __future__ import annotations

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

_WEIGHTS = ("_0", "_1")

_NO_SIBLING = (
    "no weight-0 body beside {p}: {sib} is missing. Refusing to fall back to "
    "weight 1 -- that would measure a `_0` garment in the wrong frame and "
    "print a believable number instead of an error.")


def weight_sibling(path_1, weight: str = "_1") -> Path:
    """The `weight` variant of a weight-1 body file, from the SAME directory.

    WHY A SIBLING, not a fresh lookup per weight. Run a body-picking rule again
    for weight 0 and a modlist with two body mods can hand back a weight-0 body
    from a DIFFERENT preset than the weight-1 one, so the two halves of one
    garment are measured against two unrelated bodies. Deriving `_0` from the
    chosen `_1` file's own directory keeps them a matched pair. (The canonical
    bodies below now come from src/zeroed_body.py, which enforces the same rule
    itself: a weight-0 body from a different folder than weight 1 is refused.)

    NEVER FALLS BACK. A missing sibling raises. `nif_convert.
    _weight_matched_ube_ref` DOES fall back to the weight-1 body, deliberately,
    because the converter must inject something; a measurement must not, so do
    not use that helper to pick a reference body for scoring.
    """
    if weight not in _WEIGHTS:
        raise ValueError(f"weight must be one of {_WEIGHTS}, not {weight!r}")
    p = Path(path_1)
    if weight == "_1":
        return p
    if not p.stem.endswith("_1"):
        raise ValueError(f"not a weight-1 body, so it has no weight-0 sibling "
                         f"to derive: {p.name}")
    sib = p.with_name(p.stem[:-len("_1")] + weight + p.suffix)
    if not sib.is_file():
        raise FileNotFoundError(_NO_SIBLING.format(sib=sib.name, p=p))
    return sib


def _zeroed(kind: str, weight: str, mods_root=None):
    """(path, shape_name) of BodySlide's zeroed `kind` body at `weight`, as the
    game loads it. Raises (a FileNotFoundError) rather than hand back another
    body -- see src/zeroed_body.py for how it is found and verified."""
    from src import zeroed_body as zb
    if mods_root is None:
        z = zb.zeroed_body(kind, weight)
    else:
        from src import paths as _p
        lay = _p.discover_layout()
        order = _p.enabled_mods_ordered(lay)
        if order is None:
            raise zb.ZeroedBodyError(
                "no MO2 profile found (set CBBE2UBE_MO2_INI): without load "
                "order it cannot be said which body the game loads")
        z = zb.zeroed_body(kind, weight, mods_root=Path(mods_root), order=order,
                           overwrite=_p.overwrite_dir(lay),
                           data_dirs=lay.game_data_dirs)
    return str(z.path), z.shape


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


def canonical_cbbe(mods_root=None, weight: str = "_1"):
    """(path, shape_name) of the CBBE/3BA reference body -- the SOURCE-side body:
    BodySlide's zeroed 3BA build as the game loads it, at `weight` ("_1"
    default, or "_0"). `mods_root` defaults to the converter's own discovery;
    load order always comes from the MO2 profile, because "which body does the
    game load" has no answer without it."""
    return _zeroed("cbbe", weight, mods_root)


def canonical_ube(weight: str = "_1"):
    """(path, shape_name) of the UBE reference body -- the CONVERTED-side body:
    BodySlide's zeroed UBE build as the game loads it, at `weight`."""
    return _zeroed("ube", weight)


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
