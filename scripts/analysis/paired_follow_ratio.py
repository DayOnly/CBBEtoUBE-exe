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

"""DEVELOPMENT TOOL -- the over-follow claim, read PAIRED and unpaired. #paired-follow

    python -m scripts.analysis.paired_follow_ratio <arm_dir> [--band bust|butt|belly]
        [--limit N] [--piece SUBSTRING] [--json OUT.json]

TWO READINGS OF ONE NUMBER, AND THEY DISAGREE. The 2026-08-01 note took, per
slider, the garment band's mean displacement over the body band's mean
displacement on ONE piece -- 1.25x to 3.7x, "the garment over-follows" -- and
proposed rescaling the morph deltas of every converted .tri. A later reading on a
different piece paired each garment vertex with ITS NEAREST body vertex and came
back 1.00 across every breast slider, while the unpaired form of the same number
read 0.69-0.72 on the same data. Nothing reconciles them, and the change they
motivate would touch every shipped .tri.

So this prints both, side by side, per piece and per slider, over an arm:

    paired    median over garment band verts of |garment delta| / |delta of the
              nearest body vert|. A vertex-to-vertex reading: 1.0 means each
              covered point moves with the point it covers.
    unpaired  mean |garment delta| over the band / mean |body delta| over the
              band. Two different populations: if the garment's band verts sit
              over a part of the body that moves less (or more) than the band
              average, this reads a ratio that no vertex has.

The gap between the columns IS the finding. Judge the class on `paired`.

CONTROLS, because a harness that measures nothing reads like a clean result:
  * the body must carry morphs for the slider, and the body band must move
    (a median above BODY_MOVE_FLOOR) or the slider is skipped and counted;
  * a garment vertex whose paired body vertex barely moves is dropped (the ratio
    is a division by noise) and the dropped fraction is printed;
  * pieces with no .tri at all are counted separately -- they cannot follow by
    this mechanism, which is a different defect (see find_morph_follow_gaps.py);
  * the run aborts if fewer than MIN_PIECES pieces produced a ratio.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.analysis import morph_clip_test as mct     # noqa: E402
from scripts.analysis._census_common import baked_verts  # noqa: E402
from src import body_zones                              # noqa: E402

BODY_MOVE_FLOOR = 0.05      # u: a slider that moves the body band less is noise
PAIR_MOVE_FLOOR = 0.01      # u: a paired body vert below this is a division by noise
# The two differ on purpose: a single vertex can sit still while the band moves
# (drop that pair), but a BAND that barely moves cannot divide anything at all
# (report no ratio) -- and a band-wide floor set to the per-vertex one would let
# a slider whose whole band twitches by 0.02u produce a table of ratios.
MIN_PIECES = 5
_MASKS = {"bust": body_zones.breast_mask, "butt": body_zones.butt_mask,
          "belly": body_zones.belly_mask}


def ratios(body_v, body_d, garm_v, garm_d, mask_fn,
           pair_floor=PAIR_MOVE_FLOOR, body_floor=BODY_MOVE_FLOOR):
    """Both readings for one slider on one shape, or None when the body band
    does not move enough to divide by.

    Returns {"paired": p50, "unpaired": r, "n_pairs": n, "dropped": frac,
             "body_band": n, "garment_band": n}.
    """
    body_v, body_d = np.asarray(body_v, float), np.asarray(body_d, float)
    garm_v, garm_d = np.asarray(garm_v, float), np.asarray(garm_d, float)
    b_band = np.asarray(mask_fn(body_v), bool)
    g_band = np.asarray(mask_fn(garm_v), bool)
    if not b_band.any() or not g_band.any():
        return None
    b_mag = np.linalg.norm(body_d, axis=1)
    if float(np.median(b_mag[b_band])) < body_floor:
        return None
    g_mag = np.linalg.norm(garm_d, axis=1)
    # PAIRED: each garment band vert against the body vert it covers. Nearest
    # over the WHOLE body, not the band -- a garment vert may sit over a body
    # vert just outside the zone, and forcing it into the band would invent a
    # pairing.
    _d, idx = cKDTree(body_v).query(garm_v[g_band], k=1)
    partner = b_mag[idx]
    keep = partner >= pair_floor
    if not keep.any():
        return None
    paired = float(np.median(g_mag[g_band][keep] / partner[keep]))
    unpaired = float(g_mag[g_band].mean() / b_mag[b_band].mean())
    return {"paired": paired, "unpaired": unpaired, "n_pairs": int(keep.sum()),
            "dropped": float(1.0 - keep.mean()), "body_band": int(b_band.sum()),
            "garment_band": int(g_band.sum())}


def _rows_for_nif(path, mask_fn, body_dense):
    """[(shape, slider, ratios)] for one converted NIF, plus why it was skipped."""
    pyn = mct.nc._pynifly()
    try:
        nf = pyn.NifFile(filepath=str(path))
    except Exception as e:
        return [], f"unreadable: {type(e).__name__}"
    body = next((s for s in nf.shapes if (s.name or "") == "BaseShape"), None)
    if body is None:
        return [], "no injected body shape"
    bV = baked_verts(body)
    bm, _src = body_dense(len(bV))
    if not bm:
        return [], "no body morphs"
    out = []
    for s in nf.shapes:
        nm = s.name or ""
        if nm == "BaseShape" or mct.nc._is_inline_body_name(nm):
            continue
        gV = baked_verts(s)
        gm, tri = mct.garment_morphs(path, nm, len(gV))
        if not gm:
            out.append((nm, None, None))        # no .tri morphs at all
            continue
        for slider, b_d in bm.items():
            key = mct._match(slider, list(gm))
            if key is None:
                continue
            r = ratios(bV, b_d, gV, gm[key], mask_fn)
            if r:
                out.append((nm, slider, r))
    return out, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arm", help="an arm directory from parity_convert")
    ap.add_argument("--band", default="bust", choices=sorted(_MASKS))
    ap.add_argument("--limit", type=int, default=0, help="stop after N NIFs")
    ap.add_argument("--piece", default="", help="only NIFs whose path contains this")
    ap.add_argument("--json", default="", help="write every row here")
    a = ap.parse_args(argv)

    nifs = sorted(p for p in Path(a.arm).rglob("*.nif") if a.piece.lower() in str(p).lower())
    if not nifs:
        print(f"no NIFs under {a.arm}", file=sys.stderr)
        return 2
    mask_fn = _MASKS[a.band]
    rows, no_tri, skipped = [], 0, {}
    for p in nifs[:a.limit or None]:
        got, why = _rows_for_nif(p, mask_fn, mct.body_morphs)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        for shape, slider, r in got:
            if slider is None:
                no_tri += 1
            else:
                rows.append({"nif": str(p), "shape": shape, "slider": slider, **r})

    pieces = sorted({r["nif"] for r in rows})
    print(f"\n{len(nifs)} NIF(s) read, {len(pieces)} with a {a.band} ratio, "
          f"{no_tri} shape(s) with no morph data at all")
    for why, n in sorted(skipped.items()):
        print(f"  skipped {n}: {why}")
    if len(pieces) < MIN_PIECES:
        print(f"ABORT: only {len(pieces)} piece(s) produced a ratio, below the floor "
              f"of {MIN_PIECES} -- this measured nothing", file=sys.stderr)
        return 3

    print(f"\n{'piece / shape':<58} {'slider':<22} {'paired':>7} {'unpaired':>9} {'drop%':>6}")
    per_piece = {}
    for r in sorted(rows, key=lambda r: (r["nif"], r["shape"], r["slider"])):
        label = f"{Path(r['nif']).name} / {r['shape']}"
        print(f"{label[:58]:<58} {r['slider'][:22]:<22} {r['paired']:7.3f} "
              f"{r['unpaired']:9.3f} {100 * r['dropped']:6.1f}")
        per_piece.setdefault(r["nif"], []).append(r)

    med = {p: float(np.median([r["paired"] for r in rs])) for p, rs in per_piece.items()}
    unp = {p: float(np.median([r["unpaired"] for r in rs])) for p, rs in per_piece.items()}
    within = [p for p, v in med.items() if 0.9 <= v <= 1.1]
    print(f"\nPAIRED median per piece: {min(med.values()):.3f} .. {max(med.values()):.3f}; "
          f"{len(within)} of {len(med)} pieces within 0.9-1.1 "
          f"({100 * len(within) / len(med):.0f}%)")
    print(f"UNPAIRED median per piece: {min(unp.values()):.3f} .. {max(unp.values()):.3f}")
    print("\nDecision rule (plan D-11): paired within 0.9-1.1 on >= 90% of pieces closes "
          "the over-follow memory as an unpaired artefact; otherwise the class is real.")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"rows -> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
