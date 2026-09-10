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

"""A GREEN SUITE MUST MEAN THE SUITE RAN.

Seven test files carry a module-level
`pytestmark = pytest.mark.skipif(not pynifly_available(), ...)`, gating 35
integration tests -- the ones that actually build a NIF and exercise
`_copy_shape`, the cloth colliders and the HDT-XML consistency path. They are
the only tests that touch real mesh IO.

`pynifly_available()` is a bare try/except around loading the native library,
so ANY failure answers False: a missing DLL, a Python version bump, a broken
install, a moved `.pynifly` directory. When that happens those 35 tests skip
silently and the run still reports success. The most valuable third of the
suite can disappear without a single red character, which is the same failure
this project keeps meeting elsewhere -- a measurement that stays quiet about
what it could not measure.

This test turns that silent erosion into a visible decision. If pynifly cannot
load, the suite FAILS here and says why, unless the operator explicitly opts
out with CBBE2UBE_ALLOW_NO_PYNIFLY=1 (for a docs-only or lint-only run).
"""
import os

import pytest

from tests.synthetic_nif import pynifly_available

OPT_OUT = "CBBE2UBE_ALLOW_NO_PYNIFLY"


def test_the_integration_tests_are_actually_running():
    if os.environ.get(OPT_OUT, "").strip().lower() in ("1", "true", "yes"):
        pytest.skip(f"{OPT_OUT} set: integration coverage waived on purpose")
    assert pynifly_available(), (
        "pynifly will not load, so the 7 integration test files that build "
        "real NIFs are SKIPPING and the rest of the suite is passing without "
        "them. Fix the pynifly install, or set "
        f"{OPT_OUT}=1 to state on purpose that this run has no mesh coverage.")


def test_the_gate_can_be_waived_deliberately(monkeypatch):
    """NEGATIVE CONTROL for the opt-out: prove the escape hatch is reachable,
    so a CI runner that genuinely cannot load the native library has a way
    through that is a decision rather than a silent skip."""
    monkeypatch.setenv(OPT_OUT, "1")
    assert os.environ.get(OPT_OUT) == "1"
