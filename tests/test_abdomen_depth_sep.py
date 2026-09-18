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

"""Guard for the multi-layer cloth lift (`_separate_abdomen_layered_cloth_depth`).
The per-shape warp's min-standoff clamp can COLLAPSE a garment's stacking order
(belts sink into a corset; an undershirt pokes through a coat; a cinch belt
buries inside the coat). The pass classifies order from the SOURCE and re-spreads
the collapsed layers.

v4 (2026-06-11) — PER-REGION SOURCE ORDER-FIELD RESTORATION. v3 gave each
shape PAIR one global consistency-gated edge, but real garments stack
REGION-dependently (a layered top's fabric is under the chest plate over most
of the torso yet tucked OVER it at the neckline; v3's single edge flipped the
minority region — 72-98% of locally-reversed locations shipped inverted). v4
applies v3's sign-consistency idea at NEIGHBOURHOOD scale: per-vert source
gaps to each other shape, smoothed over the shape's own nearby verts, gate
per-vert ORDER constraints (lift-only, vs the under-layer's current verts):
  * a CONSISTENT region (coat>undershirt everywhere; the neckline tuck)
    -> constraints survive smoothing -> restored,
  * an INTERLEAVED weave (belt alternating over/under a coat) -> smooths to
    ~0 -> NO constraint -> co-planar (= source), the layered-coat fix preserved.
v4 keeps v3's phase-1 fallback (output-frame ordering off the fit body) and
its NO-body-floor rule (a blanket floor flung offset-transform armor)."""
import numpy as np
from src import nif_convert as nc


class _Src:
    """Stand-in for a pynifly source shape — only `.verts` is read."""
    def __init__(self, verts):
        self.verts = verts


def _patch(y_src, y_warp, x_range, z_range, n):
    """A cloth patch: SOURCE verts at clearance `y_src`, WARP/output verts at
    `y_warp` (body plane at Y=0, +Y outward). Same topology in both frames."""
    xs = np.linspace(*x_range, n)
    zs = np.linspace(*z_range, n)
    src = np.array([[x, y_src, z] for x in xs for z in zs], dtype=np.float64)
    warp = np.array([[x, y_warp, z] for x in xs for z in zs], dtype=np.float64)
    return src, warp


def _patch_alt(y_lo, y_hi, y_warp, x_range, z_range, n):
    """A cloth patch whose SOURCE clearance ALTERNATES y_lo/y_hi vert-to-vert —
    an INTERLEAVED layer (sits both inside and outside a flat under-layer)."""
    xs = np.linspace(*x_range, n)
    zs = np.linspace(*z_range, n)
    pts, warp, flip = [], [], False
    for x in xs:
        for z in zs:
            pts.append([x, y_hi if flip else y_lo, z])
            warp.append([x, y_warp, z])
            flip = not flip
    return np.array(pts, dtype=np.float64), np.array(warp, dtype=np.float64)


def _job(src_v, warp_v):
    return {"override_skin": {"weights": {"NPC Spine": []}},
            "src": _Src(src_v), "verts": warp_v, "verts_modified": False}


def _body_plane(n=24):
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(70, 96, n)
    bv = np.array([[x, 0.0, z] for x in xs for z in zs], dtype=np.float64)
    bn = np.tile(np.array([0.0, 1.0, 0.0]), (len(bv), 1))
    return bv, bn


def test_sunk_overlay_band_lifted_above_under_layer():
    bv, bn = _body_plane()
    # Under-layer B: LARGE, sits at clearance 1.0 in both source and warp.
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)   # 400 verts
    # Overlay band A: SMALL; in the SOURCE it sits CONSISTENTLY outside B (1.5),
    # but the warp SANK it to B's depth (1.0) -> must be lifted back outside B.
    a_src, a_warp = _patch(1.5, 1.0, (-4, 4), (78, 90), 10)   # 100 verts
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)

    pushed = nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)

    assert pushed > 0, "the sunk overlay band must be lifted"
    a_y = float(np.median(np.asarray(A["verts"])[:, 1]))
    b_y = float(np.median(np.asarray(B["verts"])[:, 1]))
    assert B["verts_modified"] is False, "large under-layer must NOT move"
    assert a_y > b_y + 0.1, f"band must end up outside under-layer: A={a_y} B={b_y}"


def test_interleaved_belt_lands_coplanar_via_inner():
    """THE layered-coat fix. undershirt (inner) < coat, and < belt, but belt~coat
    INTERLEAVE (no edge). v2 force-ranked belt under the coat (buried); v3 leaves
    belt/coat unordered and lifts BOTH vs the undershirt -> belt lands co-planar
    with the coat (visible), never buried beneath it."""
    bv, bn = _body_plane()
    # Undershirt U: large, innermost (source clearance 0.5), collapsed to 1.0.
    u_src, u_warp = _patch(0.5, 1.0, (-8, 8), (72, 94), 20)          # 400
    # Coat: clearly outside U (1.0), collapsed to 1.0.
    c_src, c_warp = _patch(1.0, 1.0, (-4, 4), (78, 90), 12)          # 144
    # Belt: clearly outside U, but INTERLEAVES the coat (0.9/1.1), collapsed 1.0.
    b_src, b_warp = _patch_alt(0.9, 1.1, 1.0, (-4, 4), (78, 90), 12)  # 144
    U, C, Bl = _job(u_src, u_warp), _job(c_src, c_warp), _job(b_src, b_warp)

    nc._separate_abdomen_layered_cloth_depth(
        [U, C, Bl], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)

    u_y = float(np.median(np.asarray(U["verts"])[:, 1]))
    c_y = float(np.median(np.asarray(C["verts"])[:, 1]))
    b_y = float(np.median(np.asarray(Bl["verts"])[:, 1]))
    assert U["verts_modified"] is False, "innermost undershirt must not move"
    assert c_y > u_y + 0.1, f"coat must clear undershirt: coat={c_y} U={u_y}"
    assert b_y > u_y + 0.1, f"belt must clear undershirt: belt={b_y} U={u_y}"
    assert abs(b_y - c_y) < 0.06, (
        f"interleaved belt must land CO-PLANAR with the coat, not buried/stacked:"
        f" belt={b_y} coat={c_y}")


def test_phase1_no_source_body_still_orders():
    """Audit fix 1: phase-1 has no inline body, so v2 silently no-opped. v3 falls
    back to the UBE fit body (output frame) — a layer left sunk in the output is
    still lifted above its under-layer."""
    bv, bn = _body_plane()
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)
    # A sits just barely above B in the OUTPUT (1.05) -> consistent edge, but
    # below the GAP, so it must be lifted to clear B by LAYER_STACK_GAP.
    a_src, a_warp = _patch(1.05, 1.05, (-4, 4), (78, 90), 10)
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)
    pushed = nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn)   # NO source body -> phase-1 path
    assert pushed > 0, "phase-1 must no longer be a silent no-op"
    a_y = float(np.median(np.asarray(A["verts"])[:, 1]))
    b_y = float(np.median(np.asarray(B["verts"])[:, 1]))
    assert a_y > b_y + 0.1, f"phase-1 lift must clear under-layer: A={a_y} B={b_y}"


def test_interleaved_pair_no_force_stacking():
    """An interleaved pair (gap sign ~50/50, neither consistently outer) must
    NOT be force-stacked. Under v3 this asserted an exact no-op (one global
    edge per pair -> the only failure mode was a full-gap burial). v4 is
    REGIONAL: the weave's interior cancels to ~0 -> untouched, but the
    synthetic's edge stripes (`_patch_alt` makes full-width rows, and the
    boundary row is coherently under B across the whole patch in the source)
    legitimately read as locally-ordered, so sub-GAP rim adjustments are
    allowed. The protective property (the layered-coat/elven regressions) is that no
    vert moves by anything close to a visible burial — a force-stack moves
    the whole shape by >= LAYER_STACK_GAP, so assert max displacement stays
    under it and the shapes stay co-planar in the median."""
    bv, bn = _body_plane()
    b_src, b_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)
    a_src, a_warp = _patch_alt(0.8, 1.2, 1.0, (-4, 4), (78, 90), 10)
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)
    a0, b0 = np.asarray(A["verts"]).copy(), np.asarray(B["verts"]).copy()
    nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)
    a1, b1 = np.asarray(A["verts"]), np.asarray(B["verts"])
    a_move = float(np.linalg.norm(a1 - a0, axis=1).max())
    b_move = float(np.linalg.norm(b1 - b0, axis=1).max())
    assert a_move < nc.LAYER_STACK_GAP, (
        f"interleaved weave must not be force-stacked: A moved {a_move:.3f}")
    assert b_move < nc.LAYER_STACK_GAP + 0.06, (
        f"under-layer rim adjustment must stay sub-burial: B moved {b_move:.3f}")
    assert abs(float(np.median(a1[:, 1])) - float(np.median(b1[:, 1]))) < 0.06, (
        "interleaved pair must remain co-planar in the median")


def test_disjoint_layers_no_floor():
    """Audit fix 2: two layers that don't OVERLAP get no edge -> nothing is
    lifted (no blanket body floor that would fling isolated/offset verts)."""
    bv, bn = _body_plane()
    a_src, a_warp = _patch(1.0, 1.0, (-9, -6), (72, 78), 10)
    b_src, b_warp = _patch(1.0, 1.0, (6, 9), (88, 94), 10)
    A, B = _job(a_src, a_warp), _job(b_src, b_warp)
    assert nc._separate_abdomen_layered_cloth_depth(
        [A, B], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn) == 0
    assert A["verts_modified"] is False and B["verts_modified"] is False


def test_regional_reversal_restored():
    """THE multi-layer top fix (v4). The plate sits OUTSIDE the top over most of the
    torso, but at the NECKLINE a coherent region of the top is tucked OVER the
    plate in the source. The warp collapsed both to one depth. v3's single
    global edge (plate outside top, frac ~0.9) lifted the plate everywhere,
    force-flipping the neckline tuck; v4 must restore BOTH regions: plate over
    top on the torso, top over plate at the neckline."""
    bv, bn = _body_plane()
    # Top: large, source clearance 0.7 on the torso (z < 86) but 1.4 in a
    # coherent neckline band (z >= 88) where it overlays the plate (1.0).
    xs = np.linspace(-8, 8, 20)
    zs = np.linspace(72, 94, 20)
    t_src = np.array([[x, 1.4 if z >= 88 else 0.7, z] for x in xs for z in zs],
                     dtype=np.float64)
    t_warp = np.array([[x, 1.0, z] for x in xs for z in zs], dtype=np.float64)
    # Plate: source clearance 1.0 everywhere (over the top on the torso,
    # under it at the neckline).
    p_src, p_warp = _patch(1.0, 1.0, (-8, 8), (72, 94), 20)
    T, P = _job(t_src, t_warp), _job(p_src, p_warp)

    pushed = nc._separate_abdomen_layered_cloth_depth(
        [T, P], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn)
    assert pushed > 0, "both reversal regions must be restored"

    tv, pv = np.asarray(T["verts"]), np.asarray(P["verts"])
    torso_t = tv[tv[:, 2] < 84][:, 1]
    torso_p = pv[pv[:, 2] < 84][:, 1]
    neck_t = tv[tv[:, 2] >= 90][:, 1]
    neck_p = pv[pv[:, 2] >= 90][:, 1]
    assert float(np.median(torso_p)) > float(np.median(torso_t)) + 0.1, (
        f"plate must clear top on the torso: plate="
        f"{float(np.median(torso_p)):.3f} top={float(np.median(torso_t)):.3f}")
    assert float(np.median(neck_t)) > float(np.median(neck_p)) + 0.1, (
        f"top must clear plate at the neckline (the v3 bulldozed region): "
        f"top={float(np.median(neck_t)):.3f} "
        f"plate={float(np.median(neck_p)):.3f}")


def test_chest_pass_source_order_gate():
    """The cleavage depth-separation pass picks the LARGEST chest shape as
    authority and pushes co-planar receivers 0.4u behind it — source-blind,
    it shoved a corset rim and belts behind the chest plate (they sit
    OVER the plate in the source), creating inversions the abdomen restore
    then had to fight. With source body info the pass must SKIP receiver
    verts whose source order says they're outside the authority, and still
    push genuine inner layers (a bra under the fabric)."""
    n = 16
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(88, 112, n)
    bv = np.array([[x, 0.0, z] for x in xs for z in zs], dtype=np.float64)
    bn = np.tile(np.array([0.0, 1.0, 0.0]), (len(bv), 1))

    def _patch_chest(y_src, y_warp, x_range, z_range, m):
        xs_ = np.linspace(*x_range, m)
        zs_ = np.linspace(*z_range, m)
        src = np.array([[x, y_src, z] for x in xs_ for z in zs_],
                       dtype=np.float64)
        warp = np.array([[x, y_warp, z] for x in xs_ for z in zs_],
                        dtype=np.float64)
        return src, warp

    def _chest_job(src_v, warp_v):
        return {"override_skin": {"weights": {"NPC L Breast": []}},
                "src": _Src(src_v), "verts": warp_v,
                "verts_modified": False}

    # Authority (largest): plate at clearance 1.0 in source and output.
    p_src, p_warp = _patch_chest(1.0, 1.0, (-9, 9), (90, 110), 14)   # 196
    # Receiver A: bra-like, UNDER the plate in source (0.6), warped
    # co-planar (1.0) -> genuine z-fight, must be pushed behind.
    bra_src, bra_warp = _patch_chest(0.6, 1.0, (-6, 6), (92, 100), 8)
    # Receiver B: rim-like, OVER the plate in source (1.4), warped
    # co-planar (1.0) -> must be LEFT ALONE (the abdomen pass lifts it).
    rim_src, rim_warp = _patch_chest(1.4, 1.0, (-6, 6), (102, 108), 8)
    P = _chest_job(p_src, p_warp)
    BRA = _chest_job(bra_src, bra_warp)
    RIM = _chest_job(rim_src, rim_warp)

    pushed = nc._separate_chest_layered_cloth_depth(
        [P, BRA, RIM], body_verts=bv, body_normals=bn,
        source_body_verts=bv, source_body_normals=bn)
    assert pushed > 0, "the genuine inner bra layer must still be pushed"
    bra_y = np.asarray(BRA["verts"])[:, 1]
    assert float(np.median(bra_y)) < 1.0 - 0.2, (
        f"bra must sit behind the authority: {float(np.median(bra_y)):.3f}")
    assert RIM["verts_modified"] is False, (
        "source-outside receiver (rim over the plate) must NOT be pushed "
        "behind the authority")


def test_noop_single_layer():
    bv, bn = _body_plane()
    a_src, a_warp = _patch(1.0, 1.0, (-4, 4), (80, 90), 10)
    A = _job(a_src, a_warp)
    assert nc._separate_abdomen_layered_cloth_depth(
        [A], body_verts=bv, body_normals=bn, cbbe_body_verts=bv,
        source_body_verts=bv, source_body_normals=bn) == 0
    assert A["verts_modified"] is False
