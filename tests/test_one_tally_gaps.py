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

r"""#one-tally -- the last parent-side problem lines the record never carried.

The run counts problems from `_RUN_FAILURES` alone, and the failures file the
window reads is that list. Found on c0367ee, these printed a problem line and
left no entry, so a run could end "=== all clear ===" with an empty file:

1. "vanilla sweep DISABLED this run" -- printed by `auto` before
   `_cmd_convert` clears the record, so it has to be carried into the run.
2. The built-UBE supersede's "could not be moved" and "only partly moved"
   (#skip-built-ube-path, #supersede-whole-base).
3. The alt-texture reconcile's bare `!!` lines: a converted NIF that fails to
   load, colour-variant entries dropped, another mod's copy unreadable.
"""
import argparse
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac                               # noqa: E402
from src import ube_patcher as up                                # noqa: E402
from tests import test_run_warnings_reach_the_tally as trw       # noqa: E402
from tests.synthetic_nif import pynifly_available                # noqa: E402
from tests.test_coverage_ube_twin import _twin_of                # noqa: E402
from tests.test_npc_worn_nonplayable import load_order           # noqa: E402,F401
from tests.test_supersede_whole_base import _fail_on             # noqa: E402

needs_pynifly = pytest.mark.skipif(not pynifly_available(),
                                   reason="pynifly native lib not available")


@pytest.fixture(autouse=True)
def _fresh_record(monkeypatch):
    for k in ("CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE", "CBBE2UBE_NO_SKIP_BUILT_UBE_PATH",
              "CBBE2UBE_NO_COVERAGE_UBE_TWIN", "CBBE2UBE_NO_VANILLA_SWEEP",
              "CBBE2UBE_NO_RECONCILE_LOADED_MESH"):
        monkeypatch.delenv(k, raising=False)
    ac._RUN_FAILURES.clear()
    yield
    ac._RUN_FAILURES.clear()


def _kinds():
    return [(e["kind"], e["severity"]) for e in ac._RUN_FAILURES]


# --- 1. a disabled vanilla sweep ----------------------------------------------

class _Stop(Exception):
    pass


def test_auto_carries_a_disabled_sweep_into_the_run(monkeypatch, tmp_path, capsys):
    """The sweep's preflight fails: `auto` prints the warning and hands the
    entry to `_cmd_convert`, which starts the record."""
    data = tmp_path / "Data"
    data.mkdir()
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda: types.SimpleNamespace(game_data_dirs=[data]))
    monkeypatch.setattr(ac.paths, "export_to_env", lambda lay: None)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: tmp_path)
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: [])
    monkeypatch.setattr(ac.nif_convert, "_find_cbbe_base_body", lambda w: None)
    monkeypatch.setattr(ac.nif_convert, "_find_ube_femalebody", lambda w: None)
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda: None)
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: [
        {"name": "ModA", "path": tmp_path / "ModA", "armor_nifs": 1, "esps": 1}])
    monkeypatch.setattr(ac, "_preflight_vanilla_sweep",
                        lambda d: (False, "Skyrim.esm could not be read"))
    seen = {}

    def _convert(conv):
        seen["carried"] = list(getattr(conv, "carried_failures", None) or ())
        seen["sources"] = list(conv.sources)
        raise _Stop()
    monkeypatch.setattr(ac, "_cmd_convert", _convert)
    args = argparse.Namespace(output=tmp_path / "out", workers=1,
                              no_textures=False, merged_name="C.esp",
                              list_only=False, only_mods=None)
    with pytest.raises(_Stop):
        ac._cmd_auto(args)
    assert "vanilla sweep DISABLED this run" in capsys.readouterr().out
    assert data not in seen["sources"]
    assert [(e["kind"], e["severity"], e["detail"]) for e in seen["carried"]] == [
        ("vanilla sweep disabled", "warning", "Skyrim.esm could not be read")]


def test_convert_records_what_auto_carried(tmp_path, monkeypatch, capsys):
    """The carried entry survives the record's clear, reaches the failures
    file and counts one more warning; the exit code is unchanged."""
    _, clean_log, clean = trw._convert(tmp_path / "ok", monkeypatch, capsys)
    carried = {"kind": "vanilla sweep disabled",
               "source": "Vanilla sweep (base game + DLC)",
               "item": "whole source", "detail": "Skyrim.esm could not be read",
               "severity": "warning"}
    real_ns = trw._ns

    def _ns(*a, **k):
        ns = real_ns(*a, **k)
        ns.carried_failures = [dict(carried)]
        return ns
    monkeypatch.setattr(trw, "_ns", _ns)
    rc, log, fails = trw._convert(tmp_path / "off", monkeypatch, capsys)
    assert rc == 0, "a warning must not change the exit code"
    assert trw._tally(log)[1] == trw._tally(clean_log)[1] + 1, (
        trw._tally(clean_log), trw._tally(log))
    assert carried in fails and carried not in clean, fails
    assert fails[0] == carried, "recorded first, before anything the run finds"


# --- 2. the built-UBE supersede ---------------------------------------------------

def _plan_supersede(load_order, tmp_path, monkeypatch, dress, stale, *fail, back=()):
    class _First(BaseException):
        pass
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress])

    def _worker(item):
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    out = tmp_path / "out"
    d = out / "meshes" / "!UBE" / "armor" / "outfit"
    d.mkdir(parents=True)
    for n in stale:
        (d / n).write_bytes(b"old")
    _fail_on(monkeypatch, *fail, back=back)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    try:
        ac.auto_convert_mod(load_order, out, ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1,
                            built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
    except _First:
        pass


def test_a_piece_the_supersede_could_not_move_is_recorded(
        load_order, tmp_path, monkeypatch, capsys):
    _plan_supersede(load_order, tmp_path, monkeypatch,
                    ["armor/outfit/dress_1.nif", "armor/outfit/dress_0.nif"],
                    ("dress_1.nif", "dress_0.nif", "dress.tri"), "dress_0.nif")
    assert "could not be moved out of meshes" in capsys.readouterr().out
    assert _kinds() == [("built UBE supersede move failed", "warning")]
    e = ac._RUN_FAILURES[0]
    assert e["item"] == "1 piece(s)" and "armor/outfit/dress" in e["detail"], e


def test_a_piece_the_supersede_tore_is_recorded(
        load_order, tmp_path, monkeypatch, capsys):
    _plan_supersede(load_order, tmp_path, monkeypatch,
                    ["armor/outfit/dress_1.nif"],
                    ("dress_1.nif", "dress_0.nif", "dress.tri"), "dress.tri",
                    back=("dress_1.nif",))
    assert "were only partly moved" in capsys.readouterr().out
    assert _kinds() == [("built UBE supersede move torn", "warning")]
    assert "armor/outfit/dress (dress_1.nif)" in ac._RUN_FAILURES[0]["detail"]


# --- 3. the alt-texture reconcile -------------------------------------------------

def _unreadable_game_copy(monkeypatch, tmp_path):
    from tests.test_reconcile_loaded_mesh import REL, SET, _game_loads, _plugin
    junk = tmp_path / "builder" / REL
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_bytes(b"not a nif")
    _game_loads(monkeypatch, (junk, None))
    return _plugin(tmp_path / "out", SET)


@pytest.mark.parametrize("batch_off", [False, True])
def test_a_problem_list_takes_the_reconcile_problem_lines(
        monkeypatch, tmp_path, capsys, batch_off):
    """With a list the reconcile prints no `!!` of its own -- the converter
    warns -- and the class and its models are in the list, on the batch view
    and piece by piece alike."""
    from tests.test_reconcile_loaded_mesh import MODEL
    monkeypatch.delenv("CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY", raising=False)
    if batch_off:
        monkeypatch.setenv("CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY", "1")
    plugin = _unreadable_game_copy(monkeypatch, tmp_path)
    problems = []
    assert up.reconcile_alt_texture_indices_all(
        plugin, tmp_path / "out" / "meshes", problems=problems) == 0
    assert "!!" not in capsys.readouterr().err
    assert problems == [(up.ALTTEX_GAME_COPY_UNREADABLE, [MODEL])]


def test_a_converted_nif_that_fails_to_load_is_a_problem(
        monkeypatch, tmp_path, capsys):
    from tests.test_reconcile_loaded_mesh import MODEL, REL, SET, _plugin
    ours = tmp_path / "out" / "meshes" / "!UBE" / REL
    ours.parent.mkdir(parents=True, exist_ok=True)
    ours.write_bytes(b"not a nif")
    plugin = _plugin(tmp_path / "out", SET)
    problems = []
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes",
                                     problems=problems)
    assert "!!" not in capsys.readouterr().err
    assert [c for c, _m in problems] == [up.ALTTEX_LOAD_FAILED]
    assert [m.lower() for m in problems[0][1]] == [MODEL.lower()]


@needs_pynifly
def test_dropped_colour_entries_are_a_problem(monkeypatch, tmp_path, capsys):
    from tests.test_alttex_set_provenance import (
        LOST_CONV, LOST_SET, LOST_SRC, SWITCHES, _resolve_to, _world)
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)
    _src, plugin = _world(tmp_path, LOST_SRC, LOST_CONV, LOST_SET)
    _resolve_to(monkeypatch, {})
    problems = []
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes",
                                     problems=problems)
    assert "!!" not in capsys.readouterr().err
    assert [c for c, _m in problems] == [up.ALTTEX_ENTRIES_DROPPED]


@pytest.mark.parametrize("cls,kind,words", [
    (up.ALTTEX_ENTRIES_DROPPED, "alt-texture entries dropped",
     "could not be matched to their source mesh"),
    (up.ALTTEX_GAME_COPY_UNREADABLE, "alt-texture game copy unreadable",
     "could not be read from the mod the game loads them from"),
    (up.ALTTEX_LOAD_FAILED, "alt-texture mesh unreadable", "failed to load"),
], ids=["dropped", "game-copy", "load-failed"])
def test_each_reconcile_problem_is_warned_and_recorded(cls, kind, words, capsys):
    models = [f"!UBE\\armor\\m{i}_1.nif" for i in range(7)]
    ac._warn_alttex_problems([(cls, models)], "Combined.esp")
    out = capsys.readouterr().out
    assert "!! alt-texture reconcile: 7 " in out and words in out, out
    assert "and 2 more" in out, "the log names five and counts the rest"
    assert _kinds() == [(kind, "warning")]
    e = ac._RUN_FAILURES[0]
    assert (e["source"], e["item"]) == ("Combined.esp", "7 model(s)"), e
    assert e["detail"].startswith("!UBE\\armor\\m0_1.nif; ") and "4 more" in e["detail"]


def test_the_merge_records_what_the_reconcile_found(tmp_path, monkeypatch, capsys):
    """Wiring: `_cmd_convert` hands the reconcile a list and records what it
    holds, counted in the tally and written to the failures file -- even when
    the reconcile raises after finding it."""
    from src import failure_summary
    trw._break_combined(monkeypatch)
    monkeypatch.setattr(ac.ube_patcher, "postflight_validate_combined",
                        lambda *a, **k: {"ctd": [], "soft": [],
                                         "pieces": ["Combined.esp"]})

    def _reconcile(merged, meshes, problems=None):
        problems.append((up.ALTTEX_ENTRIES_DROPPED, ["!UBE\\a\\coat_1.nif"]))
        raise RuntimeError("plugin would not load")
    monkeypatch.setattr(ac.ube_patcher, "reconcile_alt_texture_indices_all",
                        _reconcile)
    rc, log, fails = trw._convert(tmp_path, monkeypatch, capsys, make_patch=True)
    assert "alt-texture reconcile failed" in log
    hit = [e for e in fails if e["kind"] == "alt-texture entries dropped"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning", fails
    assert hit[0]["detail"] == "!UBE\\a\\coat_1.nif"
    assert trw._tally(log) == failure_summary.counts(fails), (trw._tally(log), fails)
    assert rc == 0, "a warning must not change the exit code"
