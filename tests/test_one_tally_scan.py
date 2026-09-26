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

r"""#one-tally -- every problem warning the parent prints is in the run's record.

The run's end-of-run count and the list the window opens are counted from
`_RUN_FAILURES` alone. A problem line (`!!`) with no entry lets a run end
"=== all clear ===" over a log that says otherwise. On 7f5c04c the scan saw
63 of the 100 problem warnings with no record on their path: 5 run inside a
conversion, 16 are recorded elsewhere or have no run record to reach, and 42
were gaps. 39 are now recorded here, each as a warning (so no exit code
moved); the other 3 are recorded by lane tally-encoding-0926 and listed until
it merges.

The scan (`warning_surface.tally_rows`) sees a record that follows the warning
on its own path: later in the same block, or right after the if/elif chain it
sits in. ALLOWED is everything else, each with its reason. A new unrecorded
problem warning fails here until it is recorded or listed; a listed one that is
now recorded, or gone, fails too, so the list stays true.
"""
import concurrent.futures as cf
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import warning_surface as ws                       # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import build_info, failure_summary, paths              # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: Modules whose warn() calls run inside `convert_nif` -- in a pool worker, or
#: in-process for one piece or --workers 1. What they report reaches the record
#: through the piece's result and the parent's read-back of what was written.
WORKER_MODULES = {
    "src/nif_convert_writer.py": (
        "runs inside convert_nif: an over-cap shape left unsplit, or a skipped "
        "partition pass, is found again by the parent's postflight read-back and "
        "recorded as a CTD-class mesh issue; a re-author that drops a shape keeps "
        "the previous complete file"),
}

#: (function, warning as `tally_rows` renders it) -> why no record follows it.
ALLOWED = {
    ("_nif_convert_worker", "…: conversion raised"):
        "runs in a worker; the piece comes back with status 'error' and the "
        "parent records it as 'mesh failed'",
    ("_failed", "conversion failed: …"):
        "the error rides home in the batch's results; _cmd_convert records it "
        "once as 'source failed' when it lists the results",
    ("_write_failures_file", "could not write the failures file (…)"):
        "the record could not be written: an entry would land nowhere, and the "
        "tally was already printed",
    ("_emit_unified_coverage_patches", "unified coverage emission failed: …"):
        "returns ok=False; the caller records it as 'coverage' (winner-scan "
        "incomplete)",
    ("_cmd_convert", "no MO2 mods folder: cannot check other mods for existing "
                     "UBE patches"):
        "recorded at the tally as 'check skipped' (_ube_scan_skipped)",
    ("_cmd_convert", "could not scan for existing UBE patches (…)"):
        "recorded at the tally as 'check skipped' (_ube_scan_skipped)",
    ("_cmd_convert", "conversion failed: …"):
        "the error is kept in the results; the tally loop records it as "
        "'source failed' or 'vanilla sweep failed'",
    ("_cmd_convert", "PATCH VALIDATOR: … warning(s)"):
        "one 'patch validator' entry for the whole batch, recorded after the loop",
    ("_cmd_convert", "POSTFLIGHT: … load-breaking + … other issue(s) on the FINAL "
                     "Combined"):
        "each half is recorded under its own `if` below it: load-breaking issues "
        "as a failure, the others as a warning",
    ("_mesh_index_unreadable_warnings", "… mod folder(s) could not be fully read "
                                        "while locating armour meshes"):
        "returns one record per folder; both callers record them",
    ("_find_armor_mod_dirs_uncached", "could not read which vanilla armour meshes "
                                      "to locate (…)"):
        "source selection runs before the record starts; kept in "
        "_SELECTION_RUN_WARNINGS and recorded by _cmd_convert",
    ("_find_armor_mod_dirs_uncached", "could not locate armour meshes across the "
                                      "enabled mods (…)"):
        "source selection runs before the record starts; kept in "
        "_SELECTION_RUN_WARNINGS and recorded by _cmd_convert",
    ("_cmd_auto", "… post-convert phase(s) FAILED"):
        "a sum of failures already recorded after the convert step",
    # Printed before `_cmd_convert` starts the run's record (and clears it).
    # Lane tally-encoding-0926 (635e67e) carries it in as `carried_failures`.
    ("_cmd_auto", "vanilla sweep DISABLED this run: …"):
        "printed by auto before _cmd_convert clears the record; carried into the "
        "record by lane tally-encoding-0926 (635e67e, carried_failures)",
    # Recorded by lane tally-encoding-0926 (635e67e): these two entries go when
    # it merges -- this test then says they are recorded.
    ("_auto_convert_mod_steps", "… piece(s) from an earlier run could not be moved "
                                "out of meshes\\ (a file is in use): …"):
        "recorded by lane tally-encoding-0926 (635e67e); drop this entry at its merge",
    ("_auto_convert_mod_steps", "… piece(s) from an earlier run were only partly "
                                "moved out of meshes\\ and could not be put back: …"):
        "recorded by lane tally-encoding-0926 (635e67e); drop this entry at its merge",
    # Standalone subcommands: no run record, no tally and no failures file; the
    # exit code is their result.
    ("_cmd_merge", "master re-sort failed: …"):
        "`merge` keeps no run record; its exit code is the result",
    ("_cmd_merge", "POSTFLIGHT CTD on merged output: … load-breaking issue(s)"):
        "`merge` keeps no run record; exits 2",
    ("_cmd_merge", "postflight validation skipped: …"):
        "`merge` keeps no run record; its exit code is the result",
    ("_cmd_validate", "…"):
        "`validate` keeps no run record; exits 1 when any plugin has an issue",
}


def _unlisted(rows):
    return [r for r in rows if not r["recorded"]
            and r["module"] not in WORKER_MODULES
            and (r["function"], r["what"]) not in ALLOWED]


# --- the scan -----------------------------------------------------------------------

def test_every_parent_problem_warning_is_recorded_or_listed():
    rows = ws.tally_rows()
    assert len(rows) >= ws.FLOOR, f"only {len(rows)} problem warnings found"
    bad = _unlisted(rows)
    assert not bad, (
        "a problem warning printed in the parent with no entry in the run's "
        "record: the run can end 'all clear' after it. Record it "
        "(_record_failure, severity='warning' unless nothing converted) or list "
        "it in ALLOWED with the reason: "
        + "; ".join(f"{r['module']}:{r['line']} {r['function']}: {r['what']}"
                    for r in bad))


def test_the_list_holds_no_stale_or_recorded_entry():
    rows = ws.tally_rows()
    unrec = {(r["function"], r["what"]) for r in rows if not r["recorded"]}
    stale = sorted(k for k in ALLOWED if k not in unrec)
    assert not stale, ("listed but now recorded on its own path, or gone -- "
                       f"remove from ALLOWED: {stale}")


def test_every_module_that_warns_is_scanned_and_placed():
    """A new module calling warn() is in neither the surface nor this scan until
    it is added to warning_surface.MODULES and placed here."""
    import ast
    callers = set()
    for p in sorted((REPO / "src").glob("*.py")):
        if p.name == "user_warnings.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "warn" for n in ast.walk(tree)):
            callers.add(f"src/{p.name}")
    assert callers == set(ws.MODULES)
    assert set(WORKER_MODULES) <= callers


# --- the scan's rule, on planted code (it must flag what it exists to flag) ----------

def _planted(tmp_path, body: str):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auto_convert.py").write_text(body, encoding="utf-8")
    (tmp_path / "src" / "nif_convert_writer.py").write_text("", encoding="utf-8")
    return {r["what"]: r["recorded"] for r in ws.tally_rows(tmp_path)}


def test_the_scan_flags_a_warning_with_no_record(tmp_path):
    got = _planted(tmp_path, (
        "def f(e):\n"
        "    warn('bare', consequence='c')\n"
        "    warn('note', level=NOTE)\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        warn('handled', consequence='c')\n"
        "        _record_failure('k', 's', 'i', severity='warning')\n"))
    assert got == {"bare": "", "handled": "block"}, got


def test_a_record_in_another_branch_does_not_count(tmp_path):
    got = _planted(tmp_path, (
        "def f(a):\n"
        "    if a:\n"
        "        warn('one branch', consequence='c')\n"
        "    else:\n"
        "        _record_failure('k', 's', 'i')\n"
        "    _record_failure_later = 1\n"
        "\n"
        "def g(a):\n"
        "    warn('before a guarded record', consequence='c')\n"
        "    if a:\n"
        "        _record_failure('k', 's', 'i')\n"
        "\n"
        "def h(items):\n"
        "    warn('before a loop that records', consequence='c')\n"
        "    for x in items:\n"
        "        _record_failure('k', 's', x)\n"))
    assert got == {"one branch": "", "before a guarded record": "",
                   "before a loop that records": "block"}, got


def test_a_record_after_the_if_chain_counts_for_each_branch(tmp_path):
    got = _planted(tmp_path, (
        "def f(c):\n"
        "    for x in c:\n"
        "        if x == 1:\n"
        "            warn('first', consequence='c')\n"
        "        elif x == 2:\n"
        "            warn('second', consequence='c')\n"
        "        else:\n"
        "            warn('third', consequence='c')\n"
        "        _record_once('k', 's', 'i')\n"))
    assert got == {"first": "chain", "second": "chain", "third": "chain"}, got


def test_a_record_further_down_the_function_does_not_count(tmp_path):
    got = _planted(tmp_path, (
        "def f(a):\n"
        "    if a:\n"
        "        warn('early', consequence='c')\n"
        "    print('x')\n"
        "    _record_failure('k', 's', 'i')\n"))
    assert got == {"early": ""}, got


# --- what the new records do --------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_record():
    ac._RUN_FAILURES.clear()
    ac._ARMO_WINNER_CACHE.clear()
    ac._ARMO_WINNER_WARNED.clear()
    yield
    ac._RUN_FAILURES.clear()
    ac._ARMO_WINNER_CACHE.clear()
    ac._ARMO_WINNER_WARNED.clear()


def _kinds():
    return [(e["kind"], e["severity"], e.get("count", 1)) for e in ac._RUN_FAILURES]


def test_record_once_keeps_one_entry_per_fact():
    ac._record_once("k", "s", "i", "d")
    ac._record_once("k", "s", "i", "d")
    ac._record_once("k", "s", "i", "other detail")
    assert _kinds() == [("k", "warning", 1), ("k", "warning", 1)]
    assert failure_summary.counts(ac._RUN_FAILURES) == (0, 2)


def test_the_coverage_report_records_each_problem_line_not_its_notes(capsys):
    """Four problem lines of the coverage report are four warnings; its NOTE
    lines (a dead female path keeping the male mesh) add none."""
    ac._report_coverage_holds([{
        "withheld": [(("armour.esp", 0x800), "ExcludedCuirass")],
        "female_kept": [{"slot": "female", "kept": "a_1.nif", "male": "m_1.nif"}],
        "world_mesh_skipped": ["armour.esp|000801"],
        "nude_skipped": [{"why": "unresolved", "arma": "armour.esp|000802"}],
        "female_dead_male": [{"slot": "female", "dead": "d_1.nif", "male": "m_1.nif"}],
    }])
    out = capsys.readouterr().out
    assert out.count("!! [unified]") == 4 and "NOTE: [unified]" in out
    assert _kinds() == [("armour left uncovered", "warning", 1),
                        ("female mesh not converted", "warning", 1),
                        ("body armature not minted", "warning", 1),
                        ("UBE hands/feet not found", "warning", 1)]


def test_a_quiet_coverage_report_records_nothing():
    ac._report_coverage_holds([{"female_dead_male": [
        {"slot": "female", "dead": "d_1.nif", "male": "m_1.nif"}]}])
    assert ac._RUN_FAILURES == []


def test_coverage_validator_hits_are_one_entry_for_the_class():
    ac._print_coverage_warnings("body", {"validation_warnings": ["a", "b", "c"]})
    assert _kinds() == [("coverage validator", "warning", 3)]
    ac._print_coverage_warnings("body", {"validation_warnings": []})
    assert len(ac._RUN_FAILURES) == 1


def test_an_unreadable_modlist_for_the_dead_armature_rule_is_recorded(monkeypatch):
    monkeypatch.setattr(ac.ube_patcher, "_coverage_dead_armature", lambda: True)
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: None)
    assert ac._dead_armature_lookup(Path("out")) is None
    assert _kinds() == [("check skipped", "warning", 1)]


def test_an_unwritten_settings_record_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, "write_run_config", lambda *a, **k: None)
    monkeypatch.setattr(ac, "_checkpoint_report", lambda *a, **k: tmp_path)
    ac._stamp_run_start(tmp_path, planned=1, workers=1)
    assert _kinds() == [("settings record not written", "warning", 1)]


def test_a_failed_orphan_sweep_is_recorded(tmp_path, monkeypatch):
    from src import atomic_io

    def _dies(*a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(atomic_io, "sweep_orphan_temps", _dies)
    assert ac._sweep_orphan_temps_at_start(tmp_path, 0.0) == 0
    assert _kinds() == [("orphaned temp files not swept", "warning", 1)]


def test_every_failed_warm_up_task_counts_in_one_entry(monkeypatch):
    class _Mgr:
        def Barrier(self, n):
            return None

        def shutdown(self):
            pass

    class _Pool:
        def submit(self, fn, *a):
            f = cf.Future()
            f.set_exception(RuntimeError("worker died"))
            return f
    monkeypatch.setattr("multiprocessing.Manager", lambda: _Mgr())
    ac._prewarm_pool(_Pool(), 3, None)
    assert _kinds() == [("worker warm-up failed", "warning", 3)]


def test_a_failed_outfit_read_is_one_entry_however_often_it_is_asked(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_NO_NPC_WORN_NONPLAYABLE", raising=False)

    def _dies(lay=None):
        raise OSError("the plugin list is locked")
    monkeypatch.setattr(paths, "discover_layout", _dies)
    for _ in range(3):              # selection, the convert step, coverage
        assert ac._batch_npc_worn_armos() is None
    assert _kinds() == [("load order not read", "warning", 1)]


def _winner_load_order(monkeypatch, tmp_path, result):
    lay = paths.Layout(mods_root=tmp_path)
    monkeypatch.delenv("CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE", raising=False)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["A.esp", "B.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda l: ["Mod"])
    monkeypatch.setattr(paths, "_plugin_file_index_root",
                        lambda l: {"a.esp": tmp_path / "A.esp",
                                   "b.esp": tmp_path / "B.esp"})
    monkeypatch.setattr(ac, "_armo_winner_nonplayable", result)


@pytest.mark.parametrize("result", [
    lambda paths_: ({}, ["B.esp"]),
    lambda paths_: (_ for _ in ()).throw(OSError("unreadable")),
], ids=["a plugin unreadable", "the read failed"])
def test_a_cached_playability_warning_is_recorded_again_after_the_clear(
        monkeypatch, tmp_path, result, capsys):
    """Source selection builds the map before `_cmd_convert` clears the record;
    the convert step's cache hit records its warning again, without printing
    it again, and once however often it is asked."""
    _winner_load_order(monkeypatch, tmp_path, result)
    ac._batch_armo_winner_nonplayable()                     # source selection
    assert [k for k, _s, _n in _kinds()] == ["load order not read"]
    assert capsys.readouterr().out.count("!!") == 1
    ac._RUN_FAILURES.clear()                                # _cmd_convert starts
    ac._batch_armo_winner_nonplayable()                     # the convert step
    ac._batch_armo_winner_nonplayable()                     # coverage
    assert [k for k, _s, _n in _kinds()] == ["load order not read"]
    assert "!!" not in capsys.readouterr().out


def test_a_clean_playability_read_leaves_no_warning_behind(monkeypatch, tmp_path):
    _winner_load_order(monkeypatch, tmp_path, lambda p: ({}, ["B.esp"]))
    ac._batch_armo_winner_nonplayable()
    monkeypatch.setattr(ac, "_armo_winner_nonplayable", lambda p: ({}, []))
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["B.esp", "A.esp"])
    ac._RUN_FAILURES.clear()
    ac._batch_armo_winner_nonplayable()                     # a new load order
    ac._batch_armo_winner_nonplayable()                     # and its cache hit
    assert ac._RUN_FAILURES == []


def test_the_new_entries_are_warnings_so_no_exit_code_moves():
    """The exit code is 2 only on a FAILURE entry; each new record is a warning,
    and the popup words it as one."""
    ac._report_coverage_holds([{"world_mesh_skipped": ["armour.esp|000801"]}])
    ac._print_coverage_warnings("body", {"validation_warnings": ["x"]})
    assert failure_summary.counts(ac._RUN_FAILURES) == (0, 2)
    assert failure_summary.popup_title(ac._RUN_FAILURES) == "2 warning(s) from this run"
