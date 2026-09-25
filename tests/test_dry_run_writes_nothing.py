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

"""#dry-run-writes-nothing -- "Dry run (list mods, convert nothing)" converts nothing.

Measured 2026-09-25 on 94340ee: with Dry run ticked, Convert armor off and
Convert overlays on, the window launched `auto --list-only --overlays-only`,
and `_cmd_auto` handled --overlays-only BEFORE --list-only: the overlay
transfer ran, rebaked every overlay into the output mod and reported success.

Every combination the window can build under Dry run is run here through the
real `auto` entry, with the writers replaced by recorders."""
import pytest

from src import auto_convert as ac
from src import gui
from src import overlay_transfer as ot


def _modlist(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    (mods / "SomeMod").mkdir(parents=True)
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_NO_VANILLA_SWEEP", "1")
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac, "_find_armor_mod_dirs", lambda *a, **k: [
        {"name": "SomeMod", "path": mods / "SomeMod", "armor_nifs": 1}])
    monkeypatch.setattr(ac, "_drop_ube_native_candidates", lambda c: c)
    monkeypatch.setattr(ot, "discover_overlays", lambda *a, **k: {
        "body": {"textures/actors/character/overlays/tattoo.dds":
                 ("loose", mods / "InkMod" / "tattoo.dds", "InkMod")},
        "hands": {}, "feet": {}})
    writes = []
    monkeypatch.setattr(ot, "convert_overlays",
                        lambda *a, **k: writes.append("overlays") or {"converted": 1})
    monkeypatch.setattr(ac, "_cmd_convert",
                        lambda conv: writes.append("armor") or 0)
    return writes


@pytest.mark.parametrize("armor,overlay", [(True, False), (False, True), (True, True)])
def test_a_dry_run_from_the_window_writes_nothing(armor, overlay, tmp_path,
                                                  monkeypatch, capsys):
    writes = _modlist(tmp_path, monkeypatch)
    out = tmp_path / "out"
    argv = (["auto", "-o", str(out)] + gui._run_mode_argv(True, armor, overlay)
            + (["--overlay-skip-male"] if overlay else []))
    rc = ac.main(argv)
    log = capsys.readouterr().out
    assert rc == 0, log[-1500:]
    assert writes == [], f"{argv} reached a writer: {writes}"
    assert not out.exists(), f"{argv} created the output mod"


def test_overlays_only_lists_what_it_would_remap(tmp_path, monkeypatch, capsys):
    writes = _modlist(tmp_path, monkeypatch)
    rc = ac.main(["auto", "-o", str(tmp_path / "out"), "--overlays-only",
                  "--list-only"])
    log = capsys.readouterr().out
    assert rc == 0 and writes == []
    assert "body: 1 overlay(s)" in log, log
    assert "InkMod  (1)" in log, log
    assert "nothing was written" in log, log


def test_the_same_argv_without_dry_run_still_converts(tmp_path, monkeypatch, capsys):
    """The control: the recorders DO see a real run, so the zero above is not
    a harness that measures nothing."""
    writes = _modlist(tmp_path, monkeypatch)
    rc = ac.main(["auto", "-o", str(tmp_path / "out")]
                 + gui._run_mode_argv(False, False, True))
    capsys.readouterr()
    assert rc == 0 and writes == ["overlays"]


def test_the_window_builds_the_mode_flags_it_always_did_outside_a_dry_run():
    assert gui._run_mode_argv(False, True, False) == []
    assert gui._run_mode_argv(False, False, True) == ["--overlays-only"]
    assert gui._run_mode_argv(False, True, True) == ["--convert-overlays"]


def test_a_dry_run_never_carries_the_armor_plus_overlay_flag():
    assert gui._run_mode_argv(True, True, True) == ["--list-only"]
    assert gui._run_mode_argv(True, False, True) == ["--list-only", "--overlays-only"]


def test_the_planner_skips_our_own_output_mod(tmp_path, monkeypatch):
    """The listing reads the sources a transfer would: never the output mod."""
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    seen = {}
    monkeypatch.setattr(ot, "discover_overlays",
                        lambda lay, regions, skip_mods=(), only_mods=None:
                        seen.update(skip=set(skip_mods)) or {})
    ot.plan_overlays(mods / "CBBEtoUBE Auto", None, exclude_mods=["Gone"])
    assert seen["skip"] == {"CBBEtoUBE Auto", "Gone"}, seen


# --- #dry-run-copy-mode: the list is for the mode the real run would use ----------

_OV = "textures/actors/character/overlays/inkset"


def _paint_modlist(tmp_path, monkeypatch, *, compiler=False, base=False,
                   texconv=True):
    """One overlay mod on disk: a texture a RaceMenu script registers, one it
    does not, and a registration whose texture is missing. The tools the copy
    mode needs are present or not as asked."""
    mods = tmp_path / "mods"
    ink = mods / "InkMod"
    (ink / _OV).mkdir(parents=True)
    for name in ("registered.dds", "unregistered.dds"):
        (ink / _OV / name).write_bytes(b"DDS ")
    (ink / "Scripts" / "Source").mkdir(parents=True)
    (ink / "Scripts" / "Source" / "InkSetMenu.psc").write_text(
        'Event OnInit()\n'
        '  AddBodyPaint("Ink", "Actors\\\\Character\\\\Overlays\\\\inkset\\\\registered.dds")\n'
        '  AddBodyPaint("Gone", "Actors\\\\Character\\\\Overlays\\\\inkset\\\\missing.dds")\n'
        'EndEvent\n', encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: None)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: None)
    tool = tmp_path / "tools" / "texconv.exe"
    tool.parent.mkdir()
    tool.write_bytes(b"")
    pc = tmp_path / "game" / "Papyrus Compiler" / "PapyrusCompiler.exe"
    pc.parent.mkdir(parents=True)
    pc.write_bytes(b"")
    if base:
        (tmp_path / "game" / "Data").mkdir()
        (tmp_path / "game" / "Data" / "Scripts.zip").write_bytes(b"")
    monkeypatch.setattr(ot, "find_texconv", lambda: tool if texconv else None)
    monkeypatch.setattr(ot, "find_papyrus_compiler", lambda: pc if compiler else None)
    writes = []
    monkeypatch.setattr(ot, "convert_overlays",
                        lambda *a, **k: writes.append("overlays") or {"converted": 1})
    return mods, writes


def test_copy_mode_lists_only_the_overlays_a_script_registers(tmp_path, monkeypatch):
    mods, _w = _paint_modlist(tmp_path, monkeypatch)
    plan = ot.plan_overlay_copies(mods / "CBBEtoUBE Auto", None)
    assert plan["body"] == {f"{_OV}/registered.dds": "InkMod"}, plan
    assert plan["hands"] == {} and plan["feet"] == {}, plan


def test_the_copy_plan_is_what_the_copy_pass_bakes(tmp_path, monkeypatch):
    """The drift guard: the real 'Add UBE copy' pass, with its tools stubbed
    present, bakes exactly the set the dry run lists."""
    import numpy as np
    mods, _w = _paint_modlist(tmp_path, monkeypatch, compiler=True, base=True)
    baked = []
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(ot, "_assemble_papyrus_imports", lambda c, w: (work, work))
    monkeypatch.setattr(ot, "build_region_correspondence", lambda region: object())
    monkeypatch.setattr(ot, "dds_to_rgba", lambda *a: np.zeros((1, 1, 4), np.uint8))
    monkeypatch.setattr(ot, "transfer_overlay", lambda rgba, corr: rgba)
    monkeypatch.setattr(ot, "rgba_to_dds", lambda rgba, outp, *a: baked.append(outp))
    monkeypatch.setattr(ot, "_compile_psc", lambda *a: (None, ""))
    out = mods / "CBBEtoUBE Auto"
    ot.add_ube_overlay_copies(None, out, tmp_path / "tools" / "texconv.exe",
                              log=lambda *_a: None)
    plan = ot.plan_overlay_copies(out, None)
    assert len(baked) == sum(len(v) for v in plan.values()) == 1, (baked, plan)


@pytest.mark.parametrize("compiler,base,gap", [
    (False, False, "PapyrusCompiler.exe not found"),
    (True, False, "Papyrus base (Scripts.zip) not found"),
    (True, True, None),
], ids=["no-compiler", "no-base", "tools-present"])
def test_a_copy_mode_dry_run_says_what_it_lists_and_what_would_stop_it(
        tmp_path, monkeypatch, capsys, compiler, base, gap):
    mods, writes = _paint_modlist(tmp_path, monkeypatch, compiler=compiler, base=base)
    rc = ac.main(["auto", "-o", str(mods / "CBBEtoUBE Auto"), "--overlays-only",
                  "--list-only", "--overlay-copy"])
    log = capsys.readouterr().out
    assert rc == 0 and writes == [], log
    assert "'Add UBE copy' mode" in log, log
    assert "body: 1 overlay(s)" in log and "InkMod  (1)" in log, log
    assert "1 overlay(s) listed; nothing was written" in log, log
    if gap:
        assert f"the real run would SKIP every overlay above: {gap}" in log, log
    else:
        assert "would SKIP" not in log, log


def test_a_replace_mode_dry_run_says_its_mode_and_a_missing_texconv(
        tmp_path, monkeypatch, capsys):
    mods, writes = _paint_modlist(tmp_path, monkeypatch, texconv=False)
    rc = ac.main(["auto", "-o", str(mods / "CBBEtoUBE Auto"), "--overlays-only",
                  "--list-only"])
    log = capsys.readouterr().out
    assert rc == 0 and writes == [], log
    assert "replace mode" in log, log
    assert "would SKIP every overlay above: texconv not found" in log, log
