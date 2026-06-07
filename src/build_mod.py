"""Top-level orchestrator: refit a set of CBBE armor NIFs and emit an
MO2-installable mod folder.

Workflow:
  1. Load CBBE + UBE reference body NIFs, build the morph field.
  2. For each input armor NIF path:
     a. Load via pynifly to get current shape verts (in memory).
     b. For each shape, compute new verts:
        - body shapes (3BA, 3BA_Anus, 3BA_Vagina, Panty) -> direct replace
          with the corresponding shape's UBE verts (same topology)
        - armor shapes -> refit_armor_verts with morph + falloff
     c. Open the source file as bytes; for each shape locate-and-patch in
        place via nif_patch (preserves skin / shader / materials / partitions).
     d. Write the patched bytes to the output mod folder, preserving the
        Data\\meshes\\... relative path.

This module is invokable as `python -m src.build_mod`.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from . import nif_io, nif_patch, correspondence, discovery


log = logging.getLogger("build_mod")


@dataclass
class References:
    """Source (CBBE) and target (UBE) body surfaces, indexed for closest-point
    queries. Topology of CBBE and UBE differ — we project each armor vertex
    onto CBBE, then re-attach to the corresponding UBE surface point preserving
    its relative offset. Uniform treatment for inline body shapes and armor
    shapes alike.
    """
    cbbe: correspondence.MeshIndex
    ube:  correspondence.MeshIndex


def load_references(cbbe_path: Path, ube_path: Path) -> References:
    """Load CBBE + UBE reference bodies and build their MeshIndex spatial
    indices. Uses the largest shape from each NIF as 'the body' (CBBE's 3BA,
    UBE's BaseShape).
    """
    cbbe_nif = nif_io.load_nif(cbbe_path)
    ube_nif  = nif_io.load_nif(ube_path)

    cbbe_body = max(cbbe_nif.shapes, key=lambda s: len(s.verts))
    ube_body  = max(ube_nif.shapes,  key=lambda s: len(s.verts))
    log.info("  CBBE %s (%d verts, %d tris)  ->  UBE %s (%d verts, %d tris)",
        cbbe_body.name, len(cbbe_body.verts), len(cbbe_body.tris),
        ube_body.name,  len(ube_body.verts),  len(ube_body.tris))

    return References(
        cbbe=correspondence.MeshIndex.build(cbbe_body.verts, cbbe_body.tris),
        ube=correspondence.MeshIndex.build(ube_body.verts,  ube_body.tris),
    )


def load_reference_pair(
    cbbe_0: Path, cbbe_1: Path, ube_0: Path, ube_1: Path,
) -> tuple[References, References]:
    """Load both weight 0 (slim) and weight 1 (full) reference pairs."""
    log.info("loading weight-0 references...")
    refs_0 = load_references(cbbe_0, ube_0)
    log.info("loading weight-1 references...")
    refs_1 = load_references(cbbe_1, ube_1)
    return refs_0, refs_1


def _pick_refs_for_path(src_path: Path, refs_0: References, refs_1: References) -> References:
    """Pick weight 0 or weight 1 references based on the NIF filename suffix.
    Files named `<base>_0.nif` use slim refs; `_1.nif` and anything else use
    full refs (the more common variant).
    """
    stem = src_path.stem.lower()
    if stem.endswith("_0"):
        return refs_0
    return refs_1


def _compute_new_verts_for_shape(
    shape: nif_io.Shape,
    refs: References,
    *,
    falloff_distance: float,   # accepted for API compatibility; not used by correspondence
) -> np.ndarray:
    """Apply the closest-point-on-mesh refit to one shape. Same treatment for
    inline body shapes (3BA, 3BA_Anus, 3BA_Vagina) as for armor pieces — they
    all get projected onto CBBE and re-attached to the UBE surface preserving
    their local offset. Topology never changes; only positions.
    """
    log.debug("  %s: refit (%d verts)", shape.name, len(shape.verts))
    displacement = correspondence.compute_deformation(
        shape.verts, refs.cbbe, refs.ube,
    )
    return (shape.verts.astype(np.float64) + displacement).astype(np.float32)


def _verts_loader(src_path: Path) -> dict[str, list[tuple[float, float, float]]]:
    """Adapter for nif_patch: re-load source verts as float tuples."""
    nif = nif_io.load_nif(src_path)
    return {s.name: [tuple(v) for v in s.verts.tolist()] for s in nif.shapes}


def refit_one_nif(
    src_path: Path,
    dst_path: Path,
    refs: References,
    *,
    falloff_distance: float = 5.0,
) -> dict[str, nif_patch.VertexBlockLocation | None]:
    """Refit a single armor NIF. Returns the locator results per shape so the
    caller can see which shapes were successfully patched.
    """
    src = nif_io.load_nif(src_path)
    log.info("refit %s -> %s (%d shapes)", src_path.name, dst_path, len(src.shapes))

    shapes_to_patch: list[tuple[str, np.ndarray]] = []
    for shape in src.shapes:
        if len(shape.verts) == 0:
            continue
        new_verts = _compute_new_verts_for_shape(
            shape, refs, falloff_distance=falloff_distance,
        )
        shapes_to_patch.append((shape.name, new_verts))

    return nif_patch.patch_nif_shapes(
        src_path, dst_path, shapes_to_patch, locator_loader=_verts_loader,
    )


def refit_paths(
    armor_paths: Iterable[tuple[Path, Path]],
    refs: References,
    *,
    falloff_distance: float = 5.0,
) -> None:
    """Refit a batch of (source, dest) NIF path pairs."""
    for src, dst in armor_paths:
        result = refit_one_nif(src, dst, refs, falloff_distance=falloff_distance)
        for shape_name, loc in result.items():
            if loc is None:
                log.warning("    %s: could not locate in file (skipped)", shape_name)
            else:
                log.info("    %s: patched %d verts @ offset=0x%x stride=%d",
                         shape_name, loc.vertex_count, loc.file_offset, loc.stride)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# Defaults are auto-discovered at runtime (no hardcoded modpack paths) — see
# `_resolve_defaults`. argparse defaults stay None; anything still None after
# resolution means "not found, user must pass it explicitly".
DEFAULT_CBBE_0 = DEFAULT_CBBE_1 = None
DEFAULT_UBE_0 = DEFAULT_UBE_1 = None
DEFAULT_OUT_MOD = DEFAULT_MODS_ROOT = DEFAULT_PROFILE_DIR = None


def _resolve_defaults(args) -> None:
    """Fill any unset (None) path args from the auto-discovered MO2 layout +
    content-based body discovery. Mutates `args` in place."""
    from . import paths as _paths
    from . import nif_convert as _nc
    lay = _paths.discover_layout()
    _paths.export_to_env(lay)
    mr = _paths.mods_root()
    if getattr(args, "mods_root", None) is None and mr is not None:
        args.mods_root = mr
    if getattr(args, "profile_dir", None) is None and lay.instance_dir:
        prof = lay.selected_profile or ""
        cand = lay.instance_dir / "profiles" / prof
        args.profile_dir = cand if cand.is_dir() else None
    if getattr(args, "out_mod", None) is None and mr is not None:
        args.out_mod = mr / "CBBEtoUBE Refits"
    if getattr(args, "cbbe_0", None) is None:
        args.cbbe_0 = _nc._find_cbbe_base_body("_0")
    if getattr(args, "cbbe_1", None) is None:
        args.cbbe_1 = _nc._find_cbbe_base_body("_1")
    if getattr(args, "ube_0", None) is None:
        args.ube_0 = _nc._find_user_preset_body("_0")
    if getattr(args, "ube_1", None) is None:
        args.ube_1 = _nc._find_user_preset_body("_1")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="build_mod",
        description="Refit CBBE-shape armor NIFs to UBE-shape and emit an MO2 mod folder.")
    p.add_argument("--cbbe-0", type=Path, default=DEFAULT_CBBE_0,
                   help="CBBE 3BA reference body, weight 0 (slim).")
    p.add_argument("--cbbe-1", type=Path, default=DEFAULT_CBBE_1,
                   help="CBBE 3BA reference body, weight 1 (full).")
    p.add_argument("--ube-0",  type=Path, default=DEFAULT_UBE_0,
                   help="UBE reference body, weight 0. UBE uses custom path !UBE\\Body\\femalebody_tangent_0.nif.")
    p.add_argument("--ube-1",  type=Path, default=DEFAULT_UBE_1,
                   help="UBE reference body, weight 1.")
    p.add_argument("--out-mod",  type=Path, default=DEFAULT_OUT_MOD,
                   help="Target MO2 mod folder. meshes/... layout will be created inside.")
    p.add_argument("--falloff",  type=float, default=5.0,
                   help="Morph falloff distance from body surface (units).")
    p.add_argument("-v", "--verbose", action="count", default=0)

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-refs",
        help="Print what's being treated as CBBE vs UBE and the mean morph distance. "
             "Useful as a sanity check before committing to a batch refit.")

    one = sub.add_parser("one", help="Refit a single NIF file.")
    one.add_argument("src_path", type=Path,
                     help="Source NIF (e.g., from a source armor mod folder).")
    one.add_argument("--data-relpath", type=Path, default=None,
                     help="Path under Data\\meshes\\ to emit at. If absent, infer from src.")

    disc = sub.add_parser("discover",
        help="Dry-run: walk MO2 mods to list candidate NIFs (no refit).")
    disc.add_argument("--mods-root",   type=Path, default=DEFAULT_MODS_ROOT)
    disc.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    disc.add_argument("--skip-mods",   nargs="*", default=["CBBEtoUBE Refits"],
                      help="Mod folder names to ignore (e.g. our own output).")
    disc.add_argument("--no-classify", action="store_true",
                      help="Skip per-NIF inspection (lists all candidates by path heuristic only).")

    fromm = sub.add_parser("from-mods",
        help="Discover CBBE 3BA armors in the MO2 load order and refit them all.")
    fromm.add_argument("--mods-root",   type=Path, default=DEFAULT_MODS_ROOT)
    fromm.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    fromm.add_argument("--skip-mods",   nargs="*", default=["CBBEtoUBE Refits"],
                      help="Mod folder names to ignore.")
    fromm.add_argument("--limit",       type=int, default=0,
                      help="Stop after refitting N NIFs (0 = no limit). Useful for tests.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _resolve_defaults(args)  # fill unset paths from auto-discovery
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    if args.cmd == "check-refs":
        for w, cbbe_p, ube_p in (
            (0, args.cbbe_0, args.ube_0),
            (1, args.cbbe_1, args.ube_1),
        ):
            print(f"\n--- weight {w} ---")
            print(f"  CBBE: {cbbe_p}")
            print(f"  UBE:  {ube_p}")
            if not cbbe_p.exists():
                print(f"  CBBE MISSING"); continue
            if not ube_p.exists():
                print(f"  UBE  MISSING"); continue
            cn = nif_io.load_nif(cbbe_p); cb = max(cn.shapes, key=lambda s: len(s.verts))
            un = nif_io.load_nif(ube_p);  ub = max(un.shapes, key=lambda s: len(s.verts))
            cv = np.asarray(cb.verts, dtype=np.float64)
            uv = np.asarray(ub.verts, dtype=np.float64)
            print(f"  CBBE body: shape='{cb.name}' verts={len(cv)} tris={len(cb.tris)}  mtime={cbbe_p.stat().st_mtime_ns}")
            print(f"  UBE  body: shape='{ub.name}' verts={len(uv)} tris={len(ub.tris)}  mtime={ube_p.stat().st_mtime_ns}")
            if cv.shape == uv.shape:
                d = np.linalg.norm(uv - cv, axis=1)
                print(f"  same-topology morph stats: mean={d.mean():.4f} max={d.max():.4f}")
            else:
                print(f"  TOPOLOGY MISMATCH ({len(cv)} -> {len(uv)} verts) - correspondence refit will be used")
        return 0

    if args.cmd == "discover":
        wins = discovery.find_winning_nifs(
            mods_root=args.mods_root,
            profile_dir=args.profile_dir,
            skip_mods=tuple(args.skip_mods),
            classify=not args.no_classify,
        )
        log.warning("=== %d candidate NIFs found ===", len(wins))
        cbbe = [w for w in wins if w.has_3ba_body]
        rest = [w for w in wins if not w.has_3ba_body]
        log.warning("  with 3BA body (refit targets):   %d", len(cbbe))
        log.warning("  without 3BA body (will skip):    %d", len(rest))
        for w in cbbe[:40]:
            print(f"  REFIT  [{w.provider_mod}]  {w.relative_path}")
        if len(cbbe) > 40:
            print(f"  ... and {len(cbbe) - 40} more")
        return 0

    refs_0, refs_1 = load_reference_pair(args.cbbe_0, args.cbbe_1, args.ube_0, args.ube_1)

    if args.cmd == "one":
        src = args.src_path
        if args.data_relpath is None:
            parts = src.parts
            try:
                i = [p.lower() for p in parts].index("meshes")
                rel = Path(*parts[i:])
            except ValueError:
                log.error("could not find 'meshes' in source path; pass --data-relpath")
                return 2
        else:
            rel = args.data_relpath
        dst = args.out_mod / rel
        refs = _pick_refs_for_path(src, refs_0, refs_1)
        refit_paths([(src, dst)], refs, falloff_distance=args.falloff)
        log.info("done: %s", dst)
        return 0

    if args.cmd == "from-mods":
        wins = discovery.find_winning_nifs(
            mods_root=args.mods_root,
            profile_dir=args.profile_dir,
            skip_mods=tuple(args.skip_mods),
            classify=True,
        )
        targets = [w for w in wins if w.has_3ba_body]
        log.warning("=== refitting %d CBBE 3BA armor NIFs ===", len(targets))
        if args.limit:
            targets = targets[:args.limit]
            log.warning("limited to %d for this run", len(targets))
        ok = 0
        for i, w in enumerate(targets, 1):
            dst = args.out_mod / w.relative_path
            refs = _pick_refs_for_path(w.source_path, refs_0, refs_1)
            try:
                result = refit_one_nif(
                    w.source_path, dst, refs, falloff_distance=args.falloff,
                )
                missed = [n for n, loc in result.items() if loc is None]
                if missed:
                    log.warning("  [%d/%d] %s: %d shape(s) NOT located: %s",
                        i, len(targets), w.relative_path, len(missed), missed[:3])
                else:
                    ok += 1
            except Exception as e:
                log.error("  [%d/%d] %s FAILED: %s", i, len(targets), w.relative_path, e)
        log.warning("=== batch done: %d/%d fully refit ===", ok, len(targets))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
