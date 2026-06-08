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

"""Guard for _complete_weight_partners (#180): every converted weighted body
mesh must have BOTH _0 and _1 on disk; the safety net copies the present weight
to any missing partner. Weight-agnostic meshes (no _0/_1) and already-complete
bases are left alone."""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.auto_convert import _complete_weight_partners


def _touch(p: Path, data=b"NIF"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_fills_missing_partner(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    # _1-only base (the bug) -> should get a _0 copy
    _touch(ube / "clothes" / "robe" / "robef_1.nif", b"ONE")
    # already-complete base -> untouched
    _touch(ube / "armor" / "x" / "cuirass_0.nif", b"A0")
    _touch(ube / "armor" / "x" / "cuirass_1.nif", b"A1")
    # weight-agnostic mesh (no _0/_1) -> ignored
    _touch(ube / "armor" / "x" / "helmet.nif", b"H")

    filled = _complete_weight_partners(tmp_path)
    assert filled == 1
    miss = ube / "clothes" / "robe" / "robef_0.nif"
    assert miss.is_file()
    assert miss.read_bytes() == b"ONE"                 # copied from the present _1
    # complete base + weight-agnostic mesh: no spurious files created
    assert not (ube / "armor" / "x" / "helmet_0.nif").exists()
    assert (ube / "armor" / "x" / "cuirass_0.nif").read_bytes() == b"A0"  # untouched


def test_fills_missing_1_from_0(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "a" / "thing_0.nif", b"ZERO")          # _0-only -> get _1
    assert _complete_weight_partners(tmp_path) == 1
    assert (ube / "a" / "thing_1.nif").read_bytes() == b"ZERO"


def test_noop_when_all_complete(tmp_path):
    ube = tmp_path / "meshes" / "!UBE"
    _touch(ube / "a" / "b_0.nif"); _touch(ube / "a" / "b_1.nif")
    assert _complete_weight_partners(tmp_path) == 0


def test_no_ube_dir_is_safe(tmp_path):
    assert _complete_weight_partners(tmp_path) == 0     # no meshes/!UBE -> 0
