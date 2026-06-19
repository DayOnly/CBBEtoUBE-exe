# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# Free software under the GNU GPL v3+. See <https://www.gnu.org/licenses/>.

"""On-disk PROTOTYPE of the cross-shape seam-reconciliation fix (src/seam_reconcile).
Closes the converter-opened waist seam in a finished OUTPUT armor NIF so it can be
tested in-game WITHOUT a full reconvert. Backs up the original to <name>.bak and
writes atomically. Reports the seam gap before vs after to prove closure.

    python scripts/fix_cross_shape_seams.py <output.nif> --source <source.nif>
        --body <ube_body.nif> [--dry-run]

Output shapes must be in body space (raw placement); g2s-placed shapes are skipped
with a warning (write-back of an inverted transform is out of scope for the proto).
"""
import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from scipy.spatial import cKDTree  # noqa: E402

from src.nif_convert import (  # noqa: E402
    _pynifly, _cached_ube_body_verts, _reauthor_nif_fresh)
from src.seam_reconcile import reconcile_seam_groups, SEAM_COINCIDE  # noqa: E402
from diag_jiggle_predict import garment_world_verts  # noqa: E402


def _seam_gap_report(out_w, src_w, coincide=SEAM_COINCIDE):
    """Mean/max output gap over cross-shape source-coincident vert pairs."""
    names = list(out_w)
    chunks, owner = [], []
    for si, nm in enumerate(names):
        chunks.append(src_w[nm])
        owner.extend((si, li) for li in range(len(src_w[nm])))
    S = np.concatenate(chunks, 0)
    owner = np.asarray(owner)
    pairs = cKDTree(S).query_pairs(coincide, output_type="ndarray")
    gaps = []
    for a, b in pairs:
        if owner[a, 0] == owner[b, 0]:
            continue
        pa = out_w[names[owner[a, 0]]][owner[a, 1]]
        pb = out_w[names[owner[b, 0]]][owner[b, 1]]
        gaps.append(float(np.linalg.norm(pa - pb)))
    if not gaps:
        return 0, 0.0, 0.0
    g = np.array(gaps)
    return len(g), float(g.mean()), float(g.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output", help="converted (output) armor NIF to fix in place")
    ap.add_argument("--source", required=True, help="pre-conversion source NIF")
    ap.add_argument("--body", required=True, help="UBE reference body NIF")
    ap.add_argument("--dry-run", action="store_true", help="measure only, no write")
    a = ap.parse_args()

    out_p, src_p = Path(a.output), Path(a.source)
    _, bv, _ = _cached_ube_body_verts(Path(a.body))
    bv = np.asarray(bv, dtype=np.float64)
    btree = cKDTree(bv)

    pyn = _pynifly()
    onf = pyn.NifFile(filepath=str(out_p))
    snf = pyn.NifFile(filepath=str(src_p))

    out_w, src_w, out_raw, backing = {}, {}, {}, {}
    for s in onf.shapes:
        w, used = garment_world_verts(s, btree)
        out_w[s.name] = np.asarray(w, dtype=np.float64)
        out_raw[s.name] = not used      # True => stored verts == world (writable)
        backing[s.name] = s
    for s in snf.shapes:
        w, _ = garment_world_verts(s, btree)
        src_w[s.name] = np.asarray(w, dtype=np.float64)

    common = [n for n in out_w if n in src_w]
    n0, mean0, max0 = _seam_gap_report({n: out_w[n] for n in common},
                                        {n: src_w[n] for n in common})
    print(f"{out_p.name}: shapes {list(out_w)}")
    print(f"  seam pairs={n0}  gap BEFORE: mean={mean0:.2f}u max={max0:.2f}u")

    new_w, st = reconcile_seam_groups({n: out_w[n] for n in common},
                                      {n: src_w[n] for n in common},
                                      body_verts=bv)
    _, mean1, max1 = _seam_gap_report(new_w, {n: src_w[n] for n in common})
    print(f"  reconciled: groups={st['groups_welded']} verts_moved="
          f"{st['verts_moved']} max_close={st['max_close']:.2f}u")
    print(f"  gap AFTER:  mean={mean1:.2f}u max={max1:.2f}u")

    if a.dry_run:
        print("  (dry-run; no write)")
        return
    if st["verts_moved"] == 0:
        print("  nothing to weld; not writing.")
        return

    override, skipped = {}, []
    for nm in common:
        if np.allclose(new_w[nm], out_w[nm]):
            continue                       # unchanged shape -> copy verbatim
        if not out_raw[nm]:
            skipped.append(nm)             # g2s shape: world!=stored, out of scope
            continue
        # output shapes are raw (stored == world), so the reconciled world verts
        # ARE the stored verts to write.
        override[nm] = new_w[nm].astype(np.float32)
    if skipped:
        print(f"  !! skipped non-raw shapes (write-back unsupported): {skipped}")
    if not override:
        print("  no writable shape changed; not writing.")
        return

    bak = out_p.with_suffix(out_p.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(out_p, bak)
        print(f"  backed up original -> {bak.name}")
    # Re-author the whole NIF fresh with the welded verts -> reuses skin / BODYTRI
    # / HDT / hidden-flag preservation and recomputes normals for moved verts.
    ok = _reauthor_nif_fresh(out_p, override_verts_by_name=override)
    if ok:
        print(f"  WROTE (re-authored) {len(override)} shape(s) -> {out_p.name}")
    else:
        print("  !! re-author FAILED -> original kept (restore from .bak if needed)")


if __name__ == "__main__":
    main()
