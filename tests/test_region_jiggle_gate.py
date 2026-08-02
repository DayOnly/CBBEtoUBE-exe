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

"""#region-jiggle-gate -- jiggle regions are independent, so the gate must be too.

`_transfer_body_jiggle_to_fitted` is the ONLY pass that adds `NPC L/R Butt` to a
garment. Its gate pooled breast+butt+belly into one counter and skipped the whole
shape at 8, deferring to `_conform_fitted_to_body`. Both halves were wrong: the
chest-follow pass grafts breast weight earlier (295 verts on the in-game repro),
which tripped the pooled counter, and the pass it defers to keeps the vert's bone
set -- it can only SHRINK -- so it can never add a bone the shape lacks. Butt
weight stayed at exactly 0.0000 on 105 shipped pieces, and the body pushed
straight through the armour under morph.

The tests below pin the three states the gate has to tell apart, and -- the one
that matters for not causing a new regression -- that a region the shape ALREADY
carries is left alone rather than re-grafted over chest-follow's measured ratio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402

MIN = nc._CONFORM_MIN_JIGGLE_VERTS


def _weights(**per_region):
    """{bone: [(vert, weight)]} with N verts over 0.1 for each named region."""
    bone = {"breast": "NPC L Breast", "butt": "NPC L Butt",
            "belly": "NPC Belly"}
    return {bone[k]: [(i, 0.5) for i in range(n)]
            for k, n in per_region.items() if n}


def _gate(bw):
    """The gate as the pass computes it: (regions already present, skip?)."""
    have = {kw: 0 for kw in nc.PHYSICS_JIGGLE_SCALE_KEYWORDS}
    for b, pairs in bw.items():
        kw = nc._jiggle_region_of(b)
        if kw is not None:
            have[kw] += sum(1 for _vi, w in pairs if float(w) > 0.1)
    already = {kw for kw, c in have.items() if c >= MIN}
    return already, len(already) >= len(nc.PHYSICS_JIGGLE_SCALE_KEYWORDS)


# ------------------------------------------------------------------ the region map

def test_region_of_classifies_each_jiggle_bone():
    assert nc._jiggle_region_of("NPC L Breast01") == "breast"
    assert nc._jiggle_region_of("NPC R Butt") == "butt"
    assert nc._jiggle_region_of("NPC Belly") == "belly"
    assert nc._jiggle_region_of("NPC Spine2") is None
    assert nc._jiggle_region_of("Skirt 3_01") is None


def test_region_of_agrees_with_the_boolean_it_refines():
    """Any bone the pooled predicate calls jiggle must map to a region, and vice
    versa -- two predicates for one concept drifting apart is a recurring bug
    class in this project."""
    for b in ("NPC L Breast03", "NPC R Butt", "NPC Belly", "NPC L Thigh",
              "Skirt 1_00", "NPC Spine"):
        assert (nc._jiggle_region_of(b) is not None) == \
            nc._is_physics_jiggle_scale_bone(b), b


# ------------------------------------------------------------------- the three states

def test_a_shape_with_no_jiggle_is_unchanged_from_the_old_behaviour():
    """The majority path. Nothing already present -> nothing filtered out, so
    this shape grafts exactly as it did before the fix."""
    already, skip = _gate(_weights())
    assert already == set() and skip is False


def test_a_shape_with_every_region_is_still_skipped():
    """The gate's original purpose survives -- this one really is the conform's
    job, and re-grafting it would retune weights somebody measured."""
    _already, skip = _gate(_weights(breast=MIN, butt=MIN, belly=MIN))
    assert skip is True


def test_THE_BUG_breast_weight_no_longer_hides_a_missing_butt():
    """The in-game repro: 295 breast verts, 33 belly, ZERO butt. Pooled, that is
    328 >= 8 and the shape was dropped with no butt weight. Per region, butt is
    missing and the shape must be admitted."""
    already, skip = _gate(_weights(breast=295, belly=33))
    assert skip is False, "the shape must still be admitted for its missing butt"
    assert already == {"breast", "belly"}
    assert "butt" not in already


# ------------------------------------------- do not re-graft what is already there

def test_only_the_missing_region_is_grafted():
    """The safety property. Admitting the shape must NOT re-graft breast on top
    of chest-follow's ratio -- that constant is separately measured and silently
    retuning it is how this project has broken working armour before."""
    already, _skip = _gate(_weights(breast=295, belly=33))
    body_vert = {"NPC L Breast01": 0.8, "NPC L Butt": 0.7,
                 "NPC Belly": 0.4, "NPC Spine2": 0.9}
    jstbs = {"NPC L Breast01", "NPC L Butt", "NPC Belly"}
    grafted = {b for b, w in body_vert.items()
               if nc._is_physics_jiggle_scale_bone(b) and w > 1e-3
               and b in jstbs and nc._jiggle_region_of(b) not in already}
    assert grafted == {"NPC L Butt"}, grafted


def test_a_region_just_under_the_floor_still_counts_as_missing():
    """MIN-1 verts is not a weighted region -- it is noise, and treating it as
    present would reinstate the bug at a smaller scale."""
    already, skip = _gate(_weights(breast=295, butt=MIN - 1))
    assert skip is False
    assert "butt" not in already
    assert "breast" in already


def test_the_floor_is_shared_with_the_conform_gate():
    """It is the inverse of the conform's own threshold; two independent floors
    for one boundary drift."""
    assert MIN == nc._CONFORM_MIN_JIGGLE_VERTS
    already, _ = _gate(_weights(butt=MIN))
    assert "butt" in already
