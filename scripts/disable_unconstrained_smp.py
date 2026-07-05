# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# Free software under the GNU GPL v3+. See <https://www.gnu.org/licenses/>.

"""Live-patch: disable the converter's UNCONSTRAINED HDT-SMP XMLs (a per-vertex /
per-triangle collision setup with NO <generic-constraint> = no spring forces).
Per the FSMP source + XSD, such a cloth is an unconstrained soft body: it diverges
to infinity and OOB-crashes FSMP the moment a body collider is paired with it
(confirmed in-game on multiple armors). Renaming the XML aside makes FSMP skip it
(the NIF's dangling ref is harmless) -> the armor reverts to kinematic + the
converter's baked geometric clearance.

KEEPS XMLs that DO have <generic-constraint> (real simulated chains -- capes,
skirts the source author rigged), which are stable.

    python scripts/disable_unconstrained_smp.py <meshes_root> [--apply] [--restore]

Dry-run by default (lists what it would do); pass --apply to rename.
"""
import argparse
from pathlib import Path

SUFFIX = ".nosmp"


def is_broken_collision_pair(text: str) -> bool:
    """The CRASH pattern: an unconstrained collision PAIR -- a per-vertex cloth
    AND a per-triangle collider together, with NO <generic-constraint> spring
    forces. The cloth diverges against the collider and OOB-crashes FSMP. A
    per-vertex-only cloth (no collider to diverge against) or any constrained
    chain is NOT this pattern and is left alone."""
    return ("<per-vertex-shape" in text and "<per-triangle-shape" in text
            and "<generic-constraint" not in text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)

    if a.restore:
        n = 0
        for p in root.rglob("*.xml" + SUFFIX):
            p.rename(p.with_suffix(""))   # strip .nosmp -> back to .xml
            n += 1
        print(f"restored {n} xml(s)")
        return

    broken, kept = [], 0
    for p in root.rglob("*.xml"):
        try:
            t = p.read_text("utf-8", "ignore")
        except Exception:
            continue
        if is_broken_collision_pair(t):
            broken.append(p)            # unconstrained collision PAIR -> crash
        else:
            kept += 1                   # constrained chain / cloth-only / non-hdt

    unconstrained = broken              # (name reused by the apply loop below)
    print(f"unconstrained collision-PAIR (disable, crash pattern)={len(broken)}  "
          f"kept(constrained / cloth-only / non-hdt)={kept}")
    for p in broken[:6]:
        print("   would disable:", p.relative_to(root))
    if not a.apply:
        print("\n(dry-run; pass --apply to rename)")
        return
    n = 0
    for p in unconstrained:
        try:
            p.rename(p.with_name(p.name + SUFFIX))
            n += 1
        except Exception as e:
            print("  ERR", p.name, e)
    print(f"\ndisabled {n} unconstrained HDT XML(s) (-> *{SUFFIX}); restore with --restore")


if __name__ == "__main__":
    main()
