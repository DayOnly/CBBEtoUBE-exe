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

"""Regression guards for the 2026-07-04 whole-converter audit fixes.

Each test maps to a CONFIRMED finding (cluster/F-number in the audit log):
  C2-F1  frozen --incremental floor collapse   -> _incremental_code_mtime
  C2-F3  cache key ignored the sweep env flag   -> _find_armor_mod_dirs
  C3-F1  jiggle-strip Pelvis origin-spike        -> _strip_jiggle_weights_map
  C4-F1/F2  dead Refresh button / double worker  -> launch_gui source structure
  C5-F1  BSA read_file OOB read in eager mode     -> BSAArchive.read_file
  C5-F2  strings-table unbounded count            -> parse_strings_table
  C5-F3  BSA header trusts folder_count           -> BSAArchive._parse
"""
import inspect
import re
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.nif_convert as nc
import src.bsa_strings as bs
from src import auto_convert, gui

PELVIS = "NPC Pelvis [Pelv]"
THIGH = "NPC L Thigh [LThg]"
BREAST = "NPC L Breast"


# ---------------------------------------------------------------------------
# C3-F1: jiggle-strip must NOT force a jiggle-only vert onto Pelvis when the
# shape has no Pelvis (add_bone would give it no skin-to-bone xform -> the vert
# skins to the origin = floor spike). Mirrors the _strip_genital_weights_map
# guard.
# ---------------------------------------------------------------------------
def _leg_plate_with_jiggle_only_vert():
    # Verts 0,1,2 dominated by a rigid leg bone (-> classified leg-plate);
    # vert 3 carries ONLY grafted breast (jiggle) weight => jiggle-only.
    return {
        THIGH:  [(0, 1.0), (1, 1.0), (2, 0.9)],
        BREAST: [(2, 0.1), (3, 1.0)],
    }


def test_jiggle_strip_no_pelvis_leaves_zero_weight_not_origin_spike():
    out = nc._strip_jiggle_weights_map(_leg_plate_with_jiggle_only_vert())
    # The bug: the jiggle-only vert 3 was force-assigned {PELVIS: 1.0}, and
    # Pelvis (absent from the source) gets no STB -> origin spike. Fixed: Pelvis
    # must NOT appear, and vert 3 is left for _fill_zero_weight_verts.
    assert PELVIS not in out, "Pelvis-less shape must not gain a no-STB Pelvis bone"
    v3 = [(b, w) for b, pairs in out.items() for (vi, w) in pairs if vi == 3]
    assert v3 == [], "jiggle-only vert should be zero-weight, not pinned to Pelvis"


def test_jiggle_strip_with_pelvis_falls_back_to_pelvis():
    wm = _leg_plate_with_jiggle_only_vert()
    wm[PELVIS] = [(0, 0.0001)]          # shape ALREADY carries Pelvis (has STB)
    out = nc._strip_jiggle_weights_map(wm)
    v3_pelvis = dict(out.get(PELVIS, [])).get(3)
    assert v3_pelvis == pytest.approx(1.0), \
        "with Pelvis present the jiggle-only vert should renormalize onto Pelvis"


def test_jiggle_strip_matches_genital_guard_shape():
    # Both strippers must share the has_pelvis discipline; guard against a future
    # divergence re-introducing the unguarded fallback.
    jsrc = inspect.getsource(nc._strip_jiggle_weights_map)
    assert "has_pelvis" in jsrc


# ---------------------------------------------------------------------------
# C2-F1: in a frozen build the incremental floor must come from the executable
# mtime, not a glob of on-disk src/*.py (which is empty in the PYZ).
# ---------------------------------------------------------------------------
def test_incremental_code_mtime_frozen_uses_executable(monkeypatch, tmp_path):
    fake_exe = tmp_path / "CBBEtoUBE.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(auto_convert.sys, "frozen", True, raising=False)
    monkeypatch.setattr(auto_convert.sys, "executable", str(fake_exe))
    mt = auto_convert._incremental_code_mtime()
    assert mt == fake_exe.stat().st_mtime
    assert mt > 0.0, "frozen floor must not collapse to 0.0 (would reuse stale meshes)"


def test_incremental_code_mtime_source_build_globs_py(monkeypatch):
    monkeypatch.setattr(auto_convert.sys, "frozen", False, raising=False)
    mt = auto_convert._incremental_code_mtime()
    # There ARE src/*.py on disk in a source checkout, so the floor is > 0.
    assert mt > 0.0


# ---------------------------------------------------------------------------
# C2-F3: toggling CBBE2UBE_NO_VANILLA_SWEEP must bust the discovery memo (the
# flag changes the returned candidate set).
# ---------------------------------------------------------------------------
def test_armor_mod_dirs_cache_key_includes_sweep_flag(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _fake_uncached(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr(auto_convert, "_find_armor_mod_dirs_uncached", _fake_uncached)
    auto_convert._ARMOR_MOD_DIRS_CACHE.clear()

    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "1")
    auto_convert._find_armor_mod_dirs(tmp_path)
    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "0")
    auto_convert._find_armor_mod_dirs(tmp_path)
    assert calls["n"] == 2, "toggling the sweep flag must miss the cache, not reuse a stale list"

    # Same flag value twice DOES hit the cache (no needless rescan).
    auto_convert._find_armor_mod_dirs(tmp_path)
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# C4-F1 / C4-F2: the mis-relocated .start() left _refresh_mods dead and
# _ov_refresh double-launching. Guard each refresh closure has exactly one
# worker start.
# ---------------------------------------------------------------------------
def test_refresh_closures_start_worker_exactly_once():
    src = inspect.getsource(gui.launch_gui)
    start_rx = re.compile(r"threading\.Thread\(target=work, daemon=True\)\.start\(\)")

    def _body(name):
        m = re.search(r"\n    def " + name + r"\(\):(.*?)(?=\n    def |\n    # ----)",
                      src, re.S)
        assert m, f"could not isolate {name} body"
        return m.group(1)

    assert len(start_rx.findall(_body("_refresh_mods"))) == 1, \
        "_refresh_mods must start its scan worker exactly once (was dead)"
    assert len(start_rx.findall(_body("_ov_refresh"))) == 1, \
        "_ov_refresh must start its scan worker exactly once (was doubled)"


# ---------------------------------------------------------------------------
# C5-F1: BSA read_file must bounds-check a file-supplied offset even in eager
# mode (default) -> return None, never OOB IndexError/struct.error.
# ---------------------------------------------------------------------------
def _bare_archive(data, index, embed=False):
    a = bs.BSAArchive.__new__(bs.BSAArchive)
    a._data = data
    a._eager = True
    a._embed_names = embed
    a._index = index
    a.path = Path("does-not-exist.bsa")
    return a


def test_bsa_read_file_offset_past_eof_returns_none():
    a = _bare_archive(b"\x00" * 64, {"f/x.nif": (0x7FFFFFFF, 100, False)})
    assert a.read_file("f/x.nif") is None


def test_bsa_read_file_compressed_short_buffer_returns_none():
    # off in-range but size claims a compressed header that runs past EOF.
    a = _bare_archive(b"\x00" * 8, {"f/x.nif": (6, 4, True)})
    assert a.read_file("f/x.nif") is None


def test_bsa_read_file_valid_entry_still_reads():
    payload = b"HELLO-NIF-BYTES!"
    a = _bare_archive(payload, {"f/x.nif": (0, len(payload), False)})
    assert a.read_file("f/x.nif") == payload


# ---------------------------------------------------------------------------
# C5-F2: parse_strings_table must bound a file-supplied count against the buffer
# (no billion-iteration spin, no raw struct.error).
# ---------------------------------------------------------------------------
def test_parse_strings_table_huge_count_is_bounded():
    data = struct.pack("<II", 0xFFFFFFFF, 0) + b"\x00" * 16
    out = bs.parse_strings_table(data, lengthprefixed=False)
    assert out == {}          # clean empty result, no exception, returns promptly


def test_parse_strings_table_valid_still_parses():
    # count=1, one entry (id=7, offset=0), data block = b"hi\x00".
    body = b"hi\x00"
    data = struct.pack("<II", 1, len(body)) + struct.pack("<II", 7, 0) + body
    out = bs.parse_strings_table(data, lengthprefixed=False)
    assert out == {7: "hi"}


# ---------------------------------------------------------------------------
# C5-F3: BSA _parse must reject an absurd header folder_count with a clean
# ValueError, not a raw struct.error / hang.
# ---------------------------------------------------------------------------
def test_bsa_parse_absurd_folder_count_raises_clean(tmp_path):
    # magic + <8I> header: version=105, folder_rec_off=36, flags=0,
    # folder_count=0xFFFFFFFF, rest 0.
    hdr = b"BSA\x00" + struct.pack("<8I", 105, 36, 0, 0xFFFFFFFF, 0, 0, 0, 0)
    p = tmp_path / "bad.bsa"
    p.write_bytes(hdr + b"\x00" * 128)
    with pytest.raises(ValueError):
        bs.BSAArchive(p)


# ---------------------------------------------------------------------------
# Coverage/winner exclusion must use the REAL --merged-name (not a hardcoded
# "cbbe_to_ube_combined.esp") and cover every ESL-split piece -- else the
# non-body coverage pass reads a custom-named Combined's own overrides as
# load-order winners and mis-covers.
# ---------------------------------------------------------------------------
def test_combined_output_names_default_no_split():
    got = auto_convert._combined_output_names("CBBE_to_UBE_Combined.esp", [])
    assert got == {"cbbe_to_ube_combined.esp"}


def test_combined_output_names_includes_split_pieces():
    ordered = ["Skyrim.esm", "CBBE_to_UBE_Combined.esp",
               "CBBE_to_UBE_Combined2.esp", "CBBE_to_UBE_Combined3.esp",
               "SomeMod.esp"]
    got = auto_convert._combined_output_names("CBBE_to_UBE_Combined.esp", ordered)
    assert got == {"cbbe_to_ube_combined.esp", "cbbe_to_ube_combined2.esp",
                   "cbbe_to_ube_combined3.esp"}
    assert "somemod.esp" not in got and "skyrim.esm" not in got


def test_combined_output_names_custom_merged_name():
    # The bug: a hardcoded "cbbe_to_ube_combined.esp" would MISS a custom name.
    ordered = ["MyUBE.esp", "MyUBE2.esp", "Unrelated.esp"]
    got = auto_convert._combined_output_names("MyUBE.esp", ordered)
    assert got == {"myube.esp", "myube2.esp"}
    assert "unrelated.esp" not in got


def test_combined_output_names_accepts_paths():
    from pathlib import Path as _P
    ordered = [_P("x/CBBE_to_UBE_Combined.esp"), _P("y/CBBE_to_UBE_Combined2.esp")]
    got = auto_convert._combined_output_names("CBBE_to_UBE_Combined.esp", ordered)
    assert got == {"cbbe_to_ube_combined.esp", "cbbe_to_ube_combined2.esp"}
