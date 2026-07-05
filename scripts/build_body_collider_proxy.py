# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# Free software under the GNU GPL v3+. See <https://www.gnu.org/licenses/>.

"""Generate a low-poly body-collider proxy and inject it into converted armors
whose HDT-SMP XML uses the FULL 29k-vert BaseShape as the per-triangle collider
(280 such armors). FSMP out-of-bounds-crashes on a collider that large; a ~2-3k
decimated proxy collides identically for cloth but is FSMP-safe.

Keeps BOTH: the high-poly BaseShape stays the VISIBLE render body (untouched); a
SEPARATE hidden 'VirtualBody' proxy (decimated from that same body, same skin)
is added and the XML's <per-triangle-shape> is repointed BaseShape -> VirtualBody.

    python scripts/build_body_collider_proxy.py <armor_1.nif> [--target 2500]
    python scripts/build_body_collider_proxy.py --batch <output_meshes_root>

Vertex-cluster decimation keeps REPRESENTATIVE original verts, so weights /
skin-transforms / g2s copy straight from BaseShape (no re-rigging).
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.nif_convert import _pynifly                       # noqa: E402
from src.atomic_io import atomic_nif_save, atomic_write_bytes  # noqa: E402

VB = "VirtualBody"


def decimate_keep_reps(verts: np.ndarray, tris: np.ndarray, target: int):
    """Vertex-cluster decimation that KEEPS representative original verts.
    Returns (rep_old_indices (K,), old->new map (N,), new_tris (M,3))."""
    v = np.asarray(verts, np.float64)
    lo = v.min(0)
    span = float(np.linalg.norm(v.max(0) - lo)) or 1.0
    # auto-tune cell size to land near `target` representatives
    cell = span / 60.0
    for _ in range(24):
        ci = np.floor((v - lo) / cell).astype(np.int64)
        keys = ci[:, 0] * 1_000_003 + ci[:, 1] * 1009 + ci[:, 2]
        ncells = len(np.unique(keys))
        if ncells > target * 1.15:
            cell *= 1.12
        elif ncells < target * 0.85:
            cell *= 0.92
        else:
            break
    ci = np.floor((v - lo) / cell).astype(np.int64)
    keys = ci[:, 0] * 1_000_003 + ci[:, 1] * 1009 + ci[:, 2]
    # representative per cell = vert nearest the cell's centroid
    order = np.argsort(keys, kind="stable")
    keys_s = keys[order]
    bounds = np.flatnonzero(np.r_[True, keys_s[1:] != keys_s[:-1]])
    groups = np.split(order, bounds[1:])
    old2new = np.full(len(v), -1, np.int64)
    reps = []
    for g in groups:
        c = v[g].mean(0)
        rep = g[int(np.argmin(((v[g] - c) ** 2).sum(1)))]
        ni = len(reps)
        reps.append(int(rep))
        old2new[g] = ni
    reps = np.asarray(reps, np.int64)
    t = old2new[np.asarray(tris, np.int64)]
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    return reps, old2new, t[ok]


def _tb_list(x):
    return [tuple(float(c) for c in r) for r in np.asarray(x, np.float64)]


def inject_proxy(nif_path: Path, target: int = 2500) -> str:
    pyn = _pynifly()
    nf = pyn.NifFile(filepath=str(nif_path))
    base = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if base is None:
        return "skip: no BaseShape"
    if any(s.name == VB for s in nf.shapes):
        return "skip: already has VirtualBody"
    bv = np.asarray(base.verts, np.float64)
    bt = np.asarray(base.tris, np.int64)
    reps, old2new, new_tris = decimate_keep_reps(bv, bt, target)
    rep_set = {int(r): int(old2new[r]) for r in reps}      # old idx -> new idx
    nverts = [tuple(float(c) for c in bv[r]) for r in reps]
    uvs = (np.asarray(base.uvs, np.float64) if base.uvs is not None else None)
    nuvs = [tuple(float(c) for c in uvs[r]) for r in reps] if uvs is not None and len(uvs) else [(0.0, 0.0)] * len(reps)
    nrm = (np.asarray(base.normals, np.float64) if base.normals is not None else None)
    nnrm = ([tuple(float(c) for c in nrm[r]) for r in reps] if nrm is not None and len(nrm) else None)
    ntris = [tuple(int(c) for c in r) for r in new_tris]

    ns = nf.createShapeFromData(VB, nverts, ntris, nuvs, nnrm)
    ns.skin()
    for bn in base.bone_names:
        ns.add_bone(bn)
    for bn in base.bone_names:
        try:
            ns.set_skin_to_bone_xform(bn, base.get_shape_skin_to_bone(bn))
        except Exception:
            pass
    if base.has_global_to_skin:
        ns.set_global_to_skin(base.global_to_skin)
    moved = 0
    for bn, pairs in (base.bone_weights or {}).items():
        sub = [(rep_set[int(vi)], float(w)) for vi, w in pairs if int(vi) in rep_set]
        if sub:
            ns.setShapeWeights(bn, sub)
            moved += len(sub)
    # single partition covering all proxy tris (collider is hidden; slot irrelevant)
    try:
        if base.partitions:
            ns.set_partitions([base.partitions[0]], [0] * len(ntris))
    except Exception:
        pass
    try:
        ns.flags = int(getattr(ns, "flags", 0) or 0) | 0x1      # Hidden
    except Exception:
        pass
    atomic_nif_save(nf, nif_path)
    return f"ok: VirtualBody {len(reps)}v/{len(ntris)}t (from {len(bv)}v), weights={moved}"


def repoint_xml(xml_path: Path) -> str:
    if not xml_path.is_file():
        return "no-xml"
    txt = xml_path.read_text("utf-8", "replace")
    if '<per-triangle-shape name="BaseShape">' not in txt:
        return "xml-not-baseshape-collider"
    new = txt.replace('<per-triangle-shape name="BaseShape">',
                      f'<per-triangle-shape name="{VB}">')
    atomic_write_bytes(xml_path, new.encode("utf-8"))
    return "xml-repointed"


def process(nif_path: Path, target: int):
    print(f"\n{nif_path.name}")
    print("  ", inject_proxy(nif_path, target))
    # repoint the shared (weightless) xml referenced by this nif
    stem = nif_path.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-2]
            break
    print("  ", repoint_xml(nif_path.parent / (stem + ".xml")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nif", nargs="?")
    ap.add_argument("--target", type=int, default=2500)
    ap.add_argument("--batch", metavar="MESHES_ROOT")
    a = ap.parse_args()
    if a.batch:
        root = Path(a.batch)
        xmls = [p for p in root.rglob("*.xml")
                if '<per-triangle-shape name="BaseShape">' in p.read_text("utf-8", "ignore")]
        print(f"batch: {len(xmls)} armors use the full-body collider")
        n = 0
        for x in xmls:
            for c in (x.with_name(x.stem + "_1.nif"), x.with_name(x.stem + ".nif"),
                      x.with_name(x.stem + "_0.nif")):
                if c.is_file():
                    process(c, a.target)
                    n += 1
        print(f"\nprocessed {n} NIFs")
    elif a.nif:
        process(Path(a.nif), a.target)
    else:
        ap.error("give a NIF or --batch")


if __name__ == "__main__":
    main()
