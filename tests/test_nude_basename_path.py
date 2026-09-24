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

"""#nude-basename-path -- "femalebody" is not always the nude skin.

`_player_armor_mesh_bases` dropped any model whose FILE NAME was a nude-skin name
(femalebody, femalehands, ...), wherever it sat, on the belief that real armour
is never named that. A pair of playable pants ships as
`armor\\<set>\\pants\\femalebody_1.nif`; it was never converted, while the
coverage step gave it a UBE armature anyway, so the CBBE pants drew on the UBE
body. The path alone does not tell them apart either: follower and race bodies
live outside `actors\\character\\` too. A skin-named model is armour only outside
that folder AND when a playable armour record WITH A NAME uses it -- something a
player picks up, which a skin record never is."""
import struct

import pytest

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src import auto_convert as ac
from src.auto_convert import _player_armor_mesh_bases

OFF = "CBBE2UBE_NO_NUDE_BASENAME_PATH"
DEFAULT_RACE = 0x00000019
BODY = 1 << 2
PANTS = "Armor\\PantsSet\\Pants\\FemaleBody_1.nif"
SKIN = "Actors\\Character\\Character Assets\\FemaleBody_1.nif"
FOLLOWER_BODY = "Follower\\Body\\FemaleBody_1.nif"


@pytest.fixture(autouse=True)
def _switch_unset(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _arma(fid, edid, mesh, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
               + encode_subrecord(b"MOD3", encode_zstring(mesh)))
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, payload=payload)


def _armo(fid, edid, arma_fid, *, name=None, nonplayable=False, slot=BODY):
    payload = (encode_subrecord(b"EDID", encode_zstring(edid))
               + encode_subrecord(b"BOD2", struct.pack("<II", slot, 0)))
    if name is not None:
        payload += encode_subrecord(b"FULL", encode_zstring(name))
    payload += (encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
                + encode_subrecord(b"DATA", struct.pack("<If", 10, 1.0)))
    return esp.Record(sig=b"ARMO", flags=0x04 if nonplayable else 0, formid=fid,
                      payload=payload)


def _mod(tmp_path, *pieces):
    """pieces: (model path, ARMO kwargs or None for no armour record)."""
    mod = tmp_path / "Pants Set"
    mod.mkdir()
    armas, armos = [], []
    for i, (model, kw) in enumerate(pieces):
        armas.append(_arma(0x01000800 + i, f"AA{i}", model))
        if kw is not None:
            armos.append(_armo(0x01000900 + i, f"AR{i}", 0x01000800 + i, **kw))
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=armas),
                    esp.Group(label=b"ARMO", records=armos)]).save(mod / "PantsSet.esp")
    return mod


ITEM = {"name": "Work Pants"}


def test_pants_named_like_the_body_are_armour(tmp_path):
    bases = _player_armor_mesh_bases(_mod(tmp_path, (PANTS, ITEM)))
    assert bases == {"armor/pantsset/pants/femalebody"}, bases


def test_the_real_body_skin_is_still_not_armour(tmp_path):
    # Negative control: the rule must still keep the nude skin out, at every
    # spelling of its home (case, slashes, a redundant "meshes\\" prefix) --
    # even behind a named, playable record.
    for skin in (SKIN, "meshes\\actors\\character\\character assets\\femalehands_1.nif",
                 "Actors/Character/Character Assets Female/FemaleFeet_0.nif",
                 "actors\\character\\character assets\\1stpersonfemalebody_1.nif"):
        assert ac._is_nude_body_skin_model(ac._weight_base_key(skin), False, True), skin
    assert _player_armor_mesh_bases(_mod(tmp_path, (SKIN, ITEM), (PANTS, ITEM))) == {
        "armor/pantsset/pants/femalebody"}


@pytest.mark.parametrize("kw", [
    {},                                           # a skin record: no name
    {"name": "Body", "nonplayable": True},        # named, but never picked up
    None,                                         # no armour record in the plugin
], ids=["nameless", "non-playable", "no-record"])
def test_a_follower_body_outside_the_body_folder_is_still_skin(tmp_path, kw):
    """Measured: 108 of 114 skin-named armatures outside actors\\character\\ on a
    real load order are bodies (follower and race skins, unused body variants).
    The record is handed out by an NPC's outfit here, so the non-playable case
    reaches this rule instead of stopping at the non-playable gate
    (#npc-worn-nonplayable)."""
    worn = {("pantsset.esp", 0x000900)}
    assert _player_armor_mesh_bases(_mod(tmp_path, (FOLLOWER_BODY, kw)),
                                    npc_worn_armos=worn) == set()


def test_only_skin_names_are_ever_skin():
    assert not ac._is_nude_body_skin_model(
        "actors/character/character assets/cuirass")
    assert not ac._is_nude_body_skin_model("armor/set/cuirass", True)


def test_the_off_switch_restores_the_name_only_rule(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    assert _player_armor_mesh_bases(_mod(tmp_path, (PANTS, ITEM))) == set()
