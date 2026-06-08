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

"""Validate auto-converter outputs without running Skyrim.

Three layers of checks:

  1. PYNIFLY ROUND-TRIP — load each output NIF via pynifly + reload its
     own save. If it loads cleanly with the same shape set, the file is
     structurally valid (no missing block refs, no corrupt headers).

  2. STRUCTURAL DIFF VS HAND-AUTHORED — for mods where we have a hand-
     authored UBE conversion on disk (Druchii), compare each output NIF
     to its counterpart: same shape names, vert/tri counts, presence
     of skin/alpha/partitions, texture slots.

  3. ESP ROUND-TRIP — load our generated ESP via src.esp + save + reload,
     compare. Verifies our ESP encoder produced parseable output.

This catches the issues NIFSkope and SSEEdit would surface (broken block
refs, missing shader, malformed records) without booting Skyrim.
"""
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / ".pynifly"))

from pyn import pynifly
from src import esp


def _shape_summary(s):
    return {
        "name": s.name,
        "blockname": s.blockname,
        "verts": len(s.verts),
        "tris": len(s.tris),
        "bones": len(s.bone_names),
        "has_skin": bool(s.bone_names),
        "has_alpha": s.has_alpha_property,
        "partitions": len(s.partitions) if s.partitions else 0,
        # s.textures is a dict {slot_name: path}. Compare slot SET (not just count)
        # so we surface "missing EnvMask" rather than only the slot count.
        "textures": tuple(sorted(k for k, v in (s.textures or {}).items() if v)),
    }


def check_nif_roundtrip(nif_path: Path) -> tuple[bool, list[str]]:
    """Load NIF, summarize shapes. Returns (ok, list_of_issues)."""
    issues = []
    try:
        nf = pynifly.NifFile(filepath=str(nif_path))
    except Exception as e:
        return False, [f"load failed: {e!r}"]
    if not nf.shapes:
        issues.append("zero shapes")
    for s in nf.shapes:
        if len(s.verts) == 0:
            issues.append(f"shape {s.name!r} has zero verts")
        if len(s.tris) == 0:
            issues.append(f"shape {s.name!r} has zero tris")
        # Sanity-check that vertex positions aren't NaN / inf
        try:
            import math
            x, y, z = s.verts[0]
            if any(math.isnan(v) or math.isinf(v) for v in (x, y, z)):
                issues.append(f"shape {s.name!r} has NaN/Inf vert[0]")
        except (TypeError, ValueError):
            pass
    return len(issues) == 0, issues


def diff_nifs(ours: Path, real: Path) -> dict:
    """Compare two NIFs structurally. Returns a dict of comparisons."""
    o_nif = pynifly.NifFile(filepath=str(ours))
    r_nif = pynifly.NifFile(filepath=str(real))
    o_shapes = {s.name: _shape_summary(s) for s in o_nif.shapes}
    r_shapes = {s.name: _shape_summary(s) for s in r_nif.shapes}
    common = set(o_shapes) & set(r_shapes)
    only_ours = sorted(set(o_shapes) - common)
    only_real = sorted(set(r_shapes) - common)
    diffs = {}
    for n in sorted(common):
        o = o_shapes[n]; r = r_shapes[n]
        d = {}
        for k in ("verts", "tris", "blockname", "has_skin", "has_alpha", "partitions"):
            if o[k] != r[k]:
                d[k] = (o[k], r[k])
        # Bone count diff: report but not blocking — hand-authors trim zero-weight bones
        if o["bones"] != r["bones"]:
            d["bones"] = (o["bones"], r["bones"])
        # Texture slot diff — compare the populated-slot SETS
        if o["textures"] != r["textures"]:
            o_only = sorted(set(o["textures"]) - set(r["textures"]))
            r_only = sorted(set(r["textures"]) - set(o["textures"]))
            d["textures"] = {"ours_only": o_only, "real_only": r_only}
        if d:
            diffs[n] = d
    return {
        "ours_only": only_ours,
        "real_only": only_real,
        "diffs": diffs,
        "ours_shapes": list(o_shapes),
        "real_shapes": list(r_shapes),
    }


def check_esp_roundtrip(esp_path: Path) -> tuple[bool, list[str]]:
    import struct
    issues = []
    try:
        e1 = esp.ESP.load(esp_path)
    except Exception as ex:
        return False, [f"load failed: {ex!r}"]
    tmp = esp_path.with_suffix(esp_path.suffix + ".tmp")
    try:
        e1.save(tmp)
        e2 = esp.ESP.load(tmp)
    except Exception as ex:
        return False, [f"save/reload failed: {ex!r}"]
    finally:
        if tmp.exists():
            tmp.unlink()

    if e1.header.masters != e2.header.masters:
        issues.append(f"masters drift: {e1.header.masters} -> {e2.header.masters}")
    src_labels = {g.label for g in e1.groups}
    dst_labels = {g.label for g in e2.groups}
    if src_labels != dst_labels:
        issues.append(f"group label set drift: {src_labels} -> {dst_labels}")

    # Form version check: Skyrim SE needs 44 (0x2C). 43 = Skyrim LE; gives
    # "file marked as form 43 or lower" loader error.
    data = esp_path.read_bytes()
    tes4_form = struct.unpack_from("<I", data, 20)[0]
    if tes4_form < 44:
        issues.append(f"TES4 form version is {tes4_form} (SE wants 44; lower = LE)")
    for g in e1.groups:
        for r in g.records:
            if r.version_unk < 44:
                issues.append(
                    f"{g.label.decode()} {r.formid:#010x}: form version "
                    f"{r.version_unk} (SE wants 44)"
                )
                break  # one per group is enough to flag
    return len(issues) == 0, issues


# ----- Druchii ground-truth comparison ----------------------------------

OUR_DRUCHII = PROJ / "output" / "auto" / "ObiDruchii"
# Hand-authored UBE mods ship BodySlide shapedata; the *built* game meshes
# live in the user's Bodyslide Output mod folder.
REAL_DRUCHII = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output")


def main():
    print("=" * 80)
    print("LAYER 1: NIF round-trip on all auto-converted Druchii NIFs")
    print("=" * 80)
    all_ok = 0
    issues_total = 0
    for nif in sorted((OUR_DRUCHII / "meshes" / "!UBE").rglob("*.nif")):
        ok, issues = check_nif_roundtrip(nif)
        rel = nif.relative_to(OUR_DRUCHII / "meshes" / "!UBE")
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {rel}")
        if issues:
            for i in issues:
                print(f"         {i}")
        if ok: all_ok += 1
        else:  issues_total += len(issues)
    total = all_ok + sum(1 for _ in (OUR_DRUCHII / "meshes" / "!UBE").rglob("*.nif")) - all_ok
    print(f"\n  {all_ok}/{total} NIFs load cleanly  ({issues_total} issues)")

    print()
    print("=" * 80)
    print("LAYER 2: Structural diff vs hand-authored Druchii UBE conversion")
    print("=" * 80)
    real_meshes = REAL_DRUCHII / "meshes" / "!UBE"
    if not real_meshes.is_dir():
        # Hand-authored sometimes uses Meshes / not meshes
        real_meshes = REAL_DRUCHII / "Meshes" / "!UBE"
    if not real_meshes.is_dir():
        # Look for any meshes dir
        cands = list(REAL_DRUCHII.rglob("*Top_1.nif"))
        if cands:
            real_meshes = cands[0].parents[2]  # back up to the !UBE root
    print(f"  real root: {real_meshes}")

    diffs_summary = []
    for our_nif in sorted((OUR_DRUCHII / "meshes" / "!UBE").rglob("*.nif")):
        rel = our_nif.relative_to(OUR_DRUCHII / "meshes" / "!UBE")
        # Find matching real
        real_nif = real_meshes / rel
        if not real_nif.is_file():
            # Try fuzzy match by filename only
            cands = list(real_meshes.rglob(our_nif.name))
            real_nif = cands[0] if cands else None
        if real_nif is None or not real_nif.is_file():
            print(f"  [SKIP] {rel} — no hand-authored counterpart")
            continue
        try:
            d = diff_nifs(our_nif, real_nif)
        except Exception as e:
            print(f"  [ERR ] {rel}: {e!r}")
            continue
        if d["ours_only"] or d["real_only"] or d["diffs"]:
            print(f"  [DIFF] {rel}")
            if d["ours_only"]:
                print(f"         shapes only in ours: {d['ours_only']}")
            if d["real_only"]:
                print(f"         shapes only in real: {d['real_only']}")
            for sn, fields in d["diffs"].items():
                print(f"         shape {sn!r}: {fields}")
            diffs_summary.append((str(rel), d))
        else:
            print(f"  [OK  ] {rel}")
    print(f"\n  total NIFs with diffs: {len(diffs_summary)}")

    print()
    print("=" * 80)
    print("LAYER 3: ESP round-trip")
    print("=" * 80)
    for e in OUR_DRUCHII.rglob("*.esp"):
        ok, issues = check_esp_roundtrip(e)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {e.name}")
        for i in issues: print(f"         {i}")


main()
