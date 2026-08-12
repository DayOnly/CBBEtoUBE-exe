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

"""#family-weight-invariant -- the family-match write must renormalise over the
influences that will actually SURVIVE the write, not over the ones it wants.

A vertex holds four bones. The write is bone-by-bone and `setShapeWeights`
merges, so each write is arbitrated against a row that is still part stale: a
bone the pass wants to ADD arrives while the larger old values of the bones it
is replacing are still present, loses the four-way contest, and is gone before
those are lowered. The row ships missing that weight while the bones that did
land were scaled as a share of a total that counted it.

`test_keeping_the_largest_four_reproduces_the_bug` is the load-bearing one: it
pins the distinction between this fix and the obvious wrong one. Keeping the
four largest is the rule the SAVE applies, and applying it here reproduces the
defect exactly, because the largest four can include a newcomer the vertex has
no room for.
"""
import numpy as np

from src import nif_convert as nc

_WM = nc._WRITE_MIN
_MAX = nc._SKIN_MAX_INFLUENCES


def _survivors(G, NEW):
    """The fix, lifted from the pass: keep what the vertex HAS, spend free
    slots on the strongest newcomers, renormalise over that set."""
    sub = NEW.copy()
    have = G > _WM
    free = _MAX - have.sum(axis=1)
    newb = (~have) & (sub > _WM)
    order = np.argsort(-np.where(newb, sub, -np.inf), axis=1)
    rank = np.empty_like(order)
    np.put_along_axis(
        rank, order, np.broadcast_to(np.arange(sub.shape[1]), sub.shape), 1)
    allow = have | (newb & (rank < np.maximum(free, 0)[:, None]))
    sub = np.where(allow, sub, 0.0)
    ss = sub.sum(axis=1)
    ok = ss > 1e-6
    sub[ok] /= ss[ok, None]
    sub[~ok] = NEW[~ok]
    return sub


def _largest_four(NEW):
    """The WRONG fix, for the comparison below."""
    out = NEW.copy()
    keep = np.argsort(out, axis=1)[:, -_MAX:]
    kept = np.take_along_axis(out, keep, axis=1)
    ks = kept.sum(axis=1)
    np.put_along_axis(out, keep, kept / np.maximum(ks[:, None], 1e-12), axis=1)
    return out


def _ships(G, row):
    """What the merge-and-arbitrate write actually lands: a vertex that is
    already full cannot take a newcomer, so the newcomer's weight is lost."""
    have = G > _WM
    free = _MAX - have.sum()
    order = np.argsort(-np.where((~have) & (row > _WM), row, -np.inf))
    landed = have.copy()
    for j in order[:max(free, 0)]:
        if row[j] > _WM:
            landed[j] = True
    return float(row[landed].sum())


# The traced case: `top` vertex 1561. Columns are
# [RClv, RUar, RUt1, Spn2, Breast01]. The vertex HAS four bones -- RUar is not
# one of them -- and the pass hands RUar 0.1400 out of a perfect 1.0 row.
G = np.array([[0.3637, 0.0000, 0.1960, 0.2832, 0.1571]])
NEW = np.array([[0.3293, 0.1400, 0.1763, 0.3542, 0.0002]])


def test_the_defect_is_real_without_a_fix():
    """NEGATIVE CONTROL, and it reproduces the measured number: the vertex
    shipped at 0.8601."""
    assert abs(NEW.sum() - 1.0) < 1e-3          # the row itself is perfect
    assert abs(_ships(G[0], NEW[0]) - 0.8601) < 1e-3


def test_keeping_the_largest_four_reproduces_the_bug():
    """The obvious fix is the save's own rule, and it does NOT help: RUar is
    the 4th largest, so it survives the cap and is then dropped by the write
    anyway."""
    assert abs(_ships(G[0], _largest_four(NEW)[0]) - 1.0) > 0.1


def test_the_fix_ships_a_row_summing_to_one():
    assert abs(_ships(G[0], _survivors(G, NEW)[0]) - 1.0) < 1e-6


def test_the_fix_does_not_invent_an_influence():
    """Only bones the vertex already had may carry weight when it is full."""
    out = _survivors(G, NEW)[0]
    assert out[1] == 0.0                        # RUar, the newcomer
    assert (out[G[0] > _WM] > 0).all()


def test_a_free_slot_is_spent_on_the_strongest_newcomer():
    """A vertex with room SHOULD take a new bone -- this must not become a
    blanket ban on ever gaining an influence."""
    g = np.array([[0.6, 0.0, 0.4, 0.0, 0.0]])          # two influences, two free
    new = np.array([[0.4, 0.3, 0.2, 0.1, 0.05]])       # three newcomers
    out = _survivors(g, new)[0]
    assert out[1] > 0 and out[3] > 0                   # the two strongest
    assert out[4] == 0.0                               # the weakest, no slot
    assert abs(_ships(g[0], out) - 1.0) < 1e-6


def test_unweighted_row_stays_unweighted():
    """Scaling an all-zero row would skin the vertex to the origin."""
    z = np.zeros((1, 5))
    assert np.array_equal(_survivors(z, z), z)


def test_row_within_the_cap_is_left_alone():
    """A vertex whose target needs no new slot must ship untouched -- this pass
    is not a licence to renormalise rows nobody complained about."""
    g = np.array([[0.5, 0.0, 0.3, 0.2, 0.0]])
    new = np.array([[0.4, 0.0, 0.4, 0.2, 0.0]])
    assert np.allclose(_survivors(g, new), new)


def test_off_by_default():
    import os
    assert nc.FAMILY_WEIGHT_INVARIANT is False or (
        os.environ.get("CBBE2UBE_FAMILY_WEIGHT_INVARIANT") == "1")


def test_reachable_from_the_gui():
    from src import gui_settings
    assert any(s.env == "CBBE2UBE_FAMILY_WEIGHT_INVARIANT"
               for s in gui_settings.SETTINGS)
