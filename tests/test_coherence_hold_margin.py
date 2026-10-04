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


"""#coherence-hold-margin -- the clearance hold keeps a margin, not just "not past zero".

`_hold_repair_outside_body` used to floor each vertex at min(its clearance before
the repair, 0): it could not be pulled past the skin, but it could be smoothed
onto the surface and ship exactly on it (z-fight). The floor is now
min(its own clearance before, `COHERENCE_HOLD_MARGIN`): a vertex that started at
least the margin out stays at least that far out, and one that started closer is
held to where it was. Measured on 70 torso pieces, margin 0.2: vertices within
0.05 of the skin 3798 -> 3051 against 3109 on plain testing. 0 is the old rule.
"""
import numpy as np
import pytest

from src import nif_convert as nc
from src import nif_convert_writer as w

N = 8
XS, YS = np.meshgrid(np.arange(N, dtype=float), np.arange(N, dtype=float))
BODY = np.c_[XS.ravel(), YS.ravel(), np.zeros(N * N)]
BODY_N = np.tile([0.0, 0.0, 1.0], (N * N, 1))
V = N * 3 + 3                                  # the vertex the cases move


def _held(before_z, after_z, margin, monkeypatch):
    monkeypatch.setattr(nc, "COHERENCE_REPAIR_OUTSIDE_BODY", True)
    monkeypatch.setattr(nc, "COHERENCE_HOLD_MARGIN", margin)
    b = BODY + [0.0, 0.0, 1.0]
    a = b.copy()
    b[V, 2], a[V, 2] = before_z, after_z
    return float(np.asarray(w._hold_repair_outside_body(b, a, BODY, BODY_N))[V, 2])


def test_the_default_margin_is_a_fifth_of_a_unit():
    assert nc.COHERENCE_HOLD_MARGIN == pytest.approx(0.2)


def test_a_vert_that_started_clear_is_not_smoothed_onto_the_skin(monkeypatch):
    # 0.5 out before, the repair puts it 0.05 out: held back to the margin
    assert _held(0.5, 0.05, 0.2, monkeypatch) == pytest.approx(0.2)


def test_the_old_rule_lets_it_land_on_the_skin(monkeypatch):
    assert _held(0.5, 0.0, 0.0, monkeypatch) == pytest.approx(0.0)
    assert _held(0.5, 0.05, 0.0, monkeypatch) == pytest.approx(0.05)


def test_a_vert_that_started_closer_than_the_margin_is_held_to_where_it_was(monkeypatch):
    assert _held(0.1, 0.02, 0.2, monkeypatch) == pytest.approx(0.1)


def test_a_vert_the_repair_moves_outward_is_untouched(monkeypatch):
    assert _held(0.5, 0.9, 0.2, monkeypatch) == pytest.approx(0.9)


def test_a_vert_already_inside_is_not_driven_deeper(monkeypatch):
    assert _held(-0.3, -1.0, 0.2, monkeypatch) == pytest.approx(-0.3)
