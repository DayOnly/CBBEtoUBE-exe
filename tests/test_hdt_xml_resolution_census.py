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

"""The exposure count behind `#hdt-xml-race`.

The number this produces decides which of three non-equivalent repairs to make,
so its classification has to be exactly the resolver's own precedence and its
error has to lean the safe way. Both are pinned here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

import pytest                                                    # noqa: E402
from scripts.analysis import hdt_xml_resolution_census as cen     # noqa: E402


class _Ed:
    def __init__(self, name):
        self.name = name


class _Root:
    def __init__(self, names):
        self._n = names

    def extra_data(self):
        return [_Ed(n) for n in self._n]


class _Nif:
    def __init__(self, names=()):
        self.rootNode = _Root(list(names))


# ------------------------------------------------------------ the resolver's key

def test_the_weight_suffix_is_stripped_exactly_as_the_resolver_does():
    """`cuirass_0.nif` and `cuirass_1.nif` both key on `cuirass`. If this
    diverged from the resolver, the census would count a different population
    than the one that races."""
    assert cen.stem_of("a/b/cuirass_0.nif") == "cuirass"
    assert cen.stem_of("a/b/cuirass_1.nif") == "cuirass"
    assert cen.stem_of("a/b/cuirass.nif") == "cuirass"
    assert cen.stem_of("a/b/Cuirass_1.NIF") == "cuirass"


def test_only_a_trailing_suffix_is_stripped():
    assert cen.stem_of("a/b/armor_0_plate.nif") == "armor_0_plate"


def test_a_declared_pointer_is_detected():
    assert cen.declares_xml(_Nif(["HDT Skinned Mesh Physics Object"]))
    assert not cen.declares_xml(_Nif(["BODYTRI"]))
    assert not cen.declares_xml(_Nif([]))


def test_an_unreadable_extra_data_walk_reads_as_NO_pointer():
    """`extra_data()` stops at the first block it cannot build, so a hidden
    pointer reads as absent and the piece is counted AT RISK when it may not
    be. That is the safe direction for an exposure count, and it is stated in
    the tool rather than left to be discovered."""
    class _Bad:
        @property
        def rootNode(self):
            raise RuntimeError("unbuildable block")
    assert cen.declares_xml(_Bad()) is False


# ------------------------------------------------ classification precedence

def _tree(tmp_path, *, pointer, own_xml, other_xml):
    root = tmp_path / "out"
    d = root / "meshes" / "!UBE" / "brand" / "f"
    d.mkdir(parents=True)
    (d / "cuirass_0.nif").write_bytes(b"x")
    if own_xml:
        (d / "cuirass.xml").write_text("<system/>", encoding="utf-8")
    if other_xml:
        e = root / "meshes" / "!UBE" / "somewhere" / "else"
        e.mkdir(parents=True)
        (e / "cuirass.xml").write_text("<system/>", encoding="utf-8")
    return root, pointer


def _classify(tmp_path, monkeypatch, **kw):
    root, pointer = _tree(tmp_path, **kw)
    monkeypatch.setattr(cen.nif_io, "open_nif_retry",
                        lambda p: _Nif(["HDT Skinned Mesh Physics Object"]
                                       if pointer else []))
    rows, _dropped = cen.scan(str(root))
    assert len(rows) == 1
    return rows[0]["cls"]


def test_a_declared_pointer_beats_everything(tmp_path, monkeypatch):
    got = _classify(tmp_path, monkeypatch, pointer=True, own_xml=False,
                    other_xml=True)
    assert got == "pointer"


def test_an_xml_beside_the_nif_is_safe(tmp_path, monkeypatch):
    got = _classify(tmp_path, monkeypatch, pointer=False, own_xml=True,
                    other_xml=True)
    assert got == "own-dir"


def test_a_same_stem_xml_only_ELSEWHERE_is_the_hazard(tmp_path, monkeypatch):
    got = _classify(tmp_path, monkeypatch, pointer=False, own_xml=False,
                    other_xml=True)
    assert got == "AT RISK"


def test_no_same_stem_xml_anywhere_is_deterministic(tmp_path, monkeypatch):
    got = _classify(tmp_path, monkeypatch, pointer=False, own_xml=False,
                    other_xml=False)
    assert got == "no-match"


# ------------------------------------------------------------- exit codes

def test_pieces_at_risk_exit_1(capsys):
    rows = [{"rel": "a/b.nif", "stem": "b", "cls": "AT RISK", "elsewhere": 2}]
    assert cen.report(rows, {}) == 1
    assert "AT RISK" in capsys.readouterr().out


def test_nothing_at_risk_exits_0():
    rows = [{"rel": "a/b.nif", "stem": "b", "cls": "pointer", "elsewhere": 0}]
    assert cen.report(rows, {}) == 0


def test_both_weight_halves_count_as_ONE_garment(capsys):
    rows = [{"rel": "a/b/cuirass_0.nif", "stem": "cuirass", "cls": "AT RISK",
             "elsewhere": 1},
            {"rel": "a/b/cuirass_1.nif", "stem": "cuirass", "cls": "AT RISK",
             "elsewhere": 1}]
    cen.report(rows, {})
    assert "as garments (both weight halves are one)  : 1" in \
        capsys.readouterr().out


def test_an_empty_output_dir_exits_3(tmp_path, capsys):
    arm = tmp_path / "arm"
    (arm / "meshes" / "!UBE").mkdir(parents=True)
    with pytest.raises(SystemExit) as ex:
        cen.main([str(arm)])
    assert ex.value.code == 3
    assert "0/0 is not a pass" in capsys.readouterr().out


def test_a_missing_dir_is_refused(tmp_path):
    assert cen.main([str(tmp_path / "nope")]) == 2
    assert cen.main([]) == 2
