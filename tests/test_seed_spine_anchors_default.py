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

"""`#seed-spine-anchors` is DEFAULT ON (2026-08-17), and the flag and its GUI
setting must agree about which way round that is.

Chains hanging from `NPC Spine1/Spine2` were never anchor-seeded, so they kept a
source-LOCAL transform under an anchor flat at identity and landed ~90u low.
Confirmed fixed in game on a bandit armour; re-censused on the shipped pack the
day of the flip: 18 pieces still carried chains >5u off source, worst 92.60u, and
16 of those 18 hang from a spine anchor.

The polarity check is the point of this file. A default-ON feature takes a `NO_*`
kill switch read with `not in`, and its `Setting` must carry `invert=True`. Get
either half backwards and the option silently does the OPPOSITE of its label --
which is the same failure `unseen_settings` exists to catch, one level down.
Arm/clavicle/shoulder/neck/head stay excluded: seeding those IS proven bad in
game, so this pins that they were not swept along with the flip.
"""
import inspect
import os

from src import gui_settings as gs
from src import nif_convert as nc

KEY = "seed_spine_anchors"
ENV = "CBBE2UBE_NO_SEED_SPINE_ANCHORS"


def _decl(name):
    src = inspect.getsource(nc)
    i = src.index(f"{name} = os.environ.get(")
    return src[i:i + 240]


def test_spine_seeding_is_ON_by_default():
    assert nc.SEED_SPINE_ANCHORS is True


def test_it_takes_a_NO_kill_switch_read_the_default_ON_way():
    d = _decl("SEED_SPINE_ANCHORS")
    assert f'"{ENV}"' in d, "a default-ON flag takes a NO_* kill switch"
    assert "not in (" in d, (
        "read with `in (...)` this would be default OFF and every piece would "
        "keep shipping its spine chains ~90u low")


def test_the_kill_switch_actually_turns_it_off(monkeypatch):
    import importlib
    monkeypatch.setenv(ENV, "1")
    mod = importlib.reload(nc)
    try:
        assert mod.SEED_SPINE_ANCHORS is False
    finally:
        monkeypatch.delenv(ENV, raising=False)
        importlib.reload(nc)
    assert nc.SEED_SPINE_ANCHORS is True


def test_the_gui_setting_agrees_with_the_flag():
    s = gs.by_key()[KEY]
    assert s.default is True
    assert s.env == ENV
    assert s.invert is True, (
        "invert=False on a NO_* var would set the kill switch whenever the user "
        "ticks the box -- the option would do the opposite of its own label")


def test_a_ticked_box_sets_no_env_so_the_code_default_applies():
    # An ON default-ON setting must leave the var UNSET, not write "0": the flag
    # tests membership, so "0" would read as... not in the truthy set, i.e. still
    # ON -- correct by luck here, but the registry contract is "unset = default".
    s = gs.by_key()[KEY]
    assert gs.env_string_for(s, True) is None
    assert gs.env_string_for(s, False) == "1"


def test_an_unsaved_settings_file_still_gets_the_fix(tmp_path):
    # save_values stores only NON-default values, so a file that predates this
    # option has no key for it -- and absent must mean ON.
    p = tmp_path / "s.json"
    gs.save_values({"per_anchor_seed": True}, p)
    vals = gs.load_values(p)
    assert vals[KEY] is True
    assert gs.env_string_for(gs.by_key()[KEY], vals[KEY]) is None


def test_arm_and_shoulder_anchors_are_still_excluded():
    # Seeding these IS proven bad in game (sleeves "bound in a pose"). The flip
    # must not have widened past the spine.
    assert nc._is_spine_anchor("NPC Spine2 [Spn2]") is True
    assert nc._is_spine_anchor("NPC Spine1 [Spn1]") is True
    for bone in ("NPC L Clavicle [LClv]", "NPC R Shoulder",
                 "NPC L UpperArm [LUar]", "NPC Neck [Neck]", "NPC Head [Head]"):
        assert nc._is_spine_anchor(bone) is False, bone
    assert nc._is_arm_anchor("NPC L UpperArm [LUar]") is True
