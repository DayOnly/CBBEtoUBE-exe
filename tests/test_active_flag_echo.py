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

"""#settings-did-not-apply -- the run echoes which flags are actually set.

A full modlist reconvert (~1 hour) was once spent testing a flag that never
reached the run. The GUI reads `CBBEtoUBE_settings.json` at STARTUP, so a settings
file written while the GUI was already open had no effect, and the run silently
used the old in-memory state.

Nothing in the log said so. The only evidence was the ABSENCE of a pass's own
message -- which is indistinguishable from that pass simply having nothing to do.
Worse, the obvious check was actively misleading: "the value I set is what I see"
proves nothing when the value I set EQUALS the default.

Echoing the environment makes it a fact at the top of the log rather than an
inference at the end. It prints the ENVIRONMENT, not the settings file, because the
environment is what the conversion actually reads and the two disagreeing is the
entire failure mode."""
import os

from src import auto_convert as ac


def _echo(capsys, env):
    for k in [k for k in os.environ if k.startswith("CBBE2UBE_")]:
        os.environ.pop(k)
    os.environ.update(env)
    ac._echo_active_experiment_flags()
    out = capsys.readouterr().out
    for k in env:
        os.environ.pop(k, None)
    return out


def test_reports_a_set_flag(capsys):
    out = _echo(capsys, {"CBBE2UBE_SMP_ANTIPOKE": "1"})
    assert "SMP_ANTIPOKE=1" in out


def test_reports_a_numeric_value_not_just_the_name(capsys):
    """'the flag was on' is not enough -- the whole point of the last failed run was
    not knowing WHICH value applied."""
    out = _echo(capsys, {"CBBE2UBE_BUST_CLEAR": "2.0"})
    assert "BUST_CLEAR=2.0" in out


def test_says_so_explicitly_when_nothing_is_set(capsys):
    """Silence is what made the failure invisible. An empty list must be stated,
    never omitted -- 'no line' and 'no flags' have to look different."""
    out = _echo(capsys, {})
    assert "none (all defaults)" in out


def test_path_overrides_are_not_listed_as_experiments(capsys):
    """Discovery paths are set on nearly every run and are not experiments; listing
    them would bury the one line that matters."""
    out = _echo(capsys, {"CBBE2UBE_MO2_INI": r"D:\x\ModOrganizer.ini",
                         "CBBE2UBE_SMP_ANTIPOKE": "1"})
    assert "MO2_INI" not in out
    assert "SMP_ANTIPOKE=1" in out
    assert "active flags (1)" in out


def test_empty_value_does_not_count_as_set(capsys):
    """An exported-but-blank variable is how a shell leaves a cleared flag."""
    out = _echo(capsys, {"CBBE2UBE_SMP_ANTIPOKE": "  "})
    assert "none (all defaults)" in out


def test_never_raises(monkeypatch, capsys):
    """A diagnostic must not be able to break a conversion."""
    class Boom(dict):
        def items(self): raise RuntimeError("boom")
    monkeypatch.setattr(ac.os, "environ", Boom())
    ac._echo_active_experiment_flags()          # must not raise


def test_it_is_actually_called_by_the_auto_run():
    """An echo nobody calls is exactly the class of dead code this guards against."""
    import inspect
    src = inspect.getsource(ac)
    assert src.count("_echo_active_experiment_flags()") >= 2, (
        "defined but never invoked from the auto path")


def test_it_runs_before_anything_can_abort_the_run():
    """Placement is the feature. Verified against the real exe: with the echo after
    mod discovery, a run that died at `--only-mods` validation printed NO flag line
    at all -- the diagnostic was absent exactly when something had gone wrong. It
    must precede discovery, the mods-root check, and the --only-mods check."""
    import inspect
    src = inspect.getsource(ac._cmd_auto)
    echo = src.index("_echo_active_experiment_flags()")
    for later in ("paths.discover_layout()", "mods root:", "only_mods"):
        if later in src:
            assert echo < src.index(later), (
                f"the flag echo must print before {later!r}")
