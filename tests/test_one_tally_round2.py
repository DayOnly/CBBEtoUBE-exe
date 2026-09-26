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

r"""#one-tally, round 2 -- the review of d93b3c0 and what it flagged.

- The nude-skin morph warning was recorded AFTER `_cmd_convert` printed its
  tally: the log said one warning fewer than the window's list. It is now
  checked and recorded before the tally.
- A vertex-colour sweep that stopped with an error was a warning, exit 0. The
  sweep is the only place a colour flag on a shape with no colour buffer (a
  crash on equip) is cleared, so the files it did not reach keep whatever the
  writer left: a CTD-class mesh issue, exit 2 (#vc-sweep-failure;
  CBBE2UBE_NO_VC_SWEEP_FAILURE=1 restores the warning).
- `merge` exited 0 with its postflight check skipped -- the same 0 as a checked
  clean plugin. It exits 1 now (#merge-unverified-exit;
  CBBE2UBE_NO_MERGE_UNVERIFIED_EXIT=1 restores 0). A failed master re-sort
  alone does not move it: the postflight judges the master order.
- The mesh writer's failed partition pass, unsplit over-vertex-cap shape and
  dropped re-author shape were printed to a worker's stderr only. The piece's
  `reason` carries them home now, and the parent records each as a warning.
- Under --plugins-only the convert step never asked for the playability map,
  so the warning source selection printed before the record began was lost.
"""
import argparse
import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc                                    # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import failure_summary, paths                          # noqa: E402
from src import nif_convert_telemetry as tel                    # noqa: E402
from src import nif_convert_writer as nw                        # noqa: E402
from src import preflight as pf                                 # noqa: E402

VC_OFF = "CBBE2UBE_NO_VC_SWEEP_FAILURE"
MERGE_OFF = "CBBE2UBE_NO_MERGE_UNVERIFIED_EXIT"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for var in (VC_OFF, MERGE_OFF, "CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE"):
        monkeypatch.delenv(var, raising=False)
    ac._RUN_FAILURES.clear()
    ac._ARMO_WINNER_CACHE.clear()
    ac._ARMO_WINNER_WARNED.clear()
    tel._begin_piece_pass_log()
    yield
    ac._RUN_FAILURES.clear()
    ac._ARMO_WINNER_CACHE.clear()
    ac._ARMO_WINNER_WARNED.clear()
    tel._begin_piece_pass_log()


# --- a `_cmd_convert` run in a temporary modlist, every run-level check clean ---------

def _convert(base, monkeypatch, capsys, *, meshes=False, plugins_only=False,
             winner_read=True, shape=None, **extra):
    base.mkdir(parents=True, exist_ok=True)
    mods = base / "mods"
    mods.mkdir(exist_ok=True)
    mod = base / "SomeMod"
    mod.mkdir(exist_ok=True)
    out = base / "out"
    settings = base / "CBBEtoUBE_settings.json"
    settings.write_text("{}", encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(base / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    if winner_read:
        monkeypatch.setattr(ac, "_batch_armo_winner_nonplayable", lambda: {})
    monkeypatch.setattr(pf, "_locate_in_mods_or_data",
                        lambda *a, **k: base / "SkyPatcher.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)

    def _converted(source_dir, *a, **k):
        if meshes:
            (out / "meshes").mkdir(parents=True, exist_ok=True)
        res = ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)
        if shape is not None:
            shape(res)
        return res

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac, "refresh_mod_esp", _converted)
    ns = argparse.Namespace(
        sources=[mod], output=out, esp_name=None, no_textures=True,
        copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=plugins_only, **extra)
    rc = ac._cmd_convert(ns)
    log = capsys.readouterr().out
    fails = json.loads((base / "CBBEtoUBE_last_failures.json")
                       .read_text(encoding="utf-8"))["failures"]
    return rc, log, fails, ns


def _tally(log):
    m = re.search(r"=== (\d+) failure\(s\), (\d+) warning\(s\) ===", log)
    assert m, log[-2000:]
    return int(m.group(1)), int(m.group(2))


# --- (b) the nude-skin morph warning is in the tally it prints before -----------------

def test_the_nude_skin_morph_warning_is_counted_before_the_tally(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ac.nif_convert, "check_ube_nude_morph_files",
                        lambda: ["UBE Hands: no morph file"])
    rc, log, fails, ns = _convert(tmp_path, monkeypatch, capsys,
                                  nude_morph_check=True)
    assert log.index("UBE nude-skin morph check:") < log.index("=== 0 failure(s)")
    # The coverage the empty merge could not generate, and the morph check.
    assert _tally(log) == (0, 2), log[-2000:]
    assert [e["kind"] for e in fails].count("nude-skin morph missing") == 1, fails
    assert ns.nude_morph_check == "done", "auto would run it a second time"
    assert rc == 0


def test_a_plain_convert_runs_no_nude_skin_morph_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ac.nif_convert, "check_ube_nude_morph_files",
                        lambda: ["UBE Hands: no morph file"])
    _rc, log, fails, _ns = _convert(tmp_path, monkeypatch, capsys)
    assert "nude-skin morph" not in log
    assert "nude-skin morph missing" not in [e["kind"] for e in fails]


# --- (2) a vertex-colour sweep that did not finish is a CTD-class failure -------------

def _sweep_dies(monkeypatch):
    def _dies(*a, **k):
        raise OSError("the output folder could not be listed")
    monkeypatch.setattr(ac.nif_convert, "sanitize_output_vertex_color_flags", _dies)


def test_a_sweep_that_did_not_finish_fails_the_run_as_a_crash_risk(
        tmp_path, monkeypatch, capsys):
    _sweep_dies(monkeypatch)
    rc, log, fails, _ns = _convert(tmp_path, monkeypatch, capsys, meshes=True)
    assert "vertex-color sanitize failed" in log
    hit = [e for e in fails if e["kind"] == "CTD-class mesh issue"]
    assert len(hit) == 1 and hit[0]["severity"] == "failure", fails
    assert rc == 2
    assert _tally(log)[0] == 1
    assert "problem(s) in what was written" in failure_summary.popup_title(fails)


def test_switched_off_a_sweep_that_did_not_finish_is_the_warning_it_was(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(VC_OFF, "1")
    _sweep_dies(monkeypatch)
    rc, _log, fails, _ns = _convert(tmp_path, monkeypatch, capsys, meshes=True)
    assert [(e["kind"], e["severity"]) for e in fails
            if "sweep" in e["kind"] or e["kind"].startswith("CTD")] == [
        ("vertex-colour sweep failed", "warning")], fails
    assert rc == 0


def test_a_sweep_whose_pool_died_but_finished_stays_a_warning(
        tmp_path, monkeypatch, capsys):
    """Its serial fallback finished the sweep: nothing was left unchecked."""
    monkeypatch.setattr(ac.nif_convert, "sanitize_output_vertex_color_flags",
                        lambda *a, **k: {"files": 3, "files_changed": 0,
                                         "shapes_fixed": 0,
                                         "pool_error": "MemoryError: a worker died"})
    rc, _log, fails, _ns = _convert(tmp_path, monkeypatch, capsys, meshes=True)
    assert [e["severity"] for e in fails if e["kind"] == "worker pool died"] == [
        "warning"], fails
    assert rc == 0


# --- (1) `merge`: a plugin nobody checked is not a clean merge ------------------------

def _merge(tmp_path, monkeypatch, *, resort=None, postflight=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    a, b = tmp_path / "a.esp", tmp_path / "b.esp"
    a.write_bytes(b"")
    b.write_bytes(b"")
    out = tmp_path / "Combined.esp"

    def _no_layout():
        raise OSError("no modlist in this test")
    monkeypatch.setattr(ac.paths, "discover_layout", _no_layout)
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split",
                        lambda *a, **k: {"output": str(out)})
    monkeypatch.setattr(ac, "_report_skypatcher_unsafe_names", lambda stats: None)
    monkeypatch.setattr(ac, "_outside_ube_mesh_resolver", lambda p: None)
    monkeypatch.setattr(ac.ube_patcher, "resort_masters_all",
                        resort or (lambda *a, **k: 0))
    monkeypatch.setattr(ac.ube_patcher, "postflight_validate_combined",
                        postflight or (lambda *a, **k: {"ctd": [], "soft": [],
                                                        "pieces": [out.name]}))
    return ac._cmd_merge(argparse.Namespace(
        patches=[str(a), str(b)], output=str(out), no_esl_flag=False,
        author="t", description="t"))


def _raises(*a, **k):
    raise RuntimeError("the check could not run")


def test_merge_exits_1_when_its_postflight_could_not_run(tmp_path, monkeypatch):
    assert _merge(tmp_path, monkeypatch, postflight=_raises) == 1


def test_switched_off_merge_exits_0_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv(MERGE_OFF, "1")
    assert _merge(tmp_path, monkeypatch, postflight=_raises) == 0


def test_a_failed_resort_is_judged_by_the_postflight_that_follows(tmp_path, monkeypatch):
    assert _merge(tmp_path, monkeypatch, resort=_raises) == 0, "checked clean"
    assert _merge(tmp_path / "b", monkeypatch, resort=_raises, postflight=lambda *a, **k: {
        "ctd": [("Combined.esp", "master-ordering: out of order")], "soft": [],
        "pieces": ["Combined.esp"]}) == 2
    assert _merge(tmp_path / "c", monkeypatch, resort=_raises, postflight=_raises) == 1


def test_a_clean_merge_exits_0(tmp_path, monkeypatch):
    assert _merge(tmp_path, monkeypatch) == 0


# --- (3) the writer's facts ride home in the piece's reason --------------------------

def _labels():
    return [f.split(" (", 1)[0] for f in tel._piece_pass_failures()]


def test_a_partition_pass_that_died_is_noted_for_the_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "_pynifly", _raises)
    assert nw._normalize_partitions_on_disk(tmp_path / "piece_1.nif") == 0
    assert _labels() == ["PASS FAILED " + nw.WRITER_PASS_PARTITIONS]


def test_an_unsplit_shape_over_the_vertex_cap_is_noted_for_the_parent(
        tmp_path, monkeypatch):
    big = types.SimpleNamespace(name="Cuirass", bone_names=[],
                                verts=[(0.0, 0.0, 0.0)] * (nc.SKIN_PARTITION_VERT_CAP + 1))
    monkeypatch.setattr(nc, "_pynifly", lambda: types.SimpleNamespace(
        NifFile=lambda filepath: types.SimpleNamespace(shapes=[big])))
    monkeypatch.setattr(nc, "_preserved_dismember_slot", lambda s: None)
    monkeypatch.setattr(nc, "_split_oversize_partition_verts", lambda s, part_id=None: 1)
    assert nw._normalize_partitions_on_disk(tmp_path / "piece_1.nif") == 0
    assert _labels() == ["PASS FAILED " + nw.WRITER_PASS_VERT_SPLIT]


def test_a_re_author_that_drops_a_shape_is_noted_for_the_parent(tmp_path, monkeypatch):
    from tests.synthetic_nif import (TRIS, VERTS, build_skinned_shapes_nif,
                                     pynifly_available)
    if not pynifly_available():
        pytest.skip("pynifly native lib not available")
    p = build_skinned_shapes_nif(
        tmp_path / "piece_1.nif",
        [("fur", VERTS, TRIS, [(0.0, 0.0, 1.0)] * 4),
         ("coat", [(x + 5.0, y, z) for x, y, z in VERTS], TRIS,
          [(0.0, 0.0, 1.0)] * 4)])
    real = nw._copy_shape

    def _copy(s, *a, **k):
        if s.name == "coat":
            raise RuntimeError("could not copy")
        return real(s, *a, **k)
    monkeypatch.setattr(nw, "_copy_shape", _copy)
    before = p.read_bytes()
    assert nc._reauthor_nif_fresh(p) is False
    assert p.read_bytes() == before, "the prior complete file is kept"
    assert _labels() == ["PASS FAILED " + nw.WRITER_PASS_REAUTHOR_DROP]


def _piece(name, reason):
    return nc.ConvertResult(src_path=Path(name), dst_path=Path("out") / name,
                            status="converted (copy)", reason=reason)


def test_the_parent_records_each_writer_fact_once_per_source(capsys):
    def _noted(label):
        tel._begin_piece_pass_log()
        tel._note_pass_failure(label, RuntimeError("x"))
        return "; ".join(["heel kept"] + tel._piece_pass_failures())
    results = [_piece("a_1.nif", _noted(nw.WRITER_PASS_PARTITIONS)),
               _piece("b_1.nif", _noted(nw.WRITER_PASS_PARTITIONS)),
               _piece("c_1.nif", _noted(nw.WRITER_PASS_VERT_SPLIT)),
               _piece("d_1.nif", _noted(nw.WRITER_PASS_REAUTHOR_DROP)),
               _piece("e_1.nif", _noted("_some_other_pass")),
               _piece("f_1.nif", "")]
    ac._report_writer_pass_failures("SomeMod", results)
    out = capsys.readouterr().out
    assert out.count("!!") == 3, out
    got = [(e["kind"], e["source"], e["severity"], e.get("count", 1))
           for e in ac._RUN_FAILURES]
    assert got == [("partition pass failed", "SomeMod", "warning", 2),
                   ("over-cap shape not split", "SomeMod", "warning", 1),
                   ("re-author dropped a shape", "SomeMod", "warning", 1)], got
    assert "a_1.nif" in ac._RUN_FAILURES[0]["detail"]


def test_a_writer_fact_from_the_worker_reaches_the_tally(tmp_path, monkeypatch, capsys):
    tel._note_pass_failure(nw.WRITER_PASS_REAUTHOR_DROP, RuntimeError("x"))
    reason = "; ".join(tel._piece_pass_failures())
    tel._begin_piece_pass_log()
    rc, log, fails, _ns = _convert(
        tmp_path, monkeypatch, capsys,
        shape=lambda r: r.nif_results.append(_piece("a_1.nif", reason)))
    hit = [e for e in fails if e["kind"] == "re-author dropped a shape"]
    assert len(hit) == 1 and hit[0]["source"] == "SomeMod", fails
    assert "re-author dropped a shape on 1 NIF(s)" in log
    assert _tally(log) == (0, 2), log[-2000:]   # with the coverage warning
    assert rc == 0


def test_a_clean_source_reports_no_writer_fact(capsys):
    ac._report_writer_pass_failures("SomeMod", [_piece("a_1.nif", "heel kept")])
    assert ac._RUN_FAILURES == [] and "!!" not in capsys.readouterr().out


# --- (4) --plugins-only keeps selection's playability warning ------------------------

_SELECTION_WARNING = ("load order not read", "load order",
                      "2 plugin(s) not read for armour playability",
                      "A.esp, B.esp; an armour they override keeps the playable "
                      "flag of the record before them")


def test_plugins_only_records_the_playability_warning_selection_printed(
        tmp_path, monkeypatch, capsys):
    ac._ARMO_WINNER_WARNED[("load order",)] = _SELECTION_WARNING
    _rc, log, fails, _ns = _convert(tmp_path, monkeypatch, capsys,
                                    plugins_only=True, winner_read=False)
    hit = [e for e in fails if e["kind"] == "load order not read"]
    assert len(hit) == 1 and hit[0]["item"] == _SELECTION_WARNING[2], fails
    assert hit[0]["severity"] == "warning"
    assert _tally(log)[1] == len([e for e in fails if e["severity"] == "warning"])


def test_plugins_only_with_the_rule_off_records_nothing_kept(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE", "1")
    ac._ARMO_WINNER_WARNED[("load order",)] = _SELECTION_WARNING
    _rc, _log, fails, _ns = _convert(tmp_path, monkeypatch, capsys,
                                     plugins_only=True, winner_read=False)
    assert "load order not read" not in [e["kind"] for e in fails], fails


def test_a_read_that_failed_before_its_key_is_kept_for_the_refresh(monkeypatch):
    """The read failed before it knew the load order: nothing is cached, but
    the warning is kept, so an ESP-only refresh records it."""
    def _no_layout(*a, **k):
        raise OSError("the modlist could not be read")
    monkeypatch.setattr(paths, "discover_layout", _no_layout)
    assert ac._batch_armo_winner_nonplayable() is None
    assert ac._ARMO_WINNER_CACHE == {}
    ac._RUN_FAILURES.clear()                          # _cmd_convert starts
    ac._record_selection_playability_warning()
    ac._record_selection_playability_warning()
    assert [e["item"] for e in ac._RUN_FAILURES] == ["armour playability"]


def test_the_unread_plugins_record_says_they_were_not_read(monkeypatch, tmp_path):
    lay = paths.Layout(mods_root=tmp_path)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["A.esp", "B.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda l: ["Mod"])
    monkeypatch.setattr(paths, "_plugin_file_index_root",
                        lambda l: {"a.esp": tmp_path / "A.esp",
                                   "b.esp": tmp_path / "B.esp"})
    monkeypatch.setattr(ac, "_armo_winner_nonplayable", lambda p: ({}, ["B.esp"]))
    ac._batch_armo_winner_nonplayable()
    assert [e["item"] for e in ac._RUN_FAILURES] == [
        "1 plugin(s) not read for armour playability"]
