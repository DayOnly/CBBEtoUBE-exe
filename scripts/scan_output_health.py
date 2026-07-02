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

"""Health scan of a converted UBE output mod. Two passes:

  INVISIBLE (fast, ESP-based): every ARMA in the merged Combined ESP that points
    at a `!UBE\\...` mesh which does NOT exist on disk -> that armour renders
    invisible (referenced-but-not-converted).

  MALFORMED (NIF, parallel): every converted `!UBE` NIF; flags
    - load failure (corrupt write)
    - any shape with > SKIN_PARTITION_BONE_CAP bones (GPU palette overrun = CTD)
    - 0-vertex shape
    - NaN/inf vertex coords

Usage: python scripts/scan_output_health.py "<output mod dir>"
"""
import os, sys, struct, glob
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAP = 78


def _max_partition_bone_count(s):
    """Highest per-PARTITION bone-union for a shape, or None if unreadable.

    The GPU skin-palette cap is PER PARTITION, and the converter's bone-split
    keeps every bone but spreads a high-bone shape across multiple partitions
    (e.g. 80 total bones -> partitions of {78, 9}). So only a SINGLE partition
    over the cap is a real equip-CTD risk; the shape's TOTAL bone count is not
    (it false-positives on every correctly-split shape). Mirrors
    nif_convert._split_oversize_partition's own per-partition accounting.
    """
    import numpy as np
    try:
        raw = getattr(s, "_backing", None)
        pt = getattr(raw, "partition_tris", None)
        tris = np.asarray(s.tris, dtype=np.int64)
        if pt is None:
            return None
        pt = np.asarray(pt, dtype=np.int64)
        if len(tris) == 0 or pt.size != len(tris):
            return None
        nverts = len(s.verts)
        vert_bones = [set() for _ in range(nverts)]
        for bi, bn in enumerate(s.bone_names or []):
            pairs = s.bone_weights.get(bn)
            if pairs is None:
                continue
            seq = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            for vi, _w in seq:
                vi = int(vi)
                if 0 <= vi < nverts:
                    vert_bones[vi].add(bi)
        part_bones: dict[int, set] = {}
        for ti, t in enumerate(tris):
            part_bones.setdefault(int(pt[ti]), set()).update(
                vert_bones[t[0]] | vert_bones[t[1]] | vert_bones[t[2]])
        if not part_bones:
            return None
        return max(len(b) for b in part_bones.values())
    except Exception:
        return None


def _scan_nif(path):
    """Worker: returns (path, [issue strings])."""
    import numpy as np
    from src import nif_io
    issues = []
    try:
        nif = nif_io.load_nif(path)
    except Exception as e:
        return (path, [f"LOAD-FAIL: {e!r}"])
    for s in nif.shapes:
        nb = len(s.bone_names)
        if nb > CAP:
            # Flag only a genuine single-partition palette overrun, not a high
            # TOTAL bone count the bone-split has already made CTD-safe.
            mx = _max_partition_bone_count(s)
            if mx is None:
                issues.append(f"shape '{s.name}': {nb} bones > {CAP}, "
                              f"partitions unreadable (CTD risk)")
            elif mx > CAP:
                issues.append(f"shape '{s.name}': partition with {mx} bones "
                              f"> {CAP} ({nb} total; CTD risk)")
        v = np.asarray(s.verts, dtype=np.float64)
        if v.size == 0:
            issues.append(f"shape '{s.name}': 0 verts")
            continue
        if not np.isfinite(v).all():
            issues.append(f"shape '{s.name}': NaN/inf verts")
    return (path, issues)


def _parse_combined_arma_meshes(esp_path):
    from src import esp as E
    data = open(esp_path, "rb").read()
    off = 0
    rec, off = E.Record.parse(data, off)
    groups = []
    while off < len(data):
        if data[off:off+4] == b"GRUP":
            g, off = E.Group.parse(data, off); groups.append(g)
        else:
            r, off = E.Record.parse(data, off)
    out = []  # (formid, [mesh paths])

    def subs(p):
        i = 0
        while i + 6 <= len(p):
            sig = p[i:i+4]; sz = struct.unpack_from("<H", p, i+4)[0]
            yield sig, p[i+6:i+6+sz]; i += 6 + sz
    for g in groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            mps = [d.split(b"\x00")[0].decode("latin1", "replace")
                   for sig, d in subs(r.payload)
                   if sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")
                   and d.split(b"\x00")[0]]
            out.append((r.formid, mps))
    return out


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto"
    meshes = os.path.join(out_dir, "meshes")
    combined = os.path.join(out_dir, "CBBE_to_UBE_Combined.esp")
    print(f"=== HEALTH SCAN: {out_dir} ===")

    # ---- INVISIBLE pass ----
    missing = []
    if os.path.isfile(combined):
        armas = _parse_combined_arma_meshes(combined)
        for fid, mps in armas:
            for m in mps:
                if m.lower().startswith("!ube\\"):
                    fp = os.path.join(meshes, m.replace("\\", os.sep))
                    if not os.path.isfile(fp):
                        missing.append((fid, m))
        print(f"\n[INVISIBLE] ARMA count: {len(armas)}; "
              f"!UBE meshes referenced but MISSING: {len(missing)}")
        for fid, m in missing[:25]:
            print(f"   ARMA {fid:08X} -> {m}")
        if len(missing) > 25:
            print(f"   ... and {len(missing) - 25} more")
    else:
        print(f"\n[INVISIBLE] no Combined ESP at {combined}")

    # ---- MALFORMED pass ----
    nifs = glob.glob(os.path.join(meshes, "**", "*.nif"), recursive=True)
    print(f"\n[MALFORMED] scanning {len(nifs)} NIFs ...")
    flagged = []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for path, issues in ex.map(_scan_nif, nifs, chunksize=8):
            if issues:
                flagged.append((path, issues))
    over_cap = [f for f in flagged if any("bones >" in i for i in f[1])]
    loadfail = [f for f in flagged if any("LOAD-FAIL" in i for i in f[1])]
    print(f"[MALFORMED] flagged {len(flagged)} NIF(s): "
          f"{len(over_cap)} over-cap, {len(loadfail)} load-fail")
    for path, issues in flagged[:30]:
        rel = path.split("meshes" + os.sep, 1)[-1]
        print(f"   {rel}")
        for i in issues[:4]:
            print(f"       {i}")
    if len(flagged) > 30:
        print(f"   ... and {len(flagged) - 30} more")
    print("\n=== SCAN DONE ===")


if __name__ == "__main__":
    main()
