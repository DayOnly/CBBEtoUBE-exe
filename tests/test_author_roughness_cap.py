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

"""Author-relative weight-roughness cap (#author-roughness-cap).

Reported in game as ONE vertex weighted wrong on a leather panel above and
below its belts: making cloth follow the body fills nearly every row to the
four-influence limit, and where the rewritten row holds a near tie for the
fourth slot, neighbouring vertices keep DIFFERENT bones -- so a lone vertex
bends at the chest while the surface around it bends at the waist.

THE FIRST VERSION OF THIS PASS SHIPPED AS A SILENT NO-OP. It called
`get_skin_to_bone_xform`, which does not exist (the reader is
`get_shape_skin_to_bone`), behind a bare `except: continue` -- so every shape
was skipped, the pass returned 0, printed nothing, and read exactly like
"nothing qualified" while 2300 vertices sat queued for repair. It was caught
only by an ON-vs-OFF byte compare of converted meshes.

So the FIRST test here is not about smoothing at all: it is that the pass can
still FIRE, and that the accessors it depends on still exist. Everything else
is downstream of that.

Driven through the same duck-typed pynifly as the coincident-skin tests, whose
`setShapeWeights` reproduces the two semantics the real one has: it MERGES (a
bone given up must be written explicitly at 0.0), and the native buffer holds
four influences and drops the smallest on overflow.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import src.nif_convert as nc  # noqa: E402
# Reuse the sibling pass's duck-typed pynifly rather than writing a second one:
# its setShapeWeights reproduces the MERGE + four-influence-overflow semantics
# this pass is equally required to survive, and a fake that drifts from the
# real writer is how a test starts measuring itself.
from test_coincident_skin_match import FakeNif, FakeShape  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
SPINE = "NPC Spine [Spn0]"
SPINE1 = "NPC Spine1 [Spn1]"
SPINE2 = "NPC Spine2 [Spn2]"


def _grid(rows_by_vert, name="3LeatherMain"):
    """A 4-vertex strip: two triangles, so every vertex has neighbours.

    Positions are irrelevant to this pass (it reads topology and weights only)
    but must be distinct so nothing degenerates.
    """
    verts = [(0.0, 0.0, 80.0), (1.0, 0.0, 81.0),
             (2.0, 0.0, 82.0), (3.0, 0.0, 83.0)]
    sh = FakeShape(name, verts, rows_by_vert)
    sh.tris = [(0, 1, 2), (1, 2, 3)]
    return sh


def _run(dst_shapes, src_shapes):
    """Run the pass against in-memory NIFs, undoing the patches on the way out.

    The context manager is not optional -- a bare `pytest.MonkeyPatch()` never
    reverts, and has previously broken 15 tests in an unrelated file.
    """
    saved = []

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return (FakeNif(dst_shapes) if str(filepath) == "dst.nif"
                    else FakeNif(src_shapes))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn)
        mp.setattr(nc, "_hide_virtual_body", lambda nf: False)
        mp.setattr(nc, "atomic_nif_save", lambda nf, p: saved.append(p))
        n = nc._cap_weight_roughness_to_author("dst.nif",
                                               src_nif_path="src.nif")
    return n, saved


# A smooth author field, and OUR field with vertex 1 spiked onto a different
# spine level -- the reported defect in miniature.
_SMOOTH = [{SPINE1: 0.6, PELV: 0.4},
           {SPINE1: 0.6, PELV: 0.4},
           {SPINE1: 0.6, PELV: 0.4},
           {SPINE1: 0.6, PELV: 0.4}]
_SPIKED = [{SPINE1: 0.6, PELV: 0.4},
           {SPINE2: 0.7, SPINE: 0.3},      # <- bends on the wrong level
           {SPINE1: 0.6, PELV: 0.4},
           {SPINE1: 0.6, PELV: 0.4}]


# --------------------------------------------------------------------------
# 1. It can still FIRE -- the v1 regression guard
# --------------------------------------------------------------------------

def test_the_pass_actually_fires_on_a_rough_vertex():
    """v1 returned 0 forever. A pass whose only failure signal is `0` needs a
    test that the non-zero path is reachable at all."""
    n, saved = _run([_grid(_SPIKED)], [_grid(_SMOOTH)])
    assert n > 0, (
        "the pass reported nothing repaired on a vertex that is plainly "
        "rougher than its author -- this is the v1 silent no-op signature")
    assert saved, "it repaired rows but never saved the NIF"


def test_every_pynifly_accessor_the_pass_uses_exists_on_a_real_shape():
    """THE v1 BUG, pinned directly: the pass named a method that does not
    exist, and a bare except turned that into 'nothing qualified'.

    Asserted against the real pynifly Shape class, not the fake -- a fake that
    implements whatever the pass happens to call can never catch this.
    """
    pyn = nc._pynifly()
    shape_cls = getattr(pyn, "NiShape", None) or getattr(pyn, "BSTriShape")
    for attr in ("get_shape_skin_to_bone", "set_skin_to_bone_xform",
                 "setShapeWeights", "bone_names", "bone_weights",
                 "verts", "tris"):
        assert hasattr(shape_cls, attr), (
            f"{shape_cls.__name__} has no {attr!r} -- the roughness cap calls "
            f"it, and behind a swallowed exception that reads as a clean no-op")
    assert not hasattr(shape_cls, "get_skin_to_bone_xform"), (
        "a method by the v1 (wrong) name now exists; the guard above no longer "
        "distinguishes the right accessor from the wrong one")


def test_the_kill_switch_and_the_missing_source_both_disable_it():
    """Both zero-returning early exits, so neither is mistaken for a no-op."""
    n, _ = _run([_grid(_SPIKED)], [_grid(_SMOOTH)])
    assert n > 0                                    # control: it would fire
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "AUTHOR_ROUGHNESS_CAP", False)
        off, _ = _run([_grid(_SPIKED)], [_grid(_SMOOTH)])
    assert off == 0, "the kill switch did not turn the pass off"
    saved = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "atomic_nif_save", lambda nf, p: saved.append(p))
        assert nc._cap_weight_roughness_to_author("dst.nif",
                                                  src_nif_path=None) == 0
    assert not saved, "it saved a NIF with no author to score against"


# --------------------------------------------------------------------------
# 2. What it must NOT do
# --------------------------------------------------------------------------

def test_it_never_moves_a_vertex():
    """The docstring promises weights only, and the whole upstream clearance
    corpus rests on that."""
    dst = _grid(_SPIKED)
    before = [tuple(v) for v in dst.verts]
    n, _ = _run([dst], [_grid(_SMOOTH)])
    assert n > 0
    assert [tuple(v) for v in dst.verts] == before, "the pass moved geometry"


def test_a_shape_no_rougher_than_its_author_takes_no_writes():
    """The pass's own control: self-limiting by construction. On the reported
    outfit the belts, buckles and metal buttons took zero writes."""
    dst = _grid(_SMOOTH)
    n, saved = _run([dst], [_grid(_SMOOTH)])
    assert n == 0, "it rewrote a shape that was already as smooth as its author"
    assert not dst.writes and not saved


def test_it_preserves_a_discontinuity_the_AUTHOR_put_there():
    """Scored against the AUTHOR, never against zero. An author who weights a
    hard panel edge must keep it -- driving roughness to zero would flatten
    every seam ([[feedback_baseline_not_author]])."""
    author = _grid(_SPIKED)          # the author's OWN field is the rough one
    dst = _grid(_SPIKED)             # and ours matches it exactly
    n, _ = _run([dst], [author])
    assert n == 0, (
        "it smoothed an edge the author authored -- this pass may only remove "
        "roughness we ADDED")


def test_it_converges_rather_than_eating_the_field():
    """It is a SINGLE-PASS cap, not a fixed-point solve, and this pins which.

    Smoothing a vertex changes its neighbours' means, so a second application
    finds a little more to do -- it is NOT idempotent, and asserting that it
    were would be asserting something the design does not promise. What must
    hold is that it CONVERGES: each pass has strictly less to do than the last,
    and the field is not being progressively flattened. Measured on the
    reported piece: 830 rough vertices -> 28 after one pass, and a second pass
    would take that to 1.
    """
    dst = _grid(_SPIKED)
    n1, _ = _run([dst], [_grid(_SMOOTH)])
    assert n1 > 0
    n2, _ = _run([dst], [_grid(_SMOOTH)])
    assert n2 < n1, (
        f"a second run repaired {n2} vert(s) against the first run's {n1} -- "
        f"the operator is not converging, it is eating the field")
    n3, _ = _run([dst], [_grid(_SMOOTH)])
    assert n3 <= n2, f"third run {n3} > second {n2} -- diverging"


# --------------------------------------------------------------------------
# 3. The write contract
# --------------------------------------------------------------------------

def test_rows_stay_normalised_and_within_the_four_influence_limit():
    dst = _grid(_SPIKED)
    n, _ = _run([dst], [_grid(_SMOOTH)])
    assert n > 0
    for i in range(4):
        row = dst.row(i)
        assert len(row) <= 4, f"vert {i} ships {len(row)} influences"
        assert abs(sum(row.values()) - 1.0) < 1e-3, (
            f"vert {i} ships summing {sum(row.values()):.4f}")


def test_removals_are_written_before_additions():
    """`setShapeWeights` MERGES and the buffer drops the smallest of five, so a
    newcomer arriving while the bone it replaces still holds its old value
    loses the four-way contest and the row ships light.
    #family-weight-invariant"""
    dst = _grid(_SPIKED)
    n, _ = _run([dst], [_grid(_SMOOTH)])
    assert n > 0
    zeroing = [k for k, (_bn, prs) in enumerate(dst.writes)
               if all(float(w) <= 1e-4 for _i, w in prs)]
    valued = [k for k, (_bn, prs) in enumerate(dst.writes)
              if any(float(w) > 1e-4 for _i, w in prs)]
    if zeroing and valued:
        assert max(zeroing) < min(valued), (
            "a value was written before every removal -- the row can ship light")


def test_skin_to_bone_xforms_are_restored():
    """setShapeWeights can reset STBs; an unrestored one ships at identity =
    origin spike."""
    dst = _grid(_SPIKED)
    before = dict(dst._stb)
    n, _ = _run([dst], [_grid(_SMOOTH)])
    assert n > 0
    assert dst.stb_writes > 0, "no STB was restored after the weight write"
    assert dst._stb == before, "an STB came back different"


def test_an_unreadable_stb_is_reported_not_silently_skipped(capsys):
    """The remaining silent path, closed 2026-08-18: `get_shape_skin_to_bone`
    returns None rather than raising, so a shape whose STB cannot be read was
    dropped with no record -- the same shape of failure as v1, one layer down.
    """
    dst = _grid(_SPIKED)
    dst._stb = {}                                   # every read returns None
    n, saved = _run([dst], [_grid(_SMOOTH)])
    assert n == 0 and not saved                     # bailing is correct...
    err = capsys.readouterr().err
    assert "roughness cap: SKIPPED" in err, (
        "a whole shape was skipped silently; unrecorded, that is "
        "indistinguishable from 'nothing qualified'")


# --------------------------------------------------------------------------
# 4. Wiring
# --------------------------------------------------------------------------

def test_it_is_wired_into_both_convert_paths_with_a_source():
    """With `src_nif_path=None` the pass returns 0 immediately, so a call site
    that forgets it is a silent no-op on that whole path."""
    import inspect
    src = inspect.getsource(nc)
    calls = [ln for ln in src.splitlines()
             if "_cap_weight_roughness_to_author(" in ln and "def " not in ln
             and "_note_pass_failure" not in ln]
    assert len(calls) == 2, (
        f"expected one call per convert path, found {len(calls)}: {calls}")
    window = src.split("_cap_weight_roughness_to_author(")
    for chunk in window[1:3]:
        assert "src_nif_path=" in chunk[:120], (
            "a call site omits src_nif_path -- the pass silently returns 0")


def test_it_runs_before_the_coincident_match_on_both_paths():
    """This pass smooths a shape's INTERIOR; the coincident match settles shape
    BOUNDARIES and is in-game confirmed, so it must keep the last word."""
    import inspect
    src = inspect.getsource(nc)
    # Match the CALL form, not the bare name: a prefix match also hits the
    # `def` line, whose position in the file says nothing about run order.
    # That false positive is documented in test_spine_motion_match, which
    # tripped on it first.
    rough = [i for i in range(len(src))
             if src.startswith("_cap_weight_roughness_to_author(dst_path", i)
             and not src[max(0, i - 4):i].endswith("def ")]
    coinc = [i for i in range(len(src))
             if src.startswith("_match_coincident_cross_shape_skin(dst_path", i)
             and not src[max(0, i - 4):i].endswith("def ")]
    assert len(rough) == 2 and len(coinc) == 2, (
        f"expected 2 call sites each, found {len(rough)} and {len(coinc)} -- "
        f"zip below would silently skip the extras")
    for r, c in zip(rough, coinc):
        assert r < c, "the roughness cap must run BEFORE the coincident match"
