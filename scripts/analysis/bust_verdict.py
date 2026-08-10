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

  CUT          skin comes through the garment AT REST. Offline-fixable
               (minimum push / re-cut).
  OVERINFLATED the garment covers, but stands further off the body than a
               correctly-fitted armour does. NOT visible to a clipping test at
               all -- see below.
  MORPH        clean at rest, but the body inflates past the garment under its
               own breast morph because the garment's TRI does not carry the
               matching inflation. Offline-fixable (TRI / morph-follow).
  MOTION       clean at rest AND under morph -> what remains is SMP travel,
               which NOTHING here can measure (the pose harness poses a
               skeleton, it does not simulate). In-game A/B only.

    python scripts/analysis/bust_verdict.py <piece_1.nif> [<garment>] [--anchor <good_1.nif>]

RETIRED 2026-07-29: `mp.containment`. This script used to call a body vert
"surrounded" when >=5 of 10 cone rays were blocked. That metric, and signed
distance with it, scored the user's CONFIRMED-CLEAN armour WORSE than the one
they can see clipping (32.4% vs 23.9%) -- anti-correlated with ground truth at
every threshold, because neither separates "skin outside the garment SURFACE"
from "skin outside the garment's COVERAGE". Everything ever tuned against them
was tuning noise. It now uses the validated clip test from `standoff_audit`,
which reads 0.00% on that clean armour and 8.87% on the clipping one.

Two consequences worth knowing:
  * the old "NO VERDICT without --anchor" is gone. It existed because
    containment read ~7.6% on a piece confirmed GOOD, so no absolute threshold
    could work. The clip test reads 0.0% there, so CUT is now decidable on its
    own. --anchor is now REQUIRED only for the OVERINFLATED verdict, because
    standoff is a distribution over whichever skin the mask selects and a
    figure from a different mask is not comparable (a front-only 1.15u judged
    against a whole-band subject labels the CLEAN armour overinflated).
  * OVERINFLATED is a new verdict this script could not previously reach.
    Clipping has NO UPPER BOUND -- leather three units too far off the body
    scores perfectly -- and a probe tuned against clipping alone was reported
    in game as overinflated twice before anything measured it. STANDOFF is
    that measurement.

CONTROLS (every run, all ABORT -- "no problem" and "cannot see the problem"
must not print the same thing):
  * POLARITY/NEGATIVE  the body's CALF band scored against a BUST garment must
    read essentially zero COVERED. A bust garment cannot cover a calf; if this
    reads covered, the test is inverted. This control exists because that
    inversion has happened twice in this repo -- once in a census, and once in
    the first draft of THIS script (`ray_exposure` returns True = ESCAPED, not
    True = HIT).
  * ORIENTATION  the body's own band cast against the BODY ITSELF must read
    mostly exposed: rays leave along outward normals and escape. A low number
    means the normals point inward and every coverage figure is meaningless.
  * EQUALITY  the fast tester is checked against `mp.clipping_report` on the
    subject's own band before any number is reported.
  * RESOLUTION  the jitter noise floor prints BEFORE any verdict, and a delta
    under it is reported as NOT A RESULT.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.analysis import mesh_penetration as mp          # noqa: E402
from scripts.analysis import standoff_audit as sa            # noqa: E402
from src.body_zones import BREAST_Z                 # noqa: E402
import src.nif_convert as nc                        # noqa: E402

# Import the band, never redefine it: a hardcoded z-band is how the analysis
# drifted off the anatomy before (upper chest z102-112 is NOT the breast).
BUST = BREAST_Z              # (90.0, 102.0), apex ~95.5
CALF = (10.0, 30.0)          # negative-control band a bust garment cannot cover
CTRL_CALF_COVERED_MAX = 5.0      # % area; a bust garment cannot cover a calf
CTRL_SELF_EXPOSED_MIN = 0.80
# A clean armour reads 0.0% clipping, so this is a real threshold rather than a
# tuned one; the margin is for mesh-resolution noise only.
CLIP_BAD = 1.0               # % area of the bust band clipping = CUT
# There is NO hardcoded standoff anchor, deliberately. Standoff is a
# distribution over whichever skin the mask selects, and a number measured on a
# different mask is not comparable: judging a whole-bust-band subject against a
# front-only figure (1.15u) labelled the user's CONFIRMED-CLEAN armour
# OVERINFLATED on its own geometry. The anchor must be measured in the same run
# over the same band, so OVERINFLATED requires --anchor.


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
    the full bust band against a cuirass is several full casts once the
    noise floor is included -- minutes per piece. A strided sample
    is unbiased for a FRACTION (which is what every threshold here uses) and
    the sampled size is always printed: report the population the metric
    actually saw, never the one it was pointed at."""
    if cap <= 0 or len(idx) <= cap:
        return idx
    step = int(np.ceil(len(idx) / cap))
    return idx[::step]


def _clip(bV, bT, bN, idx, gV, gT, va):
    """Covered / CLIPPING / uncovered for body verts `idx`, area-weighted.

    Replaces the `mp.containment` cone count this script used to call
    "surrounded". That reading was anti-correlated with ground truth: it scored
    the user's confirmed-CLEAN armour worse than the one they can see clipping.
    This is the test calibrated to 0.00% / 8.87% on that pair."""
    if not len(idx):
        return {"n": 0, "clipping_pct": None, "covered_pct": None,
                "uncovered_pct": None}
    return sa.ClipTester(gV, gT).report(bV, bT, bN, idx, va, oriented=True)


def _standoff(bV, bN, idx, gV, gT):
    """How far off the body the garment sits, over covered skin only.

    The counter-metric. Clipping has no upper bound, so a garment that has been
    inflated until nothing pokes scores perfectly on it -- which is exactly the
    defect that reached the user twice before anything measured it."""
    if not len(idx):
        return {"n": 0, "median": None, "p90": None, "max": None}
    s = sa.ClipTester(gV, gT).standoff(bV, bN, idx, tmax=12.0)
    if not len(s):
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {"n": int(len(s)), "median": float(np.median(s)),
            "p90": float(np.percentile(s, 90)), "max": float(s.max())}


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
    va = sa.vert_areas(bV, bT)

    # --- CONTROL: the fast tester must equal the reference ---
    m_bust = np.zeros(len(bV), bool)
    m_bust[bust] = True
    if not sa.selftest(bV, bT, bN, gV, gT, m_bust, "subject bust band"):
        fails.append("EQUALITY control: the fast clip test disagrees with "
                     "mp.clipping_report -- it is a different metric")

    # --- CONTROL: polarity / negative ---
    c_c = _clip(bV, bT, bN, calf, gV, gT, va)
    if c_c["n"] and c_c["covered_pct"] is not None:
        print(f"    [control] calf band vs bust garment: covered "
              f"{c_c['covered_pct']:.1f}% clipping {c_c['clipping_pct']:.1f}% "
              f"(expect ~0% / ~0%)")
        if c_c["covered_pct"] > CTRL_CALF_COVERED_MAX:
            fails.append("POLARITY/NEGATIVE control: a bust garment appears to "
                         "cover the calf -- the test is inverted or the "
                         "garment is not what it claims to be")
    else:
        fails.append("NEGATIVE control has no data (no calf-band verts)")

    # --- CONTROL: normal orientation ---
    # NOTE the `else`. Without it an empty bust band skipped this control
    # silently, `100*exp/max(1,0)` printed 0.0%, and verdict() fell through to
    # "MOTION (by elimination)" -- the one verdict that says stop measuring and
    # go in-game -- on a piece where NOTHING was measured. A control that can
    # be skipped is not a control.
    n_s = len(bust)
    e_s = (int(mp.ray_exposure(bV[bust], bN[bust], bV, bT).sum())
           if n_s else 0)
    if not n_s:
        fails.append("ORIENTATION control has NO DATA: zero bust-band verts "
                     "on the body (wrong body, wrong band, or a morphed "
                     "mesh) -- nothing below was measured")
    else:
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

    rest = _clip(bV, bT, bN, bust, gV, gT, va)
    so = _standoff(bV, bN, bust, gV, gT)
    n_b = rest["n"]
    print(f"    AT REST   bust verts {n_b}: CLIPPING "
          f"{rest['clipping_pct']:.2f}% of area  (covered "
          f"{rest['covered_pct']:.1f}%, uncovered {rest['uncovered_pct']:.1f}%)")
    if so["n"]:
        print(f"    STANDOFF  over covered skin: median {so['median']:.2f}u  "
              f"p90 {so['p90']:.2f}u  max {so['max']:.2f}u  ({so['n']} verts)")
    else:
        print("    STANDOFF  no covered skin -- nothing to measure")

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
            # area weights on the MORPHED body: weighting a morphed state by
            # bind areas is a real error and it read 6.82% where the reference
            # read 7.33% on the same configuration
            m = _clip(bV2, bT, bN2, bust, gV2, gT, sa.vert_areas(bV2, bT))
            m_sur = m["clipping_pct"]
            m_exp = m["uncovered_pct"]
            print(f"    UNDER MORPH (body={bname!r}, garment={gname!r}): "
                  f"CLIPPING {m_sur:.2f}% (uncovered {m_exp:.1f}%)")
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
    return {"bust": n_b, "clip": rest["clipping_pct"],
            "covered": rest["covered_pct"], "uncovered": rest["uncovered_pct"],
            "standoff": so, "m_clip": m_sur, "m_unc": m_exp, "noise": noise}


def verdict(r, anchor=None):
    """Classify the subject on the validated clip test AND standoff.

    Both are needed and neither is sufficient. Clipping alone cannot see an
    overinflated garment -- it has no upper bound, so leather three units too
    far off the body scores 0.0%, and a probe tuned against it alone reached
    the user as 'overinflated' twice. Standoff alone cannot see a piece that is
    tight and pierced. Judged in that order: pierced is worse than baggy.

    The old anchor requirement is gone. It existed because `mp.containment`
    read ~7.6% on a piece confirmed GOOD in game, so no absolute threshold
    could work. The clip test reads 0.0% there, so CUT is decidable alone. An
    anchor measured in the same run is still preferred for standoff, because a
    hardcoded number cannot know the body it is being compared on.
    """
    print("\n--- verdict ---")
    rest, morph = r["clip"], r["m_clip"]
    print(f"  at rest: CLIPPING {rest:.2f}% of bust area"
          + (f", under morph {morph:.2f}%" if morph is not None else ""))
    so = r.get("standoff") or {}
    anc_so = (anchor or {}).get("standoff") or {}
    if so.get("n"):
        print(f"  standoff median {so['median']:.2f}u p90 {so['p90']:.2f}u"
              + (f" vs anchor {anc_so['median']:.2f}u / {anc_so['p90']:.2f}u"
                 if anc_so.get("n") else "  (no anchor: fit not judged)"))

    # CUT first: a garment that is pierced at rest is broken regardless of fit.
    # `noise` is a flipped-VERT count and clipping is an AREA percentage, so
    # this floor is an order-of-magnitude guard, not a conversion. It only
    # ever raises the threshold, never lowers it.
    noise_pts = 100.0 * r["noise"] / max(1, r["bust"])
    if rest is not None and rest > max(CLIP_BAD, noise_pts):
        print(f"  CUT: skin comes through AT REST ({rest:.2f}% > "
              f"{max(CLIP_BAD, noise_pts):.2f}% threshold). A clean armour "
              f"reads 0.0% here. Offline-fixable -- MINIMUM push to clear it, "
              f"then re-check standoff; pushing until nothing clips is what "
              f"produced the overinflated probe.")
        return

    bad = (sa.check(so, anc_so, keys=("median", "p90"))
           if so.get("n") and anc_so.get("n") else [])
    if bad:
        print(f"  OVERINFLATED: it covers, but it stands off further than a "
              f"correctly-fitted armour:")
        for b in bad:
            print(f"    {b}")
        print("  Not visible to a clipping test at any threshold. Re-solve "
              "for the minimum push that holds coverage.")
        return

    if morph is not None and morph > max(CLIP_BAD, noise_pts):
        print(f"  MORPH: clean at rest ({rest:.2f}%) but pierced under the "
              f"body's own breast morph ({morph:.2f}%). The garment does not "
              f"inflate with the body -- TRI / morph-follow, offline-fixable.")
        return

    for m in (sa.margins(so, anc_so, keys=("median", "p90"))
              if so.get("n") and anc_so.get("n") else []):
        print(f"  MARGINAL fit: {m}")
    if not anc_so.get("n"):
        print("  NOTE: no --anchor, so OVERINFLATED could not be tested. A "
              "garment inflated until nothing pokes reads 0.0% clipping, so "
              "'clean at rest' alone does not mean well fitted.")
    print(f"  MOTION (by elimination): clean at rest, fit within the reference, "
          f"and no degradation under morph. What remains is SMP travel, which "
          f"nothing in this repo can measure -- the harness poses a skeleton, "
          f"it does not simulate.")
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
