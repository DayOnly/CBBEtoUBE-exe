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

"""Peek inside the new UBE-related archives to see what they ship.

For each archive, identify:
  - ESP/ESM files (the plugin half)
  - .nif files (built meshes vs sliderset source data under CalienteTools/)
  - Top-level folder structure
"""
import os, sys, zipfile, py7zr, subprocess
from pathlib import Path

DL = Path(os.path.expanduser("~") + r'\Downloads')
WINRAR_RAR = r'C:\Program Files\WinRAR\Rar.exe'

ARCHIVES = [
    'Eve\'s Sunfire Armor Main File-140366-1-0-1738428193.zip',
    'Eve\'s Sunfire Armor UBE Bodyslide Files-140366-1-1-1738770593.rar',
    'Kozakowy Female Vampire Armor Replacer - ESL-95284-1-0-beta-1688777576.zip',
    'Female Vampire Armor Replacer 3BA-143980-1-3-1779068914.7z',
    'Female Vampire Armor Replacer UBE-143980-1-0opt-1779069100.7z',
]


def list_zip(path):
    out = []
    with zipfile.ZipFile(path, 'r') as z:
        for info in z.infolist():
            if info.is_dir(): continue
            out.append((info.filename, info.file_size))
    return out


def list_7z(path):
    out = []
    with py7zr.SevenZipFile(path, 'r') as z:
        for e in z.list():
            if not e.is_directory:
                out.append((e.filename, e.uncompressed))
    return out


def list_rar(path):
    """Use WinRAR's Rar.exe with the 'lb' (bare list) command."""
    result = subprocess.run(
        [WINRAR_RAR, 'lt', '-p-', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    out = []
    cur_name = None
    cur_size = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith('Name:'):
            cur_name = line[5:].strip()
        elif line.startswith('Size:') and cur_name:
            try:
                cur_size = int(line[5:].strip())
            except ValueError:
                cur_size = 0
            out.append((cur_name, cur_size))
            cur_name = None
    return out


def summarize(name, files):
    print(f"\n=== {name} ===  ({len(files)} files)")
    if not files:
        print("  (empty / extraction failed)")
        return

    # Find interesting categories
    esps    = [(n, s) for n, s in files if n.lower().endswith(('.esp', '.esm', '.esl'))]
    nifs    = [(n, s) for n, s in files if n.lower().endswith('.nif') and 'calientetools' not in n.lower()]
    osp     = [(n, s) for n, s in files if n.lower().endswith('.osp')]
    osd     = [(n, s) for n, s in files if n.lower().endswith('.osd')]
    bsd     = [(n, s) for n, s in files if n.lower().endswith('.bsd')]
    fomod   = [(n, s) for n, s in files if 'fomod' in n.lower()]
    textures= [(n, s) for n, s in files if n.lower().endswith(('.dds', '.bgsm', '.bgem'))]

    print(f"  ESP/ESM/ESL : {len(esps)}")
    for n, s in esps[:5]: print(f"    {n}  ({s} bytes)")
    print(f"  built NIFs (not under CalienteTools) : {len(nifs)}")
    for n, s in nifs[:8]: print(f"    {n}  ({s} bytes)")
    if len(nifs) > 8: print(f"    ... and {len(nifs)-8} more")
    print(f"  BodySlide .osp (sliderset definitions) : {len(osp)}")
    for n, s in osp[:5]: print(f"    {n}")
    print(f"  BodySlide .osd (slider data) : {len(osd)}")
    print(f"  BodySlide .bsd : {len(bsd)}")
    print(f"  fomod installer files : {len(fomod)}")
    print(f"  textures : {len(textures)}")

    # Detect top-level folder convention
    top_dirs = set()
    for n, _ in files:
        parts = n.replace('\\', '/').split('/', 1)
        if len(parts) == 2:
            top_dirs.add(parts[0])
    print(f"  top-level directories: {sorted(top_dirs)[:8]}")


for archive in ARCHIVES:
    path = DL / archive
    if not path.is_file():
        print(f"\n=== {archive} ===  MISSING")
        continue
    if archive.lower().endswith('.zip'):
        files = list_zip(path)
    elif archive.lower().endswith('.7z'):
        files = list_7z(path)
    elif archive.lower().endswith('.rar'):
        files = list_rar(path)
    else:
        continue
    summarize(archive, files)
