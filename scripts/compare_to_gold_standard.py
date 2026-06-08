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

"""Structural diff between converter output, hand-built UBE armors (Druchii),
and the gold-standard UBE Vanilla pack.

Goal: identify the structural conventions that differ between us and the
reference set so we know what (if anything) is blocking our cloth from
morphing at runtime.

Comparison axes:
  A) block types + shape flags + shader properties
  B) bone weight distribution per shape (mass + scale-bone ratio)
  F) BODYTRI + TRI presence (does ANY UBE armor morph cloth at runtime?)

The output of this script is a side-by-side table the user can read
and act on, not a verdict.
"""
from __future__ import annotations
import os

import io
import sys
from pathlib import Path

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_io  # noqa: E402

GOLD = Path(os.path.expanduser("~") + r"/Downloads/cbbe-to-ube/_ref_ube_pack/extracted")
HAND = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"/mods/Bodyslide Output/Meshes")
OURS = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"/mods/CBBEtoUBE Auto/meshes")

SCALE_BONE_KEYS = ("NPC Belly", "NPC L Butt", "NPC R Butt",
                   "FrontThigh", "RearThigh", "RearCalf")


def shape_info(s):
    raw = s._backing
    block = type(raw).__name__
    flags = getattr(raw, "flags", 0) or 0
    sh = getattr(raw, "shader", None)
    sh_type = getattr(sh, "Shader_Type", None) if sh is not None else None
    sh_flags1 = getattr(sh, "Shader_Flags_1", None) if sh is not None else None
    sh_flags1_str = f"0x{sh_flags1:x}" if sh_flags1 is not None else "—"
    has_alpha = bool(getattr(raw, "has_alpha_property", False))
    # BODYTRI?
    bodytri = None
    try:
        for ed in raw.extra_data():
            if getattr(ed, "name", None) == "BODYTRI":
                bodytri = ed.string_data
                break
    except Exception:
        pass
    return {
        "block": block,
        "flags": f"0x{flags:x}",
        "shader_type": sh_type,
        "shader_f1": sh_flags1_str,
        "alpha": has_alpha,
        "bodytri": bodytri,
    }


def bone_weight_stats(s):
    """Total weight + scale-bone fraction per shape."""
    total_w = 0.0
    scale_w = 0.0
    bone_count = 0
    top_bones: list[tuple[str, float]] = []
    for bn, pairs in (s.bone_weights or {}).items():
        if pairs is None or len(pairs) == 0:
            continue
        bone_count += 1
        w = float(np.asarray(pairs)[:, 1].sum())
        total_w += w
        if any(k in bn for k in SCALE_BONE_KEYS):
            scale_w += w
        top_bones.append((bn, w))
    top_bones.sort(key=lambda x: -x[1])
    scale_pct = (100 * scale_w / total_w) if total_w > 0 else 0.0
    return {
        "total_w": total_w,
        "scale_w": scale_w,
        "scale_pct": scale_pct,
        "bone_count": bone_count,
        "top_bones": top_bones[:5],
    }


def root_extras(nif):
    extras = []
    try:
        for ed in nif._backing.rootNode.extra_data():
            n = getattr(ed, "name", "?")
            sd = getattr(ed, "string_data", "?")
            extras.append((n, sd))
    except Exception:
        pass
    return extras


def report_nif(label: str, path: Path):
    if not path.is_file():
        print(f"\n=== {label} ===")
        print(f"  NIF MISSING: {path}")
        return
    nif = nif_io.load_nif(path)
    print(f"\n=== {label} ===")
    print(f"  path: {path}")
    print(f"  shapes: {len(nif.shapes)}")
    re = root_extras(nif)
    if re:
        print(f"  root extras: {re}")

    # Header
    print()
    print(f"  {'shape':<25} {'block':<22} {'flags':>10} {'sh.t':>5} {'sh.f1':>10} {'alpha':>6} {'bodytri'}")
    for s in nif.shapes:
        info = shape_info(s)
        bt = info["bodytri"]
        bt_str = f"-> {bt!r}" if bt else ""
        print(f"  {s.name[:24]:<25} {info['block']:<22} {info['flags']:>10} {str(info['shader_type']):>5} {info['shader_f1']:>10} {str(info['alpha']):>6} {bt_str}")

    # Bone weights
    print()
    print(f"  {'shape':<25} {'#verts':>7} {'#bones':>7} {'totalW':>9} {'scaleW':>9} {'scale%':>6}  top-3 bones")
    for s in nif.shapes:
        if len(s.verts) == 0:
            continue
        bw = bone_weight_stats(s)
        top3 = ", ".join(f"{bn}({w:.0f})" for bn, w in bw["top_bones"][:3])
        print(f"  {s.name[:24]:<25} {len(s.verts):>7} {bw['bone_count']:>7} {bw['total_w']:>9.1f} {bw['scale_w']:>9.1f} {bw['scale_pct']:>5.1f}%  {top3}")


def find_companion_tri(nif_path: Path) -> Path | None:
    """Look for a TRI next to the NIF. Naive: same stem + .tri, or
    strip _0/_1 suffix and try."""
    candidates = [nif_path.with_suffix(".tri")]
    stem = nif_path.stem
    if stem.endswith("_0") or stem.endswith("_1"):
        candidates.append(nif_path.parent / (stem[:-2] + ".tri"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def report_tri(label: str, tri_path: Path):
    if not tri_path or not tri_path.is_file():
        print(f"\n--- {label} TRI: NOT FOUND ---")
        return
    from src.tri import TriFile
    tri = TriFile.load(tri_path)
    print(f"\n--- {label} TRI ({tri_path.name}) ---")
    print(f"  version={tri.version}  shapes={len(tri.shapes)}")
    print(f"  {'shape':<26} {'morphs':>7}  sample slider names")
    for s in tri.shapes:
        m = s.morphs or []
        sample_names = [mm.name for mm in m if mm.offsets][:3]
        print(f"  {s.name[:25]:<26} {len(m):>7}  {sample_names}")


def main():
    print("=" * 70)
    print("STRUCTURAL DIFF: gold-standard UBE pack vs hand-built Druchii vs our batch 7")
    print("=" * 70)

    # ------------------------------------------------------------
    # F: Hand-built Druchii cloth — TRI structure + flags + BODYTRI
    # If Druchii cloth has the SAME structure as ours and we BOTH
    # don't morph, that explains why our cloth doesn't morph either.
    # ------------------------------------------------------------
    print("\n\n" + "#" * 70)
    print("# F. Does hand-built Druchii morph cloth at runtime?")
    print("#" * 70)
    druchii_top = HAND / "Obicnii/DruchiiArmor/Druchii Top_1.nif"
    druchii_waist = HAND / "Obicnii/DruchiiArmor/Druchii Waist_1.nif"
    report_nif("HAND  Druchii Top_1   (slot 32 cuirass)", druchii_top)
    report_tri("Druchii Top", find_companion_tri(druchii_top))
    report_nif("HAND  Druchii Waist_1 (slot 49 corset)", druchii_waist)
    report_tri("Druchii Waist", find_companion_tri(druchii_waist))

    # ------------------------------------------------------------
    # Compare our Daedric to: (1) gold-standard 1st-person, (2) Druchii
    # ------------------------------------------------------------
    print("\n\n" + "#" * 70)
    print("# A + B. Daedric: ours (3rd-person) vs gold-standard (1st-person available)")
    print("#" * 70)
    our_daedric = OURS / "!UBE/armor/daedric/daedrictorsof_1.nif"
    gold_daedric_1p = GOLD / "meshes/!UBE/armor/daedric/1stpersondaedrictorsof_1.nif"
    report_nif("OUR   daedrictorsof_1 (batch 7, 3rd-person)", our_daedric)
    report_tri("our Daedric", find_companion_tri(our_daedric))
    report_nif("GOLD  1stpersondaedrictorsof_1 (vanilla pack, 1st-person)",
               gold_daedric_1p)
    report_tri("gold Daedric 1p", find_companion_tri(gold_daedric_1p))

    # ------------------------------------------------------------
    # Wolf gauntlets — gold-standard has 3rd-person for these
    # (slot 33 hands armor)
    # ------------------------------------------------------------
    print("\n\n" + "#" * 70)
    print("# A + B. Wolf gauntlets: ours vs gold-standard 3rd-person")
    print("#" * 70)
    # Find our wolf gauntlets equivalent
    candidates = list(OURS.rglob("wolf*glove*_1.nif")) + list(OURS.rglob("wolf*gauntlet*_1.nif"))
    if candidates:
        report_nif("OUR   wolf gauntlets", candidates[0])
        report_tri("our wolf gauntlets", find_companion_tri(candidates[0]))
    else:
        # Look at any gauntlet from Remodeled
        cands = list(OURS.rglob("**/iron*gauntlets*_1.nif"))[:1]
        if cands:
            report_nif("OUR   iron gauntlets (substitute)", cands[0])
            report_tri("our iron gauntlets", find_companion_tri(cands[0]))
    report_nif("GOLD  wolf gloves m_1 (vanilla pack, 3rd-person)",
               GOLD / "meshes/!UBE/armor/wolf/glovesm_1.nif")
    report_tri("gold wolf gloves", find_companion_tri(GOLD / "meshes/!UBE/armor/wolf/glovesm_1.nif"))

    # ------------------------------------------------------------
    # Barkeeper torso — gold-standard 3rd-person with BarKeeperBody
    # (slot 32 cuirass with a baked body shape)
    # ------------------------------------------------------------
    print("\n\n" + "#" * 70)
    print("# A + B. Barkeeper torso: gold-standard reference for slot-32 baked-body")
    print("#" * 70)
    gold_barkeep = GOLD / "meshes/!UBE/clothes/barkeeper/f/torso_1.nif"
    report_nif("GOLD  barkeeper torso_1 (slot 32 cuirass)", gold_barkeep)
    report_tri("gold barkeeper", find_companion_tri(gold_barkeep))


if __name__ == "__main__":
    main()
