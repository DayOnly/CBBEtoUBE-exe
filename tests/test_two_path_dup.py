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

"""Audit F102's probe, rebuilt as a tracked tool.

The original lived in an untracked scratchpad and is gone, so its counts (24
runs, 276 lines, 0.346) cannot be reproduced and its own verifier said to treat
them as unverified. What these tests pin is the NEW tool's arithmetic -- and one
bug it shipped with for an hour, which is the interesting one: comparing raw
indented text reported ZERO literal duplication while an eleven-line block was
identical word for word, because the two paths sit at different nesting depths.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis import two_path_dup as td   # noqa: E402


# ------------------------------------------------------- the compare itself

def test_identical_blocks_match_in_full():
    a = ["x = 1", "y = 2", "z = 3", "w = 4", "v = 5", "u = 6"]
    runs, n, ratio = td.compare(a, list(a), min_run=6)
    assert len(runs) == 1 and n == 6 and ratio == 1.0


def test_a_run_shorter_than_the_floor_is_not_counted():
    a = ["x = 1", "y = 2", "z = 3"]
    _runs, n, _r = td.compare(a, list(a), min_run=6)
    assert n == 0


def test_nothing_in_common_is_zero_not_an_error():
    runs, n, ratio = td.compare(["a"] * 8, ["b"] * 8, min_run=6)
    assert runs == [] and n == 0 and ratio == 0.0


# --------------------------------- THE BUG THIS TOOL SHIPPED WITH FOR AN HOUR

def test_indentation_alone_must_not_hide_a_literal_copy():
    """THE MUTATION CONTROL for the fix. The two paths sit at different nesting
    depths, so their leading whitespace differs by construction. Comparing raw
    indented lines reported 0 literal runs while an 11-line block was identical
    word for word -- a confident wrong number produced by the measuring tool."""
    body = ["a = f(1)", "b = g(a)", "c = h(b)", "d = i(c)", "e = j(d)",
            "k = l(e)"]
    deep = ["        " + x for x in body]
    shallow = ["    " + x for x in body]
    raw_runs, raw_n, _r = td.compare(deep, shallow, min_run=6)
    assert raw_n == 0, "indented compare should miss it -- that was the bug"
    _runs, n, _ = td.compare([x.strip() for x in deep],
                             [x.strip() for x in shallow], min_run=6)
    assert n == 6, "stripped compare must find the copy"


# ------------------------------------------------------------ normalisation

def test_comments_and_docstrings_and_blanks_are_removed():
    lines = ['def f():', '    """doc."""', '    # a comment', '',
             '    x = 1  # trailing', '    return x']
    got = [c.strip() for _i, c in td.strip_noise(lines)]
    assert got == ["def f():", "x = 1", "return x"]


def test_a_trailing_comment_cuts_the_comment_not_the_line():
    """THE FIRST DRAFT DROPPED THE WHOLE LINE. That deletes real code from both
    sides and under-reports the duplication this tool exists to measure -- a
    wrong number produced by the measuring instrument, again."""
    lines = ["a = 1  # why", "b = 2", "c = 3  # also why"]
    got = [c.strip() for _i, c in td.strip_noise(lines)]
    assert got == ["a = 1", "b = 2", "c = 3"]


def test_a_string_used_as_a_value_is_not_mistaken_for_a_docstring():
    """Dropping it would delete real code and under-report duplication."""
    lines = ['def f():', '    x = "hello"', '    return x']
    got = [c.strip() for _i, c in td.strip_noise(lines)]
    assert 'x = "hello"' in got


def test_shape_collapses_names_but_not_keywords():
    """If `if` and `for` collapsed together every loop would look like every
    branch and the shape ratio would mean nothing."""
    assert td.shape_of("alpha = beta(gamma)") == td.shape_of("x = y(z)")
    assert td.shape_of("if a:") != td.shape_of("for a:")


def test_shape_ignores_indentation_the_way_literal_now_does():
    assert td.shape_of("    x = y(1)") == td.shape_of("        a = b(1)")


def test_a_rename_is_shape_identical_but_not_literal():
    """The distinction the whole report rests on."""
    a = ["alpha = one(2)", "beta = two(alpha)", "gamma = three(beta)",
         "delta = four(gamma)", "eps = five(delta)", "zeta = six(eps)"]
    b = [x.replace("alpha", "p").replace("beta", "q").replace("gamma", "r")
          .replace("delta", "s").replace("eps", "t").replace("zeta", "u")
         for x in a]
    _lr, lit, _ = td.compare(a, b, min_run=6)
    _sr, shp, _ = td.compare([td.shape_of(x) for x in a],
                             [td.shape_of(x) for x in b], min_run=6)
    assert lit == 0 and shp == 6


# ----------------------------------------------------------- the real tree

def test_both_paths_are_found_in_the_real_source():
    """A rename must not read as "no duplication" -- the tool exits 3 instead,
    and this is the check that the names it looks for still exist."""
    fns = td._functions()
    for name in td.COPY_PATH + td.SWAP_PATH:
        assert name in fns, name


def test_a_missing_path_function_exits_3(monkeypatch, capsys):
    monkeypatch.setattr(td, "COPY_PATH", ("no_such_function_at_all",))
    assert td.main([]) == 3
    assert "0/0 is not a pass" in capsys.readouterr().out


def test_the_real_tree_still_has_shape_duplication_to_report():
    """A floor, not a ceiling: if this ever reads zero, either F102 was fixed
    or the tool stopped finding the functions. Both deserve a look."""
    fns = td._functions()
    lines = {}
    for label, names in (("copy", td.COPY_PATH), ("swap", td.SWAP_PATH)):
        rows = []
        for n in names:
            _rel, _first, src = fns[n]
            rows += [c for _i, c in td.strip_noise(src)]
        lines[label] = rows
    assert len(lines["copy"]) > 100 and len(lines["swap"]) > 100
    _runs, n, _r = td.compare([td.shape_of(x) for x in lines["copy"]],
                              [td.shape_of(x) for x in lines["swap"]],
                              min_run=6)
    assert n > 0, "no shape duplication found at all -- check the extraction"
