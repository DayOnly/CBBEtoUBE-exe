# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# Free software under the GNU GPL v3+. See <https://www.gnu.org/licenses/>.

"""Rebake ONE overlay mod's body tattoos/paints into UBE UV space, even when the
mod stores them OUTSIDE the converter's standard overlay roots (e.g. a RaceMenu
tattoo pack under textures/actors/<mod>/...). Reuses the converter's correspondence
+ convert_overlay; writes loose DDS into the output mod at the original path so the
load order makes them win over the source BSA.

    CBBE2UBE_MODS_ROOT=... python scripts/fix_overlay_mod.py \
        --mod "Bitchcraft Tattoos - Racemenu" --region body \
        --output "D:/Modlists/ARR/mods/CBBEtoUBE Auto"

Assumes the named mod's overlays are all in ONE region (default body). Skips
normal/specular maps (_n/_s/_sk/_msn/_g). Stopgap for the discovery-root gap; the
durable fix is to teach discover_overlays to also follow script-registered paths.
"""
import argparse
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from src import overlay_transfer as ot          # noqa: E402
from src import paths as P                       # noqa: E402
from src.bsa_strings import BSAArchive           # noqa: E402

_NONCOLOR = ("_n.dds", "_s.dds", "_sk.dds", "_msn.dds", "_g.dds", "_em.dds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", required=True, help="source overlay mod folder name")
    ap.add_argument("--region", default="body", choices=("body", "hands", "feet"))
    ap.add_argument("--output", required=True, help="output mod dir (loose DDS land here)")
    ap.add_argument("--weight", default="_1")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    mr = P.mods_root()
    if mr is None:
        sys.exit("!! mods root not resolved (set CBBE2UBE_MODS_ROOT)")
    mod = mr / a.mod
    if not mod.is_dir():
        sys.exit(f"!! mod folder not found: {mod}")
    texconv = ot.find_texconv()
    if texconv is None:
        sys.exit("!! texconv not found (set CBBE2UBE_TEXCONV)")

    # Collect the mod's overlay color DDS (loose + every BSA), keyed by rel path.
    found = {}   # rel(lower, fwd-slash) -> ("loose", Path) | ("bsa", bsa, name)
    for f in mod.rglob("*.dds"):
        rel = f.relative_to(mod).as_posix().lower()
        if rel.startswith("textures/") and not rel.endswith(_NONCOLOR):
            found.setdefault(rel, ("loose", f))
    for bsa in mod.glob("*.bsa"):
        try:
            arc = BSAArchive(bsa, eager=False)
            names = arc.list_files("textures")
        except Exception:
            continue
        for nm in names:
            rel = nm.replace(chr(92), "/").lower()
            if rel.startswith("textures/") and rel.endswith(".dds") \
                    and not rel.endswith(_NONCOLOR):
                found.setdefault(rel, ("bsa", bsa, nm))

    print(f"{a.mod}: {len(found)} color overlay DDS, region={a.region}")
    if not found:
        sys.exit("!! no overlay DDS found under textures/ in this mod")
    if a.dry_run:
        for rel in sorted(found):
            print("   ", rel)
        return

    corr = ot.build_region_correspondence(a.region, weight=a.weight)
    if corr is None:
        sys.exit(f"!! could not build {a.region} correspondence (CBBE/UBE ref missing)")

    out_root = Path(a.output)
    work = Path(tempfile.mkdtemp(prefix="ube_ovl_mod_"))
    arc_cache = {}
    done, failed = 0, []
    for rel, src in sorted(found.items()):
        try:
            if src[0] == "loose":
                src_dds = src[1]
            else:
                arc = arc_cache.get(src[1]) or BSAArchive(src[1], eager=False)
                arc_cache[src[1]] = arc
                data = arc.read_file(src[2])
                if not data:
                    raise RuntimeError("BSA extract empty")
                src_dds = work / "src.dds"
                src_dds.write_bytes(data)
            ot.convert_overlay(src_dds, out_root / rel.replace("/", chr(92)),
                               corr, texconv, work)
            done += 1
            print(f"   ok  {rel}")
        except Exception as e:
            failed.append((rel, repr(e)))
            print(f"   ERR {rel}: {e}")
    print(f"\nrebaked {done}/{len(found)} -> {out_root}")
    if failed:
        print(f"failed: {len(failed)}")


if __name__ == "__main__":
    main()
