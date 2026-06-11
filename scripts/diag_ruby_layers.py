# Diagnostic: measure layer-order preservation on DDV Ruby flower Top (slot 32).
#
# Replicates the v3 abdomen-pass pair classification (same constants, same
# frames, same symmetric pooling) on the REAL source + converted NIFs, then
# measures per-location ORDER FLIPS: vert pairs whose radial order in the
# converted output disagrees with their order in the source. This tells us
# which shape pairs v3 refused to order ("interleaved") and how much actual
# misalignment shipped because of it.
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))

from pyn import pynifly  # noqa: E402
from src import nif_convert as nc  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

SRC_NIF = Path(r"D:\Modlists\ARR\mods\Authoria - Bodyslide Output - 3BA"
               r"\meshes\armory\Ruby flower\DDV - Ruby flower Top_1.nif")
DST_NIF = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto"
               r"\meshes\!UBE\armory\Ruby flower\DDV - Ruby flower Top_1.nif")

CLOTH = ["chest_plate", "corset", "top", "belts", "belts_metal"]
PAIR_R = nc.OVERLAY_PAIR_R          # 3.0
MIN_OVERLAP = nc.OVERLAY_MIN_OVERLAP  # 30
CONSIST = 0.65  # v3's OVERLAY_CONSIST_FRAC (removed in v4; kept for reporting)


def world_verts(shape):
    v = np.asarray(list(shape.verts), dtype=np.float64)
    g2s = nc._shape_global_to_skin(shape)
    return nc._verts_skin_to_world(v, g2s)


def load(path, body_name):
    nf = pynifly.NifFile(filepath=str(path))
    shapes = {s.name: s for s in nf.shapes}
    body = shapes[body_name]
    bv = world_verts(body)
    bn = nc._body_normals_or_compute(body)
    if bn is None:
        raise SystemExit(f"no body normals for {body_name} in {path.name}")
    bn = np.asarray(bn, dtype=np.float64)
    cloth = {n: world_verts(shapes[n]) for n in CLOTH if n in shapes}
    return bv, bn, cloth


def clearances(cloth, bv, bn):
    tree = cKDTree(bv)
    out = {}
    for n, v in cloth.items():
        _, i = tree.query(v, k=1)
        out[n] = ((v - bv[i]) * bn[i]).sum(axis=1)
    return out


def classify_pairs(cloth, clr, label):
    print(f"\n=== v3 pair classification in {label} frame ===")
    print(f"{'pair':32s} {'n_ovl':>6s} {'fracAout':>8s} {'median':>8s}  verdict")
    names = list(cloth)
    verdicts = {}
    for ai in range(len(names)):
        for bi in range(ai + 1, len(names)):
            a, b = names[ai], names[bi]
            ta, tb = cKDTree(cloth[a]), cKDTree(cloth[b])
            ddb, uib = tb.query(cloth[a], k=1, distance_upper_bound=PAIR_R)
            dda, uia = ta.query(cloth[b], k=1, distance_upper_bound=PAIR_R)
            mb, ma = np.isfinite(ddb), np.isfinite(dda)
            diffs = []
            if mb.any():
                diffs.append(clr[a][mb] - clr[b][uib[mb]])
            if ma.any():
                diffs.append(clr[a][uia[ma]] - clr[b][ma])
            if not diffs:
                print(f"{a+'~'+b:32s} {'0':>6s} {'-':>8s} {'-':>8s}  no overlap")
                continue
            diff = np.concatenate(diffs)
            if len(diff) < MIN_OVERLAP:
                print(f"{a+'~'+b:32s} {len(diff):6d} {'-':>8s} {'-':>8s}  "
                      f"below MIN_OVERLAP")
                continue
            frac = float((diff > 0).mean())
            med = float(np.median(diff))
            if frac >= CONSIST:
                v = f"EDGE {a} OUTSIDE {b}"
            elif frac <= 1.0 - CONSIST:
                v = f"EDGE {b} OUTSIDE {a}"
            else:
                v = "INTERLEAVED (no edge)"
            verdicts[(a, b)] = (frac, med, v)
            print(f"{a+'~'+b:32s} {len(diff):6d} {frac:8.2f} {med:8.3f}  {v}")
    return verdicts


def order_flips(src_cloth, src_clr, dst_cloth, dst_clr,
                src_strong=0.10, region_bins=True):
    """For each pair: pair A verts to nearest B vert in the SOURCE frame, then
    re-evaluate the SAME index pairs in the converted frame. A flip = source
    says A is outside B at this spot (by > src_strong) but converted has A
    BELOW B (negative gap). Reports flip counts, depth, and Z-band."""
    print("\n=== per-location order flips (source pairing -> converted gap) ===")
    print(f"{'pair':32s} {'paired':>7s} {'strong':>7s} {'flips':>6s} "
          f"{'flip%':>6s} {'medFlipDepth':>12s}  flip Z-range")
    names = list(src_cloth)
    for ai in range(len(names)):
        for bi in range(len(names)):
            if ai == bi:
                continue
            a, b = names[ai], names[bi]
            tb = cKDTree(src_cloth[b])
            dd, ub = tb.query(src_cloth[a], k=1, distance_upper_bound=PAIR_R)
            m = np.isfinite(dd)
            if m.sum() < MIN_OVERLAP:
                continue
            ia = np.where(m)[0]
            ib = ub[m]
            sgap = src_clr[a][ia] - src_clr[b][ib]
            dgap = dst_clr[a][ia] - dst_clr[b][ib]
            strong = sgap > src_strong          # A clearly outside B in source
            if strong.sum() == 0:
                continue
            flip = strong & (dgap < 0.0)        # ...but inside B in converted
            n_f = int(flip.sum())
            if n_f == 0:
                print(f"{a+'>'+b:32s} {int(m.sum()):7d} {int(strong.sum()):7d} "
                      f"{0:6d} {0.0:6.1f} {'-':>12s}")
                continue
            depth = float(np.median(-dgap[flip]))
            z = src_cloth[a][ia][flip][:, 2]
            print(f"{a+'>'+b:32s} {int(m.sum()):7d} {int(strong.sum()):7d} "
                  f"{n_f:6d} {100.0*n_f/strong.sum():6.1f} {depth:12.3f}  "
                  f"Z {z.min():.1f}..{z.max():.1f}")


class _Src:
    def __init__(self, verts):
        self.verts = verts


def apply_v4(scloth, dcloth, dbv, dbn, sbv, sbn):
    """Run the (current) abdomen pass on jobs built from the REAL source +
    shipped converted verts; returns the post-pass cloth verts."""
    jobs = []
    for n in CLOTH:
        jobs.append({
            "override_skin": {"weights": {"NPC Spine": []}},
            "src": _Src(scloth[n]),
            "verts": np.array(dcloth[n], dtype=np.float64),
            "verts_modified": False,
        })
    moved = nc._separate_abdomen_layered_cloth_depth(
        jobs, body_verts=dbv, body_normals=dbn,
        source_body_verts=sbv, source_body_normals=sbn)
    out = {}
    print(f"\n=== v4 pass applied offline: {moved} vert(s) moved ===")
    for n, j in zip(CLOTH, jobs):
        nv = np.asarray(j["verts"], dtype=np.float64)
        disp = np.linalg.norm(nv - dcloth[n], axis=1)
        print(f"{n:14s} moved {int((disp > 0.02).sum()):5d}  "
              f"max {disp.max():.3f}  mean-of-moved "
              f"{(disp[disp > 0.02].mean() if (disp > 0.02).any() else 0):.3f}")
        out[n] = nv
    return out


def main():
    sbv, sbn, scloth = load(SRC_NIF, "3BA")
    dbv, dbn, dcloth = load(DST_NIF, "BaseShape")
    for n in CLOTH:
        if len(scloth[n]) != len(dcloth[n]):
            raise SystemExit(f"topology mismatch on {n}")
    sclr = clearances(scloth, sbv, sbn)
    dclr = clearances(dcloth, dbv, dbn)

    print("=== per-shape clearance (median / p10 / p90) source -> converted ===")
    for n in CLOTH:
        s, d = sclr[n], dclr[n]
        print(f"{n:14s} src {np.median(s):6.2f} ({np.percentile(s,10):6.2f}"
              f"/{np.percentile(s,90):6.2f})   dst {np.median(d):6.2f} "
              f"({np.percentile(d,10):6.2f}/{np.percentile(d,90):6.2f})")

    if "--apply" in sys.argv:
        vcloth = apply_v4(scloth, dcloth, dbv, dbn, sbv, sbn)
        vclr = clearances(vcloth, dbv, dbn)
        order_flips(scloth, sclr, vcloth, vclr)
    else:
        classify_pairs(scloth, sclr, "SOURCE (what v3 classified on)")
        classify_pairs(dcloth, dclr, "CONVERTED (what shipped)")
        order_flips(scloth, sclr, dcloth, dclr)


if __name__ == "__main__":
    main()
