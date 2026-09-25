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

r"""#exclude-body-only -- an excluded mod loses only its BODY pieces' coverage.

THE DEFECT. #exclude-owned-coverage withheld every armour an excluded mod
defines, in both passes. The user excludes a mod to keep our converted meshes
off its body pieces; its eyeglasses and a helmet, which no other mod patches,
then drew nothing on UBE actors.

THE RULE (user, 2026-09-24). The body pass still withholds all of it. The
non-body pass withholds an owned armour only when
  * another mod patches it, read WITHOUT our patch reader: a SkyPatcher armor
    INI line (any depth) that adds addons names it, or a loaded plugin's
    override of it adds an armature -- so the exclusion stays the fallback for
    a hand-made refit our reader does not recognise; or
  * an armature the pass would mint (every one, race-list ones included) is
    conversion territory by the planner's own test: a body slot, a cloak name,
    a body-candidate slot whose loose world mesh is body-fit or cannot be read,
    or a converted/hand-made UBE mesh of a path the excluded mod ships.
Otherwise it is minted with its own mesh. No modlist to check: withheld.
`CBBE2UBE_NO_EXCLUDE_BODY_ONLY=1` withholds all of it again.
"""
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

OFF = "CBBE2UBE_NO_EXCLUDE_BODY_ONLY"
PARENT_OFF = "CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE"
DEFAULT = 0x00000019
BRETON, NORD = 0x013741, 0x013746

HEAD = 1 << 0          # slot 30
EYES = 1 << 14         # slot 44, a body-candidate slot
CHEST2 = 1 << 16       # slot 46, a body slot
LEG = 1 << 24          # slot 54, a body slot
FEET = 1 << 7          # slot 37

OWNED = ("follower.esp", 0x801)
MESH = r"follower\f\glasses_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(PARENT_OFF, raising=False)


def _save(path, masters, groups):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _arma(formid, slots=HEAD, models=None, primary=DEFAULT, races=(DEFAULT,)):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary))
    for sig, path in (models or {b"MOD3": MESH}).items():
        p += encode_subrecord(sig, encode_zstring(path))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots, armas, edid="FollowerGlasses"):
    q = encode_subrecord(b"EDID", encode_zstring(edid))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=q)


def _modlist(tmp_path, slots=HEAD, models=None, primary=DEFAULT,
             races=(DEFAULT,), extra=()):
    """Skyrim.esm and UBE_AllRace.esp, then mods/Follower Mod/Follower.esp:
    armour Follower.esp|000801 over its armature 000800. `extra` are more
    plugin paths loaded after it. Returns (mods root, plugin paths)."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    mods = tmp_path / "mods"
    fol = _save(mods / "Follower Mod" / "Follower.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[_arma(0x01000800, slots, models,
                                                     primary, races)]),
                 Group(label=b"ARMO", records=[_armo(0x01000801, slots,
                                                     [0x01000800])])])
    return mods, [sky, ube, fol, *extra]


def _probe(mods, *extra_mods):
    return ac._ExclusionKeepProbe(mods, ["Follower Mod", *extra_mods])


def _nonbody(tmp_path, plugins, probe, *, covered=None, conv=None, twin=None):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    return up.generate_modded_nonbody_ube_coverage_patch(
        out, plugins, converted_rel_paths=conv or set(),
        exclude_names={out.name.lower()}, exclude_armo_abs=covered,
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
        withheld_armo_abs={OWNED}, exclusion_probe=probe,
        ube_twin_exists=twin), out


def _mod3(out):
    e = ESP.load(out)
    return [v.rstrip(b"\x00").decode() for g in e.groups if g.label == b"ARMA"
            for r in g.records for s, v in iter_subrecords(r.payload)
            if s == b"MOD3"]


def _kept(st):
    return [a for a, _e in st["exclusion_nonbody_kept"]]


def _held(st):
    return [a for a, _e in st["withheld"]]


# ------------------------------------------------- kept with its own mesh

def test_an_owned_accessory_keeps_its_own_mesh(tmp_path):
    mods, plugins = _modlist(tmp_path)
    st, out = _nonbody(tmp_path, plugins, _probe(mods))
    assert st["armo_targets"] == 1
    assert _kept(st) == [OWNED] and st["withheld"] == []
    assert _mod3(out) == [MESH]


def test_switched_off_everything_owned_is_withheld(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods, plugins = _modlist(tmp_path)
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert st["armo_targets"] == 0
    assert _held(st) == [OWNED] and st["exclusion_nonbody_kept"] == []


def test_the_parent_switch_turns_the_rule_off_too(monkeypatch):
    """Nested: with #exclude-owned-coverage off nothing is withheld, and this
    rule has nothing left to relax."""
    assert up._exclude_body_only() is True
    monkeypatch.setenv(PARENT_OFF, "1")
    assert up._exclude_body_only() is False


# ------------------------------------------------------ conversion territory

def test_an_owned_body_slot_piece_is_withheld(tmp_path):
    mods, plugins = _modlist(tmp_path, slots=LEG)
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert st["armo_targets"] == 0 and _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "body slot"


def test_the_armour_slots_count_when_the_armature_has_none(tmp_path):
    """BOD2 of the armature, else the armour's -- as in the planner."""
    mods, plugins = _modlist(tmp_path, slots=LEG)
    e = ESP.load(plugins[2])
    arma = e.groups[0].records[0]
    arma.payload = b"".join(encode_subrecord(s, d) for s, d in iter_subrecords(arma.payload)
                            if s != b"BOD2")
    e.save(plugins[2])
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _held(st) == [OWNED]


def test_a_cloak_named_piece_is_withheld(tmp_path):
    mods, plugins = _modlist(tmp_path, models={b"MOD3": r"follower\f\hoodcloak_1.nif"})
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "cloak"


def test_a_candidate_slot_piece_with_no_loose_mesh_is_withheld(tmp_path):
    """Slot 44 is body cloth in some mods: the mesh decides, and a mesh that
    cannot be read counts as body (fail closed)."""
    mods, plugins = _modlist(tmp_path, slots=EYES)
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2].startswith("body-candidate slot")


def test_a_converted_mesh_of_the_excluded_mod_is_withheld(tmp_path):
    """A stale conversion of the excluded mod's own mesh must not be drawn."""
    mods, plugins = _modlist(tmp_path)
    loose = mods / "Follower Mod" / "meshes" / "follower" / "f" / "glasses_1.nif"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"x")
    st, _out = _nonbody(tmp_path, plugins, _probe(mods),
                        conv={"follower/f/glasses_1.nif"})
    assert _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "draws a converted mesh of its own"


def test_a_converted_mesh_in_the_excluded_mods_archive_is_withheld(tmp_path):
    from tests.test_bsa_seek_read import _write_bsa
    mods, plugins = _modlist(tmp_path)
    _write_bsa(mods / "Follower Mod" / "Follower.bsa",
               [(r"meshes\follower\f", "glasses_1.nif", b"nif bytes", None)])
    st, _out = _nonbody(tmp_path, plugins, _probe(mods),
                        conv={"follower/f/glasses_1.nif"})
    assert _held(st) == [OWNED]


def test_a_converted_shared_mesh_does_not_hide_the_piece(tmp_path):
    """The same converted-mesh redirect for a path another mod ships (a base
    game helmet): not the excluded mod's mesh, so the piece keeps coverage and
    draws that converted mesh."""
    shared = r"armor\iron\f\helmet_1.nif"
    mods, plugins = _modlist(tmp_path, models={b"MOD3": shared})
    other = mods / "Other Mod" / "meshes" / "armor" / "iron" / "f" / "helmet_1.nif"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"x")
    st, out = _nonbody(tmp_path, plugins, _probe(mods, "Other Mod"),
                       conv={"armor/iron/f/helmet_1.nif"})
    assert _kept(st) == [OWNED]
    assert _mod3(out) == ["!UBE\\" + shared]


def test_a_race_list_armature_on_a_body_slot_is_withheld(tmp_path):
    """Every armature the pass would mint is judged, not only DefaultRace
    ones: here the race-list rule takes a Breton-primary armature (a chest
    piece on slot 46)."""
    mods, plugins = _modlist(tmp_path, slots=CHEST2,
                             models={b"MOD3": r"follower\f\sash_1.nif"},
                             primary=BRETON, races=(BRETON, NORD))
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _held(st) == [OWNED] and st["race_listed"] == []
    assert st["exclusion_nonbody_held"][0][2] == "body slot"


def test_the_race_list_armature_of_an_accessory_is_kept(tmp_path):
    """Control for the test above: the same Breton-primary armature on a head
    slot is minted, so that test's hold is the body slot."""
    mods, plugins = _modlist(tmp_path, slots=HEAD, primary=BRETON,
                             races=(BRETON, NORD))
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _kept(st) == [OWNED] and [a for a, _e in st["race_listed"]] == [OWNED]


needs_pynifly = pytest.mark.skipif(
    not __import__("tests.synthetic_nif", fromlist=["x"]).pynifly_available(),
    reason="pynifly not available")


def _nif(tmp_path, name, bones):
    from tests.synthetic_nif import build_skinned_shape_nif
    return build_skinned_shape_nif(tmp_path / name, bones=bones).read_bytes()


@needs_pynifly
def test_a_candidate_slot_mesh_is_read(tmp_path):
    """Eyeglasses on slot 44 come back only because their loose mesh is not
    skinned to a body-fit bone; the same slot with a thigh-skinned mesh is
    body cloth; the same mesh only in an archive is not read (fail closed)."""
    from tests.test_bsa_seek_read import _write_bsa
    head = _nif(tmp_path, "h.nif", ("NPC Head [Head]", "NPC Neck [Neck]"))
    thigh = _nif(tmp_path, "t.nif", ("NPC Spine [Spn0]", "NPC L Thigh [LThg]"))
    for case, data, packed, want_kept in (("head", head, False, True),
                                          ("thigh", thigh, False, False),
                                          ("packed", head, True, False)):
        base = tmp_path / case
        mods, plugins = _modlist(base, slots=EYES)
        if packed:
            _write_bsa(mods / "Follower Mod" / "Follower.bsa",
                       [(r"meshes\follower\f", "glasses_1.nif", data, None)])
        else:
            f = mods / "Follower Mod" / "meshes" / "follower" / "f" / "glasses_1.nif"
            f.parent.mkdir(parents=True)
            f.write_bytes(data)
        st, _out = _nonbody(base, plugins, _probe(mods))
        assert (_kept(st) == [OWNED]) is want_kept, case


# ------------------------------------------------- another mod patches it

def _ini(mods, mod, text, rel="Follower/Follower.esp.ini"):
    f = mods / mod / "SKSE" / "Plugins" / "SkyPatcher" / "armor" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


@pytest.mark.parametrize("target", [
    pytest.param("Follower.esp|801", id="plain"),
    pytest.param("Follower.esp|0x00000801", id="hex-prefix"),
    pytest.param("follower.esp|00000801", id="leading-zeros"),
    pytest.param("Follower.esp|FE012801", id="esl-form"),
    pytest.param("FollowerGlasses", id="editor-id"),
])
def test_a_refit_our_reader_misses_still_withholds_the_piece(tmp_path, target):
    """The structured reader did not recognise the refit (nothing in the
    covered set) -- a nested INI, an addon that is no UBE path. The raw text
    scan still sees a line adding addons that names the armour, by any
    spelling of its FormID or by its EditorID, and the piece stays withheld."""
    mods, plugins = _modlist(tmp_path)
    _ini(mods, "Refit Mod",
         f"filterByArmors={target}:armorAddonsToAdd=Refit.esp|800\n")
    st, _out = _nonbody(tmp_path, plugins, _probe(mods, "Refit Mod"), covered=set())
    assert st["armo_targets"] == 0 and _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "named by Refit Mod"


def test_a_line_adding_no_addons_does_not_count(tmp_path):
    mods, plugins = _modlist(tmp_path)
    _ini(mods, "Keyword Mod", "filterByArmors=Follower.esp|801:keywordsToAdd=Skyrim.esm|6BBE8\n")
    st, _out = _nonbody(tmp_path, plugins, _probe(mods, "Keyword Mod"))
    assert _kept(st) == [OWNED]


def test_our_own_output_naming_it_does_not_count(tmp_path):
    """Our Combined INI names every armour we cover; it is not a third party."""
    mods, plugins = _modlist(tmp_path)
    _ini(mods, "Converter Output",
         "filterByArmors=Follower.esp|801:armorAddonsToAdd=Out.esp|800\n",
         rel="CBBE_to_UBE_Combined.ini")
    (mods / "Converter Output" / "conversion_report.json").write_text("{}", encoding="utf-8")
    st, _out = _nonbody(tmp_path, plugins, _probe(mods, "Converter Output"))
    assert _kept(st) == [OWNED]


def test_a_disabled_mod_naming_it_does_not_count(tmp_path):
    mods, plugins = _modlist(tmp_path)
    _ini(mods, "Disabled Refit",
         "filterByArmors=Follower.esp|801:armorAddonsToAdd=Refit.esp|800\n")
    st, _out = _nonbody(tmp_path, plugins, _probe(mods))
    assert _kept(st) == [OWNED]


def _override(tmp_path, armas, extra_arma=None):
    groups = []
    if extra_arma is not None:
        groups.append(Group(label=b"ARMA", records=[extra_arma]))
    groups.append(Group(label=b"ARMO", records=[_armo(0x01000801, HEAD, armas)]))
    return _save(tmp_path / "mods" / "Refit Mod" / "Refit.esp",
                 ["Skyrim.esm", "Follower.esp"], groups)


def test_an_override_that_adds_an_armature_withholds_the_piece(tmp_path):
    """A plugin refit: its override of the armour adds its own armature (no
    UBE path our reader would know). Its armature wins no DefaultRace test
    here, so only the override test can see the refit."""
    extra = _arma(0x02000800, HEAD, primary=0x01005734, races=())
    refit = _override(tmp_path, [0x01000800, 0x02000800], extra_arma=extra)
    mods, plugins = _modlist(tmp_path, extra=[refit])
    st, _out = _nonbody(tmp_path, plugins, _probe(mods, "Refit Mod"), covered=set())
    assert _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "refit.esp adds an armature"


def test_an_override_repeating_the_armatures_does_not_count(tmp_path):
    """An overhaul patch that forwards the armour's own list is no refit."""
    patch = _override(tmp_path, [0x01000800])
    mods, plugins = _modlist(tmp_path, extra=[patch])
    st, _out = _nonbody(tmp_path, plugins, _probe(mods, "Refit Mod"))
    assert _kept(st) == [OWNED]


def test_a_piece_our_reader_covers_is_never_minted_nor_kept(tmp_path):
    """Refit recognised: the covered check still comes first."""
    mods, plugins = _modlist(tmp_path)
    st, _out = _nonbody(tmp_path, plugins, _probe(mods), covered={OWNED})
    assert st["armo_targets"] == 0
    assert st["withheld"] == [] and st["exclusion_nonbody_kept"] == []


# ------------------------------------------------------------- the modlist

def test_no_modlist_withholds_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_exclusion_keep_probe", lambda: None)
    mods, plugins = _modlist(tmp_path)
    st, _out = _nonbody(tmp_path, plugins, None)
    assert _held(st) == [OWNED]
    assert st["exclusion_nonbody_held"][0][2] == "no modlist to check"


def test_the_probe_is_built_once_from_the_modlist_when_needed(tmp_path, monkeypatch):
    mods, plugins = _modlist(tmp_path)
    built = []

    def _build():
        built.append(1)
        return _probe(mods)
    monkeypatch.setattr(ac, "_exclusion_keep_probe", _build)
    st, _out = _nonbody(tmp_path, plugins, None)
    assert _kept(st) == [OWNED] and built == [1]


def test_the_modlist_is_not_read_without_an_excluded_piece(tmp_path, monkeypatch):
    def _refuse():
        raise AssertionError("the modlist was read")
    monkeypatch.setattr(ac, "_exclusion_keep_probe", _refuse)
    _mods, plugins = _modlist(tmp_path)
    out = tmp_path / "nb.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, plugins, exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True)
    assert st["armo_targets"] == 1


def test_the_probe_reads_the_active_modlist(tmp_path, monkeypatch):
    mods, plugins = _modlist(tmp_path)
    _ini(mods, "Refit Mod",
         "filterByArmors=Follower.esp|801:armorAddonsToAdd=Refit.esp|800\n")

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: ["Refit Mod", "Follower Mod"])
    probe = ac._exclusion_keep_probe()
    assert probe.named(OWNED) == "Refit Mod"
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: None)
    assert ac._exclusion_keep_probe() is None


# ------------------------------------------------------------ the body pass

def test_the_body_pass_still_withholds_an_owned_piece(tmp_path):
    mods, plugins = _modlist(tmp_path, slots=FEET,
                             models={b"MOD3": r"follower\f\boots_1.nif"})
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, plugins, converted_rel_paths={"follower/f/boots_1.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, withheld_armo_abs={OWNED})
    assert st["armo_targets"] == 0 and _held(st) == [OWNED]


# ------------------------------------------------------------------ report

def test_the_kept_pieces_are_reported(capsys):
    ac._report_coverage_holds([{
        "exclusion_nonbody_kept": [(("follower.esp", 0x801), "FollowerGlasses")],
        "withheld": [(("follower.esp", 0x802), "FollowerBoots")]}])
    text = capsys.readouterr().out
    line = next(l for l in text.splitlines() if "keep their own mesh" in l)
    assert line.lstrip().startswith("NOTE:")
    assert "1 non-body armour(s) of an excluded mod" in line
    assert "FollowerGlasses" in text and "follower.esp|000801" in text
    assert "FollowerBoots" in text, "the withheld ones are still listed"
