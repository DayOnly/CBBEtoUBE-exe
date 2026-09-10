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

"""How far the SURFACE turned between a source mesh and its refit.

`#layer-rotation` reported a median 24.7 degrees of normal rotation on a belt
strap and used it to argue that no displacement-transfer layer pass can hold an
authored gap. That number decides which end of the pipeline is worth working
on, so it needs an estimator whose failure modes are known.

THREE ESTIMATORS, DELIBERATELY, because they disagree and the disagreement is
the finding:

  * FACE   -- per triangle, from the triangle's own corners. The only one that
              answers "did this piece of surface turn"; it cannot be perturbed
              by anything off the triangle.
  * VERTEX -- area-weighted mean of the incident face normals. This is what a
              layer pass actually rides on, and it is UNSTABLE where the faces
              around a vertex point in opposite directions: on a strap two
              thousandths of a unit thick, the front and back sheets share a rim
              vertex, so their normals cancel and the mean is decided by which
              face won a rounding contest. Such a vertex can read 90+ degrees
              while every face around it turned 3.
  * STORED -- the normals in the file. Reported only to identify which estimator
              an earlier figure came from; a source body routinely ships them
              all zero, so never conclude from these alone.

AREA-WEIGHT EVERYTHING. A garment's triangle areas span four orders of
magnitude, so an unweighted median is a median over the SMALLEST triangles --
the rim slivers and the fittings -- not over the surface anyone sees. Both are
printed; the weighted one is the answer to "did the garment turn".

Correspondence is index-wise, guarded: the refit preserves vertex order, and a
count mismatch is reported as a skip rather than silently compared.

Usage:
  python scripts/analysis/normal_rotation.py <source.nif> <output.nif>
      [--shape NAME]... [--json out.json] [--csv out.csv]

  # per-pass bisect: any number of stage dumps, measured against the first
  python scripts/analysis/normal_rotation.py --stages dump_dir [--shape NAME]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                     # noqa: E402
from pyn import pynifly                                # noqa: E402

# A vertex whose incident faces disagree by more than this is a rim/fold vertex:
# its area-weighted mean normal is a cancellation, not a surface direction.
# 0.35 is |mean unit normal| -- 1.0 is a flat neighbourhood, 0.0 is two opposed
# sheets of equal area.
COHERENT_VERT = 0.35


def _load(path):
    """{shape name: (world verts, tris, stored normals or None)}."""
    nf = pynifly.NifFile(filepath=str(path))
    out = {}
    for s in nf.shapes:
        v = np.asarray(s.verts, float)
        t = s.transform
        R = np.asarray(t.rotation, float).reshape(3, 3)
        M = R * float(getattr(t, "scale", 1.0))
        if abs(np.linalg.det(M)) > 1e-9:
            v = v @ M.T + np.asarray(t.translation, float)
        try:
            n = np.asarray(s.normals, float)
            if n.shape != v.shape:
                n = None
        except Exception:
            n = None
        out[s.name] = (v, np.asarray(s.tris).reshape(-1, 3), n)
    return out


def face_normals(v, t):
    """Unit normals and areas. A DEGENERATE triangle (zero area, from a
    collapsed or duplicated vertex) has no normal: it returns the zero vector,
    whose dot with anything is 0, which arccos reads as exactly 90 degrees. So
    a mesh compared against ITSELF scores 90 on every degenerate face -- seen
    on a control run where every vertex had moved 0.000u. The area is returned
    alongside so callers can mask them out rather than average them in."""
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a[:, None], 1e-12), 0.5 * a


def vertex_normals(v, t):
    """Area-weighted, and the coherence of each vertex's neighbourhood.

    `coh` is |sum of area-weighted face normals| / sum of areas: it says how
    much of the neighbourhood survived the averaging. Returned alongside because
    a rotation read at a low-coherence vertex is not a measurement of anything.
    """
    fn, fa = face_normals(v, t)
    acc = np.zeros_like(v)
    wsum = np.zeros(len(v))
    w = fn * fa[:, None]
    for k in range(3):
        np.add.at(acc, t[:, k], w)
        np.add.at(wsum, t[:, k], fa)
    ln = np.linalg.norm(acc, axis=1)
    coh = ln / np.maximum(wsum, 1e-12)
    return acc / np.maximum(ln[:, None], 1e-12), coh


def _angles(a, b):
    return np.degrees(np.arccos(np.clip((a * b).sum(axis=1), -1.0, 1.0)))


def _stats(ang, w=None):
    if not len(ang):
        return {}
    out = {
        "n": int(len(ang)),
        "p50": float(np.percentile(ang, 50)),
        "p90": float(np.percentile(ang, 90)),
        "max": float(ang.max()),
        "frac_gt90": float((ang > 90).mean()),
    }
    if w is not None and w.sum() > 0:
        order = np.argsort(ang)
        cw = np.cumsum(w[order]) / w.sum()
        out["p50_aw"] = float(ang[order][np.searchsorted(cw, 0.50)])
        out["p90_aw"] = float(ang[order][np.searchsorted(cw, 0.90)])
        # Share of AREA that turned more than 30 degrees -- the figure that
        # says whether a viewer would see it, which a percentile cannot.
        out["area_frac_gt30"] = float(w[ang > 30].sum() / w.sum())
    return out


def compare(src, out, names=None):
    """One row per shape common to both meshes."""
    rows = []
    common = sorted(set(src) & set(out))
    for name in common:
        if names and name not in names:
            continue
        sv, st, sn = src[name]
        ov, ot, on = out[name]
        if len(sv) != len(ov) or len(st) != len(ot):
            rows.append({"shape": name,
                         "skipped": f"verts {len(sv)}->{len(ov)} "
                                    f"tris {len(st)}->{len(ot)}"})
            continue
        sfn, sfa = face_normals(sv, st)
        ofn, ofa = face_normals(ov, ot)
        # Degenerate on EITHER side has no normal to compare; keeping them
        # scores a clean 90 (see `face_normals`).
        live = (sfa > 1e-12) & (ofa > 1e-12)
        face = _angles(sfn[live], ofn[live])
        svn, scoh = vertex_normals(sv, st)
        ovn, ocoh = vertex_normals(ov, ot)
        vert = _angles(svn, ovn)
        # Vertex area, so the vertex estimator is weighted on the same footing
        # as the face one.
        va = np.zeros(len(sv))
        for k in range(3):
            np.add.at(va, st[:, k], ofa / 3.0)
        disp = np.linalg.norm(ov - sv, axis=1)
        row = {
            "shape": name,
            "verts": int(len(sv)),
            "tris": int(len(st)),
            "tris_per_vert": round(len(st) / max(len(sv), 1), 3),
            "degenerate_tris": int((~live).sum()),
            "face": _stats(face, ofa[live]),
            "vertex": _stats(vert, va),
            "disp_p50": float(np.percentile(disp, 50)),
            "disp_p90": float(np.percentile(disp, 90)),
            "disp_max": float(disp.max()),
        }
        if sn is not None and on is not None:
            sl = np.linalg.norm(sn, axis=1)
            ol = np.linalg.norm(on, axis=1)
            ok = (sl > 1e-6) & (ol > 1e-6)
            if ok.any():
                row["stored"] = _stats(
                    _angles(sn[ok] / sl[ok, None], on[ok] / ol[ok, None]),
                    va[ok])
                row["stored"]["zeroed"] = int((~ok).sum())
        # THE CONTROL. Split the vertex estimator by whether the vertex has a
        # well-defined normal at all. If the whole rotation lives in the
        # incoherent half, the shape is thin, not turned.
        good = (scoh >= COHERENT_VERT) & (ocoh >= COHERENT_VERT)
        row["coherent_frac"] = float(good.mean())
        row["vertex_coherent"] = _stats(vert[good], va[good])
        row["vertex_incoherent"] = _stats(vert[~good], va[~good])
        rows.append(row)
    return rows


def _fmt(row):
    if "skipped" in row:
        return f"  {row['shape']:16s} SKIPPED  {row['skipped']}"
    f, v = row["face"], row["vertex"]
    vc, vi = row["vertex_coherent"], row["vertex_incoherent"]
    lines = [
        f"  {row['shape']:16s} verts {row['verts']:6d}  "
        f"tris/vert {row['tris_per_vert']:.2f}  "
        f"disp p50 {row['disp_p50']:.3f} p90 {row['disp_p90']:.3f} "
        f"max {row['disp_max']:.3f}",
        f"      FACE    p50 {f['p50']:6.2f}  p90 {f['p90']:6.2f}  "
        f"max {f['max']:6.2f}   area-wtd p50 {f.get('p50_aw', -1):6.2f} "
        f"p90 {f.get('p90_aw', -1):6.2f}   >30deg by area "
        f"{100 * f.get('area_frac_gt30', 0):5.1f}%",
        f"      VERTEX  p50 {v['p50']:6.2f}  p90 {v['p90']:6.2f}  "
        f"max {v['max']:6.2f}   area-wtd p50 {v.get('p50_aw', -1):6.2f} "
        f"p90 {v.get('p90_aw', -1):6.2f}   >90deg {100 * v['frac_gt90']:5.1f}%",
        f"      ...of which coherent ({100 * row['coherent_frac']:.1f}% of "
        f"verts): p50 {vc.get('p50', -1):6.2f} p90 {vc.get('p90', -1):6.2f}"
        f"   |  incoherent: p50 {vi.get('p50', -1):6.2f} "
        f"p90 {vi.get('p90', -1):6.2f}",
    ]
    if "stored" in row:
        s = row["stored"]
        lines.append(
            f"      STORED  p50 {s['p50']:6.2f}  p90 {s['p90']:6.2f}  "
            f"max {s['max']:6.2f}   (zeroed {s['zeroed']})")
    return "\n".join(lines)


def main() -> int:
    argv = sys.argv[1:]
    names = set()
    while "--shape" in argv:
        i = argv.index("--shape")
        names.add(argv[i + 1])
        del argv[i:i + 2]

    def opt(f, d=None):
        if f in argv:
            i = argv.index(f)
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
        return d

    js = opt("--json")
    stages = opt("--stages")

    if stages:
        return _stage_mode(Path(stages), names, js)

    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) < 2:
        print(__doc__)
        return 2
    src, out = Path(pos[0]), Path(pos[1])
    rows = compare(_load(src), _load(out), names or None)
    print(f"SOURCE {src}")
    print(f"OUTPUT {out}")
    for r in rows:
        print(_fmt(r))
    if js:
        Path(js).write_text(json.dumps(
            {"source": str(src), "output": str(out), "shapes": rows}, indent=2))
        print(f"\nwrote {js}")
    return 0


def _stage_mode(d: Path, names, js) -> int:
    """Bisect a `CBBE2UBE_STAGE_DUMP` directory: `<nif>__<shape>.npz`, each
    holding `s00_entry`, `s01_<pass>`, ... and `tris`.

    Two columns per pass, and they answer different questions:

      CUMULATIVE -- this stage against the ENTRY geometry. This is the number
                    that has to reach the ~15-25 degrees the shipped mesh
                    shows, and where it gets there names the culprit.
      STEP       -- this stage against the PREVIOUS one, i.e. what this pass
                    alone turned. Steps do NOT add up to the cumulative: two
                    passes can each turn the surface and partly undo each
                    other, which is exactly what passes 4 and 5 are documented
                    as doing, so a large step with no cumulative movement is a
                    pass being cancelled, not a pass being innocent.
    """
    files = sorted(d.glob("*.npz"))
    if not files:
        print(f"no stage dumps in {d}")
        return 2
    out_rows = []
    for f in files:
        z = np.load(f, allow_pickle=True)
        shape = f.stem.split("__", 1)[-1]
        if names and shape not in names:
            continue
        stages = sorted(k for k in z.files if k.startswith("s")
                        and k[1:3].isdigit())
        if len(stages) < 2 or "tris" not in z.files:
            print(f"{f.name}: only {len(stages)} stage(s), skipped")
            continue
        t = z["tris"].reshape(-1, 3)
        entry = z[stages[0]].astype(float)
        print(f"\n=== {f.stem}   {len(entry)} verts, {len(t)} tris, "
              f"{len(stages)} stages")
        print(f"    {'pass':22s} {'CUMULATIVE vs entry':>34s}   "
              f"{'STEP vs previous':>26s}")
        print(f"    {'':22s} {'face p50aw  p90aw  >30%':>34s}   "
              f"{'face p50aw  p90aw  moved':>26s}")
        prev = entry
        for sk in stages[1:]:
            cur = z[sk].astype(float)
            if cur.shape != entry.shape:
                print(f"    {sk[4:]:22s} SKIPPED verts {len(entry)}->{len(cur)}")
                prev = cur
                continue
            cum = compare({shape: (entry, t, None)},
                          {shape: (cur, t, None)}, {shape})[0]
            step = compare({shape: (prev, t, None)},
                           {shape: (cur, t, None)}, {shape})[0]
            nmoved = int((np.linalg.norm(cur - prev, axis=1) > 1e-4).sum())
            cf, sf = cum["face"], step["face"]
            print(f"    {sk[4:]:22s} "
                  f"{cf.get('p50_aw', 0):9.2f} {cf.get('p90_aw', 0):6.2f} "
                  f"{100 * cf.get('area_frac_gt30', 0):6.1f}%"
                  f"   |{sf.get('p50_aw', 0):9.2f} {sf.get('p90_aw', 0):6.2f} "
                  f"{nmoved:7d}")
            out_rows.append({"file": f.stem, "shape": shape,
                             "pass": sk[4:], "cumulative": cum, "step": step,
                             "verts_moved": nmoved})
            prev = cur
    if js:
        Path(js).write_text(json.dumps(out_rows, indent=2))
        print(f"\nwrote {js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
