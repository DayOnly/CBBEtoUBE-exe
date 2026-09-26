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

"""#settings-import-guard -- a bad import changes nothing; a failed export says so.

Measured 2026-09-25 on 94340ee: Import of a torn preset, or of the exclusions
file picked by mistake, loaded as ALL DEFAULTS, was saved over the user's
recipe and reported "Settings imported". Export to a folder that cannot be
written reported "Settings exported". A hand-edited "false" read as ON."""
import json

import pytest

from src import gui
from src import gui_settings as gs

RECIPE = {"conform_to_body": False, "layer_order_last": True}


@pytest.mark.parametrize("text,why", [
    pytest.param('{"conform_to_body": fa', "not a readable settings file", id="torn"),
    pytest.param('["conform_to_body"]', "not a readable settings file", id="a-list"),
    pytest.param('{"exclude": ["SomeMod"], "reviewed": {"armor": true}}',
                 "holds no CBBEtoUBE settings", id="exclusions-file"),
    pytest.param("{}", "holds no CBBEtoUBE settings", id="empty"),
])
def test_a_file_that_is_not_a_settings_file_is_refused(tmp_path, text, why):
    p = tmp_path / "picked.json"
    p.write_text(text, encoding="utf-8")
    vals, reason = gs.load_for_import(p)
    assert vals is None and why in reason, reason


def test_a_missing_file_is_refused(tmp_path):
    vals, reason = gs.load_for_import(tmp_path / "gone.json")
    assert vals is None and "does not exist" in reason


def test_an_exported_recipe_imports_whole(tmp_path):
    p = tmp_path / "preset.json"
    assert gs.save_values({**gs.defaults(), **RECIPE}, p)
    vals, reason = gs.load_for_import(p)
    assert reason == "" and {k: vals[k] for k in RECIPE} == RECIPE


def test_an_all_defaults_export_still_imports(tmp_path):
    """save_values writes only `_known_settings` for an all-default recipe;
    importing it is a legitimate reset and must not be refused."""
    p = tmp_path / "defaults.json"
    assert gs.save_values(gs.defaults(), p)
    assert set(json.loads(p.read_text(encoding="utf-8"))) == {gs.KNOWN_KEYS_FIELD}
    vals, reason = gs.load_for_import(p)
    assert reason == "" and vals == gs.defaults()


def test_a_newer_builds_file_imports_what_this_build_knows(tmp_path):
    p = tmp_path / "newer.json"
    p.write_text(json.dumps({"conform_to_body": False, "a_future_option": 3}),
                 encoding="utf-8")
    vals, reason = gs.load_for_import(p)
    assert reason == "" and vals["conform_to_body"] is False


def test_a_failed_export_is_reported_by_the_return_value(tmp_path):
    """save_values never raises: a write it could not make is False. The
    window's Export now reads that instead of waiting for an exception."""
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("x", encoding="utf-8")
    assert gs.save_values(gs.defaults(), blocked / "preset.json") is False
    ok, msg = gui.export_settings_file(gs.defaults(), blocked / "preset.json")
    assert ok is False and "Could not write" in msg, msg
    ok, msg = gui.export_settings_file(gs.defaults(), tmp_path / "preset.json")
    assert ok is True and msg == "Settings exported: preset.json"


# --- the Import button ------------------------------------------------------------

def test_importing_the_exclusions_file_by_mistake_changes_nothing(tmp_path):
    p = tmp_path / "CBBEtoUBE_exclusions.json"
    p.write_text('{"exclude": ["SomeMod"]}', encoding="utf-8")
    applied = []
    ok, msg = gui.import_settings_file(p, lambda vals: applied.append(vals) or True)
    assert ok is False and applied == [], "the recipe was overwritten"
    assert "Nothing was changed" in msg, msg


def test_a_good_preset_is_applied_and_a_failed_save_is_said(tmp_path):
    p = tmp_path / "preset.json"
    assert gs.save_values({**gs.defaults(), **RECIPE}, p)
    applied = []
    ok, msg = gui.import_settings_file(p, lambda vals: applied.append(vals) or True)
    assert ok is True and msg == "Settings imported: preset.json"
    assert {k: applied[0][k] for k in RECIPE} == RECIPE
    ok, msg = gui.import_settings_file(p, lambda vals: False)
    assert ok is True and "could NOT be saved" in msg, msg


@pytest.mark.parametrize("raw,want", [
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ("true", True), ("1", True), ("yes", True), (" ON ", True),
    (False, False), (True, True), (0, False), (1, True),
])
def test_a_bool_setting_reads_words_the_way_the_environment_does(raw, want):
    s = gs.by_key()["layer_order_last"]
    assert gs._coerce(s, raw) is want, raw


def test_a_blank_bool_keeps_its_default():
    for key in ("layer_order_last", "conform_to_body"):
        s = gs.by_key()[key]
        assert gs._coerce(s, "  ") is s.default


def test_a_hand_edited_false_loads_as_off(tmp_path):
    p = tmp_path / "edited.json"
    p.write_text('{"conform_to_body": "false", "layer_order_last": "1"}',
                 encoding="utf-8")
    vals = gs.load_values(p)
    assert vals["conform_to_body"] is False and vals["layer_order_last"] is True
