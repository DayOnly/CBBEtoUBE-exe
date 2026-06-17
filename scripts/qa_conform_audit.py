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

"""QA audit for the fitted-cloth body-conform pass. Read-only -- run AFTER a
reconvert to validate that the pass did what it should across the whole output:

  * GARMENT shapes (carry jiggle weight + hug the body + no physics chain) are
    what the conform targets; it reports how well each is body-matched.
  * RIGID / LOOSE / CHAIN shapes are excluded by design and should be untouched.

It re-uses the converter's OWN gate helpers and thresholds, so this measures the
exact same classification the pass used -- no second source of truth.

Anomalies it flags:
  * jiggle-strip suspect -- a leg-dominant shape that HUGS the body but carries
    NO jiggle weight (the original "fitted pants went rigid" bug class).
  * weak match -- a GARMENT whose verts are mostly NOT body-matched (the conform
    may not have applied, or the fit is unusual -> eyeball it).
  * TORSO garments -- listed explicitly: the conform now also fires on fitted
    tops (catsuits/corsets), which is gate-validated but not yet eyeballed
    in-game -> good spot-check candidates.

Usage:
  python scripts/qa_conform_audit.py <armor_dir> [--body <femalebody_1.nif>]
                                      [--limit N]
  (set CBBE2UBE_MO2_INI or CBBE2UBE_UBE_BODY_1 so the body can be found if
   --body is omitted)
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))

import numpy as np                                  # noqa: E402
from src import nif_convert as nc                   # noqa: E402
from pyn import pynifly                             # noqa: E402


def _per_vert_weights(shape, n):
    vw = [dict() for _ in range(n)]
    for b, pairs in (shape.bone_weights or {}).items():
        for vi, w in pairs:
            iv = int(vi)
            if 0 <= iv < n:
                vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
    return vw


def classify_shape(s, body_w, body_bones, tree):
    """Return (klass, info) using the converter's exact gates. klass is one of
    SKIP / RIGID / CHAIN / LOOSE / GARMENT."""
    nm = (s.name or "").lower()
    if any(k in nm for k in nc._CONFORM_SKIP_NAMES):
        return "SKIP", {}
    bw = s.bone_weights or {}
    jig = 0
    for b, pairs in bw.items():
        if nc._is_physics_jiggle_scale_bone(b):
            jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
    try:
        V = np.asarray(s.verts, np.float64)
    except Exception:
        return "SKIP", {}
    n = len(V)
    if n == 0:
        return "SKIP", {}
    vw = _per_vert_weights(s, n)
    leg_dom = sum(1 for d in vw if d and nc._is_leg_rigid_bone(max(d, key=d.get)))
    leg_frac = leg_dom / n
    g2s = nc._shape_global_to_skin(s)
    Vw = nc._verts_skin_to_world(V, g2s)
    d, idx = tree.query(Vw)
    hug = float((d < nc._CONFORM_FIT_PROX).mean())
    info = {"n": n, "jig": jig, "hug": hug, "leg_frac": leg_frac}
    if jig < nc._CONFORM_MIN_JIGGLE_VERTS:
        # The original clip bug = a leg garment that COVERS a jiggling body
        # region but carries no jiggle itself. A thin thigh band over pure-thigh
        # body legitimately has none, so only flag when the body region it hugs
        # actually has jiggle weight (else it is a false positive).
        inrange = np.where(d < nc._CONFORM_VERT_PROX)[0]
        body_jig = sum(1 for i in inrange
                       if any(nc._is_physics_jiggle_scale_bone(b) and w > 0.1
                              for b, w in body_w[idx[i]].items()))
        info["body_jig"] = int(body_jig)
        info["jiggle_strip_suspect"] = (
            leg_frac > 0.5 and hug >= nc._CONFORM_FIT_FRAC
            and body_jig >= nc._CONFORM_MIN_JIGGLE_VERTS)
        return "RIGID", info
    chain_frac = sum(1 for dd in vw
                     if any(w > 0.1 and b not in body_bones
                            for b, w in dd.items())) / n
    info["chain_frac"] = chain_frac
    if chain_frac > nc._CONFORM_CHAIN_MAX:
        return "CHAIN", info
    if hug < nc._CONFORM_FIT_FRAC:
        return "LOOSE", info
    # GARMENT: measure how well its in-range verts are body-matched.
    matched = inrange = 0
    for i in range(n):
        if d[i] > nc._CONFORM_VERT_PROX:
            continue
        dv = vw[i]
        bd = body_w[idx[i]]
        shared = set(dv) & set(bd)
        if not shared:
            continue
        inrange += 1
        if max(abs(dv.get(b, 0.0) - bd.get(b, 0.0)) for b in shared) \
                <= nc._CONFORM_DELTA:
            matched += 1
    info["matched_frac"] = (matched / inrange) if inrange else 0.0
    info["region"] = "LEG" if leg_frac > 0.5 else "NONLEG"
    return "GARMENT", info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("armor_dir", help="converted output armor dir (walked for *.nif)")
    ap.add_argument("--body", default=None, help="UBE femalebody _1 NIF")
    ap.add_argument("--limit", type=int, default=0, help="cap NIFs scanned (0=all)")
    a = ap.parse_args()

    if a.body:
        import os
        os.environ["CBBE2UBE_UBE_BODY_1"] = a.body
    ref = nc._body_conform_ref("_1")
    if ref is None:
        print("ERROR: could not load the UBE body (pass --body or set "
              "CBBE2UBE_UBE_BODY_1 / CBBE2UBE_MO2_INI).")
        return 2
    _Vb, body_w, body_bones, tree = ref

    nifs = sorted(Path(a.armor_dir).rglob("*.nif"))
    if a.limit:
        nifs = nifs[:a.limit]
    counts = {"GARMENT": 0, "RIGID": 0, "CHAIN": 0, "LOOSE": 0, "SKIP": 0}
    match_sum = mismatch = strip_suspect = 0
    weak, torso = [], []
    for p in nifs:
        try:
            nf = pynifly.NifFile(filepath=str(p))
        except Exception:
            continue
        for s in nf.shapes:
            klass, info = classify_shape(s, body_w, body_bones, tree)
            counts[klass] = counts.get(klass, 0) + 1
            tag = f"{p.name}::{s.name}"
            if klass == "RIGID" and info.get("jiggle_strip_suspect"):
                strip_suspect += 1
                if len(weak) < 40:
                    weak.append(f"  [jiggle-strip?] {tag}  "
                                f"(leg_frac={info['leg_frac']:.2f} "
                                f"hug={info['hug']:.2f} "
                                f"body_jig={info.get('body_jig', 0)})")
            if klass == "GARMENT":
                mf = info["matched_frac"]
                match_sum += mf
                if mf < 0.5:
                    mismatch += 1
                    if len(weak) < 40:
                        weak.append(f"  [weak match {mf:.0%}] {tag}")
                if info["region"] == "NONLEG" and len(torso) < 40:
                    torso.append(f"  {tag}  (match={mf:.0%})")

    g = counts["GARMENT"]
    print(f"\nscanned {len(nifs)} NIFs")
    print("shape classes:")
    for k in ("GARMENT", "RIGID", "CHAIN", "LOOSE", "SKIP"):
        print(f"  {k:8s} {counts[k]}")
    if g:
        print(f"\nGARMENT health: mean body-match = {match_sum / g:.0%}  "
              f"({mismatch} weakly-matched <50%)")
    print(f"jiggle-strip suspects (leg cloth, hugs body, NO jiggle): {strip_suspect}")
    if weak:
        print("\nANOMALIES to eyeball:")
        print("\n".join(weak))
    if torso:
        print(f"\nNON-LEG (torso/hip) garments conformed ({len(torso)} shown) "
              "-- spot-check these (fitted tops/briefs, gate-validated only):")
        print("\n".join(torso))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
