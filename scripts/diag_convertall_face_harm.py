"""Decisive test for "just convert ALL overlays": does pushing a FACE overlay
through the BODY correspondence harm it? If convert-all were safe, a face overlay
would come out ~unchanged. Metric: IoU of the source alpha footprint vs the
transferred alpha footprint, plus surviving-alpha fraction. A BODY overlay should
stay put (high IoU, most alpha survives); a FACE overlay scattered through the
body UV should lose/move most of its design (low IoU)."""
import os
import sys
import tempfile
from pathlib import Path
import numpy as np

os.environ["CBBE2UBE_MODS_ROOT"] = r"D:\Modlists\ARR\mods"
REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from src import overlay_transfer as ot          # noqa: E402
TEXCONV = r"D:\Modlists\ARR\tools\xEdit\Edit Scripts\Texconvx64.exe"
TESTS = [
    ("BODY WNB/Arcolis", r"D:\Modlists\ARR\mods\Weathered Nordic Bodypaints SE\textures\actors\character\Overlays\WNB\Arcolis\Arcolis 1.dds"),
    ("FACE FMS/Eyeliner", r"D:\Modlists\ARR\mods\Female Makeup Suite - Face\textures\actors\character\Overlays\FMS\EyeLiner\Eyeliner 1.dds"),
    ("FACE FMS/Eyeliner Extra", r"D:\Modlists\ARR\mods\Female Makeup Suite - Face\textures\actors\character\Overlays\FMS\Extra\Eyeliner Extra 1.dds"),
    ("FACE FMS/Lips Border", r"D:\Modlists\ARR\mods\Female Makeup Suite - Face\textures\actors\character\Overlays\FMS\Lips\Border 1.dds"),
]


def footprint(rgba, thr=16):
    # These overlays use a BLACK background with the design in RGB (alpha often
    # full), so 'ink' = any RGB channel above threshold, not the alpha channel.
    return rgba[..., :3].max(axis=2) > thr


def main():
    work = Path(tempfile.mkdtemp())
    corr = ot.build_body_overlay_correspondence("_1")
    if corr is None:
        print("no body correspondence (mesh resolve failed)"); return
    for label, dds in TESTS:
        p = Path(dds)
        if p.is_dir():
            cand = next(iter(sorted(p.rglob("*.dds"))), None)
            if cand is None:
                print(f"{label}: no dds under dir"); continue
            dds = str(cand); label = f"{label} ({cand.name})"
        elif not p.is_file():
            print(f"{label}: MISSING {dds}"); continue
        src = ot.dds_to_rgba(dds, TEXCONV, work)
        out = ot.transfer_overlay(src, corr)
        a_in = footprint(src)
        a_out = footprint(out)
        inter = (a_in & a_out).sum()
        union = (a_in | a_out).sum()
        iou = inter / union if union else 0.0
        survive = inter / a_in.sum() if a_in.sum() else 0.0
        print(f"{label:32} src_alpha={100*a_in.mean():5.1f}%  "
              f"out_alpha={100*a_out.mean():5.1f}%  "
              f"IoU(in,out)={iou:5.2f}  alpha_surviving_in_place={100*survive:5.1f}%")


if __name__ == "__main__":
    main()
