"""Regression guard for _align_scale_bone_stbs_to_verts (#breast-stb).

The M6 body-blend reskin grafts the UBE body's BODY-SPACE skin-to-bone onto the
3BA scale/morph bones (Breast/Butt/Belly/twist) of armor whose verts live in the
source's SHAPE space (g2s-shifted ~60-65 below body). The engine ignores the
shape-level global_to_skin for skinned UBE armor, so those regions skin ~60 below
their bone -> breast/sleeve "collapse". The fix bakes g2s^-1 into the mismatched
bones' STBs (and leaves the consistent primary bones alone).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402

pyn = nc._pynifly()


def _tb_trans(tz):
    """A pure-translation TransformBuf with z = tz."""
    base = pyn.TransformBuf()
    base.set_identity()
    pm = base.to_matrix()
    M = np.eye(4)
    M[2, 3] = tz
    return pyn.TransformBuf.from_matrix(type(pm)(M.tolist()))


def test_bakes_mismatched_scale_bone_keeps_consistent_primary():
    # verts live in shape space at z=36 (~60 below the body)
    verts = [(0.0, 0.0, 36.0), (1.0, 0.0, 36.0), (0.0, 1.0, 36.0)]
    g2s = _tb_trans(-64.7)
    # breast STB is in BODY space (bone at z~96) -> STB*vert z ~ -60 (mismatch)
    breast = _tb_trans(-96.0)
    # spine STB is consistent with the verts -> STB*vert z ~ +11 (fine)
    spine = _tb_trans(-25.0)
    xforms = {"L Breast01": breast, "NPC Spine2 [Spn2]": spine}
    weights = {
        "L Breast01": [(0, 1.0), (1, 1.0), (2, 1.0)],
        "NPC Spine2 [Spn2]": [(0, 1.0), (1, 1.0), (2, 1.0)],
    }
    new_x, baked = nc._align_scale_bone_stbs_to_verts(xforms, g2s, verts, weights)
    assert baked is True
    # mismatched scale bone got a new (baked) STB
    assert new_x["L Breast01"] is not breast
    # consistent primary bone left untouched (would get WORSE if baked)
    assert new_x["NPC Spine2 [Spn2]"] is spine


def test_noop_on_identity_g2s():
    verts = [(0.0, 0.0, 36.0)]
    g2s = _tb_trans(0.0)
    breast = _tb_trans(-96.0)
    xforms = {"L Breast01": breast}
    weights = {"L Breast01": [(0, 1.0)]}
    new_x, baked = nc._align_scale_bone_stbs_to_verts(xforms, g2s, verts, weights)
    assert baked is False
    assert new_x["L Breast01"] is breast
