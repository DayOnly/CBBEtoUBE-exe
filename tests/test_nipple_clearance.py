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

"""`scripts/analysis/nipple_clearance.py` -- the MARGIN over the nipple.

This harness exists because binary exposure counters were CLEAN on the build
that drew "leather armor now has nipples poking through again but by the
smallest amount". The margin warns; the breach does not.

The load-bearing tests are the ones guarding the three ways a clearance harness
ships a believable wrong answer instead of an error:

  * `test_a_piece_that_does_not_cover_the_nipple_is_excluded` -- a boot or an
    open-chested corset has no cloth over the tip. Scoring it anyway would fold
    "the room over bare skin" into the pack median.
  * `test_degenerate_body_normals_abort_the_run` -- the BUG-00 fail-open shape.
    An all-zero normal array gives every ray no direction; taken raw it reads as
    a clean 0.000u rather than as an unusable body.
  * `test_the_tip_mask_is_the_one_the_converter_exempts` -- the harness must
    score the exact region `#authored-nipple-exempt` acts on, or a change that
    moved a neighbouring band would read here as a win.

`test_no_machine_specific_paths_or_mod_names` guards the promotion itself: the
scratch original hardcoded one developer's repo path.
"""
import re
from pathlib import Path

import numpy as np
import pytest

from scripts.analysis import nipple_clearance as ncl

_SRC = Path(ncl.__file__).read_text(encoding="utf-8")


class _FakeShape:
    """The little of a pynifly shape this harness touches."""

    def __init__(self, name, verts, tris=None, normals=None):
        self.name = name
        self.verts = np.asarray(verts, np.float64)
        self.tris = None if tris is None else np.asarray(tris, np.int64)
        self.normals = None if normals is None else np.asarray(normals,
                                                               np.float64)


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def _slab(y, n=6):
    """A little +y-facing quad grid at height y, covering x/z in [-2, 2]."""
    xs = np.linspace(-2.0, 2.0, n)
    zs = np.linspace(-2.0, 2.0, n)
    verts, tris = [], []
    for zi, z in enumerate(zs):
        for xi, x in enumerate(xs):
            verts.append((x, y, z))
    for zi in range(n - 1):
        for xi in range(n - 1):
            a = zi * n + xi
            tris.append((a, a + 1, a + n))
            tris.append((a + 1, a + n + 1, a + n))
    return np.asarray(verts, np.float64), np.asarray(tris, np.int64)


@pytest.fixture
def _one_shape_nif(monkeypatch):
    """Route `NifFile(path)` to a caller-supplied shape list."""
    box = {}

    def _install(shapes):
        box["shapes"] = shapes
        monkeypatch.setattr(ncl, "NifFile", lambda _p: _FakeNif(box["shapes"]))
    monkeypatch.setattr(ncl.nc, "shape_body_offset", lambda s: np.zeros(3))
    return _install


# ------------------------------------------------------------------ the ray

def test_the_clearance_is_the_distance_along_the_normal(_one_shape_nif):
    """A garment plane 1.5u out in +y from tip rays pointing +y measures 1.5u."""
    v, t = _slab(1.5)
    _one_shape_nif([_FakeShape("Cuirass", v, t)])
    origins = np.zeros((40, 3))
    directions = np.tile([0.0, 1.0, 0.0], (40, 1))
    got = ncl.clearance("ignored.nif", origins, directions)
    assert got is not None
    assert np.allclose(got, 1.5, atol=1e-6)


def test_a_piece_that_does_not_cover_the_nipple_is_excluded(_one_shape_nif):
    """Fewer than `MIN_TIP_HITS` rays land -> None, so the caller counts it as an
    exclusion instead of averaging in the room over bare skin."""
    v, t = _slab(1.5)
    _one_shape_nif([_FakeShape("Boots", v, t)])
    origins = np.zeros((ncl.MIN_TIP_HITS - 1, 3))
    directions = np.tile([0.0, 1.0, 0.0], (ncl.MIN_TIP_HITS - 1, 1))
    assert ncl.clearance("ignored.nif", origins, directions) is None


def test_a_hit_past_max_is_another_layer_not_this_garment(_one_shape_nif):
    """The far wall of the torso is not the cloth over this nipple."""
    v, t = _slab(ncl.MAX_HIT + 5.0)
    _one_shape_nif([_FakeShape("Cuirass", v, t)])
    origins = np.zeros((40, 3))
    directions = np.tile([0.0, 1.0, 0.0], (40, 1))
    assert ncl.clearance("ignored.nif", origins, directions) is None


# --------------------------------------------------------- what counts as cloth

def test_the_body_and_the_physics_proxies_are_not_garment(_one_shape_nif):
    """Casting at the injected body would report 0u clearance on every piece;
    casting at a collision proxy would report the proxy's own standoff."""
    v, t = _slab(1.5)
    _one_shape_nif([_FakeShape("BaseShape", v, t),
                    _FakeShape("VirtualBody", v, t),
                    _FakeShape("SkirtCol", v, t)])
    verts, tris = ncl.garment_mesh("ignored.nif")
    assert verts is None and tris is None


def test_every_rendering_shape_is_welded_into_one_mesh(_one_shape_nif):
    """The first hit has to be the first hit across the WHOLE garment: scoring
    shape by shape would report the inner layer's distance on a two-layer piece
    even when the outer layer is what the tip meets."""
    v, t = _slab(1.5)
    v2, t2 = _slab(0.5)
    _one_shape_nif([_FakeShape("Outer", v, t), _FakeShape("Inner", v2, t2)])
    verts, tris = ncl.garment_mesh("ignored.nif")
    assert len(verts) == len(v) + len(v2)
    assert tris.max() == len(verts) - 1, "the second shape's indices must be offset"
    origins = np.zeros((40, 3))
    directions = np.tile([0.0, 1.0, 0.0], (40, 1))
    got = ncl.clearance("ignored.nif", origins, directions)
    assert np.allclose(got, 0.5, atol=1e-6), "the NEAREST layer is the margin"


# ------------------------------------------------------------ fail-open guards

def test_degenerate_body_normals_abort_the_run(monkeypatch):
    """BUG-00's shape, inside the harness measuring a bust fix. An all-zero
    normal array leaves every ray without a direction; taken raw that reports a
    clean 0.000u instead of an unusable body."""
    v, _t = _slab(0.0, n=8)
    shape = _FakeShape("BaseShape", v)
    monkeypatch.setattr(ncl, "canonical_ube", lambda: ("body.nif", "BaseShape"))
    monkeypatch.setattr(ncl, "NifFile", lambda _p: _FakeNif([shape]))
    monkeypatch.setattr(ncl.nc, "_body_normals_or_compute",
                        lambda s: np.zeros((len(s.verts), 3)))
    monkeypatch.setattr(ncl, "_body_nipple_weight",
                        lambda s: np.ones(len(s.verts)))
    with pytest.raises(SystemExit):
        ncl.tip_rays()


def test_a_body_with_no_nipple_weight_aborts_rather_than_scoring_nothing(
        monkeypatch):
    """0/0 IS NOT A PASS: an empty mask would print a clean table over zero
    vertices."""
    v, _t = _slab(0.0, n=8)
    shape = _FakeShape("BaseShape", v)
    monkeypatch.setattr(ncl, "canonical_ube", lambda: ("body.nif", "BaseShape"))
    monkeypatch.setattr(ncl, "NifFile", lambda _p: _FakeNif([shape]))
    monkeypatch.setattr(ncl.nc, "_body_normals_or_compute",
                        lambda s: np.tile([0.0, 1.0, 0.0], (len(s.verts), 1)))
    monkeypatch.setattr(ncl, "_body_nipple_weight",
                        lambda s: np.zeros(len(s.verts)))
    with pytest.raises(SystemExit):
        ncl.tip_rays()


def test_the_tip_mask_selects_only_the_peak_of_the_weight(monkeypatch):
    """`>= 0.75 * max`, not "any weight" -- the ramp reaches well past the tip,
    and a mask that wide scores the breast rather than the feature at risk."""
    v, _t = _slab(0.0, n=8)
    shape = _FakeShape("BaseShape", v)
    w = np.linspace(0.0, 1.0, len(v))
    monkeypatch.setattr(ncl, "canonical_ube", lambda: ("body.nif", "BaseShape"))
    monkeypatch.setattr(ncl, "NifFile", lambda _p: _FakeNif([shape]))
    monkeypatch.setattr(ncl.nc, "_body_normals_or_compute",
                        lambda s: np.tile([0.0, 1.0, 0.0], (len(s.verts), 1)))
    monkeypatch.setattr(ncl, "_body_nipple_weight", lambda s: w)
    origins, directions = ncl.tip_rays()
    assert len(origins) == int((w >= 0.75).sum())
    assert len(origins) < len(v)
    assert np.allclose(np.linalg.norm(directions, axis=1), 1.0)


# ------------------------------------------------------------- the promotion

def test_the_tip_mask_is_the_one_the_converter_exempts():
    """Score the region the fix acts on. `#authored-nipple-exempt` exempts the
    tip selected by `_body_nipple_weight`; if this harness picked its own mask, a
    change that moved a neighbouring band would read here as a win."""
    assert "_body_nipple_weight" in _SRC
    assert "authored-nipple-exempt" in ncl.__doc__


def test_it_casts_a_ray_not_a_nearest_point_normal():
    """Nearest-point normals FLIP on curved geometry and fabricated a 226-vertex
    4.633u 'defect' on a glove the ray scores at 0.000%."""
    assert "ray_first_hit" in _SRC


def test_no_machine_specific_paths_or_mod_names():
    """Tracked content carries no absolute local path and no mod name. The
    scratch original hardcoded the repo root, which makes a harness useless on
    any other machine and publishes a local directory layout."""
    body = "\n".join(line for line in _SRC.splitlines()
                     if not line.lstrip().startswith("#"))
    assert not re.search(r"[A-Za-z]:\\\\|[A-Za-z]:/", body), (
        "an absolute Windows path is hardcoded")
    assert "Modlists" not in body


def test_the_body_comes_from_canonical_body():
    assert "canonical_ube" in _SRC
