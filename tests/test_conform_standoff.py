"""Guard for conform_to_source_standoff: the post-warp pass that fixes "slot-32
chest too far out" by reeling each cloth vert back to its ORIGINAL clearance from
the body. Must be safe-by-construction: pull-IN only (never push out), clamp to a
min clearance, no-op for already-tight / far-from-body cloth."""
import numpy as np
from src.nif_convert import conform_to_source_standoff


def _flat_body(n=21, span=5.0):
    # grid of body verts on the z=0 plane, all normals = +z (outward = up)
    xs = np.linspace(-span, span, n)
    gx, gy = np.meshgrid(xs, xs)
    v = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    nrm = np.tile(np.array([0.0, 0.0, 1.0]), (len(v), 1))
    return v.astype(np.float64), nrm.astype(np.float64)


def test_explicit_blend_split_the_difference():
    bv, bn = _flat_body()
    # source cloth hugged at +0.5; warp over-projected it to +2.0
    src = np.array([[0.0, 0.0, 0.5]])
    cur = np.array([[0.0, 0.0, 2.0]])
    # explicit blend=0.5 -> midpoint of source (0.5) and current (2.0) = 1.25
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                     min_clearance=0.25, blend=0.5)
    assert abs(out[0, 2] - 1.25) < 0.05, out         # split the difference
    assert src[0, 2] < out[0, 2] < cur[0, 2]         # pulled IN, but not all the way
    # explicit blend=1.0 -> all the way to the source clearance
    out1 = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                      min_clearance=0.25, blend=1.0)
    assert abs(out1[0, 2] - 0.5) < 0.05, out1


def test_adaptive_tight_keeps_room_loose_restores_drape():
    bv, bn = _flat_body()
    # TIGHT vert (source 0.5, over-projected to 2.0): adaptive default keeps ROOM
    # (low blend) -> looser than the 1.25 midpoint -> won't clip the morphed body.
    st = np.array([[0.0, 0.0, 0.5]]); ct = np.array([[0.0, 0.0, 2.0]])
    ot = conform_to_source_standoff(st, bv, bn, ct, bv, bn)   # default = adaptive
    assert ot[0, 2] > 1.25, ot                         # more room than the midpoint
    assert ot[0, 2] < ct[0, 2]                          # still reeled in a bit
    # LOOSE vert (source 5.0 drape, over-projected to 8.0): adaptive restores it
    # near its source drape (blend -> 1.0) -> closes the forsworn-style float gap.
    sl = np.array([[0.0, 0.0, 5.0]]); cl = np.array([[0.0, 0.0, 8.0]])
    ol = conform_to_source_standoff(sl, bv, bn, cl, bv, bn, max_body_dist=20.0)
    assert abs(ol[0, 2] - 5.0) < 0.6, ol               # back to ~source drape


def test_already_tight_vert_not_pushed_out():
    bv, bn = _flat_body()
    # current (0.30) already tighter than source (0.50) -> must NOT push out
    src = np.array([[0.0, 0.0, 0.5]])
    cur = np.array([[0.0, 0.0, 0.30]])
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn, min_clearance=0.25)
    assert out[0, 2] <= 0.30 + 1e-6                   # never looser than current
    assert out[0, 2] >= 0.0                            # never driven into the body


def test_min_clearance_floor():
    bv, bn = _flat_body()
    # source hugged very tight (0.05); even at full blend the clamp keeps it >= floor
    src = np.array([[0.0, 0.0, 0.05]])
    cur = np.array([[0.0, 0.0, 2.0]])
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                     min_clearance=0.25, blend=1.0)
    assert out[0, 2] >= 0.25 - 1e-6                   # not pulled below clearance


def test_far_vert_untouched():
    bv, bn = _flat_body()
    # cloth far above the body (skirt hem) -> beyond max_body_dist -> no change
    src = np.array([[0.0, 0.0, 20.0]])
    cur = np.array([[0.0, 0.0, 20.0]])
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                     min_clearance=0.25, max_body_dist=10.0)
    assert abs(out[0, 2] - 20.0) < 1e-6


def _bust_body(n=21, span=5.0, z=90.0):
    # flat body grid lifted into the bust Z-band (so in_bust triggers)
    bv, bn = _flat_body(n, span)
    bv = bv.copy()
    bv[:, 2] = z
    return bv, bn


def _bust_body_with_nipple(n=21, span=5.0, z=90.0, bump=0.7):
    # flat chest grid in the bust band with a single protruding nipple bump at
    # the centre (a sharp local protrusion the body-protrusion measure detects).
    bv, bn = _bust_body(n, span, z)
    ci = int(np.argmin(np.linalg.norm(bv[:, :2], axis=1)))   # vert nearest (0,0)
    bv[ci, 2] += bump
    return bv, bn, ci


def test_bust_flat_chest_stays_close():
    # #175 closer fit: over a FLAT chest panel (no protrusion) the bust pass must
    # NOT shove fabric out to the old blanket 1.2u -- it keeps the close fit.
    bv, bn = _bust_body(z=90.0)
    src = np.array([[0.0, 0.0, 90.5]]); cur = np.array([[0.0, 0.0, 90.5]])
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn, bust_clearance=1.2)
    assert abs(out[0, 2] - 90.5) < 0.06, out            # left close (NOT pushed to 91.2)


def test_bust_nipple_weight_raises_clearance():
    # #175: with the body's Breast03 nipple weight supplied, fabric over the
    # nipple is pushed out to ~bust_clearance; without it, only BUST_FLAT_CLEARANCE.
    bv, bn, ci = _bust_body_with_nipple(z=90.0, bump=0.7)
    nip = np.zeros(len(bv)); nip[ci] = 0.7              # mark the nipple vert
    nip_z = bv[ci, 2]
    cur = np.array([[0.0, 0.0, nip_z + 0.1]])           # 0.1u over the tip -> would poke
    out = conform_to_source_standoff(cur.copy(), bv, bn, cur, bv, bn,
                                     bust_clearance=0.9, ube_body_nipple=nip)
    assert out[0, 2] - nip_z >= 0.8, (out, nip_z)       # cleared to ~bust_clearance
    # the SAME geometry with NO nipple weight -> only the small flat clearance
    out0 = conform_to_source_standoff(cur.copy(), bv, bn, cur, bv, bn,
                                      bust_clearance=0.9)
    assert out0[0, 2] - nip_z < 0.6, out0               # not over-cleared on flat default


def test_bust_nipple_caught_even_when_not_nearest():
    # neighbourhood-worst: a fabric vert whose NEAREST body point is a FLAT vert,
    # with the nipple bump just to the side, still gets pushed out to clear the
    # nipple (the old nearest-only logic missed this off-centre case).
    bv, bn, ci = _bust_body_with_nipple(z=90.0, bump=0.7)
    side = bv[ci].copy(); side[0] += 0.5; side[2] = 90.0 + 0.3   # low, just off the tip
    cur = np.array([side])
    out = conform_to_source_standoff(cur.copy(), bv, bn, cur, bv, bn, bust_clearance=1.2)
    assert out[0, 2] > 90.0 + 0.3 + 1e-3, out           # pushed out (nipple nearby)


def test_bust_clearance_only_inside_band():
    # the SAME tight cloth, but body OUTSIDE the bust band -> pull-in-only (no push)
    bv, bn = _flat_body()                               # body at z=0 (not bust band)
    src = np.array([[0.0, 0.0, 0.7]]); cur = np.array([[0.0, 0.0, 0.7]])
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn, bust_clearance=1.2)
    assert out[0, 2] <= 0.7 + 1e-6                      # NOT pushed out below the bust


def test_vert_count_mismatch_is_noop():
    bv, bn = _flat_body()
    src = np.array([[0.0, 0.0, 0.5], [1.0, 0.0, 0.5]])   # 2 source verts
    cur = np.array([[0.0, 0.0, 2.0]])                      # 1 current vert
    out = conform_to_source_standoff(src, bv, bn, cur, bv, bn)
    assert np.allclose(out, cur)                          # unchanged


class _FakeShape:
    def __init__(self, verts, tris=None, normals=None, bone_weights=None):
        self.verts = verts
        self.tris = tris
        self.normals = normals
        self.bone_weights = bone_weights


def test_body_normals_computed_when_missing():
    # BodySlide bodies often ship ZERO/absent vertex normals -> the conform pass
    # would silently no-op (push along zero vectors). _body_normals_or_compute
    # must recompute valid unit normals from the triangles. #175
    from src.nif_convert import _body_normals_or_compute
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    t = np.array([[0, 1, 2]])
    nm = _body_normals_or_compute(_FakeShape(v, t, np.zeros((3, 3))))  # zeroed normals
    assert nm is not None
    assert np.allclose(np.linalg.norm(nm, axis=1), 1.0)    # unit
    assert np.allclose(np.abs(nm[:, 2]), 1.0)              # +/-Z face of an XY triangle


def test_body_nipple_weight_from_breast03():
    from src.nif_convert import _body_nipple_weight
    bw = {"R Breast03": [(0, 0.7), (1, 0.3)], "NPC Spine2": [(2, 0.9)]}
    w = _body_nipple_weight(_FakeShape(np.zeros((3, 3)), bone_weights=bw))
    assert w is not None
    assert w[0] == 0.7 and w[1] == 0.3 and w[2] == 0.0     # spine vert is NOT a nipple
    # a body with NO breast bones -> None (bust pass falls back to flat clearance)
    assert _body_nipple_weight(
        _FakeShape(np.zeros((2, 3)), bone_weights={"NPC Spine2": [(0, 1.0)]})) is None
