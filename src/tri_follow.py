"""#tri-follow: make the shared .tri's cloth rows follow the skin at BOTH weights and across each cloth face.

The armour's .tri is generated once, from the `_0` geometry (`_tri_is_owning_variant`), and the same rows drive the
`_1` mesh. #match-idw-floor keeps a hugging cloth vert from moving outward less than the skin it covers -- at `_0`.
At `_1` each cloth vert covers other skin: measured on the Akaviri monk cuirass (12 strongest sliders, cloth within
4u of the skin) the lag is 6.3 at `_0` and 1831 at `_1`. And a cloth TRIANGLE is flat: with every corner exact,
skin bulging under its face still rises through it (the chord), which is almost all of the poke under presets.

Two post-passes on the finished .tri, once both weights are on disk (#pair-weight-parity's hook):
  #tri-follow-floor (CBBE2UBE_NO_TRI_FOLLOW_FLOOR=1 off): the generator's floor measured again at the `_1` geometry --
      per slider, a hugging vert whose row moves less along the skin normal than the covered surface point or the
      most outward-moving corner of the covered triangle is lifted (outward only, by the stand-off match weight).
  #tri-follow-chord (CBBE2UBE_NO_TRI_FOLLOW_CHORD=1 off): per slider, a corner lift L so each skin point that sits inside
      a cloth face at rest (gap g >= 0) is cleared at +1 (L >= rise - have - g + M) and the face does not dig in at -1
      (L <= rise - have + g - M) -- two-way safe whenever g >= 0. Skin already outside the cloth at rest is left alone.
  #tri-follow-edge (CBBE2UBE_NO_TRI_FOLLOW_EDGE=1 off): an open cloth EDGE near the skin moves exactly as the skin under it --
      every slider, both signs (row = the body's delta at the edge vert's foot point, by the stand-off weight) -- so
      the edge rides the skin instead of sinking into it: the job #zap-edge-lift does by lifting edges at rest.
None of them moves a vertex at rest; both leave nipple-local sliders, colliders, hidden and simulated shapes alone.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .envflags import flag as _flag, knob as _knob

# DEFAULT ON since 2026-11-02 (each has its CBBE2UBE_NO_TRI_FOLLOW_* off switch). 34 pieces replayed from their
# pre-zap state, all three with #zap-edge-lift on, vs 4db7657: worst-camera holes -13.8% (25 better / 8 worse), cloth
# edges buried under sliders -10.1% (22 / 8), slider-caused poke -6.0% (19 / 15), gap skin +1.0% (23 / 11 by piece).
TRI_FOLLOW_FLOOR = not _flag("CBBE2UBE_NO_TRI_FOLLOW_FLOOR", False)
TRI_FOLLOW_CHORD = not _flag("CBBE2UBE_NO_TRI_FOLLOW_CHORD", False)
TRI_FOLLOW_EDGE = not _flag("CBBE2UBE_NO_TRI_FOLLOW_EDGE", False)
TRI_FOLLOW_NEAR = 4.0      # = sliderset_gen._MATCH_NEAR: full match at or under this stand-off
TRI_FOLLOW_FAR = 10.0      # = sliderset_gen._MATCH_FAR: drape cloth beyond keeps its rows
TRI_FOLLOW_CAST = 6.0      # skin points under a cloth face: cast along the skin normal this far
TRI_FOLLOW_CHORD_M = _knob("CBBE2UBE_TRI_FOLLOW_CHORD_M", 0.05)


def _nipple_slider(name: str) -> bool:
    n = name.lower()
    return "nipple" in n or "areola" in n


def _dense(morph, n: int) -> np.ndarray:
    D = np.zeros((n, 3))
    if morph is None:
        return D
    i = np.asarray(morph.idx, np.int64); ok = (i >= 0) & (i < n)
    D[i[ok]] = np.asarray(morph.delta, np.float64)[ok]
    return D


def _set_row(ts, by_name, name, new) -> None:
    from .tri import TriMorph
    keep = np.flatnonzero(np.linalg.norm(new, axis=1) > 1e-7)
    gm = by_name.get(name.lower())
    if gm is None:
        ts.morphs.append(TriMorph(name, keep, new[keep])); by_name[name.lower()] = ts.morphs[-1]
    else:
        gm.idx, gm.delta = keep, new[keep]


def follow(nif_path, tf, mode: str, collider_names=()) -> int:
    """Apply one #tri-follow mode ("floor" or "chord") to the cloth rows of `tf` at the geometry of `nif_path`
    (its injected body and cloth). Returns the number of (shape, slider) rows changed; `tf` is edited in place."""
    from . import body_zap as bz
    from . import nif_convert as nc
    from . import sliderset_gen as sg
    from . import fit_metrics as fm
    nf = nc._pynifly().NifFile(filepath=str(nif_path))
    body = next((s for s in nf.shapes if s.name == bz.BODY_NAME), None)
    if body is None:
        return 0
    w_ = "_0" if str(nif_path)[-6:-4] == "_0" else "_1"
    full, _ = bz.full_body_tris(w_)
    full = np.asarray(full, np.int64)
    BV = np.asarray(body.verts, np.float64)
    if len(BV) <= int(full.max()):
        return 0
    tri = {s.name: s for s in tf.shapes}
    bt = tri.get(bz.BODY_NAME)
    if bt is None:
        return 0
    fn = np.cross(BV[full[:, 1]] - BV[full[:, 0]], BV[full[:, 2]] - BV[full[:, 0]])
    sgn = 1.0 if ((BV[full].mean(1) - BV.mean(0)) * fn).sum(1).mean() >= 0 else -1.0
    bn = np.zeros_like(BV)
    for k in range(3):
        np.add.at(bn, full[:, k], fn)
    bn = sgn * bn / (np.linalg.norm(bn, axis=1, keepdims=True) + 1e-12)
    sliders = [m for m in bt.morphs if len(m.idx) and not _nipple_slider(m.name)]
    Dbody = {m.name: _dense(m, len(BV)) for m in sliders}
    changed = 0
    for s in nf.shapes:
        ts = tri.get(s.name)
        if (ts is None or s.name == bz.BODY_NAME or s.name in nc.UBE_BODY_INJECT_NAMES or s.name in collider_names
                or not len(s.tris)):
            continue
        try:
            if int(s.flags) & 1:
                continue
        except Exception:
            pass
        V = np.asarray(s.verts, np.float64); T = np.asarray(s.tris, np.int64).reshape(-1, 3)
        corners, bary = sg._closest_surface_bary(V, BV, full)
        foot = (BV[corners] * bary[:, :, None]).sum(1)
        d = np.linalg.norm(V - foot, axis=1)
        w = np.clip((TRI_FOLLOW_FAR - d) / (TRI_FOLLOW_FAR - TRI_FOLLOW_NEAR), 0.0, 1.0)
        hug = np.flatnonzero(w > 0)
        if not len(hug):
            continue
        vn = (bn[corners] * bary[:, :, None]).sum(1); vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12
        by_name = {m.name.lower(): m for m in ts.morphs}
        if mode == "edge":
            from . import body_zap as _bz
            emask, _ = _bz._open_edge_verts(V, T)
            on = emask & (w > 0)
            if not on.any():
                continue
            for bm in sliders:
                Db = Dbody[bm.name]
                Dc = _dense(by_name.get(bm.name.lower()), len(V))
                exact = (Db[corners] * bary[:, :, None]).sum(1)
                new = Dc.copy()
                new[on] = w[on, None] * exact[on] + (1 - w[on, None]) * Dc[on]
                if np.allclose(new, Dc, atol=1e-6):
                    continue
                _set_row(ts, by_name, bm.name, new)
                changed += 1
            continue
        if mode == "floor":
            md = np.linalg.norm(V[:, None, :] - BV[corners], axis=2).min(1)      # stand-off to the covered corners
            mw = np.clip((TRI_FOLLOW_FAR - md) / (TRI_FOLLOW_FAR - TRI_FOLLOW_NEAR), 0.0, 1.0)
            for bm in sliders:
                Db = Dbody[bm.name]
                Dc = _dense(by_name.get(bm.name.lower()), len(V))
                cov = np.einsum("ij,ij->i", (Db[corners] * bary[:, :, None]).sum(1), vn)
                cmax = np.einsum("ikj,ij->ik", Db[corners], vn).max(1)
                have = np.einsum("ij,ij->i", Dc, vn)
                short = np.clip(np.maximum(cov, cmax) - have, 0.0, None) * mw
                if short.max() < 1e-4:
                    continue
                _set_row(ts, by_name, bm.name, Dc + short[:, None] * vn)
                changed += 1
            continue
        if mode != "chord":
            raise ValueError(f"tri_follow: unknown mode {mode!r}")
        # skin points under this shape's faces (cast along their outward normal) and their rest gap
        tst = fm._ClipTester(V, T, tmax=TRI_FOLLOW_CAST + 0.5)
        O = BV - 0.5 * bn
        ri, ti = tst._pairs_segment(O, bn)
        t, who = tst._cast(O, bn, ri, ti, len(O), want_tri=True)
        hit = np.flatnonzero(np.isfinite(t))
        if not len(hit):
            continue
        P = O[hit] + t[hit, None] * bn[hit]; ti_ = T[who[hit]]
        a, b_, c = V[ti_[:, 0]], V[ti_[:, 1]], V[ti_[:, 2]]
        v0, v1, v2 = b_ - a, c - a, P - a
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        den = d00 * d11 - d01 * d01; den[np.abs(den) < 1e-12] = 1e-12
        wb = (d11 * d20 - d01 * d21) / den; wc = (d00 * d21 - d01 * d20) / den
        pb = np.clip(np.stack([1.0 - wb - wc, wb, wc], 1), 0.0, 1.0)
        g = t[hit] - 0.5
        inside = g >= 0.0
        if not inside.any():
            continue
        hit, ti_, pb, g = hit[inside], ti_[inside], pb[inside], g[inside]
        is_hug = np.zeros(len(V), bool); is_hug[hug] = True
        M = float(TRI_FOLLOW_CHORD_M)
        for bm in sliders:
            Db = Dbody[bm.name]
            Dc = _dense(by_name.get(bm.name.lower()), len(V))
            rise = np.einsum("ij,ij->i", Db[hit], bn[hit])
            have = np.einsum("ij,ij->i", np.einsum("ik,ikj->ij", pb, Dc[ti_]), bn[hit])
            lo = rise - have - g + M; hi = rise - have + g - M
            if lo.max() <= 0 and hi.min() >= 0:
                continue
            Lp = np.zeros(len(V)); Ln = np.zeros(len(V))
            LOm = np.full(len(V), -np.inf); HIm = np.full(len(V), np.inf)
            for k in range(3):
                np.maximum.at(Lp, ti_[:, k], np.clip(lo, 0, None))
                np.minimum.at(Ln, ti_[:, k], np.clip(hi, None, 0))
                np.maximum.at(LOm, ti_[:, k], lo)
                np.minimum.at(HIm, ti_[:, k], hi)
            L = np.where(Lp >= -Ln, Lp, Ln)
            with np.errstate(invalid="ignore"):
                L = np.where(LOm <= HIm, np.clip(L, LOm, HIm), 0.5 * (LOm + HIm))
            L = np.where(np.isfinite(L), L, 0.0)
            L[~is_hug] = 0.0
            if np.abs(L).max() < 1e-4:
                continue
            _set_row(ts, by_name, bm.name, Dc + (w * L)[:, None] * vn)
            changed += 1
    return changed


def follow_pair(dst_path) -> "dict | None":
    """#tri-follow on a written weight pair: the floor at `_1`, then exact edges and the chord lift at `_0`, saved into the shared
    .tri. None when neither mode is on or the pair / .tri / injected body is missing."""
    if not (TRI_FOLLOW_FLOOR or TRI_FOLLOW_CHORD or TRI_FOLLOW_EDGE):
        return None
    from . import body_zap as bz
    from . import nif_convert as nc
    from .tri import TriFile
    files = bz._pair_paths(Path(dst_path))
    if not files:
        return None
    p0 = files.get("_0"); p1 = files.get("_1") or files.get("")
    stem = Path(dst_path).stem
    stem = stem[:-2] if stem[-2:] in ("_0", "_1") else stem
    tri_path = Path(dst_path).with_name(stem + ".tri")
    if not tri_path.is_file():
        return None
    tf = TriFile.load(tri_path)
    coll = set()
    for p in (x for x in (p0, p1) if x is not None):
        try:
            coll |= set(nc._hdt_collider_shape_names(Path(p), stem_scan=nc._dst_xml_stem_scan()))
        except Exception:
            pass
    out = {}
    if TRI_FOLLOW_FLOOR and p1 is not None:
        out["floor_rows"] = follow(p1, tf, "floor", coll)
    if TRI_FOLLOW_EDGE:
        out["edge_rows"] = follow(p0 if p0 is not None else p1, tf, "edge", coll)
    if TRI_FOLLOW_CHORD:
        out["chord_rows"] = follow(p0 if p0 is not None else p1, tf, "chord", coll)
    if any(out.values()):
        tf.save(tri_path)
    return out
