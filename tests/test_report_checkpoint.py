"""#report-checkpoint -- the output mod's report and settings survive a run
that dies, marked incomplete, instead of the previous run's finished report
reading as this one's.

MEASURED 2026-09-15 (the plan's lens C): after a parent-only kill mid-batch the
output root held only _bsa_staging/ and _unmerged_patches/ -- no report,
settings, summary or failures file -- while the Results tab painted the
previous run's conversion_report.json as "Last run". Every batch-level
artefact was written after the loop.

The harness is tests/test_run_warnings_reach_the_tally.py's: a real
`_cmd_convert` inside a temporary modlist, every run-level check clean, the
per-mod converter stubbed."""
import argparse
import json
import time
from pathlib import Path

import pytest

from src import atomic_io
from src import auto_convert as ac
from src import preflight as pf


def _ns(sources, output):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def _modlist(base, monkeypatch, n_sources=2):
    """A temporary modlist with every run-level check clean: (sources, out)."""
    base.mkdir(parents=True, exist_ok=True)
    mods = base / "mods"
    mods.mkdir(exist_ok=True)
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
    monkeypatch.setattr(pf, "_locate_in_mods_or_data",
                        lambda *a, **k: base / "SkyPatcher.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    sources = []
    for i in range(n_sources):
        d = base / f"Mod{i + 1}"
        d.mkdir(exist_ok=True)
        sources.append(d)
    return sources, out


def _result(source_dir, out):
    return ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)


def _report(out):
    return json.loads((out / "conversion_report.json").read_text(encoding="utf-8"))


def test_the_settings_and_an_empty_report_exist_before_the_first_mod(tmp_path, monkeypatch, capsys):
    """A stale report from the previous run is replaced at batch start, not
    after the first mod converts -- a death during the first mod would
    otherwise leave the old scoreboard reading as this run's."""
    sources, out = _modlist(tmp_path, monkeypatch)
    out.mkdir()
    (out / "conversion_report.json").write_text(
        json.dumps({"complete": True, "source_mods": 99}), encoding="utf-8")
    seen = []

    def _converted(source_dir, *a, **k):
        rep = _report(out)
        seen.append((Path(source_dir).name, rep["complete"], rep["source_mods"],
                     rep["sources_planned"], (out / "conversion_settings.json").is_file()))
        return _result(source_dir, out)

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    ac._cmd_convert(_ns(sources, out))
    capsys.readouterr()
    assert seen == [("Mod1", False, 0, 2, True), ("Mod2", False, 1, 2, True)], seen
    final = _report(out)
    assert (final["complete"], final["source_mods"], final["sources_planned"]) == (True, 2, 2)


def test_a_run_that_dies_leaves_its_checkpoint_marked_incomplete(tmp_path, monkeypatch, capsys):
    """SystemExit from the second source: not a conversion error the loop
    catches, but the way a process goes down. The report on disk describes the
    run up to the last finished mod; the settings name the pool that ran; the
    summary, written only at the end, is the control that stays absent."""
    sources, out = _modlist(tmp_path, monkeypatch)
    calls = []

    def _converted(source_dir, *a, **k):
        calls.append(Path(source_dir).name)
        if len(calls) == 2:
            raise SystemExit(3)
        return _result(source_dir, out)

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    with pytest.raises(SystemExit):
        ac._cmd_convert(_ns(sources, out))
    capsys.readouterr()
    rep = _report(out)
    assert (rep["complete"], rep["source_mods"], rep["sources_planned"],
            rep["converted_ok"]) == (False, 1, 2, 1)
    settings = json.loads((out / "conversion_settings.json").read_text(encoding="utf-8"))
    assert settings["machine"]["workers"] == 1, "the settings stamp names the pool that ran"
    assert not (out / "conversion_summary.txt").exists(), (
        "the summary is an end-of-run artefact (control)")


def test_a_source_that_fails_is_in_the_next_checkpoint(tmp_path, monkeypatch, capsys):
    """The checkpoint runs whatever happened to the source: a caught failure
    counts in it too, so a report after a death still names what failed."""
    sources, out = _modlist(tmp_path, monkeypatch, n_sources=3)
    calls = []

    def _converted(source_dir, *a, **k):
        calls.append(Path(source_dir).name)
        if len(calls) == 1:
            raise RuntimeError("boom")      # caught by the loop: recorded, not fatal
        if len(calls) == 3:
            rep = _report(out)
            assert rep["hard_failures"] == 1 and rep["source_mods"] == 2, rep
            raise SystemExit(3)
        return _result(source_dir, out)

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    with pytest.raises(SystemExit):
        ac._cmd_convert(_ns(sources, out))
    capsys.readouterr()
    rep = _report(out)
    assert rep["failed_mods"][0]["name"] == "Mod1" and rep["source_mods"] == 2


def test_the_report_the_settings_and_the_failures_file_are_written_atomically(tmp_path, monkeypatch, capsys):
    """F047: the report and the failures file were plain write_text calls, so
    a death during the write left a torn file for verify_output, the Results
    tab and the end-of-run popup to choke on."""
    sources, out = _modlist(tmp_path, monkeypatch, n_sources=1)
    written = []
    real = atomic_io.atomic_write_bytes

    def _spy(path, data):
        written.append(Path(path).name)
        return real(path, data)

    monkeypatch.setattr(atomic_io, "atomic_write_bytes", _spy)
    monkeypatch.setattr(ac, "auto_convert_mod", lambda s, *a, **k: _result(s, out))
    ac._cmd_convert(_ns(sources, out))
    capsys.readouterr()
    assert written.count("conversion_report.json") == 3, written   # start, the source, the end
    assert "conversion_settings.json" in written, written
    assert "CBBEtoUBE_last_failures.json" in written, written


def test_a_checkpoint_costs_milliseconds(tmp_path):
    """The verifier's cost check: one write per source on a 160-result list.
    A loose bound, so a scan creeping into the writer fails it rather than
    machine noise."""
    from types import SimpleNamespace as NS
    out = tmp_path / "out"
    out.mkdir()
    res = NS(nif_results=[None] * 20, nif_copy_count=20, nif_swap_count=0,
             nif_skipped=0, nif_errors=0, nif_load_failures=[], vfs_other_mod_count=0,
             output_esps=["a.esp"], source_esps=["s.esp"], notes=[], textures_copied=0,
             nif_morph_losses=0, nif_morph_loss_results=[])
    results = [(NS(name=f"Mod{i}"), res, None) for i in range(160)]
    t0 = time.perf_counter()
    p = ac._checkpoint_report(out, results, planned=160, workers=4)
    dt = time.perf_counter() - t0
    assert p is not None and _report(out)["complete"] is False
    assert _report(out)["source_mods"] == 160
    assert dt < 2.0, f"a checkpoint took {dt:.2f} s"
