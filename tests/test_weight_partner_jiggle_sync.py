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

"""`_sync_weight_partner_jiggle` -- #weight-partner-jiggle-sync.

THE DEFECT: `_transfer_body_jiggle_to_fitted` grafts belly/butt/breast weight
onto a garment that HUGS a jiggling body region, gated on a fit FRACTION judged
PER FILE. `_0` and `_1` are ONE garment at two body weights with different
geometry, so ~20 pack pieces straddle the gate and get the jiggle at one weight
only -- the whole population of `weight_partner_warnings`.

THE FAKE MODELS THE HAZARD, and that is the point of this file. `add_bone`
RESETS every skin-to-bone xform on the shape (the class that shipped 174 of 176
colliders at identity), so `FakeShape.add_bone` resets them too. A test written
against a fake that quietly preserved them would pass on a pass that ships every
collider at the origin.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
SPINE = "NPC Spine [Spn0]"
SPINE1 = "NPC Spine1 [Spn1]"
SPINE2 = "NPC Spine2 [Spn2]"
BELLY = "NPC Belly"
LBUTT = "NPC L Butt"
IDENTITY = "IDENTITY-RESET"
_MAX_INFLUENCES = 4


def test_the_fixture_bones_really_are_scale_bones():
    """If these stopped matching `_is_scale_bone`, every test below would pass
    while measuring a pass that never fires."""
    for b in (BELLY, LBUTT, "L Breast01"):
        assert nc._is_scale_bone(b), f"{b} is not a scale bone any more"
    for b in (PELV, SPINE, SPINE1):
        assert not nc._is_scale_bone(b)


class FakeShape:
    """Duck-typed pynifly shape with the REAL write and add_bone semantics."""

    def __init__(self, name, nverts, rows, unreadable=()):
        self.name = name
        self.verts = [(0.0, 0.0, float(i)) for i in range(nverts)]
        self._rows = [dict(r) for r in rows]
        self._extra = set()
        self.has_global_to_skin = False
        self.global_to_skin = None
        self._stb = {b: f"STB:{name}:{b}" for b in self.bone_names}
        self._unreadable = set(unreadable)
        self.added = []
        self.writes = []

    @property
    def bone_names(self):
        out = set(self._extra)
        for r in self._rows:
            out |= set(r)
        return sorted(out)

    @property
    def bone_weights(self):
        out = {b: [] for b in self._extra}
        for i, r in enumerate(self._rows):
            for b, w in r.items():
                out.setdefault(b, []).append((i, w))
        return out

    def get_shape_skin_to_bone(self, bn):
        if bn in self._unreadable:
            return None                      # returns None, does NOT raise
        return self._stb.get(bn)

    def set_skin_to_bone_xform(self, bn, st):
        self._stb[bn] = st

    def add_bone(self, bn):
        """THE HAZARD, modelled: this resets EVERY existing xform to identity."""
        self.added.append(bn)
        for b in list(self._stb):
            self._stb[b] = IDENTITY
        self._stb[bn] = IDENTITY
        self._extra.add(bn)

    def setShapeWeights(self, bn, pairs):
        self.writes.append((bn, list(pairs)))
        for i, w in pairs:
            i = int(i)
            if float(w) <= 1e-4:
                self._rows[i].pop(bn, None)
                continue
            self._rows[i][bn] = float(w)
            if len(self._rows[i]) > _MAX_INFLUENCES:
                drop = min(self._rows[i], key=lambda b: (self._rows[i][b], b))
                self._rows[i].pop(drop)

    def row(self, i):
        return dict(self._rows[i])


class FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def _run(shapes0, shapes1, **patches):
    saved = []

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (FakeNif(shapes0) if str(filepath) == "n0.nif"
                    else FakeNif(shapes1))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "atomic_nif_save", lambda nf, p: saved.append(str(p)))
        for k, v in patches.items():
            mp.setattr(nc, k, v)
        n = nc._sync_weight_partner_jiggle("n0.nif", "n1.nif")
    return n, saved


def _base_row():
    return {PELV: 0.30, SPINE: 0.40, SPINE1: 0.30}


def _pair(n=30, jiggle_on="1", bone=BELLY, weight=0.25, count=20):
    """`_1` has the bone on `count` verts; `_0` has none of it (or reversed)."""
    rows_with, rows_without = [], []
    for i in range(n):
        r = _base_row()
        if i < count:
            keep = {b: w * (1.0 - weight) for b, w in r.items()}
            keep[bone] = weight
            rows_with.append(keep)
        else:
            rows_with.append(dict(r))
        rows_without.append(dict(r))
    if jiggle_on == "1":
        return (FakeShape("Cuirass", n, rows_without),
                FakeShape("Cuirass", n, rows_with))
    return (FakeShape("Cuirass", n, rows_with),
            FakeShape("Cuirass", n, rows_without))


def test_the_deficient_weight_is_given_its_partners_bone():
    s0, s1 = _pair(jiggle_on="1")
    assert BELLY not in s0.bone_names, "fixture is inert: _0 already has it"
    n, saved = _run([s0], [s1])
    assert n > 0 and saved == ["n0.nif"], (
        f"expected only the deficient file to be written, got {saved}")
    assert BELLY in s0.bone_names
    assert s0.row(0)[BELLY] == pytest.approx(s1.row(0)[BELLY], abs=1e-3), (
        "the graft did not take the PARTNER's weight for that vertex")


def test_it_works_in_the_other_direction_too():
    """The direction flips between the heavy and light imperial cuirass, so
    neither file is privileged."""
    s0, s1 = _pair(jiggle_on="0")
    n, saved = _run([s0], [s1])
    assert n > 0 and saved == ["n1.nif"]
    assert BELLY in s1.bone_names


def test_existing_skin_to_bone_xforms_SURVIVE_add_bone():
    """THE HAZARD. The fake resets every xform on add_bone, exactly as pynifly
    does, so this fails loudly if the save/restore is dropped."""
    s0, s1 = _pair(jiggle_on="1")
    before = {b: s0.get_shape_skin_to_bone(b) for b in s0.bone_names}
    _n, _saved = _run([s0], [s1])
    assert s0.added == [BELLY], "the fixture never exercised add_bone"
    for b, st in before.items():
        assert s0.get_shape_skin_to_bone(b) == st, (
            f"{b}'s skin-to-bone xform was left at {s0.get_shape_skin_to_bone(b)!r} "
            f"-- add_bone reset it and it was never restored")


def test_the_new_bone_gets_the_PARTNERS_xform_not_identity():
    """Measured on the pack: a shape's scale-bone STB is identical in both
    variants (611 of 611), so the partner is the correct source. Leaving the
    new bone at identity would bind it to the origin."""
    s0, s1 = _pair(jiggle_on="1")
    _n, _saved = _run([s0], [s1])
    assert s0.get_shape_skin_to_bone(BELLY) == s1.get_shape_skin_to_bone(BELLY)
    assert s0.get_shape_skin_to_bone(BELLY) != IDENTITY


def test_a_shape_with_an_unreadable_xform_is_skipped_WHOLE():
    """An xform that cannot be read cannot be restored, so the shape would ship
    reset. Declining is right -- and it is what leaves 1 of the 20 unrepaired."""
    s0, s1 = _pair(jiggle_on="1")
    s0._unreadable = {SPINE1}
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved
    assert not s0.added, "add_bone ran on a shape whose xforms cannot be restored"
    assert BELLY not in s0.bone_names


def test_a_bone_that_would_land_with_no_weight_is_never_added():
    """An add_bone'd bone shipped with an EMPTY weight list desyncs the
    partition palette -> out-of-range read -> equip CTD."""
    s0, s1 = _pair(jiggle_on="1")
    for r in s1._rows:                     # partner's bone exists but is inert
        if BELLY in r:
            r[BELLY] = 1e-9
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved
    assert BELLY not in s0.bone_names


def test_a_grafted_bone_is_never_evicted_by_the_four_influence_cap():
    """Evicting it would leave the divergence exactly as it was found, and the
    pass would silently do nothing while reporting work."""
    # The graft weight must clear the peak gate, or the pass declines outright
    # and the test passes while exercising nothing.
    s0, s1 = _pair(jiggle_on="1")
    for r in s0._rows:                     # fill every row to the cap first
        r[SPINE2] = 0.10
        tot = sum(r.values())
        for b in list(r):
            r[b] /= tot
    # The fake builds its xform table at construction, so a bone injected into
    # the rows afterwards has none and reads as UNREADABLE -- which makes the
    # pass skip the shape and the test measure nothing.
    s0._stb.setdefault(SPINE2, f"STB:{s0.name}:{SPINE2}")
    assert len(s0.row(0)) == _MAX_INFLUENCES, "fixture is not at the cap"
    n, _saved = _run([s0], [s1])
    assert n > 0, "fixture is inert: the pass declined before the cap mattered"
    assert BELLY in s0.row(0), "the graft was evicted by the cap"
    assert len(s0.row(0)) <= _MAX_INFLUENCES


def test_rows_stay_normalised_and_within_four_influences():
    s0, s1 = _pair(jiggle_on="1")
    _n, _saved = _run([s0], [s1])
    for i in range(len(s0.verts)):
        r = s0.row(i)
        assert len(r) <= _MAX_INFLUENCES
        assert sum(r.values()) == pytest.approx(1.0, abs=1e-6)


def test_a_bone_both_weights_carry_is_left_alone():
    s0, s1 = _pair(jiggle_on="1")
    for i, r in enumerate(s0._rows):
        if BELLY in s1._rows[i]:
            keep = {b: w * 0.75 for b, w in r.items()}
            keep[BELLY] = 0.25
            s0._rows[i] = keep
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved and not s0.added


def test_a_bone_too_weak_to_move_the_mesh_is_left_alone():
    """Vert count alone was pure noise: a real conversion's divergences all
    peaked at <= 0.114, inert bones a graft brushed at 2%."""
    s0, s1 = _pair(jiggle_on="1", weight=0.02)
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved


def test_a_bone_on_too_few_verts_is_left_alone():
    s0, s1 = _pair(jiggle_on="1", count=3)
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved


def test_shapes_the_converter_rebuilt_are_skipped():
    """Different vert counts cannot be paired by index, and pairing a weight
    variant by proximity would be a guess."""
    s0, s1 = _pair(jiggle_on="1")
    s0.verts = s0.verts[:-4]
    s0._rows = s0._rows[:-4]
    n, saved = _run([s0], [s1])
    assert n == 0 and not saved


def test_kill_switch_is_a_complete_no_op():
    s0, s1 = _pair(jiggle_on="1")
    n, saved = _run([s0], [s1], WEIGHT_PARTNER_JIGGLE_SYNC=False)
    assert n == 0 and not saved and not s0.added


def test_a_shape_missing_from_the_partner_is_skipped():
    s0, s1 = _pair(jiggle_on="1")
    lone = FakeShape("OnlyInZero", 12, [_base_row() for _ in range(12)])
    n, _saved = _run([s0, lone], [s1])
    assert n > 0, "the good shape should still be repaired"
    assert not lone.added
