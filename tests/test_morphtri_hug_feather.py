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

"""#morphtri-hug-feather: the leg match reaches a TRI-owning shape only where it
HUGS the body.

Reported in game 2026-09-26: on a one-piece plated cuirass whose source ships a
BodySlide TRI, the plate between the legs clipped into itself instead of
deforming. `#leg-motion-morphtri` had let the leg match reach that shape at the
pass's 9u hug distance, and the plates stand 2-4u off the body. The fix keeps the
match on fitted rows (the trousers it was built for sit at 0.6-1.9u) and fades
it out between _MORPHTRI_HUG_NEAR and _MORPHTRI_HUG_FAR.
"""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc                                  # noqa: E402
from src import nif_convert_weights as ncw                    # noqa: E402
from tests import _converter_sources as _cs                   # noqa: E402
from tests.synthetic_nif import pynifly_available             # noqa: E402

LT, RT, PV = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]"


def test_flag_default_on_and_kill_switch(monkeypatch):
    assert nc.MORPHTRI_HUG_FEATHER is True
    assert (nc._MORPHTRI_HUG_NEAR, nc._MORPHTRI_HUG_FAR) == (2.0, 3.0)
    assert nc._MORPHTRI_HUG_LAYER_GAP == 0.75
    monkeypatch.setenv("CBBE2UBE_NO_MORPHTRI_HUG_FEATHER", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MORPHTRI_HUG_FEATHER is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_MORPHTRI_HUG_FEATHER", raising=False)
        importlib.reload(nc)


def test_the_feather_is_full_inside_none_beyond_linear_between():
    f = ncw._hug_feather([0.0, 1.9, 2.0, 2.25, 2.5, 2.75, 3.0, 3.1, 9.0], 2.0, 3.0)
    assert np.allclose(f, [1, 1, 1, 0.75, 0.5, 0.25, 0, 0, 0])


def test_a_degenerate_feather_is_a_hard_cut_not_a_division_by_zero():
    f = ncw._hug_feather([1.0, 2.0, 2.01], 2.0, 2.0)
    assert np.array_equal(f, [1.0, 1.0, 0.0])
    f = ncw._hug_feather([1.0, 2.5], 3.0, 2.0)
    assert np.array_equal(f, [1.0, 1.0])


def test_only_the_shapes_the_opt_out_admitted_are_feathered():
    """The feather must not reach a shape the pass reached anyway (no TRI), nor a
    draping shape the gate still skips, nor anything when the instance keeps
    the gate or passes no feather."""
    with pytest.MonkeyPatch.context() as mp:
        _cs.patch(mp, "_source_morph_tri_shape_names",
                  lambda p: {"armor", "RobesLower"})
        mp.setattr(nc, "MORPHTRI_NO_LEG_GRAFT", True)
        adm = ncw._limb_tri_admitted("src_1.nif", True, (2.0, 3.0), {"RobesLower"})
        assert adm == {"armor"}
        assert ncw._limb_tri_admitted("src_1.nif", False, (2.0, 3.0), set()) == set()
        assert ncw._limb_tri_admitted("src_1.nif", True, (), set()) == set()
        assert ncw._limb_tri_admitted(None, True, (2.0, 3.0), set()) == set()
        mp.setattr(nc, "MORPHTRI_NO_LEG_GRAFT", False)
        assert ncw._limb_tri_admitted("src_1.nif", True, (2.0, 3.0), set()) == set()


def _forwarded(fn, **flags):
    seen = {}

    def fake(dst_path, biped_slots=0, **kw):
        seen.update(kw)
        return 0

    with pytest.MonkeyPatch.context() as mp:
        _cs.patch(mp, "_match_limb_motion_to_body", fake)
        for name, value in flags.items():
            mp.setattr(nc, name, value)
        fn("piece_1.nif")
    assert seen, "the instance never reached the shared pass -- nothing was tested"
    return seen


def test_the_leg_instance_forwards_the_feather_and_the_switch_removes_it():
    on = _forwarded(nc._match_leg_motion_to_body, MATCH_LEG_MOTION=True,
                    MORPHTRI_HUG_FEATHER=True)
    assert on.get("tri_hug") == (nc._MORPHTRI_HUG_NEAR, nc._MORPHTRI_HUG_FAR)
    off = _forwarded(nc._match_leg_motion_to_body, MATCH_LEG_MOTION=True,
                     MORPHTRI_HUG_FEATHER=False)
    assert off.get("tri_hug") == ()


def test_no_other_instance_is_feathered():
    for fn, enable in ((nc._match_spine_motion_to_body, "MATCH_SPINE_MOTION"),
                       (nc._match_arm_motion_to_body, "MATCH_ARM_MOTION"),
                       (nc._match_full_weights_to_body, "MATCH_FULL_WEIGHTS")):
        kw = _forwarded(fn, MORPHTRI_HUG_FEATHER=True, **{enable: True})
        assert not kw.get("tri_hug"), fn.__name__


# --------------------------------------------------------------------------
# end to end: the real pass on a real (tiny) NIF
# --------------------------------------------------------------------------

def _grid(x0, x1, z0, z1, y, n=5):
    xs, zs = np.linspace(x0, x1, n), np.linspace(z0, z1, n)
    verts = [(float(x), float(y), float(z)) for z in zs for x in xs]
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b = r * n + c, r * n + c + 1
            d, e = (r + 1) * n + c, (r + 1) * n + c + 1
            tris += [(a, b, e), (a, e, d)]
    return verts, tris


def _build(path, offsets=(-1.0, -2.5, -4.0), extra=()):
    """BaseShape: a flat patch at y=0 over the leg band, left half on the left
    thigh, right half on the right. Plate: one strip in front of it per offset
    (default 1u, 2.5u and 4u, STACKED, so each outer strip covers the one
    inside it), each Pelvis 0.6 / thighs 0.2 -- a CBBE-style pelvis-heavy row
    the leg match would re-split onto the thigh under it."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    bones = [LT, RT, PV]

    def add(name, verts, tris, weights):
        sh = nif.createShapeFromData(name, verts, tris, [(0.0, 0.0)] * len(verts),
                                     [(0.0, -1.0, 0.0)] * len(verts))
        tb = pyn.TransformBuf()
        tb.set_identity()
        sh.transform = tb
        sh.skin()
        for b in bones:
            sh.add_bone(b)
        idt = pyn.TransformBuf()
        idt.set_identity()
        for b in bones:
            sh.set_skin_to_bone_xform(b, idt)
        for b in bones:
            pairs = [(i, w[b]) for i, w in enumerate(weights) if w.get(b, 0.0) > 0]
            if pairs:
                sh.setShapeWeights(b, pairs)

    # 1u x 2.5u spacing puts a body vertex straight behind every plate vertex,
    # so the pass's nearest-VERTEX distance is exactly the strip's offset.
    bv, bt = _grid(-6.0, 6.0, 40.0, 70.0, 0.0, n=13)
    add("BaseShape", bv, bt,
        [{LT: 1.0} if x < 0 else {RT: 1.0} for x, _y, _z in bv])
    pv, pt = [], []
    for y in offsets:
        v, t = _grid(-4.0, 4.0, 45.0, 65.0, y)
        off = len(pv)
        pv += v
        pt += [(a + off, b + off, c + off) for a, b, c in t]
    add("Plate", pv, pt, [{PV: 0.6, LT: 0.2, RT: 0.2} for _ in pv])
    for spec in extra:                        # other shapes of the piece
        name, y = spec[0], spec[1]
        v, t = _grid(-5.0, 5.0, 42.0, 68.0, y)
        if len(spec) > 2 and spec[2]:         # wound to FACE THE BODY
            t = [(a, c, b) for a, b, c in t]
        add(name, v, t, [{PV: 0.6, LT: 0.2, RT: 0.2} for _ in v])
    nif.save()
    return np.asarray(pv)


def _pelvis(path):
    sh = next(s for s in nc._pynifly().NifFile(filepath=str(path)).shapes
              if s.name == "Plate")
    out = np.zeros(len(sh.verts))
    for i, w in sh.bone_weights.get(PV, []):
        out[i] += w
    return out


def _run(tmp_path, monkeypatch, *, tri_owned, feather, offsets=(-1.0, -2.5, -4.0),
         extra=(), colliders=None):
    p = tmp_path / ("f" if feather else "n") / "piece_1.nif"
    p.parent.mkdir(parents=True)
    pv = _build(p, offsets, extra)
    if colliders is not None:
        _cs.patch(monkeypatch, "_hdt_collider_shape_names",
                  lambda *a, **k: set(colliders))
    monkeypatch.setattr(nc, "MATCH_LEG_MOTION", True)
    monkeypatch.setattr(nc, "LEG_MOTION_ON_MORPHTRI", True)
    monkeypatch.setattr(nc, "MORPHTRI_NO_LEG_GRAFT", True)
    monkeypatch.setattr(nc, "COVERED_SKIN_TARGET", False)
    monkeypatch.setattr(nc, "MORPHTRI_HUG_FEATHER", feather)
    _cs.patch(monkeypatch, "_source_morph_tri_shape_names",
              lambda _p: {"Plate"} if tri_owned else set())
    nc._match_leg_motion_to_body(str(p), 0, src_nif_path=str(tmp_path / "src_1.nif"))
    return pv, _pelvis(p)


pytestmark_e2e = pytest.mark.skipif(not pynifly_available(),
                                    reason="pynifly native lib not available")


@pytestmark_e2e
def test_a_standoff_row_keeps_the_authors_weights(tmp_path, monkeypatch):
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=True)
    near, mid, far = (np.isclose(pv[:, 1], y) for y in (-1.0, -2.5, -4.0))
    assert np.all(pel[near] < 0.05), "a hugging row must still take the full match"
    assert np.allclose(pel[mid], 0.3, atol=0.02), "half-way row takes half the match"
    assert np.allclose(pel[far], 0.6, atol=1e-3), "a standoff row keeps its own weights"


@pytestmark_e2e
def test_a_standoff_row_over_bare_skin_keeps_the_full_match(tmp_path, monkeypatch):
    """The feather is for a plate over ANOTHER layer. A panel that is the only
    layer over the skin -- a lower panel over a swinging thigh -- lost coverage
    in the parent-vs-lane sample without the match, so it keeps it."""
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=True,
                   offsets=(-4.0,))
    assert np.all(pel < 0.05), "nothing under it: the full 9u reach applies"


@pytestmark_e2e
def test_a_collider_named_only_by_the_xml_is_not_a_layer_under_the_plate(
        tmp_path, monkeypatch):
    """The pass hands the piece's XML colliders to the layer set as skips. A
    plate standing over nothing but such a collider is the only VISIBLE layer
    and keeps the full match; the same shape without the XML is a real layer
    under the plate, and the plate is faded."""
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=True,
                   offsets=(-4.0,), extra=(("HipGuard", -1.0),),
                   colliders={"HipGuard"})
    assert np.all(pel < 0.05)


@pytestmark_e2e
def test_a_surface_facing_the_body_under_the_plate_is_not_a_layer(tmp_path, monkeypatch):
    """End to end: the pass gives the layer set the body, so a surface that faces
    the body (a thick part's inside) does not make the plate over it an outer
    layer."""
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=True,
                   offsets=(-4.0,), extra=(("PlateInside", -2.5, True),),
                   colliders=set())
    assert np.all(pel < 0.05)


def test_running_out_of_memory_fades_nothing_and_says_so(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise MemoryError("synthetic")

    monkeypatch.setattr(ncw.fit_metrics, "cast_chunked", boom)
    monkeypatch.setattr(ncw, "_note_pass_failure", lambda where, e: calls.append(where))
    V, T = _grid(-5.0, 5.0, 40.0, 70.0, -0.5, n=11)
    O = np.array([[0.0, -3.0, 55.0]]); P = np.array([[0.0, 0.0, 55.0]])
    got = ncw._rows_covering_a_layer(O, P, np.asarray(V), np.asarray(T), 0.75)
    assert not got.any() and calls == ["_rows_covering_a_layer"]


@pytestmark_e2e
def test_the_same_shape_without_the_xml_is_a_layer(tmp_path, monkeypatch):
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=True,
                   offsets=(-4.0,), extra=(("HipGuard", -1.0),),
                   colliders=set())
    assert np.allclose(pel, 0.6, atol=1e-3)


def test_the_layer_test_sees_a_plate_over_a_sheet_and_not_a_plate_alone():
    V, T = _grid(-5.0, 5.0, 40.0, 70.0, -0.5, n=11)
    Pv, Pt = _grid(-3.0, 3.0, 45.0, 65.0, -3.0, n=7)
    O = np.asarray(Pv)
    P = O.copy(); P[:, 1] = 0.0                       # the body point behind it
    both_V = np.vstack([np.asarray(V), O])
    both_T = np.vstack([np.asarray(T), np.asarray(Pt) + len(V)])
    assert ncw._rows_covering_a_layer(O, P, both_V, both_T, 0.75).all()
    alone = ncw._rows_covering_a_layer(O, P, O, np.asarray(Pt), 0.75)
    assert not alone.any(), "a plate's own triangles are not a layer under it"
    assert not ncw._rows_covering_a_layer(O, P, None, None, 0.75).any()


def test_a_thick_lone_plate_does_not_cover_its_own_back_face():
    """A part thicker than the gap has an inner face that FACES THE BODY. Given
    the body, the layer set keeps only faces pointing away from it, so the plate
    stands over nothing; without the body its back face reads as a layer."""
    from scipy.spatial import cKDTree
    bv, _ = _grid(-6.0, 6.0, 40.0, 70.0, 0.0, n=13)
    outer_v, outer_t = _grid(-3.0, 3.0, 45.0, 65.0, -3.0, n=7)
    inner_v, inner_t = _grid(-3.0, 3.0, 45.0, 65.0, -2.0, n=7)
    inner_t = [(a, c, b) for a, b, c in inner_t]       # wound to face the body

    class S:
        def __init__(self, name, v, t):
            self.name, self.verts, self.tris = name, v, t

    shapes = [S("Plate", outer_v + inner_v,
                outer_t + [(a + len(outer_v), b + len(outer_v), c + len(outer_v))
                           for a, b, c in inner_t])]
    O = np.asarray(outer_v)
    P = O.copy(); P[:, 1] = 0.0
    tree = cKDTree(np.asarray(bv))
    V, T = ncw._layer_soup(shapes, set(), body_tree=tree)
    assert V is None or not ncw._rows_covering_a_layer(O, P, V, T, 0.75).any()
    V0, T0 = ncw._layer_soup(shapes, set())
    assert ncw._rows_covering_a_layer(O, P, V0, T0, 0.75).all(), (
        "control: without the facing test the back face 1u in is hit")


def test_a_layer_closer_than_the_gap_is_the_plates_own_thickness():
    V, T = _grid(-5.0, 5.0, 40.0, 70.0, -2.5, n=11)   # 0.5u under the plate
    Pv, _ = _grid(-3.0, 3.0, 45.0, 65.0, -3.0, n=7)
    O = np.asarray(Pv)
    P = O.copy(); P[:, 1] = 0.0
    assert not ncw._rows_covering_a_layer(O, P, np.asarray(V), np.asarray(T), 0.75).any()


def test_a_layer_behind_the_body_point_is_not_under_the_row():
    """Rays are cast together with one reach, the longest row's. A surface past
    a SHORT row's own body point (inside the body, or on the far side) is within
    that reach but is not between the row and its skin."""
    V, T = _grid(-5.0, 5.0, 40.0, 70.0, 1.5, n=11)    # behind the body (y=0)
    O = np.array([[-2.0, -6.0, 55.0], [2.0, -2.0, 55.0]])
    P = O.copy(); P[:, 1] = 0.0
    got = ncw._rows_covering_a_layer(O, P, np.asarray(V), np.asarray(T), 0.75)
    assert not got.any()


class _Shape:
    def __init__(self, name, y):
        v, t = _grid(-1.0, 1.0, 50.0, 52.0, y, n=3)
        self.name, self.verts, self.tris = name, v, t


def test_the_layers_are_the_visible_garment_surfaces_only():
    """A collider, a proxy or the body is not a layer a plate can cover: a row
    standing over one of those alone is still the only visible layer."""
    # "HipGuard" is a collider only because the piece's physics XML says so: no
    # name key can catch it, so only the XML-derived skip list keeps it out.
    shapes = [_Shape("Armor", -3.0), _Shape("Leggings", -1.0), _Shape("Proxy", -2.0),
              _Shape("Collision", -2.0), _Shape("BaseShape", 0.0),
              _Shape("SkirtStabilizer", -2.0), _Shape("HipGuard", -2.0)]
    V, T = ncw._layer_soup(shapes, {"HipGuard", "BaseShape"})
    assert sorted(set(np.round(V[:, 1], 3))) == [-3.0, -1.0]
    assert len(T) == 2 * len(shapes[0].tris)
    assert ncw._layer_soup([_Shape("Proxy", -2.0)], set()) == (None, None)


@pytestmark_e2e
def test_switched_off_the_standoff_row_is_matched_again(tmp_path, monkeypatch):
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=True, feather=False)
    assert np.all(pel < 0.05), "without the feather every row within 9u is matched"


@pytestmark_e2e
def test_a_shape_without_a_morph_tri_is_not_feathered(tmp_path, monkeypatch):
    pv, pel = _run(tmp_path, monkeypatch, tri_owned=False, feather=True)
    assert np.all(pel < 0.05), "the feather is for the opt-out's population only"
