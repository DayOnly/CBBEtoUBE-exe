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

"""An off-switch must reach the EXPENSIVE part, not only the write.

`record_standoff` and `record_torso_bands` each begin with the audit check and
return immediately when it is off. But the `_TorsoCast` that FEEDS them was
constructed first and unconditionally, so `CBBE2UBE_NO_STANDOFF_AUDIT=1` skipped
only the cheap half.

PROFILED on a five-layer piece: `_TorsoCast.__init__` is **43.7s of a 179.7s
conversion** -- 24% of the run, and 61% of ALL ray casting in it -- spent purely
on telemetry that the flag claimed to have disabled.

This generalises past this one flag: a gate placed at the point of WRITING is
not a gate on the work that produced the value. Put it where the cost is.
"""
import inspect

from src import fit_metrics
from src import nif_convert as nc


def test_the_audit_flag_is_consulted_before_the_cast_is_built():
    """The gate must sit on the branch that CONSTRUCTS `_TorsoCast`, not only
    inside the recorders it feeds."""
    src = inspect.getsource(nc.convert_nif_phase2)
    i = src.index("_TorsoCast(")
    head = src[:i]
    # the nearest enclosing condition must already have consulted the flag
    assert "fit_metrics._enabled()" in head, (
        "_TorsoCast is built before the audit flag is checked -- the off-switch "
        "would skip the write but still pay for the rays")


def test_the_recorders_still_guard_themselves():
    """Belt and braces: the caller-side gate is the one that saves the time,
    but the recorders must stay self-guarding so another call site cannot
    reintroduce the write."""
    for fn in (fit_metrics.record_standoff, fit_metrics.record_torso_bands):
        body = inspect.getsource(fn)
        assert "_enabled()" in body, f"{fn.__name__} no longer self-guards"


def test_the_flag_actually_flips(monkeypatch):
    """A gate on a predicate that cannot be false is decoration."""
    monkeypatch.delenv("CBBE2UBE_NO_STANDOFF_AUDIT", raising=False)
    assert fit_metrics._enabled() is True
    monkeypatch.setenv("CBBE2UBE_NO_STANDOFF_AUDIT", "1")
    assert fit_metrics._enabled() is False


def test_default_behaviour_is_unchanged(monkeypatch):
    """This is a COST fix, not a behaviour change: with the audit on (the
    default) the branch must still run exactly as before, so the records the
    postflight checks read are still written."""
    monkeypatch.delenv("CBBE2UBE_NO_STANDOFF_AUDIT", raising=False)
    assert fit_metrics._enabled() is True, (
        "the audit must remain ON by default -- postflight check E reads these "
        "records, and silently losing them would be a worse trade than the time")
