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

r"""#coverage-gaps-file -- GitHub issue #26.

The coverage report named only the first 5 (or 3) armours of each list and said
"... and 75 more", and the other 75 were written nowhere: not the log, not the
failures file, not the output mod. A person told to "convert the mod that ships
the female mesh" could not tell which mod, or whether it mattered.

The console stays short. Every entry of every list is now written, untruncated,
to `coverage_gaps.tsv` in the output mod (list, plugin, form id, EDID, detail),
and each "... and N more" line says where. Three lists that were cut at 5 with
no "more" line at all now have one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac                               # noqa: E402


def _armours(n, plugin="ModA.esp"):
    return [((plugin, 0x800 + i), f"Armor{i}") for i in range(n)]


def _read(folder):
    text = (Path(folder) / ac.COVERAGE_GAPS_NAME).read_bytes().decode("utf-8")
    lines = text.split("\n")
    assert lines[-1] == ""                      # ends with one newline
    return [ln.split("\t") for ln in lines[:-1]]


# --- the rows ---------------------------------------------------------------------

def test_an_armour_entry_becomes_plugin_form_id_and_edid():
    row = ac._coverage_gap_row("withheld", (("ModA.esp", 0x801), "IronCuirass"))
    assert row == {"list": "withheld", "plugin": "ModA.esp", "form_id": "000801",
                   "edid": "IronCuirass", "detail": ""}


def test_an_entry_with_a_reason_keeps_the_reason():
    row = ac._coverage_gap_row("hands_feet_not_covered",
                               (("ModA.esp", 0x801), "Gloves", "unresolved"))
    assert (row["edid"], row["detail"]) == ("Gloves", "unresolved")


def test_a_left_to_another_mod_entry_names_the_mod():
    row = ac._coverage_gap_row("left_to_other_mod",
                               (("ModA.esp", 0x801), ("Cuirass", "OtherPatch")))
    assert (row["edid"], row["detail"]) == ("Cuirass", "patched by OtherPatch")


def test_a_dict_entry_keeps_every_field():
    row = ac._coverage_gap_row("female_kept", {"slot": "MOD3", "kept": "a.nif", "male": "b.nif"})
    assert row["detail"] == "slot=MOD3; kept=a.nif; male=b.nif"


def test_a_plugin_and_form_id_string_is_split():
    row = ac._coverage_gap_row("beast_variant", "mod.esp|801")
    assert (row["plugin"], row["form_id"], row["detail"]) == ("mod.esp", "801", "mod.esp|801")


def test_an_odd_entry_is_kept_as_text_not_dropped():
    row = ac._coverage_gap_row("x", 42)
    assert row["detail"] == "42"


# --- the file ---------------------------------------------------------------------

def test_every_entry_is_written_not_just_the_first_five(tmp_path):
    n = ac._write_coverage_gaps(tmp_path, (("withheld", _armours(80)),))
    assert n == ac.COVERAGE_GAPS_NAME
    rows = _read(tmp_path)
    assert rows[0] == ["list", "plugin", "form_id", "edid", "detail"]
    assert len(rows) == 81 and rows[-1][3] == "Armor79"


def test_nothing_to_write_writes_nothing(tmp_path):
    assert ac._write_coverage_gaps(tmp_path, (("withheld", []),)) is None
    assert not (tmp_path / ac.COVERAGE_GAPS_NAME).exists()
    assert ac._write_coverage_gaps(None, (("withheld", _armours(3)),)) is None


def test_a_tab_or_line_break_in_a_value_cannot_break_a_row(tmp_path):
    ac._write_coverage_gaps(tmp_path, (("female_kept", [{"slot": "a\tb\nc"}]),))
    rows = _read(tmp_path)
    assert len(rows) == 2 and len(rows[1]) == 5


# --- the report -------------------------------------------------------------------

def _stats(n=80):
    return [{"world_mesh_skipped": [f"mod.esp|{i:X}" for i in range(n)],
             "world_mesh_dropped": _armours(n)}]


def test_the_log_says_where_the_rest_of_a_cut_list_is(tmp_path, capsys):
    ac._report_coverage_holds(_stats(80), out_dir=tmp_path)
    out = capsys.readouterr().out
    assert f"... and 75 more (full list: {ac.COVERAGE_GAPS_NAME})" in out
    rows = _read(tmp_path)
    assert sum(1 for r in rows if r[0] == "world_mesh_not_covered") == 80
    assert sum(1 for r in rows if r[0] == "world_mesh_not_minted") == 80


def test_without_an_output_folder_nothing_is_written_and_the_log_is_as_before(capsys):
    ac._report_coverage_holds(_stats(80))
    out = capsys.readouterr().out
    assert "... and 75 more" in out and "full list" not in out


def test_a_report_with_nothing_to_say_writes_no_file(tmp_path, capsys):
    ac._report_coverage_holds([{}], out_dir=tmp_path)
    assert capsys.readouterr().out == ""
    assert not (tmp_path / ac.COVERAGE_GAPS_NAME).exists()


def test_the_female_not_covered_list_now_says_more(tmp_path, capsys):
    stats = [{"female_kept": [{"slot": "MOD3", "kept": "a.nif", "male": "b.nif"}],
              "female_guard_dropped": _armours(12)}]
    ac._report_coverage_holds(stats, out_dir=tmp_path)
    out = capsys.readouterr().out
    assert f"... and 7 more (full list: {ac.COVERAGE_GAPS_NAME})" in out
    rows = _read(tmp_path)
    assert sum(1 for r in rows if r[0] == "female_not_covered") == 12


def test_the_hands_and_feet_lists_now_say_more(tmp_path, capsys):
    stats = [{"nude_skipped": [{"why": "unresolved", "arma": f"m.esp|{i:X}"} for i in range(9)],
              "nude_dropped": [(a, e, "unresolved") for a, e in _armours(9)]}]
    ac._report_coverage_holds(stats, out_dir=tmp_path)
    out = capsys.readouterr().out
    assert out.count(f"... and 4 more (full list: {ac.COVERAGE_GAPS_NAME})") == 2


def test_the_run_hands_the_output_folder_to_the_report(tmp_path, monkeypatch):
    """The wiring: `_emit_unified_coverage_patches` is what knows the output mod."""
    from tests.test_coverage_world_mesh import _emit
    seen = {}
    monkeypatch.setattr(ac, "_report_coverage_holds",
                        lambda stats, out_dir=None: seen.update(out_dir=out_dir))
    _emit(tmp_path, monkeypatch)
    assert seen.get("out_dir") == tmp_path / "out"

