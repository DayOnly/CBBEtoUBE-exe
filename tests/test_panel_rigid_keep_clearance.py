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

"""`#panel-rigid-keep-clearance` -- stop the re-rigidification spending the
clearance the anti-poke just bought.

THE MEASURED CASE. `_rigidify_within_clearance` guards on
`floor = min(clear_of(Q), 0.0) - 1e-4`, so a vertex standing 0.5u clear may be
pulled to the skin and still pass: the contract is only "nothing NEWLY enters
the body". On the reported cuirass's bust band (477 verts, stage dump):

    s07_antipoke              p50 1.8141   p05 1.1654   min 0.6719
    s08_panel_rigidity_post   p50 1.7581   p05 0.3930   min 0.0007

The anti-poke adds 238.32 u-verts of clearance; the very next pass spends 94.42
of them (40%), over 70% of the band, and lands the minimum at 0.0007u.

WHAT IS PINNED HERE:
  * DEFAULT 0.0, resolved in a SCRUBBED subprocess;
  * **at 0.0 the floor is ARITHMETICALLY the old one** -- `where(c > 0, c*0, c)`
    IS `minimum(c, 0)`. The OFF path is unchanged by construction, not by a
    measurement someone has to trust;
  * raising it only ever REFUSES rigidification, never pushes. That is what
    keeps this in the class this codebase has found safe, and out of the class
    of `#panel-rigid-bust-hold`, which pushed, broke a clean preset
    0.000 -> 0.632, and was REMOVED;
  * the floor is raised on the SURFACE samples too, not just the vertices --
    fixing one and leaving the other is the `#body-name-prefix` failure mode,
    where the unfixed site silently kept the old behaviour.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import nif_convert            # noqa: E402,F401  (fitgeom's `_nc()`)
from src import nif_convert_fitgeom as fg          # noqa: E402

SRC = (REPO / "src" / "nif_convert_fitgeom.py").read_text(encoding="utf-8")


def _resolve(expr, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    env.update(extra_env or {})
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from src import nif_convert as nc
        print(repr(%s))
    """ % (str(REPO), expr))
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_default_is_zero_from_a_scrubbed_subprocess():
    assert _resolve("nc.PANEL_RIGID_KEEP_CLEARANCE") == "0.0"


def test_it_is_a_knob_not_a_bare_literal():
    """`BUST_SURFACE_MAX_PUSH` is a plain literal and an arm set through its
    env name was VOID rather than a negative. This must not repeat it."""
    got = _resolve("nc.PANEL_RIGID_KEEP_CLEARANCE",
                   {"CBBE2UBE_PANEL_RIGID_KEEP_CLEARANCE": "0.5"})
    assert got == "0.5", "the knob ignored its env var -- any arm set with it "\
                         "would be VOID, not a negative result"


def test_at_zero_the_floor_is_arithmetically_the_old_one():
    """THE SAFETY PROPERTY THE WHOLE DESIGN RESTS ON. Covers negative, zero and
    positive clearance, which is the full domain of `clear_of`."""
    c = np.array([-2.0, -0.3, -1e-9, 0.0, 1e-9, 0.3, 2.0, 1e3])
    old = np.minimum(c, 0.0) - 1e-4
    new = np.where(c > 0.0, c * 0.0, c) - 1e-4
    assert np.array_equal(old, new)


def test_the_floor_is_raised_on_the_surface_samples_too():
    """Two call sites compute this floor -- the vertex test and the
    triangle-interior samples. Fixing one and leaving the other is exactly how
    `#body-name-prefix` stayed broken after it was 'fixed'."""
    body = SRC[SRC.index("def _rigidify_within_clearance"):]
    body = body[:body.index("\ndef ", 10)]
    assert body.count("_kc") >= 3, (
        "the keep factor is not applied at both floors (vertex + samples)")
    assert "floor_s = np.where(_cs > 0.0, _cs * _kc, _cs) - 1e-4" in body, (
        "the SURFACE-sample floor no longer honours the keep factor")


def _panel(n=6):
    """A welded grid panel bulged over a flat body, so rigidifying it SINKS the
    apex -- the exact motion this guard exists to bound."""
    xs = np.linspace(-5.0, 5.0, n)
    zs = np.linspace(90.0, 100.0, n)
    gx, gz = np.meshgrid(xs, zs, indexing="ij")
    flat = np.stack([gx.ravel(), np.ones(gx.size), gz.ravel()], axis=1)
    cur = flat.copy()
    cur[:, 1] = 1.0 + 1.0 * np.exp(-(cur[:, 0] ** 2) / 4.0)   # a bump
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b = i * n + j, i * n + j + 1
            c, d = (i + 1) * n + j, (i + 1) * n + j + 1
            tris += [[a, b, c], [b, d, c]]
    bx, bz = np.meshgrid(np.linspace(-6, 6, 12), np.linspace(89, 101, 12),
                         indexing="ij")
    bodyv = np.stack([bx.ravel(), np.zeros(bx.size), bz.ravel()], axis=1)
    bodyn = np.tile(np.array([[0.0, 1.0, 0.0]]), (len(bodyv), 1))
    return flat, cur, np.asarray(tris, np.int64), bodyv, bodyn


def _run(keep, monkeypatch):
    src, cur, tris, bv, bn = _panel()
    monkeypatch.setattr(nif_convert, "PANEL_RIGID_KEEP_CLEARANCE", keep)
    out, touched, used = fg._rigidify_within_clearance(
        src, cur, tris, bv, bn, 0.75, min_verts=12)
    return np.asarray(out, np.float64), touched, used


def test_the_control_actually_rigidifies_at_zero(monkeypatch):
    """0/0 IS NOT A PASS: if the pass does nothing here, the comparison below
    is between two no-ops."""
    out, touched, used = _run(0.0, monkeypatch)
    _src, cur, *_ = _panel()
    assert touched >= 1, "the pass did not fire -- the fixture is not exercising it"
    assert np.abs(out - cur).max() > 1e-3, "no vertex moved at keep=0.0"


def test_raising_keep_only_ever_REFUSES(monkeypatch):
    """It must never rigidify MORE. Monotone by construction, asserted anyway --
    a guard that could push would be the `#panel-rigid-bust-hold` class."""
    _src, cur, *_ = _panel()
    prev = None
    for keep in (0.0, 0.25, 0.5, 0.75, 1.0):
        out, _t, _u = _run(keep, monkeypatch)
        moved = float(np.abs(out - cur).max())
        if prev is not None:
            assert moved <= prev + 1e-9, (
                "keep=%s rigidified MORE than the looser setting -- this guard "
                "must only ever refuse" % keep)
        prev = moved


def test_every_vertex_keeps_at_least_the_requested_fraction(monkeypatch):
    """THE CONTRACT ITSELF, asserted directly rather than through a fixture's
    happening to bind. Over this flat body, clearance IS the y coordinate, so
    the retained fraction is `out_y / cur_y` per vertex.

    A first version of this test asserted that keep=0.75 must move the worst
    vertex; it does not, and the code was right -- this panel retains 79.6% at
    full strength, so 0.75 is legitimately feasible. The invariant is not "a
    high keep always bites", it is "whatever fires respects the fraction".
    """
    _src, cur, *_ = _panel()
    fired = 0
    for keep in (0.0, 0.25, 0.5, 0.75, 0.9):
        out, touched, _u = _run(keep, monkeypatch)
        if not touched:
            continue
        fired += 1
        retained = (out[:, 1] / cur[:, 1]).min()
        assert retained >= keep - 1e-6, (
            "keep=%s but a vertex retained only %.4f of its clearance"
            % (keep, retained))
    assert fired >= 3, "0/0 IS NOT A PASS -- the pass fired %d times" % fired


def test_keep_one_refuses_the_pass_entirely(monkeypatch):
    """The far end of the knob: conceding NOTHING means no rigidification is
    feasible on a panel whose fit costs any clearance at all. This is the
    clearest demonstration that the floor is what gates the bisection."""
    out, touched, used = _run(1.0, monkeypatch)
    _src, cur, *_ = _panel()
    assert touched == 0 and used == 0.0
    assert np.abs(out - cur).max() < 1e-12, "keep=1.0 still moved a vertex"
