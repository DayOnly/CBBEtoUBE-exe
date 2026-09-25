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

"""#coverage-female-standin -- what a coverage armature's female slot draws when
the path it names exists nowhere.

THE RULE (user, 2026-09-25): "the vanilla female counterpart where the mapping
is unambiguous; otherwise keep the male mesh". A dead MOD3 (paired with MOD2)
or MOD5 (paired with MOD4) takes the converted female mesh that a vanilla
armature -- game master or Creation Club, DefaultRace-primary -- pairs with the
same male path, when there is exactly one. Else the male mesh: converted, as
#coverage-female-guard already did; or, on a NON-BODY armature, the unconverted
male path as it is. A body piece, a cloak, or a male mesh skinned to a body-fit
bone (or unreadable) keeps the dead path.

Live, before: 8 slots drew a converted MALE mesh, a cuirass was not minted (dead
female world mesh, male never converted), its gloves kept the dead path, and
two hoods and a helmet drew nothing.
"""
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

DEFAULT = 0x00000019
OTHER_RACE = 0x00013745
HEAD = 1 << 0               # slot 30, non-deforming, not a body slot
BODY = 1 << 2               # slot 32
HANDS = 1 << 3              # slot 33
FEET = 1 << 7               # slot 37
SLOT52 = 1 << 22            # a strict body slot outside the deforming set
OFF = "CBBE2UBE_NO_COVERAGE_FEMALE_STANDIN"

# The vanilla pairs (male -> female) and the mod's dead female paths.
V_BOOTS_M, V_BOOTS_F = r"armor\v\m\boots_1.nif", r"armor\v\f\boots_1.nif"
V_BODY_M, V_BODY_F = r"armor\v\m\cuirass_1.nif", r"armor\v\f\cuirass_1.nif"
V_1ST_M, V_1ST_F = r"armor\v\m\1stcuirass_1.nif", r"armor\v\f\1stcuirass_1.nif"
V_HANDS_M, V_HANDS_F = r"armor\v\m\gauntlets_1.nif", r"armor\v\f\gauntlets_1.nif"
DEAD = r"follower\f\gone_1.nif"
DEAD_1ST = r"follower\f\1stgone_1.nif"
HOOD_M = r"follower\m\hood_1.nif"


def _conv(*paths):
    return {p.replace("\\", "/").lower() for p in paths}


def _payload(models, slots=FEET, rnam=DEFAULT, mo3t=True):
    p = encode_subrecord(b"EDID", encode_zstring("AA"))
    if slots is not None:
        p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", rnam))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
            if sig in (b"MOD3", b"MOD5") and mo3t:
                p += encode_subrecord(b"MO3T" if sig == b"MOD3" else b"MO5T",
                                      struct.pack("<III", 0, 0, 0))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    return p


def _models(payload):
    return {s: d.rstrip(b"\x00").decode() for s, d in iter_subrecords(payload)
            if s in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")}


def _sigs(payload):
    return [s for s, _d in iter_subrecords(payload)]


def _save(path, masters, groups):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _armo(fid, slots, armas):
    p = encode_subrecord(b"EDID", encode_zstring("Piece"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        p += encode_subrecord(b"MODL", struct.pack("<I", a))
    return Record(sig=b"ARMO", flags=0, formid=fid, payload=p)


def _world(tmp_path, armas, armos, vanilla=(), other=(),
           vanilla_name="Skyrim.esm"):
    """`armas` {fid: (models, slots)} and `armos` [(fid, slots, [arma fids])] in
    Mod.esp; `vanilla` [(models, rnam)] armatures DEFINED by `vanilla_name`;
    `other` [(models, rnam)] armatures defined by another mod's plugin."""
    sky_recs = []
    cc = None
    for i, (models, rnam) in enumerate(vanilla):
        rec = Record(sig=b"ARMA", flags=0, formid=0x00000D00 + i,
                     payload=_payload(models, BODY, rnam=rnam, mo3t=False))
        sky_recs.append(rec)
    if vanilla_name.lower() == "skyrim.esm":
        sky = _save(tmp_path / "Skyrim.esm", [],
                    [Group(label=b"ARMA", records=sky_recs)] if sky_recs else [])
    else:
        sky = _save(tmp_path / "Skyrim.esm", [], [])
        cc = _save(tmp_path / vanilla_name, ["Skyrim.esm"],
                   [Group(label=b"ARMA", records=[
                       Record(sig=b"ARMA", flags=0, formid=r.formid | 0x01000000,
                              payload=r.payload) for r in sky_recs])])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    plugins = [sky] + ([cc] if cc else []) + [ube]
    if other:
        plugins.append(_save(tmp_path / "Other.esp", ["Skyrim.esm"], [Group(
            label=b"ARMA", records=[
                Record(sig=b"ARMA", flags=0, formid=0x01000D00 + i,
                       payload=_payload(m, BODY, rnam=r, mo3t=False))
                for i, (m, r) in enumerate(other)])]))
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"], [
        Group(label=b"ARMA", records=[
            Record(sig=b"ARMA", flags=0, formid=fid, payload=_payload(m, s))
            for fid, (m, s) in armas.items()]),
        Group(label=b"ARMO", records=[_armo(f, s, a) for f, s, a in armos])])
    return plugins + [mod]


def _lookup(live=(), fit=None):
    """A mesh-exists lookup: `live` paths exist; `fit` {path: True/False/None}
    is what reading each mesh says (None = unreadable). No `fit`: the lookup
    cannot read meshes at all."""
    keys = {ac_key(p) for p in live}

    def exists(p):
        return ac_key(p) in keys
    if fit is not None:
        fits = {ac_key(k): v for k, v in fit.items()}
        exists.body_fit = lambda p: fits.get(ac_key(p))
    return exists


def ac_key(p):
    return up._model_key(p)


def _body(tmp_path, armas, armos, conv, vanilla=(), lookup=None, **kw):
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, _world(tmp_path, armas, armos, vanilla, **kw),
        converted_rel_paths=conv, exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, cover_hands_feet=True,
        preserve_textures=True,
        female_mesh_exists=lookup if lookup is not None else _lookup())
    return st, out


def _nonbody(tmp_path, armas, armos, conv=frozenset(), vanilla=(), lookup=None):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    st = up.generate_modded_nonbody_ube_coverage_patch(
        out, _world(tmp_path, armas, armos, vanilla),
        converted_rel_paths=set(conv), exclude_names={out.name.lower()},
        master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
        female_mesh_exists=lookup if lookup is not None else _lookup())
    return st, out


def _minted(out):
    e = ESP.load(out)
    return [r.payload for g in e.groups if g.label == b"ARMA" for r in g.records]


def _boots(tmp_path, vanilla, conv=None, lookup=None, **kw):
    """Boots whose male mesh is the vanilla male boots (converted) and whose
    female path is dead."""
    return _body(tmp_path, {0x01000800: ({b"MOD2": V_BOOTS_M, b"MOD3": DEAD}, FEET)},
                 [(0x01000801, FEET, [0x01000800])],
                 conv if conv is not None else _conv(V_BOOTS_M, V_BOOTS_F),
                 vanilla, lookup, **kw)


VANILLA_BOOTS = [({b"MOD2": V_BOOTS_M, b"MOD3": V_BOOTS_F}, DEFAULT)]


# ------------------------------------------------------------------ the stand-in

def test_a_dead_female_slot_draws_the_vanilla_female_counterpart(tmp_path):
    st, out = _boots(tmp_path, VANILLA_BOOTS)
    (p,) = _minted(out)
    assert _models(p)[b"MOD3"] == "!UBE\\" + V_BOOTS_F
    assert b"MO3T" not in _sigs(p), "the dead mesh's texture hash must go"
    assert [(d["slot"], d["orig"]) for d in st["female_standin"]] == [("MOD3", DEAD)]
    assert st["female_dead_male"] == []


def test_an_ambiguous_counterpart_keeps_the_converted_male(tmp_path):
    """Two different vanilla female meshes for one male path: no stand-in."""
    st, out = _boots(tmp_path, VANILLA_BOOTS + [
        ({b"MOD2": V_BOOTS_M, b"MOD3": r"armor\v\f\boots2_1.nif"}, DEFAULT)],
        conv=_conv(V_BOOTS_M, V_BOOTS_F, r"armor\v\f\boots2_1.nif"))
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == [] and len(st["female_dead_male"]) == 1


def test_one_counterpart_spelt_two_ways_is_not_ambiguous(tmp_path):
    st, out = _boots(tmp_path, VANILLA_BOOTS + [
        ({b"MOD2": V_BOOTS_M, b"MOD3": "Meshes\\" + V_BOOTS_F.upper()}, DEFAULT)])
    assert _models(_minted(out)[0])[b"MOD3"].lower() == "!ube\\" + V_BOOTS_F


def test_an_unconverted_counterpart_is_not_used(tmp_path):
    """A counterpart that was not converted would draw a CBBE mesh on UBE."""
    st, out = _boots(tmp_path, VANILLA_BOOTS, conv=_conv(V_BOOTS_M))
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == []


def test_a_non_default_race_vanilla_armature_is_no_counterpart(tmp_path):
    st, out = _boots(tmp_path, [({b"MOD2": V_BOOTS_M, b"MOD3": V_BOOTS_F},
                                 OTHER_RACE)])
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == []


def test_a_mod_defined_armature_is_no_counterpart(tmp_path):
    st, out = _boots(tmp_path, (), other=VANILLA_BOOTS)
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == []


def test_a_creation_club_master_is_vanilla(tmp_path):
    st, out = _boots(tmp_path, VANILLA_BOOTS, vanilla_name="ccTestPack.esl")
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_F


def test_the_first_person_slot_pairs_with_the_first_person_male(tmp_path):
    """MOD5 is looked up by MOD4 in the MOD4 -> MOD5 map, never by MOD2."""
    vanilla = [({b"MOD2": V_BOOTS_M, b"MOD3": V_BOOTS_F,
                 b"MOD4": V_1ST_M, b"MOD5": V_1ST_F}, DEFAULT)]
    st, out = _body(
        tmp_path, {0x01000800: ({b"MOD2": V_BOOTS_M, b"MOD3": V_BOOTS_F,
                                 b"MOD4": V_1ST_M, b"MOD5": DEAD_1ST}, FEET)},
        [(0x01000801, FEET, [0x01000800])],
        _conv(V_BOOTS_M, V_BOOTS_F, V_1ST_M, V_1ST_F), vanilla)
    m = _models(_minted(out)[0])
    assert m[b"MOD5"] == "!UBE\\" + V_1ST_F
    assert m[b"MOD3"] == "!UBE\\" + V_BOOTS_F
    assert [d["slot"] for d in st["female_standin"]] == ["MOD5"]


def test_a_live_named_female_path_is_never_replaced(tmp_path):
    st, out = _boots(tmp_path, VANILLA_BOOTS, lookup=_lookup(live=[DEAD]))
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD
    assert st["female_standin"] == [] and len(st["female_kept"]) == 1


def test_a_torso_whose_male_was_never_converted_is_minted_with_the_stand_in(tmp_path):
    """Its female world mesh is dead and its male mesh was never converted (the
    planner converted only the first-person female), so the world-mesh rule
    dropped it. The stand-in is a converted female world mesh."""
    vanilla = [({b"MOD2": V_BODY_M, b"MOD3": V_BODY_F}, DEFAULT)]
    st, out = _body(
        tmp_path, {0x01000800: ({b"MOD2": V_BODY_M, b"MOD3": DEAD,
                                 b"MOD5": V_1ST_F}, BODY)},
        [(0x01000801, BODY, [0x01000800])], _conv(V_BODY_F, V_1ST_F), vanilla)
    assert st["armo_targets"] == 1 and st["world_mesh_skipped"] == []
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BODY_F


def test_gloves_whose_male_was_never_converted_take_the_stand_in(tmp_path):
    vanilla = [({b"MOD2": V_HANDS_M, b"MOD3": V_HANDS_F}, DEFAULT)]
    st, out = _body(
        tmp_path, {0x01000800: ({b"MOD2": V_HANDS_M, b"MOD3": DEAD}, HANDS)},
        [(0x01000801, HANDS, [0x01000800])], _conv(V_HANDS_F), vanilla)
    (p,) = _minted(out)
    assert _models(p)[b"MOD3"] == "!UBE\\" + V_HANDS_F
    assert b"MO3T" not in _sigs(p)


def test_switched_off_the_dead_slots_draw_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
    st, out = _boots(tmp_path / "a", VANILLA_BOOTS)
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == [] and len(st["female_dead_male"]) == 1
    st, out = _body(
        tmp_path / "b", {0x01000800: ({b"MOD2": V_BODY_M, b"MOD3": DEAD,
                                       b"MOD5": V_1ST_F}, BODY)},
        [(0x01000801, BODY, [0x01000800])], _conv(V_BODY_F, V_1ST_F),
        [({b"MOD2": V_BODY_M, b"MOD3": V_BODY_F}, DEFAULT)])
    assert st["armo_targets"] == 0 and st["world_mesh_skipped"] == ["mod.esp|800"]
    st, out = _body(
        tmp_path / "c", {0x01000800: ({b"MOD2": V_HANDS_M, b"MOD3": DEAD}, HANDS)},
        [(0x01000801, HANDS, [0x01000800])], _conv(V_HANDS_F),
        [({b"MOD2": V_HANDS_M, b"MOD3": V_HANDS_F}, DEFAULT)])
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD
    assert st["female_dead_kept"] == []


def test_with_the_guard_off_the_stand_in_is_off_too(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", "1")
    st, out = _boots(tmp_path, VANILLA_BOOTS)
    assert _models(_minted(out)[0])[b"MOD3"] == "!UBE\\" + V_BOOTS_M
    assert st["female_standin"] == []


# ------------------------------------------------------------ rebuild_arma_payload

def _rebuild(models, **kw):
    alt = encode_subrecord(b"MO3S", struct.pack("<I", 0))
    p = _payload(models)
    p = p.replace(encode_subrecord(b"MO3T", struct.pack("<III", 0, 0, 0)),
                  encode_subrecord(b"MO3T", struct.pack("<III", 0, 0, 0)) + alt)
    return up.rebuild_arma_payload(
        p, new_primary_rnam=1, new_additional_race_fids=[],
        alt_texture_fid_remap=lambda f: f,
        converted_nif_exists=lambda s: s.lower() == V_BOOTS_M, **kw)


def test_the_stand_in_is_not_a_male_fallback_to_undo():
    """restore_female_models undoes what male_fallback_log lists; a stand-in is
    not the male mesh, and must stay."""
    fb, log = [], []
    out = _rebuild({b"MOD2": V_BOOTS_M, b"MOD3": DEAD}, keep_named_female=True,
                   female_mesh_exists=lambda p: False, male_fallback_log=fb,
                   declined_log=log,
                   female_standin=lambda sig, male: V_BOOTS_F)
    assert _models(out)[b"MOD3"] == "!UBE\\" + V_BOOTS_F
    assert fb == []
    assert log == [{"slot": "MOD3", "standin": "!UBE\\" + V_BOOTS_F, "orig": DEAD}]
    assert b"MO3S" not in _sigs(out) and b"MO3T" not in _sigs(out)


def test_the_resolver_is_handed_the_male_source_path():
    asked = []
    _rebuild({b"MOD2": V_BOOTS_M, b"MOD3": DEAD, b"MOD4": V_1ST_M, b"MOD5": DEAD_1ST},
             keep_named_female=True, female_mesh_exists=lambda p: False,
             female_standin=lambda sig, male: asked.append((sig, male)))
    assert asked == [("MOD3", V_BOOTS_M), ("MOD5", V_1ST_M)]


# --------------------------------------------- else: a non-body piece keeps its male

def _hood(tmp_path, lookup, slots=HEAD, armo_slots=HEAD, male=HOOD_M,
          models=None):
    return _nonbody(
        tmp_path, {0x01000800: (models or {b"MOD2": male, b"MOD3": DEAD}, slots)},
        [(0x01000801, armo_slots, [0x01000800])], lookup=lookup)


def test_a_hood_with_a_dead_female_path_draws_its_male_mesh(tmp_path):
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: False}))
    (p,) = _minted(out)
    assert _models(p)[b"MOD3"] == HOOD_M, "non-body: the male path as it is"
    assert b"MO3T" not in _sigs(p)
    assert [(d["slot"], d["orig"]) for d in st["female_male_nonbody"]] == [("MOD3", DEAD)]


def test_an_armature_without_slots_takes_its_armours(tmp_path):
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: False}),
                    slots=None)
    assert _models(_minted(out)[0])[b"MOD3"] == HOOD_M


def test_a_body_fit_male_mesh_keeps_the_dead_path(tmp_path):
    """A slot-61 underskin is skinned to the body: its male mesh on a UBE body
    would clip."""
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: True}),
                    slots=1 << 31, armo_slots=1 << 31)
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD
    assert st["female_male_nonbody"] == []
    assert [(d["dead_kept"], d["male_live"]) for d in st["female_dead_kept"]] == [
        (DEAD, True)]


def test_an_unreadable_male_mesh_keeps_the_dead_path(tmp_path):
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: None}))
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD


def test_a_lookup_that_cannot_read_meshes_keeps_the_dead_path(tmp_path):
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M]))
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD


def test_a_cloak_keeps_the_dead_path(tmp_path):
    cape = r"follower\m\travel_cape_1.nif"
    st, out = _hood(tmp_path, _lookup(live=[cape], fit={cape: False}), male=cape)
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD


def test_a_strict_body_slot_keeps_the_dead_path(tmp_path):
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: False}),
                    slots=SLOT52, armo_slots=SLOT52)
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD


def test_a_dead_male_mesh_leaves_nothing_to_draw(tmp_path):
    st, out = _hood(tmp_path, _lookup(fit={HOOD_M: False}))
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD
    assert [(d["male"], d["male_live"]) for d in st["female_dead_kept"]] == [
        (HOOD_M, False)]


def test_switched_off_the_hood_keeps_the_dead_path(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, out = _hood(tmp_path, _lookup(live=[HOOD_M], fit={HOOD_M: False}))
    assert _models(_minted(out)[0])[b"MOD3"] == DEAD
    assert st["female_male_nonbody"] == [] and st["female_dead_kept"] == []


# ------------------------------------------------------------- reading the mesh

needs_pynifly = pytest.mark.skipif(
    not __import__("tests.synthetic_nif", fromlist=["x"]).pynifly_available(),
    reason="pynifly not available")


def _nif_bytes(tmp_path, name, bones):
    from tests.synthetic_nif import build_skinned_shape_nif
    return build_skinned_shape_nif(tmp_path / name, bones=bones).read_bytes()


@needs_pynifly
def test_the_mesh_reader_sees_the_skin_bones(tmp_path):
    thigh = _nif_bytes(tmp_path, "a.nif", ("NPC Spine [Spn0]", "NPC L Thigh [LThg]"))
    head = _nif_bytes(tmp_path, "b.nif", ("NPC Head [Head]", "NPC Neck [Neck]"))
    assert ac._nif_bytes_body_fit(thigh) is True
    assert ac._nif_bytes_body_fit(head) is False
    assert ac._nif_bytes_body_fit(b"not a nif") is None
    assert ac._nif_bytes_body_fit(head[:len(head) // 2]) is None


@needs_pynifly
def test_the_lookup_reads_a_loose_mesh_and_an_archived_one(tmp_path, monkeypatch):
    """The male mesh the game loads, loose or in an archive -- read into memory,
    never extracted."""
    from tests.test_bsa_seek_read import _write_bsa
    head = _nif_bytes(tmp_path, "h.nif", ("NPC Head [Head]", "NPC Neck [Neck]"))
    thigh = _nif_bytes(tmp_path, "t.nif", ("NPC Spine [Spn0]", "NPC L Thigh [LThg]"))
    mods = tmp_path / "mods"
    loose = mods / "Loose Mod" / "meshes" / "follower" / "m" / "hood_1.nif"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(head)
    _write_bsa(mods / "Packed Mod" / "Packed.bsa",
               [(r"meshes\follower\m", "skin_1.nif", thigh, "lz4"),
                (r"meshes\follower\m", "cowl_1.nif", head, None)])
    out = mods / "Out"
    out.mkdir()

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: ["Out", "Packed Mod", "Loose Mod"])
    before = sorted(p for p in tmp_path.rglob("*"))
    exists = ac._mesh_exists_anywhere(out)
    assert exists.body_fit(r"follower\m\hood_1.nif") is False
    assert exists.body_fit(r"Meshes\Follower\M\Skin_1.nif") is True
    assert exists.body_fit(r"follower\m\cowl_1.nif") is False
    assert exists.body_fit(r"follower\m\missing_1.nif") is None
    assert sorted(p for p in tmp_path.rglob("*")) == before, "nothing is written"


# ------------------------------------------------------------------- the report

def test_the_dead_slots_are_reported(capsys):
    ac._report_coverage_holds([{
        "female_standin": [{"arma": "mod.esp|800", "slot": "MOD3", "orig": DEAD,
                            "standin": "!UBE\\" + V_BOOTS_F}],
        "female_male_nonbody": [{"arma": "mod.esp|802", "slot": "MOD3",
                                 "orig": DEAD, "male_as_is": HOOD_M}],
        "female_dead_kept": [
            {"arma": "mod.esp|803", "slot": "MOD3", "dead_kept": DEAD,
             "male": "", "male_live": False},
            {"arma": "mod.esp|804", "slot": "MOD5", "dead_kept": DEAD_1ST,
             "male": "", "male_live": False},
            {"arma": "mod.esp|805", "slot": "MOD3", "dead_kept": DEAD,
             "male": HOOD_M, "male_live": True}]}])
    text = capsys.readouterr().out
    assert "1 female model slot(s) name a mesh that exists nowhere and draw the " \
           "vanilla female counterpart" in text
    assert f"MOD3 {DEAD}  (-> !UBE\\{V_BOOTS_F})" in text
    assert "1 female model slot(s) of non-body pieces name a mesh that exists " \
           "nowhere and draw their own male mesh" in text
    assert "NOTE" in text
    assert "3 female model slot(s) name a mesh that exists nowhere and have " \
           "nothing to draw instead: 2 have no male mesh either, 1 are body " \
           "pieces whose male mesh was not converted" in text


def test_no_dead_slots_print_nothing(capsys):
    ac._report_coverage_holds([{"female_standin": [], "female_male_nonbody": [],
                                "female_dead_kept": []}])
    assert capsys.readouterr().out == ""


# ------------------------------------------------ backlog 7: the planner, unchanged

def test_the_planner_still_skips_the_male_when_a_first_person_female_resolves(tmp_path):
    """Pinned (census backlog 7): the planner pools MOD3 and MOD5, so a dead
    world female with a live first-person female converts only the female
    models -- the male world mesh is never converted. The stand-in covers that
    armour; a per-slot planner fix would convert a male mesh the stand-in then
    outranks. Change this only with a live case the stand-in cannot reach."""
    from src import esp
    rec = Record(sig=b"ARMA", flags=0, formid=0x01000800, payload=_payload(
        {b"MOD2": "armor/x/bob_1.nif", b"MOD3": "armor/x/gone_1.nif",
         b"MOD5": "armor/x/alice1st_1.nif"}, BODY, mo3t=False))
    mod = tmp_path / "Planner"
    mod.mkdir()
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(mod / "planner.esp")
    bases = ac._player_armor_mesh_bases(mod, mesh_resolves=lambda b: "alice" in b)
    assert any("alice" in b for b in bases), bases
    assert not any("bob" in b for b in bases), bases
