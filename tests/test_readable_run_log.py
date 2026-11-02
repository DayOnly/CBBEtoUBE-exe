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

"""#readable-run-log -- the run log a person reads, and the detail log.

MEASURED on the 2026-10-22 full run: 23,616 lines, 88% of them per-shape pass
lines from the NIF workers; the tally sat on line 23,610. A run now writes the
readable CBBEtoUBE_last_run.log (summary at the top and the bottom, one line
per piece) and CBBEtoUBE_last_run_detail.log (every line, word for word).
CBBE2UBE_RUN_LOG_VERBOSE=1 writes the main log as before.

The end-to-end tests drive the entry module's own tee, as a launch does."""
import importlib.util
import io
import sys
from pathlib import Path

import pytest

from src import run_log

REPO = Path(__file__).resolve().parents[1]
VERBOSE_ENV = "CBBE2UBE_RUN_LOG_VERBOSE"

# A unit's output as the 10-22 log printed it (names shortened).
UNIT = """\
    [anchor-global-fix] seeded 4 chain anchor(s) at source global: ['NPC']
    [coherence-repair] Top: un-buckled 2 patch(es)
  layer ride: re-placed 1487 vert(s) on the layer beneath them (coherent stack)
  roughness cap: smoothed 1134 vert(s) rougher than the author
src/nif_convert_writer.py:1598: RuntimeWarning: Mean of empty slice.
  out = a.mean()
  PASS FAILED: registered_shape_undeclared_bones on bodyaf_1.nif -- RuntimeError('x')
    [bust-split] bodyaf_1.nif: DECLINED Low_Skirt:0 -- no XML-declared ancestor
  body-zap: bodyaf_0.nif: 762 of 57118 body triangles hidden by the armour removed (2 weight file(s), 4 pose(s))
  body-zap: bodyaf_1.nif: 4602 morph rows of undrawn body vertices dropped from the .tri
    [3/3228] 0.0 NIF/s  ETA 80307s
"""


def _results():
    from src import nif_convert as nc
    return [
        nc.ConvertResult(src_path=Path("D:/m/A/meshes/armor/x/bodyaf_1.nif"),
                         dst_path=Path("D:/o/meshes/armor/x/bodyaf_1.nif"),
                         status="converted (body-swap)",
                         reason="PASS FAILED registered_shape_undeclared_bones "
                                "(RuntimeError: x)"),
        nc.ConvertResult(src_path=Path("D:/m/A/meshes/armor/x/bodyaf_0.nif"),
                         dst_path=Path("D:/o/meshes/armor/x/bodyaf_0.nif"),
                         status="converted (body-swap)"),
    ]


# ---- the classifier -----------------------------------------------------------

@pytest.mark.parametrize("line,name", [
    ("    [coherence-repair] Top: un-buckled 2 patch(es)", "coherence-repair"),
    ("    [phase1-antipoke] panties: cleared 2583 vert(s)", "phaseN-antipoke"),
    ("  roughness cap: smoothed 1134 vert(s) rougher", "roughness cap"),
    ("  body-zap: towelwaist_0.nif: 762 of 57118 body", "body-zap"),
    ("  #authored-shape-order: x_1.nif restored 2 shape(s)", "#authored-shape-order"),
    ("  degenerate-tri repair (warp): un-pinched 1", "degenerate-tri repair (warp)"),
    ("    [3/3228] 0.0 NIF/s  ETA 68868s", None),
    ("  PASS FAILED: x on y -- z", None),
    ("", None),
])
def test_a_worker_line_names_its_pass(line, name):
    assert run_log.pass_name(line) == name


def test_a_digest_reads_what_a_piece_line_needs():
    d = run_log.digest(UNIT)
    assert d.zap == [(762, 57118)]
    assert d.declined == ["bust-split"]
    assert d.pywarn == 1
    assert [k for k in d.kept] == [
        "  PASS FAILED: registered_shape_undeclared_bones on bodyaf_1.nif -- RuntimeError('x')"]
    assert d.passes["body-zap"] == 2 and d.passes["coherence-repair"] == 1
    assert "out = a.mean()" not in " ".join(d.passes)


def test_without_a_detail_log_a_unit_prints_whole(capsys):
    """No tee (tests, an in-process convert, a capture buffer): as before."""
    run_log.reset()
    run_log.echo_unit(UNIT, _results())
    assert capsys.readouterr().out == UNIT
    assert run_log.piece_lines(_results()) == []


# ---- the two files, through the entry module's tee ------------------------------

def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_readable", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _a_run(m, monkeypatch, folder, *, verbose=False):
    """A miniature run through the real tee: header, a unit, the per-mod
    block, a problem line, the tally, the summary, the end."""
    from src import auto_convert as ac
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(folder)])
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", "auto"])
    monkeypatch.delenv("CBBE2UBE_RUN_LOG", raising=False)
    if verbose:
        monkeypatch.setenv(VERBOSE_ENV, "1")
    else:
        monkeypatch.delenv(VERBOSE_ENV, raising=False)
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    m._install_log_tee()
    try:
        rs = _results()
        print("=== auto: 1 armor mod(s) to convert ===")
        print("\n=== NIF conversion: 2 file(s) from 1 source(s) on one schedule ===")
        ac._echo_worker_output(UNIT, rs)
        print("[progress-nif] 2 2", flush=True)
        print("\n=== NIF conversion done: 2 file(s) in 3.0s ===")
        print("\n  ModA")
        run_log.detail_print("    masters    : ['Skyrim.esm']")
        for ln in run_log.piece_lines(rs):
            print(ln)
        print("  weight-partner completion: refreshed 2 STALE filled partner(s)")
        print("  !! [unified] 3 female model slot(s) kept their own unconverted mesh")
        ac._record_failure("missing piece", "ModA", "x", "why", severity="warning")
        print("\n=== 0 failure(s), 1 warning(s) ===")
        m._print_run_summary(0)
    finally:
        m._finalize_run_log()
    main = (folder / "CBBEtoUBE_last_run.log").read_text(encoding="utf-8")
    detail = (folder / "CBBEtoUBE_last_run_detail.log").read_text(encoding="utf-8")
    return main, detail


def test_the_run_log_is_readable_and_the_detail_log_is_whole(monkeypatch, tmp_path):
    m = _entry_module()
    main, detail = _a_run(m, monkeypatch, tmp_path)
    lines = main.splitlines()
    # The summary at the top and at the bottom.
    assert lines[0].strip("= ") == "RUN SUMMARY"
    assert main.count("RUN SUMMARY") == 2
    assert "  result   : 0 failure(s), 1 warning(s) (exit 0)" in lines
    assert any("missing piece: ModA / x -- why" in ln for ln in lines)
    # Per-shape pass lines are not in the run log; the lines a person needs are.
    assert "coherence-repair] Top" not in main and "roughness cap: smoothed" not in main
    assert "body-zap: bodyaf_0.nif" not in main
    assert "masters    :" not in main
    for keep in ("  PASS FAILED: registered_shape_undeclared_bones on bodyaf_1.nif -- ",
                 "[progress-nif] 2 2\n",
                 "  weight-partner completion: refreshed 2",
                 "\n=== 0 failure(s), 1 warning(s) ===\n"):
        assert main.count(keep) == 1, keep
    piece = [ln for ln in lines if ln.startswith("      armor/x/bodyaf (_0 _1)  -- ")]
    assert len(piece) == 1
    assert "body-swap" in piece[0] and "zap 762/57118 tris" in piece[0]
    assert "declined: bust-split" in piece[0]
    assert "!! pass failed: registered_shape_undeclared_bones" in piece[0]
    assert run_log.END_MARKER not in main
    # The detail log holds the unit word for word, once, in place.
    assert detail.count(UNIT) == 1
    assert detail.index(UNIT) < detail.index("[progress-nif] 2 2")
    assert "    masters    : ['Skyrim.esm']\n" in detail
    assert detail.count("\n=== 0 failure(s), 1 warning(s) ===\n") == 1
    assert detail.count("  PASS FAILED: registered_shape_undeclared_bones") == 1


def test_the_summary_counts_the_passes_and_names_both_logs(monkeypatch, tmp_path):
    m = _entry_module()
    main, _detail = _a_run(m, monkeypatch, tmp_path)
    assert "coherence-repair 1" in main and "roughness cap 1" in main
    assert "python   : 1 RuntimeWarning(s)" in main
    assert str(tmp_path / "CBBEtoUBE_last_run_detail.log") in main
    assert "!! [unified] 3 female model slot(s)" in main.split("RUN SUMMARY")[1]


def test_the_verbose_switch_writes_the_old_log(monkeypatch, tmp_path):
    """CBBE2UBE_RUN_LOG_VERBOSE=1: every line in the run log, no summary, no
    piece lines. The detail log is still written."""
    m = _entry_module()
    main, detail = _a_run(m, monkeypatch, tmp_path, verbose=True)
    assert UNIT in main
    assert "RUN SUMMARY" not in main
    assert "    masters    : ['Skyrim.esm']\n" in main
    assert "bodyaf (_0 _1)  -- " not in main
    assert UNIT in detail


def test_the_detail_log_rotates_with_its_run(monkeypatch, tmp_path):
    m = _entry_module()
    (tmp_path / "CBBEtoUBE_last_run_detail.log").write_text("the run before\n",
                                                           encoding="utf-8")
    _a_run(m, monkeypatch, tmp_path)
    assert (tmp_path / "CBBEtoUBE_previous_run_detail.log").read_text(
        encoding="utf-8") == "the run before\n"


@pytest.mark.parametrize("argv", [["--help"], ["validate", "x"], []])
def test_a_non_run_writes_no_detail_log(monkeypatch, tmp_path, argv):
    m = _entry_module()
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(tmp_path)])
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", *argv])
    monkeypatch.delenv("CBBE2UBE_RUN_LOG", raising=False)
    m._install_log_tee()
    try:
        print("output")
        assert run_log.split_active() is False
    finally:
        m.release_log_tee()
    assert not list(tmp_path.glob("*_detail.log"))


def test_a_pinned_run_log_gets_its_detail_log_beside_it(monkeypatch, tmp_path):
    m = _entry_module()
    pinned = tmp_path / "pinned" / "run.log"
    pinned.parent.mkdir()
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(tmp_path)])
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", "auto"])
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(pinned))
    m._install_log_tee()
    try:
        print("output")
    finally:
        m.release_log_tee()
    assert sorted(p.name for p in pinned.parent.iterdir()) == ["run.log", "run_detail.log"]


# ---- the summary's parts ----------------------------------------------------------

@pytest.mark.parametrize("title,kind", [
    ("[3/149] planning 'Some Mod'", "planning (each mod)"),
    ("NIF conversion: 3228 file(s) from 149 source(s)", "NIF conversion"),
    ("NIF conversion done: 3228 file(s) in 2055.9s", "NIF conversion done"),
    ("auto-merging 3 patch(es) into CBBE_to_UBE_Combined.esp", "auto-merging"),
    ("0 failure(s), 2 warning(s)", "end"),
    ("auto: 149 armor mod(s) to convert", "batch set-up"),
    ("all clear", "end"),
])
def test_a_section_title_names_its_kind(title, kind):
    assert run_log.section_kind(title) == kind


def test_the_time_table_sums_the_per_mod_sections():
    t = [0.0]
    ob = run_log.RunObserver(clock=lambda: t[0])
    for at, line in ((5, "--- [1/2] planning 'A' ---"), (15, "--- [2/2] planning 'B' ---"),
                     (35, "=== NIF conversion: 4 file(s) ===")):
        t[0] = at
        ob.feed(line + "\n")
    table = dict(ob.time_table(now=95.0))
    assert table == {"reading the modlist": 5.0, "planning (each mod)": 30.0,
                     "NIF conversion": 60.0}


def test_the_observer_survives_partial_writes():
    ob = run_log.RunObserver()
    for part in ("  !! some", "thing broke\n  NOTE: fyi\n", "=== 1 failure(s), 0 warning(s) ===\n"):
        ob.feed(part)
    assert ob.problems == ["!! something broke"]
    assert ob.notes == ["NOTE: fyi"]
    assert ob.quotes["tally"] == "1 failure(s), 0 warning(s)"


# ---- the window stops reading at the end marker -------------------------------------

def test_the_end_cut_stops_at_the_marker_even_split_across_reads():
    from src.gui import LogEndCut
    cut = LogEndCut(run_log.END_MARKER)
    got = cut.feed("line one\n[run-log")
    got += cut.feed("-end]\nSHIFTED TAIL\n")
    got += cut.feed("more\n", final=True)
    assert got == "line one\n"
    assert cut.ended


def test_the_end_cut_passes_ordinary_text_through_unchanged():
    from src.gui import LogEndCut
    cut = LogEndCut(run_log.END_MARKER)
    assert cut.feed("[progress-nif] 1 3\n") == "[progress-nif] 1 3\n"
    assert cut.feed("partial line") == "partial line"
    assert cut.feed("[run", final=True) == "[run"


class _Proc:
    def poll(self):
        return 0


def test_the_window_does_not_read_past_the_end_marker(tmp_path):
    from src import gui
    log = tmp_path / "CBBEtoUBE_last_run.log"
    log.write_text("summary bottom\n" + run_log.END_MARKER + "\nDone.\n",
                   encoding="utf-8")
    said = []
    assert gui.tail_child_log(_Proc(), str(log), said.append,
                              sleep=lambda s: None) is True
    assert "".join(said) == "summary bottom\n"


def test_quiet_weight_variants_are_one_piece(monkeypatch):
    """A copy unit that printed nothing has no digest; its _0 and _1 are still
    one line."""
    from src import nif_convert as nc

    class _T(io.StringIO):
        detail_enabled = True

        def write_detail(self, s):
            return len(s)

    monkeypatch.setattr(sys, "stdout", _T())
    monkeypatch.delenv(VERBOSE_ENV, raising=False)
    rs = [nc.ConvertResult(src_path=Path(f"D:/m/meshes/a/feet{s}.nif"),
                           dst_path=Path(f"D:/o/meshes/a/feet{s}.nif"),
                           status="converted (copy)") for s in ("_0", "_1")]
    assert run_log.piece_lines(rs, indent="") == ["a/feet (_0 _1)  -- copy"]


def test_prepend_keeps_the_body_and_drops_the_marker(tmp_path):
    for nl in ("\n", "\r\n"):
        p = tmp_path / "x.log"
        p.write_bytes(f"body{nl}{run_log.END_MARKER}{nl}Done.{nl}".encode("utf-8"))
        assert run_log.prepend_to_file(str(p), ["HEAD"])
        assert p.read_bytes().decode("utf-8") == f"HEAD{nl}{nl}body{nl}Done.{nl}"


# ---- the switch is a setting -----------------------------------------------------

def test_the_verbose_switch_is_a_diagnostics_setting():
    from src import gui_settings
    s = gui_settings.by_key()["run_log_verbose"]
    assert (s.tab, s.env, s.default, s.invert) == ("Diagnostics", VERBOSE_ENV,
                                                   False, False)


def test_detail_print_goes_to_stdout_without_a_detail_log(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    run_log.detail_print("a", "b")
    assert buf.getvalue() == "a\nb\n"
