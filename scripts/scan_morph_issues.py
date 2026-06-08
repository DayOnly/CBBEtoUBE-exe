"""Programmatic scan of converted NIFs for morph issues across the whole
output mod.

Walks every NIF under `<output-mod>/meshes/`, resolves its BODYTRI, loads
the per-armor TRI, and reports per-shape morph health. The point is to
catch regressions like the Daedric leggings failure (cloth shape with
valid TRI entry but BODYTRI on wrong carrier) and broken correspondence
(extreme delta magnitudes that tear the mesh under sliders) WITHOUT
having to inspect 1463 BMP previews by hand.

Issue categories reported:

  STATIC_CLOTH        — cloth-named shape NOT listed in TRI. In game it
                        won't move when sliders change; body grows
                        through it. The bug.
  MISSING_BODYTRI     — BODYTRI extra-data block absent. Whole NIF won't
                        morph. Acceptable for amulets/rings/weapons,
                        bug for body-region armor.
  BROKEN_BODYTRI      — BODYTRI string present but TRI file missing on
                        disk or unparseable. Converter regression.
  EXTREME_DELTA       — peak ||delta|| > 8u on cloth shape. Likely
                        broken CBBE->UBE correspondence; the slider will
                        tear that vert cluster.
  ZERO_COVERAGE       — TRI lists the shape but every morph has zero
                        offsets. Shape registered in the TRI but won't
                        actually move. Generator bug.

Usage:
  python scripts/scan_morph_issues.py
  python scripts/scan_morph_issues.py --output 'D:\\path\\to\\mod' --max-detail 20
"""
from __future__ import annotations
import os

import argparse
import io
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tri import TriFile  # noqa: E402

DEFAULT_OUTPUT = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto")

# Cloth-keyword substrings (lowercased) — if a shape name matches one,
# we EXPECT it to morph with the body. Missing from TRI → bug.
CLOTH_KEYWORDS = (
    "corset", "leather", "fabric", "tabard", "panty", "panties",
    "skirt", "tasset", "tassel", "belt", "shirt", "robe", "dress",
    "cloak", "cape", "loin", "underwear", "wrap", "sash", "harness",
    "thong", "garter", "stocking", "bra", "chest", "torso", "hip",
    "pant", "legging", "trouser", "armor", "armour", "cuirass",
    "vest", "bodice", "leg",
)
# Substrings that mark a shape as a rigid prop/accessory — static is
# fine for these and we don't flag them.
RIGID_PROP_KEYWORDS = (
    "dagger", "scabbard", "sword", "bow", "arrow", "quiver",
    "shield", "amulet", "ring", "necklace", "circlet", "crown",
    "earring", "nail", "gem", "stud", "buckle", "rivet", "clasp",
    "pauldron", "shoulder", "metal", "decoration", "deco",
    "ornament", "spike", "chain", "pendant", "collar", "horn",
    "skull", "claw",
)
# Hand/foot/glove/boot — these shapes don't get body-driven morphs
# (per task #94 / #87). Static is expected.
EXTREMITY_KEYWORDS = (
    "hand", "finger", "thumb", "palm", "feet", "foot", "toe",
    "glove", "gauntlet", "boot", "shoe", "sandal",
)
# Body / synthetic shapes that aren't armor pieces — skip outright.
SYNTHETIC_NAMES = frozenset({
    "VirtualBody", "VirtualGround", "BaseShape", "3BA",
    "3BA_Vagina", "3BA_Anus",
})

EXTREME_DELTA_U = 8.0  # peak ||delta|| above this is suspect


def classify_shape(name: str) -> str:
    nlow = name.lower()
    if any(kw in nlow for kw in EXTREMITY_KEYWORDS):
        return "extremity"
    if any(kw in nlow for kw in RIGID_PROP_KEYWORDS):
        return "rigid"
    if any(kw in nlow for kw in CLOTH_KEYWORDS):
        return "cloth"
    return "unknown"


def find_bodytri_string(nf) -> str | None:
    for s in nf.shapes:
        try:
            for ed in s.extra_data():
                if getattr(ed, "name", None) == "BODYTRI":
                    return ed.string_data
        except Exception:
            continue
    return None


def resolve_bodytri(nif_path: Path, bodytri_path: str) -> Path | None:
    norm = bodytri_path.replace("\\", "/").lstrip("/")
    p = nif_path.resolve()
    for parent in [p, *p.parents]:
        if parent.name.lower() == "meshes":
            candidates = [parent / norm]
            if norm.lower().startswith("meshes/"):
                candidates.append(parent.parent / norm)
            for c in candidates:
                if c.is_file():
                    return c
            return None
    return None


def shape_morph_stats(tri_sh) -> dict:
    """Return {max_mag, mean_mag, p99_mag, total_offsets, morphs_with_offsets}.
    Aggregated across all morphs.
    """
    all_mags: list[float] = []
    total_offsets = 0
    morphs_with_offsets = 0
    for m in tri_sh.morphs:
        if not m.offsets:
            continue
        morphs_with_offsets += 1
        arr = np.asarray(m.offsets, dtype=np.float64)
        if arr.shape[0] == 0:
            continue
        d = arr[:, 1:4]
        mags = np.linalg.norm(d, axis=1)
        all_mags.append(mags)
        total_offsets += len(arr)
    if not all_mags:
        return {
            "max_mag": 0.0, "mean_mag": 0.0, "p99_mag": 0.0,
            "total_offsets": 0, "morphs_with_offsets": 0,
        }
    flat = np.concatenate(all_mags)
    return {
        "max_mag": float(flat.max()),
        "mean_mag": float(flat.mean()),
        "p99_mag": float(np.percentile(flat, 99)),
        "total_offsets": total_offsets,
        "morphs_with_offsets": morphs_with_offsets,
    }


def scan_nif(nif_path: Path) -> dict:
    """Return per-NIF + per-shape diagnostic dict, or {'error': ...} if
    unreadable."""
    import sys
    here = Path(__file__).resolve().parent.parent
    pyn_dir = here / ".pynifly"
    if str(pyn_dir) not in sys.path:
        sys.path.insert(0, str(pyn_dir))
    from pyn import pynifly  # type: ignore[import-not-found]

    try:
        nf = pynifly.NifFile(filepath=str(nif_path))
    except Exception as e:
        return {"error": f"load failed: {e!r}"}

    shape_names = [s.name for s in nf.shapes]
    bodytri_str = find_bodytri_string(nf)
    tri: TriFile | None = None
    bodytri_resolved = False
    tri_path_actual = None
    parse_err = None
    if bodytri_str:
        tri_disk = resolve_bodytri(nif_path, bodytri_str)
        if tri_disk:
            tri_path_actual = tri_disk
            try:
                tri = TriFile.load(tri_disk)
                bodytri_resolved = True
            except Exception as e:
                parse_err = repr(e)

    tri_shape_names = {sh.name for sh in tri.shapes} if tri else set()
    tri_by_name = {sh.name: sh for sh in tri.shapes} if tri else {}

    # Carrier shape: which shape holds the BODYTRI?
    carrier = None
    for s in nf.shapes:
        try:
            for ed in s.extra_data():
                if getattr(ed, "name", None) == "BODYTRI":
                    carrier = s.name
                    break
            if carrier:
                break
        except Exception:
            pass

    shape_diag: list[dict] = []
    for s in nf.shapes:
        if s.name in SYNTHETIC_NAMES:
            continue
        cls = classify_shape(s.name)
        n_verts = len(s.verts) if s.verts is not None else 0
        in_tri = s.name in tri_shape_names
        stats = shape_morph_stats(tri_by_name[s.name]) if in_tri else {}
        shape_diag.append({
            "name": s.name,
            "class": cls,
            "n_verts": n_verts,
            "in_tri": in_tri,
            **stats,
        })

    return {
        "bodytri_str": bodytri_str,
        "bodytri_resolved": bodytri_resolved,
        "parse_err": parse_err,
        "carrier": carrier,
        "tri_path": str(tri_path_actual) if tri_path_actual else None,
        "shape_names": shape_names,
        "shapes": shape_diag,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--max-detail", type=int, default=20,
                    help="Max example NIFs to list per issue category")
    args = ap.parse_args()

    meshes = args.output / "meshes"
    if not meshes.is_dir():
        print(f"FAIL: {meshes} not found")
        sys.exit(1)

    nifs = sorted(meshes.rglob("*.nif"))
    print(f"scanning {len(nifs)} NIFs under {meshes} ...")

    # Per-issue collectors (each is a list of (rel_nif, shape_name, detail))
    static_cloth: list[tuple[str, str, str]] = []
    extreme_delta: list[tuple[str, str, float]] = []
    zero_coverage: list[tuple[str, str, int]] = []
    missing_bodytri_with_cloth: list[tuple[str, list[str]]] = []
    broken_bodytri: list[tuple[str, str]] = []
    load_errors: list[tuple[str, str]] = []

    # Aggregate counts
    n_total = len(nifs)
    n_with_bodytri = 0
    n_resolved = 0
    n_carrier_body = 0
    n_carrier_cloth = 0
    n_carrier_none = 0
    shapes_total = 0
    shapes_static = 0
    shapes_with_morph = 0

    carrier_shape_tally: Counter = Counter()
    cloth_in_tri_by_src: Counter = Counter()
    cloth_missing_by_src: Counter = Counter()

    for nif_path in nifs:
        rel = str(nif_path.relative_to(meshes))
        # Source mod from the first path component under meshes/ is
        # noisy (it groups everything under '!UBE/armor/...'). Use the
        # second-level dir as a rough family bucket.
        parts = nif_path.relative_to(meshes).parts
        family = "/".join(parts[:2]) if len(parts) > 1 else parts[0]

        diag = scan_nif(nif_path)
        if "error" in diag:
            load_errors.append((rel, diag["error"]))
            continue

        if diag["bodytri_str"]:
            n_with_bodytri += 1
            if diag["bodytri_resolved"]:
                n_resolved += 1
            else:
                err = diag["parse_err"] or "unresolvable on disk"
                broken_bodytri.append((rel, err))
        else:
            # No BODYTRI — flag only if there's a cloth-named shape that
            # needs morphs. Amulet NIFs with only rigid props are fine.
            cloth_shapes = [s["name"] for s in diag["shapes"]
                            if s["class"] == "cloth"]
            if cloth_shapes:
                missing_bodytri_with_cloth.append((rel, cloth_shapes))

        c = diag["carrier"]
        if c is None:
            n_carrier_none += 1
        elif c in ("BaseShape", "3BA"):
            n_carrier_body += 1
        else:
            n_carrier_cloth += 1
        carrier_shape_tally[c or "<none>"] += 1

        for sd in diag["shapes"]:
            shapes_total += 1
            if not sd["in_tri"]:
                shapes_static += 1
                if sd["class"] == "cloth" and diag["bodytri_resolved"]:
                    static_cloth.append((rel, sd["name"], f"verts={sd['n_verts']}"))
                    cloth_missing_by_src[family] += 1
            else:
                shapes_with_morph += 1
                if sd["class"] == "cloth":
                    cloth_in_tri_by_src[family] += 1
                max_mag = sd.get("max_mag", 0.0)
                p99_mag = sd.get("p99_mag", 0.0)
                if max_mag > EXTREME_DELTA_U:
                    extreme_delta.append((rel, sd["name"], max_mag))
                if (sd.get("morphs_with_offsets", 0) > 0
                        and sd.get("total_offsets", 0) == 0):
                    zero_coverage.append((rel, sd["name"], 0))

    # ---- Print report -----------------------------------------------------
    print()
    print(f"=== NIF-LEVEL SUMMARY ({n_total} NIFs) ===")
    print(f"  BODYTRI present       : {n_with_bodytri}")
    print(f"  BODYTRI resolved      : {n_resolved}")
    print(f"  Carrier = body shape  : {n_carrier_body}")
    print(f"  Carrier = cloth shape : {n_carrier_cloth}")
    print(f"  Carrier = none        : {n_carrier_none}")
    print()
    print(f"  Shapes total          : {shapes_total}")
    print(f"  Shapes in TRI         : {shapes_with_morph}")
    print(f"  Shapes static         : {shapes_static}")

    print()
    print(f"=== TOP CARRIER SHAPES ({len(carrier_shape_tally)} distinct) ===")
    for name, ct in carrier_shape_tally.most_common(15):
        print(f"  {name:<35} {ct:>5}")

    def section(title: str, items, fmt_item):
        print()
        print(f"=== {title}  ({len(items)} hits) ===")
        if not items:
            print("  (clean)")
            return
        for it in items[:args.max_detail]:
            print(f"  {fmt_item(it)}")
        if len(items) > args.max_detail:
            print(f"  ... ({len(items) - args.max_detail} more)")

    section("BROKEN_BODYTRI", broken_bodytri,
            lambda x: f"{x[0]}  ({x[1]})")
    section("MISSING_BODYTRI on NIF with cloth shapes", missing_bodytri_with_cloth,
            lambda x: f"{x[0]}  cloth shapes: {', '.join(x[1][:5])}")
    section("STATIC_CLOTH (cloth shape not in TRI, BODYTRI present)",
            static_cloth, lambda x: f"{x[0]} :: {x[1]} ({x[2]})")
    section("EXTREME_DELTA (peak ||delta|| > 8u — possible tearing)",
            sorted(extreme_delta, key=lambda x: -x[2]),
            lambda x: f"{x[0]} :: {x[1]}  max={x[2]:.2f}u")
    section("ZERO_COVERAGE (morph block empty — generator bug)",
            zero_coverage, lambda x: f"{x[0]} :: {x[1]}")
    section("LOAD_ERROR (NIF unreadable)",
            load_errors, lambda x: f"{x[0]}  ({x[1]})")

    print()
    print(f"=== CLOTH-SHAPE TRI COVERAGE BY FAMILY ===")
    fams = sorted(set(cloth_in_tri_by_src) | set(cloth_missing_by_src))
    for f in fams[:30]:
        ok = cloth_in_tri_by_src.get(f, 0)
        miss = cloth_missing_by_src.get(f, 0)
        total = ok + miss
        if total == 0:
            continue
        pct = 100 * ok / total
        print(f"  {f:<50} {ok:>4}/{total:<4}  ({pct:5.1f}%)")
    if len(fams) > 30:
        print(f"  ... ({len(fams) - 30} more families)")


if __name__ == "__main__":
    main()
