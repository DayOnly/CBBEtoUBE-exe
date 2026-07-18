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

"""Guard for the calf/foot-boot far-thigh scale-bone exclusion.

A tall boot whose shaft rides the NPC Thigh rigid bone had the body's FAR THIGH
scale bones (FrontThigh/RearThigh) grafted onto the shaft by the fine-animation
reskin; on a UBE actor that makes the whole boot FADE OUT at camera distance
(verified on real tall boots 2026-07-01). Fix: for foot-slot (37) calf/foot-dominant
footwear, drop the far-thigh scale bones from the graft while KEEPING RearCalf
(the calf size morph). Dropping the thighs must NOT drop RearCalf -- measured on
the real boot, it frees top-4-per-vert budget so RearCalf reaches MORE verts.
"""
import numpy as np

from src import nif_convert as nc

BODY_W = 0.16
REACH = nc.SCALE_BONE_REACH_HANDS_FEET   # 8.0


class _FakeBody:
    def __init__(self, verts, bone_names, bone_weights):
        self.verts = np.asarray(verts, dtype=np.float64)
        self.bone_names = list(bone_names)
        self.bone_weights = bone_weights

    def get_shape_skin_to_bone(self, bn):
        return None


class _FakeShape:
    """Minimal src_shape for _boot_far_thigh_scale_exclusions (needs verts +
    bone_weights to judge thigh dominance)."""
    def __init__(self, n, bone_weights):
        self.verts = np.zeros((n, 3), dtype=np.float64)
        self.bone_names = list(bone_weights)
        self.bone_weights = bone_weights


def _body():
    # thigh-region body vert (z=70) carries the far-thigh scale bones;
    # calf-region body vert (z=40) carries RearCalf.
    return _FakeBody(
        [[0, 0, 70], [0, 0, 40]],
        ["NPC L FrontThigh", "NPC L RearThigh", "NPC L RearCalf [LrClf]"],
        {
            "NPC L FrontThigh": [(0, BODY_W)],
            "NPC L RearThigh": [(0, BODY_W)],
            "NPC L RearCalf [LrClf]": [(1, BODY_W)],
        },
    )


def _boot_armor():
    # A calf/foot-dominant boot: shaft vert on the thigh (z70), calf verts (z40),
    # foot/toe verts low. Regular skinning only. thigh-dominant frac = 1/5 = 0.2.
    verts = np.array(
        [[0, 0, 70], [0, 0, 40], [0, 0, 38], [0, 0, 5], [0, 0, 2]],
        dtype=np.float64)
    weights = {
        "NPC L Thigh [LThg]": [(0, 1.0)],
        "NPC L Calf [LClf]": [(1, 1.0), (2, 1.0)],
        "NPC L Foot [Lft ]": [(3, 1.0)],
        "NPC L Toe0 [LToe]": [(4, 1.0)],
    }
    return verts, weights, _FakeShape(5, weights)


def _run(slots):
    verts, weights, shape = _boot_armor()
    excl = nc._boot_far_thigh_scale_exclusions(shape, slots)
    nc._SCALE_BONE_DATA_CACHE.clear()
    b, x, w = nc.add_scale_bone_weights(
        list(weights), {}, {k: list(v) for k, v in weights.items()},
        verts, _body(),
        reach=REACH,
        max_transfer=nc.SCALE_BONE_MAX_TRANSFER_HANDS_FEET,
        leg_region_only=True,
        exclude_scale_bone_substrings=excl,
    )
    nc._SCALE_BONE_DATA_CACHE.clear()
    return b, w, excl


def _has_far_thigh(bones):
    return any(("frontthigh" in b.lower() or "rearthigh" in b.lower())
               for b in bones)


def _rearcalf_total(weights_map):
    return sum(wt for bn, prs in weights_map.items()
               if "rearcalf" in bn.lower() for _, wt in prs)


def test_baseline_grafts_far_thigh():
    # No foot slot -> exclusions empty -> the far-thigh scale bones are grafted
    # (this is the fade-inducing behaviour the fix targets).
    bones, weights, excl = _run(0)
    assert excl == ()
    assert _has_far_thigh(bones), "baseline should graft FrontThigh/RearThigh"


def test_foot_boot_drops_far_thigh_keeps_rearcalf():
    bones, weights, excl = _run(nc.BIPED_SLOT37_BIT)
    assert excl == nc.BOOT_FAR_THIGH_SCALE_SUBSTRINGS
    assert not _has_far_thigh(bones), "far-thigh scale bones must be excluded"
    # RearCalf (the calf morph) must SURVIVE.
    assert any("rearcalf" in b.lower() for b in bones), "RearCalf was dropped!"
    assert _rearcalf_total(weights) > 0.0


def test_rearcalf_not_weakened_by_thigh_exclusion():
    # Dropping the competing thigh scale bones must not reduce RearCalf tracking.
    _, w_base, _ = _run(0)
    _, w_fix, _ = _run(nc.BIPED_SLOT37_BIT)
    assert _rearcalf_total(w_fix) >= _rearcalf_total(w_base) - 1e-9


def test_thigh_high_boot_keeps_thigh_morph():
    # A thigh-DOMINANT boot (really covers the thigh) must keep the thigh morph.
    n = 5
    weights = {"NPC L Thigh [LThg]": [(i, 1.0) for i in range(4)],  # 4/5 thigh
               "NPC L Calf [LClf]": [(4, 1.0)]}
    shape = _FakeShape(n, weights)
    assert nc._boot_far_thigh_scale_exclusions(shape, nc.BIPED_SLOT37_BIT) == ()


def test_gate_only_fires_on_foot_slot():
    _, _, shape = _boot_armor()
    # body slot (32) / hands slot (33) must NOT trigger the boot exclusion.
    assert nc._boot_far_thigh_scale_exclusions(shape, nc.BIPED_SLOT32_BIT) == ()
    assert nc._boot_far_thigh_scale_exclusions(shape, nc.BIPED_SLOT33_BIT) == ()
    assert (nc._boot_far_thigh_scale_exclusions(shape, nc.BIPED_SLOT37_BIT)
            == nc.BOOT_FAR_THIGH_SCALE_SUBSTRINGS)


def test_env_revert_disables_exclusion(monkeypatch):
    _, _, shape = _boot_armor()
    monkeypatch.setattr(nc, "EXCLUDE_BOOT_FAR_THIGH_SCALE", False)
    assert nc._boot_far_thigh_scale_exclusions(shape, nc.BIPED_SLOT37_BIT) == ()
