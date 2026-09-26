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

r"""#sweep-recover-every-run -- a killed run's sweep moves go back on the next
run of any kind, and keep their record.

THE DEFECT. The stale-output sweep moves old conversions to
`_superseded\<stamp>\` and keeps them only when the merge confirms. The GUI's
Cancel is a hard kill, so a run stopped between the moves and the merge never
runs its `finally`, and the journal stays unsettled. Only the next FULL run's
sweep put those files back. A Select-mods or `--plugins-only` run in between
left the old Combined naming missing meshes and rewrote the manifest without
the moved bases (it carries an entry only while its file is on disk), so when
a full run finally put them back they read as "not recorded as converted by
any run" and could never move again.

THE RULE. Every `auto` run puts an unsettled journal back at its start, prints
and records it; the manifest carries the record of a base whose files a move no
run settled left in a stamp folder. `CBBE2UBE_NO_SWEEP_RECOVER_EVERY_RUN=1`
restores the old behaviour.
"""
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import preflight as pf
from src import stale_sweep as ss
from src import user_warnings
from tests.test_run_warnings_reach_the_tally import _ns
from tests.test_stale_output_sweep import (
    IRON, OLD, STAMP, World, _args, _finish, _state, _sweep, _vanilla)
# The autouse fixture of the sweep's own tests: clean switches and caches.
from tests.test_stale_output_sweep import _clean  # noqa: F401

SWITCH = "CBBE2UBE_NO_SWEEP_RECOVER_EVERY_RUN"
KIND = "stale sweep put back after an interrupted run"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])


@pytest.fixture
def killed(tmp_path):
    """A full run moved OLD, then the process was killed before its merge."""
    w = World(tmp_path)
    w.mesh(IRON, "_0.nif", "_1.nif", ".tri")
    w.mesh(OLD, "_0.nif", "_1.nif", ".nif", ".tri", ".xml")
    w.record({OLD: "Old Mod", IRON: "vanilla"})
    _sweep(w, [_vanilla(w)])
    assert not w.has(OLD) and w.moved(OLD).is_file()
    ss._set_pending(None)                       # taskkill /F: no finally
    return w


def _kinds():
    return [e["kind"] for e in ac._RUN_FAILURES]


def _a_run(w, **kw):
    """The sweep's part of one `auto` run, in `_cmd_convert`'s order: the
    recovery at the start, the sweep, the finish that writes the manifest."""
    results = [_vanilla(w)]
    args = _args(w, results, **kw)
    ac._stale_recover_at_start(args, w.out)
    _sweep(w, results, args)
    _finish(w, results, args)
    return results


# ============================================================ every kind of run

def test_a_select_mods_run_puts_back_and_keeps_the_record(tmp_path, monkeypatch,
                                                          killed):
    """Through `_cmd_convert` itself: the files are back before the first source
    converts, and the manifest the run writes still records the moved base."""
    w = killed
    src = tmp_path / "SomeMod"
    src.mkdir()
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(w.mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data", lambda *a, **k: tmp_path / "x.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    seen = {}

    def _converted(source_dir, *a, **k):
        seen["back when the batch starts"] = w.has(OLD)
        r = ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=w.out)
        r.claimed_weight_bases = {IRON}
        return r
    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac, "_complete_weight_partners", lambda *a, **k: (0, 0))
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches",
                        lambda *a, **k: (True, 0, True))
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split", lambda *a, **k: {})
    args = _ns([src], w.out)
    args.stale_sweep = {"all_mods": False, "warn_base": user_warnings.problem_count(),
                        "started": 1_700_000_100.0, "mods_root": str(w.mods),
                        "enabled": None, "excluded": []}
    ac._cmd_convert(args)
    assert seen == {"back when the batch starts": True}
    assert w.has(OLD) and w.has(OLD, ".xml") and w.has(OLD, "_0.nif")
    assert w.manifest()["bases"][OLD] == "Old Mod"
    assert KIND in _kinds()


def test_a_select_mods_run_keeps_the_record_so_a_full_run_can_move_it(killed):
    """The point of the record: the next full run moves the base again."""
    w = killed
    _a_run(w, all_mods=False)
    assert w.has(OLD) and w.manifest()["bases"][OLD] == "Old Mod"
    ss._set_pending(None)
    _sweep(w, [_vanilla(w)])
    assert not w.has(OLD), "moved again on its record"
    assert [d["action"] for d in w.report()["bases"] if d["key"] == OLD] == ["move"]


def test_a_plugins_only_run_puts_back_and_keeps_the_record(killed):
    w = killed
    _a_run(w, plugins_only=True)
    assert w.has(OLD) and w.has(OLD, ".tri")
    assert w.manifest()["bases"][OLD] == "Old Mod"


def test_a_full_run_after_a_kill_puts_back_and_still_moves(killed, capsys):
    """The put-back is information, not a problem: it does not make the plan
    look incomplete, so the full run after a kill moves what it decides."""
    w = killed
    n = user_warnings.problem_count()
    results = [_vanilla(w)]
    args = _args(w, results)
    ac._stale_recover_at_start(args, w.out)
    assert w.has(OLD) and user_warnings.problem_count() == n
    assert "put back the old conversions an interrupted run" in capsys.readouterr().out
    _sweep(w, results, args)
    assert not w.has(OLD) and ss.pending() is not None


def test_the_put_back_is_recorded_in_the_tally(killed):
    w = killed
    assert ac._stale_recover_at_start(_args(w, [_vanilla(w)]), w.out) == 0
    assert ac._run_tally() == (0, 1) and _kinds() == [KIND]
    assert ac._stale_recover_at_start(_args(w, [_vanilla(w)]), w.out) == 0
    assert _kinds() == [KIND], "settled once"


def test_a_run_with_the_sweep_off_still_puts_back(killed, monkeypatch):
    """Turning the sweep off must not leave meshes missing."""
    monkeypatch.setenv("CBBE2UBE_NO_STALE_OUTPUT_SWEEP", "1")
    w = killed
    ac._stale_recover_at_start(_args(w, [_vanilla(w)], all_mods=False), w.out)
    assert w.has(OLD)


def test_a_run_without_the_sweep_context_puts_nothing_back(killed):
    """`convert` on its own is not an `auto` run."""
    w = killed
    args = _args(w, [_vanilla(w)])
    del args.stale_sweep
    assert ac._stale_recover_at_start(args, w.out) == 0
    assert not w.has(OLD) and _kinds() == []


# ============================================================ what stays moved

def _locked(monkeypatch, name):
    """`name` cannot move back (a file in use)."""
    real = ss.os.replace

    def _replace(a, b):
        if Path(b).name == name and "_superseded" in str(a):
            raise PermissionError("in use")
        return real(a, b)
    monkeypatch.setattr(ss.os, "replace", _replace)


def test_a_file_that_cannot_go_back_is_a_problem_and_keeps_its_record(
        killed, monkeypatch):
    w = killed
    _locked(monkeypatch, "cuirass_1.nif")
    n = user_warnings.problem_count()
    results = [_vanilla(w)]
    args = _args(w, results, all_mods=False)
    assert ac._stale_recover_at_start(args, w.out) == 1
    assert user_warnings.problem_count() == n + 1
    assert _kinds() == [KIND, "stale sweep put back failed"]
    assert not w.has(OLD) and w.has(OLD, "_0.nif")
    _finish(w, results, args)
    assert w.manifest()["bases"][OLD] == "Old Mod"


def test_a_base_wholly_stranded_keeps_its_record(killed, monkeypatch):
    """No file of the base came back, so it is not on disk at all."""
    w = killed
    monkeypatch.setattr(ss, "put_back", lambda pairs: [str(f) for f, _t in pairs])
    _a_run(w, all_mods=False)
    assert not any(w.has(OLD, s) for s in ("_0.nif", "_1.nif", ".nif", ".tri", ".xml"))
    assert w.manifest()["bases"] == {IRON: "vanilla", OLD: "Old Mod"}


def test_a_stranded_patch_keeps_its_record(tmp_path):
    w = World(tmp_path)
    w.mesh(IRON, "_1.nif")
    w.patches.mkdir(parents=True)
    (w.patches / "Old (CBBEtoUBE src).esp").write_bytes(b"x")
    w.record({IRON: "vanilla"}, {"Old (CBBEtoUBE src).esp": "Old Mod"})
    sdir = w.out / "_superseded" / STAMP
    (sdir / "_unmerged_patches").mkdir(parents=True)
    (w.patches / "Old (CBBEtoUBE src).esp").replace(
        sdir / "_unmerged_patches" / "Old (CBBEtoUBE src).esp")
    ss.write_json(sdir / ss.JOURNAL_NAME, {
        "status": "partly put back after an interrupted run",
        "planned": ["_unmerged_patches/Old (CBBEtoUBE src).esp"]})
    results = [_vanilla(w)]
    _finish(w, results, _args(w, results, all_mods=False))
    assert w.manifest()["patches"] == {"Old (CBBEtoUBE src).esp": "Old Mod"}


def test_a_move_the_merge_kept_drops_its_record(tmp_path):
    """A settled move is not stranded: the manifest forgets it, as before."""
    w = World(tmp_path)
    w.mesh(IRON, "_1.nif")
    w.mesh(OLD, "_1.nif")
    w.record({OLD: "Old Mod", IRON: "vanilla"})
    _a_run(w)
    assert not w.has(OLD) and w.moved(OLD).is_file()
    assert w.manifest()["bases"] == {IRON: "vanilla"}


def test_the_stranded_files_are_the_ones_still_in_the_stamp_folder(killed):
    w = killed
    assert ss.stranded_files(w.out) == ({OLD}, set())
    w.moved(OLD).unlink()
    w.moved(OLD, "_0.nif").unlink()
    w.moved(OLD, ".nif").unlink()
    w.moved(OLD, ".tri").unlink()
    w.moved(OLD, ".xml").unlink()
    assert ss.stranded_files(w.out) == (set(), set())


@pytest.mark.parametrize("status, stranded", [
    ("moving", True), ("waiting for the merge", True), ("partly put back", True),
    ("partly put back after an interrupted run", True), ("kept", False),
    ("put back", False), ("put back after an interrupted run", False),
    ("nothing moved", False)])
def test_which_journals_leave_files_stranded(killed, status, stranded):
    w = killed
    ss.update_journal(w.out / "_superseded" / STAMP / ss.JOURNAL_NAME, status=status)
    assert ss.stranded_files(w.out) == (({OLD} if stranded else set()), set())


# ============================================================ the switch

def test_switched_off_a_select_mods_run_leaves_them_moved_and_drops_the_record(
        killed, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    w = killed
    _a_run(w, all_mods=False)
    assert not w.has(OLD) and _kinds() == []
    assert w.manifest()["bases"] == {IRON: "vanilla"}


def test_switched_off_the_full_runs_sweep_still_puts_back(killed, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    w = killed
    results = [_vanilla(w)]
    args = _args(w, results)
    ac._stale_recover_at_start(args, w.out)
    assert not w.has(OLD)
    _sweep(w, results, args, _state())
    assert ss.pending() is not None, "the sweep recovered, decided and moved again"
