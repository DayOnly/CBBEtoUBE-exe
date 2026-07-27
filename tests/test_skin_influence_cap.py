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

"""#skin-influence-cap -- the 4-influence cap applied to the MAIN skin install.

`_install_skin` already gates on "only add bones that still carry weight", and its
own comment names the stake: a zero-weight `add_bone`'d bone desyncs the partition
palette and equip-CTDs. But it measures that BEFORE the save, and the save keeps
only the 4 largest influences per vertex and does NOT renormalise. A bone whose
every weight is the 5th-largest on its vertex passes the gate, gets added, and lands
on disk with an empty weight list.

Evidence this is the live mechanism, not a theory:

  - Seeding 5 bones onto verts that already had weight returned 3 of 5 after a
    save/reload -- the save silently dropped two.
  - `setShapeWeights(bone, [])` does NOT create a bone, and none of the four passes
    that run after the jiggle graft strip weight, so the save is what drops it.
  - Pack-wide: 59 zero-weight bones over 42 shapes. On one affected shape, 29% of
    verts sit saturated at 4 influences, 0 verts at 5, and 7 verts carry a LIGHT sum
    (0.9975) -- the same cap firing without renormalisation.

Ships DEFAULT OFF: this is the path that skins every converted mesh, and pack-wide
weight rebalancing has not been play-tested."""
import importlib

import pytest

import src.nif_convert as nc


@pytest.fixture(autouse=True)
def _clean_module():
    yield
    import os
    os.environ.pop("CBBE2UBE_SKIN_INFLUENCE_CAP", None)
    importlib.reload(nc)


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CBBE2UBE_SKIN_INFLUENCE_CAP", raising=False)
    else:
        monkeypatch.setenv("CBBE2UBE_SKIN_INFLUENCE_CAP", value)
    return importlib.reload(nc)


# --- the flag ---------------------------------------------------------------

def test_defaults_off(monkeypatch):
    """Main conversion path for every mesh. Not enabled on offline numbers."""
    assert _reload(monkeypatch, None).SKIN_INFLUENCE_CAP_ENABLED is False


def test_opt_in(monkeypatch):
    assert _reload(monkeypatch, "1").SKIN_INFLUENCE_CAP_ENABLED is True


# --- what the cap does ------------------------------------------------------

def test_drops_the_smallest_influence_past_four():
    """The save would drop it anyway -- doing it here makes WHICH bone survives
    deterministic instead of a side effect of the writer."""
    wm = {
        "A": [(0, 0.40)], "B": [(0, 0.30)], "C": [(0, 0.20)],
        "D": [(0, 0.08)], "E": [(0, 0.02)],
    }
    out = nc._cap_weights_map(wm, 1)
    assert "E" not in out, "the 5th-largest influence must be dropped"
    assert set(out) == {"A", "B", "C", "D"}


def test_renormalises_the_survivors_to_one():
    """The save truncates WITHOUT renormalising, so the vertex ends up transformed
    by a deflated sum of its bone matrices. That is the defect, not the truncation."""
    wm = {"A": [(0, 0.40)], "B": [(0, 0.30)], "C": [(0, 0.20)],
          "D": [(0, 0.08)], "E": [(0, 0.02)]}
    out = nc._cap_weights_map(wm, 1)
    total = sum(pairs[0][1] for pairs in out.values())
    assert abs(total - 1.0) < 1e-9


def test_a_bone_emptied_by_the_cap_is_dropped_from_the_map():
    """This is the whole point: `_install_skin` derives its add_bone list from the
    map, so a bone the cap empties must not be in it. If it survived here it would
    be add_bone'd and then written nothing. #zeroweight-bone-desync"""
    wm = {"A": [(0, 0.5), (1, 0.5)], "B": [(0, 0.3), (1, 0.3)],
          "C": [(0, 0.2), (1, 0.2)], "D": [(0, 0.1), (1, 0.1)],
          "TINY": [(0, 0.001), (1, 0.001)]}
    out = nc._cap_weights_map(wm, 2)
    assert "TINY" not in out


def test_a_bone_that_survives_on_only_some_verts_is_kept():
    """Dropping a bone because it lost SOME verts would strip real skinning."""
    wm = {"A": [(0, 0.9), (1, 0.25)], "B": [(0, 0.04), (1, 0.25)],
          "C": [(0, 0.03), (1, 0.25)], "D": [(0, 0.02), (1, 0.25)],
          "E": [(0, 0.01)]}
    out = nc._cap_weights_map(wm, 2)
    assert "B" in out and any(v == 1 for v, _ in out["B"])


def test_untouched_when_already_within_the_limit():
    """No gratuitous rewriting of a mesh the cap has no business changing."""
    wm = {"A": [(0, 0.6)], "B": [(0, 0.4)]}
    out = nc._cap_weights_map(wm, 1)
    assert sorted(out) == ["A", "B"]
    assert abs(out["A"][0][1] - 0.6) < 1e-9
    assert abs(out["B"][0][1] - 0.4) < 1e-9


def test_unweighted_vertex_is_left_alone_not_zeroed():
    """An unweighted vert skins to the ORIGIN -- a visible spike. The shared row
    helper documents this guarantee; the map wrapper must not break it."""
    wm = {"A": [(0, 1.0)]}
    out = nc._cap_weights_map(wm, 3)     # verts 1 and 2 carry nothing
    assert out["A"] == [(0, 1.0)]


def test_bone_order_is_preserved():
    """`_install_skin` builds its add_bone list by iterating the caller's
    `bone_names`; a reordered map would shuffle which STB pairs with which bone."""
    wm = {"Z": [(0, 0.5)], "A": [(0, 0.3)], "M": [(0, 0.2)]}
    assert list(nc._cap_weights_map(wm, 1)) == ["Z", "A", "M"]


def test_empty_map_is_safe():
    assert nc._cap_weights_map({}, 4) == {}
    assert nc._cap_weights_map(None, 4) == {}


def test_out_of_range_vertex_indices_are_ignored():
    """A malformed pair must not raise inside the conversion."""
    wm = {"A": [(0, 1.0), (999, 0.5), (-3, 0.5)]}
    out = nc._cap_weights_map(wm, 2)
    assert out["A"] == [(0, 1.0)]


# --- how it is wired into _install_skin -------------------------------------

def test_cap_runs_before_the_add_bone_list_is_built():
    """Ordering IS the fix. Deriving `surviving` from pre-cap weights is exactly how
    a zero-weight bone reaches disk -- the same ordering error P6 found."""
    import inspect
    src = inspect.getsource(nc._install_skin)
    cap = src.index("_cap_weights_map(")
    surv = src.index("surviving = [bn for bn in bone_names")
    assert cap < surv


def test_authored_smp_skins_are_exempt():
    """Their zero-weight XML constraint bones are DELIBERATE -- dropping them
    collapses the skirt (#smp-constraint-bones-dropped). The cap must never see
    an authored skin."""
    import inspect
    src = inspect.getsource(nc._install_skin)
    i = src.index("_cap_weights_map(")
    gate = src[max(0, i - 200):i]
    assert "not preserve_authored_skin" in gate


def test_a_failure_cannot_break_the_conversion():
    """Weight hygiene is not worth failing a mesh over."""
    import inspect
    src = inspect.getsource(nc._install_skin)
    i = src.index("_cap_weights_map(")
    assert "except Exception:" in src[i:i + 200]
