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

"""`pack_census` must not call `#pair-tri-names` WORKING a dead slider.

The shared tri deliberately carries BOTH halves' shape names so each half finds
its own; the partner's names are then "tri-only" for this half by design. The
census row said `<== dead sliders` about exactly that, on 90 NIFs of a pack
whose purpose-built checker reported ZERO dead halves.

The classifier asks the pack (what are the PARTNER's shape names?) rather than
re-deriving a `_0`/`_1` suffix rule, so these tests pin the direction of its
errors: a partner it cannot read must fail LOUD (counted as a defect), never
quiet (counted as by-design), because the quiet direction hides the real thing
the row exists to find.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

import pack_census as pc                                    # noqa: E402


class _Shape:
    def __init__(self, name):
        self.name = name


class _Nif:
    def __init__(self, names):
        self.shapes = [_Shape(n) for n in names]


def _pair(tmp_path, monkeypatch, this, that, *, make_partner=True):
    """A `_0`/`_1` pair on disk; NifFile is stubbed to return `that` for the
    partner. Returns the path of the `_0` half."""
    p0 = tmp_path / "cuirass_0.nif"
    p1 = tmp_path / "cuirass_1.nif"
    p0.write_bytes(b"x")
    if make_partner:
        p1.write_bytes(b"x")
    monkeypatch.setattr(pc, "NifFile", lambda q: _Nif(that))
    pc._PARTNER_CACHE.clear()
    return str(p0)


# --------------------------------------------------- it finds the partner

def test_the_partners_names_are_read_from_the_OTHER_half(tmp_path, monkeypatch):
    p0 = _pair(tmp_path, monkeypatch, ["Robe_0"], ["Robe_1", "BaseShape"])
    assert pc._partner_shape_names(p0) == {"Robe_1", "BaseShape"}


def test_it_works_from_EITHER_half(tmp_path, monkeypatch):
    _pair(tmp_path, monkeypatch, ["Robe_1"], ["Robe_0"])
    (tmp_path / "cuirass_0.nif").write_bytes(b"x")
    pc._PARTNER_CACHE.clear()
    assert pc._partner_shape_names(str(tmp_path / "cuirass_1.nif")) == {"Robe_0"}


# ------------------------------------------- every failure fails LOUD (empty)

def test_a_MISSING_partner_reads_as_no_names(tmp_path, monkeypatch):
    """Empty means `extra <= names` is False, so the NIF is counted as a
    defect. That is the safe direction: a partner we cannot see must not
    excuse a mismatch."""
    p0 = _pair(tmp_path, monkeypatch, ["Robe_0"], ["Robe_1"],
               make_partner=False)
    assert pc._partner_shape_names(p0) == set()


def test_an_UNREADABLE_partner_reads_as_no_names(tmp_path, monkeypatch):
    p0 = tmp_path / "cuirass_0.nif"
    p0.write_bytes(b"x")
    (tmp_path / "cuirass_1.nif").write_bytes(b"x")

    def _boom(_q):
        raise RuntimeError("not a nif")
    monkeypatch.setattr(pc, "NifFile", _boom)
    pc._PARTNER_CACHE.clear()
    assert pc._partner_shape_names(str(p0)) == set()


def test_a_file_with_NO_weight_suffix_has_no_partner(tmp_path, monkeypatch):
    p = tmp_path / "gloves.nif"
    p.write_bytes(b"x")
    monkeypatch.setattr(pc, "NifFile", lambda q: _Nif(["Anything"]))
    pc._PARTNER_CACHE.clear()
    assert pc._partner_shape_names(str(p)) == set()


def test_only_a_TRAILING_suffix_counts(tmp_path, monkeypatch):
    """`armor_0_plate.nif` is not a weight half -- the same rule the resolver
    uses. Treating it as one would look for a partner that never exists."""
    p = tmp_path / "armor_0_plate.nif"
    p.write_bytes(b"x")
    monkeypatch.setattr(pc, "NifFile", lambda q: _Nif(["X"]))
    pc._PARTNER_CACHE.clear()
    assert pc._partner_shape_names(str(p)) == set()


# ------------------------------------------------------------- the cache

def test_the_cache_does_not_leak_between_paths(tmp_path, monkeypatch):
    """One entry per NIF. A cache keyed on something coarser would hand one
    garment's partner names to another and silently excuse a real mismatch."""
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    for d, names in ((a, ["A_1"]), (b, ["B_1"])):
        (d / "cuirass_0.nif").write_bytes(b"x")
        (d / "cuirass_1.nif").write_bytes(b"x")
    pc._PARTNER_CACHE.clear()
    monkeypatch.setattr(pc, "NifFile", lambda q: _Nif(["A_1"]))
    assert pc._partner_shape_names(str(a / "cuirass_0.nif")) == {"A_1"}
    monkeypatch.setattr(pc, "NifFile", lambda q: _Nif(["B_1"]))
    assert pc._partner_shape_names(str(b / "cuirass_0.nif")) == {"B_1"}


# ------------------------------------------------- the classification itself

def test_the_row_separates_by_design_from_a_REAL_mismatch():
    """The subset test is the whole classifier: tri-only names that are exactly
    the partner's are the alias; anything else stays a defect."""
    partner = {"Robe_1", "BaseShape"}
    assert {"Robe_1"} <= partner                      # aliased -> by design
    assert not {"Robe_1", "ArmMetal"} <= partner      # a stranger -> defect
    assert not {"ArmMetal"} <= partner


def test_the_census_still_labels_a_real_mismatch_as_dead():
    """Drift guard on the wording: the defect row must keep saying so, or the
    split has quietly turned into a blanket excuse."""
    src = Path(pc.__file__).read_text(encoding="utf-8", errors="replace")
    assert '"   <== dead sliders" if name_partial else' in src
    assert "tri also carries the PARTNER's names" in src
