# One-off: introspect the v4 constraint field for specific Ruby Top pairs —
# why didn't the top's neckline / corset's strip fire their constraints?
import sys
from pathlib import Path

import numpy as np

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / ".pynifly"))

from src import nif_convert as nc  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "diag_ruby_layers", REPO / "scripts" / "diag_ruby_layers.py")
drl = importlib.util.module_from_spec(spec)
sys.modules["diag_ruby_layers"] = drl
spec.loader.exec_module.__self__ if False else None
# avoid running main: load() etc. are defined at module top level
spec.loader.exec_module(drl)  # noqa: E305  (no main() call inside)

PAIR_R = nc.OVERLAY_PAIR_R
MIN = nc.OVERLAY_LOCAL_ORDER_MIN
CONSIST = nc.OVERLAY_LOCAL_CONSIST


def field_for_pair(scloth, sclr, a, b):
    """Replicate v4's pooled, multiplicity-weighted field for pair (a, b);
    return per-a-vert (mean, fpos, has_pair_mask)."""
    A, B = scloth[a], scloth[b]
    ca, cb = sclr[a], sclr[b]
    ta, tb = cKDTree(A), cKDTree(B)
    ddb, uib = tb.query(A, k=1, distance_upper_bound=PAIR_R)
    dda, uia = ta.query(B, k=1, distance_upper_bound=PAIR_R)
    ma, mb = np.isfinite(ddb), np.isfinite(dda)

    def inv_mult(t):
        _, inv, cnt = np.unique(t, return_inverse=True, return_counts=True)
        return 1.0 / cnt[inv]

    pts = np.vstack([A[ma], B[mb]])
    gap = np.concatenate([ca[ma] - cb[uib[ma]], ca[uia[mb]] - cb[mb]])
    wt = np.concatenate([inv_mult(uib[ma]), inv_mult(uia[mb])])
    st = cKDTree(pts)
    K = min(32, len(pts))
    dd, ui = st.query(A, k=K, distance_upper_bound=PAIR_R)
    valid = np.isfinite(dd)
    safe = np.where(valid, ui, 0)
    val = np.where(valid, gap[safe], 0.0)
    w = np.where(valid, wt[safe], 0.0)
    wsum = w.sum(axis=1)
    ok = (valid.sum(axis=1) >= 4) & (wsum > 0)
    den = np.where(wsum > 0, wsum, 1.0)
    mean = np.where(ok, (val * w).sum(axis=1) / den, 0.0)
    fpos = np.where(ok, (w * (val > 0)).sum(axis=1) / den, 0.5)
    raw = np.full(len(A), np.nan)
    raw[ma] = ca[ma] - cb[uib[ma]]
    return mean, fpos, ma, raw


def report(scloth, sclr, a, b):
    mean, fpos, ma, raw = field_for_pair(scloth, sclr, a, b)
    strong = ma & (raw > 0.10)
    fired = ma & (mean >= MIN) & (fpos >= CONSIST)
    print(f"\n--- {a} vs {b}: strong-src-out={int(strong.sum())}, "
          f"of which FIRED={int((strong & fired).sum())} ---")
    miss = strong & ~fired
    print(f"missed={int(miss.sum())}")
    if miss.any():
        print(f"  missed mean-field: med {np.median(mean[miss]):.3f}  "
              f"p10 {np.percentile(mean[miss],10):.3f}  "
              f"p90 {np.percentile(mean[miss],90):.3f}")
        print(f"  missed fpos:       med {np.median(fpos[miss]):.3f}  "
              f"p10 {np.percentile(fpos[miss],10):.3f}  "
              f"p90 {np.percentile(fpos[miss],90):.3f}")
        print(f"  missed raw gap:    med {np.median(raw[miss]):.3f}  "
              f"p90 {np.percentile(raw[miss],90):.3f}")
        fail_mean = miss & (mean < MIN)
        fail_frac = miss & (fpos < CONSIST)
        print(f"  fail by mean<{MIN}: {int(fail_mean.sum())}, "
              f"by fpos<{CONSIST}: {int(fail_frac.sum())}, "
              f"both: {int((fail_mean & fail_frac).sum())}")


def main():
    sbv, sbn, scloth = drl.load(drl.SRC_NIF, "3BA")
    sclr = drl.clearances(scloth, sbv, sbn)
    report(scloth, sclr, "top", "chest_plate")
    report(scloth, sclr, "top", "belts")
    report(scloth, sclr, "top", "belts_metal")
    report(scloth, sclr, "corset", "belts_metal")


if __name__ == "__main__":
    main()
