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

"""Which SHIPPED pieces could `CBBE2UBE_SMP_CHAIN_ANTIPOKE` reach, and which of
those cover the CONVEX BUST -- the population for its A/B.

    python scripts/chain_flag_census.py <output mod root> [-o pieces.json]

WHY THE BUST SPECIFICALLY. Two changes in this family have now been measured on
convex regions and both made them WORSE (`#smp-structural-relax` put pauldrons
from 5 to 9 verts inside the body). A flag that helps a concave rear band and
hurts a convex front one nets out to nothing pack-wide, and the rear win is what
gets noticed first. So the gate is the region the class has failed on, not the
region it was tuned on.

POPULATION, not sample. The candidate set is every converted NIF with a physics
XML beside it -- the flag's necessary precondition, since it only admits shapes
with HDT-SMP chain rigging. That is a superset of what the flag actually fires
on, deliberately: a candidate that turns out to be unreachable costs one
conversion, whereas a piece missing from the population is a hole in the gate
and invisible in the result.

Emits `pieces.json` in the shape `scripts/survival_sweep.py` consumes, so the
A/B runs over exactly the set this measured.
"""
import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                        # noqa: E402
from scripts import standoff_audit as sa                  # noqa: E402
# The bust band and its vert floor are IMPORTED, not restated. Two definitions of
# one concept drift -- `clipping_report` and `ClipTester` disagreed for a whole
# release that way -- and a census measuring a different band from the postflight
# it is compared against answers a different question.
from scripts.postflight_1_2 import FIT_BAND, FIT_MIN_VERTS  # noqa: E402
import src.nif_convert as nc                              # noqa: E402

COVERED_MIN = 25.0           # % of bust-front area the garment must cover


def _world(s):
    g2s = nc._shape_global_to_skin(s)
    return nc._verts_skin_to_world(np.asarray(s.verts, np.float64), g2s)


def _visible(nf, colliders):
    """The rendered garment shapes, as the fit measurement unions them."""
    out = []
    for s in nf.shapes:
        nm = s.name or ""
        if nm == "BaseShape" or nm in colliders or nc._is_inline_body_name(nm):
            continue
        if int(getattr(s, "flags", 0) or 0) & 0x1:
            continue                                  # hidden collider clone
        if not any(v for v in (s.textures or {}).values()):
            continue                                  # not rendered
        try:
            out.append((_world(s),
                        np.asarray(s.tris, np.int64).reshape(-1, 3)))
        except Exception:
            continue
    return out


def measure(path):
    """Bust-front fit of one shipped NIF, or a reason it cannot be judged."""
    pyn = nc._pynifly()
    nf = pyn.NifFile(filepath=str(path))
    body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if body is None:
        return {"skip": "no injected BaseShape"}
    parts = _visible(nf, set())
    if not parts:
        return {"skip": "no visible garment shape"}
    bV = _world(body)
    bT = np.asarray(body.tris, np.int64).reshape(-1, 3)
    bN = np.asarray(body.normals, np.float64)
    bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)
    zz = bV[:, 2]
    fmask = (zz >= FIT_BAND[0]) & (zz <= FIT_BAND[1]) & (bV[:, 1] > 2.0)
    if int(fmask.sum()) < FIT_MIN_VERTS:
        return {"skip": "bust band too small on this body"}
    gV = np.concatenate([v for v, _t in parts])
    off, tl = 0, []
    for v, t in parts:
        tl.append(t + off)
        off += len(v)
    gT = np.concatenate(tl)
    idx = np.flatnonzero(fmask)
    ct = sa.ClipTester(gV, gT)
    rp = ct.report(bV, bT, bN, idx, sa.vert_areas(bV, bT), oriented=True)
    if rp["covered_pct"] is None:
        return {"skip": "no bust area"}
    # STANDOFF IS NOT OPTIONAL HERE. The shipped bust reads 0.0000% clipping on
    # essentially this whole population, so clipping has no downward headroom
    # and an A/B on it alone can only ever say "no change". Clipping is also
    # unbounded above: a garment pushed three units off the body scores a
    # perfect 0.0% and was reported in game as overinflated, twice. The pair is
    # the measurement; either one alone has a blind side.
    so = ct.standoff(bV, bN, idx, tmax=12.0)
    return {"covered": round(float(rp["covered_pct"]), 2),
            "clip": round(float(rp["clipping_pct"]), 4),
            "coincident": round(float(rp["clip_coincident_pct"]), 4),
            "shallow": round(float(rp["clip_shallow_pct"]), 4),
            "buried": round(float(rp["clip_buried_pct"]), 4),
            "so_median": None if not len(so) else round(float(np.median(so)), 3),
            "so_p90": None if not len(so) else round(
                float(np.percentile(so, 90)), 3),
            "so_n": int(len(so))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="converted output mod root")
    ap.add_argument("-o", "--out", default="chain_bust_pieces.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    root = Path(args.root)
    meshes = root / "meshes"
    if not meshes.is_dir():
        raise SystemExit(f"no meshes/ under {root}")

    # Candidates: a physics XML beside the mesh is the flag's precondition.
    # Weight 1 only HERE (the pair converts together and the source stem is
    # shared); the A/B itself converts and measures BOTH weights.
    cands = []
    for xml in sorted(meshes.rglob("*.xml")):
        nif = xml.with_name(f"{xml.stem}_1.nif")
        if nif.is_file():
            cands.append(nif)
    if args.limit:
        cands = cands[:args.limit]
    print(f"{len(cands)} candidate piece(s) with a physics XML")

    rows, bust = [], []
    for i, p in enumerate(cands, 1):
        try:
            m = measure(p)
        except Exception as e:
            m = {"skip": f"{type(e).__name__}: {e}"}
        rel = p.relative_to(meshes).as_posix()
        sub = rel.rsplit("/", 1)[0]
        if sub.lower().startswith("!ube/"):
            sub = sub[5:]
        stem = p.stem[:-2]
        rows.append({"rel": rel, "subdir": sub, "stem": stem, **m})
        if m.get("covered", 0) >= COVERED_MIN:
            bust.append(rows[-1])
        if i % 25 == 0:
            print(f"  {i}/{len(cands)}  bust-covering so far: {len(bust)}")

    judged = [r for r in rows if "skip" not in r]
    print(f"\n{len(rows)} candidates, {len(judged)} judged, "
          f"{len(rows) - len(judged)} unjudged")
    print(f"{len(bust)} cover the convex bust (>= {COVERED_MIN}% of the "
          f"bust-front area)")
    if judged:
        c = np.array([r["clip"] for r in judged])
        print(f"shipped bust clipping over the judged set: median "
              f"{np.median(c):.3f}%  p90 {np.percentile(c, 90):.3f}%  "
              f"max {c.max():.3f}%")
    # slot 0 -> the sweep omits --slots and lets convert_one_armor resolve them
    # from the plugins, exactly as the batch does. Hard-coding a mask here would
    # be the slots=0 bug wearing a different hat.
    Path(args.out).write_text(json.dumps(
        [[r["stem"] + "|" + r["subdir"].replace("/", "_"), r["subdir"],
          r["stem"], 0, f"bust-covering {r['covered']}%"] for r in bust],
        indent=1), encoding="utf-8")
    Path(args.out).with_suffix(".census.json").write_text(
        json.dumps(rows, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({len(bust)} pieces) and the full census beside it")
    return 0 if bust else 2


if __name__ == "__main__":
    raise SystemExit(main())
