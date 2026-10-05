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

"""#coherence-repair-rebuild-hold -- the rebuild's coherence repair keeps the hold.

A shape given new verts by a rebuild (the conform's self-intersection relax, the
collider shrinkwrap) runs the coherence repair again inside `_copy_shape`. On a
body-swap piece that is the third run, and it had no body, so the clearance hold
that keeps a repaired vert out of the skin did not apply (#44 left this open: the
write-time run was given the body, this one was not). The rebuild now hands the
piece's own UBE body to every shape it gives new verts.
"""
import numpy as np
import pytest

from src import nif_convert as nc
from src import nif_convert_writer as w

from tests.synthetic_nif import (build_skinned_shapes_nif, pynifly_available,
                                 uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="needs pynifly")

BODY = "BaseShape"                 # what ube_body_shape looks for first


def _piece(tmp_path, with_body=True):
    bv, bt, bn = uv_sphere(10.0)
    gv, gt, gn = uv_sphere(10.5)
    shapes = [("Cloth", gv, gt, gn)]
    if with_body:
        shapes.insert(0, (BODY, bv, bt, bn))
    return build_skinned_shapes_nif(tmp_path / "piece_1.nif", shapes)


def _spy(monkeypatch):
    seen = {}
    real = w._copy_shape

    def spy(src_shape, dst_nif, *a, repair_body=None, **k):
        seen[src_shape.name] = repair_body
        return real(src_shape, dst_nif, *a, repair_body=repair_body, **k)

    monkeypatch.setattr(w, "_copy_shape", spy)
    return seen


def _cloth_verts(p):
    nf = nc._pynifly().NifFile(filepath=str(p))
    return np.asarray(next(s for s in nf.shapes if s.name == "Cloth").verts)


@pytest.fixture(autouse=True)
def _armed(monkeypatch):
    monkeypatch.setattr(nc, "COHERENCE_REPAIR_OUTSIDE_BODY", True)


def test_a_shape_given_new_verts_is_handed_the_body(tmp_path, monkeypatch):
    p = _piece(tmp_path)
    if nc.ube_body_shape(nc._pynifly().NifFile(filepath=str(p))) is None:
        pytest.skip("the test body is not recognised as the UBE body here")
    seen = _spy(monkeypatch)
    new = _cloth_verts(p) * 1.001
    assert nc._reauthor_nif_fresh(p, override_verts_by_name={"Cloth": new})
    rb = seen["Cloth"]
    assert rb is not None
    bv, bn = rb
    want = np.asarray(uv_sphere(10.0)[0])
    assert np.allclose(bv, want, atol=1e-4), "identity frame: the body as stored"
    assert bn.shape == bv.shape
    assert seen[BODY] is None, "a shape without new verts gets no body"


def test_a_piece_without_a_body_hands_none(tmp_path, monkeypatch):
    p = _piece(tmp_path, with_body=False)
    seen = _spy(monkeypatch)
    assert nc._reauthor_nif_fresh(p, override_verts_by_name={"Cloth": _cloth_verts(p)})
    assert seen["Cloth"] is None


def test_with_the_hold_off_no_body_is_read(tmp_path, monkeypatch):
    p = _piece(tmp_path)
    monkeypatch.setattr(nc, "COHERENCE_REPAIR_OUTSIDE_BODY", False)
    seen = _spy(monkeypatch)
    assert nc._reauthor_nif_fresh(p, override_verts_by_name={"Cloth": _cloth_verts(p)})
    assert seen["Cloth"] is None


def test_a_rebuild_without_new_verts_reads_no_body(tmp_path, monkeypatch):
    p = _piece(tmp_path)
    called = []
    monkeypatch.setattr(w, "_rebuild_repair_body", lambda nif: called.append(1))
    assert nc._reauthor_nif_fresh(p)
    assert not called


class _Shape:
    def __init__(self, off):
        self.off = np.asarray(off, dtype=np.float64)


def test_the_body_is_moved_into_the_shapes_frame(monkeypatch):
    """The shape's verts are its body-space verts minus its checked offset, so the
    body in its frame is the body minus that same offset."""
    monkeypatch.setattr(nc, "shape_body_offset", lambda s, body_verts=None: s.off)
    bv = np.array([[0.0, 0.0, 100.0], [1.0, 0.0, 100.0]])
    bn = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]])
    got = w._repair_body_in_shape_frame((bv, bn), _Shape([0.0, 0.0, 112.0]))
    assert np.allclose(got[0], bv - [0.0, 0.0, 112.0])
    assert got[1] is bn
