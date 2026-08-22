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

"""A cached setup verdict must know WHAT IT COVERED.

`preflight.run_checks` gates two checks on the caller's options -- texconv and
the Papyrus compiler, both overlay-only. The GUI runs the check ONCE at launch
and caches the verdict, and the Convert gate reads that cache.

So a verdict cached with overlays OFF and read with overlays ON is a verdict
about checks that never ran -- and because the gate only blocks on "fail" while
both overlay checks are "warn", nothing would ever have mentioned it. The
window now records the scope each scan covered and says so when it no longer
matches.

These tests cover the two halves that can be reached without a display: the
scope RULE (`gui.preflight_scope`) and the gating it describes
(`preflight.run_checks`). The staleness notice itself lives in a Tk closure and
is covered by the structural GUI tests building the window.
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import preflight as pf  # noqa: E402
from src.gui import preflight_scope  # noqa: E402


# ---- the rule ------------------------------------------------------------

def test_overlays_off_means_no_overlay_checks():
    assert preflight_scope(False, False) == (False, False)


def test_overlays_on_pulls_in_the_texconv_check():
    assert preflight_scope(True, False) == (True, False)


def test_overlay_copy_pulls_in_the_papyrus_check():
    assert preflight_scope(True, True) == (True, True)


def test_a_dormant_overlay_copy_does_not_widen_the_scope():
    """`UBE copies` is meaningless with overlays off. If it were passed straight
    through, toggling a dormant option would invalidate a perfectly good cached
    verdict and nag the user for no reason."""
    assert preflight_scope(False, True) == (False, False)
    assert preflight_scope(False, True) == preflight_scope(False, False)


def test_the_scope_is_hashable_and_comparable():
    """The GUI stores it in `state` and compares it with `!=`. A list would
    compare fine but a set/dict would not round-trip an ordering."""
    a, b = preflight_scope(True, True), preflight_scope(True, True)
    assert a == b and hash(a) == hash(b)
    assert preflight_scope(True, True) != preflight_scope(True, False)


def test_truthy_non_bools_are_normalised():
    """Tk's BooleanVar.get() returns ints in some versions. A stored scope of
    (1, 0) must compare equal to a later (True, False) or every read looks
    stale and the notice would fire constantly."""
    assert preflight_scope(1, 0) == preflight_scope(True, False)
    assert preflight_scope(1, 1) == (True, True)


# ---- the gating the rule describes --------------------------------------

def _ids(checks):
    return {getattr(c, "key", getattr(c, "id", None)) for c in checks}


def _run(tmp_path, **kw):
    """run_checks against a SYNTHETIC layout rooted in tmp_path.

    A real `discover_layout()` finds no modlist in the test environment, and
    `run_checks` then returns after ONE failing check -- so the overlay
    assertions below would skip, and a skipped test proving the premise is
    worth nothing. A temp `mods` dir gets past that early return while keeping
    the run fast and machine-independent.
    """
    from src.paths import Layout
    mods = tmp_path / "mods"
    mods.mkdir(exist_ok=True)
    lay = Layout(mods_root=mods, game_data_dirs=[], instance_dir=tmp_path,
                 game_path=None, selected_profile=None)
    return pf.run_checks(lay, **kw)


def test_overlay_checks_are_absent_unless_asked_for(tmp_path):
    """The premise of the whole staleness problem: these checks are OPTIONAL,
    so a scan run without them cannot speak for a run that needs them."""
    off = _ids(_run(tmp_path, want_overlays=False, want_overlay_copy=False))
    assert len(off) > 1, f"run_checks returned early; nothing was scored: {off}"
    assert "texconv" not in off, off
    assert "papyrus" not in off, off


def test_asking_for_overlays_adds_the_texconv_check(tmp_path):
    on = _ids(_run(tmp_path, want_overlays=True, want_overlay_copy=False))
    assert len(on) > 1, f"run_checks returned early; nothing was scored: {on}"
    assert "texconv" in on, on
    assert "papyrus" not in on, (
        "the Papyrus check is for 'UBE copies' only, not for overlays at "
        f"large: {on}")


def test_asking_for_ube_copies_adds_the_papyrus_check(tmp_path):
    on = _ids(_run(tmp_path, want_overlays=True, want_overlay_copy=True))
    assert len(on) > 1, f"run_checks returned early; nothing was scored: {on}"
    assert {"texconv", "papyrus"} <= on, on
