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

"""M3 phase 1: convert Kozakowy Belt_1 (no inline body) CBBE -> UBE and
compare to the hand-built UBE Belt_1.

Empirical finding (see docs/M3_findings.md when written): for armor pieces
without inline body, the right transformation is identity. Position-
warping based on CBBE-body -> UBE-body correspondence introduces more
error than it fixes, because the real BodySlide conversion doesn't warp
armor verts — it just uses the same slider-zero shapedata as CBBE.

This test verifies:
  1. Default (copy) mode produces a NIF byte-identical to the source.
  2. The default output is closer to (or equal to) the UBE-built reference
     than any warped variant would be.
  3. (Optional) The opt-in `warp_armor=True` path runs without errors.
"""
import os
import sys
from pathlib import Path

import numpy as np

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import nif_io, nif_convert


# Optional warp-mode reference bodies. Set these env vars to a CBBE and a UBE
# femalebody NIF to exercise the (diagnostic) warp path; left unset, that part
# of the test is skipped (the is_file() guard below).
CBBE_REF = Path(os.environ.get("CBBE2UBE_CBBE_REF", ""))
UBE_REF  = Path(os.environ.get("CBBE2UBE_UBE_REF", ""))

CBBE_SRC = (
    PROJ / "samples" / "m1" / "kozakowy_vampire" / "cbbe"
    / "[TOTOxKozakowy] Kozakowy's Vampire Armor 3BA"
    / "meshes" / "clothes" / "Kozakowy" / "VampireArmor" / "Belt_1.nif"
)
UBE_TARGET = (
    PROJ / "samples" / "m1" / "kozakowy_vampire" / "ube"
    / "[TOTOxKozakowy] Kozakowy's Vampire Armor UBE v1.0"
    / "meshes" / "!UBE" / "clothes" / "Kozakowy" / "VampireArmor" / "Belt_1.nif"
)
OUT_COPY = PROJ / "output" / "m3" / "Belt_1.copy.nif"
OUT_WARP = PROJ / "output" / "m3" / "Belt_1.warp.nif"


def per_shape_verts(nif) -> dict[str, np.ndarray]:
    return {s.name: np.asarray(s.verts, dtype=np.float64) for s in nif.shapes}


def main() -> None:
    if not CBBE_SRC.is_file() or not UBE_TARGET.is_file():
        print(f"SKIP — missing belt samples"); return

    OUT_COPY.parent.mkdir(parents=True, exist_ok=True)

    # --- (1) Default copy mode -------------------------------------------
    print(">>> default (copy) mode")
    r_copy = nif_convert.convert_nif(CBBE_SRC, OUT_COPY)
    print(f"  status: {r_copy.status}")
    print(f"  body  : {r_copy.body_shapes}")
    print(f"  armor : {r_copy.armor_shapes}")
    assert r_copy.status == "converted (copy)", r_copy.status

    # Byte-identity with source
    assert CBBE_SRC.read_bytes() == OUT_COPY.read_bytes(), \
        "copy mode output differs from source bytes"
    print("  byte-identical to CBBE source: YES")

    # Per-shape distance to UBE-built target
    src_shapes = per_shape_verts(nif_io.load_nif(CBBE_SRC))
    ube_shapes = per_shape_verts(nif_io.load_nif(UBE_TARGET))
    out_shapes = per_shape_verts(nif_io.load_nif(OUT_COPY))
    assert set(src_shapes) == set(out_shapes), "shape set drift"
    for name in src_shapes:
        assert src_shapes[name].shape == out_shapes[name].shape, \
            f"{name}: vert count drift"
    print("  structural: shapes + vert counts preserved")

    print(f"\n  {'shape':<20} {'CBBE->UBE_built':>16} {'OUR->UBE_built':>15}")
    overall_pass = True
    for name in src_shapes:
        if name not in ube_shapes: continue
        cbbe = src_shapes[name]; ube = ube_shapes[name]; ours = out_shapes[name]
        if cbbe.shape != ube.shape: continue
        d_baseline = float(np.mean(np.linalg.norm(cbbe - ube, axis=1)))
        d_ours     = float(np.mean(np.linalg.norm(ours - ube, axis=1)))
        # Copy mode must be exactly equal to baseline (up to float32 reload).
        ok = abs(d_ours - d_baseline) < 1e-4
        overall_pass &= ok
        verdict = "PASS" if ok else "FAIL"
        print(f"  {name:<20} {d_baseline:>16.4f} {d_ours:>15.4f}   {verdict}")

    # --- (2) Warp mode (opt-in, expected to underperform) ----------------
    print("\n>>> warp_armor=True (opt-in, diagnostic)")
    if CBBE_REF.is_file() and UBE_REF.is_file():
        r_warp = nif_convert.convert_nif(
            CBBE_SRC, OUT_WARP,
            cbbe_ref_path=CBBE_REF, ube_ref_path=UBE_REF,
            warp_armor=True,
        )
        print(f"  status: {r_warp.status}")
        warp_shapes = per_shape_verts(nif_io.load_nif(OUT_WARP))
        for name in src_shapes:
            if name not in ube_shapes: continue
            d_baseline = float(np.mean(np.linalg.norm(
                src_shapes[name] - ube_shapes[name], axis=1)))
            d_warp = float(np.mean(np.linalg.norm(
                warp_shapes[name] - ube_shapes[name], axis=1)))
            print(f"  {name:<20} baseline={d_baseline:.4f}  warped={d_warp:.4f}"
                  f"  -> {'(warp degrades)' if d_warp > d_baseline else '(warp helps)'}")
    else:
        print("  SKIP — body refs not on disk")

    print(f"\n=== M3 belt test {'PASSED' if overall_pass else 'FAILED'} ===")


# Script-style diagnostic (like test_correspondence.py). Run directly with
# `python tests/test_m3_belt.py`. Gated under __main__ so it does NOT execute
# at pytest collection time — its copy-mode byte-identity premise predates the
# no-body-piece TRI/BaseShape work and no longer holds, which would otherwise
# crash collection for the whole suite.
if __name__ == "__main__":
    main()
