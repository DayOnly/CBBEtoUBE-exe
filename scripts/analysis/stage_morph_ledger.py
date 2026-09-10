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

"""WHICH PASS creates the clip that only appears UNDER A BODY PRESET.

    python scripts/analysis/stage_morph_ledger.py <stage.npz> <written.nif>
        --preset <SliderPresets.xml> [--band bust|butt|back]

`pass_ledger.py` scores each stage on FIT / ROUGH / STRETCH / SPIKES / FOLDS.
Every one of those is a BIND-POSE quantity, and this defect class is invisible
at bind: on the piece this was written for, bind clipping is 0.000% at EVERY
stage of the chain while the morphed clip runs 2-7%. So a stage ledger scored on
the usual columns says "nothing happened" all the way down.

This scores the stages on the quantity the defect is actually in.

THE MORPH MODEL IS THE CONVERTER'S OWN RULE, not an approximation invented here:
`generate_armor_tri` gives a garment vert the delta of ITS OWN nearest body
vert. Verified on a shipped mesh -- garment-move minus donor-move is -0.000u at
every percentile -- so applying it to a stage's geometry answers "what would
ship if the chain stopped HERE", without needing a `.tri` that does not exist
until the end.

The last row is the WRITTEN NIF, because `_copy_shape` re-runs geometry repairs
after the final checkpoint: a ledger built from stage dumps alone measures what
each pass CREATES, not what survives.

CONTROLS: the body must MOVE or the run aborts, and the BIND clip is printed
beside the morph clip at every stage -- so a stage that moves the morph number
without moving the bind number is visibly a morph-only effect rather than a fit
regression. A stage whose vert count does not match the written shape is
reported and skipped, never silently aligned.

Produce the input with:
    CBBE2UBE_STAGE_DUMP=<dir> python scripts/convert_one_armor.py ...
"""
import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src import paths                                          # noqa: E402
try:
    paths.export_to_env(paths.discover_layout())
except Exception as _le:                                       # noqa: BLE001
    print(f"WARNING: could not resolve an MO2 layout ({_le}); set "
          f"CBBE2UBE_MO2_INI if the body OSD fails to resolve", file=sys.stderr)

import numpy as np                                             # noqa: E402
from scipy.spatial import cKDTree                              # noqa: E402
from scripts.analysis import standoff_audit as sa              # noqa: E402
from scripts.analysis import morph_clip_test as mct            # noqa: E402
import src.nif_convert as nc                                   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("npz", help="<stem>__<shape>.npz from CBBE2UBE_STAGE_DUMP")
    ap.add_argument("nif", help="the WRITTEN NIF that dump came from")
    ap.add_argument("--preset", required=True,
                    help="BodySlide SliderPresets .xml or RaceMenu .jslot")
    ap.add_argument("--band", default="bust", choices=sorted(mct.BANDS))
    a = ap.parse_args()

    nif = Path(a.nif)
    nf = nc._pynifly().NifFile(filepath=str(nif))
    body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if body is None:
        print("ABORT: no injected BaseShape -- this is a copy-path piece and "
              "there is no body in the file to morph against", file=sys.stderr)
        return 3
    bV = mct._world(body)
    bT = np.asarray(body.tris, np.int64).reshape(-1, 3)
    bN = np.asarray(body.normals, np.float64)
    bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True), 1e-9, None)

    bm, _osd = mct.body_morphs(len(bV))
    if not bm:
        print("ABORT: no body OSD resolved -- cannot morph the body",
              file=sys.stderr)
        return 3
    pv, _kind = mct._load_preset(a.preset)
    sel, missing, _fuzzy = mct._resolve_preset(pv, bm)
    if not sel:
        print("ABORT: the preset resolved to NO body slider", file=sys.stderr)
        return 3
    dB = np.zeros_like(bV)
    for k in sel:
        dB += bm[k] * sel[k]
    if np.linalg.norm(dB, axis=1).max() < 1e-4:
        print("ABORT: the preset moves the body 0.000u", file=sys.stderr)
        return 3

    idx = np.flatnonzero(mct.BANDS[a.band](bV))
    if len(idx) < 20:
        print(f"ABORT: {a.band} band has {len(idx)} verts", file=sys.stderr)
        return 3
    bVm = bV + dB
    vaM = sa.vert_areas(bVm, bT)
    vaB = sa.vert_areas(bV, bT)
    btree = cKDTree(bV)

    d = np.load(a.npz)
    stages = [k for k in d.files if k != "tris"]
    tris = np.asarray(d["tris"], np.int64).reshape(-1, 3)
    shape_name = Path(a.npz).stem.split("__", 1)[-1]
    wshape = next((s for s in nf.shapes if s.name == shape_name), None)
    if wshape is None:
        print(f"ABORT: shape {shape_name!r} is not in the written NIF",
              file=sys.stderr)
        return 3
    wV = mct._aligned(wshape, bV)

    print(f"{nif.name}   shape {shape_name}   preset {Path(a.preset).stem}")
    print(f"{a.band} band {len(idx)} verts   {len(sel)} sliders resolved"
          f"   body max move {np.linalg.norm(dB, axis=1).max():.3f}u")
    if missing:
        print(f"  {len(missing)} preset slider(s) did not resolve against the "
              f"body OSD")
    print(f"\n{'stage':<26}{'bindclip%':>11}{'MORPHclip%':>12}"
          f"{'delta':>9}{'stand0':>9}{'moved':>9}")
    print("-" * 76)

    prev_m, prev_v = None, None
    for name in list(stages) + ["WRITTEN NIF"]:
        gV = wV if name == "WRITTEN NIF" else np.asarray(d[name], np.float64)
        if len(gV) != len(wV):
            print(f"{name:<26}  SKIPPED (vert count {len(gV)} != "
                  f"{len(wV)} in the written shape)")
            continue
        # the converter's own morph rule, applied to THIS stage's geometry
        dd, don = btree.query(gV)
        dG = dB[don]
        rb = sa.ClipTester(gV, tris).report(bV, bT, bN, idx, vaB, oriented=True)
        rm = sa.ClipTester(gV + dG, tris).report(bVm, bT, bN, idx, vaM,
                                                 oriented=True)
        inb = dd <= 6.0
        st0 = float(np.median(dd[inb])) if inb.any() else float("nan")
        mv = (float(np.median(np.linalg.norm(gV - prev_v, axis=1)))
              if prev_v is not None else 0.0)
        dlt = (rm["clipping_pct"] - prev_m) if prev_m is not None else 0.0
        flag = "   <<<" if dlt > 0.2 else ""
        print(f"{name:<26}{rb['clipping_pct']:>11.3f}"
              f"{rm['clipping_pct']:>12.3f}{dlt:>+9.3f}{st0:>9.3f}"
              f"{mv:>9.4f}{flag}")
        prev_m, prev_v = rm["clipping_pct"], gV
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
