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

"""#gap-aware-follow: armour moves like the skin directly under it, by how
tight it fits.

Measured on pieces that carry a body copy: where the armour sat 0.4-0.9u off
the skin it carried roughly half the skin's breast weight, so a bounce brought
the skin through it. Tested on a sphere 'body' (BaseShape, on Spine2 and a
breast bone) under concentric sphere 'armour' on Spine2 and Spine1: tight
armour takes the body's rows, loose armour keeps its own, and between the two
the follow is graded rather than switched."""
import numpy as np

import src.nif_convert as nc
from src import nif_convert_weights as nw

BODY_BONES = ("NPC Spine2 [Spn2]", "L Breast02")
ARMOUR_BONES = ("NPC Spine2 [Spn2]", "NPC Spine1 [Spn1]")
CENTRE = (0.0, 0.0, 95.0)


def _nif(path, armour_r, with_body=True):
    from tests.synthetic_nif import build_skinned_shapes_nif, uv_sphere
    av, at, an = uv_sphere(armour_r, centre=CENTRE)
    shapes = [("Armour", av, at, an, ARMOUR_BONES)]
    if with_body:
        bv, bt, bn = uv_sphere(8.0, centre=CENTRE)
        shapes.append(("BaseShape", bv, bt, bn, BODY_BONES))
    return build_skinned_shapes_nif(path, shapes)


def _col(path, shape, bone):
    s = next(x for x in nc._pynifly().NifFile(filepath=str(path)).shapes
             if x.name == shape)
    c = np.zeros(len(s.verts))
    for i, w in (s.bone_weights or {}).get(bone, []):
        c[int(i)] = w
    return c, list(s.bone_names or [])


def test_kill_switch_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "GAP_AWARE_FOLLOW", False)
    f = tmp_path / "x_1.nif"
    f.write_bytes(b"not a nif")
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) == 0
    assert f.read_bytes() == b"not a nif"


def test_tight_armour_takes_the_skin_row_under_it(tmp_path):
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _nif(tmp_path / "meshes" / "x" / "piece_1.nif", 8.3)
    body_b, _ = _col(f, "BaseShape", "L Breast02")
    before, _ = _col(f, "Armour", "L Breast02")
    assert not before.any()             # control: the armour does not follow yet
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) > 0
    after, names = _col(f, "Armour", "L Breast02")
    # Concentric spheres on the same layout: vert i sits over body vert i.
    assert np.abs(after - body_b).mean() < 0.1
    # The armour bone it no longer uses is dropped, never left at zero weight
    # (a zero-weight bone in the palette is an equip CTD).
    spine1, _ = _col(f, "Armour", "NPC Spine1 [Spn1]")
    assert "NPC Spine1 [Spn1]" not in names or spine1.max() > nc._WRITE_MIN


def test_loose_armour_keeps_its_own_weights(tmp_path):
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _nif(tmp_path / "meshes" / "x" / "piece_1.nif", 12.5)   # 4.5u of room
    before = f.read_bytes()
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) == 0
    assert f.read_bytes() == before


def test_follow_is_graded_by_the_gap(tmp_path):
    # 2.5u off: part of the skin's row, not all of it and not none.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    tight = _nif(tmp_path / "t" / "meshes" / "x" / "piece_1.nif", 8.3)
    mid = _nif(tmp_path / "m" / "meshes" / "x" / "piece_1.nif", 10.5)
    nw._gap_aware_follow(tight, biped_slots=nc.BIPED_SLOT32_BIT)
    nw._gap_aware_follow(mid, biped_slots=nc.BIPED_SLOT32_BIT)
    body_b, _ = _col(tight, "BaseShape", "L Breast02")
    sel = body_b > 0.99
    t_, _ = _col(tight, "Armour", "L Breast02")
    m_, _ = _col(mid, "Armour", "L Breast02")
    assert sel.any()
    assert 0.1 < m_[sel].mean() < t_[sel].mean() - 0.1


def test_body_slot_without_skin_under_it_left_alone(tmp_path):
    # Slot 32 hides the actor's body; with no body copy there is no skin under
    # the piece to follow.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _nif(tmp_path / "meshes" / "x" / "piece_1.nif", 8.3, with_body=False)
    before = f.read_bytes()
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) == 0
    assert f.read_bytes() == before


def test_hands_and_feet_left_alone(tmp_path):
    # The actor's hands and feet are separate meshes, not the body surface.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    for bit in (nc.BIPED_SLOT33_BIT, nc.BIPED_SLOT37_BIT):
        f = _nif(tmp_path / str(bit) / "meshes" / "x" / "piece_1.nif", 8.3)
        before = f.read_bytes()
        assert nw._gap_aware_follow(f, biped_slots=bit) == 0
        assert f.read_bytes() == before


def test_closest_on_body_matches_brute_force():
    from scipy.spatial import cKDTree
    from tests.synthetic_nif import uv_sphere
    bv, bt, _ = uv_sphere(8.0, rings=10, segs=16, centre=CENTRE)
    bV, bT = np.asarray(bv, float), np.asarray(bt, np.int64)
    vt = [[] for _ in bV]
    for ti, t in enumerate(bT):
        for j in t:
            vt[j].append(ti)
    vt = [np.asarray(x, np.int64) for x in vt]
    rng = np.random.default_rng(0)
    d = rng.normal(size=(200, 3))
    P = np.asarray(CENTRE) + d / np.linalg.norm(d, axis=1, keepdims=True) * rng.uniform(8.1, 11.0, (200, 1))
    Q, tri, bary = nw._closest_on_body(P, bV, bT, cKDTree(bV), vt, chunk=37)
    assert np.allclose((bV[bT[tri]] * bary[:, :, None]).sum(1), Q)
    # The closest triangle by centroid-to-point is among the candidates.
    for i in range(len(P)):
        sub_q, _, _ = nw._closest_on_body(P[i:i + 1], bV, bT, cKDTree(bV), vt, k=len(bV))
        assert np.linalg.norm(Q[i] - P[i]) <= np.linalg.norm(sub_q[0] - P[i]) + 1e-9


def _cards_nif(path):
    # Ten 4-vert 'fur cards' standing straight out of the body, base 0.2u off
    # the skin and tip 3.2u off, around the bone boundary of the body's rows.
    from tests.synthetic_nif import build_skinned_shapes_nif, uv_sphere
    bv, bt, bn = uv_sphere(8.0, centre=CENTRE)
    c = np.asarray(CENTRE)
    verts, tris, nrms = [], [], []
    for k in range(10):
        th = 2.0 * np.pi * k / 10.0
        d = np.array([np.cos(th), np.sin(th), 0.15 * (k % 3 - 1)])
        d /= np.linalg.norm(d)
        side = np.cross(d, [0.0, 0.0, 1.0])
        side /= np.linalg.norm(side)
        b0 = len(verts)
        for r_ in (8.2, 11.2):
            for s_ in (-0.5, 0.5):
                verts.append(tuple(c + d * r_ + side * s_))
                nrms.append(tuple(np.cross(side, d)))
        tris += [(b0, b0 + 1, b0 + 3), (b0, b0 + 3, b0 + 2)]
    return build_skinned_shapes_nif(path, [("Fur", verts, tris, nrms, ARMOUR_BONES),
                                           ("BaseShape", bv, bt, bn, BODY_BONES)])


def _rows(path, shape):
    s = next(x for x in nc._pynifly().NifFile(filepath=str(path)).shapes
             if x.name == shape)
    R = {}
    for b, prs in (s.bone_weights or {}).items():
        for i, w in prs:
            R.setdefault(int(i), {})[b] = w
    return [R.get(i, {}) for i in range(len(s.verts))]


def _card_spread(rows):
    worst = 0.0
    for k in range(10):
        card = rows[4 * k:4 * k + 4]
        for b in {b for r in card for b in r}:
            ws = [r.get(b, 0.0) for r in card]
            worst = max(worst, max(ws) - min(ws))
    return worst


def test_small_piece_standing_off_the_skin_rides_as_one(monkeypatch, tmp_path):
    # A per-vert blend shears a fur card (base on the skin, tip past the band):
    # creases 87 -> 278 on a fur trim. Each card must take one row.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    f = _cards_nif(tmp_path / "on" / "meshes" / "x" / "piece_1.nif")
    assert nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT) > 0
    assert _card_spread(_rows(f, "Fur")) < 1e-3
    # Control: without the piece rule the same cards split.
    monkeypatch.setattr(nc, "_GAF_PIECE", 0.0)
    g = _cards_nif(tmp_path / "off" / "meshes" / "x" / "piece_1.nif")
    nw._gap_aware_follow(g, biped_slots=nc.BIPED_SLOT32_BIT)
    assert _card_spread(_rows(g, "Fur")) > 0.2


def test_new_bone_transform_is_anchored_in_the_shapes_own_frame():
    # A bone the follow adds must land in the SHAPE's skin frame: copying the
    # body's absolute skin-to-bone is the in-game explosion
    # `_derive_anchored_stb` exists for, and is wrong outright on a shape with
    # a non-identity global-to-skin.
    from tests.synthetic_nif import pynifly_available
    if not pynifly_available():
        return
    pyn = nc._pynifly()

    def tb(M):
        _, proto = nc._stb_to_mat4(pyn.TransformBuf())
        return pyn.TransformBuf.from_matrix(type(proto)(M.tolist()))

    def rt(deg, t):
        a = np.deg2rad(deg)
        M = np.eye(4)
        M[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
        M[:3, 3] = t
        return M

    spine, breast = rt(0, (0, 0, -90.0)), rt(10, (-4.0, -6.0, -95.0))
    G = rt(30, (3.0, 1.0, -2.0))                    # the shape's global-to-skin
    body = {"NPC Spine2 [Spn2]": tb(spine), "L Breast02": tb(breast)}

    class Shape:
        def get_shape_skin_to_bone(self, b):
            return tb(spine @ np.linalg.inv(G)) if b == "NPC Spine2 [Spn2]" else None

    out = nw._gaf_new_bone_stb("L Breast02", Shape(),
                               {"NPC Spine2 [Spn2]": [(0, 1.0)]}, body,
                               {"L Breast02": "L Breast01", "L Breast01": "NPC Spine2 [Spn2]"})
    got, _ = nc._stb_to_mat4(out)
    assert np.allclose(got, breast @ np.linalg.inv(G), atol=1e-4)


def test_the_four_bone_cap_runs_without_failing(tmp_path):
    """Armour and skin on three bones each, sharing one: mixed rows carry five
    bones, so the neighbourhood-ranked 4-influence cap must run. A
    failure inside the pass is swallowed (recorded, not raised) -- the one
    that dropped the cap's neighbour lists left every piece's weights
    unfollowed while each test here still passed -- so check the record."""
    from tests.synthetic_nif import build_skinned_shapes_nif, uv_sphere
    from src import nif_convert_telemetry as tel
    av, at, an = uv_sphere(10.5, centre=CENTRE)   # 2.5u off: a graded, MIXED follow
    bv, bt, bn = uv_sphere(8.0, centre=CENTRE)
    f = build_skinned_shapes_nif(tmp_path / "cap.nif", [
        ("Armour", av, at, an, ("NPC Spine2 [Spn2]", "NPC Spine1 [Spn1]", "NPC Pelvis [Pelv]")),
        ("BaseShape", bv, bt, bn, ("NPC Spine2 [Spn2]", "L Breast02", "NPC Spine [Spn0]"))])
    # every vert on all three of its shape's bones (the builder splits by vertex)
    pyn = nc._pynifly()
    nf = pyn.NifFile(filepath=str(f))
    for sh in nf.shapes:
        nv = len(sh.verts)
        for bn in sh.bone_names:
            sh.setShapeWeights(bn, [(i, 1.0 / 3.0) for i in range(nv)])
    nf.save()
    before = dict(tel._PASS_FAILURES)
    n = nw._gap_aware_follow(f, biped_slots=nc.BIPED_SLOT32_BIT)
    new = {k: v for k, v in tel._PASS_FAILURES.items() if before.get(k, 0) != v}
    assert not new, new
    assert n > 0
