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

"""Post-reconvert check for body-motion match (#body-motion-match).

An armor vert must move exactly as the BODY SURFACE IT COVERS moves (ratio 1.0), so
its clearance is preserved and the body can neither poke through nor be ballooned
past. This measures that on the real output, and flags the one artifact the rule can
introduce.

Two checks:

 1. FOLLOW RATIO  (armor delta / body delta at the vertex it covers)
    Hugging shapes (stand-off <= _MATCH_NEAR) must read ~1.00. Below ~0.9 the body
    pokes through; above ~1.1 the armor balloons. Both were real in-game failures:
    plain IDW diluted a fitted cuirass to 0.59-0.81x, and an earlier regional-peak
    scheme overshot to 2.0-2.2x. A hugging shape reading off 1.0 means something
    downstream rewrote it (the overlay-band morph-sync did exactly this until it was
    gated -- the tell was that the one breast shape too LARGE to look like a "band"
    held 1.00 while every band candidate was rewritten).

 2. RIGID-PROP SHEAR  (new-artifact watch)
    A rigid prop (scabbard, sword, pauldron spike) that straddles the hugging and
    drape zones now gets its near-body verts moved by the body's full delta while its
    far verts barely move -- which SHEARS it. The bare IDW damped the near end, so the
    gradient was gentler. Flags props whose per-vertex delta spread is large relative
    to their size. A hit here is cosmetic (a bending sword), not a crash.

    python scripts/verify_motion_match.py

Read-only. Resolves the body via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
import os
import sys
import glob
import numpy as np
from pathlib import Path
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                                    # noqa: E402
from src import paths                                      # noqa: E402
from src.tri import TriFile                                # noqa: E402
from src.nif_convert import shape_body_offset              # noqa: E402
from src import sliderset_gen as sg                        # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
# Sliders that actually drive the chest are mostly NOT breast-named -- never filter
# morphs by name. These are just the probes with the largest body-side motion.
PROBES = ("Donaught", "Amazon", "Juicy_body", "BreastsBigger")
RIGID = sg._ATTACHMENT_KEYWORDS


def _body():
    from src.nif_convert import _find_user_preset_body
    p = _find_user_preset_body("_1")
    if p is None:
        return None, None
    sh = pynifly.NifFile(filepath=str(p)).shapes[0]
    return np.asarray(sh.verts, np.float64), cKDTree(np.asarray(sh.verts, np.float64))


def _dense(tri_shapes, name, morph, n):
    d = np.zeros((n, 3))
    sh = tri_shapes.get(name)
    for m in (sh.morphs if sh else []):
        if m.name == morph:
            for i, dx, dy, dz in m.offsets:
                if 0 <= int(i) < n:
                    d[int(i)] = (dx, dy, dz)
    return d


def main():
    lay = paths.discover_layout()
    root = (lay.mods_root / OUT_MOD / "meshes" / "!UBE") if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + reconvert first).")
        return 1
    bv, tree = _body()
    if bv is None:
        print("UBE reference body not found.")
        return 1
    files = [f for f in glob.glob(str(root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()]
    print(f"scanning {len(files)} meshes (MATCH_NEAR={sg._MATCH_NEAR}, "
          f"MATCH_FAR={sg._MATCH_FAR})...", flush=True)

    bad_ratio, shear = [], []
    for f in files:
        tri_p = Path(f).parent / (Path(f).name.replace("_1.nif", "") + ".tri")
        if not tri_p.is_file():
            continue
        try:
            nf = pynifly.NifFile(filepath=f)
            tri = {s.name: s for s in TriFile.load(str(tri_p)).shapes}
        except Exception:
            continue
        if "BaseShape" not in tri:
            continue
        rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
        for s in nf.shapes:
            if s.name == "BaseShape":
                continue
            v = np.asarray(s.verts, np.float64) + shape_body_offset(s)
            if len(v) < 30:
                continue
            d1, nn = tree.query(v, k=1)
            hug = d1 <= sg._MATCH_NEAR
            for probe in PROBES:
                bd = _dense(tri, "BaseShape", probe, len(bv))
                if np.linalg.norm(bd, axis=1).max() < 0.01:
                    continue
                cd = _dense(tri, s.name, probe, len(v))
                cm = np.linalg.norm(cd, axis=1)
                pm = np.linalg.norm(bd[nn], axis=1)
                m = hug & (pm > 0.05)
                if m.sum() >= 30:
                    r = cm[m].mean() / max(pm[m].mean(), 1e-6)
                    if r < 0.90 or r > 1.10:
                        bad_ratio.append((abs(r - 1.0), r, s.name, probe, rel))
                # rigid prop straddling both zones -> shear
                if any(k in s.name.lower() for k in RIGID) and 0.15 < hug.mean() < 0.85:
                    span = float(np.ptp(v, axis=0).max())
                    spread = float(cm.max() - cm.min())
                    if span > 1e-6 and spread / span > 0.25 and spread > 0.5:
                        shear.append((spread / span, spread, s.name, probe, rel))
                break   # one probe is enough per shape for the shear test

    bad_ratio.sort(reverse=True)
    shear.sort(reverse=True)
    print(f"\n=== 1) HUGGING SHAPES OFF RATIO 1.0 ({len(bad_ratio)}) ===")
    print("  <0.90 = body pokes through | >1.10 = armor balloons")
    print(f"  {'ratio':>5}  shape / slider / armor")
    for _, r, nm, probe, rel in bad_ratio[:30]:
        print(f"  {r:5.2f}  {nm} [{probe}] :: {rel}")
    if not bad_ratio:
        print("  none -- every hugging shape tracks the body exactly.")

    print(f"\n=== 2) RIGID PROPS AT SHEAR RISK ({len(shear)}) ===")
    print("  a prop straddling the hugging + drape zones bends as the body morphs")
    print(f"  {'spread/span':>11}  shape / armor")
    for f_, spread, nm, probe, rel in shear[:20]:
        print(f"  {f_:11.2f}  {nm} ({spread:.2f}u) :: {rel}")
    if not shear:
        print("  none -- no rigid prop shows a large morph gradient.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
