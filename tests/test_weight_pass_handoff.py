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

"""The weight passes around #gap-aware-follow hand off instead of fighting it.

Measured on 40 pieces re-converted from source, posed (breast/butt/belly
bounce, stride, lean):
  * #gap-aware-cover-side -- the pooled crotch target crossed the midline and
    gave a vert over left-thigh skin 0.31 of the RIGHT thigh (a cuirass 1 -> 22
    stride pokes, a tunic 4 -> 12);
  * #author-pull-skin-lock -- #part-pair-align and #author-deviation-skin pulled
    verts locked to the skin back toward the author's rows (a dress 6 -> 60
    pokes, a layered torso 23 -> 65);
  * #full-weight-gap-ramp -- the full-vector match wrote the body's row at full
    strength inside a hard 5u box, and the step where gap-aware's 1u -> 4u ramp
    ends cost 1437 creases and 437 stretched edges for no poke."""
import numpy as np

import src.nif_convert as nc
from src import nif_convert_weights as nw

LT, RT, PELVIS = "NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]"


# --- #gap-aware-cover-side -------------------------------------------------

def test_one_sided_skin_excludes_the_other_side():
    row = {LT: 0.84, PELVIS: 0.10, "NPC L RearThigh": 0.06}
    assert nw._cover_side_excluded(row, {"L"}) == {"R"}
    assert nw._cover_side_excluded({RT: 0.9, PELVIS: 0.1}, set()) == {"L"}


def test_unsided_or_two_sided_skin_keeps_the_whole_pool():
    # Over the pelvis cleft: the skin row is unsided, or the faces cover skin
    # on both sides or unsided skin ("M") -- the two-sided pool stays.
    assert nw._cover_side_excluded({PELVIS: 1.0}, set()) == set()
    assert nw._cover_side_excluded({LT: 0.9, PELVIS: 0.1}, {"M"}) == set()
    assert nw._cover_side_excluded({LT: 0.9, PELVIS: 0.1}, {"R"}) == set()
    assert nw._cover_side_excluded({LT: 0.5, RT: 0.5}, set()) == set()


def test_cover_side_switch_off_excludes_nothing(monkeypatch):
    monkeypatch.setattr(nc, "GAF_COVER_SIDE", False)
    assert nw._cover_side_excluded({LT: 0.84, PELVIS: 0.1}, {"L"}) == set()


def test_a_trace_weight_does_not_make_skin_sided():
    assert nw._row_sides({PELVIS: 0.97, LT: 0.03}) == set()


# --- #full-weight-gap-ramp ---------------------------------------------------

def test_full_weight_strength_follows_the_gap_aware_ramp():
    t, l_ = float(nc._GAF_TIGHT), float(nc._GAF_LOOSE)
    st = nw._full_weight_strength([t - 0.5, (t + l_) / 2, l_ + 0.5], 1.0)
    assert st[0] == 1.0                 # hugging: the whole row, as before
    assert 0.0 < st[1] < 1.0            # graded, never switched
    assert st[2] == 0.0                 # past the ramp: no step left behind
    d = np.linspace(0.0, l_ + 1.0, 50)
    assert (np.diff(nw._full_weight_strength(d, 1.0)) <= 1e-12).all()


def test_full_weight_strength_scales_the_knob():
    st = nw._full_weight_strength([0.0, 9.0], 0.6)
    assert np.allclose(st, [0.6, 0.0])


def test_full_weight_ramp_switch_off_is_flat(monkeypatch):
    monkeypatch.setattr(nc, "FULL_WEIGHT_GAP_RAMP", False)
    assert np.allclose(nw._full_weight_strength([0.0, 3.0, 4.9], 1.0), 1.0)


# --- #author-pull-skin-lock --------------------------------------------------

CENTRE = (0.0, 0.0, 95.0)


def _body_nif(path):
    from tests.synthetic_nif import build_skinned_shapes_nif, uv_sphere
    bv, bt, bn = uv_sphere(8.0, centre=CENTRE)
    return build_skinned_shapes_nif(
        path, [("BaseShape", bv, bt, bn, ("NPC Spine2 [Spn2]", "L Breast02"))])


def _ent(r, rows=None):
    from tests.synthetic_nif import uv_sphere
    v, _, _ = uv_sphere(r, centre=CENTRE)
    v = np.asarray(v, np.float64)
    return {"wv": v, "rows": rows or [{"NPC Spine2 [Spn2]": 1.0}] * len(v)}


def test_skin_lock_is_one_on_the_skin_and_zero_with_room(tmp_path):
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _body_nif(tmp_path / "meshes" / "x" / "piece_1.nif")
    nf = nc._pynifly().NifFile(filepath=str(f))
    tight, mid, loose = (_ent(8.3), _ent(10.5), _ent(12.5))
    lk = nw._skin_lock_for_ents(nf, [tight, mid, loose], f,
                                biped_slots=nc.BIPED_SLOT32_BIT)
    assert lk[0].min() > 0.99
    assert 0.05 < lk[1].mean() < 0.95
    assert lk[2].max() == 0.0


def test_skin_lock_frees_simulated_verts(tmp_path):
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _body_nif(tmp_path / "meshes" / "x" / "piece_1.nif")
    nf = nc._pynifly().NifFile(filepath=str(f))
    e = _ent(8.3)
    e["rows"] = [{"Cloth_01": 0.8, "NPC Spine2 [Spn2]": 0.2}] * len(e["wv"])
    lk = nw._skin_lock_for_ents(nf, [e], f, biped_slots=nc.BIPED_SLOT32_BIT)
    assert lk[0].max() == 0.0


def test_skin_lock_switch_off_locks_nothing(monkeypatch, tmp_path):
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    monkeypatch.setattr(nc, "AUTHOR_PULL_SKIN_LOCK", False)
    f = _body_nif(tmp_path / "meshes" / "x" / "piece_1.nif")
    nf = nc._pynifly().NifFile(filepath=str(f))
    lk = nw._skin_lock_for_ents(nf, [_ent(8.3)], f,
                                biped_slots=nc.BIPED_SLOT32_BIT)
    assert lk[0].max() == 0.0


def test_no_skin_lock_where_gap_aware_had_no_skin(tmp_path):
    # A body-slot piece with no body copy: gap-aware followed nothing there,
    # so nothing is locked.
    from tests.synthetic_nif import build_skinned_shapes_nif, pynifly_available, uv_sphere
    if not pynifly_available():
        return
    av, at, an = uv_sphere(8.3, centre=CENTRE)
    f = build_skinned_shapes_nif(tmp_path / "meshes" / "x" / "piece_1.nif",
                                 [("Armour", av, at, an, ("NPC Spine2 [Spn2]", "NPC Spine1 [Spn1]"))])
    nf = nc._pynifly().NifFile(filepath=str(f))
    lk = nw._skin_lock_for_ents(nf, [_ent(8.3)], f,
                                biped_slots=nc.BIPED_SLOT32_BIT)
    assert lk[0].max() == 0.0


# --- ordering: the SMP hold keeps the last word over gap-aware ---------------

def test_smp_hold_runs_after_gap_aware_at_both_sites():
    # Scanned per converter module (split-safe), not as one module's source.
    from tests import _converter_sources as _cs
    totals = [0, 0, 0]
    for _path, src in _cs.texts().items():
        gaf = [i for i in range(len(src)) if src.startswith("_gap_aware_follow(dst_path", i)
                and src[max(i - 4, 0):i] != "def "]
        hold = [i for i in range(len(src))
                if src.startswith("_hold_weights_at_smp_boundary(dst_path", i)
                and src[max(i - 4, 0):i] != "def "]
        coin = [i for i in range(len(src))
                if src.startswith("_match_coincident_cross_shape_skin(dst_path", i)
                and src[max(i - 4, 0):i] != "def "]
        assert len(gaf) == len(hold) == len(coin), _path
        for g, h, c in zip(gaf, hold, coin):
            assert g < h < c, _path
        totals = [totals[0] + len(gaf), totals[1] + len(hold), totals[2] + len(coin)]
    assert totals == [2, 2, 2]


# --- one lock per part; the draping exemption --------------------------------

def test_part_lock_is_the_tightest_verts():
    lock = np.array([0.0, 0.2, 0.9, 0.4])
    assert nw._part_lock(lock, [0, 1, 3]) == 0.4
    assert nw._part_lock(lock, [1, 2]) == 0.9
    assert nw._part_lock(lock, []) == 0.0


def test_part_pair_shift_uses_one_lock_per_part():
    # A per-vert lock bent a part that is tight at one end: the shift must stay
    # the uniform additive move the step is built on.
    import inspect
    src = inspect.getsource(nw._match_coincident_cross_shape_skin)
    assert "_plk = _part_lock(_lock[kk], idxk)" in src
    assert "s * (1.0 - _plk) *" in src


def test_draping_shapes_keep_the_covered_skin_exemption():
    keys = nc._CONFORM_SKIP_DRAPING
    assert nw._drape_exempt("ThalmorRobesF", keys)
    assert nw._drape_exempt("1Dress_low", keys)
    assert not nw._drape_exempt("Cuirass", keys)
    assert not nw._drape_exempt("Collar", keys)     # 'col' is structural, not draping


def test_gap_aware_runs_with_the_cover_side_switch_off(monkeypatch, tmp_path):
    # The switch-off path is what the golden off-switch check runs; it once
    # indexed the side table it had not built and the whole pass died.
    from tests.synthetic_nif import build_skinned_shapes_nif, pynifly_available, uv_sphere
    if not pynifly_available():
        return
    monkeypatch.setattr(nc, "GAF_COVER_SIDE", False)
    c = (0.0, 0.0, 62.0)                     # inside the covered-skin band
    av, at, an = uv_sphere(8.3, centre=c)
    bv, bt, bn = uv_sphere(8.0, centre=c)
    f = build_skinned_shapes_nif(tmp_path / "meshes" / "x" / "piece_1.nif", [
        ("Armour", av, at, an, ("NPC Spine2 [Spn2]", "NPC Spine1 [Spn1]")),
        # shared bones (a body-only bone needs the actor skeleton to anchor),
        # split the other way round so the skin rows differ
        ("BaseShape", bv, bt, bn, ("NPC Spine1 [Spn1]", "NPC Spine2 [Spn2]"))])
    fails = []
    monkeypatch.setattr(nw, "_note_pass_failure", lambda *a, **k: fails.append(a))
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) > 0
    assert not fails


# --- #cover-band-feather -------------------------------------------------------

def test_cover_band_weight_is_full_inside_and_zero_at_the_wall():
    w = float(nc._GAF_SMOOTH)
    mid_z = (nc._COVER_Z_LO + nc._COVER_Z_HI) / 2.0
    assert nw._cover_band_weight((0.0, 0.0, mid_z)) == 1.0
    assert nw._cover_band_weight((0.0, 0.0, nc._COVER_Z_LO)) == 0.0
    assert nw._cover_band_weight((nc._COVER_X, 0.0, mid_z)) == 0.0
    assert 0.0 < nw._cover_band_weight((0.0, 0.0, nc._COVER_Z_LO + w / 2)) < 1.0
    assert 0.0 < nw._cover_band_weight((nc._COVER_X - w / 2, 0.0, mid_z)) < 1.0
    zs = np.linspace(nc._COVER_Z_LO - 1.0, mid_z, 40)
    ws = [nw._cover_band_weight((0.0, 0.0, z)) for z in zs]
    assert all(b >= a - 1e-12 for a, b in zip(ws, ws[1:]))     # no step, no dip


# --- #gap-aware-face-cover -------------------------------------------------------

def test_face_cover_taken_where_the_covered_skin_carries_more_jiggle():
    closest = {"NPC Spine2 [Spn2]": 0.9, "L Breast01": 0.1}
    acc = {"NPC Spine2 [Spn2]": 1.2, "L Breast01": 0.8}          # over 2.0 hits
    row = nw._face_cover_row(acc, 2.0, closest)
    assert row is not None and abs(row["L Breast01"] - 0.4) < 1e-9


def test_face_cover_left_alone_off_a_jiggle_feature():
    closest = {"NPC Spine2 [Spn2]": 0.5, "NPC Spine1 [Spn1]": 0.5}
    acc = {"NPC Spine2 [Spn2]": 1.4, "NPC Spine1 [Spn1]": 0.6}
    assert nw._face_cover_row(acc, 2.0, closest) is None        # no jiggle gain
    assert nw._face_cover_row({"L Breast01": 0.5}, 0.5, closest) is None  # too little cover


# --- #family-surface-target -------------------------------------------------------

def test_surface_rows_blend_the_triangle_not_the_nearest_vertex():
    body_pv = [{"NPC L Thigh [LThg]": 1.0}, {"NPC Pelvis [Pelv]": 1.0},
               {"NPC Pelvis [Pelv]": 0.5, "NPC L Thigh [LThg]": 0.5}]
    bones = ["NPC L Thigh [LThg]", "NPC Pelvis [Pelv]"]
    rows = nw._surface_rows([[0.5, 0.5, 0.0], [0.0, 0.0, 1.0]],
                            [[0, 1, 2], [0, 1, 2]], body_pv, bones)
    assert np.allclose(rows[0], [0.5, 0.5])      # mid-edge: half of each corner
    assert np.allclose(rows[1], [0.5, 0.5])      # on vertex 2: its own row
    rows = nw._surface_rows([[0.8, 0.2, 0.0]], [[0, 1, 2]], body_pv, bones)
    assert np.allclose(rows[0], [0.8, 0.2])      # continuous, no snap to vertex 0


# --- #family-breast-keep ----------------------------------------------------------

def test_family_push_up_is_not_funded_from_the_breast():
    # unmanaged: [L Breast02, NPC Pelvis]; the managed share rose, 0.5 is left
    Go = np.array([[0.3, 0.4]])
    out = nw._fund_unmanaged(Go, [0.5], [True, False])
    assert np.allclose(out, [[0.3, 0.2]])            # breast kept, pelvis absorbs
    out = nw._fund_unmanaged(Go, [0.2], [True, False])
    assert np.allclose(out, [[0.2, 0.0]])            # breast capped at what is left


def test_family_funding_without_a_breast_bone_is_the_uniform_rescale():
    Go = np.array([[0.3, 0.1]])
    assert np.allclose(nw._fund_unmanaged(Go, [0.2], [False, False]), [[0.15, 0.05]])
    # only breast to take it from: falls back to the uniform rescale
    assert np.allclose(nw._fund_unmanaged(np.array([[0.4, 0.0]]), [0.6], [True, False]), [[0.6, 0.0]])
