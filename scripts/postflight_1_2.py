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

"""Post-reconvert health check for the 1.2 bust-follow release.

Verifies, ON THE SHIPPED MESHES, that the four things 1.2 changed actually
landed pack-wide -- and that nothing regressed:

  A  zero-weight bones          (SKIN_INFLUENCE_CAP default ON) -- expect 0
  B  bust-collider split class  (clone present AND its XML decl repointed)
  C  bust follow ratio          (torso garments; vs the pre-reconvert census)
  D  morph/physics invariants   (BODYTRI record present when a TRI exists;
                                 HDT link present when the piece has an XML)
  E  FIT: clipping AND standoff (bust front, union of visible garment)

WHY E EXISTS. A/B/C/D can all pass on a garment that stands three units off the
body. Clipping has no upper bound, so a piece inflated until nothing pokes
scores perfectly on every geometric check this script had -- and that is not
hypothetical: a bust probe tuned against clipping alone was reported IN GAME as
overinflated, twice, while nothing here could see it. STANDOFF is the
counter-metric, and E is the pack-wide gate that would have caught it.

E needs an ANCHOR, and it must be measured in the SAME RUN over the SAME MASK:
standoff is a distribution over whichever skin the mask selects, and comparing
against a figure taken on a different mask labels a correctly-fitted armour
overinflated (measured). `CTRL_SPLIT_SUBSTR` -- the piece the user confirmed
CLEAN and correctly fitted in game -- is that anchor, and it is already forced
into every sample. If it is absent, stale, or does not read ~0% clipping, E
certifies nothing and says so rather than passing.

WHY MESH-BASED, NOT LOG-BASED. The converter fans NIF work across a process
pool, and in the frozen WINDOWED exe a worker's `sys.stdout`/`sys.stderr` can
be None -- in which case `print()` is silently discarded (verified: CPython's
print returns without writing when the stream resolves to None). Every WARN a
PASS emits therefore may never reach the run log in a real MO2 launch, so a
clean log is not evidence of a clean run. This reads the output instead.

    python scripts/postflight_1_2.py [--limit N] [--workers N]

Read-only. Resolves the output mod via the live MO2 instance
(CBBE2UBE_MO2_INI). Exit 3 if a CONTROL fails (results untrustworthy),
exit 1 if a check fails, 0 if clean.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

BAND = (88.0, 104.0)        # UBE bust band; breast z 90-102, apex ~95 (pinned)
PROX = 4.0                  # garment vert counts only this close to the body
BREAST_RE = re.compile(r"breast", re.I)
WRITE_MIN = 1e-4

# Pieces whose behaviour is KNOWN from the in-game A/B -- the controls.
CTRL_SPLIT_SUBSTR = "armor/hide/f/cuirasslight_1.nif"     # MUST be split
CTRL_NOSPLIT_SUBSTR = "armor/bandit/body1f_1.nif"          # author already split

# --- E (fit) ---
# Front-only, and the breast band proper rather than the wider BAND above: a
# mask that includes skin facing away from the garment makes standoff
# meaningless (one ray reaches a distant panel -- the clean armour reads max
# 10.21u over the full bust band and 2.01u over the front).
FIT_BAND = (90.0, 102.0)
FIT_MIN_VERTS = 50          # below this the piece does not cover the bust
# Fit is only defined where the garment actually covers the bust. A piece that
# covers a sliver has a standoff distribution built from a handful of stray
# hits, and classifying it is the "metric reports a number it cannot support"
# failure: the first run of E flagged a FIRST-PERSON mesh at 8.47u standoff,
# which is not an overinflated cuirass, it is arms. Judge only what is covered;
# report the rest as unjudged rather than dropping them silently.
FIT_MIN_COVERED = 25.0      # % of the bust-front area the garment must cover
FIT_CLIP_BAD = 1.0          # % of bust-front AREA clipping; a clean piece is 0.0
CTRL_ANCHOR_CLIP_MAX = 0.5  # the anchor is user-confirmed CLEAN; if the metric
                            # disagrees, the metric is wrong for this run

_nc = None
_np = None


def _init():
    global _nc, _np
    os.chdir(_REPO)
    import numpy
    import src.nif_convert as nif_convert
    _np = numpy
    _nc = nif_convert


def _wv(s):
    """(world verts, per-vert weight dicts) for a shape."""
    g2s = _nc._shape_global_to_skin(s)
    V = _nc._verts_skin_to_world(_np.asarray(s.verts, _np.float64), g2s)
    pv = [dict() for _ in range(len(V))]
    for b, pairs in (s.bone_weights or {}).items():
        for vi, w in pairs:
            iv = int(vi)
            if 0 <= iv < len(V):
                pv[iv][b] = pv[iv].get(b, 0.0) + float(w)
    return V, pv


def _breast(dv):
    return sum(w for b, w in dv.items() if BREAST_RE.search(b))


def check_one(rel_and_root):
    """One output NIF -> a record. Runs in a pool worker."""
    rel, root = rel_and_root
    p = Path(root) / rel
    rec = {"rel": rel, "zero_weight": [], "clones": [], "xml_repointed": None,
           "follow": {}, "bodytri": None, "hdt": None, "err": None,
           "mtime": 0.0, "fit": None}
    try:
        rec["mtime"] = p.stat().st_mtime
        pyn = _nc._pynifly()
        nf = pyn.NifFile(filepath=str(p))
        names = {s.name for s in nf.shapes}

        # --- A: zero-weight bones (equip-CTD class) ---
        for s in nf.shapes:
            bw = s.bone_weights or {}
            for b in (s.bone_names or []):
                if not any(float(w) > WRITE_MIN for _v, w in bw.get(b, [])):
                    rec["zero_weight"].append(f"{s.name}:{b}")

        # --- B: split clones + XML repoint ---
        for n in sorted(names):
            if n.endswith("Col") and n[:-3] in names:
                rec["clones"].append(n)
        stem = p.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
        xml = p.parent / f"{stem}.xml"
        if rec["clones"] and xml.is_file():
            txt = xml.read_text(errors="ignore")
            decls = set(re.findall(
                r'<per-triangle-shape\s+name="([^"]+)"', txt))
            # every minted clone must be the one named, and its garment must not be
            rec["xml_repointed"] = all(
                c in decls and c[:-3] not in decls for c in rec["clones"])

        # --- D: morph / physics invariants ---
        tri = p.parent / f"{stem}.tri"
        if tri.is_file():
            has = False
            for s in nf.shapes:
                try:
                    if any(getattr(ed, "name", None) == "BODYTRI"
                           for ed in s.extra_data()):
                        has = True
                        break
                except Exception:
                    pass
            rec["bodytri"] = has
        if xml.is_file():
            rec["hdt"] = any(
                getattr(ed, "name", None) == "HDT Skinned Mesh Physics Object"
                for ed in nf.rootNode.extra_data())

        # --- C: bust follow for torso garments ---
        body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
        if body is not None:
            from scipy.spatial import cKDTree
            bV, bW = _wv(body)
            tree = cKDTree(bV)
            colliders = _nc._hdt_collider_shape_names(p, nif=nf)
            for s in nf.shapes:
                nm = s.name or ""
                if nm in ("BaseShape",) or nm in colliders:
                    continue
                if _nc._is_inline_body_name(nm):
                    continue
                try:
                    if not _nc._shape_is_rigid_torso_armor(s):
                        continue
                    gV, gW = _wv(s)
                except Exception:
                    continue
                z = gV[:, 2]
                band = _np.flatnonzero((z >= BAND[0]) & (z <= BAND[1]))
                if len(band) < 50:
                    continue
                d, idx = tree.query(gV[band])
                keep = d < PROX
                if int(keep.sum()) < 50:
                    continue
                gm = float(_np.mean([_breast(gW[int(i)])
                                     for i in band[keep]]))
                bm = float(_np.mean([_breast(bW[int(j)]) for j in idx[keep]]))
                if bm > 1e-6:
                    rec["follow"][nm] = round(gm / bm, 4)

            # --- E: FIT -- clipping AND standoff over the bust front ---
            # The UNION of visible garment shapes, not one at a time: scoring a
            # shape alone counts skin covered by a SIBLING as exposed, and half
            # of converted output has more than one visible shape.
            from scripts.analysis import standoff_audit as _sa
            parts = []
            for s in nf.shapes:
                nm = s.name or ""
                if (nm == "BaseShape" or nm in colliders
                        or _nc._is_inline_body_name(nm)):
                    continue
                if int(getattr(s, "flags", 0) or 0) & 0x1:
                    continue                       # hidden (collider clone)
                if not any(v for v in (s.textures or {}).values()):
                    continue                       # not rendered
                try:
                    parts.append((_wv(s)[0],
                                  _np.asarray(s.tris, _np.int64).reshape(-1, 3)))
                except Exception:
                    continue
            bT = _np.asarray(body.tris, _np.int64).reshape(-1, 3)
            bN = _np.asarray(body.normals, _np.float64)
            bN = bN / _np.clip(_np.linalg.norm(bN, axis=1, keepdims=True),
                               1e-9, None)
            zz = bV[:, 2]
            fmask = ((zz >= FIT_BAND[0]) & (zz <= FIT_BAND[1])
                     & (bV[:, 1] > 2.0))
            if parts and int(fmask.sum()) >= FIT_MIN_VERTS:
                gV_u = _np.concatenate([v for v, _t in parts])
                off, tl = 0, []
                for v, t in parts:
                    tl.append(t + off)
                    off += len(v)
                gT_u = _np.concatenate(tl)
                idxf = _np.flatnonzero(fmask)
                ct = _sa.ClipTester(gV_u, gT_u)
                rp = ct.report(bV, bT, bN, idxf, _sa.vert_areas(bV, bT),
                               oriented=True)
                so = ct.standoff(bV, bN, idxf, tmax=12.0)
                if rp["covered_pct"] is not None and len(so):
                    rec["fit"] = {
                        "clip": round(float(rp["clipping_pct"]), 3),
                        # Split by DEPTH. One percentage conflates 0.06u of
                        # penetration with a 2u gap; both are visible in game
                        # (the zoom test settled that the shallow band is NOT
                        # cosmetic) but they need corrections an order of
                        # magnitude apart, so one push budget cannot serve both.
                        "clip_coincident": round(
                            float(rp["clip_coincident_pct"]), 3),
                        "clip_shallow": round(float(rp["clip_shallow_pct"]), 3),
                        "clip_buried": round(float(rp["clip_buried_pct"]), 3),
                        "clip_depth_p90": (
                            None if rp["clip_depth_p90"] is None
                            else round(float(rp["clip_depth_p90"]), 3)),
                        "covered": round(float(rp["covered_pct"]), 2),
                        "median": round(float(_np.median(so)), 3),
                        "p90": round(float(_np.percentile(so, 90)), 3),
                        "n": int(len(so)),
                    }
    except Exception as e:
        rec["err"] = f"{type(e).__name__}: {e}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--weights", choices=("both", "1", "0"), default="both",
                    help="which weight files to read. BOTH by default: the "
                         "checks used to glob *_1.nif only, and _0 is not a "
                         "scaled copy of _1 -- one cuirass measures 4.52%% "
                         "bust-front clipping at weight 1 and 9.48%% at "
                         "weight 0, so half the shipped output was invisible "
                         "to every check here.")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--baseline", default="", help="pre-reconvert census jsonl")
    ap.add_argument("--stale-hours", type=float, default=6.0,
                    help="a piece older than this behind the newest output is "
                         "reported STALE (left over from an earlier run)")
    args = ap.parse_args()

    os.chdir(_REPO)
    from src import paths
    mods = paths.mods_root()
    out_mod = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
    root = Path(mods) / out_mod / "meshes"
    if not root.is_dir():
        print(f"ABORT: output not found at {root}", file=sys.stderr)
        sys.exit(3)

    # "1stp" as well as "1stperson": the Ysmir set uses the short prefix and a
    # first-person mesh slipped into E's population on the first run.
    excl = re.compile(r"1stperson|1stp", re.I)
    pats = {"both": ("*_1.nif", "*_0.nif"), "1": ("*_1.nif",),
            "0": ("*_0.nif",)}[args.weights]
    rels = sorted({str(p.relative_to(root)) for pat in pats
                   for p in root.rglob(pat) if not excl.search(str(p))})
    # Sanity-check the DISCOVERED population, before --limit: limiting is a
    # debugging aid and must not trip the "wrong root" guard.
    n1 = len([r for r in rels if r.lower().endswith("_1.nif")])
    print(f"population: {len(rels)} piece(s) under {root} "
          f"({n1} at weight 1, {len(rels) - n1} at weight 0)")
    if len(rels) < 100:
        print("ABORT: population implausibly small -- wrong root, or the "
              "reconvert did not finish", file=sys.stderr)
        sys.exit(3)
    if args.limit:
        # The control pieces are FORCED into every sample. Without this a
        # limited run reports "control missing" and certifies nothing -- a
        # sample that cannot validate itself is the check-measures-nothing
        # failure in a new costume.
        ctrl = [r for r in rels
                if r.replace(os.sep, "/").lower().endswith(
                    (CTRL_SPLIT_SUBSTR, CTRL_NOSPLIT_SUBSTR))]
        rels = ctrl + [r for r in rels[:args.limit] if r not in ctrl]
        print(f"  --limit {args.limit}: SAMPLING {len(rels)} of them "
              f"({len(ctrl)} control piece(s) force-included); class counts "
              f"below are NOT pack-wide totals")

    jobs = [(r, str(root)) for r in rels]
    recs = []
    with mp.Pool(max(1, args.workers), initializer=_init) as pool:
        for i, rec in enumerate(pool.imap_unordered(check_one, jobs,
                                                    chunksize=8)):
            recs.append(rec)
            if (i + 1) % 200 == 0:
                print(f"  {i+1}/{len(rels)}", flush=True)

    out = Path(os.environ.get("TEMP", ".")) / "postflight_1_2.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    errs = [r for r in recs if r["err"]]
    zw = [r for r in recs if r["zero_weight"]]
    split = [r for r in recs if r["clones"]]
    bad_xml = [r for r in split if r["xml_repointed"] is not True]
    no_bodytri = [r for r in recs if r["bodytri"] is False]
    no_hdt = [r for r in recs if r["hdt"] is False]
    follows = sorted((v, r["rel"], n) for r in recs
                     for n, v in r["follow"].items())

    # ---- FRESHNESS: the output mod is overwritten PER MOD, so a mod that
    # errored (or was skipped) leaves its PREVIOUS-run file in place. Measuring
    # those as if they were this run's output is the "clean result that
    # measured nothing" failure with extra steps -- a stale piece cannot have
    # the new passes in it. Report them, and refuse to certify if a CONTROL is
    # stale. ----
    newest = max((r["mtime"] for r in recs if r["mtime"]), default=0.0)
    cutoff = newest - args.stale_hours * 3600.0
    stale = [r for r in recs if r["mtime"] and r["mtime"] < cutoff]
    fresh = [r for r in recs if r not in stale]

    # ---- CONTROLS (abort on failure) ----
    fails = []
    if stale:
        import datetime as _dt
        newest_s = _dt.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
        print(f"\n!! STALE: {len(stale)} of {len(recs)} piece(s) predate the "
              f"newest output ({newest_s}) by more than {args.stale_hours}h "
              f"-- they are LEFTOVERS from an earlier run, not this one's "
              f"output. Every figure below covers all {len(recs)}; the "
              f"{len(fresh)} fresh ones are what this run actually produced.")
        for r in stale[:8]:
            print(f"     stale: {r['rel']}")
        if len(stale) > 8:
            print(f"     ... and {len(stale) - 8} more")

    def _find(sub):
        s = sub.replace("/", os.sep).lower()
        return next((r for r in recs if r["rel"].lower().endswith(s)), None)

    c_split = _find(CTRL_SPLIT_SUBSTR)
    c_nosplit = _find(CTRL_NOSPLIT_SUBSTR)
    if c_split is None:
        fails.append(f"CONTROL missing from output: {CTRL_SPLIT_SUBSTR}")
    elif c_split in stale:
        fails.append(f"CONTROL {CTRL_SPLIT_SUBSTR} is STALE (left over from an "
                     f"earlier run) -- this run did not produce it, so nothing "
                     f"here certifies the release")
    elif not c_split["clones"]:
        fails.append(f"CONTROL {CTRL_SPLIT_SUBSTR}: in-game-confirmed split "
                     f"class did NOT split -- the fix did not run")
    elif c_split["xml_repointed"] is not True:
        fails.append(f"CONTROL {CTRL_SPLIT_SUBSTR}: clone present but XML NOT "
                     f"repointed -- pass 2 did not run (ordering?)")
    if c_nosplit is not None and any(
            n.endswith("ColCol") for n in c_nosplit["clones"]):
        fails.append(f"CONTROL {CTRL_NOSPLIT_SUBSTR}: double-split "
                     f"{c_nosplit['clones']}")
    if not follows:
        fails.append("CONTROL: zero follow measurements -- the metric saw "
                     "nothing (band/body resolution broken?)")

    # ---- E CONTROLS: the fit anchor must exist, be fresh, and be CLEAN ----
    # Without a same-run anchor E cannot judge fit at all, and must say so
    # rather than pass: a garment inflated until nothing pokes reads 0.0%
    # clipping, so "no clipping anywhere" is not evidence of good fit.
    fit_recs = [r for r in recs if r.get("fit")]
    anchor_fit = (c_split or {}).get("fit")
    fit_judged = True
    if anchor_fit is None:
        fit_judged = False
        fails.append(f"CONTROL: no FIT anchor -- {CTRL_SPLIT_SUBSTR} produced "
                     f"no standoff measurement, so E judged nothing")
    elif c_split in stale:
        fit_judged = False
        fails.append("CONTROL: the FIT anchor is STALE -- E cannot certify fit")
    elif anchor_fit["clip"] > CTRL_ANCHOR_CLIP_MAX:
        fit_judged = False
        fails.append(f"CONTROL: the FIT anchor (user-confirmed CLEAN in game) "
                     f"reads {anchor_fit['clip']:.2f}% clipping -- the metric "
                     f"disagrees with ground truth, so nothing E says counts")
    if not fit_recs:
        fails.append("CONTROL: zero FIT measurements -- E saw nothing")

    print(f"\nwrote {out}")
    print(f"errors reading: {len(errs)}")
    for e in errs[:5]:
        print(f"   {e['rel']}: {e['err']}")

    print(f"\nA  zero-weight bones: {sum(len(r['zero_weight']) for r in zw)} "
          f"across {len(zw)} piece(s)   [1.2 target: 0]")
    for r in zw[:10]:
        print(f"     {r['rel']}: {r['zero_weight'][:4]}")

    print(f"\nB  split class: {len(split)} piece(s) minted a hidden collider "
          f"clone; XML repointed on {len(split) - len(bad_xml)}")
    for r in bad_xml[:10]:
        print(f"     XML NOT REPOINTED: {r['rel']} {r['clones']}")

    print(f"\nC  bust follow: {len(follows)} torso garment(s) measured")
    if follows:
        vals = [v for v, _, _ in follows]
        vals_sorted = sorted(vals)
        med = vals_sorted[len(vals_sorted) // 2]
        under = [f for f in follows if f[0] < 0.5]
        print(f"     median {med:.3f}   under 0.5: {len(under)}   "
              f"over 1.5: {len([v for v in vals if v > 1.5])}")
        print(f"     lowest 10:")
        for v, rel, n in follows[:10]:
            print(f"       {v:6.3f}  {rel}  {n}")
    if args.baseline and Path(args.baseline).is_file():
        base = {}
        for line in Path(args.baseline).read_text(encoding="utf-8").splitlines():
            try:
                b = json.loads(line)
            except Exception:
                continue
            for n, v in (b.get("follow") or {}).items():
                base[(b["rel"].lower(), n)] = v
        now = {(r["rel"].lower(), n): v for r in recs
               for n, v in r["follow"].items()}
        common = set(base) & set(now)
        if common:
            up = sum(1 for k in common if now[k] > base[k] + 0.05)
            dn = sum(1 for k in common if now[k] < base[k] - 0.05)
            print(f"     vs baseline ({len(common)} matched garments): "
                  f"{up} improved, {dn} regressed, "
                  f"{len(common) - up - dn} unchanged (+-0.05)")
            worst = sorted(((now[k] - base[k], k) for k in common))[:5]
            for d, k in worst:
                if d < -0.05:
                    print(f"       REGRESSED {d:+.3f}  {k[0]}  {k[1]}")

    print(f"\nD  invariants: TRI present but no BODYTRI record: "
          f"{len(no_bodytri)}   XML present but no HDT link: {len(no_hdt)}")
    for r in no_bodytri[:5]:
        print(f"     no BODYTRI: {r['rel']}")
    for r in no_hdt[:5]:
        print(f"     no HDT link: {r['rel']}")

    print(f"\nE  fit (bust front, union of visible garment): "
          f"{len(fit_recs)} piece(s) measured")
    inflated, clipping = [], []
    if fit_recs and anchor_fit:
        print(f"     anchor {CTRL_SPLIT_SUBSTR}: clip {anchor_fit['clip']:.2f}% "
              f"standoff median {anchor_fit['median']:.2f}u "
              f"p90 {anchor_fit['p90']:.2f}u  ({anchor_fit['n']} verts)")
    thin = [r for r in fit_recs if r["fit"]["covered"] < FIT_MIN_COVERED]
    judged = [r for r in fit_recs if r["fit"]["covered"] >= FIT_MIN_COVERED]
    if thin:
        print(f"     not judged (covers under {FIT_MIN_COVERED:.0f}% of the "
              f"bust front, so fit is undefined): {len(thin)}")
        for r in thin[:5]:
            print(f"       covered {r['fit']['covered']:5.1f}%  {r['rel']}")
    if judged and fit_judged:
        from scripts.analysis import standoff_audit as sa
        for r in judged:
            f_ = r["fit"]
            if sa.check(f_, anchor_fit, keys=("median", "p90")):
                inflated.append(r)
            if f_["clip"] > FIT_CLIP_BAD:
                clipping.append(r)
        med = sorted(r["fit"]["median"] for r in judged)[len(judged) // 2]
        print(f"     standoff median across the {len(judged)} judged "
              f"piece(s): {med:.2f}u")
        print(f"     OVERINFLATED (standoff past the clean anchor): "
              f"{len(inflated)}")
        for r in sorted(inflated,
                        key=lambda x: -x["fit"]["median"])[:10]:
            f_ = r["fit"]
            print(f"       median {f_['median']:5.2f}u p90 {f_['p90']:5.2f}u "
                  f"clip {f_['clip']:5.2f}%  {r['rel']}")
        print(f"     CLIPPING over {FIT_CLIP_BAD}%: {len(clipping)}")
        for r in sorted(clipping, key=lambda x: -x["fit"]["clip"])[:10]:
            f_ = r["fit"]
            print(f"       clip {f_['clip']:5.2f}% (standoff "
                  f"{f_['median']:.2f}u)  {r['rel']}")
    elif fit_recs:
        print("     NOT JUDGED -- the anchor control failed; the numbers are "
              "in the jsonl but no piece was classified")
    # A LOOSE GARMENT IS NOT AN INFLATED ONE. The anchor is a fitted leather
    # cuirass, so a robe or a dress that is meant to hang will exceed it and be
    # flagged -- the absolute list above cannot tell "designed loose" from
    # "inflated by a pass". The BASELINE comparison can: it asks whether THIS
    # piece got looser than it used to be, which is the question a postflight
    # is actually for. Prefer this list over the absolute one when a baseline
    # exists.
    if args.baseline and Path(args.baseline).is_file() and judged:
        bfit = {}
        for line in Path(args.baseline).read_text(encoding="utf-8").splitlines():
            try:
                b = json.loads(line)
            except Exception:
                continue
            if b.get("fit"):
                bfit[b["rel"].lower()] = b["fit"]
        common = [r for r in judged if r["rel"].lower() in bfit]
        if common:
            looser = [(r["fit"]["median"] - bfit[r["rel"].lower()]["median"], r)
                      for r in common]
            looser = [x for x in looser if x[0] > 0.15]
            print(f"     vs baseline ({len(common)} matched): {len(looser)} "
                  f"piece(s) got LOOSER by more than 0.15u")
            for d, r in sorted(looser, key=lambda x: -x[0])[:10]:
                print(f"       +{d:.2f}u -> {r['fit']['median']:.2f}u  "
                      f"{r['rel']}")
        else:
            print("     vs baseline: no matched pieces carry fit data "
                  "(baseline predates check E)")

    if fails:
        print("\n!! CONTROL FAILURES -- results NOT trustworthy:")
        for f in fails:
            print("   " + f)
        sys.exit(3)
    print("\ncontrols passed (split control split + repointed, no "
          "double-split, follow has data, fit anchor present/fresh/clean)")

    bad = bool(zw or bad_xml or no_bodytri or no_hdt or errs
               or inflated or clipping)
    print("RESULT:", "PROBLEMS FOUND (see above)" if bad else "CLEAN")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
