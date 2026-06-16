"""Profile overlay conversion to find the bottleneck before optimizing. Times
the three stages per overlay: dds_to_rgba (texconv DECODE), transfer_overlay
(numpy, first-of-size pays the map build), rgba_to_dds (texconv ENCODE). Prints
a per-stage breakdown so we know whether texconv subprocess cost dominates (=>
parallelize/batch) or the numpy transfer does."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["CBBE2UBE_MODS_ROOT"] = r"D:\Modlists\ARR\mods"
REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from src import overlay_transfer as ot          # noqa: E402
from src import paths as P                        # noqa: E402


def main():
    lay = P.discover_layout()
    tex = ot.find_texconv()
    print("texconv:", tex)
    work = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    corr = ot.build_body_overlay_correspondence("_1")
    print(f"build body correspondence: {time.perf_counter()-t0:.2f}s")

    by = ot.discover_overlays(lay, ("body",))
    items = list(by["body"].items())[:10]
    print(f"profiling {len(items)} body overlays\n")
    print(f"{'overlay':40} {'decode':>8} {'xfer':>8} {'encode':>8} {'size':>10}")
    agg = {"decode": 0.0, "xfer": 0.0, "encode": 0.0}
    from src.bsa_strings import BSAArchive
    arc_cache = {}
    for rel, src in items:
        if src[0] == "loose":
            src_dds = src[1]
        else:
            arc = arc_cache.setdefault(src[1], BSAArchive(src[1], eager=False))
            data = arc.read_file(src[2])
            src_dds = work / "src.dds"; src_dds.write_bytes(data)
        ta = time.perf_counter()
        rgba = ot.dds_to_rgba(src_dds, tex, work / "w")
        tb = time.perf_counter()
        out = ot.transfer_overlay(rgba, corr)
        tc = time.perf_counter()
        ot.rgba_to_dds(out, work / "out.dds", tex, work / "w")
        td = time.perf_counter()
        agg["decode"] += tb - ta; agg["xfer"] += tc - tb; agg["encode"] += td - tc
        print(f"{rel.rsplit('/',1)[-1][:40]:40} {tb-ta:7.2f}s {tc-tb:7.2f}s "
              f"{td-tc:7.2f}s {str(rgba.shape[:2]):>10}")
    n = len(items)
    print(f"\nTOTALS over {n}: decode {agg['decode']:.2f}s  xfer {agg['xfer']:.2f}s  "
          f"encode {agg['encode']:.2f}s")
    print(f"per-overlay avg: decode {agg['decode']/n:.2f}s  xfer {agg['xfer']/n:.2f}s  "
          f"encode {agg['encode']/n:.2f}s")
    tex_frac = (agg['decode'] + agg['encode']) / max(sum(agg.values()), 1e-9)
    print(f"texconv (decode+encode) = {100*tex_frac:.0f}% of wall time")


if __name__ == "__main__":
    main()
