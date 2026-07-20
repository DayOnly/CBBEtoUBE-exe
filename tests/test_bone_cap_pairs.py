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

"""Guards for MIRRORED-PAIR handling in the skin bone-cap trim.

_cap_skin_bone_count keeps the `limit` most locally-dominant bones and drops the
rest. Ranked individually, the cutoff can fall BETWEEN `NPC L FrontThigh` and
`NPC R FrontThigh` -- keeping one, dropping the other -- so one thigh follows the
body's morph/flex and the other stays rigid. Measured on a real pack: 24 of 224
shapes carrying leg detail bones were asymmetric this way, because thinly
propagated scale bones sit right at the cutoff and their L/R importance differs
by a hair.

Staying under the cap still wins: a pair that would straddle the limit is
dropped whole, so the result may come in UNDER `limit`. That is intended -- a
lone half-pair is worse than one fewer bone.
"""
from src.nif_convert import _cap_skin_bone_count as cap


def _filler(n):
    return [f"Bone{i:02d}" for i in range(n)]


def test_mirrored_pair_is_kept_together_when_it_fits():
    names = _filler(76) + ["NPC L FrontThigh", "NPC R FrontThigh"]
    w = {b: [(0, 0.9)] for b in names[:76]}
    w["NPC L FrontThigh"] = [(1, 0.021)]
    w["NPC R FrontThigh"] = [(1, 0.020)]      # a hair weaker
    kept, _x, _w = cap(names, {}, w, limit=78)
    assert ("NPC L FrontThigh" in kept) == ("NPC R FrontThigh" in kept)
    assert "NPC R FrontThigh" in kept, "the weaker half must not be dropped alone"


def test_pair_that_straddles_the_cap_is_dropped_whole():
    names = _filler(76) + ["NPC L FrontThigh", "NPC R FrontThigh"]
    w = {b: [(0, 0.9)] for b in names[:76]}
    w["NPC L FrontThigh"] = [(1, 0.021)]
    w["NPC R FrontThigh"] = [(1, 0.020)]
    kept, _x, _w = cap(names, {}, w, limit=77)
    assert "NPC L FrontThigh" not in kept and "NPC R FrontThigh" not in kept
    assert len(kept) <= 77


def test_bracketed_side_tags_are_mirrored():
    """`NPC L Thigh [LThg]` pairs with `NPC R Thigh [RThg]` -- the side letter
    inside the bracket must be swapped too, or the pair is never recognised."""
    names = _filler(76) + ["NPC L RearCalf [LrClf]", "NPC R RearCalf [RrClf]"]
    w = {b: [(0, 0.9)] for b in names[:76]}
    w["NPC L RearCalf [LrClf]"] = [(1, 0.05)]
    w["NPC R RearCalf [RrClf]"] = [(1, 0.01)]
    kept, _x, _w = cap(names, {}, w, limit=78)
    assert ("NPC L RearCalf [LrClf]" in kept) == ("NPC R RearCalf [RrClf]" in kept)


def test_never_exceeds_the_limit():
    names = _filler(60) + [b for i in range(10)
                           for b in (f"NPC L X{i}", f"NPC R X{i}")]
    w = {b: [(0, 0.5)] for b in names}
    for lim in (60, 70, 75, 78):
        kept, _x, _w = cap(names, {}, w, limit=lim)
        assert len(kept) <= lim, f"limit {lim} exceeded: {len(kept)}"


def test_unpaired_bones_and_under_limit_are_unchanged():
    names = _filler(10) + ["NPC Spine [Spn0]"]
    w = {b: [(0, 0.5)] for b in names}
    kept, _x, _w = cap(names, {}, w, limit=78)
    assert list(kept) == names, "under the limit nothing may be dropped"


def test_weights_are_renormalised_after_dropping():
    names = _filler(78) + ["NPC L Weak", "NPC R Weak"]
    w = {b: [(0, 0.4)] for b in names[:78]}
    w["NPC L Weak"] = [(0, 0.01)]
    w["NPC R Weak"] = [(0, 0.01)]
    kept, _x, nw = cap(names, {}, w, limit=78)
    total = sum(v[0][1] for v in nw.values() if v and v[0][0] == 0)
    assert abs(total - 1.0) < 1e-6, f"vertex 0 weights must sum to 1, got {total}"
