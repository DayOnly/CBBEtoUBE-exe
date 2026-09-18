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

"""The end-of-run passes release every NIF pair they open. #postflight-release

MEASURED 2026-09-17 in the batch parent on a 76-NIF mod: the jiggle-sync pass
grew the process by 32 MB per weight pair and the divergence pass by 23 MB,
1.44 -> 3.57 GB, nothing freed until a FULL garbage collection. Every loaded
pynifly file is a reference cycle (block.file <-> file._shapes / _nodes), and
a full collection is rare in a process holding a million objects.
`nif_io.release_nif` cuts the cycles so reference counting frees the pair the
moment it is dropped, and both passes call it per pair.

These tests run with the cyclic collector DISABLED, so only reference
counting can free anything: a leak is a live object, not a number that might
drift. Each counts live library objects against a baseline taken before the
load, because other tests leave files of their own alive.
"""
import gc
import sys
import weakref
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, nif_convert as nc, nif_io   # noqa: E402
from tests.synthetic_nif import (                              # noqa: E402
    build_skinned_shapes_nif, pynifly_available, uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="pynifly not importable")


def _pair(root: Path, stem: str = "piece"):
    """A `_0`/`_1` pair the passes will group: the same shape at two sizes."""
    d = root / "meshes" / "armor" / "test"
    v1, t1, n1 = uv_sphere(10.0)
    v0, t0, n0 = uv_sphere(9.5)
    p0 = build_skinned_shapes_nif(d / f"{stem}_0.nif", [("Body", v0, t0, n0)])
    p1 = build_skinned_shapes_nif(d / f"{stem}_1.nif", [("Body", v1, t1, n1)])
    return p0, p1


def _touch(nf):
    """What the passes read from a file."""
    shapes = list(nf.shapes)
    for s in shapes:
        _ = s.bone_weights, s.bone_names, s.verts, s.name
    return shapes


def _live(pyn, cls_name: str) -> int:
    cls = getattr(pyn, cls_name)
    return sum(1 for o in gc.get_objects() if isinstance(o, cls))


@pytest.fixture
def no_cyclic_gc():
    """Only reference counting may free anything inside the test."""
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
        gc.collect()


def test_a_released_file_dies_under_reference_counting_alone(tmp_path, no_cyclic_gc):
    """Two halves. The file must die the moment its last holder drops it even
    while a caller still holds its SHAPES (the passes keep `list(nf.shapes)`
    in a local past the release), which only cutting the block-to-file links
    achieves; and once the shapes go too, every block must be gone."""
    pyn = nc._pynifly()
    p0, _p1 = _pair(tmp_path)
    files0, blocks0 = _live(pyn, "NifFile"), _live(pyn, "NiObject")
    nf = pyn.NifFile(filepath=str(p0))
    shapes = _touch(nf)
    dead = weakref.ref(nf)
    nif_io.release_nif(nf)
    del nf
    assert dead() is None, (
        "the file is still alive while its shapes are: a shape still points at it")
    assert _live(pyn, "NifFile") == files0
    del shapes
    assert _live(pyn, "NiObject") == blocks0, (
        "a block of the released file is still alive")


def test_without_the_release_the_file_waits_for_a_full_collection(tmp_path, no_cyclic_gc):
    """Control: the same load, dropped WITHOUT the release, stays alive until
    the cyclic collector runs -- the mechanism the release exists for. If this
    stops leaking, the library changed and the release must be re-measured."""
    pyn = nc._pynifly()
    p0, _p1 = _pair(tmp_path)
    nf = pyn.NifFile(filepath=str(p0))
    shapes = _touch(nf)
    dead = weakref.ref(nf)
    del nf, shapes
    assert dead() is not None
    gc.collect()
    assert dead() is None


def test_the_divergence_pass_leaves_no_file_open(tmp_path, no_cyclic_gc, monkeypatch):
    pyn = nc._pynifly()
    _pair(tmp_path, "one")
    _pair(tmp_path, "two")
    files0 = _live(pyn, "NifFile")
    ac._postflight_weight_partner_divergence(tmp_path)
    assert _live(pyn, "NifFile") == files0, "the divergence pass left a file open"
    # control: with the release made a no-op, the pass leaks both files of each pair
    monkeypatch.setattr(nif_io, "release_nif", lambda nf: None)
    ac._postflight_weight_partner_divergence(tmp_path)
    assert _live(pyn, "NifFile") == files0 + 4


def test_the_jiggle_sync_pass_leaves_no_file_open(tmp_path, no_cyclic_gc, monkeypatch):
    pyn = nc._pynifly()
    _pair(tmp_path, "one")
    _pair(tmp_path, "two")
    files0 = _live(pyn, "NifFile")
    ac._postflight_sync_weight_partner_jiggle(tmp_path)
    assert _live(pyn, "NifFile") == files0, "the jiggle-sync pass left a file open"
    monkeypatch.setattr(nif_io, "release_nif", lambda nf: None)
    ac._postflight_sync_weight_partner_jiggle(tmp_path)
    assert _live(pyn, "NifFile") == files0 + 4
