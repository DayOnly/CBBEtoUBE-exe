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

    # rglob on a path that does not exist yields nothing and raises nothing, so
    # a typo used to read exactly like a pack with no crash-pattern XMLs: "=0",
    # exit 0. Fail on the typo instead of reporting a clean result about it.
    if not root.is_dir():
        print(f"no such directory: {root}")
        raise SystemExit(2)

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
    # A directory that exists but holds no XML at all is still the wrong
    # directory -- state the population so "=0" cannot be read as "clean".
    if len(broken) + kept == 0:
        print(f"examined NO xml under {root} -- nothing was classified, so "
              "this is not a verdict about that pack. Check the path.")
        raise SystemExit(3)
    print(f"unconstrained collision-PAIR (disable, crash pattern)={len(broken)}  "
          f"kept(constrained / cloth-only / non-hdt)={kept}  "
          f"examined={len(broken) + kept}")
    for p in broken[:6]:
        print("   would disable:", p.relative_to(root))
    if not a.apply:
        print("\n(dry-run; pass --apply to rename)")
        return
    n = 0
    errs = 0
    for p in unconstrained:
        try:
            p.rename(p.with_name(p.name + SUFFIX))
            n += 1
        except Exception as e:
            errs += 1
            print("  ERR", p.name, e)
    print(f"\ndisabled {n} unconstrained HDT XML(s) (-> *{SUFFIX}); restore with --restore")
    # Every rename failing (files locked by a running FSMP/MO2) used to print
    # ERR lines and still exit 0, so a partial patch read as a complete one.
    # The armors that did NOT get renamed are the ones that still OOB-crash.
    if errs:
        print(f"FAILED to rename {errs} of {len(unconstrained)} -- those armors "
              "are STILL the crash pattern. Close the game/MO2 and re-run.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
