# Re-run ONLY the modded non-body UBE coverage pass against the live ARR load
# order, mirroring auto_convert.py's call site. With the _HAIR_ONLY_SLOTS skip
# now refined (equippable headgear that hides hair is no longer dropped), this
# regenerates UBE_ModNonBody_Coverage.esp + its SkyPatcher INI so the Sand Snake
# headdress (and any other hair-hiding headgear) gets UBE-race coverage.
# Generates to a TEMP path first for inspection; pass --commit to write live.
import os
import sys
from pathlib import Path

os.environ.setdefault("CBBE2UBE_MO2_INI", r"D:\Modlists\ARR\ModOrganizer.ini")
sys.path.insert(0, r"C:\Users\Sam\Downloads\cbbe-to-ube")

from src import paths, ube_patcher
from src.auto_convert import _discover_master_data_dirs
from src import esp
import struct

COMMIT = "--commit" in sys.argv

OUTPUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto")
LIVE_ESP = OUTPUT / "UBE_ModNonBody_Coverage.esp"
LIVE_INI = OUTPUT / "SKSE" / "Plugins" / "SkyPatcher" / "armor" / "UBE_ModNonBody_Coverage.ini"

lay = paths.discover_layout()
names = paths.active_plugins_ordered(lay)
fidx = paths.plugin_file_index(lay)
ordered = [Path(fidx[n.lower()]) for n in (names or []) if n.lower() in fidx]
print(f"load order: {len(ordered)} plugins resolved")

src_dir = Path(r"D:\Modlists\ARR\mods\RB's Sand Snake Armor and Weapon")
md = _discover_master_data_dirs(src_dir)
print(f"master data dirs: {len(md)} (first: {md[0] if md else None})")

target_esp = LIVE_ESP if COMMIT else Path(r"C:\Users\Sam\Downloads\cbbe-to-ube\_tmp_mnb") / "UBE_ModNonBody_Coverage.esp"
target_esp.parent.mkdir(parents=True, exist_ok=True)

mnb = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
    target_esp, ordered,
    exclude_names={LIVE_ESP.name.lower(), "cbbe_to_ube_combined.esp",
                   "vanilla_ube_race_compat.esp"},
    master_data_dirs=md)

ini_lines = mnb.get("ini_lines") or []
print(f"\nminted ARMAs: {mnb.get('minted_armas')} | items covered: {mnb.get('armo_targets')} "
      f"| masters: {mnb.get('masters')} | ESL: {mnb.get('esl_flagged')} "
      f"| scanned: {mnb.get('candidates_scanned')}")
vw = [w for w in mnb.get('validation_warnings', []) if 'missing-nif' not in w]
if vw:
    print(f"validator warnings: {vw[:5]}")

# Does the INI now cover the Sand Snake headdress? (ARMO 092D in the ESPFE)
hd = [l for l in ini_lines if "headdress" in l.lower()
      or "092D" in l.upper() or "92d" in l.lower()]
print("\nheaddress INI lines:")
sand = [l for l in ini_lines if "Sand Snake" in l]
for l in sand:
    print("  ", l)
if not sand:
    # fall back: search for the ESPFE filter lines
    espfe = [l for l in ini_lines if "ESPFE" in l]
    for l in espfe[:10]:
        print("  ", l)

# Compare against the existing live ESP's coverage count (regression guard).
def count_minted(p):
    if not Path(p).is_file():
        return None
    e = esp.ESP.load(p)
    g = e.group(b"ARMA")
    return len(g.records) if g else 0
print(f"\nexisting live minted ARMAs: {count_minted(LIVE_ESP)}  "
      f"new minted ARMAs: {mnb.get('minted_armas')}")

if COMMIT:
    LIVE_INI.parent.mkdir(parents=True, exist_ok=True)
    LIVE_INI.write_text("\n".join(ini_lines) + "\n", encoding="utf-8")
    print(f"\nCOMMITTED -> {LIVE_ESP.name} + {LIVE_INI.name}")
else:
    print(f"\n(dry run -> {target_esp})  add --commit to write live")
