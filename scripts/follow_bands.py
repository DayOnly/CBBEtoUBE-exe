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

"""DEVELOPMENT TOOL -- FOLLOW ratio per anatomical band. Not part of the exe.

Motion clipping is a FOLLOW defect, not a clearance one: the garment has to
track the body it covers. For each garment vert, find the body vert it covers at
bind, pose BOTH with the same bone deltas (what the engine does), and take

    follow = dot(garment_displacement, body_direction) / |body_displacement|

    1.0  tracks its body point exactly
    <1   UNDER-follows; the body slides out from under it -- what shows as
         clipping in motion, and completely invisible at bind pose
    >1   travels further than the skin. Expected, not a fault: the ratio
         normalises by the BODY's displacement and a garment vert sits further
         from the rotation axis. It rises with distance-off-body.

WHY THIS EXISTS AS A REPO SCRIPT. Two earlier scratchpad versions of this
measurement produced CLEAN-LOOKING NUMBERS FROM NOTHING, and both cost a wrong
conclusion:

  * they picked "the body" as the largest shape in the NIF. A piece that ships
    with no injected BaseShape -- 110 of 150 sampled outputs, and every boot and
    gauntlet -- has only the garment, so the garment was measured against
    ITSELF. That reports a plausible table, not an error.
  * they scored SMP-SIMULATED cloth. Kinematic follow is meaningless on a vert
    an XML chain drives at runtime, and a skirt reads 0.000 / "100% failing"
    for behaviour that is correct. One such band was reported as a real defect.

So this version REFUSES rather than guesses, and labels contamination it cannot
refuse. Every abort below is a bug that already happened.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / ".pynifly"))

from posed_clip_test import (build_pose, read_skin, bone_parents,        # noqa: E402
                             apply_pose, DEFAULT_SKELETON)
from pose_set import POSE_SET as POSES                                   # noqa: E402
from pyn import pynifly                                                  # noqa: E402
# Anatomy comes from ONE place. Four diagnostic scripts once each re-derived a
# "breast band" by eye and four were wrong, so the bands live in src.body_zones
# and a test refuses any script that hardcodes its own.
from src.body_zones import (ARMHOLE_Z, ARMHOLE_HALF_X, SIDE_Z,           # noqa: E402
                            SIDE_HALF_X, UNDER_BUST_Z, BREAST_Z, FRONT_Y,
                            FOREARM_Z, FOREARM_HALF_X,
                            HIP_Z, THIGH_Z, KNEE_Z, CALF_Z)

MIN_BODY_MOVE = 0.25        # u -- below this the covered body point barely moves
MIN_BAND_VERTS = 20         # fewer than this and the band says nothing
MIN_BODY_VERTS = 5000       # a real body reference; a garment is far smaller

def _z(v, span):
    return (v[:, 2] >= span[0]) & (v[:, 2] < span[1])


def _lateral(v, half_x):
    return np.abs(v[:, 0]) >= half_x


BANDS = {
    "armhole":    (lambda v: _z(v, ARMHOLE_Z) & _lateral(v, ARMHOLE_HALF_X),
                   ["arms down", "arms forward", "arms crossed", "bow draw",
                    "sprint"]),
    "side":       (lambda v: _z(v, SIDE_Z) & _lateral(v, SIDE_HALF_X),
                   ["arms down", "arms crossed", "spine side bend",
                    "spine twist", "sprint"]),
    "under-bust": (lambda v: _z(v, UNDER_BUST_Z) & (v[:, 1] > 0.0),
                   ["spine fwd lean", "spine side bend", "spine twist",
                    "sprint"]),
    "bust":       (lambda v: _z(v, BREAST_Z) & (v[:, 1] > 0.0),
                   ["spine fwd lean", "spine twist", "bow draw", "sprint"]),
    "forearm":    (lambda v: _z(v, FOREARM_Z) & _lateral(v, FOREARM_HALF_X),
                   ["arms crossed", "bow draw", "arms down", "sprint"]),
    "hip":        (lambda v: _z(v, HIP_Z),
                   ["stride", "deep stride", "crouch", "walk + lean", "sprint"]),
    "thigh":      (lambda v: _z(v, THIGH_Z),
                   ["stride", "deep stride", "knee bend", "crouch", "sprint"]),
    "knee":       (lambda v: _z(v, KNEE_Z),
                   ["stride", "knee bend", "crouch", "sprint"]),
    "calf":       (lambda v: _z(v, CALF_Z),
                   ["stride", "knee bend", "crouch", "sprint"]),
}


class HarnessRefusal(SystemExit):
    """Raised instead of returning a number the caller would misread."""


def _xml_driven_bones(nif_path: Path) -> set:
    """Bones an HDT-SMP XML declares for this piece, if any.

    A vert weighted to one of these is SIMULATED at runtime; kinematic follow
    does not describe it and must not be reported as if it did.
    """
    stem = nif_path.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    xml = nif_path.parent / f"{stem}.xml"
    if not xml.is_file():
        return set()
    try:
        txt = xml.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return set(re.findall(r'<bone name="([^"]+)"', txt))


def resolve_body(nif, nif_path: Path, explicit: str | None):
    """(verts, weights, label) for the body to measure against.

    Order: explicit --body, then an injected BaseShape, then the external UBE
    reference. NEVER 'the largest shape in the NIF' -- that is the heuristic
    that measured a boot against itself.
    """
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise HarnessRefusal(f"REFUSED: --body {p} does not exist")
        bnf = pynifly.NifFile(filepath=str(p))
        sh = next((s for s in bnf.shapes if s.name == "BaseShape"), None)
        if sh is None:
            sh = max(bnf.shapes, key=lambda s: len(s.verts))
        v, _t, w = read_skin(sh)
        return np.asarray(v, float), w, f"--body {p.name}:{sh.name}"

    base = next((s for s in nif.shapes if s.name == "BaseShape"), None)
    if base is not None:
        v, _t, w = read_skin(base)
        return np.asarray(v, float), w, "injected BaseShape"

    try:
        from src import auto_convert as ac
        ref = ac._find_ube_body_ref()
    except Exception as exc:                                # pragma: no cover
        raise HarnessRefusal(
            f"REFUSED: {nif_path.name} has no injected BaseShape and the "
            f"external UBE body reference could not be located ({exc!r}). "
            f"Pass --body <femalebody_N.nif> explicitly.")
    if not ref or not Path(ref).is_file():
        raise HarnessRefusal(
            f"REFUSED: {nif_path.name} has no injected BaseShape and no "
            f"external UBE body reference was found. Pass --body explicitly. "
            f"(Measuring a garment against itself is what this refusal exists "
            f"to prevent.)")
    bnf = pynifly.NifFile(filepath=str(ref))
    sh = next((s for s in bnf.shapes if s.name == "BaseShape"), None)
    if sh is None:
        raise HarnessRefusal(
            f"REFUSED: external body reference {Path(ref).name} has no "
            f"BaseShape to measure against.")
    v, _t, w = read_skin(sh)
    return np.asarray(v, float), w, f"external {Path(ref).name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("nif")
    ap.add_argument("shape", nargs="?", default=None,
                    help="garment shape; default = every non-body shape")
    ap.add_argument("--body", default=None,
                    help="explicit body NIF to measure against")
    ap.add_argument("--bands", default=None,
                    help="comma-separated subset of " + ",".join(BANDS))
    args = ap.parse_args(argv)

    nif_path = Path(args.nif)
    if not nif_path.is_file():
        raise HarnessRefusal(f"REFUSED: {nif_path} does not exist")
    nif = pynifly.NifFile(filepath=str(nif_path))

    skel = os.environ.get("CBBE2UBE_SKELETON_NIF", DEFAULT_SKELETON)
    if not skel or not Path(skel).is_file():
        raise HarnessRefusal(
            "REFUSED: no skeleton NIF. Set CBBE2UBE_SKELETON_NIF. Armor NIFs "
            "carry a FLAT bone list, so without a real skeleton the bone "
            "HIERARCHY is missing and everything below the posed joint "
            "silently fails to move -- which reads as a follow failure.")
    par = bone_parents(pynifly.NifFile(skel))

    BV, BW, body_label = resolve_body(nif, nif_path, args.body)
    if len(BV) < MIN_BODY_VERTS:
        raise HarnessRefusal(
            f"REFUSED: resolved body ({body_label}) has only {len(BV)} verts. "
            f"That is a garment, not a body -- measuring against it compares a "
            f"shape to itself.")

    driven = _xml_driven_bones(nif_path)
    origins = {b: o for b, (w, o) in BW.items()}
    tree = cKDTree(BV)

    targets = ([args.shape] if args.shape else
               [s.name for s in nif.shapes if s.name != "BaseShape"])
    want = ([b.strip() for b in args.bands.split(",")] if args.bands
            else list(BANDS))

    print(f"{nif_path.name}   body: {body_label} ({len(BV)} verts)")
    zero = build_pose(par, origins, [])
    sb = float(np.abs(apply_pose(BV, BW, zero) - BV).max())
    if sb > 1e-4:
        raise HarnessRefusal(
            f"REFUSED: identity self-test moved the body {sb:.6f}u. The posing "
            f"path is broken; every number below would be noise.")
    print(f"  identity self-test: body {sb:.6f}u OK")

    acc = {p: build_pose(par, origins, POSES[p]) for p in POSES}
    dbc = {p: apply_pose(BV, BW, acc[p]) - BV for p in POSES}
    any_row = False

    for name in targets:
        sh = next((s for s in nif.shapes if s.name == name), None)
        if sh is None:
            print(f"  shape {name!r} not in this NIF -- skipped")
            continue
        GVr, _t, GW = read_skin(sh)
        GV = np.asarray(GVr, float)
        if len(GV) < MIN_BAND_VERTS:
            continue
        sg = float(np.abs(apply_pose(GV, GW, zero) - GV).max())
        if sg > 1e-4:
            print(f"  {name}: REFUSED, identity self-test moved it {sg:.6f}u")
            continue
        # A CHAIN bone is one the XML drives that the BODY does not have.
        # "Weighted to any bone the XML names" is far too broad -- a generated
        # XML declares Spine2, Clavicle and the rest of the skeleton as
        # collision bodies, so that test marks 100% of every band as simulated
        # and hides the real contamination. Same over-broad-predicate mistake
        # that made the XML-bone row gate reject 1601 of 1601 rows.
        sim = np.zeros(len(GV))
        for b, (w, _o) in GW.items():
            if b in driven and b not in BW:
                sim += w
        _, cover = tree.query(GV, k=1)
        rows = []
        for band in want:
            sel, poses = BANDS[band]
            mask = sel(GV)
            if mask.sum() < MIN_BAND_VERTS:
                continue
            for pname in poses:
                mv = np.linalg.norm(dbc[pname][cover], axis=1)
                use = mask & (mv > MIN_BODY_MOVE)
                if use.sum() < MIN_BAND_VERTS:
                    continue
                d = dbc[pname][cover][use]
                n = np.linalg.norm(d, axis=1, keepdims=True)
                dg = apply_pose(GV, GW, acc[pname]) - GV
                r = (dg[use] * (d / n)).sum(1) / n[:, 0]
                pct = 100.0 * float((sim[use] > 1e-4).mean())
                rows.append((band, pname, int(use.sum()), float(np.median(r)),
                             float(np.percentile(r, 10)),
                             float((r < 0.7).mean() * 100),
                             float((r < 0.5).mean() * 100), pct))
        if not rows:
            continue
        any_row = True
        print(f"\n  shape `{name}` ({len(GV)} verts)")
        print(f"  {'band':<11}{'pose':<16}{'n':>6}{'median':>9}{'p10':>8}"
              f"{'<0.7':>8}{'<0.5':>8}{'SIM':>7}")
        for band, pname, n, med, p10, u7, u5, pct in rows:
            warn = "  <-- SIMULATED, follow is meaningless" if pct >= 50 else ""
            print(f"  {band:<11}{pname:<16}{n:>6}{med:>9.3f}{p10:>8.3f}"
                  f"{u7:>7.1f}%{u5:>7.1f}%{pct:>6.0f}%{warn}")
        bad = [r for r in rows if r[7] < 50]
        if bad:
            w = min(bad, key=lambda r: r[3])
            print(f"  {'':11}WORST (excluding simulated): {w[0]}/{w[1]} "
                  f"median {w[3]:.3f}")
        else:
            print(f"  {'':11}every band is SMP-simulated -- this harness "
                  f"cannot judge this shape")

    if not any_row:
        raise HarnessRefusal(
            "REFUSED: nothing was measured -- no shape had a band with "
            f"{MIN_BAND_VERTS}+ moving verts. An empty result is not a clean "
            "result; check the shape name and the band set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
