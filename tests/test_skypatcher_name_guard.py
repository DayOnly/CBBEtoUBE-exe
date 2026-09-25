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

r"""#skypatcher-name-guard -- a plugin name SkyPatcher would split gets no line.

THE DEFECT. The merge wrote `filterByArmors=<plugin>|<id>:armorAddonsToAdd=...`
with the plugin's file name as it is. SkyPatcher splits a form list on `,`, a
line on `:` and `=`, and `;` starts a comment, so a plugin named
`Armors, Extra.esp` became two forms, neither resolving: the armour lost its
UBE armature in silence behind a line that looked fine. Now no line is written
for it, the links are counted in the reconciliation, and the plugin is named in
a run warning. Live: 1 active plugin has a comma; no armour of it is linked.
`CBBE2UBE_NO_SKYPATCHER_NAME_GUARD=1` writes such lines again.
"""
import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from tests.test_full_skypatcher import _gen, _mk_env

OFF = "CBBE2UBE_NO_SKYPATCHER_NAME_GUARD"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _merge(tmp_path, odd):
    _mk_env(tmp_path)
    p1, _ = _gen(tmp_path, "ModA.esp", "a")
    p2, _ = _gen(tmp_path, odd, "b")
    return up.merge_patches_split([p1, p2], tmp_path / "Combined.esp",
                                  master_data_dirs=[tmp_path])


def _targets(stats):
    return sorted(l.split("=", 1)[1].split(":", 1)[0]
                  for l in stats["skypatcher_ini_lines"])


@pytest.mark.parametrize("odd", ["Armors, Extra.esp", "Armors; Extra.esp"],
                         ids=["comma", "semicolon"])
def test_a_plugin_name_skypatcher_splits_gets_no_line(tmp_path, odd):
    st = _merge(tmp_path, odd)
    low = odd.lower()
    assert _targets(st) == ["moda.esp|000801", "moda.esp|000803"]
    assert st["sp_unsafe_name_targets"] == [f"{low}|000801", f"{low}|000803"]
    assert st["sp_dropped_unsafe_name"] == 2 and st["skypatcher_targets"] == 2


def test_an_equals_sign_in_a_plugin_name_keeps_its_line(tmp_path):
    """A pair splits once, at its first `=`: the INI reader reads the name
    back whole, so the line is written and resolves."""
    st = _merge(tmp_path, "Armors=Extra.esp")
    assert "armors=extra.esp|000801" in _targets(st) and len(_targets(st)) == 4
    assert st["sp_unsafe_name_targets"] == []
    line = next(l for l in st["skypatcher_ini_lines"] if "armors=extra" in l)
    assert ac._skypatcher_fields(line)["filterByArmors"] == "armors=extra.esp|000801"


def test_the_dropped_links_balance_the_reconciliation(tmp_path):
    st = _merge(tmp_path, "Armors, Extra.esp")
    out = up.report_link_reconciliation(st)
    assert not any("does not balance" in l for l in out), out
    assert "2 on a plugin name SkyPatcher cannot read" in out[0], out


def test_switched_off_the_line_is_written_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st = _merge(tmp_path, "Armors, Extra.esp")
    assert "armors, extra.esp|000801" in _targets(st) and len(_targets(st)) == 4
    assert st["sp_unsafe_name_targets"] == []


def test_an_output_name_skypatcher_splits_writes_no_line(tmp_path):
    """The Combined's own name is in every line's addon list."""
    _mk_env(tmp_path)
    p1, _ = _gen(tmp_path, "ModA.esp", "a")
    st = up.merge_patches_split([p1], tmp_path / "Combined, Mine.esp",
                                master_data_dirs=[tmp_path])
    assert st["skypatcher_ini_lines"] == []
    assert st["sp_unsafe_name_targets"] == ["moda.esp|000801", "moda.esp|000803"]
    assert st["sp_unsafe_output_names"] == ["Combined, Mine.esp"]


def test_an_output_name_that_splits_is_named_not_the_armour_plugins(
        monkeypatch, capsys):
    """The armour plugins are fine: the run names the merged plugin and tells
    the user to choose another --merged-name."""
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    n = ac._report_skypatcher_unsafe_names(
        {"sp_unsafe_name_targets": ["moda.esp|000801", "moda.esp|000803",
                                    "odd, one.esp|000801"],
         "sp_unsafe_output_names": ["Combined, Mine.esp"]})
    out = capsys.readouterr().out
    assert n == 2
    assert 'the merged plugin "Combined, Mine.esp" gets no SkyPatcher lines' in out
    assert "--merged-name" in out and '"moda.esp"' not in out
    assert '1 armour record(s) of the plugin "odd, one.esp"' in out
    assert [f["source"] for f in ac._RUN_FAILURES] == ["Combined, Mine.esp",
                                                     "odd, one.esp"]


def test_a_plain_name_is_untouched(tmp_path):
    st = _merge(tmp_path, "ModB.esp")
    assert len(_targets(st)) == 4 and st["sp_unsafe_name_targets"] == []


def test_the_plugin_is_named_in_a_run_warning(monkeypatch, capsys):
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    n = ac._report_skypatcher_unsafe_names(
        {"sp_unsafe_name_targets": ["armors, extra.esp|000801",
                                    "armors, extra.esp|000803"]})
    assert n == 1
    out = capsys.readouterr().out
    assert '2 armour record(s) of the plugin "armors, extra.esp"' in out
    assert "FIX: rename the plugin" in out
    assert [(f["kind"], f["source"], f["severity"]) for f in ac._RUN_FAILURES] == [
        ("armour not delivered", "armors, extra.esp", "warning")]


def test_nothing_to_name_says_nothing(monkeypatch, capsys):
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    assert ac._report_skypatcher_unsafe_names({"sp_unsafe_name_targets": []}) == 0
    assert capsys.readouterr().out == "" and ac._RUN_FAILURES == []
