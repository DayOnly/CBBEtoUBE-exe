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

M3 phase 1 scope: produce a UBE-compatible NIF for armor-piece-only files
(files that don't contain inline body shapes like 3BA / 3BA_Anus /
3BA_Vagina). Inline-body files are detected and SKIPPED for now — those
need shape-removal + UBE-body injection, which is the M3 phase 2 problem
(pynifly has no delete-block API).

Surprising finding from M3 measurement: for armor pieces that don't
contain inline body, the right transformation is **identity** (just copy
the NIF). Empirically, across every a hand-authored UBE armor piece tested, the
CBBE-authored verts are already what the UBE-built mesh has. Position-
warping based on CBBE-body -> UBE-body deformation introduces more error
than it fixes, because the conversion via BodySlide doesn't actually warp
armor verts — it swaps inline body shapes and may update skin instances
for breast-region pieces, but armor verts come straight from the slider-
zero shapedata regardless of CBBE vs UBE target.

So this module:
  * `convert_nif()` defaults to a verbatim file copy
  * `warp_armor=True` enables the experimental position-warp (kept for
    diagnostics — it loses on every measured piece, but the code lives
    here in case future cases benefit)

The position-warp path reuses `correspondence.compute_deformation` and
`nif_patch.patch_nif_shapes` for the actual binary patching.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import nif_io, nif_patch
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
# After body-delta warp, no armor vert is allowed to live closer than
# this (in world units) to the UBE body surface. Bumping it up adds
# more clearance — useful when skin pokes through revealing armor
# (the previous default 0.3u was tuned to "barely visible"; users with
# very large UBE morphs or sheer cloth may need 0.5–0.7u). Bumping it
# down lets armor sit flush against the body — risk of z-fighting
# shimmer when the body morphs under sliders.
#
# Reconvert any affected mods after changing this (the buffer is baked
# into the output NIFs at convert time, not applied at runtime).
ARMOR_TO_SKIN_BUFFER = 0.15  # global tighten 2026-05-29: armor sat too far
                              # off the body; halved the static standoff floor
                              # to pull every piece closer. Raise toward 0.3 if
                              # body poke-through reappears on big morphs.
# Briefly bumped 0.3 -> 0.8 to address a mashup armor loincloth
# clipping into the body at base position. Reverted because
# in-game the skirt then read as ballooned/detached. Smaller
# buffer + a per-shape inflation strategy is the correct fix
# if we revisit this.


# ---- Inflation pass: BodySlide-style safety puff-out ------------------
# Hand-authored BodySlide-built UBE armor includes a per-vert outward
# inflation that pushes cloth away from body skin during build time —
# typically 0.3-0.5u of extra standoff. Our body-delta warp preserves
# the SOURCE drape exactly (mathematically correct) but matches the
# CBBE source distance rather than the puffed-out UBE convention.
#
# Result: revealing armor (a revealing slot-32 top, lingerie, bikinis) has the
# minimum drape from source — when the user adjusts body morph
# sliders at runtime, the body can grow past the cloth surface and
# nipples/skin punch through.
#
# This constant adds a uniform-ish outward inflation after the warp,
# with linear falloff so verts already far from body don't get
# bulged unnecessarily. 0 = disable. 0.3-0.5u matches BodySlide's
# build-time convention.
#
# 2026-05-28: bumped 0.8 -> 1.0 as a conservative first pass against
# breast/butt poke-through (#129/#135) on large presets — adds ~0.2u
# more standoff to body cloth within the falloff radius. Kept small to
# avoid over-puffing armors that fit fine. Clipping is iterative: if the
# chest still pokes through, escalate to a breast/butt-region-targeted
# standoff rather than bumping this global value further (which risks
# ballooning tight cloth elsewhere). Knee clipping on tight leg armor is
# largely the inherent CBBE-leg-flex-on-UBE issue (see M3.5 finding) and
# inflation only helps the morph-growth component, not the joint flex.
ARMOR_INFLATION_MAGNITUDE = 0.7  # global tighten 2026-05-29 (was 1.0)
ARMOR_INFLATION_FALLOFF_DISTANCE = 3.0

# Slot-49 (pelvis primary — skirts, loincloths, hip cloth, tassets)
# routinely sits closer to body skin than slot-32 body armor and clips
# more aggressively under bigger UBE morphs and dynamic animation. When
# the caller supplies biped_slots and bit 19 (slot 49) is set, we use
# this boosted inflation magnitude instead of the default.
#
# Falloff stays the same so verts already well away from the body
# (far-hanging tabard tails, cape edges) aren't over-puffed.
#
# History: 1.5 -> 0.8 (2026-05-29). After the PIRT shape-count fix (see
# #139 / tri.py), slot-49 cloth shapes past TRI position 9 now actually
# morph in-game, so the inflation no longer needs to carry the entire
# fit on its own. Drop to a tighter buffer that still clears the body
# under morph + animation without making skirts look tent-like. Bump
# back up if loincloths/tassets visibly clip on big presets.
ARMOR_INFLATION_MAGNITUDE_SLOT49 = 0.5  # global tighten 2026-05-29 (was 0.8)
BIPED_SLOT49_BIT = 1 << 19  # 0x00080000

# Slot 33 (hands) and slot 37 (feet) — gauntlets and boots cling tight
# to wrist/forearm and calf/foot. They use the user's standard armor
# slots so they share the convert path with body cloth, but their
# shapes typically have less per-slider morph response (vert weights
# dominated by rigid bones like Calf/Foot). Result: body grows under
# slider changes faster than the boot/gauntlet shell, so the limb
# pokes through. Boosting their inflation gives a fixed buffer that
# stays clear of growing body geometry across the slider range.
# Gauntlets/boots: 1.5 -> 0.5 -> 0.3 (hug tight) -> 0.8 (2026-05-28, user
# asked to "increase the distance for gauntlets and boots" because the
# shell still clipped into the forearm/calf under morph + animation). The
# extremity-fraction falloff (see _extremity_vert_fraction) already shields
# finger/toe verts from this push, so raising the magnitude only stands the
# LIMB shell (forearm/calf cuff) further off the body — exactly the region
# that was clipping — without ballooning the fingers/toes. Tunable: raise
# further if boots/gauntlets still clip, lower if they look puffy.
ARMOR_INFLATION_MAGNITUDE_HANDS_FEET = 0.6  # global tighten 2026-05-29 (was 0.8)
BIPED_SLOT32_BIT = 1 << 2  # 0x00000004 — body (torso cuirass)
BIPED_SLOT33_BIT = 1 << 3  # 0x00000008 — hands
BIPED_SLOT37_BIT = 1 << 7  # 0x00000080 — feet

# Skirts / hanging hip cloth need MORE standoff than torso cloth — they
# drape close over the thighs/butt and clip through under morph +
# walk/run animation. Applied by NAME (shape or diffuse-texture keyword)
# rather than biped slot, because skirt layers frequently live INSIDE a
# slot-32 cuirass NIF (e.g. a mashup armor's Tasset/skirt-layer layers) and so
# never trip the slot-49 path.
#
# Was 2.5 while the skirt-folding bug was unsolved — we over-puffed to
# mask the creasing. Now that the fold is fixed at its real source (the
# continuous adaptive-IDW morph propagation in sliderset_gen, which
# removed the zone-boundary delta discontinuities), the big standoff is
# no longer load-bearing, so shrink it back toward the body for a closer,
# less tent-like drape.
#
# History: 2.5 -> 1.8 -> 1.0 (2026-05-29). After the PIRT shape-count
# fix (#139), skirts past TRI position 9 morph in-game like everything
# else, so the static inflation no longer has to fight for clearance on
# its own. 1.0 matches the slot-32 default — skirts now rely on the
# morph + scale-bone tracking for the dynamic portion of the fit.
# Bump back up if visible thigh/butt clipping returns on big presets.
ARMOR_INFLATION_MAGNITUDE_SKIRT = 0.7  # global tighten 2026-05-29 (was 1.0)
SKIRT_INFLATION_KEYWORDS = (
    "skirt", "tasset", "loincloth", "apron", "kilt", "aketon",
)

# Decorative WAIST BELT / SASH overlay pieces sit ON TOP of the waist garment
# (corset/top fabric), so they must stand off the body MORE than that garment
# or they Z-fight behind it and read as "merged into the body / not visible"
# (DDV Ruby Flower: `belts`/`belts_metal` warped to ~+0.55u, the same shell as
# the `top` at +0.62u -> the belt vanished behind the fabric). The underlying
# garment gets the default ~0.7 inflation (~+0.6u net), so the belt needs a
# clearly higher magnitude to ride on top. Detected by NAME / diffuse keyword
# (these pieces frequently live INSIDE a slot-32 cuirass NIF, so a slot check
# misses them). Draping sash tails far from the body are unaffected — the
# inflation falloff zeroes the push beyond ARMOR_INFLATION_FALLOFF_DISTANCE.
ARMOR_INFLATION_MAGNITUDE_BELT = 1.5
BELT_OVERLAY_KEYWORDS = (
    "belt", "sash", "girdle", "buckle", "obi", "waistband", "waistcloth",
)

# ---- Nipple-aware bust clearance (#175 nipple poke-through) -----------------
# The bust pass in conform_to_source_standoff USED to shove the entire chest
# Z-band out to a fixed clearance (`bust_clearance`) measured against only the
# NEAREST body vert. That was simultaneously:
#   * too LOOSE on flat chest panels (a sternum / side-of-cuirass plate got the
#     same standoff as the nipple -> "armor doesn't fit close"), and
#   * sometimes too WEAK right at the nipple tip, because the nipple is rarely
#     the nearest body point to the fabric vert sitting over it -> it could
#     still poke under a big preset / live RaceMenu nipple slider.
# (And, if the body NIF shipped no vertex normals -- BodySlide outputs often
#  don't -- the WHOLE pass was a silent no-op: see _body_normals_or_compute.)
#
# The replacement is nipple-aware (keyed on the body's own Breast03 tip-bone
# weight, a clean signal that localizes the breast front / nipple) and
# neighbourhood-based:
#   * BUST_FLAT_CLEARANCE -- the small standoff a FLAT chest panel keeps. Close
#     fit; just clear of Z-fighting. (Scale-bone parity tracking carries the
#     dynamic growth when the user pushes breast sliders, so the STATIC buffer
#     can be small.) Cloth already tighter than this is NOT loosened.
#   * the required clearance ramps from BUST_FLAT_CLEARANCE up to `bust_clearance`
#     by BUST_NIPPLE_GAIN x (nearest body vert's nipple weight), so ONLY the
#     breast-front / nipple region gets the full standoff -- the sternum, sides
#     and upper chest stay close.
#   * the clearance is enforced over the WORST (closest) body vert within
#     BUST_NEIGHBORHOOD_RADIUS (BUST_NEIGHBORHOOD_K neighbours) of the fabric
#     vert, not just the nearest one -> a nipple tip that pokes past the fabric
#     is caught even when a flatter body vert is closer.
# Push-OUT-biased in the bust (it only pulls a bust vert IN as far as the general
# conform already wanted, and never past the clearing floor), so it cannot create
# new clipping. Lower BUST_FLAT_CLEARANCE for a tighter chest; raise
# bust_clearance / BUST_NIPPLE_GAIN if a nipple still pokes on a big preset.
BUST_FLAT_CLEARANCE = 0.3
BUST_NIPPLE_GAIN = 1.0
BUST_NEIGHBORHOOD_K = 6
BUST_NEIGHBORHOOD_RADIUS = 4.0
# Body breast-TIP bone keywords -> "nipple weight" used to localize where the
# bust standoff is spent. Breast03 is the apex/tip bone (peaks right at the
# nipple); Breast02 contributes partially. Matched on the bone name with spaces
# stripped (so "R Breast03" / "NPC L Breast03" both hit).
NIPPLE_TIP_BONE_WEIGHTS = {"breast03": 1.0, "nipple": 1.0, "breast02": 0.4}

# ---- Final anti-poke pass (#175 nipple/belly/thigh poke-through) ------------
# After warp/inflate/conform, body-slot armor can still sit only ~0.02u off the
# body (measured: a vanilla guard cuirass at 0.02u over the nipple), because the
# source-correspondence conform is gated/silently-skipped on some shapes. The
# actor's live morph then punches straight through. clear_armor_outside_body()
# is a FINAL, robust push-OUT pass measured against the (injected) UBE body --
# which is always present and has valid normals -- so it ALWAYS lands, and it
# runs last so no later pass undoes it. Flat panels keep ANTIPOKE_FLAT_CLEAR
# (close fit); the breast front ramps to ANTIPOKE_BUST_CLEAR by the body's
# Breast03 nipple weight, enforced over the WORST nearby body vert. Tunable:
# raise ANTIPOKE_FLAT_CLEAR if the body still pokes on big presets (a touch
# looser everywhere), lower for a tighter fit.
# Measured on the real vanilla guard cuirass: 0.8 clears the 0.02u nipple to
# ~0.6 AND lifts belly/thigh from ~0.67/0.78 to ~0.80 (a buffer for the morph),
# moving only ~7% of verts (max 0.78u -- no ballooning). 1.0 over-pushes (14%).
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
# The inflation pass exists to keep clearance between armor and the body so the
# body doesn't poke through when the player morphs it BIGGER at runtime (the
# armor follows the morph via scale bones, but imperfectly). A UNIFORM inflation
# floats armor ~0.7-1u off the skin EVERYWHERE — including the ~quarter of the
# body that barely moves under any slider (shoulders, back, upper arms, calves).
# Instead, scale clearance by how far each body region can actually grow
# OUTWARD: tight where the body is static, full clearance only in the
# breast/butt/belly/hip zones. Provably <= the uniform value everywhere, and it
# only tightens STATIC regions (low clip risk), so it cannot add poke-through.
ADAPTIVE_CLEARANCE_ENABLED = True
ADAPTIVE_CLEARANCE_BASE = 0.25       # minimum clearance (z-fight floor) in static zones
ADAPTIVE_CLEARANCE_MORPH_FACTOR = 0.20  # clearance added per unit of outward body morph
# Morph-zone clearance CAP. Decoupled from the slot inflation magnitude so the
# breast/butt/belly can carry MORE clearance than a flat cuirass needs — that's
# where the body morphs OUTWARD at runtime and pokes through a too-tight cuirass
# (the guard-cuirass "nipple/stomach clip"). The scale bones make the armor
# follow ~85% of the morph, so the residual that clearance must absorb is ~15%
# of the local outward morph; FACTOR=0.20 + this cap covers realistic
# breast/belly slider ranges. Static zones are unaffected (they sit at BASE,
# far below this cap). Set == BASE to disable the morph-zone expansion.
ADAPTIVE_CLEARANCE_MORPH_MAX = 1.1
# Size/shape sliders the player actually drives (clip risk). Substrings, lowercased.
_MORPH_SIZE_KEYWORDS = (
    "breast", "butt", "belly", "cleav", "nipple", "hip", "thigh", "waist",
    "big", "pregn", "chub", "wide", "tummy", "gut", "ass", "pelvis",
    # "glute" = the anatomical butt: the UBE/3BA OSD names the BUTT size sliders
    # GluteSize/GluteSpread/GluteHeight/GluteLower/... (~25 of them) which the
    # "butt"/"ass" keywords MISS, so the buttock morph zone was under-detected
    # (too little adaptive clip-clearance over the butt). "trochanter" = the hip
    # bone slider. Both are genuine outward-volume zones (safe to include).
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
        nf = _pynifly().NifFile(filepath=str(nif_path))
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

    PREFER the genuine UBE-topology body output: `!UBE\\Body\\femalebody_
    tangent{w}.nif` (~29,298-vert UBE BaseShape — the user's BodySlide-built
    UBE body). That is the ACTUAL UBE shape.

    Why this matters (root cause of "vanilla armor stays CBBE-shaped",
    2026-05-29): a BodySlide-output mod ALSO ships a same-named CBBE/3BA body
    at `actors\\character\\character assets\\femalebody{w}.nif` (18,436-vert
    3BA topology). The OLD logic, which scanned for an 18,436-vert body in a
    bodyslide-output / UBE-named mod, picked THAT — but it's the user's nude
    3BA body, NOT a UBE body. The CBBE->UBE delta then came out as
    3BA - 3BA == ZERO (verified byte-identical on a live modlist), so the
    warp never reshaped any armor toward UBE; armor only got pushed OUTSIDE
    the UBE body by the snap/inflate passes while keeping CBBE proportions.
    Rigid armor (vanilla cuirasses), which relies entirely on the warp,
    therefore stayed fully CBBE-shaped.

    The 29,298-vert UBE body has a DIFFERENT topology from the 18,436-vert
    CBBE base, so `_cached_cbbe_to_ube_delta` builds the deformation field by
    nearest-neighbor correspondence (it no longer requires equal vert counts).

    Fallback (legacy): a UBE-named / bodyslide-output 18,436-vert body, used
    only if no `!UBE\\Body` output exists. Env override: CBBE2UBE_UBE_BODY_0/_1.
    """
    ck = f"ube{weight}"
    if ck in _BODY_DISCOVERY_CACHE:
        return _BODY_DISCOVERY_CACHE[ck]
    env = os.environ.get(f"CBBE2UBE_UBE_BODY{weight.upper()}")
    if env and Path(env).is_file():
        _BODY_DISCOVERY_CACHE[ck] = Path(env)
        return Path(env)
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
# A shape stores its verts in SKIN space and carries a global_to_skin transform
# (world -> skin) so the engine can position it. Most armor has an IDENTITY
# global_to_skin (skin == world). But some shapes (e.g. the Ebony cuirass, whose
# g2s is a -64.7u Z translation) store verts far from world position. The fit
# pipeline compares armor verts against the body in WORLD space, so for an offset
# shape every vert matches the WRONG body anatomy (the cuirass shoulder matched
# the body's hip -> the warp sheared the collar, the "ebony breaking at the
# shoulders" bug). Fix: run the fit in WORLD frame (transform verts skin->world),
# then transform the result world->skin for output. Identity-g2s shapes are a
# no-op (byte-identical to before).

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
# A SKINNED mesh must carry an IDENTITY NiAVObject (geometry) transform -- the
# engine ignores that transform for skinned geometry and positions verts purely
# from the skin (bones + skin-to-bone). Some source meshes were authored with a
# SCALE (and/or rotation) baked into the geometry transform instead of the verts
# -- e.g. Vigilant "Shaokhan" armor at scale 0.0729 (verts ~13.7x too large -> the
# converted body rendered flung to z~1480, off-screen = "invisible/static"), or
# "Pelinal" arms at 6.86 (verts ~6.9x too small -> collapsed). The converter
# previously copied that transform verbatim (createShapeFromData carries it via
# `props`), so the defect survived conversion. Fix: bake the full transform into
# the verts/normals, adjust each skin-to-bone by its inverse so the BIND is
# preserved exactly, and emit an IDENTITY transform. NO-OP for the identity
# transforms normal armor ships with (so zero risk to working conversions).
# Math validated on the real meshes (bind invariant STB'@(T@v) == STB@v). #scalebake


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

    ROOT CAUSE (measured 2026-06-06, ebony/elven cuirass breast+sleeve "break"):
    the M6 reskin grafts the UBE body's BODY-SPACE skin-to-bone transforms for
    the 3BA scale/morph bones (Breast01-03, Belly, Butt, FrontThigh, RearThigh,
    RearCalf, Upperarm/ForearmTwist) onto armor whose verts were stored in the
    source's SHAPE space (g2s-shifted ~60-65 below the body). The render engine
    IGNORES the shape-level global_to_skin for skinned UBE armor, so every
    scale-bone-weighted region (breast, butt, belly, sleeves) skins ~60 below
    its bone -> sag/collapse. The PRIMARY bones (Spine/Pelvis/Thigh/Calf) keep
    source-consistent STBs so they render fine -> only the soft-body zones break.

    Per bone: if its weighted verts sit far from it (mean |STB @ vert| > min_off)
    AND baking g2s^-1 into that bone's STB pulls them back (reduces the mean
    distance by > min_gain), bake it. SELF-VERIFYING: only touches genuinely
    mismatched bones; a consistent bone where g2s would make things worse (e.g.
    Spine2: |11|->|75|) is left untouched. Returns (new_xforms_map, baked_any).
    When baked_any, the caller leaves global_to_skin at identity -- the
    correction now lives in the per-bone STBs, matching how correctly-authored
    armor (e.g. the working Ebony Mail) ships (identity g2s, verts hug bones).
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
    guard-armor reskins (qwib Solitude/Markarth/etc. trim shapes: 18-19% of a
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
                  weights_map, use_verts, bake_T):
    """Install the skin onto a freshly-created shape: bones, skin-to-bone xforms,
    global-to-skin, per-bone weights, and partitions. Shared by both _copy_shape
    skin paths (the M6 override-skin reskin and the verbatim source copy).

    add_bone order matters (pynifly: add ALL bones first, THEN set transforms +
    weights, else they default to identity@origin -> spikes). Applies the
    #breast-stb g2s-align, the #wolf-greaves genital strip, and the #zeroweight
    fill. Caller builds/caps `bone_names`/`xforms_map`/`weights_map` first.
    """
    new_shape.skin()
    # Preserve armor physics-bone chains (skirt/cape/tail) BEFORE add_bone, so
    # their nodes keep source transforms+parents instead of collapsing to a flat
    # identity node at the origin.
    try:
        _precreate_custom_bone_chains(dst_nif, src_shape.file, bone_names)
    except Exception:
        pass
    for bn in bone_names:
        new_shape.add_bone(bn)
    # Fix the M6 reskin scale-bone space mismatch (breast/butt/belly/sleeve sag
    # ~60 below body): bake g2s^-1 into mismatched scale-bone STBs, leave g2s
    # identity. See _align_scale_bone_stbs_to_verts. #breast-stb
    g2s_aligned = False
    if bake_T is None and src_shape.has_global_to_skin:
        xforms_map, g2s_aligned = _align_scale_bone_stbs_to_verts(
            xforms_map, src_shape.global_to_skin, use_verts, weights_map)
    for bn in bone_names:
        xf = xforms_map.get(bn)
        if xf is not None:
            if bake_T is not None:
                xf = _adjust_skin_to_bone_baked(xf, bake_T)
            new_shape.set_skin_to_bone_xform(bn, xf)
    if src_shape.has_global_to_skin:
        # ALWAYS preserve the source global_to_skin -- even when the #breast-stb
        # STB-bake ran (g2s_aligned). The engine IGNORES g2s for skinned RENDER
        # (the bake fixes render via the per-bone STBs), but it DOES use g2s to
        # position the shape's bounding sphere in world. The bound is computed
        # from the stored verts, which for an offset-g2s shape stay in SHAPE space
        # (elven cuirass top: mean z ~ -18; source g2s lifts the bound +120 to the
        # torso). Leaving g2s identity left the cull bound ~120u BELOW the rendered
        # geometry, so the shape -- and the body it covers -- frustum-culls when
        # the camera zooms in close ("top/armored body invisible up close").
        # Restoring the source g2s fixes the bound and does NOT move render.
        # #breast-stb-bound
        new_shape.set_global_to_skin(src_shape.global_to_skin)
    # Genital-anatomy weights resolve to the origin on UBE actors (floor spike) ->
    # strip them; then fill any now-zero-weight verts so nothing spikes to (0,0,0).
    # #wolf-greaves #zeroweight
    weights_map = _strip_genital_weights_map(weights_map)
    # Soft-body jiggle (breast/butt/belly) the converter grafted destabilises
    # physics garments and collapses rigid leg plates on the UBE actor. On a
    # physics GARMENT (skirt/cloth chain present) strip the GRAFTED jiggle from
    # EVERY shape -> reverts to the gold skinning that holds the chain (the dwarven
    # pull fix); on plain armour strip leg-plates only (keeps torso conformance).
    # #legplate-jiggle #garment-softbody-strip
    weights_map = _strip_jiggle_weights_map(
        weights_map,
        src_bones=set(src_shape.bone_names or []),
        force=_nif_has_garment_chain(src_shape.file))
    weights_map = _fill_zero_weight_verts(weights_map, use_verts)
    for bn, pairs in weights_map.items():
        if not pairs:
            continue
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
        cbbe_nif = pyn.NifFile(filepath=str(cbbe_path))
        ube_nif = pyn.NifFile(filepath=str(ube_path))
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

    # IDW with power=2 weighting (1/d^2). The nearest body vert
    # dominates strongly; further neighbors contribute only when
    # the nearest is not much closer than them. Preserves the full
    # local body delta for surface-hugging cloth without averaging
    # in weaker deltas at neighbor positions.
    #
    # Note: empirically the choice of IDW power has minimal effect
    # on revealing armor drape (a soft-body cloth shape stayed at 1.42u under
    # both IDW^1 and IDW^2) because K=4 nearest verts are usually
    # all on the same body region anyway. The real cause of the
    # "armor sits tighter than hand-built" delta is that BodySlide
    # applies an outward INFLATION when building UBE armor, which
    # our warp doesn't replicate. That's handled by the inflation
    # post-pass after this function (see convert_nif).
    w = 1.0 / (dists * dists + 1e-9)
    w /= w.sum(axis=1, keepdims=True)
    interp_delta = (body_delta_per_vert[idx] * w[..., None]).sum(axis=1)

    # Optional distance falloff. Verts far from the body shouldn't be
    # warped (they don't follow the body's deformation field) — without
    # this, fingertip verts on a gauntlet get pulled by IDW from the
    # nearest wrist body vert, displacing finger geometry by 1-2u and
    # breaking finger pose. Falloff = 1 at d=0, linearly to 0 at
    # max_distance, clamped at 0 beyond.
    if max_distance is not None and max_distance > 0:
        nearest_d = dists[:, 0] if dists.ndim == 2 else dists
        falloff = np.clip(1.0 - nearest_d / max_distance, 0.0, 1.0)
        interp_delta = interp_delta * falloff[:, None]

    # ----- Upper-body standoff damp -----
    # Rigid decorative geometry that stands OFF the body in the UPPER body
    # (a stiff collar, high pauldrons, shoulder spikes, a stiff neckline) must
    # NOT inherit the full body delta: the body broadens/shifts CBBE->UBE at the
    # chest+shoulders and the IDW warp drags such standoff pieces out+back with
    # it, SHEARING them (measured: the Ebony cuirass collar sheared ~5.7u). We
    # smoothly reduce the warp where BOTH gates open: upper-body Z (so all
    # LOWER-body drape -- skirts/tabards -- is untouched) AND high standoff from
    # the source body (so body-FITTED chest/shoulder cloth is untouched). A
    # smoothstep ramp avoids a seam between damped and undamped verts. #178
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
    bigger UBE body (low blend, no clip = the Ruby corset), while loose pieces just
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
        #    don't clip on the bigger/morphed UBE body (the Ruby corset case);
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
    # BUST CLEARANCE (anti nipple poke-through), nipple-aware (#175). The old
    # pass shoved the WHOLE chest Z-band out to a fixed `bust_clearance` measured
    # against the NEAREST body vert -- too loose on flat panels yet sometimes too
    # weak at the nipple tip (rarely the nearest body point to the fabric over
    # it). Now the required clearance is keyed on the body's Breast03 tip-bone
    # weight (`ube_body_nipple`): flat chest fabric keeps only BUST_FLAT_CLEARANCE
    # (close fit) while the breast front / nipple ramps up to `bust_clearance`.
    # It's enforced over the WORST (closest) body vert in a local neighbourhood
    # -- not just the nearest -- so a nipple tip that pokes past the fabric is
    # caught even when a flatter body vert is closer. The move is the gentler of
    # (the general pull-in already chosen) and (the clearing floor); push-OUT only
    # kicks in where the body would poke. Cloth already clear / loose is untouched.
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
) -> np.ndarray:
    """FINAL anti-poke pass (#175): push each armor vert OUT of the body so the
    actor's live morph can't punch through. PUSH-OUT ONLY (additive; never pulls
    cloth in), measured against the (injected) UBE body -- which is always
    present with valid normals -- so unlike the source-correspondence conform it
    ALWAYS lands, and (called last) nothing undoes it.

    Each vert is cleared to at least `flat_clear` over the WORST (closest) body
    vert in a local neighbourhood (so a nipple/belly bulge that pokes past the
    fabric is caught even when a flatter body vert is nearest). In the bust
    Z-band the required clearance ramps from `flat_clear` up to `bust_clear` by
    the body's Breast03 nipple weight, so flat panels stay close and only the
    breast front gets the bigger standoff. Verts far from the body (> max_body_
    dist) are untouched (don't bulge a free-hanging drape)."""
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
    req = np.full(len(v), float(flat_clear))
    if body_nipple is not None and len(body_nipple) == len(bv):
        z = bv[nearest][:, 2]
        in_bust = (z >= bust_z[0]) & (z <= bust_z[1])
        nipw = np.asarray(body_nipple, dtype=np.float64)[nearest]
        req = np.where(in_bust,
                       np.clip(flat_clear + nipw * nipple_gain, flat_clear, bust_clear),
                       req)
    push = np.clip(req - worst, 0.0, max_push)            # push OUT only
    push = np.where(dd[:, 0] < max_body_dist, push, 0.0)  # leave far drapes alone
    return (v + nrm * push[:, None]).astype(np.float32)


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
    holes, or flicker -- the "mangled fabric" symptom (measured: Magecore dress
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


# Shape names that mark a CBBE inline body. The main 3BA mesh and its
# anatomy detail shapes (3BA_Vagina/3BA_Anus) all get stripped during
# phase 2; we replace with UBE BaseShape (verbatim from the user's
# preset-built UBE body NIF) plus UBE Hands and UBE Feet.
#
# Vanilla replacer NIFs (Iron Cuirass etc.) also embed a small
# placeholder body shape — typically named `FemaleUnderwearBody:N` or
# `Female*Body*` — that's CBBE-sized (~820 verts, below the heuristic
# threshold) and lives in the meshes used by NPC mannequin/preview. If
# we don't classify these as body shapes, Phase 1 copies them verbatim
# and the CBBE-positioned underwear clips through UBE legs at the
# floor — visible as a leather skirt / loincloth sitting below the
# character's feet in-game.
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

    # Per-shape weight / bone-count checks. We iterate each skinned
    # shape and aggregate per-vert weight stats.
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

        # Unweighted verts: skinned shape but specific vert has no
        # bones with non-zero weight. Those verts render at bone
        # origin (spike artifacts).
        unweighted = int((per_vert_count == 0).sum())
        if unweighted > 0:
            warnings.append(
                f"{name} :: {s.name}: {unweighted} verts have zero "
                f"bone weight (spike risk)"
            )

        # Over-4 influences: Skyrim hard caps at 4 bone weights per
        # vert. Exceeding this can crash the game or silently drop
        # weights at load time.
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

        # Non-identity geometry-transform SCALE on a SKINNED shape: the engine
        # ignores the NiAVObject transform for skinned meshes, so a leftover
        # scale renders the shape at the wrong size — flung off-body (invisible/
        # static) or collapsed. `_copy_shape`'s #scalebake bakes this into the
        # verts and emits an identity transform; this flags any shape that
        # slipped through (e.g. a verbatim copy whose re-author failed) so it
        # shows up in the conversion log instead of only in-game.
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

    # BODYTRI shape-name cross-check: if a BODYTRI is attached, its
    # TRI's shape entries should match shape names in the NIF.
    # Entries with no matching shape are ignored by NioOverride at
    # runtime — usually harmless waste but signals stale/mismatched
    # TRI data.
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

    # HDT-SMP XML cross-check: if the NIF has a `HDT Skinned Mesh
    # Physics Object` extra-data string, verify the referenced XML
    # exists, parses, and only references bones that are actually
    # in the NIF's skeleton. Misconfigured XMLs make HDT-SMP no-op
    # silently (cloth doesn't follow body, body clips through).
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
UBE_BODY_SHAPE_NAMES = frozenset({
    "BaseShape",  # canonical UBE body is 29298 verts; mod-specific BaseShapes are rare
})
# Vertex count thresholds used to distinguish UBE body shapes from
# similarly-named collision proxies. UBE BaseShape ~29k, VirtualBody ~14k.
_UBE_BASESHAPE_MIN_VERTS = 20_000
_UBE_VIRTUALBODY_MIN_VERTS = 10_000

# Heuristic thresholds for detecting an inline body shape that's NOT named
# `3BA` (mods commonly use bespoke names like `_Fuse00_a heavily-boned armor_Body`,
# `OBI_Body`, etc.). A shape spanning almost the full character height
# AND skinned to many bones is almost certainly a body. Tuned to:
#   * catch full-body inline meshes (CBBE 3BA spans ~103 Z, full bones)
#   * NOT catch long armor pieces (capes / coats — high Z, few bones)
#   * NOT catch accessory shapes covering much of the torso (a heavily-boned armor_Acs:
#     Z=63.6, bones=59 — fails Z threshold)
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
    # Vert-count floor. A custom inline body normally needs the high
    # _BODY_HEURISTIC_MIN_VERTS count to be told apart from an SMP skirt — but
    # skirts carry a CLOTH diffuse and were already rejected above, so here we
    # only need enough geometry to be a real body skin. This admits the
    # VANILLA-topology body skins armour replacers ship (HDT-SMP Vanilla's
    # forsworn `ForswornFemaleBody`, ~1.5k verts) that the 4000-vert gate used
    # to drop into the CLOTH path — where they were warped + scale-boned at
    # their BodySlide preset bulk, then node-scaled AGAIN at runtime =
    # double-scaled body under skimpy armour (#164). Classifying them as a body
    # routes them to the phase-2 body-swap (source skin dropped, base UBE
    # BaseShape injected) so the body scales exactly once, like the nude body.
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


def classify_shapes(nif: nif_io.Nif) -> tuple[list[str], list[str]]:
    """Split shapes into (inline-body, armor).

    Body detection combines:
      * canonical names (3BA / 3BA_Anus / 3BA_Vagina, plus BaseShape /
        VirtualBody with vertex-count guards)
      * a generic shape-shape heuristic: spans most of the character's
        vertical extent AND skinned to many bones. Catches custom-named
        inline bodies (e.g. `_Fuse00_a heavily-boned armor_Body`, mod-specific naming).
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

    # Heeled boots/shoes carry their heel-height as a NiFloatExtraData named
    # "HH_OFFSET" (NiOverride / HDT High Heels). pynifly CANNOT read
    # NiFloatExtraData ("Unknown block type") so it silently DROPS it on load —
    # every mesh we rebuild loses the heel and the boot sinks into the ground.
    # It's unpreservable through the pynifly pipeline. So for a HEELED, NON-BODY
    # piece, SKIP mesh conversion entirely: the ESP patcher then keeps the
    # ORIGINAL mesh (heel data intact) and just adds the UBE races (the
    # passthrough path). Feet barely differ CBBE<->UBE, so losing morph-scaling
    # is negligible next to a broken heel. Body-slot items still convert (a heeled
    # full bodysuit is vanishingly rare, and a CBBE torso on a UBE actor is worse
    # than a non-scaling foot). Raw byte-scan because pynifly already dropped it.
    _BODY_SLOT_BIT = 1 << (32 - 30)
    _hh_transplant_value = None  # heel offset (float) to re-inject after convert
    if not body_names and not (biped_slots & _BODY_SLOT_BIT):
        try:
            with open(src_path, "rb") as _fh:
                _heeled = b"HH_OFFSET" in _fh.read(262144)  # string table is early
        except OSError:
            _heeled = False
        if _heeled:
            # Heeled boot/shoe: the heel is a NiFloatExtraData "HH_OFFSET" that
            # pynifly silently drops on load. We CONVERT the boot (UBE-shaped)
            # and TRANSPLANT the heel block back at the binary level after all
            # pynifly saves (hh_offset.transplant_hh_offset, called at the very
            # end of this function). If our binary parser can't read the source
            # value reliably, fall back to ESP-only: skip conversion + delete any
            # stale converted mesh so the patcher keeps the original (heeled) one.
            from . import hh_offset
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

    if body_names:
        if ube_body_ref_path is not None:
            matched_ref = _weight_matched_ube_ref(src_path, Path(ube_body_ref_path))
            return convert_nif_phase2(
                src_path, dst_path,
                ube_body_ref_path=matched_ref,
                cbbe_body_ref_path=cbbe_ref_path,
                biped_slots=biped_slots,
                alt_texture_shape_names=alt_texture_shape_names,
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
        # (a) Body-aware rebuild: if we have a UBE body ref with
        #     vertex normals, REBUILD each shape via _copy_shape and
        #     apply snap_armor_outside_body. This fixes static fit
        #     for CBBE-authored corsets / panties / etc. that would
        #     otherwise sit inside the larger UBE body and have the
        #     body skin poke through them. Plus all the same HDT XML
        #     + BODYTRI + auto-TRI injection as the verbatim path.
        #
        # (b) Verbatim file copy (fallback): when no body ref is
        #     given. Preserves shape data exactly; injects extra-data
        #     only. Safe but doesn't fix static fit problems.
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

        # Body-delta warp: if we can find a same-weight CBBE base body
        # + UBE BodySlide-output body with shared 18k topology, prefer
        # the principled warp over the snap heuristic. The warp moves
        # each armor vert by the body's local CBBE -> UBE deformation,
        # preserving the artist's intended drape. Falls back to snap
        # when the body pair isn't found or topology mismatches.
        cbbe_verts_for_warp = None
        body_delta_for_warp = None
        # Per-shape recoverable failures (hand/foot warp/inflate raising,
        # etc.). Defined at this outer scope so it's visible BOTH to the
        # rebuild-path except handlers below AND the shared return at the
        # end (reached by the copy path too). Without an outer definition
        # the handlers would raise NameError and abort the shape loop.
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
        if not use_rebuild:
            shutil.copy2(src_path, dst_path)
        else:
            # Body-aware rebuild path. Open source, create fresh dst,
            # copy each shape with snap-outside + M6 proximity-blend
            # re-skin applied. The re-skin transfers UBE body bone
            # weights to armor verts close to the body — crucial for
            # single-bone "rigid prop" pieces (e.g. a hand-authored UBE armor's metal
            # ornament strips skinned to only NPC Spine2) that would
            # otherwise stay perfectly static at runtime because
            # NioOverride's BodyMorph treats single-bone shapes as
            # rigid attachments and skips vertex morphs. Once
            # re-skinned to nearby body bones, those pieces deform
            # naturally via standard skinning when the body morphs.
            pyn_lib = _pynifly()
            src_nif_for_fit = pyn_lib.NifFile(filepath=str(src_path))
            dst_nif_for_fit = pyn_lib.NifFile()
            dst_nif_for_fit.initialize("SKYRIMSE", str(dst_path))

            # Body-ref NIF + its BaseShape pynifly object for reskin
            # (compute_body_blend_skinning needs a real pynifly shape).
            ube_ref_nif_for_reskin, _, _ = _cached_ube_body_verts(
                Path(ube_body_ref_path))
            ube_base_for_reskin = next(
                (x for x in ube_ref_nif_for_reskin.shapes
                 if x.name == "BaseShape"), None,
            )

            # Two-pass conversion so z-fight fixup can run across all
            # final verts (see phase-2 for the same pattern).
            shape_jobs_p1: list[dict] = []
            # Track which body-skin extremity slots the source NIF
            # provided, so we can drop the CBBE-topology shape and
            # inject UBE Hands/Feet in its place after the loop.
            # Gauntlets typically ship a CBBE-3BA `Hands` body-skin
            # (~6.3k verts) alongside the cloth — when worn, slot 33
            # hides the actor's UBE Hands and renders this CBBE one
            # instead, giving the visible CBBE-fingers-with-armor
            # mismatch. Same logic for boots and `Feet`.
            extremity_slots_to_replace: list[str] = []
            # HDT-SMP per-vertex soft-body cloth (e.g. a hand-authored UBE armor a soft-body cloth shape)
            # must keep its authored weighting so it can still swing — skip
            # the body-fit reskin for it (see _hdt_softbody_shape_names).
            hdt_softbody_names = _hdt_softbody_shape_names(src_path)
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
                # Per-shape skin->world reconciliation. A shape with an offset
                # global_to_skin (ebony cuirass -64.7u; paladin/ashkhan gauntlets
                # & boots ~120u) stores verts far from world; the fit compares to
                # the world-frame body, so an un-reconciled shape matches the
                # WRONG anatomy and shears. Fit in WORLD frame (both branches),
                # restore to skin at output (pass 2). Identity g2s -> no-op.
                _shape_g2s = _shape_global_to_skin(s)
                # CRITICAL: hand/foot/glove/gauntlet shapes get NO vertex
                # modification. Same guard as in convert_nif_phase2 — see
                # the comment block there. Without this, the body-delta
                # warp + inflate ops at the top of pass-1 will displace
                # finger/hand verts by up to 2.6 units (Iron Gauntlets
                # Hands shape: 3184 of 6374 verts moved).
                if _shape_has_fine_animation_bones(s):
                    # Boots / gauntlets / hands shapes. We want these
                    # fully UBE-SHAPED (not CBBE-shaped) so the shell
                    # conforms to the larger UBE forearm/calf instead of
                    # clipping through it. So apply the FULL body-delta
                    # warp (same as body cloth: min_standoff buffer, no
                    # distance cap) — the whole piece, fingers included,
                    # follows the CBBE->UBE deformation. Fingers conform
                    # to the UBE base hand (near-identical to CBBE, so
                    # negligible movement there) while the wrist/forearm/
                    # calf shift out to the UBE limb.
                    #
                    # The RUNTIME body-morph response is then split per
                    # vertex below: limb verts get 3BA scale bones (follow
                    # body sliders), but finger/toe verts are masked out
                    # via _extremity_vert_mask so body morphs have ZERO
                    # effect on them. Their finger/thumb/toe bone weights
                    # stay intact, so finger morphs still work.
                    # World frame so the warp/inflate match correct anatomy on
                    # offset-g2s gauntlets/boots (restored to skin in pass 2).
                    hf_orig = _verts_skin_to_world(
                        np.asarray(s.verts, dtype=np.float64), _shape_g2s)
                    hf_verts = hf_orig
                    hf_verts_modified = False
                    # Per-vertex extremity fraction drives the warp falloff:
                    # forearm/calf (fraction~0) get the FULL CBBE->UBE warp so
                    # they conform to the UBE limb; fingers/toes (fraction~1)
                    # stay put (the UBE ref has no hand/foot mesh, so warping
                    # digits melts them); the wrist blends. See
                    # _extremity_vert_fraction.
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
                        # Post-warp inflation: replicates BodySlide's
                        # build-time outward puff-out. Without it,
                        # revealing armor sits at source CBBE drape
                        # (typically 1-2u above body) which is the
                        # minimum-safe distance the SOURCE author
                        # chose. UBE bodies tend to be morphed bigger
                        # than CBBE base, so body morphs at runtime
                        # can grow past that minimum and nipples/skin
                        # poke through cloth. The inflation adds
                        # ~0.4u additional standoff (with falloff so
                        # far-from-body verts don't get pushed). The
                        # magnitude is slot-aware — see
                        # `_slot_aware_inflation_magnitude`.
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
                    else:
                        # Legacy fallback when CBBE base body isn't
                        # available — push inside-body verts outward
                        # along UBE normals.
                        snapped = snap_armor_outside_body(
                            sv_world,
                            body_verts_for_fit,
                            body_normals_for_fit,
                        )
                    # #177: keep self-simulated cloth (custom physics-chain
                    # bones: skirt/belt/cape) at its SOURCE position so it stays
                    # aligned with its chain bones (recreated at source bind).
                    # The warp/inflate above move the cloth onto UBE while the
                    # bones stay at source -> cloth offset from its OWN bones ->
                    # SMP rest pose wrong -> collapse / fall through. Per-vertex
                    # (chain-weight fraction), so a part-physics part-body shape
                    # (skirt+chest) keeps the body/chest warped. Pass-1 runs in
                    # the shape's local space, so the source is s.verts.
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
                        and not _shape_has_fine_animation_bones(s)
                        and not _shape_is_head_dominant(s)
                        and not _shape_has_hdt_smp_rigging(s, _body_bone_set)):
                    try:
                        # World-frame verts so reskin matches correct anatomy.
                        verts_for_reskin = (snapped if snapped is not None
                                            else sv_world)
                        # Slot-aware conformance band: body-fitted armor
                        # (slot 32 + legs) tracks the body's skeleton over a
                        # wider shell so it bends WITH the body during motion
                        # (no body-poke-through); skirts keep the narrow band.
                        _rn_p1, _rf_p1 = _slot_aware_reskin_band(biped_slots)
                        bones, xforms_map, weights_map = compute_body_blend_skinning(
                            verts_for_reskin, s, ube_base_for_reskin,
                            near_dist=_rn_p1, far_dist=_rf_p1,
                        )
                        # Universal scaling pass: add 3BA scale-bone
                        # weights so the armor follows body sliders
                        # even when its verts are too far from the
                        # body for the M6 blend to reach them. This
                        # is what makes hanging cloth (loincloths,
                        # skirts, draped tabards) scale with sliders.
                        # Slot 49 cloth (hanging hems) gets an extended
                        # reach so far-hanging hem verts still pick up
                        # scale weight — see
                        # `_slot_aware_scale_bone_reach`.
                        # Add 3BA scale-bone weights to ALL reskinned cloth. The
                        # cloth shapes carry NO per-shape BODYTRI (single carrier
                        # is the body shape, #114), so they do NOT morph via the
                        # per-armor TRI — the scale bones are their ONLY runtime
                        # body-tracking layer (we don't bake the preset per-cloth
                        # like a BodySlide build does). Without them the cloth
                        # sits at the base UBE shape while the body morphs to the
                        # user's preset = "extremely far from body". The 78-bone
                        # cap (`_cap_skin_bone_count`) is the universal backstop
                        # for the rare dense suit that overruns the GPU palette;
                        # it ranks by LOCAL dominance so physics/skirt bones are
                        # kept and only thin scale tails drop. [reverts #164/#166]
                        if ADD_SCALE_BONES_TO_CLOTH:
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
                    # WORLD-frame verts (so the batch z-fight/cleavage passes,
                    # which compare to the world-frame body, also match correctly).
                    # Transformed back to skin in pass 2 via "g2s".
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
                    )
                    if n_abdo:
                        import sys as _sys
                        print(f"  abdomen depth: stacked {n_abdo} waist-layer "
                              f"vert(s) for clean separation", file=_sys.stderr)
                except Exception:
                    pass  # best-effort

            # Layered-cloth weight sync (re-enabled 2026-05-29, now GATED
            # by breast-weight fraction so it only touches genuine bust
            # layers, never decorative attachments — see
            # _sync_chest_layered_cloth_weights / CHEST_SYNC_MIN_BREAST_FRAC).
            # Keeps a bra and the fabric over it moving together under
            # breast-jiggle physics so they don't intersect in motion.
            if shape_jobs_p1:
                try:
                    n_synced = _sync_chest_layered_cloth_weights(shape_jobs_p1)
                    if n_synced:
                        import sys as _sys
                        print(f"  cleavage sync: matched {n_synced} bust-layer "
                              f"vert(s) to authority weights", file=_sys.stderr)
                except Exception:
                    pass  # best-effort; failure leaves shapes as-is

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
                    except Exception:
                        pass
            # Inject UBE Hands/Feet to replace the CBBE-topology body-
            # skin shapes the source NIF carried (e.g. gauntlet's
            # 6374-vert `Hands` shape gets replaced by 15500-vert UBE
            # Hands from the user's preset tangent NIF). Safe because
            # the gauntlet/boot occupies slot 33/37 → actor's slot
            # 33/37 ARMA is hidden, no z-fight from the actor's
            # nude hands rendering alongside.
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
            dst_nif_for_fit.save()

        # HAND (33) / FOOT (37) slots are RIGID gauntlet/boot armor — NEVER cloth.
        # Our cloth detector treats a fabric-named shape (e.g. a gauntlet's
        # "gloves" shape) as a soft-body and generates an HDT-SMP XML for it; at
        # runtime HDT-SMP then simulates the hand-worn piece as cloth and it
        # collapses/flies off -> INVISIBLE. The static mesh looks perfectly valid
        # (which is why this hid through every static check), because the failure
        # is purely runtime physics. The gold-standard UBE gauntlets AND the
        # source meshes carry NO HDT XML. So: no cloth physics on hands/feet.
        if biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT):
            hdt_xml = None
        else:
            hdt_xml = _find_hdt_xml_for_armor(src_path)
            # If the source XML drives physics-CHAIN bones (a physics-chain bone
            # /Skirt N_NN) our conversion stripped, its reference points HDT-SMP
            # at bones our NIF lacks -> dead cloth. Regenerate a fresh per-vertex
            # soft-body XML anchored to the standard body bones we DO have, so
            # hanging cloth (tabards/skirts) simulates without any BodySlide-
            # injected chain rig. See _source_hdt_needs_missing_chain_bones.
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
            # If no source HDT XML was found, generate a minimal one with
            # cloth↔body collision shapes. Returns None only if the NIF has
            # no cloth carriers at all (nothing to simulate). A cloth-only
            # NIF with no body proxy (slot-49 skirt/tabard) still gets a
            # valid cloth-only soft-body XML — the cloth collides with the
            # actor body's "body" tag at runtime. See
            # `_generate_hdt_xml_for_dst` docstring + src/hdt_xml_gen.py.
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
        # Carrier shape name is set during BODYTRI injection below and
        # later passed to generate_armor_tri so the TRI lists the
        # carrier first (matches hand-authored UBE convention).
        carrier_name_for_tri: str | None = None
        # HANDS (33) and FEET (37) BOTH get a BODYTRI / auto-TRI so gauntlets and
        # boots conform to the UBE wrist/forearm + calf/ankle. The old fear was
        # finger/toe distortion (#127), but the auto-TRI path below dampens
        # per-vert morph by _extremity_vert_fraction (#147): finger/toe verts get
        # ~0 morph while wrist/forearm/calf verts follow body sliders. These were
        # gated off under the since-DISPROVEN "BODYTRI/HDT on hand-foot = invisible"
        # theory -- the real invisibility cause was a missing _0/_1 weight partner,
        # NOT the BODYTRI. HDT cloth physics STAYS gated off for both hand+foot
        # (rigid pieces; the source meshes carry no HDT) via the slot gate above.
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

                # BODYTRI on a single cloth-priority shape. NioOverride
                # appears to read only the FIRST BODYTRI in a NIF —
                # putting BODYTRI on every shape caused the carrier
                # to shift to the first textured shape in iteration
                # order (e.g. 6MetalDecoWaist) and the actual cloth
                # piece (a corset shape) silently stopped morphing.
                # Single-shape placement matches the hand-authored
                # BodySlide convention.
                #
                # Rigid single-bone pieces (e.g. 6MetalDecoTorso) that
                # NioOverride would otherwise treat as "props" still
                # follow body morphs because M6 reskin re-weighted
                # them to multiple body bones — they morph via
                # standard bone-driven skinning, not BodyMorph.
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

                nf.filepath = str(dst_path)
                nf.save()
            except Exception:
                pass  # injection is best-effort; copy already done

        # (Removed 2026-05-29) Cloth-count reduction was here. It was
        # built under the wrong premise that skee has a ~9-shape-per-NIF
        # morph cap. Real cause was our PIRT TRI writer truncating to 9
        # via the shape-count header field (#139). The merge function
        # remains DEFINED in this file for now as dead code; once
        # #141 is acted on it can be ripped entirely. TRI now lists all
        # of the NIF's shapes verbatim with no truncation.

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
                        # Per-vert extremity fractions per shape. Long
                        # sleeves / thigh-high boots that span hand/foot
                        # AND limb regions need PER-VERT morph dampening
                        # (finger/toe verts get ~0, limb verts get full
                        # morph). Without this, the whole shape is either
                        # excluded (current behavior - sleeve doesn't
                        # follow body morph, arm clips through) or
                        # included entirely (fingers deform). #147 fix.
                        armor_vert_ef: dict[str, np.ndarray] = {}
                        for s in dst_check.shapes:
                            if s.name in UBE_BODY_INJECT_NAMES:
                                body_in_dst.add(s.name)
                                continue
                            # Include EVERY armor shape in the TRI. The
                            # per-vert extremity-fraction dampening below
                            # scales each vert's propagated morph delta by
                            # (1 - frac), so finger/toe verts (frac~1)
                            # get near-zero morph (matching the nude
                            # actor's separate Hands/Feet meshes) while
                            # forearm/calf/upper-arm sleeve verts (frac~0)
                            # get full morph (so long-sleeve gauntlets and
                            # thigh-high boots actually follow body
                            # sliders). Replaces the prior all-or-nothing
                            # `_shape_is_extremity_dominant` exclusion,
                            # which dropped long sleeves whose hand
                            # portion outweighed the sleeve portion (e.g.
                            # a hand-authored UBE armor a long-sleeve shape at 66% extremity).
                            # Body-space offset (shape transform) so the morph KNN
                            # matches the right body region for shapes authored in
                            # a shifted space (e.g. elven cuirass top at Z=-49 +
                            # +120 transform -> else "top half doesn't scale").
                            # BodyMorph is index-based, so the per-vert deltas
                            # still apply correctly to the local-space mesh.
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
                        auto_tri_dst_phase1.parent.mkdir(
                            parents=True, exist_ok=True)
                        tri.save(auto_tri_dst_phase1)
            except Exception:
                pass  # auto-TRI is best-effort; armor still works without

        # Unconditional VirtualBody-hidden pass. Phase 1's verbatim
        # copy path doesn't enter the HDT/BODYTRI conditional block
        # above, so source-inherited VirtualBody shapes never had
        # their Hidden bit set. Catch them here. Cheap no-op when
        # VirtualBody isn't present in the dst (which is the common
        # case — body-region NIFs have it, accessory NIFs don't).
        try:
            pyn_for_vb = _pynifly()
            nf_for_vb = pyn_for_vb.NifFile(filepath=str(dst_path))
            if _hide_virtual_body(nf_for_vb):
                nf_for_vb.filepath = str(dst_path)
                nf_for_vb.save()
        except Exception:
            pass  # best-effort; doesn't break the conversion

        # Multi-partition collapse (post-save reload pass) — see
        # `_normalize_partitions_on_disk`. Runs after the VirtualBody
        # save so it operates on the final on-disk NIF.
        _normalize_partitions_on_disk(dst_path)

        # FINAL HDT-SMP physics pass — must run LAST so the extra-data
        # survives (earlier round-trips were dropping it). Prefers the
        # source armor's authored XML (real skirt/tassel/flap chains).
        # SKIP for hand/foot slots: gauntlets/boots are rigid, never cloth
        # (cloth physics on them collapses the piece at runtime). See the
        # hdt_xml gate above + the gauntlet-invisibility root cause.
        if not (biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
            try:
                _finalize_hdt_physics(dst_path, src_path)
            except Exception:
                pass

        # Verbatim-copied NIFs (no body fitting — gauntlets/boots/helmets)
        # carry the source author's raw block structure, which Skyrim's
        # renderer can reject on a converted/re-pathed armor (the
        # "invisible even when worn alone" symptom). Re-author them from
        # scratch so they get the same clean pynifly-authored structure as
        # the body shapes that render. See _reauthor_nif_fresh.
        if not use_rebuild:
            try:
                _reauthor_nif_fresh(dst_path)
            except Exception:
                pass

        # Optional param-hardening LAST (gated): _reauthor + the finalize
        # passes above can re-emit the authored XML un-hardened, so re-apply
        # the param clamp here. Idempotent. NOTE: do NOT re-run
        # _harden_hdt_xml_for_fsmp here — re-pruning the copy-path armor's XML
        # caused working cuirasses (dwarven/steel/elven) to regress; the
        # in-_finalize prune pass is the only one (matches pre-regression
        # behaviour where _reauthor's re-emit left copy-path XML intact).
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

        # Heeled boot: re-inject the HH_OFFSET heel block NOW — this must be the
        # VERY LAST write, after every pynifly save (reauthor / normalize / hdt
        # all drop NiFloatExtraData). If the binary transplant can't safely run
        # (parser won't round-trip this NIF), fall back to the original source
        # mesh so the !UBE path still has a working heeled boot (heel intact,
        # just CBBE-shaped) rather than a heel-less converted one that sinks.
        if _hh_transplant_value is not None:
            from . import hh_offset
            if hh_offset.transplant_hh_offset(dst_path, _hh_transplant_value):
                reason_parts.append(
                    f"heel HH_OFFSET={_hh_transplant_value:.3g} transplanted")
            else:
                try:
                    shutil.copy2(src_path, dst_path)
                    reason_parts.append("heel transplant unsafe — used original mesh")
                except OSError:
                    pass

        return ConvertResult(
            src_path=src_path,
            dst_path=dst_path,
            status="converted (copy)",
            reason="; ".join(reason_parts),
            armor_shapes=armor_names,
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

    # Direction-choice strategy: K=4 IDW smoothing of body normals
    # is good for stable convex regions (chest, back, sides) where
    # adjacent body normals point in similar directions. But in
    # concave regions (between legs, under arms, between buttocks)
    # adjacent normals point in OPPOSITE directions and the average
    # collapses to something perpendicular to both — e.g. between
    # legs the LEFT-leg-outward and RIGHT-leg-outward normals
    # average to FORWARD. Armor verts on either inner thigh then
    # get pushed FORWARD into the same fake position, merging into
    # a "skirt" mesh between the legs.
    #
    # Hybrid: compute both K=4 smoothed and K=1 nearest-neighbor
    # normals. If they agree (angle < ~30 degrees), use the smoothed
    # version (less seam noise). If they disagree, the region is
    # concave/discontinuous — fall back to per-vert K=1 nearest
    # so left-leg verts track left-leg normals and right-leg verts
    # track right-leg normals, no cross-merging.
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
        # Only push verts that are MARGINALLY inside the body. Verts
        # deeper than `max_inside_depth` (default 0.6) are likely either
        # intentionally CBBE-body-conforming (the source author designed
        # them to sit inside the body envelope, e.g. tight leather
        # wrapping) or hidden by outer layers — pushing them to +offset
        # standoff creates large local displacement that tears tight
        # armor (a mashup cuirass: 1.5-unit pushes on the right buttock
        # at ~0.7-1.0u depth left visible mesh tears with offset=0.5/
        # max_inside_depth=1.0). With offset=0.2/max_inside_depth=0.6:
        # max displacement is ~0.8u and tighter armor stays intact.
        # The remaining tradeoff is minor body poke-through on verts
        # deeper than 0.6u, which is invisible if the armor is opaque.
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
# BUILT 2026-06-04, NOT YET WIRED into convert_nif (FEMINIZE_MALE_ARMOR=False).
# Male-only armor (a MOD2 male model with no female MOD3) falls back to the male
# mesh on a female UBE actor. The existing snap/inflation pushes the FLAT male
# chest OUT past the breasts (no poke-through — good) but leaves it sitting as a
# boxy SLAB ~3-5u off the bust (MEASURED on converted male cuirasses), so it
# isn't form-fitting. This pass pulls the armor IN to HUG the female contour in
# the feminine zones (breast/butt/belly/waist), using ONLY the female body (no
# unreliable male-body reference). It only REDUCES clearance (never pushes out ->
# can't add poke-through) and never below `target_standoff`. Zone weight comes
# from the body's outward morph amplitude (the same map adaptive clearance uses),
# so the static shoulders/back/arms — where male and female are alike — are left
# untouched. Apply AFTER the normal warp/inflate/snap, for male meshes only.
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



# BODYTRI target for our armor's BaseShape. RaceMenu only applies morphs
# to armor BaseShape if the BODYTRI points at an armor-specific TRI that
# has outfit-bridge slider names (`*_ForOutfits` etc.). The standalone
# body TRI (`femalebody_tangent.tri`) has only body-slider names — those
# don't bridge to armor.
#
# The converter auto-generates a per-armor TRI from CBBE source + UBE
# body OSD slider data, so each converted mod is fully self-contained.
# This constant is only a legacy fallback path written into NIFs when
# auto-gen is disabled or no armor relpath can be derived.
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


# BSTriShape NiAVObject flag patterns that BodySlide-built UBE armor
# uses on every morphable shape. Bits 1, 2, 3 (= 0xE) are the
# "SelectiveUpdate*" bits used by every BodySlide-built shape; bit 19
# (= 0x80000) is the alpha-sorter flag and MUST be set on shapes that
# carry a NiAlphaProperty.
#
# Why this matters for NioOverride morphing: empirically, NioOverride's
# BodyMorph engine silently skips morphing on any alpha-having shape
# whose NiAVObject flags don't have bit 19 set. The alpha block alone
# isn't enough — the renderer needs to be told to sort the shape into
# the transparent-pass draw queue, and that's bit 19's job. Without it,
# the shape is in an inconsistent rendering state (alpha block present
# but not in the alpha sorter), and NioOverride refuses to apply morphs.
#
# Hand-built UBE conventions: examined `body1f_0.nif` (UBE-converted
# vanilla CBBE armor) — every shape in the NIF (body, alpha cloth,
# opaque cloth, all of them) uses flags = 0x8000e. Same across 310
# sampled hand-built UBE cloth shapes: 97% of alpha=True and ~73% of
# alpha=False shapes use 0x8000e. The remaining 0xE shapes are rarer
# and correspond to small/simple NIFs (e.g. a slot-49 no-body cloth armor's small
# corset NIF which still morphs).
#
# The empirical takeaway: 0x8000e is the safer default. Bit 19 is
# probably best understood as "this shape participates in the
# alpha-sort rendering pass" — for opaque shapes the bit is ignored
# by the renderer (no transparency to sort), so the cost is nothing.
#
# Previous iteration of this constant split by alpha state. That
# fixed a cloak shape's flags but other alpha=False cloth shapes
# (Belt_2, Strap, MaleUnderwearBody:0) ended up at 0xE while
# hand-built would have set them to 0x8000e — and user reported
# those shapes still don't morph in-game. Going uniform 0x8000e
# matches hand-built convention more closely.
BODYTRI_SHAPE_FLAGS_OPAQUE = 0x0000E   # bits 1, 2, 3 only — alpha-less cloth
BODYTRI_SHAPE_FLAGS_ALPHA  = 0x8000E   # add bit 19 (alpha-sorter) when alpha is on
# Back-compat alias for old callers that may still reference the old
# uniform constant. New callers should use OPAQUE/ALPHA explicitly via
# `_reset_morph_flags`.
BODYTRI_SHAPE_FLAGS = BODYTRI_SHAPE_FLAGS_OPAQUE


def _reset_morph_flags(shape) -> None:
    """Set shape.flags to match the hand-built UBE convention:

      * alpha=False shapes (most cloth)  -> 0xE   (bits 1, 2, 3)
      * alpha=True  shapes (translucent) -> 0x8000E (bits 1, 2, 3, 19)

    The 0x8000 bit is the alpha-sorter — required only when the shape
    has a NiAlphaProperty. Setting it uniformly (which we used to do
    via task #65) appears to interact badly with NioOverride for
    non-carrier shapes: hand-built UBE hand-authored UBE cloth uses 0xE on every
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


# BSLightingShaderProperty Shader_Type that BodySlide-built UBE armor
# uses uniformly: 0 (Default) — plain albedo + normal + metallic.
# Source CBBE armors typically ship Shader_Type=1 (Environment Map)
# for a wet/shiny look on leather and metal. Empirical observation
# on a different-texture mashup armor: shapes with Shader_Type=1 do NOT get morphed
# by NioOverride at runtime even when the BODYTRI string, TRI shape
# entries, and morphable flag bits are all correct. Forcing the
# shader back to Default unblocks runtime morphing. The visual
# cost is loss of cubemap reflection — leather/metal look slightly
# less shiny — which is preferable to armor that doesn't track the
# body.
#
# Shader_Flags_1 bit 7 (0x80) is the matching Environment_Mapping
# flag; we clear that too so the renderer doesn't try to sample an
# environment map at render time (would produce visual artifacts).
SHADER_TYPE_DEFAULT = 0
SHADER_FLAGS_1_ENV_MAPPING_BIT = 0x80


# SkyPartition slot for slot-32 cuirass body. Hand-built UBE NIFs put
# every shape under this single partition regardless of which body slot
# the original CBBE source assigned. Multi-partition cloth shapes
# (e.g. a vanilla armor a vanilla armorLeggings split into SBP_54 + SBP_38) silently
# fail to morph via NioOverride; collapsing to a single SBP_32_BODY
# partition unblocks them. See Task #118 sub-fix A.
SBP_32_BODY_ID = 32
SBP_32_BODY_NAME = "SBP_32_BODY"

# Standard biped dismember slots that represent a DISTINCT limb / accessory
# region and must NEVER be merged into the torso (SBP_32_BODY). Two failure
# modes if collapsed:
#   * accessory slots (gauntlet=33, boot=37, helmet=30/31, circlet=42, ...) ->
#     the game renders nothing in that equip region (invisible armour);
#   * limb slots that carry their OWN skin partition on a body-spanning shape
#     (forearms=34 on a long-sleeve body suit) -> collapsing the forearm
#     partition into SBP_32_BODY corrupts the skin partition's bone palette and
#     the renderer overruns its bone-matrix buffer = hard CTD on equip. Proven
#     2026-05-31 on Traveling Mage's `_Fuse00_TMage_Body` (CrashLogger: AVX
#     vmovdqa overrun in BSBatchRenderer drawing that exact shape; source had
#     SBP_34_FOREARMS+SBP_32_BODY, our output had collapsed them to one).
# We deliberately do NOT preserve calves (38) or the modder leg slots — those
# are the leg-region cloth partitions the morph-routing collapse (#118A) was
# built to fix, and collapsing them has never crashed.
SBP_HEAD_ID = 30
SBP_HAIR_ID = 31
SBP_HANDS_ID = 33
SBP_FOREARMS_ID = 34
SBP_FEET_ID = 37
PRESERVE_DISMEMBER_SLOTS = frozenset({
    30,  # head
    31,  # hair
    33,  # hands
    34,  # forearms  (added 2026-05-31 — the Traveling Mage CTD slot)
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


def _split_oversize_partition(shape, cap: "int | None" = None) -> int:
    """Split a shape that references MORE than `cap` bones into several skin
    partitions, each referencing <= cap distinct bones, WITHOUT dropping any
    bone. Returns the partition count created (0 = no split done).

    Skyrim's GPU skin-partition bone palette overruns above the cap -> equip
    CTD. The historical fix (_cap_skin_bone_count) dropped the lowest-weight
    bones to fit, but that evicted body-MORPH bones a dense dress needs (the
    Magecore dress lost NPC Belly + NPC R Butt -> stopped tracking the body).
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
        cur, cur_set = 0, set()
        for ti in order:
            ti = int(ti)
            merged = cur_set | tri_bones[ti]
            if len(merged) > cap and cur_set:
                cur += 1
                cur_set = set(tri_bones[ti])
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
        objs = [pyn.SkyPartition(part_id=SBP_32_BODY_ID, flags=257, namedict=nd)
                for _ in range(nparts)]
        shape.set_partitions(objs, assign.tolist())
        return nparts
    except Exception:
        return 0


def _normalize_partitions_on_disk(dst_path: Path) -> int:
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
        changed = 0
        for s in nf.shapes:
            # Body shapes keep their native partitions — they already
            # render + morph via the slot-32 carrier routing. Only
            # collapse cloth/armor shapes, where a stray multi-slot
            # partition (e.g. SBP_54 + SBP_38 on a vanilla armor leggings)
            # blocks NioOverride morph routing.
            if s.name in UBE_BODY_INJECT_NAMES or s.name == "VirtualGround":
                continue
            # Over-cap shape: SPLIT into <=cap-bone partitions (keeps every bone,
            # CTD-safe) instead of collapsing to one over-budget partition. Dense
            # dresses/robes that ship 79-81 bones (Magecore) keep their full
            # morph + physics rig this way. Under-cap shapes collapse as before.
            if len(getattr(s, "bone_names", []) or []) > SKIN_PARTITION_BONE_CAP:
                if _split_oversize_partition(s) > 1:
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
            if _normalize_partitions(s):
                changed += 1
        if changed:
            nf.save()
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
    """DISABLED (no-op). Previously cleared the environment-mapping flag
    (Shader_Flags_1 bit 7) to "unblock" NioOverride body-morph, under the
    belief that env-mapped (Shader_Type=1) shapes don't morph.

    That belief was a misdiagnosis: the real morph blocker was the per-NIF
    shape-count cap, now handled by the atlas merge. In-game confirmation:
    a mashup armor's env-mapped merged shape morphs correctly. So clearing env
    mapping was never necessary — and it did active harm:

      * It stripped the reflective shine the source leather relies on,
        leaving armor looking flat and very dark (worst in interiors).
      * `save_shader_attributes()` (called here to persist the flag clear)
        TRUNCATES the BSShaderTextureSet and DROPS the EnvMask (slot 5).
        That removed the per-pixel reflection mask, so the surviving env
        mapping applied UNIFORMLY — washing out diffuse on back-facing
        surfaces and dark indoors. It also meant the atlas builder never
        saw an EnvMask to combine, so the merged shape had none either.

    Keeping the source shader untouched (env mapping + EnvMap + EnvMask
    intact) matches how the armor renders in its regular, unconverted
    form, which is the goal. Left as a no-op (rather than removing the
    call sites) so the morph-readiness cleanup loop is unchanged.
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


def _sanitize_one_nif_worker(path_str: str) -> int:
    """Worker (picklable for ProcessPoolExecutor): load ONE NIF, clear
    inconsistent vertex-color/alpha shader flags, save if changed. Returns the
    number of shapes fixed (0 = nothing changed / unreadable). Each call touches
    a distinct file, so parallel workers never write-conflict."""
    try:
        from . import nif_io
        from pathlib import Path as _Path
        nif = nif_io.load_nif(_Path(path_str))
    except Exception:
        return 0
    try:
        n = fix_vertex_color_shader_flags(nif._backing)
    except Exception:
        return 0
    if n:
        try:
            nif._backing.filepath = path_str
            nif._backing.save()
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
            try:
                n = fix_vertex_color_shader_flags(nif._backing)
            except Exception:
                continue
            if n:
                try:
                    nif._backing.filepath = ps
                    nif._backing.save()
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


def _pick_bodytri_carriers(nif) -> "list[object]":
    """Pick exactly ONE shape per NIF to receive a BODYTRI extra-data
    block, matching the hand-authored BodySlide UBE convention.

    Carrier preference (in order):
      1. BaseShape / 3BA  — body shape, if present. This is what
         88/93 sampled hand-authored UBE slot-32 NIFs use. Empirical
         finding: when BODYTRI is on a cloth shape (`Panty`,
         `3LeatherBeltArms`, etc.) instead of the body, the cloth
         pieces in the same NIF often DON'T morph in-game despite
         being listed in the TRI with valid per-shape morph deltas.
         The a vanilla armor leggings failure (a vanilla armorLeggings TRI entry
         had full 818 Donaught offsets at mean=1.71u, same as
         BaseShape, yet didn't visually scale in-game) traced back
         to BODYTRI being on `Panty`. Moving BODYTRI onto the body
         shape makes NioOverride pick up the TRI via the body and
         apply per-name morphs to every shape it lists.
      2. Cloth shape — for body-less NIFs (slot-49 cloth-only
         armors like a slot-49 no-body cloth armor). Same selection rules as before
         (cloth-keyword > vert count, exclude rigid props).
      3. Hand/foot fallback — for gauntlet/boot-only NIFs.

    Both regimes use single-carrier. NioOverride opens the TRI once
    via the BODYTRI reference, iterates every TRI shape entry, and
    applies morphs to every same-named NIF shape.

    Returns a list with 0 or 1 entry.
    """
    # Preference 1: BODY SHAPE as carrier. Matches the dominant
    # hand-built convention (88/93 sampled slot-32 UBE NIFs).
    # VirtualBody is excluded — it's a Hidden physics proxy, not
    # the visible body shape NioOverride wants to morph.
    BODY_CARRIER_NAMES = ("BaseShape", "3BA")
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
        # Prefer non-extremity shapes as the carrier so the carrier is
        # always a shape that IS in the body-morph TRI (the TRI excludes
        # extremity-dominant hand/foot skins — see the TRI build loops).
        # Use the strict weight-fraction test, NOT the broad
        # `_shape_has_fine_animation_bones` name/any-bone test: arm-shell
        # pieces (Bracers/Guards/Armstrap, <1% extremity weight) make
        # perfectly good carriers and DO morph, while only the actual
        # hand/foot skin (Hands_2 97%, Gloves_1 71%) is pushed to the
        # last-resort fallback.
        if _shape_is_extremity_dominant(s):
            hand_fallbacks.append(s)
            continue
        candidates.append(s)
    if not candidates and not hand_fallbacks:
        return []

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
    # Carrier-of-last-resort: NIF contains ONLY hand/foot/glove/boot
    # cloth shapes (e.g. a separate gauntlets or boots NIF). Without
    # a carrier, NioOverride never loads the per-armor TRI and the
    # shapes don't morph at all. Picking the hand/foot shape itself
    # is safe HERE because the per-armor TRI for these NIFs only
    # contains the same hand/foot shape's morph entries — no body
    # deltas to leak onto fingers.
    hand_fallbacks.sort(key=rank_key)
    return [hand_fallbacks[0]]


# ---------- M6: per-vertex proximity-blend re-skin ----------------------
#
# Re-skin armor verts to UBE body bones so that body morphs/animations
# propagate to the armor at runtime. Per-vertex blend keyed on distance
# to body surface:
#
#   dist < RESKIN_NEAR_DIST           -> 100% body weights (full cloth)
#   RESKIN_NEAR_DIST <= dist < RESKIN_FAR_DIST  -> linear blend
#   dist >= RESKIN_FAR_DIST           -> 100% original armor weights (rigid)
#
# This sidesteps the per-shape "is this cloth or metal?" classification
# problem. Pauldrons standing off the body stay rigid; chest fabric
# hugging the body inherits body skinning automatically; belts in the
# transition zone get partial blend (which is roughly correct — leather
# does flex with body movement).
#
# Implementation:
#   1. For each armor vert, KDTree-find nearest body vert + K nearest
#      for IDW body-weight propagation.
#   2. Per vert: weights = (1 - blend) * original + blend * body_idw.
#   3. Add any new bones (body bones not in original armor) to the shape,
#      then redo the full skin setup (add_bone resets bone info, so we
#      have to set xforms and weights from scratch).
#
# The blend math is tuned so that 0.5 / 2.0 / K=4 covers chest fabric
# (which typically sits 0.1–0.5 units off the body) without touching
# pauldrons (typically 3+ units off).

RESKIN_NEAR_DIST = 0.5
RESKIN_FAR_DIST = 2.0
RESKIN_K = 4

# ROLLBACK FLAG. The M6 body-blend reskin transfers the UBE body's bone weights
# to nearby armour verts — INCLUDING the 3BA scale bones (NPC L Breast, Butt,
# etc.). KEEP FALSE.
#
# We briefly set this True (#166) on the theory that the transferred scale bones
# (a) caused the Traveling Mage equip CTD (over the ~80-bone GPU cap) and
# (b) double-morphed the Forsworn cloth. BOTH were misdiagnoses, proven wrong by
# the actual converted meshes after the full run:
#   (a) The CTD is the per-partition bone cap being exceeded — fixed properly and
#       universally by `_cap_skin_bone_count`, independent of this flag.
#   (b) The Forsworn "double" was a second BODY-SKIN shape inside the armour,
#       fixed properly by the body-skin detection that routes it to the phase-2
#       body-swap (the output now has a single injected BaseShape). The cloth
#       shapes carry NO per-shape BODYTRI (the single BODYTRI carrier is the body
#       shape, #114) — so cloth does NOT morph via the TRI; the scale bones are
#       its ONLY body-tracking layer. Removing them made every cloth piece sit at
#       the base UBE shape while the body morphed to the user's preset = the
#       Forsworn "extremely far from body" + Ruby chest + TMage distortion.
# So scale bones STAY (see ADD_SCALE_BONES_TO_CLOTH). The cap protects the dense
# suits. Set True only to A/B test a pure-node-scaling-off configuration.
RESKIN_EXCLUDE_SCALE_BONES = False

# Wider conformance band for BODY-FITTED armor (slot 32 body + leg slots).
# After the 2026-05-29 global tighten pulled armor closer to the skin, the
# body began poking through armor DURING MOVEMENT (not at rest). That's a
# skinning-divergence problem, not a static-gap problem: beyond
# RESKIN_NEAR_DIST an armor vert blends back toward the SOURCE author's bone
# weights, so under animation (knee/hip bend, breast/butt jiggle) it deforms
# differently from the body and the body swings out through it. The larger old
# static buffer used to mask this; the tighten exposed it. Fix WITHOUT adding
# standoff: widen the band so a thicker shell of body-fitted armor FULLY adopts
# the body's bone weights and bends WITH the body. Flowing cloth (slot-49
# skirts/loincloths) deliberately KEEPS the narrow default via
# _slot_aware_reskin_band — over-conforming a skirt makes it cling to the legs
# instead of draping/simulating.
RESKIN_NEAR_DIST_BODYFIT = 1.2
RESKIN_FAR_DIST_BODYFIT = 3.5

# Body-fitted biped slots: body(32), forearms(34), calves(38), legs(53-58).
# (Hands/feet/gauntlets/boots take the separate _shape_has_fine_animation_bones
# path, not this reskin, so they're intentionally absent here.)
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
# 3BA / UBE rig their bodies with dedicated SCALE BONES that the engine
# scales (not just translates) when body sliders move at runtime:
#
#   - L Breast01/02/03, R Breast01/02/03  -- breast morph cascade
#   - NPC L Butt, NPC R Butt              -- buttock scale
#   - NPC Belly                           -- belly inflation
#   - NPC L/R FrontThigh / RearThigh /
#     RearCalf                            -- thigh and calf size
#   - Clitoral1, NPC L/R Pussy02, ...     -- anatomy detail
#
# Armor verts skinned to these bones automatically follow body shape
# changes via standard bone-driven skinning — no NioOverride BODYTRI
# needed. This is the same mechanism BodySlide-built UBE armor uses,
# and it works regardless of which slot the armor is on or how shapes
# are attached.
#
# The base M6 reskin pass (above) only transfers bones to armor verts
# within RESKIN_FAR_DIST (= 2.0) of the body. Hanging cloth pieces —
# a mashup armor's a loincloth shape loincloth, draping skirts, robe panels — sit 2-5
# units off the body and never get any scale-bone influence under M6.
# So they stay rigidly skinned to NPC Pelvis / Thigh / Spine, which the
# engine does NOT scale with sliders, and the user sees the body bulge
# while the cloth stays put.
#
# The post-pass below specifically targets the scale bones with an
# extended reach (8 units default). It's surgical: only those bones are
# transferred, with small magnitude that DOESN'T overwhelm the armor's
# existing rigid-skeleton skinning. Pauldrons at 4u standoff get a few
# percent on the breast scale bone — invisible if the breast slider is
# at 0, gently follows if the slider goes big.
SCALE_BONE_KEYWORDS = (
    "breast",
    "butt",
    "belly",
    "frontthigh", "rearthigh", "rearcalf",
    # NOTE (2026-06-05): genital/anatomy bones (clit/pussy/vagina/anus/nipple)
    # are DELIBERATELY EXCLUDED. They are HDT-SMP PHYSICS bones, not just morph-
    # scale bones, and are unstable on the UBE race. The proximity transfer in
    # add_scale_bone_weights was putting up to 57% of a rigid groin plate's
    # weight onto them (wolf Greaves: source had 0%), so the body's genital/anus
    # jiggle dragged the "pants" down and they collapsed. Armor must NEVER track
    # genital anatomy. See project_physics_cloth_collapse.md.
)

# The physics-driven (HDT-SMP jiggle) subset of the scale bones. Distinct from
# the STATIC leg-shape scale bones (frontthigh/rearthigh/rearcalf), which only
# scale with sliders and carry no physics. On LEG-ENCASING rigid armor
# (greaves / leggings / pants / boot shafts) these jiggle bones drag the plate
# with body motion and collapse it on the UBE race -- the SAME failure as the
# genital bones, one region up (wolf Greaves waist band: up to 48% butt/belly).
# Suppressed for leg-dominant shapes (see add_scale_bone_weights); the static
# leg-shape bones are kept so the armor still follows thigh/calf SIZE sliders.
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


# UPPER-body skeleton bones a physics chain can anchor on. A garment chain that
# hangs off these (cape/backpack/robe off the torso, hair off the head) needs the
# NESTED source skeleton tree so FSMP tracks torso/limb motion through it; a chain
# anchored on the lower body (pelvis/thigh) works FLAT (actor-driven). Used to
# AUTO-SELECT flat vs nested per armor (#flatchain-auto). Confirmed in-game:
# TMage (bag/book/bottle off the SPINE) needs nested; elven/wolf (pelvis/thigh
# skirts) need flat.
_UPPER_BODY_ANCHOR_KEYWORDS = ("spine", "neck", "head", "clavicle", "shoulder")


def _is_upper_body_anchor(bone_name: str) -> bool:
    low = (bone_name or "").lower()
    return any(k in low for k in _UPPER_BODY_ANCHOR_KEYWORDS)
# Whether to add 3BA scale-bone weights to cloth shapes during reskin.
#
# KEEP TRUE. These weights make cloth follow the body's shape via bone
# scaling — the body-tracking layer that, together with the per-armor
# TRI morphs, keeps cloth glued to the body. Briefly set False ("Test 2")
# on the mistaken theory that scale weights blocked NioOverride morphing
# and to "match" the gold-standard vanilla pack. That was wrong on both
# counts: (1) the actual cloth-morph blocker was the per-NIF morph cap
# (fixed via TRI ordering + merge), and cloth morphed fine WITH scale
# bones present ("everything except leggings/skirt"); (2) the gold-stand
# pack achieves fit by BAKING the preset at BodySlide-build time, which
# our pipeline does NOT do — so our cloth genuinely needs this runtime
# tracking layer. Removing it regressed fit (a mashup armor + a slot-49 no-body cloth armor
# clipping). Hands/feet keep their own scale-bone pass regardless.
ADD_SCALE_BONES_TO_CLOTH = True

# A/B-test gate (#nosoftscale): when set, add_scale_bone_weights SKIPS the
# soft-body JIGGLE scale bones (breast/butt/belly -- PHYSICS_JIGGLE_SCALE_KEYWORDS)
# while keeping the STATIC leg-shape scale bones (frontthigh/rearthigh/rearcalf).
# Those jiggle bones are what the gold-standard (2026-05-31) build did NOT graft
# onto rigid plate, and grafting them is the wolf/dwarven-Greaves drag (confirmed
# by diff: current dwarven Greaves/body carry real breast/butt/belly weight the
# old build never added). Read at import so it reaches ProcessPool workers.
NO_SOFTBODY_SCALES = (
    os.environ.get("CBBE2UBE_NO_SOFTBODY_SCALES", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Inject a fresh UBE Hands/Feet body-skin shape into gauntlets/boots, replacing
# the source's CBBE-topology one. RE-ENABLED (2026-06-01): it was wrongly blamed
# for the gauntlet invisibility and disabled -- but the REAL cause was a missing
# _0/_1 weight partner (see project memory). With the flag OFF the source body-skin
# `Hands` shape is DROPPED with no replacement (the loop `continue`s), so the bare
# hand is INVISIBLE under the gauntlet (user-reported). With it ON we drop the
# CBBE-topology Hands and inject the user's built UBE hand
# (meshes/!UBE/Hands/femalehands_tangent_{0,1}.nif) so the exposed skin matches the
# UBE body. Injection no-ops gracefully if that tangent mesh is absent.
INJECT_UBE_EXTREMITY_REPLACEMENT = True

SCALE_BONE_REACH = 12.0   # world units beyond which no scale weight added.
                          # 12u (vs the 8u that worked for upper-torso cloth)
                          # lets abdomen-region armor (e.g. corset at Z=92-94)
                          # reach NPC Belly's body-side weighted verts
                          # (Z<=87.5).

# Slot 49 cloth (loincloths, skirts, hanging tabards) typically hangs
# 15-25u from the body — past the default 12u reach. Without an extended
# reach those far hem verts pick up zero scale-bone weight and stay
# static when the user pushes Big Butt / Hip / FrontThigh sliders.
# Linear falloff applies (verts at the reach get 0%, verts at d=0 get
# 100%), so close-to-body skirt verts track 1:1 while hem verts get a
# smaller-but-nonzero share — enough to grow proportionally with body
# morph.
#
# History: 25.0 -> 15.0 (2026-05-29). After the PIRT shape-count fix
# (#139), every cloth shape — including hanging skirt panels past TRI
# position 9 — receives BodyMorph deltas in-game, so scale bones no
# longer have to be the primary morph-tracking path for far hem verts.
# 15u covers the torso-down-to-mid-thigh reach (where the body actually
# grows on butt/hip sliders); hem verts beyond that follow via TRI
# morphs instead. Cuts the cross-region scale-bone bleed that was
# pulling skirt-front verts toward NPC L/R Butt on bind-pose alignment.
SCALE_BONE_REACH_SLOT49 = 15.0

# Hand/foot (gauntlet/boot) shapes use a TIGHTER reach than torso cloth.
# These pieces are skin-tight: a boot hugs the calf at <1u, a gauntlet
# hugs the forearm. The default 12u reach is wide enough to pick up
# body regions the piece merely GRAZES in the bind pose rather than
# touches — e.g. a gauntlet's forearm sits ~11u from the hip's butt-
# weighted verts, so at 12u reach the glove wrongly inherits `NPC L/R
# Butt` scale weight and the forearm verts get yanked toward the butt
# when that slider moves. An 8u reach keeps the tightly-hugging
# legitimate region (boot calf -> RearCalf at <1u; thigh-high boot ->
# thigh) while rejecting the cross-body bind-pose cross-talk (forearm
# to hip measured at 11.3u on a heavily-boned armor's gauntlet, comfortably dropped).
# Net effect: a plain gauntlet (forearm only, no 3BA-scaled region)
# correctly ends up with ZERO body scale bones — it's purely UBE-
# shaped via the static warp — while boots still follow calf/thigh.
SCALE_BONE_REACH_HANDS_FEET = 8.0

SCALE_BONE_K = 8          # K-NN over body verts for IDW
SCALE_BONE_MAX_TRANSFER = 0.65  # cloth: at d=0, allow up to this fraction of
                                # armor vert's total weight to become scale-
                                # bone influence. Hand-built UBE soft-body cloth
                                # tops out around 0.76 on scale bones, so
                                # 0.65 sits a hair below the empirical proven
                                # ceiling — leaves 35% on rigid bones for
                                # animation and avoids extreme cross-region
                                # bleed (a torso vert at d=0 from a butt-
                                # weighted body vert no longer pulls 95% of
                                # its weight into NPC Butt).
                                #
                                # History: 0.95 -> 0.65 (2026-05-29). The
                                # old 0.95 was set on the (wrong) theory
                                # that non-carrier cloth had no other
                                # morph-response path because shapes past
                                # TRI position 9 didn't morph. After the
                                # PIRT shape-count fix (#139) every cloth
                                # shape gets BodyMorph deltas via the TRI;
                                # scale bones are now just supplementary
                                # tracking, so they can sit at the proven-
                                # safe ceiling instead of the maxed-out one.
                                # Should fix the arm-piece breast/butt
                                # spike (#133) and chest/knee clip (#135).

# Torso "parity" falloff (#175 nipple/chest, #129 butt). The chest/belly/butt
# scale-bone tracking on body-slot pieces was crushed by the LINEAR distance
# falloff: a guard cuirass whose chest plate sits ~7u off the body tracked the
# live breast morph at only ~0.38 of the body (measured chest scale-weight 0.06
# vs body 0.16). So under a big preset the body grows out from under the armor
# and pokes through (and our nipple-clearance push-out, which adds standoff,
# made it slightly worse). For these regions on TORSO / lower-body pieces we
# replace the linear falloff (1 - d/reach) with a steeper power curve
# (1 - (d/reach)^P, P>1): a 7u-standoff plate now tracks at ~0.96 instead of
# 0.38, while far cloth still decays to 0 at the reach edge (same reach, so no
# extra cross-body reach). It is BOUNDED by the body's own per-vert weight (the
# propagation takes the MAX of the neighbor body weights, then * falloff <= 1),
# so the armor can at most track the body 1:1 -- it can never balloon past it.
# Keeping P finite (not infinite) leaves the armor a touch UNDER parity, which
# is correct: an armor vert sits further from the scale-bone pivot than the body
# surface vert, so it displaces MORE per unit weight -- matching weight exactly
# would slightly over-grow. Gated to slot-32 / slot-49 pieces so arm-only
# bracers (#133) keep the linear falloff and are untouched. Tunable: raise P if
# big presets still poke at the chest/belly; lower it toward 1.0 to revert.
# History: 4.0 -> 6.0 (2026-06-02) to close a residual "barely pokes" nipple on
# guard armor under a big preset; 6.0 ~= 0.96 tracking at a 7u standoff. Still
# bounded by body weight (falloff <= 1), so it cannot over-grow past the body.
TORSO_PARITY_FALLOFF_POWER = 6.0

# #133 Valenwood-bracer spike: an ARM/HAND-dominated armor vert must never
# inherit breast/belly/butt scale weight. A forearm bracer / sleeve sits within
# scale-bone reach of breast/hip-weighted body verts in the bind pose, so it
# picks up that cross-talk and gets YANKED into a spike when the slider moves.
# Hands/feet already use leg_region_only to drop torso bones, but a slot-34
# forearm piece routes through the CLOTH path where that guard doesn't apply.
# We suppress torso-region propagation on arm-dominated verts in
# add_scale_bone_weights regardless of slot/torso_parity -- an arm tracking the
# breast/butt is cross-talk by definition, so this never removes a legitimate
# morph. Revert by setting False.
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
#
# When a NIF is rebuilt (merge / copy), pynifly re-adds only the bones a
# shape is skinned to, and adds them FLAT under the root with an IDENTITY
# node transform. For STANDARD skeleton bones that's fine — the game
# resolves their real position from the actor's skeleton by name. But
# ARMOR-SPECIFIC physics bones (a skirt's `a heavily-boned armor_Skirt_Front 00..03` chain,
# cape/cloak/tail bones, etc.) are NOT in the actor skeleton: the game
# can't resolve them, so a flattened identity transform pins their verts
# to the world origin → the skirt collapses straight down through the
# floor. The fix is to recreate those bones' nodes with their SOURCE
# local transforms + parent links (chain intact, anchored to the standard
# bone they hang off) so they follow the body. See _precreate_custom_bone_chains.
#
# A bone is treated as a resolvable SKELETON bone (no preservation needed)
# if its name starts with one of these prefixes or contains one of the
# 3BA / vanilla body-bone keywords. Everything else weighted by a shape is
# an armor-specific bone whose chain we must preserve.
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
    norm: "dict" = {}         # affected vert -> {bone: renormalized w}
    for v in affected:
        rest = other.get(v) or {}
        s = sum(rest.values())
        norm[v] = ({b: w / s for b, w in rest.items()} if s > 1e-6
                   else {PELVIS: 1.0})
    out: "dict" = {}
    for bn in (set(weights_map) - gset) | {PELVIS}:
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
      * force=False (plain armour): strip ALL jiggle ONLY on a rigid LEG-PLATE shape
        (majority of verts dominated by rigid leg bones -- greaves/leggings/pants).
        NO-OP for torso/soft armour -> it KEEPS jiggle for breast/belly conformance.

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
        jset = {b for b in weights_map if _is_physics_jiggle_scale_bone(b)}
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
    norm: "dict" = {}
    for v in affected:
        rest = other.get(v) or {}
        s = sum(rest.values())
        norm[v] = ({b: w / s for b, w in rest.items()} if s > 1e-6
                   else {PELVIS: 1.0})
    out: "dict" = {}
    for bn in (set(weights_map) - jset) | {PELVIS}:
        pairs = [(int(i), float(w)) for i, w in weights_map.get(bn, [])
                 if int(i) not in affected]
        for v in affected:
            nw = norm[v].get(bn)
            if nw and nw > 0.0:
                pairs.append((v, nw))
        if pairs:
            out[bn] = pairs
    return out


# Chain-anchor strategy (#flatchain, 2026-06-07 — the COMPROMISE).
# Physics-chain HARD-skeleton anchors (Pelvis/Spine/Thigh/...) are recreated FLAT
# (identity, parent=Scene Root) so the ACTOR's live skeleton drives them at
# runtime. This is the pre-#177 behavior, CONFIRMED in-game to restore skirt swing
# on the UBE actor (elven). The #177 change baked the full nested SOURCE skeleton
# (Spine->COM->Root, Pelvis->COM, Thigh->Pelvis...) STATICALLY into the worn armor;
# FSMP then anchored the chain to that static copy instead of the live actor
# skeleton -> the chain didn't follow the body and the cloth sagged off it.
#
# CRUCIAL: this only changes the HARD-skeleton anchor. The other two #177 parts
# are KEPT unconditionally: (a) the multishape bone UNION pre-creation (so a later
# shape's anchor isn't clobbered flat-at-origin by an earlier add_bone), and
# (b) the CUSTOM + SOFT-BODY chain recreation at SOURCE bind (so chest/breast-
# anchored cloth still doesn't collapse). So we revert ONLY the over-reach.
#
# `CBBE2UBE_NESTED_CHAIN_ANCHORS=1` restores the full #177 nesting as an opt-in
# fallback. Read at import so it reaches ProcessPool workers. Default = flat.
NESTED_CHAIN_ANCHORS = (
    os.environ.get("CBBE2UBE_NESTED_CHAIN_ANCHORS", "").strip().lower()
    in ("1", "true", "yes", "on")
)


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
    # Operate on the UNION of ALL source shapes' bones, not just THIS shape's.
    # The converter processes shapes one at a time (skin -> _precreate ->
    # add_bone). A HARD anchor (e.g. NPC Spine for a belt chain that lives on a
    # LATER shape, or NPC Head for a cape's hair chain) gets added FLAT by an
    # earlier shape's add_bone before that later shape's _precreate runs -- and
    # add_node won't overwrite a present node, so the later chain hangs off a
    # flat anchor and collapses to the origin. Pre-creating EVERY shape's chain
    # on the FIRST call (before any add_bone) places all anchors at source bind
    # up front; later per-shape calls find them present and no-op. #177-multishape
    try:
        _allb = set(bone_names)
        for _sh in src_nif.shapes:
            _allb |= set(_sh.bone_names)
        bone_names = list(_allb)
    except Exception:
        pass
    # Which bones get their SOURCE bind recreated? Always the CUSTOM (armor-
    # specific, non-skeleton) bones. For a PHYSICS garment -- one that HAS a
    # custom chain -- ALSO the soft-body jiggle bones (breast/butt/belly): their
    # HDT physics is driven by the garment's OWN xml and seeded from the NIF
    # bind, so a breast bone left flat at the origin (or, for an unskinned parent
    # like Breast_L01, dropped entirely by add_bone) collapses the CHEST cloth to
    # the floor -- the same bug as the skirt anchor, one chain over. add_bone
    # adds only the SKINNED breast tip (Breast_L02) flat and never its parent;
    # walking the source tree restores the whole chain at chest height. Gated on
    # a custom chain existing so plain (non-physics) body armour is untouched --
    # there the actor's live skeleton overrides the flat bind at runtime. #177
    custom = [b for b in bone_names if not _is_skeleton_bone(b)]
    # AUTO-SELECT flat vs nested per armor (#flatchain-auto). Decide by where the
    # GARMENT's OWN physics chains anchor -- EXCLUDING soft-body jiggle bones
    # (breast/butt/belly are body bones the converter grafts on, NOT garment
    # chains; including them would make every conforming armor read as spine-
    # anchored). A chain anchored on the UPPER body -> NESTED; lower-body-only
    # (pelvis/thigh) -> FLAT. Env CBBE2UBE_NESTED_CHAIN_ANCHORS=1 forces nested.
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
        # Physics garment -> restore the FULL source skeleton hierarchy for ALL
        # skinned bones (not just the soft-body jiggle bones), so the converted
        # bone tree matches the known-good source rig EXACTLY. HDT-SMP walks the
        # NIF bone tree to build its kinematic chain; the collision bones
        # (arms/legs/spine) being properly nested -- not flat under Scene Root --
        # is part of that. Plain (non-physics) armour has no custom chain, so
        # this whole block is skipped and the flat skeleton is kept (fine there:
        # the actor's live skeleton drives the skin by name at runtime). #177
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
            # Stop at the first HARD skeleton bone (a soft-body bone is part of
            # the chain we recreate, not its anchor) -- so e.g. the breast chain
            # Breast_L02 -> Breast_L01 anchors on Spine2, not on Breast_L01.
            if par_name is None or (_is_skeleton_bone(par_name)
                                    and not _is_soft_body_physics_bone(par_name)):
                if par_name:
                    anchors.add(par_name)
                break
            cur = par_name
    if not chain:
        return 0
    pyn = _pynifly()
    existing = set(dst_nif.nodes.keys())
    added = 0
    # Re-create each anchor's FULL SOURCE ANCESTOR CHAIN (anchor -> parent -> ...
    # -> root) with source LOCAL transforms + parent links, so the physics
    # chain's kinematic parent is properly NESTED in the skeleton tree -- NOT a
    # flat node parented straight to Scene Root. Two reasons the bind global
    # alone is not enough: (1) a flat anchor still has the right global so the
    # bind looks fine, but (2) HDT-SMP walks the NIF BONE HIERARCHY to build its
    # kinematic chain, and an anchor whose parent is Scene Root (instead of
    # Spine1->Spine->COM...) breaks that chain -> the cloth destabilises/collapses
    # in-game even though every bone sits at the correct position. The source
    # skeleton nests Spine2 under Spine1->Spine->COM (and Pelvis under COM, etc.);
    # reproducing that exactly is what makes the converted NIF behave like the
    # known-good source rig. Harmless for static armour (actor overrides at
    # runtime) and only runs for physics garments (custom chain present). #177
    for a in anchors:
        if not use_nested:
            # DEFAULT (#flatchain): recreate the anchor AND its full source
            # ANCESTOR chain (anchor -> parent -> ... -> root), each at its SOURCE
            # GLOBAL transform but FLAT-parented (parent=Scene Root) -- NOT nested.
            # WHY both: the #177 NESTED static tree is what breaks elven (FSMP
            # anchors the chain to the static skeleton copy instead of the live
            # actor) -> flat parenting fixes that. But recreating ONLY the
            # immediate anchor flat broke the Traveling Mage robe, whose
            # accessory chains (bag/book/bottle off the Spine) need the deeper
            # skeleton bones (COM/Root) to EXIST at the right place -- so we walk
            # the whole ancestor chain and recreate every bone at its correct
            # source-global position, flat (each its own root child, actor-driven
            # by name). Gives nested-needing armor every bone it needs WITHOUT the
            # static tree that breaks flat-needing armor. #flatchain-complete
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

    Bone-based check covers shapes properly rigged with hand/finger
    bones. Name-based check (added 2026-05-25) catches stylized
    gauntlets that are rigged only to UpperArm or Forearm but should
    still NOT scale with body sliders.
    """
    # Name-based detection FIRST (independent of skinning) — catches stylized
    # gauntlets / gloves rigged only to UpperArm/Forearm (no hand bones).
    name_low = (getattr(src_shape, "name", "") or "").lower()
    if any(kw in name_low for kw in HAND_FOOT_NAME_KEYWORDS):
        return True
    # Bone-based detection: the shape must actually CARRY hand/foot geometry,
    # not merely GRAZE an extremity bone. A floor-length robe/dress weights its
    # hem to NPC L/R Foot at a fraction of a percent (0 verts actually
    # controlled by the foot bone); a boot ENCASES the foot (hundreds of foot-
    # dominant verts). The old "any extremity bone present" test misfired on the
    # robe and routed a physics-cloth garment down the RIGID hand/foot path
    # (full warp + no chain-nowarp + leg-only scale bones) -> the skirt chain
    # collapsed / fell through the floor (#177). So require a real CLUSTER of
    # verts whose weight is MAJORITY on extremity bones.
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


# Fraction of a shape's total vertex weight that must lie on hand/finger/
# foot/toe bones for the shape to count as an EXTREMITY SKIN mesh — the
# actual hand or foot, not an arm/leg shell piece that merely grazes a
# finger bone. Extremity skins are EXCLUDED from the per-armor body-morph
# TRI: body sliders (breasts/butt/belly/thighs) must NOT deform fingers
# or toes. A nude UBE actor's separate Hands/Feet meshes don't body-morph
# either, so matching that behavior is correct.
#
# Empirical separation is wide (a long-sleeve gauntlet NIF):
#   Hands_2 = 97%, Gloves_1 = 71%   -> excluded (finger meshes)
#   Bracers = 0.8%, Guards = 0.2%, Armstrap = 0.3%, boots = 21% -> kept
# so 0.5 sits comfortably in the gap.
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

    # K-nearest for body-weight propagation. Over-fetch (k * 4) so we
    # can filter out body verts on the "wrong side" of the armor —
    # in concave regions like between legs, naive Euclidean K-NN
    # picks body verts on the OTHER leg, which would mix left-leg
    # and right-leg body bone weights into the armor vert, causing
    # the armor to deform with the wrong limb. This is the geodesic-
    # distance approximation: instead of true surface walking, we
    # over-fetch candidates and reject those whose body normal
    # disagrees with the dominant direction from the armor vert
    # back to the body surface.
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
        # Over-fetch for filtering, then narrow to k_eff valid candidates.
        k_query = min(k * 4, body_n)
        cand_d, cand_idx = tree.query(armor_verts, k=k_query)
        if k_query == 1:
            cand_d = cand_d[:, None]; cand_idx = cand_idx[:, None]

        # For each candidate, compute the body normal's agreement with
        # the dominant (K=1 nearest) candidate's normal. Reject if it
        # disagrees by more than ~60° (cos < 0.5) — those are
        # wrong-side neighbors in concave regions.
        ref_normal = body_normals[cand_idx[:, 0]]  # (N_armor, 3)
        cand_normals = body_normals[cand_idx]      # (N_armor, K_query, 3)
        agree = (cand_normals * ref_normal[:, None, :]).sum(axis=-1)  # (N_armor, K_query)
        valid_mask = agree > 0.5

        # Pick the first k_eff valid candidates per armor vert; if a vert has
        # fewer than k_eff valid neighbours, fall back to the unfiltered first
        # k_eff. Vectorized (was a per-vert Python loop — slow on dense shapes):
        # a STABLE argsort of ~valid_mask brings valid columns to the front in
        # their original order, so order[:, :k_eff] == valid_i[:k_eff] for rows
        # with enough valid; rows without fall back to arange(k_eff). Exactly
        # reproduces the loop (verified byte-identical).
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
# Layered cloth in the SAME NIF (e.g., bra cup sitting just under an outer
# halter top in a layered-bust top NIF) follows the body's breast
# bones via skin weights. After scale-bone reskin the inner and outer
# layers end up with slightly different bone weight distributions, so
# under HDT-SMP physics each layer jiggles with a slightly different
# amplitude and they pass through each other at the cleavage seam (visible
# as Z-fighting / shimmer / mesh poke-through).
#
# Fix: in the upper-chest region, pick the LARGEST cloth shape as the
# authority and copy its per-vertex bone weights to overlapping nearby
# verts in every other cloth shape. The layers then have identical weights
# at the overlap and move identically under physics — no intersection.
# Verts outside the chest region are untouched, so the rest of the armor
# (skirt drape, sleeves, hem) keeps its independent motion.

# Tuned for upper-chest / cleavage region, anterior only (back of body
# isn't touched). Z range covers the cup + neckline (UBE breast sits
# ~90-110 world Z); X bound keeps it within the bust width; Y >= -2 keeps
# it anterior (front torso).
CHEST_SYNC_Z_MIN = 85.0
CHEST_SYNC_Z_MAX = 115.0
CHEST_SYNC_X_BOUND = 15.0
CHEST_SYNC_Y_MIN = -2.0

# Max distance from receiver vert to authority vert for a sync to fire.
# Layered cloth typically sits ~0.5-2u apart; cross-piece gaps (e.g., bra
# vs separate pauldron) are >5u. 2.5u is the sweet spot.
CHEST_SYNC_DISTANCE = 2.5

# Minimum breast-bone weight fraction (over a shape's cleavage verts) for
# the shape to count as a bust LAYER eligible for weight sync. Bust cloth
# (bra / cup / fabric over the bust) is heavily breast-driven; decorative
# attachments (cloak, shoulder pad, strap) have only incidental breast
# weight. Gating on this stops the sync from rewriting ornaments to the
# bust garment's breast-heavy weights (the mashup-armor regression). Measured
# separation is clean: genuine bust cloth >=0.34 (a layered-bust armor the inner bra shape 0.35, the outer fabric shape
# 0.53; a mashup armor Blouse 0.34), decorative attachments <=0.11 (Cape 0.11,
# Shoulders 0.00, Strap 0.01). 0.25 sits in the gap. Tune if needed.
CHEST_SYNC_MIN_BREAST_FRAC = 0.25


CHEST_DEPTH_SEPARATION = 0.4     # clearance the inner layer is pushed to,
                                  # bumped 0.15 -> 0.4 (2026-05-29) so the
                                  # inner bra clears the outer top instead of
                                  # poking through. Front-tolerance gate below
                                  # still spares clearly-proud ornaments.
                                  # (History: 0.5 sank ornaments -> 0.15 was
                                  # too shallow, bra still poked -> 0.4.)
CHEST_DEPTH_FRONT_TOL = 0.2      # ONLY push receiver verts within this
                                  # distance IN FRONT of the authority. A
                                  # vert that pokes just barely through
                                  # (e.g. the inner bra layer at +0.17u) is genuine
                                  # Z-fighting -> push it back. A vert
                                  # clearly in front (cloak / shoulder pad /
                                  # strap layered ON TOP of the base, often
                                  # >0.3u proud) is a legitimate outer piece
                                  # -> LEAVE IT. Without this cap the pass
                                  # yanked a mashup armor's on-top ornaments back
                                  # behind the cuirass cups. The whole push
                                  # band is therefore (-SEPARATION, +TOL):
                                  # near-coplanar fighting verts only.
CHEST_DEPTH_PAIR_XZ_DIST = 3.0   # max (X,Z) distance for "this inner vert
                                  # is co-located with this outer vert".
                                  # Larger than typical inter-vert spacing
                                  # (~1u) so the lookup finds an authority
                                  # neighbor; smaller than typical cross-
                                  # piece gap (>5u) so unrelated cloth on
                                  # the other side of the body doesn't
                                  # get matched.


def _separate_chest_layered_cloth_depth(
        shape_jobs: list,
        body_verts: "np.ndarray | None" = None,
        body_normals: "np.ndarray | None" = None,
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
# bra+fabric. The WAIST routinely stacks 3+ overlapping layers (a base top +
# corset + sash + metal belt) that the warp lands at nearly the SAME depth, so
# they Z-fight = the "crumpled/jumbled gold abdomen" (DDV Ruby). Two differences
# from the chest pass: (1) N layers, each needs its OWN distinct depth (not all
# collapsed to one authority plane); (2) authority must be the OUTERMOST layer by
# body-clearance, NOT the largest (the largest is often the base under-garment,
# and pushing the gold behind it re-hides it). Algorithm: sort overlapping front-
# waist layers outer->inner by median body-clearance; keep the outermost; push
# each inner layer behind the union of already-placed outer layers by
# ABDOMEN_SEP_GAP, clamped to ABDOMEN_SEP_BODY_FLOOR so nothing sinks into the
# body. Front-center only ((X,Z) pairing can't disambiguate front vs back).
ABDOMEN_SEP_Z_MIN = 66.0
ABDOMEN_SEP_Z_MAX = 96.0
ABDOMEN_SEP_X_BOUND = 22.0
ABDOMEN_SEP_Y_MIN = -3.0
ABDOMEN_SEP_GAP = 0.15          # min depth between consecutive overlapping layers
ABDOMEN_SEP_MAX_PUSH = 0.8      # cap total outward push (avoid runaway puff-out)
ABDOMEN_SEP_PAIR_XZ = 2.5       # max (X,Z) dist to call two layers "same spot"


def _separate_abdomen_layered_cloth_depth(
        shape_jobs: list,
        body_verts: "np.ndarray | None" = None,
        body_normals: "np.ndarray | None" = None,
) -> int:
    """Multi-layer depth separation for the front waist/abdomen. See the block
    comment above.

    SMOOTH (uniform per-layer) offset — this is the key vs an earlier per-vert
    version that crumpled the surface: moving only the verts that overlap an
    inner layer (by varying amounts) makes a contiguous rigid piece (a gold
    corset) look like crushed foil. Instead, sort the overlapping front-waist
    cloth layers innermost->outermost, leave the innermost (base) put, and give
    each successive OVERLAPPING layer a CONSTANT outward offset (level x GAP)
    applied uniformly to all its band verts along their (smoothly-varying) body
    normals. A uniform shell offset preserves the layer's shape (no crumple)
    while still spacing the layers apart so they stop Z-fighting. Layers that
    don't actually overlap a lower layer are left alone (not lifted off the
    body). Mutates offset jobs' `verts` in place + sets `verts_modified`."""
    if body_verts is None or body_normals is None:
        return 0
    try:
        from scipy.spatial import cKDTree
        bva = np.asarray(body_verts, dtype=np.float64)
        bna = np.asarray(body_normals, dtype=np.float64)
        btree = cKDTree(bva)

        cands = []
        for j in shape_jobs:
            if not j.get("override_skin"):
                continue  # only reskinned cloth (excludes the injected body)
            v = j.get("verts")
            if v is None or len(v) == 0:
                continue
            v = np.asarray(v, dtype=np.float64)
            mask = ((v[:, 2] >= ABDOMEN_SEP_Z_MIN) & (v[:, 2] <= ABDOMEN_SEP_Z_MAX)
                    & (np.abs(v[:, 0]) <= ABDOMEN_SEP_X_BOUND)
                    & (v[:, 1] >= ABDOMEN_SEP_Y_MIN))
            if int(mask.sum()) < 5:
                continue
            bvm = v[mask]
            _, bi = btree.query(bvm, k=1)
            med = float(np.median(((bvm - bva[bi]) * bna[bi]).sum(axis=1)))
            cands.append({"job": j, "mask": mask, "med": med})
        if len(cands) < 2:
            return 0
        cands.sort(key=lambda c: c["med"])  # innermost (base) first

        # `lower_xz` accumulates the (X,Z) of every layer at or below the current
        # one. `level` = how many overlapping layers we've already offset (the
        # base is level 0 and never moves).
        lower_xz = np.asarray(cands[0]["job"]["verts"],
                              dtype=np.float64)[cands[0]["mask"]][:, [0, 2]]
        level = 0
        total = 0
        for c in cands[1:]:
            job, mask = c["job"], c["mask"]
            vfull = np.asarray(job["verts"], dtype=np.float64)
            idx_in_shape = np.where(mask)[0]
            pts = vfull[mask]
            # Does this layer meaningfully overlap a lower layer (in X,Z)?
            tree = cKDTree(lower_xz)
            dd, _ = tree.query(pts[:, [0, 2]], k=1,
                               distance_upper_bound=ABDOMEN_SEP_PAIR_XZ)
            overlap_frac = float((dd < ABDOMEN_SEP_PAIR_XZ).mean())
            if overlap_frac >= 0.2:
                level += 1
                offset = min(level * ABDOMEN_SEP_GAP, ABDOMEN_SEP_MAX_PUSH)
                _, bi = btree.query(pts, k=1)
                vfull[idx_in_shape] += offset * bna[bi]   # uniform smooth shell
                job["verts"] = vfull
                job["verts_modified"] = True
                total += len(idx_in_shape)
            # this layer joins the lower set for subsequent layers
            lower_xz = np.vstack([lower_xz,
                                  np.asarray(job["verts"],
                                             dtype=np.float64)[mask][:, [0, 2]]])
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
# tracking layer. The TMage CTD is handled by `_cap_skin_bone_count`; the
# Forsworn double by body-skin detection. Scale bones now go to ALL cloth again.)


# Skyrim's GPU skinning supports ~80 bones per skin PARTITION. A densely-rigged
# converted full-body suit can exceed it — the Fuse00 Traveling Mage body ends
# up at 82 bones, almost all in its slot-32 partition, so the bone-matrix
# palette overruns at draw -> EXCEPTION_ACCESS_VIOLATION (vmovdqa) in
# BSBatchRenderer = equip CTD. Cap BELOW the limit. This is the universal
# backstop that catches scale bones from ANY source (reskin transfer, the
# add_scale_bone_weights cloth/extremity paths, dense source rigs). #166
SKIN_PARTITION_BONE_CAP = 78


def _cap_skin_bone_count(bone_names, xforms_map, weights_map,
                         limit=SKIN_PARTITION_BONE_CAP):
    """If a shape references more than `limit` bones, keep the `limit` most
    LOCALLY-DOMINANT (by MAX per-vertex weight) and drop the rest, then
    renormalize each vertex so its weights still sum to 1.0. Returns the
    (possibly trimmed) skin tuple. Prevents overrunning Skyrim's per-partition
    GPU bone cap (render CTD). #166

    Rank by max-per-vert weight, NOT total weight: a bone that is the dominant
    influence on even a handful of verts — a robe SKIRT or cape physics chain
    bone (e.g. `TMage_Skirt_Back 04`, max ~0.75 on its 18 hem verts) — is
    locally critical; dropping it collapses those verts (distortion) and kills
    the skirt's HDT-SMP sway (no physics). A scale-bone "tracking" tail
    propagated thinly across the whole shape (max ~0.02) is the safe thing to
    drop. The original total-weight ranking did the exact opposite and evicted
    the Traveling Mage robe's skirt physics bones. Tie-break by total weight."""
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


def _cached_scale_bone_data(body_shape, leg_region_only: bool):
    """Build (and cache) the per-scale-bone KD-trees from the BODY. Keyed by the
    body-shape identity + leg_region_only (the only inputs). Armor-independent,
    read-only after build, so safe to share across every shape/NIF in a worker.
    Returns (scale_bones, {bone: (bone_verts, cKDTree, weights)})."""
    key = (id(body_shape), bool(leg_region_only))
    cached = _SCALE_BONE_DATA_CACHE.get(key)
    if cached is not None:
        return cached
    from scipy.spatial import cKDTree
    body_verts = np.asarray(body_shape.verts, dtype=np.float64)
    body_n = len(body_verts)
    scale_bones = [b for b in (body_shape.bone_names or []) if _is_scale_bone(b)]
    if leg_region_only:
        # Hand/foot: only LEG scale bones are anatomically legitimate (a boot's
        # calf follows RearCalf); torso bones would be bind-pose cross-talk.
        scale_bones = [b for b in scale_bones
                       if ("thigh" in b.lower() or "calf" in b.lower())]
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
    _SCALE_BONE_DATA_CACHE[key] = (scale_bones, bone_data)
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
    """
    # Auto-detect rigid attachments (dagger, scabbard, pauldron, pouch
    # etc. — single bone holds 85%+ of total weight) and use the low
    # transfer rate so their animation tracking to their parent bone is
    # preserved while still adding some morph response. Cloth shapes
    # (weight distributed across many bones, no single one dominant)
    # keep the aggressive default transfer.
    if _is_rigid_attachment(weights_by_bone):
        max_transfer = SCALE_BONE_MAX_TRANSFER_RIGID

    armor_verts = np.asarray(armor_verts, dtype=np.float64)
    armor_n = len(armor_verts)

    # Per-bone sparse KD-trees of the body verts weighted to each scale bone.
    # PER-BONE search (not one K-NN against all body verts) so an armor vert
    # whose K nearest body verts all LACK a bone still propagates from the
    # actual nearest bone-weighted vert (the slot-49 corset / NPC Belly case).
    # These are BODY-derived and CACHED across shapes (see _cached_scale_bone_data).
    scale_bones, bone_data = _cached_scale_bone_data(body_shape, leg_region_only)
    if not bone_data:
        return bone_names, xforms_by_bone, weights_by_bone

    # Existing per-vert total weight (should be ~1.0 if shape was normalized).
    # While we're iterating, track each vert's DOMINANT authored bone. Used by
    # (a) the torso-parity boost below, which skips ARM/HAND-dominated verts so
    # it can't amplify cross-talk, and (b) the #133 suppression, which drops
    # breast/belly/butt propagation onto arm-dominated verts entirely (a forearm
    # bracer / sleeve must not track the breast/butt). Always computed (cheap;
    # one pass we already make) so the #133 guard works on the cloth path too,
    # where torso_parity is off.
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
    # LEG-ENCASING ARMOR (greaves / leggings / pants): if the MAJORITY of verts
    # are dominated by rigid leg bones this is a rigid leg plate. The body's
    # breast/butt/belly PHYSICS jiggle must not drive it -- it gets dragged down
    # and collapses on the UBE race (wolf Greaves: source 0% on jiggle bones,
    # converter put up to 48% on the hip/waist band). Suppress the jiggle scale
    # bones for the whole shape below; the STATIC leg-shape scale bones
    # (frontthigh/rearthigh/rearcalf) still apply so it follows size sliders.
    # Shape-level (not per-vert) on purpose: the collapsing hip/waist band is
    # PELVIS-dominated, not leg-dominated -- only the shape AS A WHOLE reads as
    # "leg armor". #physcollapse
    leg_armor = armor_n > 0 and int(leg_vert.sum()) > 0.5 * armor_n

    # Per-armor-vert proposed scale-bone weights — propagate from each
    # scale bone's own neighborhood independently. We take the MAX of
    # the K nearest body verts' weights (then multiply by linear falloff)
    # rather than IDW-averaging, because IDW dilutes the body's per-vert
    # weight magnitude — the body's NPC Belly peaks at only 0.183 per
    # vert, so a K=8 IDW average is ~0.115. Cloth verts at that propagated
    # magnitude move 60-65% as much as the body when the user pushes
    # the Belly slider, which causes visible body-poke-through.
    # Using the MAX preserves the body's actual weight at the cloth
    # vert: when the nearest bone-weighted body vert has weight 0.18
    # and the cloth vert is right next to it, the cloth gets 0.18 *
    # falloff ≈ 0.16, very close to 1:1 with the body. The bone's max
    # weight in 3BA is bounded (no body vert exceeds ~0.2 for any single
    # scale bone), so MAX can't pathologically over-weight cloth.
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
            # Power curve on torso verts; ARM/HAND-dominated verts (sleeve /
            # gauntlet geometry) keep the original LINEAR curve so the boost
            # can't strengthen bind-pose arm<->torso cross-talk (#133).
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
        # #133: never let an ARM/HAND-dominated vert inherit torso (breast/
        # belly/butt) scale weight -- that's bind-pose cross-talk that spikes
        # the piece when the slider moves. Independent of slot/torso_parity.
        if SUPPRESS_TORSO_SCALE_ON_ARMS and _is_torso_parity_bone(bn):
            prop = np.where(arm_vert, 0.0, prop)
        if prop.max() <= 1e-6:
            continue
        proposed_scale[bn] = prop

    # Zero out scale-bone weight on excluded verts (finger/toe geometry on
    # hand/foot shapes). Done BEFORE the cap/occupy math so those verts keep
    # keep_fraction == 1.0 — their original finger/thumb/toe skinning is
    # left fully intact and no body scale bone is injected onto them.
    if exclude_vert_mask is not None:
        em = np.asarray(exclude_vert_mask, dtype=bool)
        if em.shape[0] == armor_n:
            for bn in list(proposed_scale.keys()):
                proposed_scale[bn] = np.where(em, 0.0, proposed_scale[bn])

    # Drop any bone that ended up entirely zeroed (e.g. butt weight that
    # only reached finger verts now masked out) so it isn't added as a
    # dead bone with no influence.
    proposed_scale = {bn: p for bn, p in proposed_scale.items()
                      if float(p.max()) > 1e-6}
    if not proposed_scale:
        return bone_names, xforms_by_bone, weights_by_bone

    # Cap the SUM of proposed scale weights per vert at max_transfer
    # of existing — so we never push more than half the armor vert's
    # influence onto scale bones (the rest stays on the armor's
    # rigid skeleton bones).
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

    # Now shrink existing armor weights by the SAME fraction the
    # proposed scale will occupy. After this, total per vert still
    # sums to existing_per_vert (typically 1.0).
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
    # If the shape already references the GPU bone cap, do NOT inject any NEW
    # scale bone -- adding one forces _cap_skin_bone_count to evict an existing
    # bone (which can be a body-morph or physics bone the piece needs), making
    # the armour WORSE. A 3BA-built source already carries its morph bones, so
    # we still add scale WEIGHT to those (existing) bones below; we only refuse
    # to grow the bone list past the cap. New scale bones are admitted only
    # while there is still room under the cap.
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


# --- Chain-cloth -> soft-body conversion (gated option, 2026-06-05) --------
# Authored HDT-SMP bone-CHAIN cloth (skirts/tassels rigged to custom
# Skirt_NN / FR_NN chain bones + <generic-constraint> springs) reliably
# COLLAPSES TO ORIGIN on the UBE race at runtime (wolf, ebony mail, cape —
# all verified faithful copies; the chain just doesn't survive the UBE
# actor). Per-vertex soft-body cloth, by contrast, works on UBE (it rides
# the STANDARD body skeleton + body-physics scale bones, which the UBE race
# has). This switch converts the former into the latter:
#
#   ON  -> every chain-preservation path is neutralized, so chain shapes
#          fall through to the NORMAL cloth pipeline: body-fit reskin
#          (compute_body_blend_skinning) + 3BA scale-bone weights
#          (add_scale_bone_weights) give the cloth body-driven motion +
#          body-physics jiggle, and `_generate_hdt_xml_for_dst` emits a
#          collision-only per-vertex XML (no chain). Stable on UBE.
#   OFF -> authored chains preserved verbatim (today's behaviour).
#
# Trade-off: the cloth no longer has its INDEPENDENT authored swing (the
# chain is gone); it follows the body + jiggles with body physics. That's
# the price of stability on UBE — keeping the authored chain has failed
# verification on every test armor. Gated OFF so default output is byte-for-
# byte unchanged. Toggle at RUNTIME (no rebuild) via the env var
# CBBE2UBE_CHAIN_TO_SOFTBODY=1 — read at import so it propagates into the
# ProcessPool conversion workers (they inherit the parent env). Set it
# before launching the exe to convert affected armor as soft-body; leave it
# unset for normal output. See the 5 `if CHAIN_TO_SOFTBODY` guards below in:
# _shape_has_hdt_smp_rigging, _hdt_softbody_shape_names,
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
           push it inward by `threshold/2`
         - the other ("outer") is left at original position
         (This makes layer separation `threshold/2` instead of doubled.)

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
# UBE 2.0 ships BaseShape with 5+ open boundary loops at the pubis (main
# pubic opening, anus, vagina, and a few small detail features) — UBE
# was authored expecting a TNG / SoS-style plug-mesh to render on top.
# Without that plug, the holes are exposed visually under any lighting
# that doesn't happen to mask them. _close_pubic_holes seals the loops
# with fan triangulation using ONLY existing boundary verts (no new
# verts added — injected BaseShape stays vert-identical to nude render).
# The bounding box must exclude the NECK (Z=60-114 wide loop where
# FemaleHead plugs in), WRIST (X up to ±28 where Hands plug in), and
# ANKLE (Z=11-63 where Feet plug in) — those are DESIGNED-open loops
# that other shapes attach into.
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
    game. Proven decisively: the ORIGINAL (un-warped) Traveling Mage skirt has
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


def _copy_shape(src_shape, dst_nif, parent=None, override_verts=None,
                override_skin=None, skip_alpha=False, override_tris=None):
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
    # createShapeFromData requires verts as a sequence of tuples; numpy
    # rows (when override_verts is an ndarray) trigger
    # "expected c_float_Array_3 instance, got numpy.ndarray".
    if override_verts is not None:
        ov = np.asarray(override_verts)
        use_verts = [tuple(float(c) for c in row) for row in ov]
        # Recompute normals from the new geometry — source normals
        # are stale relative to snap/inflate-modified vert positions
        # and produce speckled / noisy shading on the moved verts.
        # Pass source normals as a sign reference so boundary verts
        # at topology holes (where the recompute is underdetermined)
        # don't flip inward and back-face-cull their triangles. See
        # `_recompute_vertex_normals` boundary-vert fix note.
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

    # Triangle list: use override if provided, else source. With
    # override_tris we KEEP the source per-vert normals as-is — the
    # new fill triangles inherit existing vert normals (pointing
    # outward from the body), giving the sealed surface continuous
    # shading with the surrounding mesh. No recompute needed because
    # verts haven't moved.
    if override_tris is not None:
        ot = np.asarray(override_tris, dtype=np.int64)
        use_tris = [tuple(int(c) for c in row) for row in ot]
    else:
        use_tris = list(src_shape.tris)

    # #scalebake: bake a non-identity GEOMETRY transform (scale/rotation) into
    # the verts + normals so the output is an identity-transform skinned shape
    # (skin-to-bone is adjusted below to preserve the bind). No-op for the
    # identity transforms normal armor ships with.
    _bake_T = _shape_bake_matrix(src_shape)
    # Pure-translation bake (bind-CHANGING, no STB adjust) ONLY on the copy path:
    # override/rebuild verts are already body-positioned by the warp, so adding
    # the source translation again would over-lift them. #scalebake-translation
    # Shapes WITH a global_to_skin go through _align_scale_bone_stbs_to_verts
    # instead (the real fix for skinned breast/sleeve sag is in the per-bone
    # STBs, not a whole-shape vert lift) -- skip the vert-lift bake for them so
    # the two corrections never double-apply. #breast-stb
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
        # Lift the verts by the engine-ignored shape translation so the skin
        # renders them at the intended (body) position. Normals are unchanged
        # (a pure translation does not rotate them).
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
    if _bake_T is not None or _bake_trans is not None:
        # props carried the source's non-identity transform -> reset to identity
        try:
            _idt = _pynifly().TransformBuf()
            _idt.set_identity()
            new_shape.transform = _idt
        except Exception:
            pass

    # Shader: copy known value fields (flags, glossiness, etc.). DO NOT
    # memcpy the whole struct because source has block-ID fields
    # (textureSetID, controllerID, nameID, ...) that point at SOURCE-NIF
    # blocks which don't exist in our destination — carrying them over
    # produces a NIF the loader rejects.
    #
    # The list below is the value fields that affect rendering. Adding to
    # it is safe as long as we don't include any *ID / count fields.
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
    try:
        src_shader = src_shape.shader
        if src_shader is not None and src_shader.properties is not None:
            new_shader = new_shape.shader
            src_props = src_shader.properties
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

    # Textures — src_shape.textures is a dict {slot_name: path}, NOT a list.
    # Must be done AFTER shader memcpy because set_texture mutates the
    # shader's texture slots, but does so on the destination shader buffer.
    for slot_name, tex_path in (src_shape.textures or {}).items():
        if tex_path:
            new_shape.set_texture(slot_name, tex_path)

    # Skin instance.
    # IMPORTANT ordering per pynifly's add_bone docstring:
    #   "This resets all the shape's bone information, so skin-to-bone
    #    transforms and bone weights will need to be reset. Add all bones
    #    first, then set transforms and weights."
    # Previous version passed xform= to add_bone, which got clobbered by
    # the next add_bone call. That meant all skin-to-bone matrices defaulted
    # to identity at save time -> in-game the mesh exploded into long
    # spikes because verts weighted to non-identity bones snapped to bone
    # origins. Two-pass approach (add then set) fixes that.
    #
    # If `override_skin` is provided (= dict with keys 'bones', 'xforms',
    # 'weights'), use those instead of pulling from src_shape. Used by the
    # M6 proximity-blend re-skin path to install the blended weights in a
    # SINGLE skin setup — calling skin()+add_bone again after _copy_shape
    # corrupts the bone/weight mapping inside pynifly, so the re-skin must
    # happen in this one pass.
    if override_skin is not None:
        bone_names = override_skin["bones"]
        xforms_map = override_skin["xforms"]
        weights_map = override_skin["weights"]
        # Backstop: never exceed Skyrim's per-partition GPU bone cap (render
        # CTD on equip — Fuse00 Traveling Mage body = 82 bones). #166
        bone_names, xforms_map, weights_map = _cap_skin_bone_count(
            bone_names, xforms_map, weights_map)
        _install_skin(new_shape, dst_nif, src_shape, bone_names,
                      xforms_map, weights_map, use_verts, _bake_T)
    elif src_shape.bone_names:
        # Build the skin maps from source. A VERBATIM-copied source shape can
        # reference > the per-partition GPU bone cap — dense modded skirts /
        # dresses / robes ship 79-81 bones (Infantryman/Magecore/vampire/Kreis)
        # -> palette overrun at draw -> equip CTD. We now KEEP every source bone
        # and let the post-save `_split_oversize_partition` pass split the shape
        # into <=cap-bone partitions instead of DROPPING bones (which evicted the
        # body-morph bones a dress needs -> stopped tracking the morph). The cap
        # is per-PARTITION, so the same mesh across two partitions is in budget.
        # EXCEPTION: shapes the post-save pass SKIPS (body-inject / VirtualGround)
        # can't be split later, so they still get the in-build trim as the CTD
        # backstop. #177 (split) / #168 (was: trim).
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
            # CTD-safe without dropping any (Magecore dress: 80 bones -> 78+9).
            bone_names, xforms_map, weights_map = _vb_bones, _vb_x, _vb_w
        _install_skin(new_shape, dst_nif, src_shape, bone_names,
                      xforms_map, weights_map, use_verts, _bake_T)

    # Alpha — Option D / Fix 2.
    # Previous version called save_alpha_property() without first creating
    # the dst alpha property. Since pynifly's save only persists if
    # `_alpha` is set, the call was a silent no-op — destination shapes
    # ended up with has_alpha_property=False even when source had it
    # (visible on a heavily-boned armor's Neck_Fur_Shell_1..4 layered transparent fur).
    #
    # Correct sequence: assign `has_alpha_property = True` first to make
    # pynifly construct a fresh NiAlphaProperty on dst, then copy the
    # value fields (flags, threshold) from src's alpha buf, then save.
    # Don't memcpy the whole buf — it contains block-ID fields (nameID,
    # controllerID, extraDataCount) that point at SOURCE-NIF blocks.
    # Alpha — skip entirely if `skip_alpha=True` (cloth shapes that
    # should morph via NioOverride BodyMorph). NioOverride empirically
    # gates morph application on absence of NiAlphaProperty: shapes
    # with an alpha block stay static even when their per-shape TRI
    # entry has correct morph data. Hand-authored morphable UBE cloth
    # ships WITHOUT alpha properties. Skipping the copy means trim
    # transparency or alpha-tested edges render opaque, but morphs
    # work. The trade-off is what we want for cloth shapes; for non-
    # morphable shapes (metals, jewelry), keep the alpha copy.
    if src_shape.has_alpha_property and not skip_alpha:
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

    # NOTE: we don't call save_shader_attributes() — that adds a new
    # shader block, leaving the auto-created one orphaned and (more
    # importantly) the new block has dangling IDs. The shader property
    # mutations above are written to the same buf the file mirrors, so
    # they round-trip correctly via NifFile.save().

    # Preserve the SOURCE skin-instance TYPE. createShapeFromData ALWAYS makes a
    # BSDismemberSkinInstance, but HDT-SMP collision proxies (BodyColSkirt/Cape)
    # and some cloth pieces (belts) ship with a plain NiSkinInstance in source.
    # A BSDismemberSkinInstance carries a dismember (body-part slot) partition a
    # NiSkinInstance does not; forcing it onto an SMP collision/cloth shape
    # changes how the engine + HDT-SMP treat the shape, which is the long-
    # standing #177 "NiSkinInstance -> BSDismember" physics-break root cause.
    # Demote back so the converted shape's skin-instance type matches source
    # exactly. (Demote drops the dismember partition but keeps bones/weights/
    # skin-to-bone + the GPU NiSkinPartition.)
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

    # Score each XML against the NIF's stem. Require a NIF↔XML KEYWORD
    # match — directory proximity alone isn't enough (boots
    # shouldn't pick up the breast physics XML just because they share
    # a folder).
    best_xml = None
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


def _generate_hdt_xml_for_dst(dst_path: "Path") -> "str | None":
    """Generate a fresh HDT-SMP cloth-collision XML for the destination
    NIF, write it alongside the NIF, and return the Skyrim-relative
    path string (suitable for the `HDT Skinned Mesh Physics Object`
    root extra-data).

    Returns None only when there's genuinely nothing to simulate:
      * NIF has no cloth shapes the converter recognizes (use the
        same `_pick_bodytri_carriers` filter as the BODYTRI machinery
        — that's our agreed definition of "cloth that should track
        the body")

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

    # Reuse the BODYTRI carrier picker as the "cloth shape" classifier:
    # every textured, non-placeholder, non-rigid-prop shape qualifies.
    carriers = _pick_bodytri_carriers(nf)
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
    the original armor mod (e.g. "MAGECORE - hdt SMP"). Resolving only against
    the NIF's own mod root misses it -> the converter falls back to a GENERIC
    XML that doesn't drive the custom chain (Magecore "pulls to origin", cloak
    clip, Ruby skirt). Returns the Path or None.
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
        if mroot is not None and mroot.is_dir():
            for mod in sorted(d for d in mroot.iterdir() if d.is_dir()):
                cand = mod / norm
                if cand.is_file():
                    return cand
    except OSError:
        pass
    return None


def _read_source_hdt_xml_disk(src_nif_path: Path) -> "Path | None":
    """Resolve the source armor NIF's OWN `HDT Skinned Mesh Physics Object`
    extra-data string to a file on disk.

    This is the authoritative armor->XML link (the mod author wrote it),
    far more reliable than keyword-matching filenames — it correctly maps
    e.g. a heavily-boned armor_Female_Body_0.nif -> Meshes\\Fuse00\\Armor\\a heavily-boned armor\\a heavily-boned armor_Body.xml
    where the stems don't match. Resolves through the full VFS so an XML that
    ships in a different mod than the (BodySlide-output) NIF is still found.
    Returns the Path or None.
    """
    try:
        pyn = _pynifly()
        snf = pyn.NifFile(filepath=str(src_nif_path))
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


# --- Authored-physics param hardening (gated A/B fix, 2026-06-05) ----------
# Some hand-authored chain rigs swing fine on most actors but COLLAPSE on the
# UBE actor (wolf, furexarot ebony), while the converter handles them
# identically to rigs that work (dwarven, same source mod + same copy path +
# equivalent structure). We could not isolate a single offending field, so
# this is an EMPIRICAL fix: pull the fragile rig's stability params into the
# band the robust HDT-SMP-Vanilla-Armors rigs occupy, WITHOUT touching the
# chain structure (so the cloth keeps swinging — unlike the soft-body switch
# which removes the swing). Toggle: CBBE2UBE_HARDEN_PHYSICS=1 (default OFF,
# read at import so it reaches ProcessPool workers). Tune the bounds below.
HARDEN_AUTHORED_PHYSICS = (
    os.environ.get("CBBE2UBE_HARDEN_PHYSICS", "").strip().lower()
    in ("1", "true", "yes", "on")
)
# Mitigation for INCOMPLETE chain rigs (the wolf: missing its front SkirtF 4-7
# chains, so the chain cloth has no closed cross-link ring and falls). Gated
# per-mod (CBBE2UBE_STATIC_CHAINS=1): zero every dynamic chain mass so FSMP
# treats the chain bones as STATIC (kinematic). Static bones hold their bind
# pose relative to their NIF parent (Pelvis) -> the cloth keeps its authored
# shape and follows the body instead of free-falling. Trade-off: no swing.
# XML-only (no mesh reskin) so it can't repeat the cuirass regression, and it
# only touches the per-armor XML's chain masses (body collision has no <mass>).
# Apply by `convert`-ing ONLY the affected mod folder with this env set.
STATIC_CHAINS = (
    os.environ.get("CBBE2UBE_STATIC_CHAINS", "").strip().lower()
    in ("1", "true", "yes", "on")
)
PHYS_INERTIA_FLOOR = 70.0     # corpus stable inertia 70-150; raise the twitchy lows
PHYS_ANGDAMP_FLOOR = 0.9      # corpus angular damping 0.95-0.99; raise the under-damped
PHYS_STIFFNESS_CAP = 50.0     # corpus stiffness mode 20; cap runaway springs (ebony 200)
# THE measured differentiator (2026-06-05): chain rigs that COLLAPSE on UBE
# (ebony) allow LINEAR link stretch (linear*Limit = +/-0.1) while every rig
# that swings fine (bandit, dwarven) uses RIGID links (0). A stretchy heavy
# chain elongates under gravity + UBE-body collision -> skirt droops to the
# floor ("floats off / falls through"). Clamping linear limits to ~0 makes
# the links rigid; the ANGULAR limits (the sway) are left untouched, so the
# swing is preserved. This is the swing-keeping alternative to soft-body.
PHYS_LINEAR_LIMIT_MAX = 0.0   # rigid link length (working=0; failing ebony=+/-0.1)
# Only clamp SMALL accidental stretches (the ebony deviates from its rigid-
# link SkirtF siblings at +/-0.1..1). LARGE linear limits (>=this) are a
# deliberate free-flowing design (billowing cloaks/skirts at 15-25) and are
# LEFT ALONE — a library scan shows 150/228 chain rigs are stretchy, most of
# them intentionally, so a blanket clamp would stiffen flowing capes.
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
            Path(xml_path).write_text(t2, encoding="utf-8")
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
            Path(xml_path).write_text(t, encoding="utf-8")
        except Exception:
            pass


def _harden_hdt_xml_for_fsmp(xml_path: Path, nif) -> None:
    """FSMP-compatibility hardening of an output HDT-SMP XML (general
    hardening, 2026-05-29). Prunes references the engine can't resolve so
    Faster HDT-SMP never loads a dangling shape/bone:

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
            Path(xml_path).write_text("\n".join(out) + "\n", encoding="utf-8")
        except Exception:
            pass


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
                shutil.copyfile(str(src_xml), str(dst_xml_disk))
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

        # Collision-proxy preservation: the physics XML lists collision
        # geometry via <per-triangle-shape>/<per-vertex-shape>. The
        # converter drops textureless collision proxies (Col_Pants,
        # Col_Strips, ...), so HDT-SMP has nothing to collide the chains
        # against and they pass through each other (self-intersection).
        # Re-import any referenced collision shape that's missing from the
        # output, copied from the source NIF and flagged Hidden so it acts
        # as invisible collision geometry (same trick as VirtualBody).
        try:
            xml_text = dst_xml_disk.read_text(errors="ignore")
            col_names = set(re.findall(
                r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', xml_text))
            present = {s.name for s in nf.shapes}
            missing = [n for n in col_names if n not in present]
            # Physics-FRAMEWORK shapes (e.g. "Stabilizer"): textureless, carry
            # NO per-triangle/-vertex collision geometry of their own, but are
            # the ONLY source of certain physics BONES the XML drives (declared
            # <bone> / referenced in <generic-constraint bodyA|bodyB>). The
            # body-fit pass drops them as "textureless collision proxies", which
            # removes their bones from the worn armor's skeleton -> the skirt/
            # flap constraints anchored to those bones have NO target -> the
            # cloth falls and never settles (elven cuirass front drape). Restore
            # any source shape that supplies an XML-driven bone missing from
            # every surviving shape. Re-imported VERBATIM (no warp): unlike the
            # collision proxies these are just a vehicle to instantiate the
            # chain bones (recreated at SOURCE bind by _precreate_custom_bone_
            # chains via _copy_shape), so their verts must stay source-aligned.
            framework_names: set[str] = set()
            xml_bones = set(re.findall(r'<bone\s+name="([^"]+)"', xml_text))
            xml_bones |= set(re.findall(r'\bbody[AB]="([^"]+)"', xml_text))
            present_bones: set[str] = set()
            for _ps in nf.shapes:
                present_bones |= set(_ps.bone_names or [])
            needed_bones = xml_bones - present_bones
            if missing or needed_bones:
                snf = pyn.NifFile(filepath=str(src_nif_path))
                src_by_name = {s.name: s for s in snf.shapes}
                if needed_bones:
                    for _ss in snf.shapes:
                        if _ss.name in present or _ss.name in missing:
                            continue
                        _sb = set(_ss.bone_names or [])
                        if _sb & needed_bones:
                            missing.append(_ss.name)
                            framework_names.add(_ss.name)
                            needed_bones -= _sb
                # Warp re-imported collision proxies to the UBE body using the
                # SAME CBBE->UBE delta the cloth was warped with. Without this
                # the proxy stays at CBBE size while the cloth wraps the bigger
                # UBE body: the sim then collides the chains against geometry
                # that's too small / sits inward, so cloth droops or clips
                # into the body, and separate chains (straps vs skirt) rest at
                # mismatched radii and interpenetrate. Matching weight (_0/_1).
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
                        new_col = _copy_shape(src_shape, nf,
                                              override_verts=_col_ov)
                        # Hide it: HDT reads geometry for collision, the
                        # renderer skips Hidden (bit 0) shapes.
                        tgt = new_col if new_col is not None else next(
                            (s for s in nf.shapes if s.name == cn), None)
                        if tgt is not None:
                            cur = int(getattr(tgt, "flags", 0) or 0)
                            tgt.flags = cur | 0x1
                        dirty = True
                    except Exception as _e:
                        # Do NOT swallow silently: a dropped HDT physics shape
                        # (collision proxy OR framework like Stabilizer) breaks the
                        # armor's SMP at runtime (skirt/flap collapse). The big
                        # parallel run intermittently fails a re-import on a
                        # transient file lock (AV/MO2 scanning freshly-written
                        # NIFs) -- when that happens the user must SEE it so they
                        # can reconvert that NIF, instead of a mystery collapse.
                        import sys as _sys
                        print(f"  WARN: HDT physics shape '{cn}' failed to "
                              f"re-import into {dst_path.name}: {_e!r} -- SMP "
                              f"shape DROPPED; reconvert this NIF",
                              file=_sys.stderr)
        except Exception:
            pass

        if dirty:
            nf.filepath = str(dst_path)
            nf.save()

        # FSMP-compatibility hardening: prune the output XML so Faster HDT-SMP
        # never sees a shape/bone it can't resolve. Runs AFTER the proxy
        # re-import above so re-imported collision shapes count as present.
        # Uses the final in-memory nf (which includes any re-imported shapes).
        try:
            _harden_hdt_xml_for_fsmp(dst_xml_disk, nf)
        except Exception:
            pass
        # Optional empirical param-hardening (CBBE2UBE_HARDEN_PHYSICS=1):
        # clamp fragile chain rigs into the stable param band while keeping
        # their swing. No-op when the env switch is unset.
        try:
            _harden_physics_params(dst_xml_disk)
        except Exception:
            pass
        # Optional incomplete-rig mitigation (CBBE2UBE_STATIC_CHAINS=1):
        # make chain bones static so the cloth follows the body instead of
        # falling. No-op when unset. (Copy-path armor also re-applies this
        # after _reauthor.)
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


def _reauthor_nif_fresh(dst_path: Path) -> bool:
    """Re-author a NIF from scratch into a fresh NifFile — copy every shape
    via _copy_shape (clean pynifly authoring) instead of leaving the
    source-derived bytes produced by the verbatim `shutil.copy2` path.

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
        for s in shapes:
            try:
                _copy_shape(s, new)
            except Exception:
                pass
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
        new.save()
        import os as _os
        _os.replace(str(tmp_path), str(dst_path))
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


def _hdt_softbody_shape_names(src_nif_path: Path) -> set:
    """Shape names the armor's HDT-SMP XML drives as PER-VERTEX soft-bodies
    (free-swinging cloth, e.g. a hand-authored UBE armor's `a soft-body cloth shape`). These must KEEP
    their authored skin weighting: the converter's body-fit reskin would
    re-weight every vert firmly to body bones, over-constraining the
    soft-body so it can no longer swing/jiggle. Resolves the source XML via
    the NIF's own extra-data first, then keyword match. Empty set on any
    failure (reskin proceeds as normal)."""
    if CHAIN_TO_SOFTBODY:
        return set()  # soft-body mode: nothing is preserved; reskin all cloth
    try:
        xml_disk = _read_source_hdt_xml_disk(src_nif_path)
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
            return set()
        txt = xml_disk.read_text(errors="ignore")
        return set(re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt))
    except Exception:
        return set()


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
    CBBE2UBE_UBE_OSD.

    CACHED in `_BODY_DISCOVERY_CACHE`: this used to be called once PER NIF and
    each call iterates every installed mod (3000+ on a big list) twice — it was
    ~16% of warm convert time on a large modlist (176k stat() calls). The result is stable
    for the process, so resolve once."""
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
BODY_SKIN_EXTREMITY_NAMES = BODY_SKIN_HAND_NAMES | BODY_SKIN_FOOT_NAMES

# Detection patterns (broader than the exact-name sets above). Outfit
# Studio / mod authors frequently suffix a duplicated body-skin hand/foot
# shape — a mashup armor's gauntlet ships its CBBE hand as "Hands_2", a heavily-boned armor-
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
    """Whether a source shape should be skipped at convert time as a
    "vestigial mashup leftover" — see call sites at the shape-iter loop
    in phase 1 (~line 1170) and phase 2 (~line 5439).

    RESTORED 2026-05-29 as no-op stub after accidental removal during
    the merge-pipeline cleanup (#143). Pre-deletion behavior was
    consistent with returning False (MaleUnderwearBody:0 was already
    surviving into output, so the original function — if it filtered at
    all — had narrow matches that didn't include the obvious
    UnderwearBody case the comment mentions). Leaving as a no-op keeps
    current observable behavior intact. Add real drop rules here if a
    specific vestigial shape needs filtering."""
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
    # BaseShape injection: ON by default. The injected UBE BaseShape
    # is what makes the body under the armor a UBE body (proper UBE
    # silhouette + UBE topology + UBE genital region as bone-driven
    # geometry). Skyrim's slot-32 mechanic hides the actor's own
    # femalebody when this armor equips — so without injection the
    # player would have NO body under the cloth at all.
    #
    # Note for CBBE-3BA users: your femalebody.nif has separate
    # `3BA_Vagina` and `3BA_Anus` mesh shapes that DO get hidden when
    # this armor equips, replaced by UBE BaseShape's built-in genital
    # region (driven by Pussy/Anus/Vagina/Clitoral scale bones in
    # BaseShape's 29298-vert mesh). The geometry is there, just
    # bone-rigged instead of as separate meshes.
    inject_baseshape: bool = True,
    biped_slots: int = 0,
    alt_texture_shape_names: "set[str] | None" = None,
) -> ConvertResult:
    """Phase-2 conversion: swap inline CBBE body shapes for UBE body shapes.

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

    src_nif = pynifly.NifFile(filepath=str(src_path))
    ube_nif = pynifly.NifFile(filepath=str(ube_body_ref_path))

    # Determine body vs armor shapes in src
    src_wrapped = nif_io.load_nif(src_path)
    body_names, armor_names = classify_shapes(src_wrapped)

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

    # Determine the BODYTRI path to use. Look for a pre-built armor TRI
    # in the user's BodySlide output (the hand-authored armor's per-outfit
    # TRI built by BodySlide with their preset). If found, use its path —
    # those TRIs have the `_ForOutfits` slider bridges that RaceMenu needs
    # to apply body morphs to armor shapes at runtime. Otherwise fall back
    # to the body TRI (less likely to work but at least correctly points
    # at an existing file).
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

    # Pure UBE injection: copy UBE BaseShape (29298v) + VirtualBody from
    # the UBE template body NIF passed via `ube_body_ref_path` — should
    # be the user's preset-built `!UBE\Body\femalebody_tangent_1.nif`.
    # Body under armor is byte-identical to nude; pubic boundary loops
    # are sealed via fan triangulation (existing verts only). Slider
    # morphs apply through the per-armor TRI at runtime, identical to
    # how they apply on the nude body.
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

    # Hands and Feet are NOT injected into the slot-32 body NIF.
    # Skyrim's biped slot mechanic only hides the actor's slot 32
    # body when armor covers it — slots 33 (hands) and 37 (feet)
    # keep rendering the actor's own ARMA-routed NIFs alongside.
    # `scripts/integrate_ube_race_skins.py` patches UBE_AllRace.esp
    # to route every UBE race's slot 33/37 to UBE meshes, so those
    # slots already render UBE topology. Injecting them here too
    # would duplicate the geometry and produce z-fight artifacts.

    # Disable VirtualBody rendering — see `_hide_virtual_body` docstring.
    _hide_virtual_body(dst_nif)

    # BODYTRI attachment is deferred until AFTER armor shapes are copied —
    # we attach it to the first armor shape (mirroring hand-authored UBE
    # conversions which put BODYTRI on an armor shape like 3LeatherBeltArms,
    # not on BaseShape). NioOverride may use a different code path for
    # BODYTRI on armor shapes — applies morphs to all shapes in the TRI —
    # vs BODYTRI on BaseShape — applies only to BaseShape itself.
    # We'll add it after the source armor shapes are copied below.

    if not injected:
        return ConvertResult(
            src_path=src_path, dst_path=None,
            status="skipped",
            reason=f"UBE ref {ube_body_ref_path.name} has no shapes in "
                   f"{body_inject_names}",
        )

    # Build body MeshIndexes for the armor-fit pass (if enabled + refs available).
    # CBBE body candidates: inline shape from source (best — author's actual
    # design surface) or cbbe_body_ref_path (fallback). Detection:
    #   * named one of 3BA/3BA_Anus/3BA_Vagina, OR
    #   * heuristic body (>=4000 verts, >=40 bones, Z range >=70).
    cbbe_idx = ube_idx = None
    # Source body verts+normals for the standoff-preserving conform (restores each
    # cloth vert's original clearance after the warp over-projects onto the larger
    # UBE breast). Detected UNCONDITIONALLY -- the body-delta warp + this conform
    # run regardless of `fit_armor` (which gates only the separate fit_armor_to_ube
    # correspondence step), so this must NOT live inside `if fit_armor:`.
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
        cbbe_ref = pynifly.NifFile(filepath=str(Path(cbbe_body_ref_path)))
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
        # CRITICAL: hand/foot/glove/gauntlet shapes get NO vertex
        # modification. Vertex-warping ops (bake_preset, fit_armor,
        # body-delta warp, inflate, snap) all move verts based on
        # CBBE→UBE body delta — but hands/feet bones are identical
        # between CBBE and UBE skeletons, so the delta is zero where
        # it should be and noisy elsewhere. Result: finger geometry
        # gets bent and twisted into broken poses. The Iron Gauntlets
        # Hands shape (6374 verts) was getting 3184 verts displaced
        # by up to 2.6 units before this guard was added.
        # The M6 reskin step also skips these shapes (see further down)
        # — this just extends the same guard to the vertex-warping
        # phase above.
        if _shape_has_fine_animation_bones(s):
            # Hand/foot/glove/gauntlet shape. We want it fully UBE-SHAPED
            # so the shell conforms to the larger UBE forearm/calf rather
            # than clipping through it. Apply the FULL body-delta warp
            # (min_standoff buffer, no distance cap) over the whole piece;
            # fingers conform to the UBE base hand (near-identical to CBBE,
            # so negligible movement) while wrist/forearm/calf shift out to
            # the UBE limb.
            #
            # Runtime body-morph response is split per vertex below: limb
            # verts get 3BA scale bones, but finger/toe verts are masked
            # out via _extremity_vert_mask so body morphs have ZERO effect
            # on them (their finger/thumb/toe bone weights stay intact, so
            # finger morphs still work).
            hf_orig = np.asarray(s.verts, dtype=np.float64)
            hf_verts = hf_orig
            hf_verts_modified = False
            # Extremity-fraction warp falloff: limb conforms to UBE, digits
            # stay put (the UBE ref has no hand/foot mesh). See phase-1 path
            # and _extremity_vert_fraction.
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
        # Body-space offset: shapes authored in a shifted space (non-identity
        # transform, e.g. a vanilla elven cuirass top at Z=-49 + a +120 Z
        # transform) must have their warp/inflate/conform computed in BODY space,
        # else they match the wrong body region (distortion / "top doesn't scale").
        # Add it before the math, subtract before storage -> output mesh +
        # transform unchanged (render identical), warp now correct. Zero for the
        # identity-transform majority (no effect).
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
                # BodySlide-style safety inflation (see Phase 1 comment
                # for full rationale). Slot-aware magnitude — see
                # `_slot_aware_inflation_magnitude`.
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
                # Standoff-preserving conform: the body-delta warp over-projects
                # fitted layers onto the larger UBE breast (corset hugged at 0.58u
                # on the 3BA body -> 1.8u off the UBE body = "chest too far out").
                # Reel each cloth vert back to its OWN source clearance. Safe by
                # construction (pull-in only, clamped >= min clearance, no-op for
                # loose/already-tight cloth) -- see conform_to_source_standoff.
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
        # FINAL anti-poke pass (#175): push body-slot armor clear of the injected
        # body so the actor's morph can't punch through (nipple/belly/thigh).
        # Runs LAST (in body space, before the offset is removed) so nothing
        # undoes it; measured vs the body that's baked into the output, so it
        # always lands -- unlike the source-correspondence conform. Skips the
        # body/hand-foot shapes, soft-body cloth, and HDT-SMP physics shapes
        # (moving their verts would disturb the sim). See clear_armor_outside_body.
        if (body_verts_for_p2 is not None and body_norms_for_p2 is not None
                and (biped_slots & (BIPED_SLOT32_BIT | BIPED_SLOT49_BIT))
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_softbody_names
                and not _shape_has_hdt_smp_rigging(
                    s, set(ube_base_for_pass1.bone_names or [])
                    if ube_base_for_pass1 is not None else set())):
            try:
                base_v = (np.asarray(override, dtype=np.float64)
                          if override is not None else _sv_body)
                override = clear_armor_outside_body(
                    base_v, body_verts_for_p2, body_norms_for_p2,
                    body_nipple=body_nipple_for_p2)
            except Exception as e:
                failed.append((f"{s.name}:antipoke", repr(e)))
        # #177: keep self-simulated cloth (custom physics-chain bones: skirt/
        # belt/cape) at its SOURCE position so it stays aligned with its chain
        # bones (recreated at source bind). The warp/inflate/conform above move
        # the cloth onto UBE while the bones stay at source -> the cloth is
        # offset from its OWN bones -> SMP rest pose wrong -> collapse / fall
        # through the floor. Per-vertex (by chain-weight fraction), so a part-
        # physics part-body shape (skirt+chest) keeps the body/chest warped for
        # the UBE fit while the skirt rides its bones. Runs LAST in body space,
        # measured vs _sv_body (the source in the same space).
        if override is not None:
            override = _physics_chain_nowarp_blend(s, _sv_body, override)
        # Back to the shape's own (local) space; its transform is unchanged, so
        # render is identical -- but the warp/inflate/conform above ran in body
        # space (correct body region). No-op when _off_p2 is zero (identity xf).
        if override is not None and _off_p2.any():
            override = np.asarray(override, dtype=np.float64) - _off_p2

        # M6 reskin (deferred to be applied via override_skin in pass 2).
        override_skin = None
        if (reskin_armor
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_softbody_names
                and not _shape_has_fine_animation_bones(s)
                and not _shape_is_head_dominant(s)):
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
                    # Add scale bones to ALL reskinned cloth (its only body-
                    # tracking layer — cloth has no per-shape BODYTRI). The
                    # 78-bone cap is the GPU-palette backstop. See Phase 1
                    # comment. [reverts #164/#166]
                    if ADD_SCALE_BONES_TO_CLOTH:
                        bones, xforms_map, weights_map = add_scale_bone_weights(
                            bones, xforms_map, weights_map,
                            final_verts, ube_basereshape,
                            reach=_slot_aware_scale_bone_reach(biped_slots),
                            torso_parity=bool(biped_slots & (
                                BIPED_SLOT32_BIT | BIPED_SLOT49_BIT)),
                        )
                    if bones and weights_map:
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

    # --- Z-fight auto-offset across all pass-1 verts ---
    # Push inner-layer verts inward by ~half the z-fight threshold so
    # the two layers separate. Only runs when we have body normals.
    if body_verts_for_p2 is not None and shape_jobs:
        try:
            from scipy.spatial import cKDTree
            zfight_map = {
                j["src"].name: j["verts"] for j in shape_jobs
            }
            zfight_offsets = detect_zfight_pairs(
                zfight_map, body_verts_for_p2, body_norms_for_p2,
            )
            # Convert per-vert scalar offsets to 3D deltas along the
            # body's outward normal at each armor vert's nearest body
            # neighbor. Apply to shape verts in-place.
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

    # Cleavage depth separation — push inner-layer cloth verts backward
    # so they sit a clean clearance behind the outer layer. Fixes static
    # Z-fighting / mesh intersection visible at standstill.
    if shape_jobs and body_verts_for_p2 is not None:
        try:
            n_pushed = _separate_chest_layered_cloth_depth(
                shape_jobs,
                body_verts=body_verts_for_p2,
                body_normals=body_norms_for_p2,
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
            )
            if n_abdo:
                import sys as _sys
                print(f"  abdomen depth: stacked {n_abdo} waist-layer "
                      f"vert(s) for clean separation", file=_sys.stderr)
        except Exception:
            pass  # best-effort

    # Layered-cloth weight sync (re-enabled 2026-05-29, GATED by breast-
    # weight fraction so only genuine bust layers are synced — never
    # decorative attachments). Keeps bra + over-fabric moving together
    # under breast-jiggle. See _sync_chest_layered_cloth_weights.
    if shape_jobs:
        try:
            n_synced = _sync_chest_layered_cloth_weights(shape_jobs)
            if n_synced:
                import sys as _sys
                print(f"  cleavage sync: matched {n_synced} bust-layer "
                      f"vert(s) to authority weights", file=_sys.stderr)
        except Exception:
            pass  # best-effort; failure leaves shapes as-is

    # Degenerate-triangle repair (LAST vertex op, after warp/inflate/conform AND
    # the depth/zfight/sync passes that also move verts). Those passes can pinch
    # thin fabric/metal tris flat -> they render as black slivers / holes ("dress
    # mangled"). Restore each collapsed tri to its source-relative shape at the
    # converted location. Source-degenerate folds are left alone. #177
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

    # --- Pass 2: copy each shape verbatim, alpha preserved. ---
    # NioOverride morphs alpha-having cloth correctly when bit 19
    # of NiAVObject flags is set (see _reset_morph_flags). Don't
    # strip alpha here — it'd kill texture cutout transparency
    # (gaps between leather strips render as opaque).
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
            failed.append((s.name, repr(e)))
            continue

    # Attach BODYTRI per the regime chosen by `_pick_bodytri_carriers`:
    #   * Slot-49 / no-body NIFs (a slot-49 no-body cloth armor class): SINGLE carrier
    #     on the top-ranked cloth shape (e.g. a corset shape). Matches
    #     hand-authored BodySlide convention.
    #   * Slot-32 + BaseShape NIFs (a multi-piece armor Main, a replacer cuirass
    #     class): MULTI-carrier — every qualifying cloth shape gets
    #     its own BODYTRI block so NioOverride applies per-shape TRI
    #     morphs to ALL of them (not just BaseShape).
    #
    # Rigid single-bone pieces that NioOverride would skip in
    # BodyMorph still follow body morphs because M6 proximity-blend
    # re-skin re-weighted them to multiple body bones — they move
    # via standard skinning during body deformation, not BodyMorph.
    # Falls back to first_armor_shape only if the cloth-shape filter
    # produced an empty set.
    carriers_p2 = _pick_bodytri_carriers(dst_nif)
    if not carriers_p2 and first_armor_shape is not None:
        carriers_p2 = [first_armor_shape]
    if carriers_p2:
        try:
            from pyn.pynifly import NiStringExtraData  # type: ignore
            # Apply morph-readiness cleanup (flags + shader normalize +
            # alpha strip-attempt) to EVERY cloth shape in the NIF —
            # not just the BODYTRI carrier. NioOverride at runtime
            # walks every shape named in the TRI and applies its per-
            # shape morphs; if the non-carrier cloth shapes still have
            # Shader_Type=1 or non-canonical flags, NioOverride skips
            # morphing them. Concrete failure: a cloak shape was in
            # the TRI with 73 morphs but didn't move in-game because
            # its Shader_Type=0/alpha=True/flags=0x8000e blocked
            # NioOverride from applying the morphs.
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
            for s in cloth_shapes_to_clean:
                _reset_morph_flags(s)
                _normalize_shader_for_morph(s)
                # Alpha block KEPT (settled rule): bit-19 (set by
                # _reset_morph_flags) unblocks morphing on alpha shapes
                # without stripping alpha. Stripping destroyed cutout
                # transparency and only partially persisted, which
                # corrupted the atlas opaque-diffuse detection.
                _normalize_partitions(s)

            # BODYTRI itself goes on the carrier ONLY (single-carrier
            # convention).
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
        except Exception:
            pass

    # Attach HDT-SMP physics config reference on the root node, matching
    # the source mod's XML. The XML defines bones (CBBE-style AND custom
    # like physics-chain bones (prefix_NN)) and physics constraints between them — so when
    # body morphs/animates via UBE bones, the constraints propagate to
    # the CBBE bones our fabric is skinned to, keeping the fabric
    # attached to the morphed body. Hand-authored UBE conversions ship
    # this same extra-data on root.
    try:
        hdt_xml_path = _find_hdt_xml_for_armor(src_path)
        # If the source XML drives physics-CHAIN bones our conversion
        # stripped (a physics-chain bone /Skirt N_NN), drop the reference so the
        # post-save generator below builds a fresh per-vertex soft-body
        # XML on the standard bones we actually have (dst_nif isn't saved
        # yet, so read its in-memory bone set). See
        # _source_hdt_needs_missing_chain_bones.
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
    except Exception:
        pass  # HDT injection is best-effort

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

    dst_nif.save()

    # (Removed 2026-05-29) Cloth-count reduction was here — see the
    # phase-1 removal note above. After #139, every shape in the NIF
    # gets a TRI entry and morphs in-game, so merging shapes to fit a
    # fake "cap" was solving a problem that didn't exist. The merge
    # function definition stays in this file as dead code pending #141
    # (full retirement of the merge / texture-atlas pipeline).

    # M8 auto-TRI generation. Now that the NIF is saved, build a BODYTRI
    # file with body-slider deltas propagated to armor verts via K-NN
    # IDW. Only runs when the BODYTRI logic above couldn't find a
    # user-built TRI in the BodySlide Output mod. This is what makes
    # armors without a published UBE sliderset (e.g. a heavily-boned armor) follow body
    # morphs at runtime — no manual BodySlide step required.
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
                        # Always include; rely on per-vert extremity-frac
                        # dampening inside generate_armor_tri. Replaces
                        # the old all-or-nothing extremity-dominant
                        # exclusion that dropped long sleeves whose hand
                        # portion outweighed the sleeve portion. #147.
                        # Body-space offset (shape transform) so the morph KNN
                        # matches the right body region for shapes authored in a
                        # shifted space (else "top doesn't scale"). BodyMorph is
                        # index-based, so the resulting per-vert deltas apply
                        # correctly to the local-space mesh.
                        armor_shape_verts[s.name] = (
                            np.asarray(s.verts, dtype=np.float64)
                            + shape_body_offset(s))
                        ef = _extremity_vert_fraction(s, len(s.verts))
                        if ef is not None and ef.size:
                            armor_vert_ef[s.name] = ef
                    # Pick the carrier (where BODYTRI extra-data goes)
                    # so the generated TRI lists it first — matches
                    # hand-authored UBE convention. _pick_bodytri_carriers
                    # returns a single-shape list.
                    p2_carriers = _pick_bodytri_carriers(dst_check)
                    p2_carrier_name = p2_carriers[0].name if p2_carriers else None
                    # Unified TRI: include BaseShape entry so the
                    # single BODYTRI on the cloth carrier delivers both
                    # cloth + body morphs. See phase-1 site for the
                    # rationale (avoids double-BODYTRI shadowing).
                    tri = generate_armor_tri(
                        armor_shape_verts,
                        body_verts_arr,
                        body_osd,
                        body_shape_name="BaseShape",
                        include_body_shapes=body_in_dst,
                        carrier_shape_name=p2_carrier_name,
                        armor_vert_extremity_fractions=armor_vert_ef,
                    )
                    auto_tri_dst.parent.mkdir(parents=True, exist_ok=True)
                    tri.save(auto_tri_dst)
        except Exception as e:
            # Non-fatal — armor still works without morphs.
            result_reason = (result_reason + "; " if result_reason else "") \
                + f"auto-TRI generation failed: {e!r}"

    # Phase 2 HDT-SMP XML auto-gen. The phase 2 path defers HDT XML
    # injection until after the dst NIF is saved (cloth shapes need
    # to be enumerated from the dst NIF, which doesn't exist on disk
    # until the save above). If the BLOCK above already injected a
    # source HDT XML reference, skip auto-gen (we prefer hand-authored).
    if hdt_xml_path is None:
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
                    nf_for_inject.filepath = str(dst_path)
                    nf_for_inject.save()
        except Exception as e:
            result_reason = (result_reason + "; " if result_reason else "") \
                + f"HDT XML gen failed: {e!r}"

    # Multi-partition collapse (post-save reload pass). Must run AFTER
    # all other re-saves (HDT XML inject above) so it doesn't clobber
    # their extra-data. Reloads from disk so partition_tris is live.
    _normalize_partitions_on_disk(dst_path)

    # FINAL HDT-SMP physics pass — runs LAST so the extra-data survives
    # (earlier round-trips dropped it). Prefers the source armor's
    # authored XML. See _finalize_hdt_physics.
    try:
        _finalize_hdt_physics(dst_path, src_path)
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
    )
