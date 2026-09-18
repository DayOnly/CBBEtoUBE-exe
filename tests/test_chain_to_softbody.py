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

"""Soft-body conversion gated option (CHAIN_TO_SOFTBODY).

Verifies the master switch:
  * defaults OFF (so normal output is unchanged),
  * when OFF, authored chain rigging is still detected/preserved,
  * when ON, every chain-preservation path is neutralized so chain
    cloth falls through to the soft-body pipeline (no dynamic chain ->
    no pull-to-origin collapse on the UBE race).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.nif_convert as nc  # noqa: E402


class _FakeShape:
    """Minimal duck-typed stand-in for a pynifly shape."""
    def __init__(self, bones):
        self.bone_names = bones


def _chain_shape():
    # 6 custom chain bones (unknown to the body) + 1 standard anchor bone.
    return _FakeShape([f"SkirtF 1_{i:02d}" for i in range(6)]
                      + ["NPC Pelvis [Pelv]"])


def test_softbody_switch_defaults_off():
    assert nc.CHAIN_TO_SOFTBODY is False


def test_chain_rigging_detected_when_off():
    nc.CHAIN_TO_SOFTBODY = False
    try:
        # 6/7 bones unknown to the body -> above HDT_BONE_THRESHOLD -> rigged.
        assert nc._shape_has_hdt_smp_rigging(
            _chain_shape(), {"NPC Pelvis [Pelv]"}) is True
    finally:
        nc.CHAIN_TO_SOFTBODY = False


def test_softbody_mode_neutralizes_all_chain_preservation():
    nc.CHAIN_TO_SOFTBODY = True
    try:
        # 1. Chain shapes no longer protected from the body-fit reskin.
        assert nc._shape_has_hdt_smp_rigging(_chain_shape(), set()) is False
        # 2. No shape names preserved as authored soft-bodies.
        assert nc._hdt_softbody_shape_names(Path("does_not_exist.nif")) == set()
        # 3. Never flagged as needing a regenerated chain XML.
        assert nc._source_hdt_needs_missing_chain_bones(
            "does_not_exist.nif", []) is False
        # 4. Chain bone nodes are not recreated in the output.
        assert nc._precreate_custom_bone_chains(
            None, None, ["SkirtF 1_01"]) == 0
    finally:
        nc.CHAIN_TO_SOFTBODY = False
