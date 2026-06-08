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

"""Surgically remove nude/actor-skin hand/feet (and any body-skin) ARMAs
from a deployed Vanilla_UBE_Race_Compat.esp, plus any ARMO armature (MODL)
refs to them. Keeps all armor gauntlet/boot ARMAs and body coverage.

Usage: python scripts/strip_nude_handfeet.py <path-to-esp> [--apply]
Without --apply: dry run (reports what would change).
"""
from __future__ import annotations
import io, sys, struct, shutil
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import esp
from src.ube_patcher import _is_nude_skin_model

def edid(r):
    for s, d in esp.iter_subrecords(r.payload):
        if s == b"EDID":
            return d.rstrip(b"\x00").decode("ascii", "replace")
    return ""

def models(r):
    out = []
    for s, d in esp.iter_subrecords(r.payload):
        if s in (b"MOD2", b"MOD3"):
            out.append(d.rstrip(b"\x00").decode("latin-1", "ignore"))
    return out

def main():
    if len(sys.argv) < 2:
        print("need esp path"); return
    path = Path(sys.argv[1])
    apply = "--apply" in sys.argv[1:]
    e = esp.ESP.load(path)

    arma_g = next((g for g in e.groups if g.label == b"ARMA"), None)
    armo_g = next((g for g in e.groups if g.label == b"ARMO"), None)

    # 1. Identify nude/actor-skin ARMAs.
    nuke_fids = set()
    nuke_list = []
    for r in (arma_g.records if arma_g else []):
        ms = models(r)
        if any(_is_nude_skin_model(m) for m in ms):
            nuke_fids.add(r.formid)
            nuke_list.append((edid(r), f"0x{r.formid:08x}", next((m for m in ms if m), "")))
    print(f"nude/actor-skin ARMAs to remove: {len(nuke_list)}")
    for ed, fid, m in nuke_list:
        print(f"   {ed:34s} {fid}  {m}")

    # 2. Count ARMO MODL refs to those fids.
    ref_hits = 0
    if armo_g:
        for r in armo_g.records:
            for s, d in esp.iter_subrecords(r.payload):
                if s == b"MODL" and len(d) == 4 and struct.unpack("<I", d)[0] in nuke_fids:
                    ref_hits += 1
    print(f"ARMO armature (MODL) refs to removed ARMAs: {ref_hits}")

    # armor hand/feet kept (sanity)
    kept_armor = 0
    for r in (arma_g.records if arma_g else []):
        if r.formid in nuke_fids:
            continue
        ms = models(r)
        b = "".join(ms).lower()
        if ("\\f\\" in b or "gauntlet" in b or "boot" in b or "glove" in b
                or "feet" in b or "hands" in b):
            kept_armor += 1
    print(f"(armor-ish hand/feet ARMAs preserved: {kept_armor})")

    if not apply:
        print("\nDRY RUN. re-run with --apply to write.")
        return

    # backup
    bak = path.with_suffix(path.suffix + ".prenude.bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"backed up -> {bak.name}")

    # 3. remove ARMA records
    if arma_g:
        before = len(arma_g.records)
        arma_g.records = [r for r in arma_g.records if r.formid not in nuke_fids]
        print(f"ARMA records: {before} -> {len(arma_g.records)}")

    # 4. strip dangling MODL refs from ARMO payloads
    if armo_g:
        stripped = 0
        for r in armo_g.records:
            new = b""
            changed = False
            for s, d in esp.iter_subrecords(r.payload):
                if s == b"MODL" and len(d) == 4 and struct.unpack("<I", d)[0] in nuke_fids:
                    changed = True; stripped += 1
                    continue
                new += esp.encode_subrecord(s, d)
            if changed:
                r.payload = new
        print(f"stripped {stripped} dangling ARMO MODL refs")

    e.save(path)
    print(f"saved {path.name}")

if __name__ == "__main__":
    main()
