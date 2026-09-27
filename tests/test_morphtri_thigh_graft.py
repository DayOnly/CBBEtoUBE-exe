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

"""A morph-TRI shape takes the THIGH detail bones and skips only RearCalf
(#morphtri-thigh-graft).

`#morphtri-no-leg-graft` gated all three leg detail bones on shapes whose source
ships a BodySlide TRI, on evidence that named one: RearCalf landing on a flap
tip at calf height. FrontThigh / RearThigh went with it -- the bones CBPC
bounces under trousers -- and a forward thigh bounce then pushed the front of
the lower thigh through the cloth. The names below are LITERALS on purpose: a
set derived from the code under test would agree with any mutation of it.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402
from src import nif_convert_weights as ncw  # noqa: E402
from tests import _converter_sources as _cs  # noqa: E402

_REAR_CALF = {"NPC L RearCalf [LrClf]", "NPC R RearCalf [RrClf]"}
_THIGH_PAIR = {"NPC L FrontThigh", "NPC L RearThigh",
               "NPC R FrontThigh", "NPC R RearThigh"}


def test_flag_defaults_ON_and_the_kill_switch_is_honoured(monkeypatch):
    assert nc.MORPHTRI_THIGH_GRAFT is True
    monkeypatch.setenv("CBBE2UBE_NO_MORPHTRI_THIGH_GRAFT", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.MORPHTRI_THIGH_GRAFT is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_MORPHTRI_THIGH_GRAFT", raising=False)
        importlib.reload(nc)


def test_on_only_the_calf_detail_bones_stay_gated():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "MORPHTRI_THIGH_GRAFT", True)
        assert ncw._morphtri_gated_detail_bones() == _REAR_CALF


def test_off_every_detail_bone_is_gated_as_before():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "MORPHTRI_THIGH_GRAFT", False)
        assert ncw._morphtri_gated_detail_bones() == _REAR_CALF | _THIGH_PAIR


def test_the_graft_pass_takes_its_gate_from_the_helper():
    """The pass must narrow a morph-TRI shape by the helper's answer. A local copy
    of the old all-detail-bones set would silently undo the change."""
    src = _cs.source(nc._match_rigid_leg_bend_to_body)
    assert "_morphtri_gated_detail_bones()" in src
    assert "frozenset(_LEG_DETAIL_BONE_NAMES)" not in src
