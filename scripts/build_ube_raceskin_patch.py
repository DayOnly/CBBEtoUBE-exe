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

"""Build a STANDALONE override patch (lives entirely in our mod) that
routes every HUMAN UBE race's nude skin to the !UBE meshes.

UBE_AllRace.esp's 00UBE_SkinNaked only has primary-race UBE skin ARMAs
for Breton; the engine matches nude-skin ARMAs by RNAM PRIMARY race only
(additional-races is ignored at runtime), so every other UBE race (incl.
Redguard) falls back to the vanilla actor-asset meshes -> CBBE hands/feet
on a UBE-ish body. integrate_ube_race_skins.py fixes this by editing
UBE_AllRace.esp IN PLACE; this script instead emits a SEPARATE override
ESP so UBE_AllRace.esp is never touched (change is 100% in our mod).

Output: <CBBEtoUBE Auto>/UBE_RaceSkin_Patch.esp
  masters: [Skyrim.esm, Dawnguard.esm, RaceCompatibility.esm, UBE_AllRace.esp]
           (same order as UBE_AllRace's masters + itself, so every armature
            FormID in SkinNaked maps with its top byte UNCHANGED -> no remap)
  - new ARMA records (own, top byte 4): per human UBE race x {Torso,Hands,Feet},
    cloned from 00UBE_NakedTorso/Hands/Feet with RNAM PRIMARY = that race.
  - ARMO override of 00UBE_SkinNaked: full copy + new ARMA FormIDs PREPENDED
    to the armatures list (engine walks in order -> UBE wins).
  ESL-flagged (own records in FE-space 0x800+). Beast races NOT included.
"""
import os
import io, sys, struct
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import esp

# --- helpers (inlined from scripts/integrate_ube_race_skins.py to avoid its
#     module-level sys.stdout reassignment) ---
TEMPLATE_EDIDS = {"Torso": "00UBE_NakedTorso", "Hands": "00UBE_NakedHands",
                  "Feet": "00UBE_NakedFeet"}
SKIN_ARMO_EDID = "00UBE_SkinNaked"
ALL_UBE_RACES = [
    ("Breton", 0x03005734), ("BretonVampire", 0x03005735),
    ("Imperial", 0x0305a179), ("ImperialVampire", 0x0305a17a),
    ("Nord", 0x0305a184), ("NordVampire", 0x0305a185),
    ("Redguard", 0x0305a18e), ("RedguardVampire", 0x0305a18f),
    ("DarkElf", 0x0305a198), ("DarkElfVampire", 0x0305a199),
    ("HighElf", 0x0305a1a2), ("HighElfVampire", 0x0305a1a3),
    ("WoodElf", 0x0305a1ac), ("WoodElfVampire", 0x0305a1ad),
    ("Orc", 0x0305a1b0), ("OrcVampire", 0x0305a1b1),
    ("CustomRace01", 0x0307a4d5), ("CustomRace02", 0x0307a4d6),
]

class _IU:
    TEMPLATE_EDIDS = TEMPLATE_EDIDS
    SKIN_ARMO_EDID = SKIN_ARMO_EDID
    ALL_UBE_RACES = ALL_UBE_RACES
    @staticmethod
    def find_template_armas(arma_records):
        out = {}
        for rec in arma_records:
            for sig, data in esp.iter_subrecords(rec.payload):
                if sig == b"EDID":
                    ev = data.rstrip(b"\x00").decode("ascii", "replace")
                    for sl, ed in TEMPLATE_EDIDS.items():
                        if ev == ed:
                            out[sl] = rec
                    break
        return out
    @staticmethod
    def find_skin_armo(armo_records):
        for rec in armo_records:
            for sig, data in esp.iter_subrecords(rec.payload):
                if sig == b"EDID":
                    if data.rstrip(b"\x00").decode("ascii", "replace") == SKIN_ARMO_EDID:
                        return rec
                    break
        return None
    @staticmethod
    def make_new_arma_payload(template, new_edid, new_rnam_fid):
        out = b""; er = rr = False
        for sig, data in esp.iter_subrecords(template.payload):
            if sig == b"EDID":
                out += esp.encode_subrecord(b"EDID", new_edid.encode("ascii") + b"\x00"); er = True
            elif sig == b"RNAM":
                out += esp.encode_subrecord(b"RNAM", struct.pack("<I", new_rnam_fid)); rr = True
            else:
                out += esp.encode_subrecord(sig, data)
        if not er or not rr:
            raise RuntimeError(f"template missing EDID/RNAM: edid={er} rnam={rr}")
        return out
    @staticmethod
    def update_skin_armo_armatures(skin_armo, new_arma_fids):
        existing = set()
        for sig, data in esp.iter_subrecords(skin_armo.payload):
            if sig == b"MODL" and len(data) == 4:
                existing.add(struct.unpack("<I", data)[0])
        to_add = [f for f in new_arma_fids if f not in existing]
        if not to_add:
            return skin_armo.payload
        new_modls = b"".join(esp.encode_subrecord(b"MODL", struct.pack("<I", f)) for f in to_add)
        out = b""; inserted = False
        for sig, data in esp.iter_subrecords(skin_armo.payload):
            if sig == b"MODL" and not inserted:
                out += new_modls; inserted = True
            out += esp.encode_subrecord(sig, data)
        if not inserted:
            out += new_modls
        return out

iu = _IU()

UBE = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\UBE 2.0 U. 0.7\UBE_AllRace.esp")
OUT = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto\UBE_RaceSkin_Patch.esp")
ESL_FLAG = 0x00000200

def main():
    e = esp.ESP.load(UBE)
    print(f"UBE_AllRace masters: {e.header.masters}")
    arma_g = next(g for g in e.groups if g.label == b"ARMA")
    armo_g = next(g for g in e.groups if g.label == b"ARMO")
    templates = iu.find_template_armas(arma_g.records)
    miss = [k for k in iu.TEMPLATE_EDIDS if k not in templates]
    if miss:
        print(f"FATAL: missing templates {miss}"); return
    skin = iu.find_skin_armo(armo_g.records)
    if skin is None:
        print("FATAL: 00UBE_SkinNaked not found"); return
    print(f"templates: {[t.formid for t in templates.values()]}  "
          f"SkinNaked=0x{skin.formid:08x}")

    # masters = UBE's masters + UBE_AllRace itself -> UBE-own (top byte 3)
    # maps to index 3 (UBE_AllRace) with NO remap.
    masters = list(e.header.masters) + ["UBE_AllRace.esp"]
    own_top = len(masters)              # 4
    assert own_top == 4, masters

    new_armas = []
    new_fids = []
    next_low = 0x800
    per_race = {}
    for race_tag, race_fid in iu.ALL_UBE_RACES:
        for slot_label, base_edid in iu.TEMPLATE_EDIDS.items():
            new_edid = f"{base_edid}_{race_tag}"
            payload = iu.make_new_arma_payload(
                templates[slot_label], new_edid, race_fid)
            fid = (own_top << 24) | next_low
            next_low += 1
            new_armas.append(esp.Record(
                sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                version_unk=0x002C, payload=payload))
            new_fids.append(fid)
            per_race.setdefault(race_tag, 0)
            per_race[race_tag] += 1

    # SkinNaked override: copy payload from master, prepend new ARMA FIDs.
    skin_ovr = esp.Record(sig=b"ARMO", flags=0, formid=skin.formid,
                          timestamp_vc=0, version_unk=0x002C,
                          payload=skin.payload)
    skin_ovr.payload = iu.update_skin_armo_armatures(skin_ovr, new_fids)

    hdr = esp.TES4Header(
        masters=masters, author="cbbe-to-ube",
        description="UBE per-race nude-skin routing (override; UBE_AllRace untouched)",
        flags=ESL_FLAG, version=1.7, next_object_id=next_low)
    out = esp.ESP(header=hdr, groups=[
        esp.Group(label=b"ARMA", records=new_armas),
        esp.Group(label=b"ARMO", records=[skin_ovr]),
    ])
    out.save(OUT)
    print(f"\nWROTE {OUT.name}: {len(new_armas)} new ARMAs across "
          f"{len(per_race)} races + 1 SkinNaked override (ESL).")
    print(f"  own top byte 0x{own_top:02x}; FE-space FormIDs "
          f"0x{0x800:X}..0x{next_low-1:X}")

    # round-trip verify
    chk = esp.ESP.load(OUT)
    ca = next(g for g in chk.groups if g.label == b"ARMA")
    co = next(g for g in chk.groups if g.label == b"ARMO")
    cskin = co.records[0]
    arm_refs = [struct.unpack("<I", d)[0]
                for s, d in esp.iter_subrecords(cskin.payload)
                if s == b"MODL" and len(d) == 4]
    own_in_skin = [f for f in arm_refs if (f >> 24) == own_top]
    print(f"  verify: masters={chk.header.masters} ESL={bool(chk.header.flags & ESL_FLAG)}")
    print(f"  verify: ARMA records={len(ca.records)}  SkinNaked armatures={len(arm_refs)}"
          f" (of which OUR new={len(own_in_skin)})")
    # check every own ARMA referenced by SkinNaked exists
    own_arma_fids = {r.formid for r in ca.records}
    dangling = [f for f in own_in_skin if f not in own_arma_fids]
    print(f"  verify: dangling own refs in SkinNaked: {len(dangling)} (expect 0)")
    # show Redguard coverage
    rg = [r for r in ca.records if b"Redguard" in r.payload]
    print(f"  verify: Redguard ARMAs present: {len(rg)} (expect 3: Torso/Hands/Feet)")

if __name__ == "__main__":
    main()
