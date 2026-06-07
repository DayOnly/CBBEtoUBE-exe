"""#132 winner-aware ARMO override rebasing.

When a converted vanilla/source ARMO is overridden later in the load order by a
third-party patch (Requiem balance, Authoria, etc.), the converter used to base
its UBE override on the BARE master / source record — discarding the winner's
stats, keywords, and replacer armatures. The winner-rebase makes the merged
ARMO override start from the load-order WINNER's payload and only ADD our _UBE
armature, so balance + extra armatures survive AND the UBE armature is present.
"""
import struct
from pathlib import Path

from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher


def _armo(formid, edid, armatures, *, armor_rating=100, keywords=None,
          version_unk=0x002C):
    """Build a minimal ARMO record payload: EDID, BOD2, RNAM, MODL*, KWDA?,
    DATA, DNAM."""
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
    p += encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
    for a in armatures:
        p += encode_subrecord(b"MODL", struct.pack("<I", a))
    if keywords:
        p += encode_subrecord(b"KWDA", b"".join(struct.pack("<I", k)
                                                 for k in keywords))
    p += encode_subrecord(b"DATA", struct.pack("<If", 0, float(armor_rating)))
    p += encode_subrecord(b"DNAM", struct.pack("<I", 0))
    return Record(sig=b"ARMO", flags=0, formid=formid, timestamp_vc=0,
                  version_unk=version_unk, payload=p)


def _arma(formid, edid):
    p = encode_subrecord(b"EDID", encode_zstring(edid))
    p += encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
    p += encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
    p += encode_subrecord(b"MOD3", encode_zstring("!UBE/Armor/test_1.nif"))
    return Record(sig=b"ARMA", flags=0, formid=formid, timestamp_vc=0,
                  version_unk=0x002C, payload=p)


def _save(path, masters, groups, flags=0):
    ESP(header=TES4Header(masters=masters, num_records=0,
                          next_object_id=0x900, version=1.7, flags=flags),
        groups=groups).save(path)
    return path


def _armo_fields(payload):
    edid = None
    armatures = []
    armor = None
    kwda = []
    for sig, d in iter_subrecords(payload):
        if sig == b"EDID":
            edid = d.split(b"\x00")[0].decode("latin1")
        elif sig == b"MODL" and len(d) == 4:
            armatures.append(struct.unpack("<I", d)[0])
        elif sig == b"DATA":
            armor = struct.unpack_from("<f", d, 4)[0]
        elif sig == b"KWDA":
            kwda = [struct.unpack_from("<I", d, i)[0]
                    for i in range(0, len(d), 4)]
    return edid, armatures, armor, kwda


def test_winner_rebase_preserves_stats_and_adds_ube(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)

    # --- fake Skyrim.esm: ARMO 0x012E49 (iron cuirass), 1 armature ---
    sky = _save(
        tmp_path / "Skyrim.esm", [],
        [Group(label=b"ARMO",
               records=[_armo(0x00012E49, "ArmorIronCuirass", [0x00012E48],
                              armor_rating=100)]),
         Group(label=b"ARMA", records=[_arma(0x00012E48, "IronCuirassAA")])],
        flags=0x1)

    # --- fake Requiem.esp: overrides 0x012E49 with 4 armatures, armor=50,
    #     a keyword. Masters: Skyrim.esm. Non-localized. ---
    req = _save(
        tmp_path / "Requiem.esp", ["Skyrim.esm"],
        [Group(label=b"ARMO",
               records=[_armo(0x00012E49, "REQ_IronCuirass",
                              [0x00012E48, 0x00012E48, 0x00012E48, 0x00012E48],
                              armor_rating=50, keywords=[0x0001ABCD])])])

    # --- our per-mod UBE patch: overrides 0x012E49 (based on BARE Skyrim.esm)
    #     adding our own _UBE ARMA. masters: Skyrim.esm + UBE_AllRace.esp ---
    own_byte = 2  # len(masters)
    ube_arma_fid = (own_byte << 24) | 0x800
    patch_armo = _armo(0x00012E49, "ArmorIronCuirass",
                       [0x00012E48, ube_arma_fid], armor_rating=100)
    patch = _save(
        tmp_path / "Mod UBE patch.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
        [Group(label=b"ARMO", records=[patch_armo]),
         Group(label=b"ARMA", records=[_arma(ube_arma_fid, "IronUBE_AA")])])

    out = tmp_path / "Combined.esp"

    # Build the winner index over the load order [Skyrim.esm, Requiem.esp].
    winners = ube_patcher.build_armo_winner_index(
        [Path(sky), Path(req)],
        exclude_names={"combined.esp", "mod ube patch.esp"})

    stats = ube_patcher.merge_patches(
        [patch], out, esl_flag=True, armo_winner_index=winners)

    merged = ESP.load(out)
    own = len(merged.header.masters)
    armo = next(g for g in merged.groups if g.label == b"ARMO").records[0]
    edid, armatures, armor, kwda = _armo_fields(armo.payload)

    # Winner BALANCE preserved (stats-only rebase):
    assert edid == "REQ_IronCuirass", f"EDID not rebased on winner: {edid}"
    assert armor == 50.0, f"armor rating reverted to vanilla: {armor}"
    assert 0x0001ABCD in kwda, "winner keyword lost"
    # Our _UBE armature present, base armatures kept (NOT the winner's extra 3 —
    # stats-only rebase keeps base armatures to avoid mastering the winner):
    ube_armas = [a for a in armatures if (a >> 24) == own]
    assert len(ube_armas) == 1, \
        f"expected exactly 1 UBE armature, got {ube_armas} in {armatures}"
    base_armas = [a for a in armatures if (a >> 24) != own]
    assert len(base_armas) == 1, \
        f"base armatures changed (stats-only must keep base): {[hex(a) for a in armatures]}"
    # The winner plugin must NOT have been pulled in as a master.
    assert all("requiem" not in m.lower() for m in merged.header.masters), \
        f"winner plugin wrongly mastered: {merged.header.masters}"
    print("  test_winner_rebase_preserves_stats_and_adds_ube OK")


def test_no_winner_keeps_base(tmp_path):
    """When no third-party override exists, the base payload is kept verbatim
    (no spurious rebase)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sky = _save(
        tmp_path / "Skyrim.esm", [],
        [Group(label=b"ARMO",
               records=[_armo(0x00012E49, "ArmorIronCuirass", [0x00012E48])]),
         Group(label=b"ARMA", records=[_arma(0x00012E48, "IronCuirassAA")])],
        flags=0x1)
    own_byte = 2
    ube_arma_fid = (own_byte << 24) | 0x800
    patch = _save(
        tmp_path / "Mod UBE patch.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
        [Group(label=b"ARMO",
               records=[_armo(0x00012E49, "ArmorIronCuirass",
                              [0x00012E48, ube_arma_fid])]),
         Group(label=b"ARMA", records=[_arma(ube_arma_fid, "IronUBE_AA")])])
    out = tmp_path / "Combined.esp"
    winners = ube_patcher.build_armo_winner_index(
        [Path(sky)], exclude_names={"mod ube patch.esp"})
    ube_patcher.merge_patches([patch], out, esl_flag=True,
                              armo_winner_index=winners)
    merged = ESP.load(out)
    armo = next(g for g in merged.groups if g.label == b"ARMO").records[0]
    edid, armatures, armor, kwda = _armo_fields(armo.payload)
    assert edid == "ArmorIronCuirass"
    own = len(merged.header.masters)
    assert len([a for a in armatures if (a >> 24) == own]) == 1
    print("  test_no_winner_keeps_base OK")


test_winner_rebase_preserves_stats_and_adds_ube(
    Path(__file__).resolve().parent / "_tmp_winner_rebase")
test_no_winner_keeps_base(
    Path(__file__).resolve().parent / "_tmp_winner_none")
