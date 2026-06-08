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
            issues.append(f"shape '{s.name}': {nb} bones > {CAP} (CTD risk)")
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
