"""DEVELOPMENT TOOL -- not part of the shipped converter.

Lives in `scripts/`, which PyInstaller does not bundle, and has no GUI setting
or CLI surface in the exe. It exists to measure and verify the converter during
development; nothing in the user-facing tool depends on it.

Pack-wide survey: which armour lets skin show once the body MOVES?

Uses the validated ray metric (`verify_skin_exposure`), not a distance. For each
armour shape it poses body and armour by their own jiggle weights and reports the
fraction of chest / butt skin that becomes visible.

    python scripts/survey_motion_clipping.py [--limit N] [--bounce 4.0]

Read-only.
"""
from __future__ import annotations
import glob, os, re, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO)); sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np
from pyn import pynifly                                     # noqa: E402
from src import nif_convert as nc, paths                    # noqa: E402
from scripts.verify_skin_exposure import ray_blocked        # noqa: E402

JIG = re.compile(r'^[LR] Breast0[123]$|breast|butt', re.I)
# Non-rendered helpers only. Deliberately NOT _CONFORM_SKIP_NAMES, which also lists
# robe/cloak/cape/dress -- those are loose garments the conform pass skips, but a
# dress that exposes skin is a real finding here. "col" alone would eat "Collar".
PROXY = re.compile(r'^(colbody|collision|col_|virtual|virtualground|virtualbody)'
                   r'|collision|colbody', re.I)
BREAST = re.compile(r'breast', re.I)
BUTT = re.compile(r'butt', re.I)


def body_regions():
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
    jb = np.array([sum(x for b, x in w[i].items() if BREAST.search(b)) for i in range(n)])
    ju = np.array([sum(x for b, x in w[i].items() if BUTT.search(b)) for i in range(n)])
    band = (V[:, 2] >= 88) & (V[:, 2] <= 104)
    apex = []
    for sel in (band & (V[:, 0] > 4), band & (V[:, 0] < -4)):
        idx = np.where(sel)[0]
        if len(idx):
            apex.append(V[idx[np.argmax(V[idx][:, 1])]])
    d = np.full(n, 1e9)
    for a in apex:
        d = np.minimum(d, np.linalg.norm(V - a, axis=1))
    chest = np.where(d < 2.5)[0]
    butt = np.where(ju > 0.12)[0]
    def thin(ix, k=120):
        return ix if len(ix) <= k else ix[np.linspace(0, len(ix) - 1, k).astype(int)]
    return V, N, jb, ju, thin(chest), thin(butt)


def survey(files, bounce=4.0):
    VB, NB, jb, ju, chest, butt = body_regions()
    rows = []
    skipped = []
    for f in files:
        try:
            nf = pynifly.NifFile(filepath=f)
        except Exception:
            continue
        rel = f.replace("\\", "/").split("/!UBE/", 1)[-1]
        # Per-vertex soft-bodies are SIMULATED by FSMP at runtime: their rendered
        # shape does not come from bone skinning, so posing them by skin weights
        # says nothing about what the player sees. Scored separately, never mixed
        # in with skin-driven armour.
        try:
            soft = nc._hdt_softbody_shape_names(Path(f), nif=nf)
        except Exception:
            soft = set()
        for s in nf.shapes:
            if s.name == "BaseShape" or PROXY.search(s.name or ""):
                skipped.append(s.name)
                continue
            try:
                V = np.asarray(s.verts, np.float64)
                m = len(V)
                if m < 200:
                    continue
                g2s = nc._shape_global_to_skin(s)
                if not (g2s is None or nc._g2s_is_identity(g2s)):
                    continue
                Vw = nc._verts_skin_to_world(V, g2s)
                tris = np.asarray(s.tris, np.int32)
                vw = [dict() for _ in range(m)]
                for b, pairs in (s.bone_weights or {}).items():
                    for vi, x in pairs:
                        iv = int(vi)
                        if 0 <= iv < m:
                            vw[iv][b] = vw[iv].get(b, 0.0) + float(x)
                ab = np.array([sum(x for b, x in vw[i].items() if BREAST.search(b)) for i in range(m)])
                au = np.array([sum(x for b, x in vw[i].items() if BUTT.search(b)) for i in range(m)])
                out = {}
                for tag, idx, bw, aw, vec in (
                        ("chest", chest, jb, ab, np.array([0.0, 0.6, -0.8])),
                        ("butt",  butt,  ju, au, np.array([0.0, -0.6, -0.8]))):
                    if len(idx) == 0:
                        continue
                    # only score a region this shape actually covers at rest
                    if ray_blocked(VB[idx], NB[idx], Vw, tris).mean() < 0.5:
                        continue
                    u = vec * bounce
                    blocked = ray_blocked(VB[idx] + u * bw[idx, None], NB[idx],
                                          Vw + u * aw[:, None], tris)
                    out[tag] = 100.0 * float((~blocked).mean())
                if out:
                    tag = "SOFTBODY" if s.name in soft else "skin"
                    rows.append((max(out.values()), out, s.name, rel, tag))
            except Exception:
                continue
    return rows, skipped


def main() -> int:
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 400
    bounce = float(sys.argv[sys.argv.index("--bounce") + 1]) if "--bounce" in sys.argv else 4.0
    lay_root = Path(os.environ.get("CBBE2UBE_OUT_ROOT", "")) if os.environ.get("CBBE2UBE_OUT_ROOT") else None
    root = lay_root or (paths.discover_layout().mods_root / "CBBEtoUBE Auto" / "meshes" / "!UBE")
    files = sorted(glob.glob(str(root / "**" / "*_1.nif"), recursive=True))[:lim]
    print(f"surveying {len(files)} meshes at bounce {bounce}u\n", flush=True)
    rows, skipped = survey(files, bounce)
    rows.sort(key=lambda r: r[0], reverse=True)   # dicts are not orderable
    bad = [r for r in rows if r[0] >= 1.0]
    print(f"shapes scored: {len(rows)}   showing skin under motion: {len(bad)}\n")
    sb = [r for r in rows if r[4] == "SOFTBODY"]
    print(f"   of which per-vertex SOFT-BODY (metric does not apply): {len(sb)}")
    rows = [r for r in rows if r[4] == "skin"]
    bad = [r for r in rows if r[0] >= 1.0]
    print(f"   skin-driven shapes showing skin under motion: {len(bad)}")
    print("")
    print(f"{'worst%':>7}  {'chest':>6} {'butt':>6}  shape / file")
    for worst, out, nm, rel, _tag in rows[:25]:
        print(f"{worst:7.1f}  {out.get('chest', float('nan')):6.1f} "
              f"{out.get('butt', float('nan')):6.1f}  {nm[:20]:<20} {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
