"""The NIF writer and its per-shape repairs: shape copy with shader / effect-controller transplant, skin installation, the fresh re-author, partition normalisation, authored shape-order restore, coherence / degenerate-normal / winding repairs, vertex-colour shader flags, shape classification, z-fight detection and the output validator.

Split out of nif_convert.py on 2026-09-01 (split step 10). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import os
import sys

from . import fit_metrics, nif_io, nif_patch
from .atomic_io import (
    atomic_nif_save, atomic_copy, atomic_write_bytes, atomic_tri_save)
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
from .nif_convert_physics import (  # noqa: E402
    _ColliderDeclined, _actor_can_resolve_bone, _actor_skeleton_bone_names,
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
    except Exception as _pe:
        _note_pass_failure("_precreate_custom_bone_chains", _pe)
    # Fix scale-bone STB space mismatch: bake g2s^-1 into scale-bone STBs.
    # (Runs on the pre-strip weights_map, as before -- it only mutates xforms_map.)
    if bake_T is None and src_shape.has_global_to_skin:
        xforms_map, _ = _align_scale_bone_stbs_to_verts(
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
            force=_nc()._nif_has_garment_chain(src_shape.file))
    weights_map = _fill_zero_weight_verts(weights_map, use_verts)
    # #skin-influence-cap: cap BEFORE `surviving` is computed, so a bone the cap
    # empties is never add_bone'd. Deriving the add list from pre-cap weights is
    # precisely how a zero-weight bone reaches disk. Authored SMP skins are exempt --
    # their zero-weight XML constraint bones are deliberate and load-bearing.
    if _nc().SKIN_INFLUENCE_CAP_ENABLED and not preserve_authored_skin:
        try:
            weights_map = _cap_weights_map(weights_map, len(use_verts))
        except Exception as _e:
            # Still fail-soft -- never fail the conversion over a weight-hygiene
            # pass -- but RECORDED. A silent swallow cannot be told apart from
            # "nothing qualified", which is exactly how a pass that throws on
            # every piece reads as a design that does nothing.
            _note_pass_failure("_cap_weights_map", _e)
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
        pyn = _nc()._pynifly()
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
            # A shape WITH a bone list but NO weights at all is total skin
            # loss, and the old `continue` here made the spike check below
            # vacuously unable to fire in the worst case it exists for
            # (audit 2026-07-28: could-not-measure read as measured-fine).
            warnings.append(
                f"{name} :: {s.name}: has {len(bones)} bones but ZERO "
                f"weights -- skin lost entirely (renders at bone origin)"
            )
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
            if (s.textures or {}) and s.name not in _nc().UBE_BODY_INJECT_NAMES
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
                src_nif = _nc()._pynifly().NifFile(filepath=str(src_path))
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
    except Exception as _e:
        # Say the check did not run, or an empty list reads as "clean".
        warnings.append(f"{name} :: validator failed ({_e!r}) -- "
                        "z-fight check NOT run")
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
            # LINK absence (audit 2026-07-28): a TRI on disk with no BODYTRI
            # extra-data record in the NIF is the "ignores morphs, body
            # reverts to its _0 shape" class -- and the old check was
            # presence-conditional, so the lost-link case validated clean.
            # BODYTRI lives on its carrier SHAPE, not the root.
            # Enumerate by index over `extraDataCount`, never `extra_data()`:
            # that generator stops at the first block it cannot build, and a
            # BodySlide body carries a NiIntegersExtraData `LOCKEDNORM` at
            # index 0 -- so a NIF whose only carrier is the body read as
            # having no BODYTRI at all, which is exactly the case this check
            # exists to catch.
            _has_bodytri = False
            for s in nf.shapes:
                try:
                    _n = int(getattr(s.properties, "extraDataCount", 0) or 0)
                except Exception:
                    _n = 0
                for _i in range(_n):
                    try:
                        _ed = s.get_extra_data(target_index=_i)
                    except Exception:
                        continue
                    if _ed is not None and getattr(_ed, "name", None) == "BODYTRI":
                        _has_bodytri = True
                        break
                if _has_bodytri:
                    break
            if not _has_bodytri:
                warnings.append(
                    f"{name} :: TRI written but NO shape carries a BODYTRI "
                    f"extra-data record (morphs will not apply -- body "
                    f"reverts to _0 shape when equipped)")
        except Exception as e:
            warnings.append(f"{name} :: TRI validation failed: {e!r}")

    # HDT-SMP XML cross-check: verify the referenced XML exists, parses,
    # and only references bones in the NIF skeleton.
    #
    # ABSENCE check first (audit 2026-07-28): the block below only ran when
    # the link SURVIVED -- but the documented failure mode is a rebuild
    # DROPPING extra data, exactly when hdt_xml_rel is None and the validator
    # used to report clean. If the SOURCE carried a physics link and the dst
    # does not, that is the dead-SMP class, not a clean file. Same for a
    # source-side BODYTRI extra-data record lost from the dst.
    try:
        hdt_xml_rel: str | None = None
        for ed in nf.rootNode.extra_data():
            if (hasattr(ed, "string_data")
                    and ed.name == "HDT Skinned Mesh Physics Object"):
                hdt_xml_rel = ed.string_data
                break
        if hdt_xml_rel is None and src_path is not None:
            try:
                snf = pyn.NifFile(filepath=str(src_path))
                for ed in snf.rootNode.extra_data():
                    if (hasattr(ed, "string_data")
                            and ed.name == "HDT Skinned Mesh Physics Object"):
                        warnings.append(
                            f"{name} :: source declares an HDT physics link "
                            f"but the converted NIF has NONE (SMP dead -- "
                            f"extra-data dropped by a rebuild?)")
                        break
            except Exception:
                pass
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
                # ...AND THE NODE TREE. FSMP resolves a physics bone by
                # NODE NAME, and a chain's ROOT link legitimately carries
                # no skin weights -- so a skin-bone-only set reports every
                # healthy chain anchor as unresolvable. Measured on the
                # shipped pack: 4449 'in NEITHER the NIF nor the actor
                # skeleton' warnings over 122 pieces, of which **1839 (41%)
                # on 33 pieces are this false positive** -- e.g. an
                # SMP cloth set's `<Tome>_R 0` chain node, which IS a
                # node in the NIF and in the author's source alike. The validator's own comment says
                # it was narrowed so 'the SIX that actually mattered' stayed
                # visible; drowning them in 1839 spurious lines defeats that.
                try:
                    all_nif_bones |= set(nf.nodes.keys())
                except Exception:
                    pass
                xml_warnings = validate_armor_hdt_xml(xml_disk, all_nif_bones)
                for w in xml_warnings:
                    warnings.append(f"{name} :: {w}")
    except Exception as e:
        warnings.append(f"{name} :: HDT XML validation failed: {e!r}")

    return warnings

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
        if _nc()._looks_like_inline_body(s):
            body.append(s.name)
        else:
            armor.append(s.name)
    return body, armor

def _restore_authored_shape_order(dst_path, src_nif_path) -> int:
    """Re-emit the NIF with its AUTHORED shapes back in the AUTHOR's order, and
    anything we added after them. Returns how many shapes changed index.

    WHY THIS IS CORRECTNESS AND NOT TIDINESS. An ARMA `AlternateTextures` entry
    selects the shape it re-textures by INDEX, not by the 3D name it also
    stores. So permuting a converted mesh's shapes silently re-points every
    colour-variant swap in the load order that names that mesh -- including
    swaps in THIRD-PARTY patches we do not own and cannot edit. Confirmed in
    game (BUG-09): a white top loaded the default fabric texture while the
    actor's SKIN texture broke, because the swap bound to index 0 and our
    injected `BaseShape` was sitting there instead of the garment.

    TWO ROUTES PERMUTED THE SHAPES, and this pass only has to repair the second:

      1. the UBE body was injected BEFORE pass 2, taking index 0 and shifting
         every authored shape by +1. Fixed structurally by moving the injection
         after pass 2 -- no repair needed, so nothing here depends on it;
      2. `_finalize_hdt_physics` DROPS textureless collision proxies during the
         copy and re-appends them `sorted()` at the END. That is this pass's
         job: they are authored shapes and belong at their authored indices.
         Measured on a college robe -- author `robes, sash, bcol, rear, col,
         body` shipped as `robes, body, bcol, col, rear, sash`, i.e. the two
         textured shapes followed by the four dropped proxies in sorted order.

    Shapes the SOURCE does not have (the injected body, generated colliders like
    ButtCol/SkirtCol) keep their relative order and follow the authored ones --
    they have no authored index to preserve, and putting them last is what the
    author's own mesh and every hand-made UBE conversion already do.

    A NO-OP WHEN THE ORDER ALREADY MATCHES, and that is load-bearing: the rebuild
    is only paid by pieces that were actually wrong. Gated by
    AUTHORED_SHAPE_ORDER (`CBBE2UBE_NO_AUTHORED_SHAPE_ORDER=1` to disable).
    """
    if not _nc().AUTHORED_SHAPE_ORDER:
        return 0
    pyn = _nc()._pynifly()
    dn = pyn.NifFile(filepath=str(dst_path))
    dst_names = [s.name for s in dn.shapes]
    if len(dst_names) < 2:
        return 0                      # nothing to permute
    try:
        sn = pyn.NifFile(filepath=str(src_nif_path))
        src_names = [s.name for s in sn.shapes]
    except Exception:
        return 0                      # no source to be faithful TO -> leave it
    if not src_names:
        return 0
    src_set, dst_set = set(src_names), set(dst_names)
    authored = [n for n in src_names if n in dst_set]
    extras = [n for n in dst_names if n not in src_set]
    desired = authored + extras
    if desired == dst_names:
        return 0                      # already faithful -- do NOT rebuild
    moved = sum(1 for a, b in zip(desired, dst_names) if a != b)
    # Hand over the ALREADY-OPEN NifFile: `_reauthor_nif_fresh` preserves the
    # BODYTRI and root HDT extra-data, the Hidden bits and the authored skin of
    # colliders/soft-bodies/layered cloth, and re-seeds the flat chain anchors.
    if not _reauthor_nif_fresh(dst_path, nif=dn, shape_order=desired):
        _note_pass_failure(
            "_restore_authored_shape_order",
            RuntimeError(f"re-author declined; {dst_path.name} ships with "
                         f"{moved} shape(s) at the wrong index"))
        return 0
    import sys as _sys
    print(f"  #authored-shape-order: {dst_path.name} restored {moved} shape(s) "
          f"to the author's order", file=_sys.stderr)
    return moved

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
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
        # HDT-SMP per-triangle COLLISION shapes (the authored cloth/skirt
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
            if s.name in _nc().UBE_BODY_INJECT_NAMES or s.name == "VirtualGround":
                continue
            # SMP COLLIDER: keep its authored partitions exactly (see above).
            if s.name in collider_names:
                continue
            # An over-cap ACCESSORY (gauntlet=33, boot=37, helmet=30/31, ...) must
            # keep its dismember slot across the split or it goes invisible in its
            # equip region; body-region shapes -> None -> SBP_32_BODY.
            keep_slot = _nc()._preserved_dismember_slot(s)
            # Over-cap shape: SPLIT into <=cap-bone partitions (keeps every bone,
            # CTD-safe) instead of collapsing to one over-budget partition. Dense
            # dresses/robes that ship 79-81 bones (some armor mods) keep their full
            # morph + physics rig this way. Under-cap shapes collapse as before.
            if len(getattr(s, "bone_names", []) or []) > SKIN_PARTITION_BONE_CAP:
                # Respect BOTH caps: a dense rig that is also high-vert must not
                # leave a bone-split partition still over the vertex cap.
                if _nc()._split_oversize_partition(
                        s, vert_cap=_nc().SKIN_PARTITION_VERT_CAP,
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
            if len(getattr(s, "verts", []) or []) > _nc().SKIN_PARTITION_VERT_CAP:
                if _nc()._split_oversize_partition_verts(s, part_id=keep_slot) > 1:
                    changed += 1
                else:
                    # Split failed (no source partition / pynifly error): the
                    # shape keeps one huge partition -> morph-rebuild equip-CTD
                    # risk. Surface it loudly, mirroring the bone-cap branch.
                    import sys as _sys
                    print(f"  WARNING: {s.name!r} has "
                          f"{len(s.verts)} verts (> {_nc().SKIN_PARTITION_VERT_CAP}"
                          f"-vert morph-rebuild cap) and could NOT be split into "
                          f"partitions -> may CTD on equip in {dst_path.name}",
                          file=_sys.stderr)
                continue
            if _nc()._normalize_partitions(s):
                changed += 1
        if changed:
            # Re-assert the VirtualBody Hidden bit: a pynifly re-save can drop it
            # (-> blue body double), and on the merge path this can be a terminal
            # save. Mirrors the conform pass + _finalize_hdt_physics.
            _nc()._hide_virtual_body(nf)
            atomic_nif_save(nf, dst_path)
        return changed
    except Exception as _pe:
        # 0 is also the legit "nothing needed collapsing" answer, and the loud
        # over-cap WARNINGs above live INSIDE this try -- an early failure
        # skips them too, so an over-cap shape would ship silently (equip-CTD
        # class; audit 2026-07-28). Say the pass died.
        print(f"  WARN: partition normalize FAILED on {Path(dst_path).name}: "
              f"{_pe!r} -- partitions left as-is, over-cap checks NOT run",
              file=sys.stderr)
        return 0

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
            return bool(int(props.vertexDesc) & _nc().SLSF2_VERTEX_COLORS)
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
            if sf2 & _nc().SLSF2_VERTEX_COLORS:
                props.Shader_Flags_2 = sf2 & ~_nc().SLSF2_VERTEX_COLORS
                changed = True
            sf1 = int(getattr(props, "Shader_Flags_1", 0))
            if sf1 & _nc().SLSF1_VERTEX_ALPHA:
                props.Shader_Flags_1 = sf1 & ~_nc().SLSF1_VERTEX_ALPHA
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
        none_id = _nc()._pynifly().NODEID_NONE
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
    except Exception as _pe:
        _note_pass_failure("_repair_effect_shader_shape_controllers", _pe)
    if n:
        try:
            atomic_nif_save(nif._backing, path_str)
            return n
        except Exception as _se:
            # `return 0` is this worker's word for "nothing needed sanitising",
            # so a swallowed save reports a clean piece that was never written.
            _note_pass_failure("_sanitize_one_nif_worker/save", _se, path_str)
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
            except Exception as _pe:
                _note_pass_failure("_repair_effect_shader_shape_controllers", _pe)
            if n:
                try:
                    atomic_nif_save(nif._backing, ps)
                    fc += 1
                    sf += n
                except Exception as _pe:
                    _note_pass_failure("atomic_nif_save", _pe)
        return fc, sf

    # Serial for small batches / single worker — pool spawn (esp. frozen-exe
    # spawn re-import of pynifly per worker) costs more than it saves there.
    if workers <= 1 or files < 64:
        files_changed, shapes_fixed = _run_serial()
        return {"files": files, "files_changed": files_changed,
                "shapes_fixed": shapes_fixed}

    files_changed = shapes_fixed = 0
    pool_error = None
    # Imported BEFORE the try: the except clause below names BrokenProcessPool,
    # and a name bound inside the try is not available to its own handler.
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for n in ex.map(_sanitize_one_nif_worker, files_list, chunksize=16):
                if n:
                    files_changed += 1
                    shapes_fixed += n
    except (BrokenProcessPool, MemoryError) as _me:
        # A WORKER WAS KILLED, and this is the likeliest moment in the whole run
        # for it: the sweep fires at the very end, spawning a fresh pool that
        # re-imports numpy in every process while the machine is at its most
        # fragmented. It used to fall into the bare handler below, which
        # restarts serially and returns a clean stats dict -- so an
        # out-of-memory death here reported SUCCESS, printed nothing, and left
        # no entry in the failures file. The serial fallback is still the right
        # recovery; what was missing was saying it happened. #commit-headroom
        pool_error = (f"{type(_me).__name__}: a worker died during the "
                      f"vertex-colour sweep (most often out of memory). Redone "
                      f"serially, so the output is correct, but if this run was "
                      f"slow or unstable, lower \"Worker processes\" on the "
                      f"Run tab.")
        files_changed, shapes_fixed = _run_serial()
    except Exception as _pe:
        # Any other pool failure (spawn issue, etc.) -> safe serial fallback.
        pool_error = f"{type(_pe).__name__}: {_pe}"
        files_changed, shapes_fixed = _run_serial()
    return {"files": files, "files_changed": files_changed,
            "shapes_fixed": shapes_fixed, "pool_error": pool_error}

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
    face_area = np.linalg.norm(face_normals, axis=1)
    fan = np.zeros(len(verts))
    area_sum = np.zeros(len(verts))
    for i in range(3):
        np.add.at(vert_normals, tris[:, i], face_normals)
        np.add.at(area_sum, tris[:, i], face_area)
        np.add.at(fan, tris[:, i], 1)
    vn_len = np.linalg.norm(vert_normals, axis=1, keepdims=True)
    vn_len[vn_len < 1e-9] = 1.0
    out = vert_normals / vn_len

    if source_normals is not None:
        src = np.asarray(source_normals, dtype=np.float64)
        if src.shape == out.shape:
            # Per-vert sign alignment: if recomputed disagrees with
            # source by >90 degrees, flip it. Applied ONLY where the
            # recompute is undetermined -- see the block above the
            # function for why the blanket form was damage.
            dot = (out * src).sum(axis=1, keepdims=True)
            if _nc().NORMAL_SIGN_GUARD_BOUNDARY:
                # At a topology BOUNDARY (an edge in exactly one triangle) the
                # area-weighted recompute is unreliable: a double-sided thin
                # strap's incident faces cancel to a sideways normal, and merely
                # FLIPPING that garbage toward the source relocates it rather than
                # fixing it (measured: 9 belt-rim verts backwards vs their own
                # geometry, while the AUTHORED source normals there are correct at
                # 0.99). So at a boundary vert take the authored source normal
                # outright, and trust the recompute in the INTERIOR where it is
                # reliable -- which also stops a sharp manifold STUD (low
                # coherence, correct recompute) being flipped to a stale source,
                # the backwards-normal "shard" class.
                _ed = np.sort(np.concatenate(
                    [tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [0, 2]]]),
                    axis=1)
                _ue, _uc = np.unique(_ed, axis=0, return_counts=True)
                _bnd = np.zeros(len(verts), bool)
                _bnd[_ue[_uc == 1].ravel()] = True
                _sl = np.linalg.norm(src, axis=1, keepdims=True)
                _use = _bnd[:, None] & (_sl > 1e-6)
                out = np.where(_use, src / np.maximum(_sl, 1e-9), out)
            elif _nc().NORMAL_SIGN_GUARD_DETERMINED:
                # coherence: do the incident faces point the same way?
                # |sum(area * n)| / sum(area) -- 1.0 flat, 0.0 cancelling.
                # Both terms carry the same 2x from the raw cross product,
                # so it cancels and no factor belongs here.
                coh = np.linalg.norm(vert_normals, axis=1) / np.maximum(
                    area_sum, 1e-12)
                determined = ((fan >= _nc().NORMAL_DETERMINED_FAN_MIN)
                              & (coh >= _nc().NORMAL_DETERMINED_COHERENCE_MIN))
                flip_mask = (dot < 0) & ~determined[:, None]
                out = np.where(flip_mask, -out, out)
            else:
                out = np.where(dot < 0, -out, out)
    return out

def _geometry_repair_allowed(shape, skip_geometry_repair=False) -> bool:
    """May the rendering repairs touch this shape?

    NO for HDT-SMP COLLIDERS and collision proxies. Those are never rendered, so
    a repair aimed at RENDERING gains nothing on them -- while FSMP reads their
    triangle orientation and vertex positions for collision, and this project
    has a long history of equip CTDs from exactly that kind of well-meant edit
    (see #smp-collider-skin-preserve: 'self-contained and internally
    consistent').

    YES for soft-body and layered cloth. They are SIMULATED but they are also
    DRAWN, so a split seam or an inverted triangle shows on them like anything
    else.

    This deliberately does NOT key off `preserve_authored_skin`. That flag means
    "keep this shape's authored SKIN", and the re-author path sets it for
    colliders, soft-body AND layered cloth alike -- three classes, only one of
    which should be excluded here. Borrowing it blocked the repair on ordinary
    rendered garments: measured on a two-shape cuirass where the re-author handed
    ONE of the two shapes fresh unwelded verts with the repair disabled,
    overwriting the welded geometry, while its sibling got no override and stayed
    clean. Same NIF, opposite outcomes, from one overloaded flag.
    """
    if skip_geometry_repair:
        return False
    name = str(getattr(shape, "name", "") or "")
    low = name.lower()
    # "Virtual*" is the physics-helper naming convention -- VirtualBody (the
    # HDT collision proxy) and VirtualGround (a simulation plane). Neither is
    # rendered. VirtualGround came through a real conversion unmodified only
    # because its winding happened to already agree; that is luck, not a rule.
    if low.startswith("virtual"):
        return False
    # ENDSWITH, not `in`. The suffix is the two-letter "Col", and a substring
    # test excludes any shape whose name merely contains it -- the pack has a
    # rendered shape whose name merely ENDS in those letters (a collar, a
    # protocol part), which would then silently lose the repair.
    if low.endswith(_nc()._BUST_SPLIT_COL_SUFFIX.lower()):
        return False
    return True

# A REPAIR PASS MAY NOT PUT A GARMENT VERTEX INSIDE THE BODY.
# #coherence-repair-outside-body
#
# `_repair_coherence_collapse` runs LAST, after anti-poke, panel-rigidity,
# chain-blend, min-push and the seam weld -- and it takes no body at all. It
# smooths the DISPLACEMENT field across a buckled patch and restores the patch's
# MEAN displacement, which keeps the garment where the fit put it ON AVERAGE but
# redistributes it per vertex. A vertex the anti-poke pushed clear on purpose is
# just a large outward displacement to this pass, and averaging it against
# quieter neighbours pulls it back in. Nothing runs after it to catch that.
#
# Traced with CBBE2UBE_STAGE_DUMP on the one piece carrying every bust-band
# penetration the authored floors added (signed standoff along the body normal):
#
#     stage                    Torso v1615        Shell 4 v121
#     s07_antipoke                 +0.9555             +0.3061
#     ... panel_rigidity_post, chain_blend, min_push, seam_weld: unchanged ...
#     s12_coherence_repair         -0.0214             -1.0018
#
# The pass moves those verts 0.98u and 1.31u INWARD, straight through the skin.
# It does the same in the control (+1.0652 -> +0.2003 on that Torso vert): the
# authored floor does not create this, it spends the margin that was absorbing
# it. So the floors are not the defect -- this is, and it was there all along.
#
# The guard is one-sided and monotone: a vertex may not be moved from outside the
# body to inside, and one already inside may not be driven deeper. Everything
# else the repair asks for is granted, so the un-buckling it exists for is
# untouched wherever it does not cost clearance. Same contract as the anti-poke
# feather's own "never reopen a poke" floor.
#
# WITHOUT A BODY IT IS EXACTLY TODAY'S BEHAVIOUR. `_copy_shape`'s call site has
# no body in scope, so that one is unguarded and stays a known gap rather than a
# silent half-fix -- see the note at that call.
#
# The flag itself lives in nif_convert.py with every other one and is read
# through `_nc()`, so `importlib.reload(nc)` keeps reaching it.


def _hold_repair_outside_body(before, after, body_verts, body_normals):
    """Clamp a repair so it cannot reduce a vertex's clearance past the skin.

    `before`/`after` are the pass's own input and output. Only vertices the
    repair actually MOVED are tested, so a shape it did not touch costs one
    array comparison and no tree query.

    Returns `after` unchanged when disabled, when no body was supplied, or on
    any failure -- never worse than not running.
    """
    if not _nc().COHERENCE_REPAIR_OUTSIDE_BODY:
        return after
    if body_verts is None or body_normals is None:
        return after
    try:
        B = np.asarray(body_verts, np.float64)
        BN = np.asarray(body_normals, np.float64)
        if B.ndim != 2 or B.shape != BN.shape or not len(B):
            return after
        b4 = np.asarray(before, np.float64)
        af = np.asarray(after, np.float64)
        if b4.shape != af.shape:
            return after
        moved = np.where(np.any(np.abs(af - b4) > 1e-6, axis=1))[0]
        if not len(moved):
            return after
        BN = BN / np.clip(np.linalg.norm(BN, axis=1, keepdims=True), 1e-9, None)
        from scipy.spatial import cKDTree
        tree = cKDTree(B)
        # Each vertex against the SAME body vertex before and after, so this
        # measures what the repair did rather than a change of reference.
        _d, j = tree.query(b4[moved], k=1)
        n_at = BN[j]
        s0 = np.einsum('ij,ij->i', b4[moved] - B[j], n_at)
        s1 = np.einsum('ij,ij->i', af[moved] - B[j], n_at)
        floor = np.minimum(s0, 0.0)          # never worse, and never past zero
        short = np.clip(floor - s1, 0.0, None)
        if not np.any(short > 0):
            return after
        out = af.copy()
        out[moved] = af[moved] + n_at * short[:, None]
        return out
    except Exception as _e:
        _note_pass_failure("_hold_repair_outside_body", _e)
        return after


def _repair_coherence_collapse(src_verts, out_verts, tris, *,
                               body_verts=None, body_normals=None):
    """Un-buckle thin features whose surface normals went from COHERENT to
    SCATTERED during the fit. Returns (verts, n_patches_repaired).

    `body_verts`/`body_normals` are optional and arm `#coherence-repair-outside
    -body`, which stops the repair pulling a vertex through the skin. Omitted,
    this behaves exactly as it did.

    IN-GAME VALIDATED on two independent pieces (a cuirass hem rim seen as "a
    bent piece at the end, bent at a 90 degree angle", and a trousers rear seam
    seen as "a noticeable crease up the rear" -- the second predicted from this
    metric before the user looked).

    THE METRIC. Per-triangle angle between source and output face normal;
    cluster the turned triangles into connected patches; a patch is BROKEN when
    its area-weighted |mean normal| falls from >= COHERENCE_SRC_MIN to
    <= COHERENCE_OUT_MAX. A legitimate refit turns every normal in a patch
    TOGETHER and keeps |mean| long -- only a crumple scatters them. Judging by
    ROTATION alone instead flags 53% of the pack; judging per-triangle AREA
    instead misses the defect entirely (it is many small triangles summing to a
    visible patch). See [DESIGN: thin-rim crumple].

    THE REPAIR. Smooth the DISPLACEMENT FIELD (out - src) across the patch,
    holding its boundary fixed, then re-apply. Because the patch mean
    displacement is preserved, the garment stays exactly where the fit put it;
    only the vert-to-vert differential that buckles a thin feature is removed.
    That is deliberately NOT damping the warp -- damping the warp globally is
    what once left every garment CBBE-shaped.
    """
    if not _nc().COHERENCE_REPAIR:
        return out_verts, 0
    from collections import defaultdict
    try:
        sv = np.asarray(src_verts, dtype=np.float64)
        ov = np.asarray(out_verts, dtype=np.float64)
        t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        if len(sv) != len(ov) or len(t) == 0:
            return out_verts, 0

        def _nrm(v):
            n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
            a = np.linalg.norm(n, axis=1)
            return n / np.maximum(a[:, None], 1e-12), 0.5 * a

        ns, as_ = _nrm(sv)
        no, ao = _nrm(ov)
        rot = (ns * no).sum(axis=1)
        # The test is done on the COSINE directly; a `rot_deg` conversion used
        # to be computed here and never read -- an arccos over every triangle of
        # every shape for nothing. Removed 2026-09-06.
        turned = np.where(rot < 0.866)[0]          # cos 30deg -- > 30 degrees
        if len(turned) == 0:
            return out_verts, 0

        # connected components over shared edges, among turned triangles only
        e2t = {}
        for ti in turned:
            a, b, c = t[ti]
            for e in ((a, b), (b, c), (a, c)):
                e2t.setdefault((min(e), max(e)), []).append(ti)
        seen = set()
        disp = ov - sv
        repaired = 0
        _adj_full = None          # built lazily, only if a patch qualifies
        _v2t = None               # vert -> triangles, for the kink test
        for ti in turned:
            if ti in seen:
                continue
            stack, comp = [ti], []
            seen.add(ti)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                a, b, c = t[cur]
                for e in ((a, b), (b, c), (a, c)):
                    for nb in e2t.get((min(e), max(e)), ()):
                        if nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
            # THINNESS FIRST, so the area floor can be scaled by it.
            core_early = np.unique(t[comp])
            _p = sv[core_early]
            thin_extent = float((_p.max(axis=0) - _p.min(axis=0)).min())
            # A THIN STRAP'S FEATURES ARE ALL SMALL-AREA BY CONSTRUCTION, so an
            # absolute floor measures the wrong thing there. #coherence-thin-area
            #
            # REPORTED IN GAME as belts "still corrupted". Measured on that
            # piece: `belts` carries 82 boundary verts whose surface has FOLDED
            # back on itself -- stored and triangle-implied normals agree in the
            # source (0 flipped) and disagree by >90 degrees after conversion, so
            # the triangles turned over. Thin-rim buckling, exactly what this
            # pass repairs. The log shows it un-buckling `chest_plate` and `top`
            # repeatedly and `belts` NEVER: a belt strap's folded sliver is well
            # under COHERENCE_MIN_AREA (4.0), so the floor meant to reject noise
            # rejected the thinnest rim on the piece -- the case the pass exists
            # for.
            #
            # Scaled ONLY for patches already judged thin by the pass's own
            # `COHERENCE_THIN` criterion, so ordinary patches keep the tuned 4.0
            # and the pass's reach on everything else is unchanged.
            _area_floor = _nc().COHERENCE_MIN_AREA
            if thin_extent < _nc().COHERENCE_THIN:
                _area_floor *= _nc().COHERENCE_THIN_AREA_SCALE
            if float(ao[comp].sum()) < _area_floor:
                continue
            ws = as_[comp][:, None]
            wo = ao[comp][:, None]
            cs = float(np.linalg.norm(
                (ns[comp] * ws).sum(0) / max(float(ws.sum()), 1e-9)))
            co = float(np.linalg.norm(
                (no[comp] * wo).sum(0) / max(float(wo.sum()), 1e-9)))
            ok = (cs >= _nc().COHERENCE_SRC_MIN and co <= _nc().COHERENCE_OUT_MAX)
            # KINK: a patch that turns far MORE than the surface it is attached
            # to. It rotates RIGIDLY, so coherence is preserved and both gates
            # below miss it -- yet a strip that turns 3-5x harder than its
            # neighbours is exactly the "tip of the cloth is bent" the user sees.
            # Measured on one piece's hip flap: patch 48.6 deg vs neighbours
            # 10.3 deg. Must be SMOOTHED, never made rigid: rigid preserves the
            # kink by construction. #coherence-kink
            kink = False
            if not ok and _nc().COHERENCE_KINK:
                # IMPLEMENTED 2026-09-04. The comment above specified this test
                # and named its worked example, but `kink` was assigned False
                # and never set True anywhere -- the branch was dead, so a patch
                # that turns COHERENTLY fell through every gate.
                #
                # Measured on a reported robe's shoulder straps: 16 damaged
                # patches turning 68-113 deg while the surface they attach to
                # turns 26-48 -- coherence 0.96 -> 0.90, i.e. a DROP of 0.05,
                # far under COHERENCE_THIN_DROP (0.30), and an output coherence
                # of 0.90 nowhere near COHERENCE_OUT_MAX (0.30). Nothing else
                # here can see that shape of damage.
                if _v2t is None:
                    _v2t = defaultdict(list)
                    for _i, (_a, _b, _c) in enumerate(t):
                        _v2t[int(_a)].append(_i)
                        _v2t[int(_b)].append(_i)
                        _v2t[int(_c)].append(_i)
                try:
                    _turn = np.degrees(np.arccos(np.clip(
                        np.einsum('ij,ij->i', no, ns), -1.0, 1.0)))
                    _ring = set()
                    for _v in np.unique(t[comp]):
                        _ring.update(_v2t[int(_v)])
                    _ring -= set(int(x) for x in comp)
                    if _ring:
                        _r = np.fromiter(_ring, dtype=np.int64)
                        _pd = float(np.average(_turn[comp], weights=ao[comp]))
                        _nd = float(np.average(_turn[_r], weights=ao[_r]))
                        # BOTH conditions: an absolute turn (so a surface that
                        # merely followed a big refit is not a kink) AND a turn
                        # far harder than its own neighbourhood (so a panel
                        # legitimately reoriented WITH the body is not either).
                        if (_pd >= _nc().COHERENCE_KINK_DEG
                                and _pd >= _nc().COHERENCE_KINK_RATIO
                                * max(_nd, 1e-6)):
                            kink = True
                            ok = True
                except Exception as _ke:
                    _note_pass_failure("_coherence_kink", _ke)
            if not ok and thin_extent < _nc().COHERENCE_THIN:
                # A THIN strip that reorients COHERENTLY is still wrong. A 2u hem
                # rim has no business turning relative to the surface it edges --
                # only a wide panel can legitimately be refit that way. Measured
                # on one piece: its two mirrored tip rims split, one crumpling
                # (0.84 -> 0.13, caught by the absolute gate) and the other
                # rotating as a group (0.89 -> 0.55, spared by it) -- and the
                # spared one was the half the user could still see curling up.
                # So for thin strips gate on the coherence DROP instead.
                ok = (cs - co) >= _nc().COHERENCE_THIN_DROP
            if not ok:
                continue

            ct = t[comp]
            core = np.unique(ct)
            # DILATE the smoothing region by RINGS before pinning a boundary.
            # Pinning the patch's own rim does not work: the buckle is driven by
            # uneven displacement AMONG those rim verts, so holding them holds
            # the cause. Measured with a rim-pinned version: bent-area fell 35%
            # but coherence did not recover at all (0.334 -> 0.313). Dilating
            # lets the rim even out while a ring further out still isolates the
            # repair from the rest of the garment.
            if _adj_full is None:
                _adj_full = defaultdict(set)
                for a, b, c in t:
                    _adj_full[int(a)].update((int(b), int(c)))
                    _adj_full[int(b)].update((int(a), int(c)))
                    _adj_full[int(c)].update((int(a), int(b)))
            region = set(int(v) for v in core)
            frontier = set(region)
            for _r in range(_nc().COHERENCE_DILATE):
                nxt = set()
                for v in frontier:
                    nxt |= _adj_full[v]
                nxt -= region
                region |= nxt
                frontier = nxt
            # the LAST ring added is the pinned boundary
            fixed_set = frontier if frontier else set()
            vin = np.fromiter(region, dtype=np.int64)
            free = np.fromiter((v for v in region if v not in fixed_set),
                               dtype=np.int64)
            if len(free) == 0:
                continue
            adj = {int(v): [n for n in _adj_full[int(v)] if n in region]
                   for v in region}
            mean_before = disp[vin].mean(axis=0)
            d = disp.copy()
            # A THIN strip cannot be saved by smoothing. Its triangles are ~1u
            # across, so even 0.1u of residual variation still rotates them
            # 30-45 deg -- measured: Laplacian smoothing CONVERGED (12, 40 and
            # 100 iterations gave an IDENTICAL result) with a third of the
            # buckle left. A rim that thin has to move RIGIDLY: one displacement
            # for the whole strip, which reproduces the authored shape exactly.
            # Still mean-preserving, so the fit is untouched. #coherence-rigid
            core_set = set(int(x) for x in core)
            if thin_extent < _nc().COHERENCE_THIN and not kink:
                d[core] = disp[core].mean(axis=0)
                blend = np.fromiter((v for v in free if int(v) not in core_set),
                                    dtype=np.int64)
            else:
                blend = free
            for _ in range(_nc().COHERENCE_ITERS):
                upd = d.copy()
                for v in blend:
                    nb = adj[int(v)]
                    if nb:
                        upd[v] = d[nb].mean(axis=0)
                d = upd
            # Restore the patch's MEAN displacement: the repair must not move the
            # garment as a whole, only even out the differential.
            d[vin] += (mean_before - d[vin].mean(axis=0))
            disp = d
            repaired += 1

        if repaired == 0:
            return out_verts, 0
        return _hold_repair_outside_body(
            ov, sv + disp, body_verts, body_normals), repaired
    except Exception as _e:
        # REPORTED, not swallowed. A silent return here is indistinguishable
        # from "no patch qualified", and that is exactly how this pass spent a
        # cycle looking like a working no-op (a missing defaultdict import).
        print(f"    [coherence-repair] FAILED: {type(_e).__name__}: {_e}")
        # The print reaches only a pool worker's stderr, which the frozen
        # exe discards; the recorder rides `reason` across the pool.
        _note_pass_failure("_repair_coherence_collapse", _e)
        return out_verts, 0

def _repair_winding_consistency(verts, tris, normals):
    """Flip any triangle whose winding disagrees with its own vertex normals.

    The two are read by different parts of the GPU: backface culling uses the
    WINDING, lighting uses the NORMALS. When they disagree the triangle is
    discarded from the side it is lit for and drawn on the side it is not --
    which is a flat dark shape. Nothing else in the pipeline checks the pair
    agree, so authored errors and pipeline damage both survive to the shipped
    mesh.

    The normals are treated as the authority, deliberately. On a closed body or
    garment they are smoothed across many triangles and are the more robust of
    the two, whereas a winding is a single ordering that one bad operation
    flips. Flipping only reorders indices: no vertex moves, no normal changes,
    UVs and skin weights are addressed by vertex index and are untouched.

    THE UNIT IS A CONNECTED REGION, NEVER ONE TRIANGLE. Flipping a single
    triangle inside a surface that is already consistently wound does not repair
    it, it TEARS it: its shared edges are then traversed the SAME way by both
    their triangles, the two faces point opposite ways, one is backface-culled,
    and you see straight through the garment.

    That is not hypothetical, it is what this pass used to do. Deciding per
    triangle on `face_normal . stored_normal < 0` looks safe on the AUTHOR's
    geometry -- and this runs AFTER the fit chain has moved verts (2.7u on the
    reported robe, 4.1u on a romper) while the stored normals do NOT move in
    lockstep. A small triangle in a high-curvature region then sits more than 90
    degrees from its own stale stored normal without the surface being wrong
    anywhere. Measured author-referenced over 744 paired NIFs: 611 of them
    (82.1%) gained winding seams, 153,712 in total, against authors carrying
    ZERO ([[project_winding_repair_tears_surfaces]]).

    The authors settle the question themselves: the sources ship 52,683
    triangles whose winding disagrees with their normals, on surfaces that are
    edge-consistent throughout. Per-triangle agreement is NOT the invariant.
    Edge consistency is.

    So: partition into regions already consistent with each other across shared
    edges, take ONE area-weighted vote per region, and flip the whole region or
    none of it. This still catches what the pass exists for -- a mesh mirrored
    by a negative-determinant transform bake -- because that inverts a whole
    region at once.

    Then a HARD GUARD: if the rewrite would raise the seam count for any reason,
    the original triangles are returned untouched, so the pass cannot make a
    surface worse whatever the region logic decides.

    Returns (tris, n_flipped).
    """
    v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris, dtype=np.int64)
    n = np.asarray(normals, dtype=np.float64)
    if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0 or len(n) != len(v):
        return t, 0
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    cr = np.cross(p1 - p0, p2 - p0)
    avg = n[t].mean(1)
    live = ((np.linalg.norm(cr, axis=1) > 1e-12)
            & (np.linalg.norm(avg, axis=1) > 1e-9))
    if not live.any():
        return t, 0
    # `cr` is UNNORMALISED, so its length is twice the triangle's area and this
    # dot is area-weighted by construction. A region's verdict is the sum over
    # its triangles, so slivers cannot outvote the body of a panel.
    vote = np.einsum("ij,ij->i", cr, avg)

    comp, seams_before = _nc()._winding_regions(t)
    n_comp = int(comp.max()) + 1 if len(comp) else 0
    if n_comp <= 0:
        return t, 0
    tally = np.bincount(comp[live], weights=vote[live], minlength=n_comp)
    # Strictly negative only: a region summing to exactly 0 has no evidence
    # either way and is left as authored.
    bad = (tally < 0.0)[comp]
    if not bad.any():
        return t, 0
    out = t.copy()
    out[bad] = out[bad][:, [0, 2, 1]]
    _, seams_after = _nc()._winding_regions(out)
    if seams_after > seams_before:
        return t, 0                    # never ship a surface with a NEW tear
    return out, int(bad.sum())

def _repair_degenerate_normals(verts, tris, normals, tol=0.5):
    """Replace unusable vertex normals with geometry-derived ones.

    A normal shorter than `tol` carries no direction -- it cannot shade a
    surface and it cannot decide a winding. The UBE body ships six of them
    (|n| = 0.0068) sitting exactly on the pubic hole boundary, so every job that
    consumes normals in that region is working from a zero vector: measured on a
    converted cuirass, five fill triangles averaged a normal length of 0.56 and
    one had a winding-vs-normal dot of exactly 0.000 -- its orientation decided
    by nothing.

    Only the unusable entries are touched; every healthy authored normal is left
    byte-identical, so this cannot restyle a mesh's shading.

    Returns (normals, n_repaired).
    """
    n = np.array(normals, dtype=np.float64, copy=True)
    ln = np.linalg.norm(n, axis=1)
    bad = ln < float(tol)
    if not bad.any():
        return n, 0
    geo = _nc()._vertex_normals_from_tris(verts, tris)
    # Keep the authored sign where there is any signal at all to keep; with a
    # length of 0.0068 there generally is not, and the geometric normal is the
    # only defensible answer.
    n[bad] = geo[bad]
    ln2 = np.linalg.norm(n, axis=1)
    still = ln2 < 1e-6
    if still.any():                    # fully isolated verts: leave a unit up
        n[still] = np.array([0.0, 0.0, 1.0])
    return n, int(bad.sum())

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
    # Repair before ANY use: the per-triangle winding vote below reads these,
    # and a zero-length normal makes that vote meaningless rather than merely
    # inaccurate.
    normals, _ = _repair_degenerate_normals(verts, tris, normals)

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
    for a, b in sorted(boundary_edges):  # sorted: #deterministic-set-iteration
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
        if not (_nc().PUBIC_HOLE_Z_MIN <= z_min and z_max <= _nc().PUBIC_HOLE_Z_MAX):
            continue
        if max(abs(x_min), abs(x_max)) > _nc().PUBIC_HOLE_X_BOUND:
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
        # PER TRIANGLE, not per fan. The mean dot over the whole fan is an
        # all-or-nothing vote, and the pubic loop is curved enough that a fan
        # from one apex spans both orientations -- so every triangle that
        # disagreed with the average was left inverted, rendering flat black.
        # Measured on one converted body: 366 fill triangles, 105 inverted,
        # 28 of them outside the garment and therefore visible, at z 66-69 on the
        # rear midline. Reported in game as black polygons at the waist seen from
        # below. The per-triangle dot was already being computed here and then
        # discarded by the .mean(); this just uses it.
        flip = (face_n * src_n).sum(axis=1) < 0
        if flip.any():
            fan[flip] = fan[flip][:, [0, 2, 1]]
        new_tris_chunks.append(fan)
        n_closed += 1

    if not new_tris_chunks:
        return tris, 0
    appended = np.vstack(new_tris_chunks).astype(tris.dtype)
    return np.vstack([tris, appended]), n_closed

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
                preserve_authored_skin=False, override_normals=None,
                skip_geometry_repair=False):
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
        # #seam-weld-self on EVERY path that moves verts, not just the phase-2
        # chain. Wiring it only into the chain left the copy-path pieces torn --
        # measured on one armour set: the cuirass 0 split seams (chain path) but
        # its gauntlets 60, worst 2.95u, boots 18, first-person cuirass 70. Welding
        # BEFORE the normal recompute below so the normals describe the welded
        # surface rather than the torn one.
        if _nc().SEAM_WELD_SELF and _geometry_repair_allowed(
                src_shape, skip_geometry_repair):
            try:
                _sv = np.asarray(src_shape.verts, dtype=np.float64)
                _wv, _nw = _nc()._weld_source_coincident_verts(_sv, ov)
                if _nw:
                    ov = _wv
            except Exception as _we:
                # REPORTED, like its sibling below. A bare `pass` here is
                # indistinguishable from "no seam needed welding", and the
                # defect this pass exists for -- torn seams on the COPY path --
                # is invisible on disk until someone looks at the mesh. The
                # coherence repair immediately below already reports; this one
                # was the last silent pass in the block.
                _note_pass_failure("_weld_source_coincident_verts", _we)
        # Un-buckle thin features the fit crumpled. AFTER the weld (which can
        # itself move a rim vert) and BEFORE the normal recompute, so the
        # normals describe the repaired surface. #coherence-repair
        if _geometry_repair_allowed(src_shape, skip_geometry_repair):
            try:
                _sv2 = np.asarray(src_shape.verts, dtype=np.float64)
                _cv, _nc_local = _repair_coherence_collapse(
                    _sv2, ov, src_shape.tris)
                if _nc_local:
                    ov = _cv
                    print(f"    [coherence-repair] {src_shape.name}: "
                          f"un-buckled {_nc_local} patch(es)")
                # A crumpled strap: give every edge the length its neighbourhood
                # agrees on. Wired at BOTH sites -- doing only one left 5% of the
                # pack torn when the seam weld was added that way.
                # #strap-scale-uniform
                _uv, _nsu = _uniformise_local_scale(_sv2, ov, src_shape.tris)
                if _nsu:
                    ov = _uv
                    print(f"    [strap-scale] {src_shape.name}: "
                          f"uniformised {_nsu} vert(s)")
                # Sub-millimetre detail the fit blew up. Last, so nothing after it
                # re-stretches what it just pulled back. #short-edge-cap
                _ev, _nse = _cap_short_edge_stretch(_sv2, ov, src_shape.tris)
                if _nse:
                    ov = _ev
                    print(f"    [short-edge] {src_shape.name}: "
                          f"un-stretched {_nse} vert(s)")
            except Exception as _ce:
                # REPORTED, never swallowed. A silent handler here is
                # indistinguishable from "no patch qualified", and that is
                # exactly how a missing import once made this pass a no-op that
                # looked like a clean result. The sibling site in phase 2 is not
                # wrapped at all; this one is, so it must speak.
                _note_pass_failure("_repair_coherence_collapse", _ce)
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

    # `override_normals` wins over both branches above. Used by the pubic-hole
    # fill, whose source normals are trusted verbatim (see below) and so must be
    # repaired BEFORE they are trusted -- the UBE body ships a handful of
    # zero-length normals right on the hole boundary, and a fill triangle built
    # on them is shaded from a zero vector.
    if override_normals is not None:
        _on = np.asarray(override_normals, dtype=np.float64)
        if len(_on) == len(use_verts):
            use_normals = [tuple(float(c) for c in row) for row in _on]

    # With override_tris, keep source normals as-is: fill tris inherit existing
    # normals (outward-pointing, continuous shading). No recompute needed.
    if override_tris is not None:
        ot = np.asarray(override_tris, dtype=np.int64)
        use_tris = [tuple(int(c) for c in row) for row in ot]
    else:
        use_tris = list(src_shape.tris)

    # Bake non-identity geometry transform (scale/rotation) into verts+normals
    # so the output is an identity-transform skinned shape (skin-to-bone adjusted).
    _bake_T = _nc()._shape_bake_matrix(src_shape)
    # Pure-translation bake only on the copy path (override verts are already
    # body-positioned). Shapes with global_to_skin use _align_scale_bone_stbs_to_verts
    # instead; skip the vert-lift bake so both corrections don't double-apply.
    _bake_trans = (_nc()._shape_bake_translation(src_shape)
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

    _repair_ok = _geometry_repair_allowed(src_shape, skip_geometry_repair)

    # Degenerate normals on EVERY written shape, not just the injected body.
    # Wiring this only into the body path left the boots shipping a 0.0068-long
    # normal -- they take the copy path, so the repair never saw them.
    if (_nc().WINDING_CONSISTENCY_REPAIR and _repair_ok
            and use_normals is not None and len(use_tris)):
        _fn, _nrep = _repair_degenerate_normals(
            np.asarray(use_verts, dtype=np.float64),
            np.asarray(use_tris, dtype=np.int64),
            np.asarray(use_normals, dtype=np.float64))
        if _nrep:
            use_normals = [tuple(float(c) for c in row) for row in _fn]

    # #winding-consistency -- LAST, after the transform bake, because a bake
    # matrix with negative determinant mirrors the geometry and flips winding
    # by itself. Repairing before it would be repairing the wrong orientation.
    if (_nc().WINDING_CONSISTENCY_REPAIR and _repair_ok
            and use_normals is not None and len(use_tris)):
        _ut, _nflip = _repair_winding_consistency(
            np.asarray(use_verts, dtype=np.float64),
            np.asarray(use_tris, dtype=np.int64),
            np.asarray(use_normals, dtype=np.float64))
        if _nflip:
            use_tris = [tuple(int(c) for c in row) for row in _ut]

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
    # angles (measured on an HDT-SMP elven cuirass). The engine IGNORES a skinned shape's
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
        # leftover scale/translation flings the mesh off-body (the scale-bake
        # case). GATED on src_shape.bone_names: a NON-skinned transform IS
        # engine-honored, so it must NOT be zeroed on the override path. GATED on
        # not _g2s_offset: an offset-g2s shape's transform is the cull-bound match
        # (above) -- keep it.
        try:
            _idt = _nc()._pynifly().TransformBuf()
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
            _pyn = _nc()._pynifly()
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
                # the new controller id, or NODEID_NONE for a static glow. ANIMATED by
                # default (the reload-CTD that once forced static was traced to thigh
                # SCALE bones on the glow skin, not the controller, and fixed);
                # CBBE2UBE_NO_GLOW_ANIM=1 forces a static glow.  [DESIGN: Effect-shader glow overlays]
                try:
                    if _nc()._EFFECT_GLOW_ANIM:
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
                except Exception as _e:
                    # No effect shader block: the glow shape ships white.
                    _note_pass_failure(
                        f"_copy_shape/effect-shader:{src_shape.name}", _e)
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
                except Exception as _e:
                    _note_pass_failure(
                        f"_copy_shape/shader-flush:{src_shape.name}", _e)
    except Exception as _e:
        # Shader transplant failed: the shape ships with pynifly's default
        # lighting shader (a glow shape renders white and static).
        _note_pass_failure(f"_copy_shape/shader:{src_shape.name}", _e)

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
                     and not (_is_effect_shape and _nc().EFFECT_SHADER_SOURCE_SKIN))
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
        if (src_shape.name in _nc().UBE_BODY_INJECT_NAMES
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
    # block IDs). skip_alpha=True copies the shape WITHOUT recreating its
    # NiAlphaProperty -- an alpha-free copy for callers that need one. (An
    # earlier note claimed NioOverride gates BodyMorph on the ABSENCE of an alpha
    # property; that was disproven in-game -- alpha-bearing shapes morph fine --
    # so this is NOT a morph fix.)
    # Fault-isolate the alpha-property READ: on a source with a broken alpha
    # block reference, pynifly's has_alpha_property getter itself raises
    # ("getNiAlphaProperty called on invalid node"), and an unguarded read here
    # failed the whole _copy_shape -> shape DROPPED -> invisible piece in-game
    # (a broken-alpha gauntlet class). Copy WITHOUT alpha instead: visible geometry
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

def _reauthor_nif_fresh(dst_path: Path, override_verts_by_name=None,
                        exclude_shapes=None, nif=None, shape_order=None) -> bool:
    """Re-author a NIF from scratch into a fresh NifFile — copy every shape
    via _copy_shape (clean pynifly authoring) instead of leaving the
    source-derived bytes produced by the verbatim `shutil.copy2` path.

    `shape_order`: optional list of shape NAMES giving the order to emit them
    in. Shape order IS output state — an ARMA `AlternateTextures` entry selects
    its target by INDEX, so re-emitting a NIF with its shapes permuted silently
    re-points every colour-variant swap that names it (BUG-09). Names absent
    from the NIF are ignored; shapes absent from the list keep their current
    relative order and follow the listed ones, so a partial list is safe.

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
        pyn = _nc()._pynifly()
        # Re-author from an already-open NIF when the caller passes one (the fitted
        # finalize pass hands over its loaded nf, weight edits included, so verts +
        # weights commit in ONE re-author instead of a second load).
        old = nif if nif is not None else pyn.NifFile(filepath=str(dst_path))
        shapes = list(old.shapes)
        if not shapes:
            return False
        if shape_order:
            # Stable permutation: listed names first in the order given, then
            # everything else in its existing relative order. Never drops a
            # shape -- a name in `shape_order` that this NIF does not have is
            # skipped, and a shape the list does not mention still ships.
            _rank = {n: i for i, n in enumerate(shape_order)}
            shapes.sort(key=lambda s: (_rank.get(s.name, len(_rank)),))
        # Capture extra-data + hidden flags to re-apply after rebuild.
        #
        # ALL of them, not the first. This used to keep a single
        # (bodytri_str, bodytri_owner) and break out of both loops, so a
        # re-author COLLAPSED every BODYTRI in the NIF down to one and handed
        # it to whichever shape came first in `shape_order`. Hand-authored
        # slot-32 UBE armour tags the body AND every cloth shape, so the
        # collapse silently undid that arrangement no matter what
        # `_pick_bodytri_carriers` returned -- a pass destroying another
        # pass's output. See #bodytri-all-shapes.
        #
        # Enumerated by index over `extraDataCount`: `extra_data()` stops at
        # the first block it cannot build, and a BodySlide body carries a
        # NiIntegersExtraData `LOCKEDNORM` at index 0, which made every body
        # shape read as having no extra data -- BODYTRI included.
        bodytri_owners: "list[tuple[str, str]]" = []
        for s in shapes:
            try:
                n_ed = int(getattr(s.properties, "extraDataCount", 0) or 0)
            except Exception:
                n_ed = 0
            for i in range(n_ed):
                try:
                    ed = s.get_extra_data(target_index=i)
                except Exception:
                    continue
                if ed is not None and getattr(ed, "name", None) == "BODYTRI":
                    bodytri_owners.append((s.name, ed.string_data))
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

        # Authored-skin set: SMP colliders, per-vertex soft-bodies and layered
        # cloth must keep their skin VERBATIM through a rebuild. _copy_shape's
        # default path runs _install_skin's genital/jiggle strips + influence
        # processing on them -- the #smp-collider-skin-preserve equip-CTD class
        # -- and this rebuild runs AFTER _finalize_hdt_physics deliberately
        # re-imported those shapes with preserve_authored_skin=True (it also
        # re-processed the bust-split collider clone). Audit 2026-07-28.
        _preserve = set()
        # Kept SEPARATE on purpose. `_preserve` answers "keep this shape's
        # authored SKIN"; `_colliders` answers "may the RENDERING repairs run".
        # Those are different questions, and conflating them silently disabled
        # the seam weld on every soft-body and layered-cloth garment this path
        # re-authors -- which is most of the seam splits left in the pack.
        _colliders: set = set()
        try:
            _colliders = set(_hdt_collider_shape_names(dst_path, nif=old))
            _preserve |= _colliders
            _preserve |= _hdt_softbody_shape_names(dst_path, nif=old)
            _preserve |= _layered_cloth_shape_names(shapes)
        except Exception:
            pass

        tmp_path = dst_path.with_suffix(".nif.reauth")
        new = pyn.NifFile()
        new.initialize("SKYRIMSE", str(tmp_path))
        # Carry the physics-chain ANCHORS over at their global placement BEFORE
        # any shape is copied. This rebuild drops nodes that carry no skin weight
        # (the anchors' unweighted ancestors), and the first `add_bone` below then
        # recreates the anchor itself flat at IDENTITY -- so without this the
        # re-authored weight lands with its whole chain rig ~69u low even when the
        # pre-rebuild NIF was correct. Measured exactly that: `_0` (not
        # re-authored) came out right and `_1` came out 68.91u low, differing by
        # precisely the two seeded ancestor nodes. Seeded from `old`, which still
        # holds the correct globals at this point. #anchor-global-fix
        try:
            _seed_flat_chain_anchors(new, old)
        except Exception as _se:
            # The other two call sites report via _note_pass_failure; this one
            # swallowed. A dropped seed here is what left `_1` 68.91u low, so a
            # silent failure is the one outcome that must not be possible.
            _note_pass_failure("_seed_flat_chain_anchors", _se)
        copy_failed = []
        _ov = override_verts_by_name or {}
        _excl = exclude_shapes or set()
        for s in shapes:
            if s.name in _excl:
                continue                 # drop this shape from the re-author
            try:
                _copy_shape(s, new, override_verts=_ov.get(s.name),
                            preserve_authored_skin=(s.name in _preserve),
                            skip_geometry_repair=(s.name in _colliders))
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
        for _bt_owner, _bt_str in bodytri_owners:
            tgt = next((x for x in new.shapes if x.name == _bt_owner), None)
            if tgt is not None and _bt_str:
                NiStringExtraData.New(
                    new, name="BODYTRI", string_value=_bt_str, parent=tgt)
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
    except Exception as _se:
        # A failed re-author returns False and the caller falls back, so the
        # piece survives -- but the REASON was lost, and this rebuild is what
        # drops unweighted chain nodes (#chain-anchor-recreate). A silent
        # failure here is a whole rig quietly not rebuilt.
        _note_pass_failure("_reauthor_nif_fresh", _se, dst_path)
        try:
            import os as _os
            tp = dst_path.with_suffix(".nif.reauth")
            if tp.is_file():
                _os.remove(str(tp))
        except Exception:
            pass
        return False
