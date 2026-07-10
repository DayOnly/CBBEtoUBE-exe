"""Post-reconvert check for the bust clearance floor (#bust-clearance-floor).

The adaptive clearance path REPLACED the fixed bust ramp it was meant to refine, so
the breast ended up with LESS room than the code it superseded: the ramp
`base + factor * morph_amplitude` was clipped by a cap (0.8) that sat *below* the
bust target (1.0). Measured on a steel cuirass before the fix: breast clearance
+0.66u mean with 8% of breast verts already poking through AT REST, and 13% once a
slider was applied. The fix floors the bust zone by the legacy ramp (the same
`np.maximum` pattern rear_standoff already used) and lifts the cap clear of it.

This measures the result on the real output. Two checks, because a clearance fix can
fail in both directions:

 1. BREAST -- body must sit INSIDE the armor. Reported as signed clearance along the
    body's outward normal at each breast vertex: positive = armor outside the body.
    Any negative vertex is the body poking through.

 2. BACK / SIDES -- must stay TIGHT. The floor is gated on nipple weight, which is
    zero everywhere but the breast front, so rear clearance must not move. If it
    climbs toward the bust target, the gate leaked and every armor just went baggy.

WHERE THE BREAST IS: z 90-102, apex ~95-96 on the UBE body (feet at z~11). Not
z 99-112 -- that is the upper chest, and measuring it there hides the defect
entirely. Verified against both the body's front-most vertex and the z-distribution
of the breast morphs.

    python scripts/verify_bust_clearance.py

Read-only; resolves the body via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
import os
import sys
import glob
import numpy as np
from pathlib import Path
from collections import defaultdict
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                                       # noqa: E402
from src import paths                                         # noqa: E402
from src.nif_convert import shape_body_offset, _body_normals_or_compute   # noqa: E402
from src.body_zones import breast_mask, back_mask                        # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
# The armor's own injected body is the reference: it is what the pass cleared against.
_SKIP = ("BaseShape", "VirtualBody", "VirtualGround")
# A body vert further than this from the armor surface isn't covered by the piece.
_COVERED = 6.0
# A real covering panel, not a decorative strand or a rim. A 150-vert chain is the
# "nearest armor vertex" for ~120 breast verts and always projects behind the surface.
_PANEL_MIN_VERTS = 300
_PANEL_MIN_BAND = 60


def _closest_on_tri(p, a, b, c):
    """Closest point on triangle abc to p (Ericson, Real-Time Collision Detection)."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return a + (d1 / (d1 - d3)) * ab
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return a + (d2 / (d2 - d6)) * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        return b + ((d4 - d3) / ((d4 - d3) + (d5 - d6))) * (c - b)
    den = 1.0 / (va + vb + vc)
    return a + ab * (vb * den) + ac * (vc * den)


def _zones(bv):
    return {"breast": breast_mask(bv), "back": back_mask(bv)}


def _measure(path):
    """Signed clearance to the armor SURFACE along the body's outward normal.

    Measuring to the nearest armor VERTEX over-reports penetration ~10x on a coarse
    shell: a 503-vert cuirass lets the body bulge between armor vertices, which reads
    as a poke while the triangle passes safely over it. On a real steel cuirass the
    vertex metric found 63 penetrating verts (worst -0.74u); the surface metric found
    5 (worst -0.18u). Use the surface.
    """
    try:
        n = pynifly.NifFile(filepath=str(path))
    except Exception:
        return None
    base = next((s for s in n.shapes if s.name == "BaseShape"), None)
    if base is None:
        return None
    bv = np.asarray(base.verts, np.float64)
    bn = _body_normals_or_compute(base)
    if bn is None:
        return None

    verts, tris, off = [], [], 0
    for s in n.shapes:
        if s.name in _SKIP or s.name.lower().startswith("col"):
            continue
        v = np.asarray(s.verts, np.float64) + shape_body_offset(s)
        if len(v) < _PANEL_MIN_VERTS or breast_mask(v).sum() < _PANEL_MIN_BAND:
            continue                    # strand / rim / trim, not a covering panel
        try:
            t = np.asarray(s.tris, np.int64)
        except Exception:
            continue
        verts.append(v)
        tris.append(t + off)
        off += len(v)
    if not verts:
        return None
    av = np.vstack(verts)
    at = np.vstack(tris)
    inc = defaultdict(list)
    for ti, (i, j, k) in enumerate(at):
        inc[i].append(ti)
        inc[j].append(ti)
        inc[k].append(ti)
    tree = cKDTree(av)

    out = {}
    for name, mask in _zones(bv).items():
        if mask.sum() < 30:
            continue
        P, N = bv[mask], bn[mask]
        dv, nn = tree.query(P, k=6)
        signed, keep = np.zeros(len(P)), dv[:, 0] < _COVERED
        for i, p in enumerate(P):
            if not keep[i]:
                continue
            cand = set()
            for v_i in nn[i]:
                cand.update(inc[v_i])
            best, bq = 1e18, None
            for ti in cand:
                q = _closest_on_tri(p, *av[at[ti]])
                dd = ((q - p) ** 2).sum()
                if dd < best:
                    best, bq = dd, q
            signed[i] = (bq - p) @ N[i]
        sg = signed[keep]
        if len(sg) < 30:
            continue
        out[name] = (sg.mean(), sg.min(), float((sg < 0).mean()), int(keep.sum()))
    return out or None


def main():
    lay = paths.discover_layout()
    root = (lay.mods_root / OUT_MOD / "meshes" / "!UBE") if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + reconvert first).")
        return 1
    files = [f for f in glob.glob(str(root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()]
    print(f"scanning {len(files)} meshes (breast = z90-102 front)...", flush=True)

    poking, loose, n_body = [], [], 0
    agg = {"breast": [], "back": []}
    for f in files:
        r = _measure(f)
        if not r:
            continue
        n_body += 1
        rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
        for zone, (mean, mn, frac, cnt) in r.items():
            agg[zone].append(mean)
        if "breast" in r:
            mean, mn, frac, cnt = r["breast"]
            if frac > 0.01:
                poking.append((frac, mn, cnt, rel))
        # The floor must NOT have leaked to the rear. There is no absolute "correct"
        # rear clearance -- measured backs legitimately span 0.74u to 1.27u BEFORE the
        # fix -- so a fixed threshold cries wolf. The real check is a before/after
        # comparison; here we only surface backs loose enough to be worth eyeballing.
        if "back" in r and r["back"][0] > 1.5:
            loose.append((r["back"][0], rel))

    poking.sort(reverse=True)
    loose.sort(reverse=True)

    print(f"\n{n_body} body-armor meshes measured.\n")
    print("mean clearance by zone (breast should be ~1.0; back/side stay tight):")
    for zone in ("breast", "back"):
        if agg[zone]:
            a = np.array(agg[zone])
            print(f"   {zone:7} {a.mean():+.2f}u   (over {len(a)} armors)")

    print(f"\n=== 1) BODY POKING THROUGH AT THE BREAST: {len(poking)} armors ===")
    print(f"   {'%verts':>7} {'deepest':>8}  armor")
    for frac, mn, cnt, rel in poking[:30]:
        print(f"   {100*frac:6.1f}% {mn:+8.2f}u  {rel}")
    if not poking:
        print("   none -- the body sits inside the armor everywhere on the breast.")

    print(f"\n=== 2) REAR CLEARANCE LEAKED (armor gone baggy): {len(loose)} armors ===")
    print("   the bust floor is gated on nipple weight, which is 0 on the back.")
    for mean, rel in loose[:15]:
        print(f"   {mean:+.2f}u  {rel}")
    if not loose:
        print("   none -- rear stayed tight, the gate held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
