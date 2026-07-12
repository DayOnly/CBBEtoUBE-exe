"""Unit tests for the warp-introduced torso self-intersection repair
(nif_convert._self_intersecting_pairs / _relax_shape_self_intersection).

The full pass (folded into _conform_fitted_to_body via _selfint_overrides) needs a live UBE body ref +
NIFs, so it's exercised in-game / by the offline sample script; here we lock down
the pure geometry core: the detector finds real crossings (not topological
neighbours), and the relaxation reduces them while never pushing a vert below the
body standoff and never moving a physics-chain vert.
"""
import numpy as np
import pytest
from scipy.spatial import cKDTree

from src import nif_convert as nc


def _sheet(y_of, nx=7, nz=7, x0=-3.0, z0=95.0, dx=1.0, dz=1.0, base=0):
    """Grid sheet in the torso z-band. `y_of(ix)` gives the Y (depth) per column."""
    verts, tris = [], []
    for iz in range(nz):
        for ix in range(nx):
            verts.append([x0 + ix * dx, float(y_of(ix)), z0 + iz * dz])
    for iz in range(nz - 1):
        for ix in range(nx - 1):
            a = base + iz * nx + ix
            tris.append([a, a + 1, a + nx])
            tris.append([a + 1, a + nx + 1, a + nx])
    return verts, tris


def _two_layers():
    """Inner (ramps outward with x) + outer (flat) sheet -> they CROSS where the
    inner ramp overtakes the outer, exactly the blouse-through-corset geometry."""
    nx = nz = 7
    iv, it = _sheet(lambda ix: 0.6 + 0.45 * ix, nx, nz, base=0)          # inner ramp
    ov, ot = _sheet(lambda ix: 2.2, nx, nz, base=nx * nz)                # outer flat
    return np.array(iv + ov, float), np.array(it + ot, np.int64), nx * nz


def _flat_body(n=400):
    """A body plane at Y=0 behind both layers, outward normals +Y."""
    xs = np.linspace(-6, 6, 20)
    zs = np.linspace(90, 116, 20)
    X, Z = np.meshgrid(xs, zs)
    V = np.stack([X.ravel(), np.zeros(X.size), Z.ravel()], 1).astype(float)
    N = np.tile(np.array([0.0, 1.0, 0.0]), (len(V), 1))
    return V, N, cKDTree(V)


def test_detector_finds_crossing_and_ignores_neighbours():
    v, tris, _ = _two_layers()
    pairs = nc._self_intersecting_pairs(v, tris)
    assert len(pairs) > 0, "ramp crossing the flat layer must be detected"
    # every reported pair must be a genuine non-adjacent pair (no shared vertex)
    for i, j in pairs:
        assert not (set(tris[i].tolist()) & set(tris[j].tolist()))


def test_detector_zero_on_clean_parallel_layers():
    # two parallel non-crossing sheets -> no self-intersection
    nx = nz = 7
    iv, it = _sheet(lambda ix: 1.0, nx, nz, base=0)
    ov, ot = _sheet(lambda ix: 2.5, nx, nz, base=nx * nz)
    v = np.array(iv + ov, float)
    tris = np.array(it + ot, np.int64)
    assert len(nc._self_intersecting_pairs(v, tris)) == 0


def test_relax_reduces_crossings_and_respects_body_standoff():
    v, tris, _ = _two_layers()
    Vb, Nb, tree = _flat_body()
    before = len(nc._self_intersecting_pairs(v, tris))
    assert before > 0
    chain = np.zeros(len(v), bool)
    out, moved = nc._relax_shape_self_intersection(v, tris, chain, Vb, Nb, tree, target=0)
    after = len(nc._self_intersecting_pairs(out, tris))
    assert after < before, f"relax should reduce crossings ({before}->{after})"
    assert moved > 0
    # body-safety: no vert sits below the standoff (signed dist along +Y == its Y)
    d, bi = tree.query(out)
    signed = np.einsum('ij,ij->i', out - Vb[bi], Nb[bi])
    assert signed.min() >= nc._SELFINT_STANDOFF - 1e-6, "a vert sank below body standoff"


def test_relax_never_moves_chain_verts():
    v, tris, inner_count = _two_layers()
    Vb, Nb, tree = _flat_body()
    # mark all inner-layer verts as physics-chain -> they must stay put
    chain = np.zeros(len(v), bool)
    chain[:inner_count] = True
    out, _ = nc._relax_shape_self_intersection(v, tris, chain, Vb, Nb, tree, target=0)
    assert np.allclose(out[:inner_count], v[:inner_count]), "chain verts must not move"


def test_relax_noop_when_already_at_target():
    nx = nz = 7
    iv, it = _sheet(lambda ix: 1.0, nx, nz, base=0)
    ov, ot = _sheet(lambda ix: 2.5, nx, nz, base=nx * nz)
    v = np.array(iv + ov, float)
    tris = np.array(it + ot, np.int64)
    Vb, Nb, tree = _flat_body()
    out, moved = nc._relax_shape_self_intersection(v, tris, np.zeros(len(v), bool),
                                                   Vb, Nb, tree, target=0)
    assert moved == 0 and np.array_equal(out, v)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
