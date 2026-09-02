"""Layered cloth: authored layer depth and relation, stack grouping, the chest / abdomen depth separation, the layer ride on the reference layer (with its barycentric displacement and feathering), the layer-order repair, the glow-overlay ride and the cross-shape seam weld.

Split out of nif_convert.py on 2026-09-01 (split step 7). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import os
import sys

from .envflags import flag as _flag, knob as _knob
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


def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


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
            mask = ((v[:, 2] >= _nc().CHEST_SYNC_Z_MIN)
                    & (v[:, 2] <= _nc().CHEST_SYNC_Z_MAX)
                    & (np.abs(v[:, 0]) <= _nc().CHEST_SYNC_X_BOUND)
                    & (v[:, 1] >= _nc().CHEST_SYNC_Y_MIN))
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
        auth_src_all = _shape_src(auth_job)
        for recv_job, recv_mask, _ in candidates[1:]:
            # #coincident-twin-not-a-layer. A receiver that COINCIDES with the
            # authority is the same surface authored twice -- two materials, or
            # a two-pass shader -- not an inner layer at the same depth. This
            # pass exists for a bra under fabric: different surfaces that happen
            # to share a depth. A twin shares every VERTEX.
            #
            # Ungated, the size sort picks one twin arbitrarily and declares the
            # other an inner layer, then pushes it CHEST_DEPTH_SEPARATION (0.4u)
            # INTO the body; the cross-shape seam weld afterwards averages the
            # two coincident copies, so BOTH ship buried and identical. Measured
            # on the reported bodysuit's `Suit`/`Suit 2` pair: 7222 verts pushed
            # inward, and the slim variant shipped 2296 shoulder verts inside
            # the body against 39 with this pass neutralised.
            #
            # The SOURCE-ORDER GATE below cannot catch it: it only spares a vert
            # the source puts OUTSIDE the authority, and a coincident vert is
            # neither outside nor inside -- it sits exactly at 0.
            if _nc().TWIN_LAYER_GUARD and _nc()._is_coincident_twin(
                    auth_src_all, auth_mask, _shape_src(recv_job), recv_mask,
                    auth_job, recv_job):
                continue
            recv_v_full = np.asarray(recv_job["verts"], dtype=np.float64)
            recv_chest_idx_in_shape = np.where(recv_mask)[0]
            recv_chest = recv_v_full[recv_mask]

            xz_dists, nearest_auth = auth_xz_tree.query(
                recv_chest[:, [0, 2]], k=1,
                distance_upper_bound=_nc().CHEST_DEPTH_PAIR_XZ_DIST,
            )
            valid = (xz_dists < _nc().CHEST_DEPTH_PAIR_XZ_DIST)
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
            fighting = ((signed > -_nc().CHEST_DEPTH_SEPARATION)
                        & (signed < _nc().CHEST_DEPTH_FRONT_TOL))
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
                        distance_upper_bound=_nc().OVERLAY_PAIR_R)
                    matched = np.isfinite(sd)
                    src_gap = np.zeros(len(rs_valid))
                    if matched.any():
                        src_gap[matched] = (
                            _src_clr(rs_valid[matched])
                            - auth_src_clr[si[matched]])
                    fighting &= ~(matched
                                  & (src_gap >= _nc().OVERLAY_LOCAL_ORDER_MIN))
            push_amt = np.where(fighting, -_nc().CHEST_DEPTH_SEPARATION - signed, 0.0)
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
    except Exception as _le:
        # A refactor bug here kills this inter-layer pass for EVERY
        # conversion forever, and 'crashed on line 1' prints exactly what
        # 'no layers in this NIF' prints (audit 2026-07-28, the dead-
        # backstop class). One line, always.
        print(f"  WARN: _separate_chest_layered_cloth_depth died: {_le!r} -- "
              f"layer pass skipped for this piece", file=sys.stderr)
        # ALSO to the recorder: a pool worker's stdout can be discarded by the
        # frozen exe, and this print was the only trace that the layer order
        # shipped unresolved. #worker-print-vanishes
        _note_pass_failure("_separate_chest_layered_cloth_depth", _le)
        return 0

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
                                    distance_upper_bound=_nc().OVERLAY_PAIR_R)
                dda, uia = ta.query(B["_ov"], k=1,
                                    distance_upper_bound=_nc().OVERLAY_PAIR_R)
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
                if len(sgap) < _nc().OVERLAY_MIN_OVERLAP:
                    continue
                stree = cKDTree(spts)
                K = min(32, len(spts))

                def _field(qpts, _stree=stree, _sgap=sgap, _swt=swt, _K=K):
                    """Local order field at each query vert: weighted mean AND
                    weighted sign-consistency fraction of the pooled gap
                    samples within PAIR_R (>= 4 samples, else no opinion)."""
                    dd, ui = _stree.query(qpts, k=_K,
                                          distance_upper_bound=_nc().OVERLAY_PAIR_R)
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
                oa = ma & ~(raw_a <= -_nc().OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_a >= _nc().OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_a >= _nc().OVERLAY_LOCAL_CONSIST))
                    | ((raw_a >= _nc().OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_a >= -_nc().OVERLAY_LOCAL_ORDER_MIN)))
                ob = mb & ~(raw_b <= -_nc().OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_b <= -_nc().OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_b <= 1.0 - _nc().OVERLAY_LOCAL_CONSIST))
                    | ((raw_b >= _nc().OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_b <= _nc().OVERLAY_LOCAL_ORDER_MIN)))
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
                aa = ma & ~(raw_a >= _nc().OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_a <= -_nc().OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_a <= 1.0 - _nc().OVERLAY_LOCAL_CONSIST))
                    | ((raw_a <= -_nc().OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_a <= _nc().OVERLAY_LOCAL_ORDER_MIN)))
                ab = mb & ~(raw_b >= _nc().OVERLAY_LOCAL_RAW_STRONG) & (
                    ((mean_b >= _nc().OVERLAY_LOCAL_ORDER_MIN)
                     & (fpos_b >= _nc().OVERLAY_LOCAL_CONSIST))
                    | ((raw_b <= -_nc().OVERLAY_LOCAL_RAW_STRONG)
                       & (mean_b >= -_nc().OVERLAY_LOCAL_ORDER_MIN)))

                # Bind gated verts to their SOURCE partners (k-NN in the
                # ordering frame, split by which side of the vert each
                # partner is on). (i, j) in below_pairs[(a, b)] means: a's
                # vert i must clear b's vert j by LAYER_STACK_GAP; in
                # above_pairs it means a's vert i must stay 0.05 below j.
                def _bind(qv_ov, qv_oc, gate_out, gate_ceil, pv_ov, pv_oc,
                          ptree):
                    KS = min(8, len(pv_ov))
                    dd, jj = ptree.query(qv_ov, k=KS,
                                         distance_upper_bound=_nc().OVERLAY_PAIR_R)
                    if KS == 1:
                        dd = dd[:, None]; jj = jj[:, None]
                    val = np.isfinite(dd)
                    ii = np.broadcast_to(
                        np.arange(len(qv_ov))[:, None], val.shape)[val]
                    jj = jj[val]
                    g = qv_oc[ii] - pv_oc[jj]
                    sb = gate_out[ii] & (g >= _nc().OVERLAY_LOCAL_ORDER_MIN)
                    # A STRONG source-above partner ceilings the vert
                    # UNCONDITIONALLY (no gate): whatever lifts this vert —
                    # its own tier-2 fire on a different partner, or
                    # smoothing bleed — it must never cross a sheet that sat
                    # clearly above it in the source. This is what keeps a
                    # true vert-scale weave intact when tier-2 fires on its
                    # other side. Weak above partners still need the gate.
                    sa = (g <= -_nc().OVERLAY_LOCAL_ORDER_MIN) & (
                        gate_ceil[ii] | (g <= -_nc().OVERLAY_LOCAL_RAW_STRONG))
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
                    np.maximum.at(req, ii, co[jj] + _nc().LAYER_STACK_GAP)
                with np.errstate(invalid="ignore"):
                    push = req - c
                push[~np.isfinite(push)] = 0.0
                headroom = np.maximum(_nc().OVERLAY_CAP - cum_push[a], 0.0)
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
                push = np.minimum(np.clip(push, 0.0, _nc().OVERLAY_CAP),
                                  np.minimum(headroom, allowed))
                push = np.minimum(
                    np.clip(_nc()._smooth_overlay_push(push, v), 0.0, _nc().OVERLAY_CAP),
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
    except Exception as _le:
        # A refactor bug here kills this inter-layer pass for EVERY
        # conversion forever, and 'crashed on line 1' prints exactly what
        # 'no layers in this NIF' prints (audit 2026-07-28, the dead-
        # backstop class). One line, always.
        print(f"  WARN: _separate_abdomen_layered_cloth_depth died: {_le!r} -- "
              f"layer pass skipped for this piece", file=sys.stderr)
        _note_pass_failure("_separate_abdomen_layered_cloth_depth", _le)
        return 0

def _layered_cloth_shape_names(shapes) -> "set[str]":
    """Names of shapes in a MULTI-LAYER cloth group: 2+ shapes whose names share a base
    stem and differ only by a short layer suffix (Cuirass_A/_B/_C, Robe_01/_02). Such
    authored cloth keeps its SOURCE skin -- every body-follow graft pass skips it, so the
    body's HDT-SMP jiggle bones aren't grafted on and the shape doesn't CTD on equip.
    #layered-cloth-skin"""
    if not _nc()._LAYERED_CLOTH_SKIN:
        return set()
    groups: "dict[str, list[str]]" = {}
    for s in shapes:
        nm = getattr(s, "name", "") or ""
        m = _nc()._LAYER_SUFFIX_RE.match(nm)
        if m:
            groups.setdefault(m.group(1).lower(), []).append(nm)
    return {n for members in groups.values() if len(members) >= 2 for n in members}

def _stacked_layer_groups(shapes, *, radius: float = 0.0, cover: float = 0.0,
                          exclude: "set[str] | None" = None) -> "list[list]":
    """Connected groups of shapes that STACK on one another (#layer-follow-divergence).

    Stacked means COVERAGE, not contact: at least `cover` of the SMALLER shape's
    verts have a partner in the other within `radius`. A buckle or a trim strip
    touches a cuirass along its border and is not a layer; a layer shadows a
    large share of its neighbour. Censused over the pack, contact alone would
    have swept in two thirds of all shapes.

    Compared in WORLD space. Shapes in one NIF can carry different
    global-to-skin transforms, so comparing raw `verts` would pair shapes that
    are nowhere near each other -- and, worse, silently miss ones that are.

    `exclude` keeps non-garment shapes out of a group entirely. Without it the
    census grouped a shoe with the FEET and a cuirass with its own `ColBody`
    collider: neither is a cloth layer, and dragging one into a group would
    constrain the garment's bone basis to a body part's.

    Returns a list of groups, each a list of (name, world_verts, shape).
    """
    rad = _nc()._LAYER_STACK_RADIUS if radius <= 0.0 else radius
    cov = _nc()._LAYER_STACK_COVER if cover <= 0.0 else cover
    if not _nc()._FULL_WEIGHT_LAYER_GUARD or rad <= 0.0 or cov <= 0.0:
        return []
    skip = set(exclude or ())
    items = []
    for s in shapes:
        nm = getattr(s, "name", "") or ""
        if not nm or nm in skip or nm in _nc().RESKIN_SKIP_NAMES:
            continue
        try:
            sv = np.asarray(s.verts, dtype=np.float64)
            if len(sv) == 0:
                continue
            items.append((nm, _verts_skin_to_world(sv, _shape_global_to_skin(s)), s))
        except Exception as _e:
            _note_pass_failure("_stacked_layer_groups/verts", _e)
    if len(items) < 2:
        return []
    from scipy.spatial import cKDTree as _KD
    trees = [_KD(v) for _, v, _ in items]
    n = len(items)
    parent = list(range(n))

    def _find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            a, b = (i, j) if len(items[i][1]) <= len(items[j][1]) else (j, i)
            d, _ = trees[b].query(items[a][1], k=1)
            if float((np.asarray(d) <= rad).mean()) >= cov:
                ra, rb = _find(i), _find(j)
                if ra != rb:
                    parent[ra] = rb
    buckets: "dict[int, list]" = {}
    for i in range(n):
        buckets.setdefault(_find(i), []).append(items[i])
    return [g for g in buckets.values() if len(g) >= 2]

def _canonical_stack_name_groups(src_nif_path, exclude) -> "list | None":
    """Stacked-shape groups as NAME SETS, decided on the `_1` source weight.

    Returns None when there is no usable source, so the caller falls back to
    deciding from the file in hand rather than silently grouping nothing.
    """
    if not src_nif_path:
        return None
    try:
        p = Path(src_nif_path)
        stem = p.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        cand = p.parent / (stem + "_1" + p.suffix)
        if not cand.is_file():
            cand = p                      # unweighted piece: it IS canonical
        if not cand.is_file():
            return None
        st = cand.stat()
        key = (str(cand).lower(), st.st_mtime_ns, st.st_size, tuple(sorted(exclude)))
        hit = _nc()._STACK_NAME_GROUP_CACHE.get(key)
        if hit is not None:
            return [set(g) for g in hit]
        snif = _nc()._pynifly().NifFile(filepath=str(cand))
        groups = _stacked_layer_groups(snif.shapes, exclude=exclude)
        names = [{m[0] for m in g} for g in groups]
        _nc()._STACK_NAME_GROUP_CACHE[key] = [set(g) for g in names]
        return names
    except Exception as _e:
        _note_pass_failure("_canonical_stack_name_groups", _e)
        return None

# Adjacent solid plates that share a seam (edges modeled flush) are warped as independent
# shapes, so their shared seam ring drifts apart into a visible gap. Fix: verts coincident
# across different plate shapes in the SOURCE were meant to touch -- weld each such
# cross-shape cluster to its centroid AFTER the warp. Tight source-coincidence tol is the
# gate, so it never welds an intentional layer (which sits mm above, not coincident);
# normals aren't gated (a flush rim has opposed normals but is a real seam). Identity-g2s
# only. CBBE2UBE_NO_SEAM_WELD=1 off; CBBE2UBE_SEAM_WELD_TOL overrides tol.
_SEAM_WELD_TOL = float(os.environ.get("CBBE2UBE_SEAM_WELD_TOL", "0.05") or "0.05")

def _weld_cross_shape_seams(shape_jobs, tol: float = _SEAM_WELD_TOL,
                            exclude_names=None):
    """Weld source-coincident cross-plate seam verts to their centroid.

    Operates on the pass-1 `shape_jobs` (each {"src", "verts",
    "verts_modified", ...}). Returns the count of welded verts. Best-effort.

    `exclude_names`: SMP colliders / per-vertex soft-bodies / layered cloth.
    The weld moves REST verts and the skin-match below synthesizes an
    override_skin and edits seam weights -- both forbidden on authored physics
    geometry (sim rest-pose desync / the drift-and-CTD class; every other
    skin pass carries these skips, this one did not -- audit 2026-07-28).
    A physics shape sharing a source-coincident seam with a rigid plate keeps
    its authored rest + skin; the rigid side still welds toward the centroid
    of its own members."""
    if _flag("CBBE2UBE_NO_SEAM_WELD", False):
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    _excl = exclude_names or set()
    # Candidate plates: textured, non-effect, identity-g2s (frame match),
    # and never authored-physics geometry.
    plates = [j for j in shape_jobs
              if (j["src"].textures or {})
              and (j["src"].name or "") not in _excl
              and not _nc()._shape_has_effect_shader(j["src"])
              and _nc()._shape_has_identity_g2s(j["src"])]
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
    if seam_clusters and not _flag("CBBE2UBE_NO_SEAM_SKIN_MATCH", False):
        try:
            nsm = _nc()._match_seam_skinning(plates, seam_clusters)
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

_LAYER_RIDE_MAX = float(os.environ.get("CBBE2UBE_LAYER_RIDE_MAX", "2.0") or "2.0")

_LAYER_ORDER_NEAR = float(os.environ.get("CBBE2UBE_LAYER_ORDER_NEAR", "2.0") or "2.0")

_LAYER_ORDER_ITERS = int(os.environ.get("CBBE2UBE_LAYER_ORDER_ITERS", "2") or "2")

def _layer_order_eligible(j, softbody_names=frozenset(), collider_names=frozenset()):
    """Garment shapes whose cross-layer order we may correct: textured, non-effect,
    non-collider/softbody (those keep their authored rest pose), identity-g2s (source
    and final verts must share a frame for a signed offset to mean anything)."""
    s = j["src"]
    nm = s.name or ""
    if nm in _nc().UBE_BODY_INJECT_NAMES or _nc()._is_inline_body_name(nm):
        return False
    if nm in softbody_names or nm in collider_names or nm.lower().startswith("col"):
        return False
    if _nc()._shape_has_effect_shader(s):
        return False
    if not (s.textures or {}):
        return False
    try:
        if s.has_global_to_skin:
            g = _shape_global_to_skin(s)
            if not (g is None or _g2s_is_identity(g)):
                return False
    except Exception:
        return False
    return True

def _repair_layer_order(shape_jobs, softbody_names=frozenset(),
                        collider_names=frozenset(),
                        near: float = _LAYER_ORDER_NEAR,
                        iters: int = _LAYER_ORDER_ITERS):
    """Restore each vert to the SOURCE side of every nearby layer. See #layer-order.

    ONE DIRECTION ONLY, and now deliberately so. The test below fires on
    SWALLOWED verts (outside a neighbour in the source, INSIDE it now) and
    structurally cannot see the mirror -- an inner layer that has SURFACED
    through the garment over it, which is what "the bodystock is visible through
    the leather" looks like as geometry.

    THE MIRROR WAS BUILT AND REVERTED (#containment-restore, 2026-08-13). With
    the source pairing, a body-clearance clamp and a source-order inward budget
    all in place it cut the vertex-proxy flip count 812 -> 515 on the vanilla
    the layered robe -- and made the VALIDATED ray-occlusion score WORSE: verts
    the source covers and we expose went 1323 -> 1389 (1373 with the budget).
    It converts one order violation into the other, because a stack this tight
    has nowhere to put the vert: without the budget it created 201 newly
    swallowed verts, and with it the flips came straight back. Do not rebuild it
    against a vertex-order metric -- that family is measured ANTI-CORRELATED
    with what the user sees (project_clipping_metric_validated). The scratchpad
    `cover_census.py` scores the relation that matters.

    Returns the count of corrected verts. Best-effort; a caller wraps it.
    """
    if not _nc().LAYER_ORDER_REPAIR_ENABLED:
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    layers = [j for j in shape_jobs
              if _layer_order_eligible(j, softbody_names, collider_names)]
    if len(layers) < 2:
        return 0

    # (src verts, src normals, tris, dst verts) per layer, kept in lockstep.
    L = []
    for j in layers:
        try:
            sv = np.asarray(j["src"].verts, dtype=np.float64)
            sn = j["src"].normals
            tris = np.asarray(j["src"].tris, dtype=np.int64)
            dv = np.asarray(j["verts"], dtype=np.float64)
        except Exception:
            continue
        if sn is None or sv.ndim != 2 or sv.shape != dv.shape or len(sv) == 0:
            continue
        sn = np.asarray(sn, dtype=np.float64)
        if sn.shape != sv.shape or tris.size == 0:
            continue
        L.append({"j": j, "sv": sv, "sn": sn, "tris": tris, "dv": dv})
    if len(L) < 2:
        return 0

    # SOURCE pairing is fixed (computed once): the authored relationship is the truth.
    trees = [cKDTree(x["sv"]) for x in L]
    pairing = {}
    for ia, A in enumerate(L):
        for ib, B in enumerate(L):
            if ia == ib:
                continue
            d, idx = trees[ib].query(A["sv"])
            m = d < near
            if m.sum() < 3:
                continue
            ai = np.where(m)[0]
            bj = idx[ai]
            s_src = np.einsum('ij,ij->i', A["sv"][ai] - B["sv"][bj], B["sn"][bj])
            pairing[(ia, ib)] = (ai, bj, s_src)
    if not pairing:
        return 0

    corrected = set()
    for _ in range(max(1, iters)):
        # Destination normals must be recomputed each round -- the verts moved.
        dn = [_nc()._recompute_vertex_normals(x["dv"], x["tris"], source_normals=x["sn"])
              for x in L]
        moves = [np.zeros_like(x["dv"]) for x in L]
        mag = [np.zeros(len(x["dv"])) for x in L]
        any_fix = False
        for (ia, ib), (ai, bj, s_src) in pairing.items():
            A, B = L[ia], L[ib]
            nb = dn[ib][bj]
            s_dst = np.einsum('ij,ij->i', A["dv"][ai] - B["dv"][bj], nb)
            # Was on/outside B in the source, is INSIDE B now -> swallowed.
            #
            # ONLY the sign flip. Firing on a LOST GAP as well -- restoring the
            # authored DISTANCE rather than only the authored SIDE -- was built and
            # REVERTED; see #layer-gap-rejected in PIPELINE.md. The diagnosis behind
            # it stands (the authored gap survives on as little as 12.3% of a pair's
            # verts), but every version of the repair made the piece visibly worse,
            # because this pass can only correct verts inside its pairing radius and
            # cannot make that boundary invisible once most of them fire.
            flip = (s_src >= -_nc()._LAYER_ORDER_EPS) & (s_dst < -_nc()._LAYER_ORDER_EPS)
            if not np.any(flip):
                continue
            any_fix = True
            need = (s_src - s_dst)[flip]          # push back out to the authored offset
            vec = nb[flip] * need[:, None]
            vi = ai[flip]
            # (satisfying all of a vert's layer constraints jointly was measured to oscillate, not converge, and was removed -- refuted; see git history.)
            # Keep the LARGEST demanded push per vert (satisfies the worst
            # constraint; summing several would overshoot and bulge the shape).
            better = np.abs(need) > mag[ia][vi]
            if np.any(better):
                sel = vi[better]
                moves[ia][sel] = vec[better]
                mag[ia][sel] = np.abs(need)[better]
                corrected.update((ia, int(v)) for v in sel)
        if not any_fix:
            break
        for i, x in enumerate(L):
            nz = mag[i] > 0
            if not np.any(nz):
                continue
            # FEATHER the correction over the mesh before applying it. A raw per-vert
            # shove fixes the violating vert but leaves its untouched neighbours behind,
            # which IS a crinkle -- measured: the unsmoothed repair cut inverted verts
            # 3555->1828 but drove worst depth 2.28->3.04u and spikiness 5.38->6.09.
            # Averaging each vert's correction with its edge neighbours (the moved verts
            # pull their neighbourhood along) keeps the push field continuous, so the
            # layer comes back to the right side WITHOUT growing a spike. Same lesson as
            # the k=1 ride. #layer-order
            mv = moves[i]
            try:
                mv = _nc()._smooth_vertex_field(mv, x["tris"], iters=_nc()._LAYER_ORDER_SMOOTH,
                                         verts=x["dv"])
            except Exception as _pe:
                _note_pass_failure("_smooth_vertex_field", _pe)
            x["dv"] = x["dv"] + mv

    if not corrected:
        return 0
    for x in L:
        x["j"]["verts"] = x["dv"]
        x["j"]["verts_modified"] = True
    return len(corrected)

def _closest_point_on_tris(p, a, b, c):
    """Closest point on each triangle (a,b,c) to each point p, plus barycentrics.

    Vectorised over rows. Returns (q, v, w) with q = a + (b-a)v + (c-a)w, so the
    same (triangle, v, w) can be re-evaluated on a DIFFERENT set of vertex
    positions -- which is the whole point: it gives a correspondence that is fixed
    by the source and can be replayed on the output.
    """
    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum('ij,ij->i', ab, ap)
    d2 = np.einsum('ij,ij->i', ac, ap)
    bp = p - b
    d3 = np.einsum('ij,ij->i', ab, bp)
    d4 = np.einsum('ij,ij->i', ac, bp)
    cp = p - c
    d5 = np.einsum('ij,ij->i', ab, cp)
    d6 = np.einsum('ij,ij->i', ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-20, 1e-20, den)
    v = np.clip(vb / den, 0.0, 1.0)
    w = np.clip(vc / den, 0.0, 1.0)
    s = v + w
    over = s > 1.0
    sd = np.where(s == 0.0, 1.0, s)
    v = np.where(over, v / sd, v)
    w = np.where(over, w / sd, w)
    return a + ab * v[:, None] + ac * w[:, None], v, w

def _ride_disp_barycentric(sv, base_src, base_fin, base_tris, ride_max):
    """Displacement of the exact SURFACE POINT each rider vertex rests on.
    #layer-ride-barycentric

    WHY, measured. The vertex-blend ride moves a rider by the inverse-distance
    average of its 8 nearest reference VERTICES, so it follows the neighbourhood's
    average motion rather than the motion of the spot it is actually sitting on.
    Where the surface beneath moves unevenly -- which is exactly what the
    scale-uniform repair does to a strap -- the average is not the contact point,
    and the gap drifts. Reported in game three times as a buckle sinking into its
    leather; measured, a plate went from 0.124u below the strap (better than the
    author's 0.132u) to 0.171u once the strap moved under it.

    WHY THIS IS NOT THE k=1 RIDE, which was measured to ADD spikes (a pauldron's
    spikiness 2.92 -> 3.85). Snapping to the nearest VERTEX is discontinuous:
    adjacent riders pair to different verts whose displacements differ, so the
    rider inherits a jump. A barycentric point on a TRIANGLE is continuous -- slide
    the rider along the surface and the correspondence slides with it, and the
    displacement interpolated across a triangle is piecewise linear with no jump at
    the seams. So this is precise AND smooth, which is the combination the k=8
    blend was trading away.

    Correspondence is taken in the SOURCE and replayed on the output, so it encodes
    the authored contact and cannot be re-chosen by whatever the fit did.

    Returns (disp, dist): the displacement to apply per rider vertex and its
    distance to the reference surface in the source. Falls back to (None, None) if
    it cannot be computed, so the caller can use the blend instead.
    """
    try:
        from scipy.spatial import cKDTree
        t = np.asarray(base_tris, dtype=np.int64)
        if t.ndim != 2 or t.shape[1] != 3 or len(t) == 0:
            return None, None
        cen = base_src[t].mean(axis=1)
        k = int(min(max(_nc()._LAYER_RIDE_BARY_CAND, 1), len(t)))
        _d, cand = cKDTree(cen).query(sv, k=k)
        if k == 1:
            cand = cand[:, None]
        best_d = np.full(len(sv), np.inf)
        best_q = np.zeros_like(sv)
        best_f = np.zeros_like(sv)
        for ci in range(cand.shape[1]):
            ti = t[cand[:, ci]]
            a, b, c = base_src[ti[:, 0]], base_src[ti[:, 1]], base_src[ti[:, 2]]
            q, v, w = _closest_point_on_tris(sv, a, b, c)
            dist = np.linalg.norm(sv - q, axis=1)
            better = dist < best_d
            if not better.any():
                continue
            fa = base_fin[ti[:, 0]]
            fb = base_fin[ti[:, 1]]
            fc = base_fin[ti[:, 2]]
            # Same triangle, same barycentrics, evaluated on the FINAL positions.
            f = fa + (fb - fa) * v[:, None] + (fc - fa) * w[:, None]
            best_d[better] = dist[better]
            best_q[better] = q[better]
            best_f[better] = f[better]
        if not np.isfinite(best_d).any():
            return None, None
        return best_f - best_q, best_d
    except Exception as _re:
        _note_pass_failure("_ride_disp_barycentric", _re)
        return None, None

def _authored_layer_relation(entries, near=None, agree_min=0.70,
                             coincident_eps=1e-3, min_overlap=3):
    """The AUTHOR's over/under relation, {(outer, inner): confidence}.
    #authored-ride-order

    SPLIT OUT SO THERE IS ONE COPY. `pack_ride_order_census.py` had its own
    reimplementation of everything below, under a docstring promising "the same
    probe and the same thresholds ... so the two cannot drift". It drifted the
    moment this was fixed, and the census then scored the new ordering against a
    stale copy of the old relation -- crediting nothing and looking like a
    no-op. Any scorer must import THIS, never restate it.

    Replaces ranking by median distance to the body, which is one global scalar
    standing in for a question that is local and pairwise -- and which inverts
    on a perfectly ordinary piece. MEASURED on the reported bodysuit:

        Suit Plates       median 1.341u    774 verts  z[87.9, 94.9]  <- REFERENCE
        Suit              median 1.407u  15250 verts  z[63.7,116.8]  rides it

    A 774-vert bust plate covering seven units of height became the reference
    and the whole bodysuit rode it, because the plate happens to sit slightly
    tighter than the suit's median over its legs and arms. Everything above the
    reference is re-derived as `source_position + reference_displacement`, so
    that ranking decides whose fit survives.

    THE RELATION, not a scalar: for each pair, pair verts in the SOURCE and take
    the signed offset along the OTHER shape's SOURCE normals -- the same measure
    `_repair_layer_order` already uses, so the two cannot drift.

    BOTH DIRECTIONS, decided by COVERAGE. The evidence is asymmetric and taking
    one direction gets it wrong: on that piece `Suit -> Clavices Plates` reads
    "suit outside plate" at 66% agreement (1896 suit verts land near the little
    plate, including collar-BACK geometry with no real counterpart), while
    `Clavices Plates -> Suit` reads "plate outside suit" at 90%. The shape more
    fully covered by the other is the subset, and the subset is the one whose
    verts all have a genuine counterpart -- so it asks the meaningful question.

    COINCIDENT DUPLICATES tie instead of stacking. A median offset of ~0 over a
    full overlap is one surface authored twice (two materials), not a layer, and
    forcing an order on it makes one copy ride the other for no reason.

    The pairwise verdicts are then ORDERED by `_stack_depth_from_relation`, not
    counted -- see there for why counting contradicted an eighth of the verdicts
    this function had itself produced.

    Returns {index: depth}, depth 0 = innermost. Equal depth = same layer.
    """
    near = _LAYER_ORDER_NEAR if near is None else float(near)
    n = len(entries)
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return None
    prep = []
    for e in entries:
        v = np.asarray(e["sv"], np.float64)
        nr = e.get("sn")
        nr = None if nr is None else np.asarray(nr, np.float64)
        if nr is None or nr.shape != v.shape or not np.isfinite(nr).all() \
                or float(np.linalg.norm(nr, axis=1).max(initial=0.0)) < 1e-9:
            # Source normals ship all-zero often enough that trusting them
            # silently is how a comparison ends up reading 0.000 everywhere.
            nr = _nc()._vertex_normals_from_tris(v, e["tris"])
        if nr is None:
            return None
        prep.append((v, nr, cKDTree(v)))

    def probe(ia, ib):
        va, _, _ = prep[ia]
        vb, nb, tb = prep[ib]
        d, idx = tb.query(va)
        m = d < near
        if int(m.sum()) < min_overlap:
            return 0.0, None, None
        ai = np.where(m)[0]
        s = np.einsum('ij,ij->i', va[ai] - vb[idx[ai]], nb[idx[ai]])
        return float(m.mean()), float(np.median(s)), float((s > 0).mean())

    rel = {}
    for ia in range(n):
        for ib in range(ia + 1, n):
            cA, mA, gA = probe(ia, ib)
            cB, mB, gB = probe(ib, ia)
            if mA is None and mB is None:
                continue
            # `med` is a's offset measured along b's normals, so med > 0 reads
            # "a sits on b". Keep a and b named for what they are -- calling
            # them outer/inner before the verdict is decided is how the sign
            # branches below get read backwards.
            first = (mA is not None and (mB is None or cA >= cB))
            order = [(mA, gA, cA, ia, ib), (mB, gB, cB, ib, ia)]
            if not first:
                order.reverse()
            # COVERAGE PICKS THE DIRECTION, BUT IT DOES NOT GET A VETO. The
            # subset rationale above is a prior, not evidence, and it was
            # overriding evidence: on the reported piece the preferred direction
            # missed `agree_min` by two points (0.68 vs 0.70) while the other
            # direction agreed at 0.99, and the pair emitted NO edge at all.
            # A bra and the shirt over it were then ordered only through a
            # transitive path, which put the bra outside the shirt.
            #
            # So fall back to the other direction when the preferred one cannot
            # decide. Strictly additive: wherever the preferred direction
            # already decides, this changes nothing.
            for med, agr, cov, a, b in order:
                if med is None or abs(med) < coincident_eps:
                    continue                   # no overlap, or one surface twice
                # The weight is CONFIDENCE, not separation --
                # `_stack_depth_from_relation` uses it only to choose which edge
                # to sacrifice in a cycle, and how far apart two layers sit says
                # nothing about how sure we are of their order. Agreement times
                # coverage is what we actually know: how consistently the offset
                # pointed one way, over how much of the shape had a counterpart.
                if med > 0 and agr >= agree_min:
                    rel[(a, b)] = agr * cov              # a sits ON b
                    break
                if med < 0 and (1.0 - agr) >= agree_min:
                    rel[(b, a)] = (1.0 - agr) * cov      # b sits ON a
                    break
    return rel

def _authored_layer_depth(entries, near=None, agree_min=0.70,
                          coincident_eps=1e-3, min_overlap=3):
    """Stack depth per layer, from the authored relation. depth 0 = innermost.

    Returns None when the relation could not be built at all, which the caller
    must treat as "fall back to the old ranking", not as "no layers overlap".
    """
    rel = _authored_layer_relation(entries, near=near, agree_min=agree_min,
                                   coincident_eps=coincident_eps,
                                   min_overlap=min_overlap)
    if rel is None:
        return None
    return _stack_depth_from_relation(len(entries), rel)

def _stack_depth_from_relation(n, rel):
    """Order the pairwise verdicts into a stack depth. #authored-ride-order

    `rel` maps (outer, inner) -> CONFIDENCE in that verdict, in [0, 1]
    (agreement x coverage). It used to map to the authored SEPARATION, which is
    a length, and the difference decides which edge gets sacrificed below.

    LONGEST PATH, not a count. The first version did `depth[outer] += 1` -- how
    many layers each shape is outside of -- which is not an ordering of the
    relation at all, and MEASURED it contradicted an eighth of the verdicts the
    same function had just produced. Over 120 shipped multi-layer pieces
    carrying 1246 decided pairs:

        order                       pairs contradicted   pieces affected
        live median distance          437  (35.1%)        86 / 120
        depth as a COUNT              169  (13.6%)        51 / 120
        depth as LONGEST PATH          50  ( 4.0%)        20 / 120

    A count ties a shape that sits on five narrow trims with one that sits on a
    single dress, and then ignores a direct verdict between those two. Longest
    path cannot: a layer is placed strictly after everything it is authored to
    sit on.

    THE 4% RESIDUAL IS NOT SLACK -- it is exactly the 50 edges broken below, on
    exactly the 20 pieces whose relation contains a CYCLE. A sash that crosses
    over a coat at the front and tucks under it at the back genuinely has no
    consistent stack order, and no linear ranking can satisfy every verdict on
    such a piece. Where a cycle must be cut, cut it at the WEAKEST edge, by
    adding edges strongest-first and dropping any that would close a loop.

    WEAKEST MEANS LEAST CONFIDENT, NOT NARROWEST. This ranked by the authored
    separation and called it "the verdict we have least evidence for" -- but a
    separation is a LENGTH. A sliver that overlaps 10% of its neighbour with a
    wide gap outranked a verdict taken over the neighbour's whole surface at 99%
    agreement, and when the two conflicted it was the confident one that got
    dropped. Reported in game as an inner layer coming through an outer one.

    MEASURED, on the piece that was reported: a bra visibly clipping through the
    shirt over it. The relation held, correctly,

        3Belts       OUTSIDE 3Fabric    agreement 1.00 over 47% coverage
        3LeatherMain OUTSIDE 3Fabric    agreement 0.99 over 100% coverage

    and BOTH were discarded as cycle-closing, because a chain through a panty
    piece decided over TEN PERCENT coverage had been added first on the strength
    of a wider gap (1.196u vs 0.889u). The two outer layers landed at depths 0
    and 2 with the shirt at 4, so the shirt rode geometry the author puts on top
    of it and sank at the under-bust. Confidence ordering drops the 10% edge
    instead and both survive.
    """
    below = {i: [] for i in range(n)}          # below[x] = layers x sits on

    def _reaches(a, b, seen):
        if a == b:
            return True
        if a in seen:
            return False
        seen.add(a)
        return any(_reaches(x, b, seen) for x in below[a])

    for (outer, inner) in sorted(rel, key=lambda k: -rel[k]):
        if _reaches(inner, outer, set()):
            continue          # would close a cycle against stronger evidence
        below[outer].append(inner)

    depth = {}

    def _depth(i):
        if i in depth:
            return depth[i]
        depth[i] = 0          # re-entry guard; acyclic by construction above
        v = 0
        for j in below[i]:
            v = max(v, _depth(j) + 1)
        depth[i] = v
        return v

    for i in range(n):
        _depth(i)
    return depth

def _feather_ride_disp(disp, mask, tris, sv, stats=None):
    """Smooth the ride's displacement field WITHIN the ridden mask.

    Returns `disp` unchanged on any failure or when nothing is ridden -- never
    worse than not feathering.
    """
    try:
        m = np.asarray(mask, dtype=bool)
        d = np.asarray(disp, dtype=np.float64)
        if not m.any() or d.ndim != 2:
            return disp
        t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        w = m.astype(np.float64)[:, None]
        num = _nc()._smooth_vertex_field(d * w, t, iters=_nc()._RIDE_FEATHER_ITERS,
                                   verts=sv)
        den = _nc()._smooth_vertex_field(np.repeat(w, d.shape[1], axis=1), t,
                                   iters=_nc()._RIDE_FEATHER_ITERS, verts=sv)
        num = np.asarray(num, dtype=np.float64)
        den = np.asarray(den, dtype=np.float64)
        good = den > 1e-9
        out = d.copy()
        sm = np.where(good, num / np.where(good, den, 1.0), d)
        out[m] = sm[m]
        if stats is not None:
            moved = np.linalg.norm(out - d, axis=1)
            stats["ride_feather"] = (stats.get("ride_feather", 0)
                                     + int((moved > 1e-4).sum()))
            stats["ride_feather_max"] = max(
                stats.get("ride_feather_max", 0.0), float(moved.max()))
        return out
    except Exception as _pe:
        _note_pass_failure("_feather_ride_disp", _pe)
        return disp

def _ride_layers_on_reference(shape_jobs, body_verts=None,
                              ride_max: float = _LAYER_RIDE_MAX,
                              softbody_names=frozenset(),
                              collider_names=frozenset(),
                              body_norms=None, enable_panels=True,
                              stack=None):
    """Ride a multi-layer garment stack coherently so its layers can't cross.

    Operates on pass-1 `shape_jobs`. Ranks the eligible layers innermost-first by
    median distance to `body_verts`, then walks outward: layer i re-derives each
    vert from the FINAL position of the nearest SOURCE-paired vert among all layers
    already placed beneath it, keeping that vert's SOURCE offset. Returns the count
    of re-bound verts. Best-effort; a caller wraps it. See #layer-ride.

    Skips HDT soft-body / collider shapes (they keep their authored rest pose -- a
    ride would fight the sim) and non-identity-g2s shapes (source + final verts must
    share a frame for the offset to mean anything).
    """
    if not _nc().LAYER_RIDE_ENABLED or body_verts is None:
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    def _eligible(j):
        s = j["src"]
        nm = s.name or ""
        if nm in _nc().UBE_BODY_INJECT_NAMES or _nc()._is_inline_body_name(nm):
            return False
        if nm in softbody_names or nm in collider_names:
            return False
        if _nc()._shape_has_effect_shader(s):
            return False          # glow decals ride their plate separately
        if not (s.textures or {}):
            return False          # collision proxy
        try:
            if s.has_global_to_skin:
                g = _shape_global_to_skin(s)
                if not (g is None or _g2s_is_identity(g)):
                    return False
        except Exception:
            return False
        return True

    layers = [j for j in shape_jobs if _eligible(j)]
    if len(layers) < 2:
        return 0                  # nothing stacked -> nothing to ride

    btree = cKDTree(np.asarray(body_verts, dtype=np.float64))

    # Rank innermost-first by MEDIAN distance to the body (relative order is what
    # matters; median is robust to a few far verts on a trailing belt/strap).
    ranked = []
    for j in layers:
        try:
            sv = np.asarray(j["src"].verts, dtype=np.float64)
            fv = np.asarray(j["verts"], dtype=np.float64)
        except Exception:
            continue
        if sv.ndim != 2 or sv.shape != fv.shape or len(sv) == 0:
            continue
        d, _ = btree.query(sv)
        ranked.append((float(np.median(d)), j, sv, fv))
    if len(ranked) < 2:
        return 0
    ranked.sort(key=lambda t: t[0])

    # #authored-ride-order. Re-rank by the authored pairwise relation, keeping
    # the median as the tiebreak WITHIN a depth so shapes the author left
    # genuinely unordered (and coincident duplicates) keep a stable order.
    # Falls through to the median ranking if the relation cannot be built --
    # never silently, because a ride on the wrong reference is the defect this
    # exists to stop.
    if _nc().AUTHORED_RIDE_ORDER:
        try:
            entries = [{"sv": sv,
                        "sn": (np.asarray(j["src"].normals, np.float64)
                               if j["src"].normals is not None else None),
                        "tris": np.asarray(j["src"].tris,
                                           np.int64).reshape(-1, 3)}
                       for _d, j, sv, fv in ranked]
            rel = _authored_layer_relation(entries)
            depth = (None if rel is None
                     else _stack_depth_from_relation(len(entries), rel))
            if depth is None:
                _note_pass_failure(
                    "_authored_layer_depth",
                    RuntimeError("no relation built; kept median ranking"))
            else:
                # THE REFERENCE MUST NOT BE A LAYER THE AUTHOR PUTS ON TOP OF
                # ANOTHER. Depth alone does not guarantee it: where a cycle had
                # to be broken, the layer whose "sits on" edge was sacrificed
                # can land at depth 0, and depth 0 is the reference -- the only
                # layer that keeps its own fit, with every other layer
                # re-derived from it. Measured pack-wide, that happened on 9 of
                # 167 pieces.
                #
                # Demote such layers WITHIN their depth band. This is purely a
                # tiebreak: every surviving edge imposes a strict depth
                # inequality, so reordering inside one band cannot violate one.
                outer_of = {o for (o, _i) in rel}
                order = sorted(range(len(ranked)),
                               key=lambda i: (depth[i], i in outer_of,
                                              ranked[i][0]))
                ranked = [ranked[i] for i in order]
        except Exception as _pe:
            _note_pass_failure("_authored_layer_depth", _pe)

    n_rebound = 0
    _cst = {}        # this pass's own counters (#ride-feather)
    # Accumulated geometry already placed (innermost outward), in lockstep.
    ref_src = [ranked[0][2]]
    ref_fin = [ranked[0][3]]
    # ...and their TRIANGLES, re-indexed into the concatenated vertex array, so a
    # rider can be paired to the reference SURFACE and not just to its verts.
    ref_tris = []
    try:
        _t0 = np.asarray(ranked[0][1]["src"].tris, dtype=np.int64).reshape(-1, 3)
        ref_tris.append(_t0)
    except Exception:
        ref_tris.append(np.zeros((0, 3), dtype=np.int64))
    _voff = [len(ranked[0][2])]
    for _ri, (_d, j, sv, fv) in enumerate(ranked[1:], start=1):
        base_src = np.concatenate(ref_src)
        base_fin = np.concatenate(ref_fin)
        base_disp = base_fin - base_src          # the reference DISPLACEMENT field
        base_tris = (np.concatenate(ref_tris) if any(len(x) for x in ref_tris)
                     else np.zeros((0, 3), dtype=np.int64))
        tree = cKDTree(base_src)
        # SMOOTH the ride: take the k-nearest reference verts and inverse-distance
        # blend their DISPLACEMENT, rather than snapping to the single nearest.
        # A k=1 ride re-introduces the very artifact it's meant to remove --
        # adjacent rider verts can pair to DIFFERENT reference verts whose
        # displacements differ, so the rider inherits a discontinuity and spikes
        # (MEASURED: k=1 raised a shoulder pauldron's spikiness 2.92 -> 3.85 and a
        # cord's 4.78 -> 5.35 even while it fixed the stack's coherence). Blending
        # gives a continuous field, so the layer moves WITH what's beneath it
        # smoothly. #layer-ride
        k = int(min(_nc()._LAYER_RIDE_K, len(base_src)))
        d, idx = tree.query(sv, k=k)
        if k == 1:
            d = d[:, None]
            idx = idx[:, None]
        disp = None
        if _nc().LAYER_RIDE_BARY and len(base_tris):
            # Follow the surface POINT, not a blend of nearby verts. Continuous,
            # so it does not reintroduce the k=1 spikes. #layer-ride-barycentric
            disp, bdist = _ride_disp_barycentric(
                sv, base_src, base_fin, base_tris, ride_max)
            if disp is not None:
                mask = bdist <= ride_max
        if disp is None:
            w = 1.0 / (d + 1e-6)
            w /= w.sum(axis=1, keepdims=True)
            disp = (base_disp[idx] * w[:, :, None]).sum(axis=1)
            mask = d[:, 0] <= ride_max
        if np.any(mask):
            cur = fv.copy()
            # Move the rider by the (smoothly interpolated) displacement of the
            # geometry beneath it -> its SOURCE offset to that geometry is preserved.
            _rd = disp
            _apply = mask
            if _nc().PANEL_RIGID_RIDE and enable_panels:
                _st = {}
                _rd, _force = _panel_rigid_disp(
                    sv, j["src"].tris, disp, mask,
                    _nc().PANEL_RIGIDITY_MIN_VERTS,
                    body_v=body_verts, body_n=body_norms, stats=_st,
                    base_v=fv, stack=stack, self_name=j["src"].name)
                if _force is not None and np.any(_force):
                    # A rigidified panel moves WHOLE. Assigning only `mask` here
                    # would leave its un-ridden tenth behind at a per-vertex fit
                    # and reintroduce the step this exists to remove.
                    _apply = mask | _force
                if _st.get("seen"):
                    import sys as _sys
                    print(f"    [panel-ride] {j['src'].name}: "
                          f"{_st.get('flat', 0)} of {_st['seen']} panel(s) "
                          f"ridden rigidly ({_st.get('partial', 0)} only "
                          f"partly ridden), {_st.get('fitted', 0)} fitted "
                          f"clear, {_st.get('no_push', 0)} already clear, "
                          f"{_st.get('declined', 0)} DECLINED"
                          + (f" ({_st.get('declined_layer', 0)} for crossing "
                             f"another layer, worst deepen "
                             f"{_st['declined_worst']:.2f}u)"
                             if _st.get("declined") else "")
                          + f", stack {_st.get('stack', 0)}"
                          + (f", worst pair -> {_st['worst_pair'][0]} "
                             f"+{_st['worst_pair'][1]}"
                             if _st.get("worst_pair") else "")
                          + (f"  ** DISCARDED: {_st['error']} **"
                             if _st.get("error") else "")
                          + (", NO BODY" if _st.get("no_body") else "")
                          + (f", min clearance {_st['min_clear']:.2f}u"
                             if "min_clear" in _st else ""),
                          file=_sys.stderr)
            if _nc().RIDE_FEATHER:
                _rd = _feather_ride_disp(_rd, _apply, j["src"].tris, sv, _cst)
            # #ride-body-floor: clamp the INWARD component of the ride so this
            # pass cannot put cloth into the body. Applied after the feather so
            # nothing downstream re-introduces what it removes.
            if _nc().RIDE_BODY_FLOOR and body_verts is not None and body_norms is not None:
                try:
                    _cand = sv + _rd
                    _bvv = np.asarray(body_verts, dtype=np.float64)
                    _bnn = np.asarray(body_norms, dtype=np.float64)
                    _, _bj = btree.query(_cand[_apply])
                    _n = _bnn[_bj]
                    _sd = np.einsum('ij,ij->i', _cand[_apply] - _bvv[_bj], _n)
                    # Never deeper than the FIT CHAIN already had this vert:
                    # a vert it left inside stays where it was rather than being
                    # hauled out by a pass that is not the anti-poke.
                    _, _fj = btree.query(fv[_apply])
                    _fd = np.einsum('ij,ij->i', fv[_apply] - _bvv[_fj],
                                    _bnn[_fj])
                    _floor = np.minimum(_fd, 0.0) - 1e-4
                    _short = np.clip(_floor - _sd, 0.0, None)
                    if float(_short.max(initial=0.0)) > 0.0:
                        _fix = np.zeros_like(_rd)
                        _fix[_apply] = _n * _short[:, None]
                        _rd = _rd + _fix
                        _cst["body_floor"] = _cst.get("body_floor", 0) + int(
                            (_short > 0.0).sum())
                except Exception as _rbe:
                    _note_pass_failure("ride/body-floor", _rbe)
            cur[_apply] = sv[_apply] + _rd[_apply]
            j["verts"] = cur
            j["verts_modified"] = True
            n_rebound += int(_apply.sum())
            fv = cur
        ref_src.append(sv)
        ref_fin.append(fv)
        try:
            _tj = np.asarray(j["src"].tris, dtype=np.int64).reshape(-1, 3)
            ref_tris.append(_tj + int(np.sum(_voff)))
        except Exception:
            ref_tris.append(np.zeros((0, 3), dtype=np.int64))
        _voff.append(len(sv))
    if _cst.get("ride_feather"):
        import sys as _sys
        print(f"  [ride-feather] smoothed {_cst['ride_feather']} vert(s) of "
              f"ride displacement (largest change "
              f"{_cst.get('ride_feather_max', 0.0):.3f}u)", file=_sys.stderr)
    return n_rebound

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
    if _flag("CBBE2UBE_NO_GLOW_RIDE", False):
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0

    overlays = [j for j in shape_jobs
                if _nc()._shape_has_effect_shader(j["src"]) and _nc()._shape_has_identity_g2s(j["src"])]
    plates = [j for j in shape_jobs
              if not _nc()._shape_has_effect_shader(j["src"]) and _nc()._shape_has_identity_g2s(j["src"])]
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
