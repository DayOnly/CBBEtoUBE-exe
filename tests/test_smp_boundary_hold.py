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

"""`_hold_weights_at_smp_boundary` -- #smp-boundary-weight-hold.

THE DEFECT, reported in game twice on the same cuirass: "disconnect between the
belt and abdomen ... weighted wrong between layers", then "weight issues
especially near the abdomen". A shape the physics XML declares as a
`<per-vertex-shape>` MUST keep its authored rig or it comes loose from the actor
and drifts, so `_hdt_softbody_shape_names` protects it. The layer touching it
has no such protection and is refitted onto the UBE body. Measured on the
reported piece, the protected shape sat at 0.0000 from its author while its
neighbour sat at 0.3956 with 2248 of 9504 verts past 0.5 -- and in the same band
the two moved OPPOSITE ways, Spine -> Spine2 against Spine/Spine1 -> Pelvis.

What is pinned here is the shape of the rule, not the tuning:

  * the hold FADES with distance (a hard cap only relocates the shear to the
    edge of the capped region, which is why the falloff is the design);
  * body-follow beyond the falloff is untouched -- the pass must not quietly
    undo the conversion it sits at the end of;
  * NO BONE IS EVER ADDED, because `add_bone` resets every skin-to-bone xform;
  * a piece with no physics cloth is not merely unchanged but never written.

Several tests assert the BEFORE state is already wrong, so a fixture that
happens to start correct fails loudly instead of passing while measuring
nothing ([[feedback_measurement_discipline]]).
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
RARE = "NPC L Clavicle [LClv]"
_MAX_INFLUENCES = 4


class FakeShape:
    """Duck-typed pynifly shape with the real write semantics.

    `setShapeWeights` UPDATES rather than replaces and the buffer holds four
    bones, so a row that drops a bone must write it to 0.0 first. Modelling
    that here is the point: a pass that forgets it ships rows summing wrong.
    """

    def __init__(self, name, verts, rows):
        self.name = name
        self.verts = [tuple(map(float, v)) for v in verts]
        self._rows = [dict(r) for r in rows]
        self.has_global_to_skin = False
        self.global_to_skin = None
        self._stb = {b: object() for b in self.bone_names}
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
        self._stb[bn] = st

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


def _l1(a, b):
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))


def _run(dst_shapes, src_shapes, protected, **patches):
    """Run the pass against in-memory NIFs, reverting every patch on exit.

    The context manager is not optional: a bare `pytest.MonkeyPatch()` never
    reverts, and a leaked `nc._pynifly` has broken unrelated files before.
    """
    saved = []

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (FakeNif(dst_shapes) if str(filepath) == "dst.nif"
                    else FakeNif(src_shapes))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "_hdt_softbody_shape_names",
                   lambda p, nif=None: set(protected))
        mp.setattr(nc, "_hide_virtual_body", lambda nf: False)
        mp.setattr(nc, "atomic_nif_save", lambda nf, p: saved.append(p))
        for k, v in patches.items():
            mp.setattr(nc, k, v)
        n = nc._hold_weights_at_smp_boundary("dst.nif", src_nif_path="src.nif")
    return n, saved


# The protected layer, identical in source and output -- it is what the free
# shape is being held to agree with. Sampled densely along z at x=y=0 so a free
# vert's distance to it IS its own x offset. Two verts 2u apart is not enough:
# the nearest one is then over a unit away in z, the falloff reads 0.91 instead
# of 1.0, and the fixture looks like a bug in the pass.
def _skirt():
    zs = [70.0 + 0.5 * k for k in range(29)]         # 70..84 every 0.5u
    return FakeShape("Skirt", [(0.0, 0.0, z) for z in zs],
                     [{PELV: 0.6, SPINE: 0.4} for _ in zs])


# The author's rig for the free layer, and the rig the body-follow gave it.
# These are the reported piece's own direction of travel: the author sits on
# Spine/Spine1, the conversion drove it up to Spine2.
AUTHOR_ROW = {PELV: 0.20, SPINE: 0.50, SPINE1: 0.30}
OURS_ROW = {PELV: 0.10, SPINE: 0.20, SPINE1: 0.30, SPINE2: 0.40}


def _pair(offsets):
    """A free shape at the given x offsets from the skirt, plus its author.

    FILLER VERTS ARE NOT DECORATION. They sit far beyond the falloff, so the
    pass leaves them alone, and they keep `SPINE2` alive somewhere in the shape.
    Without them every fixture is a shape whose ONLY vertex carries SPINE2 --
    holding it to the author empties that bone out of the whole shape, the
    zero-weight rescue correctly reverts the vertex, and the pass looks broken
    when it is the fixture that is degenerate. A real garment carries a bone on
    hundreds of verts.
    """
    verts = [(float(x), 0.0, 77.0) for x in offsets] + [(60.0, 0.0, 77.0)] * 2
    dst = FakeShape("Cuirass", verts,
                    [dict(OURS_ROW) for _ in range(len(verts))])
    src = FakeShape("Cuirass", verts,
                    [dict(AUTHOR_ROW) for _ in offsets]
                    + [dict(OURS_ROW) for _ in range(2)])
    return dst, src


def test_a_vertex_against_the_physics_layer_is_returned_to_its_author():
    dst, src = _pair([0.2])
    assert _l1(dst.row(0), AUTHOR_ROW) > 0.5, (
        "fixture is inert: the vertex must START disagreeing with the author "
        "or this test proves nothing")
    n, saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n == 1 and saved, "the pass reported no work and saved nothing"
    assert _l1(dst.row(0), AUTHOR_ROW) < 0.02, (
        f"a vertex 0.2u from the protected layer kept {dst.row(0)}")


def test_a_vertex_far_from_the_physics_layer_keeps_its_body_follow():
    """The pass sits at the END of the fit chain. If it reached the whole
    garment it would quietly undo the conversion, not repair a seam."""
    dst, src = _pair([20.0])
    n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n == 0
    assert dst.row(0) == pytest.approx(OURS_ROW), (
        "body-follow beyond the falloff was overwritten")
    assert not dst.writes, "a shape outside the falloff was written to"


def test_the_hold_fades_with_distance():
    """THE DESIGN. A hard cap would leave a step at its own edge -- the same
    shear, moved outward. Nearer must mean closer to the author, strictly."""
    dst, src = _pair([0.2, 2.0, 4.0, 20.0])
    _n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    d = [_l1(dst.row(i), AUTHOR_ROW) for i in range(4)]
    assert d[0] < d[1] < d[2] < d[3], (
        f"the hold is not monotonic in distance: {d}")
    assert d[3] == pytest.approx(_l1(OURS_ROW, AUTHOR_ROW)), (
        "the far vertex should be untouched")


def test_a_piece_with_no_physics_cloth_is_never_written():
    """THE CONTROL, and the reason the pass is cheap on 1358 of 1536 pack
    NIFs. Measured pack-wide: 13 of 13 such pieces came back byte-identical."""
    dst, src = _pair([0.2])
    n, saved = _run([_skirt(), dst], [_skirt(), src], set())
    assert n == 0
    assert not saved, "a piece with no per-vertex-shape was re-saved"
    assert not dst.writes
    assert dst.row(0) == pytest.approx(OURS_ROW)


def test_the_protected_shape_itself_is_never_written():
    """Rewriting the SMP layer is the one thing that must never happen: it
    un-anchors the cloth from the actor and the garment drifts away."""
    skirt = _skirt()
    dst, src = _pair([0.2])
    _n, _saved = _run([skirt, dst], [_skirt(), src], {"Skirt"})
    assert not skirt.writes, "the protected physics shape was rewritten"


def test_no_bone_is_ever_added():
    """`add_bone` resets every skin-to-bone xform, which ships colliders at the
    origin. The blend is restricted to the destination's own palette, so an
    author bone we do not carry is dropped and the rest renormalised."""
    dst, src = _pair([0.2])
    src._rows[0] = {PELV: 0.20, SPINE: 0.60, BELLY: 0.20}   # BELLY not in dst
    before = set(dst.bone_names)
    n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n == 1, "the vertex should still be held on the shared bones"
    assert BELLY not in dst.row(0), "an author-only bone was added to the shape"
    assert set(dst.bone_names) <= before, "the shape gained a bone"


def test_a_row_the_palette_cannot_carry_is_left_alone():
    """If most of the author's weight sits on bones we do not have, the
    renormalised remainder is a guess, not a restoration. Leave it."""
    dst, src = _pair([0.2])
    src._rows[0] = {BELLY: 0.80, PELV: 0.20}     # 80% on a bone dst lacks
    n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n == 0
    assert dst.row(0) == pytest.approx(OURS_ROW)


def test_held_rows_still_sum_to_one_and_hold_at_most_four_bones():
    dst, src = _pair([0.2, 2.0, 4.0])
    _n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    for i in range(3):
        r = dst.row(i)
        assert len(r) <= _MAX_INFLUENCES, f"vert {i} holds {len(r)} bones"
        assert sum(r.values()) == pytest.approx(1.0, abs=1e-6), (
            f"vert {i} sums to {sum(r.values())}")


def test_a_shape_the_converter_rebuilt_has_no_author_to_hold_to():
    """Vert counts differ -> the shapes cannot be paired by index, and pairing
    by proximity is exactly the guesswork this pass exists to avoid."""
    dst, src = _pair([0.2])
    src.verts = src.verts + [(0.0, 0.0, 90.0)]
    src._rows = src._rows + [{PELV: 1.0}]
    n, saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n == 0 and not saved
    assert dst.row(0) == pytest.approx(OURS_ROW)


def test_kill_switch_is_a_complete_no_op():
    dst, src = _pair([0.2])
    n, saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"},
                    SMP_BOUNDARY_HOLD=False)
    assert n == 0 and not saved and not dst.writes


def test_the_pass_never_raises_on_a_broken_shape():
    """It runs at the end of a conversion; an exception here would turn a
    weighting repair into a failed piece."""
    class _Broken(FakeShape):
        @property
        def bone_weights(self):
            raise RuntimeError("no skin data")

    dst, src = _pair([0.2])
    bad = _Broken("Cuirass2", [(0.3, 0.0, 77.0)], [{PELV: 1.0}])
    n, _saved = _run([_skirt(), dst, bad], [_skirt(), src], {"Skirt"})
    assert isinstance(n, int)
    assert _l1(dst.row(0), AUTHOR_ROW) < 0.02, (
        "one broken shape stopped the good shape being repaired")


# --------------------------------------------------------------------------
# #zeroweight-bone-desync -- found by the pack-wide gate AFTER the first
# reconvert that shipped this pass, not by these tests. That is the gap this
# section closes.
# --------------------------------------------------------------------------

def test_the_pass_never_takes_a_bones_LAST_vertex():
    """A bone left in the shape carrying no weight is dropped from the
    regenerated skin-partition palette, so a per-vertex bone index can run past
    it -- an equip CTD.

    The 4-influence cap causes this WITHOUT the pass ever meaning to remove a
    bone: it evicts the lightest, and on the one vertex that was a bone's last
    carrier that eviction empties it out of the shape.
    """
    near = (0.2, 0.0, 77.0)
    far = (60.0, 0.0, 77.0)
    # RARE is carried on the NEAR vertex only, lightly enough that blending the
    # author's row in evicts it.
    dst = FakeShape("Cuirass", [near, far, far],
                    [{PELV: 0.30, SPINE: 0.30, SPINE1: 0.30, RARE: 0.10}]
                    + [dict(OURS_ROW) for _ in range(2)])
    src = FakeShape("Cuirass", [near, far, far],
                    [{PELV: 0.34, SPINE: 0.33, SPINE1: 0.33}]
                    + [dict(OURS_ROW) for _ in range(2)])
    assert RARE in dst.bone_names, "fixture is inert: RARE is not on the shape"
    _n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    live = [w for _i, w in (dst.bone_weights.get(RARE) or []) if w > 1e-4]
    assert live, (
        f"{RARE} was left in the shape with no weight -- palette desync, the "
        f"#zeroweight-bone-desync class")


def test_a_bone_already_empty_before_the_pass_is_not_resurrected():
    """The rescue must not adopt bones an EARLIER pass emptied -- that would
    make this pass responsible for every upstream leftover, and it would revert
    vertices for a defect it did not cause."""
    dst, src = _pair([0.2])
    dst._rows[0][RARE] = 0.0          # present in name only, already empty
    # The fake builds its xform table at construction, so a bone injected
    # afterwards has none and the shape is skipped WHOLE by the STB guard --
    # which would make this test pass while measuring nothing.
    dst._stb.setdefault(RARE, object())
    n, _saved = _run([_skirt(), dst], [_skirt(), src], {"Skirt"})
    assert n >= 1, "the pass should still hold the near vertex to the author"
    assert _l1(dst.row(0), AUTHOR_ROW) < 0.02, (
        "an already-empty bone made the pass revert a vertex it should hold")
