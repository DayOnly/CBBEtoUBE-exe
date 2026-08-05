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

"""Does `#coherence-repair` over-fire? Same-build flag A/B, with the
counter-metric.

`CBBE2UBE_NO_COHERENCE_REPAIR=1` is the OFF arm, default is ON. Comparing fresh
output against the LIVE PACK instead would be void: the deployed pack predates
the branch, and that exact mistake once showed an untouched control shape
moving 918 verts. Only a same-build flag A/B isolates one change.

FOUR questions, because "it fixed the crumple" alone is not a verdict:

  1. EFFICACY      -- do qualifying patches go away?
  2. OVER-FIRE (a) -- are any triangles NEWLY crumpled in the ON arm?
  3. OVER-FIRE (b) -- does it move shapes that had NO qualifying patch?
                      A repair that edits clean geometry is a warp damper
                      wearing a different hat.
  4. COUNTER-METRIC-- CLIPPING and STANDOFF against the UBE body. A geometry
                      win that pushes skin through is a regression that every
                      geometry metric calls an improvement. THIS IS THE ONE
                      THAT FOUND A COST; an earlier version of this script
                      promised it in the docstring and did not implement it.

CAUTION on (b): the shipped repair gates THIN strips on the coherence DROP
(`COHERENCE_THIN_DROP`), while the census metric requires an absolute
`co <= OUT_SCATTERED`. The repair therefore legitimately touches geometry this
detector cannot see. Movement on a "census-clean" shape inside a crumpled piece
is that widening, NOT proven over-fire. The meaningful control is movement on
pieces that are clean ALL OVER -- that must be zero.

CONTROLS (all ABORT):
  * POSITIVE -- the probe MUST show patches in the OFF arm, or the flag is not
    reaching the conversion.
  * MUST-NOT-MOVE -- sampled fully-clean pieces must show 0 verts moved.
  * SAMPLE SIZE -- a census that scores a handful of pieces and reports "0
    over-fire" is measuring nothing. An earlier run scored 1 of 18 and still
    printed a passing control.

RESULT 2026-08-05: patches 27->5, area 474.0->44.9 (-90.5%), 2 newly crumpled
triangles, 0 verts moved on all 8 fully-clean pieces. Counter-metric: clipping
3619->3694 (+75) with 9 of 14 pieces worse, and +60 of the +75 on ONE piece;
standoff median delta mean +0.005u, i.e. genuinely mean-preserving. A trade,
not a free win.

Usage:
  python scripts/analysis/coherence_repair_ab.py --baseline crumple_baseline.json
      [--probe <substr>] [--hits N] [--clean N]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.analysis import _census_common as C     # noqa: E402
import numpy as np                                   # noqa: E402
from pyn import pynifly                              # noqa: E402
from src import discovery, fit_metrics as fm         # noqa: E402

_REPO = Path(__file__).resolve().parent.parent.parent


def shapes_of(p):
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        v = np.asarray(s.verts, float)
        t = s.transform
        R = np.asarray(t.rotation, float).reshape(3, 3)
        M = R * float(getattr(t, "scale", 1.0))
        if abs(np.linalg.det(M)) > 1e-9:
            v = v @ M.T + np.asarray(t.translation, float)
        out[s.name] = (v, np.asarray(s.tris))
    return out


def body_ref():
    """UBE body verts + normals. A source's inline body is often useless here --
    decimated, or shipping ZERO normals -- so this uses the real UBE body."""
    mods, _p, ube = C.layout()
    for cand in sorted((mods).glob("*/meshes/!UBE/Body/femalebody_tangent_1.nif")):
        nf = pynifly.NifFile(filepath=str(cand))
        for s in nf.shapes:
            if s.name.casefold() in C.BODY_SHAPE_NAMES and s.normals is not None:
                return (np.asarray(s.verts, float),
                        np.asarray(s.normals, float))
    raise SystemExit("no UBE body reference with normals found")


def garment(p):
    """Concatenated non-body geometry, transforms baked."""
    V, T = [], []
    for n, (v, t) in shapes_of(p).items():
        if n.casefold() in C.BODY_SHAPE_NAMES:
            continue
        T.append(np.asarray(t).reshape(-1, 3) + sum(len(x) for x in V))
        V.append(v)
    return (np.concatenate(V), np.concatenate(T)) if V else (None, None)


def convert(mods, w, stem, outdir, env_extra):
    sub = str(Path(*Path(str(w.relative_path)).parts[1:-1]))
    env = dict(os.environ)
    env.update(env_extra)
    r = subprocess.run([sys.executable, "scripts/convert_one_armor.py",
                        str(mods / w.provider_mod), sub, stem, str(outdir)],
                       cwd=str(_REPO), env=env, capture_output=True, text=True)
    return r.returncode, outdir / "meshes" / "!UBE" / sub, r.stdout + r.stderr


def main() -> int:
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    base = json.load(open(opt("--baseline", "crumple_baseline.json")))
    probe = opt("--probe", "orcish")
    n_hits, n_clean = int(opt("--hits", "10")), int(opt("--clean", "8"))

    mods, prof, ube = C.layout()
    tmp = Path(os.environ.get("CBBE2UBE_AB_DIR", _REPO / "_ab_coherence"))
    # Wide prefixes: the DEFAULTS cover only armor/clothes/DLC/CC, and a run
    # that used them dropped 11 of 18 sampled pieces as "no winning source",
    # leaving the whole result resting on the probe alone.
    wins = discovery.find_winning_nifs(mods, prof,
                                       skip_mods=(ube.parent.parent.name,),
                                       path_prefixes=("meshes\\",),
                                       classify=False)
    by_rel = {str(C.meshes_rel(w.relative_path)).lower(): w for w in wins}

    hits = sorted(base["hits"],
                  key=lambda h: -max(p["area"] for p in h["patches"]))
    probe_hits = [h for h in hits if probe in h["piece"].lower()]
    rest = [h for h in hits if probe not in h["piece"].lower()]
    # Offer a POOL several times the target: pieces drop for reasons unrelated
    # to the repair (biped slots unresolvable), and the run must still reach a
    # usable N.
    pool_h = (probe_hits + rest)[:n_hits * 6]
    clean = base["clean"]
    pool_c = clean[::max(1, len(clean) // (n_clean * 6))][:n_clean * 6]
    print(f"baseline: {len(base['hits'])} hits, {len(base['clean'])} clean")

    drops = {"no_source": [], "slots0": [], "convert_err": [], "read": []}
    got = {"HIT": 0, "CLEAN": 0}
    want = {"HIT": n_hits, "CLEAN": n_clean}
    rows, pairs = [], []
    for kind, piece in ([("HIT", h["piece"]) for h in pool_h]
                        + [("CLEAN", c) for c in pool_c]):
        if got[kind] >= want[kind]:
            continue
        w = by_rel.get(piece.lower())
        if w is None:
            drops["no_source"].append(piece)
            continue
        stem = Path(piece).stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        off = convert(mods, w, stem, tmp / "off",
                      {"CBBE2UBE_NO_COHERENCE_REPAIR": "1"})
        on = convert(mods, w, stem, tmp / "on", {})
        if off[0] or on[0]:
            # The harness REFUSES a slots=0 run on purpose: it silently skips
            # every slot-gated pass. Counted, never silently dropped.
            drops["slots0" if "biped slots resolved to 0" in (off[2] + on[2])
                  else "convert_err"].append(piece)
            continue
        fn = Path(piece).name
        try:
            soff, son = shapes_of(off[1] / fn), shapes_of(on[1] / fn)
            ssrc = shapes_of(Path(w.source_path))
        except Exception as e:
            drops["read"].append(f"{piece}: {e}")
            continue
        p_off = p_on = newly = moved_clean = 0
        a_off = a_on = 0.0
        for n in sorted(set(soff) & set(son) & set(ssrc)):
            if n.casefold() in C.BODY_SHAPE_NAMES:
                continue
            sv, st = ssrc[n]
            vo, vn = soff[n][0], son[n][0]
            if not (len(sv) == len(vo) == len(vn)):
                continue
            fo, to_ = C.coherence_patches(sv, vo, st)
            fn_, tn = C.coherence_patches(sv, vn, st)
            p_off, p_on = p_off + len(fo), p_on + len(fn_)
            a_off += sum(f["area"] for f in fo)
            a_on += sum(f["area"] for f in fn_)
            newly += len(tn - to_)
            if not fo:
                moved_clean += int(
                    (np.linalg.norm(vo - vn, axis=1) > 1e-4).sum())
        rows.append((kind, piece, p_off, p_on, a_off, a_on, newly, moved_clean))
        pairs.append((piece, off[1] / fn, on[1] / fn))
        got[kind] += 1
        print(f"  [{kind:5}] {piece[:44]:44} patches {p_off:3}->{p_on:3} "
              f"area {a_off:7.1f}->{a_on:7.1f} newly {newly:4} "
              f"moved-on-clean {moved_clean:6}")

    print("\n" + "=" * 78)
    print("SAMPLE ACCOUNTING -- the population this census can SEE")
    print(f"  offered: {len(pool_h)} hit candidates + {len(pool_c)} clean")
    print(f"  SCORED : {got['HIT']} hits + {got['CLEAN']} clean")
    for k, v in drops.items():
        print(f"  dropped [{k:12}]: {len(v)}")

    print(f"\n  qualifying patches OFF {sum(r[2] for r in rows)} -> "
          f"ON {sum(r[3] for r in rows)}")
    print(f"  patch area         OFF {sum(r[4] for r in rows):.1f} -> "
          f"ON {sum(r[5] for r in rows):.1f}")
    print(f"  NEWLY crumpled tris (over-fire a): {sum(r[6] for r in rows)}")
    print(f"  verts moved on CLEAN-SAMPLED pieces (over-fire b): "
          f"{sum(r[7] for r in rows if r[0] == 'CLEAN')}")

    # ---- 4. THE COUNTER-METRIC ------------------------------------------
    bV, bN = body_ref()
    print(f"\nCOUNTER-METRIC (clipping + standoff vs the UBE body, "
          f"{len(bV)} verts):")
    c_rows = []
    for piece, po, pn in pairs:
        vo, to_ = garment(po)
        vn, tn = garment(pn)
        if vo is None or vn is None or vo.shape != vn.shape:
            continue
        if not (np.linalg.norm(vo - vn, axis=1) > 1e-4).any():
            continue                    # repair did nothing here
        zs = np.concatenate([vo[:, 2], vn[:, 2]])
        band = np.where((bV[:, 2] >= zs.min()) & (bV[:, 2] <= zs.max()))[0]
        if len(band) < 200:
            print(f"  {piece[:44]:44} VOID: {len(band)} body verts in band")
            continue
        got_ = []
        for V, T in ((vo, to_), (vn, tn)):
            mask, _ = fm._ClipTester(V, T).clipping(bV, bN, band, oriented=True)
            so = np.asarray(fm.standoff(bV, bN, V, T, band), float)
            got_.append((int(np.asarray(mask).sum()), so[np.isfinite(so)]))
        (co_, so_), (cn_, sn_) = got_
        if len(so_) < 100 or len(sn_) < 100:
            print(f"  {piece[:44]:44} VOID: {len(so_)}/{len(sn_)} covered")
            continue
        c_rows.append((piece, co_, cn_, float(np.median(so_)),
                       float(np.median(sn_))))
        print(f"  {piece[:44]:44} clipN {co_:5}->{cn_:5} ({cn_-co_:+4})  "
              f"standoff med {np.median(so_):6.3f}->{np.median(sn_):6.3f}"
              f"{'   <== CLIPPING REGRESSION' if cn_ > co_ else ''}")
    if c_rows:
        t_off = sum(r[1] for r in c_rows)
        t_on = sum(r[2] for r in c_rows)
        worse = [r for r in c_rows if r[2] > r[1]]
        ds = [r[4] - r[3] for r in c_rows]
        print(f"\n  clipping verts OFF {t_off} -> ON {t_on} ({t_on-t_off:+})")
        print(f"  pieces WORSE on clipping: {len(worse)} of {len(c_rows)}")
        print(f"  standoff median delta: mean {np.mean(ds):+.4f}u  "
              f"min {min(ds):+.4f}  max {max(ds):+.4f}")

    assert got["HIT"] >= 6 and got["CLEAN"] >= 5, (
        f"SAMPLE TOO SMALL: scored {got['HIT']} hits / {got['CLEAN']} clean. "
        f"A census reporting 0 over-fire from a sample this size is measuring "
        f"nothing. Widen the pool or resolve the drops.")
    pr = [r for r in rows if probe in r[1].lower()]
    assert pr and pr[0][2] > 0, (
        "POSITIVE CONTROL FAILED: the probe has NO qualifying patch in the OFF "
        "arm -- CBBE2UBE_NO_COHERENCE_REPAIR is not reaching the conversion, "
        "so every 'no change' above is meaningless")
    assert sum(r[7] for r in rows if r[0] == "CLEAN") == 0, (
        "MUST-NOT-MOVE CONTROL FAILED: the repair moved geometry on a piece "
        "with no crumple anywhere")
    print(f"\ncontrols OK: probe {pr[0][2]} patches OFF -> {pr[0][3]} ON; "
          f"0 verts moved on fully-clean pieces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
