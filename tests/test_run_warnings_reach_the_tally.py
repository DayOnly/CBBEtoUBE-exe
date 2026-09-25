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

"""#run-warnings -- run-level warnings reach the tally and the failures file.

Measured 2026-09-15 on a source run of `convert` over an empty folder, with a
truncated settings file and no SkyPatcher: the log printed SETTINGS FILE
MALFORMED and the SkyPatcher warning, then "=== all clear ===", exit 0 -- and
the failures file held one entry, "merge did not run, so no coverage was
generated", which the GUI would have titled "1 item(s) failed to convert".

Each `_cmd_convert` run here is inside a temporary modlist with every
run-level check clean unless the test breaks one: SkyPatcher present, the
settings file valid, the already-UBE scan able to run."""
import argparse
import json
import re
from pathlib import Path

from src import auto_convert as ac
from src import preflight as pf

REPO = Path(__file__).resolve().parents[1]


def _ns(sources, output, *, auto_merge=True):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=auto_merge,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def _convert(base, monkeypatch, capsys, *, settings_text="{}", skypatcher=True,
             make_patch=False, shape=None):
    """`shape(result)` fills the converted source's result (a class of problem
    the batch loop reads)."""
    base.mkdir(parents=True, exist_ok=True)
    mods = base / "mods"
    mods.mkdir(exist_ok=True)
    mod = base / "SomeMod"
    mod.mkdir(exist_ok=True)
    out = base / "out"
    settings = base / "CBBEtoUBE_settings.json"
    settings.write_text(settings_text, encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(base / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data",
                        lambda *a, **k: (base / "SkyPatcher.dll") if skypatcher else None)
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)

    def _converted(source_dir, *a, **k):
        if make_patch:
            patches = out / "_unmerged_patches"
            patches.mkdir(parents=True, exist_ok=True)
            (patches / "SomeMod UBE patch.esp").write_bytes(b"")
        res = ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)
        if shape is not None:
            shape(res)
        return res

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    rc = ac._cmd_convert(_ns([mod], out))
    log = capsys.readouterr().out
    fails = json.loads((base / "CBBEtoUBE_last_failures.json")
                       .read_text(encoding="utf-8"))["failures"]
    return rc, log, fails


def _tally(log):
    """(failures, warnings) from the end-of-run line."""
    m = re.search(r"=== (\d+) failure\(s\), (\d+) warning\(s\) ===", log)
    if m:
        return int(m.group(1)), int(m.group(2))
    assert "=== all clear ===" in log, log[-2000:]
    return 0, 0


# --- the record ------------------------------------------------------------------

def test_every_entry_says_how_bad_it_is():
    ac._RUN_FAILURES.clear()
    try:
        ac._record_failure("mesh failed", "SomeMod", "a.nif", "boom")
        ac._record_failure("SkyPatcher not ready", "SkyPatcher", "all", "x",
                           severity="warning")
        assert [e["severity"] for e in ac._RUN_FAILURES] == ["failure", "warning"]
    finally:
        ac._RUN_FAILURES.clear()


# --- each run-level warning is counted and recorded ------------------------------

def test_a_run_that_generated_no_coverage_is_not_all_clear(tmp_path, monkeypatch, capsys):
    """Nothing to merge, so no race coverage: that printed NO RACE COVERAGE
    GENERATED and the run still ended "all clear". Also the baseline for the
    fixture: no other warning."""
    rc, log, fails = _convert(tmp_path, monkeypatch, capsys)
    assert "NO RACE COVERAGE GENERATED" in log
    assert _tally(log) == (0, 1), log[-2000:]
    assert rc == 0, "a warning must not change the exit code"
    assert [(e["kind"], e["severity"]) for e in fails] == [("coverage", "warning")], fails


def test_incomplete_coverage_is_a_counted_warning(tmp_path, monkeypatch, capsys):
    """The winner scan ran and came back incomplete, so the merge used the
    per-source patches: recorded, never counted."""
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches",
                        lambda *a, **k: (False, 0, False))
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split", lambda *a, **k: {})
    rc, log, fails = _convert(tmp_path, monkeypatch, capsys, make_patch=True)
    assert "coverage empty/incomplete" in log, log[-2500:]
    assert _tally(log) == (0, 1), log[-2500:]
    hit = [e for e in fails if e["kind"] == "coverage"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning", fails
    assert "incomplete" in hit[0]["detail"], fails


def test_a_malformed_settings_file_is_a_counted_warning(tmp_path, monkeypatch, capsys):
    _, clean_log, _ = _convert(tmp_path / "ok", monkeypatch, capsys)
    rc, log, fails = _convert(tmp_path / "torn", monkeypatch, capsys,
                              settings_text='{"broken": ')
    assert "SETTINGS FILE MALFORMED" in log
    assert _tally(log)[1] == _tally(clean_log)[1] + 1, (_tally(clean_log), _tally(log))
    assert rc == 0
    hit = [e for e in fails if e["kind"] == "settings file unreadable"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning", fails


def test_a_missing_skypatcher_is_a_counted_warning(tmp_path, monkeypatch, capsys):
    _, clean_log, _ = _convert(tmp_path / "ok", monkeypatch, capsys)
    rc, log, fails = _convert(tmp_path / "none", monkeypatch, capsys, skypatcher=False)
    assert "SkyPatcher.dll not found" in log
    assert _tally(log)[1] == _tally(clean_log)[1] + 1, (_tally(clean_log), _tally(log))
    assert rc == 0
    hit = [e for e in fails if e["source"] == "SkyPatcher"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning", fails


# --- #one-tally: the tally IS the record ------------------------------------------
# Measured 2026-09-25 on 94340ee: a load-breaking issue on the final Combined
# ESP ended "1 failure(s), 1 warning(s)", exit 2, with an EMPTY failures file --
# so the GUI opened no list. Five classes were counted without being recorded.
# Each is broken here on its own; the tally must equal what the file says, and
# the class must be in the file.

def _break_combined(monkeypatch):
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches",
                        lambda *a, **k: (False, 0, False))
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split", lambda *a, **k: {})
    monkeypatch.setattr(ac.ube_patcher, "postflight_validate_combined",
                        lambda *a, **k: {"ctd": [("Combined.esp", "masters out of order")],
                                         "soft": [("Combined.esp", "missing-nif a.nif"),
                                                  ("Combined.esp", "missing-nif b.nif")],
                                         "pieces": ["Combined.esp"]})


_CLASSES = {
    # class: (setup(monkeypatch) -> _convert kwargs, kind, severity, count)
    "VirtualBody re-hide": (
        lambda mp: {"shape": lambda r: setattr(
            r, "virtualbody_rehide_failures", ["a_1.nif", "b_1.nif"])},
        "VirtualBody re-hide failed", "warning", 2),
    "patch validator": (
        lambda mp: {"shape": lambda r: setattr(
            r, "esp_stats_list", [{"validation_warnings": ["w1", "w2", "w3", "w4"]}])},
        "patch validator", "warning", 4),
    "missing partner": (
        lambda mp: (mp.setattr(ac, "_postflight_missing_weight_partners",
                               lambda out: ["x_0.nif has no x_1.nif",
                                            "y_1.nif has no y_0.nif",
                                            "z_0.nif has no z_1.nif"]) or {}),
        "missing _0/_1 partner", "warning", 3),
    "parity": (
        lambda mp: (mp.setattr(ac, "_postflight_weight_partner_fold",
                               lambda out, check: (0, ["d1", "d2"])) or {}),
        "_0/_1 parity", "warning", 2),
    "unreadable output": (
        lambda mp: {"shape": lambda r: setattr(
            r, "nif_load_failures", ["a_0.nif", "a_1.nif"])},
        "output mesh unreadable", "failure", 2),
    "Combined load-breaking": (
        lambda mp: (_break_combined(mp) or {"make_patch": True}),
        "load-breaking plugin issue", "failure", 1),
    "Combined other": (
        lambda mp: (_break_combined(mp) or {"make_patch": True}),
        "plugin postflight", "warning", 2),
    # Recorded and never counted: the popup listed it, the log's tally did not.
    "merge skipped": (
        lambda mp: {"shape": _source_fails},
        "merge skipped", "failure", 1),
}


def _source_fails(result):
    raise RuntimeError("this source did not convert")


import pytest  # noqa: E402


@pytest.mark.parametrize("cls", sorted(_CLASSES))
def test_the_tally_is_counted_from_the_record(cls, tmp_path, monkeypatch, capsys):
    from src import failure_summary as fs
    setup, kind, severity, count = _CLASSES[cls]
    rc, log, fails = _convert(tmp_path, monkeypatch, capsys, **setup(monkeypatch))
    assert _tally(log) == fs.counts(fails), (cls, _tally(log), fails)
    hit = [e for e in fails if e["kind"] == kind]
    # One entry per class -- except an unreadable mesh, which names each file
    # (the log said "1 failure" for any number of them before #one-tally).
    assert len(hit) == (count if kind == "output mesh unreadable" else 1), (cls, fails)
    assert {fs.severity_of(e) for e in hit} == {severity}, hit
    assert sum(fs.count_of(e) for e in hit) == count, hit
    assert rc == (2 if fs.counts(fails)[0] else 0), (cls, rc)


def test_a_class_is_one_popup_line_with_its_count():
    """N validator hits: one entry that counts N, not N entries."""
    from src import failure_summary as fs
    ac._RUN_FAILURES.clear()
    try:
        ac._record_failure("patch validator", "per-source patches", "40 warning(s)",
                           severity="warning", count=40)
        ac._record_failure("mesh failed", "ModA", "a.nif", "boom")
        assert ac._run_tally() == (1, 40)
        assert fs.counts(ac._RUN_FAILURES) == (1, 40)
        assert fs.popup_title(ac._RUN_FAILURES) == (
            "1 item(s) failed to convert, 40 warning(s)")
        assert "count" not in ac._RUN_FAILURES[1], (
            "a single entry keeps the file format it always had")
    finally:
        ac._RUN_FAILURES.clear()


def test_a_class_detail_names_the_first_few_and_says_how_many_more():
    d = ac._first_few([f"w{i}" for i in range(7)])
    assert d.startswith("w0; w1; w2") and "4 more" in d, d
    assert ac._first_few(["only"]) == "only"


def test_a_failed_overlay_transfer_reaches_the_file(tmp_path, monkeypatch, capsys):
    """`auto --convert-overlays`: the transfer raising was counted into the exit
    code and never recorded, so the GUI's list did not name it."""
    from src import overlay_transfer
    mods = tmp_path / "mods"
    (mods / "SomeMod").mkdir(parents=True)
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "1")
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: [
        {"name": "SomeMod", "path": mods / "SomeMod", "armor_nifs": 1}])
    monkeypatch.setattr(ac, "_drop_ube_native_candidates", lambda c: c)

    def _convert_clean(conv):
        ac._RUN_FAILURES.clear()
        return 0
    monkeypatch.setattr(ac, "_cmd_convert", _convert_clean)

    def _boom(*a, **k):
        raise RuntimeError("texconv crashed")
    monkeypatch.setattr(overlay_transfer, "convert_overlays", _boom)
    monkeypatch.setattr(ac.nif_convert, "check_ube_nude_morph_files", lambda: [])
    rc = ac.main(["auto", "--convert-overlays", "-o", str(tmp_path / "out")])
    capsys.readouterr()
    fails = json.loads((tmp_path / "CBBEtoUBE_last_failures.json")
                       .read_text(encoding="utf-8"))["failures"]
    assert rc == 2
    assert [(e["kind"], e["severity"]) for e in fails] == [
        ("overlay transfer failed", "failure")], fails


# --- the words a person reads -------------------------------------------------------

_FAILED = {"kind": "mesh failed", "source": "ModA", "item": "a.nif",
           "detail": "boom", "severity": "failure"}
_WARNED = {"kind": "SkyPatcher not ready", "source": "SkyPatcher",
           "item": "every converted piece", "detail": "", "severity": "warning"}
_OLD = {"kind": "mesh failed", "source": "ModB", "item": "b.nif", "detail": ""}


def test_a_warning_is_never_titled_a_failure():
    from src import failure_summary as fs
    assert fs.popup_title([_WARNED]) == "1 warning(s) from this run"
    assert "did NOT convert" not in fs.popup_intro([_WARNED])
    assert fs.popup_title([_FAILED, _WARNED]) == "1 item(s) failed to convert, 1 warning(s)"
    assert fs.popup_title([_FAILED, _OLD]) == "2 item(s) failed to convert", (
        "an entry from before severities existed is a failure")
    lines = "\n".join(fs.popup_lines([_FAILED, _WARNED]))
    assert "[FAILED: mesh failed] a.nif — boom" in lines
    assert "[WARNING: SkyPatcher not ready] every converted piece" in lines


def test_the_status_line_does_not_say_success_over_a_warning():
    from src import failure_summary as fs
    assert fs.status_line(0, []).startswith("Done - success")
    assert "1 warning(s)" in fs.status_line(0, [_WARNED])
    assert "success" not in fs.status_line(0, [_WARNED])
    assert "exit code 2" in fs.status_line(2, [_FAILED])


def test_the_gui_words_its_popup_and_status_from_the_summary():
    """Wiring only -- the wording itself is tested above."""
    gui = (REPO / "src" / "gui.py").read_text(encoding="utf-8")
    for call in ("failure_summary.popup_title(", "failure_summary.popup_intro(",
                 "failure_summary.popup_lines(", "failure_summary.status_line("):
        assert call in gui, f"gui.py no longer calls {call}"
    assert 'item(s) failed to convert")' not in gui


# --- before a run -----------------------------------------------------------------------

def test_preflight_names_a_malformed_settings_file(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    lay = argparse.Namespace(mods_root=mods, game_data_dirs=[], selected_profile="Main")
    monkeypatch.setattr(pf, "_probe_ube_body", lambda: None)
    monkeypatch.setattr(pf, "_probe_cbbe_body", lambda: None)
    monkeypatch.setattr(pf._paths, "enabled_mods", lambda l: set())
    monkeypatch.setattr(pf._paths, "enabled_mods_ordered", lambda l: [])
    settings = tmp_path / "CBBEtoUBE_settings.json"
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    settings.write_text('{"broken": ', encoding="utf-8")
    row = {c.id: c for c in pf.run_checks(lay)}["settingsfile"]
    assert row.status == pf.WARN and "CBBEtoUBE_settings.json.bak" in row.fix, row
    settings.write_text("{}", encoding="utf-8")
    assert {c.id: c for c in pf.run_checks(lay)}["settingsfile"].status == pf.OK
