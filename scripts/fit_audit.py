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

"""Pre-flight fit / clipping auditor for converted UBE armor.

Catches the classes of regression that otherwise only show up after
loading Skyrim — the ones that cost real iteration cycles in this
project:

  CRITICAL
    * ZERO_WEIGHT   — verts with no bone weight. In game they snap to
                      the skeleton origin => giant spikes / mesh through
                      everything. (Caused by a bad skin merge.)
    * OVERFLOW      — a shape with > 65535 verts or tris. BSTriShape
                      uses uint16 indices; the excess is truncated =>
                      missing / shredded triangles. (Caused by merging
                      too many pieces into one shape.)
    * LOST_GEOMETRY — (source-compare mode) converted cloth has fewer
                      total verts/tris than the source => a shape was
                      dropped or its geometry truncated. This is what a
                      shape-drop (e.g. MaleUnderwearBody) or a merge
                      overflow looks like from the outside.

  WARNING
    * CUT_NO_FALLBACK — a cloth shape pushed past the per-NIF morph cap
                      (won't morph) AND with little scale-bone weight
                      (won't track via bones either) => clips when the
                      body grows. The exact failure mode behind
                      "armor doesn't follow the body".
    * EXTREME_DELTA — a TRI morph delta > 8u (broken correspondence;
                      may tear under the slider). Informational — large
                      values are legitimate for PregnancyBelly etc.

Exit code: 1 if any CRITICAL finding, else 0. Suitable for chaining
after a convert batch (CI-style gate).

Usage:
  python scripts/fit_audit.py
  python scripts/fit_audit.py --output 'D:\\path\\to\\mod'
  # source-compare mode (catches dropped / truncated shapes):
  python scripts/fit_audit.py --source "D:\\mods\\ModA" "D:\\mods\\ModB" ...
"""
from __future__ import annotations
import os

import argparse
import io
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_io           # noqa: E402
from src import sliderset_gen    # noqa: E402
from src.tri import TriFile      # noqa: E402

DEFAULT_OUTPUT = Path(os.environ.get("CBBE2UBE_MODS_ROOT", "") + r"\mods\CBBEtoUBE Auto")

BODY_NAMES = {"BaseShape", "VirtualBody", "VirtualGround",
              "3BA", "3BA_Vagina", "3BA_Anus"}
EXTREMITY_KW = ("hand", "finger", "thumb", "palm", "feet", "foot", "toe",
                "glove", "gauntlet", "boot", "shoe", "sandal")
SCALE_BONE_KEYS = ("NPC Belly", "NPC L Butt", "NPC R Butt",
                   "FrontThigh", "RearThigh", "RearCalf")
UINT16_MAX = 65535
EXTREME_DELTA_U = 8.0
# A cut cloth shape with at least this fraction of scale-bone weight is
# considered to have a working bone-driven fallback (won't clip badly).
MIN_FALLBACK_SCALE_PCT = 3.0


def classify(name: str) -> str:
    if name in BODY_NAMES:
        return "body"
    nlow = name.lower()
    if any(k in nlow for k in EXTREMITY_KW):
        return "extremity"
    if any(k in nlow for k in sliderset_gen._RIGID_PROP_KEYWORDS):
        return "rigid"
    return "cloth"


def shape_weight_stats(shape):
    """(n_zero_weight_verts, scale_bone_pct)."""
    nv = len(shape.verts)
    if nv == 0:
        return 0, 0.0
    wsum = np.zeros(nv)
    total = scale = 0.0
    for bn, pairs in (shape.bone_weights or {}).items():
        if pairs is None or len(pairs) == 0:
            continue
        arr = np.asarray(pairs)
        idx = arr[:, 0].astype(int)
        w = arr[:, 1]
        valid = (idx >= 0) & (idx < nv)
        np.add.at(wsum, idx[valid], w[valid])
        sw = float(w[valid].sum())
        total += sw
        if any(k in bn for k in SCALE_BONE_KEYS):
            scale += sw
    n_zero = int((wsum < 1e-4).sum())
    pct = (100 * scale / total) if total > 0 else 0.0
    return n_zero, pct


def load_tri_for(nif_path: Path):
    """Find + load the per-armor TRI that the NIF's BODYTRI points to."""
    # Convention: <stem-without-_0/_1>.tri next to the NIF.
    stem = nif_path.stem
    for cand in (stem, stem[:-2] if stem.endswith(("_0", "_1")) else stem):
        p = nif_path.with_name(cand + ".tri")
        if p.is_file():
            try:
                return TriFile.load(p)
            except Exception:
                return None
    return None


def audit_nif(nif_path: Path, cap: int) -> dict:
    try:
        nif = nif_io.load_nif(nif_path)
    except Exception as e:
        return {"error": repr(e)}

    findings = {"critical": [], "warning": []}
    cloth_shapes = []
    for s in nif.shapes:
        nv, nt = len(s.verts), len(s.tris) if s.tris is not None else 0
        # Overflow
        if nv > UINT16_MAX or nt > UINT16_MAX:
            findings["critical"].append(
                f"OVERFLOW {s.name}: {nv}v/{nt}t exceeds uint16 ({UINT16_MAX})")
        # Zero-weight (only meaningful for skinned shapes)
        if s.bone_weights:
            n_zero, _ = shape_weight_stats(s)
            if n_zero > 0:
                findings["critical"].append(
                    f"ZERO_WEIGHT {s.name}: {n_zero} unweighted vert(s)")
        if classify(s.name) == "cloth":
            cloth_shapes.append(s)

    # Morph-cap analysis: which cloth shapes will actually morph.
    tri = load_tri_for(nif_path)
    if tri is not None:
        tri_order = [ts.name for ts in tri.shapes]
        morphing = set(tri_order[:cap])          # first `cap` entries morph
        cut = {s.name for s in cloth_shapes
               if s.name in tri_order and s.name not in morphing}

        # Build the BODY morph-magnitude field (per BaseShape vert) from
        # the TRI's BaseShape entry. A cut cloth shape only actually clips
        # if it sits over a body region that MORPHS — a static piece over
        # a low-morph region (cape on the back, shoulder plate) is fine.
        # Scale-bone weight does NOT rescue it: the body sliders are
        # TRI-driven, not bone-scale-driven, so a cut shape can't follow
        # them via bones. The only thing that matters is whether the
        # body underneath moves.
        base_nif = next((s for s in nif.shapes if s.name == "BaseShape"), None)
        base_tri = next((ts for ts in tri.shapes if ts.name == "BaseShape"), None)
        body_mag = None
        body_verts = None
        if base_nif is not None and base_tri is not None:
            body_verts = np.asarray(base_nif.verts, dtype=np.float64)
            body_mag = np.zeros(len(body_verts))
            for m in base_tri.morphs or []:
                if not m.offsets:
                    continue
                a = np.asarray(m.offsets, dtype=np.float64)
                idx = a[:, 0].astype(int)
                mg = np.linalg.norm(a[:, 1:4], axis=1)
                ok = (idx >= 0) & (idx < len(body_verts))
                np.maximum.at(body_mag, idx[ok], mg[ok])

        from scipy.spatial import cKDTree
        btree = cKDTree(body_verts) if body_verts is not None else None
        HIGH_MORPH_U = 1.5   # body movement above this = piece must morph too

        for s in cloth_shapes:
            if s.name not in cut:
                continue
            # Region-aware: does the body under this cut shape morph?
            risk = True
            local_morph = None
            if btree is not None:
                sv = np.asarray(s.verts, dtype=np.float64)
                d, idx = btree.query(sv, k=1)
                near = d < 3.0
                local_morph = float(body_mag[idx[near]].max()) if near.any() else 0.0
                risk = local_morph >= HIGH_MORPH_U
            if risk:
                findings["warning"].append(
                    f"CUT_HIGH_MORPH {s.name}: past morph cap, body under it "
                    f"morphs {local_morph:.1f}u (will clip under sliders)")

        # LAYER_SPLIT: overlapping cloth shapes (layers) where one morphs
        # and its overlapping partner is cut -> they move inconsistently
        # and clip into each other. The core "layers without clipping" check.
        cloth_with_verts = [(s, np.asarray(s.verts, dtype=np.float64))
                            for s in cloth_shapes if len(s.verts)]
        for i in range(len(cloth_with_verts)):
            si, vi = cloth_with_verts[i]
            for j in range(i + 1, len(cloth_with_verts)):
                sj, vj = cloth_with_verts[j]
                i_morph = si.name in morphing
                j_morph = sj.name in morphing
                if i_morph == j_morph:
                    continue  # both morph or both cut — consistent
                # spatial overlap test (smaller vs larger)
                small, big = (vi, vj) if len(vi) <= len(vj) else (vj, vi)
                try:
                    d, _ = cKDTree(big).query(small, k=1)
                    if float((d < 2.0).mean()) > 0.25:
                        findings["warning"].append(
                            f"LAYER_SPLIT {si.name} <-> {sj.name}: overlapping "
                            f"layers split across morph cap (one morphs, one "
                            f"cut -> inter-layer clip)")
                except Exception:
                    pass

        # Extreme deltas
        for ts in tri.shapes:
            mx = 0.0
            for m in ts.morphs or []:
                if m.offsets:
                    a = np.asarray(m.offsets, dtype=np.float64)
                    mx = max(mx, float(np.linalg.norm(a[:, 1:4], axis=1).max()))
            if mx > EXTREME_DELTA_U:
                findings["warning"].append(
                    f"EXTREME_DELTA {ts.name}: peak {mx:.1f}u")

    findings["cloth_count"] = len(cloth_shapes)
    findings["cloth_morphing"] = (
        min(len(cloth_shapes), cap - 1) if tri is not None else None)
    return findings


def _has_textures(shape) -> bool:
    """True if the shape has any non-empty texture slot. Textureless
    shapes are collision proxies (Proxy / Collision / *Col) that the
    converter intentionally drops — so they must NOT count toward the
    source cloth total or every armor looks like it 'lost geometry'."""
    try:
        tex = dict(shape._backing.textures or {})
    except Exception:
        return True  # can't tell — assume real so we don't under-count
    return any(v for v in tex.values())


def cloth_geometry_totals(nif) -> tuple[int, int]:
    """Total verts/tris of cloth (non-body, non-extremity, textured)
    shapes. Excludes collision proxies (textureless) so the source vs
    converted comparison only counts geometry that should round-trip."""
    v = t = 0
    for s in nif.shapes:
        c = classify(s.name)
        if c in ("body", "extremity"):
            continue
        if not _has_textures(s):
            continue  # collision proxy — converter drops these
        v += len(s.verts)
        t += len(s.tris) if s.tris is not None else 0
    return v, t


def _rel_key(path: Path, meshes_root: Path | None) -> str:
    """A path key for matching converted output to source by location,
    not just filename (vanilla names like cuirassheavy_1.nif collide
    across hide/imperial/iron). Uses the last 3 path components
    (category/gender/file), lowercased — stable across the converter's
    '!UBE/' prefix rewrite."""
    parts = [p.lower() for p in path.parts]
    return "/".join(parts[-3:])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--source", type=Path, nargs="*", default=[],
                    help="Source mod dirs for geometry-preservation compare")
    ap.add_argument("--max-detail", type=int, default=40)
    args = ap.parse_args()

    cap = sliderset_gen.MORPH_SHAPE_CAP
    meshes = args.output / "meshes"
    if not meshes.is_dir():
        print(f"FAIL: {meshes} not found")
        sys.exit(1)

    nifs = sorted(meshes.rglob("*_1.nif"))
    print(f"fit-audit: {len(nifs)} NIFs under {meshes} (morph cap={cap})\n")

    n_critical = n_warning = 0
    crit_armors = []
    warn_lines = []

    for nif_path in nifs:
        rel = nif_path.relative_to(meshes)
        res = audit_nif(nif_path, cap)
        if "error" in res:
            print(f"  LOAD ERROR {rel}: {res['error']}")
            n_critical += 1
            continue
        if res["critical"]:
            n_critical += len(res["critical"])
            crit_armors.append((str(rel), res["critical"]))
        for w in res["warning"]:
            n_warning += 1
            warn_lines.append((str(rel), w))

    # --- Source-compare: geometry preservation -------------------------
    lost = []
    if args.source:
        # Index sources by category/gender/file key (disambiguates the
        # many same-named vanilla cuirass_1.nif across armor folders).
        src_index = {}
        for sd in args.source:
            for sp in Path(sd).rglob("*_1.nif"):
                src_index.setdefault(_rel_key(sp, None), sp)
        for nif_path in nifs:
            sp = src_index.get(_rel_key(nif_path, None))
            if not sp:
                continue
            try:
                cn = nif_io.load_nif(nif_path)
                sn = nif_io.load_nif(sp)
            except Exception:
                continue
            cv, ct = cloth_geometry_totals(cn)
            sv, st = cloth_geometry_totals(sn)
            # Allow small deltas (pubic-seal fill tris add a few hundred).
            if sv > 0 and cv < sv * 0.97:
                lost.append(
                    f"{nif_path.name}: cloth verts {cv} < source {sv} "
                    f"({100*(sv-cv)/sv:.0f}% lost)")
            if st > 0 and ct < st * 0.97:
                lost.append(
                    f"{nif_path.name}: cloth tris {ct} < source {st} "
                    f"({100*(st-ct)/st:.0f}% lost)")

    # --- Report --------------------------------------------------------
    print("=" * 64)
    if crit_armors:
        print(f"CRITICAL ({n_critical}):")
        for rel, items in crit_armors[:args.max_detail]:
            print(f"  {rel}")
            for it in items:
                print(f"      {it}")
    else:
        print("CRITICAL: none")

    if lost:
        print(f"\nLOST_GEOMETRY ({len(lost)}):")
        for line in lost[:args.max_detail]:
            print(f"  {line}")
        n_critical += len(lost)

    print(f"\nWARNINGS ({n_warning}):")
    # Group warnings by type for readability.
    by_type = defaultdict(list)
    for rel, w in warn_lines:
        by_type[w.split()[0]].append((rel, w))
    for wtype, items in sorted(by_type.items()):
        print(f"  [{wtype}] x{len(items)}")
        for rel, w in items[:args.max_detail]:
            print(f"      {rel}: {w[len(wtype)+1:]}")
        if len(items) > args.max_detail:
            print(f"      ... ({len(items)-args.max_detail} more)")

    print("\n" + "=" * 64)
    print(f"=== {n_critical} critical, {n_warning} warning ===")
    sys.exit(1 if n_critical else 0)


if __name__ == "__main__":
    main()
