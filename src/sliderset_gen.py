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

"""Generate a per-armor BODYTRI (PIRT) file by propagating UBE body slider
deltas to armor verts.

Given:
  * UBE reference body verts (BaseShape with 29298 verts; canonical UBE topology)
  * UBE reference body OSD (per-slider, per-vertex deltas on that body)
  * A CBBE-authored armor NIF whose shapes sit on / around the body

Produces a TriFile that NioOverride loads at runtime: one TriShape per armor
shape, each carrying a TriMorph per UBE slider. Deltas are propagated from
the body to each armor vert via adaptive-K nearest-body-vertex IDW (K varies
1 / 4 / 16 by per-vert standoff distance from the body; see generate_armor_tri).

This replaces the BodySlide build-time workflow with runtime morph
application, so users only need to ship the TRI + ESP patch rather than
.osd + .osp + a BodySlide build step.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .osd import OsdFile
from .tri import TriFile, TriShape, TriMorph


# ---------- BODYTRI (PIRT) writer — RaceMenu runtime morph format -------

# Default minimum delta for inclusion in TRI offsets. Body sliders that
# don't perceptibly affect an armor vert (because the vert is far from
# the body or the slider only touches a different region) produce tiny
# propagated deltas. Dropping them keeps the TRI compact — typical
# hand-authored TRIs are ~10MB; without filtering we'd produce 50MB+.
DEFAULT_TRI_MIN_DELTA = 0.005

# Threshold (in units, per-vertex max delta magnitude over the body)
# above which a morph is treated as a "global body reshape" rather
# than a local feature slider. For these big morphs we drop K from 4
# to 1 in propagation so the armor vert tracks the body vert it sits
# on closest to (no K=4 IDW averaging dampening). Without this, big
# preset-style sliders like Amazon / Peachy under-track the body and
# the body grows past the armor, collapsing the visual fit.
#
# 2.0 is empirically tuned from UBE's OSD: most local sliders top out
# around 1.0-1.5 units; preset-style sliders are 2-7 units.
HIGH_MAGNITUDE_K1_THRESHOLD = 2.0

# Substrings (lowercased) marking a shape as a rigid prop / metal piece
# that doesn't need body-morph tracking. These are sorted to the TAIL of
# the TRI as a soft authoring convention (cloth before rigid). After
# #139 there is no engine cap that drops them; ordering is cosmetic.
_RIGID_PROP_KEYWORDS = (
    "dagger", "scabbard", "sword", "bow", "arrow", "quiver", "shield",
    "pouch", "amulet", "ring", "necklace", "chain", "circlet", "crown",
    "earring", "nail", "gem", "stud", "buckle", "rivet", "clasp",
    "metal", "pauldron", "shoulder", "deco", "ornament", "spike",
    "collar", "button", "plate", "guard", "bracer", "vambrace",
    "armband", "armlet", "cuff",
)

# Of the rigid props, these ride a single weapon/jewelry bone and NEVER
# benefit from body morph — they're the very first to drop when the morph
# cap is tight. Body-worn rigid armor (pauldrons/shoulders/guards/bracers)
# is NOT in this set: it sits on the body and should keep a morph slot
# ahead of weapon/jewelry attachments (otherwise e.g. a shoulder pad stops
# scaling with the body when the cap cuts it — exactly what the 3-atlas
# split exposed). Priority tiers: cloth (0) < body-worn rigid (1) <
# weapon/jewelry attachment (2).
_ATTACHMENT_KEYWORDS = (
    "dagger", "scabbard", "sheath", "sword", "knife", "axe", "mace",
    "hammer", "staff", "bow", "arrow", "quiver", "shield",
    "pouch", "satchel",
    "amulet", "ring", "necklace", "circlet", "crown", "earring", "gem",
)


def generate_armor_tri(
    armor_shapes: dict[str, np.ndarray],
    body_verts: np.ndarray,
    body_osd: OsdFile,
    *,
    body_shape_name: str = "BaseShape",
    include_body_shapes: bool | set[str] = True,
    k: int = 4,
    min_delta: float = DEFAULT_TRI_MIN_DELTA,
    carrier_shape_name: str | None = None,
    extra_body_osds: "dict[str, OsdFile] | None" = None,
    armor_vert_extremity_fractions: "dict[str, np.ndarray] | None" = None,
) -> TriFile:  # noqa: docstring continues below
    # Perf note: The naive structure (call propagate_slider_deltas
    # per shape × per morph) was O(S * M) KDTree rebuilds and O(S * M)
    # K-NN queries, even though K-NN is the same for every morph of a
    # given shape. For ~10 armor shapes × 202 morphs that's ~2000
    # KDTree builds per armor NIF. This refactor builds the KDTree
    # once, queries K-NN per shape once, then for each morph just
    # does the IDW math via vectorized numpy gather + multiply + sum.
    # On a typical slot-49 no-body cloth armor conversion this took TRI generation
    # from ~30s down to ~3s.
    """Build a BODYTRI for the armor by propagating UBE body slider deltas.

    `armor_shapes`: {shape_name -> verts_array (N, 3)} for armor pieces
                    in the destination NIF. Body deltas get propagated
                    to these verts via K-nearest-body-vertex IDW.
    `body_verts`: UBE body verts (used both for KD lookup and to copy
                  body-shape morphs verbatim when include_body_shapes=True)
    `body_osd`: parsed UBE body OSD (morph names like
                'BaseShape<SliderName>')
    `body_shape_name`: prefix in OSD morph names — stripped to produce
                      the slider name RaceMenu uses to look up morphs
    `include_body_shapes`: which body-region shapes to embed verbatim
                          (carrying body OSD morphs unmodified). Pass
                          a set of shape names to include only those
                          (e.g. {"BaseShape"} when the dest NIF has
                          BaseShape but not VirtualBody). Pass True
                          for the default {"BaseShape"}. Pass False
                          to omit body shapes entirely (correct for
                          armors like a slot-49 no-body cloth armor whose NIF has no
                          BaseShape — hand-built TRIs for these only
                          contain armor-piece entries).
    `extra_body_osds`: {shape_name -> OsdFile} for additional body-
                       region shapes (Hands, Feet) that have their
                       own OSDs. The morphs are emitted verbatim as
                       extra TriShapes in the per-armor TRI, so
                       NioOverride applies the same slider deltas to
                       e.g. an injected Hands shape inside an
                       equipped armor NIF that it applies to the
                       nude Hands NIF via its own TRI. Without this,
                       Hands/Feet inside armor stay at default while
                       nude Hands/Feet morph per RaceMenu sliders.

    Output TRI: one TriShape per armor shape (plus optional BaseShape).
    Each TriShape has TriMorphs named after the body slider — e.g.
    'BreastsBigger', 'BigButt'. Per-shape morphs share the slider name,
    which is what NioOverride looks up at runtime.
    """
    prefix = body_shape_name
    body_verts_arr = np.asarray(body_verts, dtype=np.float64)
    body_n = len(body_verts_arr)

    # Build KDTree ONCE for body verts; reused across all shapes.
    tree = cKDTree(body_verts_arr)

    # Defensive de-dup: if a shape appears in both `armor_shapes`
    # (K-NN propagation candidate) and `extra_body_osds` (verbatim
    # per-shape OSD), keep ONLY the OSD path — it's the authoritative
    # source for that shape's morphs. Without this, callers that don't
    # pre-filter would double-emit the shape's TriShape (one with
    # propagated body morphs, one with the OSD's own morphs).
    extra_names = set(extra_body_osds.keys()) if extra_body_osds else set()
    # Remember the original shape set BEFORE filtering, so the extras-
    # emit pass below still recognizes the shape as "present in dst".
    original_armor_names = set(armor_shapes)
    armor_shapes = {
        name: verts for name, verts in armor_shapes.items()
        if name not in extra_names
    }

    # Precompute per-shape K-NN data — using a HIGH K (= 16) so we
    # have enough neighbors to choose from per vert based on each
    # vert's distance from the body. The per-morph propagation step
    # below uses an adaptive K per vert (K=1 for body-hugging verts,
    # K=4 for medium standoff, K=16 for far stand-off pieces like
    # metal ornament strips or hanging tabards). Far stand-off verts
    # need a wider average of body movement to track the body's
    # overall reshape rather than just the single nearest vert
    # (which may sit in a region of body that barely moves for the
    # active slider).
    QUERY_K = 16
    qk = min(QUERY_K, body_n)
    shape_knn: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for armor_shape_name, av in armor_shapes.items():
        av_arr = np.asarray(av, dtype=np.float64)
        dists, neighbors = tree.query(av_arr, k=qk)
        if qk == 1:
            dists = dists[:, None]
            neighbors = neighbors[:, None]
        # Distance from each armor vert to its nearest body vert
        # — used per-vert below to decide which K to use.
        nearest_dist = dists[:, 0]
        shape_knn[armor_shape_name] = (neighbors, dists, nearest_dist)

    # Per-morph dense body delta buffer; reused across morphs.
    # Building it inside this scope rather than inside the per-shape
    # loop avoids re-allocating (29298, 3) floats per (shape, morph).
    morph_delta_buf = np.zeros((body_n, 3), dtype=np.float64)
    # Per-shape morph accumulator.
    shape_morphs: dict[str, list[TriMorph]] = {
        name: [] for name in armor_shapes
    }

    for body_m in body_osd.morphs:
        if not body_m.offsets:
            continue
        slider_name = (body_m.name[len(prefix):]
                       if body_m.name.startswith(prefix)
                       else body_m.name)

        # Fill the dense body-delta buffer for THIS morph (vectorized).
        # body_m.offsets is a list of (idx, dx, dy, dz) tuples; we
        # need a (body_n, 3) array. Vectorize via numpy gather.
        offsets_arr = np.asarray(body_m.offsets, dtype=np.float64)
        idxs = offsets_arr[:, 0].astype(np.int64)
        deltas = offsets_arr[:, 1:4]
        valid = (idxs >= 0) & (idxs < body_n)
        morph_delta_buf.fill(0.0)
        morph_delta_buf[idxs[valid]] = deltas[valid]

        # Adaptive K based on per-vert standoff. The nearest-body
        # distance controls how much averaging to use:
        #   * Hugging the body (dist < 0.4):  K=1 — track nearest
        #     body vert exactly so the armor stays glued to the body
        #   * Medium (0.4 <= dist < 1.0):    K=4 — small IDW average
        #     to smooth over CBBE-vs-UBE topology mismatch
        #   * Far stand-off (dist >= 1.0):   K=16 — wide IDW average
        #     so metal ornament strips, hanging tabards, etc. track
        #     the body's overall regional reshape rather than just
        #     one nearest body vert (which may live in a low-delta
        #     spot like the center-front sternum even when sides /
        #     hips are moving heavily under the slider).
        # This handles both small local sliders and big preset
        # sliders without the previous magnitude-based threshold.
        # Stand-off pieces don't need K=1 magnitude fidelity (they
        # weren't tracking specific body landmarks anyway); they
        # need to follow the body's bulk motion in the area.

        # Propagate this single morph to every armor shape using
        # the cached K-NN data — adaptive K per vert.
        for armor_shape_name, (neighbors, dists, nearest_dist) in shape_knn.items():
            # CONTINUOUS adaptive IDW (no discrete zones). The old scheme
            # split verts into hug (<0.4, K=4, 1/d^2), medium (0.4-1.0, K=4,
            # 1/d) and far (>=1.0, K=16, 1/d) buckets. Those hard thresholds
            # stepped both the neighbor count AND the weighting, so a DRAPING
            # piece (skirt / tasset) whose verts straddle 0.4 or 1.0 got
            # discontinuous deltas across the boundary — the surface FOLDED /
            # creased at those seams as the body morphed (the "skirt folds in
            # odd areas" report). The big K=4 -> K=16 jump at 1.0 was the
            # worst offender.
            #
            # Now: ALWAYS use all K neighbors, with an inverse-distance weight
            # whose exponent eases SMOOTHLY from 2 (body-hugging: nearest
            # dominates, armor stays glued, preserves the earlier waist-shear
            # fix) down to 1 (stand-off / drape: wide average that follows the
            # body's bulk reshape). Continuous in both neighbor set and
            # weighting -> the morph deforms the cloth smoothly with no seam
            # at any distance threshold.
            #   p = clip(2 - nearest_dist, 1, 2): d=0 -> p=2 (glued),
            #   d>=1 -> p=1 (drape), linear in between.
            p = np.clip(2.0 - nearest_dist, 1.0, 2.0)
            w = 1.0 / (np.power(dists, p[:, None]) + 1e-9)
            w /= w.sum(axis=1, keepdims=True)
            propagated = (morph_delta_buf[neighbors] * w[..., None]).sum(axis=1)
            # Per-vert extremity dampening: a long sleeve has its hand portion
            # rigged to finger/hand bones (extremity fraction ~1) and its
            # sleeve portion to forearm/upperarm bones (fraction ~0). Scaling
            # the propagated delta by (1 - fraction) gives the SLEEVE full
            # body-morph response (so it follows arm-region sliders and
            # doesn't get clipped through by the morphed arm) while keeping
            # FINGER verts near-zero (matches the nude actor's separate
            # Hands mesh, which doesn't body-morph). Smooth blend at the
            # wrist. Same logic catches thigh-high boots that span
            # foot/calf/thigh. No-op when fractions weren't supplied.
            if armor_vert_extremity_fractions is not None:
                ef = armor_vert_extremity_fractions.get(armor_shape_name)
                if ef is not None and len(ef) == len(propagated):
                    propagated = propagated * np.clip(
                        1.0 - np.asarray(ef, dtype=np.float64), 0.0, 1.0
                    )[:, None]
            magnitudes = np.linalg.norm(propagated, axis=1)
            keep = np.where(magnitudes >= min_delta)[0]
            if len(keep) == 0:
                continue
            kept_d = propagated[keep]
            shape_morphs[armor_shape_name].append(TriMorph(
                name=slider_name,
                offsets=[(int(i), float(d0), float(d1), float(d2))
                         for i, (d0, d1, d2) in zip(keep, kept_d)],
            ))

    # ---- Overlay-band morph-sync ----
    # A thin band lifted on top of a larger layer by the pass-8 band-lift must
    # MORPH IN LOCKSTEP with the layer beneath it, or it re-sinks under body
    # sliders even though the base mesh is fixed. For each thin band that, in
    # this (already-lifted) UBE mesh, sits clearly OUTSIDE another cloth shape,
    # replace its per-slider deltas with the under-layer's nearest-vertex
    # deltas so the layering gap holds at every slider value. Large body-
    # conforming pieces keep their own morph. Fully gated: if the band can't be
    # classified (e.g. verts aren't lifted) it simply no-ops and the base lift
    # still stands.
    try:
        from scipy.spatial import cKDTree as _cKDTree
        _OM_SIZE_FRAC, _OM_R, _OM_MIN, _OM_THRESH, _OM_SYNC_R = 0.40, 3.0, 30, 0.20, 5.0
        _names = list(armor_shapes)
        _maxv = max((len(np.asarray(v)) for v in armor_shapes.values()), default=0)
        for A in _names:
            av = np.asarray(armor_shapes[A], dtype=np.float64)
            if _maxv <= 0 or len(av) >= _OM_SIZE_FRAC * _maxv or A not in shape_knn:
                continue   # large body-conforming piece -> keep own morph
            a_nd = shape_knn[A][2]   # A's per-vert clearance to the body
            under_v, under_tag = [], []
            for B in _names:
                if B == A or B not in shape_knn:
                    continue
                bv = np.asarray(armor_shapes[B], dtype=np.float64)
                b_nd = shape_knn[B][2]
                dd, ui = _cKDTree(bv).query(av, k=1, distance_upper_bound=_OM_R)
                ov = np.isfinite(dd)
                if int(ov.sum()) < _OM_MIN:
                    continue
                if float(np.median((a_nd - b_nd[np.where(ov, ui, 0)])[ov])) < _OM_THRESH:
                    continue   # A does not sit clearly outside B
                under_v.append(bv)
                under_tag.extend((B, int(i)) for i in range(len(bv)))
            if not under_v:
                continue
            ddu, idu = _cKDTree(np.vstack(under_v)).query(
                av, k=1, distance_upper_bound=_OM_SYNC_R)
            okv = np.isfinite(ddu)
            if not okv.any():
                continue
            dense = {}
            for Bn in {t[0] for t in under_tag}:
                dB = {}
                for m in shape_morphs.get(Bn, []):
                    a2 = np.zeros((len(armor_shapes[Bn]), 3), dtype=np.float64)
                    for (vi, d0, d1, d2) in m.offsets:
                        if 0 <= vi < len(a2):
                            a2[vi] = (d0, d1, d2)
                    dB[m.name] = a2
                dense[Bn] = dB
            new_morphs = []
            for sl in set().union(*(set(dense[Bn]) for Bn in dense)):
                arr = np.zeros((len(av), 3), dtype=np.float64)
                for vi in np.where(okv)[0]:
                    Bn, Bi = under_tag[idu[vi]]
                    da = dense.get(Bn, {}).get(sl)
                    if da is not None:
                        arr[vi] = da[Bi]
                mag = np.linalg.norm(arr, axis=1)
                keep = np.where(mag >= min_delta)[0]
                if len(keep):
                    new_morphs.append(TriMorph(
                        name=sl,
                        offsets=[(int(i), float(arr[i, 0]), float(arr[i, 1]),
                                  float(arr[i, 2])) for i in keep]))
            if new_morphs:
                shape_morphs[A] = new_morphs
    except Exception:
        pass

    # Build the shape list with the BODYTRI carrier listed FIRST, then
    # the remaining shapes ordered by MORPH PRIORITY (body-conforming
    # cloth before rigid metal props).
    #
    # Why ordering matters — the per-NIF morph cap. NioOverride/SKEE
    # only applies BodyMorph deltas to roughly the FIRST ~9 shapes a
    # TRI lists (verified in-game: a 12-shape a vanilla armor TRI morphed shapes
    # 1-9 and silently dropped 10-12; reordering moved which shapes got
    # cut). So whichever shapes land past the cap simply don't morph.
    #
    # The fix is to spend the limited early slots on the shapes that
    # VISIBLY need to track the body — cloth, skirts, leggings, panties —
    # and push rigid props (pauldrons, armbands, chains, gems, buckles)
    # to the tail, where the cap drops them harmlessly (they barely
    # morph anyway). Within the cloth group, bigger movers come first.
    #
    # Carrier stays first regardless (hand-authored UBE convention; the
    # BODYTRI NiStringExtraData lives on it).
    def _max_morph_mag(name: str) -> float:
        mags = [
            float(np.linalg.norm(
                np.asarray(m.offsets, dtype=np.float64)[:, 1:4], axis=1).max())
            for m in shape_morphs.get(name, []) if m.offsets
        ]
        return max(mags) if mags else 0.0

    def _priority_key(name: str):
        nlow = name.lower()
        # Three tiers (ascending): cloth (0) < body-worn rigid (1) <
        # weapon/jewelry attachment (2). This only decides ORDER; the goal
        # is for NOTHING to be cut (the merge keeps the total under the
        # cap). Ordering still matters as a safety net: if a NIF ever does
        # exceed the cap, the least-morph-needing props drop last. Within a
        # tier, larger max-morph-magnitude first (negated for ascending).
        if any(kw in nlow for kw in _ATTACHMENT_KEYWORDS):
            tier = 2
        elif any(kw in nlow for kw in _RIGID_PROP_KEYWORDS):
            tier = 1
        else:
            tier = 0
        return (tier, -_max_morph_mag(name))

    non_carrier = [n for n in armor_shapes if n != carrier_shape_name]
    non_carrier.sort(key=_priority_key)
    ordered_names: list[str] = []
    if carrier_shape_name is not None and carrier_shape_name in armor_shapes:
        ordered_names.append(carrier_shape_name)
    ordered_names.extend(non_carrier)

    # (Removed 2026-05-29) The over-cap warning that used to print here
    # was based on the misdiagnosed "skee per-NIF ~9-shape cap" (#118,
    # #139). After the PIRT shape-count fix, every cloth shape we list
    # gets BodyMorph deltas applied in-game — there is nothing to warn
    # about. Priority ordering above still runs (rigid props to the
    # tail, big movers first) as a soft authoring convention, but the
    # ordering no longer determines which shapes morph.

    tri_shapes: list[TriShape] = []
    for armor_shape_name in ordered_names:
        morphs = shape_morphs[armor_shape_name]
        if morphs:
            tri_shapes.append(TriShape(
                name=armor_shape_name, morphs=morphs))

    # Optionally include body-region shapes (BaseShape, etc.) verbatim
    # from the OSD. Critical: only include shapes that the dest NIF
    # ACTUALLY contains. Hand-built TRIs only list shapes that exist
    # in the NIF; adding extra body shapes appears to confuse some
    # NioOverride lookups (e.g. a slot-49 no-body cloth armor whose NIF has no
    # BaseShape — including BaseShape in the TRI may break the
    # corset's morph application).
    body_shape_set: set[str]
    if include_body_shapes is True:
        body_shape_set = {"BaseShape"}
    elif include_body_shapes is False:
        body_shape_set = set()
    else:
        body_shape_set = set(include_body_shapes)
    if body_shape_set:
        body_morphs: list[TriMorph] = []
        for body_m in body_osd.morphs:
            if body_m.name.startswith(prefix):
                slider_name = body_m.name[len(prefix):]
            else:
                slider_name = body_m.name
            if not body_m.offsets:
                continue
            body_morphs.append(TriMorph(
                name=slider_name,
                offsets=[(idx, dx, dy, dz)
                         for idx, dx, dy, dz in body_m.offsets],
            ))
        if body_morphs:
            # OSD only has BaseShape data, so we can only emit body
            # morphs under that name. If caller wants VirtualBody too,
            # they'd need a separate OSD with VirtualBody morphs.
            if "BaseShape" in body_shape_set:
                tri_shapes.insert(0, TriShape(
                    name="BaseShape", morphs=body_morphs))

    # Extra body-region OSDs (Hands, Feet — UBE ships these as
    # separate OSDs because their morphs operate on different
    # topologies than BaseShape). For each extra OSD, emit a TriShape
    # named after the destination shape, with morphs renamed to strip
    # the prefix (so slider lookups still work the same way they do
    # on the nude actor's Hands/Feet TRI).
    if extra_body_osds:
        for shape_name, extra_osd in extra_body_osds.items():
            if (shape_name not in original_armor_names
                    and shape_name not in body_shape_set):
                # No matching shape in the dst NIF — skip (avoids
                # NioOverride attempting to morph a non-existent
                # shape and producing a no-op warning at runtime).
                continue
            extra_prefix = shape_name  # OSD morphs prefix == shape name
            extra_morphs: list[TriMorph] = []
            for m in extra_osd.morphs:
                if not m.offsets:
                    continue
                slider_name = (m.name[len(extra_prefix):]
                               if m.name.startswith(extra_prefix)
                               else m.name)
                extra_morphs.append(TriMorph(
                    name=slider_name,
                    offsets=[(idx, dx, dy, dz)
                             for idx, dx, dy, dz in m.offsets],
                ))
            if extra_morphs:
                tri_shapes.append(TriShape(
                    name=shape_name, morphs=extra_morphs))

    # The `version` field on TriFile is misnamed (legacy) — bytes 4-5 of
    # the PIRT file are actually the SHAPE COUNT, which `TriFile.save()`
    # writes from `len(self.shapes)` regardless of what we pass here. So
    # this kwarg is now effectively cosmetic; we leave it for symmetry.
    from .tri import TRI_VERSION
    return TriFile(version=TRI_VERSION, shapes=tri_shapes)
