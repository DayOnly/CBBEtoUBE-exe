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

"""Morph the BODY and the GARMENT together, then measure the clip.

    python scripts/morph_clip_test.py <converted.nif> [--strength 1.0]
        [--slider Breast] [--band bust|butt] [--list]

WHY THIS EXISTS. Every clip number in this project is taken at BIND POSE, and
bind pose is not what ships: in game the body is morphed by the player's sliders.
A garment that clears the bind body perfectly can still be punched through by the
morphed one, and -- the case that motivated this -- a fix that RESTORES morph
following moves no vertex at all, so a bind-pose metric reads 0.000 change and
looks like nothing happened. Measured on three cuirasses whose breast follow went
0.0000 -> 0.23 with bind-pose clipping identical to four decimal places.

THE MODEL, and it mirrors how the game actually does it:

  * the BODY morphs from the UBE body's own slider data (`.osd`, or its sibling
    `.tri`) -- these are the BodySlide sliders RaceMenu drives at runtime;
  * the GARMENT morphs from ITS OWN `.tri`, by the SAME slider name. That is
    what makes armour track the body: BodySlide bakes matching morphs into the
    armour, and skee applies them through the BODYTRI reference.

So a garment fails to follow when its `.tri` has no morph for that slider, when
it has no `.tri` at all, or when something overrides its verts at runtime --
which is exactly what a GENERATED per-vertex soft-body does
(see #rigid-majority-softbody-gate).

WHAT IT DOES NOT MODEL: physics. Jiggle bones, SMP cloth and collision are
runtime-only. A skirt that swings clear in game will look clipped here, so read
this for FITTED armour and treat draping/simulated cloth with suspicion -- the
`chain%` column is printed so you can see which you are looking at.

CONTROLS, printed every run because a morph harness that silently morphs nothing
is the failure mode this project keeps hitting:
  * the body must actually MOVE (non-zero displacement) or the run aborts;
  * the garment's own displacement is reported beside it -- if it is 0.000 while
    the body moved, that IS the finding, not a broken harness.
"""
import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                    # noqa: E402
from scripts import standoff_audit as sa              # noqa: E402
import src.nif_convert as nc                          # noqa: E402
from src.body_zones import BREAST_Z, BUTT_Z, TORSO_HALF_X, REAR_Y  # noqa: E402

BANDS = {"bust": (BREAST_Z, True), "butt": (BUTT_Z, False)}


def _world(shape):
    return nc._verts_skin_to_world(np.asarray(shape.verts, np.float64),
                                   nc._shape_global_to_skin(shape))


def _sparse_to_dense(offsets, n):
    d = np.zeros((n, 3), np.float64)
    for idx, dx, dy, dz in offsets:
        if 0 <= idx < n:
            d[idx] = (dx, dy, dz)
    return d


def body_morphs(n_verts):
    """{slider: dense delta} for the UBE body, from its OSD."""
    osd_p = nc._find_ube_body_osd()
    if not osd_p:
        return {}, None
    osd = nc._cached_osd_load(Path(osd_p))
    out = {}
    for m in osd.morphs:
        d = _sparse_to_dense(m.offsets, n_verts)
        if np.abs(d).max() > 1e-6:
            out[m.name] = d
    return out, Path(osd_p).name


def garment_morphs(nif_path, shape_name, n_verts):
    """{slider: dense delta} for one garment shape, from its sibling .tri."""
    stem = Path(nif_path).stem
    for s in ("_0", "_1"):
        if stem.endswith(s):
            stem = stem[:-2]
    tri = Path(nif_path).parent / f"{stem}.tri"
    if not tri.is_file():
        return {}, None
    from src.tri import TriFile
    t = TriFile.load(tri)
    for sh in t.shapes:
        if sh.name != shape_name:
            continue
        return ({m.name: _sparse_to_dense(m.offsets, n_verts)
                 for m in sh.morphs}, tri.name)
    return {}, tri.name


def _match(slider, keys):
    """The garment names its morphs the same way the body does, but not
    always identically -- match case-insensitively, then by suffix."""
    low = slider.lower()
    for k in keys:
        if k.lower() == low:
            return k
    for k in keys:
        if k.lower().endswith(low) or low.endswith(k.lower()):
            return k
    return None


def _garment_parts(nf, p, hits_provider):
    """[(shape, bind verts, tris, per-slider morph table)] for rendered shapes."""
    out = []
    for s in nf.shapes:
        nm = s.name or ""
        if nm == "BaseShape" or nc._is_inline_body_name(nm):
            continue
        if int(getattr(s, "flags", 0) or 0) & 0x1:
            continue
        if not any(v for v in (s.textures or {}).values()):
            continue
        gV = _world(s)
        gT = np.asarray(s.tris, np.int64).reshape(-1, 3)
        gm, _tn = garment_morphs(p, nm, len(gV))
        out.append((nm, gV, gT, gm))
    return out


def _union(parts):
    V = np.concatenate([v for v, _t in parts])
    off, tl = 0, []
    for v, t in parts:
        tl.append(t + off)
        off += len(v)
    return V, np.concatenate(tl)


def _sweep(a, nf, bV, bT, bN, bm, p) -> int:
    """Score every slider that actually moves the band.

    Sliders are ranked by the clipping they COST, so the output is a list of
    suspects ordered by how much they matter -- not 202 numbers to read.
    `--min-body-move` drops sliders that barely touch the band: they cannot be
    responsible, and including them would bury the real ones in noise.
    """
    (zlo, zhi), front = BANDS[a.band]
    mask = ((bV[:, 2] >= zlo) & (bV[:, 2] <= zhi)
            & (np.abs(bV[:, 0]) < TORSO_HALF_X)
            & ((bV[:, 1] > 2) if front else (bV[:, 1] < REAR_Y)))
    idx = np.flatnonzero(mask)
    if len(idx) < 20:
        print(f"ABORT: {a.band} band has {len(idx)} verts", file=sys.stderr)
        return 3
    parts = _garment_parts(nf, p, None)
    if not parts:
        print("ABORT: no rendered garment shape", file=sys.stderr)
        return 3
    va = sa.vert_areas(bV, bT)
    gV0, gT0 = _union([(v, t) for _n, v, t, _m in parts])
    base = sa.ClipTester(gV0, gT0).report(bV, bT, bN, idx, va,
                                          oriented=True)["clipping_pct"]
    print(f"{a.band.upper()} band {len(idx)} verts   BIND clipping "
          f"{base:.3f}%   strength {a.strength}")
    print(f"scoring sliders that move the band by >= {a.min_body_move}u ...",
          flush=True)

    rows, skipped = [], 0
    for name, dB in bm.items():
        move = np.linalg.norm(dB[idx], axis=1)
        if move.max() < a.min_body_move:
            skipped += 1
            continue
        dBs = dB * float(a.strength)
        moved_parts, gmove = [], 0.0
        for _nm, gV, gT, gm in parts:
            mk = _match(name, gm.keys())
            dG = (gm[mk] * float(a.strength) if mk is not None
                  else np.zeros_like(gV))
            gmove = max(gmove, float(np.linalg.norm(dG, axis=1).max()))
            moved_parts.append((gV + dG, gT))
        gVm, gTm = _union(moved_parts)
        r = sa.ClipTester(gVm, gTm).report(bV + dBs, bT, bN, idx,
                                           sa.vert_areas(bV + dBs, bT),
                                           oriented=True)
        rows.append({"slider": name, "clip": r["clipping_pct"],
                     "d": r["clipping_pct"] - base,
                     "buried": r["clip_buried_pct"],
                     "bmove": float(np.median(move)), "gmove": gmove})
    if not rows:
        print("ABORT: no slider moved the band -- nothing was measured",
              file=sys.stderr)
        return 3
    rows.sort(key=lambda r: -r["d"])
    print(f"{len(rows)} sliders scored ({skipped} skipped as too small)\n")
    print(f"{'slider':<42}{'clip%':>8}{'delta':>8}{'buried':>8}"
          f"{'body':>7}{'garment':>8}{'follow':>8}")
    print("-" * 89)
    for r in rows[:20]:
        fol = (r["gmove"] / r["bmove"]) if r["bmove"] > 1e-6 else float("nan")
        print(f"{r['slider'][:41]:<42}{r['clip']:>8.3f}{r['d']:>+8.3f}"
              f"{r['buried']:>8.3f}{r['bmove']:>7.3f}{r['gmove']:>8.3f}"
              f"{fol:>8.2f}")
    worst = rows[0]
    nofollow = [r for r in rows if r["gmove"] < 1e-4]
    print(f"\nworst: {worst['slider']}  {worst['d']:+.3f} points")
    print(f"sliders the garment does NOT follow at all (0.000u): "
          f"{len(nofollow)} of {len(rows)}")
    for r in nofollow[:8]:
        print(f"   {r['d']:+7.3f}  {r['slider']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nif")
    ap.add_argument("--slider", default="breast",
                    help="substring of the slider name (default: breast)")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--band", default="bust", choices=sorted(BANDS))
    ap.add_argument("--list", action="store_true",
                    help="list the body sliders that match and exit")
    ap.add_argument("--sweep", action="store_true",
                    help="score EVERY slider that moves the band, ranked by the "
                         "clipping it costs")
    ap.add_argument("--min-body-move", type=float, default=0.15,
                    help="sweep only sliders that move the band at least this "
                         "much (default 0.15u) -- a slider that barely moves "
                         "the band cannot be responsible for a clip")
    a = ap.parse_args()

    p = Path(a.nif)
    nf = nc._pynifly().NifFile(filepath=str(p))
    body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if body is None:
        print("ABORT: no injected BaseShape -- phase-1 piece, nothing to morph "
              "against", file=sys.stderr)
        return 3
    bV = _world(body)
    bT = np.asarray(body.tris, np.int64).reshape(-1, 3)
    bN = np.asarray(body.normals, np.float64)
    bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)

    bm, osd_name = body_morphs(len(bV))
    if not bm:
        print("ABORT: no body OSD resolved -- cannot morph the body",
              file=sys.stderr)
        return 3
    if a.sweep:
        return _sweep(a, nf, bV, bT, bN, bm, p)
    hits = [k for k in bm if a.slider.lower() in k.lower()]
    if a.list or not hits:
        print(f"body slider source: {osd_name}   {len(bm)} sliders")
        for k in sorted(hits or bm)[:40]:
            print(f"   {k}")
        return 0 if a.list else 3

    # --- morph the body ---
    dB = np.zeros_like(bV)
    for k in hits:
        dB += bm[k]
    dB *= float(a.strength)
    moved_b = np.linalg.norm(dB, axis=1)
    if moved_b.max() < 1e-4:
        print("ABORT: the chosen sliders move the body by 0.000u -- the run "
              "would measure nothing", file=sys.stderr)
        return 3

    # --- morph each rendered garment shape from its OWN tri ---
    parts_bind, parts_morph, report = [], [], []
    for s in nf.shapes:
        nm = s.name or ""
        if nm == "BaseShape" or nc._is_inline_body_name(nm):
            continue
        if int(getattr(s, "flags", 0) or 0) & 0x1:
            continue
        if not any(v for v in (s.textures or {}).values()):
            continue
        gV = _world(s)
        gT = np.asarray(s.tris, np.int64).reshape(-1, 3)
        gm, tri_name = garment_morphs(p, nm, len(gV))
        dG = np.zeros_like(gV)
        used = []
        for k in hits:
            mk = _match(k, gm.keys())
            if mk is not None:
                dG += gm[mk]
                used.append(mk)
        dG *= float(a.strength)
        parts_bind.append((gV, gT))
        parts_morph.append((gV + dG, gT))
        n = len(gV)
        vw = [dict() for _ in range(n)]
        for b, prs in (s.bone_weights or {}).items():
            for vi, w in prs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        chain = sum(nc._chain_vert_mask(vw, n)) / max(n, 1)
        report.append((nm, len(gV), tri_name, len(used),
                       float(np.linalg.norm(dG, axis=1).max()), chain))

    if not parts_bind:
        print("ABORT: no rendered garment shape", file=sys.stderr)
        return 3

    def _union(parts):
        V = np.concatenate([v for v, _t in parts])
        off, tl = 0, []
        for v, t in parts:
            tl.append(t + off)
            off += len(v)
        return V, np.concatenate(tl)

    (zlo, zhi), front = BANDS[a.band]
    mask = ((bV[:, 2] >= zlo) & (bV[:, 2] <= zhi)
            & (np.abs(bV[:, 0]) < TORSO_HALF_X)
            & ((bV[:, 1] > 2) if front else (bV[:, 1] < REAR_Y)))
    idx = np.flatnonzero(mask)
    if len(idx) < 20:
        print(f"ABORT: {a.band} band has {len(idx)} verts", file=sys.stderr)
        return 3

    print(f"body sliders : {', '.join(hits)}  (strength {a.strength})")
    print(f"body moves   : max {moved_b.max():.3f}u   "
          f"median-in-band {np.median(moved_b[idx]):.3f}u")
    print(f"{'garment shape':<24}{'verts':>7}{'tri':>16}{'morphs':>8}"
          f"{'max move':>10}{'chain%':>8}")
    for nm, nv, tn, nu, mx, ch in report:
        print(f"  {nm[:22]:<22}{nv:>7}{(tn or '-'):>16}{nu:>8}{mx:>10.3f}"
              f"{100 * ch:>7.1f}%")

    rows = []
    for lbl, parts, body_v in (("BIND ", parts_bind, bV),
                               ("MORPH", parts_morph, bV + dB)):
        gV, gT = _union(parts)
        r = sa.ClipTester(gV, gT).report(body_v, bT, bN, idx,
                                         sa.vert_areas(body_v, bT),
                                         oriented=True)
        rows.append((lbl, r))
    print(f"\n{a.band.upper()} band, {len(idx)} verts")
    print(f"{'':<7}{'clip%':>9}{'coinc':>8}{'shal':>8}{'buried':>8}"
          f"{'cover%':>9}")
    for lbl, r in rows:
        print(f"{lbl:<7}{r['clipping_pct']:>9.3f}{r['clip_coincident_pct']:>8.3f}"
              f"{r['clip_shallow_pct']:>8.3f}{r['clip_buried_pct']:>8.3f}"
              f"{r['covered_pct']:>9.2f}")
    d = rows[1][1]["clipping_pct"] - rows[0][1]["clipping_pct"]
    print(f"{'delta':<7}{d:>+9.3f}")
    if all(r[4] < 1e-4 for r in report):
        print("\nNOTE: every garment shape moved 0.000u while the body moved. "
              "That is the FOLLOW defect, not a broken harness -- the garment "
              "has no matching morph, or none at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
