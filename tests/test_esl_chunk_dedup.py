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

"""#esl-chunk-dedup -- armours that share a minted armature go into ONE piece.

The coverage passes cut their output into ESL-sized pieces, one armour at a time in
scan order. Two armours sharing an armature landed in different pieces often enough
that the armature was minted in both: live, 36 of the non-body coverage's 2,093
distinct armatures (2,129 records in two pieces). Grouping the armours that share
armatures and placing each group whole removes every duplicate while each armour keeps
its one line in one piece. CBBE2UBE_NO_ESL_CHUNK_DEDUP=1 restores the scan-order fill.
"""
import json

import pytest

from src import esp
from src import ube_patcher as up
from src.ube_patcher import _chunk_targets_for_esl

OFF = "CBBE2UBE_NO_ESL_CHUNK_DEDUP"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _rec(tag=0):
    return esp.Record(sig=b"ARMA", flags=0, formid=0x800, timestamp_vc=0,
                      version_unk=0x002C,
                      payload=esp.encode_subrecord(b"DNAM", bytes([tag & 0xFF]) * 4))


def _t(n, keys):
    return (("armours.esp", 0x1000 + n), "armours.esp", [("src.esp", k) for k in keys])


def _mint(targets):
    return {a: _rec(a[1]) for _armo, _p, tm in targets for a in tm}


def _minted_in(chunk, mint):
    return {a for _armo, _p, tm in chunk for a in tm if a in mint}


def _repeats(chunks, mint):
    seen, rep = set(), 0
    for c in chunks:
        ks = _minted_in(c, mint)
        rep += len(ks & seen)
        seen |= ks
    return rep


def _boundary_case():
    """cap 5. Scan order: armour 0 mints 1,2,3; armour 1 mints 4,5,6 (does not fit
    with 0 -> second piece); armour 2 mints 1,7,8 (fits neither -> third piece) and
    so mints armature 1 a second time. Armours 0 and 2 together need exactly 5."""
    targets = [_t(0, [1, 2, 3]), _t(1, [4, 5, 6]), _t(2, [1, 7, 8])]
    return targets, _mint(targets)


# --- the grouping -----------------------------------------------------------------

def test_an_armature_two_armours_share_is_minted_in_one_piece_only():
    targets, mint = _boundary_case()
    chunks = _chunk_targets_for_esl(targets, mint, cap=5)
    assert len(chunks) == 2
    assert _repeats(chunks, mint) == 0
    holder = [i for i, c in enumerate(chunks) if ("armours.esp", 0x1000) in
              {armo for armo, _p, _tm in c}]
    assert [armo for armo, _p, _tm in chunks[holder[0]]] == [
        ("armours.esp", 0x1000), ("armours.esp", 0x1002)]


def test_a_chain_of_shared_armatures_stays_together():
    """0 shares with 1, 1 shares with 2: one group, though 0 and 2 share nothing."""
    targets = [_t(0, [1, 2]), _t(9, [7, 8, 20]), _t(1, [2, 3]), _t(2, [3, 4])]
    mint = _mint(targets)
    chunks = _chunk_targets_for_esl(targets, mint, cap=4)
    assert _repeats(chunks, mint) == 0
    where = {armo[1]: i for i, c in enumerate(chunks) for armo, _p, _tm in c}
    assert where[0x1000] == where[0x1001] == where[0x1002]


def test_a_later_group_fills_room_left_in_an_earlier_piece():
    """cap 10: groups of 6, 6 and 4 armatures fit in two pieces, not three."""
    targets = [_t(0, range(0, 6)), _t(1, range(10, 16)), _t(2, range(20, 24))]
    mint = _mint(targets)
    chunks = _chunk_targets_for_esl(targets, mint, cap=10)
    assert len(chunks) == 2
    assert [len(_minted_in(c, mint)) for c in chunks] == [10, 6]


def test_a_piece_may_fill_exactly_to_the_cap():
    targets = [_t(0, range(0, 6)), _t(1, range(10, 14))]
    mint = _mint(targets)
    assert len(_chunk_targets_for_esl(targets, mint, cap=10)) == 1


def test_armours_keep_scan_order_inside_a_piece():
    """A run that fits one piece mints its records in the order it always has."""
    targets = [_t(0, [1]), _t(1, [2]), _t(2, [1]), _t(3, [3]), _t(4, [2])]
    mint = _mint(targets)
    chunks = _chunk_targets_for_esl(targets, mint, cap=2048)
    assert chunks == [targets]


def test_a_group_bigger_than_a_piece_is_split_and_every_piece_keeps_the_cap():
    """Seven armours chained through shared armatures need 8 -- more than a cap-5
    piece holds. Only that group is split (scan order); no armour is lost or
    repeated, and every piece stays within the cap."""
    chain = [_t(i, [i, i + 1]) for i in range(7)]
    other = [_t(50, [100, 101])]
    targets = chain + other
    mint = _mint(targets)
    chunks = _chunk_targets_for_esl(targets, mint, cap=5)
    armos = [armo for c in chunks for armo, _p, _tm in c]
    assert sorted(armos) == sorted(t[0] for t in targets)
    assert all(len(_minted_in(c, mint)) <= 5 for c in chunks)


def test_an_armour_minting_nothing_opens_no_extra_piece():
    """A single over-cap armour fills its own piece; a later armour whose armatures
    were not minted must not open an empty piece of its own."""
    big = _t(0, range(0, 8))
    ghost = (("armours.esp", 0x2000), "armours.esp", [("src.esp", 9999)])
    mint = _mint([big])
    chunks = _chunk_targets_for_esl([big, ghost], mint, cap=5)
    assert len(chunks) == 1
    assert len(chunks[0]) == 2


def test_switched_off_the_scan_order_fill_returns(monkeypatch):
    monkeypatch.setenv(OFF, "1")
    targets, mint = _boundary_case()
    chunks = _chunk_targets_for_esl(targets, mint, cap=5)
    assert _repeats(chunks, mint) == 1
    assert [[armo[1] for armo, _p, _tm in c] for c in chunks] == [
        [0x1000], [0x1001], [0x1002]]


# --- the written pieces ------------------------------------------------------------

def _emit(tmp_path, targets, mint, cap):
    out = tmp_path / "UBE_ModNonBody_Coverage UBE patch.esp"
    res = up._emit_coverage_pieces(
        out, targets, mint, ["src.esp"], own_byte=1, author="t", description="t",
        ini_header=[], emit_sidecar=True, cap=cap)
    per_piece, links = {}, {}
    for name in res["pieces"]:
        doc = json.loads((tmp_path / (name + ".skypatcher.json"))
                         .read_text(encoding="utf-8"))
        saved = esp.ESP.load(tmp_path / name)
        fids = {r.formid for g in saved.groups for r in g.records}
        per_piece[name] = set()
        for ent in doc:
            srcs = []
            for a in ent["adds"]:
                assert a["fid"] in fids, "a link names a record its piece lacks"
                srcs.append(tuple(a["src"]))
                per_piece[name].add(tuple(a["src"]))
            links[tuple(ent["armo"])] = srcs
    return res, per_piece, links


def test_written_pieces_hold_each_armature_once_and_every_armour_its_links(
        tmp_path, monkeypatch):
    targets, mint = _boundary_case()
    res, per_piece, links = _emit(tmp_path, targets, mint, cap=5)
    assert res["minted_armas"] == 8 and len(res["pieces"]) == 2
    seen = set()
    for srcs in per_piece.values():
        assert not (srcs & seen)
        seen |= srcs

    monkeypatch.setenv(OFF, "1")
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    res_off, _pp, links_off = _emit(off_dir, targets, mint, cap=5)
    assert res_off["minted_armas"] == 9 and len(res_off["pieces"]) == 3
    assert links == links_off, "every armour links the same armatures either way"
