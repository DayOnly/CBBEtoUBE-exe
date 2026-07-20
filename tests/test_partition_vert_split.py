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

"""Guards for VERTEX-based skin-partition splitting.

This is CTD-class code with in-game history and, until now, no test at any
layer -- neither here nor in the postflight detector, which only checks the
BONE cap.

Both directions have burned a real conversion:

  * too HIGH / no split: a ~31.8k-vert torso in ONE partition made the runtime
    body-morph rebuild read past the vertex buffer -> equip CTD.
  * too LOW: the cap was once 16000, roughly HALF the true 16-bit index limit,
    so ordinary body-sized armor got split unnecessarily. The resulting
    partition was corrupt enough that the HDT-SMP re-skin read out of bounds ->
    equip CTD plus a "garbage explosion" mesh.

So the constant is pinned from BOTH sides here, and the splitter's structural
invariants (no geometry dropped, every partition under the cap, contiguous
indices) are checked on a real oversize mesh rather than asserted about.
"""
import numpy as np

from src import nif_convert


class _FakePart:
    def __init__(self, pid=32):
        self.id = pid
        self.flags = 257
        self.namedict = {}          # presence gates the split path


class _FakeShape:
    """Grid mesh with a settable partition list, mirroring the pynifly surface
    the splitter touches."""

    def __init__(self, verts, tris):
        self.verts = verts
        self.tris = tris
        self.partitions = [_FakePart()]
        self.set_objs = None
        self.set_assign = None

    def set_partitions(self, objs, assign):
        self.set_objs = objs
        self.set_assign = assign


class _FakeSkyPartition:
    def __init__(self, part_id=32, flags=257, namedict=None):
        self.id = part_id
        self.flags = flags
        self.namedict = namedict


class _FakePyn:
    SkyPartition = _FakeSkyPartition


def _grid(n):
    """(verts, tris) for an n x n grid: n*n verts, spread over Z so the
    splitter's centroid-Z ordering has something real to work with."""
    xs, zs = np.meshgrid(np.arange(n, dtype=float), np.arange(n, dtype=float))
    verts = np.stack([xs.ravel(), np.zeros(n * n), zs.ravel()], axis=1)
    tris = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b = r * n + c, r * n + c + 1
            d, e = (r + 1) * n + c, (r + 1) * n + c + 1
            tris += [[a, b, d], [b, e, d]]
    return verts, np.asarray(tris, dtype=np.int64)


def _split(monkeypatch, n_verts_side, cap):
    monkeypatch.setattr(nif_convert, "_pynifly", lambda: _FakePyn)
    v, t = _grid(n_verts_side)
    s = _FakeShape(v, t)
    return s, nif_convert._split_oversize_partition_verts(s, cap=cap), v, t


def test_under_cap_is_a_noop(monkeypatch):
    s, n, v, _ = _split(monkeypatch, 20, cap=len(_grid(20)[0]) + 1)
    assert n == 0
    assert s.set_objs is None, "must not touch partitions when under the cap"


def test_oversize_splits_and_keeps_every_partition_under_cap(monkeypatch):
    cap = 120
    s, n, v, t = _split(monkeypatch, 30, cap=cap)   # 900 verts vs cap 120
    assert n > 1, "an oversize shape must actually split"
    assert len(s.set_objs) == n

    assign = np.asarray(s.set_assign)
    assert len(assign) == len(t), "every triangle must get an assignment"

    # THE invariant: no partition may reference more than `cap` distinct verts.
    for p in range(n):
        used = np.unique(t[assign == p])
        assert len(used) <= cap, (
            f"partition {p} references {len(used)} verts, cap is {cap}")


def test_no_geometry_is_dropped(monkeypatch):
    """Every triangle lands in exactly one partition, and the union of the
    partitions covers every vertex the mesh used."""
    s, n, v, t = _split(monkeypatch, 30, cap=120)
    assign = np.asarray(s.set_assign)
    covered = set()
    for p in range(n):
        covered |= {int(x) for x in np.unique(t[assign == p])}
    assert covered == {int(x) for x in np.unique(t)}


def test_partition_indices_are_contiguous_and_none_empty(monkeypatch):
    """A gap or an empty partition is a malformed skin instance."""
    s, n, v, t = _split(monkeypatch, 30, cap=120)
    assign = np.asarray(s.set_assign)
    present = sorted(set(int(x) for x in assign))
    assert present == list(range(n)), f"non-contiguous partitions: {present}"
    for p in range(n):
        assert (assign == p).sum() > 0, f"partition {p} is empty"


def test_vert_cap_stays_within_the_16bit_index_limit():
    """Upper bound. A partition indexes its verts with a 16-bit value, so the
    cap must stay under 65535 -- and comfortably under, since the one measured
    CTD was a ~31.8k-vert single partition."""
    cap = nif_convert.SKIN_PARTITION_VERT_CAP
    assert cap < 65535, "cap must stay inside the 16-bit vertex index range"
    assert cap <= 31800, (
        "cap must stay below the ~31.8k single-partition size that was measured "
        "causing an equip CTD in the runtime morph rebuild")


def test_vert_cap_is_high_enough_not_to_split_body_sized_armor():
    """Lower bound, and the reason this test exists. The cap was once 16000 --
    about half the real limit -- which split ordinary body-sized armor and
    produced a corrupt partition that crashed HDT-SMP on equip. The injected UBE
    body is 29298 verts in a SINGLE partition and has always been stable, so any
    cap at or below that would split shapes that are known-good unsplit."""
    assert nif_convert.SKIN_PARTITION_VERT_CAP > 29298, (
        "cap must exceed the 29298-vert UBE body, which ships as one partition "
        "and is the proof that body-sized shapes need no split")


# ---- untrusted physics-XML path resolution -------------------------------
# The HDT physics-XML path is read from a third-party NIF's string extra-data,
# so a hostile or broken mesh controls it. The resolved file is copied into the
# converted output mod, which users re-upload -- so an escape is a disclosure
# route, not merely a bad read.

def test_safe_data_rel_accepts_ordinary_relative_paths():
    f = nif_convert._safe_data_rel
    assert f(r"meshes\armor\x\hdt.xml") == "meshes/armor/x/hdt.xml"
    assert f("/meshes/a/b.xml") == "meshes/a/b.xml"      # leading slash stripped
    assert f("meshes/./a/b.xml") == "meshes/a/b.xml"     # '.' segments dropped


def test_safe_data_rel_rejects_escapes():
    """lstrip('/') alone does NOT stop these: pathlib discards the left operand
    entirely for a drive-absolute right side, and '..' walks out of the tree."""
    f = nif_convert._safe_data_rel
    bs = chr(92)
    assert f(r"C:\Users\someone\.ssh\id_rsa") is None        # drive-absolute
    assert f(r"..\..\..\secret.txt") is None                 # parent escape
    assert f("meshes/a/../../../secret") is None             # embedded ..
    assert f(bs * 2 + r"server\share\x.xml") is None         # UNC
    assert f(bs * 2 + "?" + bs + r"C:\x") is None            # extended-length
    assert f("") is None and f(None) is None
