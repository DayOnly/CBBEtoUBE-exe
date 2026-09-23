"""Skinning and weight passes: the body-blend reskin, scale-bone graft, slot-aware band/reach, the limb / leg / arm / spine / spine-twist / full-weight motion matches, the rigid leg-bend match, the coincident and seam skin matches, the weight conform, the roughness cap, the SMP boundary hold, the weight-partner jiggle sync, the body-jiggle transfer, the bone predicates, and the row cap / renormalise / zero-fill helpers.

Split out of nif_convert.py on 2026-09-01 (split step 8). Imported by name into
nif_convert, so every `nc.<name>` handle keeps working. Names that stayed in
nif_convert are reached through `_nc()` at CALL time, so flags read at
import, tests' monkeypatches on `nc` and `importlib.reload(nc)` all still
apply."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import os
import sys

from . import fit_metrics, nif_io
from .atomic_io import atomic_nif_save
from .envflags import flag as _flag, knob as _knob
from .nif_convert_bodyrefs import _find_ube_femalebody  # noqa: E402
from .nif_convert_bust import _chest_follow_target  # noqa: E402
from .nif_convert_layers import (  # noqa: E402
    _canonical_stack_name_groups,
    _stacked_layer_groups,
    _layered_cloth_shape_names,
)
from .nif_convert_skinframe import (  # noqa: E402
    _g2s_is_identity,
    _shape_global_to_skin,
    _verts_skin_to_world,
)
from .nif_convert_telemetry import (  # noqa: E402
    _note_pass_failure,
    _note_pass_effect,
)
from .nif_convert_trigen import _source_morph_tri_shape_names  # noqa: E402


def _nc():
    """The monolith, resolved at call time (never at import: circular)."""
    return sys.modules[__package__ + ".nif_convert"]


def _slot_aware_scale_bone_reach(biped_slots: int) -> float:
    """Pick the scale-bone weight propagation radius for an armor shape
    based on its biped slot bitfield. Slot 49 cloth (skirts, loincloths,
    hanging tabards) gets the extended reach (`SCALE_BONE_REACH_SLOT49`,
    ~25u) so hem verts 15-25u from the body still pick up scale-bone
    weight and grow proportionally with body morphs at runtime.
    Other slots use the standard 12u reach. See `SCALE_BONE_REACH_SLOT49`
    docstring for the full rationale.
    """
    if biped_slots & _nc().BIPED_SLOT49_BIT:
        return _nc().SCALE_BONE_REACH_SLOT49
    return SCALE_BONE_REACH

def _adjust_skin_to_bone_baked(xf, bake_T):
    """Given a skin-to-bone TransformBuf `xf` and the geometry-transform matrix
    `bake_T` being baked into the verts, return the adjusted TransformBuf
    (full(xf) @ inv(bake_T)) so STB'@(bake_T@v) == STB@v (bind preserved)."""
    pyn = _nc()._pynifly()
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
    pyn = _nc()._pynifly()

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

def _body_breast_motion_weight(shape) -> "np.ndarray | None":
    """Per-vertex share of the body driven by ANY breast bone -- the map of
    where the bust actually TRAVELS under physics.

    Deliberately not `_body_nipple_weight`, which is Breast03/02 only and by its
    own docstring reads ~0 on "the sternum, SIDES and upper chest". The side of
    the breast is exactly where a re-hugged garment gets punched through when
    the bust swings (reported in game, walking, 2026-08-13), so a tip-weighted
    map cannot protect it. Sums every breast bone instead: Breast01 carries the
    root/flank travel that 02 and 03 do not.

    Returns a (V,) array clipped to [0,1], or None if the body has no breast
    bones at all (a static body has nothing to swing, so the caller no-ops).
    """
    try:
        n = len(shape.verts)
    except Exception:
        return None
    bw = getattr(shape, "bone_weights", None) or {}
    out = np.zeros(n, dtype=np.float64)
    found = False
    for bn, pairs in bw.items():
        if "breast" not in (bn or "").lower() or pairs is None:
            continue
        found = True
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        for i, w in pl:
            if 0 <= i < n:
                out[i] += float(w)
    return np.clip(out, 0.0, 1.0) if found else None

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

RESKIN_NEAR_DIST = 0.5

RESKIN_FAR_DIST = 2.0

RESKIN_K = 4

def _slot_aware_reskin_band(biped_slots: int) -> "tuple[float, float]":
    """Return (near_dist, far_dist) for the body-weight conformance blend,
    widened for body-fitted slots so the armor deforms with the body during
    animation (kills body-poke-through-during-movement). Slot-49-only flowing
    cloth and slot-less armor keep the narrow default so they still drape."""
    if biped_slots & _nc()._RESKIN_BODY_FITTED_BITS:
        # #reskin-wide: only the BODY-FITTED branch widens. Slot-49 flowing cloth
        # keeps the narrow band deliberately -- over-conforming a skirt makes it
        # cling instead of drape, which is a different defect, not a fix.
        return (_nc().RESKIN_NEAR_DIST_BODYFIT, _nc().RESKIN_FAR_DIST_BODYFIT)
    return (RESKIN_NEAR_DIST, RESKIN_FAR_DIST)

def _is_physics_jiggle_scale_bone(bone_name: str) -> bool:
    low = bone_name.lower()
    return any(kw in low for kw in _nc().PHYSICS_JIGGLE_SCALE_KEYWORDS)

def _jiggle_region_of(bone_name: str) -> "str | None":
    """Which jiggle REGION a bone belongs to, or None if it is not a jiggle bone.

    The boolean above answers "does this shape jiggle at all", which is the wrong
    question wherever the regions are independent: a garment can carry breast
    weight and no butt weight, and pooling them let the second hide behind the
    first for the entire life of the project. #region-jiggle-gate
    """
    low = bone_name.lower()
    for kw in _nc().PHYSICS_JIGGLE_SCALE_KEYWORDS:
        if kw in low:
            return kw
    return None

def _is_leg_rigid_bone(bone_name: str) -> bool:
    if _is_scale_bone(bone_name):
        return False  # frontthigh/rearthigh/rearcalf are scale bones, not rigid
    low = bone_name.lower()
    return any(kw in low for kw in _nc().LEG_RIGID_BONE_KEYWORDS)

# Scale-bone reach: world units beyond which no scale weight is added.
# 12u covers the torso including the belly-to-corset gap (Z ~92-94 to Z<=87.5).
SCALE_BONE_REACH = 12.0

SCALE_BONE_K = 8

# Max fraction of a vert's total weight that can become scale-bone influence.
# Hand-built UBE soft-body cloth tops out ~0.76; 0.65 leaves 35% on rigid bones
# for animation and avoids extreme cross-region bleed.
SCALE_BONE_MAX_TRANSFER = 0.65

def _is_scale_bone(bone_name: str) -> bool:
    low = bone_name.lower()
    return any(kw in low for kw in _nc().SCALE_BONE_KEYWORDS)

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
    if name.startswith(_nc()._SKELETON_BONE_PREFIXES):
        return True
    low = name.lower()
    return any(kw in low for kw in _nc()._SKELETON_BONE_KEYWORDS)

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
    return any(k in n for k in _nc()._SOFT_BODY_PHYSICS_BONE_KEYWORDS)

def _is_genital_anatomy_bone(name: str) -> bool:
    """True for genital anatomy bones (clitoral/pussy/vagina/anus/...). These do
    NOT exist on the UBE body skeleton, so a converted armor that carries SOURCE
    weights to them has those verts resolve to the ORIGIN at runtime -> they
    spike through the floor (wolf Greaves: 8 verts at ~13% Clitoral1 over ~84%
    Pelvis were still visibly pulled to the ground). Narrow on purpose: does NOT
    match breast/butt/belly (those ARE bone-driven on UBE)."""
    n = (name or "").lower()
    return any(k in n for k in _nc()._GENITAL_ANATOMY_KEYWORDS)

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
    for v in sorted(affected):  # sorted: #deterministic-set-iteration
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
    for bn in sorted(_bones_iter):  # sorted: #deterministic-set-iteration
        pairs = [(int(i), float(w)) for i, w in weights_map.get(bn, [])
                 if int(i) not in affected]
        for v in sorted(affected):  # sorted: #deterministic-set-iteration
            nw = norm[v].get(bn)
            if nw and nw > 0.0:
                pairs.append((v, nw))
        if pairs:
            out[bn] = pairs
    return out

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
    for v in sorted(affected):  # sorted: #deterministic-set-iteration
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
        for v in sorted(affected):  # sorted: #deterministic-set-iteration
            nw = norm[v].get(bn)
            if nw and nw > 0.0:
                pairs.append((v, nw))
        if pairs:
            out[bn] = pairs
    return out

def _is_breast_bone(name: str) -> bool:
    return "breast" in (name or "").lower()

def _cap_weights_map(weights_map, n_verts: int):
    """Cap a {bone: [(vert, weight), ...]} map so no vertex carries more than 4
    influences, renormalising the survivors to 1.0. Returns a NEW map; bones left
    with no weight are dropped entirely, so a caller that derives its add_bone list
    from the result cannot add a bone the cap emptied.

    Authored SMP skins must NEVER be passed here -- their zero-weight XML constraint
    bones are load-bearing (dropping them collapses the skirt). #skin-influence-cap"""
    vw = [dict() for _ in range(n_verts)]
    for bn, pairs in (weights_map or {}).items():
        for vi, w in pairs:
            iv = int(vi)
            if 0 <= iv < n_verts:
                vw[iv][bn] = vw[iv].get(bn, 0.0) + float(w)
    writable = {bn for bn in (weights_map or {})}
    _cap_and_renormalise_rows(vw, n_verts, writable)
    out: dict = {}
    for i in range(n_verts):
        for bn, w in vw[i].items():
            if w > _nc()._WRITE_MIN:
                out.setdefault(bn, []).append((i, w))
    # Preserve the caller's bone ordering for the bones that survived.
    return {bn: out[bn] for bn in (weights_map or {}) if bn in out}

def _cap_and_renormalise_rows(vw, n, writable, rows=None,
                              incumbents=None) -> None:
    """Cap each row to the 4 largest influences among `writable`, then renormalise
    the survivors to exactly 1.0. Mutates `vw` (a list of {bone: weight} dicts).

    Why both, in this order: the NIF format holds 4 influences per vertex, and the
    save resolves an overflow ITSELF -- keeping the largest 4 and NOT renormalising
    (measured), so an overflowing row ships light and the vertex is transformed by a
    deflated sum of its bone matrices. Choosing the survivors here makes what lands
    deterministic, and the renormalise repairs both the truncation and any mass a
    caller's own fold discarded.

    `rows`: restrict to these vertex indices (the ones a pass actually touched).
    None means every vertex -- only correct for a caller that rewrites the whole
    shape. A row whose writable mass is already zero is left ALONE, not zeroed: an
    unweighted vertex skins to the origin, which is a visible spike.

    #last-carrier-hold. `incumbents` = the bones each row ALREADY HELD before
    the calling pass touched it, one set per vertex. A row may not evict an
    incumbent whose LAST carrier it is in order to seat a bone that row did not
    have; between two incumbents, weight decides exactly as before. Omit it and
    this function is byte-identical to its old self.

    THAT NARROWNESS IS THE DESIGN, and the first attempt was wider and wrong. A
    version that simply preferred last carriers for the smallest surviving slot
    -- never displacing a dominant bone, so it looked safe -- changed 2771 weight
    rows on a 184-NIF population, 2643 of them on ONE SMP collider shape, worst
    per-influence delta 0.849. The mechanism is a CASCADE: flipping which bone
    survives a cap changes whether a vertex is in a LATER pass's matched set, and
    that pass then re-derives the whole row from the body. Reordering two bones
    the row already had is never a local edit. Refusing a NEWCOMER a slot is.

    NEWCOMER IS PER ROW, not per shape, and the second attempt got that wrong
    too: passing the pass's graft list fixed nothing, because the bone doing the
    displacing (`NPC R Butt`) was already in the shape's bone list and merely new
    to THAT VERTEX. This is the same rule `#family-weight-invariant` states --
    keep the influences a vertex already HAS, spend only free slots on newcomers
    -- applied at the cap instead of inside one pass.

    WHY, and it is measured, not reasoned. A bone the AUTHOR held on ONE vertex
    is by construction the lightest influence there, so "keep the largest four"
    evicts it and the bone is left in the shape's bone list with no weight at
    all: `#zeroweight-bone-desync`, the equip-CTD class. Bisected on the piece
    that ships it -- `CBBE2UBE_NO_LEG_BEND_MATCH=1` gives `SkirtBBone02` back its
    single authored row (v757, 0.03467) and takes the shape to 0 zero-weight
    bones; at defaults it is evicted by `NPC R Butt` at 0.04747, a bone the same
    pass GRAFTS and which carries weight on plenty of other vertices.

    DECLINING TO WRITE DOES NOT PROTECT IT, which is why the caller's own "NEVER
    EMPTY A BONE" guard could not. `setShapeWeights` MERGES and the SAVE resolves
    an overflowing row itself, so leaving the stale 0.03467 in the file while
    writing a fifth influence over it just moves the eviction to save time. The
    only move that saves the bone is refusing the newcomer the slot.
    """
    idxs = range(n) if rows is None else rows
    hold = bool(incumbents) and _nc().LAST_CARRIER_HOLD
    # Live rows per bone across the WHOLE shape, not just `rows`: a bone with
    # weight on a vertex this pass never touched is not being stranded, so it
    # must not be protected.
    carriers: "dict[str, int]" = {}
    if hold:
        for i in range(n):
            for b, w in vw[i].items():
                if b in writable and w > _nc()._WRITE_MIN:
                    carriers[b] = carriers.get(b, 0) + 1
    for i in idxs:
        live = {b: w for b, w in vw[i].items()
                if b in writable and w > _nc()._WRITE_MIN}
        if len(live) > _nc()._SKIN_MAX_INFLUENCES:
            # TOTAL ORDER, not weight alone. `sorted` is stable, so EQUAL weights
            # fell back to `live`'s insertion order -- which traces back through
            # `vw` to sets iterated upstream, making the survivor depend on
            # PYTHONHASHSEED. Symmetric bones tie EXACTLY: measured on a fitted
            # dress, `L Breast02` and `R Breast02` both 0.003428 on vertex 6816,
            # and which survived flipped between seeds. Breaking the tie on the
            # bone NAME settles the whole class here instead of chasing every
            # producer that feeds `vw`.  #deterministic-set-iteration
            order = sorted(live, key=lambda b: (-live[b], b))
            kept = order[:_nc()._SKIN_MAX_INFLUENCES]
            dropped = order[_nc()._SKIN_MAX_INFLUENCES:]
            if hold:
                # Seat every stranded incumbent that a NEWCOMER is displacing,
                # cheapest newcomer first. Walking `dropped` in weight order and
                # `kept` from the back keeps the whole thing a total order.
                had = incumbents[i]
                for pos, b in enumerate(dropped):
                    if b not in had or carriers.get(b, 0) != 1:
                        continue       # only an INCUMBENT is ever held
                    give = next((j for j in range(len(kept) - 1, -1, -1)
                                 if kept[j] not in had), None)
                    if give is None:
                        break          # nothing left that this rule may unseat
                    dropped[pos], kept[give] = kept[give], b
            keep = set(kept)
            # A bone this row just lost is one row closer to being stranded, so
            # the count has to follow the cap -- otherwise a bone holding TWO
            # vertices is evicted from both, each time looking safe.
            for b in dropped:
                carriers[b] = max(0, carriers.get(b, 0) - 1)
            for b in list(vw[i]):
                if b in writable and b not in keep:
                    vw[i][b] = 0.0
            live = {b: w for b, w in live.items() if b in keep}
        tot = sum(live.values())
        if tot > _nc()._WRITE_MIN and abs(tot - 1.0) > 1e-6:
            for b in live:
                vw[i][b] /= tot

_LEG_BEND_MASS_MIN = _knob("CBBE2UBE_LEG_BEND_MASS_MIN", 0.15)

_BUTT_JIGGLE_CAP = _knob("CBBE2UBE_BUTT_JIGGLE_CAP", 0.15)  # max grafted jiggle/bone (subtle)

_CHEST_ANCHOR = "NPC Spine2 [Spn2]"                                 # breast graft anchor (plate has it)

_CHEST_JIGGLE_CAP = _knob("CBBE2UBE_CHEST_JIGGLE_CAP", 0.15)  # LOW total cap: metal stays rigid

def _chest_match_strength(z: float) -> float:
    """Trapezoid chest-graft strength by world Z: 0 outside [_CHEST_Z_LO, _CHEST_Z_HI],
    ramping in/out over _CHEST_RAMP so the neck/clavicle above and the belly below are never
    touched. Peak = _CHEST_JIGGLE_STRENGTH. Pure."""
    if z <= _nc()._CHEST_Z_LO or z >= _nc()._CHEST_Z_HI:
        return 0.0
    r = max(1e-6, _nc()._CHEST_RAMP)
    up = (z - _nc()._CHEST_Z_LO) / r
    down = (_nc()._CHEST_Z_HI - z) / r
    return min(1.0, _nc()._CHEST_JIGGLE_STRENGTH) * max(0.0, min(1.0, up, down))

def _butt_match_strength(z: float) -> float:
    """Trapezoid butt-rebalance strength by world Z: 0 outside [_BUTT_Z_LO, _BUTT_Z_HI],
    ramping 0 -> _BUTT_STRENGTH over _BUTT_RAMP at the bottom, flat across the glutes,
    ramping back to 0 at the top (so the lower back/spine is never touched). Pure."""
    if z <= _nc()._BUTT_Z_LO or z >= _nc()._BUTT_Z_HI:
        return 0.0
    peak = _nc()._BUTT_STRENGTH
    r = max(1e-6, _nc()._BUTT_RAMP)
    up = (z - _nc()._BUTT_Z_LO) / r            # 0..1 over the bottom ramp
    down = (_nc()._BUTT_Z_HI - z) / r          # 0..1 over the top ramp
    return peak * max(0.0, min(1.0, up, down))

def _body_jiggle_ref(weight: str):
    """({jiggle_bone: skin_to_bone_xform}, body_g2s_is_identity) for the UBE body
    at `weight`, or None. The jiggle bones (butt/belly/breast) are the body-only
    bones a fitted garment lacks; their bind transforms let us graft them onto a
    hugging garment WITHOUT a spike (a bone added with no STB skins to the origin
    -- the _install_skin / audit-#4 class). Only valid when the body's
    global-to-skin is identity (then the STB copies straight to an identity-g2s
    garment); the caller skips the graft otherwise. Cached."""
    if weight in _nc()._BODY_JIGGLE_REF_CACHE:
        return _nc()._BODY_JIGGLE_REF_CACHE[weight]
    out = None
    try:
        p = _find_ube_femalebody(weight) or _find_ube_femalebody("_1")
        if p is not None and Path(p).is_file():
            pyn = _nc()._pynifly()
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
            _nc()._BODY_JIGGLE_NF_KEEPALIVE.append(nf)   # keep native STBs valid
            out = (stbs, body_ident)
    except Exception:
        out = None
    _nc()._BODY_JIGGLE_REF_CACHE[weight] = out
    return out

def _jiggle_transfer_vert(dv: dict, bd_jig: dict, closeness: float,
                          factor: float):
    """Pure per-vert jiggle graft (extracted for testability). Give the garment
    vert a jiggle weight of `factor * closeness * body_weight` on each of the body
    vert's jiggle bones `bd_jig`, and scale the vert's existing (leg) weight down
    to fill the remainder so the weights still sum to 1. The grafted jiggle is thus
    a REAL share of the vert's skinning -- it follows the body's jiggle at a
    comparable amplitude -- not a token amount renormalization would shrink away.

    REINFORCES as well as grafts (#jiggle-reinforce): the final weight on each
    target bone is max(existing, target) -- raise, never lower. The old refusal
    here ("only reinforces existing jiggle -> leave it") was the presence-is-not-
    follow fallacy in miniature: a vert carrying an authored TOKEN weight (a
    name-copy at 1-5% of the body's drive) on every target bone was refused, and
    the (0, 0.1] band was claimed by NEITHER this pass NOR the jiggle-gated
    conform -- the measured 0.154-follow garment class. Idempotent: a vert
    already at/above target on every bone returns None, so re-runs no-op.

    Returns (new_weights, added) where `added` is the set of target bones the
    vert did NOT previously carry -- EMPTY for a pure reinforcement -- or
    (None, set()) when nothing changes (no body jiggle here, far vert, all
    targets negligible, or already at target)."""
    if not bd_jig or closeness <= 0.0:
        return None, set()
    targets = {jb: wb * factor * closeness for jb, wb in bd_jig.items()}
    targets = {jb: t for jb, t in targets.items() if t > 1e-3}
    if not targets:
        return None, set()
    if all(float(dv.get(jb, 0.0)) >= t - 1e-3 for jb, t in targets.items()):
        return None, set()       # already follows at target -> idempotent no-op
    final = {jb: max(float(dv.get(jb, 0.0)), t) for jb, t in targets.items()}
    added = {jb for jb in targets if jb not in dv}
    tot = sum(final.values())
    if tot >= 0.95:              # never let the graft dominate the vert
        sc = 0.95 / tot
        final = {jb: t * sc for jb, t in final.items()}
        tot = sum(final.values())
    remain = max(0.0, 1.0 - tot)
    base_sum = sum(w for b, w in dv.items() if b not in final)
    new: dict = {}
    if base_sum > 0:
        for b, w in dv.items():
            if b not in final:
                new[b] = w / base_sum * remain
    for jb, t in final.items():
        new[jb] = new.get(jb, 0.0) + t
    new = {b: w for b, w in new.items() if w > 1e-4}
    if not new:
        return None, set()
    return new, added

def _conform_fitted_to_body(dst_path, src_path=None, biped_slots: int = 0) -> int:
    """Fitted-cloth FINALIZE pass -- ONE load + one save for BOTH the weight-conform
    (skin-tight garments hug the UBE body's per-vert skinning) AND the self-intersection
    repair (un-cross warp-introduced torso layer clips). Both move the SAME shapes, so
    they share the loaded NIF and commit together: the relaxed verts + the conform
    weight edits go out in one _reauthor_nif_fresh (no second load / re-author). Each
    sub-pass keeps its own gate -- CONFORM_FITTED_CLOTH and SELFINT_REPAIR. Returns the
    verts conformed (weight side)."""
    hands_feet = bool(biped_slots & (_nc().BIPED_SLOT33_BIT | _nc().BIPED_SLOT37_BIT))
    do_conform = _nc().CONFORM_FITTED_CLOTH and not hands_feet
    do_selfint = _nc().SELFINT_REPAIR and not hands_feet and src_path is not None
    if not do_conform and not do_selfint:
        return 0
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    total = 0
    dirty = False
    if do_conform:
        # Shapes driven by a source BodySlide morph TRI keep their source skin
        # (see _conform_weights_core docstring + the reskin's morph-TRI gate).
        morph_tri_names = (_source_morph_tri_shape_names(Path(src_path))
                           if (src_path and _nc().RESKIN_PREFER_SOURCE_WHEN_MORPH_TRI)
                           else frozenset())
        # BUT only for a SINGLE-armor-layer piece (a lone bra, where the conform's
        # body-blend can misfire onto Spine2 and make it rigid). A MULTI-LAYER
        # stack (corset + blouse + belts...) NEEDS the conform: it makes every
        # layer deform WITH the body so they stay separated under idle -- keeping
        # their independent source skins makes the layers drift and CLIP into each
        # other (measured regression on a layered top). #morphtri-conform-multilayer
        n_armor = sum(1 for s in nf.shapes
                      if s.name not in _nc().UBE_BODY_INJECT_NAMES)
        if n_armor > 1:
            morph_tri_names = frozenset()
        dirty, total = _conform_weights_core(nf, dst_path, weight, morph_tri_names)
    overrides = _nc()._selfint_overrides(nf, dst_path, src_path) if do_selfint else {}
    if overrides:
        # ONE re-author commits the relaxed verts AND (from the in-memory nf) the
        # conform weight edits, recomputing normals for the moved verts. Re-assert the
        # VirtualBody Hidden bit first so a re-save can't surface the blue body double.
        _nc()._hide_virtual_body(nf)
        ok = False
        try:
            ok = _nc()._reauthor_nif_fresh(Path(dst_path),
                                     override_verts_by_name=overrides, nif=nf)
        except Exception:
            ok = False
        # _reauthor RETURNS False on its real failure modes (dropped shape / write
        # error) rather than raising, so branch on the result: if the re-author didn't
        # commit, at least save the conform weight edits (the native buffer already
        # holds them) so they aren't lost along with the self-int verts.
        if not ok and dirty:
            try:
                atomic_nif_save(nf, dst_path)
            except Exception as _pe:
                _note_pass_failure("atomic_nif_save", _pe)
    elif dirty:
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            # See _match_rigid_leg_bend_to_body: `return 0` already means "no
            # rows matched", so a swallowed save reads as a clean no-op.
            _note_pass_failure("_conform_fitted_to_body/save", _se, dst_path)
            return 0
    return total

def _conform_weights_core(nf, dst_path, weight,
                          morph_tri_names: "frozenset[str] | set[str]" = frozenset()
                          ) -> "tuple[bool, int]":
    """The fitted-cloth weight-conform loop on an already-loaded nf. Returns
    (dirty, verts_conformed). See CONFORM_FITTED_CLOTH for the detection gates --
    rigid plate armor is excluded and stays rigid.

    `morph_tri_names`: shapes driven by a SOURCE BodySlide morph TRI keep their
    STABLE source skin (same reason the M6 reskin excludes them, ~12257):
    rebuilding the skin desyncs that TRI so the shape stops inflating to match a
    morphed body AND, for a breast-jiggle bra, the body-weight blend pulls weight
    off the Breast bones onto Spine2 -> the bra goes rigid and no longer follows
    the breasts under physics (distorted/spiky). Skip them here too. #morphtri-conform"""
    ref = _nc()._body_conform_ref(weight)
    if ref is None:
        return False, 0
    _Vb, body_w, _body_bones, tree = ref  # chain test uses _is_skeleton_bone now
    # #covered-skin-target, second site: see _match_rigid_leg_bend_to_body.
    _body_nrm = (_nc()._body_conform_normals(weight)
                 if _nc().COVERED_SKIN_TARGET else None)
    # Precise SMP-collider exclusion. The _CONFORM_SKIP_NAMES substring gate below
    # only catches name-tagged colliders ("...Col..."); re-weighting an UNTAGGED
    # per-triangle collider would re-introduce the exact over-graft the reskin pass
    # is careful to avoid (the collider over-jiggles -> the cloth it stabilises
    # implodes / sinks). Read the collider set straight from the already-open nf so
    # there is NO second disk parse per armor. #smp-collider-graft
    collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    # Lazy: with the gate OFF this pass must do NO extra work at all.
    _skip_keys = _nc()._conform_skip_keys(
        _nc()._piece_has_hdt_xml(dst_path, nif=nf) if _nc().DRAPE_SKIP_XML_GATED else None)
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        if (s.name in collider_names or s.name in softbody_names
                or s.name in layered_cloth_names
                or s.name in morph_tri_names
                or any(k in nm for k in _skip_keys)):
            continue
        bw = s.bone_weights or {}
        # (a) CHEAP pre-gate: real soft-body jiggle weight -> deform-with-body
        # garment. Avoids building per-vert maps for rigid plate armor.
        jig = 0
        for b, pairs in bw.items():
            if _is_physics_jiggle_scale_bone(b):
                jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
                if jig >= _nc()._CONFORM_MIN_JIGGLE_VERTS:
                    break
        if jig < _nc()._CONFORM_MIN_JIGGLE_VERTS:
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
        if chain_frac > _nc()._CONFORM_CHAIN_MAX:
            continue
        g2s = _shape_global_to_skin(s)
        Vw = _verts_skin_to_world(V, g2s)
        d, idx = tree.query(Vw)
        # (c) HUGS the body (a flaring skirt/robe sits away -> excluded)
        if float((d < _nc()._CONFORM_FIT_PROX).mean()) < _nc()._CONFORM_FIT_FRAC:
            continue
        # #covered-skin-target: the blend below aimed each vertex at the ONE
        # nearest body vertex. On a coarse crotch panel that is the inner-thigh
        # skin, while the panel also covers the pelvis-static cleft: the reported
        # gusset's shipped row IS this blend (0.1 x author + 0.9 x nearest, to
        # 0.02). Where a vertex covers skin, the clearance-weighted mean of that
        # skin is the target instead.
        _cov_target: dict = {}
        if _body_nrm is not None and len(_body_nrm) == len(_Vb):
            _zb, _xb = _Vb[:, 2], _Vb[:, 0]
            _cband = ((_zb >= _nc()._COVER_Z_LO) & (_zb <= _nc()._COVER_Z_HI)
                      & (np.abs(_xb) < _nc()._COVER_X))
            _cover, _clr = _covered_skin_map(Vw, _Vb, _body_nrm, _cband,
                                             _nc()._COVER_REACH)
            for _gi, _bis in _cover.items():
                if len(_bis) < _nc()._COVER_MIN_VERTS:
                    continue
                _ct0 = _covered_skin_target(_bis, _clr, body_w, _nc()._COVER_EPS)
                if _ct0:
                    _cov_target[_gi] = _ct0
        touched: "set" = set()
        removed: dict = {}   # bone -> vert indices that LOST it in the blend
        conf = 0
        for i in range(n):
            if d[i] > _nc()._CONFORM_VERT_PROX:
                continue
            dv = vw[i]
            if not dv:
                continue
            if any(w > 0.1 and not _is_skeleton_bone(b) for b, w in dv.items()):
                continue  # custom-chain vert -> leave it (partition safety)
            bd = _cov_target.get(i) or body_w[idx[i]]
            new = _nc()._conform_blend_vert(dv, bd, _nc()._CONFORM_BLEND, _nc()._CONFORM_DELTA)
            if new is None:
                continue
            touched |= set(dv)        # bones the vert had (may now lose this vert)
            for b in dv:
                if b not in new:
                    removed.setdefault(b, set()).add(i)
            vw[i] = new
            touched |= set(vw[i])     # bones it kept after the blend
            conf += 1
        if conf:
            dirty = True
            total += conf
            for bn in sorted(touched):     # sorted: see #deterministic-weight-write
                # setShapeWeights MERGES per vert (omitted verts KEEP their old
                # value; explicit 0.0 removes) -- the old comment here claimed a
                # full rebuild applied removals, which is false under merge
                # semantics: a bone the blend dropped from a vert survived on
                # disk and the row summed >1 (the view-angle-flicker class the
                # leg pass bisected). Emit explicit 0.0 for every vert that
                # lost the bone. #weight-write-invariant
                pairs = [(i, vw[i][bn]) for i in range(n)
                         if bn in vw[i] and vw[i][bn] > 1e-4]
                pairs += [(i, 0.0) for i in removed.get(bn, ())]
                if not any(w > 0.0 for _i, w in pairs):
                    # NEVER EMPTY A BONE: writing pure removals would leave the
                    # bone in the list but out of the regenerated partition
                    # palette (equip CTD). Stale sums on a few rows are the
                    # cheaper failure. #zeroweight-bone-desync
                    continue
                s.setShapeWeights(bn, pairs)
            # setShapeWeights writes the NATIVE skin buffer but leaves pynifly's
            # cached `bone_weights` (_weights) stale. The fold re-authors from THIS
            # in-memory nf, and _copy_shape reads bone_weights -- so without this it
            # would copy the PRE-conform weights and silently drop the conform.
            # Invalidate so the re-author re-reads the native buffer we just wrote.
            s._weights = None
    return dirty, total

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
    for leg in _nc()._LEG_DEFORM_BONES:
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
    base = [b for b in _nc()._BUTT_MATCH_BONES if dv.get(b, 0.0) > 1e-4]
    if len(base) < 2:
        return touched, added  # need >=2 of (thigh/pelvis) present to anchor + conserve
    # Process the jiggle bones the body weights here (to graft) AND any the vert ALREADY
    # carries (so a re-run redistributes the same total = idempotent, never double-counts).
    jig = sorted(
        {b for b in _nc()._BUTT_JIGGLE_BONES if dv.get(b, 0.0) > 1e-4}
        | ({b for b in _nc()._BUTT_JIGGLE_BONES if bd.get(b, 0.0) > 1e-3}
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
                      anchor: str = _CHEST_ANCHOR,
                      follow: "float | None" = None) -> "tuple":
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
    present = [b for b in _nc()._CHEST_JIGGLE_BONES if dv.get(b, 0.0) > 1e-4]
    if anc_w <= 1e-4 and not present:
        return touched, added           # no Spine2 to draw from / nothing to manage
    body = {b: bd.get(b, 0.0) for b in _nc()._CHEST_JIGGLE_BONES if bd.get(b, 0.0) > 1e-3}
    # managed mass = anchor + any breast the vert ALREADY has (idempotent re-run)
    mass = anc_w + sum(dv.get(b, 0.0) for b in present)
    if mass < 1e-4:
        return touched, added
    bsum = sum(body.values())
    if follow is not None:
        # #chest-follow-ratio: the ceiling is a FRACTION of what the body does here,
        # so every vert tracks the same proportion regardless of how much the body
        # jiggles at that spot. The absolute cap below cannot do that -- 0.15 is a
        # third of the body's motion at the bust and all of it at the butt.
        want = min(bsum * max(0.0, follow), mass) if bsum > 0.0 else 0.0
        # THE CEILING CAPS THIS PASS'S GRAFT, NEVER THE PASS-THROUGH.
        # `target` below SETS each breast bone rather than raising it, so without
        # this a ceiling under the vert's existing follow drags it DOWN. That is
        # not hypothetical: since the torso jiggle graft went default-ON it runs
        # FIRST and leaves real bust weight here, and this pass then overwrote it
        # with the material ceiling -- measured on a metal cuirass as 0.66 follow
        # (torso graft alone) -> 0.325 (both passes), i.e. the two features
        # together were WORSE than either alone, and worse than the 0.805 the
        # chest pass reached before the torso graft existed. The rigid ceiling
        # exists so THIS pass does not make metal look rubbery; it was never a
        # licence to strip follow another pass established.
        # #chest-follow-passthrough
        want = max(want, sum(dv.get(b, 0.0) for b in present))
    else:
        want = min(bsum * min(1.0, strength), cap, mass) if bsum > 0.0 else 0.0
    target = {b: want * body[b] / bsum for b in body} if bsum > 0.0 else {}
    # Per-bone clamp < the 0.1 rigid-gate threshold (re-run safety); the clamped-off weight
    # stays on the anchor (total just ends up a little lower), never redistributed away.
    # Ratio mode deliberately does NOT clamp: holding a bone under 0.1 would cap the
    # follow ratio at ~0.6x the body and defeat the point. The cost is that a shape
    # grafted this way reads as "already jiggling" to a re-run's rigid gate -- which is
    # true, and the pass is idempotent through the `already matched` check below.
    if follow is None:
        for b in list(target):
            if target[b] > _nc()._CHEST_JIGGLE_PERBONE:
                target[b] = _nc()._CHEST_JIGGLE_PERBONE
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

def _source_bust_weight_map(src_nif_path, shape_name, n_verts):
    """Per-vert weight dicts for `shape_name` AS THE AUTHOR SHIPPED IT, or None.

    #source-follow asks "did the outfit author weight this garment's bust" --
    a question about the SOURCE. Measuring it on the converted shape used to be
    equivalent, and stopped being so when the torso jiggle graft went default-ON
    and started running BEFORE this pass: the graft's own weight then read as
    authorship, flipping the shape from "unweighted" (ceiling lifted to the full
    geometric requirement) to "weighted" (capped at the material ceiling), and
    the measured result was a metal cuirass held at 0.616 where the chest pass
    alone reached 0.792. Reading the source restores the question's meaning.

    Vert indices are preserved through conversion (the passes move positions and
    reweight; they do not renumber), so a source shape with the SAME vert count
    maps 1:1. Any mismatch returns None -> the caller falls through to today's
    material ceiling, which is the conservative direction. #chest-follow-passthrough"""
    if src_nif_path is None:
        return None
    try:
        pyn = _nc()._pynifly()
        snf = pyn.NifFile(filepath=str(src_nif_path))
        ss = next((x for x in snf.shapes if x.name == shape_name), None)
        if ss is None or len(ss.verts) != n_verts:
            return None
        out = [dict() for _ in range(n_verts)]
        for b, pairs in (ss.bone_weights or {}).items():
            for vi, w in pairs:
                iv = int(vi)
                if 0 <= iv < n_verts:
                    out[iv][b] = out[iv].get(b, 0.0) + float(w)
        return out
    except Exception:
        return None

def _covered_skin_target(cover, clearance, body_w, eps):
    """The skin a garment vertex COVERS decides its split. `cover` lists the body
    vertices whose nearest garment vertex this is; `clearance` holds each one's
    outward distance to the vertex (negative = the vertex sits inside the skin
    there, which counts as touching). Returns the 1/(max(clearance, 0) + eps)-
    weighted mean of their weight rows, so the skin the panel would touch first
    has the most say -- or None for an empty cover. Pure. #covered-skin-target"""
    if not cover:
        return None
    acc: dict = {}
    tot = 0.0
    for b in cover:
        c = float(clearance[b])
        wgt = 1.0 / (max(c, 0.0) + eps)
        tot += wgt
        for bone, w in body_w[b].items():
            acc[bone] = acc.get(bone, 0.0) + float(w) * wgt
    if tot <= 0.0:
        return None
    return {bone: w / tot for bone, w in acc.items()}


def _covered_skin_map(Vg, Vb, Nb, band, reach):
    """Which body vertices each garment vertex COVERS: for every body vertex in
    `band`, its nearest garment vertex within `reach`. Returns ({garment vertex:
    [body vertices]}, clearance per body vertex = dot(garment - body, body normal),
    NaN where uncovered). Pure. #covered-skin-target"""
    from scipy.spatial import cKDTree
    cover: dict = {}
    clearance = np.full(len(Vb), np.nan)
    idx = np.flatnonzero(band)
    if len(idx) == 0 or len(Vg) == 0:
        return cover, clearance
    d, j = cKDTree(Vg).query(Vb[idx], k=1)
    for bi, dist, gi in zip(idx, d, j):
        if dist > reach:
            continue
        cover.setdefault(int(gi), []).append(int(bi))
        clearance[bi] = float(np.dot(Vg[gi] - Vb[bi], Nb[bi]))
    return cover, clearance


def _morphtri_gated_detail_bones() -> frozenset:
    """The leg DETAIL bones a morph-TRI shape must not be grafted.

    `#morphtri-no-leg-graft` gated all three detail bones per leg, on evidence
    that named one: RearCalf landing on a flap tip at calf height. With
    `#morphtri-thigh-graft` on, only the bones anchored to the CALF stay gated;
    FrontThigh / RearThigh anchor to the thigh and are grafted like any other
    shape's. Off, every detail bone is gated as before."""
    if _nc().MORPHTRI_THIGH_GRAFT:
        return frozenset(b for leg in _nc()._LEG_DEFORM_BONES
                         for b, anc in leg["detail"] if anc == leg["calf"])
    return frozenset(_nc()._LEG_DETAIL_BONE_NAMES)


def _match_rigid_leg_bend_to_body(dst_path, biped_slots: int = 0,
                                  src_nif_path=None) -> int:
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
    if not _nc().MATCH_RIGID_LEG_BEND:
        return 0
    if biped_slots & (_nc().BIPED_SLOT33_BIT | _nc().BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the clip class
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _nc()._body_conform_ref(weight)
    dref = _nc()._body_leg_detail_ref(weight)
    if ref is None or dref is None:
        return 0
    _Vb, body_w, _bones, tree = ref
    body_stbs, _body_ident = dref
    # #covered-skin-target: the body's outward normals give each covered skin
    # vertex a clearance; None (no body, or a non-world skin frame) means the
    # six-nearest target is used everywhere, as before.
    _body_nrm = (_nc()._body_conform_normals(weight)
                 if _nc().COVERED_SKIN_TARGET else None)
    # Body leg-bone STB matrices (anchor + detail) -- inputs to the re-anchoring.
    body_mat: dict = {}
    body_proto = None
    for bn, tb in body_stbs.items():
        m, proto = _nc()._stb_to_mat4(tb)
        if m is not None:
            body_mat[bn] = m
            if body_proto is None:
                body_proto = proto
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    # A shape with its OWN source BodySlide morph TRI already tracks body sliders
    # at runtime, keyed to its ORIGINAL skin -- which is exactly why the reskin
    # excludes it (`_MORPHTRI_SCALE or not _is_morph_tri`). Grafting the leg
    # DETAIL bones (FrontThigh/RearThigh/RearCalf) onto it is therefore both
    # redundant AND harmful: those bones move with the body at runtime, so a thin
    # cloth rim weighted to them deforms PER-ANIMATION while the BIND pose
    # measures perfectly clean. Reported in game as the flap tip bending, and
    # getting worse with different animations. Measured on a heavy cuirass rear
    # flap tip: R/L RearCalf 0.00% -> ~1.26%, taken off the calves; with this
    # gate the tip returns to source skinning. #morphtri-no-leg-graft
    morph_tri_names = (_source_morph_tri_shape_names(Path(src_nif_path))
                       if (src_nif_path and _nc().MORPHTRI_NO_LEG_GRAFT) else set())
    # Bones grafted onto the plate + the EXISTING bone each re-anchors to: leg detail bones
    # anchor to Thigh/Calf; the butt-jiggle bones to the Pelvis; the breast bones to Spine2.
    # graft_anchor also drives the fold-back of any bone we can't safely anchor.
    graft_anchor = {b: anc for leg in _nc()._LEG_DEFORM_BONES for b, anc in leg["detail"]}
    _do_jiggle = _nc()._BUTT_JIGGLE and _nc()._BUTT_JIGGLE_STRENGTH > 0.0
    if _do_jiggle:
        for jb in _nc()._BUTT_JIGGLE_BONES:
            graft_anchor[jb] = _nc()._BUTT_PELVIS
    _do_chest = _nc()._CHEST_JIGGLE and _nc()._CHEST_JIGGLE_STRENGTH > 0.0
    if _do_chest:
        for cb in _nc()._CHEST_JIGGLE_BONES:
            graft_anchor[cb] = _CHEST_ANCHOR
    _graftable = (set(_nc()._LEG_DETAIL_BONE_NAMES)
                  | (set(_nc()._BUTT_JIGGLE_BONES) if _do_jiggle else set())
                  | (set(_nc()._CHEST_JIGGLE_BONES) if _do_chest else set()))
    # The subset a morph-TRI shape must NOT receive -- see #morphtri-keep-jiggle
    # at the top of the loop. Everything else this pass grafts is still allowed.
    # #morphtri-thigh-graft: only the CALF detail bone (RearCalf, the flap-tip
    # defect) stays gated; the thigh pair anchors to the thigh and is let through.
    _LEG_DETAIL_BONE_NAME_SET = _morphtri_gated_detail_bones()
    # Lazy: with the gate OFF this pass must do NO extra work at all.
    _has_xml = _nc()._piece_has_hdt_xml(dst_path, nif=nf) if _nc().DRAPE_SKIP_XML_GATED else None
    _skip_keys = _nc()._conform_skip_keys(_has_xml)
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        if (s.name in collider_names or s.name in softbody_names
                or s.name in layered_cloth_names
                or any(k in nm for k in _skip_keys)):
            continue
        # #morphtri-keep-jiggle. A morph-TRI shape used to be skipped ENTIRELY
        # here, which threw away the butt/chest jiggle graft along with the leg
        # detail graft. Only the LEG DETAIL bones caused the reported defect
        # (RearCalf landing on a flap tip at calf height); the jiggle bones this
        # same pass grafts anchor at the Pelvis and Spine2 and are exactly what
        # the shape needs. So narrow the graft instead of dropping the shape.
        _mt = s.name in morph_tri_names
        _anchor = ({b: a for b, a in graft_anchor.items()
                    if b not in _LEG_DETAIL_BONE_NAME_SET} if _mt
                   else graft_anchor)
        _shape_graftable = ((_graftable - _LEG_DETAIL_BONE_NAME_SET) if _mt
                            else _graftable)
        if _mt and not _anchor:
            continue        # nothing left to graft once the leg detail is out
        if _nc()._shape_has_effect_shader(s) or _nc()._is_fx_overlay_name(s.name):
            continue  # glow/decal overlay -- grafting+re-saving corrupts its effect-shader
                      # controller -> CTD (the Daedric 'MaleTorsoGlow' crash). Not body armor.
                      # Name check catches shapes whose shader is attached AFTER this pass
                      # (the 'TorsoF:FX' timing hole the buffer check alone missed).
        bw = s.bone_weights or {}
        # Eligible if it's LEG armor (carries Thigh+Calf -> knee/thigh/butt passes) OR a rigid
        # CHEST plate (carries the Spine2 chest anchor -> breast-jiggle pass). The rigid gate
        # below still excludes anything that already jiggles.
        has_leg = any(leg["thigh"] in bw and leg["calf"] in bw for leg in _nc()._LEG_DEFORM_BONES)
        has_chest = _do_chest and (_CHEST_ANCHOR in bw)
        if not (has_leg or has_chest):
            continue
        # ONLY rigid plate the jiggle-gated conform left alone.
        jig = 0
        for b, pairs in bw.items():
            if _is_physics_jiggle_scale_bone(b):
                jig += sum(1 for _vi, w in pairs if float(w) > 0.1)
                # Ratio mode needs the FULL count to judge a fraction, so it cannot
                # short-circuit at the threshold. #chest-follow-ratio
                if jig >= _nc()._CONFORM_MIN_JIGGLE_VERTS and not _nc().CHEST_FOLLOW_RATIO:
                    break
        _defer_to_conform = False
        if jig >= _nc()._CONFORM_MIN_JIGGLE_VERTS:
            # An ABSOLUTE count of 8 verts means a big shape is called "already
            # jiggling" on a trace: the traced leather cuirass tripped it with 9 of
            # 3742 verts (0.24%) and was dropped from the chest graft entirely, which
            # is why its follow ratio sat at 0.25 and nothing could raise it.
            # Genuinely jiggling shapes are far above that -- measured across the
            # pack, the fraction of verts over 0.1 runs p10 0.70%, p50 8.35% -- so a
            # 1% floor separates "this shape jiggles" from "a graft brushed it".
            _n_v = len(s.verts) or 1
            if not (_nc().CHEST_FOLLOW_RATIO and (jig / _n_v) < _nc()._CHEST_RIGID_JIGGLE_FRAC):
                if not _nc().CHEST_FOLLOW_RATIO:
                    continue
                # #conform-coverage-hole: do not hand the shape over blind. The
                # conform pass has its own gates, and when it declines, deferring
                # orphans the shape -- neither pass grafts it and its follow ratio
                # is whatever the reskin happened to leave. Decided below, once the
                # body query needed to answer it exists.
                _defer_to_conform = True
        existing = set(s.bone_names or [])
        if len(existing | _shape_graftable) > SKIN_PARTITION_BONE_CAP:
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
        for b, anc in _anchor.items():
            if (anc not in existing or b in existing or b not in body_mat
                    or anc not in body_mat or body_proto is None):
                continue
            ma, _p = _nc()._stb_to_mat4(s.get_shape_skin_to_bone(anc))
            if ma is None:
                continue
            stb = _nc()._derive_anchored_stb(body_mat[b], body_mat[anc], ma, body_proto)
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
        # What each vertex ALREADY HELD, snapshotted before the match loop
        # mutates `vw`. #last-carrier-hold reads it to tell an incumbent
        # from a bone that is new to this row.
        _pre_live = [frozenset(b for b, w in r.items()
                               if w > _nc()._WRITE_MIN) for r in vw]
        # #chain-welded-torso: which verts are SIMULATED cloth. Needed both for the
        # per-vert guard in the match loop and for judging a chain-welded torso on
        # its rigid verts below.
        is_chain = _nc()._chain_vert_mask(vw, n)
        Vw = _verts_skin_to_world(V, g2s)
        _K = max(1, min(_nc()._LEG_MATCH_K, len(body_w)))
        d_k, idx_k = tree.query(Vw, k=_K)
        if _K == 1:
            d_k = d_k[:, None]
            idx_k = idx_k[:, None]
        d = d_k[:, 0]                # nearest distance still gates the passes
        # #covered-skin-target: the cover map is body -> garment (which garment
        # vertex is nearest to each body vertex in the crotch/hip band), the
        # inverse of the query above, so a coarse panel is charged with every
        # skin vertex it passes over, not only the six nearest to it.
        _cov_target: dict = {}
        if _body_nrm is not None and len(_body_nrm) == len(_Vb):
            _zb, _xb = _Vb[:, 2], _Vb[:, 0]
            _cband = ((_zb >= _nc()._COVER_Z_LO) & (_zb <= _nc()._COVER_Z_HI)
                      & (np.abs(_xb) < _nc()._COVER_X))
            _cover, _clr = _covered_skin_map(Vw, _Vb, _body_nrm, _cband,
                                             _nc()._COVER_REACH)
            for _gi, _bis in _cover.items():
                if len(_bis) < _nc()._COVER_MIN_VERTS:
                    continue
                _ct0 = _covered_skin_target(_bis, _clr, body_w, _nc()._COVER_EPS)
                if _ct0:
                    _cov_target[_gi] = _ct0
        # #chest-follow-ratio target, computed HERE (it used to sit below the
        # deferral) because the deferral decision needs it: "does this shape
        # already follow well enough" is a question about FOLLOW, and answering
        # it with a vert count is what let the torso graft disqualify shapes
        # from this pass. See the block below for the derivation.
        _chest_follow = _chest_follow_target(
            s, n, d, idx_k, vw, body_w, is_chain,
            src_vw=(_source_bust_weight_map(src_nif_path, s.name, n)
                    if (_nc().CHEST_FOLLOW_RATIO and _nc().SOURCE_FOLLOW_CEILING) else None))
        if _defer_to_conform:
            # #conform-coverage-hole. Hand over ONLY if the conform pass will really
            # take the shape; otherwise keep it and graft it here, because the
            # alternative is that nothing does. Measured on the traced leather
            # cuirass: 1.02% jiggle verts (just over the ratio floor) deferred it,
            # and a whole-shape fit of 0.501 against the conform's 0.90 meant the
            # conform declined -- follow left at 0.34 against a 0.81 requirement,
            # which the census puts in the 92%-clip band.
            #
            # #chest-follow-passthrough: in ratio mode, do NOT hand over a shape
            # that is still SHORT of its requirement. `_defer_to_conform` is
            # decided by a jiggle-VERT COUNT, and since the torso graft went
            # default-ON that count is tripped by our own earlier pass -- so a
            # garment the torso graft lifted partway was declared "already
            # jiggling" and handed off, ending BELOW what either pass reached
            # alone (measured: 0.650 both vs 0.784 chest-only). Achieved follow
            # against the requirement is the honest test; the count never was.
            _achieved = None
            if _nc().CHEST_FOLLOW_RATIO and _chest_follow is not None:
                _achieved = _nc()._shape_bust_follow(vw, body_w, idx_k, _nc()._chest_band(
                    n, d, idx_k, body_w, is_chain))
            if not (_nc().CHEST_FOLLOW_RATIO and _chest_follow is not None
                    and _achieved is not None
                    and _achieved < _chest_follow - _nc()._CHEST_FOLLOW_SHORTFALL):
                if not _nc()._conform_orphans_shape(s, vw, n, d, collider_names,
                                              softbody_names, layered_cloth_names,
                                              is_chain=is_chain,
                                              piece_has_hdt_xml=_has_xml):
                    continue

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
        # #chest-follow-ratio. Geometry sets the amount, the material only caps it.
        # ONE ratio for the whole shape, deliberately: applying the per-vert
        # requirement directly was MEASURED WORSE than doing nothing (leather cuirass
        # 4.0% -> 9.5% skin visible at 5u) because neighbouring verts then move by
        # different amounts and the surface tears open between them. The requirement
        # varies smoothly but the garment has to deform as one piece, so the shape
        # takes the p90 of what its covered bust verts need.
        # (computed above, before the deferral decision, which needs it)
        _max_prox = max(_nc()._LEG_BEND_PROX, _nc()._BUTT_PROX, _nc()._CHEST_PROX)
        for i in range(n):
            di = d[i]
            if di > _max_prox:
                continue  # not hugging the body by any pass -> leave it
            if _nc().LEG_CHAIN_GUARD and is_chain[i]:
                # #chain-welded-torso. SIMULATED cloth: HDT-SMP drives this vert at
                # runtime, so a skeletal graft here is both meaningless (the sim
                # overrides it) and dangerous (the layered-cloth equip crash). Every
                # other pass already refuses these verts; this one used to write them
                # because no chain garment survived its shape gates -- until the
                # chain-welded torso claim below started letting them through.
                continue
            zi = Vw[i, 2]
            # Three independent passes, each with its OWN prox/z-gate (the leg pass stays at
            # _LEG_BEND_PROX -- correct in-game; butt/chest reach further):
            #  - LEG: z-tapered Thigh:Calf bend + detail flex (full knee -> partial thigh).
            #  - BUTT: Thigh<->Pelvis rebalance + matched butt-jiggle graft.
            #  - CHEST: matched, capped breast-jiggle graft (self-gates to the front).
            sgi = _nc()._leg_bend_strength(zi) if di <= _nc()._LEG_BEND_PROX else 0.0
            bgi = (_butt_match_strength(zi) if di <= _nc()._BUTT_PROX else 0.0)
            # #covered-skin-target: a vertex with a covered-skin target is matched
            # to THAT skin at full strength, whatever the z-ramp above says.
            _ct = _cov_target.get(i) if di <= _nc()._BUTT_PROX else None
            if _ct is not None:
                bgi = max(bgi, _nc()._COVER_STRENGTH)
            cgi = (_chest_match_strength(zi) if (_do_chest and di <= _nc()._CHEST_PROX) else 0.0)
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
                    vw[i], _ct if _ct is not None else bwi, strength=bgi,
                    jiggle=_do_jiggle, jiggle_strength=_nc()._BUTT_JIGGLE_STRENGTH,
                    rebalance=_nc()._BUTT_REBALANCE)
                t |= t2
                need |= jadded
            if cgi > 0.0:
                t3, cadded = _chest_match_vert(vw[i], bwi, strength=cgi,
                                               follow=_chest_follow)
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
        # #weight-write-invariant: CAP BEFORE `to_add` IS DECIDED.
        # The `any(... > 1e-4 ...)` test below exists so we never `add_bone` a bone
        # that will emit no weights. Capping AFTER it re-opens exactly that hole:
        # the cap can zero a grafted bone on every vertex, leaving a bone already
        # added to the shape with an EMPTY weight list -- `#zeroweight-bone-desync`,
        # a per-vert index running past the regenerated palette on equip. So cap
        # over a PROVISIONAL palette (everything we could bind) first; a graft that
        # does not survive then simply fails the test below, is never added, and
        # falls into `unsafe` to be folded onto its anchor by the existing loop.
        if _nc().WEIGHT_INVARIANT_ENABLED:
            # #last-carrier-hold applies HERE TOO, and this is the call that
            # actually evicts: the graft is already in `vw` by now, so this is
            # where a five-influence row first appears. Wiring only the re-cap
            # below fixed nothing -- traced, the bone was already gone.
            _cap_and_renormalise_rows(
                vw, n, set(existing) | {b for b in need if b in graft_stb},
                incumbents=_pre_live)
        # SORTED, not `need`'s own order. `need` is a real set, and a list
        # comprehension over a set is exactly as arbitrary as the set -- so this
        # decided `add_bone` order, which IS the written NIF's bone palette
        # order, which decides which bone the 4-influence cap evicts on a tie.
        # It was stable only because the build spec pins the interpreter's hash
        # seed; remove that pin and the palette varied per run. Sorting makes the
        # order INTRINSIC, so the output no longer depends on a build flag.
        # (BUG-04. Changes palette order and therefore output BYTES, which is why
        # it was held until a reconvert was due rather than slipped in mid-pack.)
        to_add = sorted(b for b in need if b in graft_stb and b not in existing
                        and any(vw[i].get(b, 0.0) > 1e-4 for i in range(n)))
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
                    except Exception as _pe:
                        _note_pass_failure("add_bone", _pe)
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
        # #weight-write-invariant (P6 step 2b) -- write EVERY (bone, vert) whose
        # weight this pass CHANGED, and state removals explicitly.
        #
        # The previous write was `for bn in touched: setShapeWeights(bn, [... if
        # vw[i][bn] > 1e-4])`. Because `setShapeWeights` MERGES -- anything not
        # written keeps its old file value -- that lost weight two ways and left
        # the vertex's row not summing to 1:
        #   1. a bone OUTSIDE `touched` whose weight the rebalance changed was
        #      never written at all;
        #   2. a bone INSIDE `touched` whose weight fell to <=1e-4 was filtered
        #      out of the pair list instead of being CLEARED, so its stale value
        #      survived (the mechanism bisected 2026-07-22 via
        #      CBBE2UBE_NO_LEG_BEND_MATCH=1 -> 0 off-normal rows).
        # Traced 2026-07-25: this pass introduces ALL of the on-disk drift on a
        # real cuirass -- 0 bad-sum verts before it, 2016 after.
        #
        # `vw` is the desired end state and `bw` is the pre-pass snapshot, so the
        # diff between them is exactly what must reach the file. Writing 0.0
        # genuinely REMOVES an influence (verified by save+reload), which is what
        # makes (2) expressible. Unchanged pairs are skipped -- the merge already
        # preserves them, and rewriting them would only widen the blast radius.
        #
        # Only bones the shape can BIND are written: an `unsafe` bone has no
        # skin-to-bone xform, and weighting one skins those verts to the origin.
        #
        # DEFAULT ON -- see WEIGHT_INVARIANT_ENABLED (`CBBE2UBE_NO_WEIGHT_INVARIANT=1`
        # turns it off and restores the original write byte-for-byte). Measures far
        # better: bad-sum verts 1966 -> 16, 181 -> 0.
        #
        # This comment said "OPT-IN (default OFF)" while the flag above was already
        # default ON. That contradiction was read as fact twice on 2026-07-27 and
        # reported as "the fix is written but not enabled" -- wrong both times. A
        # comment that disagrees with its own flag is worse than no comment.
        if not _nc().WEIGHT_INVARIANT_ENABLED:
            for bn in sorted(touched):     # sorted: see #deterministic-weight-write
                s.setShapeWeights(bn, [(i, vw[i][bn]) for i in range(n)
                                       if bn in vw[i] and vw[i][bn] > 1e-4])
            for eb, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(eb, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            for b in to_add:
                try:
                    s.set_skin_to_bone_xform(b, graft_stb[b])
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            continue
        _writable = set(existing) | set(to_add)
        # RE-CAP over the FINAL palette. The pre-`to_add` pass above already capped
        # over the provisional one; this second call settles two things it could not
        # know: a graft that failed the emit test is no longer writable, and the
        # `unsafe` fold has since folded such a bone's weight onto its anchor (which
        # can push a row back over 4 if the anchor was not already in it, and leaks
        # mass outright when a bone had no anchor). Idempotent on rows that are
        # already capped and normalised.
        # #last-carrier-hold: `_pre_live` is what each vertex held BEFORE this
        # pass, so a bone new to a vertex may not take the slot of one whose
        # only vertex that is. BISECTED to here -- `CBBE2UBE_NO_LEG_BEND_MATCH=1`
        # gives `SkirtBBone02` back its single authored row and takes the piece
        # to 0 zero-weight bones, while `CBBE2UBE_NO_FULL_WEIGHT_MATCH=1`
        # changes nothing. Passing `to_add` instead does NOT work: the bone that
        # displaces it (`NPC R Butt`) is already in the shape's bone list and is
        # only new to that VERTEX.
        _cap_and_renormalise_rows(vw, n, _writable, incumbents=_pre_live)
        _orig: dict = {}
        for _b, _pairs in bw.items():
            if _b not in _writable:
                continue
            _d: dict = {}
            for _vi, _w in _pairs:
                _iv = int(_vi)
                if 0 <= _iv < n:
                    _d[_iv] = _d.get(_iv, 0.0) + float(_w)
            _orig[_b] = _d
        for bn in sorted(_writable):
            _was = _orig.get(bn, {})
            # NEVER EMPTY A BONE. The cap can zero a bone across every vertex --
            # including one an EARLIER pass (`_install_skin`) added, which the
            # `to_add`-after-cap fix does not cover because this pass never added
            # it. Writing those 0.0 removals strips the influence while the BONE
            # stays in the shape's list: a zero-weight bone, absent from the
            # regenerated skin-partition palette, so a per-vert index can run past
            # the palette on equip (#zeroweight-bone-desync). Measured on a real
            # first-person mesh -- the equip surface -- 2 orphans with this pass
            # enabled, 0 with it disabled.
            # pynifly has no remove_bone, so the only safe move is not to empty it:
            # leave the stale weights and accept the row being off 1.0 for those
            # verts. Drift is a cosmetic defect; an orphan bone is a crash.
            if _was and not any(vw[i].get(bn, 0.0) > _nc()._WRITE_MIN
                                for i in range(n)):
                continue
            _out = []
            for i in range(n):
                _nv = vw[i].get(bn, 0.0)
                _ov = _was.get(i, 0.0)
                if _nv <= _nc()._WRITE_MIN and _ov <= _nc()._WRITE_MIN:
                    continue          # absent before and after
                if abs(_nv - _ov) <= 1e-6:
                    continue          # unchanged -> the merge keeps it
                _out.append((i, _nv if _nv > _nc()._WRITE_MIN else 0.0))
            if _out:
                s.setShapeWeights(bn, _out)
        # STBs LAST: restore the existing bones' originals (add_bone/setShapeWeights
        # zeroed them) + set the grafted detail bones'. Nothing after this resets them.
        for eb, st in saved_stb.items():
            try:
                s.set_skin_to_bone_xform(eb, st)
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
        for b in to_add:
            try:
                s.set_skin_to_bone_xform(b, graft_stb[b])
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
    if dirty:
        # A re-save must never silently un-hide an SMP collision proxy.
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            # A LOST SAVE IS NOT "NOTHING QUALIFIED". `return 0` is this pass's
            # own word for "no rows matched", so swallowing here makes a failed
            # write indistinguishable from a clean no-op -- the exact shape of
            # the two defects that shipped unnoticed. Report, then return.
            _note_pass_failure("_match_rigid_leg_bend_to_body/save", _se, dst_path)
            return 0
    return total

def _limb_morph_tri_skip(dst_path, nf, src_nif_path, ignore_morph_tri: bool,
                         keep_draping_skip: bool) -> set:
    """Shapes a limb-motion instance must leave on their source skin because the
    source ships a BodySlide morph TRI naming them (#morphtri-no-leg-graft).

    An instance that ignores that gate gets an empty set -- unless it keeps the
    DRAPING exemption (#leg-motion-morphtri): then the TRI-owning shapes whose
    names the leg passes skip (robe / cloak / cape / dress / gown / sarong /
    loincloth on a piece with no HDT XML, `_conform_skip_keys`) stay skipped.
    The user kept that exemption on 2026-09-19, and before this instance ignored
    the TRI gate it never reached them."""
    if not (src_nif_path and _nc().MORPHTRI_NO_LEG_GRAFT):
        return set()
    if ignore_morph_tri and not keep_draping_skip:
        return set()
    names = _source_morph_tri_shape_names(Path(src_nif_path))
    if not ignore_morph_tri:
        return names
    keys = _nc()._conform_skip_keys(
        _nc()._piece_has_hdt_xml(dst_path, nif=nf)
        if _nc().DRAPE_SKIP_XML_GATED else None)
    return {n for n in names if any(k in (n or "").lower() for k in keys)}


def _match_leg_motion_to_body(dst_path, biped_slots: int = 0, src_nif_path=None) -> int:
    """LEG instance of the limb-motion match -- see _match_limb_motion_to_body.

    The flag is read HERE, at call time, not captured into the arguments, so a
    test that monkeypatches the module global still disables the pass.
    """
    if not _nc().MATCH_LEG_MOTION:
        return 0
    return _match_limb_motion_to_body(
        dst_path, biped_slots, src_nif_path=src_nif_path,
        family="leg", bones=_nc()._LEG_MOTION_BONES,
        z_lo=_nc()._LEG_MOTION_Z_LO, z_hi=_nc()._LEG_MOTION_Z_HI,
        max_dist=_nc()._LEG_MOTION_MAX_DIST, strength=_nc()._LEG_MOTION_STRENGTH,
        # Same fallback the arm pass uses (#smp-row-gate). Measured over 400
        # converted pieces: 39 leg-bearing shapes are gated out of this pass
        # entirely, and every one of them has rows that survive the per-row test,
        # so the shape gate was costing real work rather than protecting a chain.
        smp_row_gate=True,
        # #leg-motion-morphtri: the population this instance was built for --
        # minus the DRAPING-named shapes the leg passes already skip by name.
        ignore_morph_tri=_nc().LEG_MOTION_ON_MORPHTRI,
        keep_draping_skip=True)

def _match_arm_motion_to_body(dst_path, biped_slots: int = 0, src_nif_path=None) -> int:
    """ARM instance of the limb-motion match  (#armhole-arm-follow).

    Same defect as the leg case, at the shoulder: CBBE ends UpperArm weight at
    z 99.7 and UBE carries it to z 110.1, so a CBBE-authored garment arrives with
    ZERO UpperArm weight across the armhole over a body that has 0.179 there, and
    the shoulder walks out through it. See _ARM_MOTION_BONES for the measurements.
    """
    if not _nc().MATCH_ARM_MOTION:
        return 0
    return _match_limb_motion_to_body(
        dst_path, biped_slots, src_nif_path=src_nif_path,
        family="arm", bones=_nc()._ARM_MOTION_BONES,
        z_lo=_nc()._ARM_MOTION_Z_LO, z_hi=_nc()._ARM_MOTION_Z_HI,
        max_dist=_nc()._ARM_MOTION_MAX_DIST, strength=_nc()._ARM_MOTION_STRENGTH,
        # MEASURED over 391 converted pieces: the shape gate fires on the main
        # garment of 141 (36%), and 35 (9.0%) also carry a physics XML -- the
        # combination that makes every gated pass a silent no-op, including the
        # LEG pass. Not "most pieces", but 9% of the pack getting nothing from a
        # pass that is supposed to be default-ON is worth the row-level fallback.
        smp_row_gate=True)

def _match_spine_motion_to_body(dst_path, biped_slots: int = 0, src_nif_path=None) -> int:
    """SPINE instance of the limb-motion match  (#spine-follow).

    The torso case, and the cause of the under-bust follow deficit (S3): the
    garment carries its spine mass on Spine1 where the body uses Spine2, so it
    under-travels every spine rotation by ~20%. See _SPINE_MOTION_BONES for the
    measurements and for why this runs BEFORE the arm pass.
    """
    if not _nc().MATCH_SPINE_MOTION:
        return 0
    return _match_limb_motion_to_body(
        dst_path, biped_slots, src_nif_path=src_nif_path,
        family="spine", bones=_nc()._SPINE_MOTION_BONES,
        z_lo=_nc()._SPINE_MOTION_Z_LO, z_hi=_nc()._SPINE_MOTION_Z_HI,
        max_dist=_nc()._SPINE_MOTION_MAX_DIST, strength=_nc()._SPINE_MOTION_STRENGTH,
        smp_row_gate=True)

def _match_spine_twist_to_body(dst_path, biped_slots: int = 0,
                               src_nif_path=None) -> int:
    """The spine split again, at PARTIAL strength, on TRI-owned shapes
    (#spine-twist-partial).

    The bust emerging through the SIDE of a cuirass under a weapon swing. Same
    Spine1-vs-Spine2 defect as _match_spine_motion_to_body, but that instance
    never reaches this population (source morph TRI) and at full strength it
    trades the forward-lean pose away. See _SPINE_TWIST_BONES for the sweep and
    for the flank-scoping attempt that failed.

    Runs LAST of the family matches: every match rescales the bones it does not
    manage, so whichever runs last wins the overlapping rows.
    """
    if not _nc().MATCH_SPINE_TWIST:
        return 0
    return _match_limb_motion_to_body(
        dst_path, biped_slots, src_nif_path=src_nif_path,
        family="spine_twist", bones=_nc()._SPINE_TWIST_BONES,
        z_lo=_nc()._SPINE_MOTION_Z_LO, z_hi=_nc()._SPINE_MOTION_Z_HI,
        max_dist=_nc()._SPINE_TWIST_MAX_DIST, strength=_nc()._SPINE_TWIST_STRENGTH,
        smp_row_gate=True, lateral_half_x=_nc()._SPINE_TWIST_LATERAL_X,
        ignore_morph_tri=True, pair_by_ray=_nc()._SPINE_TWIST_PAIR_RAY)

def _match_full_weights_to_body(dst_path, biped_slots: int = 0,
                                src_nif_path=None) -> int:
    """Match the covered body's WHOLE weight vector on hugging torso rows
    (#full-weight-match).

    The family matches each fix one bone family and fund it out of the rest; this
    one has nothing left over to fund it from, so its ideal is follow = 1.0 in
    every pose simultaneously. See MATCH_FULL_WEIGHTS for the gap measurements
    that motivate it, and for why it SHIPPED off and has been DEFAULT ON since
    2026-08-11 (`CBBE2UBE_NO_FULL_WEIGHT_MATCH=1` disables). This line used to
    read "why it is default OFF", which stopped being true on 2026-08-11.

    KNOWN DEFECT, traced 2026-09-02 (docs/worklog/2026-09-02_ZEROWEIGHT_BONE_PRODUCER.md):
    the shared write in `_match_limb_motion_to_body` filters only on `_WRITE_MIN`
    and never re-applies the 4-influence cap that the SAVE applies. So this pass
    can demote an EXISTING bone's last weight to 5th place on its vertex --
    written, then dropped at save -- stranding a bone `_install_skin` had
    legitimately added (`#zeroweight-bone-desync`, an equip CTD). Proven here for
    `L/R Breast03`; 225 such bones on 163 shapes in the 2026-08-28 pack.

    `bones` is still the spine family, and is used ONLY for the caller-facing
    family label -- the full-vector branch manages every shared bone.
    """
    if not _nc().MATCH_FULL_WEIGHTS:
        return 0
    from .body_zones import ARMHOLE_Z
    return _match_limb_motion_to_body(
        dst_path, biped_slots, src_nif_path=src_nif_path,
        family="full", bones=_nc()._SPINE_MOTION_BONES,
        z_lo=_nc()._SPINE_MOTION_Z_LO, z_hi=_nc()._SPINE_MOTION_Z_HI,
        max_dist=_nc()._FULL_WEIGHT_MAX_DIST, strength=_nc()._FULL_WEIGHT_STRENGTH,
        smp_row_gate=True, ignore_morph_tri=True, pair_by_ray=True,
        full_vector=True,
        shoulder_z=ARMHOLE_Z[0], shoulder_max_dist=_nc()._FULL_WEIGHT_SHOULDER_DIST)

def _match_limb_motion_to_body(dst_path, biped_slots: int = 0, *,
                               family: str = "leg", bones=(),
                               z_lo: float = 0.0, z_hi: float = 0.0,
                               max_dist: float = 0.0,
                               strength: float = 1.0,
                               smp_row_gate: bool = False,
                               lateral_half_x: float = 0.0,
                               ignore_morph_tri: bool = False,
                               pair_by_ray: bool = False,
                               full_vector: bool = False,
                               shoulder_z: float = 0.0,
                               shoulder_max_dist: float = 0.0,
                               keep_draping_skip: bool = False,
                               src_nif_path=None) -> int:
    """Raise a garment's LIMB-BONE share toward the body's so it travels WITH the
    limb instead of being left behind. Returns the number of verts matched.

    Parameterised by bone family + Z band so the LEG (hip flexion) and ARM
    (shoulder rotation) instances are the same code: they are the same defect
    twice, and the maths -- match the managed family's mass AND its split to the
    covered body's -- is identical. Only the family, band and hug distance differ.

    Fills the gap left by _match_rigid_leg_bend_to_body: that pass only reaches shapes
    eligible for a reskin, so every BodySlide-built garment that keeps its SOURCE skin
    (the `_keep_src_skin` morph-TRI branch) still wears CBBE-fitted weights over a
    UBE body. Its limb share is then lower than the body's underneath it, the body
    out-travels the cloth as the joint swings, and skin emerges -- a defect that is
    completely invisible at bind pose.

    Deliberately conservative:
      * redistributes ONLY among bones the SHAPE already has. No add_bone, so the
        add_bone-resets-every-STB footgun cannot apply. (setShapeWeights can still
        reset STBs, so they are saved and restored regardless.)

        TRUE PER SHAPE, FALSE PER VERTEX, and the gap is where a real defect
        lived (2026-08-12). A vertex holds at most four bones; this pass will
        happily assign one of the shape's bones to a vertex that does not have
        it and has no room for it. The write is bone-by-bone and merges, so that
        assignment loses the four-way contest against the vertex's still-stale
        old values and is dropped -- while the bones that DID land were scaled
        as a share of a total that counted it, so the vertex ships light.
        Traced on `top` v1561: a perfect row summing to 1.0000 hands 0.1400 to
        `NPC R UpperArm`, which that vertex does not carry, and it ships at
        0.8601. See `#family-weight-invariant` below for the fix.
      * PUSH-UP ONLY -- the TARGET is never below the garment's existing share, so
        a garment already tracking the body is left alone. This is also what makes
        the Z band a mere safety rail: where the covered body carries no weight on
        the family, the target collapses to the garment's own share and the vert is
        written back unchanged.
        NOT an end-to-end guarantee, and this said "never lowers" until it was
        measured. The 4-INFLUENCE CAP runs after the target and can still drop a
        family bone off an overflowing row, so the WRITTEN share occasionally
        falls: measured on a multi-shape mashup, 118 of 3254 changed verts lost
        family mass, worst 0.0303, mean 0.0012. Small and cap-induced, not a
        systematic lowering -- but "never" was wrong.
      * NEVER moves a vert: rest pose stays byte-identical, which is what keeps the
        bind-pose clearance work from earlier passes intact.
      * only verts HUGGING the body (<= max_dist) inside the Z band, so a free-hanging
        hem -- or a free-hanging pauldron -- is never pulled onto the limb bones and
        made to cling or swing.
      * `lateral_half_x` narrows the band further to the FLANK (|x| >= that).
        Kept as a knob, default 0.0 = off, and NO shipped instance uses it:
        geometric flank scoping was measured and REFUTED for the spine family
        (see the "GEOMETRIC SCOPING TO THE FLANK WAS TRIED FIRST" block above
        `_match_spine_twist_to_body`) -- the twist defect does not live where a
        flank mask can reach it.
      * `ignore_morph_tri` opts an instance out of the morph-TRI skip. It exists
        because for some defects the TRI-owning shapes ARE the population -- gated,
        the pass is a measured no-op. The gate's own evidence was a spine crease
        and a calf-height flap tip on RIGID plates, so an instance that ships ON
        with it set needs its own in-game verdict on a TRI-owning piece. The
        LEG instance (`#leg-motion-morphtri`) takes it from
        `LEG_MOTION_ON_MORPHTRI`.
      * skips colliders / soft-body / HDT-SMP-rigged shapes, per the standing rule
        that every skin pass leaves authored physics geometry alone.
    """
    if biped_slots & (_nc().BIPED_SLOT33_BIT | _nc().BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the limb-motion class
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader NIF: a reload+re-save corrupts its controller -> CTD
    collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)
    # A shape driven by its OWN source morph TRI keeps its authored skin: the TRI
    # morphs it at runtime keyed to that skin, and re-sharing its limb mass makes
    # it respond differently to limb/spine rotation. Reported in game as a crease
    # that "raises when leaning forward" -- spine rotation -- while the bind pose
    # measured clean. Measured on a heavy cuirass rear band: Spine1 4.86% -> 0.94%.
    # Same rule as the reskin and the graft gates. #morphtri-no-leg-graft
    morph_tri_names = _limb_morph_tri_skip(dst_path, nf, src_nif_path,
                                           ignore_morph_tri, keep_draping_skip)
    # Does a physics XML exist for this piece at all? Drives the inert-chain
    # allowance below. Stem is per-armor (weight suffix stripped), matching where
    # both the generator and the source-XML copy write.
    # NOTE: callers pass dst_path as EITHER str or Path (the sibling passes all
    # str()-coerce for the same reason), so normalise before using Path members --
    # a bare `.stem` raised AttributeError on every str caller and, because the
    # per-shape body swallows exceptions, silently did nothing.
    _dst_p = Path(dst_path)
    _xml_stem = _dst_p.stem
    for _suf in ("_0", "_1"):
        if _xml_stem.endswith(_suf):
            _xml_stem = _xml_stem[:-len(_suf)]
            break
    try:
        _piece_has_physics_xml = (_dst_p.parent / f"{_xml_stem}.xml").is_file()
    except Exception:
        _piece_has_physics_xml = True      # unknown -> behave conservatively
    ube_bones: set = set()
    base_shape = None
    for s in nf.shapes:
        if s.name == "BaseShape":
            base_shape = s
            ube_bones = set(s.bone_names or [])
            break
    # Body reference: PREFER the injected BaseShape in THIS nif. It is the body the
    # game actually skins beside these shapes and shares their space exactly. The
    # external reference body is a different mesh with its own vertex order and
    # global-to-skin, and matching against it mapped garment verts to the wrong body
    # verts -- total leg share came out right but the L/R-thigh SPLIT was wrong by up
    # to 0.87, so the match landed on the wrong bones and fixed nothing (measured:
    # 65 -> 65 newly-exposed, where the same maths against BaseShape gave 65 -> 0).
    tree = None
    body_pv = None
    body_tris = None
    if base_shape is not None:
        try:
            from scipy.spatial import cKDTree as _KD
            bsv = np.asarray(base_shape.verts, dtype=np.float64)
            Vb = _verts_skin_to_world(bsv, _shape_global_to_skin(base_shape))
            try:
                body_tris = np.asarray(base_shape.tris, dtype=np.int64)
            except Exception:
                body_tris = None       # ray pairing falls back to KD
            nb = len(Vb)
            body_pv = [dict() for _ in range(nb)]
            for b, pairs in (base_shape.bone_weights or {}).items():
                for vi, w in pairs:
                    iv = int(vi)
                    if 0 <= iv < nb:
                        body_pv[iv][b] = body_pv[iv].get(b, 0.0) + float(w)
            tree = _KD(Vb)
        except Exception:
            tree = None
            body_pv = None
    if tree is None or body_pv is None:
        weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
        ref = _nc()._body_conform_ref(weight)
        if ref is None:
            return 0
        Vb, body_pv, _body_bones, tree = ref
        if not ube_bones:
            # The row gate below needs a body-bone set. Without one every bone
            # reads as foreign and the gate would silently reject every row.
            ube_bones = set(_body_bones or ())

    total = 0
    dirty = False
    _ray_paired = 0          # rows re-paired by ray; 0 with pair_by_ray on is a BUG
    _lm_layered = _layered_cloth_shape_names(nf.shapes)
    # #layer-follow-divergence. Stacked layers have to resolve the body TOGETHER
    # or they stop deforming together. See _stacked_layer_groups for why the
    # name-based set above cannot see a semantically-named stack, and why simply
    # skipping such shapes was censused (66.6% of the pack) and rejected.
    #
    # Scoped to the full-vector instance on purpose: the four family passes
    # rescale ONE bone family and leave the rest of the row proportional, so
    # they decohere a stack far less, and each was validated in game as it
    # stands. Widening this to them is a separate change needing its own
    # measurement.
    _stack_plan: dict = {}
    if full_vector and _nc()._FULL_WEIGHT_LAYER_GUARD:
        try:
            _excl = (set(collider_names) | set(softbody_names)
                     | set(_nc().UBE_BODY_INJECT_NAMES) | set(_lm_layered)
                     | set(morph_tri_names))
            # #layer-group-canonical. Decide WHICH shapes stack once, on the
            # `_1` source, so `_0` and `_1` cannot disagree -- 51 of 812 stacked
            # pieces did. Fall back to the file in hand when there is no usable
            # source, rather than grouping nothing.
            _names = _canonical_stack_name_groups(src_nif_path, _excl)
            _sg = (_nc()._dst_groups_for_names(nf.shapes, _names, _excl)
                   if _names is not None
                   else _stacked_layer_groups(nf.shapes, exclude=_excl))
            if _sg:
                _stack_plan = _nc()._stacked_layer_plan(_sg, tree, ube_bones)
        except Exception as _e:
            # Never silent: a swallowed failure here is indistinguishable from
            # "no stack qualified", which is exactly how this pass's ray pairing
            # once became a no-op that read like a clean result.
            _note_pass_failure("_match_limb_motion_to_body/stack-plan", _e)
    for s in nf.shapes:
        if s.name == "BaseShape" or s.name in _nc().RESKIN_SKIP_NAMES:
            continue
        if (s.name in collider_names or s.name in softbody_names
                or s.name in morph_tri_names):
            continue
        if s.name in _lm_layered:
            # Layered cloth keeps its SOURCE skin; this was the only one of the
            # four sibling post-passes without the skip (audit 2026-07-28) --
            # rewriting a layer stack's leg split per-layer independently
            # reopens inter-layer clipping under hip flexion.
            continue
        # INERT-CHAIN ALLOWANCE (#inert-chain-leg-motion). `_shape_has_hdt_smp_rigging`
        # is a NIF-bone heuristic: >40% bones unknown to the body = "physics-rigged",
        # meant to stop a reskin from replacing chain bones that a runtime SMP config
        # drives. But a chain only matters if something DRIVES it, and that means an
        # XML. With no physics XML for this piece the chain bones are INERT -- the
        # garment is plain skinning that happens to carry unused bones, and refusing to
        # match it just leaves the leg walking through a skirt that never moves.
        # (The collider/softbody sets above are derived FROM the XML, so they already
        # self-disable when there is none -- this is the only gate that did not.)
        # Measured on a common-clothes dress left kinematic: 113 -> 37 newly-exposed
        # verts, bind-pose exposure unchanged, free-hem drape motion identical.
        # #smp-row-gate. When the shape-level heuristic fires, a family-scoped pass
        # does not have to give up on the whole shape: it can fall back to the SAME
        # test applied PER ROW -- rewrite only verts whose entire weight sits on
        # bones the BODY also has. Such a row provably carries no authored chain
        # bone, so the rescale cannot weaken a chain, whatever the shape-level
        # heuristic concluded about the shape as a whole.
        #
        # This is not hypothetical tidiness. On a vanilla cuirass the shape gate
        # fired on the MAIN GARMENT and cost the arm fix everything, and what it
        # was "protecting" on those rows was NPC L/R UpperarmTwist2 -- skeleton arm
        # bones, counted foreign only because the injected BaseShape declares 36
        # bones and the garment carries 59. The piece's XML drives Proxy/Collision
        # and nothing else; the garment shape is plain skinned geometry.
        #
        # Gating on "referenced by the physics XML" instead was MEASURED AND
        # REJECTED: `_xml_referenced_bone_names` harvests by any route including
        # constraint bodies, so a generated XML names 126 bones -- Clavicle, Calf,
        # Forearm included -- and the gate excluded 1601 of 1601 rows. A silent
        # no-op, not a safer gate. Do not re-attempt it.
        #
        # OPT-IN per family: the arm pass uses it, the leg pass keeps the plain
        # shape-wide skip it shipped and was validated with. Widening the leg
        # pass's reach is a separate change that needs its own measurement.
        _row_gate = False
        if _piece_has_physics_xml:
            try:
                if _nc()._shape_has_hdt_smp_rigging(s, ube_bones):
                    if not smp_row_gate:
                        continue
                    _row_gate = True
            except Exception:
                pass
        try:
            bw = s.bone_weights or {}
            shape_bones = list(bw.keys())
            managed = [b for b in bones if b in bw]
            # A full-vector match manages every SHARED bone, so it does not need
            # the caller's family to be present on the shape.
            if (not managed and not full_vector) or len(shape_bones) < 2:
                continue
            sv = np.asarray(s.verts, dtype=np.float64)
            n = len(sv)
            if n == 0:
                continue
            wv = _verts_skin_to_world(sv, _shape_global_to_skin(s))
            # #layer-follow-divergence -- resolve the body through the stacked
            # group's shared anchor rather than this shape's own surface.
            _plan = _stack_plan.get(s.name)
            if (_plan is not None and _nc()._LAYER_STACK_SHARED_BASIS
                    and not _plan["basis"]):
                # Shared-basis mode only: the group shares no body bone to copy
                # onto, so any row written here would be renormalised over a
                # different set per member -- which IS the defect.
                continue
            # THE SHARED ANCHOR APPLIES ONLY WHERE THE LAYERS OVERLAP.
            # #layer-anchor-local. Substituting it for EVERY vert of a grouped
            # shape is what destroyed arm follow in game: a sleeve vert borrowed
            # the nearest point on the corset -- a torso anchor for arm geometry
            # -- and `top` ARM went 3840.6 -> 2928.3, `chest_plate` 73.0 -> 0.3.
            # `near_ok` is the subset actually within stacking distance of the
            # innermost member; everything else keeps its own surface, so a
            # sleeve still pairs to the arm while the overlapping verts still
            # agree with the layer beneath them.
            _qv = wv
            if _plan is not None:
                _ok = np.asarray(_plan["near_ok"], dtype=bool)
                if _ok.any():
                    _qv = wv.copy()
                    _qv[_ok] = np.asarray(_plan["pos"], dtype=np.float64)[_ok]
            dist, near = tree.query(_qv, k=1)
            band = ((wv[:, 2] >= z_lo) & (wv[:, 2] <= z_hi)
                    & (dist <= max_dist))
            if _plan is not None:
                # Conservative on BOTH distances. The shared anchor can hug the
                # body while THIS layer hangs well off it, and a free-hanging
                # outer layer must not be dragged onto the body's motion just
                # because the layer underneath it is in contact.
                band &= np.asarray(tree.query(wv, k=1)[0]) <= max_dist
            if lateral_half_x > 0.0:
                band &= np.abs(wv[:, 0]) >= lateral_half_x
            # ABOVE THE SHOULDER, "near the body" IS NOT "touching it".
            # REPORTED IN GAME after the first full-vector deploy: "the leather
            # protrusion above the shoulder moves in weird ways". Cause, measured
            # on that piece: at z>=103 the pass put `NPC L/R UpperArm` weight on
            # verts that had 0.000 of it and took it off Clavicle/UpperarmTwist1,
            # so a standing decorative plate started swinging with the arm.
            #
            # The shoulder band is BIMODAL -- standoff p50 0.69u but p90 3.2-3.5u
            # and max 7.5u -- because that is where garments carry pauldrons,
            # straps and raised trim over a very convex shoulder, and
            # nearest-vertex distance overstates contact there. One threshold
            # cannot serve both: 5.0u admits the protrusion (104 proud verts
            # rewritten), and tightening it EVERYWHERE to 2.0u protects the
            # shoulder but hands back most of the bust win (breast region 3.12%
            # -> 27.01%) because bust cloth legitimately sits 2.2u out.
            # So the tighter gate applies ONLY above the shoulder.
            if shoulder_z > 0.0 and shoulder_max_dist > 0.0:
                band &= (wv[:, 2] < shoulder_z) | (dist <= shoulder_max_dist)
            if not band.any():
                continue

            # WHICH BODY POINT DOES THIS GARMENT VERT COVER? `near` above is
            # KD-NEAREST, and nearest is not covered. On a torso the two disagree
            # wherever the surface folds toward itself -- the armpit, the breast
            # crease, the waist -- and there the closest body vert belongs to a
            # DIFFERENT anatomy than the one the garment is actually in front of.
            # Matching to it copies the wrong bone's weight and the pass corrects
            # a mismatch the vert does not have. Already measured on the upper
            # back: KD targets skin the vert does not cover, while a ray along the
            # normal pairs 73.3%.
            #
            # Cast the vert's INWARD normal at the body and take the body point at
            # the HIT. Reuses the shipped `_ClipTester` (the project's validated
            # ray test) rather than a second implementation, and a miss keeps the
            # KD answer -- so this can only ever re-pair a row, never drop one.
            if pair_by_ray and body_tris is not None and len(body_tris):
                try:
                    _rows = np.flatnonzero(band)
                    # Cast from the stacked group's shared anchor when there is
                    # one. Casting from each layer's own position along its own
                    # normal is what makes two stacked layers hit different body
                    # triangles, and that is the whole residual divergence of
                    # the worst measured pair. #layer-follow-divergence
                    _gn = _nc()._vertex_normals_from_tris(
                        wv, np.asarray(s.tris, dtype=np.int64))
                    _src = wv
                    if _plan is not None:
                        # Same locality rule as the KD query above: cast from
                        # the shared anchor ONLY on the overlapping verts, or a
                        # sleeve fires its ray from a point on the torso.
                        _ok2 = np.asarray(_plan["near_ok"], dtype=bool)
                        if _ok2.any():
                            _gn, _src = _gn.copy(), wv.copy()
                            _gn[_ok2] = np.asarray(
                                _plan["nrm"], dtype=np.float64)[_ok2]
                            _src[_ok2] = np.asarray(
                                _plan["pos"], dtype=np.float64)[_ok2]
                    _O = _src[_rows]
                    _D = -_gn[_rows]
                    _dl = np.linalg.norm(_D, axis=1)
                    _ok = _dl > 1e-9
                    if _ok.any():
                        _rows, _O, _D = _rows[_ok], _O[_ok], _D[_ok] / _dl[_ok, None]
                        # Reach past the hug distance: the vert stands off the
                        # body by up to max_dist and the ray starts at the vert.
                        _tester = fit_metrics._ClipTester(
                            Vb, body_tris, tmax=float(max_dist) + 2.0)
                        _hit = np.asarray(fit_metrics.cast_chunked(
                            _tester, _O, _D, finite_only=False), dtype=np.float64)
                        if len(_hit) == len(_rows):
                            _fin = np.isfinite(_hit)
                            if _fin.any():
                                _P = _O[_fin] + _D[_fin] * _hit[_fin][:, None]
                                _, _n2 = tree.query(_P, k=1)
                                near = np.asarray(near).copy()
                                near[_rows[_fin]] = _n2
                                _ray_paired += int(_fin.sum())
                except Exception as _re:
                    # NEVER swallow this silently. The first version wrote
                    # `except Exception: pass` and referenced `_fm`, which is a
                    # LOCAL import in a different function -- so every call raised
                    # NameError, fell back to KD, and produced a byte-identical
                    # mesh that read exactly like "ray pairing changes nothing".
                    # A swallowed exception is indistinguishable from "nothing
                    # qualified"; make it observable.
                    _note_pass_failure("_match_limb_motion_to_body/ray-pair", _re)

            # #covered-skin-target: a vertex that covers skin is matched to that
            # skin's shares, not to the ONE body vertex nearest to it (or the ray
            # hit). This pass may only RAISE a family share, and raising a crotch
            # panel toward the inner thigh's share is what re-armed the reported
            # gusset (0.421 -> 0.463) after the reskin had already put it there.
            _cov_t: dict = {}
            if _nc().COVERED_SKIN_TARGET and body_tris is not None and len(body_tris):
                try:
                    _nb = _nc()._vertex_normals_from_tris(Vb, body_tris)
                    _zb, _xb = Vb[:, 2], Vb[:, 0]
                    _cband = ((_zb >= _nc()._COVER_Z_LO) & (_zb <= _nc()._COVER_Z_HI)
                              & (np.abs(_xb) < _nc()._COVER_X))
                    _cover, _clr = _covered_skin_map(wv, Vb, _nb, _cband,
                                                     _nc()._COVER_REACH)
                    for _gi, _bis in _cover.items():
                        if len(_bis) < _nc()._COVER_MIN_VERTS:
                            continue
                        _t = _covered_skin_target(_bis, _clr, body_pv, _nc()._COVER_EPS)
                        if _t:
                            _cov_t[_gi] = _t
                except Exception as _ce:
                    _note_pass_failure("_match_limb_motion_to_body/covered-skin", _ce)
            G = np.zeros((n, len(shape_bones)), dtype=np.float64)
            for j, b in enumerate(shape_bones):
                for vi, w in bw[b]:
                    iv = int(vi)
                    if 0 <= iv < n:
                        G[iv, j] += float(w)
            tot = G.sum(axis=1)
            live = tot > 1e-6
            G[live] /= tot[live, None]

            if full_vector:
                # FULL-VECTOR MATCH (#full-weight-match). Copy the covered body's
                # ENTIRE weight vector, blended by `strength`, instead of fixing
                # one bone family and rescaling everything else proportionally.
                #
                # WHY. A family match cannot avoid a trade, and the numbers say so
                # plainly: on this garment the median garment-vs-body L1 gap over
                # the 17 shared bones is 0.507, spread across UpperArm 0.081/0.076,
                # Spine2 0.063, UpperarmTwist1 0.059/0.057, Pelvis 0.057, Spine1
                # 0.036, Clavicle 0.023. The spine family is ~0.10 of that. So
                # correcting the spine and smearing the remainder proportionally
                # funds the fix out of the Pelvis and the Clavicle -- measured,
                # SPINE +0.040 while Pelvis -0.026 and Clavicle -0.004 -- and the
                # one pose that leans, swings the arms and drives the hips at once
                # gets worse. The ideal of THIS operation is follow = 1.0 in every
                # pose at once, so it has no trade in it by construction.
                #
                # NO add_bone, same as the family path: only bones the shape
                # already has. That costs nothing here -- measured on the clean
                # rows, the body's weight on bones the garment LACKS is mean
                # 0.0006, p90 0.0000, no row above 0.10 (all of it `NPC Head`).
                #
                # THE CLEAN-ROW GATE IS MANDATORY HERE, not conditional as it is
                # for the family path: blending toward a body that has no chain
                # bone would drain an authored chain to zero. A row carrying any
                # weight on a bone the body lacks is left alone.
                # THE COPY BASIS (#layer-follow-divergence). Every bone the body
                # has, unless the opt-in shared-basis mode narrows it to what a
                # stacked group has in common -- see _LAYER_STACK_SHARED_BASIS
                # for the counter-metric that keeps that OFF.
                _fv_basis = (_plan["basis"]
                             if (_plan is not None and _nc()._LAYER_STACK_SHARED_BASIS)
                             else ube_bones)
                BF = np.zeros((n, len(shape_bones)), dtype=np.float64)
                for _j, _b in enumerate(shape_bones):
                    if _b in _fv_basis:
                        BF[:, _j] = [body_pv[int(i)].get(_b, 0.0) for i in near]
                # THE BASIS MUST EXPLAIN THE BODY, NOT JUST BE NON-EMPTY.
                # `_bs` is the share of the body's row that the basis captures
                # (a body row sums to 1), and the line below rescales whatever
                # it captured up to 1. At 1e-6 that is a licence to take 10% of
                # a vert's motion and present it as all of it: on a shoulder
                # vert whose body row is mostly Clavicle/UpperArm, a basis of
                # spine bones alone would write FULL spine weight there.
                # Harmless while the basis was every body bone -- the pass
                # measured the shortfall at mean 0.0006 -- but #layer-follow-
                # divergence narrows the basis to what a stacked group shares,
                # so the weak gate became reachable. Below the floor the vert
                # keeps its authored row, which is the conservative answer.
                for _gi, _t in _cov_t.items():
                    for _j, _b in enumerate(shape_bones):
                        if _b in _fv_basis:
                            BF[_gi, _j] = _t.get(_b, 0.0)
                _bs = BF.sum(axis=1)
                _okb = _bs > max(_nc()._FULL_WEIGHT_BASIS_MIN, 1e-6)
                BF[_okb] /= _bs[_okb, None]
                foreign = np.zeros(n, dtype=np.float64)
                for _j, _b in enumerate(shape_bones):
                    if _b not in ube_bones:
                        foreign += G[:, _j]
                # LIMB-BOUNDARY GATE (#full-weight-limb-boundary). Refuse a match
                # whose body vertex is ARM-dominated unless the garment vert is
                # arm geometry too. KD-nearest crosses the armpit: in the A-pose
                # the upper arm hangs beside the chest, so chest-plate verts get
                # paired to the ARM and this copy would hand them the arm's whole
                # row -- measured up to 0.98 arm weight on plate at bust height.
                # Clavicle is deliberately NOT arm here (`_is_arm_hand_bone`): a
                # chest plate legitimately follows the clavicle.
                # No `else:` branch on purpose -- default-then-narrow. The
                # sibling invariant test slices this branch at the first `else:`
                # after `if full_vector:`, so an inner one silently truncated
                # what it was checking and it failed on a guard that was still
                # there. (That test now slices on a stable marker too.)
                _limb_ok = np.ones(n, dtype=bool)
                _arm_cols = [_j for _j, _b in enumerate(shape_bones)
                             if _is_arm_hand_bone(_b)]
                if _arm_cols and _nc()._FULL_WEIGHT_LIMB_MAX > 0.0:
                    _body_arm = BF[:, _arm_cols].sum(axis=1)
                    _garm_arm = G[:, _arm_cols].sum(axis=1)
                    _limb_ok = ((_body_arm <= _nc()._FULL_WEIGHT_LIMB_MAX)
                                | (_garm_arm > _nc()._FULL_WEIGHT_LIMB_MAX))
                _sel = band & live & _okb & (foreign <= 1e-4) & _limb_ok
                rows = np.where(_sel)[0]
                if len(rows) == 0:
                    continue
                NEW = G.copy()
                NEW[rows] = (1.0 - strength) * G[rows] + strength * BF[rows]
                if _nc().BREAST_FOLLOW_KEEP:
                    # CULPRIT 2 of the authored-breast-follow loss. The family
                    # path below is push-up only by construction (np.maximum);
                    # this branch manages EVERY shared bone and so happily
                    # LOWERS breast to the body's share. Restore the authored
                    # breast mass and fund it from the non-breast bones, which
                    # keeps each row summing to 1. #breast-follow-keep
                    _bcol = np.array([_j for _j, _b in enumerate(shape_bones)
                                      if _is_breast_bone(_b)], dtype=int)
                    if len(_bcol):
                        _ocol = np.array([_j for _j in range(len(shape_bones))
                                          if _j not in set(_bcol.tolist())],
                                         dtype=int)
                        _g_br = G[rows][:, _bcol].sum(axis=1)
                        _n_br = NEW[rows][:, _bcol].sum(axis=1)
                        _short = _n_br < _g_br - 1e-6
                        if np.any(_short) and len(_ocol):
                            _r = rows[_short]
                            _want = _g_br[_short]
                            _cur = _n_br[_short]
                            _oth = NEW[_r][:, _ocol].sum(axis=1)
                            # breast: rescale to the authored mass, or fall back
                            # to the garment's OWN distribution where the blend
                            # zeroed the family outright.
                            _blk = NEW[np.ix_(_r, _bcol)]
                            _safe = _cur > 1e-9
                            _blk[_safe] *= (_want[_safe] / _cur[_safe])[:, None]
                            _blk[~_safe] = G[np.ix_(_r[~_safe], _bcol)]
                            NEW[np.ix_(_r, _bcol)] = _blk
                            _fo = np.divide(1.0 - _want, _oth,
                                            out=np.ones_like(_oth),
                                            where=_oth > 1e-9)
                            NEW[np.ix_(_r, _ocol)] *= _fo[:, None]
            else:
                midx = [shape_bones.index(b) for b in managed]
                B = np.zeros((n, len(managed)), dtype=np.float64)
                for k, b in enumerate(managed):
                    B[:, k] = [body_pv[int(i)].get(b, 0.0) for i in near]
                for _gi, _t in _cov_t.items():
                    for _k, _b in enumerate(managed):
                        B[_gi, _k] = _t.get(_b, 0.0)

                g_mass = G[:, midx].sum(axis=1)
                b_mass = np.clip(B.sum(axis=1), 0.0, 1.0)
                # push-up only: np.maximum, never lowers a share that already tracks
                target = np.maximum(
                    np.clip(g_mass + strength * (b_mass - g_mass), 0.0, 1.0),
                    g_mass)
                bsum = B.sum(axis=1)
                has_b = bsum > 1e-6
                shape_of = np.zeros_like(B)
                shape_of[has_b] = B[has_b] / bsum[has_b, None]
                # body vert carries no managed weight -> keep the garment's own split
                keep = ~has_b & (g_mass > 1e-6)
                if keep.any():
                    shape_of[keep] = (G[np.ix_(np.where(keep)[0], midx)]
                                      / g_mass[keep, None])

            # STRENGTH HAS TO REACH THE SPLIT, NOT ONLY THE MASS. `target` above
            # scales how far the family TOTAL travels toward the body's; the split
            # was then applied at FULL strength regardless. For a defect that IS a
            # split that made `strength` a knob with no effect on behaviour --
            # measured on the flank of a vanilla cuirass, where the garment holds
            # Spine1 0.167 against the body's 0.016 at a family total that already
            # nearly matches (0.819 vs 0.758):
            #
            #   strength   max weight delta vs 1.0   flank exposure, every pose
            #     0.50            0.238                     IDENTICAL
            #     0.25            0.378                     IDENTICAL
            #
            # Large weight movement, byte-identical outcome, because what a
            # garment FOLLOWS is which bone of the chain carries its mass. Without
            # this there is no partial setting to trade with -- the pass is all or
            # nothing, and a family match that helps one pose family and hurts
            # another has no middle to look for.
                # Inert at strength 1.0, which is what all three shipped instances use.
                if strength < 1.0:
                    live_g = g_mass > 1e-6
                    if live_g.any():
                        g_split = (G[np.ix_(np.where(live_g)[0], midx)]
                                   / g_mass[live_g, None])
                        shape_of[live_g] = ((1.0 - strength) * g_split
                                            + strength * shape_of[live_g])

            # NOTE: do NOT filter to rows where the limb share actually RISES. Most of
            # the benefit comes from RE-SPLITTING the limb mass a vert already has
            # across the family to match the body's split -- a vert whose total is
            # already correct can still be following the wrong bone of the family.
            # (Leg: thigh/calf/pelvis. Arm: the Clavicle-vs-UpperArm split, which is
            # the whole defect in the armhole.) Filtering
            # on `target > g_mass` skipped exactly those verts and the fix did nothing
            # (measured: 65 -> 65, versus 65 -> 18 once they were included).
                _sel = band & live & (g_mass > 1e-6)
                if _row_gate:
                    # See #smp-row-gate above. `foreign` is weight on any bone the
                    # body does not have -- an authored chain bone, or a skeleton bone
                    # the injected body simply does not declare. Either way we decline
                    # to touch the row rather than guess which it is.
                    foreign = np.zeros(n, dtype=np.float64)
                    for _j, _b in enumerate(shape_bones):
                        if _b not in ube_bones:
                            foreign += G[:, _j]
                    _sel &= foreign <= 1e-4
                rows = np.where(_sel)[0]
                if len(rows) == 0:
                    continue
                NEW = G.copy()
                NEW[np.ix_(rows, midx)] = shape_of[rows] * target[rows, None]
                other = [i for i in range(len(shape_bones)) if i not in midx]
                if other:
                    o_old = G[np.ix_(rows, other)].sum(axis=1)
                    o_new = 1.0 - target[rows]
                    sc = np.zeros(len(rows))
                    nzo = o_old > 1e-6
                    sc[nzo] = o_new[nzo] / o_old[nzo]
                    NEW[np.ix_(rows, other)] = G[np.ix_(rows, other)] * sc[:, None]

            # 4-INFLUENCE CAP, APPLIED HERE ON PURPOSE. Matching to the body's split
            # can give a vert a 5th influence, and Skyrim's skin partition only holds
            # 4 -- the save then drops the SMALLEST and does NOT renormalise (measured
            # 2026-07-25: a 4-influence row given two extra bones came back with the
            # largest 4 summing 1.160), which scrambles the split we just computed
            # (measured: 65 -> 18 newly-exposed written, where the same weights in
            # memory gave 65 -> 0). Prune the SMALLEST ourselves and renormalise, so
            # what lands is deterministic and the mass we intended stays on the bones
            # we intended.
            #
            # P6 RETROFIT ATTEMPTED AND REVERTED 2026-07-25 -- see
            # DESIGN_P6_WEIGHT_WRITE_INVARIANT.md. Replacing the cap+floor pair below
            # with `weights.plan_weight_writes` (prune to 4, renormalise, clear the
            # dropped influences with an explicit 0.0) is theoretically cleaner and
            # its unit tests pass, but on REAL meshes it appeared to make the
            # on-disk invariant WORSE, not better: traced heavy cuirass bad-sum
            # 1650 -> 1966, worst deviation +0.074 -> +0.130 (the pass does fire,
            # 1776 verts).
            #
            # TREAT THAT 1650 -> 1966 AS VOID. Those are the two numbers the
            # project later identified as a BATCH-produced output compared
            # against a SINGLE-MESH conversion -- mismatched arms, so the
            # "regression" was a harness artifact and not a result. The advice
            # attached to it ("do not re-attempt without first finding which
            # later pass rewrites these rows") is still GOOD advice, and it was
            # followed: a per-pass trace on 2026-08-12 showed the drift is
            # introduced HERE, that `_match_full_weights_to_body` is the last
            # weight pass, and that nothing downstream repairs it. The fix that
            # came out of it is `#family-weight-invariant` below -- which is
            # deliberately NOT this prune-and-clear design, because the real
            # mechanism turned out to be different again (a bone the VERTEX has
            # no room for, not a row over the cap).
            if NEW.shape[1] > _nc()._SKIN_MAX_INFLUENCES and len(rows):
                sub = NEW[rows]
                cut = np.argsort(sub, axis=1)[:, :-_nc()._SKIN_MAX_INFLUENCES]
                np.put_along_axis(sub, cut, 0.0, axis=1)
                ssum = sub.sum(axis=1)
                good = ssum > 1e-6
                sub[good] /= ssum[good, None]
                NEW[rows] = sub

            # #legmotion-normalise -- TWO invariants, both learned the hard way.
            #
            # (1) NEVER DROP AN EXISTING (bone, vert) WEIGHT TO ZERO.
            # `setShapeWeights` MERGES: it only updates the pairs you pass, so a
            # vertex you OMIT keeps its previous value. Zeroing a weight by leaving
            # it out therefore does not remove it -- the stale value survives the
            # save and the vertex ends up OVER-weighted (measured on this pass:
            # weight sums up to 1.67). A vertex whose bone weights do not sum to 1
            # is transformed by a partial/inflated sum of its bone matrices, so it
            # drifts off, dragging long near-degenerate triangles that flicker with
            # VIEW ANGLE -- in game, "part of the armour is invisible head-on but
            # fine from the side". So any bone the vert ALREADY had is floored just
            # above the write threshold and always written back.
            #
            # (2) Then renormalise every touched row, unconditionally -- no branch
            # above may skip it.
            if len(rows):
                _sub = NEW[rows]
                _had = G[rows] > 1e-4
                _sub = np.where(_had & (_sub <= _nc()._WRITE_MIN), _nc()._WRITE_MIN * 2.0, _sub)
                _ss = _sub.sum(axis=1)
                _ok = _ss > 1e-6
                _sub[_ok] /= _ss[_ok, None]
                # a row that lost ALL weight would skin to the origin -- restore it
                # rather than ship a spike.
                if (~_ok).any():
                    _sub[~_ok] = G[rows][~_ok]
                NEW[rows] = _sub

            # #family-weight-invariant. BOTH blocks above only touch `rows`, but
            # the write below writes EVERY vert -- so an UNTOUCHED row carrying
            # more than four influences reaches the save uncapped, the save keeps
            # its largest four and does not renormalise, and the vertex ships
            # under-weighted. Traced on the reported piece: this pass alone adds
            # 218 bad-sum verts to one shape, worst 0.140, and it is the LAST
            # weight pass, so nothing downstream repairs them. In game that is a
            # vertex transformed by a deflated sum of its bone matrices -- it
            # drifts off and drags a spike, which is what "broken verts" is.
            #
            # Renormalise ONLY the four that will survive, and leave the rest of
            # the row alone. That is what makes this safe where the sibling fix
            # in `_match_rigid_leg_bend_to_body` needed a provisional palette:
            # nothing is removed and no explicit 0.0 is written, so no bone can
            # be left in the shape with an empty weight list
            # (`#zeroweight-bone-desync`, an equip CTD). The surviving four only
            # ever grow, so they are still the largest four and the save's choice
            # is unchanged -- this alters the shipped WEIGHTS, never the shipped
            # influence SET.
            #
            # Note the earlier `#legmotion-cap-then-floor` warning above, which
            # says a cap here measured WORSE (1650 -> 1966) and not to retry
            # without first finding which later pass rewrites these rows. Those
            # are the two numbers the project later identified as a BATCH output
            # compared against a SINGLE-MESH conversion -- a false regression from
            # mismatched arms, not a real one. The precondition is met regardless:
            # the per-pass trace that motivated this shows the drift is introduced
            # HERE and that no weight pass runs after it.
            if _nc().FAMILY_WEIGHT_INVARIANT and len(rows):
                # #family-weight-invariant -- RENORMALISE OVER THE INFLUENCES
                # THAT WILL ACTUALLY SURVIVE THE WRITE.
                #
                # A vertex holds at most four bones. The write below is
                # bone-by-bone and `setShapeWeights` MERGES, so each write is
                # arbitrated against a row that is still PART STALE -- a bone
                # this pass wants to ADD arrives while the old, larger values
                # of the bones it is meant to replace are still in place, loses
                # the four-way contest immediately, and is gone before those
                # get lowered. The row then ships missing exactly that weight,
                # while the bones that did land were scaled as a share of a
                # total that counted it.
                #
                # Traced on the reported piece, `top` vertex 1561: the pass
                # computes a perfect row (sum 1.0000) that hands 0.1400 to
                # `NPC R UpperArm`, a bone the vertex does not have; the vertex
                # ships at 0.8601 with `R Breast01` sitting at the 0.0002 floor
                # where the arm weight should be. 288 vertices on that shape,
                # none in the author's own mesh. In game: a vertex placed by a
                # deflated sum of its bones, dragging a spike.
                #
                # So do not hand weight to a bone that cannot take it. Keep the
                # influences the vertex already HAS, spend any free slots on the
                # strongest newcomers, and renormalise over that set. Nothing is
                # removed and no explicit 0.0 is written, so no bone can be
                # stranded with an empty weight list (`#zeroweight-bone-desync`,
                # an equip CTD) -- the alternative fix, freeing slots by zeroing
                # first, has to solve that and does not buy a better row.
                #
                # It is deliberately NOT "keep the four largest of NEW": that is
                # the rule the save applies, and applying it here reproduces the
                # bug, because the largest four can include a newcomer the
                # vertex has no room for.
                _sub = NEW[rows]
                _have = G[rows] > _nc()._WRITE_MIN
                _free = _nc()._SKIN_MAX_INFLUENCES - _have.sum(axis=1)
                _newb = (~_have) & (_sub > _nc()._WRITE_MIN)
                # rank newcomers by weight, strongest first
                _ord = np.argsort(-np.where(_newb, _sub, -np.inf), axis=1)
                _rank = np.empty_like(_ord)
                np.put_along_axis(
                    _rank, _ord,
                    np.broadcast_to(np.arange(_sub.shape[1]), _sub.shape), 1)
                _allow = _have | (_newb & (_rank < np.maximum(
                    _free, 0)[:, None]))
                _sub = np.where(_allow, _sub, 0.0)
                _ss = _sub.sum(axis=1)
                # A row left with nothing would skin to the origin: keep what
                # the pass had rather than ship a spike.
                _ok = _ss > 1e-6
                _sub[_ok] /= _ss[_ok, None]
                if (~_ok).any():
                    _sub[~_ok] = NEW[rows][~_ok]
                NEW[rows] = _sub

            # STBs: setShapeWeights can reset them, so save every bone we write and
            # restore afterwards. If any can't be read, skip this shape rather than
            # ship an identity-reset bone (an identity STB = origin spike = explosion).
            saved_stb = {}
            ok = True
            for b in shape_bones:
                try:
                    st = s.get_shape_skin_to_bone(b)
                except Exception:
                    st = None
                if st is None:
                    ok = False
                    break
                saved_stb[b] = st
            if not ok:
                continue
            # Writes EVERY live vert, not just `rows`: `G` is normalised, so this
            # incidentally repairs verts whose source weights never summed to 1.
            # Measured load-bearing -- narrowing it regressed the invariant.
            #
            # The write loop gets its OWN except with an STB restore: the outer
            # per-shape catch below would swallow a mid-loop failure with the
            # STBs still reset, and the half-written shape SHIPS if any other
            # shape marks the NIF dirty (audit 2026-07-28 -- the add_bone-STB
            # equip-explosion class via the exception path).
            try:
                for j, b in enumerate(shape_bones):
                    s.setShapeWeights(b, [(i, NEW[i, j]) for i in range(n)
                                          if NEW[i, j] > _nc()._WRITE_MIN])
            except Exception as _we:
                for b, st in saved_stb.items():
                    try:
                        s.set_skin_to_bone_xform(b, st)
                    except Exception as _pe:
                        _note_pass_failure("set_skin_to_bone_xform", _pe)
                print(f"  WARN: {family}-motion weight write failed mid-shape on "
                      f"{s.name!r} ({_we!r}) -- STBs restored, shape left "
                      f"partially matched", file=sys.stderr)
                continue
            for b, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(b, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            total += len(rows)
            dirty = True
        except Exception as _se:
            # Was `continue`. The weight-write handler above restores the STBs,
            # prints a WARN and records -- but anything failing OUTSIDE that
            # inner try (building `rows`, reading weights) skipped the whole
            # SHAPE mutely, and a shape that never got limb-motion matched looks
            # exactly like a shape that did not need it.
            _note_pass_failure(f"{family}-motion/shape:{s.name}", _se, dst_path)
            continue
    if dirty:
        # A re-save must never silently un-hide an SMP collision proxy.
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            # See _match_rigid_leg_bend_to_body: `return 0` already means "no
            # rows matched", so a swallowed save reads as a clean no-op.
            _note_pass_failure("_match_limb_motion_to_body/save", _se, dst_path)
            return 0
    return total

def _bone_side(name: str) -> "str | None":
    """'L' or 'R' for a sided bone -- a standalone L / R token, as in
    `NPC L Thigh [LThg]` or `L Breast01` -- and None for a midline bone
    (`NPC Pelvis [Pelv]`, and `NPC LB Anus2`, whose token is LB). #part-pair-bilateral"""
    toks = (name or "").replace("[", " ").split()
    if "L" in toks:
        return "L"
    if "R" in toks:
        return "R"
    return None


def _part_is_bilateral(rows, idx, frac: float, sample: int = 600) -> bool:
    """Does this part sit on BOTH sides of the body?

    True when at least `frac` of its verts put more than half their weight on
    LEFT-sided bones AND at least `frac` put more than half on RIGHT-sided ones.
    Pass the AUTHOR's rows: the answer is a property of the garment, and no pass
    of ours should be able to change it. Large parts are sampled evenly.
    #part-pair-bilateral"""
    idx = np.asarray(idx)
    if not len(idx):
        return False
    if len(idx) > sample:
        idx = idx[np.linspace(0, len(idx) - 1, sample).astype(int)]
    n_l = n_r = 0
    for vi in idx:
        left = right = 0.0
        for b, w in rows[int(vi)].items():
            sd = _bone_side(b)
            if sd == "L":
                left += w
            elif sd == "R":
                right += w
        if left > 0.5:
            n_l += 1
        elif right > 0.5:
            n_r += 1
    need = max(1, int(np.ceil(frac * len(idx))))
    return n_l >= need and n_r >= need


def _match_coincident_cross_shape_skin(dst_path, src_nif_path=None) -> int:
    """Give cross-shape coincident verts ONE weight row. Returns verts unified.

    Runs LAST of the weight passes: every pass above pairs to the body per
    shape, so anything placed before them would simply be re-diverged. See the
    block comment for the measurements. #coincident-skin-match"""
    if not _nc().COINCIDENT_SKIN_MATCH or src_nif_path is None:
        return 0
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader NIF: a reload+re-save corrupts its controller -> CTD

    def _rows_of(shape, n):
        """Per-vert {bone: weight}. `bone_weights`, never `get_weights`
        ([[project_shape_weight_accessor]])."""
        out = [dict() for _ in range(n)]
        for b, prs in (shape.bone_weights or {}).items():
            for vi, w in prs:
                iv = int(vi)
                if 0 <= iv < n and float(w) > 0.0:
                    out[iv][b] = out[iv].get(b, 0.0) + float(w)
        return out

    # Authored physics geometry is off limits to every skin pass here.
    collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)

    cand = [s for s in nf.shapes
            if (s.name or "") not in _nc().RESKIN_SKIP_NAMES
            and (s.name or "") not in collider_names
            and (s.name or "") not in softbody_names
            and getattr(s, "bone_weights", None) and len(s.verts) >= 3]
    if len(cand) < 2:
        return 0
    want = {s.name or "": len(s.verts) for s in cand}

    # THE AUTHOR'S ROWS ARE THE GATE. Vert indices survive conversion -- the
    # passes move positions and reweight, they do not renumber -- so a source
    # shape with the same name and vert count maps 1:1 (same pairing rule as
    # `_source_bust_weight_map`). Only the shapes still in play are read: the
    # source also carries the author's BODY, and parsing tens of thousands of
    # rows nothing can use is pure cost.
    src_rows: dict = {}
    try:
        snf = pyn.NifFile(filepath=str(src_nif_path))
        for ss in snf.shapes:
            nm = ss.name or ""
            if (want.get(nm) == len(ss.verts)
                    and getattr(ss, "bone_weights", None)):
                src_rows[nm] = _rows_of(ss, len(ss.verts))
    except Exception as _se:
        _note_pass_failure("_match_coincident_cross_shape_skin/source", _se)
        return 0
    if not src_rows:
        return 0

    ents: list = []          # keyed by INDEX: shape names are not unique
    for s in cand:
        sr = src_rows.get(s.name or "")
        if sr is None:
            continue         # no authored answer -> no gate -> leave it alone
        try:
            wv = _verts_skin_to_world(np.asarray(s.verts, dtype=np.float64),
                                      _shape_global_to_skin(s))
            rr = _rows_of(s, len(sr))
        except Exception:
            continue
        # `old` is filled in lazily, only for the rows actually rewritten --
        # snapshotting every row of every shape doubled the pass's memory for
        # the sake of the ~1% it ends up needing.
        ents.append({"s": s, "wv": wv, "rows": rr, "old": {},
                     "pal": set(s.bone_weights or {}), "src": sr})
    if len(ents) < 2:
        return 0

    owner: list = []
    for k, e in enumerate(ents):
        owner.extend((k, i) for i in range(len(e["wv"])))
    try:
        allv = np.concatenate([e["wv"] for e in ents])
        pairs = cKDTree(allv).query_pairs(_nc()._COINCIDENT_SKIN_TOL,
                                          output_type="ndarray")
    except Exception:
        return 0
    if not len(pairs):
        return 0

    parent = list(range(len(owner)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    n_cut = 0
    joined: set = set()
    for a, b in pairs:
        a, b = int(a), int(b)
        ka, ia = owner[a]
        kb, ib = owner[b]
        if ka == kb:
            continue
        ra, rb = ents[ka]["src"][ia], ents[kb]["src"][ib]
        if sum(abs(ra.get(x, 0.0) - rb.get(x, 0.0))
               for x in set(ra) | set(rb)) > _nc()._COINCIDENT_SKIN_GATE:
            n_cut += 1
            continue          # the author drew a skin boundary here -- keep it
        joined.add(a)
        joined.add(b)
        pa, pb = find(a), find(b)
        if pa != pb:
            parent[pa] = pb
    # ---- A PART THE AUTHOR SKINNED RIGID MUST STAY RIGID --------------------
    #
    # REPORTED IN GAME after the cross-shape fix shipped: "the belts look better
    # but the buckles are still distorted / some verts are morphed in strange
    # ways". Correct, and it is the SAME defect one level in. Unifying only the
    # verts that TOUCH ACROSS shapes fixes a buckle's contact ring and leaves
    # the rest of the buckle on its own rows -- so the ring follows the belt,
    # the body of the buckle follows something else, and the part shears
    # INTERNALLY under motion. A rigid ornament has no business deforming at all.
    #
    # MEASURED on the shipped romper (max pairwise L1 WITHIN a welded part,
    # against the author's own value for that part):
    #
    #     part                 verts   AUTHOR   OURS
    #     6BraSideButtons_1       52    0.025   1.033
    #     6BraSideButtons_1       52    0.037   0.706
    #     3ButtonsWaist          364    0.180   0.859
    #     summed over 154 parts          60.15  89.31
    #
    # So the gate is the AUTHOR'S OWN RIGIDITY, not a size heuristic: a part the
    # author skinned as one unit is re-unified, a part they skinned to deform is
    # left alone. `3Fabric` reads 0.43-0.58 in the author and is correctly never
    # touched. Same "restore the authored relationship" move as the cross-shape
    # half, and it shares its cluster machinery -- so a buckle welded to its belt
    # ring ends up in ONE cluster with it, which is exactly the author's rig.
    #
    # PARTS ARE WELDED COMPONENTS (1e-3u), never raw topological ones: authors
    # split verts at hard edges, so one buckle is many islands
    # ([[project_component_is_not_an_object]]).
    _rigid_nodes: set = set()
    _soft_parts: list = []          # (ent, vert idx, the AUTHOR's own spread)
    _all_parts: list = []           # (ent, vert idx) -- every welded part
    if _nc().RIGID_PART_SKIN_MATCH:
        try:
            from scipy.sparse import coo_matrix as _coo
            from scipy.sparse.csgraph import connected_components as _cc
            _base = 0
            _offs = []
            for e in ents:
                _offs.append(_base)
                _base += len(e["wv"])
            for k, e in enumerate(ents):
                sv = np.asarray(e["s"].verts, dtype=np.float64)
                try:
                    tri = np.asarray(e["s"].tris, dtype=np.int64)
                except Exception:
                    continue
                if not len(tri):
                    continue
                ed = np.vstack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
                wp = cKDTree(sv).query_pairs(1e-3, output_type="ndarray")
                if len(wp):
                    ed = np.vstack([ed, wp])
                n = len(sv)
                _n, lab = _cc(_coo((np.ones(len(ed)), (ed[:, 0], ed[:, 1])),
                                   shape=(n, n)), directed=False)
                src_rows_k = e["src"]
                for pid in range(_n):
                    idx = np.flatnonzero(lab == pid)
                    if len(idx) < _nc()._RIGID_PART_MIN_VERTS:
                        continue
                    # The AUTHOR's spread inside this part decides. Sampled --
                    # the answer is a max over pairs and a 60-vert sample of a
                    # rigid part is still ~0.
                    smp = (idx if len(idx) <= 48
                           else idx[np.linspace(0, len(idx) - 1, 48).astype(int)])
                    worst = 0.0
                    for _a in range(len(smp)):
                        ra_ = src_rows_k[smp[_a]]
                        for _b in range(_a + 1, len(smp)):
                            rb_ = src_rows_k[smp[_b]]
                            d_ = sum(abs(ra_.get(x, 0.0) - rb_.get(x, 0.0))
                                     for x in set(ra_) | set(rb_))
                            if d_ > worst:
                                worst = d_
                            if worst > _nc()._RIGID_PART_GATE:
                                break
                        if worst > _nc()._RIGID_PART_GATE:
                            break
                    _all_parts.append((k, idx))
                    if worst > _nc()._RIGID_PART_GATE:
                        # The author meant this part to deform -- but not
                        # necessarily as much as we do. Hand it to
                        # `#author-deviation-skin` below instead of walking
                        # away. NOTE `worst` is TRUNCATED: the loop above breaks
                        # at the gate, so it is a lower bound, not the spread.
                        _soft_parts.append((k, idx, worst))
                        continue
                    root = int(idx[0]) + _offs[k]
                    for vi in idx[1:]:
                        gid = int(vi) + _offs[k]
                        _rigid_nodes.add(gid)
                        pa, pb = find(root), find(gid)
                        if pa != pb:
                            parent[pa] = pb
                    _rigid_nodes.add(root)
                    joined.add(root)
                    joined.update(int(v) + _offs[k] for v in idx[1:])
        except Exception as _re:
            _note_pass_failure(
                "_match_coincident_cross_shape_skin/rigid-part", _re)

    # ---- ADJACENT PARTS MUST TRAVEL TOGETHER (#part-pair-align) -----------
    #
    # REPORTED IN GAME: "the metal buckles along the top belts still move
    # incorrectly instead of staying solid" -- after both the cross-shape fix
    # and the rigid-part fix. Neither can see this defect. The cross-shape one
    # only unifies verts within 0.15u of each other; the rigid one only looks
    # WITHIN a part. A buckle can be internally rigid AND agree with its belt
    # where they touch, and still ride a different AVERAGE bone from the belt --
    # so the whole buckle slides across the whole belt as the skeleton moves.
    #
    # MEASURED on the shipped piece (`part_pair_offset.py`, L1 between the MEAN
    # rows of parts whose closest approach is <= 1u):
    #
    #     3BeltWaist / 3ButtonsWaist    author 0.273   ours 0.892
    #     3ButtonsWaist / 3LeatherMain  author 0.679   ours 1.120
    #     3Belts / 3ButtonsWaist        author 0.416   ours 0.834
    #     10 pairs exceed the author by >0.3; 472 pairs total
    #
    # THE OPERATION IS AN ADDITIVE SHIFT, NOT A BLEND. Each part is moved
    # bodily toward the pair's joint mean:  row_i += s * (M - mean_part).
    # That changes the part's MEAN while leaving every within-part difference
    # untouched, so it cannot stiffen the part -- which is exactly how the
    # blend-based `#rigid-part-cap` failed (it flattened cloth: 3Fabric 6.49 ->
    # 3.73 against the author). A shift whose components sum to zero also keeps
    # every row summing to 1 by construction.
    #
    # Runs BEFORE the cluster unification below, so the cross-shape agreement
    # gets the LAST word on verts that touch -- a shift applied afterwards would
    # pull those apart again and undo the fix it is built on top of.
    from collections import defaultdict as _dd
    changed: dict = _dd(set)
    if _nc().PART_PAIR_ALIGN and len(_all_parts) > 1:
        try:
            _bil_guard = _nc().PART_PAIR_BILATERAL_GUARD
            _pmean = []
            for k, idx in _all_parts:
                e = ents[k]
                mo: dict = _dd(float)
                ma: dict = _dd(float)
                for vi in idx:
                    for b, w in e["rows"][int(vi)].items():
                        mo[b] += w / len(idx)
                    for b, w in e["src"][int(vi)].items():
                        ma[b] += w / len(idx)
                bil = (_bil_guard and _part_is_bilateral(
                    e["src"], idx, _nc()._PART_PAIR_BILATERAL_FRAC))
                _pmean.append((k, idx, dict(mo), dict(ma),
                               cKDTree(e["wv"][idx]), bil))
            for _i in range(len(_pmean)):
                ki, ii, moi, mai, ti, bi = _pmean[_i]
                for _j in range(_i + 1, len(_pmean)):
                    kj, ij, moj, maj, tj, bj = _pmean[_j]
                    # adjacency, cheapest test first
                    d, _q = ti.query(ents[kj]["wv"][ij])
                    if d.min() > _nc()._PART_PAIR_NEAR:
                        continue
                    if bi and bj:
                        continue    # #part-pair-bilateral: neither has a mean to move
                    if bi or bj:
                        # #part-pair-bilateral. The two-sided part never moves.
                        # Its one-sided partner is judged against -- and moved
                        # toward -- the two-sided part's rows NEAR it, ours and
                        # the author's over the same verts. Moving one part the
                        # whole step closes the same share of the excess as two
                        # parts each moving half of it.
                        (kb, ib), (ks, iks, mos, mas, ts) = (
                            ((ki, ii), (kj, ij, moj, maj, tj)) if bi
                            else ((kj, ij), (ki, ii, moi, mai, ti)))
                        dl, _ql = ts.query(ents[kb]["wv"][ib])
                        loc = np.asarray(ib)[dl <= _nc()._PART_PAIR_LOCAL]
                        if not len(loc):
                            continue
                        eb = ents[kb]
                        mob: dict = _dd(float)
                        mab: dict = _dd(float)
                        for vi in loc:
                            for b, w in eb["rows"][int(vi)].items():
                                mob[b] += w / len(loc)
                            for b, w in eb["src"][int(vi)].items():
                                mab[b] += w / len(loc)
                        lo = sum(abs(mos.get(b, 0.0) - mob.get(b, 0.0))
                                 for b in set(mos) | set(mob))
                        la = sum(abs(mas.get(b, 0.0) - mab.get(b, 0.0))
                                 for b in set(mas) | set(mab))
                        if lo <= la + _nc()._PART_PAIR_MARGIN or lo <= 1e-6:
                            continue
                        s = 0.5 * (1.0 - (la + _nc()._PART_PAIR_MARGIN) / lo)
                        if s <= 0:
                            continue
                        _moves = ((ks, iks, mos, dict(mob)),)
                    else:
                        lo = sum(abs(moi.get(b, 0.0) - moj.get(b, 0.0))
                                 for b in set(moi) | set(moj))
                        la = sum(abs(mai.get(b, 0.0) - maj.get(b, 0.0))
                                 for b in set(mai) | set(maj))
                        if lo <= la + _nc()._PART_PAIR_MARGIN or lo <= 1e-6:
                            continue
                        s = 0.5 * (1.0 - (la + _nc()._PART_PAIR_MARGIN) / lo)
                        if s <= 0:
                            continue
                        _moves = tuple(
                            (kk, idxk, mk,
                             {b: 0.5 * (mk.get(b, 0.0) + other.get(b, 0.0))
                              for b in set(mk) | set(other)})
                            for (kk, idxk, mk, other) in (
                                (ki, ii, moi, moj), (kj, ij, moj, moi)))
                    for (kk, idxk, mk, M) in _moves:
                        e = ents[kk]
                        pal = e["pal"]
                        for vi in idxk:
                            vi = int(vi)
                            cur = e["rows"][vi]
                            row = {}
                            for b in set(cur) | set(M):
                                if b not in pal:
                                    continue
                                v = cur.get(b, 0.0) + s * (M.get(b, 0.0)
                                                           - mk.get(b, 0.0))
                                if v > _nc()._WRITE_MIN:
                                    row[b] = v
                            if len(row) > _nc()._SKIN_MAX_INFLUENCES:
                                row = dict(sorted(row.items(),
                                                  key=lambda kv: (-kv[1], kv[0]))
                                           [:_nc()._SKIN_MAX_INFLUENCES])
                            tot_r = sum(row.values())
                            if tot_r <= _nc()._WRITE_MIN:
                                continue
                            row = {b: w / tot_r for b, w in row.items()}
                            e["old"].setdefault(vi, cur)
                            e["rows"][vi] = row
                            changed[kk].add(vi)
        except Exception as _ae:
            _note_pass_failure(
                "_match_coincident_cross_shape_skin/part-pair", _ae)

    # ---- A PART DEFORMS THE WAY ITS AUTHOR MADE IT (#author-deviation-skin) --
    #
    # REPORTED IN GAME, after the cross-shape, rigid-part and part-pair fixes
    # had all shipped: "the metal buckles on the belts across the stomach --
    # SOME VERTS SHIFT BASED ON MOVEMENT WHILE OTHERS DON'T, making a distortion
    # during movement". That is neither of the defects above. The buckle agrees
    # with its belt where they touch, it rides the right average bone, and it is
    # still torn apart from the inside, because nothing bounds how far OUR rows
    # diverge WITHIN a part the author skinned semi-deformably -- above
    # `_RIGID_PART_GATE`, so the rigid branch deliberately walked away.
    #
    # `#rigid-part-cap` was built for exactly this and MEASURED ITS WAY TO A
    # REJECTION: aiming at the part mean aims at zero variation, so it fixed the
    # buckles by freezing the fabric. See its constant above for the numbers.
    # The fix is not a weaker cap, it is a different TARGET -- the author's own
    # per-vertex deviation, transplanted onto our mean. Rationale and the
    # measured result are on `AUTHOR_DEVIATION_SKIN`.
    #
    # ONLY EVER FIRES WHERE WE OVER-DEFORM (`ours > author + margin`), so a part
    # we already skin more smoothly than the author is left exactly as it is --
    # this pass may remove variation we invented, never add variation they did
    # not ask for.
    #
    # Runs BEFORE the cluster unification below, for the same reason
    # `#part-pair-align` does: the cross-shape agreement is the in-game-verified
    # fix this is built on top of, and it must get the LAST word on verts that
    # touch across shapes.
    #
    # Reads `_soft_parts`, so it is inert when `#rigid-part-skin` is off -- that
    # branch is what separates "the author meant this to deform" from "the
    # author meant this rigid", and rigid parts are already unified outright.
    n_devmatch = 0
    if _nc().AUTHOR_DEVIATION_SKIN and _soft_parts:
        try:
            for k, idx, _partial in _soft_parts:
                e = ents[k]
                rows_k, src_k, pal = e["rows"], e["src"], e["pal"]
                smp = (idx if len(idx) <= 48
                       else idx[np.linspace(0, len(idx) - 1,
                                            48).astype(int)])

                def _worst(rows):
                    w = 0.0
                    for _a in range(len(smp)):
                        ra_ = rows[smp[_a]]
                        for _b in range(_a + 1, len(smp)):
                            rb_ = rows[smp[_b]]
                            d_ = sum(abs(ra_.get(x, 0.0) - rb_.get(x, 0.0))
                                     for x in set(ra_) | set(rb_))
                            if d_ > w:
                                w = d_
                    return w

                # RECOMPUTED, NOT taken from `_soft_parts`. The spread stored
                # there stops as soon as it clears `_RIGID_PART_GATE` -- right
                # for a rigid/deformable verdict, and a TRUNCATED LOWER BOUND as
                # a number. Using it made this gate fire on parts already within
                # the author's margin, and it would have fired on more of the
                # pack than the census the defaults were chosen from.
                auth_spread = _worst(src_k)
                if _worst(rows_k) <= auth_spread + _nc()._AUTHOR_DEV_MARGIN:
                    continue
                a_set: set = set()
                o_set: set = set()
                for vi in idx:
                    a_set |= set(src_k[int(vi)])
                    o_set |= set(rows_k[int(vi)])
                if not a_set:
                    continue
                # A bone we cannot WRITE is not usable however well it matches:
                # weighting to a bone outside the shape's palette skins the
                # vertex to the origin ([[project_shape_weight_accessor]]).
                shared = (a_set & o_set) & pal
                if len(shared) < _nc()._AUTHOR_DEV_MIN_PALETTE * len(a_set):
                    continue
                mo: dict = _dd(float)
                ma: dict = _dd(float)
                for vi in idx:
                    for b, w in rows_k[int(vi)].items():
                        mo[b] += w / len(idx)
                    for b, w in src_k[int(vi)].items():
                        ma[b] += w / len(idx)
                base = {b: w for b, w in mo.items() if b in pal}
                if not base:
                    continue
                for vi in idx:
                    vi = int(vi)
                    cur = rows_k[vi]
                    row = dict(base)
                    for b in shared:
                        # Clamped at zero: a deviation deeper than our mean
                        # holds cannot be represented, and a negative weight is
                        # not a weight. The renormalise below absorbs it.
                        v = row.get(b, 0.0) + (src_k[vi].get(b, 0.0)
                                               - ma.get(b, 0.0))
                        row[b] = v if v > 0.0 else 0.0
                    row = {b: w for b, w in row.items() if w > _nc()._WRITE_MIN}
                    if len(row) > _nc()._SKIN_MAX_INFLUENCES:
                        # Total order, never weight alone -- symmetric bones tie
                        # EXACTLY. #deterministic-set-iteration
                        row = dict(sorted(row.items(),
                                          key=lambda kv: (-kv[1], kv[0]))
                                   [:_nc()._SKIN_MAX_INFLUENCES])
                    tot_r = sum(row.values())
                    if tot_r <= _nc()._WRITE_MIN:
                        continue
                    row = {b: w / tot_r for b, w in row.items()}
                    e["old"].setdefault(vi, cur)
                    rows_k[vi] = row
                    changed[k].add(vi)
                n_devmatch += 1
        except Exception as _de:
            _note_pass_failure(
                "_match_coincident_cross_shape_skin/author-deviation", _de)

    # Only nodes an accepted edge actually touched can be in a cluster of two or
    # more; walking every vertex here built one throwaway list per vertex on a
    # 163k-vert piece to find the ~1% that matter.
    groups = _dd(list)
    for g in sorted(joined):
        groups[find(g)].append(g)

    n_weak = 0
    for mem in groups.values():
        if len(mem) < 2:
            continue
        cluster = [owner[m] for m in mem]
        if len({k for k, _ in cluster}) < 2 and not (
                _rigid_nodes and any(m in _rigid_nodes for m in mem)):
            continue   # one shape and not an author-rigid part -- nothing to do
        basis = None
        for k, _i in cluster:
            basis = ents[k]["pal"] if basis is None else (basis & ents[k]["pal"])
        if not basis:
            n_weak += 1
            continue
        ro = [ents[k]["rows"][i] for k, i in cluster]
        if any(sum(w for b, w in r.items() if b in basis)
               < _nc()._COINCIDENT_SKIN_MIN_SHARE for r in ro):
            n_weak += 1
            continue
        avg: dict = _dd(float)
        for r in ro:
            for b, w in r.items():
                if b in basis:
                    avg[b] += w / len(ro)
        kept = {b: w for b, w in avg.items() if w > _nc()._WRITE_MIN}
        if len(kept) > _nc()._SKIN_MAX_INFLUENCES:
            # Total order, never weight alone: symmetric bones tie EXACTLY and
            # the survivor would follow set iteration order.
            # #deterministic-set-iteration
            kept = dict(sorted(kept.items(), key=lambda kv: (-kv[1], kv[0]))
                        [:_nc()._SKIN_MAX_INFLUENCES])
        tot = sum(kept.values())
        if tot <= _nc()._WRITE_MIN:
            n_weak += 1
            continue
        tgt = {b: w / tot for b, w in kept.items()}
        for k, i in cluster:
            e = ents[k]
            e["old"].setdefault(i, e["rows"][i])
            e["rows"][i] = dict(tgt)
            changed[k].add(i)

    total = 0
    dirty = False
    for k, e in enumerate(ents):
        ch = changed.get(k)
        if not ch:
            continue
        s, rr, old = e["s"], e["rows"], e["old"]
        n = len(e["wv"])
        # ---- NEVER TAKE A BONE'S LAST CARRIER -----------------------------
        #
        # The `write_bones` guard below is NOT sufficient, and the 2026-08-25
        # trace proved it: this pass emptied `NPC Belly` off a corset that held
        # it on TWENTY-FIVE vertices. The guard's reasoning is that a bone it
        # never writes is "left entirely alone" -- but the native skin buffer
        # holds only FOUR influences, so when the other four bones of a rebuilt
        # row ARE written, the unwritten bone is evicted anyway. Declining to
        # write a bone does not protect it.
        #
        # WHY THE BONE LEAVES THE ROW AT ALL: `basis` is the INTERSECTION of the
        # cluster's palettes, so a bone only ONE shape in the cluster carries is
        # dropped from the merged row on every vertex of that cluster. That is
        # correct for the merge -- the shared row has to be expressible in every
        # palette -- but it must not cost the bone its existence in the shape.
        #
        # The repair is the same one the other capping passes use: hand back the
        # ONE vertex where our own pre-pass row weighted the bone most, and let
        # that vertex keep the row it already had. It costs one vertex of the
        # unification and it never invents a weight. Note `ours` here is OUR
        # rows, not the author's, so a vertex given back keeps the conversion's
        # own reskin -- this cannot resurrect a bone an earlier pass retired.
        # #zeroweight-bone-desync
        pre = [old.get(i, rr[i]) for i in range(n)]
        chg = {int(i): rr[int(i)] for i in ch}
        if _restore_emptied_bones(pre, chg):
            for i in sorted(set(ch) - set(chg)):
                rr[int(i)] = old[int(i)]        # back to its pre-pass row
                ch.discard(i)
            if not ch:
                continue
        touched: set = set()
        removed: dict = _dd(set)
        for i in ch:
            o, w = old[i], rr[i]
            for b in set(o) | set(w):
                if abs(o.get(b, 0.0) - w.get(b, 0.0)) > _nc()._WRITE_MIN:
                    touched.add(b)
                # `setShapeWeights` is an UPDATE, not a REPLACE -- a vert simply
                # omitted KEEPS its old value, so a bone this row gives up has
                # to be written explicitly at 0.0
                # ([[project_setshapeweights_update_semantics]]).
                if o.get(b, 0.0) > _nc()._WRITE_MIN and w.get(b, 0.0) <= _nc()._WRITE_MIN:
                    removed[b].add(i)
        if not touched:
            continue
        # NEVER EMPTY A BONE, and decide it BEFORE writing anything: a bone left
        # in the shape's list but with no weighted vertex is absent from the
        # regenerated skin-partition palette, and the per-vert index then runs
        # past that palette on equip. Such a bone is left ENTIRELY alone -- both
        # its removals and its values -- rather than half-written.
        # #zeroweight-bone-desync
        write_bones = sorted(         # sorted: #deterministic-weight-write
            b for b in touched
            if any(rr[i].get(b, 0.0) > _nc()._WRITE_MIN for i in range(n)))
        if not write_bones:
            continue
        # STBs: setShapeWeights can reset them, so save every bone we write and
        # restore afterwards. If any cannot be read, skip this shape rather than
        # ship an identity-reset bone (identity STB = origin spike = explosion).
        saved_stb = {}
        ok = True
        for b in write_bones:
            try:
                st = s.get_shape_skin_to_bone(b)
            except Exception:
                st = None
            if st is None:
                ok = False
                break
            saved_stb[b] = st
        if not ok:
            continue
        try:
            # REMOVALS FIRST, IN THEIR OWN PASS. The native skin buffer holds
            # FOUR influences per vertex and `setShapeWeights` MERGES, so a bone
            # this row ADDS arrives while the bones it is replacing still hold
            # their OLD, larger values -- it loses the four-way contest
            # immediately and is gone before those get lowered, and the row then
            # ships light while the bones that did land were scaled as a share
            # of a total that counted it. Measured here before this was split
            # in two: vertex 655 of a waist belt shipped summing 0.9621 with its
            # `NPC L Thigh` 0.0379 simply missing. Zeroing first frees the slots,
            # so every newcomer has somewhere to land. #family-weight-invariant
            for b in write_bones:
                rem = removed.get(b)
                if rem:
                    s.setShapeWeights(b, [(i, 0.0) for i in sorted(rem)])
            for b in write_bones:
                s.setShapeWeights(b, [(i, rr[i][b]) for i in range(n)
                                      if rr[i].get(b, 0.0) > _nc()._WRITE_MIN])
        except Exception as _we:
            for b, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(b, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            print(f"  WARN: coincident-skin write failed mid-shape on "
                  f"{s.name!r} ({_we!r}) -- STBs restored, shape left "
                  f"partially unified", file=sys.stderr)
            continue
        for b, st in saved_stb.items():
            try:
                s.set_skin_to_bone_xform(b, st)
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
        # setShapeWeights writes the NATIVE skin buffer and leaves pynifly's
        # cached `bone_weights` stale; anything re-authoring from this in-memory
        # nf would copy the PRE-unification weights.
        s._weights = None
        total += len(ch)
        dirty = True
    if _flag("CBBE2UBE_COINCIDENT_SKIN_DEBUG", False):
        print(f"  [coincident-skin] shapes={len(ents)} edges-cut={n_cut} "
              f"clusters-skipped={n_weak} "
              f"parts-devmatched={n_devmatch} "
              f"verts-unified={total}", file=sys.stderr)
    if dirty:
        # A re-save must never silently un-hide an SMP collision proxy.
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            # `return 0` already means "nothing unified", so a swallowed save
            # would read as a clean no-op.
            _note_pass_failure("_match_coincident_cross_shape_skin/save",
                               _se, dst_path)
            return 0
    return total

def _cap_weight_roughness_to_author(dst_path, src_nif_path=None) -> int:
    """Stop the weight field ending up LOCALLY ROUGHER than the author's.

    THE DEFECT (user-reported in game, 2026-08-18, on a romper's leather panel
    above and below its abdomen belts): a vertex whose bone weights disagree
    with its own topological neighbours' while the author's agreed. It bends
    on a different spine level than the surface around it, so it travels the
    wrong way and pokes through the layer over it.

    THE MECHANISM, measured rather than assumed. Two hypotheses were tested and
    KILLED first: ray-pairing incoherence (the broken verts pair 0.07-1.18u
    from their neighbours' targets -- normal -- and the split across the piece
    was 0.252 vs 0.234, inert), and rows shipping light (zero verts under 0.999
    on the measured piece). What separates them is the 4-INFLUENCE CAP: the
    body-follow matches push 1359 of 1510 rows on that panel to four bones
    where the author used four on 792, and where the blended row holds a near
    tie for the fourth slot, neighbouring vertices keep DIFFERENT bones. The
    field is smooth in the mean and jagged at the tie -- exactly one vertex
    that "looks wrong".

    THE PROPERTY, class-level and author-relative: per vertex, the largest
    per-bone deviation from the mean of its topological neighbours' rows. Ours
    may exceed the author's by at most `AUTHOR_ROUGHNESS_ALLOW`; where it does,
    the row is blended toward the neighbourhood mean by the SMALLEST alpha that
    restores the allowance, then re-capped to four and renormalised. Scored
    against the AUTHOR, never against zero ([[feedback_baseline_not_author]]):
    driving roughness to zero would erase the author's panel edges.

    Self-limiting by construction -- a shape already no rougher than its author
    is not touched at all, which is the pass's own control. Measured on the
    reported piece, all 21 shapes: rough vertices (excess > 0.10) 830 -> 28,
    the reported leather panel 203 -> 5 with its worst 0.460 -> 0.136, while
    the belts, buckles and metal buttons -- already smooth -- took ZERO writes.

    WEIGHTS ONLY: no vertex moves, so every clearance result upstream stands.
    Off with CBBE2UBE_NO_AUTHOR_ROUGHNESS_CAP=1.  #author-roughness-cap
    """
    if not _nc().AUTHOR_ROUGHNESS_CAP or src_nif_path is None:
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
        snf = pyn.NifFile(filepath=str(src_nif_path))
    except Exception as _oe:
        _note_pass_failure("_cap_weight_roughness_to_author/open", _oe)
        return 0
    src_by_name = {s.name: s for s in snf.shapes}

    def _rows(shape):
        out = [dict() for _ in range(len(shape.verts))]
        for bn, pairs in (shape.bone_weights or {}).items():
            for i, w in pairs:
                if 0 <= i < len(out) and w > 0:
                    out[i][bn] = out[i].get(bn, 0.0) + float(w)
        return out

    def _nb_mean(rows, nbrs):
        if not nbrs:
            return {}
        acc: dict = {}
        for j in nbrs:
            for b, w in rows[j].items():
                acc[b] = acc.get(b, 0.0) + w
        k = float(len(nbrs))
        return {b: w / k for b, w in acc.items()}

    def _rough(row, mean_row):
        bones = set(row) | set(mean_row)
        return max((abs(row.get(b, 0.0) - mean_row.get(b, 0.0))
                    for b in bones), default=0.0)

    total = 0
    dirty = False
    for s in nf.shapes:
        a = src_by_name.get(s.name)
        if a is None or len(a.verts) != len(s.verts):
            continue            # reauthored/injected shape: no author to score
        try:
            tris = np.asarray(s.tris, dtype=np.int64).reshape(-1, 3)
        except Exception:
            continue
        n = len(s.verts)
        if n < 3 or not len(tris):
            continue
        ours, auth = _rows(s), _rows(a)
        adj: "list[set]" = [set() for _ in range(n)]
        for t0, t1, t2 in tris:
            adj[t0].update((t1, t2))
            adj[t1].update((t0, t2))
            adj[t2].update((t0, t1))
        changed: dict = {}
        for i in range(n):
            if not adj[i]:
                continue
            m_o = _nb_mean(ours, adj[i])
            r_o = _rough(ours[i], m_o)
            if r_o <= 0.0:
                continue
            r_a = _rough(auth[i], _nb_mean(auth, adj[i]))
            target = r_a + _nc().AUTHOR_ROUGHNESS_ALLOW
            if r_o <= target:
                continue
            alpha = min(1.0, max(0.0, 1.0 - target / r_o))
            blended = {}
            for b in set(ours[i]) | set(m_o):
                w = ((1.0 - alpha) * ours[i].get(b, 0.0)
                     + alpha * m_o.get(b, 0.0))
                if w > 1e-6:
                    blended[b] = w
            top = sorted(blended.items(), key=lambda kv: -kv[1])
            top = top[:_nc()._ROUGHNESS_MAX_INFLUENCES]
            tot = sum(w for _, w in top)
            if tot <= 0.0:
                continue
            new = {b: w / tot for b, w in top}
            if sum(abs(new.get(b, 0.0) - ours[i].get(b, 0.0))
                   for b in set(new) | set(ours[i])) / 2.0 > 1e-6:
                changed[i] = new
        if not changed:
            continue
        # `top[:_ROUGHNESS_MAX_INFLUENCES]` above is a CAP, and a cap can take a
        # bone's last carrier. MEASURED, not anticipated: the 2026-08-25 trace
        # caught this pass emptying `L Breast03` off a `Chain` shape that held
        # it on two vertices. Same rule as the other capping passes -- give that
        # one vertex back rather than invent a weight for it. #zeroweight-bone-desync
        _restore_emptied_bones(ours, changed)
        if not changed:
            continue
        # Same write contract as the coincident match: STBs saved and restored
        # around setShapeWeights, REMOVALS in their own pass first so a newcomer
        # has a free slot, and the stale pynifly weight cache dropped after.
        # STBs are saved around setShapeWeights and restored after. The reader
        # is `get_shape_skin_to_bone`, which returns None (it does not raise)
        # when an xform is missing -- and the FIRST version of this pass called
        # a method that does not exist behind a bare `except: continue`, so
        # every shape was skipped and the whole pass read as "nothing
        # qualified" while 2300 vertices were queued for repair. Record, never
        # swallow ([[feedback_method_traps]]).
        saved_stb: dict = {}
        skipped_for = None
        for b in (s.bone_names or []):
            try:
                st = s.get_shape_skin_to_bone(b)
            except Exception as _xe:
                _note_pass_failure("_cap_weight_roughness_to_author/stb", _xe)
                st = None
            if st is None:
                # `get_shape_skin_to_bone` returns None rather than raising, so
                # this branch is the SAME silent path that made v1 a no-op --
                # one layer down. Bailing the shape is correct (an unrestorable
                # STB would ship at identity = origin spike) but it must never
                # be silent: unrecorded, a whole shape's repair vanishes and
                # `total` still reads like "nothing qualified".
                skipped_for = b
                break
            saved_stb[b] = st
        if skipped_for is not None:
            print(f"  roughness cap: SKIPPED {s.name!r} -- cannot read the "
                  f"skin-to-bone xform for {skipped_for!r}, so "
                  f"{len(changed)} queued vert(s) were left unrepaired",
                  file=sys.stderr)
            continue
        write_bones = set()
        removed: dict = {}
        for i, new in changed.items():
            write_bones |= set(new) | set(ours[i])
            for b in set(ours[i]) - set(new):
                removed.setdefault(b, set()).add(i)
        try:
            for b in sorted(write_bones):
                rem = removed.get(b)
                if rem:
                    s.setShapeWeights(b, [(i, 0.0) for i in sorted(rem)])
            for b in sorted(write_bones):
                pairs = [(i, changed[i][b]) for i in sorted(changed)
                         if changed[i].get(b, 0.0) > _nc()._WRITE_MIN]
                if pairs:
                    s.setShapeWeights(b, pairs)
        except Exception as _we:
            for b, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(b, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            print(f"  WARN: roughness-cap write failed on {s.name!r} "
                  f"({_we!r}) -- STBs restored, shape left partly capped",
                  file=sys.stderr)
            continue
        for b, st in saved_stb.items():
            try:
                s.set_skin_to_bone_xform(b, st)
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
        s._weights = None
        total += len(changed)
        dirty = True
    if dirty:
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            _note_pass_failure("_cap_weight_roughness_to_author/save",
                               _se, dst_path)
            return 0
    return total

def _restore_emptied_bones(ours, changed) -> int:
    """Never let a weight pass take a bone's LAST vertex.

    A bone that stays in a shape's bone list while carrying no weight above the
    write threshold is left out of the regenerated skin-partition palette, so a
    per-vertex bone index can run past that palette -- an equip CTD
    (#zeroweight-bone-desync). `_match_coincident_cross_shape_skin` has always
    honoured this; the two 2026-08-24 passes did not, and the pack-wide gate
    `verify_zero_weight_bones.py` caught it on the first reconvert that shipped
    them: 19 newly-emptied bones across the 101 comparable files.

    ANY pass that CAPS a row to four influences can do this, not just one that
    removes a bone deliberately -- the cap evicts the lightest bone, and on the
    one vertex where that bone was the shape's last carrier the eviction empties
    it. Both new passes cap, so both need this.

    THE REPAIR IS TO LEAVE THAT VERTEX ALONE, not to re-add a token weight: a
    synthesised weight is an invention, while the row already there is correct
    and already sums to 1. Dropping the vertex from `changed` costs one vertex
    of the pass's effect and keeps the palette sound.

    `ours` IS OUR OWN PRE-PASS ROWS, NOT THE AUTHOR'S, and the distinction
    matters. A vertex handed back keeps whatever the conversion had already made
    of it, so this can never resurrect a bone an earlier pass deliberately
    retired, and never undoes a reskin -- it only declines THIS pass's change on
    one vertex. (There is no CBBE->UBE bone renaming to worry about either:
    measured 2026-08-25, the two rigs share 45 bone names at identical bind
    positions, max deviation 0.0002u; only hands, feet and UpperarmTwist2 are
    CBBE-side-only, and those are excluded from reskinning anyway by
    `RESKIN_PRESERVE_BONE_KEYWORDS`.)

    Mutates `changed` in place; returns how many bones were rescued.
    """
    if not changed:
        return 0
    bones = set()
    for r in ours:
        bones |= set(r)
    for r in changed.values():
        bones |= set(r)
    rescued = 0
    for b in sorted(bones):
        if any((changed.get(i, ours[i])).get(b, 0.0) > _nc()._WRITE_MIN
               for i in range(len(ours))):
            continue                      # still carried somewhere: fine
        # Give back the vertex where OUR pre-pass row weighted it most. (This
        # comment said "the AUTHOR-side row" until 2026-08-25 while the code
        # read `ours` -- a mismatch that made the helper look like it restored
        # authored weights, which would have been unsafe across the reskin.)
        best_i, best_w = -1, _nc()._WRITE_MIN
        for i in changed:
            w = ours[i].get(b, 0.0)
            if w > best_w:
                best_i, best_w = i, w
        if best_i < 0:
            continue     # it was already empty before this pass: not ours
        del changed[best_i]
        rescued += 1
    return rescued

def _hold_weights_at_smp_boundary(dst_path, src_nif_path=None) -> int:
    """Stop a reskinned layer shearing away from the SMP layer it touches.

    THE DEFECT, reported in game twice: "disconnect between the belt and
    abdomen ... they are weighted wrong between layers", then again after the
    2026-08-24 reconvert, "weight issues especially near the abdomen". On a
    MIXED-CLOTH piece the two layers of ONE garment end up rigged to different
    bones at the same body height, so they travel opposite ways the moment the
    actor bends.

    THE MECHANISM, measured on the reported cuirass. Vert counts are identical
    there, so every shape is scored against its own author BY INDEX and no
    matching heuristic is involved:

        shape        verts   mean L1 vs author   >0.5   declared in the XML?
        Plane.001     9836        0.0000            0   per-vertex-shape
        ColLegs        539        0.0000            0   per-triangle-shape
        Plane.002     9504        0.3956         2248   no
        awdaw.012    18228        0.1841         1692   no

    The shapes at EXACTLY zero are exactly the shapes the physics XML declares.
    That is `_hdt_softbody_shape_names` working as designed -- an SMP soft body
    must keep its authored rig or it un-anchors and drifts. But the layer
    against it has no such protection and is fitted onto the UBE body, and the
    seam between them is the waist: 1327 of `Plane.002`'s 2614 abdomen verts
    land past 0.5 from the author. Per bone over z 70-90 the two layers moved
    in OPPOSITE directions -- Spine -> Spine2 (+0.117) against Spine/Spine1 ->
    Pelvis (+0.137). Row sums stay a clean 1.0000: nothing is malformed, the
    ASSIGNMENT split.

    THE PROPERTY, class-level: a vertex sitting against an immovable layer may
    not drift from the author either. Weights ramp back to the author's over
    `_SMP_HOLD_NEAR`..`_SMP_HOLD_FAR` from the nearest protected surface -- a
    FALLOFF, not a cap, because a hard cap only relocates the shear to the edge
    of the capped region. Body-follow is left untouched further in.

    NOT the seam-agreement defect an earlier note claimed. Scoring the AUTHOR's
    coincident verts against OURS read "4x the author's divergence"; evaluated
    on THE SAME vertex pairs it is 0.404 vs 0.408. `#coincident-skin-match` is
    not at fault and its gate does not want loosening -- it unifies verts
    within 0.15u (152 on this piece) while the defect lives in the layer
    BODIES, ~4000 verts each.

    Self-controlling twice over: a piece whose XML drives no per-vertex shape
    returns 0 before reading a single vertex (1358 of 1536 pack NIFs, and 112
    more whose XML drives only colliders), and a vertex already carrying its
    author's row takes no write.

    NEVER ADDS A BONE -- the blend is restricted to the destination shape's own
    palette, and a vertex whose author row that palette cannot carry to
    `_SMP_HOLD_MIN_SHARE` is left alone. `add_bone` resets every skin-to-bone
    xform, which would ship colliders at the origin.

    WEIGHTS ONLY: no vertex moves, so every clearance result upstream stands.
    Off with CBBE2UBE_NO_SMP_BOUNDARY_HOLD=1.  #smp-boundary-weight-hold
    """
    if not _nc().SMP_BOUNDARY_HOLD or src_nif_path is None:
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
        snf = pyn.NifFile(filepath=str(src_nif_path))
    except Exception as _oe:
        _note_pass_failure("_hold_weights_at_smp_boundary/open", _oe)
        return 0
    try:
        protected = _nc()._hdt_softbody_shape_names(Path(src_nif_path), nif=snf)
    except Exception as _xe:
        _note_pass_failure("_hold_weights_at_smp_boundary/xml", _xe)
        return 0
    if not protected:
        return 0            # not a mixed-cloth piece: nothing to hold against
    from scipy.spatial import cKDTree      # imported per-function in this file

    def _world(shape):
        v = np.asarray(shape.verts, dtype=np.float64)
        try:
            return np.asarray(_verts_skin_to_world(
                v, _shape_global_to_skin(shape)), dtype=np.float64)
        except Exception:
            return v

    anchor = [_world(s) for s in nf.shapes
              if (s.name or "") in protected and len(s.verts)]
    if not anchor:
        return 0            # the XML names shapes this NIF does not carry
    tree = cKDTree(np.vstack(anchor))
    src_by_name = {s.name: s for s in snf.shapes}
    span = max(_nc()._SMP_HOLD_FAR - _nc()._SMP_HOLD_NEAR, 1e-6)

    def _rows(shape):
        out = [dict() for _ in range(len(shape.verts))]
        for bn, pairs in (shape.bone_weights or {}).items():
            for i, w in pairs:
                if 0 <= i < len(out) and w > 0:
                    out[i][bn] = out[i].get(bn, 0.0) + float(w)
        return out

    total = 0
    dirty = False
    for s in nf.shapes:
        name = s.name or ""
        if name in protected or name == "BaseShape":
            continue
        a = src_by_name.get(name)
        if a is None or len(a.verts) != len(s.verts) or not len(s.verts):
            continue        # reauthored/injected shape: no author to hold to
        d, _hit = tree.query(_world(s))
        alpha = np.clip((_nc()._SMP_HOLD_FAR - np.asarray(d, dtype=np.float64))
                        / span, 0.0, 1.0)
        if not float(alpha.max()) > 0.0:
            continue        # nothing on this shape is near the protected layer
        ours, auth = _rows(s), _rows(a)
        palette = set(s.bone_names or [])
        changed: dict = {}
        for _vi in np.nonzero(alpha > 0.0)[0]:
            i = int(_vi)
            src_row = auth[i]
            if not src_row:
                continue
            tot = sum(src_row.values())
            keep = {b: w for b, w in src_row.items() if b in palette}
            if tot <= 0 or sum(keep.values()) < _nc()._SMP_HOLD_MIN_SHARE * tot:
                continue    # our palette cannot carry the author's row
            k = sum(keep.values())
            tgt = {b: w / k for b, w in keep.items()}
            f = float(alpha[i])
            row = dict(ours[i])
            for b in set(row) | set(tgt):
                row[b] = (1.0 - f) * row.get(b, 0.0) + f * tgt.get(b, 0.0)
            row = {b: w for b, w in row.items() if w > _nc()._WRITE_MIN}
            if len(row) > 4:
                row = dict(sorted(row.items(), key=lambda kv: -kv[1])[:4])
            t = sum(row.values())
            if t <= 0:
                continue
            row = {b: w / t for b, w in row.items()}
            if max((abs(row.get(b, 0.0) - ours[i].get(b, 0.0))
                    for b in set(row) | set(ours[i])), default=0.0) > 1e-3:
                changed[i] = row
        if not changed:
            continue
        # The 4-influence cap above can evict a bone on the one vertex that was
        # its last carrier, which empties it out of the skin partition palette.
        _restore_emptied_bones(ours, changed)
        if not changed:
            continue
        # STBs are saved around setShapeWeights and restored after; the reader
        # returns None rather than raising when an xform is missing, and that
        # silent path is what made an earlier pass of this family a no-op.
        # Record, never swallow.
        saved_stb: dict = {}
        skipped_for = None
        for b in (s.bone_names or []):
            try:
                st = s.get_shape_skin_to_bone(b)
            except Exception as _be:
                _note_pass_failure("_hold_weights_at_smp_boundary/stb", _be)
                st = None
            if st is None:
                skipped_for = b
                break
            saved_stb[b] = st
        if skipped_for is not None:
            print(f"  smp-boundary hold: SKIPPED {name!r} -- cannot read the "
                  f"skin-to-bone xform for {skipped_for!r}, so "
                  f"{len(changed)} queued vert(s) were left unheld",
                  file=sys.stderr)
            continue
        # REMOVALS IN THEIR OWN PASS, BEFORE THE ADDITIONS. `setShapeWeights`
        # UPDATES rather than replaces, so a row that drops a bone must write
        # that bone to 0.0 first or the 4-slot merge ships a row summing wrong.
        write_bones = set()
        removed: dict = {}
        for i, new in changed.items():
            write_bones |= set(new) | set(ours[i])
            for b in set(ours[i]) - set(new):
                removed.setdefault(b, set()).add(i)
        try:
            for b in sorted(write_bones):
                rem = removed.get(b)
                if rem:
                    s.setShapeWeights(b, [(i, 0.0) for i in sorted(rem)])
            for b in sorted(write_bones):
                pairs = [(i, changed[i][b]) for i in sorted(changed)
                         if changed[i].get(b, 0.0) > _nc()._WRITE_MIN]
                if pairs:
                    s.setShapeWeights(b, pairs)
        except Exception as _we:
            for b, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(b, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            print(f"  WARN: smp-boundary-hold write failed on {name!r} "
                  f"({_we!r}) -- STBs restored, shape left partly held",
                  file=sys.stderr)
            continue
        for b, st in saved_stb.items():
            try:
                s.set_skin_to_bone_xform(b, st)
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
        s._weights = None
        total += len(changed)
        dirty = True
    if dirty:
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            _note_pass_failure("_hold_weights_at_smp_boundary/save",
                               _se, dst_path)
            return 0
    if total:
        _note_pass_effect("#smp-boundary-weight-hold",
                          f"held {total} vert(s) toward the author")
    return total

def _sync_weight_partner_jiggle(path0, path1) -> int:
    """Give a garment the SAME jiggle bones at both body weights.

    THE DEFECT, every one of the 20 `weight_partner_warnings` on the 2026-08-24
    pack. `_transfer_body_jiggle_to_fitted` grafts butt/belly/breast weight onto
    a garment that HUGS a jiggling body region, gated on a fit FRACTION
    (`_TORSO_JIGGLE_FIT_FRAC`, 0.5). That gate is evaluated PER FILE -- but `_0`
    and `_1` are ONE garment at two body weights, with different geometry and so
    different fit fractions. Around twenty pieces straddle the threshold, so the
    graft fires on one weight and not the other:

        piece                          bone        OURS _0/_1   AUTHOR _0/_1
        armor/imperial/f/cuirassheavy  NPC Belly      . / Y        . / .
        armor/imperial/f/cuirasslight  NPC Belly      Y / .        . / .

    **WE add the bone to one weight; the author has it in NEITHER**, and the
    direction FLIPS between the heavy and light cuirass -- a straddled
    threshold, not a bias. `_CONFORM_MIN_JIGGLE_VERTS` (8) is exactly the
    detector's `present_min`: the two constants describe the same edge.

    UNION, NOT INTERSECTION. Removing the bone from the side that has it would
    throw away the anti-poke the graft exists for, so the deficient side gets it
    instead.

    THE PARTNER IS THE SOURCE, and both halves of that are measured:

      * the WEIGHTS come from the partner BY VERTEX INDEX -- `_0` and `_1` are
        the same mesh, so the pairing is exact and no proximity guess is needed;
      * the new bone's SKIN-TO-BONE XFORM is copied from the partner, verified
        identical across variants on every shared scale bone in a 60-pair sample
        (611 identical, 0 different, 0 unreadable). The two files carry the same
        skeleton and bind pose; only vertex positions differ.

    ONLY THE BONE IS GRAFTED, NOT THE ROW. The rest of a vertex's weighting
    legitimately differs between weights (measured: mean L1 0.03-0.29 apart from
    the missing bone, and most rows differ), so copying the partner's whole row
    would overwrite each variant's own body-follow. The existing row is scaled
    down to make room, the graft added, then re-capped to four influences --
    never evicting a bone being grafted, or the pass would undo itself.

    `add_bone` RESETS EVERY SKIN-TO-BONE XFORM ([[project_identity_stb_collider]]
    -- the class that shipped 174 of 176 colliders at identity), so every
    existing STB is saved first and restored last, and a shape with even ONE
    unreadable STB is skipped whole rather than shipped reset. A bone is also
    never added unless it lands with weight: an `add_bone`'d bone with an empty
    weight list desyncs the partition palette -> equip CTD.

    WEIGHTS ONLY: no vertex moves. Returns verts changed across both files.
    Off with CBBE2UBE_NO_WEIGHT_PARTNER_JIGGLE_SYNC=1.  #weight-partner-jiggle-sync
    """
    if not _nc().WEIGHT_PARTNER_JIGGLE_SYNC:
        return 0
    try:
        pyn = _nc()._pynifly()
        nf = {"0": pyn.NifFile(filepath=str(path0)),
              "1": pyn.NifFile(filepath=str(path1))}
    except Exception as _oe:
        _note_pass_failure("_sync_weight_partner_jiggle/open", _oe)
        return 0
    try:
        return _sync_weight_partner_jiggle_loaded(path0, path1, nf)
    finally:
        # Both files die HERE, by reference counting, not at the next full
        # collection: the batch parent held 32 MB per pair through this pass
        # until then, unbounded by the tree (MEASURED 2026-09-17).
        # #postflight-release
        for _f in nf.values():
            nif_io.release_nif(_f)


def _sync_weight_partner_jiggle_loaded(path0, path1, nf) -> int:
    """The pass proper, on an OPEN pair. The caller owns `nf` and releases it
    after the return; nothing here may keep a shape past that."""

    def _rows(shape):
        n = len(shape.verts)
        out = [dict() for _ in range(n)]
        for bn, prs in (shape.bone_weights or {}).items():
            pl = prs.tolist() if hasattr(prs, "tolist") else prs
            for i, w in pl:
                i = int(i)
                if 0 <= i < n and w > 0:
                    out[i][bn] = out[i].get(bn, 0.0) + float(w)
        return out

    by = {w: {(s.name or ""): s for s in nf[w].shapes} for w in ("0", "1")}
    # weight-that-is-DEFICIENT -> {shape name: [bones to graft]}
    plan: dict = {"0": {}, "1": {}}
    for name, s0 in by["0"].items():
        s1 = by["1"].get(name)
        if not name or s1 is None:
            continue
        try:
            if len(s0.verts) != len(s1.verts) or not len(s0.verts):
                continue
            r0, r1 = _rows(s0), _rows(s1)
        except Exception as _re:
            _note_pass_failure("_sync_weight_partner_jiggle/rows", _re)
            continue
        cand = {b for b in (list(s0.bone_names or []) + list(s1.bone_names or []))
                if _is_scale_bone(b)}
        for bn in sorted(cand):
            c0 = sum(1 for r in r0 if r.get(bn, 0.0) > _nc()._WRITE_MIN)
            c1 = sum(1 for r in r1 if r.get(bn, 0.0) > _nc()._WRITE_MIN)
            p0 = max((r.get(bn, 0.0) for r in r0), default=0.0)
            p1 = max((r.get(bn, 0.0) for r in r1), default=0.0)
            # Same rule as the detector: substantially present on one side,
            # effectively absent on the other, and actually MOVING the mesh.
            if (c0 >= _nc()._WP_JIGGLE_PRESENT_MIN and c1 <= _nc()._WP_JIGGLE_ABSENT_MAX
                    and p0 >= _nc()._WP_JIGGLE_PEAK_MIN):
                plan["1"].setdefault(name, []).append(bn)
            elif (c1 >= _nc()._WP_JIGGLE_PRESENT_MIN and c0 <= _nc()._WP_JIGGLE_ABSENT_MAX
                    and p1 >= _nc()._WP_JIGGLE_PEAK_MIN):
                plan["0"].setdefault(name, []).append(bn)

    total = 0
    for side in ("0", "1"):
        if not plan[side]:
            continue
        other = "1" if side == "0" else "0"
        dirty = False
        for name, bones in sorted(plan[side].items()):
            s, src = by[side][name], by[other][name]
            ours, theirs = _rows(s), _rows(src)
            existing = list(s.bone_names or [])
            # The new bones' STBs, from the partner, BEFORE anything is written.
            new_stb: dict = {}
            unreadable = None
            for b in bones:
                if b in existing:
                    continue
                try:
                    st = src.get_shape_skin_to_bone(b)
                except Exception as _pe:
                    _note_pass_failure("_sync_weight_partner_jiggle/stb", _pe)
                    st = None
                if st is None:
                    unreadable = b
                    break
                new_stb[b] = st
            if unreadable is not None:
                print(f"  weight-partner jiggle: SKIPPED {name!r} -- the "
                      f"partner has no skin-to-bone xform for {unreadable!r}, "
                      f"so the graft would ship it at the origin",
                      file=sys.stderr)
                continue
            changed: dict = {}
            for i in range(len(ours)):
                add = {b: theirs[i][b] for b in bones
                       if theirs[i].get(b, 0.0) > _nc()._WRITE_MIN}
                if not add:
                    continue
                share = sum(add.values())
                if share >= _nc()._WP_JIGGLE_MAX_SHARE:
                    # The graft must never annihilate the row it lands on.
                    k = _nc()._WP_JIGGLE_MAX_SHARE / share
                    add = {b: w * k for b, w in add.items()}
                    share = _nc()._WP_JIGGLE_MAX_SHARE
                row = {b: w * (1.0 - share) for b, w in ours[i].items()}
                for b, w in add.items():
                    row[b] = row.get(b, 0.0) + w
                row = {b: w for b, w in row.items() if w > _nc()._WRITE_MIN}
                if len(row) > 4:
                    # Evict the lightest, but NEVER a bone being grafted -- that
                    # would leave the divergence exactly as it was found.
                    for _w, b in sorted((w, b) for b, w in row.items()
                                        if b not in add)[:len(row) - 4]:
                        row.pop(b, None)
                t = sum(row.values())
                if t <= 0:
                    continue
                row = {b: w / t for b, w in row.items()}
                if max((abs(row.get(b, 0.0) - ours[i].get(b, 0.0))
                        for b in set(row) | set(ours[i])), default=0.0) > 1e-4:
                    changed[i] = row
            if not changed:
                continue
            # BEFORE `to_add`, because rescuing a vertex can remove the only
            # weight a grafted bone would have had -- and then it must not be
            # added either. The 4-influence cap above can evict a bone on the
            # one vertex that was its last carrier, emptying it out of the skin
            # partition palette.
            _restore_emptied_bones(ours, changed)
            if not changed:
                continue
            # Only add a bone that actually lands with weight: an add_bone'd
            # bone shipped with an EMPTY weight list desyncs the partition
            # palette -> out-of-range read -> equip CTD.
            #
            # SORTED, and that is not cosmetic (BUG-04): add_bone order IS the
            # written NIF's bone palette order, which decides which bone the
            # 4-influence cap evicts on a tie. Sorting makes the output
            # INTRINSICALLY ordered instead of depending on how `bones` was
            # accumulated.
            to_add = sorted(b for b in bones if b not in existing
                            and any(r.get(b, 0.0) > _nc()._WRITE_MIN
                                    for r in changed.values()))
            if not to_add and not any(b in existing for b in bones):
                continue
            saved_stb: dict = {}
            missing_for = None
            for eb in existing:
                try:
                    st = s.get_shape_skin_to_bone(eb)
                except Exception as _xe:
                    _note_pass_failure("_sync_weight_partner_jiggle/stb", _xe)
                    st = None
                if st is None:
                    missing_for = eb
                    break
                saved_stb[eb] = st
            if missing_for is not None:
                print(f"  weight-partner jiggle: SKIPPED {name!r} -- cannot "
                      f"read the skin-to-bone xform for {missing_for!r}, so "
                      f"add_bone would leave it at identity",
                      file=sys.stderr)
                continue
            try:
                for b in to_add:
                    s.add_bone(b)
            except Exception as _ae:
                _note_pass_failure("add_bone", _ae)
                for eb, st in saved_stb.items():
                    try:
                        s.set_skin_to_bone_xform(eb, st)
                    except Exception as _pe:
                        _note_pass_failure("set_skin_to_bone_xform", _pe)
                continue
            write_bones = set()
            removed: dict = {}
            for i, new in changed.items():
                write_bones |= set(new) | set(ours[i])
                for b in set(ours[i]) - set(new):
                    removed.setdefault(b, set()).add(i)
            try:
                for b in sorted(write_bones):
                    rem = removed.get(b)
                    if rem:
                        s.setShapeWeights(b, [(i, 0.0) for i in sorted(rem)])
                for b in sorted(write_bones):
                    prs = [(i, changed[i][b]) for i in sorted(changed)
                           if changed[i].get(b, 0.0) > _nc()._WRITE_MIN]
                    if prs:
                        s.setShapeWeights(b, prs)
            except Exception as _we:
                print(f"  WARN: weight-partner jiggle write failed on "
                      f"{name!r} ({_we!r}) -- STBs restored",
                      file=sys.stderr)
            # STBs LAST: add_bone AND setShapeWeights can each reset them.
            for eb, st in saved_stb.items():
                try:
                    s.set_skin_to_bone_xform(eb, st)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            for b in to_add:
                try:
                    s.set_skin_to_bone_xform(b, new_stb[b])
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
            s._weights = None
            total += len(changed)
            dirty = True
        if dirty:
            dst = path0 if side == "0" else path1
            try:
                atomic_nif_save(nf[side], dst)
            except Exception as _se:
                _note_pass_failure("_sync_weight_partner_jiggle/save", _se, dst)
                return 0
    if total:
        _note_pass_effect("#weight-partner-jiggle-sync",
                          f"synced {total} vert(s) across the weight pair")
    return total

def _layered_cloth_jiggle_regions(dst_path, nf, layered_cloth_names) -> frozenset:
    """The jiggle REGIONS `_transfer_body_jiggle_to_fitted` may still graft onto
    this piece's layered-cloth shapes; empty = skip them outright, as before.

    #layered-cloth-butt-follow: the butt only, and only on a piece with no
    physics XML -- every SMP interaction behind `#layered-cloth-skin` needed one.
    See LAYERED_CLOTH_BUTT_JIGGLE for the measurements. The XML is read only when
    the piece has layered cloth and the flag is on."""
    if not (layered_cloth_names and _nc().LAYERED_CLOTH_BUTT_JIGGLE):
        return frozenset()
    if _nc()._piece_has_hdt_xml(dst_path, nif=nf):
        return frozenset()
    return frozenset(_nc()._LAYERED_CLOTH_JIGGLE_REGIONS)

def _jiggle_regions_closed(bw, open_regions=None) -> set:
    """The jiggle regions the graft must NOT touch on a shape with weights `bw`.

    A region the shape already carries on `_CONFORM_MIN_JIGGLE_VERTS` or more
    verts (#region-jiggle-gate) and, for a layered-cloth shape (`open_regions`
    given), every region outside `open_regions` (#layered-cloth-butt-follow)."""
    have = {kw: 0 for kw in _nc().PHYSICS_JIGGLE_SCALE_KEYWORDS}
    for b, pairs in bw.items():
        kw = _jiggle_region_of(b)
        if kw is not None:
            have[kw] += sum(1 for _vi, w in pairs if float(w) > 0.1)
    closed = {kw for kw, c in have.items()
              if c >= _nc()._CONFORM_MIN_JIGGLE_VERTS}
    if open_regions is not None:
        closed |= {kw for kw in _nc().PHYSICS_JIGGLE_SCALE_KEYWORDS
                   if kw not in open_regions}
    return closed

def _transfer_body_jiggle_to_fitted(dst_path, biped_slots: int = 0,
                                    src_nif_path=None) -> int:
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
    Leg-dominant garments plus fitted TORSO garments (corset / bra / cuirass);
    the torso path (default ON since 1.2, in-game validated via the bust collider
    split) is opt-out behind CBBE2UBE_TORSO_JIGGLE=0.  #torso-jiggle-graft
    Layered cloth takes the BUTT region only, and only on a piece with no physics
    XML (#layered-cloth-butt-follow); every other gate applies to it unchanged."""
    if not _nc().TRANSFER_BODY_JIGGLE:
        return 0
    if biped_slots & (_nc().BIPED_SLOT33_BIT | _nc().BIPED_SLOT37_BIT):
        return 0  # hands/feet -- not the clip class
    weight = "_0" if str(dst_path).lower().endswith("_0.nif") else "_1"
    ref = _nc()._body_conform_ref(weight)
    jref = _body_jiggle_ref(weight)
    if ref is None or jref is None:
        return 0
    _Vb, body_w, _bb, tree = ref
    jstbs, body_ident = jref
    if not jstbs or not body_ident:
        return 0  # no jiggle bones, or skin spaces don't align -> skip (no spike)
    try:
        pyn = _nc()._pynifly()
        nf = pyn.NifFile(filepath=str(dst_path))
    except Exception:
        return 0
    if _nc()._nif_has_fx_shape(nf):
        return 0  # effect-shader/glow NIF: a reload+re-save corrupts its controller -> CTD.
                  # Leave it exactly as the main conversion wrote it (see _nif_has_fx_shape).
    collider_names = _nc()._hdt_collider_shape_names(dst_path, nif=nf)
    softbody_names = _nc()._hdt_softbody_shape_names(dst_path, nif=nf)
    # Same rule as the reskin and the leg-bend graft: a shape driven by its OWN
    # source morph TRI already tracks the body at runtime on its ORIGINAL skin.
    #
    # THE MORPH-TRI PREDICATE NO LONGER GATES THIS PASS. The defect that
    # motivated it was leg DETAIL bones on a flap tip at calf height, which this
    # pass does not graft; what this pass grafts is breast/butt/belly, and gating
    # it stripped those from ~1184 shapes with no authored jiggle -- the
    # #source-follow CLIPPING population, which needs them most. The per-region
    # gate below already skips any region the author DID weight, so nothing is
    # double-grafted. See MORPHTRI_KEEP_JIGGLE for the measurements.
    # #morphtri-keep-jiggle (CBBE2UBE_MORPHTRI_GATE_JIGGLE=1 restores the old
    # coupled behaviour). #morphtri-no-leg-graft still gates the leg graft.
    morph_tri_names = (_source_morph_tri_shape_names(Path(src_nif_path))
                       if (src_nif_path and _nc().MORPHTRI_NO_LEG_GRAFT
                           and not _nc().MORPHTRI_KEEP_JIGGLE) else set())
    layered_cloth_names = _layered_cloth_shape_names(nf.shapes)  # keep source skin
    # #layered-cloth-butt-follow: what a layered-cloth shape may still take here --
    # the butt, on a piece with no physics XML. Empty keeps the blanket skip.
    layered_regions = _layered_cloth_jiggle_regions(dst_path, nf, layered_cloth_names)
    # Lazy: with the gate OFF this pass must do NO extra work at all.
    _skip_keys = _nc()._conform_skip_keys(
        _nc()._piece_has_hdt_xml(dst_path, nif=nf) if _nc().DRAPE_SKIP_XML_GATED else None)
    total = 0
    dirty = False
    for s in nf.shapes:
        nm = (s.name or "").lower()
        layered = s.name in layered_cloth_names
        if (s.name in softbody_names or (layered and not layered_regions)
                or s.name in morph_tri_names
                or any(k in nm for k in _skip_keys)):
            continue
        # A per-triangle COLLIDER is NEVER grafted. #smp-collider-graft, and the
        # torso path does not get an exception.
        #
        # It was given one, behind a standoff from the NIF's simulated cloth, on the
        # reasoning that the cloth resting on the collider was the thing at risk.
        # IN-GAME THAT WAS REFUTED, hard: on a cuirass that is its own bust collider,
        # the breasts tore away from the body and fell through the terrain. The
        # collision partner that matters for a bust graft is the BREAST, not the
        # skirt -- the body's breast physics collides against this very surface, so
        # grafting breast motion onto it closes a feedback loop (breast moves ->
        # collider moves with it -> breast is pushed further). A standoff measured
        # against the skirt chains cannot help, because the graft region and the
        # runaway collision are the same place. There is no safe standoff for a
        # collider that covers the region being grafted, which for a torso garment
        # is always. #torso-jiggle-graft
        if s.name in collider_names:
            continue
        if _nc()._shape_has_effect_shader(s) or _nc()._is_fx_overlay_name(s.name):
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
        # Only regions that LACK jiggle. PER REGION, not pooled. #region-jiggle-gate
        #
        # This counter used to pool breast+butt+belly into one number and skip the
        # whole shape at 8, on the reasoning that a shape which already jiggles is
        # handled by `_conform_fitted_to_body`. BOTH HALVES WERE WRONG:
        #
        #  * the chest-follow pass grafts breast weight EARLIER, so 295 breast verts
        #    tripped the counter on the first bone and the shape was dropped with
        #    its butt weight still at zero -- a whole-shape gate deciding a
        #    per-region question;
        #  * and the pass it defers to cannot finish. `_conform_blend_vert` keeps
        #    the vert's bone set and it "can only SHRINK (no body-only bone is
        #    added)" -- it rebalances bones the shape HAS, so a shape with no
        #    `NPC L/R Butt` never gets one from there. Butt weight stayed at
        #    exactly 0.0000 permanently.
        #
        # Measured over the shipped pack: 184 pieces show a butt-follow gap, 105 of
        # them in exactly this state. In game the body pushed straight through the
        # armour under morph, on the vanilla leather cuirass among others.
        #
        # `already` is then used to filter the per-vert graft below, so a region the
        # shape ALREADY carries is left untouched -- this must not re-graft over
        # chest-follow's measured ratio. On a layered-cloth shape every region
        # outside `layered_regions` counts as closed too, which is what keeps
        # breast and belly off the cloth (#layered-cloth-butt-follow).
        already = _jiggle_regions_closed(bw, layered_regions if layered else None)
        if len(already) >= len(_nc().PHYSICS_JIGGLE_SCALE_KEYWORDS):
            continue        # every region present -> genuinely the conform's job
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
        # Verts a custom (non-skeleton) bone drives are SIMULATED cloth. They are
        # never grafted -- both as partition safety and because a simulated vert has
        # no rest position to follow the body from.
        is_chain = [any(w > 0.1 and not _is_skeleton_bone(b) for b, w in dv.items())
                    for dv in vw]
        chain_frac = sum(is_chain) / n
        Vw = _verts_skin_to_world(V, g2s)
        d, idx = tree.query(Vw)
        # #torso-jiggle-graft: a fitted torso garment fails all three leg gates --
        # it is chain-welded (a cuirass sharing a shape with its own skirt), its
        # whole-shape fit is dragged down by that skirt, and it is spine-dominant,
        # not leg-dominant. Judge it on its RIGID verts instead, with the existing
        # rigid-torso-armour classifier standing in for "hugs the body".
        rigid_i = [i for i in range(n) if not is_chain[i]]
        torso_mode = bool(
            _nc().TORSO_JIGGLE_TRANSFER and rigid_i
            and _nc()._shape_is_rigid_torso_armor(s)
            and float((d[rigid_i] < _nc()._CONFORM_FIT_PROX).mean()) >= _nc()._TORSO_JIGGLE_FIT_FRAC)
        if not torso_mode and s.name in collider_names:
            continue
        if not torso_mode:
            # not a physics-chain garment (custom non-skeleton bones -> SMP cloth)
            if chain_frac > _nc()._CONFORM_CHAIN_MAX:
                continue
            # HUGS the body (a loose skirt sits away -> excluded)
            if float((d < _nc()._CONFORM_FIT_PROX).mean()) < _nc()._CONFORM_FIT_FRAC:
                continue
            # leg-dominant: the measured close-to-body clip population is leg cloth.
            leg_dom = sum(1 for dd in vw
                          if dd and _is_leg_rigid_bone(max(dd, key=dd.get))) / n
            if leg_dom <= 0.5:
                continue
        # (A collider standoff used to live here. It was removed: colliders are now
        # refused outright, because the runaway collision is the BREAST against this
        # same surface -- see the collider skip above. #torso-jiggle-graft)
        new_bones: dict = {}
        grafted_rows: list = []
        graft = 0
        for i in range(n):
            if d[i] > _nc()._CONFORM_VERT_PROX:
                continue
            dvi = vw[i]
            if not dvi:
                continue
            if is_chain[i]:
                continue  # custom-chain vert -> leave it (partition safety)
            bd = body_w[idx[i]]
            bd_jig = {b: w for b, w in bd.items()
                      if _is_physics_jiggle_scale_bone(b) and w > 1e-3
                      and b in jstbs
                      # #region-jiggle-gate: graft ONLY the regions this shape is
                      # missing. A region it already carries was weighted by the
                      # source or by chest-follow, whose ratio is separately
                      # measured -- re-grafting over it would silently retune a
                      # constant somebody validated in game.
                      and _jiggle_region_of(b) not in already}
            if not bd_jig:
                continue  # body doesn't jiggle under this vert -> nothing to follow
            closeness = max(0.0, 1.0 - d[i] / _nc()._CONFORM_VERT_PROX)
            new, added = _jiggle_transfer_vert(
                dvi, bd_jig, closeness, _nc()._JIGGLE_TRANSFER_FACTOR)
            if new is None:
                continue
            for jb in added:
                if jb not in existing:
                    new_bones[jb] = jstbs.get(jb)
            vw[i] = new
            grafted_rows.append(i)
            graft += 1
        # `new_bones` empty does NOT mean nothing happened: a pure REINFORCEMENT
        # (every raised bone already in the shape's list) updates rows without
        # needing any add_bone. The old `or not new_bones` clause here threw
        # away every such row -- the shape-level half of the presence-is-not-
        # follow trap (#jiggle-reinforce).
        if not graft:
            continue
        # Graft each new jiggle bone with the body's bind transform. CRITICAL:
        # add ALL bones FIRST, THEN set the STBs -- a later add_bone RESETS the
        # STB of an earlier-added bone (matches _install_skin's add-all-first
        # order). A bone we can't give a valid STB would skin to the ORIGIN
        # (spike, audit #4), so DROP its grafted weight rather than ship the spike.
        # ...and it must also EMIT at least one weight above the write threshold.
        # `new_bones` only means "the graft gave this bone weight somewhere"; the
        # write below filters on `> 1e-4`, so a bone whose every grafted weight
        # lands under that is added to the shape and then written an EMPTY list --
        # a zero-weight bone left in the list but absent from the regenerated skin
        # partition palette, so a per-vert index runs past the palette on equip.
        # `_match_rigid_leg_bend_to_body`'s `to_add` already carries exactly this
        # `any(... > 1e-4 ...)` test; this pass was missing it. Dropping the bone
        # here is safe: its sub-threshold weight would not have been written
        # anyway. #zeroweight-bone-desync
        #
        # #weight-write-invariant: cap FIRST, and before `addable` is decided. The
        # graft can push a row to 5 influences; the save then keeps the largest 4
        # and does NOT renormalise, so the row ships light (measured 0.9655 on 26
        # verts of the hide cuirass) and the vertex is transformed by a deflated sum
        # of its bone matrices. Capping here makes the survivors deterministic, and
        # running it BEFORE `addable` means a graft the cap discards is never
        # add_bone'd -- the ordering P6 had to learn the hard way.
        # Every bone in a grafted row is writable here: the write below rewrites each
        # touched bone's FULL weight list, so a bone the cap zeroes really does lose
        # the influence (unlike a changed-pairs writer, where zeroing something it
        # will not write would leave the renormalise chasing a weight still on disk).
        _cap_and_renormalise_rows(
            vw, n, {b for i in grafted_rows for b in vw[i]}, rows=grafted_rows)
        addable = [(jb, stb) for jb, stb in new_bones.items()
                   if stb is not None
                   and any(vw[i].get(jb, 0.0) > 1e-4 for i in range(n))]
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
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)

        for jb, _stb in addable:
            try:
                s.add_bone(jb)
            except Exception as _pe:
                _note_pass_failure("add_bone", _pe)
        safe: set = set()
        for jb, stb in addable:
            try:
                s.set_skin_to_bone_xform(jb, stb)
                safe.add(jb)
            except Exception as _pe:
                _note_pass_failure("set_skin_to_bone_xform", _pe)
        unsafe = set(new_bones) - safe
        if unsafe:
            for i in range(n):
                if any(jb in vw[i] for jb in unsafe):
                    for jb in unsafe:
                        vw[i].pop(jb, None)
                    ss = sum(vw[i].values())
                    if ss > 0:
                        vw[i] = {b: w / ss for b, w in vw[i].items()}
        if addable and not safe:
            # We TRIED to add bones and none took an STB -> nothing safely
            # grafted; add_bone above already reset the existing STBs, restore
            # before bailing. (`addable` empty is the pure-reinforcement case:
            # no add_bone ran, rows only touch existing bones -> proceed to
            # write. #jiggle-reinforce)
            _restore_existing_stbs()
            continue   # leave this shape untouched
        touched: set = set()
        for i in range(n):
            touched |= set(vw[i])
        # SORTED, and it is not cosmetic. Iterating the set directly made the
        # WRITE ORDER depend on PYTHONHASHSEED, and write order decides which
        # influence survives when a row still overflows the 4-slot limit at save
        # time. Measured on a vanilla light cuirass: ONE vertex (694) kept
        # `HideSkirt 6_01` at seed 0 and `HideSkirt 5_01` at seed 2 -- the two
        # are near-tied at 0.037872 / 0.038025 -- which moved that bone's weight
        # total by 0.038 and made the golden harness flag the piece on roughly
        # half of all runs. A gate that cries wolf gets ignored, so this was
        # costing more than the 0.2% it moved.  #deterministic-weight-write
        for bn in sorted(touched):
            pairs = [(i, vw[i][bn]) for i in range(n)
                     if bn in vw[i] and vw[i][bn] > 1e-4]
            if not pairs and bn in existing:
                # NEVER EMPTY A BONE. The cap above can zero an existing bone on
                # every row it touched; writing the empty list would leave a bone in
                # the shape's list but out of the regenerated partition palette, so a
                # per-vert index runs past it on equip. Skipping the write leaves that
                # bone exactly as it was -- at worst a few rows sum slightly over 1,
                # which is cosmetic where the alternative is a CTD.
                # #zeroweight-bone-desync
                continue
            s.setShapeWeights(bn, pairs)
        # STBs LAST: add_bone + setShapeWeights zeroed BOTH the existing bones' STBs
        # and the just-set graft STBs -> restore the originals + re-set the safe
        # grafts. Nothing after this resets them.
        _restore_existing_stbs()
        for jb, stb in addable:
            if jb in safe:
                try:
                    s.set_skin_to_bone_xform(jb, stb)
                except Exception as _pe:
                    _note_pass_failure("set_skin_to_bone_xform", _pe)
        dirty = True
        total += graft
    if dirty:
        # Re-assert the VirtualBody Hidden bit (a re-save can drop it -> blue body
        # double), mirroring _conform_fitted_to_body / _reauthor.
        _nc()._hide_virtual_body(nf)
        try:
            atomic_nif_save(nf, dst_path)
        except Exception as _se:
            # See _match_rigid_leg_bend_to_body: `return 0` already means "no
            # rows matched", so a swallowed save reads as a clean no-op.
            _note_pass_failure("_transfer_body_jiggle_to_fitted/save", _se, dst_path)
            return 0
    return total

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

    # #covered-skin-target. This K-NN propagation is the FIRST decision on a
    # body-swap garment's row (traced 2026-09-17: it wrote the reported gusset's
    # R Thigh 0.421 / Pelvis 0.549 from the inner-thigh skin 0.65u away, while the
    # same panel passes over the pelvis-static cleft 4u further on). A vertex
    # that COVERS skin -- the body vertices whose nearest garment vertex it is --
    # takes the clearance-weighted mean of THAT skin instead, inside the crotch
    # and hip band. The K-NN answer stands everywhere else.
    _cov_rows: dict = {}
    if _nc().COVERED_SKIN_TARGET:
        _nb = _nc()._body_normals_or_compute(body_shape)
        if _nb is not None and len(_nb) == body_n:
            _zb, _xb = body_verts[:, 2], body_verts[:, 0]
            _cband = ((_zb >= _nc()._COVER_Z_LO) & (_zb <= _nc()._COVER_Z_HI)
                      & (np.abs(_xb) < _nc()._COVER_X))
            _cover, _clr = _covered_skin_map(armor_verts, body_verts, _nb, _cband,
                                             _nc()._COVER_REACH)
            for _gi, _bis in _cover.items():
                if len(_bis) < _nc()._COVER_MIN_VERTS:
                    continue
                _w = 1.0 / (np.maximum(_clr[_bis], 0.0) + _nc()._COVER_EPS)
                _cov_rows[_gi] = (np.asarray(_bis, dtype=np.int64), _w / _w.sum())

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
        if _nc().RESKIN_EXCLUDE_SCALE_BONES and _is_scale_bone(bn):
            continue
        # K-NN propagation
        propagated = (body_arr[knn_idx] * inv_d).sum(axis=1)
        for _gi, (_bis, _w) in _cov_rows.items():
            propagated[_gi] = float((body_arr[_bis] * _w).sum())
        propagated *= blend
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
            mask = ((v[:, 2] >= _nc().CHEST_SYNC_Z_MIN)
                    & (v[:, 2] <= _nc().CHEST_SYNC_Z_MAX)
                    & (np.abs(v[:, 0]) <= _nc().CHEST_SYNC_X_BOUND)
                    & (v[:, 1] >= _nc().CHEST_SYNC_Y_MIN))
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
            if breast_frac < _nc().CHEST_SYNC_MIN_BREAST_FRAC:
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
                recv_verts, k=1, distance_upper_bound=_nc().CHEST_SYNC_DISTANCE)

            recv_os = recv_job["override_skin"]
            recv_weights = recv_os.setdefault("weights", {})
            recv_xforms = recv_os.setdefault("xforms", {})
            recv_bones = recv_os.setdefault("bones", [])
            recv_bones_set = set(recv_bones)

            # Collect replacements first, then apply atomically.
            replace_map: "dict[int, dict[str, float]]" = {}
            for ri_local, (d, ai_local) in enumerate(zip(dists, nearest_local)):
                if not np.isfinite(d) or d > _nc().CHEST_SYNC_DISTANCE:
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
    except Exception as _le:
        # A refactor bug here kills this inter-layer pass for EVERY
        # conversion forever, and 'crashed on line 1' prints exactly what
        # 'no layers in this NIF' prints (audit 2026-07-28, the dead-
        # backstop class). One line, always.
        print(f"  WARN: _sync_chest_layered_cloth_weights died: {_le!r} -- "
              f"layer pass skipped for this piece", file=sys.stderr)
        _note_pass_failure("_sync_chest_layered_cloth_weights", _le)
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
    if not _nc()._ABDO_JIGGLE_SYNC:
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
            mask = (v[:, 2] >= _nc().ABDO_SYNC_Z_MIN) & (v[:, 2] <= _nc().ABDO_SYNC_Z_MAX)
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
            if tw <= 0 or (jw / tw) < _nc().ABDO_SYNC_MIN_JIGGLE_FRAC:
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
                recv_verts, k=1, distance_upper_bound=_nc().ABDO_SYNC_DISTANCE)
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
                if not np.isfinite(d) or d > _nc().ABDO_SYNC_DISTANCE:
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
    except Exception as _le:
        # A refactor bug here kills this inter-layer pass for EVERY
        # conversion forever, and 'crashed on line 1' prints exactly what
        # 'no layers in this NIF' prints (audit 2026-07-28, the dead-
        # backstop class). One line, always.
        print(f"  WARN: _sync_abdomen_layered_cloth_weights died: {_le!r} -- "
              f"layer pass skipped for this piece", file=sys.stderr)
        _note_pass_failure("_sync_abdomen_layered_cloth_weights", _le)
        return 0

# Skyrim's GPU skinning supports ~80 bones per skin partition. Exceeding it
# overruns the bone-matrix palette at draw -> equip CTD. This backstop catches
# scale bones from any source (reskin transfer, add_scale_bone_weights, dense rigs).
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
    # Keep MIRRORED PAIRS together. Ranking bones individually lets the cutoff
    # fall between `NPC L FrontThigh` and `NPC R FrontThigh`, keeping one and
    # dropping the other -- so one thigh follows the body's morph/flex and the
    # other stays rigid, a visible asymmetric deformation. Measured on a real
    # pack: 24 of 224 shapes carrying leg detail bones had exactly this, because
    # thinly-propagated scale bones sit near the cutoff and their L/R importance
    # differs by a hair. Rank each pair by its BEST member and admit both or
    # neither; a pair that would straddle the limit is dropped whole, so the
    # result can be 1 under `limit` -- deliberately, since staying under the GPU
    # bone cap is the point and a lone half-pair is worse than one fewer bone.
    def _mirror(b: str) -> str:
        if b.startswith("NPC L "):
            m = "NPC R " + b[6:]
        elif b.startswith("NPC R "):
            m = "NPC L " + b[6:]
        else:
            return ""
        # bracketed side tag too: "[LThg]" <-> "[RThg]", "[LrClf]" <-> "[RrClf]"
        i = m.rfind("[")
        if i != -1 and i + 1 < len(m):
            c = m[i + 1]
            if c in "LR":
                m = m[:i + 1] + ("R" if c == "L" else "L") + m[i + 2:]
        return m

    # Pair only when BOTH members matter comparably. A group is ranked by its
    # best member, so an unconditional pair lets a near-zero partner ride in on
    # its partner's rank and displace a higher-ranked singleton -- measured, an
    # adversarial one-sided drape (dominant 0.90 / partner 0.01) displaced 2
    # mid-ranked chain bones that individual ranking kept. Dropping a 0.01
    # partner costs nothing visible; dropping a 0.75 skirt chain bone kills the
    # sway. The asymmetry this pairing exists to prevent comes from bones whose
    # L/R importance "differs by a hair", so requiring the weaker member to be
    # within PAIR_MIN_RATIO of the stronger keeps the fix and drops the misfire.
    PAIR_MIN_RATIO = 0.5

    def _pairable(a: str, b: str) -> bool:
        ia, ib = _importance(a)[0], _importance(b)[0]
        hi = max(ia, ib)
        return hi <= 0.0 or min(ia, ib) >= PAIR_MIN_RATIO * hi

    nameset = set(names)
    groups, seen = [], set()
    for b in names:
        if b in seen:
            continue
        mate = _mirror(b)
        grp = ([b, mate] if mate and mate in nameset and mate != b
               and _pairable(b, mate) else [b])
        seen.update(grp)
        groups.append(grp)
    groups.sort(key=lambda g: max(_importance(x) for x in g), reverse=True)
    keep, used = set(), 0
    for g in groups:
        if used + len(g) > limit:
            continue          # would straddle the cap -> drop the whole pair
        keep.update(g)
        used += len(g)
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
    cache = _nc()._SCALE_BONE_DATA_CACHE
    # KEYED BY THE BODY FILE, HOLDING NO SHAPE. #scale-bone-cache-by-file
    # Phase 2 re-opens the UBE body NIF for EVERY conversion, so the old id()
    # key never hit: each conversion rebuilt the KD-trees and its entry pinned a
    # whole body NIF for the life of the worker. Measured by the hardening plan's
    # verifier: post-gc commit stepped up on every body-swap conversion, and
    # clearing this dict alone freed 229 MB and returned the live NifFile count
    # to its baseline. A body read from an unchanged file has that file's data,
    # so every such load can share one entry, and the shape itself can be freed.
    file_key = _body_file_identity(body_shape)
    if file_key is not None:
        key = ("file",) + file_key + (bool(leg_region_only), excl)
        cached = cache.get(key)
        if cached is not None:
            return cached[1], cached[2]
    else:
        # No file on disk to key on (a test double, a NIF built in memory):
        # key on id(body_shape) AND keep a strong ref in the value, because
        # Python recycles an id() after GC and a bare id key can return a
        # DIFFERENT, dead-and-replaced body's KD-trees -> wrong scale weights.
        # The `is` guard is the belt-and-suspenders miss. #idreuse-cache
        key = (id(body_shape), bool(leg_region_only), excl)
        cached = cache.get(key)
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
    if file_key is not None:
        # One entry per body file and option set: an entry for an earlier
        # version of the same file (other size or mtime) is dropped, not kept.
        same_file = (file_key[0], file_key[3])
        for stale in [k for k in cache
                      if k[:1] == ("file",) and (k[1], k[4]) == same_file
                      and k[-2:] == key[-2:] and k != key]:
            del cache[stale]
        cache[key] = (None, scale_bones, bone_data)
    else:
        cache[key] = (body_shape, scale_bones, bone_data)
    return scale_bones, bone_data


def _body_file_identity(body_shape):
    """(normalised path, size, mtime_ns, shape name, vert count) of the NIF a
    pynifly shape was read from, when that file is on disk; None otherwise.
    pynifly shapes carry their NifFile as `.file`, and it keeps `.filepath`."""
    try:
        filepath = getattr(getattr(body_shape, "file", None), "filepath", None)
        if not filepath:
            return None
        path = Path(filepath)
        st = path.stat()
        return (os.path.normcase(str(path.resolve())), st.st_size,
                st.st_mtime_ns, str(body_shape.name), len(body_shape.verts))
    except (OSError, TypeError, ValueError, AttributeError):
        return None

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
    if _nc()._is_rigid_attachment(weights_by_bone):
        max_transfer = _nc().SCALE_BONE_MAX_TRANSFER_RIGID

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
        if _is_physics_jiggle_scale_bone(bn) and (leg_armor or _nc().NO_SOFTBODY_SCALES):
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
                1.0 - (nearest / reach) ** _nc().TORSO_PARITY_FALLOFF_POWER,
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
        if _nc().SUPPRESS_TORSO_SCALE_ON_ARMS and _is_torso_parity_bone(bn):
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

def _is_physics_evidence_bone(name: str) -> bool:
    """True if this bone indicates PHYSICS rigging rather than a plain
    structural bone the reference body happens not to carry.

    Soft-body jiggle bones and cloth-named bones count even when the actor
    skeleton resolves them, because standard-but-simulated is still simulated.
    Everything else counts only when the skeleton CANNOT supply it.
    """
    if _is_soft_body_physics_bone(name) or _nc()._CLOTH_BONE_RE.search(name or ""):
        return True
    return not _nc()._actor_can_resolve_bone(name)

def _weights_to_index(osk):
    """`{bone: [(vi, w), ...]}` -> `{bone: {vi: w}}`, in place, once per shape.

    The list form makes "set the weights of ONE vertex" cost a full rebuild of
    every bone's list, so unifying N seam verts was O(N x total weight pairs).
    Profiled on a copy-path armour: `_set_override_vert_weights` ran 39,024
    times and its list rebuild alone was 98.8s of a 186.9s conversion.
    """
    w = osk.get("weights")
    if not isinstance(w, dict):
        return
    for bn, pairs in list(w.items()):
        if isinstance(pairs, dict):
            continue
        d = {}
        for v, ww in (pairs.tolist() if hasattr(pairs, "tolist") else pairs):
            d[int(v)] = float(ww)
        w[bn] = d

def _weights_from_index(osk):
    """`{bone: {vi: w}}` -> `{bone: [(vi, w), ...]}`. Restores the shape every
    downstream consumer expects; a dict leaking out would be a silent format
    change. Sorted so output is deterministic across runs."""
    w = osk.get("weights")
    if not isinstance(w, dict):
        return
    for bn, d in list(w.items()):
        if isinstance(d, dict):
            w[bn] = [(int(v), float(x)) for v, x in sorted(d.items())]

def _set_override_vert_weights(osk, vi, tgt, bone_xform):
    """Set vert `vi`'s skin weights in an override_skin dict to exactly `tgt`
    ({bone: weight}). Removes `vi` from every existing bone entry, then adds it
    to each target bone, creating the bone in the bones list / xforms map as
    needed (xform pulled from `bone_xform`, the cluster-wide skeleton-global
    skin-to-bone map).

    Expects `weights` in INDEX form (see `_weights_to_index`), so removing a
    vertex is a dict delete rather than rebuilding every bone's list.
    """
    weights = osk["weights"]
    bones = osk.setdefault("bones", list(weights.keys()))
    xforms = osk.setdefault("xforms", {})
    for bn in weights:
        weights[bn].pop(vi, None)
    for bn, w in tgt.items():
        if w <= 0:
            continue
        weights.setdefault(bn, {})[vi] = float(w)
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
        # A plate can legitimately carry no source shape. Without this guard the
        # AttributeError propagates up to `_weld_cross_shape_seams`, whose
        # catch-all then abandons seam welding for the ENTIRE nif -- one
        # unusable member silently costing every other cluster its weld.
        if src_shape is None:
            return None
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
    touched = set()
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
        for _osk in oss:
            _weights_to_index(_osk)      # O(1) per-vert edits below
        touched.update(id(o) for o in oss)
        member_w = []
        bone_xform = {}  # cluster-wide bone -> skin-to-bone (skeleton-global)
        for (pi, li), osk in zip(cluster, oss):
            # Indexed lookup. This scanned EVERY (vert, weight) pair of
            # every bone to read ONE vertex -- the second half of the
            # same quadratic.
            _li = int(li)
            wd = {bn: d[_li] for bn, d in osk["weights"].items()
                  if _li in d}
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
    # Back to the list form every downstream consumer expects.
    for _p in plates:
        _o = _p.get("override_skin")
        if _o and id(_o) in touched:
            _weights_from_index(_o)
    return n_matched
