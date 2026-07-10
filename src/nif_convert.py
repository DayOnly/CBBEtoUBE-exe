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

"""CBBE armor NIF -> UBE-targeted NIF.

Converts a CBBE / 3BA-authored armor mesh so it FITS and MORPHS on the UBE
body. `convert_nif()` picks one of two paths from the source shapes:

  * ARMOR-ONLY (no inline body shape): a body-aware REBUILD -- each shape is
    warped by the CBBE->UBE body deformation (snap-outside heuristic as a
    fallback), re-skinned to the injected UBE body's bone weights near the
    surface (M6 proximity blend), and pushed clear of the body (anti-poke).
    With no usable UBE body ref it degrades to a verbatim file copy.
  * INLINE BODY or EXPOSED BODY-SKIN slice: phase-2 BODY-SWAP
    (`convert_nif_phase2`) -- drop the source body/skin shapes, inject the
    full UBE BaseShape, and refit the armor shapes around it. `_0`/`_1`
    weight partners are reconciled so they take the same path (morph safety).

On top of the fit, per-shape passes handle: layered-cloth radial depth
ordering, rigid leg-plate bend / butt-jiggle conform, HDT-SMP soft-body &
collider skin PRESERVATION, cross-plate seam welding, adaptive + flex-zone
(rear-butt / calf) anti-poke clearance, and per-armor BODYTRI (.tri)
generation for RaceMenu body morphs. Every add_bone pass SAVES/RESTORES the
existing bones' skin-to-bone transforms (add_bone resets them -> collapse).

`warp_armor=` selects the warp-vs-snap fit heuristic on the armor-only path.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import nif_io, nif_patch
from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
from .correspondence import MeshIndex, compute_deformation


# ---------- module-level caches (one per process) -----------------------
# These avoid re-doing expensive work per-NIF during a batch convert.
# A typical armor mod has 18-24 NIFs; without caching we'd parse the
# 11.3 MB UBE body OSD 18-24 times, walk BodySlide-output dirs 18-24
# times, etc. Caching turns those into one-shots per process.

_OSD_CACHE: "dict[Path, object]" = {}  # path -> OsdFile
_UBE_BODY_REF_CACHE: "dict[Path, tuple[object, np.ndarray]]" = {}  # path -> (NifFile, BaseShape_verts)
_BODY_MORPH_AMP_CACHE: "dict[Path, np.ndarray]" = {}  # osd_path -> per-body-vert outward size-morph amplitude
# (id(body_shape), leg_region_only) -> (scale_bones, {bone: (verts, cKDTree, wts)}).
# The per-scale-bone KD-trees are BODY-derived (independent of the armour shape),
# so they're identical for every shape converted against the same body — building
# them once per body instead of once per shape removes the #1 per-NIF hotspot
# (add_scale_bone_weights was ~27% of warm convert time, almost all KD-tree builds).
_SCALE_BONE_DATA_CACHE: dict = {}
_HDT_DIR_SCAN_CACHE: "dict[Path, list]" = {}  # mod_root -> [(xml_path, rel_path)]
_CBBE_UBE_DELTA_CACHE: "dict[tuple[Path, Path], tuple[np.ndarray, np.ndarray]]" = {}
# Keyed by (cbbe_body_path, ube_body_path) -> (cbbe_verts, per_vert_delta).
# Used by warp_armor_by_body_delta to avoid re-parsing the two 18k-vert
# body NIFs and recomputing the delta for every armor NIF in a batch.


# ---- Single-place tuneable: armor-to-skin buffer ----------------------
# Minimum world-unit clearance between any armor vert and the UBE body
# after the body-delta warp. Baked into output NIFs at convert time.
# Raise toward 0.3 if body poke-through appears on large morphs;
# lower for a tighter flush fit (risk of z-fight shimmer under sliders).
ARMOR_TO_SKIN_BUFFER = 0.15


# ---- Inflation pass: BodySlide-style safety puff-out ------------------
# After the body-delta warp (which preserves the source CBBE drape exactly),
# this pass adds a uniform outward inflation with linear falloff so armor
# retains clearance when body morph sliders grow the mesh at runtime.
# 0 = disable. Reconvert any affected mod after changing.
ARMOR_INFLATION_MAGNITUDE = 0.7
ARMOR_INFLATION_FALLOFF_DISTANCE = 3.0

# Slot-49 (skirts, loincloths, hip cloth) sit closer to skin than torso
# armor and clip more under large morphs. Applied when biped_slots bit 19
# is set. Raise if loincloths/tassets clip on big presets.
ARMOR_INFLATION_MAGNITUDE_SLOT49 = 0.5
BIPED_SLOT49_BIT = 1 << 19  # 0x00080000

# Gauntlets and boots: limb shell (forearm/calf) gets extra standoff because
# body grows faster there under sliders than the rigid-bone-dominated shell.
# The extremity-fraction falloff protects finger/toe verts from this push.
# Raise if boots/gauntlets still clip; lower if they look puffy.
ARMOR_INFLATION_MAGNITUDE_HANDS_FEET = 0.6
BIPED_SLOT32_BIT = 1 << 2  # 0x00000004 — body (torso cuirass)
BIPED_SLOT33_BIT = 1 << 3  # 0x00000008 — hands
BIPED_SLOT37_BIT = 1 << 7  # 0x00000080 — feet

# Skirt shapes live inside slot-32 NIFs (Tasset, etc.) so they never
# trip the slot-49 path — detect by name/texture keyword instead.
# Raise if thigh/butt clipping appears on large presets.
ARMOR_INFLATION_MAGNITUDE_SKIRT = 0.7
SKIRT_INFLATION_KEYWORDS = (
    "skirt", "tasset", "loincloth", "apron", "kilt", "aketon",
)

# Belt/sash overlay shapes sit ON TOP of the waist garment and must
# stand off further than it or they Z-fight behind it and disappear.
# The falloff zeroes the push on draping tails already far from the body.
# Detected by name/diffuse keyword (these pieces live inside slot-32 NIFs).
ARMOR_INFLATION_MAGNITUDE_BELT = 1.5
BELT_OVERLAY_KEYWORDS = (
    "belt", "sash", "girdle", "buckle", "obi", "waistband", "waistcloth",
)

# ---- Nipple-aware bust clearance ----------------------------------------
# conform_to_source_standoff ramps chest clearance from BUST_FLAT_CLEARANCE up to
# bust_clearance by Breast03 nipple weight (checked over the worst nearby body vert,
# so a peeking tip is caught). Lower BUST_FLAT_CLEARANCE for a tighter chest; raise
# the gain if a nipple still pokes.  [DESIGN: Clearance & anti-poke]
BUST_FLAT_CLEARANCE = 0.3
BUST_NIPPLE_GAIN = 1.0
BUST_NEIGHBORHOOD_K = 6
BUST_NEIGHBORHOOD_RADIUS = 4.0
# Breast03 = nipple apex; Breast02 partial contributor. Matched without spaces.
NIPPLE_TIP_BONE_WEIGHTS = {"breast03": 1.0, "nipple": 1.0, "breast02": 0.4}

# ---- Final anti-poke pass -----------------------------------------------
# clear_armor_outside_body() runs last (after warp/inflate/conform) and pushes
# armor clear of the injected UBE body. Flat panels use FLAT_CLEAR; the breast
# front ramps up to BUST_CLEAR by nipple weight. Raise FLAT_CLEAR if the body
# still pokes on large presets, lower it for a tighter fit.
ANTIPOKE_FLAT_CLEAR = 0.8
ANTIPOKE_BUST_CLEAR = 1.0
ANTIPOKE_NIPPLE_GAIN = 1.5


def _is_belt_overlay(shape) -> bool:
    """True if the shape is a decorative waist belt/sash that rides ON TOP of
    the waist garment (by shape name OR diffuse texture keyword). Gets extra
    outward standoff so it clears the underlying corset/top instead of
    Z-fighting behind it. See ARMOR_INFLATION_MAGNITUDE_BELT."""
    try:
        nm = (getattr(shape, "name", "") or "").lower()
        if any(k in nm for k in BELT_OVERLAY_KEYWORDS):
            return True
        td = dict(getattr(shape, "textures", None) or {})
        d = (td.get("Diffuse") or "").lower()
        return any(k in d for k in BELT_OVERLAY_KEYWORDS)
    except Exception:
        return False


def _is_skirt_like(shape) -> bool:
    """True if the shape is skirt/hip-drape cloth (by shape name OR diffuse
    texture name). Used to give it extra outward standoff so the body
    doesn't clip through it under morph/animation."""
    try:
        nm = (getattr(shape, "name", "") or "").lower()
        if any(k in nm for k in SKIRT_INFLATION_KEYWORDS):
            return True
        td = dict(getattr(shape, "textures", None) or {})
        d = (td.get("Diffuse") or "").lower()
        return any(k in d for k in SKIRT_INFLATION_KEYWORDS)
    except Exception:
        return False

# Inflation close-threshold (units) for hand/foot shapes. Only verts
# within this distance from the body get an outward push. Tighter than
# the default ARMOR_INFLATION_FALLOFF_DISTANCE so fingertips don't drift
# outward. (Hand/foot shapes get the FULL body-delta warp — see the
# convert paths — so the whole piece conforms to the UBE limb; this
# threshold only governs the small extra inflation buffer.)
HAND_FOOT_INFLATION_FALLOFF = 4.0


def _slot_aware_inflation_magnitude(biped_slots: int, shape=None) -> float:
    """Pick the inflation magnitude for an armor shape.

    Priority:
      0. Belt/sash OVERLAY (by name, any slot) -> BELT magnitude. Must ride
         on top of the waist garment, so it gets the MOST clearance — checked
         first so a "belt" inside a slot-32/49 cuirass still wins.
      1. Skirt-like geometry (by name, any slot) -> SKIRT magnitude. Most
         clearance; skirt layers often hide inside a slot-32 cuirass NIF
         so a slot check alone misses them.
      2. Slot 49 (loincloth/hip cloth) -> SLOT49 magnitude.
      3. Slots 33/37 (gauntlets/boots) -> HANDS_FEET (now small, for a
         tight skin-hugging fit).
      4. Everything else (slot-32 body cloth) -> default.
    """
    if shape is not None and _is_belt_overlay(shape):
        return ARMOR_INFLATION_MAGNITUDE_BELT
    if shape is not None and _is_skirt_like(shape):
        return ARMOR_INFLATION_MAGNITUDE_SKIRT
    if biped_slots & BIPED_SLOT49_BIT:
        return ARMOR_INFLATION_MAGNITUDE_SLOT49
    if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
        return ARMOR_INFLATION_MAGNITUDE_HANDS_FEET
    return ARMOR_INFLATION_MAGNITUDE


def _slot_aware_scale_bone_reach(biped_slots: int) -> float:
    """Pick the scale-bone weight propagation radius for an armor shape
    based on its biped slot bitfield. Slot 49 cloth (skirts, loincloths,
    hanging tabards) gets the extended reach (`SCALE_BONE_REACH_SLOT49`,
    ~25u) so hem verts 15-25u from the body still pick up scale-bone
    weight and grow proportionally with body morphs at runtime.
    Other slots use the standard 12u reach. See `SCALE_BONE_REACH_SLOT49`
    docstring for the full rationale.
    """
    if biped_slots & BIPED_SLOT49_BIT:
        return SCALE_BONE_REACH_SLOT49
    return SCALE_BONE_REACH


def _cached_osd_load(path: Path):
    """Cache OsdFile.load to avoid re-parsing the 11 MB body OSD per NIF."""
    from .osd import OsdFile
    p = Path(path)
    cached = _OSD_CACHE.get(p)
    if cached is None:
        cached = OsdFile.load(p)
        _OSD_CACHE[p] = cached
    return cached


def _cached_ube_body_verts(path: Path):
    """Cache UBE body ref NIF + its BaseShape verts + per-vertex normals.

    Returns (nif, verts, normals). Normals are used by
    `snap_armor_outside_body` to determine the body's outward direction
    at each surface point — the safe way to push armor outward even
    when the armor vert sits inside the body. None if BaseShape has
    no normals (rare; pynifly populates them from the NIF).
    """
    p = Path(path)
    cached = _UBE_BODY_REF_CACHE.get(p)
    if cached is None:
        pyn = _pynifly()
        nif = pyn.NifFile(filepath=str(p))
        base = next((s for s in nif.shapes if s.name == "BaseShape"), None)
        verts = (np.asarray(base.verts, dtype=np.float64)
                 if base is not None else None)
        # Use stored normals if valid, else recompute from tris. BodySlide body
        # outputs frequently ship ZERO/absent vertex normals; without this the
        # standoff/conform passes that project along the body normal would
        # silently no-op (push along zero vectors). See _body_normals_or_compute.
        normals = _body_normals_or_compute(base) if base is not None else None
        cached = (nif, verts, normals)
        _UBE_BODY_REF_CACHE[p] = cached
    return cached


# --- Adaptive (morph-aware) armor clearance ----------------------------
# Scales clearance by how far each body region can grow outward at runtime,
# rather than applying a uniform standoff everywhere. Static regions (shoulders,
# back, arms) get tight ADAPTIVE_CLEARANCE_BASE; high-morph zones (breast/butt/
# belly) ramp up to ADAPTIVE_CLEARANCE_MORPH_MAX via per-vert outward amplitude.
# Always <= uniform inflation; cannot add poke-through.
ADAPTIVE_CLEARANCE_ENABLED = True
ADAPTIVE_CLEARANCE_BASE = 0.25       # minimum clearance in static zones
ADAPTIVE_CLEARANCE_MORPH_FACTOR = 0.20  # clearance added per unit of outward body morph
ADAPTIVE_CLEARANCE_MORPH_MAX = 0.8   # clearance cap for high-morph zones

# Extra anti-poke clearance scaled by local jiggle-bone weight, for SMP bounce that
# swings past the static envelope. Experimental, default off;
# CBBE2UBE_JIGGLE_CLEARANCE=1 on.  [DESIGN: Clearance & anti-poke]
JIGGLE_CLEARANCE_ENABLED = (
    os.environ.get("CBBE2UBE_JIGGLE_CLEARANCE", "").strip().lower()
    in ("1", "true", "yes", "on")
)
JIGGLE_CLEARANCE_GAIN = 0.5   # extra clearance (units) at full jiggle weight
JIGGLE_CLEARANCE_MAX = 0.5    # hard cap on the jiggle term

# Flat clearance floor on rear-facing verts at butt/upper-thigh height, so leg armor
# isn't punched through when the thigh swings back mid-stride. Raises below-floor
# verts only. Default on; CBBE2UBE_NO_REAR_STANDOFF=1 off.  [DESIGN: Flex-zone standoffs]
REAR_STANDOFF = float(os.environ.get("CBBE2UBE_REAR_BUTT_STANDOFF", "1.0"))
if os.environ.get("CBBE2UBE_NO_REAR_STANDOFF", "").strip().lower() in ("1", "true", "yes", "on"):
    REAR_STANDOFF = 0.0
REAR_STANDOFF_NY = -0.15      # nearest body normal.y below this = rear-facing
REAR_STANDOFF_Z_LO = 45.0     # butt + upper-thigh band (injected UBE body coords)
REAR_STANDOFF_Z_HI = float(os.environ.get("CBBE2UBE_REAR_STANDOFF_Z_HI", "80.0"))  # raise to reach a belt band above the butt

# Flat clearance floor over the lower-leg band (all-round), so calf/knee flex doesn't
# punch through leg armor. Raises below-floor verts only. Default on;
# CBBE2UBE_NO_CALF_STANDOFF=1 off.  [DESIGN: Flex-zone standoffs]
CALF_STANDOFF = float(os.environ.get("CBBE2UBE_CALF_STANDOFF", "0.6"))
if os.environ.get("CBBE2UBE_NO_CALF_STANDOFF", "").strip().lower() in ("1", "true", "yes", "on"):
    CALF_STANDOFF = 0.0
CALF_STANDOFF_Z_LO = 20.0     # lower-leg band (above the ankle/boot line)
CALF_STANDOFF_Z_HI = 46.0     # up to just below the knee

# ALL-ROUND thigh standoff (default off). A modest uniform floor over the thigh so tight
# leg armor sits just outside the body on EVERY side -- unlike the rear-only REAR_STANDOFF,
# which lifts only the back and (cranked high) shoves that side into an over-skirt while the
# front still shows skin. Keep it modest: enough to clear the body, low enough to stay under
# a hip skirt/tasset layer. CBBE2UBE_THIGH_STANDOFF=<u>.  [DESIGN: Flex-zone standoffs]
THIGH_STANDOFF = float(os.environ.get("CBBE2UBE_THIGH_STANDOFF", "0.0"))
THIGH_STANDOFF_Z_LO = float(os.environ.get("CBBE2UBE_THIGH_STANDOFF_Z_LO", "55.0"))  # lower to reach the mid/inner thigh
THIGH_STANDOFF_Z_HI = 78.0    # up to the hip (below the butt-crest)
# Restrict the thigh standoff to the INNER (medial) face only. The inner thigh is
# where a spread/bent pose punches the body through thin bind clearance; pushing
# the OUTER thigh too would shove it into a hip skirt. Medial = the nearest body
# normal points toward the centerline. CBBE2UBE_THIGH_STANDOFF_MEDIAL=1.
THIGH_STANDOFF_MEDIAL = (os.environ.get("CBBE2UBE_THIGH_STANDOFF_MEDIAL", "").strip().lower()
                         in ("1", "true", "yes", "on"))

# Inflate the CUIRASS/torso cloth shapes outward a hair (away from the body), while
# leaving LEG armor (greaves/leggings) untouched -- a targeted way to give the upper
# layers a little more room without disturbing the legs. A leg shape (name contains
# "greave" OR leg-bone-dominated) is skipped. Value in units. CBBE2UBE_CUIRASS_INFLATE.
CUIRASS_INFLATE = float(os.environ.get("CBBE2UBE_CUIRASS_INFLATE", "0.0"))

# Anti-poke push-field SMOOTHING (default ON): the final anti-poke pushes each
# vert independently along its nearest body normal, so adjacent verts get
# different magnitudes -> faceted/crinkled cloth exactly where clearance was
# applied. Feather the push scalar over the armor mesh adjacency instead. The
# smoothed field is FLOORED at the original per-vert requirement, so smoothing
# Feather the anti-poke push over the mesh so adjacent verts don't crinkle. Default
# off (it can collapse a multi-layer gap); CBBE2UBE_ANTIPOKE_SMOOTH=1 on.
# [DESIGN: Push-field smoothing]
ANTIPOKE_SMOOTH_ENABLED = (
    os.environ.get("CBBE2UBE_ANTIPOKE_SMOOTH", "").strip().lower()
    in ("1", "true", "yes", "on")
)
ANTIPOKE_SMOOTH_ITERS = 2

# Extra per-layer anti-poke floor so stacked garments don't converge to the same
# standoff and z-fight. Default off (same finding as smoothing);
# CBBE2UBE_LAYERED_ANTIPOKE=1 on.  [DESIGN: Layered anti-poke floors]
LAYERED_ANTIPOKE_ENABLED = (
    os.environ.get("CBBE2UBE_LAYERED_ANTIPOKE", "").strip().lower()
    in ("1", "true", "yes", "on")
)
LAYERED_ANTIPOKE_EPSILON = 0.15   # per-layer extra floor (units)
LAYERED_ANTIPOKE_MAX_EXTRA = 0.45  # cap (3+ layers share the top separation)
# OSD morph names matched by substring to build the per-vert outward-amplitude map.
_MORPH_SIZE_KEYWORDS = (
    "breast", "butt", "belly", "cleav", "nipple", "hip", "thigh", "waist",
    "big", "pregn", "chub", "wide", "tummy", "gut", "ass", "pelvis",
    # "glute" covers UBE/3BA GluteSize/Spread/Height/... sliders (missed by "butt").
    # "trochanter" = hip-bone slider. Both are genuine outward-volume zones.
    "glute", "trochanter",
)


def _cached_body_morph_amplitude(osd_path: Path,
                                 body_normals: "np.ndarray",
                                 n_verts: int) -> "np.ndarray | None":
    """Per-body-vert OUTWARD morph amplitude = max over the major size/shape
    sliders of `max(0, delta . outward_normal)`. This is "how far this body
    vertex can grow outward at runtime" — the clip-risk map that drives adaptive
    armor clearance. Cached per OSD path. Returns None if no OSD."""
    if osd_path is None or body_normals is None:
        return None
    p = Path(osd_path)
    cached = _BODY_MORPH_AMP_CACHE.get(p)
    if cached is not None:
        return cached
    try:
        osd = _cached_osd_load(p)
    except Exception:
        return None
    bn = np.asarray(body_normals, dtype=np.float64)
    amp = np.zeros(n_verts, dtype=np.float64)
    for m in osd.morphs:
        nm = m.name.lower()
        if not any(k in nm for k in _MORPH_SIZE_KEYWORDS):
            continue
        for idx, dx, dy, dz in m.offsets:
            if idx >= n_verts:
                continue
            outward = dx * bn[idx, 0] + dy * bn[idx, 1] + dz * bn[idx, 2]
            if outward > amp[idx]:
                amp[idx] = outward
    _BODY_MORPH_AMP_CACHE[p] = amp
    return amp


# --- Portable body discovery (no hardcoded modpack paths) -------------
# The mods root is auto-discovered at runtime (see src/paths.py) from the
# MO2 instance and propagated to worker processes via the CBBE2UBE_MODS_ROOT
# env var. The CBBE base body and UBE body are then found by SCANNING that
# root by content + name hint, never by a fixed mod name — so the tool works
# in any modpack. Env-var overrides (CBBE2UBE_CBBE_BODY / CBBE2UBE_UBE_BODY)
# are the escape hatch for unusual layouts.
from . import paths as _paths  # noqa: E402

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


# ----- Shape skin-frame reconciliation --------------------------------------
# Shapes store verts in SKIN space with a global_to_skin (world->skin) transform.
# Most armor has an identity g2s (skin == world), but shapes with a translation
# (e.g. Ebony cuirass -64.7u Z) store verts far from their body anatomy. The fit
# pipeline must compare armor verts against the body in WORLD space, or warps
# match the wrong region. Fix: lift verts to world before warp/inflate/conform,
# lower back to skin for output. Identity-g2s shapes are a no-op.

def _g2s_is_identity(g2s, eps=1e-4) -> bool:
    try:
        t = g2s.translation
        if abs(t[0]) > eps or abs(t[1]) > eps or abs(t[2]) > eps:
            return False
        if abs(float(g2s.scale) - 1.0) > eps:
            return False
        R = g2s.rotation
        for i in range(3):
            for j in range(3):
                if abs(R[i][j] - (1.0 if i == j else 0.0)) > eps:
                    return False
        return True
    except Exception:
        return True  # unknown -> treat as identity (keep current behavior)


def _shape_global_to_skin(shape):
    """Return the shape's global_to_skin TransformBuf, or None if unavailable."""
    try:
        return shape.global_to_skin
    except Exception:
        return None


def _verts_skin_to_world(verts_skin: np.ndarray, g2s) -> np.ndarray:
    """Inverse of global_to_skin: skin-space verts -> world. No-op if identity."""
    if g2s is None or _g2s_is_identity(g2s):
        return verts_skin
    R = np.asarray(g2s.rotation, np.float64)
    t = np.asarray(g2s.translation, np.float64)
    sc = float(g2s.scale) or 1.0
    return ((np.asarray(verts_skin, np.float64) - t) @ R) / sc


def _verts_world_to_skin(verts_world: np.ndarray, g2s) -> np.ndarray:
    """Apply global_to_skin: world verts -> skin space. No-op if identity."""
    if g2s is None or _g2s_is_identity(g2s):
        return verts_world
    R = np.asarray(g2s.rotation, np.float64)
    t = np.asarray(g2s.translation, np.float64)
    sc = float(g2s.scale) or 1.0
    return (np.asarray(verts_world, np.float64) @ R.T) * sc + t


# ----- Non-identity SHAPE-TRANSFORM bake -------------------------------------
# Skinned meshes must carry an identity NiAVObject (geometry) transform — the
# engine ignores it for skinned geometry and positions verts from bones only.
# Source meshes sometimes bake a non-identity SCALE or ROTATION into the geometry
# transform instead of the verts (e.g. a bespoke-armor-mod shape at scale 0.0729 ->
# armor flung off-screen; another at 6.86 -> collapsed). Fix: bake the
# transform into the verts/normals, adjust each skin-to-bone by its inverse to
# preserve the bind exactly, then emit an identity transform. No-op for the
# identity transforms that normal armor ships. #scalebake


def _shape_bake_matrix(shape):
    """Return the shape's full 4x4 NiAVObject transform (incl scale) as an
    np.ndarray IF it is non-identity in SCALE or ROTATION (the cases that break
    skinned rendering); else None. `to_matrix()` returns ROTATION+translation
    only (scalar scale stored separately), so fold the scale into the 3x3."""
    try:
        T = shape.transform
        s = float(T.scale)
        M = np.array(T.to_matrix()._array, dtype=np.float64)
    except Exception:
        return None
    R = M[:3, :3]
    scale_noniden = abs(s - 1.0) > 1e-3
    rot_noniden = not np.allclose(R, np.eye(3), atol=1e-3)
    if not (scale_noniden or rot_noniden):
        return None
    # Fold the scalar scale into the 3x3 ONLY if to_matrix gave a pure rotation
    # (|det| ~ 1). If a pynifly build already baked scale in (|det| ~ scale^3),
    # leave it. Robust to either convention.
    try:
        if abs(abs(float(np.linalg.det(R))) - 1.0) < 1e-2:
            M[:3, :3] = R * s
    except Exception:
        M[:3, :3] = R * s
    return M


def _shape_bake_translation(shape):
    """Return (tx,ty,tz) if the shape has a non-identity TRANSLATION with
    IDENTITY scale AND rotation; else None.

    A skinned shape's own NiAVObject transform is IGNORED by the engine at
    render (bones position the verts), so a leftover translation is dropped ->
    the mesh renders at its raw (un-lifted) vert positions = collapsed off the
    body. Measured on the ebony cuirass: shape translation z+64.68, mesh rendered
    ~65 units below the body (center_z 17 vs the UBE body's 75) -> "breasts
    collapse to the floor" at rest. The working ebony MAIL has an identity
    transform. Bake the translation into the verts WITHOUT a skin-to-bone adjust:
    the STB already targets the intended (body) position (it is byte-identical to
    the body's), so lifting the verts onto it makes the bind correct (verified:
    cuirass center_z 17 -> 82, matching the UBE body). This is bind-CHANGING --
    deliberately the OPPOSITE of _shape_bake_matrix, which is bind-PRESERVING for
    the scale/rotation case (the verts there are already body-correct and the
    transform is the artefact). Scale/rotation are handled by _shape_bake_matrix;
    this only covers the pure-translation case it returns None for."""
    try:
        T = shape.transform
        s = float(T.scale)
        M = np.array(T.to_matrix()._array, dtype=np.float64)
        tr = T.translation
    except Exception:
        return None
    if abs(s - 1.0) > 1e-3:
        return None
    if not np.allclose(M[:3, :3], np.eye(3), atol=1e-3):
        return None
    tx, ty, tz = float(tr[0]), float(tr[1]), float(tr[2])
    if max(abs(tx), abs(ty), abs(tz)) < 1e-3:
        return None
    return (tx, ty, tz)


def _adjust_skin_to_bone_baked(xf, bake_T):
    """Given a skin-to-bone TransformBuf `xf` and the geometry-transform matrix
    `bake_T` being baked into the verts, return the adjusted TransformBuf
    (full(xf) @ inv(bake_T)) so STB'@(bake_T@v) == STB@v (bind preserved)."""
    pyn = _pynifly()
    try:
        pm = xf.to_matrix()
        M = np.array(pm.to_matrix()._array if hasattr(pm, "to_matrix")
                     else pm._array, dtype=np.float64)
        s = float(xf.scale)
        if abs(abs(float(np.linalg.det(M[:3, :3]))) - 1.0) < 1e-2:
            M[:3, :3] = M[:3, :3] * s
        Mnew = M @ np.linalg.inv(bake_T)
        return pyn.TransformBuf.from_matrix(type(pm)(Mnew.tolist()))
    except Exception:
        return xf  # never break the copy on an edge case


def _align_scale_bone_stbs_to_verts(xforms_map, g2s_tb, verts, weights_map,
                                    min_off=30.0, min_gain=8.0):
    """Fix the M6 body-blend reskin's scale-bone coordinate-space mismatch.

    The M6 reskin grafts the UBE body's BODY-SPACE skin-to-bone transforms for
    3BA scale/morph bones onto armor whose verts are in a g2s-shifted shape
    space. The engine ignores the shape-level g2s for skinned UBE armor, so
    scale-bone-weighted regions sag/collapse. Primary bones keep source STBs
    and render fine; only the soft-body zones break.

    Per bone: if its weighted verts sit far from it (mean |STB @ vert| > min_off)
    AND baking g2s^-1 into that bone's STB reduces the distance by > min_gain,
    bake it. Self-verifying: only touches genuinely mismatched bones.
    Returns (new_xforms_map, baked_any). When baked_any, caller leaves
    global_to_skin at identity so the correction lives in the per-bone STBs.
    """
    pyn = _pynifly()

    def _mat4(tb):
        pm = tb.to_matrix()
        M = np.array(pm.to_matrix()._array if hasattr(pm, "to_matrix")
                     else pm._array, dtype=np.float64)
        s = float(tb.scale)
        if abs(abs(float(np.linalg.det(M[:3, :3]))) - 1.0) < 1e-2:
            M[:3, :3] = M[:3, :3] * s
        return M, pm

    try:
        G, _ = _mat4(g2s_tb)
        if np.allclose(G, np.eye(4), atol=1e-4):
            return xforms_map, False
        Ginv = np.linalg.inv(G)
    except Exception:
        return xforms_map, False

    V = np.asarray(verts, dtype=np.float64)
    if V.size == 0:
        return xforms_map, False
    new = dict(xforms_map)
    baked = False
    for bn, xf in xforms_map.items():
        if xf is None:
            continue
        pairs = (weights_map or {}).get(bn)
        if not pairs:
            continue
        idxs = [int(i) for i, _w in pairs if 0 <= int(i) < len(V)]
        if not idxs:
            continue
        try:
            S, pm = _mat4(xf)
        except Exception:
            continue
        vh = np.c_[V[idxs], np.ones(len(idxs))]
        off0 = float(np.linalg.norm((S @ vh.T).T[:, :3].mean(axis=0)))
        if off0 <= min_off:
            continue
        Snew = S @ Ginv
        off1 = float(np.linalg.norm((Snew @ vh.T).T[:, :3].mean(axis=0)))
        if off1 < off0 - min_gain:
            try:
                new[bn] = pyn.TransformBuf.from_matrix(type(pm)(Snew.tolist()))
                baked = True
            except Exception:
                pass
    return new, baked


def _fill_zero_weight_verts(weights_map, verts, eps=1e-4):
    """Verts with ~0 total bone weight skin to the ORIGIN -> a spike/streak to
    (0,0,0). Some source meshes ship verts the author never weighted -- esp.
    guard-armor reskins (some guard-armor trim shapes: 18-19% of a
    sub-shape) + decoration/1st-person shapes -- and the proximity reskin can
    miss verts far from the body. Give each zero-weight vert the bone weights of
    its NEAREST weighted vert so it rides along instead of spiking. No-op when
    every vert already carries weight (zero regression on clean meshes).
    #zeroweight"""
    import numpy as _np
    V = _np.asarray(verts, dtype=_np.float64)
    n = len(V)
    if n == 0:
        return weights_map
    wsum = _np.zeros(n)
    pervert = [[] for _ in range(n)]
    for bn, pairs in weights_map.items():
        for i, w in pairs:
            ii = int(i)
            if 0 <= ii < n:
                fw = float(w)
                wsum[ii] += fw
                pervert[ii].append((bn, fw))
    zero = _np.where(wsum < eps)[0]
    if zero.size == 0:
        return weights_map
    weighted = _np.where(wsum >= eps)[0]
    if weighted.size == 0:
        return weights_map  # nothing to borrow from -> leave as-is
    try:
        from scipy.spatial import cKDTree
        _, nn = cKDTree(V[weighted]).query(V[zero], k=1)
    except Exception:
        return weights_map
    new = {bn: list(pairs) for bn, pairs in weights_map.items()}
    for zi, wl in zip(zero.tolist(), _np.atleast_1d(nn).tolist()):
        for bn, w in pervert[int(weighted[int(wl)])]:
            new.setdefault(bn, []).append((int(zi), float(w)))
    return new


def _install_skin(new_shape, dst_nif, src_shape, bone_names, xforms_map,
                  weights_map, use_verts, bake_T, preserve_authored_skin=False):
    """Install the skin onto a freshly-created shape: bones, skin-to-bone xforms,
    global-to-skin, per-bone weights, and partitions. Shared by both _copy_shape
    skin paths (the M6 override-skin reskin and the verbatim source copy).

    `preserve_authored_skin` keeps the source weighting VERBATIM -- no genital or
    jiggle strip. Set for HDT-SMP per-triangle COLLIDERS / framework carriers
    re-imported by `_finalize_hdt_physics`: their authored skin is internally
    consistent (worked on the source body) and self-contained, so stripping
    bones from it desyncs the skin partition palette FSMP reads -> out-of-bounds
    read in Main::Update on equip (CTD) + the collider deforms wrong (invisible
    piece). #smp-collider-skin-preserve

    add_bone order matters (pynifly: add ALL bones first, THEN set transforms +
    weights, else they default to identity@origin -> spikes). Applies the
    #breast-stb g2s-align, the #wolf-greaves genital strip, and the #zeroweight
    fill. Caller builds/caps `bone_names`/`xforms_map`/`weights_map` first.
    """
    new_shape.skin()
    # Preserve physics-bone chains BEFORE add_bone (source transforms+parents).
    try:
        _precreate_custom_bone_chains(dst_nif, src_shape.file, bone_names)
    except Exception:
        pass
    # Fix scale-bone STB space mismatch: bake g2s^-1 into scale-bone STBs.
    # (Runs on the pre-strip weights_map, as before -- it only mutates xforms_map.)
    g2s_aligned = False
    if bake_T is None and src_shape.has_global_to_skin:
        xforms_map, g2s_aligned = _align_scale_bone_stbs_to_verts(
            xforms_map, src_shape.global_to_skin, use_verts, weights_map)
    # Strip genital + jiggle weights, then fill zero-weight verts, ALL before add_bone
    # so we only add bones that still carry weight -- a zero-weight add_bone'd bone
    # desyncs the partition palette (bone list > palette -> OOB read -> equip CTD).
    # Authored SMP skins are preserved verbatim (stripping them desyncs the palette).
    # [DESIGN: Zero-weight bones desync the partition palette]
    if not preserve_authored_skin:
        weights_map = _strip_genital_weights_map(weights_map)
        # Strip jiggle weights (breast/butt/belly) that destabilise physics
        # garments or collapse rigid leg plates on UBE actors. Full-garment strip
        # for chains; leg-plates only for plain armour.
        weights_map = _strip_jiggle_weights_map(
            weights_map,
            src_bones=set(src_shape.bone_names or []),
            force=_nif_has_garment_chain(src_shape.file))
    weights_map = _fill_zero_weight_verts(weights_map, use_verts)
    surviving = [bn for bn in bone_names
                 if weights_map.get(bn)
                 and any(w > 0.0 for _, w in weights_map[bn])]
    # Include any bone the strip/fill injected that was NOT in the caller's list
    # -- the genital/jiggle fallback "NPC Pelvis [Pelv]" assigned to verts left
    # genital/jiggle-only. Without this it would be dropped and those verts go
    # zero-weight -> spike. Preserve caller order; append the extras. #zeroweight-bone-desync
    _surv_seen = set(surviving)
    for _bn, _pairs in weights_map.items():
        if _bn not in _surv_seen and _pairs and any(w > 0.0 for _, w in _pairs):
            surviving.append(_bn)
            _surv_seen.add(_bn)
    for bn in surviving:
        new_shape.add_bone(bn)
    for bn in surviving:
        xf = xforms_map.get(bn)
        if xf is not None:
            if bake_T is not None:
                xf = _adjust_skin_to_bone_baked(xf, bake_T)
            new_shape.set_skin_to_bone_xform(bn, xf)
    if src_shape.has_global_to_skin:
        # Preserve source g2s even after STB-bake. The engine ignores g2s for
        # skinned render but uses it to place the bounding sphere. An identity g2s
        # on an offset-g2s shape puts the cull bound ~120u below the geometry ->
        # frustum-culled when the camera zooms in (invisible torso/body up close).
        new_shape.set_global_to_skin(src_shape.global_to_skin)
    for bn in surviving:
        pairs = weights_map.get(bn)
        if pairs:
            new_shape.setShapeWeights(bn, [(int(i), float(w)) for i, w in pairs])
    if src_shape.partitions and src_shape.partition_tris is not None:
        new_shape.set_partitions(src_shape.partitions, src_shape.partition_tris)


def _cached_cbbe_to_ube_delta(
        cbbe_path: Path, ube_path: Path,
) -> "tuple[np.ndarray, np.ndarray] | tuple[None, None]":
    """Return (cbbe_body_verts, per_vert_delta).

      cbbe_body_verts: (M, 3) float64, M = 18436 for stock 3BA topology
      per_vert_delta:  (M, 3) float64, = ube_verts - cbbe_verts

    The first call parses both NIFs and computes the delta; subsequent
    calls with the same path pair return the cached result instantly.
    Returns (None, None) only if either NIF fails to load / has no shapes.

    Topologies need NOT match. When the CBBE base (18,436-vert 3BA) and the
    UBE body (29,298-vert UBE BaseShape) have different vert counts, the
    delta is built by NEAREST-NEIGHBOR correspondence: for each CBBE vert,
    the delta is the vector to its nearest UBE body vert. (Equal counts use
    a direct index-wise subtraction.) Either way the result is indexed by
    CBBE vert, which is what `warp_armor_by_body_delta` consumes.
    """
    key = (Path(cbbe_path).resolve(), Path(ube_path).resolve())
    cached = _CBBE_UBE_DELTA_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        pyn = _pynifly()
        cbbe_nif = nif_io.open_nif_retry(str(cbbe_path))  # transient-IO resilient
        ube_nif = nif_io.open_nif_retry(str(ube_path))
        # Pick the 3BA shape — that's the standard 18k topology we want.
        # Fall back to the largest shape if the name doesn't match (some
        # mod variants use different shape names).
        def _pick_main_body(nf):
            named = next((s for s in nf.shapes if s.name == "3BA"), None)
            if named is not None:
                return named
            return max(nf.shapes, key=lambda s: len(s.verts), default=None)
        cbbe_shape = _pick_main_body(cbbe_nif)
        ube_shape = _pick_main_body(ube_nif)
        if cbbe_shape is None or ube_shape is None:
            _CBBE_UBE_DELTA_CACHE[key] = (None, None)
            return None, None
        cbbe_v = np.asarray(cbbe_shape.verts, dtype=np.float64)
        ube_v = np.asarray(ube_shape.verts, dtype=np.float64)
        if cbbe_v.shape == ube_v.shape:
            # Same topology (e.g. legacy 18,436-vert UBE ref) -> exact
            # per-vert displacement.
            delta = ube_v - cbbe_v
        else:
            # Different topology (CBBE 3BA 18,436 verts vs the genuine UBE
            # body 29,298 verts). Build the CBBE->UBE deformation field by
            # nearest-neighbor: each CBBE vert's delta is the vector to its
            # nearest UBE body vert. Indexed by CBBE vert so the warp
            # (which finds nearest CBBE verts per armor vert) consumes it
            # unchanged. This is what lets us use the REAL UBE body as the
            # target instead of a same-topology 3BA body whose delta is ~0.
            from scipy.spatial import cKDTree
            _, nn = cKDTree(ube_v).query(cbbe_v, k=1)
            delta = ube_v[nn] - cbbe_v
        _CBBE_UBE_DELTA_CACHE[key] = (cbbe_v, delta)
        return cbbe_v, delta
    except Exception:
        _CBBE_UBE_DELTA_CACHE[key] = (None, None)
        return None, None


GROOVE_SMOOTH_CLOSE = 6.0   # only smooth verts within this of the UBE body (tight armor)
GROOVE_SMOOTH_ITERS = 8
GROOVE_SMOOTH_ROUGH = 0.25  # displacement-deviation (u) above which a vert is "grooved"


def _smooth_warp_grooves(src_world, warped, ube_body_verts):
    """Flatten warp-induced displacement grooves on body-conforming armor.

    The per-vert body-delta warp can introduce localized roughness in the
    CBBE->UBE displacement field where tight armor stretches over the larger
    UBE bust — visible as 'indent lines' on the breast/chest. This does a
    roughness-weighted Laplacian smooth of the DISPLACEMENT (warped - source),
    gated to verts close to the body, so genuine drape on loose/decorative
    geometry (far from the body) and already-smooth regions are left alone.
    Returns the (possibly) smoothed warped verts."""
    try:
        from scipy.spatial import cKDTree
        src = np.asarray(src_world, dtype=np.float64)
        w = np.asarray(warped, dtype=np.float64)
        if len(src) != len(w) or len(src) < 12:
            return warped
        disp = w - src
        if ube_body_verts is not None and len(ube_body_verts):
            d2b, _ = cKDTree(
                np.asarray(ube_body_verts, dtype=np.float64)).query(w, k=1)
            active = (d2b < GROOVE_SMOOTH_CLOSE).astype(np.float64)[:, None]
        else:
            active = np.ones((len(src), 1), dtype=np.float64)
        if not active.any():
            return warped
        _, idx = cKDTree(src).query(src, k=9)
        nbr = idx[:, 1:]
        for _ in range(GROOVE_SMOOTH_ITERS):
            nm = disp[nbr].mean(axis=1)
            rough = np.linalg.norm(disp - nm, axis=1)
            wt = np.clip(rough / GROOVE_SMOOTH_ROUGH, 0.15, 1.0)[:, None]
            disp = disp + active * (0.6 * wt) * (nm - disp)
        return src + disp
    except Exception:
        return warped


def warp_armor_by_body_delta(
    armor_verts: np.ndarray,
    cbbe_body_verts: np.ndarray,
    body_delta_per_vert: np.ndarray,
    *,
    k: int = 4,
    min_standoff: float = 0.3,
    ube_body_verts: "np.ndarray | None" = None,
    ube_body_normals: "np.ndarray | None" = None,
    max_distance: "float | None" = None,
    upper_damp_z: "tuple[float, float]" = (95.0, 105.0),
    upper_damp_standoff: "tuple[float, float]" = (2.0, 5.0),
    upper_damp_max: float = 0.6,
) -> np.ndarray:
    """Warp armor verts to follow the body's CBBE -> UBE deformation,
    then enforce a minimum standoff above the UBE body surface.

    Algorithm:

      Pass 1 — body-delta warp (no parameter tuning, no inside/outside
      decision):

        For each armor vert `a` (in CBBE space):
          1. Find K nearest CBBE body verts to `a`.
          2. IDW-blend the body's per-vert delta at those K neighbors.
          3. Move armor vert by the blended delta.

      Mathematically armor verts inherit the same local deformation
      field that morphs CBBE body into UBE body. The artist's intended
      drape (relative offset of armor from body surface) is preserved
      because we apply the SAME displacement to both.

      Pass 2 — minimum standoff buffer (skip if UBE body / normals not
      provided):

        For each warped armor vert:
          1. Find the nearest UBE body vert.
          2. Compute signed distance along the UBE outward normal.
          3. If signed < `min_standoff`, push armor outward along that
             normal to sit exactly at `min_standoff`.

      Why we need this on top of pass 1: revealing armor (a hand-authored UBE armor's
      breast strap, lingerie, thin cloth) has near-zero source drape
      — the cloth is glued to the CBBE surface. Pass 1 preserves
      drape exactly, including "almost-zero" — so any small numerical
      noise in the deformation field puts cloth INSIDE the UBE body
      and skin pokes through. A small fixed standoff (0.3u default)
      makes sure no armor surface lives below the body surface, so
      Skyrim's z-buffer always renders cloth on top of skin. Tune
      higher for puffier garments, lower for skin-tight ones — but
      0.3u is small enough not to visibly bulge tight armor.

    Args:
      armor_verts:         (N, 3) source CBBE-space armor verts
      cbbe_body_verts:     (M, 3) CBBE body reference verts
      body_delta_per_vert: (M, 3) per-vert ube_pos - cbbe_pos
      k: number of CBBE neighbors to blend (4 gives smooth coverage)
      min_standoff: enforced clearance between warped armor and UBE
        surface; 0 disables the buffer pass
      ube_body_verts: (U, 3) UBE body verts for the standoff pass.
        Optional — pass None to disable the buffer pass entirely.
      ube_body_normals: (U, 3) outward unit normals matched 1:1 with
        `ube_body_verts`. Required when `ube_body_verts` is given.
      max_distance: if set, linearly falls off the body-delta warp and
        the standoff push to zero at this nearest-body distance.
        Verts <= 0u from body get full delta; verts at max_distance
        get none; in between interpolates linearly. Used for gauntlet
        / boot shapes where body-adjacent verts (wrist, ankle) need
        the warp but extremity verts (fingertips, toes) must stay
        put or finger geometry breaks. Pass None to disable the
        falloff (default: full warp at all distances).

    Returns:
      (N, 3) float32 — armor verts conformed to UBE body shape.
    """
    from scipy.spatial import cKDTree
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    cbbe_body_verts = np.asarray(cbbe_body_verts, dtype=np.float64)
    body_delta_per_vert = np.asarray(body_delta_per_vert, dtype=np.float64)

    # ----- Pass 1: body-delta warp -----
    tree = cKDTree(cbbe_body_verts)
    dists, idx = tree.query(armor_verts, k=max(1, k))
    if k == 1:
        dists = dists[:, None]; idx = idx[:, None]

    # IDW (1/d^2, K-nearest) interpolation of the body delta -- the nearest body
    # vert dominates, so surface-hugging cloth keeps its full local delta.
    # [DESIGN: Fitting]
    w = 1.0 / (dists * dists + 1e-9)
    w /= w.sum(axis=1, keepdims=True)
    interp_delta = (body_delta_per_vert[idx] * w[..., None]).sum(axis=1)

    # Distance falloff: don't warp verts far from the body (else a gauntlet's
    # fingertips get dragged by the wrist delta and lose pose). Linear 1->0 over
    # max_distance.  [DESIGN: Fitting]
    if max_distance is not None and max_distance > 0:
        nearest_d = dists[:, 0] if dists.ndim == 2 else dists
        falloff = np.clip(1.0 - nearest_d / max_distance, 0.0, 1.0)
        interp_delta = interp_delta * falloff[:, None]

    # ----- Upper-body standoff damp -----
    # Fade the warp for rigid stand-off geometry (stiff collars, high pauldrons) in
    # the upper body: the chest/shoulders broaden CBBE->UBE and the warp would shear
    # those pieces out+back. Gated on BOTH upper-body Z and high source standoff
    # (so lower drape and body-fitted chest cloth are untouched), smoothstep ramp.
    # [DESIGN: Fitting]
    if upper_damp_max > 0:
        az = armor_verts[:, 2]
        sd0 = dists[:, 0] if dists.ndim == 2 else dists

        def _ss(x, lo, hi):
            t = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
            return t * t * (3.0 - 2.0 * t)
        gate = (_ss(az, upper_damp_z[0], upper_damp_z[1])
                * _ss(sd0, upper_damp_standoff[0], upper_damp_standoff[1]))
        interp_delta = interp_delta * (1.0 - gate * upper_damp_max)[:, None]

    warped = armor_verts + interp_delta

    # ----- Pass 2: minimum standoff buffer -----
    if (min_standoff > 0
            and ube_body_verts is not None
            and ube_body_normals is not None):
        ube_v = np.asarray(ube_body_verts, dtype=np.float64)
        ube_n = np.asarray(ube_body_normals, dtype=np.float64)
        ube_tree = cKDTree(ube_v)
        # k=1: each armor vert pushes against its single closest UBE
        # surface point. K-NN smoothing here would blend opposing normals
        # in concave regions (between legs, under arms) and produce a
        # zero direction, defeating the push entirely.
        ube_dists, ube_idx = ube_tree.query(warped, k=1)
        near_v = ube_v[ube_idx]
        near_n = ube_n[ube_idx]
        # Signed distance: positive = outside body, negative = inside.
        to_armor = warped - near_v
        signed = (to_armor * near_n).sum(axis=1)
        # Verts with standoff below the floor get pushed along the normal.
        need_push = signed < min_standoff
        # Apply the same far-distance falloff to the standoff push so
        # fingertip / toe verts don't get yanked toward the body
        # surface from many units away (would pin extremities to the
        # nearest wrist or ankle body vert).
        if max_distance is not None and max_distance > 0:
            push_falloff = np.clip(
                1.0 - ube_dists / max_distance, 0.0, 1.0,
            )
            need_push = need_push & (push_falloff > 0)
        if need_push.any():
            deficit = (min_standoff - signed[need_push])[:, None]
            push_vecs = near_n[need_push] * deficit
            if max_distance is not None and max_distance > 0:
                push_vecs = push_vecs * push_falloff[need_push][:, None]
            warped[need_push] += push_vecs

    return warped.astype(np.float32)


def _vertex_normals_from_tris(verts, tris) -> np.ndarray:
    """Area-weighted per-vertex normals from triangle geometry. Used when a body
    NIF ships no (or zeroed) vertex normals -- common for BodySlide outputs --
    so the standoff/conform passes have a valid outward direction to push along
    instead of silently no-op'ing on zero vectors."""
    v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    vn = np.zeros_like(v)
    if t.size:
        # un-normalized cross product == area-weighted face normal
        fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        for c in range(3):
            np.add.at(vn, t[:, c], fn)
    lens = np.linalg.norm(vn, axis=1, keepdims=True)
    lens[lens < 1e-9] = 1.0
    return vn / lens


def _body_normals_or_compute(shape) -> "np.ndarray | None":
    """Valid per-vertex outward normals for a body shape: use the NIF's stored
    normals if present AND non-degenerate, else recompute them from the triangle
    mesh. Returns None only if neither is available (no verts/tris)."""
    try:
        v = np.asarray(shape.verts, dtype=np.float64)
    except Exception:
        return None
    nm = getattr(shape, "normals", None)
    if nm is not None:
        nm = np.asarray(nm, dtype=np.float64)
        # accept only if shaped right AND actually populated (mean |n| ~ 1, not 0)
        if nm.shape == v.shape and np.linalg.norm(nm, axis=1).mean() > 0.5:
            lens = np.linalg.norm(nm, axis=1, keepdims=True)
            lens[lens < 1e-9] = 1.0
            return nm / lens
    try:
        return _vertex_normals_from_tris(v, shape.tris)
    except Exception:
        return None


def _body_nipple_weight(shape) -> "np.ndarray | None":
    """Per-vertex 'nipple weight' from the body's breast-TIP bone weights
    (Breast03 = the apex/tip bone, peaks right at the nipple; Breast02 partial).
    Cleanly localizes the breast front / nipple -- where converted armor needs
    real clearance -- while the sternum, sides and upper chest read ~0 (close
    fit). Returns a (V,) array in ~[0,1], or None if the body carries no breast
    bones (then the bust pass falls back to the flat clearance)."""
    try:
        n = len(shape.verts)
    except Exception:
        return None
    bw = getattr(shape, "bone_weights", None) or {}
    out = np.zeros(n, dtype=np.float64)
    found = False
    for bn, pairs in bw.items():
        b = bn.lower().replace(" ", "")
        mul = next((m for kw, m in NIPPLE_TIP_BONE_WEIGHTS.items() if kw in b), 0.0)
        if mul <= 0 or pairs is None:
            continue
        found = True
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for i, w in pl:
            if 0 <= i < n:
                out[i] = max(out[i], float(w) * mul)
    return out if found else None


def _body_jiggle_weight(shape) -> "np.ndarray | None":
    """Per-vertex jiggle weight from the body's softbody bones (breast/butt/
    belly, PHYSICS_JIGGLE_SCALE_KEYWORDS): max weight over those bones, ~[0,1].
    This is the map of where SMP jiggle actually moves the body — the dynamic-
    overshoot clip-risk map for JIGGLE_CLEARANCE. Returns None if the body has
    no jiggle bones (static body: nothing to overshoot, the pass no-ops)."""
    try:
        n = len(shape.verts)
    except Exception:
        return None
    bw = getattr(shape, "bone_weights", None) or {}
    out = np.zeros(n, dtype=np.float64)
    found = False
    for bn, pairs in bw.items():
        if not _is_physics_jiggle_scale_bone(bn) or pairs is None:
            continue
        found = True
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for i, w in pl:
            if 0 <= i < n:
                out[i] = max(out[i], float(w))
    return np.clip(out, 0.0, 1.0) if found else None


def conform_to_source_standoff(
    src_cloth: np.ndarray,
    src_body_verts: np.ndarray,
    src_body_normals: np.ndarray,
    cur_cloth: np.ndarray,
    ube_body_verts: np.ndarray,
    ube_body_normals: np.ndarray,
    *,
    min_clearance: float = 0.25,
    blend: "float | None" = None,
    blend_tight: float = 0.3,
    tight_standoff: float = 1.0,
    loose_standoff: float = 4.0,
    max_pull: float = 4.0,
    max_body_dist: float = 12.0,
    bust_clearance: float = 0.9,
    bust_z: "tuple[float, float]" = (84.0, 100.0),
    max_push_out: float = 2.5,
    ube_body_nipple: "np.ndarray | None" = None,
) -> np.ndarray:
    """Restore each cloth vert's ORIGINAL clearance from the body after the
    CBBE->UBE warp+inflate, so a piece that HUGGED the source body still hugs the
    UBE body instead of standing off it.

    Why: the body-delta warp over-projects fitted layers onto the larger UBE
    breast (measured: a corset that sat 0.58u off the 3BA body warped to 1.8u off
    the UBE body -> "chest too far out"). Inflation isn't the cause (the standoff
    persists at inflation 0), so the tuning knob doesn't fix it.

    `blend` = how far to reel an over-projected vert toward its source clearance
    (1.0 = all the way to the source fit; 0.0 = no conform). Default None =
    ADAPTIVE per-vert: the fraction ramps from `blend_tight` (for skin-hugging
    verts whose source clearance <= `tight_standoff`) up to 1.0 (for loose/draping
    verts whose source clearance >= `loose_standoff`). Rationale: the source fit
    was authored for the SMALLER 3BA body, so tight pieces need EXTRA room on the
    bigger UBE body (low blend, no clip = a tight-fitted corset), while loose pieces just
    need their original drape restored (high blend, no float = the forsworn fur).
    Pass an explicit float to force a uniform blend (tests / overrides).

    SAFE BY CONSTRUCTION outside the bust band (cannot create new clipping or
    loosen anything):
      tight  = clamp( min(source_standoff, current_standoff), >= min_clearance )
      target = current + (tight - current) * blend     # partway IN, never OUT
      move   = min(target - current, 0)                # PULL IN ONLY
    The ONE exception is the bust Z-band (`bust_z`), where over-tight cloth is
    pushed OUT to >= `bust_clearance` so the body's nipple can't poke through it
    (`bust_clearance` defaults above the measured UBE nipple protrusion). Cloth
    already at/above bust_clearance there is untouched.
    => Loose-draping cloth (source standoff already large, e.g. a skirt/tabard) and
       cloth already tighter than its source: tight == current -> NO-OP.
       Only an over-projected fitted layer (current > source) gets reeled back (by
       `blend` of the way), never closer than min_clearance. Far-from-body verts
       (either reference) and pulls beyond max_pull are skipped (bad correspondence
       guard). No-op + returns input unchanged if vert counts differ (merged shape).
    """
    from scipy.spatial import cKDTree
    src_cloth = np.asarray(src_cloth, dtype=np.float64)
    cur_cloth = np.asarray(cur_cloth, dtype=np.float64)
    if len(src_cloth) != len(cur_cloth) or len(cur_cloth) == 0:
        return cur_cloth.astype(np.float32)
    src_body_verts = np.asarray(src_body_verts, dtype=np.float64)
    src_body_normals = np.asarray(src_body_normals, dtype=np.float64)
    ube_body_verts = np.asarray(ube_body_verts, dtype=np.float64)
    ube_body_normals = np.asarray(ube_body_normals, dtype=np.float64)
    if (len(src_body_verts) == 0 or len(ube_body_verts) == 0
            or src_body_normals.shape != src_body_verts.shape
            or ube_body_normals.shape != ube_body_verts.shape):
        return cur_cloth.astype(np.float32)
    # signed standoff (along the nearest body vert's outward normal) in each space
    sd, si = cKDTree(src_body_verts).query(src_cloth, k=1)
    s_src = ((src_cloth - src_body_verts[si]) * src_body_normals[si]).sum(1)
    ube_tree = cKDTree(ube_body_verts)
    ud, ui = ube_tree.query(cur_cloth, k=1)
    s_cur = ((cur_cloth - ube_body_verts[ui]) * ube_body_normals[ui]).sum(1)
    tight = np.clip(np.minimum(s_src, s_cur), min_clearance, None)
    if blend is None:
        # ADAPTIVE per-vert blend keyed on the source clearance:
        #  - tight verts (skin-hugging, small s_src) keep ROOM (low blend) so they
        #    don't clip on the bigger/morphed UBE body (tight-corset case);
        #  - loose/draping verts (large s_src, e.g. forsworn fur/feathers) are
        #    reeled most of the way back to their source drape (-> 1.0) so they
        #    don't FLOAT off the body (the forsworn gap). One knob, both symptoms.
        _b = np.clip((s_src - tight_standoff)
                     / max(loose_standoff - tight_standoff, 1e-6), 0.0, 1.0)
        blend_v = blend_tight + _b * (1.0 - blend_tight)
    else:
        blend_v = float(blend)
    target = s_cur + (tight - s_cur) * blend_v
    move = np.minimum(target - s_cur, 0.0)            # pull IN only (default)
    # BUST CLEARANCE (anti nipple poke-through): required clearance is keyed on the
    # body's Breast03 nipple weight -- flat chest fabric keeps BUST_FLAT_CLEARANCE,
    # the nipple ramps to bust_clearance -- enforced over the WORST body vert in a
    # local neighbourhood so a poking tip is caught even when a flatter vert is
    # nearer. Push-out only where the body would poke.  [DESIGN: Clearance & anti-poke]
    body_z = ube_body_verts[ui][:, 2]
    in_bust = (body_z >= bust_z[0]) & (body_z <= bust_z[1])
    if np.any(in_bust):
        kk = min(BUST_NEIGHBORHOOD_K, len(ube_body_verts))
        dd, jj = ube_tree.query(cur_cloth, k=kk)
        if kk == 1:
            dd = dd[:, None]; jj = jj[:, None]
        nrm0 = ube_body_normals[ui]                   # nearest-vert outward normal
        diff = cur_cloth[:, None, :] - ube_body_verts[jj]          # (n, kk, 3)
        s_k = (diff * nrm0[:, None, :]).sum(axis=2)                # clearance over each neighbour along nrm0
        s_k = np.where(dd <= BUST_NEIGHBORHOOD_RADIUS, s_k, np.inf)
        worst = np.min(s_k, axis=1)                                # closest nearby body point
        worst = np.where(np.isfinite(worst), worst, s_cur)         # fallback: no neighbour in radius
        # required clearance: small everywhere, ramping up only at the nipple
        if (ube_body_nipple is not None
                and len(ube_body_nipple) == len(ube_body_verts)):
            nipw = np.asarray(ube_body_nipple, dtype=np.float64)[ui]
        else:
            nipw = np.zeros(len(ui))
        req = np.clip(BUST_FLAT_CLEARANCE + nipw * BUST_NIPPLE_GAIN,
                      BUST_FLAT_CLEARANCE, bust_clearance)
        # pull IN only as far as the general conform wanted AND no closer than
        # `req` over the worst neighbour; push OUT if the nipple would poke.
        move = np.where(in_bust, np.maximum(move, req - worst), move)
    near = (sd < max_body_dist) & (ud < max_body_dist)
    move = np.where(near, np.clip(move, -max_pull, max_push_out), 0.0)
    return (cur_cloth + ube_body_normals[ui] * move[:, None]).astype(np.float32)


def _rank_body_layers(shapes, body_verts, *, body_names, reskin_skip,
                      softbody_names, collider_names,
                      ube_bones: "set[str]",
                      epsilon: float = LAYERED_ANTIPOKE_EPSILON,
                      max_extra: float = LAYERED_ANTIPOKE_MAX_EXTRA,
                      ) -> "dict[str, float]":
    """LAYERED_ANTIPOKE ranking: {shape name -> extra anti-poke floor}. Ranks a
    NIF's eligible body-layer shapes innermost-first by median distance to the
    body, layer i getting min(i*epsilon, max_extra). Eligibility mirrors the
    anti-poke's own gates (skip body/reskin-skip/softbody/collider/SMP-rigged),
    plus: <8 verts (decorative) and median>10u (far drape) never rank. Verts are
    measured in the SAME space as the main loop (skin->world + AVObject offset).
    Medians are QUANTIZED (0.5u) with name tie-break so the _0/_1 weight
    partners of one outfit rank identically (a swap would self-inflict weight-
    slider divergence). <2 eligible shapes -> {} (single layer = unchanged)."""
    from scipy.spatial import cKDTree
    tree = cKDTree(np.asarray(body_verts, dtype=np.float64))
    elig: "list[tuple[float, str]]" = []
    for ls in shapes:
        if (ls.name in body_names or ls.name in reskin_skip
                or ls.name in softbody_names or ls.name in collider_names
                or _shape_has_hdt_smp_rigging(ls, ube_bones)):
            continue
        lv = np.asarray(ls.verts, dtype=np.float64)
        if len(lv) < 8:
            continue                      # micro-shapes don't define a layer
        lv = _verts_skin_to_world(lv, _shape_global_to_skin(ls))
        lv = lv + shape_body_offset(ls)
        d, _ = tree.query(lv, k=1)
        med = float(np.median(d))
        if med > 10.0:
            continue                      # far drape: not a body layer
        elig.append((round(med * 2.0) / 2.0, ls.name))
    if len(elig) < 2:
        return {}
    elig.sort()
    return {nm: min(rk * epsilon, max_extra)
            for rk, (_m, nm) in enumerate(elig)}


def _smooth_push_field(push: np.ndarray, needed: np.ndarray, tris,
                       iters: int = ANTIPOKE_SMOOTH_ITERS,
                       blend: float = 0.5) -> np.ndarray:
    """Feather an anti-poke push scalar over the armor mesh adjacency (see
    ANTIPOKE_SMOOTH_ENABLED). Each iteration blends toward the neighbor average
    then re-floors at `needed` (the original per-vert requirement), so a poke can
    never reopen; verts with no push near no pushed verts stay exactly 0.
    Returns `push` unchanged on any failure (never worse than no smoothing)."""
    try:
        t = np.asarray(tris, dtype=np.int64)
        n = len(push)
        if t.size == 0 or n < 3 or not np.any(push > 0):
            return push
        from scipy import sparse
        e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e = np.concatenate([e, e[:, ::-1]])
        e = e[(e[:, 0] < n) & (e[:, 1] < n) & (e[:, 0] >= 0) & (e[:, 1] >= 0)]
        if len(e) == 0:
            return push
        A = sparse.coo_matrix(
            (np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)).tocsr()
        deg = np.asarray(A.sum(axis=1)).ravel()
        deg[deg == 0] = 1.0
        p = np.asarray(push, dtype=np.float64).copy()
        req = np.asarray(needed, dtype=np.float64)
        for _ in range(max(1, int(iters))):
            avg = np.asarray(A @ p).ravel() / deg
            p = (1.0 - blend) * p + blend * avg
            p = np.maximum(p, req)        # never reopen a poke
        return p
    except Exception:
        return push


def clear_armor_outside_body(
    verts: np.ndarray,
    body_verts: np.ndarray,
    body_normals: np.ndarray,
    body_nipple: "np.ndarray | None" = None,
    *,
    flat_clear: float = ANTIPOKE_FLAT_CLEAR,
    bust_clear: float = ANTIPOKE_BUST_CLEAR,
    nipple_gain: float = ANTIPOKE_NIPPLE_GAIN,
    bust_z: "tuple[float, float]" = (84.0, 100.0),
    k: int = 6,
    radius: float = 4.0,
    max_push: float = 3.0,
    max_body_dist: float = 10.0,
    morph_amplitude: "np.ndarray | None" = None,
    adaptive_base: float = ADAPTIVE_CLEARANCE_BASE,
    adaptive_factor: float = ADAPTIVE_CLEARANCE_MORPH_FACTOR,
    adaptive_cap: float = ADAPTIVE_CLEARANCE_MORPH_MAX,
    jiggle_amplitude: "np.ndarray | None" = None,
    jiggle_gain: float = JIGGLE_CLEARANCE_GAIN,
    jiggle_cap: float = JIGGLE_CLEARANCE_MAX,
    rear_standoff: float = REAR_STANDOFF,
    rear_standoff_ny: float = REAR_STANDOFF_NY,
    rear_standoff_z_lo: float = REAR_STANDOFF_Z_LO,
    rear_standoff_z_hi: float = REAR_STANDOFF_Z_HI,
    calf_standoff: float = CALF_STANDOFF,
    calf_standoff_z_lo: float = CALF_STANDOFF_Z_LO,
    calf_standoff_z_hi: float = CALF_STANDOFF_Z_HI,
    thigh_standoff: float = THIGH_STANDOFF,
    thigh_standoff_z_lo: float = THIGH_STANDOFF_Z_LO,
    thigh_standoff_z_hi: float = THIGH_STANDOFF_Z_HI,
    req_extra: float = 0.0,
    tris=None,
    smooth_iters: int = ANTIPOKE_SMOOTH_ITERS,
) -> np.ndarray:
    """Final anti-poke pass: push each armor vert out of the body so the
    actor's live morph can't punch through. Push-out only; never pulls cloth in.
    Measured against the injected UBE body (always present with valid normals)
    so it always lands, and called last so nothing undoes it.

    Each vert is cleared over the WORST body vert in a local neighbourhood so a
    nipple/belly bulge is caught even when a flatter vert is nearest.

    With `morph_amplitude`: adaptive clearance scales with per-vert outward
    amplitude — tight in static zones (sternum/back/sides, drops to adaptive_base),
    full clearance only where the body actually grows at runtime (breast/belly/butt,
    up to adaptive_cap). Without it, falls back to fixed flat_clear + bust-zone ramp.
    Verts > max_body_dist from the body are untouched."""
    from scipy.spatial import cKDTree
    v = np.asarray(verts, dtype=np.float64)
    bv = np.asarray(body_verts, dtype=np.float64)
    bn = np.asarray(body_normals, dtype=np.float64)
    if len(v) == 0 or len(bv) == 0 or bv.shape != bn.shape:
        return np.asarray(verts, dtype=np.float32)
    tree = cKDTree(bv)
    kk = min(k, len(bv))
    dd, jj = tree.query(v, k=kk)
    if kk == 1:
        dd = dd[:, None]; jj = jj[:, None]
    nearest = jj[:, 0]
    nrm = bn[nearest]
    s_cur = ((v - bv[nearest]) * nrm).sum(1)
    s_k = ((v[:, None, :] - bv[jj]) * nrm[:, None, :]).sum(axis=2)
    s_k = np.where(dd <= radius, s_k, np.inf)
    worst = np.min(s_k, axis=1)
    worst = np.where(np.isfinite(worst), worst, s_cur)   # fallback: nearest only
    if morph_amplitude is not None and len(morph_amplitude) == len(bv):
        # ADAPTIVE: only ramp clearance where the body actually grows at runtime
        # (high morph amplitude). Static zones get just the z-fight floor, so
        # loose/thick armor stops floating off the body. WORST (max) amplitude
        # over the in-radius neighbours -> a high-morph nipple/belly bulge still
        # drives the clearance even when the nearest body vert is flat.
        amp = np.asarray(morph_amplitude, dtype=np.float64)
        amp_k = np.where(dd <= radius, amp[jj], 0.0)
        amp_worst = np.max(amp_k, axis=1)
        req = np.clip(adaptive_base + adaptive_factor * amp_worst,
                      adaptive_base, adaptive_cap)
    else:
        req = np.full(len(v), float(flat_clear))
        if body_nipple is not None and len(body_nipple) == len(bv):
            z = bv[nearest][:, 2]
            in_bust = (z >= bust_z[0]) & (z <= bust_z[1])
            nipw = np.asarray(body_nipple, dtype=np.float64)[nearest]
            req = np.where(in_bust,
                           np.clip(flat_clear + nipw * nipple_gain, flat_clear, bust_clear),
                           req)
    if jiggle_amplitude is not None and len(jiggle_amplitude) == len(bv):
        # JIGGLE overshoot term (see JIGGLE_CLEARANCE_ENABLED): SMP softbody
        # swings past the rest surface, so ADD clearance where the body's jiggle
        # weight is high. Worst (max) weight over the in-radius neighbours, same
        # rationale as the morph term. Additive ON TOP of the morph-clipped req:
        # static growth and dynamic bounce stack at runtime. Bounded by
        # jiggle_cap; zero-weight zones add exactly 0 (tight fit preserved).
        jig = np.asarray(jiggle_amplitude, dtype=np.float64)
        jig_k = np.where(dd <= radius, jig[jj], 0.0)
        jig_worst = np.max(jig_k, axis=1)
        req = req + np.clip(jiggle_gain * jig_worst, 0.0, jiggle_cap)
    if rear_standoff > 0.0:
        # REAR butt / upper-thigh dynamic standoff (see REAR_STANDOFF): a flat
        # minimum gap where the nearest body vert is rear-facing and at butt/
        # upper-thigh height, so tight leg armor survives the stride's back-swing.
        # np.maximum (not +=): it's a FLOOR on req, never stacks on the terms above.
        bz = bv[nearest][:, 2]
        rear_zone = (nrm[:, 1] < rear_standoff_ny) & (bz >= rear_standoff_z_lo) & (bz <= rear_standoff_z_hi)
        req = np.where(rear_zone, np.maximum(req, rear_standoff), req)
    if calf_standoff > 0.0:
        # CALF / lower-leg flex standoff (see CALF_STANDOFF): flat minimum gap over the
        # lower-leg band, all-round -- the knee/calf bend punches through the thin
        # static-zone clearance mid-stride. Floor on req; raises only sub-floor verts.
        bz = bv[nearest][:, 2]
        calf_zone = (bz >= calf_standoff_z_lo) & (bz <= calf_standoff_z_hi)
        req = np.where(calf_zone, np.maximum(req, calf_standoff), req)
    if thigh_standoff > 0.0:
        # THIGH all-round standoff (see THIGH_STANDOFF): flat minimum gap over the whole
        # thigh circumference so the plate clears the body front + back + sides without the
        # rear-only lopsidedness. Floor on req; raises only sub-floor verts.
        bz = bv[nearest][:, 2]
        thigh_zone = (bz >= thigh_standoff_z_lo) & (bz <= thigh_standoff_z_hi)
        if THIGH_STANDOFF_MEDIAL:
            # inner face only: nearest body normal points toward the centerline
            # (opposite sign to the body vert's x) and is meaningfully sideways.
            bx = bv[nearest][:, 0]
            nx = nrm[:, 0]
            thigh_zone = thigh_zone & (nx * bx < 0.0) & (np.abs(nx) > 0.30)
        req = np.where(thigh_zone, np.maximum(req, thigh_standoff), req)
    if req_extra > 0.0:
        # Layer-aware floor (LAYERED_ANTIPOKE): outer layers require extra
        # standoff so stacked garments don't converge to the same surface.
        # Added AFTER the morph/jiggle clips so the cap can't swallow it.
        req = req + float(req_extra)
    push = np.clip(req - worst, 0.0, max_push)            # push OUT only
    if tris is not None and smooth_iters > 0:
        # Feather the push over the mesh so per-vert normal/magnitude jumps
        # don't crinkle the cloth; floored at the raw push (never reopens).
        push = np.clip(_smooth_push_field(push, push, tris, smooth_iters),
                       0.0, max_push)
    push = np.where(dd[:, 0] < max_body_dist, push, 0.0)  # leave far drapes alone
    return (v + nrm * push[:, None]).astype(np.float32)


# Push soft-body / HDT-rigged CLOTH outward over the breast & butt where the larger
# UBE body pokes through it. The main anti-poke (clear_armor_outside_body) SKIPS
# soft-body / physics shapes because moving every vert disturbs the sim; this pass
# is band-limited to the breast + butt so only the poke-through zone is nudged (the
# sim rest shape is otherwise untouched). Body-PRESERVING (never moves the body),
# push-out only. Confirmed in-game (Ancient Falmer cuirass breast). Default ON;
# CBBE2UBE_NO_SOFTCLOTH_INFLATE=1 disables.
INFLATE_SOFTCLOTH = (
    os.environ.get("CBBE2UBE_NO_SOFTCLOTH_INFLATE", "").strip().lower()
    not in ("1", "true", "yes", "on"))
_SOFTCLOTH_BUST_CLEAR = float(os.environ.get("CBBE2UBE_SOFTCLOTH_BUST_CLEAR", "1.8"))
_SOFTCLOTH_BUTT_CLEAR = float(os.environ.get("CBBE2UBE_SOFTCLOTH_BUTT_CLEAR", "1.5"))


def _inflate_cloth_over_bust_butt(
    verts, body_verts, body_normals, *, tris=None,
    bust_clear: float = _SOFTCLOTH_BUST_CLEAR,
    butt_clear: float = _SOFTCLOTH_BUTT_CLEAR,
    max_push: float = 4.5, radius: float = 4.0, smooth_iters: int = 4,
) -> np.ndarray:
    """Inflate soft-body / HDT-rigged cloth outward over the breast & butt bands so
    the larger UBE body stops poking through, WITHOUT moving the body. Body-driven:
    for every protruding body vert in a band, push the nearby cloth verts out along
    the body normal to sit `*_clear` proud (jiggle headroom). Push-out only, capped
    and smoothed. Bands are measured in body space (breast: front, z 93-118; butt:
    back, z 70-96) so a bra-line/leg-line seam elsewhere on the shape is untouched.
    See INFLATE_SOFTCLOTH."""
    from scipy.spatial import cKDTree
    v = np.asarray(verts, np.float64)
    bv = np.asarray(body_verts, np.float64)
    bn = np.asarray(body_normals, np.float64)
    if len(v) == 0 or len(bv) == 0 or bv.shape != bn.shape:
        return np.asarray(verts, np.float32)
    bn = bn / (np.linalg.norm(bn, axis=1, keepdims=True) + 1e-9)
    breast = ((bv[:, 1] > 1.0) & (bv[:, 2] > 93.0) & (bv[:, 2] < 118.0)
              & (bn[:, 1] > 0.2))
    butt = ((bv[:, 1] < -2.0) & (bv[:, 2] > 70.0) & (bv[:, 2] < 96.0)
            & (bn[:, 1] < -0.4))
    if not (breast.any() or butt.any()):
        return np.asarray(verts, np.float32)
    atree = cKDTree(v)
    _, it = atree.query(bv)                     # nearest cloth vert per body vert
    poke = ((bv - v[it]) * bn).sum(1)           # + => body is OUTSIDE the cloth
    push = np.zeros(len(v))
    for band, clear in ((breast, float(bust_clear)), (butt, float(butt_clear))):
        for bi in np.where(band & (poke > 0.1))[0]:
            for av in atree.query_ball_point(bv[bi], radius):
                need = clear - float((v[av] - bv[bi]) @ bn[bi])
                if need > push[av]:
                    push[av] = need
    push = np.clip(push, 0.0, max_push)
    if not push.any():
        return np.asarray(verts, np.float32)
    if tris is not None and smooth_iters > 0:
        try:
            push = np.clip(
                _smooth_push_field(push, push, np.asarray(tris, np.int64),
                                   smooth_iters), 0.0, max_push)
        except Exception:
            pass
    _, ib = cKDTree(bv).query(v)                 # push each vert along its body normal
    return (v + bn[ib] * push[:, None]).astype(np.float32)


def shape_body_offset(shape) -> np.ndarray:
    """Translation that maps a shape's STORED (local) verts into body space.

    Some armor shapes are authored in a shifted coordinate space and repositioned
    by their NiAVObject `transform` at render time (e.g. a vanilla elven cuirass
    top whose verts sit at Z=-49 with a +120 Z transform -> renders at the chest).
    The converter's warp/morph/conform math runs in BODY space (against the UBE
    body), so for such a shape it must use `verts + offset`, not the raw verts --
    otherwise it matches the wrong body region (-> "top doesn't scale", distortion).

    Returns the (3,) translation. Identity-transform shapes (the vast majority)
    return zeros, so callers that add/subtract it are unaffected. Translation only:
    a rotation/scale in the transform is left unhandled (rare; the translation part
    is still corrected, which is strictly better than ignoring the transform).
    Render is preserved by callers that ADD this before the math and SUBTRACT it
    before writing -- the stored verts + unchanged transform are identical except
    for the (now correctly-computed) warp.
    """
    tr = getattr(shape, "transform", None)
    t = getattr(tr, "translation", None) if tr is not None else None
    if t is None:
        return np.zeros(3, dtype=np.float64)
    try:
        return np.array([float(t.x), float(t.y), float(t.z)], dtype=np.float64)
    except Exception:
        try:
            return np.array([float(t[0]), float(t[1]), float(t[2])], dtype=np.float64)
        except Exception:
            return np.zeros(3, dtype=np.float64)


def repair_collapsed_tris(cur_verts: np.ndarray, src_verts: np.ndarray,
                          tris: np.ndarray, *, area_eps: float = 1e-4,
                          max_fix: float = 3.0) -> "tuple[np.ndarray, int]":
    """Un-pinch triangles the vertex ops collapsed to zero area.

    The warp / inflate / conform / depth-separation passes apply slightly
    different per-vert displacements to adjacent verts, which pinches thin
    fabric/metal triangles flat (zero area). Those render as black slivers,
    holes, or flicker -- the "mangled fabric" symptom (measured: a multi-layer garment
    0 -> 58 collapsed tris, its metal belt 106 -> 253). Source-quality folded
    geometry (e.g. a 3BA_Vagina seam) is degenerate in the SOURCE too; we must
    NOT disturb that, so we only repair a tri whose source area was fine.

    For each op-collapsed tri, restore its verts to their SOURCE relative shape
    (offset from the tri centroid) at the CONVERTED centroid location -- this
    regains the source area while keeping the fitted position, so it un-pinches
    without un-fitting. Verts shared by several collapsed tris get the average
    target; a restore that would move a vert more than `max_fix` is skipped
    (guards against a huge collapsed tri yanking a vert across the mesh).
    Returns (possibly-modified verts, number of tris repaired)."""
    cur = np.asarray(cur_verts, dtype=np.float64).copy()
    src = np.asarray(src_verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    if t.size == 0 or cur.shape != src.shape or cur.ndim != 2:
        return cur.astype(np.float32), 0

    def _areas(v):
        a = v[t[:, 0]]; b = v[t[:, 1]]; c = v[t[:, 2]]
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    # Op-collapsed = current area pinched below area_eps while the SOURCE tri was
    # clearly healthy (>= 4*area_eps). The margin avoids touching legitimately
    # tiny fabric tris (fine meshes have many sub-area_eps tris by design) and
    # source-degenerate folds (seams/genital geometry).
    bad = np.where((_areas(cur) < area_eps) & (_areas(src) >= 4.0 * area_eps))[0]
    if bad.size == 0:
        return cur.astype(np.float32), 0
    targets: "dict[int, list]" = {}
    for ti in bad.tolist():
        idx = t[ti]
        ccen = cur[idx].mean(axis=0)
        scen = src[idx].mean(axis=0)
        for vi in idx.tolist():
            targets.setdefault(vi, []).append(ccen + (src[vi] - scen))
    for vi, tgs in targets.items():
        tgt = np.mean(np.asarray(tgs), axis=0)
        if np.linalg.norm(tgt - cur[vi]) <= max_fix:
            cur[vi] = tgt
    return cur.astype(np.float32), int(bad.size)


def _weight_matched_ube_ref(src_path: Path, ube_body_ref_path: Path) -> Path:
    """If the file being converted has a `_0` / `_1` weight suffix, try to
    use a UBE body ref with the same weight suffix. Otherwise return the
    provided ref unchanged.

    Skyrim renders `_0.nif` for slim characters and `_1.nif` for full.
    Mismatching these means injecting a slim UBE body into a full output,
    which gives the player the wrong-shape body underneath the armor.
    """
    src_stem = src_path.stem
    ref = Path(ube_body_ref_path)
    for suffix in ("_0", "_1"):
        if src_stem.endswith(suffix):
            other = "_1" if suffix == "_0" else "_0"
            if ref.stem.endswith(other):
                candidate = ref.parent / (ref.stem[:-len(other)] + suffix + ref.suffix)
                if candidate.is_file():
                    return candidate
            break
    return ref


# Lazy pynifly import — used by phase 2. Phase 1 doesn't need it.
def _pynifly():
    """Return the pyn.pynifly module, importing lazily so phase 1 stays
    independent of having pynifly installed.

    Source runs: add the repo's `.pynifly/` dir to sys.path so `pyn` resolves.
    Frozen runs (PyInstaller): `pyn` is bundled as a top-level package and
    NiflyDLL.dll sits at the bundle root next to the exe, so pyn's own DLL
    loader (dirname(dirname(__file__))/NiflyDLL.dll) finds it — we just import,
    no sys.path surgery (the source-tree `.pynifly` path doesn't exist there)."""
    if not getattr(sys, "frozen", False):
        proj_root = Path(__file__).resolve().parent.parent
        pn_path = str(proj_root / ".pynifly")
        if pn_path not in sys.path:
            sys.path.insert(0, pn_path)
    from pyn import pynifly  # type: ignore
    return pynifly


# Shape names that mark a CBBE inline body -- the 3BA mesh + anatomy shapes, stripped
# in phase 2 and replaced with the UBE BaseShape. Vanilla replacers also embed a small
# placeholder body (FemaleUnderwearBody etc., below the heuristic vert-count) caught by
# the name prefixes below; else phase 1 copies it through and the CBBE-sized underwear
# clips the UBE legs at the floor.  [DESIGN: Phase-2 body-swap]
BODY_SHAPE_NAMES = frozenset({
    "3BA", "3BA_Anus", "3BA_Vagina",
})

# Lowercase substring matches that ALSO identify inline body shapes
# beyond the canonical BODY_SHAPE_NAMES (mostly vanilla-replacer
# placeholder bodies). Matched via `name.lower().startswith(...)`.
BODY_SHAPE_NAME_PREFIXES = (
    "femaleunderwearbody",  # vanilla "FemaleUnderwearBody:0" placeholder
    "femalebody",           # generic placeholder used in some replacers
)


def _is_inline_body_name(name: "str | None") -> bool:
    """Name-only inline-body test (canonical names + vanilla placeholder
    prefixes). Lightweight companion to _looks_like_inline_body for code that
    has a shape NAME but not a full Shape (e.g. HDT re-import from a raw NIF)."""
    nl = (name or "").lower()
    return name in BODY_SHAPE_NAMES or nl.startswith(BODY_SHAPE_NAME_PREFIXES)


def validate_dst_nif(dst_path: "Path",
                     tri_path: "Path | None" = None,
                     src_path: "Path | None" = None) -> list[str]:
    """Run a series of sanity checks on a converted NIF + optional TRI.

    Returns a list of human-readable warning strings (empty list means
    "clean"). Catches subtle bugs that would otherwise only manifest
    as visual glitches in-game:
      * Skinned verts whose bone-weight sum != 1.0 (~0.01 tolerance)
      * Skinned verts with NO weights (rendered at bone origin / spike)
      * More than 4 bone influences per vert (Skyrim hard limit)
      * BODYTRI references a TRI whose shape entries don't match
        the NIF's shape names (morph application no-ops at runtime)
      * NEW z-fight pairs introduced by the conversion — i.e. shape
        pairs that overlap MORE in dst than they did in src. If
        `src_path` is provided we subtract source overlap from dst
        overlap; otherwise we report dst overlap as-is. (The pre-
        delta version flagged hundreds of inherent design-time
        overlaps — e.g. Ebony Mail's two co-located Cuirass layers
        ship with 279 near-coincident verts at source and the
        converter actually REDUCED that to 26. Without the delta
        check those warnings were noise drowning real regressions.)
    """
    warnings: list[str] = []
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception as e:
        return [f"failed to reload for validation: {e!r}"]

    name = dst_path.name

    # Per-shape weight / bone-count checks.
    for s in nf.shapes:
        bones = s.bone_names or []
        if not bones:
            continue
        bw = s.bone_weights or {}
        if not bw:
            continue
        n_verts = len(s.verts)
        per_vert_sum = np.zeros(n_verts, dtype=np.float64)
        per_vert_count = np.zeros(n_verts, dtype=np.int32)
        for _, pairs in bw.items():
            for idx, w in pairs:
                if 0 <= idx < n_verts and w > 1e-6:
                    per_vert_sum[idx] += w
                    per_vert_count[idx] += 1

        # Zero-weight verts render at bone origin (spike artifacts).
        unweighted = int((per_vert_count == 0).sum())
        if unweighted > 0:
            warnings.append(
                f"{name} :: {s.name}: {unweighted} verts have zero "
                f"bone weight (spike risk)"
            )

        # Skyrim hard cap: 4 bone weights per vert.
        over_4 = int((per_vert_count > 4).sum())
        if over_4 > 0:
            warnings.append(
                f"{name} :: {s.name}: {over_4} verts have >4 bone "
                f"influences (Skyrim hard cap = 4)"
            )

        # Weight sum check (only for verts that have ANY weight).
        weighted_mask = per_vert_count > 0
        if weighted_mask.any():
            sums = per_vert_sum[weighted_mask]
            off_by_1 = np.abs(sums - 1.0) > 0.01
            if off_by_1.any():
                worst = float(np.abs(sums - 1.0).max())
                warnings.append(
                    f"{name} :: {s.name}: {int(off_by_1.sum())} verts "
                    f"have weight sum != 1.0 (worst delta {worst:.3f})"
                )

        # Non-identity scale on a skinned shape: the engine ignores NiAVObject
        # transform for skinned meshes, so it renders at the wrong size.
        # _copy_shape bakes this; flag any that slipped through.
        try:
            _tscale = float(s.transform.scale)
            if abs(_tscale - 1.0) > 1e-3:
                warnings.append(
                    f"{name} :: {s.name}: skinned shape has non-identity "
                    f"transform scale {_tscale:.4f} (renders at wrong size — "
                    f"scale not baked into verts)"
                )
        except Exception:
            pass

    # Z-fight detection across pairs of shapes. Finds verts in
    # different shapes that are within ~0.05 units of each other
    # — classic setup for shimmering shading at runtime where two
    # cloth/leather layers occupy the same surface depth. We compare
    # dst overlap against src overlap (if src_path is given) so we
    # only flag NEW overlaps the converter introduced. Inherent
    # source-mod design overlaps (e.g. multi-layer fur shaders that
    # ship co-located by design) get filtered out as background noise.
    ZFIGHT_THRESHOLD = 0.05

    def _pairwise_overlap_counts(loaded_nif) -> "dict[tuple[str, str], int]":
        """Return {(a_name, b_name) sorted: count_of_pairs_within_thresh}."""
        from scipy.spatial import cKDTree
        textured = [
            s for s in loaded_nif.shapes
            if (s.textures or {}) and s.name not in UBE_BODY_INJECT_NAMES
        ]
        trees = {
            s.name: cKDTree(np.asarray(s.verts, dtype=np.float64))
            for s in textured
        }
        out: dict[tuple[str, str], int] = {}
        for i, a in enumerate(textured):
            a_verts = np.asarray(a.verts, dtype=np.float64)
            for b in textured[i + 1:]:
                dists, _ = trees[b.name].query(
                    a_verts, k=1, distance_upper_bound=ZFIGHT_THRESHOLD)
                n = int((dists != np.inf).sum())
                if n > 0:
                    key = tuple(sorted((a.name, b.name)))
                    out[key] = n
        return out

    try:
        dst_overlap = _pairwise_overlap_counts(nf)
        src_overlap: dict[tuple[str, str], int] = {}
        if src_path is not None:
            try:
                src_nif = _pynifly().NifFile(filepath=str(src_path))
                src_overlap = _pairwise_overlap_counts(src_nif)
            except Exception:
                src_overlap = {}  # fall through to absolute counts
        for (a_name, b_name), dst_n in sorted(dst_overlap.items()):
            src_n = src_overlap.get((a_name, b_name), 0)
            delta = dst_n - src_n
            if src_path is not None:
                # Only warn on NEW overlaps (converter introduced).
                if delta <= 0:
                    continue
                warnings.append(
                    f"{name} :: z-fight risk: {a_name} ↔ {b_name} "
                    f"share {dst_n} verts within "
                    f"{ZFIGHT_THRESHOLD} units ({delta:+d} vs src)"
                )
            else:
                warnings.append(
                    f"{name} :: z-fight risk: {a_name} ↔ {b_name} "
                    f"share {dst_n} verts within "
                    f"{ZFIGHT_THRESHOLD} units"
                )
    except Exception:
        pass

    # BODYTRI cross-check: TRI shapes must match NIF shape names.
    # Unmatched entries are silently ignored by NioOverride at runtime.
    if tri_path is not None and tri_path.is_file():
        try:
            from .tri import TriFile
            tri = TriFile.load(tri_path)
            nif_shapes = {s.name for s in nf.shapes}
            tri_shapes = {sh.name for sh in tri.shapes}
            tri_only = tri_shapes - nif_shapes
            if tri_only:
                warnings.append(
                    f"{name} :: BODYTRI lists {len(tri_only)} shape(s) "
                    f"not in NIF: {sorted(tri_only)[:5]}"
                )
        except Exception as e:
            warnings.append(f"{name} :: TRI validation failed: {e!r}")

    # HDT-SMP XML cross-check: verify the referenced XML exists, parses,
    # and only references bones in the NIF skeleton.
    try:
        hdt_xml_rel: str | None = None
        for ed in nf.rootNode.extra_data():
            if (hasattr(ed, "string_data")
                    and ed.name == "HDT Skinned Mesh Physics Object"):
                hdt_xml_rel = ed.string_data
                break
        if hdt_xml_rel:
            # Resolve relative to the NIF's `meshes/` ancestor.
            xml_disk: "Path | None" = None
            for parent in [dst_path, *dst_path.parents]:
                if parent.name.lower() == "meshes":
                    norm = hdt_xml_rel.replace("\\", "/").lstrip("/")
                    if norm.lower().startswith("meshes/"):
                        norm = norm[len("meshes/"):]
                    cand = parent / norm
                    if cand.is_file():
                        xml_disk = cand
                    break
            if xml_disk is None:
                warnings.append(
                    f"{name} :: HDT XML referenced but not found on "
                    f"disk: {hdt_xml_rel!r}"
                )
            else:
                # Use hdt_xml_gen's validator. Gather all bones across
                # all shapes — HDT XML can reference any of them.
                from .hdt_xml_gen import validate_armor_hdt_xml
                all_nif_bones: set[str] = set()
                for s in nf.shapes:
                    for b in (s.bone_names or []):
                        all_nif_bones.add(b)
                xml_warnings = validate_armor_hdt_xml(xml_disk, all_nif_bones)
                for w in xml_warnings:
                    warnings.append(f"{name} :: {w}")
    except Exception as e:
        warnings.append(f"{name} :: HDT XML validation failed: {e!r}")

    return warnings

# Shape names that signal the NIF already targets UBE — only true UBE body
# shapes with their canonical large vertex counts qualify (see M1_findings).
# Smaller "VirtualBody" entries in a CBBE source are skirt/cloth collision
# proxies and don't disqualify the file.
# Vertex count thresholds used to distinguish UBE body shapes from
# similarly-named collision proxies. UBE BaseShape ~29k, VirtualBody ~14k.
_UBE_BASESHAPE_MIN_VERTS = 20_000
_UBE_VIRTUALBODY_MIN_VERTS = 10_000

# Heuristic thresholds for detecting an inline body shape that's NOT named
# `3BA` (mods commonly use bespoke names like `<prefix>_<ArmorName>_Body`,
# `<Name>_Body`, etc.). A shape spanning almost the full character height
# AND skinned to many bones is almost certainly a body. Tuned to:
#   * catch full-body inline meshes (CBBE 3BA spans ~103 Z, full bones)
#   * NOT catch long armor pieces (capes / coats — high Z, few bones)
#   * NOT catch accessory shapes covering much of the torso (e.g. an
#     `_Acs`-suffixed shape: Z=63.6, bones=59 — fails the Z threshold)
_BODY_HEURISTIC_MIN_Z_RANGE = 70.0
_BODY_HEURISTIC_MIN_BONES = 40
# Skirts and other cloth pieces can have lots of bones (one per SMP segment)
# and span most of the character's height — so we also require a vertex count
# typical of body meshes. Real CBBE-style bodies have 5000+ verts; SMP skirts
# typically 1000-3000.
_BODY_HEURISTIC_MIN_VERTS = 4000
# Lower vert floor used ONLY once a shape has already passed the nude-body
# diffuse gate (so cloth is excluded). Vanilla-topology body skins shipped by
# armour replacers (HDT-SMP Vanilla's forsworn `ForswornFemaleBody` ~1.5k
# verts) are well under the custom-inline-body count above but ARE bodies.
# Floor exists only to reject tiny body-textured decals. See #164.
_BODY_SKIN_MIN_VERTS = 500
# Bone floor for the same body-skin path. A vanilla-topology body skin is
# skinned to ~22 bones (no 3BA scale-bone cluster), well under the 40-bone
# custom-body threshold. Once the body-skin diffuse + full-Z gates pass, this
# only confirms the shape is skinned to a real skeleton (not a static decal).
_BODY_SKIN_MIN_BONES = 15

# Diffuse-texture substrings that identify an actual nude BODY mesh
# (as opposed to a large full-length CLOTH piece). The generic body
# heuristic below additionally requires one of these — otherwise a
# floor-length robe / gown / dress (8000+ verts, many SMP bones,
# full-height Z span) gets misclassified as an inline body and DROPPED,
# leaving only the panty (real bug found on monkrobes / archmagerobes /
# necromancerrobes etc. — the converted NIF had BaseShape + Panty only).
_BODY_SKIN_TEXTURE_MARKERS = (
    "femalebody", "malebody", "bodyfemale", "bodymale", "femaleskin",
)


def _shape_diffuse_is_body_skin(shape) -> bool:
    """True if the shape's diffuse texture looks like a nude body skin.
    Accepts either a nif_io.Shape (via ._backing) or a raw pynifly shape.
    Used to gate the generic inline-body heuristic so full-length cloth
    isn't mistaken for a body."""
    raw = getattr(shape, "_backing", None) or shape
    try:
        tex = dict(getattr(raw, "textures", {}) or {})
    except Exception:
        return False
    diff = (tex.get("Diffuse") or tex.get("0")
            or next((v for v in tex.values() if v), "")).lower()
    return any(m in diff for m in _BODY_SKIN_TEXTURE_MARKERS)


def _looks_like_inline_body(shape: "nif_io.Shape") -> bool:
    """Heuristic body detector for shapes not caught by name."""
    if shape.name in BODY_SHAPE_NAMES:
        return True
    # Lowercase-prefix match for vanilla placeholder bodies that ship
    # with replacer NIFs (FemaleUnderwearBody:0 etc.). These are
    # CBBE-sized and below the 4000-vert heuristic, so we need the
    # explicit name match to catch them.
    name_low = (shape.name or "").lower()
    for prefix in BODY_SHAPE_NAME_PREFIXES:
        if name_low.startswith(prefix):
            return True
    if shape.name == "BaseShape" and len(shape.verts) >= _UBE_BASESHAPE_MIN_VERTS:
        return True
    if shape.name == "VirtualBody" and len(shape.verts) >= _UBE_VIRTUALBODY_MIN_VERTS:
        return True
    # General heuristic — needs ALL of: a nude-body-skin diffuse texture,
    # full character height span, many distinct skeleton bones, and enough
    # geometry to be a body. The TEXTURE gate goes first: it's what keeps a
    # floor-length robe / SMP skirt (large + many SMP bones + full Z span, but
    # a CLOTH diffuse) from being misread as a body and dropped.
    if not _shape_diffuse_is_body_skin(shape):
        return False
    if len(shape.bone_names) < _BODY_SKIN_MIN_BONES:
        return False
    import numpy as _np
    z = _np.asarray(shape.verts, dtype=_np.float64)[:, 2]
    if float(z.max() - z.min()) < _BODY_HEURISTIC_MIN_Z_RANGE:
        return False
    # Vert-count floor, low here because cloth was already rejected above (by diffuse),
    # so we only need enough geometry to be a real body skin. This catches the vanilla-
    # topology body skins some replacers ship (~1.5k verts) that a 4000-vert gate dropped
    # into the cloth path, where they got scaled twice (warp + runtime node scale) = a
    # double-scaled body under skimpy armor. Phase 2 body-swaps them so the body scales
    # once.  [DESIGN: Phase-2 body-swap]
    return len(shape.verts) >= _BODY_SKIN_MIN_VERTS


@dataclass
class ConvertResult:
    src_path: Path
    dst_path: Path | None
    status: str                              # "converted" | "skipped"
    reason: str = ""
    body_shapes: list[str] = field(default_factory=list)
    armor_shapes: list[str] = field(default_factory=list)
    shape_locations: dict = field(default_factory=dict)  # name -> VertexBlockLocation|None
    # Shapes that FAILED to copy into the output NIF (both the primary and the
    # fallback copy raised) -> the piece is silently ABSENT in-game (invisible).
    # A "converted" result with a non-empty dropped_shapes is really a PARTIAL
    # conversion; auto_convert surfaces it as its own report bucket so it isn't
    # mistaken for a clean success.
    dropped_shapes: list[str] = field(default_factory=list)


def classify_shapes(nif: nif_io.Nif) -> tuple[list[str], list[str]]:
    """Split shapes into (inline-body, armor).

    Body detection combines:
      * canonical names (3BA / 3BA_Anus / 3BA_Vagina, plus BaseShape /
        VirtualBody with vertex-count guards)
      * a generic shape-shape heuristic: spans most of the character's
        vertical extent AND skinned to many bones. Catches custom-named
        inline bodies (e.g. `<prefix>_<ArmorName>_Body`, mod-specific naming).
    """
    body = []
    armor = []
    for s in nif.shapes:
        if _looks_like_inline_body(s):
            body.append(s.name)
        else:
            armor.append(s.name)
    return body, armor


def _load_body_mesh(ref_path: Path) -> MeshIndex:
    """Load a body reference NIF and build a MeshIndex of its largest shape.

    Picks the shape with the highest vertex count (the actual body mesh,
    not the VirtualBody collision proxy or any accessory shape).
    """
    nif = nif_io.load_nif(ref_path)
    if not nif.shapes:
        raise RuntimeError(f"reference body NIF has no shapes: {ref_path}")
    # Pick the largest shape — for CBBE 3BA femalebody this is the body,
    # for !UBE femalebody_tangent this is BaseShape.
    biggest = max(nif.shapes, key=lambda s: len(s.verts))
    return MeshIndex.build(biggest.verts, biggest.tris)


def convert_nif(
    src_path: str | Path,
    dst_path: str | Path,
    *,
    cbbe_ref_path: str | Path | None = None,
    ube_ref_path:  str | Path | None = None,
    cbbe_index: MeshIndex | None = None,
    ube_index:  MeshIndex | None = None,
    warp_armor: bool = False,
    ube_body_ref_path: str | Path | None = None,
    biped_slots: int = 0,
    alt_texture_shape_names: "set[str] | None" = None,
) -> ConvertResult:
    """Convert one CBBE armor NIF to a UBE-targeted NIF.

    `alt_texture_shape_names`: shape names an ESP alt-texture set targets by
    name (collected from the source mod's ARMO MO2S/MO3S entries). These are
    protected from the morph-cap merge so color variants keep working.

    Default behavior: verbatim file copy if no inline body shapes are
    present. This matches what real BodySlide-built UBE NIFs do for armor
    pieces (the armor verts come from the slider-zero shapedata, not from
    body-driven warping).

    If inline body shapes ARE present AND `ube_body_ref_path` is provided,
    run phase 2 body-swap: deep-copy non-body shapes from source + inject
    BaseShape / VirtualBody from the UBE reference NIF.

    Set `warp_armor=True` to position-warp armor verts using the
    CBBE-body -> UBE-body correspondence (experimental — empirically loses
    on every measured piece, kept for diagnostics).
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)

    nif = nif_io.load_nif(src_path)
    body_names, armor_names = classify_shapes(nif)

    # HH_OFFSET is a NiFloatExtraData that pynifly silently drops on load.
    # For non-body heeled pieces: skip mesh conversion so the patcher keeps the
    # original mesh (heel intact) and only adds UBE races. Feet barely differ
    # CBBE<->UBE so losing morph-scaling is negligible vs a broken heel.
    # Raw byte-scan because pynifly already dropped the block.
    _BODY_SLOT_BIT = 1 << (32 - 30)
    _hh_transplant_value = None  # heel offset (float) to re-inject after convert
    if not body_names and not (biped_slots & _BODY_SLOT_BIT):
        from . import hh_offset
        try:
            with open(src_path, "rb") as _fh:
                # Case-insensitive: boots ship 'HH_Offset' too (NiOverride reads
                # any case); a case-sensitive scan dropped the heel block.
                _heeled = hh_offset.contains_hh_offset(_fh.read(262144))
        except OSError:
            _heeled = False
        if _heeled:
            # Convert the boot normally and transplant the HH_OFFSET block back
            # at the binary level after all pynifly saves. If the binary parser
            # can't read the source value, fall back to ESP-only (skip + delete
            # stale mesh so the patcher keeps the original heeled mesh).
            _hh_transplant_value = hh_offset.read_hh_offset(src_path)
            if _hh_transplant_value is None:
                try:
                    dst_path.unlink()
                except OSError:
                    pass
                return ConvertResult(
                    src_path=src_path,
                    dst_path=None,
                    status="skipped",
                    reason="heeled (HH_OFFSET) but binary parser unsupported — "
                           "kept ORIGINAL mesh (ESP-only) so the heel survives",
                )
            # else: convert normally; transplant the heel at the very end.

    # Exposed body skin baked into a body-slot armor (open-cleavage breast/belly
    # skin slice): drop the partial slice and inject the full UBE BaseShape so
    # exposed skin IS the real body. Routes to phase-2 body-swap; body-slot items
    # only (slot 32 hides the actor body) or unknown slot (direct calls).
    exposed_skin_names: list[str] = []
    if (ube_body_ref_path is not None and not body_names
            and (not biped_slots or (biped_slots & _BODY_SLOT_BIT))):
        try:
            # Decide on the WEIGHT PAIR, not this file alone: the coincidence test is
            # weight-sensitive, so a borderline baked-skin slice can qualify at one
            # weight but not the other -> mismatched shape sets -> morph explosion.
            # Union the exposed-skin names over both weights so they take the same path.
            # [DESIGN: Weight-pair (_0/_1) consistency]
            _names: "set[str]" = set()
            _pair = [(src_path, nif)]
            _stem = src_path.stem
            for _a, _b in (("_0", "_1"), ("_1", "_0")):
                if _stem.endswith(_a):
                    _sib = src_path.with_name(
                        _stem[: -len(_a)] + _b + src_path.suffix)
                    if _sib.exists():
                        try:
                            _pair.append((_sib, nif_io.load_nif(_sib)))
                        except Exception:
                            pass
                    break
            for _sp, _snif in _pair:
                _wsuf = next(
                    (x for x in ("_0", "_1") if _sp.stem.endswith(x)), "_1")
                _cb = _find_cbbe_base_body(weight=_wsuf)
                _ub = _find_ube_femalebody(weight=_wsuf)
                if not (_cb and _ub):
                    continue
                _cbbe_v0, _ = _cached_cbbe_to_ube_delta(_cb, _ub)
                _names.update(_exposed_body_skin_shape_names(_snif, _cbbe_v0))
            # Only inject for names that actually exist in THIS file (the pair
            # shares shape names, but never route a phantom shape).
            _here = {s.name for s in nif.shapes}
            exposed_skin_names = sorted(n for n in _names if n in _here)
        except Exception:
            exposed_skin_names = []

    if body_names or exposed_skin_names:
        if ube_body_ref_path is not None:
            matched_ref = _weight_matched_ube_ref(src_path, Path(ube_body_ref_path))
            return convert_nif_phase2(
                src_path, dst_path,
                ube_body_ref_path=matched_ref,
                cbbe_body_ref_path=cbbe_ref_path,
                biped_slots=biped_slots,
                alt_texture_shape_names=alt_texture_shape_names,
                extra_body_drop_names=tuple(exposed_skin_names),
            )
        return ConvertResult(
            src_path=src_path,
            dst_path=None,
            status="skipped",
            reason=f"contains inline body shapes {body_names} (M3 phase 2; "
                   f"pass ube_body_ref_path to enable body-swap)",
            body_shapes=body_names,
            armor_shapes=armor_names,
        )

    if not armor_names:
        return ConvertResult(
            src_path=src_path,
            dst_path=None,
            status="skipped",
            reason="no armor shapes",
        )

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if not warp_armor:
        # Phase 1 = no inline body shape to swap, so just copy armor
        # shapes through. Two paths:
        #
        # (a) Body-aware rebuild: if we have a UBE body ref, rebuild each shape
        #     via _copy_shape with snap_armor_outside_body to fix static fit.
        # (b) Verbatim file copy (fallback): no body ref; preserves shape data
        #     exactly but doesn't fix fit.
        body_verts_for_fit = None
        body_normals_for_fit = None
        if ube_body_ref_path is not None:
            try:
                _, _bv, _bn = _cached_ube_body_verts(
                    Path(ube_body_ref_path))
                if _bv is not None and _bn is not None:
                    body_verts_for_fit = _bv
                    body_normals_for_fit = _bn
            except Exception:
                pass

        # Body-delta warp: preferred over the snap heuristic when a CBBE+UBE
        # body pair is available. Moves each vert by the body's CBBE->UBE
        # deformation, preserving the artist's drape. Falls back to snap.
        cbbe_verts_for_warp = None
        body_delta_for_warp = None
        # Per-shape recoverable failures. Defined at outer scope so it's
        # visible to both the rebuild-path handlers and the shared return.
        failed: list[tuple[str, str]] = []
        weight_suf = next(
            (s for s in ("_0", "_1") if src_path.stem.endswith(s)), "_1")
        cbbe_body_path_p1 = _find_cbbe_base_body(weight=weight_suf)
        ube_femalebody_path_p1 = _find_ube_femalebody(weight=weight_suf)
        if cbbe_body_path_p1 and ube_femalebody_path_p1:
            cbbe_verts_for_warp, body_delta_for_warp = \
                _cached_cbbe_to_ube_delta(
                    cbbe_body_path_p1, ube_femalebody_path_p1)

        use_rebuild = body_normals_for_fit is not None
        if not use_rebuild and ube_body_ref_path is not None:
            # A body ref WAS supplied but produced no usable verts (corrupt/locked
            # ref, pynifly error) -> we'd silently ship the CBBE-shaped source as a
            # clean "converted (copy)". Record it so the report flags the unfitted
            # passthrough instead.
            failed.append(("body-fit", "UBE body-ref present but unusable -> "
                                       "shipped UNFITTED verbatim copy"))
        if not use_rebuild:
            atomic_copy(src_path, dst_path)
        else:
            # Body-aware rebuild: open source, fresh dst, copy each shape with snap-outside
            # + M6 proximity re-skin. The re-skin transfers body bone weights to close armor
            # verts -- crucial for single-bone "rigid prop" pieces that NioOverride's
            # BodyMorph would otherwise skip; re-skinned to body bones they deform via
            # ordinary skinning.  [DESIGN: Fitting]
            pyn_lib = _pynifly()
            src_nif_for_fit = nif_io.open_nif_retry(str(src_path))  # transient-IO resilient
            dst_nif_for_fit = pyn_lib.NifFile()
            dst_nif_for_fit.initialize("SKYRIMSE", str(dst_path))

            # Body-ref NIF's BaseShape object for reskin.
            ube_ref_nif_for_reskin, _, _ = _cached_ube_body_verts(
                Path(ube_body_ref_path))
            ube_base_for_reskin = next(
                (x for x in ube_ref_nif_for_reskin.shapes
                 if x.name == "BaseShape"), None,
            )

            # Two-pass conversion so z-fight fixup can run across all
            # final verts (see phase-2 for the same pattern).
            shape_jobs_p1: list[dict] = []
            # Track which extremity slots the source NIF provides so we can
            # drop the CBBE-topology Hands/Feet shape and inject UBE ones.
            # Gauntlets ship a CBBE Hands body-skin alongside the cloth; slot 33
            # hides the UBE hands and renders the CBBE shape (finger mismatch).
            extremity_slots_to_replace: list[str] = []
            # HDT-SMP per-vertex soft-body cloth (e.g. a soft-body cloth shape on a hand-authored UBE armor)
            # must keep its authored weighting so it can still swing — skip
            # the body-fit reskin for it (see _hdt_softbody_shape_names).
            hdt_softbody_names = _hdt_softbody_shape_names(src_path)
            # SMP colliders (per-triangle) likewise skip the reskin -- the graft
            # over-jiggles them and destabilises the cloth they collide against
            # (see _hdt_collider_shape_names).
            hdt_collider_names = _hdt_collider_shape_names(src_path)
            # Multi-layer cloth (Cuirass_A/_B/_C) keeps source skin -- every graft pass
            # skips it or it CTDs on equip (see _layered_cloth_shape_names).
            layered_cloth_names = _layered_cloth_shape_names(src_nif_for_fit.shapes)
            for s in src_nif_for_fit.shapes:
                if _should_drop_shape(s.name):
                    continue  # vestigial mashup leftover (e.g. MaleUnderwearBody)
                if _is_body_skin_extremity(s.name):
                    # ALWAYS drop the source CBBE body-skin Hands/Feet shape.
                    # The working BOOTS carry NO body-skin shape (just the boot
                    # shell) and render; the only structural thing GAUNTLETS had
                    # that boots didn't was this extra body-skin "Hands" shape.
                    # Dropping it makes a gauntlet structurally match a boot.
                    # Only inject a UBE replacement when the (now-off) flag is on.
                    if INJECT_UBE_EXTREMITY_REPLACEMENT:
                        if _is_body_skin_hand(s.name):
                            extremity_slots_to_replace.append("Hands")
                        else:
                            extremity_slots_to_replace.append("Feet")
                    continue
                if not (s.textures or {}):
                    continue  # collision proxies dropped
                # Reconcile skin<->world: shapes with an offset global_to_skin
                # store verts far from world-frame body. Fit in WORLD frame,
                # restore to skin in pass 2. Identity g2s is a no-op.
                _shape_g2s = _shape_global_to_skin(s)
                # Gauntlet/boot shapes with fine animation bones get warp+inflate
                # but with per-vertex extremity masking to protect fingers/toes.
                if _shape_has_fine_animation_bones(s):
                    # Apply the full body-delta warp so the shell conforms to
                    # the UBE forearm/calf. Limb verts get 3BA scale bones;
                    # finger/toe verts are masked via _extremity_vert_mask so
                    # body morphs don't deform digits. World frame (restored in pass 2).
                    hf_orig = _verts_skin_to_world(
                        np.asarray(s.verts, dtype=np.float64), _shape_g2s)
                    hf_verts = hf_orig
                    hf_verts_modified = False
                    # Extremity fraction: forearm/calf(~0)=full warp;
                    # fingers/toes(~1)=no warp (UBE has no digit mesh); wrist blends.
                    hf_ef = _extremity_vert_fraction(s, len(hf_orig))
                    if (cbbe_verts_for_warp is not None
                            and body_delta_for_warp is not None):
                        try:
                            warped = warp_armor_by_body_delta(
                                hf_orig,
                                cbbe_verts_for_warp,
                                body_delta_for_warp,
                                ube_body_verts=body_verts_for_fit,
                                ube_body_normals=body_normals_for_fit,
                                min_standoff=ARMOR_TO_SKIN_BUFFER,
                            ).astype(np.float64)
                            if hf_ef is not None:
                                wf = (1.0 - hf_ef)[:, None]
                                hf_verts = hf_orig + (warped - hf_orig) * wf
                            else:
                                hf_verts = warped
                            hf_verts_modified = True
                        except Exception as e:
                            failed.append((f"{s.name}:warp-hf", repr(e)))
                    if body_verts_for_fit is not None:
                        try:
                            inflated = inflate_armor_outward(
                                hf_verts, body_verts_for_fit,
                                magnitude=ARMOR_INFLATION_MAGNITUDE_HANDS_FEET,
                                close_threshold=HAND_FOOT_INFLATION_FALLOFF,
                                body_normals=body_normals_for_fit,
                            ).astype(np.float64)
                            # Same digit protection on the inflation push.
                            if hf_ef is not None:
                                wf = (1.0 - hf_ef)[:, None]
                                hf_verts = hf_verts + (inflated - hf_verts) * wf
                            else:
                                hf_verts = inflated
                            hf_verts_modified = True
                        except Exception as e:
                            failed.append((f"{s.name}:inflate-hf", repr(e)))

                    hf_override_skin = None
                    if (ube_base_for_reskin is not None
                            and (s.bone_names or [])
                            and s.name not in RESKIN_SKIP_NAMES):
                        try:
                            existing_bones = list(s.bone_names)
                            existing_xforms = {}
                            existing_weights = {}
                            for bn in existing_bones:
                                pairs = s.bone_weights.get(bn) if hasattr(
                                    s, "bone_weights") else None
                                if pairs is None:
                                    continue
                                existing_weights[bn] = [
                                    (int(i), float(w))
                                    for i, w in (pairs.tolist()
                                                 if hasattr(pairs, "tolist")
                                                 else pairs)
                                ]
                                try:
                                    xf = s.get_shape_skin_to_bone(bn)
                                    if xf is not None:
                                        existing_xforms[bn] = xf
                                except Exception:
                                    pass
                            bones2, xf2, weights2 = add_scale_bone_weights(
                                existing_bones, existing_xforms,
                                existing_weights,
                                hf_verts,
                                ube_base_for_reskin,
                                reach=SCALE_BONE_REACH_HANDS_FEET,
                                max_transfer=SCALE_BONE_MAX_TRANSFER_HANDS_FEET,
                                exclude_vert_mask=_extremity_vert_mask(
                                    s, len(hf_verts)),
                                leg_region_only=True,
                                exclude_scale_bone_substrings=(
                                    _boot_far_thigh_scale_exclusions(
                                        s, biped_slots)),
                            )
                            if bones2 and weights2:
                                hf_override_skin = {
                                    "bones": bones2,
                                    "xforms": xf2,
                                    "weights": weights2,
                                }
                        except Exception:
                            hf_override_skin = None
                    shape_jobs_p1.append({
                        "src": s,
                        "verts": hf_verts,   # WORLD frame; restored in pass 2
                        "override_skin": hf_override_skin,
                        "verts_modified": hf_verts_modified,
                        "g2s": _shape_g2s,
                    })
                    continue
                # World-frame verts for the fit (see the top-of-loop note);
                # _shape_g2s was computed once above. Identity -> no-op.
                sv_world = _verts_skin_to_world(
                    np.asarray(s.verts, dtype=np.float64), _shape_g2s)
                try:
                    if (cbbe_verts_for_warp is not None
                            and body_delta_for_warp is not None):
                        # Body-delta warp + standoff buffer. The warp
                        # makes armor follow the body's CBBE->UBE
                        # deformation; the buffer keeps revealing
                        # armor from sinking into the UBE body and
                        # exposing skin between body and cloth.
                        snapped = warp_armor_by_body_delta(
                            sv_world,
                            cbbe_verts_for_warp,
                            body_delta_for_warp,
                            ube_body_verts=body_verts_for_fit,
                            ube_body_normals=body_normals_for_fit,
                            min_standoff=ARMOR_TO_SKIN_BUFFER,
                        )
                        # Post-warp inflation: adds standoff so body morphs don't
                        # grow past the author's CBBE drape and poke through cloth.
                        # Magnitude is slot-aware; see _slot_aware_inflation_magnitude.
                        _infl_mag = _slot_aware_inflation_magnitude(
                            biped_slots, shape=s)
                        if _infl_mag > 0 and body_verts_for_fit is not None:
                            try:
                                _morph_amp = _cached_body_morph_amplitude(
                                    _find_ube_body_osd(), body_normals_for_fit,
                                    len(body_verts_for_fit))
                                snapped = inflate_armor_outward(
                                    snapped, body_verts_for_fit,
                                    magnitude=_infl_mag,
                                    close_threshold=ARMOR_INFLATION_FALLOFF_DISTANCE,
                                    body_normals=body_normals_for_fit,
                                    morph_amplitude=_morph_amp,
                                    morph_max=ADAPTIVE_CLEARANCE_MORPH_MAX,
                                )
                            except Exception:
                                pass
                        # Groove-smooth: flatten warp-induced indent grooves on
                        # tight bust cloth. Near-body verts only; decorative shapes unaffected.
                        snapped = _smooth_warp_grooves(
                            sv_world, snapped, body_verts_for_fit)
                    else:
                        # Legacy fallback: no CBBE base body; push inside-body verts
                        # outward along UBE normals.
                        snapped = snap_armor_outside_body(
                            sv_world,
                            body_verts_for_fit,
                            body_normals_for_fit,
                        )
                    # Keep chain-bone cloth (skirt/belt/cape) at SOURCE position so
                    # it stays aligned with its chain bones; warping it onto UBE
                    # while bones stay at source breaks the SMP rest pose.
                    # Per-vertex (chain-weight fraction) so hybrid shapes still work.
                    if snapped is not None:
                        # source_verts must match snapped's frame (world).
                        snapped = _physics_chain_nowarp_blend(s, sv_world, snapped)
                except Exception:
                    snapped = None

                override_skin_p1 = None
                _body_bone_set = (
                    set(ube_base_for_reskin.bone_names or [])
                    if ube_base_for_reskin is not None else set()
                )
                if (ube_base_for_reskin is not None
                        and (s.bone_names or [])
                        and s.name not in RESKIN_SKIP_NAMES
                        and s.name not in hdt_softbody_names
                        and s.name not in hdt_collider_names
                        and not _shape_has_fine_animation_bones(s)
                        and not _shape_is_head_dominant(s)
                        and s.name not in layered_cloth_names
                        and not _shape_has_hdt_smp_rigging(s, _body_bone_set)):
                    try:
                        verts_for_reskin = (snapped if snapped is not None
                                            else sv_world)
                        # Slot-aware conformance band: body-fitted armor (slot 32+legs)
                        # uses a wider band so it bends WITH the body; skirts keep narrow.
                        _rn_p1, _rf_p1 = _slot_aware_reskin_band(biped_slots)
                        bones, xforms_map, weights_map = compute_body_blend_skinning(
                            verts_for_reskin, s, ube_base_for_reskin,
                            near_dist=_rn_p1, far_dist=_rf_p1,
                        )
                        # Add 3BA scale-bone weights so cloth follows body sliders.
                        # Cloth shapes carry no per-shape BODYTRI; scale bones are
                        # their ONLY runtime body-tracking layer. Skip exposed body-skin
                        # shapes (already blend==1 from M6; adding scale bones causes
                        # over-inflation vs the real body under a slider).
                        if (ADD_SCALE_BONES_TO_CLOTH
                                and not _is_exposed_body_skin_shape(
                                    sv_world, cbbe_verts_for_warp)):
                            bones, xforms_map, weights_map = add_scale_bone_weights(
                                bones, xforms_map, weights_map,
                                verts_for_reskin, ube_base_for_reskin,
                                reach=_slot_aware_scale_bone_reach(biped_slots),
                                torso_parity=bool(biped_slots & (
                                    BIPED_SLOT32_BIT | BIPED_SLOT49_BIT)),
                            )
                        if bones and weights_map:
                            override_skin_p1 = {
                                "bones": bones,
                                "xforms": xforms_map,
                                "weights": weights_map,
                            }
                    except Exception:
                        override_skin_p1 = None

                shape_jobs_p1.append({
                    "src": s,
                    # WORLD-frame verts; transformed back to skin in pass 2 via g2s.
                    "verts": (np.asarray(snapped, dtype=np.float64)
                              if snapped is not None else sv_world),
                    "override_skin": override_skin_p1,
                    "verts_modified": snapped is not None,
                    "g2s": _shape_g2s,
                })

            # Z-fight auto-offset.
            if shape_jobs_p1:
                try:
                    from scipy.spatial import cKDTree
                    zfight_map = {
                        j["src"].name: j["verts"] for j in shape_jobs_p1
                    }
                    zfight_offsets = detect_zfight_pairs(
                        zfight_map, body_verts_for_fit, body_normals_for_fit,
                    )
                    body_tree_p1 = cKDTree(body_verts_for_fit)
                    for j in shape_jobs_p1:
                        scalar = zfight_offsets.get(j["src"].name)
                        if scalar is None or not np.any(scalar != 0):
                            continue
                        verts = j["verts"]
                        _, idx = body_tree_p1.query(verts, k=1)
                        outward = body_normals_for_fit[idx]
                        j["verts"] = verts + scalar[:, None] * outward
                        j["verts_modified"] = True
                except Exception:
                    pass  # z-fight fix is best-effort

            # Cleavage depth separation — push inner-layer cloth verts
            # backward so they sit a clean clearance behind the outer
            # layer. Fixes static Z-fighting / mesh intersection visible
            # even at standstill (separate problem from the motion-time
            # weight-sync below). See _separate_chest_layered_cloth_depth.
            if shape_jobs_p1:
                try:
                    n_pushed = _separate_chest_layered_cloth_depth(
                        shape_jobs_p1,
                        body_verts=body_verts_for_fit,
                        body_normals=body_normals_for_fit,
                    )
                    if n_pushed:
                        import sys as _sys
                        print(f"  cleavage depth: pushed {n_pushed} inner-layer "
                              f"vert(s) back for clean separation",
                              file=_sys.stderr)
                    n_abdo = _separate_abdomen_layered_cloth_depth(
                        shape_jobs_p1,
                        body_verts=body_verts_for_fit,
                        body_normals=body_normals_for_fit,
                        cbbe_body_verts=cbbe_verts_for_warp,
                    )
                    if n_abdo:
                        import sys as _sys
                        print(f"  overlay-band lift: raised {n_abdo} band "
                              f"vert(s) back on top of their under-layer", file=_sys.stderr)
                except Exception:
                    pass  # best-effort

            # Layered-cloth weight sync: gated by breast-weight fraction so
            # it only touches genuine bust layers (not decorative attachments).
            # Keeps a bra and the fabric over it moving together under
            # breast-jiggle so they don't intersect in motion.
            if shape_jobs_p1:
                try:
                    n_synced = _sync_chest_layered_cloth_weights(shape_jobs_p1)
                    if n_synced:
                        import sys as _sys
                        print(f"  cleavage sync: matched {n_synced} bust-layer "
                              f"vert(s) to authority weights", file=_sys.stderr)
                    n_async = _sync_abdomen_layered_cloth_weights(shape_jobs_p1)
                    if n_async:
                        import sys as _sys
                        print(f"  waist jiggle sync: matched {n_async} inner-layer "
                              f"vert(s) to the outer layer", file=_sys.stderr)
                except Exception:
                    pass  # best-effort; failure leaves shapes as-is

            # Cross-plate seam weld: close gaps where adjacent solid plates
            # that share a seam drifted apart under independent warp. Runs
            # BEFORE the glow ride so the glow rides the welded plate.
            if shape_jobs_p1:
                try:
                    n_weld = _weld_cross_shape_seams(shape_jobs_p1)
                    if n_weld:
                        import sys as _sys
                        print(f"  seam weld: closed {n_weld} cross-plate seam "
                              f"vert(s)", file=_sys.stderr)
                except Exception:
                    pass  # best-effort; failure leaves seams as-is

            # Effect-shader decal overlays (Daedric red glow etc.) must RIDE
            # their underlying plate, not be warped independently -- else the
            # thin source offset amplifies through the body-fit and the glow
            # clips through the plate. Runs LAST so it rides the plate's FINAL
            # position. Frame-safe: identity-g2s only, so the WORLD-frame verts
            # here match. See _ride_effect_overlays_on_plate.
            if shape_jobs_p1:
                try:
                    n_ride = _ride_effect_overlays_on_plate(shape_jobs_p1)
                    if n_ride:
                        import sys as _sys
                        print(f"  glow overlay ride: re-bound {n_ride} "
                              f"effect-overlay vert(s) to their plate",
                              file=_sys.stderr)
                except Exception:
                    pass  # best-effort; failure leaves overlays as-is

            # PELVIS RE-ANCHOR (copy/fit path): a NIF-root-hung skirt (Anequina)
            # takes this path, not phase-2, so recreate its custom bone chains up
            # front -- lifting the root-parented garment bones onto Pelvis -- so the
            # shape copy's add_bone reuses those re-anchored nodes. Gated on the
            # pattern actually being present, so all other armors are byte-unchanged.
            if _has_nif_root_garment_chain(src_nif_for_fit):
                try:
                    _pc_bones: set = set()
                    for _ps in src_nif_for_fit.shapes:
                        _pc_bones |= set(_ps.bone_names or [])
                    _precreate_custom_bone_chains(
                        dst_nif_for_fit, src_nif_for_fit, list(_pc_bones))
                except Exception as _pe:
                    failed.append(("pelvis-reanchor", repr(_pe)))

            # Pass 2: actually copy. The fit ran in WORLD frame; transform each
            # shape's verts back to its own SKIN frame before writing (no-op for
            # identity global_to_skin, i.e. almost every shape).
            for j in shape_jobs_p1:
                s = j["src"]
                override_v = (
                    _verts_world_to_skin(
                        j["verts"], j.get("g2s")).astype(np.float32)
                    if j["verts_modified"] else None)
                try:
                    _copy_shape(s, dst_nif_for_fit,
                                override_verts=override_v,
                                override_skin=j["override_skin"])
                except Exception:
                    try:
                        _copy_shape(s, dst_nif_for_fit)
                    except Exception as _e2:
                        # BOTH copies failed -> the shape is absent from the
                        # output = an invisible piece in-game. Record it (tagged
                        # DROPPED) instead of swallowing, so the run reports a
                        # partial conversion rather than a clean success.
                        failed.append((s.name, f"DROPPED (copy failed): {_e2!r}"))
            # Inject UBE Hands/Feet to replace the CBBE-topology body-skin shapes.
            # Safe: slot 33/37 hides the actor's nude hands/feet; no z-fight.
            if extremity_slots_to_replace:
                weight_suf_for_inj = next(
                    (sx for sx in ("_0", "_1") if src_path.stem.endswith(sx)),
                    "_1",
                )
                inject_log: list[str] = []
                for slot_label in set(extremity_slots_to_replace):
                    _inject_ube_extremity_replacement(
                        dst_nif_for_fit, weight_suf_for_inj,
                        slot_label, inject_log,
                    )
            atomic_nif_save(dst_nif_for_fit, dst_nif_for_fit.filepath)

        # Hand/foot slots are rigid — never cloth. HDT-SMP on gauntlets/boots
        # collapses them at runtime even though the static mesh looks fine.
        # Gold-standard UBE gauntlets carry no HDT XML; enforce the same here.
        if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
            hdt_xml = None
        else:
            hdt_xml = _find_hdt_xml_for_armor(src_path)
            # If source XML references chain bones our conversion stripped, the HDT
            # cloth goes dead. Regenerate a fresh soft-body XML on standard body bones.
            if hdt_xml is not None:
                try:
                    _pyn = _pynifly()
                    _nfchk = _pyn.NifFile(filepath=str(dst_path))
                    _dstbones: set[str] = set()
                    for _s in _nfchk.shapes:
                        _dstbones |= set(_s.bone_names or [])
                    try:
                        _dstbones |= set(_nfchk.nodes.keys())
                    except Exception:
                        pass
                    if _source_hdt_needs_missing_chain_bones(src_path, _dstbones):
                        _regen = _generate_hdt_xml_for_dst(dst_path)
                        if _regen:
                            hdt_xml = _regen
                except Exception:
                    pass
            # No source HDT XML: generate a minimal one. Returns None only if the
            # NIF has no cloth carriers. Slot-49 cloth gets a cloth-only XML that
            # collides with the actor body's "body" tag at runtime.
            if hdt_xml is None:
                hdt_xml = _generate_hdt_xml_for_dst(dst_path)

        # Figure out armor-specific TRI path (same logic as phase 2).
        armor_relpath = None
        try:
            parts = src_path.parts
            for marker in ("meshes", "Meshes"):
                if marker in parts:
                    i = parts.index(marker)
                    armor_relpath = Path(*parts[i + 1:])
                    break
        except Exception:
            pass
        bodytri_path = None
        auto_tri_dst_phase1: Path | None = None
        # Carrier shape name is set during BODYTRI injection below and passed to
        # generate_armor_tri so the TRI lists the carrier first.
        carrier_name_for_tri: str | None = None
        # Hands/feet slots get BODYTRI + auto-TRI: extremity verts are damped via
        # _extremity_vert_fraction so fingers/toes don't distort under body morphs.
        # HDT cloth stays gated off for hand/foot (rigid; no HDT in source) above.
        if armor_relpath is not None and ube_body_ref_path is not None:
            # Always auto-generate the BODYTRI / TRI from CBBE source +
            # UBE target shape — see module-level note above the
            # UBE_BODY_TRI_PATH constant.
            tri_stem = dst_path.stem
            for suf in ("_0", "_1"):
                if tri_stem.endswith(suf):
                    tri_stem = tri_stem[:-len(suf)]; break
            auto_tri_dst_phase1 = dst_path.parent / (tri_stem + ".tri")
            dst_parts = auto_tri_dst_phase1.parts
            for i, seg in enumerate(dst_parts):
                if seg.lower() == "meshes":
                    bodytri_path = "\\".join(dst_parts[i + 1:])
                    break

        _inject_err = None
        if hdt_xml or bodytri_path:
            try:
                pynifly = _pynifly()
                nf = pynifly.NifFile(filepath=str(dst_path))

                # HDT XML on root.
                if hdt_xml:
                    already_has = False
                    for ed in nf.rootNode.extra_data():
                        if hasattr(ed, "string_data") and ed.name == "HDT Skinned Mesh Physics Object":
                            already_has = True
                            break
                    if not already_has:
                        from pyn.pynifly import NiStringExtraData  # type: ignore
                        NiStringExtraData.New(
                            nf,
                            name="HDT Skinned Mesh Physics Object",
                            string_value=hdt_xml,
                            parent=nf.rootNode,
                        )

                # BODYTRI on a SINGLE cloth carrier: NioOverride reads only the first
                # BODYTRI in a NIF, so putting it on every shape shifts the carrier to
                # whatever textured shape iterates first and the real cloth stops
                # morphing. Rigid single-bone pieces still morph via the M6 reskin's
                # bone-driven skinning.  [DESIGN: BODYTRI / body-morph generation]
                if bodytri_path:
                    from pyn.pynifly import NiStringExtraData  # type: ignore
                    # Single-carrier BODYTRI matching hand-authored
                    # UBE convention. See `_pick_bodytri_carriers`.
                    carriers = _pick_bodytri_carriers(nf)
                    carrier_name_for_tri = carriers[0].name if carriers else None
                    # Apply morph-readiness cleanup to ALL cloth shapes
                    # — not just the carrier. See Phase 2 equivalent
                    # block for the full rationale.
                    cleanup_shapes = list(carriers)
                    carrier_names = {c.name for c in carriers}
                    for s in nf.shapes:
                        nlow = s.name.lower()
                        if s.name in carrier_names:
                            continue
                        if not (s.textures or {}):
                            continue
                        if s.name in UBE_BODY_INJECT_NAMES:
                            continue
                        if any(kw in nlow for kw in NON_CLOTH_SHAPE_KEYWORDS):
                            continue
                        cleanup_shapes.append(s)
                    # HDT-SMP per-triangle COLLIDER shapes must keep their authored
                    # partitions: collapsing them desyncs FSMP's collision build ->
                    # equip CTD (the `Greaves` 32+38 -> 32 case). CRITICAL here: this
                    # phase-1 `nf` was RELOADED from disk above, so partition_tris is
                    # live and `_normalize_partitions` actually COLLAPSES the collider
                    # -- BEFORE _normalize_partitions_on_disk runs, so its skip is too
                    # late. The skip has to be in this inline pass. (#elven Greaves.)
                    _coll_names_p1 = _hdt_collider_shape_names(src_path)
                    for s in cleanup_shapes:
                        _reset_morph_flags(s)
                        _normalize_shader_for_morph(s)
                        # Alpha block KEPT (settled rule): the bit-19
                        # alpha-sorter flag set by _reset_morph_flags is
                        # what unblocks morphing on alpha shapes — NOT
                        # stripping alpha. Stripping (a) destroyed cutout
                        # transparency on cloth and (b) persisted only
                        # partially, corrupting the atlas opaque-diffuse
                        # detection (cut-out tiles wrongly forced opaque).
                        if s.name not in _coll_names_p1:
                            _normalize_partitions(s)

                    # BODYTRI goes on the carrier only.
                    for target_shape in carriers:
                        already_has = False
                        for ed in target_shape.extra_data():
                            if hasattr(ed, "string_data") and ed.name == "BODYTRI":
                                already_has = True
                                break
                        if not already_has:
                            NiStringExtraData.New(
                                nf,
                                name="BODYTRI",
                                string_value=bodytri_path,
                                parent=target_shape,
                            )

                # Disable VirtualBody rendering on Phase 1 NIFs too —
                # source CBBE NIFs sometimes ship a VirtualBody we
                # copied through verbatim. Same blue-double artifact.
                _hide_virtual_body(nf)

                atomic_nif_save(nf, dst_path)
            except Exception as _e:
                # Best-effort, but surface it: a swallowed failure here means no
                # cloth physics (HDT) and/or no body-morph (BODYTRI), no signal.
                _inject_err = _e

        # M8 phase-1 auto-TRI generation. Runs after NIF copy + BODYTRI
        # injection. Loads the destination NIF, reads armor-shape verts,
        # propagates UBE body OSD deltas via K-NN, writes the TRI at
        # auto_tri_dst_phase1. Skipped when a user-built BodySlide TRI
        # was found above (auto_tri_dst_phase1 is None in that case).
        if auto_tri_dst_phase1 is not None:
            try:
                ube_osd_path = _find_ube_body_osd()
                if ube_osd_path is not None and ube_body_ref_path is not None:
                    from .sliderset_gen import generate_armor_tri
                    body_osd = _cached_osd_load(ube_osd_path)
                    _, body_verts_arr, _body_normals = _cached_ube_body_verts(
                        Path(ube_body_ref_path))
                    if body_verts_arr is not None:
                        pyn = _pynifly()
                        dst_check = pyn.NifFile(filepath=str(dst_path))
                        armor_shape_verts: dict[str, np.ndarray] = {}
                        body_in_dst: set[str] = set()
                        # Per-vert extremity fractions: finger/toe verts get ~0 morph;
                        # forearm/calf verts get full morph. Handles long sleeves/boots
                        # that span both regions without deforming digits.
                        armor_vert_ef: dict[str, np.ndarray] = {}
                        for s in dst_check.shapes:
                            if s.name in UBE_BODY_INJECT_NAMES:
                                body_in_dst.add(s.name)
                                continue
                            # Include every armor shape; per-vert extremity fraction
                            # scales morph delta by (1-frac) so digits get ~0 morph.
                            # Body-space offset applied so morph KNN matches the right
                            # body region for shapes with non-identity transforms.
                            armor_shape_verts[s.name] = (
                                np.asarray(s.verts, dtype=np.float64)
                                + shape_body_offset(s))
                            ef = _extremity_vert_fraction(s, len(s.verts))
                            if ef is not None and ef.size:
                                armor_vert_ef[s.name] = ef
                        # Unified TRI: include a BaseShape entry so the
                        # single cloth-carrier BODYTRI delivers body
                        # morphs to the injected BaseShape too.
                        # NioOverride only honors one BODYTRI per NIF,
                        # so we can't put a separate hook on BaseShape;
                        # the per-armor TRI must carry both.
                        tri = generate_armor_tri(
                            armor_shape_verts,
                            body_verts_arr,
                            body_osd,
                            body_shape_name="BaseShape",
                            include_body_shapes=body_in_dst,
                            carrier_shape_name=carrier_name_for_tri,
                            armor_vert_extremity_fractions=armor_vert_ef,
                        )
                        atomic_tri_save(tri, auto_tri_dst_phase1)
            except Exception as _e_tri:
                # The armor still RENDERS without a TRI, but it won't follow
                # body-morph sliders (static on every OBody preset). Surface it
                # rather than swallow, so "armor doesn't conform" is visible.
                failed.append(("auto-TRI", f"body-morph unavailable: {_e_tri!r}"))

        # Unconditional VirtualBody-hide: the verbatim copy path skips the
        # HDT/BODYTRI block so source-inherited VirtualBody shapes need a second
        # chance here. No-op when VirtualBody isn't present.
        try:
            pyn_for_vb = _pynifly()
            nf_for_vb = pyn_for_vb.NifFile(filepath=str(dst_path))
            if _hide_virtual_body(nf_for_vb):
                atomic_nif_save(nf_for_vb, dst_path)
        except Exception:
            pass  # best-effort; doesn't break the conversion

        # Multi-partition collapse — see _normalize_partitions_on_disk.
        _normalize_partitions_on_disk(dst_path, src_path)

        # FINAL HDT-SMP physics pass — must run LAST so extra-data survives
        # earlier round-trips. Skip hand/foot: cloth physics collapses them.
        if not (biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
            try:
                _finalize_hdt_physics(dst_path, src_path)
            except Exception:
                pass

        # Graft body jiggle onto fitted leg cloth that lacks its own, THEN conform.
        # ORDER MATTERS: the graft gives a no-jiggle pant the body's butt/belly
        # jiggle, which lets the conform pass (gated on jiggle) ALSO weight-match it
        # to the body -- fixing the knee-BEND clip, not just butt-jiggle follow.
        try:
            _transfer_body_jiggle_to_fitted(dst_path, biped_slots)
        except Exception:
            pass
        # Fitted-cloth body conform (gated; skin-tight garments only). Runs after
        # finalize so it sees final skinning; reauthor/harden below preserve it.
        try:
            _conform_fitted_to_body(dst_path, biped_slots)
        except Exception:
            pass
        # Knee-bend conform for RIGID leg plate (the conform above skips it): match
        # the plate's Thigh:Calf split to the body so it bends with the knee.
        try:
            _match_rigid_leg_bend_to_body(dst_path, biped_slots)
        except Exception:
            pass

        # Verbatim-copied NIFs carry raw block structure the renderer can reject.
        # Re-author for a clean pynifly structure identical to body shapes.
        if not use_rebuild:
            try:
                _reauthor_nif_fresh(dst_path)
            except Exception:
                pass

        # Param-hardening LAST (gated, idempotent). Do NOT re-run
        # _harden_hdt_xml_for_fsmp here — re-pruning copy-path XML caused
        # cuirass regressions; only the in-_finalize prune runs.
        if HARDEN_AUTHORED_PHYSICS or STATIC_CHAINS:
            try:
                _hp_stem = dst_path.stem
                for _hp_suf in ("_0", "_1"):
                    if _hp_stem.endswith(_hp_suf):
                        _hp_stem = _hp_stem[:-len(_hp_suf)]
                        break
                _hp_xml = dst_path.parent / (_hp_stem + ".xml")
                if HARDEN_AUTHORED_PHYSICS:
                    _harden_physics_params(_hp_xml)
                if STATIC_CHAINS:
                    _make_chains_static(_hp_xml)
            except Exception:
                pass

        # Validation pass — catches subtle skinning / TRI mismatches
        # that would only show up as spikes / missing morphs in-game.
        val_warnings = validate_dst_nif(
            dst_path,
            tri_path=auto_tri_dst_phase1 if auto_tri_dst_phase1 else None,
            src_path=src_path,
        )

        reason_parts: list[str] = []
        if failed:
            reason_parts.append(
                "shape op failures: "
                + ", ".join(f"{n} ({err})" for n, err in failed))
        if val_warnings:
            reason_parts.extend(val_warnings)
        if _inject_err is not None:
            reason_parts.append(
                f"HDT/BODYTRI injection failed ({_inject_err!r}) -- piece may "
                "lack cloth physics / body-morph")

        # Heeled boot: re-inject HH_OFFSET as the VERY LAST write — every pynifly
        # save drops NiFloatExtraData. If transplant fails, fall back to original
        # source mesh so the boot at least has a working heel (CBBE-shaped).
        if _hh_transplant_value is not None:
            from . import hh_offset
            if hh_offset.transplant_hh_offset(dst_path, _hh_transplant_value):
                reason_parts.append(
                    f"heel HH_OFFSET={_hh_transplant_value:.3g} transplanted")
            else:
                try:
                    atomic_copy(src_path, dst_path)
                    reason_parts.append("heel transplant unsafe — used original mesh")
                except OSError:
                    pass

        return ConvertResult(
            src_path=src_path,
            dst_path=dst_path,
            status="converted (copy)",
            reason="; ".join(reason_parts),
            armor_shapes=armor_names,
            dropped_shapes=[n for (n, msg) in failed
                            if msg.startswith("DROPPED")],
        )

    # Experimental position-warp path
    if cbbe_index is None:
        if cbbe_ref_path is None:
            raise ValueError("warp_armor=True requires cbbe_ref_path or cbbe_index")
        cbbe_index = _load_body_mesh(Path(cbbe_ref_path))
    if ube_index is None:
        if ube_ref_path is None:
            raise ValueError("warp_armor=True requires ube_ref_path or ube_index")
        ube_index = _load_body_mesh(Path(ube_ref_path))

    shapes_to_patch: list[tuple[str, np.ndarray]] = []
    for s in nif.shapes:
        if _looks_like_inline_body(s):
            continue
        displacement = compute_deformation(s.verts, cbbe_index, ube_index)
        new_verts = (s.verts + displacement).astype(np.float32)
        shapes_to_patch.append((s.name, new_verts))

    def _vert_provider(path: Path):
        cur = nif_io.load_nif(path)
        return {s.name: [tuple(v) for v in s.verts.tolist()] for s in cur.shapes}

    located = nif_patch.patch_nif_shapes(
        src_path, dst_path,
        shapes_to_patch=shapes_to_patch,
        locator_loader=_vert_provider,
    )

    return ConvertResult(
        src_path=src_path,
        dst_path=dst_path,
        status="converted (warped)",
        armor_shapes=armor_names,
        shape_locations=located,
    )


# ---------- Armor body-fit (per-vertex snap to swapped body) -----------

def fit_armor_to_ube_body(
    verts: np.ndarray,
    cbbe_index: MeshIndex,
    ube_index: MeshIndex,
    *,
    close_threshold: float = 2.0,
    full_threshold: float = 0.5,
) -> np.ndarray:
    """Snap body-hugging armor verts to the UBE body surface.

    For each vertex:
      * project to nearest CBBE body surface point  (cbbe_proj)
      * find the corresponding UBE body point       (ube_proj_at_cbbe)
      * shift vert by (ube_proj - cbbe_proj), weighted by how close the
        vert is to the body surface (close = full shift, far = no shift)

    Threshold defaults: verts within 0.5 units of the body get full
    translation; verts beyond 2.0 stay put; linear blend between. Tuned
    for skin-tight pieces (a soft-body cloth shape sits ~0 from breast surface) without
    disturbing accessories sitting >2 units out (pauldrons, fur collars).

    Uses translation-only (no surface-frame rotation) — for verts this
    close, frame rotation adds more error than it fixes (validated in M3
    measurements: see docs/M3_findings.md).
    """
    verts = np.asarray(verts, dtype=np.float64)
    if len(verts) == 0:
        return verts.astype(np.float32)
    from .correspondence import project_to_mesh
    # Project the original vert directly onto each body's surface. Both
    # projections find the breast/chest/hip surface closest to the vert
    # (which sits outside both bodies). The difference is the local body
    # shape delta. Earlier version projected cbbe_proj onto UBE — wrong:
    # if UBE breast extends past cbbe_proj, ube projection of cbbe_proj
    # could be a different surface (back/side), reversing the shift.
    cbbe_proj, _, _ = project_to_mesh(verts, cbbe_index)
    ube_proj, _, _ = project_to_mesh(verts, ube_index)
    body_shift = ube_proj - cbbe_proj

    dists = np.linalg.norm(verts - cbbe_proj, axis=1)
    band = max(close_threshold - full_threshold, 1e-6)
    weights = np.clip((close_threshold - dists) / band, 0.0, 1.0)[:, None]
    return (verts + body_shift * weights).astype(np.float32)


# ---------- M3 phase 2.5: inflate armor verts away from body ------------

def snap_armor_outside_body(
    armor_verts: np.ndarray,
    body_verts: np.ndarray,
    body_normals: np.ndarray,
    *,
    offset: float = 0.2,
    apply_threshold: float = -0.2,
    iterations: int = 8,
    max_inside_depth: float = 0.6,
) -> np.ndarray:
    """Push armor verts that are INSIDE the body outward to sit at
    `offset` units above the body surface along the body's outward
    normal.

    Why this is needed: CBBE-authored armor verts are positioned to
    fit a CBBE-shaped body. When we put that armor on a (typically
    larger) UBE body, some armor verts end up INSIDE the UBE body
    surface — the UBE body skin pokes through the armor where the
    armor is meant to cover. The body's vertex normals give us a
    reliable "outward" direction even when the armor vert sits
    inside the body (where `armor - nearest_body` direction can
    point further inward).

    `apply_threshold` (default -0.2): signed distance below which a
    vert gets pushed. Only meaningfully-inside-body verts move
    (≥ 0.2 deep). Verts that sit barely inside (e.g. signed=-0.05
    on an inner sock layer) or anywhere outside the surface are
    left alone. This preserves layered armor structure: inner cloth
    layer at -0.05 standoff, outer leather at +0.3 standoff — both
    untouched, no layer-flipping. Threshold of 0.0 (push anything
    inside) was too aggressive and caused inner socks/loincloths to
    jump past their outer shorts layers because the snap target
    (+offset) overshot the outer layer's standoff. Tune to 0 or
    higher to enforce a minimum standoff at the cost of layer
    collapse; tune more negative to ignore minor body interpenetration.

    Iteration: after moving an armor vert outward, its NEW nearest
    body vert may differ from the pre-move nearest, so a single
    snap can leave the vert short of `offset`. We loop the snap up
    to `iterations` times until no verts need to move.

    `body_normals`: per-vertex unit normals aligned with
    `body_verts`. pynifly exposes these as `shape.normals`.
    """

    armor_verts = np.asarray(armor_verts, dtype=np.float64).copy()
    body_verts = np.asarray(body_verts, dtype=np.float64)
    body_normals = np.asarray(body_normals, dtype=np.float64)

    from scipy.spatial import cKDTree
    tree = cKDTree(body_verts)

    # Direction-choice for the push. K=4 IDW-smoothed body normals are stable in convex
    # regions but collapse in concave ones: between the legs the left- and right-outward
    # normals average to FORWARD, merging both inner thighs into a fake "skirt". Hybrid:
    # use the smoothed normal where it agrees with the K=1 nearest (< ~30deg), else fall
    # back to nearest so each leg tracks its own normal.
    SMOOTH_K = 4
    DISAGREE_COS_THRESHOLD = 0.866  # cos(30 deg) ~ 0.866
    for _ in range(max(1, iterations)):
        d, idx = tree.query(armor_verts, k=SMOOTH_K)
        if SMOOTH_K == 1:
            d = d[:, None]; idx = idx[:, None]
        # K=1 nearest neighbor — used as the fallback in concave regions.
        nearest_body_pts = body_verts[idx[:, 0]]
        nearest_body_nrm = body_normals[idx[:, 0]]

        # K=4 IDW averaging — smoother direction in convex regions.
        w = 1.0 / (d + 1e-6)
        w /= w.sum(axis=1, keepdims=True)
        smooth_body_pts = (body_verts[idx] * w[..., None]).sum(axis=1)
        smooth_nrm = (body_normals[idx] * w[..., None]).sum(axis=1)
        sn_len = np.linalg.norm(smooth_nrm, axis=1, keepdims=True)
        sn_len[sn_len < 1e-9] = 1.0
        smooth_body_nrm = smooth_nrm / sn_len

        # Per-vert: use smoothed unless smoothed disagrees with nearest.
        agree_cos = (nearest_body_nrm * smooth_body_nrm).sum(axis=1)
        use_smooth = agree_cos > DISAGREE_COS_THRESHOLD
        body_pts = np.where(
            use_smooth[:, None], smooth_body_pts, nearest_body_pts)
        body_nrm = np.where(
            use_smooth[:, None], smooth_body_nrm, nearest_body_nrm)

        # Signed distance from armor vert to surface point along normal.
        rel = armor_verts - body_pts
        signed = (rel * body_nrm).sum(axis=1)
        # Only push MARGINALLY inside verts. Verts deeper than max_inside_depth (0.6)
        # are likely intentional (tight wrapping designed inside the envelope) or hidden
        # by outer layers; pushing them out creates large displacement that tears tight
        # armor. Tradeoff: minor poke-through past 0.6u, invisible if the armor is opaque.
        need_push = (signed < apply_threshold) & (signed > -max_inside_depth)
        if not need_push.any():
            break
        armor_verts[need_push] = (body_pts[need_push] +
                                  body_nrm[need_push] * offset)

    return armor_verts.astype(np.float32)


def inflate_armor_outward(
    armor_verts: np.ndarray,
    body_verts: np.ndarray,
    *,
    magnitude: float = 0.5,
    close_threshold: float = 2.0,
    body_normals: "np.ndarray | None" = None,
    morph_amplitude: "np.ndarray | None" = None,
    base_magnitude: float = ADAPTIVE_CLEARANCE_BASE,
    morph_factor: float = ADAPTIVE_CLEARANCE_MORPH_FACTOR,
    morph_max: float = ADAPTIVE_CLEARANCE_MORPH_MAX,
) -> np.ndarray:
    """Push body-hugging armor verts outward to avoid z-fighting with a
    morphed body.

    For each armor vertex:
      - find nearest body vert
      - direction = that body vert's OUTWARD NORMAL (smooth) when
        `body_normals` is supplied, else (armor_vert - body_vert) normalized
      - push armor vert along that direction by `magnitude` * falloff

    Why the body normal matters: when armor hugs the body tightly (a rigid
    gold corset), `armor_vert - body_vert` is a near-zero vector and
    normalizing it amplifies floating-point noise into a RANDOM per-vert
    direction — so a uniform-magnitude inflation pushes adjacent verts in
    wildly different directions and crumples a contiguous surface into
    "crushed foil". The body's own vertex normal varies smoothly across the
    surface, so pushing along it gives a smooth outward shell offset (the
    clearance we want) with no crumpling. The (armor-body) fallback is kept
    only for callers that have no body normals.

    Falloff: full push within 0 of body, zero push at `close_threshold`.
    Linear blend between. Verts further than `close_threshold` aren't moved.

    Use case: fabric/cloth pieces skinned to CBBE breast bones sit
    statically on the UBE body. When the body morphs at runtime (via
    RaceMenu/NioOverride applied to BaseShape only), the fabric stays
    put and the body pokes through. A small outward inflation (~0.5)
    creates clearance so the morphed body doesn't z-fight with the fabric.
    """
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    body_verts = np.asarray(body_verts, dtype=np.float64)
    # Empty input would make cKDTree / idxs.max() raise (caught by callers, but
    # the shape then silently loses its clearance pass). Match the guard the
    # sibling clearance functions already have.
    if len(armor_verts) == 0 or len(body_verts) == 0:
        return np.asarray(armor_verts, dtype=np.float32)

    from scipy.spatial import cKDTree
    tree = cKDTree(body_verts)
    dists, idxs = tree.query(armor_verts, k=1)

    if body_normals is not None:
        # Smooth outward direction = nearest body vert's normal. This is the
        # crumple fix — see the docstring.
        bn = np.asarray(body_normals, dtype=np.float64)[idxs]
        bnn = np.linalg.norm(bn, axis=1, keepdims=True)
        directions_unit = bn / np.where(bnn > 1e-6, bnn, 1.0)
    else:
        # Fallback: direction from body to armor (outward). Noise-prone for
        # body-hugging verts — only used when no normals are available.
        directions = armor_verts - body_verts[idxs]
        norms = np.linalg.norm(directions, axis=1, keepdims=True)
        safe_norms = np.where(norms > 1e-6, norms, 1.0)
        directions_unit = directions / safe_norms

    # Per-vert magnitude. Adaptive (morph-aware) when a morph_amplitude map is
    # supplied AND enabled: clearance = clip(base + factor*outward_morph, base,
    # cap) where cap = max(slot magnitude, morph_max). STATIC zones sit at `base`
    # (closer to the skin than the old uniform `magnitude`); MORPH zones
    # (breast/butt/belly) are allowed to climb ABOVE the slot magnitude up to
    # `morph_max`, because that's where the body grows outward at runtime and a
    # too-tight cuirass clips (the guard-cuirass nipple/stomach poke-through).
    # The cap only binds high-morph verts, so static-zone tightening is
    # unchanged. Falls back to uniform `magnitude` when no morph map.
    if (ADAPTIVE_CLEARANCE_ENABLED and morph_amplitude is not None
            and len(morph_amplitude) > idxs.max()):
        amp_at = np.asarray(morph_amplitude, dtype=np.float64)[idxs]
        cap = max(float(magnitude), float(morph_max))
        per_vert_mag = np.clip(
            base_magnitude + morph_factor * amp_at, base_magnitude, cap)
    else:
        per_vert_mag = np.full(len(armor_verts), float(magnitude))

    # Linear falloff: full magnitude at body, zero at close_threshold
    falloff = np.clip((close_threshold - dists) / close_threshold, 0.0, 1.0)
    push = directions_unit * (per_vert_mag * falloff)[:, None]

    return (armor_verts + push).astype(np.float32)


# ---------- Feminize male-only armor on the female UBE body ---------------
#
# NOT YET WIRED (FEMINIZE_MALE_ARMOR=False). Pulls male-mesh armor inward to
# hug the female contour in breast/butt/belly/waist zones, using the body's
# outward morph amplitude as zone weight. Only reduces clearance (can't
# add poke-through); never below FEMINIZE_TARGET_STANDOFF. Apply AFTER
# normal warp/inflate/snap, for male meshes only.
FEMINIZE_MALE_ARMOR = False          # master switch — keep OFF until wired+reviewed
FEMINIZE_TARGET_STANDOFF = 0.7       # hug clearance the bust/curve conforms to
FEMINIZE_AMP_THRESHOLD = 1.0         # morph amplitude where conform starts ramping
FEMINIZE_AMP_FULL = 3.0              # amplitude at which conform is at full strength
FEMINIZE_MAX_PULL = 5.0              # cap inward move per vert (anti-tear safety)
FEMINIZE_FAR_GATE = 8.0              # don't conform verts farther than this (loose drape)
FEMINIZE_SMOOTH_ITERS = 3           # Laplacian smoothing of the pull field (anti-stretch)


def feminize_male_armor_conform(
    armor_verts: np.ndarray,
    body_verts: np.ndarray,
    body_normals: np.ndarray,
    morph_amplitude: "np.ndarray | None",
    *,
    target_standoff: float = FEMINIZE_TARGET_STANDOFF,
    amp_threshold: float = FEMINIZE_AMP_THRESHOLD,
    amp_full: float = FEMINIZE_AMP_FULL,
    max_pull: float = FEMINIZE_MAX_PULL,
    far_gate: float = FEMINIZE_FAR_GATE,
    rigid_scale: float = 1.0,
    tris: "np.ndarray | None" = None,
    smooth_iters: int = FEMINIZE_SMOOTH_ITERS,
) -> np.ndarray:
    """Pull male-armor verts IN to hug the female body in the feminine zones.

    For each armor vert: signed clearance to nearest body surface (outward+);
    zone weight = ramp of the body's outward morph amplitude (0 in static zones,
    1 in breast/butt/belly). Move the vert inward by
    `zone_weight * rigid_scale * (clearance - target_standoff)`, clamped so it
    never goes below `target_standoff` (no new penetration) and never more than
    `max_pull` (anti-tear). Verts farther than `far_gate` (loose drape) are left.

    `rigid_scale` (0..1) softens the pull for rigid pieces so plates don't crush;
    pass < 1 for `_is_rigid_attachment` shapes. Returns float32 verts.
    """
    from scipy.spatial import cKDTree
    av = np.asarray(armor_verts, dtype=np.float64)
    bv = np.asarray(body_verts, dtype=np.float64)
    bn = np.asarray(body_normals, dtype=np.float64)
    if morph_amplitude is None or len(av) == 0:
        return av.astype(np.float32)
    tree = cKDTree(bv)
    d, idx = tree.query(av, k=1)
    if idx.max() >= len(morph_amplitude):
        return av.astype(np.float32)
    nrm = bn[idx]
    signed = ((av - bv[idx]) * nrm).sum(axis=1)            # clearance, outward +
    amp = np.asarray(morph_amplitude, dtype=np.float64)[idx]
    zone = np.clip((amp - amp_threshold) / max(amp_full - amp_threshold, 1e-6),
                   0.0, 1.0)
    excess = np.maximum(signed - target_standoff, 0.0)     # how much slab to remove
    near = signed < far_gate                               # skip loose drape
    pull = np.where(near, np.minimum(zone * rigid_scale * excess, max_pull), 0.0)

    # Smooth the pull magnitude across mesh neighbours so the bust bulge blends
    # into the surrounding panel instead of pinching at the zone boundary (the
    # edge-stretch source). Pure relaxation of a scalar field — never increases
    # any pull, so it can't create poke-through.
    if tris is not None and smooth_iters > 0 and len(av) > 3:
        t = np.asarray(tris, dtype=np.int64)
        if t.size:
            nbr_sum = np.zeros(len(av), dtype=np.float64)
            nbr_cnt = np.zeros(len(av), dtype=np.float64)
            e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
            for _ in range(int(smooth_iters)):
                nbr_sum[:] = 0.0
                nbr_cnt[:] = 0.0
                np.add.at(nbr_sum, e[:, 0], pull[e[:, 1]])
                np.add.at(nbr_sum, e[:, 1], pull[e[:, 0]])
                np.add.at(nbr_cnt, e[:, 0], 1.0)
                np.add.at(nbr_cnt, e[:, 1], 1.0)
                avg = np.where(nbr_cnt > 0, nbr_sum / np.maximum(nbr_cnt, 1), pull)
                pull = 0.5 * pull + 0.5 * avg   # gentle relaxation

    return (av - pull[:, None] * nrm).astype(np.float32)


# ---------- M3 phase 2.5: bake user's UBE preset into armor verts -------

def bake_preset_into_armor(
    armor_verts: np.ndarray,
    ube_template_body_verts: np.ndarray,
    user_preset_body_verts: np.ndarray,
    *,
    k: int = 4,
    close_threshold: float = 5.0,
) -> np.ndarray:
    """Apply the user's UBE BodySlide preset to armor verts via K-nearest
    body-vertex weighted delta propagation.

    Math:
        body_delta = user_preset - template      (per body vert)
        for each armor vert:
            find K nearest body verts
            weighted average of their deltas (inverse distance)
            apply to armor vert

    Same topology (29298 UBE verts) on both sides — just positional delta.
    Equivalent to what BodySlide does when "building" an outfit at the
    user's preset values: it propagates body slider deltas to armor verts.

    `close_threshold`: armor verts further than this from the body are
    NOT morphed (they're loose armor pieces, e.g. fur collars, pauldrons
    that hang in the air — morphing them with body delta would warp them
    incorrectly).
    """
    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    template = np.asarray(ube_template_body_verts, dtype=np.float64)
    preset = np.asarray(user_preset_body_verts, dtype=np.float64)
    if template.shape != preset.shape:
        raise ValueError(
            f"template body and preset body must have same vert count: "
            f"{template.shape} vs {preset.shape}"
        )

    body_delta = preset - template
    from scipy.spatial import cKDTree
    tree = cKDTree(template)
    dists, idxs = tree.query(armor_verts, k=k)
    if k == 1:
        dists = dists[:, None]; idxs = idxs[:, None]

    # Inverse-distance weights, normalized per armor vert
    weights = 1.0 / (dists + 1e-6)
    weights /= weights.sum(axis=1, keepdims=True)

    # Weighted sum of body deltas
    propagated_delta = (body_delta[idxs] * weights[..., None]).sum(axis=1)

    # Zero out the delta for armor verts that are too far from the body
    # (avoid morphing loose accessories — they shouldn't follow body shape)
    dist_to_body = dists[:, 0]  # nearest body vert distance
    far_mask = dist_to_body > close_threshold
    propagated_delta[far_mask] = 0.0

    return (armor_verts + propagated_delta).astype(np.float32)


# ---------- M3 phase 2: body swap via pynifly deep-copy -----------------

# Names that we'll try to pull from the UBE reference NIF when injecting
# bodies. BaseShape is the visible UBE body; VirtualBody is the SMP
# collision proxy (recommended but not strictly required for visuals).
UBE_BODY_INJECT_NAMES = ("BaseShape", "VirtualBody")



# BODYTRI target. RaceMenu only morphs an armor's BaseShape if the BODYTRI points at an
# armor-specific TRI with outfit-bridge slider names -- the standalone body TRI has only
# body-slider names that don't bridge to armor. The converter auto-generates a per-armor
# TRI; this constant is only the legacy fallback when auto-gen can't derive a path.
# [DESIGN: BODYTRI / body-morph generation]
UBE_BODY_TRI_PATH = r"!UBE\Body\femalebody_tangent.tri"  # legacy fallback only


# Shapes that NEVER carry BODYTRI even if a keyword matches their
# name. VirtualBody / VirtualGround / BaseShape are pynifly-injected
# placeholders that host bone weights and physics but have no textures
# tied to the user's actor body — morphing them at runtime is
# pointless, and naming them as carrier would steal the slot away
# from real cloth shapes.
BODYTRI_CARRIER_EXCLUDE = frozenset({
    "VirtualBody", "VirtualGround", "BaseShape",
})


# Substrings (lowercased) that mark a shape as a RIGID PROP / WEAPON /
# small accessory — pieces that shouldn't morph with body sliders
# because they're not anatomy-tracking. Excluding them from multi-
# BODYTRI injection keeps the morph scope narrow to cloth that
# actually drapes over the body.
NON_CLOTH_SHAPE_KEYWORDS = (
    "dagger", "scabbard", "sword", "bow", "arrow", "quiver",
    "shield", "pouch", "amulet", "ring", "necklace", "chain",
    "circlet", "crown", "earring", "nail", "gem", "stud",
    "buckle", "rivet", "clasp", "metal", "pauldron", "shoulder",
)


# NiAVObject flags BodySlide-built UBE armor sets on morphable shapes: 0xE (bits 1/2/3,
# "SelectiveUpdate") plus bit 19 (0x80000, the alpha-sorter). NioOverride refuses to
# morph an alpha shape without bit 19, so the converter sets 0x8000E uniformly (bit 19
# is harmless on opaque shapes).  [DESIGN: BODYTRI / body-morph generation]
BODYTRI_SHAPE_FLAGS_OPAQUE = 0x0000E   # bits 1, 2, 3 only — alpha-less cloth
BODYTRI_SHAPE_FLAGS_ALPHA  = 0x8000E   # add bit 19 (alpha-sorter) when alpha is on


def _reset_morph_flags(shape) -> None:
    """Set shape.flags to match the hand-built UBE convention:

      * alpha=False shapes (most cloth)  -> 0xE   (bits 1, 2, 3)
      * alpha=True  shapes (translucent) -> 0x8000E (bits 1, 2, 3, 19)

    The 0x8000 bit is the alpha-sorter — required only when the shape
    has a NiAlphaProperty. Setting it uniformly (which we used to do
    via task #65) appears to interact badly with NioOverride for
    non-carrier shapes: hand-authored UBE cloth uses 0xE on every
    non-alpha cloth piece, and a hand-authored UBE armor is the only UBE armor in our
    test set that's been confirmed to follow body sliders.

    No-op on pynifly errors.
    """
    try:
        has_alpha = bool(getattr(shape, "has_alpha_property", False))
        target = (BODYTRI_SHAPE_FLAGS_ALPHA if has_alpha
                  else BODYTRI_SHAPE_FLAGS_OPAQUE)
        if int(getattr(shape, "flags", 0)) != target:
            shape.flags = target
    except Exception:
        pass


# UBE armor uses Shader_Type=0 (Default). CBBE armors often ship Shader_Type=1
# (Environment Map) which blocks NioOverride morphing at runtime even with correct
# BODYTRI/TRI/flags. Force to Default; also clear the Environment_Mapping flag bit
# (0x80) to avoid the renderer sampling a missing cubemap.
SHADER_TYPE_DEFAULT = 0
SHADER_FLAGS_1_ENV_MAPPING_BIT = 0x80


# SkyPartition slot for slot-32 cuirass body. Hand-built UBE NIFs use a single
# SBP_32_BODY partition. Multi-partition shapes (e.g. leggings split across
# SBP_54 + SBP_38 inside a slot-32 cuirass) silently fail to morph via
# NioOverride; collapsing to SBP_32_BODY unblocks them.
SBP_32_BODY_ID = 32

# Biped dismember slots that must NOT be merged into SBP_32_BODY.
# Collapsing accessory slots (gauntlet/boot/helmet) renders that equip region
# invisible. Collapsing limb slots with their own partition (forearms=34) on a
# body-spanning shape corrupts the bone palette -> CTD on equip.
# Calves (38) and modder leg slots are NOT preserved; collapsing them fixes
# leg cloth partitions without known crashes.
PRESERVE_DISMEMBER_SLOTS = frozenset({
    30,  # head
    31,  # hair
    33,  # hands
    34,  # forearms
    35,  # amulet
    36,  # ring
    37,  # feet
    39,  # shield
    40,  # tail
    41,  # long hair
    42,  # circlet
    43,  # ears
})

def _normalize_partitions(shape) -> bool:
    """Collapse a multi-partition shape into a single SBP_32_BODY
    partition. No-op if shape already has 0 or 1 partition. Returns
    True if a collapse was performed.

    Why: NioOverride's BodyMorph routing skips shapes whose partition
    table doesn't match the wearer's primary slot. Source CBBE armors
    sometimes split a leg-region shape across SBP_54_MOD_LEG_LEFT and
    SBP_38_CALVES — even though the parent cuirass NIF is slot 32.
    Hand-built UBE convention (a hand-authored UBE armor, the only reference that
    morphs cloth in our test set) puts every shape at SBP_32_BODY.
    Match that.
    """
    try:
        parts = list(getattr(shape, "partitions", None) or [])
        if len(parts) <= 1:
            return False
        # CRITICAL: never collapse a shape that owns a HANDS/FEET/HEAD/HAIR
        # dismember slot. A gauntlet (slot 33), boot (37) or helmet (30/31)
        # whose mesh partition gets rewritten to SBP_32_BODY no longer
        # renders in its equip region — the game shows nothing and the
        # piece goes invisible (this was a heavily-boned-armor accessory regression).
        # Collapse is ONLY for body-region cloth: leg-slot partitions
        # (SBP_54_MOD_LEG, SBP_38_CALVES) sitting inside a slot-32 cuirass,
        # which block NioOverride morph routing. Those carry no primary
        # extremity/head slot, so they still collapse.
        try:
            part_ids = {getattr(p, "id", None) for p in parts}
        except Exception:
            part_ids = set()
        if part_ids & PRESERVE_DISMEMBER_SLOTS:
            return False
        # Reuse the existing partitions' namedict to construct the
        # canonical SBP_32_BODY entry — direct kwargs construction
        # crashes without one. pynifly stores namedict on every
        # partition; the first one's is fine.
        nd = getattr(parts[0], "namedict", None)
        if nd is None:
            return False
        pyn = _pynifly()
        new_part = pyn.SkyPartition(
            part_id=SBP_32_BODY_ID,
            flags=int(getattr(parts[0], "flags", 257) or 257),
            namedict=nd,
        )
        # All tris go to the (sole) new partition (index 0).
        tri_count = len(getattr(shape, "partition_tris", None) or [])
        if tri_count == 0:
            return False
        shape.set_partitions([new_part], [0] * tri_count)
        return True
    except Exception:
        return False


def _preserved_dismember_slot(shape) -> "int | None":
    """The dismember slot id an over-cap SPLIT must keep so the shape still
    renders in its equip region, or None for body-region shapes (-> SBP_32_BODY).

    Only returns a slot when EVERY source partition sits on ONE preserved
    dismember slot (hands/feet/head/...). Mixed-slot shapes -> None: re-slotting
    them on a Z-rebinned split is ambiguous, so fall back to SBP_32_BODY (the
    prior behavior) rather than guess. Mirrors `_normalize_partitions`' preserve
    guard, but for the split path (which can't simply bail -- the cap overrun
    would CTD). Without this, an over-cap accessory (a dense-boned gauntlet=33 or
    high-vert helmet=30/31) would be split onto SBP_32_BODY and go invisible.
    """
    try:
        ids = {getattr(p, "id", None)
               for p in (getattr(shape, "partitions", None) or [])}
        ids.discard(None)
        preserved = ids & PRESERVE_DISMEMBER_SLOTS
        if len(preserved) == 1 and ids == preserved:
            return next(iter(preserved))
        return None
    except Exception:
        return None


def _split_oversize_partition(shape, cap: "int | None" = None,
                              vert_cap: "int | None" = None,
                              part_id: "int | None" = None) -> int:
    """Split a shape that references MORE than `cap` bones into several skin
    partitions, each referencing <= cap distinct bones, WITHOUT dropping any
    bone. Returns the partition count created (0 = no split done).

    If `vert_cap` is given, ALSO start a new partition when a partition's
    vertex-union would exceed it -- so a dense rig that is over BOTH the bone
    cap AND the vertex cap stays safe on both (else a bone-split partition could
    still hold > vert_cap verts and hit the morph-rebuild OOB).

    Skyrim's GPU skin-partition bone palette overruns above the cap -> equip
    CTD. The historical fix (_cap_skin_bone_count) dropped the lowest-weight
    bones to fit, but that evicted body-MORPH bones a dense dress needs (the
    a dense-dress mod lost NPC Belly + NPC R Butt -> stopped tracking the body).
    The GPU limit is PER PARTITION, so the SAME mesh rendered across two
    partitions (measured: 78 + 9 bones) keeps every bone and never overruns.
    Triangles are ordered by centroid Z and greedily packed so each partition's
    bone-union stays under the cap (spatial ordering keeps the sets compact).
    Best-effort: returns 0 on any failure so the caller keeps source partitions."""
    if cap is None:
        cap = SKIN_PARTITION_BONE_CAP
    try:
        names = list(getattr(shape, "bone_names", []) or [])
        if len(names) <= cap:
            return 0
        verts = np.asarray(shape.verts, dtype=np.float64)
        tris = np.asarray(shape.tris, dtype=np.int64)
        if tris.size == 0:
            return 0
        bw = getattr(shape, "bone_weights", None) or {}
        vert_bones = [set() for _ in range(len(verts))]
        for bi, bn in enumerate(names):
            pairs = bw.get(bn)
            if pairs is None:
                continue
            seq = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            for vi, _w in seq:
                vi = int(vi)
                if 0 <= vi < len(verts):
                    vert_bones[vi].add(bi)
        tri_bones = [vert_bones[t[0]] | vert_bones[t[1]] | vert_bones[t[2]]
                     for t in tris]
        order = np.argsort(verts[tris].mean(axis=1)[:, 2])  # by centroid Z
        assign = np.zeros(len(tris), dtype=np.int64)
        cur, cur_set, cur_verts = 0, set(), set()
        for ti in order:
            ti = int(ti)
            t = tris[ti]
            tri_v = {int(t[0]), int(t[1]), int(t[2])}
            merged = cur_set | tri_bones[ti]
            merged_v = cur_verts | tri_v
            if ((len(merged) > cap or (vert_cap and len(merged_v) > vert_cap))
                    and cur_set):
                cur += 1
                cur_set = set(tri_bones[ti])
                cur_verts = set(tri_v)
            else:
                cur_set = merged
                cur_verts = merged_v
            assign[ti] = cur
        nparts = cur + 1
        if nparts <= 1:
            return 0
        parts0 = list(getattr(shape, "partitions", None) or [])
        nd = getattr(parts0[0], "namedict", None) if parts0 else None
        if nd is None:
            return 0
        pyn = _pynifly()
        pid = SBP_32_BODY_ID if part_id is None else int(part_id)
        objs = [pyn.SkyPartition(part_id=pid, flags=257, namedict=nd)
                for _ in range(nparts)]
        shape.set_partitions(objs, assign.tolist())
        return nparts
    except Exception:
        return 0


def _split_oversize_partition_verts(shape, cap: "int | None" = None,
                                    part_id: "int | None" = None) -> int:
    """Split a shape with MORE than `cap` VERTICES into several skin partitions,
    each referencing <= cap distinct verts, WITHOUT dropping geometry. Returns
    the partition count created (0 = no split done / not needed).

    A single huge partition is not safe for the runtime body-morph rebuild
    (NioOverride reads past the vertex buffer -> equip CTD; measured on a
    ~31.8k-vert torso). The injected UBE body ships multiple partitions for the
    same reason. Triangles are ordered by centroid Z and greedily packed so each
    partition's vertex-union stays under the cap (spatial ordering keeps the sets
    compact). Mirrors `_split_oversize_partition` but gates on vertex count, not
    bone count. Best-effort: returns 0 on any failure so the caller is unchanged."""
    if cap is None:
        cap = SKIN_PARTITION_VERT_CAP
    try:
        verts = np.asarray(shape.verts, dtype=np.float64)
        tris = np.asarray(shape.tris, dtype=np.int64)
        if len(verts) <= cap or tris.size == 0:
            return 0
        order = np.argsort(verts[tris].mean(axis=1)[:, 2])  # by centroid Z
        assign = np.zeros(len(tris), dtype=np.int64)
        cur, cur_set = 0, set()
        for ti in order:
            ti = int(ti)
            t = tris[ti]
            tri_v = {int(t[0]), int(t[1]), int(t[2])}
            merged = cur_set | tri_v
            if len(merged) > cap and cur_set:
                cur += 1
                cur_set = set(tri_v)
            else:
                cur_set = merged
            assign[ti] = cur
        nparts = cur + 1
        if nparts <= 1:
            return 0
        parts0 = list(getattr(shape, "partitions", None) or [])
        nd = getattr(parts0[0], "namedict", None) if parts0 else None
        if nd is None:
            return 0
        pyn = _pynifly()
        pid = SBP_32_BODY_ID if part_id is None else int(part_id)
        objs = [pyn.SkyPartition(part_id=pid, flags=257, namedict=nd)
                for _ in range(nparts)]
        shape.set_partitions(objs, assign.tolist())
        return nparts
    except Exception:
        return 0


def _normalize_partitions_on_disk(dst_path: Path,
                                  src_path: "Path | None" = None) -> int:
    """Post-save pass: reload the NIF at `dst_path`, collapse any
    multi-partition cloth shape to a single SBP_32_BODY partition, and
    re-save IF anything changed. Returns the number of shapes collapsed.

    Why a separate reload pass: on freshly-copied shapes the in-memory
    `partition_tris` array isn't materialized until the NIF has been
    saved and reloaded once (pynifly quirk). Running `_normalize_partitions`
    inside the build path silently no-ops because `partition_tris` reads
    empty. Reloading from disk guarantees the partition table is live.
    """
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
        # HDT-SMP per-triangle COLLISION shapes (the authored furexarot/skirt
        # colliders) must KEEP their authored skin partitions: collapsing or
        # re-slotting them desyncs FSMP's collision build from the XML -> an
        # out-of-bounds read in Main::Update on equip (CTD -- the elven cuirass
        # `Greaves` 32+38 -> 32 case). Same collider set the conform pass preserves.
        # NOTE: the phase-1 inline cleanup loop runs this SAME collapse on the
        # reloaded-from-disk NIF (partition_tris live) BEFORE this pass, so it must
        # skip colliders too -- by the time we get here the collider is already
        # collapsed. See the phase-1/phase-2 inline loops.
        collider_names = (_hdt_collider_shape_names(src_path)
                          if src_path is not None else set())
        changed = 0
        for s in nf.shapes:
            # Body shapes keep their native partitions — they already
            # render + morph via the slot-32 carrier routing. Only
            # collapse cloth/armor shapes, where a stray multi-slot
            # partition (e.g. SBP_54 + SBP_38 on a vanilla armor leggings)
            # blocks NioOverride morph routing.
            if s.name in UBE_BODY_INJECT_NAMES or s.name == "VirtualGround":
                continue
            # SMP COLLIDER: keep its authored partitions exactly (see above).
            if s.name in collider_names:
                continue
            # An over-cap ACCESSORY (gauntlet=33, boot=37, helmet=30/31, ...) must
            # keep its dismember slot across the split or it goes invisible in its
            # equip region; body-region shapes -> None -> SBP_32_BODY.
            keep_slot = _preserved_dismember_slot(s)
            # Over-cap shape: SPLIT into <=cap-bone partitions (keeps every bone,
            # CTD-safe) instead of collapsing to one over-budget partition. Dense
            # dresses/robes that ship 79-81 bones (some armor mods) keep their full
            # morph + physics rig this way. Under-cap shapes collapse as before.
            if len(getattr(s, "bone_names", []) or []) > SKIN_PARTITION_BONE_CAP:
                # Respect BOTH caps: a dense rig that is also high-vert must not
                # leave a bone-split partition still over the vertex cap.
                if _split_oversize_partition(
                        s, vert_cap=SKIN_PARTITION_VERT_CAP,
                        part_id=keep_slot) > 1:
                    changed += 1
                else:
                    # Split failed (no source partition / pynifly error): the
                    # shape ships over the per-partition cap -> equip-CTD risk.
                    # Log loudly so it's visible rather than a silent crash.
                    import sys as _sys
                    print(f"  WARNING: {s.name!r} has "
                          f"{len(s.bone_names)} bones (> {SKIN_PARTITION_BONE_CAP}"
                          f"-bone GPU cap) and could NOT be split into partitions "
                          f"-> may CTD on equip in {dst_path.name}", file=_sys.stderr)
                continue
            # Over-VERTEX-cap shape: a single huge partition is unsafe for the
            # runtime body-morph rebuild (equip CTD; measured on a ~31.8k-vert
            # torso). Split into vertex-balanced partitions like the injected
            # body ships -- do NOT collapse to one partition below (that's the
            # very thing that CTDs). Under-cap shapes fall through and collapse.
            if len(getattr(s, "verts", []) or []) > SKIN_PARTITION_VERT_CAP:
                if _split_oversize_partition_verts(s, part_id=keep_slot) > 1:
                    changed += 1
                else:
                    # Split failed (no source partition / pynifly error): the
                    # shape keeps one huge partition -> morph-rebuild equip-CTD
                    # risk. Surface it loudly, mirroring the bone-cap branch.
                    import sys as _sys
                    print(f"  WARNING: {s.name!r} has "
                          f"{len(s.verts)} verts (> {SKIN_PARTITION_VERT_CAP}"
                          f"-vert morph-rebuild cap) and could NOT be split into "
                          f"partitions -> may CTD on equip in {dst_path.name}",
                          file=_sys.stderr)
                continue
            if _normalize_partitions(s):
                changed += 1
        if changed:
            # Re-assert the VirtualBody Hidden bit: a pynifly re-save can drop it
            # (-> blue body double), and on the merge path this can be a terminal
            # save. Mirrors the conform pass + _finalize_hdt_physics.
            _hide_virtual_body(nf)
            atomic_nif_save(nf, dst_path)
        return changed
    except Exception:
        return 0


def _hide_virtual_body(nif) -> bool:
    """Set the NiAVObject Hidden bit on any VirtualBody shape in the
    NIF so Skyrim's renderer skips it. Returns True if a VirtualBody
    was found and (re)hidden, False otherwise.

    Why this is needed: VirtualBody is an HDT-SMP collision proxy
    shape with empty texture paths. Pynifly's shape copy leaves it
    with Shader_Flags_1 bit 1 (Skinned) set — enough for Skyrim to
    invoke its shader. With no diffuse texture available the
    shader falls back to the blue missing-texture placeholder,
    rendering as a visible blue body behind / through any revealing
    cloth on the wearer. The famous "blue body double" artifact.

    Hand-authored BodySlide UBE armor ships VirtualBody with
    Shader_Flags_1 = 0 so the shader is skipped entirely. Pynifly's
    shader property setter doesn't reliably persist on textureless
    shapes (same C-level binding gap as `_strip_alpha_property`).
    Setting the NiAVObject Hidden bit (bit 0 of `flags`) is the
    workaround that DOES persist: Skyrim's renderer skips Hidden
    shapes outright; HDT-SMP reads geometry for collision
    registration independently of the render flags.

    Called from both Phase 1 (copy mode) and Phase 2 (body-swap)
    paths because either can produce a NIF carrying a source-
    inherited or newly-injected VirtualBody.
    """
    found = False
    try:
        for s in nif.shapes:
            if s.name != "VirtualBody":
                continue
            found = True
            try:
                cur = int(getattr(s, "flags", 0))
                if not (cur & 0x1):
                    s.flags = cur | 0x1
            except Exception:
                pass
            break
    except Exception:
        pass
    return found


def _strip_alpha_property(shape) -> None:
    """Detach the shape's NiAlphaProperty reference.

    Empirical finding: hand-authored BodySlide UBE morphable shapes
    have `alphaPropertyID = 0xFFFFFFFF` (no alpha property), while
    source CBBE armor shapes often have an alpha property attached
    for translucent leather/cloth stitching detail. With every other
    structural difference between hand-authored and ours eliminated
    (flags, shader env-mapping, BODYTRI presence, scale-bone
    weights), alpha property is the remaining suspect — NioOverride's
    BodyMorph appears to skip shapes with NiAlphaProperty,
    presumably because Skyrim's renderer treats alpha-blended shapes
    as transparency overlays that shouldn't deform with the body.

    Cost: the shape loses its translucent rendering — transparent
    UV regions render opaque. For most CBBE armor pieces this is
    invisible (the alpha property was used for trim detail or
    alpha-tested edges that already look fine opaque). Worth the
    trade-off versus the shape never morphing.

    Idempotent. Safe to call on shapes that already lack an alpha
    property (no-op)."""
    try:
        props = shape.properties
        current = int(getattr(props, "alphaPropertyID", 0xFFFFFFFF))
        if current == 0xFFFFFFFF or current == -1:
            return  # already none
        try:
            props.alphaPropertyID = 0xFFFFFFFF
        except Exception:
            return
        # Persist via save_shader_attributes — same path that worked
        # for Shader_Flags_1 mutations. The alpha-property reference
        # lives in the same BSTriShape header block as the shader
        # property reference, so the same save call covers it.
        try:
            shape.save_shader_attributes()
        except Exception:
            pass
    except Exception:
        pass


def _normalize_shader_for_morph(shape) -> None:
    """DISABLED (no-op). The env-map flag (Shader_Flags_1 bit 7) does NOT
    block NioOverride morphing. The former flag-clear also caused
    save_shader_attributes() to truncate the BSShaderTextureSet and drop
    the EnvMask (slot 5). Left as a no-op so call sites are unchanged.
    """
    return


# Skyrim BSLightingShaderProperty flag bits for per-vertex color / alpha.
# A shape that has these set in its shader MUST carry a vertex-color buffer;
# the engine reads color per-vertex while building the 3D model and crashes
# (access violation) if the buffer is absent.
SLSF2_VERTEX_COLORS = 0x20  # Shader_Flags_2 bit 5
SLSF1_VERTEX_ALPHA = 0x08   # Shader_Flags_1 bit 3


def _shape_has_vertex_colors(shape) -> bool:
    """True if the shape's BSTriShape carries a per-vertex color buffer.
    Unknown (can't read the field) -> assume present so we never strip a
    flag from a shape we're unsure about."""
    props = getattr(shape, "properties", None)
    if props is not None and hasattr(props, "hasVertexColors"):
        try:
            return bool(int(props.hasVertexColors))
        except (TypeError, ValueError):
            pass
    if props is not None and hasattr(props, "vertexDesc"):
        try:
            return bool(int(props.vertexDesc) & SLSF2_VERTEX_COLORS)
        except (TypeError, ValueError):
            pass
    return True


def fix_vertex_color_shader_flags(nif) -> int:
    """Clear the Vertex_Colors (SLSF2 0x20) / Vertex_Alpha (SLSF1 0x08) shader
    flags on any shape whose mesh carries NO vertex-color buffer.

    WHY THIS EXISTS — this is a hard-CTD fix. Our shape-rebuild paths
    (`createShapeFromData` for merge/atlas, the deep-copy in `_copy_shape`,
    the body-delta warp) produce a BSTriShape WITHOUT the source's per-vertex
    colors, but they copy the source shader's `Shader_Flags`, which on most
    armor have Vertex_Colors / Vertex_Alpha set. The result is a shader that
    tells the renderer "read a color per vertex" pointing at a vertex buffer
    that has no color component. When the engine builds the 3D model (on
    equip / first render) it reads unmapped memory -> EXCEPTION_ACCESS_VIOLATION.
    It is deterministic for player-worn gear (the model is built at load), so
    it presents as a startup CTD on whatever the player happens to wear. This
    is exactly the shader/data consistency that NifSkope and SSE NIF Optimizer
    enforce. Returns the number of shapes fixed. Takes the pynifly NifFile.
    """
    fixed = 0
    for shape in getattr(nif, "shapes", []):
        if _shape_has_vertex_colors(shape):
            continue
        sh = getattr(shape, "shader", None)
        if sh is None:
            continue
        props = getattr(sh, "properties", None)
        if props is None:
            continue
        changed = False
        try:
            sf2 = int(getattr(props, "Shader_Flags_2", 0))
            if sf2 & SLSF2_VERTEX_COLORS:
                props.Shader_Flags_2 = sf2 & ~SLSF2_VERTEX_COLORS
                changed = True
            sf1 = int(getattr(props, "Shader_Flags_1", 0))
            if sf1 & SLSF1_VERTEX_ALPHA:
                props.Shader_Flags_1 = sf1 & ~SLSF1_VERTEX_ALPHA
                changed = True
        except (TypeError, AttributeError):
            continue
        if changed:
            try:
                sh.write_properties()
                fixed += 1
            except Exception:
                pass
    return fixed


def _repair_effect_shader_shape_controllers(nif_backing) -> int:
    """Reset a SHAPE's shape-level controllerID to NONE when it wrongly points at
    that same shape's shader property (controllerID == shaderPropertyID).

    A post-conversion re-save path can leave an effect-shader (glow/decal) shape
    with its shape-level controllerID dangling onto its own BSEffectShaderProperty
    block. At equip the engine walks the shape's controller chain, finds a
    BSEffectShaderProperty where a NiTimeController must be, and calls a bad vtable
    slot -> EXCEPTION_ACCESS_VIOLATION `call [rax+0x28]` CTD (the Daedric
    MaleTorsoGlow crash). A shape's controller can NEVER validly be its own shader
    property, so this self-reference is unambiguously corrupt; source glow shapes
    carry NONE here. Returns the number of shapes repaired. #glow-shape-controller"""
    try:
        none_id = _pynifly().NODEID_NONE
    except Exception:
        return 0
    fixed = 0
    for s in nif_backing.shapes:
        try:
            pr = s.properties
            cid = getattr(pr, "controllerID", none_id)
            spid = getattr(pr, "shaderPropertyID", none_id)
            if cid != none_id and cid == spid:
                pr.controllerID = none_id
                try:
                    s.write_properties()
                except Exception:
                    pass
                fixed += 1
        except Exception:
            continue
    return fixed


def _sanitize_one_nif_worker(path_str: str) -> int:
    """Worker (picklable for ProcessPoolExecutor): load ONE NIF, clear
    inconsistent vertex-color/alpha shader flags AND repair dangling effect-shader
    shape controllers, save if changed. Returns the number of shapes fixed
    (0 = nothing changed / unreadable). Each call touches a distinct file, so
    parallel workers never write-conflict."""
    try:
        from . import nif_io
        from pathlib import Path as _Path
        nif = nif_io.load_nif(_Path(path_str))
    except Exception:
        return 0
    n = 0
    try:
        n += fix_vertex_color_shader_flags(nif._backing)
    except Exception:
        pass
    try:
        n += _repair_effect_shader_shape_controllers(nif._backing)
    except Exception:
        pass
    if n:
        try:
            atomic_nif_save(nif._backing, path_str)
            return n
        except Exception:
            return 0
    return 0


def sanitize_output_vertex_color_flags(meshes_root, workers: "int | None" = None) -> dict:
    """Final post-conversion sweep: walk every converted NIF under
    `meshes_root` and clear inconsistent vertex-color/alpha shader flags
    (see `fix_vertex_color_shader_flags`). Run AFTER all conversion paths so
    it catches output regardless of which build path wrote the NIF — cheaper
    and more reliable than threading the fix through every `.save()` site.
    Idempotent. Returns a small stats dict.

    PARALLEL: the per-NIF cost is dominated by the full load+parse, and every
    file is independent (each worker saves only its own file), so the sweep
    fans out across a process pool — the same pattern the main conversion uses.
    On a big modlist (thousands of output NIFs) this turns a multi-minute serial
    walk into a few seconds. Falls back to serial for small batches (pool spawn
    overhead not worth it) or single-core / pool-init failure."""
    from pathlib import Path as _Path
    files_list = [str(p) for p in _Path(meshes_root).rglob("*.nif")]
    files = len(files_list)
    if files == 0:
        return {"files": 0, "files_changed": 0, "shapes_fixed": 0}

    if workers is None:
        try:
            workers = max(1, min(16, (os.cpu_count() or 2) - 2))
        except Exception:
            workers = 1

    def _run_serial() -> "tuple[int, int]":
        from . import nif_io
        fc = sf = 0
        for ps in files_list:
            try:
                nif = nif_io.load_nif(_Path(ps))
            except Exception:
                continue
            n = 0
            try:
                n += fix_vertex_color_shader_flags(nif._backing)
            except Exception:
                pass
            try:
                n += _repair_effect_shader_shape_controllers(nif._backing)
            except Exception:
                pass
            if n:
                try:
                    atomic_nif_save(nif._backing, ps)
                    fc += 1
                    sf += n
                except Exception:
                    pass
        return fc, sf

    # Serial for small batches / single worker — pool spawn (esp. frozen-exe
    # spawn re-import of pynifly per worker) costs more than it saves there.
    if workers <= 1 or files < 64:
        files_changed, shapes_fixed = _run_serial()
        return {"files": files, "files_changed": files_changed,
                "shapes_fixed": shapes_fixed}

    files_changed = shapes_fixed = 0
    try:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for n in ex.map(_sanitize_one_nif_worker, files_list, chunksize=16):
                if n:
                    files_changed += 1
                    shapes_fixed += n
    except Exception:
        # Any pool failure (spawn issue, etc.) -> safe serial fallback.
        files_changed, shapes_fixed = _run_serial()
    return {"files": files, "files_changed": files_changed,
            "shapes_fixed": shapes_fixed}


# Upper-torso RIGID skeleton bones. A shape dominated by these ARMORS the chest
# and shoulders -- it is body-fitted plate, not free hanging cloth. Free cloth
# (skirt / cape / tabard) hangs off pelvis/thigh + chain bones and barely weights
# these. Used to keep the GENERATED per-vertex soft-body off rigid torso armour.
_UPPER_TORSO_RIGID_BONE_KEYS = (
    "Clavicle", "UpperArm", "Pauldron", "Spine2", "Spine1", "Neck", "Breast",
)


def _shape_is_rigid_torso_armor(shape, threshold: float = 0.35) -> bool:
    """True if `shape` is a body-fitted TORSO cuirass rather than free hanging
    cloth: at least `threshold` of its total skin weight sits on the upper-torso
    rigid bones (clavicle / upperarm / pauldron / upper-spine). Such a shape must
    never be turned into a GENERATED per-vertex soft-body -- with no authored
    chain it becomes free cloth1 and the whole armour flops / disjoints. The
    measured separation is wide (a real skirt ~2%, a cuirass ~54%), so a mid
    threshold cleanly splits them. #softbody-rigid-gate"""
    try:
        wpb = shape.bone_weights
    except Exception:
        return False
    if not wpb:
        return False
    total = 0.0
    upper = 0.0
    for b, lst in wpb.items():
        w = sum(x[1] for x in lst)
        total += w
        if any(k in b for k in _UPPER_TORSO_RIGID_BONE_KEYS):
            upper += w
    return total > 0.0 and (upper / total) >= threshold


def _pick_bodytri_carriers(nif, *, exclude_body: bool = False) -> "list[object]":
    """Pick exactly ONE shape per NIF to receive a BODYTRI extra-data
    block, matching the hand-authored BodySlide UBE convention.

    `exclude_body=True` skips the body-shape preference (BaseShape/3BA) so the
    result is a CLOTH carrier even on a body-swap NIF. Used by the HDT-XML
    generator: the body is the kinematic COLLIDER (emitted as a per-triangle
    shape), never a simulated per-vertex cloth — picking BaseShape there made
    the injected body flop as soft-body cloth while the real cape got no
    physics. The default (False) keeps the body-first preference the BODYTRI
    MORPH carrier wants.

    Carrier preference (in order):
      1. BaseShape / 3BA — body shape, if present. NioOverride morphs
         all shapes in the TRI when BODYTRI is on the body shape; on
         a cloth shape NioOverride often skips other shapes.
      2. Cloth shape — for body-less NIFs (slot-49 cloth-only armors).
      3. Hand/foot fallback — for gauntlet/boot-only NIFs.

    Single-carrier: NioOverride opens the TRI once via the BODYTRI
    reference and applies per-name morphs to every shape in the TRI.

    Returns a list with 0 or 1 entry.
    """
    # Preference 1: BODY SHAPE as carrier. Matches the dominant
    # hand-built convention (88/93 sampled slot-32 UBE NIFs).
    # VirtualBody is excluded — it's a Hidden physics proxy, not
    # the visible body shape NioOverride wants to morph.
    BODY_CARRIER_NAMES = ("BaseShape", "3BA")
    if not exclude_body:
        for s in nif.shapes:
            if s.name in BODY_CARRIER_NAMES:
                return [s]

    candidates: list = []
    hand_fallbacks: list = []
    for s in nif.shapes:
        if not (s.textures or {}):
            continue
        if s.name in BODYTRI_CARRIER_EXCLUDE:
            continue
        nlow = s.name.lower()
        if any(kw in nlow for kw in NON_CLOTH_SHAPE_KEYWORDS):
            continue
        # Use strict weight-fraction test: arm-shell pieces (Bracers, Guards,
        # <1% extremity weight) are good carriers; only actual hand/foot skin
        # (Hands_2 97%, Gloves_1 71%) goes to the last-resort fallback.
        if _shape_is_extremity_dominant(s):
            hand_fallbacks.append(s)
            continue
        candidates.append(s)
    if not candidates and not hand_fallbacks:
        # Every textured shape was excluded by rigid-name keywords (e.g. a
        # chest garment named "straps" hits the "shoulder" keyword). A NIF
        # with no carrier never loads its TRI -> nothing morphs. Pick the
        # largest textured shape as the carrier (a truly rigid prop has near-
        # zero deltas so the morph is harmless).
        rigid_named = [
            s for s in nif.shapes
            if (s.textures or {}) and s.name not in BODYTRI_CARRIER_EXCLUDE
        ]
        non_ext = [s for s in rigid_named
                   if not _shape_is_extremity_dominant(s)]
        pool = non_ext or rigid_named
        if not pool:
            return []
        return [max(pool, key=lambda s: len(s.verts))]

    CLOTH_KEYWORDS = (
        "corset", "leather", "fabric", "tabard", "panties", "panty",
        "skirt", "tassel", "tasset", "belt", "shirt", "robe", "dress",
        "cloak", "cape", "scarf", "loin",
    )
    def rank_key(s):
        nlow = s.name.lower()
        kw_match = next(
            (i for i, kw in enumerate(CLOTH_KEYWORDS) if kw in nlow),
            len(CLOTH_KEYWORDS),
        )
        return (kw_match, -len(s.verts))
    if candidates:
        candidates.sort(key=rank_key)
        return [candidates[0]]
    # Carrier-of-last-resort: NIF contains only hand/foot shapes. The per-armor
    # TRI for these NIFs only contains hand/foot morph entries — no body deltas
    # leak onto fingers — so picking the hand/foot shape is safe.
    hand_fallbacks.sort(key=rank_key)
    return [hand_fallbacks[0]]


# ---------- M6: per-vertex proximity-blend re-skin ----------------------
#
# Re-skin armor verts to UBE body bones so body morphs/animations propagate
# at runtime. Per-vertex blend keyed on distance to body surface:
#   dist < NEAR -> 100% body weights; NEAR..FAR -> linear blend;
#   dist >= FAR -> 100% original armor weights (rigid).
# Sidesteps the cloth/metal classification problem: pauldrons stay rigid;
# chest fabric hugging the body inherits body skinning automatically.

RESKIN_NEAR_DIST = 0.5
RESKIN_FAR_DIST = 2.0
RESKIN_K = 4

# ROLLBACK FLAG — keep False. Excluding scale bones leaves cloth shapes with no
# body-tracking layer (the TRI carries per-shape deltas, but cloth that has no
# per-shape BODYTRI relies on scale bones alone). The per-partition bone cap
# (_cap_skin_bone_count) is the correct fix for GPU-cap CTDs, independent of
# this flag. Set True only to A/B test a configuration without scale bones.
RESKIN_EXCLUDE_SCALE_BONES = False

# When the source ships its own BodySlide morph TRI for a shape, that TRI drives the
# body morph, so the M6 reskin is redundant AND is the equip fly/spike instability (its
# K-NN body-bone blend is unstable under animation). Prefer the shape's stable source
# skin; conform + clearance still run. Shapes with no source TRI keep the reskin.
# CBBE2UBE_RESKIN_KEEP=1 always reskins.  [DESIGN: Fitting]
RESKIN_PREFER_SOURCE_WHEN_MORPH_TRI = (
    os.environ.get("CBBE2UBE_RESKIN_KEEP", "").strip().lower()
    not in ("1", "true", "yes", "on")
)

# Opt-in (default OFF): graft animation scale bones onto a morph-TRI shape's source
# skin instead of fully excluding it from the reskin. OFF restores the proven
# exemption -- a morph-TRI shape keeps its untouched source skin so its BodySlide
# body-slider TRI stays in sync (grafting desynced it -> leg armor stopped inflating
# with a morphed body -> thigh-coverage loss). See [DESIGN: Morph-TRI reskin].
_MORPHTRI_SCALE = (
    os.environ.get("CBBE2UBE_MORPHTRI_SCALE", "").strip().lower()
    in ("1", "true", "yes", "on")
)


def _source_morph_tri_shape_names(src_path: "Path") -> "set[str]":
    """Shape names covered by the SOURCE mod's own BodySlide morph TRI (it sits
    next to the source NIF as `<armor-stem>.tri`). These shapes are morphed by
    that TRI at runtime, so they don't need the M6 reskin's scale-bone morph and
    are better served by their stable source skin. Empty set if the source ships
    no such TRI (-> keep the reskin). Best-effort; never raises."""
    try:
        stem = src_path.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        tri = src_path.parent / (stem + ".tri")
        if not tri.is_file():
            return set()
        from .tri import TriFile
        return {sh.name for sh in TriFile.load(tri).shapes}
    except Exception:
        return set()

# Wider conformance band for body-fitted armor (slot 32 + leg slots).
# A thicker shell fully adopts body bone weights so it bends with the body
# during animation. Slot-49 flowing cloth keeps the narrow default via
# _slot_aware_reskin_band — over-conforming a skirt makes it cling, not drape.
RESKIN_NEAR_DIST_BODYFIT = 1.2
RESKIN_FAR_DIST_BODYFIT = 3.5

# Body-fitted biped slots: body(32), forearms(34), calves(38), legs(53-58).
# Hands/feet/gauntlets/boots take _shape_has_fine_animation_bones path instead.
_RESKIN_BODY_FITTED_BITS = (
    (1 << (32 - 30)) | (1 << (34 - 30)) | (1 << (38 - 30))
    | (1 << (53 - 30)) | (1 << (54 - 30)) | (1 << (55 - 30))
    | (1 << (56 - 30)) | (1 << (57 - 30)) | (1 << (58 - 30))
)


def _slot_aware_reskin_band(biped_slots: int) -> "tuple[float, float]":
    """Return (near_dist, far_dist) for the body-weight conformance blend,
    widened for body-fitted slots so the armor deforms with the body during
    animation (kills body-poke-through-during-movement). Slot-49-only flowing
    cloth and slot-less armor keep the narrow default so they still drape."""
    if biped_slots & _RESKIN_BODY_FITTED_BITS:
        return (RESKIN_NEAR_DIST_BODYFIT, RESKIN_FAR_DIST_BODYFIT)
    return (RESKIN_NEAR_DIST, RESKIN_FAR_DIST)


# ----- Scale-bone (morph-driven) reskin --------------------------------
#
# 3BA / UBE use dedicated SCALE BONES (Breast01-03, Belly, Butt, FrontThigh,
# etc.) that the engine scales when body sliders move. Armor verts skinned to
# these bones automatically follow body-shape changes — same mechanism as
# BodySlide-built UBE armor, works for any slot.
#
# The base M6 reskin only reaches RESKIN_FAR_DIST (2u). Hanging cloth (loincloth,
# draping skirt panels) sits 2-5u off the body and misses it. This post-pass
# targets scale bones with an extended reach — surgical (only those bones, small
# magnitude) so rigid pauldrons/plates are barely touched while loose cloth follows
# sliders proportionally.
SCALE_BONE_KEYWORDS = (
    "breast",
    "butt",
    "belly",
    "frontthigh", "rearthigh", "rearcalf",
    # Genital/anatomy bones (clit/pussy/vagina/anus/nipple) DELIBERATELY EXCLUDED.
    # They are HDT-SMP PHYSICS bones, unstable on the UBE race. Proximity transfer
    # was putting up to 57% weight on them for rigid groin plates -> jiggle dragged
    # the "pants" down. Armor must never track genital anatomy.
)

# HDT-SMP physics-driven subset of the scale bones (breast/butt/belly jiggle).
# Distinct from the STATIC leg-shape scale bones (frontthigh/rearthigh/rearcalf).
# Suppressed for leg-encasing rigid armor (greaves/pants) where jiggle drag
# collapses the plate; static leg-shape bones are kept for SIZE slider tracking.
PHYSICS_JIGGLE_SCALE_KEYWORDS = ("breast", "butt", "belly")


def _is_physics_jiggle_scale_bone(bone_name: str) -> bool:
    low = bone_name.lower()
    return any(kw in low for kw in PHYSICS_JIGGLE_SCALE_KEYWORDS)


# Rigid LEG skeleton bones (the actual animation bones, NOT the frontthigh/
# rearthigh/rearcalf SCALE bones, which also contain "thigh"/"calf"). A shape
# whose verts are MAJORITY-dominated by these is leg-encasing armor.
LEG_RIGID_BONE_KEYWORDS = ("thigh", "calf", "foot", "toe")


def _is_leg_rigid_bone(bone_name: str) -> bool:
    if _is_scale_bone(bone_name):
        return False  # frontthigh/rearthigh/rearcalf are scale bones, not rigid
    low = bone_name.lower()
    return any(kw in low for kw in LEG_RIGID_BONE_KEYWORDS)


# Upper-body anchor bones: a chain hanging off these (cape off spine, etc.)
# needs a NESTED skeleton tree so FSMP tracks torso/limb motion through it.
# Chains anchored on the lower body (pelvis/thigh) work FLAT (actor-driven).
_UPPER_BODY_ANCHOR_KEYWORDS = ("spine", "neck", "head", "clavicle", "shoulder")


def _is_upper_body_anchor(bone_name: str) -> bool:
    low = (bone_name or "").lower()
    return any(k in low for k in _UPPER_BODY_ANCHOR_KEYWORDS)


# Whether to add 3BA scale-bone weights to cloth shapes during reskin.
# Keep True — scale bones are the runtime body-tracking layer for cloth that
# has no per-shape BODYTRI. Our pipeline does not bake a preset at build time,
# so cloth must track body sliders this way. Hands/feet keep their own pass.
ADD_SCALE_BONES_TO_CLOTH = True

# When set, add_scale_bone_weights skips the physics-driven jiggle bones
# (breast/butt/belly) while keeping static leg-shape bones. Useful for
# troubleshooting jiggle-drag collapse on rigid leg armor. Env: CBBE2UBE_NO_SOFTBODY_SCALES.
NO_SOFTBODY_SCALES = (
    os.environ.get("CBBE2UBE_NO_SOFTBODY_SCALES", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Inject UBE Hands/Feet into gauntlets/boots, replacing CBBE-topology extremity shapes.
# Without this, the source body-skin `Hands` shape is dropped with no replacement
# and the bare hand is invisible under the gauntlet. No-ops gracefully if the
# tangent mesh is absent.
INJECT_UBE_EXTREMITY_REPLACEMENT = True

# Scale-bone reach: world units beyond which no scale weight is added.
# 12u covers the torso including the belly-to-corset gap (Z ~92-94 to Z<=87.5).
SCALE_BONE_REACH = 12.0

# Slot-49 cloth hangs 15-25u from the body. 15u covers the torso-to-mid-thigh
# range where body grows on butt/hip sliders; hem verts further out follow via
# TRI morphs instead. Keeps scale-bone reach from pulling skirt-front verts
# toward NPC Butt via cross-region bind-pose bleed.
SCALE_BONE_REACH_SLOT49 = 15.0

# Gauntlets/boots: tighter reach so the forearm doesn't inherit butt scale
# weight from nearby hip-weighted body verts (~11u away in bind pose).
# A plain gauntlet correctly gets zero body scale bones; boots still follow
# RearCalf/thigh at <1u.
SCALE_BONE_REACH_HANDS_FEET = 8.0

SCALE_BONE_K = 8
# Max fraction of a vert's total weight that can become scale-bone influence.
# Hand-built UBE soft-body cloth tops out ~0.76; 0.65 leaves 35% on rigid bones
# for animation and avoids extreme cross-region bleed.
SCALE_BONE_MAX_TRANSFER = 0.65

# Torso-parity falloff: replace linear (1 - d/reach) with a power curve
# (1 - (d/reach)^P) for breast/belly/butt bones on torso/lower-body pieces.
# A 7u-standoff cuirass tracks the live morph at ~0.96 vs 0.38 linear —
# preventing body-poke-through without extra standoff. Bounded by the body's
# own per-vert weight (never inflates past body). Raise P if big presets
# still poke at the chest/belly; lower toward 1.0 to revert to linear.
TORSO_PARITY_FALLOFF_POWER = 6.0

# Suppress breast/belly/butt scale weight on arm-dominated verts (forearm
# bracers / sleeves sit within scale-bone reach of hip-weighted body verts
# and get cross-talk spikes if this is not suppressed). Revert by setting False.
SUPPRESS_TORSO_SCALE_ON_ARMS = True


def _is_scale_bone(bone_name: str) -> bool:
    low = bone_name.lower()
    return any(kw in low for kw in SCALE_BONE_KEYWORDS)


def _is_torso_parity_bone(bone_name: str) -> bool:
    """Breast / belly / butt scale bones — the torso regions that grow most
    under body sliders and poke through under-tracking armor. Targeted by the
    torso-parity falloff boost (see TORSO_PARITY_FALLOFF_POWER)."""
    b = bone_name.lower()
    return ("breast" in b) or ("belly" in b) or ("butt" in b)


def _is_arm_hand_bone(bone_name: str) -> bool:
    """Arm / hand skeleton bone. A converted vert whose AUTHORED skinning is
    dominated by one of these is sleeve/gauntlet geometry, NOT a torso plate —
    so it is excluded from the torso-parity falloff boost. This is what keeps
    the boost from amplifying bind-pose cross-talk (a forearm that hangs ~11u
    from the hip/butt-weighted body verts in the A-pose), i.e. it can't make
    the #133 arm-piece spike worse: arm verts keep the original linear falloff."""
    b = bone_name.lower()
    return any(kw in b for kw in
               ("forearm", "upperarm", "hand", "finger", "thumb"))


# Tall calf/foot boots (slot 37) whose shaft rides the Thigh bone get the body's
# far-thigh scale bones (Front/RearThigh) grafted onto the shaft by the fine-anim
# reskin, which makes the whole boot FADE OUT at camera distance on a UBE actor.
# Exclude the far-thigh scale bones from calf/foot-dominant footwear (keep RearCalf);
# genuine thigh-high boots keep the thigh morph. Default on;
# CBBE2UBE_KEEP_BOOT_THIGH_SCALE=1 off.
EXCLUDE_BOOT_FAR_THIGH_SCALE = (
    os.environ.get("CBBE2UBE_KEEP_BOOT_THIGH_SCALE", "").strip().lower()
    not in ("1", "true", "yes", "on")
)
# The far-thigh scale bones to drop (RearCalf/calf are deliberately NOT here).
BOOT_FAR_THIGH_SCALE_SUBSTRINGS = ("frontthigh", "rearthigh")
# A foot-slot shape with at least this fraction of verts dominated by the rigid
# THIGH bone is a thigh-high boot that really covers the thigh -> keep thigh morph.
BOOT_THIGH_DOMINANT_FRAC = 0.5


def _boot_far_thigh_scale_exclusions(src_shape, biped_slots: int) -> tuple[str, ...]:
    """Far-thigh scale-bone name substrings to EXCLUDE from the scale-bone graft
    for this shape, or () to exclude nothing. Non-empty only when the feature is
    on, the piece is foot-slot (37), and the shape is calf/foot-dominant (i.e. a
    normal boot, not a thigh-high one). See EXCLUDE_BOOT_FAR_THIGH_SCALE."""
    if not EXCLUDE_BOOT_FAR_THIGH_SCALE:
        return ()
    if not (biped_slots & BIPED_SLOT37_BIT):
        return ()
    try:
        bw = src_shape.bone_weights or {}
        n = len(src_shape.verts)
        if n <= 0:
            return BOOT_FAR_THIGH_SCALE_SUBSTRINGS
        dom_w = np.zeros(n, dtype=np.float64)
        thigh_dom = np.zeros(n, dtype=bool)
        for bn, pairs in bw.items():
            is_thigh = ("thigh" in bn.lower()) and not _is_scale_bone(bn)
            pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            for i, w in pl:
                i = int(i)
                if 0 <= i < n and w > dom_w[i]:
                    dom_w[i] = w
                    thigh_dom[i] = is_thigh
        if float(thigh_dom.mean()) >= BOOT_THIGH_DOMINANT_FRAC:
            return ()  # thigh-high boot: keep thigh size morph
    except Exception:
        pass
    return BOOT_FAR_THIGH_SCALE_SUBSTRINGS


# Don't re-skin these shapes — they ARE the body / VirtualBody. We inject
# them from the UBE ref so their skinning is already correct.
RESKIN_SKIP_NAMES = set(UBE_BODY_INJECT_NAMES)

# Source-shape bone names that signal "this shape is rigged for fine
# articulation we shouldn't disturb" — hands, fingers, feet, toes.
# The UBE body NIF's BaseShape doesn't include hand/foot bones in its
# weight table (hands and feet are separate meshes in Skyrim's setup).
# If we re-skin a glove or boot to body torso bones via M6 proximity-
# blend, we replace its hand/finger skinning with arm-near-body
# skinning, which breaks finger / toe animation. So: if a source
# shape has any of these bones in its skinning, skip M6 entirely
# and keep the original armor skinning intact.
RESKIN_PRESERVE_BONE_KEYWORDS = (
    "hand", "finger", "thumb",
    "foot", "toe",
)

# --- Custom (armor-specific) physics-bone preservation ----------------
# Armor-specific physics bones (skirt/cape/tail chains) aren't in the actor skeleton,
# so on a rebuild pynifly's flat identity node pins their verts to the origin and the
# garment collapses through the floor -- _precreate_custom_bone_chains recreates their
# source transforms + parent links instead. A bone counts as a resolvable SKELETON bone
# (no preservation) if it matches these prefixes/keywords; everything else weighted by a
# shape is armor-specific.  [DESIGN: Custom physics-bone chains]
_SKELETON_BONE_PREFIXES = ("NPC ", "CME ", "HDT ")
_SKELETON_BONE_KEYWORDS = (
    "breast", "butt", "belly", "pelvis", "spine", "thigh", "calf",
    "foot", "hand", "finger", "thumb", "toe", "clav", "arm", "head",
    "neck", "vagina", "pussy", "clitoral", "anus", "genital", "scrotum",
    "tongue", "jaw", "eye", "root", "com", "shoulder", "forearm",
)


def _is_skeleton_bone(name: str) -> bool:
    """True if `name` is a standard actor-skeleton bone the game resolves
    by itself (so a flat/identity node in the armor NIF is fine). False
    for armor-specific physics bones (skirt/cape/tail chains) whose node
    hierarchy + transforms must be preserved or they collapse to origin."""
    if not name:
        return True
    # Armor-specific physics-chain bones conventionally carry a mod prefix that
    # starts with '_' (e.g. '_SomeMod_Neck_L_01 02'). A body-part keyword INSIDE
    # such a name ('neck', 'breast', 'tail'...) must NOT mark it a skeleton bone,
    # or _precreate_custom_bone_chains skips it and its chain nodes are recreated
    # flat at the origin -> the cloth rides them through the floor in game. Real
    # skeleton/animation bones never start with '_'.
    if name.startswith("_"):
        return False
    if name.startswith(_SKELETON_BONE_PREFIXES):
        return True
    low = name.lower()
    return any(kw in low for kw in _SKELETON_BONE_KEYWORDS)


_SOFT_BODY_PHYSICS_BONE_KEYWORDS = (
    "breast", "butt", "belly", "genital", "vagina", "anus", "clit", "labia",
)


def _is_soft_body_physics_bone(name: str) -> bool:
    """True for 3BA/UBE soft-body JIGGLE bones (breast/butt/belly/genital).

    Distinct from hard skeleton bones: although these ARE part of the body
    skeleton, a garment's own HDT-SMP xml drives them and seeds their rest from
    the NIF bind. So -- unlike a hard bone, whose flat NIF transform the actor's
    live skeleton overrides at runtime -- a soft-body bone left flat/missing in a
    physics NIF makes that body region's cloth collapse to the origin. They must
    therefore be recreated at their SOURCE bind (like a custom physics chain),
    not added flat by add_bone. None of these substrings collide with a hard
    skeleton bone name (Spine/Pelvis/Thigh/...)."""
    n = name.lower()
    return any(k in n for k in _SOFT_BODY_PHYSICS_BONE_KEYWORDS)


_GENITAL_ANATOMY_KEYWORDS = (
    "clit", "pussy", "vagin", "anus", "labia", "vulva", "penis", "scrotum",
)


def _is_genital_anatomy_bone(name: str) -> bool:
    """True for genital anatomy bones (clitoral/pussy/vagina/anus/...). These do
    NOT exist on the UBE body skeleton, so a converted armor that carries SOURCE
    weights to them has those verts resolve to the ORIGIN at runtime -> they
    spike through the floor (wolf Greaves: 8 verts at ~13% Clitoral1 over ~84%
    Pelvis were still visibly pulled to the ground). Narrow on purpose: does NOT
    match breast/butt/belly (those ARE bone-driven on UBE)."""
    n = (name or "").lower()
    return any(k in n for k in _GENITAL_ANATOMY_KEYWORDS)


def _strip_genital_weights_map(weights_map):
    """Drop weights to genital anatomy bones from a {bone: [(vert,w)]} map and
    renormalize each affected vert's REMAINING bones to sum 1.0 (fallback NPC
    Pelvis if a vert was genital-only). Genital bones resolve to the origin on
    UBE actors, so ANY weight on them pulls the vert through the floor; the
    earlier fix stopped the converter ADDING genital weights but a source mesh
    can still carry them (vanilla Companions wolf armor did). General -- no
    per-armor logic. Returns the input unchanged when no genital bone is present
    (zero overhead / zero regression for the overwhelmingly common case)."""
    gset = {b for b in weights_map if _is_genital_anatomy_bone(b)}
    if not gset:
        return weights_map
    other: "dict" = {}        # vert -> {bone: w}  (non-genital only)
    affected: "set" = set()
    for bn, pairs in weights_map.items():
        for i, w in pairs:
            iv, fw = int(i), float(w)
            if bn in gset:
                if fw > 0.0:
                    affected.add(iv)
            elif fw > 0.0:
                d = other.setdefault(iv, {})
                d[bn] = d.get(bn, 0.0) + fw
    if not affected:
        return {b: p for b, p in weights_map.items() if b not in gset}
    PELVIS = "NPC Pelvis [Pelv]"
    # Only fall a genital-ONLY vert back to Pelvis when the shape ALREADY carries
    # Pelvis: _install_skin sets a bone's skin-to-bone xform only for bones the
    # shape had, so a Pelvis added here on a Pelvis-less shape has no STB -> the
    # vert skins to the origin (spike). When Pelvis is absent, leave the vert
    # zero-weight; `_fill_zero_weight_verts` (the very next step in _install_skin)
    # gives it its nearest weighted vert's bones -- which DO have valid STBs.
    has_pelvis = PELVIS in weights_map
    norm: "dict" = {}         # affected vert -> {bone: renormalized w}
    for v in affected:
        rest = other.get(v) or {}
        s = sum(rest.values())
        if s > 1e-6:
            norm[v] = {b: w / s for b, w in rest.items()}
        elif has_pelvis:
            norm[v] = {PELVIS: 1.0}
        else:
            norm[v] = {}      # left for _fill_zero_weight_verts (no-STB spike guard)
    out: "dict" = {}
    _bones_iter = (set(weights_map) - gset) | ({PELVIS} if has_pelvis else set())
    for bn in _bones_iter:
        pairs = [(int(i), float(w)) for i, w in weights_map.get(bn, [])
                 if int(i) not in affected]
        for v in affected:
            nw = norm[v].get(bn)
            if nw and nw > 0.0:
                pairs.append((v, nw))
        if pairs:
            out[bn] = pairs
    return out


_GARMENT_CHAIN_NIF_CACHE: "dict" = {}


def _nif_has_garment_chain(src_nif) -> bool:
    """True if the source NIF is an HDT-SMP physics GARMENT -- some shape is skinned
    to a custom (non-skeleton) chain bone that is NOT a soft-body jiggle bone
    (skirt/cape/tail/cloth/stabilizer chains). Soft-body jiggle bones
    (breast/butt/belly) are EXCLUDED on purpose: the converter grafts those onto
    plain conforming armour too, so counting them would make every fitted cuirass
    read as a physics garment. Cached per source filepath.

    Used by _strip_jiggle_weights_map to decide whether to strip the GRAFTED
    soft-body weights from EVERY shape of the garment. On the UBE actor the body's
    breast/butt/belly jiggle drags the garment's rigid plates and destabilises its
    physics chain (the dwarven pull -- the gold build never grafted them). On plain
    (non-garment) armour the strip stays leg-plate-only, so breast/belly jiggle
    conformance is preserved. #garment-softbody-strip"""
    key = None
    try:
        key = getattr(src_nif, "filepath", None) or None
    except Exception:
        key = None
    if key is None:
        key = id(src_nif)
    cached = _GARMENT_CHAIN_NIF_CACHE.get(key)
    if cached is not None:
        return cached
    result = False
    try:
        allb: "set" = set()
        for _sh in src_nif.shapes:
            allb |= set(_sh.bone_names or [])
        for b in allb:
            if (not _is_skeleton_bone(b)) and (not _is_soft_body_physics_bone(b)):
                result = True
                break
    except Exception:
        result = False
    _GARMENT_CHAIN_NIF_CACHE[key] = result
    return result


def _strip_jiggle_weights_map(weights_map, src_bones=None, force=False):
    """Drop soft-body JIGGLE scale-bone weights (breast/butt/belly) the converter
    grafted, and renormalize each affected vert's remaining bones to 1.0 (Pelvis
    fallback). On the UBE actor the body's breast/butt/belly physics jiggle drags a
    rigid plate down and collapses it; on a physics garment it also destabilises the
    skirt/cloth chain (the dwarven pull -- measured: real Breast/Butt/Belly weight
    on DwarvenArmorF the gold-standard build never added).

    Two modes:
      * force=True  (physics GARMENT, see _nif_has_garment_chain): strip the jiggle
        bones the converter GRAFTED -- i.e. NOT present in `src_bones` (the source
        shape's own bones) -- from EVERY shape. This exactly reverts to the gold
        (pre-graft) skinning that holds the chain, while KEEPING any legitimate
        source jiggle weight (a real chest-cloth shape that physics-drives off a
        breast bone). Soft conformance is sacrificed on garment plates only.
      * force=False (plain armour): on a leg-bone-dominant shape (greaves / leggings
        / pants / stockings) strip only the GRAFTED jiggle bones (absent from
        `src_bones`) -- so a rigid metal plate the converter grafted breast/butt
        weight onto reverts (no collapse), while FITTED LEG CLOTH that already had
        SOURCE butt/belly weight KEEPS it (else a skin-tight pant/stocking goes
        rigid and the jiggling UBE body clips straight through it). NO-OP for
        torso/soft armour -> it KEEPS jiggle for breast/belly conformance.

    add_scale_bone_weights suppresses leg-plate jiggle up front; the M6 reskin does
    NOT, so this post-pass closes the gap regardless of which path added the weight.
    #legplate-jiggle #garment-softbody-strip"""
    # Dominant authored bone per vert -> is this shape majority rigid-leg?
    dom: "dict" = {}
    for bn, pairs in weights_map.items():
        for i, w in pairs:
            iv, fw = int(i), float(w)
            cur = dom.get(iv)
            if cur is None or fw > cur[1]:
                dom[iv] = (bn, fw)
    if not dom:
        return weights_map
    if force:
        # Garment: strip ONLY converter-grafted jiggle bones (absent from source).
        _srcb = src_bones or set()
        jset = {b for b in weights_map
                if _is_physics_jiggle_scale_bone(b) and b not in _srcb}
    else:
        leg_dom = sum(1 for (bn, _w) in dom.values() if _is_leg_rigid_bone(bn))
        if leg_dom <= 0.5 * len(dom):
            return weights_map  # not a rigid leg plate -> keep jiggle (conformance)
        # Strip only GRAFTED jiggle (absent from source) -- a rigid greave the
        # converter grafted jiggle onto reverts, but fitted leg CLOTH that had
        # SOURCE butt/belly jiggle keeps it so it conforms to the jiggling body.
        _srcb = src_bones or set()
        jset = {b for b in weights_map
                if _is_physics_jiggle_scale_bone(b) and b not in _srcb}
    if not jset:
        return weights_map
    other: "dict" = {}
    affected: "set" = set()
    for bn, pairs in weights_map.items():
        for i, w in pairs:
            iv, fw = int(i), float(w)
            if bn in jset:
                if fw > 0.0:
                    affected.add(iv)
            elif fw > 0.0:
                d = other.setdefault(iv, {})
                d[bn] = d.get(bn, 0.0) + fw
    if not affected:
        return {b: p for b, p in weights_map.items() if b not in jset}
    PELVIS = "NPC Pelvis [Pelv]"
    # Same no-STB spike guard as _strip_genital_weights_map: only fall a
    # jiggle-ONLY vert back to Pelvis when the shape ALREADY carries Pelvis
    # (else _install_skin's add_bone gives Pelvis no skin-to-bone xform and the
    # vert skins to the origin -> floor spike). With no Pelvis, leave the vert
    # zero-weight for _fill_zero_weight_verts to reassign to a valid-STB bone.
    has_pelvis = PELVIS in weights_map
    norm: "dict" = {}
    for v in affected:
        rest = other.get(v) or {}
        s = sum(rest.values())
        if s > 1e-6:
            norm[v] = {b: w / s for b, w in rest.items()}
        elif has_pelvis:
            norm[v] = {PELVIS: 1.0}
        else:
            norm[v] = {}
    out: "dict" = {}
    for bn in (set(weights_map) - jset) | ({PELVIS} if has_pelvis else set()):
        pairs = [(int(i), float(w)) for i, w in weights_map.get(bn, [])
                 if int(i) not in affected]
        for v in affected:
            nw = norm[v].get(bn)
            if nw and nw > 0.0:
                pairs.append((v, nw))
        if pairs:
            out[bn] = pairs
    return out


# ----- Fitted-cloth body conform --------------------------------------------
# A skin-tight garment (leggings, bodysuit) must deform WITH the body or the body clips
# through it where a limb swings most. The body-blend closes the gross mismatch, but a
# residual survives -- cosine similarity is blind to a single-bone gap (54% vs the body's
# 65% leg-follow still scores ~0.99). This conforms the divergent verts of garment-class
# shapes to the body's per-vert skinning, gated per-bone so matched verts are untouched.
# "Garment" is detected, not hardcoded: carries jiggle weight + not a physics chain +
# hugs the body. Per-vert it only shrinks the bone set (partition-safe).
# [DESIGN: Leg-plate bend / butt-jiggle conform]
CONFORM_FITTED_CLOTH = (
    os.environ.get("CBBE2UBE_NO_CONFORM", "").strip().lower()
    not in ("1", "true", "yes", "on")
)
# Tunables (env-overridable). Validated on a pantyhose: 727/3846 verts conformed,
# inner-back-thigh leg-follow 54% -> 65% (body ~65-71%); a rigid greave: 0 verts.
_CONFORM_FIT_PROX = float(os.environ.get("CBBE2UBE_CONFORM_FIT_PROX", "2.0"))
_CONFORM_VERT_PROX = float(os.environ.get("CBBE2UBE_CONFORM_VERT_PROX", "6.0"))
_CONFORM_DELTA = float(os.environ.get("CBBE2UBE_CONFORM_DELTA", "0.08"))
_CONFORM_BLEND = float(os.environ.get("CBBE2UBE_CONFORM_BLEND", "0.90"))
_CONFORM_FIT_FRAC = float(os.environ.get("CBBE2UBE_CONFORM_FIT_FRAC", "0.90"))
_CONFORM_CHAIN_MAX = float(os.environ.get("CBBE2UBE_CONFORM_CHAIN_MAX", "0.05"))
_CONFORM_MIN_JIGGLE_VERTS = 8
# Shapes the leg/butt/chest conform+graft passes skip: the body, colliders,
# virtual/ref shapes, and draping-cloth garment names (robe/cloak/...) that may be
# runtime-global SMP cloth. "skirt" excluded on purpose (rigid metal tassets want
# the conform).  [DESIGN: HDT-SMP physics-cloth preservation]
_CONFORM_SKIP_NAMES = ("baseshape", "3ba", "virtual", "col", "ground", "ref",
                       "robe", "cloak", "cape", "dress", "gown", "sarong",
                       "loincloth")

# Graft a share of the body's jiggle onto a fitted garment that hugs a jiggling
# region but carries none of its own, so it follows the bounce instead of letting the
# body poke through. Default on; CBBE2UBE_NO_JIGGLE_TRANSFER=1 off, _FACTOR (0..1)
# scales it.  [DESIGN: Leg-plate bend / butt-jiggle conform]
TRANSFER_BODY_JIGGLE = (
    os.environ.get("CBBE2UBE_NO_JIGGLE_TRANSFER", "").strip().lower()
    not in ("1", "true", "yes", "on"))
_JIGGLE_TRANSFER_FACTOR = float(
    os.environ.get("CBBE2UBE_JIGGLE_TRANSFER_FACTOR", "0.85"))

# Make a rigid leg plate track the body's leg bend: match each leg vert's Thigh/Calf
# split to its nearest body vert and graft the body's detail bones (Front/Rear thigh,
# rear calf) so the plate flexes with the thigh instead of the body poking through.
# Never moves a vert or adds a jiggle bone. Default on; CBBE2UBE_NO_LEG_BEND_MATCH=1
# off.  [DESIGN: Leg-plate bend / butt-jiggle conform]
MATCH_RIGID_LEG_BEND = (
    os.environ.get("CBBE2UBE_NO_LEG_BEND_MATCH", "").strip().lower()
    not in ("1", "true", "yes", "on"))
# Per leg: the prime bend bones (thigh/calf, already on the armor) and the skeleton
# DETAIL bones (front/rear thigh, rear calf) -- each paired with the existing leg bone
# its grafted STB is ANCHORED to (front/rear thigh -> thigh; rear calf -> calf).
_LEG_DEFORM_BONES = (
    {"thigh": "NPC L Thigh [LThg]", "calf": "NPC L Calf [LClf]",
     "detail": (("NPC L FrontThigh", "NPC L Thigh [LThg]"),
                ("NPC L RearThigh", "NPC L Thigh [LThg]"),
                ("NPC L RearCalf [LrClf]", "NPC L Calf [LClf]"))},
    {"thigh": "NPC R Thigh [RThg]", "calf": "NPC R Calf [RClf]",
     "detail": (("NPC R FrontThigh", "NPC R Thigh [RThg]"),
                ("NPC R RearThigh", "NPC R Thigh [RThg]"),
                ("NPC R RearCalf [RrClf]", "NPC R Calf [RClf]"))},
)
_LEG_DETAIL_BONE_NAMES = tuple(
    b for leg in _LEG_DEFORM_BONES for b, _anc in leg["detail"])
# All leg-deform bone names (thigh+calf+detail) -- the body STBs we cache.
_LEG_ALL_DEFORM_NAMES = tuple({
    *(leg["thigh"] for leg in _LEG_DEFORM_BONES),
    *(leg["calf"] for leg in _LEG_DEFORM_BONES),
    *_LEG_DETAIL_BONE_NAMES})
_LEG_BEND_MASS_MIN = float(os.environ.get("CBBE2UBE_LEG_BEND_MASS_MIN", "0.15"))
_LEG_BEND_PROX = float(os.environ.get("CBBE2UBE_LEG_BEND_PROX", "3.0"))
# The leg/butt/chest match reads the body distribution over the K NEAREST body verts
# (averaged), not the single nearest. A single-nearest match flips its result on a
# sub-unit shift in the armor mesh -- and the compiled exe's warp math differs from the
# interpreted source by ~0.4u (float non-determinism), which amplified into a ~3x weaker
# leg graft (rear-thigh clip after a reconvert). Averaging over a small neighbourhood
# samples essentially the same body region either way, so the graft is stable.
_LEG_MATCH_K = int(os.environ.get("CBBE2UBE_LEG_MATCH_K", "6"))
# Z-tapered conform strength (knee crease ~z34-40, thigh ~z42-64). Full strength through
# the knee (<= _LEG_BEND_MAX_Z), ramp down to _LEG_BEND_THIGH_STRENGTH across the thigh
# (to _LEG_BEND_THIGH_Z), stop above _LEG_BEND_CUTOFF_Z (hip/butt untouched). The reduced
# thigh strength avoids over-rotating the larger-radius plate into a static bulge while
# still giving the rear thigh flex when moving.
# [DESIGN: Leg-plate bend / butt-jiggle conform]
_LEG_BEND_MAX_Z = float(os.environ.get("CBBE2UBE_LEG_BEND_MAX_Z", "41.0"))       # full-strength knee ceiling
_LEG_BEND_THIGH_Z = float(os.environ.get("CBBE2UBE_LEG_BEND_THIGH_Z", "58.0"))   # taper end (reaches min strength)
_LEG_BEND_THIGH_STRENGTH = float(os.environ.get("CBBE2UBE_LEG_BEND_THIGH_STRENGTH", "0.40"))  # min (thigh) strength
_LEG_BEND_CUTOFF_Z = float(os.environ.get("CBBE2UBE_LEG_BEND_CUTOFF_Z", "66.0")) # above this: no LEG conform (handed to the butt pass)
# BUTT pass: a RIGID one-piece plate covering the glutes (z ~60-78) often weights its
# outer butt too much to the Thigh and too little to the Pelvis vs the body, so when the
# pelvis moves the body's butt follows it but the plate lags -> the outer butt pokes
# through when moving. This is SKELETAL (the body's butt JIGGLE bones carry only ~0.03),
# so we fix it with a pure Thigh<->Pelvis REBALANCE among bones the plate ALREADY has --
# NO add_bone, NO jiggle graft (the plate stays rigid). Reduced strength for the same
# larger-radius overshoot reason as the leg. Trapezoid by world-Z (ramp in/out so no
# seam, and we never touch the lower back/spine above _BUTT_Z_HI). Tunable.
_BUTT_MATCH = (os.environ.get("CBBE2UBE_NO_BUTT_MATCH", "").strip().lower()
               not in ("1", "true", "yes", "on"))
_BUTT_Z_LO = float(os.environ.get("CBBE2UBE_BUTT_Z_LO", "60.0"))     # ramp-in start
_BUTT_Z_HI = float(os.environ.get("CBBE2UBE_BUTT_Z_HI", "78.0"))     # ramp-out end (above = spine/back, left alone)
_BUTT_RAMP = float(os.environ.get("CBBE2UBE_BUTT_RAMP", "4.0"))      # ramp width at each end
_BUTT_STRENGTH = float(os.environ.get("CBBE2UBE_BUTT_STRENGTH", "0.80"))  # peak rebalance strength
# BUTT-JIGGLE transfer: after the skeletal Thigh<->Pelvis rebalance, the body's outer butt
# still physically JIGGLES (the NPC L/R Butt bones carry ~0.03-0.05 there) while the rigid
# plate does not -> the bounce grazes through. Graft the body's butt-jiggle weight onto the
# plate's butt so it bounces WITH the body (matched -> maintains the offset -> no clip AND
# no rest-float). MATCHED not exaggerated (the plate moves exactly as much as the bare body
# would there, so it stays subtle), and CAPPED so a high-jiggle source can't make the metal
# rubbery. Routed through the SAME add_bone save/restore + Pelvis-anchored STB graft as the
# leg detail bones. User-chosen 2026-06-30 ("jiggle with the body").
_BUTT_JIGGLE = (os.environ.get("CBBE2UBE_NO_BUTT_JIGGLE", "").strip().lower()
                not in ("1", "true", "yes", "on"))
_BUTT_JIGGLE_BONES = ("NPC L Butt", "NPC R Butt")
_BUTT_PELVIS = "NPC Pelvis [Pelv]"                                  # the jiggle bones' graft anchor
_BUTT_JIGGLE_STRENGTH = float(os.environ.get("CBBE2UBE_BUTT_JIGGLE_STRENGTH", "1.0"))  # full match -> tracks body
_BUTT_JIGGLE_CAP = float(os.environ.get("CBBE2UBE_BUTT_JIGGLE_CAP", "0.15"))  # max grafted jiggle/bone (subtle)
# The Thigh<->Pelvis REBALANCE half of the butt match is DEFAULT-ON: it is the ORIGINAL,
# proven behavior (the user-approved "looks good" New Leather Armor was made with it). A
# 2026-07-08 attempt to blame it for a "coverage regression" and default it off was a
# MISDIAGNOSIS -- the real regression was elsewhere (see [[project_newleather_working_recipe]]).
# Opt out only if a specific armor needs it: CBBE2UBE_BUTT_REBALANCE=0.
_BUTT_REBALANCE = (os.environ.get("CBBE2UBE_BUTT_REBALANCE", "1").strip().lower()
                   in ("1", "true", "yes", "on"))
# Wider than the leg prox (3.0): the outer-butt plate stands ~5u off the body, so the
# leg prox misses ~20% of the glutes. Widening here (not the leg pass) is safe -- a
# Pelvis<->Thigh rebalance can't overshoot the body's own ratio.
_BUTT_PROX = float(os.environ.get("CBBE2UBE_BUTT_PROX", "5.0"))
_BUTT_MATCH_BONES = ("NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]")
# Chest/breast-jiggle transfer -- the upper-body mirror of the butt-jiggle graft, onto
# a rigid Spine2-dominant chest plate. Jiggle-only (no skeletal rebalance), capped low
# so a metal cuirass doesn't bounce like flesh, self-gated to the front chest. Default
# on; CBBE2UBE_NO_CHEST_JIGGLE=1 off.  [DESIGN: Leg-plate bend / butt-jiggle conform]
_CHEST_JIGGLE = (os.environ.get("CBBE2UBE_NO_CHEST_JIGGLE", "").strip().lower()
                 not in ("1", "true", "yes", "on"))
_CHEST_JIGGLE_BONES = ("L Breast01", "L Breast02", "L Breast03",
                       "R Breast01", "R Breast02", "R Breast03")
_CHEST_ANCHOR = "NPC Spine2 [Spn2]"                                 # breast graft anchor (plate has it)
_CHEST_Z_LO = float(os.environ.get("CBBE2UBE_CHEST_Z_LO", "88.0"))   # ramp-in start
_CHEST_Z_HI = float(os.environ.get("CBBE2UBE_CHEST_Z_HI", "102.0"))  # ramp-out end (above = neck/clav)
_CHEST_RAMP = float(os.environ.get("CBBE2UBE_CHEST_RAMP", "4.0"))
_CHEST_JIGGLE_STRENGTH = float(os.environ.get("CBBE2UBE_CHEST_JIGGLE_STRENGTH", "1.0"))
_CHEST_JIGGLE_CAP = float(os.environ.get("CBBE2UBE_CHEST_JIGGLE_CAP", "0.15"))  # LOW total cap: metal stays rigid
# Per-bone clamp strictly UNDER the rigid-gate's 0.1 jiggle threshold: a single grafted
# breast bone must never reach 0.1, or a RE-RUN's rigid-gate would count the plate as
# "jiggling" and skip the whole shape (non-idempotent). The real body spreads breast weight
# across 6 bones (~0.04 each at the cap), so this only bites a pathological single-bone body.
_CHEST_JIGGLE_PERBONE = float(os.environ.get("CBBE2UBE_CHEST_JIGGLE_PERBONE", "0.09"))
_CHEST_PROX = float(os.environ.get("CBBE2UBE_CHEST_PROX", "5.0"))
_BODY_CONFORM_CACHE: "dict" = {}
_BODY_LEG_DETAIL_CACHE: "dict" = {}


def _chest_match_strength(z: float) -> float:
    """Trapezoid chest-graft strength by world Z: 0 outside [_CHEST_Z_LO, _CHEST_Z_HI],
    ramping in/out over _CHEST_RAMP so the neck/clavicle above and the belly below are never
    touched. Peak = _CHEST_JIGGLE_STRENGTH. Pure."""
    if z <= _CHEST_Z_LO or z >= _CHEST_Z_HI:
        return 0.0
    r = max(1e-6, _CHEST_RAMP)
    up = (z - _CHEST_Z_LO) / r
    down = (_CHEST_Z_HI - z) / r
    return min(1.0, _CHEST_JIGGLE_STRENGTH) * max(0.0, min(1.0, up, down))


def _butt_match_strength(z: float) -> float:
    """Trapezoid butt-rebalance strength by world Z: 0 outside [_BUTT_Z_LO, _BUTT_Z_HI],
    ramping 0 -> _BUTT_STRENGTH over _BUTT_RAMP at the bottom, flat across the glutes,
    ramping back to 0 at the top (so the lower back/spine is never touched). Pure."""
    if z <= _BUTT_Z_LO or z >= _BUTT_Z_HI:
        return 0.0
    peak = _BUTT_STRENGTH
    r = max(1e-6, _BUTT_RAMP)
    up = (z - _BUTT_Z_LO) / r            # 0..1 over the bottom ramp
    down = (_BUTT_Z_HI - z) / r          # 0..1 over the top ramp
    return peak * max(0.0, min(1.0, up, down))


def _leg_bend_strength(z: float) -> float:
    """Per-vert conform strength by world Z: 1.0 through the knee (z <= _LEG_BEND_MAX_Z),
    linearly ramped down to _LEG_BEND_THIGH_STRENGTH across the thigh, and 0.0 above
    _LEG_BEND_CUTOFF_Z (leave the hip/butt). The reduced thigh strength gives partial
    rear-thigh flex without the plate-overshoot that a full conform causes (the plate is
    at a larger radius than the body). Pure; unit-tested."""
    if z <= _LEG_BEND_MAX_Z:
        return 1.0
    if z >= _LEG_BEND_CUTOFF_Z:
        return 0.0
    s_min = _LEG_BEND_THIGH_STRENGTH
    if z >= _LEG_BEND_THIGH_Z:
        return s_min
    span = _LEG_BEND_THIGH_Z - _LEG_BEND_MAX_Z
    if span <= 1e-6:
        return s_min
    frac = (z - _LEG_BEND_MAX_Z) / span          # 0 at knee ceiling -> 1 at thigh_z
    return 1.0 + frac * (s_min - 1.0)            # 1.0 -> s_min


def _stb_to_mat4(tb):
    """4x4 (rotation+translation, scalar scale folded in) for a skin-to-bone
    TransformBuf + the matrix-proto object for round-tripping via from_matrix.
    Returns (M, proto) or (None, None). Mirrors _adjust_skin_to_bone_baked."""
    try:
        pm = tb.to_matrix()
        M = np.array(pm.to_matrix()._array if hasattr(pm, "to_matrix") else pm._array,
                     dtype=np.float64)
        s = float(tb.scale)
        if abs(abs(float(np.linalg.det(M[:3, :3]))) - 1.0) < 1e-2:
            M[:3, :3] = M[:3, :3] * s
        return M, pm
    except Exception:
        return None, None


def _reanchor_stb_mat4(m_detail_body, m_anchor_body, m_anchor_armor):
    """Pure 4x4 re-anchor (extracted for testability):
        STB_detail_armor = STB_detail_body @ inv(STB_anchor_body) @ STB_anchor_armor
    Returns the 4x4 np.ndarray, or None if degenerate (non-invertible / non-finite).
    By construction `result @ inv(m_anchor_armor) == m_detail_body @ inv(m_anchor_body)`
    -- the detail-relative-to-anchor bind is preserved into the armor's skin space,
    which is exactly the consistency that prevents the in-game spike (copying the
    body's ABSOLUTE STB onto an armor with a different bind convention tore verts)."""
    try:
        m = m_detail_body @ np.linalg.inv(m_anchor_body) @ m_anchor_armor
        return m if np.isfinite(m).all() else None
    except Exception:
        return None


def _derive_anchored_stb(m_detail_body, m_anchor_body, m_anchor_armor, proto):
    """Re-anchor a grafted detail bone's skin-to-bone xform to the ARMOR's own bind
    (see _reanchor_stb_mat4) and return it as a TransformBuf, or None if degenerate.
    So the detail bone contributes the SAME world position as its anchor (Thigh/Calf)
    at bind in the ARMOR's skin space -- preventing the tear that copying the body's
    absolute STB caused (the in-game explosion)."""
    m_new = _reanchor_stb_mat4(m_detail_body, m_anchor_body, m_anchor_armor)
    if m_new is None:
        return None
    try:
        return _pynifly().TransformBuf.from_matrix(type(proto)(m_new.tolist()))
    except Exception:
        return None


def _body_conform_ref(weight: str):
    """Lazy (verts_world, per_vert_weights, body_bones, kdtree) for the UBE body
    at `weight` ('_0'/'_1'); None if the body or scipy is unavailable. Cached."""
    if weight in _BODY_CONFORM_CACHE:
        return _BODY_CONFORM_CACHE[weight]
    out = None
    try:
        from scipy.spatial import cKDTree
        p = _find_ube_femalebody(weight) or _find_ube_femalebody("_1")
        if p is not None and Path(p).is_file():
            pyn = _pynifly()
            nf = pyn.NifFile(filepath=str(p))
            body = max(nf.shapes, key=lambda s: len(s.verts))
            g2s = _shape_global_to_skin(body)
            V = _verts_skin_to_world(np.asarray(body.verts, np.float64), g2s)
            n = len(V)
            pv = [dict() for _ in range(n)]
            for b, pairs in (body.bone_weights or {}).items():
                for vi, w in pairs:
                    iv = int(vi)
                    if 0 <= iv < n:
                        pv[iv][b] = pv[iv].get(b, 0.0) + float(w)
            out = (V, pv, set(body.bone_names), cKDTree(V))
    except Exception:
        out = None
    _BODY_CONFORM_CACHE[weight] = out
    return out


_BODY_JIGGLE_REF_CACHE: dict = {}
# The body's STB TransformBufs are native references INTO its NifFile; keep the
# NifFile alive (the read alone would let it be GC'd, leaving the cached STBs
# dangling -> they set identity -> the grafted bone spikes to the origin).
_BODY_JIGGLE_NF_KEEPALIVE: list = []


def _body_jiggle_ref(weight: str):
    """({jiggle_bone: skin_to_bone_xform}, body_g2s_is_identity) for the UBE body
    at `weight`, or None. The jiggle bones (butt/belly/breast) are the body-only
    bones a fitted garment lacks; their bind transforms let us graft them onto a
    hugging garment WITHOUT a spike (a bone added with no STB skins to the origin
    -- the _install_skin / audit-#4 class). Only valid when the body's
    global-to-skin is identity (then the STB copies straight to an identity-g2s
    garment); the caller skips the graft otherwise. Cached."""
    if weight in _BODY_JIGGLE_REF_CACHE:
        return _BODY_JIGGLE_REF_CACHE[weight]
    out = None
    try:
        p = _find_ube_femalebody(weight) or _find_ube_femalebody("_1")
        if p is not None and Path(p).is_file():
            pyn = _pynifly()
            nf = pyn.NifFile(filepath=str(p))
            body = max(nf.shapes, key=lambda s: len(s.verts))
            g2s = _shape_global_to_skin(body)
            body_ident = (g2s is None) or _g2s_is_identity(g2s)
            stbs: dict = {}
            for bn in (body.bone_names or []):
                if _is_physics_jiggle_scale_bone(bn):
                    try:
                        stbs[bn] = body.get_shape_skin_to_bone(bn)
                    except Exception:
                        pass
            _BODY_JIGGLE_NF_KEEPALIVE.append(nf)   # keep native STBs valid
            out = (stbs, body_ident)
    except Exception:
        out = None
    _BODY_JIGGLE_REF_CACHE[weight] = out
    return out


def _jiggle_transfer_vert(dv: dict, bd_jig: dict, closeness: float,
                          factor: float):
    """Pure per-vert jiggle graft (extracted for testability). Give the garment
    vert a jiggle weight of `factor * closeness * body_weight` on each of the body
    vert's jiggle bones `bd_jig`, and scale the vert's existing (leg) weight down
    to fill the remainder so the weights still sum to 1. The grafted jiggle is thus
    a REAL share of the vert's skinning -- it follows the body's jiggle at a
    comparable amplitude -- not a token amount renormalization would shrink away.
    Returns (new_weights, set_of_newly_added_bones), or (None, set()) when nothing
    new is grafted (no body jiggle here, far vert, or all targets negligible)."""
    if not bd_jig or closeness <= 0.0:
        return None, set()
    targets = {jb: wb * factor * closeness for jb, wb in bd_jig.items()}
    targets = {jb: t for jb, t in targets.items() if t > 1e-3}
    added = {jb for jb in targets if jb not in dv}
    if not added:
        return None, set()       # only reinforces existing jiggle -> leave it
    tot = sum(targets.values())
    if tot >= 0.95:              # never let the graft dominate the vert
        sc = 0.95 / tot
        targets = {jb: t * sc for jb, t in targets.items()}
        tot = sum(targets.values())
    remain = max(0.0, 1.0 - tot)
    base_sum = sum(w for b, w in dv.items() if b not in targets)
    new: dict = {}
    if base_sum > 0:
        for b, w in dv.items():
            if b not in targets:
                new[b] = w / base_sum * remain
    for jb, t in targets.items():
        new[jb] = new.get(jb, 0.0) + t
    new = {b: w for b, w in new.items() if w > 1e-4}
    if not new:
        return None, set()
    return new, added


def _conform_blend_vert(dv: dict, bd: dict, blend: float, delta: float):
    """Pure per-vert conform decision (extracted for testability). Blend the
    vert's weights `dv` toward the body vert's weights `bd` by `blend`, KEEPING
    the vert's bone set -- it can only SHRINK (no body-only bone is added), so
    partition bone-palettes stay valid. Returns the renormalized weight dict, or
    None to leave the vert untouched: no shared bone, already matched (max
    shared-bone |delta| <= `delta`), or a degenerate (zero-sum) blend."""
    shared = set(dv) & set(bd)
    if not shared:
        return None
    if max(abs(dv.get(b, 0.0) - bd.get(b, 0.0)) for b in shared) <= delta:
        return None
    new = {b: (1.0 - blend) * dv[b] + (blend * bd[b] if b in bd else 0.0)
           for b in dv}
    ss = sum(new.values())
    if ss <= 0:
        return None
    return {b: w / ss for b, w in new.items() if w / ss > 1e-4}


def _conform_fitted_to_body(dst_path, biped_slots: int = 0) -> int:
    """Conform skin-tight GARMENT shapes to the UBE body's per-vert skinning so
    the body doesn't clip through where a limb swings. On-disk post-pass; returns
    the number of verts conformed (0 = nothing touched). See CONFORM_FITTED_CLOTH
    for the detection gates -- rigid plate armor is excluded and stays rigid."""
    if not CONFORM_FITTED_CLOTH:
        return 0
    if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the clip class, and risky to soften
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _body_conform_ref(weight)
    if ref is None:
        return 0
    _Vb, body_w, _body_bones, tree = ref  # chain test uses _is_skeleton_bone now
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    # Precise SMP-collider exclusion. The _CONFORM_SKIP_NAMES substring gate below
    # only catches name-tagged colliders ("...Col..."); re-weighting an UNTAGGED
    # per-triangle collider would re-introduce the exact over-graft the reskin pass
    # is careful to avoid (the collider over-jiggles -> the cloth it stabilises
    # implodes / sinks). Read the collider set straight from the already-open nf so
    # there is NO second disk parse per armor. #smp-collider-graft
    collider_names = _hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _hdt_softbody_shape_names(dst_path, nif=nf)
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        if (s.name in collider_names or s.name in softbody_names
                or s.name in layered_cloth_names
                or any(k in nm for k in _CONFORM_SKIP_NAMES)):
            continue
        bw = s.bone_weights or {}
        # (a) CHEAP pre-gate: real soft-body jiggle weight -> deform-with-body
        # garment. Avoids building per-vert maps for rigid plate armor.
        jig = 0
        for b, pairs in bw.items():
            if _is_physics_jiggle_scale_bone(b):
                jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
                if jig >= _CONFORM_MIN_JIGGLE_VERTS:
                    break
        if jig < _CONFORM_MIN_JIGGLE_VERTS:
            continue
        try:
            V = np.asarray(s.verts, np.float64)
        except Exception:
            continue
        n = len(V)
        if n == 0:
            continue
        vw = [dict() for _ in range(n)]
        for b, pairs in bw.items():
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        # (b) not a physics-chain garment (SMP skirt/cloak). "Chain" = a CUSTOM
        # (non-skeleton) bone -- test the skeleton, NOT the body-MESH bone set,
        # which omits Foot/Hand/etc.: a long pant weighted to the foot bones would
        # otherwise read as a chain (chain_frac just over the gate) and be skipped.
        chain_frac = sum(1 for d in vw
                         if any(w > 0.1 and not _is_skeleton_bone(b)
                                for b, w in d.items())) / n
        if chain_frac > _CONFORM_CHAIN_MAX:
            continue
        g2s = _shape_global_to_skin(s)
        Vw = _verts_skin_to_world(V, g2s)
        d, idx = tree.query(Vw)
        # (c) HUGS the body (a flaring skirt/robe sits away -> excluded)
        if float((d < _CONFORM_FIT_PROX).mean()) < _CONFORM_FIT_FRAC:
            continue
        touched: "set" = set()
        conf = 0
        for i in range(n):
            if d[i] > _CONFORM_VERT_PROX:
                continue
            dv = vw[i]
            if not dv:
                continue
            if any(w > 0.1 and not _is_skeleton_bone(b) for b, w in dv.items()):
                continue  # custom-chain vert -> leave it (partition safety)
            bd = body_w[idx[i]]
            new = _conform_blend_vert(dv, bd, _CONFORM_BLEND, _CONFORM_DELTA)
            if new is None:
                continue
            touched |= set(dv)        # bones the vert had (may now lose this vert)
            vw[i] = new
            touched |= set(vw[i])     # bones it kept after the blend
            conf += 1
        if conf:
            dirty = True
            total += conf
            for bn in touched:
                # full rebuild from the complete per-vert map -> removals applied
                s.setShapeWeights(bn, [(i, vw[i][bn]) for i in range(n)
                                       if bn in vw[i] and vw[i][bn] > 1e-4])
    if dirty:
        # A re-save must never silently un-hide an SMP collision proxy (the "blue
        # body double"); re-assert the VirtualBody Hidden bit, as _reauthor does.
        _hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception:
            return 0
    return total


def _body_leg_detail_ref(weight: str):
    """({leg_bone: body skin-to-bone TransformBuf}, body_g2s_is_identity) for the UBE
    body's leg-deformation bones (thigh, calf, AND the detail bones FrontThigh/
    RearThigh/RearCalf). The detail STBs + the body's thigh/calf STBs let us
    RE-ANCHOR a grafted detail bone to the armor's own bind (see _derive_anchored_stb)
    -- the body STBs are inputs to the derivation, never copied raw onto the armor.
    Cached; the body NifFile is kept alive (shared keepalive) so the native STBs stay
    valid."""
    if weight in _BODY_LEG_DETAIL_CACHE:
        return _BODY_LEG_DETAIL_CACHE[weight]
    out = None
    try:
        p = _find_ube_femalebody(weight) or _find_ube_femalebody("_1")
        if p is not None and Path(p).is_file():
            pyn = _pynifly()
            nf = pyn.NifFile(filepath=str(p))
            body = max(nf.shapes, key=lambda s: len(s.verts))
            g2s = _shape_global_to_skin(body)
            body_ident = (g2s is None) or _g2s_is_identity(g2s)
            # leg-deform bones (thigh/calf/detail) + the butt- and chest-jiggle graft bones
            # and their anchors (Pelvis / Spine2) for _match_rigid_leg_bend_to_body's
            # butt- and chest-jiggle transfers.
            _want = (set(_LEG_ALL_DEFORM_NAMES) | set(_BUTT_JIGGLE_BONES) | {_BUTT_PELVIS}
                     | set(_CHEST_JIGGLE_BONES) | {_CHEST_ANCHOR})
            stbs: dict = {}
            for bn in (body.bone_names or []):
                if bn in _want:
                    try:
                        stbs[bn] = body.get_shape_skin_to_bone(bn)
                    except Exception:
                        pass
            _BODY_JIGGLE_NF_KEEPALIVE.append(nf)   # keep native STBs valid
            out = (stbs, body_ident)
    except Exception:
        out = None
    _BODY_LEG_DETAIL_CACHE[weight] = out
    return out


def _leg_deform_match_vert(dv: dict, bd: dict,
                           mass_min: float = _LEG_BEND_MASS_MIN,
                           strength: float = 1.0) -> "tuple":
    """Pure per-vert leg-deformation match (extracted for testability). For each leg the
    vert `dv` carries (Thigh+Calf mass >= mass_min) whose nearest body vert `bd` is itself
    a leg vert (its leg-bone mass >= 0.2), move the vert's weight on that leg's deformation
    bones -- Thigh, Calf, and the detail bones FrontThigh/RearThigh/RearCalf -- TOWARD the
    body vert's distribution (scaled to the vert's EXISTING leg mass). So the plate bends
    (Thigh:Calf) AND flexes its front/back (the detail bones) like the body. `strength`
    in [0,1] BLENDS between the vert's current split (0 = unchanged) and the body's full
    distribution (1 = full match): used to give the THIGH partial flex without the
    plate-overshoot a full match causes (the plate is at a larger radius than the body).
    MUTATES dv; the vert's TOTAL leg mass and every NON-leg bone are untouched. Returns
    (touched_bones, detail_bones_added): the detail bones the CALLER must give a
    re-anchored bind transform before they are valid."""
    touched: "set" = set()
    added: "set" = set()
    if strength <= 0.0:
        return touched, added
    strength = min(1.0, strength)
    for leg in _LEG_DEFORM_BONES:
        thg, clf = leg["thigh"], leg["calf"]
        all_bones = (thg, clf) + tuple(b for b, _a in leg["detail"])
        # mass over ALL of this leg's deform bones the vert has (NOT just Thigh+Calf) so a
        # re-run -- where the vert already carries grafted detail weight -- redistributes
        # the SAME total and is idempotent (else the blend toward a Thigh+Calf-only `full`
        # silently drops the existing detail weight = lost mass).
        mass = sum(dv.get(b, 0.0) for b in all_bones)
        if mass < mass_min:
            continue
        bdist = {b: bd.get(b, 0.0) for b in all_bones if bd.get(b, 0.0) > 1e-3}
        bmass = sum(bdist.values())
        if bmass < 0.2:
            continue  # nearest body vert isn't a leg vert here
        full = {b: mass * w / bmass for b, w in bdist.items()}     # full body match
        before = {b: dv.get(b, 0.0) for b in all_bones if dv.get(b, 0.0) > 1e-4}
        if strength >= 1.0:
            new = full
        else:
            # blend current split -> body match by `strength`; conserves leg mass (both
            # `before` and `full` sum to `mass`), drops near-zero entries.
            keys = set(full) | set(before)
            new = {}
            for b in keys:
                w = (1.0 - strength) * before.get(b, 0.0) + strength * full.get(b, 0.0)
                if w > 1e-4:
                    new[b] = w
        if (set(new) == set(before)
                and all(abs(new[b] - before[b]) <= 1e-3 for b in new)):
            continue  # already matched
        for b in all_bones:
            dv.pop(b, None)
        for b, w in new.items():
            dv[b] = w
        touched |= set(before) | set(new)
        added |= {b for b, _a in leg["detail"]} & set(new)
    return touched, added


def _butt_match_vert(dv: dict, bd: dict, strength: float = 1.0,
                     mass_min: float = _LEG_BEND_MASS_MIN,
                     jiggle: bool = False, jiggle_strength: float = 1.0,
                     jiggle_cap: float = _BUTT_JIGGLE_CAP,
                     rebalance: bool = True) -> "tuple":
    """Pure per-vert BUTT match. (1) If `rebalance`, REBALANCE the vert's split across the
    (L Thigh, R Thigh, Pelvis) bones it ALREADY has toward the body vert `bd`'s split, blended
    by `strength`. DEFAULT-OFF at the call site: on tight leg armor it drains Thigh weight onto
    the (static) Pelvis, so the plate stops following the thigh during the stride and the body
    pokes out (thigh-coverage loss -- the fix was to stop rebalancing). (2) If `jiggle`, GRAFT
    the body's butt-JIGGLE bones (NPC L/R Butt) at `jiggle_strength` of the body's weight
    (capped at `jiggle_cap`) so the plate's butt bounces WITH the body instead of being grazed
    by it. jiggle-only (rebalance off) keeps the base Thigh/Pelvis weights untouched -- the
    small jiggle draw is taken from them proportionally, mass conserved, idempotent. Returns
    (touched_bones, jiggle_bones_added): grafts the CALLER must give a Pelvis-anchored bind."""
    touched: "set" = set()
    added: "set" = set()
    if strength <= 0.0:
        return touched, added
    strength = min(1.0, strength)
    base = [b for b in _BUTT_MATCH_BONES if dv.get(b, 0.0) > 1e-4]
    if len(base) < 2:
        return touched, added  # need >=2 of (thigh/pelvis) present to anchor + conserve
    # Process the jiggle bones the body weights here (to graft) AND any the vert ALREADY
    # carries (so a re-run redistributes the same total = idempotent, never double-counts).
    jig = sorted(
        {b for b in _BUTT_JIGGLE_BONES if dv.get(b, 0.0) > 1e-4}
        | ({b for b in _BUTT_JIGGLE_BONES if bd.get(b, 0.0) > 1e-3}
           if jiggle and jiggle_strength > 0.0 else set()))
    allb = base + jig
    mass = sum(dv.get(b, 0.0) for b in allb)   # incl existing jiggle -> idempotent
    if mass < mass_min:
        return touched, added
    bdist = {b: bd.get(b, 0.0) for b in allb if bd.get(b, 0.0) > 1e-3}
    bmass = sum(bdist.values())
    if bmass < 0.2:
        return touched, added  # nearest body vert isn't a butt/pelvis vert
    full = {b: mass * bdist.get(b, 0.0) / bmass for b in allb}    # body match scaled to mass
    before = {b: dv.get(b, 0.0) for b in allb}
    new = {}
    for b in base:
        # rebalance toward the body split, OR (jiggle-only) keep the base weight
        # so Thigh/Pelvis coverage is preserved; the jiggle draw comes out below.
        new[b] = ((1.0 - strength) * before.get(b, 0.0) + strength * full.get(b, 0.0)
                  if rebalance else before.get(b, 0.0))
    for b in jig:
        # grafted from 0; match the body's weight (jiggle_strength), capped subtle
        new[b] = min(jiggle_cap, min(1.0, jiggle_strength) * full.get(b, 0.0))
    # Conserve the combined mass: any surplus/deficit (the jiggle drawn in, or rounding)
    # is taken from / returned to the anchor bones proportionally.
    deficit = mass - sum(new.values())
    asum = sum(new.get(b, 0.0) for b in base)
    if abs(deficit) > 1e-9 and asum > 1e-9:
        for b in base:
            new[b] += deficit * new[b] / asum
    if all(abs(new.get(b, 0.0) - before.get(b, 0.0)) <= 1e-3 for b in allb):
        return touched, added  # already matched
    for b in allb:
        if new.get(b, 0.0) > 1e-4:
            dv[b] = new[b]
        else:
            dv.pop(b, None)
    touched |= set(allb)
    added |= {b for b in jig if dv.get(b, 0.0) > 1e-4}
    return touched, added


def _chest_match_vert(dv: dict, bd: dict, strength: float = 1.0,
                      cap: float = _CHEST_JIGGLE_CAP,
                      anchor: str = _CHEST_ANCHOR) -> "tuple":
    """Pure per-vert CHEST/BREAST-jiggle graft. Give a rigid chest plate vert (Spine2-
    dominant) a SMALL share of the body vert `bd`'s breast-jiggle (L/R Breast01/02/03) so it
    follows the bounce instead of being poked by it. JIGGLE-ONLY (no rebalance): the grafted
    weight is drawn from the `anchor` (Spine2), the TOTAL is capped at `cap` (the breast
    jiggle is large -- the cap keeps the metal mostly rigid), mass is conserved, idempotent.
    Self-gates to the front (a vert whose nearest body vert has no breast weight grafts
    nothing). Returns (touched, breast_bones_added) -- the grafts the caller must give a
    Spine2-anchored bind transform."""
    touched: "set" = set()
    added: "set" = set()
    if strength <= 0.0:
        return touched, added
    anc_w = dv.get(anchor, 0.0)
    present = [b for b in _CHEST_JIGGLE_BONES if dv.get(b, 0.0) > 1e-4]
    if anc_w <= 1e-4 and not present:
        return touched, added           # no Spine2 to draw from / nothing to manage
    body = {b: bd.get(b, 0.0) for b in _CHEST_JIGGLE_BONES if bd.get(b, 0.0) > 1e-3}
    # managed mass = anchor + any breast the vert ALREADY has (idempotent re-run)
    mass = anc_w + sum(dv.get(b, 0.0) for b in present)
    if mass < 1e-4:
        return touched, added
    bsum = sum(body.values())
    want = min(bsum * min(1.0, strength), cap, mass) if bsum > 0.0 else 0.0
    target = {b: want * body[b] / bsum for b in body} if bsum > 0.0 else {}
    # Per-bone clamp < the 0.1 rigid-gate threshold (re-run safety); the clamped-off weight
    # stays on the anchor (total just ends up a little lower), never redistributed away.
    for b in list(target):
        if target[b] > _CHEST_JIGGLE_PERBONE:
            target[b] = _CHEST_JIGGLE_PERBONE
    new_anchor = mass - sum(target.values())
    cur = {anchor: anc_w}
    for b in present:
        cur[b] = dv.get(b, 0.0)
    new = {anchor: new_anchor}
    for b in set(present) | set(target):
        new[b] = target.get(b, 0.0)
    if all(abs(new.get(k, 0.0) - cur.get(k, 0.0)) <= 1e-3 for k in set(new) | set(cur)):
        return touched, added           # already matched
    dv[anchor] = new_anchor
    touched.add(anchor)
    for b in set(present) | set(target):
        v = target.get(b, 0.0)
        if v > 1e-4:
            dv[b] = v
            if b in body:
                added.add(b)
        else:
            dv.pop(b, None)
        touched.add(b)
    return touched, added


def _is_fx_overlay_name(name: "str | None") -> bool:
    """Name heuristic for a glow/decal FX overlay shape ('MaleTorsoGlow', 'TorsoF:FX',
    'MiscMFx', 'DSkirt Glow'). Belt-and-suspenders for the effect-shader BUFFER check:
    some armors attach the effect shader to a shape AFTER the conform/jiggle passes run
    (a sub-shape/finalize path), so the buffer isn't visible at conform time and the graft
    slips through -> the 'TorsoF:FX' CTD class. The name is stable regardless of WHEN the
    shader is attached. Skipping a (rare) false positive only forgoes the conform on that
    shape -- never a crash. Tuned to NOT match legit shapes (ArmF/MiscF/GreaveF end in 'f',
    not 'fx')."""
    nm = (name or "").lower()
    return "glow" in nm or ":fx" in nm or nm.endswith("fx")


def _nif_has_fx_shape(nf) -> bool:
    """True if ANY shape in the NIF is an effect-shader/glow overlay. The post-conversion
    re-saving passes (conform/jiggle/leg-bend) must NOT touch such a NIF AT ALL -- not even
    a legit sibling shape -- because a pynifly RELOAD->modify->RE-SAVE round-trips the
    transplanted BSEffectShaderProperty CONTROLLER chain (_recreate_effect_shader) through
    pynifly's lossy controller read-back, corrupting it -> CTD on render, EVEN when the glow
    shape itself is never grafted (the 2nd 'MaleTorsoGlow' crash: a clean-glow output still
    crashed because the NIF was re-saved for its Greaves/torso). Leave the whole NIF as the
    main conversion wrote it."""
    try:
        for s in nf.shapes:
            if _shape_has_effect_shader(s) or _is_fx_overlay_name(s.name):
                return True
    except Exception:
        pass
    return False


def _shape_has_effect_shader(shape) -> bool:
    """True if the shape uses a BSEffectShaderProperty (an additive glow/decal overlay,
    e.g. the Daedric red glow). These carry a transplanted effect-shader + animation
    CONTROLLER chain (see _recreate_effect_shader); GRAFTING a bone onto one and re-saving
    corrupts that controller -> the engine calls a virtual through a dead pointer = CTD
    (the 'MaleTorsoGlow' BSEffectShaderProperty crash, 2026-06-30). The conform/jiggle passes
    must NEVER touch a glow overlay -- it isn't body armor and needs no weight conform."""
    try:
        sh = getattr(shape, "shader", None)
        props = getattr(sh, "properties", None) if sh is not None else None
        if props is None:
            return False
        pyn = _pynifly()
        eff = getattr(pyn.PynBufferTypes, "BSEffectShaderPropertyBufType", None)
        return eff is not None and getattr(props, "bufType", None) == eff
    except Exception:
        return False


def _match_rigid_leg_bend_to_body(dst_path, biped_slots: int = 0) -> int:
    """Conform a RIGID plate's deformation to the UBE body so it deforms/bounces WITH the
    body instead of staying stiff while the body pokes through. Complements
    _conform_fitted_to_body (jiggle-gated, SKIPS rigid plate); runs ONLY on the rigid plate
    it leaves alone. Four per-vert passes, each prox/world-Z gated (see the per-pass
    helpers), applied where the vert hugs the body:
      1. KNEE/THIGH (_leg_deform_match_vert): match the vert's leg-bone split to the body's
         FULL leg distribution -- Thigh:Calf bend + GRAFT the detail bones FrontThigh/
         RearThigh/RearCalf -- z-tapered FULL at the knee -> partial in the thigh (a full
         thigh match over-rotates the larger-radius plate = a static bulge).
      2. BUTT (_butt_match_vert rebalance): Thigh<->Pelvis rebalance among EXISTING bones so
         the outer butt tracks the pelvis instead of lagging it when moving.
      3. BUTT-JIGGLE (_butt_match_vert graft): graft the body's small butt-jiggle (NPC L/R
         Butt), matched + capped, so the rigid butt bounces with the body.
      4. CHEST (_chest_match_vert): graft the body's breast-jiggle (L/R Breast01/02/03)
         onto a rigid chest plate, matched but LOW-capped (the breast jiggle is large; keep
         the metal mostly rigid). Self-gates to the front via the body's breast weight.
    Every grafted bone's bind transform is RE-ANCHORED to the armor's OWN anchor bone
    (Thigh/Calf for detail, Pelvis for butt-jiggle, Spine2 for breast) via
    _derive_anchored_stb -- copying the body's absolute STB exploded the armor in-game.
    add-all-bones-first then set-STBs (a later add_bone resets earlier STBs, so save/restore
    every existing bone's STB); a bone we can't anchor folds its weight back into the anchor
    (no origin spike). Each pass conserves the managed mass and is idempotent. Never moves a
    vert (rest pose byte-identical). Eligible shapes: rigid (non-jiggling) leg armor
    (Thigh+Calf) OR rigid chest plate (Spine2). Returns the number of verts matched."""
    if not MATCH_RIGID_LEG_BEND:
        return 0
    if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the clip class
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _body_conform_ref(weight)
    dref = _body_leg_detail_ref(weight)
    if ref is None or dref is None:
        return 0
    _Vb, body_w, _bones, tree = ref
    body_stbs, _body_ident = dref
    # Body leg-bone STB matrices (anchor + detail) -- inputs to the re-anchoring.
    body_mat: dict = {}
    body_proto = None
    for bn, tb in body_stbs.items():
        m, proto = _stb_to_mat4(tb)
        if m is not None:
            body_mat[bn] = m
            if body_proto is None:
                body_proto = proto
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    collider_names = _hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _hdt_softbody_shape_names(dst_path, nif=nf)
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    # Bones grafted onto the plate + the EXISTING bone each re-anchors to: leg detail bones
    # anchor to Thigh/Calf; the butt-jiggle bones to the Pelvis; the breast bones to Spine2.
    # graft_anchor also drives the fold-back of any bone we can't safely anchor.
    graft_anchor = {b: anc for leg in _LEG_DEFORM_BONES for b, anc in leg["detail"]}
    _do_jiggle = _BUTT_MATCH and _BUTT_JIGGLE and _BUTT_JIGGLE_STRENGTH > 0.0
    if _do_jiggle:
        for jb in _BUTT_JIGGLE_BONES:
            graft_anchor[jb] = _BUTT_PELVIS
    _do_chest = _CHEST_JIGGLE and _CHEST_JIGGLE_STRENGTH > 0.0
    if _do_chest:
        for cb in _CHEST_JIGGLE_BONES:
            graft_anchor[cb] = _CHEST_ANCHOR
    _graftable = (set(_LEG_DETAIL_BONE_NAMES)
                  | (set(_BUTT_JIGGLE_BONES) if _do_jiggle else set())
                  | (set(_CHEST_JIGGLE_BONES) if _do_chest else set()))
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        if (s.name in collider_names or s.name in softbody_names
                or s.name in layered_cloth_names
                or any(k in nm for k in _CONFORM_SKIP_NAMES)):
            continue
        if _shape_has_effect_shader(s) or _is_fx_overlay_name(s.name):
            continue  # glow/decal overlay -- grafting+re-saving corrupts its effect-shader
                      # controller -> CTD (the Daedric 'MaleTorsoGlow' crash). Not body armor.
                      # Name check catches shapes whose shader is attached AFTER this pass
                      # (the 'TorsoF:FX' timing hole the buffer check alone missed).
        bw = s.bone_weights or {}
        # Eligible if it's LEG armor (carries Thigh+Calf -> knee/thigh/butt passes) OR a rigid
        # CHEST plate (carries the Spine2 chest anchor -> breast-jiggle pass). The rigid gate
        # below still excludes anything that already jiggles.
        has_leg = any(leg["thigh"] in bw and leg["calf"] in bw for leg in _LEG_DEFORM_BONES)
        has_chest = _do_chest and (_CHEST_ANCHOR in bw)
        if not (has_leg or has_chest):
            continue
        # ONLY rigid plate the jiggle-gated conform left alone.
        jig = 0
        for b, pairs in bw.items():
            if _is_physics_jiggle_scale_bone(b):
                jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
                if jig >= _CONFORM_MIN_JIGGLE_VERTS:
                    break
        if jig >= _CONFORM_MIN_JIGGLE_VERTS:
            continue
        existing = set(s.bone_names or [])
        if len(existing | _graftable) > SKIN_PARTITION_BONE_CAP:
            continue  # bone-cap headroom (realistic plate is far under)
        g2s = _shape_global_to_skin(s)
        # Derive each detail bone's bind transform RE-ANCHORED to the armor's OWN
        # current Thigh/Calf STB (read here, BEFORE any add_bone). Every leg armor's
        # verts are positioned for its existing leg-bone STB (the body's value), so
        # the detail bone must be consistent with THAT, not the body's absolute on a
        # mismatched bind nor identity. STB = STB_detail_body @ inv(STB_anchor_body) @
        # STB_anchor_armor -> detail-relative-to-anchor matches the body's, so the
        # detail bone contributes the SAME as its anchor at bind. The existing anchor
        # STB itself is preserved via the save/restore below (add_bone zeroes it).
        graft_stb: dict = {}
        for b, anc in graft_anchor.items():
            if (anc not in existing or b in existing or b not in body_mat
                    or anc not in body_mat or body_proto is None):
                continue
            ma, _p = _stb_to_mat4(s.get_shape_skin_to_bone(anc))
            if ma is None:
                continue
            stb = _derive_anchored_stb(body_mat[b], body_mat[anc], ma, body_proto)
            if stb is not None:
                graft_stb[b] = stb
        try:
            V = np.asarray(s.verts, np.float64)
        except Exception:
            continue
        n = len(V)
        if n == 0:
            continue
        vw = [dict() for _ in range(n)]
        for b, pairs in bw.items():
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        Vw = _verts_skin_to_world(V, g2s)
        _K = max(1, min(_LEG_MATCH_K, len(body_w)))
        d_k, idx_k = tree.query(Vw, k=_K)
        if _K == 1:
            d_k = d_k[:, None]
            idx_k = idx_k[:, None]
        d = d_k[:, 0]                # nearest distance still gates the passes

        def _match_body_w(i):
            # Average the k-nearest body verts' weight dicts -> a match target a sub-unit
            # mesh shift can't flip (see _LEG_MATCH_K). Only called for verts that graft.
            acc: dict = {}
            for j in idx_k[i]:
                for b, w in body_w[j].items():
                    acc[b] = acc.get(b, 0.0) + w
            inv = 1.0 / len(idx_k[i])
            return {b: w * inv for b, w in acc.items()}

        touched: "set" = set()
        need: "set" = set()
        conf = 0
        _max_prox = max(_LEG_BEND_PROX, _BUTT_PROX, _CHEST_PROX)
        for i in range(n):
            di = d[i]
            if di > _max_prox:
                continue  # not hugging the body by any pass -> leave it
            zi = Vw[i, 2]
            # Three independent passes, each with its OWN prox/z-gate (the leg pass stays at
            # _LEG_BEND_PROX -- correct in-game; butt/chest reach further):
            #  - LEG: z-tapered Thigh:Calf bend + detail flex (full knee -> partial thigh).
            #  - BUTT: Thigh<->Pelvis rebalance + matched butt-jiggle graft.
            #  - CHEST: matched, capped breast-jiggle graft (self-gates to the front).
            sgi = _leg_bend_strength(zi) if di <= _LEG_BEND_PROX else 0.0
            bgi = (_butt_match_strength(zi) if (_BUTT_MATCH and di <= _BUTT_PROX) else 0.0)
            cgi = (_chest_match_strength(zi) if (_do_chest and di <= _CHEST_PROX) else 0.0)
            if sgi <= 0.0 and bgi <= 0.0 and cgi <= 0.0:
                continue
            bwi = _match_body_w(i)          # k-nearest-averaged body distribution
            t: "set" = set()
            if sgi > 0.0:
                t1, added = _leg_deform_match_vert(vw[i], bwi, strength=sgi)
                t |= t1
                need |= added
            if bgi > 0.0:
                t2, jadded = _butt_match_vert(
                    vw[i], bwi, strength=bgi,
                    jiggle=_do_jiggle, jiggle_strength=_BUTT_JIGGLE_STRENGTH,
                    rebalance=_BUTT_REBALANCE)
                t |= t2
                need |= jadded
            if cgi > 0.0:
                t3, cadded = _chest_match_vert(vw[i], bwi, strength=cgi)
                t |= t3
                need |= cadded
            if t:
                touched |= t
                conf += 1
        if not conf:
            continue
        # Only graft a detail bone that will actually EMIT >=1 weight above the
        # setShapeWeights 1e-4 threshold: an add_bone'd bone written an EMPTY weight
        # list is a zero-weight bone left in the list but absent from the regenerated
        # skin-partition palette -> per-vert index runs past the palette -> equip CTD
        # (#zeroweight-bone-desync; same guard _install_skin's `surviving` applies).
        # A detail bone that emits nothing falls through to the fold below (onto its
        # anchor), so its tiny weight isn't lost. (Review finding #2.)
        to_add = [b for b in need if b in graft_stb and b not in existing
                  and any(vw[i].get(b, 0.0) > 1e-4 for i in range(n))]
        # CRITICAL: add_bone (pynifly) RESETS every existing bone's skin-to-bone xform
        # to identity, which would skin the armor's OWN Thigh/Calf-weighted verts (most
        # of the leg) to the origin -> the whole plate explodes (the in-game spike that
        # broke the first graft). SAVE the existing STBs first and RESTORE them at the
        # very end, after add_bone AND setShapeWeights (either may reset them).
        saved_stb: dict = {}
        if to_add:
            for eb in existing:
                try:
                    saved_stb[eb] = s.get_shape_skin_to_bone(eb)
                except Exception:
                    saved_stb[eb] = None
            # get_shape_skin_to_bone returns None (not raises) when the xform isn't
            # found. If ANY existing bone's STB can't be read, we can't restore it
            # after add_bone zeroes it -> it would be left at identity = origin spike.
            # Bail the graft for this shape (it gets the safe knee-only fold below)
            # rather than ship an identity-reset real bone. (Review finding #1.)
            if any(st is None for st in saved_stb.values()):
                to_add = []
                saved_stb = {}
            else:
                for b in to_add:
                    try:
                        s.add_bone(b)
                    except Exception:
                        pass
        unsafe = need - set(existing) - set(to_add)
        if unsafe:
            # Detail bone we couldn't anchor: fold its weight back into the anchor the
            # plate DOES have (never weight an unbindable bone -> origin spike).
            for i in range(n):
                hit = [b for b in unsafe if b in vw[i]]
                if not hit:
                    continue
                for b in hit:
                    anc = graft_anchor.get(b)
                    w = vw[i].pop(b)
                    if anc:
                        vw[i][anc] = vw[i].get(anc, 0.0) + w
                        touched.add(anc)
            touched -= unsafe
        dirty = True
        total += conf
        for bn in touched:
            s.setShapeWeights(bn, [(i, vw[i][bn]) for i in range(n)
                                   if bn in vw[i] and vw[i][bn] > 1e-4])
        # STBs LAST: restore the existing bones' originals (add_bone/setShapeWeights
        # zeroed them) + set the grafted detail bones'. Nothing after this resets them.
        for eb, st in saved_stb.items():
            try:
                s.set_skin_to_bone_xform(eb, st)
            except Exception:
                pass
        for b in to_add:
            try:
                s.set_skin_to_bone_xform(b, graft_stb[b])
            except Exception:
                pass
    if dirty:
        # A re-save must never silently un-hide an SMP collision proxy.
        _hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception:
            return 0
    return total


def _transfer_body_jiggle_to_fitted(dst_path, biped_slots: int = 0) -> int:
    """Graft the UBE body's jiggle (butt/belly/breast) weight onto a fitted
    garment that HUGS a jiggling body region but carries NONE of its own, so the
    garment follows the body's runtime jiggle instead of staying rigid and letting
    the body poke through (the close-to-body "clip when moving" class). Returns the
    number of verts grafted (0 = nothing touched).

    Selectivity is anatomical, not by name: a vert only gets weight if the body
    vert under it actually jiggles, so leg cloth over the butt/upper-thigh is
    grafted while a shin greave / arm guard (over a non-jiggling region) is left
    alone. Armor-only -- the body is never modified. Spike-proof: the grafted bone
    gets the body's own skin-to-bone transform, valid because both carry an
    identity global-to-skin (the graft is skipped otherwise, see _body_jiggle_ref).
    Scoped to leg-dominant garments for now (torso bras/corsets deferred -- breast
    jiggle on rigid cups is visually riskier; revisit after in-game)."""
    if not TRANSFER_BODY_JIGGLE:
        return 0
    if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the clip class
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _body_conform_ref(weight)
    jref = _body_jiggle_ref(weight)
    if ref is None or jref is None:
        return 0
    _Vb, body_w, _bb, tree = ref
    jstbs, body_ident = jref
    if not jstbs or not body_ident:
        return 0  # no jiggle bones, or skin spaces don't align -> skip (no spike)
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    collider_names = _hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _hdt_softbody_shape_names(dst_path, nif=nf)
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        if (s.name in collider_names or s.name in softbody_names
                or s.name in layered_cloth_names
                or any(k in nm for k in _CONFORM_SKIP_NAMES)):
            continue
        if _shape_has_effect_shader(s) or _is_fx_overlay_name(s.name):
            continue  # glow/decal overlay -- never graft jiggle onto an effect-shader shape
                      # (re-save corrupts its controller -> CTD; see _shape_has_effect_shader
                      # / _is_fx_overlay_name for the post-conform-attach timing hole)
        # Direct STB copy needs the garment's g2s to match the body's (identity).
        g2s = _shape_global_to_skin(s)
        if not (g2s is None or _g2s_is_identity(g2s)):
            continue
        existing = set(s.bone_names or [])
        # Bone-cap headroom: the partition split already ran, so never grow a shape
        # past the per-shape palette cap. Realistic counts are far under (a pant
        # ~15 bones + <=9 jiggle); this only guards the pathological edge.
        if len(existing) + len(jstbs) > SKIN_PARTITION_BONE_CAP:
            continue
        bw = s.bone_weights or {}
        # Only shapes that LACK jiggle (inverse of the conform gate -- a shape that
        # already jiggles is handled by _conform_fitted_to_body).
        jig = 0
        for b, pairs in bw.items():
            if _is_physics_jiggle_scale_bone(b):
                jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
                if jig >= _CONFORM_MIN_JIGGLE_VERTS:
                    break
        if jig >= _CONFORM_MIN_JIGGLE_VERTS:
            continue
        try:
            V = np.asarray(s.verts, np.float64)
        except Exception:
            continue
        n = len(V)
        if n == 0:
            continue
        vw = [dict() for _ in range(n)]
        for b, pairs in bw.items():
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        # not a physics-chain garment (custom non-skeleton bones -> SMP cloth)
        chain_frac = sum(1 for d in vw
                         if any(w > 0.1 and not _is_skeleton_bone(b)
                                for b, w in d.items())) / n
        if chain_frac > _CONFORM_CHAIN_MAX:
            continue
        Vw = _verts_skin_to_world(V, g2s)
        d, idx = tree.query(Vw)
        # HUGS the body (a loose skirt sits away -> excluded)
        if float((d < _CONFORM_FIT_PROX).mean()) < _CONFORM_FIT_FRAC:
            continue
        # leg-dominant: the measured close-to-body clip population is leg cloth.
        leg_dom = sum(1 for dd in vw
                      if dd and _is_leg_rigid_bone(max(dd, key=dd.get))) / n
        if leg_dom <= 0.5:
            continue
        new_bones: dict = {}
        graft = 0
        for i in range(n):
            if d[i] > _CONFORM_VERT_PROX:
                continue
            dvi = vw[i]
            if not dvi:
                continue
            if any(w > 0.1 and not _is_skeleton_bone(b) for b, w in dvi.items()):
                continue  # custom-chain vert -> leave it (partition safety)
            bd = body_w[idx[i]]
            bd_jig = {b: w for b, w in bd.items()
                      if _is_physics_jiggle_scale_bone(b) and w > 1e-3
                      and b in jstbs}
            if not bd_jig:
                continue  # body doesn't jiggle under this vert -> nothing to follow
            closeness = max(0.0, 1.0 - d[i] / _CONFORM_VERT_PROX)
            new, added = _jiggle_transfer_vert(
                dvi, bd_jig, closeness, _JIGGLE_TRANSFER_FACTOR)
            if new is None:
                continue
            for jb in added:
                if jb not in existing:
                    new_bones[jb] = jstbs.get(jb)
            vw[i] = new
            graft += 1
        if not graft or not new_bones:
            continue
        # Graft each new jiggle bone with the body's bind transform. CRITICAL:
        # add ALL bones FIRST, THEN set the STBs -- a later add_bone RESETS the
        # STB of an earlier-added bone (matches _install_skin's add-all-first
        # order). A bone we can't give a valid STB would skin to the ORIGIN
        # (spike, audit #4), so DROP its grafted weight rather than ship the spike.
        addable = [(jb, stb) for jb, stb in new_bones.items() if stb is not None]
        # CRITICAL add_bone-STB footgun: add_bone AND setShapeWeights below RESET every
        # existing bone's skin-to-bone xform to identity -> the plate's own Pelvis/Thigh
        # verts skin to the ORIGIN and the piece collapses/flies. Save existing STBs first,
        # restore them LAST; bail before add_bone if any can't be read (can't restore).
        # [DESIGN: Skin-to-bone (STB) preservation -- the add_bone footgun]
        saved_stb: dict = {}
        for eb in existing:
            try:
                saved_stb[eb] = s.get_shape_skin_to_bone(eb)
            except Exception:
                saved_stb[eb] = None
        if any(st is None for st in saved_stb.values()):
            continue

        def _restore_existing_stbs():
            for eb, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(eb, st)
                except Exception:
                    pass

        for jb, _stb in addable:
            try:
                s.add_bone(jb)
            except Exception:
                pass
        safe: set = set()
        for jb, stb in addable:
            try:
                s.set_skin_to_bone_xform(jb, stb)
                safe.add(jb)
            except Exception:
                pass
        unsafe = set(new_bones) - safe
        if unsafe:
            for i in range(n):
                if any(jb in vw[i] for jb in unsafe):
                    for jb in unsafe:
                        vw[i].pop(jb, None)
                    ss = sum(vw[i].values())
                    if ss > 0:
                        vw[i] = {b: w / ss for b, w in vw[i].items()}
        if not safe:
            # add_bone above already reset the existing STBs -> restore before bailing.
            _restore_existing_stbs()
            continue   # nothing safely grafted -> leave this shape untouched
        touched: set = set()
        for i in range(n):
            touched |= set(vw[i])
        for bn in touched:
            s.setShapeWeights(bn, [(i, vw[i][bn]) for i in range(n)
                                   if bn in vw[i] and vw[i][bn] > 1e-4])
        # STBs LAST: add_bone + setShapeWeights zeroed BOTH the existing bones' STBs
        # and the just-set graft STBs -> restore the originals + re-set the safe
        # grafts. Nothing after this resets them.
        _restore_existing_stbs()
        for jb, stb in addable:
            if jb in safe:
                try:
                    s.set_skin_to_bone_xform(jb, stb)
                except Exception:
                    pass
        dirty = True
        total += graft
    if dirty:
        # Re-assert the VirtualBody Hidden bit (a re-save can drop it -> blue body
        # double), mirroring _conform_fitted_to_body / _reauthor.
        _hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception:
            return 0
    return total


# Chain-anchor strategy: physics-chain hard-skeleton anchors (Pelvis/Spine/...)
# are recreated FLAT (identity, parent=Scene Root) so the ACTOR's live skeleton
# drives them at runtime. Nesting them statically into the worn armor caused FSMP
# to anchor against the static copy rather than the live actor — cloth sagged off.
# Custom-bone chains (chain-specific bones) and soft-body chain bones are still
# recreated at SOURCE bind (see _precreate_custom_bone_chains).
# `CBBE2UBE_NESTED_CHAIN_ANCHORS=1` restores full nesting as an opt-in fallback.
NESTED_CHAIN_ANCHORS = (
    os.environ.get("CBBE2UBE_NESTED_CHAIN_ANCHORS", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# PELVIS RE-ANCHOR: a bone-driven garment chain (skirt/apron) whose top bone hangs
# off a NIF-ROOT node (e.g. "BodyM_1.nif", "Scene Root") instead of a skeleton bone
# tracks the actor ROOT (feet) at runtime, so the waist garment disconnects /
# collapses as the body moves (every physics attempt fails because the anchor was
# never on the body). Re-parent that root node onto NPC Pelvis while PRESERVING its
# global position: each descendant chain bone's global transform + skin-to-bone
# (STB) is therefore unchanged (skin byte-identical), but the chain now follows the
# pelvis like a correctly-rigged skirt. Confirmed in-game (Anequina wolf-armor
# skirt). Default ON; CBBE2UBE_NO_PELVIS_REANCHOR=1 disables.
PELVIS_REANCHOR_CHAINS = (
    os.environ.get("CBBE2UBE_NO_PELVIS_REANCHOR", "").strip().lower()
    not in ("1", "true", "yes", "on"))
# Only re-anchor a root whose garment children sit at pelvis/waist height (global Z);
# hair/cape chains hang higher and need their own anchor bone (deferred).
_PELVIS_REANCHOR_ZMIN = float(os.environ.get("CBBE2UBE_PELVIS_REANCHOR_ZMIN", "40.0"))
_PELVIS_REANCHOR_ZMAX = float(os.environ.get("CBBE2UBE_PELVIS_REANCHOR_ZMAX", "100.0"))


def _reanchor_nif_root_chains(chain, anchors, src_nodes) -> int:
    """Re-parent garment-chain bones that hang off a NIF-ROOT node onto NPC Pelvis.

    `chain` maps bone -> (transform, parent_name) as gathered by
    _precreate_custom_bone_chains. The failure case (Anequina): the top skirt bones
    are parented directly to the source scene root (e.g. "BodyM_1.nif"), which the
    engine tracks as the actor ROOT (feet), so the skirt disconnects. The root node
    itself is Pelvis's OWN ancestor (the whole skeleton hangs under it) and so can't
    be moved; instead we lift each of its garment children onto Pelvis, rewriting
    that bone's LOCAL transform to keep its GLOBAL position identical (bind + STB
    unchanged, skin byte-identical). Gated to waist/hip-height bones so hair/cape
    chains are left alone. Returns the number of bones re-anchored.
    See PELVIS_REANCHOR_CHAINS."""
    pelvis_name = next((nm for nm in src_nodes
                        if "pelvis" in nm.lower() and _is_skeleton_bone(nm)), None)
    if pelvis_name is None:
        return 0
    try:
        _pg = src_nodes[pelvis_name].global_transform
        pgt = np.array(_pg.translation, float)
        # The re-anchor math below (subtract pelvis translation, keep the bone's
        # global rotation) is exact ONLY when Pelvis has IDENTITY global rotation
        # -- true for every standard skeleton (verified), which is what armor is
        # authored against. On an exotic skeleton with a rotated Pelvis, add_node
        # composes pelvis_rot . new_local and would mis-place the chain, so bail
        # (leaving the chain untouched = the pre-fix behaviour, never worse).
        if not np.allclose(np.array(_pg.rotation, float), np.eye(3), atol=1e-3):
            return 0
    except Exception:
        return 0

    def _is_nif_root(nm: str) -> bool:
        low = nm.lower()
        return (not _is_skeleton_bone(nm)
                and (low.endswith(".nif") or low == "scene root"
                     or low.startswith("bodym") or low.startswith("bodyf")))

    done = 0
    for b, (_bxf, bpar) in list(chain.items()):
        if not bpar or not _is_nif_root(bpar) or b not in src_nodes:
            continue
        try:
            xf = src_nodes[b].global_transform          # carries the bone's rotation
            gt = np.array(xf.translation, float)
        except Exception:
            continue
        if not (_PELVIS_REANCHOR_ZMIN <= float(gt[2]) <= _PELVIS_REANCHOR_ZMAX):
            continue                    # waist/hip garment bones only
        # New local under Pelvis: subtract pelvis bind translation (pelvis bind
        # rotation is identity), preserving the bone's global rotation + position.
        xf.translation = (float(gt[0] - pgt[0]), float(gt[1] - pgt[1]),
                          float(gt[2] - pgt[2]))
        chain[b] = (xf, pelvis_name)
        anchors.add(pelvis_name)
        done += 1
    return done


def _has_nif_root_garment_chain(src_nif) -> bool:
    """True if the NIF has a non-skeleton (garment) bone parented directly to a
    NIF-root node at waist/hip height -- the pattern _reanchor_nif_root_chains
    fixes. Used to gate the pelvis re-anchor in the copy/fit path so every other
    armor stays byte-unchanged. See PELVIS_REANCHOR_CHAINS."""
    if not PELVIS_REANCHOR_CHAINS:
        return False
    try:
        nodes = src_nif.nodes
    except Exception:
        return False

    def _is_nif_root(nm: str) -> bool:
        low = nm.lower()
        return (not _is_skeleton_bone(nm)
                and (low.endswith(".nif") or low == "scene root"
                     or low.startswith("bodym") or low.startswith("bodyf")))

    for nm in nodes:
        if _is_skeleton_bone(nm):
            continue
        nd = nodes[nm]
        par = nd.parent
        pn = par.name if par is not None else None
        if not pn or not _is_nif_root(pn):
            continue
        try:
            z = float(nd.global_transform.translation[2])
        except Exception:
            continue
        if _PELVIS_REANCHOR_ZMIN <= z <= _PELVIS_REANCHOR_ZMAX:
            return True
    return False


def _precreate_custom_bone_chains(dst_nif, src_nif, bone_names) -> int:
    """Recreate, in `dst_nif`, the node sub-trees for any armor-specific
    (non-skeleton) physics bones a shape is skinned to — INCLUDING their
    unweighted parent-chain bones up to the standard skeleton bone they
    anchor on — with the source local transforms + parent links intact.

    Must be called AFTER the shape's `skin()` but BEFORE its `add_bone`
    loop: add_bone then reuses these pre-created nodes (verified) instead
    of adding a fresh flat/identity one. Returns the number of nodes added.
    """
    if CHAIN_TO_SOFTBODY:
        return 0  # soft-body mode: don't recreate chain bones; reskin to body
    try:
        src_nodes = src_nif.nodes
    except Exception:
        return 0
    if not src_nodes:
        return 0
    # Operate on ALL source shapes' bones (union). An anchor added flat by an
    # earlier shape's add_bone can't be overwritten by a later _precreate call;
    # pre-creating all chains upfront ensures correct bind placement.
    try:
        _allb = set(bone_names)
        for _sh in src_nif.shapes:
            _allb |= set(_sh.bone_names)
        # Seed from the physics XML's <bone> list too. Pure CONSTRAINT bones --
        # the skirt/flap chains a bone-driven SMP garment hangs from (SkirtFBone,
        # HDT_FS, ...) -- carry ZERO skin weight, so they appear in NO shape's
        # bone list and the shape-driven copy never recreates their NODES. But
        # HDT-SMP walks the NIF hierarchy to build its kinematic chain; with the
        # chain's parent nodes gone it has nothing to hang from and the garment
        # free-falls to the ground. Add the XML's own custom bones that exist in
        # the source rig so the chain (below) recreates them at source bind.
        # #smp-constraint-bones
        try:
            _sp = getattr(src_nif, "filepath", None)
            if _sp:
                _xt = _read_source_hdt_xml_text(Path(_sp), nif=src_nif)
                if _xt:
                    for _xb in re.findall(r'<bone\s+name="([^"]+)"', _xt):
                        if _xb in src_nodes and not _is_skeleton_bone(_xb):
                            _allb.add(_xb)
        except Exception:
            pass
        bone_names = list(_allb)
    except Exception:
        pass
    # Recreate SOURCE bind for CUSTOM (non-skeleton) bones. For physics garments
    # also restore soft-body jiggle bones (breast/butt/belly): their HDT physics
    # is seeded from the NIF bind; a flat/missing parent collapses chest cloth.
    # Gated on a custom chain existing; plain body armour is untouched.
    custom = [b for b in bone_names if not _is_skeleton_bone(b)]
    # Auto-select flat vs nested: upper-body chain anchor -> NESTED; lower-body-
    # only (pelvis/thigh) -> FLAT. CBBE2UBE_NESTED_CHAIN_ANCHORS=1 forces nested.
    # Jiggle bones excluded from the anchor scan (body bones, not garment chains).
    _garment_chain = [b for b in custom if not _is_soft_body_physics_bone(b)]
    _heur_anchors: set[str] = set()
    for _gb in _garment_chain:
        _cur = _gb
        _seen0: set[str] = set()
        while _cur and _cur not in _seen0:
            _seen0.add(_cur)
            _n0 = src_nodes.get(_cur)
            if _n0 is None:
                break
            _pn0 = _n0.parent.name if _n0.parent is not None else None
            if _pn0 is None or _is_skeleton_bone(_pn0):
                if _pn0:
                    _heur_anchors.add(_pn0)
                break
            _cur = _pn0
    use_nested = NESTED_CHAIN_ANCHORS or any(
        _is_upper_body_anchor(a) for a in _heur_anchors)
    walk_bones = list(custom)
    if custom and use_nested:
        # Nested mode: restore all skeleton bones so the bone tree matches the
        # source rig. Plain armour has no custom chain; actor overrides at runtime.
        walk_bones += [b for b in bone_names if _is_skeleton_bone(b)]
    chain: dict[str, tuple] = {}
    anchors: set[str] = set()
    for b in walk_bones:
        cur = b
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            n = src_nodes.get(cur)
            if n is None:
                break
            par = n.parent
            par_name = par.name if par is not None else None
            chain[cur] = (n.transform, par_name)
            # Stop at the first hard skeleton bone (soft-body bones are part of
            # the chain, not the anchor — Breast_L02->L01 anchors on Spine2).
            if par_name is None or (_is_skeleton_bone(par_name)
                                    and not _is_soft_body_physics_bone(par_name)):
                if par_name:
                    anchors.add(par_name)
                break
            cur = par_name
    if not chain:
        return 0
    if PELVIS_REANCHOR_CHAINS:
        try:
            _reanchor_nif_root_chains(chain, anchors, src_nodes)
        except Exception:
            pass
    pyn = _pynifly()
    existing = set(dst_nif.nodes.keys())
    added = 0
    # Re-create each anchor's full source ancestor chain with source local
    # transforms + parent links. HDT-SMP walks the NIF hierarchy to build its
    # kinematic chain; a flat anchor breaks that even if every bone has the
    # correct global position.
    for a in anchors:
        if not use_nested:
            # Flat mode: recreate full ancestor chain each at its SOURCE GLOBAL
            # transform but flat-parented (parent=Scene Root). Walking the whole
            # ancestor chain (not just the immediate anchor) ensures deep skeleton
            # bones required by accessory chains (bag/book off Spine) are present.
            cur = a
            seen2: set[str] = set()
            while cur and cur not in seen2:
                seen2.add(cur)
                src_c = src_nodes.get(cur)
                if src_c is None:
                    break
                if cur not in existing:
                    try:
                        xf = src_c.global_transform
                    except Exception:
                        xf = None
                    if xf is None:
                        xf = pyn.TransformBuf()
                        xf.set_identity()
                    try:
                        dst_nif.add_node(cur, xf, parent=None)
                        existing.add(cur)
                        added += 1
                    except Exception:
                        pass
                p = src_c.parent
                cur = p.name if p is not None else None
            continue
        anc: list[tuple] = []  # [(name, local_xform, parent_name)] leaf -> root
        cur = a
        seen2: set[str] = set()
        while cur and cur not in seen2:
            seen2.add(cur)
            n = src_nodes.get(cur)
            if n is None:
                break
            p = n.parent
            pn = p.name if p is not None else None
            anc.append((cur, n.transform, pn))
            if pn is None:
                break
            cur = pn
        # Add root-first so each parent exists before its child.
        for name, xf, pn in reversed(anc):
            if name in existing:
                continue
            try:
                dst_nif.add_node(name, xf, parent=pn)
                existing.add(name)
                added += 1
            except Exception:
                pass
        # Fallback: source had no node for the anchor -> add it flat so the
        # custom chain still has something to hang on.
        if a not in existing:
            try:
                xf = pyn.TransformBuf()
                xf.set_identity()
                dst_nif.add_node(a, xf, parent=None)
                existing.add(a)
                added += 1
            except Exception:
                pass
    # Place custom bones parent-first (parent must already exist).
    remaining = dict(chain)
    guard = 0
    while remaining and guard < 200:
        guard += 1
        progressed = False
        for name in list(remaining.keys()):
            xf, par = remaining[name]
            if name in existing:
                del remaining[name]
                progressed = True
                continue
            if par is None or par in existing:
                try:
                    dst_nif.add_node(name, xf, parent=par)
                except Exception:
                    pass
                existing.add(name)
                added += 1
                del remaining[name]
                progressed = True
        if not progressed:
            break
    return added

# Shape name keywords that indicate "this shape is hand/foot armor and
# must not receive body-morph scale-bone weights even if its skinning
# doesn't include hand/foot bones explicitly". Stylized gauntlets and
# fantasy boots may be rigged to upperarm/calf bones alone but should
# still NOT deform with breast/belly/butt body sliders.
HAND_FOOT_NAME_KEYWORDS = (
    "hand", "glove", "gauntlet", "finger", "fist",
    "knuckle", "wrist", "bracer",
)


def _shape_has_fine_animation_bones(src_shape) -> bool:
    """Detect shapes rigged for hand/foot/finger/toe articulation OR
    whose name marks them as hand/glove armor. Returns True for any
    of these — caller's intent is "don't modify the skinning, don't
    add body-morph influence, the shape needs to track its parent
    extremity precisely."

    Bone-based check covers shapes rigged with hand/finger bones.
    Name-based check catches stylized gauntlets rigged only to UpperArm/Forearm.
    """
    # Name-based detection FIRST (independent of skinning) — catches stylized
    # gauntlets / gloves rigged only to UpperArm/Forearm (no hand bones).
    name_low = (getattr(src_shape, "name", "") or "").lower()
    if any(kw in name_low for kw in HAND_FOOT_NAME_KEYWORDS):
        return True
    # Bone-based detection: shape must carry real hand/foot geometry, not merely
    # graze an extremity bone (e.g. a robe's hem weights NPC Foot at <1%).
    # Require a cluster of verts with MAJORITY weight on extremity bones.
    ext_bones = {b for b in (src_shape.bone_names or [])
                 if any(kw in b.lower() for kw in RESKIN_PRESERVE_BONE_KEYWORDS)}
    if not ext_bones:
        return False
    try:
        n = len(src_shape.verts)
    except Exception:
        n = 0
    if n == 0:
        return True  # no vert data to weigh -> presence-based (old behaviour)
    bw = getattr(src_shape, "bone_weights", None) or {}
    tot = np.zeros(n, dtype=np.float64)
    ext = np.zeros(n, dtype=np.float64)
    for bn, pairs in bw.items():
        is_ext = bn in ext_bones
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for i, w in pl:
            ii = int(i)
            if 0 <= ii < n:
                tot[ii] += float(w)
                if is_ext:
                    ext[ii] += float(w)
    dom = int((ext > tot * 0.5).sum())   # verts majority-controlled by extremity
    # Boot=282/4074 (6.9%) -> kept; robe=0/4595 (0%) -> excluded. 1% (floor 8)
    # sits safely between, with margin for tall-shaft boots / partial gauntlets.
    return dom >= max(8, int(0.01 * n))


# Fraction of a shape's total vertex weight on hand/finger/foot/toe bones that
# classifies it as extremity SKIN (the actual hand/foot, not an arm/leg shell).
# Extremity skins are excluded from the body-morph TRI (body sliders must not
# deform fingers/toes). Threshold 0.5 sits in a wide empirical gap:
#   Hands_2 = 97%, Gloves_1 = 71% -> excluded; Bracers = 0.8%, boots = 21% -> kept.
EXTREMITY_DOMINANT_WEIGHT_FRAC = 0.5


def _shape_is_extremity_dominant(
        src_shape, frac: float = EXTREMITY_DOMINANT_WEIGHT_FRAC) -> bool:
    """True if the MAJORITY (> `frac`) of the shape's vertex weight is on
    hand/finger/foot/toe bones — i.e. the shape IS the hand or foot skin,
    not an arm/leg shell that merely touches an extremity bone.

    Stricter than `_shape_has_fine_animation_bones` (which fires on ANY
    extremity bone or hand/glove name). Used specifically to keep body-
    slider morphs OFF finger/toe geometry while still letting arm/leg
    shell pieces (bracers, guards, straps) follow the limb. No-op-safe
    on errors (returns False)."""
    try:
        bw = src_shape.bone_weights or {}
        total = ext = 0.0
        for bn, pairs in bw.items():
            if not pairs:
                continue
            w = float(sum(wt for _, wt in pairs))
            total += w
            if any(kw in bn.lower() for kw in RESKIN_PRESERVE_BONE_KEYWORDS):
                ext += w
        return total > 0 and (ext / total) > frac
    except Exception:
        return False


def _extremity_vert_fraction(src_shape, n_verts: int) -> "np.ndarray | None":
    """Per-vertex fraction (0.0..1.0) of a vertex's skin weight that lies on
    hand/finger/thumb/foot/toe bones. 1.0 = pure digit/extremity geometry,
    0.0 = pure limb (forearm/calf/thigh). The wrist/ankle transition lands
    around 0.5.

    Two uses, both about keeping the fingers/toes intact while still making
    the LIMB conform to the UBE body:

      * As a WARP falloff: the UBE body reference has NO hand/foot mesh
        (hands and feet are separate meshes in Skyrim), so finger/toe verts
        have no valid body vert to follow — warping them by the nearest
        (forearm/calf) body delta visibly melts the fingers. Scaling the
        warp displacement by (1 - fraction) gives the forearm/calf the full
        CBBE->UBE warp while digits stay put, blending smoothly at the wrist.

      * As a SCALE-BONE exclusion (via `_extremity_vert_mask`): body-morph
        scale bones must have zero effect on fingers/toes.

    Returns None on error (callers then fall back to no falloff / no mask)."""
    try:
        bw = src_shape.bone_weights or {}
        total = np.zeros(n_verts, dtype=np.float64)
        ext = np.zeros(n_verts, dtype=np.float64)
        for bn, pairs in bw.items():
            if not pairs:
                continue
            is_ext = any(kw in bn.lower()
                         for kw in RESKIN_PRESERVE_BONE_KEYWORDS)
            for vi, w in pairs:
                if 0 <= vi < n_verts:
                    total[vi] += float(w)
                    if is_ext:
                        ext[vi] += float(w)
        frac = np.zeros(n_verts, dtype=np.float64)
        nz = total > 1e-9
        frac[nz] = ext[nz] / total[nz]
        return frac
    except Exception:
        return None


def _extremity_vert_mask(
        src_shape, n_verts: int,
        frac: float = EXTREMITY_DOMINANT_WEIGHT_FRAC) -> "np.ndarray | None":
    """Boolean array of length `n_verts`: True where the MAJORITY (> `frac`)
    of that vertex's weight lies on hand/finger/thumb/foot/toe bones — i.e.
    the vert IS digit/extremity geometry.

    Used to keep body-morph scale bones (breast/butt/belly/thigh) OFF the
    fingers and toes (user requirement — "body morphs should have zero
    effect" on fingers/toes), and to drop the spurious bind-pose artifact
    where a glove's hand verts (~12u from the hips) wrongly inherit
    `NPC L/R Butt` scale weight. Limb verts (forearm/calf/thigh) stay False
    so they still follow legitimate body morph (e.g. boot calf -> RearCalf).

    Returns None on error (caller then applies scale bones to every vert)."""
    ef = _extremity_vert_fraction(src_shape, n_verts)
    if ef is None:
        return None
    return ef > frac


# Head-region bones a helmet/hood/circlet rides on. A shape dominantly
# weighted to these is head-worn gear: it must NOT be reskinned to the
# body or have 3BA scale bones (breast/belly/butt) added — a helmet that
# follows breast sliders is nonsensical and was producing helmets weighted
# to L/R Breast bones. Keeping head gear on the head bone(s) only matches
# how it was authored. "head"/"neck" substrings match "NPC Head [Head]"
# and "NPC Neck [Neck]" without colliding with any body/limb bone name.
HEAD_REGION_BONE_KEYWORDS = ("head", "neck")
HEAD_DOMINANT_WEIGHT_FRAC = 0.5


def _shape_is_head_dominant(
        src_shape, frac: float = HEAD_DOMINANT_WEIGHT_FRAC) -> bool:
    """True if the MAJORITY (> `frac`) of the shape's vertex weight is on
    head/neck bones — i.e. the shape is a helmet/hood/circlet that rides
    the head, not a cuirass with a high collar. Used to keep the body-
    blend reskin + scale-bone pass OFF head gear so it stays weighted to
    the head only. No-op-safe on errors (returns False)."""
    try:
        bw = src_shape.bone_weights or {}
        total = head = 0.0
        for bn, pairs in bw.items():
            if not pairs:
                continue
            w = float(sum(wt for _, wt in pairs))
            total += w
            if any(kw in bn.lower() for kw in HEAD_REGION_BONE_KEYWORDS):
                head += w
        return total > 0 and (head / total) > frac
    except Exception:
        return False


# --- Exposed body-skin detection (task: open-cleavage breast clip) ---------
# Fraction of a shape's verts that must sit within EXPOSED_SKIN_COINCIDE_DIST
# of the CBBE base body for the shape to BE the body surface (skin baked into
# the armor) rather than draped cloth. Measured separation on an open-cleavage
# corset mod (verify-don't-guess): the exposed 'CBBE' breast-skin shape
# sits 100% within 0.5u of the body (meanD 0.007u); EVERY cloth shape is <=8%
# within 0.5u (tightest corset meanD 1.10u). 0.9 / 0.5u leaves a wide margin.
EXPOSED_SKIN_COINCIDE_DIST = 0.5
EXPOSED_SKIN_COINCIDE_FRAC = 0.9

_CBBE_BODY_TREE_CACHE: dict = {}


def _cached_cbbe_body_tree(cbbe_body_verts):
    """cKDTree over the CBBE warp-basis body verts, cached per array.

    `cbbe_body_verts` is the process-stable cached delta basis (see
    `_cached_cbbe_to_ube_delta`), so keying by id() is safe; we re-verify
    identity before reusing to defend against any id reuse."""
    from scipy.spatial import cKDTree
    key = id(cbbe_body_verts)
    hit = _CBBE_BODY_TREE_CACHE.get(key)
    if hit is not None and hit[0] is cbbe_body_verts:
        return hit[1]
    tree = cKDTree(np.asarray(cbbe_body_verts, dtype=np.float64))
    _CBBE_BODY_TREE_CACHE[key] = (cbbe_body_verts, tree)
    return tree


def _is_exposed_body_skin_shape(src_world_verts, cbbe_body_verts) -> bool:
    """True if this shape is EXPOSED BODY SKIN baked into the armor — a
    (near-)copy of the nude body surface — rather than draped cloth.

    Open-cleavage corsets / lingerie often bake a slice of the body's own
    skin (frequently named 'CBBE'/'3BA'/a body part) so bare skin shows in
    the opening. Such a shape must morph EXACTLY like the nude body it
    imitates. `compute_body_blend_skinning` already transplants the body's
    graduated weights (blend==1 for on-body verts), so the shape co-moves
    with the body. The subsequent `add_scale_bone_weights` pass then
    OVER-weights its scale bones via MAX-propagation (measured on an
    open-cleavage corset mod: breast-bone fraction 0.12 -> 0.19), so the baked skin inflates
    ~55% more than the real body under a bust slider and pokes through the
    corset. Detecting these shapes lets the caller SKIP that redundant pass
    so the skin stays a faithful co-mover of the body.

    Pure geometry (no name match) so it generalizes to any body region
    (breast / belly / butt skin baked into any armor): a shape whose verts
    overwhelmingly coincide with the CBBE base body surface IS the body.
    Verts must be in the same (CBBE, world) frame as `cbbe_body_verts`
    (i.e. the PRE-warp source verts). Returns False when the CBBE basis is
    unavailable (safe fallback to current behaviour).
    """
    if cbbe_body_verts is None or src_world_verts is None:
        return False
    v = np.asarray(src_world_verts, dtype=np.float64)
    if len(v) == 0:
        return False
    tree = _cached_cbbe_body_tree(cbbe_body_verts)
    d, _ = tree.query(v, k=1)
    return float(
        (d <= EXPOSED_SKIN_COINCIDE_DIST).mean()) >= EXPOSED_SKIN_COINCIDE_FRAC


# Below this a body-skin-textured shape is a small decal / accent, not the
# exposed-skin slice that should pull in the whole body. The breast/cleavage
# slices that motivate this (typical open-cleavage armor `CBBE` shape = 1267v) are well above it.
_EXPOSED_BODY_SKIN_MIN_VERTS = 300


def _exposed_body_skin_shape_names(nif, cbbe_body_verts) -> "list[str]":
    """Names of shapes that are EXPOSED BODY SKIN baked into the armor — a
    visible slice of the nude body (an open-cleavage corset's breast/cleavage
    skin etc.) that should be REPLACED by the injected full UBE body, NOT kept
    as a static patch. Such a slice can't morph or connect to the neck on its
    own; injecting the whole UBE body in its place makes the exposed skin the
    real body — seamless to the neck and morphing as one unit (the same
    body-swap the converter already does for full inline body skins).

    A shape qualifies when ALL hold:
      * NOT already a full inline body (those route to phase 2 by themselves);
      * a nude body-skin DIFFUSE (keeps skin-tight CLOTH out — that's cloth to
        refit, not skin to replace);
      * substantial geometry (not a tiny skin decal);
      * geometrically coincident with the CBBE body surface (it IS the body),
        measured in WORLD frame via the shape's global-to-skin transform.

    Returns [] when the CBBE basis is unavailable (safe fallback: the shape is
    kept and refit, the prior behaviour).
    """
    if cbbe_body_verts is None:
        return []
    names: list[str] = []
    for s in nif.shapes:
        if _looks_like_inline_body(s):
            continue
        if not _shape_diffuse_is_body_skin(s):
            continue
        v = np.asarray(s.verts, dtype=np.float64)
        if len(v) < _EXPOSED_BODY_SKIN_MIN_VERTS:
            continue
        try:
            g2s = _shape_global_to_skin(getattr(s, "_backing", None) or s)
            world = _verts_skin_to_world(v, g2s)
        except Exception:
            world = v
        if _is_exposed_body_skin_shape(world, cbbe_body_verts):
            names.append(s.name)
    return names


def compute_body_blend_skinning(
    armor_verts: np.ndarray,
    src_shape,
    body_shape,
    *,
    near_dist: float = RESKIN_NEAR_DIST,
    far_dist: float = RESKIN_FAR_DIST,
    k: int = RESKIN_K,
) -> tuple[list[str], dict, dict[str, list[tuple[int, float]]]]:
    """Compute the blended skinning to apply to an armor shape.

    Returns (bone_names, xforms_by_bone, weights_by_bone) where:
      - bone_names: union of armor bones used + body bones with non-zero
        propagated weight on at least one armor vert
      - xforms_by_bone: skin-to-bone xform per bone (prefer armor's xform
        when both armor and body have the same bone — they should match
        since the body was rigged to the same skeleton, but armor's xform
        was authored against the original mesh position)
      - weights_by_bone: {bone: [(vert_idx, weight), ...]} with per-vert
        weight sums normalized to 1.0
    """
    from scipy.spatial import cKDTree

    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    body_verts = np.asarray(body_shape.verts, dtype=np.float64)
    body_n = len(body_verts)
    armor_n = len(armor_verts)

    # Per-vert blend coefficient by nearest-body distance.
    tree = cKDTree(body_verts)
    nearest_d, _ = tree.query(armor_verts, k=1)
    blend = np.zeros(armor_n, dtype=np.float64)
    blend[nearest_d < near_dist] = 1.0
    mid = (nearest_d >= near_dist) & (nearest_d < far_dist)
    blend[mid] = 1.0 - (nearest_d[mid] - near_dist) / (far_dist - near_dist)
    # nearest_d >= far_dist stays 0 -> keep original armor weights

    # K-nearest for body-weight propagation. Over-fetch (k*4) to filter body
    # verts on the "wrong side" of concave regions (e.g. between the legs
    # naive K-NN picks verts from the other leg). Reject candidates whose body
    # normal disagrees >60° with the nearest neighbour's normal.
    body_normals = None
    if hasattr(body_shape, "normals") and body_shape.normals is not None:
        try:
            body_normals = np.asarray(body_shape.normals, dtype=np.float64)
            ln = np.linalg.norm(body_normals, axis=1, keepdims=True)
            ln[ln < 1e-9] = 1.0
            body_normals = body_normals / ln
        except Exception:
            body_normals = None

    k_eff = min(k, body_n)
    if body_normals is not None:
        k_query = min(k * 4, body_n)
        cand_d, cand_idx = tree.query(armor_verts, k=k_query)
        if k_query == 1:
            cand_d = cand_d[:, None]; cand_idx = cand_idx[:, None]

        # Reject candidates whose normal disagrees >60° with the nearest
        # neighbour's normal (wrong-side verts in concave regions).
        ref_normal = body_normals[cand_idx[:, 0]]
        cand_normals = body_normals[cand_idx]
        agree = (cand_normals * ref_normal[:, None, :]).sum(axis=-1)
        valid_mask = agree > 0.5

        # Pick the first k_eff valid candidates; fall back to unfiltered first
        # k_eff when a vert has fewer than k_eff valid neighbours.
        order = np.argsort(~valid_mask, axis=1, kind="stable")
        valid_count = valid_mask.sum(axis=1)
        fallback = np.arange(k_eff)
        chosen_cols = np.where((valid_count >= k_eff)[:, None],
                               order[:, :k_eff], fallback[None, :])
        knn_d = np.take_along_axis(cand_d, chosen_cols, axis=1)
        knn_idx = np.take_along_axis(cand_idx, chosen_cols, axis=1)
    else:
        knn_d, knn_idx = tree.query(armor_verts, k=k_eff)
        if k_eff == 1:
            knn_d = knn_d[:, None]
            knn_idx = knn_idx[:, None]

    inv_d = 1.0 / (knn_d + 1e-6)
    inv_d /= inv_d.sum(axis=1, keepdims=True)

    # Dense body bone-weights for fast K-NN lookup.
    body_weights_dense: dict[str, np.ndarray] = {}
    for bn, pairs in (body_shape.bone_weights or {}).items():
        arr = np.zeros(body_n, dtype=np.float64)
        for idx, w in pairs:
            if 0 <= idx < body_n:
                arr[int(idx)] = float(w)
        body_weights_dense[bn] = arr

    # Original armor weights as a per-vert dict for fast scaling.
    armor_weights_sparse: dict[str, dict[int, float]] = {}
    for bn, pairs in (src_shape.bone_weights or {}).items():
        armor_weights_sparse[bn] = {
            int(idx): float(w) for idx, w in pairs
        }

    # xforms — prefer armor's (it was authored against this armor's verts).
    armor_xforms = {}
    for bn in src_shape.bone_names or []:
        try:
            xf = src_shape.get_shape_skin_to_bone(bn)
            if xf is not None:
                armor_xforms[bn] = xf
        except Exception:
            pass
    body_xforms = {}
    for bn in body_shape.bone_names or []:
        try:
            xf = body_shape.get_shape_skin_to_bone(bn)
            if xf is not None:
                body_xforms[bn] = xf
        except Exception:
            pass

    # Accumulate final per-vert weights: dict[vert_idx][bone] = weight.
    # Then transpose to per-bone sparse lists.
    # Use per-bone arrays for efficiency rather than per-vert dicts.
    final_dense: dict[str, np.ndarray] = {}

    # Original armor contribution: scaled by (1 - blend).
    inv_blend = 1.0 - blend
    for bn, vert_w in armor_weights_sparse.items():
        if not vert_w:
            continue
        arr = np.zeros(armor_n, dtype=np.float64)
        for vi, w in vert_w.items():
            if 0 <= vi < armor_n:
                arr[vi] = w
        arr *= inv_blend
        final_dense[bn] = arr

    # Body contribution: scaled by blend.
    # For each body bone, propagate its dense weights to armor verts via K-NN IDW.
    # propagated[i] = sum_k (body_w[knn_idx[i,k]] * inv_d[i,k]) * blend[i]
    for bn, body_arr in body_weights_dense.items():
        # Don't transfer the body's 3BA scale bones (Breast/Butt/Belly/...) onto
        # cloth: the per-armor TRI already drives the morph for a BodyMorph user,
        # so these are redundant — and they push a full-body suit's slot-32
        # partition past Skyrim's ~80-bone GPU skinning cap (render CTD) and
        # double-morph cloth under node-scaling. The shape keeps its own native
        # bones + the reskin's REGULAR (pose) body bones, then renormalizes
        # below. Toggle via RESKIN_EXCLUDE_SCALE_BONES. #166
        if RESKIN_EXCLUDE_SCALE_BONES and _is_scale_bone(bn):
            continue
        # K-NN propagation
        propagated = (body_arr[knn_idx] * inv_d).sum(axis=1) * blend
        if not np.any(propagated > 1e-7):
            continue
        if bn in final_dense:
            final_dense[bn] += propagated
        else:
            final_dense[bn] = propagated

    # Normalize per-vert weight sum to 1.0 (defends against any drift).
    if final_dense:
        per_vert_sum = np.zeros(armor_n, dtype=np.float64)
        for arr in final_dense.values():
            per_vert_sum += arr
        nz = per_vert_sum > 1e-7
        # Avoid divide-by-zero for verts with no weight (shouldn't happen
        # if we did things right; safe fallback is to leave 0).
        for bn in final_dense:
            final_dense[bn][nz] /= per_vert_sum[nz]

    # Cap per-vertex bone count to Skyrim's 4-weight limit. Skyrim's
    # BSTriShape stores up to 4 bone influences per vert; if we hand
    # pynifly more, the file may crash the game on load OR silently
    # drop weights and corrupt the skin instance. So we explicitly
    # clip to the top 4 bones per vert and renormalize.
    MAX_BONES_PER_VERT = 4
    if final_dense:
        # Stack into (n_bones, n_verts) array for argsort.
        bone_list = list(final_dense.keys())
        stack = np.stack([final_dense[bn] for bn in bone_list], axis=0)  # (B, N)
        # For each vert (column), find the top-K bone indices.
        # argsort on -stack gives descending order; take first MAX_BONES_PER_VERT.
        topk_bone_idx = np.argsort(-stack, axis=0)[:MAX_BONES_PER_VERT, :]  # (K, N)
        # Build a mask: True where (bone_idx, vert_idx) is in top-K.
        # Guard against shapes with fewer bones than MAX_BONES_PER_VERT
        # (e.g. 3MetalDecoPauldron has 3 bones; topk row 3 would index
        # axis 0 of size 3, raising IndexError).
        mask = np.zeros_like(stack, dtype=bool)
        for k in range(min(MAX_BONES_PER_VERT, topk_bone_idx.shape[0])):
            mask[topk_bone_idx[k], np.arange(armor_n)] = True
        stack = np.where(mask, stack, 0.0)
        # Renormalize per vert.
        per_vert_sum = stack.sum(axis=0)
        nz = per_vert_sum > 1e-7
        stack[:, nz] /= per_vert_sum[nz]
        final_dense = {bn: stack[i] for i, bn in enumerate(bone_list)}

    # Sparsify -> per-bone (vert_idx, weight) pairs, dropping near-zeros.
    WEIGHT_EPS = 1e-4
    weights_by_bone: dict[str, list[tuple[int, float]]] = {}
    for bn, arr in final_dense.items():
        idxs = np.where(arr >= WEIGHT_EPS)[0]
        if len(idxs) == 0:
            continue
        weights_by_bone[bn] = [(int(i), float(arr[i])) for i in idxs]

    # Final bone set + xforms (prefer armor's when both have the same name).
    bone_names = list(weights_by_bone.keys())
    xforms_by_bone = {}
    for bn in bone_names:
        if bn in armor_xforms:
            xforms_by_bone[bn] = armor_xforms[bn]
        elif bn in body_xforms:
            xforms_by_bone[bn] = body_xforms[bn]

    return bone_names, xforms_by_bone, weights_by_bone


# ----- Layered-cloth weight sync (cleavage anti-intersection) ----------
#
# After scale-bone reskin, layered cloth in the same NIF (bra under halter top)
# can end up with slightly different breast-bone weights and jiggle at different
# amplitudes -> Z-fighting at the cleavage seam. Fix: in the upper-chest region,
# copy the LARGEST cloth shape's per-vertex weights to overlapping verts in every
# other cloth shape. Verts outside the chest region are untouched.

# Upper-chest region bounds (UBE breast sits ~Z 90-110, anterior only).
CHEST_SYNC_Z_MIN = 85.0
CHEST_SYNC_Z_MAX = 115.0
CHEST_SYNC_X_BOUND = 15.0
CHEST_SYNC_Y_MIN = -2.0

# Max distance for a sync to fire (layered cloth ~0.5-2u apart;
# cross-piece gaps >5u).
CHEST_SYNC_DISTANCE = 2.5

# Min breast-bone weight fraction to qualify as a bust layer. Genuine bust cloth
# >=0.34; decorative attachments <=0.11. 0.25 sits in the gap.
CHEST_SYNC_MIN_BREAST_FRAC = 0.25

# --- ABDOMEN/BUTT layer jiggle sync (sibling of the chest sync above) ---
# An inner cloth layer grafted MORE butt/belly jiggle than the outer layer over
# it (jiggle is proximity-grafted, and the inner layer sits closer to the body)
# out-swings the outer during motion and punches through it. Sync every inner
# layer's waist/butt verts to the OUTERMOST layer's weights so the stack moves as
# one (inner <= outer). Default OFF (opt-in, pending cross-armor validation);
# CBBE2UBE_ABDO_JIGGLE_SYNC=1.  [DESIGN: Layer-coherent jiggle]
ABDO_SYNC_Z_MIN = 64.0   # above the mid-thigh, so leg skinning is never touched
ABDO_SYNC_Z_MAX = 96.0
ABDO_SYNC_DISTANCE = 2.5          # layered cloth ~0.5-2u apart; cross-piece >5u
ABDO_SYNC_MIN_JIGGLE_FRAC = 0.12  # region verts must be meaningfully butt/belly-driven
_ABDO_JIGGLE_SYNC = (os.environ.get("CBBE2UBE_ABDO_JIGGLE_SYNC", "").strip().lower()
                     in ("1", "true", "yes", "on"))


CHEST_DEPTH_SEPARATION = 0.4     # target clearance the inner bust layer is pushed to
CHEST_DEPTH_FRONT_TOL = 0.2      # only push receiver verts within this distance in FRONT
                                  # of the authority. Verts clearly in front (ornaments,
                                  # straps, cloaks) are left alone so the pass doesn't sink
                                  # legitimate outer pieces. Push band: (-SEPARATION, +TOL).
CHEST_DEPTH_PAIR_XZ_DIST = 3.0   # max (X,Z) distance to pair an inner vert with an
                                  # authority vert (larger than inter-vert spacing; smaller
                                  # than cross-piece gap so opposite-side cloth is ignored).


def _separate_chest_layered_cloth_depth(
        shape_jobs: list,
        body_verts: "np.ndarray | None" = None,
        body_normals: "np.ndarray | None" = None,
        source_body_verts: "np.ndarray | None" = None,
        source_body_normals: "np.ndarray | None" = None,
) -> int:
    """Push inner-layer cloth verts in the cleavage zone backward (along
    the body's inward normal) so they sit a clean CHEST_DEPTH_SEPARATION
    behind the outer layer. Fixes the case where two cloth layers (bra +
    outer fabric) occupy the same depth at the cleavage and Z-fight at
    standstill — visible as mesh-on-mesh interference even when nothing
    is moving (no HDT physics involved).

    Authority = the largest chest-region cloth shape (the outer layer; it
    stays put). Receivers = every other chest-region cloth shape (the
    inner layer(s); their too-close verts get pushed inward).

    For each receiver chest vert, finds the nearest authority chest vert
    in the (X, Z) projection (NOT 3D — we want to compare "same XZ slot,
    different depth"). Computes the signed distance from receiver to
    authority along the body's outward normal at that vert; if the
    receiver sits less than CHEST_DEPTH_SEPARATION behind the authority
    (signed >= -CHEST_DEPTH_SEPARATION), pushes it inward enough to
    reach exactly that clearance. Verts already comfortably behind the
    outer layer are untouched, so the cleavage isn't visually deepened
    where it doesn't need to be.

    SOURCE-ORDER GATE: when `source_body_verts`/`_normals` are given
    (phase-2), a receiver vert whose SOURCE order says it sits OUTSIDE the
    authority is never pushed behind it. Authority-by-size is source-blind
    and would push corset rim/belt verts behind the chest plate if left
    ungated. The push is meant for true inner layers (bra under fabric).

    Mutates each receiver job's `verts` in place and sets
    `verts_modified` so the shape-copy pass picks up the new positions.
    Returns the total count of pushed-back receiver verts (0 = no-op).

    No-op without body_verts + body_normals (needs the body normal to
    define "inward"), or if fewer than 2 candidate cloth shapes exist.
    """
    if body_verts is None or body_normals is None:
        return 0
    try:
        candidates = []
        for j in shape_jobs:
            os_ = j.get("override_skin")
            if not os_:
                continue
            weights_map = os_.get("weights") or {}
            if not any("breast" in bn.lower() for bn in weights_map):
                continue
            v = j["verts"]
            if v is None or len(v) == 0:
                continue
            v = np.asarray(v, dtype=np.float64)
            mask = ((v[:, 2] >= CHEST_SYNC_Z_MIN)
                    & (v[:, 2] <= CHEST_SYNC_Z_MAX)
                    & (np.abs(v[:, 0]) <= CHEST_SYNC_X_BOUND)
                    & (v[:, 1] >= CHEST_SYNC_Y_MIN))
            n_chest = int(mask.sum())
            if n_chest < 5:
                continue
            candidates.append((j, mask, n_chest))
        if len(candidates) < 2:
            return 0

        candidates.sort(key=lambda c: -c[2])
        auth_job, auth_mask, _ = candidates[0]
        auth_v_full = np.asarray(auth_job["verts"], dtype=np.float64)
        auth_chest = auth_v_full[auth_mask]
        if len(auth_chest) == 0:
            return 0

        from scipy.spatial import cKDTree
        body_verts_arr = np.asarray(body_verts, dtype=np.float64)
        body_normals_arr = np.asarray(body_normals, dtype=np.float64)
        body_tree = cKDTree(body_verts_arr)
        # Auth lookup: by (X, Z) projection only — we want to find the
        # outer vert at "this same XZ location" so we can compare depths.
        auth_xz_tree = cKDTree(auth_chest[:, [0, 2]])

        # SOURCE-ORDER GATE setup (see docstring): source-frame clearance of
        # the authority's chest verts, so receivers can check who was outside
        # whom BEFORE the warp scrambled it.
        def _shape_src(jb):
            s = jb.get("src")
            if s is None:
                return None
            sv = np.asarray(list(s.verts), dtype=np.float64)
            return sv if len(sv) == len(jb["verts"]) else None

        src_gate_ready = False
        if (source_body_verts is not None and source_body_normals is not None
                and np.any(source_body_normals)):
            auth_src = _shape_src(auth_job)
            if auth_src is not None:
                sbv = np.asarray(source_body_verts, dtype=np.float64)
                sbn = np.asarray(source_body_normals, dtype=np.float64)
                sb_tree = cKDTree(sbv)

                def _src_clr(pts):
                    _, i = sb_tree.query(pts, k=1)
                    return ((pts - sbv[i]) * sbn[i]).sum(axis=1)

                auth_src_chest = auth_src[auth_mask]
                auth_src_tree = cKDTree(auth_src_chest)
                auth_src_clr = _src_clr(auth_src_chest)
                src_gate_ready = True

        total_pushed = 0
        for recv_job, recv_mask, _ in candidates[1:]:
            recv_v_full = np.asarray(recv_job["verts"], dtype=np.float64)
            recv_chest_idx_in_shape = np.where(recv_mask)[0]
            recv_chest = recv_v_full[recv_mask]

            xz_dists, nearest_auth = auth_xz_tree.query(
                recv_chest[:, [0, 2]], k=1,
                distance_upper_bound=CHEST_DEPTH_PAIR_XZ_DIST,
            )
            valid = (xz_dists < CHEST_DEPTH_PAIR_XZ_DIST)
            if not valid.any():
                continue

            # For the valid pairs: compute signed distance from receiver
            # to authority along body normal. Body normal is taken at the
            # receiver vert's nearest body point (most accurate "outward"
            # direction at that armor location).
            recv_valid_pts = recv_chest[valid]
            _, body_idx = body_tree.query(recv_valid_pts, k=1)
            outward = body_normals_arr[body_idx]
            auth_pts = auth_chest[nearest_auth[valid]]
            delta = recv_valid_pts - auth_pts
            signed = (delta * outward).sum(axis=1)
            # Push ONLY verts in the Z-fight band: between the target depth
            # (-SEPARATION, behind the authority) and a small tolerance IN
            # FRONT (+FRONT_TOL). Those are near-coplanar and genuinely
            # fighting. Verts already comfortably behind (signed <=
            # -SEPARATION) need no push; verts CLEARLY in front (signed >=
            # +FRONT_TOL) are legitimate on-top layers (cloak, shoulder
            # pad, strap) and must be LEFT ALONE — pushing them inward sinks
            # them behind the base garment (the mashup-armor regression). For
            # the band verts, the push moves them to exactly -SEPARATION.
            fighting = ((signed > -CHEST_DEPTH_SEPARATION)
                        & (signed < CHEST_DEPTH_FRONT_TOL))
            # SOURCE-ORDER GATE: never push a vert behind the authority when
            # the SOURCE had it OUTSIDE the authority at this spot (the
            # corset rim over the chest plate; the belts) — that inversion
            # is exactly what the abdomen order-restore pass must then undo.
            if src_gate_ready:
                recv_src = _shape_src(recv_job)
                if recv_src is not None:
                    rs_chest = recv_src[recv_mask]
                    rs_valid = rs_chest[valid]
                    sd, si = auth_src_tree.query(
                        rs_valid, k=1,
                        distance_upper_bound=OVERLAY_PAIR_R)
                    matched = np.isfinite(sd)
                    src_gap = np.zeros(len(rs_valid))
                    if matched.any():
                        src_gap[matched] = (
                            _src_clr(rs_valid[matched])
                            - auth_src_clr[si[matched]])
                    fighting &= ~(matched
                                  & (src_gap >= OVERLAY_LOCAL_ORDER_MIN))
            push_amt = np.where(fighting, -CHEST_DEPTH_SEPARATION - signed, 0.0)
            push_mask = fighting & (push_amt < 0)
            if not push_mask.any():
                continue

            # Apply per-vert push along outward (push_amt is negative so
            # multiplying by outward moves the vert inward, toward body).
            push_3d = (push_amt[push_mask, None]
                       * outward[push_mask])
            local_valid_idx = np.where(valid)[0][push_mask]
            push_dst_indices = recv_chest_idx_in_shape[local_valid_idx]
            recv_v_full[push_dst_indices] += push_3d

            recv_job["verts"] = recv_v_full
            recv_job["verts_modified"] = True
            total_pushed += int(push_mask.sum())

        return total_pushed
    except Exception:
        return 0


# ----- ABDOMEN / waist multi-layer depth separation -------------------------
# The chest pass above separates ONE inner layer behind ONE authority — fine for
# bra+fabric. A multi-layer garment (base top + corset + sash + metal belt +
# breastplate) is warped one shape at a time, so the warp can SCRAMBLE the
# stacking order: the min-standoff clamp lands every layer at ~the same standoff
# off the (bigger) UBE body and the author's radial order collapses (the
# "crumpled/jumbled gold abdomen" / "belt clips the corset" bugs in a multi-layer garment).
# This pass re-imposes the SOURCE order, classified from the source-frame
# clearance so it's immune to whatever the warp did.
OVERLAY_PAIR_R = 3.0       # 3D dist to pair an A vert with the nearest B vert
OVERLAY_MIN_OVERLAP = 30   # minimum overlapping verts to consider a pair
OVERLAY_CAP = 3.0          # per-vert lift cap (runaway guard)
LAYER_STACK_GAP = float(os.environ.get("CBBE2UBE_LAYER_STACK_GAP", "0.15"))     # clearance a locally-outer layer keeps above the outermost inner vert beneath it
# v4 gating thresholds for _separate_abdomen_layered_cloth_depth:
OVERLAY_LOCAL_ORDER_MIN = 0.05  # min kernel-averaged gap field for tier-1 constraint;
                                # genuinely interleaved weaves cancel to ~0 -> no constraint.
OVERLAY_LOCAL_CONSIST = 0.70    # fraction of nearby gap samples that must agree in sign;
                                # interleaved noise ~0.5, coherent reversals ~1.0.
OVERLAY_LOCAL_RAW_STRONG = float(os.environ.get("CBBE2UBE_OVERLAY_RAW_STRONG", "0.12"))  # tier-2: fires on own raw source gap even without
                                  # neighbourhood consistency; recovers thin overlay strips
                                  # narrower than OVERLAY_PAIR_R. Safe under source-pair
                                  # binding (never lifts past a source-above partner).


def _smooth_overlay_push(vals, pts, iters=4, k=8):
    """Neighbour-average a per-vertex push field so a band lift has no creases."""
    if len(pts) < k + 1:
        return vals
    from scipy.spatial import cKDTree
    tree = cKDTree(pts)
    _, idx = tree.query(pts, k=k)
    nbr = idx[:, 1:]
    v = vals.copy()
    for _ in range(iters):
        v = 0.5 * v + 0.5 * v[nbr].mean(axis=1)
    return v


def _separate_abdomen_layered_cloth_depth(
        shape_jobs: list,
        body_verts: "np.ndarray | None" = None,
        body_normals: "np.ndarray | None" = None,
        cbbe_body_verts: "np.ndarray | None" = None,
        source_body_verts: "np.ndarray | None" = None,
        source_body_normals: "np.ndarray | None" = None,
) -> int:
    """Re-impose the SOURCE radial layer order on a multi-layer outfit
    (corset / top / belts / coat / undershirt / ...). The per-shape warp's
    min-standoff clamp pushes every inner layer to ~the same standoff off the
    (bigger) UBE body, collapsing the author's stacking order.

    Per-region source order-field restoration (v4): rather than giving each
    shape pair a single global order edge (v3), verts are gated and bound
    region-by-region so minority regions (e.g. a top that is under a plate on
    the torso but over it at the neckline) are not bulldozed:
      1. GATING: gap samples are pooled from both directions, 1/multiplicity
         weighted, and kernel-averaged (OVERLAY_PAIR_R). Tier-1 fires on
         field >= OVERLAY_LOCAL_ORDER_MIN + consistency >= OVERLAY_LOCAL_CONSIST.
         Tier-2 fires on own raw gap >= OVERLAY_LOCAL_RAW_STRONG even without
         neighbourhood agreement (recovers thin overlay strips).
      2. BINDING: each vert is lifted vs its source-below partners and ceilinged
         by source-above partners, making leapfrogging structurally impossible.
      3. RESOLUTION: lift-only along body normal (no new body clipping), 2 rounds
         inner->outer; pushes are smoothed then re-clamped by ceilings and
         OVERLAY_CAP. Set CBBE2UBE_LAYER_DEBUG=1 for per-round stats.

    Ordering basis: source body frame (phase-2 inline body) when available;
    phase-1 falls back to UBE fit body in output frame.
    `cbbe_body_verts` unused since v3 (kept for call-site compat). Mutates
    jobs' `verts` + sets `verts_modified`. Returns verts moved."""
    if body_verts is None or body_normals is None:
        return 0
    try:
        import os as _os
        import sys as _sys
        from scipy.spatial import cKDTree
        bva = np.asarray(body_verts, dtype=np.float64)
        bna = np.asarray(body_normals, dtype=np.float64)
        ube_tree = cKDTree(bva)

        # ORDERING basis. Prefer the SOURCE body the armor was built on (same
        # frame as the source verts) -> classify the stack PRE-collapse, immune
        # to whatever the warp did. Compute clearance in that frame. Phase-1 has
        # no inline body: fall back to the UBE fit body in the OUTPUT frame
        # (phase-1 keeps the source order since it doesn't body-swap), which
        # un-gates phase-1 instead of the old silent no-op.
        use_source_frame = (source_body_verts is not None
                            and source_body_normals is not None
                            and np.any(source_body_normals))
        if use_source_frame:
            ord_v = np.asarray(source_body_verts, dtype=np.float64)
            ord_n = np.asarray(source_body_normals, dtype=np.float64)
        else:
            ord_v = bva
            ord_n = bna
        ord_tree = cKDTree(ord_v)

        def _clr(verts, tree, ov, on):
            d, i = tree.query(verts, k=1)
            return ((verts - ov[i]) * on[i]).sum(axis=1)

        jobs = []
        for j in shape_jobs:
            if not j.get("override_skin"):
                continue  # only reskinned cloth (excludes the injected body)
            wv = j.get("verts")
            src = j.get("src")
            if wv is None or len(wv) == 0 or src is None:
                continue
            wv = np.asarray(wv, dtype=np.float64)
            sv = np.asarray(list(src.verts), dtype=np.float64)
            if len(sv) != len(wv):
                continue  # topology mismatch (e.g. body-inject) -> skip
            # verts used to CLASSIFY order (source frame if available, else
            # output frame) and the per-shape signed clearance in that frame.
            ov = sv if use_source_frame else wv
            j["_wv"] = wv
            j["_ov"] = ov
            j["_oc"] = _clr(ov, ord_tree, ord_v, ord_n)
            jobs.append(j)
        if len(jobs) < 2:
            for j in jobs:
                for k in ("_wv", "_ov", "_oc"):
                    j.pop(k, None)
            return 0

        # PER-VERT order constraints from the SOURCE gap field. For each pair
        # (a, b): pool signed gap SAMPLES ("a outside b" positive) from BOTH
        # sides (a verts -> nearest b, AND b verts -> nearest a, like v3's
        # symmetric classification), then evaluate the LOCAL field at each
        # vert by kernel-averaging the nearby pooled samples. The pooling is
        # what makes the gate honest at weave scale: one-sided pairing aliases
        # a vert-to-vert interleave into coherent same-sign patches on the
        # other shape's grid (several of its verts map to ONE nearest vert),
        # which would survive smoothing and fake an order; pooled samples at
        # the same spot carry both signs and cancel to ~0. A vert whose local
        # field exceeds OVERLAY_LOCAL_ORDER_MIN gets an ORDER constraint: it
        # must clear the other shape in the output.
        n = len(jobs)
        # Per-pair SOURCE-BOUND constraints. Gates (below) decide WHICH verts
        # act; these arrays decide AGAINST WHOM: each gated vert is bound to
        # the SPECIFIC partner verts on the proper side of it IN THE SOURCE
        # (lift targets = its source-below partners; ceilings = its source-
        # above partners). Mask-level reference selection cannot resolve a
        # three-sheet sandwich (e.g. top-fabric < belt < top-rim, all within
        # one pairing radius — TWO sheets of the SAME shape on opposite sides
        # of the belt), because "is this belt vert under the top" has no
        # per-vert answer; bound to source partners it does.
        below_pairs: "dict[tuple[int, int], tuple]" = {}
        above_pairs: "dict[tuple[int, int], tuple]" = {}
        for a in range(n):
            for b in range(a + 1, n):
                A, B = jobs[a], jobs[b]
                ta = cKDTree(A["_ov"]); tb = cKDTree(B["_ov"])
                ddb, uib = tb.query(A["_ov"], k=1,
                                    distance_upper_bound=OVERLAY_PAIR_R)
                dda, uia = ta.query(B["_ov"], k=1,
                                    distance_upper_bound=OVERLAY_PAIR_R)
                ma = np.isfinite(ddb)   # a verts with a b neighbour
                mb = np.isfinite(dda)   # b verts with an a neighbour

                def _inv_mult(targets):
                    """1/multiplicity sample weights: when MANY verts of one
                    shape share the SAME nearest vert of the other (density
                    mismatch, or the band just outside an overlap rim), their
                    samples all repeat that one vert's gap — unweighted they'd
                    outvote the genuine local mix. Weighting by 1/count makes
                    each matched vert worth one opinion total."""
                    _, inv, cnt = np.unique(targets, return_inverse=True,
                                            return_counts=True)
                    return 1.0 / cnt[inv]

                pts, gaps, wts = [], [], []
                if ma.any():
                    pts.append(A["_ov"][ma])
                    gaps.append(A["_oc"][ma] - B["_oc"][uib[ma]])
                    wts.append(_inv_mult(uib[ma]))
                if mb.any():
                    pts.append(B["_ov"][mb])
                    gaps.append(A["_oc"][uia[mb]] - B["_oc"][mb])
                    wts.append(_inv_mult(uia[mb]))
                if not pts:
                    continue
                spts = np.vstack(pts)
                sgap = np.concatenate(gaps)
                swt = np.concatenate(wts)
                if len(sgap) < OVERLAY_MIN_OVERLAP:
                    continue
                stree = cKDTree(spts)
                K = min(32, len(spts))

                def _field(qpts, _stree=stree, _sgap=sgap, _swt=swt, _K=K):
                    """Local order field at each query vert: weighted mean AND
                    weighted sign-consistency fraction of the pooled gap
                    samples within PAIR_R (>= 4 samples, else no opinion)."""
                    dd, ui = _stree.query(qpts, k=_K,
                                          distance_upper_bound=OVERLAY_PAIR_R)
                    if _K == 1:
                        dd = dd[:, None]; ui = ui[:, None]
                    valid = np.isfinite(dd)
                    safe = np.where(valid, ui, 0)
                    val = np.where(valid, _sgap[safe], 0.0)
                    w = np.where(valid, _swt[safe], 0.0)
                    wsum = w.sum(axis=1)
                    ok = (valid.sum(axis=1) >= 4) & (wsum > 0)
                    den = np.where(wsum > 0, wsum, 1.0)
                    mean = np.where(ok, (val * w).sum(axis=1) / den, 0.0)
                    fpos = np.where(
                        ok, (w * (val > 0)).sum(axis=1) / den, 0.5)
                    return mean, fpos

                # Fire only where the neighbourhood is BOTH consistently
                # signed (>= OVERLAY_LOCAL_CONSIST of local opinion, v3's
                # discriminator at neighbourhood scale) and material
                # (|mean| >= OVERLAY_LOCAL_ORDER_MIN).
                mean_a, fpos_a = _field(A["_ov"])
                mean_b, fpos_b = _field(B["_ov"])
                # Per-vert RAW source gap (one-sided), for the tier-2 gate.
                raw_a = np.zeros(len(A["_ov"]))
                raw_a[ma] = A["_oc"][ma] - B["_oc"][uib[ma]]
                raw_b = np.zeros(len(B["_ov"]))
                raw_b[mb] = B["_oc"][mb] - A["_oc"][uia[mb]]
                # Tier 1 (regional): the pooled field is consistent and
                # material. Tier 2 (local-strong): the vert's OWN source gap
                # is large (>= OVERLAY_LOCAL_RAW_STRONG, far above pairing
                # noise) and the field does not actively contradict it —
                # this is what restores overlay features THINNER than the
                # pairing radius (e.g. a narrow neckline trim in a multi-layer garment), where the kernel
                # unavoidably mixes both sides of the order-crossing line
                # and tier 1 can never reach consistency ON the strip.
                # Tier-2 also works as a VETO: a vert whose OWN raw gap
                # strongly contradicts the regional verdict is a thin-strip
                # casualty of kernel mixing (an under-rim belt vert inside a
                # belt-over-top neighbourhood) — firing it would lift it over
                # the very strip it sits beneath, and excluding it from the
                # other side's reference set leaves that strip nothing to
                # clear (MEASURED on a multi-layer garment: top had 2222 constrained verts but
                # only 378 reachable references before this veto).
                oa = ma & ~(raw_a <= -OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_a >= OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_a >= OVERLAY_LOCAL_CONSIST))
                    | ((raw_a >= OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_a >= -OVERLAY_LOCAL_ORDER_MIN)))
                ob = mb & ~(raw_b <= -OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_b <= -OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_b <= 1.0 - OVERLAY_LOCAL_CONSIST))
                    | ((raw_b >= OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_b <= OVERLAY_LOCAL_ORDER_MIN)))
                # CEILING masks: where a shape is clearly the INNER layer of
                # this pair (the mirror of the other side's constraint), it
                # must not rise ABOVE that outer shape — without this, a
                # shape's legitimate lift elsewhere bleeds in via the push
                # smoothing and out-escalates the locally-outer shape's lift
                # across rounds (last mover wins: a neckline trim in a multi-layer garment stayed
                # flipped even though the top's constraints FIRED, because
                # the plate's torso lift bled over the rim). A ceiling, NOT a
                # freeze: a sandwiched layer (top above belts, below plate)
                # must still clear the shape beneath it.
                aa = ma & ~(raw_a >= OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_a <= -OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_a <= 1.0 - OVERLAY_LOCAL_CONSIST))
                    | ((raw_a <= -OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_a <= OVERLAY_LOCAL_ORDER_MIN)))
                ab = mb & ~(raw_b >= OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_b >= OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_b >= OVERLAY_LOCAL_CONSIST))
                    | ((raw_b <= -OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_b >= -OVERLAY_LOCAL_ORDER_MIN)))

                # Bind gated verts to their SOURCE partners (k-NN in the
                # ordering frame, split by which side of the vert each
                # partner is on). (i, j) in below_pairs[(a, b)] means: a's
                # vert i must clear b's vert j by LAYER_STACK_GAP; in
                # above_pairs it means a's vert i must stay 0.05 below j.
                def _bind(qv_ov, qv_oc, gate_out, gate_ceil, pv_ov, pv_oc,
                          ptree):
                    KS = min(8, len(pv_ov))
                    dd, jj = ptree.query(qv_ov, k=KS,
                                         distance_upper_bound=OVERLAY_PAIR_R)
                    if KS == 1:
                        dd = dd[:, None]; jj = jj[:, None]
                    val = np.isfinite(dd)
                    ii = np.broadcast_to(
                        np.arange(len(qv_ov))[:, None], val.shape)[val]
                    jj = jj[val]
                    g = qv_oc[ii] - pv_oc[jj]
                    sb = gate_out[ii] & (g >= OVERLAY_LOCAL_ORDER_MIN)
                    # A STRONG source-above partner ceilings the vert
                    # UNCONDITIONALLY (no gate): whatever lifts this vert —
                    # its own tier-2 fire on a different partner, or
                    # smoothing bleed — it must never cross a sheet that sat
                    # clearly above it in the source. This is what keeps a
                    # true vert-scale weave intact when tier-2 fires on its
                    # other side. Weak above partners still need the gate.
                    sa = (g <= -OVERLAY_LOCAL_ORDER_MIN) & (
                        gate_ceil[ii] | (g <= -OVERLAY_LOCAL_RAW_STRONG))
                    below = (ii[sb], jj[sb]) if sb.any() else None
                    above = (ii[sa], jj[sa]) if sa.any() else None
                    return below, above

                bel, abv = _bind(A["_ov"], A["_oc"], oa, aa,
                                 B["_ov"], B["_oc"], tb)
                if bel is not None:
                    below_pairs[(a, b)] = bel
                if abv is not None:
                    above_pairs[(a, b)] = abv
                bel, abv = _bind(B["_ov"], B["_oc"], ob, ab,
                                 A["_ov"], A["_oc"], ta)
                if bel is not None:
                    below_pairs[(b, a)] = bel
                if abv is not None:
                    above_pairs[(b, a)] = abv
        if not below_pairs:
            for j in jobs:
                for k in ("_wv", "_ov", "_oc"):
                    j.pop(k, None)
            return 0

        def _signed(v):
            _, i = ube_tree.query(v, k=1)
            return ((v - bva[i]) * bna[i]).sum(axis=1), i

        # Resolve constraints: 2 rounds, shapes inner -> outer by SOURCE median
        # clearance, so stacking chains cascade in round 1 and round 2 mops up
        # re-violations caused by later lifts. Lift-only against the CURRENT
        # positions of the under-layer; cumulative cap bounds total movement.
        shape_order = sorted(range(n),
                             key=lambda i: float(np.median(jobs[i]["_oc"])))
        cum_push = [np.zeros(len(jobs[i]["_wv"])) for i in range(n)]
        moved_mask = [np.zeros(len(jobs[i]["_wv"]), dtype=bool)
                      for i in range(n)]
        for _round in range(2):
            any_moved = False
            for a in shape_order:
                lifts = [(o, pr) for (s, o), pr in below_pairs.items()
                         if s == a]
                if not lifts:
                    continue
                j = jobs[a]
                v = j["_wv"]
                c, bi = _signed(v)
                # Lift targets: each constrained vert must clear ITS OWN
                # source-below partner verts (at their CURRENT positions) by
                # LAYER_STACK_GAP. No current-frame reference search: the
                # source pairing already says exactly who is beneath whom,
                # so an adjacent sheet that is legitimately ABOVE the vert
                # never enters its target (no leapfrog).
                req = np.full(len(v), -np.inf)
                for o, (ii, jj) in lifts:
                    co, _ = _signed(jobs[o]["_wv"])
                    np.maximum.at(req, ii, co[jj] + LAYER_STACK_GAP)
                with np.errstate(invalid="ignore"):
                    push = req - c
                push[~np.isfinite(push)] = 0.0
                headroom = np.maximum(OVERLAY_CAP - cum_push[a], 0.0)
                # Ceilings: each gated vert stays 0.05 below ITS OWN source-
                # above partners. Applied pre- and post-smooth so neither a
                # constraint nor smoothing bleed can flip a region whose
                # source order says we're underneath.
                allowed = np.full(len(v), np.inf)
                for (s, o), (ii, jj) in above_pairs.items():
                    if s != a:
                        continue
                    co, _ = _signed(jobs[o]["_wv"])
                    ceil_arr = np.full(len(v), np.inf)
                    np.minimum.at(ceil_arr, ii, co[jj])
                    am = np.isfinite(ceil_arr)
                    if am.any():
                        allowed[am] = np.minimum(
                            allowed[am],
                            np.maximum(ceil_arr[am] - 0.05 - c[am], 0.0))
                push = np.minimum(np.clip(push, 0.0, OVERLAY_CAP),
                                  np.minimum(headroom, allowed))
                push = np.minimum(
                    np.clip(_smooth_overlay_push(push, v), 0.0, OVERLAY_CAP),
                    np.minimum(headroom, allowed))
                if _os.environ.get("CBBE2UBE_LAYER_DEBUG"):
                    ncon = len(set(int(x) for _, (ii, _jj) in lifts
                                   for x in ii))
                    ncap = int(((req - c) > push + 1e-9).sum())
                    print(f"    [layer r{_round} s{a}] constrained={ncon} "
                          f"req={int(np.isfinite(req).sum())} "
                          f"pushed={int((push > 0.02).sum())} "
                          f"max={push.max():.3f} capped={ncap}",
                          file=_sys.stderr)
                if (push > 0.02).any():
                    nv = v + push[:, None] * bna[bi]
                    j["verts"] = nv
                    j["verts_modified"] = True
                    j["_wv"] = nv
                    cum_push[a] += push
                    moved_mask[a] |= push > 0.02
                    any_moved = True
            if not any_moved:
                break

        total = int(sum(m.sum() for m in moved_mask))
        for j in jobs:
            for k in ("_wv", "_ov", "_oc"):
                j.pop(k, None)
        return total
    except Exception:
        return 0


def _sync_chest_layered_cloth_weights(shape_jobs: list) -> int:
    """Cleavage-region weight sync across cloth layers in one NIF.

    For shape jobs whose `override_skin` contains breast-bone weighting
    and that have verts in the cleavage box: pick the job with the most
    chest verts as authority and rewrite the bone weights of every other
    candidate's nearby chest verts to match the authority's at the
    closest authority vert. Both layers then move identically under
    physics, eliminating inter-layer intersection.

    Mutates each job's `override_skin` in place. Returns count of
    receiver verts whose weights got replaced (0 = no-op).

    Skips jobs without `override_skin` (they fall back to source skinning
    untouched).
    """
    try:
        candidates = []  # (job, mask_in_shape, n_chest_verts)
        for j in shape_jobs:
            os_ = j.get("override_skin")
            if not os_:
                continue
            weights_map = os_.get("weights") or {}
            if not any("breast" in bn.lower() for bn in weights_map):
                continue
            v = j["verts"]
            if v is None or len(v) == 0:
                continue
            v = np.asarray(v, dtype=np.float64)
            mask = ((v[:, 2] >= CHEST_SYNC_Z_MIN)
                    & (v[:, 2] <= CHEST_SYNC_Z_MAX)
                    & (np.abs(v[:, 0]) <= CHEST_SYNC_X_BOUND)
                    & (v[:, 1] >= CHEST_SYNC_Y_MIN))
            n_chest = int(mask.sum())
            if n_chest < 5:
                continue
            # GATE (prevents the mashup armor regression): only treat this
            # shape as a bust LAYER if its cleavage verts are PREDOMINANTLY
            # breast-bone driven — i.e. it's actual bust cloth (bra / cup /
            # fabric over the bust), not a decorative attachment (cloak,
            # shoulder pad, strap, dagger) that merely has incidental
            # breast weight where it grazes the chest. Without this gate
            # the sync rewrote those ornaments to the bust garment's
            # breast-heavy weights, ballooning them with morph. Compute
            # the breast-weight fraction over THIS shape's chest verts.
            chest_idx = set(int(i) for i in np.where(mask)[0])
            breast_w = 0.0
            total_w = 0.0
            for bn, pairs in weights_map.items():
                is_breast = "breast" in bn.lower()
                for vi, w in pairs:
                    if int(vi) in chest_idx and w > 0.0:
                        total_w += w
                        if is_breast:
                            breast_w += w
            breast_frac = (breast_w / total_w) if total_w > 0 else 0.0
            if breast_frac < CHEST_SYNC_MIN_BREAST_FRAC:
                continue  # decorative attachment, not a bust layer — skip
            candidates.append((j, mask, n_chest))
        if len(candidates) < 2:
            return 0

        # Authority = largest chest-region cloth (typically the outer fabric).
        candidates.sort(key=lambda c: -c[2])
        auth_job, auth_mask, _ = candidates[0]
        receivers = [(j, m) for (j, m, _) in candidates[1:]]

        auth_verts_chest = np.asarray(
            auth_job["verts"], dtype=np.float64)[auth_mask]
        auth_idx_in_shape = np.where(auth_mask)[0]

        # Pre-build authority's per-(chest-vert)-index weight table.
        # Map shape-vert-idx -> local position in auth_idx_in_shape for fast
        # lookup of "is this auth vert in our chest set?".
        auth_shape_to_local = {
            int(vi): i for i, vi in enumerate(auth_idx_in_shape)}
        auth_local_weights: "list[dict[str, float]]" = [
            {} for _ in range(len(auth_idx_in_shape))]
        for bn, pairs in (auth_job["override_skin"]["weights"] or {}).items():
            for vi, w in pairs:
                local = auth_shape_to_local.get(int(vi))
                if local is not None and w > 0.0:
                    auth_local_weights[local][bn] = float(w)

        from scipy.spatial import cKDTree
        tree = cKDTree(auth_verts_chest)
        auth_xforms = auth_job["override_skin"].get("xforms") or {}

        total_synced = 0
        for recv_job, recv_mask in receivers:
            recv_verts = np.asarray(
                recv_job["verts"], dtype=np.float64)[recv_mask]
            recv_idx_in_shape = np.where(recv_mask)[0]

            dists, nearest_local = tree.query(
                recv_verts, k=1, distance_upper_bound=CHEST_SYNC_DISTANCE)

            recv_os = recv_job["override_skin"]
            recv_weights = recv_os.setdefault("weights", {})
            recv_xforms = recv_os.setdefault("xforms", {})
            recv_bones = recv_os.setdefault("bones", [])
            recv_bones_set = set(recv_bones)

            # Collect replacements first, then apply atomically.
            replace_map: "dict[int, dict[str, float]]" = {}
            for ri_local, (d, ai_local) in enumerate(zip(dists, nearest_local)):
                if not np.isfinite(d) or d > CHEST_SYNC_DISTANCE:
                    continue
                ai = int(ai_local)
                if ai < 0 or ai >= len(auth_local_weights):
                    continue
                new_w = auth_local_weights[ai]
                if not new_w:
                    continue
                r_vert_idx = int(recv_idx_in_shape[ri_local])
                replace_map[r_vert_idx] = new_w
                # Ensure all donor bones exist on receiver; copy transform.
                for bn in new_w:
                    if bn not in recv_bones_set:
                        recv_bones.append(bn)
                        recv_bones_set.add(bn)
                        xf = auth_xforms.get(bn)
                        if xf is not None:
                            recv_xforms[bn] = xf

            if not replace_map:
                continue

            # Strip the replaced verts' old weights from EVERY bone in the
            # receiver (so we cleanly overwrite — no leftover contributions).
            replaced_set = set(replace_map.keys())
            for bn in list(recv_weights.keys()):
                recv_weights[bn] = [
                    (vi, w) for (vi, w) in recv_weights[bn]
                    if int(vi) not in replaced_set
                ]

            # Insert the authority's weights for replaced verts.
            for r_vert_idx, weight_dict in replace_map.items():
                for bn, w in weight_dict.items():
                    recv_weights.setdefault(bn, []).append(
                        (r_vert_idx, float(w)))

            total_synced += len(replace_map)

        return total_synced
    except Exception:
        return 0


def _sync_abdomen_layered_cloth_weights(shape_jobs: list) -> int:
    """Butt/belly-region jiggle-weight sync across stacked cloth layers (sibling
    of `_sync_chest_layered_cloth_weights`). Fixes an inner cloth layer that was
    grafted MORE body-jiggle than the outer layer above it (jiggle is proximity-
    grafted; the inner sits closer to the body) and so out-swings the outer and
    punches through it during motion. Authority = the OUTERMOST waist/butt cloth
    layer; every inner layer's nearby verts are rewritten to the authority's
    weights, so the stack moves as one and no inner layer over-swings the outer.
    Only replaces existing per-vert weights with the authority's already-valid
    bones+xforms (no new scale-bone mint -> no STB footgun). Mutates each job's
    `override_skin` in place. Returns receiver verts rewritten (0 = no-op)."""
    if not _ABDO_JIGGLE_SYNC:
        return 0
    try:
        from scipy.spatial import cKDTree
        candidates = []  # (job, mask, n, outerness)
        for j in shape_jobs:
            os_ = j.get("override_skin")
            if not os_:
                continue
            wmap = os_.get("weights") or {}
            if not any(("butt" in bn.lower() or "belly" in bn.lower())
                       for bn in wmap):
                continue
            v = j.get("verts")
            if v is None or len(v) == 0:
                continue
            v = np.asarray(v, dtype=np.float64)
            mask = (v[:, 2] >= ABDO_SYNC_Z_MIN) & (v[:, 2] <= ABDO_SYNC_Z_MAX)
            n = int(mask.sum())
            if n < 5:
                continue
            # jiggle-dominant gate (mirrors the chest breast-frac gate): only a
            # real jiggling cloth layer qualifies, not a rigid strap/buckle that
            # merely grazes butt weight. Fraction over THIS shape's region verts.
            idxset = set(int(i) for i in np.where(mask)[0])
            jw = 0.0
            tw = 0.0
            for bn, pairs in wmap.items():
                isj = ("butt" in bn.lower() or "belly" in bn.lower())
                for vi, w in pairs:
                    if int(vi) in idxset and w > 0.0:
                        tw += w
                        if isj:
                            jw += w
            if tw <= 0 or (jw / tw) < ABDO_SYNC_MIN_JIGGLE_FRAC:
                continue
            rv = v[mask]
            outerness = float(np.median(np.sqrt(rv[:, 0] ** 2 + rv[:, 1] ** 2)))
            candidates.append((j, mask, n, outerness))
        if len(candidates) < 2:
            return 0

        # Authority = OUTERMOST layer (largest waist radius) so inner layers
        # REDUCE to its (already clearance-validated) motion -> inner <= outer.
        candidates.sort(key=lambda c: -c[3])
        auth_job, auth_mask, _, _ = candidates[0]
        receivers = [(j, m) for (j, m, _, _) in candidates[1:]]

        auth_verts = np.asarray(auth_job["verts"], dtype=np.float64)[auth_mask]
        auth_idx_in_shape = np.where(auth_mask)[0]
        auth_shape_to_local = {int(vi): i for i, vi in enumerate(auth_idx_in_shape)}
        auth_local_weights = [{} for _ in range(len(auth_idx_in_shape))]
        for bn, pairs in (auth_job["override_skin"]["weights"] or {}).items():
            for vi, w in pairs:
                local = auth_shape_to_local.get(int(vi))
                if local is not None and w > 0.0:
                    auth_local_weights[local][bn] = float(w)
        tree = cKDTree(auth_verts)
        auth_xforms = auth_job["override_skin"].get("xforms") or {}

        def _isjig(b):
            bl = b.lower()
            return ("butt" in bl or "belly" in bl or "breast" in bl)

        total_synced = 0
        for recv_job, recv_mask in receivers:
            recv_verts = np.asarray(
                recv_job["verts"], dtype=np.float64)[recv_mask]
            recv_idx_in_shape = np.where(recv_mask)[0]
            dists, nearest_local = tree.query(
                recv_verts, k=1, distance_upper_bound=ABDO_SYNC_DISTANCE)
            recv_os = recv_job["override_skin"]
            recv_weights = recv_os.setdefault("weights", {})
            recv_xforms = recv_os.setdefault("xforms", {})
            recv_bones = recv_os.setdefault("bones", [])
            recv_bones_set = set(recv_bones)
            # JIGGLE-ONLY: each touched receiver vert takes the authority's
            # jiggle-bone weights; its OWN base (thigh/pelvis/spine) skinning is
            # KEPT and merely rescaled to absorb the delta (total stays 1). This
            # is the fix for the inner-thigh clip a full-weight replace caused --
            # the leg deformation must stay the receiver's own.
            target = {}   # recv vert idx -> {jiggle bone: authority weight}
            for ri_local, (d, ai_local) in enumerate(zip(dists, nearest_local)):
                if not np.isfinite(d) or d > ABDO_SYNC_DISTANCE:
                    continue
                ai = int(ai_local)
                if ai < 0 or ai >= len(auth_local_weights):
                    continue
                aw = auth_local_weights[ai]
                if not aw:
                    continue
                target[int(recv_idx_in_shape[ri_local])] = {
                    b: w for b, w in aw.items() if _isjig(b)}
            if not target:
                continue
            # current per-vert weights for the touched verts
            cur = {vi: {} for vi in target}
            for bn, pairs in recv_weights.items():
                for vi, w in pairs:
                    ivi = int(vi)
                    if ivi in cur and w > 0.0:
                        cur[ivi][bn] = float(w)
            new_per_vert = {}
            for vi, auth_jig in target.items():
                c = cur.get(vi, {})
                tot = sum(c.values()) or 1.0
                base = {b: w for b, w in c.items() if not _isjig(b)}
                base_tot = sum(base.values())
                new_jig_tot = sum(auth_jig.values())
                target_base = max(0.0, tot - new_jig_tot)
                nb = {}
                if base_tot > 1e-9:
                    sc = target_base / base_tot
                    for b, w in base.items():
                        nb[b] = w * sc
                elif target_base > 0:
                    nb["NPC Pelvis [Pelv]"] = target_base
                for b, w in auth_jig.items():
                    if w > 1e-6:
                        nb[b] = nb.get(b, 0.0) + w
                        if b not in recv_bones_set:
                            recv_bones.append(b)
                            recv_bones_set.add(b)
                            xf = auth_xforms.get(b)
                            if xf is not None:
                                recv_xforms[b] = xf
                new_per_vert[vi] = nb
            ts = set(new_per_vert)
            for bn in list(recv_weights.keys()):
                recv_weights[bn] = [(vi, w) for (vi, w) in recv_weights[bn]
                                    if int(vi) not in ts]
            for vi, wd in new_per_vert.items():
                for b, w in wd.items():
                    if w > 1e-6:
                        recv_weights.setdefault(b, []).append((vi, float(w)))
            total_synced += len(new_per_vert)
        return total_synced
    except Exception:
        return 0


SCALE_BONE_MAX_TRANSFER_HANDS_FEET = 0.45  # for boots / gauntlets / similar
                                          # hand+foot-rigged armor. Static
                                          # buffer alone doesn't help when
                                          # body sliders push past it; giving
                                          # these shapes a moderate body-bone
                                          # scale share makes them follow
                                          # body morphs proportionally. Cap
                                          # at ~half so the rigid foot/calf/
                                          # hand bones still dominate for
                                          # animation tracking — preserves
                                          # finger/toe stability.
SCALE_BONE_MAX_TRANSFER_RIGID = 0.15  # for rigid attachments (1-bone-dominated
                                      # shapes like daggers, scabbards,
                                      # pauldrons): keep most of the weight
                                      # on their original parent bone so
                                      # animation tracking is preserved
                                      # (dagger stays on thigh during walk
                                      # cycle), but inject a small slice
                                      # of scale-bone weight so the shape
                                      # at least partially follows body
                                      # morphs (uniform scale by ~15% of
                                      # the corresponding body change).
RIGID_DOMINANT_FRACTION = 0.65  # if a single bone holds >= this fraction
                                # of the shape's total weight, treat the
                                # shape as a rigid attachment.
                                # Threshold lowered from 0.85 because
                                # M6 body-blend reskin runs before
                                # add_scale_bone_weights and dilutes
                                # source single-bone dominance (Dagger
                                # source 90% -> post-M6 84%); 0.65
                                # catches all post-blend rigid shapes
                                # (Scabbard_2 fell to 66.1%) while still
                                # excluding genuine cloth (max ~40-55%
                                # on dominant bone — Cape semi-rigid is
                                # 55% on Spine2 and stays cloth-treated).


def _is_rigid_attachment(weights_by_bone: dict[str, list[tuple[int, float]]]) -> bool:
    """Return True if the shape's bone weights are dominated by a single
    bone (>= RIGID_DOMINANT_FRACTION of total — currently 0.65). Rigid
    attachments (daggers, scabbards, pouches, pauldrons, etc.) match this
    — they're designed to track ONE parent bone for proper animation,
    with negligible weight on other bones. Cloth shapes typically have
    weight distributed across 3+ bones with no single bone exceeding
    ~50-55%, so they don't match.
    """
    per_bone_totals = {}
    for bn, pairs in (weights_by_bone or {}).items():
        per_bone_totals[bn] = sum(float(w) for _, w in pairs)
    total = sum(per_bone_totals.values())
    if total <= 0:
        return False
    return (max(per_bone_totals.values()) / total) >= RIGID_DOMINANT_FRACTION


# (#166 `_needs_bone_driven_scaling` removed: it gated scale bones to few-bone
# rigid props only, on the misdiagnosis that multi-bone cloth morphs via the TRI.
# It doesn't — cloth has no per-shape BODYTRI, so scale bones are its only
# tracking layer. The robe-skirt bone-overflow CTD is handled by `_cap_skin_bone_count`; the
# Forsworn double by body-skin detection. Scale bones now go to ALL cloth again.)


# Skyrim's GPU skinning supports ~80 bones per skin partition. Exceeding it
# overruns the bone-matrix palette at draw -> equip CTD. This backstop catches
# scale bones from any source (reskin transfer, add_scale_bone_weights, dense rigs).
SKIN_PARTITION_BONE_CAP = 78

# A single skin partition with a very high VERTEX count is not safe for the
# runtime body-morph rebuild (NioOverride/RaceMenu): measured equip CTD when a
# ~31.8k-vert torso shape sat in ONE partition (the morph walk read past the
# vertex buffer at vertex 32768). The injected UBE body itself ships MULTIPLE
# partitions; we mirror that by splitting any over-cap shape into vertex-balanced
# partitions (CTD-safe, drops no bone/vert). Distinct from the BONE-count cap.
SKIN_PARTITION_VERT_CAP = 16000


def _cap_skin_bone_count(bone_names, xforms_map, weights_map,
                         limit=SKIN_PARTITION_BONE_CAP):
    """If a shape references more than `limit` bones, keep the `limit` most
    LOCALLY-DOMINANT (by MAX per-vertex weight) and drop the rest, then
    renormalize each vertex so its weights still sum to 1.0. Returns the
    (possibly trimmed) skin tuple. Prevents overrunning Skyrim's per-partition
    GPU bone cap (render CTD). #166

    Rank by max-per-vert weight, NOT total weight: a bone that is the dominant
    influence on even a handful of verts — a robe SKIRT or cape physics chain
    bone (e.g. `Skirt_Back 04`, max ~0.75 on its 18 hem verts) — is
    locally critical; dropping it collapses those verts (distortion) and kills
    the skirt's HDT-SMP sway (no physics). A scale-bone "tracking" tail
    propagated thinly across the whole shape (max ~0.02) is the safe thing to
    drop. The original total-weight ranking did the exact opposite and evicted
    a long robe's skirt physics bones. Tie-break by total weight."""
    names = list(bone_names or [])
    if len(names) <= limit:
        return bone_names, xforms_map, weights_map

    def _importance(b):
        prs = weights_map.get(b) or []
        mx = max((float(w) for _, w in prs), default=0.0)
        tot = sum(float(w) for _, w in prs)
        return (mx, tot)
    keep = set(sorted(names, key=_importance, reverse=True)[:limit])
    new_names = [b for b in names if b in keep]
    new_x = {b: v for b, v in (xforms_map or {}).items() if b in keep}
    new_w = {b: list(v) for b, v in (weights_map or {}).items() if b in keep}
    psum: dict = {}
    for prs in new_w.values():
        for vi, w in prs:
            psum[vi] = psum.get(vi, 0.0) + float(w)
    for b in list(new_w):
        new_w[b] = [(int(vi),
                     (float(w) / psum[vi] if psum.get(vi, 0.0) > 1e-9
                      else float(w)))
                    for vi, w in new_w[b]]
    return new_names, new_x, new_w


def _cached_scale_bone_data(body_shape, leg_region_only: bool,
                            exclude_substrings: tuple[str, ...] = ()):
    """Build (and cache) the per-scale-bone KD-trees from the BODY. Keyed by the
    body-shape identity + leg_region_only + exclude_substrings (the only inputs).
    Armor-independent, read-only after build, so safe to share across every
    shape/NIF in a worker.
    Returns (scale_bones, {bone: (bone_verts, cKDTree, weights)}).

    `exclude_substrings`: lowercase bone-name substrings to drop entirely (e.g.
    ("frontthigh","rearthigh") for calf/foot boots, so the fade-inducing far-thigh
    scale bones are never grafted -- see _boot_far_thigh_scale_exclusions)."""
    excl = tuple(sorted(exclude_substrings or ()))
    # Key on id(body_shape) BUT keep a strong ref to the body in the cache value:
    # Python recycles an object's id() after it is GC'd, so a bare id key can
    # return a DIFFERENT (now-dead-and-replaced) body's KD-trees -> wrong scale
    # weights. Holding the body alive keeps its id stable; the `is` guard is a
    # belt-and-suspenders miss if an id somehow still collides. #idreuse-cache
    key = (id(body_shape), bool(leg_region_only), excl)
    cached = _SCALE_BONE_DATA_CACHE.get(key)
    if cached is not None and cached[0] is body_shape:
        return cached[1], cached[2]
    from scipy.spatial import cKDTree
    body_verts = np.asarray(body_shape.verts, dtype=np.float64)
    body_n = len(body_verts)
    scale_bones = [b for b in (body_shape.bone_names or []) if _is_scale_bone(b)]
    if leg_region_only:
        # Hand/foot: only LEG scale bones are anatomically legitimate (a boot's
        # calf follows RearCalf); torso bones would be bind-pose cross-talk.
        scale_bones = [b for b in scale_bones
                       if ("thigh" in b.lower() or "calf" in b.lower())]
    if excl:
        scale_bones = [b for b in scale_bones
                       if not any(sub in b.lower() for sub in excl)]
    body_bw = body_shape.bone_weights or {}
    bone_data: dict = {}
    for bn in scale_bones:
        pairs = body_bw.get(bn) or []
        if not pairs:
            continue
        idxs_w = np.array([i for i, _ in pairs if 0 <= i < body_n], dtype=np.int64)
        wts = np.array([w for i, w in pairs if 0 <= i < body_n], dtype=np.float64)
        if idxs_w.size == 0 or wts.max() <= 0:
            continue
        bone_verts = body_verts[idxs_w]
        bone_data[bn] = (bone_verts, cKDTree(bone_verts), wts)
    _SCALE_BONE_DATA_CACHE[key] = (body_shape, scale_bones, bone_data)
    return scale_bones, bone_data


def add_scale_bone_weights(
    bone_names: list[str],
    xforms_by_bone: dict,
    weights_by_bone: dict[str, list[tuple[int, float]]],
    armor_verts: np.ndarray,
    body_shape,
    *,
    reach: float = SCALE_BONE_REACH,
    k: int = SCALE_BONE_K,
    max_transfer: float = SCALE_BONE_MAX_TRANSFER,
    exclude_vert_mask: "np.ndarray | None" = None,
    leg_region_only: bool = False,
    torso_parity: bool = False,
    exclude_scale_bone_substrings: tuple[str, ...] = (),
) -> tuple[list[str], dict, dict[str, list[tuple[int, float]]]]:
    """Add 3BA scale-bone (Breast / Butt / Belly / Thigh / etc.) weights
    to an armor shape so all of its verts respond to body sliders via
    bone scaling — even when the armor sits too far from the body for
    the M6 blend reskin to reach it.

    Algorithm:
      1. Extract the body's scale-bone weights into dense arrays.
      2. For each armor vert, find K nearest body verts (within `reach`).
      3. IDW-blend each scale bone's weight onto the armor vert,
         multiplied by a distance falloff (1.0 at d=0, 0.0 at d=reach).
      4. Cap the total transferred weight at `max_transfer` of the
         armor vert's existing total. This preserves the armor's
         original rigid-skeleton skinning (animation still works) while
         adding a small scale-bone component that follows body morphs.
      5. Renormalize so per-vert weights still sum to 1.0.
      6. Clip to Skyrim's 4-bones-per-vert hard cap.

    Returns the updated (bone_names, xforms_by_bone, weights_by_bone)
    — same shape as `compute_body_blend_skinning` so the call sites can
    chain them.

    exclude_vert_mask: optional boolean array (len == armor_verts). Where
      True, NO scale-bone weight is added to that vert and its existing
      skinning is left untouched. Used for hand/foot shapes to keep body
      morphs off finger/toe verts (see `_extremity_vert_mask`).

    exclude_scale_bone_substrings: lowercase bone-name substrings whose scale
      bones are dropped entirely before the graft (never added to any vert).
      Used for calf/foot boots to drop the far-thigh scale bones that fade the
      boot at distance while keeping RearCalf (see _boot_far_thigh_scale_exclusions).
    """
    # Auto-detect rigid attachments (dagger, scabbard, pauldron, pouch
    # etc. — one bone holds RIGID_DOMINANT_FRACTION+ of the weight) and use the low
    # transfer rate so their animation tracking to their parent bone is
    # preserved while still adding some morph response. Cloth shapes
    # (weight distributed across many bones, no single one dominant)
    # keep the aggressive default transfer.
    if _is_rigid_attachment(weights_by_bone):
        max_transfer = SCALE_BONE_MAX_TRANSFER_RIGID

    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    armor_n = len(armor_verts)

    # Per-bone KD-trees (cached): per-bone search ensures propagation from the
    # actual nearest bone-weighted vert, not from nearby verts that lack the bone.
    scale_bones, bone_data = _cached_scale_bone_data(
        body_shape, leg_region_only, exclude_scale_bone_substrings)
    if not bone_data:
        return bone_names, xforms_by_bone, weights_by_bone

    # Existing per-vert total weight; also track dominant bone per vert.
    # Used by: (a) torso-parity boost (skips arm-dominated verts) and
    # (b) arm-suppression (no breast/belly/butt weight on arm-dominated verts).
    existing_per_vert = np.zeros(armor_n, dtype=np.float64)
    _dom_w = np.zeros(armor_n, dtype=np.float64)
    arm_vert = np.zeros(armor_n, dtype=bool)
    leg_vert = np.zeros(armor_n, dtype=bool)
    for bn, pairs in weights_by_bone.items():
        bn_is_arm = _is_arm_hand_bone(bn)
        bn_is_leg = _is_leg_rigid_bone(bn)
        for vi, w in pairs:
            if 0 <= vi < armor_n:
                existing_per_vert[vi] += w
                if w > _dom_w[vi]:
                    _dom_w[vi] = w
                    arm_vert[vi] = bn_is_arm
                    leg_vert[vi] = bn_is_leg
    # Defensive: if any vert has zero existing weight, assume 1.0.
    existing_per_vert[existing_per_vert < 1e-6] = 1.0
    boost_ok = ~arm_vert  # verts eligible for the torso-parity power falloff
    # Leg-encasing armor: suppress breast/butt/belly jiggle bones on shapes whose
    # majority of verts are dominated by rigid leg bones. Static leg-shape bones
    # (frontthigh/rearthigh/rearcalf) still apply for size sliders.
    # Shape-level check: hip/waist bands are pelvis-dominated, not leg-dominated.
    leg_armor = armor_n > 0 and int(leg_vert.sum()) > 0.5 * armor_n

    # Proposed scale-bone weights: use MAX of K nearest verts' weights (not IDW).
    # IDW dilutes body weight magnitude (NPC Belly peaks at 0.183; K=8 IDW ~0.115).
    # MAX preserves the body's actual weight: cloth vert next to a 0.18-weight
    # body vert gets 0.18*falloff ≈ 0.16 (~1:1). 3BA per-bone weight is bounded
    # so MAX can't pathologically over-weight.
    proposed_scale: dict[str, np.ndarray] = {}
    for bn, (bone_verts, bone_tree, bone_wts) in bone_data.items():
        if _is_physics_jiggle_scale_bone(bn) and (leg_armor or NO_SOFTBODY_SCALES):
            # rigid leg plate, OR #nosoftscale test: no breast/butt/belly jiggle
            # transfer (the soft-body bones that drag armor on the UBE actor).
            continue
        k_eff = min(k, len(bone_verts))
        d, j = bone_tree.query(armor_verts, k=k_eff)
        if k_eff == 1:
            d = d[:, None]; j = j[:, None]
        # Falloff against the NEAREST bone-weighted body vert. Torso parity
        # (#175/#129): chest/belly/butt bones on body-slot pieces use a steeper
        # POWER curve so a close-fitting plate that sits a few units off the
        # body still tracks the live morph near parity instead of being crushed
        # to ~0.38 by the linear curve. Same `reach`, so no extra cross-body
        # reach -- only the in-reach magnitude rises, and it stays bounded by
        # the body's own weight (prop = body_weight * falloff, falloff <= 1).
        nearest = d[:, 0]
        if torso_parity and _is_torso_parity_bone(bn):
            # Power curve on torso verts; arm/hand-dominated verts keep linear
            # to prevent bind-pose arm<->torso cross-talk.
            power = np.clip(
                1.0 - (nearest / reach) ** TORSO_PARITY_FALLOFF_POWER,
                0.0, 1.0)
            linear = np.clip(1.0 - nearest / reach, 0.0, 1.0)
            falloff = np.where(boost_ok, power, linear)
        else:
            falloff = np.clip(1.0 - nearest / reach, 0.0, 1.0)
        if not np.any(falloff > 0):
            continue
        # Take the strongest body-side weight among the K neighbors.
        # Distance-blend: zero out neighbors past `reach`.
        neighbor_wts = bone_wts[j]
        neighbor_active = d < reach
        neighbor_wts = np.where(neighbor_active, neighbor_wts, 0.0)
        prop = neighbor_wts.max(axis=1) * falloff
        # Never add torso (breast/belly/butt) scale weight to arm-dominated verts.
        if SUPPRESS_TORSO_SCALE_ON_ARMS and _is_torso_parity_bone(bn):
            prop = np.where(arm_vert, 0.0, prop)
        if prop.max() <= 1e-6:
            continue
        proposed_scale[bn] = prop

    # Zero scale-bone weight on excluded verts (finger/toe) before cap math,
    # so their original skinning is fully preserved.
    if exclude_vert_mask is not None:
        em = np.asarray(exclude_vert_mask, dtype=bool)
        if em.shape[0] == armor_n:
            for bn in list(proposed_scale.keys()):
                proposed_scale[bn] = np.where(em, 0.0, proposed_scale[bn])

    # Drop bones entirely zeroed (e.g. butt weight that only reached masked verts).
    proposed_scale = {bn: p for bn, p in proposed_scale.items()
                      if float(p.max()) > 1e-6}
    if not proposed_scale:
        return bone_names, xforms_by_bone, weights_by_bone

    # Cap proposed scale weight sum at max_transfer of existing total.
    proposed_sum = np.zeros(armor_n, dtype=np.float64)
    for prop in proposed_scale.values():
        proposed_sum += prop
    target_cap = existing_per_vert * max_transfer
    scale_factor = np.where(
        proposed_sum > target_cap,
        target_cap / np.maximum(proposed_sum, 1e-9),
        1.0,
    )
    for bn in proposed_scale:
        proposed_scale[bn] = proposed_scale[bn] * scale_factor

    # Shrink existing weights proportionally so per-vert total stays at 1.0.
    occupied = np.minimum(proposed_sum * scale_factor, target_cap)
    keep_fraction = 1.0 - occupied / existing_per_vert  # in [0.5, 1.0]
    weights_by_bone = {
        bn: [(vi, w * float(keep_fraction[vi]))
             for vi, w in pairs
             if 0 <= vi < armor_n and w * keep_fraction[vi] > 1e-6]
        for bn, pairs in weights_by_bone.items()
    }
    # Drop bones that ended up empty.
    weights_by_bone = {bn: p for bn, p in weights_by_bone.items() if p}

    # Inject scale-bone weights.
    body_xforms = {}
    for bn in scale_bones:
        try:
            xf = body_shape.get_shape_skin_to_bone(bn)
            if xf is not None:
                body_xforms[bn] = xf
        except Exception:
            pass
    # At the GPU bone cap: add WEIGHT to existing scale bones but don't inject
    # new ones (evicting existing bones makes things worse). New scale bones
    # admitted only while room remains under the cap.
    present = set(bone_names)
    room = SKIN_PARTITION_BONE_CAP - len(present)
    for bn, prop in proposed_scale.items():
        is_new = bn not in present
        if is_new and room <= 0:
            continue  # at the bone cap -> don't inject a new bone
        new_pairs = weights_by_bone.get(bn, [])
        # Convert existing entries to a dict for quick merge.
        existing_dict = {vi: w for vi, w in new_pairs}
        for vi in np.where(prop > 1e-6)[0]:
            existing_dict[int(vi)] = existing_dict.get(int(vi), 0.0) + float(prop[vi])
        weights_by_bone[bn] = sorted(existing_dict.items())
        if is_new:
            bone_names = bone_names + [bn]
            present.add(bn)
            room -= 1
            if bn in body_xforms:
                xforms_by_bone[bn] = body_xforms[bn]

    # Renormalize per vert and cap to top 4 bones (Skyrim hard limit).
    bone_list = list(weights_by_bone.keys())
    stack = np.zeros((len(bone_list), armor_n), dtype=np.float64)
    for i, bn in enumerate(bone_list):
        for vi, w in weights_by_bone[bn]:
            if 0 <= vi < armor_n:
                stack[i, vi] = w
    # Top-4 per vert.
    MAX_BONES_PER_VERT = 4
    if len(bone_list) > MAX_BONES_PER_VERT:
        topk = np.argsort(-stack, axis=0)[:MAX_BONES_PER_VERT, :]
        mask = np.zeros_like(stack, dtype=bool)
        for r in range(MAX_BONES_PER_VERT):
            mask[topk[r], np.arange(armor_n)] = True
        stack = np.where(mask, stack, 0.0)
    per_vert_sum = stack.sum(axis=0)
    nz = per_vert_sum > 1e-7
    stack[:, nz] /= per_vert_sum[nz]

    WEIGHT_EPS = 1e-4
    new_weights_by_bone: dict[str, list[tuple[int, float]]] = {}
    for i, bn in enumerate(bone_list):
        idxs2 = np.where(stack[i] >= WEIGHT_EPS)[0]
        if len(idxs2) == 0:
            continue
        new_weights_by_bone[bn] = [(int(j), float(stack[i, j])) for j in idxs2]
    bone_names = list(new_weights_by_bone.keys())
    xforms_by_bone = {bn: xforms_by_bone[bn] for bn in bone_names if bn in xforms_by_bone}
    return bone_names, xforms_by_bone, new_weights_by_bone


HDT_BONE_THRESHOLD = 0.4  # fraction of armor bones unknown to the body
                          # required to classify a shape as HDT-SMP rigged.


# --- Chain-cloth -> soft-body conversion (gated option) --------------------
# Authored HDT-SMP bone-chain cloth (Skirt_NN / FR_NN + constraints) can
# collapse to origin on the UBE race. When set, all chain-preservation paths
# are disabled and chain shapes fall through to the normal reskin pipeline,
# producing per-vertex soft-body cloth (stable on UBE). Trade-off: no
# independent swing — cloth follows the body and jiggles with body physics.
# Default OFF (byte-for-byte output unchanged). Set CBBE2UBE_CHAIN_TO_SOFTBODY=1
# before launch to convert affected armor as soft-body.
# Guards: _shape_has_hdt_smp_rigging, _hdt_softbody_shape_names,
# _source_hdt_needs_missing_chain_bones, _precreate_custom_bone_chains,
# _finalize_hdt_physics.
CHAIN_TO_SOFTBODY = (
    os.environ.get("CBBE2UBE_CHAIN_TO_SOFTBODY", "").strip().lower()
    in ("1", "true", "yes", "on")
)


def _shape_has_hdt_smp_rigging(src_shape, body_bone_names: set[str]) -> bool:
    """Detect armor shapes rigged for HDT-SMP physics chains.

    Returns True if more than `HDT_BONE_THRESHOLD` of the shape's
    source bones are NOT in the UBE body's bone list — a strong
    signal that the shape uses mod-specific physics chain bones
    (e.g. a hand-authored UBE armor's `physics-chain bones (prefix_NN)`, a heavily-boned armor's accessory
    chain bones). M6 proximity-blend re-skin would replace those
    chain bones with body bones on close-to-body verts, killing
    the HDT-SMP physics that drives cloth/breast/skirt animation.

    Threshold of 0.4 means: if 40%+ of armor bones are unknown to
    the body, treat as HDT-rigged. Standard armor shapes share
    most of their bones with the body (spine, breast, butt, etc.).
    HDT chain shapes have many _01, _02 chain bones unique to the
    mod author's setup.
    """
    if CHAIN_TO_SOFTBODY:
        # Soft-body mode: don't protect chains from reskin -> the shape
        # falls through to the body-fit reskin and becomes soft-body cloth.
        return False
    armor_bones = set(src_shape.bone_names or [])
    if not armor_bones:
        return False
    unknown = armor_bones - body_bone_names
    return len(unknown) / len(armor_bones) > HDT_BONE_THRESHOLD


# Multi-layer cloth cuirasses (Cuirass_A/_B/_C, Robe_01/_02) are authored bone-driven
# cloth that a RUNTIME config often drives with HDT-SMP (not the NIF, so
# _shape_has_hdt_smp_rigging can't see it -- they're weighted to body bones, not custom
# chain bones, so the garment-chain check misses them too). Every body-follow pass (the
# M6 reskin AND the conform/jiggle passes) grafts the body's HDT-SMP JIGGLE bones
# (Breast/Butt/Belly) onto them; the runtime then drives that cloth by those SMP bones
# and the engine CTDs on equip (New Leather "Cuirass_A/B", crash 2026-07-09). So KEEP
# their SOURCE skin -- skip EVERY graft pass for them (pynifly can't cleanly remove a
# bone after the fact, so prevention is the only reliable path). Detect structurally:
# 2+ sibling shapes sharing a base stem + a short layer suffix. Off with
# CBBE2UBE_NO_LAYERED_CLOTH_SKIN. #layered-cloth-skin
_LAYERED_CLOTH_SKIN = (
    os.environ.get("CBBE2UBE_NO_LAYERED_CLOTH_SKIN", "").strip().lower()
    not in ("1", "true", "yes", "on"))
_LAYER_SUFFIX_RE = re.compile(r"^(.*?)[_ ]([A-Za-z]|\d{1,2})$")


def _layered_cloth_shape_names(shapes) -> "set[str]":
    """Names of shapes in a MULTI-LAYER cloth group: 2+ shapes whose names share a base
    stem and differ only by a short layer suffix (Cuirass_A/_B/_C, Robe_01/_02). Such
    authored cloth keeps its SOURCE skin -- every body-follow graft pass skips it, so the
    body's HDT-SMP jiggle bones aren't grafted on and the shape doesn't CTD on equip.
    #layered-cloth-skin"""
    if not _LAYERED_CLOTH_SKIN:
        return set()
    groups: "dict[str, list[str]]" = {}
    for s in shapes:
        nm = getattr(s, "name", "") or ""
        m = _LAYER_SUFFIX_RE.match(nm)
        if m:
            groups.setdefault(m.group(1).lower(), []).append(nm)
    return {n for members in groups.values() if len(members) >= 2 for n in members}


def detect_zfight_pairs(
    armor_shape_verts: dict[str, np.ndarray],
    body_verts: np.ndarray,
    body_normals: np.ndarray,
    *,
    threshold: float = 0.05,
) -> dict[str, np.ndarray]:
    """Detect verts in pairs of armor shapes that sit within `threshold`
    of each other along the body normal direction — classic z-fight
    setup where two cloth/leather layers occupy the same depth.

    Returns a dict mapping shape_name -> per-vert push offsets (delta
    along body normal, signed). Inner shape's overlapping verts get a
    small negative push (toward body), outer's a small positive push
    (away from body). Layers that were intentionally co-planar (which
    doesn't really happen in well-authored armor) would also get
    split, which is desirable for render.

    Algorithm:
      1. For each armor shape, compute its per-vert signed distance to
         body surface (sign comes from body normal at nearest body vert).
      2. For each pair of shapes, K-NN search shape A's verts in shape B.
      3. For pairs within `threshold` of each other:
         - whichever vert has SMALLER signed distance is "inner",
           push it inward by `threshold * 1.1` (full threshold + 10%
           margin so the pushed pair clears the detection radius)
         - the other ("outer") is left at original position

    Returns offsets that should be ADDED to verts along their nearest
    body-vert's normal direction. Caller applies and re-saves.
    """
    from scipy.spatial import cKDTree
    body_verts = np.asarray(body_verts, dtype=np.float64)
    body_normals = np.asarray(body_normals, dtype=np.float64)
    body_tree = cKDTree(body_verts)

    # Per-shape: signed distance to body surface + nearest body normal
    # (so we know which direction is "outward" for push).
    shape_signed: dict[str, np.ndarray] = {}
    shape_outward: dict[str, np.ndarray] = {}
    for name, av in armor_shape_verts.items():
        av_arr = np.asarray(av, dtype=np.float64)
        _, idx = body_tree.query(av_arr, k=1)
        body_pts = body_verts[idx]
        nrm = body_normals[idx]
        rel = av_arr - body_pts
        signed = (rel * nrm).sum(axis=1)
        shape_signed[name] = signed
        shape_outward[name] = nrm

    # Per-shape push offset (along outward normal). Accumulates from
    # multiple pair conflicts so a vert in multiple z-fight pairs
    # gets pushed appropriately.
    offsets_per_shape: dict[str, np.ndarray] = {
        name: np.zeros(len(av), dtype=np.float64)
        for name, av in armor_shape_verts.items()
    }

    names = list(armor_shape_verts.keys())
    # Per-shape KDTree for fast pairwise vert lookup.
    shape_trees = {
        name: cKDTree(np.asarray(av, dtype=np.float64))
        for name, av in armor_shape_verts.items()
    }

    # Push by full threshold (plus a 10% safety margin) so that
    # post-push vert pairs are guaranteed to sit beyond the detection
    # radius. Pushing by threshold/2 leaves coplanar pairs still
    # within threshold — detection retrips next time.
    PUSH = threshold * 1.1
    for i in range(len(names)):
        a_name = names[i]
        a_verts = np.asarray(armor_shape_verts[a_name], dtype=np.float64)
        for j in range(i + 1, len(names)):
            b_name = names[j]
            # Query B's tree for each A vert; threshold is the radius.
            b_tree = shape_trees[b_name]
            # Use distance_upper_bound to limit search to verts within
            # threshold. Inefficient on tiny shapes; fast on big ones.
            dists, idxs = b_tree.query(a_verts, k=1,
                                       distance_upper_bound=threshold)
            valid = dists != np.inf
            if not valid.any():
                continue
            a_idxs = np.where(valid)[0]
            b_idxs = idxs[valid]
            # For each conflicting pair, push the inner one inward.
            a_signed = shape_signed[a_name][a_idxs]
            b_signed = shape_signed[b_name][b_idxs]
            a_is_inner = a_signed < b_signed
            # A inner -> push A's vert inward (negative along normal)
            offsets_per_shape[a_name][a_idxs[a_is_inner]] -= PUSH
            # B inner -> push B's vert inward
            offsets_per_shape[b_name][b_idxs[~a_is_inner]] -= PUSH

    return offsets_per_shape


def _recompute_vertex_normals(
        verts: np.ndarray, tris,
        source_normals: "np.ndarray | None" = None,
) -> np.ndarray:
    """Compute per-vertex normals from triangle geometry.

    Used when an override_verts is supplied to _copy_shape — the
    source's stored normals were baked for the ORIGINAL vert
    positions; using them on snap-modified positions produces
    speckled / noisy shading because the per-vertex normal points
    away from the surface tangent of the new geometry.

    Standard area-weighted approach: face normal per tri (cross of
    two edges), accumulated into incident verts, then normalized.
    Triangles that produce a zero face normal (degenerate) are
    skipped via the safe-divide on the norm.

    BOUNDARY-VERT FIX: at a topology hole (a vert on a boundary edge
    incident to only one triangle), the area-weighted recompute can
    flip the normal inward if the lone adjacent face winds away from
    where the missing geometry would have been. This was observed on
    UBE BaseShape's 84-vert pubic boundary loop (Z=64.9-65.6, X=+/-0.2)
    after the genital bake displaced verts: 84 boundary normals
    flipped, turning their triangles back-face-culled and looking
    like a chunk of mesh had been deleted. When `source_normals` is
    provided, any recomputed normal whose dot with the source is
    negative is flipped back to align with the source — preserves
    visual continuity at boundary verts without disabling the
    recompute elsewhere.
    """
    verts = np.asarray(verts, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    face_normals = np.cross(v1 - v0, v2 - v0)
    # Don't pre-normalize face normals — leaving magnitudes in lets
    # larger tris contribute more to their verts (area weighting),
    # which is the standard convention for smooth shading.
    vert_normals = np.zeros_like(verts)
    for i in range(3):
        np.add.at(vert_normals, tris[:, i], face_normals)
    vn_len = np.linalg.norm(vert_normals, axis=1, keepdims=True)
    vn_len[vn_len < 1e-9] = 1.0
    out = vert_normals / vn_len

    if source_normals is not None:
        src = np.asarray(source_normals, dtype=np.float64)
        if src.shape == out.shape:
            # Per-vert sign alignment: if recomputed disagrees with
            # source by >90 degrees, flip it. Hits ~all interior verts
            # as a no-op (recompute already agrees on a smooth mesh)
            # and only matters at boundary verts where the recompute
            # is fundamentally underdetermined.
            dot = (out * src).sum(axis=1, keepdims=True)
            flip_mask = dot < 0
            out = np.where(flip_mask, -out, out)
    return out


# Pubic-region bbox for the topology-hole closure pass on UBE BaseShape.
# UBE ships BaseShape with open boundary loops at the pubis (for TNG/SoS plug-mesh).
# _close_pubic_holes seals them via fan triangulation (no new verts added).
# Bounds exclude DESIGNED-open loops: neck (Z=60-114), wrist (X up to +-28),
# and ankle (Z=11-63) that other mesh shapes attach into.
PUBIC_HOLE_Z_MIN = 63.0
PUBIC_HOLE_Z_MAX = 72.0
PUBIC_HOLE_X_BOUND = 6.0


def _close_pubic_holes(
        verts: np.ndarray, tris: np.ndarray, normals: np.ndarray,
) -> "tuple[np.ndarray, int]":
    """Triangulate the UBE pubic-region boundary loops with fan tris.

    Returns (new_tris, n_loops_closed). Vert array is UNCHANGED — we
    only append triangles using existing vert indices. Skin weights
    inherit through those vert indices automatically (no skin changes
    needed). Per-vert normals stay as-is; new fill tris share the
    boundary verts' outward normals, giving continuous shading with
    the surrounding mesh.

    Winding for each fan: detected per-loop by comparing the fan's
    initial face-normal to the average source vert-normal at the
    triangle's corners — if the dot is negative, the winding is
    flipped so all fill tris face outward.

    Loops in the pubic bbox that are non-manifold (degree-1 endpoints
    or degree-4 junctions) are SKIPPED, not force-closed — fan-
    triangulating an open chain produces overlapping faces and would
    look worse than the hole.
    """
    from collections import Counter, defaultdict

    if normals is None or len(verts) == 0:
        return tris, 0

    # Build boundary-edge set.
    edge_count: Counter = Counter()
    for tri in tris:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            edge_count[(int(min(a, b)), int(max(a, b)))] += 1
    boundary_edges = {e for e, c in edge_count.items() if c == 1}
    if not boundary_edges:
        return tris, 0

    # Build boundary-vert adjacency.
    adj: "dict[int, set[int]]" = defaultdict(set)
    for a, b in boundary_edges:
        adj[a].add(b)
        adj[b].add(a)

    # Connected components.
    visited: set[int] = set()
    components: list[set[int]] = []
    for start in list(adj.keys()):
        if start in visited:
            continue
        stack = [start]
        comp: set[int] = set()
        while stack:
            v = stack.pop()
            if v in visited:
                continue
            visited.add(v)
            comp.add(v)
            for nb in adj[v]:
                if nb not in visited:
                    stack.append(nb)
        components.append(comp)

    new_tris_chunks: list[np.ndarray] = []
    n_closed = 0
    for comp in components:
        # Spatial filter: must lie entirely in the pubic bbox.
        cv = verts[list(comp)]
        z_min, z_max = float(cv[:, 2].min()), float(cv[:, 2].max())
        x_min, x_max = float(cv[:, 0].min()), float(cv[:, 0].max())
        if not (PUBIC_HOLE_Z_MIN <= z_min and z_max <= PUBIC_HOLE_Z_MAX):
            continue
        if max(abs(x_min), abs(x_max)) > PUBIC_HOLE_X_BOUND:
            continue
        # Topology filter: must be a clean closed loop (every vert has
        # exactly 2 boundary-edge neighbors WITHIN the component).
        degs = {v: len(adj[v] & comp) for v in comp}
        if any(d != 2 for d in degs.values()):
            continue
        if len(comp) < 3:
            continue
        # Walk the loop in order.
        start = min(comp)
        loop = [start]
        prev = -1
        cur = start
        while True:
            nb = [x for x in adj[cur] if x != prev and x in comp]
            if not nb:
                break
            nxt = nb[0]
            if nxt == start:
                break
            loop.append(nxt)
            prev, cur = cur, nxt
            if len(loop) > len(comp) + 1:
                break
        if len(loop) != len(comp):
            continue  # walk failed — non-simple loop
        # Fan triangulation from loop[0].
        fan = np.array(
            [[loop[0], loop[i], loop[i + 1]] for i in range(1, len(loop) - 1)],
            dtype=np.int64,
        )
        # Winding: ensure fan face normals align with source vert
        # normals (outward). If average dot is negative, flip.
        v0 = verts[fan[:, 0]]
        v1 = verts[fan[:, 1]]
        v2 = verts[fan[:, 2]]
        face_n = np.cross(v1 - v0, v2 - v0)
        face_n /= np.linalg.norm(face_n, axis=1, keepdims=True) + 1e-9
        src_n = (normals[fan[:, 0]] + normals[fan[:, 1]] + normals[fan[:, 2]]) / 3.0
        src_n /= np.linalg.norm(src_n, axis=1, keepdims=True) + 1e-9
        if (face_n * src_n).sum(axis=1).mean() < 0:
            fan = fan[:, [0, 2, 1]]
        new_tris_chunks.append(fan)
        n_closed += 1

    if not new_tris_chunks:
        return tris, 0
    appended = np.vstack(new_tris_chunks).astype(tris.dtype)
    return np.vstack([tris, appended]), n_closed


def _physics_chain_nowarp_blend(src_shape, source_verts, warped_verts):
    """Keep SELF-SIMULATED cloth (custom physics-chain bones: skirt/belt/cape)
    at its SOURCE position instead of the UBE-warped position. #177

    Why: an armour's own HDT-SMP cloth is simulated from its custom chain bones,
    which we recreate at their SOURCE bind (see _precreate_custom_bone_chains).
    The body-delta warp moves the *cloth verts* ~0.5u onto the UBE body but the
    chain bones stay at source -> the cloth is now offset from its own bones ->
    SMP rest pose is wrong -> the chain collapses / falls through the floor in
    game. Proven decisively: the ORIGINAL (un-warped) long-robe skirt has
    working physics on the UBE actor, the warped conversion does not (houseCARL
    WorldModel swap, 2026-06-04).

    The CHEST/body works because it is driven by the ACTOR's body bones (breast
    soft-body etc.), not the garment's own SMP -- so those verts are LEFT warped
    for the UBE fit. We blend per-vertex by the fraction of skin weight on custom
    (non-skeleton) chain bones: full chain weight -> stay at source; full
    skeleton weight -> stay warped; partial -> proportional.
    """
    try:
        bw = src_shape.bone_weights
    except Exception:
        return warped_verts
    if not bw:
        return warped_verts
    sv = np.asarray(source_verts, dtype=np.float64)
    wv = np.asarray(warped_verts, dtype=np.float64)
    n = len(sv)
    if n == 0 or len(wv) != n:
        return warped_verts
    frac = np.zeros(n, dtype=np.float64)
    any_chain = False
    for bn, pairs in bw.items():
        # Soft-body bones (breast/butt/belly) are ACTOR-driven skeleton bones the
        # body rides -> treat as skeleton (warp them). Only true garment chain
        # bones (non-skeleton) are held at source.
        if _is_skeleton_bone(bn) or _is_soft_body_physics_bone(bn):
            continue
        any_chain = True
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for idx, wgt in pl:
            ii = int(idx)
            if 0 <= ii < n:
                frac[ii] += float(wgt)
    if not any_chain:
        return warped_verts
    frac = np.clip(frac, 0.0, 1.0)
    if not frac.any():
        return warped_verts
    return sv + (wv - sv) * (1.0 - frac)[:, None]


# Effect-shader glow ANIMATION (the controller chain). DEFAULT ON. (An earlier theory
# blamed the controller for the 'MaleTorsoGlow' CTD and made this static -- WRONG: the
# controller round-trips fine, and the real cause was thigh SCALE bones on the glow's skin,
# fixed by _drop_scale_bones_from_skin below. Animation is safe.) CBBE2UBE_NO_GLOW_ANIM=1
# forces a static glow (colour but no texture-scroll) as an escape hatch.
_EFFECT_GLOW_ANIM = (os.environ.get("CBBE2UBE_NO_GLOW_ANIM", "").strip().lower()
                     not in ("1", "true", "yes", "on"))

# Effect-shader glow overlays (Daedric red glow etc.) keep their SOURCE skin: the UBE
# reskin re-skins the decal to body bones it never had, and a skinned
# BSEffectShaderProperty CTDs on equip. So a glow shape ignores its override_skin and
# copies the source skin verbatim (minus scale bones). Default on;
# CBBE2UBE_EFFECT_RESKIN=1 reverts.  [DESIGN: Effect-shader glow overlays]
EFFECT_SHADER_SOURCE_SKIN = (
    os.environ.get("CBBE2UBE_EFFECT_RESKIN", "").strip().lower()
    not in ("1", "true", "yes", "on"))


def _drop_scale_bones_from_skin(bone_names, xforms_map, weights_map):
    """Remove SCALE bones (Front/RearThigh/RearCalf/breast/butt/belly deform bones) from a
    skin, folding each vertex's scale-bone weight into its LARGEST kept (skeleton) bone so
    per-vertex total weight is preserved and no bone goes zero-weight. Used for effect-shader
    glow overlays, which CTD if skinned to scale bones. Returns the filtered
    (bone_names, xforms_map, weights_map).  [DESIGN: Effect-shader glow overlays]"""
    scale = [b for b in bone_names if _is_scale_bone(b)]
    keep = [b for b in bone_names if b not in scale]
    if not scale or not keep:
        return bone_names, xforms_map, weights_map
    vw: dict = {}   # vi -> {bone: weight} over KEPT bones
    for b in keep:
        for vi, w in weights_map.get(b, []):
            vw.setdefault(int(vi), {})[b] = vw.get(int(vi), {}).get(b, 0.0) + float(w)
    for b in scale:                        # fold each scale bone into the vert's biggest kept bone
        for vi, w in weights_map.get(b, []):
            d = vw.setdefault(int(vi), {})
            tgt = max(d, key=d.get) if d else keep[0]
            d[tgt] = d.get(tgt, 0.0) + float(w)
    new_w: dict = {}
    for vi, d in vw.items():
        for b, w in d.items():
            if w > 1e-6:
                new_w.setdefault(b, []).append((vi, w))
    new_w = {b: sorted(lst) for b, lst in new_w.items()}
    new_x = {b: xforms_map[b] for b in keep if b in xforms_map}
    return keep, new_x, new_w


def _transplant_effect_controller(src_shader, dst_nif, pyn):
    """Recreate a BSEffectShaderProperty's animation controller chain
    (controller -> interpolator -> NiFloatData + keys) in `dst_nif`, so a transplanted
    glow keeps its animation -- e.g. the Daedric glow's V-offset texture scroll.

    Returns the NEW controller's id (to store in the effect shader's controllerID), or
    NODEID_NONE when the source shader has no controller, the chain is an unsupported
    shape, or anything fails (a static glow -- still the right colour, just not moving).

    pynifly can't MODIFY shader/controller blocks after creation (setBlock of those
    buftypes is NYI), so the chain is built bottom-up and the controller's targetID is
    set to the PREDICTED effect-shader id. NifFile.save() remaps every block-id ref, so
    in-memory ids resolve on disk. The CALLER must create the effect shader IMMEDIATELY
    after this returns (with no intervening add_block), so its id == ctrl.id + 1."""
    none_id = pyn.NODEID_NONE
    try:
        if getattr(src_shader.properties, "controllerID", none_id) == none_id:
            return none_id
        src_ctrl = src_shader.controller
        if src_ctrl is None:
            return none_id
        src_interp = src_ctrl.interpolator
        if src_interp is None:
            return none_id
        src_data = src_shader.file.read_node(id=src_interp.properties.dataID)
        if src_data is None:
            return none_id
        src_keys = src_data.keys  # raises on an unsupported key type -> caught below

        def _clone(buf):
            return type(buf).from_buffer_copy(buf)

        # data + keyframes
        data = dst_nif.add_block(None, _clone(src_data.properties), parent=None)
        for k in src_keys:
            data.keys_add(k)
        # interpolator -> data
        interp_buf = _clone(src_interp.properties)
        interp_buf.dataID = data.id
        interp = dst_nif.add_block(None, interp_buf, parent=None)
        # controller -> interpolator; target = the effect shader the caller makes next.
        # Sequential ids: this controller = interp.id + 1, that shader = interp.id + 2.
        ctrl_buf = _clone(src_ctrl.properties)
        ctrl_buf.interpolatorID = interp.id
        ctrl_buf.nextControllerID = none_id   # only the first controller is transplanted
        ctrl_buf.targetID = interp.id + 2
        ctrl = dst_nif.add_block(None, ctrl_buf, parent=None)
        return ctrl.id
    except Exception:
        return none_id


def _copy_shape(src_shape, dst_nif, parent=None, override_verts=None,
                override_skin=None, skip_alpha=False, override_tris=None,
                preserve_authored_skin=False):
    """Deep-copy a single shape from src NIF to dst NIF via pynifly.

    Carries through: geometry (verts/tris/uvs/normals), shape properties
    (from BSTriShapeBuf), full shader (NiShaderBuf memcpy — preserves all
    shader flags so texture slots are read back correctly), textures,
    alpha property, skin instance (bones, skin-to-bone transforms,
    global-to-skin, per-bone weights, partitions).

    `override_verts`: if provided, use these verts instead of src_shape.verts.
    Used by the armor-fit pass to snap body-hugging verts to UBE body
    surface. Must have the same length as src_shape.verts.

    `override_tris`: if provided, use these tris instead of src_shape.tris.
    Used by mesh surgery (e.g. closing topology holes by appending fill
    triangles to the source tri list). Must reference only valid vert
    indices (i.e. all in [0, n_verts)).

    Known limitation: pynifly's API doesn't expose enough to faithfully
    copy every controller or extra-data block. For typical armor shapes
    that's fine.
    """
    # createShapeFromData requires tuple sequences; numpy rows trigger a
    # "expected c_float_Array_3 instance, got numpy.ndarray" error.
    if override_verts is not None:
        ov = np.asarray(override_verts)
        use_verts = [tuple(float(c) for c in row) for row in ov]
        # Recompute normals: source normals are stale after warp/inflate.
        # Pass source normals as sign reference so boundary verts don't flip.
        if src_shape.normals is not None and src_shape.tris is not None:
            src_n = np.asarray(src_shape.normals, dtype=np.float64)
            new_normals = _recompute_vertex_normals(
                ov, src_shape.tris, source_normals=src_n)
            use_normals = [tuple(float(c) for c in row) for row in new_normals]
        else:
            use_normals = None
    else:
        use_verts = list(src_shape.verts)
        use_normals = (list(src_shape.normals)
                       if src_shape.normals is not None else None)

    # With override_tris, keep source normals as-is: fill tris inherit existing
    # normals (outward-pointing, continuous shading). No recompute needed.
    if override_tris is not None:
        ot = np.asarray(override_tris, dtype=np.int64)
        use_tris = [tuple(int(c) for c in row) for row in ot]
    else:
        use_tris = list(src_shape.tris)

    # Bake non-identity geometry transform (scale/rotation) into verts+normals
    # so the output is an identity-transform skinned shape (skin-to-bone adjusted).
    _bake_T = _shape_bake_matrix(src_shape)
    # Pure-translation bake only on the copy path (override verts are already
    # body-positioned). Shapes with global_to_skin use _align_scale_bone_stbs_to_verts
    # instead; skip the vert-lift bake so both corrections don't double-apply.
    _bake_trans = (_shape_bake_translation(src_shape)
                   if (_bake_T is None and override_verts is None
                       and not src_shape.has_global_to_skin) else None)
    if _bake_T is not None:
        _vh = np.c_[np.asarray(use_verts, dtype=np.float64),
                    np.ones(len(use_verts))]
        use_verts = [tuple(float(c) for c in r)
                     for r in (_bake_T @ _vh.T).T[:, :3]]
        if use_normals is not None:
            _sc = float(np.linalg.norm(_bake_T[:3, 0])) or 1.0
            _Rn = _bake_T[:3, :3] / _sc
            _nb = np.asarray(use_normals, dtype=np.float64) @ _Rn.T
            _nb /= (np.linalg.norm(_nb, axis=1, keepdims=True) + 1e-12)
            use_normals = [tuple(float(c) for c in r) for r in _nb]
    elif _bake_trans is not None:
        # Lift verts by the engine-ignored translation. Normals unchanged.
        _tx, _ty, _tz = _bake_trans
        use_verts = [(v[0] + _tx, v[1] + _ty, v[2] + _tz) for v in use_verts]

    new_shape = dst_nif.createShapeFromData(
        src_shape.name,
        use_verts,
        use_tris,
        list(src_shape.uvs) if src_shape.uvs is not None else [],
        use_normals,
        props=src_shape.properties,
        parent=parent,
    )
    # Preserve per-vertex colors. createShapeFromData makes a COLORLESS shape, but a
    # shape may carry RGBA (baked AO/tint, or -- with SLSF2_Vertex_Colors -- an ALPHA
    # GRADIENT that does the actual work). The Daedric glow's fade is exactly this: RGB
    # white, vertex ALPHA 0..1. Drop it and the overlay renders SOLID (opaque) instead
    # of faded. Vert count/order is preserved (override_verts is 1:1), so colors map
    # straight across.
    try:
        _src_colors = src_shape.colors
        if _src_colors is not None and len(_src_colors) == len(use_verts):
            new_shape.set_colors(list(_src_colors))
    except Exception:
        pass
    # An authored OFFSET global_to_skin is COMPENSATED by the source NiAVObject
    # transform (g2s t=-120.3 + transform t=+120.3 = net identity); the engine uses
    # BOTH to place the bounding/cull sphere, and _install_skin PRESERVES the offset
    # g2s. Zeroing the transform (even on the fit path, where the fit verts are
    # lowered BACK to skin space) leaves g2s offset + transform identity -> the cull
    # bound lands ~g2s-offset below the geometry -> frustum-culled / invisible at
    # angles (the furexarot SMP elven cuirass). The engine IGNORES a skinned shape's
    # transform for RENDER, so keeping it can't fling the mesh -- it only restores the
    # matched pair the cull bound needs. So skip the reset for an offset-g2s skinned
    # shape; a SCALE/ROTATION bake (_bake_T) still wins (those must land in verts).
    _g2s = _shape_global_to_skin(src_shape) if src_shape.has_global_to_skin else None
    _g2s_offset = _g2s is not None and not _g2s_is_identity(_g2s)
    if (_bake_T is not None or _bake_trans is not None
            or (override_verts is not None and src_shape.bone_names
                and not _g2s_offset)):
        # The source props carried a (possibly non-identity) NiAVObject transform.
        # Force identity ONLY when the verts are already in final space WITHOUT it:
        # baked above, OR body-positioned via override_verts (the fit path) -- else a
        # leftover scale/translation flings the mesh off-body (project_scale_bake_
        # vigilant). GATED on src_shape.bone_names: a NON-skinned transform IS
        # engine-honored, so it must NOT be zeroed on the override path. GATED on
        # not _g2s_offset: an offset-g2s shape's transform is the cull-bound match
        # (above) -- keep it.
        try:
            _idt = _pynifly().TransformBuf()
            _idt.set_identity()
            new_shape.transform = _idt
        except Exception:
            pass

    # Shader: copy value fields only (flags, glossiness, etc.). Don't memcpy the
    # whole struct — block-ID fields point at source-NIF blocks and would break dst.
    _SHADER_VALUE_FIELDS = (
        "Shader_Flags_1", "Shader_Flags_2", "Shader_Type",
        "Alpha", "Emissive_Mult", "Glossiness", "Spec_Str",
        "Env_Map_Scale", "Refraction_Str", "Soft_Lighting",
        "Rim_Light_Power", "Skin_Tint_Alpha",
        "UV_Offset_U", "UV_Offset_V", "UV_Scale_U", "UV_Scale_V",
        "shaderFlags", "bslspShaderType",
        "textureClampMode", "clamp_mode_s", "clamp_mode_t",
        "subsurfaceRolloff", "rimlightPower2", "backlightPower",
        "grayscaleToPaletteScale", "fresnelPower",
        "wetnessSpecScale", "wetnessSpecPower", "wetnessMinVar",
        "wetnessEnvmapScale", "wetnessFresnelPower", "wetnessMetalness",
        "lumEmittance", "exposureOffset",
        "finalExposureMin", "finalExposureMax",
        "envMapScale", "parallaxEnvmapStrength",
    )
    _is_effect_shape = False
    try:
        src_shader = src_shape.shader
        if src_shader is not None and src_shader.properties is not None:
            src_props = src_shader.properties
            _pyn = _pynifly()
            _effect_buftype = getattr(
                _pyn.PynBufferTypes, "BSEffectShaderPropertyBufType", None)
            if (_effect_buftype is not None
                    and getattr(src_props, "bufType", None) == _effect_buftype):
                _is_effect_shape = True   # -> skin to skeleton bones only (see below)
                # Source uses a BSEffectShaderProperty -- an additive glow/decal shader
                # (e.g. Daedric's red glow: emissive + greyscale-to-colour gradient).
                # createShapeFromData only ever makes a BSLightingShaderProperty, which
                # cannot represent it: the emissive is zeroed and the greyscale texture
                # dropped, so it renders the bare (pale) overlay texture = WHITE, no
                # glow. pynifly can't MODIFY a lighting shader into an effect shader
                # (setBlock of the effect buftype is NYI) but it CAN CREATE one via
                # add_block -- so transplant the source effect buffer onto the new
                # shape and re-point the shape's shader reference at it.
                try:
                    eff_buf = type(src_props).from_buffer_copy(src_props)  # clone (don't mutate src)
                except Exception:
                    eff_buf = src_props
                # Transplant the glow's animation controller chain (if any) so it keeps
                # MOVING (e.g. texture scroll). Built BEFORE the shader so it can reference
                # it at creation (those block buffers can't be modified afterward). Returns
                # the new controller id, or NODEID_NONE for a static glow. Static by default:
                # the controller chain doesn't survive the HDT inject's reload -> CTD.
                # CBBE2UBE_GLOW_ANIM=1 restores animation.  [DESIGN: Effect-shader glow overlays]
                try:
                    if _EFFECT_GLOW_ANIM:
                        eff_buf.controllerID = _transplant_effect_controller(
                            src_shader, dst_nif, _pyn)
                    else:
                        eff_buf.controllerID = _pyn.NODEID_NONE
                except Exception:
                    try:
                        eff_buf.controllerID = _pyn.NODEID_NONE
                    except Exception:
                        pass
                try:
                    eff = dst_nif.add_block(
                        src_shader.name or "", eff_buf, parent=new_shape)
                    # Re-point the shape's shader ref (mirrors pynifly's own
                    # save_shader_attributes: set the id, let NifFile.save() persist
                    # it -- do NOT write_properties() the freshly-created shape, which
                    # flushes a stale nameID and blanks the shape's name).
                    new_shape.properties.shaderPropertyID = eff.id
                    new_shape._shader = None  # drop cache -> textures bind the new block
                except Exception:
                    pass
            else:
                new_shader = new_shape.shader
                dst_props = new_shader.properties  # access lazy-loads
                for fld in _SHADER_VALUE_FIELDS:
                    if hasattr(src_props, fld) and hasattr(dst_props, fld):
                        try:
                            setattr(dst_props, fld, getattr(src_props, fld))
                        except (TypeError, AttributeError):
                            pass

                # NOTE: We previously attempted to force-clear render flags
                # on textureless (collision) shapes here, but pynifly's
                # NifFile.save() unconditionally sets Shader_Flags_1 bit 1
                # (Skinned) on any skinned shape — overriding our write.
                # The collision-proxy fix now happens upstream in
                # convert_nif_phase2 by SKIPPING those shapes entirely.

                # Flush the mutated buf back to the file's shader block.
                try:
                    new_shader.write_properties()
                except Exception:
                    pass
    except Exception:
        pass

    # Textures: must be done after shader copy (set_texture mutates dst shader buf).
    for slot_name, tex_path in (src_shape.textures or {}).items():
        if tex_path:
            new_shape.set_texture(slot_name, tex_path)

    # Skin instance. add_bone resets all bone info on each call, so:
    # add all bones first, then set transforms and weights (two-pass).
    # override_skin (if provided) installs blended weights in a single pass;
    # calling skin()+add_bone after _copy_shape corrupts pynifly's mapping.
    # Effect-shader glow overlays skip the body reskin: they keep their SOURCE
    # skin (the proven-good vanilla skinning) instead of the override_skin's body
    # bones, which CTD on equip. Falls through to the verbatim source-skin path
    # below (which also drops scale bones). See EFFECT_SHADER_SOURCE_SKIN.
    _use_override = (override_skin is not None
                     and not (_is_effect_shape and EFFECT_SHADER_SOURCE_SKIN))
    if _use_override:
        bone_names = override_skin["bones"]
        xforms_map = override_skin["xforms"]
        weights_map = override_skin["weights"]
        # Cap to per-partition GPU bone limit to prevent equip CTD.
        bone_names, xforms_map, weights_map = _cap_skin_bone_count(
            bone_names, xforms_map, weights_map)
        if _is_effect_shape:   # scale bones on an effect-shader overlay CTD the render
            bone_names, xforms_map, weights_map = _drop_scale_bones_from_skin(
                bone_names, xforms_map, weights_map)
        _install_skin(new_shape, dst_nif, src_shape, bone_names,
                      xforms_map, weights_map, use_verts, _bake_T,
                      preserve_authored_skin=preserve_authored_skin)
    elif src_shape.bone_names:
        # Source shapes can exceed the GPU bone cap (dense skirts ship 79-81).
        # Keep all bones and let _split_oversize_partition split into partitions
        # instead of dropping bones (which evicted morph bones). Exception:
        # body-inject/VirtualGround shapes can't be split; they get the in-build
        # trim as backstop.
        _vb_bones = list(src_shape.bone_names)
        _vb_x: dict = {}
        for bn in _vb_bones:
            try:
                xf = src_shape.get_shape_skin_to_bone(bn)
                if xf is not None:
                    _vb_x[bn] = xf
            except Exception:
                pass
        _vb_w: dict = {}
        for bn, pairs in (src_shape.bone_weights or {}).items():
            _vb_w[bn] = [(int(i), float(w)) for i, w in
                         (pairs.tolist() if hasattr(pairs, "tolist") else pairs)]
        if (src_shape.name in UBE_BODY_INJECT_NAMES
                or src_shape.name == "VirtualGround"):
            # not split post-save -> keep the in-build cap (drop-to-fit) backstop
            bone_names, xforms_map, weights_map = _cap_skin_bone_count(
                _vb_bones, _vb_x, _vb_w)
        else:
            # split-eligible -> keep ALL source bones; post-save split makes it
            # CTD-safe without dropping any (a dense-dress mod: 80 bones -> 78+9).
            bone_names, xforms_map, weights_map = _vb_bones, _vb_x, _vb_w
        if _is_effect_shape:   # scale bones on an effect-shader overlay CTD the render
            bone_names, xforms_map, weights_map = _drop_scale_bones_from_skin(
                bone_names, xforms_map, weights_map)
        _install_skin(new_shape, dst_nif, src_shape, bone_names,
                      xforms_map, weights_map, use_verts, _bake_T,
                      preserve_authored_skin=preserve_authored_skin)

    # Alpha: set has_alpha_property=True first (creates dst NiAlphaProperty),
    # then copy flags/threshold. Don't memcpy the whole buf (contains source
    # block IDs). skip_alpha=True is for cloth shapes where NioOverride gates
    # morph application on absence of NiAlphaProperty.
    # Fault-isolate the alpha-property READ: on a source with a broken alpha
    # block reference, pynifly's has_alpha_property getter itself raises
    # ("getNiAlphaProperty called on invalid node"), and an unguarded read here
    # failed the whole _copy_shape -> shape DROPPED -> invisible piece in-game
    # (Steelheart gauntlets class). Copy WITHOUT alpha instead: visible geometry
    # beats a lost alpha flag on a mesh whose alpha ref was broken anyway.
    try:
        _src_has_alpha = bool(src_shape.has_alpha_property)
    except Exception:
        _src_has_alpha = False
    if _src_has_alpha and not skip_alpha:
        try:
            new_shape.has_alpha_property = True  # creates dst _alpha
            src_ap = src_shape.alpha_property
            dst_ap = new_shape.alpha_property
            if src_ap is not None and dst_ap is not None:
                for fld in ("flags", "threshold"):
                    sv = getattr(src_ap.properties, fld, None)
                    if sv is not None and hasattr(dst_ap.properties, fld):
                        try:
                            setattr(dst_ap.properties, fld, sv)
                        except (TypeError, AttributeError):
                            pass
            new_shape.save_alpha_property()
        except Exception:
            # Fallback to original behavior (no-op for missing _alpha,
            # but at least it doesn't crash).
            try:
                new_shape.save_alpha_property()
            except Exception:
                pass

    # Don't call save_shader_attributes() — it adds a new orphaned block with
    # dangling IDs. Mutations to the existing buf round-trip via NifFile.save().

    # Preserve source skin-instance type: createShapeFromData always makes
    # BSDismemberSkinInstance, but HDT-SMP collision proxies and some cloth
    # ship plain NiSkinInstance. Demote back if source was NiSkinInstance
    # (keeps bones/weights/STB + NiSkinPartition; drops dismember partition).
    try:
        if (getattr(src_shape, "skin_instance_name", None) == "NiSkinInstance"
                and new_shape.has_skin_instance):
            new_shape.demote_skin_instance()
    except Exception:
        pass

    return new_shape


# HDT-SMP XML auto-detection: keyword categories. When multiple XMLs
# exist in the source mod's mesh directory, match XML keyword to NIF
# stem keyword to pick the right physics config for the body region.
HDT_XML_KEYWORDS = {
    # XML name fragment -> NIF stem fragments that should use it
    "boob":   ("top", "chest", "torso", "shirt", "bra", "bodice", "corset"),
    "breast": ("top", "chest", "torso", "shirt", "bra", "bodice", "corset"),
    "tasset": ("waist", "skirt", "tabard", "pelvis", "panties", "loincloth"),
    "skirt":  ("waist", "skirt", "tabard", "pelvis"),
    "tail":   ("tail", "cape"),
}


_HDT_XML_INDEX_CACHE: "dict[str, list]" = {}


def _mod_xml_index(source_mod_root: Path) -> list:
    """Cached recursive listing of every ``*.xml`` under a source mod's
    ``meshes/`` subtree. The XML set is static for the duration of a
    conversion run, so this rglob — which otherwise re-runs for EVERY
    armor NIF that lives in the same mod — is memoized per mod root.
    Returns the same list (same order) the old inline rglob produced."""
    key = str(source_mod_root)
    cached = _HDT_XML_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    xmls: list = []
    for marker in ("meshes", "Meshes"):
        mesh_dir = source_mod_root / marker
        if mesh_dir.is_dir():
            xmls = list(mesh_dir.rglob("*.xml"))
            break
    _HDT_XML_INDEX_CACHE[key] = xmls
    return xmls


def _find_hdt_xml_for_armor(armor_nif_path: Path,
                            source_mod_root: Path | None = None) -> str | None:
    """Find the HDT-SMP XML config in the source mod that matches the
    armor NIF's body region.

    Returns the Skyrim-relative path (e.g. `Meshes\\<mod>\\<armor>\\
    a hand-authored physics XML`) suitable for the `HDT Skinned Mesh Physics Object`
    extra-data string. Or None if no suitable XML found.
    """
    if source_mod_root is None:
        # Walk up from the NIF's path to find the mod root (the dir that
        # contains the `meshes` folder)
        for parent in armor_nif_path.parents:
            if any(p.name.lower() == "meshes" for p in parent.iterdir() if p.is_dir()):
                source_mod_root = parent
                break
    if source_mod_root is None or not source_mod_root.is_dir():
        return None

    # Find all XML files in the source mod's Meshes/ subtree (cached per
    # mod root — the same rglob otherwise re-runs for every armor NIF in
    # the mod).
    xmls = _mod_xml_index(source_mod_root)
    if not xmls:
        return None

    nif_stem = armor_nif_path.stem.lower()
    # Strip _0/_1 weight suffix
    for s in ("_0", "_1"):
        if nif_stem.endswith(s):
            nif_stem = nif_stem[:-len(s)]
            break

    best_xml = None

    # HIGHEST confidence: an XML whose stem EXACTLY matches the NIF stem is the
    # armour's own config (e.g. `ThorHair_1.nif` <-> `ThorHair.xml`), no matter
    # whether the name contains a region keyword. Prefer one in the SAME folder;
    # else a UNIQUE same-stem match anywhere in the mod. Catches authored configs
    # the small keyword map misses -- without it they fall back to a GENERATED
    # XML (worse physics than the hand-authored one). #xml-stem-match
    exact = [x for x in xmls if x.stem.lower() == nif_stem]
    same_dir_exact = [x for x in exact if x.parent == armor_nif_path.parent]
    if same_dir_exact:
        best_xml = same_dir_exact[0]
    elif len(exact) == 1:
        best_xml = exact[0]

    # Else score each XML by a NIF↔XML KEYWORD match — directory proximity alone
    # isn't enough (boots shouldn't pick up the breast physics XML just because
    # they share a folder).
    if best_xml is None:
        best_score = 0
        for xml in xmls:
            xml_stem = xml.stem.lower()
            keyword_score = 0
            for xml_kw, nif_kws in HDT_XML_KEYWORDS.items():
                if xml_kw in xml_stem:
                    for nif_kw in nif_kws:
                        if nif_kw in nif_stem:
                            keyword_score = 10
                            break
                    if keyword_score:
                        break
            if keyword_score == 0:
                continue  # XML keyword doesn't match this NIF's region — skip
            dir_bonus = 5 if xml.parent == armor_nif_path.parent else 0
            score = keyword_score + dir_bonus
            if score > best_score:
                best_score = score
                best_xml = xml
    if best_xml is None:
        return None

    # Build Skyrim-relative path (relative to Data/). Capitalize "Meshes"
    # to match the hand-authored convention.
    parts = list(best_xml.parts)
    for i, p in enumerate(parts):
        if p.lower() == "meshes":
            parts[i] = "Meshes"
            return "\\".join(parts[i:])
    return None


def _source_hdt_needs_missing_chain_bones(src_path, dst_bone_names) -> bool:
    """True if the source armor's HDT-SMP XML drives physics-CHAIN bones
    (physics-chain bones (prefix_NN), Skirt N_NN, etc.) that are NOT present in the
    converted output's bone set (`dst_bone_names`).

    Why this matters: those chain bones are injected at BodySlide-BUILD
    time and live only in the built mesh / a separate output mod — the
    source mod's raw meshes (which the converter reads) don't carry them,
    and our reskin doesn't recreate them. So the source XML's
    `<bone name="a physics-chain bone ...">` + per-vertex-shape thresholds reference
    bones our NIF lacks. Preserving that XML reference points HDT-SMP at
    absent bones -> the cloth has no working physics (dead tabards/skirts).

    When this returns True the caller regenerates a fresh per-vertex
    soft-body XML (`_generate_hdt_xml_for_dst`) anchored to the STANDARD
    body bones the converted mesh actually has — which makes hanging
    cloth (tabards/skirts) simulate + collide WITHOUT any BodySlide-
    injected chain rig. Returns False (keep the source XML) when the XML
    has no chain bones, or when our output still has all the chain bones
    it needs (rare: chains that survived conversion), or on any error."""
    if CHAIN_TO_SOFTBODY:
        # Soft-body mode: never regenerate/keep a chain XML on this basis;
        # `_generate_hdt_xml_for_dst` emits the collision-only soft-body XML.
        return False
    try:
        src_path = Path(src_path)  # tolerate str callers
        # Resolve the source armor's HDT XML on disk — same two-step the
        # softbody detector uses: the NIF's OWN extra-data first, then the
        # dir-scan fallback (the raw source mesh often has no extra-data,
        # but a *.xml sits beside it in the mod).
        xml_disk = _read_source_hdt_xml_disk(src_path)
        if xml_disk is None:
            rel = _find_hdt_xml_for_armor(src_path)
            if rel:
                norm = rel.replace("\\", "/").lstrip("/")
                for parent in [src_path, *src_path.parents]:
                    if parent.name.lower() == "meshes":
                        cand = parent.parent / norm
                        if cand.is_file():
                            xml_disk = cand
                        break
        if xml_disk is None or not xml_disk.is_file():
            return False
        txt = xml_disk.read_text(errors="ignore")
        xml_bones = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
        if not xml_bones:
            return False
        from .hdt_xml_gen import detect_physics_chains
        chain_bones = {b for ch in detect_physics_chains(xml_bones)
                       for b in ch.bones}
        if not chain_bones:
            return False  # XML uses only standard bones we already have
        return bool(chain_bones - set(dst_bone_names or ()))
    except Exception:
        return False


def _is_unconstrained_collision_pair(
    body_collision_shape_name: "str | None", chains: "list | None"
) -> bool:
    """True for the FSMP equip-CTD pattern: a per-vertex cloth paired
    with a per-triangle body collider but with NO simulated chain (no
    <generic-constraint>). Such a cloth has zero spring forces, so FSMP
    lets it diverge to infinity and the collision SIMD then reads out of
    bounds -> ACCESS_VIOLATION on equip (confirmed in-game on several
    pieces). Dropping just the collider leaves the same unconstrained
    cloth, which explodes instead of crashing -- so the caller emits NO
    physics XML at all and the piece stays kinematic with the converter's
    baked geometric clearance.

    Mirrors `scripts/disable_unconstrained_smp.is_broken_collision_pair`
    but at generation time: the per-vertex cloth side is implied (this is
    only consulted when cloth carriers exist), so the test reduces to
    "has a body collider AND no chain". Cloth-only NIFs (no collider) and
    constrained chains (chains present) are stable and are NOT this case.
    """
    return body_collision_shape_name is not None and not chains


def _generate_hdt_xml_for_dst(dst_path: "Path") -> "str | None":
    """Generate a fresh HDT-SMP cloth-collision XML for the destination
    NIF, write it alongside the NIF, and return the Skyrim-relative
    path string (suitable for the `HDT Skinned Mesh Physics Object`
    root extra-data).

    Returns None when there's genuinely nothing to simulate:
      * NIF has no cloth shapes the converter recognizes (use the
        same `_pick_bodytri_carriers` filter as the BODYTRI machinery
        — that's our agreed definition of "cloth that should track
        the body")
      * the result would be an unconstrained collision pair (cloth +
        body collider, no chain) — see `_is_unconstrained_collision_pair`;
        we skip the XML entirely so the piece stays kinematic rather than
        shipping an FSMP equip-CTD.

    A NIF with cloth but NO body collision proxy (VirtualBody /
    BaseShape) — e.g. a slot-49 cloth-only skirt/tabard — still gets a
    valid XML. We pass `body_collision_shape_name=None` so the body
    per-triangle-shape block is omitted; the cloth per-vertex-shapes
    still declare `<can-collide-with-tag>body</can-collide-with-tag>`,
    so they collide with whatever provides the "body" tag in the
    actor's merged SMP system at runtime (the worn body's own physics
    XML). This is what makes hanging cloth on no-body NIFs actually
    simulate instead of keeping a now-dead source chain-bone reference.

    Behavior:
      * Loads the dst NIF, enumerates its cloth + body-proxy shapes
      * Builds the XML in-memory via `hdt_xml_gen.generate_armor_hdt_xml`
      * Writes the XML to `<dst_nif_dir>/<dst_nif_stem>.xml`,
        stripping the _0 / _1 weight suffix from the stem so both
        weight variants share one XML (matches BodySlide convention).
      * Computes the Skyrim-relative path (relative to Data/Meshes/)
        for use in the BODYTRI-style extra-data injection step.

    This is the Phase A generator wired in. It's the fallback for
    when `_find_hdt_xml_for_armor` couldn't locate a hand-authored
    XML in the source mod. We prefer hand-authored when available
    (they get authored physics chains for free); we only generate
    when we'd otherwise have no XML at all.
    """
    try:
        from . import hdt_xml_gen
    except Exception:
        return None

    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return None

    # Clear a STALE auto-generated XML from a PRIOR run before deciding whether
    # to (re)generate. Reconverts write into the same output dir, so a leftover
    # `<stem>.xml` we generated last time survives even when this run decides the
    # shape should get NO physics (e.g. the rigid-torso gate) -- and then
    # `_finalize_hdt_physics` re-points the fresh NIF at that stale file, so the
    # armour still soft-bodies. Delete ONLY our own auto-generated file (marker);
    # an authored source XML has no marker and is left alone (finalize re-copies
    # it from source if present). #stale-gen-xml
    try:
        _stale_stem = dst_path.stem
        for _suf in ("_0", "_1"):
            if _stale_stem.endswith(_suf):
                _stale_stem = _stale_stem[:-len(_suf)]
                break
        _stale_xml = dst_path.parent / f"{_stale_stem}.xml"
        if _stale_xml.is_file():
            _head = _stale_xml.read_text(errors="ignore")[:400]
            if "Auto-generated by cbbe-to-ube" in _head:
                _stale_xml.unlink()
    except Exception:
        pass

    # Reuse the BODYTRI carrier picker as the "cloth shape" classifier:
    # every textured, non-placeholder, non-rigid-prop shape qualifies.
    # exclude_body=True: the body (BaseShape/VirtualBody) is the COLLIDER
    # (per-triangle, below), NEVER a simulated per-vertex cloth — without this
    # a body-swap NIF picked its injected BaseShape as the cloth carrier, so
    # the body flopped as soft-body while the real cape got no physics at all.
    carriers = _pick_bodytri_carriers(nf, exclude_body=True)
    # NOTE: an earlier revision dropped carriers flagged `_shape_is_rigid_torso_
    # armor` here to stop a rigid cuirass flopping as generated cloth. That gate
    # was TOO BROAD -- it keys on upper-torso rigid-bone weight, which a cloak /
    # dress / long robe ALSO has (they attach at the shoulders), so it silently
    # stripped physics from ~335 legitimate hanging-cloth armours. Reverted; the
    # rigid-torso-vs-skirt split can't be done by a single weight fraction. See
    # [[project_softbody_rigid_gate]]. The helper is kept for future use.

    # Multi-layer cloth is deliberately kept on its SOURCE skin by every graft
    # pass (#layered-cloth-skin), so it carries NO body jiggle bones. Simulating
    # it as per-vertex SMP cloth therefore leaves it unconstrained: FSMP's soft
    # body diverges and its collision SIMD reads out of bounds -> access violation
    # while updating the shape (New Leather Cuirass_A/_B, crash 2026-07-09;
    # disabling the generated XML stopped the crash, confirmed in-game). This is
    # the same failure `_is_unconstrained_collision_pair` guards, but that gate
    # only fires when the NIF has a body collider -- the FIRST-PERSON NIF has
    # none, so its XML still shipped and FSMP applied those per-vertex shapes by
    # NAME into the actor's merged SMP system, reaching the third-person shapes.
    # Skin-strip and physics must go together: keep layered cloth kinematic.
    _layered = _layered_cloth_shape_names(nf.shapes)
    if _layered:
        carriers = [s for s in carriers if s.name not in _layered]
    if not carriers:
        return None

    body_shape_name = hdt_xml_gen.pick_body_collision_shape_name(
        s.name for s in nf.shapes)
    # body_shape_name may be None for a cloth-only NIF (slot-49 skirt /
    # tabard with no inline body proxy). That's fine: we still emit the
    # cloth per-vertex soft-body shapes, which collide with the actor's
    # body via the "body" tag at runtime (provided by the worn body's own
    # SMP XML). Previously this returned None here, leaving such NIFs with
    # NO generated XML — so a slot-49 cloth armor whose source XML drove
    # now-stripped physics-chain bones kept its dead reference and the
    # cloth never simulated. Generating the cloth-only XML fixes that.

    cloth_shapes_for_xml: list[tuple[str, list[str]]] = []
    all_bones_seen: set[str] = set()
    for sh in carriers:
        bones = list(sh.bone_names or [])
        cloth_shapes_for_xml.append((sh.name, bones))
        all_bones_seen.update(bones)

    # Escalation A: detect any physics-chain bones already in the NIF
    # skeleton (Skirt 1_NN, physics-chain bones (prefix_NN), etc.). If found, the
    # XML generator emits the corresponding bone-default + constraint-
    # group blocks so the chain actually swings in HDT-SMP. We don't
    # need to add new bones — they're already in the source mod's
    # skeleton, just unused without this XML.
    chains = hdt_xml_gen.detect_physics_chains(all_bones_seen)

    # Don't emit the FSMP equip-CTD pattern: an unconstrained collision
    # pair (cloth + per-triangle body collider, no simulated chain). The
    # unconstrained soft body diverges and the collision SIMD reads out of
    # bounds -> crash on equip. Skip the XML entirely (the piece stays
    # kinematic with the baked geometric clearance). Cloth-only NIFs (no
    # body collider) and constrained chains still emit normally.
    if _is_unconstrained_collision_pair(body_shape_name, chains):
        return None

    # Where to write. Strip weight suffix from stem so _0.nif and
    # _1.nif share one XML (file is per-armor, not per-weight).
    stem = dst_path.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]; break
    xml_disk_path = dst_path.parent / f"{stem}.xml"

    try:
        hdt_xml_gen.write_armor_hdt_xml(
            xml_disk_path,
            cloth_shapes_for_xml,
            body_collision_shape_name=body_shape_name,
            chains=chains,
        )
    except Exception:
        return None

    # Compute Skyrim-relative path (relative to Data/, with leading
    # "Meshes\..."). Walk the path parts to find the "meshes" segment.
    parts = list(xml_disk_path.parts)
    for i, p in enumerate(parts):
        if p.lower() == "meshes":
            parts[i] = "Meshes"
            return "\\".join(parts[i:])
    return None


def _resolve_data_rel_in_vfs(rel: str, src_nif_path: Path) -> "Path | None":
    """Resolve a Data-relative path string (e.g. an armor NIF's authored
    "Meshes\\...\\Foo.xml" physics-XML reference) to a real file on disk.

    Tries the source NIF's OWN mod root first (the dir holding 'meshes'), then
    falls back to the full MO2 load order. The fallback matters because the
    AUTHORED physics XML routinely ships in a DIFFERENT mod than the source
    NIF: BodySlide writes the built mesh into its own output mod (e.g.
    "...- Bodyslide Output - 3BA") while the hand-authored chain XML stays in
    the original armor mod (e.g. an armor mod's "hdt SMP" folder). Resolving only against
    the NIF's own mod root misses it -> the converter falls back to a GENERIC
    XML that doesn't drive the custom chain (cloth "pulls to origin", cloak
    clip, skirt physics broken). Returns the Path or None.
    """
    if not rel:
        return None
    norm = rel.replace("\\", "/").lstrip("/")
    # 1) Local: the source NIF's own mod root (dir that contains 'meshes').
    for parent in [src_nif_path, *src_nif_path.parents]:
        if parent.name.lower() == "meshes":
            cand = parent.parent / norm
            if cand.is_file():
                return cand
            break  # meshes root found, XML not local -> fall through to VFS
    # 2) VFS: scan the whole load order for whichever mod ships the file.
    try:
        mroot = _paths.mods_root()
    except Exception:
        mroot = None
    # Fallback: derive the mods root from the source NIF's OWN location (the dir
    # ABOVE the mod folder that holds 'meshes') when the global mods_root isn't
    # set in this process. Without it, VFS resolution silently returns None ->
    # the source HDT XML isn't read -> per-triangle colliders go undetected ->
    # their partitions get collapsed -> FSMP equip CTD. Belt-and-suspenders so
    # the collider skip never depends on an env var being present.
    if mroot is None:
        for parent in src_nif_path.parents:
            if parent.name.lower() == "meshes":
                mroot = parent.parent.parent  # meshes -> mod dir -> mods root
                break
    try:
        if mroot is not None and mroot.is_dir():
            for mod in sorted(d for d in mroot.iterdir() if d.is_dir()):
                cand = mod / norm
                if cand.is_file():
                    return cand
    except OSError:
        pass
    return None


def _read_source_hdt_xml_disk(src_nif_path: Path, nif=None) -> "Path | None":
    """Resolve the source armor NIF's OWN `HDT Skinned Mesh Physics Object`
    extra-data string to a file on disk.

    This is the authoritative armor->XML link (the mod author wrote it),
    far more reliable than keyword-matching filenames — it correctly maps
    e.g. <Armor>_Female_Body_0.nif -> Meshes\\<ModFolder>\\Armor\\<Armor>\\<Armor>_Body.xml
    where the stems don't match. Resolves through the full VFS so an XML that
    ships in a different mod than the (BodySlide-output) NIF is still found.
    Returns the Path or None.

    Pass `nif` to reuse an ALREADY-LOADED NifFile (the conform does this) so we
    don't re-parse the same NIF from disk just to read its extra-data.
    """
    try:
        snf = nif if nif is not None else _pynifly().NifFile(filepath=str(src_nif_path))
        rel = None
        for ed in snf.rootNode.extra_data():
            if (getattr(ed, "name", None) == "HDT Skinned Mesh Physics Object"
                    and getattr(ed, "string_data", None)):
                rel = ed.string_data
                break
        if not rel:
            return None
        return _resolve_data_rel_in_vfs(rel, src_nif_path)
    except Exception:
        return None


_SKELETON_BONES_CACHE: "set[str] | None" = None


def _actor_skeleton_bone_names() -> "set[str]":
    """Lowercased node names of the actor's animated skeleton (XPMSE/vanilla).

    FSMP / HDT-SMP resolve every bone an XML references against the ACTOR'S
    skeleton at runtime, NOT against the armor NIF's own bone list. So a
    physics XML can legitimately reference standard skeleton bones (NPC L
    Forearm, NPC Neck, NPC L Hand, breast/butt bones, ...) that the armour
    mesh isn't skinned to — they still resolve. We load the skeleton once so
    the FSMP-hardening pass only prunes bones that exist in NEITHER the NIF
    NOR the skeleton (i.e. genuinely unresolvable), instead of stripping
    valid skeleton-bone collisions. Empty set if no skeleton is found (then
    the caller skips bone pruning to stay safe)."""
    global _SKELETON_BONES_CACHE
    if _SKELETON_BONES_CACHE is not None:
        return _SKELETON_BONES_CACHE
    names: "set[str]" = set()
    for pat in (
        "meshes/actors/character/character assets female/skeleton_female.nif",
        "meshes/actors/character/character assets/skeleton_female.nif",
        "meshes/actors/character/character assets/skeleton.nif",
    ):
        try:
            p = _glob_first_in_mods(pat)
        except Exception:
            p = None
        if not p:
            continue
        try:
            nf = _pynifly().NifFile(filepath=str(p))
            names |= {n.lower() for n in nf.nodes.keys()}
        except Exception:
            continue
        if names:
            break
    _SKELETON_BONES_CACHE = names
    return names


# --- Authored-physics param hardening (gated) -------------------------------
# Clamps fragile chain-rig stability params into the band that robust rigs
# occupy, WITHOUT touching chain structure (cloth keeps swinging). Toggle:
# CBBE2UBE_HARDEN_PHYSICS=1 (default OFF).
HARDEN_AUTHORED_PHYSICS = (
    os.environ.get("CBBE2UBE_HARDEN_PHYSICS", "").strip().lower()
    in ("1", "true", "yes", "on")
)
# Zeroes dynamic chain masses so bones become static/kinematic — cloth holds
# its authored shape and follows Pelvis. Mitigates incomplete rigs (e.g.
# missing front-chain ring). Trade-off: no swing. XML-only, no mesh reskin.
# Apply with CBBE2UBE_STATIC_CHAINS=1 on the affected mod folder only.
STATIC_CHAINS = (
    os.environ.get("CBBE2UBE_STATIC_CHAINS", "").strip().lower()
    in ("1", "true", "yes", "on")
)
PHYS_INERTIA_FLOOR = 70.0     # floor for stable inertia (typical range 70-150)
PHYS_ANGDAMP_FLOOR = 0.9      # floor for angular damping (typical range 0.95-0.99)
PHYS_STIFFNESS_CAP = 50.0     # cap for spring stiffness (typical ~20; ebony was 200)
# Key differentiator: collapsing rigs allow linear link stretch (+/-0.1);
# working rigs use rigid links (0). Clamp to rigid to prevent skirt droop while
# leaving angular (sway) limits untouched. Only clamp SMALL stretches
# (<PHYS_LINEAR_STRETCH_CLAMP_BELOW); large intentional flows (cloaks at 15-25)
# are left alone.
PHYS_LINEAR_LIMIT_MAX = 0.0
PHYS_LINEAR_STRETCH_CLAMP_BELOW = 3.0


def _make_chains_static(xml_path: Path) -> None:
    """Gated (STATIC_CHAINS). Zero every dynamic chain mass in the XML so the
    chain bones become static/kinematic — they hold their bind pose following
    their NIF parent (Pelvis) and the cloth follows the body instead of
    free-falling. Mitigates incomplete rigs (e.g. the wolf's missing front
    chains). Trade-off: no swing. XML-only, idempotent. The per-armor XML's
    only <mass> values belong to chain bones (body collision shapes have none),
    so this never touches body physics."""
    if not STATIC_CHAINS:
        return
    try:
        t = Path(xml_path).read_text(errors="ignore")
    except Exception:
        return

    def _zero(m):
        try:
            return '<mass>0</mass>' if float(m.group(1)) > 0 else m.group(0)
        except Exception:
            return m.group(0)
    t2 = re.sub(r'<mass>\s*([0-9.]+)\s*</mass>', _zero, t)
    if t2 != t:
        try:
            atomic_write_bytes(xml_path, t2.encode("utf-8"))
        except Exception:
            pass


def _harden_physics_params(xml_path: Path) -> None:
    """Clamp a chain XML's rigid-body stability params toward the hand-
    authored corpus norms (inertia floor, angular-damping floor, stiffness
    cap). No-op unless HARDEN_AUTHORED_PHYSICS. Keeps the chain structure
    intact — only the per-body damping/inertia and per-constraint spring
    magnitudes are bounded, so the authored swing is preserved."""
    if not HARDEN_AUTHORED_PHYSICS:
        return
    try:
        t = Path(xml_path).read_text(errors="ignore")
    except Exception:
        return
    orig = t

    def _inertia(m):
        v = [max(float(m.group(i)), PHYS_INERTIA_FLOOR) for i in (1, 2, 3)]
        return '<inertia x="%g" y="%g" z="%g"' % (v[0], v[1], v[2])
    t = re.sub(r'<inertia x="([\-\d.]+)" y="([\-\d.]+)" z="([\-\d.]+)"',
               _inertia, t)

    def _angdamp(m):
        return '<angularDamping>%g</angularDamping>' % max(
            float(m.group(1)), PHYS_ANGDAMP_FLOOR)
    t = re.sub(r'<angularDamping>([\-\d.]+)</angularDamping>', _angdamp, t)

    def _stiff(m):
        v = [min(float(m.group(i)), PHYS_STIFFNESS_CAP) for i in (2, 3, 4)]
        return '<%sStiffness x="%g" y="%g" z="%g"' % (
            m.group(1), v[0], v[1], v[2])
    t = re.sub(r'<(linear|angular)Stiffness x="([\-\d.]+)" y="([\-\d.]+)"'
               r' z="([\-\d.]+)"', _stiff, t)

    # THE swing-preserving fix: clamp LINEAR link limits to ~rigid (the
    # measured difference between collapsing and working rigs). Only the
    # linear (translation/stretch) limits are touched — angular (sway) is
    # left exactly as authored.
    def _linlim(m):
        lim = PHYS_LINEAR_LIMIT_MAX
        out = []
        for i in (2, 3, 4):
            v = float(m.group(i))
            # Clamp only small accidental stretches to rigid; preserve large
            # intentionally-flowing limits.
            if abs(v) <= PHYS_LINEAR_STRETCH_CLAMP_BELOW:
                v = max(-lim, min(v, lim))
            out.append(v)
        return '<linear%sLimit x="%g" y="%g" z="%g"' % (
            m.group(1), out[0], out[1], out[2])
    t = re.sub(r'<linear(Lower|Upper)Limit x="([\-\d.]+)" y="([\-\d.]+)"'
               r' z="([\-\d.]+)"', _linlim, t)

    if t != orig:
        try:
            atomic_write_bytes(xml_path, t.encode("utf-8"))
        except Exception:
            pass


def _ensure_cloth_body_collider(xml_path: Path, nif) -> bool:
    """Give a simulated cloth the body collider it needs at the CHEST.

    Authored HDT-SMP XMLs sometimes give a per-vertex (simulated) cloth a body-
    collision tag (e.g. `ColBody`) but only supply a LOWER-body collider for it
    (a skirt-level `Greaves`/`Col*` proxy at the hips). On UBE the larger breast
    then pokes through the cloth with nothing at the chest to hold it out (the
    UBE nude body ships no HDT-SMP collider of its own). Register the body shape
    ALREADY PRESENT in the NIF (BaseShape) as a per-triangle collider carrying the
    tag the cloth collides with, so the simulated cloth rests on the whole UBE
    body (breast/belly/butt) -- exactly what the XML GENERATOR already emits for
    cloth-only NIFs (`pick_body_collision_shape_name`). No NEW geometry is added
    (BaseShape is already the visible body), so there is no double-body/equip-CTD
    risk.

    DEFAULT OFF (opt-in `CBBE2UBE_BODY_COLLIDER=1`): in-game this DESTABILISED the
    sim -- the Ancient Falmer body (head/chest/butt) collapsed to the floor. A
    full-body per-triangle collider paired with cloth that is ALSO skinned +
    weight-pinned to that same body diverges in FSMP. A chest-only KINEMATIC
    sub-mesh collider is the next approach; until proven in-game this stays off.
    Returns True if it patched. #breast-collider"""
    if os.environ.get("CBBE2UBE_BODY_COLLIDER", "").strip().lower() not in (
            "1", "true", "yes", "on"):
        return False
    try:
        from . import hdt_xml_gen
        text = Path(xml_path).read_text(errors="ignore")
    except Exception:
        return False
    shape_names = {s.name for s in nif.shapes}
    body_name = hdt_xml_gen.pick_body_collision_shape_name(shape_names)
    if not body_name or body_name not in shape_names:
        return False
    # Body tags some SIMULATED cloth wants to collide with (ignore 'ground').
    cloth_body_tags: set[str] = set()
    for m in re.finditer(r'<per-vertex-shape\b.*?</per-vertex-shape>', text,
                         re.S):
        for t in re.findall(r'<can-collide-with-tag>([^<]+)</can-collide-with-tag>',
                            m.group(0)):
            t = t.strip()
            if t and t.lower() != "ground":
                cloth_body_tags.add(t)
    if not cloth_body_tags:
        return False
    # Cloth tags (so the collider can name them back in can-collide-with).
    cloth_tags: set[str] = set()
    for m in re.finditer(r'<per-vertex-shape\b.*?</per-vertex-shape>', text,
                         re.S):
        for t in re.findall(r'<tag>([^<]+)</tag>', m.group(0)):
            if t.strip():
                cloth_tags.add(t.strip())
    # Already-registered per-triangle colliders: which body tags cover the chest?
    registered = set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', text))
    if body_name in registered:
        return False  # body already a collider
    chest_covered_tags: set[str] = set()
    for m in re.finditer(
            r'<per-triangle-shape\s+name="([^"]+)">(.*?)</per-triangle-shape>',
            text, re.S):
        cname, cblock = m.group(1), m.group(2)
        sh = next((s for s in nif.shapes if s.name == cname), None)
        if sh is None or len(sh.verts) == 0:
            continue
        zmax = max(v[2] for v in sh.verts)
        if zmax >= 90.0:  # this collider reaches the chest
            chest_covered_tags |= {t.strip()
                                   for t in re.findall(r'<tag>([^<]+)</tag>', cblock)}
    need = cloth_body_tags - chest_covered_tags
    if not need or "</system>" not in text:
        return False
    tag = sorted(need)[0]
    block = (f'\t<per-triangle-shape name="{body_name}">\n'
             f'\t\t<margin>0.1</margin>\n'
             f'\t\t<penetration>0.15</penetration>\n'
             f'\t\t<shared>private</shared>\n'
             f'\t\t<tag>{tag}</tag>\n'
             + ''.join(f'\t\t<can-collide-with-tag>{ct}</can-collide-with-tag>\n'
                       for ct in sorted(cloth_tags or {"Fabric"}))
             + '\t</per-triangle-shape>\n')
    try:
        Path(xml_path).write_text(
            text.replace("</system>", block + "</system>", 1))
        return True
    except Exception:
        return False


def _harden_hdt_xml_for_fsmp(xml_path: Path, nif) -> None:
    """FSMP-compatibility hardening of an output HDT-SMP XML. Prunes
    references the engine can't resolve so Faster HDT-SMP never loads
    a dangling shape/bone:

      * `<per-vertex-shape>` / `<per-triangle-shape name="Y">` whose Y is not
        a shape in the converted NIF (after collision-proxy re-import) -> drop
        the whole block (FSMP can't attach to a shape that isn't there).
      * `<weight-threshold bone="X">` whose X is in NEITHER the NIF's nodes
        NOR the actor skeleton -> drop the line. Standard skeleton bones are
        KEPT (FSMP resolves them against the actor skeleton even when the
        mesh isn't skinned to them) so we never strip valid collisions.

    Conservative: bone pruning runs ONLY when the actor skeleton loaded (so we
    can tell "truly missing" from "valid skeleton bone"); `<bone>` definitions
    and `<generic-constraint>` blocks are left untouched (removing one could
    break an authored chain — a NIF that lost its chain bones is regenerated
    upstream via _source_hdt_needs_missing_chain_bones instead)."""
    try:
        text = Path(xml_path).read_text(errors="ignore")
    except Exception:
        return
    nif_shapes = {s.name for s in nif.shapes}
    nif_bones: "set[str]" = set()
    for s in nif.shapes:
        nif_bones |= {b.lower() for b in (s.bone_names or [])}
    try:
        nif_bones |= {n.lower() for n in nif.nodes.keys()}
    except Exception:
        pass
    skel = _actor_skeleton_bone_names()
    resolvable_bones = nif_bones | skel
    prune_bones = bool(skel)  # only prune bones if we have a skeleton ref

    out: list[str] = []
    drop_block = False
    changed = False
    for line in text.splitlines():
        m = re.search(r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', line)
        if m:
            drop_block = m.group(1) not in nif_shapes
        if drop_block:
            changed = True
            if re.search(r'</per-(?:triangle|vertex)-shape>', line):
                drop_block = False
            continue
        if prune_bones:
            wt = re.search(r'<weight-threshold\s+bone="([^"]+)"', line)
            if wt and wt.group(1).lower() not in resolvable_bones:
                changed = True
                continue
        out.append(line)
    if changed:
        try:
            atomic_write_bytes(xml_path, ("\n".join(out) + "\n").encode("utf-8"))
        except Exception:
            pass


def _select_framework_bone_carriers(xml_bones, present_bones, source_shapes, *,
                                    skel_bones=(), exclude_names=()):
    """Source shape names to re-import as HDT framework-bone carriers: a shape
    that holds a CUSTOM (non-skeleton) bone the authored XML drives but no
    surviving shape provides. Two guards prevent the double-body CTD class:

      * Skeleton bones -- the passed ``skel_bones`` set, or the standard
        ``NPC `` naming prefix -- resolve against the ACTOR skeleton at runtime,
        so they never count as "needing" a mesh. A dropped body that merely
        carried e.g. ``NPC L/R Hand`` is therefore NOT re-imported for them.
      * DROPPED inline-body shapes (``BODY_SHAPE_NAMES`` / placeholder prefixes)
        are never carriers: the body has been replaced by the UBE BaseShape, so
        re-adding it re-creates the double body = HDT-SMP fault on equip.

    ``source_shapes``: iterable of ``(name, bone_names)`` in source-NIF order.
    ``exclude_names``: shapes already present/queued. Greedy -- each carrier
    consumes the needed bones it covers; returns carriers in iteration order.
    """
    skel_lc = {b.lower() for b in skel_bones}
    needed = {b for b in (set(xml_bones) - set(present_bones))
              if b.lower() not in skel_lc and not b.lower().startswith("npc ")}
    if not needed:
        return []
    excl = set(exclude_names)
    work = set(needed)
    carriers: list[str] = []
    for name, bones in source_shapes:
        if name in excl:
            continue
        if _is_inline_body_name(name):
            continue
        hit = set(bones or ()) & work
        if hit:
            carriers.append(name)
            work -= hit
    return carriers


def _finalize_hdt_physics(dst_path: Path, src_nif_path: Path) -> bool:
    """FINAL physics pass — runs AFTER every other NIF round-trip (merge,
    VirtualBody-hide, partition-normalize) so the HDT-SMP extra-data can't
    be dropped by a later save.

    Prefers the source armor's AUTHORED physics XML (which carries the real
    skirt/tassel/flap chains) over the generic generated one: copies it next
    to the output NIF and points the `HDT Skinned Mesh Physics Object`
    extra-data at it. The chain BONES it drives are already preserved by
    `_precreate_custom_bone_chains`, so this is what actually lights up the
    jiggle. Render-safe: only touches physics, never visibility. Returns
    True if extra-data ended up present.
    """
    try:
        pyn = _pynifly()
        # Per-armor XML lives next to the NIF as <stem>.xml (matches the
        # generator's convention); _0/_1 share one file.
        stem = dst_path.stem
        weight_suf = "_1"
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                weight_suf = suf
                stem = stem[:-len(suf)]
                break
        dst_xml_disk = dst_path.parent / f"{stem}.xml"

        # Prefer the source authored XML; overwrite the generic generated
        # one if present. In soft-body mode we KEEP the generated collision-
        # only XML instead (the authored chain XML is what collapses on UBE).
        src_xml = None if CHAIN_TO_SOFTBODY else _read_source_hdt_xml_disk(src_nif_path)
        if src_xml is not None:
            try:
                atomic_copy(str(src_xml), str(dst_xml_disk))
            except Exception:
                pass
        if not dst_xml_disk.is_file():
            return False  # nothing to point at (no source + no generated)

        # Skyrim-relative path ("Meshes\\...").
        parts = list(dst_xml_disk.parts)
        rel = None
        for i, p in enumerate(parts):
            if p.lower() == "meshes":
                parts[i] = "Meshes"
                rel = "\\".join(parts[i:])
                break
        if rel is None:
            return False

        nf = pyn.NifFile(filepath=str(dst_path))
        dirty = False
        has = any(getattr(ed, "name", None) == "HDT Skinned Mesh Physics Object"
                  for ed in nf.rootNode.extra_data())
        if not has:
            from pyn.pynifly import NiStringExtraData  # type: ignore
            NiStringExtraData.New(
                nf, name="HDT Skinned Mesh Physics Object",
                string_value=rel, parent=nf.rootNode)
            dirty = True

        # Collision-proxy preservation: the converter drops textureless proxies
        # (Col_Pants, Col_Strips, ...) so HDT-SMP chains self-intersect.
        # Re-import missing collision shapes from source, flagged Hidden.
        try:
            xml_text = dst_xml_disk.read_text(errors="ignore")
            col_names = set(re.findall(
                r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', xml_text))
            present = {s.name for s in nf.shapes}
            missing = [n for n in col_names if n not in present]
            # Defensive (same bug class as the framework path below): never
            # re-import a dropped inline body even if the XML names it as a
            # per-vertex/per-triangle collider -- that re-adds a hidden body =
            # double body = CTD on equip. Cloth collides with the injected UBE
            # BaseShape instead; the stale ref is pruned by _harden_hdt_xml_for_fsmp.
            missing = [n for n in missing if not _is_inline_body_name(n)]
            # Physics-framework shapes (e.g. "Stabilizer"): textureless, but
            # carry physics BONES the XML references (<bone>/bodyA/bodyB).
            # Dropping them removes bones -> chain constraints have no target ->
            # cloth falls and never settles. Re-imported VERBATIM (no warp) since
            # their verts must match the chain bones recreated at source bind.
            framework_names: set[str] = set()
            xml_bones = set(re.findall(r'<bone\s+name="([^"]+)"', xml_text))
            xml_bones |= set(re.findall(r'\bbody[AB]="([^"]+)"', xml_text))
            present_bones: set[str] = set()
            for _ps in nf.shapes:
                present_bones |= set(_ps.bone_names or [])
            if missing or (xml_bones - present_bones):
                snf = pyn.NifFile(filepath=str(src_nif_path))
                src_by_name = {s.name: s for s in snf.shapes}
                # Re-import shapes that uniquely carry a CUSTOM physics bone the
                # XML drives. Skeleton bones resolve via the actor skeleton; a
                # dropped inline body is NEVER a carrier (re-adding it = double
                # body = CTD on equip). See _select_framework_bone_carriers.
                for cn in _select_framework_bone_carriers(
                        xml_bones, present_bones,
                        [(s.name, list(s.bone_names or [])) for s in snf.shapes],
                        skel_bones=_actor_skeleton_bone_names(),
                        exclude_names=set(present) | set(missing)):
                    missing.append(cn)
                    framework_names.add(cn)
                # Warp collision proxies to match the UBE body; without this the
                # proxy stays at CBBE size and chains collide at the wrong radius.
                _col_cbbe_v = _col_delta = None
                try:
                    _cb = _find_cbbe_base_body(weight_suf)
                    _ub = _find_ube_femalebody(weight_suf)
                    if _cb and _ub:
                        _col_cbbe_v, _col_delta = _cached_cbbe_to_ube_delta(
                            _cb, _ub)
                except Exception:
                    _col_cbbe_v = _col_delta = None
                for cn in missing:
                    src_shape = src_by_name.get(cn)
                    if src_shape is None:
                        continue
                    try:
                        _col_ov = None
                        if (cn not in framework_names
                                and _col_cbbe_v is not None
                                and _col_delta is not None):
                            try:
                                _col_ov = warp_armor_by_body_delta(
                                    np.asarray(src_shape.verts,
                                               dtype=np.float64),
                                    _col_cbbe_v, _col_delta,
                                    min_standoff=0.0,
                                )
                            except Exception:
                                _col_ov = None
                        # Preserve the authored collider/framework skin VERBATIM
                        # -- no genital/jiggle strip. These SMP shapes are
                        # self-contained and internally consistent; stripping
                        # bones desyncs the skin palette FSMP reads -> equip CTD
                        # + the collider deforms wrong. Only the VERTS are warped
                        # (override_verts) to the UBE body radius. #smp-collider-skin-preserve
                        new_col = _copy_shape(src_shape, nf,
                                              override_verts=_col_ov,
                                              preserve_authored_skin=True)
                        # Hide (bit 0): HDT reads geometry; renderer skips Hidden.
                        tgt = new_col if new_col is not None else next(
                            (s for s in nf.shapes if s.name == cn), None)
                        if tgt is not None:
                            cur = int(getattr(tgt, "flags", 0) or 0)
                            tgt.flags = cur | 0x1
                        dirty = True
                    except Exception as _e:
                        # Surface this: a dropped collision proxy/framework breaks
                        # SMP at runtime (skirt/flap collapse).
                        import sys as _sys
                        print(f"  WARN: HDT physics shape '{cn}' failed to "
                              f"re-import into {dst_path.name}: {_e!r} -- SMP "
                              f"shape DROPPED; reconvert this NIF",
                              file=_sys.stderr)
        except Exception:
            pass

        if dirty:
            # Terminal NIF save on the merge/body-swap path: re-assert the
            # VirtualBody Hidden bit (a pynifly round-trip can drop it -> the
            # blue body double). The conform pass only re-hides when it actually
            # conforms verts, so for rigid/flaring/no-jiggle armor THIS is the
            # last save and must restore it itself.
            _hide_virtual_body(nf)
            atomic_nif_save(nf, dst_path)

        # Give a simulated cloth the CHEST body collider it lacks (authored XMLs
        # that only ship a lower-body collider let the UBE breast poke through).
        # Runs BEFORE harden so the added BaseShape block is validated (kept:
        # BaseShape is in the NIF).
        try:
            _ensure_cloth_body_collider(dst_xml_disk, nf)
        except Exception:
            pass
        # FSMP-compatibility hardening: prune XML so FSMP never sees an unresolved
        # shape/bone. Runs after proxy re-import so re-imported shapes count.
        try:
            _harden_hdt_xml_for_fsmp(dst_xml_disk, nf)
        except Exception:
            pass
        # Optional: clamp chain params (CBBE2UBE_HARDEN_PHYSICS=1).
        try:
            _harden_physics_params(dst_xml_disk)
        except Exception:
            pass
        # Optional: make chains static (CBBE2UBE_STATIC_CHAINS=1).
        # Copy-path armor re-applies this after _reauthor.
        try:
            _make_chains_static(dst_xml_disk)
        except Exception:
            pass
        return True
    except Exception:
        if os.environ.get("CBBE2UBE_DEBUG_FINALIZE"):
            import traceback as _tb
            _tb.print_exc()
        return False


def _reauthor_nif_fresh(dst_path: Path, override_verts_by_name=None,
                        exclude_shapes=None) -> bool:
    """Re-author a NIF from scratch into a fresh NifFile — copy every shape
    via _copy_shape (clean pynifly authoring) instead of leaving the
    source-derived bytes produced by the verbatim `shutil.copy2` path.

    `override_verts_by_name`: optional {shape_name -> (N,3) verts} to write NEW
    vertex positions for those shapes (same count/order). Used by the cross-shape
    seam-reconciliation pass to commit welded seam verts while reusing this
    function's skin / BODYTRI / HDT / hidden-flag preservation. Verts must be in
    the shape's STORED frame (== body space for identity-g2s output shapes).

    Why: accessory NIFs (gauntlets/boots/helmets — no body region to fit)
    are copied verbatim then edited in place, so they carry whatever block
    structure the source author shipped. Some of that is tolerated by
    pynifly on read but REJECTED by Skyrim's renderer on a converted /
    re-pathed armor (the "valid-looking but invisible even when worn
    alone" symptom). The body shapes that render are all re-authored from
    scratch (merge / fit rebuild); this gives the accessories the same
    clean structure. Preserves skin (incl. preserved physics-bone chains),
    shader, textures, alpha, partitions, Hidden flags, and the BODYTRI /
    root-HDT extra-data. Returns True if the NIF was rewritten.
    """
    try:
        pyn = _pynifly()
        old = pyn.NifFile(filepath=str(dst_path))
        shapes = list(old.shapes)
        if not shapes:
            return False
        # Capture extra-data + hidden flags to re-apply after rebuild.
        bodytri_str = None
        bodytri_owner = None
        for s in shapes:
            try:
                for ed in s.extra_data():
                    if getattr(ed, "name", None) == "BODYTRI":
                        bodytri_str = ed.string_data
                        bodytri_owner = s.name
                        break
            except Exception:
                pass
            if bodytri_str:
                break
        hdt_str = None
        try:
            for ed in old.rootNode.extra_data():
                if getattr(ed, "name", None) == "HDT Skinned Mesh Physics Object":
                    hdt_str = ed.string_data
                    break
        except Exception:
            pass
        hidden_names = {
            s.name for s in shapes
            if int(getattr(s, "flags", 0) or 0) & 0x1
        }

        tmp_path = dst_path.with_suffix(".nif.reauth")
        new = pyn.NifFile()
        new.initialize("SKYRIMSE", str(tmp_path))
        copy_failed = []
        _ov = override_verts_by_name or {}
        _excl = exclude_shapes or set()
        for s in shapes:
            if s.name in _excl:
                continue                 # drop this shape from the re-author
            try:
                _copy_shape(s, new, override_verts=_ov.get(s.name))
            except Exception as _ce:
                copy_failed.append((s.name, repr(_ce)))
        if copy_failed:
            # A re-authored NIF missing a shape would be atomically committed over
            # the good (verbatim) file = silent partial-mesh loss. Abort instead:
            # keep the prior complete file, surface the drop, never os.replace.
            import sys as _sys
            print(f"  WARN: re-author of {dst_path.name} dropped shape(s) "
                  f"{[n for n, _ in copy_failed]} -> kept prior file "
                  f"(no partial commit)", file=_sys.stderr)
            return False
        for s in new.shapes:
            if s.name in hidden_names:
                try:
                    s.flags = int(getattr(s, "flags", 0) or 0) | 0x1
                except Exception:
                    pass
        from pyn.pynifly import NiStringExtraData  # type: ignore
        if bodytri_str and bodytri_owner:
            tgt = next((x for x in new.shapes if x.name == bodytri_owner), None)
            if tgt is not None:
                NiStringExtraData.New(
                    new, name="BODYTRI", string_value=bodytri_str, parent=tgt)
        if hdt_str:
            NiStringExtraData.New(
                new, name="HDT Skinned Mesh Physics Object",
                string_value=hdt_str, parent=new.rootNode)
        # Route through the shared atomic saver (temp-in-same-dir + os.replace +
        # lock-aware OutputLockedError + temp cleanup on failure) instead of a
        # hand-rolled save()+os.replace, so this writer matches every other
        # game-loaded NIF save. atomic_nif_save repoints new.filepath at its own
        # temp, so the .nif.reauth temp initialized above is unused here.
        from .atomic_io import atomic_nif_save
        atomic_nif_save(new, dst_path)
        try:
            import os as _os
            if tmp_path.is_file():
                _os.remove(str(tmp_path))
        except OSError:
            pass
        return True
    except Exception:
        try:
            import os as _os
            tp = dst_path.with_suffix(".nif.reauth")
            if tp.is_file():
                _os.remove(str(tp))
        except Exception:
            pass
        return False


def _hdt_softbody_shape_names(src_nif_path: Path, nif=None) -> set:
    """Shape names the armor's HDT-SMP XML drives as PER-VERTEX soft-bodies
    (free-swinging cloth, e.g. a hand-authored UBE armor's `soft-body cloth shape`). These must KEEP
    their authored skin weighting: the converter's body-fit reskin (AND the
    post-pass jiggle/chest/butt grafts) would add body jiggle bones the XML has
    no weight-threshold anchor for, so those verts become un-anchored free cloth
    in the sim and DRIFT away from the actor. Resolves the source XML via the
    NIF's own extra-data first, then keyword match. Empty set on any failure
    (reskin proceeds as normal). `nif` reuses an already-loaded NifFile."""
    if CHAIN_TO_SOFTBODY:
        return set()  # soft-body mode: nothing is preserved; reskin all cloth
    txt = _read_source_hdt_xml_text(src_nif_path, nif=nif)
    if not txt:
        return set()
    return set(re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt))


def _read_source_hdt_xml_text(src_nif_path: Path, nif=None) -> "str | None":
    """The armor's authored HDT-SMP XML text, resolved via the NIF's own
    extra-data first, then a keyword match. None on any failure.
    `nif` (optional) reuses an already-loaded NifFile for the extra-data read."""
    try:
        xml_disk = _read_source_hdt_xml_disk(src_nif_path, nif=nif)
        if xml_disk is None:
            rel = _find_hdt_xml_for_armor(src_nif_path)
            if rel:
                norm = rel.replace("\\", "/").lstrip("/")
                for parent in [src_nif_path, *src_nif_path.parents]:
                    if parent.name.lower() == "meshes":
                        cand = parent.parent / norm
                        if cand.is_file():
                            xml_disk = cand
                        break
        if xml_disk is None or not xml_disk.is_file():
            return None
        return xml_disk.read_text(errors="ignore")
    except Exception:
        return None


def _hdt_collider_shape_names(src_nif_path: Path, nif=None) -> set:
    """Shape names the armor's HDT-SMP XML uses as PER-TRIANGLE colliders -- the
    body/ground collision proxies the soft-body cloth bounces off (e.g. a source
    outfit's own `...Col...` body). Like the soft-bodies, these must KEEP their
    authored skin weighting: the body-fit reskin's scale-bone / body-blend graft
    piles excess butt/belly jiggle onto them (measured ~4x the source weight on
    one outfit's skirt collider), so on UBE the collider deforms violently with
    the body physics and destabilises the cloth it is meant to be a STABLE
    collider for (skirt implodes / cloth sinks through the floor). Leave colliders
    exactly as the source authored them. #smp-collider-graft"""
    txt = _read_source_hdt_xml_text(src_nif_path, nif=nif)
    if not txt:
        return set()
    return set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt))


def _glob_first_in_mods(pattern: str,
                        name_substrs: "tuple[str, ...] | None" = None) -> "Path | None":
    """Return the first file matching `pattern` (a mod-relative glob) across
    all installed mods, optionally requiring ALL of `name_substrs`
    (lowercased) to appear in the filename. Portable replacement for
    hardcoded mod paths."""
    root = _paths.mods_root()
    if root is None or not root.is_dir():
        return None
    try:
        mods = sorted(d for d in root.iterdir() if d.is_dir())
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
                return hit
        except OSError:
            continue
    return None


def _find_ube_template_body() -> Path | None:
    """Find a UBE Release Body template NIF (BodySlide ShapeData, slider-
    zero). Scanned from any mod's CalienteTools/BodySlide/ShapeData by
    content/name hint — no fixed mod name. Env override: CBBE2UBE_UBE_TEMPLATE."""
    ck = "ube_template"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    env = os.environ.get("CBBE2UBE_UBE_TEMPLATE")
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(env)
        return _BODY_DISCOVERY_CACHE[ck]
    pat = "CalienteTools/BodySlide/ShapeData/*/*.nif"
    # Prefer the canonical "...Release Body" over outfit-specific UBE body
    # variants (BDOR_Hair, etc.) that also contain "ube"+"body".
    # CACHED (per-NIF caller, scans all mods).
    res = (_glob_first_in_mods(pat, name_substrs=("ube", "release", "body"))
           or _glob_first_in_mods(pat, name_substrs=("ube", "body")))
    _BODY_DISCOVERY_CACHE[ck] = res
    return res


def _find_ube_body_osd() -> Path | None:
    """Find a UBE body OSD (slider-deltas catalog) for M8 auto-TRI. Scanned
    from any mod's BodySlide ShapeData by name hint. Env override:
    CBBE2UBE_UBE_OSD. Result is cached (stable for the process lifetime)."""
    ck = "ube_osd"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    env = os.environ.get("CBBE2UBE_UBE_OSD")
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(env)
        return _BODY_DISCOVERY_CACHE[ck]
    pat = "CalienteTools/BodySlide/ShapeData/*/*.osd"
    res = (_glob_first_in_mods(pat, name_substrs=("ube", "release", "body"))
           or _glob_first_in_mods(pat, name_substrs=("ube", "body")))
    _BODY_DISCOVERY_CACHE[ck] = res
    return res


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


def check_ube_nude_morph_files() -> "list[str]":
    """Pre-flight check for the UBE NUDE body/hands/feet morph (.tri) files.

    The UBE nude race skin morphs to the player's RaceMenu sliders via a
    BodySlide-built `.tri` that sits NEXT TO the mesh (femalebody_tangent.tri,
    femalehands_tangent.tri, femalefeet_tangent.tri) and is found by name
    convention -- NOT via a `BODYTRI` string extra-data (that's the armor
    convention; the nude meshes carry none). BodySlide writes the .tri only
    when 'Build Morphs' is checked. If the body has its .tri but hands/feet
    don't, the hands/feet stay at base shape while the body morphs to the
    preset -> they look mismatched / 'not UBE'. This is purely a BodySlide
    build issue (the converter doesn't touch the nude skin), but it's a
    common, hard-to-spot trap, so we surface it. Returns warning strings.
    """
    warns: "list[str]" = []
    parts = [
        ("Body", "meshes/!UBE/Body/femalebody_tangent_1.nif"),
        ("Hands", "meshes/!UBE/Hands/femalehands_tangent_1.nif"),
        ("Feet", "meshes/!UBE/Feet/femalefeet_tangent_1.nif"),
    ]
    found = []
    for label, pat in parts:
        p = _glob_first_in_mods(pat)
        if p is None:
            warns.append(
                f"UBE {label}: no !UBE/{label} nude mesh found -- the UBE "
                f"{label} BodySlide group isn't built.")
            continue
        p = Path(p)
        # femalebody_tangent_1.nif -> femalebody_tangent.tri (weight stripped)
        stem = p.stem
        if stem.endswith(("_0", "_1")):
            stem = stem[:-2]
        tri = p.with_name(stem + ".tri")
        found.append((label, tri.is_file()))
        if not tri.is_file():
            warns.append(
                f"UBE {label}: mesh present but NO morph file ({tri.name}) -- "
                f"the nude {label} won't follow body sliders (stays at base "
                f"shape while the body morphs). Rebuild the UBE {label} in "
                f"BodySlide with 'Build Morphs' checked.")
    # Highlight the specific asymmetry that produces the 'body is UBE but
    # hands/feet aren't' symptom.
    fd = dict(found)
    if fd.get("Body") and (fd.get("Hands") is False or fd.get("Feet") is False):
        warns.append(
            "UBE nude skin: the BODY has morph data but the HANDS/FEET do "
            "not -- this is exactly the 'body morphs UBE, hands/feet stay "
            "CBBE-shaped' mismatch. Rebuild Hands + Feet with Build Morphs.")
    return warns


# Body-skin Hands/Feet shape names that appear in CBBE 3BA gauntlet/boot
# armor NIFs. When the converter encounters one of these in a slot-33 or
# slot-37 source NIF, it drops the CBBE shape and injects the user's UBE
# Hands/Feet in its place — see `_inject_ube_extremity_replacement` and
# the phase-1 fit-path call site for the full rationale.
BODY_SKIN_HAND_NAMES = frozenset({"Hands", "FemaleHands"})
BODY_SKIN_FOOT_NAMES = frozenset({"Feet", "FemaleFeet"})

# Detection patterns (broader than the exact-name sets above). Outfit
# Studio / mod authors frequently suffix a duplicated body-skin hand/foot
# shape — a mashup armor's gauntlet ships its CBBE hand as "Hands_2", another armor-
# style boots as "Feet_1", etc. The exact-name sets miss those, so the
# CBBE hand/foot survived conversion and rendered (the "gloves reset my
# hands to CBBE" bug). Match "Hands"/"FemaleHands"/"Feet"/"FemaleFeet"
# with an optional numeric suffix ("_2", " 2", "2") but NOT armor pieces
# like "Gloves_1", "Handstrap", "Bracers". The injection side keeps using
# the exact sets above (the UBE replacement ref NIF's shape is named
# exactly "Hands"/"Feet").
_BODY_SKIN_HAND_RE = re.compile(r"^(female)?hands(\s*_?\d+)?$", re.IGNORECASE)
_BODY_SKIN_FOOT_RE = re.compile(r"^(female)?feet(\s*_?\d+)?$", re.IGNORECASE)


def _is_body_skin_hand(name: str) -> bool:
    return bool(name) and _BODY_SKIN_HAND_RE.match(name) is not None


def _is_body_skin_foot(name: str) -> bool:
    return bool(name) and _BODY_SKIN_FOOT_RE.match(name) is not None


def _is_body_skin_extremity(name: str) -> bool:
    return _is_body_skin_hand(name) or _is_body_skin_foot(name)


def _should_drop_shape(name: str) -> bool:
    """Whether a source shape should be skipped as a vestigial mashup leftover.
    Currently a no-op (returns False). Add rules here if a specific shape needs
    filtering."""
    return False


def _inject_ube_extremity_replacement(
        dst_nif, weight_suffix: str, slot_to_replace: str,
        injected: list[str],
) -> None:
    """Copy a single UBE extremity shape (Hands OR Feet) from the user's
    BodySlide-built tangent NIF into `dst_nif`.

    Used to REPLACE a CBBE-topology body-skin Hands/Feet shape that the
    source gauntlet/boot NIF contained. Caller is responsible for
    dropping the source shape; this function just adds the UBE version.

    Distinct from the discontinued body-NIF inject (slot 32 NIFs):
    here the gauntlet/boot occupies slot 33/37, so the actor's slot
    33/37 ARMA is automatically hidden — no z-fight risk from a
    duplicate Hands/Feet mesh elsewhere.

    `slot_to_replace` is one of "Hands" or "Feet" (case-sensitive).
    `injected` is mutated in-place with the name of any shape copied.
    """
    if slot_to_replace == "Hands":
        ref_p = _glob_first_in_mods(
            f"meshes/!UBE/Hands/femalehands_tangent{weight_suffix}.nif")
        wanted = BODY_SKIN_HAND_NAMES
    elif slot_to_replace == "Feet":
        ref_p = _glob_first_in_mods(
            f"meshes/!UBE/Feet/femalefeet_tangent{weight_suffix}.nif")
        wanted = BODY_SKIN_FOOT_NAMES
    else:
        return
    if ref_p is None or not ref_p.is_file():
        return
    try:
        pynifly = _pynifly()
        ref_nif = pynifly.NifFile(filepath=str(ref_p))
        existing = {sh.name for sh in dst_nif.shapes}
        for sh in ref_nif.shapes:
            if sh.name not in wanted or sh.name in existing:
                continue
            try:
                _copy_shape(sh, dst_nif)
                injected.append(sh.name)
            except Exception:
                pass
    except Exception:
        pass


def _inject_ube_baseshape(
        ube_nif, dst_nif,
        body_inject_names: tuple[str, ...],
        inject_baseshape: bool,
        injected: list[str],
) -> "tuple[str, str] | None":
    """Inject UBE BaseShape (+VirtualBody) from `ube_nif` into `dst_nif`.

    BaseShape verts are copied byte-identical to the source UBE body NIF
    (no genital morph bake — the historical bake opened UBE's topology
    hole wider, see `_close_pubic_holes` comment block for the full
    rationale). The pubic boundary loops are sealed via fan
    triangulation using only existing verts, so armored body == nude
    body geometry except for the +366 fill tris that close the gaps.

    Mutates `injected` in-place with the names of shapes copied.
    Returns (shape_name, repr_exc) on copy failure — caller turns that
    into a ConvertResult.skipped. Returns None on success.
    """
    for s in ube_nif.shapes:
        if s.name not in body_inject_names:
            continue
        if s.name == "BaseShape" and not inject_baseshape:
            continue
        # Mesh-surgery hole closure for BaseShape. UBE's mesh ships with
        # 5 open boundary loops at the pubis (designed for a TNG/SoS
        # plug-mesh that doesn't exist in pure-UBE setups). Triangulate
        # them with fan tris using only existing verts. See
        # `_close_pubic_holes` for details.
        override_tris = None
        if s.name == "BaseShape":
            try:
                src_verts = np.asarray(s.verts, dtype=np.float64)
                src_normals = (np.asarray(s.normals, dtype=np.float64)
                               if s.normals is not None else None)
                if src_normals is not None:
                    sealed_tris, n_loops = _close_pubic_holes(
                        src_verts,
                        np.asarray(s.tris, dtype=np.int64),
                        src_normals,
                    )
                    if n_loops > 0:
                        override_tris = sealed_tris
            except Exception:
                override_tris = None
        try:
            _copy_shape(s, dst_nif, override_tris=override_tris)
            injected.append(s.name)
        except Exception as e:
            return (s.name, repr(e))
    return None


# Adjacent solid plates that share a seam (edges modeled flush) are warped as independent
# shapes, so their shared seam ring drifts apart into a visible gap. Fix: verts coincident
# across different plate shapes in the SOURCE were meant to touch -- weld each such
# cross-shape cluster to its centroid AFTER the warp. Tight source-coincidence tol is the
# gate, so it never welds an intentional layer (which sits mm above, not coincident);
# normals aren't gated (a flush rim has opposed normals but is a real seam). Identity-g2s
# only. CBBE2UBE_NO_SEAM_WELD=1 off; CBBE2UBE_SEAM_WELD_TOL overrides tol.
_SEAM_WELD_TOL = float(os.environ.get("CBBE2UBE_SEAM_WELD_TOL", "0.05") or "0.05")


def _weld_cross_shape_seams(shape_jobs, tol: float = _SEAM_WELD_TOL):
    """Weld source-coincident cross-plate seam verts to their centroid.

    Operates on the pass-1 `shape_jobs` (each {"src", "verts",
    "verts_modified", ...}). Returns the count of welded verts. Best-effort.
    """
    if os.environ.get("CBBE2UBE_NO_SEAM_WELD", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    def _ident_g2s(s):
        try:
            if not s.has_global_to_skin:
                return True
            g = _shape_global_to_skin(s)
            return g is None or _g2s_is_identity(g)
        except Exception:
            return False

    # Candidate plates: textured, non-effect, identity-g2s (frame match).
    plates = [j for j in shape_jobs
              if (j["src"].textures or {})
              and not _shape_has_effect_shader(j["src"])
              and _ident_g2s(j["src"])]
    if len(plates) < 2:
        return 0
    src_arrs, fin_arrs, owner = [], [], []  # owner[g] = (plate_idx, local_idx)
    for pi, pj in enumerate(plates):
        try:
            psrc = np.asarray(pj["src"].verts, dtype=np.float64)
            pfin = np.asarray(pj["verts"], dtype=np.float64).copy()
        except Exception:
            src_arrs.append(None); fin_arrs.append(None); continue
        if psrc.ndim != 2 or psrc.shape != pfin.shape or len(psrc) == 0:
            src_arrs.append(None); fin_arrs.append(None); continue
        src_arrs.append(psrc); fin_arrs.append(pfin)
        owner.extend((pi, li) for li in range(len(psrc)))
    valid = [a for a in src_arrs if a is not None]
    if len(valid) < 2:
        return 0
    all_src = np.concatenate([a for a in src_arrs if a is not None])
    tree = cKDTree(all_src)
    try:
        pairs = tree.query_pairs(tol, output_type="ndarray")
    except Exception:
        return 0
    if len(pairs) == 0:
        return 0
    # Union-find over CROSS-shape coincident pairs only.
    parent = list(range(len(owner)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        a, b = int(a), int(b)
        if owner[a][0] != owner[b][0]:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    from collections import defaultdict
    groups = defaultdict(list)
    for g in range(len(owner)):
        groups[find(g)].append(g)
    n_weld = 0
    changed = set()
    seam_clusters = []  # each: [(plate_idx, local_idx), ...] spanning >=2 plates
    for members in groups.values():
        if len(members) < 2:
            continue
        cluster = [owner[m] for m in members]
        if len({pi for pi, _ in cluster}) < 2:
            continue  # single-shape cluster -> not a cross-shape seam
        centroid = np.mean([fin_arrs[pi][li] for pi, li in cluster], axis=0)
        for pi, li in cluster:
            fin_arrs[pi][li] = centroid
            changed.add(pi)
            n_weld += 1
        seam_clusters.append(cluster)
    for pi in changed:
        plates[pi]["verts"] = fin_arrs[pi]
        plates[pi]["verts_modified"] = True
    # Skin-match: give every vert in a welded seam cluster IDENTICAL weights so
    # the plates DEFORM together under animation. A position-only weld reopens
    # when the two plates' seam verts follow different bones (MEASURED: the
    # Daedric waist seam's cross-plate skin diff went 0.25 in source -> 0.70
    # after the independent per-shape reskin -> the seam splits when posed).
    if seam_clusters and os.environ.get(
            "CBBE2UBE_NO_SEAM_SKIN_MATCH", "").strip().lower() not in (
            "1", "true", "yes", "on"):
        try:
            nsm = _match_seam_skinning(plates, seam_clusters)
            if os.environ.get("CBBE2UBE_SEAM_DEBUG"):
                import sys as _sys
                print("  [seam-dbg] clusters=%d skin-matched verts=%s haveOSK=%s"
                      % (len(seam_clusters), nsm,
                         [bool(plates[pi].get("override_skin"))
                          for pi in {p for c in seam_clusters for p, _ in c}]),
                      file=_sys.stderr)
        except Exception as _sm:
            if os.environ.get("CBBE2UBE_SEAM_DEBUG"):
                import sys as _sys, traceback as _tb
                print("  [seam-dbg] EXC %r" % (_sm,), file=_sys.stderr)
                _tb.print_exc()
    return n_weld


def _set_override_vert_weights(osk, vi, tgt, bone_xform):
    """Set vert `vi`'s skin weights in an override_skin dict to exactly `tgt`
    ({bone: weight}). Removes `vi` from every existing bone entry, then adds it
    to each target bone, creating the bone in the bones list / xforms map as
    needed (xform pulled from `bone_xform`, the cluster-wide skeleton-global
    skin-to-bone map)."""
    weights = osk["weights"]
    bones = osk.setdefault("bones", list(weights.keys()))
    xforms = osk.setdefault("xforms", {})
    for bn in list(weights.keys()):
        weights[bn] = [(v, w) for (v, w) in weights[bn] if int(v) != vi]
    for bn, w in tgt.items():
        if w <= 0:
            continue
        weights.setdefault(bn, []).append((vi, float(w)))
        if bn not in bones:
            bones.append(bn)
        if bn not in xforms and bn in bone_xform:
            xforms[bn] = bone_xform[bn]


def _match_seam_skinning(plates, seam_clusters):
    """Unify skin weights across welded cross-plate seam clusters so both
    plates deform together. For each cluster: read every member vert's current
    weights, average them (missing bone = 0), cap to the engine's 4-bone
    per-vertex limit (top-4 by weight), renormalize, and write that identical
    weighting onto every member. Rewrites each plate job's override_skin in
    place. Skips a cluster if any member lacks an override_skin (source-skinned
    shape -- would need a full skin rebuild). Best-effort. Returns the count of
    verts whose weights were unified."""
    def _osk_from_source(src_shape):
        # Build an override_skin that FAITHFULLY copies the source skin (all
        # bones/xforms/weights) so a source-skinned member can have just its
        # seam verts edited. Guard on bone count: the override path caps bones
        # (a no-op under the GPU limit) where the source path would split, so
        # only build when capping can't drop a bone (dense shapes -> skip).
        bones = list(src_shape.bone_names or [])
        if not bones or len(bones) > 40:
            return None
        xforms = {}
        for bn in bones:
            try:
                xf = src_shape.get_shape_skin_to_bone(bn)
                if xf is not None:
                    xforms[bn] = xf
            except Exception:
                pass
        weights = {}
        for bn, pairs in (src_shape.bone_weights or {}).items():
            weights[bn] = [(int(i), float(w)) for i, w in
                           (pairs.tolist() if hasattr(pairs, "tolist") else pairs)]
        return {"bones": bones, "xforms": xforms, "weights": weights}

    n_matched = 0
    for cluster in seam_clusters:
        oss = []
        ok = True
        for pi, li in cluster:
            osk = plates[pi].get("override_skin")
            if not osk or "weights" not in osk:
                # Source-skinned member: synthesize an override_skin so its
                # seam verts can be matched (pass 2 then uses it).
                built = _osk_from_source(plates[pi].get("src"))
                if not built:
                    ok = False
                    break
                plates[pi]["override_skin"] = built
                osk = built
            oss.append(osk)
        if not ok:
            continue
        member_w = []
        bone_xform = {}  # cluster-wide bone -> skin-to-bone (skeleton-global)
        for (pi, li), osk in zip(cluster, oss):
            wd = {}
            for bn, pairs in osk["weights"].items():
                for vi, w in pairs:
                    if int(vi) == li:
                        wd[bn] = wd.get(bn, 0.0) + float(w)
            member_w.append(wd)
            for bn, xf in (osk.get("xforms") or {}).items():
                bone_xform.setdefault(bn, xf)
        allb = set().union(*member_w) if member_w else set()
        if not allb:
            continue
        tgt = {bn: sum(wd.get(bn, 0.0) for wd in member_w) / len(member_w)
               for bn in allb}
        # Cap to 4 bones per vertex (engine skin-partition limit).
        if len(tgt) > 4:
            top = sorted(tgt.items(), key=lambda kv: kv[1], reverse=True)[:4]
            tgt = dict(top)
        tot = sum(tgt.values())
        if tot <= 0:
            continue
        tgt = {bn: w / tot for bn, w in tgt.items()}
        for (pi, li), osk in zip(cluster, oss):
            _set_override_vert_weights(osk, int(li), tgt, bone_xform)
            n_matched += 1
    return n_matched


# Effect-shader decal overlays sit ~0.03u off their solid plate as a thin additive shell.
# They're not body-hugging, so the per-vertex fit passes displace them and their plate by
# slightly different amounts, amplifying that tiny offset into a visible gap (the glow
# "clips through"). Fix: after every vertex pass, make each overlay RIDE its plate --
# re-derive each overlay vert from the FINAL position of its nearest source-paired plate
# vert, preserving the source offset. CBBE2UBE_NO_GLOW_RIDE=1 disables.
# [DESIGN: Effect-shader glow overlays]
_GLOW_RIDE_MAX = float(os.environ.get("CBBE2UBE_GLOW_RIDE_MAX", "2.0") or "2.0")


def _ride_effect_overlays_on_plate(shape_jobs, ride_max: float = _GLOW_RIDE_MAX):
    """Re-bind BSEffectShaderProperty decal overlays to ride their solid plate.

    Operates on the pass-1 `shape_jobs` list (each: {"src", "verts",
    "verts_modified", ...}). For every effect-shader overlay shape, pair each of
    its SOURCE verts to the nearest SOURCE vert across all NON-effect (plate)
    shapes, then set the overlay's FINAL vert = that plate's FINAL vert + the
    source offset vector. Per-vert gated on `ride_max` (world units) so an
    overlay with no plate beneath it keeps its independently-warped verts.
    Returns the count of re-bound overlay verts. Best-effort; a caller wraps it.
    """
    if os.environ.get("CBBE2UBE_NO_GLOW_RIDE", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    def _ident_g2s(s):
        # Ride only between identity-global-to-skin shapes so the source verts
        # (skin frame) and the job's final verts share a frame in BOTH phases
        # (phase-1 stores WORLD, phase-2 stores SKIN; identical when g2s is
        # identity). A non-identity-g2s shape keeps its own warp (graceful).
        try:
            if not s.has_global_to_skin:
                return True
            g = _shape_global_to_skin(s)
            return g is None or _g2s_is_identity(g)
        except Exception:
            return False

    overlays = [j for j in shape_jobs
                if _shape_has_effect_shader(j["src"]) and _ident_g2s(j["src"])]
    plates = [j for j in shape_jobs
              if not _shape_has_effect_shader(j["src"]) and _ident_g2s(j["src"])]
    if os.environ.get("CBBE2UBE_GLOW_RIDE_DEBUG"):
        import sys as _sys
        print("  [ride-dbg] overlays=%r plates=%r" % (
            [j["src"].name for j in overlays],
            [j["src"].name for j in plates]), file=_sys.stderr)
    if not overlays or not plates:
        return 0
    # Flat SOURCE + FINAL plate vert arrays (kept in lockstep). A plate whose
    # final-vert count differs from its source (shouldn't happen: the fit is
    # 1:1) is skipped so the pairing stays valid.
    src_flat, fin_flat = [], []
    for pj in plates:
        try:
            psrc = np.asarray(pj["src"].verts, dtype=np.float64)
            pfin = np.asarray(pj["verts"], dtype=np.float64)
        except Exception:
            continue
        if psrc.ndim != 2 or psrc.shape != pfin.shape or len(psrc) == 0:
            continue
        src_flat.append(psrc)
        fin_flat.append(pfin)
    if not src_flat:
        return 0
    src_flat = np.concatenate(src_flat)
    fin_flat = np.concatenate(fin_flat)
    tree = cKDTree(src_flat)
    n_rebound = 0
    for oj in overlays:
        try:
            gsrc = np.asarray(oj["src"].verts, dtype=np.float64)
            cur = np.asarray(oj["verts"], dtype=np.float64)
        except Exception:
            continue
        if gsrc.ndim != 2 or cur.shape != gsrc.shape or len(gsrc) == 0:
            continue
        d, idx = tree.query(gsrc, k=1)
        mask = d <= ride_max
        if not np.any(mask):
            continue
        cur = cur.copy()
        # final = plate_final[nearest] + (glow_src - plate_src[nearest])
        cur[mask] = fin_flat[idx[mask]] + (gsrc[mask] - src_flat[idx[mask]])
        oj["verts"] = cur
        oj["verts_modified"] = True
        n_rebound += int(mask.sum())
    return n_rebound


def convert_nif_phase2(
    src_path: str | Path,
    dst_path: str | Path,
    *,
    ube_body_ref_path: str | Path,
    body_inject_names: tuple[str, ...] = UBE_BODY_INJECT_NAMES,
    cbbe_body_ref_path: str | Path | None = None,
    fit_armor: bool = False,
    bake_preset: bool = True,
    reskin_armor: bool = True,
    reskin_near_dist: float = RESKIN_NEAR_DIST,
    reskin_far_dist: float = RESKIN_FAR_DIST,
    reskin_k: int = RESKIN_K,
    auto_gen_tri: bool = True,
    # BaseShape injection: ON by default. Without it, a slot-32 armor has
    # no body under the cloth (slot-32 hides the actor's femalebody). UBE
    # BaseShape's genital region is bone-driven inside the 29298-vert mesh.
    inject_baseshape: bool = True,
    biped_slots: int = 0,
    alt_texture_shape_names: "set[str] | None" = None,
    extra_body_drop_names: "tuple[str, ...]" = (),
) -> ConvertResult:
    """Phase-2 conversion: swap inline CBBE body shapes for UBE body shapes.

    `extra_body_drop_names`: shape names to treat as body skin to DROP (in
    addition to the auto-classified inline bodies) and replace with the
    injected UBE body. Used for EXPOSED body-skin slices baked into an armor
    (an open-cleavage corset's breast/cleavage skin) that aren't a full inline
    body but should still be replaced by the whole UBE body so the bare skin is
    seamless to the neck and morphs as one — see `_exposed_body_skin_shape_names`.

    Process:
      1. Open the UBE body reference NIF (must contain BaseShape — and
         ideally VirtualBody) — typically a BodySlide-built UBE armor NIF
         like a revealing slot-32 top NIF since the standalone UBE body NIF
         only ships BaseShape.
      2. Create a fresh target NIF.
      3. Deep-copy each shape from the UBE ref whose name is in
         `body_inject_names`.
      4. Deep-copy each non-body shape from the CBBE source.
      5. Save.

    Returns ConvertResult with status="converted (body-swap)" on success.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    ube_body_ref_path = Path(ube_body_ref_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    pynifly = _pynifly()

    src_nif = nif_io.open_nif_retry(str(src_path))  # transient-IO resilient
    ube_nif = nif_io.open_nif_retry(str(ube_body_ref_path))  # every worker opens the ref -> contention

    # Determine body vs armor shapes in src
    src_wrapped = nif_io.load_nif(src_path)
    body_names, armor_names = classify_shapes(src_wrapped)

    # Fold in any caller-supplied exposed-skin slices: treat them as body
    # (so the source-shape copy loop DROPS them) and let the injected UBE
    # body stand in. The drop loop below keys on `body_names`.
    if extra_body_drop_names:
        _extra = [n for n in extra_body_drop_names if n not in body_names]
        body_names = list(body_names) + _extra
        armor_names = [n for n in armor_names if n not in set(extra_body_drop_names)]

    if not body_names:
        # No body shapes to swap — phase 1 (copy) is what you want here.
        return ConvertResult(
            src_path=src_path, dst_path=None,
            status="skipped",
            reason="no inline body shapes; use phase 1 (copy) for this file",
            armor_shapes=armor_names,
        )

    # Build target NIF from scratch
    dst_nif = pynifly.NifFile()
    dst_nif.initialize("SKYRIMSE", str(dst_path))

    # BODYTRI path: use a pre-built armor TRI if found (has _ForOutfits slider
    # bridges for RaceMenu), otherwise fall back to the body TRI.
    armor_relpath = None
    try:
        # Compute relative path from a meshes root marker if present
        parts = src_path.parts
        for marker in ("meshes", "Meshes"):
            if marker in parts:
                i = parts.index(marker)
                armor_relpath = Path(*parts[i + 1:])
                break
    except Exception:
        pass
    body_tri_path = UBE_BODY_TRI_PATH
    # Always auto-generate the armor TRI from CBBE source + UBE body
    # OSD slider data (see module-level UBE_BODY_TRI_PATH note).
    auto_tri_dst: Path | None = None  # if set, write generated TRI here
    if armor_relpath is not None and auto_gen_tri:
        tri_stem = dst_path.stem
        for suf in ("_0", "_1"):
            if tri_stem.endswith(suf):
                tri_stem = tri_stem[:-len(suf)]
                break
        auto_tri_dst = dst_path.parent / (tri_stem + ".tri")
        # Compute Skyrim-relative path from auto_tri_dst by finding
        # the "meshes" segment.
        dst_parts = auto_tri_dst.parts
        for i, seg in enumerate(dst_parts):
            if seg.lower() == "meshes":
                body_tri_path = "\\".join(dst_parts[i + 1:])
                break

    # Copy UBE BaseShape + VirtualBody from the UBE template NIF (user's
    # preset femalebody_tangent). Pubic holes sealed by fan triangulation.
    # Slider morphs apply through the per-armor TRI at runtime.
    # Shapes covered by the SOURCE mod's own BodySlide morph TRI -> prefer their
    # stable source skin over the M6 reskin (the TRI morphs them at runtime; the
    # reskin's body-bone blend is the equip-fly/CTD instability and is redundant
    # here). Computed once; consumed in the reskin gate below. See
    # RESKIN_PREFER_SOURCE_WHEN_MORPH_TRI.
    src_morph_shapes = (_source_morph_tri_shape_names(src_path)
                        if RESKIN_PREFER_SOURCE_WHEN_MORPH_TRI else set())

    injected: list[str] = []
    copy_err = _inject_ube_baseshape(
        ube_nif, dst_nif, body_inject_names, inject_baseshape, injected,
    )
    if copy_err is not None:
        shape_name, exc_repr = copy_err
        return ConvertResult(
            src_path=src_path, dst_path=None,
            status="skipped",
            reason=f"failed to copy UBE shape {shape_name!r}: {exc_repr}",
        )

    # Hands/Feet NOT injected: slot 33/37 ARMAs stay live alongside slot 32.
    # UBE_AllRace.esp already routes those slots to UBE meshes; injecting
    # them here would duplicate geometry and cause z-fight.

    # Disable VirtualBody rendering — see `_hide_virtual_body` docstring.
    _hide_virtual_body(dst_nif)

    # BODYTRI attached to the first armor shape (not BaseShape), mirroring
    # hand-authored UBE NIFs where NioOverride morphs all TRI shapes via
    # an armor-shape carrier. Added after armor shapes are copied below.

    if not injected:
        return ConvertResult(
            src_path=src_path, dst_path=None,
            status="skipped",
            reason=f"UBE ref {ube_body_ref_path.name} has no shapes in "
                   f"{body_inject_names}",
        )

    # Build body MeshIndexes for the armor-fit pass (if enabled + refs available).
    # CBBE body: inline shape from source or cbbe_body_ref_path fallback.
    cbbe_idx = ube_idx = None
    # Source body verts+normals for standoff-preserving conform. Detected
    # unconditionally: the conform runs regardless of `fit_armor`.
    src_body_v_p2 = src_body_n_p2 = None

    def _is_body_pynifly_shape(s):
        if s.name in BODY_SHAPE_NAMES:
            return True
        name_low = (s.name or "").lower()
        for prefix in BODY_SHAPE_NAME_PREFIXES:
            if name_low.startswith(prefix):
                return True
        if len(s.verts) < _BODY_HEURISTIC_MIN_VERTS:
            return False
        if len(s.bone_names) < _BODY_HEURISTIC_MIN_BONES:
            return False
        z = np.asarray(s.verts, dtype=np.float64)[:, 2]
        if float(z.max() - z.min()) < _BODY_HEURISTIC_MIN_Z_RANGE:
            return False
        # Texture gate (see _looks_like_inline_body): a full-length
        # robe must not be picked as the CBBE body reference.
        return _shape_diffuse_is_body_skin(s)

    cbbe_body_shape = next((s for s in src_nif.shapes if _is_body_pynifly_shape(s)), None)
    if cbbe_body_shape is None and cbbe_body_ref_path is not None:
        cbbe_ref = nif_io.open_nif_retry(str(Path(cbbe_body_ref_path)))  # transient-IO resilient
        cbbe_body_shape = max(cbbe_ref.shapes, key=lambda s: len(s.verts)) if cbbe_ref.shapes else None
    if cbbe_body_shape is not None:
        _sbn = getattr(cbbe_body_shape, "normals", None)
        if _sbn is not None and len(_sbn) == len(cbbe_body_shape.verts):
            src_body_v_p2 = np.asarray(cbbe_body_shape.verts, dtype=np.float64)
            src_body_n_p2 = np.asarray(_sbn, dtype=np.float64)

    if fit_armor:
        ube_body_shape = next((s for s in ube_nif.shapes if s.name == "BaseShape"), None)
        if cbbe_body_shape is not None and ube_body_shape is not None:
            cbbe_idx = MeshIndex.build(
                np.asarray(cbbe_body_shape.verts, dtype=np.float64),
                np.asarray(cbbe_body_shape.tris, dtype=np.int64),
            )
            ube_idx = MeshIndex.build(
                np.asarray(ube_body_shape.verts, dtype=np.float64),
                np.asarray(ube_body_shape.tris, dtype=np.int64),
            )

    # Build UBE-template + user-preset body vert arrays for preset baking.
    # When `bake_preset` is on, armor verts get the user's body morph
    # propagated to them via K-nearest body vertex weighted average.
    preset_template_verts = None
    preset_user_verts = None
    if bake_preset:
        # Match weight suffix
        weight = "_1"
        for s in ("_0", "_1"):
            if Path(src_path).stem.endswith(s):
                weight = s; break
        tmpl_p = _find_ube_template_body()
        user_p = _find_user_preset_body(weight)
        if tmpl_p is not None and user_p is not None:
            try:
                tmpl_nif = pynifly.NifFile(filepath=str(tmpl_p))
                user_nif = pynifly.NifFile(filepath=str(user_p))
                tmpl_bs = tmpl_nif.shape_dict.get("BaseShape")
                user_bs = user_nif.shape_dict.get("BaseShape")
                if tmpl_bs is not None and user_bs is not None and \
                   len(tmpl_bs.verts) == len(user_bs.verts):
                    preset_template_verts = np.asarray(tmpl_bs.verts, dtype=np.float64)
                    preset_user_verts = np.asarray(user_bs.verts, dtype=np.float64)
            except Exception:
                preset_template_verts = preset_user_verts = None

    # Copy non-body shapes from source via TWO-PASS conversion so
    # we can z-fight-fix across shapes:
    #   Pass 1: compute per-shape final verts (bake / fit / snap) and
    #           M6 re-skin data. Don't call _copy_shape yet.
    #   Z-fight: detect verts in different shapes within ~0.05 units;
    #            push the inner one inward (along body normal).
    #   Pass 2: _copy_shape each with offset-adjusted verts + skin.
    copied: list[str] = []
    failed: list[tuple[str, str]] = []
    skipped_collision: list[str] = []  # M7 Fix 1
    shape_jobs: list[dict] = []        # per-shape state for pass 2

    # Body verts + normals used by snap (legacy), z-fight, and as the
    # authoritative outward direction for offset application.
    ube_base_for_pass1 = next(
        (x for x in ube_nif.shapes if x.name == "BaseShape"), None)
    body_nipple_for_p2 = None
    if ube_base_for_pass1 is not None:
        body_verts_for_p2 = np.asarray(
            ube_base_for_pass1.verts, dtype=np.float64)
        # Compute normals from tris when the body NIF ships none/zeroed (common
        # for BodySlide output) -- else the conform/standoff passes that push
        # along the body normal silently no-op (#175). _body_nipple_weight gives
        # the bust pass its Breast03 nipple localization.
        body_norms_for_p2 = _body_normals_or_compute(ube_base_for_pass1)
        body_nipple_for_p2 = _body_nipple_weight(ube_base_for_pass1)
    else:
        body_verts_for_p2 = None
        body_norms_for_p2 = None

    # Body-delta warp: prefer the principled per-vert CBBE -> UBE
    # delta over the snap heuristic when both 18k-vert bodies are
    # available. See `warp_armor_by_body_delta`.
    weight_suf_p2 = next(
        (s for s in ("_0", "_1") if src_path.stem.endswith(s)), "_1")
    cbbe_body_path_p2 = _find_cbbe_base_body(weight=weight_suf_p2)
    ube_femalebody_path_p2 = _find_ube_femalebody(weight=weight_suf_p2)
    cbbe_verts_for_warp_p2 = None
    body_delta_for_warp_p2 = None
    if cbbe_body_path_p2 and ube_femalebody_path_p2:
        cbbe_verts_for_warp_p2, body_delta_for_warp_p2 = \
            _cached_cbbe_to_ube_delta(
                cbbe_body_path_p2, ube_femalebody_path_p2)

    # HDT-SMP per-vertex soft-body cloth keeps its authored weighting
    # (skip body-fit reskin) so it can still swing — see
    # _hdt_softbody_shape_names.
    hdt_softbody_names = _hdt_softbody_shape_names(src_path)
    # SMP colliders (per-triangle) likewise skip the reskin/anti-poke -- the
    # graft over-jiggles them and destabilises the cloth (see
    # _hdt_collider_shape_names).
    hdt_collider_names = _hdt_collider_shape_names(src_path)
    # Multi-layer cloth (Cuirass_A/_B/_C) keeps source skin -- every graft pass skips it
    # or it CTDs on equip (see _layered_cloth_shape_names).
    layered_cloth_names = _layered_cloth_shape_names(src_nif.shapes)

    # LAYERED_ANTIPOKE pre-pass: rank this NIF's body-layer shapes innermost-
    # first (median distance to the body -- relative order is what matters, so
    # source-space verts vs the UBE body is a valid ranking proxy) and give
    # layer i an extra +i*EPSILON anti-poke floor. Mirrors the anti-poke's own
    # eligibility gates so decorative/softbody/collider shapes never rank.
    _layer_extra: "dict[str, float]" = {}
    _antipoke_stat_tree = None            # lazy shared tree for clip telemetry
    if (LAYERED_ANTIPOKE_ENABLED and body_verts_for_p2 is not None
            and (biped_slots & (BIPED_SLOT32_BIT | BIPED_SLOT49_BIT))):
        try:
            _layer_extra = _rank_body_layers(
                src_nif.shapes, body_verts_for_p2,
                body_names=set(body_names),
                reskin_skip=RESKIN_SKIP_NAMES,
                softbody_names=hdt_softbody_names,
                collider_names=hdt_collider_names,
                ube_bones=(set(ube_base_for_pass1.bone_names or [])
                           if ube_base_for_pass1 is not None else set()))
        except Exception:
            _layer_extra = {}

    # --- Pass 1: compute final verts + skin per shape ---
    for s in src_nif.shapes:
        if s.name in body_names:
            continue
        if _should_drop_shape(s.name):
            continue  # vestigial mashup leftover (e.g. MaleUnderwearBody)
        # Skip collision proxies (no textures). See M7 Fix 1 comment.
        if not (s.textures or {}):
            skipped_collision.append(s.name)
            continue
        # Gauntlet/boot shapes: warp + inflate with per-vertex extremity masking
        # to protect fingers/toes. Body-delta ops use CBBE->UBE delta which is
        # zero at hand/foot bones but noisy elsewhere, so digits need protection.
        if _shape_has_fine_animation_bones(s):
            # Full body-delta warp: conforms wrist/forearm/calf to UBE;
            # digits stay via (1-extremity_frac) weighting. Limb verts get
            # 3BA scale bones; digit verts are masked so body morphs skip them.
            hf_orig = np.asarray(s.verts, dtype=np.float64)
            hf_verts = hf_orig
            hf_verts_modified = False
            # Extremity fraction: limb gets full warp; digits stay put (no UBE digit mesh).
            hf_ef = _extremity_vert_fraction(s, len(hf_orig))

            # Body-delta warp — limb conforms to UBE shape, digits protected.
            if (cbbe_verts_for_warp_p2 is not None
                    and body_delta_for_warp_p2 is not None):
                try:
                    warped = warp_armor_by_body_delta(
                        hf_orig,
                        cbbe_verts_for_warp_p2,
                        body_delta_for_warp_p2,
                        ube_body_verts=body_verts_for_p2,
                        ube_body_normals=body_norms_for_p2,
                        min_standoff=ARMOR_TO_SKIN_BUFFER,
                    ).astype(np.float64)
                    if hf_ef is not None:
                        wf = (1.0 - hf_ef)[:, None]
                        hf_verts = hf_orig + (warped - hf_orig) * wf
                    else:
                        hf_verts = warped
                    hf_verts_modified = True
                except Exception as e:
                    failed.append((f"{s.name}:warp-hf", repr(e)))

            # Inflation with tight falloff — adds standoff against
            # body growth without puffing fingertips outward.
            if body_verts_for_p2 is not None:
                try:
                    inflated = inflate_armor_outward(
                        hf_verts, body_verts_for_p2,
                        magnitude=ARMOR_INFLATION_MAGNITUDE_HANDS_FEET,
                        close_threshold=HAND_FOOT_INFLATION_FALLOFF,
                        body_normals=body_norms_for_p2,
                    ).astype(np.float64)
                    if hf_ef is not None:
                        wf = (1.0 - hf_ef)[:, None]
                        hf_verts = hf_verts + (inflated - hf_verts) * wf
                    else:
                        hf_verts = inflated
                    hf_verts_modified = True
                except Exception as e:
                    failed.append((f"{s.name}:inflate-hf", repr(e)))

            # Scale-bone weight injection so the whole shape follows
            # body morph sliders proportionally at runtime. Reduced
            # max_transfer keeps rigid foot/hand bone animation intact
            # while letting body morph drag the shape with it.
            hf_override_skin = None
            try:
                ube_ref_for_reskin, _, _ = _cached_ube_body_verts(
                    Path(ube_body_ref_path))
                ube_base_for_reskin = next(
                    (x for x in ube_ref_for_reskin.shapes
                     if x.name == "BaseShape"), None,
                )
            except Exception:
                ube_base_for_reskin = None
            if (ube_base_for_reskin is not None
                    and (s.bone_names or [])
                    and s.name not in RESKIN_SKIP_NAMES):
                try:
                    existing_bones = list(s.bone_names)
                    existing_xforms = {}
                    existing_weights = {}
                    for bn in existing_bones:
                        pairs = (s.bone_weights.get(bn)
                                 if hasattr(s, "bone_weights") else None)
                        if pairs is None:
                            continue
                        pairs_list = (pairs.tolist()
                                      if hasattr(pairs, "tolist") else pairs)
                        existing_weights[bn] = [
                            (int(i), float(w)) for i, w in pairs_list
                        ]
                        try:
                            xf = s.get_shape_skin_to_bone(bn)
                            if xf is not None:
                                existing_xforms[bn] = xf
                        except Exception:
                            pass
                    bones2, xf2, weights2 = add_scale_bone_weights(
                        existing_bones, existing_xforms, existing_weights,
                        hf_verts,
                        ube_base_for_reskin,
                        reach=SCALE_BONE_REACH_HANDS_FEET,
                        max_transfer=SCALE_BONE_MAX_TRANSFER_HANDS_FEET,
                        exclude_vert_mask=_extremity_vert_mask(
                            s, len(hf_verts)),
                        leg_region_only=True,
                        exclude_scale_bone_substrings=(
                            _boot_far_thigh_scale_exclusions(s, biped_slots)),
                    )
                    if bones2 and weights2:
                        hf_override_skin = {
                            "bones": bones2,
                            "xforms": xf2,
                            "weights": weights2,
                        }
                except Exception:
                    hf_override_skin = None
            shape_jobs.append({
                "src": s,
                "verts": hf_verts,
                "override_skin": hf_override_skin,
                "verts_modified": hf_verts_modified,
            })
            continue
        override = None
        # Body-space offset: shapes with non-identity transforms must have
        # warp/inflate/conform computed in body space or they match the wrong
        # body region. Applied before math, removed before storage. Zero for
        # identity-transform shapes (no effect).
        _off_p2 = shape_body_offset(s)
        _sv_body = np.asarray(s.verts, dtype=np.float64) + _off_p2
        if preset_template_verts is not None and preset_user_verts is not None:
            try:
                override = bake_preset_into_armor(
                    _sv_body,
                    preset_template_verts, preset_user_verts,
                    k=4, close_threshold=5.0,
                )
            except Exception as e:
                failed.append((f"{s.name}:bake", repr(e)))
                override = None
        if override is not None and cbbe_idx is not None and ube_idx is not None:
            try:
                override = fit_armor_to_ube_body(
                    np.asarray(override, dtype=np.float64),
                    cbbe_idx, ube_idx,
                )
            except Exception as e:
                failed.append((f"{s.name}:fit", repr(e)))
        if (cbbe_verts_for_warp_p2 is not None
                and body_delta_for_warp_p2 is not None):
            # Body-delta warp + standoff buffer. See pass-2 docstring
            # in warp_armor_by_body_delta for why we need the buffer.
            try:
                base_verts = (np.asarray(override, dtype=np.float64)
                              if override is not None else _sv_body)
                override = warp_armor_by_body_delta(
                    base_verts,
                    cbbe_verts_for_warp_p2,
                    body_delta_for_warp_p2,
                    ube_body_verts=body_verts_for_p2,
                    ube_body_normals=body_norms_for_p2,
                    min_standoff=ARMOR_TO_SKIN_BUFFER,
                )
                # Slot-aware inflation to maintain standoff under body morphs.
                _infl_mag_p2 = _slot_aware_inflation_magnitude(
                    biped_slots, shape=s)
                if _infl_mag_p2 > 0 and body_verts_for_p2 is not None:
                    try:
                        _morph_amp_p2 = _cached_body_morph_amplitude(
                            _find_ube_body_osd(), body_norms_for_p2,
                            len(body_verts_for_p2))
                        override = inflate_armor_outward(
                            override, body_verts_for_p2,
                            magnitude=_infl_mag_p2,
                            close_threshold=ARMOR_INFLATION_FALLOFF_DISTANCE,
                            body_normals=body_norms_for_p2,
                            morph_amplitude=_morph_amp_p2,
                            morph_max=ADAPTIVE_CLEARANCE_MORPH_MAX,
                        )
                    except Exception:
                        pass
                # Standoff-preserving conform: reel over-projected verts back to
                # their source clearance (pull-in only, >= min clearance).
                if (src_body_v_p2 is not None and body_verts_for_p2 is not None
                        and body_norms_for_p2 is not None):
                    try:
                        override = conform_to_source_standoff(
                            _sv_body,
                            src_body_v_p2, src_body_n_p2,
                            override, body_verts_for_p2, body_norms_for_p2,
                            ube_body_nipple=body_nipple_for_p2,
                        )
                    except Exception:
                        pass
                # Groove-smooth: flatten warp-induced indent grooves on tight
                # bust cloth. Mirrors phase-1 call; near-body verts only.
                if override is not None and body_verts_for_p2 is not None:
                    try:
                        override = _smooth_warp_grooves(
                            _sv_body, np.asarray(override, dtype=np.float64),
                            body_verts_for_p2)
                    except Exception:
                        pass
            except Exception as e:
                failed.append((f"{s.name}:warp", repr(e)))
        elif body_verts_for_p2 is not None:
            # Legacy fallback: push inside-body verts outward.
            try:
                base_verts = (np.asarray(override, dtype=np.float64)
                              if override is not None else _sv_body)
                override = snap_armor_outside_body(
                    base_verts, body_verts_for_p2, body_norms_for_p2,
                )
            except Exception as e:
                failed.append((f"{s.name}:snap", repr(e)))
        # FINAL anti-poke: push body-slot armor clear of the injected body.
        # Runs LAST in body space so nothing undoes it; skips soft-body cloth
        # and HDT-SMP physics shapes (moving verts would disturb the sim).
        if (body_verts_for_p2 is not None and body_norms_for_p2 is not None
                and (biped_slots & (BIPED_SLOT32_BIT | BIPED_SLOT49_BIT))
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_softbody_names
                and s.name not in hdt_collider_names
                and not _shape_has_hdt_smp_rigging(
                    s, set(ube_base_for_pass1.bone_names or [])
                    if ube_base_for_pass1 is not None else set())):
            try:
                base_v = (np.asarray(override, dtype=np.float64)
                          if override is not None else _sv_body)
                # Morph-aware clearance: ramp standoff where the body grows at runtime.
                # None -> legacy fixed clearance.
                try:
                    _antipoke_amp = _cached_body_morph_amplitude(
                        _find_ube_body_osd(), body_norms_for_p2,
                        len(body_verts_for_p2))
                except Exception:
                    _antipoke_amp = None
                # Jiggle-overshoot headroom (default OFF): only rigid/fitted
                # cloth reaches this pass (softbody/HDT shapes are skipped
                # above), which is exactly what a bouncing body punches through.
                _antipoke_jig = None
                if JIGGLE_CLEARANCE_ENABLED:
                    try:
                        _antipoke_jig = _body_jiggle_weight(ube_base_for_pass1)
                    except Exception:
                        _antipoke_jig = None
                override = clear_armor_outside_body(
                    base_v, body_verts_for_p2, body_norms_for_p2,
                    body_nipple=body_nipple_for_p2,
                    morph_amplitude=_antipoke_amp,
                    jiggle_amplitude=_antipoke_jig,
                    req_extra=_layer_extra.get(s.name, 0.0),
                    tris=(np.asarray(s.tris, dtype=np.int64)
                          if ANTIPOKE_SMOOTH_ENABLED else None))
                # Clip-risk telemetry: verts still INSIDE the body after the
                # final pass (deep verts past max_push, or capped regions) are
                # the residual in-game clip risk. Greppable in the run log.
                try:
                    if _antipoke_stat_tree is None:
                        from scipy.spatial import cKDTree as _KD_st
                        _antipoke_stat_tree = _KD_st(body_verts_for_p2)
                    _fv = np.asarray(override, dtype=np.float64)
                    _dt, _jt = _antipoke_stat_tree.query(_fv, k=1)
                    _sgn = ((_fv - body_verts_for_p2[_jt])
                            * body_norms_for_p2[_jt]).sum(1)
                    _near = _dt < 10.0
                    _pen = int(np.sum(_near & (_sgn < -0.05)))
                    if _pen > max(4, 0.005 * len(_fv)):
                        print(f"  [clip-risk] {s.name}: {_pen} vert(s) remain "
                              f"inside the body (min {float(_sgn[_near].min()):.2f}u)"
                              f" after anti-poke")
                except Exception:
                    pass
            except Exception as e:
                failed.append((f"{s.name}:antipoke", repr(e)))
        elif (INFLATE_SOFTCLOTH and body_verts_for_p2 is not None
                and body_norms_for_p2 is not None
                and (biped_slots & (BIPED_SLOT32_BIT | BIPED_SLOT49_BIT))
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_collider_names
                and (s.name in hdt_softbody_names
                     or _shape_has_hdt_smp_rigging(
                         s, set(ube_base_for_pass1.bone_names or [])
                         if ube_base_for_pass1 is not None else set()))):
            # Soft-body / HDT-rigged cloth is skipped by the anti-poke above (moving
            # every vert disturbs the sim). The larger UBE breast/butt still punches
            # through it, so nudge ONLY those bands outward to cover, body-preserving.
            try:
                base_v = (np.asarray(override, dtype=np.float64)
                          if override is not None else _sv_body)
                override = _inflate_cloth_over_bust_butt(
                    base_v, body_verts_for_p2, body_norms_for_p2,
                    tris=np.asarray(s.tris, dtype=np.int64))
            except Exception as e:
                failed.append((f"{s.name}:softcloth", repr(e)))
        # Chain-bone cloth stays at SOURCE position so it aligns with its chain
        # bones (recreated at source bind). Per-vertex by chain-weight fraction;
        # hybrid shapes (skirt+chest) keep the chest warped.
        if override is not None:
            override = _physics_chain_nowarp_blend(s, _sv_body, override)
        # Back to local space; transform is unchanged, render identical.
        # No-op when _off_p2 is zero.
        if override is not None and _off_p2.any():
            override = np.asarray(override, dtype=np.float64) - _off_p2

        # M6 reskin (deferred to be applied via override_skin in pass 2).
        override_skin = None
        # A source BodySlide TRI drives this shape's body-SLIDER morph at runtime,
        # keyed to its ORIGINAL source skin. So a morph-TRI shape is EXCLUDED from
        # the reskin (kept on its stable source skin) -- rebuilding its skin desyncs
        # that TRI, so the armor no longer inflates to match a morphed body and the
        # body pokes out (thigh-coverage loss, all leg armor). Opt-in experiment to
        # still graft animation scale bones onto the source skin (double-morph risk):
        # CBBE2UBE_MORPHTRI_SCALE=1. Default OFF. See [DESIGN: Morph-TRI reskin].
        _is_morph_tri = s.name in src_morph_shapes
        _keep_src_skin = _MORPHTRI_SCALE and _is_morph_tri
        # Draping cloth (robe/cloak/dress...) is often bone-driven HDT-SMP that the
        # bone-fraction SMP heuristic misses; HDT-SMP CTDs on equip if UBE scale bones
        # are grafted onto it (see CLIPPING_LOG C1). Keep the no-scale-graft path for
        # the keep-src-skin branch, matching the conform passes' _CONFORM_SKIP_NAMES.
        _drape_skip = any(k in (s.name or "").lower() for k in _CONFORM_SKIP_NAMES)
        if (reskin_armor
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_softbody_names
                and s.name not in hdt_collider_names
                and s.name not in layered_cloth_names
                and not _shape_has_fine_animation_bones(s)
                and not _shape_is_head_dominant(s)
                and (_MORPHTRI_SCALE or not _is_morph_tri)):
            try:
                ube_basereshape = ube_base_for_pass1
                _body_bone_set_p2 = (
                    set(ube_basereshape.bone_names or [])
                    if ube_basereshape is not None else set()
                )
                if (ube_basereshape is not None and (s.bone_names or [])
                        and not _shape_has_hdt_smp_rigging(
                            s, _body_bone_set_p2)):
                    final_verts = (override if override is not None
                                   else np.asarray(s.verts, dtype=np.float64))
                    if _keep_src_skin:
                        # Morph-TRI shape: seed the maps from the shape's own
                        # source skin (no body-blend), so only scale bones get
                        # added below.
                        bones = list(s.bone_names)
                        xforms_map = {}
                        weights_map = {}
                        for bn in bones:
                            pairs = (s.bone_weights.get(bn)
                                     if hasattr(s, "bone_weights") else None)
                            if pairs is None:
                                continue
                            weights_map[bn] = [
                                (int(i), float(w))
                                for i, w in (pairs.tolist()
                                             if hasattr(pairs, "tolist")
                                             else pairs)
                            ]
                            try:
                                xf = s.get_shape_skin_to_bone(bn)
                                if xf is not None:
                                    xforms_map[bn] = xf
                            except Exception:
                                pass
                    else:
                        # Slot-aware conformance band (see Phase 1): body-fitted
                        # armor tracks the body over a wider shell so it deforms
                        # with the body during motion; skirts keep the narrow band.
                        # max() so an explicit caller override (reskin_*_dist) is
                        # never narrowed below the body-fitted minimum.
                        _rn_p2, _rf_p2 = _slot_aware_reskin_band(biped_slots)
                        _rn_p2 = max(_rn_p2, reskin_near_dist)
                        _rf_p2 = max(_rf_p2, reskin_far_dist)
                        bones, xforms_map, weights_map = compute_body_blend_skinning(
                            final_verts, s, ube_basereshape,
                            near_dist=_rn_p2,
                            far_dist=_rf_p2,
                            k=reskin_k,
                        )
                    # Add scale bones to the body-tracking layer so it follows
                    # body morphs + leg/butt flex. Skip exposed body-skin shapes
                    # (already at blend==1; extra scale bones over-inflate vs the
                    # real body) -- but ONLY real baked skin: BOTH geometrically
                    # coincident with the body AND a body-skin diffuse (the
                    # geometric test alone is borderline for tight leggings).
                    _n_before = len(bones)
                    if (ADD_SCALE_BONES_TO_CLOTH
                            and not (_keep_src_skin and _drape_skip)
                            and not (_is_exposed_body_skin_shape(
                                _sv_body, cbbe_verts_for_warp_p2)
                                and _shape_diffuse_is_body_skin(s))):
                        bones, xforms_map, weights_map = add_scale_bone_weights(
                            bones, xforms_map, weights_map,
                            final_verts, ube_basereshape,
                            reach=_slot_aware_scale_bone_reach(biped_slots),
                            torso_parity=bool(biped_slots & (
                                BIPED_SLOT32_BIT | BIPED_SLOT49_BIT)),
                        )
                    # For a morph-TRI shape we only override the skin when scale
                    # bones were actually grafted -- otherwise the map equals the
                    # untouched source skin, so leave override_skin=None and let
                    # the true source skin flow through unchanged.
                    if (bones and weights_map
                            and (not _keep_src_skin or len(bones) > _n_before)):
                        override_skin = {
                            "bones": bones,
                            "xforms": xforms_map,
                            "weights": weights_map,
                        }
            except Exception as e:
                failed.append((f"{s.name}:reskin-compute", repr(e)))

        shape_jobs.append({
            "src": s,
            "verts": (np.asarray(override, dtype=np.float64)
                      if override is not None
                      else np.asarray(s.verts, dtype=np.float64)),
            "override_skin": override_skin,
            "verts_modified": override is not None,
        })

    # Z-fight auto-offset: push inner-layer verts inward along body normals.
    if body_verts_for_p2 is not None and shape_jobs:
        try:
            from scipy.spatial import cKDTree
            zfight_map = {
                j["src"].name: j["verts"] for j in shape_jobs
            }
            zfight_offsets = detect_zfight_pairs(
                zfight_map, body_verts_for_p2, body_norms_for_p2,
            )
            # Convert scalar offsets to 3D deltas along nearest body normal.
            body_tree = cKDTree(body_verts_for_p2)
            for j in shape_jobs:
                name = j["src"].name
                scalar = zfight_offsets.get(name)
                if scalar is None or not np.any(scalar != 0):
                    continue
                verts = j["verts"]
                _, idx = body_tree.query(verts, k=1)
                outward = body_norms_for_p2[idx]
                j["verts"] = verts + scalar[:, None] * outward
                j["verts_modified"] = True
        except Exception as e:
            failed.append(("zfight-fix", repr(e)))

    # Cleavage depth separation: push inner-layer cloth verts behind the outer
    # layer to fix static Z-fighting visible at standstill.
    if shape_jobs and body_verts_for_p2 is not None:
        try:
            # Source body normals often zeroed in BodySlide output; compute
            # from tris. Used by chest pass and abdomen order restore.
            src_body_n_p2 = (_body_normals_or_compute(cbbe_body_shape)
                             if cbbe_body_shape is not None else None)
            n_pushed = _separate_chest_layered_cloth_depth(
                shape_jobs,
                body_verts=body_verts_for_p2,
                body_normals=body_norms_for_p2,
                source_body_verts=src_body_v_p2,
                source_body_normals=src_body_n_p2,
            )
            if n_pushed:
                import sys as _sys
                print(f"  cleavage depth: pushed {n_pushed} inner-layer "
                      f"vert(s) back for clean separation",
                      file=_sys.stderr)
            n_abdo = _separate_abdomen_layered_cloth_depth(
                shape_jobs,
                body_verts=body_verts_for_p2,
                body_normals=body_norms_for_p2,
                cbbe_body_verts=cbbe_verts_for_warp_p2,
                source_body_verts=src_body_v_p2,
                source_body_normals=src_body_n_p2,
            )
            if n_abdo:
                import sys as _sys
                print(f"  overlay-band lift: raised {n_abdo} band "
                      f"vert(s) above their under-layer", file=_sys.stderr)
        except Exception:
            pass  # best-effort

    # Cuirass inflate: push the torso/cuirass cloth out a hair from the body,
    # leaving LEG armor (greaves) untouched. Per-shape gate: skip anything named
    # "greave" or leg-bone-dominated, so the legs are never disturbed.
    if shape_jobs and CUIRASS_INFLATE > 0.0 and body_verts_for_p2 is not None:
        try:
            from scipy.spatial import cKDTree as _ckd
            _btree = _ckd(body_verts_for_p2)
            _bn = np.asarray(body_norms_for_p2)
            n_inf = 0
            for j in shape_jobs:
                if not j.get("override_skin"):
                    continue  # reskinned cloth only (excludes injected body)
                nm = (getattr(j.get("src"), "name", "") or "").lower()
                if "greave" in nm:
                    continue
                v = j.get("verts")
                if v is None or len(v) == 0:
                    continue
                v = np.asarray(v, dtype=np.float64)
                wmap = (j["override_skin"].get("weights") or {})
                # PER-VERTEX leg gate: a full torso+leg undersuit (the "pants")
                # is one shape, so gate each vert by its OWN leg-bone weight ->
                # torso verts inflate, the leg/pants portion stays put, with a
                # smooth taper between (no crease at the waist boundary).
                nv = len(v)
                legw = np.zeros(nv)
                totw = np.zeros(nv)
                for bn, pairs in wmap.items():
                    is_leg = any(k in bn for k in ("Thigh", "Calf", "Knee"))
                    for vi, w in pairs:
                        ivi = int(vi)
                        if 0 <= ivi < nv:
                            totw[ivi] += w
                            if is_leg:
                                legw[ivi] += w
                legfrac = np.where(totw > 1e-9, legw / np.maximum(totw, 1e-9), 0.0)
                factor = np.clip(1.0 - legfrac / 0.25, 0.0, 1.0)  # 1 torso -> 0 leg
                if not np.any(factor > 0.01):
                    continue
                _, idx = _btree.query(v, k=1)
                j["verts"] = (v + _bn[idx] * (CUIRASS_INFLATE * factor[:, None])
                              ).astype(np.float32)
                j["verts_modified"] = True
                n_inf += 1
            if n_inf:
                import sys as _sys
                print(f"  cuirass inflate: pushed {n_inf} torso shape(s) out "
                      f"{CUIRASS_INFLATE:.2f}u (greaves untouched)", file=_sys.stderr)
        except Exception:
            pass  # best-effort

    # Layered-cloth weight sync: gated by breast-weight fraction (genuine
    # bust layers only). Keeps bra + over-fabric moving together under
    # breast-jiggle. See _sync_chest_layered_cloth_weights.
    if shape_jobs:
        try:
            n_synced = _sync_chest_layered_cloth_weights(shape_jobs)
            if n_synced:
                import sys as _sys
                print(f"  cleavage sync: matched {n_synced} bust-layer "
                      f"vert(s) to authority weights", file=_sys.stderr)
            n_async = _sync_abdomen_layered_cloth_weights(shape_jobs)
            if n_async:
                import sys as _sys
                print(f"  waist jiggle sync: matched {n_async} inner-layer "
                      f"vert(s) to the outer layer", file=_sys.stderr)
        except Exception:
            pass  # best-effort; failure leaves shapes as-is

    # Degenerate-triangle repair (LAST vertex op): prior passes can pinch thin
    # tris flat -> black slivers. Restore collapsed tris to source-relative shape.
    # Source-degenerate folds are left alone.
    if shape_jobs:
        _n_demangle = 0
        _n_shapes_demangle = 0
        for j in shape_jobs:
            try:
                _src_shape = j["src"]
                _tris = np.asarray(_src_shape.tris, dtype=np.int64)
                _srcv = np.asarray(_src_shape.verts, dtype=np.float64)
                _curv = np.asarray(j["verts"], dtype=np.float64)
                if _tris.size == 0 or _curv.shape != _srcv.shape:
                    continue
                _fixed, _nfix = repair_collapsed_tris(_curv, _srcv, _tris)
                if _nfix:
                    j["verts"] = _fixed
                    j["verts_modified"] = True
                    _n_demangle += _nfix
                    _n_shapes_demangle += 1
            except Exception:
                pass  # best-effort; a failed repair leaves the shape as-is
        if _n_demangle:
            import sys as _sys
            print(f"  degenerate-tri repair: un-pinched {_n_demangle} collapsed "
                  f"tri(s) across {_n_shapes_demangle} shape(s)", file=_sys.stderr)

    # Cross-plate seam weld: close gaps where adjacent solid plates that share
    # a seam drifted apart under independent warp. Runs BEFORE the glow ride so
    # the glow rides the welded plate. See _weld_cross_shape_seams.
    if shape_jobs:
        try:
            n_weld = _weld_cross_shape_seams(shape_jobs)
            if n_weld:
                import sys as _sys
                print(f"  seam weld: closed {n_weld} cross-plate seam vert(s)",
                      file=_sys.stderr)
        except Exception:
            pass  # best-effort; failure leaves seams as-is

    # Effect-shader decal overlays (Daedric red glow etc.) must RIDE their
    # underlying plate, not be warped independently -- else the thin source
    # offset amplifies through the body-fit and the glow clips through the
    # plate. Runs LAST (after every vertex pass) so the glow rides the plate's
    # FINAL position. See _ride_effect_overlays_on_plate.
    if shape_jobs:
        try:
            n_ride = _ride_effect_overlays_on_plate(shape_jobs)
            if n_ride:
                import sys as _sys
                print(f"  glow overlay ride: re-bound {n_ride} effect-overlay "
                      f"vert(s) to their plate", file=_sys.stderr)
        except Exception:
            pass  # best-effort; failure leaves overlays as-is

    # Pass 2: copy shapes. Alpha preserved — bit-19 (set by _reset_morph_flags)
    # enables NioOverride morphs on alpha cloth without stripping transparency.
    first_armor_shape = None
    for j in shape_jobs:
        s = j["src"]
        override_v = (j["verts"].astype(np.float32)
                      if j["verts_modified"] else None)
        try:
            new_armor = _copy_shape(
                s, dst_nif,
                override_verts=override_v,
                override_skin=j["override_skin"],
            )
            copied.append(s.name)
            if first_armor_shape is None:
                first_armor_shape = new_armor
        except Exception as e:
            # Copy failed -> the shape is absent from the output (invisible
            # piece). Tag DROPPED so it's reported as a partial conversion.
            failed.append((s.name, f"DROPPED (copy failed): {e!r}"))
            continue

    # Attach BODYTRI via _pick_bodytri_carriers: single carrier for slot-49/no-body
    # NIFs, multi-carrier for slot-32+BaseShape NIFs so NioOverride morphs all shapes.
    # Rigid single-bone pieces follow morphs via M6 re-skin (standard skinning).
    # Falls back to first_armor_shape if the filter returns empty.
    _bodytri_err = None
    carriers_p2 = _pick_bodytri_carriers(dst_nif)
    if not carriers_p2 and first_armor_shape is not None:
        carriers_p2 = [first_armor_shape]
    if carriers_p2:
        try:
            from pyn.pynifly import NiStringExtraData  # type: ignore
            # Apply morph-readiness cleanup to EVERY cloth shape, not just the
            # carrier. NioOverride skips shapes with Shader_Type=1 or wrong flags
            # even when they're listed in the TRI.
            cloth_shapes_to_clean = list(_pick_bodytri_carriers(dst_nif))
            carrier_names = {c.name for c in carriers_p2}
            for s in dst_nif.shapes:
                nlow = s.name.lower()
                if s.name in carrier_names:
                    continue
                if not (s.textures or {}):
                    continue
                if s.name in UBE_BODY_INJECT_NAMES:
                    continue
                if any(kw in nlow for kw in NON_CLOTH_SHAPE_KEYWORDS):
                    continue
                cloth_shapes_to_clean.append(s)
            # SMP per-triangle COLLIDER shapes keep their authored partitions:
            # collapsing them desyncs FSMP's collision build -> equip CTD (`Greaves`
            # 32+38 -> 32). Here in phase-2 the shapes are in-memory (partition_tris
            # not materialized) so the collapse usually no-ops and the on-disk pass
            # does the real work -- but skip colliders anyway, belt-and-suspenders to
            # match phase-1 (where the reloaded-from-disk NIF makes it bite).
            _coll_names_p2 = _hdt_collider_shape_names(src_path)
            for s in cloth_shapes_to_clean:
                _reset_morph_flags(s)
                _normalize_shader_for_morph(s)
                if s.name not in _coll_names_p2:
                    _normalize_partitions(s)

            # BODYTRI goes on carriers only.
            for target in carriers_p2:
                already = False
                for ed in target.extra_data():
                    if hasattr(ed, "string_data") and ed.name == "BODYTRI":
                        already = True; break
                if not already:
                    NiStringExtraData.New(
                        dst_nif,
                        name="BODYTRI",
                        string_value=body_tri_path,
                        parent=target,
                    )
        except Exception as _e:
            # Surface a swallowed BODYTRI injection: failure here = armor doesn't
            # follow body morphs (static on every preset), otherwise silent.
            _bodytri_err = _e

    # Attach HDT-SMP physics config reference on the root node, matching
    # the source mod's XML. The XML defines bones (CBBE-style AND custom
    # like physics-chain bones (prefix_NN)) and physics constraints between them — so when
    # body morphs/animates via UBE bones, the constraints propagate to
    # the CBBE bones our fabric is skinned to, keeping the fabric
    # attached to the morphed body. Hand-authored UBE conversions ship
    # this same extra-data on root.
    hdt_injected = False     # True ONLY after the source XML ref is attached
    _hdt_inject_err = None    # set if a FOUND source XML failed to attach
    try:
        hdt_xml_path = _find_hdt_xml_for_armor(src_path)
        # If source XML references chain bones we stripped, clear it so the
        # post-save generator builds a fresh soft-body XML on standard bones.
        if hdt_xml_path is not None:
            try:
                _dstbones: set[str] = set()
                for _s in dst_nif.shapes:
                    _dstbones |= set(_s.bone_names or [])
                if _source_hdt_needs_missing_chain_bones(src_path, _dstbones):
                    hdt_xml_path = None
            except Exception:
                pass
        # Phase 2 needs the dst_nif saved to disk before we can read
        # back cloth shapes for XML generation, but at this point in
        # phase 2 we haven't called `dst_nif.save()` yet (it happens
        # right after this block). So defer the auto-gen to a second
        # injection pass after save. We just remember the intent.
        # See the post-save block below.
        if hdt_xml_path:
            from pyn.pynifly import NiStringExtraData  # type: ignore
            NiStringExtraData.New(
                dst_nif,
                name="HDT Skinned Mesh Physics Object",
                string_value=hdt_xml_path,
                parent=dst_nif.rootNode,
            )
            hdt_injected = True
    except Exception as _e:
        # A FOUND source XML that fails to attach must NOT suppress the regen
        # fallback below: the gate keys on hdt_injected (did we attach a ref?),
        # not on hdt_xml_path (did we find one?). Capturing the error also lets
        # us surface it -- otherwise the piece ships with no physics reference
        # and no signal at all.
        _hdt_inject_err = _e

    if failed:
        # Surface failures via the reason field — auto_convert's CLI
        # already shows reason in the report, and load-check would catch
        # the missing-shape case anyway, but explicit beats silent.
        result_reason = f"errors during shape copy: {failed}"
    elif skipped_collision:
        result_reason = (f"skipped {len(skipped_collision)} textureless "
                         f"collision shape(s): {skipped_collision}")
    else:
        result_reason = ""
    if _hdt_inject_err is not None:
        result_reason = (result_reason + "; " if result_reason else "") \
            + f"source HDT inject failed ({_hdt_inject_err!r}); fell back to regen"
    if _bodytri_err is not None:
        result_reason = (result_reason + "; " if result_reason else "") \
            + f"BODYTRI injection failed ({_bodytri_err!r}); armor may not morph"

    atomic_nif_save(dst_nif, dst_path)

    # M8 auto-TRI generation. Propagates UBE body OSD deltas to armor verts
    # via K-NN IDW. Only runs when no user-built BodySlide TRI was found.
    # Makes armors without a published UBE sliderset follow body morphs at
    # runtime without a manual BodySlide step.
    if auto_tri_dst is not None:
        try:
            ube_osd_path = _find_ube_body_osd()
            if ube_osd_path is not None:
                from .sliderset_gen import generate_armor_tri
                body_osd = _cached_osd_load(ube_osd_path)
                # Reload our just-saved NIF to gather the final armor verts
                # (post inflate / bake / reskin). Use the dst NIF to be
                # sure we propagate to what the player actually sees.
                pyn = _pynifly()
                dst_check = pyn.NifFile(filepath=str(dst_path))
                ube_basereshape = next(
                    (x for x in dst_check.shapes if x.name == "BaseShape"),
                    None,
                )
                if ube_basereshape is not None:
                    body_verts_arr = np.asarray(
                        ube_basereshape.verts, dtype=np.float64)
                    armor_shape_verts: dict[str, np.ndarray] = {}
                    body_in_dst: set[str] = set()
                    # Per-vert extremity fractions; see phase-1 #147 note.
                    armor_vert_ef: dict[str, np.ndarray] = {}
                    for s in dst_check.shapes:
                        if s.name in UBE_BODY_INJECT_NAMES:
                            body_in_dst.add(s.name)
                            continue
                        # Include all shapes; per-vert extremity-frac dampening
                        # inside generate_armor_tri protects digits. Body-space
                        # offset applied so KNN matches the right body region.
                        armor_shape_verts[s.name] = (
                            np.asarray(s.verts, dtype=np.float64)
                            + shape_body_offset(s))
                        ef = _extremity_vert_fraction(s, len(s.verts))
                        if ef is not None and ef.size:
                            armor_vert_ef[s.name] = ef
                    # Carrier-first TRI (hand-authored UBE convention).
                    p2_carriers = _pick_bodytri_carriers(dst_check)
                    p2_carrier_name = p2_carriers[0].name if p2_carriers else None
                    # Include BaseShape so one BODYTRI delivers both cloth + body morphs.
                    tri = generate_armor_tri(
                        armor_shape_verts,
                        body_verts_arr,
                        body_osd,
                        body_shape_name="BaseShape",
                        include_body_shapes=body_in_dst,
                        carrier_shape_name=p2_carrier_name,
                        armor_vert_extremity_fractions=armor_vert_ef,
                    )
                    atomic_tri_save(tri, auto_tri_dst)
        except Exception as e:
            # Non-fatal — armor still works without morphs.
            result_reason = (result_reason + "; " if result_reason else "") \
                + f"auto-TRI generation failed: {e!r}"

    # Phase 2 HDT-SMP XML auto-gen. The phase 2 path defers HDT XML
    # injection until after the dst NIF is saved (cloth shapes need
    # to be enumerated from the dst NIF, which doesn't exist on disk
    # until the save above). Skip auto-gen only if the block above
    # actually ATTACHED a source HDT reference (we prefer hand-authored);
    # if it found one but failed to attach it, fall through and regen.
    if not hdt_injected:
        try:
            generated_xml_path = _generate_hdt_xml_for_dst(dst_path)
            if generated_xml_path:
                # Re-open the NIF, add the extra-data, save again.
                pyn = _pynifly()
                nf_for_inject = pyn.NifFile(filepath=str(dst_path))
                already = False
                for ed in nf_for_inject.rootNode.extra_data():
                    if (hasattr(ed, "string_data")
                            and ed.name == "HDT Skinned Mesh Physics Object"):
                        already = True
                        break
                if not already:
                    from pyn.pynifly import NiStringExtraData  # type: ignore
                    NiStringExtraData.New(
                        nf_for_inject,
                        name="HDT Skinned Mesh Physics Object",
                        string_value=generated_xml_path,
                        parent=nf_for_inject.rootNode,
                    )
                    atomic_nif_save(nf_for_inject, dst_path)
        except Exception as e:
            result_reason = (result_reason + "; " if result_reason else "") \
                + f"HDT XML gen failed: {e!r}"

    # Multi-partition collapse (post all re-saves so extra-data isn't clobbered).
    _normalize_partitions_on_disk(dst_path, src_path)

    # FINAL HDT-SMP physics pass — runs LAST so the extra-data survives
    # (earlier round-trips dropped it). Prefers the source armor's
    # authored XML. See _finalize_hdt_physics.
    try:
        _finalize_hdt_physics(dst_path, src_path)
    except Exception:
        pass

    # Graft body jiggle onto fitted leg cloth that lacks its own, THEN conform --
    # the graft lets the jiggle-gated conform ALSO weight-match these pants to the
    # body, fixing the knee-BEND clip (not just butt-jiggle follow). Order matters.
    try:
        _transfer_body_jiggle_to_fitted(dst_path, biped_slots)
    except Exception:
        pass
    # Fitted-cloth body conform (gated; skin-tight garments only).
    try:
        _conform_fitted_to_body(dst_path, biped_slots)
    except Exception:
        pass
    # Knee-bend conform for RIGID leg plate (the conform above skips it): match the
    # plate's Thigh:Calf split to the body so it bends with the knee (Orcish #knee).
    try:
        _match_rigid_leg_bend_to_body(dst_path, biped_slots)
    except Exception:
        pass

    # Validation pass — catches subtle skinning / TRI mismatches.
    val_warnings = validate_dst_nif(
        dst_path, tri_path=auto_tri_dst if auto_tri_dst else None,
        src_path=src_path,
    )
    if val_warnings:
        joined = "; ".join(val_warnings)
        result_reason = (result_reason + "; " if result_reason else "") + joined

    return ConvertResult(
        src_path=src_path, dst_path=dst_path,
        status="converted (body-swap)",
        reason=result_reason,
        body_shapes=body_names,
        armor_shapes=copied,
        shape_locations={n: None for n in (injected + copied + [f for f,_ in failed])},
        dropped_shapes=[n for (n, msg) in failed if msg.startswith("DROPPED")],
    )
