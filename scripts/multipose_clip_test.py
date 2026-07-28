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
from scripts.posed_clip_test import (                           # noqa: E402
    build_pose, read_skin, bone_parents, apply_pose, rays_hit, _posed,
    DEFAULT_SKELETON)
from scripts.pose_set import (                                  # noqa: E402
    POSE_SET, REGION_POSES, REGIONS, ARM_X, MID_X)


def load(nif_path, skeleton=None):
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
    if 'BaseShape' not in data:
        raise SystemExit("no injected BaseShape in this nif -- nothing to measure")
    par = bone_parents(pynifly.NifFile(skel))
    garments = [n for n in data if n != 'BaseShape']
    _bv, _bt, body_w = data['BaseShape']
    origins = {b: o for b, (w, o) in body_w.items()}
    return data, garments, par, origins


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


def analyse(nif_path, skeleton=None, only_region=None, sample=400, seed=12345):
    """Per region: the worst pose and how much bind-coverage it loses.

    TWO THINGS MAKE THIS AFFORDABLE, and without them a population sweep is 31 hours:
      * pose ONCE, score every region from it. The obvious loop -- for each region,
        for each of its poses -- recomputes all six regions per pose and throws five
        away (42 posings instead of 13).
      * SAMPLE the region's verts. These are proportions over thousands of vertices;
        ray casting is the whole cost. Fixed seed, and the SAME vertex indices are
        used at bind and posed, so the regression is a paired comparison.
    """
    data, garments, par, origins = load(nif_path, skeleton)
    body_v, _body_t, body_w = data['BaseShape']

    # SELF-TEST first: identity must reproduce the bind mesh exactly, or the skinning
    # is wrong and every number below is meaningless.
    ident = float(np.abs(apply_pose(body_v, body_w, {}) - body_v).max())

    regions = [(n, s) for n, s in REGIONS if not only_region or n == only_region]
    # Fix each region's sampled vertex set ONCE, from the bind mesh, so bind and
    # posed states are compared over identical vertices.
    pb0, pbn0, GV0, GT0 = _posed(data, garments, {})
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
        pb, pbn, GV, GT = _posed(data, garments, acc)
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
