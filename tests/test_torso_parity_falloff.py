"""Guard for the torso-parity scale-bone falloff (#175 chest/nipple, #129 butt).
A body-slot plate that sits a few units off the body was tracking the live
breast/belly/butt morph at only ~0.38 of the body (linear distance falloff) and
the body poked through. With torso_parity=True the chest/belly/butt bones use a
steeper power falloff so the plate tracks near parity -- but NEVER past the
body's own weight (no ballooning), and non-torso bones / the torso_parity=False
path stay exactly as before (arm bracers #133 untouched)."""
import numpy as np
import pytest
from src.nif_convert import add_scale_bone_weights, SCALE_BONE_REACH

BODY_W = 0.16            # body's per-vert weight on the scale bone
STANDOFF = 7.0           # plate sits 7u off the body surface


class _FakeBody:
    def __init__(self, verts, bone_names, bone_weights):
        self.verts = np.asarray(verts, dtype=np.float64)
        self.bone_names = list(bone_names)
        self.bone_weights = bone_weights

    def get_shape_skin_to_bone(self, bn):
        return None


def _run(torso_parity):
    # Body: a breast-weighted vert at Z=90 and a thigh-weighted vert at Z=50.
    body = _FakeBody(
        [[0, 0, 90], [0, 0, 50]],
        ["NPC L Breast01", "NPC L Thigh"],
        {"NPC L Breast01": [(0, BODY_W)], "NPC L Thigh": [(1, BODY_W)]},
    )
    # Armor: vert0 = 7u off the breast vert; vert1 = 7u off the thigh vert;
    # vert2 = 20u off the breast vert (beyond the 12u reach).
    armor_verts = np.array(
        [[0, 0, 90 + STANDOFF], [0, 0, 50 + STANDOFF], [0, 0, 110]],
        dtype=np.float64)
    weights = {"ArmorPlate": [(0, 1.0), (1, 1.0), (2, 1.0)]}
    return add_scale_bone_weights(
        ["ArmorPlate"], {}, weights, armor_verts, body,
        reach=SCALE_BONE_REACH, torso_parity=torso_parity)


def _w(weights_map, bone, vi):
    for i, x in weights_map.get(bone, []):
        if i == vi:
            return x
    return 0.0


def test_torso_parity_boosts_breast_tracking():
    _, _, off = _run(False)
    _, _, on = _run(True)
    breast_off = _w(off, "NPC L Breast01", 0)
    breast_on = _w(on, "NPC L Breast01", 0)
    # linear baseline ~ BODY_W * (1 - 7/12) = 0.0667
    assert breast_off == pytest.approx(0.0667, abs=0.01), breast_off
    # boosted: substantially higher (power P=4 -> ~0.142)
    assert breast_on > breast_off + 0.05, (breast_off, breast_on)
    # but NEVER past the body's own weight -> armor can't grow past the body
    assert breast_on <= BODY_W + 1e-6, breast_on


def test_non_torso_bone_unchanged():
    _, _, off = _run(False)
    _, _, on = _run(True)
    # the thigh bone is NOT a torso-parity bone -> identical with/without boost
    assert _w(on, "NPC L Thigh", 1) == pytest.approx(_w(off, "NPC L Thigh", 1))


def test_far_vert_gets_no_weight_either_way():
    _, _, off = _run(False)
    _, _, on = _run(True)
    # vert2 is 20u from the breast vert (> 12u reach) -> zero both ways
    assert _w(off, "NPC L Breast01", 2) == pytest.approx(0.0)
    assert _w(on, "NPC L Breast01", 2) == pytest.approx(0.0)


def test_arm_dominated_vert_gets_no_torso_scale():
    # #133: a vert whose AUTHORED skinning is dominated by an ARM bone (sleeve /
    # bracer geometry) must get ZERO breast/belly/butt scale weight, even within
    # reach of a butt-weighted body vert with torso_parity on -- an arm tracking
    # the butt is cross-talk. (Stronger than the earlier 'keep linear' rule: the
    # weight is now suppressed entirely, not just left un-boosted.)
    body = _FakeBody(
        [[0, 0, 75]], ["NPC L Butt"], {"NPC L Butt": [(0, BODY_W)]})
    av = np.array([[0, 0, 75 + STANDOFF]], dtype=np.float64)   # 7u off the butt
    weights = {"NPC L Forearm": [(0, 0.8)], "ArmorPlate": [(0, 0.2)]}  # arm-dominated
    _, _, wm = add_scale_bone_weights(
        ["NPC L Forearm", "ArmorPlate"], {}, weights, av, body,
        reach=SCALE_BONE_REACH, torso_parity=True)
    assert _w(wm, "NPC L Butt", 0) == pytest.approx(0.0), wm


def test_133_arm_vert_never_gets_torso_scale_on_cloth_path():
    # #133 Valenwood bracer: a forearm-dominated bracer vert within reach of a
    # butt-weighted body vert must get ZERO butt scale weight -- even on the
    # cloth path (torso_parity=False), which is how a slot-34 bracer routes.
    body = _FakeBody([[0, 0, 75]], ["NPC L Butt"], {"NPC L Butt": [(0, BODY_W)]})
    av = np.array([[0, 0, 75 + STANDOFF]], dtype=np.float64)   # 7u off the butt
    weights = {"NPC L Forearm": [(0, 0.9)], "ArmorPlate": [(0, 0.1)]}  # arm-dominated
    _, _, wm = add_scale_bone_weights(
        ["NPC L Forearm", "ArmorPlate"], {}, weights, av, body,
        reach=SCALE_BONE_REACH, torso_parity=False)
    assert _w(wm, "NPC L Butt", 0) == pytest.approx(0.0), wm


def test_133_non_arm_vert_still_tracks_torso():
    # Control: a spine-dominated (torso) vert in the SAME spot DOES inherit butt
    # weight -- the suppression is arm-specific, not a blanket cutoff.
    body = _FakeBody([[0, 0, 75]], ["NPC L Butt"], {"NPC L Butt": [(0, BODY_W)]})
    av = np.array([[0, 0, 75 + STANDOFF]], dtype=np.float64)
    weights = {"NPC Spine2": [(0, 0.9)], "ArmorPlate": [(0, 0.1)]}  # torso-dominated
    _, _, wm = add_scale_bone_weights(
        ["NPC Spine2", "ArmorPlate"], {}, weights, av, body,
        reach=SCALE_BONE_REACH, torso_parity=False)
    assert _w(wm, "NPC L Butt", 0) > 0.02, wm


def test_default_is_linear_no_change():
    # torso_parity defaults False -> behavior identical to the explicit-False run
    body = _FakeBody(
        [[0, 0, 90]], ["NPC L Breast01"], {"NPC L Breast01": [(0, BODY_W)]})
    av = np.array([[0, 0, 90 + STANDOFF]], dtype=np.float64)
    _, _, dflt = add_scale_bone_weights(
        ["ArmorPlate"], {}, {"ArmorPlate": [(0, 1.0)]}, av, body,
        reach=SCALE_BONE_REACH)
    assert _w(dflt, "NPC L Breast01", 0) == pytest.approx(0.0667, abs=0.01)
