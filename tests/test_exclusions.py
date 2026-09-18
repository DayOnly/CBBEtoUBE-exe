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

"""Guards for the persistent exclusion model (the un-ignorable Run-tab safety
gate's data layer)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import exclusions as ex


def test_empty_and_roundtrip(tmp_path):
    p = tmp_path / "excl.json"
    st = ex.empty()
    assert ex.excluded_names(st, "armor") == []
    ex.set_excluded(st, "armor", ["Mod B", "Mod A"], reason="r", source="manual")
    assert ex.save(st, p)
    st2 = ex.load(p)
    assert ex.excluded_names(st2, "armor") == ["Mod A", "Mod B"]  # sorted
    assert st2["armor"]["Mod A"]["reason"] == "r"
    assert ex.excluded_names(st2, "overlay") == []


def test_load_malformed_is_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert ex.load(p) == ex.empty()
    assert ex.load(tmp_path / "missing.json") == ex.empty()


def test_set_excluded_preserves_existing_meta(tmp_path):
    st = ex.empty()
    reason = ex.UBE_NATIVE_REASON
    st["armor"]["KeepMe"] = {"reason": reason, "source": "ube-auto"}
    # re-set including KeepMe + a new manual one; KeepMe keeps its auto meta
    ex.set_excluded(st, "armor", ["KeepMe", "NewOne"], source="manual")
    assert st["armor"]["KeepMe"]["source"] == "ube-auto"
    assert st["armor"]["KeepMe"]["reason"] == reason
    assert st["armor"]["NewOne"]["source"] == "manual"
    # dropping a name removes it
    ex.set_excluded(st, "armor", ["NewOne"])
    assert ex.excluded_names(st, "armor") == ["NewOne"]


def test_scan_names_ube_token_boundaries():
    names = ["UBE 2.0 U. 0.7", "ube_body", "MyUBEArmor", "Cube Armor",
             "Tube Top", "Nord Plate", "UBEArmor Pack", "someUBE"]
    props = {p["name"] for p in ex.scan_names(names)}
    # matches: a standalone `ube` token (digits ok), or ube at a non-letter edge
    assert "UBE 2.0 U. 0.7" in props
    assert "ube_body" in props
    # non-matches: `ube` glued to a letter is NOT flagged (conservative -- the
    # deep mesh detector catches "UBEArmor"-style names; the name pass avoids
    # false positives like Cube/Tube/Uber).
    assert "UBEArmor Pack" not in props
    assert "MyUBEArmor" not in props
    assert "Cube Armor" not in props
    assert "Tube Top" not in props
    assert "Nord Plate" not in props
    assert "someUBE" not in props


def test_scan_names_skips_existing():
    props = ex.scan_names(["ube_body", "OtherUBE thing"],
                          existing=["ube_body"])
    assert all(p["name"] != "ube_body" for p in props)


def test_config_path_is_the_tool_folder(monkeypatch):
    """Beside the settings file, in the tool's own folder. #tool-folder-only"""
    from src import gui_settings, paths
    monkeypatch.delenv("CBBE2UBE_EXCLUSIONS", raising=False)
    monkeypatch.delenv("CBBE2UBE_CONFIG", raising=False)
    assert ex.config_path() == paths.tool_dir() / "CBBEtoUBE_exclusions.json"
    assert ex.config_path().parent == gui_settings.config_path().parent, (
        "exclusions and settings must live in the same folder")


def test_appdata_is_never_read_moved_or_created(tmp_path, monkeypatch):
    """THE REPORT: a user replaced the tool folder and their exclusions were
    still there, because 1.1-1.4.1 kept them under %LOCALAPPDATA%. The tool must
    not read that old file, must not move it, and must not create an AppData
    folder of its own. #tool-folder-only"""
    from src import paths
    tool = tmp_path / "tool"
    tool.mkdir()
    local = tmp_path / "local"
    old = local / "CBBEtoUBE" / "CBBEtoUBE_exclusions.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"armor": {"Old Mod": {"reason": "", "source": "manual"}}}',
                   encoding="utf-8")
    roaming = tmp_path / "roaming"                  # does not exist
    monkeypatch.delenv("CBBE2UBE_EXCLUSIONS", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setattr(paths, "tool_dir", lambda: tool)

    p = ex.config_path()
    assert p == tool / "CBBEtoUBE_exclusions.json"
    assert ex.load() == ex.empty(), "the old AppData exclusions were read"
    st = ex.empty()
    ex.set_excluded(st, "armor", ["New Mod"])
    assert ex.save(st)
    assert ex.excluded_names(ex.load(p), "armor") == ["New Mod"]
    assert old.is_file() and "Old Mod" in old.read_text(encoding="utf-8"), (
        "the old AppData file was moved, rewritten or deleted")
    assert not roaming.exists(), "an AppData folder was created"
    assert [x.name for x in local.iterdir()] == ["CBBEtoUBE"]


def test_config_path_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_EXCLUSIONS", str(tmp_path / "x.json"))
    assert ex.config_path() == tmp_path / "x.json"
