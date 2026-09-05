"""The per-shape fit chain: body-delta warp (outlier clamp, shear limit, groove smoothing, local-scale uniformising), cloth inflation, source-standoff conform with its surface/chord charges and clearance field, the anti-poke, snap, collapsed-triangle repair, panel rigidity and its clearance guard, plus the knobs those passes take as signature defaults.

Split out of nif_convert.py on 2026-09-01 (split step 5). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

import sys

import numpy as np
import os

from .envflags import flag as _flag, knob as _knob
from .nif_convert_bodyrefs import (  # noqa: E402
    _CBBE_3BA_VERTS, _CBBE_BODY_NAME_HINTS, _UBE_BODY_NAME_HINTS,
    _BODYSLIDE_OUT_HINTS, _FEMBODY_REL, _BODY_DISCOVERY_CACHE,
    _MOD_DIR_LIST_CACHE, _GLOB_FIRST_MEMO,
    _iter_femalebody_nifs, _shape_has_3ba_topology, _find_cbbe_base_body,
    _find_ube_femalebody, _sorted_mod_dirs, _glob_first_in_mods,
    _find_ube_shapedata, _find_ube_template_body, _find_ube_body_osd,
    _find_user_preset_body,
)
from .nif_convert_telemetry import (  # noqa: E402
    _PASS_FAILURES, _PASS_FAILURES_THIS_PIECE, _PASS_EFFECTS,
    _PASS_EFFECTS_THIS_PIECE, _note_pass_failure, _note_pass_effect,
    _piece_pass_effects, pass_effect_summary, _begin_piece_pass_log,
    _piece_pass_failures, pass_failure_summary,
)
from .nif_convert_trigen import (  # noqa: E402
   _OSD_CACHE, _BODY_MORPH_STACK_CACHE, _BODY_MORPH_AMP_CACHE,
   _BODY_MORPH_DIFF_CACHE, _MORPH_TRI_NAME_CACHE, _MORPH_STACK_MIN,
   _MORPH_SIZE_KEYWORDS, _cached_osd_load, _cached_body_morph_stack,
   _cached_body_morph_amplitude, _cached_body_morph_differential,
   _body_array_digest, _tri_is_owning_variant, _reset_morph_flags,
   _collect_tri_inputs, _normalize_shader_for_morph, _pick_bodytri_carriers,
   _source_morph_tri_shape_names, _refresh_armor_tri_after_reimport,
   check_ube_nude_morph_files,
)


def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


CONFORM_BUST_CLEARANCE = _knob("CBBE2UBE_CONFORM_BUST_CLEAR", 0.9)

# Damping steps. Each halves the remaining motion on the offending triangles'
# vertices, so 8 steps reach 1/256 of the requested pull -- effectively zero for
# a vertex that simply cannot move without folding, while a vertex that only
# slightly overshot recovers most of its motion in the first step or two.
CONFORM_FOLD_GUARD_STEPS = _knob("CBBE2UBE_CONFORM_FOLD_GUARD_STEPS", 8, int)

# Fraction of its ENTRY signed area a triangle must keep. A plain sign test is
# not enough and the difference is not academic: stopping the moment the
# orientation is barely positive leaves the triangle pinched almost flat, and
# the float32 cast on the way out of the pass then rounds a good share of them
# straight back over the edge -- measured as the guard converging in 8 steps
# yet the shipped mesh still gaining 29 folds.
#
# Swept on the traced cuirass by the pass's OWN added folds, which is the only
# part this knob controls: 0.10 -> +17, 0.25 -> +9, 0.40 -> +3, 0.60 -> +3. It
# is a knee, not an optimum -- past 0.40 the count stops improving and the only
# thing still growing is how much authored pull-in gets thrown away.
CONFORM_FOLD_GUARD_MARGIN = _knob("CBBE2UBE_CONFORM_FOLD_GUARD_MARGIN", 0.40)

# Feather rounds over the damping field. Damping only the offending vertices
# leaves their neighbours at full motion, so the displacement field gains a
# cliff exactly where the guard acted -- which is a CRINKLE, the defect this
# project has already paid for twice (see the anti-poke push-field smoothing).
# First measurement of the unfeathered guard: armor_clip_diag reported
# max_jump 2.29u against moved_mean 0.12u, spikiness 13.4 -- its own threshold
# for "real crease, not a broad morph". Feathering spreads each vertex's damping
# into its ring, and because it only ever LOWERS a scale it cannot undo the
# guard or reintroduce a fold.
#
# ONE round. This is a standing project rule, settled by earlier testing and not
# to be re-derived per pass: extra rounds keep spreading the damping into
# geometry that was never at fault, and the measurement here agrees -- 2, 3 and
# 5 rounds all land on the same fold count as 1, while each one throws away more
# authored pull-in.
CONFORM_FOLD_GUARD_FEATHER = _knob("CBBE2UBE_CONFORM_FOLD_GUARD_FEATHER", 1, int)

# Largest area growth a single triangle may take from the warp.
WARP_SHEAR_MAX_GROWTH = _knob("CBBE2UBE_WARP_SHEAR_MAX_GROWTH", 2.0)

WARP_SHEAR_STEPS = _knob("CBBE2UBE_WARP_SHEAR_STEPS", 8, int)

# An outlier FENCE, not a tuned parameter: it sits between the mesh's own
# roughness and the fliers, and the numbers either side differ by a factor of
# four. 1.0u was the first choice, from the traced piece's p99 of 1.77u (that is
# TOTAL displacement roughness, so the delta-field tail is smaller) against 4-6u
# fliers.
#
# 0.5 IS THE DEFAULT because that is the value with evidence behind it. The GUI
# has offered 0.5 since the setting was exposed while this constant still read
# 1.0, so enabling the guard from the GUI and from the environment gave
# DIFFERENT clamps -- a silent factor-of-two disagreement in which knob you
# happened to use. The generalization test was run at 0.5 over 10 pieces the
# guard had never seen: clipping -56 verts with 0 pieces worse, standoff
# +0.0082u. Nothing was measured at 1.0, so 0.5 is the honest default and the
# two surfaces now agree. #warp-outlier-default
WARP_DELTA_OUTLIER_MAX = _knob("CBBE2UBE_WARP_DELTA_OUTLIER_MAX", 0.5)

# Stop just SHORT of the occluder, so a capped vertex rests inside its own
# shell instead of landing exactly on it and z-fighting.
WARP_PUSH_SHELL_GAP = _knob("CBBE2UBE_WARP_PUSH_SHELL_GAP", 0.1)

# ---- Anti-poke pass -----------------------------------------------------
# clear_armor_outside_body() runs after warp/inflate/conform and pushes armor
# clear of the injected UBE body. It is NOT the last vertex op: panel
# rigidity, softcloth, rebury, chain blend, min-push, seam weld and the
# cross-shape passes follow it (see docs/PASS_MAP.md's traced chain), and the
# write-time layer ride can undo it. Flat panels use FLAT_CLEAR; the breast
# front ramps up to BUST_CLEAR by nipple weight. Raise FLAT_CLEAR if the body
# still pokes on large presets, lower it for a tighter fit.
#
# ENV-TUNABLE, DEFAULT UNCHANGED (`CBBE2UBE_FLAT_CLEAR=<units>`). Lowering the
# DEFAULT would tighten every garment for every NPC preset and trade back the
# poke headroom this floor exists to buy, so it stays where it was tuned.
#
# BE WARNED THAT THIS KNOB IS INERT ON THE CASE THAT MOTIVATED IT. It was
# exposed while chasing a skin-tight BodyStock that the author holds 0.121u off
# the skin and we ship at 0.791u; 0.791 is so close to this 0.8 that the
# constant looked like the obvious culprit. MEASURED: 0.8 -> 0.25 changed that
# standoff by NOTHING. `clear_armor_outside_body` ignores this value whenever an
# amplitude map exists (see #clearance-differential below), and the stage dump
# puts the whole +0.637u on `inflate`, with anti-poke at -0.001. So this reaches
# only the no-amplitude-map path, and a chest standoff is not it.
ANTIPOKE_FLAT_CLEAR = _knob("CBBE2UBE_FLAT_CLEAR", 0.8)

# The BUST clearance TARGET, and the real lever on bust poke-through -- measured,
# not assumed. The clearance pass pushes a vert "far enough to reach the target and
# no further", so raising the push BUDGET alone changes nothing: on the traced
# cuirass, budget 1.0 -> 2.5 left min gap, %<0.5u and %<0.2u all identical and simply
# moved fewer verts further. The target is what binds.
#
# It is also the number the motion evidence indicts: the live body physics permits
# the breast chain -6.0..+3.0 units of linear travel in its largest axis, against a
# 1.0 target. Armour fitted to this target is clear of a body standing still and is
# passed straight through once the breast moves.
# Raising it costs a looser bust silhouette, so it is left at the tuned default and
# moved deliberately, in game.  CBBE2UBE_BUST_CLEAR=<units>
ANTIPOKE_BUST_CLEAR = float(
    os.environ.get("CBBE2UBE_BUST_CLEAR", "").strip() or "1.0")

ANTIPOKE_NIPPLE_GAIN = 1.5

def _is_belt_overlay(shape) -> bool:
    """True if the shape is a decorative waist belt/sash that rides ON TOP of
    the waist garment (by shape name OR diffuse texture keyword). Gets extra
    outward standoff so it clears the underlying corset/top instead of
    Z-fighting behind it. See ARMOR_INFLATION_MAGNITUDE_BELT."""
    try:
        nm = (getattr(shape, "name", "") or "").lower()
        if any(k in nm for k in _nc().BELT_OVERLAY_KEYWORDS):
            return True
        td = dict(getattr(shape, "textures", None) or {})
        d = (td.get("Diffuse") or "").lower()
        return any(k in d for k in _nc().BELT_OVERLAY_KEYWORDS)
    except Exception:
        return False

def _is_skirt_like(shape) -> bool:
    """True if the shape is skirt/hip-drape cloth (by shape name OR diffuse
    texture name). Used to give it extra outward standoff so the body
    doesn't clip through it under morph/animation."""
    try:
        nm = (getattr(shape, "name", "") or "").lower()
        if any(k in nm for k in _nc().SKIRT_INFLATION_KEYWORDS):
            return True
        td = dict(getattr(shape, "textures", None) or {})
        d = (td.get("Diffuse") or "").lower()
        return any(k in d for k in _nc().SKIRT_INFLATION_KEYWORDS)
    except Exception:
        return False

ADAPTIVE_CLEARANCE_BASE = 0.25       # minimum clearance in static zones

ADAPTIVE_CLEARANCE_MORPH_FACTOR = 0.20  # clearance added per unit of outward body morph

# Cap for the high-morph ramp. Was 0.8, which sat BELOW the fixed bust target it
# replaced (ANTIPOKE_BUST_CLEAR = 1.0), so the breast ended up with less clearance
# than before adaptive clearance existed. Measured per-zone on the UBE body (outward
# morph amplitude -> fraction of verts the cap clipped):
#     breast  amp 3.48 mean / 5.34 max -> ramp wants 0.95-1.32u -> 72% clipped
#     belly   amp 3.35 mean            -> ramp wants 0.92u      -> 50% clipped
#     sternum amp 1.66 mean            ->                          20% clipped
#     butt / back / thigh  amp <= 0.85 ->                           0% clipped
# So the cap only ever binds on the three zones the ramp exists to serve, and
# raising it cannot loosen the back, butt or thighs. 1.1 lets the ramp clear the
# 1.0 bust floor without letting the belly's outlier verts (amp up to 8.7) run to
# ~2u. Tune with CBBE2UBE_CLEARANCE_MORPH_MAX (no rebuild needed for a reconvert).
ADAPTIVE_CLEARANCE_MORPH_MAX = _knob("CBBE2UBE_CLEARANCE_MORPH_MAX", 1.1)

# Outward morph amplitude (units) at/above which a vert counts as a MORPH zone
# and keeps today's behaviour. Measured per-zone amplitudes: breast 3.48 mean,
# belly 3.35, sternum 1.66, butt/back/thigh <= 0.85.
#
# 2.0, not 1.0. At 1.0 the STERNUM (1.66) landed on the morph side and kept the
# loose treatment, which left the upper chest the worst remaining band on a
# reported armour -- authored 0.197u, shipped 0.561u, still 0.353u with the
# conform on. The sternum is not a jiggle zone: it is the flat plate between the
# breasts, and the breast's own travel is served by the bust band's push-out,
# which is applied afterwards and is untouched by this. 2.0 sits in the wide gap
# between sternum (1.66) and belly (3.35), so breast and belly keep every unit
# of room they have today and only the sternum changes sides.
STATIC_AUTHORED_AMP = _knob("CBBE2UBE_STATIC_AUTHORED_AMP", 2.0)

# How far a SKIN-HUGGING vert is reeled back toward the standoff the author gave
# it. 0.3 means seven tenths of the authored fit is deliberately not restored.
#
# Env-exposed for ABLATION, on the same grounds as ARMOR_INFLATION_MAGNITUDE: it
# is the single largest lever on "how close to the original is this", and the
# measured seat error (mean |our standoff - the author's|, 0.66u on a reported
# bodysuit) cannot be attributed without being able to move it in one command.
# The pass ledger shows the clearance knobs are NOT that lever -- halving both
# anti-poke clearances moved seat error 0.6627 -> 0.6561, and
# INFLATION_MAGNITUDE 0.7 -> 0.35 is a bit-identical no-op because conform pulls
# back whatever inflate pushes out.
CONFORM_BLEND_TIGHT = _knob("CBBE2UBE_CONFORM_BLEND_TIGHT", 0.3)

# Floor the authored fit may reach in a fully static zone. Not 0: coincident
# surfaces z-fight, and the warp's own error is not zero either.
STATIC_AUTHORED_MIN_CLEARANCE = _knob("CBBE2UBE_STATIC_AUTHORED_MIN", 0.06)

JIGGLE_CLEARANCE_GAIN = _knob("CBBE2UBE_JIGGLE_CLEARANCE_GAIN", 0.5)   # units at full jiggle weight

JIGGLE_CLEARANCE_MAX = _knob("CBBE2UBE_JIGGLE_CLEARANCE_MAX", 0.5)    # hard cap on the jiggle term

# Flat clearance floor on rear-facing verts at butt/upper-thigh height, so leg armor
# isn't punched through when the thigh swings back mid-stride. Raises below-floor
# verts only. Default on; CBBE2UBE_NO_REAR_STANDOFF=1 off.  [DESIGN: Flex-zone standoffs]
REAR_STANDOFF = _knob("CBBE2UBE_REAR_BUTT_STANDOFF", 1.0)

REAR_STANDOFF_NY = -0.15      # nearest body normal.y below this = rear-facing

REAR_STANDOFF_Z_LO = 45.0     # butt + upper-thigh band (injected UBE body coords)

REAR_STANDOFF_Z_HI = _knob("CBBE2UBE_REAR_STANDOFF_Z_HI", 80.0)  # raise to reach a belt band above the butt

# Feather widths for the zone edges -- a hard edge creases the garment where the
# floor switches off. 0 restores the old binary zone. #rear-standoff-feather
REAR_STANDOFF_FEATHER = _knob("CBBE2UBE_REAR_STANDOFF_FEATHER", 8.0)

REAR_STANDOFF_FEATHER_NY = _knob("CBBE2UBE_REAR_STANDOFF_FEATHER_NY", 0.25)

# Flat clearance floor over the lower-leg band (all-round), so calf/knee flex doesn't
# punch through leg armor. Raises below-floor verts only. Default on;
# CBBE2UBE_NO_CALF_STANDOFF=1 off.  [DESIGN: Flex-zone standoffs]
CALF_STANDOFF = _knob("CBBE2UBE_CALF_STANDOFF", 0.6)

CALF_STANDOFF_Z_LO = 20.0     # lower-leg band (above the ankle/boot line)

CALF_STANDOFF_Z_HI = 46.0     # up to just below the knee

# ALL-ROUND thigh standoff (default off). A modest uniform floor over the thigh so tight
# leg armor sits just outside the body on EVERY side -- unlike the rear-only REAR_STANDOFF,
# which lifts only the back and (cranked high) shoves that side into an over-skirt while the
# front still shows skin. Keep it modest: enough to clear the body, low enough to stay under
# a hip skirt/tasset layer. CBBE2UBE_THIGH_STANDOFF=<u>.  [DESIGN: Flex-zone standoffs]
THIGH_STANDOFF = _knob("CBBE2UBE_THIGH_STANDOFF", 0.0)

THIGH_STANDOFF_Z_LO = _knob("CBBE2UBE_THIGH_STANDOFF_Z_LO", 55.0)  # lower to reach the mid/inner thigh

THIGH_STANDOFF_Z_HI = 78.0    # up to the hip (below the butt-crest)

ANTIPOKE_SMOOTH_ITERS = 2

def _smooth_warp_grooves(src_world, warped, ube_body_verts,
                         ube_body_normals=None,
                         src_body_verts=None, src_body_normals=None):
    """Flatten warp-induced displacement grooves on body-conforming armor.

    The per-vert body-delta warp can introduce localized roughness in the
    CBBE->UBE displacement field where tight armor stretches over the larger
    UBE bust — visible as 'indent lines' on the breast/chest. This does a
    roughness-weighted Laplacian smooth of the DISPLACEMENT (warped - source),
    gated to verts close to the body, so genuine drape on loose/decorative
    geometry (far from the body) and already-smooth regions are left alone.
    Returns the (possibly) smoothed warped verts.

    ONE-SIDED SINCE 2026-07-29: a vert may be smoothed along the surface or
    AWAY from the body, never TOWARD it. Measured cause, not a precaution.

    A per-pass trace over 9 phase-2 pieces (292 measurements) found this pass
    regressed bust fit on 13 of 42 shapes -- net +1052 exposed verts, worst
    +394 -- and improved fit ZERO times. Reproduced in isolation on a
    farm-clothes torso: 31 -> 161 exposed (+130).

    Why: it smooths the DISPLACEMENT field, and over a convex feature that field
    PEAKS at the apex (UBE's bust is larger than CBBE's, so the apex travels
    furthest). Laplacian smoothing flattens a peak, pulling the garment back
    onto the skin. The weighting made it worse -- `rough` is largest exactly at
    the apex, so `wt` saturates there and the pass smoothed HARDEST where
    flattening does the most damage. On the newly-exposed verts: displacement
    reduced by a mean 0.191u, pre-smooth roughness a median 0.254u against
    0.140u elsewhere (1.8x, at the full-weight cap). Nothing downstream restored
    it: the pass knew only "is this vert near the body", never "did I just move
    it toward the body".

    Removing ONLY the inward component keeps the tangential and outward motion,
    which is what actually flattens a groove. Set CBBE2UBE_GROOVE_ONESIDED=0 to
    restore the old behaviour for comparison.
    """
    if not _nc().GROOVE_SMOOTH_ENABLED:
        return warped
    try:
        from scipy.spatial import cKDTree
        src = np.asarray(src_world, dtype=np.float64)
        w = np.asarray(warped, dtype=np.float64)
        if len(src) != len(w) or len(src) < 12:
            return warped
        disp = w - src
        body = (np.asarray(ube_body_verts, dtype=np.float64)
                if ube_body_verts is not None and len(ube_body_verts) else None)
        if body is not None:
            btree = cKDTree(body)
            d2b, nn0 = btree.query(w, k=1)
            active = (d2b < _nc().GROOVE_SMOOTH_CLOSE).astype(np.float64)[:, None]
        else:
            nn0 = None
            active = np.ones((len(src), 1), dtype=np.float64)
        if not active.any():
            return warped
        _, idx = cKDTree(src).query(src, k=9)
        nbr = idx[:, 1:]
        for _ in range(_nc().GROOVE_SMOOTH_ITERS):
            nm = disp[nbr].mean(axis=1)
            rough = np.linalg.norm(disp - nm, axis=1)
            wt = np.clip(rough / _nc().GROOVE_SMOOTH_ROUGH, 0.15, 1.0)[:, None]
            disp = disp + active * (0.6 * wt) * (nm - disp)
        out = src + disp
        if (_nc().GROOVE_ONESIDED or _nc().GROOVE_AUTHORED_CAP) and body is not None:
            # Outward direction at each vert. Prefer the body's own normals: for
            # tight armour `vert - nearest_body_vert` is near zero and
            # normalising it amplifies float noise into a random direction (the
            # documented failure that made inflate_armor_outward crumple a
            # corset). Fall back to the position difference only when no normals
            # were supplied.
            nrm = None
            if ube_body_normals is not None:
                bn = np.asarray(ube_body_normals, dtype=np.float64)
                if bn.shape == body.shape:
                    nrm = bn[nn0]
            if nrm is None:
                d = out - body[nn0]
                ln = np.linalg.norm(d, axis=1, keepdims=True)
                nrm = np.divide(d, np.where(ln > 1e-6, ln, 1.0))
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            nrm = np.divide(nrm, np.where(ln > 1e-9, ln, 1.0))
            if _nc().GROOVE_ONESIDED:
                move = out - w                   # what smoothing did, total
                along = np.einsum("ij,ij->i", move, nrm)
                inward = np.minimum(along, 0.0)[:, None]
                out = out - inward * nrm         # cancel only the inward part
            # #groove-authored-cap: bound the OUTWARD motion at the authored
            # standoff so this pass can no longer hand back the clearance the
            # conform just removed. Restricts only; never adds motion, so the
            # GROOVE_ONESIDED guarantee above is unaffected.
            if _nc().GROOVE_AUTHORED_CAP and src_body_verts is not None:
                sbv = np.asarray(src_body_verts, dtype=np.float64)
                sbn = (np.asarray(src_body_normals, dtype=np.float64)
                       if src_body_normals is not None else None)
                if (sbn is not None and sbn.shape == sbv.shape and len(sbv)
                        and len(sbv[0]) == 3):
                    _, si = cKDTree(sbv).query(src, k=1)
                    # authored clearance, measured the same way the conform
                    # measures it (signed, along the SOURCE body's normal)
                    s_auth = ((src - sbv[si]) * sbn[si]).sum(axis=1)
                    s_in = ((w - body[nn0]) * nrm).sum(axis=1)
                    s_out = ((out - body[nn0]) * nrm).sum(axis=1)
                    cap = np.maximum(s_in, s_auth)
                    excess = np.maximum(s_out - cap, 0.0)
                    # Feather the subtraction over the SAME neighbourhood the
                    # smoothing above used. A raw per-vert shove is itself a
                    # crinkle (the lesson `_LAYER_ORDER_SMOOTH` records), and
                    # nothing runs after this pass to clean one up.
                    for _ in range(_nc().GROOVE_CAP_FEATHER):
                        excess = 0.5 * (excess + excess[nbr].mean(axis=1))
                    # Back off where the normal field itself is incoherent. The
                    # cap subtracts ALONG `nrm` (the nearest body vert's
                    # normal), and in a body fold -- armpit, inner thigh,
                    # cleavage -- adjacent verts snap to opposite walls, so that
                    # direction flips between neighbours (measured: 100-179deg
                    # between adjacent moves on a sleeve). Subtracting along a
                    # direction that flips IS a crease, and no amount of
                    # feathering the scalar fixes a discontinuous direction.
                    # `coh` is the length of the averaged neighbour normals: ~1
                    # on smooth surface, -> 0 where they oppose. Scaling by it
                    # only ever REDUCES the correction, so the fold keeps
                    # today's behaviour instead of gaining a new crease.
                    coh = np.linalg.norm(nrm[nbr].mean(axis=1), axis=1)
                    excess = excess * np.clip(coh, 0.0, 1.0)
                    # Feathering must not push a vert PAST where it arrived:
                    # clamped to the outward motion the smoothing actually made,
                    # so the vert lands in [s_in, s_out] and GROOVE_ONESIDED's
                    # "never toward the body" guarantee is preserved exactly.
                    excess = np.clip(excess, 0.0,
                                     np.maximum(s_out - s_in, 0.0))
                    out = out - excess[:, None] * nrm
        return out
    except Exception as _e:
        _note_pass_failure("_smooth_warp_grooves", _e)
        return warped

def _cap_push_at_own_shell(verts, tris, dirs, push,
                          gap=WARP_PUSH_SHELL_GAP, tmax=8.0):
    """Cap each standoff push so a vertex never emerges through its own garment.

    For every vertex with a pending push, cast a ray from it along the push
    direction against THIS garment's triangles. A finite hit means there is
    more garment in front of the vertex: it is not a place skin can show, so
    the push is capped at (hit distance - `gap`). No hit means the vertex is
    genuinely exposed and the push is left exactly as it was -- which is what
    makes this unable to reopen clipping.

    The ray origin is nudged `gap` along the direction so the vertex's OWN
    incident triangles, which it lies on, are behind the origin and cannot
    register as an occluder at t~0.

    Returns (push, n_capped).
    """
    from src import fit_metrics as _fm
    p = np.array(push, dtype=np.float64, copy=True)
    t = np.asarray(tris, dtype=np.int64)
    if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0:
        return p, 0
    idx = np.flatnonzero(p > 1e-6)
    if not len(idx):
        return p, 0
    V = np.asarray(verts, dtype=np.float64)
    D = np.asarray(dirs, dtype=np.float64)[idx]
    dl = np.linalg.norm(D, axis=1)
    ok = dl > 1e-9
    if not ok.any():
        return p, 0
    idx, D, dl = idx[ok], D[ok], dl[ok]
    D = D / dl[:, None]
    O = V[idx] + D * float(gap)

    tester = _fm._ClipTester(V, t, tmax=float(tmax))
    hit = _fm.cast_chunked(tester, O, D, finite_only=False)
    hit = np.asarray(hit, dtype=np.float64)
    if len(hit) != len(idx):
        return p, 0                      # alignment lost -> change nothing
    finite = np.isfinite(hit)
    if not finite.any():
        return p, 0
    # `hit` is measured from the NUDGED origin, so the vertex-to-occluder
    # distance is (gap + hit). Stopping `gap` short of the occluder therefore
    # allows exactly (gap + hit) - gap == hit.
    allowed = np.maximum(hit[finite], 0.0)
    target = np.minimum(p[idx[finite]], allowed)
    capped = int((target < p[idx[finite]] - 1e-9).sum())
    p[idx[finite]] = target
    return p, capped

def _cap_short_edge_stretch(src_verts, out_verts, tris):
    """A pair of verts the author placed 0.02u apart may not end up 0.4u apart.
    #short-edge-cap

    REPORTED IN GAME, three times, on the metal fittings of a belt. Found only
    after the edge-distortion metric was checked for contamination: 28 of the
    buckle's 2938 edges are under 0.05u in the author's mesh, and the fit stretched
    the worst of them 0.0229u -> 0.4138u, an 18x blow-up, all clustered at
    z 86.1-87.3 -- the band reported. The leather beside it has nothing comparable
    (5 short edges, worst +0.016u). A vertex dragged 0.39u from a neighbour it was
    0.02u from is a spike or a tear in a small plate.

    WHY THE SEAM WELD MISSES THEM. `_weld_source_coincident_verts` welds verts that
    are COINCIDENT; 0.0229u is far above its tolerance while still being detail
    that has to move as one piece.

    WHY THIS CANNOT REPEAT THE FAILURES BEFORE IT. Two earlier attempts at these
    same fittings were reverted for RELOCATING them -- restoring a fitting's
    authored shape sank it into the strap, and every re-anchoring under-corrected.
    This pass moves each pair's endpoints toward each other by EQUAL AND OPPOSITE
    amounts, so every corrected edge keeps its midpoint exactly. It cannot move a
    fitting, only un-stretch it. And it can only ever touch edges the author drew
    shorter than 0.05u, which bounds the blast radius by construction.

    The allowance scales with the garment's OWN growth, measured from the edges
    long enough to measure it honestly, so a piece legitimately fitted to a larger
    body keeps its detail proportionally. The cap is on the blow-up, not on the fit.

    Returns (verts, n_moved). Best-effort; never raises.
    """
    if not _nc().SHORT_EDGE_CAP:
        return out_verts, 0
    try:
        sv = np.asarray(src_verts, dtype=np.float64)
        ov = np.asarray(out_verts, dtype=np.float64)
        t = np.asarray(tris, dtype=np.int64)
        if (sv.ndim != 2 or sv.shape != ov.shape or len(sv) < 4
                or t.ndim != 2 or t.shape[1] != 3 or len(t) == 0):
            return out_verts, 0
        e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e = np.unique(np.sort(e, axis=1), axis=0)
        ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
        lo0 = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
        ok = ls > 1e-9
        _long = ok & (ls >= _nc().SHORT_EDGE_MAX)
        short = ok & (ls < _nc().SHORT_EDGE_MAX)
        if not short.any() or not _long.any():
            return out_verts, 0
        # Deliberately NOT measured from the short edges this pass is judging.
        grow = float(np.median(lo0[_long] / ls[_long]))
        grow = min(max(grow, 1.0), 2.0)
        es = e[short]
        allowed = ls[short] * grow * _nc().SHORT_EDGE_SLACK
        cur = ov.copy()
        n = len(sv)
        for _ in range(_nc().SHORT_EDGE_ITERS):
            d = cur[es[:, 1]] - cur[es[:, 0]]
            L = np.linalg.norm(d, axis=1)
            bad = (L > allowed) & (L > 1e-12)
            if not np.any(bad):
                break
            # Equal and opposite: the midpoint of every corrected edge is fixed.
            half = (0.5 * (L[bad] - allowed[bad]) / L[bad])[:, None] * d[bad]
            acc = np.zeros_like(cur)
            cnt = np.zeros(n)
            np.add.at(acc, es[bad, 0], half)
            np.add.at(cnt, es[bad, 0], 1.0)
            np.add.at(acc, es[bad, 1], -half)
            np.add.at(cnt, es[bad, 1], 1.0)
            mv = cnt > 0
            cur[mv] += acc[mv] / cnt[mv, None]
        moved = int((np.linalg.norm(cur - ov, axis=1) > 1e-6).sum())
        return (cur, moved) if moved else (out_verts, 0)
    except Exception as _ee:
        _note_pass_failure("_cap_short_edge_stretch", _ee)
        return out_verts, 0

def _uniformise_local_scale(src_verts, out_verts, tris):
    """Remove the VARIANCE in a shape's local scale, keeping its overall growth.
    #strap-scale-uniform

    REPORTED IN GAME, twice: "belts still have distortion", then "belts are still
    visably distorting". This is the STRAP. The buckle on the same belt is a
    separate defect and is still open -- see #rigid-small-element-rejected in
    PIPELINE.md for why the obvious repair for it cannot work.

    WHAT IT IS, measured. Edge length is the frame-free way to separate bending
    from stretching: a belt wrapped round a wider waist bends, and bending keeps
    every edge the length the author drew. On the reported piece the strap's edge
    ratios run 0.517 to 1.514 -- edges HALVED next to edges stretched by half,
    with a mean absolute deviation of 0.219 against 0.060 for the chest plate on
    the same garment. 78% of its edges are outside 5%.

    WHY NOT ISOMETRY. The strap's MEDIAN ratio is 1.025, and that is correct: a
    belt fitted to a wider body should be slightly longer. Forcing edges back to
    the author's lengths would fight the fit. The defect is the SPREAD, so the
    repair is to give every edge the length its NEIGHBOURHOOD agrees on -- a
    smoothed local scale field -- and relax to that. Uniform growth survives; the
    crumple does not.

    NOT A REGRESSION, and that is why it needs its own pass rather than a bisect:
    1.2 measures 0.224 on the same strap against today's 0.219. It has always
    looked like this.

    Edge lengths only -- no rigid fit, no reference frame. That matters because
    every per-vertex Kabsch test in this project degenerates on a 2u-wide strap,
    where the 1-ring is nearly collinear and the fitted rotation is noise.

    Gated on the shape's OWN distortion so it cannot resurface a garment that is
    merely imperfect: at 0.15 the reported strap (0.219) qualifies and the chest
    plate (0.060) and outer layer (0.121) do not.

    Returns (verts, n_moved). Best-effort; never raises.
    """
    if not _nc().STRAP_SCALE_UNIFORM:
        return out_verts, 0
    try:
        sv = np.asarray(src_verts, dtype=np.float64)
        ov = np.asarray(out_verts, dtype=np.float64)
        t = np.asarray(tris, dtype=np.int64)
        if (sv.ndim != 2 or sv.shape != ov.shape or len(sv) < 16
                or t.ndim != 2 or t.shape[1] != 3 or len(t) == 0):
            return out_verts, 0
        n = len(sv)
        e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e = np.unique(np.sort(e, axis=1), axis=0)
        ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
        ok = ls > 1e-6
        e, ls = e[ok], ls[ok]
        if len(e) == 0:
            return out_verts, 0
        # FREEZE the fittings -- but ONLY while the rigid pass is the one handling
        # them. The two ran in series on the same verts and fought: this one went
        # first and took `belts_metal` from an exact 1.000 isometry back to 0.009,
        # because the rigid pass afterwards only re-refit what still met its gates.
        #
        # LETTING IT REACH THE FITTINGS WAS TRIED AND IS WORSE, so the freeze is
        # unconditional. The buckle's own edge deviation is 0.364 and this pass is
        # an algorithm for reducing exactly that, so reaching it looked obviously
        # right -- measured, it took the buckle to 0.454 and a chest plate from
        # 0.060 to 0.090. A cluster of separate small objects has a scale field
        # that legitimately VARIES between them, and smoothing it pushes each one
        # toward a consensus that belongs to its neighbours, not to it. This pass
        # repairs a continuous surface; a fitting is not one.
        _small = _nc()._small_element_mask(sv, t)
        if _small is not None and _small.any():
            _free = ~(_small[e[:, 0]] | _small[e[:, 1]])
            e, ls = e[_free], ls[_free]
            if len(e) == 0:
                return out_verts, 0
        else:
            _small = np.zeros(n, dtype=bool)
        lo = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
        ratio = lo / ls
        if float(np.abs(ratio - 1.0).mean()) < _nc().STRAP_SCALE_MIN_DEV:
            return out_verts, 0        # this shape is not distorted enough
        # MIS-SCALED IS NOT CRUMPLED. This pass makes the local scale UNIFORM, so
        # on a shape whose median edge is 3x or 20x the author's it would produce
        # an evenly ballooned shape rather than a repaired one -- tidier by this
        # metric and no less broken. Censused, the pack's extremes are exactly
        # that: a boot fitted from a MALE source measures 21.3 mean deviation and
        # a skirt collider 3.7. Those need a different fix, not this one.
        _med = float(np.median(ratio))
        if not (1.0 / _nc().STRAP_SCALE_MAX_GROWTH) <= _med <= _nc().STRAP_SCALE_MAX_GROWTH:
            return out_verts, 0
        # Smooth local scale field: what each neighbourhood AGREES the scale is.
        s = np.ones(n)
        acc = np.zeros(n)
        cnt = np.zeros(n)
        for k in (0, 1):
            np.add.at(acc, e[:, k], ratio)
            np.add.at(cnt, e[:, k], 1.0)
        m = cnt > 0
        s[m] = acc[m] / cnt[m]
        for _ in range(_nc().STRAP_SCALE_SMOOTH):
            acc[:] = 0.0
            cnt[:] = 0.0
            np.add.at(acc, e[:, 0], s[e[:, 1]])
            np.add.at(cnt, e[:, 0], 1.0)
            np.add.at(acc, e[:, 1], s[e[:, 0]])
            np.add.at(cnt, e[:, 1], 1.0)
            m = cnt > 0
            s[m] = acc[m] / cnt[m]
        target = ls * 0.5 * (s[e[:, 0]] + s[e[:, 1]])
        cur = ov.copy()
        for _ in range(_nc().STRAP_SCALE_ITERS):
            d = cur[e[:, 1]] - cur[e[:, 0]]
            L = np.linalg.norm(d, axis=1)
            live = (L > 1e-9) & (target > 1e-9)
            act = live & (np.abs(L / np.where(live, target, 1.0) - 1.0)
                          > _nc().STRAP_SCALE_TOL)
            if not act.any():
                break
            corr = d[act] * (0.5 * (L[act] - target[act]) / L[act])[:, None]
            acc2 = np.zeros_like(cur)
            cnt2 = np.zeros(n)
            np.add.at(acc2, e[act, 0], corr)
            np.add.at(cnt2, e[act, 0], 1.0)
            np.add.at(acc2, e[act, 1], -corr)
            np.add.at(cnt2, e[act, 1], 1.0)
            mv = (cnt2 > 0) & (~_small)
            cur[mv] += acc2[mv] / cnt2[mv, None]
            # Anchor only what moved: blending every vertex toward its original
            # turns a repair into a shape-wide nudge and makes the moved count
            # useless as a blast radius.
            cur[mv] = ((1.0 - _nc().STRAP_SCALE_ANCHOR) * cur[mv]
                       + _nc().STRAP_SCALE_ANCHOR * ov[mv])
        moved = int((np.linalg.norm(cur - ov, axis=1) > 1e-4).sum())
        return (cur, moved) if moved else (out_verts, 0)
    except Exception as _se:
        # REPORTED, never swallowed. Returning 0 quietly is indistinguishable from
        # "no shape was distorted enough to qualify", which is this pass's normal
        # and most common outcome -- so a broken pass would look exactly like a
        # clean run. That is the failure this project fixed in seven other passes
        # earlier the same day; do not reintroduce it here.
        _note_pass_failure("_uniformise_local_scale", _se)
        return out_verts, 0

def _clamp_delta_outliers(delta, tris, n, max_dev=WARP_DELTA_OUTLIER_MAX):
    """Pull each vertex's warp delta back toward its 1-ring mean.

    Only the DEVIATION is capped, so a vertex whose whole neighbourhood moves
    together keeps every bit of that motion -- this cannot flatten the reshape,
    only the lone flier. A vertex with no ring (isolated) is left alone: there
    is nothing to compare it against, and inventing a reference would be worse
    than the outlier.

    Returns (delta, n_clamped).
    """
    from scipy import sparse
    t = np.asarray(tris, dtype=np.int64)
    d = np.array(delta, dtype=np.float64, copy=True)
    if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0 or max_dev <= 0:
        return d, 0
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    rows = np.concatenate([e[:, 0], e[:, 1]])
    cols = np.concatenate([e[:, 1], e[:, 0]])
    adj = sparse.coo_matrix((np.ones(len(rows)), (rows, cols)),
                            shape=(n, n)).tocsr()
    adj.data[:] = 1.0
    deg = np.asarray(adj.sum(axis=1)).ravel()
    has = deg > 0
    ring = np.zeros_like(d)
    ring[has] = (adj @ d)[has] / deg[has][:, None]
    dev = d - ring
    mag = np.linalg.norm(dev, axis=1)
    over = has & (mag > max_dev)
    if not over.any():
        return d, 0
    scale = np.ones(len(d))
    scale[over] = max_dev / mag[over]
    d = ring + dev * scale[:, None]
    return d, int(over.sum())

def _limit_triangle_shear(verts, delta, tris, max_growth=WARP_SHEAR_MAX_GROWTH,
                          steps=WARP_SHEAR_STEPS):
    """Cap how far the warp may STRETCH any one triangle, without warping less.

    Each vertex's displacement is split into the ring-average of its neighbours'
    displacements plus its own deviation from that average. Only the DEVIATION is
    scaled back. The average is never touched, so a clamped vertex still travels
    the full local CBBE->UBE delta -- this cannot leave a garment CBBE-shaped,
    which is the failure mode that makes any edit to this pass dangerous.

    Triangles that are degenerate before the warp are skipped: an area ratio
    against ~0 is meaningless, not infinite.
    """
    v = np.asarray(verts, dtype=np.float64)
    d = np.asarray(delta, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0:
        return d

    def area(x):
        p0, p1, p2 = x[t[:, 0]], x[t[:, 1]], x[t[:, 2]]
        return 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)

    a0 = area(v)
    live = a0 > 1e-9
    if not live.any():
        return d

    bar = _nc()._ring_average(d, t, len(v))
    dev = d - bar
    scale = np.ones(len(v), dtype=np.float64)
    cap = float(max_growth)
    for _ in range(int(steps)):
        a1 = area(v + bar + dev * scale[:, None])
        bad = live & (a1 > cap * a0)
        if not bad.any():
            break
        scale[np.unique(t[bad])] *= 0.5
    # Feather ONCE, min-only so it can only tighten (see feather rule).
    ring = _nc()._ring_average(scale[:, None], t, len(v)).ravel()
    scale = np.minimum(scale, ring)
    return bar + dev * scale[:, None]

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
    tris: "np.ndarray | None" = None,
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
      tris: (T, 3) armor triangle indices. Optional; when given (and
        WARP_STANDOFF_SMOOTH_ENABLED) the Pass-2 standoff push is feathered
        over mesh adjacency instead of applied on a hard threshold, which is
        what turns the push boundary from a cliff into a gradient. Omitting
        it preserves the original unsmoothed behaviour exactly.
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

    # #warp-delta-smooth. The delta field is what the distortion IS: measured
    # on one garment, the applied displacement jumps between adjacent vertices
    # by 15-23% of the edge between them, and that number is the same as the
    # edge stretch it produces. `warp` contributes 72-87% of the waist
    # distortion on that piece, more than every other pass combined.
    #
    # REFUTED END TO END, 2026-08-13. Kept default-OFF and reachable so the
    # negative stays reproducible.
    #
    # AT THIS STAGE it works, and dramatically: k=2/w=0.5 took belts_metal's
    # band stretch 0.137 -> 0.069 and its anisotropy 3.97 -> 1.89. It also ate
    # the clearance handed downstream -- standoff p10 0.150 -> 0.094, and the
    # shape went from ZERO verts inside the body to 12.
    #
    # ON THE SHIPPED MESH the gain is gone and the mesh is worse:
    #
    #     arm      belts stretch   belts aniso   FOLDED verts (all shapes)
    #     k=0          0.141          2.46            92
    #     k=2          0.139          2.54           102
    #     k=10         0.128          2.59           123
    #
    # Five times the smoothing buys ~9% of stretch on ONE shape while folds
    # rise 34% and anisotropy -- the quantity that actually smears a texture --
    # gets worse.
    #
    # WHY, and it invalidates the reason this was tried. The argument for it
    # was that #push-divergence-smooth failed because the clearance push
    # GUARANTEES each vertex's standoff along its own normal, so its restore
    # step re-added the divergence smoothing removed -- while the body-delta
    # field carries no such guarantee and nothing would fight it back. The
    # guarantee is simply enforced LATER. Hand inflate and anti-poke a smoother
    # mesh with less clearance and they push harder, along the same diverging
    # normal field, re-creating the distortion and adding folds. Same
    # structure, one pass removed.
    #
    # THE METHODOLOGICAL LESSON, which outlives this flag: a stage ledger
    # attributes but does NOT establish cause. `warp` measures as 72-87% of the
    # waist distortion, yet halving what warp contributes did not halve the
    # result, because the passes after it compensate toward their own targets.
    # Read the fold ledger the same way -- `inflate` at +296 may likewise be
    # compensating for what reaches it, not originating the damage.
    if (_nc().WARP_DELTA_SMOOTH_ITERS > 0 and tris is not None
            and len(interp_delta) > 3):
        try:
            _t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
            _e = np.unique(np.sort(np.vstack(
                [_t[:, [0, 1]], _t[:, [1, 2]], _t[:, [0, 2]]]), axis=1), axis=0)
            _w = float(np.clip(_nc().WARP_DELTA_SMOOTH_WEIGHT, 0.0, 1.0))
            for _ in range(int(_nc().WARP_DELTA_SMOOTH_ITERS)):
                _acc = np.zeros_like(interp_delta)
                _cnt = np.zeros(len(interp_delta))
                for _a, _b in ((_e[:, 0], _e[:, 1]), (_e[:, 1], _e[:, 0])):
                    np.add.at(_acc, _a, interp_delta[_b])
                    np.add.at(_cnt, _a, 1.0)
                _ok = _cnt > 0
                _tgt = interp_delta.copy()
                _tgt[_ok] = _acc[_ok] / _cnt[_ok, None]
                interp_delta[_ok] = ((1.0 - _w) * interp_delta[_ok]
                                     + _w * _tgt[_ok])
        except Exception as _wse:
            _note_pass_failure("warp_delta_smooth", _wse)

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
            # Build the push as a full-length SCALAR magnitude along near_n so it
            # can be feathered; the vector form can't be smoothed without blending
            # opposing normals in concave regions (see the k=1 note above).
            push_mag = np.zeros(len(warped), dtype=np.float64)
            push_mag[need_push] = min_standoff - signed[need_push]
            if max_distance is not None and max_distance > 0:
                push_mag[need_push] *= push_falloff[need_push]
            # #warp-push-shell-cap, BEFORE `raw_mag` is snapshotted. The
            # safety clamp below can revert a vertex to the UNSMOOTHED result,
            # so capping only the smoothed field would let the very spike this
            # is removing come straight back through that path.
            if _nc().WARP_PUSH_SHELL_CAP and tris is not None:
                try:
                    push_mag, _n_cap = _cap_push_at_own_shell(
                        warped, tris, near_n, push_mag)
                except Exception as _e:
                    # measurement failure must not move geometry -- but record
                    # it, or a cap that never runs looks like a cap that never
                    # had anything to do.
                    _note_pass_failure("_cap_push_at_own_shell", _e)
            raw_mag = push_mag.copy()
            if _nc().WARP_STANDOFF_SMOOTH_ENABLED and tris is not None:
                # Floored at the raw requirement: smoothing may only ADD push to
                # under-pushed neighbours, never reduce a vert below its standoff.
                push_mag = _smooth_push_field(
                    push_mag, raw_mag, tris,
                    iters=_nc().WARP_STANDOFF_SMOOTH_ITERS, verts=warped)
            unsmoothed = warped.copy()
            unsmoothed[raw_mag > 0] += (
                near_n[raw_mag > 0] * raw_mag[raw_mag > 0][:, None])
            moved = push_mag > 0
            if moved.any():
                warped[moved] += near_n[moved] * push_mag[moved][:, None]
            if _nc().WARP_STANDOFF_SMOOTH_ENABLED and tris is not None and moved.any():
                # SAFETY CLAMP. Re-flooring the MAGNITUDE is not enough: spreading
                # push onto a neighbour that needed none moves it along ITS OWN
                # near_n, and where the surface is thin or concave (a belt strap,
                # between the legs) that direction can drive the vert INTO another
                # part of the body -- measured on a thin belt strap, min standoff
                # 0.0206 -> -0.0092 (outside -> inside). So verify against the
                # real geometry and revert any vert whose clearance got worse
                # than it would have been unsmoothed. Smoothing is then a strict
                # improvement or a no-op, never a new poke.
                sm_d, sm_i = ube_tree.query(warped, k=1)
                un_d, un_i = ube_tree.query(unsmoothed, k=1)
                sm_signed = ((warped - ube_v[sm_i]) * ube_n[sm_i]).sum(axis=1)
                un_signed = ((unsmoothed - ube_v[un_i]) * ube_n[un_i]).sum(axis=1)
                worse = moved & (sm_signed < un_signed - 1e-9)
                if worse.any():
                    warped[worse] = unsmoothed[worse]

    # #warp-delta-outlier: against the TOTAL displacement, NOT the interpolated
    # delta. Traced on the vertex that motivated it: its delta deviates from its
    # ring by 0.372u (mesh max 1.070u) and is entirely unremarkable, yet the
    # vertex ends up 5.07u from source -- the other ~4.6u is PASS 2 shoving a
    # deep interior vertex out to the clearance floor while its ring stays put.
    # Clamping the delta therefore measured as a perfect no-op. The roughness a
    # viewer sees is a property of the finished geometry, so it has to be capped
    # on the finished geometry.
    if _nc().WARP_DELTA_OUTLIER and tris is not None:
        _tot, _n_out = _clamp_delta_outliers(
            warped.astype(np.float64) - armor_verts, tris, len(armor_verts))
        warped = armor_verts + _tot

    # #warp-shear-limit: applied HERE, against the pass's TOTAL displacement,
    # not against `interp_delta` before the standoff pass. Clamping the delta
    # first measured as a complete no-op on the traced cuirass -- the guard fired
    # (4 triangles, 2.66x -> 1.73x) and the shipped mesh was byte-identical --
    # because Pass 2 then repositions those same verts against the body surface
    # and overwrites the correction. The offending corners are placed by the
    # standoff push, not by the body delta, so the cap has to see the result.
    if _nc().WARP_SHEAR_LIMIT and tris is not None:
        total = _limit_triangle_shear(
            armor_verts, warped.astype(np.float64) - armor_verts, tris)
        warped = armor_verts + total

    return warped.astype(np.float32)

def _damp_to_avoid_inversion(cur, disp, tris, steps=CONFORM_FOLD_GUARD_STEPS,
                             margin=CONFORM_FOLD_GUARD_MARGIN,
                             feather=CONFORM_FOLD_GUARD_FEATHER):
    """Scale back per-vertex displacement until no triangle turns inside-out.

    `cur` is the geometry ENTERING the pass and is the orientation reference, so
    a triangle the author already shipped inverted stays exactly as inverted as
    it arrived -- this guard forbids NEW flips, it does not repair old ones. A
    triangle that is degenerate on entry has no reliable normal and is ignored
    rather than guessed at.

    A triangle must keep `margin` of its entry signed area, not merely stay on
    the positive side of zero. Barely-positive is not a safe answer here: the
    pass returns float32, and a triangle left pinched flat rounds back over the
    boundary on the cast.

    Returns the damped displacement. Every component is the requested
    displacement times a per-vertex factor in [0, 1], so the result can only be
    a SHORTER move in the SAME direction -- there is no configuration in which
    this steers a vertex sideways or sends it further than asked.

    Damping is re-evaluated over ALL triangles every step rather than only the
    offending ones, because shrinking one vertex's motion reshapes every
    triangle that vertex belongs to and can in principle disturb a neighbour
    that was fine. The fixed point is scale = 0, which is the (valid) entry
    geometry, so the loop cannot diverge; `steps` bounds the work, and the
    result is safe at any step count because it is only ever less motion.
    """
    cur = np.asarray(cur, dtype=np.float64)
    disp = np.asarray(disp, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0:
        return disp

    def wind(v):
        p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        return np.cross(p1 - p0, p2 - p0)

    ref = wind(cur)
    ref_len = np.linalg.norm(ref, axis=1)
    live = ref_len > 1e-12                     # degenerate on entry -> ignore
    if not live.any():
        return disp

    # Projecting the candidate cross product onto the ENTRY one measures signed
    # area in the entry orientation, so the test is "kept this share of the area
    # it had, facing the same way" -- one comparison for both flip and collapse.
    floor = float(margin) * (ref_len * ref_len)

    def settle(scale):
        for _ in range(int(steps)):
            cand = wind(cur + disp * scale[:, None])
            bad = live & (np.einsum("ij,ij->i", ref, cand) < floor)
            if not bad.any():
                break
            scale[np.unique(t[bad])] *= 0.5
        return scale

    scale = settle(np.ones(len(cur), dtype=np.float64))

    if int(feather) > 0:
        # Ring-average the damping field, then take the MIN against what the
        # settle step decided. Min is what keeps this safe: feathering can only
        # pull a scale DOWN, so it can neither undo the guard nor push a vertex
        # past what conform asked for. Re-settle afterwards because a lowered
        # scale is still a different geometry and deserves the same test.
        from scipy import sparse
        n = len(cur)
        e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        rows = np.concatenate([e[:, 0], e[:, 1]])
        cols = np.concatenate([e[:, 1], e[:, 0]])
        adj = sparse.coo_matrix((np.ones(len(rows)), (rows, cols)),
                                shape=(n, n)).tocsr()
        adj.data[:] = 1.0
        deg = np.asarray(adj.sum(axis=1)).ravel()
        has = deg > 0
        for _ in range(int(feather)):
            ring = np.array(scale, copy=True)
            ring[has] = ((adj @ scale)[has] + scale[has]) / (deg[has] + 1.0)
            scale = np.minimum(scale, ring)
        scale = settle(scale)

    return disp * scale[:, None]

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
    blend_tight: float = CONFORM_BLEND_TIGHT,
    tight_standoff: float = 1.0,
    loose_standoff: float = 4.0,
    max_pull: float = 4.0,
    max_body_dist: float = 12.0,
    bust_clearance: float = CONFORM_BUST_CLEARANCE,
    bust_z: "tuple[float, float]" = (84.0, 100.0),
    max_push_out: float = 2.5,
    ube_body_nipple: "np.ndarray | None" = None,
    morph_amplitude: "np.ndarray | None" = None,
    morph_differential: "np.ndarray | None" = None,
    static_amp: float = STATIC_AUTHORED_AMP,
    static_min_clearance: float = STATIC_AUTHORED_MIN_CLEARANCE,
    tris: "np.ndarray | None" = None,
    conform_margin: float = 0.0,
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
    pushed OUT to >= `bust_clearance` so the body's nipple can't poke through it.
    Cloth already at/above bust_clearance there is untouched.

    That requirement is a MARGIN OVER THE WORST NEARBY BODY POINT, not a budget
    sized to cover the nipple: `worst` below is the smallest clearance among the
    vert's k nearest body points, so on the bust the nipple IS the point being
    cleared and the garment is held `req` in front of it. This docstring used to
    claim `bust_clearance` "defaults above the measured UBE nipple protrusion",
    which was unbacked -- no figure was recorded and the phrase describes a
    budget, not the margin the code applies. Measured on the UBE body when the
    default moved (2026-08-13): a tip vert stands 0.057u (p90) above the surface
    within 1u of it, while the same measure over a 2-4u neighbourhood reads
    0.7-1.1u because it is dominated by the breast's own curvature rather than by
    the nipple. So do not re-derive this constant from a protrusion number
    without saying which scale it was taken at.
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
    # #static-authored-fit: relax BOTH limiters where the body doesn't morph.
    # `static_w` is 1 in a fully static zone and 0 once the outward morph
    # amplitude reaches `static_amp`, so morph zones keep today's numbers
    # exactly and there is no discontinuity between the two.
    min_clear_v = min_clearance
    blend_tight_v = blend_tight
    if (_nc().STATIC_AUTHORED_FIT and morph_amplitude is not None
            and len(morph_amplitude) == len(ube_body_verts)):
        amp_at = np.asarray(morph_amplitude, dtype=np.float64)[ui]
        static_w = np.clip((static_amp - amp_at) / max(static_amp, 1e-6),
                           0.0, 1.0)
        min_clear_v = (min_clearance
                       + static_w * (static_min_clearance - min_clearance))
        blend_tight_v = blend_tight + static_w * (1.0 - blend_tight)
    tight = np.maximum(np.minimum(s_src, s_cur), min_clear_v)
    # #derived-clearance -- OPT-IN. Aim at a target DERIVED per vertex instead of
    # holding back a fixed fraction of the authored fit.
    #
    # `blend_tight` (0.3) exists because a piece fitted to the smaller 3BA body
    # needs room on the bigger UBE one -- so it keeps seven tenths of the gap as
    # an unmeasured safety margin, everywhere, whatever the garment. MEASURED on
    # a reported bodysuit the author held at a uniform 0.24-0.34u:
    #
    #   region        authored   headroom   TARGET   shipped   excess
    #   flank            0.288      0.004    0.292     0.816    +0.524
    #   STOMACH          0.339      0.251    0.590     1.271    +0.681
    #   BUTT             0.314      0.705    1.019     1.420    +0.401
    #
    # The right gap varies 3.5x across one garment -- 0.29u at a flank the body
    # never moves, 1.02u at a butt that grows 0.7u -- so no constant can express
    # it, and a GLOBAL tightening gets it backwards: pushing `blend_tight` to 1.0
    # pulled the butt and lower back BELOW their target (-0.08, -0.30), exactly
    # where the body needs the most room, while still leaving +0.33 at the flank
    # where it needs none.
    #
    # So: target = the author's own standoff + the clearance the worst slider
    # TAKES AWAY at that vertex. `morph_differential` is that second term,
    # already computed by `_cached_body_morph_differential` for the anti-poke and
    # evaluated over the whole slider envelope rather than one preset -- it is a
    # DIFFERENTIAL, so a slider that merely translates or inflates uniformly
    # contributes nothing (see #clearance-differential: paid for GROWTH, not
    # RESHAPE).
    #
    # Still pull-IN only: `move` below is clamped <= 0 outside the bust band, so
    # this can lower a target but can never push a vert outward.
    if (_nc().DERIVED_CLEARANCE_TARGET and morph_differential is not None
            and len(morph_differential) == len(ube_body_verts)):
        _diff = np.clip(
            np.asarray(morph_differential, dtype=np.float64)[ui], 0.0, None)
        _derived = np.maximum(s_src + _diff, min_clear_v)
        # Capped at the CURRENT standoff so this can only ever aim inward. The
        # `move <= 0` clamp below enforces that too; stating it here keeps the
        # target itself honest rather than relying on a later clip.
        tight = np.minimum(_derived, np.maximum(s_cur, min_clear_v))
        # The blend existed to hold back an unmeasured margin. The margin is now
        # explicit in the target, so aim at it in full -- holding back on top
        # would double-count the headroom.
        blend = 1.0
    # #unified-offset candidate (c): fold inflate's additive margin into THIS
    # target instead of running it as a separate pass. `inflate` adds headroom
    # to the authored drape; conform aims AT the authored drape; so the two
    # composed are just "authored drape PLUS headroom".
    #
    # Raising `tight` reduces how far a vert is pulled in, i.e. leaves it
    # `margin` further out -- for the verts conform actually touches. It cannot
    # PUSH a vert out, because `move` below is clamped to <= 0. Whether that
    # reach is enough is the empirical question; see AUDIT A16.
    if float(conform_margin) > 0.0:
        tight = tight + float(conform_margin)
    if blend is None:
        # ADAPTIVE per-vert blend keyed on the source clearance:
        #  - tight verts (skin-hugging, small s_src) keep ROOM (low blend) so they
        #    don't clip on the bigger/morphed UBE body (tight-corset case);
        #  - loose/draping verts (large s_src, e.g. forsworn fur/feathers) are
        #    reeled most of the way back to their source drape (-> 1.0) so they
        #    don't FLOAT off the body (the forsworn gap). One knob, both symptoms.
        _b = np.clip((s_src - tight_standoff)
                     / max(loose_standoff - tight_standoff, 1e-6), 0.0, 1.0)
        blend_v = blend_tight_v + _b * (1.0 - blend_tight_v)
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
    in_back = None
    if np.any(in_bust):
        # #bust-neighbourhood-spacing (see the constants for the measurement):
        # each garment vertex must clear the body under the patch its surface
        # spans, and that patch is its own local vertex spacing -- not a
        # constant, and not the ~0.67u that k=6 happens to reach.
        _spacing_aware = _nc().BUST_SPACING_AWARE and len(cur_cloth) > 4
        kk = min(_nc().BUST_NEIGHBORHOOD_K_MAX if _spacing_aware
                 else _nc().BUST_NEIGHBORHOOD_K, len(ube_body_verts))
        dd, jj = ube_tree.query(cur_cloth, k=kk)
        if kk == 1:
            dd = dd[:, None]; jj = jj[:, None]
        if _spacing_aware:
            gk = min(5, len(cur_cloth))
            gd, _ = cKDTree(cur_cloth).query(cur_cloth, k=gk)
            spacing = gd[:, 1:].mean(axis=1)          # local vertex spacing
            radius_v = np.minimum(spacing * _nc().BUST_SPACING_MULT,
                                  _nc().BUST_NEIGHBORHOOD_RADIUS_MAX)[:, None]
            # Today's sample is kept as an exact SUBSET -- the first
            # BUST_NEIGHBORHOOD_K neighbours are always included, whatever the
            # radius works out to -- and the spacing-driven radius only ever
            # ADDS to it. So a garment finer than the body is sampled bit for
            # bit as before, and the required push can only ever grow, never
            # shrink. (A plain radius floor is not equivalent: on tied
            # distances it admits neighbours that k truncated arbitrarily.)
            keep = (np.arange(kk)[None, :] < _nc().BUST_NEIGHBORHOOD_K) | (dd <= radius_v)
        else:
            keep = dd <= _nc().BUST_NEIGHBORHOOD_RADIUS
        nrm0 = ube_body_normals[ui]                   # nearest-vert outward normal
        diff = cur_cloth[:, None, :] - ube_body_verts[jj]          # (n, kk, 3)
        s_k = (diff * nrm0[:, None, :]).sum(axis=2)                # clearance over each neighbour along nrm0
        s_k = np.where(keep, s_k, np.inf)
        worst = np.min(s_k, axis=1)                                # closest nearby body point
        worst = np.where(np.isfinite(worst), worst, s_cur)         # fallback: no neighbour in radius
        # required clearance: small everywhere, ramping up only at the nipple
        if (ube_body_nipple is not None
                and len(ube_body_nipple) == len(ube_body_verts)):
            nipw = np.asarray(ube_body_nipple, dtype=np.float64)[ui]
        else:
            nipw = np.zeros(len(ui))
        # #nipple-ramp-sharpness. The ramp is linear in the body's nipple
        # WEIGHT, and that weight is broad: on the shipped UBE body 94% of the
        # bust band carries some, p50 0.119 and p90 0.537 against a maximum of
        # 0.563. So `req` lifts the whole breast DOME, not the tip -- band
        # median 0.239u where the flat floor is 0.12u -- and the garment sits
        # proud everywhere. Measured across the pack, our bust standoff runs
        # +0.467u (p50) over the AUTHOR's on 88% of shapes.
        #
        # The comment on CONFORM_BUST_CLEARANCE names this exact fix and says
        # it is unbuilt: "a narrower nipple region with a steeper ramp, so the
        # tip keeps its clearance without dragging the whole bust band out with
        # it". Lowering the ceiling instead was tried and reverted the same day
        # -- it clips the TIP, and a nipple came through a leather cuirass.
        #
        # Sharpening alone would lower the tip too, so the gain is SOLVED from
        # the body's own maximum weight to hold the tip's requirement exactly
        # where it is today. The tip column is therefore constant by
        # construction, and only the shoulder of the ramp moves:
        #
        #     k     gain      p50     p75     p90     tip
        #     1.0   1.00    0.239   0.566   0.657   0.683   <- today
        #     2.0   1.77    0.145   0.473   0.632   0.683
        #     3.0   3.15    0.125   0.399   0.608   0.683
        #
        # Calibrated on the WHOLE body's weight array, not this shape's subset,
        # so a garment covering only the flat chest cannot solve a huge gain off
        # a local maximum of nearly zero.
        #
        # MEASURED INERT ON THE FINAL MESH, DEFAULT 1.0. On the piece it was
        # built for it changes `req` exactly as modelled and then does not
        # survive: bust median 1.087u -> 1.084u. `s07_antipoke` re-pushes AFTER
        # conform (+0.691u on that piece), so a lower conform requirement is
        # overwritten -- the same "two pushes, a floor on one is UNDONE
        # downstream" that `#snugness-two-pushes` records, hit from the other
        # side.
        #
        # It is kept, off, because the modelled narrowing is real and this pass
        # IS the last word on shapes that skip anti-poke. It is NOT a fix for
        # the bust gap. Five clearance knobs are now measured inert on that
        # piece -- inflate magnitude, ANTIPOKE_FLAT_CLEAR, CBBE2UBE_BUST_CLEAR,
        # this ramp, and CLEARANCE_MORPH_MAX (1.1 -> 0.7 moved the median only
        # 1.087 -> 1.006, and 0.4 was no better). The gap is +0.467u over the
        # AUTHOR at p50 across 296 shapes on 88% of them, and closing it needs
        # the producer changed, not a knob turned.
        _k = float(_nc().BUST_NIPPLE_SHARPNESS)
        _gain = float(_nc().BUST_NIPPLE_GAIN)
        if _k > 1.0 and ube_body_nipple is not None:
            _wmax = float(np.max(np.asarray(ube_body_nipple, dtype=np.float64))
                          or 0.0)
            if _wmax > 0.2:                 # a real nipple map, not a flat panel
                _gain = (_wmax * _nc().BUST_NIPPLE_GAIN) / (_wmax ** _k)
                nipw = nipw ** _k
        req = np.clip(_nc().BUST_FLAT_CLEARANCE + nipw * _gain,
                      _nc().BUST_FLAT_CLEARANCE, bust_clearance)
        # #bust-authored-nipple-cap: never demand MORE room at the nipple than
        # the author left there. See the constant for the in-game report and
        # the numbers -- on the reported piece the author's plate sits CLOSER
        # at the nipple than on its own flat chest, so the ramp above is
        # pushing against what they built and embossing the tip through it.
        #
        # Distance, not signed standoff: the source body's normals are zeroed
        # at defaults on this path (F011), and a normal-based measure would
        # read every authored clearance as 0 and flatten `req` to nothing.
        if _nc().BUST_AUTHORED_NIPPLE_CAP:
            try:
                # `sd` is already the authored clearance by distance -- the
                # function computes it at the top as
                #     sd, si = cKDTree(src_body_verts).query(src_cloth, k=1)
                # so this reuses it rather than rebuilding the same tree.
                _flat = in_bust & (nipw < _nc().BUST_AUTHORED_FLAT_NIPW)
                if int(_flat.sum()) >= 20:
                    # the author's OWN baseline for THIS shape, so a garment
                    # that simply sits far out everywhere is not penalised
                    _base = float(np.median(sd[_flat]))
                    _extra = np.clip(sd - _base, 0.0, None)
                    _allow = (_nc().BUST_FLAT_CLEARANCE + _extra
                              + _nc().BUST_AUTHORED_NIPPLE_GROWTH)
                    req = np.minimum(req, _allow)   # only ever LOWERS it
            except Exception as _e:
                _nc()._note_pass_failure("bust-authored-nipple-cap", _e)
        # #bust-morph-residual (see the constants): `req` above is a BIND-pose
        # requirement, and the character in game is morphed. `generate_armor_tri`
        # hands a hugging garment vert the delta of ITS OWN nearest body vert,
        # so under slider m the clearance over neighbour b changes by
        #     (delta_m[b] - delta_m[ui]) . nrm0
        # -- zero when the slider merely inflates (both move alike), POSITIVE
        # when it reshapes and carries b outward relative to the vert covering
        # it. Take the worst such residual over every slider and demand it on
        # top of `req`, so the fit that is tight at bind stays covered morphed.
        if _nc().BUST_MORPH_RESIDUAL:
            _stack = _cached_body_morph_stack(_find_ube_body_osd(),
                                              len(ube_body_verts))
            if _stack is not None and len(_stack):
                bi = np.where(in_bust)[0]           # bust verts only: this is
                if len(bi):                          # per-slider work, keep it small
                    resid = np.zeros(len(bi))
                    jb, kb, nb = jj[bi], keep[bi], nrm0[bi]
                    ub = ui[bi]
                    for dm in _stack:
                        du = (dm[ub].astype(np.float64) * nb).sum(axis=1)
                        dj = np.einsum("nkj,nj->nk",
                                       dm[jb].astype(np.float64), nb)
                        r = np.where(kb, dj - du[:, None], -np.inf).max(axis=1)
                        np.maximum(resid, r, out=resid)
                    # Charge the residual only where a poke can actually
                    # happen -- weighted by the SAME nipple map `req` ramps on.
                    # Applied flat across the bust band it costs the whole torso
                    # (measured: fit 0.496 -> 0.751u) to protect a tip that is a
                    # fraction of it, which is the overinflation this project
                    # keeps re-learning.
                    req = req.copy()
                    w_nip = np.clip(nipw[bi] / max(_nc().BUST_MORPH_RESIDUAL_NIPW,
                                                   1e-6), 0.0, 1.0)
                    req[bi] += w_nip * np.clip(resid, 0.0,
                                               _nc().BUST_MORPH_RESIDUAL_MAX)
        # #back-morph-residual: the SAME charge on the upper back, which the
        # nipple gate above charges nothing. Rear-facing by the body NORMAL, not
        # by a height band alone -- that is the mistake `bust_z` still carries.
        in_back = np.zeros(len(ui), dtype=bool)
        req_back = None
        w_back = None
        if _nc().BACK_MORPH_RESIDUAL:
            _bx = ube_body_verts[ui][:, 0]
            in_back = ((body_z >= _nc().BACK_RESIDUAL_Z[0])
                       & (body_z <= _nc().BACK_RESIDUAL_Z[1])
                       & (nrm0[:, 1] < _nc().BACK_RESIDUAL_NY)
                       & (np.abs(_bx) < _nc().BACK_RESIDUAL_HALF_X))
            # #back-residual-feather: ramp the charge to zero at the zone edge so
            # no triangle straddles a displacement step. Interior stays 1.0, so
            # this can only REDUCE the charge, never extend it past `in_back`.
            if _nc().BACK_RESIDUAL_FEATHER > 0.0:
                _fz = max(_nc().BACK_RESIDUAL_FEATHER, 1e-6)
                _wz = np.minimum(
                    np.clip((body_z - _nc().BACK_RESIDUAL_Z[0]) / _fz, 0.0, 1.0),
                    np.clip((_nc().BACK_RESIDUAL_Z[1] - body_z) / _fz, 0.0, 1.0))
                _fn = max(_nc().BACK_RESIDUAL_FEATHER_NY, 1e-6)
                _wn = np.clip((_nc().BACK_RESIDUAL_NY - nrm0[:, 1]) / _fn, 0.0, 1.0)
                _fx = max(_nc().BACK_RESIDUAL_FEATHER_X, 1e-6)
                _wx = np.clip(
                    (_nc().BACK_RESIDUAL_HALF_X - np.abs(_bx)) / _fx, 0.0, 1.0)
                w_back = _wz * _wn * _wx
            if in_back.any():
                _stack = _cached_body_morph_stack(_find_ube_body_osd(),
                                                  len(ube_body_verts))
                if _stack is not None and len(_stack):
                    ki = np.where(in_back)[0]
                    resid_b = np.zeros(len(ki))
                    jb, kb, nb2 = jj[ki], keep[ki], nrm0[ki]
                    ub2 = ui[ki]
                    for dm in _stack:
                        du = (dm[ub2].astype(np.float64) * nb2).sum(axis=1)
                        dj = np.einsum("nkj,nj->nk",
                                       dm[jb].astype(np.float64), nb2)
                        r = np.where(kb, dj - du[:, None], -np.inf).max(axis=1)
                        np.maximum(resid_b, r, out=resid_b)
                    # SEPARATE ARRAY, NOT `req`. `req` is handed whole to
                    # `_surface_deficit` below, which evaluates per garment
                    # TRIANGLE -- so a triangle in the bust band that happens to
                    # own one back-band vertex would read that vertex's inflated
                    # requirement and demand a push for it. That pass's own
                    # docstring records where this leads: letting points that the
                    # triangle does not cover demand a push "inflated the whole
                    # chest (0.434 -> 2.297u)".
                    # Measured with the shared array: `hide-collider` gained 15-17
                    # upper-chest clipping verts FROM ZERO on every preset, at
                    # 3.14u of travel that capping the back push did not budge --
                    # because the push was never the back charge's, it was the
                    # surface pass acting on a requirement the back charge wrote.
                    req_back = req.copy()
                    req_back[ki] += np.clip(resid_b, 0.0,
                                            _nc().BACK_MORPH_RESIDUAL_MAX)
        # pull IN only as far as the general conform wanted AND no closer than
        # `req` over the worst neighbour; push OUT if the nipple would poke.
        move = np.where(in_bust, np.maximum(move, req - worst), move)
        # The back charge is applied SEPARATELY and CAPPED, so bust behaviour is
        # bit-for-bit what it was -- the bust line above is untouched, and a vert
        # in both bands takes the larger of the two, with only the back half
        # bounded. See BACK_MOVE_MAX for what the uncapped version did.
        if req_back is not None and in_back.any():
            _move_pre = move.copy()
            # MEASURE FIRST, act only if there is enough to gain, and SAY what
            # was done. `minimum_push` is conditional by construction because a
            # census found only 6% of pieces need it; this charge fired on every
            # in-band vert regardless, which is how it reached a piece whose back
            # improved by 3.5 verts while disturbing its front (hide-collider).
            _deficit = np.clip(req_back - worst, 0.0, _nc().BACK_MOVE_MAX)
            _hit = int((_deficit > _nc().BACK_MIN_DEFICIT).sum())
            if _hit < _nc().BACK_MIN_VERTS:
                if _nc().BACK_RESIDUAL_VERBOSE:
                    print(f"  [back-residual] {_hit} vert(s) over "
                          f"{_nc().BACK_MIN_DEFICIT}u -- under the {_nc().BACK_MIN_VERTS}"
                          f" floor, skipped")
            else:
                # (a SURFACE-deficit rule for the back -- measuring the deficit against the garment surface, not its vertices -- was measured worse than this vertex rule and removed; refuted, see git history.)
                _raised = np.maximum(move, _deficit)
                if _nc().BACK_BOUND_EDIT:
                    _raised = np.minimum(_raised, move + _nc().BACK_MOVE_MAX)
                if w_back is not None:
                    # FEATHER THE EDIT, not the requirement. Scaling
                    # `_deficit` instead leaves `max(move, 0)` in force where
                    # the weight is zero, so the charge still cancels
                    # conform's pull-in at exactly the edge the feather
                    # exists to leave alone: measured, feather 0 and feather
                    # 1e-9 differed on 330 verts, which is the floor, not the
                    # ramp. Blending the EDIT is continuous by construction
                    # -- w=1 is the flat behaviour, w=0 is no change at all.
                    # `np.where` on w==1 rather than blending everywhere:
                    # `move + 1.0*(raised - move)` is ALGEBRAICALLY `raised`
                    # but not bit-identical to it, and this chain amplifies
                    # that. Measured, blending every vert at an effectively
                    # zero-width ramp: 529 verts differed from the flat arm,
                    # median 0.0010u but reaching 0.2546u once the layer and
                    # anti-poke passes made discrete decisions on the
                    # perturbed input. The zone interior must be untouched,
                    # not merely equal.
                    _raised = np.where(
                        w_back >= 1.0, _raised,
                        move + w_back * (_raised - move))
                move = np.where(in_back, _raised, move)
                _applied = int((_deficit > 0.0).sum())
                _mx = float(_deficit.max())
                if _nc().BACK_RESIDUAL_VERBOSE:
                    print(f"  [back-residual] {_applied} vert(s), deficit over "
                          f"{_hit} in-band, max {_mx:.2f}u (vertex)")
        # #bust-morph-chord (see the constants): the term the two-vertex residual
        # above cannot contain -- the garment SURFACE between three correct
        # vertices interpolates linearly across a body that curves. Added to
        # `req` BEFORE the surface test below, so the existing per-triangle
        # machinery is what delivers the push; this only changes the number it is
        # asked for. Charged where the body would come through, NOT scaled by
        # nipple weight: the reported defect includes the UNDER-curve, which
        # `_body_nipple_weight` reads ~0 on by its own docstring.
        if _nc().BUST_MORPH_CHORD and tris is not None:
            _stack_c = _cached_body_morph_stack(_find_ube_body_osd(),
                                                len(ube_body_verts))
            _chord = _bust_morph_chord_req(
                cur_cloth, tris, ui, ube_body_verts, ube_body_normals,
                in_bust, bust_z, ube_tree, _stack_c)
            if _chord is not None:
                # ADDED to the donor charge, not maxed with it. Taking the
                # LARGER of the two was built on the theory that they
                # double-charge the same headroom -- principled, and REFUTED by
                # measurement: it saved 0.029u of standoff (+0.066 -> +0.037u
                # median) and cost 1.6 points of clip across 5 arms, giving back
                # a third of the win on the reported piece (Punk 1.059 -> 1.441,
                # Alenye 4.934 -> 5.796). The extra push earns its keep. Do not
                # re-derive this from first principles without re-measuring.
                req = req + _chord
        # #bust-surface-req (see the constants): the same requirement, evaluated
        # against the garment SURFACE instead of its vertices. Per triangle, over
        # the body points that project INSIDE it, so a point off to the side --
        # which the surface does not cover -- cannot demand a push.
        if _nc().BUST_SURFACE_REQ and tris is not None:
            need = _surface_deficit(
                cur_cloth, tris, ube_body_verts, ube_body_normals,
                in_bust, req, ube_tree, bust_z)
            if need is not None:
                move = np.where(need > 0.0, np.maximum(move, need), move)
    near = (sd < max_body_dist) & (ud < max_body_dist)
    move = np.where(near, np.clip(move, -max_pull, max_push_out), 0.0)
    disp = ube_body_normals[ui] * move[:, None]
    # #conform-fold-guard: this pass is per-vertex by construction, so nothing
    # above stops two neighbours crossing through each other. Clamp last, after
    # every contribution (pull-in, bust push-out, surface deficit) is in `disp`,
    # so the guard sees the motion that will actually be applied.
    # #conform-stretch-field: relax the field over the garment's own adjacency
    # BEFORE the fold guard, so the guard judges the field that will actually be
    # applied and still has the last word if both are on.
    if _nc().CONFORM_STRETCH_FIELD and tris is not None:
        disp = _relax_conform_field(
            cur_cloth, disp, move, ube_body_normals[ui], tris,
            body_tree=ube_tree, body_verts=ube_body_verts,
            body_normals=ube_body_normals)
    _pre_guard = disp.copy() if _nc().BACK_DUMP_DISP else None
    if _nc().CONFORM_FOLD_GUARD and tris is not None:
        disp = _damp_to_avoid_inversion(cur_cloth, disp, tris)
    if _pre_guard is not None:
        # The guard is the only step here that is NOT per-vertex: it damps whole
        # triangles, so a displacement change inside the back band can move a
        # vertex that was never in the band. `move` shows 0 out-of-band edits,
        # which is why the transmitter has to be looked for after it.
        _nc()._dump_conform_disp(_pre_guard, disp, in_back)
    return (cur_cloth + disp).astype(np.float32)

def _surface_deficit(cur_cloth, tris, ube_body_verts, ube_body_normals,
                     in_bust, req, ube_tree, bust_z, max_push=None):
    """Per-vertex push needed so the garment SURFACE meets `req` over the body.

    Region-agnostic despite the parameter names: `in_bust`/`bust_z` are simply
    the mask and height band to evaluate, and the back charge passes its own.
    Formerly `_bust_surface_deficit`; renamed when the second caller arrived,
    because one surface rule with two call sites beats two implementations that
    drift apart -- the failure mode this project keeps re-learning.

    The bust rule elsewhere asks whether each garment VERTEX stands `req` clear.
    That is not the defect: the tightest point sits in a triangle interior, and a
    surface can sag 0.855u below vertices that all pass. This asks the question
    the defect is in -- for every garment triangle in the bust band, how far the
    body points that project INSIDE it rise above the surface -- and returns the
    outward push each vertex needs to fix its worst triangle.

    Inside-ness matters: a body point beside the triangle is not covered BY that
    triangle, and letting it demand a push is how an earlier attempt inflated the
    whole chest (0.434 -> 2.297u). Returns None when there is nothing to do.
    """
    try:
        t = np.asarray(tris, dtype=np.int64)
        if t.ndim != 2 or t.shape[1] != 3 or not len(t):
            return None
        n = len(cur_cloth)
        t = t[(t >= 0).all(axis=1) & (t < n).all(axis=1)]
        if not len(t):
            return None
        sel = np.where(in_bust[t].any(axis=1))[0]
        if not len(sel):
            return None
        t = t[sel]
        A, B, C = cur_cloth[t[:, 0]], cur_cloth[t[:, 1]], cur_cloth[t[:, 2]]
        fn = np.cross(B - A, C - A)
        fl = np.linalg.norm(fn, axis=1, keepdims=True)
        ok = fl[:, 0] > 1e-12
        if not ok.any():
            return None
        fn = np.divide(fn, np.where(fl > 1e-12, fl, 1.0))
        kb = int(min(_nc().BUST_SURFACE_K, len(ube_body_verts)))
        _d, bidx = ube_tree.query((A + B + C) / 3.0, k=kb)
        if kb == 1:
            bidx = bidx[:, None]
        P = ube_body_verts[bidx]                       # (T, kb, 3)
        bn = ube_body_normals[bidx]
        # Orient each triangle normal OUTWARD using the body's own normal, so a
        # winding-flipped triangle cannot invert the sign of the whole test.
        sgn = np.sign(np.einsum("tj,tkj->tk", fn, bn))
        sgn = np.where(sgn == 0.0, 1.0, sgn)
        nrm = fn[:, None, :] * sgn[:, :, None]         # (T, kb, 3)
        # clearance of the surface plane over each body point
        clear = -np.einsum("tkj,tkj->tk", P - A[:, None, :], nrm)
        # barycentric inside test, in the triangle's own plane
        v0 = (B - A)[:, None, :]
        v1 = (C - A)[:, None, :]
        v2 = P - A[:, None, :]
        d00 = np.einsum("tkj,tkj->tk", v0, v0)
        d01 = np.einsum("tkj,tkj->tk", v0, v1)
        d11 = np.einsum("tkj,tkj->tk", v1, v1)
        d20 = np.einsum("tkj,tkj->tk", v2, v0)
        d21 = np.einsum("tkj,tkj->tk", v2, v1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        v = (d11 * d20 - d01 * d21) / den
        w = (d00 * d21 - d01 * d20) / den
        inside = (v >= -1e-6) & (w >= -1e-6) & (v + w <= 1.0 + 1e-6)
        bz = ube_body_verts[bidx][:, :, 2]
        inband = (bz >= bust_z[0]) & (bz <= bust_z[1])
        reqb = req[t[:, 0]][:, None]                   # the triangle's own need
        deficit = np.where(inside & inband, reqb - clear, -np.inf).max(axis=1)
        deficit = np.where(np.isfinite(deficit), deficit, 0.0)
        deficit = np.clip(deficit, 0.0,
                          _nc().BUST_SURFACE_MAX_PUSH if max_push is None
                          else float(max_push))
        if not (deficit > 0).any():
            return None
        need = np.zeros(n, dtype=np.float64)
        for col in range(3):
            np.maximum.at(need, t[:, col], deficit)
        # NOT feathered. `_smooth_push_field` was tried here on the theory that
        # a raw per-vert scatter creases (the `_LAYER_ORDER_SMOOTH` lesson): it
        # moved a light cuirass's max edge-jump 0.269 -> 0.264u, i.e. nothing,
        # while costing another 0.033u of fit on the piece this exists to fix.
        # The scatter is not what roughens this pass; do not re-add it.
        return need
    except Exception as _e:
        # A raise here used to read as "no surface deficit" at the caller.
        _note_pass_failure("_surface_deficit", _e)
        return None

def _bust_morph_chord_req(cur_cloth, tris, ui, ube_body_verts,
                          ube_body_normals, in_bust, bust_z, ube_tree,
                          morph_stack, k=None, cap=None):
    """#bust-morph-chord: extra clearance the morph CHORD will consume, per vert.

    For each garment triangle in the band, and each body point that projects
    INSIDE it, the surface over that point moves by the barycentric blend of the
    three corners' deltas -- and each corner copies the delta of its own nearest
    body vert, which is what `generate_armor_tri` will do. The body point itself
    moves by its own delta. The difference along the body normal is clearance the
    morph takes away and nothing currently charges for:

        chord = max over sliders, over inside points of
                (delta_m[b] - SUM_i w_i * delta_m[donor_i]) . n_b

    Returns a per-garment-vertex requirement (each vertex takes the worst of the
    triangles it belongs to), or None when there is nothing to charge -- which
    leaves `req` exactly as it was, so the flag is a true no-op when off.

    WHY THE WORST SLIDER RATHER THAN A SUM: this mirrors `#bust-morph-residual`,
    whose sum-of-positive-residuals variant was built and measured WORSE (Punk
    -0.082 -> -0.279u, 138 poking). Presets do add, but the supremum over
    single sliders already exceeds a real preset's demand on the measured piece
    -- the shortfall there was the missing chord term, not the combination rule.
    """
    if morph_stack is None or not len(morph_stack):
        return None
    try:
        t = np.asarray(tris, dtype=np.int64)
        if t.ndim != 2 or t.shape[1] != 3 or not len(t):
            return None
        n = len(cur_cloth)
        t = t[(t >= 0).all(axis=1) & (t < n).all(axis=1)]
        if not len(t):
            return None
        t = t[in_bust[t].any(axis=1)]
        if not len(t):
            return None
        A, B, C = cur_cloth[t[:, 0]], cur_cloth[t[:, 1]], cur_cloth[t[:, 2]]
        kb = int(min(_nc().BUST_SURFACE_K if k is None else k, len(ube_body_verts)))
        _d, bidx = ube_tree.query((A + B + C) / 3.0, k=kb)
        if kb == 1:
            bidx = bidx[:, None]
        P = ube_body_verts[bidx]                       # (T, kb, 3)
        nb = ube_body_normals[bidx]
        # Barycentric coordinates in the triangle's own plane -- the same
        # construction `_surface_deficit` uses, so the two agree on which body
        # points a triangle is responsible for.
        v0 = (B - A)[:, None, :]
        v1 = (C - A)[:, None, :]
        v2 = P - A[:, None, :]
        d00 = np.einsum("tkj,tkj->tk", v0, v0)
        d01 = np.einsum("tkj,tkj->tk", v0, v1)
        d11 = np.einsum("tkj,tkj->tk", v1, v1)
        d20 = np.einsum("tkj,tkj->tk", v2, v0)
        d21 = np.einsum("tkj,tkj->tk", v2, v1)
        den = d00 * d11 - d01 * d01
        den = np.where(np.abs(den) < 1e-12, 1e-12, den)
        wB = (d11 * d20 - d01 * d21) / den
        wC = (d00 * d21 - d01 * d20) / den
        wA = 1.0 - wB - wC
        inside = (wB >= -1e-6) & (wC >= -1e-6) & (wB + wC <= 1.0 + 1e-6)
        bz = P[:, :, 2]
        ok = inside & (bz >= bust_z[0]) & (bz <= bust_z[1])
        if not ok.any():
            return None
        u0, u1, u2 = ui[t[:, 0]], ui[t[:, 1]], ui[t[:, 2]]
        worst = np.full(len(t), -np.inf)
        for dm in morph_stack:
            d = dm.astype(np.float64)
            # the surface point's motion: what the three corners will copy
            interp = (wA[:, :, None] * d[u0][:, None, :]
                      + wB[:, :, None] * d[u1][:, None, :]
                      + wC[:, :, None] * d[u2][:, None, :])
            ch = np.einsum("tkj,tkj->tk", d[bidx] - interp, nb)
            np.maximum(worst, np.where(ok, ch, -np.inf).max(axis=1),
                       out=worst)
        worst = np.where(np.isfinite(worst), worst, 0.0)
        worst = np.clip(worst, 0.0,
                        _nc().BUST_MORPH_CHORD_MAX if cap is None else float(cap))
        if not (worst > 0).any():
            return None
        out = np.zeros(n, dtype=np.float64)
        for col in range(3):
            np.maximum.at(out, t[:, col], worst)
        return out
    except Exception as _e:
        # A raise here used to read as "no chord demand" at the caller.
        _note_pass_failure("_bust_morph_chord_req", _e)
        return None

def _local_edge_length(verts, tris=None, *, src=None, dst=None, lengths=None):
    """Per-vertex LOCAL EDGE LENGTH -- the local scale of the surface.
    #edge-scaled-reach

    Returns `(elen, has)`, or `(None, None)` on any degenerate input. `has` is
    False wherever a vertex carries no real (non-weld) edge; those vertices
    have NO measurable local scale, and a caller must leave them at whatever it
    does today rather than read the 0 as "infinitely fine".

    ONE DEFINITION OF "how fine is the surface here", so the places that ask
    cannot disagree. `_relax_conform_field` computed exactly this inline and
    now calls it. `_reach_iters` deliberately still does NOT: it needs one
    number per shape and takes the median EDGE LENGTH, which is a different
    statistic from the median of the per-vertex means and would change an
    opt-in feature's behaviour with no measurement behind it.

    MEAN of the incident edges, not the median, for two reasons. It is what
    `_relax_conform_field` already computed, so adopting it here is provably
    byte-neutral rather than argued; and on the reported suit the two differ by
    a p50 of 20% (mean 0.496u against median 0.595u), a near-constant offset
    that any reach expressed in units absorbs into its own calibration.

    Pass `src`/`dst`/`lengths` when the caller has already built its adjacency
    (`_welded_edges` returns exactly that pair, both directions) so the scale
    is measured over the SAME notion of "next to" the solve itself uses.
    """
    try:
        v = np.asarray(verts, dtype=np.float64)
        n = len(v)
        if n < 3:
            return None, None
        if src is None or dst is None:
            t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
            if not len(t):
                return None, None
            e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
            e = np.vstack([e, e[:, ::-1]])
            e = e[(e[:, 0] >= 0) & (e[:, 0] < n)
                  & (e[:, 1] >= 0) & (e[:, 1] < n)]
            if not len(e):
                return None, None
            s, d = e[:, 0], e[:, 1]
            L = np.linalg.norm(v[s] - v[d], axis=1)
        else:
            s = np.asarray(src, dtype=np.int64)
            d = np.asarray(dst, dtype=np.int64)
            L = (np.linalg.norm(v[s] - v[d], axis=1) if lengths is None
                 else np.asarray(lengths, dtype=np.float64))
        keep = L > _nc()._REACH_WELD_EPS
        if not keep.any():
            return None, None
        cnt = np.bincount(s[keep], minlength=n).astype(np.float64)
        tot = np.bincount(s[keep], weights=L[keep], minlength=n)
        has = cnt > 0
        if not has.any():
            return None, None
        return np.where(has, tot / np.maximum(cnt, 1.0), 0.0), has
    except Exception:
        return None, None

def _reach_screen(elen, has, reach_u, iters, *, fallback):
    """Per-vertex `lam` whose smoothing decay length is `reach_u` UNITS
    everywhere on the mesh.  #edge-scaled-reach

    `fallback` is used wherever a vertex has no measurable local scale, so this
    can never make a vertex smoother OR stiffer than the caller's own constant
    on geometry it could not measure.

    FLOORED AT `1 / iters`, which is not a fudge: it is the reach the solver
    can actually deliver. A Jacobi sweep is a DIFFUSION step, so `k` sweeps
    spread a response about `sqrt(k)` rings, not `k` -- the same physics the
    `#smooth-reach` note above states for neighbour averaging. Setting
    `sqrt(k) * elen = elen / sqrt(lam)` gives `lam = 1/k`. Below that the solve
    is simply under-converged and the extra reach is never realised.

    MEASURED, because the first cut of this floor was `1 / iters**2` -- the
    limit if a sweep carried information a full ring -- and the end-to-end
    decay test caught it: at `lam` under the true floor a 400-sweep solve on a
    0.1u chain reached 1.30u where the formula promised 2.00u, while the coarse
    chain, whose `lam` was well above its floor, landed exactly. An
    unconverged solve does not fail loudly; it quietly returns a shorter reach
    than the caller asked for.
    """
    base = np.full(len(elen), float(fallback), dtype=np.float64)
    R = float(reach_u)
    if not np.isfinite(R) or R <= 1e-6:
        return base
    lam = np.square(np.asarray(elen, dtype=np.float64) / R)
    lam = np.maximum(lam, 1.0 / max(float(iters), 1.0))
    return np.where(has, lam, base)

def _reach_iters(verts, tris, iters: int) -> int:
    """Ring count that carries the feather the SAME DISTANCE on this mesh.

    Returns `iters` unchanged when disabled, when the verts were not threaded
    through, or on any failure -- never fewer rings than the caller asked for.
    """
    if not _nc().SMOOTH_REACH or verts is None:
        return iters
    try:
        v = np.asarray(verts, dtype=np.float64)
        t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        if len(t) == 0 or len(v) < 3:
            return iters
        e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e = e[(e[:, 0] < len(v)) & (e[:, 1] < len(v))]
        if not len(e):
            return iters
        L = np.linalg.norm(v[e[:, 0]] - v[e[:, 1]], axis=1)
        L = L[L > 1e-9]
        if not len(L):
            return iters
        med = float(np.median(L))
        if not np.isfinite(med) or med <= 1e-9:
            return iters
        ratio = max(_nc()._SMOOTH_REF_EDGE / med, 1.0)
        scale = min(ratio * ratio, float(_nc()._SMOOTH_REACH_MAX))   # diffusion
        return int(max(1, round(int(iters) * scale)))
    except Exception:
        return iters

def _smooth_push_field(push: np.ndarray, needed: np.ndarray, tris,
                       iters: int = ANTIPOKE_SMOOTH_ITERS,
                       blend: float = 0.5, verts=None) -> np.ndarray:
    """Feather an anti-poke push scalar over the armor mesh adjacency (see
    ANTIPOKE_SMOOTH_ENABLED). Each iteration blends toward the neighbor average
    then re-floors at `needed` (the original per-vert requirement), so a poke can
    never reopen; verts with no push near no pushed verts stay exactly 0.
    Returns `push` unchanged on any failure (never worse than no smoothing)."""
    iters = _reach_iters(verts, tris, iters)          # #smooth-reach
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
    except Exception as _e:
        _note_pass_failure("_smooth_push_field", _e)
        return push

def _relax_conform_field(cur, disp, move, normals, tris, *,
                         body_tree=None, body_verts=None, body_normals=None,
                         iters=None, lam=None, tangent_max=None,
                         weld_tol=1e-3):
    """Smooth conform's per-vertex displacement over the SURFACE, then put the
    along-normal component back inside the envelope the pass validated.
    #conform-stretch-field

    Two clamps, and both are load-bearing:

    ALONG-NORMAL is clipped into [min(move,0), max(move,0)] -- the interval
    between zero and exactly what conform asked for, per vertex. So the relaxed
    field can never pull a vert further IN than the pass's own `max_pull`/
    `min_clearance` logic allowed (no new clipping), never push it further OUT
    than `max_push_out` (no new inflation), and never reverse a vert's sign. The
    envelope is inherited rather than restated, which is why this cannot drift
    away from the safety reasoning above it.

    TANGENTIAL is capped at `tangent_max` AND re-tested against the body. The
    cap alone is not enough, and assuming it was cost a deploy: a slide is only
    "along the surface" with respect to ONE vertex's body normal, so across a
    curved region it cuts into the body. Measured on the slim (`_0`) variant of
    the reported piece, where the shoulder is tighter than on `_1`: a 0.5u
    budget drove **4643 of 19641 shoulder verts inside the body**, against 78
    with this pass off, while `_1` improved in the same run. A one-weight check
    would have shipped it.

    So the slide is held to the clearance conform's OWN field would have
    produced -- `body_tree`/`body_verts`/`body_normals` re-queried at the moved
    position, tangential magnitude halved on any vertex that came out worse.
    The floor is inherited rather than restated, exactly as the along-normal
    envelope is. WITHOUT a body to test against, `tangent_max` is forced to
    zero: the along-normal smoothing still works and the unverifiable part is
    simply not taken.

    MONOTONE ON ITS OWN OBJECTIVE, and that is not decoration. Relaxation helps
    a solid garment and HURTS a thin one: measured over a five-piece population,
    a hide cuirass went 89 folds -> 63 and 40 stretched edges -> 6, while the
    same code on a robe's physics chain took a clean shape from 0 folds and 15
    stretched edges to 38 and 627, worst edge 1.68x -> 6.77x. A chain is a few
    verts wide, so its "neighbourhood average" is dominated by verts across the
    strand rather than along it, and smoothing pulls it apart.

    Gating that by shape name would be a per-piece rule and would still be wrong
    on the next thin shape nobody named. Instead every iterate is SCORED on the
    quantity this pass exists to protect -- squared deviation of each edge from
    its length entering conform -- and the best-scoring one wins, with the
    UNMODIFIED requested field as iterate zero. So the result is never worse
    than doing nothing, on any shape, by construction rather than by gate.

    Returns the original `disp` unchanged on any failure or degenerate input: a
    smoothing repair must never be able to cost the pass its fit.
    """
    iters = _nc().CONFORM_STRETCH_ITERS if iters is None else int(iters)
    lam = _nc().CONFORM_STRETCH_LAMBDA if lam is None else float(lam)
    tmax = (_nc().CONFORM_STRETCH_TANGENT_MAX if tangent_max is None
            else float(tangent_max))
    try:
        D = np.asarray(disp, np.float64)
        n = len(D)
        src, dst, deg, live = _nc()._welded_edges(cur, tris, weld_tol)
        if src is None or n == 0:
            return disp

        Nu = np.asarray(normals, np.float64)
        ln = np.linalg.norm(Nu, axis=1, keepdims=True)
        Nu = Nu / np.where(ln > 1e-9, ln, 1.0)
        m = np.asarray(move, np.float64)
        lo, hi = np.minimum(m, 0.0), np.maximum(m, 0.0)

        P = np.asarray(cur, np.float64)
        l0 = np.linalg.norm(P[src] - P[dst], axis=1)
        # #conform-relative-stretch. Work in RELATIVE stretch, not absolute.
        #
        # MEASURED on the reported piece's standing collar, the region the user
        # points at: conform's displacement there is SMOOTH in absolute units --
        # neighbour disagreement p50 0.067u, p90 0.164u, direction angle p50 4
        # degrees, only 40 of 9851 edges pointing >90 degrees apart -- and
        # catastrophic relative to edge length: p90 0.74, max 17.3.
        #
        # The collar is simply the finest geometry on the garment. Median edge
        # 0.385u against the torso's 0.625u, and 15.1% of its edges are under
        # 0.1u against the waist's 7.5%. A 0.164u disagreement is 2.6x stretch
        # across a 0.1u edge and an invisible 26% across a 0.625u one. So the
        # gold trim, the frame around a gem, embroidery -- every finely
        # tessellated detail -- pays many times the relative price of the same
        # absolute error, which is exactly what "the gold is massively
        # distorted" looks like as geometry.
        #
        # Two consequences, both applied below: the cost is scored on
        # (delta / original length), and the Laplacian weights are inverse
        # length so a short edge holds its neighbours harder. Weights are
        # renormalised to sum to the vertex's degree, so `lam` keeps exactly the
        # meaning it had and only the RELATIVE pull among neighbours changes.
        # #conform-adaptive-reach. `lam` is a MASS term: the screened
        # Laplacian's decay length is roughly (edge length / sqrt(lam)), so ONE
        # global lam gives a finely tessellated region a SHORTER world-space
        # reach than a coarse one -- it is smoothed over fewer units of the
        # body exactly where the body's own detail needs it smoothed over more.
        # Scaling lam with the square of the local edge length equalises the
        # world-space reach instead of the graph-step reach.
        #
        # Weld edges are ~0 long and are not geometry, so they are excluded
        # from the local scale or every seam vertex would read as ultra-fine.
        elen, has = _local_edge_length(P, src=src, dst=dst, lengths=l0)
        if elen is None:
            elen = np.zeros(n, dtype=np.float64)
            has = np.zeros(n, dtype=bool)
        med = float(np.median(elen[has])) if has.any() else 0.0
        if _nc().EDGE_SCALED_REACH:
            # #edge-scaled-reach supersedes the relative form below: the reach
            # is stated in UNITS and resolved per vertex, so a uniformly fine
            # shape stops being smoothed over fewer units of body than a coarse
            # one. `lam` remains the fallback wherever a vertex has no
            # measurable local scale.
            lam_v = _reach_screen(elen, has, _nc().REACH_UNITS, iters, fallback=lam)
        elif _nc().CONFORM_STRETCH_ADAPTIVE and med > 1e-6:
            ratio = np.where(has, elen / med, 1.0)
            lam_v = np.clip(lam * ratio * ratio,
                            lam / _nc().CONFORM_STRETCH_REACH_MAX,
                            lam * _nc().CONFORM_STRETCH_REACH_MAX)
        else:
            lam_v = np.full(n, lam, dtype=np.float64)
        denom = deg + lam_v
        denom[~live] = 1.0

        # A REWEIGHTING of the coupling by inverse length DOES NOT HELP -- that
        # is a different idea and it was built, measured, and reverted the same
        # day; see below. This one changes the REACH, not the weights.
        #
        # ...the reweighting attempt, recorded so it is not re-derived --
        # reverted the same day. Scoring the cost as (delta / length) and making
        # the Laplacian weights inverse-length gave, at that collar: conform's
        # stretched-edge COUNT slightly better (>1.5x 537 -> 464, >2x 290 ->
        # 253) and its WORST edge clearly worse (11.26x -> 14.08x), and by the
        # end of the chain worse on everything (>1.5x 488 -> 558, >2x 275 ->
        # 297, worst 11.32 -> 12.28). Holding short edges harder buys their
        # neighbours' slack, and on a rim the neighbours are where it shows.
        # The diagnosis stands and the lever is elsewhere; do not re-derive it.

        # No body to test the slide against -> do not take the slide. The
        # along-normal smoothing is unaffected and needs no such test.
        can_test = (body_tree is not None and body_verts is not None
                    and body_normals is not None)
        if not can_test:
            tmax = 0.0

        def clearance(Q):
            _, i = body_tree.query(Q, k=1)
            return np.einsum('ij,ij->i', Q - body_verts[i], body_normals[i])

        # The floor: whatever clearance conform's OWN field would have left.
        ref_clear = clearance(P + D) if can_test else None

        def clamp(u):
            """Into the envelope conform validated: along-normal between zero
            and exactly what it asked for, tangential on its own budget and
            never at the cost of clearance conform would have kept."""
            a = np.clip(np.einsum('ij,ij->i', u, Nu), lo, hi)
            t = u - Nu * np.einsum('ij,ij->i', u, Nu)[:, None]
            tl = np.linalg.norm(t, axis=1)
            over = tl > max(tmax, 0.0)
            if over.any():
                t[over] *= (tmax / np.maximum(tl[over], 1e-12))[:, None]
            if not can_test or tmax <= 0.0:
                return Nu * a[:, None] + t
            # Halve the slide wherever it costs clearance. Only ever LESS
            # slide, so this cannot introduce motion of its own; the fixed
            # point is a pure along-normal move, which is already validated.
            base = Nu * a[:, None]
            for _ in range(4):
                bad = clearance(P + base + t) < ref_clear - 1e-9
                if not bad.any():
                    break
                t[bad] *= 0.5
            t[clearance(P + base + t) < ref_clear - 1e-9] = 0.0
            return base + t

        def cost(u):
            """What this pass exists to protect: how far the applied field
            drags each edge off the length it had entering conform."""
            Q = P + u
            d = np.linalg.norm(Q[src] - Q[dst], axis=1) - l0
            return float(d @ d)

        # Iterate zero is the UNMODIFIED request, so "no improvement found"
        # returns exactly what conform asked for and this pass is a no-op.
        best_u, best_c = D, cost(D)
        # Screened Jacobi seeded at the requested field. Each sweep replaces a
        # vertex by the (mass-screened) mean of its neighbours, so every
        # iterate stays in the convex hull of the input -- no new extreme, and
        # nothing to diverge.
        u = D.copy()
        for _ in range(int(iters)):
            acc = np.zeros((n, 3))
            for k in range(3):
                np.add.at(acc[:, k], src, u[dst, k])
            un = (acc + lam_v[:, None] * D) / denom[:, None]
            un[~live] = D[~live]
            u = un
            cand = clamp(u)
            c = cost(cand)
            if c < best_c:
                best_u, best_c = cand, c
        return best_u
    except Exception as _pe:
        _note_pass_failure("_relax_conform_field", _pe)
        return disp

def _solve_clearance_field(verts, normals, need, tris, *,
                           max_push=3.0, lam=None, iters=None,
                           tol=1e-4, weld_tol=1e-3):
    """One minimum-stretch displacement that clears the body.  #clearance-field

    Returns (u, stats): `u` is the per-vertex displacement (n, 3); `stats` lets
    the caller ASSERT THE PASS FIRED (n_need / n_moved / max_move / iters /
    converged). On ANY failure returns a zero field and stats['ok']=False, so a
    solver error is a NO-OP the caller falls through -- never a silent loss of
    clearance.

    `need` is the outward gain each vertex must achieve ALONG its own body
    normal -- exactly the capped `req - worst` the anti-poke already computed.
    The constraint is one-sided: `n_i . u_i >= need_i`. Verts with need_i <= 0
    are free; the `lam` mass term decays their motion to zero away from the
    active set, so a far drape is not dragged out by the harmonic tail.

    WELD (a component is not an object): coincident verts at a UV seam are
    separate indices with identical position, and with no edge between them the
    Laplacian never couples them, so the seam re-diverges. Group by position at
    `weld_tol` and chain intra-group edges, so one SURFACE smooths as one.
    """
    lam = _nc().CLEARANCE_FIELD_LAMBDA if lam is None else float(lam)
    iters = _nc().CLEARANCE_FIELD_ITERS if iters is None else int(iters)
    V = np.asarray(verts, np.float64)
    n = len(V)
    stats = {"ok": False, "n_need": 0, "n_moved": 0,
             "max_move": 0.0, "iters": 0, "converged": False}
    try:
        N = np.asarray(normals, np.float64)
        s = np.clip(np.asarray(need, np.float64), 0.0, float(max_push))
        stats["n_need"] = int((s > 1e-9).sum())
        T = np.asarray(tris, np.int64).reshape(-1, 3)
        if n < 3 or not len(T) or not np.any(s > 1e-9):
            return np.zeros((n, 3)), stats
        # Unit normals; a zero-length normal makes its own constraint inert
        # rather than injecting a NaN direction.
        Nl = np.linalg.norm(N, axis=1, keepdims=True)
        Nu = N / np.where(Nl > 1e-9, Nl, 1.0)

        src, dst, deg, live = _nc()._welded_edges(V, T, weld_tol)
        if src is None:
            return np.zeros((n, 3)), stats
        denom = deg + lam
        denom[~live] = 1.0

        # Projected Jacobi. Each step: relax toward the screened neighbour
        # average (the gradient step of the stretch+mass energy), then project
        # each vert onto its half-space `n_i . u_i >= s_i`. The projection only
        # ever RAISES the along-normal component to the floor, never pins it, so
        # a vert is free to sit above the floor -- the lift-off.
        u = np.zeros((n, 3))
        for it in range(int(iters)):
            acc = np.zeros((n, 3))
            for k in range(3):
                np.add.at(acc[:, k], src, u[dst, k])
            un = acc / denom[:, None]
            un[~live] = 0.0
            along = np.einsum('ij,ij->i', un, Nu)
            short = np.clip(s - along, 0.0, None)
            un = un + Nu * short[:, None]
            step = float(np.linalg.norm(un - u, axis=1).max())
            u = un
            stats["iters"] = it + 1
            if step < float(tol):
                stats["converged"] = True
                break
        mv = np.linalg.norm(u, axis=1)
        stats["n_moved"] = int((mv > 1e-6).sum())
        stats["max_move"] = float(mv.max())
        stats["ok"] = True
        return u, stats
    except Exception as _pe:
        _note_pass_failure("_solve_clearance_field", _pe)
        return np.zeros((n, 3)), stats

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
    morph_differential: "np.ndarray | None" = None,
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
    rear_standoff_feather: float = REAR_STANDOFF_FEATHER,
    rear_standoff_feather_ny: float = REAR_STANDOFF_FEATHER_NY,
    calf_standoff: float = CALF_STANDOFF,
    calf_standoff_z_lo: float = CALF_STANDOFF_Z_LO,
    calf_standoff_z_hi: float = CALF_STANDOFF_Z_HI,
    thigh_standoff: float = THIGH_STANDOFF,
    thigh_standoff_z_lo: float = THIGH_STANDOFF_Z_LO,
    thigh_standoff_z_hi: float = THIGH_STANDOFF_Z_HI,
    req_extra: float = 0.0,
    tris=None,
    smooth_iters: int = ANTIPOKE_SMOOTH_ITERS,
    src_armor_verts: "np.ndarray | None" = None,
    src_body_verts: "np.ndarray | None" = None,
    src_body_normals: "np.ndarray | None" = None,
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
        # #clearance-differential (see the constant): the amp ramp above pays for
        # GROWTH; clipping is caused by the DIFFERENTIAL. Charge the clearance
        # the worst slider actually takes away, as a MONOTONE FLOOR so no zone
        # can lose room it has today.
        #
        # Indexed at `nearest`, NOT maxed over the neighbourhood again: the array
        # is already `max over neighbours j of (d[j]-d[i]).n_i`, so a second
        # neighbourhood max would dilate a value that has already been dilated.
        # `amp` needs that max because it is a bare per-vert quantity; this does
        # not.
        if morph_differential is not None and len(morph_differential) == len(bv):
            _diff = np.asarray(morph_differential, dtype=np.float64)[nearest]
            req = np.maximum(req, np.minimum(adaptive_base + _diff,
                                             adaptive_cap))
        # BUST FLOOR. The adaptive ramp REPLACED the bust ramp below, and its cap
        # sat under the bust target -- so switching adaptive clearance on gave the
        # breast LESS room than the fixed path it replaced, on the one zone that
        # morphs most. Measured on the UBE body: breast morph amplitude is 3.48
        # mean / 5.34 max, so the ramp wants 0.95-1.32u, but adaptive_cap=0.8
        # clipped 72% of breast verts -- landing at 0.73u mean where the legacy
        # bust target was 1.0u. Body then sits proud of the cuirass at the breast,
        # at rest and in motion. Apply the bust ramp as a FLOOR, exactly the way
        # rear_standoff does below (np.maximum, never stacks) so adaptive can add
        # room but can never take the bust below what the fixed path guaranteed.
        if body_nipple is not None and len(body_nipple) == len(bv):
            z = bv[nearest][:, 2]
            nipw = np.asarray(body_nipple, dtype=np.float64)[nearest]
            # Gate on NIPPLE WEIGHT, not just the z-band. `bust_z` is a height
            # range with no front/back test, so a vert on the BACK at chest height
            # is "in bust" too -- flooring it at flat_clear would re-impose a fixed
            # standoff on the static zones adaptive clearance exists to keep tight
            # (measured: it pushed a fur cuirass's back from 0.74u to 1.02u).
            # Nipple weight is non-zero only on the bust itself, so it confines the
            # floor to the geometry that actually needs it.
            in_bust = ((z >= bust_z[0]) & (z <= bust_z[1])
                       & (nipw > 0.0) & (nrm[:, 1] > 0.0))
            bust_req = np.clip(flat_clear + nipw * nipple_gain,
                               flat_clear, bust_clear)
            req = np.where(in_bust, np.maximum(req, bust_req), req)
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
        # FEATHERED, not a binary zone. A hard-edged push field creases the
        # garment at its own boundary: measured on a heavy cuirass, push-out
        # along the body normal collapsed +1.13 -> +0.30 -> -0.50 across the
        # zone's upper edge, and the user saw the flap "curve at about a 60
        # degree angle instead of being flat" at exactly that height. Ramping
        # the FLOOR to zero over `rear_standoff_feather` units of body-z (and
        # over the rear-facing normal test) removes the step while leaving the
        # zone interior identical. It can only LOWER the floor near the edges,
        # never raise it, so it cannot push anything further into the body.
        # FEATHER ONCE: this is the single feather for this field.
        # #rear-standoff-feather
        # Ramp INWARD from the zone edge. Ramping OUTWARD was tried and is
        # strictly worse -- it restored the crease (buttbend 78 -> 99) AND made
        # clipping worse (30 -> 35 verts behind the skin), because pushing more
        # verts out along their own body normal lands some of them against a
        # different body region. Measured, not assumed.
        _fz = max(float(rear_standoff_feather), 1e-6)
        wz = np.minimum(
            np.clip((bz - rear_standoff_z_lo) / _fz, 0.0, 1.0),
            np.clip((rear_standoff_z_hi - bz) / _fz, 0.0, 1.0))
        _fn = max(float(rear_standoff_feather_ny), 1e-6)
        wn = np.clip((rear_standoff_ny - nrm[:, 1]) / _fn, 0.0, 1.0)
        req = np.maximum(req, rear_standoff * wz * wn)
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
        if _nc().THIGH_STANDOFF_MEDIAL:
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

    # #authored-antipoke. Everything above builds the requirement from
    # clearance rules alone -- it has never known where the AUTHOR put the
    # vertex, so a garment already sitting at the author's spacing is pushed out
    # to a flat floor anyway. The multi-piece pass ledger (18 shapes) makes that
    # the worst entry in the chain: this is the ONLY pass that moves the mesh
    # AWAY from the author's fit (+0.0537u median) and it is also the largest
    # single roughness source in the chain (+2.00 dihedral, more than conform or
    # inflate), adding ~3 spikes per shape for later passes to clean up.
    #
    # Same construction as `#authored-inflate`, monotone for the same reason:
    # the requirement computed above is the CEILING, so this can only lower a
    # requirement, never raise one. No vertex is pushed further than today, so
    # over-inflation cannot worsen and the whole risk surface is "did the floor
    # come out too low" -- which the census clearance counters measure directly.
    #
    # It relaxes only where BOTH hold: the author had this vertex tighter than
    # the flat floor, AND the body does not grow much here. Where the body
    # morphs outward -- the bust, belly and butt this pass exists for --
    # `amp_room` holds the requirement up.
    if (_nc().AUTHORED_ANTIPOKE and src_armor_verts is not None
            and src_body_verts is not None and src_body_normals is not None):
        try:
            sa = np.asarray(src_armor_verts, dtype=np.float64)
            sb = np.asarray(src_body_verts, dtype=np.float64)
            sn = np.asarray(src_body_normals, dtype=np.float64)
            if sa.shape == v.shape and sb.shape == sn.shape and len(sb):
                _, si = _nc()._authored_src_tree(sb).query(sa, k=1, workers=-1)
                authored = np.maximum(
                    np.einsum('ij,ij->i', sa - sb[si], sn[si]), 0.0)
                amp_room = np.zeros(len(v))
                if morph_amplitude is not None and len(morph_amplitude):
                    _amp = np.asarray(morph_amplitude, dtype=np.float64)
                    if len(_amp) > int(nearest.max()):
                        amp_room = np.minimum(_amp[nearest],
                                              _nc().AUTHORED_INFLATE_AMP_CAP)
                floor = np.maximum(authored, _nc().ARMOR_TO_SKIN_BUFFER + amp_room)
                req = np.minimum(req, np.maximum(floor, worst))
        except Exception as e:
            # A silently-failed floor here reads as "the requirement was
            # already satisfied" and would ship as a quiet loss of clearance.
            _note_pass_failure("clear_armor_outside_body/authored-floor", e)

    push = np.clip(req - worst, 0.0, max_push)            # push OUT only
    # #clearance-field: solve ONE minimum-stretch displacement that meets every
    # vertex's outward requirement, instead of pushing each vertex along its own
    # (diverging) body normal and then feathering/re-projecting the damage. `push`
    # is the per-vertex requirement -- the constraint's lower bound. This REPLACES
    # both the scalar feather and the vector re-projection below (which only ever
    # post-process the diverging push). Own `need` var so the OFF path is
    # untouched and byte-identical. Falls through on solver failure.
    if _nc().CLEARANCE_FIELD_SOLVE and tris is not None:
        need = np.where(dd[:, 0] < max_body_dist, push, 0.0)  # far drapes free
        u, _cf = _solve_clearance_field(v, nrm, need, tris, max_push=max_push)
        if _cf.get("ok"):
            return (v + u).astype(np.float32)
    if _nc().ANTIPOKE_SMOOTH_ENABLED and tris is not None and smooth_iters > 0:
        # Feather the push over the mesh so per-vert normal/magnitude jumps
        # don't crinkle the cloth; floored at the raw push (never reopens).
        # Gated on its own flag: `tris` may now be present only for the
        # #clearance-field solve above (which returns early on success), so this
        # scalar feather must not switch itself on off the back of that.
        push = np.clip(_smooth_push_field(push, push, tris, smooth_iters, verts=verts),
                       0.0, max_push)
    push = np.where(dd[:, 0] < max_body_dist, push, 0.0)  # leave far drapes alone
    push_vec = nrm * push[:, None]
    return (v + push_vec).astype(np.float32)

_SOFTCLOTH_BUST_CLEAR = _knob("CBBE2UBE_SOFTCLOTH_BUST_CLEAR", 1.8)

_SOFTCLOTH_BUTT_CLEAR = _knob("CBBE2UBE_SOFTCLOTH_BUTT_CLEAR", 1.5)

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
    # Each cloth vert's OWN nearest body vert. Hoisted from the bottom of
    # this function (it was built there to aim the push) because
    # #softcloth-own-plane needs the same pairing to MEASURE the deficit.
    btree = cKDTree(bv)
    _, ib = btree.query(v)
    own_clear = np.einsum('ij,ij->i', v - bv[ib], bn[ib])
    push = np.zeros(len(v))
    # #softcloth-seated-cap. `clear` is BUST JIGGLE HEADROOM (1.8u). Applied as
    # `need = clear - standoff` it does not merely cover a body that is poking
    # through -- it LIFTS cloth that is already seated correctly. Measured on a
    # reported robe's shoulder straps, against the author's own build:
    #
    #     author strap -> its body   median 0.32u  p90 0.48u  MAX 0.83u
    #     ours          -> UBE body  median 0.34u  p90 2.24u  MAX 2.55u
    #
    # The median is right; the TAIL is the defect the user sees as bumps along
    # the collarbone. A strap the author holds within 0.83u of the body has no
    # jiggle headroom to preserve, and inflating it to 1.8u is simply wrong
    # there -- headroom is a BUST property, not a property of every vert the
    # bust band's radius happens to reach.
    #
    # ON, a vert already OUTSIDE the body is capped at `seated_cap` of extra
    # lift instead of being raised to `clear`; a vert the body genuinely
    # penetrates is still pushed out to cover, which is what the pass is for.
    # Strictly reduces push, so it can only lower the standoff tail.
    _seated = _nc().SOFTCLOTH_SEATED_CAP if _nc().SOFTCLOTH_SEATED_CAP > 0 else None
    for band, clear in ((breast, float(bust_clear)), (butt, float(butt_clear))):
        for bi in np.where(band & (poke > 0.1))[0]:
            for av in atree.query_ball_point(bv[bi], radius):
                need = clear - float((v[av] - bv[bi]) @ bn[bi])
                if _nc().SOFTCLOTH_OWN_PLANE:
                    # #softcloth-own-plane: the deficit belongs on the
                    # plane the push is applied along. A vert already
                    # standing `clear` proud of the body NEAREST IT is not
                    # one the body is poking through, whatever a body vert
                    # up to `radius` away says about it.
                    if own_clear[av] >= clear:
                        continue
                    need = min(need, clear - float(own_clear[av]))
                if _seated is not None and own_clear[av] > 0.0:
                    # already outside the body: cover, do not inflate
                    need = min(need, _seated)
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
        except Exception as _pe:
            _note_pass_failure("_smooth_push_field", _pe)
    # `ib` was computed above; push each vert along its own body normal.
    #
    # #softcloth-smooth-direction. `push` is a SCALAR and is smoothed above, but
    # the DIRECTION `bn[ib]` is per-vertex, so neighbouring cloth verts move
    # along diverging body normals even where the magnitude is already uniform.
    # On a thin feature -- a shoulder strap crossing a curving chest -- that
    # differential is what buckles the surface, which is the same mechanism
    # `#coherence-rigid` documents ("a strip under 3u thick takes ONE
    # displacement for the whole strip"). Smoothing the DIRECTION field over the
    # mesh, not just the magnitude, removes the differential while leaving the
    # magnitude the smoother already agreed on.
    #
    # PUSH-OUT ONLY IS PRESERVED: the smoothed direction is re-projected onto
    # each vert's own body normal and the component clipped at >= 0, so a vert
    # can never be pulled IN by its neighbours' directions -- the property that
    # makes this shippable next to a clearance pass.
    dirs = bn[ib]
    if _nc().SOFTCLOTH_SMOOTH_DIR and tris is not None and smooth_iters > 0:
        try:
            t_ = np.asarray(tris, np.int64)
            sm = np.empty_like(dirs)
            for _c in range(3):
                sm[:, _c] = _smooth_push_field(
                    dirs[:, _c].copy(), dirs[:, _c].copy(), t_, smooth_iters)
            ln = np.linalg.norm(sm, axis=1, keepdims=True)
            ok = ln[:, 0] > 1e-6
            sm[ok] = sm[ok] / ln[ok]
            sm[~ok] = dirs[~ok]
            # keep it a PUSH: never let the smoothed direction oppose the vert's
            # own outward normal.
            along = np.einsum('ij,ij->i', sm, dirs)
            sm[along < 0.0] = dirs[along < 0.0]
            dirs = sm
        except Exception as _de:
            _note_pass_failure("_softcloth_smooth_direction", _de)
    return (v + dirs * push[:, None]).astype(np.float32)

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

def _weld_components(verts, tris, tol: float = 1e-3):
    """Connected components over WELDED topology. Welding first is required:
    a seam splits one panel into many and a COMPONENT is not an OBJECT until
    coincident verts are joined (#component-is-not-an-object). Returns a label
    array, or None on failure."""
    try:
        from scipy.spatial import cKDTree as _KD
        v = np.asarray(verts, dtype=np.float64)
        t = np.asarray(tris, dtype=np.int64)
        if len(v) < 3 or t.size == 0:
            return None
        parent = np.arange(len(v))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[max(rx, ry)] = min(rx, ry)

        for x, y in _KD(v).query_pairs(tol, output_type='ndarray'):
            union(int(x), int(y))
        for tri in t:
            union(int(tri[0]), int(tri[1]))
            union(int(tri[1]), int(tri[2]))
        return np.array([find(i) for i in range(len(v))])
    except Exception:
        return None

def _locally_rigid_panel(P, Q, k: int):
    """Moving-least-squares rigid fit: every vertex takes the rigid motion of its
    own K-nearest neighbourhood, not of the whole panel.

    P = source positions, Q = current (conformed) positions, same length.
    Returns the locally-rigid target for every vertex.

    The neighbourhood is defined on P (the source) so the support of each fit is
    fixed by the authored geometry and cannot drift as the panel moves -- the
    same reason the cross-shape skin match clusters in a stable space.

    Batched: one stacked SVD over (n, 3, 3) covariances, so this costs a few
    milliseconds on a 4k panel rather than a Python loop of 4k SVDs.
    """
    from scipy.spatial import cKDTree
    P = np.asarray(P, dtype=np.float64)
    Q = np.asarray(Q, dtype=np.float64)
    n = len(P)
    kk = int(max(4, min(int(k), n)))
    _, nbr = cKDTree(P).query(P, k=kk)
    if nbr.ndim == 1:
        nbr = nbr[:, None]
    Pn, Qn = P[nbr], Q[nbr]                      # (n, k, 3)
    Pm, Qm = Pn.mean(1), Qn.mean(1)
    Pc, Qc = Pn - Pm[:, None, :], Qn - Qm[:, None, :]
    H = np.einsum('nki,nkj->nij', Pc, Qc)        # (n, 3, 3)
    U, _S, Vt = np.linalg.svd(H)
    # Reflection guard, per vertex: a neighbourhood that is nearly planar can
    # otherwise solve a mirror, which would fold the surface.
    det = np.sign(np.linalg.det(np.einsum('nij,njk->nik',
                                          Vt.transpose(0, 2, 1),
                                          U.transpose(0, 2, 1))))
    D = np.zeros((n, 3, 3))
    D[:, 0, 0] = 1.0
    D[:, 1, 1] = 1.0
    D[:, 2, 2] = det
    R = np.einsum('nij,njk,nkl->nil', Vt.transpose(0, 2, 1), D,
                  U.transpose(0, 2, 1))
    return np.einsum('nij,nj->ni', R, P - Pm) + Qm

def _partial_rigid_panels(src_v, dst_v, tris, strength: float,
                          skip_mask=None, min_verts: int = 24):
    """Give each rigid PANEL back `strength` of its lost rigidity.

    For every connected component: fit the best rigid transform source->current
    (Procrustes, no scale) and move the panel a fraction of the way from its
    deformed state toward that rigid result. `strength` 0 leaves the mesh alone,
    1 makes each panel fully rigid.

    `skip_mask` marks vertices that must not be touched (simulated cloth).
    Returns (verts, panels_touched, worst_deform_before). Best-effort: returns
    the input unchanged on any failure.
    """
    try:
        s = np.asarray(src_v, dtype=np.float64)
        d = np.asarray(dst_v, dtype=np.float64)
        if strength <= 0 or s.shape != d.shape or len(s) < 3:
            return dst_v, 0, 0.0
        lab = _weld_components(s, tris)
        if lab is None:
            return dst_v, 0, 0.0
        out = d.copy()
        touched = 0
        worst = 0.0
        for u in np.unique(lab):
            m = lab == u
            if int(m.sum()) < max(12, int(min_verts)):
                continue
            # SIMULATED cloth is excluded per VERTEX, not per component. A
            # cuirass panel routinely carries a small chain-driven flap -- on the
            # reported piece, 125 of 4084 verts -- and skipping the whole
            # component for it disqualified the 2062-vert bust panel that was
            # the entire point. Fit the panel from its RIGID verts only, move
            # only those, and leave every simulated vert exactly where the
            # solver expects it (#mixed-cloth-clearance draws the same line).
            free = m if skip_mask is None else (m & ~skip_mask)
            if int(free.sum()) < max(12, int(min_verts)):
                continue                       # mostly cloth -- not a plate
            P, Q = s[free], d[free]
            Pc = P - P.mean(0)
            Qc = Q - Q.mean(0)
            U, S, Vt = np.linalg.svd(Pc.T @ Qc)
            det = np.sign(np.linalg.det(Vt.T @ U.T))
            R = Vt.T @ np.diag([1.0, 1.0, det]) @ U.T
            rigid = (R @ Pc.T).T + Q.mean(0)   # panel's own motion, shape kept
            if _nc()._PANEL_LOCAL_RIGID:
                # Solve the same idea LOCALLY -- see #panel-local-rigid. Falls
                # back to the whole-panel fit above on any failure, so a bad
                # neighbourhood can never leave the panel unrigidified silently.
                try:
                    rigid = _locally_rigid_panel(P, Q, _nc()._PANEL_LOCAL_K)
                except Exception as _le:
                    _note_pass_failure("panel-local-rigid", _le)
            resid = np.linalg.norm(Q - rigid, axis=1)
            worst = max(worst, float(resid.max()))
            out[free] = Q + (rigid - Q) * float(strength)
            touched += 1
        return out, touched, worst
    except Exception as _pe:
        # Was a bare `return dst_v, 0, 0.0`. Both call sites act only `if
        # touched:`, so a crash in here returned "0 panels" and was
        # indistinguishable from "no panel qualified" -- and #panel-rigidity now
        # runs on BOTH convert paths, so a silent failure would look like the
        # feature simply not applying to three quarters of the pack.
        _note_pass_failure("_partial_rigid_panels", _pe)
        return dst_v, 0, 0.0

def _rigidify_within_clearance(src_v, cur_v, tris, body_v, body_n,
                               strength: float, skip_mask=None,
                               min_verts: int = 24):
    """Rigidify each panel as far as it can go WITHOUT losing body clearance.

    `_partial_rigid_panels` has to run before the anti-poke, so the anti-poke
    then re-deforms every panel it pushes -- measured ceiling: even at full
    strength the residual only falls to ~0.98u from 1.45u. This runs AFTER, and
    recovers the rest where there is room for it.

    The strength is solved PER PANEL, not per vertex: bisect the largest s in
    [0, strength] whose result leaves no vertex deeper inside the body than the
    anti-poke already left it. A per-VERTEX clamp would be a crinkle (the
    `_LAYER_ORDER_SMOOTH` lesson); one scalar per panel moves the plate as a
    unit, so the panel keeps its shape and no step is introduced.

    Returns (verts, panels_touched, mean_strength_used).
    """
    try:
        from scipy.spatial import cKDTree as _KD
        s = np.asarray(src_v, dtype=np.float64)
        d = np.asarray(cur_v, dtype=np.float64)
        if strength <= 0 or s.shape != d.shape or len(s) < 3:
            return cur_v, 0, 0.0
        if body_v is None or body_n is None or not len(body_v):
            return cur_v, 0, 0.0
        lab = _weld_components(s, tris)
        if lab is None:
            return cur_v, 0, 0.0
        tree = _KD(np.asarray(body_v, dtype=np.float64))
        bv = np.asarray(body_v, dtype=np.float64)
        bn = np.asarray(body_n, dtype=np.float64)

        def clear_of(pts):
            _, j = tree.query(pts)
            return np.einsum('ij,ij->i', pts - bv[j], bn[j])

        # A BODY-SIDE version of this was built and REVERTED. `clear_of`
        # approximates the body by ONE vertex's tangent plane, so measuring over
        # `BUST_NEIGHBORHOOD_K` neighbours (worst wins) is strictly tighter and
        # looked like the obvious completion. Measured, it is a NET LOSS: it
        # costs a college robe 3.466 -> 5.218 morphed bust clip and buys only
        # 3.807 -> 3.647 on the cuirass. Stricter is not better here -- the
        # oscillation again ([[project_pass_damage_ledger]]): refusing more
        # rigidification leaves the panel at its tighter conformed shape, and
        # the morph then pushes through. Do not re-add it without re-measuring
        # BOTH pieces.

        # #panel-rigid-surface-guard: triangle-interior sample points, in LOCAL
        # panel indices. Built once per panel, not per bisection step.
        _t_all = (np.asarray(tris, dtype=np.int64).reshape(-1, 3)
                  if _nc().PANEL_RIGID_SURFACE_GUARD and tris is not None else None)
        if _t_all is not None and len(_t_all):
            _t_all = _t_all[(_t_all >= 0).all(axis=1)
                            & (_t_all < len(d)).all(axis=1)]

        def _samples(V, tl):
            """Centroid + 3 edge midpoints per triangle -- where a chord sags.

            The vertices are already tested; these are the points BETWEEN them,
            which is the whole of what the vertex test cannot see.
            """
            A, B, C = V[tl[:, 0]], V[tl[:, 1]], V[tl[:, 2]]
            return np.concatenate([(A + B + C) / 3.0, (A + B) * 0.5,
                                   (B + C) * 0.5, (A + C) * 0.5])

        out = d.copy()
        touched = 0
        used = []
        for u in np.unique(lab):
            m = lab == u
            if int(m.sum()) < max(12, int(min_verts)):
                continue
            free = m if skip_mask is None else (m & ~skip_mask)
            if int(free.sum()) < max(12, int(min_verts)):
                continue
            P, Q = s[free], d[free]
            Pc, Qc = P - P.mean(0), Q - Q.mean(0)
            U, S, Vt = np.linalg.svd(Pc.T @ Qc)
            det = np.sign(np.linalg.det(Vt.T @ U.T))
            R = Vt.T @ np.diag([1.0, 1.0, det]) @ U.T
            rigid = (R @ Pc.T).T + Q.mean(0)
            # PER-VERTEX floor, not the panel's worst vertex. Guarding only the
            # minimum let every OTHER vertex sink in: measured on the reported
            # piece, verts inside the body went 55 -> 105 while the worst depth
            # barely moved. The floor each vertex must still clear is its own
            # current clearance, or 0 if the anti-poke already left it inside --
            # so nothing NEWLY enters the body and nothing already in gets
            # deeper.
            floor = np.minimum(clear_of(Q), 0.0) - 1e-4

            # #panel-rigid-surface-guard: the same floor, applied to the points
            # BETWEEN the vertices. `tl` is this panel's triangles in local
            # indices; a panel with none (a strip whose tris straddle the skip
            # mask) simply falls back to the vertex test unchanged.
            tl = None
            floor_s = None
            if _t_all is not None:
                _pos = np.full(len(d), -1, dtype=np.int64)
                _pos[np.flatnonzero(free)] = np.arange(int(free.sum()))
                _keep = free[_t_all].all(axis=1)
                if _keep.any():
                    tl = _pos[_t_all[_keep]]
                    floor_s = np.minimum(clear_of(_samples(Q, tl)), 0.0) - 1e-4

            def feasible(sv):
                moved = Q + (rigid - Q) * sv
                if not bool(np.all(clear_of(moved) >= floor)):
                    return False
                if tl is None:
                    return True
                return bool(np.all(clear_of(_samples(moved, tl)) >= floor_s))

            lo, hi = 0.0, float(strength)
            if feasible(hi):
                best = hi                      # full strength costs nothing
            else:
                for _ in range(10):            # bisect the feasible strength
                    mid = 0.5 * (lo + hi)
                    if feasible(mid):
                        lo = mid
                    else:
                        hi = mid
                best = lo
            if best <= 1e-3:
                continue
            out[free] = Q + (rigid - Q) * best
            touched += 1
            used.append(best)
        return out, touched, (float(np.mean(used)) if used else 0.0)
    except Exception as _re:
        # Same shape as `_partial_rigid_panels` above: a crash returned
        # "0 panels" and read as "nothing qualified".
        _note_pass_failure("_rigidify_within_clearance", _re)
        return cur_v, 0, 0.0

def _panel_fit_out_of_body(pts, c, kd, body_v, body_n):
    """Get one rigid panel out of the body WITHOUT bending it.

    Two shape-preserving moves, tried in that order:

      1. TRANSLATE. Free -- the panel keeps its exact size and shape. Works for
         an open plate, and only for an open plate.
      2. GROW uniformly about the panel's own centroid. A closed BAND cannot be
         translated clear at all: pushing the chest off the bust drives the back
         half into the spine, so the search runs away and hits its cap. Measured
         on the reported cuirass -- worst penetration -1.26u, translation
         rejected, panel left inside. A uniform scale is still a similarity, so
         the plate stays exactly as flat/straight as it was authored; it just
         becomes the size that fits over a larger body, which is what an
         armourer would actually do.

    Returns the panel's final POSITIONS (translation alone is not enough to
    express a scale), or None to leave the panel where it is.
    """
    try:
        cur = np.asarray(c, dtype=np.float64).copy()

        def clear_at(q):
            _, jj = kd.query(q)
            return np.einsum('ij,ij->i', q - body_v[jj], body_n[jj]), jj

        q = pts + cur
        clr, jj = clear_at(q)
        if not np.any(clr < 0.0):
            return None                       # already clear: touch nothing
        # --- 1. translation --------------------------------------------------
        total = 0.0
        tq = q
        for _ in range(6):
            clr, jj = clear_at(tq)
            bad = clr < 0.0
            if not np.any(bad):
                return tq
            step = float(-clr[bad].min())
            if step <= 1e-4:
                return tq
            nout = body_n[jj][bad].mean(0)
            nn = float(np.linalg.norm(nout))
            if nn <= 1e-6:
                break
            total += step
            if total > _nc()._PANEL_RIDE_PUSH_MAX:
                break                          # a band: translation runs away
            tq = tq + (nout / nn) * step
        # --- 2. uniform growth ------------------------------------------------
        if _nc()._PANEL_RIDE_SCALE_MAX <= 1.0:
            return None
        ctr = q.mean(0)
        rel = q - ctr

        def grown(k):
            return ctr + rel * k

        hi = float(_nc()._PANEL_RIDE_SCALE_MAX)
        clr_hi, _ = clear_at(grown(hi))
        if np.any(clr_hi < 0.0):
            # Even the largest permitted growth does not fully clear it. Take it
            # anyway: growth is monotone in clearance, so this is strictly the
            # best available, and a partly-fixed plate beats a bent one.
            return grown(hi)
        lo = 1.0
        for _ in range(12):                    # smallest growth that clears
            mid = 0.5 * (lo + hi)
            c_mid, _ = clear_at(grown(mid))
            if np.any(c_mid < 0.0):
                lo = mid
            else:
                hi = mid
        return grown(hi)
    except Exception:
        return None

def _panel_rigid_disp(src_v, tris, disp, mask, min_verts: int,
                      body_v=None, body_n=None, stats=None, base_v=None,
                      stack=None, self_name=None):
    """Flatten the ride's displacement to one constant per rigid panel.

    Returns a displacement field: unchanged everywhere except on welded
    components that the ride covers (>= `_PANEL_RIDE_COVERAGE` of their verts),
    which get their own mean. Returns `disp` untouched on any failure -- never
    worse than not doing it.

    CLEARANCE. The ride runs after the anti-poke and overwrites it, so a plate
    made rigid here re-enters the body wherever the body is fuller than the
    authored plate -- measured 83 -> 149 verts inside on the reported piece.
    Given a body, the panel's constant is then pushed OUTWARD as a unit until
    nothing is inside, which is the whole point of doing this rigidly: one
    translation fixes the plate without bending it back into the shape the user
    is complaining about. Bounded by `_PANEL_RIDE_PUSH_MAX` so a panel that
    would need an absurd push stays put rather than flying off the body.
    """
    try:
        s = np.asarray(src_v, dtype=np.float64)
        d = np.asarray(disp, dtype=np.float64)
        m = np.asarray(mask, dtype=bool)
        if s.shape != d.shape or len(m) != len(s):
            return disp, None
        lab = _weld_components(s, np.asarray(tris, dtype=np.int64))
        if lab is None:
            return disp, None
        _kd = None
        if body_v is not None and body_n is not None:
            try:
                from scipy.spatial import cKDTree as _KDp
                _kd = _KDp(np.asarray(body_v, dtype=np.float64))
                body_v = np.asarray(body_v, dtype=np.float64)
                body_n = np.asarray(body_n, dtype=np.float64)
            except Exception:
                _kd = None
        # Everything else in the piece, at its baseline placement -- layers
        # ABOVE this one included, which is the half the ride's own reference
        # cannot supply.
        others = [x for x in (stack or []) if x[0] != self_name]
        # What would otherwise ship for these verts: the plain ride's result on
        # the ridden ones, the fit chain's on the rest. This is the baseline the
        # acceptance budget below judges each panel against.
        base = None
        if base_v is not None:
            try:
                base = np.asarray(base_v, dtype=np.float64).copy()
                if base.shape == s.shape:
                    base[m] = s[m] + d[m]
                else:
                    base = None
            except Exception:
                base = None
        out = d.copy()
        force = np.zeros(len(s), dtype=bool)
        for u in np.unique(lab):
            comp = lab == u
            n = int(comp.sum())
            if n < max(12, int(min_verts)):
                continue
            sel = comp & m
            if stats is not None:
                stats["seen"] = stats.get("seen", 0) + 1
            if int(sel.sum()) < _nc()._PANEL_RIDE_COVERAGE * n:
                if stats is not None:      # COUNT what is excluded, never 0/0
                    stats["partial"] = stats.get("partial", 0) + 1
                continue          # partly-ridden panel: a step would land INSIDE it
            if stats is not None:
                stats["flat"] = stats.get("flat", 0) + 1
            # THE WHOLE COMPONENT MOVES, not just the ridden part of it.
            #
            # The coverage gate admits a panel at 90%, so up to a tenth of it can
            # sit outside the ride mask -- and those verts would otherwise keep
            # their per-VERTEX fit while the other nine tenths moved as a plate.
            # That is a STEP along the boundary between them, and it lands on the
            # parts furthest from the layer beneath: the waist and the UNDER-BUST.
            # User, on the first build that shipped this: "the underside still
            # looks a little off". Measured, front of the panel, similarity
            # residual p95 -- under-bust 0.554 and waist 0.868 against 0.186 /
            # 0.244 for the same build with no growth, because growth widens the
            # very step it was creating. They belong to one physical plate; move
            # them with it.
            sel = comp
            c = d[sel].mean(0)
            if _nc()._PANEL_RIDE_Q != 0.5:
                # Slide the plate along its OWN ride direction to the requested
                # quantile of what its verts individually wanted. Only the
                # component along that direction is re-picked; the tangential
                # mean is kept, so the plate does not skew sideways.
                nrm = float(np.linalg.norm(c))
                if nrm > 1e-9:
                    u_dir = c / nrm
                    proj = d[sel] @ u_dir
                    c = c + u_dir * float(
                        np.quantile(proj, _nc()._PANEL_RIDE_Q) - proj.mean())
            cand = s[sel] + c                      # flattened, before fitting
            if _kd is not None:
                if stats is not None:
                    _, _j0 = _kd.query(cand)
                    _c0 = np.einsum('ij,ij->i',
                                    cand - body_v[_j0], body_n[_j0])
                    stats["min_clear"] = min(stats.get("min_clear", 9e9),
                                             float(_c0.min()))
                _q = _panel_fit_out_of_body(s[sel], c, _kd, body_v, body_n)
                if _q is not None:
                    # A scale cannot be expressed as one constant, so the panel
                    # gets a per-vertex displacement here. It is still a
                    # SIMILARITY of the authored panel, not a per-vertex fit.
                    cand = _q
                    if stats is not None:
                        stats["fitted"] = stats.get("fitted", 0) + 1
                elif stats is not None:
                    stats["no_push"] = stats.get("no_push", 0) + 1
                # THE PASS MUST BE ABLE TO DECLINE. `_panel_fit_out_of_body`
                # returns best-effort growth even when the growth does NOT
                # clear, so without this a panel it cannot fix still gets
                # frozen -- half inside the body, which is worse than leaving
                # the per-vertex fit alone. Judge the candidate against what
                # would otherwise ship, per panel, and back out if it loses.
                if base is not None:
                    new_in, deepen = _nc()._penetration_delta(
                        base[sel], cand, _kd, body_v, body_n)
                    # AND EVERY OTHER LAYER OF THE PIECE. The ride exists to
                    # keep a stack coherent -- each layer holding its authored
                    # offset to its neighbour at EVERY point. A constant per
                    # panel holds only the AVERAGE offset, so where a
                    # neighbouring layer bulges the plate stops following and
                    # the two cross. Body clearance is blind to this: both
                    # shapes can be perfectly outside the body and still pass
                    # through each other. User: "it does help yes but the
                    # problem is it creates issues with the layers of the
                    # armor."
                    #
                    # AGAINST THE WHOLE STACK, not just the layers beneath. A
                    # beneath-only test passed this plate while it drove 1641 ->
                    # 3188 verts into the belts ABOVE it.
                    #
                    # RELATIVE, never absolute: these layers overlap heavily BY
                    # DESIGN (the authored plate already sits 1743 verts inside
                    # the belts), so the only meaningful question is whether WE
                    # made it worse.
                    layer_in, layer_worst = 0, 0.0
                    _blame = None
                    for _onm, _ov, _on, _okd in others:
                        _di, _dw = _nc()._penetration_delta(
                            base[sel], cand, _okd, _ov, _on,
                            near=_nc()._LAYER_NEAR)
                        if _di > 0 and (_blame is None or _di > _blame[1]):
                            _blame = (_onm, _di)
                        layer_in += max(0, _di)
                        layer_worst = max(layer_worst, _dw)
                    if stats is not None:
                        stats["stack"] = len(others)
                        if _blame is not None:
                            stats["worst_pair"] = max(
                                stats.get("worst_pair", ("", 0)), _blame,
                                key=lambda x: x[1])
                    budget = _nc()._PANEL_RIDE_MAX_NEW_INSIDE * n
                    if (new_in > budget or layer_in > budget
                            or deepen > _nc()._PANEL_RIDE_MAX_DEEPEN):
                        if stats is not None:
                            stats["declined"] = stats.get("declined", 0) + 1
                            stats["declined_worst"] = max(
                                stats.get("declined_worst", 0.0), deepen)
                            if layer_in > budget:
                                stats["declined_layer"] = stats.get(
                                    "declined_layer", 0) + 1
                        continue           # leave this panel entirely alone
            elif stats is not None:
                stats["no_body"] = stats.get("no_body", 0) + 1
            out[sel] = cand - s[sel]
            force |= comp
        return out, force
    except Exception as e:
        # NEVER SILENT. `stats` is mutated as the loop runs, so a throw AFTER
        # the counters were filled printed a healthy-looking "N ridden rigidly,
        # 0 DECLINED" while the caller actually got the plain ride back. That
        # cost a wrong diagnosis: the piece read as unchanged for no reason.
        if stats is not None:
            stats["error"] = repr(e)
        return disp, None
