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

"""Census of body-through-garment PENETRATION across the whole converted output.

Companion to `collect_fit_dataset.py`, same discipline: full census, no sampling, one
JSON object per line, so later questions are queries rather than rescans.

WHAT IS DIFFERENT FROM THE OLD NUMBERS. Penetration is measured against the garment
SURFACE with a contact gate (`scripts/mesh_penetration`), not by projecting the nearest
garment VERTEX onto the body normal. The old way reported drape as penetration wherever
cloth hangs away from the body -- verts it called "poking" at the butt averaged 4.09u
from the garment, 0.2% within 1.0u. That produced a bogus "breast 2.3% vs butt 13.7%"
asymmetry and a reverted feature. Every rear number predating this file is void.

Each row also records BOTH metrics, so the disagreement itself is queryable.

    python scripts/collect_penetration_census.py [--out FILE] [--limit N]
                                                 [--contact U] [--out-mod NAME]

Read-only. Resolves the output via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402
from scipy.spatial import cKDTree                                # noqa: E402

from pyn import pynifly                                          # noqa: E402
from src import nif_convert as nc, paths                         # noqa: E402
from src.nif_convert import UBE_BODY_INJECT_NAMES                # noqa: E402
from src.nif_convert import _body_normals_or_compute             # noqa: E402
from scripts.mesh_penetration import surface_penetration         # noqa: E402

# Physics helper shapes are not the visible garment. Counting them as coverage makes
# the body read as protected where nothing renders.
PHYSICS_ONLY = {"collision", "proxy", "stabilizer"}

# front = +Y on the UBE body. Breast z90-102 (apex ~95) and z99-112 = UPPER CHEST are
# pinned facts about this body -- measuring the bust at the upper-chest band hides the
# defect entirely.
REGIONS = (
    ("breast",      lambda z, ny: (z >= 90) & (z <= 102) & (ny > 0.3)),
    ("upper_chest", lambda z, ny: (z >= 99) & (z <= 112) & (ny > 0.3)),
    ("belly",       lambda z, ny: (z >= 75) & (z < 90) & (ny > 0.3)),
    ("butt",        lambda z, ny: (z >= 55) & (z <= 75) & (ny < -0.3)),
    ("lower_back",  lambda z, ny: (z > 75) & (z <= 95) & (ny < -0.3)),
    ("thigh",       lambda z, ny: (z >= 35) & (z < 55)),
)


def _shapes(nif):
    body, garments = None, []
    for sh in nif.shapes:
        try:
            if not len(sh.verts):
                continue
        except Exception:
            continue
        if sh.name in UBE_BODY_INJECT_NAMES:
            body = sh
        elif sh.name.strip().lower() not in PHYSICS_ONLY:
            garments.append(sh)
    return body, garments


def _combined(garments):
    """Concatenate garment shapes into one vert/tri/normal set with offset indices."""
    vs, ts, ns, off = [], [], [], 0
    for sh in garments:
        v = np.asarray(sh.verts, dtype=np.float64)
        try:
            t = np.asarray(sh.tris, dtype=np.int64).reshape(-1, 3)
        except Exception:
            continue
        n = None
        try:
            n = np.asarray(sh.normals, dtype=np.float64)
        except Exception:
            n = None
        vs.append(v)
        ts.append(t + off)
        ns.append(n if (n is not None and len(n) == len(v))
                  else np.zeros((len(v), 3)))
        off += len(v)
    if not vs:
        return None, None, None
    return np.vstack(vs), np.vstack(ts), np.vstack(ns)


def row_for(path: Path, root: Path, contact: float = 5.0) -> "dict | None":
    nif = pynifly.NifFile(str(path))
    body, garments = _shapes(nif)
    if body is None or not garments:
        return None
    gv, gt, gn = _combined(garments)
    if gv is None or not len(gt):
        return None
    bv = np.asarray(body.verts, dtype=np.float64)
    bn = np.asarray(_body_normals_or_compute(body), dtype=np.float64)
    bbones = set(body.bone_names or [])

    signed, dist, covered, agree = surface_penetration(
        bv, gv, gt, gn if gn.any() else None, contact=contact)
    # The OLD metric, kept alongside so the disagreement is queryable, never to be
    # used on its own for a rear number.
    od, oi = cKDTree(gv).query(bv, k=1)
    old_signed = np.einsum("ij,ij->i", gv[oi] - bv, bn)

    z, ny = bv[:, 2], bn[:, 1]
    rigged = [bool(nc._shape_has_hdt_smp_rigging(s, bbones)) for s in garments]
    row = {
        "armor": str(path.relative_to(root)).replace("\\", "/"),
        "garment_shapes": [s.name for s in garments],
        "garment_verts": int(len(gv)), "garment_tris": int(len(gt)),
        "body_verts": int(len(bv)),
        "smp_rigged_all": bool(all(rigged)),
        "smp_rigged_any": bool(any(rigged)),
        "winding_agreement": (round(agree, 4) if agree is not None else None),
        "contact_gate": contact,
        "regions": {},
    }
    for name, sel in REGIONS:
        m = sel(z, ny)
        cov = m & covered
        r = {"verts": int(m.sum()), "covered": int(cov.sum())}
        if cov.sum():
            s = signed[cov]
            pk = s > 0
            r.update(
                pct_poking=round(float(100.0 * pk.mean()), 3),
                max_depth=round(float(s.max()), 4),
                mean_gap=round(float(-s.mean()), 4),          # +ve = body inside
                # the OLD metric on the SAME verts, for the disagreement column
                old_pct_poking=round(float(100.0 * (old_signed[cov] < 0).mean()), 3),
            )
            # Depth distribution, so a truncated measurement is visible instead of
            # looking like a real ceiling: max_depth == contact_gate means the gate
            # clipped it, not the mesh.
            if pk.any():
                d_pk = s[pk]
                r.update(
                    depth_p50=round(float(np.percentile(d_pk, 50)), 4),
                    depth_p99=round(float(np.percentile(d_pk, 99)), 4),
                    depth_truncated=bool(float(s.max()) >= contact - 1e-6),
                )
            r["old_pct_poking_ungated"] = round(
                float(100.0 * (old_signed[m & (od < 6.0)] < 0).mean()), 3
            ) if (m & (od < 6.0)).any() else None
        row["regions"][name] = r
    return row


def main():
    argv = sys.argv[1:]

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    out = Path(opt("--out", "penetration_census.jsonl"))
    limit = int(opt("--limit", "0") or 0)
    contact = float(opt("--contact", "5.0"))
    lay = paths.discover_layout()
    root = (lay.mods_root / opt("--out-mod", os.environ.get("CBBE2UBE_OUT_MOD",
                                                            "CBBEtoUBE Auto"))
            / "meshes" / "!UBE")
    if not root.is_dir():
        raise SystemExit(f"output not found: {root}")

    files = sorted(root.rglob("*_1.nif"))
    if limit:
        files = files[:limit]
    print(f"scanning {len(files)} mesh(es) under {root}\n"
          f"judge radius {contact}u -- verts further from the garment are NOT judged,"
          f" and this also BOUNDS measurable depth (see depth_truncated)")
    t0, n, skipped = time.time(), 0, 0
    with out.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(files, 1):
            try:
                row = row_for(p, root, contact)
            except Exception as exc:
                skipped += 1
                # Loud, not silent: a swallowed measurement error is indistinguishable
                # from a clean zero, which has cost this project a wrong diagnosis.
                print(f"  SKIP {p.name}: {type(exc).__name__}: {exc}")
                continue
            if row is None:
                skipped += 1
                continue
            fh.write(json.dumps(row) + "\n")
            n += 1
            if i % 100 == 0:
                print(f"  {i}/{len(files)}  ({n} rows, {skipped} skipped)")
    print(f"\n{n} rows -> {out}  ({skipped} skipped, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
