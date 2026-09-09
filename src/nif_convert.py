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

`warp_armor=True` selects an EXPERIMENTAL position-warp branch of the copy
path that is never used in production (it loses on every measured piece and
is kept for diagnostics). The production copy path is the default branch:
a body-aware rebuild that warps, inflates, conforms and anti-pokes every
shape against the UBE body, then writes it -- not a verbatim copy.

NAVIGATING THIS FILE. It is ~28k lines and its topic banners say what a thing
IS, never what runs WHEN -- the execution order has had to be reconstructed by
hand more than once. `docs/PASS_MAP.md` answers that: per entry point, in source
order, every module-level pass call with the flag/knob guards wrapping it, the
traced stage chain for each path, and an index of every banner in this file.

    python scripts/pass_map.py            # regenerate
    python scripts/pass_map.py --check    # is it current?

It is GENERATED and pinned by `tests/test_pass_map.py`, so it cannot drift from
the code the way a hand-kept index would. A pointer is used here rather than an
inline copy for the same reason: a copy is a comment that can go stale, and this
project has already paid for one that did.
"""
from __future__ import annotations

import os
import re
import sys
import types as _types
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import fit_metrics, nif_io, nif_patch
from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
from .correspondence import MeshIndex, compute_deformation
# The ONE way to read a CBBE2UBE_* env flag/knob -- 287 inline spellings in 12
# variants collapsed 2026-08-18; the variants disagreed on what "0"/"true"/
# empty meant, and the sprawl is why flag audits kept finding wrong-polarity
# comments. See envflags docstring for the exact contract.
from .envflags import flag as _flag, knob as _knob


# ---- DEFAULTS PROMOTED 2026-08-22 ------------------------------------------
# THE BLOCK FOUR COMMENTS ELSEWHERE POINT AT. Until 2026-08-23 they cited
# `_DEFAULTS_PROMOTED_2026_08_22` and it did not exist anywhere -- four dangling
# cross-references, the same "cited thing that is not there" class the
# 2026-08-17 comment audit found six of. It is a real symbol now, and
# `tests/test_promoted_defaults.py` asserts its contents match the set that test
# pins, so the record and the test cannot drift apart.
#
# WHY THESE FOUR MOVED. All had been ON in the live recipe for weeks while the
# code shipped them OFF, so a defaults-only convert produced a configuration
# NOBODY HAD EVER RUN -- while the 2026-08-22 reconvert, built with all four ON
# across 163 mods, is the pack the user judged "everything looks as it should".
# The defaults now match the only configuration that has ever been judged.
#
#     mixed_cloth_clearance   False -> True
#     panel_rigidity          0.0   -> 0.75
#     panel_rigid_ride        False -> True
#     per_anchor_seed         False -> True
#
# `warp_push_shell_cap` was in the same recipe and was REFUSED: its own test
# records it inert on every large spike measured and REGRESSING two shapes while
# moving geometry 1.38u. A general "looks fine so far" does not overturn a
# specific negative measurement. It is still True in the live recipe and False
# in code, which is the one remaining recipe/code divergence.
_DEFAULTS_PROMOTED_2026_08_22 = (
    "MIXED_CLOTH_CLEARANCE",
    "PANEL_RIGIDITY",
    "PANEL_RIGID_RIDE",
    "PER_ANCHOR_ANCHOR_SEED",
)

# #defaults-promoted-2026-08-26 -- the two BUG-15(a)/(b) fixes.
#
# THE ORDER IS INVERTED FROM 2026-08-22 AND THAT IS DELIBERATE, SO SAY SO: those
# four were promoted BECAUSE an in-game verdict had already judged them. These
# two are promoted so the reconvert that PRODUCES that verdict runs them, at the
# user's explicit instruction. They are measured, not judged.
#
#     bust_morph_chord          False -> True
#     panel_rigid_surface_guard False -> True
#     ride_body_floor           False -> True
#
# `ride_body_floor` is the ODD ONE OUT and was added later the same day.
# The first two are new work. This one had been ON in the live recipe
# for weeks while the code shipped it OFF -- the SAME situation the
# 2026-08-22 four were promoted to fix. Aligning the settings to the
# code defaults removed it, and `verify_reconvert.py` failed the pack
# for its absence. Promoting keeps the measured behaviour (-69%
# clipping, 19 better / 0 worse over 28 pieces) and still leaves the
# settings file free of overrides.
#
# What the measurements are, in one line each -- the long form is in the
# constants' own comments and the BUG-15 block:
#   * chord: 72-arm sweep over 6 pieces x 14 presets -- 39 improved, 0
#     regressed, and all 22 arms that were ALREADY CLEAN stayed at 0.000%.
#   * guard: no regression on any piece measured; a college robe 12.957 ->
#     3.466 with its BIND clip 1.473 -> 0.000; standoff p90 goes DOWN.
#
# NOT promoted alongside them, and the reason is a measurement:
# `PANEL_RIGID_EARLY_CLEAR` was re-tested WITH the guard (its 12-better/6-worse
# verdict predates it) and came back 1 better / 2 worse -- velothisteel
# 6.486 -> 10.813, cowarchrobe 3.466 -> 8.443. Same shape as its old verdict.
_DEFAULTS_PROMOTED_2026_08_26 = (
    "BUST_MORPH_CHORD",
    "PANEL_RIGID_SURFACE_GUARD",
    "RIDE_BODY_FLOOR",
)


# ---------- module-level caches (one per process) -----------------------
# These avoid re-doing expensive work per-NIF during a batch convert.
# A typical armor mod has 18-24 NIFs; without caching we'd parse the
# 11.3 MB UBE body OSD 18-24 times, walk BodySlide-output dirs 18-24
# times, etc. Caching turns those into one-shots per process.

# (split step 4) _OSD_CACHE, _BODY_MORPH_STACK_CACHE, _BODY_MORPH_AMP_CACHE, _BODY_MORPH_DIFF_CACHE ... live in nif_convert_trigen.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
# Split modules are re-executed whenever THIS module is (re)loaded, so
# `importlib.reload(nc)` keeps its pre-split meaning: every flag/knob and
# every cache is re-read / re-created in the siblings too. Without this a
# reload would re-import already-loaded siblings unchanged, and a test that
# flips an env var and reloads would silently keep the old value. Order =
# dependency order (trigen imports bodyrefs and telemetry by name).
import importlib as _importlib  # noqa: E402
_SPLIT_MODULES = ("nif_convert_telemetry", "nif_convert_skinframe",
                  "nif_convert_bodyrefs", "nif_convert_trigen",
                  "nif_convert_fitgeom",
                  "nif_convert_bust",
                  "nif_convert_layers",
                  "nif_convert_weights",
                  "nif_convert_physics",
                  "nif_convert_writer",)


def _reload_split_modules() -> None:
    for _m in _SPLIT_MODULES:
        _k = f"{__package__}.{_m}"
        if _k in sys.modules:
            _importlib.reload(sys.modules[_k])


_reload_split_modules()

from .nif_convert_trigen import (  # noqa: E402
   _OSD_CACHE, _BODY_MORPH_STACK_CACHE, _BODY_MORPH_AMP_CACHE,
   _BODY_MORPH_DIFF_CACHE, _MORPH_TRI_NAME_CACHE, _MORPH_STACK_MIN,
   _MORPH_SIZE_KEYWORDS, _cached_osd_load, _cached_body_morph_stack,
   _cached_body_morph_amplitude, _cached_body_morph_differential,
   _body_array_digest, _tri_is_owning_variant, _tri_fits_variant,
   pair_shape_aliases, pair_alias_map,
   _reset_morph_flags,
   armor_relpath_under_meshes,
   _collect_tri_inputs, _normalize_shader_for_morph, _pick_bodytri_carriers,
   _source_morph_tri_shape_names, _refresh_armor_tri_after_reimport,
   check_ube_nude_morph_files,
)

_UBE_BODY_REF_CACHE: "dict[Path, tuple[object, np.ndarray]]" = {}  # path -> (NifFile, BaseShape_verts)
# (moved to nif_convert_trigen.py, 2026-09-01)
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
# **0 DOES NOT DISABLE THIS PASS, AND THIS KNOB IS NOT AN ABLATION LEVER.**
# That is what this comment claimed until 2026-09-06 and it was false in both
# halves. On the adaptive path -- every piece, since `ADAPTIVE_CLEARANCE_ENABLED`
# is a hardcoded True and the OSD amplitude map is always found -- the pass
# computes `cap = max(magnitude, ADAPTIVE_CLEARANCE_MORPH_MAX)` and then
# `clip(BASE + FACTOR*amp, BASE, cap)`. With morph_max 1.1, ANY magnitude at or
# below 1.1 -- including 0.0 -- drops out of the arithmetic entirely and every
# vert still gets BASE..1.1u of push. Only the BELT variant (1.5) exceeds the cap
# and actually binds; SLOT49 0.5, HANDS_FEET 0.6 and SKIRT 0.7 are all inert,
# so `_slot_aware_inflation_magnitude` is a no-op on 4 of its 5 branches.
#
# MEASURED: an arm at 0.35 is BYTE-IDENTICAL to one at 0.7 across all 184 NIFs of
# the acceptance population. So every "inflate magnitude is inert" line in the
# record was measuring a knob that cannot reach the pass, and inflate has never
# actually been ablated. To ablate it, or to attack the over-standoff class, use
# `CBBE2UBE_CLEARANCE_BASE` / `CBBE2UBE_CLEARANCE_MORPH_FACTOR` /
# `CBBE2UBE_CLEARANCE_MORPH_MAX` -- see ADAPTIVE_CLEARANCE_BASE.
# Reconvert any affected mod after changing.
ARMOR_INFLATION_MAGNITUDE = _knob("CBBE2UBE_INFLATION_MAGNITUDE", 0.7)
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
#
# THIS IS THE PAIR THAT ACTUALLY BINDS THE CHEST STANDOFF, and `CBBE2UBE_BUST_CLEAR`
# DOES NOT REACH IT -- that env feeds `ANTIPOKE_BUST_CLEAR`, a different number in a
# different pass. Two predicates for one concept, and they drifted (the same class as
# the clip-metric divergence). Traced with CBBE2UBE_STAGE_DUMP on a skin-tight
# BodyStock the author holds 0.121u off the skin and we shipped at 0.791u:
#
#     magnitude 0.7        magnitude 0.2
#     s03_inflate  +0.637   s03_inflate  +0.278
#     s04_conform  -0.029   s04_conform  +0.323   <- puts back what inflate gave up
#
# Both land at ~0.78, because THIS floor is what the conform enforces in the bust
# band. That is why lowering the inflate magnitude, ANTIPOKE_FLAT_CLEAR and
# CBBE2UBE_BUST_CLEAR all measured inert on it. Env-tunable now, DEFAULTS
# UNCHANGED, so the chest can be A/B'd in game without a rebuild:
#     CBBE2UBE_BUST_FLAT_CLEAR=<units>   the floor everywhere in the band
#     CBBE2UBE_CONFORM_BUST_CLEAR=<units>  the ceiling the nipple ramp climbs to
#
# DEFAULTS MOVED 2026-08-13, 0.3/0.9 -> 0.12/0.3. The old pair held a
# SKIN-TIGHT layer 0.79u off the body when its author had it at 0.121u, which
# ships as a sheer bodysuit standing proud of the skin and reading as a dark
# region across the chest (reported in game with a screenshot). These values
# were then gated per REGION against the previously-shipped mesh on 7 pieces:
# no region worse, bust verts-inside-body DOWN on most (the layered robe 11 -> 2,
# that plated top 206 -> 196, hide cuirasslight 5 -> 2).
#
# The morph charge is unaffected -- `req` still adds the measured per-slider
# residual on top of this floor -- and `ANTIPOKE_BUST_CLEAR` (the anti-poke
# pass's own target) is untouched, so the last line against skin-through-steel
# is where it was. Restore the old fit with
# `CBBE2UBE_BUST_FLAT_CLEAR=0.3 CBBE2UBE_CONFORM_BUST_CLEAR=0.9`.
#
# THE CEILING WENT BACK TO 0.9 (2026-08-13, same day it moved). Reported in
# game on a leather cuirass: "leather armor once again has a nipple show". This
# value is the ONLY thing standing between a breast tip and the garment over it
# -- it is what the nipple ramp climbs to -- and 0.3 is not enough for it.
#
# NOTE THE COUPLING, because it is the whole difficulty here: the same ceiling
# also decides how far a SKIN-TIGHT layer is held off the chest, since a
# bodysuit's chest verts carry real nipple weight and so ride the same ramp.
# Measured on a layered robe's bodysuit (author 0.121u):
#     floor 0.12 / ceiling 0.3  -> median 0.561   nipple POKES
#     floor 0.12 / ceiling 0.9  -> median 1.225   nipple safe
# So this is not a knob with a right answer, it is a trade, and the tip wins:
# a visible nipple through armour is a worse defect than a layer sitting proud.
# Splitting them needs a narrower nipple region with a steeper ramp, so the tip
# keeps its clearance without dragging the whole bust band out with it.
#
# THAT IS NOW BUILT -- `#nipple-ramp-sharpness` / `BUST_NIPPLE_SHARPNESS` below,
# which raises `nipw` to an exponent and SOLVES the gain so the tip requirement
# is held exactly where it is. This line read "it is unbuilt" long after it
# shipped, and `_conform_to_body` quotes it as if current. Corrected 2026-09-06.
# It defaults to 1.0 (off) not because it is unfinished but because it was
# MEASURED INERT ON THE FINAL MESH: `s07_antipoke` re-pushes after conform, so a
# lower conform requirement is overwritten -- see the numbers at its call site.
#
# NEITHER default was reachable from the GUI before 2026-08-13, and neither is
# the same number as `ANTIPOKE_BUST_CLEAR`, which is a different pass.
BUST_FLAT_CLEARANCE = _knob("CBBE2UBE_BUST_FLAT_CLEAR", 0.12)
# (split step 5) WARP_PUSH_SHELL_GAP, WARP_DELTA_OUTLIER_MAX, CONFORM_FOLD_GUARD_FEATHER, CONFORM_FOLD_GUARD_MARGIN ... live in nif_convert_fitgeom.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_fitgeom import (  # noqa: E402
    WARP_PUSH_SHELL_GAP, WARP_DELTA_OUTLIER_MAX, CONFORM_FOLD_GUARD_FEATHER,
    CONFORM_FOLD_GUARD_MARGIN, CONFORM_FOLD_GUARD_STEPS,
    _SOFTCLOTH_BUST_CLEAR, _SOFTCLOTH_BUTT_CLEAR, WARP_SHEAR_MAX_GROWTH,
    WARP_SHEAR_STEPS, ANTIPOKE_SMOOTH_ITERS, ADAPTIVE_CLEARANCE_BASE,
    ADAPTIVE_CLEARANCE_MORPH_FACTOR, ADAPTIVE_CLEARANCE_MORPH_MAX,
    _authored_floor_amp_room, _authored_normals_usable, _feathered_authored,
    ANTIPOKE_BUST_CLEAR, ANTIPOKE_FLAT_CLEAR, ANTIPOKE_NIPPLE_GAIN,
    CALF_STANDOFF, CALF_STANDOFF_Z_HI, CALF_STANDOFF_Z_LO,
    JIGGLE_CLEARANCE_GAIN, JIGGLE_CLEARANCE_MAX, REAR_STANDOFF,
    REAR_STANDOFF_FEATHER, REAR_STANDOFF_FEATHER_NY, REAR_STANDOFF_NY,
    REAR_STANDOFF_Z_HI, REAR_STANDOFF_Z_LO, THIGH_STANDOFF,
    THIGH_STANDOFF_Z_HI, THIGH_STANDOFF_Z_LO, CONFORM_BLEND_TIGHT,
    CONFORM_BUST_CLEARANCE, STATIC_AUTHORED_AMP,
    STATIC_AUTHORED_MIN_CLEARANCE, warp_armor_by_body_delta,
    _clamp_delta_outliers, _damp_to_avoid_inversion, _limit_triangle_shear,
    _smooth_warp_grooves, _uniformise_local_scale, _local_edge_length,
    _cap_short_edge_stretch, _inflate_cloth_over_bust_butt,
    _cap_push_at_own_shell, conform_to_source_standoff, _surface_deficit,
    _bust_morph_chord_req, _relax_conform_field, _solve_clearance_field,
    _reach_iters, _reach_screen, clear_armor_outside_body, _smooth_push_field,
    snap_armor_outside_body, repair_collapsed_tris, _partial_rigid_panels,
    _rigidify_within_clearance, _locally_rigid_panel, _panel_rigid_disp,
    _panel_fit_out_of_body, _weld_components, _is_skirt_like,
    _is_belt_overlay,
)

BUST_NIPPLE_GAIN = 1.0
# --- #nipple-ramp-sharpness -- exponent on the nipple ramp. 1.0 = today.
# Above 1 narrows the high-clearance region toward the tip; the gain is solved
# so the TIP requirement is unchanged. See `_conform_to_body` for the numbers
# and for why lowering the ceiling instead was reverted.
BUST_NIPPLE_SHARPNESS = _knob("CBBE2UBE_BUST_NIPPLE_SHARPNESS", 1.0)
BUST_NEIGHBORHOOD_K = 6
BUST_NEIGHBORHOOD_RADIUS = 4.0
# #bust-neighbourhood-spacing.
# MEASURED: `BUST_NEIGHBORHOOD_RADIUS` above is very nearly INERT. The UBE body's
# local vertex spacing is 0.359u, so k=6 reaches a median of only 0.673u -- the
# 4.0u filter almost never removes a neighbour, and the effective neighbourhood
# is set by k, not by the radius the constant advertises.
# That matters because the push-out is applied PER GARMENT VERTEX while garment
# spacing is 0.71-1.21u on the pieces measured: the body tip that pokes BETWEEN
# two garment verts sits up to ~0.6u from either, i.e. right at or outside the
# 0.673u the pass actually looks at. Result on a 3339-vert cuirass: 0.125u of
# nipple clearance delivered against a 0.55u requirement, with nothing in the
# pass aware it had missed.
# Fix: size the neighbourhood by the patch a vertex is responsible for -- its own
# local spacing -- and raise k enough to actually REACH that far. The floor
# preserves today's effective 0.67u reach, so a garment finer than the body is
# unchanged. The cap stays well under the advertised 4.0u deliberately: at 4u the
# "worst body point" on a breast can be a far-away protrusion, which would
# balloon the garment rather than clear a tip.
BUST_SPACING_AWARE = not _flag("CBBE2UBE_NO_BUST_SPACING", False)
# SATURATED at 1.5 by measurement: 1.5 / 2.0 / 3.0 all give the same clearance
# on the failing piece, so the radius stops being the limiter here. Not a knob.
BUST_SPACING_MULT = 1.5
BUST_NEIGHBORHOOD_RADIUS_MAX = 2.0     # ~ what K_MAX reaches on this body
BUST_NEIGHBORHOOD_K_MAX = 64
# #bust-surface-req. `worst` (above) is the clearance of a garment VERTEX over
# the body. The defect is the clearance of the garment SURFACE. Measured on a
# studded cuirass: 50 of the 50 tightest nipple spots sit in a triangle INTERIOR
# a median 1.607u from any vertex, the vertices stand 1.45u clear, and the
# surface spanning them sags to 0.338u -- the vertex measure OVERSTATES by
# 0.855u. `req - worst` is then negative at every nipple vert (mean -0.921), so
# the push never fires and `req` is never read: raising it by 3.5u moved
# delivered clearance by 0.04u. Six earlier attempts were all compensating for
# that, and each ballooned the torso because it pushed on some other quantity.
# So evaluate the requirement where the defect is -- against the SURFACE, with
# the same closest-point-on-triangle test the validated poke metric uses -- and
# push the offending triangle's own vertices. Needs `tris`; without them the
# pass behaves exactly as before.
BUST_SURFACE_REQ = not _flag("CBBE2UBE_NO_BUST_SURFACE_REQ", False)
BUST_SURFACE_K = 24            # body points tested per garment triangle
# Ceiling on what the surface test may demand. NOT a knob: 1.5 / 2.5 / 3.5 all
# give the same result across 112 installed presets (1 still poking, the same
# outlier) and the same fit, so the cap is not the limiter -- it is a safety rail.
BUST_SURFACE_MAX_PUSH = 1.5
# #bust-morph-chord -- DEFAULT ON since 2026-08-26 (was opt-in
# `CBBE2UBE_BUST_MORPH_CHORD=1`; `=0` turns it off).
#
# `#bust-morph-residual` charges the clearance a slider takes away by comparing
# TWO BODY VERTICES: the one a garment vert hugs and a neighbour, projected on
# the bind normal. That is a GRADIENT term, and its neighbourhood is capped at
# BUST_NEIGHBORHOOD_RADIUS_MAX = 2.0u.
#
# What it cannot see is the CHORD. A garment triangle spans the breast; each of
# its three corners copies the delta of ITS OWN nearest body vert
# (`generate_armor_tri`), so the surface between them moves by the LINEAR
# INTERPOLATION of three donors while the body under it moves along its own
# curve. Where the body's morph field is convex across the span, the flat
# triangle cuts the corner and the skin comes through between vertices that are
# each individually correct.
#
# Measured on a light cuirass under a real preset (the reported piece): the
# donor model predicts a 0.295u median loss, the true loss is 0.936u, and an
# EXACT barycentric surface model reproduces the truth to 0.079u. The hit
# triangles span 6.7u, their three donors sit 5.6u apart and move 3.5u
# DIFFERENTLY -- three times the 2.0u the requirement is allowed to look at. So
# this is not a tuning shortfall in the existing charge; it is a term the
# existing charge does not contain.
#
# This is the "NOT built" item from #bust-neighbourhood-spacing, which recorded
# the same residual from the other end: "lifting a vertex by the deficit lifts
# the midpoint between vertices by less; closing it needs the push scaled by the
# barycentric weight."
#
# Charged per TRIANGLE over the body points that project INSIDE it -- the same
# inside test `_surface_deficit` uses, and for the same reason: a body point
# beside a triangle is not covered by it, and letting it demand a push is what
# ballooned a chest in an earlier attempt. Computable from the OSD alone, so no
# garment `.tri` is needed at conform time.
# DEFAULT ON 2026-08-26 (#defaults-promoted-2026-08-26). 72-arm sweep, 6 pieces
# x 14 presets: 39 improved, 0 regressed, and all 22 arms that were ALREADY
# CLEAN stayed at 0.000% -- the bar three earlier attempts on this class failed.
BUST_MORPH_CHORD = _flag("CBBE2UBE_BUST_MORPH_CHORD", True)
# Ceiling on what the chord term may demand, matching BUST_SURFACE_MAX_PUSH's
# role as a safety rail rather than a tuning knob.
BUST_MORPH_CHORD_MAX = 1.5
# #conform-fold-guard -- OPT-IN. Stop `conform` turning the garment surface
# inside-out.
#
# The pass moves every vertex along ITS OWN nearest-body-vertex normal by ITS
# OWN scalar, and nothing couples neighbours. Across a body crease -- spine
# groove, underbust, waist -- two adjacent cloth verts snap to body verts whose
# normals diverge sharply and whose pulls differ by up to `max_pull`, so they
# travel through each other and the triangle between them inverts. It then
# renders backface-culled and lit the wrong way: a flat dark patch, in mirrored
# pairs because the body's creases are symmetric. Reported in game on a
# cuirass as "two black squares", traced to this pass by per-pass fold count
# (0 at entry, +121 here, of which `groove_smooth` happens to undo 52).
#
# The guard is a CLAMP, never a push: a vertex may end up moved LESS than the
# unguarded pass wanted, never further and never in a new direction. So it
# cannot create clipping. What it CAN do is leave a garment standing off
# slightly further out, which is the overinflation axis this project keeps
# paying for -- hence opt-in and a standoff measurement, not just a fold count.
CONFORM_FOLD_GUARD = _flag("CBBE2UBE_CONFORM_FOLD_GUARD", False)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# #conform-stretch-field -- OPT-IN. Couple neighbouring verts in the conform
# DISPLACEMENT, instead of damping the ones that misbehave.
#
# Same defect as #conform-fold-guard, opposite instrument. Conform sends each
# vert along `ube_body_normals[ui]`, the normal of its NEAREST BODY VERTEX. That
# direction field belongs to the BODY, so it inherits the body's creases: at the
# neck/clavicle the nearest-vertex assignment flips across the crease and two
# adjacent garment verts are handed normals >90deg apart, then travel different
# distances along them. Nothing in the pass couples them, so the edge between
# them is free to grow without bound.
#
# MEASURED on a reported bodysuit, per-stage, against the author's own edge
# lengths: entering conform the mesh is at 4.98x worst edge stretch; leaving it,
# 63.1x, with 979 of the 1525 edges past 2x inside z 102-116 -- the neckline.
# Folds over the same boundary: +1911.
#
# The fold guard addresses this by damping the offending verts toward NO motion.
# It works on its own terms (-44% folds, 37.3x -> 11.6x) but conform's motion on
# a fitted piece is mostly authored PULL-IN, so discarding it leaves the garment
# floating: the same measurement put the suit 1.152u off the body against the
# author's 0.604u, which is the overinflation this project keeps paying for
# (see project_bodystock_standoff_defect). It also RAISES moderate stretch
# (edges >2x: 1008 -> 1401), because holding one vert still while its ring
# travels is itself a stretch.
#
# So: relax the displacement field over the garment's own welded adjacency
# (`_welded_edges`, the same notion of "next to" the clearance field uses), then
# put the along-normal component back inside the envelope the pass already
# validated. The relaxation is an AVERAGING operator seeded at the requested
# field, so no vertex can be handed a displacement outside the convex hull of
# its neighbourhood -- it cannot invent a new extreme. What survives is the
# TANGENTIAL part, a slide along the body surface, which is exactly the freedom
# that relieves stretch and which the per-vertex formulation never had.
#
# MEASURED on that piece at the settings below, shipped mesh against the author:
#
#                        author   default    guard   stretch field
#     conform's folds         --     +1911       --           +765
#     worst edge stretch    1.00x    37.32x   11.59x         13.82x
#     edges  >1.5x              0      2313     3033           1681
#     edges  <0.5x              0       772      282            652
#     collar plate folds        0        17        1              0
#     collar plate buried    9.8%     19.3%    16.0%          12.9%
#     bust plate buried      8.7%     18.1%    15.6%          12.7%
#     STANDOFF              0.604     0.744    1.152          0.771
#
# The layering rows are why this is worth having: burial of both overlay plates
# falls by more than half, BACK TOWARD the authored value -- the reported defect
# -- and the standoff row says it cost 0.027u to get there.
#
# It does NOT rescue the base shape's own fold count on its own (1920 -> 1784).
# `antipoke` immediately spends what conform stops taking (+546 -> +705), so the
# shipped total barely moves. That is a measured result about a DIFFERENT pass,
# not a shortfall in this one -- do not read the flat total as this failing.
CONFORM_STRETCH_FIELD = _flag("CBBE2UBE_CONFORM_STRETCH_FIELD", False)
# Relaxation sweeps. Cheap (one sparse accumulate each), and far short of the
# clearance field's 256 because this is a local repair rather than a global
# solve -- the divergence is between immediate neighbours.
CONFORM_STRETCH_ITERS = _knob("CBBE2UBE_CONFORM_STRETCH_ITERS", 32, int)
# Locality/mass term, as CLEARANCE_FIELD_LAMBDA: 0.0 = pure harmonic (infinite
# reach), larger = shorter reach.
#
# SWEPT on the reported bodysuit by conform's OWN added folds, which is the only
# thing this knob controls, with the standoff measured alongside because the
# whole point of this pass over the fold guard is that it does NOT buy folds
# with fit:
#
#     lam/iters      conform folds    shipped standoff (author 0.604)
#     (guard off)          +1911            0.744
#     2.0 / 12             +1048            0.749
#     1.0 / 32              +876            0.757
#     0.5 / 32              +765            0.771
#
# Lower keeps paying and the standoff barely notices -- 0.027u across the whole
# sweep, against the fold guard's 0.408u. Held at the clearance field's own 0.5
# rather than pushed lower: past here the reach starts to exceed the defect,
# and an authored fit redistributed across a whole garment is the failure this
# project has already paid for (#unified-offset).
CONFORM_STRETCH_LAMBDA = _knob("CBBE2UBE_CONFORM_STRETCH_LAMBDA", 0.5)
# Ceiling on the NEW motion this can introduce -- the tangential slide, the one
# component the original pass never authorised. The along-normal component is
# clamped back to what conform asked for, so it needs no budget of its own; this
# is the only way the field can move a vert somewhere conform did not choose,
# and it is therefore the number to hold down and to measure.
CONFORM_STRETCH_TANGENT_MAX = _knob("CBBE2UBE_CONFORM_STRETCH_TANGENT_MAX", 0.5)
# #conform-adaptive-reach. Scale the mass term with the SQUARE of the local edge
# length, so the smoothing reaches the same distance in WORLD units everywhere
# instead of the same number of graph steps. Aimed at the reported collar, which
# is the finest geometry on its garment (median edge 0.385u against the torso's
# 0.625u, 15.1% of its edges under 0.1u) and therefore gets the shortest
# world-space reach from a single global lam. 0 disables and restores one lam.
CONFORM_STRETCH_ADAPTIVE = _flag("CBBE2UBE_CONFORM_STRETCH_ADAPTIVE", True)
# Bound on how far the per-vertex lam may stray from the global one, either way.
# Unbounded, a single sliver triangle sets a lam that either freezes its region
# or smooths it across the whole shape.
CONFORM_STRETCH_REACH_MAX = _knob("CBBE2UBE_CONFORM_STRETCH_REACH_MAX", 8.0)

# #warp-shear-limit -- OPT-IN. Stop the body-delta warp shearing a large triangle
# until it faces INTO the body.
#
# Reported in game as "two black squares at the back waist" on a converted cuirass,
# and NOT the same defect as #conform-fold-guard. Triangles 4877/4878 there are
# the two biggest in the mesh; the warp grew them 2.6x and rotated them past the
# body surface:
#
#     stage    area          stored.outward
#     source   27.5 / 29.5   +0.678 / +0.852   (facing out)
#     output   73.7 / 79.8   -0.288 / -0.572   (facing into the body)
#
# `stored.winding` stays +0.99 the whole way, i.e. winding and normals remain
# CONSISTENT -- so the fold metric, and the conform guard built on it, are both
# structurally blind to this. The triangle is not inverted, it is turned over.
#
# Cause is the same shape as the conform fold: `interp_delta` is per-vertex, IDW
# from each vert's own nearest body verts, with nothing coupling neighbours. A
# big triangle's three corners sample very different deltas at the waist, where
# CBBE and UBE diverge most, and the triangle shears.
#
# THE FIX MUST NOT BE "warp less". Damping the warp is exactly what once left
# every armour CBBE-shaped -- the warp is the only pass that reshapes a rigid
# cuirass to UBE proportions, so a blunt clamp here is far worse than the defect.
# Instead this limits ONLY THE DIFFERENTIAL: a vertex is blended toward the
# ring-average of its neighbours' deltas, never toward zero. At full strength the
# vertex still moves by the local average delta, so the garment still reaches UBE
# size and position; what it loses is the shear between neighbours. Whole-mesh
# area grows 1.8% under the warp against 160% for these two triangles, so a cap
# near 2x is an outlier clamp that touches almost nothing else.
WARP_SHEAR_LIMIT = _flag("CBBE2UBE_WARP_SHEAR_LIMIT", False)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# #warp-delta-outlier -- DEFAULT ON. Stop the warp flinging a LONE vertex.
#
# Distinct from #warp-shear-limit, which bounds how much a TRIANGLE stretches.
# This bounds how far one VERTEX may move relative to its own neighbours, and
# they catch different defects: a vertex can be dragged 4u past its ring while
# every triangle it belongs to stays within an area cap, because the triangles
# simply follow it.
#
# Cause: the delta is IDW-interpolated from each vertex's 4 nearest CBBE BODY
# vertices, independently. A vertex sitting in a crevice -- a boundary corner at
# the waist, ring of 3 -- can have its nearest body vertices land on different
# anatomy from its neighbours', so it receives a completely different delta.
# Measured on one converted cuirass, a MIRRORED PAIR at the waist (two verts,
# x=-8.54/+8.91, z~71) each moved ~3.9u with a roughness of ~4.4u against a mesh
# p50 of 0.16u and p99 of 1.77u. Reported in game as "one broken vert on each
# side". `groove_smooth` shaves a little off and nothing else touches them.
#
# The cap is on the DEVIATION from the ring mean, not on the motion: a vertex
# may travel as far as the warp wants provided its neighbourhood travels with
# it, so broad reshaping is untouched and only the isolated flier is reined in.
#
# DEFAULT ON since 2026-08-11, MEASURED ON A QUANTITY IT DOES NOT OPTIMISE. This
# caps deviation-from-ring-mean, so scoring it on deviation-from-ring-mean would
# be circular. Judged instead on ABSOLUTE surface irregularity (|v - mean(1-ring)|
# of the final surface) of the vertices it actually touched, against the AUTHOR'S
# own value for those vertices -- and it acts hardest exactly where the defect is
# worst. On a five-shape steel cuirass, worst-spike before -> after:
#
#   Skirt   10.335 -> 6.439     Seeves 5.603 -> 5.510    Pants 3.638 -> 3.420
#   HeavyFur 4.034 -> 3.895     SteelArmor.012 3.830 -> 3.820
#
# The costs are real and an order of magnitude smaller: four shapes worse by
# <=0.03, up to +0.545 on UNTOUCHED verts of one fur shape, and on a smooth piece
# (a plated bust top) the touched verts go 0.029 -> 0.076 against a source 0.015.
# A 3.9u spike is a visible broken vertex; 0.05u is not. That asymmetry is the
# whole argument -- not the count of shapes that moved either way.
#
# Its sibling `WARP_PUSH_SHELL_CAP` was measured the same way and STAYS OFF: it
# touches ZERO verts on every shape of that cuirass carrying a large spike
# (Skirt, Pants, Seeves, SteelArmor.012), wins only marginally where it does fire
# (CuirassFur 5.498 -> 5.491), regresses Corset 5.916 -> 5.978 and HeavyFur 4.034
# -> 4.127, and yet accounts for a 1.38u geometry change on its own. Cost without
# a demonstrated benefit.
WARP_DELTA_OUTLIER = not _flag("CBBE2UBE_NO_WARP_DELTA_OUTLIER", False)
# #authored-shape-order (BUG-09). SHAPE ORDER IS OUTPUT STATE, not cosmetics: an
# ARMA `AlternateTextures` entry picks its target shape by INDEX, so permuting a
# NIF's shapes silently re-points every colour variant that names it. Reported in
# game on a white top that loaded the wrong texture AND broke the actor's skin
# texture -- one cause: our injected `BaseShape` had taken index 0, so the white
# fabric swap landed on the BODY and the garment never got its own.
#
# TWO routes moved shapes and both are closed: the body injection now runs after
# pass 2 (that one is structural, no flag), and the textureless collision proxies
# that `_finalize_hdt_physics` drops and re-appends `sorted()` are put back in
# their AUTHORED slots by `_restore_authored_shape_order`, which this flag gates.
#
# A SAFETY INVARIANT, not a preference -- so it is DEFAULT ON with a kill switch
# and deliberately gets no `Setting(...)` row, exactly like the BUG-00 fail-closed
# fix. It is also a NO-OP whenever the order already matches the author, so the
# blast radius is only the pieces that were actually wrong.
AUTHORED_SHAPE_ORDER = not _flag("CBBE2UBE_NO_AUTHORED_SHAPE_ORDER", False)
# #no-invented-chains -- REPLICATE THE SOURCE'S PHYSICS, do not improve on it.
#
# A garment can be RIGGED for a chain (skinned to SkirtBBone01..03, Skirt 1_NN,
# ...) and still ship NO physics reference of any kind -- no SMP XML, no HDT-PE
# path, nothing. The Campfire travel cloaks are the measured example: 8 NIFs,
# zero root or shape extra-data, no XML anywhere for the source path, no global
# SMP config, and the chain bones are not even in the actor skeleton. In game
# that garment is STATIC.
#
# The generate-fresh path used to treat those dead bones as intent and drive
# them with a generated chain, so the piece swung in a way its mod never did.
# That reached 149 of the pack's 357 physics XMLs -- every generated one. The
# authored 208 are copied from source and are NOT affected by this flag.
#
# With chains suppressed, the EXISTING `#chainless-softbody-gate` below takes
# over and emits NO XML at all, leaving the piece kinematic with its baked
# geometric clearance. That is deliberate and it is the only safe shape: a
# literal "collision only" XML (cloth per-vertex + body per-triangle, no
# constrained chain) is the documented FSMP equip-CTD pattern -- an
# unconstrained soft body diverges and the collision SIMD reads out of bounds.
# See `_is_unconstrained_collision_pair`.
#
# Set CBBE2UBE_INVENT_CHAINS=1 to restore the old behaviour for an A/B.
INVENT_CHAINS_FROM_DEAD_BONES = _flag("CBBE2UBE_INVENT_CHAINS", False)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# WHAT COUNTS AS A SMALL FITTING -- a stud, buckle, clasp or rivet, as opposed to
# a strap or a panel. Used to keep `_uniformise_local_scale` off them; see
# `_small_element_mask`. These two outlived the rigid-element pass that introduced
# them (deleted 2026-08-11, see #rigid-small-element-rejected in PIPELINE.md).
#
# Diagonal: the reported belt's fittings are 2-5.5u, a strap on the SAME shape is
# 14-21u and a corset 26.8u, so 6.0 separates them with room either side.
SMALL_ELEMENT_MAX_DIAG = _knob("CBBE2UBE_SMALL_ELEMENT_MAX_DIAG", 6.0)
# Distance below which two vertices are the SAME point split for shading. Well
# under any real gap between two objects; a missed weld only degrades to treating
# one surface as several, which the size gate still bounds.
SMALL_ELEMENT_WELD = _knob("CBBE2UBE_SMALL_ELEMENT_WELD", 0.001)

# #strap-scale-uniform -- see `_uniformise_local_scale`. A strap whose edges run
# 0.52x to 1.51x the author's lengths is crumpled, not bent. OPT-IN until judged
# in game.
STRAP_SCALE_UNIFORM = (
    _flag("CBBE2UBE_STRAP_SCALE_UNIFORM", False))
# Shape-level gate: mean |edge ratio - 1|. The reported strap measures 0.219, the
# chest plate on the SAME garment 0.060 and the outer layer 0.121, so 0.15 fires
# on the crumpled one and leaves the merely-imperfect ones alone. Censused at
# 16.2% of shapes pack-wide.
STRAP_SCALE_MIN_DEV = _knob("CBBE2UBE_STRAP_SCALE_MIN_DEV", 0.15)
# Refuse a shape whose MEDIAN edge is this far from the author's. Past that it is
# mis-scaled rather than crumpled, and uniformising it only makes it evenly wrong.
# The reported strap's median is 1.025; the pack's extremes are 3.7x and 21.3x.
STRAP_SCALE_MAX_GROWTH = _knob("CBBE2UBE_STRAP_SCALE_MAX_GROWTH", 1.5)
# Solver shape, deliberately NOT environment-tunable: these were swept once and
# fixed, and every additional switch is one more thing that can ship set wrong
# (see the flag-surface audit). SMOOTH is what separates "the garment grew"
# (survives neighbour averaging) from "this edge is crumpled" (does not); TOL
# leaves an edge alone once it agrees with its neighbourhood, so this stays a
# repair rather than a resurfacing; ANCHOR pulls back toward the fit's own answer
# so satisfying edge lengths cannot walk the shape off the body.
# #short-edge-cap -- see `_cap_short_edge_stretch`. OPT-IN until judged in game.
SHORT_EDGE_CAP = (
    _flag("CBBE2UBE_SHORT_EDGE_CAP", False))
# What counts as a SHORT authored edge. Sub-millimetre detail -- a tight seam, a
# fold, the rim of a stud. On the reported buckle 28 of 2938 edges are below this
# and the worst was stretched 18x; the leather beside it has 5 and none stretched.
SHORT_EDGE_MAX = _knob("CBBE2UBE_SHORT_EDGE_MAX", 0.05)
# How far past the garment's OWN growth such an edge may still stretch. 3x leaves
# ordinary fit variation alone (the reported piece's long edges grow ~1.09) while
# catching a blow-up: a 0.0229u edge is allowed ~0.075u, not 0.41u.
SHORT_EDGE_SLACK = _knob("CBBE2UBE_SHORT_EDGE_SLACK", 3.0)
SHORT_EDGE_ITERS = _knob("CBBE2UBE_SHORT_EDGE_ITERS", 3, int)

STRAP_SCALE_SMOOTH = 8
STRAP_SCALE_ITERS = 12
STRAP_SCALE_TOL = 0.05
STRAP_SCALE_ANCHOR = 0.05

# A FINAL DE-SPIKE WAS BUILT HERE AND REMOVED -- do not rebuild it without
# reading this. Reported in game as "the belts still have distortion", I capped
# the 1-ring deviation of TOTAL displacement at the end of the geometry
# pipeline, on the theory that the warp-stage cap never sees roughness added by
# later passes. It changed ONE vertex. #no-final-despike
#
# The theory was untestable as posed, because the metric behind it was wrong. It
# measured how far a vertex's DISPLACEMENT deviated from its neighbours',
# relative to another build -- "what moved differently", not "is the surface
# bumpy". A vertex can move very differently from its neighbours and still land
# on a smooth surface.
#
# Measured properly -- absolute Laplacian |v - mean(1-ring)| on the final mesh --
# there is no geometry regression to fix. Verts above 0.5u, source / 1.2 / now:
#   belts        283 / 461 / 488     (p90 0.553 -> 0.559: unchanged)
#   belts_metal   92 / 138 / 139
#   top          163 / 233 / 217     (BETTER than 1.2)
#   corset         0 /   8 /   0     (fixed)
# The author's own mesh already carries 283 bumpy verts on `belts` -- studded,
# segmented geometry, i.e. design. The belts' surface matches 1.2.
#
# The real belt defect is SKINNING, not shape: `belts_metal/belts` weight-row
# divergence is 0.107 against 1.2's 0.027. Two stacked belts deforming
# differently from each other reads as distortion on a piece whose geometry is
# unchanged. Fix follow, not form.

# #warp-push-shell-cap -- OPT-IN. Never push a vertex out THROUGH its own
# garment.
#
# The standoff push exists for exactly one reason: stop body skin showing
# through the garment. It fires on any vertex whose clearance is below the
# floor, including vertices the author deliberately BURIED -- a lining, the
# hidden edge of a flap. Those cannot be where skin shows through, because
# there is more garment in front of them, so pushing them buys nothing; and
# when the push is large it drives them out THROUGH the outer shell, where they
# become a visible spike.
#
# Measured on one converted cuirass, the three worst fliers (a mirrored pair at
# the waist plus one more) sat 2.0-2.5u INSIDE the body in the author's own
# mesh with 10-15 garment triangles outward of them, and the push moved them to
# +1.3u -- straight through the shell. Their own normals were near
# PERPENDICULAR to the body normal they were pushed along (-0.03, -0.10), i.e.
# the correspondence driving the push did not describe them at all.
#
# WHY IT CANNOT REOPEN CLIPPING: the cap binds ONLY where the ray from the
# vertex along its push direction HITS this garment's own surface. Where that
# ray escapes -- the only case in which skin can actually be seen -- nothing
# changes. That is a strictly stronger safety argument than the existing
# smooth-then-revert, which trades a clip for a spike and keeps the spike.
# STAYS OPT-IN. Considered for promotion 2026-08-22 with the other four
# recipe flags and REJECTED: `test_the_shell_cap_is_OFF_and_the_
# measurement_is_why` records that it is INERT on every large spike
# measured and REGRESSES TWO SHAPES while moving geometry by 1.38u.
# A general "looks fine so far" does not overturn a specific negative
# measurement. NOTE: the live user recipe has it ON -- that is worth
# raising with them, not silently blessing.
WARP_PUSH_SHELL_CAP = _flag("CBBE2UBE_WARP_PUSH_SHELL_CAP", False)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# #winding-consistency -- DEFAULT ON. Every written shape leaves CONSISTENTLY
# WOUND: neighbouring triangles traverse their shared edge in opposite
# directions, which is what stops one of the pair being backface-culled.
#
# Culling reads the winding, lighting reads the normals, and nothing else in the
# pipeline checks a surface holds together.
#
# THE ORIGINAL RULE HERE WAS WRONG AND SHIPPED REAL DAMAGE. It flipped any
# triangle whose winding disagreed with its own vertex normals, justified by a
# pack scan -- "sources carry 52683 such triangles, our output 404106". That gap
# is not damage to repair, it is the fit chain moving verts while the stored
# normals do not follow; and the same scan says the AUTHORS ship 52683 of them
# on surfaces that are perfectly consistent. Flipping single triangles inside a
# consistent surface TEARS it, and it tore 611 of 744 paired NIFs
# ([[project_winding_repair_tears_surfaces]]).
#
# The unit is now a consistently-wound REGION, decided by one area-weighted
# vote, with a hard guard that refuses any rewrite raising the seam count.
#
# Default ON because this is not a fit choice -- it changes no vertex, no
# normal, no weight and no UV, only the order of three indices, and it can no
# longer make a surface worse. The escape hatch exists for one scenario: a mesh
# that deliberately ships inside-out geometry and relies on it.
# CBBE2UBE_NO_WINDING_REPAIR=1 disables it.
WINDING_CONSISTENCY_REPAIR = not _flag("CBBE2UBE_NO_WINDING_REPAIR", False)

# #seam-weld-self -- DEFAULT ON. Vertices coincident in the SOURCE must still be
# coincident in the output.
#
# A UV/normal seam is several vertices at one position; they exist because their
# NORMALS differ, and nearly every pass here pushes a vertex along its own
# normal. So the halves of a seam are pushed apart and it opens into a gap that
# shows the unlit interior -- a black shape running along the seam line, which
# is how the user described it. The existing seam machinery welds CROSS-SHAPE
# seams only; a seam inside one shape was never handled.
#
# Default ON: coincidence is an authored invariant of a closed surface, so
# restoring it can only close a hole that should not have opened. It does move
# geometry, though (up to half the split), so the risk is a vertex that a
# clearance pass had deliberately pushed out being averaged back in -- verify
# clipping, not just the seam count. CBBE2UBE_NO_SEAM_WELD_SELF=1 disables.
SEAM_WELD_SELF = not _flag("CBBE2UBE_NO_SEAM_WELD_SELF", False)
# #bust-morph-residual. The bust requirement is met against the BIND body, but
# the character in game is MORPHED, and a nipple can travel 5.35u outward at
# runtime. The armour follows -- `generate_armor_tri` gives a hugging vert the
# delta of its NEAREST body vert (match_w -> 1) -- so what survives is not the
# travel but the RESIDUAL: the poking body point and the garment vert covering
# it are different body verts, and under a slider that reshapes rather than
# merely inflates, their deltas differ in DIRECTION.
# Measured on a studded cuirass: 1 of 23 bust sliders (Big_SaggyBreasts) turned
# +0.284u of clearance into -0.165u while following at ratio 1.00 by magnitude.
# That is exactly why a poke is PRESET-DEPENDENT, and why every bind-pose metric
# read it as clean.
# So: add the residual to the required clearance. Requiring the full 5u travel
# would balloon every garment; the residual is small and affordable.
# --- #bust-authored-nipple-cap -- OPT-IN, default OFF ------------------------
#
# REPORTED IN GAME 2026-09-03: "nipples on certain armor create outlines through
# the plate". Measured on the reported piece, against the author's own mesh:
#
#     lift at the nipple, relative to the same shape's flat chest
#       chest_plate   AUTHOR -0.059u   OURS +0.114u   (we over-lift 0.174u)
#       top           AUTHOR -0.139u   OURS +0.105u   (we over-lift 0.243u)
#     correlation(nipple weight, extra standoff vs author) = +0.44 / +0.50
#
# The author's garment sits CLOSER at the nipple than on the flat chest. Ours
# pushes OUT there. So the bust requirement is not merely too strong -- on these
# shapes it has the WRONG SIGN against what the author built, and the outline
# the user sees is the nipple map embossed into a plate.
#
# WHY IT HAPPENS. `req` ramps with nipple weight (0.12u flat -> 0.68u at the
# tip) and is applied as a hard floor on the push-out:
#     move = np.where(in_bust, np.maximum(move, req - worst), move)
# Nothing in that chain asks what the AUTHOR's clearance was. The ramp exists so
# a tip cannot poke through CLOTH; on a rigid plate it embosses instead, trading
# a defect the plate cannot have for one it visibly does.
#
# WHY NOT A CLOTH/PLATE GATE, which is where this started: the two worst shapes
# on the reported piece disagree about which they are -- `_shape_is_rigid_torso_
# armor` calls both rigid, `_chest_follow_for_shape` calls one 0.35 and the
# other 1.0 -- and BOTH tuck in at the nipple in the author's own mesh. The
# axis that separates them is not the material, it is the AUTHORED RELATIONSHIP.
#
# MEASURED INERT ON THE DEFECT IT WAS BUILT FOR (2026-09-03). Turning it on
# moved the reported plate's nipple lift +0.1143u -> +0.1145u: nothing. The
# reason is in the stage dump -- conform REDUCES the lift (-0.059), and the
# requirement is applied as `move = max(move, req - worst)`, so lowering `req`
# only bites when the conform's own movement is SMALLER. It is not.
#
# The real cause is elsewhere and is now traced: the plate's own chain ends at
# +0.0295u, and the LAYER RIDE then lifts it onto a still-bulging inner layer,
# which it inherits (74% of the visible defect). See
# docs/worklog/NIPPLE_OUTLINE_THROUGH_PLATE.md.
#
# Kept, default OFF, because the invariant is still sound on its own terms --
# do not demand more room than the author left -- and it may earn its keep on a
# piece whose conform DOES bind. But it does not fix nipple outlines, and
# anyone reaching for it for that reason should stop here.
#
# WHAT THIS DOES. Caps the nipple ramp at the extra room the author actually
# left over the same shape's flat chest. Where they left none, the ramp is
# suppressed and the requirement falls back to the flat clearance. It can only
# LOWER `req`, never raise it.
#
# Measured by DISTANCE, not along the source body's normal, deliberately: those
# normals are identically zero at defaults on this path (F011), so a
# normal-based cap would read "the author had zero clearance everywhere" and
# flatten the requirement to nothing. Distance needs no normals and works today.
#
# THE COUNTER-METRIC IS NIPPLE POKE-THROUGH. Lowering a clearance floor can let
# the tip through, which is worse than an outline. `_GROWTH` allows extra above
# the author's own room for a UBE bust larger than the CBBE one they fitted --
# the `#authored-inflate` pattern ("the author's spacing, or enough for the body
# to grow there, whichever is larger"). Default 0.0: prove the cap first, then
# tune the allowance against measured clipping.
BUST_AUTHORED_NIPPLE_CAP = _flag("CBBE2UBE_BUST_AUTHORED_NIPPLE_CAP", False)
BUST_AUTHORED_NIPPLE_GROWTH = _knob(
    "CBBE2UBE_BUST_AUTHORED_NIPPLE_GROWTH", 0.0)
# Nipple weight below which a bust vert counts as "flat chest" when measuring
# the author's own baseline clearance for this shape.
BUST_AUTHORED_FLAT_NIPW = _knob("CBBE2UBE_BUST_AUTHORED_FLAT_NIPW", 0.15)

BUST_MORPH_RESIDUAL = not _flag("CBBE2UBE_NO_BUST_MORPH_RESIDUAL", False)
BUST_MORPH_RESIDUAL_MAX = 1.5   # u -- ceiling on what the residual may demand
# Nipple weight at which the residual is charged IN FULL. Not a taste knob:
# 0.25 (the tip only) leaves the piece poking at -0.141u, 0.10 closes it at
# +0.035u, and 0.05 is IDENTICAL to 0.10 -- saturated, so there is nothing below
# it to win. The ring around the tip has to be charged too, because the body
# point that pokes is not the one carrying the peak weight.
BUST_MORPH_RESIDUAL_NIPW = 0.10
# (moved to nif_convert_trigen.py, 2026-09-01)
# Breast03 = nipple apex; Breast02 partial contributor. Matched without spaces.
NIPPLE_TIP_BONE_WEIGHTS = {"breast03": 1.0, "nipple": 1.0, "breast02": 0.4}

# ---- #back-morph-residual: the same charge, on the UPPER BACK -------------
# The bust residual above is gated on NIPPLE WEIGHT, so the upper back -- which
# has none -- is charged nothing and keeps a BIND-pose requirement only. Reported
# in game as skin through the fur across the shoulder blades, and measured:
#
#   upper back (z95-112, rear), 1438 covered verts on a fur cuirass
#     clearance it has          median 0.494u   p10 0.163u
#     residual it needs         median 0.265u   p90 0.658u   max 1.893u
#     verts SHORT of the need   396 / 1438  (27.5%)
#     deficit to close          p90 0.279u      max 1.552u
#
# The envelope is the WORST over the 49 installed presets that actually drive the
# UBE body -- NOT the sum-of-all-sliders supremum, which is 2-4x larger and is
# recorded as measured-worse twice ("any preset" attempts, -0.279u / 138 poking).
# 64 of 113 installed presets are CBBE/3BA and match almost no UBE slider; count
# matched sliders, not presets, or the envelope silently reads 0.000.
#
# Note bust_z is height-only with no front/back test, so rear verts at z90-102
# were already inside `in_bust` and got the flat floor; what they never got is a
# MORPH allowance. Above z102 they got nothing at all.
#
# DEFAULT ON as of 2026-08-08, together with #clearance-differential. Measured
# over the 6 golden pieces the charge reaches x 4 presets, gated metric, shared
# OFF baseline, 96 piece/preset/region pairs:
#
#     arm     upper back  lower back  breast  uchest   worse  better  worst +
#     BACK         -1513         -91      +2      -5      11      33       +8
#     DIFF          -666        -106      -8    -164       5      41      +16
#     BOTH         -1522        -105     -11    -168       5      49       +3
#
# The two are COMPLEMENTARY IN BOTH DIRECTIONS, which is why neither replaces the
# other: this charge alone leaves the upper chest untouched, the differential
# alone recovers it (-164) but regresses hide-collider's upper chest 0 -> 16 on
# every preset, and enabling both removes that regression entirely. Together the
# worst single regression across all 96 pairs is +3 verts.
#
# It was default OFF while unproven: a fit change on a band nothing had pushed
# before, and the bust version needed a saturation study plus a triangle-interior
# correction before it stopped overinflating.
# NO_-style var to match its sibling BUST_MORPH_RESIDUAL and the GUI's convention
# for a default-ON flag: CBBE2UBE_NO_BACK_MORPH_RESIDUAL=1 disables.
BACK_MORPH_RESIDUAL = not _flag("CBBE2UBE_NO_BACK_MORPH_RESIDUAL", False)
# Ceiling well under the bust's 1.5: the measured deficit is p90 0.279u, so 0.5
# covers the bulk while capping the 1.55u tail that would balloon the piece.
BACK_MORPH_RESIDUAL_MAX = _knob("CBBE2UBE_BACK_MORPH_RESIDUAL_MAX", 0.5)
# The band, matching pose_set's upper_back so the harness and the pass agree.
BACK_RESIDUAL_Z = (_knob("CBBE2UBE_BACK_RESIDUAL_Z_LO", 95.0),
                   _knob("CBBE2UBE_BACK_RESIDUAL_Z_HI", 112.0))
# Rear-facing only, by the body NORMAL rather than by y position: a height band
# with no facing test is what made `bust_z` quietly include the back.
BACK_RESIDUAL_NY = -0.3
BACK_RESIDUAL_HALF_X = 20.0     # beyond this is the arm, bare by design
# FEATHER THE BAND EDGE. A binary zone puts a displacement STEP across the
# triangles that straddle its boundary, and conform's fold guard is the one step
# in the pass that is not per-vertex -- it damps whole triangles, so a straddling
# triangle applies a factor set by its in-band corner to its OUT-OF-BAND corners,
# which then lose displacement they had with the charge off and settle closer to
# the body. Measured, OFF vs ON, out-of-band displacement change:
#     robes-thalmor      0.0000u before the guard -> 1.7191u after
#     chainweld-studded  0.0000u before the guard -> 1.5093u after
#     nameskip-dress     0.0000u before the guard -> 0.1498u after
# Zero before, real after, on every piece: the guard is the transmitter and the
# step is what it reacts to. That is also why bounding the edit made the front
# WORSE (11 -> 18 regressions) -- a smaller step is still a step.
# Same shape as #rear-standoff-feather: ramp INWARD from the edge, so the zone
# interior is unchanged and the charge can only be REDUCED near the boundary.
# FEATHER ONCE: this is the single feather for this field.  #back-residual-feather
#
# MEASURED WORSE, DEFAULT OFF. The transmission finding above is sound, but
# removing the step is not the lever: the guard damps any triangle whose in-band
# corner moves at all, so ramping the magnitude changes WHICH triangles trip it,
# not WHETHER they do. Six golden pieces, four presets, gated metric:
#     flat charge   back 71.1 -> 8.0    back-worse 0   front-worse 11   4.03u
#     feathered     back 71.1 -> 10.8   back-worse 1   front-worse 12   4.03u
# Peak travel identical, back degraded (softbody-dress 1.5 -> 13.5), front cost
# unchanged. Kept behind the constant because the diagnosis is worth preserving:
# set CBBE2UBE_BACK_RESIDUAL_FEATHER to a body-z width to opt back in.
BACK_RESIDUAL_FEATHER = _knob("CBBE2UBE_BACK_RESIDUAL_FEATHER", 0.0)      # body-z
BACK_RESIDUAL_FEATHER_NY = _knob("CBBE2UBE_BACK_RESIDUAL_FEATHER_NY", 0.2)   # normal-y
BACK_RESIDUAL_FEATHER_X = _knob("CBBE2UBE_BACK_RESIDUAL_FEATHER_X", 3.0)    # body-x
# CAP THE MOVE, not just the requirement. `move = max(move, req - worst)` has no
# lower bound on `worst`: where the body already intersects the garment at bind,
# `worst` is deeply negative and the push runs away. Measured on the golden set:
# 3.14u of travel against a 0.5u residual cap, which MANUFACTURED 16 upper-chest
# clipping verts from zero on a piece whose back barely improved (38 -> 34.5).
# The sibling pass guards the same failure with PUSH_REQ_CAP / PUSH_MAX_TOTAL.
#
# A deep bind intersection is another pass's business. This charge exists only to
# buy a MORPH allowance, so it may never ask for more than the allowance it can
# itself compute -- flat clearance plus the residual ceiling. Expressed from the
# constants rather than as a literal so it stays coherent if either moves.
#
# THAT DERIVATION WAS A LIABILITY, and it moved this cap twice without anyone
# asking. `BUST_FLAT_CLEARANCE` stopped being a general "flat clearance" and
# became a BUST-tuned number, so taking it 0.3 -> 0.12 for a chest defect
# silently took this BACK cap 0.8 -> 0.62 -- a 22% cut in a band whose own note
# records "4 fixes, 3 measured worse", re-measured at the new value by nobody.
# It then FAILED a test outright once the bust ceiling went back to 0.9: the
# bust push reached 0.63 against a back cap of 0.62, and the overlap assertion
# that guards `req_back = req.copy()` fired. A bust retune must not be able to
# do that.
#
# So the back keeps its OWN base, pinned at the 0.3 this cap was tuned against.
# Same arithmetic as before (0.3 + 0.5 = 0.8), now immune to the bust knobs.
BACK_MOVE_BASE_CLEARANCE = 0.3
BACK_MOVE_MAX = _knob("CBBE2UBE_BACK_MOVE_MAX", (BACK_MOVE_BASE_CLEARANCE + BACK_MORPH_RESIDUAL_MAX))
# ...except it still did not, and the comment above was the giveaway. The charge
# applies as `max(move, deficit)`, and `move` there is conform PULLING IN toward
# the fit the source author gave the garment. Replacing a -7.64u pull-in with a
# +0.3u push is a 7.94u edit while every capped number stays under 0.8u.
# Instrumented over the golden pieces that fire:
#     fitted-dress       1616 of 1621 edits cancel a pull-in, worst 2.76u
#     chainweld-studded   515 of  515 edits cancel a pull-in, worst 7.64u
#     robes-thalmor       168 of  168 edits cancel a pull-in, worst 5.11u
# So the cap bounded a term that never binds, and BACK_MOVE_MAX was decorative.
# With this ON the charge may raise a vert at most BACK_MOVE_MAX beyond wherever
# conform put it, which is what the constant has always claimed to mean.
BACK_BOUND_EDIT = _flag("CBBE2UBE_BACK_BOUND_EDIT", False)
# CONDITIONAL BY CONSTRUCTION, like `minimum_push`. A piece with nothing to gain
# must exit having moved ZERO verts -- that is what keeps the cost, and the risk,
# off the majority. Measured: the charge fires on 6 of 13 golden pieces, and on
# one of those (hide-collider) it bought 3.5 verts of back while disturbing the
# front, which a floor would have declined outright.
BACK_MIN_DEFICIT = _knob("CBBE2UBE_BACK_MIN_DEFICIT", 0.05)
BACK_MIN_VERTS = _knob("CBBE2UBE_BACK_MIN_VERTS", 24, int)
# One line per piece that fires, so a run is auditable the way min-push is.
BACK_RESIDUAL_VERBOSE = not _flag("CBBE2UBE_BACK_RESIDUAL_QUIET", False)
# Reports the EDIT the charge makes, not the charge itself. Diagnostic only.
# Writes to a FILE, never stdout. `golden_output._convert` runs the worker under
# `redirect_stdout(io.StringIO())`, so a diagnostic that prints is discarded by
# the harness -- which reads as "the pass never fired" while it fires normally.


# Directory for the per-call conform displacement dump. Diagnostic only: names
# the step that carries a back-band edit to an out-of-band vertex.
BACK_DUMP_DISP = os.environ.get("CBBE2UBE_BACK_DUMP_DISP", "").strip()
_BACK_DUMP_N = [0]


def _dump_conform_disp(pre, post, in_back) -> None:
    """One .npz per conform call, indexed by CALL ORDER.

    Builds are deterministic (verified: same-flag repeat moves 0 verts on all six
    golden pieces that fire), so call N in one arm is the same shape as call N in
    another and the arms can be compared without a shape name -- which conform
    never receives.
    """
    import numpy as _np
    i = _BACK_DUMP_N[0]
    _BACK_DUMP_N[0] = i + 1
    try:
        d = Path(BACK_DUMP_DISP)
        d.mkdir(parents=True, exist_ok=True)
        _np.savez_compressed(
            d / f"{i:04d}.npz", pre=_np.asarray(pre, _np.float32),
            post=_np.asarray(post, _np.float32),
            in_back=(_np.zeros(len(pre), bool) if in_back is None
                     else _np.asarray(in_back, bool)))
    except OSError:
        pass



# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# #smp-collision-only-antipoke -- DEFAULT ON since 1.2. Let the anti-poke pass
# reach a shape that carries SMP rigging but is NOT itself simulated per-vertex
# (its XML only names it as a collider). Such a shape otherwise gets no bust
# clearance at all, so the body pushes straight through it. Measured 6.3% -> 3.3%
# exposed on a collider-only cuirass, then run over a full pack and used in game.
# Pushing verts outward on a CONVEX region has backfired before (see the gate
# comment in convert_nif_phase2), so the escape hatch stays:
# CBBE2UBE_NO_SMP_ANTIPOKE=1 disables it.
SMP_COLLISION_ONLY_ANTIPOKE = not _flag("CBBE2UBE_NO_SMP_ANTIPOKE", False)
# #mixed-cloth-clearance -- DEFAULT ON (was opt-in / default OFF when built).
#
# The anti-poke/bust/nipple clearance pass is skipped PER SHAPE for anything
# carrying SMP rigging. But a single shape routinely holds BOTH a simulated
# skirt and ordinary skinned cloth, and the whole shape is excluded on account
# of the simulated part. Measured on the leather cuirass: `bodyREVISE` is one
# shape whose skirt is chain-driven (34% of verts) and whose CHEST carries
# 0.0% chain weight over all 416 verts -- so no bust or nipple target can ever
# land on it, and every clearance knob reads as INERT there. That is why
# CONFORM_BUST_CLEAR and BUST_CLEAR both moved 0 verts on that piece, including
# a 5.0 control that should have moved everything.
#
# CENSUS of the shipped pack (1658 pieces, 314 with physics):
#     cloth shapes skipped by the clearance pass   482
#       entirely chain-driven (skip is correct)     90
#       MIXED, carry zero-chain verts too          392   (81%)
#     verts inside skipped shapes            1,289,840
#       of those with ZERO chain weight        730,992   (56.7%)
#     pieces with excluded CHEST cloth             198
#
# So the pass runs, and then every SIMULATED vertex is restored to its
# pre-pass position -- the sim's rest state is bit-identical, and only cloth
# that carries no chain weight at all is allowed to move.
#
# DEFAULT ON since 2026-08-22. It was OFF "because this touches clearance on 392
# shapes and the last two defaults flipped on a single piece's verdict both
# shipped bugs" -- a good reason, and the thing that answers it is NOT another
# single piece. It is a WHOLE-PACK verdict: this flag had been ON in the live
# recipe for weeks, the 2026-08-22 reconvert was built with it ON across all
# 163 mods, and the user's in-game verdict on that pack was "everything looks as
# it should". The default now MATCHES THE ONLY CONFIGURATION ANYONE HAS EVER
# JUDGED. See the block comment above `_DEFAULTS_PROMOTED_2026_08_22`.
MIXED_CLOTH_CLEARANCE = _flag("CBBE2UBE_MIXED_CLOTH_CLEARANCE", True)
# A vert is SIMULATED (and therefore untouchable) at or above this weight on any
# bone the ACTOR skeleton cannot resolve, i.e. a custom physics-chain bone.
MIXED_CLOTH_CHAIN_EPS = _knob("CBBE2UBE_MIXED_CLOTH_CHAIN_EPS", 0.01)
# Push budget for that path. NOT the 3.0 default: measured offline on the traced
# hide cuirass, exposure went 6.9% -> 2.4% at 1.0 but only 5.7% at 3.0 and WORSE
# (8.1%) at 0.6. The optimum sits exactly at ANTIPOKE_BUST_CLEAR -- i.e. push far
# enough to reach the clearance target and no further, which is what stops the
# vert-spreading that opens new gaps on a rounded volume.
# ...but that optimum was measured AT BIND POSE, which by construction cannot see a
# motion defect. The live body physics config permits the breast chain -6.0..+3.0
# units of linear travel in its largest axis (1-2 in the others) against a clearance
# of 1.0, so the budget is several times short of the motion it exists to absorb.
# Hence tunable SEPARATELY from the clearance: the two answer different questions
# ("how far off the body at rest" vs "how far does the body travel").
#
# NOT simply raised here -- a bigger push has its own measured failure (spreading
# verts on a convex region). Raise it deliberately, in game, one step at a time.
SMP_ANTIPOKE_MAX_PUSH = float(
    os.environ.get("CBBE2UBE_SMP_ANTIPOKE_PUSH", "").strip() or ANTIPOKE_BUST_CLEAR)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)

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


# (split step 8) _BUTT_JIGGLE_CAP, _LEG_BEND_MASS_MIN, SKIN_PARTITION_BONE_CAP, _CHEST_ANCHOR ... live in nif_convert_weights.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_weights import (  # noqa: E402
    _BUTT_JIGGLE_CAP, _LEG_BEND_MASS_MIN, SKIN_PARTITION_BONE_CAP,
    _CHEST_ANCHOR, _CHEST_JIGGLE_CAP, SCALE_BONE_K, SCALE_BONE_MAX_TRANSFER,
    SCALE_BONE_REACH, RESKIN_FAR_DIST, RESKIN_K, RESKIN_NEAR_DIST,
    _match_limb_motion_to_body, _match_leg_motion_to_body,
    _match_arm_motion_to_body, _match_spine_motion_to_body,
    _match_spine_twist_to_body, _match_full_weights_to_body,
    _match_rigid_leg_bend_to_body, _match_coincident_cross_shape_skin,
    _match_seam_skinning, _cap_weight_roughness_to_author,
    _hold_weights_at_smp_boundary, _sync_weight_partner_jiggle,
    _transfer_body_jiggle_to_fitted, _conform_fitted_to_body,
    _conform_weights_core, _restore_emptied_bones, _cap_skin_bone_count,
    _cap_weights_map, _cap_and_renormalise_rows, _fill_zero_weight_verts,
    _set_override_vert_weights, _weights_to_index, _weights_from_index,
    _strip_genital_weights_map, _strip_jiggle_weights_map,
    add_scale_bone_weights, compute_body_blend_skinning,
    _slot_aware_reskin_band, _slot_aware_scale_bone_reach,
    _sync_chest_layered_cloth_weights, _sync_abdomen_layered_cloth_weights,
    _source_bust_weight_map, _body_jiggle_weight, _body_jiggle_ref,
    _body_breast_motion_weight, _jiggle_region_of, _jiggle_transfer_vert,
    _chest_match_vert, _chest_match_strength, _butt_match_vert,
    _butt_match_strength, _leg_deform_match_vert, _cached_scale_bone_data,
    _align_scale_bone_stbs_to_verts, _adjust_skin_to_bone_baked,
    _is_scale_bone, _is_breast_bone, _is_leg_rigid_bone,
    _is_physics_jiggle_scale_bone, _is_soft_body_physics_bone,
    _is_genital_anatomy_bone, _is_arm_hand_bone, _is_torso_parity_bone,
    _is_physics_evidence_bone, _is_skeleton_bone,
)



# (moved to nif_convert_trigen.py, 2026-09-01)


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
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# --- Authored-aware outward push (#authored-inflate) ----------------------
#
# RE-MEASURED 2026-08-23: THE FLAG FIRES ON THE COPY PATH AND IS INERT ON THE
# BODY-SWAP PATH. It splits the pack, which is worse than being uniformly dead
# because a verdict measured on one path does not transfer to the other.
#
#   copy       16 shapes / 6 pieces: median |shipped - authored| 0.077u -> 0.043u
#              (-44%), 10/16 shapes closer to the author, clipping +36 verts
#              (+2.1%) -- the small clip rise is the MONOTONE design working,
#              since a reduced push leaves cloth nearer the skin by construction.
#   body-swap   9 shapes / 5 pieces: 0.081u -> 0.092u, 2/9 closer, and the
#              census's own defect shape does not move at all (a bra the author
#              held 0.33u off the skin ships at 1.19u in BOTH arms).
#
# THERE WERE THREE REASONS. (3), FOUND 2026-09-05, IS THE ONE THAT MATTERED:
# NEITHER OF THE OTHER TWO COULD HAVE HELPED WHILE IT STOOD.
#
# (3) THE FLOOR WAS DISARMED ON EXACTLY THE BANDS IT WAS AIMED AT. It read
# `floor = max(authored, ARMOR_TO_SKIN_BUFFER + min(amp, AUTHORED_INFLATE_AMP_CAP))`,
# i.e. the FULL outward morph amplitude, while the push it caps allocates
# `ADAPTIVE_CLEARANCE_BASE + ADAPTIVE_CLEARANCE_MORPH_FACTOR * amp` -- a fifth of
# it. On the UBE body this pack fits, breast amplitude p50 is 3.944u, so the
# floor stood at 0.15 + 1.5 = 1.650u: ABOVE our own shipped 1.436u and the
# author's 0.948u alike. `authored` could not win that `maximum` on a breast for
# any author and any garment, so the pass was inert there by construction --
# which is why the census's own defect shape "does not move at all in BOTH
# arms". Same shape as the BUST_CLEAR ceiling found inside a `maximum`.
# Fixed by `_authored_floor_amp_room`: the floor now allocates headroom by the
# same rule as the ramp it caps. Bust floor 1.650 -> 1.039, belly 1.650 -> 0.800,
# butt 0.265 -> 0.273 (the low-morph bands were already tighter and do not move).
#
# THE OTHER TWO ARE REAL AND BOTH ARE NOW CLOSED.
#
# (1) THE FLOOR IS BLIND ON PHASE 2. The floor reads
# the authored standoff as `dot(src_armor - src_body, src_body_normal)`, so a
# zero normal makes `authored` zero for every vertex and the floor collapses to
# the plain buffer, carrying no authored information at all. The copy path builds
# that normal with `_cached_cbbe_body_normals`, which RECOMPUTES when the stored
# ones are unusable; phase 2 uses the STORED normals unless `_SRC_NORMAL_FIX` is
# on, and it is default off. Measured on this modlist's CBBE base: 18436 of
# 18436 stored normals are zero-length (100%), matching the "18 of 21 sampled
# inline bodies" already recorded at the phase-2 site.
# CLOSED 2026-09-05 two ways: phase 2 now builds `src_body_n_authored` with the
# hardened fetch UNCONDITIONALLY and hands that to both authored floors (leaving
# `conform` on `_SRC_NORMAL_FIX`, whose constants were tuned around the zeros),
# and `_authored_normals_usable` makes a floor REFUSE rather than silently
# degrade to its constant term when it cannot read the author at all.
#
# (2) A LATER PASS OVERWRITES THE RESULT, which is why (1) is not worth fixing on
# its own. Stage-traced on that bra (author 0.33u, ships 1.19u):
#
#     s02_warp 0.370 -> s03_inflate 1.241 (+0.872) -> s04_conform 0.952
#     -> s06_panel_rigidity 0.851 -> s07_antipoke 1.294 (+0.443) -> ships 1.193
#
# TWO passes set the standoff, not one. This floor is monotone at `s03` only, so
# `s07_antipoke` pushes back out regardless of what it does. Measured: turning on
# the source-normal fix alone moved the aggregate 0.257u -> 0.262u, adding this
# floor 0.264u, and adding `#authored-antipoke` as well 0.264u -- the bra reads
# 1.19 -> 1.20 in every one of those arms. Anti-poke's own floor deliberately
# refuses to relax where the body morphs outward, and a bra is on the bust, so
# the bust case is policy (morph headroom) rather than an oversight. Any real fix
# here has to move BOTH pushes, and has to be judged in game against poke-through
# rather than on bind-pose standoff alone.
#
# The superseded 2026-08-13 note claimed the flag "CANNOT FIRE AT THE SHIPPING
# DEFAULTS" because `CLEARANCE_FIELD_INFLATE`'s solve "returns before the floor
# is consulted". That is not what the code does: the floor rewrites `push_len`,
# and the solve then takes `push_len` as its lower bound (a8a70b2 says so at the
# call). What the solve skips is the ADDITIVE APPLICATION below it, not the
# floor. Anyone re-reading that note would have skipped the one lever aimed at
# this defect.
#
# `inflate_armor_outward` is ADDITIVE: it adds its per-vert magnitude to
# whatever standoff a vertex already has, and it has never known where the
# AUTHOR put that vertex. So it pushes a garment that is already sitting at the
# author's spacing further out anyway.
#
# The pack census (160/160 mods, 13,889 shape-pairs, 32.1M verts) measured both
# halves of that. Deleting the pass is NOT the answer -- clearance got worse on
# 4.1x as many shapes as it helped, so it stays. But on the 6738 shapes it
# actually moves, removing it moved 67.5% of them CLOSER to the author's fit.
# That over-push is the cost being paid, and it is what this addresses.
#
# Stated as a FLOOR instead of an addition:
#
#     floor    = max(authored standoff, buffer + min(morph amplitude, cap))
#     ceiling  = what the additive pass would have produced (UNCHANGED)
#     required = min(ceiling, max(current, floor))
#     push     = max(0, required - current)
#
# MONOTONE BY CONSTRUCTION: `required <= ceiling`, so this can only ever push a
# vertex LESS than today, never more. Over-inflation therefore cannot get worse,
# and the entire risk surface is "did the floor come out too low somewhere",
# which the clearance counters in the census measure directly.
#
# Why it is not the `#unified-offset` floor that measured 50% worse: that one
# used inflate's own magnitude as the floor value, which is inert (it lifted
# 5.6% of verts against inflate's 75% reach) -- AND it was computed when
# `conform`'s authored standoff read identically ZERO, so "authored" carried no
# information at all. Both halves of that are different here.
#
# PROMOTED AND THEN REVERTED ON 2026-09-05, SAME DAY. The floor is now correct
# and can bind (see (3) above); what is NOT settled is whether the surface cost
# of arming it is acceptable, and the answer from the only judge that counts was
# no -- "we can't use that in its current state". DEFAULT OFF until the fold
# regression is designed out, not tuned away.
#
# The mechanism stays fixed and armable so the next attempt starts from a floor
# that works rather than re-deriving why it never did. Arm BOTH halves together
# (`CBBE2UBE_AUTHORED_INFLATE=1 CBBE2UBE_AUTHORED_ANTIPOKE=1`) and read the table
# below before drawing anything from a single-flag arm.
#
# Measured over 51 shapes (38 body-swap, 13 copy), pinned seed, repeat control
# identical:
#
#     arm                     copy path    body-swap    folds    inverted
#     control                   +0.288       +0.572     16355      2060
#     this flag ALONE           +0.178       +0.606     16383      2062
#     both floors               +0.178       +0.483     16795      2194
#
# ON ITS OWN THIS FLAG MAKES THE BODY-SWAP PATH WORSE. Capping inflate leaves
# the cloth nearer the body at `s03`, and anti-poke's push is `req - worst`, so
# it simply pushes further from the lower start. Ship both or neither.
#
# Together: 44 shapes of 51 closer to the author, 4 worse, median -0.124u; the
# whole penetration cost is 6 verts on 3 shapes; zero-weight bones 0 in both
# arms; and the `[clip-risk]` counter improves (a torso 43 verts inside -> 0, a
# panty 136 -> 91). The cost is surface -- folds +2.7%, inverted +6.5%, carried
# entirely by the anti-poke half and concentrated on loose dresses (48 pieces
# improve, 44 worsen), where the author ships ZERO inverted triangles. That cost
# is why the default is back off.
#
# WHAT NOT TO TRY NEXT, both already paid for: feathering the authored target
# (`#authored-floor-feather`, kept at 0 with its numbers) recovers 22% of the
# folds and gives up 94% of the copy-path win, so the rise is not target
# roughness; and arming one half alone is worse than arming neither. The open
# lead is the per-shape property separating the 48 pieces that IMPROVED from the
# 44 that worsened -- same prescription F068 and `#surface-warp-field` got.
# PROMOTED TO DEFAULT ON 2026-09-09, to match what actually ships. The
# deployed recipe has set this since before the 2026-09-06 reconvert, so
# the pack has been built with it ON while the code said OFF -- and the
# code is what every reader consults. `flag_retirement --recipe` now
# measures that split; it was 5 flags, all in this direction.
# Kill switch: CBBE2UBE_NO_AUTHORED_INFLATE=1
AUTHORED_INFLATE = (not _flag("CBBE2UBE_NO_AUTHORED_INFLATE", False))
# The same floor for the ANTI-POKE (`clear_armor_outside_body`). Separate flag
# because it is a separate pass with a different safety story: anti-poke is the
# LAST line against skin through steel, so its floor must never drop below the
# body-growth allowance, and it is judged on the morph counters rather than at
# bind pose. See `#authored-antipoke` at the push site.
# Was reverted alongside its inflate twin on 2026-09-05 and PROMOTED WITH IT
# again on 2026-09-09 -- see the table there, and the note below for why the
# revert's cost figure predates the fix that answered it. This half is what reaches the BODY-SWAP path (+0.572 -> +0.483) and it
# is also what carries the whole surface cost (+440 folds, +134 inverted; the
# inflate half alone is +28/+2), so it is the half any future guard has to
# target. It must be armed and disarmed TOGETHER with the inflate floor: alone,
# either one is worse than neither on one of the two paths.
# PROMOTED TO DEFAULT ON 2026-09-09, to match what actually ships. The
# deployed recipe has set this since before the 2026-09-06 reconvert, so
# the pack has been built with it ON while the code said OFF -- and the
# code is what every reader consults. `flag_retirement --recipe` now
# measures that split; it was 5 flags, all in this direction.
# Kill switch: CBBE2UBE_NO_AUTHORED_ANTIPOKE=1
AUTHORED_ANTIPOKE = (not _flag("CBBE2UBE_NO_AUTHORED_ANTIPOKE", False))
# How much of the body's local outward morph the floor must cover. The margin
# has to be the body's OWN morph amplitude -- the converter never sees the
# player's preset -- and it has to be CAPPED, because the belly's runs to 8.7u
# and a floor that tracked it would fling loose drape outward. Same lesson as
# `#chain-rest-outside-body`.
AUTHORED_INFLATE_AMP_CAP = _knob("CBBE2UBE_AUTHORED_INFLATE_AMP_CAP", 1.5)

# Fraction of the BODY's maximum nipple weight above which a vertex counts as
# the TIP, where neither authored floor may relax the clearance rules.
# #authored-nipple-exempt
#
# The author fitted their garment over a CBBE bust; UBE's protrudes further, so
# their spacing under-provisions ours at the tip by a fixed amount however
# careful they were, and `_authored_floor_amp_room` does not cover it (that
# reserves headroom for the body's MORPH amplitude; this is a STATIC difference
# between two bodies). Reported in game as nipples through a leather cuirass
# "by the smallest amount" -- measured as tip clearance p50 1.193u -> 1.085u
# over the 36 pack pieces that cover the nipple, 26 of 36 tighter.
#
# 0.5, so the exemption is the tip and its immediate shoulder rather than the
# whole breast: the bust-gap win these floors exist for comes from the DOME, and
# exempting the dome would give the win back. Raise it toward 1.0 to exempt less
# (more gap win, less tip clearance), lower it to exempt more.
AUTHORED_NIPPLE_EXEMPT = _knob("CBBE2UBE_AUTHORED_NIPPLE_EXEMPT", 0.5)

# How far from a tip body vertex a garment vertex still counts as "over the
# tip", in units.  #authored-nipple-exempt
#
# A RADIUS, NOT THE NEAREST VERTEX, AND NOT THE k-NEIGHBOURHOOD EITHER. The
# garment is coarser than the body, so "my nearest body vertex is a tip vertex"
# selected 1 garment vert of 1700; widening to the anti-poke's own 6 nearest
# body verts only reached 3, because those six sit in a patch smaller than the
# nipple. Both under-select the thing being protected. Measured tip counts on
# one shape as the rule widens: 1 -> 3 -> the radius form below.
#
# 2.0u is a nipple-sized neighbourhood rather than the anti-poke's 4.0u search
# radius: at 4.0 the exemption covers much of the breast dome, which is where
# the bust-gap win these floors exist for actually comes from.
AUTHORED_NIPPLE_RADIUS = _knob("CBBE2UBE_AUTHORED_NIPPLE_RADIUS", 2.0)

# Mesh rings the tip mask is grown by after the radius test.
# #authored-nipple-exempt
#
# A garment is far coarser than the body and what needs protecting is a SURFACE.
# On the worst-affected piece the nearest garment vertex to the tip is 1.64u away
# and only 6 of 1700 fall within 2u -- yet rays from the tip still strike it,
# because the triangle spanning the nipple has all three corners outside any
# sensible radius. Without the dilation the exemption leaves exactly that
# triangle free to be relaxed.
AUTHORED_NIPPLE_RINGS = _knob("CBBE2UBE_AUTHORED_NIPPLE_RINGS", 2, int)


def _nipple_tip_mask(armor_verts, body_verts, body_nipple, *, frac, radius,
                     tris=None, rings=None):
    """Per-vertex WEIGHT in [0,1] for how much a garment vert covers the body's
    nipple TIP: 1 at the tip, 0 by `radius`, spread over the mesh's topology.

    Returns None when there is no usable nipple map, which leaves the caller on
    exactly the unexempted behaviour. Calibrated on the BODY's own maximum
    weight, never the shape's subset: a garment over the flat chest has a local
    maximum near zero and a fraction of that would exempt the whole piece.

    THE DILATION IS THE LOAD-BEARING PART, because a garment is far coarser than
    the body and what needs protecting is a SURFACE, not a vertex set. On the
    worst-affected piece the garment's nearest vertex to the tip is 1.64u away
    and only 6 of its 1700 verts fall within 2u -- yet rays cast from the tip
    still strike it, because the triangle spanning the nipple has all three
    corners outside any sensible radius. Masking vertices alone therefore leaves
    exactly the triangle covering the nipple free to be relaxed; growing the mask
    over the 1-ring pulls in the corners of every triangle that touches the
    region, which is what actually holds that surface up.
    """
    if body_nipple is None:
        return None
    try:
        nw = np.asarray(body_nipple, dtype=np.float64)
        bv = np.asarray(body_verts, dtype=np.float64)
        av = np.asarray(armor_verts, dtype=np.float64)
        if nw.shape[0] != bv.shape[0] or not len(av):
            return None
        wmax = float(nw.max()) if nw.size else 0.0
        if wmax <= 0.2:              # a flat panel's noise, not a nipple map
            return None
        tip = np.where(nw >= float(frac) * wmax)[0]
        if not len(tip):
            return None
        from scipy.spatial import cKDTree
        d = np.asarray(cKDTree(bv[tip]).query(av, k=1)[0], dtype=np.float64)
        # A HARD MASK, and the softer alternatives were MEASURED AND REJECTED.
        # Ramping the weight from 1 at the tip to 0 at `radius` looks more
        # principled and scores worse where it matters: tip clearance p05 0.439u
        # feathered against 0.454u hard (the old build is 0.451u), because a
        # partial exemption is still a partial relaxation of the one feature that
        # must not be relaxed. Dropping the topology spread entirely is not
        # merely weaker but DANGEROUS -- the weight then exists on a handful of
        # scattered verts and the bust-band penetration counter went to 465
        # against 12, which is a spiky requirement field, not a gentler one.
        w = (np.asarray(d) <= float(radius)).astype(np.float64)
        rings = AUTHORED_NIPPLE_RINGS if rings is None else rings
        if tris is not None and int(rings) > 0 and (w > 0).any():
            t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
            if len(t):
                # Spread by the 1-ring MAXIMUM, so every corner of a triangle
                # that touches the tip inherits the tip's weight. Without this
                # the triangle actually covering the nipple keeps three low
                # corners and is relaxed anyway.
                for _ in range(int(rings)):
                    tw = w[t].max(axis=1)
                    nxt = w.copy()
                    np.maximum.at(nxt, t.ravel(), np.repeat(tw, 3))
                    if np.allclose(nxt, w):
                        break
                    w = nxt
        return w
    except Exception as _e:
        _note_pass_failure("_nipple_tip_mask", _e)
        return None

# The source body is the SAME array for every shape in a NIF, and the CBBE base
# is the same for the whole run, so building its KD-tree per shape is pure
# waste. Measured: the tree is 1.2ms over an 8k inline body and 4.0ms over the
# 18,436-vert CBBE base, against ~5ms for the query that actually needs doing.
# Keyed on id(), which is only safe while something holds the array alive --
# so the entry KEEPS THE ARRAY, and identity is re-verified on hit. Without
# that, a freed array's id can be reused and the cache would hand back a tree
# built over different geometry.
_AUTHORED_SRC_TREE: "dict[int, tuple]" = {}


def _authored_src_tree(sb):
    key = id(sb)
    hit = _AUTHORED_SRC_TREE.get(key)
    if hit is not None and hit[0] is sb:
        return hit[1]
    from scipy.spatial import cKDTree
    tree = cKDTree(sb)
    if len(_AUTHORED_SRC_TREE) > 8:      # a run needs 1-2; never let it grow
        _AUTHORED_SRC_TREE.clear()
    _AUTHORED_SRC_TREE[key] = (sb, tree)
    return tree

# --- Authored fit in STATIC zones (#static-authored-fit) -----------------
# `conform_to_source_standoff` deliberately leaves tight cloth looser than the
# author had it: it floors the target at `min_clearance` (0.25) and only reels a
# skin-hugging vert `blend_tight` (0.3) of the way back. Its stated reason is
# that the source was fitted to the SMALLER 3BA body, so a tight piece needs
# extra room on the bigger UBE one.
#
# That reasoning holds where the UBE body is bigger and MOVES -- bust, butt,
# belly. It does not hold at a shoulder, a back or an upper chest, and those are
# exactly where it showed: measured per-vertex across 8.1M verts of the shipped
# pack (loose + BSA sources), verts the author placed at 0.10-0.25u ship
# +0.346u further out (p90 +1.00u), the largest push of any band, landing them
# at or above the 0.25 floor. Loose verts (>1u) move +0.034u. So the tighter the
# author fitted it, the more we inflate it -- backwards. One reported armour
# fits its mail layer at 0.055u on the shoulder and ships it at 0.472u.
#
# So both limiters ramp OFF as the body's outward morph amplitude falls: full
# authored fit in static zones, current behaviour untouched where the body
# actually grows. Still pull-IN only, and the bust band's push-out is applied
# after this and is unchanged -- this cannot create nipple poke-through.
STATIC_AUTHORED_FIT = (
    not _flag("CBBE2UBE_NO_STATIC_AUTHORED_FIT", False)
)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# #derived-clearance -- OPT-IN. Aim conform at (authored standoff + the
# clearance the worst slider takes away) per vertex, instead of keeping a fixed
# fraction of the authored gap as an unmeasured margin. See the target
# computation in `conform_to_source_standoff` for the measurement that motivates
# it. DEFAULT OFF: it changes conform's target for every fitted piece.
DERIVED_CLEARANCE_TARGET = _flag("CBBE2UBE_DERIVED_CLEARANCE", False)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# --- Phase-1 source-standoff conform (#phase1-conform) -------------------
# Phase 1 carries TWO `inflate_armor_outward` call sites and NO conform, while
# phase 2 has both. So a phase-1 piece gets clearance ADDED with nothing to reel
# it back to the author's fit -- it inflates and never returns. Phase 1 is the
# large majority of files, so this asymmetry, not any per-armour quirk, is why a
# tightly-fitted piece ships standing off the body.
#
# The conform needs a SOURCE body to measure the authored standoff against.
# Phase-1 pieces frequently ship no inline body -- part of why they are phase 1 --
# so the CBBE base body already loaded for the warp (`cbbe_body_path_p1`) is used
# as the reference. That is the body the source was fitted to, and it is the same
# geometry the warp itself is keyed on, so the two passes agree by construction.
#
# DEFAULT OFF until the census + clipping A/B justify it. Clearance exists to
# stop poke-through; the clip metric has no upper bound while overinflation is
# invisible to it, so flipping this on unmeasured trades a visible defect for an
# invisible one. Enable with CBBE2UBE_PHASE1_CONFORM=1.
PHASE1_CONFORM = (
    _flag("CBBE2UBE_PHASE1_CONFORM", False)
)

# --- #phase1-bust-clearance -- OPT-IN, `CBBE2UBE_PHASE1_BUST_CLEARANCE=1` ---
# THE CLEARANCE HALF OF THE CONFORM, WITHOUT THE PULL-IN HALF, on the copy path.
#
# WHY IT IS NOT `PHASE1_CONFORM`. That flag runs the WHOLE pass, whose dominant
# effect here is reeling an over-projected vert back toward its source standoff.
# Measured verdict: it buys 0.022u of standoff and costs +22% clipping, so it
# stays off. But the same function also carries the bust CLEARANCE block --
# `#bust-morph-chord` and `#bust-surface-req` -- which only ever pushes OUT
# where the morphed body would otherwise come through. Those are opposite
# motions that happen to live in one function, and only one of them is unwanted.
#
# WHY IT MATTERS. Measured 2026-08-27, pack census on the bust band under Punk
# UBE (768 examined / 257 scored, 0 error exclusions): the "clean at bind but
# clipping morphed" class is 28% of body-swap pieces AND 28% of copy-path
# pieces -- identical. The copy path is ~78% of the pack, so roughly 50 pieces
# carry the chord defect and receive NO chord charge at all. The BUG-15 census
# excluded them as "out of scope", which was never evidence they were healthy.
# CONFIRMED, not inferred: on `OldArmor/RustArmorF_1` (16.998% -> a copy-path
# chord candidate) `CBBE2UBE_BUST_MORPH_CHORD=0` moves **0 verts**, so the
# charge provably cannot reach this path today.
#
# HOW. Call the SAME `conform_to_source_standoff` with `blend=0.0`. Its own
# docstring defines that as "no conform": `target = s_cur`, so `move` starts at
# 0 and only the clearance block can raise it (`max(move, deficit)`), leaving a
# push-out-only pass. Reusing the function rather than lifting the block keeps
# ONE surface rule with two call sites -- the failure mode this file keeps
# re-learning is two implementations of one concept drifting apart.
#
# SAFE BY CONSTRUCTION: with no deficit anywhere `move` stays 0, `disp` is 0,
# and the relax + fold guard operate on a zero field, so the pass is a true
# no-op. DEFAULT OFF pending an A/B and an in-game verdict.
# PROMOTED TO DEFAULT ON 2026-09-09, to match what actually ships. The
# deployed recipe has set this since before the 2026-09-06 reconvert, so
# the pack has been built with it ON while the code said OFF -- and the
# code is what every reader consults. `flag_retirement --recipe` now
# measures that split; it was 5 flags, all in this direction.
# Kill switch: CBBE2UBE_NO_PHASE1_BUST_CLEARANCE=1
PHASE1_BUST_CLEARANCE = (
    not _flag("CBBE2UBE_NO_PHASE1_BUST_CLEARANCE", False)
)

# --- #phase1-nipple-map -- OPT-IN, `CBBE2UBE_PHASE1_NIPPLE_MAP=1` -----------
#
# The copy path's conform runs WITHOUT the body's nipple map (and without
# `conform_margin`), so its bust clearance is the FLAT requirement while the
# body-swap path's ramps up toward the nipple. Same function, two algorithms:
# a verdict on one population does not transfer to the other (2026-09-01
# audit, F094). With this on, both copy-path conform sites receive the same
# `ube_body_nipple` / `conform_margin` the body-swap site passes, computed
# once per piece from the UBE reference body (the same array the copy path
# fits against, so the map is aligned). DEFAULT OFF until a paired A/B on the
# copy-path sample (bust-band morph clip under >= 2 presets) and the
# body-swap negative control (0 verts moved) say otherwise.
PHASE1_NIPPLE_MAP = _flag("CBBE2UBE_PHASE1_NIPPLE_MAP", False)

# --- #phase1-antipoke -- OPT-IN, `CBBE2UBE_PHASE1_ANTIPOKE=1` ---------------
# GIVE THE COPY PATH THE BODY REPAIR IT HAS NEVER HAD (BUG-02).
#
# The copy path runs NOTHING that pushes a vert out of the body in its normal
# branch. `snap_armor_outside_body` looks like it does -- `FIT_STAGES` even
# listed it as a both-paths stage -- but it is the ELSE of "a CBBE base body
# exists", so every BodySlide output takes the other arm and gets
# warp/inflate/conform/groove-smooth and no body repair at all.
# `clear_armor_outside_body` and `_inflate_cloth_over_bust_butt` WERE both
# body-swap only. So the copy path, 78% of the pack, shipped whatever the fit
# chain left inside the body.
#
# ONLY THE FIRST OF THE PAIR MOVED. `#phase1-antipoke` (below) brought
# `clear_armor_outside_body` to the copy path on 2026-09-02; its `elif`
# sibling `_inflate_cloth_over_bust_butt` -- the branch phase 2 runs INSTEAD
# for physics/soft cloth, which the anti-poke must skip -- did NOT. So on the
# copy path that cloth is restored to its pre-anti-poke position and gets no
# body repair at all, where phase 2 gives it the bust/butt inflate. The
# asymmetry is narrower than it was, not closed; it is the same "clearance
# skipped PER SHAPE" class as the unreachable chest cloth. Unmeasured.
#
# SIZED, from an in-game report (the reported trousers: copy path, no physics XML,
# one shape):
#     the AUTHOR's own pants   0 of 2594 butt-band verts inside their CBBE body
#     ours                  1535 of 2589 inside the UBE body (59.3%, worst 0.929u)
# 687 of those sit at 0.2-0.6u. Pack census: butt bind-pose penetration is 75%
# of copy pieces vs 32% of swap.
#
# This runs phase 2's own anti-poke, in phase 2's own position (after panel
# rigidity, before the chain blend), against the body the copy chain already
# uses. Parity, not a new pass. Push-out only by construction, so it cannot
# produce the opposite defect.
#
# DEFAULT OFF until measured. F010 is the standing caution for this exact area:
# a copy-path repair that looked like a uniform clipping win still could not
# ship, because standoff inflated on 9 of 9 pieces and it stranded zero-weight
# bones. Score clip AND standoff AND `verify_zero_weight_bones.py`, then in
# game. docs/worklog/BUTT_COPY_PATH_TROUSERS.md
#
# DEFAULT ON since 2026-09-02 (`CBBE2UBE_NO_PHASE1_ANTIPOKE=1` restores the
# previous behaviour exactly). Promoted on two populations plus an in-game
# verdict, and specifically on the two axes that killed the last copy-path
# repair (F010: standoff inflated 9 of 9, and it stranded 16 bones):
#
#   a heavy-plate set (86 NIFs, 86 copy / 0 swap, heavy plate)
#     BUST 15 arms  inside 1 better/0 worse   clip 0/0 (already 0.000)
#                   standoff 8 out 0.03-0.23u  coverage 0 dropped
#     BUTT 11 arms  inside 11 better/0 worse  clip 4 better/0 worse
#                   standoff 2 out / 8 IN     coverage 7 dropped (~3pt, torsos)
#     best: one cuirass's pauldrons clip 32.258% -> 0.265%
#   an HDT-SMP cloth set (40 NIFs, 4 physics XMLs, 48 chain-carrying shapes)
#     simulated verts moved 130 of 42994 (0.3%), ZERO fully-chain
#     physics XMLs 4 of 4 byte-identical; rigid verts moved 2093
#   zero-weight bones 0 -> 0 on every arm of both mods
#   in game: the reported trousers, "looks good" (author 0.0% inside, ours was 59.3%)
#
# THE ONE THING NOT EXPLAINED: butt coverage drops ~3 points on the big torso
# pieces (82.7 -> 80.0 and similar). It is NOT uniform -- coverage RISES
# elsewhere (11.3 -> 38.8 on a pauldron) -- but nobody has traced it. If a piece
# ever looks like it has pulled off skin it used to cover, start there.
PHASE1_ANTIPOKE = (
    not _flag("CBBE2UBE_NO_PHASE1_ANTIPOKE", False))

# --- #hdt-xml-sanitise -- OPT-IN, `CBBE2UBE_HDT_XML_SANITISE=1` -------------
# Repair authored physics XMLs that are malformed OUTSIDE the root element.
#
# TEN authored XMLs in this modlist end with junk after the root close --
# `</system>undefined</xml>` (6) or `</system></xml>` (4); a stray close for a
# wrapper the authoring tool never opened. XML forbids non-whitespace after the
# document element, so every strict parser rejects the file outright.
#
# WHAT IT COSTS TODAY. `_read_source_hdt_xml_text` hands that text to every
# collider / soft-body / validation consumer, and the destination copy is a
# VERBATIM `atomic_copy`, so the damage ships. Measured on the shipped pack:
# **94 of 3673 NIFs reference an unparseable XML** and get no physics
# processing at all -- and an empty collider set is the condition BUG-00
# recorded as disarming every guard at once.
#
# The repair drops 8-16 bytes per file and is verified on all ten: the
# per-vertex-shape / per-triangle-shape / bone declarations are IDENTICAL
# before and after, 170 well-formed XMLs are untouched, 0 remain broken. It
# operates on BYTES with no decode -- transcoding one of these is exactly how
# BUG-12 double-encoded a BOM -- and returns the input unchanged unless the
# repaired text parses AND keeps the same root tag.
#
# DEFAULT OFF pending a verdict: it changes what ships for those pieces.
HDT_XML_SANITISE = _flag("CBBE2UBE_HDT_XML_SANITISE", False)

# --- Phase-2 source-standoff conform (#phase2-conform) --------------------
# The SIBLING of the flag above, on the body-swap path, where this pass has
# always run UNCONDITIONALLY. DEFAULT ON: adding the switch changes nothing by
# construction, it only makes the pass reachable for an A/B.
#
# DO NOT CONFUSE IT WITH `CBBE2UBE_NO_CONFORM`, which gates
# `CONFORM_FITTED_CLOTH` -> `_conform_fitted_to_body`, a WEIGHTS pass in the
# shared tail. Two passes, both called "conform"; the older, obvious-looking
# flag is the other one, so an A/B run against it measures nothing and reads as
# "conform does not matter".
#
# Measured 2026-08-23 over 28 pieces per path: on body-swap this pass has the
# LARGEST motion of any stage (0.70u median) and the LOWEST survival (0.38
# pooled, 0.32 per piece), while the copy path -- 78% of the pack, judged good
# in game -- runs no equivalent at all. Whether it earns its place is a real
# question with a natural control, and only an in-game A/B can settle it: a
# bind-pose survival number cannot judge a motion pass.
PHASE2_CONFORM = not _flag("CBBE2UBE_NO_PHASE2_CONFORM", False)

_CBBE_BODY_NORMALS_CACHE: dict = {}


def _cached_cbbe_body_normals(path) -> "np.ndarray | None":
    """Outward vertex normals of the CBBE base body, cached by path.

    `conform_to_source_standoff` measures the authored standoff along the SOURCE
    body's normal, so the sign is meaningless without them. Uses the same
    normals-or-recompute helper as the UBE side, because BodySlide output
    frequently ships zero/absent normals and a silent zero vector would make
    every authored standoff read 0 -- which would look like "the author fitted
    everything coincident" and pull the whole garment onto the skin.
    """
    key = str(path)
    if key in _CBBE_BODY_NORMALS_CACHE:
        return _CBBE_BODY_NORMALS_CACHE[key]
    out = None
    try:
        nf = _pynifly().NifFile(filepath=str(path))
        sh = next((s for s in nf.shapes if _looks_like_inline_body(s)), None)
        if sh is None and nf.shapes:
            sh = max(nf.shapes, key=lambda s: len(s.verts))
        if sh is not None:
            out = _body_normals_or_compute(sh)
    except Exception:
        out = None
    _CBBE_BODY_NORMALS_CACHE[key] = out
    return out

# Extra anti-poke clearance scaled by local jiggle-bone weight, for the SMP bounce that
# swings the body PAST its static envelope. Every other clearance term reasons about the
# body at REST; a rigid cuirass has no idea the breast is about to be thrown outward by
# physics. That leaves a rest-clean armor showing skin in-game -- clearance +1.18u, morph
# tracking at ratio 1.0, and the breast still pokes because it doesn't stay where it was
# measured.
#
# DEFAULT ON since 2026-07-10. It was opt-in and simply never switched on, and an env var
# would not have reached the converter anyway: MO2 doesn't inherit them (the same reason
# UNIFIED_COVERAGE had to become a sentinel file). Measured blast radius on the UBE body --
# the term is `gain * jiggle_weight`, and jiggle weight is ~0 outside the jiggle zones, so
# tight fits stay tight:
#     breast  jiggle 0.28 mean / 0.56 max -> adds +0.14u mean, +0.28u at the nipple
#     belly   0.03 mean                   -> adds +0.02u
#     butt    0.02 mean                   -> adds +0.01u
#     back    0.00 max                    -> adds  0.000u  (exactly zero, not rounded)
# Disable with CBBE2UBE_NO_JIGGLE_CLEARANCE=1. Raise GAIN if a bouncier SMP setup still
# shows skin at the nipple.  [DESIGN: Clearance & anti-poke]
JIGGLE_CLEARANCE_ENABLED = (
    not _flag("CBBE2UBE_NO_JIGGLE_CLEARANCE", False)
)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# (moved to nif_convert_fitgeom.py, 2026-09-01)
# The `CBBE2UBE_NO_REAR_STANDOFF` kill switch used to live HERE as
# `if _flag(...): REAR_STANDOFF = 0.0`. It was DEAD: `REAR_STANDOFF` is imported
# from fitgeom above, and `clear_armor_outside_body` binds it as a parameter
# default at FITGEOM import time, so rebinding this module's name changed
# nothing any pass reads. Applied at the definition in fitgeom instead.
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# (moved to nif_convert_fitgeom.py, 2026-09-01)
# `CBBE2UBE_NO_CALF_STANDOFF` was dead here for the same reason as
# `CBBE2UBE_NO_REAR_STANDOFF` above; applied at the definition in fitgeom.
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# Restrict the thigh standoff to the INNER (medial) face only. The inner thigh is
# where a spread/bent pose punches the body through thin bind clearance; pushing
# the OUTER thigh too would shove it into a hip skirt. Medial = the nearest body
# normal points toward the centerline. CBBE2UBE_THIGH_STANDOFF_MEDIAL=1.
THIGH_STANDOFF_MEDIAL = (_flag("CBBE2UBE_THIGH_STANDOFF_MEDIAL", False))

# Inflate the CUIRASS/torso cloth shapes outward a hair (away from the body), while
# leaving LEG armor (greaves/leggings) untouched -- a targeted way to give the upper
# layers a little more room without disturbing the legs. A leg shape (name contains
# "greave" OR leg-bone-dominated) is skipped. Value in units. CBBE2UBE_CUIRASS_INFLATE.
CUIRASS_INFLATE = _knob("CBBE2UBE_CUIRASS_INFLATE", 0.0)

# Anti-poke push-field SMOOTHING (default OFF): the final anti-poke pushes each
# vert independently along its nearest body normal, so adjacent verts get
# different magnitudes -> faceted/crinkled cloth exactly where clearance was
# applied. Feather the push scalar over the armor mesh adjacency instead. The
# smoothed field is FLOORED at the original per-vert requirement, so smoothing
# can never re-open a poke it was called to close.
#
# OFF because in game it raised the inner layer of a multi-layer garment toward
# an unpushed outer one and collapsed the gap between them; re-enable once the
# smoothing is gap-aware. `CBBE2UBE_ANTIPOKE_SMOOTH=1` turns it on.
#
# (This block previously opened "default ON" and closed "Default off" in two
# spliced halves, the first cut off mid-sentence. The code has always been OFF.)
# [DESIGN: Push-field smoothing]
ANTIPOKE_SMOOTH_ENABLED = (
    _flag("CBBE2UBE_ANTIPOKE_SMOOTH", False)
)
# (moved to nif_convert_fitgeom.py, 2026-09-01)

# Warp Pass-2 min-standoff SMOOTHING (default ON). Pass 2 pushes every vert whose
# signed distance falls below `min_standoff` up to exactly the floor, on a HARD
# threshold: a vert at floor-0.001 gets ~0 push and its neighbour at floor+0.001
# gets none, so the boundary of the pushed region is a cliff. Measured: this makes
# 94% of a cord shape's sharp edges (n_sharp 1583 -> 99 with min_standoff=0),
# making it the largest single spike source left in the chain. Feather the push
# scalar over mesh adjacency, FLOORED at the original requirement so no vert ever
# ends up below the standoff (the clearance guarantee is preserved exactly).
# Needs `tris`; call sites that pass none silently keep the old hard behaviour.
# Lower collapse risk than ANTIPOKE_SMOOTH: this push is bounded by the 0.15u
# buffer, not by a full clearance ramp. Kill with CBBE2UBE_NO_WARP_SMOOTH=1.
WARP_STANDOFF_SMOOTH_ENABLED = (
    not _flag("CBBE2UBE_NO_WARP_SMOOTH", False)
)
WARP_STANDOFF_SMOOTH_ITERS = int(
    os.environ.get("CBBE2UBE_WARP_SMOOTH_ITERS", "2") or 2)

# #warp-delta-smooth -- Laplacian passes over the BODY-DELTA field itself,
# before it is applied. Distinct from the two smoothings above it: those act on
# the standoff PUSH, this acts on the displacement the body asked for. Measured
# on one garment, `warp` produces 72-87% of the waist distortion and its field
# jumps 15-23% of an edge length between neighbours, which is the stretch.
# DEFAULT 0 (off): it trades that distortion against the clearance this stage
# hands downstream, and the net on the shipped mesh has to be measured end to
# end before any default moves. See the block at the call site.
WARP_DELTA_SMOOTH_ITERS = int(
    os.environ.get("CBBE2UBE_WARP_DELTA_SMOOTH", "0") or 0)
WARP_DELTA_SMOOTH_WEIGHT = float(
    os.environ.get("CBBE2UBE_WARP_DELTA_SMOOTH_W", "0.5") or 0.5)

# Source-body normals for the phase-2 conform pass. DEFAULT OFF -- opt in with
# CBBE2UBE_SRC_NORMAL_FIX=1.
#
# The DEFECT is real and confirmed: the producer gated source normals on LENGTH
# only, so an all-zero normal array (BodySlide output ships them routinely --
# measured 18 of 21 sampled inline bodies) reached conform_to_source_standoff.
# Zeroed normals make every source standoff read 0, which tells that pass the
# cloth was skin-tight everywhere, so it reels LOOSE drape inward instead of
# leaving it alone -- the opposite of its contract.
#
# It was long marked "OFF anyway because fixing it is NOT MEASURABLY BETTER",
# on this A/B: 19.6% of verts move, mean 0.024u, max 3.01u, and mean |standoff
# error vs the source drape| 4.211u -> 4.225u (+0.3%), 15 shapes closer and 23
# worse -- with the reason given as "the later passes largely overwrite what
# conform did, so correcting its input mostly reshuffles rather than improves".
#
# THAT CONCLUSION DID NOT SURVIVE RE-MEASUREMENT (2026-08-12). Two things were
# wrong with it, and both are worth keeping because they are easy to repeat:
#
#   * The metric was a MEAN over |standoff error|, which on a garment is
#     dominated by the verts furthest from the body -- exactly the ones conform
#     is gated away from. Per-pass, area-weighted, it is not close: across the
#     conform boundary the authored-offset error goes 0.227 -> 0.319 (WORSE)
#     with the normals zeroed and 0.227 -> 0.200 (BETTER) with them fixed on
#     one shape, 0.666 -> 0.817 vs 0.666 -> 0.552 on another. Same pass, same
#     piece, opposite verdicts.
#   * "The later passes overwrite it anyway" is an argument for fixing the
#     CHAIN, not for leaving the target broken. Shipped numbers on the reported
#     piece with it on: layers on the wrong side 1074 -> 706, rough edges
#     4205 -> 3910, dihedral mean 10.56 -> 10.33, edge deviation 0.0814 ->
#     0.0810.
#
# It also explains `#unified-offset`: that solver was asked for
# clip(target, floor, ceiling) with target identically ZERO.
#
# WHAT IT COSTS THE REST OF THE CHAIN, measured 2026-08-23 with the damage/repair
# ledger over 34 body-swap shapes. With the target zeroed, `conform` is a net
# damage CREATOR; with the normals fixed it is roughly neutral, which is what a
# pass with a correct target should look like:
#
#     s04_conform            default   SRC_NORMAL_FIX=1
#       creates penetration      476        46   (-90%)
#       creates stretch         1831       540   (-71%)
#
# and the passes downstream do less work for it -- `panel_rigidity` creates
# 4721 -> 4055, `antipoke` repairs 4902 -> 3894. So the broken target is not only
# wrong for conform, it is manufacturing the load the repair passes carry.
#
# READ THE SHIPPED NUMBER WITH WEIGHTING. Pooled, the final mesh looks worse
# (1031 -> 1327 stretched edges) -- but that total is carried by ONE garment
# counted twice (its `_0` and `_1` weight variants, +315 between them) while
# everything else nets negative. Per shape it is 6 better / 6 worse / 22
# unchanged, and the median stretched-edge RATE goes 0.005% -> 0.000%.
#
# STILL DEFAULT OFF, but now for a different and smaller reason: every fit
# constant here was tuned over dozens of in-game cycles WITH the zeroed normals
# in play, so it shifts ~20% of verts modlist-wide. It is deployed for an
# in-game verdict; when that lands, retune the conform constants with it on and
# flip the default.
_SRC_NORMAL_FIX = (
    _flag("CBBE2UBE_SRC_NORMAL_FIX", False)
)

# Extra per-layer anti-poke floor so stacked garments don't converge to the same
# standoff and z-fight. Default off (same finding as smoothing);
# CBBE2UBE_LAYERED_ANTIPOKE=1 on.  [DESIGN: Layered anti-poke floors]
LAYERED_ANTIPOKE_ENABLED = (
    _flag("CBBE2UBE_LAYERED_ANTIPOKE", False)
)
LAYERED_ANTIPOKE_EPSILON = 0.15   # per-layer extra floor (units)
LAYERED_ANTIPOKE_MAX_EXTRA = 0.45  # cap (3+ layers share the top separation)
# (moved to nif_convert_trigen.py, 2026-09-01)


# #clearance-differential: adaptive clearance allocates room by the WRONG
# quantity, and the back is where that shows.
#
# `clear_armor_outside_body` ignores ANTIPOKE_FLAT_CLEAR whenever an amplitude map
# exists and uses `BASE + FACTOR * amp`, where amp is a vertex's own outward
# GROWTH. Clipping is not caused by growth. It is caused by the DIFFERENTIAL --
# how much a neighbour outgrows the point covering it -- because a garment vert
# carries the delta of the body point it hugs. Uniform inflation has large amp
# and zero differential and a hugging garment follows it for free; a reshaping
# slider has small amp and large differential, and that is what pokes.
#
# Measured on the UBE body (29298 verts), amp vs differential need:
#     zone            amp p50   adaptive grants   differential asks (p90)
#     back band          0.02             0.26u                    0.385u  SHORT
#     rear 80-95         0.23             0.31u                    0.354u  SHORT
#     breast             3.69             1.05u                    0.569u  covers
#     belly              1.41             0.62u                    0.502u  covers
# Pearson r between amp and need: +0.088 on the back, -0.413 on the breast. The
# proxy is uncorrelated where it matters and NEGATIVELY correlated on the front;
# the front is protected by accident, because amp happens to be enormous there.
#
# Applied as a MONOTONE FLOOR (np.maximum, never stacks, never lowers), so the
# breast keeps the 1.05u the amp ramp already grants and only under-served zones
# move. That also makes "newly starved" zero by construction, which is the shape
# this project's measurement rules ask for.
# DEFAULT ON as of 2026-08-08. Its largest effect is NOT the back: the upper
# chest (z 99-112) sits above the amp-rich breast, so the amp ramp under-serves it
# too, and the differential recovers 164 clipping verts there. Golden six x 4
# presets, with #back-morph-residual also on: 96 pairs -> 5 worse, 49 better, 42
# unchanged, worst single regression +3 verts, travel unchanged at 4.03u.
# NO_-style var, matching the GUI's convention for a default-ON flag:
# CBBE2UBE_NO_CLEARANCE_DIFFERENTIAL=1 disables.
#
# REACH: this rides `clear_armor_outside_body`, so it cannot touch a shape that
# pass skips. Measured on the golden set it fires on 5 of 6 pieces --
# chainweld-studded moved ZERO verts, because the anti-poke is skipped for
# SMP/chain shapes. The conform-side back charge still reaches those.
CLEARANCE_DIFFERENTIAL = not _flag("CBBE2UBE_NO_CLEARANCE_DIFFERENTIAL", False)
# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# --- Portable body discovery (no hardcoded modpack paths) -------------
# Body / ShapeData / preset discovery and the mod-tree lookups live in
# nif_convert_bodyrefs.py since 2026-09-01 (split step 3). Imported BY NAME
# so `nc.<name>` keeps working; the caches are the same objects (mutated in
# place, never rebound).
from . import paths as _paths  # noqa: E402
from .nif_convert_bodyrefs import (  # noqa: E402
    _CBBE_3BA_VERTS, _CBBE_BODY_NAME_HINTS, _UBE_BODY_NAME_HINTS,
    _BODYSLIDE_OUT_HINTS, _FEMBODY_REL, _BODY_DISCOVERY_CACHE,
    _MOD_DIR_LIST_CACHE, _GLOB_FIRST_MEMO,
    _iter_femalebody_nifs, _shape_has_3ba_topology, _find_cbbe_base_body,
    weight_suffix_of,
    _find_ube_femalebody, _sorted_mod_dirs, _glob_first_in_mods,
    _find_ube_shapedata, _find_ube_template_body, _find_ube_body_osd,
    _find_user_preset_body,
)



# ----- Shape skin-frame reconciliation --------------------------------------
# `_g2s_is_identity`, `_shape_global_to_skin`, `_verts_skin_to_world` and
# `_verts_world_to_skin` live in nif_convert_skinframe.py since 2026-09-01
# (split step 2). Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_skinframe import (  # noqa: E402
    _g2s_is_identity, _shape_global_to_skin, _verts_skin_to_world,
    _verts_world_to_skin,
)



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


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (split step 10) _copy_shape, _install_skin, _reauthor_nif_fresh, validate_dst_nif ... live in nif_convert_writer.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_writer import (  # noqa: E402
    _copy_shape, _install_skin, _reauthor_nif_fresh, validate_dst_nif,
    _normalize_partitions_on_disk, _restore_authored_shape_order,
    _repair_coherence_collapse, _repair_degenerate_normals,
    _repair_effect_shader_shape_controllers, _repair_winding_consistency,
    _transplant_effect_controller, _shape_has_vertex_colors,
    _close_pubic_holes, _drop_scale_bones_from_skin, _geometry_repair_allowed,
    _recompute_vertex_normals, _sanitize_one_nif_worker, classify_shapes,
    detect_zfight_pairs, fix_vertex_color_shader_flags,
    sanitize_output_vertex_color_flags,
)



# --- The warp field's SMOOTHNESS (#warp-field-staircase) ------------------
# The CBBE->UBE field is built by snapping each CBBE vertex to the nearest UBE
# VERTEX. That quantises a continuous deformation onto discrete targets, so two
# adjacent CBBE verts can land on UBE verts that are not adjacent: a staircase,
# not a deformation. Measured over body edges as |d(a)-d(b)| / |a-b|, which a
# smooth field keeps well under 1:
#
#     nearest VERTEX     p50 0.48   p90 1.07   11.51% of edges over 1.0
#     closest POINT      p50 0.07   p90 0.29    0.21%
#
# and the tell is that BOTH endpoint bodies are smooth (mean dihedral 3.7 and
# 4.3 degrees) while `cbbe + delta` reads 43.2. Closest-point brings that to
# 4.6 -- back to the source body's own smoothness. Worst regions are hip/butt
# (16.8% of edges) and bust (16.4%), which is where this project's recurring
# defects live.
#
# TEMPER THE EXPECTATION: the garment does not see the field per-vertex, it
# samples it with a k=4 IDW, which blurs most of the staircase away. Measured
# THROUGH the pass, area-weighted p50 rotation: chest_plate 3.87 -> 1.93, top
# 6.98 -> 6.51, belts_metal 10.93 -> 10.08, and belts 10.03 -> 10.03 -- no
# change at all on the shape the investigation started from. This is a
# smoothness fix, NOT the fix for surface rotation; see docs/PIPELINE.md §7.
# Counter-metric on the same run: standoff p05/p50 unchanged to three decimals,
# verts inside the body 0.06% -> 0.00%, verts move a median 0.07-0.16u.
SURFACE_WARP_FIELD = _flag("CBBE2UBE_SURFACE_WARP_FIELD", False)
SURFACE_FIELD_CANDIDATES = 24    # triangles tested per point


def _closest_point_delta(points, tv, tt, *, fallback=None,
                         k: int = SURFACE_FIELD_CANDIDATES):
    """Vector from each point to the nearest point on the triangle mesh.

    Candidates come from a KD-tree over triangle CENTROIDS, which is an
    approximation: a long thin triangle can be near the point while its
    centroid is not. So the nearest-VERTEX result is passed in as `fallback`
    and taken wherever it is closer. That is not a safety net bolted on, it is
    exact -- a vertex IS a point on the surface, so the elementwise minimum of
    the two can only be closer to the true answer than either. It also makes
    `k` a speed knob rather than a correctness one.
    """
    from scipy.spatial import cKDTree
    pts = np.asarray(points, dtype=np.float64)
    tv = np.asarray(tv, dtype=np.float64)
    tt = np.asarray(tt, dtype=np.int64).reshape(-1, 3)
    A0, B0, C0 = tv[tt[:, 0]], tv[tt[:, 1]], tv[tt[:, 2]]
    k = int(min(max(k, 1), len(tt)))
    _, cand = cKDTree((A0 + B0 + C0) / 3.0).query(pts, k=k, workers=-1)
    cand = np.atleast_2d(cand.reshape(len(pts), -1))
    best = np.full(len(pts), np.inf)
    out = np.zeros_like(pts)
    if fallback is not None:
        out = np.asarray(fallback, dtype=np.float64).copy()
        best = np.linalg.norm(out, axis=1)
    for j in range(cand.shape[1]):
        ti = cand[:, j]
        A, ab, ac = A0[ti], B0[ti] - A0[ti], C0[ti] - A0[ti]
        ap = pts - A
        # Project onto the triangle's plane in barycentrics, then clamp into
        # the triangle. The clamp alone is not the true closest point outside
        # the face region, so every edge is tested below and the nearest wins;
        # vertices need no separate case, being edge endpoints.
        d00 = np.einsum('ij,ij->i', ab, ab)
        d01 = np.einsum('ij,ij->i', ab, ac)
        d11 = np.einsum('ij,ij->i', ac, ac)
        den = np.maximum(d00 * d11 - d01 * d01, 1e-20)
        u = np.clip((d11 * np.einsum('ij,ij->i', ab, ap)
                     - d01 * np.einsum('ij,ij->i', ac, ap)) / den, 0.0, 1.0)
        v = np.clip((d00 * np.einsum('ij,ij->i', ac, ap)
                     - d01 * np.einsum('ij,ij->i', ab, ap)) / den, 0.0, 1.0)
        s = np.maximum(u + v, 1.0)
        p = A + (u / s)[:, None] * ab + (v / s)[:, None] * ac
        dp = np.linalg.norm(pts - p, axis=1)
        for P, Q in ((A0[ti], B0[ti]), (B0[ti], C0[ti]), (A0[ti], C0[ti])):
            e = Q - P
            t = np.clip(np.einsum('ij,ij->i', pts - P, e)
                        / np.maximum(np.einsum('ij,ij->i', e, e), 1e-20),
                        0.0, 1.0)
            q = P + t[:, None] * e
            dq = np.linalg.norm(pts - q, axis=1)
            closer = dq < dp
            p = np.where(closer[:, None], q, p)
            dp = np.where(closer, dq, dp)
        take = dp < best
        best = np.where(take, dp, best)
        out = np.where(take[:, None], p - pts, out)
    return out


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
        _pynifly()   # ensure pyn is importable for the nif_io calls below
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
            if SURFACE_WARP_FIELD:
                # #warp-field-staircase. See the constant for the measurement.
                try:
                    ube_t = np.asarray(ube_shape.tris,
                                       dtype=np.int64).reshape(-1, 3)
                    delta = _closest_point_delta(cbbe_v, ube_v, ube_t,
                                                 fallback=delta)
                except Exception as e:
                    # A field this one silently failed to build is a field the
                    # whole pack is warped by. Say so; keep the nearest-vertex
                    # result, which is the shipped behaviour.
                    print(f"  [warp-field] closest-point field failed, "
                          f"keeping nearest-vertex: {e!r}")
        _CBBE_UBE_DELTA_CACHE[key] = (cbbe_v, delta)
        return cbbe_v, delta
    except Exception:
        _CBBE_UBE_DELTA_CACHE[key] = (None, None)
        return None, None


GROOVE_SMOOTH_CLOSE = 6.0   # only smooth verts within this of the UBE body (tight armor)
GROOVE_SMOOTH_ITERS = 8
# Kill switch, for ABLATION. This pass is a CLEANUP: it exists because the warp
# and the conform leave grooves. Measured on a five-layer top, area-weighted
# mean dihedral across the conform boundary: 7.09 -> 13.52 with the conform
# reading a ZEROED authored standoff, and 7.09 -> 11.50 once it reads the real
# one. So how much cleanup is still needed is a function of how well the passes
# upstream behave, and that has to be re-measurable in one command rather than
# assumed from when it was written.
GROOVE_SMOOTH_ENABLED = not _flag("CBBE2UBE_NO_GROOVE_SMOOTH", False)
GROOVE_SMOOTH_ROUGH = 0.25  # displacement-deviation (u) above which a vert is "grooved"


GROOVE_ONESIDED = os.environ.get("CBBE2UBE_GROOVE_ONESIDED", "1") != "0"
# REMOVED 2026-07-31: #groove-nipple-hold held this smoothing back over a body
# protrusion, and it earned that while it was the only thing protecting the tip.
# Once #bust-surface-req landed it became actively harmful. Ablation on the piece
# it was written for, across all 112 installed BodySlide presets:
#     hold ON    1 preset still poking, nipple clearance +0.220u
#     hold OFF   0 presets poking,      nipple clearance +0.528u
# with identical torso fit (0.520u) and IDENTICAL crinkle on every shape.
# Deleting it cleared the last holdout. Do not re-add without redoing that
# ablation -- a pass that was load-bearing can stop being so.


# --- Authored-standoff cap on the groove smooth (#groove-authored-cap) ----
# GROOVE_ONESIDED made this pass outward-only, and it runs AFTER
# `conform_to_source_standoff`, so the only thing it could do to a vert the
# conform had just reeled in was hand the clearance back: traced on a mail
# layer, the conform reached 0.235u on the chest and this pass pushed it back to
# 0.322u. Measured against the other two limiters on the same piece, that
# giveback (+0.085u) is LARGER than the conform's min-clearance floor (0.024u)
# and blend fraction (0.033u) combined, so it is the biggest single term left.
#
# Reordering was tried and rejected (see IN_GAME_BUGS 4c/4d): output smoothness
# comes from this pass smoothing the CUMULATIVE field last, so nothing may be
# moved after it. The cap keeps it last and bounds only its OUTWARD motion:
#     s_out <= max(s_in, s_authored)
# A vert already looser than the author's fit gets cap = where it already is, so
# it cannot be pushed out at all -- the giveback dies. A vert TIGHTER than the
# author's fit is a genuine groove and may still be raised, up to the authored
# standoff. Inward motion is still cancelled by GROOVE_ONESIDED, so the
# regression that flag exists to stop (flattening the bust apex onto the skin,
# 13 of 42 shapes) is untouched: this only ever restricts, never adds, motion.
# Needs the SOURCE body to know the authored standoff; without it the pass
# behaves exactly as before. Set CBBE2UBE_NO_GROOVE_CAP=1 to disable.
GROOVE_AUTHORED_CAP = not _flag("CBBE2UBE_NO_GROOVE_CAP", False)
GROOVE_CAP_FEATHER = 2      # rounds of feathering on the cap's subtraction


# (moved to nif_convert_fitgeom.py, 2026-09-01)


def _ring_average(values, tris, n):
    """Mean of each vertex's 1-ring (neighbours + itself). Shared by the guards."""
    from scipy import sparse
    t = np.asarray(tris, dtype=np.int64)
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    rows = np.concatenate([e[:, 0], e[:, 1]])
    cols = np.concatenate([e[:, 1], e[:, 0]])
    adj = sparse.coo_matrix((np.ones(len(rows)), (rows, cols)),
                            shape=(n, n)).tocsr()
    adj.data[:] = 1.0
    deg = np.asarray(adj.sum(axis=1)).ravel()
    out = np.array(values, dtype=np.float64, copy=True)
    has = deg > 0
    out[has] = ((adj @ values)[has] + values[has]) / (deg[has] + 1.0)[:, None]
    return out


# (moved to nif_convert_fitgeom.py, 2026-09-01)


def _welded_components(sv, tris):
    """Connected components of `tris`, with positionally coincident verts WELDED.

    WHY WELD: a topological component is NOT an object. Authors split vertices at
    hard edges for shading, so one continuous surface arrives as many topological
    pieces whose verts are positionally coincident across the split. Censused on
    the pack, a stocking reads as 865 components on 3459 verts and a mantle's body
    mesh as 2000 on 8776 -- in both, 100% of those verts coincide with a vertex of
    a DIFFERENT component and the median piece is 4 verts. A real fitting measures
    nothing like that (a skull ornament on the same piece: 145 verts, 29% welded).

    Single definition on purpose, so nothing that reasons about "a part" can drift
    from anything else that does -- the duplicate-predicate failure this project
    has already paid for (#conform-skip-two-detectors).

    Returns (labels, n_components) or (None, 0) if it cannot be computed.
    """
    try:
        from scipy import sparse
        from scipy.sparse import csgraph
        t = np.asarray(tris, dtype=np.int64)
        n = len(sv)
        e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        if SMALL_ELEMENT_WELD > 0.0:
            try:
                from scipy.spatial import cKDTree
                _wp = cKDTree(sv).query_pairs(
                    r=SMALL_ELEMENT_WELD, output_type="ndarray")
                if len(_wp):
                    e = np.vstack([e, np.asarray(_wp, dtype=np.int64)])
            except Exception:
                pass
        g = sparse.coo_matrix(
            (np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n))
        ncomp, lab = csgraph.connected_components(g, directed=False)
        return (lab, ncomp) if ncomp > 0 else (None, 0)
    except Exception:
        return None, 0


def _small_element_mask(sv, tris):
    """Verts belonging to a welded component small enough to be a FITTING.

    Below this size a part is a stud, buckle, clasp or rivet; above it, a strap or
    a panel. `_uniformise_local_scale` uses it to leave fittings alone: that pass
    smooths a local scale field, and a cluster of separate small objects has a
    scale that legitimately VARIES between them, so smoothing pushes each toward a
    consensus belonging to its neighbours. Measured on the reported piece, letting
    it reach them took a buckle's edge deviation from 0.364 to 0.454 and a chest
    plate's from 0.060 to 0.090.
    """
    lab, ncomp = _welded_components(sv, tris)
    if lab is None:
        return None
    mask = np.zeros(len(sv), dtype=bool)
    for ci in range(ncomp):
        idx = np.flatnonzero(lab == ci)
        if len(idx) < 4:
            continue
        ext = sv[idx].max(axis=0) - sv[idx].min(axis=0)
        if float(np.linalg.norm(ext)) <= SMALL_ELEMENT_MAX_DIAG:
            mask[idx] = True
    return mask


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


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


# (split step 6) _body_nipple_weight, _breast_chain_index, _breast_chain_levers, _bust_split_candidates ... live in nif_convert_bust.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_bust import (  # noqa: E402
    _body_nipple_weight, _breast_chain_index, _breast_chain_levers,
    _bust_split_candidates, _bust_split_xml_text, _chest_follow_target,
    _split_bust_collider_shape, _split_bust_collider_xml,
    _sync_bust_plate_follow_postwrite,
)



# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# --- #rebury-authored: a vert the AUTHOR hid INSIDE the body stays inside ---
# Authors routinely sink whole REGIONS of a garment below the skin -- the part of
# a leather top that runs under the bust, the back of a bodystock, the buried
# half of a thong -- because geometry under the skin cannot z-fight and costs
# nothing to draw. Every clearance pass in this file enforces a POSITIVE floor
# (`min_clearance` 0.25, `ARMOR_TO_SKIN_BUFFER` 0.15, the anti-poke targets), and
# `#authored-inflate` clamps the authored standoff at zero on purpose --
# "Negative means they tucked it under the surface; a floor must not honour
# that". So every one of those verts is dragged out into view.
#
# MEASURED on a layered robe (`body_bury_census.py`):
#
#   shape         author-buried   still buried   SURFACED   median depth
#   TopLeather             227             0     227 (100%)      0.313u
#   Thong                 2588            38    2550  (99%)      0.323u
#   Feathers               172            96      76  (44%)      0.196u
#   whole piece           3066           133    2933  (96%)
#
# 96% of the author's hidden geometry is pushed into view. On the chest that is
# 227 verts of DARK LEATHER lying just outside the skin across the bust -- the
# user's report is "the skin goes extremely dark below the neck ... that is the
# mesh that should most likely be inside the body", with a screenshot.
#
# This is the user's own criterion ("inside on CBBE should be inside on UBE")
# applied to the pair every other pass ignores: garment vs THE BODY. It is also
# why the standoff work could not fix it -- pulling a layer from 0.79u to 0.56u
# still leaves it OUTSIDE, and outside by any amount renders.
#
# SAFE BY CONSTRUCTION: it only ever moves a vert the author had inside the body,
# only ever INWARD, and never deeper than the author put it. A vert that ends up
# inside the skin is invisible, so this cannot create a visible artifact of its
# own; the risk it does carry is stretching the triangles that bridge a buried
# vert and an exposed neighbour, which is why the move field is feathered and
# capped. DEFAULT OFF -- and no longer "pending": the verdict came back and it
# was BAD. Enabled, this destroyed a reported cuirass's shoulders and arms in
# game (a known clamp bug in this pass). Do not flip it without fixing that
# first; the flag is kept so the measurement reproduces, not as a candidate.
REBURY_AUTHORED = _flag("CBBE2UBE_REBURY_AUTHORED", False)
REBURY_MAX_MOVE = _knob("CBBE2UBE_REBURY_MAX_MOVE", 1.5)
# INSIDE is not the only hidden state, and on the reported piece it is not even
# the relevant one. MEASURED on that BodyStock -- a bodysuit -- against the CBBE
# body it was authored on: min +0.001, p25 +0.089, median +0.129, and ZERO of
# its 4309 verts negative. It is never inside; it is FLUSH, hugging the skin
# within a tenth of a unit, which is why it reads as the body's own surface in
# the source. Ship the same verts at 0.56-0.79u and they become a separate dark
# shell standing off the skin -- the reported "the skin goes extremely dark
# below the neck". So the rule has to be "as close as the author had it" for
# every vert the author kept AT the surface, of which "inside" is one case.
#
# The threshold is the project's own armor-to-skin buffer: at or below it, a
# vert was never meant to read as its own surface. Above it the author is
# genuinely standing the garment off, and that vert keeps its morph clearance --
# which is what stops this from becoming "throw away all headroom everywhere".
# Set to 0.0 for exactly "inside on CBBE stays inside on UBE" and nothing more.
REBURY_FLUSH_MAX = _knob("CBBE2UBE_REBURY_FLUSH_MAX", ARMOR_TO_SKIN_BUFFER)
# Breast-bone share at which the restore is fully suppressed. Below it the
# restore ramps linearly, so there is no step at the edge of the bust for the
# feathering to turn into a crease. 0.35 rather than 1.0 because the SIDE of the
# breast -- where it punched through -- carries a partial share, not a full one;
# a threshold near 1.0 would protect only the apex and leave the flank exposed,
# which is the same mistake `_body_nipple_weight` makes.
REBURY_MOTION_FULL = _knob("CBBE2UBE_REBURY_MOTION_FULL", 0.35)


def rebury_authored_verts(
    src_cloth, src_body_verts, src_body_normals,
    cur_cloth, ube_body_verts, ube_body_normals,
    tris=None, max_move: float = REBURY_MAX_MOVE,
    body_motion_weight=None,
):
    """Put back inside the UBE body every vert the author had inside theirs.

    `body_motion_weight`: per-BODY-vertex share driven by breast bones
    (`_body_breast_motion_weight`). Where the body TRAVELS, the standoff is not
    slack to be reclaimed -- it is the room the bust swings through, and taking
    it back punches the breast out through the side of the garment on the first
    step (reported in game). The restore is scaled by (1 - w) so the static
    torso is re-hugged fully and the moving bust keeps its clearance. Passing
    None restores everywhere, which is the behaviour that produced that report.

    Returns (verts, n_moved). No-op (input returned) on any mismatch, so a
    caller can wire it unconditionally. See #rebury-authored above.
    """
    from scipy.spatial import cKDTree
    cur = np.asarray(cur_cloth, dtype=np.float64)
    sa = np.asarray(src_cloth, dtype=np.float64)
    if sa.shape != cur.shape or len(cur) == 0:
        return cur_cloth, 0
    sb = np.asarray(src_body_verts, dtype=np.float64)
    sn = np.asarray(src_body_normals, dtype=np.float64)
    ub = np.asarray(ube_body_verts, dtype=np.float64)
    un = np.asarray(ube_body_normals, dtype=np.float64)
    if sb.shape != sn.shape or ub.shape != un.shape or not len(sb) or not len(ub):
        return cur_cloth, 0
    if not np.any(sn):
        # All-zero source normals are the #conform-target-was-zero trap: every
        # authored clearance would read 0.000 and NOTHING would look buried.
        return cur_cloth, 0

    _, si = _authored_src_tree(sb).query(sa, k=1, workers=-1)
    authored = np.einsum('ij,ij->i', sa - sb[si], sn[si])
    # HIDDEN = inside the author's body, or flush against it (see the constant).
    buried = authored <= REBURY_FLUSH_MAX
    if not np.any(buried):
        return cur_cloth, 0

    # Same body array for every shape in the NIF, so share the tree rather than
    # paying ~4ms per shape to rebuild it over 29k verts (17 shapes x 1658
    # pieces is not a rounding error). Cache is id-keyed and re-verifies
    # identity, and it KEEPS the array alive -- see `_authored_src_tree`.
    utree = _authored_src_tree(ub)
    _, ui = utree.query(cur, k=1, workers=-1)
    nrm = un[ui]
    curc = np.einsum('ij,ij->i', cur - ub[ui], nrm)

    # Only verts the author buried, and only where we have them further out
    # than the author did. Target is the AUTHORED clearance -- not "just under
    # the skin", so a vert buried 0.6u deep goes back to 0.6u deep and the
    # surface it belongs to keeps its shape.
    move = np.zeros(len(cur))
    act = buried & (curc > authored)
    move[act] = np.clip(authored[act] - curc[act], -abs(max_move), 0.0)
    # MOTION GATE: give back none of the standoff where the body swings.
    if body_motion_weight is not None:
        w = np.asarray(body_motion_weight, dtype=np.float64)
        if len(w) == len(ub):
            keep = np.clip(w[ui] / max(REBURY_MOTION_FULL, 1e-6), 0.0, 1.0)
            move *= (1.0 - keep)
    if not np.any(move < -1e-6):
        return cur_cloth, 0

    vec = nrm * move[:, None]
    if tris is not None:
        try:
            vec = _smooth_vertex_field(vec, tris, iters=2, verts=cur_cloth)
        except Exception as _pe:
            _note_pass_failure("rebury/smooth", _pe)
    out = cur + vec
    # Re-clamp AFTER feathering: smoothing hands part of the move to neighbours
    # that were never buried, and no vert may end up deeper than the author put
    # it (that is what would tear the seam between buried and exposed regions).
    _, ui2 = utree.query(out, k=1, workers=-1)
    newc = np.einsum('ij,ij->i', out - ub[ui2], un[ui2])
    over = newc < np.minimum(authored, curc) - 1e-6
    if np.any(over):
        fix = (np.minimum(authored, curc) - newc)[over]
        out[over] += un[ui2][over] * fix[:, None]
    return out.astype(np.float32), int((move < -1e-6).sum())


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


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


# #clearance-field. THE alternative the refuted push-divergence smoothing named
# as the only way forward: "a displacement solved for
# minimum stretch subject to clearance -- not post-process the push."
#
# Every clearance pass today states its guarantee PER VERTEX ALONG ITS OWN
# NORMAL: `u_i = n_i * max(req_i - offset_i, 0)`. That normal field fans at a
# concave crease (the waist, under a belt), so neighbours are pushed in
# different directions and the surface stretches or folds. Smoothing the push
# (scalar OR vector) is refuted because the honest-keeping RE-PROJECTION re-adds
# displacement along exactly the diverging normals it just removed.
#
# So do not push-then-repair. Solve ONE field u that meets every requirement
# with the least stretch:
#
#     minimise  sum_edges ||u_i - u_j||^2  +  lambda * sum_i ||u_i||^2
#     s.t.      n_i . u_i  >=  need_i        (clear the body, a LOWER BOUND)
#
# The floor is an INEQUALITY, not a target: a vertex may sit further out than
# need_i when that costs less stretch, so over a concave crease the field lifts
# off the valley and BRIDGES it instead of diving in along the fanning normals.
# That is what push-smoothing cannot do -- it re-projects every vertex back
# ONTO need_i (an equality) as its terminal step, forbidding the lift-off -- and
# what the scalar feather cannot do -- it never touches direction. Solved by
# projected Jacobi to a fixed point (the constrained-QP optimum), which is a
# strict stretch improvement over today's push because that push is itself a
# feasible point of the same solve. `lambda` gives the reach a finite length so
# a far drape is not dragged by the harmonic tail.
#
# DEFAULT ON (2026-08-13). Pack-validated over 17 mods / 1226 shape-pairs: folds
# -14.5%, verts inside body -6.4%, grazing -4.6%, roughness and stretch slightly
# better, standoff +0.013u; the only apparent regressions (high-neck/collar
# shapes) were a concave-neck proxy artifact that the ray-cone truth check
# cleared (ON has FEWER real penetrations). Verified in-game on that plated top
# (whole set). `CBBE2UBE_CLEARANCE_FIELD=0` turns it back off (the census OFF arm).
# This is NOT the rejected `#unified-offset`, which reformulated the operator
# ALGEBRA but still applied its scalar result along each vertex's own diverging
# normal.
CLEARANCE_FIELD_SOLVE = _flag("CBBE2UBE_CLEARANCE_FIELD", True)
# The SAME solve on the earlier `inflate_armor_outward` pass -- the bigger fold
# source (stage ledger on a plated bust top: inflate creates +592 folds vs the
# anti-poke's +360). Independent flag so inflate and anti-poke can be A/B'd
# apart; both share LAMBDA / ITERS / DEBUG below. DEFAULT ON (see above);
# `CBBE2UBE_CLEARANCE_FIELD_INFLATE=0` turns it off.
CLEARANCE_FIELD_INFLATE = _flag("CBBE2UBE_CLEARANCE_FIELD_INFLATE", True)
# Locality/mass term. 0.0 = pure harmonic (infinite reach); larger = shorter
# reach, tighter hug. Numeric tuning knob, so env-only per the flag rule.
CLEARANCE_FIELD_LAMBDA = _knob("CBBE2UBE_CLEARANCE_FIELD_LAMBDA", 0.5)
CLEARANCE_FIELD_ITERS = _knob("CBBE2UBE_CLEARANCE_FIELD_ITERS", 256, int)


# #smooth-reach -- OPT-IN, `CBBE2UBE_SMOOTH_REACH=1`.
#
# Both feathering helpers below spread a displacement by EDGE-NEIGHBOUR
# averaging, so `iters` is a RING count and the distance actually covered is
# `iters * median_edge`. Authored tessellation varies ~20x WITHIN ONE PIECE, so
# the same call reaches 2.4u on a leather panel and 0.11u on a buckle strip --
# the pass that exists to stop a displacement becoming a spike barely reaches at
# all on precisely the shapes that spike.
#
# MEASURED on the piece reported in game ("the belt verts are still jagged"),
# displacement in units of each shape's OWN authored edge:
#
#     shape           authored edge   move p99 / edge   >2x edges   NEW folds
#     5BraBuckles           0.056          35.4            119           9
#     3ButtonsWaist         0.136          21.0            491          97
#     4Panties              0.322          11.0            805         615
#     3Fabric               1.028           1.7             30          33
#     3LeatherMain          1.207           1.2              0           2
#
# Everything above ~6x is damaged, everything below ~2.6x is clean.
#
# The lever is REACH, not magnitude. Scaling the displacement (relative
# reweighting) and bounding it (a displacement cap) were both built and both
# died here: each improved the edge COUNT while worsening worst-edge and folds
# (PIPELINE.md, #collar-fine-tessellation). This changes neither -- only how far
# the feather carries.
#
# Applied as a MULTIPLIER on the caller's own `iters`, not a global reach, so
# every pass keeps its own relative strength; and floored at `iters`, so a
# coarse shape is bit-identical to today and only fine meshes get more rings.
#
# THE SCALING IS QUADRATIC, AND THAT IS PHYSICS, NOT A TUNING CHOICE. Neighbour
# averaging is DIFFUSION: after `n` rounds a displacement has spread about
# `sqrt(n) * edge`, not `n * edge`. Scaling the ring count LINEARLY with the
# tessellation ratio therefore does not restore the distance -- measured on a
# flat grid, a 20x finer mesh came back to 0.20u of feather against the coarse
# mesh's 0.92u (better than the 0.07u it started with, still 4.6x short).
# Matching an `r`-fold finer mesh needs `r**2` times the rounds. Caught by
# `test_feathering_reaches_the_same_distance_on_both`, which measures the
# feather's MASS-WEIGHTED RADIUS in units -- a peak-amplitude threshold reports
# a wider feather as a narrower one, because spreading the same displacement
# further lowers every individual vertex.
#
# AND IT REACHES ONLY THE SCALAR FEATHER, WHICH THE DEFAULT PATH DOES NOT RUN.
# `_reach_iters` is called from `_smooth_push_field` alone, and both callers of
# that sit BELOW a `#clearance-field` solve that returns early whenever it
# succeeds -- and `CLEARANCE_FIELD_SOLVE`/`CLEARANCE_FIELD_INFLATE` are both
# default ON. So on the shipped path this knob has never had anything to
# compensate. The field solve needs a different remedy anyway: it is SCREENED, so
# its reach is set by `lam` rather than by the ring count, and it already reports
# `converged=True` at the default 256 iterations on the very meshes that crease.
# More rings do nothing there. See `#field-screen-physical` in
# nif_convert_fitgeom.py, which scales the mass term instead.
_SMOOTH_REF_EDGE = 1.0        # the tessellation the current ring counts assume
_SMOOTH_REACH_MAX = 400       # bound the cost; the finest strip here asks ~320
SMOOTH_REACH = _flag("CBBE2UBE_SMOOTH_REACH", False)

# A weld edge is ~0 long and is NOT tessellation: two coincident verts at a UV
# seam are one point of surface, so counting the join between them as an
# incident edge reads every seam vertex as infinitely fine. Excluded everywhere
# a local scale is measured.
_REACH_WELD_EPS = 1e-4


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# #edge-scaled-reach -- OPT-IN, `CBBE2UBE_EDGE_SCALED_REACH=1`.
#
# EVERY SMOOTHING NEIGHBOURHOOD IN THIS FILE IS SIZED IN GRAPH STEPS, WHICH IS
# A MESH-DEPENDENT UNIT. A ring count reaches `rings * edge` units, and a
# screened Laplacian's `lam` gives a decay length of `edge / sqrt(lam)` units --
# so ONE constant means one distance on a leather panel and a twentieth of it
# on a buckle strip. That is the shared cause under three open defects:
# BUG-08 (a 0.49u skin-tight suit that cannot be tightened without folding),
# BUG-01 (a pointwise correction to a smooth field relocating its
# discontinuity), and #collar-fine-tessellation ("passes work in ABSOLUTE
# units; TWO fixes dead, only smoothing-REACH untried").
#
# TWO PARTIAL ANSWERS WERE ALREADY HERE, and neither is this one:
#   `#smooth-reach`             ABSOLUTE, but ONE median per SHAPE, and only
#                               for ring counts.
#   `#conform-adaptive-reach`   PER VERTEX, but RELATIVE to the shape's own
#                               median -- it equalises reach WITHIN a shape and
#                               still gives a uniformly fine shape a shorter
#                               world-space reach than a coarse one.
# The missing primitive is a reach stated in UNITS and resolved PER VERTEX, so
# a 0.49u mesh and a 2.5u mesh behave the same way relative to their own
# detail, and a shape that is fine in one region and coarse in another gets
# both right at once.
#
# THE MECHANISM. For the screened solve this file already runs -- `(deg + lam)
# u_i = sum_j u_j + lam * D_i`, i.e. `sum_j (u_j - u_i) - lam * u_i = -lam D_i`
# -- substituting a decaying response `u_i ~ exp(-x / delta)` on a chain of
# spacing `elen` gives `2 (cosh(elen / delta) - 1) = lam`, so for small `lam`
#
#       delta = elen / sqrt(lam)        units
#
# Setting `delta` to a stated reach `R` therefore needs
#
#       lam_i = (elen_i / R) ** 2
#
# which is the whole change: a per-vertex mass term proportional to the SQUARE
# of the local edge length. On a 1.0u mesh, today's constant `lam = 0.5` is
# exactly `R = 1.414u`, which is where the default below comes from -- so a
# coarse mesh keeps roughly the behaviour it has and fine geometry gains reach.
# The 2-D constant is valence-dependent (a valence-6 umbrella puts the true
# decay nearer 1.2 R than R), so `R` is a CALIBRATED distance, not a certified
# one; it is named in units because units are the thing that must not depend on
# the mesh, not because 1.414 is exact.
#
# WHY NOT SCALE THE RING COUNT INSTEAD, which is what `#smooth-reach` does: a
# ring loop applies ONE count to every vertex, so raising it to serve the fine
# tail over-smooths the coarse regions of the same shape. Per-vertex reach is
# not expressible as a ring count at all -- it has to live in the OPERATOR.
# That is why this wires into the screened solve and not into `_reach_iters`.
EDGE_SCALED_REACH = _flag("CBBE2UBE_EDGE_SCALED_REACH", False)
# The reach in UNITS. 1.414 reproduces today's `lam = 0.5` on a 1.0u mesh.
REACH_UNITS = _knob("CBBE2UBE_REACH_UNITS", 1.414)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


def _welded_edges(V, T, weld_tol=1e-3):
    """Surface adjacency for a smoothing solve: triangle edges PLUS weld edges
    between coincident vertices. Returns (src, dst, deg, live), or four Nones
    when there is no usable adjacency.

    The weld half is not optional (a component is not an object): coincident
    verts at a UV seam are separate indices at identical positions, and with no
    triangle edge between them a Laplacian never couples them -- so the seam
    re-diverges under exactly the smoothing that was meant to close it. Group by
    rounded position and chain each group, so one SURFACE relaxes as one.

    Shared by `_solve_clearance_field` and `#conform-stretch-field`; both need
    the identical notion of "next to", and two copies would drift.
    """
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    n = len(V)
    if n < 3 or not len(T):
        return None, None, None, None
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    key = np.round(V / float(weld_tol)).astype(np.int64)
    order = np.lexsort((key[:, 2], key[:, 1], key[:, 0]))
    sk = key[order]
    bnd = np.where(np.any(np.diff(sk, axis=0) != 0, axis=1))[0] + 1
    starts = np.concatenate([[0], bnd, [n]])
    weld = [np.column_stack([order[a:b - 1], order[a + 1:b]])
            for a, b in zip(starts[:-1], starts[1:]) if b - a > 1]
    if weld:
        e = np.concatenate([e] + weld)
    e = np.concatenate([e, e[:, ::-1]])
    e = e[(e[:, 0] >= 0) & (e[:, 1] >= 0) & (e[:, 0] < n) & (e[:, 1] < n)]
    if not len(e):
        return None, None, None, None
    src, dst = e[:, 0], e[:, 1]
    deg = np.bincount(src, minlength=n).astype(np.float64)
    return src, dst, deg, deg > 0


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# Push soft-body / HDT-rigged CLOTH outward over the breast & butt where the larger
# UBE body pokes through it. The main anti-poke (clear_armor_outside_body) SKIPS
# soft-body / physics shapes because moving every vert disturbs the sim; this pass
# is band-limited to the breast + butt so only the poke-through zone is nudged (the
# sim rest shape is otherwise untouched). Body-PRESERVING (never moves the body),
# push-out only. Confirmed in-game (a fur-collared cuirass breast). Default ON;
# CBBE2UBE_NO_SOFTCLOTH_INFLATE=1 disables.
INFLATE_SOFTCLOTH = (
    not _flag("CBBE2UBE_NO_SOFTCLOTH_INFLATE", False))
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# (moved to nif_convert_fitgeom.py, 2026-09-01)
# --- #softcloth-own-plane -- DEFAULT ON since 2026-09-04 --------------------
# Kill switch: CBBE2UBE_NO_SOFTCLOTH_OWN_PLANE=1
# The pass above measures a cloth vert's deficit against the POKING body
# vertex's tangent plane, then applies the push along the CLOTH vertex's OWN
# nearest body normal. Those are two different vertices, up to `radius` (4.0u)
# apart on a curving chest, so the pass overshoots its own target: it is built
# to hold cloth `_SOFTCLOTH_BUST_CLEAR` = 1.8u proud and measured holding it
# 2.19u proud on a reported over-inflated cuirass.
#
# Simulated on that piece's real stage geometry (input = s06_panel_rigidity):
#     no push                       band standoff median 1.448u
#     as shipped                    2.186u   (317 verts, max push 3.914)
#     measured on the OWN plane     1.800u   (256 verts, max push 2.751)
# i.e. the fix lands exactly on the designed 1.8u and removes 0.386u of chest
# standoff, while still covering -- it declines 61 of 317 verts, all of them
# verts already standing `clear` proud of the body NEAREST THEM, which are by
# definition not the ones the body is poking through.
#
# MONOTONE BY CONSTRUCTION: both halves can only ever LOWER `need` (a vert
# already clear is skipped; otherwise the deficit is capped by the one measured
# on its own plane). So this pass can give clearance back, never take more --
# the same safety property that made `#panel-rigid-surface-guard` shippable.
#
# The A/B it was held for was run 2026-09-04 on a reported piece (folds in the
# band 109 -> 98, max push 3.71u -> 2.10u, 6 pieces better / 0 worse) and the
# build carrying it was judged good in game. DEFAULT ON.
# PROMOTED TO DEFAULT ON 2026-09-04 after the in-game verdict on the strap
# defect; monotone by construction (it can only LOWER the push).
SOFTCLOTH_OWN_PLANE = not _flag("CBBE2UBE_NO_SOFTCLOTH_OWN_PLANE", False)
# --- #softcloth-smooth-direction -- DEFAULT ON since 2026-09-04 -------------
# Kill switch: CBBE2UBE_NO_SOFTCLOTH_SMOOTH_DIR=1
# The softcloth push smooths its MAGNITUDE but aims every vert along its own
# body normal, so a thin feature crossing a curving body buckles on the
# direction differential alone. Smooths the direction field too, re-projected
# so the pass stays push-out only. See `_inflate_cloth_over_bust_butt`.
SOFTCLOTH_SMOOTH_DIR = not _flag("CBBE2UBE_NO_SOFTCLOTH_SMOOTH_DIR", False)
# Max EXTRA lift softcloth may add to a vert that is already OUTSIDE the body.
# 0 disables the cap (ship behaviour: everything is raised to the bust headroom
# `clear`, which lifts correctly-seated straps off the collarbone).
# See #softcloth-seated-cap in `_inflate_cloth_over_bust_butt`.
SOFTCLOTH_SEATED_CAP = _knob("CBBE2UBE_SOFTCLOTH_SEATED_CAP", 0.3)
# Minimum fraction of BREAST-BAND vertex weight that must be carried by CHAIN
# (non-body) bones for the bust to count as physics-driven. Below this the bust is
# rigid/body-skinned -> use the normal anti-poke (clearance cap) not the softcloth
# inflation. See _shape_bust_is_softbody_driven / #softcloth-bust-driven-gate.
_SOFTCLOTH_BUST_CHAIN_MIN = _knob("CBBE2UBE_SOFTCLOTH_BUST_CHAIN_MIN", 0.20)


def _shape_bust_is_softbody_driven(shape, body_bone_names, softbody_names,
                                   threshold: float = _SOFTCLOTH_BUST_CHAIN_MIN) -> bool:
    """True when the shape's BREAST band is genuinely physics-driven, so the
    anti-poke (which moves poking verts) would disturb its sim and the softcloth
    breast-inflation must handle clearance instead.

    A per-vertex soft-body qualifies outright (the whole shape is simulated).
    Otherwise measure the fraction of breast-band vertex weight carried by CHAIN
    (non-body) bones: an HDT-rigged robe whose chains drive only the SKIRT has a
    RIGID, body-skinned bust (fraction ~0) and must go through the normal
    anti-poke -- routing it to the softcloth inflation BALLOONS the rigid bust
    (a chain-rigged robe whose chains drive only the skirt: 60% chain bones
    overall but bust chain-fraction ~0.01, inflated +3.2u). Replaces the old
    whole-shape `_shape_has_hdt_smp_rigging` test for the softcloth-vs-antipoke
    split. #softcloth-bust-driven-gate"""
    try:
        if shape.name in softbody_names:
            return True
        v = np.asarray(shape.verts, np.float64)
        bust = np.where((v[:, 2] > 92) & (v[:, 2] < 100) & (v[:, 1] > 4)
                        & (np.abs(v[:, 0]) < 13))[0]
        if len(bust) < 8:
            # No meaningful bust to judge (e.g. a skirt) -> don't strip softcloth;
            # it only touches bust+butt bands and there's no bust here to balloon.
            return True
        bset = set(int(i) for i in bust.tolist())
        wtot = wchain = 0.0
        for b, pairs in (shape.bone_weights or {}).items():
            isc = b not in body_bone_names
            for vi, ww in pairs:
                if int(vi) in bset:
                    wtot += float(ww)
                    if isc:
                        wchain += float(ww)
        return wtot > 0.0 and (wchain / wtot) >= threshold
    except Exception:
        return False


# (moved to nif_convert_fitgeom.py, 2026-09-01)


def shape_body_offset(shape, body_verts=None) -> np.ndarray:
    """Translation that maps a shape's STORED (local) verts into body space.

    PASS `body_verts` TO GET THE OFFSET CHECKED (2026-07-29). Without it the
    behaviour is exactly as before, so untouched callers are unaffected.

    WHY THE CHECK EXISTS, measured not reasoned. This adds a shape's
    `NiAVObject.transform.translation` to its verts. That is right for a shape
    authored in a shifted space and repositioned by that transform at render.
    It is WRONG for a SKINNED shape already in body space, because a skinned
    shape renders through its skin data and the NiAVObject transform is inert --
    adding it displaces the shape bodily. A real cuirass carries translation
    [-40, 0, 0] with an IDENTITY global_to_skin and verts already correctly
    placed; the offset moved it 40u sideways, and the median distance from bust
    skin to that garment went 2.11u -> 21.09u. Every phase-2 fit pass (warp,
    conform, inflate, anti-poke) was therefore matching a garment that was not
    where the body is, which is a strong candidate for why that piece resisted
    fixing and shipped at 8.87% bust clipping.

    A census over 765 source NIFs across 194 mods: 351 shapes carry a non-zero
    offset, and of those, restricted to shapes genuinely fitted to a body
    (median nearest-vert reach < 5u), the ones the offset DISPLACES were 4/4
    skinned-with-identity-g2s. The offset is genuinely REQUIRED for others --
    one stabiliser shape measures 40.26u raw and 5.48u with the offset -- so it
    cannot simply be removed.

    The check is therefore GEOMETRIC rather than a rule about NIF semantics: if
    adding the offset moves the shape FURTHER from the body, it is not a
    body-space correction and is discarded. That cannot mis-handle a shape which
    needs the offset (for those, the offset moves it closer and is kept), and it
    degrades gracefully on cases nobody has looked at yet.

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
    zero = np.zeros(3, dtype=np.float64)
    tr = getattr(shape, "transform", None)
    t = getattr(tr, "translation", None) if tr is not None else None
    if t is None:
        return zero
    try:
        off = np.array([float(t.x), float(t.y), float(t.z)], dtype=np.float64)
    except Exception:
        try:
            off = np.array([float(t[0]), float(t[1]), float(t[2])],
                           dtype=np.float64)
        except Exception:
            return zero
    if body_verts is None or not np.any(np.abs(off) > 1e-6):
        return off
    # GEOMETRIC CHECK. Median nearest-vert distance to the body, with and
    # without the offset; keep the offset only if it moves the shape CLOSER.
    # Median (not mean) so a handful of far verts on a long shape cannot decide
    # it. The 0.5u slack means a wash leaves existing behaviour alone.
    try:
        bv = np.asarray(body_verts, dtype=np.float64)
        v = np.asarray(shape.verts, dtype=np.float64)
        if bv.ndim != 2 or len(bv) < 3 or v.ndim != 2 or len(v) < 3:
            return off
        from scipy.spatial import cKDTree as _KD
        tree = _KD(bv)
        d_raw = float(np.median(tree.query(v, k=1)[0]))
        d_off = float(np.median(tree.query(v + off, k=1)[0]))
        if d_off > d_raw + 0.5:
            return zero          # the offset is not a body-space correction
        return off
    except Exception:
        return off               # never fail a conversion over a sanity check


# (moved to nif_convert_fitgeom.py, 2026-09-01)


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


# --- broken pass vs failed design -------------------------------------------
# The recorders (`_note_pass_failure`, `_note_pass_effect`, the per-piece and
# per-process summaries) and their state live in nif_convert_telemetry.py
# since 2026-09-01 (split step 1). Imported BY NAME so `nc.<name>` keeps
# working for every caller, test and tool; the state dicts/lists are the SAME
# objects (mutated in place, never rebound), so `nc._PASS_FAILURES_THIS_PIECE`
# .clear() in a test clears what the recorder appends to.
from .nif_convert_telemetry import (  # noqa: E402
    _PASS_FAILURES, _PASS_FAILURES_THIS_PIECE, _PASS_EFFECTS,
    _PASS_EFFECTS_THIS_PIECE, _note_pass_failure, _note_pass_effect,
    _piece_pass_effects, pass_effect_summary, _begin_piece_pass_log,
    _piece_pass_failures, pass_failure_summary, make_stage_hook,
)
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


# A "3BA"-family shape name (the CBBE 3BA body / its parts). Mod authors leave a
# BodySlide REFERENCE body -- the full CBBE body, named "3BA Ref"/"3BA Reference"
# -- inside skimpy armor NIFs, with a rendering shader but the ARMOR's diffuse
# texture. The texture-gated body heuristic misses it (diffuse isn't a body skin)
# so a whole CBBE body renders on the UBE actor (boots/panties/corset: the
# in-game "explosion" that survived every mesh check). We swap it to the UBE body
# by matching the name here -- but ONLY together with a FULL-BODY geometry gate
# (see _looks_like_inline_body), so the SMALLER lower-body "3BA Ref" COLLISION
# PROXIES that skirt HDT-SMP XMLs legitimately reference (~13k verts, z-range
# ~78, listed as a per-vertex-shape) are left untouched. #3ba-ref-body
def _is_3ba_body_family_name(name: "str | None") -> bool:
    return (name or "").lower().startswith("3ba")


# Full-body gates for the 3BA-ref-body swap: the full CBBE body is ~31.9k verts
# spanning the whole character (~132u); a skirt collision proxy is ~13.4k verts
# spanning only the lower body (~78u). Thresholds sit between the two.
_INLINE_BODY_3BA_REF_MIN_VERTS = 20000
_INLINE_BODY_3BA_REF_MIN_Z_RANGE = 100.0


# (moved to nif_convert_writer.py, 2026-09-01)

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
# a layered robe etc. — the converted NIF had BaseShape + Panty only).
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
    #
    # #body-name-prefix: GATED ON THE TEXTURE, 2026-09-09. This branch used to
    # `return True` on the NAME ALONE -- the exact thing the `3BA`-family branch
    # below refuses to do, because its own comment says a name without a
    # geometry gate over-fires. It does: an author named one garment's CUIRASS
    # `FemaleUnderwearBody:0` and its ARMS `...:0_1`, both on armour diffuse,
    # and both were deleted and replaced with one injected body -- so that
    # piece's `_1` half, the one actors near weight 100 use, shipped with no
    # cuirass and no arms.
    #
    # Measured over every source behind the pack, loose and archived: 34 shapes
    # match these prefixes, 32 carry a body-skin diffuse and are stripped
    # correctly, 2 do not and are this defect. The texture test below is the
    # detector's OWN, already applied to the general heuristic, and it separates
    # all 34 with no exceptions.
    name_low = (shape.name or "").lower()
    for prefix in BODY_SHAPE_NAME_PREFIXES:
        if name_low.startswith(prefix) and _shape_diffuse_is_body_skin(shape):
            return True
    if shape.name == "BaseShape" and len(shape.verts) >= _UBE_BASESHAPE_MIN_VERTS:
        return True
    if shape.name == "VirtualBody" and len(shape.verts) >= _UBE_VIRTUALBODY_MIN_VERTS:
        return True
    # A "3BA Ref"-style reference body (full CBBE body left in skimpy armor,
    # rendering with the ARMOR diffuse so the texture gate below misses it).
    # Catch it by the "3BA" name PLUS a full-body geometry gate, which excludes
    # the smaller lower-body "3BA Ref" collision proxies skirts reference. #3ba-ref-body
    if _is_3ba_body_family_name(shape.name) and len(shape.verts) >= _INLINE_BODY_3BA_REF_MIN_VERTS:
        import numpy as _np
        _z = _np.asarray(shape.verts, dtype=_np.float64)[:, 2]
        if (float(_z.max() - _z.min()) >= _INLINE_BODY_3BA_REF_MIN_Z_RANGE
                and len(shape.bone_names) >= _BODY_SKIN_MIN_BONES):
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


# (moved to nif_convert_writer.py, 2026-09-01)


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


# --- The per-shape FIT STAGE TABLE: the contract both paths are held to ------
#
# `_fit_shapes_copy` and `_fit_shapes_swap` (lifted verbatim out of the two
# entry functions, 2026-09-01) each call the fit passes below, in this order.
# The 2026-09-01 audit found the two loops had drifted for years -- a fix
# landed on one path and not the other, twice tearing 5% of the pack -- and
# that nothing could say WHICH differences were deliberate. This table is
# that statement, and `tests/test_fit_stage_table.py` derives the real call
# sequence from both functions and fails when:
#   * a stage marked for both paths is missing from one, or out of order;
#   * a stage on both paths is called with different keyword arguments and
#     no `reason` explains it;
#   * a stage marked for one path has no `reason` for its absence on the other.
# It does NOT execute anything: the guards around each call use path-specific
# state and the two paths handle a failing stage differently on purpose (the
# copy path abandons the shape's chain, phase 2 records the stage and goes on),
# so driving the calls from here would encode those differences, not remove
# them. Change the code and the table together; the test will say if you
# only did one.
#
# Rows: (label, callee, paths, reason-for-difference-or-None). A callee may
# appear more than once (the fine-animation sub-branch runs warp and panel
# rigidity again on both paths).
FIT_STAGES = (
    ("warp_hf",             "warp_armor_by_body_delta",   ("copy", "swap"), None),
    ("panel_rigid_hf",      "_rigidify_within_clearance", ("copy", "swap"), None),
    ("panel_blind_hf",      "_partial_rigid_panels",      ("copy", "swap"),
     "BRANCH-GATED on both paths: it is the ELSE of `PANEL_RIGID_EARLY_CLEAR and "
     "<body verts and normals available>`, i.e. the body-BLIND form that runs "
     "when the guarded clearance form does not. PANEL_RIGID_EARLY_CLEAR is "
     "default OFF (F010, re-measured 2026-09-02: still OFF), so this blind arm "
     "is what actually runs today."),
    ("warp",                "warp_armor_by_body_delta",   ("copy", "swap"), None),
    ("conform",             "conform_to_source_standoff", ("copy", "swap"),
     "F094 (audit 2026-09-01): phase 2 passes ube_body_nipple / conform_margin / "
     "morph_differential; the copy path passes them only under "
     "CBBE2UBE_PHASE1_NIPPLE_MAP (measured, OFF, in-game verdict owed) and runs a "
     "second, push-out-only call (blend=0.0) under PHASE1_BUST_CLEARANCE."),
    ("groove_smooth",       "_smooth_warp_grooves",       ("copy", "swap"),
     "BRANCH-GATED on both paths, by the SAME test that gates the snap below: "
     "`cbbe_verts_for_warp is not None and body_delta_for_warp is not None`. "
     "This is the IF arm (a CBBE base body exists -- the normal case); the ELSE "
     "arm runs the legacy snap instead. The two are alternatives, never both."),
    ("snap",                "snap_armor_outside_body",    ("copy", "swap"),
     "READ THE BRANCH, NOT THE ROW: on the COPY path this call sits in the ELSE "
     "of `cbbe_verts_for_warp is not None and body_delta_for_warp is not None` "
     "-- the LEGACY no-CBBE-body fallback -- while the IF branch (the normal "
     "case, and every BodySlide output) runs warp/inflate/conform/groove_smooth "
     "and NO snap. The two are mutually exclusive, so a normal copy-path piece "
     "gets NO body-relative push-out at all: no snap (wrong branch), no antipoke "
     "and no bust/butt inflate (both swap-only below). Measured 2026-09-02 on "
     "the reported trousers -- author 0.0% of the butt band inside its own body, "
     "ours 59.3%, and 687 of those verts sit in 0.2-0.6u, exactly the window "
     "`snap` exists to close. That is BUG-02. This row said `(copy, swap)` with "
     "no reason until then, and the table's own test cannot catch it: it derives "
     "calls by walking the AST, which sees both arms of an if/else. "
     "docs/worklog/BUTT_COPY_PATH_TROUSERS.md"),
    ("panel_rigid",         "_rigidify_within_clearance", ("copy", "swap"), None),
    ("panel_blind",         "_partial_rigid_panels",      ("copy", "swap"), None),
    ("antipoke",            "clear_armor_outside_body",   ("copy", "swap"),
     "BOTH PATHS AT DEFAULTS since 2026-09-02, when `#phase1-antipoke` was "
     "promoted (`CBBE2UBE_NO_PHASE1_ANTIPOKE=1` restores the old behaviour). "
     "Before it the copy path had NO body repair at all -- BUG-02, measured at "
     "59.3% of the butt band inside the body on the reported trousers against the "
     "author's own 0.0%. "
     "The copy call passes far fewer kwargs than phase 2 (no nipple map, morph "
     "amplitude/differential, jiggle amplitude or layer extra): those are all "
     "body-swap-derived inputs the copy path does not compute, and the pass "
     "documents its own fallback for each. "
     "docs/worklog/BUTT_COPY_PATH_TROUSERS.md"),
    ("panel_rigid_post",    "_rigidify_within_clearance", ("copy", "swap"),
     "Rides `#phase1-antipoke` on the copy path, for the same reason phase 2 "
     "pairs them: the anti-poke re-deforms every panel it pushes and this "
     "recovers the rest. Porting the push without the recovery would ship the "
     "damage and not the repair. Default ON with its anti-poke since "
     "2026-09-02. The pair is ordered exactly as phase 2 orders it: push, "
     "restore simulated verts, then recover -- so the ~0.3% of barely-chained "
     "verts the per-panel recovery moves is inherent to the pair, not to this "
     "path (measured on 48 chain-carrying shapes: 130 of 42994, none of them "
     "fully chain-driven)."),
    ("inflate",             "_inflate_cloth_over_bust_butt", ("swap",),
     "Soft-cloth inflate over bust/butt is a body-swap stage; the copy path's "
     "inflation is `_slot_aware_inflation_magnitude` inside its warp block."),
    ("chain_blend",         "_physics_chain_nowarp_blend", ("copy", "swap"), None),
    ("uniformise_scale",    "_uniformise_local_scale",    ("swap",),
     "Phase-2-only local-scale uniformising; parity reason recorded in "
     "tests/test_convert_path_parity.py."),
    ("short_edge",          "_cap_short_edge_stretch",    ("swap",),
     "Phase-2-only short-edge cap; parity reason recorded in "
     "tests/test_convert_path_parity.py."),
)


def _fit_shapes_copy(ctx) -> None:
    """The copy path's per-shape fit chain: warp, inflate, conform (the two clearance sites), groove smoothing, snap, panel rigidity and the chain blend, plus the fine-animation sub-branch, for every shape of a piece that gets no injected body.

    Lifted verbatim out of `convert_nif` on 2026-09-01 (audit step 6, increment 1):
    the loop body below is the orchestrator's own text, unchanged; `ctx` carries
    exactly the orchestrator locals it read. Results still flow through the
    mutated containers on `ctx` (the shape jobs and the failure list).
    """
    _nip_kw = ctx._nip_kw
    biped_slots = ctx.biped_slots
    body_delta_for_warp = ctx.body_delta_for_warp
    body_normals_for_fit = ctx.body_normals_for_fit
    body_verts_for_fit = ctx.body_verts_for_fit
    cbbe_body_path_p1 = ctx.cbbe_body_path_p1
    cbbe_verts_for_warp = ctx.cbbe_verts_for_warp
    dst_path = ctx.dst_path
    extremity_slots_to_replace = ctx.extremity_slots_to_replace
    failed = ctx.failed
    hdt_collider_names = ctx.hdt_collider_names
    hdt_softbody_names = ctx.hdt_softbody_names
    layered_cloth_names = ctx.layered_cloth_names
    shape_jobs_p1 = ctx.shape_jobs_p1
    src_nif_for_fit = ctx.src_nif_for_fit
    ube_base_for_reskin = ctx.ube_base_for_reskin

    for s in src_nif_for_fit.shapes:
        if _should_drop_shape(s.name):
            continue  # vestigial mashup leftover (e.g. MaleUnderwearBody)
        if _is_body_skin_extremity(s.name):
            # ALWAYS drop the source CBBE body-skin Hands/Feet shape.
            # The working BOOTS carry NO body-skin shape (just the boot
            # shell) and render; the only structural thing GAUNTLETS had
            # that boots didn't was this extra body-skin "Hands" shape.
            # Dropping it makes a gauntlet structurally match a boot.
            # Then put the UBE version back. The flag is ON by default
            # and this comment used to call it "(now-off)" -- wrong, and
            # wrong in the dangerous direction: with it off the shape is
            # dropped with NO replacement and the bare hand is invisible
            # under the gauntlet. Measured 2026-08-22 on the output pack:
            # 172/172 shipped `Hands` shapes are UBE topology, 0 CBBE.
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

            # ---- DIAGNOSTICS ON THE FINE-ANIMATION SUB-BRANCH -------
            # This branch `continue`s before the copy path's own
            # arming below, so until 2026-08-23 it took NO checkpoint
            # at all -- and it is not a small corner: the 08-23
            # pass-usefulness re-run measured 17 of 84 sampled copy
            # `_1` shapes here (20.2%), and SIX of 28 sampled copy
            # pieces produced no survival row whatsoever, one of them
            # a slot-32 first-person torso whose two shapes both carry
            # hand bones. A shape with no row reads downstream exactly
            # like a pass that moved nothing, which is the confusion
            # both tools exist to end
            # ([[project_pass_usefulness_audit_2026_08_17]]).
            #
            # LABELS ARE `warp_hf` / `inflate_hf`, NOT `warp` /
            # `inflate`, deliberately. They ARE the same two functions,
            # but called with different constants
            # (ARMOR_INFLATION_MAGNITUDE_HANDS_FEET,
            # HAND_FOOT_INFLATION_FALLOFF) and blended through the
            # extremity mask, so pooling them under the main labels
            # would silently move published copy-path numbers by
            # changing the population underneath them. Separate rows
            # can always be added up; a pooled one cannot be split.
            _surv_hf = fit_metrics.DisplacementSurvival()
            if not _surv_hf.armed:
                _surv_hf = None
            else:
                _surv_hf.checkpoint("entry", hf_orig)
            _dump_hf = fit_metrics.GeometryDump()
            if not _dump_hf.armed:
                _dump_hf = None
            else:
                _dump_hf.checkpoint("entry", hf_orig)

            def _stage_hf(label, v, _s=_surv_hf, _d=_dump_hf):
                """A pass boundary on the fine-animation sub-branch."""
                if v is None:
                    return
                if _s is not None:
                    _s.checkpoint(label, v)
                if _d is not None:
                    _d.checkpoint(label, v)

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
                        tris=np.asarray(s.tris, dtype=np.int64),
                    ).astype(np.float64)
                    if hf_ef is not None:
                        wf = (1.0 - hf_ef)[:, None]
                        hf_verts = hf_orig + (warped - hf_orig) * wf
                    else:
                        hf_verts = warped
                    hf_verts_modified = True
                except Exception as e:
                    failed.append((f"{s.name}:warp-hf", repr(e)))
                # OUTSIDE the try, matching the main chain's
                # `_stage_p1('warp', ...)`: if the warp raised,
                # `hf_verts` is still the entry geometry and the row
                # correctly reads "pass moved nothing" rather than
                # vanishing.
                _stage_hf('warp_hf', hf_verts)
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
                _stage_hf('inflate_hf', hf_verts)

            # #panel-rigidity-fine-anim, opt-in. Digits masked out.
            if PANEL_RIGIDITY_FINE_ANIM and PANEL_RIGIDITY > 0:
                try:
                    # #panel-rigid-early-clearance reaches here too.
                    # This site is behind a DIFFERENT opt-in
                    # (PANEL_RIGIDITY_FINE_ANIM), so leaving it on the
                    # blind form would plant the same body-blind
                    # penetration for whoever promotes that flag later
                    # -- the exact shape of gap that let `inflate_hf`
                    # skip the authored floor unnoticed.
                    if (PANEL_RIGID_EARLY_CLEAR
                            and body_verts_for_fit is not None
                            and body_normals_for_fit is not None):
                        _pv_hf, _np_hf, _wd_hf = (
                            _rigidify_within_clearance(
                                hf_orig, hf_verts,
                                np.asarray(s.tris, dtype=np.int64),
                                body_verts_for_fit,
                                body_normals_for_fit,
                                PANEL_RIGIDITY,
                                skip_mask=_extremity_vert_mask(
                                    s, len(hf_verts)),
                                min_verts=PANEL_RIGIDITY_MIN_VERTS))
                    else:
                        _pv_hf, _np_hf, _wd_hf = _partial_rigid_panels(
                            hf_orig, hf_verts,
                            np.asarray(s.tris, dtype=np.int64),
                            PANEL_RIGIDITY,
                            skip_mask=_extremity_vert_mask(
                                s, len(hf_verts)),
                            min_verts=PANEL_RIGIDITY_MIN_VERTS)
                    if _np_hf:
                        hf_verts = _pv_hf
                        hf_verts_modified = True
                        _stage_hf('panel_rigidity_hf', hf_verts)
                        _note_pass_effect(
                            "#panel-rigidity-fine-anim",
                            f"{s.name}: {_np_hf} panel(s), worst "
                            f"deform {_wd_hf:.3f}u", dst_path)
                except Exception as _pe_hf:
                    _note_pass_failure(
                        "panel-rigidity/fine-anim", _pe_hf)

            # THE GEOMETRY CHAIN ENDS HERE, AND THAT IS AN UNCLOSED GAP,
            # NOT A DESIGN. #panel-rigidity, #phase1-conform, the groove
            # smooth and the chain blend all run on the main chain below
            # and none of them runs here.
            #
            # `_partial_rigid_panels` is the one that matters, because it
            # is DEFAULT 0.75 since 2026-08-22 and its whole purpose was
            # to stop a knob silently splitting the pack. It splits it
            # again here, one level down.
            #
            # THE OBVIOUS REASON IS FALSE AND WAS MEASURED, NOT ASSUMED.
            # "Gauntlets and boots are single rigid shells, so panel
            # rigidity would be a no-op" is wrong. Replaying the pass
            # offline on this branch's own stage dumps (51 fine-animation
            # shapes, 2026-08-23, probe validated by reproducing the real
            # result EXACTLY on 485 main-chain shapes) it would fire on
            # 51 of 51, finding 568 qualifying panels and moving verts by
            # a median 0.214u -- the same order as the 0.211u it moves on
            # the copy main chain. `Arcane_Mage_Boots` alone offers 88
            # panels at 0.82u worst deformation. So this is live work
            # being skipped, not work with nothing to do.
            #
            # DO NOT PORT IT AS A TIDY-UP. It is a behaviour change on
            # ~20% of copy-path shapes, #panel-rigidity is explicitly a
            # TRADE (a rigid plate cannot follow a growing body), and
            # `warp_hf` here is ALREADY 30-45% cancelled by `inflate_hf`
            # -- a third pass pulling back toward the source would
            # compound that. It needs an in-game verdict of its own.
            #
            # AND THE PARITY GUARD CANNOT SEE THIS. `tests/
            # test_convert_path_parity.py` walks the CALL GRAPH between
            # two entry points; both fine-animation branches live INSIDE
            # those same two functions, so a sub-branch asymmetry is
            # invisible to it by construction. Measured via the survival
            # trace instead ([[project_pass_usefulness_audit_2026_08_17]]).
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
            # Flush BEFORE the `continue` -- the main chain's flush is
            # past it and would never run for this shape. Same contract
            # as that one: a flush error is RECORDED and an EMPTY dump
            # is called out rather than passing as a clean run. Scope is
            # the per-shape chain only; the cross-shape passes run later
            # over `shape_jobs_p1` and are outside it either way.
            if _surv_hf is not None:
                try:
                    _surv_hf.flush(dst_path, s.name, hf_verts)
                except Exception as e:
                    failed.append((f"{s.name}:survival-hf", repr(e)))
                finally:
                    _surv_hf.release()
            if _dump_hf is not None:
                try:
                    if not _dump_hf.flush(dst_path, s.name, s.tris,
                                          hf_verts):
                        failed.append((f"{s.name}:stagedump-hf",
                                       "wrote nothing"))
                except Exception as e:
                    failed.append((f"{s.name}:stagedump-hf", repr(e)))
                finally:
                    _dump_hf.release()
            continue
        # World-frame verts for the fit (see the top-of-loop note);
        # _shape_g2s was computed once above. Identity -> no-op.
        sv_world = _verts_skin_to_world(
            np.asarray(s.verts, dtype=np.float64), _shape_g2s)

        # ---- DIAGNOSTICS ON THE COPY PATH (both default OFF) --------
        # Until 2026-08-22 `DisplacementSurvival` and `GeometryDump`
        # armed ONLY in phase 2, so the project's two first-reach
        # measurement tools could not see the ~74% of the pack that
        # takes this path. That is why an earlier population test read
        # "UNMEASURED" rather than "no change", and why the 2026-08-17
        # pass-usefulness audit ("`inflate` is 69% UNDONE by `conform`")
        # describes ONLY the body-swap population -- its numbers were
        # never measurable here.
        #
        # It is not merely unmeasured, it is expected to DIFFER: that
        # audit's canceller is `conform`, and on this path
        # `conform_to_source_standoff` is behind `PHASE1_CONFORM`,
        # DEFAULT OFF. If the canceller does not run, inflate's motion
        # may fully survive here.
        #
        # SCOPE, stated so the trace is not over-read: this covers the
        # PER-SHAPE chain only (warp -> inflate -> conform ->
        # groove_smooth -> panel_rigidity -> chain_blend). The
        # cross-shape passes run later over `shape_jobs_p1` as a whole
        # and are outside it, exactly as phase 2's per-shape trace is.
        _surv_p1 = fit_metrics.DisplacementSurvival()
        if not _surv_p1.armed:
            _surv_p1 = None
        else:
            _surv_p1.checkpoint("entry", sv_world)
        _dump_p1 = fit_metrics.GeometryDump()
        if not _dump_p1.armed:
            _dump_p1 = None
        else:
            _dump_p1.checkpoint("entry", sv_world)

        # A pass boundary on the copy path. Same labels phase 2 uses, so the
        # two populations are directly comparable. Deliberately NO tracer and
        # NO chain: the chain is the rollback checkpoint, and this path has
        # never had it (see make_stage_hook).
        _stage_p1 = make_stage_hook(surv=_surv_p1, dump=_dump_p1,
                                    skip_none=True)

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
                    tris=np.asarray(s.tris, dtype=np.int64),
                )
                _stage_p1('warp', snapped)
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
                        # #authored-inflate on the phase-1 chain too.
                        # Phase 1 has no INLINE body by definition, but
                        # it does have the body the garment was authored
                        # against -- the CBBE base it warps from, the
                        # same pair the phase-1 conform reads below.
                        # This is the ONLY reason the floor carries
                        # authored information at all: it RECOMPUTES the
                        # source-body normals, which ship zero-length,
                        # and a zero normal silently zeroes the authored
                        # standoff. Phase 2 reads the stored ones and is
                        # inert for exactly that reason -- so the claim
                        # this comment used to make, that without it the
                        # floor "would reach only phase 2", is backwards.
                        _a_bn = None
                        try:
                            _a_bn = _cached_cbbe_body_normals(
                                cbbe_body_path_p1)
                        except Exception:
                            _a_bn = None
                        snapped = inflate_armor_outward(
                            snapped, body_verts_for_fit,
                            magnitude=_infl_mag,
                            close_threshold=ARMOR_INFLATION_FALLOFF_DISTANCE,
                            body_normals=body_normals_for_fit,
                            morph_amplitude=_morph_amp,
                            morph_max=ADAPTIVE_CLEARANCE_MORPH_MAX,
                            src_armor_verts=sv_world,
                            src_body_verts=cbbe_verts_for_warp,
                            src_body_normals=_a_bn,
                            tris=np.asarray(s.tris, dtype=np.int64),
                        )
                    except Exception as e:
                        # RECORDED. The pack's main clearance provider;
                        # a silent failure ships a garment with none.
                        failed.append((f"{s.name}:inflate", repr(e)))
                _stage_p1('inflate', snapped)
                # Reel the inflation back to the AUTHORED standoff. Phase 2
                # has always done this; phase 1 inflated with no counter-
                # pass, so a tightly-fitted piece just stood off the body.
                # Source body = the CBBE base the warp is already keyed on.
                # #phase1-conform
                if (PHASE1_CONFORM and cbbe_verts_for_warp is not None
                        and body_verts_for_fit is not None
                        and body_normals_for_fit is not None):
                    try:
                        _src_bn = _cached_cbbe_body_normals(
                            cbbe_body_path_p1)
                        if _src_bn is not None:
                            _amp1 = None
                            try:
                                _amp1 = _cached_body_morph_amplitude(
                                    _find_ube_body_osd(),
                                    body_normals_for_fit,
                                    len(body_verts_for_fit))
                            except Exception:
                                _amp1 = None
                            snapped = conform_to_source_standoff(
                                sv_world,
                                cbbe_verts_for_warp, _src_bn,
                                snapped,
                                body_verts_for_fit,
                                body_normals_for_fit,
                                morph_amplitude=_amp1,
                                tris=np.asarray(s.tris,
                                                dtype=np.int64),
                                **_nip_kw,
                            )
                    except Exception as e:
                        # RECORDED, not swallowed -- the phase-2 sibling
                        # was silently absent for months and looked
                        # identical to "nothing to conform".
                        failed.append((f"{s.name}:phase1-conform",
                                       repr(e)))
                # #phase1-bust-clearance: the CLEARANCE half of
                # the same pass, with `blend=0.0` so there is no
                # pull-in -- only the bust push-out, which is what
                # carries `#bust-morph-chord` and
                # `#bust-surface-req` to this path. `elif`, not a
                # second call: when the full conform runs it has
                # already applied the clearance block, and running
                # it twice would double-charge the push.
                elif (PHASE1_BUST_CLEARANCE
                        and cbbe_verts_for_warp is not None
                        and body_verts_for_fit is not None
                        and body_normals_for_fit is not None):
                    try:
                        _src_bn = _cached_cbbe_body_normals(
                            cbbe_body_path_p1)
                        if _src_bn is not None:
                            _amp1 = None
                            try:
                                _amp1 = _cached_body_morph_amplitude(
                                    _find_ube_body_osd(),
                                    body_normals_for_fit,
                                    len(body_verts_for_fit))
                            except Exception:
                                _amp1 = None
                            snapped = conform_to_source_standoff(
                                sv_world,
                                cbbe_verts_for_warp, _src_bn,
                                snapped,
                                body_verts_for_fit,
                                body_normals_for_fit,
                                morph_amplitude=_amp1,
                                blend=0.0,
                                tris=np.asarray(s.tris,
                                                dtype=np.int64),
                                **_nip_kw,
                            )
                    except Exception as e:
                        # RECORDED for the same reason as the
                        # sibling above: a swallowed failure here
                        # is indistinguishable from 'nothing to
                        # charge', which is the state this flag
                        # exists to change.
                        failed.append(
                            (f"{s.name}:phase1-bust-clearance",
                             repr(e)))
                _stage_p1('conform', snapped)
                # Groove-smooth: flatten warp-induced indent grooves on
                # tight bust cloth. Near-body verts only; decorative shapes unaffected.
                # Source body passed for #groove-authored-cap -- same
                # pairing the conform uses, so both measure the authored
                # standoff against the same reference.
                try:
                    _gc_bn = _cached_cbbe_body_normals(cbbe_body_path_p1)
                except Exception:
                    _gc_bn = None
                # Guarded like _gc_bn: this call sits inside the big try
                # that wraps the whole phase-1 fit, so an exception here
                # would discard the warp, inflate AND conform for this
                # shape -- which reads in the output as the conform
                # having been switched off (torso 0.496 -> 1.830u).
                snapped = _smooth_warp_grooves(
                    sv_world, snapped, body_verts_for_fit,
                    ube_body_normals=body_normals_for_fit,
                    src_body_verts=cbbe_verts_for_warp,
                    src_body_normals=_gc_bn)
                _stage_p1('groove_smooth', snapped)
            else:
                # Legacy fallback: no CBBE base body; push inside-body verts
                # outward along UBE normals.
                snapped = snap_armor_outside_body(
                    sv_world,
                    body_verts_for_fit,
                    body_normals_for_fit,
                )
                _stage_p1('snap_legacy', snapped)
            # #panel-rigidity on the COPY path. Phase 2 has run this
            # since 2026-08-16; without it here the setting SPLITS THE
            # PACK -- layered plates straightened on the ~26% of pieces
            # that body-swap and not on the other ~74%. The guard's own
            # debt note anticipated a DEFAULT FLIP, but a user recipe
            # override does the same damage, and `panel_rigidity` has
            # been 0.75 in the live recipe. #convert-path-parity
            #
            # ONLY THE FIRST HALF PORTS, and that is deliberate:
            #   * `_rigidify_within_clearance` is phase 2's SECOND half
            #     and exists to recover what the ANTI-POKE re-deforms.
            #     The copy path runs no anti-poke (`clear_armor_outside_
            #     body` is body-swap-only, correctly), so there is
            #     nothing for it to recover and porting it would be
            #     inventing a pass, not achieving parity.
            #   * `_panel_rigid_disp` belongs to `_ride_layers_on_
            #     reference`, i.e. the LAYER-RIDE machinery -- a
            #     separate, separately-owed debt. Not this fix.
            #
            # Runs BEFORE the chain blend below, matching phase 2, which
            # also rigidifies before its chain/mixed-cloth restore. The
            # `skip_mask` is built the same way phase 2 builds it: any
            # vert carrying weight from a bone the ACTOR SKELETON cannot
            # resolve is SMP chain cloth and must not be rigidified.
            if PANEL_RIGIDITY > 0 and snapped is not None:
                _skip_p1 = None
                try:
                    _skip_p1 = simulated_vert_mask(s, MIXED_CLOTH_CHAIN_EPS)
                except Exception:
                    _skip_p1 = None
                # Its OWN try: the block below is inside the big phase-1
                # fit try whose handler sets `snapped = None`, so an
                # escape here would discard the warp, inflate AND conform
                # for this shape and read as the fit being switched off.
                try:
                    # #panel-rigid-early-clearance: same panels, same
                    # blend, but the strength is solved against the body
                    # so this pass never hands the anti-poke a mess to
                    # clean up. Falls back to the blind form whenever the
                    # body is unavailable, so the OFF path and the
                    # no-body path stay byte-identical.
                    if (PANEL_RIGID_EARLY_CLEAR
                            and body_verts_for_fit is not None
                            and body_normals_for_fit is not None):
                        _pv_p1, _npan_p1, _wd_p1 = (
                            _rigidify_within_clearance(
                                sv_world, snapped,
                                np.asarray(s.tris, dtype=np.int64),
                                body_verts_for_fit,
                                body_normals_for_fit,
                                PANEL_RIGIDITY, skip_mask=_skip_p1,
                                min_verts=PANEL_RIGIDITY_MIN_VERTS))
                    else:
                        _pv_p1, _npan_p1, _wd_p1 = _partial_rigid_panels(
                            sv_world, snapped,
                            np.asarray(s.tris, dtype=np.int64),
                            PANEL_RIGIDITY, skip_mask=_skip_p1,
                            min_verts=PANEL_RIGIDITY_MIN_VERTS)
                    if _npan_p1:
                        snapped = _pv_p1
                        _stage_p1('panel_rigidity', snapped)
                        print(f"    [panel-rigidity] {s.name}: "
                              f"{_npan_p1} panel(s) re-rigidified at "
                              f"{PANEL_RIGIDITY:.2f} (worst deformation "
                              f"was {_wd_p1:.3f}u)")
                except Exception as _pe_p1:
                    _note_pass_failure("panel-rigidity/phase1", _pe_p1)
            # --- #phase1-antipoke: the copy path's MISSING body repair -------
            #
            # BUG-02, sized. The copy path has no pass that pushes a vert OUT
            # of the body in its normal branch: `snap_armor_outside_body` is
            # the ELSE of "a CBBE base body exists", so every BodySlide output
            # takes the other arm; `clear_armor_outside_body` and
            # `_inflate_cloth_over_bust_butt` are body-swap only. Phase 2 runs
            # the anti-poke right here, after panel rigidity, and phase 1 runs
            # nothing.
            #
            # Measured on the piece an in-game report named (the reported trousers,
            # copy path, no physics XML): the AUTHOR's own pants are 0 of 2594
            # butt-band verts inside their CBBE body; ours are 1535 of 2589
            # inside the UBE body (59.3%, worst 0.929u). 687 of those sit at
            # 0.2-0.6u -- the exact window a body repair closes. Pack census:
            # butt bind-pose penetration is 75% of copy pieces vs 32% of swap.
            #
            # SAME pass, SAME position, SAME body the rest of this chain already
            # uses (`body_verts_for_fit`) -- parity, not a new pass. The
            # function is push-out only ("never pulls cloth in"), so it cannot
            # create the opposite defect, and it carries the rear/thigh standoff
            # terms the butt needs.
            #
            # OPT-IN, DEFAULT OFF. It is a geometry change on ~78% of the pack
            # and F010 is the standing caution: a copy-path repair that looked
            # like a uniform clipping win still could not ship, because it
            # inflated standoff on 9 of 9 pieces and stranded zero-weight bones.
            # Judge this one on clip AND standoff AND the bone gate, then in
            # game -- never on clipping alone.
            if (PHASE1_ANTIPOKE and snapped is not None
                    and body_verts_for_fit is not None
                    and body_normals_for_fit is not None):
                # Its OWN try, like the panel-rigidity block above: the
                # enclosing handler sets `snapped = None`, so an escape here
                # would discard the whole phase-1 fit for this shape and read
                # as the fit having been switched off.
                try:
                    # SMP OVERSHOOT CAP, ported from phase 2. A collision-only
                    # SMP shape pushed by the full 3.0u default "spreads verts
                    # on the convex bust and opens new gaps"
                    # (#smp-collision-only-antipoke), so phase 2 clamps it to
                    # the clearance target. Phase 2 also requires its local
                    # `_smp_relax`, which does not exist here; this arms the cap
                    # on the shape test alone, which is the CONSERVATIVE
                    # direction -- capping the push on a shape that did not need
                    # capping costs clearance, overshooting one that did opens
                    # gaps.
                    _ap_kw_p1 = {}
                    try:
                        if (s.name in hdt_collider_names
                                or _shape_has_hdt_smp_rigging(
                                    s, set(ube_base_for_reskin.bone_names or [])
                                    if ube_base_for_reskin is not None else set())):
                            _ap_kw_p1["max_push"] = SMP_ANTIPOKE_MAX_PUSH
                    except Exception:
                        _ap_kw_p1 = {}          # never fail the fit over a gate
                    _ap_p1 = clear_armor_outside_body(
                        np.asarray(snapped, dtype=np.float64),
                        body_verts_for_fit, body_normals_for_fit,
                        tris=(np.asarray(s.tris, dtype=np.int64)
                              if (ANTIPOKE_SMOOTH_ENABLED or CLEARANCE_FIELD_SOLVE)
                              else None),
                        **_ap_kw_p1,
                    )
                    # MIXED-CLOTH RESTORE, ported from phase 2. Pushing a
                    # SIMULATED vert out of the body fights the sim: HDT-SMP
                    # decides where that vert goes at runtime, so a pushed rest
                    # position is simply wrong. `_skip_p1` is the same
                    # chain-weight mask the panel rigidity above uses
                    # (#mixed-cloth-clearance), so the clearance lands on the
                    # non-simulated part of a mixed shape and the chain part is
                    # put back exactly as it was.
                    _pre_ap_p1 = np.asarray(snapped, dtype=np.float64)
                    _ap_p1 = np.asarray(_ap_p1, dtype=np.float64)
                    if (_skip_p1 is not None
                            and len(_ap_p1) == len(_skip_p1) == len(_pre_ap_p1)):
                        _nsim_p1 = int(_skip_p1.sum())
                        if _nsim_p1:
                            _ap_p1[_skip_p1] = _pre_ap_p1[_skip_p1]
                            print(f"    [mixed-cloth] {s.name}: clearance on "
                                  f"{int((~_skip_p1).sum())} non-simulated "
                                  f"vert(s), {_nsim_p1} simulated vert(s) "
                                  f"restored")
                    _moved_p1 = int((np.linalg.norm(
                        _ap_p1 - _pre_ap_p1, axis=1) > 1e-4).sum())
                    snapped = _ap_p1
                    _stage_p1('antipoke', snapped)
                    if _moved_p1:
                        print(f"    [phase1-antipoke] {s.name}: cleared "
                              f"{_moved_p1} vert(s) out of the body")
                    # #panel-rigidity, SECOND half -- and it rides this flag
                    # deliberately. Phase 2 pairs these: the anti-poke
                    # re-deforms every panel it pushes, and this recovers the
                    # rest where there is clearance for it. Porting the push
                    # WITHOUT the recovery would ship the damage and not the
                    # repair -- a softened plate is exactly the defect
                    # `#panel-rigidity` exists to prevent, and it is what
                    # `test_the_second_half_is_absent_for_a_REASON_THAT_IS_TRUE`
                    # is guarding when it says "wire it in as well, or rewrite
                    # the reason". Wired in.
                    if PANEL_RIGIDITY > 0 and snapped is not None:
                        try:
                            _rv1, _rn1, _rs1 = _rigidify_within_clearance(
                                sv_world, snapped,
                                np.asarray(s.tris, dtype=np.int64),
                                body_verts_for_fit, body_normals_for_fit,
                                PANEL_RIGIDITY, skip_mask=_skip_p1,
                                min_verts=PANEL_RIGIDITY_MIN_VERTS)
                            if _rn1:
                                snapped = _rv1
                                _stage_p1('panel_rigidity_post', snapped)
                                print(f"    [panel-rigidity] {s.name}: {_rn1} "
                                      f"panel(s) re-rigidified AFTER anti-poke, "
                                      f"mean strength {_rs1:.2f} of "
                                      f"{PANEL_RIGIDITY:.2f}")
                        except Exception as _pe2_p1:
                            _note_pass_failure("panel-rigidity/post/phase1",
                                               _pe2_p1)
                except Exception as _ap_e:
                    # Voiced, not swallowed: a silent failure here is
                    # indistinguishable from "nothing was inside the body",
                    # which is the state this flag exists to change.
                    _note_pass_failure("phase1-antipoke", _ap_e)
            # Keep chain-bone cloth (skirt/belt/cape) at SOURCE position so
            # it stays aligned with its chain bones; warping it onto UBE
            # while bones stay at source breaks the SMP rest pose.
            # Per-vertex (chain-weight fraction) so hybrid shapes still work.
            if snapped is not None:
                # source_verts must match snapped's frame (world).
                snapped = _physics_chain_nowarp_blend(s, sv_world, snapped)
                _stage_p1('chain_blend', snapped)
        except Exception as e:
            # The whole warp/inflate/conform chain for this shape is
            # discarded; the shape ships unfitted. Phase 2 records the
            # same event per stage, so record it here too.
            failed.append((f"{s.name}:fit-chain", repr(e)))
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
                # SMP colliders keep authored skin -- see the phase-2 site.
                # #smp-collider-graft
                if (ADD_SCALE_BONES_TO_CLOTH
                        and s.name not in hdt_collider_names
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
            except Exception as e:
                failed.append((f"{s.name}:reskin-compute", repr(e)))
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

        # Flush the copy-path trace for THIS shape. Without this the
        # checkpoints accumulate and nothing is ever written, which
        # reads exactly like "no pass moved anything" -- the failure
        # mode both tools exist to prevent. Same reporting contract as
        # phase 2: a flush error is RECORDED, and an EMPTY dump is
        # called out rather than passing as a clean run.
        _final_p1 = (np.asarray(snapped, dtype=np.float64)
                     if snapped is not None else sv_world)
        if _surv_p1 is not None:
            try:
                _surv_p1.flush(dst_path, s.name, _final_p1)
            except Exception as e:
                failed.append((f"{s.name}:survival", repr(e)))
            finally:
                _surv_p1.release()
        if _dump_p1 is not None:
            try:
                if not _dump_p1.flush(dst_path, s.name, s.tris,
                                      _final_p1):
                    failed.append((f"{s.name}:stagedump",
                                   "wrote nothing"))
            except Exception as e:
                failed.append((f"{s.name}:stagedump", repr(e)))
            finally:
                _dump_p1.release()


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
    variant_sources: "dict[str, str] | None" = None,
) -> ConvertResult:
    """Convert one CBBE armor NIF to a UBE-targeted NIF.

    `variant_sources`: this stem's OTHER weight variants (`"_0"` / `"_1"` /
    `""` -> resolved source path), resolved by the caller through the same mod
    list / VFS / BSA chain that found `src_path`. It decides which variant owns
    the shared `.tri` and whether this one may point at it -- see
    `_tri_is_owning_variant`. None (a single-file convert) falls back to
    probing for a sibling next to `src_path`. #tri-variant-collision

    `alt_texture_shape_names`: shape names an ESP alt-texture set targets by
    name (collected from the source mod's ARMO MO2S/MO3S entries). These are
    protected from the morph-cap merge so color variants keep working.

    Default behavior (the COPY path, no inline body shape): a body-aware
    rebuild. Every shape is warped by the CBBE->UBE body delta, inflated,
    conformed and anti-poked against the UBE body, then written with the
    shared skin/physics tail. A verbatim file copy happens only as the
    fallback when no body reference can be found.

    If inline body shapes ARE present AND `ube_body_ref_path` is provided,
    this dispatches to `convert_nif_phase2` (the BODY-SWAP path): deep-copy
    non-body shapes from source + inject BaseShape / VirtualBody from the UBE
    reference NIF.

    `warp_armor=True` selects an EXPERIMENTAL branch that is never used in
    production (it loses on every measured piece; kept for diagnostics). It
    is not "the warp" -- the production copy path warps too.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    # Per-piece pass-failure log. THE one entry point, so phase 2 (reached
    # through this function) accumulates into the same list.
    _begin_piece_pass_log()
    # Bound the HDT-XML memo to ONE armor. Its entries are also mtime-keyed, but a
    # per-armor clear means a stale collider set can never survive into a different
    # piece even if both bounds were somehow wrong at once. See
    # `_read_source_hdt_xml_text`.
    _hdt_xml_cache_clear()

    nif = nif_io.load_nif(src_path)
    # #xml-source-of-truth. Bind the piece's physics XML from the SOURCE now,
    # while the only copy that exists is one no write can race. Every later
    # lookup goes through `dst_path`, whose pointer conversion has rewritten to
    # `Meshes\!UBE\...`, and resolving that depends on this run's own write
    # order. Losing that race used to fail OPEN and free-fall the armour
    # (BUG-00). Must stay AFTER the load so the already-parsed nif is reused,
    # and BEFORE any pass that reads a collider/soft-body set.
    _hdt_xml_bind_piece_source(src_path, nif=nif)
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
                # Inject the UBE BaseShape only when this armor HIDES/PROVIDES the
                # body the viewer sees: a slot-32 body piece (or a slotless one),
                # OR a FIRST-PERSON body viewmodel (the 1st-person view renders
                # ONLY 1st-person meshes -- dropping its body = invisible arms).
                # A plain non-body slot (boots 37, panties/underwear, a cloak, a
                # waist) does NOT hide slot 32, so the actor's own nude UBE body
                # already renders; injecting a second body there just z-fights.
                # For those we DROP the inline CBBE body (it's in body_names) and
                # inject nothing. Matches the exposed-skin gate above. #3ba-ref-body
                inject_baseshape=((not biped_slots)
                                  or bool(biped_slots & _BODY_SLOT_BIT)
                                  or _is_first_person_mesh(src_path, nif)),
                alt_texture_shape_names=alt_texture_shape_names,
                extra_body_drop_names=tuple(exposed_skin_names),
                variant_sources=variant_sources,
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
        weight_suf = weight_suffix_of(src_path)
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

            # #phase1-nipple-map: the same nipple-ramped bust clearance the
            # body-swap conform gets, computed ONCE per piece. Empty kwargs
            # when the flag is off, so the call sites are byte-for-byte the
            # old ones at defaults.
            _nip_kw: dict = {}
            if PHASE1_NIPPLE_MAP and ube_base_for_reskin is not None:
                try:
                    _nip_p1 = _body_nipple_weight(ube_base_for_reskin)
                    if _nip_p1 is not None:
                        _nip_kw = {"ube_body_nipple": _nip_p1,
                                   "conform_margin": CONFORM_MARGIN}
                except Exception as _e:
                    _note_pass_failure("phase1-nipple-map", _e, dst_path)

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
            _fit_shapes_copy(_types.SimpleNamespace(
                _nip_kw=_nip_kw,
                biped_slots=biped_slots,
                body_delta_for_warp=body_delta_for_warp,
                body_normals_for_fit=body_normals_for_fit,
                body_verts_for_fit=body_verts_for_fit,
                cbbe_body_path_p1=cbbe_body_path_p1,
                cbbe_verts_for_warp=cbbe_verts_for_warp,
                dst_path=dst_path,
                extremity_slots_to_replace=extremity_slots_to_replace,
                failed=failed,
                hdt_collider_names=hdt_collider_names,
                hdt_softbody_names=hdt_softbody_names,
                layered_cloth_names=layered_cloth_names,
                shape_jobs_p1=shape_jobs_p1,
                src_nif_for_fit=src_nif_for_fit,
                ube_base_for_reskin=ube_base_for_reskin,
            ))

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
                except Exception as e:
                    # Best-effort, but a raise still has to be visible.
                    failed.append(("zfight-fix", repr(e)))

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
                except Exception as _pe:
                    _note_pass_failure(
                        "_separate_abdomen_layered_cloth_depth/phase1", _pe)

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
                except Exception as _pe:
                    _note_pass_failure("_sync_layered_cloth_weights", _pe)

            # Cross-plate seam weld: close gaps where adjacent solid plates
            # that share a seam drifted apart under independent warp. Runs
            # BEFORE the glow ride so the glow rides the welded plate.
            # Physics shapes are excluded (small inner try: a scoping error
            # only empties the exclusion, it must never kill the weld).
            if shape_jobs_p1:
                try:
                    try:
                        _weld_excl = (set(hdt_collider_names)
                                      | set(hdt_softbody_names)
                                      | set(layered_cloth_names))
                    except Exception:
                        _weld_excl = set()
                    n_weld = _weld_cross_shape_seams(shape_jobs_p1,
                                                     exclude_names=_weld_excl)
                    if n_weld:
                        import sys as _sys
                        print(f"  seam weld: closed {n_weld} cross-plate seam "
                              f"vert(s)", file=_sys.stderr)
                except Exception as _pe:
                    _note_pass_failure("_weld_cross_shape_seams", _pe)

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
                except Exception as _pe:
                    _note_pass_failure(
                        "_ride_effect_overlays_on_plate/phase1", _pe)

            # PELVIS RE-ANCHOR (copy/fit path): a NIF-root-hung skirt (a custom-race
            # armor) takes this path, not phase-2, so recreate its custom bone chains up
            # front -- lifting the root-parented garment bones onto Pelvis -- so the
            # shape copy's add_bone reuses those re-anchored nodes. Gated on the
            # pattern actually being present, so all other armors are byte-unchanged.
            if _has_nif_root_garment_chain(src_nif_for_fit):
                try:
                    _pc_bones: set = set()
                    for _ps in src_nif_for_fit.shapes:
                        _pc_bones |= set(_ps.bone_names or [])
                    # sorted(): _pc_bones is a set, and this list is the bone
                    # NODE CREATION order in the output -- set order follows
                    # the hash seed and made the written file nondeterministic.
                    _precreate_custom_bone_chains(
                        dst_nif_for_fit, src_nif_for_fit, sorted(_pc_bones))
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
                except Exception as _e1:
                    try:
                        _copy_shape(s, dst_nif_for_fit)
                    except Exception as _e2:
                        # BOTH copies failed -> the shape is absent from the
                        # output = an invisible piece in-game. Record it (tagged
                        # DROPPED) instead of swallowing, so the run reports a
                        # partial conversion rather than a clean success.
                        failed.append((s.name, f"DROPPED (copy failed): {_e2!r} "
                                               f"(fitted copy: {_e1!r})"))
                    else:
                        # The fitted copy failed but the plain copy succeeded:
                        # the shape ships with its SOURCE verts and skin,
                        # unfitted, under a "converted" status. Record it so
                        # the run does not read as a clean conversion. (If the
                        # first call raised after creating the shape, the
                        # retry may have created a twin of the same name --
                        # the write-time validator is where that surfaces.)
                        failed.append((s.name, "UNFITTED (fitted copy failed, "
                                               f"shipped source verts+skin): {_e1!r}"))
            # Inject UBE Hands/Feet to replace the CBBE-topology body-skin shapes.
            # Safe: slot 33/37 hides the actor's nude hands/feet; no z-fight.
            if extremity_slots_to_replace:
                weight_suf_for_inj = weight_suffix_of(src_path)
                inject_log: list[str] = []
                # sorted(): injection order = shape order in the written NIF;
                # bare set iteration follows the hash seed.
                for slot_label in sorted(set(extremity_slots_to_replace)):
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
                except Exception as e:
                    # A raise here keeps a source XML whose chain bones the
                    # conversion may have stripped: the cloth goes dead.
                    failed.append(("hdt-chain-regen", repr(e)))
            # No source HDT XML: generate a minimal one. Returns None only if the
            # NIF has no cloth carriers. Slot-49 cloth gets a cloth-only XML that
            # collides with the actor body's "body" tag at runtime.
            if hdt_xml is None:
                hdt_xml = _generate_hdt_xml_for_dst(dst_path, only_loose=True)

        # Figure out armor-specific TRI path (same logic as phase 2).
        armor_relpath = armor_relpath_under_meshes(src_path)
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
                # #tri-variant-collision: a low-poly variant sharing a
                # stem with the worn pair derives the SAME `.tri`, whose
                # offsets address vertices it does not have. Pointing at
                # it is worse than having no morphs.
                if bodytri_path and _tri_fits_variant(src_path, variant_sources):
                    from pyn.pynifly import NiStringExtraData  # type: ignore
                    # Single-carrier BODYTRI matching hand-authored
                    # UBE convention. See `_pick_bodytri_carriers`.
                    carriers = _pick_bodytri_carriers(
                        nf, exclude_body=BODYTRI_CARRIER_CLOTH,
                        all_cloth=(BODYTRI_ALL_SHAPES or BODYTRI_CARRIER_CLOTH))
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
        # #tri-write-once, PHASE 1 TOO. Gating only phase 2 fixed nothing for
        # boots, gloves and other armour-only pieces -- they route through HERE,
        # and they were most of what lost a TRI to the race. Caught by putting a
        # boot pair through the DEPLOYED exe and finding it had shipped the `_0`
        # TRI. Note this suppresses only the WRITE: `bodytri_path` above is still
        # derived, so the `_0` mesh keeps pointing at the shared TRI. Dropping
        # that reference would cost it body morphs outright -- worse than the
        # race being fixed.
        if (auto_tri_dst_phase1 is not None and _TRI_WRITE_ONCE
                and not _tri_is_owning_variant(src_path, variant_sources)):
            auto_tri_dst_phase1 = None
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
                        (armor_shape_verts, body_in_dst,
                         armor_vert_ef) = _collect_tri_inputs(
                            dst_check, len(body_verts_arr))
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
                            # #pair-tri-names: this ONE tri serves both halves
                            # of the weight pair, so a shape the partner names
                            # differently needs its table under that name too.
                            also_named=pair_alias_map(src_path,
                                                      variant_sources),
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
        except Exception as _pe:
            # It does not break the conversion, but an unhidden VirtualBody
            # renders as a SECOND body in game -- a visible defect, not a
            # nothing. Runs in a worker, so it must ride home in `reason`.
            _note_pass_failure("virtualbody-hide", _pe)

        # Multi-partition collapse — see _normalize_partitions_on_disk.
        _finalize_physics_and_motion_match(dst_path, src_path, biped_slots)
        # Author-relative roughness cap (#author-roughness-cap). BEFORE the
        # coincident match on purpose: this one smooths a shape's INTERIOR,
        # that one settles shape BOUNDARIES and is in-game confirmed, so it
        # keeps the last word where the two populations meet.
        try:
            n_rc = _cap_weight_roughness_to_author(dst_path,
                                                   src_nif_path=src_path)
            if n_rc:
                import sys as _sys
                print(f"  roughness cap: smoothed {n_rc} vert(s) rougher than "
                      f"the author", file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_cap_weight_roughness_to_author", _pe)
        # Hold a reskinned layer to the author where it meets an SMP layer that
        # KEPT the author's rig (#smp-boundary-weight-hold). After the body
        # matches, for the same reason the coincident match is: each of them
        # pairs to the body per shape and would re-diverge this.
        try:
            n_sb = _hold_weights_at_smp_boundary(dst_path,
                                                 src_nif_path=src_path)
            if n_sb:
                import sys as _sys
                print(f"  smp-boundary hold: held {n_sb} vert(s) toward the "
                      f"author", file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_hold_weights_at_smp_boundary", _pe)
        # Coincident-vertex skin unification (#coincident-skin-match). Same
        # placement rule as the phase-2 site: after every weight pass, because
        # each of them pairs to the body PER SHAPE and would re-diverge an
        # earlier repair.
        try:
            n_cs = _match_coincident_cross_shape_skin(dst_path,
                                                      src_nif_path=src_path)
            if n_cs:
                import sys as _sys
                print(f"  coincident skin: unified {n_cs} cross-shape vert(s)",
                      file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_match_coincident_cross_shape_skin", _pe)

        # Verbatim-copied NIFs carry raw block structure the renderer can reject.
        # Re-author for a clean pynifly structure identical to body shapes.
        if not use_rebuild:
            try:
                _reauthor_nif_fresh(dst_path)
            except Exception as _pe:
                # Records like its phase-2 sibling: a silent death here ships a
                # raw-block NIF the renderer can reject, indistinguishable from
                # a clean copy.
                _note_pass_failure("_reauthor_nif_fresh/copy-path", _pe)

        # Static-chain conversion LAST (gated, idempotent). Do NOT re-run
        # _harden_hdt_xml_for_fsmp here — re-pruning copy-path XML caused
        # cuirass regressions; only the in-_finalize prune runs.
        if STATIC_CHAINS:
            try:
                _hp_stem = dst_path.stem
                for _hp_suf in ("_0", "_1"):
                    if _hp_stem.endswith(_hp_suf):
                        _hp_stem = _hp_stem[:-len(_hp_suf)]
                        break
                _make_chains_static(dst_path.parent / (_hp_stem + ".xml"))
            except Exception:
                pass

        # Same re-import gap as the phase-2 path: the TRI was written before
        # _finalize_hdt_physics restored the textureless proxies, so they carry
        # no morph table. Re-emit now the NIF is final. #tri-reimport-refresh
        _p1_body_verts = None
        if ube_body_ref_path is not None:
            try:
                _, _p1_body_verts, _ = _cached_ube_body_verts(
                    Path(ube_body_ref_path))
            except Exception:
                _p1_body_verts = None
        _refresh_armor_tri_after_reimport(
            dst_path, auto_tri_dst_phase1,
            fallback_body_verts=_p1_body_verts)

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

        # A pass that RAISED must reach the caller. The parent process cannot
        # see this worker's module state or its stderr, so `reason` is the only
        # channel -- see _PASS_FAILURES_THIS_PIECE. Effects ride the same channel
        # for the same reason, and are listed SEPARATELY so "what broke" and
        # "which change touched this" never blur (#change-attribution).
        reason_parts.extend(_piece_pass_failures())
        reason_parts.extend(_piece_pass_effects())

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
    close, frame rotation adds more error than it fixes.

    THAT CLAIM IS UNCITED. It used to read "validated in M3 measurements: see
    docs/M3_findings.md" — a file that was never written (`tests/test_m3_belt.py`
    is honest about it, saying "when written"). A cited safety net that is not
    there is worse than an uncited one: it stops the next reader looking for the
    real one, which is exactly the class the 2026-08-17 comment audit found six
    of. What DOES bear on surface-frame rotation is
    `scripts/analysis/normal_rotation.py` (how far the surface turned between a
    source mesh and its refit), but it does not pin THIS threshold — so treat
    the translation-only choice as untested rather than validated.
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

# (moved to nif_convert_fitgeom.py, 2026-09-01)


# A TUNING KNOB (numeric, so env-only is fine per the flag rule): how much
# headroom `conform` leaves above the authored drape when it reels a garment in.
#
# Both attempts to REPLACE inflate failed on REACH -- a floor touches 5.6% of
# verts and a conform margin ~16%, against inflate's 75%. This is the composition
# instead of the replacement: inflate keeps its reach AND conform stops reeling
# the result quite so tight. 0.0 is exactly today's behaviour.
CONFORM_MARGIN = _knob("CBBE2UBE_CONFORM_MARGIN", 0.0)


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
    src_armor_verts: "np.ndarray | None" = None,
    src_body_verts: "np.ndarray | None" = None,
    src_body_normals: "np.ndarray | None" = None,
    tris: "np.ndarray | None" = None,
    body_nipple: "np.ndarray | None" = None,
) -> np.ndarray:
    """Push body-hugging armor verts outward to avoid z-fighting with a
    morphed body.

    `body_nipple` is optional and only feeds `#authored-nipple-exempt`: without
    it the authored floor behaves exactly as it did, so a caller that cannot
    supply the map loses nothing else.

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
    push_len = per_vert_mag * falloff

    # #authored-inflate. Re-state the push as a FLOOR the vertex must reach
    # rather than an amount to add, so a garment already sitting at the author's
    # spacing stops being shoved further out. See the constant for the census
    # that motivates it and for why this is not the rejected `#unified-offset`
    # floor. Needs the author's own mesh AND their body; without either it
    # cannot know the authored standoff, so it leaves the additive behaviour
    # exactly as it was rather than guessing a floor.
    #
    # AND IT MUST ACTUALLY BE ABLE TO READ IT. `src_body_normals` reaching here
    # all-zero (this pack's source body: 18436 of 18436) made `authored` read 0
    # everywhere and collapsed the floor to its constant term -- a flat clearance
    # rule wearing the author's name, which is worse than not running. Refuse.
    if (AUTHORED_INFLATE and body_normals is not None
            and src_armor_verts is not None and src_body_verts is not None
            and src_body_normals is not None
            and _authored_normals_usable(src_body_normals)):
        try:
            sa = np.asarray(src_armor_verts, dtype=np.float64)
            sb = np.asarray(src_body_verts, dtype=np.float64)
            sn = np.asarray(src_body_normals, dtype=np.float64)
            if sa.shape == armor_verts.shape and sb.shape == sn.shape and len(sb):
                bn_at = np.asarray(body_normals, dtype=np.float64)[idxs]
                # Where the vertex sits NOW, signed along the body's normal.
                s_cur = np.einsum('ij,ij->i', armor_verts - body_verts[idxs],
                                  bn_at)
                # Where the AUTHOR put it, on THEIR body. Negative means they
                # tucked it under the surface; a floor must not honour that, so
                # it is clamped at zero.
                _, si = _authored_src_tree(sb).query(sa, k=1, workers=-1)
                # Feathered for the same reason every other clearance term is.
                # #authored-floor-feather
                authored = _feathered_authored(
                    np.maximum(np.einsum('ij,ij->i', sa - sb[si], sn[si]), 0.0),
                    tris, armor_verts)
                # Headroom by the SAME rule as the ramp this floor caps. The old
                # `ARMOR_TO_SKIN_BUFFER + min(amp, AMP_CAP)` was five times more
                # generous than the push, so on a breast it stood at 1.650u --
                # above our own 1.436u result and the author's 0.948u alike, and
                # `authored` could never win the maximum. See
                # `_authored_floor_amp_room`.
                amp_room = np.full(len(armor_verts), ARMOR_TO_SKIN_BUFFER)
                if (ADAPTIVE_CLEARANCE_ENABLED and morph_amplitude is not None
                        and len(morph_amplitude) > idxs.max()):
                    amp_room = _authored_floor_amp_room(
                        np.asarray(morph_amplitude, dtype=np.float64)[idxs])
                floor = np.maximum(authored, amp_room)
                # The additive result is the CEILING: this may reduce a push,
                # never raise one, so over-inflation cannot get worse.
                required = np.minimum(s_cur + push_len,
                                      np.maximum(s_cur, floor))
                _relaxed = np.maximum(required - s_cur, 0.0)
                # #authored-nipple-exempt: the tip keeps the full additive push.
                # Measured, this floor is what costs the nipple's WORST case --
                # tip clearance p05 0.466u -> 0.421u with it armed, while the
                # median barely moves; the anti-poke floor is the mirror image
                # (median 1.205 -> 1.107, tail intact). They have to be exempted
                # together or the tip loses one end of its distribution.
                _tip = _nipple_tip_mask(
                    armor_verts, body_verts, body_nipple,
                    frac=AUTHORED_NIPPLE_EXEMPT, radius=AUTHORED_NIPPLE_RADIUS,
                    tris=tris)
                # `push_len` is still the raw additive push at this point, so
                # `where` keeps it verbatim on the tip and takes the floored one
                # everywhere else.
                if os.environ.get("CBBE2UBE_NIPPLE_PROBE"):
                    print(f"    [nip-inf] verts={len(push_len)} "
                          f"tipw={0.0 if _tip is None else float(_tip.sum()):.1f} "
                          f"nipple_map={'no' if body_nipple is None else 'yes'}")
                push_len = (_relaxed if _tip is None
                            else np.where(_tip > 0.5, push_len, _relaxed))
        except Exception as e:
            # A silently-failed floor is indistinguishable from "the floor was
            # already satisfied", and would ship as a quiet loss of clearance.
            _note_pass_failure("inflate/authored-floor", e)

    # #clearance-field: solve ONE minimum-stretch displacement that meets every
    # vert's outward gain, instead of pushing each along its own (diverging) body
    # normal. Same solve as the final anti-poke; `push_len` (already the authored
    # -inflate-capped outward gain) is the constraint's lower bound. Own flag and
    # early return so the OFF path below stays byte-identical. `cap` doubles as
    # the "assert the pass fired" guard: nothing to inflate -> don't run.
    if CLEARANCE_FIELD_INFLATE and tris is not None:
        need = np.clip(push_len, 0.0, None)
        cap = float(need.max()) if need.size else 0.0
        if cap > 1e-9:
            u, _cf = _solve_clearance_field(
                armor_verts, directions_unit, need, tris, max_push=cap)
            if _cf.get("ok"):
                return (armor_verts + u).astype(np.float32)
    push = directions_unit * push_len[:, None]

    return (armor_verts + push).astype(np.float32)






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

# --- #tri-write-once ------------------------------------------------------
# See the call site in `convert_nif_phase2` for the measurements. Short form:
# both weight variants of an armour derive the SAME `.tri` path, so both write
# it -- a race, and a nondeterministic result, on every pair in the pack.
_TRI_WRITE_ONCE = not _flag("CBBE2UBE_TRI_BOTH_WEIGHTS", False)


# (moved to nif_convert_trigen.py, 2026-09-01)


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


# (moved to nif_convert_trigen.py, 2026-09-01)


# HISTORICAL: an earlier approach FORCED Shader_Type to 0 (Default) and cleared the
# Environment_Mapping bit (0x80), on the theory that Shader_Type=1 blocks NioOverride
# morphing. That forcing is gone -- `_copy_shape` now copies the source's shader VALUE
# fields verbatim (`_SHADER_VALUE_FIELDS`), Shader_Type included, so an armour keeps
# the look its author gave it. The two constants naming those magic numbers survived
# the behaviour by some margin and were unreferenced; removed 2026-07-27. Kept as a
# note because "why doesn't UBE force Shader_Type=0?" is a reasonable question to have
# about this file.


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


def _finalize_physics_and_motion_match(dst_path, src_path, biped_slots) -> None:
    """The tail both conversion paths share: partitions, the SMP physics
    finalize with its collider patches, then the body-motion weight matches.

    EXTRACTED FROM TWO VERBATIM COPIES, and the duplication had already cost a
    bug: the hand/foot gate on the physics finalize existed only on the phase-1
    copy, so a gauntlet with an authored HDT XML got cloth physics attached on
    the other path (parity audit 2026-07-28). One copy cannot drift from itself.

    ORDER IS LOAD-BEARING throughout and the reasons are inline: the bust split
    straddles the finalize, the collider patches read the XML it writes, and the
    weight matches run last-wins so each notes why it sits where it does.
    """
    _normalize_partitions_on_disk(dst_path, src_path)

    # Bust collider split, pass 1 (shape): BEFORE the physics finalize so the
    # hidden clone exists when the XML is validated/hardened against the NIF.
    # [DESIGN: bust collider split]
    if not (biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
        try:
            _split_bust_collider_shape(dst_path, src_path)
        except Exception as _pe:
            _note_pass_failure("_split_bust_collider_shape", _pe)

    # FINAL HDT-SMP physics pass — after every earlier round-trip so
    # extra-data survives them. NOT literally last: the passes below use
    # the in-place load+save pattern and preserve it — any NEW pass added
    # after this point must do the same (a rebuild drops it). Skip
    # hand/foot: cloth physics collapses them.
    if not (biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
        try:
            _finalize_hdt_physics(dst_path, src_path)
        except Exception as _pe:
            # A silent death here ships the piece with NO physics attached
            # while the run reports success -- the exact class the recording
            # convention exists for. Sibling call sites already record.
            _note_pass_failure("_finalize_hdt_physics", _pe)

    # Bust collider split, pass 2 (XML): AFTER the finalize (which overwrites
    # the XML with the authored copy) and BEFORE the jiggle graft (which reads
    # it to decide what is a collider). [DESIGN: bust collider split]
    if not (biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
        try:
            _split_bust_collider_xml(dst_path)
            # Grow the SMP collider onto the UBE body (#collider-shrinkwrap).
            # AFTER _finalize_hdt_physics, like the bust split: that pass
            # overwrites the XML and RE-IMPORTS the colliders, so anything
            # earlier is discarded.
            _conform_collider_to_body(dst_path)
            # Add the buttock collider the source never had
            # (#butt-collider-patch). LAST: it reads the XML the finalize
            # wrote and appends to it.
            _add_butt_collider_patch(dst_path)
            # Give the VISIBLE cloth a collision proxy
            # (#skirt-proxy-rebuild). After the butt patch: it clones a
            # FABRIC block from the XML that patch may just have extended.
            # Under #skirt-proxy-after-weights this moves below the weight
            # passes instead, because the source it picks is ranked on weights
            # those passes rewrite.
            if not SKIRT_PROXY_AFTER_WEIGHTS:
                _add_skirt_collider_proxy(dst_path)
        except Exception as _pe:
            _note_pass_failure("_split_bust_collider_xml", _pe)

    # Graft body jiggle onto fitted leg cloth that lacks its own, THEN conform.
    # ORDER MATTERS: the graft gives a no-jiggle pant the body's butt/belly
    # jiggle, which lets the conform pass (gated on jiggle) ALSO weight-match it
    # to the body -- fixing the knee-BEND clip, not just butt-jiggle follow.
    try:
        _transfer_body_jiggle_to_fitted(dst_path, biped_slots,
                                        src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_transfer_body_jiggle_to_fitted", _pe)
    # Fitted-cloth body conform (gated; skin-tight garments only). Runs after
    # finalize so it sees final skinning; reauthor/harden below preserve it.
    try:
        _conform_fitted_to_body(dst_path, src_path, biped_slots)
    except Exception as _pe:
        _note_pass_failure("_conform_fitted_to_body", _pe)
    # Knee-bend conform for RIGID leg plate (the conform above skips it): match
    # the plate's Thigh:Calf split to the body so it bends with the knee.
    try:
        _match_rigid_leg_bend_to_body(dst_path, biped_slots,
                                      src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_rigid_leg_bend_to_body", _pe)
    # Leg-MOTION match: the pass above only reaches reskin-eligible shapes, so a
    # source-skin garment still under-travels the leg under hip flexion (#leg-motion-match).
    try:
        _match_leg_motion_to_body(dst_path, biped_slots,
                                  src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_leg_motion_to_body", _pe)
    # Spine-MOTION match: the TORSO instance -- the garment carries its spine
    # mass on Spine1 where the body uses Spine2, so it under-travels every
    # spine rotation (#spine-follow). MUST run BEFORE the arm pass: the two
    # bands overlap, every family match rescales the bones it does not manage,
    # and whichever runs LAST wins those rows. Measured: spine last costs the
    # armhole 1.106 -> 1.076, while arm last costs the under-bust nothing,
    # because under-bust rows carry no arm weight and are skipped anyway.
    try:
        _match_spine_motion_to_body(dst_path, biped_slots,
                                  src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_spine_motion_to_body", _pe)
    # Arm-MOTION match: the same gap at the SHOULDER. CBBE ends UpperArm weight
    # at the armhole and UBE does not, so the shoulder walks out through a
    # source-skin garment that never got the bone (#armhole-arm-follow).
    try:
        _match_arm_motion_to_body(dst_path, biped_slots,
                                  src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_arm_motion_to_body", _pe)
    # Spine-TWIST match: the spine split at partial strength, on TRI-owned shapes
    # (#spine-twist-partial). Runs LAST -- the last pass wins the overlap.
    try:
        _match_spine_twist_to_body(dst_path, biped_slots,
                                   src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_spine_twist_to_body", _pe)
    # Full-vector weight match (#full-weight-match). Runs LAST: it manages
    # every shared bone, so anything after it would overwrite the match.
    try:
        _match_full_weights_to_body(dst_path, biped_slots,
                                    src_nif_path=src_path)
    except Exception as _pe:
        _note_pass_failure("_match_full_weights_to_body", _pe)

    # #skirt-proxy-after-weights: the same pass, ranked on the weights that
    # actually ship. See the flag for the measurement -- a 7% ranking margin
    # decided before four weight passes rewrite the ranked quantity is how a
    # skirt collider ended up built from a potion bottle's net.
    if SKIRT_PROXY_AFTER_WEIGHTS and not (
            biped_slots & (BIPED_SLOT33_BIT | BIPED_SLOT37_BIT)):
        try:
            _add_skirt_collider_proxy(dst_path)
        except Exception as _pe:
            _note_pass_failure("_add_skirt_collider_proxy/after-weights", _pe)

    # Put the shapes back in the order the AUTHOR wrote them (#authored-shape-order,
    # BUG-09). Must run after `_finalize_hdt_physics`, which is what re-appends the
    # dropped collision proxies -- anything earlier would be undone by it. Before
    # the audit below so that guard reads the bytes that actually ship.
    try:
        _restore_authored_shape_order(dst_path, src_path)
    except Exception as _pe:
        _note_pass_failure("_restore_authored_shape_order", _pe)

    # LAST, after every weight pass: the invariant that decides whether the
    # armour stays on the actor. #registered-shape-declared-bones
    _audit_registered_shape_declared_bones(dst_path, src_path)


# (moved to nif_convert_writer.py, 2026-09-01)


# (split step 9) _ColliderDeclined, _actor_can_resolve_bone, _actor_skeleton_bone_names, _actor_skeleton_bone_parents ... live in nif_convert_physics.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_physics import (  # noqa: E402
    _ColliderDeclined, _actor_can_resolve_bone, _actor_skeleton_bone_names,
    simulated_vert_mask,
    _actor_skeleton_bone_parents, _add_butt_collider_patch,
    _add_skirt_collider_proxy, _audit_registered_shape_declared_bones,
    _cluster_decimate, _finalize_hdt_physics, _find_hdt_xml_for_armor,
    _generate_hdt_xml_for_dst, _hdt_collider_shape_names,
    _hdt_softbody_shape_names, _hdt_xml_bind_piece_source,
    _hdt_xml_cache_clear, _lift_chain_roots_off_body, _mod_xml_index,
    _nearest_declared_ancestor, _norm_bone, _precreate_custom_bone_chains,
    _read_source_hdt_xml_disk, _read_source_hdt_xml_text,
    _read_source_hdt_xml_text_uncached, _seed_flat_chain_anchors,
    _select_framework_bone_carriers, _xml_referenced_bone_names,
    _source_hdt_needs_missing_chain_bones, _make_chains_static,
    _ensure_cloth_body_collider, _harden_hdt_xml_for_fsmp,
    _read_xml_roundtrip, _hdt_sanitise, _hdt_protect_all_or_none,
    _nif_declares_hdt_xml, _ube_conform_body_tree,
    _carrier_is_body_conforming,
)



# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


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
    shapes. Setting the NiAVObject Hidden bit (bit 0 of `flags`) is
    the workaround that DOES persist: Skyrim's renderer skips Hidden
    shapes outright; HDT-SMP reads geometry for collision
    registration independently of the render flags.

    This used to read "same C-level binding gap as
    `_strip_alpha_property`", and that was wrong twice over: the
    function was DELETED in 682284f, and it documented something
    else -- NioOverride's BodyMorph skipping shapes that carry a
    NiAlphaProperty, not a setter-persistence gap. A cross-reference
    that names a missing symbol AND misdescribes it is worse than
    none, because it reads as corroboration and cannot be checked.
    The claim above rests on the Hidden-bit workaround actually
    holding, which is what was verified.

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


# (moved to nif_convert_trigen.py, 2026-09-01)


# Skyrim BSLightingShaderProperty flag bits for per-vertex color / alpha.
# A shape that has these set in its shader MUST carry a vertex-color buffer;
# the engine reads color per-vertex while building the 3D model and crashes
# (access violation) if the buffer is absent.
SLSF2_VERTEX_COLORS = 0x20  # Shader_Flags_2 bit 5
SLSF1_VERTEX_ALPHA = 0x08   # Shader_Flags_1 bit 3


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


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
    except Exception as _re:
        # False here routes the shape toward GENERATED soft-body -- the branch
        # this gate exists to prevent ("the whole armour flops / disjoints").
        # An accessor drift (the get_weights-vs-bone_weights incident class)
        # must not take that branch silently (audit 2026-07-28).
        print(f"  WARN: rigid-torso gate could not read weights on "
              f"{getattr(shape, 'name', '?')!r} ({_re!r}) -- treating as "
              f"NOT rigid torso", file=sys.stderr)
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


def _cloth_candidate_shapes(nif) -> "list[object]":
    """EVERY cloth-eligible shape in the NIF (not just the single BODYTRI carrier).

    Mirrors the candidate filter inside `_pick_bodytri_carriers` -- textured,
    not a known non-carrier, not a rigid-prop name, not extremity-dominant --
    but returns them ALL. `_pick_bodytri_carriers` deliberately narrows to one
    shape because BODYTRI wants a single morph carrier; the HDT-XML generator
    needs the full set, because any of them may carry a physics chain that must
    be simulated (#shadowed-chain-skirt).
    """
    out: list = []
    for s in getattr(nif, "shapes", []) or []:
        if s.name in ("BaseShape", "3BA", "VirtualBody"):
            continue
        if not (s.textures or {}):
            continue
        if s.name in BODYTRI_CARRIER_EXCLUDE:
            continue
        nlow = s.name.lower()
        if any(kw in nlow for kw in NON_CLOTH_SHAPE_KEYWORDS):
            continue
        try:
            if _shape_is_extremity_dominant(s):
                continue
        except Exception:
            pass
        out.append(s)
    return out


# (moved to nif_convert_trigen.py, 2026-09-01)


# ---------- M6: per-vertex proximity-blend re-skin ----------------------
#
# Re-skin armor verts to UBE body bones so body morphs/animations propagate
# at runtime. Per-vertex blend keyed on distance to body surface:
#   dist < NEAR -> 100% body weights; NEAR..FAR -> linear blend;
#   dist >= FAR -> 100% original armor weights (rigid).
# Sidesteps the cloth/metal classification problem: pauldrons stay rigid;
# chest fabric hugging the body inherits body skinning automatically.

# (moved to nif_convert_weights.py, 2026-09-01)
# (moved to nif_convert_weights.py, 2026-09-01)
# (moved to nif_convert_weights.py, 2026-09-01)

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
    not _flag("CBBE2UBE_RESKIN_KEEP", False)
)

# Opt-in (default OFF): graft animation scale bones onto a morph-TRI shape's source
# skin instead of fully excluding it from the reskin. OFF restores the proven
# exemption -- a morph-TRI shape keeps its untouched source skin so its BodySlide
# body-slider TRI stays in sync (grafting desynced it -> leg armor stopped inflating
# with a morphed body -> thigh-coverage loss). See [DESIGN: Morph-TRI reskin].
_MORPHTRI_SCALE = (
    _flag("CBBE2UBE_MORPHTRI_SCALE", False)
)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


# Wider conformance band for body-fitted armor (slot 32 + leg slots).
# A thicker shell fully adopts body bone weights so it bends with the body
# during animation. Slot-49 flowing cloth keeps the narrow default via
# _slot_aware_reskin_band — over-conforming a skirt makes it cling, not drape.
RESKIN_NEAR_DIST_BODYFIT = 1.2
RESKIN_FAR_DIST_BODYFIT = 3.5

# #reskin-wide -- a WIDER conformance band for body-fitted slots.
#
# DEFORMATION MATCHING BEATS CLEARANCE, measured. Replacing a garment's weights with
# the body's outright (an upper bound, not shippable) takes pose-induced exposure from
# 83.7% -> 5.4% at the thigh, 11.0% -> 0.1% at the breast, 14.7% -> 2.8% at the butt.
# The best a clearance push managed on the same cases was 3.5% / 8.0%. And it costs NO
# volume: the bind shape is untouched, only how the garment MOVES, so it cannot
# produce the baggy look a uniform push is rejected for.
#
# The band decides how much of that the converter captures, and today's is too tight.
# Swept on real armour (pose-induced exposure, worst pose per region):
#
#   band        thigh   butt   breast
#   0.5/2.0     83.7%  14.1%    3.2%    <- non-body-fitted default
#   1.2/3.5      ~     ~14%     ~3%     <- current body-fitted
#   2.0/6.0     66.2%   7.5%    0.1%
#   4.0/10.0    17.0%   4.6%    0.1%
#   full         5.4%   2.8%    0.1%
#
# SAFE ONLY BECAUSE THE RESKIN IS ALREADY GATED: both call sites run only when
# `not _shape_has_hdt_smp_rigging(...)`, so chain-driven cloth never reaches it.
# That gate is load-bearing here -- widening the band on a skirt would replace its
# `Skirt N_NN` weights with body weights and the skirt would stop swinging. The armour
# needing the WIDEST band (hanging skirts, thigh coverage) is exactly the chain-driven
# kind this must not touch, so the win is the rigid part of the pack: 204 of 314
# armours measured fully rigid.
#
# THE WIDE-BAND TOGGLE IS DELETED. It was never reachable from a real run, and a
# wider band makes a RIGID plate deform further with the body -- a metal cuirass
# starts bending like skin. If it is ever rebuilt, gate it on the rigid 204 of 314
# measured above and judge the golden set for SHAPE change, not clip counts.

# Body-fitted biped slots: body(32), forearms(34), calves(38), legs(53-58).
# Hands/feet/gauntlets/boots take _shape_has_fine_animation_bones path instead.
_RESKIN_BODY_FITTED_BITS = (
    (1 << (32 - 30)) | (1 << (34 - 30)) | (1 << (38 - 30))
    | (1 << (53 - 30)) | (1 << (54 - 30)) | (1 << (55 - 30))
    | (1 << (56 - 30)) | (1 << (57 - 30)) | (1 << (58 - 30))
)


# (moved to nif_convert_weights.py, 2026-09-01)


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


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# Rigid LEG skeleton bones (the actual animation bones, NOT the frontthigh/
# rearthigh/rearcalf SCALE bones, which also contain "thigh"/"calf"). A shape
# whose verts are MAJORITY-dominated by these is leg-encasing armor.
LEG_RIGID_BONE_KEYWORDS = ("thigh", "calf", "foot", "toe")


# (moved to nif_convert_weights.py, 2026-09-01)


# Upper-body anchor bones: a chain hanging off these (cape off spine, etc.)
# needs a NESTED skeleton tree so FSMP tracks torso/limb motion through it.
# Chains anchored on the lower body (pelvis/thigh) work FLAT (actor-driven).
_UPPER_BODY_ANCHOR_KEYWORDS = ("spine", "neck", "head", "clavicle", "shoulder")

# #seed-spine-anchors -- DEFAULT ON since 2026-08-17.
# Kill switch: `CBBE2UBE_NO_SEED_SPINE_ANCHORS=1`.
#
# SPINE is the one keyword above the arm argument does not cover. That argument
# is explicitly about distance from the actor root: "an arm swings far from the
# root -- so a flat-anchored sleeve holds a fixed pose in actor space". A spine
# bone sits ON the torso axis and moves with the root much as the PELVIS does,
# and pelvis flat-seeding is CONFIRMED GOOD in game (`#per-anchor-seed`, the
# iron collapse). Clavicle and shoulder DO ride the arm and stay excluded, as do
# neck and head.
#
# The defect it addresses, measured on the shipped pack after the 2026-08-15
# reconvert -- chains whose GLOBAL is >5u from source, by the anchor they hang
# from:
#
#     NPC Spine2   196 chains across 13 pieces
#     NPC Spine1   104 chains across  7 pieces
#     NPC Pelvis    29 chains across  3 pieces   (a SEPARATE anomaly: these
#                                                 should already be seeded)
#     Scene Root     8 chains across  7 pieces   (not a real anchor)
#
# So spine alone is 300 chains across 20 of the 28 affected pieces. Reported in
# game as a fur chest piece collapsing on the breast: its `FurL`/`FurR` chains
# hang from `NPC Spine2` and land 89.78u low -- source z 101.67, shipped z 12.00
# -- while every pelvis-anchored chain on the same mesh is correct to 0.9u.
#
# CONFIRMED IN GAME 2026-08-16 on the bandit armour ("the bandit armor does work
# that is correct"), which is what moved this from opt-in to default. Re-censused
# on the shipped pack 2026-08-17 (1-in-3 sample, 892 pieces carrying chains):
# 18 pieces still ship chains >5u off source, worst 92.60u, and 16 of those 18 --
# 231 of the 238 displaced chains -- hang from a SPINE anchor. The bandit meshes
# themselves currently carry a hand-deployed fix (0.90u), so leaving this OFF
# through a reconvert would have REGRESSED an already-approved piece.
#
# It moves NODES only: measured geometry delta off-vs-on is 0.000000u on every
# piece tested, so it cannot introduce a clip/fold/stretch regression.
#
# STILL EXCLUDED, deliberately: arm, clavicle, shoulder, neck, head. Arm seeding
# is proven BAD in game (sleeves "disconnected from the body, bound in a pose"),
# and clavicle/shoulder ride the arm. The residual pelvis/Scene-Root anomaly
# (2 pieces at 68.91u in the same census) is a DIFFERENT defect this does not
# touch -- those anchors should already seed and do not.
SEED_SPINE_ANCHORS = not _flag("CBBE2UBE_NO_SEED_SPINE_ANCHORS", False)


def _is_spine_anchor(bone_name: str) -> bool:
    """Torso-axis anchor: moves with the root like the pelvis, unlike an arm.
    Deliberately NOT clavicle/shoulder -- those ride the arm."""
    return "spine" in (bone_name or "").lower()


# Arm anchors are handled SEPARATELY from the list above, on purpose.
#
# Flat mode re-parents an anchor to Scene Root at its source GLOBAL position.
# That is fine for a skirt, whose anchor sits near the actor root and barely
# moves relative to it, but an arm swings far from the root -- so a flat-anchored
# sleeve holds a fixed pose in actor space instead of following the arm.
# Observed in game as sleeves "disconnected from the body, bound in a pose".
# The source rig parents its sleeve chains to `NPC L/R Forearm`.
#
# Kept out of _UPPER_BODY_ANCHOR_KEYWORDS because nesting is decided per FILE.
# Measured over the pack, 5 rigs carry an arm anchor and 3 of them are MIXED --
# a skirt on `NPC Pelvis` plus a sleeve on `NPC L UpperArm` in one NIF. Putting
# the arm keywords in the list above would flip those files whole, dragging
# their pelvis-anchored chains into nested mode, which is precisely what made
# working skirts sag in June. The gate below therefore requires EVERY anchor in
# the file to be an arm. The 3 mixed rigs keep flat behaviour unchanged; their
# sleeves stay as they are today, which is pre-existing rather than a new
# regression, and is the safer half of a trade we cannot settle without an
# in-game report.
#
# Per-ANCHOR nesting was tried first and does not work: the chain bones are
# parented during skin install, before the anchor-creation loop runs, so
# switching mode there left `LArmA 01` on Scene Root anyway. Verified.
_ARM_ANCHOR_KEYWORDS = ("upperarm", "forearm")


def _is_arm_anchor(bone_name: str) -> bool:
    low = (bone_name or "").lower()
    return any(k in low for k in _ARM_ANCHOR_KEYWORDS)


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
    _flag("CBBE2UBE_NO_SOFTBODY_SCALES", False)
)

# Inject UBE Hands/Feet into gauntlets/boots, replacing CBBE-topology extremity shapes.
# Without this, the source body-skin `Hands` shape is dropped with no replacement
# and the bare hand is invisible under the gauntlet. No-ops gracefully if the
# tangent mesh is absent.
INJECT_UBE_EXTREMITY_REPLACEMENT = True

# (moved to nif_convert_weights.py, 2026-09-01)

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

# (moved to nif_convert_weights.py, 2026-09-01)
# (moved to nif_convert_weights.py, 2026-09-01)

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


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# Tall calf/foot boots (slot 37) whose shaft rides the Thigh bone get the body's
# far-thigh scale bones (Front/RearThigh) grafted onto the shaft by the fine-anim
# reskin, which makes the whole boot FADE OUT at camera distance on a UBE actor.
# Exclude the far-thigh scale bones from calf/foot-dominant footwear (keep RearCalf);
# genuine thigh-high boots keep the thigh morph. Default on;
# CBBE2UBE_KEEP_BOOT_THIGH_SCALE=1 off.
EXCLUDE_BOOT_FAR_THIGH_SCALE = (
    not _flag("CBBE2UBE_KEEP_BOOT_THIGH_SCALE", False)
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
# --- #body-lookup-prefers-baseshape -- OPT-IN, default OFF -------------------
#
# `UBE_BODY_INJECT_NAMES` holds BOTH "BaseShape" and "VirtualBody", and four
# passes find "the body" with
#     next(s for s in nf.shapes if s.name in UBE_BODY_INJECT_NAMES)
# which takes whichever comes FIRST IN SHAPE ORDER. `VirtualBody` is an HDT-SMP
# COLLISION PROXY, not a body -- and on every piece that has one, it precedes
# the real BaseShape.
#
# REPORTED IN GAME 2026-09-04: "breasts and muscle mass disappear when
# equipped", on pieces that have physics. Root-caused to this, arithmetically:
#
#   piece         picked as "body"   verts    max vert index in its TRI
#   DressA        VirtualBody         5041            5040
#   DressB        VirtualBody         5776            5775
#   DressC        VirtualBody         5019            5018
#   DressALewd    BaseShape          29298           29297   (no VirtualBody)
#
# One-to-one. `generate_armor_tri` bounds-filters the body OSD's 202 morphs to
# the body vert count (#osd-bounds, correctly -- an out-of-range index is a
# runtime CTD), so with a 5k proxy standing in for a 29,298-vert body, every
# offset past ~5000 is dropped and any morph living entirely above it vanishes.
# DressA keeps 78 of 202 morphs; the ones lost include every breast slider
# (BreastsBigger, BreastFlat, BreastUnderCurve, Big_SaggyBreasts ...). The
# variant with no VirtualBody keeps all 202 and is correct -- which is why the
# defect tracks "has physics" and looked like a physics bug.
#
# The same mis-pick reaches three other passes: the butt-collider patch, the
# skirt-proxy source ranking (its `body_bones` becomes VirtualBody's 11 bones,
# so nearly every bone reads as "chain"), and the collider conform.
#
# OFF because it changes the TRI on every physics piece, which is a change to
# how bodies morph in game and needs its own verdict.
# PROMOTED TO DEFAULT ON 2026-09-04: without it the injected body is looked
# up as a collision proxy and the armour ships a fraction of the body's
# morphs -- sliders collapse on every physics outfit. Confirmed in game.
BODY_LOOKUP_PREFERS_BASESHAPE = not _flag(
    "CBBE2UBE_NO_BODY_LOOKUP_PREFERS_BASESHAPE", False)


def ube_body_shape(nif):
    """The actual injected BODY, never a collision proxy that shares its name
    list. See `#body-lookup-prefers-baseshape` above for why this exists."""
    shapes = list(getattr(nif, "shapes", ()) or ())
    if BODY_LOOKUP_PREFERS_BASESHAPE:
        for s in shapes:
            if s.name == "BaseShape":
                return s
    return next((s for s in shapes if s.name in UBE_BODY_INJECT_NAMES), None)


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


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


_XML_BONE_DECL_RE = re.compile(r'<bone\s+name="([^"]+)"')
_XML_BONE_TEXT_RE = re.compile(r"<bone>([^<]+)</bone>")
_XML_CONSTRAINT_BODY_RE = re.compile(r'body[AB]="([^"]+)"')


# (moved to nif_convert_physics.py, 2026-09-01)


_SOFT_BODY_PHYSICS_BONE_KEYWORDS = (
    "breast", "butt", "belly", "genital", "vagina", "anus", "clit", "labia",
)


# (moved to nif_convert_weights.py, 2026-09-01)


_GENITAL_ANATOMY_KEYWORDS = (
    "clit", "pussy", "vagin", "anus", "labia", "vulva", "penis", "scrotum",
)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


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


# (moved to nif_convert_weights.py, 2026-09-01)


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
    not _flag("CBBE2UBE_NO_CONFORM", False)
)
# Tunables (env-overridable). Validated on a pantyhose: 727/3846 verts conformed,
# inner-back-thigh leg-follow 54% -> 65% (body ~65-71%); a rigid greave: 0 verts.
_CONFORM_FIT_PROX = _knob("CBBE2UBE_CONFORM_FIT_PROX", 2.0)
_CONFORM_VERT_PROX = _knob("CBBE2UBE_CONFORM_VERT_PROX", 6.0)
_CONFORM_DELTA = _knob("CBBE2UBE_CONFORM_DELTA", 0.08)
_CONFORM_BLEND = _knob("CBBE2UBE_CONFORM_BLEND", 0.90)
_CONFORM_FIT_FRAC = _knob("CBBE2UBE_CONFORM_FIT_FRAC", 0.90)
_CONFORM_CHAIN_MAX = _knob("CBBE2UBE_CONFORM_CHAIN_MAX", 0.05)
_CONFORM_MIN_JIGGLE_VERTS = 8
# Shapes the leg/butt/chest conform+graft passes skip: the body, colliders,
# virtual/ref shapes, and draping-cloth garment names (robe/cloak/...) that may be
# runtime-global SMP cloth. "skirt" excluded on purpose (rigid metal tassets want
# the conform).  [DESIGN: HDT-SMP physics-cloth preservation]
# Split by WHAT EACH GROUP IS FOR, because only one of them is CTD-critical.
#
# STRUCTURAL: the body, collision proxies, virtual/ref helpers. Never garments.
# Always applied. ("col" also catches real collar shapes, and that collision looks
# like a bug -- but it is load-bearing by accident: such collars typically belong to
# cloaks driven by a GLOBAL physics config, so freeing them walks straight into C1
# below. Left alone deliberately.)
_CONFORM_SKIP_STRUCTURAL = ("baseshape", "3ba", "virtual", "col", "ground", "ref")
# DRAPING: the C1 mitigation. Draping cloth is often driven by a runtime-GLOBAL
# HDT-SMP config with no per-mesh XML, so there is nothing structural to detect;
# grafting UBE scale bones onto it crashed the SMP update on equip (skin-data OOB,
# the "robes" CTD -- still filed under Crashes, not Fixed). `_CHEST_JIGGLE_BONES`
# ARE that bone class. "skirt" excluded on purpose (rigid metal tassets want the
# conform).  [DESIGN: HDT-SMP physics-cloth preservation]
_CONFORM_SKIP_DRAPING = ("robe", "cloak", "cape", "dress", "gown", "sarong",
                         "loincloth")
_CONFORM_SKIP_NAMES = _CONFORM_SKIP_STRUCTURAL + _CONFORM_SKIP_DRAPING

# #drape-xml-gate -- apply the DRAPING names only to a piece that declares NO HDT
# physics XML.
#
# The reasoning the flag rests on: a piece that ships an XML has its physics AUTHORED
# and declared, and the existing structural gates read that same file. A shape absent
# from it is not simulated, so the name adds nothing there. The name is only the sole
# guard for the pieces with no XML at all -- which is exactly the class docs/DESIGN.md
# describes and C1 crashed on.
#
# Measured over 400 name-excluded shapes: 239 are already caught by a structural gate
# (name adds nothing); of the 161 doing real work, 57 belong to pieces WITH an XML and
# 104 to pieces without. On the clipping subset that is 22 freed / 13 kept.
#
# NOT airtight, and the failure mode is an equip CTD. "Absent from the XML" would be
# proof only if every simulated shape were named in it; C1's cloth was BONE-driven, and
# a bone-driven shape can in principle be simulated through `<bone>` constraints without
# being named. `LEG_CHAIN_GUARD` does NOT close that hole -- it skips verts driven by
# CUSTOM bones, while C1's robe was driven by SKELETON bones. Judge it by equipping
# robes, not by reading a metric.
# DEFAULT ON since 1.2. The failure mode is a crash when equipping a robe, so
# CBBE2UBE_NO_DRAPE_XML_GATE=1 restores the blanket skip if one appears.
DRAPE_SKIP_XML_GATED = (
    not _flag("CBBE2UBE_NO_DRAPE_XML_GATE", False))


def _conform_skip_keys(piece_has_hdt_xml=None) -> tuple:
    """Name keys that skip a shape, for a piece that does/doesn't declare an HDT XML.

    `piece_has_hdt_xml=None` means "unknown" and keeps today's full list -- an
    unanswered question must not relax a CTD guard. #drape-xml-gate"""
    if DRAPE_SKIP_XML_GATED and piece_has_hdt_xml:
        return _CONFORM_SKIP_STRUCTURAL
    return _CONFORM_SKIP_NAMES


def _piece_has_hdt_xml(path, nif=None) -> bool:
    """Does this piece declare an HDT-SMP physics XML that actually resolves?

    False for a piece driven by a runtime-global config (nothing to read) -- which is
    precisely the population the draping-name skip exists to protect. #drape-xml-gate"""
    try:
        return bool(_read_source_hdt_xml_text(Path(path), nif=nif))
    except Exception:
        return False        # unreadable -> treat as "no XML" -> keep the guard

# Graft a share of the body's jiggle onto a fitted garment that hugs a jiggling
# region but carries none of its own, so it follows the bounce instead of letting the
# body poke through. Default on; CBBE2UBE_NO_JIGGLE_TRANSFER=1 off, _FACTOR (0..1)
# scales it.  [DESIGN: Leg-plate bend / butt-jiggle conform]
TRANSFER_BODY_JIGGLE = (
    not _flag("CBBE2UBE_NO_JIGGLE_TRANSFER", False))
_JIGGLE_TRANSFER_FACTOR = _knob("CBBE2UBE_JIGGLE_TRANSFER_FACTOR", 0.85)

# #torso-jiggle-graft -- extend the jiggle graft to fitted TORSO garments (corsets,
# bras, cuirasses), which the leg-only gating in _transfer_body_jiggle_to_fitted
# excludes. Motivated in-game: a cuirass sits over a body carrying NINE jiggle
# bones and carries NONE of its own, so breast/butt travel and the leather does not
# -- "clips especially under movement". Static clearance cannot reach that; the
# garment has no way to follow the body at all.
#
# DEFAULT ON since 1.2. The original deferral ("jiggle on a rigid cup can read as
# rubbery, judge in game") was answered in game: the motivating cuirass with the
# full graft (via the bust collider split) was confirmed GOOD, first success after
# three reverted attempts. The tear-off failure mode that forced the 7c revert is
# structurally prevented -- a collider is never grafted, and the split hands the
# graft a non-collider target instead of relaxing that rule. The graft copies the
# BODY's own per-bone breast mix under each vert (a hand-tuned single-bone
# weighting with a HIGHER total follow read worse in game than the graft's matched
# distribution). CBBE2UBE_TORSO_JIGGLE=0 restores the old rigid behaviour.
# House convention for default-ON features is a NO_* opt-out (the GUI registry
# can only emit "1"-or-unset, so a positive-name flag with a "0" opt-out made
# the checkbox a no-op in BOTH directions -- audit 2026-07-28). The legacy
# CBBE2UBE_TORSO_JIGGLE=0 spelling published with 1.2 stays honored.
TORSO_JIGGLE_TRANSFER = (
    not _flag("CBBE2UBE_NO_TORSO_JIGGLE", False)
    and _flag("CBBE2UBE_TORSO_JIGGLE", True))
# Fraction of a torso shape's NON-CHAIN verts that must sit within _CONFORM_FIT_PROX
# of the body. The whole-shape 0.90 gate cannot serve: a cuirass welded to its own
# simulated skirt scores 0.43 over all verts (the skirt hangs away) and 0.67 over the
# rigid ones. Calibrated across 134 torso shapes in the shipped output -- capes,
# scarves, pauldrons and scabbards land at 0.00-0.03, corsets/bras/chest plates at
# 1.00 -- so any floor in 0.4-0.7 separates them; 0.5 takes the middle with margin.
_TORSO_JIGGLE_FIT_FRAC = _knob("CBBE2UBE_TORSO_JIGGLE_FIT", 0.5)

# #bust-collider-split -- a bust garment that is ITS OWN per-triangle collider can
# never carry jiggle: grafting onto it closes a feedback loop (cloth moves collider,
# collider pushes cloth) that tore the breasts off in game and forced a revert. The
# in-game-validated fix mirrors the arrangement the well-behaved vanilla siblings
# author by hand: a SEPARATE hidden collider clone carries the garment's current
# rigid weights (the resting chain sees identical support), the physics XML is
# repointed at the clone, and the garment -- no longer a collider -- becomes
# reachable by the stock torso jiggle graft. Detection is by MEASURED WEIGHT, never
# bone presence: a rendered per-triangle collider covering the bust band whose
# breast follow ratio against the body underneath is below the floor. Split and
# graft are one fix, so the split keys off the torso-graft gate; the kill switch
# only exists for bisection.  [DESIGN: bust collider split]
BUST_COLLIDER_SPLIT = (
    not _flag("CBBE2UBE_NO_BUST_COLLIDER_SPLIT", False))
_BUST_SPLIT_MIN_VERTS = 50       # bust-band verts before "covers the bust"
_BUST_SPLIT_FOLLOW_FLOOR = _knob("CBBE2UBE_BUST_SPLIT_FOLLOW_FLOOR", 0.5)
_BUST_SPLIT_PROX = 4.0           # garment vert counts only when this close to body
_BUST_SPLIT_COL_SUFFIX = "Col"
_BUST_SPLIT_HIDDEN_FLAGS = 15    # matches the hand-authored hidden colliders

# #collider-declared-bones, on the BUST-SPLIT CLONE. Default ON, kill switch
# CBBE2UBE_NO_SPLIT_COL_DECLARED_BONES, no Setting row -- a safety invariant,
# not an option, exactly as #authored-shape-order is.
#
# A shape the piece's physics XML REGISTERS must not carry a bone that XML never
# DECLARES: FSMP cannot resolve the influence and the piece free-falls off the
# actor (confirmed in game twice). `_add_butt_collider_patch` and
# `_add_skirt_collider_proxy` already relabel such a bone onto its nearest
# XML-declared ancestor and DECLINE when there is none. This clone did not, and
# it is the same class -- we CREATE the registered shape, so every undeclared
# bone on it is ours by construction.
#
# MEASURED ON THE SHIPPED PACK, 2026-08-23: 1536 `_1` NIFs, 175 pieces register
# a shape at all, and 10 GENERATED `<name>Col` clones across 8 pieces carry
# undeclared bones -- 19 bones relabellable, 4 not:
#
#     TorsoCol / BaronArmorCol / collisionCol / collision2Col
#                       UpperarmTwist1,2  ->  UpperArm
#     coatCol           Finger00          ->  Hand
#     CloakCol          UpperArm          ->  Clavicle
#     Low_Skirt:1Col    Pelvis            ->  NONE, x4  -> the clone is DECLINED
#
# (The pack also shows 127 AUTHORED registered shapes with undeclared bones.
# Those are NOT in scope and must not be swept in: the author shipped them that
# way, `_audit_registered_shape_declared_bones` scores only bones WE added, and
# rewriting authored weights walks into the setShapeWeights-is-an-update and
# add_bone-resets-STBs traps for no measured defect.)
#
# THE REDIRECT IS POSITION- AND SIMULATION-NEUTRAL, which is why it is safe: a
# bone the XML does not declare is not simulated by this piece at all, so it is
# kinematic either way, and every bone's STB is its own bind inverse -- so which
# bone carries the weight does not move the skinned point. The ancestor must
# already be ON THIS SHAPE (so an STB for it exists); checked per shape, not
# against the NIF's union, which would over-report by finding a bone the clone
# cannot weight to.
SPLIT_COL_DECLARED_BONES = not _flag(
    "CBBE2UBE_NO_SPLIT_COL_DECLARED_BONES", False)

# Make a rigid leg plate track the body's leg bend: match each leg vert's Thigh/Calf
# split to its nearest body vert and graft the body's detail bones (Front/Rear thigh,
# rear calf) so the plate flexes with the thigh instead of the body poking through.
# Never moves a vert or adds a jiggle bone. Default on; CBBE2UBE_NO_LEG_BEND_MATCH=1
# off.  [DESIGN: Leg-plate bend / butt-jiggle conform]
MATCH_RIGID_LEG_BEND = (
    not _flag("CBBE2UBE_NO_LEG_BEND_MATCH", False))

# LEG-MOTION MATCH (#leg-motion-match). Complements MATCH_RIGID_LEG_BEND, which only
# runs on shapes eligible for a full reskin. A garment that KEEPS ITS SOURCE SKIN --
# every BodySlide-built piece shipping its own morph TRI, via the `_keep_src_skin`
# branch -- carries CBBE-fitted leg weights over a UBE body, so its leg-bone share is
# LOWER than the body's underneath it. Measured on a common-clothes dress (clipping
# log F1): garment L-Thigh 0.592 vs body 0.763, lower on 80% of the failing verts.
# Under hip flexion the body travels further than the cloth and emerges through it --
# invisible at bind pose, which is why every bind-pose metric called that mesh clean.
#
# This raises the garment's leg share toward the body's, redistributing ONLY among
# bones the shape ALREADY HAS (no add_bone -> none of the add_bone STB-reset footguns).
# Push-up only (never lowers a leg share), never moves a vert, gated to verts that HUG
# the body so a free-hanging hem is not pulled onto the leg bones.
#
# Validated with scripts/analysis/posed_clip_test.py --regression (covered-at-bind -> exposed-
# under-stride); bind-pose metrics CANNOT see this defect and must not be used to judge
# it. Dress 113 -> 21 newly-exposed verts (-81%); four heavy-armor variants 65/41/17/4
# -> 0; nine other garments unchanged; bind-pose exposure identical on every mesh
# tested (no static regression); free-hem stride motion +0.03u.
# Default ON; CBBE2UBE_NO_LEG_MOTION_MATCH=1 off.
MATCH_LEG_MOTION = (
    not _flag("CBBE2UBE_NO_LEG_MOTION_MATCH", False))
# Fraction of the body-vs-garment leg-share gap to close (1.0 = full match).
_LEG_MOTION_STRENGTH = _knob("CBBE2UBE_LEG_MOTION_STRENGTH", 1.0)
# Only match verts within this distance of the body: beyond it the cloth is drape, not
# a fitted layer, and matching it would make a skirt cling to the legs.
_LEG_MOTION_MAX_DIST = _knob("CBBE2UBE_LEG_MOTION_MAX_DIST", 9.0)
# World-Z band: lower bound keeps the free hem out, upper bound stops at the waist.
_LEG_MOTION_Z_LO = _knob("CBBE2UBE_LEG_MOTION_Z_LO", 30.0)
_LEG_MOTION_Z_HI = _knob("CBBE2UBE_LEG_MOTION_Z_HI", 80.0)
# Bones whose share is rebalanced. Only these are managed; every other bone on the
# shape keeps its relative proportion and is rescaled to fill the remainder.
_LEG_MOTION_BONES = ("NPC L Thigh [LThg]", "NPC R Thigh [RThg]",
                     "NPC L Calf [LClf]", "NPC R Calf [RClf]",
                     "NPC Pelvis [Pelv]")

# #armhole-arm-follow -- the SHOULDER instance of the same defect the leg pass fixes.
# CBBE/3BA and UBE rig the shoulder DIFFERENTLY: measured on the reference bodies,
# CBBE's UpperArm weight STOPS at z 99.7 and the shoulder above it is Clavicle-only,
# while UBE carries UpperArm up to z 110.1 (mean 0.239 at z 100-105, 0.194 at 105-110).
# A CBBE-authored garment therefore arrives with the CBBE convention baked in: measured
# on a vanilla cuirass, ZERO UpperArm weight across the whole 439-vert armhole band
# against a body carrying mean 0.179 there. An UpperArm rotation then moves the body and
# not the garment (follow p10 = 0.000 -- those verts do not move AT ALL) and the shoulder
# emerges through the armhole in motion. Invisible at bind pose, which is why clearance
# work never touched it.
#
# This is NOT a converter leak: the source garment and the source body BOTH measure 0.000
# there, so nothing is being dropped -- it is a body-rig mismatch, and it applies to every
# CBBE-sourced piece covering the shoulder, not to one armor.
#
# Band from measurement, not guesswork: the deficit spans z 87.7-113.9 (p5 91.5, p95
# 108.9) with a garment-to-body hug of median 0.52 / p90 1.45. The Z bounds are only a
# safety rail -- the real selector is the BODY's own arm weight, since push-up-only
# leaves any vert whose body point carries no arm mass exactly as authored.
# Default ON; CBBE2UBE_NO_ARM_MOTION_MATCH=1 off.
MATCH_ARM_MOTION = (
    not _flag("CBBE2UBE_NO_ARM_MOTION_MATCH", False))
_ARM_MOTION_STRENGTH = _knob("CBBE2UBE_ARM_MOTION_STRENGTH", 1.0)
# Tighter than the leg's 9.0: a shoulder sits close, and the gap to keep clear of is a
# free-hanging pauldron/cape, which must not be pulled onto the arm and made to swing.
_ARM_MOTION_MAX_DIST = _knob("CBBE2UBE_ARM_MOTION_MAX_DIST", 5.0)
_ARM_MOTION_Z_LO = _knob("CBBE2UBE_ARM_MOTION_Z_LO", 84.0)
_ARM_MOTION_Z_HI = _knob("CBBE2UBE_ARM_MOTION_Z_HI", 125.0)
# Clavicle is managed ALONGSIDE UpperArm on purpose. The defect is not just missing
# UpperArm mass, it is mass sitting on the WRONG bone of the pair: the garment carries
# Clavicle 0.429 where the body carries Clavicle 0.377 + UpperArm 0.179. Managing only
# UpperArm would add mass without correcting the split.
_ARM_MOTION_BONES = ("NPC L Clavicle [LClv]", "NPC R Clavicle [RClv]",
                     "NPC L UpperArm [LUar]", "NPC R UpperArm [RUar]")

# #spine-follow -- the TORSO instance, and the cause of the under-bust follow deficit
# logged as S3. The rig sweep (CBBE reference body vs UBE body, matched by nearest
# surface) makes Spine2 the LARGEST disagreement between the two bodies: 6564 verts,
# mean 0.227, z 80.9-110.7. The reverse direction shows CBBE loading `Spine1` where
# UBE does not, so this is a SPLIT problem across the three spine bones, not missing
# mass -- exactly the shape of the Clavicle-vs-UpperArm defect.
#
# Measured in the under-bust band (z 84-90, front) of a vanilla cuirass, normalised
# spine split:
#     garment   Spine 0.121   Spine1 0.727   Spine2 0.151
#     body      Spine 0.098   Spine1 0.492   Spine2 0.410
# Spine2 is furthest up the chain so it accumulates the most rotation; the garment
# parks its mass on Spine1 and carries 0.151 of the motion-heaviest bone against the
# body's 0.410. That is the ~20% under-follow, and it is invisible at bind pose.
#
# ORDER IS LOAD-BEARING: this pass runs BEFORE the arm pass. Every family-scoped
# match rescales the bones it does not manage, so two passes over overlapping bands
# fight, and whichever runs LAST wins. Measured on the same piece: spine running
# last drags the armhole from 1.106 back to 1.076. The arm pass cannot do the
# reverse harm, because under-bust rows carry no arm-family weight at all and are
# skipped by the `g_mass > 1e-6` test. So spine first, arm second.
# Holding the already-matched families at their absolute weights instead was
# MEASURED AND REJECTED -- it reached only 437 of 1177 rows, left the under-bust at
# 0.948 instead of 1.099, and pushed the armhole into over-follow (median 1.34,
# verts below 0.5 up from 3.8% to 5.1%). Do not re-attempt it.
# Default ON; CBBE2UBE_NO_SPINE_MOTION_MATCH=1 off.
MATCH_SPINE_MOTION = (
    not _flag("CBBE2UBE_NO_SPINE_MOTION_MATCH", False))
_SPINE_MOTION_STRENGTH = _knob("CBBE2UBE_SPINE_MOTION_STRENGTH", 1.0)
_SPINE_MOTION_MAX_DIST = _knob("CBBE2UBE_SPINE_MOTION_MAX_DIST", 5.0)
# Covers the measured mismatch (z 80.9-110.7) with margin at the waist end, where
# the garment over-weights Spine1 against the body's Spine/Spine2.
_SPINE_MOTION_Z_LO = _knob("CBBE2UBE_SPINE_MOTION_Z_LO", 72.0)
_SPINE_MOTION_Z_HI = _knob("CBBE2UBE_SPINE_MOTION_Z_HI", 120.0)
# All three managed together: the defect is the SPLIT between them.
_SPINE_MOTION_BONES = ("NPC Spine [Spn0]", "NPC Spine1 [Spn1]",
                       "NPC Spine2 [Spn2]")

# --- #spine-twist-partial: the same split, at PARTIAL strength ----------------
# Reported in game: the bust comes out the SIDE of a cuirass while SWINGING a
# weapon; motion only, not at rest, and not preset-dependent. Root-caused on the
# vanilla leather/studded cuirass by follow measurement, over the flank verts that
# newly emerge under the swing:
#
#     follow            0.88-0.89   UNDER-follow
#     d clearance       -0.47u      the skin closes on the garment
#     NPC Spine1        body 0.016   garment 0.167   +0.150
#     NPC Spine2        body 0.742   garment 0.652   -0.090
#
# Spine1 is Spine2's PARENT, so mass one link up the chain under-rotates every
# twist and the skin slides out sideways. Same shape as the Clavicle-vs-UpperArm
# split in _ARM_MOTION_BONES.
#
# WHY A SEPARATE INSTANCE AND NOT JUST UNGATING THE TORSO ONE. The shipped spine
# pass is built for exactly this defect and never runs here: this garment owns a
# source morph TRI, and MORPHTRI_NO_LEG_GRAFT skips it (that gate reaches 88% of
# the pack). Ungating it at FULL strength was measured, same build, zero vertex
# movement, and it TRADES one pose family for another.
#
# GEOMETRIC SCOPING TO THE FLANK WAS TRIED FIRST AND IS REFUTED. It is the obvious
# move -- touch only the verts the defect was measured on -- and it threw away
# most of the fix while keeping the cost (`swing strike` 12.81 -> 12.06 where the
# unscoped pass reaches 7.79, and `spine twist` got WORSE, 7.29 -> 9.04). The
# garment is a continuous surface: the flank is pulled straight by verts on the
# FRONT and BACK of the same panel, so a fix confined to the flank cannot move it.
# `lateral_half_x` is kept as a knob and defaults to OFF for that reason.
#
# WHAT DOES WORK IS PARTIAL STRENGTH -- but only since `strength` was made to
# reach the SPLIT (see _match_limb_motion_to_body). breast_side, Punk UBE,
# same build, weights-only:
#
#     strength      0.00     0.35     0.60     1.00
#       sprint     13.82    16.08    17.34    19.60   <- monotonically WORSE
#       swing strike 12.81  10.55     7.79     8.54
#       spine twist   7.29   4.52     3.77     6.28
#       swing windup  5.28   3.27     2.26     2.51
#       bow draw      2.76   0.75     0.00     0.00
#
# 0.60 is the best swing. IT IS NOT FREE: `sprint` -- a hard forward lean -- is
# worse at EVERY non-zero strength, monotonically, so this is a genuine trade and
# not a tuning problem. Every other region is neutral or better at 0.60.
# NOTHING HERE IS CONFIRMED IN GAME AND IT IS MEASURED ON ONE PIECE, which is why
# it is DEFAULT OFF -- CBBE2UBE_SPINE_TWIST_MATCH=1. It also carries
# ignore_morph_tri, which no shipped instance does.
MATCH_SPINE_TWIST = (
    _flag("CBBE2UBE_SPINE_TWIST_MATCH", False))
_SPINE_TWIST_STRENGTH = _knob("CBBE2UBE_SPINE_TWIST_STRENGTH", 0.6)
_SPINE_TWIST_MAX_DIST = _knob("CBBE2UBE_SPINE_TWIST_MAX_DIST", 5.0)
# 0.0 = the whole torso band. Measured WORSE when narrowed to the flank; kept so
# the negative stays reproducible rather than only written down.
_SPINE_TWIST_LATERAL_X = _knob("CBBE2UBE_SPINE_TWIST_LATERAL_X", 0.0)
# Ray-along-normal pairing instead of KD-nearest. ON for this instance because
# the whole point of it is to copy the RIGHT body vert's weighting;
# CBBE2UBE_SPINE_TWIST_PAIR_RAY=0 restores KD so the two stay comparable.
# The shipped leg/arm/spine instances keep KD until this is measured on them --
# changing their pairing changes production output and needs its own census.
_SPINE_TWIST_PAIR_RAY = (
    _flag("CBBE2UBE_SPINE_TWIST_PAIR_RAY", True))
_SPINE_TWIST_BONES = _SPINE_MOTION_BONES

# --- #full-weight-match: match the whole vector, not one family ---------------
# The family matches all share one structural limit: they fix ONE bone family and
# rescale every other bone by a single proportional factor to make room, so the
# fix is funded out of whatever else the row carries. Measured on the vanilla
# leather/studded cuirass, spine instance: SPINE +0.0397 while NPC Pelvis -0.0258
# and L/R Clavicle -0.0041 -- and `sprint`, the one pose that leans, swings the
# arms and drives the hips together, got worse while the twist poses got better.
#
# The scale of what a single family leaves unmatched, garment vs the covered body
# over the 2322 hugging torso rows, L1 across the 17 shared bones:
#
#     p10 0.156   p50 0.507   p90 0.824      rows already within 0.10: 5.0%
#     L/R UpperArm 0.081/0.076   Spine2 0.063   L/R UpperarmTwist1 0.059/0.057
#     Pelvis 0.057   Spine1 0.036   Clavicle 0.023
#
# The spine family is ~0.10 of a 0.507 gap. NOT a pairing artefact: ray-along-
# normal pairing re-pairs ~60% of rows and leaves the gap where it was
# (KD p50 0.492 vs RAY p50 0.503), because the two rules pick body points a
# median 0.442u apart and the body's weights barely vary over that.
#
# So: copy the whole vector. Its ideal is follow = 1.0 in EVERY pose at once.
#
# MEASURED, and the prediction held -- NO pose is worse than production at either
# strength, which no family match managed at any setting. breast_side, Punk UBE,
# same build, weights-only (zero vertex movement, so every bind-pose clearance
# result from the earlier passes survives by construction):
#
#     pose              production   family(0.6)   FULL 0.6   FULL 1.0
#       sprint             13.82        17.84         8.54       2.01
#       swing strike       12.81         7.04         5.03       3.52
#       spine fwd lean     10.80        10.80         1.26       0.25
#       spine twist         7.29         3.77         0.50       0.00
#       walk + lean         6.28         6.28         0.00       0.00
#       swing windup        5.28         2.01         2.01       2.01
#     region  breast       50.91        51.43        22.86       3.12
#     region  upper_back    3.32         3.32         1.02       1.28
#     region  lower_back    4.02         1.72         1.44       0.86
#     belly / butt / thigh  unchanged at every setting (outside the band)
#
# Monotone in strength, which is what "the ideal is follow 1.0" predicts: at 1.0
# the hugging rows ARE the body's rows, so the garment travels with the skin and
# there is nothing to slide out from under.
#
# DEFAULT 0.6 DESPITE 1.0 MEASURING BETTER HERE. At 1.0 a garment deforms exactly
# like skin, which is right for this soft leather and WRONG for a rigid plate --
# the material ceiling is aesthetic, not geometric, and jiggling steel looks
# wrong. That concern is unmeasured, so the default stays conservative.
#
# DEFAULT ON since 2026-08-11 (CBBE2UBE_NO_FULL_WEIGHT_MATCH=1 disables).
#
# It shipped OFF because this is the closest thing in the codebase to a reskin of
# a source-skinned garment -- the thing the morph-TRI gate exists to prevent --
# and because at strength 1.0 a garment deforms exactly like skin, which is right
# for soft leather and was feared WRONG for rigid plate ("jiggling steel looks
# wrong"). That was the one unmeasured objection and it is now answered: the
# glass cuirass, rigid plate with 3.97% of its mass relocated, was judged in game
# alongside the leather. USER: both "look perfect".
MATCH_FULL_WEIGHTS = (
    not _flag("CBBE2UBE_NO_FULL_WEIGHT_MATCH", False))

# --- #breast-follow-keep: push-up only on the BREAST family ------------------
# Both body-match passes blend a garment's weights TOWARD the body's. That is
# right for LIMBS -- the garment has to travel with the joint or the body
# out-swings it and skin emerges -- and wrong for the BUST, where the author
# deliberately gives an inner layer MORE breast follow than the body so it hugs.
# The UBE BaseShape's own band follow is 0.330, so every layer converges on
# 0.330 from wherever the author put it. BISECTED on a layered robe
# (`scratchpad .../follow_ledger.py`, band z88-104):
#
#   shape        CBBE source   first write   shipped
#   BodyStock        0.681        0.680        0.355   <- _conform_fitted_to_body
#   TopLeather       0.465        0.469        0.214   <- and _match_full_weights
#   Cloth            0.463          .          0.070
#
# THE PRE-WRITE PIPELINE IS INNOCENT: the first write still carries the author's
# values. Two post-write passes take them.
#
# WHY IT IS THE VISIBLE DEFECT. Shipped, BodyStock (0.355) follows the breast
# slightly MORE than the body under it (0.330) while the TopLeather over it
# follows at 0.214 -- so under breast motion the inner layer travels outward and
# the outer one lags, and the bodystock swings out THROUGH the leather. The
# author's ordering is the opposite: every layer follows LESS than the body
# (0.681, 0.465 vs the body's 1.0), inner-most most. No bind-pose metric can see
# this -- measured on the same piece, only 37 of 1982 body-stock verts covered by
# the leather in the source are uncovered in ours (1.9%), i.e. the REST POSE
# stack is very nearly right. #clearance-is-the-wrong-axis, again.
#
# `_match_limb_motion_to_body` already states this rule for its own managed
# family ("PUSH-UP ONLY -- the TARGET is never below the garment's existing
# share"). The full-vector instance manages EVERY shared bone and never applied
# it; the conform blend never had it. This applies it to breast in both.
# DEFAULT ON since 2026-08-13. VERIFIED IN GAME, under motion, which is the only
# place the difference shows: "the bust layers track together yes". Then gated
# per REGION against the previously-shipped mesh on 7 pieces (4 hide cuirasses,
# a light-armour replacer, a plated bust top, the layered robe) with no
# region worse and most better. Off with CBBE2UBE_NO_BREAST_FOLLOW_KEEP=1.
BREAST_FOLLOW_KEEP = not _flag("CBBE2UBE_NO_BREAST_FOLLOW_KEEP", False)


# (moved to nif_convert_weights.py, 2026-09-01)


def _keep_breast_share(before: "dict", after: "dict") -> "dict":
    """Push-up only on breast: never return a row with LESS breast share than it
    came in with. The shortfall is funded proportionally from the non-breast
    bones, so the row still sums to what `after` summed to.

    Never introduces a bone the vert did not already have -- the restored breast
    entries are taken from `before`, and `after` is always a subset of it, so the
    result stays inside the vert's original (<= 4) influence set and the
    partition palette stays valid. #breast-follow-keep
    """
    if not after or not before:
        return after
    sb = sum(before.values())
    sa = sum(after.values())
    if sb <= 0 or sa <= 0:
        return after
    want = sum(w for b, w in before.items() if _is_breast_bone(b)) / sb
    have = sum(w for b, w in after.items() if _is_breast_bone(b)) / sa
    if want <= 0 or have >= want - 1e-6:
        return after                     # no breast, or already tracking
    other = sum(w for b, w in after.items() if not _is_breast_bone(b))
    if other <= 0:
        return after                     # all-breast row: nothing to fund from
    f_other = ((1.0 - want) * sa) / other
    out = {b: w * f_other for b, w in after.items() if not _is_breast_bone(b)}
    br_before = {b: w for b, w in before.items() if _is_breast_bone(b) and w > 0}
    tb = sum(br_before.values())
    for b, w in br_before.items():
        out[b] = (w / tb) * want * sa
    return out


# 1.0, not the 0.6 this shipped with: 1.0 is the strength that was BUILT and
# judged on both pieces. Leaving the knob at 0.6 while flipping the toggle would
# default the pack to a recipe nobody has looked at -- the exact "the shipped
# default configuration is not the one being validated in game" trap the
# 2026-07-27 audit recorded.
_FULL_WEIGHT_STRENGTH = _knob("CBBE2UBE_FULL_WEIGHT_STRENGTH", 1.0)
_FULL_WEIGHT_MAX_DIST = _knob("CBBE2UBE_FULL_WEIGHT_MAX_DIST", 5.0)
# Above the shoulder the hug gate has to mean CONTACT, not proximity -- that is
# where pauldrons and raised trim live. Band start comes from body_zones
# (ARMHOLE_Z[0]); 0 disables and restores one gate everywhere.
_FULL_WEIGHT_SHOULDER_DIST = _knob("CBBE2UBE_FULL_WEIGHT_SHOULDER_DIST", 2.0)
# #full-weight-limb-boundary. A garment vert may adopt an ARM-DOMINATED body
# weight vector only if it is ITSELF arm geometry. Above this share the matched
# body vertex is judged to be on the arm rather than the torso.
#
# REPORTED IN GAME on a multi-layer cuirass: "stretched parts that link to the
# arms when they shouldn't". The nearest body vertex is not the covered one, and in
# the A-pose the upper arm hangs right beside the chest -- so for chest-plate
# geometry near the armpit, KD-nearest lands ON THE ARM and the full-vector copy
# takes the arm's whole weight row with it. Measured on that cuirass: 396 units
# of arm weight moved onto `chest_plate`, concentrated at |x| 8-12 and z 90-100
# (the front of the chest at bust height, where the arms are at |x| > 16), with
# the worst verts reaching 0.98 -- chest plate almost fully following the arm.
#
# The z>=103 shoulder gate above cannot see this: it is a band, and this is a
# whole band lower. The property that separates them is not height but WHICH
# LIMB the matched body point belongs to, which is why this gate is expressed
# that way and generalises to any limb the same accident could reach.
_FULL_WEIGHT_LIMB_MAX = _knob("CBBE2UBE_FULL_WEIGHT_LIMB_MAX", 0.5)
# The copy renormalises whatever share of the body's row its basis captured up
# to 1.0, so a basis that captures little turns a partial sample into a
# confident wrong answer. Require it to explain at least half the body's motion
# at that vertex; below the floor the vert keeps its authored row. 0 restores
# the old effectively-absent gate. See #layer-follow-divergence, which narrows
# the basis to a stacked group's shared bones and so makes this reachable.
_FULL_WEIGHT_BASIS_MIN = _knob("CBBE2UBE_FULL_WEIGHT_BASIS_MIN", 0.5)

# #chain-skirt-physics. Generating soft-body physics for a shadowed chain-driven
# skirt (see #shadowed-chain-skirt) is OFF by default: shipped ON once and a
# common-clothes dress's skirt COLLAPSED in game. Everything structural checked out
# (all 12 chain bones present as nodes, all referenced by the XML, parenting identical
# to sibling dresses that emit fine), so the generated collision-only XML is simply not
# reliable on an arbitrary chain and we cannot predict which. A collapse is worse than
# the clipping it fixes. Kinematic + the leg-motion match covers the same ground safely.
CHAIN_SKIRT_PHYSICS = (
    _flag("CBBE2UBE_CHAIN_SKIRT_PHYSICS", False))
# Skyrim's skin partition holds at most 4 bone influences per vertex. Exceed it and
# the SAVE silently keeps the LARGEST 4 and does NOT renormalise (measured
# 2026-07-25), scrambling a computed split and leaving the row off 1.0, so
# the pass prunes to the 4 largest itself. #leg-motion-match
_SKIN_MAX_INFLUENCES = 4
# setShapeWeights ignores pairs at or below this, so anything we intend to KEEP
# must be written above it (see #legmotion-normalise).
_WRITE_MIN = 1e-4

# #weight-write-invariant (P6 step 2b) -- DEFAULT ON since 2026-07-26.
# Makes `_match_rigid_leg_bend_to_body` cap each row to 4 influences, renormalise
# to 1.0, and write every CHANGED (bone, vert) pair with explicit 0.0 removals.
# That pass introduces ALL of the on-disk weight drift -- 0 bad-sum verts before
# it, 2016 after -- and un-normalised rows transform a vertex by a deflated sum of
# its bone matrices, which is the documented "invisible head-on, fine from the
# side" view-angle flicker.
#
# MEASURED (same-context, single-mesh convert):
#   bad-sum verts   1966 -> 16 (traced heavy cuirass), 181 -> 0 (a light body mesh)
#   zero-weight bones  3 -> 1  (it REMOVES pre-existing orphans; adds none)
#
# The equip-CTD objection that held this back is RESOLVED. `to_add` used to be
# decided before the cap, so the cap could zero a grafted bone on every vertex and
# leave a bone already `add_bone`'d carrying an EMPTY weight list -- the
# `#zeroweight-bone-desync` class (per-vert index runs past the regenerated
# palette on equip). The cap now runs FIRST, over a provisional palette, so a
# graft that will not survive simply fails the emit test, is never added, and
# folds onto its anchor via `unsafe`. Verified: no new zero-weight bones.
#
# STILL UNVERIFIED IN-GAME: this shifts row magnitudes (0.93 / 1.16 -> exactly
# 1.0) and `_CHEST_JIGGLE_CAP`, `_BUTT_JIGGLE_CAP`, `_LEG_BEND_*` and the z-ramps
# were tuned against the UN-normalised output. If knee/thigh/butt conform looks
# wrong on rigid leg plate, set CBBE2UBE_NO_WEIGHT_INVARIANT=1 and reconvert --
# that restores the previous write exactly (no rebuild needed).
#
# Also still to do: restrict the cap/renormalise to the verts the pass actually
# conformed (it currently sweeps all `n`), and honour the "row that lost all
# weight is left alone" guarantee in `_cap_and_renormalise_rows`' contract.
WEIGHT_INVARIANT_ENABLED = not _flag("CBBE2UBE_NO_WEIGHT_INVARIANT", False)


# #family-weight-invariant -- the SAME defect in the family-match write, which
# the flag above does not reach.
#
# DEFAULT ON since 2026-09-02 (`CBBE2UBE_NO_FAMILY_WEIGHT_INVARIANT=1` restores
# the previous write). It is the fix for `#zeroweight-bone-desync` at this site,
# and the measurement that promoted it is in
# docs/worklog/ZEROWEIGHT_BONE_PRODUCER.md. On a 38-NIF outfit (38 NIFs, 294
# shapes), converted through the deployed exe at defaults:
#
#     zero-weight bones   21 -> 0        (13 shapes -> 0)
#     bad-sum verts      361 -> 0        (34 warnings -> 0)
#     verts moved          0             (weights only; positions untouched)
#     bust follow      1.033-1.082 -> 1.033-1.084 on the shape that was losing
#                                        L/R Breast03 -- unchanged
#
# It beats the alternative of switching `#full-weight-match` OFF, which also
# clears the orphans but leaves 26 bad-sum verts and moves 112,984 verts.
# The reason it works is the rule it already stated for itself: keep the
# influences the vertex already HAS and spend only FREE slots on newcomers, so
# a newcomer can never displace an existing bone's last weight.
#
# IN-GAME VERDICT STILL OWED -- promoted on measurement, like the 2026-08-26
# three. If the verdict is bad the honest fix is to move it back, not to
# re-argue the numbers.
#
# ITS ORIGINAL SAFETY STORY WAS WRONG, and the correction is the whole point of
# docs/worklog/ZEROWEIGHT_BONE_PRODUCER.md. This comment used to read "it adds no
# bones, so it cannot strand one unweighted". A pass does not have to ADD a bone
# to strand one: the family write filters only on `_WRITE_MIN` (1e-4) while the
# SAVE keeps the largest FOUR influences per vertex, so demoting an existing
# bone's last weight to 5th place -- e.g. `_match_full_weights_to_body` taking
# `L Breast03` on one vertex from 0.02432 to 0.00020, traced 2026-09-02 -- writes
# a weight that clears the filter and is then dropped at save, leaving a bone
# `_install_skin` legitimately added carrying an EMPTY weight list. That is
# `#zeroweight-bone-desync`, the equip-CTD class, and the 2026-08-28 pack ships
# 225 of them across 163 shapes. Do not reason about stranding from add_bone
# alone; reason about what survives the 4-influence cap at SAVE.
#
# Traced per pass over the reported piece, bad-sum verts added to `top`:
#     _match_rigid_leg_bend_to_body   +76   worst 0.032
#     _match_full_weights_to_body    +218   worst 0.140
# and the author's own mesh has ZERO on every shape, so all of it is ours.
# Nothing weight-related runs after the second, so nothing repairs it.
FAMILY_WEIGHT_INVARIANT = (
    not _flag("CBBE2UBE_NO_FAMILY_WEIGHT_INVARIANT", False))


# --- #last-carrier-hold: the 4-influence cap may not strand a bone ------------
#
# `#family-weight-invariant` above states the rule that works -- keep the
# influences a vertex already HAS, spend only FREE slots on newcomers -- and
# implements it inside `_match_limb_motion_to_body` only. This is the same rule
# in `_cap_and_renormalise_rows`, the SHARED cap both "capped" write sites use,
# so it covers them without a per-pass guard.
#
# THE RESIDUAL IT CLOSES, and the attribution here CORRECTS the record. The
# 2026-09-02 analysis put the surviving zero-weight bones on the five UNCAPPED
# post-write sites and named `_match_limb_motion_to_body` the actor. Bisected
# 2026-09-06 on the piece that actually ships one -- `Top_Wrap` / `SkirtBBone02`,
# a bone the author holds on ONE vertex at 0.03467:
#
#     defaults                          zero-weight 2   SkirtBBone02 live 0
#     CBBE2UBE_NO_LEG_BEND_MATCH=1      zero-weight 0   SkirtBBone02 live 1
#     CBBE2UBE_NO_FULL_WEIGHT_MATCH=1   zero-weight 2   (not the actor)
#
# So the producer is `_match_rigid_leg_bend_to_body`, one of the two CAPPED
# sites. The cap is not missing; the cap is the eviction. It grafts `NPC R Butt`
# onto that vertex at 0.04747, the row goes to five influences, and "keep the
# largest four" drops the author's 0.03467 -- which was that bone's only weight
# in the shape.
#
# WHY THE GUARD ALREADY THERE COULD NOT WORK. The caller's own "NEVER EMPTY A
# BONE" check declines to WRITE a bone the cap zeroed everywhere and leaves the
# stale weight in the file. But `setShapeWeights` MERGES and the SAVE resolves an
# overflowing row itself, so the fifth influence is still written over it and the
# eviction simply happens at save time instead. Declining to write does not
# protect a bone -- the same lesson `#family-weight-invariant` records.
#
# BOUNDED ON PURPOSE: weight alone still decides the first three survivors, so a
# dominant bone can never be displaced by this. Only the smallest surviving
# influence is contested, which is exactly where the class lives -- a bone
# stranded this way was carrying 0.004% of the shape's weight mass (measured,
# docs/worklog/ZEROWEIGHT_BONE_PRODUCER.md), so the fit cost is bounded by
# construction and the bone-list bookkeeping is the whole defect.
#
# `CBBE2UBE_NO_LAST_CARRIER_HOLD=1` restores the previous survivor choice
# byte-for-byte: with the hold off no carrier counts are built and the sort key
# degenerates to the old `(-weight, name)`.
LAST_CARRIER_HOLD = (
    not _flag("CBBE2UBE_NO_LAST_CARRIER_HOLD", False))

# The third promotion. `FAMILY_WEIGHT_INVARIANT` is the first made on a
# BOOKKEEPING measurement rather than a fit one: it moves no vertex, so the pair
# it is judged on is `verify_zero_weight_bones.py` (21 -> 0) and the report's
# bad-sum count (361 -> 0), with bust follow held flat as the counter-metric.
#
# `PHASE1_ANTIPOKE` is the opposite kind: it MOVES GEOMETRY on ~78% of the pack,
# and it is here because the copy path ran no body repair at all (BUG-02). Two
# populations, an in-game verdict, and clean results on both axes that killed
# F010 -- see its own comment for the numbers. Unlike the 2026-08-26 three, this
# one WAS judged in game before promotion, on the piece that reported it.
_DEFAULTS_PROMOTED_2026_09_02 = (
    "FAMILY_WEIGHT_INVARIANT",
    "PHASE1_ANTIPOKE",
)


# #skin-influence-cap -- apply the same 4-influence cap to the MAIN skin install.
# `_install_skin` gates on "only add bones that still carry weight", but it measures
# that BEFORE the save, and the save keeps only the 4 largest influences per vertex
# without renormalising. A bone whose every weight is the 5th-largest on its vertex
# therefore passes the gate, gets add_bone'd, and then lands on disk with an EMPTY
# weight list -- a bone in the shape's list but absent from the regenerated partition
# palette, so a per-vert index can run past it on equip. Exactly the defeat P6 found
# on the leg pass, at the site that skins every converted mesh.
#
# Measured: 59 zero-weight bones across 42 shapes pack-wide, and on one affected
# shape 29% of verts sit saturated at 4 influences with 0 at 5 (the save capping) and
# 7 light-sum verts (the same cap firing without renormalising).
#
# DEFAULT ON since 1.2 (was opt-in via CBBE2UBE_SKIN_INFLUENCE_CAP, which -- per
# the audit -- meant 59 equip-CTD-class zero-weight bones shipped in every
# default conversion while the fix sat behind a flag nobody sets). The same
# family's leg-pass fix (WEIGHT_INVARIANT) went default-ON 2026-07-26; both get
# their first full-pack playtest in the 1.2 reconvert, and both share the
# bisection story: CBBE2UBE_NO_SKIN_INFLUENCE_CAP=1 restores the previous
# main-path write exactly.
SKIN_INFLUENCE_CAP_ENABLED = not _flag("CBBE2UBE_NO_SKIN_INFLUENCE_CAP", False)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)

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
# (moved to nif_convert_weights.py, 2026-09-01)
_LEG_BEND_PROX = _knob("CBBE2UBE_LEG_BEND_PROX", 3.0)
# The leg/butt/chest match reads the body distribution over the K NEAREST body verts
# (averaged), not the single nearest. A single-nearest match flips its result on a
# sub-unit shift in the armor mesh -- and the compiled exe's warp math differs from the
# interpreted source by ~0.4u (float non-determinism), which amplified into a ~3x weaker
# leg graft (rear-thigh clip after a reconvert). Averaging over a small neighbourhood
# samples essentially the same body region either way, so the graft is stable.
_LEG_MATCH_K = _knob("CBBE2UBE_LEG_MATCH_K", 6, int)
# Z-tapered conform strength (knee crease ~z34-40, thigh ~z42-64). Full strength through
# the knee (<= _LEG_BEND_MAX_Z), ramp down to _LEG_BEND_THIGH_STRENGTH across the thigh
# (to _LEG_BEND_THIGH_Z), stop above _LEG_BEND_CUTOFF_Z (hip/butt untouched). The reduced
# thigh strength avoids over-rotating the larger-radius plate into a static bulge while
# still giving the rear thigh flex when moving.
# [DESIGN: Leg-plate bend / butt-jiggle conform]
_LEG_BEND_MAX_Z = _knob("CBBE2UBE_LEG_BEND_MAX_Z", 41.0)       # full-strength knee ceiling
_LEG_BEND_THIGH_Z = _knob("CBBE2UBE_LEG_BEND_THIGH_Z", 58.0)   # taper end (reaches min strength)
_LEG_BEND_THIGH_STRENGTH = _knob("CBBE2UBE_LEG_BEND_THIGH_STRENGTH", 0.40)  # min (thigh) strength
_LEG_BEND_CUTOFF_Z = _knob("CBBE2UBE_LEG_BEND_CUTOFF_Z", 66.0) # above this: no LEG conform (handed to the butt pass)
# BUTT pass: a RIGID one-piece plate covering the glutes (z ~60-78) often weights its
# outer butt too much to the Thigh and too little to the Pelvis vs the body, so when the
# pelvis moves the body's butt follows it but the plate lags -> the outer butt pokes
# through when moving. This is SKELETAL (the body's butt JIGGLE bones carry only ~0.03),
# so we fix it with a pure Thigh<->Pelvis REBALANCE among bones the plate ALREADY has --
# NO add_bone, NO jiggle graft (the plate stays rigid). Reduced strength for the same
# larger-radius overshoot reason as the leg. Trapezoid by world-Z (ramp in/out so no
# seam, and we never touch the lower back/spine above _BUTT_Z_HI). Tunable.
# RETIRED 2026-09-08 (plan item E1). `CBBE2UBE_NO_BUTT_MATCH` was the one
# kill switch of 76 that met all four retirement conditions: default ON,
# an IN-GAME verdict on record, unused since, and an OFF branch measured
# WORSE. Turning it off on the piece that has the verdict traded a correct
# Thigh->Pelvis drain for skirt-vs-greaves layer clipping and cost ~10
# deploy rounds; the record calls that a misdiagnosis, not a trade. The
# rebalance itself is unchanged and still tunable through the knobs below.
_BUTT_Z_LO = _knob("CBBE2UBE_BUTT_Z_LO", 60.0)     # ramp-in start
_BUTT_Z_HI = _knob("CBBE2UBE_BUTT_Z_HI", 78.0)     # ramp-out end (above = spine/back, left alone)
_BUTT_RAMP = _knob("CBBE2UBE_BUTT_RAMP", 4.0)      # ramp width at each end
_BUTT_STRENGTH = _knob("CBBE2UBE_BUTT_STRENGTH", 0.80)  # peak rebalance strength
# BUTT-JIGGLE transfer: after the skeletal Thigh<->Pelvis rebalance, the body's outer butt
# still physically JIGGLES (the NPC L/R Butt bones carry ~0.03-0.05 there) while the rigid
# plate does not -> the bounce grazes through. Graft the body's butt-jiggle weight onto the
# plate's butt so it bounces WITH the body (matched -> maintains the offset -> no clip AND
# no rest-float). MATCHED not exaggerated (the plate moves exactly as much as the bare body
# would there, so it stays subtle), and CAPPED so a high-jiggle source can't make the metal
# rubbery. Routed through the SAME add_bone save/restore + Pelvis-anchored STB graft as the
# leg detail bones. User-chosen 2026-06-30 ("jiggle with the body").
_BUTT_JIGGLE = (not _flag("CBBE2UBE_NO_BUTT_JIGGLE", False))
_BUTT_JIGGLE_BONES = ("NPC L Butt", "NPC R Butt")
_BUTT_PELVIS = "NPC Pelvis [Pelv]"                                  # the jiggle bones' graft anchor
_BUTT_JIGGLE_STRENGTH = _knob("CBBE2UBE_BUTT_JIGGLE_STRENGTH", 1.0)  # full match -> tracks body
# (moved to nif_convert_weights.py, 2026-09-01)
# The Thigh<->Pelvis REBALANCE half of the butt match is DEFAULT-ON: it is the ORIGINAL,
# proven behavior (the user-approved "looks good" leather cuirass was made with it). A
# 2026-07-08 attempt to blame it for a "coverage regression" and default it off was a
# MISDIAGNOSIS -- the real regression was in a different pass entirely.
# Opt out only if a specific armor needs it: CBBE2UBE_BUTT_REBALANCE=0.
_BUTT_REBALANCE = (_flag("CBBE2UBE_BUTT_REBALANCE", True))
# Wider than the leg prox (3.0): the outer-butt plate stands ~5u off the body, so the
# leg prox misses ~20% of the glutes. Widening here (not the leg pass) is safe -- a
# Pelvis<->Thigh rebalance can't overshoot the body's own ratio.
_BUTT_PROX = _knob("CBBE2UBE_BUTT_PROX", 5.0)
_BUTT_MATCH_BONES = ("NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC Pelvis [Pelv]")
# Chest/breast-jiggle transfer -- the upper-body mirror of the butt-jiggle graft, onto
# a rigid Spine2-dominant chest plate. Jiggle-only (no skeletal rebalance), capped low
# so a metal cuirass doesn't bounce like flesh, self-gated to the front chest. Default
# on; CBBE2UBE_NO_CHEST_JIGGLE=1 off.  [DESIGN: Leg-plate bend / butt-jiggle conform]
_CHEST_JIGGLE = (not _flag("CBBE2UBE_NO_CHEST_JIGGLE", False))
_CHEST_JIGGLE_BONES = ("L Breast01", "L Breast02", "L Breast03",
                       "R Breast01", "R Breast02", "R Breast03")
# (moved to nif_convert_weights.py, 2026-09-01)
_CHEST_Z_LO = _knob("CBBE2UBE_CHEST_Z_LO", 88.0)   # ramp-in start
_CHEST_Z_HI = _knob("CBBE2UBE_CHEST_Z_HI", 102.0)  # ramp-out end (above = neck/clav)
_CHEST_RAMP = _knob("CBBE2UBE_CHEST_RAMP", 4.0)
_CHEST_JIGGLE_STRENGTH = _knob("CBBE2UBE_CHEST_JIGGLE_STRENGTH", 1.0)
# (moved to nif_convert_weights.py, 2026-09-01)
# Per-bone clamp strictly UNDER the rigid-gate's 0.1 jiggle threshold: a single grafted
# breast bone must never reach 0.1, or a RE-RUN's rigid-gate would count the plate as
# "jiggling" and skip the whole shape (non-idempotent). The real body spreads breast weight
# across 6 bones (~0.04 each at the cap), so this only bites a pathological single-bone body.
_CHEST_JIGGLE_PERBONE = _knob("CBBE2UBE_CHEST_JIGGLE_PERBONE", 0.09)
_CHEST_PROX = _knob("CBBE2UBE_CHEST_PROX", 5.0)

# #chest-follow-ratio -- express the chest graft as a FOLLOW RATIO of the body's local
# jiggle instead of an absolute weight cap, and pick it by material.
#
# The cap above is absolute (0.15) while the body's median breast weight is 0.427, so
# armour tracks the bust at ~0.35 -- the flesh travels roughly three times as far as
# the garment covering it. The same 0.15 on the butt never binds (median butt weight
# 0.126), so the butt already tracks at 1.00. That asymmetry is emergent, not designed.
#
# Measured pack-wide with the ray metric: every shape that exposes skin under motion
# tracks at <= 0.31, and the one armour reported clean in game tracks at 1.46.
#
# Material matters because the cap has a real job on plate -- jiggle on a steel cuirass
# reads as rubber. Soft leather/cloth has no such excuse and is where the clipping is.
# Unknown material stays RIGID: the conservative direction is today's behaviour.
# DEFAULT ON since 1.2, after a full-pack conversion and in-game use. Disable
# with CBBE2UBE_NO_CHEST_FOLLOW=1 if stiff armour starts reading as rubbery.
CHEST_FOLLOW_RATIO = (
    not _flag("CBBE2UBE_NO_CHEST_FOLLOW", False))
_CHEST_FOLLOW_SOFT = _knob("CBBE2UBE_CHEST_FOLLOW_SOFT", 1.0)
_CHEST_FOLLOW_RIGID = _knob("CBBE2UBE_CHEST_FOLLOW_RIGID", 0.35)
# Material the keyword lists do NOT recognise. Its own knob rather than sharing the
# rigid one, because it is where the ceiling actually bites: of 182 bust-covering
# shapes whose derived requirement exceeds their ceiling, 129 are UNKNOWN and only 53
# are recognised metal. The commonest diffuse tokens among them are "armor", "chest",
# "body" -- no keyword can reach those, and the shader numerics were tested and do not
# separate material either (best Glossiness split scored 76.0% against a 76.3% base
# rate), so there is nothing to classify them WITH.
#
# Defaults to the rigid value, i.e. no change: the rigid ceiling is an AESTHETIC
# choice (jiggling steel looks wrong), not a measured necessity, so raising this
# trades "unlabelled armour may look rubbery" for "unlabelled armour stops clipping".
# That is the user's call to make, which is why it is a knob and not a new default.
# Reads through the helper like every other knob. It was the ONE numeric read
# the 2026-08-18 collapse left raw, and the only one in this module that still
# crashed the import when set to an empty string -- which matters because it
# HAS a GUI row (`chest_follow_unknown`), so an empty value in a hand-written
# recipe killed the run instead of falling back to the default.
_CHEST_FOLLOW_UNKNOWN = _knob("CBBE2UBE_CHEST_FOLLOW_UNKNOWN",
                              _CHEST_FOLLOW_RIGID)

# #source-follow -- classify by what the OUTFIT AUTHOR did, not by what the garment
# is called. Measured over 581 bust-covering shapes joined from converted output back
# to the source mesh that produced them:
#
#   source bust weighting | n   | output follow | requirement | short of it | skin@5u
#   ----------------------|-----|---------------|-------------|-------------|--------
#   WEIGHTED   (>= 0.5)   | 279 |     1.454     |    0.634    |    0.7%     |  0.031
#   UNWEIGHTED (<  0.5)   | 302 |     0.349     |    0.646    |   69.9%     |  0.525
#
# The requirement is the SAME for both (0.634 vs 0.646), so this is not a geometry
# difference -- it is entirely whether the author weighted the bust. Nothing repairs
# the ones they didn't, and that population IS the clipping population.
#
# Material does not separate it. Within the unweighted group the requirement is
# 0.634 / 0.662 / 0.640 for soft / rigid / unknown, and soft actually has MORE
# clearance than rigid (1.97 vs 1.55) -- the opposite of the story the ceiling tells.
#
# So a shape whose author left the bust unweighted gets its ceiling lifted and the
# DERIVED requirement is allowed to stand. Its median is 0.646 -- well under the 1.0
# that 54% of the pack already ships at, including 28% of shapes the keyword lists
# call "rigid" (chainmail, daedric plate, dwarven mail). Those reach it through their
# own source weighting, which the ceiling never touched: the ceiling caps the GRAFT,
# never the pass-through. That is why it only ever bites the garments that need help.
#
# Measured on the DESTINATION shape, before this pass grafts. That is the source's
# authored weighting as the reskin passed it through -- the very quantity the table
# above measures -- and it makes the pass idempotent: once grafted, the shape reads
# as weighted and is left alone.
# DEFAULT ON since 1.2. It only ever ADDS movement to pieces nothing was
# helping. Disable with CBBE2UBE_NO_SOURCE_FOLLOW=1.
SOURCE_FOLLOW_CEILING = (
    not _flag("CBBE2UBE_NO_SOURCE_FOLLOW", False))
# Split point. The distribution is strongly BIMODAL -- an author either copies the
# body's bust weights (landing near 1.0) or leaves the bust at 0 -- so any threshold
# in the middle separates the same populations. 0.5 takes the midpoint; it is not
# fitted, and does not need to be.
_SOURCE_WEIGHTED_MIN = _knob("CBBE2UBE_SOURCE_WEIGHTED_MIN", 0.5)
# Ceiling handed to a shape whose author left the bust unweighted. 1.0 = "let the
# derived requirement stand"; the requirement itself is what limits the graft.
_CHEST_FOLLOW_UNWEIGHTED = _knob("CBBE2UBE_CHEST_FOLLOW_UNWEIGHTED", 1.0)
# Bust verts needed before the measurement is trusted. Under this the shape is
# treated as UNKNOWN (None), which falls through to today's material ceiling rather
# than guessing from a handful of verts.
_SOURCE_FOLLOW_MIN_VERTS = 20
_BREAST_BONE_RE = re.compile(r"breast", re.I)


def _shape_bust_follow(vw, body_w, idx_k, band_idx) -> "float | None":
    """How much of the body's bust motion this shape ALREADY reproduces, over the
    bust verts it covers. None when too few verts to judge.

    Deliberately regex-matched on "breast" rather than restricted to
    `_CHEST_JIGGLE_BONES`: the question is whether the AUTHOR weighted the bust at
    all, and an author may have used a different bone naming scheme than the one this
    converter grafts. Restricting to our own bone list would read those shapes as
    unweighted and re-graft a bust that already tracks. #source-follow"""
    if len(band_idx) < _SOURCE_FOLLOW_MIN_VERTS:
        return None
    fol = []
    for i in band_idx:
        bj = sum(w for b, w in body_w[idx_k[i][0]].items()
                 if _BREAST_BONE_RE.search(b))
        if bj <= 1e-6:
            continue
        aj = sum(w for b, w in vw[i].items() if _BREAST_BONE_RE.search(b))
        fol.append(aj / bj)
    if len(fol) < _SOURCE_FOLLOW_MIN_VERTS:
        return None
    return float(np.median(fol))
# A shape counts as "already jiggling" (and so is left to the conform pass) only if
# this FRACTION of its verts carry jiggle weight, not merely 8 of them. Calibrated on
# the pack: genuinely jiggling shapes run p10 0.70% / p50 8.35%, while a graft that
# merely brushed a shape leaves 0.24-0.6%.
_CHEST_RIGID_JIGGLE_FRAC = _knob("CBBE2UBE_CHEST_RIGID_JIGGLE_FRAC", 0.01)
# GEOMETRY sets how much follow a vert gets; the material ratios above only CAP it.
#
#     body travel      = bounce x body_jiggle_weight
#     required follow  = 1 - clearance / body_travel
#
# i.e. a garment only has to track the body where the body would otherwise travel
# through it. Validated against the three armours whose in-game behaviour is known
# (p90 across the covered bust, bounce 5.0): leather needs 0.44 and has 0.24 (clips),
# hide needs 0.42 and has 0.00 (clips badly), leatherdark needs 0.49 and has 1.47
# (clean). Both clippers sit below their requirement, the clean one above it.
#
# Why the material cap SURVIVES this, despite the derivation making it look
# redundant: the tempting claim is "rigid plate stands off further, so its computed
# requirement is near zero and it is never grafted". That is not reliably true, and
# the aggregate version of it did NOT reproduce (metal clearance p50 came out 1.50,
# 4.84 and 5.15 on three different samples -- inconclusive, small n, high variance).
# A FULL-PACK CENSUS then settled it: metal and soft are not geometrically different
# at all (clearance p50 1.58 vs 1.55, required follow 0.66 vs 0.61, n=99/370). So the
# ceiling has NO geometric justification -- metal does not stand off more, and it does
# not need less. It survives on an AESTHETIC judgement: jiggling steel looks wrong, and
# without a ceiling the geometry would grant a chainmail cuirass 1.06. That is a
# legitimate reason to keep it, but it must not be presented as a measurement.
# Design bounce = the travel the LIVE physics config actually permits on the breast
# chain (measured -6.0..+3.0 linear on the largest axis), not a guess. Calibrated on
# the traced cuirass: at 5.0 the derived follow is 0.39 and 8.0% of nipple skin still
# shows at a 6u bounce; at 6.0 it is 0.53 and shows 0.4%; at 7.0 it is 0.63 for no
# further gain. 6.0 grants roughly HALF the flat 1.0 the first draft used, for the
# same coverage -- and the rubbery-armour risk scales with the excess.
_CHEST_FOLLOW_BOUNCE = _knob("CBBE2UBE_CHEST_FOLLOW_BOUNCE", 6.0)
_CHEST_FOLLOW_MARGIN = _knob("CBBE2UBE_CHEST_FOLLOW_MARGIN", 1.2)
_CHEST_JIGGLE_BONE_SET = frozenset(_CHEST_JIGGLE_BONES)

# #chain-welded-torso -- a vert driven by a CUSTOM (non-skeleton) bone is SIMULATED
# cloth, and no pass may write skeletal jiggle onto it. `_conform_weights_core` and
# `_transfer_body_jiggle_to_fitted` both enforce that already; this pass never did,
# because its shape-level gates happened to filter chain garments out first, so the
# hole was invisible. It is not invisible any more: measured over the shipped output,
# 93 shapes reach these passes carrying 33,773 chain verts inside the proximity
# window. Writing a graft onto one is the layered-cloth equip crash (C2, 2026-07-09).
#
# Default ON -- it only ever writes LESS, never more, and every other pass already
# obeys the same rule. CBBE2UBE_NO_LEG_CHAIN_GUARD=1 turns it off for bisection.
LEG_CHAIN_GUARD = (
    not _flag("CBBE2UBE_NO_LEG_CHAIN_GUARD", False))
# #chain-welded-torso -- let the leg-bend pass claim a cuirass that is WELDED into one
# shape with its own simulated skirt, by judging it on its RIGID verts.
#
# Such a shape fails every whole-shape gate for a reason that has nothing to do with
# its chest: the skirt hangs away from the body, so the whole-shape fit collapses (the
# traced studded cuirass scores 0.50 over all verts and 0.69 over the rigid ones), and
# the skirt's bone count puts chain_frac far over _CONFORM_CHAIN_MAX (0.33 vs 0.05).
# Its bust, meanwhile, is 148 verts of rigid leather sitting 1.26u off the body.
#
# Safe only because LEG_CHAIN_GUARD stops the graft at the simulated verts; without
# it this flag would write jiggle straight onto SMP cloth.
#
# DEFAULT OFF, AND UNPROVEN. Measured by source-converting the traced cuirass and six
# more chain-welded candidates: turning this on changes NOTHING -- follow ratios match
# to 1e-3 and no shape gains or loses a bone. Instrumenting `_conform_orphans_shape`
# over four real conversions found it consulted twice, claiming nothing. The defer path
# it hangs off only opens for a shape that arrives ALREADY carrying >=1% jiggle verts,
# and these arrive with almost none -- the 1.02% that suggested otherwise was measured
# on shipped OUTPUT, i.e. on jiggle this very pass had written.
#
# Kept because the reasoning holds for a shape that really does arrive jiggling, and
# because the guard below is worth having regardless. But it is NOT the fix for the
# reported clipping (that was `_CHEST_FOLLOW_UNKNOWN` / the `studded` keyword), and it
# must not be described as one. See CLIPPING_LOG.md #chain-welded-torso.
CHAIN_TORSO_CLAIM = (
    _flag("CBBE2UBE_CHAIN_TORSO", False))


def _carrier_chain_fraction(shape) -> float:
    """Fraction of a shape's verts driven by a CUSTOM (non-skeleton) bone.

    The same 0.1 threshold and the same skeleton test every other pass uses, so
    "is this vert cloth" means one thing across the converter. Returns 0.0 on any
    error -- a shape we cannot read is treated as fully rigid, which for
    #rigid-majority-softbody-gate means "do not invent physics for it", the safe
    direction (the source shipped none).
    """
    try:
        n = len(shape.verts)
        if not n:
            return 0.0
        vw = [dict() for _ in range(n)]
        for b, pairs in (shape.bone_weights or {}).items():
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n:
                    vw[iv][b] = vw[iv].get(b, 0.0) + float(w)
        return sum(_chain_vert_mask(vw, n)) / float(n)
    except Exception:
        return 0.0


def _chain_vert_mask(vw, n) -> "list":
    """Per-vert: is this vertex driven by a CUSTOM (non-skeleton) bone, i.e. is it
    SIMULATED cloth rather than skinned armour? Same 0.1 threshold every other pass
    uses, so all of them agree on which verts are cloth. #chain-welded-torso"""
    return [any(w > 0.1 and not _is_skeleton_bone(b) for b, w in vw[i].items())
            for i in range(n)]


def _conform_orphans_shape(shape, vw, n, d, collider_names, softbody_names,
                           layered_cloth_names, is_chain=None,
                           piece_has_hdt_xml=None) -> bool:
    """True when `_conform_weights_core` will NOT take this shape, so the leg-bend
    pass deferring to it ("you already jiggle, the conform handles you") drops the
    shape on the floor and NOBODY grafts it.

    That coverage hole is real and it is what left the traced leather cuirass at a
    follow ratio of 0.34 against a requirement of 0.81: 34 of its 3339 verts carry
    jiggle (1.02%, just over the ratio-mode floor) so the graft deferred, and its
    whole-shape fit is 0.501 against the conform's 0.90 so the conform declined.

    Deliberately conservative -- returns False (do not claim the shape) for every
    reason BOTH passes agree to skip. It claims a shape only when the conform's
    "hugs the body" fit gate is the sole thing rejecting it, which is the orphan
    case. Mirrors `_conform_weights_core`'s gates (a)-(d); if those change, this
    must follow. #conform-coverage-hole"""
    nm = (shape.name or "").lower()
    if (shape.name in collider_names or shape.name in softbody_names
            or shape.name in layered_cloth_names
            or any(k in nm for k in _conform_skip_keys(piece_has_hdt_xml))):
        return False        # neither pass touches these -- not ours to claim
    if n <= 0:
        return False
    if is_chain is None:
        is_chain = _chain_vert_mask(vw, n)
    chain = sum(1 for c in is_chain if c) / n
    if chain > _CONFORM_CHAIN_MAX:
        # #chain-welded-torso. A cuirass welded into one shape with its own simulated
        # skirt lands here, and used to be refused outright. Judge it on its RIGID
        # verts instead -- the skirt is what fails the gate, not the chest.
        #
        # Note the fit test flips direction. Above, "orphan" means the conform's 0.90
        # gate REJECTED the shape, so a LOW fit is the qualifier. Here the conform
        # rejected it on the chain gate regardless of fit, so the only question left
        # is whether this pass should want it -- and for that a HIGH rigid fit is the
        # qualifier. Same threshold `_transfer_body_jiggle_to_fitted`'s torso_mode
        # uses, calibrated on 134 torso shapes (capes/scarves 0.00-0.03, fitted
        # chest pieces 1.00).
        if not (CHAIN_TORSO_CLAIM and LEG_CHAIN_GUARD):
            return False    # without the per-vert guard this would graft onto cloth
        rigid = [i for i in range(n) if not is_chain[i]]
        if not rigid or not _shape_is_rigid_torso_armor(shape):
            return False    # free-hanging cloth, not a fitted torso piece
        return float((d[rigid] < _CONFORM_FIT_PROX).mean()) >= _TORSO_JIGGLE_FIT_FRAC
    return float((d < _CONFORM_FIT_PROX).mean()) < _CONFORM_FIT_FRAC


def _chest_follow_required(clearance: float, body_jiggle: float) -> float:
    """How much of the body's motion this vert must reproduce to stay covered.

    0 when the body cannot reach the garment (large clearance or little jiggle);
    rises toward 1 as the travel exceeds the gap. Includes a safety margin -- the
    bounce figure is a design value, not a guarantee. #chest-follow-ratio"""
    travel = _CHEST_FOLLOW_BOUNCE * max(0.0, body_jiggle)
    if travel <= 1e-6:
        return 0.0
    return max(0.0, (1.0 - max(0.0, clearance) / travel)) * _CHEST_FOLLOW_MARGIN
# Keyword sets read from the shape name AND its diffuse path. Grounded in the real
# texture paths of a large armour list -- the recurring shapes are `*leather*.dds`,
# `armor/hide/f/cuirasslight.dds`, `*underwear*.dds`, `*_dress_diffuse.dds`.
_SOFT_MATERIAL_KEYS = (
    "leather", "hide", "cloth", "fabric", "linen", "silk", "dress", "robe", "gown",
    "underwear", "corset", "bra", "bikini", "fur", "tunic", "shirt", "wrap",
    "sarashi", "vest", "padded", "rag", "sash",
    # "studded" is the vanilla light-armour set: leather with metal studs on it, not
    # a plate. It was landing in neither list, so it took the unknown default and the
    # traced cuirass sat at a 0.35 ceiling against a 0.81 requirement. The
    # rigid-wins-over-soft rule still keeps "steel-studded" stiff.
    "studded",
)
_RIGID_MATERIAL_KEYS = (
    "steel", "iron", "ebony", "dwarven", "daedric", "orcish", "glass", "elven",
    "plate", "chitin", "bone", "metal", "mail", "silver", "gold", "brass", "plaid",
)


def _chest_follow_for_shape(shape, source_weighted=None) -> float:
    """Follow ratio for this shape's chest graft: how much of the body's local breast
    jiggle it should reproduce. RIGID unless the material says otherwise.

    `source_weighted` (#source-follow) is the measured answer to "did the outfit
    author weight this garment's bust", or None when there were too few bust verts to
    tell. False lifts the ceiling: that shape is in the population where the graft is
    the only thing standing between the body and daylight, and its geometric
    requirement is the same whatever the garment is made of. True and None fall
    through to the material ceiling below, so the flag can only ever ADD grafting to
    shapes nothing was helping.

    Reads the shape name and its DIFFUSE texture path only. A RIGID keyword wins over
    a soft one -- "steel-studded leather" should stay stiff.

    Diffuse only, and that is load-bearing: reading every texture slot misclassified
    real armour twice. A leather cuirass matched "steel" from its ENVIRONMENT map
    (`cubemaps/steel_e.dds`, a sheen asset), and a dress matched "metal" from its PBR
    map (`*_dress_metallic.dds`, a shader input). Neither says anything about the
    garment's material; the diffuse does. #chest-follow-ratio"""
    if SOURCE_FOLLOW_CEILING and source_weighted is False:
        return _CHEST_FOLLOW_UNWEIGHTED
    hay = (getattr(shape, "name", "") or "").lower()
    try:
        tex = shape.textures or {}
        dif = tex.get("Diffuse") or tex.get(0) or ""
        if dif:
            hay += " " + str(dif).lower()
    except Exception:
        pass
    if any(k in hay for k in _RIGID_MATERIAL_KEYS):
        return _CHEST_FOLLOW_RIGID
    if any(k in hay for k in _SOFT_MATERIAL_KEYS):
        return _CHEST_FOLLOW_SOFT
    return _CHEST_FOLLOW_UNKNOWN
_BODY_CONFORM_CACHE: "dict" = {}
_BODY_LEG_DETAIL_CACHE: "dict" = {}


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


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
    except Exception as _be:
        out = None
        print(f"  WARN: body-conform reference failed to load ({_be!r})",
              file=sys.stderr)
    if out is None:
        # Cached None = conform, bust-split and jiggle transfer are ALL
        # silently disabled for every piece at this weight for the rest of
        # the run, while each conversion still reports success (audit
        # 2026-07-28, fail-dangerous class). One loud line per weight.
        print(f"  WARN: no UBE body reference for weight {weight!r} -- the "
              f"body-follow repair layer (conform / bust split / jiggle "
              f"transfer) is OFF for this run", file=sys.stderr)
    _BODY_CONFORM_CACHE[weight] = out
    return out


_BODY_JIGGLE_REF_CACHE: dict = {}
# The body's STB TransformBufs are native references INTO its NifFile; keep the
# NifFile alive (the read alone would let it be GC'd, leaving the cached STBs
# dangling -> they set identity -> the grafted bone spikes to the origin).
_BODY_JIGGLE_NF_KEEPALIVE: list = []


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


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
    out = {b: w / ss for b, w in new.items() if w / ss > 1e-4}
    if BREAST_FOLLOW_KEEP:
        # CULPRIT 1 of the authored-breast-follow loss: this blend drags an
        # inner layer's breast share down to the BODY's. #breast-follow-keep
        out = _keep_breast_share(dv, out)
    return out


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# ---- Warp-introduced torso self-intersection repair -----------------------
#
# The per-vert body-delta warp moves an inner cloth surface (blouse, hugging the
# body) outward MORE than the outer surface (corset) it sits under -- the inner
# tracks the bustier UBE more -- so the two layers CROSS and the inner pokes
# through the outer. The inter-shape layer passes (_separate_chest_layered_cloth_depth)
# only separate DISTINCT shapes; a merged single-shape outfit (or a self-crossing
# corset shape) has no partner to rank against, so nothing catches it.
#
# On-disk POST-pass (runs after conform/leg-bend, alongside _conform_fitted_to_body):
# detect self-intersecting triangle pairs in the torso band, push the inner triangle
# IN (toward the body) and the outer OUT, clamp so no moved vert sinks below the body
# standoff, iterate until the count reaches the SOURCE baseline. The source baseline
# is the gate that leaves BY-DESIGN self-intersecting geometry (fur, layered strands)
# alone: fur self-intersects in the source too, so its target is already met and the
# pass does nothing; real cloth is clean in the source (target ~0) so it is repaired.
# Physics-chain verts (SMP softbody) never move -- only the static torso layers do.
# Measured offline: a layered dress 26->10, a corset top 4197->1148, a corset
# 2569->675; body clearance held, fur untouched. Opt out with
# CBBE2UBE_NO_SELFINT_REPAIR=1; band + gate tunable via CBBE2UBE_SELFINT_*.
SELFINT_REPAIR = os.environ.get(
    "CBBE2UBE_NO_SELFINT_REPAIR", "").strip().lower() not in ("1", "true", "yes")
# Feathering rounds for the self-int separation step. Keeps the push continuous so
# separating layers doesn't spike the mesh. 4 by SWEEP (0/1/2/4) on a bust top + a
# cuirass cord shape: the cord's max edge jump falls 2.19 -> 1.09 (-50%) AND the
# crossings it resolves IMPROVE (368 -> 248 remaining) -- a coherent push separates
# better than a jittery one, so there's no trade here. 1 is a no-op (the neighbour
# blend needs >=2 rounds to propagate). 0 restores the old raw behaviour.
_SELFINT_SMOOTH = int(os.environ.get("CBBE2UBE_SELFINT_SMOOTH", "4") or "4")
_SELFINT_ZLO = _knob("CBBE2UBE_SELFINT_ZLO", 88.0)
_SELFINT_ZHI = _knob("CBBE2UBE_SELFINT_ZHI", 116.0)
# Source self-intersection >= this = by-design (fur/strands) -> leave the shape alone.
_SELFINT_FUR_GATE = _knob("CBBE2UBE_SELFINT_FUR_GATE", 300, int)
# Absolute safety net: a real warp clip is tens-to-hundreds of crossings (worst cloth
# seen ~4200); thousands means by-design self-intersecting geometry (fur/strands).
# Skip WITHOUT the source-baseline check -- that gate fails OPEN on a topology/name
# mismatch (would then repair fur), and the source scan is costly on 50k-tri fur.
_SELFINT_MAX_CROSSINGS = _knob("CBBE2UBE_SELFINT_MAX", 10000, int)
_SELFINT_MIN = 5           # skip shapes with fewer than this many output crossings
# Hard safety cap. Iterate to CONVERGENCE (crossings <= source baseline), not to a
# fixed count -- the old 16-cap plateaued far above what the same algorithm reaches
# given more rounds (a layered dress 26->20 at 16 iters, 26->3 run to convergence). The
# stall early-out below usually stops well before this cap.
_SELFINT_ITERS = _knob("CBBE2UBE_SELFINT_ITERS", 45, int)
_SELFINT_STEP = 0.20
_SELFINT_STANDOFF = 0.3    # keep every moved vert this far above the body
_SELFINT_STALL_ROUNDS = 4    # rounds with no NEW best crossing count -> stop (dense mesh floor)

_BODY_SELFINT_CACHE: dict = {}


def _body_selfint_ref(weight: str):
    """(body_verts, body_normals, kdtree) for the UBE body at `weight`, or None if
    unavailable or the body's global-to-skin isn't identity (a raw-space compare would
    then be wrong -- skip rather than mis-measure). Cached."""
    if weight in _BODY_SELFINT_CACHE:
        return _BODY_SELFINT_CACHE[weight]
    out = None
    try:
        from scipy.spatial import cKDTree
        p = _find_ube_femalebody(weight) or _find_ube_femalebody("_1")
        if p is not None and Path(p).is_file():
            pyn = _pynifly()
            nf = pyn.NifFile(filepath=str(p))
            body = max(nf.shapes, key=lambda s: len(s.verts))
            g2s = _shape_global_to_skin(body)
            if g2s is None or _g2s_is_identity(g2s):
                V = np.asarray(body.verts, np.float64)
                N = _body_normals_or_compute(body)
                if N is not None:
                    out = (V, np.asarray(N, np.float64), cKDTree(V))
    except Exception:
        out = None
    _BODY_SELFINT_CACHE[weight] = out
    return out


def _self_intersecting_pairs(v, tris, zlo=None, zhi=None, k=10):
    """Indices (into `tris`) of self-intersecting triangle PAIRS within the [zlo,zhi]
    z-band, excluding topological (shared-vertex) neighbours. k-NN prefiltered,
    vectorized strict-interior segment/triangle test (Ericson/Moller-Trumbore)."""
    from scipy.spatial import cKDTree
    zlo = _SELFINT_ZLO if zlo is None else zlo
    zhi = _SELFINT_ZHI if zhi is None else zhi
    cen = v[tris].mean(1)
    band = np.where((cen[:, 2] >= zlo) & (cen[:, 2] <= zhi))[0]
    if len(band) < 20:
        return np.zeros((0, 2), int)
    bc = cen[band]
    _, nn = cKDTree(bc).query(bc, k=min(k, len(band)))
    I = np.repeat(np.arange(len(band)), nn.shape[1])
    J = nn.ravel()
    m = I < J
    I, J = I[m], J[m]
    A = tris[band[I]]
    B = tris[band[J]]
    keep = ~((A[:, :, None] == B[:, None, :]).any(2).any(1))
    I, J = I[keep], J[keep]
    if len(I) == 0:
        return np.zeros((0, 2), int)

    def _seg_tri(P, Q, TA, TB, TC):
        D = Q - P
        e1 = TB - TA
        e2 = TC - TA
        h = np.cross(D, e2)
        det = np.einsum('ij,ij->i', e1, h)
        ok = np.abs(det) > 1e-12
        f = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = P - TA
        u = f * np.einsum('ij,ij->i', s, h)
        q = np.cross(s, e1)
        vv = f * np.einsum('ij,ij->i', D, q)
        t = f * np.einsum('ij,ij->i', e2, q)
        return (ok & (u >= -1e-6) & (u <= 1 + 1e-6) & (vv >= -1e-6)
                & (u + vv <= 1 + 1e-6) & (t > 1e-4) & (t < 1 - 1e-4))

    a = v[tris[band[I]]]
    b = v[tris[band[J]]]
    hit = np.zeros(len(I), bool)
    for e0, e1 in ((0, 1), (1, 2), (2, 0)):
        hit |= _seg_tri(a[:, e0], a[:, e1], b[:, 0], b[:, 1], b[:, 2])
        hit |= _seg_tri(b[:, e0], b[:, e1], a[:, 0], a[:, 1], a[:, 2])
    return np.stack([band[I[hit]], band[J[hit]]], 1)


def _relax_shape_self_intersection(V, tris, chain_vert, Vb, Nb, tree, target):
    """Separate self-intersecting torso triangles: inner tri pushed IN (toward the
    body), outer OUT (gentler), clamped so no moved vert sits below _SELFINT_STANDOFF
    above the body. `chain_vert` (bool per vert) marks SMP physics-chain verts that
    must NOT move. Returns (new_verts, moved_vert_count). Deterministic."""
    v = V.copy()
    moved_any = np.zeros(len(v), bool)
    # Track the BEST (lowest) crossing count seen and return that state -- the
    # relaxation can briefly bump a round UP before resolving it, so returning the
    # final state (or bailing on a single up-round) leaves reduction on the table.
    best_v = v.copy()
    best_moved = moved_any.copy()
    initial_c = best_c = len(_self_intersecting_pairs(v, tris))
    no_improve = 0
    for _ in range(_SELFINT_ITERS):
        cr = _self_intersecting_pairs(v, tris)
        c = len(cr)
        if c < best_c:
            best_c, best_v, best_moved = c, v.copy(), moved_any.copy()
            no_improve = 0
        elif best_c < initial_c:
            # Only count a stall once we've made real progress. The relaxation can
            # RISE for several rounds before it comes down (an initial hump), and
            # cutting off during that leaves all the reduction on the table.
            no_improve += 1
        if best_c <= target:
            break
        # EARLY-OUT: a dense torso mesh hits a floor the push-relaxation can't clear
        # (deeply interlocked crossings). Stop after a few no-new-best rounds so we
        # don't grind at the floor; mild meshes keep finding new bests to ~target.
        if no_improve >= _SELFINT_STALL_ROUNDS:
            break
        _, vbi = tree.query(v)                 # nearest body vert per garment vert
        cen = v[tris].mean(1)
        bd, _ = tree.query(cen)                # tri-centroid distance to body
        move = np.zeros_like(v)
        cnt = np.zeros(len(v))
        for i, j in cr:
            inner, outer = (i, j) if bd[i] <= bd[j] else (j, i)
            for vi in tris[inner]:
                if not chain_vert[vi]:
                    move[vi] -= Nb[vbi[vi]] * _SELFINT_STEP
                    cnt[vi] += 1
            for vo in tris[outer]:
                if not chain_vert[vo]:
                    move[vo] += Nb[vbi[vo]] * _SELFINT_STEP * 0.6
                    cnt[vo] += 1
        m = cnt > 0
        if not m.any():
            break
        step = np.zeros_like(v)
        step[m] = move[m] / cnt[m, None]
        # FEATHER the separation step over the mesh. Pushing only the verts of a
        # crossing triangle, and leaving their untouched neighbours behind, IS a
        # crinkle -- this pass moves thousands of verts (measured: 5879 on one
        # cuirass cord shape) and roughly DOUBLES the displacement's max edge jump
        # (0.59 -> 1.21 on a bust top) purely from that discontinuity. The spiky-vert
        # artifact is the pipeline ACCUMULATING these unsmoothed per-vert pushes, so
        # each one must move its neighbourhood with it. Chain verts stay pinned: they
        # are zeroed after smoothing so a physics rest pose is never disturbed.
        try:
            step = _smooth_vertex_field(step, tris, iters=_SELFINT_SMOOTH, verts=V)
            step[chain_vert] = 0.0
        except Exception as _pe:
            _note_pass_failure("_smooth_vertex_field", _pe)
        v = v + step
        moved_any |= m
        # body-safety clamp: a moved vert must never end up below the standoff
        _, vbi2 = tree.query(v)
        signed = np.einsum('ij,ij->i', v - Vb[vbi2], Nb[vbi2])
        under = (signed < _SELFINT_STANDOFF) & moved_any
        if under.any():
            v[under] = Vb[vbi2][under] + Nb[vbi2][under] * _SELFINT_STANDOFF
    return best_v, int(best_moved.sum())


def _selfint_overrides(nf, dst_path, src_path) -> dict:
    """Compute {shape_name -> relaxed verts} that un-cross warp-introduced torso layer
    self-intersections, on an ALREADY-LOADED nf. No I/O except reading the SOURCE nif
    for the baseline gate. Returns {} if nothing to do; the caller persists in ONE
    _reauthor_nif_fresh (which recomputes normals). Gated by the SOURCE self-int
    baseline so by-design fur/strand geometry is left alone. See SELFINT_REPAIR."""
    if not SELFINT_REPAIR:
        return {}
    from scipy.spatial import cKDTree
    # Body reference for the inner/outer test + the standoff clamp: prefer the NIF's
    # OWN injected BaseShape -- the actual body the garment covers, so clamping against
    # it can't leave a vert below the visible body. Fall back to the reference UBE body
    # for plain-armor NIFs that carry no BaseShape.
    body = None
    _base = next((s for s in nf.shapes if s.name == "BaseShape"), None)
    if _base is not None:
        _g2sb = _shape_global_to_skin(_base)
        if _g2sb is None or _g2s_is_identity(_g2sb):
            _Vb = np.asarray(_base.verts, np.float64)
            _Nb = _body_normals_or_compute(_base)
            if _Nb is not None:
                body = (_Vb, np.asarray(_Nb, np.float64), cKDTree(_Vb))
    if body is None:
        weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
        body = _body_selfint_ref(weight)
    if body is None:
        return {}
    Vb, Nb, tree = body
    collider_names = _hdt_collider_shape_names(dst_path, nif=nf)
    src_shapes: dict = {}
    try:
        snf = _pynifly().NifFile(filepath=str(src_path))
        for s in snf.shapes:
            src_shapes[s.name] = (np.asarray(s.verts, np.float64),
                                  np.asarray(s.tris, np.int64))
    except Exception:
        src_shapes = {}
    overrides: dict = {}
    for s in nf.shapes:
        nm = s.name or ""
        if (nm in ("BaseShape", "VirtualBody", "VirtualGround")
                or nm in collider_names or nm.lower().startswith("col")):
            continue
        g2s = _shape_global_to_skin(s)
        if not (g2s is None or _g2s_is_identity(g2s)):
            continue  # non-identity scale/translation -> raw-space compare wrong; skip
        try:
            V = np.asarray(s.verts, np.float64)
            T = np.asarray(s.tris, np.int64)
        except Exception:
            continue
        if len(V) < 30 or int(((V[:, 2] >= _SELFINT_ZLO)
                               & (V[:, 2] <= _SELFINT_ZHI)).sum()) < 30:
            continue
        out_cr = len(_self_intersecting_pairs(V, T))
        if out_cr < _SELFINT_MIN or out_cr >= _SELFINT_MAX_CROSSINGS:
            continue  # too few to matter, or by-design fur/strands (see the constant)
        # SOURCE baseline (same topology): fur self-intersects here too -> high
        # target -> the shape is left untouched. Real cloth is clean -> target ~0.
        S = 0
        src = src_shapes.get(nm)
        if src is not None and len(src[0]) == len(V) and len(src[1]) == len(T):
            S = len(_self_intersecting_pairs(src[0], src[1]))
        if S >= _SELFINT_FUR_GATE:
            continue
        target = max(S, 3)
        if out_cr <= target:
            continue
        # SMP physics-chain verts must not move (they drive the softbody rest pose)
        chain_vert = np.zeros(len(V), bool)
        for b, pairs in (s.bone_weights or {}).items():
            if not _is_skeleton_bone(b):
                for vi, w in pairs:
                    iv = int(vi)
                    if 0 <= iv < len(V) and float(w) > 0.1:
                        chain_vert[iv] = True
        Vr, moved = _relax_shape_self_intersection(V, T, chain_vert, Vb, Nb, tree, target)
        if moved:
            # The push-relaxation moves verts along the body normal with no
            # degenerate-tri guard, so a thin fabric fold (front + back sheet ~one
            # step apart) can be pinched flat -- two verts snapped coincident, a
            # zero-area sliver that renders as a black "malformed underside". This
            # post-save pass runs AFTER the pre-save degenerate-tri repair, so
            # nothing else catches it. Un-pinch any op-collapsed tri back to its
            # source-relative shape at the relaxed position (same repair; small
            # moves that restore thickness without re-crossing). Source-degenerate
            # folds are left alone by the repair's own source-area gate.
            # #selfint-collapse-guard
            if src is not None and len(src[0]) == len(Vr) and len(src[1]) == len(T):
                try:
                    Vr, _nfix = repair_collapsed_tris(
                        np.asarray(Vr, np.float64), src[0], T)
                except Exception as _e:
                    # best-effort; leave relaxed verts as-is on failure -- but
                    # recorded, so a repair that always throws is visible.
                    _note_pass_failure("repair_collapsed_tris/selfint", _e)
            overrides[nm] = Vr
    return overrides


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


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


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


# How far under its requirement a shape must sit before this pass refuses to
# hand it to the conform pass. A margin, not zero: the requirement carries its
# own safety factor and the follow measurement has vert-level noise, so a shape
# a hair short is not worth re-claiming. #chest-follow-passthrough
_CHEST_FOLLOW_SHORTFALL = _knob("CBBE2UBE_CHEST_FOLLOW_SHORTFALL", 0.05)


def _chest_band(n, d, idx_k, body_w, is_chain) -> list:
    """Vert indices this pass judges the bust on: close to the body, not
    simulated cloth, and over body that actually jiggles. Shared by the
    requirement, the source-follow measurement and the achieved-follow check so
    all three describe the SAME surface."""
    out = []
    for i in range(n):
        if d[i] > _CHEST_PROX:
            continue
        if LEG_CHAIN_GUARD and is_chain[i]:
            continue        # a vert the graft will refuse must not set its size
        bj = sum(w for b, w in body_w[idx_k[i][0]].items()
                 if b in _CHEST_JIGGLE_BONE_SET)
        if bj > 0.05:
            out.append(i)
    return out


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# #morphtri-no-leg-graft. Skip the leg-detail-bone graft on a shape that owns a
# source morph TRI -- it already tracks body sliders, and the graft makes a thin
# rim follow body scale bones per-animation. CBBE2UBE_MORPHTRI_LEG_GRAFT=1
# restores the old behaviour.
MORPHTRI_NO_LEG_GRAFT = (
    not _flag("CBBE2UBE_MORPHTRI_LEG_GRAFT", False))

# #morphtri-keep-jiggle. The gate above is RIGHT for the LEG DETAIL bones and
# WRONG for the jiggle bones; the two were only ever coupled by sharing a
# predicate.
#
# WHAT THE IN-GAME DEFECT ACTUALLY WAS: a flap tip bending per-animation,
# measured as `R/L RearCalf 0.00% -> ~1.26%` -- LEG DETAIL bones landing on a
# thin cloth rim at CALF height. Breast/butt/belly bones anchor at Spine2 and the
# Pelvis and carry no weight down there, so gating THEM was collateral damage,
# not the fix.
#
# WHAT IT COST: a census over the converted pack measured the gate reaching 88%
# of shapes -- 94% of a BodySlide-built modlist ships a morph TRI -- and
# stripping breast/butt/belly from ~1184 shapes that had NO authored jiggle of
# their own. That population is exactly the one #source-follow identifies as the
# CLIPPING population: shapes whose author left the bust unweighted track at
# 0.349 against a 0.646 requirement, 69.9% of them short, skin-at-5u 0.525
# against 0.031 for the weighted group. Denying them the graft removes the only
# thing that repairs it.
#
# So the morph-TRI predicate still gates the leg-detail graft and the limb-motion
# matches (which move WEIGHT, not bone sets), and no longer gates the jiggle
# transfer. That pass already grafts ONLY regions that LACK jiggle
# (#region-jiggle-gate), so it cannot double up on a shape whose author weighted
# the bust. CBBE2UBE_MORPHTRI_GATE_JIGGLE=1 restores the coupled behaviour.
MORPHTRI_KEEP_JIGGLE = (
    not _flag("CBBE2UBE_MORPHTRI_GATE_JIGGLE", False))


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# ---- Cross-shape coincident-vertex skin unification -----------------------
#
# REPORTED IN GAME on a converted romper: "the belt and belt buckle verts pull
# in different directions and look to be weighed differently". Both halves were
# right. Verts that TOUCH but belong to DIFFERENT shapes come out skinned
# differently, so neighbouring parts of one garment shear apart the moment the
# skeleton moves. It survived a whole session of fit work because every metric
# in the project compares ONE shape against its own author -- not one shape
# against ANOTHER.
#
# MEASURED on that piece (verts coincident within 0.15u; L1 summed over the bone
# axis, so 0 = identically skinned and 2 = no bone in common):
#
#     pair                          touching  L1 med  L1 p90  >0.5  worst bone
#     OURS    3ButtonsWaist/3RopeTied     77   0.207   1.115    32  NPC Pelvis
#     AUTHOR  3ButtonsWaist/3RopeTied     71   0.000   0.061     0  NPC Spine1
#
# The author has ZERO verts above 0.5 on ANY pair; we shipped 73 across four. The
# worst-divergent bones are PELVIS vs SPINE1/SPINE2 -- neighbouring parts of one
# belt following different sections of the skeleton, which is literally "pulling
# in different directions", and it is why the belts read correct in some poses
# only: at another pose the disagreement cancels differently.
#
# CAUSE, traced by scoring the WRITTEN nif after every weight pass rather than
# deducing it (`shear_trace.py`):
#     _conform_fitted_to_body           0 verts over L1 0.5
#     _match_rigid_leg_bend_to_body     7
#     _match_full_weights_to_body      73        <- the bulk
# Both pair each garment vert to a BODY vert independently PER SHAPE -- by KD, or
# by a ray along that vert's OWN normal. Two verts at the same position in
# different shapes have different normals, so they hit different body triangles,
# copy different body rows, and are then capped to 4 bones and renormalised
# differently. The author skinned them alike because they were authored as one
# garment. Nothing downstream can undo it, so the repair belongs after them all.
#
# THE CLASS PROPERTY, falsifiable with no in-game verdict: coincident verts
# across shapes must carry IDENTICAL weights, with the author's own divergence
# as the reference.
#
# FIX: join cross-shape coincident verts into clusters and give each cluster ONE
# row. Two things make that a RESTORATION rather than an invention:
#
#   * THE EDGE GATE IS THE AUTHOR'S OWN AGREEMENT. Two verts are joined only if
#     the author skinned them alike (L1 <= _COINCIDENT_SKIN_GATE), so a genuine
#     authored skin discontinuity is never welded shut. Gating per EDGE rather
#     than per CLUSTER is load-bearing: union-find chains transitively, so one
#     discontinuity anywhere in a chain would veto the whole cluster -- and those
#     are exactly the clusters we diverge on most (measured on the reported
#     piece: per-cluster gating left 40 verts over 0.5, per-edge left 5).
#     A shape that cannot be paired to its source shape is excluded outright:
#     with no authored answer there is no gate, and unifying on faith is how a
#     safety rail becomes decoration.
#
#   * THE ROW IS AVERAGED OVER THE INTERSECTION of the member shapes' bone
#     palettes, so no shape ever gains a bone -- no `add_bone`, hence none of the
#     add_bone-resets-every-STB class. A cluster the shared palette cannot carry
#     (< _COINCIDENT_SKIN_MIN_SHARE of EVERY member's row) is left alone rather
#     than unified by discarding weight.
#
# MEASURED on the reported piece at the pack recipe:
#     verts over L1 0.5    73 -> 5        (author: 0)
#     worst pair median    0.207 -> 0.029 (author: 0.149)
# with an INDEPENDENT counter-metric, because "the rows now agree" is the
# quantity this pass optimises and quoting it as evidence would be a tautology:
# mean L1 to the AUTHOR's own row for the same vertex went 0.2681 -> 0.2430 over
# 3161 repaired verts. The unified rows move TOWARD the author, not off into
# something invented.
#
# Coincidence is measured on the OUTPUT -- both the population the defect lives
# in and the one the acceptance test measures. Measuring it in SOURCE space
# instead structurally misses the verts the warp brings together (measured: two
# of the five worst verts on the reported pair sit 0.42u apart in the source) and
# scored worse on every axis. Safety comes from the author gate, not from the
# positions.
#
# CBBE2UBE_NO_COINCIDENT_SKIN=1 turns it off. #coincident-skin-match
COINCIDENT_SKIN_MATCH = (
    not _flag("CBBE2UBE_NO_COINCIDENT_SKIN", False))
# The acceptance test's own coincidence radius -- the repair covers exactly the
# population the metric judges.
_COINCIDENT_SKIN_TOL = float(
    os.environ.get("CBBE2UBE_COINCIDENT_SKIN_TOL", "0.15") or "0.15")
# How far the AUTHOR may disagree across an edge before it reads as a deliberate
# skin boundary. The author's own cross-shape agreement on the reported piece is
# median 0.005 / p90 0.074, so 0.10 sits above the noise and below any real
# discontinuity.
_COINCIDENT_SKIN_GATE = float(
    os.environ.get("CBBE2UBE_COINCIDENT_SKIN_GATE", "0.10") or "0.10")
# Fraction of EVERY member's row the shared palette must carry.
_COINCIDENT_SKIN_MIN_SHARE = float(
    os.environ.get("CBBE2UBE_COINCIDENT_SKIN_MIN_SHARE", "0.75") or "0.75")
# #rigid-part-skin -- a welded part the AUTHOR skinned as one unit must stay one
# unit. Off with CBBE2UBE_NO_RIGID_PART_SKIN=1. See the block inside the pass.
RIGID_PART_SKIN_MATCH = (
    not _flag("CBBE2UBE_NO_RIGID_PART_SKIN", False))
# How far the AUTHOR's own rows may vary inside a part before it counts as
# DEFORMABLE and is left alone. The measured separation is wide: the reported
# buttons/buckles sit at 0.02-0.11 in the author while `3Fabric` -- cloth that
# IS supposed to deform -- sits at 0.43-0.58, so 0.15 splits them cleanly and
# sits nowhere near either edge.
_RIGID_PART_GATE = float(
    os.environ.get("CBBE2UBE_RIGID_PART_GATE", "0.15") or "0.15")
# Below this a "part" is a stray sliver, not an ornament worth rigidifying.
_RIGID_PART_MIN_VERTS = int(
    os.environ.get("CBBE2UBE_RIGID_PART_MIN_VERTS", "8") or "8")
# DO NOT REBUILD `#rigid-part-cap` HERE -- it was written, measured and DELETED.
#
# It bounded a part's deformation by pulling every row toward the part MEAN by
# `t = 1 - author/ours`, which scales every pairwise L1 by exactly (1 - t). The
# operation is exact; the TARGET was wrong. Aiming at the mean aims at ZERO
# variation, so it fixed the reported buckles by freezing the fabric:
#
#     3Fabric        6.49 -> 3.73     <- CLOTH, far stiffer than authored
#     4Panties       2.67 -> 0.83
#     total |ours-author|  29.45 -> 34.11  (over 21.65 -> 0.10,
#                                           under  7.79 -> 34.01)
#
# It traded a modest over-deformation for a large UNDER-deformation, and fabric
# that will not move is a worse defect than a buckle that moves a little too
# much. `#author-deviation-skin` below supersedes it by aiming at the AUTHOR'S
# OWN deviation instead of at zero, which is what makes cloth a fixed point.
# #part-pair-align -- two ADJACENT parts must not diverge in MEAN follow more
# than the author had them. Off with CBBE2UBE_NO_PART_PAIR_ALIGN=1.
PART_PAIR_ALIGN = (
    not _flag("CBBE2UBE_NO_PART_PAIR_ALIGN", False))
# Closest approach at which two parts count as adjacent.
_PART_PAIR_NEAR = float(
    os.environ.get("CBBE2UBE_PART_PAIR_NEAR", "1.0") or "1.0")
# Headroom over the author before a pair is pulled back together.
_PART_PAIR_MARGIN = float(
    os.environ.get("CBBE2UBE_PART_PAIR_MARGIN", "0.10") or "0.10")
# #author-deviation-skin -- a part deforms INTERNALLY the way its author made it
# deform. Off with CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN=1.
#
# This is the answer to the defect `#rigid-part-cap` above could not solve
# without wrecking cloth, and WHAT IT AIMS AT is the whole difference. The cap
# pulled rows toward the part MEAN -- toward ZERO internal variation -- with the
# author's spread only choosing how far, so everything it touched got stiffer
# and fabric stopped moving. This TRANSPLANTS the author's own deviation:
#
#     row_i := our_part_mean + (author_i - author_part_mean)
#
# Our mean says WHERE the part rides: it carries the UBE retarget, the fit chain
# and `#part-pair-align`, all of which are right and none of which the author
# knows about. The author's deviation says HOW the part deforms about that mean.
# Neither term is a compromise between the two rigs -- each is taken from
# whichever rig is authoritative for it
# ([[feedback_preserve_authored_relationships]]).
#
# So the target is the author's variation, not zero, and cloth is a FIXED POINT
# rather than a casualty. Measured on the reported piece, summed intra-part
# spread, against the same scorekeeper that condemned the cap:
#
#     3Fabric        author  6.49   ours 14.71   ->  6.71   (cap gave 3.73)
#     3ButtonsWaist  author 10.09   ours 12.24   ->  6.51
#     3Belts         author  6.68   ours  8.96   ->  6.21
#     total |x - author|    13.92  ->  7.17      (cap: 29.45 -> 34.11)
#
# Per fired part it lands ON the author, not under -- the worst offenders read
# author 0.179 / ours 1.776 -> 0.178 and author 0.369 / ours 1.437 -> 0.409.
# The residual shape-level gap is parts where we already vary LESS than the
# author (sub-1u studs, 0.030 vs 0.098) and which this pass never touches: it
# only ever fires where we OVER-deform.
AUTHOR_DEVIATION_SKIN = (
    not _flag("CBBE2UBE_NO_AUTHOR_DEVIATION_SKIN", False))
# Headroom over the author's own spread before a part is rebuilt. Shares the
# rigid gate's value for the same reason: under it the difference is noise.
_AUTHOR_DEV_MARGIN = float(
    os.environ.get("CBBE2UBE_AUTHOR_DEV_MARGIN", "0.15") or "0.15")
# The author's deviation is only meaningful on bones BOTH rigs have. Under this
# share of the author's palette the transplant would be mostly guesswork, so the
# part is left alone rather than rebuilt from a fragment.
_AUTHOR_DEV_MIN_PALETTE = float(
    os.environ.get("CBBE2UBE_AUTHOR_DEV_MIN_PALETTE", "0.5") or "0.5")


# (moved to nif_convert_weights.py, 2026-09-01)


# How much ROUGHER than the author's own weight field a vertex may end up.
# Not zero: the author's field has real discontinuities (panel edges, the seam
# where leather meets fabric) and flattening those is a different defect. The
# allowance is what separates "the author put an edge here" from "our pass
# spiked one vertex".
AUTHOR_ROUGHNESS_ALLOW = _knob("CBBE2UBE_AUTHOR_ROUGHNESS_ALLOW", 0.05)
# A vertex holds four influences; a blend toward the neighbourhood can propose
# a fifth, so the result is re-capped and renormalised exactly like every other
# weight write here.
_ROUGHNESS_MAX_INFLUENCES = 4
AUTHOR_ROUGHNESS_CAP = not _flag("CBBE2UBE_NO_AUTHOR_ROUGHNESS_CAP", False)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


_SMP_HOLD_NEAR = _knob("CBBE2UBE_SMP_HOLD_NEAR", 0.5)
_SMP_HOLD_FAR = _knob("CBBE2UBE_SMP_HOLD_FAR", 6.0)
_SMP_HOLD_MIN_SHARE = _knob("CBBE2UBE_SMP_HOLD_MIN_SHARE", 0.75)
SMP_BOUNDARY_HOLD = not _flag("CBBE2UBE_NO_SMP_BOUNDARY_HOLD", False)


# (moved to nif_convert_weights.py, 2026-09-01)


_WP_JIGGLE_PRESENT_MIN = _knob("CBBE2UBE_WP_JIGGLE_PRESENT_MIN", 8, int)
_WP_JIGGLE_ABSENT_MAX = _knob("CBBE2UBE_WP_JIGGLE_ABSENT_MAX", 1, int)
_WP_JIGGLE_PEAK_MIN = _knob("CBBE2UBE_WP_JIGGLE_PEAK_MIN", 0.10)
_WP_JIGGLE_MAX_SHARE = _knob("CBBE2UBE_WP_JIGGLE_MAX_SHARE", 0.9)
WEIGHT_PARTNER_JIGGLE_SYNC = not _flag(
    "CBBE2UBE_NO_WEIGHT_PARTNER_JIGGLE_SYNC", False)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# --- #collider-shrinkwrap: grow the SMP collider onto the UBE body -----------
# THE LONG-UNFIXED BUTT CLIP. Reported for many builds: on a skirted cuirass the
# buttocks come through the skirt at standstill AND in motion.
#
# It is not a skinning defect and no weight pass can reach it: 84% of the garment
# over the buttocks is HDT-SMP chain cloth (mean chain weight 0.904), so where it
# ends up is decided by the SIMULATION. What the simulation collides against is
# this armour's own per-triangle collider -- and that collider is CBBE-sized:
#
#     z 64-70          body rearmost y   collider rearmost y   shortfall
#       on CBBE/3BA         -9.34              -8.85             0.49u  flush
#       on UBE             -12.43              -9.55             2.89u
#
# The body grew 3.09u rearward and the collider did not follow, so the cloth
# settles ~2.9u INSIDE the visible buttock. A collision gap fails identically at
# rest and in motion, which is exactly the reported symptom.
#
# WHY THE EXISTING WARP DOES NOT FIX IT. The collider IS warped
# (`#smp-collider-skin-preserve` warps its verts by the CBBE->UBE delta, and
# recomputing that by hand reproduces the shipped collider to 0.000u). But the
# GENERIC delta field only moves the butt 0.497u mean / 1.467u max, while the
# armour's own bundled body and the injected UBE body differ by 3.09u there --
# the difference is the user's two BodySlide presets, which no generic field
# knows about. The garment closes that gap because the fit/conform passes fit it
# to the REAL injected body; the collider gets the generic warp and nothing else,
# because every fit pass skips colliders.
#
# WHY NOT MOVE THE VERTS TO THE NEAREST BODY POINT (measured, and it fails):
# the collider is a TUBE whose cross-section is too small, not a shell that sank.
# Its verts already sit p50 +0.24u OUTSIDE the body -- outside a DIFFERENT part
# of it -- and 0 of them have the buttock apex as their nearest body point.
# Projecting every vert onto the surface moves the rearmost from -9.74 to -9.62:
# 0.12u of the 2.89u needed. Do not re-attempt nearest-point projection or
# standoff enforcement here.
#
# WHAT WORKS: expand the tube RADIALLY onto the body. For each collider vert take
# the body's centre-line at that height, cast outward along the vert's own radial
# direction, and move the vert to where that ray leaves the body. Vert count,
# topology, skinning and the physics XML are all untouched -- which is what keeps
# this out of the collision-pair CTD class that every other collider edit lives
# in. OUTWARD ONLY, so the collider can only ever stop cloth earlier, never let
# it through somewhere it used to be caught.
# DEFAULT OFF -- CBBE2UBE_COLLIDER_SHRINKWRAP=1.
COLLIDER_SHRINKWRAP = (
    _flag("CBBE2UBE_COLLIDER_SHRINKWRAP", False))
# Sit just proud of the skin: cloth should rest ON the body, not inside it.
_COLLIDER_SHRINKWRAP_OFFSET = _knob("CBBE2UBE_COLLIDER_SHRINKWRAP_OFFSET", 0.2)
# A vert that wants to move further than this is not a tube-radius shortfall --
# it is a mis-paired ray. Leave it alone rather than fling a collider vert.
_COLLIDER_SHRINKWRAP_MAX = _knob("CBBE2UBE_COLLIDER_SHRINKWRAP_MAX", 4.0)


def _conform_collider_to_body(dst_path) -> int:
    """Expand kinematic SMP colliders radially onto the UBE body surface.

    Returns the number of collider verts moved. See #collider-shrinkwrap above
    for the measurements; the short version is that the collider is CBBE-sized
    on a UBE-sized body and the cloth falls through the difference.

    ONLY kinematic colliders. A collider carrying chain weight moves WITH the
    cloth it is supposed to stop (this piece's `Proxy` is 92.5% chain-weighted at
    the butt), so re-shaping it neither helps nor is meaningful.
    """
    if not COLLIDER_SHRINKWRAP:
        return 0
    p = Path(dst_path)
    try:
        pyn = _pynifly()
        nf = pyn.NifFile(filepath=str(p))
    except Exception:
        return 0
    collider_names = _hdt_collider_shape_names(p, nif=nf)
    if not collider_names:
        return 0
    base = ube_body_shape(nf)
    if base is None:
        return 0
    try:
        from scipy.spatial import cKDTree as _KD
        bv = _verts_skin_to_world(np.asarray(base.verts, dtype=np.float64),
                                  _shape_global_to_skin(base))
        bt = np.asarray(base.tris, dtype=np.int64)
    except Exception:
        return 0
    if not len(bt):
        return 0
    body_bones = set(base.bone_names or [])

    moved_total = 0
    overrides: dict = {}
    for s in nf.shapes:
        if s.name not in collider_names:
            continue
        try:
            bw = s.bone_weights or {}
            # KINEMATIC only: any weight on a bone the BODY does not have is a
            # chain bone, and a chain-driven collider follows the cloth.
            if any(b not in body_bones for b in bw):
                continue
            cv = _verts_skin_to_world(np.asarray(s.verts, dtype=np.float64),
                                      _shape_global_to_skin(s))
            if not len(cv):
                continue
            # Body centre-line per height: the mean of the body's cross-section,
            # so "radially outward" means outward from the torso, not from the
            # world origin (a mesh whose origin is at the feet would otherwise
            # push everything upward).
            zc = cv[:, 2]
            axis = np.zeros((len(cv), 3), dtype=np.float64)
            for i, z in enumerate(zc):
                sel = np.abs(bv[:, 2] - z) <= 1.5
                axis[i] = (bv[sel].mean(axis=0) if sel.any()
                           else np.r_[0.0, 0.0, z])
            axis[:, 2] = zc
            d = cv - axis
            d[:, 2] = 0.0
            dl = np.linalg.norm(d, axis=1)
            ok = dl > 1e-6
            if not ok.any():
                continue
            D = np.zeros_like(d)
            D[ok] = d[ok] / dl[ok, None]
            tester = fit_metrics._ClipTester(
                bv, bt, tmax=float(_COLLIDER_SHRINKWRAP_MAX) + 12.0)
            hit = np.asarray(fit_metrics.cast_chunked(
                tester, axis[ok], D[ok], finite_only=False), dtype=np.float64)
            if len(hit) != int(ok.sum()):
                continue
            fin = np.isfinite(hit)
            if not fin.any():
                continue
            idx = np.flatnonzero(ok)[fin]
            target_r = hit[fin] + float(_COLLIDER_SHRINKWRAP_OFFSET)
            cur_r = dl[idx]
            grow = target_r - cur_r
            # OUTWARD ONLY, and never a wild throw.
            take = (grow > 1e-3) & (grow <= float(_COLLIDER_SHRINKWRAP_MAX))
            if not take.any():
                continue
            rows = idx[take]
            new = cv.copy()
            new[rows] = axis[rows] + D[rows] * target_r[take, None]
            # STORED frame, not world -- _reauthor_nif_fresh writes these verbatim.
            overrides[s.name] = _verts_world_to_skin(
                new, _shape_global_to_skin(s))
            moved_total += int(take.sum())
        except Exception as _ce:
            _note_pass_failure("_conform_collider_to_body", _ce)
    if not overrides:
        return 0
    # pynifly cannot mutate verts in place, and rebuilding a NIF from shapes
    # alone DROPS ALL EXTRA DATA -- which here would take the HDT physics link
    # and the BODYTRI with it, i.e. break the very thing being fixed plus every
    # morph. `_reauthor_nif_fresh` is the shipped path that preserves both.
    try:
        if not _reauthor_nif_fresh(p, override_verts_by_name=overrides, nif=nf):
            _note_pass_failure(
                "_conform_collider_to_body",
                RuntimeError("re-author declined; collider left unchanged"))
            return 0
    except Exception as _se:
        _note_pass_failure("_conform_collider_to_body/reauthor", _se)
        return 0
    return moved_total


# --- #butt-collider-patch: ADD the collision surface the buttocks never had ---
# The end of the road for the butt clip. Three vert-MOVING fixes were built and
# all three failed for one reason, measured three ways: nearest-point projection
# closed 0.12u of the 2.89u gap, standoff enforcement nothing, and radial
# shrink-wrap moved 50 collider verts -- all of them on the LEGS -- while not one
# rear vert moved rearward. The collider carries only 10 rear verts in the whole
# buttock band z62-72 and NONE at the apex. There is nothing there to move.
#
# So add it: a decimated patch of the UBE body's own buttock surface, skinned
# from the body's own weights, hidden, welded in as an extra per-triangle
# collider. The skirt then has a surface to rest on where the body actually is.
#
# THIS IS THE RISKIEST CHANGE IN THE PROJECT and it is shaped to minimise that:
#   * it ADDS a shape and never edits the existing collider, so nothing that
#     currently collides changes behaviour;
#   * the XML block is CLONED from the existing <per-triangle-shape> element with
#     only the name changed, so margin / penetration / tag / can-collide-with-tag
#     / no-collide-with-bone / weight-threshold all carry over verbatim -- an
#     invented block is how collision-pair equip-CTDs happen;
#   * all-or-nothing with a byte-restore, the same contract as the bust split;
#   * it fires ONLY where the gap is measured, so a piece whose collider already
#     covers the buttocks is untouched.
# DEFAULT ON since 2026-08-11 (CBBE2UBE_NO_BUTT_COLLIDER_PATCH=1 disables).
# The equip test this needed is passed: the added collision shape and its XML
# declaration equipped clean in game, and the piece carrying it was judged
# "perfect" with the patch, the skirt proxy and the chain lift all present.
# It stays narrow by measurement, not by the default -- the fire gate is >=150
# butt verts with no collider within 3u, which is 1 of the 15 golden pieces.
BUTT_COLLIDER_PATCH = (
    not _flag("CBBE2UBE_NO_BUTT_COLLIDER_PATCH", False))
_BUTT_COL_NAME = "ButtCol"
# FSMP cost scales with collider triangles; the body's raw butt is ~2.6k verts.
_BUTT_COL_TARGET = _knob("CBBE2UBE_BUTT_COLLIDER_TARGET", 400, int)
# Sit just proud of the skin so cloth rests ON the body, not inside it. 0.6, not
# the 0.2 this shipped with: 0.6 is what round 2 deployed and what was judged.
# See the strength note on MATCH_FULL_WEIGHTS -- flipping a toggle without the
# value it was validated at ships an unjudged recipe.
_BUTT_COL_OFFSET = _knob("CBBE2UBE_BUTT_COLLIDER_OFFSET", 0.6)
# The cloned donor block's own <margin>. The derived standoff (#derived-butt-
# standoff) sits this far UNDER the cloth's resting p10, because FSMP's margin
# already inflates the collision surface by it -- landing the effective barrier
# at the cloth rather than through it.
_BUTT_COL_MARGIN = _knob("CBBE2UBE_BUTT_COLLIDER_MARGIN", 0.1)
# "Uncovered" = no existing collider vert within this. Also the fire gate.
_BUTT_COL_GAP = _knob("CBBE2UBE_BUTT_COLLIDER_GAP", 3.0)
_BUTT_COL_MIN_UNCOVERED = _knob("CBBE2UBE_BUTT_COLLIDER_MIN_UNCOVERED", 150, int)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# --- #skirt-proxy-rebuild: give the VISIBLE cloth a collision proxy ----------
# Round 2 of the butt clip. With `#butt-collider-patch` in, the BODY side is
# provably right -- rear coverage 0.0% uncovered z44-80, ButtCol +0.60u standoff
# holding through the preset morph (body -12.43 -> -14.23, ButtCol -13.03 ->
# -14.83), no equip crash -- and the skirt still clipped.
#
# BECAUSE THE VISIBLE CLOTH IS NOT IN THE COLLISION AT ALL. This piece's chain
# bones are declared self-closing (`<bone name="Skirt 1_02"/>`) with no collision
# shape and no tag, so they collide with nothing. The only pair in the XML is
# `Proxy` (tag Fabric) <-> `Collision`/`ButtCol` (tag Collision), and `Proxy` is a
# 198-vert stand-in that does not resemble the skirt:
#
#     rearmost y   visible skirt -14.56   Proxy -11.43   body -12.43
#     z span       visible 58.2-79.9      Proxy 37.7-72.6
#     visible rear skirt verts with no Proxy vert within 3u:  48.4%
#
# The proxy sits 1.0u INSIDE the body's own rear extent and 3.1u short of the
# cloth it represents. HDT rests the PROXY on the collider correctly and the
# rendered skirt, a separate unconstrained surface, goes through the buttock
# anyway -- which is why +0.4u of extra collider standoff barely moved it.
#
# So build the missing proxy: decimate the garment's CHAIN-DRIVEN region keeping
# ORIGINAL verts (weights/STB/g2s copy across untouched, no re-rigging) and add it
# as a second Fabric-tagged per-triangle shape. The authored `Proxy` is left
# alone -- it supports the skirt elsewhere and replacing it is how a working chain
# gets destabilised. Fabric shapes carry `no-collide-with-tag Fabric`, so the new
# proxy cannot fight the old one.
#
# RISK RANK: this is a step above ButtCol. ButtCol is KINEMATIC and cannot
# destabilise the simulation; a cloth proxy is chain-driven and a bad one can
# balloon, collapse, or pull to the origin.
# DEFAULT ON since 2026-08-11 (CBBE2UBE_NO_SKIRT_PROXY_REBUILD=1 disables). The
# equip test AND the look at the skirt in motion are both done -- the piece
# carrying it was judged "perfect", which is the only check that could clear a
# chain-driven proxy.
# --- #skirt-proxy-after-weights -- OPT-IN, default OFF ------------------------
#
# `_add_skirt_collider_proxy` picks its SOURCE as the shape with the most
# vertices weighted to bones the body does not have -- and it runs BEFORE the
# four passes that rewrite exactly those weights:
#
#     _add_skirt_collider_proxy      <- picks here
#     _transfer_body_jiggle_to_fitted
#     _match_leg_motion_to_body
#     _match_spine/arm_motion_to_body
#     _match_full_weights_to_body
#
# So the pick is made on a weight state that does not survive the same
# function. Measured 2026-09-04 on three dresses of one mod, in the SHIPPED
# weights:
#
#     piece    dress chain-verts   potion-net   margin
#     DressA        5830              1519      3.8x
#     DressB        4708              1519      3.1x
#     DressC        1634              1519      1.08x   <- 115 verts
#
# DressA and DressB win by nearly 4x and are correct in the shipped output.
# DressC's margin is 7%, and it shipped a "SkirtCol" built from the POTION
# BOTTLE NET -- 1 bone (`MCPotion 1`), coincident with `4_belt_potion_net` at
# 0.000u, 4.085u from the skirt -- while its actual skirt (45 chain bones,
# z 34.8..111.3) got no collision proxy at all. A 7% margin does not survive
# four passes rewriting the quantity being ranked; a 380% margin does. That is
# the whole difference between the three.
#
# This moves the pick to AFTER the weight passes in the same shared tail, so it
# ranks on the weights that ship. It stays after `_add_butt_collider_patch`
# (whose FABRIC block it clones) and before `_restore_authored_shape_order`
# (which must see every shape that ships) and the declared-bone audit.
#
# NOT A NO-OP: it changes which shape the proxy is built from wherever the
# ranking flips, so it moves geometry and needs its own verdict. Three later
# weight passes still run after this point in the CALLER
# (`_cap_weight_roughness_to_author`, `_hold_weights_at_smp_boundary`,
# `_match_coincident_cross_shape_skin`); moving past those as well would take
# the pass out of the shared tail that both convert paths call, which is a
# bigger change and is not attempted here.
# --- #bodytri-carrier-cloth -- EXPERIMENT, default OFF -----------------------
#
# `_pick_bodytri_carriers` prefers the BODY shape (BaseShape/3BA) and says why:
# "matches the dominant hand-built convention (88/93 sampled slot-32 UBE NIFs)"
# and "on a cloth shape NioOverride often skips other shapes". That evidence is
# real and this flag does NOT overturn it.
#
# It exists because an in-game report survived everything else. On the reported
# outfit the armour's body morph data is now a byte-for-byte copy of the nude
# body's -- same 196 names, same offset counts, same peaks, ratio 1.00x on every
# breast/glute/ab slider -- the injected body is identical to the user's own
# BodySlide build, the weight pair is correct, and the dress follows the breast
# sliders at ~1.00x. And the sliders still collapse when it is worn.
#
# The ONE structural difference left from the author's own file is which shape
# carries the BODYTRI tag:
#     author (a CBBE/3BA mod)   1_dress      -- the visible garment
#     ours   (UBE convention)   BaseShape    -- the injected body
#
# So this is a HYPOTHESIS TEST, not a fix, and the prior is against it. If it
# changes nothing the carrier is eliminated and the cause is outside the mesh.
# --- #bodytri-all-shapes -- DEFAULT ON since 2026-09-04 ----------------------
# Kill switch: CBBE2UBE_NO_BODYTRI_ALL_SHAPES=1
#
# The authored arrangement, read at BLOCK level: BODYTRI on the body AND on
# every cloth shape. Both earlier readings were wrong --
#
#   "body shape, 88/93 hand-built NIFs"  did not describe carrier choice
#   "18 of 18 EXCLUDE the body"          was a TOOL ARTEFACT: pynifly's
#                                        `extra_data()` stops at the first
#                                        block it cannot build, and a
#                                        BodySlide body carries a
#                                        NiIntegersExtraData `LOCKEDNORM` at
#                                        index 0, so every body reported no
#                                        extra data at all
#
# Enumerated properly (`get_extra_data(target_index=i)` over extraDataCount):
# the nude body's BaseShape carries LOCKEDNORM + BODYTRI, a hand-built armor's
# BaseShape carries LOCKEDNORM + BODYTRI twice, and all 14 of its cloth shapes
# carry BODYTRI.
#
# WHY IT PLAUSIBLY MATTERS. If NioOverride morphs a shape only when THAT shape
# carries a BODYTRI, then a single carrier morphs half the piece:
#
#     carrier      body morphs   cloth morphs   full-coverage garment looks
#     body only        yes           no         unmorphed cloth over a correct
#                                               body -- the body is HIDDEN
#     cloth only       no            yes        morphed cloth over base skin
#     body + cloth     yes           yes        correct  (what authors ship)
#
# That is consistent with the in-game reports: a report of lost sliders on
# full-length dresses, while smaller pieces on the same build looked right --
# on a bra or a top the correctly-morphed body IS what you see, so a
# body-only carrier hides its own defect.
#
# IN-GAME: `#bodytri-carrier-cloth` (cloth only, body excluded) was tested and
# changed NOTHING, which this model predicts -- it swaps which half is broken.
BODYTRI_ALL_SHAPES = not _flag("CBBE2UBE_NO_BODYTRI_ALL_SHAPES", False)
BODYTRI_CARRIER_CLOTH = _flag("CBBE2UBE_BODYTRI_CARRIER_CLOTH", False)

# #pair-tri-names -- emit each armour shape's morph table under the PARTNER
# half's name for it as well, so one tri serves a `_0`/`_1` pair whose author
# named the two halves' shapes differently.
#
# MEASURED on the 2026-09-06 pack, whole population: of 1536 pairs, 1482 name
# their shapes identically and 54 do not -- and in EVERY one of those 54 it is
# the `_1` half that names nothing in the tri it points at (42 lose every
# morph, 12 lose some; `_0` never loses anything, because `_0` is the half the
# tri is built from). `_1` is the half that ships, since actors sit near weight
# 100. The precondition was measured too, not assumed: 53 of the 54 have the
# same shape count AND the same per-shape vert counts, so one delta table is
# correct under both names. The 54th has 5 shapes vs 4 and is refused by
# `pair_shape_aliases` -- it is the same piece the census already reports under
# `pairs with a vert-count mismatch`.
#
# PROMOTED TO DEFAULT ON, 2026-09-09, on the user's call. Measured across 53 of
# the 54 affected pairs over two arms: halves losing every morph went 48 -> 0 on
# one population and 5 -> 1 on the other, with 769 NIFs BYTE-IDENTICAL and only
# the affected `.tri` files changing. The one survivor was the 5-shapes-vs-4
# piece, whose own cause is `#body-name-prefix` -- fixed in the same cycle, so
# the pack-wide prediction is now 54 -> 0 rather than 54 -> 1.
#
# The kill switch keeps the old behaviour: CBBE2UBE_NO_PAIR_TRI_NAMES=1.
# See [[project_weight_pair_tri_name_split]].
PAIR_TRI_NAMES = (not _flag("CBBE2UBE_NO_PAIR_TRI_NAMES", False))

SKIRT_PROXY_AFTER_WEIGHTS = _flag("CBBE2UBE_SKIRT_PROXY_AFTER_WEIGHTS", False)

SKIRT_PROXY_REBUILD = (
    not _flag("CBBE2UBE_NO_SKIRT_PROXY_REBUILD", False))
_SKIRT_PROXY_NAME = "SkirtCol"
# --- #proxy-weight-invariant -- DEFAULT ON since 2026-09-04 ------------------
# Kill switch: CBBE2UBE_NO_PROXY_WEIGHT_INVARIANT=1
#
# Generated collision proxies are decimated on a POSITION grid, so the `_0` and
# `_1` files of one garment -- same topology, different body weight -- decimate
# differently. Measured on the shipped pack: 14 of 28 pairs carrying a
# generated skirt proxy disagree on its vertex count. Skyrim blends the pair per
# vertex and one `.tri` serves both, so the disagreement ships a broken weight
# blend AND morph offsets addressing vertices the other weight lacks (seen: an
# offset for vertex 478 of a proxy with 428 vertices).
#
# ON, the proxy clusters on the edge graph instead. See `_topo_decimate` in
# nif_convert_physics.py for the mechanism and the full measurement.
#
# It MOVES GEOMETRY on every piece that carries a generated proxy. The build
# carrying it was judged good in game 2026-09-04, so the default is ON.
PROXY_WEIGHT_INVARIANT = not _flag("CBBE2UBE_NO_PROXY_WEIGHT_INVARIANT", False)
# Cell budget multiplier for the invariant path -- see `_decimate`. 1.5 recovers
# the grid's coverage at fewer triangles; 2.0 buys nothing further.
PROXY_TOPO_TARGET_SCALE = _knob("CBBE2UBE_PROXY_TOPO_TARGET_SCALE", 1.5)
# --- #proxy-encloses-chain -- the proxy must not CONTAIN what it collides with
#
# CONFIRMED IN GAME 2026-08-23. On a cuirass whose generated proxy contained 11
# of the skirt's own 71 chain nodes, the skirt showed an intermittent spike --
# "roughly 2/3 of the time and sometimes vanishes", oriented relative to the
# character -- and the skirt as a whole behaved wrongly. Building the same piece
# with `CBBE2UBE_NO_SKIRT_PROXY_REBUILD=1` fixed both, reported as "that fixed it
# as well as the entire skirt working as it should".
#
# WHY: a chain node that starts INSIDE its own collider is a constraint violated
# on frame one. SMP ejects it every frame; the solve may settle or diverge, which
# is exactly an intermittent, character-relative artefact. The block above
# already names this risk -- "a cloth proxy is chain-driven and a bad one can
# balloon, collapse, or pull to the origin" -- and the pass was cleared on ONE
# piece judged perfect. It is DEFAULT ON, so every other piece took it on faith.
#
# THE CLASS PROPERTY, falsifiable with no in-game verdict: a generated collision
# proxy must not contain the chain nodes it exists to collide with. Pack census
# at the shipping recipe: 32 proxies scored, **16 enclose a chain node** -- half
# of every proxy this pass has ever produced.
#
# Inside/outside by RAY PARITY, never a nearest-vertex normal: the normal test
# flips sign on a concave surface and has produced void numbers here before
# (#closest-point-plane-bug).
#
# DECLINE rather than shrink. The pass's own contract already prefers that --
# "past some share it is no longer doing that and a stale collision surface is
# worse than none" -- and an inset proxy is a new shape nobody has judged.
PROXY_ENCLOSE_GUARD = not _flag("CBBE2UBE_NO_PROXY_ENCLOSE_GUARD", False)


def _proxy_encloses_chain_nodes(verts, tris, nodes) -> list:
    """Chain-node names that fall INSIDE the proxy hull. Ray parity along +X."""
    try:
        V = np.asarray(verts, dtype=np.float64)
        T = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        if len(V) < 4 or len(T) < 4 or not nodes:
            return []
        names = list(nodes)
        P = np.asarray([nodes[k] for k in names], dtype=np.float64)
        a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        e1, e2 = b - a, c - a
        # A GENERIC RAY DIRECTION, not an axis. An axis-aligned ray exits through
        # a shared triangle EDGE whenever the mesh is symmetric about that axis
        # -- both triangles then register a hit and the parity flips, reporting
        # a contained node as outside. Reproduced exactly on an axis-aligned
        # cube. Nothing here is axis-dependent, so an irrational-ish direction
        # costs nothing and makes an exact edge hit vanishingly unlikely.
        d = np.array([1.0, 0.3178123, 0.1290551])
        d = d / np.linalg.norm(d)
        h = np.cross(np.broadcast_to(d, e2.shape), e2)
        det = np.einsum('ij,ij->i', e1, h)
        ok = np.abs(det) > 1e-12
        if not ok.any():
            return []
        out = []
        for i, pt in enumerate(P):
            s = pt - a
            u = np.einsum('ij,ij->i', s, h) / np.where(ok, det, 1.0)
            q = np.cross(s, e1)
            v = np.einsum('j,ij->i', d, q) / np.where(ok, det, 1.0)
            tt = np.einsum('ij,ij->i', e2, q) / np.where(ok, det, 1.0)
            hit = ok & (u >= 0.0) & (u <= 1.0) & (v >= 0.0)                 & (u + v <= 1.0) & (tt > 1e-6)
            if int(hit.sum()) % 2:
                out.append(names[i])
        return out
    except Exception as e:
        # A guard that fails silently is a guard that is not there.
        _note_pass_failure("skirt-proxy/encloses-chain", e)
        return []
_SKIRT_PROXY_TARGET = _knob("CBBE2UBE_SKIRT_PROXY_TARGET", 500, int)
# A vert is "cloth" when the SIM drives it: weight on bones the body does not have.
_SKIRT_PROXY_CHAIN_MIN = _knob("CBBE2UBE_SKIRT_PROXY_CHAIN_MIN", 0.5)
_SKIRT_PROXY_GAP = _knob("CBBE2UBE_SKIRT_PROXY_GAP", 3.0)
# #collider-declared-bones on this proxy: how much of its weight may be moved
# onto kinematic XML-declared ancestors before the proxy is DECLINED instead.
# The proxy exists to FOLLOW simulated cloth, so past some share it is no longer
# doing that and a stale collision surface is worse than none. 0.25 is the
# conservative end -- the audited offenders carry undeclared bones on a small
# minority of their mass (twist/neck/thigh-detail trim), so the cap declines the
# pathological case without touching them.
_SKIRT_PROXY_REDIRECT_MAX = _knob("CBBE2UBE_SKIRT_PROXY_REDIRECT_MAX", 0.25)


# (moved to nif_convert_physics.py, 2026-09-01)
_SKIRT_PROXY_MIN_UNREPRESENTED = _knob("CBBE2UBE_SKIRT_PROXY_MIN_UNREPRESENTED", 60, int)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# Chain-anchor strategy: physics-chain hard-skeleton anchors (Pelvis/Spine/...)
# are recreated FLAT (identity, parent=Scene Root) so the ACTOR's live skeleton
# drives them at runtime. Nesting them statically into the worn armor caused FSMP
# to anchor against the static copy rather than the live actor — cloth sagged off.
# Custom-bone chains (chain-specific bones) and soft-body chain bones are still
# recreated at SOURCE bind (see _precreate_custom_bone_chains).
# `CBBE2UBE_NESTED_CHAIN_ANCHORS=1` restores full nesting as an opt-in fallback.
NESTED_CHAIN_ANCHORS = (
    _flag("CBBE2UBE_NESTED_CHAIN_ANCHORS", False)
)

# PELVIS RE-ANCHOR: a bone-driven garment chain (skirt/apron) whose top bone hangs
# off a NIF-ROOT node (e.g. "BodyM_1.nif", "Scene Root") instead of a skeleton bone
# tracks the actor ROOT (feet) at runtime, so the waist garment disconnects /
# collapses as the body moves (every physics attempt fails because the anchor was
# never on the body). Re-parent that root node onto NPC Pelvis while PRESERVING its
# global position: each descendant chain bone's global transform + skin-to-bone
# (STB) is therefore unchanged (skin byte-identical), but the chain now follows the
# pelvis like a correctly-rigged skirt. Confirmed in-game (a custom-race wolf-armor
# skirt). Default ON; CBBE2UBE_NO_PELVIS_REANCHOR=1 disables.
PELVIS_REANCHOR_CHAINS = (
    not _flag("CBBE2UBE_NO_PELVIS_REANCHOR", False))
# Only re-anchor a root whose garment children sit at pelvis/waist height (global Z);
# hair/cape chains hang higher and need their own anchor bone (deferred).
_PELVIS_REANCHOR_ZMIN = _knob("CBBE2UBE_PELVIS_REANCHOR_ZMIN", 40.0)
_PELVIS_REANCHOR_ZMAX = _knob("CBBE2UBE_PELVIS_REANCHOR_ZMAX", 100.0)


def _is_nif_root(nm: str) -> bool:
    """A node that is the NIF's own ROOT rather than a skeleton bone -- the thing a
    physics chain must never hang off, because at runtime it tracks the actor root
    (the feet) instead of the body.

    Was defined identically inside BOTH `_reanchor_nif_root_chains` and
    `_has_nif_root_garment_chain` (the detector and the fix for the same condition).
    Two copies of the predicate that decides whether a garment collapses through the
    floor is one copy too many: they must agree by construction, not by review."""
    low = nm.lower()
    return (not _is_skeleton_bone(nm)
            and (low.endswith(".nif") or low == "scene root"
                 or low.startswith("bodym") or low.startswith("bodyf")))


def _reanchor_nif_root_chains(chain, anchors, src_nodes) -> int:
    """Re-parent garment-chain bones that hang off a NIF-ROOT node onto NPC Pelvis.

    `chain` maps bone -> (transform, parent_name) as gathered by
    _precreate_custom_bone_chains. The failure case (a disjointed skirt): the top skirt bones
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


def _chain_root_subtrees(chain: dict, custom_only: bool = False) -> dict:
    """{root: every bone in its subtree}. A root is a chain bone whose parent is
    NOT itself a chain bone -- i.e. it hangs off the skeleton.

    `custom_only` restricts roots to GARMENT bones. `chain` carries the skeleton
    ancestors a chain hangs off (Pelvis, Spine) so the hierarchy can be
    recreated, and without this the plain rule elects those skeleton bones as
    roots -- measured on a real cuirass, the roots came back as
    `NPC Pelvis [Pelv]` and `NPC Spine [Spn0]`. Translating an actor's pelvis is
    never what a caller wants; the topmost GARMENT bone is.
    """
    kids: dict = {}
    for b, (_xf, par) in chain.items():
        kids.setdefault(par, []).append(b)

    def _is_root(b, par):
        if custom_only:
            return (not _is_skeleton_bone(b)) and (
                par not in chain or _is_skeleton_bone(par))
        return par not in chain

    out: dict = {}
    for r in [b for b, (_xf, par) in chain.items() if _is_root(b, par)]:
        sub, stack = set(), [r]
        while stack:
            cur = stack.pop()
            if cur in sub:
                continue
            sub.add(cur)
            stack.extend(kids.get(cur, ()))
        out[r] = sub
    return out


# ------------------------------------ #chain-rest-outside-body (default ON)
# Lift a physics chain whose REST POSE sits INSIDE the body it drapes over.
#
# THE DEFECT. Chain bone globals move 0.000000u through the conversion (measured
# source vs output, all 63 bones on the test cuirass) while the body grows to UBE
# proportions, so the rest pose the solver pulls toward ends up inside the
# buttock. HDT-SMP resolves an equilibrium -- `generic-constraint` pulls each
# bone back to its rest pose while collision pushes out -- so an inside rest pose
# drags the cloth in every frame, identically at standstill and in motion. That
# is the reported symptom, and it is why three collider passes each helped and
# none could finish: they add push against a pull nothing addressed.
#
# THE SIZE OF IT, measured on the vanilla studded cuirass:
#
#     against the BUILT UBE body            2 of 63 bones inside, max 0.778u
#     against that body under the user's    8 of 63 inside, mean 0.900u,
#       RaceMenu preset (Punk UBE, w100)      max 2.000u
#
# Always the `_01` segment at the fullest part of the buttock; `_02.._04` hang
# clear (+0.3 to +7.6u) and the front/stabilizer chains clear everywhere. An
# earlier note put this at ~3.2u; that compared a bone's y against the body's
# rearmost point ANYWHERE in the band rather than the surface at the bone's own
# location. 2.0u is the honest figure.
#
# WHY THE MARGIN IS THE MORPH AMPLITUDE, not a fudge. The converter only ever
# sees the BUILT body; the player's RaceMenu sliders grow it further at runtime,
# and that is where 6 of the 8 penetrations come from. Rather than teach this
# pass a preset it cannot know, it clears the body's OWN outward morph headroom
# (`_cached_body_morph_amplitude` -- the same map adaptive clearance uses). Over
# the at-risk bones on the test piece that map reads mean 1.009u against an
# actual Punk UBE growth of mean 0.879u / max 1.615u, so it is a fair proxy.
# Adaptive clearance takes only 20% of that amplitude for GARMENT verts because
# those verts morph too; a chain bone has no morph channel at all, so it needs
# the whole of it. Hence FACTOR 1.0 here against 0.20 there.
#
# REFUTED, with numbers, before this was written -- "restore the clearance the
# author gave each bone against its OWN body", the bone-space analogue of
# `conform_to_source_standoff`. It is the wrong criterion twice over: the
# largest losses are on the FRONT skirt (2.96u) where nothing clips, and 10 of
# 63 bones were ALREADY inside the source body, because an author routinely runs
# a skirt's bones down the INSIDE of the cloth. The two bones that actually clip
# do not appear in the top 16. Sampling the source body also needs care --
# its 6463 normals are ALL ZERO, so a signed distance taken from stored normals
# reads exactly +0.000 for every bone and looks like a clean measurement.
#
# ROOTS ONLY, never individual bones. Chain transforms are PARENT-LOCAL, so
# displacing a root translates the whole chain RIGIDLY -- every inter-bone
# distance changes by exactly 0.000000u. Warping bones individually changes rest
# lengths and is how a chain explodes (`docs/PIPELINE.md` §7). THE COST of that
# rigidity is that the free-hanging lower chain moves out too; it is recorded
# per root as `flare` so the trade is visible, and capped by CHAIN_LIFT_MAX.
# DEFAULT ON since 2026-08-11 (CBBE2UBE_NO_CHAIN_REST_LIFT=1 disables). Judged
# in game on the piece the defect was reported against, in motion, with the two
# collider passes present: USER "perfect". The risks this was held back for --
# ballooning, collapse, pull-to-origin, a skirt standing too far off -- are the
# ones that look wrong in motion, and none appeared.
#
# CAVEAT worth keeping in view: this fires on 6 of the 12 chain-bearing golden
# pieces (34 chains), where the butt patch fires on 1 of 15. ONE piece carried
# the verdict. Two pieces (`fitted-dress`, `hide-collider`) hit CHAIN_LIFT_MAX,
# meaning the criterion wanted more than it is allowed -- those are the first
# places to look if a skirt is reported standing off.
CHAIN_REST_LIFT = not _flag("CBBE2UBE_NO_CHAIN_REST_LIFT", False)
# Clearance a bone must end up with = BASE + FACTOR * (outward morph amplitude).
CHAIN_LIFT_BASE = _knob("CBBE2UBE_CHAIN_LIFT_BASE", 0.25)
CHAIN_LIFT_MORPH_FACTOR = _knob("CBBE2UBE_CHAIN_LIFT_MORPH", 1.0)
# Ceiling on that wanted clearance, and the pass's real engagement rule: a bone
# further than this from the skin is not the defect, whatever the morph map says
# about its neighbourhood. WITHOUT IT the pass recruited two FRONT skirt chains
# sitting +3.63u clear of the belly, because belly amplitude runs to 8.7u there
# and drove a wanted clearance of 6.79u -- a confident 2.0u push on cloth with
# nothing wrong with it, which the `flare` counter-metric caught at +8.5u.
# Adaptive clearance caps its own ramp for exactly this outlier
# (ADAPTIVE_CLEARANCE_MORPH_MAX). 1.75 clears the measured requirement -- over
# the at-risk bones of the test piece the wanted clearance peaks at 1.696u and
# the actual preset growth at 1.615u -- while excluding the belly outliers.
CHAIN_LIFT_WANT_MAX = _knob("CBBE2UBE_CHAIN_LIFT_WANT_MAX", 1.75)
# Cap. The measured requirement is 1.87u on the worst chain of the test piece,
# so 2.0 is the ceiling that requirement fits under, not a target.
CHAIN_LIFT_MAX = _knob("CBBE2UBE_CHAIN_LIFT_MAX", 2.0)
# Below this a lift is noise against the thing it is trying to fix.
CHAIN_LIFT_MIN = 0.05
# Node-global vs skin-derived bind position must agree within this, or the
# node tree is in a frame we cannot sample a body in. See _chain_frame_ok.
CHAIN_LIFT_FRAME_TOL = 0.5


# Keyed on the body path, and it holds the tree TOGETHER with the verts and
# normals it was built from. `_ube_conform_body_tree` exists and is cached, but
# it re-opens the body itself and returns no normals -- pairing its index space
# with normals from `_cached_ube_body_verts` would silently mismatch if the two
# ever resolved different files. One tuple, one source, no pairing to get wrong.
_CHAIN_LIFT_BODY_CACHE: dict = {}


def _xf_matrix(xf) -> "np.ndarray":
    """TransformBuf -> 4x4. Rotation is row-major 3x3; scale is uniform."""
    m = np.eye(4)
    r = np.array([[float(c) for c in row] for row in xf.rotation], np.float64)
    m[:3, :3] = r * float(getattr(xf, "scale", 1.0) or 1.0)
    m[:3, 3] = [float(c) for c in xf.translation]
    return m


def _chain_rest_globals(chain: dict, src_nodes) -> dict:
    """{bone -> (3,) world position} for every bone in `chain`.

    Composed from the CHAIN's OWN local transforms rather than read off the
    source node tree, so anything that already mutated `chain` -- the pelvis
    re-anchor, the chain-rest-lift -- is accounted for instead of silently
    discarded. The walk roots at the first ancestor the chain does not own (a
    real skeleton bone), whose SOURCE global supplies the frame.
    """
    memo: dict = {}

    def _g(b, stack):
        if b in memo:
            return memo[b]
        if b in stack:
            return None                      # cyclic parent link -> give up
        ent = chain.get(b)
        if ent is None:                      # anchor: the frame comes from here
            nd = src_nodes.get(b)
            if nd is None:
                return None
            try:
                M = _xf_matrix(nd.global_transform)
            except Exception:
                return None
            memo[b] = M
            return M
        xf, par = ent
        if not par:
            P = np.eye(4)
        else:
            P = _g(par, stack | {b})
            if P is None:
                return None
        M = P @ _xf_matrix(xf)
        memo[b] = M
        return M

    out: dict = {}
    for b in chain:
        M = _g(b, frozenset())
        if M is not None:
            out[b] = M[:3, 3].copy()
    return out


def _chain_frame_ok(src_nif, gpos: dict) -> "tuple[bool, int, float]":
    """Do the node-tree globals agree with the SKINNING? (ok, checked, worst).

    Chain nodes are parent-local and an armour NIF's skeleton bones are often
    (0,0,0) placeholders, in which case the composed global is PELVIS-RELATIVE
    -- measured once as z -26..28 against a butt band at z 62..80. Sampling a
    body surface with those coordinates compares two different frames and lifts
    every chain by a confident, meaningless number.

    A bone's bind world transform is independently recoverable from the skin,
    which cannot be in the wrong frame: `inv(global_to_skin) . inv(skin_to_bone)`.
    Where both exist they must agree. Bones with no skin weight at all (pure
    constraint bones -- the `_00` anchors are exactly these) have no STB and are
    not checkable; the frame is a property of the NODE TREE, so one agreeing
    bone anywhere in the file vouches for it. `checked == 0` is NOT agreement
    and the caller must treat it as a refusal.
    """
    worst = 0.0
    checked = 0
    for sh in src_nif.shapes:
        try:
            G_inv = np.linalg.inv(_xf_matrix(_shape_global_to_skin(sh)))
        except Exception:
            continue
        for b in (sh.bone_names or []):
            if b not in gpos:
                continue
            try:
                stb = _xf_matrix(sh.get_shape_skin_to_bone(b))
                skin_pos = (G_inv @ np.linalg.inv(stb))[:3, 3]
            except Exception:
                continue
            checked += 1
            worst = max(worst, float(np.linalg.norm(skin_pos - gpos[b])))
    return (checked > 0 and worst <= CHAIN_LIFT_FRAME_TOL), checked, worst


# (moved to nif_convert_physics.py, 2026-09-01)


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


# #anchor-global-fix. Flat mode is meant to place a chain anchor at its SOURCE
# GLOBAL position; if `add_bone` already created that node (flat, identity) the
# add is skipped and the global is lost, dropping the whole chain to the origin.
# Default ON: it only ever writes the transform the surrounding branch already
# documents as its intent, and only onto a FLAT-parented node, so it is a no-op
# on anything already correct. CBBE2UBE_NO_ANCHOR_GLOBAL_FIX=1 is the hatch.
ANCHOR_GLOBAL_FIX = (
    not _flag("CBBE2UBE_NO_ANCHOR_GLOBAL_FIX", False))

# #chain-anchor-recreate. Recreate a MISSING flat anchor node, so the chain that
# hangs off it can be attached at all -- see `_precreate_custom_bone_chains`'
# flat branch for the measurement. Default ON: it only ever creates a node that
# is ABSENT, so it is a no-op wherever the anchor already survived.
#
# IT HAS ITS OWN HATCH ON PURPOSE. The regression this repairs shipped with no
# flag of its own, so bisecting it cost a full rebuild per hypothesis, and two
# plausible suspects (`CBBE2UBE_NO_ANCHOR_GLOBAL_FIX`,
# `CBBE2UBE_NO_LEG_GARMENT_GUARD`) each read as "not it" only because neither
# could switch the real cause off. A behaviour with no off-switch cannot be
# A/B'd. CBBE2UBE_NO_CHAIN_ANCHOR_RECREATE=1 disables.
CHAIN_ANCHOR_RECREATE = (
    not _flag("CBBE2UBE_NO_CHAIN_ANCHOR_RECREATE", False))

# #per-anchor-seed. `_seed_flat_chain_anchors` decides per FILE whether to seed,
# and ONE upper-body anchor turns the whole thing off -- including the pelvis
# every skirt actually hangs from. MEASURED on vanilla iron, which anchors BOTH
# `NPC Pelvis` (the real skirt/belt/bag chains) and `NPC Spine2` (a `PBelt`
# chain that carries ZERO skin weight and whose shape the mesh does not even
# have -- it is there only because the physics XML is shared with the plate
# variant). `any(_is_upper_body_anchor)` fires on that vestigial chain, nothing
# is seeded, `NPC Pelvis` stays flat at IDENTITY, and every chain parented onto
# it keeps a source-LOCAL transform that assumed a parent at hip height -- so
# the whole rig lands 68.91u low, under the floor. REPORTED IN GAME as the legs
# sinking away from the body and falling forever. All 8 iron files, both
# weights, both variants, and identically in the shipped pack.
#
# The ARM branch of that same gate is already `all()` rather than `any()`,
# precisely because mixed rigs exist (an `any()` there "would nest their pelvis
# chains = the June sag"). This gives the upper-body branch the same treatment:
# decide per ANCHOR, seed the pelvis, and leave the upper-body anchor to the
# nested branch that builds real parent links for it.
#
# WAS opt-in until censused (DEFAULT ON since 2026-08-22). The blast radius is every NIF that mixes a pelvis chain
# with ANY upper-body chain, which is unmeasured, and this area has regressed
# TWICE (the June skirt sag; arm-anchor nesting).
# DEFAULT ON since 2026-08-22 -- promoted with the other four recipe
# opt-ins. The 08-22 reconvert ran all five ON over 163 mods and the
# in-game verdict was "everything looks as it should", so the defaults
# now match the only configuration that has EVER been judged. Details in
# the `_DEFAULTS_PROMOTED_2026_08_22` block.
PER_ANCHOR_ANCHOR_SEED = _flag("CBBE2UBE_PER_ANCHOR_SEED", True)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)

# Shape name keywords that indicate "this shape is hand/foot armor and
# must not receive body-morph scale-bone weights even if its skinning
# doesn't include hand/foot bones explicitly". Stylized gauntlets and
# fantasy boots may be rigged to upperarm/calf bones alone but should
# still NOT deform with breast/belly/butt body sliders.
HAND_FOOT_NAME_KEYWORDS = (
    "hand", "glove", "gauntlet", "finger", "fist",
    "knuckle", "wrist", "bracer",
)


# #leg-garment-not-extremity. A full-length leg garment whose cuffs overlap the
# feet clears the extremity vert-cluster test on the same fraction a real boot
# does, and is then routed down the rigid hand/foot branch that returns before
# the cloth pass. Default ON: real boots/gloves carry ~0% thigh+pelvis mass, so
# the guard has a ~500x margin and cannot reclassify them.
# CBBE2UBE_NO_LEG_GARMENT_GUARD=1 is the hatch.
LEG_GARMENT_NOT_EXTREMITY = (
    not _flag("CBBE2UBE_NO_LEG_GARMENT_GUARD", False))

# #sleeve-garment-not-extremity -- OPT-IN, `CBBE2UBE_SLEEVE_GARMENT_GUARD=1`.
# THE ARM SIBLING OF THE GUARD ABOVE, and it exists for the same reason: a
# LONG-SLEEVED torso garment whose CUFFS reach the hands clears the extremity
# vert-cluster test exactly as a real gauntlet does, is routed down the rigid
# hand/foot branch, and that branch `continue`s before the cloth pass -- so the
# piece never gets conform, panel-rigidity or anti-poke at all.
#
# MEASURED, and this is a shipped defect, not a theoretical one. The worst
# chest-clipping piece in the pack, `dbmpatronclothing/dbmhighelfleatherdusterf`
# (8.899% bind clip over the bust band), is a 1220-vert full-body robe whose
# sleeves carry `NPC L/R Hand`. Its whole stage chain is three rows --
# entry 40.074 -> warp_hf 28.799 -> inflate_hf 8.899 -> SHIPPED -- against the
# eleven a torso garment normally gets. Nothing clears the chest because
# nothing that clears chests ever runs.
#
# THE DISCRIMINATOR MIRRORS `#boot-pelvis-only`: a boot is a tube around the leg
# and never reaches the PELVIS, so pelvis mass separates pants from boots. A
# gauntlet is a tube around the forearm and never reaches the SPINE. Measured
# over 173 source shapes that reach this gate as extremity:
#     real handwear (22 sampled)   spine mass  0.0     -- every one
#     HighElfRobe (the defect)     spine/hand  7.69
#     Mage's Daily `shirt`         spine/hand  5.74
#     ebonymail `Cuirass:1`        spine/hand 15.35
#
# TWO GUARDS ON TOP OF THE RATIO, both bought by measurement:
#   * `hand > 0` -- every bad candidate had ZERO hand mass and was not a sleeved
#     garment at all: a knife SCABBARD HANDLE (spine 9777, hand 0), a shape
#     named `WolfGauntlets`, an embedded `3BA Ref` body. They carry spine weight
#     without ever reaching an arm.
#   * a 2x MARGIN -- `3BA Ref` sits at 1.03 and a first-person `coat` at 1.39.
#     A bare `spine > hand` flips the reference body; 2x does not, and still
#     clears the real cases by 3-7x.
# Net: 3 of 173 shapes reclassified (1.7%), all genuine sleeved torso garments.
#
# COVERAGE COST, stated plainly like its sibling: marginal garments below 2x
# (that `coat`, and `sleeves:1` on a pirate shirt at hand 260.9 / spine 127.2)
# stay misrouted. Conservative on purpose -- reclassifying a real gauntlet is
# the expensive direction, and `#boot-pelvis-only` records a classifier change
# here regressing 58 footwear shapes once already.
#
# MONOTONE: it can only ever WITHDRAW an extremity classification, never add
# one, so nothing that is on the cloth path today can be moved off it.
#
# DEFAULT OFF pending an A/B and an in-game verdict -- it changes which passes
# run on a piece, which is the largest kind of change this file makes.
SLEEVE_GARMENT_NOT_EXTREMITY = _flag("CBBE2UBE_SLEEVE_GARMENT_GUARD", False)
# Spine mass must exceed hand mass by this factor before the shape is called a
# sleeved torso garment. 2.0 sits between `3BA Ref` at 1.03 and the real cases
# at 5.7-15.4.
SLEEVE_GARMENT_SPINE_MARGIN = _knob("CBBE2UBE_SLEEVE_GARMENT_MARGIN", 2.0)

# #boot-pelvis-only. Weigh the guard against PELVIS mass alone, not thigh+pelvis.
# A tall boot IS a thigh garment (thigh/ext up to 6.79 on thigh-high socks), so
# including thigh let a boot's own shaft read as a leg garment's hip anchor and
# reclassified 58 real footwear shapes. See the full measurement and the dead
# chain-bone variant at the guard itself. Set to 0 to restore thigh+pelvis.
LEG_GARMENT_PELVIS_ONLY = (
    _flag("CBBE2UBE_LEG_GARMENT_PELVIS_ONLY", True))


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
    if dom < max(8, int(0.01 * n)):
        return False
    if not LEG_GARMENT_NOT_EXTREMITY:
        return True
    # A LEG GARMENT IS NOT A BOOT. #leg-garment-not-extremity
    #
    # The vert-cluster test above is necessary but NOT sufficient: full-length
    # pants/leggings whose CUFFS overlap the feet clear it on the same fraction a
    # real boot does -- measured, pants 19/274 and boot 282/4074 are BOTH 6.9%, so
    # no cluster threshold can separate them. Misrouted, the piece takes the rigid
    # hand/foot branch, which returns before the normal cloth pass entirely: it
    # never gets collider handling, and it picks up a leg scale-bone graft from
    # the hands/feet call that the cloth path's collider guard never sees.
    #
    # WEIGHT MASS separates them, but ONLY THE PELVIS TERM. #boot-pelvis-only
    #     real boot   thigh  0.1%  pelvis  0.0%  foot 54.6%
    #     real glove  thigh  0.0%  pelvis  0.0%  hand 64.9%
    #     leg pants   thigh 35.1%  pelvis 12.7%  foot  6.7%
    #
    # THIS ORIGINALLY SUMMED THIGH + PELVIS, and the "~500x margin, cannot
    # misfire on real boots" that justified it is true only of the single ankle
    # boot it was tuned on. Measured over the whole converted pack, 58 real
    # footwear shapes reclassified, spanning ratio 0.53-1.02 across a boundary
    # at 1.00 while a heel survived at 1.02 -- the threshold ran straight
    # through the middle of the class it was supposed to protect.
    #
    # THIGH IS THE TERM THAT BREAKS IT. A tall boot IS a thigh garment: measured
    # thigh/ext reaches 6.79 on thigh-high socks, 2.77 on a knee boot, 1.31 on a
    # tall armoured boot. Summing thigh in lets a boot's own shaft stand in for a
    # leg garment's pelvis mass, and no threshold can then separate them.
    #
    # THE PELVIS TERM ALONE DOES SEPARATE. A boot is a TUBE AROUND THE LEG and
    # never reaches the pelvis; pants HANG FROM it. Measured pelvis/ext over the
    # pack: all 730 footwear shapes <= 0.189, while the leg garment this guard
    # was built for sits at 1.901. Dropping thigh fixes all 58 and breaks none.
    #
    # MONOTONE BY CONSTRUCTION, which is the point: `ext > thigh + pelvis`
    # implies `ext > pelvis` because thigh >= 0, so this rule can only ever
    # WITHDRAW an extremity->cloth flip, never add one. "Newly broken" is 0 by
    # proof rather than by measurement -- and a classifier change here is what
    # regressed boots in June.
    #
    # COVERAGE COST, stated plainly: leg garments caught drops 42 -> 10. Every
    # one dropped is rigid plate (greaves, metal leg shells) carrying no chain
    # bones, so it is inert on either path -- rigid is rigid. The exception is
    # one cloth `pants` shape at pelvis/ext 0.892 that now stays extremity; it
    # has no chain bones either, so it cannot hit the physics-collapse class,
    # but it does miss the cloth fit pass. Known and accepted.
    #
    # A CHAIN-BONE TERM WAS TRIED AND IS DEAD -- do not re-add it. Requiring "no
    # custom chain bones" to be an extremity flips real GLOVES: chain PRESENCE
    # catches 4 handwear shapes, and chain MASS does not separate them either
    # (glove arms_mesh 0.09245 vs dress 0.09164 -- indistinguishable).
    # CBBE2UBE_LEG_GARMENT_PELVIS_ONLY=0 restores the thigh+pelvis sum.
    anchor = 0.0
    for bn, pairs in bw.items():
        low = bn.lower()
        is_pelvis = "pelvis" in low
        # frontthigh/rearthigh are SCALE bones, not the animation thigh
        is_thigh = ("thigh" in low and "front" not in low and "rear" not in low)
        if not (is_pelvis or (is_thigh and not LEG_GARMENT_PELVIS_ONLY)):
            continue
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        anchor += sum(float(w) for _i, w in pl)
    if anchor > float(ext.sum()):
        return False                     # #leg-garment-not-extremity
    # #sleeve-garment-not-extremity: the ARM sibling. Same shape of rule --
    # a garment that HANGS FROM THE SPINE is not a tube around the forearm,
    # however much its cuffs weigh on the hand.
    if SLEEVE_GARMENT_NOT_EXTREMITY:
        hand_m = spine_m = 0.0
        for bn, pairs in bw.items():
            low = bn.lower()
            is_hand = any(k in low for k in ("hand", "finger", "thumb"))
            is_spine = any(k in low for k in
                           ("spine", "clavicle", "chest", "neck"))
            if not (is_hand or is_spine):
                continue
            pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            w = sum(float(x) for _i, x in pl)
            if is_hand:
                hand_m += w
            else:
                spine_m += w
        # `hand_m > 0` first: a scabbard handle and an embedded reference body
        # both carry spine mass with NO hand mass, and neither is a sleeve.
        if hand_m > 0.0 and spine_m > float(SLEEVE_GARMENT_SPINE_MARGIN) * hand_m:
            return False
    return True


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


# (moved to nif_convert_weights.py, 2026-09-01)


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

# --- #bust-plate-sync: a rigid bust PLATE follows the jiggle cloth beneath it ---
# A rigid bust plate (a metal cuirass front) sits OVER breast cloth but follows
# the breast far LESS (that plated top chest_plate band-follow 0.095 vs the cloth
# `top` 0.212), so under jiggle the cloth swings out THROUGH the stiffer plate --
# a MOTION defect invisible at bind pose, so an in-game look is the only judge.
# The fix (post-write, see _sync_bust_plate_follow_postwrite) raises the plate's
# breast-family SHARE among the bones it ALREADY has, per vert, up to the
# overlapping cloth's local fraction. No add_bone -> a physics/chain bone can
# never be introduced onto the plate, so the equip-CTD guard is STRUCTURAL rather
# than a name filter.
#
# DEFAULT ON (`=0` turns it off). History worth keeping, because the default has
# moved twice: flipped on after the plated top's verdict, then OFF when the first
# censused spot-check -- a layered robe -- SPLIT APART at the bust,
# then back ON once BUST_PLATE_SYNC_MIN_COVER made that piece a no-op (the split
# was a partly-reachable layer being raised in half; see that constant). Fires on
# 21 of 1658 censused pieces, 0 new bones anywhere.
# WHAT THIS PASS IS, HONESTLY: a DOWNSTREAM COMPENSATOR. The conversion does not
# preserve the AUTHORED breast follow -- measured source vs converted, the ruby
# flower's plate/cloth order INVERTS (0.181 > 0.135 becomes 0.095 < 0.212) and the
# the layered robe's layers are wiped outright (BodyStock 0.681 -> 0.000). This
# pass copies follow from a surviving layer to paper over that, which is why it
# cannot help a piece where the layers it would copy FROM were also wiped. The
# real fix is upstream, in whatever pass is eating the authored weights.
BUST_PLATE_SYNC = _flag("CBBE2UBE_BUST_PLATE_SYNC", True)
# Min mutual overlap (each layer's cleavage verts within CHEST_SYNC_DISTANCE of
# the other's, BOTH ways, over the cleavage box) to treat two breast-boned shapes
# as a genuine stacked bust. On that plated top, chest_plate<->top = 1.00/0.82; an
# ornament that merely grazes the chest overlaps one-way at most (corset/belts
# 0.18-0.25), cleanly excluded. COVERAGE, not weight -- a real plate HAS breast
# bones but low follow, so a weight gate would wrongly call it decorative.
BUST_PLATE_SYNC_OVERLAP = _knob("CBBE2UBE_BUST_PLATE_SYNC_OVERLAP", 0.5)
# Min band-follow GAP (authority - receiver) before a layer is worth syncing.
# Without it the pass rewrites thousands of weights to chase follow NOISE, which
# can only cost an authored difference.
#
# PACK CENSUS, measured BOTH WAYS on 1658 converted `_1` NIFs (the shipped pass
# applied to a copy of each, then diffed per (nif, shape) -- not inferred):
#     threshold 0     106 synced shapes / 95 pieces
#     threshold 0.02   66 synced shapes / 55 pieces   (40 dropped, 0 added)
# The 40 it drops have a MEDIAN actual follow change of 0.0003 and a MAX of
# 0.0416; NONE reaches 0.05, and only 4 reach 0.02. What survives keeps a median
# change of 0.0154 and a max of 0.4879. So this removes near-no-ops and leaves
# the real target class intact (that plated top gap 0.117, the big movers 0.4-0.6).
# NOTE the census's own printed "gap" column is an OVERESTIMATE -- it recovers
# the authority as the highest-follow UNCHANGED shape, which picks a shape that
# failed the overlap test when there is one. Trust this per-shape diff, not that
# column; predicting the drop from it was wrong by 2x (19 vs the real 40).
BUST_PLATE_SYNC_MIN_GAP = _knob("CBBE2UBE_BUST_PLATE_SYNC_MIN_GAP", 0.02)
# Min fraction of a receiver's verts that WANT raising and CAN be raised, before
# the piece is treated as safely fixable at all. A vertex carrying no breast bone
# cannot be raised without add_bone (the structural CTD guard), so on a shape
# whose breast bones cover only part of it the pass would raise one region and
# leave the rest -- REPORTED IN GAME: a robe's `TopLeather` carries R-side breast
# bones only, so its LEFT half went 0.000 -> 0.366 while the RIGHT half stayed at
# 0.000 and the garment split apart at the bust. Below this fraction NOTHING is
# written for the whole piece: a stack raised in part is not a partial fix, it is
# a fresh divergence between the layers that moved and the ones that could not.
BUST_PLATE_SYNC_MIN_COVER = _knob("CBBE2UBE_BUST_PLATE_SYNC_MIN_COVER", 0.90)
# #bust-plate-bone-shape -- ATTEMPTED AND REVERTED 2026-08-14. Recorded because
# the defect is real and the next attempt should not start from scratch.
#
# THE DEFECT. This pass matches a plate's breast FRACTION to the cloth's, which
# is the right SIZE and the wrong SHAPE when the plate has fewer breast bones.
# MEASURED on the reported plated top: UBE drives the breast hardest at the TIP
# (L Breast03 weight 351, centroid y 7.47) and least at the BASE (Breast01, 85,
# y 5.64); the cloth `top` spans all three out to y 8.32 while `chest_plate`
# carries Breast01 ALONE and took its entire raised follow -- 2381 units -- on
# that one base bone. A joint chain compounds, so the plate tracks the
# least-moving joint while the layer over it tracks the tip.
#
# WHY THE OBVIOUS FIX FAILED, in the order the walls were hit:
#   1. `s.bone_names` is CACHED and does not refresh after `add_bone`, so a
#      refreshed write list silently omits the new bones and they ship at ZERO
#      weight. Extend the list you already hold; never re-read it.
#   2. A vertex may carry only 4 influences (`MAX_BONES_PER_VERT`, already a
#      local in two other functions here). These plate verts are ALREADY at 3-4
#      with Spine1/Spine2/Clavicle/Breast01, so a three-bone breast chain does
#      not fit, and the writer evicts the smallest WITHOUT redistributing --
#      727 verts summing 0.956-1.063.
#   3. Choosing the best 4 explicitly and renormalising DOES give clean sums,
#      and then the chain bones lose the eviction to the spine weights: total
#      breast follow fell 3690 -> 2277 with no Breast02/03 surviving at all.
#      Correct weights, worse follow than doing nothing.
#
# So the budget, not the bone-add, is the binding constraint: the plate cannot
# hold the chain without giving up spine weight, and whether that is safe is
# unmeasured.
#
# THE WAY THROUGH (2026-08-14b): do not build a MIX -- TRANSPLANT. Rewrite WHICH
# chain bone carries the breast weight the vertex already has, instead of
# splitting that weight across three bones. One chain bone replaces another, so
# the influence COUNT never rises: the 4-per-vertex budget is never contested,
# nothing is evicted, and the per-vert weight sum is identical by construction --
# which is walls 2 and 3 above, both gone. Wall 1 still applies and is handled
# (extend the bone list you hold; never re-read `s.bone_names` after add_bone).
#
# MEASURED, region-matched per vertex on that plated top (nearest cloth vert
# within CHEST_SYNC_DISTANCE, over the jiggle band, 10910 verts). NOTE the
# whole-shape weight SUMS mislead here -- plate and cloth have different vertex
# counts over different areas -- and the earlier "the cloth tracks the TIP" read
# was one of those: the BODY is tip-heavy, the cloth over the plate is NOT.
#     chain fraction   Breast01  Breast02  Breast03   effective lever
#     chest_plate         1.000     0.000     0.000        0.000
#     top (cloth)         0.393     0.519     0.088        0.601
# The plate under-travels on 95% of matched verts. Residual lever error after a
# transplant, against 0.601 for doing nothing:
#     Breast01+02        mean 0.314  p90 0.417   47.8% closed   2 new bones
#     Breast01+02+03     mean 0.261  p90 0.405   56.6% closed   4 new bones
# p90 barely separates them; the full chain only wins in the tail. Depth 2 is the
# default because the one wall this does NOT knock down is that a transplant
# still needs `add_bone`, which trades #bust-plate-sync's STRUCTURAL "a chain
# bone can never reach a rigid plate" guard for a judgement -- so the smaller
# bone surface goes first.
#
# TRAP FOR THE NEXT ATTEMPT: `ctrl_parity.py` compares vertex POSITIONS, so it
# reports "reproduces" for a pass that only writes WEIGHTS -- including one that
# has died. Verify this pass on weights.
#
# OPT-IN. Off, this pass is byte-identical to the scale-only build.
BUST_PLATE_CHAIN_TRANSPLANT = _flag("CBBE2UBE_BUST_PLATE_CHAIN_TRANSPLANT", False)
# Chain bones, base -> tip. The index in this tuple IS the chain depth.
BUST_PLATE_CHAIN = ("Breast01", "Breast02", "Breast03")
# How far up the chain a transplant may reach. 2 = Breast01/02 (default), 3 =
# the full chain. Clamped to the tuple above.
BUST_PLATE_CHAIN_DEPTH = max(1, min(len(BUST_PLATE_CHAIN), _knob("CBBE2UBE_BUST_PLATE_CHAIN_DEPTH", 2, int)))
# The tighter JIGGLE BAND (front, z 88-104) that ranks authority. The full
# cleavage box (z 85-115) reaches the rigid upper chest and can rank a plate's
# breast fraction ABOVE the cloth's, inverting who should follow whom.
BUST_PLATE_BAND_Z_LO = 88.0
BUST_PLATE_BAND_Z_HI = 104.0
BUST_PLATE_BAND_Y_MIN = 2.0

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
_ABDO_JIGGLE_SYNC = (_flag("CBBE2UBE_ABDO_JIGGLE_SYNC", False))


# Target clearance the inner bust layer is pushed to. Env-overridable so it can
# be bisected: it was the only number in this chain that could not be, and the
# pass moves thousands of verts INWARD, which is the shape of a burial.
CHEST_DEPTH_SEPARATION = _knob("CBBE2UBE_CHEST_DEPTH_SEP", 0.4)
# #coincident-twin-not-a-layer -- DEFAULT ON, off with
# CBBE2UBE_NO_TWIN_LAYER_GUARD=1. See the receiver loop below for the
# measurement. Default ON rather than opt-in because the gate is extremely
# narrow -- it fires only when a shape's chest verts sit within
# TWIN_COINCIDENT_TOL of the authority's over TWIN_COINCIDENT_FRAC of them,
# which no genuine second layer does -- and the behaviour it replaces is
# destructive whenever it does fire.
TWIN_LAYER_GUARD = not _flag("CBBE2UBE_NO_TWIN_LAYER_GUARD", False)
# Tight on purpose. A bra and an outer fabric sharing a depth are ~0.1-0.4u
# apart, three orders of magnitude above this; a duplicate is bit-identical.
TWIN_COINCIDENT_TOL = _knob("CBBE2UBE_TWIN_COINCIDENT_TOL", 1e-3)
TWIN_COINCIDENT_FRAC = _knob("CBBE2UBE_TWIN_COINCIDENT_FRAC", 0.95)


def _is_coincident_twin(auth_src, auth_mask, recv_src, recv_mask,
                        auth_job, recv_job):
    """Is `recv` the same surface as `auth`, authored twice?

    Judged on the SOURCE geometry where available -- the author's own arrangement
    is the truth, and by this point in the pipeline both copies have been through
    the fit chain. Falls back to the current verts, which for a real twin are
    still identical because both copies were given identical treatment.

    Conservative on every failure: returns False, i.e. the pass behaves exactly
    as it did before. A guard that wrongly fires would silently disable a
    legitimate cleavage separation, so it must be the doubtful case that keeps
    the old behaviour, not the confident one.
    """
    try:
        a = auth_src if auth_src is not None else np.asarray(
            auth_job["verts"], dtype=np.float64)
        r = recv_src if recv_src is not None else np.asarray(
            recv_job["verts"], dtype=np.float64)
        ac, rc = a[auth_mask], r[recv_mask]
        if len(ac) < 5 or len(rc) < 5:
            return False
        from scipy.spatial import cKDTree
        d, _ = cKDTree(ac).query(rc, k=1)
        return float((d <= TWIN_COINCIDENT_TOL).mean()) >= TWIN_COINCIDENT_FRAC
    except Exception as _pe:
        _note_pass_failure("_is_coincident_twin", _pe)
        return False
CHEST_DEPTH_FRONT_TOL = 0.2      # only push receiver verts within this distance in FRONT
                                  # of the authority. Verts clearly in front (ornaments,
                                  # straps, cloaks) are left alone so the pass doesn't sink
                                  # legitimate outer pieces. Push band: (-SEPARATION, +TOL).
CHEST_DEPTH_PAIR_XZ_DIST = 3.0   # max (X,Z) distance to pair an inner vert with an
                                  # authority vert (larger than inter-vert spacing; smaller
                                  # than cross-piece gap so opposite-side cloth is ignored).


# (split step 7) _LAYER_ORDER_ITERS, _LAYER_ORDER_NEAR, _GLOW_RIDE_MAX, _LAYER_RIDE_MAX ... live in nif_convert_layers.py
# since 2026-09-01. Imported BY NAME so `nc.<name>` keeps working everywhere.
from .nif_convert_layers import (  # noqa: E402
    _LAYER_ORDER_ITERS, _LAYER_ORDER_NEAR, _GLOW_RIDE_MAX, _LAYER_RIDE_MAX,
    _SEAM_WELD_TOL, _authored_layer_depth, _authored_layer_relation,
    _stack_depth_from_relation, _canonical_stack_name_groups,
    _stacked_layer_groups, _layered_cloth_shape_names, _closest_point_on_tris,
    _ride_disp_barycentric, _feather_ride_disp,
    _ride_effect_overlays_on_plate, _ride_layers_on_reference,
    _layer_order_eligible, _repair_layer_order,
    _separate_abdomen_layered_cloth_depth,
    _separate_chest_layered_cloth_depth, _weld_cross_shape_seams,
)



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
LAYER_STACK_GAP = _knob("CBBE2UBE_LAYER_STACK_GAP", 0.15)     # clearance a locally-outer layer keeps above the outermost inner vert beneath it
# v4 gating thresholds for _separate_abdomen_layered_cloth_depth:
OVERLAY_LOCAL_ORDER_MIN = 0.05  # min kernel-averaged gap field for tier-1 constraint;
                                # genuinely interleaved weaves cancel to ~0 -> no constraint.
OVERLAY_LOCAL_CONSIST = 0.70    # fraction of nearby gap samples that must agree in sign;
                                # interleaved noise ~0.5, coherent reversals ~1.0.
OVERLAY_LOCAL_RAW_STRONG = _knob("CBBE2UBE_OVERLAY_RAW_STRONG", 0.12)  # tier-2: fires on own raw source gap even without
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


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_bust.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


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


# (moved to nif_convert_weights.py, 2026-09-01)

# A single skin partition with a very high VERTEX count is not safe for the
# runtime body-morph rebuild (NioOverride/RaceMenu): measured equip CTD when a
# shape sat in ONE partition and the morph walk read past the vertex buffer at
# vertex 32768 -- i.e. the 16-bit vertex-INDEX limit (32767). We split any shape
# that would exceed that limit in a single partition (CTD-safe, drops no
# bone/vert). Distinct from the BONE-count cap.
#
# 2026-07-13: the cap was 16000 -- HALF the real 16-bit limit -- so it split
# body-sized morphable armor shapes that were nowhere near the overflow
# (a large arm shape 29012v, a shoe 24088v). That UNNECESSARY 2-way split corrupts the
# skin partition for HDT-SMP's per-frame actor re-skin: FSMP's Update moves the
# skeleton, the game re-skins the split shape, and the read runs off the end ->
# EXCEPTION_ACCESS_VIOLATION in hdtsmp64.dll (and the same bad skin data renders
# as an exploded/garbage mass BEFORE the crash). Confirmed in-game: merging the
# two partitions back to one on the deployed NIFs fixed BOTH the crash and the
# explosion. The injected UBE body (29298v) already ships a SINGLE partition and
# has always worked, which is the proof that ~29-32k in one partition is safe.
# Raised to 31000 -- below the ONE measured ~31.8k CTD, above every body-sized
# armor shape -- so nothing splits unless it genuinely approaches the 16-bit
# index limit. Shapes still over 31000 (rare: 50k+ fur/book meshes) must split
# regardless (they overflow the index) and are unaffected. #partition-split-smp
SKIN_PARTITION_VERT_CAP = 31000


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


HDT_BONE_THRESHOLD = 0.4  # fraction of armor bones unknown to the body
                          # required to classify a shape as HDT-SMP rigged.

# A bone the ACTOR SKELETON supplies, carrying no physics role, is NOT evidence
# of author physics rigging -- it is a gap in the reference BODY.
# `_shape_has_hdt_smp_rigging` counts every bone the injected BaseShape lacks,
# and the BaseShape is a body: no hand, finger, pauldron or twist bones. So a
# 2-bone pauldron clears the 0.4 ratio by missing both, and loses the clearance
# pass. Measured over a converted modlist, 26 of 236 gated shapes are this
# misclassification (MiraakPauldrons 2/2, Claws 32/38 hand+finger).
#
# DELIBERATELY NOT APPLIED INSIDE `_shape_has_hdt_smp_rigging`. That was built
# and measured first, and the predicate gates EIGHT call sites -- so relaxing it
# there also switched on the M6 reskin and the conform/graft passes for these
# shapes. Measured on a vanilla robe: the pauldrons moved 435/474 verts and
# their clearance went DOWN ~0.07u, a direction the clearance pass cannot even
# produce, because the movement was coming from the other passes. One predicate
# feeding eight decisions is exactly why this is scoped to the ONE call site
# that asks the question this answers.
#
# Jiggle and cloth-named bones stay evidence EVEN WHEN THE SKELETON HAS THEM:
# XPMSE ships `SkirtFBone01`, and treating skeleton membership alone as
# "structural" reclassified 26 genuine chain shapes -- the skirt-collapse family.
#
# The floor of 3 is measured, not chosen: at 3 the collateral is 0/194 real chain
# shapes with 22/26 misclassifications recovered; at 4 it is 6/194. The two
# populations touch at exactly 3, so 4 of the 26 stay conservatively excluded.
#
# DEFAULT OFF -- the detection is right and the CONSEQUENCE was measured HARMFUL.
# Identifying these shapes as structural is correct; letting the clearance pass
# run on them is not. A/B on a vanilla robe, both weights, everything else
# identical (the only shape that changes is MiraakPauldrons, 435/474 verts):
#
#     weight   verts INSIDE body   worst poke        mean clearance
#     _0        5 ->  9            -1.335 -> -1.026u  +1.086 -> +1.002u
#     _1        7 -> 10            -0.968 -> -1.972u  +1.087 -> +1.001u
#
# Worse on every measure, consistently, including the count under the 0.8u
# target (250->282, 254->276). This is the failure the gate comment in
# convert_nif_phase2 already records -- anti-poke fixes FLAT regions and spreads
# verts on CONVEX ones (a suit's butt 28%->29%, worst poke -1.81->-2.20u).
# Pauldrons sit on convex shoulders and reproduce it almost exactly.
#
# Kept, not deleted: the classification is sound and reusable, and the next
# person to notice these shapes get no clearance deserves the measurement rather
# than a second run at the same wall. CBBE2UBE_SMP_STRUCTURAL_RELAX=1 to enable.
# #smp-structural-relax
_SMP_STRUCTURAL_RELAX = (
    _flag("CBBE2UBE_SMP_STRUCTURAL_RELAX", False))
_SMP_CHAIN_EVIDENCE_MIN = 3
_CLOTH_BONE_RE = re.compile(
    r"skirt|cloth|cape|coat|robe|dress|tail|hair|belt|sash", re.I)


# (moved to nif_convert_weights.py, 2026-09-01)


def _smp_rigging_is_structural_only(src_shape, body_bone_names: set) -> bool:
    """True when a shape classified as SMP-rigged has NO physics evidence.

    i.e. `_shape_has_hdt_smp_rigging` said yes, but only because the reference
    BODY lacks ordinary skeleton bones the garment is weighted to. Such a shape
    has no simulation to disturb, so the ANTI-POKE may safely run on it.

    Answers a narrower question than the predicate it refines, and is used at
    exactly one call site. Returns False when no skeleton can be loaded --
    without one there is nothing to tell structural from authored, and keeping
    the conservative answer beats guessing.
    """
    if not _SMP_STRUCTURAL_RELAX:
        return False
    armor_bones = set(getattr(src_shape, "bone_names", None) or [])
    if not armor_bones:
        return False
    try:
        if not _actor_skeleton_bone_names():
            return False
    except Exception:
        return False
    evidence = {b for b in armor_bones - set(body_bone_names)
                if _is_physics_evidence_bone(b)}
    return (len(evidence) < _SMP_CHAIN_EVIDENCE_MIN
            and len(evidence) / len(armor_bones) <= HDT_BONE_THRESHOLD)


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
    _flag("CBBE2UBE_CHAIN_TO_SOFTBODY", False)
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
# and the engine CTDs on equip (a layered-cloth cuirass, crash 2026-07-09). So KEEP
# their SOURCE skin -- skip EVERY graft pass for them (pynifly can't cleanly remove a
# bone after the fact, so prevention is the only reliable path). Detect structurally:
# 2+ sibling shapes sharing a base stem + a short layer suffix. Off with
# CBBE2UBE_NO_LAYERED_CLOTH_SKIN. #layered-cloth-skin
_LAYERED_CLOTH_SKIN = (
    not _flag("CBBE2UBE_NO_LAYERED_CLOTH_SKIN", False))
_LAYER_SUFFIX_RE = re.compile(r"^(.*?)[_ ]([A-Za-z]|\d{1,2})$")

# #layer-follow-divergence. A GEOMETRIC companion to the name-based detector
# above, for ONE consumer: the full-vector weight match.
#
# REPORTED IN GAME after 1.3-alpha: "layers clipping into other layers (not the
# body)". The full-vector match copies the covered body's whole weight row into
# each shape INDEPENDENTLY, and two stacked layers do not get the same answer.
# Measured on that piece as mean weight-row divergence between stacked vertex
# pairs -- 1.2 -> now: chest_plate/top 0.190 -> 0.309, belts/corset 0.026 ->
# 0.071, belts_metal/belts 0.027 -> 0.204. Turning the pass off restores EVERY
# pair to its 1.2 value, which is what makes this the cause and not a
# correlate.
#
# TWO mechanisms, one root, and neither is fixed by sharing a single body
# lookup between the shapes:
#   * BASIS. The body row is projected onto each shape's OWN bone list and
#     renormalised, so `top` (26 bones, including Breast02/03 and Belly) and
#     `chest_plate` (9, none of those) get different rows from the SAME body
#     vertex. Renormalising both onto their shared bones collapses that pair
#     0.288 -> 0.062, so basis is ~76% of it.
#   * RAY PAIRING. `pair_by_ray` casts from each vert along its OWN normal, so
#     stacked layers hit different body triangles by construction. That is the
#     whole of `belts_metal/belts`, which survives every other control: both
#     rows rewritten, same KD body vertex, shared basis, still 0.190 -> 0.191.
#
# Ruled out by measurement, so do not re-attempt them: PARTIAL APPLICATION
# (18,254 of 18,389 stacked pairs have BOTH members rewritten, and the
# rewrote-only-one class diverges LESS, 0.185 vs 0.310), and pairing alone
# (same-KD-vertex pairs diverge as much as different-vertex ones).
#
# The name-based detector cannot see this piece: its layers are `chest_plate`,
# `top`, `belts`, `corset`, `belts_metal` -- named semantically, sharing no
# stem -- so the guard written for exactly this defect returned an empty set.
# Same failure as #conform-skip-two-detectors: one concept, and a predicate
# that had drifted away from it.
#
# SKIPPING STACKED SHAPES WAS MEASURED AND REJECTED. Extending the existing
# keep-source-skin guard geometrically is the obvious fix and it is far too
# blunt: censused over the converted pack (977 NIFs / 2,802 garment shapes),
# a 2.0u/30% stacking rule covers 66.6% of shapes and 2.0u/50% still covers
# 55.9%. That would switch the pass off for two thirds of the pack, including
# the single-layer cuirasses whose in-game verdict is what earned it its
# default. So the pass stays ON and is made COHERENT instead.
#
# Nor is this folded into `_layered_cloth_shape_names`: that set feeds seven
# sites including `_preserve` (keep authored skin), and widening it there
# would change source-skin decisions across the whole pack.
#
# WHAT THE FIX DOES. For each stacked group, the members resolve the body
# through ONE shared anchor -- the innermost member's surface point and normal,
# so the group gets one pairing instead of one per shape -- and the copied row
# is restricted to the bones every member of the group shares. A group can only
# follow the body as far as its least-capable member: two layers 2u apart that
# follow different bones interpenetrate, so the intersection is not a
# concession, it is the physical constraint.
# IT WAS DEFAULTED OFF ON 2026-08-11 AND IS ON AGAIN THE SAME DAY. The history
# stays because the successor must not repeat it.
#
# Shipped ON, reconverted, and judged in game the same day: "clips at the belts
# and the breasts", "the back hard regressed at poses", "the sleeves being
# bound". Measured on that piece, 1.2 -> this pass ON:
#
#   top          ARM 3840.6 -> 2928.3   BREAST 708.1 ->  118.9
#   chest_plate  ARM   73.0 ->    0.3   BREAST 1208.3 -> 155.2
#
# and the lost mass landed on SPINE/CLAVICLE (+1190 and +1126). Bound sleeves,
# a bust that no longer follows, and a back that tracks the torso rigidly are
# exactly what those three numbers predict. Same build with this OFF keeps them
# (ARM 3741.2 / 81.4, BREAST 990.8 / 1008.9), which is what makes this the
# cause rather than a correlate.
#
# WHY THE DESIGN IS WRONG, not just mistuned. Routing every stacked layer
# through the INNERMOST member's anchor means a sleeve -- grouped with the
# chest plate because they overlap -- resolves its body row through a TORSO
# point instead of an arm one. Sharing an anchor is only sound where the layers
# genuinely cover the same anatomy, and "stacked within 2u" does not establish
# that.
#
# THE WARNING WAS THERE AND I TALKED PAST IT: the same A/B showed `top`'s own
# body gap rising 0.082 -> 0.159 and I recorded it as "by design". A layer that
# fits the body WORSE is the defect, not the design.
#
# RESOLVED, AND VERIFIED IN GAME BEFORE THIS DEFAULT. The successor reconciles
# layers only WHERE THEY OVERLAP (`near_ok`) and prefers the innermost REACHABLE
# inner layer, so a sleeve grouped with a chest plate keeps an ARM anchor. Judged
# in game on the reported piece: back, sleeves and bust all confirmed good, with
# follow intact -- `top` ARM 3741.2 (identical to this OFF) and `chest_plate`
# BREAST 1064.2 (BETTER than OFF's 1008.9) -- and four of five layer pairs at or
# below their 1.2 divergence. Judged on BOTH numbers, because judging it on
# divergence alone is precisely what shipped the broken version.
#
# DEFAULT ON since 2026-08-11, off-switch `CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD`.
# It was opt-in for exactly as long as it took to fix the anchor: an opt-in that
# nothing sets is a fix that does not ship, which is the finding of the flag
# surface audit (#audit-2026-08-01-flags), not a hypothetical.
_FULL_WEIGHT_LAYER_GUARD = (
    not _flag("CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD", False))
# COVERAGE, not contact. A trim strip or a buckle touches a cuirass along its
# border and is not a layer; a layer shadows a large share of its neighbour.
_LAYER_STACK_RADIUS = _knob("CBBE2UBE_LAYER_STACK_RADIUS", 2.0)
_LAYER_STACK_COVER = _knob("CBBE2UBE_LAYER_STACK_COVER", 0.30)
# THE SHARED BASIS IS OFF, AND THE COUNTER-METRIC IS WHY.
#
# Restricting a stacked group's copy to the bones every member shares does fix
# the divergence -- on the reported piece it took all five pairs to <=0.032,
# below their 1.2 values. It also wrecks the two things the pass exists for.
# Same build, same piece, 1.2 -> 1.3-alpha -> shared basis:
#
#   chest_plate  jiggle mass 1208 -> 958 -> 155   body gap 0.253 -> 0.298 -> 0.617
#   top          jiggle mass  710 -> 993 ->  76   body gap 0.082 -> 0.082 -> 0.571
#   corset       jiggle mass  6.7 -> 6.6 ->  0.9  body gap 0.010 -> 0.015 -> 0.431
#
# ~87% of the breast/butt/belly follow destroyed and body-follow 7x worse than
# EITHER baseline. The cause is structural, not a threshold: the five layers
# form one connected group, so the shared set collapses to what an 8-bone
# accessory and a 9-bone plate have in common and every layer renormalises onto
# that stub. Kept behind a flag because it is the only construction that makes
# stacked layers deform identically, and a future design may want it for a
# group whose members already agree on bones -- but it must never default ON
# without a counter-metric beside it.
_LAYER_STACK_SHARED_BASIS = (
    _flag("CBBE2UBE_LAYER_STACK_SHARED_BASIS", False))


def _is_first_person_mesh(dst_path, nif) -> bool:
    """A FIRST-PERSON mesh: the player's viewmodel, never simulated cloth.

    Auto-generating an HDT-SMP config for one is worse than useless. FSMP merges every
    `<per-vertex-shape name="...">` into the ACTOR's physics system by SHAPE NAME, and a
    first-person NIF carries the SAME shape names as its third-person twin (a layered-cloth mod's
    `1st.nif` and `cuirass.nif` both hold `Cuirass_A/_B/_C`). So the first-person XML
    ends up driving the third-person shapes, as skin-stripped cloth with nothing to
    constrain it -> FSMP's soft body diverges and its collision SIMD reads out of bounds
    -> access violation on equip (crash 2026-07-09). Deleting exactly those two XMLs is
    what fixed it in-game; the third-person NIF never emitted one (its body collider +
    absent chain already trip `_is_unconstrained_collision_pair`).

    Detection is name AND structure, because neither alone is safe:
      * name only -- a `1st...`-prefixed stem can belong to an ITEM whose NAME simply
        starts with "First" (e.g. a "First ... Garb" outfit), a third-person body
        armor that would silently lose its physics.
      * structure only -- a cloak, boots and gloves also carry no injected `BaseShape`,
        and they DO want physics.
    A genuine first-person mesh is the viewmodel: it never has a body injected into it.
    """
    try:
        stem = Path(dst_path).stem.lower()
    except Exception:
        return False
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    if "1st" not in stem and "firstperson" not in stem:
        return False
    try:
        return not any(s.name in UBE_BODY_INJECT_NAMES for s in nif.shapes)
    except Exception:
        return False


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# #layer-group-canonical. WHICH shapes form a stack is a property of the
# GARMENT; WHERE their shared anchor sits is a property of the BODY WEIGHT.
# Deciding both from the file in hand made the first half weight-dependent:
# `_0` and `_1` are the same garment on different bodies, so a coverage fraction
# sitting near the threshold flips between them.
#
# MEASURED over the reconverted pack: of 812 pieces that form a stack, **51
# (6.3%) grouped DIFFERENTLY at `_0` than at `_1`** -- e.g. one guard cuirass
# partitioned (0,1,2)+(3,4,5) at `_1` and (0,1,3,4,5) at `_0`. Since the engine
# MORPHS BETWEEN the two meshes, skinning them on different groupings is exactly
# the per-weight leak the postflight parity check exists to catch.
#
# So the grouping is decided ONCE, on the `_1` source, and both weights reuse
# it. Source-side is also the more defensible frame: whether two layers are
# stacked is authored, not a consequence of which body they were fitted to.
# The ANCHOR is still resolved per weight from the shapes in hand, because that
# one genuinely differs.
#
# Memoised on (path, mtime, size) -- the pass runs five instances per weight and
# would otherwise re-read the source NIF ten times per piece.
_STACK_NAME_GROUP_CACHE: "dict[tuple, list]" = {}


# (moved to nif_convert_layers.py, 2026-09-01)


def _dst_groups_for_names(shapes, name_groups, exclude) -> "list[list]":
    """Rebuild the canonical groups against the shapes IN HAND.

    Names come from the canonical weight; the world verts must come from this
    file, because the anchor is measured against this body. A name the source
    grouped but this file lacks is simply dropped -- a group that ends up with
    fewer than two members here is not a stack here.
    """
    by_name = {}
    for s in shapes:
        nm = getattr(s, "name", "") or ""
        if not nm or nm in exclude or nm in RESKIN_SKIP_NAMES:
            continue
        try:
            sv = np.asarray(s.verts, dtype=np.float64)
            if len(sv) == 0:
                continue
            by_name[nm] = (nm, _verts_skin_to_world(
                sv, _shape_global_to_skin(s)), s)
        except Exception as _e:
            _note_pass_failure("_dst_groups_for_names/verts", _e)
    out = []
    for g in name_groups:
        members = [by_name[n] for n in sorted(g) if n in by_name]
        if len(members) >= 2:
            out.append(members)
    return out


def _stacked_layer_plan(groups, tree, ube_bones) -> dict:
    """Per-shape anchor surface + shared bone basis for each stacked group.

    THE ANCHOR IS THE LAYER DIRECTLY BENEATH, AND IT IS LOCAL. Members are
    ordered by depth (median distance to the body), and each vert anchors to the
    nearest point on ANY member closer to the body than its own -- within the
    stacking radius, or not at all.

    Two corrections are folded in here, each paid for in game:

    * LOCAL (#layer-anchor-local). The first version queried the innermost
      surface for EVERY vertex with no distance limit, so a SLEEVE vertex 20u
      from the corset still borrowed the nearest corset point -- a TORSO anchor
      for arm geometry. Reported as "the sleeves being bound", measured as `top`
      ARM 3840.6 -> 2928.3 and `chest_plate` 73.0 -> 0.3. Reconciling two layers
      only means anything where they OVERLAP.

    * ADJACENT, not innermost (#layer-anchor-adjacent). Anchoring everything to
      the GLOBALLY innermost member leaves a three-deep stack inconsistent:
      `belts` sits within 2u of the corset and takes the shared anchor, while
      `belts_metal` sits on `belts` and is FURTHER than 2u from the corset, so it
      keeps its own -- and the two belts end up on different anchors, which is
      exactly the pair that stayed divergent (0.107 against 1.2's 0.027).
      Anchoring to whatever lies directly beneath makes the agreement propagate
      inward layer by layer.

    THE BASIS. Kept behind a flag and OFF -- restricting the copy to the bones
    every member shares fixes divergence and costs ~87% of the jiggle follow,
    because the group collapses to what its least-capable member can express.
    See _LAYER_STACK_SHARED_BASIS.

    Returns {name: {"pos", "nrm", "basis", "near_ok"}}.
    """
    from scipy.spatial import cKDTree as _KD
    plan: dict = {}
    for g in groups:
        basis = None
        for nm, _wv, s in g:
            own = {b for b in (getattr(s, "bone_names", None) or ()) if b in ube_bones}
            basis = own if basis is None else (basis & own)
        if not basis:
            # Nothing shared to copy onto. Leaving the group unplanned would let
            # every member re-diverge, so record the empty basis explicitly and
            # let the caller refuse the group.
            basis = set()
        # Order by DEPTH -- median distance to the body -- so "beneath" is
        # defined before anything anchors to anything.
        ranked = []
        for nm, wv, s in g:
            try:
                d, _ = tree.query(wv, k=1)
                ranked.append((float(np.median(d)), nm, wv, s))
            except Exception as _e:
                _note_pass_failure("_stacked_layer_plan/depth", _e)
        if not ranked:
            continue
        ranked.sort(key=lambda r: r[0])          # innermost first
        # Accumulate the surface of everything already placed, so each member
        # anchors to whatever lies DIRECTLY BENEATH it rather than to the
        # globally innermost layer. #layer-anchor-adjacent
        below_v: "list" = []
        below_n: "list" = []
        for _med, nm, wv, s in ranked:
            try:
                own_n = _vertex_normals_from_tris(
                    wv, np.asarray(s.tris, dtype=np.int64))
            except Exception as _e:
                _note_pass_failure("_stacked_layer_plan/normals", _e)
                own_n = None
            if not below_v:
                # The innermost member has nothing beneath it; it IS the anchor
                # the next layer up will use.
                if own_n is not None:
                    plan[nm] = {"pos": wv, "nrm": own_n, "basis": set(basis),
                                "near_ok": np.ones(len(wv), dtype=bool)}
                    below_v.append(wv)
                    below_n.append(own_n)
                continue
            # PREFER THE INNERMOST LAYER, FALL BACK TO THE NEAREST REACHABLE
            # ONE. Measured on a five-layer piece, neither rule alone wins:
            #
            #   pair                 1.2     innermost-only   adjacent-only
            #   belts_metal/belts  0.027        0.107            0.045
            #   chest_plate/corset 0.128        0.013            0.190
            #   top/corset         0.075        0.000            0.038
            #
            # Anchoring everything to the innermost layer is best WHERE IT IS
            # REACHABLE -- `chest_plate` sits within the radius of the corset and
            # goes to 0.013. It fails where it is not: `belts_metal` sits on
            # `belts` and is out of range of the corset, so it kept its own
            # anchor and stayed divergent. Preferring the innermost and only
            # falling back when it is out of reach gets both.
            # #layer-anchor-innermost-first
            in_v, in_n = below_v[0], below_n[0]
            try:
                d0, n0 = _KD(in_v).query(wv, k=1)
            except Exception as _e:
                _note_pass_failure("_stacked_layer_plan/anchor-inner", _e)
                continue
            d0 = np.asarray(d0, dtype=np.float64)
            n0 = np.asarray(n0, dtype=np.int64)
            pos = in_v[n0]
            nrm = in_n[n0]
            near_ok = d0 <= _LAYER_STACK_RADIUS
            if len(below_v) > 1 and not near_ok.all():
                # Out of reach of the innermost: use the nearest point on ANY
                # layer beneath, which for a three-deep stack is the one
                # directly below.
                bV = np.vstack(below_v)
                bN = np.vstack(below_n)
                try:
                    d1, n1 = _KD(bV).query(wv, k=1)
                except Exception as _e:
                    _note_pass_failure("_stacked_layer_plan/anchor-any", _e)
                    d1 = None
                if d1 is not None:
                    d1 = np.asarray(d1, dtype=np.float64)
                    n1 = np.asarray(n1, dtype=np.int64)
                    _fb = (~near_ok) & (d1 <= _LAYER_STACK_RADIUS)
                    if _fb.any():
                        pos = pos.copy()
                        nrm = nrm.copy()
                        pos[_fb] = bV[n1[_fb]]
                        nrm[_fb] = bN[n1[_fb]]
                        near_ok = near_ok | _fb
            plan[nm] = {"pos": pos, "nrm": nrm, "basis": set(basis),
                        "near_ok": near_ok}
            if own_n is not None:
                below_v.append(wv)
                below_n.append(own_n)
    return plan


# (moved to nif_convert_writer.py, 2026-09-01)


# The sign guard below rescues normals the recompute cannot determine. It used
# to apply to EVERY vertex, on the docstring's assertion that it "only matters
# at boundary verts where the recompute is fundamentally underdetermined" --
# which nothing measured. It does not: on one garment it forced the source's
# sign onto vertices with a full, agreeing triangle fan, storing normals that
# point into the surface. Those shade as if lit from behind, and no positional
# metric can see them -- three fixes aimed at stretch missed them entirely.
#
# Determinacy is the property that actually separates the two populations, and
# it needs BOTH terms. Coherence alone scores a vertex belonging to ONE
# triangle as perfectly coherent (nothing to disagree with), which is exactly
# the isolated-tab case the guard exists for -- measuring its own blind spot.
# The pubic-loop regression the guard was written for is fan-1 boundary verts,
# so it stays guarded under `fan < 3`.
#
# CBBE2UBE_NO_NORMAL_DETERMINACY=1 restores the blanket flip.
NORMAL_SIGN_GUARD_DETERMINED = not _flag("CBBE2UBE_NO_NORMAL_DETERMINACY", False)
# A vertex needs a real fan, and that fan has to agree, before its recomputed
# normal is trusted over the source's sign.
NORMAL_DETERMINED_FAN_MIN = 3
NORMAL_DETERMINED_COHERENCE_MIN = 0.5
# The determinacy gate handles the sign by FLIPPING the recompute toward the
# source. That is wrong at a thin-strap RIM: the rim is a topology boundary where
# the area-weighted recompute is meaningless (double-sided faces cancel to a
# sideways normal), so flipping it just relocates garbage -- the white "shard"
# class (measured on a plated top's belt: 9 rim verts backwards vs their own
# geometry, all on boundary edges, while the AUTHORED source normals there are
# correct at 0.99). When on, this takes the AUTHORED source normal outright at
# boundary verts and trusts the recompute in the interior -- fixing the rim
# shards, preserving the pubic-loop case (also a boundary), and no longer
# flipping a sharp manifold stud to a stale source.
#
# EFFECTIVELY DEFAULT **ON**, and VERIFIED IN GAME -- see the conditional right
# below, which forces it wherever a clearance-field solve is active, and that
# solve is itself default ON. The env read here is the bare default and is
# shadowed; this line used to end "DEFAULT OFF pending in game", which was the
# exact opposite of what ships and of what has been judged. Same shadowing shape
# as `LAYER_ORDER_REPAIR_ENABLED`, which the clearance-field default silently
# turned OFF and which cost a set of normal-determinacy tests. When reading any
# flag in this file, check for a conditional reassignment before trusting the
# comment on its declaration.
NORMAL_SIGN_GUARD_BOUNDARY = _flag("CBBE2UBE_NORMAL_GUARD_BOUNDARY", False)
# Part of the clearance-field pipeline. That build drops the layer-order repair,
# so this sign-guard rim garbage is the last broad shading defect left; the
# boundary-source form fixes it (measured: belt rim verts output-vs-author
# 0.899 -> 1.000, misaligned 181 -> 0). Default it on wherever a clearance-field
# solve is active. VERIFIED IN GAME. Force independently with the env flag.
if CLEARANCE_FIELD_SOLVE or CLEARANCE_FIELD_INFLATE:
    NORMAL_SIGN_GUARD_BOUNDARY = True


# (moved to nif_convert_writer.py, 2026-09-01)


# Pubic-region bbox for the topology-hole closure pass on UBE BaseShape.
# UBE ships BaseShape with open boundary loops at the pubis (for TNG/SoS plug-mesh).
# _close_pubic_holes seals them via fan triangulation (no new verts added).
# Bounds exclude DESIGNED-open loops: neck (Z=60-114), wrist (X up to +-28),
# and ankle (Z=11-63) that other mesh shapes attach into.
PUBIC_HOLE_Z_MIN = 63.0
PUBIC_HOLE_Z_MAX = 72.0
PUBIC_HOLE_X_BOUND = 6.0


# (moved to nif_convert_writer.py, 2026-09-01)


# #coherence-repair. A THIN feature (a hem rim, a seam ridge) buckles when
# neighbouring verts take different displacements -- even sub-unit ones that
# every absolute-magnitude guard in the pipeline passes. Measured: 0.41u of
# differential wrecks a 2.2u rim while the same displacement on a panel is
# invisible. Default ON: it only touches patches whose normals demonstrably went
# from coherent to scattered, and it PRESERVES each patch's mean displacement, so
# the fit (clearance / standoff) is unchanged -- it removes the high-frequency
# differential, not the warp. CBBE2UBE_NO_COHERENCE_REPAIR=1 is the hatch.
COHERENCE_REPAIR = (
    not _flag("CBBE2UBE_NO_COHERENCE_REPAIR", False))

# Stop the coherence repair pulling a garment vertex through the skin.
# #coherence-repair-outside-body -- the reasoning and the stage trace live beside
# `_hold_repair_outside_body` in nif_convert_writer.py, which reads this through
# `_nc()`. Default OFF pending a measured A/B.
# PROMOTED TO DEFAULT ON 2026-09-09, to match what actually ships. The
# deployed recipe has set this since before the 2026-09-06 reconvert, so
# the pack has been built with it ON while the code said OFF -- and the
# code is what every reader consults. `flag_retirement --recipe` now
# measures that split; it was 5 flags, all in this direction.
# Kill switch: CBBE2UBE_NO_COHERENCE_REPAIR_OUTSIDE_BODY=1
COHERENCE_REPAIR_OUTSIDE_BODY = (not _flag(
    "CBBE2UBE_NO_COHERENCE_REPAIR_OUTSIDE_BODY", False))
COHERENCE_MIN_AREA = _knob("CBBE2UBE_COHERENCE_MIN_AREA", 4.0)
COHERENCE_SRC_MIN = _knob("CBBE2UBE_COHERENCE_SRC_MIN", 0.70)
COHERENCE_OUT_MAX = _knob("CBBE2UBE_COHERENCE_OUT_MAX", 0.30)
COHERENCE_ITERS = _knob("CBBE2UBE_COHERENCE_ITERS", 12, int)
# Rings of one-ring neighbours added around a buckled patch before pinning the
# boundary. 0 pins the patch rim itself, which does NOT work -- see the note at
# the dilation. FEATHER ONCE: this is the single smoothing region, not a chain.
COHERENCE_DILATE = _knob("CBBE2UBE_COHERENCE_DILATE", 2, int)
# A patch whose bbox min extent is under this is a THIN STRIP (a hem rim, a
# seam ridge) and is moved RIGIDLY rather than smoothed -- see #coherence-rigid.
COHERENCE_THIN = _knob("CBBE2UBE_COHERENCE_THIN", 3.0)
# #coherence-thin-area -- OPT-IN, and it did NOT do what it was built for.
#
# Theory: a thin strap's features are all small-area by construction, so the
# absolute floor rejects the very class this pass repairs. `belts` carried 82
# folded boundary verts and the repair had never run on it once, while firing
# repeatedly on the thicker shapes.
#
# MEASURED AT 0.15: the repair STILL never touched `belts` (flipped normals only
# 82 -> 74, which is the winding repair, not this). The area floor was not why
# the belts were skipped -- see #coherence-kink: a FOLD is a RIGID rotation, so
# the patch's mean-normal coherence is PRESERVED and the collapse criterion
# (`co <= COHERENCE_OUT_MAX`) can never fire on it.
#
# It did, however, widen the repair's reach on the shapes it already handled --
# `chest_plate` 2 -> 5 patches, `top` 1 -> 10 -- and improved THEIR flipped
# normals (`top` 13 -> 3, `chest_plate` 4 -> 2). That is a real gain on the same
# defect class, but it is an unmeasured widening of a fit pass over ~2800 pieces,
# so it ships at 1.0 (no change) until censused. Set 0.15 to re-enable.
COHERENCE_THIN_AREA_SCALE = _knob("CBBE2UBE_COHERENCE_THIN_AREA_SCALE", 1.0)
# For a THIN strip, gate on how far coherence FELL rather than its absolute
# value -- a rim that reorients coherently is still a defect. #coherence-rigid
COHERENCE_THIN_DROP = _knob("CBBE2UBE_COHERENCE_THIN_DROP", 0.30)
# --- #coherence-kink -- DEFAULT ON since 2026-09-04 --------------------------
# Kill switch: CBBE2UBE_NO_COHERENCE_KINK=1
# The kink test was DOCUMENTED beside `_repair_coherence_collapse` but never
# implemented: `kink` was assigned False and never set True, so a patch that
# rotates COHERENTLY fell through every gate -- collapse (needs out<=0.30) and
# thin-drop (needs a fall >=0.30) both miss a strip whose coherence went
# 0.96 -> 0.90 while the strip turned 88 degrees. Measured on a reported robe:
# 16 shoulder-strap patches turning 68-113 deg against neighbours at 26-48.
# Repaired by SMOOTHING, never rigidly -- rigid preserves a kink by
# construction.
COHERENCE_KINK = not _flag("CBBE2UBE_NO_COHERENCE_KINK", False)
COHERENCE_KINK_DEG = _knob("CBBE2UBE_COHERENCE_KINK_DEG", 40.0)
COHERENCE_KINK_RATIO = _knob("CBBE2UBE_COHERENCE_KINK_RATIO", 2.0)
# A patch turning this many degrees AND this many times harder than the surface
# it attaches to is a KINK -- rigid rotation, so the coherence gates miss it.
#
# DEFAULT OFF. It works, but it trades clipping ~1:1 for bend reduction, because
# the kink IS the anti-poke / standoff push holding the cloth off the body:
# smoothing the kink away undoes the push. Measured on one piece (verts BEHIND
# the skin, and 1st-percentile signed clearance):
#     ratio  patches  hip bent-area  behind-skin   p1
#     off       5        107.72          30       0.130
#     3.0      28         75.83          71      -0.147
#     4.0      16         97.35          53      -0.066
#     6.0      11        100.93          30       0.115
# There is no setting that fixes the bend without costing clearance, so the real
# fix is upstream -- FEATHER the push so it never forms a kink -- not smoothing
# the result. THE TOGGLE IS DELETED: no ratio in the sweep above buys the bend
# without paying clearance, so there was nothing to tune. Rebuild it only as the
# upstream feather. #coherence-kink


# (moved to nif_convert_writer.py, 2026-09-01)


def _weld_source_coincident_verts(src_verts, out_verts, tol=1e-4):
    """Re-close seams the per-vertex passes pulled apart.

    A UV/normal seam is stored as SEVERAL vertices at one position with separate
    indices -- they exist precisely because their normals differ. Every fit pass
    here is per-vertex and most push along each vertex's OWN normal, so the two
    halves of a seam are pushed apart and the seam opens into a gap. Through the
    gap you see the unlit interior: a black shape running along the seam line.

    Measured on one converted cuirass: of 739 source-coincident groups, 237 were
    split in the shipped mesh, 157 by over 0.05u, the worst a MIRRORED PAIR of
    3.3u gaps at (+/-10.8, -7.0, 75.7) -- the rear waist, exactly where the user
    reported black squares along a seam.

    Coincidence is an authored invariant, not an accident: the mesh is a closed
    surface and those vertices are one point. Restoring it can only close a hole
    that should never have opened. Each group is moved to its own centroid, so
    the seam lands between where the passes put its halves rather than snapping
    to either side.
    """
    sv = np.asarray(src_verts, dtype=np.float64)
    ov = np.array(out_verts, dtype=np.float64, copy=True)
    if sv.shape != ov.shape or len(sv) == 0:
        return ov, 0
    key = np.round(sv / float(tol)).astype(np.int64)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True,
                               return_counts=True)
    multi = counts > 1
    if not multi.any():
        return ov, 0
    sums = np.zeros((len(counts), 3), dtype=np.float64)
    np.add.at(sums, inv, ov)
    means = sums / counts[:, None]
    mask = multi[inv]
    before = ov[mask]
    target = means[inv][mask]
    n_moved = int((np.linalg.norm(before - target, axis=1) > 1e-6).sum())
    ov[mask] = target
    return ov, n_moved


# (moved to nif_convert_writer.py, 2026-09-01)


def _winding_regions(t):
    """Label triangles by CONSISTENTLY-WOUND region, and count winding seams.

    Two triangles sharing an edge belong to one region when they traverse that
    edge in OPPOSITE directions -- the definition of agreeing winding. Sharing
    it in the SAME direction is a seam, and leaves them in separate regions so a
    vote can still correct one of them.

    Non-manifold edges (shared by more than two triangles) join nothing: their
    orientation is genuinely undecidable, and guessing there is how a repair
    starts inventing damage.

    Returns (labels, n_seams).
    """
    from scipy.sparse import coo_matrix as _coo
    from scipy.sparse.csgraph import connected_components as _cc

    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    ti = np.tile(np.arange(len(t), dtype=np.int64), 3)
    fwd = e[:, 0] < e[:, 1]
    key = np.sort(e, axis=1)
    _u, inv, cnt = np.unique(key, axis=0, return_inverse=True,
                             return_counts=True)
    inv = np.asarray(inv).ravel()
    sel = np.flatnonzero(cnt[inv] == 2)
    if not len(sel):
        return np.arange(len(t), dtype=np.int64), 0
    # Stable sort pairs the two halves of each shared edge next to each other.
    sel = sel[np.argsort(inv[sel], kind="stable")]
    a, b = sel[0::2], sel[1::2]
    agree = fwd[a] != fwd[b]
    n_seams = int((~agree).sum())
    ra, rb = ti[a][agree], ti[b][agree]
    if not len(ra):
        return np.arange(len(t), dtype=np.int64), n_seams
    g = _coo((np.ones(len(ra)), (ra, rb)), shape=(len(t), len(t)))
    _n, lab = _cc(g, directed=False)
    return lab.astype(np.int64), n_seams


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


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
_EFFECT_GLOW_ANIM = (not _flag("CBBE2UBE_NO_GLOW_ANIM", False))

# Effect-shader glow overlays (Daedric red glow etc.) keep their SOURCE skin: the UBE
# reskin re-skins the decal to body bones it never had, and a skinned
# BSEffectShaderProperty CTDs on equip. So a glow shape ignores its override_skin and
# copies the source skin verbatim (minus scale bones). Default on;
# CBBE2UBE_EFFECT_RESKIN=1 reverts.  [DESIGN: Effect-shader glow overlays]
EFFECT_SHADER_SOURCE_SKIN = (
    not _flag("CBBE2UBE_EFFECT_RESKIN", False))


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


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


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# If the source HDT-SMP XML drives at least this many bones the converted
# skeleton LACKS, the piece's physics rig is substantially gone -> regenerate a
# stable soft-body XML instead of keeping a source XML that points FSMP at
# non-existent bones (unconstrained -> divergence -> exploded spiky mass). A
# cleanly-converted piece loses ~0 bones, so a large count is unambiguous.
_HDT_REGEN_MISSING_BONES = _knob("CBBE2UBE_HDT_REGEN_MISSING_BONES", 8, int)


# (moved to nif_convert_physics.py, 2026-09-01)


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


# --- Tight-vs-loose gate for GENERATED soft-body physics -----------------
# When the source armor had NO authored HDT-SMP XML, the converter generates a
# per-vertex soft-body for every cloth carrier so hanging cloth (skirts, capes,
# tabards) drapes + collides. But a SKIN-TIGHT garment (leggings/pantyhose)
# doesn't need a sim -- and worse, an FSMP per-vertex soft-body OVERWRITES the
# shape's verts every frame from its own rest state, which CLOBBERS RaceMenu
# BodyMorph: the tight cloth stops following the body sliders entirely. So a
# body-conforming carrier must stay skinned+morphable, NOT become soft-body.
# Calibrated on real output: a pantyhose reads max-standoff 3.7u / 100% within
# 4u; every loose skirt/cape has verts hanging 8-23u out. #tight-softbody-gate
# #rigid-majority-softbody-gate -- OPT-IN, default OFF. Changes physics for a
# whole class (55% of generated soft-bodies at threshold 0.5, ~37% at 0.15), so
# it needs equip-CTD and cloth-collapse checks in game, not just a clip number.
# GUI entry `rigid_majority_softbody` exists so it can actually be tested: an
# env-only behaviour toggle is unreachable under MO2 and can never be validated.
RIGID_MAJORITY_SOFTBODY_GATE = _flag("CBBE2UBE_RIGID_MAJORITY_SOFTBODY", False)
# Chain fraction below which a shape is "not a cloth garment". Census of 216
# generated soft-bodies: p25 0.030, p50 0.355, p75 0.788 -- continuous, no
# natural gap. 0.15 clears the measured 0.0535 failure and stays well under the
# median. TUNE THIS AGAINST THE CENSUS, not against one piece.
RIGID_MAJORITY_CHAIN_MIN = _knob("CBBE2UBE_RIGID_MAJORITY_CHAIN_MIN", 0.15)

_TIGHT_SOFTBODY_GATE = (
    not _flag("CBBE2UBE_NO_TIGHT_SOFTBODY_GATE", False)
)
_SOFTBODY_CONFORM_STANDOFF = _knob("CBBE2UBE_SOFTBODY_CONFORM_STANDOFF", 4.0)
_SOFTBODY_CONFORM_FRAC = _knob("CBBE2UBE_SOFTBODY_CONFORM_FRAC", 0.95)
_SOFTBODY_CONFORM_P95_CAP = _knob("CBBE2UBE_SOFTBODY_CONFORM_P95CAP", 5.0)

_ube_conform_tree_cache: dict = {}


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


def _safe_data_rel(rel: str) -> "str | None":
    """Normalise a Data-relative path that came from UNTRUSTED file content, or
    return None if it tries to escape.

    The physics-XML path is read out of a third-party NIF's
    `HDT Skinned Mesh Physics Object` string extra-data, so a hostile or merely
    broken mesh controls it. `rel.replace("\\\\", "/").lstrip("/")` alone is NOT
    enough: it strips leading slashes but keeps `..` segments, and pathlib
    DISCARDS the left operand entirely when the right side is drive-absolute --
    so `C:\\Users\\...\\id_rsa` resolves to exactly that, and `..\\..\\secret`
    walks out of the mods tree. The file is then copied into the converted
    output mod, which users routinely re-upload, making this a disclosure route
    rather than just a bad read.

    Rejects: drive letters, UNC roots, and any `..` component. Returns a clean
    forward-slash relative path otherwise."""
    if not rel:
        return None
    norm = str(rel).replace("\\", "/")
    # ORDER MATTERS. The UNC test must run BEFORE stripping (stripping destroys
    # the "//" that identifies it), and the drive test must run AFTER (a single
    # leading separator makes the pre-strip first segment empty, so a drive test
    # placed first sees "" and waves `/C:/Users/...` straight through -- then the
    # strip re-exposes the drive letter. That bypass was live and is exactly the
    # attack this function exists to stop).
    if norm.startswith("//"):                 # UNC \\server\share, \\?\C:\...
        return None
    norm = norm.lstrip("/")
    if ":" in norm.split("/", 1)[0]:          # C:, after any leading separators
        return None
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) or None


# (moved to nif_convert_bodyrefs.py, 2026-09-01)
_VFS_DATA_REL_MEMO: "dict[tuple, Path]" = {}
# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


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
    norm = _safe_data_rel(rel)
    if norm is None:
        # Say so. A rejection here is not inert: the caller falls back, and if
        # that also misses, `_hdt_collider_shape_names` and
        # `_hdt_softbody_shape_names` both return EMPTY -- which every skin
        # pass reads as "this armor has no colliders and no soft-bodies" and
        # then reskins them, the documented equip-CTD and softbody-drift.
        # Silent was the wrong default for a safety invariant failing open.
        print(f"  !! physics XML path rejected (escapes the mods tree): {rel!r}"
              " -- collider/soft-body detection may fail open for this armor")
        return None
    # 1) Local: the source NIF's own mod root (dir that contains 'meshes').
    local_root = None
    for parent in [src_nif_path, *src_nif_path.parents]:
        if parent.name.lower() == "meshes":
            local_root = parent.parent
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
    # Memo (positives only, re-validated): the same authored rel is resolved
    # once per SHAPE by the chain pre-create and once per pass family. Keyed on
    # the local root too, so two pieces from different mods that authored the
    # same rel cannot serve each other's answer.
    memo_key = (norm, str(local_root or ""), str(mroot or ""))
    hit = _VFS_DATA_REL_MEMO.get(memo_key)
    if hit is not None and hit.is_file():
        return hit
    try:
        if mroot is not None and mroot.is_dir():
            for mod in _sorted_mod_dirs(mroot):
                cand = mod / norm
                if cand.is_file():
                    _VFS_DATA_REL_MEMO[memo_key] = cand
                    return cand
    except OSError:
        pass
    return None


# (moved to nif_convert_physics.py, 2026-09-01)


_SKELETON_BONES_CACHE: "set[str] | None" = None
# (raw set, normalised frozenset) -- see _actor_can_resolve_bone. Held as a PAIR
# so the raw set's identity is the cache key: replacing _SKELETON_BONES_CACHE
# invalidates this automatically rather than leaving a stale skeleton behind.
_SKELETON_BONES_NORM_CACHE: "tuple | None" = None


# (moved to nif_convert_physics.py, 2026-09-01)


_SKELETON_PARENTS_CACHE: "dict[str, str] | None" = None


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# REMOVED 2026-07-27: `HARDEN_AUTHORED_PHYSICS` / `_harden_physics_params`.
#
# It clamped chain-rig stability params (inertia floor 70, angular-damping floor 0.9,
# stiffness cap 50, linear link limits to rigid) "into the band that robust rigs
# occupy". Present since the first commit, never enabled, never measured, no tests.
#
# Measured before deleting, and the premise did not survive it:
#   * it would have modified 366 of 367 physics XMLs -- 100%, not a few fragile rigs;
#   * every clamp's stated "typical" band is contradicted by the actual data --
#     inertia claimed 70-150, observed 0-60 (median 10); angular damping claimed
#     0.95-0.99, observed 0-0.88 (the whole corpus sits BELOW the floor); stiffness
#     claimed ~20, observed median 200, max 10000;
#   * the XMLs it would rewrite are the converter's OWN GENERATED output
#     (`hdt_xml_gen`), not authored physics, so it amounted to writing a config and
#     immediately overwriting its numbers.
#
# If bounding chain stability params is ever wanted, it belongs in `hdt_xml_gen`,
# calibrated against what that generator actually emits. Precedent for caution:
# `CHAIN_SKIRT_PHYSICS` is quarantined because ONE generated XML collapsed a skirt
# in game; this touched 366.

# Zeroes dynamic chain masses so bones become static/kinematic — cloth holds
# its authored shape and follows Pelvis. Mitigates incomplete rigs (e.g.
# missing front-chain ring). Trade-off: no swing. XML-only, no mesh reskin.
# Apply with CBBE2UBE_STATIC_CHAINS=1 on the affected mod folder only.
STATIC_CHAINS = (
    _flag("CBBE2UBE_STATIC_CHAINS", False)
)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_writer.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


_HDT_XML_TEXT_CACHE: "dict" = {}
# This piece's authored physics XML text, captured from the SOURCE nif at the
# top of `convert_nif`. See `_hdt_xml_bind_piece_source`. #xml-source-of-truth
_PIECE_HDT_XML_TEXT: "str | None" = None


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_physics.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_bodyrefs.py, 2026-09-01)


# (moved to nif_convert_trigen.py, 2026-09-01)


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
            except Exception as _ie:
                # Was `pass`. This pass is DEFAULT ON, and a silent failure here
                # means the UBE Hands/Feet are simply absent from the piece --
                # the BUG-06 "vambraces hide the HANDS" class -- with nothing in
                # the report to say so.
                _note_pass_failure(
                    f"_inject_ube_extremity_replacement/shape:{sh.name}", _ie)
    except Exception as _oe:
        # Was `pass`, so BOTH levels were mute: if the reference NIF failed to
        # open, NO extremity was injected on the whole piece and the run looked
        # identical to "this piece needed none".
        _note_pass_failure("_inject_ube_extremity_replacement", _oe)


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
        override_normals = None
        if s.name == "BaseShape":
            try:
                src_verts = np.asarray(s.verts, dtype=np.float64)
                src_normals = (np.asarray(s.normals, dtype=np.float64)
                               if s.normals is not None else None)
                if src_normals is not None:
                    src_tris = np.asarray(s.tris, dtype=np.int64)
                    # Repair, and SHIP the repair. Fixing them only inside
                    # _close_pubic_holes would sort the winding vote and leave
                    # the fill triangles still shaded from a zero vector,
                    # because _copy_shape trusts the source normals verbatim on
                    # this path.
                    fixed, n_fixed = _repair_degenerate_normals(
                        src_verts, src_tris, src_normals)
                    if n_fixed:
                        override_normals = fixed
                    sealed_tris, n_loops = _close_pubic_holes(
                        src_verts, src_tris, fixed,
                    )
                    if n_loops > 0:
                        override_tris = sealed_tris
            except Exception:
                override_tris = None
                override_normals = None
        try:
            _copy_shape(s, dst_nif, override_tris=override_tris,
                        override_normals=override_normals)
            injected.append(s.name)
        except Exception as e:
            return (s.name, repr(e))
    return None


# (moved to nif_convert_layers.py, 2026-09-01)


def _shape_has_identity_g2s(s) -> bool:
    """True when this shape's global-to-skin is identity (or absent).

    Both cross-shape passes need it for the same reason: they compare or move verts
    BETWEEN shapes, and that is only valid when those shapes share a frame. Phase 1
    stores WORLD verts and phase 2 stores SKIN verts, which are identical exactly
    when g2s is identity -- so a non-identity shape is left with its own warp rather
    than being welded or ridden against a frame it does not share.

    Was defined identically inside `_weld_cross_shape_seams` and
    `_ride_effect_overlays_on_plate`; only one copy carried that explanation, which
    is how a shared invariant quietly becomes two."""
    try:
        if not s.has_global_to_skin:
            return True
        g = _shape_global_to_skin(s)
        return g is None or _g2s_is_identity(g)
    except Exception:
        return False


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# (moved to nif_convert_weights.py, 2026-09-01)


# Multi-layer garment stacks (chest_plate / corset / top / belts / belts_metal) each get
# displaced by the fit passes according to their OWN distance to the body, so a layer 1u
# off the skin and one 2u off move by DIFFERENT amounts -> their spacing drifts -> they
# cross = visible layer clipping at standstill. MEASURED: a 5-layer stack gained 5 NEW
# cross-shape penetrations (~1.2u) vs its source, and a per-pass bisect (conform/self-int/
# softcloth/motion-match/jiggle, and the opt-in ANTIPOKE_SMOOTH + LAYERED_ANTIPOKE floors)
# moved WHICH pairs crossed but never the count -- it isn't one pass, it's the per-layer
# independence itself.
#
# Fix: ride the stack COHERENTLY. Rank the layers innermost-first, then walk outward: each
# layer re-derives its verts from the FINAL position of the nearest SOURCE-paired vert of
# the geometry ALREADY placed beneath it, preserving that vert's SOURCE offset. The
# innermost layer keeps its own body fit, and every layer above rides it, so relative
# spacing is preserved BY CONSTRUCTION and layers can't cross. Same trick as the glow ride
# below, cascaded over the layer stack instead of one plate. Per-vert gated on ride_max so
# a layer with nothing beneath it locally (a waist belt over a chest-only reference) keeps
# its own warp. CBBE2UBE_NO_LAYER_RIDE=1 disables. #layer-ride
# Cord/trim shapes (decorative laces, cords, piping) are NOT a layer with a
# consistent side -- they thread half-in / half-out of their host surface by design
# (MEASURED: a 'Jacket Cords' trim shape vs its 'Jacket' host -- source signed offset median -0.01u,
# range -0.71..+0.68, 4160/12100 verts already "inside" the host). So the layer-ride /
# order-repair (which assume a definite side) can't help them and my order metric even
# over-counts their natural weave. They sink in-game for a DIFFERENT reason: the host
# surface warps ~0.9u CBBE->UBE while the cord, sitting ~0.04u ON that surface, doesn't
# follow that exact motion -> it ends up buried.
#
# Fix: GLUE the cord to the host surface. For each cord vert take its foot-point on the
# nearest host triangle + its signed HEIGHT along the host normal IN THE SOURCE, then
# place the cord vert at host_FINAL_footpoint + source_height * host_FINAL_normal, with
# the resulting displacement FEATHERED over the cord mesh so the strand moves coherently
# (the cord's real defect is CRINKLE -- adjacent verts flung apart, measured max edge
# jump 4.80u at the shoulder -- not sinking).
#
# THE CORD-CONFORM PASS IS DELETED (it was unreachable from a real run, and its own
# measurement says why it should stay that way). MEASURED on a cuirass whose cords
# lace its jacket: the target cord's max edge jump improves 4.42 -> 3.38 (-23%), but it
# is only a PARTIAL fix and it perturbs neighbouring pieces through pass interactions --
# a shoulder pauldron's jump rose 1.33 -> 1.84 (~half of that via the cross-shape seam
# weld coupling a moved cord vert to the pauldron; disabling the weld only halves it and
# costs cord quality). Net-positive but NOT clean, so it stays opt-in rather than
# trading one visible piece for another. The durable fix is to protect cord/trim shapes
# DURING the warp (as the hand/foot extremity masking does) instead of repairing them
# afterwards -- do not rebuild the repair-afterwards form. #cord-conform








LAYER_RIDE_ENABLED = (
    not _flag("CBBE2UBE_NO_LAYER_RIDE", False))
# 2.0 chosen by SWEEP (1.0/1.5/2.0/3.0) against layer_penetration + crinkle on a
# 5-layer stack AND an angled-pauldron cuirass: 2.0 gave the best overall -- stack
# chest_plate spikiness 5.38->0.99 and penetration 1.22->0.68u, while a shoulder
# pauldron IMPROVED 2.92->2.23 (3.0 rides too far and drags it to 3.43). See #layer-ride.
# #authored-ride-order -- DEFAULT ON since 2026-08-16. Rank the ride stack by
# the AUTHOR's own pairwise layer relation instead of by median distance to the
# body. Kill with `CBBE2UBE_NO_AUTHORED_RIDE_ORDER=1`.
#
# The median is one global scalar answering a question that is local: on the
# reported bodysuit it made a 774-vert bust plate the REFERENCE and had the
# 15250-vert full bodysuit ride it, because the plate sits slightly tighter
# than the suit's median over its legs and arms (1.341u vs 1.407u). Since the
# reference is the only layer that keeps its own fit, that decides whose fit
# survives -- and it also amplifies: measured on the slim variant's shoulder,
# a perturbation upstream of the ride grew from +618 verts-inside-body with the
# ride disabled to +4533 with it on, a factor of seven.
#
# WHY IT EARNED THE DEFAULT. Census over 167 shipped multi-layer pieces / 1747
# authored pairs: the median ranking makes an OUTER layer the reference on 65 of
# them (38.9%) and contradicts 33.5% of the author's own verdicts; this reads
# 3.8%, all of it on the 23 pieces whose relation genuinely CYCLES. On a
# reported 5-layer top it had the waist BELTS as the base of the stack with the
# bodysuit riding them, and correcting that took verts-inside-body from 790 to 1
# (141 -> 0 at pure defaults, 783 -> 1 on the other weight) while moving the
# belts/corset contact TOWARD the author. It is the root cause the layer-ride
# clamp, the coupled clamp, the joint solve and the ride-as-inequality were all
# fighting downstream -- see project_layer_ride_discards_fit.
#
# CONFIRMED IN GAME 2026-08-16 on two pieces, one of them the previously
# approved control: "they both look much better."
#
# See `_authored_layer_depth` for the relation and `_stack_depth_from_relation`
# for why the depth is a longest path and not a count.
AUTHORED_RIDE_ORDER = not _flag("CBBE2UBE_NO_AUTHORED_RIDE_ORDER", False)
# (moved to nif_convert_layers.py, 2026-09-01)
# k-nearest reference verts blended per rider vert. >1 is REQUIRED: a k=1 (snap to
# nearest) ride inherits a discontinuous displacement and ADDS spikes. See #layer-ride.
_LAYER_RIDE_K = int(os.environ.get("CBBE2UBE_LAYER_RIDE_K", "8") or "8")
# Follow the SURFACE POINT a rider rests on instead of a blend of nearby reference
# VERTICES. See `_ride_disp_barycentric`. Opt-in until judged in game.
LAYER_RIDE_BARY = (
    _flag("CBBE2UBE_LAYER_RIDE_BARY", False))
# Reference triangles tested per rider vertex. The nearest CENTROID is not always
# on the nearest triangle -- a long thin triangle's centroid sits far from its own
# edge -- so several candidates are evaluated and the closest surface point wins.
_LAYER_RIDE_BARY_CAND = int(
    os.environ.get("CBBE2UBE_LAYER_RIDE_BARY_CAND", "6") or "6")


# Layer-ORDER repair. The visible "layers clipping into each other" artifact is NOT
# "one layer is inside another" -- layered armor AUTHORS that (a source `top` sits 1.32u
# inside its `chest_plate` across 6221 verts, hidden by design). The artifact is an ORDER
# INVERSION: a vert that was on one side of another shape in the SOURCE ends up on the
# OTHER side after the fit passes, so a piece that should sit on top gets swallowed (the
# outermost metal band sinking 0.60u into the chest plate it should lie on; a cuirass's
# cords sinking into the jacket across 40% of their verts).
#
# Fix: pair each vert to its nearest neighbour on every nearby shape IN SOURCE SPACE, take
# the signed distance along that shape's normal in BOTH spaces, and where the SIGN FLIPPED
# (was outside/on, now inside) push the vert back out along the destination normal until it
# regains its SOURCE signed offset. Order + authored spacing restored; verts that never
# flipped are untouched, so a correct fit is never disturbed. #layer-order
LAYER_ORDER_REPAIR_ENABLED = (
    not _flag("CBBE2UBE_NO_LAYER_ORDER", False))
# The #clearance-field solve produces a SMOOTH, layer-consistent mesh. This repair
# was built to clean up the SPIKY per-vertex push, and on the smooth solve output
# it MISFIRES: it reads fine interface verts as violations and hard-yanks them,
# buckling thin straps (a plated top's belt: folds ~55 with the repair, 24 without;
# pack total 92->46, belt spikiness 19.4->4.1). So when a clearance-field solve is
# active the repair is OFF by default. VERIFIED IN GAME -- the belt reads better
# without it. Force it back on with CBBE2UBE_FORCE_LAYER_ORDER=1.
if (CLEARANCE_FIELD_SOLVE or CLEARANCE_FIELD_INFLATE) and (
        not _flag("CBBE2UBE_FORCE_LAYER_ORDER", False)):
    LAYER_ORDER_REPAIR_ENABLED = False
# (moved to nif_convert_layers.py, 2026-09-01)
_LAYER_ORDER_EPS = 0.05     # sign-noise band

# #layer-order-last -- let the cross-shape layer reconciliation be the last
# thing that moves a vert on phase 2, instead of the per-shape geometry repairs
# re-running at write time. See the `_copy_shape` call in `convert_nif_phase2`.
LAYER_ORDER_LAST = _flag("CBBE2UBE_LAYER_ORDER_LAST", False)
# (moved to nif_convert_layers.py, 2026-09-01)
# Feathering rounds for the correction field. MUST be > 0: a raw per-vert shove IS a
# crinkle (measured 5.38 -> 6.09 spikiness unsmoothed). See #layer-order.
_LAYER_ORDER_SMOOTH = int(os.environ.get("CBBE2UBE_LAYER_ORDER_SMOOTH", "2") or "2")


# #relative-displacement-cap -- BUILT, MEASURED, REVERTED 2026-08-15.
#
# `project_collar_fine_tessellation` named it as the untried, "possibly simpler"
# lever: cap each vert's displacement at k x its own mean incident edge, feather
# the pull-back, and run it BEFORE the final anti-poke so the anti-poke can push
# anything back out of the body. The ordering worked -- body penetration stayed
# 0 in every arm -- but the cap itself REPRODUCES the failure of the
# reweighting that was reverted for the collar, by a different route:
#
#     arm        edge p99  edge max  >10% strain  spike p99  FOLDED  INVERTED
#     baseline    1.3796     5.700      11974      0.3394      586       81
#     cap 2.0     1.4060     8.610      11607      0.3426        -        -
#     cap 1.5     1.4131     7.838      11429      0.3456        -        -
#     cap 1.0     1.3944     5.680      10797      0.3425      655      106
#
# Same signature as the reverted reweighting: the strained-edge COUNT improves
# slightly while the WORST edge, p99, spikiness and FOLDS all get worse --
# pulling the offenders back buys their neighbours' slack, and on a rim the
# neighbours are where it shows. It also did NOT reduce what it was built for:
# strap verts buried in the pants 3130 -> 3098, worst depth -0.226 -> -0.248.
#
# TWO INDEPENDENT ROUTES NOW FAIL THE SAME WAY, so treat the whole "hold fine
# verts back" family as dead. The remaining DISTINCT lever from that memory is
# the smoothing REACH (`CONFORM_STRETCH_LAMBDA` scaling with local edge length),
# which changes how far the correction spreads rather than how much of it lands.


# #panel-rigidity -- OPT-IN, `CBBE2UBE_PANEL_RIGIDITY=<0..1>` (0/unset = off).
#
# Armour that reads as LAYERED PLATES is routinely one shape holding many rigid
# panels. The fit chain moves vertices individually, so a plate that should
# travel as a unit gets STRETCHED, and the crisp authored edge between two
# panels warps. Reported in game on a khajiit cuirass: "supposed to have a line
# indented on that part of the armor, but when there are breasts the indent
# heavily distorts".
#
# Measured on that piece -- 4084 verts, 12 connected components after welding.
# Per component, the best RIGID fit (rotation + translation, no scale) from
# source to converted, and the residual it cannot explain:
#
#     panel      verts   z     rigid MOVE   DEFORM mean   p95
#     bust        2062  96.7      0.674        0.740     1.453   <- the reported one
#     lower        645  77.5      0.426        0.917     1.339
#     10 plates  80-165 54-74   0.26-1.23    0.10-0.44  0.12-0.92
#
# The bust panel is DEFORMED MORE THAN IT IS MOVED. A plate that merely
# travelled would read ~0.
#
# THE TRADE, stated because it is the whole risk: a plate moved rigidly cannot
# follow a larger bust -- fully rigid trades a distorted indent for a clearance
# defect. So this is PARTIAL: each panel keeps its own rigid motion and gives up
# only `strength` of its deformation. It also runs BEFORE the final anti-poke, so
# anything pulled back into the body is pushed clear again -- the same ordering
# that held for the (reverted) displacement cap, where body penetration stayed 0
# in every arm.
#
# SIMULATED cloth is excluded per VERTEX, not per shape: a component carrying any
# chain weight is left alone entirely, because rigidifying cloth would fight the
# solver (#mixed-cloth-clearance records the same distinction).
# DEFAULT 0.75 (was 0.0) since 2026-08-22 -- promoted with the other four recipe
# opt-ins. The 08-22 reconvert ran all five ON over 163 mods and the
# in-game verdict was "everything looks as it should", so the defaults
# now match the only configuration that has EVER been judged. Details in
# the `_DEFAULTS_PROMOTED_2026_08_22` block.
PANEL_RIGIDITY = _knob("CBBE2UBE_PANEL_RIGIDITY", 0.75)
# --- #panel-rigid-early-clearance -- OPT-IN, CBBE2UBE_PANEL_RIGID_EARLY_CLEAR=1
#
# MEASURED 2026-08-23. `_partial_rigid_panels` is BODY-BLIND -- it takes no body
# argument and cannot avoid penetrating. The comment above accepts that on the
# grounds that it "runs BEFORE the final anti-poke, so anything pulled back into
# the body is pushed clear again", and that is true: stage-traced on three
# body-swap shapes, verts inside the body go 0 -> 491 (Outfit), 0 -> 83 (Belt),
# 0 -> 38 (Bra) across this pass, and the anti-poke returns them to 3 / 0 / 0.
# Penetration does stay ~0 at the shipping defaults, exactly as claimed.
#
# THE HIDDEN COST IS THAT ANTI-POKE'S BUDGET IS SPENT ON THE CLEANUP, so it can
# never be tightened to respect the author's fit. That is what blocks snugness:
#
#     arm                                  author error   garment clip
#     defaults                                 0.257u          147
#     morph headroom removed                   0.072u (-72%)   317  (+116%)
#     headroom removed + PANEL_RIGIDITY=0      0.085u (-67%)    89  (-39%)
#
# Nearly the whole snugness gain survives without this pass, and the clipping
# regression goes with it -- so the +116% was never the price of the headroom,
# it was this pass's penetration surviving once the anti-poke could no longer
# mask it.
#
# Turning the pass off is NOT the fix: it exists for a reported defect (a
# distorted indent on a khajiit cuirass) and shipped in the only configuration
# ever judged good in game. Instead, run the CLEARANCE-AWARE form here too.
# `_rigidify_within_clearance` already solves precisely this -- per panel,
# bisect the largest strength whose result leaves no vertex deeper inside the
# body than it already was -- and already ships as phase 2's SECOND half at the
# `panel_rigidity_post` site. It runs AFTER the anti-poke, which is why it
# spares the anti-poke nothing. This flag reuses that same solved pass at the
# EARLY site, so the penetration is never created rather than repaired.
#
# NOT a rebuild of `#relative-displacement-cap` (BUILT/MEASURED/REVERTED
# 2026-08-15, above): that capped displacement by local EDGE LENGTH and died
# with the "hold fine verts back" family, worsening p99 edge, spikiness and
# folds. This changes nothing per vertex -- one scalar per panel, so the plate
# still moves as a unit -- and its constraint is the BODY, not edge length.
#
# VERIFIED ON THE MORPH AXIS TOO, which is the one that matters for clearance and
# which bind-pose numbers cannot see. Body and garment morphed together at full
# strength, bust band:
#
#     copy       BIND -65%   MORPH better on 5/5 pieces, worst case -40%
#     body-swap  BIND -41%   MORPH neutral (2 better, 2 worse, median flat)
#
# WIDENED TO 28 BODY-SWAP PIECES, and read PER PIECE rather than pooled, because
# a percentage over a whole sample hides which pieces moved:
#
#     copy       6 pieces: 6 better, 0 worse -- and -67% with the biggest piece
#                removed, so the win is uniform rather than outlier-driven
#     body-swap  28 pieces: 12 better, 6 WORSE, 10 unchanged; the pooled -41%
#                is 85% ONE bodysuit (2844 -> 668), and -11% without it.
#                Fit is FLAT (0.288u -> 0.293u, 99/204 shapes closer) -- the
#                small fit gain seen at 7 pieces did not survive.
#
# That asymmetry IS the mechanism: the copy path has no anti-poke, so penetration
# this pass creates ships. Preventing it there is a straight win. On body-swap the
# anti-poke already cleaned up, so prevention mostly relieves it.
#
# The copy path gains most because it has NO anti-poke: penetration this pass
# creates there has nothing to repair it and ships. Note that the morph harness
# could not measure the copy path at all before 2026-08-23 -- it aborted on every
# phase-1 piece -- so any earlier "no morph evidence of harm" covered 22% of the
# pack, not the pack.
#
# DEFAULT OFF ANYWAY. It can only REDUCE how rigid a panel gets, so the risk it
# carries is the indent distortion coming back on pieces whose panels sit near
# the skin -- and NO metric here can see that; it is what eyes are for.
# #panel-rigidity's own verdict is still pending, so this ships off until both
# are judged together in game.
PANEL_RIGID_EARLY_CLEAR = _flag("CBBE2UBE_PANEL_RIGID_EARLY_CLEAR", False)
# A component smaller than this is a stud or a buckle, not a panel; a rigid fit
# over a handful of verts is noise.
PANEL_RIGIDITY_MIN_VERTS = int(
    os.environ.get("CBBE2UBE_PANEL_RIGIDITY_MIN_VERTS", "24") or "24")

# #panel-rigidity-fine-anim -- OPT-IN, DEFAULT OFF,
# CBBE2UBE_PANEL_RIGIDITY_FINE_ANIM=1. Reachable from the GUI so a real run can
# turn it on for a dedicated A/B.
#
# THE GAP IT CLOSES. `panel_rigidity` is 0.75 by default since 2026-08-22, but
# the FINE-ANIMATION sub-branch (gauntlets, boots, heels, any shape carrying
# finger/toe bones) calls none of `_partial_rigid_panels`,
# `_rigidify_within_clearance` or `_physics_chain_nowarp_blend`. So the knob
# splits the pack one level below the split its 2026-08-22 parity fix closed.
#
# IT IS NOT A NO-OP THERE, and that was measured rather than assumed -- the
# obvious reason ("gauntlets are single rigid shells") is FALSE. Replaying the
# pass offline on this branch's own stage dumps, 51 fine-animation shapes, with
# the probe validated by reproducing the real result EXACTLY on 485 main-chain
# shapes: it would fire on 51 of 51, find 568 qualifying panels and move verts a
# median 0.214u -- against the 0.211u it actually moves on the copy main chain.
#
# WHY DEFAULT OFF, unlike the 08-22 parity fix which shipped ON: that one had
# the recipe already running the knob for weeks, so the configuration was
# already judged. This is NEW geometry on ~20% of copy-path shapes with no
# verdict at all, and it would ship alongside #collider-declared-bones -- two
# unjudged geometry changes in one build cannot be told apart from one bad
# report. Turn it on for a build of its own.
#
# DIGITS ARE PROTECTED: the skip mask is the extremity mask, so the limb shell
# rigidifies and fingers/toes are left exactly where the branch put them. That
# is the same contract every other pass on this branch honours.
PANEL_RIGIDITY_FINE_ANIM = _flag("CBBE2UBE_PANEL_RIGIDITY_FINE_ANIM", False)
# #panel-local-rigid -- OPT-IN. Fit the rigid transform over a NEIGHBOURHOOD
# instead of over the whole welded component.
#
# WHY, measured on the reported bodysuit. One transform per component is exact
# only if the whole component needs the same motion. It does not: on a 4309-vert
# panel spanning 46u, rigidifying pushed the BUTT out 0.726u while the rest of
# the same panel moved 0.183u -- the single transform splits the difference and
# the butt pays. Two 729-vert panels spanning 33u read 0.88u at the butt against
# 0.16u elsewhere. Panels under ~3u of extent showed ~0.00u: small panels
# rigidify harmlessly, large ones cannot.
#
# So the transform is solved PER VERTEX over its K nearest neighbours in SOURCE
# space (moving-least-squares rigid deformation). Local shape is preserved --
# each neighbourhood moves by a rotation, no stretch or shear -- while the panel
# as a whole may bend and tighten to follow the body. K is the shape/fit dial:
# large K tends to the old whole-component behaviour, small K to the conform.
#
# Neighbourhoods come from the SOURCE, not the current state, so the support of
# each fit cannot drift as the panel moves.
_PANEL_LOCAL_RIGID = _flag("CBBE2UBE_PANEL_LOCAL_RIGID", False)
_PANEL_LOCAL_K = int(_knob("CBBE2UBE_PANEL_LOCAL_K", 64.0))

# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_fitgeom.py, 2026-09-01)



# THREE TOGGLES DELETED HERE, 2026-08-16. DO NOT REBUILD THEM.
#
#   `#panel-rigid-push`      CBBE2UBE_PANEL_RIGID_PUSH
#   `#panel-facet-rigidity`  CBBE2UBE_PANEL_FACET (+ _LO/_HI)
#   `#panel-rigid-antipoke`  CBBE2UBE_PANEL_RIGID_ANTIPOKE (+ _PUSH_MAX)
#
# All three tried to keep a plate straight by redistributing displacement inside
# `_rigidify_within_clearance`, and all three measured FLAT on the reported
# cuirass -- front deform p95 0.804 / 0.807 / 0.807 against a live 0.805.
#
# THEY WERE NOT WRONG IDEAS; THEY WERE MEASURED AGAINST A LIE. This pass was
# already writing a PERFECT rigid panel (stage deform 0.000) and
# `_ride_layers_on_reference` was overwriting it afterwards with
# `source + displacement_of_the_layer_beneath` -- see `#panel-rigid-ride` below
# and project_layer_ride_discards_fit. Nothing tuned here could ever have shown
# up in the file. The fix belongs at the ride, which is where it now lives.
#
# So: do not re-add a lever in this function to chase panel straightness without
# first confirming the ride is not overwriting the verts in question. The
# `[panel-ride]` log line reports exactly that per shape.
#
# (Historic note kept because it is still true of the pass that remains:
# `_rigidify_within_clearance` resolves a clash by giving BACK deformation --
# it reduces the panel's rigidity until nothing sinks into the body, so a flat
# plate over a larger bust still ends up ROUNDED, just less so.)


# SHAPE FACT WORTH KEEPING from the deleted facet work: the cuirass's largest
# component is not a plate but a closed BAND -- x -11..+11, y -11.5..+9.6, 1054
# verts at the FRONT and 772 at the BACK. That is why translating a panel clear
# of the bust cannot work: it drives the back half into the spine. Any future
# "just push it out" idea has to answer that first.

# HOW THE RE-ROUNDING WAS FOUND, recorded because the same question will be
# asked again about some other pass. A temporary "write every panel fully rigid,
# no guard at all" switch separates two failures that look identical from
# outside: the guard being the limiter (forcing it makes the panel straight) vs
# something downstream re-deforming (forcing it changes nothing). Here the pass
# wrote a PERFECT rigid panel and the NIF still shipped it rounded, which named
# `#panel-rigid-ride` as the culprit in one run. Note it cannot be diagnosed by
# re-running the pass on the output (project_chain_welded_torso) -- the stage
# dump vs the WRITTEN NIF is the comparison that works.


# #panel-rigid-surface-guard -- DEFAULT ON since 2026-08-26 (was opt-in
# `CBBE2UBE_PANEL_RIGID_SURFACE_GUARD=1`; `=0` turns it off).
#
# `_rigidify_within_clearance`'s feasibility test is a VERTEX test, and its floor
# lets a vertex sink all the way to zero clearance:
#
#     floor = min(clear_of(Q), 0.0) - 1e-4
#
# so a vertex standing 1.5u clear may end up at 0.000u and still pass. Two such
# vertices with a CONVEX body between them put the chord INSIDE the body, and
# nothing in the guard looks between vertices. Measured on a tunic's stage
# ledger: this pass takes the BIND bust clip from 0.000% to 18.174% while median
# standoff barely moves (1.345 -> 1.317u), and the anti-poke then has to repair
# it. That is the same vertex-vs-surface gap `#bust-surface-req` closed in the
# conform, still open one pass later.
#
# The fix samples each panel triangle's INTERIOR -- centroid plus the three edge
# midpoints -- and holds those to the same floor as the vertices. Sampling, not a
# new metric: `clear_of` is unchanged, it is simply asked about more points.
#
# MONOTONE BY CONSTRUCTION: the surface test is ANDed with the existing vertex
# test, so the feasible strength can only ever be LOWER, never higher. The pass
# can therefore only give back rigidity, never take more -- which makes
# `mean_strength_used` the counter-metric to watch, since rigidity recovered is
# exactly what this pass exists to buy.
# DEFAULT ON 2026-08-26 (#defaults-promoted-2026-08-26). No regressions on any
# piece measured; a college robe 12.957 -> 3.466 with its BIND clip 1.473 ->
# 0.000, and it earns its place independently of the chord charge (Steelheart
# 2.865 -> 2.239 with the chord OFF). Standoff p90 goes DOWN, i.e. tighter.
PANEL_RIGID_SURFACE_GUARD = _flag("CBBE2UBE_PANEL_RIGID_SURFACE_GUARD", True)


# (moved to nif_convert_fitgeom.py, 2026-09-01)


def _smooth_vertex_field(vec: np.ndarray, tris, iters: int = 2,
                         blend: float = 0.5, verts=None) -> np.ndarray:
    """Feather a per-vert VECTOR field over the mesh adjacency.

    Vector sibling of _smooth_push_field (which handles a scalar push). Each round
    blends every vert's vector toward its edge-neighbour average, so a correction
    applied to one vert drags its neighbourhood along instead of leaving a step.
    Verts whose neighbourhood has no correction stay ~0. Returns `vec` unchanged on
    any failure (never worse than not smoothing).
    """
    iters = _reach_iters(verts, tris, iters)          # #smooth-reach
    try:
        t = np.asarray(tris, dtype=np.int64)
        v = np.asarray(vec, dtype=np.float64)
        n = len(v)
        if t.size == 0 or n < 3 or not np.any(np.abs(v) > 0):
            return vec
        from scipy import sparse
        e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e = np.concatenate([e, e[:, ::-1]])
        e = e[(e[:, 0] < n) & (e[:, 1] < n) & (e[:, 0] >= 0) & (e[:, 1] >= 0)]
        if len(e) == 0:
            return vec
        A = sparse.coo_matrix(
            (np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)).tocsr()
        deg = np.asarray(A.sum(axis=1)).ravel()
        deg[deg == 0] = 1.0
        out = v.copy()
        for _ in range(max(1, int(iters))):
            avg = np.vstack([np.asarray(A @ out[:, c]).ravel() / deg
                             for c in range(out.shape[1])]).T
            out = (1.0 - blend) * out + blend * avg
        return out
    except Exception as _e:
        _note_pass_failure("_smooth_vertex_field", _e)
        return vec


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# #panel-rigid-ride -- DEFAULT ON since 2026-08-22 (was opt-in
# `CBBE2UBE_PANEL_RIGID_RIDE=1`; `=0` turns it off).
#
# THIS IS WHERE THE PANEL WAS BEING RE-ROUNDED, and it explains why three
# separate rigidity levers all measured flat at ~0.80u of front deformation
# while the pass itself was doing exactly what it was told.
#
# MEASURED, forced-rigid diagnostic on the reported cuirass, front facet of the
# 2062-vert bust panel:
#
#     stage s08_panel_rigidity_post      deform p95 0.000   (perfectly rigid)
#     stage s10_final                    deform p95 0.000   (still rigid)
#     WRITTEN NIF                        deform p95 0.560   <-- 3024 of 4084
#                                        verts moved at write time, mean 0.43u
#     WRITTEN NIF, CBBE2UBE_NO_LAYER_RIDE=1
#                                        deform p95 0.000   strain >10% 0.0%
#
# The ride is not subtly degrading the panel; it is DISCARDING the fit outright
# for every vert it touches -- `cur[mask] = sv[mask] + disp[mask]` sets the
# rider from its SOURCE position plus the displacement of the layer beneath.
# Nothing any earlier pass did to those verts survives.
#
# Which also hands us the fix for free. Since the ridden position is
# `source + displacement`, making the displacement CONSTANT across a panel
# yields the AUTHORED panel under a pure translation -- rigid by construction,
# not by approximation, and "a straight piece instead of rounded" exactly as
# asked. The layer still rides out coherently, which is what the ride is for;
# it just rides as a plate instead of as 2062 independent points.
#
# NOT A LICENCE TO DISABLE THE RIDE. Turning it off scores well here and is
# known to make roughness WORSE overall (project_layer_ride), so the panel-aware
# form is the only acceptable version of this.
#
# WHOLE COMPONENTS ONLY. A component the ride covers only partly would get a
# constant displacement on the covered verts and a smooth field on the rest,
# putting a step INSIDE one connected surface. Between components a step is
# invisible -- they share no vertex, and the boundary is the authored edge
# between plates -- so the coverage gate is what keeps this from tearing.
# DEFAULT ON since 2026-08-22 -- promoted with the other four recipe
# opt-ins. The 08-22 reconvert ran all five ON over 163 mods and the
# in-game verdict was "everything looks as it should", so the defaults
# now match the only configuration that has EVER been judged. Details in
# the `_DEFAULTS_PROMOTED_2026_08_22` block.
PANEL_RIGID_RIDE = _flag("CBBE2UBE_PANEL_RIGID_RIDE", True)

# --- #ride-body-floor -- DEFAULT ON since 2026-08-26 (was opt-in =1) --------
#
# MEASURED 2026-08-23, and it is where the clipping a player actually sees comes
# from. Stage dumps end before this pass, so every clip census taken from them
# was blind to it. On a layered cuirass, verts inside the body:
#
#     end of the fit chain (last stage dumped)        0
#     the file that ships                           110
#     ...with the layer ride disabled                  8
#
# **The ride causes 102 of the 110 -- 93%.** The fit chain finishes clean and
# this pass puts cloth back into the body, which is the same finding as
# project_layer_ride_discards_fit ("the layer ride DISCARDS the fit chain")
# quantified against the body rather than against the fit.
#
# WHY THE EXISTING GUARD DOES NOT CATCH IT. `_panel_rigid_disp` does test body
# clearance, but only for panels it fully rides; a partly-ridden panel takes an
# early `continue`, and verts in no qualifying panel never reach it. Both fall
# through to the plain application below, which had no body test at all. Proof
# it is not the panel budget: `_PANEL_RIDE_MAX_NEW_INSIDE=0.0` leaves the count
# at 111 against 110 -- the knob is aimed at a different path.
#
# THE FIX IS ONE-SIDED, the idiom `_smooth_warp_grooves` already uses: a ridden
# vert may move along the surface or AWAY from the body, never INTO it. The
# tangential motion is what makes layers cohere, so it is kept in full; only the
# inward component is clamped, and only where the vert would end up deeper than
# THE FIT CHAIN ALREADY HAD IT (`min(fitted clearance, 0)`), so a vert the fit
# chain left inside is not dragged out and nothing NEW goes in.
#
# DEFAULT ON since 2026-08-26 (#defaults-promoted-2026-08-26). It had been ON in
# the live recipe for weeks while the code shipped it OFF, so a defaults-only
# convert produced a configuration nobody had ever run -- the same reason the
# 2026-08-22 four were promoted. Measured: 28 pieces, clipping 3596 -> 1117
# (-69%), **19 better and 0 WORSE**, and the ride still re-places every vert it
# did before.
#
# WHAT THE EARLIER "DEFAULT OFF" NOTE WAS RIGHT ABOUT, and it still stands as the
# thing to look at: this changes the shipped position of ridden verts on a stack,
# which is exactly what #layer-ride exists to control, so LAYER COHERENCE is what
# an in-game verdict has to judge. Promoting it does not discharge that.
#
# It was briefly removed from the live settings on 2026-08-26 while aligning them
# to the code defaults, and `verify_reconvert.py` FAILED the resulting pack for
# exactly that -- which is the check working. Promoting it is the correct
# resolution: it keeps the measured behaviour AND leaves the settings file free
# of overrides.
# --- #ride-outward-cap -- OPT-IN, default OFF --------------------------------
#
# The mirror of `#ride-body-floor` below. That one stops the ride pushing cloth
# INTO the body; this one stops it hauling cloth OUT past what the rider's own
# fit chain decided.
#
# TRACED FROM AN IN-GAME REPORT (nipple outlines through a plate,
# docs/worklog/NIPPLE_OUTLINE_THROUGH_PLATE.md). On the reported piece the
# plate's own chain finished at +0.0295u of nipple lift -- nearly the author's
# own shape. The ride then placed it over the layer beneath, which was still
# bulging at +0.1044u, and the plate inherited it: +0.1143u shipped.
# `CBBE2UBE_NO_LAYER_RIDE=1` returns it to +0.0293u, an identity with its own
# chain result, so the ride is the ENTIRE gap. EIGHT other levers were measured
# against this defect -- every conform-side knob, both authored pushes,
# PANEL_RIGIDITY=1.0 -- and none moved it.
#
# WHY THE RIDE IS NOT SIMPLY WRONG. It preserves the rider's SOURCE OFFSET to
# whatever is beneath it, which is right when that layer is where its author put
# it, and wrong when it is not: the offset then faithfully transmits someone
# else's deviation. Preserving NON-CROSSING is the guarantee that matters;
# preserving the full offset is stronger than needed. Measured on this piece the
# plate clears the layer beneath by 0.30u at the tip and would still clear it by
# 0.22u at its own chain position -- so the outward push bought no separation.
#
# So: pull the rider back toward its own fit-chain standoff, but NEVER closer to
# the layer beneath than `_MIN_GAP`. The anti-crossing guarantee is kept; only
# the redundant outward travel is dropped.
RIDE_OUTWARD_CAP = _flag("CBBE2UBE_RIDE_OUTWARD_CAP", False)
# How far past its own fit-chain standoff the ride may still take a vert.
RIDE_OUTWARD_ALLOW = _knob("CBBE2UBE_RIDE_OUTWARD_ALLOW", 0.0)
# Never pull a rider closer than this to the geometry it is riding on. This is
# the non-crossing guarantee the cap must not break.
RIDE_MIN_GAP = _knob("CBBE2UBE_RIDE_MIN_GAP", 0.05)

RIDE_BODY_FLOOR = _flag("CBBE2UBE_RIDE_BODY_FLOOR", True)
_PANEL_RIDE_COVERAGE = _knob("CBBE2UBE_PANEL_RIDE_COVERAGE", 0.9)
# WHERE in the panel's displacement spread to sit the plate, as a quantile along
# the panel's own ride direction. 0.5 is the plain mean: the plate lands in the
# middle of what its verts individually wanted, so the half that wanted to move
# further out end up INSIDE the body -- measured 83 -> 149 verts inside on the
# reported piece. Raising it slides the whole plate outward toward what its
# most-displaced verts asked for, trading standoff for clearance. This is the
# straight-plate trade made adjustable rather than assumed.
_PANEL_RIDE_Q = _knob("CBBE2UBE_PANEL_RIDE_Q", 0.5)


_PANEL_RIDE_PUSH_MAX = _knob("CBBE2UBE_PANEL_RIDE_PUSH_MAX", 1.5)


# How much a panel may be uniformly GROWN to clear the body, when translating it
# cannot. 1.0 forbids it (translation only).
#
# 1.04 IS NOT AN ARBITRARY MIDPOINT. Measured on the reported cuirass, the LIVE
# pipeline already grows that panel to scale 1.038 all by itself -- it just gets
# there by stretching the plate unevenly instead of sizing it. So a 4% cap ships
# the plate at the size it ships at today; what changes is that the growth is
# uniform. The full sweep, front facet of the bust panel:
#
#   cap      scale   rigid p95   SIMILARITY p95   strain>10%   inside   worst
#   live     1.038     0.805         0.443           9.3%        83     -0.78
#   1.00     1.001     0.196         0.189           2.4%       149     -1.26
#   1.02     1.020     0.338         0.150           2.4%       113     -1.08
#   1.04     1.039     0.677         0.250           3.5%        89     -0.91
#   1.08     1.077     1.334         0.587           5.8%        53     -0.55
#
# Read the SIMILARITY column for shape and `inside` for clipping; the rigid
# column charges uniform growth as if it were distortion (see `sim_resid` in the
# scratch metric). 1.04 is the only setting that improves shape (0.443 -> 0.250)
# without giving up clearance against the build that ships today.
#
# Growing further is NOT monotone in shape: at 1.08 the enlarged panel starts
# overlapping neighbouring layers and the layer-ORDER repair, which runs after
# this, bends it back -- similarity 0.189 -> 0.587. That is why the cap exists
# at all rather than "grow until clear".
_PANEL_RIDE_SCALE_MAX = _knob("CBBE2UBE_PANEL_RIDE_SCALE_MAX", 1.04)


# Only verts actually NEAR another garment surface can be said to be inside it.
# Beyond this a nearest-VERTEX normal is arbitrary -- a vert on the far side of
# the torso resolves to some rim vertex whose normal points wherever the rim
# faces. On an 18k-vert plate against a narrow belt strip that noise IS the
# measurement: an ungated read showed a plate driving 1641 -> 3189 verts into
# the belts, and gating showed the true figure was ZERO in every arm. It sent me
# after the wrong pair. Not needed for the BODY, which is a closed surface that
# encloses every garment vert, so its nearest-vertex normal is always meaningful.
_LAYER_NEAR = 2.0


def _penetration_delta(base_pts, cand_pts, kd, ov, on, near=None):
    """(NET change in verts inside `ov`, how much deeper the worst got).

    RELATIVE to what would otherwise ship, never absolute -- armour layers
    overlap heavily by design, so an absolute penetration count says nothing
    about whether a change is an improvement.

    NET, and that is not a detail. Counting only verts that go clear -> inside,
    without crediting the ones that go inside -> clear, is a one-sided metric
    that reads any reshuffle as a regression. It rejected a panel whose layer
    contacts all IMPROVED (into the pauldrons 446 -> 353, into the choker
    574 -> 385, into the layer beneath 538 -> 529) because a minority of its
    verts moved the other way. A pass that cannot see its own improvements will
    decline every one of them.
    """
    db, jb = kd.query(base_pts)
    cb = np.einsum('ij,ij->i', base_pts - ov[jb], on[jb])
    dc, jc = kd.query(cand_pts)
    cc = np.einsum('ij,ij->i', cand_pts - ov[jc], on[jc])
    if near is not None:
        cb = np.where(db < near, cb, 1.0)
        cc = np.where(dc < near, cc, 1.0)
    return (int(np.sum(cc < 0.0)) - int(np.sum(cb < 0.0)),
            float(max(0.0, cb.min() - cc.min())))


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# ACCEPTANCE BUDGET. A rigidified panel may not make body clearance materially
# worse than what would otherwise ship. Without this the pass freezes panels it
# CANNOT fit clear -- `_panel_fit_out_of_body` returns best-effort growth even
# when the growth fails, so a body-hugging inner layer gets frozen half inside
# the body. Measured on a second piece: a 3353-vert corset went 469 -> 1534
# verts inside, worst -0.37 -> -1.16u, while the outer plate on the same piece
# improved. One number cannot tell those apart; a per-panel budget can.
#
# The two pieces measured separate by more than an order of magnitude, so these
# are not knife-edge values -- newly-inside as a fraction of the panel:
#     outer chest plate (WANT)   ~0.5%   deepened 0.13u
#     inner corset      (REJECT) ~32%    deepened 0.79u
# Calibrated on exactly two pieces, so widen only with a measurement.
_PANEL_RIDE_MAX_NEW_INSIDE = _knob("CBBE2UBE_PANEL_RIDE_MAX_NEW_INSIDE", 0.03)
_PANEL_RIDE_MAX_DEEPEN = _knob("CBBE2UBE_PANEL_RIDE_MAX_DEEPEN", 0.35)


# #ride-feather -- OPT-IN, `CBBE2UBE_RIDE_FEATHER=1`.
#
# The ride is the ONLY pass with no feathering of its own. Every other pass that
# moves cloth spreads the movement into the surrounding verts so it cannot
# become a spike; the ride assigns `source + interpolated displacement` per
# vertex and applies it raw. Neighbouring verts can take their displacement from
# different reference triangles, and on a finely modelled strip that
# discontinuity IS the jagged edge.
#
# WHY THIS AND NOT #smooth-reach: on the piece reported jagged, the ride is the
# ONLY pass that fires (46096 verts; cleavage, overlay-lift, layer-order,
# ride-clamp, panel-ride and joint-solve all zero). Scaling the OTHER passes'
# feather reach therefore could not touch the three worst shapes -- measured,
# `3ButtonsWaist` 100 folds, `3PocketsWaist` 222 and `3RopeTied` 205 were
# byte-for-byte unchanged by it. There was no feathering there to scale.
#
# NORMALISED (Shepard) SMOOTHING, not plain smoothing. The displacement is zero
# outside the ridden mask, so averaging against those zeros would drag the field
# DOWN at the mask rim -- shrinking the very offset the ride exists to hold and
# opening a gap exactly at the layer boundary. Smoothing the field and its own
# indicator and dividing keeps the magnitude at the boundary and only removes
# the vert-to-vert discontinuity.
#
# Kept deliberately SHALLOW (1 round before the tessellation scaling). The ride's
# job is holding each layer's authored offset to the one beneath AT EVERY POINT,
# so this may take the jaggedness out of the field, not flatten it.
RIDE_FEATHER = _flag("CBBE2UBE_RIDE_FEATHER", False)
_RIDE_FEATHER_ITERS = _knob("CBBE2UBE_RIDE_FEATHER_ITERS", 1, int)


# (moved to nif_convert_layers.py, 2026-09-01)


def _stack_probe(shape_jobs, verts_by_name):
    """(name, verts, normals, kdtree) for every garment shape in a placed stack.

    Built ONCE from a baseline ride so a panel can be judged against the WHOLE
    piece. The ride's own `base_fin` reference only ever holds the layers
    BENEATH the current one -- the outer layers have not been placed yet -- so a
    panel that punches UP into an outer layer is invisible to it. Measured: a
    rigidified chest plate went 1641 -> 3188 verts inside the belts ABOVE it
    while every beneath-test passed.
    """
    out = []
    for j in shape_jobs:
        try:
            nm = j["src"].name
            v = verts_by_name.get(nm)
            if v is None or len(v) < 12:
                continue
            t = np.asarray(j["src"].tris, dtype=np.int64).reshape(-1, 3)
            if not len(t):
                continue
            n = _vertex_normals_from_tris(np.asarray(v, dtype=np.float64), t)
            from scipy.spatial import cKDTree as _KDs
            out.append((nm, np.asarray(v, dtype=np.float64),
                        np.asarray(n, dtype=np.float64),
                        _KDs(np.asarray(v, dtype=np.float64))))
        except Exception:
            continue
    return out


# (moved to nif_convert_fitgeom.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


# (moved to nif_convert_layers.py, 2026-09-01)


def _fit_shapes_swap(ctx) -> None:
    """The body-swap path's per-shape fit chain: warp, conform with the nipple map, groove smoothing, snap, panel rigidity, the anti-poke, inflate-over-bust, chain blend, local-scale uniformising and the short-edge cap, plus the fine-animation sub-branch, for every non-body shape of a piece that gets a UBE body injected.

    Lifted verbatim out of `convert_nif_phase2` on 2026-09-01 (audit step 6, increment 1):
    the loop body below is the orchestrator's own text, unchanged; `ctx` carries
    exactly the orchestrator locals it read. Results still flow through the
    mutated containers on `ctx` (the shape jobs and the failure list).
    """
    _layer_extra = ctx._layer_extra
    _rebury_motion_w = ctx._rebury_motion_w
    # The `[clip-risk]` telemetry's lazy KD-tree. It was initialised in
    # `convert_nif_phase2` and never handed over, and because the block below
    # ASSIGNS it, it was an unbound LOCAL here rather than a global -- so every
    # shape raised UnboundLocalError straight into that block's `except`, and
    # the residual-verts-inside-the-body diagnostic has never once printed on
    # the body-swap path. Pre-existing; found by the same pyflakes sweep that
    # caught `src_body_n_authored` below.
    _antipoke_stat_tree = None
    biped_slots = ctx.biped_slots
    body_delta_for_warp_p2 = ctx.body_delta_for_warp_p2
    body_names = ctx.body_names
    body_nipple_for_p2 = ctx.body_nipple_for_p2
    body_norms_for_p2 = ctx.body_norms_for_p2
    body_verts_for_p2 = ctx.body_verts_for_p2
    cbbe_idx = ctx.cbbe_idx
    cbbe_verts_for_warp_p2 = ctx.cbbe_verts_for_warp_p2
    dst_path = ctx.dst_path
    failed = ctx.failed
    hdt_collider_names = ctx.hdt_collider_names
    hdt_softbody_names = ctx.hdt_softbody_names
    layered_cloth_names = ctx.layered_cloth_names
    preset_template_verts = ctx.preset_template_verts
    preset_user_verts = ctx.preset_user_verts
    reskin_armor = ctx.reskin_armor
    reskin_far_dist = ctx.reskin_far_dist
    reskin_k = ctx.reskin_k
    reskin_near_dist = ctx.reskin_near_dist
    shape_jobs = ctx.shape_jobs
    skipped_collision = ctx.skipped_collision
    # Hardened twin of `src_body_n_p2`, read ONLY by the two authored floors.
    # It has to come through `ctx` like everything else here: bound as a plain
    # local in `convert_nif_phase2`, it is simply an undefined global at the two
    # call sites below, and the resulting NameError is swallowed by the
    # per-shape handler -- so the floors quietly did not run on ANY body-swap
    # piece while the A/B table showed a clean, believable zero.
    src_body_n_authored = ctx.src_body_n_authored
    src_body_n_p2 = ctx.src_body_n_p2
    src_body_v_p2 = ctx.src_body_v_p2
    src_morph_shapes = ctx.src_morph_shapes
    src_nif = ctx.src_nif
    ube_base_for_pass1 = ctx.ube_base_for_pass1
    ube_body_ref_path = ctx.ube_body_ref_path
    ube_idx = ctx.ube_idx

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

            # ---- DIAGNOSTICS ON THE FINE-ANIMATION SUB-BRANCH -------------
            # Phase 2's copy of the same blind spot as `convert_nif`'s: this
            # branch `continue`s before the per-shape arming below, so a
            # gauntlet or boot shape produced no row here either. Smaller than
            # on the copy path (3.8% of sampled body-swap source shapes vs
            # 20.2%) but the same defect, and armed with the SAME labels so the
            # two paths stay directly comparable
            # ([[project_pass_usefulness_audit_2026_08_17]]).
            _surv_hf2 = fit_metrics.DisplacementSurvival()
            if not _surv_hf2.armed:
                _surv_hf2 = None
            else:
                _surv_hf2.checkpoint("entry", hf_orig)
            _dump_hf2 = fit_metrics.GeometryDump()
            if not _dump_hf2.armed:
                _dump_hf2 = None
            else:
                _dump_hf2.checkpoint("entry", hf_orig)

            def _stage_hf2(label, v, _s=_surv_hf2, _d=_dump_hf2):
                """A pass boundary on phase 2's fine-animation sub-branch."""
                if v is None:
                    return
                if _s is not None:
                    _s.checkpoint(label, v)
                if _d is not None:
                    _d.checkpoint(label, v)

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
                        tris=np.asarray(s.tris, dtype=np.int64),
                    ).astype(np.float64)
                    if hf_ef is not None:
                        wf = (1.0 - hf_ef)[:, None]
                        hf_verts = hf_orig + (warped - hf_orig) * wf
                    else:
                        hf_verts = warped
                    hf_verts_modified = True
                except Exception as e:
                    failed.append((f"{s.name}:warp-hf", repr(e)))
                # Outside the try, so a raised warp reads as "moved nothing"
                # rather than as a missing stage.
                _stage_hf2('warp_hf', hf_verts)

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
                _stage_hf2('inflate_hf', hf_verts)

            # #panel-rigidity-fine-anim, opt-in. Same contract as the copy-path
            # sibling: digits masked out, effect recorded for attribution.
            if PANEL_RIGIDITY_FINE_ANIM and PANEL_RIGIDITY > 0:
                try:
                    # #panel-rigid-early-clearance -- see the copy-path hf
                    # sibling. All four panel-rigidity sites now take the same
                    # form, so the flag cannot reach three quarters of them.
                    if (PANEL_RIGID_EARLY_CLEAR
                            and body_verts_for_p2 is not None
                            and body_norms_for_p2 is not None):
                        _pv_h2, _np_h2, _wd_h2 = _rigidify_within_clearance(
                            hf_orig, hf_verts,
                            np.asarray(s.tris, dtype=np.int64),
                            body_verts_for_p2, body_norms_for_p2,
                            PANEL_RIGIDITY,
                            skip_mask=_extremity_vert_mask(s, len(hf_verts)),
                            min_verts=PANEL_RIGIDITY_MIN_VERTS)
                    else:
                        _pv_h2, _np_h2, _wd_h2 = _partial_rigid_panels(
                            hf_orig, hf_verts,
                            np.asarray(s.tris, dtype=np.int64), PANEL_RIGIDITY,
                            skip_mask=_extremity_vert_mask(s, len(hf_verts)),
                            min_verts=PANEL_RIGIDITY_MIN_VERTS)
                    if _np_h2:
                        hf_verts = _pv_h2
                        hf_verts_modified = True
                        _stage_hf2('panel_rigidity_hf', hf_verts)
                        _note_pass_effect(
                            "#panel-rigidity-fine-anim",
                            f"{s.name}: {_np_h2} panel(s), worst deform "
                            f"{_wd_h2:.3f}u", dst_path)
                except Exception as _pe_h2:
                    _note_pass_failure("panel-rigidity/fine-anim-p2", _pe_h2)

            # Geometry chain ends here on this branch too, and phase 2 skips
            # MORE than the copy path does: no conform, no groove smooth, no
            # panel rigidity, no anti-poke, no chain blend. Same unclosed gap,
            # same measurement, same caution -- see the sibling site in
            # `convert_nif` for the numbers and for why the parity guard cannot
            # see either of them. #panel-rigidity is the one to settle first.

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
            # Flush BEFORE the `continue`; phase 2's own flush is past it and
            # would never run for this shape. Same reporting contract as the
            # sibling sites: a flush error is RECORDED and an EMPTY dump is
            # called out rather than passing as a clean run.
            if _surv_hf2 is not None:
                try:
                    _surv_hf2.flush(dst_path, s.name, hf_verts)
                except Exception as e:
                    failed.append((f"{s.name}:survival-hf", repr(e)))
                finally:
                    _surv_hf2.release()
            if _dump_hf2 is not None:
                try:
                    if not _dump_hf2.flush(dst_path, s.name, s.tris, hf_verts):
                        failed.append((f"{s.name}:stagedump-hf",
                                       "wrote nothing"))
                except Exception as e:
                    failed.append((f"{s.name}:stagedump-hf", repr(e)))
                finally:
                    _dump_hf2.release()
            continue
        override = None
        # Body-space offset: shapes with non-identity transforms must have
        # warp/inflate/conform computed in body space or they match the wrong
        # body region. Applied before math, removed before storage. Zero for
        # identity-transform shapes (no effect).
        # Checked against the body: a transform translation that moves this
        # shape AWAY from the body is not a body-space correction and is
        # discarded. Every fit pass below consumes `_off_p2`, so the check
        # belongs here rather than in each of them.
        _off_p2 = shape_body_offset(s, body_verts=body_verts_for_p2)
        _sv_body = np.asarray(s.verts, dtype=np.float64) + _off_p2
        # PRECONDITION, reported before any pass runs. Every pass below computes
        # against the body and assumes this shape is in body space; nothing used
        # to assert it, and when the assumption broke all twelve computed against
        # a garment 40u out of place and the piece shipped clipping. Recording it
        # here is what makes that class of error greppable instead of a hunt.
        if body_verts_for_p2 is not None:
            try:
                # The RAW (unchecked) offset, deliberately: `_off_p2` has already
                # had a bad offset discarded, so reporting on it could never
                # observe that a correction happened -- the report would be
                # structurally incapable of firing, which is how a check ends up
                # measuring nothing. Give it the raw value and let it judge.
                _fr = fit_metrics.frame_report(
                    np.asarray(s.verts, dtype=np.float64),
                    shape_body_offset(s), body_verts_for_p2)
                if _fr.get("corrected") or _fr.get("suspect"):
                    fit_metrics.record_frame(dst_path, s.name, _fr)
                    if _fr.get("corrected"):
                        print(f"    [frame] {s.name}: transform offset "
                              f"{_fr['offset']} DISCARDED -- it moved the shape "
                              f"off the body ({_fr['raw_reach']}u -> "
                              f"{_fr['offset_reach']}u)")
            except Exception:
                pass          # a precondition report must never fail a convert
        # VERIFY harness for this shape. Arms only where the validated metric
        # can actually see the shape (enough covered skin in the measured band);
        # on a piece it cannot see, guarding would burn ray casts to learn
        # nothing, which is the "measured nothing" failure wearing a new hat.
        #
        # CHAIN contract: diagnose here, verify at the end, roll back to the
        # best checkpoint if the chain AS A WHOLE regressed. Two measurements
        # per armed shape against eleven for guarding every pass -- and more
        # correct, because intermediate regressions are legitimate. This
        # replaced per-pass guards on the anti-poke and the soft-cloth inflate:
        # over 48 traced shapes neither ever regressed, `conform` was the only
        # pass that did, all 5 of its regressions were recovered downstream,
        # and 0 of 48 shapes ended worse than they started. Per-pass reverting
        # would therefore have blocked a correct pass and biased every garment
        # looser -- the over-inflation reported twice from the game.
        _tracer = None
        _chain = None
        # Deliberately OUTSIDE the body-geometry guard below and armed with no
        # region at all: this one compares a pass against its own displacement
        # field, so it needs neither. Gating it the way the two metric-based
        # harnesses are gated would blind it on every shape the bust band cannot
        # see -- which includes the hip, the band it exists to explain.
        _surv = fit_metrics.DisplacementSurvival()
        if not _surv.armed:
            _surv = None
        else:
            _surv.checkpoint("entry", _sv_body)
        # Geometry dump, default OFF (CBBE2UBE_STAGE_DUMP=<dir>). The same
        # snapshots written to disk, so an OFFLINE question can be bisected
        # over the chain without adding a measurement in here for each one --
        # and without N kill-switch conversions, which misattribute wherever
        # two passes cancel each other.
        _dump = fit_metrics.GeometryDump()
        if not _dump.armed:
            _dump = None
        else:
            _dump.checkpoint("entry", _sv_body)
        if body_verts_for_p2 is not None and body_norms_for_p2 is not None:
            try:
                _gtris = np.asarray(s.tris, dtype=np.int64).reshape(-1, 3)
                _c = fit_metrics.ChainGuard(
                    body_verts_for_p2, body_norms_for_p2, _gtris)
                _chain = _c if _c.armed else None
                # Diagnostic, default OFF (CBBE2UBE_PASS_TRACE=1). Measures
                # after EVERY pass and reverts NOTHING -- this is how a pass
                # earns a guard, rather than being guarded on suspicion. It is
                # what identified `groove_smooth` regressing 13 of 42 shapes,
                # and what showed that reverting per pass was the wrong
                # contract.
                _t = fit_metrics.PassTracer(
                    body_verts_for_p2, body_norms_for_p2, _gtris)
                _tracer = _t if _t.armed else None
                if _tracer is not None:
                    _tracer.mark("entry", _sv_body)
                if _chain is not None:
                    # Share the tracer's entry measurement when it already ran,
                    # so turning the trace on does not double the cost.
                    _chain.begin(_sv_body, known=(
                        _tracer._prev_count if _tracer is not None else None))
            except Exception:
                _tracer = None
                _chain = None

        # A pass boundary: trace it (opt-in) and snapshot it (always). The
        # snapshot is an array copy -- microseconds against ~120ms for a
        # measurement -- so the chain can afford to remember every pass and pay
        # for measurements only if the final verify fails. `skip_none` stays
        # False here: this path has always let a None snapshot reach
        # `_chain.checkpoint`, and changing that changes when the rollback
        # chain records one.
        _stage = make_stage_hook(tracer=_tracer, chain=_chain, surv=_surv,
                                 dump=_dump, skip_none=False)

        if preset_template_verts is not None and preset_user_verts is not None:
            try:
                override = bake_preset_into_armor(
                    _sv_body,
                    preset_template_verts, preset_user_verts,
                    k=4, close_threshold=5.0,
                )
                _stage('bake_preset', override)
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
                    tris=np.asarray(s.tris, dtype=np.int64),
                )
                _stage('warp', override)
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
                            # #authored-inflate: the author's own mesh and body,
                            # so the push can be a floor rather than a blind
                            # addition. Same pair `conform` reads just below,
                            # except for the normals: the floor takes the
                            # HARDENED array, because the stored one is all zero
                            # on this pack's source body and a floor that reads
                            # `authored == 0` everywhere is not a floor.
                            src_armor_verts=_sv_body,
                            src_body_verts=src_body_v_p2,
                            src_body_normals=src_body_n_authored,
                            tris=np.asarray(s.tris, dtype=np.int64),
                            # #authored-nipple-exempt: the tip keeps the full
                            # additive push -- the author's CBBE bust cannot
                            # inform clearance over UBE's larger nipple.
                            body_nipple=body_nipple_for_p2,
                        )
                        _stage('inflate', override)
                    except Exception as e:
                        # RECORDED. This pass is the pack's main clearance
                        # provider; a silent failure ships a garment with none.
                        failed.append((f"{s.name}:inflate", repr(e)))
                # Standoff-preserving conform: reel over-projected verts back to
                # their source clearance (pull-in only, >= min clearance).
                #
                # #phase2-conform -- DEFAULT ON, kill switch
                # CBBE2UBE_NO_PHASE2_CONFORM. Adding the switch is a NO-OP by
                # construction (it defaults to the previous behaviour); it
                # exists because this pass could not be A/B'd in game at all.
                #
                # AND `CBBE2UBE_NO_CONFORM` IS NOT IT. That flag gates
                # `CONFORM_FITTED_CLOTH` -> `_conform_fitted_to_body`, a WEIGHTS
                # pass in the shared tail. Two different passes both called
                # "conform", and the obvious-looking flag switches the other
                # one -- so an A/B run against it would have measured nothing
                # and read as "conform does not matter".
                #
                # WHY IT IS WORTH A SWITCH: on the body-swap path this pass
                # MOVES THE MOST of any stage (0.70u median) and KEEPS THE LEAST
                # (survival 0.38 pooled / 0.32 per piece), measured over 28
                # pieces ([[project_pass_usefulness_audit_2026_08_17]]). The copy
                # path, 78% of the pack, runs no equivalent at all
                # (`PHASE1_CONFORM` is default off) and is judged good in game --
                # so "is this pass earning its place" is a real question with a
                # natural control. A bind-pose survival number CANNOT settle it;
                # only an in-game A/B can, and until now there was no way to run
                # one.
                if (PHASE2_CONFORM
                        and src_body_v_p2 is not None
                        and body_verts_for_p2 is not None
                        and body_norms_for_p2 is not None):
                    try:
                        # Morph map lets the conform restore the AUTHORED fit in
                        # static zones while leaving morph zones alone.
                        # #static-authored-fit
                        try:
                            _amp_conf = _cached_body_morph_amplitude(
                                _find_ube_body_osd(), body_norms_for_p2,
                                len(body_verts_for_p2))
                        except Exception:
                            _amp_conf = None
                        # #derived-clearance: the clearance each slider TAKES
                        # AWAY, per body vert, over the whole slider envelope.
                        # Already computed for the anti-poke; conform's target
                        # never consumed it. None when unavailable, which leaves
                        # the blend path exactly as it was.
                        try:
                            _diff_conf = _cached_body_morph_differential(
                                _find_ube_body_osd(), body_verts_for_p2,
                                body_norms_for_p2)
                        except Exception as _de:
                            _note_pass_failure(
                                "_cached_body_morph_differential/conform", _de)
                            _diff_conf = None
                        override = conform_to_source_standoff(
                            _sv_body,
                            src_body_v_p2, src_body_n_p2,
                            override, body_verts_for_p2, body_norms_for_p2,
                            ube_body_nipple=body_nipple_for_p2,
                            morph_amplitude=_amp_conf,
                            morph_differential=_diff_conf,
                            tris=np.asarray(s.tris, dtype=np.int64),
                            conform_margin=CONFORM_MARGIN,
                        )
                        _stage('conform', override)
                    except Exception as e:
                        # RECORDED, not swallowed. This pass is the only one
                        # that reels an over-projected garment back onto the
                        # body, so when it dies the garment simply stays out
                        # there -- and a bare `pass` made that indistinguishable
                        # from "nothing to conform". Traced from a strap-line
                        # gap reported in game: the pass was absent from the
                        # per-pass trace entirely and nothing anywhere said so.
                        failed.append((f"{s.name}:conform", repr(e)))
                # Groove-smooth: flatten warp-induced indent grooves on tight
                # bust cloth. Mirrors phase-1 call; near-body verts only.
                # Source body passed for #groove-authored-cap -- the SAME arrays
                # the conform above was given, so the authored standoff the cap
                # bounds against is the one the conform aimed at.
                if override is not None and body_verts_for_p2 is not None:
                    try:
                        override = _smooth_warp_grooves(
                            _sv_body, np.asarray(override, dtype=np.float64),
                            body_verts_for_p2,
                            ube_body_normals=body_norms_for_p2,
                            src_body_verts=src_body_v_p2,
                            src_body_normals=src_body_n_p2)
                        _stage('groove_smooth', override)
                    except Exception as _pe:
                        _note_pass_failure("_smooth_warp_grooves", _pe)
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
                _stage('snap_legacy', override)
            except Exception as e:
                failed.append((f"{s.name}:snap", repr(e)))
        # #panel-rigidity. Give layered PLATES back the rigidity the per-vertex
        # fit took off them, before the anti-poke below can push anything clear
        # again. See the constant for the measurements and the trade.
        # bound here, not inside the branch: the post-anti-poke half reads it and
        # a shape can reach one block without the other.
        _skip = None
        if PANEL_RIGIDITY > 0 and override is not None and _sv_body is not None:
            try:
                try:
                    _skip = simulated_vert_mask(s, MIXED_CLOTH_CHAIN_EPS)
                except Exception:
                    _skip = None
                # #panel-rigid-early-clearance -- see the copy-path sibling.
                # Wiring BOTH paths together, because a fit flag that reaches one
                # path splits the pack and makes every later verdict
                # untransferable (#panel-rigidity spent a release like that).
                if (PANEL_RIGID_EARLY_CLEAR and body_verts_for_p2 is not None
                        and body_norms_for_p2 is not None):
                    _pv, _np_, _wd = _rigidify_within_clearance(
                        _sv_body, override, np.asarray(s.tris, dtype=np.int64),
                        body_verts_for_p2, body_norms_for_p2,
                        PANEL_RIGIDITY, skip_mask=_skip,
                        min_verts=PANEL_RIGIDITY_MIN_VERTS)
                else:
                    _pv, _np_, _wd = _partial_rigid_panels(
                        _sv_body, override, np.asarray(s.tris, dtype=np.int64),
                        PANEL_RIGIDITY, skip_mask=_skip,
                        min_verts=PANEL_RIGIDITY_MIN_VERTS)
                if _np_:
                    override = _pv
                    _stage('panel_rigidity', override)
                    print(f"    [panel-rigidity] {s.name}: {_np_} panel(s) "
                          f"re-rigidified at {PANEL_RIGIDITY:.2f} "
                          f"(worst deformation was {_wd:.3f}u)")
            except Exception as _pe:
                _note_pass_failure("panel-rigidity", _pe)
        # FINAL anti-poke: push body-slot armor clear of the injected body.
        # Runs LAST in body space so nothing undoes it; skips soft-body cloth
        # and HDT-SMP physics shapes (moving verts would disturb the sim).
        # _bust_driven (bust genuinely physics-driven) gates ONLY the softcloth
        # elif below -- a rigid-bust HDT-rigged robe stays out of BOTH passes and
        # keeps its warp position instead of being ballooned. #softcloth-bust-driven-gate
        _bust_driven = _shape_bust_is_softbody_driven(
            s, set(ube_base_for_pass1.bone_names or [])
            if ube_base_for_pass1 is not None else set(),
            hdt_softbody_names)
        # #smp-collision-only-antipoke (default OFF, opt-in). The
        # `_shape_has_hdt_smp_rigging` term below skips ANY shape carrying SMP
        # rigging, on the grounds that moving its verts disturbs the sim. That is
        # right for a SIMULATED garment, but a shape whose XML only declares it as
        # a COLLIDER has no per-vertex sim to disturb -- and it still gets no bust
        # clearance, so the body pushes through it. Traced on a vanilla hide
        # cuirass: 0.0% of covered bust verts exposed in the CBBE source vs 6.9%
        # after a body-swap convert (worst -0.82u), with the clearance pass never
        # invoked and no clip-risk telemetry to show it.
        #
        # OFF BY DEFAULT ON PURPOSE. Running anti-poke on SMP shapes was built,
        # measured and REVERTED once before: it fixes flat regions but spreads
        # verts apart on CONVEX ones (a suit's butt went 28% -> 29%, worst poke
        # -1.81u -> -2.20u), and the breast is the same rounded-volume case. The
        # offline A/B here is a PARTIAL win with a sharp optimum -- 6.9% -> 2.4%
        # at max_push 1.0, but WORSE (8.1%) at 0.6 and only 5.7% at 3.0 -- so it
        # wants in-game confirmation before it becomes a default.
        # Soft-body and collider shapes stay excluded by the terms above either way.
        # The collider term is relaxed too, deliberately. `_hdt_collider_shape_names`
        # returns EVERY `<per-triangle-shape>`, which includes a VISIBLE garment that
        # doubles as its own collider (the traced cuirass is exactly that). Two
        # reasons that is safe here: textureless proxies never reach this loop at all
        # (pass 1 drops them, `if not (s.textures or {})`), and the exclusion's own
        # rationale is about preserving authored SKIN WEIGHTING, which this pass does
        # not touch -- it moves verts, and moving a shape that IS its own collider
        # keeps the two identical rather than desyncing them. Soft-bodies stay out.
        # COLLISION-ONLY, as the constant's name promises. This was previously
        # `FLAG and s.name not in hdt_softbody_names`, which admitted EVERY shape
        # that is not a per-vertex soft-body -- including bone-driven SMP chain
        # garments, which ARE simulated and are the skirt-collapse family. Two
        # narrowings:
        #   * the shape must itself be a declared per-triangle COLLIDER, which is
        #     the case the relaxation was written for (a visible garment doubling
        #     as its own collider has no per-vertex sim to disturb);
        #   * and the NIF must contain NO simulated cloth at all. Moving a collider
        #     that some `<per-vertex-shape>` in the same system rests against
        #     changes that cloth's authored rest state, so it starts the first
        #     frame interpenetrating and FSMP resolves it with an impulse.
        # An off-by-default flag whose name overstates its scope is how a bad
        # default gets adopted later, so the code now matches the label.
        _smp_relax = (SMP_COLLISION_ONLY_ANTIPOKE
                      and s.name in hdt_collider_names
                      and not hdt_softbody_names)
        # #smp-structural-relax -- a SECOND, independent reason the SMP term may
        # be withdrawn here, and the only site that gets it. The shape was
        # classified as rigged purely because the reference BODY lacks ordinary
        # skeleton bones it is weighted to (hands, fingers, pauldrons, twist):
        # there is no simulation to disturb, so the clearance pass is safe.
        # DEFAULT OFF like the flag above, and for the same reason: the
        # classification is right but ACTING on it measured worse (pauldrons
        # went from 5 to 9 verts inside the body -- see the constant's comment).
        # Deliberately NOT folded into `_shape_has_hdt_smp_rigging`, whose eight
        # call sites would then also enable the reskin and conform passes.
        _structural_relax = (
            s.name not in hdt_collider_names
            and s.name not in hdt_softbody_names
            and _smp_rigging_is_structural_only(
                s, set(ube_base_for_pass1.bone_names or [])
                if ube_base_for_pass1 is not None else set()))
        # #mixed-cloth-clearance -- a THIRD relax, per VERTEX rather than per
        # shape. Admit a rigged shape that also carries cloth with NO chain
        # weight, and protect the simulated part by restoring it after the pass
        # (see the restore below). `_mix_sim` is the untouchable set.
        _mix_sim = None
        _mixed_relax = False
        if MIXED_CLOTH_CLEARANCE and s.name not in hdt_softbody_names:
            try:
                _sim = simulated_vert_mask(s, MIXED_CLOTH_CHAIN_EPS)
                # Only relevant when the shape is genuinely MIXED. A wholly
                # chain-driven shape stays excluded exactly as before.
                if _sim.any() and not _sim.all():
                    _mix_sim = _sim
                    _mixed_relax = True
            except Exception:
                _mix_sim, _mixed_relax = None, False
        if (body_verts_for_p2 is not None and body_norms_for_p2 is not None
                and (biped_slots & (BIPED_SLOT32_BIT | BIPED_SLOT49_BIT))
                and s.name not in RESKIN_SKIP_NAMES
                and s.name not in hdt_softbody_names
                and (_smp_relax
                     or _structural_relax
                     or _mixed_relax
                     or (s.name not in hdt_collider_names
                         and not _shape_has_hdt_smp_rigging(
                             s, set(ube_base_for_pass1.bone_names or [])
                             if ube_base_for_pass1 is not None else set())))):
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
                # #clearance-differential: what the worst slider TAKES AWAY,
                # rather than how far the body grows. Cached per (OSD, verts).
                _antipoke_diff = None
                if CLEARANCE_DIFFERENTIAL:
                    try:
                        _antipoke_diff = _cached_body_morph_differential(
                            _find_ube_body_osd(), body_verts_for_p2,
                            body_norms_for_p2)
                    except Exception:
                        _antipoke_diff = None
                # Jiggle-overshoot headroom (default ON): only rigid/fitted
                # cloth reaches this pass (softbody/HDT shapes are skipped
                # above), which is exactly what a bouncing body punches through.
                _antipoke_jig = None
                if JIGGLE_CLEARANCE_ENABLED:
                    try:
                        _antipoke_jig = _body_jiggle_weight(ube_base_for_pass1)
                    except Exception:
                        _antipoke_jig = None
                # A shape reached ONLY via the SMP relaxation is pushed exactly to
                # the clearance target, not the 3.0 default -- overshoot spreads
                # verts on the convex bust and opens new gaps. #smp-collision-only-antipoke
                _ap_kw = {}
                if _smp_relax and (
                        s.name in hdt_collider_names
                        or _shape_has_hdt_smp_rigging(
                            s, set(ube_base_for_pass1.bone_names or [])
                            if ube_base_for_pass1 is not None else set())):
                    _ap_kw["max_push"] = SMP_ANTIPOKE_MAX_PUSH
                    print(f"    [smp-antipoke] {s.name}: clearance pass enabled "
                          f"(collision-only SMP, max_push={SMP_ANTIPOKE_MAX_PUSH})")
                override = clear_armor_outside_body(
                    base_v, body_verts_for_p2, body_norms_for_p2,
                    body_nipple=body_nipple_for_p2,
                    morph_amplitude=_antipoke_amp,
                    morph_differential=_antipoke_diff,
                    jiggle_amplitude=_antipoke_jig,
                    req_extra=_layer_extra.get(s.name, 0.0),
                    # Topology is needed by the scalar feather (ANTIPOKE_SMOOTH)
                    # AND by the #clearance-field solve; pass it when EITHER is
                    # on. With both off this stays None, so the OFF path is
                    # unchanged.
                    tris=(np.asarray(s.tris, dtype=np.int64)
                          if (ANTIPOKE_SMOOTH_ENABLED or CLEARANCE_FIELD_SOLVE)
                          else None),
                    # #authored-antipoke: the author's own mesh and body, so the
                    # clearance requirement can be relaxed where they had the
                    # vertex tighter AND the body does not grow there. Same pair
                    # conform and inflate read, and like inflate's floor it takes
                    # the HARDENED normals -- the stored ones are all zero on
                    # this pack's source body, which reads as "the author fitted
                    # everything skin-tight" and disarms the relaxation.
                    src_armor_verts=_sv_body,
                    src_body_verts=src_body_v_p2,
                    src_body_normals=src_body_n_authored,
                    **_ap_kw)
                # #mixed-cloth-clearance: hand every SIMULATED vertex straight
                # back. The sim's authored rest state must be bit-identical --
                # a cloth vert moved here starts frame one interpenetrating and
                # FSMP resolves it with an impulse, which is the skirt-collapse
                # family. Only zero-chain-weight cloth keeps the pass's result.
                if _mix_sim is not None and override is not None:
                    try:
                        _ov = np.asarray(override, dtype=np.float64)
                        _bv0 = np.asarray(base_v, dtype=np.float64)
                        if len(_ov) == len(_mix_sim) == len(_bv0):
                            _moved = int((np.linalg.norm(
                                _ov - _bv0, axis=1) > 1e-4).sum())
                            _ov[_mix_sim] = _bv0[_mix_sim]
                            override = _ov
                            _kept = int((~_mix_sim).sum())
                            print(f"    [mixed-cloth] {s.name}: clearance on "
                                  f"{_kept} non-simulated vert(s), "
                                  f"{int(_mix_sim.sum())} simulated vert(s) "
                                  f"restored ({_moved} moved by the pass)")
                    except Exception as _me:
                        _note_pass_failure("mixed-cloth-restore", _me)
                _stage('antipoke', override)
                # #panel-rigidity, second half. Anti-poke re-deforms every panel
                # it pushes, which is what capped the pre-pass at ~0.98u of
                # residual. Recover the rest where there is clearance for it --
                # per PANEL, never per vertex, and never at the cost of a
                # vertex the anti-poke just pushed clear.
                if (PANEL_RIGIDITY > 0 and override is not None
                        and _sv_body is not None):
                    try:
                        _rv, _rn, _rs = _rigidify_within_clearance(
                            _sv_body, override,
                            np.asarray(s.tris, dtype=np.int64),
                            body_verts_for_p2, body_norms_for_p2,
                            PANEL_RIGIDITY, skip_mask=_skip,
                            min_verts=PANEL_RIGIDITY_MIN_VERTS)
                        if _rn:
                            override = _rv
                            _stage('panel_rigidity_post', override)
                            print(f"    [panel-rigidity] {s.name}: {_rn} panel(s) "
                                  f"re-rigidified AFTER anti-poke, mean "
                                  f"strength {_rs:.2f} of {PANEL_RIGIDITY:.2f}")
                    except Exception as _pe2:
                        _note_pass_failure("panel-rigidity/post", _pe2)
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
                and _bust_driven
                and (s.name in hdt_softbody_names
                     or _shape_has_hdt_smp_rigging(
                         s, set(ube_base_for_pass1.bone_names or [])
                         if ube_base_for_pass1 is not None else set()))):
            # Soft-body / HDT-rigged cloth is skipped by the anti-poke above (moving
            # every vert disturbs the sim). The larger UBE breast/butt still punches
            # through it, so nudge ONLY those bands outward to cover, body-preserving.
            # Gated on _bust_driven: a RIGID bust (chains drive only the skirt) skips
            # this -> stays at warp position, not ballooned. #softcloth-bust-driven-gate
            try:
                base_v = (np.asarray(override, dtype=np.float64)
                          if override is not None else _sv_body)
                override = _inflate_cloth_over_bust_butt(
                    base_v, body_verts_for_p2, body_norms_for_p2,
                    tris=np.asarray(s.tris, dtype=np.int64))
                _stage('softcloth', override)
            except Exception as e:
                failed.append((f"{s.name}:softcloth", repr(e)))
        # #rebury-authored. Put back inside the body every vert the author had
        # inside theirs. Runs AFTER inflate/conform/anti-poke on purpose: those
        # all enforce a positive clearance floor, so anywhere earlier they would
        # simply drag the vert back out (the same cancellation the stage dump
        # caught between inflate and conform). Physics cloth and colliders are
        # excluded -- their rest pose is owned by the sim.
        if (REBURY_AUTHORED and override is not None
                and src_body_v_p2 is not None and src_body_n_p2 is not None
                and body_verts_for_p2 is not None
                and body_norms_for_p2 is not None
                and s.name not in hdt_collider_names
                and s.name not in hdt_softbody_names):
            try:
                override, _n_reb = rebury_authored_verts(
                    _sv_body, src_body_v_p2, src_body_n_p2,
                    override, body_verts_for_p2, body_norms_for_p2,
                    tris=np.asarray(s.tris, dtype=np.int64),
                    body_motion_weight=_rebury_motion_w)
                if _n_reb:
                    _stage('rebury', override)
            except Exception as e:
                # RECORDED: a silent failure here reads exactly like "this shape
                # had nothing buried", which is what 96% surfaced looked like for
                # as long as nobody measured it.
                _note_pass_failure(f"{s.name}:rebury-authored", e)
        # Chain-bone cloth stays at SOURCE position so it aligns with its chain
        # bones (recreated at source bind). Per-vertex by chain-weight fraction;
        # hybrid shapes (skirt+chest) keep the chest warped.
        if override is not None:
            override = _physics_chain_nowarp_blend(s, _sv_body, override)
            _stage('chain_blend', override)
        # MINIMUM PUSH -- the only pass here driven by MEASURED skin-through-
        # armour rather than by proximity to the body. Every push pass above
        # keys off "how close is this vert to the body" against a constant, so
        # it moves leather whether or not anything is actually exposed, and
        # cannot move leather that IS exposed but sits further out. This one
        # measures with the validated clip test (calibrated 0.00% on an armour
        # the user confirmed clean in game, 8.87% on one they can see clipping)
        # and moves the smallest amount that clears what it found.
        #
        # CONDITIONAL: a pack census found only 4 of 72 judged pieces (6%)
        # clipping above 1% at the bust, so this exits having moved ZERO verts
        # on the other 94% -- which is both the safety property and what keeps
        # the cost off pieces that do not need it. The clean-armour negative
        # control is asserted in tests/test_minimum_push.py and re-verified on
        # the real meshes (cuirasslight: 0 verts moved, both weights).
        # Off with CBBE2UBE_NO_MIN_PUSH=1.
        # FRAME GUARD: `override is not None` is the only condition under which
        # this shape is known to be in the UBE body's frame. TRACED, not
        # assumed: falling back to `_sv_body` when override is None measures
        # PRE-WARP source geometry, and CBBE's cup is far smaller than UBE's
        # bust, so UBE bust skin sits outside the unwarped garment entirely. On
        # a real piece that reads 8.87% clipping in its FINISHED form, the
        # in-converter attempt reported "no judgeable skin after rim/reach
        # gating" and moved nothing -- the reach gate caught the bad frame and
        # failed safe rather than pushing against a misaligned body.
        #
        # CONSEQUENCE, and it is a real limitation of this integration point:
        # shapes that no earlier phase-2 pass modified are NOT reached here, and
        # that includes pieces in the very class this fix targets. Fixing those
        # requires running the solve where the garment is FINAL (a post-write
        # pass over the output NIF, which is what the validated scratch harness
        # does), not mid-stack. Left in place because it is correct and safe for
        # the shapes it does reach; not yet sufficient on its own.
        if (override is not None
                and body_verts_for_p2 is not None
                and body_norms_for_p2 is not None
                and ube_base_for_pass1 is not None):
            try:
                _mp_v = np.asarray(override, dtype=np.float64)
                # FRAME SELECTION. `_off_p2` (shape_body_offset) is added by
                # every phase-2 pass so its maths runs in body space. That is
                # correct for a shape authored in a shifted space, and WRONG for
                # a skinned shape already in body space whose NiAVObject
                # transform is inert at render. Measured on a real cuirass:
                # translation [-40,0,0] with IDENTITY global_to_skin and verts
                # already placed, so the offset shoves it 40u off the body
                # (bust-skin reach median 29.6u, vs 1.6u for a sibling with zero
                # offset). Ask the geometry which frame is on the body instead of
                # trusting the offset; a shape that genuinely needs the offset
                # still wins, because for it the offset candidate is the closer
                # one. Only a DISPLACEMENT computed in the chosen frame is used,
                # so the offset bookkeeping downstream stays intact.
                _mp_region = fit_metrics.push_region_mask(body_verts_for_p2)
                _mp_cands = [("body-space", _mp_v)]
                if np.any(np.abs(_off_p2) > 1e-6):
                    _mp_cands.append(("no-offset", _mp_v - _off_p2))
                _mp_lbl, _mp_base, _mp_med = fit_metrics.choose_aligned(
                    _mp_cands, body_verts_for_p2, _mp_region)
                _mp_n = len(_mp_v)
                _mp_bw = s.bone_weights or {}
                _mp_vw = [dict() for _ in range(_mp_n)]
                for _b, _pairs in _mp_bw.items():
                    for _vi, _w in _pairs:
                        _iv = int(_vi)
                        if 0 <= _iv < _mp_n:
                            _mp_vw[_iv][_b] = _mp_vw[_iv].get(_b, 0.0) + float(_w)
                _mp_chain = np.asarray(_chain_vert_mask(_mp_vw, _mp_n), bool)
                # Normals must match the CURRENT (post-push-stack) positions,
                # not the source-file ones -- the stored normals belong to the
                # geometry as authored, and this shape has already been warped
                # and inflated by the passes above.
                _mp_tris = np.asarray(s.tris, dtype=np.int64).reshape(-1, 3)
                _mp_gn = np.asarray(
                    _recompute_vertex_normals(
                        _mp_base, _mp_tris,
                        source_normals=(np.asarray(s.normals, dtype=np.float64)
                                        if getattr(s, "normals", None) is not None
                                        else None)),
                    dtype=np.float64)
                _mp_out, _mp_st = fit_metrics.minimum_push(
                    _mp_base,
                    _mp_tris,
                    _mp_gn,
                    body_verts_for_p2,
                    np.asarray(ube_base_for_pass1.tris,
                               dtype=np.int64).reshape(-1, 3),
                    body_norms_for_p2,
                    is_chain=_mp_chain)
                if _mp_st.get("moved"):
                    # Apply only the DISPLACEMENT, added back onto the array the
                    # rest of phase 2 owns. Never assign the aligned-frame verts
                    # directly: if the no-offset frame won, doing so would drop
                    # `_off_p2` and the later `override - _off_p2` would shift the
                    # shape by the offset a second time.
                    override = _mp_v + (_mp_out - _mp_base)
                    _stage("min_push", override)
                    print(f"    [min-push] {s.name}: {_mp_st['moved']} vert(s), "
                          f"exposed {_mp_st['exposed_before']} -> "
                          f"{_mp_st['exposed_after']}, max "
                          f"{_mp_st['max_push']:.2f}u, frame={_mp_lbl} "
                          f"(reach {_mp_med:.2f}u)")
            except Exception as e:
                failed.append((f"{s.name}:min-push", repr(e)))
        # STANDOFF ASSERTION -- the last point where this shape's FINISHED
        # geometry and the body are both in world space. Every push pass above
        # is bounded by its own max_push, which bounds what THAT pass adds, not
        # where the vertex ends up; several run in sequence over the same verts,
        # so the stack is jointly unbounded and nothing measured the result.
        # Clipping cannot see it -- a garment three units too far off the body
        # scores 0.0% -- and an overinflated mesh reached the user twice.
        # MEASURE ONLY: records to a JSONL beside the output, because a worker's
        # print can be discarded outright in the frozen windowed exe. Never
        # edits geometry.
        #
        # Same FRAME GUARD as the push above, for the same traced reason: with
        # override None the shape is still pre-warp, so a standoff number there
        # would be measured against a body the garment has not been fitted to.
        # It early-outs on hit count rather than reporting a wrong number, but
        # the honest fix is to assert where the garment is final. KNOWN GAP:
        # shapes untouched by phase-2 passes produce no record.
        # CHAIN VERIFY. Must run BEFORE the standoff record below, so that
        # telemetry describes the geometry actually shipped, and before the
        # offset is subtracted back off, because the snapshots are all in the
        # same world space the passes worked in.
        if _chain is not None:
            try:
                override, _cv = _chain.finish(override)
                if _chain.rolled_back_to is not None:
                    print(f"    [chain] {s.name}: {_cv}")
                # #seam-weld-self: after the chain has settled, while the shape
                # is still in the same world frame the passes worked in and
                # before the offset is subtracted back off. `_sv_body` is this
                # shape's SOURCE geometry in that frame, so coincidence in it is
                # exactly coincidence in the authored mesh.
                if SEAM_WELD_SELF and override is not None:
                    override, _nw = _weld_source_coincident_verts(
                        _sv_body, override)
                    if _nw:
                        _stage('seam_weld', override)
                # Un-buckle thin features the fit crumpled -- same frame, after
                # the weld. Wiring the seam weld into only ONE of these two sites
                # left 5% of the pack torn last time; both sites, both passes.
                # #coherence-repair
                if override is not None:
                    # #coherence-repair-outside-body: hand it the body so the
                    # last pass in the chain cannot undo the clearance every
                    # pass before it just established. Traced here: this pass
                    # moved a Torso vert from +0.9555 to -0.0214 and a shell
                    # vert from +0.3061 to -1.0018, in BOTH arms.
                    override, _nc = _repair_coherence_collapse(
                        _sv_body, override, s.tris,
                        body_verts=body_verts_for_p2,
                        body_normals=body_norms_for_p2)
                    if _nc:
                        _stage('coherence_repair', override)
                        print(f"    [coherence-repair] {s.name}: "
                              f"un-buckled {_nc} patch(es)")
                # COLLIDER GUARD. The sibling site wraps its repairs in
                # `_geometry_repair_allowed`; this one only checks `override is
                # not None`, so a collision proxy reaches the repairs here. FSMP
                # reads a collider's triangles and positions, and this project
                # has a history of equip CTDs from well-meant edits to them.
                # KNOWN GAP, pre-existing: `_repair_coherence_collapse` above is
                # still unguarded here. Widening THAT changes collider geometry
                # across the pack and needs its own census, so it is left alone
                # rather than quietly altered under cover of this fix.
                if override is not None and _geometry_repair_allowed(s):
                    # #strap-scale-uniform
                    override, _nsu = _uniformise_local_scale(
                        _sv_body, override, s.tris)
                    if _nsu:
                        _stage('strap_scale', override)
                        print(f"    [strap-scale] {s.name}: "
                              f"uniformised {_nsu} vert(s)")
                    # #short-edge-cap, last for the same reason as the other site.
                    override, _nse = _cap_short_edge_stretch(
                        _sv_body, override, s.tris)
                    if _nse:
                        _stage('short_edge', override)
                        print(f"    [short-edge] {s.name}: "
                              f"un-stretched {_nse} vert(s)")
                _chain.record(dst_path, s.name)
                # Per-pass STANDOFF, default OFF (CBBE2UBE_STANDOFF_TRACE=1).
                # Reads the snapshots the chain already holds, so it must run
                # before release() drops them.
                _chain.trace_standoff(dst_path, s.name, override)
            except Exception as e:
                # RECORDED. A bare `pass` here meant a shape whose verification
                # FAILED looked exactly like one that verified clean -- the only
                # tell was a missing chain record, which nothing checks. Same
                # class of blindness that hid the conform skip for a full day.
                failed.append((f"{s.name}:chain-verify", repr(e)))
            finally:
                _chain.release()      # snapshots of a torso are not small
        # DISPLACEMENT SURVIVAL, default OFF (CBBE2UBE_SURVIVAL_TRACE=1). Must
        # run AFTER the chain verify: a pass whose work a rollback discarded has
        # not survived either, and `override` only names the SHIPPED geometry
        # once finish() has had its say. Same world space as the snapshots, and
        # before the offset is subtracted back off, for the same reason.
        if _surv is not None:
            try:
                _surv.flush(dst_path, s.name, override)
            except Exception as e:
                # RECORDED, never swallowed. A silent failure here reads as
                # "no pass was cancelled", which is the exact wrong conclusion.
                failed.append((f"{s.name}:survival", repr(e)))
            finally:
                _surv.release()
        if _dump is not None:
            try:
                if not _dump.flush(dst_path, s.name, s.tris, override):
                    # An EMPTY dump reads exactly like "no pass moved
                    # anything". Say which it was.
                    failed.append((f"{s.name}:stagedump", "wrote nothing"))
            except Exception as e:
                failed.append((f"{s.name}:stagedump", repr(e)))
            finally:
                _dump.release()
        if _tracer is not None:
            try:
                _tracer.flush(dst_path, s.name)
            except Exception:
                pass
        # THE AUDIT'S OFF-SWITCH HAS TO REACH THE EXPENSIVE PART.
        # `record_standoff` and `record_torso_bands` both check the audit flag
        # and return immediately -- but the cast that FEEDS them was built
        # first and unconditionally, so `CBBE2UBE_NO_STANDOFF_AUDIT=1` skipped
        # only the cheap half and bought nothing. Profiled on a five-layer
        # piece: `_TorsoCast.__init__` is 43.7s of a 179.7s conversion -- 24%
        # of the run and 61% of ALL its ray casting -- spent purely on
        # telemetry the flag claimed to have disabled. Gate it where the cost
        # is, not only where the write is.
        # `_band_enabled()`, not `_enabled()`: this branch is the RAY CASTING,
        # and it is 17.5% of a conversion while the records it feeds are a tenth
        # of the sink and have no reader in the shipping pipeline. The cheap
        # record kinds keep their own gate and stay on. #standoff-band-audit
        if (override is not None and body_verts_for_p2 is not None
                and body_norms_for_p2 is not None
                and fit_metrics._band_enabled()):
            try:
                _ov = np.asarray(override, dtype=np.float64)
                _tr = np.asarray(s.tris, dtype=np.int64).reshape(-1, 3)
                # ONE cast for both records below. They run on the same
                # geometry at the same moment with the same tmax, over ray sets
                # that overlap heavily -- five casts of the same garment where
                # one will do. Rays are independent, so slicing the union is
                # arithmetically identical.
                _cast = fit_metrics._TorsoCast(
                    _ov, _tr, body_verts_for_p2, body_norms_for_p2)
                fit_metrics.record_standoff(
                    dst_path, s.name, _ov, _tr,
                    body_verts_for_p2, body_norms_for_p2, cast=_cast)
                # Additive: the calibrated bust record above is unchanged. This
                # covers the REST of the torso -- under-bust through strap line
                # -- which no pack-wide record has ever measured, and which is
                # where a gap reported in game turned out to live.
                fit_metrics.record_torso_bands(
                    dst_path, s.name, _ov, _tr,
                    body_verts_for_p2, body_norms_for_p2, cast=_cast)
            except Exception:
                # telemetry must never fail a conversion; the module records
                # its own exceptions to the sink, so a broken measurement is
                # visible there rather than silently absent
                pass
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
                    # An SMP COLLIDER keeps its authored skin, full stop. Grafting
                    # scale bones onto one makes it deform with the body instead of
                    # standing still, and the cloth it is meant to be a STABLE
                    # collider for then has a moving floor. Measured on a leg-plate
                    # collider: 6 scale bones the source has ZERO of, taking weight
                    # off Thigh (-2.16), Calf (-1.18) and Pelvis (-0.57). Every other
                    # skin pass already honours this set; this one never asked.
                    # #smp-collider-graft
                    if (ADD_SCALE_BONES_TO_CLOTH
                            and s.name not in hdt_collider_names
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
    variant_sources: "dict[str, str] | None" = None,
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
    # Seed the physics-chain ANCHOR bones at their SOURCE GLOBAL while the NIF is
    # still EMPTY. Whoever adds them first wins: the injected UBE BaseShape's skin
    # adds every skeleton bone flat at IDENTITY, and after that nothing can move
    # them -- `_precreate_custom_bone_chains`' flat branch skips a node that
    # already exists, and pynifly exposes NO node-transform setter that survives a
    # save (measured: assignment changes memory, the written file is unchanged).
    # Left uncorrected, the anchor sits at the origin, every chain bone parented
    # onto it keeps its source-LOCAL transform (which assumed a parent at
    # pelvis/COM height), and the whole rig lands ~69u low -- at floor level --
    # so the cloth hangs through the ground and slowly collapses. #anchor-global-fix
    try:
        _seed_flat_chain_anchors(dst_nif, src_nif)
    except Exception as _pe:
        _note_pass_failure("_seed_flat_chain_anchors", _pe)

    # BODYTRI path: use a pre-built armor TRI if found (has _ForOutfits slider
    # bridges for RaceMenu), otherwise fall back to the body TRI.
    armor_relpath = armor_relpath_under_meshes(src_path)
    body_tri_path = UBE_BODY_TRI_PATH
    # Always auto-generate the armor TRI from CBBE source + UBE body
    # OSD slider data (see module-level UBE_BODY_TRI_PATH note).
    auto_tri_dst: Path | None = None  # if set, write generated TRI here
    # Set inside the branch below; initialised here because the generation gate
    # far downstream reads it unconditionally.
    _tri_write_this_variant = True
    if armor_relpath is not None and auto_gen_tri:
        tri_stem = dst_path.stem
        for suf in ("_0", "_1"):
            if tri_stem.endswith(suf):
                tri_stem = tri_stem[:-len(suf)]
                break
        auto_tri_dst = dst_path.parent / (tri_stem + ".tri")
        # #tri-write-once. The stem drops the weight suffix, so `x_0.nif` and
        # `x_1.nif` derive the SAME `x.tri` -- BOTH variants generate it and both
        # write it, on every armour pair in the pack. Two consequences, both
        # measured on a 161-mod reconvert:
        #
        #   * A RACE. With a worker pool the two writers collide on the atomic
        #     rename; 24 writes lost it, and 4 pieces shipped with a missing or
        #     stale TRI (they then do not follow body sliders at all).
        #   * NONDETERMINISM, which is the worse half and was invisible. The two
        #     variants do NOT produce the same file -- on one boot, 17 of 24
        #     morphs differ, worst 0.171u per vertex -- so which BODYTRI ships
        #     has been decided by thread timing.
        #
        # Generate it ONCE, from the variant `_tri_is_owning_variant` picks --
        # which is `_0`, MEASURED, not the `_1` this comment used to claim:
        # shipping the `_1`-derived TRI put a nipple through a leather cuirass
        # in game within hours (92/155 morphs differ on a bust shape, worst
        # 2.16u; the full measurement lives on that function). A piece with no
        # weight partner keeps generating from whatever it has, so
        # single-variant armour is unaffected. Also halves TRI work pack-wide.
        # `CBBE2UBE_TRI_BOTH_WEIGHTS=1` restores the old racing behaviour.
        #
        # Suppress only the WRITE, and only AFTER `body_tri_path` is derived
        # below: the `_0` mesh must keep pointing at the shared TRI or it loses
        # body morphs outright, which is worse than the race. (Nulling the path
        # here instead would also have crashed on the very next line.)
        _tri_write_this_variant = (
            (not _TRI_WRITE_ONCE)
            or _tri_is_owning_variant(src_path, variant_sources))
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

    # #authored-shape-order (BUG-09). The UBE body used to be COPIED HERE --
    # before a single authored shape was written -- so it took shape index 0 and
    # shifted every authored shape by +1. An ARMA `AlternateTextures` entry
    # binds by INDEX, not by name, so that silently re-pointed every colour
    # variant in the load order: the swap landed on the injected body (breaking
    # the actor's skin texture) while the garment kept its default texture.
    # Reported in game on a white top whose body texture broke at the same time.
    #
    # The COPY now happens AFTER pass 2, so every authored shape keeps the index
    # it had in the source and the body lands LAST -- which is what the author's
    # own mesh and every hand-made UBE conversion already do.
    #
    # Only the PRECONDITION is checked here: an unusable UBE reference must
    # still fail fast, before ~1600 lines of fitting work, not after it.
    injected: list[str] = []
    _injectable = [
        s.name for s in ube_nif.shapes
        if s.name in body_inject_names
        and not (s.name == "BaseShape" and not inject_baseshape)
    ]
    if not _injectable and inject_baseshape:
        # Only a FAILURE when we actually wanted to inject the UBE body (slot-32
        # body armor). For a non-body slot (inject_baseshape=False -- boots/
        # panties/underwear whose inline CBBE body we DROP, letting the actor's
        # own nude UBE body show), an empty set is EXPECTED: we're not
        # replacing the body, just removing the stray one and copying the armor.
        # Without this gate, routing a "3BA Ref"-body piece here (see
        # #3ba-ref-body) skips the whole conversion -> the CBBE body survives.
        return ConvertResult(
            src_path=src_path, dst_path=None,
            status="skipped",
            reason=f"UBE ref {ube_body_ref_path.name} has no shapes in "
                   f"{body_inject_names}",
        )

    # Hands/Feet NOT injected: slot 33/37 ARMAs stay live alongside slot 32.
    # UBE_AllRace.esp already routes those slots to UBE meshes; injecting
    # them here would duplicate geometry and cause z-fight.

    # BODYTRI attached to the first armor shape (not BaseShape), mirroring
    # hand-authored UBE NIFs where NioOverride morphs all TRI shapes via
    # an armor-shape carrier. Added after armor shapes are copied below.

    # Build body MeshIndexes for the armor-fit pass (if enabled + refs available).
    # CBBE body: inline shape from source or cbbe_body_ref_path fallback.
    cbbe_idx = ube_idx = None
    # Source body verts+normals for standoff-preserving conform. Detection stays
    # unconditional: it does not depend on `fit_armor`, and it must still happen
    # when the conform is off, so switching `PHASE2_CONFORM` changes ONLY whether
    # the pass runs -- never what it would have seen.
    #
    # This read "the conform runs regardless of `fit_armor`" until 2026-08-23,
    # when `#phase2-conform` gave the pass a kill switch. Left alone it would
    # tell a reader the pass cannot be disabled -- the exact confusion that
    # switch exists to end, since `CBBE2UBE_NO_CONFORM` gates a DIFFERENT pass
    # of the same name.
    src_body_v_p2 = src_body_n_p2 = None
    # Hardened twin of `src_body_n_p2`, for the authored floors only. See where
    # it is filled in for why it is not simply `_SRC_NORMAL_FIX`.
    src_body_n_authored = None

    def _is_body_pynifly_shape(s):
        if s.name in BODY_SHAPE_NAMES:
            return True
        # #body-name-prefix: the SAME gate as `_looks_like_inline_body`, and for
        # the same reason. The texture test three lines below already exists to
        # stop a full-length robe being picked as the CBBE body reference; the
        # prefix shortcut was jumping over it, so a garment an author named
        # `FemaleUnderwearBody:0` could be chosen as the body the whole piece is
        # fitted against. Fixing only the strip site would leave this one -- the
        # "every fix has to land twice" shape audit F102 is about.
        name_low = (s.name or "").lower()
        for prefix in BODY_SHAPE_NAME_PREFIXES:
            if name_low.startswith(prefix) and _shape_diffuse_is_body_skin(s):
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
    if cbbe_body_shape is None and body_names:
        # TWO BODY DETECTORS DISAGREED, and the stricter one silently cost the
        # conform its reference. `classify_shapes` -> `_looks_like_inline_body`
        # already identified these shapes as the body and DROPPED them for the
        # swap; `_is_body_pynifly_shape` then refused the same shape because its
        # heuristic wants >= 40 bones. A BodySlide-output inline body carries
        # only the bones its surviving verts touch -- the two hide cuirasses ship
        # one at 26 bones -- so it fails on bone count before the texture gate is
        # even reached.
        #
        # Consequence: `src_body_v_p2` stayed None, so
        # `conform_to_source_standoff` -- the ONLY pass that reels an
        # over-projected garment back onto the body -- never ran, and nothing
        # anywhere recorded that. Measured: the affected piece sits 2.40u off the
        # body at the strap line, past the MAXIMUM (1.79u) of 42 shapes where the
        # pass did run.
        #
        # Deliberately a FALLBACK, not a replacement: on the 42 shapes where the
        # first detector already answers, this cannot change which shape is
        # picked, so it cannot move geometry that is currently correct. Largest
        # wins because `body_names` can also carry exposed-skin slices, and the
        # body proper is the biggest of them.
        _bn = set(body_names)
        _named_body = [s for s in src_nif.shapes if s.name in _bn]
        if _named_body:
            cbbe_body_shape = max(_named_body, key=lambda s: len(s.verts))
    if cbbe_body_shape is None and cbbe_body_ref_path is not None:
        cbbe_ref = nif_io.open_nif_retry(str(Path(cbbe_body_ref_path)))  # transient-IO resilient
        cbbe_body_shape = max(cbbe_ref.shapes, key=lambda s: len(s.verts)) if cbbe_ref.shapes else None
    if cbbe_body_shape is not None:
        # NOTE: this branch is OFF BY DEFAULT (see _SRC_NORMAL_FIX -- opt in with
        # CBBE2UBE_SRC_NORMAL_FIX=1). The defect below is real and confirmed, but
        # correcting it measured no better overall, so it does not ship enabled.
        # Read the rest of this comment as the RATIONALE for the opt-in, not as a
        # description of current behaviour.
        # Use the HARDENED normal fetch, not a raw length check. BodySlide output
        # routinely ships a body whose normals are all ZERO (see the same note at
        # the `_body_normals_or_compute` call further down), and a length-only
        # gate lets that straight through. Zeroed normals make every signed
        # standoff `s_src` come out 0, which tells conform_to_source_standoff the
        # source cloth was skin-tight everywhere -- so it reels LOOSE drape
        # (skirts, robes, tabards) inward instead of leaving it alone, the exact
        # opposite of that pass's stated contract. Measured on a real modlist:
        # 18 of 21 sampled inline bodies had zeroed normals.
        # `_body_normals_or_compute` verifies the normals are populated, unit-
        # normalises them, and falls back to computing them from the triangles.
        _sbn = (_body_normals_or_compute(cbbe_body_shape)
                if _SRC_NORMAL_FIX else getattr(cbbe_body_shape, "normals", None))
        if _sbn is not None and len(_sbn) == len(cbbe_body_shape.verts):
            src_body_v_p2 = np.asarray(cbbe_body_shape.verts, dtype=np.float64)
            src_body_n_p2 = np.asarray(_sbn, dtype=np.float64)
        # The two AUTHORED FLOORS get the hardened normals regardless of
        # `_SRC_NORMAL_FIX`, in their own variable. That gate is off because
        # flipping it moves ~20% of verts modlist-wide through `conform`, whose
        # constants were all tuned with the zeroed normals in play -- a real
        # reason to keep it, and no reason at all to hand a floor an input it
        # cannot read. Without this the floors see `authored == 0` on every
        # body-swap piece (18436 of 18436 zero-length on this pack's source
        # body) and either refuse or, before `_authored_normals_usable`,
        # silently degrade. `conform` still reads `src_body_n_p2` as it did.
        #
        # Skipped entirely when neither floor is armed, so the OFF path does not
        # even pay for the recompute -- it is a normals-from-triangles pass over
        # an 18k-vert body, per piece, per weight.
        if AUTHORED_INFLATE or AUTHORED_ANTIPOKE:
            _sbn_authored = _body_normals_or_compute(cbbe_body_shape)
            if (_sbn_authored is not None
                    and len(_sbn_authored) == len(cbbe_body_shape.verts)):
                src_body_n_authored = np.asarray(_sbn_authored, dtype=np.float64)

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
    _rebury_motion_w = None
    if ube_base_for_pass1 is not None:
        body_verts_for_p2 = np.asarray(
            ube_base_for_pass1.verts, dtype=np.float64)
        # Compute normals from tris when the body NIF ships none/zeroed (common
        # for BodySlide output) -- else the conform/standoff passes that push
        # along the body normal silently no-op (#175). _body_nipple_weight gives
        # the bust pass its Breast03 nipple localization.
        body_norms_for_p2 = _body_normals_or_compute(ube_base_for_pass1)
        body_nipple_for_p2 = _body_nipple_weight(ube_base_for_pass1)
        # ALL breast bones, not the tip: #rebury-authored must not reclaim the
        # standoff the bust SWINGS through, and the flank is where it punched
        # out. See _body_breast_motion_weight for why the nipple map is wrong here.
        if REBURY_AUTHORED:
            _rebury_motion_w = _body_breast_motion_weight(ube_base_for_pass1)
    else:
        body_verts_for_p2 = None
        body_norms_for_p2 = None

    # Body-delta warp: prefer the principled per-vert CBBE -> UBE
    # delta over the snap heuristic when both 18k-vert bodies are
    # available. See `warp_armor_by_body_delta`.
    weight_suf_p2 = weight_suffix_of(src_path)
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
    _fit_shapes_swap(_types.SimpleNamespace(
        _layer_extra=_layer_extra,
        _rebury_motion_w=_rebury_motion_w,
        biped_slots=biped_slots,
        body_delta_for_warp_p2=body_delta_for_warp_p2,
        body_names=body_names,
        body_nipple_for_p2=body_nipple_for_p2,
        body_norms_for_p2=body_norms_for_p2,
        body_verts_for_p2=body_verts_for_p2,
        cbbe_idx=cbbe_idx,
        cbbe_verts_for_warp_p2=cbbe_verts_for_warp_p2,
        dst_path=dst_path,
        failed=failed,
        hdt_collider_names=hdt_collider_names,
        hdt_softbody_names=hdt_softbody_names,
        layered_cloth_names=layered_cloth_names,
        preset_template_verts=preset_template_verts,
        preset_user_verts=preset_user_verts,
        reskin_armor=reskin_armor,
        reskin_far_dist=reskin_far_dist,
        reskin_k=reskin_k,
        reskin_near_dist=reskin_near_dist,
        shape_jobs=shape_jobs,
        skipped_collision=skipped_collision,
        src_body_n_authored=src_body_n_authored,
        src_body_n_p2=src_body_n_p2,
        src_body_v_p2=src_body_v_p2,
        src_morph_shapes=src_morph_shapes,
        src_nif=src_nif,
        ube_base_for_pass1=ube_base_for_pass1,
        ube_body_ref_path=ube_body_ref_path,
        ube_idx=ube_idx,
    ))

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
        except Exception as _pe:
            # RECORDED. This exact pass was once a SILENT NO-OP in phase 2 (the
            # call site had stopped passing `cbbe_body_verts`) and it read as
            # "no band qualified" for as long as it lasted.
            _note_pass_failure("_separate_abdomen_layered_cloth_depth", _pe)

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
        except Exception as _pe:
            # wraps BOTH the chest and abdomen sync -- either can raise
            _note_pass_failure("_sync_layered_cloth_weights", _pe)

    # Layer ride: the per-shape fit passes above displaced each layer by its OWN
    # distance to the body, so a stacked garment's layers drift apart/through each
    # other. Re-place them coherently (innermost keeps its fit; each layer above
    # rides what's beneath it, preserving source offsets). See _ride_layers_on_reference.
    if shape_jobs:
        try:
            # #panel-rigid-ride needs the WHOLE placed stack to judge a panel
            # against, and the ride only ever has the layers BENEATH the one it
            # is working on. So ride TWICE: once plain, to get a complete
            # baseline placement, then again with panels enabled and each panel
            # judged against that baseline. Two runs of a pass that costs ~0.5%
            # of the conversion; the alternative is a panel that punches into an
            # outer layer with nothing able to see it.
            _stack = None
            if PANEL_RIGID_RIDE:
                _snap = {}
                try:
                    for _j in shape_jobs:
                        _v = _j.get("verts")
                        if _v is not None:
                            _snap[id(_j)] = (np.array(_v, copy=True),
                                             _j.get("verts_modified", False))
                    _ride_layers_on_reference(
                        shape_jobs, body_verts=body_verts_for_p2,
                        softbody_names=hdt_softbody_names,
                        collider_names=hdt_collider_names,
                        body_norms=body_norms_for_p2, enable_panels=False)
                    _stack = _stack_probe(
                        shape_jobs,
                        {j["src"].name: j["verts"] for j in shape_jobs
                         if j.get("verts") is not None})
                finally:
                    # RESTORE, always. Leaving the baseline ride's output in
                    # place would silently ship a double-ridden stack.
                    for _j in shape_jobs:
                        _s = _snap.get(id(_j))
                        if _s is not None:
                            _j["verts"], _j["verts_modified"] = _s[0], _s[1]
            n_ride_l = _ride_layers_on_reference(
                shape_jobs, body_verts=body_verts_for_p2,
                softbody_names=hdt_softbody_names,
                collider_names=hdt_collider_names,
                body_norms=body_norms_for_p2, stack=_stack)
            if n_ride_l:
                import sys as _sys
                print(f"  layer ride: re-placed {n_ride_l} vert(s) on the layer "
                      f"beneath them (coherent stack)", file=_sys.stderr)
        except Exception as _pe:
            # RECORDED: failure leaves each layer independently warped, which
            # is indistinguishable on disk from a stack that needed no ride.
            _note_pass_failure("_ride_layers_on_reference", _pe)

    # Layer-ORDER repair: runs AFTER the ride (and every other vertex pass) so it
    # corrects whatever any of them got wrong -- a vert that ended up on the wrong
    # side of a neighbouring layer is pushed back to its authored side. This is the
    # pass that targets the VISIBLE "layers clipping into each other" artifact.
    # See _repair_layer_order / #layer-order.
    if shape_jobs:
        try:
            n_ord = _repair_layer_order(
                shape_jobs, softbody_names=hdt_softbody_names,
                collider_names=hdt_collider_names)
            if n_ord:
                import sys as _sys
                print(f"  layer order: restored {n_ord} vert(s) to their source "
                      f"side of a neighbouring layer", file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_repair_layer_order", _pe)

    # Degenerate-triangle repair: prior passes can pinch thin tris flat -> black
    # slivers. Restore collapsed tris to source-relative shape; source-degenerate
    # folds are left alone. Run TWICE: once after the warp/inflate/conform passes,
    # and again after the seam-weld/glow-ride below -- the seam-weld SNAPS verts
    # (welding a detail shape's own verts to one centroid collapses its tris, e.g.
    # a cuirass 'Top Stiches' band) AFTER this first pass, so a single early repair
    # misses those. #post-weld-degenerate-repair
    def _degenerate_repair_pass(_tag: str) -> None:
        if not shape_jobs:
            return
        _nfix_tot = 0
        _nshapes = 0
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
                    _nfix_tot += _nfix
                    _nshapes += 1
            except Exception as _e:
                # best-effort; a failed repair leaves the shape as-is -- but
                # recorded, so a repair failing on every shape is visible.
                _note_pass_failure("repair_collapsed_tris/postrelax", _e)
        if _nfix_tot:
            import sys as _sys
            print(f"  degenerate-tri repair ({_tag}): un-pinched {_nfix_tot} "
                  f"collapsed tri(s) across {_nshapes} shape(s)", file=_sys.stderr)

    _degenerate_repair_pass("warp")

    # Cross-plate seam weld: close gaps where adjacent solid plates that share
    # a seam drifted apart under independent warp. Runs BEFORE the glow ride so
    # the glow rides the welded plate. Physics shapes excluded (see phase 1 --
    # the small inner try keeps a scoping error from killing the weld).
    if shape_jobs:
        try:
            try:
                _weld_excl = (set(hdt_collider_names)
                              | set(hdt_softbody_names)
                              | set(layered_cloth_names))
            except Exception:
                _weld_excl = set()
            n_weld = _weld_cross_shape_seams(shape_jobs,
                                             exclude_names=_weld_excl)
            if n_weld:
                import sys as _sys
                print(f"  seam weld: closed {n_weld} cross-plate seam vert(s)",
                      file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_weld_cross_shape_seams", _pe)

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
        except Exception as _pe:
            _note_pass_failure("_ride_effect_overlays_on_plate", _pe)

    # Genuine LAST vertex op: catch tris the seam-weld/glow-ride snaps collapsed
    # after the first repair (e.g. a detail band welded to a shared centroid).
    # #post-weld-degenerate-repair
    _degenerate_repair_pass("post-weld")

    # #layer-order-last. The three per-shape geometry repairs also run inside
    # `_copy_shape`, i.e. at WRITE time, after everything here -- so on phase 2
    # they run twice and the second run is the last thing to touch a vertex.
    # It moves 74% of a belt's vertices by up to 0.600u, which is what undoes
    # the cross-shape reconciliation: `_repair_layer_order` leaves 669
    # wrong-side verts (103 for top-inside-belts) and 1068 (240) ship.
    #
    # But that second run is NOT redundant, which is the whole difficulty:
    # simply suppressing it recovers the layer order (top-inside-belts
    # 248 -> 168) and costs surface quality across the board (neighbour-step
    # edges 4205 -> 4806, dihedral mean 10.56 -> 11.37, edge deviation
    # 0.0814 -> 0.0868), because the ride and order passes CREATE crumple that
    # it is there to repair.
    #
    # So do both, in the only order that can satisfy both: run the repairs
    # HERE, on the post-cross-shape geometry, and then re-run the order repair
    # so the relationship has the last word -- the same rule the groove smooth
    # follows. `_copy_shape` is then told to skip them, so nothing moves a
    # vertex after this.
    if LAYER_ORDER_LAST and shape_jobs:
        for j in shape_jobs:
            try:
                _s = j["src"]
                if not _geometry_repair_allowed(_s):
                    continue
                _sv = np.asarray(_s.verts, dtype=np.float64)
                _ov = np.asarray(j["verts"], dtype=np.float64)
                if _ov.shape != _sv.shape:
                    continue
                for _fn in (_repair_coherence_collapse, _uniformise_local_scale,
                            _cap_short_edge_stretch):
                    # Only the coherence repair takes the body today
                    # (#coherence-repair-outside-body); the other two keep the
                    # bare signature, so the kwargs go in per function rather
                    # than to all three.
                    _kw = ({"body_verts": body_verts_for_p2,
                            "body_normals": body_norms_for_p2}
                           if _fn is _repair_coherence_collapse else {})
                    _ov2, _n = _fn(_sv, _ov, _s.tris, **_kw)
                    if _n:
                        _ov = _ov2
                        j["verts"] = _ov
                        j["verts_modified"] = True
            except Exception as _pe:
                # RECORDED: a silent failure here is indistinguishable from
                # "nothing qualified", and it would ship the crumple the
                # suppressed copy-time run used to catch.
                _note_pass_failure("layer-order-last/geometry-repair", _pe)
        try:
            _n_ord2 = _repair_layer_order(
                shape_jobs, softbody_names=set(hdt_softbody_names),
                collider_names=set(hdt_collider_names))
            if _n_ord2:
                import sys as _sys
                print(f"  layer order (final): restored {_n_ord2} vert(s) "
                      f"after the geometry repairs", file=_sys.stderr)
        except Exception as _pe:
            _note_pass_failure("_repair_layer_order/final", _pe)

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
                # #layer-order-last. The three geometry repairs are wired at
                # BOTH this site and inside `_copy_shape`, deliberately, so the
                # phase-1 copy path gets them too. On a phase-2 piece that
                # means they run TWICE on the same geometry -- and the second
                # run happens at WRITE time, after the cross-shape chain, so it
                # is the last thing to touch the verts. Traced execution order:
                #     [coherence, strap-scale, short-edge] x5 shapes
                #     _ride_layers_on_reference -> _repair_layer_order
                #     _weld_cross_shape_seams
                #     [coherence, strap-scale, short-edge] x5 shapes   <- again
                # The second group moves 74% of the belt's vertices by up to
                # 0.600u, which is what undoes the layer reconciliation:
                # `_repair_layer_order` leaves 669 wrong-side verts (top-inside-
                # belts 103) and 1068 (240) ship.
                #
                # A pass that RESTORES A RELATIONSHIP has to have the last word
                # on position, for the same reason the groove smooth does. So
                # skip the repairs here on phase 2, where they have already run.
                skip_geometry_repair=LAYER_ORDER_LAST,
            )
            copied.append(s.name)
            if first_armor_shape is None:
                first_armor_shape = new_armor
        except Exception as e:
            # Copy failed -> the shape is absent from the output (invisible
            # piece). Tag DROPPED so it's reported as a partial conversion.
            failed.append((s.name, f"DROPPED (copy failed): {e!r}"))
            continue

    # #authored-shape-order (BUG-09). Inject the UBE body AFTER every authored
    # shape, so authored shapes keep the indices they had in the source and the
    # body lands last. Moved down from before pass 2 -- the precondition check
    # up there records why, and it is the only thing left at the old site.
    # Nothing between the two sites touched `dst_nif` except `_hide_virtual_body`,
    # which travels with the injection because VirtualBody only exists once this
    # has run. `_pick_bodytri_carriers` below DOES need BaseShape present, so
    # this must stay above it -- it does.
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

    # Disable VirtualBody rendering — see `_hide_virtual_body` docstring.
    _hide_virtual_body(dst_nif)

    # Attach BODYTRI via _pick_bodytri_carriers: single carrier for slot-49/no-body
    # NIFs, multi-carrier for slot-32+BaseShape NIFs so NioOverride morphs all shapes.
    # Rigid single-bone pieces follow morphs via M6 re-skin (standard skinning).
    # Falls back to first_armor_shape if the filter returns empty.
    _bodytri_err = None
    # #tri-variant-collision -- see the phase-1 guard above.
    carriers_p2 = _pick_bodytri_carriers(
        dst_nif, exclude_body=BODYTRI_CARRIER_CLOTH,
        all_cloth=(BODYTRI_ALL_SHAPES or BODYTRI_CARRIER_CLOTH))
    if not carriers_p2 and first_armor_shape is not None:
        carriers_p2 = [first_armor_shape]
    if not _tri_fits_variant(src_path, variant_sources):
        carriers_p2 = []
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
            except Exception as e:
                failed.append(("hdt-chain-check", repr(e)))
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
    # #tri-write-once: the `_0` variant of a pair does not write the shared TRI,
    # but it HAS kept `body_tri_path` pointing at it (see the derivation above).
    if not _tri_write_this_variant:
        auto_tri_dst = None
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
                if ube_basereshape is None:
                    # Non-body-slot piece (inject_baseshape=False -> we DROPPED the
                    # inline body and injected nothing, e.g. boots/panties whose
                    # "3BA Ref" body we removed): there's no BaseShape in the dst,
                    # but the armor shapes STILL need their per-armor BODYTRI so
                    # they follow body sliders. Propagate from the UBE body REF's
                    # BaseShape (same body space, just not embedded here). Without
                    # this the TRI-gen was skipped entirely -> a STALE TRI survived
                    # and the piece stopped morphing. #3ba-ref-body
                    ube_basereshape = next(
                        (x for x in ube_nif.shapes if x.name == "BaseShape"),
                        None,
                    )
                if ube_basereshape is not None:
                    body_verts_arr = np.asarray(
                        ube_basereshape.verts, dtype=np.float64)
                    (armor_shape_verts, body_in_dst,
                     armor_vert_ef) = _collect_tri_inputs(
                        dst_check, len(body_verts_arr))
                    # Carrier-first TRI (hand-authored UBE convention).
                    p2_carriers = _pick_bodytri_carriers(dst_check, exclude_body=BODYTRI_CARRIER_CLOTH)
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
                        # #pair-tri-names -- see the phase 1 call site.
                        also_named=pair_alias_map(src_path, variant_sources),
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
            generated_xml_path = _generate_hdt_xml_for_dst(dst_path, only_loose=True)
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
    _finalize_physics_and_motion_match(dst_path, src_path, biped_slots)
    # Bust-plate follow (#bust-plate-sync): a rigid bust plate under-follows the
    # jiggle cloth beneath it, so the cloth swings out through it. Runs LAST of
    # the weight passes -- ON the FINAL follow split, which the matches above only
    # now settle -- and raises the plate's breast share to the cloth's. Default
    # ON (CBBE2UBE_BUST_PLATE_SYNC=0 turns it off); a no-op that never loads the
    # NIF when off.
    try:
        n_bps = _sync_bust_plate_follow_postwrite(dst_path)
        if n_bps:
            import sys as _sys
            print(f"  bust-plate follow: raised {n_bps} plate vert(s) to the "
                  f"cloth's breast follow", file=_sys.stderr)
    except Exception as _pe:
        _note_pass_failure("_sync_bust_plate_follow_postwrite", _pe)
    # Author-relative roughness cap (#author-roughness-cap). BEFORE the
    # coincident match on purpose: this one smooths a shape's INTERIOR, that
    # one settles shape BOUNDARIES and is in-game confirmed, so it keeps the
    # last word where the two populations meet.
    try:
        n_rc = _cap_weight_roughness_to_author(dst_path, src_nif_path=src_path)
        if n_rc:
            import sys as _sys
            print(f"  roughness cap: smoothed {n_rc} vert(s) rougher than the "
                  f"author", file=_sys.stderr)
    except Exception as _pe:
        _note_pass_failure("_cap_weight_roughness_to_author", _pe)
    # Hold a reskinned layer to the author where it meets an SMP layer that KEPT
    # the author's rig (#smp-boundary-weight-hold). Placed with the other
    # author-relative passes, after the body matches, because each of those
    # pairs to the body per shape and would re-diverge this one.
    try:
        n_sb = _hold_weights_at_smp_boundary(dst_path, src_nif_path=src_path)
        if n_sb:
            import sys as _sys
            print(f"  smp-boundary hold: held {n_sb} vert(s) toward the author",
                  file=_sys.stderr)
    except Exception as _pe:
        _note_pass_failure("_hold_weights_at_smp_boundary", _pe)
    # Coincident-vertex skin unification (#coincident-skin-match). Runs after
    # EVERY weight pass on purpose: each one pairs to the body per shape, so two
    # touching verts in different shapes get different rows, and a repair placed
    # earlier would simply be re-diverged by the next pass.
    try:
        n_cs = _match_coincident_cross_shape_skin(dst_path,
                                                  src_nif_path=src_path)
        if n_cs:
            import sys as _sys
            print(f"  coincident skin: unified {n_cs} cross-shape vert(s)",
                  file=_sys.stderr)
    except Exception as _pe:
        _note_pass_failure("_match_coincident_cross_shape_skin", _pe)

    # The TRI above was generated BEFORE _finalize_hdt_physics re-imported the
    # textureless collision / physics-framework proxies, so they shipped with no
    # morph table -> at runtime the cloth morphs to the preset and its collider
    # does not, and the chain sags off it. Re-emit now that the NIF is final.
    # No-op unless a shape is actually missing. #tri-reimport-refresh
    _refresh_armor_tri_after_reimport(dst_path, auto_tri_dst)

    # Validation pass — catches subtle skinning / TRI mismatches.
    val_warnings = validate_dst_nif(
        dst_path, tri_path=auto_tri_dst if auto_tri_dst else None,
        src_path=src_path,
    )
    if val_warnings:
        joined = "; ".join(val_warnings)
        result_reason = (result_reason + "; " if result_reason else "") + joined
    # A pass that RAISED must reach the caller. The parent process cannot see
    # this worker's module state or its stderr, so `reason` is the only channel
    # -- see _PASS_FAILURES_THIS_PIECE.
    _pf = _piece_pass_failures() + _piece_pass_effects()
    if _pf:
        result_reason = ((result_reason + "; " if result_reason else "")
                         + "; ".join(_pf))

    return ConvertResult(
        src_path=src_path, dst_path=dst_path,
        status="converted (body-swap)",
        reason=result_reason,
        body_shapes=body_names,
        armor_shapes=copied,
        shape_locations={n: None for n in (injected + copied + [f for f,_ in failed])},
        dropped_shapes=[n for (n, msg) in failed if msg.startswith("DROPPED")],
    )
