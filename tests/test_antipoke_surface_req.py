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

"""`#antipoke-surface-req` -- the THIRD site of the vertex-vs-surface gap.

WHY IT EXISTS. `#bust-surface-req` closed the gap in `conform`, and
`#panel-rigid-surface-guard` in panel rigidity. The ANTI-POKE -- the pass whose
whole job is "no body through the garment" -- still asks per VERTEX. On the
reported cuirass (BUG-16) the WRITTEN mesh has essentially no garment vertex
inside the body while 556 BODY verts escape through it: every vertex is lifted
clear and the flat triangle between them still cuts across the nipple.

WHAT IS PINNED HERE:
  * DEFAULT OFF, resolved in a SCRUBBED subprocess, never from this process's
    already-imported module;
  * it FIRES on the defect it was built for -- a surface sagging under corners
    that all pass the vertex test;
  * it DISCRIMINATES -- zero movement when the surface is already clear. That
    is the bar three earlier attempts in this band failed, and it is what makes
    the term zone-selective rather than a standoff ramp;
  * the surface is held to its OWN low bar, not to `req`: holding it to `req`
    would lift the spanning vertices PAST `req` and add standoff, on a pack
    whose reported in-game defect is already "sits too far off the body";
  * it is INERT without `tris` and inert without a bust mask -- and the copy
    path passes no `body_nipple`, so it is inert there BY CONSTRUCTION. A
    copy-path zero is "did not run", never "measured no defect".

MEASURED 2026-09-09, AND THE REASON IT IS OFF. Armed on the reported piece it
fires (15 verts raised, max +0.5u, over 495 bust verts) and the WRITTEN NIF is
UNCHANGED -- 0 of 8867 verts moved on the shape carrying 100% of the clip area.
`DisplacementSurvival` names the absorber: the anti-poke's own displacement
survives at 0.7279 with 13.1% of its verts fully cancelled, `cancelled_by
panel_rigidity_post -0.2359`, and the surface verts land inside that 13.1%.
So the anti-poke is the WRONG SITE while a re-rigidification runs after it --
the `#pass-damage-ledger` oscillation, now with a number attached.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# `clear_armor_outside_body` resolves the monolith at CALL time through
# `_nc()`, which is `sys.modules["src.nif_convert"]`. Importing only fitgeom
# leaves that key absent and every call raises KeyError -- so the import below
# is load-bearing, not tidiness.
from src import nif_convert            # noqa: E402,F401
from src import nif_convert_fitgeom as fg          # noqa: E402


def _resolve(expr: str, extra_env=None) -> str:
    """Resolve an expression in a subprocess with every CBBE2UBE_* removed.

    Reading `fg.ANTIPOKE_SURFACE_REQ` in THIS process reports whatever the
    developer's shell happened to export, which is exactly how a default gets
    pinned to an override.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    env.update(extra_env or {})
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from src import nif_convert_fitgeom as fg
        print(repr(%s))
    """ % (str(REPO_ROOT), expr))
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_default_is_off_resolved_from_a_scrubbed_subprocess():
    assert _resolve("fg.ANTIPOKE_SURFACE_REQ") == "False"


def test_the_knobs_are_env_overridable_not_bare_literals():
    """`BUST_SURFACE_MAX_PUSH` is a PLAIN LITERAL, and one arm run by setting
    `CBBE2UBE_BUST_SURFACE_MAX_PUSH` was silently VOID rather than a negative
    result. These two must not repeat it."""
    assert _resolve("fg.ANTIPOKE_SURFACE_CLEAR") == "0.1"
    assert _resolve("fg.ANTIPOKE_SURFACE_MAX_PUSH") == "0.5"
    got = _resolve("fg.ANTIPOKE_SURFACE_CLEAR",
                   {"CBBE2UBE_ANTIPOKE_SURFACE_CLEAR": "0.37"})
    assert got == "0.37", (
        "the knob ignored its env var -- it is a bare literal, and any arm set "
        "with it would be VOID rather than a negative result")


def _one_triangle(apex_y):
    """One BIG garment triangle with a body point in its INTERIOR.

    The corners stand 2.5u clear, and their nearest body verts sit 9u from the
    apex -- beyond the pass's own `radius=4.0` neighbourhood -- so the VERTEX
    test is satisfied and only a SURFACE test can see the apex. That is the
    geometry BUG-16 measured, reduced to its smallest form.
    """
    v = np.array([[-8.0, 2.5, 90.0], [8.0, 2.5, 90.0], [0.0, 2.5, 104.0]])
    tris = np.array([[0, 1, 2]], dtype=np.int64)
    bv = np.array([[-8.0, 0.0, 90.0], [8.0, 0.0, 90.0], [0.0, 0.0, 104.0],
                   [0.0, apex_y, 95.0]])
    bn = np.tile(np.array([[0.0, 1.0, 0.0]]), (len(bv), 1))
    nip = np.ones(len(bv))
    return v, tris, bv, bn, nip


def _run(apex_y, on, monkeypatch, **kw):
    v, tris, bv, bn, nip = _one_triangle(apex_y)
    monkeypatch.setattr(fg, "ANTIPOKE_SURFACE_REQ", on)
    out = fg.clear_armor_outside_body(v, bv, bn, body_nipple=nip, tris=tris,
                                      **kw)
    return np.asarray(out, np.float64) - v


def test_off_moves_nothing_on_the_defect_geometry(monkeypatch):
    """THE NO-OP CONTROL. Every vertex already clears `req`, so the host pass
    must do nothing at all with the term off -- otherwise the ON arm below is
    measuring the host pass rather than this term."""
    off = _run(2.6, False, monkeypatch)
    assert np.abs(off).max() < 1e-9


def test_on_fires_where_the_body_comes_through_the_surface(monkeypatch):
    off = _run(2.6, False, monkeypatch)
    on = _run(2.6, True, monkeypatch)
    moved = int((np.linalg.norm(on - off, axis=1) > 1e-9).sum())
    assert moved == 3, (
        "expected the offending triangle's own 3 corners, got %d" % moved)
    # deficit = CLEAR - clearance = 0.1 - (-0.1): the body sits 0.1u PROUD of
    # the surface, so the term is exactly 0.2u.
    assert np.isclose(np.abs(on).max(), 0.2, atol=1e-6)


def test_on_moves_NOTHING_when_the_surface_is_already_clear(monkeypatch):
    """THE DISCRIMINATION TEST. A term that fires everywhere is a standoff
    ramp, and a global ramp is already measured to make clipping WORSE under
    morph. This is what makes the term zone-selective."""
    on = _run(0.0, True, monkeypatch)
    assert np.abs(on).max() < 1e-9


def test_it_is_inert_without_topology(monkeypatch):
    """`tris` reaches this pass only when ANTIPOKE_SMOOTH or CLEARANCE_FIELD is
    on. Without them the term cannot run, and it must not raise trying."""
    v, _tris, bv, bn, nip = _one_triangle(2.6)
    monkeypatch.setattr(fg, "ANTIPOKE_SURFACE_REQ", True)
    out = fg.clear_armor_outside_body(v, bv, bn, body_nipple=nip, tris=None)
    assert np.abs(np.asarray(out, np.float64) - v).max() < 1e-9


def test_it_is_inert_without_a_bust_mask_which_is_the_copy_path(monkeypatch):
    """The phase-1 (copy-path) call site passes NO `body_nipple`, so `_in_bust`
    is never assigned there and this term cannot fire. Pinned so a copy-path
    zero is read as "did not run", never as "measured no defect"."""
    v, tris, bv, bn, _nip = _one_triangle(2.6)
    monkeypatch.setattr(fg, "ANTIPOKE_SURFACE_REQ", True)
    out = fg.clear_armor_outside_body(v, bv, bn, body_nipple=None, tris=tris)
    assert np.abs(np.asarray(out, np.float64) - v).max() < 1e-9


def test_the_surface_bar_does_not_track_the_vertex_requirement(monkeypatch):
    """THE ANTI-STANDOFF PROPERTY, asserted rather than left in prose.

    `req` on a bust vertex runs ~1.15u. Held to `req`, the surface test would
    lift the corners spanning it PAST `req` and act as a standoff ramp -- the
    thing the in-game report complains about. Held to its own 0.1u bar the push
    is bounded by the SAG, so raising the VERTEX requirement cannot bleed into
    this term.
    """
    on_default = _run(2.6, True, monkeypatch)
    on_big_req = _run(2.6, True, monkeypatch, flat_clear=3.0, bust_clear=3.0)
    assert np.abs(on_default).max() <= fg.ANTIPOKE_SURFACE_MAX_PUSH + 1e-9
    # ... and the control must be able to tell the two apart at all.
    assert np.abs(on_big_req).max() > np.abs(on_default).max(), (
        "tripling the VERTEX requirement should still move the host pass; if "
        "it does not, this control is not exercising what it claims to")


def test_the_firing_assertion_can_fail(monkeypatch):
    """MUTATION CONTROL. Neutralise the term and the firing test must break --
    otherwise it is agreeing with the host pass instead of measuring this
    one."""
    monkeypatch.setattr(fg, "ANTIPOKE_SURFACE_CLEAR", -99.0)
    on = _run(2.6, True, monkeypatch)
    assert np.abs(on).max() < 1e-9, (
        "with the surface bar driven below every reachable clearance the term "
        "must go silent; it did not, so the firing test proves nothing")
