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

"""#authored-shape-order (BUG-09) -- shape ORDER is output state.

An ARMA `AlternateTextures` entry selects the shape it re-textures by INDEX,
not by the 3D name stored beside it. So a converted mesh whose shapes are
permuted relative to the author's silently re-points every colour-variant swap
that names it, including swaps in third-party patches we do not own. Confirmed
in game: a white top loaded the default fabric texture while the actor's SKIN
texture broke, because the swap bound to index 0 and our injected `BaseShape`
sat there instead of the garment.

Two routes permuted the shapes and this file pins BOTH:

  * the UBE body was injected BEFORE pass 2 -> it took index 0 and shifted every
    authored shape by +1. Fixed by MOVING the call, so the guard is positional
    (`test_body_injection_happens_after_the_armour_shapes`);
  * `_finalize_hdt_physics` drops textureless collision proxies and re-appends
    them `sorted()` at the end. Repaired by `_restore_authored_shape_order`.

The end-to-end tests build REAL NIFs -- the permutation only exists in the
written bytes, so a mocked shape list could not see it.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402
from tests.synthetic_nif import (  # noqa: E402
    TRIS, VERTS, pynifly_available,
)

pytestmark = pytest.mark.skipif(
    not pynifly_available(), reason="pynifly native lib not available")

_BONES = ("NPC Spine [Spn0]", "NPC Spine1 [Spn1]")


def _build_multi_shape_nif(path, names):
    """A REAL SKYRIMSE NIF carrying `names` as skinned shapes, in that order.

    Skinned on purpose: `_reauthor_nif_fresh` rebuilds through `_copy_shape`,
    which installs skin -- an unskinned fixture would exercise a different path
    from the one that ships.
    """
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    uvs = [(0.0, 0.0)] * len(VERTS)
    normals = [(0.0, 0.0, 1.0)] * len(VERTS)
    for nm in names:
        sh = nif.createShapeFromData(nm, VERTS, TRIS, uvs, normals)
        sh.skin()
        for bn in _BONES:
            sh.add_bone(bn)
        idt = pyn.TransformBuf()
        idt.set_identity()
        for bn in _BONES:
            try:
                sh.set_skin_to_bone_xform(bn, idt)
            except Exception:
                pass
        half = len(VERTS) // 2
        sh.setShapeWeights(_BONES[0], [(i, 1.0) for i in range(half)])
        sh.setShapeWeights(_BONES[1],
                           [(i, 1.0) for i in range(half, len(VERTS))])
    nif.save()
    return path


def _shape_names(path):
    """Shape names in ON-DISK order -- the only order the engine indexes by."""
    return [s.name for s in nc._pynifly().NifFile(filepath=str(path)).shapes]


# --------------------------------------------------------------------------
# The repair: dropped-and-re-appended proxies go back to their authored slots
# --------------------------------------------------------------------------

def test_reappended_proxies_return_to_their_authored_index(tmp_path):
    """The measured college-robe permutation, reproduced and repaired.

    Author ships `robes, sash, bcol, rear, col, body`; the converter drops the
    four textureless proxies and re-appends them sorted, shipping
    `robes, body, bcol, col, rear, sash`. Every authored shape except `robes`
    is then at the wrong index.
    """
    src = _build_multi_shape_nif(
        tmp_path / "src.nif", ["robes", "sash", "bcol", "rear", "col", "body"])
    dst = _build_multi_shape_nif(
        tmp_path / "dst.nif", ["robes", "body", "bcol", "col", "rear", "sash"])

    moved = nc._restore_authored_shape_order(dst, src)

    assert moved > 0, "the pass did not fire -- a null result here is a broken test, not a clean mesh"
    assert _shape_names(dst) == ["robes", "sash", "bcol", "rear", "col", "body"]


def test_injected_and_generated_shapes_land_after_the_authored_ones(tmp_path):
    """The reported piece. Author `FabricChest, LeatherChest`; ours put the
    injected `BaseShape` first, so the swap bound to `Index=0` hit the BODY.

    `ButtCol` stands in for a collider we generate: like `BaseShape` it has no
    authored index to preserve, so it belongs after the authored shapes.
    """
    src = _build_multi_shape_nif(
        tmp_path / "src.nif", ["FabricChest", "LeatherChest"])
    dst = _build_multi_shape_nif(
        tmp_path / "dst.nif",
        ["BaseShape", "FabricChest", "ButtCol", "LeatherChest"])

    assert nc._restore_authored_shape_order(dst, src) > 0
    names = _shape_names(dst)
    assert names[:2] == ["FabricChest", "LeatherChest"], (
        "authored shapes must occupy their authored indices")
    assert set(names[2:]) == {"BaseShape", "ButtCol"}, (
        "shapes we added must follow, and none may be dropped")


def test_the_alternate_texture_index_now_selects_the_authored_shape(tmp_path):
    """The property the defect is actually about, asserted as the engine sees it.

    `Name=FabricChest, Index=0` must select `FabricChest`. Before the fix that
    index named `BaseShape`, which is the whole bug -- so this asserts the
    binding, not just the permutation.
    """
    src = _build_multi_shape_nif(
        tmp_path / "src.nif", ["FabricChest", "LeatherChest"])
    dst = _build_multi_shape_nif(
        tmp_path / "dst.nif", ["BaseShape", "FabricChest", "LeatherChest"])

    assert _shape_names(dst)[0] == "BaseShape", "fixture must start BROKEN"
    nc._restore_authored_shape_order(dst, src)
    assert _shape_names(dst)[0] == "FabricChest"


# --------------------------------------------------------------------------
# Controls: the pass must be able to decline, and must not touch a good file
# --------------------------------------------------------------------------

def test_an_already_faithful_nif_is_not_rebuilt_at_all(tmp_path):
    """THE NEGATIVE CONTROL. A piece whose order already matches the author must
    come out BYTE-IDENTICAL -- the rebuild is only paid by pieces that were
    wrong. If this ever fails, the pass is rewriting the whole pack to no
    purpose and every A/B built on it is measuring the rebuild, not the fix.
    """
    names = ["FabricChest", "LeatherChest", "BaseShape"]
    src = _build_multi_shape_nif(tmp_path / "src.nif", names[:2])
    dst = _build_multi_shape_nif(tmp_path / "dst.nif", names)
    before = dst.read_bytes()

    assert nc._restore_authored_shape_order(dst, src) == 0
    assert dst.read_bytes() == before, "a faithful NIF must not be re-emitted"


def test_the_kill_switch_declines_without_touching_the_file(tmp_path, monkeypatch):
    src = _build_multi_shape_nif(tmp_path / "src.nif", ["A", "B"])
    dst = _build_multi_shape_nif(tmp_path / "dst.nif", ["B", "A"])
    before = dst.read_bytes()

    monkeypatch.setattr(nc, "AUTHORED_SHAPE_ORDER", False)
    assert nc._restore_authored_shape_order(dst, src) == 0
    assert dst.read_bytes() == before


def test_a_missing_source_leaves_the_output_alone(tmp_path):
    """No source means nothing to be faithful TO. Declining is correct; guessing
    an order would permute a mesh on no evidence."""
    dst = _build_multi_shape_nif(tmp_path / "dst.nif", ["B", "A"])
    before = dst.read_bytes()
    assert nc._restore_authored_shape_order(dst, tmp_path / "gone.nif") == 0
    assert dst.read_bytes() == before


def test_no_shape_is_ever_dropped_or_duplicated(tmp_path):
    """A reorder that loses a shape is a silent partial-mesh loss -- the exact
    failure `_reauthor_nif_fresh` refuses to commit for. Assert set equality,
    not just length."""
    src = _build_multi_shape_nif(tmp_path / "src.nif", ["A", "B", "C"])
    dst = _build_multi_shape_nif(
        tmp_path / "dst.nif", ["C", "Extra", "A", "B"])

    nc._restore_authored_shape_order(dst, src)
    got = _shape_names(dst)
    assert sorted(got) == sorted(["A", "B", "C", "Extra"])
    assert len(got) == len(set(got))


def test_a_shape_the_source_has_but_the_output_dropped_is_skipped(tmp_path):
    """The converter legitimately drops shapes (`_should_drop_shape`, textureless
    proxies with no XML entry). A name in the authored order that the output does
    not have must be skipped, not resurrected."""
    src = _build_multi_shape_nif(tmp_path / "src.nif", ["A", "Gone", "B"])
    dst = _build_multi_shape_nif(tmp_path / "dst.nif", ["B", "A"])

    nc._restore_authored_shape_order(dst, src)
    assert _shape_names(dst) == ["A", "B"]


def test_partial_shape_order_keeps_unlisted_shapes_in_relative_order(tmp_path):
    """`_reauthor_nif_fresh(shape_order=...)` takes a PARTIAL list: unlisted
    shapes follow, in the order they already had. Pinned because the ordering is
    a `sort` with a default rank -- an unstable sort would scramble them."""
    dst = _build_multi_shape_nif(
        tmp_path / "dst.nif", ["A", "B", "C", "D"])
    assert nc._reauthor_nif_fresh(dst, shape_order=["D", "C"])
    assert _shape_names(dst) == ["D", "C", "A", "B"]


# --------------------------------------------------------------------------
# Route 1 is positional, so its guard has to be positional too
# --------------------------------------------------------------------------

def test_body_injection_happens_after_the_armour_shapes():
    """`_inject_ube_baseshape` must be called AFTER pass 2 inside
    `convert_nif_phase2`.

    This is the whole of the route-1 fix and nothing else pins it: moving that
    one call back above the pass-2 loop re-breaks every colour variant in the
    pack while every other test still passes. Verified to FAIL when the call is
    moved back above the loop.
    """
    src = Path(nc.__file__).read_text(encoding="utf-8")
    phase2 = src.index("def convert_nif_phase2(")
    body = src[phase2:]

    inject = body.index("_inject_ube_baseshape(")
    pass2 = body.index("# Pass 2: copy shapes")
    assert inject > pass2, (
        "the UBE body is being injected before the authored shapes are copied, "
        "so it takes shape index 0 and shifts every authored shape by +1 -- "
        "which re-points every ARMA AlternateTextures entry (BUG-09)")

    # ...and it must still precede the BODYTRI carrier pick, which needs
    # BaseShape present to choose the multi-carrier branch.
    carriers = body.index("_pick_bodytri_carriers(dst_nif)")
    assert inject < carriers, (
        "BaseShape must exist before _pick_bodytri_carriers runs")


def test_the_precondition_check_stays_before_the_fit_work():
    """An unusable UBE reference must still fail FAST. The copy moved down; the
    precondition deliberately did not, or a broken reference is only discovered
    after ~1600 lines of fitting."""
    src = Path(nc.__file__).read_text(encoding="utf-8")
    body = src[src.index("def convert_nif_phase2("):]
    assert body.index("_injectable") < body.index("# Pass 2: copy shapes")


def test_the_reimported_proxy_order_is_still_deterministic():
    """The `sorted()` on the re-import list is a determinism fix in its own right
    (hash-seed dependence, proven 2026-08-18). The order repair runs LATER and
    must not tempt anyone into removing it."""
    src = Path(nc.__file__).read_text(encoding="utf-8")
    blk = src[src.index("def _finalize_hdt_physics("):]
    blk = blk[:blk.index("\ndef ", 10)]
    assert re.search(r"missing\s*=\s*sorted\(", blk), (
        "the collision-proxy re-import must stay sorted -- iterating the set "
        "directly makes output byte-order follow the interpreter hash seed")
