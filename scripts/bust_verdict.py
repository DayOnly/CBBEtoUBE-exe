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

"""Decide WHY a piece still shows breast-through-armour in game, when its
bust FOLLOW is already at the in-game-validated level.

Follow is not health. A piece measured at 0.78 follow with a body-matched
per-bone distribution was still reported "breasts fully outside" -- so once
follow is good, three candidates remain, and they need different fixes:

  CUT      the body comes through the garment AT REST -> static containment
           is bad at bind. Offline-fixable (clearance / containment).
  MORPH    contained at rest, but the body inflates past the garment under
           its own breast morph, because the garment's TRI does not carry the
           matching inflation. Offline-fixable (TRI / morph-follow).
  MOTION   contained at rest AND under morph -> what remains is SMP travel,
           which NOTHING here can measure (the pose harness poses a skeleton,
           it does not simulate). In-game A/B only.

    python scripts/bust_verdict.py <piece_1.nif> [<garment>] [--anchor <good_1.nif>]

CONTROLS (every run, all ABORT -- "no problem" and "cannot see the problem"
must not print the same thing):
  * POLARITY/NEGATIVE  the body's CALF band scored against a BUST garment must
    read almost fully EXPOSED and almost nothing surrounded. A bust garment
    cannot contain a calf; if this reads "covered", the exposure test is
    inverted. This control exists because that inversion has happened twice in
    this repo -- once in a census, and once in the first draft of THIS script
    (`ray_exposure` returns True = ESCAPED/EXPOSED, not True = HIT).
  * ORIENTATION  the body's own band cast against the BODY ITSELF must read
    mostly exposed: rays leave along outward normals and escape. A low number
    means the normals point inward and every coverage figure is meaningless.
  * RESOLUTION  the jitter noise floor prints BEFORE any verdict, and a delta
    under it is reported as NOT A RESULT.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts import mesh_penetration as mp          # noqa: E402
from src.body_zones import BREAST_Z                 # noqa: E402
import src.nif_convert as nc                        # noqa: E402

# Import the band, never redefine it: a hardcoded z-band is how the analysis
# drifted off the anatomy before (upper chest z102-112 is NOT the breast).
BUST = BREAST_Z              # (90.0, 102.0), apex ~95.5
CALF = (10.0, 30.0)          # negative-control band a bust garment cannot cover
SURROUND_MIN = 5             # >=5/10 cone rays blocked = poke-through
CTRL_CALF_EXPOSED_MIN = 0.90
CTRL_CALF_SURROUND_MAX = 0.05
CTRL_SELF_EXPOSED_MIN = 0.80


def _shape(nf, name=None, exclude=()):
    if name:
        return next((s for s in nf.shapes if s.name == name), None)
    cands = [s for s in nf.shapes
             if s.name not in exclude and not nc._is_inline_body_name(s.name)]
    return max(cands, key=lambda s: len(s.verts)) if cands else None


def _world(s):
    g2s = nc._shape_global_to_skin(s)
    return nc._verts_skin_to_world(np.asarray(s.verts, np.float64), g2s)


def _normals(s, V):
    """Outward per-vertex normals for `V`. Stored normals are valid only for
    the STORED positions, so a morphed V recomputes (converter's own helper,
    sign-referenced to the stored normals so boundary verts keep orientation)."""
    try:
        n = np.asarray(s.normals, np.float64)
    except Exception:
        n = None
    if (n is not None and n.shape == V.shape
            and np.allclose(np.asarray(s.verts, np.float64), V, atol=1e-6)):
        return n / np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-9, None)
    try:
        rn = np.asarray(nc._recompute_vertex_normals(V, s.tris,
                                                     source_normals=n),
                        np.float64)
        return rn / np.clip(np.linalg.norm(rn, axis=1, keepdims=True), 1e-9, None)
    except Exception:
        return None


def _band(V, lo, hi):
    z = V[:, 2]
    return np.flatnonzero((z >= lo) & (z <= hi))


def _sample(idx, cap):
    """Deterministic stride subsample. Ray casting is O(rays x triangles) and
    the full bust band against a cuirass is ~19 full casts once containment
    and the noise floor are included -- minutes per piece. A strided sample
    is unbiased for a FRACTION (which is what every threshold here uses) and
    the sampled size is always printed: report the population the metric
    actually saw, never the one it was pointed at."""
    if cap <= 0 or len(idx) <= cap:
        return idx
    step = int(np.ceil(len(idx) / cap))
    return idx[::step]


def _expose(bV, bN, idx, gV, gT):
    """(n_checked, n_exposed, n_surrounded) for body verts `idx` vs a garment.

    `mp.ray_exposure` returns True = EXPOSED (the ray escaped without crossing
    the garment). Do NOT invert it -- that is the documented metric-inversion
    bug, and it bit this script's first draft."""
    if not len(idx):
        return 0, 0, 0
    exposed_mask = mp.ray_exposure(bV[idx], bN[idx], gV, gT)
    exposed = np.flatnonzero(exposed_mask)
    if not len(exposed):
        return len(idx), 0, 0
    pts, nrm = bV[idx][exposed], bN[idx][exposed]
    blocked = mp.containment(pts, nrm, gV, gT)
    return len(idx), int(len(exposed)), int((blocked >= SURROUND_MIN).sum())


def _dense_morph(morph, n_verts):
    """TriMorph.offsets is SPARSE ((vi, dx, dy, dz)); densify to (n,3)."""
    d = np.zeros((n_verts, 3), np.float64)
    for row in (morph.offsets or ()):
        vi = int(row[0])
        if 0 <= vi < n_verts:
            d[vi] = (float(row[1]), float(row[2]), float(row[3]))
    return d


def _band_morph(tri_path, shape_name, n_verts, band_idx):
    """The TRI morph with the largest motion IN THE BAND for `shape_name`.

    Selected by in-band magnitude on purpose: a largest-overall or
    alphabetical pick lands on a morph that does not touch the breast and
    then 'proves' the garment follows -- that exact mistake was made once
    (an AbsAsymmetry pick showing +0.0)."""
    try:
        from src.tri import TriFile
        tri = TriFile.load(tri_path)
    except Exception:
        return None, None
    sh = next((s for s in tri.shapes if s.name == shape_name), None)
    if sh is None:
        return None, None
    inband = set(int(i) for i in band_idx)
    best = (0.0, None, None)
    for m in (sh.morphs or ()):
        mag = 0.0
        for row in (m.offsets or ()):
            if int(row[0]) in inband:
                mag += abs(float(row[1])) + abs(float(row[2])) + abs(float(row[3]))
        if mag > best[0]:
            best = (mag, m, m.name)
    if best[1] is None:
        return None, None
    return _dense_morph(best[1], n_verts), best[2]


def analyse(path, garment_name=None, label="", cap=900, resolution=True):
    pyn = nc._pynifly()
    nf = pyn.NifFile(filepath=str(path))
    body = _shape(nf, "BaseShape")
    if body is None:
        print(f"ABORT: {path} has no BaseShape (no injected UBE body)")
        sys.exit(3)
    garment = _shape(nf, garment_name, exclude={"BaseShape"})
    if garment is None:
        print(f"ABORT: no garment shape found in {path}")
        sys.exit(3)

    bV = _world(body)
    bN = _normals(body, bV)
    bT = np.asarray(body.tris)
    gV = _world(garment)
    gT = np.asarray(garment.tris)
    if bN is None:
        print("ABORT: body normals unavailable -- cannot cast rays")
        sys.exit(3)

    print(f"\n=== {label or path}")
    print(f"    body={body.name!r} ({len(bV)} v)  "
          f"garment={garment.name!r} ({len(gV)} v)")

    bust_all = _band(bV, *BUST)
    bust = _sample(bust_all, cap)
    calf = _sample(_band(bV, *CALF), max(200, cap // 3))
    print(f"    bust band: {len(bust_all)} verts, measuring {len(bust)} "
          f"(stride sample; all figures below are over THAT sample)")
    fails = []

    # --- CONTROL: polarity / negative ---
    n_c, e_c, s_c = _expose(bV, bN, calf, gV, gT)
    if n_c:
        f_exp, f_sur = e_c / n_c, s_c / n_c
        print(f"    [control] calf band vs bust garment: exposed "
              f"{f_exp*100:.1f}% surrounded {f_sur*100:.1f}% "
              f"(expect ~100% / ~0%)")
        if f_exp < CTRL_CALF_EXPOSED_MIN or f_sur > CTRL_CALF_SURROUND_MAX:
            fails.append("POLARITY/NEGATIVE control: a bust garment appears to "
                         "cover the calf -- the exposure test is inverted or "
                         "the garment is not what it claims to be")
    else:
        fails.append("NEGATIVE control has no data (no calf-band verts)")

    # --- CONTROL: normal orientation ---
    n_s, e_s, _ = _expose(bV, bN, bust, bV, bT)
    if n_s:
        print(f"    [control] body vs ITSELF: exposed {100.0*e_s/n_s:.1f}% "
              f"(expect high -- outward rays escape; concavities self-hit)")
        if e_s / n_s < CTRL_SELF_EXPOSED_MIN:
            fails.append("ORIENTATION control: body rays mostly hit the body "
                         "itself -- normals likely point INWARD, every "
                         "coverage number below is meaningless")

    # --- RESOLUTION before verdict ---
    noise = 0.03 * len(bust)      # conservative fallback if not measured
    if resolution:
        try:
            res = mp.noise_floor(bV[bust], bN[bust], gV, gT,
                                 amps=(0.01, 0.02), trials=2)
            print(f"    [resolution] jitter noise floor (on the sample): {res}")
            noise = max((abs(v.get("flipped", 0)) for v in res.values()),
                        default=noise)
        except Exception as e:
            print(f"    [resolution] unavailable ({e!r}) -- using "
                  f"{noise:.0f} vert fallback")
    else:
        print(f"    [resolution] SKIPPED (--no-resolution); assuming a "
              f"{noise:.0f}-vert floor")

    n_b, exp, sur = _expose(bV, bN, bust, gV, gT)
    print(f"    AT REST   bust verts {n_b}: exposed {exp} "
          f"({100.0*exp/max(1,n_b):.1f}%), of those SURROUNDED "
          f"(body coming THROUGH the garment) {sur}")

    # --- MORPH: inflate body AND garment by their own TRI morphs ---
    stem = Path(path).stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
    tri = Path(path).parent / f"{stem}.tri"
    m_exp = m_sur = None
    if tri.is_file():
        # Morph SELECTION uses the full band (a strided sample would pick a
        # weaker morph); the measurement below still uses the sample.
        bd, bname = _band_morph(tri, body.name, len(bV), bust_all)
        gd, gname = _band_morph(tri, garment.name, len(gV),
                                _band(gV, *BUST))
        if bd is not None:
            bV2 = bV + bd
            gV2 = gV + gd if gd is not None else gV
            bN2 = _normals(body, bV2)
            _, m_exp, m_sur = _expose(bV2, bN2, bust, gV2, gT)
            print(f"    UNDER MORPH (body={bname!r}, garment={gname!r}): "
                  f"exposed {m_exp} ({100.0*m_exp/max(1,n_b):.1f}%), "
                  f"surrounded {m_sur}")
            if gd is None:
                print("      NOTE: the garment carries NO in-band morph -- it "
                      "cannot inflate with the body at all")
        else:
            print(f"    UNDER MORPH: body has no in-band morph in {tri.name}")
    else:
        print(f"    UNDER MORPH: no TRI beside the NIF ({tri.name})")

    if fails:
        print("\n!! CONTROL FAILURES -- numbers above are NOT trustworthy:")
        for f in fails:
            print("   " + f)
        sys.exit(3)
    return {"bust": n_b, "exp": exp, "sur": sur,
            "m_exp": m_exp, "m_sur": m_sur, "noise": noise}


def _frac(r, key):
    return r[key] / max(1, r["bust"]) if r.get(key) is not None else None


def verdict(r, anchor=None):
    """Classify the subject -- ANCHOR-RELATIVE, never on an absolute count.

    Calibration that forced this: the hide cuirass CONFIRMED GOOD in game
    measures 7.6% of its bust verts 'surrounded' (body inside the garment's
    coverage cone). An absolute threshold therefore calls a healthy piece
    CUT, and would have sent the next session into clearance work -- the
    exact move that produced three reverts. Chest containment reads high
    everywhere (it is 5-7x any other region even on clean armour), so the
    only thing the number can support is a COMPARISON against a piece whose
    in-game verdict is known."""
    print("\n--- verdict ---")
    sub_rest = _frac(r, "sur")
    sub_morph = _frac(r, "m_sur")
    print(f"  subject at rest: {r['sur']}/{r['bust']} surrounded "
          f"({sub_rest*100:.1f}%)"
          + (f", under morph {r['m_sur']}/{r['bust']} ({sub_morph*100:.1f}%)"
             if sub_morph is not None else ""))

    # MORPH is judged WITHIN the subject (rest vs morphed), so it needs no
    # anchor -- a garment that contains the bust at rest and not under the
    # body's own slider has a morph-follow defect regardless of calibration.
    noise_frac = r["noise"] / max(1, r["bust"])
    if sub_morph is not None and sub_morph > sub_rest + max(2 * noise_frac, 0.03):
        print(f"  MORPH: contained at rest but not under the body's own breast "
              f"morph (+{100*(sub_morph-sub_rest):.1f} pts, noise "
              f"+/-{100*noise_frac:.1f}). The garment does not inflate with "
              f"the body -- TRI / morph-follow, offline-fixable.")
        return

    if anchor is None:
        print("  NO VERDICT: an absolute containment count cannot separate CUT "
              "from MOTION -- the in-game-GOOD reference piece reads ~7-8% "
              "surrounded at the bust too. Re-run with --anchor <a piece you "
              "have confirmed GOOD in game> to calibrate.")
        return

    anc_rest = _frac(anchor, "sur")
    print(f"  anchor (in-game GOOD): {anchor['sur']}/{anchor['bust']} "
          f"({anc_rest*100:.1f}%)")
    margin = max(2 * noise_frac, 0.03)
    if sub_rest > anc_rest + margin:
        print(f"  CUT: the subject is materially worse AT REST than a piece "
              f"confirmed good in game (+{100*(sub_rest-anc_rest):.1f} pts > "
              f"{100*margin:.1f} margin). Offline-fixable -- clearance / "
              f"containment, NOT more weight.")
        return
    print(f"  MOTION (by elimination): at rest the subject is not measurably "
          f"worse than the in-game-GOOD anchor, and it does not degrade under "
          f"morph. What remains is SMP travel, which nothing in this repo can "
          f"measure -- the harness poses a skeleton, it does not simulate.")
    print("  Do NOT chase this with more weight or clearance on the strength "
          "of an offline number: that is what produced three reverts. Next "
          "step is an in-game A/B of ONE lever.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nif")
    ap.add_argument("garment", nargs="?", default=None)
    ap.add_argument("--anchor", default=None,
                    help="a piece CONFIRMED GOOD in game, for calibration")
    ap.add_argument("--anchor-garment", default=None)
    ap.add_argument("--cap", type=int, default=900,
                    help="max bust verts to ray-cast (0 = all; the full band "
                         "is minutes per piece)")
    ap.add_argument("--no-resolution", action="store_true",
                    help="skip the jitter noise floor (the expensive part)")
    args = ap.parse_args()

    a = None
    if args.anchor:
        a = analyse(args.anchor, args.anchor_garment,
                    label=f"ANCHOR (in-game GOOD) {args.anchor}",
                    cap=args.cap, resolution=not args.no_resolution)
    r = analyse(args.nif, args.garment, label=f"SUBJECT {args.nif}",
                cap=args.cap, resolution=not args.no_resolution)
    verdict(r, a)


if __name__ == "__main__":
    main()
