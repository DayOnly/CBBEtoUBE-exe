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

"""Augment UBE nude hands/feet .tri morph tables so they follow EVERY body
slider that moves the shared wrist/ankle seam.

Problem: the user builds nude body/hands/feet at zeroed sliders and shapes
the body with RaceMenu. The body .tri has ~196 sliders; the hands/feet
.tri have only ~35/25. When a body-only slider moves the wrist/ankle, the
hand/foot doesn't follow -> the shared seam ring tears ("UBE hand without
a wrist").

Fix: for each body slider the hand/foot lacks, transfer the body's vertex
deltas onto the hand/foot verts via shared-seam spatial correspondence
(K-NN inverse-distance weighting from the body mesh). Add only sliders
that actually move the extremity (threshold-pruned, so breast/belly/butt
sliders -> ~0 on the hand are dropped). The seam ring, being spatially
coincident with the body's seam, receives the body's exact seam delta ->
stays connected. Geometry (the .nif) is NEVER modified; only the .tri
morph table grows.

Usage:
  python scripts/augment_nude_tri.py            # dry run (report only)
  python scripts/augment_nude_tri.py --apply     # write augmented .tri
"""
from __future__ import annotations
import os
import io, sys, shutil
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import nif_io
from src import tri as tri_mod

# Set CBBE2UBE_NUDE_BUILD to your BodySlide output's `meshes\!UBE` directory
# (the folder containing Body/, Hands/, Feet/).
BUILD = Path(os.environ.get("CBBE2UBE_NUDE_BUILD", ""))
BODY_DIR = BUILD / "Body"
PARTS = {
    "HANDS": (BUILD / "Hands", "femalehands_tangent"),
    "FEET":  (BUILD / "Feet",  "femalefeet_tangent"),
}
BODY_TRI = BODY_DIR / "femalebody_tangent.tri"
BODY_NIF = BODY_DIR / "femalebody_tangent_1.nif"

OFFSET_EPS = 5e-4      # per-vert: keep offset components above this (units)
SLIDER_KEEP = 0.02     # add a transferred slider only if its max |delta| on
                       # the extremity exceeds this (units) -> prunes sliders
                       # that don't move the hand/foot at all.
R_FALL = 8.0           # transfer falloff radius (units). A hand/foot vert at
                       # distance d from its nearest BODY vert gets the body
                       # delta * clamp(1 - d/R_FALL, 0, 1). The wrist/ankle
                       # ring (d~=0) follows the body EXACTLY (seam stays
                       # connected); fingers/toes (the A-pose hand hangs near
                       # the thigh, so naive nearest-neighbour wrongly grabs
                       # THIGH verts at d~=15) are zeroed -> no spurious
                       # thigh/butt morph bleed onto the extremity.

def nif_verts(path, want_shape=None):
    nf = nif_io.load_nif(str(path))
    for s in nf.shapes:
        if want_shape is None or s.name == want_shape:
            return s.name, np.asarray(s.verts, dtype=float)
    # fallback: first shape
    s = nf.shapes[0]
    return s.name, np.asarray(s.verts, dtype=float)

def dense_from_morph(morph, n):
    d = np.zeros((n, 3), dtype=float)
    for idx, dx, dy, dz in morph.offsets:
        if 0 <= idx < n:
            d[idx] = (dx, dy, dz)
    return d

def main():
    apply = "--apply" in sys.argv[1:]

    body_tri = tri_mod.TriFile.load(BODY_TRI)
    body_shape = body_tri.shapes[0]
    bname, body_v = nif_verts(BODY_NIF, body_shape.name)
    nB = len(body_v)
    print(f"BODY: shape={body_shape.name!r} verts={nB} sliders={len(body_shape.morphs)}")
    body_morphs = {m.name: m for m in body_shape.morphs}

    for label, (pdir, stem) in PARTS.items():
        ptri_path = pdir / f"{stem}.tri"
        pnif = pdir / f"{stem}_1.nif"
        if not ptri_path.is_file() or not pnif.is_file():
            print(f"\n{label}: MISSING ({ptri_path.name} / {pnif.name}) -- skip")
            continue
        ptri = tri_mod.TriFile.load(ptri_path)
        pshape = ptri.shapes[0]
        sname, part_v = nif_verts(pnif, pshape.name)
        nP = len(part_v)
        have = {m.name for m in pshape.morphs}
        print(f"\n{label}: shape={pshape.name!r} verts={nP} "
              f"existing sliders={len(have)}")

        # correspondence: each part vert -> nearest BODY vert, with a
        # distance falloff so only verts on/near the shared seam receive
        # the body's morph (the wrist/ankle ring is coincident -> follows
        # exactly; fingers/toes far from the body are zeroed).
        tree = cKDTree(body_v)
        dist, nbr = tree.query(part_v, k=1)          # (nP,), (nP,)
        falloff = np.clip(1.0 - dist / R_FALL, 0.0, 1.0)  # (nP,)
        seam_n = int((dist < 0.5).sum())
        near_n = int((falloff > 0).sum())
        print(f"   seam-coincident verts (d<0.5): {seam_n}; "
              f"within falloff (d<{R_FALL:g}): {near_n}/{nP}")

        added = 0
        pruned = 0
        new_morphs = list(pshape.morphs)
        for name, bm in body_morphs.items():
            if name in have:
                continue                            # keep the part's own version
            bdense = dense_from_morph(bm, nB)        # (nB,3)
            pdelta = bdense[nbr] * falloff[:, None]  # (nP,3) seam-weighted
            mx = float(np.abs(pdelta).max()) if nP else 0.0
            if mx < SLIDER_KEEP:
                pruned += 1
                continue
            offs = []
            mask = (np.abs(pdelta) > OFFSET_EPS).any(axis=1)
            for i in np.nonzero(mask)[0]:
                dx, dy, dz = pdelta[i]
                offs.append((int(i), float(dx), float(dy), float(dz)))
            if not offs:
                pruned += 1
                continue
            new_morphs.append(tri_mod.TriMorph(name=name, offsets=offs))
            added += 1
        print(f"   transferred (seam-affecting) sliders added: {added}")
        print(f"   pruned (no effect on this part): {pruned}")
        print(f"   resulting slider count: {len(new_morphs)}")

        if apply:
            bak = ptri_path.with_suffix(".tri.preaug.bak")
            if not bak.exists():
                shutil.copy2(ptri_path, bak)
            pshape.morphs = new_morphs
            ptri.shapes = [pshape] + [s for s in ptri.shapes if s is not pshape]
            ptri.save(ptri_path)
            # round-trip verify
            chk = tri_mod.TriFile.load(ptri_path)
            print(f"   WROTE {ptri_path.name} (backup .preaug.bak); "
                  f"reload sliders={len(chk.shapes[0].morphs)}")
        else:
            print("   (dry run -- pass --apply to write)")

if __name__ == "__main__":
    main()
