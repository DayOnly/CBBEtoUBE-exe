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

"""Guards for the preflight environment checks (the pre-run safety net)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import preflight as pf


class _Lay:
    def __init__(self, mods_root, **kw):
        self.mods_root = mods_root
        self.game_data_dirs = kw.get("game_data_dirs", [])
        self.selected_profile = kw.get("selected_profile")


def _by_id(checks):
    return {c.id: c for c in checks}


def test_no_modlist_fails_fast():
    checks = pf.run_checks(_Lay(None))
    assert len(checks) == 1
    assert checks[0].id == "modlist" and checks[0].status == pf.FAIL
    assert pf.overall(checks) == pf.FAIL


def test_full_run_all_present(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    (mods / "CBBEtoUBE Auto").mkdir(parents=True)
    data = tmp_path / "Data"
    data.mkdir()
    (data / "UBE_AllRace.esp").write_text("x")
    (data / "RaceCompatibility.esm").write_text("x")
    lay = _Lay(mods, game_data_dirs=[data], selected_profile="Main")
    monkeypatch.setattr(pf, "_probe_ube_body", lambda: tmp_path / "ube_1.nif")
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: tmp_path / "cbbe_1.nif")
    monkeypatch.setattr(pf._paths, "enabled_mods",
                        lambda l: {"CBBEtoUBE Auto", "SomeMod"})
    monkeypatch.setattr(pf._paths, "enabled_mods_ordered",
                        lambda l: ["SomeMod", "CBBEtoUBE Auto"])
    checks = pf.run_checks(lay)
    b = _by_id(checks)
    assert b["modlist"].status == pf.OK
    assert b["ubebody"].status == pf.OK
    assert b["allrace"].status == pf.OK
    assert b["racecompat"].status == pf.OK
    assert b["output"].status == pf.OK
    assert pf.overall(checks) in (pf.OK, pf.WARN)   # disk may warn in CI


def test_missing_ube_body_and_prereqs_flag(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    lay = _Lay(mods, game_data_dirs=[], selected_profile="Main")
    monkeypatch.setattr(pf, "_probe_ube_body", lambda: None)
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: None)
    monkeypatch.setattr(pf._paths, "enabled_mods", lambda l: set())
    monkeypatch.setattr(pf._paths, "enabled_mods_ordered", lambda l: [])
    checks = _by_id(pf.run_checks(lay))
    assert checks["ubebody"].status == pf.FAIL and checks["ubebody"].fix
    assert checks["allrace"].status == pf.WARN      # invisible-armor prereq
    assert checks["racecompat"].status == pf.WARN
    assert pf.overall(list(checks.values())) == pf.FAIL


def test_overlay_checks_only_when_requested(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    lay = _Lay(mods, game_data_dirs=[], selected_profile="Main")
    monkeypatch.setattr(pf, "_probe_ube_body", lambda: tmp_path / "u.nif")
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: tmp_path / "c.nif")
    monkeypatch.setattr(pf, "_probe_texconv", lambda: None)
    monkeypatch.setattr(pf, "_probe_papyrus", lambda: None)
    monkeypatch.setattr(pf._paths, "enabled_mods", lambda l: set())
    monkeypatch.setattr(pf._paths, "enabled_mods_ordered", lambda l: [])
    ids_off = {c.id for c in pf.run_checks(lay)}
    assert "texconv" not in ids_off and "papyrus" not in ids_off
    ids_on = {c.id for c in pf.run_checks(lay, want_overlays=True,
                                          want_overlay_copy=True)}
    assert "texconv" in ids_on and "papyrus" in ids_on


def test_vanilla_sweep_check_statuses(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    data = tmp_path / "Data"
    data.mkdir()
    lay = _Lay(mods, game_data_dirs=[data], selected_profile="Main")
    monkeypatch.setattr(pf, "_probe_ube_body", lambda: tmp_path / "u.nif")
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: tmp_path / "c.nif")
    monkeypatch.setattr(pf._paths, "enabled_mods", lambda l: set())
    monkeypatch.setattr(pf._paths, "enabled_mods_ordered", lambda l: [])
    # Probe OK -> check OK
    monkeypatch.setattr(pf, "_probe_vanilla_sweep",
                        lambda d: (True, "5 master(s), 707 bases; 40/40"))
    b = _by_id(pf.run_checks(lay))
    assert b["vanillasweep"].status == pf.OK
    # Probe fail -> WARN with a fix line (mod conversion unaffected)
    monkeypatch.setattr(pf, "_probe_vanilla_sweep",
                        lambda d: (False, "no Skyrim.esm at X"))
    b = _by_id(pf.run_checks(lay))
    assert b["vanillasweep"].status == pf.WARN and b["vanillasweep"].fix
    # No game data dir -> no sweep check at all
    lay2 = _Lay(mods, game_data_dirs=[], selected_profile="Main")
    assert "vanillasweep" not in {c.id for c in pf.run_checks(lay2)}
