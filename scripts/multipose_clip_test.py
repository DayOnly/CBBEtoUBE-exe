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

"""Will this garment clip in ANY pose a player actually strikes -- per body region.

    python scripts/multipose_clip_test.py <converted.nif>
    python scripts/multipose_clip_test.py <converted.nif> --region breast
    python scripts/multipose_clip_test.py <converted.nif> --json

THE MEASURE IS A REGRESSION, NOT AN EXPOSURE LEVEL. For each region it counts body
verts that are COVERED at bind and EXPOSED under a pose, as a fraction of the covered
set. Raw exposure cannot be compared across garments -- a bikini is 90% exposed by
design and a full robe 0%, neither of which is a defect. Each garment is its own
baseline, so only pose-INDUCED loss of coverage counts. (`posed_clip_test.py`
established this; the only thing added here is that it runs over a POSE SET and every
region rather than one stride and the legs.)

WHY IT EXISTS. Bind pose is an A-pose nobody stands in, and `posed_clip_test` moved
only thighs and calves -- so the torso, and with it the whole chest, was still judged
at bind. A day of measurement produced region rankings that could not see any
pose-induced clipping at all.

SELF-TEST. The identity pose must reproduce the bind mesh exactly; it is asserted on
every run and prints as `identity check`. If that is not ~0, the skinning is wrong and
nothing below it means anything.

WHAT IT STILL DOES NOT DO. It poses but does not MORPH. The runtime body is
BaseShape + BodyMorph/OBody deltas, which can inflate bust/butt past the bind body,
so at-rest clipping this harness cannot reproduce remains the morph path's suspect.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                              # noqa: E402

from pyn import pynifly                                         # noqa: E402
from src.nif_convert import UBE_BODY_INJECT_NAMES               # noqa: E402
from scripts.posed_clip_test import (                           # noqa: E402
    build_pose, read_skin, bone_parents, apply_pose, rays_hit, vert_normals,
    DEFAULT_SKELETON)


def _posed(data, garments, acc, body_name):
    """Pose the body and every garment with the SAME bone deltas.

    `posed_clip_test._posed` hardcodes `BaseShape`; this takes the body's name so the
    same code can pose a SOURCE nif, whose author named it `body` / `CBBE` / whatever.
    """
    body_v, body_t, body_w = data[body_name]
    pb = apply_pose(body_v, body_w, acc)
    GV, GT, off = [], [], 0
    for g in garments:
        gv, gt, gw = data[g]
        GV.append(apply_pose(gv, gw, acc))
        GT.append(gt + off)
        off += len(gv)
    return pb, vert_normals(pb, body_t), np.vstack(GV), np.vstack(GT)
from scripts.pose_set import (                                  # noqa: E402
    POSE_SET, REGION_POSES, REGIONS, ARM_X, MID_X)


def pick_body_shape(nif):
    """Which shape is the BODY -- works on a SOURCE nif as well as our output.

    Our output names it `BaseShape`; a source names it whatever the author chose
    (`body`, `CBBE`, `CBBE Body`, ...), so a name list would need extending forever
    and would silently pick nothing when it missed. Detect it structurally instead:
    the body is the shape with the most vertices that also reaches the FLOOR (it has
    legs). Collision/proxy hulls also reach the floor but carry two orders of
    magnitude fewer verts, so max-verts breaks the tie.
    """
    best, best_n = None, -1
    zfloor = None
    for s in nif.shapes:
        try:
            v = np.asarray(s.verts, dtype=np.float64)
        except Exception:
            continue
        if not len(v):
            continue
        zfloor = v[:, 2].min() if zfloor is None else min(zfloor, v[:, 2].min())
    for s in nif.shapes:
        if s.name in UBE_BODY_INJECT_NAMES:
            return s.name
        try:
            v = np.asarray(s.verts, dtype=np.float64)
        except Exception:
            continue
        if len(v) > best_n and zfloor is not None and v[:, 2].min() <= zfloor + 3.0:
            best, best_n = s.name, len(v)
    return best


def load(nif_path, skeleton=None, body_name=None):
    """Mirror `posed_clip_test.main`'s loading exactly.

    Two things that are easy to get wrong and silently produce a no-op pose:
      * `par` MUST come from the real SKELETON nif -- an armour NIF's bone list is
        FLAT, so a calf never resolves as a child of a thigh and nothing below the
        joint moves;
      * bone ORIGINS come from the MESH's own bind transforms, not the skeleton.
    """
    skel = skeleton or DEFAULT_SKELETON
    if not skel or not Path(skel).is_file():
        # Fall back to pose_engine's glob over the mods tree, which finds XPMSSE
        # without any environment set. Requiring an env var here is how a harness
        # ends up unused.
        try:
            from scripts.pose_engine import load_skeleton
            skel = load_skeleton()[2]
        except Exception:
            skel = None
    if not skel or not Path(skel).is_file():
        raise SystemExit(
            "No skeleton NIF found. Set CBBE2UBE_SKELETON_NIF (or "
            "CBBE2UBE_GAME_DATA / CBBE2UBE_MODS_ROOT), or pass --skeleton.")
    nif = pynifly.NifFile(str(nif_path))
    data = {s.name: read_skin(s) for s in nif.shapes}
    bn = body_name or pick_body_shape(nif)
    if bn not in data:
        raise SystemExit(f"no body shape found in {Path(nif_path).name}")
    # Physics helpers are not the visible garment; counting them as coverage makes
    # the body read as protected where nothing renders.
    garments = [n for n in data if n != bn
                and n.strip().lower() not in ("collision", "hidecollision", "proxy",
                                              "proxy2", "proxy3", "stabilizer")]
    if not garments:
        raise SystemExit(f"no garment shapes in {Path(nif_path).name}")
    par = bone_parents(pynifly.NifFile(skel))
    _bv, _bt, body_w = data[bn]
    origins = {b: o for b, (w, o) in body_w.items()}
    return data, garments, par, origins, bn


def region_visible(body, bn, GV, GT, sel):
    """Body verts in `sel` whose outward ray escapes the garment.

    The arm and midline exclusions are not optional -- without them the midline
    crevice reads exposed in EVERY pose including bind and buries the signal.
    """
    z, ny = body[:, 2], bn[:, 1]
    m = sel(z, ny) & (np.abs(body[:, 0]) < ARM_X) & (np.abs(body[:, 0]) > MID_X)
    idx = np.flatnonzero(m)
    if not len(idx):
        return idx, np.zeros(0, dtype=bool)
    return idx, ~rays_hit(body[idx], bn[idx], GV, GT)


def analyse(nif_path, skeleton=None, only_region=None, sample=400, seed=12345,
            body_name=None):
    """Per region: the worst pose and how much bind-coverage it loses.

    TWO THINGS MAKE THIS AFFORDABLE, and without them a population sweep is 31 hours:
      * pose ONCE, score every region from it. The obvious loop -- for each region,
        for each of its poses -- recomputes all six regions per pose and throws five
        away (42 posings instead of 13).
      * SAMPLE the region's verts. These are proportions over thousands of vertices;
        ray casting is the whole cost. Fixed seed, and the SAME vertex indices are
        used at bind and posed, so the regression is a paired comparison.
    """
    data, garments, par, origins, bname = load(nif_path, skeleton, body_name)
    body_v, _body_t, body_w = data[bname]

    # SELF-TEST first: identity must reproduce the bind mesh exactly, or the skinning
    # is wrong and every number below is meaningless.
    ident = float(np.abs(apply_pose(body_v, body_w, {}) - body_v).max())

    regions = [(n, s) for n, s in REGIONS if not only_region or n == only_region]
    # Fix each region's sampled vertex set ONCE, from the bind mesh, so bind and
    # posed states are compared over identical vertices.
    pb0, pbn0, GV0, GT0 = _posed(data, garments, {}, bname)
    rng = np.random.default_rng(seed)
    picks, base = {}, {}
    for name, sel in regions:
        z, ny = pb0[:, 2], pbn0[:, 1]
        m = (sel(z, ny) & (np.abs(pb0[:, 0]) < ARM_X)
             & (np.abs(pb0[:, 0]) > MID_X))
        idx = np.flatnonzero(m)
        if len(idx) > sample:
            idx = np.sort(rng.choice(idx, size=sample, replace=False))
        picks[name] = idx
        base[name] = (~rays_hit(pb0[idx], pbn0[idx], GV0, GT0)
                      if len(idx) else np.zeros(0, dtype=bool))

    # Which poses does anyone actually need?
    needed = {p for n, _s in regions for p in REGION_POSES.get(n, [])
              if POSE_SET.get(p)}
    scores = {n: [] for n, _s in regions}
    for pose in sorted(needed):
        acc = build_pose(par, origins, POSE_SET[pose])
        pb, pbn, GV, GT = _posed(data, garments, acc, bname)
        for name, _sel in regions:
            if pose not in REGION_POSES.get(name, []):
                continue
            idx = picks[name]
            if not len(idx):
                continue
            exp = ~rays_hit(pb[idx], pbn[idx], GV, GT)
            covered = ~base[name]                    # covered at bind
            newly = covered & exp
            scores[name].append(
                (pose, int(newly.sum()),
                 100.0 * newly.sum() / max(int(covered.sum()), 1)))

    results = {}
    for name, _sel in regions:
        rows = sorted(scores[name], key=lambda r: -r[2])
        results[name] = {
            "sampled": int(len(picks[name])),
            "covered_at_bind": int((~base[name]).sum()),
            "worst_pose": rows[0][0] if rows else None,
            "worst_pct": round(rows[0][2], 3) if rows else 0.0,
            "worst_verts": rows[0][1] if rows else 0,
            "per_pose": [{"pose": p, "verts": v, "pct": round(q, 3)} for p, v, q in rows],
        }
    return results, ident


def main():
    argv = sys.argv[1:]
    as_json = '--json' in argv
    if as_json:
        argv.remove('--json')
    region = None
    if '--region' in argv:
        i = argv.index('--region')
        region = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        print(__doc__)
        sys.exit(1)

    res, ident = analyse(argv[0], only_region=region)
    if as_json:
        print(json.dumps({"armor": argv[0], "identity_check": ident,
                          "regions": res}, indent=1))
        return
    print(f"{Path(argv[0]).name}   identity check {ident:.6f}u "
          f"({'OK' if ident < 1e-4 else 'FAIL -- skinning is wrong, ignore all below'})")
    print(f"\n{'region':<13}{'covered':>9}{'worst pose':>18}{'newly exposed':>15}{'%':>8}")
    for name, r in res.items():
        print(f"{name:<13}{r['covered_at_bind']:>9}{str(r['worst_pose']):>18}"
              f"{r['worst_verts']:>15}{r['worst_pct']:>8.2f}")
    if region:
        print(f"\nall poses for {region}:")
        for p in res[region]["per_pose"]:
            print(f"  {p['pose']:<18}{p['verts']:>7}{p['pct']:>8.2f}%")


if __name__ == "__main__":
    main()


def analyse_with_body(garment_path, garment_names, body_path, body_shape,
                      skeleton=None, sample=400, seed=12345, only_region=None):
    """Same measurement, but the BODY comes from a canonical reference NIF.

    Pairing a garment with whatever body its own file bundles is biased -- sources
    bundle different bodies, or none. Taking the body from a fixed reference makes
    the source and converted sides differ only in what the converter changed.
    `garment_names` is supplied by the caller (matched by name across the two sides),
    so no "which shape is the body" heuristic is involved on either side.
    """
    skel = skeleton or DEFAULT_SKELETON
    if not skel or not Path(skel).is_file():
        try:
            from scripts.pose_engine import load_skeleton
            skel = load_skeleton()[2]
        except Exception:
            skel = None
    if not skel or not Path(skel).is_file():
        raise SystemExit("no skeleton NIF found")

    gnif = pynifly.NifFile(str(garment_path))
    have = {s.name: s for s in gnif.shapes}
    missing = [n for n in garment_names if n not in have]
    if missing:
        raise SystemExit(f"garment shapes absent from {Path(garment_path).name}: {missing}")
    bnif = pynifly.NifFile(str(body_path))
    bsh = next((s for s in bnif.shapes if s.name == body_shape), None)
    if bsh is None:
        raise SystemExit(f"body shape {body_shape!r} not in {Path(body_path).name}")

    BODY = "__body__"
    data = {BODY: read_skin(bsh)}
    for n in garment_names:
        data[n] = read_skin(have[n])
    par = bone_parents(pynifly.NifFile(skel))
    body_v, _bt, body_w = data[BODY]
    origins = {b: o for b, (w, o) in body_w.items()}

    ident = float(np.abs(apply_pose(body_v, body_w, {}) - body_v).max())
    regions = [(n, s) for n, s in REGIONS if not only_region or n == only_region]
    pb0, pbn0, GV0, GT0 = _posed(data, list(garment_names), {}, BODY)
    rng = np.random.default_rng(seed)
    picks, base = {}, {}
    for name, sel in regions:
        z, ny = pb0[:, 2], pbn0[:, 1]
        m = (sel(z, ny) & (np.abs(pb0[:, 0]) < ARM_X) & (np.abs(pb0[:, 0]) > MID_X))
        idx = np.flatnonzero(m)
        if len(idx) > sample:
            idx = np.sort(rng.choice(idx, size=sample, replace=False))
        picks[name] = idx
        base[name] = (~rays_hit(pb0[idx], pbn0[idx], GV0, GT0)
                      if len(idx) else np.zeros(0, dtype=bool))

    needed = {p for n, _s in regions for p in REGION_POSES.get(n, []) if POSE_SET.get(p)}
    scores = {n: [] for n, _s in regions}
    for pose in sorted(needed):
        acc = build_pose(par, origins, POSE_SET[pose])
        pb, pbn, GV, GT = _posed(data, list(garment_names), acc, BODY)
        for name, _sel in regions:
            if pose not in REGION_POSES.get(name, []):
                continue
            idx = picks[name]
            if not len(idx):
                continue
            exp = ~rays_hit(pb[idx], pbn[idx], GV, GT)
            covered = ~base[name]
            newly = covered & exp
            scores[name].append((pose, int(newly.sum()),
                                 100.0 * newly.sum() / max(int(covered.sum()), 1)))
    out = {}
    for name, _sel in regions:
        rows = sorted(scores[name], key=lambda r: -r[2])
        out[name] = {"sampled": int(len(picks[name])),
                     "covered_at_bind": int((~base[name]).sum()),
                     "worst_pose": rows[0][0] if rows else None,
                     "worst_pct": round(rows[0][2], 3) if rows else 0.0,
                     "worst_verts": rows[0][1] if rows else 0,
                     "per_pose": [{"pose": p, "verts": v, "pct": round(q, 3)}
                                  for p, v, q in rows]}
    return out, ident
