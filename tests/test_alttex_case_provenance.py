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

r"""#alttex-case-provenance -- a name shared only up to case ('Fur' beside
'fur') binds through the source like any other shared name, or is dropped.

THE DEFECT. The reconcile matches an entry's name case-insensitively and keeps
the FIRST spelling the converted NIF carries. The rename works on exact names,
so it never touches 'Fur' beside 'fur', and #alttex-set-provenance grouped the
source's shells by exact name too: a set naming 'fur' and 'Fur' read the
source, found no group to bind, and fell back to one entry per name -- the
set's first-listed entry on the first spelling, possibly the other shell's
colour (with no source the name was dropped, correctly). A set naming 'fur'
once read no source at all and did the same. Now a converted NIF carrying one
name under two spellings has its source read; the source's shells are grouped
case-insensitively; and every name the reconcile knows was shared binds source
3D index -> the converted shape with that shell's print, or is dropped.
`CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE=1` restores #alttex-set-provenance as it
first shipped.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import ube_patcher as up                               # noqa: E402
from tests.synthetic_nif import uv_sphere                        # noqa: E402
from tests.test_alttex_exact_provenance import (                # noqa: E402
    BLUE, GREEN, RED, TAN, _resolve_to)
from tests.test_alttex_set_provenance import (                  # noqa: E402
    BODY, COAT, KEY, SHELL1, SHELL2, SWITCHES, _reconciled, _world,
    needs_pynifly)

CASE_OFF = "CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE"
assert CASE_OFF in SWITCHES


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


OTHER = uv_sphere(9.0, rings=7, segs=9)     # a print no source shell has

# The author's 'Fur' (shell 0) beside 'fur' (shell 1): no exact repeat, so
# the rename leaves both and the converted NIF carries both spellings.
PAIR = [("Fur", *SHELL1), ("fur", *SHELL2), ("coat", *COAT)]
# GREEN is written for shell 1 ('fur') and listed FIRST.
PAIR_SET = [("fur", GREEN, 1), ("Fur", TAN, 0), ("coat", BLUE, 2)]


def test_case_variant_names_are_the_names_carried_under_two_spellings():
    assert up._case_variant_names(["Fur", "fur", "coat"]) == {"fur"}
    assert up._case_variant_names(["FUR", "Fur", "fur", "x"]) == {"fur"}
    assert up._case_variant_names(["fur", "fur", "coat"]) == frozenset(), (
        "one spelling twice is a literal duplicate, not a case variant")
    assert up._case_variant_names(["", "coat"]) == frozenset()


@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_a_case_pair_binds_each_entry_to_its_own_shell(monkeypatch, tmp_path,
                                                       found):
    src, plugin = _world(tmp_path, PAIR, PAIR, PAIR_SET)
    seen = _resolve_to(monkeypatch, {KEY: src} if found else {})
    got = _reconciled(plugin, tmp_path)
    if found:
        assert got == [[("fur", GREEN, 1), ("Fur", TAN, 0),
                        ("coat", BLUE, 2)]], (
            "one entry per name put GREEN on 'Fur', the other shell")
    else:
        assert got == [[("coat", BLUE, 2)]], "no source: the name is dropped"
    assert seen == [[KEY]]


@needs_pynifly
@pytest.mark.parametrize("found", [True, False])
def test_a_case_pair_named_once_reads_the_source(monkeypatch, tmp_path, found):
    # No set repeats the name: the converted NIF's two spellings are what
    # make the source read.
    src, plugin = _world(tmp_path, PAIR, PAIR,
                         [("fur", GREEN, 1), ("coat", BLUE, 2)])
    seen = _resolve_to(monkeypatch, {KEY: src} if found else {})
    got = _reconciled(plugin, tmp_path)
    assert got == ([[("fur", GREEN, 1), ("coat", BLUE, 2)]] if found
                   else [[("coat", BLUE, 2)]]), (
        "by name GREEN landed on 'Fur' (index 0), the other shell")
    assert seen == [[KEY]]


@needs_pynifly
def test_a_case_pairs_colour_follows_the_print_not_the_order(monkeypatch,
                                                             tmp_path):
    src, plugin = _world(tmp_path, PAIR,
                         [("fur", *SHELL2), ("Fur", *SHELL1), ("coat", *COAT)],
                         PAIR_SET)
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 0),
                                              ("Fur", TAN, 1),
                                              ("coat", BLUE, 2)]]


@needs_pynifly
def test_a_case_pair_of_identical_shells_is_told_apart_by_its_spelling(
        monkeypatch, tmp_path):
    # Unlike literal duplicates, each spelling is one source shell's shipped
    # name, so two shells with one print still bind -- as renamed shells do.
    same = [("Fur", *SHELL1), ("fur", *SHELL1), ("coat", *COAT)]
    src, plugin = _world(tmp_path, same, same, PAIR_SET)
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 1),
                                              ("Fur", TAN, 0),
                                              ("coat", BLUE, 2)]]


@needs_pynifly
def test_a_case_pair_whose_shape_matches_no_source_shell_is_dropped(
        monkeypatch, tmp_path):
    src, plugin = _world(tmp_path, PAIR,
                         [("Fur", *SHELL1), ("fur", *OTHER), ("coat", *COAT)],
                         PAIR_SET)
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("coat", BLUE, 2)]], (
        "the converted 'fur' is not the source shell's geometry")


@needs_pynifly
def test_a_lost_case_variant_drops_its_colour_when_another_name_reads_the_source(
        monkeypatch, tmp_path):
    # 'fur' (shell 1) was lost; the NIF carries 'Fur' once, so nothing about
    # 'fur' reads the source -- the renamed 'belt' pair does. The source's
    # 'Fur'/'fur' group tells that GREEN was the lost shell's.
    src_shapes = PAIR[:2] + [("belt", *COAT), ("belt", *BODY)]
    src, plugin = _world(tmp_path, src_shapes,
                         [("Fur", *SHELL1), ("belt", *COAT),
                          ("belt:1", *BODY)],
                         [("fur", GREEN, 1), ("belt", BLUE, 2)])
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("belt", BLUE, 1)]], (
        "by name the lost shell's GREEN landed on 'Fur'")


@needs_pynifly
def test_a_repeated_name_the_source_has_once_binds_by_its_index(monkeypatch,
                                                                tmp_path):
    # The set names 'coat' twice; the source has one 'coat'. Each entry binds
    # by its source index: BLUE's index 1 is not a 'coat' shell, so it is
    # dropped rather than kept as the name's first-listed entry.
    shapes = [("coat", *COAT), ("fur", *SHELL1)]
    src, plugin = _world(tmp_path, shapes, shapes,
                         [("coat", BLUE, 1), ("coat", TAN, 0),
                          ("fur", GREEN, 1)])
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("coat", TAN, 0),
                                              ("fur", GREEN, 1)]]


@needs_pynifly
def test_a_name_the_nif_carries_twice_but_the_source_once_is_dropped(
        monkeypatch, tmp_path):
    # The source (read for its renamed 'belt' pair) has one 'fur'; the
    # converted NIF carries two, one of them no source shell explains. Which
    # 'fur' the entry meant is not known: by name it took the LAST.
    src, plugin = _world(
        tmp_path, [("coat", *COAT), ("fur", *SHELL1), ("belt", *SHELL2),
                   ("belt", *BODY)],
        [("coat", *COAT), ("fur", *SHELL1), ("fur", *OTHER),
         ("belt", *SHELL2), ("belt:1", *BODY)],
        [("fur", GREEN, 1), ("coat", TAN, 0)])
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("coat", TAN, 0)]]


@needs_pynifly
def test_a_case_variant_of_a_renamed_shell_must_keep_its_print(monkeypatch,
                                                               tmp_path,
                                                               capsys):
    # Source 'Fur', 'fur', 'fur' ships 'Fur', 'fur', 'fur:1'. The converted
    # 'Fur' is not the source 'Fur's geometry: the mesh changed since, so
    # nothing of the shared name is bound.
    src, plugin = _world(
        tmp_path, [("Fur", *COAT), ("fur", *SHELL1), ("fur", *SHELL2)],
        [("Fur", *OTHER), ("fur", *SHELL1), ("fur:1", *SHELL2)],
        [("Fur", TAN, 0), ("fur", GREEN, 1), ("fur", RED, 2)])
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[]]
    assert "is not the mesh converted" in capsys.readouterr().err


@needs_pynifly
def test_switched_off_a_case_pair_binds_as_the_parent(monkeypatch, tmp_path):
    monkeypatch.setenv(CASE_OFF, "1")
    src, plugin = _world(tmp_path, PAIR, PAIR, PAIR_SET)
    _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 0),
                                              ("coat", BLUE, 2)]], (
        "#alttex-set-provenance as first shipped: the known wrong shell")
    src, plugin = _world(tmp_path, PAIR, PAIR,
                         [("fur", GREEN, 1), ("coat", BLUE, 2)])
    seen = _resolve_to(monkeypatch, {KEY: src})
    assert _reconciled(plugin, tmp_path) == [[("fur", GREEN, 0),
                                              ("coat", BLUE, 2)]]
    assert seen == [], "a case variant alone reads no source"
