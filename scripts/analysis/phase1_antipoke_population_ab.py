"""Population A/B for #phase1-antipoke: per piece, per band, with the pair.

F010 is the reason this is not a clipping census: a copy-path repair that
improved clipping uniformly still could not ship, because standoff inflated on
9 of 9 pieces. So every row carries clip AND standoff AND coverage, and the
verdict lines count pieces, not a pooled percentage that one big piece can
carry.

    python population_ab.py <off_root> <on_root> [bands]
"""
import sys
from pathlib import Path
import numpy as np

# scripts/analysis/<this> -> repo root is three parents up. NEVER hardcode a
# machine path here: it publishes one user's layout and silently makes the
# harness useless everywhere else.
_REPO = Path(__file__).resolve().parent.parent.parent
REPO = _REPO
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "analysis"))
from src import nif_io, body_zones as bz, nif_convert as nc      # noqa: E402
import standoff_audit as sa                                       # noqa: E402
from scipy.spatial import cKDTree                                 # noqa: E402

BANDS = {"butt": bz.butt_mask, "bust": bz.breast_mask}
off_root, on_root = Path(sys.argv[1]), Path(sys.argv[2])
want = (sys.argv[3].split(",") if len(sys.argv) > 3 else ["butt", "bust"])
MIN_BAND = 60          # a band this piece barely touches says nothing


def world(sh):
    return nc._verts_skin_to_world(np.asarray(sh.verts, np.float64),
                                   nc._shape_global_to_skin(sh))


_body_cache = {}


def body_for(path):
    w = "_1" if Path(path).stem.endswith("_1") else "_0"
    if w not in _body_cache:
        ext = nc._find_ube_femalebody(w)
        nf = nif_io.open_nif_retry(str(ext))
        b = max(nf.shapes, key=lambda s: len(s.verts))
        bV = world(b)
        bN = np.asarray(b.normals, np.float64)
        bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)
        _body_cache[w] = (bV, np.asarray(b.tris, np.int64).reshape(-1, 3), bN,
                          cKDTree(bV))
    return _body_cache[w]


def score(path):
    nf = nif_io.open_nif_retry(str(path))
    if any(s.name in nc.UBE_BODY_INJECT_NAMES for s in nf.shapes):
        return None                      # body-swap piece: not our population
    bV, bT, bN, tree = body_for(path)
    out = {}
    for sh in nf.shapes:
        if not any(v for v in (sh.textures or {}).values()):
            continue
        gV = world(sh)
        gT = np.asarray(sh.tris, np.int64).reshape(-1, 3)
        _d, idx = tree.query(gV, k=1)
        signed = np.einsum('ij,ij->i', gV - bV[idx], bN[idx])
        for band in want:
            # BOOLEAN MASK, not indices: clip_stats/standoff_stats call
            # `np.flatnonzero(mask)` THEMSELVES. Passing indices makes them
            # flatnonzero an index array, which silently selects a meaningless
            # vertex set -- it showed up as a pants shape reporting 35% BUST
            # coverage, equal to its butt coverage, because both bands were
            # measuring the same wrong verts.
            bmask = BANDS[band](bV)
            if int(bmask.sum()) < MIN_BAND:
                continue
            near = np.isin(idx, np.flatnonzero(bmask))
            c = sa.clip_stats(bV, bT, bN, gV, gT, bmask)
            s = sa.standoff_stats(bV, bN, gV, gT, bmask)
            # relevance is what the metric itself says it covers, not a
            # nearest-neighbour prefilter
            if float(c.get("covered_pct", 0.0)) < 5.0:
                continue
            out[(sh.name, band)] = dict(
                inside=int(((signed < 0) & near).sum()),
                near=int(near.sum()),
                clip=float(c.get("clipping_pct", float("nan"))),
                cov=float(c.get("covered_pct", float("nan"))),
                so=float(s.get("median", float("nan"))),
                so90=float(s.get("p90", float("nan"))),
            )
    return out


files = sorted(p for p in off_root.rglob("*_1.nif")
               if "1stperson" not in p.name.lower())
print(f"{len(files)} candidate piece(s)\n", flush=True)
rows = []
for i, f in enumerate(files):
    g = on_root / f.relative_to(off_root)
    if not g.is_file():
        continue
    try:
        a, b = score(f), score(g)
    except Exception as e:
        print(f"  skip {f.name}: {type(e).__name__}: {e}")
        continue
    if not a or not b:
        continue
    for k in sorted(set(a) & set(b)):
        rows.append((f.name, k[0], k[1], a[k], b[k]))
    if (i + 1) % 10 == 0:
        print(f"  ...{i+1}/{len(files)}", flush=True)

print(f"\n{'piece':<26}{'shape':<18}{'band':<6}"
      f"{'inside':>14}{'clip%':>16}{'standoff':>16}{'cover%':>14}")
agg = {}
for name, shape, band, a, b in rows:
    print(f"{name[:25]:<26}{shape[:17]:<18}{band:<6}"
          f"{a['inside']:6d}->{b['inside']:<7d}"
          f"{a['clip']:7.3f}->{b['clip']:<8.3f}"
          f"{a['so']:7.3f}->{b['so']:<8.3f}"
          f"{a['cov']:6.1f}->{b['cov']:<7.1f}")
    d = agg.setdefault(band, dict(n=0, clip_b=0, clip_w=0, so_up=0, so_dn=0,
                                  cov_dn=0, ins_b=0, ins_w=0))
    d["n"] += 1
    if b["clip"] < a["clip"] - 0.05: d["clip_b"] += 1
    if b["clip"] > a["clip"] + 0.05: d["clip_w"] += 1
    if b["so"] > a["so"] + 0.02: d["so_up"] += 1
    if b["so"] < a["so"] - 0.02: d["so_dn"] += 1
    if b["cov"] < a["cov"] - 0.5: d["cov_dn"] += 1
    if b["inside"] < a["inside"]: d["ins_b"] += 1
    if b["inside"] > a["inside"]: d["ins_w"] += 1

print()
for band, d in agg.items():
    print(f"=== {band.upper()} band, {d['n']} (shape) arms ===")
    print(f"  verts inside body   better {d['ins_b']:3d}   WORSE {d['ins_w']:3d}")
    print(f"  clipping            better {d['clip_b']:3d}   WORSE {d['clip_w']:3d}")
    print(f"  standoff            OUT    {d['so_up']:3d}   in    {d['so_dn']:3d}"
          f"   (out = further off the body; F010 died here)")
    print(f"  coverage dropped >0.5pt    {d['cov_dn']:3d}")
