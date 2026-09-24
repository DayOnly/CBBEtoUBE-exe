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

r"""#supersede-whole-base -- a base left to its builder leaves meshes\ whole,
and the weight-partner fill never writes into it.

THE DEFECT. #skip-built-ube-path moved only the weight variants the run
planned. A source that ships only `_1` plans only `x_1`, so the `x_0` an
earlier run's partner fill wrote stayed in `meshes\!UBE`; after the batch the
fill copied that stale `x_0` back to `x_1` -- the builder's path, beating the
hand-made mesh again, on every run. A move that failed (a file in use) was
silent and left half a base, which the fill then completed.

THE RULE. Every `_0`/`_1` of a superseded base in our output moves with its
`.tri` and `.xml`, all or nothing: a failed move puts the moved files back and
is named in a warning. The fill skips every base the run left to a builder.
`CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE=1` moves only the planned variants again.
"""
import os

import pytest

from src import auto_convert as ac
from tests.test_coverage_ube_twin import _twin_of
from tests.test_npc_worn_nonplayable import load_order  # noqa: F401 (fixture)

SWITCH = "CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE"
BASE = "armor/mage/robe"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_SKIP_BUILT_UBE_PATH", raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_COVERAGE_UBE_TWIN", raising=False)


def _seed(tmp_path, names, folder="armor/mage"):
    out = tmp_path / "Out"
    root = out / "meshes" / "!UBE"
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"OUR OLD " + n.encode())
    return out, root, d


def _left(d):
    return sorted(p.name for p in d.iterdir())


def _sup(out, folder="armor/mage"):
    d = out / "_superseded" / "meshes" / "!UBE" / folder
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _fail_on(monkeypatch, *names, back=()):
    """os.replace raises for a move of a file named in `names` into
    _superseded, and for a move of one named in `back` out of it."""
    real = os.replace

    def _replace(src, dst):
        s = str(src)
        if "_superseded" not in s and os.path.basename(s) in names:
            raise PermissionError(13, "in use by another process", s)
        if "_superseded" in s and os.path.basename(s) in back:
            raise PermissionError(13, "in use by another process", s)
        return real(src, dst)
    monkeypatch.setattr(ac.os, "replace", _replace)


# ---------------------------------------------------------------- the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    assert ac._supersede_whole_base() is True
    monkeypatch.setenv(SWITCH, "1")
    assert ac._supersede_whole_base() is False


# ---------------------------------------------------------------- the move

def test_a_filled_partner_moves_with_its_base(tmp_path):
    """The source ships only `_1`, so only `robe_1` is planned; the `robe_0`
    an earlier fill wrote goes too, and nothing is left for the fill."""
    out, root, d = _seed(tmp_path, ["robe_1.nif", "robe_0.nif", "robe.tri", "robe.xml",
                                    "boots_1.nif", "boots_0.nif", "boots.tri"])
    moved = ac._supersede_built_ube_outputs(out, root, [BASE + "_1.nif"])
    assert moved == 4
    assert _left(d) == ["boots.tri", "boots_0.nif", "boots_1.nif"], "only that base moves"
    assert _sup(out) == ["robe.tri", "robe.xml", "robe_0.nif", "robe_1.nif"]
    assert ac._complete_weight_partners(
        out, source_variants={BASE: {"_1"}}) == (0, 0)
    assert not (d / "robe_1.nif").exists()


def test_switched_off_the_filled_partner_stays_and_comes_back(tmp_path, monkeypatch):
    """Control, the parent's behaviour: the fill copies the stale `_0` back
    to the builder's path."""
    monkeypatch.setenv(SWITCH, "1")
    out, root, d = _seed(tmp_path, ["robe_1.nif", "robe_0.nif", "robe.tri"])
    assert ac._supersede_built_ube_outputs(out, root, [BASE + "_1.nif"]) == 2
    assert _left(d) == ["robe_0.nif"]
    assert ac._complete_weight_partners(
        out, source_variants={BASE: {"_1"}}) == (1, 0)
    assert (d / "robe_1.nif").read_bytes() == b"OUR OLD robe_0.nif"


def test_an_unweighted_mesh_moves_alone(tmp_path):
    """A mesh without a weight suffix has no partner to take along."""
    out, root, d = _seed(tmp_path, ["ring.nif", "ring_1.nif", "ring.tri"])
    assert ac._supersede_built_ube_outputs(out, root, ["armor/mage/ring.nif"]) == 2
    assert _left(d) == ["ring_1.nif"]


# ---------------------------------------------------------------- a failed move

def test_a_file_in_use_leaves_the_whole_base(tmp_path, monkeypatch):
    out, root, d = _seed(tmp_path, ["robe_1.nif", "robe_0.nif", "robe.tri",
                                    "boots_1.nif", "boots_0.nif"])
    _fail_on(monkeypatch, "robe_0.nif")
    failed = []
    moved = ac._supersede_built_ube_outputs(
        out, root, [BASE + "_1.nif", "armor/mage/boots_1.nif",
                    "armor/mage/boots_0.nif"], failed=failed)
    assert moved == 2, "the other base still moves"
    assert _left(d) == ["robe.tri", "robe_0.nif", "robe_1.nif"], \
        "the moved robe_1 is put back"
    assert _sup(out) == ["boots_0.nif", "boots_1.nif"]
    assert [(b, t) for b, _e, t in failed] == [(BASE, [])]


def test_a_file_that_cannot_go_back_is_named(tmp_path, monkeypatch):
    out, root, d = _seed(tmp_path, ["robe_1.nif", "robe_0.nif"])
    _fail_on(monkeypatch, "robe_0.nif", back=("robe_1.nif",))
    failed = []
    assert ac._supersede_built_ube_outputs(
        out, root, [BASE + "_1.nif"], failed=failed) == 0
    assert [(b, t) for b, _e, t in failed] == [(BASE, ["robe_1.nif"])]


def test_the_planner_names_a_base_it_could_not_move(
        load_order, tmp_path, monkeypatch, capsys):
    """Behavioural: the planner warns with the base, and the whole stale base
    stays in meshes\\."""
    class _First(BaseException):
        pass
    dress = ["armor/outfit/dress_1.nif", "armor/outfit/dress_0.nif"]
    boots = ["armor/outfit/boots_1.nif", "armor/outfit/boots_0.nif"]
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress + boots])

    def _worker(item):
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    out = tmp_path / "out"
    d = out / "meshes" / "!UBE" / "armor" / "outfit"
    d.mkdir(parents=True)
    for n in ("dress_1.nif", "dress_0.nif", "dress.tri"):
        (d / n).write_bytes(b"old")
    _fail_on(monkeypatch, "dress_0.nif")
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    with pytest.raises(_First):
        ac.auto_convert_mod(load_order, out, ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1,
                            built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
    log = capsys.readouterr().out
    assert "could not be moved out of meshes" in log
    assert "armor/outfit/dress" in log
    assert _left(d) == ["dress.tri", "dress_0.nif", "dress_1.nif"]


# ---------------------------------------------------------------- the fill

def test_the_fill_never_writes_into_a_base_left_to_its_builder(tmp_path):
    out, root, d = _seed(tmp_path, ["robe_0.nif", "cape_0.nif",
                                    "hood_1.nif", "hood_0.nif"])
    assert ac._complete_weight_partners(
        out, source_variants={BASE: {"_1"}, "armor/mage/hood": {"_1"}},
        skip_bases={BASE, "armor/mage/hood"}) == (1, 0)
    assert _left(d) == ["cape_0.nif", "cape_1.nif", "hood_0.nif", "hood_1.nif",
                        "robe_0.nif"], "cape is filled; robe is not"
    assert (d / "hood_0.nif").read_bytes() == b"OUR OLD hood_0.nif", \
        "nor is a partner refreshed there"


def test_the_batch_keeps_the_fill_out_of_a_superseded_base(tmp_path, monkeypatch):
    """Behavioural: the real `convert` hands the bases its planners left to a
    builder to the partner fill; a base not left to one is still filled."""
    from tests.test_exclude_owned_coverage import run_convert

    def _shape(res, out):
        d = out / "meshes" / "!UBE" / "armor" / "mage"
        d.mkdir(parents=True, exist_ok=True)
        for n in ("robe_0.nif", "cape_0.nif"):
            (d / n).write_bytes(n.encode())
        res.superseded_weight_bases.add(BASE)
    run_convert(tmp_path, monkeypatch, on_result=_shape)
    d = tmp_path / "out" / "meshes" / "!UBE" / "armor" / "mage"
    assert (d / "cape_1.nif").is_file(), "control: the fill ran"
    assert not (d / "robe_1.nif").exists()


def test_the_planner_records_the_base_it_left(load_order, tmp_path, monkeypatch):
    """Behavioural: a base left to its builder is on the result, so the fill
    after the batch can skip it; switched off, nothing is recorded."""
    class _First(BaseException):
        pass
    dress = ["armor/outfit/dress_1.nif"]
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress])
    seen = []
    real = ac.AutoConvertResult

    def _result(*a, **k):
        seen.append(real(*a, **k))
        return seen[-1]
    monkeypatch.setattr(ac, "AutoConvertResult", _result)

    def _worker(item):
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    for off, want in (("", {"armor/outfit/dress"}), ("1", set())):
        seen.clear()
        if off:
            monkeypatch.setenv(SWITCH, off)
        try:
            ac.auto_convert_mod(load_order, tmp_path / f"out{off}",
                                ube_body_ref_path=ref, master_data_dirs=[],
                                nif_workers=1,
                                built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
        except _First:
            pass
        assert seen and seen[0].superseded_weight_bases == want
