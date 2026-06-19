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

"""Offline jiggle-clip predictor math. No pynifly / no game -- pure arrays, so
the worst-case kinematic model is provable by construction."""
import numpy as np

from src.jiggle_predict import (
    DEFAULT_EXCURSION,
    calibrate_excursion,
    excursion_for_bone,
    is_jiggle_bone,
    jiggle_field,
    predict_clip,
    summarize,
)

E = DEFAULT_EXCURSION  # breast 2.0, glute/butt 1.5, belly 1.0


def test_is_jiggle_bone_and_excursion_scalar():
    assert is_jiggle_bone("NPC L Breast")
    assert is_jiggle_bone("NPC L Butt")
    assert is_jiggle_bone("HDT Belly")
    assert not is_jiggle_bone("NPC Spine2")
    assert not is_jiggle_bone("NPC L Forearm")
    assert excursion_for_bone("NPC L Breast") == 2.0
    assert excursion_for_bone("NPC Belly") == 1.0
    assert excursion_for_bone("NPC Spine2") == 0.0
    # a name matching two zones takes the worst-case (largest)
    assert excursion_for_bone("Breast Belly Hybrid") == 2.0


def test_jiggle_field_sums_weight_times_excursion_and_is_nonneg():
    bw = {
        "NPC L Breast": [(0, 1.0)],
        "NPC Belly": [(1, 0.5)],
        "NPC Spine2": [(2, 1.0)],   # non-jiggle -> contributes nothing
    }
    f = jiggle_field(bw, 3)
    assert np.isclose(f[0], 2.0)   # 1.0 * E_breast
    assert np.isclose(f[1], 0.5)   # 0.5 * E_belly
    assert np.isclose(f[2], 0.0)   # non-jiggle bone
    # non-negativity is the safety invariant for folding via np.maximum into the
    # existing morph-amplitude map: the fold can never DECREASE clearance.
    assert (f >= 0.0).all()
    morph_amp = np.array([0.3, 0.9, 0.1])
    folded = np.maximum(morph_amp, f)
    assert (folded >= morph_amp).all()


def _body():
    # 3 body verts on a line, all facing +X (outward normal +X).
    bv = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 10.0], [0.0, 0.0, 20.0]])
    bn = np.array([[1.0, 0.0, 0.0]] * 3)
    bw = {"NPC L Breast": [(0, 1.0)]}   # only vert 0 jiggles (breast)
    field = jiggle_field(bw, 3)         # [2.0, 0.0, 0.0]
    return bv, bn, field


def test_garment_that_follows_jiggle_and_has_standoff_does_not_clip():
    bv, bn, field = _body()
    # garment vert 0.5u outside body vert 0, fully breast-weighted -> moves WITH
    # the breast, plus it has standoff. body_exc 2.0 - garment_exc 2.0 - 0.5 < 0.
    gv = np.array([[0.5, 0.0, 0.0]])
    gw = {"NPC L Breast": [(0, 1.0)]}
    pred = predict_clip(gv, gw, bv, bn, field)
    assert np.isclose(pred["standoff"][0], 0.5)
    assert np.isclose(pred["body_exc"][0], 2.0)
    assert np.isclose(pred["garment_exc"][0], 2.0)
    assert pred["margin"][0] < 0
    assert summarize(pred)["n_clip"] == 0


def test_underweighted_garment_clips_in_jiggle_bucket_as_underweight():
    bv, bn, field = _body()
    # clear at rest (standoff +0.5) but carries NO jiggle weight -> it stays put
    # while the breast surface swings out 2.0u: margin = 2.0 - 0 - 0.5 > 0, and
    # standoff >= 0 so it is a JIGGLE clip, classified under-weight.
    gv = np.array([[0.5, 0.0, 0.0]])
    gw = {"NPC Spine2": [(0, 1.0)]}
    pred = predict_clip(gv, gw, bv, bn, field)
    assert np.isclose(pred["garment_exc"][0], 0.0)
    assert np.isclose(pred["margin"][0], 1.5)
    s = summarize(pred)
    assert s["n_jiggle"] == 1 and s["n_static"] == 0
    assert s["cause"] == "under-weight"


def test_partially_following_garment_too_close_is_jiggle_standoff_tight():
    bv, bn, field = _body()
    # follows jiggle 75% (breast=0.75 -> garment_exc 1.5) and sits +0.2u out:
    # margin = body 2.0 - garment 1.5 - 0.2 = +0.3, standoff >= 0 -> JIGGLE,
    # and garment_exc 1.5 >= 0.5*body 1.0 -> standoff-tight, not under-weight.
    gv = np.array([[0.2, 0.0, 0.0]])
    gw = {"NPC L Breast": [(0, 0.75)]}
    pred = predict_clip(gv, gw, bv, bn, field)
    assert np.isclose(pred["garment_exc"][0], 1.5)
    assert np.isclose(pred["margin"][0], 0.3)
    s = summarize(pred)
    assert s["n_jiggle"] == 1 and s["n_static"] == 0
    assert s["cause"] == "standoff-tight"


def test_rest_penetration_is_static_not_jiggle():
    bv, bn, field = _body()
    # 0.2u INSIDE the body at rest (standoff -0.2): a STATIC clip regardless of
    # jiggle -- must NOT be counted as a jiggle motion-clip.
    gv = np.array([[-0.2, 0.0, 0.0]])
    gw = {"NPC L Breast": [(0, 1.0)]}
    pred = predict_clip(gv, gw, bv, bn, field)
    assert np.isclose(pred["standoff"][0], -0.2)
    s = summarize(pred)
    assert s["n_static"] == 1 and s["n_jiggle"] == 0
    assert s["cause"] == "static-only"


def test_far_drape_verts_are_excluded():
    bv, bn, field = _body()
    gv = np.array([[50.0, 0.0, 0.0]])   # 50u from the body -> loose drape
    gw = {"NPC L Breast": [(0, 1.0)]}
    pred = predict_clip(gv, gw, bv, bn, field, max_body_dist=10.0)
    assert not np.isfinite(pred["margin"][0])
    assert summarize(pred)["n_eval"] == 0


def test_calibrate_excursion_backs_out_E_from_one_capture():
    assert calibrate_excursion(3.0, 1.0) == 3.0
    assert calibrate_excursion(2.0, 0.5) == 4.0   # half-weight apex -> 2x E
    assert calibrate_excursion(2.0, 0.0) == 0.0   # guard against div-by-zero


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} jiggle-predict tests passed")
    sys.exit(0)
