# Diag: dump vambraces ARMA/ARMO records from every Sand Snake plugin in play
# to explain the duplicate UBE AA pair (000C7B Hands+Forearms vanilla+UBE vs
# 000C83 Forearms-only UBE-only) on the final ARMO override.
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))

from src import esp  # noqa: E402

FILES = [
    r"D:\Modlists\ARR\mods\RB's Sand Snake Armor and Weapon\RB's Sand Snake CBBE 3BA Bodyslide ESPFE.esp",
    r"D:\Modlists\ARR\mods\Authoria - Sand Snake Fixed Plugin\RB's Sand Snake CBBE 3BA Bodyslide ESPFE.esp",
    r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\_unmerged_patches\RB's Sand Snake CBBE 3BA Bodyslide ESPFE UBE patch.esp",
    r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\_unmerged_patches\Authoria - Rectificated - Sandsnake Armor UBE patch.esp",
]

SLOT_NAMES = {i: f"slot{30+i}" for i in range(32)}


def slots(bits):
    out = []
    for i in range(32):
        if bits & (1 << i):
            s = 30 + i
            tag = {33: "HANDS", 34: "FOREARMS", 32: "BODY", 37: "FEET",
                   38: "CALVES"}.get(s, str(s))
            out.append(tag)
    return "+".join(out) or "none"


for f in FILES:
    p = Path(f)
    if not p.is_file():
        print(f"\n##### MISSING: {f}")
        continue
    e = esp.ESP.load(p)
    print(f"\n##### {p.parent.name}\\{p.name}")
    print(f"  masters: {[m.decode() if isinstance(m, bytes) else m for m in e.header.masters]}")
    for g in e.groups:
        if g.label not in (b"ARMA", b"ARMO"):
            continue
        for r in g.records:
            edid = None
            bod2 = None
            rnam = None
            modl = []
            mod3 = None
            races = []
            for sig, d in esp.iter_subrecords(r.payload):
                if sig == b"EDID":
                    edid = d.rstrip(b"\x00").decode("utf-8", "ignore")
                elif sig in (b"BOD2", b"BODT") and len(d) >= 4:
                    import struct
                    bod2 = struct.unpack_from("<I", d, 0)[0]
                elif sig == b"RNAM" and len(d) == 4:
                    import struct
                    rnam = struct.unpack("<I", d)[0]
                elif sig == b"MODL" and g.label == b"ARMO" and len(d) == 4:
                    import struct
                    modl.append(struct.unpack("<I", d)[0])
                elif sig == b"MOD3":
                    mod3 = d.rstrip(b"\x00").decode("utf-8", "ignore")
                elif sig == b"MNAM" and len(d) == 4:
                    import struct
                    races.append(struct.unpack("<I", d)[0])
            if edid and "vambraces" in edid.lower() and "n2" not in edid.lower() and "n3" not in edid.lower():
                kind = g.label.decode()
                line = f"  {kind} {r.formid:08X} {edid!r}"
                if bod2 is not None:
                    line += f" slots={slots(bod2)}"
                if rnam is not None:
                    line += f" rnam={rnam:08X}"
                if mod3:
                    line += f" mod3={mod3}"
                if modl:
                    line += f" armatures={[f'{m:08X}' for m in modl]}"
                print(line)
