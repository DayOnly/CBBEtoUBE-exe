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

"""The census behind `#body-name-prefix`.

The defect it counts is a name-only body test with no texture gate, so the
tests that matter are: the census asks the DETECTOR's own question rather than
a re-derived one, a clean result is a pass while an unresolved population is
not, and loose and archived sources are separated -- the previous census
covered only the archived half and had to name the rest as uncounted.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import nif_convert as nc                             # noqa: E402
from scripts.analysis import inline_body_name_strip as ibs    # noqa: E402


class _Shape:
    def __init__(self, name, tex=None, verts=()):
        self.name = name
        self.textures = tex or {}
        self.verts = verts


# ------------------------------------------------ it asks the DETECTOR's question

def test_the_prefix_filter_is_the_detectors_own_prefix_tuple():
    """A re-derived list would drift from the branch it is auditing."""
    assert ibs.prefix_named([_Shape("FemaleUnderwearBody:0")])
    assert ibs.prefix_named([_Shape("femalebody99")])
    assert not ibs.prefix_named([_Shape("HOOD")])
    for p in nc.BODY_SHAPE_NAME_PREFIXES:
        assert ibs.prefix_named([_Shape(p.upper() + "X")]), p


def test_a_name_with_the_prefix_INSIDE_it_is_not_a_match():
    """`startswith`, not `in` -- the branch being audited uses startswith, and
    an `in` here would invent defects the converter never commits."""
    assert not ibs.prefix_named([_Shape("MyFemaleBodyCape")])


def test_the_texture_gate_is_called_through_the_module():
    """So a change to the marker list moves the census with it."""
    body = _Shape("FemaleUnderwearBody:0",
                  {"Diffuse": r"textures\actors\character\female\femalebody_1.dds"})
    garment = _Shape("FemaleUnderwearBody:0",
                     {"Diffuse": r"textures\x\armor\cuirassfi.dds"})
    assert ibs.is_body_skin(body)
    assert not ibs.is_body_skin(garment)


def test_the_marker_list_still_contains_what_this_census_assumes():
    """A mutation control on the gate itself: if `femalebody` ever leaves the
    marker list, every correct strip in the census silently becomes a defect."""
    assert any("femalebody" in m for m in nc._BODY_SKIN_TEXTURE_MARKERS)


# ---------------------------------------------------- loose vs archived

def test_loose_and_archived_sources_are_told_apart():
    """The earlier census could only see the archived half and had to name the
    loose half as an uncounted exclusion. That is not a number about a
    population."""
    staged = Path(tempfile.gettempdir()) / "cbbe2ube_harness_bsa" / "a" / "b.nif"
    assert ibs.origin_of(staged) == "bsa"
    assert ibs.origin_of(Path("D:/anything/else/b.nif")) == "loose"


# --------------------------------------------------------- exit codes

def test_a_clean_result_is_a_PASS_not_an_empty_population(capsys):
    """Zero prefix hits over real sources is exit 0. Zero SOURCES is exit 3.
    Conflating them is the 0/0 bug the pair-tri checker shipped with."""
    assert ibs.report([], {}, sources=120) == 0
    out = capsys.readouterr().out
    assert "distinct author sources read              : 120" in out
    assert "WRONG strip          : 0   OK" in out


def test_a_wrong_strip_exits_1(capsys):
    rows = [{"rel": "a/b.nif", "shape": "FemaleUnderwearBody:0", "verts": 1473,
             "body_skin": False, "origin": "bsa"}]
    assert ibs.report(rows, {}, sources=1) == 1
    out = capsys.readouterr().out
    assert "real armour deleted" in out
    assert "FemaleUnderwearBody:0" in out


def test_a_correct_strip_alone_exits_0(capsys):
    rows = [{"rel": "a/b.nif", "shape": "FemaleUnderwearBody:0", "verts": 292,
             "body_skin": True, "origin": "loose"}]
    assert ibs.report(rows, {}, sources=1) == 0
    assert "real armour deleted" not in capsys.readouterr().out


def test_no_source_resolved_exits_3(tmp_path, capsys, monkeypatch):
    arm = tmp_path / "arm"
    (arm / "meshes" / "!UBE").mkdir(parents=True)
    with pytest.raises(SystemExit) as ex:
        ibs.main([str(arm)])
    assert ex.value.code == 3
    assert "0/0 is not a pass" in capsys.readouterr().out


def test_a_missing_output_dir_is_refused(tmp_path):
    assert ibs.main([str(tmp_path / "nope")]) == 2
    assert ibs.main([]) == 2


def test_a_valued_flag_eats_its_value(tmp_path):
    """`--limit 5 <dir>` must leave ONE positional."""
    assert ibs.main(["--limit", "5"]) == 2
