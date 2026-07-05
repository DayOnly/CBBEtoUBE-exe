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

SkyPatcher is now the PRODUCT default (see ube_patcher._full_skypatcher_enabled).
The legacy ESP-override path still ships as the CBBE2UBE_NO_SKYPATCHER=1 escape
hatch, and the bulk of the suite (ARMO-override output, winner rebase, body-SP,
cross-ESP overrides, dedup, xedit5 fixes) asserts against THAT path. So pin the
test session to the legacy path by default and let the dedicated full-SkyPatcher
tests (test_full_skypatcher.py) opt into the default path explicitly.

The module-level `setdefault` runs when conftest is imported -- BEFORE any test
module -- so script-style modules that run generator checks at import time
(test_ube_patcher.py) also see the pin. The autouse fixture re-pins before every
test so a test that clears the var to exercise full SkyPatcher can't leak it into
the next test (pytest-randomly reorders freely)."""
import os

import pytest

os.environ.setdefault("CBBE2UBE_NO_SKYPATCHER", "1")


@pytest.fixture(autouse=True)
def _legacy_skypatcher_default(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_SKYPATCHER", "1")
