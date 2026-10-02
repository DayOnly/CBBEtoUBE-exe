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
r"""#path-field-quotes and #output-preflight -- GitHub issue #25.

Explorer's "Copy as path" wraps a path in double quotes. Pasted into the output
folder field, the quotes reached `Path()`, every write failed with WinError 123,
and the run carried on through all 125 mods and ended 23 minutes later with 126
failures. Two things are pinned here:

1. A path field is read without its surrounding quotes (a Windows path cannot
   contain one, so no valid path changes) -- the window's fields, the settings
   tab's path rows, and the `-o` option of the command line.
2. The output folder is tried (created, one probe file written and removed)
   before the first mod converts, and a folder that cannot be used ends the run
   at once with one message and exit 2. A dry run writes nothing, so it does not
   try the folder.
"""
import argparse
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac                               # noqa: E402
from src import gui                                              # noqa: E402
from src import paths                                            # noqa: E402


class _Var:
    def __init__(self, text):
        self._t = text

    def get(self):
        return self._t


# --- 1. quotes ------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ('"mods/Out Folder"', "mods/Out Folder"),
    ('  "out"  ', "out"),
    ('"  out  "', "out"),
    ("plain", "plain"),
    ("has space/out", "has space/out"),
    ("", ""),
    ('""', ""),
    ('"unbalanced', '"unbalanced'),           # one quote is not a pair: left as typed
    ('unbalanced"', 'unbalanced"'),
    ('""twice""', '"twice"'),         # ONE pair only, never a loop
])
def test_a_pasted_path_loses_exactly_one_pair_of_quotes(raw, want):
    assert paths.strip_path_quotes(raw) == want


def test_the_window_reads_the_output_field_without_quotes():
    assert gui._path_text(_Var('  "mods/Out"  ')) == "mods/Out"


def test_the_command_line_output_option_takes_quotes_off():
    p = ac._build_parser()
    ns = p.parse_args(["auto", "-o", '"mods/Out"'])
    assert ns.output == Path("mods/Out")
    ns = p.parse_args(["auto", "-o", "mods/Out"])
    assert ns.output == Path("mods/Out")


def test_every_output_option_of_the_command_line_does():
    p = ac._build_parser()
    assert p.parse_args(["merge", "a.esp", "b.esp", "-o", '"x.esp"']).output == Path("x.esp")
    assert p.parse_args(["convert", "src", "-o", '"mods/Out"']).output == Path("mods/Out")


def test_a_settings_path_row_is_saved_without_quotes():
    """The settings tab keeps what is typed; a path row is cleaned on the way in."""
    src = (Path(gui.__file__)).read_text(encoding="utf-8")
    assert "_path_text(v) if kd == \"path\"" in src


# --- 2. the preflight -----------------------------------------------------------

def test_a_usable_folder_passes_and_leaves_no_probe_behind(tmp_path):
    out = tmp_path / "new" / "deeper"
    assert paths.preflight_output_folder(out) is None
    assert out.is_dir() and list(out.iterdir()) == []


def test_a_folder_with_quotes_round_it_is_refused_with_a_reason(tmp_path):
    why = paths.preflight_output_folder(f'"{tmp_path}/out"')
    assert why and "OSError" in why


def test_a_folder_under_a_file_is_refused(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    assert paths.preflight_output_folder(f / "out")


class _Stop(Exception):
    pass


def _stub_run(monkeypatch, tmp_path):
    data = tmp_path / "Data"
    data.mkdir(exist_ok=True)
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
    monkeypatch.setattr(ac, "_preflight_vanilla_sweep", lambda d: (True, "ok"))
    seen = {"converted": 0}

    def _convert(conv):
        seen["converted"] += 1
        raise _Stop()
    monkeypatch.setattr(ac, "_cmd_convert", _convert)
    return seen


def _args(output, **kw):
    base = dict(output=output, workers=1, no_textures=False, merged_name="C.esp",
                list_only=False, only_mods=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_an_unusable_output_folder_stops_the_run_before_any_mod(monkeypatch, tmp_path, capsys):
    seen = _stub_run(monkeypatch, tmp_path)
    bad = Path(f'"{tmp_path}/out"')
    assert ac._cmd_auto(_args(bad)) == 2
    assert seen["converted"] == 0
    out = capsys.readouterr().out
    assert "the output folder cannot be used" in out and "Nothing was converted" in out


def test_a_usable_output_folder_goes_on_to_convert(monkeypatch, tmp_path):
    seen = _stub_run(monkeypatch, tmp_path)
    with pytest.raises(_Stop):
        ac._cmd_auto(_args(tmp_path / "out"))
    assert seen["converted"] == 1 and (tmp_path / "out").is_dir()


def test_a_dry_run_does_not_try_the_folder(monkeypatch, tmp_path):
    """--list-only converts nothing and writes nothing: it must not create the
    output folder just to test it. #dry-run-writes-nothing"""
    seen = _stub_run(monkeypatch, tmp_path)
    assert ac._cmd_auto(_args(tmp_path / "never", list_only=True)) == 0
    assert seen["converted"] == 0 and not (tmp_path / "never").exists()
