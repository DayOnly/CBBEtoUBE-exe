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

"""`open_nif_retry` must SURVIVE a transient open failure (Windows file-share /
handle contention under many parallel workers made valid 3-4MB Sigrin meshes
report "Could not open ... as nif" at 23 workers) and still RE-RAISE for a
genuinely unreadable file. Regression guard: too many workers must not silently
drop a valid mesh. #transient-open-retry"""
import pytest

from src import nif_io


def test_open_nif_retry_survives_transient(monkeypatch):
    calls = []

    class FakeMod:
        @staticmethod
        def NifFile(filepath):
            calls.append(filepath)
            if len(calls) < 3:            # fail twice (transient), then succeed
                raise RuntimeError("Could not open '%s' as nif" % filepath)
            return ("nif", filepath)

    monkeypatch.setattr(nif_io, "pynifly", FakeMod)
    out = nif_io.open_nif_retry("big.nif", attempts=5, base_delay=0.0)
    assert out == ("nif", "big.nif")
    assert len(calls) == 3                # retried past the two transient failures


def test_open_nif_retry_reraises_after_exhaustion(monkeypatch):
    class FakeMod:
        @staticmethod
        def NifFile(filepath):
            raise RuntimeError("genuinely corrupt")

    monkeypatch.setattr(nif_io, "pynifly", FakeMod)
    with pytest.raises(RuntimeError, match="genuinely corrupt"):
        nif_io.open_nif_retry("bad.nif", attempts=3, base_delay=0.0)


def test_open_nif_retry_first_try_no_delay(monkeypatch):
    """A file that opens immediately must not incur any retry/sleep."""
    class FakeMod:
        @staticmethod
        def NifFile(filepath):
            return ("nif", filepath)

    monkeypatch.setattr(nif_io, "pynifly", FakeMod)
    assert nif_io.open_nif_retry("ok.nif") == ("nif", "ok.nif")
