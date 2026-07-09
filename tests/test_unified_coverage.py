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

"""Unified-coverage mode (cover_all=True) for the two winner-scan passes.

Promoting the winner-scan to the PRIMARY generator (so UBE_ModBody/ModNonBody
fold into Combined) requires it to be a superset of the per-source pass. Two
principled fixes close the gap -- each is the two passes converging on the
OTHER's already-documented behavior:

  FIX1 (non-body): cover_all covers NON-PLAYABLE gear (NPC helmets/jewelry on a
    UBE-race NPC), mirroring the body pass's cover_all rationale.
  FIX2 (body): cover_all filters on the ARMATURE's race, not the ARMO's RNAM
    (a quirky authoring choice), exactly as the non-body pass already does.

Both stay beast-safe: the armature-level DefaultRace guard is unchanged, so a
beast armature is NEVER extended even in cover_all mode. Default (cover_all=
False) is byte-identical to today. #unified-coverage
"""
import struct
from pathlib import Path

from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher

DEFAULT = 0x00000019          # Skyrim.esm DefaultRace (human/mer)
BEAST = 0x000109C7            # arbitrary non-default (beast/custom) race local
QUIRKY_ARMO_RNAM = 0x0010D452  # non-default race authored on the ARMO itself
HEAD = 1 << 0                 # non-deforming slot
BODY = 1 << 2                 # deforming torso slot (slot 32)
NONPLAYABLE = 0x00000004


def _save(path, masters, groups, flags=0):
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=flags),
        groups=groups).save(path)
    return path


def _arma(formid, edid, primary_race_fid, mesh, slots_bit):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary_race_fid))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    p += encode_subrecord(b"MOD3", encode_zstring(mesh))
    p += encode_subrecord(b"MODL", struct.pack("<I", primary_race_fid))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, edid, arma_fid, armo_rnam, slots_bit, flags=0):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots_bit, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", armo_rnam))
    p += encode_subrecord(b"MODL", struct.pack("<I", arma_fid))
    p += encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))
    return Record(sig=b"ARMO", flags=flags, formid=formid, payload=p)


def _world(tmp_path, arma_race, armo_rnam, slot, armo_flags=0):
    """Build a 3-plugin load order (Skyrim.esm, UBE_AllRace.esp, Mod.esp) whose
    Mod defines one ARMA + one ARMO and return the ordered paths."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    own = 1 << 24
    arma_fid, armo_fid = own | 0x800, own | 0x801
    mod = _save(
        tmp_path / "Mod.esp", ["Skyrim.esm"],
        [Group(label=b"ARMA", records=[
            _arma(arma_fid, "AA", arma_race, "armor/x/body_0.nif", slot)]),
         Group(label=b"ARMO", records=[
            _armo(armo_fid, "It", arma_fid, armo_rnam, slot, flags=armo_flags)])])
    return [Path(sky), Path(ube), Path(mod)]


def _run_nonbody(paths, out, **kw):
    return ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, paths, exclude_names={out.name.lower()},
        master_data_dirs=[out.parent], **kw)


def _run_body(paths, out, **kw):
    return ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths={"armor/x/body_0.nif"},
        exclude_names={out.name.lower()}, master_data_dirs=[out.parent], **kw)


# ---------------- FIX1: non-body covers non-playable in cover_all ------------

def test_nonbody_nonplayable_skipped_by_default(tmp_path):
    paths = _world(tmp_path, DEFAULT, DEFAULT, HEAD, armo_flags=NONPLAYABLE)
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = _run_nonbody(paths, out)                       # cover_all=False
    assert stats["armo_targets"] == 0, "non-playable must be skipped in fallback"


def test_nonbody_nonplayable_covered_in_cover_all(tmp_path):
    paths = _world(tmp_path, DEFAULT, DEFAULT, HEAD, armo_flags=NONPLAYABLE)
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = _run_nonbody(paths, out, cover_all=True)       # FIX1
    assert stats["armo_targets"] == 1, "cover_all must cover non-playable NPC gear"
    assert stats["minted_armas"] == 1


def test_nonbody_cover_all_still_beast_guarded(tmp_path):
    # Non-playable AND beast armature -> never extended, even in cover_all.
    paths = _world(tmp_path, BEAST, DEFAULT, HEAD, armo_flags=NONPLAYABLE)
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    stats = _run_nonbody(paths, out, cover_all=True)
    assert stats["armo_targets"] == 0, "beast armature must never be UBE-extended"


# ---------------- FIX2: body filters armature race, not ARMO RNAM ------------

def test_body_quirky_armo_rnam_skipped_by_default(tmp_path):
    # ARMO RNAM is non-default but the armature is DefaultRace + converted.
    paths = _world(tmp_path, DEFAULT, QUIRKY_ARMO_RNAM, BODY)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = _run_body(paths, out)                          # cover_all=False
    assert stats["armo_targets"] == 0, "fallback filters on the ARMO RNAM"


def test_body_quirky_armo_rnam_covered_in_cover_all(tmp_path):
    paths = _world(tmp_path, DEFAULT, QUIRKY_ARMO_RNAM, BODY)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = _run_body(paths, out, cover_all=True)          # FIX2
    assert stats["armo_targets"] == 1, \
        "cover_all must judge by armature race, not the quirky ARMO RNAM"
    assert stats["minted_armas"] == 1


def test_body_cover_all_still_beast_guarded(tmp_path):
    # Beast ARMATURE (regardless of ARMO RNAM) is never extended.
    paths = _world(tmp_path, BEAST, DEFAULT, BODY)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = _run_body(paths, out, cover_all=True)
    assert stats["armo_targets"] == 0, "beast armature must never be UBE-extended"


# ---------------- GOLDEN: shared helper == pre-refactor inline logic ---------

def _legacy_inline(slot_bits, src_rnam, src_additional, remap,
                   src_to_patch_byte, ube_primary, ube_additional):
    """Verbatim copy of generate_ube_patch's pre-extraction inline block. If the
    shared helper ever drifts from this, the golden below fails. #unified-coverage
    """
    HF = ube_patcher._BIPED_SLOT_HANDS_FEET_BITS
    if slot_bits & HF:
        _prim = remap(src_rnam)
        if not _prim:
            _prim = ube_primary
        _addl = []
        _seen = set()
        for _f in src_additional:
            if ((_f >> 24) & 0xFF) not in src_to_patch_byte:
                continue
            _r = remap(_f)
            if _r and _r not in _seen:
                _seen.add(_r)
                _addl.append(_r)
        for _u in ube_additional:
            if _u not in _seen:
                _seen.add(_u)
                _addl.append(_u)
        _additional = _addl or list(ube_additional)
    else:
        _prim = ube_primary
        _additional = ube_additional
    return _prim, _additional


# ---------------- Step 2: winner-scan covers hands/feet source-primary --------

ARGONIAN = 0x00013740          # a vanilla non-default race (Skyrim.esm)
HANDS = 1 << 3                 # biped slot 33


def _hf_world(tmp_path, primary_race, addl_races, slot):
    """3-plugin load order whose Mod defines one hands/feet ARMA (primary +
    given additional races) + its ARMO."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    own = 1 << 24
    arma_fid, armo_fid = own | 0x800, own | 0x801
    p = encode_subrecord(b"EDID", encode_zstring("HandAA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slot, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary_race))
    p += encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
    p += encode_subrecord(b"MOD3", encode_zstring("armor/x/gaunt_0.nif"))
    for r in addl_races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    arma = Record(sig=b"ARMA", flags=0, formid=arma_fid, payload=p)
    armo = _armo(armo_fid, "Hand", arma_fid, primary_race, slot)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]),
                 Group(label=b"ARMO", records=[armo])])
    return [Path(sky), Path(ube), Path(mod)]


def _mint_races(out):
    merged = ESP.load(out)
    arma = next(g for g in merged.groups if g.label == b"ARMA").records[0]
    m = merged.header.masters
    ube_i = next(i for i, x in enumerate(m) if x.lower() == "ube_allrace.esp")
    sky_i = next(i for i, x in enumerate(m) if x.lower() == "skyrim.esm")
    rnam, addl = None, []
    for s, d in iter_subrecords(arma.payload):
        if s == b"RNAM":
            rnam = struct.unpack("<I", d)[0]
        elif s == b"MODL" and len(d) == 4:
            addl.append(struct.unpack("<I", d)[0])
    return rnam, addl, ube_i, sky_i


def test_handsfeet_minted_source_primary(tmp_path):
    paths = _hf_world(tmp_path, DEFAULT, [DEFAULT, ARGONIAN], HANDS)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=False)
    assert stats["armo_targets"] == 1 and stats["minted_armas"] == 1, stats
    rnam, addl, ube_i, sky_i = _mint_races(out)
    # PRIMARY stays the SOURCE DefaultRace (Skyrim), NOT UBE-Breton.
    assert rnam == ((sky_i << 24) | ube_patcher._DEFAULT_RACE_LOW24), \
        f"hands ARMA must be source-primary, got RNAM={rnam:08X}"
    assert rnam != ((ube_i << 24) | ube_patcher.UBE_PRIMARY_BRETON_FID_24)
    # Vanilla race PRESERVED + all UBE races ADDED.
    assert ((sky_i << 24) | ARGONIAN) in addl, "vanilla race must be preserved"
    ube_races = {(ube_i << 24) | f for f in ube_patcher.UBE_RACE_FIDS_24}
    assert ube_races <= set(addl), "UBE races must be added on top"


def test_handsfeet_not_covered_without_flag(tmp_path):
    # cover_all body-only mode (no cover_hands_feet) must leave hands/feet out.
    paths = _hf_world(tmp_path, DEFAULT, [DEFAULT], HANDS)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=False, preserve_textures=False)
    assert stats["armo_targets"] == 0, "hands/feet must stay out unless flagged"


def test_handsfeet_beast_still_guarded(tmp_path):
    # A beast-primary hands armature with no DefaultRace armature -> never minted.
    paths = _hf_world(tmp_path, BEAST, [BEAST], HANDS)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    stats = ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=False)
    assert stats["armo_targets"] == 0, "beast hands armature must not be extended"


# ---------------- Step 3b: sidecar emission for the merge fold ---------------

def test_body_sidecar_schema_and_postprune_fids(tmp_path):
    import json
    paths = _hf_world(tmp_path, DEFAULT, [DEFAULT, ARGONIAN], HANDS)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, preserve_textures=False,
        emit_sidecar=True)
    sc = Path(str(out) + ".skypatcher.json")
    assert sc.is_file(), "sidecar must be written when emit_sidecar=True"
    doc = json.loads(sc.read_text())
    assert doc, "sidecar must have entries"
    ent = doc[0]
    # schema the merge consumes: armo [plugin, local] + adds [{fid, src}]
    assert isinstance(ent["armo"], list) and len(ent["armo"]) == 2
    assert ent["armo"][0] == "Mod.esp"
    add = ent["adds"][0]
    assert "fid" in add and "src" in add and len(add["src"]) == 2
    # fid must be a REAL post-prune ARMA fid in the saved ESP (merge keys on it)
    merged = ESP.load(out)
    fids = {r.formid for g in merged.groups if g.label == b"ARMA"
            for r in g.records}
    assert add["fid"] in fids, "sidecar fid must match a post-prune ARMA record"
    # src = source armature identity (used for cross-patch dedup)
    assert add["src"][0].lower() == "mod.esp"


def test_no_sidecar_without_flag(tmp_path):
    paths = _hf_world(tmp_path, DEFAULT, [DEFAULT], HANDS)
    out = tmp_path / "UBE_ModBody_Coverage.esp"
    ube_patcher.generate_modded_body_ube_coverage_patch(
        out, paths, converted_rel_paths=set(),
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, cover_hands_feet=True, emit_sidecar=False)
    assert not Path(str(out) + ".skypatcher.json").is_file(), \
        "no sidecar unless emit_sidecar=True"


def test_nonbody_sidecar_emitted(tmp_path):
    import json
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(tmp_path / "Skyrim.esm", [], [], flags=0x1)
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [], flags=0)
    own = 1 << 24
    arma_fid, armo_fid = own | 0x800, own | 0x801
    p = encode_subrecord(b"EDID", encode_zstring("HelmAA"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", HEAD, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD3", encode_zstring("armor/x/helm_0.nif"))
    p += encode_subrecord(b"MODL", struct.pack("<I", DEFAULT))
    arma = Record(sig=b"ARMA", flags=0, formid=arma_fid, payload=p)
    armo = _armo(armo_fid, "Helm", arma_fid, DEFAULT, HEAD)
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[arma]),
                 Group(label=b"ARMO", records=[armo])])
    out = tmp_path / "UBE_ModNonBody_Coverage.esp"
    ube_patcher.generate_modded_nonbody_ube_coverage_patch(
        out, [Path(sky), Path(ube), Path(mod)],
        exclude_names={out.name.lower()}, master_data_dirs=[tmp_path],
        cover_all=True, emit_sidecar=True)
    sc = Path(str(out) + ".skypatcher.json")
    assert sc.is_file()
    doc = json.loads(sc.read_text())
    merged = ESP.load(out)
    fids = {r.formid for g in merged.groups if g.label == b"ARMA"
            for r in g.records}
    assert doc[0]["adds"][0]["fid"] in fids


def test_helper_byte_identical_to_legacy_inline():
    # src master bytes: 0->patch0 (Skyrim), 1 = unmappable mod, 2->patch4 (own).
    s2p = {0: 0, 2: 4}

    def remap(fid):
        top = (fid >> 24) & 0xFF
        return ((s2p[top] << 24) | (fid & 0xFFFFFF)) if top in s2p else fid

    UBE_PRIM = 0x05005734
    UBE_ADDL = [0x05005734, 0x05005730, 0x05005731]
    HANDS, FEET = 1 << 3, 1 << 7
    cases = [
        (BODY, 0x00000019, []),                                  # body: UBE-only
        (HANDS, 0x00000019, [0x00013740, 0x01000ABC, 0x02000111]),
        (FEET, 0x00000019, []),                                  # no source addl
        (HANDS | FEET, 0x00000019, [0x01000ABC]),                # all unmappable
        (HANDS, 0x00000000, [0x00013740]),                       # primary falls back
        (HANDS, 0x02000019, [0x00013740, 0x00013740]),           # dup source race
        (BODY | HANDS, 0x00000019, [0x00013740]),                # combo counts as HF
    ]
    for slots, rnam, addl in cases:
        legacy = _legacy_inline(slots, rnam, addl, remap, s2p, UBE_PRIM, UBE_ADDL)
        new = ube_patcher.coverage_arma_race_targeting(
            slots, rnam, addl, remap_src_fid=remap, src_to_patch_byte=s2p,
            ube_primary=UBE_PRIM, ube_additional=UBE_ADDL)
        assert new == legacy, f"drift on {(slots, rnam, addl)}: {new} != {legacy}"
