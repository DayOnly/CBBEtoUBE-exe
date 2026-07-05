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

"""Generate a UBE patch ESP and compare against the real hand-authored one.

We don't require byte-identity (different authors will pick different EDID
suffixes, FormID assignments, etc.). What we check:

  * Same master set (Skyrim.esm, ?Dawnguard.esm, UBE_AllRace.esp, source.esp)
  * Same number of new ARMAs
  * Each new ARMA has:
      - primary RNAM pointing to UBE_BretonRace
      - 15 additional MODL entries (all UBE races except primary)
      - MOD2/MOD3/MOD4/MOD5 paths all start with "!UBE\\"
  * Same number of ARMO overrides
  * Each ARMO override has at least one new ARMA in its Armatures list
"""
import sys, struct
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import esp
from src.ube_patcher import (
    generate_ube_patch, UBE_RACE_FIDS_24, UBE_PRIMARY_BRETON_FID_24,
    make_master_byte, ARMA_ADDITIONAL_RACE_SIG, ARMA_MODEL_SIGS,
)


CASES = [
    {
        "name": "eve_sunfire",
        "source": PROJ / "samples" / "m1" / "eve_sunfire" / "cbbe"
                 / "Obi - Eve's Sunfire Armor.esp",
        "real_patch": PROJ / "samples" / "m1" / "eve_sunfire" / "ube"
                 / "Obi - Eve's Sunfire Armor UBE patch.esp",
    },
    {
        "name": "kozakowy_vampire",
        "source": PROJ / "samples" / "m1" / "kozakowy_vampire" / "cbbe"
                 / "[TOTOxKozakowy] Kozakowy's Vampire Armor 3BA"
                 / "KozakowyVampireArmor.esp",
        "real_patch": PROJ / "samples" / "m1" / "kozakowy_vampire" / "ube"
                 / "[TOTOxKozakowy] Kozakowy's Vampire Armor UBE v1.0"
                 / "KozakowyVampireArmor UBE patch.esp",
    },
]


def check_case(case: dict) -> None:
    print(f"\n>>> {case['name']}")
    if not case["source"].is_file():
        print(f"  SKIP — source missing: {case['source']}"); return
    if not case["real_patch"].is_file():
        print(f"  SKIP — real patch missing: {case['real_patch']}"); return

    out_path = PROJ / "output" / f"{case['name']}_AUTO_UBE_patch.esp"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    stats = generate_ube_patch(case["source"], out_path)
    print(f"  generated: {stats['output']}")
    print(f"    masters: {stats['masters']}")
    print(f"    new ARMA records: {stats['new_arma_count']}")
    print(f"    ARMO overrides:   {stats['armo_override_count']}")

    # Load both for comparison
    ours = esp.ESP.load(out_path)
    real = esp.ESP.load(case["real_patch"])

    print(f"\n  comparison: our patch vs real patch")
    print(f"    masters     : ours={ours.header.masters}")
    print(f"                  real={real.header.masters}")
    print(f"    ARMA count  : ours={len(ours.group(b'ARMA').records) if ours.group(b'ARMA') else 0}"
          f"  real={len(real.group(b'ARMA').records) if real.group(b'ARMA') else 0}")
    print(f"    ARMO count  : ours={len(ours.group(b'ARMO').records) if ours.group(b'ARMO') else 0}"
          f"  real={len(real.group(b'ARMO').records) if real.group(b'ARMO') else 0}")

    # Check each of our new ARMAs has the right race structure
    ours_arma = ours.group(b"ARMA")
    if ours_arma is None:
        print("  no ARMA group in output"); return
    ube_top = make_master_byte(ours.header.masters, "UBE_AllRace.esp") << 24
    expected_primary = ube_top | UBE_PRIMARY_BRETON_FID_24

    arma_ok = 0
    arma_problems = []
    for rec in ours_arma.records:
        primary = None
        additional = []
        model_paths = []
        for sig, data in esp.iter_subrecords(rec.payload):
            if sig == b"RNAM" and len(data) == 4:
                primary = struct.unpack("<I", data)[0]
            elif sig == ARMA_ADDITIONAL_RACE_SIG and len(data) == 4:
                additional.append(struct.unpack("<I", data)[0])
            elif sig in ARMA_MODEL_SIGS:
                model_paths.append(data.rstrip(b"\x00").decode("utf-8", errors="ignore"))
        problems = []
        if primary != expected_primary:
            problems.append(f"RNAM={primary:#010x} expected {expected_primary:#010x}")
        if len(additional) != 15:
            problems.append(f"additional count={len(additional)} expected 15")
        for mp in model_paths:
            if not mp.startswith("!UBE\\"):
                problems.append(f"path '{mp}' missing !UBE\\ prefix")
        if not problems:
            arma_ok += 1
        else:
            arma_problems.append((rec.formid, problems))

    print(f"\n  ARMA validation: {arma_ok}/{len(ours_arma.records)} pass")
    for fid, probs in arma_problems[:5]:
        print(f"    {fid:#010x}: {probs}")

    # Sanity-check round-trip on the generated patch
    print(f"\n  round-trip check on generated patch:")
    rt_path = PROJ / "output" / f"{case['name']}_AUTO_UBE_patch.rt-tmp.esp"
    ours.save(rt_path)
    rt = esp.ESP.load(rt_path)
    assert rt.header.masters == ours.header.masters
    assert len(rt.groups) == len(ours.groups)
    print(f"    OK (saved and reloaded cleanly)")
    rt_path.unlink()


for case in CASES:
    check_case(case)

print("\n=== M2.1 done ===")


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
    # The module-level call below passes a fixed dir that may not exist yet;
    # also lets pytest collect this file (ESP.save needs the dir present).
    tmp_path.mkdir(parents=True, exist_ok=True)
    from src.ube_patcher import promote_slot49_cloth_to_slot32
    from src import esp as esp_mod
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


test_add_slot32_to_bod2_payload()
test_promote_slot49_cloth_uses_bodytri_predicate(
    Path(__file__).resolve().parent / "_tmp_slot32_promote"
)

print("\n=== slot-32 promotion tests done ===")


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


def test_armo_override_modls_grouped_before_data():
    """Every ARMO override the patcher emits must have ALL MODL armature
    subrecords grouped before DATA (canonical Skyrim ordering). If a MODL
    lands after DATA, Skyrim's parser ignores it.
    """
    from src.ube_patcher import (
        rebuild_arma_payload, ARMO_ARMATURE_SIG, _merge_armo_armatures,
    )
    from src.esp import encode_subrecord, encode_zstring

    # ---- Case A: source override path (add_arma_to_armo_payload) ----
    # A typical ARMO has its existing MODL BEFORE DATA. Appending a new
    # MODL must NOT push it past DATA.
    payload_existing_modl = (
        encode_subrecord(b"EDID", encode_zstring("TestArmor"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    from src.ube_patcher import add_arma_to_armo_payload
    out = add_arma_to_armo_payload(payload_existing_modl, 0x06000800)
    modls, data = _armo_modl_positions(out)
    assert len(modls) == 2, f"expected 2 MODLs, got {len(modls)}"
    assert data > 0, "DATA subrecord missing"
    for m in modls:
        assert m < data, (
            f"MODL at idx {m} is AFTER DATA at idx {data} — "
            "Skyrim parser will silently ignore it"
        )

    # ---- Case B: ARMO with NO existing MODL ----
    # Splice point should be just before DATA.
    payload_no_modl = (
        encode_subrecord(b"EDID", encode_zstring("TestArmor2"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    out2 = add_arma_to_armo_payload(payload_no_modl, 0x06000800)
    modls2, data2 = _armo_modl_positions(out2)
    assert len(modls2) == 1, f"expected 1 MODL, got {len(modls2)}"
    assert data2 > 0, "no-MODL payload lost DATA on insert"
    assert modls2[0] < data2, (
        f"add_arma_to_armo_payload with no existing MODL emitted MODL "
        f"at idx {modls2[0]} AFTER DATA at idx {data2}"
    )

    # ---- Case C: merger path (_merge_armo_armatures) ----
    # Two patches override the same ARMO with different new UBE ARMAs;
    # the merger should produce both MODLs grouped before DATA.
    a_payload = (
        encode_subrecord(b"EDID", encode_zstring("TestArmor3"))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x06000800))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    b_payload = (
        encode_subrecord(b"EDID", encode_zstring("TestArmor3"))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x07000800))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    merged = _merge_armo_armatures(a_payload, b_payload)
    modls3, data3 = _armo_modl_positions(merged)
    assert len(modls3) == 3, (
        f"merger should produce 3 deduped MODLs (vanilla + 2 UBE), "
        f"got {len(modls3)}"
    )
    assert data3 > 0, "merger lost DATA subrecord"
    for m in modls3:
        assert m < data3, (
            f"merger emitted MODL at idx {m} AFTER DATA at idx {data3}"
        )

    print("  test_armo_override_modls_grouped_before_data OK")


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


test_armo_override_modls_grouped_before_data()

print("\n=== ARMO MODL-ordering test done ===")


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


test_validate_patch_catches_modl_after_data(
    Path(__file__).resolve().parent / "_tmp_validator"
)

print("\n=== validate_patch test done ===")


# ---------------------------------------------------------------------------
# Cross-patch ARMO merge: when two patches override the same Skyrim.esm
# ARMO, the merger must combine their armatures lists. Reproduces the
# diagnostic we ran by hand on the user's HDT-SMP + Remodeled overlap.
# ---------------------------------------------------------------------------

def test_merge_combines_armo_armatures_from_multiple_patches(tmp_path):
    from src.ube_patcher import merge_patches
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring, iter_subrecords

    # Two patch ESPs, both override the same Skyrim.esm ARMO 0x00012E49
    # (ArmorIronCuirass). Each adds its own UBE ARMA into the armatures
    # list. The merger must produce a single ARMO record carrying BOTH
    # new UBE armatures (plus the original).
    def make_armo_override(own_arma_fid: int) -> Record:
        payload = (
            encode_subrecord(b"EDID", encode_zstring("ArmorIronCuirass"))
            + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
            + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
            + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
            + encode_subrecord(b"MODL", struct.pack("<I", own_arma_fid))
            + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
            + encode_subrecord(b"DNAM", struct.pack("<I", 0))
        )
        return Record(
            sig=b"ARMO", flags=0, formid=0x00012E49, timestamp_vc=0,
            version_unk=0x002C, payload=payload,
        )

    def make_arma_record(own_fid: int) -> Record:
        # Minimal ARMA: just EDID + RNAM, our merger doesn't need more.
        payload = (
            encode_subrecord(b"EDID", encode_zstring(f"ARMA_{own_fid:X}"))
            + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
            + encode_subrecord(b"RNAM", struct.pack("<I", 0x02005734))
            + encode_subrecord(b"DNAM",
                struct.pack("<IIf", 0x05050202, 0, 0.2))
            + encode_subrecord(b"MOD3", encode_zstring("Armor/test.nif"))
        )
        return Record(
            sig=b"ARMA", flags=0, formid=own_fid, timestamp_vc=0,
            version_unk=0x002C, payload=payload,
        )

    def make_patch(path: Path, own_arma_local: int) -> Path:
        masters = ["Skyrim.esm", "UBE_AllRace.esp"]
        own_byte = len(masters)
        own_arma_fid = (own_byte << 24) | own_arma_local
        armo = make_armo_override(own_arma_fid)
        arma = make_arma_record(own_arma_fid)
        esp_obj = ESP(
            header=TES4Header(masters=masters, num_records=0,
                              next_object_id=own_arma_local + 1,
                              version=1.7),
            groups=[Group(label=b"ARMO", records=[armo]),
                    Group(label=b"ARMA", records=[arma])],
        )
        esp_obj.save(path)
        return path

    tmp_path.mkdir(parents=True, exist_ok=True)
    p1 = make_patch(tmp_path / "patch_a.esp", 0x800)
    p2 = make_patch(tmp_path / "patch_b.esp", 0x801)
    out = tmp_path / "merged.esp"
    stats = merge_patches([p1, p2], out, esl_flag=True)

    assert stats["armo_duplicates_merged"] == 1, \
        f"expected 1 duplicate ARMO merge, got {stats['armo_duplicates_merged']}"
    assert stats["total_armo_records"] == 1, \
        "merged ARMO should appear exactly once after dedup"

    # Reload + count armatures on the merged ARMO.
    merged = ESP.load(out)
    armo_grp = next(g for g in merged.groups if g.label == b"ARMO")
    armatures = []
    for sig, sd in iter_subrecords(armo_grp.records[0].payload):
        if sig == b"MODL" and len(sd) == 4:
            armatures.append(struct.unpack("<I", sd)[0])
    # Expected: 0x00012E48 (vanilla) + 2 new UBE ARMAs from both patches.
    assert len(armatures) == 3, \
        f"merged ARMO should have 3 armatures, got {len(armatures)}: " \
        f"{[f'{a:08X}' for a in armatures]}"
    assert 0x00012E48 in armatures, "vanilla armature dropped"

    print("  test_merge_combines_armo_armatures_from_multiple_patches OK")


test_merge_combines_armo_armatures_from_multiple_patches(
    Path(__file__).resolve().parent / "_tmp_armo_merge"
)


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


test_merge_downgrades_to_full_esp_on_esl_overflow(
    Path(__file__).resolve().parent / "_tmp_esl_overflow"
)


# ---------------------------------------------------------------------------
# FormID-out-of-range detection in validate_patch. Reproduces the
# diagnostic that caught the post-CC-removal vanilla-compat patch.
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


test_validate_patch_catches_out_of_range_formid(
    Path(__file__).resolve().parent / "_tmp_oor"
)


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


test_validate_patch_catches_missing_nif(
    Path(__file__).resolve().parent / "_tmp_missing_nif"
)


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


test_validate_patch_catches_unmappable_transitive_master(
    Path(__file__).resolve().parent / "_tmp_unmappable_master"
)


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


test_synthesize_name_from_edid()


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


def test_master_armo_override_emits_full_when_source_lacks_it(tmp_path):
    """When generate_ube_patch's master-ARMO scan copies a localized-master
    ARMO whose FULL is an LSTRING ref, the override emits a synthesized
    FULL derived from EDID instead of nothing. Skyrim's inventory UI
    requires FULL to display the item.
    """
    from src.ube_patcher import generate_ube_patch
    from src.esp import (
        ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring,
        iter_subrecords,
    )

    tmp_path.mkdir(parents=True, exist_ok=True)

    # Synthetic Skyrim.esm with one ARMA + one ARMO that references it,
    # where the ARMO's FULL is an LSTRING-style 4-byte ref. The ARMO
    # also has KSIZ/KWDA/DATA/DNAM to look complete.
    arma_payload = (
        encode_subrecord(b"EDID", encode_zstring("TestARMA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
        + encode_subrecord(b"MOD3", encode_zstring("Armor\\Test\\test.nif"))
    )
    armo_payload = (
        encode_subrecord(b"EDID", encode_zstring("ArmorTestCuirass"))
        + encode_subrecord(b"OBND", b"\x00" * 12)
        + encode_subrecord(b"FULL", struct.pack("<I", 0x12345678))  # LSTRING ref
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00000100))  # arma ref
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    arma_rec = Record(sig=b"ARMA", flags=0, formid=0x00000100,
                      timestamp_vc=0, version_unk=0x002C, payload=arma_payload)
    armo_rec = Record(sig=b"ARMO", flags=0, formid=0x00000200,
                      timestamp_vc=0, version_unk=0x002C, payload=armo_payload)
    fake_master = ESP(
        header=TES4Header(masters=[], num_records=0, next_object_id=0x300,
                          version=1.7),
        groups=[Group(label=b"ARMO", records=[armo_rec]),
                Group(label=b"ARMA", records=[arma_rec])],
    )
    fake_master_path = tmp_path / "Skyrim.esm"  # mimic vanilla master name
    fake_master.save(fake_master_path)

    # Source ESP that overrides the ARMA (CBBE-style replacer). Master
    # ARMO scan will then find the ARMO in fake_master and emit override.
    src_arma_payload = (
        encode_subrecord(b"EDID", encode_zstring("TestARMA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"DNAM", struct.pack("<IIf", 0x05050202, 0, 0.2))
        + encode_subrecord(b"MOD3", encode_zstring("CBBE\\Test\\test.nif"))
    )
    # Override Skyrim's ARMA 0x00000100 — that's the trigger that makes
    # the master ARMO scan pick up its referencing ARMO.
    src_arma_rec = Record(sig=b"ARMA", flags=0, formid=0x00000100,
                          timestamp_vc=0, version_unk=0x002C,
                          payload=src_arma_payload)
    src_esp = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x101, version=1.7),
        groups=[Group(label=b"ARMA", records=[src_arma_rec])],
    )
    src_esp_path = tmp_path / "Source.esp"
    src_esp.save(src_esp_path)

    # We also need a UBE_AllRace.esp file so master discovery works.
    ube_payload = b""
    ube_esp = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x800, version=1.7),
        groups=[],
    )
    ube_esp.save(tmp_path / "UBE_AllRace.esp")

    # Run generator.
    out = tmp_path / "out.esp"
    generate_ube_patch(src_esp_path, out, master_data_dirs=[tmp_path])

    # Reload + find the override of our ARMO (FormID 0x00000200 in
    # Skyrim's space, byte 0 in our patch).
    result = ESP.load(out)
    armo_grp = next((g for g in result.groups if g.label == b"ARMO"), None)
    assert armo_grp is not None, "patch has no ARMO group"
    override = next((r for r in armo_grp.records
                     if (r.formid & 0xFFFFFF) == 0x000200), None)
    assert override is not None, "ARMO override missing from generated patch"

    full_value = None
    for sig, data in iter_subrecords(override.payload):
        if sig == b"FULL":
            full_value = data.rstrip(b"\x00").decode("latin1", errors="ignore")
            break
    assert full_value is not None, (
        "FULL subrecord missing from override — inventory UI will hide "
        "the item"
    )
    # The synthesized name should be derived from EDID, NOT the raw LSTRING.
    assert full_value == "Test Cuirass", (
        f"expected synthesized FULL 'Test Cuirass', got {full_value!r}"
    )

    print("  test_master_armo_override_emits_full_when_source_lacks_it OK")


test_master_armo_override_emits_full_when_source_lacks_it(
    Path(__file__).resolve().parent / "_tmp_full_synth"
)


def test_validate_patch_catches_armo_missing_full(tmp_path):
    """validate_patch must flag ARMO overrides that have no FULL
    subrecord — Skyrim's inventory UI silently hides them.
    """
    from src.ube_patcher import validate_patch
    from src.esp import (
        ESP, TES4Header, Group, Record, encode_subrecord, encode_zstring,
    )

    tmp_path.mkdir(parents=True, exist_ok=True)

    # ARMO override deliberately missing FULL.
    armo_no_full = (
        encode_subrecord(b"EDID", encode_zstring("ArmorNoFull"))
        + encode_subrecord(b"OBND", b"\x00" * 12)
        + encode_subrecord(b"MOD2", encode_zstring("foo.nif"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    rec = Record(sig=b"ARMO", flags=0, formid=0x00012E49, timestamp_vc=0,
                 version_unk=0x002C, payload=armo_no_full)
    bad_esp = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMO", records=[rec])],
    )
    out_path = tmp_path / "no_full.esp"
    bad_esp.save(out_path)

    warnings = validate_patch(out_path, check_nifs=False)
    matched = [w for w in warnings if w.startswith("armo-missing-full")]
    assert matched, (
        f"validate_patch did NOT catch ARMO missing FULL: {warnings}"
    )

    # Now add FULL — warning should disappear.
    armo_with_full = (
        encode_subrecord(b"EDID", encode_zstring("ArmorNoFull"))
        + encode_subrecord(b"OBND", b"\x00" * 12)
        + encode_subrecord(b"FULL", encode_zstring("Some Armor"))
        + encode_subrecord(b"MOD2", encode_zstring("foo.nif"))
        + encode_subrecord(b"BOD2", struct.pack("<II", 0x4, 4))
        + encode_subrecord(b"RNAM", struct.pack("<I", 0x00000019))
        + encode_subrecord(b"MODL", struct.pack("<I", 0x00012E48))
        + encode_subrecord(b"DATA", struct.pack("<II", 100, 5))
        + encode_subrecord(b"DNAM", struct.pack("<I", 0))
    )
    rec2 = Record(sig=b"ARMO", flags=0, formid=0x00012E49, timestamp_vc=0,
                  version_unk=0x002C, payload=armo_with_full)
    good_esp = ESP(
        header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x801, version=1.7),
        groups=[Group(label=b"ARMO", records=[rec2])],
    )
    out_path2 = tmp_path / "with_full.esp"
    good_esp.save(out_path2)
    warnings2 = validate_patch(out_path2, check_nifs=False)
    matched2 = [w for w in warnings2 if w.startswith("armo-missing-full")]
    assert not matched2, (
        f"validate_patch flagged armo-missing-full when FULL IS present: "
        f"{warnings2}"
    )

    print("  test_validate_patch_catches_armo_missing_full OK")


test_validate_patch_catches_armo_missing_full(
    Path(__file__).resolve().parent / "_tmp_armo_full"
)


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


test_build_nif_slot_map(
    Path(__file__).resolve().parent / "_tmp_slot_map"
)

print("\n=== validator extended tests done ===")
