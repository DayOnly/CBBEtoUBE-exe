"""Post-reconvert check for the protrusion-follow fix (#protrusion-follow).

Breast-covering armor at a stand-off used to under-follow the breast slider, so
the body poked through at the breasts on a large preset (CLIPPING_LOG entry 0,
memory project_breast_standoff_morph_follow). This measures, per converted body
armor, how well each breast-covering shape's BreastsBigger morph tracks the UBE
body's -- BEFORE the fix a plate 3-4u off tracked at 0.1-0.6x; AFTER it should
ride out toward the body (~0.8-1.0x) while the injected BaseShape stays 1.0x.

Run AFTER a reconvert. Read-only. Reports the worst-tracking breast shapes so you
can spot any armor the fix didn't reach (e.g. a shape with no BaseShape normals).

    python scripts/verify_protrusion_follow.py

Compares against the UBE reference body TRI carried in each armor's own TRI
(BaseShape morph = the body's verbatim slider), so it needs no external OSD.
"""
import os
import sys
import glob
import numpy as np
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                          # noqa: E402
from src import paths                            # noqa: E402
from src.tri import TriFile                      # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
MORPH = os.environ.get("CBBE2UBE_VERIFY_MORPH", "BreastsBigger")


def _breast_follow(nif_path, tri_path):
    """For each shape covering the breast band, mean magnitude of its MORPH over
    that band, plus the injected BaseShape's magnitude (the body reference)."""
    try:
        n = pynifly.NifFile(filepath=str(nif_path))
        tri = TriFile.load(str(tri_path))
    except Exception:
        return None
    verts = {s.name: np.asarray(s.verts, np.float64) for s in n.shapes}
    tri_by = {ts.name: ts for ts in tri.shapes}
    base = None
    rows = []
    for name, av in verts.items():
        ts = tri_by.get(name)
        if ts is None:
            continue
        m = next((mm for mm in ts.morphs if mm.name == MORPH and mm.offsets), None)
        if m is None:
            continue
        idx = np.array([o[0] for o in m.offsets], np.int64)
        d = np.array([o[1:] for o in m.offsets], np.float64)
        inb = (av[idx, 2] >= 99) & (av[idx, 2] < 112) & (np.abs(av[idx, 0]) < 16) \
            & (av[idx, 1] > 3)
        if inb.sum() < 30:
            continue
        mag = np.linalg.norm(d[inb], axis=1)
        val = float(mag[mag > 0.01].mean()) if (mag > 0.01).any() else 0.0
        if name == "BaseShape":
            base = val
        else:
            rows.append((val, name, int(inb.sum())))
    if base is None or not rows:
        return None
    return base, rows


def main():
    lay = paths.discover_layout()
    root = (lay.mods_root / OUT_MOD / "meshes" / "!UBE") if lay.mods_root else None
    if root is None or not root.is_dir():
        print("output not found (set CBBE2UBE_MO2_INI + reconvert first).")
        return 1
    files = [f for f in glob.glob(str(root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()]
    print(f"scanning {len(files)} meshes for breast-covering shapes ({MORPH})...",
          flush=True)
    worst = []
    for f in files:
        tri = Path(f).parent / (Path(f).name.replace("_1.nif", "").replace(
            "_0.nif", "") + ".tri")
        if not tri.is_file():
            continue
        r = _breast_follow(f, tri)
        if r is None:
            continue
        base, rows = r
        rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
        for val, name, nb in rows:
            ratio = val / base if base > 0.05 else 1.0
            worst.append((ratio, val, base, name, nb, rel))
    worst.sort()
    print(f"\n=== breast-morph follow (lower ratio = body pokes through more) ===")
    print(f"{'ratio':>5} {'shape':>5} {'body':>5}  shape / armor")
    for ratio, val, base, name, nb, rel in worst[:40]:
        flag = "  <-- still low" if ratio < 0.5 else ""
        print(f"{ratio:5.2f} {val:5.2f} {base:5.2f}  {name} :: {rel}{flag}")
    lows = [w for w in worst if w[0] < 0.5]
    print(f"\n{len(worst)} breast-covering shapes; {len(lows)} still track < 0.5x "
          f"the body. After the fix most standoff plates should be >= ~0.8x; a "
          f"shape stuck low may lack BaseShape normals (phase-2 fallback).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
