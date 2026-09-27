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

r"""#coverage-nude-skin -- a coverage armature never draws the CBBE NUDE hands or
feet on a UBE actor.

THE DEFECT. An NPC costume's "boots" and "gloves" that are bare feet and hands
reuse `Actors\Character\Character Assets\FemaleFeet_1.nif` /
`FemaleHands_1.nif`. The body winner scan -- unlike the per-source and slot-33
passes, which never extend a nude-skin armature -- minted them for the UBE races
with that CBBE mesh. Measured live: 4 armatures, 2 on such an item pair and 2
on a custom race's skin.

THE RULE. A slot-33/37 armature whose MOD3 is a nude hand/foot under the
character assets folder draws the UBE body's own part, when it resolves; else it
is not minted. On a race skin (an armour that also lists a nude torso) it is not
minted: a UBE actor wears UBE's own skin.
"""
import struct
from pathlib import Path

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

DEFAULT = 0x00000019
BODY = 1 << 2
HANDS = 1 << 3
FEET = 1 << 7
OFF = "CBBE2UBE_NO_COVERAGE_NUDE_SKIN"
NUDE_FEET = r"Actors\Character\Character Assets\FemaleFeet_1.nif"
NUDE_HANDS = r"Actors\Character\Character Assets\FemaleHands_0.nif"
NUDE_TORSO = r"Actors\Character\Character Assets\FemaleBody_1.nif"
UBE_FEET = "!UBE\\Feet\\femalefeet_tangent_1.nif"
UBE_HANDS = "!UBE\\Hands\\femalehands_tangent_0.nif"


# ------------------------------------------------------------- the path test

def test_the_ube_part_keeps_the_weight():
    assert up.ube_body_part_for(NUDE_FEET) == UBE_FEET
    assert up.ube_body_part_for(NUDE_HANDS) == UBE_HANDS
    assert up.ube_body_part_for(r"meshes\actors\character\character assets\femalehands.nif") \
        == "!UBE\\Hands\\femalehands_tangent_1.nif", "no suffix reads as weight 1"


def test_only_the_nude_hands_and_feet_are_parts():
    """Negative controls: the folder AND the exact basename decide."""
    assert up.ube_body_part_for(r"Armor\CrbEX\pants\femalefeet_1.nif") is None, \
        "a real armour named after the body part is not skin"
    assert up.ube_body_part_for(
        r"Actors\Character\Character Assets\FemaleHandsKhajiit_1.nif") is None
    assert up.ube_body_part_for(r"Actors\Character\Character Assets\MaleHands_1.nif") is None
    assert up.ube_body_part_for(NUDE_TORSO) is None
    assert up.ube_body_part_for("") is None


def test_the_validator_knows_exactly_the_ube_parts():
    assert up.is_ube_body_part_path(UBE_FEET) and up.is_ube_body_part_path(UBE_HANDS)
    assert up.is_ube_body_part_path("!ube/hands/FemaleHands_Tangent_1.nif")
    assert not up.is_ube_body_part_path("!UBE\\Armor\\boots_1.nif")


# ------------------------------------------------------------ the body pass

def _save(path, masters, groups):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _arma(fid, slots, mod2, mod3):
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD2", encode_zstring(mod2))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    p += encode_subrecord(b"MO3T", struct.pack("<III", 0, 0, 0))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return Record(sig=b"ARMA", flags=0, formid=fid, payload=p)


def _armo(fid, slots, armas):
    p = encode_subrecord(b"EDID", encode_zstring("Piece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        p += encode_subrecord(b"MODL", struct.pack("<I", a))
    return Record(sig=b"ARMO", flags=0, formid=fid, payload=p)


FEET_ARMA = (0x01000800, FEET, r"Actors\Character\Character Assets\MaleFeet_1.nif",
             NUDE_FEET)
TORSO_ARMA = (0x01000802, BODY, r"Actors\Character\Character Assets\MaleBody_1.nif",
              NUDE_TORSO)


def _run(tmp_path, armas, armos, **kw):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[_arma(*a) for a in armas]),
                 Group(label=b"ARMO", records=[_armo(*o) for o in armos])])
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    kw.setdefault("converted_rel_paths", {"elsewhere/x_1.nif"})
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True, **kw)
    if not out.is_file():            # nothing to mint writes no piece
        return st, []
    e = ESP.load(out)
    minted = [dict(iter_subrecords(r.payload)) for g in e.groups
              if g.label == b"ARMA" for r in g.records]
    return st, minted


def _mod3(m):
    return m[b"MOD3"].rstrip(b"\x00").decode()


def test_nude_boots_draw_the_ube_feet(tmp_path):
    asked = []
    st, minted = _run(tmp_path, [FEET_ARMA], [(0x01000801, FEET, [0x01000800])],
                      mesh_exists=lambda p: asked.append(p) or True)
    assert st["armo_targets"] == 1 and len(minted) == 1
    assert _mod3(minted[0]) == UBE_FEET
    assert b"MO3T" not in minted[0], "the hash belonged to the CBBE feet"
    assert asked == [UBE_FEET]
    assert st["nude_redirected"] == [{"arma": "mod.esp|800", "to": UBE_FEET}]


def test_the_off_switch_draws_the_cbbe_feet_again(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, minted = _run(tmp_path, [FEET_ARMA], [(0x01000801, FEET, [0x01000800])],
                      mesh_exists=lambda p: True)
    assert _mod3(minted[0]) == NUDE_FEET and b"MO3T" in minted[0]
    assert st["nude_redirected"] == []


def test_the_weight_is_kept(tmp_path):
    hands = (0x01000800, HANDS, r"Actors\Character\Character Assets\MaleHands_0.nif",
             NUDE_HANDS)
    _st, minted = _run(tmp_path, [hands], [(0x01000801, HANDS, [0x01000800])],
                       mesh_exists=lambda p: True)
    assert _mod3(minted[0]) == UBE_HANDS


def test_unresolved_ube_feet_are_not_minted(tmp_path):
    st, minted = _run(tmp_path, [FEET_ARMA], [(0x01000801, FEET, [0x01000800])],
                      mesh_exists=lambda p: False)
    assert st["armo_targets"] == 0 and minted == []
    assert st["nude_skipped"] == [{"arma": "mod.esp|800", "why": "unresolved"}]
    assert [(a, w) for a, _e, w in st["nude_dropped"]] == [(("mod.esp", 0x801), "unresolved")]


def test_no_lookup_means_the_ube_feet_cannot_be_shown_to_resolve(tmp_path):
    st, minted = _run(tmp_path, [FEET_ARMA], [(0x01000801, FEET, [0x01000800])])
    assert minted == [] and st["nude_skipped"][0]["why"] == "unresolved"


def test_a_race_skin_is_not_minted(tmp_path):
    """The live skin: an armour on slot 37 alone listing the nude torso, hands and
    feet. The torso is never admitted (not converted); the feet were."""
    st, minted = _run(tmp_path, [FEET_ARMA, TORSO_ARMA],
                      [(0x01000801, FEET, [0x01000802, 0x01000800])],
                      mesh_exists=lambda p: True)
    assert minted == [] and st["armo_targets"] == 0
    assert st["nude_skipped"] == [{"arma": "mod.esp|800", "why": "skin"}]
    assert [(a, w) for a, _e, w in st["nude_dropped"]] == [(("mod.esp", 0x801), "skin")]


def test_an_armature_shared_by_a_skin_and_an_item_is_minted_for_the_item(tmp_path):
    st, minted = _run(tmp_path, [FEET_ARMA, TORSO_ARMA],
                      [(0x01000801, FEET, [0x01000802, 0x01000800]),
                       (0x01000803, FEET, [0x01000800])],
                      mesh_exists=lambda p: True)
    assert st["armo_targets"] == 1 and [_mod3(m) for m in minted] == [UBE_FEET]
    # It was minted (for the boots), so it is not reported as left out -- the
    # skin is still left without one.
    assert st["nude_redirected"] == [{"arma": "mod.esp|800", "to": UBE_FEET}]
    assert st["nude_skipped"] == []
    assert [(a, w) for a, _e, w in st["nude_dropped"]] == [(("mod.esp", 0x801), "skin")]


def test_real_boots_are_untouched(tmp_path):
    """Negative control: an armour path, even one named like the body part."""
    boots = (0x01000800, FEET, r"armor\m\boots_1.nif", r"Armor\CrbEX\pants\femalefeet_1.nif")
    st, minted = _run(tmp_path, [boots], [(0x01000801, FEET, [0x01000800])],
                      mesh_exists=lambda p: True)
    assert _mod3(minted[0]) == r"Armor\CrbEX\pants\femalefeet_1.nif"
    assert st["nude_redirected"] == [] and st["nude_skipped"] == []


# --------------------------------------------------------------- validator

def _patch_with(tmp_path, mod3):
    meshes = tmp_path / "mod" / "meshes"
    meshes.mkdir(parents=True)
    p = encode_subrecord(b"EDID", encode_zstring("UBE_MBD_800"))
    p += encode_subrecord(b"MOD3", encode_zstring(mod3))
    esp_path = tmp_path / "mod" / "Patch.esp"
    _save(esp_path, ["Skyrim.esm"],
          [Group(label=b"ARMA", records=[Record(sig=b"ARMA", flags=0,
                                                formid=0x01000800, payload=p)])])
    return esp_path


def _missing(warns):
    return [w for w in warns if w.startswith("missing-nif")]


def test_the_validator_flags_the_ube_feet_without_a_resolver(tmp_path):
    """The defect this guards against: our output never ships the UBE feet."""
    assert _missing(up.validate_patch(_patch_with(tmp_path, UBE_FEET)))


def test_the_validator_accepts_ube_feet_that_resolve(tmp_path):
    esp_path = _patch_with(tmp_path, UBE_FEET)
    assert not _missing(up.validate_patch(esp_path, mesh_resolves=lambda p: True))


def test_the_validator_still_flags_a_path_that_does_not_resolve(tmp_path):
    """Negative control: the resolver is asked, not trusted blindly."""
    esp_path = _patch_with(tmp_path, "!UBE\\Armor\\boots_1.nif")
    assert _missing(up.validate_patch(
        esp_path, mesh_resolves=up._outside_paths_predicate([UBE_FEET])))


def test_the_minted_piece_is_validated_with_its_outside_paths(tmp_path):
    """The pass validates its own piece; with a meshes folder beside it the NIF
    check runs, and the redirected feet must not read as a crash risk."""
    (tmp_path / "meshes").mkdir()
    st, _m = _run(tmp_path, [FEET_ARMA], [(0x01000801, FEET, [0x01000800])],
                  mesh_exists=lambda p: True)
    assert not _missing(st["validation_warnings"])


def test_the_postflight_resolver_asks_only_for_ube_parts(tmp_path, monkeypatch):
    """Anything else under `!UBE\\` that our output lacks is still missing --
    even when some mod ships it, when twins are switched off."""
    asked = []
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_UBE_TWIN", "1")
    monkeypatch.setattr(ac, "_mesh_exists_anywhere",
                        lambda output: lambda p: asked.append(p) or True)
    monkeypatch.setattr(ac, "_third_party_ube_twin_lookup",
                        lambda output, excl=(): lambda p: "Some Mod")
    res = ac._outside_ube_mesh_resolver(tmp_path)
    assert res(UBE_FEET) and asked == [UBE_FEET]
    assert not res("!UBE\\Armor\\boots_1.nif"), "with twins off, a twin is not a mesh"


def test_the_postflight_resolver_is_off_with_the_switch(tmp_path, monkeypatch):
    """Both users of the resolver off (the UBE twin is the other one): none."""
    monkeypatch.setenv(OFF, "1")
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_UBE_TWIN", "1")
    assert ac._outside_ube_mesh_resolver(tmp_path) is None


def test_the_postflight_gets_the_resolver():
    import inspect
    src = inspect.getsource(ac._cmd_convert)
    assert "mesh_resolves=_outside_ube_mesh_resolver(" in src
    assert "mesh_resolves=_outside_ube_mesh_resolver(" in inspect.getsource(ac._cmd_merge)


# ------------------------------------------------------------------ report

def test_the_parts_are_reported(capsys):
    ac._report_coverage_holds([{
        "nude_redirected": [{"arma": "mod.esp|800", "to": UBE_FEET}],
        "nude_skipped": [{"arma": "mod.esp|802", "why": "unresolved"},
                         {"arma": "skyrim.esm|D6C", "why": "skin"}],
        "nude_dropped": [(("mod.esp", 0x803), "Skin", "skin"),
                         (("mod.esp", 0x805), "Boots", "unresolved")]}])
    text = capsys.readouterr().out
    assert "1 hand/foot armature(s) that drew the nude CBBE hands or feet now draw" in text
    unres = next(l for l in text.splitlines() if "were not found" in l)
    assert unres.lstrip().startswith("!!"), "an unresolved UBE part is a problem"
    assert "(1 armour(s) left without one)" in unres
    assert "not covered: Boots  (mod.esp|000805)" in text
    assert ("1 nude hand/foot armature(s) of a race skin were not minted "
            "(1 armour(s) left without one)") in text


def _pair(tmp_path, **kw):
    """Boots and gloves that are bare feet and hands, and a race skin (slot 37
    alone, listing a nude torso, hands and feet on armatures of its own)."""
    hands = (0x01000804, HANDS, r"Actors\Character\Character Assets\MaleHands_1.nif",
             r"Actors\Character\Character Assets\FemaleHands_1.nif")
    skin_feet = (0x01000806, FEET, r"Actors\Character\Character Assets\MaleFeet_0.nif",
                 r"Actors\Character\Character Assets\FemaleFeet_0.nif")
    skin_hands = (0x01000807, HANDS, r"Actors\Character\Character Assets\MaleHands_0.nif",
                  r"Actors\Character\Character Assets\FemaleHands_0.nif")
    return _run(tmp_path, [FEET_ARMA, hands, TORSO_ARMA, skin_feet, skin_hands],
                [(0x01000801, FEET, [0x01000800]),
                 (0x01000805, HANDS, [0x01000804]),
                 (0x01000808, FEET, [0x01000802, 0x01000806, 0x01000807])], **kw)


def test_armours_are_counted_per_reason(tmp_path, capsys):
    """The defect: with the UBE hands/feet not built, the two items dropped as
    unresolved were counted as race skins -- '2 armatures of a race skin ... (3
    armour(s) left without one)' for ONE skin."""
    st, minted = _pair(tmp_path, mesh_exists=lambda p: False)
    assert minted == []
    assert sorted(w for _a, _e, w in st["nude_dropped"]) == ["skin", "unresolved", "unresolved"]
    ac._report_coverage_holds([st])
    text = capsys.readouterr().out
    unres = next(l for l in text.splitlines() if "were not found" in l)
    assert "2 hand/foot armature(s)" in unres and "(2 armour(s) left without one)" in unres
    assert ("2 nude hand/foot armature(s) of a race skin were not minted "
            "(1 armour(s) left without one)") in text


def test_a_shared_armature_is_not_reported_as_a_skin_left_out(tmp_path, capsys):
    """The defect: minted for the boots, it was still reported as a race skin's
    armature 'not minted'. The skin itself is still left without one."""
    st, _m = _run(tmp_path, [FEET_ARMA, TORSO_ARMA],
                  [(0x01000801, FEET, [0x01000802, 0x01000800]),
                   (0x01000803, FEET, [0x01000800])],
                  mesh_exists=lambda p: True)
    ac._report_coverage_holds([st])
    text = capsys.readouterr().out
    assert "1 hand/foot armature(s) that drew the nude CBBE hands or feet now draw" in text
    assert ("0 nude hand/foot armature(s) of a race skin were not minted "
            "(1 armour(s) left without one)") in text


def test_a_race_skin_alone_is_still_reported(tmp_path, capsys):
    """Negative control: the minted-elsewhere filter leaves a real skip alone."""
    st, _m = _pair(tmp_path, mesh_exists=lambda p: True)
    assert st["armo_targets"] == 2
    ac._report_coverage_holds([st])
    text = capsys.readouterr().out
    assert "were not found" not in text
    assert ("2 nude hand/foot armature(s) of a race skin were not minted "
            "(1 armour(s) left without one)") in text
