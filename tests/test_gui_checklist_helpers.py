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

"""The two checklist helpers shared by the mod list and the overlay list.

These were four near-identical closures inside `launch_gui` (0.992 and 0.986
similar). Nothing could reach them without a display, so the filter contract
was only covered by a RE-IMPLEMENTATION of it in `test_gui_filter.py` -- a test
that would keep passing if the real closure broke. Lifting them to module level
is what makes these tests possible, and that is most of the point of the
consolidation.

The load-bearing property is `test_filtering_never_changes_a_tick`: hiding a mod
must not untick it, or filtering would silently drop mods from a run.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src.gui import repack_filtered, set_ticks_for_visible  # noqa: E402


class _Cb:
    """A stand-in for a tkinter Checkbutton: records pack/pack_forget order."""

    def __init__(self, log, name):
        self.log, self.name = log, name

    def pack(self, **_kw):
        self.log.append(("pack", self.name))

    def pack_forget(self):
        self.log.append(("forget", self.name))


class _Var:
    def __init__(self, v=False):
        self._v = v

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _Canvas:
    def __init__(self, boom=False):
        self.boom, self.moved = boom, None

    def bbox(self, _what):
        if self.boom:
            raise RuntimeError("no scroll region yet")
        return (0, 0, 10, 10)

    def configure(self, **_kw):
        if self.boom:
            raise RuntimeError("nope")

    def yview_moveto(self, frac):
        self.moved = frac


NAMES = ["Alpha Armor", "Beta Armor", "Gamma Robes", "beta boots"]


def _fixture():
    log = []
    items = [{"name": n} for n in NAMES]
    cbs = {n: _Cb(log, n) for n in NAMES}
    varz = {n: _Var(False) for n in NAMES}
    return log, items, cbs, varz


# ------------------------------------------------------------ repack_filtered
def test_shows_only_matches_in_master_order():
    _log, items, cbs, _v = _fixture()
    assert repack_filtered(items, cbs, "beta") == ["Beta Armor", "beta boots"]


def test_empty_query_shows_everything():
    _log, items, cbs, _v = _fixture()
    assert repack_filtered(items, cbs, "") == NAMES
    assert repack_filtered(items, cbs, "   ") == NAMES


def test_everything_is_hidden_before_anything_is_shown():
    """Without the full pack_forget sweep, a mod shown by the previous query
    stays on screen when the query narrows."""
    log, items, cbs, _v = _fixture()
    repack_filtered(items, cbs, "gamma")
    forgot = [n for kind, n in log if kind == "forget"]
    packed = [n for kind, n in log if kind == "pack"]
    assert sorted(forgot) == sorted(NAMES)
    assert packed == ["Gamma Robes"]
    assert log.index(("pack", "Gamma Robes")) > max(
        i for i, (k, _n) in enumerate(log) if k == "forget")


def test_filtering_never_changes_a_tick():
    """THE load-bearing property: a filter must not untick anything, or mods
    silently drop out of the run."""
    _log, items, cbs, varz = _fixture()
    for v in varz.values():
        v.set(True)
    repack_filtered(items, cbs, "gamma")
    assert all(v.get() for v in varz.values())


def test_a_missing_checkbutton_is_skipped_not_raised():
    """items and the widget map are built together, but if they ever diverge a
    KeyError here would take down the whole filter box."""
    _log, items, cbs, _v = _fixture()
    del cbs["Beta Armor"]
    assert repack_filtered(items, cbs, "beta") == ["beta boots"]


def test_canvas_is_optional_and_its_failure_is_swallowed():
    _log, items, cbs, _v = _fixture()
    assert repack_filtered(items, cbs, "alpha", None) == ["Alpha Armor"]
    assert repack_filtered(items, cbs, "alpha", _Canvas(boom=True)) == [
        "Alpha Armor"]


def test_canvas_scrolls_back_to_the_top():
    _log, items, cbs, _v = _fixture()
    c = _Canvas()
    repack_filtered(items, cbs, "beta", c)
    assert c.moved == 0.0


# ------------------------------------------------------- set_ticks_for_visible
def test_ticks_only_the_visible_set():
    _log, items, _c, varz = _fixture()
    assert set_ticks_for_visible(items, varz, "beta", True) == 2
    assert varz["Beta Armor"].get() and varz["beta boots"].get()
    assert not varz["Alpha Armor"].get() and not varz["Gamma Robes"].get()


def test_empty_query_ticks_everything():
    _log, items, _c, varz = _fixture()
    assert set_ticks_for_visible(items, varz, "", True) == len(NAMES)
    assert all(v.get() for v in varz.values())


def test_unticking_is_also_filtered():
    _log, items, _c, varz = _fixture()
    set_ticks_for_visible(items, varz, "", True)
    set_ticks_for_visible(items, varz, "gamma", False)
    assert not varz["Gamma Robes"].get()
    assert varz["Alpha Armor"].get(), "None on a filter unticked a hidden mod"


def test_a_query_matching_nothing_reports_zero():
    """Distinguishes 'ticked nothing' from 'ticked all' at the call site."""
    _log, items, _c, varz = _fixture()
    assert set_ticks_for_visible(items, varz, "zzz", True) == 0
    assert not any(v.get() for v in varz.values())


def test_a_missing_tick_var_is_skipped_not_raised():
    _log, items, _c, varz = _fixture()
    del varz["Beta Armor"]
    assert set_ticks_for_visible(items, varz, "beta", True) == 1
