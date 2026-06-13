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

"""Silent-failure surfacing: a NIF that converts but DROPS a shape (the piece is
absent/invisible in-game) must be counted + reported as a PARTIAL conversion,
not folded into a clean 'converted' success with no signal."""
from pathlib import Path

from src.nif_convert import ConvertResult
from src.auto_convert import AutoConvertResult


def _cr(name, dropped=None):
    return ConvertResult(src_path=Path(name), dst_path=Path(name + ".out"),
                         status="converted (copy)", dropped_shapes=dropped or [])


def test_nif_partial_counts_only_dropped():
    acr = AutoConvertResult(
        source_dir=Path("."), output_dir=Path("."),
        nif_results=[_cr("a", ["X"]), _cr("b"), _cr("c", ["Y", "Z"])])
    assert acr.nif_partial == 2
    # a clean converted NIF must NOT be counted partial
    assert AutoConvertResult(source_dir=Path("."), output_dir=Path("."),
                             nif_results=[_cr("b")]).nif_partial == 0


def test_write_report_surfaces_partial_bucket(tmp_path):
    acr = AutoConvertResult(
        source_dir=tmp_path, output_dir=tmp_path,
        nif_results=[_cr(str(tmp_path / "armor.nif"), ["ShoulderStrap"])])
    rep = tmp_path / "report.txt"
    acr.write_report(rep)
    txt = rep.read_text()
    assert "PARTIAL conversions" in txt
    assert "ShoulderStrap" in txt


def test_write_report_no_partial_bucket_when_clean(tmp_path):
    acr = AutoConvertResult(
        source_dir=tmp_path, output_dir=tmp_path,
        nif_results=[_cr(str(tmp_path / "armor.nif"))])
    rep = tmp_path / "report.txt"
    acr.write_report(rep)
    assert "PARTIAL conversions" not in rep.read_text()


def test_esp_gen_failures_field_defaults_empty():
    acr = AutoConvertResult(source_dir=Path("."), output_dir=Path("."))
    assert acr.esp_gen_failures == []
