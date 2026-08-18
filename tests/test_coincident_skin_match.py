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

"""Cross-shape coincident-vertex skin unification (#coincident-skin-match).

Reported in game as a belt and its buckle "pulling in different directions":
verts that TOUCH but belong to DIFFERENT shapes came out skinned differently,
because every weight pass pairs each garment vert to the body PER SHAPE and two
coincident verts have different normals, so they hit different body triangles.

The pass reads and writes a NIF, so these drive it through a duck-typed pynifly
whose `setShapeWeights` reproduces the two semantics the real one has and that
this pass is required to survive:

  * it MERGES -- a vertex simply omitted keeps its old value, so a bone the new
    row gives up has to be written explicitly at 0.0
    ([[project_setshapeweights_update_semantics]]); and
  * the native skin buffer holds FOUR influences per vertex and resolves an
    overflow itself by dropping the smallest.

Together those are what made the first version of this pass ship rows summing to
0.9621 -- a newcomer arrived while the bones it replaced still held their old
values, lost the four-way contest, and was gone before they were lowered.
`test_removals_are_written_before_additions` is the regression guard.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
SPINE = "NPC Spine [Spn0]"
SPINE1 = "NPC Spine1 [Spn1]"
BELLY = "NPC Belly"
LTHIGH = "NPC L Thigh [LThg]"
_MAX_INFLUENCES = 4


class FakeShape:
    """Duck-typed pynifly shape with the real write semantics."""

    def __init__(self, name, verts, rows):
        self.name = name
        self.verts = [tuple(map(float, v)) for v in verts]
        self._rows = [dict(r) for r in rows]      # per-vert {bone: weight}
        self.has_global_to_skin = False
        self.global_to_skin = None
        self._stb = {b: object() for b in self.bone_names}
        self.stb_writes = 0
        self.writes = []

    @property
    def bone_names(self):
        out = []
        for r in self._rows:
            for b in r:
                if b not in out:
                    out.append(b)
        return sorted(out)

    @property
    def bone_weights(self):
        out = {}
        for i, r in enumerate(self._rows):
            for b, w in r.items():
                out.setdefault(b, []).append((i, w))
        return out

    def get_shape_skin_to_bone(self, bn):
        return self._stb.get(bn)

    def set_skin_to_bone_xform(self, bn, st):
        self.stb_writes += 1
        self._stb[bn] = st

    def setShapeWeights(self, bn, pairs):
        self.writes.append((bn, list(pairs)))
        for i, w in pairs:
            i = int(i)
            if float(w) <= 1e-4:
                self._rows[i].pop(bn, None)       # explicit 0.0 REMOVES
                continue
            self._rows[i][bn] = float(w)
            if len(self._rows[i]) > _MAX_INFLUENCES:
                # The buffer holds four; the smallest loses. This is the whole
                # reason removals have to be written first.
                drop = min(self._rows[i], key=lambda b: (self._rows[i][b], b))
                self._rows[i].pop(drop)

    def row(self, i):
        return dict(self._rows[i])


# The pass skips degenerate shapes, so every fake carries filler verts. They are
# pushed far away AND to a position unique to the shape -- filler at a shared
# position would cluster with every other shape's filler and quietly become part
# of what the test measures.
_FILLER_SEEN: dict = {}


def _shape(name, verts, rows, filler=None):
    """`filler` is one row, or a list of rows -- one per filler vert, for when a
    shape's palette needs more bones than the four a single vertex can hold."""
    fr = filler or {PELV: 1.0}
    fr = [dict(r) for r in fr] if isinstance(fr, list) else [dict(fr)]
    while len(fr) < 2:
        fr.append(dict(fr[0]))
    for r in fr:
        assert len(r) <= _MAX_INFLUENCES, (
            f"filler row {r} has {len(r)} influences -- a vertex holds four, so "
            f"this cannot occur in a real NIF and the fixture would be testing "
            f"the fake's overflow rule instead of the pass")
    slot = _FILLER_SEEN.setdefault(name, 100.0 * (len(_FILLER_SEEN) + 1))
    pad = [(slot + 10.0 * k, slot, slot) for k in range(len(fr))]
    return FakeShape(name, list(verts) + pad,
                     [dict(r) for r in rows] + fr)


def _assert_no_bone_emptied(sh):
    """Every bone the pass WROTE must still hold weight somewhere. A bone left
    in the shape with an empty weight list is absent from the regenerated skin
    partition palette -> equip CTD. #zeroweight-bone-desync"""
    live = sh.bone_weights
    for bn, _prs in sh.writes:
        assert live.get(bn), f"{bn} was written and shipped with no weight"


class FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def _install(monkeypatch, dst_shapes, src_shapes, saved):
    """Point the pass at in-memory NIFs and capture the save."""
    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (FakeNif(dst_shapes) if str(filepath) == "dst.nif"
                    else FakeNif(src_shapes))

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn)
    monkeypatch.setattr(nc, "_nif_has_fx_shape", lambda nf: False)
    monkeypatch.setattr(nc, "_hdt_collider_shape_names",
                        lambda p, nif=None: set())
    monkeypatch.setattr(nc, "_hdt_softbody_shape_names",
                        lambda p, nif=None: set())
    monkeypatch.setattr(nc, "_hide_virtual_body", lambda nf: False)
    monkeypatch.setattr(nc, "atomic_nif_save",
                        lambda nf, p: saved.append(p))


def _run(dst_shapes, src_shapes):
    """Run the pass with the fakes installed, and UNDO them on the way out.

    The context manager is not optional. A bare `pytest.MonkeyPatch()` applies
    its patches and never reverts them, so `nc._pynifly` stayed replaced by the
    fake for the rest of the session -- which broke 15 tests in another file
    that had nothing to do with this one."""
    saved = []
    with pytest.MonkeyPatch.context() as mp:
        _install(mp, dst_shapes, src_shapes, saved)
        n = nc._match_coincident_cross_shape_skin("dst.nif",
                                                  src_nif_path="src.nif")
    return n, saved


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------

def test_touching_verts_of_different_shapes_end_up_identically_skinned():
    # Two verts at the SAME position in different shapes. The author skinned
    # them alike; the conversion split them between the PELVIS and the SPINE,
    # which is the reported "pulling in different directions". These are the
    # real numbers off vertex 3046 of the reported outfit.
    pos = [(0.0, 0.0, 77.0)]
    # `NPC Belly` is carried by the rest of the garment too, as it is on the
    # real shape -- a bone held by ONE vertex is a different case, pinned by
    # test_a_bone_whose_last_vertex_would_be_taken_is_left_in_place.
    dst = [_shape("Buttons", pos, [{PELV: 0.126, SPINE: 0.551,
                                    SPINE1: 0.278, BELLY: 0.045}],
                  filler={PELV: 0.5, SPINE: 0.3, BELLY: 0.2}),
           _shape("Rope", pos, [{PELV: 0.938, SPINE: 0.055, SPINE1: 0.007}])]
    src = [_shape("Buttons", pos, [{PELV: 0.562, SPINE: 0.220,
                                    SPINE1: 0.186, LTHIGH: 0.032}]),
           _shape("Rope", pos, [{PELV: 0.562, SPINE: 0.220,
                                 SPINE1: 0.186, LTHIGH: 0.032}])]
    n, saved = _run(dst, src)
    assert n == 2 and saved == ["dst.nif"]
    a, b = dst[0].row(0), dst[1].row(0)
    assert a == b, "coincident verts must carry IDENTICAL weights"
    assert abs(sum(a.values()) - 1.0) < 1e-6
    assert BELLY not in a, "a bone outside the shared palette must be given up"
    for sh in dst:
        _assert_no_bone_emptied(sh)


def test_a_bone_whose_last_vertex_would_be_taken_is_left_in_place():
    # `NPC Belly` sits on ONE vertex of Buttons, and that vertex's unified row
    # does not include it. Removing it would leave the bone in the shape with an
    # empty weight list -- absent from the regenerated partition palette, which
    # is an equip CTD. So the bone is left entirely alone and the row keeps a
    # stale share: a documented trade, and the cheaper failure of the two.
    # #zeroweight-bone-desync
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("Buttons", pos, [{PELV: 0.126, SPINE: 0.551,
                                    SPINE1: 0.278, BELLY: 0.045}]),
           _shape("Rope", pos, [{PELV: 0.938, SPINE: 0.055, SPINE1: 0.007}])]
    src = [_shape("Buttons", pos, [{PELV: 0.562, SPINE: 0.220, SPINE1: 0.186}]),
           _shape("Rope", pos, [{PELV: 0.562, SPINE: 0.220, SPINE1: 0.186}])]
    _run(dst, src)
    assert dst[0].row(0).get(BELLY) == 0.045
    for sh in dst:
        _assert_no_bone_emptied(sh)


def test_a_bone_only_one_shape_has_is_never_grafted_onto_the_other():
    # `NPC Belly` exists on Buttons and not on Rope. Unifying must not add it:
    # `add_bone` resets every STB ([[project_identity_stb_collider]]).
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("Buttons", pos, [{PELV: 0.5, SPINE: 0.3, BELLY: 0.2}],
                  filler={PELV: 0.5, SPINE: 0.3, BELLY: 0.2}),
           _shape("Rope", pos, [{PELV: 0.9, SPINE: 0.1}])]
    src = [_shape("Buttons", pos, [{PELV: 0.7, SPINE: 0.3}]),
           _shape("Rope", pos, [{PELV: 0.7, SPINE: 0.3}])]
    _run(dst, src)
    assert BELLY not in dst[1].row(0)
    assert dst[0].row(0) == dst[1].row(0)


def test_cluster_the_shared_palette_cannot_carry_is_left_alone():
    # `Rope` keeps 40% of its weight on a bone `Buttons` has no slot for.
    # Unifying would mean either grafting that bone (add_bone, STB reset) or
    # throwing the weight away, so the honest answer is to do nothing.
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("Buttons", pos, [{PELV: 0.6, SPINE: 0.4}]),
           _shape("Rope", pos, [{PELV: 0.4, SPINE: 0.2, LTHIGH: 0.4}],
                  filler={PELV: 0.5, SPINE: 0.2, LTHIGH: 0.3})]
    src = [_shape("Buttons", pos, [{PELV: 0.5, SPINE: 0.5}]),
           _shape("Rope", pos, [{PELV: 0.5, SPINE: 0.5}])]
    n, saved = _run(dst, src)
    assert n == 0 and saved == []
    assert dst[1].row(0) == {PELV: 0.4, SPINE: 0.2, LTHIGH: 0.4}


# --------------------------------------------------------------------------
# The author gate -- what keeps this a restoration and not an invention
# --------------------------------------------------------------------------

def test_an_authored_skin_boundary_is_left_alone():
    # The author deliberately skinned these two touching verts differently
    # (L1 1.0, far over the gate). A seam between differently-weighted parts is
    # the author's decision, not our defect.
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.9, SPINE: 0.1}]),
           _shape("B", pos, [{PELV: 0.2, SPINE: 0.8}])]
    src = [_shape("A", pos, [{PELV: 1.0}]),
           _shape("B", pos, [{SPINE: 1.0}], filler={SPINE: 1.0})]
    n, saved = _run(dst, src)
    assert n == 0 and saved == []
    assert dst[0].row(0) == {PELV: 0.9, SPINE: 0.1}
    assert dst[1].row(0) == {PELV: 0.2, SPINE: 0.8}


def test_shape_with_no_source_counterpart_is_excluded():
    # No authored answer -> no gate. Unifying on faith is how a safety rail
    # becomes decoration, so such a shape takes no part at all.
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.9, SPINE: 0.1}]),
           _shape("Minted", pos, [{PELV: 0.2, SPINE: 0.8}])]
    src = [_shape("A", pos, [{PELV: 1.0}])]
    n, saved = _run(dst, src)
    assert n == 0 and saved == []


def test_verts_further_apart_than_the_tolerance_are_not_joined():
    far = nc._COINCIDENT_SKIN_TOL * 4
    dst = [_shape("A", [(0.0, 0.0, 77.0)], [{PELV: 0.9, SPINE: 0.1}]),
           _shape("B", [(0.0, 0.0, 77.0 + far)], [{PELV: 0.2, SPINE: 0.8}])]
    src = [_shape("A", [(0.0, 0.0, 77.0)], [{PELV: 0.5, SPINE: 0.5}]),
           _shape("B", [(0.0, 0.0, 77.0 + far)], [{PELV: 0.5, SPINE: 0.5}])]
    n, _ = _run(dst, src)
    assert n == 0


def test_gate_is_per_edge_so_one_boundary_does_not_veto_a_whole_chain():
    # A--B agree in the source; B--C do not. Joining A and B must still happen.
    # Gating per CLUSTER instead left 40 verts shearing where per-EDGE left 5.
    p = 0.05
    both = {PELV: 0.5, SPINE: 0.5}       # keeps PELV+SPINE in every palette
    dst = [_shape("A", [(0.0, 0.0, 0.0)], [{PELV: 1.0}], filler=both),
           _shape("B", [(0.0, 0.0, p)], [{PELV: 0.6, SPINE: 0.4}],
                  filler=both),
           _shape("C", [(0.0, 0.0, 2 * p)], [{SPINE: 1.0}], filler=both)]
    src = [_shape("A", [(0.0, 0.0, 0.0)], [{PELV: 0.8, SPINE: 0.2}],
                  filler=both),
           _shape("B", [(0.0, 0.0, p)], [{PELV: 0.8, SPINE: 0.2}],
                  filler=both),
           _shape("C", [(0.0, 0.0, 2 * p)], [{SPINE: 1.0}], filler=both)]
    n, _ = _run(dst, src)
    assert n == 2, "A and B must still be unified"
    assert dst[0].row(0) == dst[1].row(0)
    assert dst[2].row(0) == {SPINE: 1.0}, "C keeps its authored boundary"


# --------------------------------------------------------------------------
# Write semantics
# --------------------------------------------------------------------------

def test_removals_are_written_before_additions():
    # REGRESSION GUARD. Both verts already hold FOUR influences, and the unified
    # row swaps two of them out for a bone neither vert currently carries. If
    # the newcomer is written while the bones it replaces still hold their old,
    # larger values it loses the four-way contest and vanishes -- which shipped
    # as a row summing 0.9621 with its `NPC L Thigh` share simply missing.
    pos = [(0.0, 0.0, 77.0)]
    # Both shapes carry the full six-bone palette, spread over two filler verts
    # because ONE vertex only holds four. So the shared basis is not what limits
    # this test -- the four SLOTS on the clustered vertex are.
    full = [{PELV: 0.4, SPINE: 0.3, "NPC R Butt": 0.2,
             "NPC R Thigh [RThg]": 0.1},
            {PELV: 0.4, SPINE: 0.3, "NPC L Butt": 0.2, LTHIGH: 0.1}]
    dst = [_shape("A", pos, [{PELV: 0.75, "NPC R Butt": 0.12,
                              SPINE: 0.08, "NPC R Thigh [RThg]": 0.05}],
                  filler=full),
           _shape("B", pos, [{PELV: 0.75, "NPC L Butt": 0.12,
                              SPINE: 0.09, LTHIGH: 0.04}], filler=full)]
    src = [_shape("A", pos, [{PELV: 0.8, SPINE: 0.2}]),
           _shape("B", pos, [{PELV: 0.8, SPINE: 0.2}])]
    _run(dst, src)
    a, b = dst[0].row(0), dst[1].row(0)
    assert a == b
    for row in (a, b):
        assert abs(sum(row.values()) - 1.0) < 1e-6, (
            f"row shipped light: {row} sums {sum(row.values()):.4f}")
        assert len(row) <= _MAX_INFLUENCES
    for sh in dst:
        _assert_no_bone_emptied(sh)


def test_stbs_are_restored_for_every_bone_written():
    # setShapeWeights can reset a skin-to-bone xform, and an identity STB skins
    # its verts to the origin -- the explosion class.
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.6, SPINE: 0.4}]),
           _shape("B", pos, [{PELV: 0.9, SPINE: 0.1}])]
    src = [_shape("A", pos, [{PELV: 0.8, SPINE: 0.2}]),
           _shape("B", pos, [{PELV: 0.8, SPINE: 0.2}])]
    _run(dst, src)
    assert dst[0].stb_writes >= 1 and dst[1].stb_writes >= 1


def test_no_vertex_is_moved():
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.6, SPINE: 0.4}]),
           _shape("B", pos, [{PELV: 0.9, SPINE: 0.1}])]
    src = [_shape("A", pos, [{PELV: 0.8, SPINE: 0.2}]),
           _shape("B", pos, [{PELV: 0.8, SPINE: 0.2}])]
    before = [list(s.verts) for s in dst]
    _run(dst, src)
    assert [list(s.verts) for s in dst] == before


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def test_kill_switch_is_a_complete_no_op(monkeypatch):
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.6, SPINE: 0.4}]),
           _shape("B", pos, [{PELV: 0.9, SPINE: 0.1}])]
    src = [_shape("A", pos, [{PELV: 0.8, SPINE: 0.2}]),
           _shape("B", pos, [{PELV: 0.8, SPINE: 0.2}])]
    monkeypatch.setattr(nc, "COINCIDENT_SKIN_MATCH", False)
    saved = []
    _install(monkeypatch, dst, src, saved)
    assert nc._match_coincident_cross_shape_skin(
        "dst.nif", src_nif_path="src.nif") == 0
    assert saved == []
    assert dst[0].row(0) == {PELV: 0.6, SPINE: 0.4}


def test_without_a_source_path_the_pass_does_nothing():
    assert nc._match_coincident_cross_shape_skin("dst.nif") == 0


def test_authored_physics_geometry_is_skipped(monkeypatch):
    pos = [(0.0, 0.0, 77.0)]
    dst = [_shape("A", pos, [{PELV: 0.6, SPINE: 0.4}]),
           _shape("Cloth", pos, [{PELV: 0.9, SPINE: 0.1}])]
    src = [_shape("A", pos, [{PELV: 0.8, SPINE: 0.2}]),
           _shape("Cloth", pos, [{PELV: 0.8, SPINE: 0.2}])]
    saved = []
    _install(monkeypatch, dst, src, saved)
    monkeypatch.setattr(nc, "_hdt_softbody_shape_names",
                        lambda p, nif=None: {"Cloth"})
    assert nc._match_coincident_cross_shape_skin(
        "dst.nif", src_nif_path="src.nif") == 0
    assert dst[1].row(0) == {PELV: 0.9, SPINE: 0.1}


def test_unified_row_never_exceeds_the_four_bone_cap():
    pos = [(0.0, 0.0, 77.0)]
    quarter = {PELV: 0.25, SPINE: 0.25, SPINE1: 0.25, BELLY: 0.25}
    dst = [_shape("A", pos, [{PELV: 0.4, SPINE: 0.3, SPINE1: 0.2, BELLY: 0.1}],
                  filler=quarter),
           _shape("B", pos, [{PELV: 0.1, SPINE: 0.2, SPINE1: 0.3, BELLY: 0.4}],
                  filler=quarter)]
    src = [_shape("A", pos, [dict(quarter)]),
           _shape("B", pos, [dict(quarter)])]
    _run(dst, src)
    for sh in dst:
        assert len(sh.row(0)) <= _MAX_INFLUENCES


def test_deterministic_across_runs():
    def once():
        pos = [(0.0, 0.0, 77.0)]
        dst = [_shape("A", pos, [{PELV: 0.5, SPINE: 0.5}],
                      filler={PELV: 0.4, SPINE: 0.3, SPINE1: 0.3}),
               _shape("B", pos, [{PELV: 0.5, SPINE1: 0.5}],
                      filler={PELV: 0.4, SPINE: 0.3, SPINE1: 0.3})]
        src = [_shape("A", pos, [{PELV: 0.5, SPINE: 0.5}]),
               _shape("B", pos, [{PELV: 0.5, SPINE: 0.5}])]
        _run(dst, src)
        return [sh.row(0) for sh in dst]

    first = once()
    for _ in range(4):
        assert once() == first
