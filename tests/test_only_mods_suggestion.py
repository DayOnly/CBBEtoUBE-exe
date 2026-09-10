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

"""`--only-mods` matched nothing: say so ACCURATELY.

The old refusal read "Run `scan` or the GUI 'Refresh mod list' for the exact
names" -- and `scan` lists mods that merely LOOK like armour (an ESP plus NIFs
under armour paths), which is a WIDER, differently-derived set than the
plugin-driven conversion candidates this flag actually filters. A mod can sit
in `scan`'s table and be absent here. Following that message cost three arms
on 2026-09-07 before the two lists were noticed to disagree.

So the message now names the right list, and the near-miss suggestion does the
work: the real case that went wrong is the first suggestion for the name that
failed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.auto_convert import _near_mod_names           # noqa: E402


# SYNTHETIC names -- tracked content never names a third-party mod. What is
# reproduced here is the SHAPE of the real miss, which is the part that matters:
# one project ships several plugins whose names share a distinctive prefix and
# differ only in the suffix, and the two listings show DIFFERENT ones of them.
_ASKED_FOR = "PROJECTX - Extra Outfits Add-On"      # what the wider list showed
_REAL_NAME = "PROJECTX - Translation Pack SE"       # what this filter holds
_OTHERS = ["Alpha Robes Replacer", "Beta Guard Armour",
           "Gamma Cloth Collection", "Delta Heavy Set",
           "Epsilon Light Set", "Zeta Circlets"]


def test_the_case_that_actually_went_wrong_is_the_first_suggestion():
    hits = _near_mod_names(_ASKED_FOR.lower(), [_REAL_NAME] + _OTHERS)
    assert hits, "no suggestion at all for a name that differs by a suffix"
    assert hits[0] == _REAL_NAME


def test_suggestions_come_back_in_their_REAL_casing():
    """`--only-mods` lowercases what it matches, so a suggestion echoed in
    lowercase cannot be pasted straight back into the flag."""
    hits = _near_mod_names(_ASKED_FOR.lower(), [_REAL_NAME] + _OTHERS)
    assert _REAL_NAME in hits
    assert all(h in ([_REAL_NAME] + _OTHERS) for h in hits)


def test_matching_is_case_insensitive_in_both_directions():
    assert _near_mod_names("ZETA CIRCLETS", _OTHERS)[0] == "Zeta Circlets"
    assert _near_mod_names("zeta circlets", _OTHERS)[0] == "Zeta Circlets"


def test_nothing_close_suggests_nothing():
    """A wrong suggestion is worse than none -- it sends the reader off again."""
    assert _near_mod_names("zzzz completely unrelated qqqq", _OTHERS) == []


def test_it_is_bounded_and_safe_on_an_empty_list():
    assert _near_mod_names("anything", []) == []
    many = ["Armour Mod %02d" % i for i in range(50)]
    assert len(_near_mod_names("Armour Mod 07", many)) <= 3


def test_the_refusal_no_longer_points_at_the_wrong_list():
    """The message is the thing that misled; pin its content, not just the
    existence of a suggestion."""
    src = (REPO_ROOT / "src" / "auto_convert.py").read_text(
        encoding="utf-8", errors="replace")
    block = src.split("--only-mods matched no")[1][:400]
    assert "NOT the same list as `scan`" in block, (
        "the refusal must say that `scan` is a different list")
    assert "list_convertible_mods" in block, (
        "it must name the function that mirrors this filter exactly")
    # and the old, wrong instruction must be gone
    assert not re.search(r"Run\s+`scan`\s+or the GUI", src), (
        "the old message sent the reader to `scan` for names this flag does "
        "not match")
