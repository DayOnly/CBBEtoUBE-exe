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

"""#pair-weight-parity (audit 2026-10-20 F11).

Nearest-body weight copies looked the body up at each weight's own geometry, so `_0` and `_1` diverged where the
source pair's weights are identical. Once both are written, `_0` takes `_1`'s weights there.
"""
from types import SimpleNamespace

from src import nif_convert as nc
from src import nif_convert_writer as nwr
from src import nif_convert_weights as nw

A, B = "NPC Spine2 [Spn2]", "NPC L UpperArm [LUar]"


def _shape(rows):
    bw = {}
    for i, r in enumerate(rows):
        for b, w in r.items():
            bw.setdefault(b, []).append((i, w))
    return SimpleNamespace(name="Cuirass", verts=[(0, 0, 0)] * len(rows), bone_weights=bw, bone_names=list(bw),
                           get_shape_skin_to_bone=lambda b: "xf:" + b)


def _run(monkeypatch, tmp_path, src0, src1, out0, out1, on=True):
    files = {}
    for k, rows in (("o0", out0), ("o1", out1), ("s0", src0), ("s1", src1)):
        files[str(tmp_path / f"{k}.nif")] = SimpleNamespace(shapes=[_shape(rows)])
    (tmp_path / "piece_0.nif").write_bytes(b""); (tmp_path / "piece_1.nif").write_bytes(b"")
    files[str(tmp_path / "piece_0.nif")] = files.pop(str(tmp_path / "o0.nif"))
    files[str(tmp_path / "piece_1.nif")] = files.pop(str(tmp_path / "o1.nif"))
    monkeypatch.setattr(nc, "_pynifly", lambda: SimpleNamespace(NifFile=lambda filepath: files[str(filepath)]))
    got = {}
    monkeypatch.setattr(nwr, "_reauthor_nif_fresh", lambda p, nif=None, override_skin_by_name=None:
                        got.update(override_skin_by_name) or True)
    monkeypatch.setattr(nw, "PAIR_WEIGHT_PARITY", on)
    n = nw._pair_weight_parity(tmp_path / "piece_1.nif",
                               {"_0": str(tmp_path / "s0.nif"), "_1": str(tmp_path / "s1.nif")})
    return n, got


def test_where_the_author_has_one_weight_set_ours_does_too(monkeypatch, tmp_path):
    src = [{A: 1.0}, {A: 0.5, B: 0.5}]
    out0 = [{A: 0.7, B: 0.3}, {A: 0.9, B: 0.1}]
    out1 = [{A: 0.4, B: 0.6}, {A: 0.4, B: 0.6}]
    n, got = _run(monkeypatch, tmp_path, src, [{A: 1.0}, {A: 0.2, B: 0.8}], out0, out1)
    assert n == 1, "only the vert whose SOURCE weights agree"
    w = {b: dict(v) for b, v in got["Cuirass"]["weights"].items()}
    assert abs(w[A][0] - 0.4) < 1e-9 and abs(w[B][0] - 0.6) < 1e-9
    assert abs(w[A][1] - 0.9) < 1e-9, "a vert the author weights differently per weight is left alone"
    assert got["Cuirass"]["xforms"][A] == "xf:" + A


def test_already_equal_or_switched_off_writes_nothing(monkeypatch, tmp_path):
    same = [{A: 1.0}, {A: 0.5, B: 0.5}]
    assert _run(monkeypatch, tmp_path, same, same, same, same)[0] == 0
    assert _run(monkeypatch, tmp_path, same, same, [{A: 0.7, B: 0.3}] * 2, same, on=False)[0] == 0
