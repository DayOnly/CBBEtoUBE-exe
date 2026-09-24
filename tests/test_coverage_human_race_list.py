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

r"""#coverage-human-race-list -- an armour whose only human-drawing armature
has a primary race other than DefaultRace is drawn on UBE.

THE DEFECT. Both coverage passes minted only armatures whose primary race
(RNAM) is DefaultRace. An amulet re-authored with an Argonian primary that
lists every human and mer race as an additional race draws on a vanilla human
woman and on nothing of a UBE race. Live census: 291 adult armours a female UBE
actor can wear.

THE RULE. Only where the old rule admitted no armature of the armour: the
WINNING armour record is playable or an NPC wears it, it is no race's or NPC's
skin, the armature's female world mesh is no effect, and the armature lists
DefaultRace or a vanilla human/mer race. The minted armature targets the UBE
counterpart of each vanilla race listed -- every UBE race only when DefaultRace
is listed. A body armature still needs a converted mesh; a hands/feet one keeps
its source races and adds the mapped UBE ones.
`CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST=1` restores the DefaultRace-only rule.
"""
import inspect
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import ESP, Group, Record, encode_subrecord, encode_zstring, iter_subrecords
from tests.test_coverage_ube_twin import DEFAULT, _save

OFF = "CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST"

# Skyrim.esm races (low 24 bits; Skyrim.esm is master 0 of Mod.esp).
ARGONIAN, KHAJIIT, ARGONIAN_VAMP = 0x013740, 0x013745, 0x08883A
BRETON, IMPERIAL, NORD, REDGUARD = 0x013741, 0x013744, 0x013746, 0x013748
DARKELF, HIGHELF, WOODELF, ORC = 0x013742, 0x013743, 0x013749, 0x013747
VAMPIRES = (0x08883C, 0x088844, 0x088794, 0x088846,
            0x08883D, 0x088840, 0x088884, 0x0A82B9)
HUMANS = (BRETON, IMPERIAL, NORD, REDGUARD, DARKELF, HIGHELF, WOODELF, ORC)
CUSTOM = 0x01000900          # a race Mod.esp defines itself

# UBE_AllRace.esp races (low 24 bits).
UBE_BRETON, UBE_NORD, UBE_WOODELF = 0x005734, 0x05A184, 0x05A1AC
ALL_UBE = list(up.UBE_RACE_FIDS_24)

AMULET = 1 << 5              # slot 35
BODY = 1 << 2                # slot 32
FEET = 1 << 7                # slot 37
NONPLAYABLE = 0x00000004
AMULET_MESH = r"clothes\jewelry\amuletgnd.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _arma(formid, slots, primary, extra=(), models=None):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary))
    for sig, path in (models or {b"MOD3": AMULET_MESH}).items():
        p += encode_subrecord(sig, encode_zstring(path))
    for r in extra:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots, armas, flags=0, edid="Piece"):
    q = encode_subrecord(b"EDID", encode_zstring(edid))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    q += encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0))
    return Record(sig=b"ARMO", flags=flags, formid=formid, payload=q)


def _world(tmp_path, armas, slots=AMULET, flags=0, extra_groups=()):
    """Skyrim.esm, UBE_AllRace.esp, and Mod.esp defining the armatures and one
    armour (Mod.esp|000801) that lists them."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    armo = _armo(0x01000801, slots, armas, flags=flags)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=list(armas)),
                 Group(label=b"ARMO", records=[armo]), *extra_groups])
    return [sky, ube, mod]


ARMOUR = ("mod.esp", 0x000801)


def _minted(out):
    """Each minted armature: its world mesh and its races, as (plugin, low24)."""
    if not out.is_file():
        return []
    e = ESP.load(out)
    ms = [m.lower() for m in e.header.masters]

    def _abs(fid):
        top = fid >> 24
        return (ms[top] if top < len(ms) else out.name.lower(), fid & 0xFFFFFF)

    got = []
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            d = {"races": []}
            for s, v in iter_subrecords(r.payload):
                if s == b"MOD3":
                    d["mod3"] = v.rstrip(b"\x00").decode()
                elif s == b"RNAM":
                    d["primary"] = _abs(struct.unpack("<I", v)[0])
                elif s == b"MODL":
                    d["races"].append(_abs(struct.unpack("<I", v)[0]))
            got.append(d)
    return got


def _nonbody(tmp_path, world, worn=None):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, world, exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True, npc_worn_armo_abs=worn)
    return st, _minted(out)


def _body(tmp_path, world, conv=(), worn=None):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, world, converted_rel_paths=set(conv), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True, npc_worn_armo_abs=worn)
    return st, _minted(out)


def _ube(*lows):
    return [("ube_allrace.esp", f) for f in lows]


def _sky(*lows):
    return [("skyrim.esm", f) for f in lows]


def _human_amulet(formid=0x01000800, **kw):
    """The live shape: Argonian primary, every human race and vampire added."""
    return _arma(formid, AMULET, ARGONIAN,
                 extra=(ARGONIAN, *HUMANS, *VAMPIRES), **kw)


# --------------------------------------------------------------- the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    assert up._coverage_human_race_list() is True
    monkeypatch.setenv(OFF, "0")
    assert up._coverage_human_race_list() is True
    monkeypatch.setenv(OFF, "1")
    assert up._coverage_human_race_list() is False


# ---------------------------------------------------------- the race table

def test_every_ube_race_has_exactly_one_vanilla_counterpart():
    """The table maps the 16 human/mer races (vampires included) one to one
    onto the 16 UBE races every coverage armature gets."""
    table = up.UBE_RACE_FOR_VANILLA_24
    assert sorted(table.values()) == sorted(up.UBE_RACE_FIDS_24)
    assert len(set(table.values())) == len(table)


def test_the_human_races_are_the_ube_capable_ones_without_the_beasts():
    beasts = {ARGONIAN, KHAJIIT, ARGONIAN_VAMP, 0x088845}
    assert set(up.UBE_RACE_FOR_VANILLA_24) == set(ac._UBE_CAPABLE_VANILLA_RACES) - beasts


# ------------------------------------------------------------ the non-body pass

def test_an_argonian_primary_amulet_listing_the_human_races_is_drawn(tmp_path):
    st, minted = _nonbody(tmp_path, _world(tmp_path, [_human_amulet()]))
    assert len(minted) == 1
    m = minted[0]
    assert m["mod3"] == AMULET_MESH, "an accessory keeps its own mesh"
    assert m["races"] == _ube(*ALL_UBE), "every human race listed -> every UBE race"
    assert m["primary"] == _ube(UBE_BRETON)[0]
    assert st["armo_targets"] == 1
    assert st["race_listed"] == [(ARMOUR, "Piece")]


def test_switched_off_only_defaultrace_armatures_are_minted(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, minted = _nonbody(tmp_path, _world(tmp_path, [_human_amulet()]))
    assert minted == [] and st["armo_targets"] == 0
    assert st["race_listed"] == []


def test_a_non_playable_armour_no_one_wears_is_not_taken(tmp_path):
    """Gore, creature gear, effect props: non-playable and on no NPC."""
    world = _world(tmp_path, [_human_amulet()], flags=NONPLAYABLE)
    st, minted = _nonbody(tmp_path, world, worn=frozenset({("mod.esp", 0x999)}))
    assert minted == [] and st["race_listed"] == []


def test_a_non_playable_armour_an_npc_wears_is_taken(tmp_path):
    world = _world(tmp_path, [_human_amulet()], flags=NONPLAYABLE)
    st, minted = _nonbody(tmp_path, world, worn=frozenset({ARMOUR}))
    assert len(minted) == 1 and st["race_listed"] == [(ARMOUR, "Piece")]


def _race_skin():
    p = encode_subrecord(b"EDID", encode_zstring("SnowElfRace"))
    p += encode_subrecord(b"WNAM", struct.pack("<I", 0x01000801))
    return Group(label=b"RACE", records=[
        Record(sig=b"RACE", flags=0, formid=0x01000A00, payload=p)])


def _npc_skin():
    p = encode_subrecord(b"EDID", encode_zstring("ElderNPC"))
    p += encode_subrecord(b"WNAM", struct.pack("<I", 0x01000801))
    return Group(label=b"NPC_", records=[
        Record(sig=b"NPC_", flags=0, formid=0x01000A01, payload=p)])


@pytest.mark.parametrize("skin", [_race_skin, _npc_skin], ids=["race", "npc"])
def test_a_skin_is_not_taken(tmp_path, skin):
    """A race's or an NPC's skin is the body, not an armour worn over it."""
    world = _world(tmp_path, [_human_amulet()], extra_groups=[skin()])
    st, minted = _nonbody(tmp_path, world)
    assert minted == [] and st["race_listed"] == []


@pytest.mark.parametrize("models", [
    {b"MOD3": r"effects\fxveil01.nif"},                  # under effects\
    {b"MOD3": r"magic\FXBallOfLight.nif"},               # an fx file
    {b"MOD2": r"meshes\effects\glow.nif"},               # no MOD3: MOD2
    {b"MOD3": ""},                                        # draws nothing
], ids=["effects-dir", "fx-name", "male-only", "empty"])
def test_an_effect_mesh_is_not_taken(tmp_path, models):
    world = _world(tmp_path, [_human_amulet(models=models)])
    st, minted = _nonbody(tmp_path, world)
    assert minted == [] and st["race_listed"] == []


def test_a_male_world_mesh_alone_is_drawn(tmp_path):
    """No MOD3: the female world mesh is the male one, and it is no effect."""
    world = _world(tmp_path, [_human_amulet(models={b"MOD2": AMULET_MESH})])
    st, minted = _nonbody(tmp_path, world)
    assert len(minted) == 1 and st["race_listed"] == [(ARMOUR, "Piece")]


def test_a_beast_only_armature_is_not_taken(tmp_path):
    arma = _arma(0x01000800, AMULET, ARGONIAN, extra=(ARGONIAN, KHAJIIT, ARGONIAN_VAMP))
    st, minted = _nonbody(tmp_path, _world(tmp_path, [arma]))
    assert minted == [] and st["race_listed"] == []


def test_a_wood_elf_only_armature_stays_wood_elf_only(tmp_path):
    """A custom-race primary with one human race added: the UBE armature is for
    the UBE Wood Elf alone, not every UBE race."""
    arma = _arma(0x01000800, AMULET, CUSTOM, extra=(CUSTOM, WOODELF))
    st, minted = _nonbody(tmp_path, _world(tmp_path, [arma]))
    assert len(minted) == 1
    assert minted[0]["races"] == _ube(UBE_WOODELF)
    assert minted[0]["primary"] == _ube(UBE_WOODELF)[0]


def test_defaultrace_among_the_races_means_every_ube_race(tmp_path):
    arma = _arma(0x01000800, AMULET, ARGONIAN, extra=(ARGONIAN, DEFAULT, WOODELF))
    st, minted = _nonbody(tmp_path, _world(tmp_path, [arma]))
    assert len(minted) == 1
    assert minted[0]["races"] == _ube(*ALL_UBE)
    assert minted[0]["primary"] == _ube(UBE_BRETON)[0]


def test_an_armour_with_a_defaultrace_armature_is_unchanged(tmp_path):
    """The rule is only for armour the DefaultRace rule took nothing of: no
    second armature beside the one it mints."""
    human = _arma(0x01000802, AMULET, DEFAULT, extra=(DEFAULT,),
                  models={b"MOD3": r"clothes\jewelry\human.nif"})
    world = _world(tmp_path, [human, _human_amulet()])
    st, minted = _nonbody(tmp_path, world)
    assert [m["mod3"] for m in minted] == [r"clothes\jewelry\human.nif"]
    assert minted[0]["races"] == _ube(*ALL_UBE)
    assert st["race_listed"] == []


# ---------------------------------------------------------------- the body pass

CUIRASS = r"armor\scaled\cuirass_1.nif"


def _cuirass():
    return _arma(0x01000800, BODY, ARGONIAN, extra=(ARGONIAN, WOODELF, NORD),
                 models={b"MOD3": CUIRASS})


def test_a_body_armature_still_needs_a_converted_mesh(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_cuirass()], slots=BODY))
    assert minted == [] and st["race_listed"] == []


def test_a_converted_body_armature_is_taken_with_its_races_mapped(tmp_path):
    st, minted = _body(tmp_path, _world(tmp_path, [_cuirass()], slots=BODY),
                       conv={CUIRASS.replace("\\", "/")})
    assert len(minted) == 1
    assert minted[0]["mod3"] == "!UBE\\" + CUIRASS
    assert minted[0]["races"] == _ube(UBE_NORD, UBE_WOODELF)
    assert minted[0]["primary"] == _ube(UBE_NORD)[0]
    assert st["race_listed"] == [(ARMOUR, "Piece")]


def test_a_skin_body_is_not_taken(tmp_path):
    """A race's naked skin with a converted body mesh is still a skin."""
    world = _world(tmp_path, [_cuirass()], slots=BODY, extra_groups=[_race_skin()])
    st, minted = _body(tmp_path, world, conv={CUIRASS.replace("\\", "/")})
    assert minted == [] and st["race_listed"] == []


BOOTS = r"armor\scaled\boots_1.nif"


def _boots(primary=ARGONIAN):
    return _arma(0x01000800, FEET, primary, extra=(WOODELF, NORD),
                 models={b"MOD3": BOOTS})


def test_boots_keep_their_source_races_and_add_the_mapped_ube_ones(tmp_path):
    """Hands/feet stay on the source-primary path: a UBE actor's foot slot can
    resolve to a vanilla race, so the source list is kept -- and only the UBE
    counterparts of its human races are added."""
    st, minted = _body(tmp_path, _world(tmp_path, [_boots()], slots=FEET))
    assert len(minted) == 1
    m = minted[0]
    assert m["mod3"] == BOOTS, "unconverted boots keep their own mesh"
    assert m["primary"] == _sky(ARGONIAN)[0]
    assert m["races"] == _sky(WOODELF, NORD) + _ube(UBE_NORD, UBE_WOODELF)
    assert st["race_listed"] == [(ARMOUR, "Piece")]


def test_boots_whose_primary_the_patch_cannot_name_fall_back_to_ube(tmp_path, monkeypatch):
    """A custom primary whose plugin the patch does not hold as a master (the
    master cap) would dangle; it falls back to the mapped UBE primary."""
    monkeypatch.setattr(up, "_arma_race_master_names", lambda *a: [])
    st, minted = _body(tmp_path, _world(tmp_path, [_boots(primary=CUSTOM)], slots=FEET))
    assert len(minted) == 1
    assert minted[0]["primary"] == _ube(UBE_NORD)[0]


def test_boots_with_a_defaultrace_armature_are_unchanged(tmp_path):
    human = _arma(0x01000802, FEET, DEFAULT, extra=(DEFAULT,),
                  models={b"MOD3": r"armor\scaled\humanboots_1.nif"})
    st, minted = _body(tmp_path, _world(tmp_path, [human, _boots()], slots=FEET))
    assert [m["mod3"] for m in minted] == [r"armor\scaled\humanboots_1.nif"]
    assert st["race_listed"] == []


def test_non_playable_boots_no_one_wears_are_not_taken(tmp_path):
    world = _world(tmp_path, [_boots()], slots=FEET, flags=NONPLAYABLE)
    st, minted = _body(tmp_path, world)
    assert minted == []
    st, minted = _body(tmp_path, world, worn=frozenset({ARMOUR}))
    assert len(minted) == 1


# ------------------------------------------------------------ report and wiring

def test_the_armours_are_reported(capsys):
    ac._report_coverage_holds([{"race_listed": [(ARMOUR, "Piece")]}])
    out = capsys.readouterr().out
    assert ("1 armour(s) whose only human-drawing armature has another primary "
            "race are now drawn on UBE (race list mapped)") in out
    assert "Piece  (mod.esp|000801)" in out


def test_the_batch_hands_the_worn_set_to_both_passes():
    """WIRING GUARD: every test above calls the passes directly."""
    src = inspect.getsource(ac._emit_unified_coverage_patches)
    assert "_worn = (_batch_npc_worn_armos()" in src
    assert src.count("npc_worn_armo_abs=_worn)") == 2
