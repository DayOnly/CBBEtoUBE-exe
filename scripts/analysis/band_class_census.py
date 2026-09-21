"""PACK CENSUS on the MORPHED clip, for ANY band, across BOTH convert paths.

WHY THIS EXISTS. The BUG-15 class census scored `--band bust` and excluded the
280 copy-path pieces as "out of scope". `morph_clip_test.py` itself does NOT
need that exclusion -- it falls back to the UBE template body when a piece has
no injected `BaseShape` -- so the exclusion was a choice, not a capability
limit. This runs the SAME metric over BOTH paths and over any band.

IT IS NOT A SECOND METRIC. Every number is produced by calling
`morph_clip_test`'s own helpers (`_aligned`, `body_morphs`, `garment_morphs`,
`_resolve_preset`, `_match`, `BANDS`) and `standoff_audit.ClipTester` in the
same order `main()` calls them. The only thing added is a loop and the
per-piece bookkeeping. `--control` asserts it reproduces the CLI on known arms
before any census number is believed.

POPULATION DISCIPLINE: every piece that is not scored is counted under a named
reason and printed. A census that cannot say why it looked at N and scored M
cannot tell "nothing is wrong" from "I measured nothing".

WEIGHTS: `_1` only -- a DECLARED BLIND SPOT, not a correct scoping, and the one
exclusion the discipline above never counted. This widened the census across
BOTH convert PATHS; it never widened it across both WEIGHTS. It scores per-file
clip area, and a `_0` mesh is separately authored: `standoff_audit.output_nifs`
records bust-front clipping at 4.52% on weight 1 against 9.48% on weight 0 --
the very metric `--band bust` measures here.

DO NOT fix it by swapping the glob. Three things are pinned to weight 1, and each
would turn `_0` rows into believable wrong numbers:
  1. the COPY-PATH template body is `nc._find_ube_femalebody("_1")` -- and the
     copy path is most of the pack;
  2. the preset comes from `morph_clip_test._load_preset`, which reads the
     BodySlide `big` (weight-100) side only -- a `_0` mesh would be morphed with
     slider values that never ship on it;
  3. `--every N` strides a SORTED list (`cands[::a.every]`). With both weights
     `foo_0` sorts beside `foo_1`, so any EVEN stride silently becomes a
     weight-0-ONLY census. Measured on the shipped pack (1032 garments, every
     one with both weights): `--every 2` picks 1032 files, 100.0% of them
     `_0`; `--every 4` the same; `--every 3` splits 50/50. `acceptance.py`
     passes this stride straight through from `CBBE2UBE_CLIP_EVERY`.
It also feeds FIVE rows of `acceptance.py` (morph/bind clip shares and
percentiles), so widening re-baselines all five with nothing to check them by.

To close it: resolve the template and the preset side from each file's own
weight suffix, stride by garment PAIR rather than by file, then widen, then
re-baseline the acceptance rows in the same change with an old-vs-new A/B.
"""
import argparse
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src import paths                                          # noqa: E402
paths.export_to_env(paths.discover_layout())

import numpy as np                                             # noqa: E402
from scripts.analysis import standoff_audit as sa              # noqa: E402
from scripts.analysis import morph_clip_test as mct            # noqa: E402
import src.nif_convert as nc                                   # noqa: E402

# A piece is only worth scoring if its geometry actually reaches the band. This
# is a COVERAGE precondition, not a verdict: a gauntlet has no butt, and
# counting it as "clean" would dilute every share this census reports.
MIN_BAND_COVER = 5.0


# THE SUMMED BODY DELTA DEPENDS ONLY ON (vertex count, preset), so building it
# per piece rebuilt ~200 dense (n,3) arrays from the OSD 1220 times and made the
# census 19s/piece. Cached by vertex count, which is EXACTLY the assumption
# `morph_clip_test` itself makes -- it calls `body_morphs(len(bV))` and applies
# the offsets by index, so two bodies of equal vert count already get identical
# treatment there. This changes the speed, not the number: the control arms
# reproduce the CLI both before and after.
_DB_CACHE = {}

# `garment_morphs` re-reads and re-PARSES the piece's `.tri` ONCE PER SHAPE. On
# a 19-shape cuirass that is 19 parses of the same file -- measured at 47s of a
# 249s two-piece run, 46M struct.unpack_from calls. The parse is pure, so
# memoising it BY PATH changes only how often it happens. Done by wrapping
# `TriFile.load` rather than reimplementing `garment_morphs`, so the harness's
# own lookup (stem stripping, shape matching, dense expansion) still runs.
_TRI_FILE_CACHE = {}


def _install_tri_cache():
    from src import tri as _tri
    real = _tri.TriFile.load

    def cached(path, *a, **kw):
        key = str(path)
        if key not in _TRI_FILE_CACHE:
            _TRI_FILE_CACHE[key] = real(path, *a, **kw)
        return _TRI_FILE_CACHE[key]

    _tri.TriFile.load = staticmethod(cached)


def _body_delta(n_verts, sel_by_name, strength):
    key = (n_verts, strength)
    hit = _DB_CACHE.get(key)
    if hit is not None:
        return hit
    bm = mct.body_morphs(n_verts)[0]
    if not bm:
        _DB_CACHE[key] = (None, None)
        return None, None
    sel = {k: v for k, v in sel_by_name.items() if k in bm}
    if not sel:
        _DB_CACHE[key] = (None, sel)
        return None, sel
    dB = np.zeros((n_verts, 3), np.float64)
    for k, v in sel.items():
        dB += bm[k] * v
    dB *= float(strength)
    _DB_CACHE[key] = (dB, sel)
    return dB, sel


def _report_sliced(tester, bodyV, bT, bN, idx, va, slice_n=384):
    """`ClipTester.report` over the band in RAY SLICES, combined exactly.

    WHY. Scoring the whole band at once made `_pairs` build one
    (rays x candidate-triangles) index pair per piece, which on the largest
    meshes exhausted RAM: the first butt census lost 192 pieces to MemoryError
    and then took the machine down. Excluding those pieces is not neutral --
    they are the big, many-layered ones most likely to clip -- so the sample
    stops being random exactly where it matters.

    THIS IS NOT AN APPROXIMATION. Rays are independent, which is the same
    property `mesh_penetration` already relies on to batch its own casts
    ("Rays are independent, so slicing the union is arithmetically
    identical"). The tester is built ONCE on the full garment, so every ray
    still sees every triangle; only the ray set is chunked. Every reported
    quantity is a sum over the band weighted by vertex area, so the slices
    recombine by addition. Asserted equal to the unsliced call by --control.
    """
    idx = np.asarray(idx)
    tot = float(va[idx].sum())
    if tot <= 0:
        return None
    clip_a, cover_a = 0.0, 0.0
    for s in range(0, len(idx), slice_n):
        sl = idx[s:s + slice_n]
        r = tester.report(bodyV, bT, bN, sl, va, oriented=True)
        ci = np.asarray(r["clip_idx"], np.int64)
        if len(ci):
            clip_a += float(va[ci].sum())
        oh = np.isfinite(r["out_t"])
        if oh.any():
            cover_a += float(va[sl[oh]].sum())
    return {"clipping_pct": 100.0 * clip_a / tot,
            "covered_pct": 100.0 * cover_a / tot}


def _body_arrays(shape):
    bV = mct._world(shape)
    bT = np.asarray(shape.tris, np.int64).reshape(-1, 3)
    bN = np.asarray(shape.normals, np.float64)
    bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)
    return bV, bT, bN


def score(nif_path, band, sel_by_name, strength=1.0, template=None):
    """One piece -> a row, or ('skip', reason). Mirrors morph_clip_test.main."""
    nf = nc._pynifly().NifFile(filepath=str(nif_path))
    body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    path_kind = "swap"
    if body is None:
        path_kind = "copy"
        if template is None:
            return None, "copy_no_template"
        body = template
    bV, bT, bN = _body_arrays(body)

    # Resolve the preset against THIS body's OSD keys (the OSD is keyed by vert
    # count, so a body with a different count resolves a different set).
    dB, sel = _body_delta(len(bV), sel_by_name, strength)
    if sel is None:
        return None, "no_body_osd"
    if not sel:
        return None, "preset_unresolved"
    moved_b = np.linalg.norm(dB, axis=1)
    if moved_b.max() < 1e-4:
        return None, "body_did_not_move"

    mask = mct.BANDS[band](bV)
    idx = np.flatnonzero(mask)
    if len(idx) < 20:
        return None, "band_too_small"

    parts_bind, parts_morph = [], []
    gmax, chain_w, nv_tot, tri_seen = 0.0, 0.0, 0, False
    for s in nf.shapes:
        nm = s.name or ""
        if nm == "BaseShape" or nc._is_inline_body_name(nm):
            continue
        if int(getattr(s, "flags", 0) or 0) & 0x1:
            continue
        if not any(v for v in (s.textures or {}).values()):
            continue
        gV = mct._aligned(s, bV)
        gT = np.asarray(s.tris, np.int64).reshape(-1, 3)
        gm, tri_name = mct.garment_morphs(nif_path, nm, len(gV))
        if tri_name:
            tri_seen = True
        dG = np.zeros_like(gV)
        for k in sel:
            mk = mct._match(k, gm.keys())
            if mk is not None:
                dG += gm[mk] * sel[k]
        dG *= float(strength)
        gmax = max(gmax, float(np.linalg.norm(dG, axis=1).max()))
        n = len(gV)
        vw = [dict() for _ in range(n)]
        for b, prs in (s.bone_weights or {}).items():
            for vi, w in prs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        chain_w += sum(nc._chain_vert_mask(vw, n))
        nv_tot += n
        parts_bind.append((gV, gT))
        parts_morph.append((gV + dG, gT))

    if not parts_bind:
        return None, "no_rendered_shape"
    if not tri_seen:
        return None, "no_tri"

    va_b = sa.vert_areas(bV, bT)
    gV0, gT0 = mct._union(parts_bind)
    rb = _report_sliced(sa.ClipTester(gV0, gT0), bV, bT, bN, idx, va_b)
    if rb is None:
        return None, "zero_band_area"
    if rb["covered_pct"] < MIN_BAND_COVER:
        return None, "band_not_covered"
    gV1, gT1 = mct._union(parts_morph)
    rm = _report_sliced(sa.ClipTester(gV1, gT1), bV + dB, bT, bN, idx,
                        sa.vert_areas(bV + dB, bT))
    if rm is None:
        return None, "zero_band_area"
    bmove = float(np.median(moved_b[idx]))
    return {
        "nif": str(nif_path),
        "path": path_kind,
        "bind": rb["clipping_pct"],
        "morph": rm["clipping_pct"],
        "cover_bind": rb["covered_pct"],
        "cover_morph": rm["covered_pct"],
        "body_move_band": bmove,
        "garment_max_move": gmax,
        "follow": (gmax / float(moved_b.max())) if moved_b.max() > 1e-6 else 0.0,
        "chain_share": chain_w / max(nv_tot, 1),
        "verts": nv_tot,
        # SHAPE COUNT: the layer ride only has something to ride when a piece
        # has more than one garment layer, so this separates pieces the
        # write-time ride can touch from ones it provably cannot (measured: a
        # single-shape piece reproduces byte-for-byte with the ride disabled).
        "n_shapes": len(parts_bind),
    }, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--band", default="butt", choices=sorted(mct.BANDS))
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--floor", type=int, default=40,
                    help="refuse to report below this many SCORED pieces")
    ap.add_argument("--control-nif", action="append", default=[],
                    help="NIF to reproduce under --control (repeatable); "
                         "defaults to the first two the pack offers")
    ap.add_argument("--control", action="store_true",
                    help="reproduce two known CLI arms and exit")
    a = ap.parse_args()

    _install_tri_cache()
    pv, _kind = mct._load_preset(a.preset)

    # Resolve the preset ONCE against a reference body's OSD, then reuse. The
    # per-piece call re-filters to that body's own keys.
    tpl_path = nc._find_ube_femalebody("_1")
    tpl = None
    if tpl_path:
        tn = nc._pynifly().NifFile(filepath=str(tpl_path))
        tpl = max(tn.shapes, key=lambda s: len(s.verts))
    if tpl is None:
        print("ABORT: no UBE template body resolved", file=sys.stderr)
        return 3
    tV, _tT, _tN = _body_arrays(tpl)
    bm_ref, _ = mct.body_morphs(len(tV))
    sel, missing, fuzzy = mct._resolve_preset(pv, bm_ref)
    engaged = sum(1 for v in pv.values() if abs(v) > 1e-9)
    print(f"preset       : {Path(a.preset).name}")
    print(f"             : {engaged} engaged, {len(sel)} resolved "
          f"({100.0 * len(sel) / max(engaged, 1):.1f}%), {len(missing)} "
          f"unresolved, {len(fuzzy)} fuzzy")
    if not sel:
        print("ABORT: preset resolved to no slider", file=sys.stderr)
        return 3

    if a.control:
        # REPRODUCE THE CLI BEFORE ANY CENSUS NUMBER IS BELIEVED. These arms
        # were produced by `morph_clip_test.py` itself earlier today; if this
        # wrapper composes the same helpers in a different order, it says so
        # here rather than in a census nobody can check.
        # Arms come from --control-nif, or failing that from the PACK ITSELF,
        # walked in sorted order so the pick is deterministic. The first
        # version named one machine's pack path and a specific mod, which is
        # the whole reason this tool sat untracked while sessions cited it as
        # if it were toolkit.
        #
        # IT MUST PICK PIECES THAT ACTUALLY SCORE. Taking simply the first two
        # files found two that both SKIP `band_not_covered` -- a control that
        # reproduces nothing, printed in the same shape as one that reproduces
        # something. So walk until two arms return a row, and FAIL if the pack
        # cannot supply them: an unverifiable control is worse than none,
        # because it looks like verification.
        want = 2
        if a.control_nif:
            arms = [(n, a.band) for n in a.control_nif]
        else:
            arms = []
            for p in sorted(Path(a.pack).rglob("*_1.nif")):
                row, _why = score(str(p), a.band, sel, a.strength, tpl)
                if row is not None:
                    arms.append((str(p), a.band))
                    if len(arms) >= want:
                        break
            if len(arms) < want:
                print(f"ABORT: --control found only {len(arms)} scorable "
                      f"{a.band} piece(s) under --pack; a control that "
                      f"reproduces nothing proves nothing", file=sys.stderr)
                return 3
        scored_arms = 0
        for nif, band in arms:
            row, why = score(nif, band, sel, a.strength, tpl)
            if row is None:
                print(f"CONTROL {Path(nif).name} band={band}: SKIP {why}")
            else:
                scored_arms += 1
                print(f"CONTROL {Path(nif).name} band={band}: "
                      f"bind {row['bind']:.3f}  morph {row['morph']:.3f}  "
                      f"cover {row['cover_bind']:.2f}  "
                      f"bodymove {row['body_move_band']:.3f}")
        if not scored_arms:
            print("ABORT: every control arm skipped -- nothing was reproduced",
                  file=sys.stderr)
            return 3
        return 0

    root = Path(a.pack)
    cands = sorted(p for p in root.rglob("*_1.nif"))
    if a.every > 1:
        cands = cands[::a.every]
    if a.limit:
        cands = cands[:a.limit]

    rows, skips = [], {}
    errored = []          # NAME the failures: a count alone cannot be audited
    t0 = time.time()
    for i, p in enumerate(cands):
        try:
            row, why = score(p, a.band, sel, a.strength, tpl)
        except MemoryError:
            # A 20%-of-population MemoryError rate biased the first butt run
            # toward EXCLUDING the largest meshes -- exactly the pieces most
            # likely to clip -- so "scored 402" was not a random sample of
            # 1536. Retry once with the ray batch forced small; the metric is
            # unchanged, only the temporaries are.
            try:
                import scripts.analysis.mesh_penetration as _mp
                _old = _mp._auto_chunk
                _mp._auto_chunk = lambda n_tris, budget_bytes=2.0e7: max(
                    16, int(_old(n_tris, budget_bytes)))
                try:
                    row, why = score(p, a.band, sel, a.strength, tpl)
                finally:
                    _mp._auto_chunk = _old
            except Exception as e2:                           # noqa: BLE001
                row, why = None, f"error:{type(e2).__name__}"
                errored.append((str(p), f"{type(e2).__name__}: {e2}"))
        except Exception as e:                                # noqa: BLE001
            row, why = None, f"error:{type(e).__name__}"
            errored.append((str(p), f"{type(e).__name__}: {e}"))
        if row is None:
            skips[why] = skips.get(why, 0) + 1
        else:
            rows.append(row)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(cands)}  scored {len(rows)}  "
                  f"{time.time() - t0:.0f}s", flush=True)

    Path(a.out).write_text(json.dumps(
        {"band": a.band, "preset": Path(a.preset).name,
         "examined": len(cands), "scored": len(rows), "skips": skips,
         "errored": errored, "rows": rows}, indent=1), encoding="utf-8")

    print(f"\nband {a.band}   preset {Path(a.preset).name}")
    print(f"examined {len(cands)}   SCORED {len(rows)}")
    for k, v in sorted(skips.items(), key=lambda kv: -kv[1]):
        print(f"   excluded {v:>5}  {k}")
    nerr = sum(v for k, v in skips.items() if k.startswith("error:"))
    if nerr:
        # An ERROR exclusion is not a neutral one: it removes a piece for a
        # reason unrelated to whether it clips, so a high rate makes every
        # share below a statement about a biased subset. Named, so it can be
        # audited rather than trusted.
        print(f"   ERROR exclusions {nerr} of {len(cands)} "
              f"({100.0 * nerr / max(len(cands), 1):.1f}%) -- "
              f"{'ACCEPTABLE' if nerr <= 0.05 * len(cands) else 'HIGH: treat every share below as a biased subset'}")
        for pth, msg in errored[:5]:
            print(f"      {Path(pth).name}: {msg[:90]}")
    if len(rows) < a.floor:
        print(f"\nABORT: only {len(rows)} scored, floor is {a.floor} -- "
              f"this census cannot distinguish 'nothing is wrong' from "
              f"'I measured nothing'", file=sys.stderr)
        return 2

    def share(pred, pool):
        n = sum(1 for r in pool if pred(r))
        return n, 100.0 * n / max(len(pool), 1)

    for kind in ("swap", "copy", "ALL"):
        pool = rows if kind == "ALL" else [r for r in rows if r["path"] == kind]
        if not pool:
            continue
        nb, sb = share(lambda r: r["bind"] > 0.05, pool)
        nm, sm = share(lambda r: r["morph"] > 0.05, pool)
        n1, s1 = share(lambda r: r["morph"] > 1.0, pool)
        nc_, sc_ = share(lambda r: r["bind"] <= 0.05 and r["morph"] > 0.05, pool)
        print(f"\n  {kind:<5} n={len(pool)}")
        print(f"     bind  > 0.05%   {nb:>4}  ({sb:.0f}%)")
        print(f"     MORPH > 0.05%   {nm:>4}  ({sm:.0f}%)")
        print(f"     MORPH > 1.0%    {n1:>4}  ({s1:.0f}%)")
        print(f"     CLEAN AT BIND, CLIPPING MORPHED  {nc_:>4}  ({sc_:.0f}%)"
              f"   <- the class")
        # SPLIT THE CLASS on follow and chain share before quoting it -- the
        # bucket mixes chord error, morph-follow gaps and physics cloth.
        cls = [r for r in pool if r["bind"] <= 0.05 and r["morph"] > 0.05]
        chord = [r for r in cls if r["follow"] > 0.3 and r["chain_share"] < 0.3]
        nofol = [r for r in cls if r["follow"] <= 0.3]
        cloth = [r for r in cls if r["follow"] > 0.3 and r["chain_share"] >= 0.3]
        print(f"        follow>0.3 & chain<0.3 (CHORD candidate) {len(chord):>4}")
        print(f"        follow<=0.3            (follow gap)      {len(nofol):>4}")
        print(f"        chain>=0.3             (physics cloth)   {len(cloth):>4}")
        # Can the WRITE-TIME layer ride reach this piece at all? It rides
        # non-reference layers onto the reference, so a piece with one garment
        # shape has no rider and the pass provably no-ops there (verified: 0
        # verts moved with it disabled). Measured on the reported cuirass, the
        # ride owns 56% of the residual bust clip on the traced piece, so
        # if that
        # generalises, the clipping population should skew multi-shape.
        multi = [r for r in pool if r.get("n_shapes", 0) > 1]
        single = [r for r in pool if r.get("n_shapes", 0) <= 1]
        for lbl, grp in (("multi-shape (ride CAN fire)", multi),
                         ("single-shape (ride CANNOT)", single)):
            if not grp:
                continue
            nclip = sum(1 for r in grp if r["morph"] > 0.05)
            med = float(np.median([r["morph"] for r in grp]))
            print(f"        {lbl}  n={len(grp):<4} clipping {nclip:>4} "
                  f"({100.0 * nclip / len(grp):.0f}%)  median {med:.3f}%")

    worst = sorted(rows, key=lambda r: -r["morph"])[:15]
    print(f"\n  WORST {a.band} MORPH clip")
    print(f"  {'piece':<52}{'path':>6}{'bind':>8}{'morph':>8}"
          f"{'follow':>8}{'chain':>7}")
    for r in worst:
        print(f"  {Path(r['nif']).parent.name + '/' + Path(r['nif']).name:<52}"
              f"{r['path']:>6}{r['bind']:>8.3f}{r['morph']:>8.3f}"
              f"{r['follow']:>8.2f}{100 * r['chain_share']:>6.0f}%")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
