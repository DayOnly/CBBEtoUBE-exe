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

"""#tail-fold -- the end-of-run weight-pair jiggle sync and the pair
divergence check share one walk.

THE COST. Both were serial walks over every `_0`/`_1` pair of the whole
output, each loading both files: 150 s for the sync and 72 s for the check on
the reported modlist. The check is detect-only and reads the state the sync
leaves, so the fold hands it the files the sync already holds -- but only when
the sync left them exactly as they are on disk. When the sync wrote the pair,
or failed with its copy changed and the file not, the pair is read again.

Every test here compares the fold against the two serial passes on REAL
files (synthetic NIFs through pynifly), never against a number written down.
`CBBE2UBE_NO_TAIL_FOLD=1` runs the serial passes again.
"""
import gc
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, nif_convert as nc           # noqa: E402
from tests import _converter_sources as _cs                    # noqa: E402
from tests.synthetic_nif import (                              # noqa: E402
    build_skinned_shapes_nif, pynifly_available, uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="pynifly not importable")

OFF = "CBBE2UBE_NO_TAIL_FOLD"
SPINE = "NPC Spine [Spn0]"
THIGH = "NPC L Thigh [LThg]"
BELLY = "NPC Belly"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.setattr(nc, "WEIGHT_PARTNER_JIGGLE_SYNC", True)


def _pair(root: Path, stem: str, belly_on_1: bool):
    """A `_0`/`_1` pair: the same sphere at two sizes. With `belly_on_1`, half
    of `_1`'s verts ride the belly jiggle bone and `_0` has none of it -- the
    divergence the sync repairs and the check reports."""
    d = root / "meshes" / "!UBE" / "armor" / "test"
    v1, t1, n1 = uv_sphere(10.0)
    v0, t0, n0 = uv_sphere(9.5)
    build_skinned_shapes_nif(d / f"{stem}_0.nif", [("Body", v0, t0, n0)],
                             bones=(SPINE, THIGH))
    build_skinned_shapes_nif(d / f"{stem}_1.nif", [("Body", v1, t1, n1)],
                             bones=(SPINE, BELLY) if belly_on_1 else (SPINE, THIGH))


def _tree(root: Path) -> Path:
    _pair(root, "clean", belly_on_1=False)
    _pair(root, "straddle", belly_on_1=True)
    return root


def _serial(out: Path):
    """What the tail did before the fold: the sync walk, then the check walk."""
    return (ac._postflight_sync_weight_partner_jiggle(out),
            ac._postflight_weight_partner_divergence(out))


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _count_loads(monkeypatch) -> list:
    pyn = nc._pynifly()
    real = pyn.NifFile
    loads: list = []

    class _Counting(real):
        """A subclass, not a function: the library reads class attributes
        off the name `NifFile` in its own module."""

        def __init__(self, *a, **k):
            if k.get("filepath"):
                loads.append(Path(k["filepath"]).name)
            super().__init__(*a, **k)

    monkeypatch.setattr(pyn, "NifFile", _Counting)
    return loads


def test_the_fold_leaves_the_same_files_and_findings_as_the_two_walks(tmp_path):
    src = _tree(tmp_path / "src")
    a, b = tmp_path / "serial", tmp_path / "fold"
    shutil.copytree(src, a)
    shutil.copytree(src, b)
    want = _serial(a)
    got = ac._postflight_weight_partner_fold(b, check=True)
    assert want[0] > 0, "fixture is inert: the sync repaired nothing"
    assert got == want
    assert _files(b) == _files(a), "the fold wrote different files"
    assert _files(b) != _files(src), "the sync never wrote the straddling pair"


def test_the_fold_still_reports_a_divergent_pair(tmp_path):
    """With the sync switched off nothing repairs the pair, and the check must
    still name it, exactly as the serial check does."""
    nc_off = pytest.MonkeyPatch()
    try:
        nc_off.setattr(nc, "WEIGHT_PARTNER_JIGGLE_SYNC", False)
        out = _tree(tmp_path)
        want = _serial(out)
        got = ac._postflight_weight_partner_fold(out, check=True)
    finally:
        nc_off.undo()
    assert want[1], "fixture is inert: the serial check flags nothing"
    assert any("straddle_1.nif" in w and BELLY in w for w in want[1])
    assert got == (0, want[1])


def test_the_check_reads_the_disk_after_the_sync_changed_the_pair(tmp_path, monkeypatch):
    """The sync changes its copy of the pair and the save puts nothing on disk:
    the file still diverges, so the check must still say so. A fold that let
    the check read the sync's changed copy would report a clean pair that is
    not clean on disk."""
    _cs.patch(monkeypatch, "atomic_nif_save", lambda nf, p: None)
    out = _tree(tmp_path)
    got = ac._postflight_weight_partner_fold(out, check=True)
    want = _serial(out)
    assert got[0] > 0, "fixture is inert: the sync changed nothing"
    assert want[1], "control: the file on disk still diverges"
    assert got == want


def test_the_check_reads_the_disk_after_the_sync_failed_part_way(tmp_path, monkeypatch):
    """The save RAISES: the sync reports 0 verts with its copy already changed
    and the failure recorded. The check must read the file, not that copy."""
    def _boom(nf, p):
        raise OSError("locked")
    _cs.patch(monkeypatch, "atomic_nif_save", _boom)
    out = _tree(tmp_path)
    f0 = nc.pass_failure_summary()
    got = ac._postflight_weight_partner_fold(out, check=True)
    f1 = nc.pass_failure_summary()
    want = _serial(out)
    f2 = nc.pass_failure_summary()
    assert want == (0, want[1]) and want[1], "control: nothing synced, still diverges"
    assert got == want
    noted = {k: f1.get(k, 0) - f0.get(k, 0) for k in f1}
    assert any(noted.values()), "control: the failed save recorded nothing"
    assert noted == {k: f2.get(k, 0) - f1.get(k, 0) for k in f2}, \
        "the fold recorded different failures from the serial passes"


def test_a_check_that_raises_stops_the_check_and_not_the_sync(tmp_path, monkeypatch):
    """The serial check raised on the first pair and reported nothing, but
    the serial sync had already walked every pair. The fold hands back the
    exception, so the run gives the same "scan skipped" warning, and still
    syncs the pairs after the one the check died on."""
    def _bad(*a, **k):
        raise RuntimeError("unreadable shape")
    monkeypatch.setattr(ac, "_weight_partner_scale_divergence", _bad)
    src = _tree(tmp_path / "src")     # "clean" sorts before "straddle"
    a, b = tmp_path / "serial", tmp_path / "fold"
    shutil.copytree(src, a)
    shutil.copytree(src, b)
    want_sync = ac._postflight_sync_weight_partner_jiggle(a)
    with pytest.raises(RuntimeError):
        ac._postflight_weight_partner_divergence(a)
    got = ac._postflight_weight_partner_fold(b, check=True)
    assert want_sync > 0, "fixture is inert: the sync repaired nothing"
    assert got is not None and got[0] == want_sync
    assert isinstance(got[1], RuntimeError)
    assert _files(b) == _files(a), "the fold stopped syncing with the check"


def test_a_pair_the_sync_left_alone_is_loaded_once(tmp_path, monkeypatch):
    """THE SAVING. Two clean pairs: the fold loads each file once; the two
    serial walks load each twice."""
    _pair(tmp_path, "one", belly_on_1=False)
    _pair(tmp_path, "two", belly_on_1=False)
    loads = _count_loads(monkeypatch)
    assert ac._postflight_weight_partner_fold(tmp_path, check=True) == (0, [])
    assert len(loads) == 4, loads
    loads.clear()
    _serial(tmp_path)
    assert len(loads) == 8, "control: the serial walks load every file twice"


def test_a_pair_the_sync_wrote_is_read_again(tmp_path, monkeypatch):
    _pair(tmp_path, "straddle", belly_on_1=True)
    loads = _count_loads(monkeypatch)
    total, found = ac._postflight_weight_partner_fold(tmp_path, check=True)
    assert total > 0 and found == []
    assert sorted(loads) == sorted(["straddle_0.nif", "straddle_1.nif"] * 2)


def test_without_the_check_the_fold_only_syncs(tmp_path, monkeypatch):
    nc_off = pytest.MonkeyPatch()
    try:
        nc_off.setattr(nc, "WEIGHT_PARTNER_JIGGLE_SYNC", False)
        out = _tree(tmp_path)
        loads = _count_loads(monkeypatch)
        assert ac._postflight_weight_partner_fold(out, check=False) == (0, [])
        assert loads == [], "with both passes off no file may be loaded"
    finally:
        nc_off.undo()


def test_switched_off_the_fold_leaves_it_to_the_serial_passes(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    out = _tree(tmp_path)
    before = _files(out)
    loads = _count_loads(monkeypatch)
    assert ac._postflight_weight_partner_fold(out, check=True) is None
    assert loads == [] and _files(out) == before


def test_the_fold_leaves_no_file_open(tmp_path, monkeypatch):
    """#postflight-release: every pair the fold opens -- and every pair it
    reads again -- is released, so reference counting alone frees it."""
    pyn = nc._pynifly()
    _tree(tmp_path)
    gc.collect()
    gc.disable()
    try:
        files0 = sum(1 for o in gc.get_objects() if isinstance(o, pyn.NifFile))
        ac._postflight_weight_partner_fold(tmp_path, check=True)
        files1 = sum(1 for o in gc.get_objects() if isinstance(o, pyn.NifFile))
    finally:
        gc.enable()
        gc.collect()
    assert files1 == files0, "the fold left a file open"
