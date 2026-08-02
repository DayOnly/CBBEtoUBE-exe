"""The validated fit metrics: CLIPPING (is skin coming through) and STANDOFF
(how far off the body does it sit). One module, because using either alone
ships a defect.

CLIPPING -- per body vert, ray out along its normal and in along the negative,
against the garment. out-hit = covered; out escapes AND in hits = CLIPPING
(garment BEHIND the skin); both escape = uncovered by design. Area-weighted.
Calibrated on user-supplied in-game ground truth, and any change to this file
must keep the pair exactly:

    Hide\\F\\CuirassLight_1.nif   (user: CLEAN)  -> 0.00%
    Hide\\F\\CuirassMedium_1.nif  (user: CLIPS)  -> 8.87%

Signed distance (`mp.surface_penetration`) and the ray cone (`mp.containment`)
both scored the CLEAN armour WORSE than the clipping one at every threshold --
anti-correlated with ground truth, because neither can separate "skin outside
the garment SURFACE" from "skin outside the garment's COVERAGE". Do not
reintroduce them for fit work.

STANDOFF -- the counter-metric, and the reason this module is not called
clip_test. Clipping has NO UPPER BOUND: leather three units too far off the
body scores 0.00%. Every parameter of the 2026-07-29 bust probe was tuned
against clipping alone and the result was reported in game as OVERINFLATED,
twice, because nothing in the harness could see it. Measured on the same body:

    cuirasslight (CLEAN, correctly fitted)  median 1.15u  p90 1.52u  max 2.01u
    shipped cuirassmedium                          0.34u       0.83u      1.34u   (too TIGHT, clips 8.87%)
    the overinflated probe                         2.88u       3.84u      4.70u   (clips 0.21%)

Both the too-tight and the ballooned mesh look acceptable on clipping alone.
The pair brackets the answer; either alone does not.

ORIENTATION GATE (`oriented=True`) -- the clip test is sound at bind, where it
was validated, and develops a false positive under a large morph: a downward
sag can carry skin past the cut rim of a cup, and the inward ray then strikes
the far side of the garment from the inside. Requiring the hit triangle to FACE
THE SAME WAY as the skin removes that. It does not touch the calibration (all
640 real hits on the shipped mesh face the same way), so the pair still reads
0.00% / 8.87%. NOT yet confirmed in game -- see the project notes.

`selftest()` re-runs `mp.clipping_report` and requires EQUALITY. A faster or
gated test that merely correlates with the validated one is a different test.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

try:                                    # importable as `standoff_audit` or
    import mesh_penetration as mp       # as `scripts.standoff_audit`
except ImportError:                     # pragma: no cover
    from scripts import mesh_penetration as mp


class ClipTester:
    """Ray test against one garment, reusable as that garment moves.

    Exact optimisations over `mp.clipping_report`, all verified equal:
      * the inward ray is cast ONLY where the outward ray escaped -- it cannot
        change the verdict anywhere else, and typical coverage is >90%;
      * candidate triangles are pruned by each triangle's OWN centroid radius
        (a global maximum lets a few large triangles inflate the search ball
        for every ray -- that version measured only 1.3x);
      * the triangle index is rebuilt by `set_garment()`, not per call.
    Measured 4.5x on one report, 3.7x on a six-iteration solve.
    """

    def __init__(self, gV, gT, tmax: float = 5.0):
        self.gT = np.asarray(gT, np.int64).reshape(-1, 3)
        self.tmax = float(tmax)
        self.set_garment(gV)

    def set_garment(self, gV) -> None:
        self.gV = np.asarray(gV, np.float64)
        tv = self.gV[self.gT]
        self.cent = tv.mean(axis=1)
        self.trad = np.linalg.norm(tv - self.cent[:, None, :], axis=2).max(1)
        self.ctree = cKDTree(self.cent)
        self.reach = float(self.tmax + self.trad.max())
        self._fn = None

    def face_normals(self):
        if self._fn is None:
            a = self.gV[self.gT[:, 0]]
            n = np.cross(self.gV[self.gT[:, 1]] - a, self.gV[self.gT[:, 2]] - a)
            self._fn = n / np.clip(np.linalg.norm(n, axis=1, keepdims=True),
                                   1e-12, None)
        return self._fn

    def _pairs(self, O):
        balls = self.ctree.query_ball_point(O, self.reach)
        cnt = np.fromiter((len(b) for b in balls), np.int64, len(balls))
        if not cnt.sum():
            return np.empty(0, np.int64), np.empty(0, np.int64)
        ray_i = np.repeat(np.arange(len(O), dtype=np.int64), cnt)
        tri_i = np.concatenate([np.asarray(b, np.int64)
                                for b in balls if len(b)])
        d = np.linalg.norm(self.cent[tri_i] - O[ray_i], axis=1)
        keep = d <= self.tmax + self.trad[tri_i]
        return ray_i[keep], tri_i[keep]

    def _cast(self, O, D, ray_i, tri_i, n_rays, want_tri=False):
        out = np.full(n_rays, np.inf)
        who = np.full(n_rays, -1, np.int64)
        if not len(ray_i):
            return (out, who) if want_tri else out
        V, T = self.gV, self.gT
        a = V[T[tri_i, 0]]
        e1 = V[T[tri_i, 1]] - a
        e2 = V[T[tri_i, 2]] - a
        d = D[ray_i]
        p = np.cross(d, e2)
        det = np.einsum("ij,ij->i", p, e1)
        ok = np.abs(det) > 1e-9
        inv = np.zeros_like(det)
        np.divide(1.0, det, out=inv, where=ok)
        t0 = O[ray_i] - a
        u = np.einsum("ij,ij->i", t0, p) * inv
        q = np.cross(t0, e1)
        v = np.einsum("ij,ij->i", q, d) * inv
        t = np.einsum("ij,ij->i", q, e2) * inv
        hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
               & (t > 1e-4) & (t < self.tmax))
        np.minimum.at(out, ray_i[hit], t[hit])
        if want_tri:
            sel = hit & (t <= out[ray_i] + 1e-12)
            who[ray_i[sel]] = tri_i[sel]
            return out, who
        return out

    def classify(self, bV, bN, idx, want_tri: bool = False):
        """(out_t, in_t[, in_tri]).

        `in_t` is inf wherever the outward ray HIT -- that vert is covered and
        the inward ray cannot change its verdict, so it is not cast. A caller
        wanting a distance on covered skin must use `standoff()`.
        """
        O, N = np.asarray(bV)[idx], np.asarray(bN)[idx]
        ray_i, tri_i = self._pairs(O)
        o = self._cast(O, N, ray_i, tri_i, len(O))
        i_t = np.full(len(O), np.inf)
        who = np.full(len(O), -1, np.int64)
        esc = ~np.isfinite(o)
        if esc.any() and len(ray_i):
            sel = esc[ray_i]
            eidx = np.flatnonzero(esc)
            remap = np.full(len(O), -1, np.int64)
            remap[eidx] = np.arange(len(eidx))
            res = self._cast(O[esc], -N[esc], remap[ray_i[sel]], tri_i[sel],
                             len(eidx), want_tri=want_tri)
            if want_tri:
                i_t[esc], who[esc] = res
            else:
                i_t[esc] = res
        return (o, i_t, who) if want_tri else (o, i_t)

    def report(self, bV, bT, bN, idx, vert_area, oriented: bool = False,
               body_occlusion: bool = True):
        if oriented:
            o, i_t, who = self.classify(bV, bN, idx, want_tri=True)
            fn = self.face_normals()
            same = np.zeros(len(idx), bool)
            g = who >= 0
            same[g] = np.einsum("ij,ij->i", fn[who[g]],
                                np.asarray(bN)[idx][g]) > 0.0
        else:
            o, i_t = self.classify(bV, bN, idx)
            same = np.ones(len(idx), bool)
        out_hit, in_hit = np.isfinite(o), np.isfinite(i_t)
        if body_occlusion:
            # `mp.clipping_report` gained this gate on 2026-08-01; this tester
            # did NOT, so the two silently disagreed wherever it fires --
            # measured 100.0% here against 0.0% there on a garment past the far
            # wall. `selftest` missed it because it is called on the BUST band,
            # where the body is thick and the gate rejects nothing, while the
            # regions it exists for are the thin ones (hip, inner thigh,
            # armpit). Two predicates for one concept drift apart, so this one
            # DELEGATES the body cast rather than reimplementing it.
            i_t = i_t.copy()
            far = mp.ray_first_hit(
                np.asarray(bV)[idx], -np.asarray(bN)[idx],
                bV, np.asarray(bT, np.int64).reshape(-1, 3),
                tmax=mp.BODY_TMAX, tmin=mp.BODY_EPS)
            i_t[in_hit & ~(i_t < far)] = np.inf
            in_hit = np.isfinite(i_t)
        clip = in_hit & ~out_hit & same
        A = vert_area[idx].sum()
        if A <= 0:
            return {"n": len(idx), "clipping_pct": None, "covered_pct": None,
                    "uncovered_pct": None, "clip_idx": idx[:0],
                    "out_t": o, "in_t": i_t,
                    **mp.depth_bands(np.empty(0), np.empty(0), 0.0)}
        return {"n": len(idx),
                "clipping_pct": float(100.0 * vert_area[idx][clip].sum() / A),
                "covered_pct": float(100.0 * vert_area[idx][out_hit].sum() / A),
                "uncovered_pct": float(
                    100.0 * vert_area[idx][~in_hit & ~out_hit].sum() / A),
                "clip_idx": idx[clip], "out_t": o, "in_t": i_t,
                **mp.depth_bands(i_t[clip], vert_area[idx][clip], A)}

    def standoff(self, bV, bN, idx, tmax: float = 12.0):
        """Distance to the garment along +normal, covered skin only.

        tmax is deliberately larger than the clip test's 5u: a ballooned
        garment can sit further away than the clip test ever looks, and
        truncating there would hide precisely the failure this is for.
        """
        old_t, old_r = self.tmax, self.reach
        self.tmax = float(tmax)
        self.reach = float(self.tmax + self.trad.max())
        O = np.asarray(bV)[idx]
        ray_i, tri_i = self._pairs(O)
        o = self._cast(O, np.asarray(bN)[idx], ray_i, tri_i, len(O))
        self.tmax, self.reach = old_t, old_r
        return o[np.isfinite(o)]


def output_nifs(root, weights: str = "both", exclude_first_person: bool = True):
    """Every converted NIF under `root`, BOTH weight files by default.

    WHY THIS EXISTS. Fifteen validation scripts in this repo independently
    glob `*_1.nif` only, and weight 0 is NOT a scaled copy of weight 1 -- it is
    a separately-authored mesh. Measured 2026-07-29 on one cuirass:

        bust-front clipping   weight 1: 4.52%    weight 0: 9.48%

    The WORSE half of the shipped output was invisible to every check. A second
    piece read 10.10% at weight 0 against 8.87% at weight 1. Any audit that
    reads one weight and reports on "the output" is reporting on half of it.

    `exclude_first_person` also drops `1stp*`, not just `1stperson*`: the short
    prefix let a first-person mesh (arms only, no torso) into a fit census and
    it flagged at 8.47u standoff -- a meaningless number for a piece that does
    not cover the bust at all.
    """
    import re as _re
    root = Path(root)
    pats = {"both": ("*_1.nif", "*_0.nif"), "1": ("*_1.nif",),
            "0": ("*_0.nif",)}[weights]
    excl = _re.compile(r"1stperson|1stp", _re.I)
    out = set()
    for pat in pats:
        for p in root.rglob(pat):
            if exclude_first_person and excl.search(str(p)):
                continue
            out.add(p)
    return sorted(out)


def vert_areas(bV, bT):
    """One-ring area share per body vert. Vertex density is not uniform, so a
    vert COUNT over-weights dense regions; every percentage here is by area."""
    bT = np.asarray(bT, np.int64).reshape(-1, 3)
    bV = np.asarray(bV, np.float64)
    a = bV[bT[:, 0]]
    ta = 0.5 * np.linalg.norm(np.cross(bV[bT[:, 1]] - a, bV[bT[:, 2]] - a),
                              axis=1)
    va = np.zeros(len(bV))
    for k in range(3):
        np.add.at(va, bT[:, k], ta / 3.0)
    return va


def clip_stats(bV, bT, bN, gV, gT, mask, *, oriented: bool = True) -> dict:
    return ClipTester(gV, gT).report(bV, bT, bN, np.flatnonzero(mask),
                                     vert_areas(bV, bT), oriented=oriented)


def standoff_stats(bV, bN, gV, gT, mask, tmax: float = 12.0) -> dict:
    s = ClipTester(gV, gT).standoff(bV, bN, np.flatnonzero(mask), tmax=tmax)
    if not len(s):
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {"n": int(len(s)), "median": float(np.median(s)),
            "p90": float(np.percentile(s, 90)), "max": float(s.max())}


def check(stats: dict, anchor: dict, *, slack: float = 0.15,
          rel: float = 0.25, keys=("median", "p90")) -> list:
    """Standoff failures against a clean-armour anchor. Empty = acceptable fit.

    THE ANCHOR MUST BE MEASURED OVER THE SAME MASK AS `stats`. Standoff is a
    distribution over whichever skin the mask selects, and the populations are
    not interchangeable: comparing a whole-bust-band subject against a
    front-only anchor labelled the user's CONFIRMED-CLEAN armour OVERINFLATED
    on its own numbers. Measure both in the same run, over the same band.

    `max` is NOT in the default keys. Over any mask that includes skin facing
    away from the garment, one ray travels to a distant panel and the maximum
    becomes that distance -- the clean armour reads max 10.21u over the full
    bust band and 2.01u over the front alone. Add "max" only for a tight,
    single-facing mask.

    THE TOLERANCE IS RELATIVE (`rel`) PLUS ABSOLUTE (`slack`), and the relative
    term was added AFTER a build of mine tripped the absolute-only form -- which
    is exactly the move this project has been burned by, so here is the reason
    to accept or reject on its own merits rather than on the number:

      * standoff scales with the garment, so a fixed 0.15u means something
        different on a 0.5u standoff than on a 3.0u one;
      * a percentile tail is far noisier than a median. Over a strided sample
        of ~480 covered verts, p90 sits on ~48 verts and one vert shifts it,
        while the median does not move. A single tolerance applied to both is
        not statistically coherent.

    The case that prompted it: a build with median 0.59u (HALF the clean
    armour's 1.21u -- i.e. tighter, the opposite of inflated) failed on p90
    1.86u vs 1.83u, a 0.03u margin. The overinflated probe it replaced read
    median 2.60u and p90 3.88u. A gate that cannot tell those two apart is
    measuring noise. At rel=0.25 the probe still fails on BOTH statistics and
    the clean armour still passes against itself.

    If you disagree, set rel=0.0 and the strict behaviour returns.

    `slack` is small on purpose. It is for mesh-resolution noise, not for
    making a build pass -- a threshold quietly raised to green a result is how
    the overinflated probe got deployed.
    """
    out = []
    if not stats.get("n"):
        return ["no covered skin measured -- the audit saw nothing"]
    for k in keys:
        if anchor.get(k) is None or stats.get(k) is None:
            continue
        limit = anchor[k] * (1.0 + rel) + slack
        if stats[k] > limit:
            out.append(f"standoff {k} {stats[k]:.2f}u exceeds the clean anchor "
                       f"{anchor[k]:.2f}u (limit {limit:.2f}u = "
                       f"+{rel:.0%} +{slack:.2f}u)")
    return out


def margins(stats: dict, anchor: dict, *, slack: float = 0.15,
            rel: float = 0.25, keys=("median", "p90")) -> list:
    """Statistics that pass but sit within one slack of the limit.

    Reported, never hidden: a build sitting just under a tolerance is a
    different thing from one comfortably inside it, and the distinction is the
    user's to make.
    """
    out = []
    for k in keys:
        if anchor.get(k) is None or stats.get(k) is None:
            continue
        limit = anchor[k] * (1.0 + rel) + slack
        if limit - slack < stats[k] <= limit:
            out.append(f"standoff {k} {stats[k]:.2f}u is within {slack:.2f}u "
                       f"of the {limit:.2f}u limit")
    return out


def selftest(bV, bT, bN, gV, gT, mask, label: str = "") -> bool:
    """This tester must EQUAL `mp.clipping_report`, not merely track it.

    Compares the DEPTH BANDS as well as the total. Checking only the total let
    the body-occlusion gate live in one implementation for a whole release: the
    percentages happened to agree on the band this is called with, and nothing
    looked at the split that would have shown the two answering differently.
    """
    got = ClipTester(gV, gT).report(bV, bT, bN, np.flatnonzero(mask),
                                    vert_areas(bV, bT))
    ref = mp.clipping_report(bV, bT, bN, [(gV, gT)], mask=mask)
    keys = ("clipping_pct", "clip_coincident_pct", "clip_shallow_pct",
            "clip_buried_pct")
    bad = [k for k in keys
           if (ref[k] is None) != (got[k] is None)
           or (ref[k] is not None and abs(ref[k] - got[k]) >= 1e-6)]
    print(f"  standoff_audit selftest {label}: reference "
          f"{ref['clipping_pct']:.4f}% vs {got['clipping_pct']:.4f}% -> "
          f"{'MATCH' if not bad else 'MISMATCH on ' + ', '.join(bad)}")
    return not bad
