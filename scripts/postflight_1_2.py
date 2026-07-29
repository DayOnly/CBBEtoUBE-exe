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
           "follow": {}, "bodytri": None, "hdt": None, "err": None}
    try:
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
    except Exception as e:
        rec["err"] = f"{type(e).__name__}: {e}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--baseline", default="", help="pre-reconvert census jsonl")
    args = ap.parse_args()

    os.chdir(_REPO)
    from src import paths
    mods = paths.mods_root()
    out_mod = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
    root = Path(mods) / out_mod / "meshes"
    if not root.is_dir():
        print(f"ABORT: output not found at {root}", file=sys.stderr)
        sys.exit(3)

    excl = re.compile(r"1stperson", re.I)
    rels = [str(p.relative_to(root)) for p in root.rglob("*_1.nif")
            if not excl.search(str(p))]
    rels.sort()
    if args.limit:
        rels = rels[:args.limit]
    print(f"population: {len(rels)} pieces under {root}")
    if len(rels) < 100:
        print("ABORT: population implausibly small -- wrong root, or the "
              "reconvert did not finish", file=sys.stderr)
        sys.exit(3)

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

    # ---- CONTROLS (abort on failure) ----
    fails = []

    def _find(sub):
        s = sub.replace("/", os.sep).lower()
        return next((r for r in recs if r["rel"].lower().endswith(s)), None)

    c_split = _find(CTRL_SPLIT_SUBSTR)
    c_nosplit = _find(CTRL_NOSPLIT_SUBSTR)
    if c_split is None:
        fails.append(f"CONTROL missing from output: {CTRL_SPLIT_SUBSTR}")
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

    if fails:
        print("\n!! CONTROL FAILURES -- results NOT trustworthy:")
        for f in fails:
            print("   " + f)
        sys.exit(3)
    print("\ncontrols passed "
          "(split control split + repointed, no double-split, metric has data)")

    bad = bool(zw or bad_xml or no_bodytri or no_hdt or errs)
    print("RESULT:", "PROBLEMS FOUND (see above)" if bad else "CLEAN")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
