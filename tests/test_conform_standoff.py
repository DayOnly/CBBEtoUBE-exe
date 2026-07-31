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

"""Guard for conform_to_source_standoff: the post-warp pass that fixes "slot-32
chest too far out" by reeling each cloth vert back to its ORIGINAL clearance from
the body. Must be safe-by-construction: pull-IN only (never push out), clamp to a
min clearance, no-op for already-tight / far-from-body cloth."""
from pathlib import Path

import numpy as np
import src.nif_convert as nc_mod
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


# --- #bust-neighbourhood-spacing ------------------------------------------
# The bust push-out is applied PER GARMENT VERTEX, so each vertex must clear
# every body point the surface around it spans -- a span set by the garment's
# own vertex spacing, not by a constant. A fixed 6-neighbour / 4.0u sample
# misses a tip that pokes between two vertices of a coarse garment (measured:
# 0.125u of nipple clearance delivered against a 0.55u requirement).

def _nipple_body(n=61, span=6.0, z=92.0, tip=0.8, bump=1.2):
    """Chest plane in the bust band with a protruding tip, at the REAL body's
    vertex density (0.2u here vs the UBE body's 0.359u) so the sampling geometry
    this fix is about is reproduced rather than idealised."""
    xs = np.linspace(-span, span, n)
    gx, gy = np.meshgrid(xs, xs)
    v = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1)
    # Deterministic jitter so no two body verts are EXACTLY equidistant from a
    # garment vert. A perfect grid is pathological here: tied distances let a
    # k=64 query return a different "nearest 6" than a k=6 query, which is a
    # property of the fixture, not of the pass. Real body meshes are irregular.
    v[:, 0] += 0.03 * np.sin(7.0 * v[:, 1] + 1.3)
    v[:, 1] += 0.03 * np.sin(5.0 * v[:, 0] + 0.7)
    r = np.linalg.norm(v[:, :2], axis=1)
    v[:, 2] += tip * np.exp(-(r ** 2) / (2 * (bump / 2.5) ** 2))    # sharp tip
    nrm = np.tile(np.array([0.0, 0.0, 1.0]), (len(v), 1))
    nip = 0.6 * np.exp(-(r ** 2) / (2 * bump ** 2))
    return v.astype(float), nrm.astype(float), nip, int(np.argmin(r))


def _coarse_garment(spacing=1.2, z=92.35, half=3):
    """A garment at realistic coarse spacing, offset so NO vertex sits over the
    tip -- the poke is on the surface between them."""
    o = np.arange(-half, half + 1) * spacing + spacing / 2.0
    gx, gy = np.meshgrid(o, o)
    return np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1)


def test_coarse_garment_still_clears_a_tip_between_its_verts(monkeypatch):
    """The tip sits between garment verts, inside the patch a vertex spans
    (spacing 1.2u) but outside the ~0.67u that k=6 actually reaches."""
    bv, bn, nip, c = _nipple_body()
    cur = _coarse_garment()
    src = cur.copy()
    tip_z = bv[c, 2]
    monkeypatch.setattr(nc_mod, "BUST_SPACING_AWARE", False)
    tight = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                       ube_body_nipple=nip)
    monkeypatch.setattr(nc_mod, "BUST_SPACING_AWARE", True)
    aware = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                       ube_body_nipple=nip)
    inner = np.linalg.norm(cur[:, :2], axis=1) < 1.5
    # NEGATIVE CONTROL: today's sampling really does under-clear this tip.
    assert tight[inner, 2].min() - tip_z < 0.5, (
        "the fixed sample already clears the tip; this test would be vacuous")
    assert aware[inner, 2].min() > tight[inner, 2].min() + 1e-3, (
        tight[inner, 2].min(), aware[inner, 2].min())
    # PUSH-OUT ONLY: never tighter than the fixed version, anywhere.
    assert np.all(aware[:, 2] >= tight[:, 2] - 1e-6)


def test_spacing_aware_leaves_a_fine_garment_alone(monkeypatch):
    """Today's k nearest are always kept and the spacing radius only ADDS to
    them, so once the garment is fine enough that its radius falls inside what
    k already reaches, sampling is bit for bit unchanged and dense pieces cannot
    drift. Threshold measured on this fixture: the body's smallest k=6 reach is
    0.271u, so a 0.15u garment (radius 0.225u) is provably inside it."""
    bv, bn, nip, _c = _nipple_body()
    cur = _coarse_garment(spacing=0.15, half=20)
    src = cur.copy()
    monkeypatch.setattr(nc_mod, "BUST_SPACING_AWARE", False)
    a = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    monkeypatch.setattr(nc_mod, "BUST_SPACING_AWARE", True)
    b = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    assert np.allclose(a, b, atol=1e-6), np.abs(a - b).max()



# --- #bust-morph-residual -------------------------------------------------
# `req` is a BIND-pose requirement but the character in game is MORPHED, and a
# nipple travels up to 5.35u at runtime. The armour follows (generate_armor_tri
# gives a hugging vert the delta of its NEAREST body vert), so what survives is
# the RESIDUAL: the poking body point and the vert covering it are different
# body verts, and a slider that RESHAPES moves them differently. Measured: 1 of
# 23 bust sliders turned +0.284u of clearance into -0.165u while following at
# ratio 1.00 by magnitude -- which is why the poke was PRESET-DEPENDENT and why
# every bind-pose metric called the piece clean.

class _FakeOsdMorph:
    def __init__(self, name, offsets):
        self.name = name
        self.offsets = offsets


class _FakeOsd:
    def __init__(self, morphs):
        self.morphs = morphs


def _reshaping_osd(bv, c, nrm):
    """One slider that moves the TIP outward and its neighbours not at all --
    a reshape, not an inflate. An inflate moves both alike and must cost
    nothing; this is the case the residual exists for."""
    offs = []
    for i in range(len(bv)):
        if i == c:
            d = nrm[i] * 1.2
            offs.append((i, float(d[0]), float(d[1]), float(d[2])))
    return _FakeOsd([_FakeOsdMorph("BaseShapeReshape", offs)])


def test_morph_residual_demands_more_over_a_reshaping_slider(monkeypatch):
    bv, bn, nip, c = _nipple_body(n=34)
    cur = _coarse_garment(spacing=1.2, half=3)
    src = cur.copy()
    monkeypatch.setattr(nc_mod, "_find_ube_body_osd", lambda: Path("fake.osd"))
    monkeypatch.setattr(nc_mod, "_cached_osd_load",
                        lambda _p: _reshaping_osd(bv, c, bn))
    nc_mod._BODY_MORPH_STACK_CACHE.clear()
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", False)
    plain = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                       ube_body_nipple=nip)
    nc_mod._BODY_MORPH_STACK_CACHE.clear()
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", True)
    resid = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                       ube_body_nipple=nip)
    near = np.linalg.norm(cur[:, :2] - bv[c, :2], axis=1) < 2.0
    assert near.any()
    # NEGATIVE CONTROL: without it the pass really does stop short here.
    assert resid[near, 2].max() > plain[near, 2].max() + 1e-4, (
        plain[near, 2].max(), resid[near, 2].max())
    # PUSH-OUT ONLY: it may never end up tighter anywhere.
    assert np.all(resid[:, 2] >= plain[:, 2] - 1e-6)


def test_morph_residual_costs_nothing_for_a_pure_inflate(monkeypatch):
    """A slider that moves the tip and its surroundings ALIKE has zero residual
    -- the garment follows it exactly -- so it must not buy any clearance. This
    is what keeps the requirement from inflating every garment by the full 5u
    of runtime travel."""
    bv, bn, nip, _c = _nipple_body(n=34)
    cur = _coarse_garment(spacing=1.2, half=3)
    src = cur.copy()
    offs = [(i, float(bn[i, 0]), float(bn[i, 1]), float(bn[i, 2]))
            for i in range(len(bv))]                    # uniform outward = inflate
    monkeypatch.setattr(nc_mod, "_find_ube_body_osd", lambda: Path("fake.osd"))
    monkeypatch.setattr(nc_mod, "_cached_osd_load",
                        lambda _p: _FakeOsd([_FakeOsdMorph("BaseShapeInflate", offs)]))
    nc_mod._BODY_MORPH_STACK_CACHE.clear()
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", False)
    a = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    nc_mod._BODY_MORPH_STACK_CACHE.clear()
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", True)
    b = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    assert np.allclose(a, b, atol=1e-6), np.abs(a - b).max()


def test_morph_residual_is_a_no_op_without_an_osd(monkeypatch):
    bv, bn, nip, _c = _nipple_body(n=34)
    cur = _coarse_garment(spacing=1.2, half=3)
    src = cur.copy()
    monkeypatch.setattr(nc_mod, "_find_ube_body_osd", lambda: None)
    nc_mod._BODY_MORPH_STACK_CACHE.clear()
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", True)
    a = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    monkeypatch.setattr(nc_mod, "BUST_MORPH_RESIDUAL", False)
    b = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    assert np.array_equal(a, b)


def _surface_gap_at_tip(cur, out, bv, c, tris):
    """Clearance of the garment SURFACE directly over the tip (barycentric
    point-in-triangle on xy), which a per-vertex assertion cannot see."""
    tip = bv[c]
    best = None
    for t in tris:
        a, b, cc = out[t[0]], out[t[1]], out[t[2]]
        d = ((b[1]-cc[1])*(a[0]-cc[0]) + (cc[0]-b[0])*(a[1]-cc[1]))
        if abs(d) < 1e-12:
            continue
        u = ((b[1]-cc[1])*(tip[0]-cc[0]) + (cc[0]-b[0])*(tip[1]-cc[1])) / d
        v = ((cc[1]-a[1])*(tip[0]-cc[0]) + (a[0]-cc[0])*(tip[1]-cc[1])) / d
        w = 1.0 - u - v
        if u < -1e-9 or v < -1e-9 or w < -1e-9:
            continue
        z = u*a[2] + v*b[2] + w*cc[2]
        if best is None or z < best:
            best = z
    return None if best is None else best - tip[2]


def _patch(spacing=2.0, z=92.35, half=3):
    """Coarse garment patch with tris, offset so no vertex sits over the tip."""
    o = np.arange(-half, half + 1) * spacing + spacing / 2.0
    gx, gy = np.meshgrid(o, o)
    v = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1)
    n = len(o)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            tris.append([a, a + 1, a + n]); tris.append([a + 1, a + n + 1, a + n])
    return v, np.asarray(tris, dtype=np.int64)


# --- #bust-surface-req ----------------------------------------------------
# `worst` asks whether each garment VERTEX stands `req` clear. The defect is the
# SURFACE: the tightest point sits in a triangle interior, and a surface can sag
# 0.855u below vertices that all pass. That is why `req - worst` was negative at
# every nipple vert (mean -0.921) and raising `req` by 3.5u moved delivered
# clearance by 0.04u -- the push was never firing. These guard the surface test.

def test_surface_req_pushes_where_the_vertices_pass_but_the_surface_sags(monkeypatch):
    """The tip pokes BETWEEN the verts: every vertex is clear, the surface is
    not. The vertex rule alone cannot see this -- that is the whole point."""
    # spacing 3.0 reproduces the real geometry: the tightest surface point sits
    # far enough from any vertex that the vertex rule passes while the surface
    # pokes (-0.003u). At 2.0 the vertex rule already clears it and the negative
    # control below correctly calls the test vacuous.
    bv, bn, nip, c = _nipple_body(n=34, tip=1.1)
    cur, tris = _patch(spacing=3.0)
    src = cur.copy()
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", False)
    vert_only = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                           ube_body_nipple=nip, tris=tris)
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", True)
    surf = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                      ube_body_nipple=nip, tris=tris)
    g0 = _surface_gap_at_tip(cur, vert_only, bv, c, tris)
    g1 = _surface_gap_at_tip(cur, surf, bv, c, tris)
    assert g0 is not None and g1 is not None
    # NEGATIVE CONTROL: the vertex rule really does leave the surface short here.
    assert g0 < 0.5, f"vertex rule already clears the surface ({g0:.3f}); vacuous"
    assert g1 > g0 + 1e-3, (g0, g1)
    # PUSH-OUT ONLY: never tighter than the vertex rule, anywhere.
    assert np.all(surf[:, 2] >= vert_only[:, 2] - 1e-6)


def test_surface_req_ignores_body_points_beside_the_triangle(monkeypatch):
    """Only points that project INSIDE a triangle are covered by it. Letting a
    point beside it demand a push is how an earlier attempt inflated the whole
    chest 0.434 -> 2.297u, so a tip well outside the patch must cost nothing."""
    bv, bn, nip, _c = _nipple_body(n=34, tip=1.1)
    # patch offset far to one side: the tip at the origin is not under it
    cur, tris = _patch(spacing=1.0, half=2)
    cur = cur + np.array([6.0, 6.0, 0.0])
    src = cur.copy()
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", False)
    a = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                   ube_body_nipple=nip, tris=tris)
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", True)
    b = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                   ube_body_nipple=nip, tris=tris)
    assert np.allclose(a, b, atol=1e-6), np.abs(a - b).max()


def test_surface_req_is_a_no_op_without_tris(monkeypatch):
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", True)
    bv, bn, nip, _c = _nipple_body(n=34, tip=1.1)
    cur, _tris = _patch(spacing=3.0)
    src = cur.copy()
    a = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", False)
    b = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    assert np.array_equal(a, b)


def test_surface_req_survives_junk_tris(monkeypatch):
    """Degenerate adjacency must fall back, not raise: this call sits inside the
    try that records `errors during shape copy`, and a raise there silently
    removes the whole conform (it has happened twice)."""
    monkeypatch.setattr(nc_mod, "BUST_SURFACE_REQ", True)
    bv, bn, nip, _c = _nipple_body(n=34, tip=1.1)
    cur, _t = _patch(spacing=3.0)
    src = cur.copy()
    base = conform_to_source_standoff(src, bv, bn, cur, bv, bn, ube_body_nipple=nip)
    for junk in (np.zeros((0, 3), np.int64), np.array([[0, 1, 999999]], np.int64),
                 np.array([1, 2, 3], np.int64)):
        out = conform_to_source_standoff(src, bv, bn, cur, bv, bn,
                                         ube_body_nipple=nip, tris=junk)
        assert out.shape == base.shape and np.all(np.isfinite(out))
