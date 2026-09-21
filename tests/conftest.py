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
import pytest


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
