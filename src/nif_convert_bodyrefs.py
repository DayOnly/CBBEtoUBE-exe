"""Body discovery and mod-tree lookups: where the CBBE base body, the UBE
body, the UBE ShapeData / OSD and the user's preset body are, found by
SCANNING the discovered mods root by content and name hint -- never by a
fixed mod name -- with env-var escape hatches.

Split out of nif_convert.py on 2026-09-01 (split step 3). Imported by name
into nif_convert, so `nc._find_ube_femalebody(...)` and friends keep working
for every caller, script and test. The three caches (`_BODY_DISCOVERY_CACHE`,
`_MOD_DIR_LIST_CACHE`, `_GLOB_FIRST_MEMO`) are per-process, mutated in place
and NEVER rebound -- tests reset them with `.clear()`, not by assignment, so
both modules keep seeing the same objects.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import numpy as np

from . import nif_io
from . import paths as _paths

# --- Portable body discovery (no hardcoded modpack paths) -------------
# The mods root is auto-discovered at runtime (see src/paths.py) from the
# MO2 instance and propagated to worker processes via the CBBE2UBE_MODS_ROOT
# env var. The CBBE base body and UBE body are then found by SCANNING that
# root by content + name hint, never by a fixed mod name — so the tool works
# in any modpack. Env-var overrides are the escape hatch for unusual layouts,
# and they are NOT spelled the same way: the CBBE body is weight-specific only
# (CBBE2UBE_CBBE_BODY_0 / _1 — a bare CBBE2UBE_CBBE_BODY is read by nothing),
# while the UBE body takes CBBE2UBE_UBE_BODY_0 / _1 OR the bare
# CBBE2UBE_UBE_BODY the GUI picker writes, from which the weight sibling is
# derived.

_CBBE_3BA_VERTS = 18436  # canonical CBBE 3BA femalebody topology
# Name hints (lowercased substrings) — content is always re-validated; a hint
# only RANKS candidates, it's never the sole criterion.
_CBBE_BODY_NAME_HINTS = ("cbbe", "3ba", "3bbb")
_UBE_BODY_NAME_HINTS = ("ube",)
_BODYSLIDE_OUT_HINTS = ("bodyslide output", "bodyslide_output", "bodyslide-output")
_FEMBODY_REL = ("meshes", "actors", "character", "character assets")

# Per-process cache so the scan runs once.
_BODY_DISCOVERY_CACHE: "dict[str, Path | None]" = {}


def _iter_femalebody_nifs(weight: str):
    """Yield (mod_dir, nif_path) for every installed mod that ships a
    meshes/actors/character/character assets/femalebody<weight>.nif."""
    root = _paths.mods_root()
    if root is None or not root.is_dir():
        return
    fname = f"femalebody{weight}.nif"
    try:
        mod_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return
    for mod in mod_dirs:
        p = mod.joinpath(*_FEMBODY_REL, fname)
        if p.is_file():
            yield mod, p


def _shape_has_3ba_topology(nif_path: Path) -> bool:
    try:
        nf = nif_io.open_nif_retry(str(nif_path))  # transient-IO resilient
        return any(len(s.verts) == _CBBE_3BA_VERTS for s in nf.shapes)
    except Exception:
        return False


def _find_cbbe_base_body(weight: str = "_1") -> "Path | None":
    """Locate the CBBE 3BA base (template, slider-zero) femalebody NIF by
    scanning installed mods for a femalebody with the 18,436-vert 3BA
    topology, preferring a CBBE/3BA-named mod and EXCLUDING BodySlide-output
    mods (those carry the morphed UBE body, not the CBBE template). This is
    the baseline the CBBE->UBE warp morphs FROM. Returns None if no CBBE 3BA
    base mod is installed (callers degrade to snap_armor_outside_body)."""
    ck = f"cbbe{weight}"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    env = os.environ.get(f"CBBE2UBE_CBBE_BODY{weight.upper()}")
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(env)
        return Path(env)
    cands: list[tuple[int, Path]] = []
    for mod, p in _iter_femalebody_nifs(weight):
        nm = mod.name.lower()
        if any(h in nm for h in _BODYSLIDE_OUT_HINTS):
            continue  # that's the morphed UBE output, not the CBBE template
        if any(h in nm for h in _UBE_BODY_NAME_HINTS):
            continue
        score = sum(1 for h in _CBBE_BODY_NAME_HINTS if h in nm)
        cands.append((score, p))
    # Highest name-hint score first; content-validate before accepting.
    for _, p in sorted(cands, key=lambda t: -t[0]):
        if _shape_has_3ba_topology(p):
            _BODY_DISCOVERY_CACHE[ck] = p
            return p
    _BODY_DISCOVERY_CACHE[ck] = None
    return None


def _find_ube_femalebody(weight: str = "_1") -> "Path | None":
    """Locate the UBE body that the CBBE -> UBE warp morphs armor TOWARD.

    Prefers `!UBE\\Body\\femalebody_tangent{w}.nif` (the user's BodySlide-built
    29,298-vert UBE BaseShape). The fallback 18,436-vert body at the standard
    character-assets path is a 3BA/CBBE body, not a UBE body; using it would
    produce a zero CBBE->UBE delta and leave armor CBBE-shaped.
    `_cached_cbbe_to_ube_delta` uses nearest-neighbor correspondence so topology
    mismatch between CBBE and UBE bodies is not a problem.
    Env override: CBBE2UBE_UBE_BODY_0 / _1 (weight-specific), or the single-path
    CBBE2UBE_UBE_BODY (the GUI picker) from which the weight sibling is derived.
    """
    ck = f"ube{weight}"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    env = os.environ.get(f"CBBE2UBE_UBE_BODY{weight.upper()}")
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(env)
        return Path(env)
    # Single-path GUI override: the picker sets one NIF, but BodySlide bodies
    # ship as a _0/_1 pair -- derive the weight-matching sibling from it (swap a
    # trailing _0/_1; use as-is if the name isn't weight-suffixed). The
    # weight-specific vars above still take priority. #ube-body-override
    bare = os.environ.get("CBBE2UBE_UBE_BODY")
    if bare:
        bp = Path(bare)
        cand = (bp.with_name(bp.stem[:-2] + weight + bp.suffix)
                if bp.stem.endswith(("_0", "_1")) else bp)
        if cand.is_file():
            _BODY_DISCOVERY_CACHE[ck] = cand
            return cand
    # Preferred: the genuine UBE-topology body output (`!UBE\Body` tangent).
    real = _find_user_preset_body(weight)
    if real is not None and Path(real).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(real)
        return Path(real)
    # Legacy fallback: a UBE-named / bodyslide-output 18,436-vert 3BA body.
    cands: list[tuple[int, Path]] = []
    for mod, p in _iter_femalebody_nifs(weight):
        nm = mod.name.lower()
        score = 0
        if any(h in nm for h in _BODYSLIDE_OUT_HINTS):
            score = 2
        elif any(h in nm for h in _UBE_BODY_NAME_HINTS):
            score = 1
        if score:
            cands.append((score, p))
    for _, p in sorted(cands, key=lambda t: -t[0]):
        if _shape_has_3ba_topology(p):
            _BODY_DISCOVERY_CACHE[ck] = p
            return p
    _BODY_DISCOVERY_CACHE[ck] = None
    return None

# FS-resolution memos (2026-08-18, profiled): one body-swap piece spent 3.0s of
# 19.4s re-walking the mods tree -- `sorted(mroot.iterdir())` alone was ~0.9s
# per call and the pair of functions below issued 104,650 nt.stat calls for two
# weights of ONE piece. The mod-DIRECTORY list is immutable during a run, and a
# POSITIVE resolution is validated with a single is_file() stat before reuse
# (vanished file -> recompute). Negative results are deliberately NOT cached:
# a miss re-scans, so a file appearing mid-run behaves exactly as before.
_MOD_DIR_LIST_CACHE: "dict[Path, list[Path]]" = {}

_GLOB_FIRST_MEMO: "dict[tuple, Path]" = {}

def _sorted_mod_dirs(root: Path) -> "list[Path]":
    got = _MOD_DIR_LIST_CACHE.get(root)
    if got is None:
        got = sorted(d for d in root.iterdir() if d.is_dir())
        _MOD_DIR_LIST_CACHE[root] = got
    return got

def _glob_first_in_mods(pattern: str,
                        name_substrs: "tuple[str, ...] | None" = None) -> "Path | None":
    """Return the first file matching `pattern` (a mod-relative glob) across
    all installed mods, optionally requiring ALL of `name_substrs`
    (lowercased) to appear in the filename. Portable replacement for
    hardcoded mod paths."""
    root = _paths.mods_root()
    if root is None or not root.is_dir():
        return None
    # Memo (positives only, re-validated with one stat): callers ask for the
    # same UBE body/ShapeData assets once per piece, and each miss-path walk
    # globs every installed mod (366ms measured). A vanished file falls through
    # to a fresh walk, so behaviour on change is identical to the uncached path.
    memo_key = (pattern, name_substrs, str(root))
    hit = _GLOB_FIRST_MEMO.get(memo_key)
    if hit is not None and hit.is_file():
        return hit
    try:
        mods = _sorted_mod_dirs(root)
    except OSError:
        return None
    for mod in mods:
        try:
            for hit in mod.glob(pattern):
                if not hit.is_file():
                    continue
                low = hit.name.lower()
                if name_substrs and not all(s in low for s in name_substrs):
                    continue
                _GLOB_FIRST_MEMO[memo_key] = hit
                return hit
        except OSError:
            continue
    return None

def _find_ube_shapedata(cache_key: str, env_var: str,
                        ext: str) -> Path | None:
    """A UBE body asset from any mod's BodySlide ShapeData, by name hint.

    Shared by the template NIF and the slider-delta OSD, which differed only in
    cache key, env override and file extension (0.92 similar). No fixed mod
    name is ever used — the scan is by content/name hint across all mods.

    The TWO-TIER hint is load-bearing, not belt-and-braces: prefer the canonical
    "...Release Body" over outfit-specific UBE body variants that also contain
    "ube"+"body". Falling straight through to the loose hint would pick whichever
    outfit's body happened to be found first.

    CACHED per process (the caller runs per-NIF and this scans every mod).
    """
    if cache_key in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[cache_key]
    env = os.environ.get(env_var)
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[cache_key] = Path(env)
        return _BODY_DISCOVERY_CACHE[cache_key]
    pat = f"CalienteTools/BodySlide/ShapeData/*/*.{ext}"
    res = (_glob_first_in_mods(pat, name_substrs=("ube", "release", "body"))
           or _glob_first_in_mods(pat, name_substrs=("ube", "body")))
    _BODY_DISCOVERY_CACHE[cache_key] = res
    return res

def _find_ube_template_body() -> Path | None:
    """The UBE Release Body template NIF (BodySlide ShapeData, slider-zero).
    Env override: CBBE2UBE_UBE_TEMPLATE."""
    return _find_ube_shapedata("ube_template", "CBBE2UBE_UBE_TEMPLATE", "nif")

def _find_ube_body_osd() -> Path | None:
    """The UBE body OSD (slider-deltas catalog) for M8 auto-TRI.
    Env override: CBBE2UBE_UBE_OSD."""
    return _find_ube_shapedata("ube_osd", "CBBE2UBE_UBE_OSD", "osd")

def _find_user_preset_body(weight_suffix: str = "_1") -> Path | None:
    """Find the user's BodySlide-built UBE body NIF (the `!UBE\\Body` tangent
    output) at the requested weight, scanned across mods. No fixed mod name.
    CACHED (per-NIF caller, scans all mods)."""
    ck = f"user_preset{weight_suffix}"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    res = _glob_first_in_mods(
        f"meshes/!UBE/Body/femalebody_tangent{weight_suffix}.nif")
    _BODY_DISCOVERY_CACHE[ck] = res
    return res
