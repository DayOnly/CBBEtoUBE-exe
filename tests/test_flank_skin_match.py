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

r"""#flank-skin-match -- GitHub issue #41.

A piece converted from its BodySlide build keeps the author's skin (the build owns
a morph file, and re-skinning such a shape desyncs the file). The full-weight
match reaches those shapes weights-only, but only on rows with NO weight on a bone
the UBE body's skin lacks. At the flank of the bust the author's rows carry weight
on NPC L/R UpperarmTwist2, a skeleton arm bone the body's skin does not list, so
the match left them as authored and the flank followed the arm further than the
body under it: 9.25% of the covered flank showed skin with the arms down.

Pinned here, on a real (tiny) NIF through the real pass:

1. On a morph-owning shape, in the flank band, skeleton arm weight no longer makes
   a row unclean: the row is matched to the body and the twist weight goes.
2. Nothing else moves: not a row outside the band, not a row carrying a chain bone
   (anything but an actor-skeleton arm bone), not a shape without a morph file.
3. It is weights only: no bone is added, and the kill switch gives the old gate.
"""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc                                  # noqa: E402
from tests import _converter_sources as _cs                   # noqa: E402
from tests.synthetic_nif import pynifly_available             # noqa: E402

SP2 = "NPC Spine2 [Spn2]"
UA = "NPC L UpperArm [LUar]"
UT2 = "NPC L UpperarmTwist2 [LUt2]"
CHAIN = "SkirtF 4_01"

needs_pynifly = pytest.mark.skipif(not pynifly_available(),
                                   reason="pynifly native lib not available")


def _grid_x(x, y0, y1, z0, z1, n=6, flip=False):
    """A patch in the plane x = const (its normal points along +-x: a flank)."""
    ys, zs = np.linspace(y0, y1, n), np.linspace(z0, z1, n)
    verts = [(float(x), float(y), float(z)) for z in zs for y in ys]
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b = r * n + c, r * n + c + 1
            d, e = (r + 1) * n + c, (r + 1) * n + c + 1
            tris += [(a, e, b), (a, d, e)] if flip else [(a, b, e), (a, e, d)]
    return verts, tris


def _build(path):
    """BaseShape: a flank-facing patch at x = 0 over z 70-110, skinned to Spine2
    only. Plate: groups of rows 1u in front of it -- the flank band (z 92-100),
    below the band (z 74-80), the flank band carrying a chain bone, a "sleeve" row
    that also carries authored UpperArm weight, and a "clean" row with no foreign
    bone at all."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))

    def add(name, verts, tris, bones, weights):
        sh = nif.createShapeFromData(name, verts, tris, [(0.0, 0.0)] * len(verts),
                                     [(1.0, 0.0, 0.0)] * len(verts))
        tb = pyn.TransformBuf()
        tb.set_identity()
        sh.transform = tb
        sh.skin()
        idt = pyn.TransformBuf()
        idt.set_identity()
        for b in bones:
            sh.add_bone(b)
            sh.set_skin_to_bone_xform(b, idt)
        for b in bones:
            pairs = [(i, w[b]) for i, w in enumerate(weights) if w.get(b, 0.0) > 0]
            if pairs:
                sh.setShapeWeights(b, pairs)

    bv, bt = _grid_x(0.0, -6.0, 9.0, 70.0, 110.0, n=21)
    add("BaseShape", bv, bt, [SP2, UA], [{SP2: 1.0} for _ in bv])
    groups, verts, tris, weights = {}, [], [], []
    for key, (z0, z1, w) in {
            "band": (92.0, 100.0, {SP2: 0.5, UT2: 0.5}),
            "below": (74.0, 80.0, {SP2: 0.5, UT2: 0.5}),
            "chain": (92.0, 100.0, {SP2: 0.4, UT2: 0.3, CHAIN: 0.3}),
            "sleeve": (92.0, 100.0, {SP2: 0.2, UA: 0.5, UT2: 0.3}),
            "clean": (92.0, 100.0, {SP2: 0.5, UA: 0.5})}.items():
        y0 = {"band": -4.0, "below": -4.0, "chain": 0.5, "sleeve": 4.0, "clean": 4.0}[key]
        v, t = _grid_x(1.0, y0, y0 + 3.0, z0, z1, n=5, flip=True)
        off = len(verts)
        groups[key] = np.arange(off, off + len(v))
        verts += v
        tris += [(a + off, b + off, c + off) for a, b, c in t]
        weights += [dict(w) for _ in v]
    add("Plate", verts, tris, [SP2, UA, UT2, CHAIN], weights)
    nif.save()
    return groups


def _weight(path, bone):
    sh = next(s for s in nc._pynifly().NifFile(filepath=str(path)).shapes
              if s.name == "Plate")
    out = np.zeros(len(sh.verts))
    for i, w in sh.bone_weights.get(bone, []):
        out[i] += w
    return out, set(sh.bone_names)


def _run(tmp_path, monkeypatch, *, tri_owned=True, flank=True, name="Plate"):
    p = tmp_path / "piece_1.nif"
    groups = _build(p)
    monkeypatch.setattr(nc, "MATCH_FULL_WEIGHTS", True)
    monkeypatch.setattr(nc, "COVERED_SKIN_TARGET", False)
    monkeypatch.setattr(nc, "FLANK_SKIN_MATCH", flank)
    _cs.patch(monkeypatch, "_source_morph_tri_shape_names",
              lambda _p: {name} if tri_owned else set())
    nc._match_full_weights_to_body(str(p), 0, src_nif_path=str(tmp_path / "src_1.nif"))
    return p, groups


def test_flag_default_on_and_kill_switch(monkeypatch):
    assert nc.FLANK_SKIN_MATCH is True
    assert (nc._FLANK_Z_LO, nc._FLANK_Z_HI) == (90.0, 103.0)
    assert (nc._FLANK_NORMAL_X, nc._FLANK_NORMAL_Y) == (0.3, 0.8)
    monkeypatch.setenv("CBBE2UBE_NO_FLANK_SKIN_MATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.FLANK_SKIN_MATCH is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_FLANK_SKIN_MATCH", raising=False)
        importlib.reload(nc)


def test_the_instance_hands_the_switch_to_the_pass(monkeypatch):
    seen = {}
    monkeypatch.setattr(nc, "MATCH_FULL_WEIGHTS", True)
    for on in (True, False):
        monkeypatch.setattr(nc, "FLANK_SKIN_MATCH", on)
        _cs.patch(monkeypatch, "_match_limb_motion_to_body",
                  lambda *a, **k: seen.update(k) or 0)
        nc._match_full_weights_to_body("x_1.nif", 0, src_nif_path="s_1.nif")
        assert seen.get("flank_skeleton_ok") is on


@needs_pynifly
def test_a_flank_row_with_skeleton_arm_weight_is_matched_to_the_body(tmp_path, monkeypatch):
    p, g = _run(tmp_path, monkeypatch)
    twist, _bones = _weight(p, UT2)
    spine, _ = _weight(p, SP2)
    assert np.all(twist[g["band"]] < 0.02), "the twist weight is handed to the body's bones"
    assert np.allclose(spine[g["band"]], 1.0, atol=0.02), "and the row is the body's row"


@needs_pynifly
@needs_pynifly
def test_a_relaxed_row_keeps_its_authored_arm_chain_weight(tmp_path, monkeypatch):
    """The point of the second version: a sleeve row's nearest body vertex is torso,
    so copying the body's whole vector stripped the arm follow it needs (Imperial
    light cuirass, arms crossed 1.5% -> 3.75%). Only the non-arm bones follow the
    body; UpperArm stays as authored (0.5 of 1.5 after renormalising) and the
    skeleton-arm twist weight goes."""
    p, g = _run(tmp_path, monkeypatch)
    arm, _ = _weight(p, UA)
    twist, _ = _weight(p, UT2)
    spine, _ = _weight(p, SP2)
    assert np.allclose(arm[g["sleeve"]], 0.5 / 1.5, atol=0.02)
    assert np.all(twist[g["sleeve"]] < 0.02)
    assert np.allclose(spine[g["sleeve"]], 1.0 / 1.5, atol=0.02)


@needs_pynifly
def test_a_clean_row_still_takes_the_bodys_whole_vector(tmp_path, monkeypatch):
    """Rows the plain gate admits are not touched by the second version: the body
    skins this patch with Spine2 alone, so the arm weight goes, as it always did."""
    p, g = _run(tmp_path, monkeypatch)
    arm, _ = _weight(p, UA)
    assert np.all(arm[g["clean"]] < 0.02)


@needs_pynifly
def test_the_flank_band_row_without_arm_weight_is_the_bodys_row(tmp_path, monkeypatch):
    p, g = _run(tmp_path, monkeypatch)
    spine, _ = _weight(p, SP2)
    assert np.allclose(spine[g["band"]], 1.0, atol=0.02)


def test_a_row_below_the_band_keeps_its_authored_weights(tmp_path, monkeypatch):
    p, g = _run(tmp_path, monkeypatch)
    twist, _ = _weight(p, UT2)
    assert np.allclose(twist[g["below"]], 0.5, atol=1e-3)


@needs_pynifly
def test_a_row_carrying_a_chain_bone_is_still_left_alone(tmp_path, monkeypatch):
    """The clean-row gate protects authored physics: only a skeleton ARM bone is
    relaxed, never a chain."""
    p, g = _run(tmp_path, monkeypatch)
    twist, _ = _weight(p, UT2)
    chain, _ = _weight(p, CHAIN)
    assert np.allclose(twist[g["chain"]], 0.3, atol=1e-3)
    assert np.allclose(chain[g["chain"]], 0.3, atol=1e-3)


@needs_pynifly
def test_a_shape_without_a_morph_file_keeps_the_old_gate(tmp_path, monkeypatch):
    p, g = _run(tmp_path, monkeypatch, tri_owned=False)
    twist, _ = _weight(p, UT2)
    assert np.allclose(twist[g["band"]], 0.5, atol=1e-3)


@needs_pynifly
def test_the_kill_switch_gives_the_old_gate_back(tmp_path, monkeypatch):
    p, g = _run(tmp_path, monkeypatch, flank=False)
    twist, _ = _weight(p, UT2)
    assert np.allclose(twist[g["band"]], 0.5, atol=1e-3)


@needs_pynifly
def test_it_never_adds_a_bone(tmp_path, monkeypatch):
    p, _g = _run(tmp_path, monkeypatch)
    _w, bones = _weight(p, SP2)
    assert bones == {SP2, UA, UT2, CHAIN}


def test_the_relaxation_only_subtracts_skeleton_arm_weight():
    """The text of the gate: the effective foreign weight starts as the plain one
    and only skeleton ARM bones the body lacks are taken off it."""
    import inspect
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("_foreign_eff = foreign")
    branch = src[i:i + 1600]
    assert "_b not in ube_bones and _is_skeleton_bone(_b)" in branch
    assert "_is_arm_hand_bone(_b)" in branch
    assert "np.maximum(foreign - _skel, 0.0)" in branch
    assert "(_foreign_eff <= 1e-4)" in branch



def test_only_relaxed_rows_get_their_arm_chain_restored():
    import inspect
    src = inspect.getsource(nc._match_limb_motion_to_body)
    i = src.index("_relaxed = _sel & (foreign > 1e-4)")
    branch = src[i:i + 900]
    assert "_is_arm_hand_bone(_b) and _b in ube_bones" in branch
    assert "NEW[np.ix_(_rr, _keep)] = G[np.ix_(_rr, _keep)]" in branch
    assert "NEW[_rr[_ok]] /= _tot[_ok, None]" in branch
