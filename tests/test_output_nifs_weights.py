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

"""`output_nifs` must see BOTH weight files, and must not see first-person.

Both halves are load-bearing and were both real defects found 2026-07-29:
  * fifteen validation scripts globbed `*_1.nif` only, while one cuirass
    measured 4.52% bust-front clipping at weight 1 and 9.48% at weight 0 --
    the worse half of the shipped output was invisible to every check;
  * a `1stp*` first-person mesh (arms, no torso) slipped past an exclusion that
    only matched `1stperson*` and flagged at 8.47u standoff, a meaningless
    number for a piece that never covers the bust.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

import standoff_audit as sa  # noqa: E402


def _tree(tmp_path):
    (tmp_path / "armor" / "hide" / "f").mkdir(parents=True)
    for n in ("cuirass_0.nif", "cuirass_1.nif",
              "1stpersoncuirass_0.nif", "1stpersoncuirass_1.nif",
              "1stpCuirassF_0.nif", "1stpCuirassF_1.nif"):
        (tmp_path / "armor" / "hide" / "f" / n).write_bytes(b"x")
    (tmp_path / "armor" / "hide" / "f" / "notes.txt").write_bytes(b"x")
    return tmp_path


def test_both_weights_by_default(tmp_path):
    got = {p.name for p in sa.output_nifs(_tree(tmp_path))}
    assert got == {"cuirass_0.nif", "cuirass_1.nif"}, (
        "default must cover BOTH weights -- weight 0 is a separately authored "
        "mesh, not a scaled copy, and measured WORSE on real pieces")


def test_single_weight_selectable(tmp_path):
    root = _tree(tmp_path)
    assert {p.name for p in sa.output_nifs(root, weights="1")} == {"cuirass_1.nif"}
    assert {p.name for p in sa.output_nifs(root, weights="0")} == {"cuirass_0.nif"}


def test_first_person_excluded_including_short_prefix(tmp_path):
    names = {p.name for p in sa.output_nifs(_tree(tmp_path))}
    assert not any("1stp" in n.lower() for n in names), (
        "must drop the SHORT 1stp prefix too, not only 1stperson")


def test_first_person_can_be_kept_explicitly(tmp_path):
    got = {p.name for p in sa.output_nifs(_tree(tmp_path),
                                          exclude_first_person=False)}
    assert len(got) == 6


def test_non_nif_files_ignored(tmp_path):
    assert all(p.suffix == ".nif" for p in sa.output_nifs(_tree(tmp_path)))


def test_result_is_sorted_and_deduped(tmp_path):
    got = sa.output_nifs(_tree(tmp_path))
    assert got == sorted(got)
    assert len(got) == len(set(got))
