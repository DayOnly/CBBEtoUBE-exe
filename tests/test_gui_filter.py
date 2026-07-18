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

"""The selected-mods checklist Filter box. Importing src.gui is side-effect-free
(tkinter is imported lazily inside launch_gui), so the matcher is testable
without a display."""
from src.gui import mod_name_matches as M

MODS = [
    "EXM - Rose Petal Armor",
    "EXM - Rose Petal Belts",
    "Songs Of The Minstrels Vol.1 Main File",
    "abc_mst Minstrel Coats",
    "Sample Dark Outfit",
    "Velvet Countess",
]


def _visible(query):
    # mirrors _apply_filter: matches kept in MASTER order.
    return [m for m in MODS if M(m, query)]


def test_empty_query_matches_all():
    assert all(M(m, "") for m in MODS)
    assert all(M(m, "   ") for m in MODS)
    assert _visible("") == MODS


def test_case_insensitive_substring():
    assert M("EXM - Rose Petal Armor", "rose")
    assert M("EXM - Rose Petal Armor", "ROSE")
    assert not M("Velvet Countess", "rose")


def test_multi_token_and_order_independent():
    # every token must be present, in any order
    assert M("EXM - Rose Petal Armor", "rose pe")
    assert M("EXM - Rose Petal Armor", "petal rose")
    assert M("EXM - Rose Petal Armor", "exm armor")
    assert not M("EXM - Rose Petal Armor", "rose cape")   # 'cape' absent


def test_filter_narrows_and_preserves_master_order():
    v = _visible("rose")
    assert v == ["EXM - Rose Petal Armor", "EXM - Rose Petal Belts"]
    # narrower token set drops the belts
    assert _visible("rose armor") == ["EXM - Rose Petal Armor"]
    # a family filter ("minstrel") spans differently-named mods
    assert _visible("minstrel") == ["Songs Of The Minstrels Vol.1 Main File",
                                    "abc_mst Minstrel Coats"]


def test_no_match_is_empty():
    assert _visible("nonexistent") == []
