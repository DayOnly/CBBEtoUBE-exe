"""Sweep the converted output for HDT-SMP XMLs that reference a SHAPE not present
in the NIF (dangling per-vertex/per-triangle-shape). HDT-SMP binds physics to
that missing shape on load -> near-null deref / use-after-free = CTD on cell
entry. _harden_hdt_xml_for_fsmp is supposed to prune these; any survivor is the
crash mechanism. Flags body-name dangles (3BA/femalebody) specially -- those are
the shapes our double-body fix now drops."""
import re
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Sam\Downloads\cbbe-to-ube")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / ".pynifly"))
from pyn import pynifly  # noqa: E402

OUT = Path(r"D:\Modlists\ARR\mods\CBBEtoUBE Auto\meshes")
BODYISH = ("3ba", "femalebody", "femaleunderwearbody", "body")


def xml_for(nifp):
    stem = nifp.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-2]; break
    return nifp.with_name(stem + ".xml")


def main():
    t0 = time.time()
    hits = []
    scanned = 0
    for p in OUT.rglob("*.nif"):
        xmlp = xml_for(p)
        if not xmlp.is_file():
            continue
        try:
            nif = pynifly.NifFile(str(p))
        except Exception:
            continue
        scanned += 1
        shapes = {s.name for s in nif.shapes}
        txt = xmlp.read_text(errors="ignore")
        pv = set(re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt))
        pt = set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt))
        dangling_pv = sorted(pv - shapes)
        dangling_pt = sorted(pt - shapes)
        if dangling_pv or dangling_pt:
            hits.append((p, dangling_pv, dangling_pt))

    print(f"scanned {scanned} HDT NIFs in {time.time()-t0:.0f}s\n")
    body_hits = [h for h in hits
                 if any(d.lower().startswith(BODYISH) for d in (h[1] + h[2]))]
    print(f"==== {len(hits)} NIFs with DANGLING XML shape refs "
          f"({len(body_hits)} reference a dropped BODY shape) ====\n")
    for p, dpv, dpt in hits[:60]:
        flag = " <<< BODY" if any(
            d.lower().startswith(BODYISH) for d in (dpv + dpt)) else ""
        print(f"  {p.relative_to(OUT)}{flag}")
        if dpv:
            print(f"       softbody(per-vertex) dangling: {dpv}")
        if dpt:
            print(f"       collider(per-triangle) dangling: {dpt}")
    if len(hits) > 60:
        print(f"  ... and {len(hits)-60} more")


if __name__ == "__main__":
    main()
