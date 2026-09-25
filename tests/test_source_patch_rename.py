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

"""#source-patch-rename -- our per-source patches are '<stem> (CBBEtoUBE src).esp'.

THE DEFECT. They were '<stem> UBE patch.esp', the name hand-made UBE patches
carry: on a live modlist 22 of our un-loaded copies in _unmerged_patches shared
a name with another mod's active plugin, and a plugin index that walked
subfolders read ours in place of theirs.

THE RULE. New per-source patches get the new name; a run renames the old sets
(ESP + .skypatcher.json + .espgen.json + .male_fallbacks.json) first and
deletes an old set whose renamed twin exists. The coverage pieces keep
'UBE_Mod*Coverage* UBE patch.esp'. The glob that gated the whole
post-conversion block (female restore, coverage, merge) sees both names -- on
a fresh output nothing matches the old one. The fallback merge never takes an
old and a renamed file for one source. `CBBE2UBE_NO_SOURCE_PATCH_RENAME=1`
keeps the old names and migrates nothing.
"""
import argparse
import json
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import esp
from src import preflight as pf
from src import ube_patcher as up
from src.ube_patcher import restore_female_models
from tests.test_female_model_lastpass import (UBE_FEMALE, _models, _rebuild,
                                              _src_payload)
from tests.test_plugins_only_refresh import _mk_mod

SWITCH = "CBBE2UBE_NO_SOURCE_PATCH_RENAME"
NEW = " (CBBEtoUBE src).esp"
OLD = " UBE patch.esp"
SIDECARS = (".skypatcher.json", ".espgen.json", ".male_fallbacks.json")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)


def _set(pdir: Path, name: str, tag: bytes = b"old", sidecars=SIDECARS):
    """Write a patch ESP and its sidecars, each holding `tag` + its suffix."""
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / name).write_bytes(tag)
    for s in sidecars:
        (pdir / (name + s)).write_bytes(tag + s.encode())


def _names(pdir: Path):
    return sorted(p.name for p in pdir.iterdir())


# ---------------------------------------------------------------- the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    assert ac._source_patch_rename_on() is True
    assert ac._source_patch_name("Mod") == "Mod" + NEW
    monkeypatch.setenv(SWITCH, "1")
    assert ac._source_patch_rename_on() is False
    assert ac._source_patch_name("Mod") == "Mod" + OLD


# ---------------------------------------------------------------- SPN-a fresh output

def _fresh_run(tmp_path, monkeypatch):
    src = _mk_mod(tmp_path)
    monkeypatch.setattr(ac, "_resolve_armor_meshes", lambda *a, **k: [])
    ref = tmp_path / "ref.nif"
    ref.write_bytes(b"x")
    r = ac.auto_convert_mod(src, tmp_path / "out", ube_body_ref_path=ref,
                            master_data_dirs=[tmp_path], nif_workers=1)
    return r, tmp_path / "out" / "_unmerged_patches"


def test_a_fresh_run_writes_only_renamed_patches(tmp_path, monkeypatch):
    """SPN-a: the patch and its snapshot carry the new name; nothing matches
    the old '*UBE patch.esp' any more."""
    r, pdir = _fresh_run(tmp_path, monkeypatch)
    assert [p.name for p in r.output_esps] == ["MyMod" + NEW]
    assert _names(pdir) == ["MyMod" + NEW, "MyMod" + NEW + ".espgen.json"]
    assert not list(pdir.glob("*UBE patch.esp"))


def test_switched_off_a_fresh_run_keeps_the_old_name(tmp_path, monkeypatch):
    """SPN-g: the switch writes '<stem> UBE patch.esp', as before."""
    monkeypatch.setenv(SWITCH, "1")
    r, pdir = _fresh_run(tmp_path, monkeypatch)
    assert [p.name for p in r.output_esps] == ["MyMod" + OLD]
    assert _names(pdir) == ["MyMod" + OLD, "MyMod" + OLD + ".espgen.json"]


# ---------------------------------------------------------------- SPN-b migration

def test_migration_renames_the_whole_set(tmp_path):
    pdir = tmp_path / "p"
    _set(pdir, "Armour Set" + OLD)
    res = ac._migrate_source_patch_names(pdir)
    assert (res["renamed"], res["removed"], res["failed"]) == (1, 0, [])
    assert _names(pdir) == sorted(["Armour Set" + NEW]
                                  + ["Armour Set" + NEW + s for s in SIDECARS])
    # Content moved unchanged, each sidecar with its own patch.
    assert (pdir / ("Armour Set" + NEW)).read_bytes() == b"old"
    for s in SIDECARS:
        assert (pdir / ("Armour Set" + NEW + s)).read_bytes() == b"old" + s.encode()


def test_migration_moves_only_the_sidecars_that_exist(tmp_path):
    pdir = tmp_path / "p"
    _set(pdir, "Solo" + OLD, sidecars=(".espgen.json",))
    ac._migrate_source_patch_names(pdir)
    assert _names(pdir) == ["Solo" + NEW, "Solo" + NEW + ".espgen.json"]


def test_migration_is_idempotent(tmp_path):
    pdir = tmp_path / "p"
    _set(pdir, "A" + OLD)
    _set(pdir, "B" + OLD)
    assert ac._migrate_source_patch_names(pdir)["renamed"] == 2
    before = {n: (pdir / n).read_bytes() for n in _names(pdir)}
    again = ac._migrate_source_patch_names(pdir)
    assert (again["renamed"], again["removed"], again["failed"]) == (0, 0, [])
    assert {n: (pdir / n).read_bytes() for n in _names(pdir)} == before


def test_an_old_set_beside_its_renamed_twin_is_deleted(tmp_path):
    """The renamed set is the newer run's; the old one is stale and ours."""
    pdir = tmp_path / "p"
    _set(pdir, "Mod" + OLD, tag=b"old")
    _set(pdir, "Mod" + NEW, tag=b"new")
    res = ac._migrate_source_patch_names(pdir)
    assert (res["renamed"], res["removed"]) == (0, 1)
    assert _names(pdir) == sorted(["Mod" + NEW] + ["Mod" + NEW + s for s in SIDECARS])
    assert (pdir / ("Mod" + NEW)).read_bytes() == b"new"


def test_coverage_pieces_keep_their_names(tmp_path):
    pdir = tmp_path / "p"
    for n in ("UBE_ModBody_Coverage" + OLD, "UBE_ModBody_Coverage2" + OLD,
              "UBE_ModNonBody_Coverage" + OLD):
        _set(pdir, n, sidecars=(".skypatcher.json",))
    before = _names(pdir)
    res = ac._migrate_source_patch_names(pdir)
    assert (res["renamed"], res["removed"], res["failed"]) == (0, 0, [])
    assert _names(pdir) == before


def test_switched_off_nothing_is_migrated(tmp_path, monkeypatch, capsys):
    """SPN-g: the old sets stay exactly as they are."""
    monkeypatch.setenv(SWITCH, "1")
    out = tmp_path / "out"
    pdir = out / "_unmerged_patches"
    _set(pdir, "Mod" + OLD)
    before = _names(pdir)
    assert ac._migrate_source_patch_names_at_start(out, "_unmerged_patches") == 0
    assert _names(pdir) == before
    assert "[migrate]" not in capsys.readouterr().out


def test_the_run_start_logs_the_count(tmp_path, capsys):
    out = tmp_path / "out"
    _set(out / "_unmerged_patches", "A" + OLD)
    _set(out / "_unmerged_patches", "B" + OLD)
    assert ac._migrate_source_patch_names_at_start(out, "_unmerged_patches") == 0
    log = capsys.readouterr().out
    assert "[migrate] renamed 2 per-source patch(es)" in log
    assert "enable the renamed plugins" not in log, "they are not plugins here"


def _ours(out: Path):
    """Mark `out` as this tool's output the way a finished run does."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "conversion_report.json").write_text("{}", encoding="utf-8")


def test_root_write_mode_says_to_enable_the_renamed_plugins(tmp_path, capsys):
    """--unmerged-patch-subdir '.': the patches are plugins MO2 loads."""
    out = tmp_path / "out"
    _ours(out)
    _set(out, "A" + OLD)
    ac._migrate_source_patch_names_at_start(out, ".")
    log = capsys.readouterr().out
    assert "[migrate] renamed 1 per-source patch(es)" in log
    assert "enable the renamed plugins" in log
    assert (out / ("A" + NEW)).is_file()


# ---------------------------------------------------------------- RG root-write guards

@pytest.mark.parametrize("subdir", [".", ""], ids=["dot", "empty"])
def test_root_write_leaves_a_plugin_without_our_snapshot_alone(tmp_path, capsys, subdir):
    """RG-a: in our own output folder, a '<x> UBE patch.esp' with no
    .espgen.json beside it is another mod's plugin: it keeps its name (and a
    renamed file of that name beside it is no licence to delete it); ours
    beside it is renamed."""
    out = tmp_path / "out"
    _ours(out)
    _set(out, "Ours" + OLD)
    _set(out, "HandMade" + OLD, tag=b"theirs", sidecars=())
    _set(out, "Twin" + OLD, tag=b"theirs", sidecars=(".skypatcher.json",))
    _set(out, "Twin" + NEW, tag=b"ours", sidecars=(".espgen.json",))
    assert ac._migrate_source_patch_names_at_start(out, subdir) == 0
    log = capsys.readouterr().out
    assert (out / ("HandMade" + OLD)).read_bytes() == b"theirs"
    assert not (out / ("HandMade" + NEW)).exists()
    assert (out / ("Twin" + OLD)).read_bytes() == b"theirs"
    assert (out / ("Twin" + OLD + ".skypatcher.json")).is_file()
    assert (out / ("Twin" + NEW)).read_bytes() == b"ours"
    assert (out / ("Ours" + NEW)).read_bytes() == b"old"
    assert not (out / ("Ours" + OLD)).exists()
    assert "[migrate] renamed 1 per-source patch(es)" in log
    assert "NOTE: left 2" in log
    assert "HandMade" + OLD in log and "Twin" + OLD in log


def test_root_write_in_a_folder_that_is_not_ours_renames_nothing(tmp_path, capsys):
    """RG-b: no conversion report, no marked INI: not our output. Even a file
    with an .espgen.json beside it stays where it is."""
    out = tmp_path / "shared"
    _set(out, "HandMade" + OLD, tag=b"theirs", sidecars=())
    _set(out, "Looks Like Ours" + OLD)
    before = {n: (out / n).read_bytes() for n in _names(out)}
    assert ac._migrate_source_patch_names_at_start(out, ".") == 0
    log = capsys.readouterr().out
    assert {n: (out / n).read_bytes() for n in _names(out)} == before
    assert "renamed" not in log
    assert "holds no conversion report of this tool" in log
    assert "2 '<plugin> UBE patch.esp' file(s)" in log


def test_a_subfolder_is_still_migrated_without_a_snapshot(tmp_path, capsys):
    """RG-c: the guards are for the root only; _unmerged_patches is ours by
    name and a set without .espgen.json there is renamed as before."""
    out = tmp_path / "out"
    pdir = out / "_unmerged_patches"
    _set(pdir, "Bare" + OLD, sidecars=())
    assert ac._migrate_source_patch_names_at_start(out, "_unmerged_patches") == 0
    assert _names(pdir) == ["Bare" + NEW]
    assert "NOTE" not in capsys.readouterr().out


# ---------------------------------------------------------------- SPN-h a failed rename

def test_a_failed_rename_is_a_named_warning_and_splits_nothing(tmp_path, monkeypatch, capsys):
    """The ESP moves last; when it cannot, the sidecars already moved go back."""
    out = tmp_path / "out"
    pdir = out / "_unmerged_patches"
    _set(pdir, "Locked" + OLD)
    _set(pdir, "Free" + OLD)
    real = ac.os.replace

    def _replace(src, dst):
        if Path(src).name == "Locked" + OLD:
            raise PermissionError(13, "in use")
        return real(src, dst)
    monkeypatch.setattr(ac.os, "replace", _replace)
    ac._RUN_FAILURES.clear()
    try:
        n = ac._migrate_source_patch_names_at_start(out, "_unmerged_patches")
        fails = list(ac._RUN_FAILURES)
    finally:
        ac._RUN_FAILURES.clear()
    assert n == 1
    log = capsys.readouterr().out
    assert "could not rename the old-named per-source patch Locked UBE patch.esp" in log
    assert [(e["kind"], e["severity"]) for e in fails] == [("rename failed", "warning")]
    # Locked keeps its whole old set; Free moved whole.
    assert sorted(n for n in _names(pdir) if n.startswith("Locked")) == sorted(
        ["Locked" + OLD] + ["Locked" + OLD + s for s in SIDECARS])
    assert sorted(n for n in _names(pdir) if n.startswith("Free")) == sorted(
        ["Free" + NEW] + ["Free" + NEW + s for s in SIDECARS])


# ---------------------------------------------------------------- SPN-c fallback merge

def test_the_fallback_merge_never_takes_both_names_for_one_source(tmp_path):
    pdir = tmp_path / "p"
    for n in ("Both" + OLD, "Both" + NEW, "OnlyOld" + OLD, "OnlyNew" + NEW,
              "UBE_ModBody_Coverage" + OLD, "UBE_ModNonBody_Coverage2" + OLD):
        _set(pdir, n, sidecars=())
    got = [p.name for p in ac._per_source_patch_paths(pdir)]
    assert sorted(got) == sorted(["Both" + NEW, "OnlyOld" + OLD, "OnlyNew" + NEW])


def test_the_fallback_merge_keeps_the_old_order(tmp_path):
    """Renamed patches merge in the order the old names sorted in, so the merge
    numbers their records as before ('Foo (' sorts before 'Foo B', 'Foo U'
    after it)."""
    stems = ["Foo", "Foo Bar", "Alpha", "zeta"]
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for s in stems:
        _set(old_dir, s + OLD, sidecars=())
        _set(new_dir, s + NEW, sidecars=())
    old_order = [p.name[:-len(OLD)] for p in ac._per_source_patch_paths(old_dir)]
    new_order = [p.name[:-len(NEW)] for p in ac._per_source_patch_paths(new_dir)]
    assert new_order == old_order


def test_switched_off_the_fallback_merge_takes_the_old_glob(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    pdir = tmp_path / "p"
    for n in ("A" + OLD, "B" + NEW, "UBE_ModBody_Coverage" + OLD):
        _set(pdir, n, sidecars=())
    assert [p.name for p in ac._per_source_patch_paths(pdir)] == ["A" + OLD]


# ---------------------------------------------------------------- SPN-d --plugins-only

def _snapshot_patch(tmp_path, name):
    src_dir = _mk_mod(tmp_path)
    out_dir = tmp_path / "out"
    pdir = out_dir / "_unmerged_patches"
    pdir.mkdir(parents=True, exist_ok=True)
    patch = pdir / name
    conv = {"armor/m/body_0.nif"}
    up.generate_ube_patch(src_dir / "MyMod.esp", patch,
                          master_data_dirs=[tmp_path], converted_rel_paths=conv)
    Path(str(patch) + ".espgen.json").write_text(json.dumps({
        "source_esp": str(src_dir / "MyMod.esp"),
        "converted_rel_paths": sorted(conv),
        "body_mesh_rel_paths": []}), encoding="utf-8")
    orig = patch.read_bytes()
    patch.unlink()
    return src_dir, out_dir, patch, orig


def test_plugins_only_replays_the_renamed_snapshot(tmp_path):
    src_dir, out_dir, patch, orig = _snapshot_patch(tmp_path, "MyMod" + NEW)
    r = ac.refresh_mod_esp(src_dir, out_dir, master_data_dirs=[tmp_path])
    assert [p.name for p in r.output_esps] == ["MyMod" + NEW]
    assert patch.read_bytes() == orig


def test_plugins_only_falls_back_to_an_old_named_snapshot(tmp_path):
    """A snapshot whose migration failed is replayed in place, beside its own
    sidecars -- not skipped as 'never converted'."""
    src_dir, out_dir, patch, orig = _snapshot_patch(tmp_path, "MyMod" + OLD)
    r = ac.refresh_mod_esp(src_dir, out_dir, master_data_dirs=[tmp_path])
    assert [p.name for p in r.output_esps] == ["MyMod" + OLD], r.notes
    assert patch.read_bytes() == orig
    assert not (patch.parent / ("MyMod" + NEW)).exists()


# ---------------------------------------------------------------- SPN-e source scan

def test_a_renamed_patch_at_a_mod_root_is_not_a_source(tmp_path):
    mod = tmp_path / "X"
    mod.mkdir()
    for n in ("Real.esp", "Real" + NEW, "Real" + OLD):
        (mod / n).write_bytes(b"")
    assert {p.name for p in ac._find_source_esps(mod)} == {"Real.esp"}


# ---------------------------------------------------------------- SPN-f female restore

def test_female_restore_works_on_a_migrated_set(tmp_path):
    """The .male_fallbacks.json sidecar moves with its patch, so the last-pass
    restore still finds and re-points the female model."""
    log = []
    payload = _rebuild(_src_payload(with_female=True), log)
    pdir = tmp_path / "_unmerged_patches"
    pdir.mkdir()
    fid = 0x01000800
    old = pdir / ("Test" + OLD)
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[esp.Record(
                sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                version_unk=0x002C, payload=payload)])]).save(old)
    Path(str(old) + ".male_fallbacks.json").write_text(
        json.dumps([dict(fid=fid, **e) for e in log]), encoding="utf-8")
    assert ac._migrate_source_patch_names(pdir)["renamed"] == 1
    mesh = tmp_path / "meshes" / "!UBE" / "armor" / "x" / "cuirass_f_1.nif"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"NIF")
    stats = restore_female_models(pdir, tmp_path)
    assert stats["models_restored"] == 1
    new = pdir / ("Test" + NEW)
    assert _models(esp.ESP.load(new).group(b"ARMA").records[0].payload)[b"MOD3"] == UBE_FEMALE


# ---------------------------------------------------------------- SPN-i the gate

def _ns(sources, output):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def _run(base, monkeypatch, capsys, before=None):
    """One `_cmd_convert` over a temporary modlist. The batch writes a real
    per-source patch for MyMod under the current name; coverage is a spy that
    comes back empty, so the fallback merge (real) builds the Combined.
    `before(out)` lays out the output folder first. Returns
    (log, patches written, coverage calls, output folder)."""
    base.mkdir(parents=True, exist_ok=True)
    src_dir = _mk_mod(base)
    mods = base / "mods"
    mods.mkdir()
    out = base / "out"
    if before is not None:
        before(out)
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
    monkeypatch.setattr(ac, "_discover_master_data_dirs", lambda *a, **k: [base])
    written, emitted = [], []

    def _converted(source_dir, *a, **k):
        pdir = out / "_unmerged_patches"
        pdir.mkdir(parents=True, exist_ok=True)
        p = pdir / ac._source_patch_name("MyMod")
        up.generate_ube_patch(src_dir / "MyMod.esp", p, master_data_dirs=[base],
                              converted_rel_paths={"armor/m/body_0.nif"})
        written.append(p.name)
        return ac.AutoConvertResult(source_dir=Path(source_dir), output_dir=out)
    monkeypatch.setattr(ac, "auto_convert_mod", _converted)

    def _emit(output, patches_dir, *a, **k):
        emitted.append(sorted(q.name for q in Path(patches_dir).iterdir()))
        return (False, 0, False)
    monkeypatch.setattr(ac, "_emit_unified_coverage_patches", _emit)
    ac._cmd_convert(_ns([src_dir], out))
    return capsys.readouterr().out, written, emitted, out


def _tally(log):
    import re
    m = re.search(r"=== (\d+) failure\(s\), (\d+) warning\(s\) ===", log)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


@pytest.mark.parametrize("off", [False, True], ids=["renamed", "switched-off"])
def test_a_fresh_output_still_emits_coverage_and_the_combined(
        tmp_path, monkeypatch, capsys, off):
    """SPN-i: an EMPTY patches folder, and the batch writes only per-source
    patches under the new name. The post-conversion block must still run:
    coverage is emitted, and (coverage coming back empty here) the fallback
    merge writes CBBE_to_UBE_Combined.esp from the renamed patch. The old gate,
    '*UBE patch.esp', matched nothing and skipped both."""
    if off:
        monkeypatch.setenv(SWITCH, "1")
    log, written, emitted, out = _run(tmp_path, monkeypatch, capsys)
    assert written == ["MyMod" + (OLD if off else NEW)]
    assert emitted, "coverage was never reached: " + log[-2500:]
    assert "NO RACE COVERAGE GENERATED" not in log
    assert "auto-merging 1 patch(es) into CBBE_to_UBE_Combined.esp" in log, log[-2500:]
    assert (out / "CBBE_to_UBE_Combined.esp").is_file()


def test_a_run_migrates_an_old_output_before_the_batch(tmp_path, monkeypatch, capsys):
    """The last run's old-named set is renamed at the start, so the batch
    overwrites it in place and no old name is left for anything to read."""
    def _old(out):
        _set(out / "_unmerged_patches", "MyMod" + OLD, sidecars=(".espgen.json",))
        _set(out / "_unmerged_patches", "Gone" + OLD, sidecars=(".espgen.json",))
    log, _w, emitted, out = _run(tmp_path, monkeypatch, capsys, before=_old)
    assert "[migrate] renamed 2 per-source patch(es)" in log, log[:3000]
    assert emitted
    assert {"Gone" + NEW, "Gone" + NEW + ".espgen.json", "MyMod" + NEW,
            "MyMod" + NEW + ".espgen.json"} <= set(emitted[0])
    assert not [n for n in emitted[0] if OLD in n], emitted[0]
    assert not list((out / "_unmerged_patches").glob("*UBE patch.esp*"))


def test_a_failed_rename_is_counted_in_the_tally(tmp_path, monkeypatch, capsys):
    """SPN-h: recorded AND counted, one warning more than the same run without it."""
    log0, *_ = _run(tmp_path / "a", monkeypatch, capsys)
    real = ac.os.replace

    def _replace(src, dst):
        if Path(src).name == "Locked" + OLD:
            raise PermissionError(13, "in use")
        return real(src, dst)
    monkeypatch.setattr(ac.os, "replace", _replace)
    def _locked(out):
        # A real patch, so the fallback merge (which takes a lone old name)
        # merges it cleanly and the tally differs by the warning alone.
        (out / "_unmerged_patches").mkdir(parents=True)
        up.generate_ube_patch(out.parent / "MyMod" / "MyMod.esp",
                              out / "_unmerged_patches" / ("Locked" + OLD),
                              master_data_dirs=[out.parent],
                              converted_rel_paths={"armor/m/body_0.nif"})
    log1, *_ = _run(tmp_path / "b", monkeypatch, capsys, before=_locked)
    assert "auto-merging 2 patch(es)" in log1, log1[-2500:]
    assert "could not rename the old-named per-source patch Locked UBE patch.esp" in log1
    assert _tally(log1)[1] == _tally(log0)[1] + 1, (_tally(log0), _tally(log1))
