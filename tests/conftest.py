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

"""Shared pytest setup.

SkyPatcher (armorAddonsToAdd) is the ONLY armor-delivery path -- the legacy
ESP-override machinery was removed once SkyPatcher was proven in-game. The suite
runs the default (SkyPatcher) path everywhere; nothing pins an escape hatch.
"""
import importlib
import time

import pytest

from tests import _module_guard

# Loaded up front so the guard below watches them from the first test on; a
# module first imported INSIDE a test is watched from the next test.
for _name in ("src.nif_convert", "src.auto_convert", "src.ube_patcher",
              "src.paths", "scripts.pass_map"):
    importlib.import_module(_name)
from scripts import pass_map as _pass_map  # noqa: E402
for _rel in _pass_map.CONVERTER_MODULES:
    importlib.import_module("src." + _rel[len("src/"):-len(".py")])

_GUARD_SECONDS = [0.0, 0]


@pytest.fixture(autouse=True)
def _converter_modules_are_left_as_found():
    """FAIL the test that leaves a `src.*` / `scripts.*` module rebound.

    Patch converter modules with `monkeypatch` (or `_converter_sources.patch`
    for a name the split siblings share), never by bare assignment: a fake
    that outlives its test decides whether LATER tests pass. The guard puts
    the module back first, so one leak cannot cascade, then names each
    `module.attr` it had to restore. See tests/_module_guard.py for what
    counts as a legitimate rebinding. This fixture's name sorts before every
    other autouse fixture here, so it is set up first and torn down last --
    after `monkeypatch` has undone its own patches."""
    t0 = time.perf_counter()
    snaps = _module_guard.snapshot()
    _GUARD_SECONDS[0] += time.perf_counter() - t0
    yield
    t0 = time.perf_counter()
    leaks = _module_guard.settle(snaps)
    _GUARD_SECONDS[0] += time.perf_counter() - t0
    _GUARD_SECONDS[1] += 1
    if leaks:
        pytest.fail("this test left converter modules changed for the tests "
                    "after it (restored now; patch with monkeypatch):\n  "
                    + "\n  ".join(leaks), pytrace=False)


def pytest_terminal_summary(terminalreporter):
    if _GUARD_SECONDS[1]:
        terminalreporter.write_line(
            f"module guard: {_GUARD_SECONDS[1]} tests, "
            f"{_GUARD_SECONDS[0]:.2f} s snapshot+compare")


@pytest.fixture(autouse=True)
def _zeroed_body_never_reads_the_host_instance(monkeypatch):
    """The converter's body resolvers now try the zeroed-body resolver first,
    which discovers the MO2 instance. A test must not depend on the machine
    it runs on: with CBBE2UBE_MO2_INI set, unrelated tests would resolve that
    modlist's real bodies (and pay a multi-second scan). Discovery is refused
    here, so the DEFAULT path runs everywhere -- zeroed attempted, refused,
    discovery by name as before. Tests of the resolver pass their own
    instance explicitly and never reach this."""
    from src import zeroed_body as zb

    def _refuse():
        raise zb.ZeroedBodyError("tests never read the host's MO2 instance")
    monkeypatch.setattr(zb, "_layout_dirs", _refuse)
