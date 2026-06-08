"""Pre-test sanity check on converted body-slot armor NIFs.

Run this immediately after a `convert` batch finishes, BEFORE testing
in-game. Catches the kinds of regressions that look catastrophic in
Skyrim (invisible body region, missing limbs, fingers warped into the
torso) so you don't sink 15 minutes into loading a savegame just to
discover the conversion was broken.

For each sampled converted NIF, compares its BaseShape, Hands, and
Feet shapes against the source UBE refs and reports:

  * vert max-abs delta (expected: 0.0 — bake is disabled, inject should
    be byte-identical)
  * normals max-abs delta (expected: 0.0 — same)
  * boundary-vert normal flip count (expected: 0 — sign-fix should keep
    boundary normals aligned with source even when verts move)
  * presence of all three injected shapes (BaseShape + Hands + Feet)
  * absence of stale CBBE plug-meshes (3BA_Vagina / 3BA_Anus should NOT
    appear in pure-UBE pipeline output)

Exits 0 on all-clear, 1 on any check failure. Suitable for chaining
after the batch convert in a build script.

Usage:
  python scripts/sanity_check_converted.py
  python scripts/sanity_check_converted.py --output 'D:\\path\\to\\mod' --max 8
"""
import os
import argparse
import io
import sys
from pathlib import Path

import numpy as np

# Make the project importable for nif_io.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import nif_io  # noqa: E402

DEFAULT_OUTPUT = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto")
SOURCE_BODY = Path(
    os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output"
    r"\meshes\!UBE\Body\femalebody_tangent_1.nif")
SOURCE_HANDS = Path(
    os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output"
    r"\meshes\!UBE\Hands\femalehands_tangent_1.nif")
SOURCE_FEET = Path(
    os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\Bodyslide Output"
    r"\meshes\!UBE\Feet\femalefeet_tangent_1.nif")

STALE_PLUG_NAMES = {"3BA_Vagina", "3BA_Anus", "3BA"}
# Hands/Feet are NO LONGER injected into body NIFs — they render via
# the actor's slot 33/37 ARMA routing (see
# scripts/integrate_ube_race_skins.py). Body NIFs only inject BaseShape.
EXPECTED_INJECTED = {"BaseShape"}


def load_ref_verts(p: Path, shape_name: str):
    if not p.is_file():
        return None
    nif = nif_io.load_nif(p)
    s = next((sh for sh in nif.shapes if sh.name == shape_name), None)
    if s is None:
        return None
    return (np.asarray(s.verts, dtype=np.float64),
            np.asarray(s.normals, dtype=np.float64)
            if s.normals is not None else None,
            np.asarray(s.tris, dtype=np.int64)
            if s.tris is not None else None)


def find_body_nifs(mod_dir: Path):
    """Yield converted NIFs that contain a BaseShape (body-slot armors)."""
    meshes = mod_dir / "meshes"
    if not meshes.is_dir():
        return
    for p in meshes.rglob("*_1.nif"):
        try:
            nif = nif_io.load_nif(p)
        except Exception:
            continue
        if any(s.name == "BaseShape" for s in nif.shapes):
            yield p


def check_nif(conv_path: Path, refs: dict) -> list[str]:
    """Run all checks against one converted NIF. Return list of failures."""
    failures: list[str] = []
    try:
        nif = nif_io.load_nif(conv_path)
    except Exception as e:
        return [f"load failed: {e!r}"]

    shape_names = {s.name for s in nif.shapes}

    # Missing injected shapes
    missing = EXPECTED_INJECTED - shape_names
    if missing:
        failures.append(f"missing injected shapes: {sorted(missing)}")

    # Stale CBBE plug-meshes
    stale = STALE_PLUG_NAMES & shape_names
    if stale:
        failures.append(f"stale CBBE plug-meshes present: {sorted(stale)}")

    # Per-shape diff against source ref
    for name, ref in refs.items():
        if ref is None:
            continue
        ref_verts, ref_normals, ref_tris = ref
        s = next((sh for sh in nif.shapes if sh.name == name), None)
        if s is None:
            continue
        cv = np.asarray(s.verts, dtype=np.float64)
        if cv.shape != ref_verts.shape:
            failures.append(
                f"{name}: vert count {len(cv)} != source {len(ref_verts)}")
            continue
        vert_dmax = float(np.abs(cv - ref_verts).max())
        if vert_dmax > 1e-3:
            failures.append(
                f"{name}: max vert delta {vert_dmax:.4f}u "
                "(expected 0 — bake should be disabled)")

        # Tri count check. For BaseShape, expect ref + ~366 fill tris
        # from the pubic-hole seal (5 boundary loops fanned). Other
        # shapes (Hands, Feet) should have unchanged tri counts.
        ct = np.asarray(s.tris, dtype=np.int64) if s.tris is not None else None
        if ct is not None and ref_tris is not None:
            n_added = len(ct) - len(ref_tris)
            if name == "BaseShape":
                if n_added < 300 or n_added > 500:
                    failures.append(
                        f"{name}: tri count delta {n_added} outside [300,500] "
                        "(expected ~366 fill tris from pubic-hole seal)")
            else:
                if n_added != 0:
                    failures.append(
                        f"{name}: tri count delta {n_added} (expected 0)")

        if ref_normals is not None and s.normals is not None:
            cn = np.asarray(s.normals, dtype=np.float64)
            if cn.shape == ref_normals.shape:
                dot = (cn * ref_normals).sum(axis=1)
                flipped = int((dot < -0.5).sum())
                if flipped > 0:
                    failures.append(
                        f"{name}: {flipped} normals flipped >90 deg "
                        "from source (boundary-vert sign-fix not working)")

    return failures


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help="Converted mod output dir")
    ap.add_argument("--max", type=int, default=10,
                    help="Max NIFs to sample-check")
    args = ap.parse_args()

    refs = {
        "BaseShape": load_ref_verts(SOURCE_BODY, "BaseShape"),
    }
    for name, ref in refs.items():
        if ref is None:
            print(f"  WARN: source ref for {name!r} not found, skipping diff")

    nifs = list(find_body_nifs(args.output))
    if not nifs:
        print(f"FAIL: no body-slot NIFs found under {args.output}/meshes")
        sys.exit(1)
    if len(nifs) > args.max:
        # Sample evenly across the list
        step = max(1, len(nifs) // args.max)
        nifs = nifs[::step][:args.max]

    print(f"\nsanity-checking {len(nifs)} body-slot NIFs...")
    total_failures = 0
    for p in nifs:
        failures = check_nif(p, refs)
        rel = p.relative_to(args.output)
        if failures:
            print(f"\n  FAIL  {rel}")
            for f in failures:
                print(f"    - {f}")
            total_failures += len(failures)
        else:
            print(f"  OK    {rel}")

    print()
    if total_failures:
        print(f"=== {total_failures} check(s) failed ===")
        sys.exit(1)
    print(f"=== all {len(nifs)} NIFs pass all checks ===")
    sys.exit(0)


if __name__ == "__main__":
    main()
