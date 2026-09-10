"""HDT-SMP physics: the actor skeleton caches and bone resolution, physics-XML lookup / read / bind / sanitise / harden, the generated XML for a destination, the framework bone carriers, chain anchors and rest-pose lifts, the skirt and butt collider proxies, the declared-bone audit, the tight-vs-loose soft-body gate and the physics finaliser.

Split out of nif_convert.py on 2026-09-01 (split step 9). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import os
import re
import sys

from . import fit_metrics, nif_io, nif_patch
from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
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
from .nif_convert_fitgeom import (  # noqa: E402
    WARP_PUSH_SHELL_GAP, WARP_DELTA_OUTLIER_MAX, CONFORM_FOLD_GUARD_FEATHER,
    CONFORM_FOLD_GUARD_MARGIN, CONFORM_FOLD_GUARD_STEPS,
    _SOFTCLOTH_BUST_CLEAR, _SOFTCLOTH_BUTT_CLEAR, WARP_SHEAR_MAX_GROWTH,
    WARP_SHEAR_STEPS, ANTIPOKE_SMOOTH_ITERS, ADAPTIVE_CLEARANCE_BASE,
    ADAPTIVE_CLEARANCE_MORPH_FACTOR, ADAPTIVE_CLEARANCE_MORPH_MAX,
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
from .nif_convert_skinframe import (  # noqa: E402
    _g2s_is_identity, _shape_global_to_skin, _verts_skin_to_world,
    _verts_world_to_skin,
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


def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


def _audit_registered_shape_declared_bones(dst_path, src_path) -> int:
    """A shape the piece's physics XML REGISTERS must not carry a bone that XML
    never DECLARES. Records a violation; returns how many it found.

    WHY IT IS A GUARD AND NOT A REPAIR. An undeclared influence on a registered
    shape has no rigid body in FSMP's system, and the piece free-falls off the
    actor -- confirmed in game twice now (iron, 2026-08-14; the bandit cuirass,
    2026-08-19). The repair EXISTS for shapes we generate: relabel the bone onto
    its nearest XML-declared ancestor, decline when there is none
    (`_add_butt_collider_patch` / `_add_skirt_collider_proxy`). Applying the same
    move to an AUTHORED shape means rewriting weights already written, which
    walks straight into the `setShapeWeights`-is-an-update trap, the
    add_bone-resets-STBs trap, and a pack-wide weight change no measurement can
    clear without an in-game verdict. So this reports and does not touch.

    It is scored as a DIFFERENTIAL against the author: 336 registered shapes in
    the pack carry undeclared bones their own AUTHOR shipped, so "undeclared" is
    not by itself a defect. Only bones WE added are counted.

    Runs at the very end of the shared tail, because anything earlier can be
    re-diverged by a later weight pass -- the same placement rule
    `#coincident-skin-match` had to follow."""
    try:
        pyn = _nc()._pynifly()
        dn = pyn.NifFile(filepath=str(dst_path))
        registered = (set(_hdt_collider_shape_names(dst_path, nif=dn))
                      | set(_hdt_softbody_shape_names(dst_path, nif=dn)))
        # DECLARED BONES COME FROM THE SAME SIDE AS `registered` -- the
        # DESTINATION. Reading them from `src_path` instead made this guard FAIL
        # OPEN in exactly the way the bug it exists to catch does: on a piece
        # whose XML resolves only from the output side, the source read returned
        # None, the guard returned "0 violations", and 44 real BaseShape
        # offenders went unreported (22 warnings raised against 64 pieces the
        # census sees). Measured on `<an SMP dress piece>_1.nif`:
        # source-side NONE, output-side 19 declared bones.
        # NOT gated with `stem_scan=False`, deliberately. This is a GUARD,
        # and refusing the fallback here does not make it read the right
        # XML -- it makes it read NONE, and the guard then returns "cannot
        # check" and stops filtering bones at all. Measured 2026-09-09 on a
        # 769-NIF arm: gating this site moved 421 NIFs, 62 of them by BONE
        # COUNT, because pieces that were being filtered against a
        # stem-matched XML stopped being filtered against anything. A
        # wrong-but-active guard and an inactive guard are both wrong, and
        # swapping one for the other is not a fix. #hdt-xml-race, second
        # instance -- it needs its own decision, with that number attached.
        txt = _read_source_hdt_xml_text(dst_path, nif=dn)
        declared = set(re.findall(r'<bone\s+name="([^"]+)"', txt or ""))
        if not declared:
            # Cannot CHECK is not the same as nothing to report. Say so, but
            # only when the piece actually registers shapes -- otherwise every
            # physics-free piece would log noise.
            if registered:
                _note_pass_failure(
                    "registered_shape_bones_UNCHECKED", RuntimeError(
                        f"{Path(dst_path).name}: registers {sorted(registered)} "
                        f"but no XML bone declarations could be read, so the "
                        f"declared-bone invariant was NOT verified"), dst_path)
            return 0
        if not registered:
            return 0
        sn = pyn.NifFile(filepath=str(src_path))
        author = {s.name: set((getattr(s, "bone_weights", None) or {}).keys())
                  for s in sn.shapes}
        bad = {}
        for s in dn.shapes:
            if s.name not in registered:
                continue
            ours = set((getattr(s, "bone_weights", None) or {}).keys())
            added = (ours - declared) - (author.get(s.name, set()) - declared)
            if added:
                bad[s.name] = sorted(added)
        if bad:
            _note_pass_failure(
                "registered_shape_undeclared_bones", RuntimeError(
                    f"{Path(dst_path).name}: physics-registered shape(s) carry "
                    f"bones this piece's XML does not declare, which FSMP "
                    f"cannot resolve (cloth free-falls): {bad}"), dst_path)
        return len(bad)
    except Exception as _pe:                                 # pragma: no cover
        _note_pass_failure("_audit_registered_shape_declared_bones", _pe,
                           dst_path)
        return 0

def _norm_bone(name: str) -> str:
    """Bone names compare case- AND whitespace-insensitively.

    Both matter in real data: XPMSSE writes `NPC L Foot [Lft ]` with a trailing
    space INSIDE the brackets where physics XMLs write `[Lft]`, and `[PElv]` vs
    `[Pelv]` differ only in case. An exact-match comparison reports real,
    resolvable skeleton bones as missing -- which is how the first measurement
    of this defect produced a 1260-name result that was mostly casing noise.

    Drops whitespace entirely rather than collapsing runs, because the observed
    difference is interior (`[Lft ]`) and a collapse leaves it. Two genuinely
    different bones differing ONLY in whitespace would collide, which is not a
    naming pattern that occurs; and the consequence of a collision is merely
    treating the bone as actor-resolvable, i.e. the long-standing behaviour.
    """
    return re.sub(r"\s+", "", name).casefold()

def _actor_can_resolve_bone(name: str) -> bool:
    """True if the ACTOR's skeleton supplies this bone, so the armour NIF does
    not need to carry a node for it.

    This is the question the node-preservation sites actually ask, and it is
    answerable by measurement -- the bone is either in the skeleton or it is
    not. `_is_skeleton_bone` answers a DIFFERENT question by name heuristic,
    matching `_SKELETON_BONE_KEYWORDS` as unanchored substrings, so a custom
    physics-chain bone called `LArmA 01` matches "arm" and is treated as an
    actor bone. Nothing then carries its node (chain anchors are zero-weight,
    so no shape's skin references them either), FSMP cannot resolve it, and the
    chain hanging off it is placed at the origin -- the sleeves-pull-to-origin
    defect. The `startswith("_")` escape hatch in `_is_skeleton_bone` was an
    earlier partial patch for this same class, covering only mods that prefix
    chain bones with an underscore.

    Deliberately NOT a fix to `_is_skeleton_bone` itself: that predicate also
    drives weighting, leg-rigid detection and the jiggle strip, and a blanket
    change to it is exactly what regressed working cuirasses on 2026-06-05.

    Falls back to the name heuristic when no skeleton can be loaded, so a
    missing skeleton keeps the old behaviour instead of preserving every bone.
    """
    try:
        skel = _actor_skeleton_bone_names()
    except Exception:
        skel = set()
    if not skel:
        return _is_skeleton_bone(name)
    # The NORMALISED set is cached, not just the raw one. This line used to read
    # `{_norm_bone(b) for b in skel}`, rebuilding the whole normalised skeleton
    # on EVERY membership test -- profiled at 1,937,715 `_norm_bone` calls over
    # five pieces, ~8.8% of wall clock in `re.sub` alone. Identical comparison
    # and identical result; only the rebuild is gone.
    pass  # (global _SKELETON_BONES_NORM_CACHE -> _nc().<name>, split)
    if (_nc()._SKELETON_BONES_NORM_CACHE is None
            or _nc()._SKELETON_BONES_NORM_CACHE[0] is not skel):
        # Keyed on the IDENTITY of the cached set, so an upstream invalidation
        # (which replaces the object) invalidates this too. A stale skeleton
        # here would silently prune resolvable bones -- the exact class that
        # regressed working cuirasses in June.
        _nc()._SKELETON_BONES_NORM_CACHE = (
            skel, frozenset(_norm_bone(b) for b in skel))
    return _norm_bone(name) in _nc()._SKELETON_BONES_NORM_CACHE[1]


def simulated_vert_mask(shape, eps: float):
    """Which of a shape's vertices are driven by HDT-SMP cloth simulation.

    A vertex carrying weight from a bone the ACTOR SKELETON cannot resolve is
    chain cloth: the simulation owns it, and any pass that rigidifies, pushes
    or re-weights it is fighting the solver rather than fixing anything.

    Written out THREE TIMES in nif_convert (audit F102) -- once on the copy
    path and twice on the body-swap path -- as the same loop with three sets of
    identifier names (`_cw_p1`/`_cw`/`_cwv`). Identical logic under three names
    is exactly the shape that lets a fix land in two of three places, which the
    file's own history records happening twice.

    Takes the eps rather than reading the knob, because one caller compares
    against a DIFFERENT concept (`_sim`, simulated-ness for mixed-cloth
    clearance) than the other two (`_skip`, do-not-rigidify). Same arithmetic,
    two meanings -- keeping the threshold at the call site keeps that visible.

    RAISES rather than returning None on a malformed shape: all three call
    sites already sit inside their own `try` and each has its OWN fallback
    (two set the mask to None, one abandons a wider block). Swallowing here
    would silently pick one of those for all of them.
    """
    cw = np.zeros(len(shape.verts), dtype=np.float64)
    for b, pr in (shape.bone_weights or {}).items():
        if _actor_can_resolve_bone(b):
            continue
        for vi, w in pr:
            vi = int(vi)
            if vi < len(cw):
                cw[vi] = max(cw[vi], float(w))
    return cw > eps

def _xml_referenced_bone_names(xml_text: str) -> "set[str]":
    """Every bone name a physics XML refers to, by ANY route.

    Harvesting only `<bone name=...>` misses CONSTRAINT-ONLY bones -- names that
    appear solely as `bodyA`/`bodyB` of a `<generic-constraint>` and are never
    declared. Those are real to FSMP, which resolves constraint bodies against
    the NIF hierarchy rather than the XML's own `<bone>` list (established
    2026-06-05, when pruning constraints by the declared-bone set regressed
    working cuirasses). Drop such a node and its constraint points at a phantom
    body, which drags the chain to the origin.

    Measured on one shipped skirt XML: 124 declared bones, 81 constraint bodies,
    18 of which are never declared -- including the whole `FVOc` chain that went
    missing from the output.
    """
    return (set(_nc()._XML_BONE_DECL_RE.findall(xml_text))
            | {m.strip() for m in _nc()._XML_BONE_TEXT_RE.findall(xml_text)}
            | set(_nc()._XML_CONSTRAINT_BODY_RE.findall(xml_text)))

def _cluster_decimate(verts, tris, target):
    """Vertex-cluster decimation KEEPING representative original verts.

    Representatives are original vertices, so weights, skin-to-bone transforms
    and g2s copy straight across with no re-rigging -- the same trick
    `scripts/build_body_collider_proxy.py` uses for the full-body proxy.
    Returns (rep_old_indices, old->new map, new_tris).
    """
    v = np.asarray(verts, np.float64)
    lo = v.min(0)
    span = float(np.linalg.norm(v.max(0) - lo)) or 1.0
    cell = span / 12.0
    for _ in range(24):
        ci = np.floor((v - lo) / max(cell, 1e-6)).astype(np.int64)
        keys = ci[:, 0] * 1_000_003 + ci[:, 1] * 1009 + ci[:, 2]
        n = len(np.unique(keys))
        if n > target * 1.15:
            cell *= 1.12
        elif n < target * 0.85:
            cell *= 0.92
        else:
            break
    ci = np.floor((v - lo) / max(cell, 1e-6)).astype(np.int64)
    keys = ci[:, 0] * 1_000_003 + ci[:, 1] * 1009 + ci[:, 2]
    order = np.argsort(keys, kind="stable")
    ks = keys[order]
    bounds = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    groups = np.split(order, bounds[1:])
    old2new = np.full(len(v), -1, np.int64)
    reps = []
    for g in groups:
        c = v[g].mean(0)
        reps.append(int(g[int(np.argmin(((v[g] - c) ** 2).sum(1)))]))
        old2new[g] = len(reps) - 1
    t = old2new[np.asarray(tris, np.int64)]
    ok = ((t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
          & (t >= 0).all(axis=1))
    return np.asarray(reps, np.int64), old2new, t[ok]

# --- #proxy-weight-invariant -- the two weights must decimate IDENTICALLY -----
#
# `_cluster_decimate` grids on `v.min(0)` and the SPAN of the verts it is given.
# A `_0` and a `_1` file hold the SAME garment at two body weights: identical
# vertex count, identical indexing, identical triangle set -- different
# POSITIONS. So both the cell size and the cell membership differ, and the two
# weights get proxies with different vertex counts and different topology.
#
# Skyrim blends an armour's `_0` and `_1` meshes per vertex, and one `.tri`
# serves the pair. A proxy that differs across the pair therefore ships two
# defects at once: a weight blend over mismatched vertex arrays, and morph
# offsets addressing vertices the other weight does not have.
#
# MEASURED on the shipped pack (2026-09-04), every `_0`/`_1` pair in the output:
#     generated skirt proxy   14 of 28 pairs mismatched   (50%)
#     generated butt patch     0 of 41 pairs mismatched
#     hand-authored control    0 of 12 pairs
# and on 13 of those 14 the decimator's INPUT is identical at both weights --
# same source shape, same chain mask, same triangle set, same vertex set. Only
# the positions differ. The 14th differs only in triangle ORDER (same set, same
# checksum). The decimator is the whole cause.
#
# THE FIX: cluster on TOPOLOGY, which is identical across the pair. A greedy
# BFS cover over the edge graph, seeded in vertex-index order, assigns every
# vertex to a representative using only `tris` and vertex indices. The
# representative stays an ORIGINAL vertex -- weights, skin-to-bone transforms
# and g2s still copy across with no re-rigging, which is the property
# `_cluster_decimate`'s docstring exists to protect.
#
# It is also better on thin cloth: a POSITION grid merges the front and back of
# a skirt into one cell wherever the two surfaces pass within a cell of each
# other, collapsing the sheet. A topological cover cannot, because the two
# surfaces are far apart in the graph.
def _topo_decimate(verts, tris, target):
    """Weight-INVARIANT decimation keeping representative original verts.

    Clusters on the edge graph, not on positions, so a `_0` and a `_1` file --
    same topology, different positions -- produce the SAME vertex count, the
    SAME correspondence and the SAME triangles. See #proxy-weight-invariant.
    Returns (rep_old_indices, old->new map, new_tris), the same contract as
    `_cluster_decimate`.
    """
    t = np.asarray(tris, np.int64)
    n = int(np.asarray(verts).shape[0])
    if not len(t) or n <= 0:
        return (np.zeros(0, np.int64), np.full(max(n, 0), -1, np.int64),
                np.zeros((0, 3), np.int64))

    # Undirected adjacency in CSR form, built from a SORTED unique edge list so
    # it does not inherit the triangle ORDER -- which is not stable across the
    # weight pair (measured: same triangle set, 82% of rows in a different
    # order).
    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    e = np.vstack([e, e[:, ::-1]])
    e = e[np.lexsort((e[:, 1], e[:, 0]))]
    e = e[np.r_[True, (e[1:] != e[:-1]).any(axis=1)]]
    deg = np.bincount(e[:, 0], minlength=n)
    start = np.r_[0, np.cumsum(deg)].astype(np.int64)
    nbr = np.ascontiguousarray(e[:, 1])

    # A breadth-first traversal from the lowest-index vertex, continued into
    # every further component in index order. Purely topological, and it walks
    # the surface, so striding it spreads seeds ACROSS the cloth instead of
    # clumping them wherever the vertex numbering happens to start.
    order = np.empty(n, np.int64)
    seen = np.zeros(n, bool)
    pos = 0
    for s0 in range(n):
        if seen[s0]:
            continue
        seen[s0] = True
        order[pos] = s0
        pos += 1
        head = pos - 1
        while head < pos:
            u = order[head]
            head += 1
            for w in nbr[start[u]:start[u + 1]]:
                if not seen[w]:
                    seen[w] = True
                    order[pos] = w
                    pos += 1

    def _cover(nseeds):
        """Multi-source BFS from strided seeds -- a graph Voronoi partition.

        Cells come out compact and of even size, which is what makes the DUAL
        triangulation dense: a proxy triangle exists only where a source
        triangle spans three DIFFERENT cells, so a ragged partition yields a
        sparse collider. Frontier vertices are visited in index order, so ties
        on a cell boundary resolve the same way at both weights.
        """
        step = max(1, n // max(nseeds, 1))
        seeds = order[::step]
        lab = np.full(n, -1, np.int64)
        lab[seeds] = np.arange(len(seeds))
        frontier = sorted(int(x) for x in seeds)
        while frontier:
            nxt = []
            for u in frontier:
                lu = lab[u]
                for w in nbr[start[u]:start[u + 1]]:
                    if lab[w] == -1:
                        lab[w] = lu
                        nxt.append(int(w))
            frontier = sorted(nxt)
        nlab = len(seeds)
        stray = np.flatnonzero(lab < 0)          # isolated verts, no edges
        if len(stray):
            lab[stray] = nlab + np.arange(len(stray))
            nlab += len(stray)
        return lab, nlab

    def _merge_fragments(lab, nlab, floor):
        """Absorb undersized cells into a neighbour, by lowest neighbouring
        label -- topological, so still invariant. A cell too small to reach
        three-way junctions contributes no proxy triangle."""
        for _ in range(4):
            sizes = np.bincount(lab, minlength=nlab)
            small = set(int(x) for x in np.flatnonzero(sizes < floor))
            if not small:
                break
            la, lb = lab[e[:, 0]], lab[e[:, 1]]
            cross = la != lb
            best_nb = {}
            for x, y in zip(la[cross], lb[cross]):
                x = int(x)
                if x not in small:
                    continue
                y = int(y)
                if y in small and y >= x:
                    continue                      # prefer merging INTO a keeper
                if x not in best_nb or y < best_nb[x]:
                    best_nb[x] = y
            if not best_nb:
                break
            remap = np.arange(nlab)
            for x, y in best_nb.items():
                remap[x] = y
            for _ in range(4):                    # collapse merge chains
                remap = remap[remap]
            lab = remap[lab]
            _uniq, lab = np.unique(lab, return_inverse=True)
            nlab = len(_uniq)
        return lab, nlab

    def _reps_for(lab, nlab):
        """rep[r] = the LOWEST-INDEX member of cell r -- an ORIGINAL vertex,
        picked without reading a position, so both weights pick the same one
        and weights / skin-to-bone transforms / g2s still copy straight
        across."""
        o = np.lexsort((np.arange(n), lab))
        ls = lab[o]
        firsts = np.r_[True, ls[1:] != ls[:-1]]
        rep = np.zeros(nlab, np.int64)
        rep[ls[firsts]] = o[firsts]
        return rep

    def _grow(seeds):
        """Graph Voronoi: multi-source BFS from `seeds`, index order breaking
        ties on a cell boundary so both weights partition identically."""
        lab = np.full(n, -1, np.int64)
        lab[seeds] = np.arange(len(seeds))
        frontier = sorted(int(x) for x in seeds)
        while frontier:
            nxt = []
            for u in frontier:
                lu = lab[u]
                for w in nbr[start[u]:start[u + 1]]:
                    if lab[w] == -1:
                        lab[w] = lu
                        nxt.append(int(w))
            frontier = sorted(nxt)
        return lab

    def _recenter(lab, nlab):
        """Each cell's new seed = its vertex FURTHEST (in hops) from any other
        cell -- one multi-source BFS out of every boundary vertex. This is the
        graph analogue of a Lloyd step: it pulls seeds off the cell edges, and
        even cells are what make the DUAL dense, which is where the collider's
        triangles come from."""
        la, lb = lab[e[:, 0]], lab[e[:, 1]]
        bnd = np.unique(e[:, 0][la != lb])
        if not len(bnd):
            return None
        dist = np.full(n, -1, np.int64)
        dist[bnd] = 0
        frontier = sorted(int(x) for x in bnd)
        while frontier:
            nxt = []
            for u in frontier:
                du = dist[u] + 1
                for w in nbr[start[u]:start[u + 1]]:
                    if dist[w] == -1:
                        dist[w] = du
                        nxt.append(int(w))
            frontier = sorted(nxt)
        # per cell: argmax depth, ties to the lowest vertex index
        o = np.lexsort((np.arange(n), -dist, lab))
        ls = lab[o]
        return o[np.r_[True, ls[1:] != ls[:-1]]]

    # Strided seeds along the traversal, then two Lloyd steps to even the cells.
    step = max(1, n // max(target, 1))
    seeds = order[::step]
    lab = _grow(seeds)
    for _ in range(2):
        nxt_seeds = _recenter(lab, len(seeds))
        if nxt_seeds is None or len(nxt_seeds) != len(seeds):
            break
        if np.array_equal(np.sort(nxt_seeds), np.sort(seeds)):
            break
        seeds = nxt_seeds
        lab = _grow(seeds)
    nlab = len(seeds)
    stray = np.flatnonzero(lab < 0)               # isolated verts, no edges
    if len(stray):
        lab[stray] = nlab + np.arange(len(stray))
        nlab += len(stray)
    lab, nlab = _merge_fragments(lab, nlab, max(2, step // 2))
    reps = _reps_for(lab, nlab)

    tt = lab[t]
    ok = ((tt[:, 0] != tt[:, 1]) & (tt[:, 1] != tt[:, 2]) & (tt[:, 0] != tt[:, 2])
          & (tt >= 0).all(axis=1))
    tt = tt[ok]
    # Canonical triangle order. The SOURCE triangle order is not stable across
    # the weight pair, and an unsorted proxy would inherit that difference even
    # though its vertices now match. Rotating to lowest-index-first keeps the
    # winding (it is a cyclic shift), so normals are unaffected.
    if len(tt):
        rot = np.argmin(tt, axis=1)
        idx = (np.arange(3)[None, :] + rot[:, None]) % 3
        tt = np.take_along_axis(tt, idx, axis=1)
        tt = tt[np.lexsort((tt[:, 2], tt[:, 1], tt[:, 0]))]
        tt = tt[np.r_[True, (tt[1:] != tt[:-1]).any(axis=1)]]
    return reps, lab, tt


def _decimate(verts, tris, target):
    """Dispatch to the weight-invariant decimator when #proxy-weight-invariant
    is on, else the legacy position grid.

    The invariant path gets a LARGER cell budget. Its cells are even, so the
    partition's dual runs about one triangle per cell on an open sheet, where
    the position grid reaches ~2.8 -- but most of that surplus is spurious:
    it comes from cells that weld two disconnected patches, whose triangles
    bridge the gap between the front and back of the cloth. Measured on four
    pieces, exact point-to-triangle distance from every cloth vertex:

        legacy            ~460 verts / 830-1400 tris   99.7% within GAP
        invariant x1.0    ~500 verts /  500-650 tris   95.5%
        invariant x1.5    ~720-840   /  810-1080 tris  98.5%
        invariant x2.0    ~900       / 810-1550 tris   98.6%  (not monotonic)

    x1.5 recovers the coverage at FEWER triangles than the grid it replaces,
    so it costs less collision work, not more; x2.0 buys nothing further.
    """
    if _nc().PROXY_WEIGHT_INVARIANT:
        scale = _nc().PROXY_TOPO_TARGET_SCALE
        return _topo_decimate(verts, tris, max(1, int(round(target * scale))))
    return _cluster_decimate(verts, tris, target)


def _add_butt_collider_patch(dst_path) -> int:
    """Add a hidden buttock collider derived from the UBE body. See
    #butt-collider-patch. Returns 1 if a patch was added."""
    if not _nc().BUTT_COLLIDER_PATCH:
        return 0
    p = Path(dst_path)
    stem = p.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    xml = p.parent / f"{stem}.xml"
    if not xml.is_file():
        return 0                      # no SMP cloth here -> nothing to catch
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(p))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0                      # reload+re-save corrupts the controller
    names = {s.name for s in nf.shapes}
    if _nc()._BUTT_COL_NAME in names:
        return 0
    base = _nc().ube_body_shape(nf)
    if base is None:
        return 0
    try:
        raw = xml.read_bytes()
        try:
            txt, codec = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            txt, codec = raw.decode("latin-1"), "latin-1"
    except Exception:
        return 0
    decls = re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt)
    # THE DONOR MUST BE A KINEMATIC COLLIDER, and "the first one declared" is not
    # good enough. Taking it that way picked this piece's `Proxy`, which is
    # CHAIN-DRIVEN and tagged `<tag>Fabric</tag>` with
    # `<no-collide-with-tag>Fabric</no-collide-with-tag>` -- so the patch shipped
    # tagged as cloth the skirt is explicitly FORBIDDEN to collide with. Inert,
    # and silently so. A body collider must be cloned from a body collider; if
    # none of the declared shapes is kinematic, DECLINE rather than invent a tag.
    # THE INJECTED BODY'S BONE LIST IS NOT THE SET OF "BODY BONES".
    #
    # UBE ships hands and feet as SEPARATE meshes, so the body mesh weights
    # neither. Measured 2026-08-25 across three bodies: the CBBE body carries 51
    # bones and the UBE body 45, and the six it lacks are exactly
    # `NPC L/R Hand`, `NPC L/R Foot` and `NPC L/R UpperarmTwist2` -- all of them
    # present in the actor skeleton, and all of them body bones by anatomy. They
    # are simply not in THIS mesh. (There is no CBBE->UBE renaming: the 45
    # shared names sit at identical bind positions, max deviation 0.0002u.)
    #
    # Using the body mesh's list as the test therefore rejects any collider that
    # touches a hand or a foot -- which a leg garment usually does. MEASURED on
    # the piece that produced the in-game report: the HEAVY cuirass got no butt
    # collider at all, because its only kinematic candidate, `Pants`, was
    # rejected solely for using `NPC L Foot` and `NPC R Foot`.
    #
    # WIDENED NARROWLY, to the hand/foot family only. Testing against the whole
    # actor skeleton instead is WRONG and was rejected: this modlist's skeleton
    # declares `SkirtFBone01`, so a skeleton test would accept a CHAIN-DRIVEN
    # shape as a body collider -- precisely the failure the comment above
    # records, where a `Proxy` tagged as Fabric (which the skirt is forbidden to
    # collide with) shipped inert and silently so.
    _BODY_ADJACENT = ("hand", "finger", "thumb", "foot", "toe",
                      "upperarmtwist2")
    _shape_by = {s.name: s for s in nf.shapes}
    _body_bones = set(base.bone_names or [])

    def _is_body_bone(b: str) -> bool:
        if b in _body_bones:
            return True
        lb = b.lower()
        return b.startswith("NPC ") and any(k in lb for k in _BODY_ADJACENT)

    donor = None
    for d in decls:
        s_ = _shape_by.get(d)
        if s_ is None or d == base.name:
            continue
        try:
            bwd = s_.bone_weights or {}
        except Exception:
            continue
        if bwd and all(_is_body_bone(b) for b in bwd):
            donor = d                 # kinematic: every bone is a body bone
            break
    if donor is None:
        return 0

    try:
        from scipy.spatial import cKDTree as _KD
        from .body_zones import BUTT_Z, TORSO_HALF_X
        bv = _verts_skin_to_world(np.asarray(base.verts, dtype=np.float64),
                                  _shape_global_to_skin(base))
        bt = np.asarray(base.tris, dtype=np.int64)
        bn = _nc()._vertex_normals_from_tris(bv, bt)
    except Exception:
        return 0
    if not len(bt):
        return 0

    # ---- fire ONLY where the gap is real ------------------------------------
    rear = ((bv[:, 2] >= BUTT_Z[0]) & (bv[:, 2] <= BUTT_Z[1])
            & (bn[:, 1] < -0.3) & (np.abs(bv[:, 0]) < TORSO_HALF_X + 6.0))
    if rear.sum() < _nc()._BUTT_COL_MIN_UNCOVERED:
        return 0
    col_pts = []
    for s in nf.shapes:
        if s.name in decls and s.name != base.name:
            try:
                col_pts.append(_verts_skin_to_world(
                    np.asarray(s.verts, dtype=np.float64),
                    _shape_global_to_skin(s)))
            except Exception:
                pass
    if not col_pts:
        return 0
    allcol = np.vstack(col_pts)
    d_cov, _ = _KD(allcol).query(bv[rear], k=1)
    uncovered = int((d_cov > _nc()._BUTT_COL_GAP).sum())
    if uncovered < _nc()._BUTT_COL_MIN_UNCOVERED:
        return 0                      # this piece's collider already covers it

    # ---- build the patch ----------------------------------------------------
    try:
        sel = np.flatnonzero(rear)
        keep = np.zeros(len(bv), dtype=bool)
        keep[sel] = True
        tri_ok = keep[bt].all(axis=1)
        if tri_ok.sum() < 8:
            return 0
        sub_tris = bt[tri_ok]
        used = np.unique(sub_tris)
        remap = np.full(len(bv), -1, np.int64)
        remap[used] = np.arange(len(used))
        reps, _o2n, new_tris = _decimate(
            bv[used], remap[sub_tris], _nc()._BUTT_COL_TARGET)
        if not len(new_tris) or not len(reps):
            return 0
        rep_old = used[reps]                       # indices into the BODY
        # A COLLIDER IS A BACKSTOP, NOT A SHAPE-SETTER. #derived-butt-standoff
        #
        # A flat 0.6 sits ABOVE where close-fitting rear cloth actually rests, so
        # it stops being a floor against penetration and starts dictating the
        # silhouette -- the user's "the collider for leather armor is inflating
        # the rear". Measured on the deployed pieces, rear-band signed standoff
        # of the garment from the body:
        #
        #                        authored p10   converted p10   0.6 floor?
        #     leather (hugs)        -1.29           +0.30        BITES
        #     iron (flared skirt)  +12.80           +0.79        never touches
        #
        # The author's leather cloth ranged down to -1.29 -- it is SUPPOSED to
        # tuck in. A uniform +0.60 forbids that everywhere on the rear and
        # flattens the shape out.
        #
        # So derive the standoff from where this piece's own cloth rests, and
        # sit just under it. CAPPED at the old constant so this can only ever
        # REDUCE the offset (iron is unchanged, protecting the collapse fix that
        # was just confirmed in game), and floored at 0 so the collider is never
        # placed inside the skin, which is the clip it exists to stop.
        _off = float(_nc()._BUTT_COL_OFFSET)
        try:
            _decl_col = set(re.findall(
                r'<per-triangle-shape\s+name="([^"]+)"', txt))
            _cloth = []
            for _s in nf.shapes:
                if _s.name in _decl_col or _s.name == base.name:
                    continue
                if not (_s.textures or {}):
                    continue          # textureless == collider/proxy, not cloth
                _cw = _verts_skin_to_world(
                    np.asarray(_s.verts, np.float64), _shape_global_to_skin(_s))
                _m = ((_cw[:, 2] >= BUTT_Z[0]) & (_cw[:, 2] <= BUTT_Z[1])
                      & (_cw[:, 1] < -2.0))
                if _m.sum():
                    _d, _i = _KD(bv).query(_cw[_m])
                    _cloth.append(np.einsum(
                        'ij,ij->i', _cw[_m] - bv[_i], bn[_i]))
            if _cloth:
                _all = np.concatenate(_cloth)
                if len(_all) >= 20:
                    _p10 = float(np.percentile(_all, 10))
                    _off = min(_off, max(0.0, _p10 - _nc()._BUTT_COL_MARGIN))
        except Exception as _oe:
            _note_pass_failure("_add_butt_collider_patch/standoff", _oe)
            _off = float(_nc()._BUTT_COL_OFFSET)
        if abs(_off - float(_nc()._BUTT_COL_OFFSET)) > 1e-6:
            print(f"    [butt-col] {p.name}: standoff {_nc()._BUTT_COL_OFFSET} -> "
                  f"{_off:.2f} (derived from this piece's own rear cloth)")
        pv = bv[rep_old] + bn[rep_old] * _off
        # back to the body's STORED frame so the new shape shares its space
        pv_stored = _verts_world_to_skin(pv, _shape_global_to_skin(base))
        nverts = [tuple(float(c) for c in r) for r in pv_stored]
        ntris = [tuple(int(c) for c in r) for r in new_tris]
        nnrm = [tuple(float(c) for c in bn[i]) for i in rep_old]
        nuvs = [(0.0, 0.0)] * len(rep_old)
    except Exception as _be:
        _note_pass_failure("_add_butt_collider_patch/build", _be)
        return 0

    try:
        backup = p.read_bytes()
    except Exception:
        return 0

    def _all_extra(nf_):
        # KNOWN BLIND SPOT, now MEASURED (2026-09-07). `extra_data()` stops
        # at the first block it cannot build, so everything after that block
        # is invisible here. The guard is `pre_extra <= post_extra`
        # ("nothing that was there has gone"), and a block hidden on BOTH
        # sides is simply outside its cover -- it neither protects it nor
        # falsely fires on it.
        #
        # HOW BIG THE HOLE IS, counted against the index-enumerated reader:
        #
        #     SOURCE NIFs   1200 read, 5319 shapes
        #        1 block :   865 shapes,  46 blind
        #        2 blocks:    16 shapes,  10 blind   <- 62%
        #        hidden  :   BODYTRI x10, unnamed x56
        #     OUR OUTPUT  1200 read, 2885 shapes
        #        blind   :     4 shapes (0.14%) -- we rarely write 2 blocks
        #
        # CORRECTION to the earlier note here: the block that stops the walk
        # does NOT read as LOCKEDNORM through this accessor. In every blind
        # case its name comes back None -- it is unbuildable, so it has no
        # readable name -- which is also why a probe cannot simply skip it.
        #
        # STILL NOT swapped to the index-enumerated reader: that makes the
        # invariant STRICTER, and this is a safety guard, so the number that
        # decides it is the ROLLBACK RATE over a real convert, before and
        # after. That has not been measured. Three identical copies exist
        # (nif_convert_bust.py and twice here) -- fix them together.
        # See feedback_pynifly_extra_data_stops, and `_census_common.bodytri_of`
        # for the correct reader.
        out = {(None, getattr(ed, "name", None))
               for ed in nf_.rootNode.extra_data()}
        for s_ in nf_.shapes:
            try:
                out |= {(s_.name, getattr(ed, "name", None))
                        for ed in s_.extra_data()}
            except Exception:
                pass
        return out

    pre_extra, pre_shapes = _all_extra(nf), set(names)
    try:
        ns = nf.createShapeFromData(_nc()._BUTT_COL_NAME, nverts, ntris, nuvs, nnrm)
        ns.skin()
        rep_pos = {int(o): i for i, o in enumerate(rep_old)}
        wrote = 0
        # VALIDATE, THEN MUTATE (#identity-stb-collider). Read every bone's
        # skin-to-bone transform BEFORE adding a single one, because once the
        # shape has been touched the three ways this goes wrong are mutually
        # exclusive:
        #   * setting the transform inside a swallowing handler leaves a failed
        #     read at IDENTITY -- and the next line weights vertices to it. An
        #     identity STB skins those verts to the ORIGIN, the documented
        #     explosion mode, on a COLLIDER the body's physics collides against.
        #   * skipping the bone AFTER `add_bone` leaves it in the shape with an
        #     empty weight list -- `#zeroweight-bone-desync`, an equip CTD.
        #   * skipping it and writing the rest leaves those vertices
        #     under-weighted, which displaces them (same defect as
        #     `#family-weight-invariant`).
        # So an unreadable transform aborts the whole patch: the handler below
        # records it and returns BEFORE the save, leaving the NIF on disk
        # untouched. A missing butt patch is a state this pack shipped in for
        # its whole life; a collider anchored to the origin is not.
        # ONLY BONES THE PIECE'S OWN PHYSICS XML DECLARES. #collider-declared-bones
        #
        # This collider is REGISTERED in the XML, and FSMP resolves a registered
        # per-triangle shape's vertices through the bones of ITS OWN system. The
        # body donates its full skinning, which includes the UBE body's JIGGLE
        # bones (NPC L/R Butt, L/R Front/RearThigh) -- and iron's XML declares
        # none of them, so 6 of ButtCol's 12 influences had no rigid body in the
        # system it was registered into.
        #
        # THE INVARIANT IS THE AUTHOR'S, measured on iron: every authored
        # collider is 100% XML-declared (Collision 5/5, Belt Col 3/3, Bag Col
        # 4/4). ButtCol was 6/12. Bisected in game 2026-08-14: with the shape in
        # the mesh but its <per-triangle-shape> block REMOVED from the XML the
        # collapse STOPS, which puts the fault in the registration, not the
        # geometry.
        #
        # So relabel WHICH bone holds the weight -- the same move as
        # `#bust-plate-chain-transplant`. Walk the ACTOR skeleton to the nearest
        # DECLARED ancestor (Butt -> Pelvis, Front/RearThigh -> Thigh) and
        # ACCUMULATE onto it. At bind this is exactly position-preserving:
        # every bone's STB is its own bind inverse, so the skinned point does
        # not depend on which bone carries the weight -- verified 0.000u. No
        # bone is ADDED (every target is already an influence), so the
        # add_bone/STB reset above is not re-entered and the influence count per
        # vertex never rises.
        _declared_bones = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
        _avail = set((base.bone_weights or {}).keys())
        _acc: "dict[str, dict[int, float]]" = {}
        _redirects: "dict[str, str]" = {}
        for bname, pairs in (base.bone_weights or {}).items():
            sub = [(rep_pos[int(vi)], float(w)) for vi, w in pairs
                   if int(vi) in rep_pos and float(w) > 1e-4]
            if not sub:
                continue
            tgt = bname
            if _declared_bones and bname not in _declared_bones:
                tgt = _nearest_declared_ancestor(
                    bname, _declared_bones, _avail)
                if tgt is None:
                    # No declared ancestor: shipping it would re-create the very
                    # defect this guards. Decline the whole patch -- a missing
                    # butt collider is a state the pack shipped in for its whole
                    # life; a collider FSMP cannot resolve collapses the cloth.
                    raise _ColliderDeclined(
                        f"no XML-declared ancestor for {bname!r}")
                _redirects[bname] = tgt
            d = _acc.setdefault(tgt, {})
            for vi, w in sub:
                d[vi] = d.get(vi, 0.0) + w      # ACCUMULATE, never overwrite
        plan = []
        for bname, vw in _acc.items():
            try:
                _stb = base.get_shape_skin_to_bone(bname)
            except Exception as _be:
                raise RuntimeError(
                    f"skin-to-bone unreadable for {bname!r}: {_be!r}") from _be
            if _stb is None:
                raise RuntimeError(f"skin-to-bone missing for {bname!r}")
            plan.append((bname, _stb, sorted(vw.items())))
        if _redirects:
            print(f"    [butt-col] {p.name}: redirected {len(_redirects)} "
                  f"XML-undeclared bone(s) onto declared parents: "
                  f"{ {k: v for k, v in sorted(_redirects.items())} }")
        # ADD EVERY BONE FIRST, THEN SET EVERY STB (`_install_skin`'s order).
        # #identity-stb-collider. `add_bone` RESETS the skin-to-bone transforms
        # of bones already on the shape, so the interleaved
        # add/set/set-weights loop this used to be left ONLY THE LAST BONE with
        # a valid STB and shipped every other one at IDENTITY -- measured on the
        # deployed iron: 11 of ButtCol's 12 bones identity, the survivor being
        # the last one added. An identity STB does NOT skin the vertex to the
        # origin here (g2s still places it); it drops the bone's own bind
        # inverse, so at runtime the collider is transformed by the raw actor
        # bone globals and lands nowhere near the buttocks -- reconstructed
        # against the actor skeleton, all 354 verts were >1u out, p50 52.8u,
        # max 72.8u, while all three AUTHORED colliders reconstruct inside
        # 0.02u. That is what made HDT-SMP cloth free-fall: the skirts are told
        # to collide with a 561-triangle ColBody shape that is not where the
        # body is, so the solver never settles. #butt-collider-patch was
        # DEFAULT ON, so this shipped.
        #
        # The comment this replaced said "NEW shape -> add_bone is safe here;
        # the STB footgun is about adding a bone to an ALREADY-skinned shape."
        # That is wrong twice over: `ns.skin()` has already been called, and the
        # reset applies to any bone previously added to THIS shape regardless of
        # where the shape came from. `project_jiggle_transfer` recorded the same
        # footgun in June and the fix it prescribes is exactly this ordering.
        for bname, _stb, _sub in plan:
            ns.add_bone(bname)
        for bname, _stb, _sub in plan:
            ns.set_skin_to_bone_xform(bname, _stb)
        for bname, _stb, sub in plan:
            ns.setShapeWeights(bname, sub)
            wrote += len(sub)
        if not wrote:
            raise RuntimeError("patch got no weights")
        if base.has_global_to_skin:
            ns.set_global_to_skin(base.global_to_skin)
        try:
            if base.partitions:
                ns.set_partitions([base.partitions[0]], [0] * len(ntris))
        except Exception:
            pass
        for slot in list((ns.textures or {}).keys()):
            try:
                ns.set_texture(slot, "")
            except Exception:
                pass
        pr = getattr(ns, "properties", None)
        if pr is not None and hasattr(pr, "flags"):
            pr.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
        if hasattr(ns, "flags"):
            ns.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
    except _ColliderDeclined as _dc:
        # A correct refusal, not a defect -- see `_ColliderDeclined`.
        print(f"    [butt-col] {p.name}: DECLINED -- {_dc}", file=sys.stderr)
        return 0
    except Exception as _ce:
        _note_pass_failure("_add_butt_collider_patch/author", _ce)
        return 0

    _nc()._hide_virtual_body(nf)
    try:
        atomic_nif_save(nf, p)
    except Exception as _se:
        _note_pass_failure("_add_butt_collider_patch/save", _se)
        return 0
    ok = False
    try:
        nf2 = pyn.NifFile(filepath=str(p))
        post = {s.name for s in nf2.shapes}
        ok = (pre_extra <= _all_extra(nf2) and pre_shapes <= post
              and _nc()._BUTT_COL_NAME in post)
    except Exception:
        ok = False
    if not ok:
        try:
            atomic_write_bytes(p, backup)
        except Exception:
            pass
        print(f"  WARN: butt-collider patch on {p.name} lost shapes/extra-data "
              f"-- RESTORED original, patch skipped", file=sys.stderr)
        return 0

    # ---- XML: clone the donor block, name changed, nothing else -------------
    m = re.search(r'([ \t]*)<per-triangle-shape\s+name="'
                  + re.escape(donor) + r'".*?</per-triangle-shape>',
                  txt, re.S)
    if m is None or f'name="{_nc()._BUTT_COL_NAME}"' in txt:
        return 1                      # geometry is in; XML already done or odd
    block = m.group(0).replace(f'name="{donor}"', f'name="{_nc()._BUTT_COL_NAME}"', 1)
    new_txt = txt[:m.end()] + "\n" + m.group(1) + block.lstrip() + txt[m.end():]
    try:
        atomic_write_bytes(xml, new_txt.encode(codec))
    except Exception as _xe:
        _note_pass_failure("_add_butt_collider_patch/xml", _xe)
        return 1
    print(f"    [butt-col] {p.name}: +{len(rep_old)}v/{len(ntris)}t "
          f"({uncovered} uncovered butt verts), XML cloned from {donor!r}")
    return 1

class _ColliderDeclined(Exception):
    """A collider patch REFUSING to ship, which is a correct outcome, not a bug.

    Both collider patches decline rather than register a collider whose bones
    the piece's own physics XML cannot resolve -- that is the whole point of
    `#collider-declared-bones`. Raising a bare `RuntimeError` for it routed the
    decline into `_note_pass_failure`, so an intentional, healthy refusal was
    counted and printed as a PASS FAILURE. That is the same inversion as
    [[feedback_grep_shape_copy_errors]] read backwards: a working design
    reported as broken teaches you to ignore the one channel that matters.
    Caught separately, logged as a decline, no failure recorded."""

def _add_skirt_collider_proxy(dst_path) -> int:
    """Add a Fabric collision proxy built from the VISIBLE cloth.
    See #skirt-proxy-rebuild. Returns 1 if a proxy was added."""
    if not _nc().SKIRT_PROXY_REBUILD:
        return 0
    p = Path(dst_path)
    stem = p.stem
    for suf in ("_0", "_1"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    xml = p.parent / f"{stem}.xml"
    if not xml.is_file():
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(p))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0
    names = {s.name for s in nf.shapes}
    if _nc()._SKIRT_PROXY_NAME in names:
        return 0
    base = _nc().ube_body_shape(nf)
    if base is None:
        return 0
    try:
        raw = xml.read_bytes()
        try:
            txt, codec = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            txt, codec = raw.decode("latin-1"), "latin-1"
    except Exception:
        return 0
    decls = re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt)
    shape_by = {s.name: s for s in nf.shapes}
    body_bones = set(base.bone_names or [])

    def _chain_mass(sh_):
        """Per-vert weight on bones the BODY does not have == simulated."""
        v = np.asarray(sh_.verts, dtype=np.float64)
        m = np.zeros(len(v))
        for b, pairs in (sh_.bone_weights or {}).items():
            if b in body_bones:
                continue
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < len(v):
                    m[iv] += float(w)
        return m

    # DONOR: a CHAIN-DRIVEN per-triangle shape -- the mirror of ButtCol's rule.
    # Cloning a kinematic block here would tag the cloth as a body collider and
    # it would collide with the wrong things.
    #
    # "Chain-driven" was tested as "carries weight on a bone the BODY does not
    # have". That admits a KINEMATIC GROUND PLANE: it is weighted to a skeleton
    # root the body shape has no weights on, so it scored as chain mass. On a
    # reported piece the donor picked that way was the 4-vertex ground plane,
    # and the generated proxy inherited its block verbatim --
    #
    #     ours    <tag>ground</tag>   margin 1  prenetration 4
    #     authored <tag>Fabric</tag>  margin 0  penetration -1  shared private
    #
    # -- so a 589-vertex "ground" was planted inside the character's skirt, and
    # the piece's own body collider carries `no-collide-with-tag ground`. The
    # comment above predicted exactly this failure; the TEST did not implement
    # it. #donor-must-be-simulated
    #
    # THE CLASS PROPERTY: a bone is chain-driven only if the XML actually
    # SIMULATES it, i.e. it appears as a `generic-constraint` body. A skeleton
    # root never does. So require a bone that is BOTH outside the body's own
    # bone set AND a constraint body. Where no donor qualifies, DECLINE the
    # proxy -- which is what the authored file ships for such a piece.
    _sim_bones = set(re.findall(r'body[AB]="([^"]+)"', txt or ""))
    donor = None
    for d in decls:
        s_ = shape_by.get(d)
        if s_ is None or d == base.name:
            continue
        try:
            if not (s_.bone_weights or {}) or _chain_mass(s_).max() <= 1e-3:
                continue
            if (set(s_.bone_names or []) - body_bones) & _sim_bones:
                donor = d
                break
        except Exception:
            continue
    if donor is None:
        return 0

    # SOURCE: the largest rendered shape that is actually simulated.
    src_sh, best = None, -1
    for s in nf.shapes:
        if s.name in decls or s.name in _nc().UBE_BODY_INJECT_NAMES:
            continue
        try:
            cm = _chain_mass(s)
            n_cloth = int((cm >= _nc()._SKIRT_PROXY_CHAIN_MIN).sum())
        except Exception:
            continue
        if n_cloth > best:
            src_sh, best = s, n_cloth
    if src_sh is None or best < 40:
        return 0

    try:
        gv = np.asarray(src_sh.verts, dtype=np.float64)
        cm = _chain_mass(src_sh)
        cloth = cm >= _nc()._SKIRT_PROXY_CHAIN_MIN
        # FIRE GATE: only where the authored proxies genuinely fail to represent
        # the cloth. A piece whose proxy already tracks its skirt is untouched.
        from scipy.spatial import cKDTree as _KD
        prox_pts = []
        for d in decls:
            s_ = shape_by.get(d)
            if s_ is None:
                continue
            try:
                if _chain_mass(s_).max() > 1e-3:
                    prox_pts.append(np.asarray(s_.verts, dtype=np.float64))
            except Exception:
                pass
        if not prox_pts:
            return 0
        dist, _ = _KD(np.vstack(prox_pts)).query(gv[cloth], k=1)
        unrep = int((dist > _nc()._SKIRT_PROXY_GAP).sum())
        if unrep < _nc()._SKIRT_PROXY_MIN_UNREPRESENTED:
            return 0

        keep = np.zeros(len(gv), dtype=bool)
        keep[np.flatnonzero(cloth)] = True
        gt = np.asarray(src_sh.tris, dtype=np.int64)
        tri_ok = keep[gt].all(axis=1)
        if tri_ok.sum() < 8:
            return 0
        sub = gt[tri_ok]
        used = np.unique(sub)
        remap = np.full(len(gv), -1, np.int64)
        remap[used] = np.arange(len(used))
        reps, _o2n, new_tris = _decimate(
            gv[used], remap[sub], _nc()._SKIRT_PROXY_TARGET)
        if not len(new_tris) or not len(reps):
            return 0
        rep_old = used[reps]
        nverts = [tuple(float(c) for c in gv[i]) for i in rep_old]
        ntris = [tuple(int(c) for c in r) for r in new_tris]
        gn = _nc()._vertex_normals_from_tris(gv, gt)
        nnrm = [tuple(float(c) for c in gn[i]) for i in rep_old]
        nuvs = [(0.0, 0.0)] * len(rep_old)
    except Exception as _be:
        _note_pass_failure("_add_skirt_collider_proxy/build", _be)
        return 0

    try:
        backup = p.read_bytes()
    except Exception:
        return 0

    def _all_extra(nf_):
        # KNOWN BLIND SPOT, now MEASURED (2026-09-07). `extra_data()` stops
        # at the first block it cannot build, so everything after that block
        # is invisible here. The guard is `pre_extra <= post_extra`
        # ("nothing that was there has gone"), and a block hidden on BOTH
        # sides is simply outside its cover -- it neither protects it nor
        # falsely fires on it.
        #
        # HOW BIG THE HOLE IS, counted against the index-enumerated reader:
        #
        #     SOURCE NIFs   1200 read, 5319 shapes
        #        1 block :   865 shapes,  46 blind
        #        2 blocks:    16 shapes,  10 blind   <- 62%
        #        hidden  :   BODYTRI x10, unnamed x56
        #     OUR OUTPUT  1200 read, 2885 shapes
        #        blind   :     4 shapes (0.14%) -- we rarely write 2 blocks
        #
        # CORRECTION to the earlier note here: the block that stops the walk
        # does NOT read as LOCKEDNORM through this accessor. In every blind
        # case its name comes back None -- it is unbuildable, so it has no
        # readable name -- which is also why a probe cannot simply skip it.
        #
        # STILL NOT swapped to the index-enumerated reader: that makes the
        # invariant STRICTER, and this is a safety guard, so the number that
        # decides it is the ROLLBACK RATE over a real convert, before and
        # after. That has not been measured. Three identical copies exist
        # (nif_convert_bust.py and twice here) -- fix them together.
        # See feedback_pynifly_extra_data_stops, and `_census_common.bodytri_of`
        # for the correct reader.
        out = {(None, getattr(ed, "name", None))
               for ed in nf_.rootNode.extra_data()}
        for s_ in nf_.shapes:
            try:
                out |= {(s_.name, getattr(ed, "name", None))
                        for ed in s_.extra_data()}
            except Exception:
                pass
        return out

    # #proxy-encloses-chain: test the hull BEFORE creating the shape. A proxy
    # containing the chain nodes it exists to collide with starts the solver in
    # violation, and the cost of that is an intermittent in-game artefact no
    # offline clip metric sees. Declining is cheaper than shipping it.
    if _nc().PROXY_ENCLOSE_GUARD:
        try:
            _cn = {}
            for _nm, _nd in (nf.nodes or {}).items():
                if _nm == _nc()._SKIRT_PROXY_NAME:
                    continue
                if not any(_w in _nm.lower() for _w in
                           ("skirt", "chain", "hdt", "smp", "cloth")):
                    continue
                _tr = _nd.global_transform.translation
                _cn[_nm] = np.asarray([_tr[0], _tr[1], _tr[2]], dtype=np.float64)
            if len(_cn) >= 3:
                _in = _nc()._proxy_encloses_chain_nodes(nverts, ntris, _cn)
                if _in:
                    print(f"    [skirt-proxy] {p.name}: DECLINED -- the proxy "
                          f"would CONTAIN {len(_in)} of {len(_cn)} chain "
                          f"node(s) ({', '.join(_in[:4])}"
                          f"{'...' if len(_in) > 4 else ''}); a node inside its "
                          f"own collider starts the solver in violation",
                          file=sys.stderr)
                    return 0
        except Exception as _ee:
            _note_pass_failure("skirt-proxy/enclose-guard", _ee)

    pre_extra, pre_shapes = _all_extra(nf), set(names)
    try:
        ns = nf.createShapeFromData(_nc()._SKIRT_PROXY_NAME, nverts, ntris, nuvs, nnrm)
        ns.skin()
        rep_pos = {int(o): i for i, o in enumerate(rep_old)}
        wrote = 0
        # VALIDATE, THEN MUTATE -- see `#identity-stb-collider` in
        # `_add_butt_collider_patch` for the three failure modes this ordering
        # is avoiding at once.
        # #collider-declared-bones, EXTENDED TO THIS PROXY 2026-08-17.
        #
        # A registered collider may only carry bones the piece's OWN physics XML
        # declares -- an influence with no rigid body in the system it is
        # registered into is what collapsed the cloth, bisected in game on
        # ButtCol ([[project_collider_declared_bones]]). That fix went to ButtCol
        # only, and the postrun audit still finds 19 of 64 `SkirtCol` shapes
        # carrying undeclared bones: `NPC L/R UpperarmTwist1+2` on a dragonbone
        # cuirass, `NPC Neck` on a Tullius robe, `NPC R Front/RearThigh` on a
        # belt, `SkirtB/FBone*` on a farm robe, `cocob09`/`cococ09` on a towel.
        #
        # The redirect is SOUND here for the same reason it is on ButtCol: a bone
        # the XML does not declare is not simulated by this piece at all, so it
        # is kinematic either way, and every bone's STB is its own bind inverse
        # -- so which bone carries the weight does not move the skinned point.
        #
        # THE ONE THING BUTTCOL DOES NOT NEED. This proxy is CHAIN-DRIVEN: its
        # job is to follow the simulated cloth. Redirecting a lot of its mass
        # onto kinematic skeleton ancestors would leave a proxy that no longer
        # tracks what it proxies -- worse than no proxy, because the cloth would
        # then collide against a stale surface. So the redirect is capped by
        # SHARE OF TOTAL WEIGHT, and over the cap the proxy is DECLINED. That is
        # why this could not simply be copied across from ButtCol.
        _declared = set(re.findall(r'<bone\s+name="([^"]+)"', txt))
        _avail = set((src_sh.bone_weights or {}).keys())
        _acc: "dict[str, list]" = {}
        _redirects: "dict[str, str]" = {}
        _w_total = _w_moved = 0.0
        for bname, pairs in (src_sh.bone_weights or {}).items():
            s_pairs = [(rep_pos[int(vi)], float(w)) for vi, w in pairs
                       if int(vi) in rep_pos and float(w) > 1e-4]
            if not s_pairs:
                continue
            _w_total += sum(w for _v, w in s_pairs)
            tgt = bname
            if _declared and bname not in _declared:
                tgt = _nearest_declared_ancestor(bname, _declared, _avail)
                if tgt is None:
                    raise _ColliderDeclined(
                        f"no XML-declared ancestor for {bname!r}")
                _redirects[bname] = tgt
                _w_moved += sum(w for _v, w in s_pairs)
            _acc.setdefault(tgt, []).extend(s_pairs)
        if _w_total > 0 and (_w_moved / _w_total) > _nc()._SKIRT_PROXY_REDIRECT_MAX:
            raise _ColliderDeclined(
                f"{_w_moved / _w_total:.0%} of the proxy's weight would move to "
                f"kinematic ancestors (cap {_nc()._SKIRT_PROXY_REDIRECT_MAX:.0%}); a "
                f"proxy that no longer follows the cloth it proxies is worse "
                f"than no proxy")
        plan = []
        for bname, s_pairs in _acc.items():
            # ACCUMULATE per vertex -- two redirected bones can land on the same
            # ancestor, and the second must not overwrite the first.
            merged: "dict[int, float]" = {}
            for vi, w in s_pairs:
                merged[vi] = merged.get(vi, 0.0) + w
            try:
                _stb = src_sh.get_shape_skin_to_bone(bname)
            except Exception as _be:
                raise RuntimeError(
                    f"skin-to-bone unreadable for {bname!r}: {_be!r}") from _be
            if _stb is None:
                raise RuntimeError(f"skin-to-bone missing for {bname!r}")
            plan.append((bname, _stb, sorted(merged.items())))
        if _redirects:
            print(f"    [skirt-proxy] {p.name}: redirected "
                  f"{len(_redirects)} undeclared bone(s) onto their nearest "
                  f"XML-declared ancestor "
                  f"({_w_moved / max(_w_total, 1e-9):.1%} of weight): "
                  f"{sorted(_redirects.items())[:4]}", file=sys.stderr)
        # Add-all-then-set-all, same as `_add_butt_collider_patch` and for the
        # same measured reason (#identity-stb-collider): `add_bone` resets the
        # STBs already on the shape, so interleaving ships every bone but the
        # last at identity. Worse here than on ButtCol -- this proxy is
        # CHAIN-DRIVEN, so a dropped bind inverse misplaces a shape the
        # simulation itself moves.
        for bname, _stb, _s_pairs in plan:
            ns.add_bone(bname)
        for bname, _stb, _s_pairs in plan:
            ns.set_skin_to_bone_xform(bname, _stb)
        for bname, _stb, s_pairs in plan:
            ns.setShapeWeights(bname, s_pairs)
            wrote += len(s_pairs)
        if not wrote:
            raise RuntimeError("skirt proxy got no weights")
        if src_sh.has_global_to_skin:
            ns.set_global_to_skin(src_sh.global_to_skin)
        try:
            if src_sh.partitions:
                ns.set_partitions([src_sh.partitions[0]], [0] * len(ntris))
        except Exception:
            pass
        for slot in list((ns.textures or {}).keys()):
            try:
                ns.set_texture(slot, "")
            except Exception:
                pass
        pr = getattr(ns, "properties", None)
        if pr is not None and hasattr(pr, "flags"):
            pr.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
        if hasattr(ns, "flags"):
            ns.flags = _nc()._BUST_SPLIT_HIDDEN_FLAGS
    except _ColliderDeclined as _dc:
        print(f"    [skirt-proxy] {p.name}: DECLINED -- {_dc}", file=sys.stderr)
        return 0
    except Exception as _ce:
        _note_pass_failure("_add_skirt_collider_proxy/author", _ce)
        return 0

    _nc()._hide_virtual_body(nf)
    try:
        atomic_nif_save(nf, p)
    except Exception as _se:
        _note_pass_failure("_add_skirt_collider_proxy/save", _se)
        return 0
    ok = False
    try:
        nf2 = pyn.NifFile(filepath=str(p))
        post = {s.name for s in nf2.shapes}
        ok = (pre_extra <= _all_extra(nf2) and pre_shapes <= post
              and _nc()._SKIRT_PROXY_NAME in post)
    except Exception:
        ok = False
    if not ok:
        try:
            atomic_write_bytes(p, backup)
        except Exception:
            pass
        print(f"  WARN: skirt proxy on {p.name} lost shapes/extra-data -- "
              f"RESTORED original, proxy skipped", file=sys.stderr)
        return 0

    m = re.search(r'([ \t]*)<per-triangle-shape\s+name="'
                  + re.escape(donor) + r'".*?</per-triangle-shape>', txt, re.S)
    if m is None or f'name="{_nc()._SKIRT_PROXY_NAME}"' in txt:
        return 1
    block = m.group(0).replace(f'name="{donor}"',
                               f'name="{_nc()._SKIRT_PROXY_NAME}"', 1)
    new_txt = txt[:m.end()] + "\n" + m.group(1) + block.lstrip() + txt[m.end():]
    try:
        atomic_write_bytes(xml, new_txt.encode(codec))
    except Exception as _xe:
        _note_pass_failure("_add_skirt_collider_proxy/xml", _xe)
        return 1
    print(f"    [skirt-proxy] {p.name}: +{len(rep_old)}v/{len(ntris)}t from "
          f"{src_sh.name!r} ({unrep} unrepresented cloth verts), XML cloned "
          f"from {donor!r}")
    return 1

def _lift_chain_roots_off_body(chain: dict, src_nif, dst_path=None) -> int:
    """Translate each physics chain until no bone of it rests inside the body.

    Mutates `chain` in place (ROOT entries only) and returns the number of
    chains lifted. Gated on `CHAIN_REST_LIFT`, DEFAULT ON since 2026-08-11.

    This line described the opposite for the four weeks after that flip, i.e.
    for the whole time the pass has been shipping the in-game butt-clip fix.
    The binding in `nif_convert.py` is the authority; a sentence never is.
    `tests/test_flag_default_prose.py` now checks this one against it.

    `dst_path` is telemetry only: every root's decision, INCLUDING the skips and
    their reason, goes to the run's JSONL sink. Without it the pass is
    unverifiable in bulk -- it moves bones rather than verts, so nothing
    downstream can see whether it fired.
    """
    if not _nc().CHAIN_REST_LIFT or not chain:
        return 0
    # NOT wrapped in a bare `except: return 0`. Its sibling was, and it swallowed
    # a NameError as "nothing to move" -- a broken pass reading exactly like a
    # clean one. Genuine absences return 0 explicitly; anything else propagates
    # to the caller, which reports it.
    from scipy.spatial import cKDTree

    def _say(root, **kw):
        if dst_path is None:
            return
        try:
            fit_metrics.record_chain_shift(
                dst_path, dict(pass_="chain-rest-lift", root=str(root), **kw))
        except Exception:
            pass

    body_p = _find_user_preset_body()
    if not body_p:
        _say("*", skipped="no UBE body to measure against")
        return 0
    # Cached as one tuple: this pass runs once per SHAPE COPY (8x over a weight
    # pair on the test piece), and a 29k-vert tree per call is pure waste.
    cached = _nc()._CHAIN_LIFT_BODY_CACHE.get(Path(body_p))
    if cached is None:
        _bnif, _V, _N = _nc()._cached_ube_body_verts(Path(body_p))
        if _V is None or _N is None or len(_V) < 3:
            _say("*", skipped="UBE body has no usable surface")
            return 0
        _V = np.asarray(_V, np.float64)
        _N = np.asarray(_N, np.float64)
        cached = (_V, _N,
                  _cached_body_morph_amplitude(_find_ube_body_osd(), _N,
                                               len(_V)),
                  cKDTree(_V))
        _nc()._CHAIN_LIFT_BODY_CACHE[Path(body_p)] = cached
    # `amp` is how far each body vert can still grow OUTWARD at runtime. None
    # (no OSD) is not fatal -- the margin falls back to the flat base.
    V, N, amp, tree = cached
    try:
        src_nodes = src_nif.nodes
    except Exception:
        return 0

    gpos = _nc()._chain_rest_globals(chain, src_nodes)
    if not gpos:
        _say("*", skipped="could not resolve chain bone globals")
        return 0
    ok, checked, worst = _nc()._chain_frame_ok(src_nif, gpos)
    if not ok:
        # Refusing is the whole point: an untrusted frame produces a confident
        # wrong lift, which is worse than no lift at all.
        _say("*", skipped="node-tree frame disagrees with the skinning",
             checked=int(checked), worst=round(float(worst), 4),
             tol=_nc().CHAIN_LIFT_FRAME_TOL)
        return 0

    # How far each body vert can still grow OUTWARD at runtime. None (no OSD) is
    # not fatal -- it just means the margin falls back to the flat base.
    amp = _cached_body_morph_amplitude(_find_ube_body_osd(), N, len(V))
    tree = cKDTree(V)

    # Which chain bones actually carry cloth. Same 0.2 threshold the sibling
    # shift samples at, so "this chain drives cloth" means one thing here.
    cloth_bones: "set[str]" = set()
    for sh in src_nif.shapes:
        for b, pairs in (sh.bone_weights or {}).items():
            if b in chain and b not in cloth_bones and any(
                    float(w) > 0.2 for _v, w in pairs):
                cloth_bones.add(b)

    moved = 0
    # GARMENT roots only. The skeleton anchors in `chain` are the actor's own
    # bones; the armour NIF's copies are placeholders the actor overrides at
    # runtime, so translating them is both wrong in intent and inert in effect.
    for root, sub in _nc()._chain_root_subtrees(chain, custom_only=True).items():
        # A chain with no cloth on it has nothing to un-clip, and this is also
        # the gate that keeps the pass off things that are not garment chains at
        # all. `custom_only` only means "not a skeleton bone": on a real cuirass
        # it elected `NPC` as a garment root. That subtree sat 12.28u clear so
        # it skipped, but it skipped on the arithmetic, not on a rule, and a
        # root that qualifies translates whatever hangs under it. Requiring
        # skinned cloth is the same rule the sibling shift uses, and it excludes
        # `NPC` for the right reason instead of by a name match -- `_is_nif_root`
        # does NOT match it (measured: skeleton=False, nif_root=False).
        if not (sub & cloth_bones):
            _say(root, skipped="no skinned cloth on this chain",
                 bones=len(sub))
            continue
        # SOFT-BODY jiggle bones (breast/butt/belly) cannot be ROOTS -- they are
        # `_is_skeleton_bone`, so `custom_only` already dropped them, and adding
        # a second predicate for that here is how two detectors for one concept
        # drift apart. They CAN sit inside a garment chain's subtree though, and
        # there they must not be candidates for the worst bone: they rest inside
        # the body BY DESIGN, so one would peg the lift and push a garment out
        # to clear the body's own physics rig.
        bones = [b for b in sorted(sub) if b in gpos
                 and not _is_soft_body_physics_bone(b)]
        if not bones:
            _say(root, skipped="no resolvable bone positions on this chain")
            continue
        P = np.array([gpos[b] for b in bones], np.float64)
        _d, j = tree.query(P, k=1)
        # Signed clearance along the body's own outward normal. Negative = the
        # rest pose is behind the skin.
        clear = np.einsum('ij,ij->i', P - V[j], N[j])
        # A per-bone ARRAY even with no OSD, not a bare 0.0: as a scalar the
        # morph term collapses `want` to a 0-d array and the `want[w]` below
        # raises IndexError -- on the no-OSD path only, which is exactly the
        # path least likely to be exercised while developing.
        grow = (_nc().CHAIN_LIFT_MORPH_FACTOR * amp[j] if amp is not None
                else np.zeros(len(bones), np.float64))
        want = np.minimum(_nc().CHAIN_LIFT_BASE + grow, _nc().CHAIN_LIFT_WANT_MAX)
        need = want - clear
        w = int(np.argmax(need))
        lift = float(need[w])
        if lift < _nc().CHAIN_LIFT_MIN:
            _say(root, skipped="every bone already clears the body",
                 bones=len(bones), worst_bone=bones[w],
                 clearance=round(float(clear[w]), 4),
                 wanted=round(float(want[w]), 4),
                 amp=round(float(amp[j[w]]) if amp is not None else 0.0, 4))
            continue
        n_hat = N[j[w]]
        nl = float(np.linalg.norm(n_hat))
        if not np.isfinite(nl) or nl < 1e-9:
            _say(root, skipped="body normal is degenerate here",
                 worst_bone=bones[w])
            continue
        n_hat = n_hat / nl
        capped = lift > _nc().CHAIN_LIFT_MAX
        if capped:
            lift = _nc().CHAIN_LIFT_MAX
        shift = n_hat * lift
        # The counter-metric: a rigid lift also moves the part of the chain that
        # was hanging free, and over-inflation scores 0.0% on a clip test, so it
        # is invisible unless something states it (#standoff-counter-metric).
        # Report the chain's PRE-LIFT median clearance and let `lift` be the
        # cost. An earlier version reported median+lift as a single "flare" and
        # that number was unreadable -- a chain hanging 8u off the body scored
        # 10.25 after a 2.0u lift and looked like the pass had thrown it into
        # orbit. How far the chain already hung is not this pass's doing.
        _say(root, moved=True, lift=round(lift, 4), capped=bool(capped),
             bones=len(bones), worst_bone=bones[w],
             clearance=round(float(clear[w]), 4),
             wanted=round(float(want[w]), 4),
             amp=round(float(amp[j[w]]) if amp is not None else 0.0, 4),
             inside=int((clear < 0).sum()),
             p50_before=round(float(np.median(clear)), 4),
             shift=[round(float(c), 4) for c in shift])
        # COPY, never mutate. `node.transform` is a VIEW onto the source node --
        # writing through it changes what the SOURCE reports, so the second
        # weight file converted from the same load inherits the lift twice.
        xf, par = chain[root]
        nxf = xf.copy()
        t = nxf.translation
        nxf.translation = (float(t[0] + shift[0]), float(t[1] + shift[1]),
                           float(t[2] + shift[2]))
        chain[root] = (nxf, par)
        moved += 1
    return moved

def _seed_flat_chain_anchors(dst_nif, src_nif) -> int:
    """Pre-create physics-chain ANCHOR bones (and their source ancestors) at their
    SOURCE GLOBAL transform, flat-parented, into a still-EMPTY dst NIF.
    #anchor-global-fix

    WHY IT MUST BE FIRST. `_precreate_custom_bone_chains`' flat branch is meant to
    do this, but it only ADDS a node that is not already there. By the time it
    runs, the injected UBE BaseShape's skin (phase 2) or an earlier shape's
    `add_bone` has already created every skeleton bone FLAT AT IDENTITY, so the
    source global is silently dropped. It cannot be repaired afterwards either:
    pynifly has no node-transform setter that survives a save -- assignment
    updates the Python-side properties and the written file is UNCHANGED
    (measured both by field-mutation and whole-transform assignment; the DLL
    exposes getNodeTransform but no setter). Whoever creates the node FIRST wins,
    so we create it here.

    Left unfixed the anchor sits at the origin while every chain bone parented
    onto it keeps its source-LOCAL transform -- which assumed a parent at
    pelvis/COM height -- so the entire physics rig lands ~69u low, at floor level.
    In game the cloth hangs down through the ground and collapses slowly,
    surviving `smp reset`. It is invisible to every other check: LOCAL transforms,
    verts, skin weights, weight-threshold binding, skin-to-bone binds, partitions,
    the physics XML and the morph tables all compare IDENTICAL. Only a GLOBAL walk
    finds it.

    Anchors are found the same way `_precreate_custom_bone_chains` finds them:
    walk up from each CUSTOM (non-actor-resolvable) chain bone to the first bone
    the ACTOR supplies. Nested/arm-anchored rigs are deliberately NOT seeded --
    they need real parent links, which the existing nested branch builds.
    Returns the number of nodes seeded."""
    if not _nc().ANCHOR_GLOBAL_FIX or _nc().CHAIN_TO_SOFTBODY:
        return 0
    try:
        src_nodes = src_nif.nodes
    except Exception:
        return 0
    if not src_nodes:
        return 0
    try:
        allb: "set[str]" = set()
        for sh in src_nif.shapes:
            allb |= set(sh.bone_names or [])
        try:
            sp = getattr(src_nif, "filepath", None)
            if sp:
                xt = _read_source_hdt_xml_text(Path(sp), nif=src_nif)
                if xt:
                    allb |= {b for b in _xml_referenced_bone_names(xt)
                             if b in src_nodes}
        except Exception:
            pass
        custom = [b for b in allb if not _actor_can_resolve_bone(b)]
        if not custom:
            return 0                      # no garment chain -> nothing to anchor

        anchors: "set[str]" = set()
        for gb in custom:
            if _is_soft_body_physics_bone(gb):
                continue
            cur, seen = gb, set()
            while cur and cur not in seen:
                seen.add(cur)
                n = src_nodes.get(cur)
                if n is None:
                    break
                pn = n.parent.name if n.parent is not None else None
                if pn is None or _actor_can_resolve_bone(pn):
                    if pn:
                        anchors.add(pn)
                    break
                cur = pn
        if not anchors:
            return 0
        # Nested and arm-anchored rigs build real parent links elsewhere; seeding
        # them flat would be the June skirt-sag regression. Same gate as
        # `_precreate_custom_bone_chains`.
        if (_nc().NESTED_CHAIN_ANCHORS
                or all(_nc()._is_arm_anchor(a) for a in anchors)):
            return 0
        # #per-anchor-seed. Whole-file OFF switch on ANY upper-body anchor. One
        # vestigial upper-body chain takes the pelvis down with it -- see the
        # flag. Opt-in decides per ANCHOR in the loop below instead.
        # #seed-spine-anchors also implies the per-anchor path when every
        # upper-body anchor in the file is a SPINE -- otherwise this whole-file
        # return takes the file before the loop can seed anything, and the flag
        # would read as inert.
        _ub = [a for a in anchors if _nc()._is_upper_body_anchor(a)]
        _spine_only = bool(_ub) and all(_nc()._is_spine_anchor(a) for a in _ub)
        if (not _nc().PER_ANCHOR_ANCHOR_SEED
                and not (_nc().SEED_SPINE_ANCHORS and _spine_only)
                and any(_nc()._is_upper_body_anchor(a) for a in anchors)):
            return 0

        seeded = []
        existing = set(dst_nif.nodes.keys())
        for a in sorted(anchors):          # sorted: #deterministic-set-iteration
            # Upper-body anchors are skipped HERE rather than aborting the whole
            # file: flat-seeding one would freeze a sleeve/pauldron that has to
            # swing with the limb, but that is a reason to leave THAT anchor to
            # the nested branch, not to abandon the pelvis beside it. With
            # PER_ANCHOR_ANCHOR_SEED off this is unreachable -- the early return
            # above already took the file -- so the default path is unchanged.
            # #seed-spine-anchors: a SPINE anchor is torso-axis and behaves like
            # the pelvis, so it may be seeded when `SEED_SPINE_ANCHORS` is on --
            # which is the DEFAULT. It is opt-OUT (CBBE2UBE_NO_SEED_SPINE_ANCHORS)
            # and confirmed in game; this used to call it "the opt-in". Arm,
            # clavicle, shoulder, neck and head stay excluded either way.
            if _nc()._is_arm_anchor(a) or (
                    _nc()._is_upper_body_anchor(a)
                    and not (_nc().SEED_SPINE_ANCHORS and _nc()._is_spine_anchor(a))):
                continue
            cur, seen2 = a, set()
            while cur and cur not in seen2:
                seen2.add(cur)
                src_c = src_nodes.get(cur)
                if src_c is None:
                    break
                if cur not in existing:
                    try:
                        dst_nif.add_node(cur, src_c.global_transform, parent=None)
                        existing.add(cur)
                        seeded.append(cur)
                    except Exception:
                        pass
                p = src_c.parent
                cur = p.name if p is not None else None
        if seeded:
            print(f"    [anchor-global-fix] seeded {len(seeded)} chain anchor(s) "
                  f"at source global: {sorted(seeded)[:6]}")
        return len(seeded)
    except Exception as _e:
        _note_pass_failure("_seed_flat_chain_anchors", _e)
        return 0

def _precreate_custom_bone_chains(dst_nif, src_nif, bone_names) -> int:
    """Recreate, in `dst_nif`, the node sub-trees for any armor-specific
    (non-skeleton) physics bones a shape is skinned to — INCLUDING their
    unweighted parent-chain bones up to the standard skeleton bone they
    anchor on — with the source local transforms + parent links intact.

    Must be called AFTER the shape's `skin()` but BEFORE its `add_bone`
    loop: add_bone then reuses these pre-created nodes (verified) instead
    of adding a fresh flat/identity one. Returns the number of nodes added.
    """
    if _nc().CHAIN_TO_SOFTBODY:
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
    # XML-referenced bones the ACTOR cannot supply. Tracked separately because
    # the `custom` filter below would otherwise discard them again on the same
    # name heuristic that lost them in the first place.
    _xml_must_keep: "set[str]" = set()
    try:
        _allb = set(bone_names)
        for _sh in src_nif.shapes:
            _allb |= set(_sh.bone_names)
        # Seed from the physics XML too. Pure CONSTRAINT bones -- the skirt/flap
        # chains a bone-driven SMP garment hangs from (SkirtFBone, HDT_FS, the
        # `_01` anchor of an arm chain, ...) -- carry ZERO skin weight, so they
        # appear in NO shape's bone list and the shape-driven copy never
        # recreates their NODES. But HDT-SMP walks the NIF hierarchy to build its
        # kinematic chain; with the chain's parent nodes gone it has nothing to
        # hang from and the garment free-falls or stretches to the origin.
        # #smp-constraint-bones
        #
        # Two ways this set was previously computed too NARROW, each losing a
        # different armour (both measured, see _xml_referenced_bone_names and
        # _actor_can_resolve_bone):
        #   * only `<bone name=>` was harvested, so constraint-only bodies were
        #     never seen at all;
        #   * `_is_skeleton_bone` cleared custom bones whose names happen to
        #     contain a body-part substring ("LArmA 01" contains "arm").
        try:
            _sp = getattr(src_nif, "filepath", None)
            if _sp:
                _xt = _read_source_hdt_xml_text(Path(_sp), nif=src_nif)
                if _xt:
                    for _xb in _xml_referenced_bone_names(_xt):
                        if _xb in src_nodes and not _actor_can_resolve_bone(_xb):
                            _allb.add(_xb)
                            _xml_must_keep.add(_xb)
        except Exception:
            pass
        # sorted(): _allb is a set, and bone_names decides the NODE CREATION
        # order downstream -- set order follows the hash seed and made the
        # written bone tree nondeterministic.
        bone_names = sorted(_allb)
    except Exception:
        pass
    # Recreate SOURCE bind for CUSTOM (non-skeleton) bones. For physics garments
    # also restore soft-body jiggle bones (breast/butt/belly): their HDT physics
    # is seeded from the NIF bind; a flat/missing parent collapses chest cloth.
    # Gated on a custom chain existing; plain body armour is untouched.
    custom = [b for b in bone_names
              if not _is_skeleton_bone(b) or b in _xml_must_keep]
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
            # Stop at a bone the ACTOR supplies, not at one whose NAME merely
            # looks skeletal. With `_is_skeleton_bone` here the walk up from
            # `LArmA 02` stopped at `LArmA 01` -- a custom chain bone matching
            # the "arm" substring -- and recorded IT as the anchor, so the
            # anchor set filled with chain bones and the real anchor
            # (`NPC L Forearm`) never appeared. Verified by instrumenting a
            # real conversion.
            if _pn0 is None or _actor_can_resolve_bone(_pn0):
                if _pn0:
                    _heur_anchors.add(_pn0)
                break
            _cur = _pn0
    # `all` for the arm case, not `any` -- see _ARM_ANCHOR_KEYWORDS. A file whose
    # chains ALL hang off an arm has no pelvis-anchored chain to put at risk.
    use_nested = (
        _nc().NESTED_CHAIN_ANCHORS
        or any(_nc()._is_upper_body_anchor(a) for a in _heur_anchors)
        or (bool(_heur_anchors)
            and all(_nc()._is_arm_anchor(a) for a in _heur_anchors)))
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
    if _nc().PELVIS_REANCHOR_CHAINS:
        try:
            _nc()._reanchor_nif_root_chains(chain, anchors, src_nodes)
        except Exception:
            pass
    # AFTER the re-anchor, so it adds to whatever local transform that left --
    # the two compose, and running first would have the re-anchor overwrite it.
    if _nc().CHAIN_REST_LIFT:
        # dst path for telemetry only; absent -> the pass still runs, it just
        # cannot record. Never let a missing path skip a shift.
        _dp = getattr(dst_nif, "filepath", None) or getattr(
            dst_nif, "filename", None)
        # Snapshot the root translations before the pass runs. The node loop
        # further down only ADDS nodes that don't already exist, and a garment
        # chain root is normally already present, so a root the pass moves must
        # have its new transform re-applied below or the shift is silently
        # discarded.
        _before = {b: tuple(float(c) for c in x.translation)
                   for b, (x, _p) in chain.items()}
        # `_on`, not `_flag`: this module imports `flag as _flag`, and a loop
        # variable of that name shadows it for the rest of the function. Nothing
        # in this function calls `_flag(...)` today, so it was harmless -- but
        # the next person to add a flag lookup here would silently get a
        # BOOLEAN instead of the function.
        for _label, _fn, _on in (
                ("chain-rest-lift", _lift_chain_roots_off_body,
                 _nc().CHAIN_REST_LIFT),):
            if not _on:
                continue
            try:
                _n = _fn(chain, src_nif, dst_path=_dp)
                if _n:
                    print(f"    [{_label}] moved {_n} chain(s) off the UBE body")
            except Exception as _e:
                # RECORDED, not swallowed: a silent failure here is
                # indistinguishable from "the pass ran and found nothing to do".
                print(f"    [{_label}] FAILED: {type(_e).__name__}: {_e}")
                _note_pass_failure(_label, _e, _dp)
        # Roots whose transform a pass actually changed. The node loop below only
        # ADDS nodes that do not already exist, and a garment chain root is
        # normally already present -- `_copy_shape` adds it as a skin bone before
        # this runs. So without re-applying here the shift is computed, reported,
        # and silently DISCARDED: measured on a real cuirass as "moved 10
        # chain(s)" with ZERO nodes differing in the written file.
        _shifted = {b: chain[b][0] for b, t in _before.items()
                    if tuple(float(c) for c in chain[b][0].translation) != t}
        # Re-apply onto nodes that already exist. Restricted to roots a pass
        # itself moved, and those are garment bones by construction
        # (custom_only), so this cannot touch a skeleton node.
        for _b, _x in (_shifted or {}).items():
            _nd = dst_nif.nodes.get(_b)
            if _nd is None:
                continue           # not yet created -> the add loop uses `chain`
            try:
                _nd.transform.translation = tuple(
                    float(c) for c in _x.translation)
            except Exception:
                pass
    pyn = _nc()._pynifly()
    existing = set(dst_nif.nodes.keys())
    added = 0
    # Re-create each anchor's full source ancestor chain with source local
    # transforms + parent links. HDT-SMP walks the NIF hierarchy to build its
    # kinematic chain; a flat anchor breaks that even if every bone has the
    # correct global position.
    for a in sorted(anchors):  # sorted: #deterministic-set-iteration
        # Per-anchor, not per-file: an arm-anchored chain must keep its parent
        # link so it follows the limb, while a pelvis-anchored chain in the SAME
        # nif keeps flat behaviour unchanged. See _ARM_ANCHOR_KEYWORDS.
        if not (use_nested or _nc()._is_arm_anchor(a)):
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
                try:
                    xf = src_c.global_transform
                except Exception:
                    xf = None
                if xf is None:
                    xf = pyn.TransformBuf()
                    xf.set_identity()
                # NB when `cur` ALREADY exists the source global above is simply
                # DROPPED -- `_copy_shape`'s add_bone created it flat at IDENTITY
                # during skin install, so the anchor stays at the origin and every
                # chain bone parented onto it (which kept its source-LOCAL
                # transform, assuming a parent at pelvis/COM height) lands ~69u
                # low. Correcting it HERE does not survive: a later rebuild
                # re-emits the nodes and discards it -- measured, the same
                # discard trap the `_shifted` re-apply above guards against (and
                # that the removed chain-body-shift pass hit; see git history).
                # The repair for THAT is `_seed_flat_chain_anchors`, which runs
                # FIRST into the empty NIF. #anchor-global-fix
                #
                # BUT WHEN IT DOES NOT EXIST IT MUST STILL BE CREATED HERE.
                # #chain-anchor-recreate -- REPORTED IN GAME as "skirts appear
                # stretched". Deleting this add along with the already-exists case
                # broke the chain writer at the bottom of this function, which can
                # only attach a bone once its PARENT is in `existing`: with the
                # anchor absent the very first chain bone never qualifies, the
                # loop makes no progress and gives up, and `add_bone` then creates
                # every weighted chain bone flat at IDENTITY under the root.
                #
                # Measured on a chain-driven skirt, v1.2 -> 1.3-alpha:
                #   RFSd 1  z 79.80 -> 11.57
                #   RFSd 2  z 84.01 ->  0.00   (parent became `Scene Root`)
                #   RFSd 3  z 85.75 ->  0.00
                # 7,379 of 9,290 verts hang off those bones, so the skirt
                # stretched from the hip to the origin between the feet. Censused
                # over the live pack: 44 of 446 chain-carrying pieces, 594 bones,
                # 167,920 verts -- skirts, vests, robes, belts, a Skaal torso.
                #
                # This is a no-op wherever the anchor already survived, which is
                # why it cannot reintroduce the #anchor-global-fix trap above.
                if _nc().CHAIN_ANCHOR_RECREATE and cur not in existing:
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

def _mod_xml_index(source_mod_root: Path) -> list:
    """Cached recursive listing of every ``*.xml`` under a source mod's
    ``meshes/`` subtree. The XML set is static for the duration of a
    conversion run, so this rglob — which otherwise re-runs for EVERY
    armor NIF that lives in the same mod — is memoized per mod root.
    Returns the same list (same order) the old inline rglob produced."""
    key = str(source_mod_root)
    cached = _nc()._HDT_XML_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    xmls: list = []
    for marker in ("meshes", "Meshes"):
        mesh_dir = source_mod_root / marker
        if mesh_dir.is_dir():
            xmls = list(mesh_dir.rglob("*.xml"))
            break
    _nc()._HDT_XML_INDEX_CACHE[key] = xmls
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
    # armour's own config (e.g. `LongHair_1.nif` <-> `LongHair.xml`), no matter
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
            for xml_kw, nif_kws in _nc().HDT_XML_KEYWORDS.items():
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
    if _nc().CHAIN_TO_SOFTBODY:
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
                norm = _nc()._safe_data_rel(rel)
                if norm is None:
                    return False
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
        dst = set(dst_bone_names or ())
        # Tolerate the "[Xxx]" node-id suffix on either side when matching.
        def _norm(x):
            return x.split('[', 1)[0].strip()
        dst_norm = {_norm(b) for b in dst}
        from .hdt_xml_gen import detect_physics_chains
        chain_bones = {b for ch in detect_physics_chains(xml_bones)
                       for b in ch.bones}
        # (1) A RECOGNIZED physics chain the converted skeleton lacks -> regen.
        if any(_norm(b) not in dst_norm and b not in dst for b in chain_bones):
            return True
        # (2) The XML drives MANY missing bones even when their naming isn't a
        # recognized chain pattern (space-separated custom rigs like "CustomChain 1",
        # "HDT_BSN 1" that detect_physics_chains can't parse). Leaving the source
        # XML points FSMP at bones that don't exist -> unconstrained soft body ->
        # divergence -> exploded spiky mass. Regenerate a stable soft-body instead.
        # #hdt-many-missing-regen
        missing = sum(1 for b in xml_bones
                      if _norm(b) not in dst_norm and b not in dst)
        return missing >= _nc()._HDT_REGEN_MISSING_BONES
    except Exception as _e:
        # False here means "keep the author's XML", which is wrong when the
        # conversion stripped its chain bones: say so instead of hiding it.
        _note_pass_failure("_source_hdt_needs_missing_chain_bones", _e)
        return False

def _ube_conform_body_tree(weight_suffix: str):
    """Cached (verts, cKDTree) of the UBE reference female body for the given
    weight -- the morph target the converted cloth is warped into. Used to
    measure how tightly a cloth carrier hugs the body. None if unavailable."""
    key = weight_suffix if weight_suffix in ("_0", "_1") else "_1"
    if key in _nc()._ube_conform_tree_cache:
        return _nc()._ube_conform_tree_cache[key]
    val = None
    try:
        from scipy.spatial import cKDTree
        bp = _find_ube_femalebody(key)
        if bp is not None:
            nf = _nc()._pynifly().NifFile(filepath=str(bp))
            b = next((x for x in nf.shapes if x.name == "BaseShape"), None) \
                or (nf.shapes[0] if nf.shapes else None)
            if b is not None:
                bv = np.asarray(b.verts, np.float64)
                val = (bv, cKDTree(bv))
    except Exception:
        val = None
    _nc()._ube_conform_tree_cache[key] = val
    return val

def _carrier_is_body_conforming(shape, weight_suffix: str) -> bool:
    """True if this cloth carrier hugs the body so tightly that a GENERATED
    per-vertex soft-body would clobber its BodyMorph (skin-tight leggings /
    pantyhose) -- such a shape must stay skinned+morphable. False for loose
    hanging cloth (skirts/capes/tabards) that legitimately needs the sim.
    #tight-softbody-gate. Fails OPEN (False = keep soft-body) on any error or
    missing body ref, so a measurement gap never strips physics."""
    try:
        bt = _ube_conform_body_tree(weight_suffix)
        if bt is None:
            return False
        _, tree = bt
        v = np.asarray(shape.verts, np.float64)
        if len(v) < 30:
            return False
        d, _ = tree.query(v)
        frac = float((d < _nc()._SOFTBODY_CONFORM_STANDOFF).mean())
        p95 = float(np.percentile(d, 95))
        return frac >= _nc()._SOFTBODY_CONFORM_FRAC and p95 <= _nc()._SOFTBODY_CONFORM_P95_CAP
    except Exception as _ce:
        # Was a bare `return False`. This is a CLASSIFIER, so a swallowed
        # exception does not merely skip work -- it answers "not body-conforming"
        # and routes the carrier down the other branch. A wrong answer is worse
        # than no answer, and it left no trace at all.
        _note_pass_failure("_carrier_is_body_conforming", _ce)
        return False

def _generate_hdt_xml_for_dst(dst_path: "Path", only_loose: bool = False) -> "str | None":
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
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return None

    # First-person viewmodels never simulate cloth, and their per-vertex shapes collide
    # BY NAME with the third-person ones in the actor's merged SMP system. See
    # _is_first_person_mesh -- this is the layered-cloth equip crash. Bail before the stale
    # cleanup below so a previously-generated first-person XML is still removed.
    _first_person = _nc()._is_first_person_mesh(dst_path, nf)

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

    if _first_person:
        return None

    # Reuse the BODYTRI carrier picker as the "cloth shape" classifier:
    # every textured, non-placeholder, non-rigid-prop shape qualifies.
    # exclude_body=True: the body (BaseShape/VirtualBody) is the COLLIDER
    # (per-triangle, below), NEVER a simulated per-vertex cloth — without this
    # a body-swap NIF picked its injected BaseShape as the cloth carrier, so
    # the body flopped as soft-body while the real cape got no physics at all.
    carriers = _pick_bodytri_carriers(nf, exclude_body=True)
    # #shadowed-chain-skirt: _pick_bodytri_carriers returns exactly ONE shape --
    # correct for BODYTRI (one morph carrier per NIF), WRONG as this generator's
    # cloth classifier. A NIF with several cloth shapes gets only the top-ranked
    # one, so a chain-driven skirt is silently shadowed by a higher-ranked
    # sibling and its chain bones never reach detect_physics_chains -> chains=[]
    # -> the chainless gate returns None -> the skirt ships with NO physics and
    # the leg walks straight through it.
    # Measured: a common-clothes dress with shapes {Corset, Dress} -- "corset"
    # outranks "dress" in CLOTH_KEYWORDS, so the 12-bone (SkirtF/B/L/R Bone01-03)
    # skirt was dropped and the tight-softbody gate then dropped the Corset too,
    # leaving no XML at all. Its structurally identical siblings, whose shapes are
    # {Top, Skirt}, ranked "skirt" first and emitted physics normally.
    # Narrow fix on purpose: add back ONLY cloth shapes that actually carry
    # detected physics chains. Those are authored to simulate, so this cannot
    # invent soft-body for a rigid piece (the #fur-auto-smp / #chainless-cloth-only
    # explosion class) -- chainless shapes are still excluded.
    #
    # DEFAULT OFF since 2026-07-21. Shipping this ON gave a common-clothes dress
    # physics it had never had, and IN-GAME THE SKIRT COLLAPSED / fell away from the
    # actor. The chain bones were all present as nodes, all referenced by the XML,
    # parented exactly like the sibling dresses that emit fine -- so the generated
    # collision-only XML simply is not stable driving THIS chain, and we cannot tell
    # in advance which chains it will be stable on. A collapse is far worse than the
    # clipping it was meant to fix, and the safe alternative covers the same ground:
    # leaving the piece KINEMATIC lets `_match_leg_motion_to_body` treat the inert
    # chain as ordinary skinning and track the leg (measured on that dress:
    # 113 -> 37 newly-exposed verts, no static change, drape untouched).
    # Turn on with CBBE2UBE_CHAIN_SKIRT_PHYSICS=1 to revisit per-garment.
    if _nc().CHAIN_SKIRT_PHYSICS:
        try:
            _have = {c.name for c in carriers}
            for _s in _nc()._cloth_candidate_shapes(nf):
                if _s.name in _have:
                    continue
                if hdt_xml_gen.detect_physics_chains(set(_s.bone_names or [])):
                    carriers.append(_s)
                    _have.add(_s.name)
        except Exception:
            pass
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
    # while updating the shape (a layered-cloth cuirass, crash 2026-07-09;
    # disabling the generated XML stopped the crash, confirmed in-game). This is
    # the same failure `_is_unconstrained_collision_pair` guards, but that gate
    # only fires when the NIF has a body collider -- the FIRST-PERSON NIF has
    # none, so its XML still shipped and FSMP applied those per-vertex shapes by
    # NAME into the actor's merged SMP system, reaching the third-person shapes.
    # Skin-strip and physics must go together: keep layered cloth kinematic.

    # #tight-softbody-gate: on the GENERATE-when-source-had-no-physics path,
    # drop carriers that hug the body tightly. A generated per-vertex soft-body
    # would overwrite their verts every frame and kill BodyMorph (skin-tight
    # leggings/pantyhose stop following body sliders). They stay skinned +
    # morphable instead. Loose hanging cloth is untouched. Never applied when
    # regenerating a replacement for an armor that HAD authored physics.
    if only_loose and _nc()._TIGHT_SOFTBODY_GATE and carriers:
        _suf = "_1"
        for _s in ("_0", "_1"):
            if dst_path.stem.endswith(_s):
                _suf = _s
                break
        _kept, _dropped = [], []
        for c in carriers:
            (_dropped if _carrier_is_body_conforming(c, _suf) else _kept).append(c.name)
        if _dropped:
            carriers = [c for c in carriers if c.name in _kept]
            try:
                print(f"  tight-softbody gate: {', '.join(_dropped)} kept "
                      f"skinned+morphable (body-conforming; no generated soft-body)",
                      file=sys.stderr)
            except Exception:
                pass

    # #rigid-majority-softbody-gate: a shape that is mostly RIGID must not be
    # declared simulated cloth just because a small flap on it is chain-driven.
    #
    # The soft-body decision is per SHAPE; chain drive is per VERTEX. Measured
    # cause of 7.5-8.1% bust clipping on a vanilla-style cuirass: 5511 verts, 27
    # bones, THREE custom skirt-flap bones driving 295 verts (5.4%). The source
    # shipped NO physics at all; we invented a per-vertex soft-body over the
    # whole shape, so the 94.6% of rigid cuirass covering the bust became
    # simulated cloth -- and simulated cloth does not follow BodyMorph. Breast
    # follow measured 0.0000, the UBE breast punched straight through, and no
    # clearance pass could reach it (three were tried and all failed).
    #
    # THRESHOLD, and it is a judgement call, so here is the census it rests on.
    # Over the 216 GENERATED soft-body shapes in a shipped pack the chain
    # fraction is continuous with no natural gap -- p25 0.030, p50 0.355,
    # p75 0.788 -- and 27% sit under 0.05, i.e. declared cloth while essentially
    # nothing on them is chain-driven. 0.15 sits clear of the measured 0.0535
    # failure and well below the median, so it reads as "under 15% chain-driven
    # is not a cloth garment" rather than as a fit to one piece.
    #
    # THE TRADE, stated: the flap loses its swing (the whole shape stays
    # kinematic) and the panel keeps its morph-follow. Splitting the shape would
    # give both and is the better long-term answer; this is the cheap half.
    # Mirrors #tight-softbody-gate, which already keeps body-conforming garments
    # skinned+morphable for the same underlying reason.
    if only_loose and _nc().RIGID_MAJORITY_SOFTBODY_GATE and carriers:
        _kept, _dropped = [], []
        for c in carriers:
            fr = _nc()._carrier_chain_fraction(c)
            (_dropped if fr < _nc().RIGID_MAJORITY_CHAIN_MIN
             else _kept).append((c.name, fr))
        if _dropped:
            _keep_names = {n for n, _f in _kept}
            carriers = [c for c in carriers if c.name in _keep_names]
            try:
                print("  rigid-majority gate: "
                      + ", ".join(f"{n} ({100 * f:.1f}% chain)"
                                  for n, f in _dropped)
                      + " kept skinned+morphable (mostly rigid; no generated "
                        "soft-body)", file=sys.stderr)
            except Exception:
                pass

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
    #
    # #no-invented-chains: NOT on the generate-fresh path. `only_loose` means
    # the source shipped no physics XML at all, so those chain bones are dead
    # in the source too and the garment is STATIC in its own mod. Driving them
    # is inventing motion, not converting it. Suppressing the chain here lets
    # the `#chainless-softbody-gate` below emit NO XML, which keeps the piece
    # kinematic with its baked clearance — the same outcome the source has.
    if only_loose and not _nc().INVENT_CHAINS_FROM_DEAD_BONES:
        chains = []
    else:
        chains = hdt_xml_gen.detect_physics_chains(all_bones_seen)

    # #chainless-softbody-gate: on the GENERATE-fresh path (only_loose -- the
    # source shipped NO authored physics XML), a carrier set with NO detectable
    # physics-chain bones can only yield constraint-less per-vertex soft-bodies
    # (write_armor_hdt_xml with chains=[] emits per-vertex-shapes and ZERO
    # constraints). FSMP then simulates those verts as unconstrained mass points
    # with no stiffness -> they diverge into a spiky "exploded" mass. That is
    # exactly what happens to a body-conforming fur/drape that was RIGID in the
    # source (skinned to standard skeleton + breast/butt bones, never authored
    # for simulation): the converter must NOT invent physics for it. Emit
    # nothing -> the piece stays kinematic (skinned), matching the source.
    #
    # Fires on TWO cases (both = a chainless per-vertex soft-body that FSMP would
    # diverge on):
    #   (a) only_loose (generate-fresh, source had no physics) + no chains.
    #   (b) ANY path with NO body collider + no chains -- including the missing-
    #       chain REGEN path (only_loose=False). detect_physics_chains can't see
    #       space-separated custom chains (e.g. "CustomChain 1"), so a cloth-only skirt
    #       whose authored chains are space-separated regen's to chains=[]; with a
    #       collider `_is_unconstrained_collision_pair` below catches it, but a
    #       cloth-only NIF (collider is None) previously slipped BOTH guards and
    #       shipped an exploding soft-body. A chainless soft-body with no collider
    #       has nothing to stabilise it, so emit nothing (static) either way.
    # #fur-auto-smp #chainless-cloth-only
    if not chains and (only_loose or body_shape_name is None):
        try:
            print(f"  chainless-softbody gate: {dst_path.name} kept rigid "
                  f"(no source physics + no chain bones -> no generated "
                  f"soft-body)", file=sys.stderr)
        except Exception:
            pass
        return None

    # Don't emit the FSMP equip-CTD pattern: an unconstrained collision
    # pair (cloth + per-triangle body collider, no simulated chain). The
    # unconstrained soft body diverges and the collision SIMD reads out of
    # bounds -> crash on equip. Skip the XML entirely (the piece stays
    # kinematic with the baked geometric clearance). Cloth-only NIFs (no
    # body collider) and constrained chains still emit normally.
    if _nc()._is_unconstrained_collision_pair(body_shape_name, chains):
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
        snf = nif if nif is not None else _nc()._pynifly().NifFile(filepath=str(src_nif_path))
        rel = None
        for ed in snf.rootNode.extra_data():
            if (getattr(ed, "name", None) == "HDT Skinned Mesh Physics Object"
                    and getattr(ed, "string_data", None)):
                rel = ed.string_data
                break
        if not rel:
            return None
        return _nc()._resolve_data_rel_in_vfs(rel, src_nif_path)
    except Exception:
        return None

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
    pass  # (global _SKELETON_BONES_CACHE -> _nc().<name>, split)
    if _nc()._SKELETON_BONES_CACHE is not None:
        return _nc()._SKELETON_BONES_CACHE
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
            nf = _nc()._pynifly().NifFile(filepath=str(p))
            names |= {n.lower() for n in nf.nodes.keys()}
        except Exception:
            continue
        if names:
            break
    _nc()._SKELETON_BONES_CACHE = names
    return names

def _actor_skeleton_bone_parents() -> "dict[str, str]":
    """child -> parent over the ACTOR's skeleton, raw names.

    The armour NIF's OWN node tree cannot answer this: the converter recreates
    every skeleton bone FLAT at identity parented to Scene Root by design
    (see `_seed_flat_chain_anchors`), so walking it yields `Scene Root` for
    everything. Only the actor skeleton carries the real hierarchy --
    `NPC L Butt -> ... -> NPC Pelvis [Pelv]`,
    `NPC L FrontThigh -> ... -> NPC L Thigh [LThg]`. Empty dict if no skeleton
    is found, and every caller must then decline rather than guess.
    """
    pass  # (global _SKELETON_PARENTS_CACHE -> _nc().<name>, split)
    if _nc()._SKELETON_PARENTS_CACHE is not None:
        return _nc()._SKELETON_PARENTS_CACHE
    out: "dict[str, str]" = {}
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
            nf = _nc()._pynifly().NifFile(filepath=str(p))
            for nm, nd in nf.nodes.items():
                par = getattr(nd, "parent", None)
                pnm = getattr(par, "name", None) if par is not None else None
                if pnm and pnm != nm:
                    out[nm] = pnm
        except Exception:
            continue
        if out:
            break
    _nc()._SKELETON_PARENTS_CACHE = out
    return out

def _nearest_declared_ancestor(bone: str, declared: "set[str]",
                               available: "set[str]") -> "str | None":
    """Walk the ACTOR skeleton up from `bone` to the first ancestor that the
    piece's own physics XML DECLARES and that we can actually weight to.
    None when there is no such ancestor (caller must decline)."""
    parents = _actor_skeleton_bone_parents()
    seen: "set[str]" = set()
    cur = parents.get(bone)
    while cur and cur not in seen:
        seen.add(cur)
        if cur in declared and cur in available:
            return cur
        cur = parents.get(cur)
    return None

def _read_xml_roundtrip(xml_path) -> "tuple[str, str] | None":
    """Read an HDT-SMP XML as (text, codec) so it can be written back BYTE-FOR-
    BYTE where we did not deliberately change it. Returns None if unreadable.

    #bug-12. Three passes used to spell this `Path(x).read_text(errors="ignore")`
    and write back `.encode("utf-8")`. `read_text()` with no encoding uses
    `locale.getpreferredencoding()`, which on Windows is **cp1252** — so an
    author's UTF-8 BOM (`EF BB BF`) decoded to `ï»¿` and re-encoded to SIX bytes
    (`C3 AF C2 BB C2 BF`) sitting BEFORE the `<?xml` declaration. That is not
    well-formed XML, so FSMP loaded no physics at all for the piece: 8 shipped
    XMLs in the 2026-08-22 pack, two of them cloaks. `errors="ignore"` made it
    worse by SILENTLY DROPPING any byte cp1252 could not decode.

    Decode as UTF-8 and fall back to latin-1, remembering which — the pattern
    `_add_butt_collider_patch`, `_add_skirt_collider_proxy` and
    `_split_bust_collider_xml` already use. The BOM is deliberately NOT stripped:
    round-tripping the author's file unchanged is the whole point, and stripping
    it would be a second unrequested edit to their XML."""
    try:
        raw = Path(xml_path).read_bytes()
    except Exception:
        return None
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1"), "latin-1"
        except Exception:
            return None

def _make_chains_static(xml_path: Path) -> None:
    """Gated (STATIC_CHAINS). Zero every dynamic chain mass in the XML so the
    chain bones become static/kinematic — they hold their bind pose following
    their NIF parent (Pelvis) and the cloth follows the body instead of
    free-falling. Mitigates incomplete rigs (e.g. the wolf's missing front
    chains). Trade-off: no swing. XML-only, idempotent. The per-armor XML's
    only <mass> values belong to chain bones (body collision shapes have none),
    so this never touches body physics."""
    if not _nc().STATIC_CHAINS:
        return
    _rt = _read_xml_roundtrip(xml_path)
    if _rt is None:
        return
    t, _codec = _rt

    def _zero(m):
        try:
            return '<mass>0</mass>' if float(m.group(1)) > 0 else m.group(0)
        except Exception:
            return m.group(0)
    t2 = re.sub(r'<mass>\s*([0-9.]+)\s*</mass>', _zero, t)
    if t2 != t:
        try:
            atomic_write_bytes(xml_path, t2.encode(_codec))
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
    sim -- a custom-race body (head/chest/butt) collapsed to the floor. A
    full-body per-triangle collider paired with cloth that is ALSO skinned +
    weight-pinned to that same body diverges in FSMP. A chest-only KINEMATIC
    sub-mesh collider is the next approach; until proven in-game this stays off.
    Returns True if it patched. #breast-collider"""
    if not _flag("CBBE2UBE_BODY_COLLIDER", False):
        return False
    try:
        from . import hdt_xml_gen
    except Exception:
        return False
    _rt = _read_xml_roundtrip(xml_path)          # #bug-12
    if _rt is None:
        return False
    text, _codec = _rt
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
        # atomic_write_bytes + the SOURCE codec: `write_text()` with no encoding
        # wrote cp1252 on Windows, so a UTF-8 author file came back mangled
        # (#bug-12).
        atomic_write_bytes(
            xml_path,
            text.replace("</system>", block + "</system>", 1).encode(_codec))
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
    _rt = _read_xml_roundtrip(xml_path)          # #bug-12
    if _rt is None:
        return
    text, _codec = _rt
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
    dropped_shapes: "list[tuple[str, str]]" = []   # (kind, name)
    dropped_bones = 0
    for line in text.splitlines():
        m = re.search(r'<per-(?:triangle|vertex)-shape\s+name="([^"]+)"', line)
        if m:
            drop_block = m.group(1) not in nif_shapes
            if drop_block:
                kind = ("cloth" if "per-vertex-shape" in line else "collider")
                dropped_shapes.append((kind, m.group(1)))
        if drop_block:
            changed = True
            if re.search(r'</per-(?:triangle|vertex)-shape>', line):
                drop_block = False
            continue
        if prune_bones:
            wt = re.search(r'<weight-threshold\s+bone="([^"]+)"', line)
            if wt and wt.group(1).lower() not in resolvable_bones:
                changed = True
                dropped_bones += 1
                continue
        out.append(line)

    # SAY WHAT WAS DELETED. This pruning is correct -- FSMP cannot attach to a
    # shape that is not there -- but a dropped `<per-triangle-shape>` is a
    # COLLIDER the cloth no longer bounces off, and until 2026-08-25 it happened
    # in total silence. That silence is how it reached a screenshot: one piece
    # shipped with its skirt passing through a tasset plate and through the
    # pants, because the authored XML named `Tassets`/`Pants` while the mesh we
    # convert calls that shape `Tasset` and has no `Pants` at all.
    #
    # `_note_pass_failure` is the channel deliberately: it counts pack-wide into
    # `conversion_report.json` (which survives a lost run log) AND lands a
    # per-piece line in the per-mod report, so the class is countable instead of
    # discoverable one screenshot at a time.
    #
    # The NEAR-MATCH is reported, not acted on. A name that differs only in case
    # or a trailing plural is almost certainly the same part under a re-export,
    # and separating those from genuinely-absent shapes is what tells anyone
    # whether a remap is worth building. Remapping without that measurement
    # would be attaching physics to a guess.
    if dropped_shapes:
        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower()).rstrip("s")

        parts = []
        for kind, name in dropped_shapes:
            hits = [s for s in nif_shapes if _norm(s) == _norm(name)]
            near = f" [NEAR-MATCH in mesh: {hits[0]!r}]" if len(hits) == 1 else ""
            parts.append(f"{kind} {name!r}{near}")
        _note_pass_failure(
            "hdt_xml_shape_dropped",
            RuntimeError(
                f"{xml_path.name}: {len(dropped_shapes)} shape block(s) pruned "
                f"because the converted NIF has no such shape -- "
                + "; ".join(parts)
                + (f"; plus {dropped_bones} unresolvable weight-threshold "
                   f"bone(s)" if dropped_bones else "")),
            xml_path)
    if changed:
        try:
            atomic_write_bytes(xml_path,
                               ("\n".join(out) + "\n").encode(_codec))
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
        if _nc()._is_inline_body_name(name):
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
        pyn = _nc()._pynifly()
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
        src_xml = None if _nc().CHAIN_TO_SOFTBODY else _read_source_hdt_xml_disk(src_nif_path)
        if src_xml is not None:
            # KNOWN ISSUE (audit 2026-07-28, left OPEN by design): for the
            # narrow class where the phases regenerated the XML because the
            # authored one drives BodySlide-BUILD-injected chain bones, this
            # copy REVERSES that decision. A re-check of
            # _source_hdt_needs_missing_chain_bones here was tried and
            # REVERTED the same day: the helper cannot distinguish truly
            # missing bones from runtime-resolvable ones (a runtime-physics
            # mod's authored XML legitimately drives skeleton bones no mesh
            # NIF carries -- the common case, in-game-proven across four
            # reconverts), so the gate over-fired, declined the authored XML
            # pack-wide, and the harness negative controls caught grafts dead
            # and physics shapes lost. Any future fix must classify the XML's
            # bones against what actually resolves AT RUNTIME, not against
            # the NIF/source bone sets available here.
            pass
        if src_xml is not None:
            # (an authored-XML breast-chain bone remap was an unproven opt-in and was removed -- refuted; see git history. verbatim copy is the long-standing default.)
            try:
                # #hdt-xml-sanitise: the authored file is copied VERBATIM, so a
                # malformed one ships malformed. Repair only damage OUTSIDE the
                # root element, and only when the result parses; otherwise fall
                # through to the byte-for-byte copy this has always done.
                _done = False
                if _nc().HDT_XML_SANITISE:
                    try:
                        _raw = Path(src_xml).read_bytes()
                        _fixed, _note = _hdt_sanitise(_raw)
                        if _note is not None:
                            atomic_write_bytes(str(dst_xml_disk), _fixed)
                            _done = True
                    except Exception:
                        _done = False
                if not _done:
                    atomic_copy(str(src_xml), str(dst_xml_disk))
            except Exception as _e:
                # A failed copy leaves the piece with no physics XML and the
                # caller discards the bool this function returns.
                _note_pass_failure("_finalize_hdt_physics/xml-copy", _e, dst_path)
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
            # SORTED: `col_names` is a set, and this list decides the ORDER the
            # proxies are re-imported in -- which is the order the shapes land
            # in the written NIF. Iterating the set directly made the output
            # byte-order follow the interpreter's hash seed (proven 2026-08-18:
            # ['rear','col','bcol','sash'] swapped positions between seeds).
            missing = sorted(n for n in col_names if n not in present)
            # Defensive (same bug class as the framework path below): never
            # re-import a dropped inline body even if the XML names it as a
            # per-vertex/per-triangle collider -- that re-adds a hidden body =
            # double body = CTD on equip. Cloth collides with the injected UBE
            # BaseShape instead; the stale ref is pruned by _harden_hdt_xml_for_fsmp.
            missing = [n for n in missing if not _nc()._is_inline_body_name(n)]
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
                        _col_cbbe_v, _col_delta = _nc()._cached_cbbe_to_ube_delta(
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
                        new_col = _nc()._copy_shape(src_shape, nf,
                                              override_verts=_col_ov,
                                              preserve_authored_skin=True,
                                              skip_geometry_repair=True)
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
                        # A dropped collision proxy breaks SMP at runtime, and
                        # this print was its only trace. #worker-print-vanishes
                        _note_pass_failure(
                            f"_finalize_hdt_physics/collider:{cn}", _e,
                            dst_path)
        except Exception as _ce:
            # Was `pass`. The inner handler above is carefully voiced (WARN +
            # recorder, `#worker-print-vanishes`), and this outer one threw all
            # of that away: if the LOOP SETUP failed -- reading the XML, listing
            # shapes -- then EVERY collision-proxy re-import was skipped in
            # silence, which breaks SMP at runtime exactly as a per-proxy
            # failure does. A voiced inner handler is worthless inside a mute
            # outer one.
            _note_pass_failure("_finalize_hdt_physics/collider-scan", _ce,
                               dst_path)

        if dirty:
            # Terminal NIF save on the merge/body-swap path: re-assert the
            # VirtualBody Hidden bit (a pynifly round-trip can drop it -> the
            # blue body double). The conform pass only re-hides when it actually
            # conforms verts, so for rigid/flaring/no-jiggle armor THIS is the
            # last save and must restore it itself.
            _nc()._hide_virtual_body(nf)
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
        # Optional: make chains static (CBBE2UBE_STATIC_CHAINS=1).
        # Copy-path armor re-applies this after _reauthor.
        try:
            _make_chains_static(dst_xml_disk)
        except Exception:
            pass
        return True
    except Exception as _fe:
        # One line, always: a silent failure here drops the authored-XML
        # attach, the collider re-import AND the FSMP hardening in one go
        # (dead skirts / skirt collapse / equip-CTD gate skipped), and the
        # caller swallows the return -- "no physics needed" and "physics
        # finalize crashed" printed the same nothing (audit 2026-07-28).
        print(f"  WARN: physics finalize FAILED on {Path(dst_path).name}: "
              f"{_fe!r} -- SMP/XML state for this piece is whatever the "
              f"earlier phases left", file=sys.stderr)
        _note_pass_failure("_finalize_hdt_physics", _fe, dst_path)
        if os.environ.get("CBBE2UBE_DEBUG_FINALIZE"):
            import traceback as _tb
            _tb.print_exc()
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
    if _nc().CHAIN_TO_SOFTBODY:
        return set()  # soft-body mode: nothing is preserved; reskin all cloth
    txt = _read_source_hdt_xml_text(src_nif_path, nif=nif)
    if not txt:
        return _hdt_protect_all_or_none(src_nif_path, nif=nif)
    return set(re.findall(r'<per-vertex-shape\s+name="([^"]+)"', txt))

def _hdt_protect_all_or_none(src_nif_path: Path, nif=None) -> set:
    """The FAIL-CLOSED answer when a piece's physics XML cannot be read.

    A piece that declares no physics XML has no colliders and no soft-bodies,
    and the empty set is the honest answer -- reskin everything.

    A piece that DECLARES one we could not read is the dangerous case, and the
    empty set is the WRONG answer: every protection downstream is a membership
    test, so an empty set silently un-protects every shape at once. When we
    cannot tell WHICH shapes are collision geometry, the safe answer is that ALL
    of them are -- the passes then leave the piece's authored skinning alone,
    which is exactly what the protections would have done for the real collider
    set plus some harmless extra caution on ordinary cloth.

    Returns a REAL set of the NIF's shape names (not a magic always-contains
    object) so callers that iterate it, union it or take its length keep
    working. #hdt-fail-closed"""
    if not _nif_declares_hdt_xml(src_nif_path, nif=nif):
        return set()
    try:
        _nfq = nif if nif is not None else \
            _nc()._pynifly().NifFile(filepath=str(src_nif_path))
        return {s.name for s in _nfq.shapes if s.name}
    except Exception:                                        # pragma: no cover
        return set()

def _hdt_xml_cache_clear() -> None:
    """Drop the per-armor HDT-XML memo. Called at the top of every `convert_nif`."""
    _nc()._HDT_XML_TEXT_CACHE.clear()

def _nif_declares_hdt_xml(src_nif_path: Path, nif=None) -> bool:
    """Does this NIF carry an `HDT Skinned Mesh Physics Object` extra-data
    string? True means the piece CLAIMS physics, so a failure to READ the XML is
    a RESOLUTION FAILURE and not "this piece has no physics" -- the distinction
    the collider/soft-body protections live or die on."""
    try:
        _nfq = nif if nif is not None else \
            _nc()._pynifly().NifFile(filepath=str(src_nif_path))
        for _ed in _nfq.rootNode.extra_data():
            if (getattr(_ed, "name", None) == "HDT Skinned Mesh Physics Object"
                    and getattr(_ed, "string_data", None)):
                return True
    except Exception:
        pass
    return False

def _hdt_xml_bind_piece_source(src_nif_path: Path, nif=None) -> None:
    """Capture this piece's authored physics XML from the SOURCE, once, at the
    start of its conversion. #xml-source-of-truth

    WHY THIS EXISTS (BUG-00, 2026-08-19, confirmed by exact fingerprint
    reproduction). Conversion REWRITES the NIF's physics pointer to the
    output-relative `Meshes\\!UBE\\...\\<stem>.xml`. The later weight passes then
    ask for the collider / soft-body sets via `dst_path`, so they re-resolve
    THAT pointer -- against a file this same run is still writing. When the
    lookup loses that race it returns None, `_hdt_collider_shape_names` and
    `_hdt_softbody_shape_names` both return the EMPTY SET, and because every
    protection is spelled `if s.name in collider_names: continue`, ALL of them
    disengage at once. The jiggle passes then graft breast/butt bones onto the
    registered collision proxies; those bones are not XML-declared, FSMP has no
    rigid body for them, and the cloth free-falls off the actor.

    The SOURCE nif is never written by the converter, so its resolution is
    immutable for the whole conversion -- which is exactly what the memo's own
    docstring already relies on. Binding it here makes every downstream lookup
    independent of the destination's write order.

    Deliberately silent on absence: a piece with no physics XML binds None, and
    every lookup keeps returning an honest empty set.

    `nif` is used ONLY if it is a real pynifly NifFile. `convert_nif` has an
    `nif_io.load_nif` result to hand, which is this project's `Nif` dataclass and
    has no `rootNode.extra_data()` -- passing it made the read throw and bound
    nothing, i.e. the whole recovery was INERT while looking wired up. Caught by
    asserting the bind actually populates, not by reading the code."""
    pass  # (global _PIECE_HDT_XML_TEXT -> _nc().<name>, split)
    _nc()._PIECE_HDT_XML_TEXT = None
    _pyn_nif = nif if hasattr(nif, "rootNode") else None
    try:
        # Memoised call: this also warms the per-armor memo for every later
        # SOURCE-path query, so the extra parse is paid once per piece.
        _nc()._PIECE_HDT_XML_TEXT = _read_source_hdt_xml_text(
            src_nif_path, nif=_pyn_nif)
    except Exception as _e:                                  # pragma: no cover
        _note_pass_failure("_hdt_xml_bind_piece_source", _e, src_nif_path)

def _hdt_sanitise(data: bytes) -> "tuple[bytes, str | None]":
    """Thin import shim for `hdt_xml_gen.sanitise_hdt_xml_bytes` (see
    #hdt-xml-sanitise). Import is local because `hdt_xml_gen` imports this
    module. Never raises: a sanitiser that throws must not take a conversion
    with it, so a failure returns the input unchanged."""
    try:
        from .hdt_xml_gen import sanitise_hdt_xml_bytes
        return sanitise_hdt_xml_bytes(data)
    except Exception:
        return data, None

def _read_source_hdt_xml_text(src_nif_path: Path, nif=None,
                              stem_scan: bool = True) -> "str | None":
    """The armor's authored HDT-SMP XML text, resolved via the NIF's own
    extra-data first, then a keyword match. None on any failure.
    `nif` (optional) reuses an already-loaded NifFile for the extra-data read.

    MEMOISED, because this was the single hottest helper in a conversion: profiled at
    **24.7% of total wall-clock**, 23.5 calls per NIF, 147ms each. The waste is one
    file re-read per SHAPE -- `_precreate_custom_bone_chains` (via `_install_skin` ->
    `_copy_shape`) accounted for 10 of 13 reads of the same source XML on one dress,
    9 of them slow, because the resolution falls through to a glob across every mod.

    A stale answer here would be severe: a missed collider set means a skin pass
    grafts onto an SMP collider, which is the in-game-proven failure (breasts tore off
    and fell through terrain, 2026-07-26). So the memo is bounded TWICE:
      1. keyed on the NIF's own (path, mtime_ns, size) -- any rewrite invalidates, and
         the passes DO rewrite the destination NIF between reads;
      2. cleared at the top of every `convert_nif`, so nothing can cross armours.
    The expensive reads are all against the SOURCE NIF, which the converter never
    writes, so those are immutable for the whole conversion by construction."""
    _key = None
    try:
        _st = os.stat(src_nif_path)
        # `stem_scan` IS PART OF THE KEY. Two callers can ask about the
        # same path and mean different questions, and a memo that
        # conflated them would hand a destination caller the very
        # answer this parameter exists to refuse.
        _key = (str(src_nif_path), _st.st_mtime_ns, _st.st_size, stem_scan)
        if _key in _nc()._HDT_XML_TEXT_CACHE:
            return _nc()._HDT_XML_TEXT_CACHE[_key]
    except OSError:
        _key = None                      # unstatable -> never cache, always re-read
    _out = _read_source_hdt_xml_text_uncached(src_nif_path, nif=nif,
                                              stem_scan=stem_scan)
    if _key is not None:
        _nc()._HDT_XML_TEXT_CACHE[_key] = _out
    return _out

def _read_source_hdt_xml_text_uncached(src_nif_path: Path, nif=None,
                                       stem_scan: bool = True
                                       ) -> "str | None":
    """The real resolution. Split out so the memo above stays trivially auditable.

    `stem_scan=False` REFUSES the filename fallback.  #hdt-xml-race

    That fallback globs the mod tree the path lives in and, when exactly
    one same-stem XML exists, takes it from ANY directory. Against a
    SOURCE mod that is safe and useful -- the tree is static and the match
    catches authored configs the keyword map misses. Against the
    DESTINATION it is a race: the run is still writing XMLs into that
    tree, `_mod_xml_index` memoises whatever each worker saw first, and
    the same-stem count for one piece climbs from 0 to 6 during a
    conversion. Measured 2026-09-09: two arms of identical code differed
    on two NIFs because one run caught the window where the count was 1
    and resolved an unrelated garment's physics; 207 NIFs (104 garments,
    5.6% of the pack) sit in that class.

    Callers holding a DESTINATION path pass False. The destination's own
    extra-data pointer is still read first and is unaffected -- that is
    the legitimate destination route, and it is deterministic.
    """
    try:
        xml_disk = _read_source_hdt_xml_disk(src_nif_path, nif=nif)
        if xml_disk is None and stem_scan:
            rel = _find_hdt_xml_for_armor(src_nif_path)
            if rel:
                norm = _nc()._safe_data_rel(rel)
                if norm is None:
                    return None          # this function returns str | None
                for parent in [src_nif_path, *src_nif_path.parents]:
                    if parent.name.lower() == "meshes":
                        cand = parent.parent / norm
                        if cand.is_file():
                            xml_disk = cand
                        break
        if xml_disk is None or not xml_disk.is_file():
            # If the NIF itself DECLARES a physics link, "no XML text" is a
            # RESOLUTION FAILURE, not "this piece has no physics" -- and every
            # collider/softbody skip downstream silently disengages on the
            # empty set (audit 2026-07-28: grafting onto colliders is the
            # in-game-proven tear-off class). Distinguish the two loudly.
            if not _nif_declares_hdt_xml(src_nif_path, nif=nif):
                return None          # honestly physics-free: empty set is right
            # #xml-source-of-truth: recover from the copy bound at the top of
            # this conversion, off the SOURCE nif, which no write can race.
            # This is the whole reason that binding exists -- the destination
            # pointer resolving is a function of WRITE ORDER, and losing that
            # race used to fail OPEN (BUG-00).
            if _nc()._PIECE_HDT_XML_TEXT:
                return _nc()._PIECE_HDT_XML_TEXT
            # Nothing left to fall back to. Say so where a real run can SEE it:
            # this warning used to go to a pool worker's stderr, which the
            # frozen exe discards ([[feedback_worker_prints_invisible]]), so the
            # one condition that silently disarms every physics protection was
            # unobservable in the runs that mattered.
            _note_pass_failure(
                "hdt_xml_unresolved", RuntimeError(
                    f"{Path(src_nif_path).name} declares an HDT physics XML "
                    f"that did not resolve; collider/soft-body protections "
                    f"FAIL CLOSED for this piece"), src_nif_path)
            return None
        if not _nc().HDT_XML_SANITISE:
            return xml_disk.read_text(errors="ignore")
        # #hdt-xml-sanitise. The OFF path and the nothing-to-repair path both
        # return the ORIGINAL `read_text` result, so this is bit-identical to
        # the previous behaviour except on a file that does not parse at all.
        _raw = xml_disk.read_bytes()
        _fixed, _note = _hdt_sanitise(_raw)
        if _note is None:
            return xml_disk.read_text(errors="ignore")
        _note_pass_failure(
            "hdt_xml_sanitised", RuntimeError(
                f"{Path(xml_disk).name}: repaired malformed authored XML -- "
                f"{_note}"), src_nif_path)
        import locale as _locale
        return _fixed.decode(_locale.getpreferredencoding(False),
                             errors="ignore")
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
        return _hdt_protect_all_or_none(src_nif_path, nif=nif)
    return set(re.findall(r'<per-triangle-shape\s+name="([^"]+)"', txt))
