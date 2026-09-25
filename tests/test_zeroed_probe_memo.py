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

r"""#zeroed-probe-memo -- the zeroed-build check asks the disk once per path.

THE COST. The #zeroed-output-source check asked `_Vfs.winner` about ~3,000
paths (~930 distinct) and each question probed every one of ~3,300 folders:
5.4M file-system stats, 72% of a ~6.5 min preamble step. Inside a
`probe_memo()` scope an answer is remembered per (folder list, exact path), and
a path of several parts is only probed in folders that have its first part as
a folder. Every answer must be the one the plain probe gives: the same winner
in the same priority order, case-insensitive, a root FILE still found, and
nothing remembered once the scope closes. `CBBE2UBE_NO_ZEROED_PROBE_MEMO=1`
turns the memo off.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import zeroed_body as zb  # noqa: E402

OFF = "CBBE2UBE_NO_ZEROED_PROBE_MEMO"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _file(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())
    return path


def _instance(tmp_path):
    """Four folders, highest priority first. `hi` has the shared-data folder
    but not the file; `mid` has it; `lo` has it too (loses to `mid`); `bare`
    has nothing."""
    hi, mid, lo, bare = (tmp_path / n for n in ("hi", "mid", "lo", "bare"))
    for d in (hi, mid, lo, bare):
        d.mkdir()
    (hi / "CalienteTools" / "BodySlide" / "ShapeData" / "Other").mkdir(parents=True)
    _file(mid / "CalienteTools" / "BodySlide" / "ShapeData" / "Set" / "Base.nif", "mid")
    _file(lo / "calientetools" / "bodyslide" / "shapedata" / "set" / "base.nif", "lo")
    _file(lo / "Root.txt", "lo root")
    return [hi, mid, lo, bare]


RELS = ("calientetools/bodyslide/shapedata/set/base.nif",
        "CALIENTETOOLS\\BodySlide\\ShapeData\\Set\\BASE.NIF",
        "calientetools/bodyslide/shapedata/other/missing.nif",
        "root.txt", "Root.txt", "nothing/here.nif", "calientetools")


def test_the_memo_gives_what_the_probe_gives(tmp_path):
    dirs = _instance(tmp_path)
    plain = {r: zb._Vfs(dirs).winner(r) for r in RELS}
    with zb.probe_memo():
        vfs = zb._Vfs(dirs)
        memo = {r: vfs.winner(r) for r in RELS}
        again = {r: vfs.winner(r) for r in RELS}
    assert memo == plain == again
    assert plain[RELS[0]][1] == dirs[1]          # the highest folder that has it


def test_case_does_not_matter(tmp_path):
    dirs = _instance(tmp_path)
    with zb.probe_memo():
        hit = zb._Vfs(dirs).winner("CALIENTETOOLS\\BodySlide\\ShapeData\\Set\\BASE.NIF")
    assert hit is not None and hit[1] == dirs[1]


def test_priority_skips_a_folder_that_has_the_first_part_but_not_the_file(tmp_path):
    dirs = _instance(tmp_path)
    with zb.probe_memo():
        hit = zb._Vfs(dirs).winner("calientetools/bodyslide/shapedata/set/base.nif")
    assert hit[1] == dirs[1] and hit[0].read_text() == "mid"


def test_the_highest_provider_wins_when_several_have_the_file(tmp_path):
    dirs = _instance(tmp_path)
    _file(dirs[0] / "calientetools" / "bodyslide" / "shapedata" / "set" / "base.nif", "hi")
    with zb.probe_memo():
        hit = zb._Vfs(dirs).winner("calientetools/bodyslide/shapedata/set/base.nif")
    assert hit[1] == dirs[0] and hit[0].read_text() == "hi"


def test_a_file_at_a_folder_root_is_still_found(tmp_path):
    """A one-part path names a FILE at the root: no folder of that name exists,
    so filtering folders by it would lose the file."""
    dirs = _instance(tmp_path)
    with zb.probe_memo():
        hit = zb._Vfs(dirs).winner("root.txt")
    assert hit is not None and hit[1] == dirs[2]


def test_a_first_part_that_is_a_file_is_not_a_folder(tmp_path):
    """A folder whose `calientetools` is a FILE cannot provide a path below it;
    the next folder does, as with the plain probe."""
    dirs = _instance(tmp_path)
    blocker = tmp_path / "blocker"
    _file(blocker / "calientetools", "not a folder")
    dirs = [blocker, *dirs]
    rel = "calientetools/bodyslide/shapedata/set/base.nif"
    plain = zb._Vfs(dirs).winner(rel)
    with zb.probe_memo():
        memo = zb._Vfs(dirs).winner(rel)
    assert memo == plain and memo[1] == dirs[2]


def test_two_folder_lists_never_share_an_answer(tmp_path):
    dirs = _instance(tmp_path)
    rel = "calientetools/bodyslide/shapedata/set/base.nif"
    with zb.probe_memo():
        a = zb._Vfs(dirs).winner(rel)
        b = zb._Vfs([dirs[2], dirs[3]]).winner(rel)
        c = zb._Vfs([dirs[3]]).winner(rel)
    assert a[1] == dirs[1] and b[1] == dirs[2] and c is None


def test_the_memo_lasts_only_as_long_as_its_scope(tmp_path):
    dirs = _instance(tmp_path)
    rel = "meshes/armor/piece_1.nif"
    with zb.probe_memo():
        assert zb._Vfs(dirs).winner(rel) is None
        _file(dirs[3] / "meshes" / "armor" / "piece_1.nif")
        assert zb._Vfs(dirs).winner(rel) is None     # remembered inside the scope
    assert zb._PROBE_MEMO is None
    hit = zb._Vfs(dirs).winner(rel)
    assert hit is not None and hit[1] == dirs[3]
    with zb.probe_memo():                            # a new scope asks again
        assert zb._Vfs(dirs).winner(rel)[1] == dirs[3]


def test_switched_off_nothing_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    dirs = _instance(tmp_path)
    rel = "meshes/armor/piece_1.nif"
    with zb.probe_memo():
        assert zb._Vfs(dirs).winner(rel) is None
        _file(dirs[3] / "meshes" / "armor" / "piece_1.nif")
        assert zb._Vfs(dirs).winner(rel)[1] == dirs[3]


def test_the_zeroed_output_check_opens_one_scope_per_call(tmp_path, monkeypatch):
    """Inside one call an answer is asked of the disk once; the next call (the
    user may have re-run BodySlide in between) asks again."""
    dirs = _instance(tmp_path)
    rel = "meshes/armor/piece_1.nif"
    seen = []

    def check(*_a, **_k):
        seen.append(zb._Vfs(dirs).winner(rel))
        _file(dirs[3] / "meshes" / "armor" / "piece_1.nif")
        seen.append(zb._Vfs(dirs).winner(rel))

    monkeypatch.setattr(discovery, "_prefer_zeroed_outputs_in", check)
    discovery._prefer_zeroed_outputs({}, {}, tmp_path, [], set())
    assert seen == [None, None]
    assert zb._PROBE_MEMO is None
    seen.clear()
    discovery._prefer_zeroed_outputs({}, {}, tmp_path, [], set())
    assert seen[0] is not None and seen[0][1] == dirs[3]
