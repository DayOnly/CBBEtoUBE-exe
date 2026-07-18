"""Post-reconvert check for the layered-cloth equip-CTD fix (#layered-cloth-skin).

Run AFTER a reconvert, BEFORE the in-game equip test. For every converted mesh that
has a multi-layer cloth group (Cuirass_A/_B/_C etc.), it confirms the layer shapes
carry NO body HDT-SMP jiggle bones that weren't in their SOURCE -- i.e. the graft that
CTDs FSMP on equip is gone. Any mesh that still shows grafted SMP bones means the fix
did NOT land for it (stale exe / incremental skip) -- fix that before testing in-game.

    python scripts/verify_layered_cloth.py

Read-only; resolves sources via the live MO2 instance (CBBE2UBE_MO2_INI).
"""
import os
import re
import sys
import glob
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))
from pyn import pynifly                        # noqa: E402
from src import paths, discovery               # noqa: E402

OUT_MOD = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
_SUFFIX = re.compile(r"^(.*?)[_ ]([A-Za-z]|\d{1,2})$")
_JIGGLE = ("breast", "butt", "belly")


def _shapes(path):
    try:
        n = pynifly.NifFile(filepath=str(path))
    except Exception:
        return None
    return {s.name: set(s.bone_names or []) for s in n.shapes}


def _layer_names(shape_names):
    groups = {}
    for nm in shape_names:
        m = _SUFFIX.match(nm or "")
        if m:
            groups.setdefault(m.group(1).lower(), []).append(nm)
    return {n for v in groups.values() if len(v) >= 2 for n in v}


def main():
    lay = paths.discover_layout()
    if lay.mods_root is None:
        print("MO2 layout not found (set CBBE2UBE_MO2_INI).")
        return 1
    enabled = paths.enabled_mods_ordered(lay)
    out_root = lay.mods_root / OUT_MOD / "meshes" / "!UBE"
    if not out_root.is_dir():
        print(f"output not found: {out_root}\n(run a reconvert first)")
        return 1

    files = [f for f in glob.glob(str(out_root / "**" / "*_1.nif"), recursive=True)
             if "1stperson" not in f.lower()]
    print(f"scanning {len(files)} converted meshes for layered cloth...", flush=True)
    layered = {}
    for f in files:
        S = _shapes(f)
        if not S:
            continue
        ln = _layer_names(S.keys())
        if ln:
            rel = f.replace("\\", "/").split("/!UBE/", 1)[1]
            layered[rel] = (S, ln)

    print(f"{len(layered)} meshes have multi-layer cloth; checking for grafted SMP bones...\n")
    src_idx = discovery.build_mesh_index(
        lay.mods_root, enabled,
        target_keys={r.lower() for r in layered}, skip_mods=(OUT_MOD,))

    bad = []
    for rel, (out_shapes, layer_names) in sorted(layered.items()):
        src_path = src_idx.get(rel.lower())
        src_shapes = _shapes(src_path) if src_path and os.path.isfile(src_path) else {}
        grafted = {}
        for nm in layer_names:
            out_b = out_shapes.get(nm, set())
            src_b = src_shapes.get(nm, set())
            g = [b for b in out_b - src_b if any(t in b.lower() for t in _JIGGLE)]
            if g:
                grafted[nm] = len(g)
        if grafted:
            bad.append(rel)
        print(f"  {'FAIL' if grafted else 'ok  '}  {rel}"
              + (f"  {grafted}" if grafted else ""))

    print(f"\n{len(layered)} layered meshes; {len(bad)} still carry grafted SMP jiggle "
          f"bones on a layer shape.")
    if bad:
        print("-> the fix did NOT land for those (stale exe / incremental skip). "
              "Rebuild+redeploy the exe and full-reconvert before the in-game test.")
    else:
        print("-> all clear: no grafted SMP bones on any layered cloth. Safe to equip-test "
              "the Noble Dark Leather in-game.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
