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

"""Guards for the machine-readable conversion report + verify-output re-check
(the GUI health panel's data layer)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import auto_convert as ac


def _res(nifs=3, err=0, notes=None):
    return NS(nif_results=[None] * nifs, nif_copy_count=nifs, nif_swap_count=0,
              nif_skipped=0, nif_errors=err, nif_load_failures=[],
              vfs_other_mod_count=0, output_esps=["a.esp"], source_esps=["s.esp"],
              notes=notes or [], textures_copied=0)


def test_write_conversion_report_json(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    results = [
        (NS(name="ModA"), _res(3), None),
        (NS(name="ModB"), _res(0, notes=["collision skipped"]), None),  # dup
        (NS(name="ModC"), _res(0), None),                               # missing
        (NS(name="ModD"), None, RuntimeError("boom")),                  # failed
    ]
    p = ac.write_conversion_report_json(out, results,
                                        weight_warnings=["ModX shape _0 vs _1"])
    rep = json.loads(p.read_text(encoding="utf-8"))
    assert rep["source_mods"] == 4
    assert rep["converted_ok"] == 3
    assert rep["hard_failures"] == 1
    assert rep["armor_nifs"] == 3
    assert rep["zero_mesh_dup_mods"] == ["ModB"]
    assert rep["zero_mesh_mods"] == ["ModC"]
    assert rep["failed_mods"][0]["name"] == "ModD"
    assert rep["weight_partner_warnings"] == ["ModX shape _0 vs _1"]


def test_verify_output_reads_report_and_rescans(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "conversion_report.json").write_text('{"source_mods": 5}',
                                                encoding="utf-8")
    monkeypatch.setattr(ac, "_postflight_weight_partner_divergence",
                        lambda o: ["w1", "w2"])
    res = ac.verify_output(out)
    assert res["exists"] is True
    assert res["report"]["source_mods"] == 5
    assert res["weight_partner_warnings"] == ["w1", "w2"]


def test_verify_output_missing_dir_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_postflight_weight_partner_divergence",
                        lambda o: [])
    res = ac.verify_output(tmp_path / "nope")
    assert res["exists"] is False
    assert "report" not in res
