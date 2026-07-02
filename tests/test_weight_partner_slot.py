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

"""Guard for the weight-agnostic slot fold (#slot0-weight-partner).

An ARMA names the `_1` mesh; the engine derives `_0` from it, so the NIF->slot
map only has the `_1` key. Without folding, the `_0` weight file converts with
biped_slots=0 -> every slot-gated path (torso_parity, slot-aware inflation /
reskin band / scale reach, the calf/foot-boot far-thigh exclusion) misfires on
`_0` while `_1` is correct, so the two weights convert DIFFERENTLY (observed:
GTO boots_0 kept the fade-inducing far-thigh scale bones, boots_1 did not).
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.auto_convert import (
    _weight_agnostic_slot_map, _weight_base_key, _make_slot_resolver,
    _scale_bone_vert_counts, _weight_partner_scale_divergence,
)

SLOT37 = 1 << 7   # feet


class _FakeShape:
    def __init__(self, name, bone_weights):
        self.name = name
        self.bone_names = list(bone_weights)
        self.bone_weights = bone_weights


def _slot_for(slot_map, agnostic, rel):
    # Mirrors the work-item lookup in convert(): exact key, else weight-base.
    return (slot_map.get(rel.lower(), 0)
            or agnostic.get(_weight_base_key(rel), 0))


def test_zero_partner_recovers_slot_from_one():
    # The ARMA-derived map only has the _1 mesh.
    slot_map = {"meshes/girl's travel outfit/boots_1.nif": SLOT37}
    agnostic = _weight_agnostic_slot_map(slot_map)
    one = _slot_for(slot_map, agnostic, "meshes/Girl's Travel Outfit/boots_1.nif")
    zero = _slot_for(slot_map, agnostic, "meshes/Girl's Travel Outfit/boots_0.nif")
    assert one & SLOT37
    assert zero & SLOT37, "boots_0 must inherit the foot slot from its _1 partner"
    assert zero == one, "both weights must convert with identical slot bits"


def test_fold_ors_partner_bits():
    slot_map = {"a/x_0.nif": 0x4, "a/x_1.nif": 0x80}
    agnostic = _weight_agnostic_slot_map(slot_map)
    assert agnostic[_weight_base_key("a/x_0.nif")] == (0x4 | 0x80)


def test_exact_hit_still_wins_and_agnostic_is_fallback():
    slot_map = {"a/y_1.nif": SLOT37}
    agnostic = _weight_agnostic_slot_map(slot_map)
    # exact key present -> used directly
    assert _slot_for(slot_map, agnostic, "a/y_1.nif") == SLOT37
    # unrelated mesh -> no bits
    assert _slot_for(slot_map, agnostic, "a/unrelated_0.nif") == 0


def test_empty_map_safe():
    assert _weight_agnostic_slot_map({}) == {}
    assert _weight_agnostic_slot_map(None) == {}


def test_resolver_matches_manual_lookup():
    slot_map = {"a/z_1.nif": SLOT37}
    resolve = _make_slot_resolver(slot_map)
    assert resolve("a/z_1.nif") & SLOT37
    assert resolve("a/z_0.nif") & SLOT37     # inherits from _1 partner
    assert resolve("a/other_0.nif") == 0


# ---- weight-partner parity guardrail (#slot0-weight-partner) ----

def _pairs(n):
    # n weighted verts on a bone -> [(i, 0.1), ...]
    return [(i, 0.1) for i in range(n)]


def test_parity_flags_missing_scale_bone_on_one_weight():
    # The exact GTO boots_0/_1 shape: _0 kept the far-thigh scale bones, _1 didn't.
    s0 = _FakeShape("Plane Boots", {
        "NPC L Calf [LClf]": _pairs(600),
        "NPC L RearThigh": _pairs(175),      # present on _0
        "NPC L RearCalf [LrClf]": _pairs(260),
    })
    s1 = _FakeShape("Plane Boots", {
        "NPC L Calf [LClf]": _pairs(600),
        "NPC L RearCalf [LrClf]": _pairs(280),   # RearThigh absent on _1
    })
    issues = _weight_partner_scale_divergence([s0], [s1], "boots_1.nif")
    assert len(issues) == 1
    assert "RearThigh" in issues[0]
    assert "_0-only" in issues[0]


def test_parity_ignores_boundary_flicker_below_threshold():
    # A single boundary vert difference must NOT trip the check (present_min=8).
    s0 = _FakeShape("Boot", {"NPC L RearCalf [LrClf]": _pairs(200),
                             "NPC L RearThigh": _pairs(3)})   # tiny -> ignored
    s1 = _FakeShape("Boot", {"NPC L RearCalf [LrClf]": _pairs(200)})
    assert _weight_partner_scale_divergence([s0], [s1], "b.nif") == []


def test_parity_ignores_slim_vs_curvy_gradient():
    # The graft legitimately reaches different vert counts at _0 (slim) vs _1
    # (curvy). A gradient where the bone is PRESENT in both must NOT flag --
    # only a true presence/absence leak should.
    s0 = _FakeShape("Cuirass", {"NPC L Breast01": _pairs(7)})    # present, small
    s1 = _FakeShape("Cuirass", {"NPC L Breast01": _pairs(50)})   # present, large
    assert _weight_partner_scale_divergence([s0], [s1], "c.nif") == []
    # But a genuine leak (present vs truly absent) still flags.
    s1b = _FakeShape("Cuirass", {"NPC L Breast01": _pairs(50),
                                 "NPC L Butt": _pairs(40)})
    s0b = _FakeShape("Cuirass", {"NPC L Breast01": _pairs(45)})  # Butt absent
    out = _weight_partner_scale_divergence([s0b], [s1b], "c.nif")
    assert len(out) == 1 and "Butt" in out[0] and "_1-only" in out[0]


def test_parity_clean_when_identical():
    bw = {"NPC L RearCalf [LrClf]": _pairs(100), "NPC L Breast01": _pairs(50)}
    s0 = _FakeShape("X", dict(bw))
    s1 = _FakeShape("X", dict(bw))
    assert _weight_partner_scale_divergence([s0], [s1], "x.nif") == []


def test_scale_bone_vert_counts_ignores_rigid():
    s = _FakeShape("S", {
        "NPC L Calf [LClf]": _pairs(500),        # rigid, not a scale bone
        "NPC L RearCalf [LrClf]": _pairs(50),    # scale
        "NPC L RearThigh": _pairs(2),            # scale
    })
    assert _scale_bone_vert_counts(s) == {"NPC L RearCalf [LrClf]": 50,
                                          "NPC L RearThigh": 2}
