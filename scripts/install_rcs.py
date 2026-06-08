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

"""Install RaceCompatibility SKSE + UBE compatibility mods into MO2.

Idempotent: re-running overwrites mod folders and re-adds entries cleanly.
Requires MO2 closed (script does NOT verify; caller verifies).
"""
import os, sys, shutil, zipfile, py7zr
from datetime import datetime

DL = os.path.expanduser("~") + r'\Downloads'
MODS = os.environ.get("CBBE2UBE_MODS_ROOT", "") + r'\mods'
PROFILE = os.environ.get("CBBE2UBE_MODS_ROOT", "") + r'\profiles\Main Profile'
MODLIST = os.path.join(PROFILE, 'modlist.txt')
PLUGINS = os.path.join(PROFILE, 'plugins.txt')

# 1. BACKUP
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copy(MODLIST, MODLIST + f'.bak_{ts}')
shutil.copy(PLUGINS, PLUGINS + f'.bak_{ts}')
print(f'[backup] modlist.txt + plugins.txt -> .bak_{ts}')

def fresh(p):
    if os.path.exists(p): shutil.rmtree(p)
    os.makedirs(p, exist_ok=True)
    return p

def extract_7z(src, dst, strip_top_dir=None):
    """Extract a .7z. If strip_top_dir is set, the named top-level folder is
    flattened away (its children become the dst's children).
    """
    if strip_top_dir is None:
        with py7zr.SevenZipFile(src, 'r') as z:
            z.extractall(path=dst)
        return

    # Extract to a temp area, then move strip_top_dir's contents into dst
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with py7zr.SevenZipFile(src, 'r') as z:
            z.extractall(path=tmp)
        inner = os.path.join(tmp, strip_top_dir)
        if not os.path.isdir(inner):
            raise RuntimeError(f"expected top-level folder '{strip_top_dir}' inside {src}")
        for name in os.listdir(inner):
            shutil.move(os.path.join(inner, name), os.path.join(dst, name))

# 2a. Race Compatibility SKSE (RCS) - AE DLL + scripts
rcs_mod = fresh(os.path.join(MODS, 'Race Compatibility SKSE (RCS)'))
with zipfile.ZipFile(os.path.join(DL, 'Race Compatibility SKSE-122592-2-4-0-1760156437.zip'), 'r') as z:
    for info in z.infolist():
        if info.is_dir(): continue
        name = info.filename
        if name.startswith('scripts/'):
            out = os.path.join(rcs_mod, name.replace('/', os.sep))
        elif name.startswith('ae/'):
            tail = name[len('ae/'):]
            out = os.path.join(rcs_mod, 'SKSE', 'Plugins', tail.replace('/', os.sep))
        else:
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(info) as src_f, open(out, 'wb') as out_f:
            shutil.copyfileobj(src_f, out_f)
print(f'[install] Race Compatibility SKSE (RCS) -> {rcs_mod}')

# 2b. RaceCompatibility Light
rcl_mod = fresh(os.path.join(MODS, 'RaceCompatibility Light'))
extract_7z(os.path.join(DL, 'RaceCompatibilityLight.7z'), rcl_mod, strip_top_dir='RaceCompatibilityLight')
print(f'[install] RaceCompatibility Light -> {rcl_mod}')

# 2c. UBE_AllRace replacement
ube_repl_mod = fresh(os.path.join(MODS, 'UBE_AllRace Newrite Replacement'))
extract_7z(os.path.join(DL, 'UBE_AllRace.7z'), ube_repl_mod)
print(f'[install] UBE_AllRace Newrite Replacement -> {ube_repl_mod}')

# 2d. Sacro Sky Patch
sacro_mod = fresh(os.path.join(MODS, 'UBE RCSKSE Sacro Sky Patch'))
extract_7z(os.path.join(DL, 'UBE RCSKSE Sacro Sky Patch - Head Parts FLM-165523-1-0-1-1769235385.7z'), sacro_mod)
print(f'[install] UBE RCSKSE Sacro Sky Patch -> {sacro_mod}')

# 3. CREATE meta.ini FOR EACH
def meta_ini(folder, comment):
    with open(os.path.join(folder, 'meta.ini'), 'w', encoding='utf-8') as f:
        f.write('[General]\ngameName=Skyrim Special Edition\nmodid=0\nversion=\ncategory=0\n')
        f.write(f'comments={comment}\n')
        f.write('[installedFiles]\nsize=0\n')

meta_ini(rcs_mod, 'Race Compatibility SKSE by shuc1, Nexus 122592, AE DLL')
meta_ini(rcl_mod, 'RaceCompatibilityLight from Newrite Google Drive')
meta_ini(ube_repl_mod, 'UBE_AllRace.esp replacement (Newrite) - dialogue records removed for RCC')
meta_ini(sacro_mod, 'UBE RCSKSE Sacro Sky Patch - Head Parts FLM (Nexus 165523)')

# 4. UPDATE modlist.txt
with open(MODLIST, 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

# Disable old RaceCompatibility AIO
out = []
disabled_count = 0
for line in lines:
    if line.startswith('+RaceCompatibilty All-In-One Scripted Installer'):
        out.append('-RaceCompatibilty All-In-One Scripted Installer')
        disabled_count += 1
    else:
        out.append(line)
print(f'[modlist] disabled {disabled_count} RaceCompatibilty AIO entry(ies)')

# Remove any pre-existing entries with the same mod names (for idempotency)
new_mods = [
    '+UBE RCSKSE Sacro Sky Patch',
    '+UBE_AllRace Newrite Replacement',
    '+RaceCompatibility Light',
    '+Race Compatibility SKSE (RCS)',
]
names_to_remove = {m.lstrip('+-').strip() for m in new_mods}
out = [l for l in out if l.lstrip('+-').strip() not in names_to_remove]

# Insert after the leading '# ...' comment lines
insert_at = 0
for i, l in enumerate(out):
    if l.strip().startswith('#'):
        insert_at = i + 1
    else:
        break

for nm in new_mods:
    out.insert(insert_at, nm)
print(f'[modlist] added at top:')
for nm in new_mods:
    print(f'  {nm}')

with open(MODLIST, 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')

# 5. UPDATE plugins.txt
with open(PLUGINS, 'r', encoding='utf-8') as f:
    plines = f.read().splitlines()

def is_entry(line, name):
    return line.lstrip('*').strip().lower() == name.lower()

need_enabled = [
    'RaceCompatibility.esm',
    'UBE_AllRace.esp',
]

for plug in need_enabled:
    found_at = -1
    for i, line in enumerate(plines):
        if is_entry(line, plug):
            found_at = i
            break
    if found_at >= 0:
        if not plines[found_at].startswith('*'):
            plines[found_at] = '*' + plug
            print(f'[plugins] enabled existing entry: {plug}')
        else:
            print(f'[plugins] already enabled: {plug}')
    else:
        if plug.lower().endswith('.esm'):
            insert_pi = 0
            for i, l in enumerate(plines):
                if l.strip().startswith('#'): insert_pi = i + 1
                else: break
            plines.insert(insert_pi, '*' + plug)
        else:
            plines.append('*' + plug)
        print(f'[plugins] added: {plug}')

with open(PLUGINS, 'w', encoding='utf-8') as f:
    f.write('\n'.join(plines) + '\n')

# 6. INVENTORY OUTPUT
print()
print('=== Done. Inventory of new mod folders: ===')
for d in [rcs_mod, rcl_mod, ube_repl_mod, sacro_mod]:
    print(f'\n{d}:')
    for root, dirs, files in os.walk(d):
        rel = os.path.relpath(root, d)
        for f in files:
            full = os.path.join(root, f)
            sz = os.path.getsize(full)
            display = os.path.join(rel, f) if rel != '.' else f
            print(f'  {display}  ({sz} bytes)')
