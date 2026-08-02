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

"""Memo for `_read_source_hdt_xml_text` -- the hottest helper in a conversion.

Profiled: **24.7% of total wall-clock**, 23.5 calls per NIF at 147ms each. The waste is
one re-read per SHAPE: `_precreate_custom_bone_chains` (via `_install_skin` ->
`_copy_shape`) made 10 of 13 reads of the same SOURCE XML on one dress, 9 of them slow,
because resolution falls through to a glob across every installed mod.

Measured, 4 interleaved warm reps per arm on 4 NIFs:

    wall-clock  memo ON  : 25.6s (median)      memo OFF : 32.1s (median)   ~20%
    inside the helper    : 0.71s (median)                 7.04s (median)   ~90%

(Two earlier figures -- 48% and 33% -- were cold-cache and n=2 noise. The OFF arm is
stable to 0.2s across reps, which is what makes 20% trustworthy.)

WHY THE SAFETY TESTS BELOW MATTER MORE THAN THE SPEED. A stale answer means a missed
collider set, and a skin pass grafting onto an SMP collider is the in-game-proven
failure: on 2026-07-26 the breasts tore off the body and fell through the terrain. So
the memo is bounded twice -- mtime/size keyed AND cleared per armor -- and both bounds
are pinned here."""
import os
import time
from pathlib import Path

import pytest

import src.nif_convert as nc


@pytest.fixture(autouse=True)
def _clean():
    nc._hdt_xml_cache_clear()
    yield
    nc._hdt_xml_cache_clear()


def _stub(monkeypatch, values):
    """Make the underlying resolution return successive values, counting calls."""
    calls = {"n": 0}

    def fake(path, nif=None):
        v = values[min(calls["n"], len(values) - 1)]
        calls["n"] += 1
        return v
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text_uncached", fake)
    return calls


def test_second_read_is_served_from_the_memo(tmp_path, monkeypatch):
    f = tmp_path / "a.nif"
    f.write_bytes(b"x")
    calls = _stub(monkeypatch, ["<xml/>"])
    assert nc._read_source_hdt_xml_text(f) == "<xml/>"
    assert nc._read_source_hdt_xml_text(f) == "<xml/>"
    assert calls["n"] == 1, "the second read must not hit the disk again"


def test_a_None_result_is_memoised_too(tmp_path, monkeypatch):
    """The MISS is the expensive case -- it is the one that falls through to the
    glob across every mod. Not caching it would leave most of the cost in place."""
    f = tmp_path / "a.nif"
    f.write_bytes(b"x")
    calls = _stub(monkeypatch, [None])
    assert nc._read_source_hdt_xml_text(f) is None
    assert nc._read_source_hdt_xml_text(f) is None
    assert calls["n"] == 1


def test_rewriting_the_nif_INVALIDATES_the_entry(tmp_path, monkeypatch):
    """BOUND 1. The passes rewrite the destination NIF between reads; a memo that
    survived that could hand a later pass a collider set from before the rewrite."""
    f = tmp_path / "a.nif"
    f.write_bytes(b"x")
    calls = _stub(monkeypatch, ["first", "second"])
    assert nc._read_source_hdt_xml_text(f) == "first"
    time.sleep(0.01)
    f.write_bytes(b"xxxxxxx")            # different size AND mtime
    assert nc._read_source_hdt_xml_text(f) == "second"
    assert calls["n"] == 2


def test_size_change_alone_invalidates(tmp_path, monkeypatch):
    """mtime granularity is coarse on some filesystems, so size is part of the key."""
    f = tmp_path / "a.nif"
    f.write_bytes(b"x")
    _stub(monkeypatch, ["first", "second"])
    nc._read_source_hdt_xml_text(f)
    st = os.stat(f)
    f.write_bytes(b"xx")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))   # same mtime, different size
    assert nc._read_source_hdt_xml_text(f) == "second"


def test_distinct_paths_do_not_share_an_entry(tmp_path, monkeypatch):
    a, b = tmp_path / "a.nif", tmp_path / "b.nif"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    _stub(monkeypatch, ["A", "B"])
    assert nc._read_source_hdt_xml_text(a) == "A"
    assert nc._read_source_hdt_xml_text(b) == "B"


def test_an_unstatable_path_is_NEVER_memoised(tmp_path, monkeypatch):
    """No stat -> no key -> no cache. Fails toward re-reading, never toward stale."""
    missing = tmp_path / "gone.nif"
    calls = _stub(monkeypatch, [None])
    nc._read_source_hdt_xml_text(missing)
    nc._read_source_hdt_xml_text(missing)
    assert calls["n"] == 2
    assert not nc._HDT_XML_TEXT_CACHE


def test_clear_empties_it(tmp_path, monkeypatch):
    f = tmp_path / "a.nif"
    f.write_bytes(b"x")
    calls = _stub(monkeypatch, ["v"])
    nc._read_source_hdt_xml_text(f)
    nc._hdt_xml_cache_clear()
    nc._read_source_hdt_xml_text(f)
    assert calls["n"] == 2


def test_convert_nif_clears_it_per_armor():
    """BOUND 2. Even if the mtime key were somehow wrong, staleness cannot cross from
    one armour to the next."""
    import inspect
    src = inspect.getsource(nc.convert_nif)
    assert "_hdt_xml_cache_clear()" in src
    body = src[src.index("dst_path = Path(dst_path)"):]
    assert body.index("_hdt_xml_cache_clear()") < body.index("load_nif"), \
        "the clear must happen before anything reads the NIF"


def test_the_collider_and_softbody_readers_go_through_the_memo():
    """They are the reason the memo has to be correct -- and the reason it pays."""
    import inspect
    for fn in (nc._hdt_collider_shape_names, nc._hdt_softbody_shape_names):
        assert "_read_source_hdt_xml_text" in inspect.getsource(fn)
