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

"""#osd-columnar -- OSD and TRI morphs are two arrays per morph, and every
consumer that used to read a tuple per offset gives the same bits.

The oracles are the code this replaced, copied here verbatim: the struct loops
that parsed and wrote both formats, and the scalar loops behind the amplitude
map and the morph stack. The columnar code must match them field for field
and bit for bit, because the TRI writer quantises from these values and a TRI
byte is load-bearing (NioOverride reads it at runtime). The footprint test is
the reason the change exists: a parse must cost what the file costs, not a
tuple per offset.
"""
import struct
import sys
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nif_convert_trigen as tg                     # noqa: E402
from src.osd import OSD_MAGIC, OsdFile, OsdMorph             # noqa: E402
from src.sliderset_gen import generate_armor_tri             # noqa: E402
from src.tri import TRI_MAGIC, TriFile, TriMorph, TriShape   # noqa: E402


# --- the oracles: the tuple code as it was --------------------------------------

def _osd_parse_oracle(data):
    assert data[:4] == OSD_MAGIC
    version = struct.unpack_from("<I", data, 4)[0]
    morph_count = min(struct.unpack_from("<I", data, 8)[0], len(data) // 3, 100_000)
    p = 12
    morphs = []
    for _ in range(morph_count):
        if p + 1 > len(data):
            break
        name_len = data[p]; p += 1
        name = data[p:p + name_len].decode("utf-8", errors="replace")
        p += name_len
        if p + 2 > len(data):
            break
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        if p + n * 14 > len(data):
            break
        offs = []
        for _ in range(n):
            vi = struct.unpack_from("<H", data, p)[0]
            dx, dy, dz = struct.unpack_from("<3f", data, p + 2)
            offs.append((vi, dx, dy, dz))
            p += 14
        morphs.append((name, offs))
    return version, morphs


def _osd_save_oracle(version, morphs):
    out = bytearray(OSD_MAGIC) + struct.pack("<II", version, len(morphs))
    for name, offs in morphs:
        nb = name.encode("utf-8")
        out += bytes([len(nb)]) + nb + struct.pack("<H", len(offs))
        for i, x, y, z in offs:
            out += struct.pack("<H3f", i, x, y, z)
    return bytes(out)


def _tri_parse_oracle(data):
    assert data[:4] == TRI_MAGIC
    p = 6
    shapes = []
    while p < len(data) - 3:
        sname_len = data[p]
        if sname_len == 0:
            break
        try:
            sname = data[p + 1:p + 1 + sname_len].decode("ascii")
        except UnicodeDecodeError:
            break
        if not all(c.isprintable() for c in sname):
            break
        p += 1 + sname_len
        if p + 2 > len(data):
            break
        num_morphs = struct.unpack_from("<H", data, p)[0]; p += 2
        morphs = []
        for _ in range(num_morphs):
            if p + 7 > len(data):
                break
            mname_len = data[p]; p += 1
            mname = data[p:p + mname_len].decode("ascii", errors="replace")
            p += mname_len
            if p + 6 > len(data):
                break
            mult = struct.unpack_from("<f", data, p)[0]; p += 4
            noff = struct.unpack_from("<H", data, p)[0]; p += 2
            if p + noff * 8 > len(data):
                break
            offs = []
            for _ in range(noff):
                idx, dx_q, dy_q, dz_q = struct.unpack_from("<H3h", data, p)
                p += 8
                offs.append((int(idx), float(dx_q) * mult, float(dy_q) * mult,
                             float(dz_q) * mult))
            morphs.append((mname, offs))
        shapes.append((sname, morphs))
    return shapes


def _tri_save_oracle(shapes):
    out = bytearray(TRI_MAGIC) + struct.pack("<H", len(shapes))
    for sname, morphs in shapes:
        sn = sname.encode("ascii")
        out += bytes([len(sn)]) + sn + struct.pack("<H", len(morphs))
        for mname, offs in morphs:
            mn = mname.encode("ascii")
            out += bytes([len(mn)]) + mn
            if not offs:
                out += struct.pack("<f", 1.0) + struct.pack("<H", 0)
                continue
            arr = np.array([[dx, dy, dz] for _, dx, dy, dz in offs], dtype=np.float64)
            max_abs = float(np.abs(arr).max())
            mult = 1.0 if max_abs <= 0.0 else max_abs / 32767.0
            out += struct.pack("<f", mult) + struct.pack("<H", len(offs))
            inv = 1.0 / mult if mult > 0 else 0.0
            arr = np.asarray(offs, dtype=np.float64)
            idxs = arr[:, 0].astype(np.int64)
            q = np.round(arr[:, 1:4] * inv)
            np.clip(q, -32768.0, 32767.0, out=q)
            buf = np.empty((len(offs), 4), dtype=np.uint16)
            buf[:, 0] = idxs.astype(np.uint16)
            buf[:, 1:4] = q.astype(np.int16).view(np.uint16)
            out += buf.tobytes()
    return bytes(out)


def _amplitude_oracle(osd, bn, n_verts):
    amp = np.zeros(n_verts, dtype=np.float64)
    for m in osd.morphs:
        nm = m.name.lower()
        if not any(k in nm for k in tg._MORPH_SIZE_KEYWORDS):
            continue
        for idx, dx, dy, dz in m.offsets:
            if idx >= n_verts:
                continue
            outward = dx * bn[idx, 0] + dy * bn[idx, 1] + dz * bn[idx, 2]
            if outward > amp[idx]:
                amp[idx] = outward
    return amp


def _stack_oracle(osd, n_verts):
    keep = []
    for m in osd.morphs:
        if not m.offsets:
            continue
        arr = np.asarray(m.offsets, dtype=np.float64)
        idx = arr[:, 0].astype(np.int64)
        d = arr[:, 1:4]
        ok = (idx >= 0) & (idx < n_verts)
        if not ok.any():
            continue
        if np.linalg.norm(d[ok], axis=1).max() < tg._MORPH_STACK_MIN:
            continue
        buf = np.zeros((n_verts, 3), dtype=np.float32)
        buf[idx[ok]] = d[ok]
        keep.append(buf)
    return np.stack(keep) if keep else None


# --- synthetic data ------------------------------------------------------------

def _morph_rows(rng, n, idx_max, scale=3.0):
    """n (vert_idx, dx, dy, dz) rows with float32-exact deltas, duplicates
    and out-of-range indices included on purpose."""
    idx = rng.integers(0, idx_max, size=n)
    d = (rng.standard_normal((n, 3)) * scale).astype(np.float32).astype(np.float64)
    return [(int(i), float(x), float(y), float(z)) for i, (x, y, z) in zip(idx, d)]


def _osd_rows(rng, n_verts):
    return [
        ("BaseShapeBreastsBig", _morph_rows(rng, 4000, n_verts + 200)),
        ("BaseShapeButtCrack", _morph_rows(rng, 1500, n_verts + 200)),
        ("BaseShapeEmpty", []),
        ("BaseShapeNothingSizedé", _morph_rows(rng, 700, n_verts)),
        ("BaseShapeTinyButt", [(i, 0.01, -0.02, 0.005) for i in range(50)]),
        ("BaseShapeAllOutside", [(n_verts + 1 + k, 1.0, 1.0, 1.0) for k in range(30)]),
        ("BaseShapeBellyBig", _morph_rows(rng, 2500, n_verts)),
    ]


def _osd_from_rows(rows):
    return OsdFile(version=1, morphs=[OsdMorph.from_offsets(n, o) for n, o in rows])


# --- the OSD ---------------------------------------------------------------------

def test_the_parse_matches_the_struct_oracle_field_for_field():
    rng = np.random.default_rng(7)
    rows = _osd_rows(rng, 3000)
    data = _osd_save_oracle(1, rows)
    for blob in (data, data[:-5]):        # whole, and cut inside the last table
        ours = OsdFile.parse(blob)
        version, theirs = _osd_parse_oracle(blob)
        assert ours.version == version
        assert [m.name for m in ours.morphs] == [n for n, _ in theirs]
        for m, (_, offs) in zip(ours.morphs, theirs):
            assert len(m) == len(offs)
            assert m.idx.dtype == np.uint16 and m.delta.dtype == np.float32
            assert m.idx.tolist() == [o[0] for o in offs]
            want = np.array([o[1:] for o in offs], dtype=np.float64).reshape(-1, 3)
            assert np.array_equal(m.delta.astype(np.float64), want), m.name
            assert np.array_equal(m.delta.view(np.uint32),
                                  want.astype(np.float32).view(np.uint32)), m.name
    assert len(OsdFile.parse(data[:-5]).morphs) == len(rows) - 1, "the cut morph is dropped"


def test_the_save_matches_the_struct_oracle_byte_for_byte(tmp_path):
    rng = np.random.default_rng(8)
    rows = _osd_rows(rng, 3000)
    # Values a float32 cannot hold exactly: the writer used to round them at
    # pack time; from_offsets rounds them once, the same way.
    rows.append(("BaseShapeInexact", [(3, 0.1, 1.0 / 3.0, -2.7e-8), (65535, 1e30, -1e-30, 0.0)]))
    osd = _osd_from_rows(rows)
    osd.save(tmp_path / "a.osd")
    assert (tmp_path / "a.osd").read_bytes() == _osd_save_oracle(1, rows)
    # And a morph handed the arrays directly writes the same bytes as one built
    # from tuples.
    m = osd.morphs[0]
    twin = OsdMorph(m.name, m.idx.copy(), m.delta.copy())
    OsdFile(version=1, morphs=[twin]).save(tmp_path / "b.osd")
    OsdFile(version=1, morphs=[m]).save(tmp_path / "c.osd")
    assert (tmp_path / "b.osd").read_bytes() == (tmp_path / "c.osd").read_bytes()


def test_a_parse_costs_the_file_not_a_tuple_per_offset():
    """The reason for #osd-columnar. A tuple per offset held 15.5x the file
    (MEASURED on the body OSD); the arrays hold the file's own bytes."""
    rng = np.random.default_rng(9)
    rows = [(f"BaseShapeM{k}", _morph_rows(rng, 10_000, 30_000)) for k in range(20)]
    data = _osd_save_oracle(1, rows)
    assert len(data) > 2_500_000, len(data)
    tracemalloc.start()
    try:
        tracemalloc.clear_traces()
        before = tracemalloc.get_traced_memory()[0]
        osd = OsdFile.parse(data)
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert sum(len(m) for m in osd.morphs) == 200_000
    assert held < 2 * len(data), (
        f"the parse holds {held / 2**20:.1f} MB for a {len(data) / 2**20:.1f} MB file")


def test_the_tuples_are_made_on_demand_and_never_kept():
    m = OsdMorph.from_offsets("BaseShapeX", [(5, 1.0, 2.0, 3.0), (7, -1.0, 0.5, 0.0)])
    a, b = m.offsets, m.offsets
    assert a == [(5, 1.0, 2.0, 3.0), (7, -1.0, 0.5, 0.0)] and a is not b
    assert set(vars(m)) == {"name", "idx", "delta"}, "a cached list would bring the 166 MB back"
    assert m.offsets_dict() == {5: (1.0, 2.0, 3.0), 7: (-1.0, 0.5, 0.0)}
    assert OsdMorph.from_offsets("BaseShapeNone", []).offsets == []
    t = TriMorph.from_offsets("X", [(5, 1.0, 2.0, 3.0)])
    assert t.offsets == [(5, 1.0, 2.0, 3.0)] and t.offsets is not t.offsets
    assert set(vars(t)) == {"name", "idx", "delta"}


def test_an_index_beyond_uint16_is_refused_at_construction():
    with pytest.raises(ValueError, match="uint16"):
        OsdMorph.from_offsets("BaseShapeX", [(65536, 0.0, 0.0, 0.0)])
    with pytest.raises(ValueError, match="uint16"):
        OsdMorph("BaseShapeX", np.array([-1]), np.zeros((1, 3)))
    with pytest.raises(ValueError, match="indices"):
        OsdMorph("BaseShapeX", np.array([1, 2]), np.zeros((1, 3)))
    assert OsdMorph.from_offsets("BaseShapeX", [(65535, 0.0, 0.0, 0.0)]).idx.tolist() == [65535]


def test_two_morphs_compare_by_value():
    a = OsdMorph.from_offsets("BaseShapeX", [(1, 1.0, 2.0, 3.0)])
    b = OsdMorph.from_offsets("BaseShapeX", [(1, 1.0, 2.0, 3.0)])
    c = OsdMorph.from_offsets("BaseShapeX", [(1, 1.0, 2.0, 3.5)])
    assert a == b and a != c and a != "BaseShapeX"


# --- the two numeric consumers --------------------------------------------------

def test_the_amplitude_map_matches_the_scalar_loop_bit_for_bit(monkeypatch):
    rng = np.random.default_rng(10)
    n_verts = 3000
    osd = _osd_from_rows(_osd_rows(rng, n_verts))
    bn = rng.standard_normal((n_verts, 3))
    bn /= np.linalg.norm(bn, axis=1, keepdims=True)
    monkeypatch.setattr(tg, "_cached_osd_load", lambda _p: osd)
    tg._BODY_MORPH_AMP_CACHE.clear()
    try:
        amp = tg._cached_body_morph_amplitude(Path("fake.osd"), bn, n_verts)
    finally:
        tg._BODY_MORPH_AMP_CACHE.clear()
    want = _amplitude_oracle(osd, bn, n_verts)
    assert amp is not None and amp.shape == want.shape
    assert (amp > 0).sum() > 1000, "the fixture must exercise thousands of verts"
    assert np.array_equal(amp.view(np.uint64), want.view(np.uint64)), (
        f"max |diff| {np.abs(amp - want).max():.3g}: the multiply/add order changed")


def test_the_morph_stack_matches_the_scalar_loop_bit_for_bit(monkeypatch):
    rng = np.random.default_rng(11)
    n_verts = 3000
    osd = _osd_from_rows(_osd_rows(rng, n_verts))
    monkeypatch.setattr(tg, "_cached_osd_load", lambda _p: osd)
    tg._BODY_MORPH_STACK_CACHE.clear()
    try:
        stack = tg._cached_body_morph_stack(Path("fake.osd"), n_verts)
    finally:
        tg._BODY_MORPH_STACK_CACHE.clear()
    want = _stack_oracle(osd, n_verts)
    assert stack is not None and stack.shape == want.shape
    assert stack.shape[0] == 4, "the tiny and the all-outside morphs must be dropped"
    assert np.array_equal(stack.view(np.uint32), want.view(np.uint32))


# --- the TRI ---------------------------------------------------------------------

def _tri_rows(rng):
    return [
        ("Body_1", [("BreastsBig", _morph_rows(rng, 3000, 3000, 4.0)),
                    ("Zero", [(1, 0.0, 0.0, 0.0), (2, 0.0, 0.0, 0.0)]),
                    ("Empty", []),
                    ("Half", [(k, (k + 0.5) * 0.25, -(k + 0.5) * 0.25, 0.125 * k)
                              for k in range(200)])]),
        ("Sleeve_1", [("BreastsBig", _morph_rows(rng, 120, 400, 0.02))]),
    ]


def test_the_tri_parse_matches_the_struct_oracle_bit_for_bit():
    rng = np.random.default_rng(12)
    data = _tri_save_oracle(_tri_rows(rng))
    for blob in (data, data[:-3]):
        ours = TriFile.parse(blob)
        theirs = _tri_parse_oracle(blob)
        assert [s.name for s in ours.shapes] == [n for n, _ in theirs]
        for sh, (_, morphs) in zip(ours.shapes, theirs):
            assert [m.name for m in sh.morphs] == [n for n, _ in morphs]
            for m, (_, offs) in zip(sh.morphs, morphs):
                assert m.idx.dtype == np.int64 and m.delta.dtype == np.float64
                assert m.idx.tolist() == [o[0] for o in offs]
                want = np.array([o[1:] for o in offs], dtype=np.float64).reshape(-1, 3)
                assert np.array_equal(m.delta.view(np.uint64), want.view(np.uint64)), m.name


def test_the_tri_save_matches_the_struct_oracle_byte_for_byte(tmp_path):
    rng = np.random.default_rng(13)
    rows = _tri_rows(rng)
    tri = TriFile(version=9, shapes=[
        TriShape(name=s, morphs=[TriMorph.from_offsets(n, o) for n, o in ms])
        for s, ms in rows])
    tri.save(tmp_path / "a.tri")
    assert (tmp_path / "a.tri").read_bytes() == _tri_save_oracle(rows)
    # Arrays straight from a generator, not tuples: the same bytes.
    m = tri.shapes[0].morphs[0]
    twin = TriMorph(m.name, m.idx.copy(), m.delta.copy())
    TriFile(version=9, shapes=[TriShape("S", [twin])]).save(tmp_path / "b.tri")
    TriFile(version=9, shapes=[TriShape("S", [m])]).save(tmp_path / "c.tri")
    assert (tmp_path / "b.tri").read_bytes() == (tmp_path / "c.tri").read_bytes()
    with pytest.raises(ValueError, match="uint16"):
        TriFile(version=9, shapes=[TriShape("S", [
            TriMorph("M", np.array([70000]), np.ones((1, 3)))])]).save(tmp_path / "d.tri")


# --- the generator ---------------------------------------------------------------

def test_body_morphs_are_copied_verbatim_inside_the_body_only():
    """#osd-bounds with arrays: an OSD index at or past the body's vert count
    never reaches the TRI, and the ones inside arrive unchanged."""
    body = np.array([[x, 0.0, 0.0] for x in range(6)], dtype=np.float64)
    osd = OsdFile(version=1, morphs=[
        OsdMorph.from_offsets("BaseShapeWide", [(1, 0.25, 0.0, 0.0), (6, 9.0, 9.0, 9.0),
                                                (5, -0.5, 0.125, 0.0), (7, 1.0, 1.0, 1.0)]),
        OsdMorph.from_offsets("BaseShapeGone", [(6, 1.0, 1.0, 1.0)])])
    tri = generate_armor_tri({"Plate": np.array([[2.0, 1.0, 0.0]])}, body, osd,
                             body_shape_name="BaseShape", include_body_shapes=True)
    by = {s.name: s for s in tri.shapes}
    assert "BaseShape" in by
    morphs = {m.name: m for m in by["BaseShape"].morphs}
    assert set(morphs) == {"Wide"}, "a morph with nothing inside the body is dropped"
    assert morphs["Wide"].idx.tolist() == [1, 5]
    assert morphs["Wide"].delta.tolist() == [[0.25, 0.0, 0.0], [-0.5, 0.125, 0.0]]
    for sh in tri.shapes:
        for m in sh.morphs:
            assert m.idx.dtype == np.int64 and m.delta.dtype == np.float64


def test_extra_body_osds_are_copied_verbatim_under_the_slider_name():
    """The Hands/Feet path: the OSD morph arrives as arrays, unchanged, with
    the shape prefix stripped from its name."""
    body = np.array([[x, 0.0, 0.0] for x in range(6)], dtype=np.float64)
    hands = OsdFile(version=1, morphs=[
        OsdMorph.from_offsets("HandsWide", [(0, 1.0, 0.0, 0.0), (1, 2.0, 0.5, -0.25)]),
        OsdMorph.from_offsets("HandsEmpty", [])])
    tri = generate_armor_tri({"Hands": np.array([[0.0, 5.0, 0.0], [1.0, 5.0, 0.0]])},
                             body, OsdFile(version=1, morphs=[]),
                             body_shape_name="BaseShape", include_body_shapes=False,
                             extra_body_osds={"Hands": hands})
    by = {s.name: s for s in tri.shapes}
    assert [m.name for m in by["Hands"].morphs] == ["Wide"], "an empty morph is dropped"
    assert by["Hands"].morphs[0].idx.tolist() == [0, 1]
    assert by["Hands"].morphs[0].delta.tolist() == [[1.0, 0.0, 0.0], [2.0, 0.5, -0.25]]
