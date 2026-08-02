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

"""DEVELOPMENT TOOL -- not part of the shipped converter.

Lives in `scripts/`, which PyInstaller does not bundle, and has no GUI setting
or CLI surface in the exe. It exists to measure and verify the converter during
development; nothing in the user-facing tool depends on it.

Sweep the WHOLE converted output and record one row per shape.

Built because sampling burned us: a median over 13 metal shapes was written up as a
property of the population, and re-drawing the sample reversed it. A full census has
no sampling variance, and dumping it to JSONL means later questions are queries
instead of rescans -- so an answer can be re-checked without trusting a remembered
number.

    python scripts/analysis/collect_fit_dataset.py [--out FILE] [--limit N] [--no-rays]

One JSON object per line. Read-only; safe to run while a conversion is in progress.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                          # noqa: E402
from scipy.spatial import cKDTree                            # noqa: E402

from pyn import pynifly                                      # noqa: E402
from src import nif_convert as nc, paths                     # noqa: E402
from scripts.analysis.verify_skin_exposure import ray_blocked         # noqa: E402

BREAST = re.compile(r'breast', re.I)
BUTT = re.compile(r'butt', re.I)
PROXY = re.compile(r'^(colbody|collision|col_|virtual)|collision|colbody', re.I)
BOUNCE_VEC = np.array([0.0, 0.6, -0.8])     # forward+down: a bust bounce
BUTT_VEC = np.array([0.0, -0.6, -0.8])      # back+down: a butt bounce


def body_context():
    p = nc._find_ube_femalebody("_1")
    nf = pynifly.NifFile(filepath=str(p))
    sb = max(nf.shapes, key=lambda x: len(x.verts))
    V = np.asarray(sb.verts, np.float64)
    N = np.asarray(sb.normals, np.float64)
    n = len(V)
    w = [dict() for _ in range(n)]
    for b, pairs in (sb.bone_weights or {}).items():
        for vi, x in pairs:
            iv = int(vi)
            if 0 <= iv < n:
                w[iv][b] = w[iv].get(b, 0.0) + float(x)
    jb = np.array([sum(v for k, v in d.items() if BREAST.search(k)) for d in w])
    ju = np.array([sum(v for k, v in d.items() if BUTT.search(k)) for d in w])
    band = (V[:, 2] >= 88) & (V[:, 2] <= 104)
    ap = []
    for sel in (band & (V[:, 0] > 4), band & (V[:, 0] < -4)):
        ix = np.where(sel)[0]
        if len(ix):
            ap.append(V[ix[np.argmax(V[ix][:, 1])]])
    d = np.full(n, 1e9)
    for a in ap:
        d = np.minimum(d, np.linalg.norm(V - a, axis=1))
    nip = np.where(d < 2.2)[0]
    if len(nip) > 160:
        nip = nip[np.linspace(0, len(nip) - 1, 160).astype(int)]
    bt = np.where(ju > 0.12)[0]
    if len(bt) > 160:
        bt = bt[np.linspace(0, len(bt) - 1, 160).astype(int)]
    return V, N, jb, ju, nip, cKDTree(V), bt


def shape_row(s, path, nf, ctx, col, soft, lay, do_rays):
    VB, NB, jb, ju, nip, btree, bt_idx = ctx
    V = np.asarray(s.verts, np.float64)
    m = len(V)
    g2s = nc._shape_global_to_skin(s)
    ident = bool(g2s is None or nc._g2s_is_identity(g2s))
    Vw = nc._verts_skin_to_world(V, g2s)
    d, idx = btree.query(Vw)
    vw = [dict() for _ in range(m)]
    for b, pairs in (s.bone_weights or {}).items():
        for vi, x in pairs:
            iv = int(vi)
            if 0 <= iv < m:
                vw[iv][b] = vw[iv].get(b, 0.0) + float(x)
    ja = np.array([sum(v for k, v in vw[i].items() if BREAST.search(k)) for i in range(m)])
    jig = sum(1 for v in vw
              if any(nc._is_physics_jiggle_scale_bone(b) and w > 0.1 for b, w in v.items()))
    chain = sum(1 for v in vw
                if any(w > 0.1 and not nc._is_skeleton_bone(b) for b, w in v.items()))
    dif = str((s.textures or {}).get("Diffuse", "")).lower()
    row = {
        "file": path, "shape": s.name, "verts": m,
        "collider": s.name in col, "softbody": s.name in soft,
        "layered": s.name in lay, "proxy": bool(PROXY.search(s.name or "")),
        "identity_g2s": ident,
        "diffuse": dif.split("\\")[-1],
        "material": ("rigid" if any(k in dif for k in nc._RIGID_MATERIAL_KEYS)
                     else "soft" if any(k in dif for k in nc._SOFT_MATERIAL_KEYS)
                     else "unknown"),
        "ceiling": float(nc._chest_follow_for_shape(s)),
        "bones": len(s.bone_names or []),
        "has_chest_anchor": nc._CHEST_ANCHOR in (s.bone_weights or {}),
        "jig_verts": int(jig), "jig_frac": float(jig / m) if m else 0.0,
        "chain_frac": float(chain / m) if m else 0.0,
        "fit_whole": float((d < nc._CONFORM_FIT_PROX).mean()),
        "first_person": "1stperson" in path.lower(),
    }
    # --- shader numerics. Kept even though they did NOT separate material (best
    # Glossiness threshold scored 76.0% against a 76.3% base rate) -- recording them
    # means that conclusion can be re-tested on a census instead of re-argued.
    try:
        sh = getattr(s, "shader", None)
        pr = getattr(sh, "properties", None)
        row["gloss"] = float(getattr(pr, "Glossiness", 0.0))
        row["spec_str"] = float(getattr(pr, "Spec_Str", 0.0))
        row["env_scale"] = float(getattr(pr, "Env_Map_Scale", 0.0))
        row["env_mapped"] = bool(getattr(sh, "flag_environment_mapping", False))
        row["double_sided"] = bool(getattr(sh, "flag_double_sided", False))
        row["has_alpha"] = bool(getattr(s.properties, "alphaPropertyID", 0xFFFFFFFF)
                                != 0xFFFFFFFF)
    except Exception:
        pass
    # --- mesh density. The "armour is too coarse to follow the nipple curve" idea was
    # never tested; without tri size in the data it cannot be.
    try:
        tris = np.asarray(s.tris, np.int32)
        row["tris"] = int(len(tris))
        a, b2, c = Vw[tris[:, 0]], Vw[tris[:, 1]], Vw[tris[:, 2]]
        row["edge_mean"] = float(np.linalg.norm(a - b2, axis=1).mean())
    except Exception:
        pass
    # --- per-shape weight health: the two CTD classes plus the drift measure, so a
    # regression is visible per shape instead of only as a pack total.
    try:
        zw = sum(1 for b in (s.bone_names or [])
                 if not any(w > 1e-4 for _v, w in s.bone_weights[b]))
        sums = np.array([sum(v.values()) for v in vw if v])
        row["zero_weight_bones"] = int(zw)
        row["bad_sum_verts"] = int((np.abs(sums - 1.0) > 1e-3).sum()) if len(sums) else 0
        row["max_influences"] = int(max((len(v) for v in vw), default=0))
        row["breast_bones"] = sum(1 for b in (s.bone_names or []) if BREAST.search(b))
        row["butt_bones"] = sum(1 for b in (s.bone_names or []) if BUTT.search(b))
    except Exception:
        pass
    # the bust population this shape actually covers
    band = ((Vw[:, 2] > nc._CHEST_Z_LO) & (Vw[:, 2] < nc._CHEST_Z_HI)
            & (d <= nc._CHEST_PROX) & (jb[idx] > 0.05))
    row["bust_verts"] = int(band.sum())
    if band.sum() >= 20:
        row["fit_local"] = float((d[band] < nc._CONFORM_FIT_PROX).mean())
        row["clear_p50"] = float(np.median(d[band]))
        row["clear_p10"] = float(np.percentile(d[band], 10))
        row["body_jig_p50"] = float(np.median(jb[idx[band]]))
        row["follow_now"] = float(np.median(
            ja[band] / np.maximum(jb[idx[band]], 1e-6)))
        row["req_follow"] = float(np.percentile(
            [nc._chest_follow_required(d[i], jb[idx[i]]) for i in np.where(band)[0]], 90))
    # BUTT region -- reported clipping too, and it has no explicit SMP constraint
    # of its own (it inherits the default), so it is measured rather than assumed.
    jau = np.array([sum(v for k, v in vw[i].items() if BUTT.search(k)) for i in range(m)])
    bband = ((Vw[:, 2] > nc._BUTT_Z_LO) & (Vw[:, 2] < nc._BUTT_Z_HI)
             & (d <= nc._BUTT_PROX) & (ju[idx] > 0.05))
    row["butt_verts"] = int(bband.sum())
    if bband.sum() >= 20:
        row["butt_fit_local"] = float((d[bband] < nc._CONFORM_FIT_PROX).mean())
        row["butt_clear_p50"] = float(np.median(d[bband]))
        row["butt_body_jig_p50"] = float(np.median(ju[idx[bband]]))
        row["butt_follow_now"] = float(np.median(
            jau[bband] / np.maximum(ju[idx[bband]], 1e-6)))
        row["butt_req_follow"] = float(np.percentile(
            [nc._chest_follow_required(d[i], ju[idx[i]]) for i in np.where(bband)[0]], 90))
    if do_rays and ident:
        try:
            tris = np.asarray(s.tris, np.int32)
            # A CURVE, not two points: the bounce at which skin first appears is the
            # comparable number between armours, and it needs more than 4u/6u.
            if row["bust_verts"] >= 20:
                for mag in (0.0, 2.0, 3.0, 4.0, 5.0, 6.0):
                    u = BOUNCE_VEC * mag
                    bl = ray_blocked(VB[nip] + u * jb[nip, None], NB[nip],
                                     Vw + u * ja[:, None], tris)
                    row[f"exposed_{int(mag)}u"] = float((~bl).mean())
            # BUTT exposure -- reported in game, never ray-measured until now.
            if row["butt_verts"] >= 20 and len(bt_idx):
                for mag in (0.0, 4.0, 6.0):
                    u = BUTT_VEC * mag
                    bl = ray_blocked(VB[bt_idx] + u * ju[bt_idx, None], NB[bt_idx],
                                     Vw + u * jau[:, None], tris)
                    row[f"butt_exposed_{int(mag)}u"] = float((~bl).mean())
        except Exception:
            pass
    return row


def main() -> int:
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else _REPO / "fit_dataset.jsonl"
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    do_rays = "--no-rays" not in sys.argv
    root = paths.discover_layout().mods_root / "CBBEtoUBE Auto" / "meshes" / "!UBE"
    files = sorted(glob.glob(str(root / "**" / "*_1.nif"), recursive=True))
    if limit:
        files = files[:limit]
    ctx = body_context()
    # A dataset is only comparable to another if you know what produced it. First
    # line is a header record, not a shape.
    import datetime, subprocess
    try:
        ver = (_REPO / "src" / "version.py").read_text(encoding="utf-8")
        ver = re.search(r'__version__\s*=\s*"([^"]+)"', ver).group(1)
    except Exception:
        ver = "?"
    meta = {
        "_meta": True,
        "when": datetime.datetime.now().isoformat(timespec="seconds"),
        "converter_version": ver,
        "meshes": len(files),
        "rays": do_rays,
        "flags": {k: v for k, v in os.environ.items()
                  if k.startswith("CBBE2UBE_") and "INI" not in k and "ROOT" not in k},
        "constants": {n: float(getattr(nc, n)) for n in (
            "_CHEST_JIGGLE_CAP", "_CHEST_JIGGLE_PERBONE", "_CHEST_FOLLOW_SOFT",
            "_CHEST_FOLLOW_RIGID", "_CHEST_FOLLOW_BOUNCE", "_CHEST_FOLLOW_MARGIN",
            "_CHEST_RIGID_JIGGLE_FRAC", "_CONFORM_FIT_FRAC", "_CONFORM_FIT_PROX",
            "_CHEST_PROX", "_BUTT_PROX", "ANTIPOKE_BUST_CLEAR")},
    }
    print(f"sweeping {len(files)} meshes -> {out}  (rays={'on' if do_rays else 'off'})",
          flush=True)
    t0 = time.time()
    n_rows = 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta) + chr(10))
        for k, f in enumerate(files):
            if k and k % 250 == 0:
                print(f"  {k}/{len(files)}  rows={n_rows}  "
                      f"{time.time()-t0:.0f}s", flush=True)
            try:
                nf = pynifly.NifFile(filepath=f)
            except Exception:
                continue
            rel = f.replace("\\", "/").split("/!UBE/", 1)[-1]
            try:
                col = nc._hdt_collider_shape_names(Path(f), nif=nf)
                soft = nc._hdt_softbody_shape_names(Path(f), nif=nf)
                lay = nc._layered_cloth_shape_names(nf.shapes)
            except Exception:
                col, soft, lay = set(), set(), set()
            for s in nf.shapes:
                if s.name == "BaseShape" or len(s.verts) < 200:
                    continue
                try:
                    row = shape_row(s, rel, nf, ctx, col, soft, lay, do_rays)
                except Exception:
                    continue
                fh.write(json.dumps(row) + "\n")
                n_rows += 1
    print(f"done: {n_rows} shape rows in {time.time()-t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
