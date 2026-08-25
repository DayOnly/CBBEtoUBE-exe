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

"""PAIRED zero-weight-bone A/B between two builds' output (#zeroweight-bone-desync).

WHY THE PACK-WIDE GATE IS NOT ENOUGH. `verify_zero_weight_bones.py` answers "how
many are on disk" and exits 1 on any. The pack has carried a large PRE-EXISTING
population for a long time with no reported equip CTD (most of it scale bones),
so an absolute-zero gate can only ever say FAIL, and it says it in words that
blame whichever pass shipped most recently. The question that actually decides
whether a weight pass is safe is a DELTA on a PAIRED population:

    newly emptied   weighted in BEFORE, empty in AFTER   <- the regression
    rescued         empty in BEFORE, weighted in AFTER   <- a pass cleaned up
    both empty      pre-existing, untouched              <- not this build's

`_restore_emptied_bones` (src/nif_convert.py) exists to hold `newly emptied` at
zero: a capping pass evicts the lightest bone, and on the one vertex where that
bone was the shape's last carrier the eviction empties it out of the shape. So
NEWLY EMPTIED IS THE METRIC THIS SCRIPT EXISTS FOR, and it is also the only
output-side signal that separates the build carrying the rescue from the one
that did not -- both builds run the same two passes and print the same lines.

POPULATION DISCIPLINE. Pairing is by relative path, then by shape name within
the file. Files or shapes present in only one arm are EXCLUDED AND COUNTED, and
the run exits 2 if nothing could be paired, because "0 newly emptied" over an
empty set is not a pass.

    python scripts/analysis/zero_weight_pair_ab.py --before DIR [--after DIR]
    python scripts/analysis/zero_weight_pair_ab.py --before DIR --save-before F
    python scripts/analysis/zero_weight_pair_ab.py --load-before F --after DIR

`--save-before` / `--load-before` let the control arm be measured while it is
still on disk. A predeploy backup is usually the ONLY copy of the previous
build's meshes, and a reconvert overwrites the pack it was taken from.

Read-only. `--after` defaults to the live pack via the MO2 instance.
Exit 1 if any bone was newly emptied, 2 if nothing was measured.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from pyn import pynifly                                    # noqa: E402
from src import paths                                      # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
WRITE_MIN = 1e-4


def _pack_root():
    lay = paths.discover_layout()
    if not lay.mods_root:
        return None
    root = lay.mods_root / OUT_MOD / "meshes" / "!UBE"
    return root if root.is_dir() else None


def measure(root, only=None):
    """-> ({relpath: {shape: {bone: carries_weight}}}, counters).

    `only` restricts the walk to a set of relative paths. The AFTER arm is
    normally the whole shipped pack (~3670 NIFs) while the BEFORE arm is a
    predeploy backup of ~100, and every unpaired file is read, parsed and then
    discarded at the pairing step. Passing the before-arm's key set makes the
    gate read what it will actually compare.
    """
    out = {}
    stats = collections.Counter()
    files = sorted(glob.glob(str(Path(root) / "**" / "*.nif"), recursive=True))
    for f in files:
        rel = os.path.relpath(f, str(root)).replace("\\", "/")
        if only is not None and rel not in only:
            stats["skipped_not_in_before"] += 1
            continue
        try:
            nf = pynifly.NifFile(filepath=f)
        except Exception:
            stats["load_failed"] += 1
            continue
        shapes = {}
        for s in nf.shapes:
            try:
                bones = list(s.bone_names or [])
            except Exception:
                stats["shape_unreadable"] += 1
                continue
            if not bones:
                continue
            carried = {}
            for b in bones:
                try:
                    pairs = s.bone_weights[b]
                except Exception:
                    stats["bone_unreadable"] += 1
                    continue
                carried[b] = any(w > WRITE_MIN for _, w in pairs)
            if carried:
                shapes[s.name] = carried
        if shapes:
            out[rel] = shapes
            stats["files"] += 1
            stats["shapes"] += len(shapes)
    stats["files_seen"] = len(files)
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", help="root of the BEFORE build's meshes")
    ap.add_argument("--after", help="root of the AFTER build's meshes "
                                    "(default: the live pack)")
    ap.add_argument("--save-before", help="measure --before and cache it here")
    ap.add_argument("--load-before", help="use a cached BEFORE measurement")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    # ------------------------------------------------------------- BEFORE
    if a.load_before:
        blob = json.loads(Path(a.load_before).read_text(encoding="utf-8"))
        before = blob["data"]
        bstats = collections.Counter(blob["stats"])
        print("BEFORE  loaded from " + a.load_before)
        print("        origin: " + str(blob.get("root", "?")))
    elif a.before:
        print("BEFORE  scanning " + a.before + " ...", flush=True)
        before, bstats = measure(Path(a.before))
        if a.save_before:
            Path(a.save_before).write_text(json.dumps(
                {"root": str(a.before), "data": before, "stats": dict(bstats)},
                indent=1), encoding="utf-8")
            print("        cached -> " + a.save_before)
    else:
        print("need --before or --load-before")
        return 2
    print("        {} file(s), {} shape(s)   [{} nif(s) seen, {} unreadable]"
          .format(bstats["files"], bstats["shapes"],
                  bstats["files_seen"], bstats["load_failed"]))

    if a.save_before and not a.after:
        print("")
        print("BEFORE arm cached; no --after given, so nothing is compared "
              "yet. That is the\npoint: the control is now safe from being "
              "overwritten.")
        return 0

    # ------------------------------------------------------------- AFTER
    after_root = Path(a.after) if a.after else _pack_root()
    if after_root is None:
        print("no --after and the live pack was not found "
              "(set CBBE2UBE_MO2_INI).")
        return 2
    print("AFTER   scanning " + str(after_root) + " ...", flush=True)
    after, astats = measure(after_root, only=set(before))
    print("        {} file(s), {} shape(s)   [{} nif(s) seen, {} skipped as "
          "not in BEFORE, {} unreadable]"
          .format(astats["files"], astats["shapes"], astats["files_seen"],
                  astats["skipped_not_in_before"], astats["load_failed"]))

    # ------------------------------------------------------------- pair
    newly = collections.Counter()
    rescued = collections.Counter()
    both = collections.Counter()
    where = []
    excl = collections.Counter()
    paired_files = paired_shapes = paired_bones = 0

    for rel, bshapes in sorted(before.items()):
        ashapes = after.get(rel)
        if ashapes is None:
            excl["file_missing_in_after"] += 1
            continue
        paired_files += 1
        for sname, bcar in sorted(bshapes.items()):
            acar = ashapes.get(sname)
            if acar is None:
                excl["shape_missing_in_after"] += 1
                continue
            paired_shapes += 1
            hits = []
            for bone, had in sorted(bcar.items()):
                if bone not in acar:
                    excl["bone_missing_in_after"] += 1
                    continue
                paired_bones += 1
                has = acar[bone]
                if had and not has:
                    newly[bone] += 1
                    hits.append(bone)
                elif has and not had:
                    rescued[bone] += 1
                elif not had and not has:
                    both[bone] += 1
            if hits:
                where.append((len(hits), sname, rel, hits))
            excl["bone_only_in_after"] += len(set(acar) - set(bcar))

    print("")
    print("=" * 72)
    print("PAIRED POPULATION")
    print("=" * 72)
    print("   files paired            : {}".format(paired_files))
    print("   shapes paired           : {}".format(paired_shapes))
    print("   (shape, bone) paired    : {}".format(paired_bones))
    if excl:
        print("   EXCLUDED (counted, never silently dropped):")
        for k, v in sorted(excl.items()):
            print("      {:6d}  {}".format(v, k))
    if not paired_bones:
        print("")
        print("NOTHING WAS PAIRED -- this is not a pass, it is an empty set.")
        return 2

    n_new = sum(newly.values())
    n_res = sum(rescued.values())
    n_both = sum(both.values())

    print("")
    print("=" * 72)
    print("VERDICT METRIC")
    print("=" * 72)
    print("   NEWLY EMPTIED  (weighted -> empty) : {:6d}   <-- must be 0"
          .format(n_new))
    print("   rescued        (empty -> weighted) : {:6d}".format(n_res))
    print("   both empty     (pre-existing)      : {:6d}".format(n_both))
    print("   net zero-weight change             : {:+d}".format(n_new - n_res))

    if newly:
        print("")
        print("   newly emptied, by bone (the pattern names the pass):")
        for b, k in newly.most_common(a.top):
            print("      {:5d}  {}".format(k, b))
        where.sort(reverse=True)
        print("")
        print("   worst shapes:")
        for k, sname, rel, hits in where[:15]:
            print("      {:3d}  {:<22} {}".format(k, sname, rel))
            print("           " + ", ".join(hits[:6])
                  + (" ..." if len(hits) > 6 else ""))
    if rescued:
        print("")
        print("   rescued, by bone:")
        for b, k in rescued.most_common(a.top):
            print("      {:5d}  {}".format(k, b))

    print("")
    if n_new:
        # THIS MESSAGE USED TO NAME ONE CAUSE ("check `_restore_emptied_bones`
        # is in the build") and that was a wrong diagnosis on its first real
        # result: the 2026-08-25 pack produced 8, and the rescue WAS in the
        # build. A four-arm A/B found `#smp-boundary-weight-hold` necessary for
        # all 8 and the jiggle sync innocent. Naming one cause teaches the next
        # reader to stop looking, so this asks for the attribution instead.
        print("FAIL: a bone lost its LAST carrier. It drops out of the "
              "regenerated")
        print("      skin-partition palette -- an equip CTD.")
        print("")
        print("      ATTRIBUTE BEFORE CONCLUDING. Re-run --after against arms "
              "that")
        print("      disable one pass each (CBBE2UBE_NO_SMP_BOUNDARY_HOLD=1, "
              "CBBE2UBE_NO_")
        print("      WEIGHT_PARTNER_JIGGLE_SYNC=1). A pass being NECESSARY for "
              "the count")
        print("      is not proof it does the emptying -- it may only change "
              "rows so a")
        print("      later capping pass evicts the bone.")
        print("")
        print("      THEN SPLIT BY THE AUTHOR. A bone the author weights is a "
              "rescue gap;")
        print("      one it never weighted is graft-minted, is declined on "
              "purpose, and")
        print("      needs bone REMOVAL -- which pynifly does not expose.")
        return 1
    print("PASS: no bone lost its last carrier on the paired population.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
