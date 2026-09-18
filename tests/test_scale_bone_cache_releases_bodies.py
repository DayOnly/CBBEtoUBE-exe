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

"""#scale-bone-cache-by-file -- the scale-bone KD-tree cache must not pin bodies.

Measured by the hardening plan's verifier: in one worker, post-gc commit charge
stepped up on every body-swap conversion, and clearing `_SCALE_BONE_DATA_CACHE`
alone freed 229 MB and returned the live NifFile count to its baseline. Phase 2
re-opens the UBE body NIF for every conversion, and the cache was keyed on
id(body_shape) with a strong reference to the shape (#idreuse-cache) -- so
every conversion missed, rebuilt the KD-trees, and pinned a whole body NIF for
the life of the worker.

Keyed by the body FILE, an entry holds no shape: a body loaded again from the
same unchanged file shares the trees, and the old one can be freed. A shape
that cannot name a file on disk keeps the id key and its strong reference, the
protection against a recycled id()."""
import gc
import os
import weakref
from types import SimpleNamespace

import pytest

from src import nif_convert as nc
from tests.synthetic_nif import build_skinned_shape_nif, pynifly_available


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(nc, "_SCALE_BONE_DATA_CACHE", {})


class _Body:
    """What `_cached_scale_bone_data` reads, plus -- when `path` is given -- the
    `.file.filepath` a shape pynifly read from a file carries."""

    def __init__(self, path=None, name="BaseShape", z_breast=90.0):
        if path is not None:
            self.file = SimpleNamespace(filepath=str(path))
        self.name = name
        self.verts = [(0.0, 0.0, z_breast), (0.0, 0.0, 50.0)]
        self.bone_names = ["NPC L Breast01", "NPC L RearThigh"]
        self.bone_weights = {"NPC L Breast01": [(0, 0.16)],
                             "NPC L RearThigh": [(1, 0.2)]}


@pytest.fixture
def body_file(tmp_path):
    p = tmp_path / "femalebody_1.nif"
    p.write_bytes(b"stands in for the body NIF on disk")
    return p


def test_a_body_loaded_twice_from_one_file_shares_one_entry(body_file):
    first, second = _Body(body_file), _Body(body_file)
    bones_a, data_a = nc._cached_scale_bone_data(first, False)
    bones_b, data_b = nc._cached_scale_bone_data(second, False)
    assert bones_a == bones_b == ["NPC L Breast01", "NPC L RearThigh"]
    assert len(nc._SCALE_BONE_DATA_CACHE) == 1
    assert data_b["NPC L Breast01"][1] is data_a["NPC L Breast01"][1], (
        "the second load of the same body rebuilt the KD-tree")


def test_the_cache_does_not_keep_a_body_alive(body_file):
    """THE LEAK: the cache value held the shape, so a body re-opened for every
    conversion was never freed."""
    body = _Body(body_file)
    nc._cached_scale_bone_data(body, False)
    alive = weakref.ref(body)
    del body
    gc.collect()
    assert alive() is None, "the scale-bone cache is keeping a body shape alive"


def test_a_changed_body_file_replaces_its_entry(body_file):
    nc._cached_scale_bone_data(_Body(body_file), False)
    body_file.write_bytes(b"a different body now -- longer, so its size changes")
    st = body_file.stat()
    os.utime(body_file, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    _, data = nc._cached_scale_bone_data(_Body(body_file, z_breast=95.0), False)
    assert data["NPC L Breast01"][0][0][2] == 95.0, "the old body's data was served"
    assert len(nc._SCALE_BONE_DATA_CACHE) == 1, "the entry for the old file was kept"


def test_options_keep_separate_entries(body_file):
    body = _Body(body_file)
    nc._cached_scale_bone_data(body, False)
    nc._cached_scale_bone_data(body, True)
    nc._cached_scale_bone_data(body, False, ("rearthigh",))
    assert len(nc._SCALE_BONE_DATA_CACHE) == 3


def test_a_body_with_no_file_keeps_the_id_reuse_protection():
    """No file to key on: the id key AND a strong reference stay, so a recycled
    id() can never be served another body's trees (#idreuse-cache)."""
    first, second = _Body(), _Body(z_breast=95.0)
    nc._cached_scale_bone_data(first, False)
    _, data = nc._cached_scale_bone_data(second, False)
    assert len(nc._SCALE_BONE_DATA_CACHE) == 2
    assert data["NPC L Breast01"][0][0][2] == 95.0
    alive = weakref.ref(first)
    del first
    gc.collect()
    assert alive() is not None, "a body with no file must stay referenced"


def test_a_body_whose_file_is_not_on_disk_uses_the_id_key(tmp_path):
    """A NIF built in memory names a target path that does not exist yet."""
    ghost = _Body(tmp_path / "never_saved.nif")
    nc._cached_scale_bone_data(ghost, False)
    (key,) = nc._SCALE_BONE_DATA_CACHE
    assert key[0] == id(ghost)


def test_two_real_loads_of_one_body_nif_share_one_entry(tmp_path):
    """The assumption the key rests on: a shape pynifly reads from a file names
    that file through `shape.file.filepath`."""
    if not pynifly_available():
        pytest.skip("pynifly native lib not available")
    path = build_skinned_shape_nif(tmp_path / "body.nif", name="BaseShape",
                                   bones=("NPC L Breast01", "NPC L RearThigh"))
    pyn = nc._pynifly()
    shapes = [next(s for s in pyn.NifFile(filepath=str(path)).shapes
                   if s.name == "BaseShape") for _ in range(2)]
    assert shapes[0] is not shapes[1]
    bones_a, data_a = nc._cached_scale_bone_data(shapes[0], False)
    bones_b, data_b = nc._cached_scale_bone_data(shapes[1], False)
    assert bones_a and bones_a == bones_b, (
        "the synthetic body has no scale bones -- this test would prove nothing")
    assert len(nc._SCALE_BONE_DATA_CACHE) == 1
    assert data_b[bones_a[0]][1] is data_a[bones_a[0]][1]
    assert all(value[0] is None for value in nc._SCALE_BONE_DATA_CACHE.values())
