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

"""#esp-report-pairing -- the run log and the per-mod report name the plugin
that made each patch.

THE DEFECT. Both zipped every source plugin FOUND with every patch WRITTEN. A
plugin with no armour (or a failed one) writes no patch, so every later pair
shifted onto the wrong plugin: 'A House For Anska.esp -> Nibenean Armors UBE
patch.esp'-style lines, and a report giving one plugin's stats under another's
name. Text only: no output file changes.
"""
import json
from pathlib import Path

from src import auto_convert as ac
from src.esp import ESP, TES4Header, Group
from tests.test_plugins_only_refresh import _mk_mod

NEW = " (CBBEtoUBE src).esp"


def _mod_with_an_armourless_plugin_first(tmp_path):
    src = _mk_mod(tmp_path)
    # Sorts before MyMod.esp at the same depth and carries no ARMA group.
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[Group(label=b"KYWD", records=[])]).save(src / "Aaa Quest.esp")
    return src


def _convert(tmp_path, monkeypatch):
    src = _mod_with_an_armourless_plugin_first(tmp_path)
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: [])
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    return ac.auto_convert_mod(src, tmp_path / "out", ube_body_ref_path=ref,
                               master_data_dirs=[tmp_path], nif_workers=1)


def test_the_log_names_the_plugin_that_made_the_patch(tmp_path, monkeypatch):
    r = _convert(tmp_path, monkeypatch)
    assert [p.name for p in r.source_esps] == ["Aaa Quest.esp", "MyMod.esp"]
    assert r.esp_skipped_no_armor == 1
    lines = ac._esp_patch_log_lines(r)
    assert lines[0].strip() == "source ESPs: 2 (1 patched)"
    assert [ln.strip() for ln in lines[1:]] == [f"[0] MyMod.esp -> MyMod{NEW}"]


def test_the_report_gives_each_patch_its_own_plugin_and_stats(tmp_path, monkeypatch):
    r = _convert(tmp_path, monkeypatch)
    rep = tmp_path / "report.txt"
    r.write_report(rep)
    text = rep.read_text(encoding="utf-8").splitlines()
    assert "ESP (1 patched of 2 plugin(s) found)" in text
    srcs = [ln.split(":", 1)[1].strip() for ln in text
            if ln.startswith("  source         :")]
    outs = [ln.split(":", 1)[1].strip() for ln in text
            if ln.startswith("  output         :")]
    assert [Path(s).name for s in srcs] == ["MyMod.esp"]
    assert [Path(o).name for o in outs] == [f"MyMod{NEW}"]
    # The stats printed are that patch's own.
    assert any(ln.startswith("  masters") for ln in text)


def test_the_plugins_only_replay_pairs_by_plugin_too(tmp_path, monkeypatch):
    """The ESP-only refresh records the same pairs: the armourless plugin
    first is skipped, and the one patch stays with its own plugin."""
    src = _mod_with_an_armourless_plugin_first(tmp_path)
    out = tmp_path / "out"
    pdir = out / "_unmerged_patches"
    pdir.mkdir(parents=True)
    for stem in ("Aaa Quest", "MyMod"):
        Path(str(pdir / f"{stem}{NEW}") + ".espgen.json").write_text(json.dumps({
            "source_esp": str(src / f"{stem}.esp"),
            "converted_rel_paths": ["armor/m/body_0.nif"],
            "body_mesh_rel_paths": []}), encoding="utf-8")
    r = ac.refresh_mod_esp(src, out, master_data_dirs=[tmp_path])
    assert [(s.name, o.name) for s, o, _ in r.esp_patched] == [
        ("MyMod.esp", f"MyMod{NEW}")]
    assert [ln.strip() for ln in ac._esp_patch_log_lines(r)[1:]] == [
        f"[0] MyMod.esp -> MyMod{NEW}"]
