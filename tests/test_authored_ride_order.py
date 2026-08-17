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

"""Guard for the layer ride's STACK ORDER (`#authored-ride-order`).

The ride re-derives every layer above the first from the one beneath it, so the
layer it ranks innermost is the only one that keeps its own fit. Ranking by
median distance to the body gets that wrong on a plain waist stack -- a narrow
belt hugging the waist has a smaller median than the corset it is worn over --
and `_authored_layer_depth` reads the relation off the author's geometry
instead.

These cover the two failure modes that were measured on real pieces:

  * the ORDERING arithmetic. `depth[outer] += 1` counted how many layers a
    shape sits on, which is not an ordering: it ties a shape sitting on several
    narrow trims with one sitting on a single dress and then ignores a direct
    verdict between those two. Longest path cannot.
  * CYCLES. A real garment can weave over a layer in one place and under it in
    another, so the relation is not always a DAG and the sort must still
    terminate with the strongest verdicts intact.
"""
import numpy as np

from src import nif_convert as nc


def _rel(*pairs):
    """(outer, inner, weight) triples -> the relation dict."""
    return {(o, i): w for o, i, w in pairs}


def test_longest_path_beats_a_count_on_a_direct_verdict():
    """The failure the count arithmetic shipped.

    3 sits on two narrow trims (0, 1); 2 sits only on the dress (0). A count
    gives 3 depth 2 and 2 depth 1, placing 2 first -- even though the direct
    verdict says 3 is UNDER 2.
    """
    depth = nc._stack_depth_from_relation(
        4, _rel((3, 0, 0.5), (3, 1, 0.5), (2, 0, 0.5), (2, 3, 0.9)))
    assert depth[3] < depth[2], (
        f"a direct '2 sits on 3' verdict must order them, got {depth}")
    assert depth[0] == 0 and depth[1] == 0


def test_depth_is_strictly_increasing_up_a_stack():
    depth = nc._stack_depth_from_relation(
        4, _rel((1, 0, 0.4), (2, 1, 0.4), (3, 2, 0.4)))
    assert [depth[i] for i in range(4)] == [0, 1, 2, 3]


def test_unrelated_layers_tie_at_zero():
    """A shape the author never places against anything must not be forced
    into the stack -- it has no counterpart to ride."""
    depth = nc._stack_depth_from_relation(3, _rel((1, 0, 0.4)))
    assert depth[2] == 0
    assert depth[0] == 0 and depth[1] == 1


def test_a_cycle_is_broken_at_its_weakest_edge():
    """A weave has no consistent order. The two confident verdicts survive; the
    marginal one is the one dropped."""
    depth = nc._stack_depth_from_relation(
        3, _rel((1, 0, 0.9), (2, 1, 0.8), (0, 2, 0.05)))
    assert depth[0] < depth[1] < depth[2], (
        f"the strong edges must survive the cycle break, got {depth}")


def test_every_layer_gets_a_depth_even_with_no_relation():
    depth = nc._stack_depth_from_relation(3, {})
    assert depth == {0: 0, 1: 0, 2: 0}


# --------------------------------------------------------------------------
# The geometric half: three nested cylinders, the middle one narrow, so the
# median-distance ranking and the authored relation disagree the way they do on
# the reported waist stack.
# --------------------------------------------------------------------------

def _shell(radius, z0, z1, nz=8, nt=24):
    """A cylindrical band whose tris wind OUTWARD.

    The winding is asserted below, not assumed: the whole comparison is a sign
    along these normals, so a band wound the other way makes every verdict come
    out exactly backwards and the test passes or fails for the wrong reason.
    """
    zs = np.linspace(z0, z1, nz)
    th = np.linspace(0, 2 * np.pi, nt, endpoint=False)
    v = np.array([[radius * np.cos(t), radius * np.sin(t), z]
                  for z in zs for t in th], dtype=np.float64)
    tris = []
    for iz in range(nz - 1):
        for it in range(nt):
            a = iz * nt + it
            b = iz * nt + (it + 1) % nt
            c = a + nt
            d = b + nt
            tris.append([a, b, c])
            tris.append([b, d, c])
    return v, np.asarray(tris, dtype=np.int64)


def test_shell_fixture_faces_outward():
    v, t = _shell(10.0, 0.0, 20.0)
    n = nc._vertex_normals_from_tris(v, t)
    radial = v.copy()
    radial[:, 2] = 0.0
    radial /= np.linalg.norm(radial, axis=1, keepdims=True)
    assert float(np.einsum('ij,ij->i', n, radial).mean()) > 0.9


def _entry(radius, z0, z1):
    v, t = _shell(radius, z0, z1)
    return {"sv": v, "sn": None, "tris": t}


def test_authored_depth_orders_a_narrow_outer_band_correctly():
    """A narrow band at radius 11 worn OVER a tall shell at radius 10.

    The band is the outer layer, and `_authored_layer_depth` must say so from
    the geometry -- this is the shape of the case where the live median ranking
    inverts, because a narrow band hugging its neighbour has the smaller median
    distance to the body.
    """
    inner = _entry(10.0, 0.0, 20.0)
    band = _entry(11.0, 8.0, 12.0)
    depth = nc._authored_layer_depth([inner, band], near=2.0)
    assert depth is not None
    assert depth[0] < depth[1], (
        f"the band at radius 11 sits on the shell at radius 10, got {depth}")


def test_authored_depth_ranks_three_nested_shells_outward():
    e = [_entry(10.0, 0.0, 20.0), _entry(10.8, 6.0, 16.0),
         _entry(11.6, 8.0, 12.0)]
    depth = nc._authored_layer_depth(e, near=2.0)
    assert depth is not None
    assert depth[0] < depth[1] < depth[2], depth


def test_coincident_duplicates_do_not_stack():
    """One surface authored twice (two materials) is not two layers."""
    e = [_entry(10.0, 0.0, 20.0), _entry(10.0, 0.0, 20.0)]
    depth = nc._authored_layer_depth(e, near=2.0)
    assert depth is not None
    assert depth[0] == depth[1] == 0, depth


# --------------------------------------------------------------------------
# THE WIRING. `_authored_layer_depth` being correct is worth nothing if the
# ride does not use it -- and it did not, for as long as the flag was opt-in
# and unreachable. Flipping that default changed the geometry of ~39% of the
# pack's multi-layer pieces and NOT ONE TEST MOVED, which is the gap these
# close: that the ride ranks by the authored relation, and that it is on.
# --------------------------------------------------------------------------

class _FakeShape:
    """The narrow surface `_ride_layers_on_reference._eligible` actually reads."""

    def __init__(self, name, verts, tris):
        self.name = name
        self.verts = verts
        self.tris = tris
        self.normals = None
        self.textures = {"diffuse": "x.dds"}
        self.shader = None
        self.has_global_to_skin = False


def _merge(*bands):
    """Concatenate cylinder bands into ONE shape, re-indexing the tris."""
    vs, ts, off = [], [], 0
    for v, t in bands:
        vs.append(v)
        ts.append(t + off)
        off += len(v)
    return np.concatenate(vs), np.concatenate(ts)


def _job(name, geom, push=0.0):
    v, t = geom
    src = _FakeShape(name, v, t)
    # A "fitted" position that differs from source, so a ride that re-derives a
    # layer from its reference is visible as a change.
    out = v.copy()
    out[:, 0] += push
    return {"src": src, "verts": out, "verts_modified": False}


def _ride_reference(jobs, body):
    """Run the ride and report which layer kept its own fit (the reference)."""
    before = {j["src"].name: j["verts"].copy() for j in jobs}
    nc._ride_layers_on_reference(jobs, body_verts=body)
    unmoved = [j["src"].name for j in jobs
               if np.allclose(j["verts"], before[j["src"].name])]
    return unmoved


def test_authored_ride_order_is_on_by_default():
    """The flag is inverted (`CBBE2UBE_NO_AUTHORED_RIDE_ORDER`) and defaults ON.
    It shipped opt-in and unreachable for long enough to hide the defect it was
    written to fix; a default this load-bearing gets pinned."""
    assert nc.AUTHORED_RIDE_ORDER is True


def test_ride_reference_is_the_authored_innermost_not_the_closest():
    """The case the median ranking gets backwards, built to its real shape.

    CONCENTRIC CYLINDERS CANNOT REPRODUCE IT -- the outer one is further from
    the body everywhere, so the median agrees with the author and the test
    passes for the wrong reason (my first draft did exactly that; the premise
    assertion below is what caught it). The inversion needs the inner layer to
    FLARE away from the body outside the overlap, which is the real geometry: a
    waist belt is measured only where the body is closest to it, while the
    garment it is worn over also spans the hip flare and is scored there too.

    So: a `gown` that hugs at the waist (r=10) and flares above and below
    (r=12), and a `belt` (r=10.5) worn over it at the waist only. The belt is
    nearer the body on median; the author says the gown is underneath.
    """
    body, _ = _shell(9.5, -5.0, 25.0)
    gown = _job("gown", _merge(_shell(12.0, 0.0, 7.5),
                               _shell(10.0, 8.0, 12.0),
                               _shell(12.0, 12.5, 20.0)), push=0.3)
    # DIFFERENT fitted offsets on purpose: the ride sets a rider to
    # `source + the reference's displacement`, so if both layers carry the SAME
    # displacement the ride is an arithmetic no-op and nothing moves whichever
    # layer is chosen -- the test then cannot see the ordering at all.
    belt = _job("belt", _shell(10.5, 8.0, 12.0), push=0.9)

    # PREMISE: the belt really is the one the median ranking would pick. Without
    # this the test cannot distinguish "the fix works" from "there was nothing
    # to fix".
    from scipy.spatial import cKDTree
    bt = cKDTree(body)
    med = {j["src"].name: float(np.median(bt.query(j["src"].verts)[0]))
           for j in (gown, belt)}
    assert med["belt"] < med["gown"], (
        f"premise broken -- median must favour the belt, got {med}")

    kept = _ride_reference([gown, belt], body)
    assert kept == ["gown"], (
        f"the authored-innermost gown must be the reference, kept {kept}")


def test_the_median_ranking_really_does_get_that_case_wrong(monkeypatch):
    """The negative control. With the authored order OFF, the same stack must
    ride the BELT -- otherwise the test above proves nothing about the fix."""
    monkeypatch.setattr(nc, "AUTHORED_RIDE_ORDER", False)
    body, _ = _shell(9.5, -5.0, 25.0)
    gown = _job("gown", _merge(_shell(12.0, 0.0, 7.5),
                               _shell(10.0, 8.0, 12.0),
                               _shell(12.0, 12.5, 20.0)), push=0.3)
    belt = _job("belt", _shell(10.5, 8.0, 12.0), push=0.9)
    kept = _ride_reference([gown, belt], body)
    assert kept == ["belt"], (
        f"the median ranking is supposed to pick the belt here, kept {kept}")
