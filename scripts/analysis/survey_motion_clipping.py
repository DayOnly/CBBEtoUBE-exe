"""DEVELOPMENT TOOL -- not part of the shipped converter.

Lives in `scripts/`, which PyInstaller does not bundle, and has no GUI setting
or CLI surface in the exe. It exists to measure and verify the converter during
development; nothing in the user-facing tool depends on it.

Pack-wide survey: which armour lets skin show once the body MOVES?

Uses the validated ray metric (`verify_skin_exposure`), not a distance. For each
armour shape it poses body and armour by their own jiggle weights and reports the
fraction of chest / butt skin that becomes visible.

    python scripts/analysis/survey_motion_clipping.py [--limit N] [--bounce 4.0]

Read-only.
"""
from __future__ import annotations
import collections, glob, os, re, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO)); sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np
from pyn import pynifly                                     # noqa: E402
from src import nif_convert as nc, paths                    # noqa: E402
from scripts.analysis.verify_skin_exposure import ray_blocked        # noqa: E402
from scripts.analysis import standoff_audit as sa                    # noqa: E402
from scipy.spatial import cKDTree                                    # noqa: E402

JIG = re.compile(r'^[LR] Breast0[123]$|breast|butt', re.I)
# Non-rendered helpers only. Deliberately NOT _CONFORM_SKIP_NAMES, which also lists
# robe/cloak/cape/dress -- those are loose garments the conform pass skips, but a
# dress that exposes skin is a real finding here. "col" alone would eat "Collar".
#
# THE NAME ALONE WAS NOT ENOUGH. Anchoring on `col_` WITH AN UNDERSCORE let
# `ColSkirt`, `ColBelt`, `ColBack`, `ColLegs` and `Colision` straight through:
# measured over the shipped pack, 80 shapes whose name starts col*/hdt* miss
# this pattern and 55 of them were SCORED AS GARMENT. That is the standing
# "this survey scores colliders as garments" defect, as one number.
#
# So the rule is COMPOSED -- the same one `seat_error_vs_author` arrived at
# independently: a shape is a proxy only when it BOTH carries a proxy token AND
# renders nothing. Either half alone is wrong. Rendering alone drops real
# garments textured from the plugin's alternate-texture list; the token alone
# drops `HDTSkirt` and `HDTBelt`, which are physics GARMENTS and render.
# `collar` stays exempt: `col` is a real token and a Collar is a real part.
PROXY = re.compile(r'^(colbody|collision|col_|virtual|virtualground|virtualbody)'
                   r'|collision|colbody', re.I)
# SUBSTRING, not anchored: `ButtCol` is a 597-vert unrendered collider and an
# anchored `^col` walked straight past it -- it scored 74.2% and sat fourth in
# the worst-offender table. Composing with "renders nothing" is what makes a
# substring safe: `Cylinder.015` carries Diffuse/Normal/EnvMap over 33k verts,
# so it is a real visible mesh and stays IN however its name reads.
PROXY_TOKEN = re.compile(r'col|hdt|virtual|proxy|occlusion|stabilizer|ground',
                         re.I)
BREAST = re.compile(r'breast', re.I)
BUTT = re.compile(r'butt', re.I)


def renders(s) -> bool:
    """A shape with no texture is a collision proxy or a helper, not garment."""
    return any(v for v in (s.textures or {}).values())


def is_proxy(s) -> bool:
    """Proxy = a proxy TOKEN in the name AND nothing rendered. Both signals."""
    nm = s.name or ""
    if "collar" in nm.lower():
        return False
    if PROXY.search(nm):
        return True
    return bool(PROXY_TOKEN.search(nm)) and not renders(s)


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
    btree = cKDTree(VB)
    rows = []
    skipped = collections.Counter()
    skipped_names = collections.defaultdict(set)
    frames = collections.Counter()
    for f in files:
        try:
            nf = pynifly.NifFile(filepath=f)
        except Exception:
            skipped["NIF would not open"] += 1
            continue
        rel = str(f).replace("\\", "/").split("/!UBE/", 1)[-1]
        # Per-vertex soft-bodies are SIMULATED by FSMP at runtime: their rendered
        # shape does not come from bone skinning, so posing them by skin weights
        # says nothing about what the player sees. Scored separately, never mixed
        # in with skin-driven armour.
        try:
            soft = nc._hdt_softbody_shape_names(Path(f), nif=nf)
        except Exception:
            soft = set()
        for s in nf.shapes:
            nm = s.name or ""
            if nm == "BaseShape":
                skipped["the injected body"] += 1
                continue
            if is_proxy(s):
                skipped["proxy: token AND renders nothing"] += 1
                skipped_names["proxy"].add(nm)
                continue
            try:
                V = np.asarray(s.verts, np.float64)
                m = len(V)
                if m < 200:
                    skipped["under 200 verts"] += 1
                    skipped_names["small"].add(nm)
                    continue
                g2s = nc._shape_global_to_skin(s)
                # THE FRAME IS CHOSEN, NOT REQUIRED TO BE IDENTITY. Demanding
                # identity here dropped 463 of 5939 candidate shapes -- 7.8% of
                # the pack -- and dropped them SILENTLY, with no line in the
                # report and no entry in `skipped`. 403 of them simply needed
                # the transform applying and are perfectly measurable.
                Vw, frame = sa.pick_frame(
                    V, nc._verts_skin_to_world(V, g2s), btree)
                frames[frame] += 1
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
                skipped["scoring raised"] += 1
                skipped_names["raised"].add(nm)
                continue
            else:
                if not out:
                    skipped["covers neither region at rest"] += 1
    return rows, skipped, skipped_names, frames


def main() -> int:
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 400
    bounce = float(sys.argv[sys.argv.index("--bounce") + 1]) if "--bounce" in sys.argv else 4.0
    lay_root = Path(os.environ.get("CBBE2UBE_OUT_ROOT", "")) if os.environ.get("CBBE2UBE_OUT_ROOT") else None
    root = lay_root or (paths.discover_layout().mods_root / "CBBEtoUBE Auto" / "meshes" / "!UBE")
    # BOTH weights. This globbed `*_1.nif` only, which is the exact defect
    # `output_nifs` exists to prevent -- weight 0 is a separately authored mesh,
    # not a scaled copy, and on one measured cuirass it clipped 9.48% against
    # weight 1's 4.52%. Half the shipped pack was invisible to this survey.
    allf = sa.output_nifs(root)
    files = allf[:lim] if lim else allf
    print(f"population: {len(allf)} NIF(s) under {root.name} (both weights, "
          f"no 1stperson)", flush=True)
    if len(files) < len(allf):
        print(f"  --limit {lim}: surveying the FIRST {len(files)} of them -- "
              f"this is a sample, not the pack", flush=True)
    print(f"surveying {len(files)} meshes at bounce {bounce}u\n", flush=True)
    rows, skipped, skipped_names, frames = survey(files, bounce)
    print("shapes NOT scored, by reason:")
    for why, k in skipped.most_common():
        ex = ""
        key = {"proxy: token AND renders nothing": "proxy",
               "under 200 verts": "small", "scoring raised": "raised"}.get(why)
        if key and skipped_names.get(key):
            ex = "   e.g. " + ", ".join(sorted(skipped_names[key])[:6])
        print(f"   {k:6d}  {why}{ex}")
    print(f"frame chosen per scored shape: "
          f"{dict(frames) if frames else 'none scored'}\n")
    if not rows:
        print("NOTHING WAS SCORED -- this is a broken run, not a clean pack.")
        return 2
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
