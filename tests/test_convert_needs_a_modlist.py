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

"""#convert-needs-a-modlist -- `convert` refuses to run outside a modlist.

`auto` has always refused to start without the MO2 mods folder; `convert` did
not. Measured from source on the pre-fix code, with no MO2 layout reachable:

  * an EMPTY source folder: the already-UBE scan died with a TypeError (caught
    and printed), no UBE body reference was found, nothing was converted -- and
    the run ended "=== all clear ===" with exit code 0;
  * a REAL mod: the same TypeError, 12 NIFs written without a body reference,
    then a crash at the coverage step -- "not enough values to unpack
    (expected 3, got 2)" -- exit code 1 and no conversion report.

Now no mods folder AND no --ube-body-ref is refused with exit 2, as `auto`
does. A deliberate --ube-body-ref conversion of a loose folder still runs: the
already-UBE scan says it could not run and counts a warning, and the coverage
step's early return has the shape its caller unpacks."""
import argparse
from pathlib import Path

import pytest

from src import auto_convert as ac


def _ns(sources, output, *, ube_body_ref=None):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=ube_body_ref,
        workers=1, unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


@pytest.fixture
def no_modlist(monkeypatch, tmp_path):
    """No MO2 layout anywhere: discovery finds nothing and no env names one.
    The failures file is pinned into tmp so nothing lands in the repo root."""
    monkeypatch.delenv("CBBE2UBE_MODS_ROOT", raising=False)
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda *a, **k: ac.paths.Layout())
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    return tmp_path


def _recording_converter(monkeypatch, output):
    """Record every source handed to the per-mod converter. Recorded, not
    raised: `_cmd_convert` catches a source's exception and carries on, so a
    stub that raised could never fail these tests."""
    calls = []

    def _record(source_dir, *a, **k):
        calls.append(Path(source_dir))
        return ac.AutoConvertResult(source_dir=Path(source_dir),
                                    output_dir=Path(output))

    monkeypatch.setattr(ac, "auto_convert_mod", _record)
    return calls


def test_convert_without_a_modlist_is_refused(no_modlist, monkeypatch, capsys):
    src = no_modlist / "SomeMod"
    src.mkdir()
    out = no_modlist / "out"
    calls = _recording_converter(monkeypatch, out)
    rc = ac._cmd_convert(_ns([src], out))
    text = capsys.readouterr().out
    assert calls == [], "a source was converted outside a modlist"
    assert rc == 2, f"exit {rc}: a convert outside a modlist must fail like auto"
    assert "could not locate the MO2 mods folder" in text
    assert "all clear" not in text


def test_a_deliberate_body_ref_conversion_still_runs(no_modlist, monkeypatch,
                                                     capsys):
    """--ube-body-ref is the way to convert a loose folder on purpose."""
    src = no_modlist / "LooseMod"
    src.mkdir()
    ref = no_modlist / "femalebody_1.nif"
    ref.write_bytes(b"")
    out = no_modlist / "out"
    calls = _recording_converter(monkeypatch, out)
    ac._cmd_convert(_ns([src], out, ube_body_ref=ref))
    text = capsys.readouterr().out
    assert calls == [src], "a deliberate --ube-body-ref conversion was refused"
    assert "TypeError" not in text, (
        "the already-UBE scan still crashes when there is no mods folder")
    assert "cannot check other mods for existing UBE patches" in text
    assert "all clear" not in text, (
        "a skipped safety check must not end the run as 'all clear'")
    import json
    fails = json.loads((no_modlist / "CBBEtoUBE_last_failures.json")
                       .read_text(encoding="utf-8"))["failures"]
    scan = [f for f in fails if f["kind"] == "check skipped"]
    assert len(scan) == 1 and scan[0]["severity"] == "warning", (
        "the skipped scan must reach the failures file as a warning", fails)


def test_the_coverage_step_returns_the_shape_its_caller_unpacks(monkeypatch,
                                                                tmp_path):
    """Its early return handed back 2 values to a caller that unpacks 3."""
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos",
                        lambda *a, **k: set())
    monkeypatch.setattr(ac.paths, "discover_layout",
                        lambda *a, **k: ac.paths.Layout())
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda lay: None)
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda lay: {})
    patches = tmp_path / "patches"
    patches.mkdir()
    ok, targets, body_ran = ac._emit_unified_coverage_patches(
        tmp_path / "out", patches, None, "CBBE_to_UBE_Combined.esp")
    assert (ok, targets, body_ran) == (False, 0, False)


def test_the_already_ube_scan_accepts_no_mods_folder():
    assert ac._third_party_ube_covered_armos(None) == set()
