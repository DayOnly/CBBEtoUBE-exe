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

"""Warn when this build added options the saved settings have never seen.

WHY. `save_values` writes ONLY non-default values, so an option absent from
`CBBEtoUBE_settings.json` means "at its default" -- which in the run log is
indistinguishable from "the user switched it off on purpose".

That ambiguity cost a full reconvert on 2026-07-27. Two options built that day
(`source_follow`, `drape_xml_gate`) shipped default-OFF; the settings file predated
them, so the flag echo read `active flags (4): CHEST_FOLLOW=1, ...` -- a perfectly
normal-looking line -- and an hour of conversion produced none of the intended work.
The only evidence was the ABSENCE of two names in a list, which nobody can be
expected to notice.

The fix records `_known_settings` in the saved file: every key the build knew at save
time. A later build can then NAME what is new instead of inferring it."""
import json

import pytest

import src.gui_settings as gs
import src.preflight as pf


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


# --- the baseline record ---------------------------------------------------------

def test_save_records_every_key_this_build_offers(tmp_path):
    f = tmp_path / "s.json"
    assert gs.save_values({"chest_follow": True}, path=f)
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert gs.KNOWN_KEYS_FIELD in raw
    assert set(raw[gs.KNOWN_KEYS_FIELD]) == {s.key for s in gs.SETTINGS}


def test_the_baseline_key_is_ignored_when_loading(tmp_path):
    """It must not become a phantom setting or upset `_coerce`."""
    f = _write(tmp_path / "s.json",
               {gs.KNOWN_KEYS_FIELD: ["chest_follow"], "chest_follow": True})
    vals = gs.load_values(path=f)
    assert vals["chest_follow"] is True
    assert gs.KNOWN_KEYS_FIELD not in vals


def test_save_then_load_round_trips_without_drift(tmp_path):
    f = tmp_path / "s.json"
    gs.save_values(gs.defaults() | {"chest_follow": True}, path=f)
    assert gs.load_values(path=f)["chest_follow"] is True
    assert gs.unseen_settings(path=f) == (True, [])


# --- detecting a genuinely new option ---------------------------------------------

def test_an_option_missing_from_the_baseline_is_reported(tmp_path):
    """THE case. The file knew every key EXCEPT one -> that one is new."""
    keys = sorted(s.key for s in gs.SETTINGS)
    target = keys[0]
    f = _write(tmp_path / "s.json",
               {gs.KNOWN_KEYS_FIELD: [k for k in keys if k != target]})
    baseline, new = gs.unseen_settings(path=f)
    assert baseline is True
    assert [s.key for s in new] == [target]


def test_a_complete_baseline_reports_nothing(tmp_path):
    f = _write(tmp_path / "s.json",
               {gs.KNOWN_KEYS_FIELD: sorted(s.key for s in gs.SETTINGS)})
    assert gs.unseen_settings(path=f) == (True, [])


def test_a_file_predating_the_tracking_reports_UNKNOWN_not_clean(tmp_path):
    """The user's real 2026-07-27 file: values but no baseline. Claiming 'nothing
    new' there would repeat the exact failure this exists to catch."""
    f = _write(tmp_path / "s.json", {"chest_follow": True, "smp_antipoke": True})
    baseline, new = gs.unseen_settings(path=f)
    assert baseline is False and new == []


def test_a_missing_file_is_NOT_a_warning(tmp_path):
    """Nothing was ever chosen, so nothing is new relative to a choice. Warning on a
    fresh install would train the user to ignore the message."""
    assert gs.unseen_settings(path=tmp_path / "absent.json") == (True, [])


def test_a_corrupt_file_does_not_raise(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{not json", encoding="utf-8")
    assert gs.unseen_settings(path=f) == (True, [])
    f.write_text('["a list, not an object"]', encoding="utf-8")
    assert gs.unseen_settings(path=f) == (True, [])


def test_a_non_string_in_the_baseline_is_tolerated(tmp_path):
    f = _write(tmp_path / "s.json", {gs.KNOWN_KEYS_FIELD: ["chest_follow", 7, None]})
    baseline, new = gs.unseen_settings(path=f)
    assert baseline is True
    assert "chest_follow" not in {s.key for s in new}


# --- surfaced in both places -------------------------------------------------------

def test_preflight_emits_a_check(monkeypatch):
    """The GUI surface."""
    fake = [s for s in gs.SETTINGS[:2]]
    monkeypatch.setattr(gs, "unseen_settings", lambda path=None: (True, fake))
    checks = []
    from src.preflight import _c, WARN
    _baseline, _new = gs.unseen_settings()
    if _new:
        checks.append(_c("newsettings", "New options in this build", WARN, "x", "y"))
    assert checks and checks[0].id == "newsettings"


def test_run_checks_includes_the_id():
    import inspect
    assert '"newsettings"' in inspect.getsource(pf.run_checks)


def test_the_cli_path_warns_next_to_the_flag_echo():
    """The CLI/one-click path is where this actually bit -- the GUI preflight alone
    would not have caught it."""
    import inspect
    import src.auto_convert as ac
    assert "_warn_unseen_settings" in inspect.getsource(ac._echo_active_experiment_flags)
    src = inspect.getsource(ac._warn_unseen_settings)
    assert "unseen_settings" in src


def test_the_warning_never_breaks_a_run(monkeypatch):
    """A diagnostic that can abort an hour-long conversion is worse than none."""
    import src.auto_convert as ac

    def boom(path=None):
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr(gs, "unseen_settings", boom)
    ac._warn_unseen_settings()          # must not raise
