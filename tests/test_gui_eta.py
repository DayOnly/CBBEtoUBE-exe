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

"""Guards for the per-mod EWMA ETA (progress-marker-driven 'time left')."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gui import _eta_step, _fmt_eta


def test_fmt_eta_buckets():
    assert _fmt_eta(0.4) == "finishing…"
    assert _fmt_eta(30) == "~30s left"
    assert _fmt_eta(90) == "~1m 30s left"
    assert _fmt_eta(3661) == "~61m 01s left"
    assert _fmt_eta(-5) == "finishing…"          # clamped


def test_first_marker_has_no_estimate():
    eta = {"last_t": None, "rate": None}
    # no inter-mod gap yet -> can't estimate; timestamp is recorded
    assert _eta_step(eta, 1, 10, now=100.0) == "estimating…"
    assert eta["last_t"] == 100.0 and eta["rate"] is None


def test_rate_from_inter_mod_gap():
    eta = {"last_t": None, "rate": None}
    _eta_step(eta, 1, 10, now=100.0)             # first marker
    # mod 1 took 10s -> rate 10s/mod, 9 mods to go (current + 8 after)
    out = _eta_step(eta, 2, 10, now=110.0)
    assert eta["rate"] == 10.0
    assert out == "~1m 30s left"                 # 10 * (10-2+1)=90s


def test_ewma_adapts_to_speedup():
    eta = {"last_t": None, "rate": None}
    _eta_step(eta, 1, 10, now=0.0)
    _eta_step(eta, 2, 10, now=10.0)              # gap 10 -> rate 10
    # a fast mod (2s) pulls the EWMA down, not all the way (alpha 0.25)
    _eta_step(eta, 3, 10, now=12.0)              # gap 2 -> rate 0.25*2+0.75*10 = 8
    assert abs(eta["rate"] - 8.0) < 1e-9
    out = _eta_step(eta, 4, 10, now=14.0)        # gap 2 -> rate 0.25*2+0.75*8 = 6.5
    assert abs(eta["rate"] - 6.5) < 1e-9
    # remaining = 10-4+1 = 7 mods * 6.5s = 45.5 -> ~46s
    assert out == "~46s left"


def test_last_mod_estimate_shrinks():
    eta = {"last_t": None, "rate": None}
    _eta_step(eta, 1, 3, now=0.0)
    _eta_step(eta, 2, 3, now=5.0)                # rate 5
    out = _eta_step(eta, 3, 3, now=10.0)         # last mod: remaining = 3-3+1 = 1
    assert out == "~5s left"


def test_non_monotonic_clock_ignored():
    eta = {"last_t": None, "rate": None}
    _eta_step(eta, 1, 5, now=100.0)
    _eta_step(eta, 2, 5, now=110.0)              # rate 10
    r = eta["rate"]
    # clock went backwards -> gap<0 ignored, rate unchanged
    _eta_step(eta, 3, 5, now=105.0)
    assert eta["rate"] == r
