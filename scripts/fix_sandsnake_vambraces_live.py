# LIVE hot-fix: Sand Snake vambraces make the hands invisible.
# Cause: the equipped ARMO claims biped slot 33 (Hands) + 34 (Forearms), but the
# converted mesh is a forearm bracer with ~2% hand-bone weight and NO hand
# geometry. Claiming slot 33 suppresses the nude UBE hands while drawing nothing
# there -> invisible hands. The original RB's mesh was forearms-only (0x10); the
# "Authoria - Rectificated" patch added the Hands bit and our merge carried it.
# Fix: clear the slot-33 (Hands) bit from the vambraces ARMOs and their
# Hands+Forearms UBE ARMAs, leaving forearms-only (0x10). The clean forearms-only
# UBE ARMAs (0x10) already exist; the mesh has no alpha so the duplicate
# armature's identical-geometry double-draw is harmless. Mesh untouched.
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Sam\Downloads\cbbe-to-ube")
from src import esp

ESP_PATH = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\CBBE_to_UBE_Combined2.esp")
HANDS_BIT = 1 << (33 - 30)   # 0x8

# Records to fix (vambraces n1/n2/n3): the 3 ARMOs + the 3 Hands+Forearms UBE ARMAs.
TARGET_FIDS = {
    0x27000915, 0x27000919, 0x2700091D,   # ARMO vambraces / n2 / n3
    0x94000C7B, 0x94000C7C, 0x94000C7D,   # ARMA *_UBE Hands+Forearms duplicates
}


def clear_hands_in_payload(payload: bytes):
    """Return (new_payload, changed_bool). Clears the Hands bit in the first
    u32 of any BOD2/BODT subrecord."""
    out = bytearray()
    changed = False
    i = 0
    n = len(payload)
    while i + 6 <= n:
        sig = payload[i:i + 4]
        size = struct.unpack_from("<H", payload, i + 4)[0]
        data = payload[i + 6:i + 6 + size]
        if sig in (b"BOD2", b"BODT") and len(data) >= 4:
            bits = struct.unpack_from("<I", data, 0)[0]
            if bits & HANDS_BIT:
                new_bits = bits & ~HANDS_BIT
                data = struct.pack("<I", new_bits) + data[4:]
                changed = True
        out += sig + struct.pack("<H", size) + data
        i += 6 + size
    return bytes(out), changed


def main():
    bak = ESP_PATH.with_suffix(".esp.prevambracesfix.bak")
    if not bak.exists():
        shutil.copy2(ESP_PATH, bak)
        print(f"backup -> {bak.name}")
    else:
        print(f"backup already exists: {bak.name}")

    e = esp.ESP.load(str(ESP_PATH))
    fixed = []
    for g in e.groups:
        if g.label not in (b"ARMA", b"ARMO"):
            continue
        for r in g.records:
            if r.formid in TARGET_FIDS:
                new_payload, changed = clear_hands_in_payload(r.payload)
                if changed:
                    r.payload = new_payload
                    fixed.append((g.label.decode(), r.formid))
    if not fixed:
        print("no records changed (already fixed?)")
        return
    e.save(str(ESP_PATH))
    for kind, fid in fixed:
        print(f"  cleared HANDS bit: {kind} {fid:08X}")
    print(f"saved {ESP_PATH.name} ({len(fixed)} records)")


main()
