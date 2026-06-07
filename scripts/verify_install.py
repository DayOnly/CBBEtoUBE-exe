"""Verify the RCS / UBE install:
  - modlist.txt structure (entries present, no duplicates, AIO state)
  - plugins.txt structure (ESMs first, no duplicates, our entries enabled)
  - Mod folder contents (DLLs, ESPs, scripts present)
  - Master dependency chain (every ESP/ESM's masters are themselves enabled)
  - File-conflict priorities (newer mods win where needed)
"""
import os, struct, sys
from pathlib import Path

MODS    = Path(r'<MODLIST>\mods')
PROFILE = Path(r'<MODLIST>\profiles\Main Profile')
MODLIST = PROFILE / 'modlist.txt'
PLUGINS = PROFILE / 'plugins.txt'

errors   = []
warnings = []
notes    = []

def err(msg):  errors.append(msg)
def warn(msg): warnings.append(msg)
def note(msg): notes.append(msg)


# -- 1. modlist.txt structure ----------------------------------------------
ml_lines = MODLIST.read_text(encoding='utf-8').splitlines()

# Index each mod by name, recording priority position (line number)
mod_state = {}  # name -> ('+'/'-'/'*', line index)
seen = {}
for i, line in enumerate(ml_lines):
    s = line.strip()
    if not s or s.startswith('#'): continue
    if s[0] in '+-*':
        name = s[1:].strip()
        if name in seen:
            err(f"modlist.txt: duplicate entry for '{name}' (lines {seen[name]} and {i})")
        else:
            seen[name] = i
            mod_state[name] = (s[0], i)

required_mods = [
    'Race Compatibility SKSE (RCS)',
    'RaceCompatibility Light',
    'UBE_AllRace Newrite Replacement',
    'UBE RCSKSE Sacro Sky Patch',
]
for m in required_mods:
    if m not in mod_state:
        err(f"modlist.txt: missing required mod '{m}'")
    elif mod_state[m][0] != '+':
        err(f"modlist.txt: '{m}' is not enabled (state={mod_state[m][0]})")
    else:
        note(f"modlist.txt: '{m}' enabled at line {mod_state[m][1]}")

# Old RaceCompatibility AIO state
aio = 'RaceCompatibilty All-In-One Scripted Installer'
if aio in mod_state and mod_state[aio][0] == '+':
    warn(f"modlist.txt: '{aio}' still ENABLED — README says to disable it before installing RCS")
elif aio in mod_state:
    note(f"modlist.txt: '{aio}' disabled (state={mod_state[aio][0]})")
else:
    note(f"modlist.txt: '{aio}' not present")

# Our new mods should be at the top (low line numbers = high priority in MO2)
for m in required_mods:
    if m in mod_state and mod_state[m][1] > 50:
        warn(f"modlist.txt: '{m}' is at line {mod_state[m][1]} — could be too low priority to win file conflicts")


# -- 2. plugins.txt structure ----------------------------------------------
pl_lines = PLUGINS.read_text(encoding='utf-8').splitlines()
enabled = []
disabled = []
seen_p = {}
for i, line in enumerate(pl_lines):
    s = line.strip()
    if not s or s.startswith('#'): continue
    name = s.lstrip('*').lstrip('-').strip()
    if name.lower() in seen_p:
        err(f"plugins.txt: duplicate plugin entry '{name}' (lines {seen_p[name.lower()]} and {i})")
    seen_p[name.lower()] = i
    if s.startswith('*'): enabled.append((name, i))
    else:                 disabled.append((name, i))

# ESMs must come before any ESP
first_esp = None
for name, i in enabled:
    if name.lower().endswith('.esp') and first_esp is None:
        first_esp = i
    if name.lower().endswith('.esm') and first_esp is not None and i > first_esp:
        warn(f"plugins.txt: ESM '{name}' appears AFTER an ESP at line {first_esp} — load order may not respect ESM-first rule")

# Required plugins enabled
required_plugins = ['RaceCompatibility.esm', 'UBE_AllRace.esp']
enabled_names = {n.lower() for n, _ in enabled}
for p in required_plugins:
    if p.lower() not in enabled_names:
        err(f"plugins.txt: '{p}' not enabled")
    else:
        idx = next(i for n, i in enabled if n.lower() == p.lower())
        note(f"plugins.txt: '{p}' enabled at line {idx}")


# -- 3. Mod folder contents ------------------------------------------------
checks = {
    'Race Compatibility SKSE (RCS)': [
        'SKSE/Plugins/race-compatibility.dll',
        'scripts/RaceCompatibility.pex',
    ],
    'RaceCompatibility Light': [
        'RaceCompatibility.esm',
    ],
    'UBE_AllRace Newrite Replacement': [
        'UBE_AllRace.esp',
    ],
    'UBE RCSKSE Sacro Sky Patch': [
        'SKSE/Plugins/rcs/UBE_AllRace.json',
        'SKSE/Plugins/SkyPatcher/formList/UBE_AllRace.ini',
    ],
}
for mod, files in checks.items():
    base = MODS / mod
    if not base.is_dir():
        err(f"mod folder missing: {mod}")
        continue
    for f in files:
        full = base / f.replace('/', os.sep)
        if not full.is_file():
            err(f"mod '{mod}': missing file '{f}'")
        else:
            note(f"mod '{mod}': has '{f}' ({full.stat().st_size} bytes)")


# -- 4. Master dependency chain --------------------------------------------
def parse_esp_masters(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'TES4':
        return []
    tes4_size = struct.unpack_from('<I', data, 4)[0]
    tp = data[24:24+tes4_size]
    p = 0
    masters = []
    while p < len(tp):
        sig = tp[p:p+4]
        size = struct.unpack_from('<H', tp, p+4)[0]
        p += 6
        if sig == b'MAST':
            masters.append(tp[p:p+size].rstrip(b'\x00').decode('ascii', errors='ignore'))
        p += size
    return masters

# UBE_AllRace.esp masters (the Newrite replacement should win)
ube_esp = MODS / 'UBE_AllRace Newrite Replacement' / 'UBE_AllRace.esp'
ube_masters = parse_esp_masters(ube_esp)
note(f"UBE_AllRace.esp masters: {ube_masters}")
for m in ube_masters:
    if m.lower() not in enabled_names and not any(
        m.lower() == p.lower() for p in ['skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm', 'dragonborn.esm']):
        err(f"UBE_AllRace.esp master '{m}' is NOT enabled in plugins.txt")

# Compare with the ORIGINAL UBE_AllRace.esp in UBE 2.0 mod folder
orig_ube = MODS / 'UBE 2.0 U. 0.7' / 'UBE_AllRace.esp'
if orig_ube.is_file():
    orig_masters = parse_esp_masters(orig_ube)
    note(f"Original UBE 2.0's UBE_AllRace.esp masters: {orig_masters}")
    if set(ube_masters) != set(orig_masters):
        diff_added   = set(ube_masters) - set(orig_masters)
        diff_removed = set(orig_masters) - set(ube_masters)
        if diff_added:   note(f"  Newrite ADDS masters: {diff_added}")
        if diff_removed: note(f"  Newrite REMOVES masters: {diff_removed}")


# -- 5. File-conflict priorities (UBE_AllRace.esp) --------------------------
new_pos = mod_state.get('UBE_AllRace Newrite Replacement', ('-', 1e9))[1]
old_pos = mod_state.get('UBE 2.0 U. 0.7',                  ('-', 1e9))[1]
if new_pos < old_pos:
    note(f"UBE_AllRace Newrite Replacement (line {new_pos}) wins file conflict over UBE 2.0 (line {old_pos})")
else:
    err(f"UBE_AllRace Newrite Replacement (line {new_pos}) is NOT above UBE 2.0 (line {old_pos}) - file conflict will lose!")


# -- 6. Optional: SkyPatcher framework presence -----------------------------
sp_present = any('SkyPatcher' in n for n in mod_state if mod_state[n][0] == '+' and 'Sacro' not in n)
if not sp_present:
    warn("SkyPatcher framework not detected as enabled. The SkyPatcher\\formList\\*.ini files in the Sacro Patch will not be applied. The rcs\\*.json file works without SkyPatcher.")
else:
    note("SkyPatcher framework present")


# -- Summary ----------------------------------------------------------------
def section(title, items, prefix):
    if not items: return
    print(f"\n=== {title} ===")
    for it in items: print(f"  {prefix} {it}")

section("NOTES", notes, "*")
section("WARNINGS", warnings, "!")
section("ERRORS", errors, "X")

print(f"\n--- summary: {len(errors)} error(s), {len(warnings)} warning(s), {len(notes)} note(s) ---")
sys.exit(1 if errors else 0)
