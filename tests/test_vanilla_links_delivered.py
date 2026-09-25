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

"""#vanilla-links-delivered -- the "vanilla sweep linked nothing" warning reads
the links the game will load.

THE DEFECT. The warning summed the vanilla sweep source's own patch links. When
the winner-scan coverage is the sole generator those per-source patches are
left unmerged, so a coverage change that dropped every vanilla armour still
passed the check: the sweep's own (unloaded) patch had links, and the run
printed "vanilla coverage: 0" as a plain line. Now the delivered count decides
there; the fallback merge, which ships the per-source patches, keeps the sweep
source's own count."""
import argparse
import json
from pathlib import Path

from src import auto_convert as ac
from src import preflight as pf

VAN = "filterByArmors=Skyrim.esm|012E49:armorAddonsToAdd=CBBE_to_UBE_Combined.esp|800"
MOD = "filterByArmors=SomeMod.esp|000801:armorAddonsToAdd=CBBE_to_UBE_Combined.esp|801"


def _sweep_dir(tmp_path):
    d = tmp_path / "Data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Skyrim.esm").write_bytes(b"")
    return d


def _result(src, links):
    return (src, ac.AutoConvertResult(
        source_dir=src, output_dir=src,
        esp_stats_list=[{"skypatcher_link_targets": links}]), None)


# ---------------------------------------------------------------- the decision

def test_sole_coverage_with_no_delivered_vanilla_link_is_dead(tmp_path):
    """The reported shape: the sweep's own patch has links, the delivered INI
    has none -- and only the delivered INI is loaded."""
    res = [_result(_sweep_dir(tmp_path), 40)]
    assert ac._vanilla_links_check([MOD], res, True) == (0, True)


def test_sole_coverage_with_a_delivered_vanilla_link_is_alive(tmp_path):
    """Negative control: the sweep's own count no longer decides here."""
    res = [_result(_sweep_dir(tmp_path), 0)]
    assert ac._vanilla_links_check([MOD, VAN], res, True) == (1, False)


def test_the_fallback_merge_keeps_the_sweep_sources_count(tmp_path):
    """The per-source patches ship there: a mod's link to a vanilla record must
    not mask a dead sweep."""
    res = [_result(_sweep_dir(tmp_path), 0)]
    assert ac._vanilla_links_check([VAN], res, False) == (1, True)
    res = [_result(_sweep_dir(tmp_path), 7)]
    assert ac._vanilla_links_check([MOD], res, False) == (0, False)


def test_no_sweep_this_batch_asserts_nothing(tmp_path):
    src = tmp_path / "SomeMod"
    src.mkdir()
    assert ac._vanilla_links_check([MOD], [_result(src, 0)], True) == (0, False)


def test_only_vanilla_and_dlc_targets_are_counted(tmp_path):
    lines = [VAN, MOD, VAN.replace("Skyrim.esm", "DAWNGUARD.ESM"),
             "; " + VAN, "armorAddonsToAdd=Skyrim.esm|1"]
    res = [_result(_sweep_dir(tmp_path), 1)]
    assert ac._vanilla_links_check(lines, res, True)[0] == 2


# ------------------------------------------------------------------ the batch

def _ns(sources, output):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def _run(tmp_path, monkeypatch, capsys, delivered):
    """A batch whose only source is the vanilla sweep (its own patch reports 40
    links) and whose winner-scan coverage is the sole generator, delivering
    `delivered` INI lines."""
    mods = tmp_path / "mods"
    mods.mkdir()
    out = tmp_path / "out"
    settings = tmp_path / "CBBEtoUBE_settings.json"
    settings.write_text("{}", encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data",
                        lambda *a, **k: tmp_path / "SkyPatcher.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)
    src = _sweep_dir(tmp_path)

    def _converted(source_dir, *a, **k):
        patches = out / "_unmerged_patches"
        patches.mkdir(parents=True, exist_ok=True)
        (patches / "Vanilla UBE patch.esp").write_bytes(b"")
        return ac.AutoConvertResult(
            source_dir=Path(source_dir), output_dir=out,
            esp_stats_list=[{"skypatcher_link_targets": 40}])

    def _emit(output, patches_dir, *a, **k):
        (patches_dir / "UBE_ModBody_Coverage UBE patch.esp").write_bytes(b"")
        return (True, 5, True)

    monkeypatch.setattr(ac, "auto_convert_mod", _converted)
    monkeypatch.setattr(ac.ube_patcher, "restore_female_models", lambda *a, **k: {})
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches", _emit)
    monkeypatch.setattr(ac.ube_patcher, "merge_patches_split",
                        lambda *a, **k: {"skypatcher_ini_lines": list(delivered),
                                         "skypatcher_targets": len(delivered)})
    ac._cmd_convert(_ns([src], out))
    return capsys.readouterr().out


def test_the_batch_warns_when_the_delivered_ini_drops_vanilla(
        tmp_path, monkeypatch, capsys):
    log = _run(tmp_path, monkeypatch, capsys, [MOD])
    assert "[unified/3c]" in log, log[-3000:]
    assert "vanilla coverage: 0 vanilla/DLC" in log
    assert "the delivered coverage links 0 vanilla/DLC records" in log, log[-3000:]


def test_the_batch_is_quiet_when_vanilla_is_delivered(
        tmp_path, monkeypatch, capsys):
    log = _run(tmp_path, monkeypatch, capsys, [MOD, VAN])
    assert "vanilla coverage: 1 vanilla/DLC" in log, log[-3000:]
    assert "links 0 vanilla/DLC records" not in log
    assert "linked 0 records" not in log
