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

"""#coherence-repair-outside-body -- the last pass may not undo the clearance.

`test_a_vert_is_not_pulled_through_the_skin` is the load-bearing one: it is the
defect, stated directly. The rest guard the contract that makes this safe to
turn on -- it is ONE-SIDED (it can only push a vertex back out, never further
in), it is INERT without a body, and it leaves alone every vertex the repair did
not move.

Why this pass and not the fit chain: traced with CBBE2UBE_STAGE_DUMP on the one
piece carrying every bust-band penetration the authored floors added, the
garment holds +0.9555 through anti-poke, panel-rigidity, chain-blend, min-push
and the seam weld, and only `coherence_repair` -- which takes no body -- moves it
to -0.0214. It does the same in the control arm (+1.0652 -> +0.2003), so the
floors do not create this; they spend the margin that was hiding it.
"""
import numpy as np
import pytest

from src import nif_convert as nc
from src import nif_convert_writer as w


# A flat body slab in z=0 with +z normals, and garment floating above it.
_XS = np.linspace(-4.0, 4.0, 9)
BODY = np.array([[x, y, 0.0] for x in _XS for y in _XS], dtype=float)
BODY_N = np.tile(np.array([0.0, 0.0, 1.0]), (len(BODY), 1))


@pytest.fixture(autouse=True)
def _armed(monkeypatch):
    monkeypatch.setattr(nc, "COHERENCE_REPAIR_OUTSIDE_BODY", True)


def _pair(before_z, after_z):
    """Two single-column vertex sets differing only in height."""
    b = np.array([[0.0, 0.0, z] for z in before_z], dtype=float)
    a = np.array([[0.0, 0.0, z] for z in after_z], dtype=float)
    return b, a


def _held(before_z, after_z):
    b, a = _pair(before_z, after_z)
    out = w._hold_repair_outside_body(b, a, BODY, BODY_N)
    return np.asarray(out)[:, 2]


def test_a_vert_is_not_pulled_through_the_skin():
    """THE DEFECT. The repair asks to move a vertex from clear of the body to
    behind it; the guard holds it at the surface instead."""
    z = _held([1.0], [-0.25])
    assert z[0] == pytest.approx(0.0, abs=1e-9)


def test_a_vert_already_inside_is_not_driven_deeper():
    """Where the repair inherits a vertex that is already inside, the guard may
    not make it worse -- but it must not silently 'fix' it either, because that
    would be this pass quietly doing the anti-poke's job."""
    z = _held([-0.30], [-0.80])
    assert z[0] == pytest.approx(-0.30, abs=1e-9)


def test_a_vert_already_inside_may_still_be_improved():
    z = _held([-0.30], [-0.10])
    assert z[0] == pytest.approx(-0.10, abs=1e-9)


def test_the_guard_is_one_sided():
    """It may only ever push a vertex OUT. A repair that moves a vertex further
    from the body is the pass doing its job and must come through untouched."""
    z = _held([0.50], [1.60])
    assert z[0] == pytest.approx(1.60, abs=1e-9)


def test_a_repair_that_keeps_clearance_is_untouched():
    z = _held([1.00], [0.40])
    assert z[0] == pytest.approx(0.40, abs=1e-9)


def test_only_moved_verts_are_considered():
    """A shape the repair did not touch must come back byte-identical, and must
    not pay for a tree query. A vertex sitting inside the body that the pass left
    alone is not this guard's business -- clamping it would change geometry no
    pass asked to change."""
    b, a = _pair([-0.90, 1.00], [-0.90, 0.40])
    out = np.asarray(w._hold_repair_outside_body(b, a, BODY, BODY_N))
    assert out[0][2] == pytest.approx(-0.90, abs=1e-9)
    assert out[1][2] == pytest.approx(0.40, abs=1e-9)


def test_an_untouched_shape_is_returned_unchanged():
    b, _a = _pair([1.0, 2.0], [1.0, 2.0])
    out = w._hold_repair_outside_body(b, b, BODY, BODY_N)
    assert out is b


def test_without_a_body_it_is_todays_behaviour():
    """`_copy_shape` has no body in scope, so the no-body path is a real one and
    must be exactly inert rather than a half-guard."""
    b, a = _pair([1.0], [-0.25])
    assert w._hold_repair_outside_body(b, a, None, None) is a
    assert w._hold_repair_outside_body(b, a, BODY, None) is a
    assert w._hold_repair_outside_body(b, a, None, BODY_N) is a


def test_off_by_default_and_the_flag_is_what_switches_it(monkeypatch):
    monkeypatch.setattr(nc, "COHERENCE_REPAIR_OUTSIDE_BODY", False)
    b, a = _pair([1.0], [-0.25])
    assert w._hold_repair_outside_body(b, a, BODY, BODY_N) is a


def test_the_default_is_off():
    """Resolve the default from the CODE -- a default written anywhere else is a
    dated claim."""
    from src.envflags import flag
    assert flag("CBBE2UBE_COHERENCE_REPAIR_OUTSIDE_BODY", False) is False


def test_it_fails_soft_on_a_malformed_body():
    b, a = _pair([1.0], [-0.25])
    assert w._hold_repair_outside_body(b, a, BODY[:3], BODY_N) is a
    assert w._hold_repair_outside_body(b, a, np.zeros((0, 3)),
                                       np.zeros((0, 3))) is a


def test_the_repair_still_takes_the_body_at_the_traced_call_site():
    """The behavioural tests above would all pass just as well if the call site
    never handed the pass a body -- which is exactly how this defect survived.
    Pin the wiring at the site the stage dump named."""
    import inspect
    src = inspect.getsource(nc._fit_shapes_swap)
    i = src.index("_repair_coherence_collapse(")
    call = src[i:i + 320]
    assert "body_verts=" in call and "body_normals=" in call, (
        "the traced call site no longer hands the repair a body:\n" + call)


def test_the_repair_signature_still_accepts_a_body():
    import inspect
    sig = inspect.signature(w._repair_coherence_collapse)
    assert "body_verts" in sig.parameters
    assert "body_normals" in sig.parameters
    # Keyword-only, so a positional caller cannot silently pass tris as a body.
    assert sig.parameters["body_verts"].kind is inspect.Parameter.KEYWORD_ONLY
