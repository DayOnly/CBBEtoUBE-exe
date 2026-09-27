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

"""#coverage-female-guard -- a minted coverage armature never puts a converted
MALE mesh in a female slot that names a mesh of its own.

THE DEFECT. `rebuild_arma_payload` filled an unconverted MOD3/MOD5 with the
converted MOD2/MOD4. The coverage passes neither logged nor undid it. Reported
in game: a follower wore converted male Ebony boots on her UBE body. Over the
live pack 9 world and 4 first-person female slots took a male mesh.

THE POLICY (user, 2026-09-23). A female slot takes a converted female mesh or
its own source path. Hands/feet and accessories keep the source path; a slot-32
body armature is not minted, as an unconverted vest already is not. A source
with NO female model keeps the male one there -- what the engine draws anyway.
"""
import struct
from pathlib import Path

import pytest

from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

DEFAULT = 0x00000019
HEAD = 1 << 0                # slot 30, non-deforming
BODY = 1 << 2                # slot 32
FEET = 1 << 7                # slot 37
MALE = r"armor\m\boots_1.nif"
FEMALE = r"follower\f\boots_1.nif"
OFF = "CBBE2UBE_NO_COVERAGE_FEMALE_GUARD"


def _payload(models, slots=FEET, mo3t=False):
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
            if sig == b"MOD3" and mo3t:
                p += encode_subrecord(b"MO3T", struct.pack("<III", 0, 0, 0))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return p


def _models(payload):
    return {s: d.rstrip(b"\x00").decode() for s, d in iter_subrecords(payload)
            if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}


def _rebuild(models, **kw):
    conv = {"armor\\m\\boots_1.nif", "armor\\m\\1stboots_1.nif"}
    return up.rebuild_arma_payload(
        _payload(models, mo3t=True), new_primary_rnam=1, new_additional_race_fids=[],
        converted_nif_exists=lambda p: p.lower() in conv, **kw)


# ------------------------------------------------------ rebuild_arma_payload

def test_a_named_female_slot_keeps_its_own_mesh():
    log = []
    out = _rebuild({b"MOD2": MALE, b"MOD3": FEMALE}, keep_named_female=True,
                   declined_log=log)
    assert _models(out)[b"MOD3"] == FEMALE
    assert log == [{"slot": "MOD3", "kept": FEMALE, "male": "!UBE\\" + MALE}]
    assert any(s == b"MO3T" for s, _d in iter_subrecords(out)), \
        "the female texture hash still matches the kept mesh and must stay"


def test_without_the_guard_the_male_mesh_fills_it():
    """The old behaviour, still what the per-source builder asks for."""
    out = _rebuild({b"MOD2": MALE, b"MOD3": FEMALE})
    assert _models(out)[b"MOD3"] == "!UBE\\" + MALE


def test_a_named_first_person_slot_keeps_its_own_mesh():
    out = _rebuild({b"MOD2": MALE, b"MOD3": FEMALE,
                    b"MOD4": r"armor\m\1stboots_1.nif", b"MOD5": r"follower\f\1st_1.nif"},
                   keep_named_female=True)
    assert _models(out)[b"MOD5"] == r"follower\f\1st_1.nif"


def test_an_absent_female_slot_still_takes_the_male_mesh():
    """No female model: the engine draws the male one for a female anyway."""
    out = _rebuild({b"MOD2": MALE}, keep_named_female=True)
    assert _models(out)[b"MOD3"] == "!UBE\\" + MALE


def test_an_empty_female_slot_counts_as_absent():
    out = _rebuild({b"MOD2": MALE, b"MOD3": ""}, keep_named_female=True)
    assert _models(out)[b"MOD3"] == "!UBE\\" + MALE


def test_a_dead_female_path_keeps_the_male_mesh():
    """A female mesh that exists nowhere draws nothing: the female-only selection
    keeps the male mesh for it (#174), and so does the guard (user, 09-24)."""
    log = []
    out = _rebuild({b"MOD2": MALE, b"MOD3": FEMALE}, keep_named_female=True,
                   declined_log=log, female_mesh_exists=lambda p: False)
    assert _models(out)[b"MOD3"] == "!UBE\\" + MALE
    assert log == [{"slot": "MOD3", "dead": FEMALE, "male": "!UBE\\" + MALE}]


def test_the_existence_lookup_is_asked_only_where_the_male_mesh_would_go():
    """It probes the whole modlist, so asking it for every minted slot would cost
    a scan per armature."""
    asked = []

    def exists(p):
        asked.append(p)
        return True
    _rebuild({b"MOD2": r"armor\f\other_1.nif", b"MOD3": FEMALE},
             keep_named_female=True, female_mesh_exists=exists)
    assert asked == []
    _rebuild({b"MOD2": MALE, b"MOD3": FEMALE}, keep_named_female=True,
             female_mesh_exists=exists)
    assert asked == [FEMALE]


# ------------------------------------------------------- the coverage passes

def _save(path, masters, groups):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _world(tmp_path, slots, models):
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800,
                  payload=_payload(models, slots))
    p = encode_subrecord(b"EDID", encode_zstring("Piece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
    armo = Record(sig=b"ARMO", flags=0, formid=0x01000801, payload=p)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]),
                 Group(label=b"ARMO", records=[armo])])
    return [sky, ube, mod]


def _body(tmp_path, slots, models, conv, **kw):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, slots, models), converted_rel_paths=conv,
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True, **kw)
    return st, out


def _minted(out):
    e = ESP.load(out)
    return [_models(r.payload) for g in e.groups if g.label == b"ARMA"
            for r in g.records]


def test_boots_keep_their_own_female_mesh(tmp_path):
    """The reported piece: feet, male converted, female named but not converted."""
    st, out = _body(tmp_path, FEET, {b"MOD2": MALE, b"MOD3": FEMALE},
                    {"armor/m/boots_1.nif"})
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == FEMALE
    assert [k["slot"] for k in st["female_kept"]] == ["MOD3"]


def test_the_off_switch_restores_the_male_boots(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, out = _body(tmp_path, FEET, {b"MOD2": MALE, b"MOD3": FEMALE},
                    {"armor/m/boots_1.nif"})
    assert _minted(out)[0][b"MOD3"] == "!UBE\\" + MALE
    assert st["female_kept"] == []


def test_a_body_piece_with_only_its_male_mesh_converted_is_not_minted(tmp_path):
    cuirass_m, cuirass_f = r"armor\m\cuirass_1.nif", r"follower\f\vest_1.nif"
    st, _out = _body(tmp_path, BODY, {b"MOD2": cuirass_m, b"MOD3": cuirass_f},
                     {"armor/m/cuirass_1.nif"})
    assert st["armo_targets"] == 0
    assert st["female_guard_skipped"] == ["mod.esp|800"]
    assert [a for a, _e in st["female_guard_dropped"]] == [("mod.esp", 0x801)]


def test_the_off_switch_mints_the_male_body_again(tmp_path, monkeypatch):
    """#coverage-world-mesh (09-24) also refuses this torso -- its female world
    mesh exists and was not converted -- so the old mint needs both switches."""
    monkeypatch.setenv(OFF, "1")
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_WORLD_MESH", "1")
    st, out = _body(tmp_path, BODY, {b"MOD2": r"armor\m\cuirass_1.nif",
                                     b"MOD3": r"follower\f\vest_1.nif"},
                    {"armor/m/cuirass_1.nif"})
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == "!UBE\\armor\\m\\cuirass_1.nif"


def test_a_body_piece_with_its_female_mesh_converted_is_minted(tmp_path):
    """Negative control: the guard bites only where the male mesh would be used."""
    st, out = _body(tmp_path, BODY, {b"MOD2": r"armor\m\cuirass_1.nif",
                                     b"MOD3": r"follower\f\vest_1.nif"},
                    {"armor/m/cuirass_1.nif", "follower/f/vest_1.nif"})
    assert st["armo_targets"] == 1 and st["female_guard_skipped"] == []
    assert _minted(out)[0][b"MOD3"] == "!UBE\\follower\\f\\vest_1.nif"


def test_a_shared_armature_is_counted_once(tmp_path):
    """One cuirass armature is usually shared by its enchanted variants; the
    report says how many ARMATURES were not minted, not armour-armature pairs."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=_payload(
        {b"MOD2": r"armor\m\cuirass_1.nif", b"MOD3": r"follower\f\vest_1.nif"}, BODY))
    armos = []
    for fid in (0x01000801, 0x01000802):
        p = encode_subrecord(b"EDID", encode_zstring("Piece"))
        p += encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        p += encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
        armos.append(Record(sig=b"ARMO", flags=0, formid=fid, payload=p))
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]), Group(label=b"ARMO", records=armos)])
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], converted_rel_paths={"armor/m/cuirass_1.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True)
    assert st["female_guard_skipped"] == ["mod.esp|800"]
    assert len(st["female_guard_dropped"]) == 2


def test_the_gauntlets_armature_of_a_body_armour_keeps_its_own_mesh(tmp_path):
    """The skip is per ARMATURE: a cuirass-and-gauntlets armour's hands armature
    is hands, so it is minted with its own female path, not skipped."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    body = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=_payload(
        {b"MOD2": r"armor\m\cuirass_1.nif", b"MOD3": r"follower\f\vest_1.nif"}, BODY))
    hands = Record(sig=b"ARMA", flags=0, formid=0x01000802, payload=_payload(
        {b"MOD2": r"armor\m\gauntlets_1.nif", b"MOD3": r"follower\f\gloves_1.nif"},
        1 << 3))
    p = encode_subrecord(b"EDID", encode_zstring("Piece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", BODY | (1 << 3), 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MODL", struct.pack("<I", 0x01000800))
    p += encode_subrecord(b"MODL", struct.pack("<I", 0x01000802))
    armo = Record(sig=b"ARMO", flags=0, formid=0x01000801, payload=p)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[body, hands]),
                 Group(label=b"ARMO", records=[armo])])
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod],
        converted_rel_paths={"armor/m/cuirass_1.nif", "armor/m/gauntlets_1.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=True)
    assert st["female_guard_skipped"] == ["mod.esp|800"]
    assert st["armo_targets"] == 1 and st["female_guard_dropped"] == []
    assert [m[b"MOD3"] for m in _minted(out)] == [r"follower\f\gloves_1.nif"]


def test_a_body_piece_whose_female_path_is_dead_keeps_the_male_mesh(tmp_path):
    st, out = _body(tmp_path, BODY, {b"MOD2": r"armor\m\cuirass_1.nif",
                                     b"MOD3": r"follower\f\missing_1.nif"},
                    {"armor/m/cuirass_1.nif"}, female_mesh_exists=lambda p: False)
    assert st["armo_targets"] == 1 and st["female_guard_skipped"] == []
    assert _minted(out)[0][b"MOD3"] == "!UBE\\armor\\m\\cuirass_1.nif"
    assert [d["dead"] for d in st["female_dead_male"]] == [r"follower\f\missing_1.nif"]


def test_a_male_only_body_piece_is_still_minted(tmp_path):
    """No female model at all: the male mesh is what the engine draws."""
    st, out = _body(tmp_path, BODY, {b"MOD2": r"armor\m\cuirass_1.nif"},
                    {"armor/m/cuirass_1.nif"})
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == "!UBE\\armor\\m\\cuirass_1.nif"


def test_an_accessory_keeps_its_own_female_mesh(tmp_path):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, HEAD, {b"MOD2": r"armor\m\belt_1.nif",
                                     b"MOD3": r"follower\f\belt_1.nif"}),
        converted_rel_paths={"armor/m/belt_1.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, preserve_textures=True)
    assert st["armo_targets"] == 1
    assert _minted(out)[0][b"MOD3"] == r"follower\f\belt_1.nif"
    assert len(st["female_kept"]) == 1


# ------------------------------------------------------ the existence lookup

def _lookup_modlist(tmp_path, monkeypatch):
    from src import auto_convert as ac
    from tests.test_bsa_seek_read import _write_bsa
    mods = tmp_path / "mods"
    loose = mods / "Loose Mod" / "meshes" / "follower" / "f" / "loose_1.nif"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"x")
    _write_bsa(mods / "Packed Mod" / "Packed - Textures.bsa",
               [(r"meshes\follower\f", "packed_1.nif", b"x", None)])
    _write_bsa(mods / "Packed Mod" / "Packed - Voices.bsa",
               [(r"meshes\follower\f", "voiced_1.nif", b"x", None)])
    out = mods / "Out"
    out.mkdir()

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: ["Out", "Packed Mod", "Loose Mod"])
    return ac._mesh_exists_anywhere(out)


def test_the_lookup_finds_loose_and_texture_archive_meshes(tmp_path, monkeypatch):
    exists = _lookup_modlist(tmp_path, monkeypatch)
    assert exists(r"follower\f\loose_1.nif")
    assert exists(r"Meshes\Follower\F\Packed_1.nif"), \
        "a mesh in a texture-named archive still loads in game"
    assert not exists(r"follower\f\missing_1.nif")
    assert not exists("")


def test_the_texture_archive_switch_does_not_blind_the_lookup(tmp_path, monkeypatch):
    """CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES restores the converter's old SOURCE
    listing; whether a mesh exists in game is a different question, and a mesh
    in a texture-named archive still loads."""
    monkeypatch.setenv("CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES", "1")
    exists = _lookup_modlist(tmp_path, monkeypatch)
    assert exists(r"follower\f\packed_1.nif")
    assert not exists(r"follower\f\voiced_1.nif")


def test_the_lookup_finds_a_mesh_built_into_overwrite(tmp_path, monkeypatch):
    """BodySlide run through MO2 writes to overwrite; a mesh there exists."""
    from src import auto_convert as ac
    ow = tmp_path / "overwrite" / "meshes" / "follower" / "f" / "built_1.nif"
    ow.parent.mkdir(parents=True)
    ow.write_bytes(b"x")
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: tmp_path / "overwrite")
    exists = _lookup_modlist(tmp_path, monkeypatch)
    assert exists(r"follower\f\built_1.nif")


def test_an_unreadable_folder_does_not_stop_the_lookup(tmp_path, monkeypatch):
    import pathlib
    exists = _lookup_modlist(tmp_path, monkeypatch)
    real = pathlib.Path.is_file

    def is_file(self):
        if "Packed Mod" in str(self):
            raise PermissionError("access denied")
        return real(self)
    monkeypatch.setattr(pathlib.Path, "is_file", is_file)
    assert exists(r"follower\f\loose_1.nif")
    assert not exists(r"follower\f\missing_1.nif")


@pytest.mark.parametrize("utf8_rebuild", [False, True],
                         ids=["path-bytes", "utf8-rebuild"])
def test_the_lookup_gets_a_non_ascii_path_as_the_game_reads_it(
        monkeypatch, utf8_rebuild):
    """Model paths are cp1252; the utf-8 decode the rebuild uses with
    CBBE2UBE_NO_ARMA_PATH_BYTES drops the byte, and the mesh would read as
    dead -- the probe must not reuse that decode. Since #arma-path-bytes the
    rebuild's own path is a cp1252 read too, so only the utf8-rebuild case
    still decides what the lookup is asked (the case pair CFG-n names)."""
    monkeypatch.delenv("CBBE2UBE_NO_ARMA_PATH_BYTES", raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_MODEL_PATH_CODEPAGE", raising=False)
    if utf8_rebuild:
        monkeypatch.setenv("CBBE2UBE_NO_ARMA_PATH_BYTES", "1")
    asked = []
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    p += encode_subrecord(b"MOD2", encode_zstring(MALE))
    p += encode_subrecord(b"MOD3", "follower\\f\\caf\xe9_1.nif".encode("cp1252") + b"\x00")
    up.rebuild_arma_payload(
        p, new_primary_rnam=1, new_additional_race_fids=[],
        converted_nif_exists=lambda s: s.lower() == MALE.lower(),
        keep_named_female=True,
        female_mesh_exists=lambda s: asked.append(s) or True)
    assert asked == ["follower\\f\\caf\xe9_1.nif"]


def test_the_lookup_skips_voice_archives(tmp_path, monkeypatch):
    """Negative control: the archive filter is live, so the texture-archive hit
    above is the skip list's doing."""
    exists = _lookup_modlist(tmp_path, monkeypatch)
    assert not exists(r"follower\f\voiced_1.nif")


def test_no_modlist_means_no_lookup(monkeypatch, tmp_path):
    from src import auto_convert as ac
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: None)
    assert ac._mesh_exists_anywhere(tmp_path) is None
