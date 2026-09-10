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

"""#panel-rigid-early-clearance: the early rigidifier must not enter the body.

`_partial_rigid_panels` is body-blind by construction -- it takes no body
argument -- so it can drag a plate under the skin and relies on the anti-poke
downstream to push it clear. That coupling is what stops the anti-poke from ever
being tightened to respect the author's fit, so this flag swaps in the
clearance-solved form at the SAME site.

These tests pin the property that matters (no vertex NEWLY inside the body) and
the two ways the swap could go wrong quietly: reporting panels it did not move,
and diverging between the two convert paths.
"""
import numpy as np
import pytest

from src import nif_convert as nc


def _slab():
    """Body patch at z=0 (+z outward), a flat source panel, and a CURRENT state
    whose best rigid fit would drive one end under the skin.

    The construction matters: an arbitrary deformation does NOT make the blind
    form penetrate, and a test where it doesn't asserts nothing. So this
    reproduces the real situation -- a panel that was TILTED by the fit chain and
    then had its low end pushed clear of the body. Restoring the panel's own
    rigid motion puts that end back where it was: inside.
    """
    gx, gy = np.meshgrid(np.linspace(-8, 8, 17), np.linspace(-8, 8, 17))
    body = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    bn = np.tile(np.array([0.0, 0.0, 1.0]), (len(body), 1))

    n = 7
    px, py = np.meshgrid(np.linspace(-3, 3, n), np.linspace(-3, 3, n))
    src = np.column_stack([px.ravel(), py.ravel(), np.full(px.size, 0.5)])

    th = np.deg2rad(20.0)
    rot = np.array([[np.cos(th), 0.0, np.sin(th)],
                    [0.0, 1.0, 0.0],
                    [-np.sin(th), 0.0, np.cos(th)]])
    tilted = (rot @ (src - src.mean(0)).T).T + src.mean(0)
    # ...and then the anti-poke lifted whatever that put under the skin.
    cur = tilted.copy()
    cur[:, 2] = np.maximum(cur[:, 2], 0.05)

    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b = i * n + j, i * n + j + 1
            c, d = (i + 1) * n + j, (i + 1) * n + j + 1
            tris += [[a, b, c], [b, d, c]]
    return body, bn, src, cur, np.asarray(tris, dtype=np.int64)


def test_the_blind_form_really_does_penetrate():
    """THE CONTROL. Without this the test below can pass while asserting nothing.

    A first version of these fixtures produced a scenario in which the blind form
    never entered the body, so "the solved form stays out" was vacuously true.
    """
    body, bn, src, cur, tris = _slab()
    blind, n, _ = nc._partial_rigid_panels(src, cur, tris, 1.0, min_verts=12)
    assert n == 1, "the panel was not found -- fixture is inert"
    floor = np.minimum(cur[:, 2], 0.0) - 1e-4
    assert int((np.asarray(blind)[:, 2] < floor).sum()) > 0, (
        "the blind form did not penetrate, so the comparison below proves "
        "nothing -- fix the fixture, not the assertion")


def test_clearance_form_keeps_the_panel_out_of_the_body():
    body, bn, src, cur, tris = _slab()
    solved, n, used = nc._rigidify_within_clearance(
        src, cur, tris, body, bn, 1.0, min_verts=12)
    floor = np.minimum(cur[:, 2], 0.0) - 1e-4
    assert int((np.asarray(solved)[:, 2] < floor).sum()) == 0
    # It must still do SOME rigidifying where there is room, or "no penetration"
    # is just "the pass was disabled".
    assert n == 1 and used > 0.0


def test_flag_is_off_by_default_and_reachable():
    """An opt-in nobody can reach is a fix that does not ship."""
    assert nc.PANEL_RIGID_EARLY_CLEAR is False
    from src import gui_settings
    rows = [s for s in gui_settings.SETTINGS
            if s.env == "CBBE2UBE_PANEL_RIGID_EARLY_CLEAR"]
    assert len(rows) == 1, "flag must have exactly one GUI row"
    assert rows[0].default is False


def test_wired_into_both_convert_paths():
    """A fit flag that reaches one path splits the pack.

    `#panel-rigidity` shipped a release reaching only 26% of files and every
    verdict taken in that window was untransferable. Guard the shape of the fix
    rather than trusting it stayed wired.
    """
    import inspect
    src = inspect.getsource(nc)
    guarded = src.count("PANEL_RIGID_EARLY_CLEAR\n") + src.count(
        "PANEL_RIGID_EARLY_CLEAR ")
    assert guarded >= 3, (
        "expected the constant plus a guard on each convert path; "
        f"found {guarded} occurrence(s)")
    # Both guards must hand the body in -- the whole point of the swap.
    assert "body_verts_for_fit,\n" in src or "body_verts_for_fit," in src
    assert "body_verts_for_p2, body_norms_for_p2," in src


@pytest.mark.parametrize("strength", [0.0, -1.0])
def test_zero_strength_is_a_no_op(strength):
    body, bn, src, cur, tris = _slab()
    out, n, _ = nc._rigidify_within_clearance(
        src, cur, tris, body, bn, strength, min_verts=12)
    assert n == 0
    assert np.allclose(np.asarray(out), cur)
