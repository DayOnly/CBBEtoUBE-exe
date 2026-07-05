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

"""ube_patcher unit tests (SkyPatcher-only delivery).

Covers the still-live pieces of the patch pipeline: ARMA rebuild + additional-
race lists, slot-49->32 promotion, ESL split/downgrade on the merge, ESM-tier
master ordering, EDID name synthesis, and patch validation. The legacy ARMO-
override generation + its real-patch A/B comparison were removed with the
override machinery.
"""
import sys, struct
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import esp


# NOTE: SkyPatcher is the only delivery path; the legacy ARMO-override
# comparison cases (and the sample-based real-patch A/B) were removed with the
# override machinery. The tests below cover the still-live pieces: ARMA
# rebuild/race lists, slot promotion, ESL split/downgrade, master-order, and
# patch validation.


# ---------------------------------------------------------------------------
# Slot-32 promotion for BODYTRI'd slot-49 cloth
# ---------------------------------------------------------------------------

def test_add_slot32_to_bod2_payload():
    """Setting slot 32 on a slot-49-only BOD2 leaves the rest intact and
    is idempotent."""
    from src.ube_patcher import add_slot32_to_bod2_payload
    from src.esp import iter_subrecords, encode_subrecord, encode_zstring

    bit49 = 1 << (49 - 30)
    bit32 = 1 << (32 - 30)

    # Build minimal ARMA-ish payload: EDID + BOD2(slots=slot49, armor_type=4).
    edid = encode_subrecord(b"EDID", encode_zstring("TEST_Corset"))
    bod2 = encode_subrecord(b"BOD2", struct.pack("<II", bit49, 4))
    payload = edid + bod2

    out, changed = add_slot32_to_bod2_payload(payload)
    assert changed, "should promote slot-49-only BOD2"
    for sig, data in iter_subrecords(out):
        if sig == b"BOD2":
            slots, atype = struct.unpack_from("<II", data, 0)
            assert slots & bit32, "slot 32 not set"
            assert slots & bit49, "slot 49 lost"
            assert atype == 4, "armor_type field corrupted"
            break
    else:
        raise AssertionError("BOD2 missing from output")

    # Idempotent — running again is a no-op.
    out2, changed2 = add_slot32_to_bod2_payload(out)
    assert not changed2, "second pass should be no-op"

    # BODT (legacy) also supported.
    bodt = encode_subrecord(b"BODT", struct.pack("<III", bit49, 4, 0))
    out3, changed3 = add_slot32_to_bod2_payload(edid + bodt)
    assert changed3, "BODT should be supported"

    # Payload with no BOD2/BODT is unchanged.
    out4, changed4 = add_slot32_to_bod2_payload(edid)
    assert not changed4
    assert out4 == edid

    # Doesn't touch ARMAs that already cover slot 32.
    bod2_both = encode_subrecord(b"BOD2", struct.pack("<II", bit32 | bit49, 4))
    out5, changed5 = add_slot32_to_bod2_payload(edid + bod2_both)
    assert not changed5

    print("  test_add_slot32_to_bod2_payload OK")


def test_promote_slot49_cloth_uses_bodytri_predicate(tmp_path):
    """End-to-end on a synthetic ESP: only ARMAs whose linked NIF is
    flagged by the BODYTRI predicate get promoted."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    from src.ube_patcher import promote_slot49_cloth_to_slot32
    from src.esp import encode_subrecord, encode_zstring

    bit49 = 1 << (49 - 30)
    bit35 = 1 << (35 - 30)
    bit32 = 1 << (32 - 30)

    # Three ARMA records:
    #   A: slot 49, model points at "cloth.nif" (predicate -> True)
    #   B: slot 49, model points at "jewelry.nif" (predicate -> False)
    #   C: slot 35, model points at "anything.nif" (not slot 49 -> skip)
    def make_arma(edid: str, slots: int, model_path: str):
        payload = (
            encode_subrecord(b"EDID", encode_zstring(edid))
            + encode_subrecord(b"BOD2", struct.pack("<II", slots, 4))
            + encode_subrecord(b"MOD2", encode_zstring(model_path))
        )
        return esp.Record(
            sig=b"ARMA", flags=0, formid=0x01000000 | hash(edid) & 0xFFFFFF,
            timestamp_vc=0, version_unk=0x002C, payload=payload,
        )

    armas = [
        make_arma("Cloth_UBE", bit49, "!UBE\\test\\cloth.nif"),
        make_arma("Jewelry_UBE", bit49, "!UBE\\test\\jewelry.nif"),
        make_arma("Wrist_UBE", bit35, "!UBE\\test\\wrist.nif"),
    ]
    hdr = esp.TES4Header(masters=["Skyrim.esm"])
    test_esp = esp.ESP(header=hdr, groups=[esp.Group(label=b"ARMA", records=armas)])
    esp_path = tmp_path / "test.esp"
    test_esp.save(esp_path)

    # Touch the corresponding NIF paths so the file-exists check passes.
    meshes_root = tmp_path / "meshes"
    for sub in ("test/cloth.nif", "test/jewelry.nif", "test/wrist.nif"):
        p = meshes_root / "!UBE" / sub
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")

    # Predicate: True only for cloth.nif. (Real predicate reads the NIF
    # via pynifly; we inject a deterministic stub here so the test
    # doesn't depend on having a real NIF on disk.)
    def stub(nif_path):
        return nif_path.name == "cloth.nif"

    stats = promote_slot49_cloth_to_slot32(
        esp_path, meshes_root, nif_has_bodytri=stub,
    )
    assert stats["armas_examined"] == 3
    assert stats["armas_promoted_to_slot32"] == 1, \
        f"expected 1 promotion, got {stats['armas_promoted_to_slot32']}"
    assert stats["promoted_edids"] == ["Cloth_UBE"]

    # Reload + verify the bit actually landed.
    reloaded = esp.ESP.load(esp_path)
    arma_grp = reloaded.group(b"ARMA")
    assert arma_grp is not None
    for rec in arma_grp.records:
        edid = None
        slot_bits = 0
        for sig, sd in esp.iter_subrecords(rec.payload):
            if sig == b"EDID":
                edid = sd.rstrip(b"\x00").decode()
            elif sig == b"BOD2":
                slot_bits = struct.unpack_from("<I", sd, 0)[0]
        if edid == "Cloth_UBE":
            assert slot_bits & bit32, "Cloth_UBE not promoted on disk"
            assert slot_bits & bit49, "Cloth_UBE lost slot 49"
        elif edid == "Jewelry_UBE":
            assert not (slot_bits & bit32), \
                "Jewelry_UBE should NOT be promoted"
        elif edid == "Wrist_UBE":
            assert slot_bits == bit35, "Wrist_UBE slots changed unexpectedly"

    print("  test_promote_slot49_cloth_uses_bodytri_predicate OK")





# ---------------------------------------------------------------------------
# ARMO MODL-ordering regression test
# ---------------------------------------------------------------------------
#
# Skyrim's ARMO parser stops reading the armature list (MODL subrecords) at
# DATA. If new UBE MODLs land after DATA/DNAM, the engine silently ignores
# them — every replacer armor goes invisible because the engine never sees
# the new UBE ARMA in the armature list. Bug was live in three code paths
# until 2026-05-25 fix; this test catches reintroduction.
# ---------------------------------------------------------------------------

def _armo_modl_positions(payload: bytes) -> tuple[list[int], int]:
    """Return (modl_indexes, data_index) for an ARMO record payload."""
    from src.esp import iter_subrecords
    modl_idxs: list[int] = []
    data_idx = -1
    for i, (sig, _data) in enumerate(iter_subrecords(payload)):
        if sig == b"MODL":
            modl_idxs.append(i)
        elif sig == b"DATA":
            data_idx = i
    return modl_idxs, data_idx




def test_rebuild_arma_emits_full_additional_race_list_before_sndd():
    """Gauntlet-invisible fix: a hand/foot ARMA must carry the VANILLA
    playable races (not just UBE) or it never matches the UBE actor's
    hand/foot slot (which falls back to a vanilla race). The caller builds
    new_additional_race_fids = [source vanilla races...] + [UBE races...];
    rebuild_arma_payload must emit ALL of them as MODL subrecords, grouped
    before SNDD (canonical ARMA order) — else Skyrim ignores the trailing
    ones and the piece stays invisible."""
    from src.ube_patcher import rebuild_arma_payload, ARMA_ADDITIONAL_RACE_SIG
    from src.esp import encode_subrecord, encode_zstring, iter_subrecords

    # Source gauntlet ARMA: slot 33+36, DefaultRace primary, 2 vanilla races,
    # then SNDD (footstep). rebuild should drop the source MODLs and re-emit
    # the full combined list before SNDD.
    src = (
        encode_subrecord(b"EDID", encode_zstring("TestGauntlets"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x48, 0))   # slots 33,36
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"DNAM", struct.pack("<BBhffI", 1, 1, 0, 1.0, 0.0, 0))
        + encode_subrecord(b"MOD3", encode_zstring("armor\\test\\gauntlets.nif"))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00013744))  # vanilla race
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00013743))  # vanilla race
        + encode_subrecord(b"SNDD", struct.pack("<I", 0x000C9F50))  # footstep set
    )
    combined = [0x00013744, 0x00013743, 0x17005734, 0x17005735]  # vanilla + UBE
    out = rebuild_arma_payload(
        src, new_primary_rnam=0x00000019, new_additional_race_fids=combined,
    )
    sigs = [sig for sig, _ in iter_subrecords(out)]
    races = [struct.unpack("<I", d)[0] for sig, d in iter_subrecords(out)
             if sig == ARMA_ADDITIONAL_RACE_SIG and len(d) == 4]
    assert races == combined, (
        f"all 4 races (vanilla + UBE) must be emitted in order: got {races}")
    assert b"SNDD" in sigs, "SNDD must survive the rebuild"
    last_modl = max(i for i, s in enumerate(sigs) if s == b"MODL")
    assert last_modl < sigs.index(b"SNDD"), (
        "race MODL list must precede SNDD (else Skyrim ignores trailing races)")
    # primary preserved as DefaultRace (vanilla), matching working vanilla gauntlets
    rnam = next(struct.unpack("<I", d)[0] for sig, d in iter_subrecords(out)
                if sig == b"RNAM")
    assert rnam == 0x00000019
    print("  test_rebuild_arma_emits_full_additional_race_list_before_sndd OK")





def test_validate_patch_catches_modl_after_data(tmp_path):
    """validate_patch must flag a MODL-after-DATA bug — the failure
    mode we hit in May 2026 with replacer armor invisibility.
    """
    from src.ube_patcher import validate_patch
    from src.esp import (
        ESP, TES4Header, Group, Record,
        encode_subrecord, encode_zstring,
    )

    # Construct an intentionally malformed ARMO: DATA before MODL.
    bad_armo_payload = (
        encode_subrecord(b"EDID", encode_zstring("BadArmor"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))  # AFTER DATA
    )
    rec = Record(
        sig=b"ARMO", flags=0, formid=0x01000800, timestamp_vc=0,
        version_unk=0x002C, payload=bad_armo_payload,
    )
    bad_esp = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMO", records=[rec])],
    )
    out_path = tmp_path / "bad.esp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bad_esp.save(out_path)

    warnings = validate_patch(out_path)
    matched = [w for w in warnings if "MODL after DATA" in w]
    assert matched, f"validate_patch did NOT catch MODL-after-DATA: {warnings}"

    print("  test_validate_patch_catches_modl_after_data OK")





# ---------------------------------------------------------------------------
# Cross-patch ARMO merge: when two patches override the same Skyrim.esm
# ARMO, the merger must combine their armatures lists. Reproduces the
# diagnostic we ran by hand on the user's HDT-SMP + Remodeled overlap.
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# ESL-overflow downgrade: when the union of new ARMAs exceeds the ESL cap,
# merge_patches must NOT raise (the old behaviour aborted the merge and left a
# STALE Combined.esp on disk — the real "Vigilant armor invisible" bug). It
# must instead ship a regular non-ESL ESP and keep every record.
# ---------------------------------------------------------------------------

def test_merge_downgrades_to_full_esp_on_esl_overflow(tmp_path):
    import src.ube_patcher as up
    from src.ube_patcher import merge_patches
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring

    def make_arma(own_fid: int) -> Record:
        payload = (
            encode_subrecord(b"EDID", encode_zstring(f"ARMA_{own_fid:X}_UBE"))
            + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
            + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
            + encode_subrecord(b"MOD3", encode_zstring("!UBE/Armor/test.nif"))
        )
        return Record(sig=b"ARMA", flags=0, formid=own_fid, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)

    def make_patch(path: Path, locals_: list[int]) -> Path:
        masters = ["Skyrim.esm", "UBE_AllRace.esp"]
        own_byte = len(masters)
        armas = [make_arma((own_byte << 24) | lo) for lo in locals_]
        esp_obj = ESP(
            header=TES4Header(masters=masters, num_records=0,
                              next_object_id=max(locals_) + 1, version=1.7),
            groups=[Group(label=b"ARMA", records=armas)],
        )
        esp_obj.save(path)
        return path

    tmp_path.mkdir(parents=True, exist_ok=True)
    p1 = make_patch(tmp_path / "ov_a.esp", [0x800, 0x801])
    p2 = make_patch(tmp_path / "ov_b.esp", [0x800, 0x801])
    out = tmp_path / "ov_merged.esp"

    # Force the cap below the 4 total new ARMAs so we exercise the downgrade.
    saved = up.ESL_MAX_OWN_RECORDS
    try:
        up.ESL_MAX_OWN_RECORDS = 3
        stats = merge_patches([p1, p2], out, esl_flag=True)
    finally:
        up.ESL_MAX_OWN_RECORDS = saved

    assert stats["downgraded_to_full_esp"] is True, \
        "expected downgrade flag when new ARMAs exceed the ESL cap"
    assert stats["esl_flagged"] is False, "must NOT be ESL-flagged after downgrade"
    assert stats["own_arma_records"] == 4, \
        f"all 4 new ARMAs must survive, got {stats['own_arma_records']}"

    merged = ESP.load(out)
    assert not (merged.header.flags & up.TES4_FLAG_ESL), \
        "merged ESP header still has the ESL flag set"
    arma_grp = next(g for g in merged.groups if g.label == b"ARMA")
    assert len(arma_grp.records) == 4

    print("  test_merge_downgrades_to_full_esp_on_esl_overflow OK")




# ---------------------------------------------------------------------------
# FormID-out-of-range detection in validate_patch. Reproduces the
# diagnostic that first caught a real out-of-range FormID in a generated patch.
# ---------------------------------------------------------------------------

def test_validate_patch_catches_out_of_range_formid(tmp_path):
    from src.ube_patcher import validate_patch
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring

    # Build a patch with 2 masters but an ARMA record carrying a MODL
    # FormID whose top byte is 5 (way past the master list).
    masters = ["Skyrim.esm", "UBE_AllRace.esp"]
    payload = (
        encode_subrecord(b"EDID", encode_zstring("BadARMA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x05005734))  # bad
    )
    rec = Record(
        sig=b"ARMA", flags=0, formid=0x02000800, timestamp_vc=0,
        version_unk=0x002C, payload=payload,
    )
    bad_esp = ESP(
        header=TES4Header(masters=masters, num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMA", records=[rec])],
    )
    out_path = tmp_path / "bad_fid.esp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bad_esp.save(out_path)

    warnings = validate_patch(out_path, check_nifs=False)
    matched = [w for w in warnings if w.startswith("formid-out-of-range")]
    assert matched, (
        f"validate_patch did NOT catch out-of-range FormID: {warnings}"
    )

    print("  test_validate_patch_catches_out_of_range_formid OK")




# ---------------------------------------------------------------------------
# NIF-existence check in validate_patch.
# ---------------------------------------------------------------------------

def test_validate_patch_catches_missing_nif(tmp_path):
    from src.ube_patcher import validate_patch
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring

    masters = ["Skyrim.esm", "UBE_AllRace.esp"]
    # MOD3 points at "!UBE\\test\\nope_1.nif" — we don't create that file.
    payload = (
        encode_subrecord(b"EDID", encode_zstring("MissingNifARMA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
        + encode_subrecord(b"MOD3", encode_zstring("!UBE\\test\\nope_1.nif"))
    )
    rec = Record(
        sig=b"ARMA", flags=0, formid=0x02000800, timestamp_vc=0,
        version_unk=0x002C, payload=payload,
    )
    bad_esp = ESP(
        header=TES4Header(masters=masters, num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMA", records=[rec])],
    )
    out_path = tmp_path / "missing_nif.esp"
    meshes_root = tmp_path / "meshes"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Clear any leftover NIFs from a previous test run.
    import shutil
    if meshes_root.exists():
        shutil.rmtree(meshes_root)
    meshes_root.mkdir(parents=True, exist_ok=True)
    bad_esp.save(out_path)

    warnings = validate_patch(out_path, meshes_root=meshes_root)
    matched = [w for w in warnings if w.startswith("missing-nif")]
    assert matched, (
        f"validate_patch did NOT catch missing NIF: {warnings}"
    )

    # Now create the NIF and verify the warning goes away.
    nif_path = meshes_root / "!UBE" / "test" / "nope_1.nif"
    nif_path.parent.mkdir(parents=True, exist_ok=True)
    nif_path.write_bytes(b"")
    warnings2 = validate_patch(out_path, meshes_root=meshes_root)
    matched2 = [w for w in warnings2 if w.startswith("missing-nif")]
    assert not matched2, (
        f"validate_patch reported missing-nif after we created the file: "
        f"{warnings2}"
    )

    print("  test_validate_patch_catches_missing_nif OK")




# ---------------------------------------------------------------------------
# Unmappable-transitive-master detection in validate_patch. Reproduces the
# diagnostic that caught the fish.esm + HearthFires.esm startup crash.
# ---------------------------------------------------------------------------

def test_validate_patch_catches_unmappable_transitive_master(tmp_path):
    from src.ube_patcher import validate_patch
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring

    tmp_path.mkdir(parents=True, exist_ok=True)

    # Build a fake "master" ESM that lists HearthFires.esm + Skyrim.esm
    # as its own masters. The validator should flag a patch that names
    # this fake_master as a master without also listing HearthFires.
    fake_master_path = tmp_path / "fake_master.esm"
    fake_master = ESP(
        header=TES4Header(masters=["Skyrim.esm", "HearthFires.esm"],
                          num_records=0, next_object_id=0x800, version=1.7),
        groups=[],
    )
    fake_master.save(fake_master_path)

    # Build our patch — declares fake_master.esm but NOT HearthFires.esm.
    # That's the bug pattern: our patch carries records from fake_master
    # whose payloads may reference HearthFires content, which would
    # silently misroute through our patch's master byte 0.
    patch_masters = ["Skyrim.esm", "fake_master.esm"]
    # Need an override record so the validator actually checks the master.
    override = Record(
        sig=b"ARMA", flags=0, formid=0x01000800,  # override of fake_master fid
        timestamp_vc=0, version_unk=0x002C,
        payload=encode_subrecord(b"EDID", encode_zstring("FakeARMA")),
    )
    patch = ESP(
        header=TES4Header(masters=patch_masters, num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMA", records=[override])],
    )
    out_path = tmp_path / "unsafe.esp"
    patch.save(out_path)

    warnings = validate_patch(out_path, check_nifs=False,
                              master_data_dirs=[tmp_path])
    matched = [w for w in warnings if w.startswith("unmappable-master-ref")]
    assert matched, (
        f"validate_patch did NOT catch unmappable transitive master: "
        f"{warnings}"
    )
    # The flagged master should be fake_master.esm, with HearthFires.esm
    # in its missing transitives.
    assert "fake_master.esm" in matched[0]
    assert "HearthFires.esm" in matched[0]

    print("  test_validate_patch_catches_unmappable_transitive_master OK")




# ---------------------------------------------------------------------------
# FULL synthesis from EDID — fixes the "body slot vanilla item doesn't
# appear in inventory" bug caused by stripping LSTRING FULL refs from
# localized-master ARMO overrides. Without FULL, Skyrim's inventory UI
# silently hides the item.
# ---------------------------------------------------------------------------

def test_synthesize_name_from_edid():
    from src.ube_patcher import synthesize_name_from_edid

    cases = [
        # (EDID,                                 expected_name)
        ("ArmorIronCuirass",                     "Iron Cuirass"),
        ("ArmorDwarvenBoots",                    "Dwarven Boots"),
        ("ClothesRobesGreybeardTunic",           "Robes Greybeard Tunic"),
        ("EnchArmorDwarvenCuirassDestruction04", "Dwarven Cuirass Destruction 04"),
        # DLC prefix + type prefix both stripped. Mid-string "Armor"
        # stays since it's usually part of the natural item name
        # (e.g. "Vampire Armor Red").
        ("DLC1ArmorVampireArmorGrayLight",       "Vampire Armor Gray Light"),
        ("DLC2nDarkElfOutfitvar01",              "Dark Elf Outfitvar 01"),
        ("DLC1nVampireBloodMagicRingDrainingClaws",
                                                  "Vampire Blood Magic Ring Draining Claws"),
        ("DLC1EnchClothesVampireRobesDestruction02",
                                                  "Vampire Robes Destruction 02"),
        # Pathological inputs — fall back to raw EDID
        ("Armor",                                "Armor"),  # only prefix, kept as-is
        ("X",                                    "X"),
    ]
    for edid, expected in cases:
        got = synthesize_name_from_edid(edid)
        assert got == expected, (
            f"synthesize_name_from_edid({edid!r}) = {got!r}, expected {expected!r}"
        )

    print("  test_synthesize_name_from_edid OK")




def test_is_esm_tier_master_detects_esl_flagged_esp(tmp_path):
    """Regression: an ESL-flagged .esp (TES4 flag 0x200, WITHOUT the ESM bit
    0x1 — i.e. an ESPFE, the modern compact-plugin standard) must classify as
    MASTER-TIER so the merge sorts it before regular .esps. The old code checked
    only 0x1, so 84 ESPFE armor masters (3BBB, Magecore, Velothi, ...) sorted
    AFTER UBE_AllRace.esp and corrupted the Combined's master order, misrouting
    their overrides (invisible/static armor on UBE)."""
    import struct
    from src.ube_patcher import _is_esm_tier_master, _ESM_TIER_CACHE, \
        clear_master_path_cache

    def make(name, flags):
        # Minimal TES4 head: sig(4) + size(4) + flags(4) is all _read_tes4_flags
        # reads (first 12 bytes).
        (tmp_path / name).write_bytes(
            b"TES4" + struct.pack("<I", 0) + struct.pack("<I", flags) + b"\x00" * 4)
        return name

    _ESM_TIER_CACHE.clear()
    clear_master_path_cache()
    dirs = [tmp_path]
    # Create ALL master files up front. Real masters exist before a run; the
    # per-dir file index is built on first lookup, so creating files mid-test
    # would index a stale, partial directory.
    for n, f in (("esl_only.esp", 0x200), ("esm_flag.esp", 0x1),
                 ("both.esp", 0x201), ("regular.esp", 0x0)):
        make(n, f)
    assert _is_esm_tier_master("esl_only.esp", dirs) is True   # ESPFE
    assert _is_esm_tier_master("esm_flag.esp", dirs) is True   # USSEP-style
    assert _is_esm_tier_master("both.esp", dirs) is True
    assert _is_esm_tier_master("regular.esp", dirs) is False
    # extension-based classification still holds (no file needed)
    assert _is_esm_tier_master("x.esm", dirs) is True
    assert _is_esm_tier_master("x.esl", dirs) is True
    _ESM_TIER_CACHE.clear()
    print("  test_is_esm_tier_master_detects_esl_flagged_esp OK")










def test_build_nif_slot_map(tmp_path):
    """build_nif_slot_map should pull biped-slot bits + MOD?-paths from
    ARMA records and OR-merge slots across multiple ARMAs that reference
    the same NIF. Path keys should be lowercased forward-slash form with
    no 'meshes/' prefix."""
    from src.ube_patcher import build_nif_slot_map
    from src.esp import (encode_subrecord, encode_zstring, Record,
                         Group, ESP, TES4Header)

    # Two ARMAs referencing the SAME NIF — one slot 49 (0x80000),
    # one slot 32 (0x4). After OR-merge the combined bits should
    # contain BOTH.
    arma1_payload = (
        encode_subrecord(b"EDID", encode_zstring("ArmaSlot49"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x80000, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MOD2", encode_zstring("Armor\\foo\\Skirt_1.nif"))
    )
    arma2_payload = (
        encode_subrecord(b"EDID", encode_zstring("ArmaSlot32"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MOD2", encode_zstring("Armor\\foo\\Skirt_1.nif"))
        + encode_subrecord(b"MOD3", encode_zstring("Meshes\\Armor\\foo\\BodyOnly.nif"))
    )
    arma1 = Record(sig=b"ARMA", flags=0, formid=0x00000801,
                   timestamp_vc=0, version_unk=0x002C, payload=arma1_payload)
    arma2 = Record(sig=b"ARMA", flags=0, formid=0x00000802,
                   timestamp_vc=0, version_unk=0x002C, payload=arma2_payload)
    e = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x803, version=1.7),
        groups=[Group(label=b"ARMA", records=[arma1, arma2])],
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    esp_path = tmp_path / "slot_map_test.esp"
    e.save(esp_path)

    m = build_nif_slot_map([esp_path])

    # MOD2 path from both ARMAs (lowercased forward-slash) should
    # be present with OR-merged slots.
    key_skirt = "armor/foo/skirt_1.nif"
    assert key_skirt in m, f"missing key {key_skirt!r} in {sorted(m)}"
    bits = m[key_skirt]
    assert bits & (1 << 19), f"slot 49 bit missing from {bits:#010x}"
    assert bits & (1 << 2),  f"slot 32 bit missing from {bits:#010x}"
    # MOD3 path had "Meshes\" prefix — should have been stripped.
    key_bodyonly = "armor/foo/bodyonly.nif"
    assert key_bodyonly in m, f"meshes/ prefix not stripped; got {sorted(m)}"
    assert m[key_bodyonly] & (1 << 2)

    print("  test_build_nif_slot_map OK")



