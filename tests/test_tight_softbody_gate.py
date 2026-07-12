"""Tight-vs-loose gate for GENERATED soft-body physics
(nif_convert._carrier_is_body_conforming).

A skin-tight cloth carrier (leggings/pantyhose) must be recognised as
body-conforming so the generator leaves it skinned+morphable instead of
turning it into a per-vertex soft-body (which FSMP would drive over the top of
RaceMenu BodyMorph, killing slider-following). Loose hanging cloth must NOT be
flagged, so it still gets its physics. Calibrated cutoff: >=95% of verts within
4u AND 95th-percentile <=5u. See #tight-softbody-gate.
"""
import numpy as np
import pytest
from scipy.spatial import cKDTree

from src import nif_convert as nc


class _Shape:
    def __init__(self, verts):
        self.verts = [tuple(map(float, v)) for v in verts]


def _flat_body(n=40):
    """A body plane at Y=0 (outward +Y), dense enough for nearest-point queries."""
    xs = np.linspace(-10, 10, n)
    zs = np.linspace(0, 100, n)
    X, Z = np.meshgrid(xs, zs)
    return np.stack([X.ravel(), np.zeros(X.size), Z.ravel()], 1).astype(float)


@pytest.fixture
def body_tree(monkeypatch):
    bv = _flat_body()
    monkeypatch.setattr(nc, "_ube_conform_body_tree",
                        lambda suf: (bv, cKDTree(bv)))
    return bv


def _grid(y_of, nx=30, nz=30):
    xs = np.linspace(-9, 9, nx)
    zs = np.linspace(5, 95, nz)
    X, Z = np.meshgrid(xs, zs)
    Y = np.vectorize(y_of)(Z)
    return np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)


def test_tight_shape_is_conforming(body_tree):
    # hugs the body: every vert ~1u off the plane
    tight = _Shape(_grid(lambda z: 1.0))
    assert nc._carrier_is_body_conforming(tight, "_1") is True


def test_loose_skirt_not_conforming(body_tree):
    # waistband hugs, hem swings out to ~15u -> not conforming
    loose = _Shape(_grid(lambda z: 0.5 + max(0.0, (60 - z)) * 0.3))
    assert nc._carrier_is_body_conforming(loose, "_1") is False


def test_outlier_verts_block_conforming(body_tree):
    # 96% tight but a few verts hang at 12u -> p95 cap rejects it
    v = _grid(lambda z: 1.0)
    v[:max(1, len(v) // 15), 1] = 12.0
    assert nc._carrier_is_body_conforming(_Shape(v), "_1") is False


def test_fails_open_without_body_ref(monkeypatch):
    # no body reference -> never claim conforming (keep soft-body, no regression)
    monkeypatch.setattr(nc, "_ube_conform_body_tree", lambda suf: None)
    assert nc._carrier_is_body_conforming(_Shape(_grid(lambda z: 1.0)), "_1") is False


def test_tiny_shape_ignored(body_tree):
    assert nc._carrier_is_body_conforming(_Shape([(0, 1, 0)] * 10), "_1") is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
