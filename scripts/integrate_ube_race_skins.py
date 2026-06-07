"""Integrate per-race UBE skin ARMAs into UBE_AllRace.esp.

UBE 2.0's `00UBE_SkinNaked` ARMO is the SkinForm for all 18 UBE races,
but its only UBE-routed ARMAs (00UBE_NakedTorso/NakedHands/NakedFeet)
have RNAM primary = Breton UBE only. The Skyrim engine matches ARMAs
to actors by RNAM primary; the MODL "additional races" list is NOT
used for runtime ARMA selection. For any UBE race other than Breton,
the engine fails to find a primary-matching UBE ARMA in SkinNaked's
armatures list and falls back to the race's vanilla SkinForm — which
loads body/hands/feet from `meshes/actors/character/character assets/
femalebody_1.nif` (etc.), the actor-asset path your CBBE 3BA install
provides as CBBE-topology meshes.

This script generates per-race UBE skin ARMAs and registers them in
SkinNaked. After patching:

  * For every UBE race (Breton/Imperial/Nord/Redguard/Dark+High+Wood
    Elf/Orc + Vampire variants + CustomRace01/02), there is a triple
    of NEW ARMA records (body, hands, feet) with RNAM primary = that
    race and MOD3 = the UBE mesh path.
  * SkinNaked's armatures list is extended with all new ARMA FormIDs
    PREPENDED to the existing list — engine walks armatures in order,
    so prepending ensures UBE ARMAs match before any vanilla fallback.
  * Non-UBE races are untouched (their SkinForm isn't SkinNaked, so
    this change doesn't affect them).

Backs the original up to `<path>.bak` first. Idempotent — running
twice is a no-op (won't re-create ARMAs that already exist).

Usage:
  python scripts/integrate_ube_race_skins.py
  python scripts/integrate_ube_race_skins.py --esp 'D:\\path\\to\\UBE_AllRace.esp'
  python scripts/integrate_ube_race_skins.py --dry-run
"""
import argparse
import io
import shutil
import struct
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import esp  # noqa: E402

DEFAULT_CANDIDATES = [
    Path(r"<MODLIST>\mods\UBE_AllRace Newrite Replacement\UBE_AllRace.esp"),
    Path(r"<MODLIST>\mods\UBE 2.0 U. 0.7\UBE_AllRace.esp"),
]

# Source ARMA EDIDs to clone (one per slot). RNAM gets swapped per race.
TEMPLATE_EDIDS = {
    "Torso": "00UBE_NakedTorso",
    "Hands": "00UBE_NakedHands",
    "Feet":  "00UBE_NakedFeet",
}

# SkinNaked ARMO EDID — its armatures (MODL) list gets extended.
SKIN_ARMO_EDID = "00UBE_SkinNaked"

# All UBE races defined in UBE_AllRace.esp (FormID in its own address
# space, top byte 0x03). EDID-suffix for new ARMA naming. The order
# here determines the order new ARMAs are listed in SkinNaked.
ALL_UBE_RACES = [
    ("Breton",            0x03005734),
    ("BretonVampire",     0x03005735),
    ("Imperial",          0x0305a179),
    ("ImperialVampire",   0x0305a17a),
    ("Nord",              0x0305a184),
    ("NordVampire",       0x0305a185),
    ("Redguard",          0x0305a18e),
    ("RedguardVampire",   0x0305a18f),
    ("DarkElf",           0x0305a198),
    ("DarkElfVampire",    0x0305a199),
    ("HighElf",           0x0305a1a2),
    ("HighElfVampire",    0x0305a1a3),
    ("WoodElf",           0x0305a1ac),
    ("WoodElfVampire",    0x0305a1ad),
    ("Orc",               0x0305a1b0),
    ("OrcVampire",        0x0305a1b1),
    ("CustomRace01",      0x0307a4d5),
    ("CustomRace02",      0x0307a4d6),
]


def find_template_armas(arma_records: list) -> dict[str, esp.Record]:
    """Find the 3 source ARMAs by EDID."""
    out: dict[str, esp.Record] = {}
    for rec in arma_records:
        for sig, data in esp.iter_subrecords(rec.payload):
            if sig == b"EDID":
                e = data.rstrip(b"\x00").decode("ascii", "replace")
                for slot_label, edid in TEMPLATE_EDIDS.items():
                    if e == edid:
                        out[slot_label] = rec
                break
    return out


def find_skin_armo(armo_records: list) -> "esp.Record | None":
    """Find the 00UBE_SkinNaked ARMO."""
    for rec in armo_records:
        for sig, data in esp.iter_subrecords(rec.payload):
            if sig == b"EDID":
                e = data.rstrip(b"\x00").decode("ascii", "replace")
                if e == SKIN_ARMO_EDID:
                    return rec
                break
    return None


def make_new_arma_payload(
        template: esp.Record, new_edid: str, new_rnam_fid: int,
) -> bytes:
    """Clone a template ARMA's payload, swapping EDID + RNAM.

    Keeps every other subrecord verbatim (BOD2, DNAM, MOD2/MO2T,
    MOD3/MO3T, MOD4/MOD5, MODL extras). The result is a new ARMA that
    routes the new_rnam_fid race to the same mesh as the template.
    """
    out = b""
    edid_replaced = False
    rnam_replaced = False
    for sig, data in esp.iter_subrecords(template.payload):
        if sig == b"EDID":
            new_data = new_edid.encode("ascii") + b"\x00"
            out += esp.encode_subrecord(b"EDID", new_data)
            edid_replaced = True
        elif sig == b"RNAM":
            new_data = struct.pack("<I", new_rnam_fid)
            out += esp.encode_subrecord(b"RNAM", new_data)
            rnam_replaced = True
        else:
            out += esp.encode_subrecord(sig, data)
    if not edid_replaced or not rnam_replaced:
        raise RuntimeError(
            f"template missing EDID or RNAM: "
            f"edid={edid_replaced} rnam={rnam_replaced}")
    return out


def update_skin_armo_armatures(
        skin_armo: esp.Record, new_arma_fids: list[int],
) -> bytes:
    """Prepend new ARMA FormIDs as MODL subrecords to the ARMO's
    armatures list. Idempotent — skips FIDs already present.

    Armatures (MODL) are inserted right after the first non-MODL
    subrecord block ends (matches Skyrim's convention; the engine
    just needs them to appear in the list — order within the MODL
    block is what determines walk order).
    """
    existing: set[int] = set()
    for sig, data in esp.iter_subrecords(skin_armo.payload):
        if sig == b"MODL" and len(data) == 4:
            existing.add(struct.unpack("<I", data)[0])
    to_add = [fid for fid in new_arma_fids if fid not in existing]
    if not to_add:
        return skin_armo.payload

    new_modls = b"".join(
        esp.encode_subrecord(b"MODL", struct.pack("<I", fid))
        for fid in to_add
    )

    # Insert new MODLs immediately BEFORE the first existing MODL so
    # they're walked first. If there are no existing MODLs, append at
    # the end.
    out = b""
    inserted = False
    for sig, data in esp.iter_subrecords(skin_armo.payload):
        if sig == b"MODL" and not inserted:
            out += new_modls
            inserted = True
        out += esp.encode_subrecord(sig, data)
    if not inserted:
        out += new_modls
    return out


def find_max_own_fid(e: esp.ESP) -> int:
    """Highest own-record FormID across all groups (own = top byte
    equal to len(masters))."""
    own_top = len(e.header.masters)
    max_low = 0
    for g in e.groups:
        for rec in g.records:
            top = (rec.formid >> 24) & 0xFF
            if top == own_top:
                low = rec.formid & 0x00FFFFFF
                if low > max_low:
                    max_low = low
    return max_low


def main():
    ap = argparse.ArgumentParser(
        description="Add per-race UBE skin ARMAs to UBE_AllRace.esp")
    ap.add_argument("--esp", type=Path, default=None,
                    help="Path to UBE_AllRace.esp to patch")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing")
    args = ap.parse_args()

    esp_path = args.esp
    if esp_path is None:
        for cand in DEFAULT_CANDIDATES:
            if cand.is_file():
                esp_path = cand
                break
    if esp_path is None or not esp_path.is_file():
        print(f"FATAL: no UBE_AllRace.esp found (tried {DEFAULT_CANDIDATES})")
        sys.exit(1)
    print(f"Patching: {esp_path}")

    e = esp.ESP.load(esp_path)
    print(f"  masters: {e.header.masters}")

    arma_g = next((g for g in e.groups if g.label == b"ARMA"), None)
    armo_g = next((g for g in e.groups if g.label == b"ARMO"), None)
    if arma_g is None or armo_g is None:
        print("FATAL: ARMA or ARMO group missing")
        sys.exit(1)

    templates = find_template_armas(arma_g.records)
    missing = [k for k in TEMPLATE_EDIDS if k not in templates]
    if missing:
        print(f"FATAL: template ARMAs not found: "
              f"{[TEMPLATE_EDIDS[k] for k in missing]}")
        sys.exit(1)
    print(f"  found {len(templates)} template ARMAs: "
          f"{[t.formid for t in templates.values()]}")

    skin_armo = find_skin_armo(armo_g.records)
    if skin_armo is None:
        print(f"FATAL: ARMO {SKIN_ARMO_EDID!r} not found")
        sys.exit(1)
    print(f"  found SkinNaked ARMO: 0x{skin_armo.formid:08x}")

    # Existing race -> slot ARMA map (so we don't duplicate).
    existing_race_slot: set[tuple[int, str]] = set()
    for rec in arma_g.records:
        edid_v = None
        rnam_v = None
        for sig, data in esp.iter_subrecords(rec.payload):
            if sig == b"EDID":
                edid_v = data.rstrip(b"\x00").decode("ascii", "replace")
            elif sig == b"RNAM" and len(data) == 4:
                rnam_v = struct.unpack("<I", data)[0]
        if edid_v is None or rnam_v is None:
            continue
        for slot_label, base_edid in TEMPLATE_EDIDS.items():
            # Our naming convention: <base_edid>_<RaceTag>
            # The original 3 are "<base_edid>" with no suffix (Breton).
            if edid_v == base_edid or edid_v.startswith(base_edid + "_"):
                existing_race_slot.add((rnam_v, slot_label))
                break

    # Compute new records.
    own_top = len(e.header.masters)
    own_top_byte = own_top << 24
    next_low = find_max_own_fid(e) + 1
    new_records: list[esp.Record] = []
    new_skin_fids: list[int] = []
    for race_tag, race_fid in ALL_UBE_RACES:
        for slot_label, base_edid in TEMPLATE_EDIDS.items():
            if (race_fid, slot_label) in existing_race_slot:
                continue
            new_edid = f"{base_edid}_{race_tag}"
            new_fid = own_top_byte | next_low
            next_low += 1
            template = templates[slot_label]
            payload = make_new_arma_payload(template, new_edid, race_fid)
            new_records.append(esp.Record(
                sig=b"ARMA",
                flags=0,
                formid=new_fid,
                timestamp_vc=0,
                version_unk=0x002C,
                payload=payload,
            ))
            new_skin_fids.append(new_fid)

    if not new_records:
        print("Nothing to do — every UBE race already has dedicated ARMAs.")
        return

    print(f"\n  will add {len(new_records)} new ARMAs:")
    by_race: dict[str, list[str]] = {}
    for race_tag, _ in ALL_UBE_RACES:
        by_race[race_tag] = []
    for rec in new_records:
        for sig, data in esp.iter_subrecords(rec.payload):
            if sig == b"EDID":
                edid_v = data.rstrip(b"\x00").decode("ascii", "replace")
                # Last token after "_" is the race tag
                race_tag = edid_v.rsplit("_", 1)[-1]
                if race_tag in by_race:
                    by_race[race_tag].append(edid_v)
                break
    for race_tag, edids in by_race.items():
        if edids:
            print(f"    {race_tag}: {len(edids)} ARMAs")

    if args.dry_run:
        print(f"\nDRY RUN: would write {len(new_records)} ARMAs and update "
              f"SkinNaked with {len(new_skin_fids)} new MODL entries. "
              "No file written.")
        return

    # Backup.
    bak_path = esp_path.with_suffix(esp_path.suffix + ".bak")
    if not bak_path.is_file():
        shutil.copy2(esp_path, bak_path)
        print(f"  backed up original to {bak_path.name}")
    else:
        print(f"  backup {bak_path.name} already exists (preserved)")

    # Append new ARMAs to the ARMA group.
    arma_g.records.extend(new_records)
    # Update SkinNaked's armatures list.
    skin_armo.payload = update_skin_armo_armatures(skin_armo, new_skin_fids)
    # Update next_object_id so future patches don't collide.
    if next_low > (e.header.next_object_id & 0x00FFFFFF):
        e.header.next_object_id = next_low

    e.save(esp_path)
    print(f"\nWrote {esp_path.name}: +{len(new_records)} ARMA records, "
          f"SkinNaked now has {len(new_skin_fids)} additional armatures.")


if __name__ == "__main__":
    main()
