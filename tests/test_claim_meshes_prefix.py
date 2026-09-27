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

r"""#claim-meshes-prefix -- a hand-made UBE patch whose armature spells its
model path with the `meshes\` folder in front is still a UBE claim.

THE DEFECT. The engine reads `meshes\!UBE\x_1.nif` and `!UBE\x_1.nif` as the
same file, and the SkyPatcher half of the claim test strips the folder. The
plugin half compared the path as written, so a softbody pack's own UBE nude
suits never read as covered (10 armours on the live pack).
`CBBE2UBE_NO_CLAIM_MESHES_PREFIX=1` compares the path as written again.
"""
import struct

import pytest

from src import auto_convert as ac
from src.esp import Group, encode_subrecord
from tests.test_skypatcher_patch_recognition import (UBE_RACE, HEAD, _arma,
                                                     _armo, _save)

OFF = "CBBE2UBE_NO_CLAIM_MESHES_PREFIX"
SUIT = r"!UBE\SoftPack\nudesuit_1.nif"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    ac._UBE_COVERED_CACHE.clear()
    yield
    ac._UBE_COVERED_CACHE.clear()


def _pack(root, mod3, *, ship_mesh=True):
    """A softbody pack whose own plugin lists a UBE-path armature on its armour."""
    pack = root / "Softbody Pack"
    arma = _arma(0x02000800, (1 << 24) | UBE_RACE, HEAD, mod3)
    armo = _armo(0x02000801, HEAD)
    armo.payload += encode_subrecord(b"MODL", struct.pack("<I", 0x02000800))
    _save(pack / "Pack.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
          [Group(label=b"ARMA", records=[arma]), Group(label=b"ARMO", records=[armo])])
    if ship_mesh:
        mesh = pack / "meshes" / SUIT.replace("\\", "/")
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(b"presence is all the check reads")
    return ("pack.esp", 0x801)


def test_the_folder_spelled_out_is_the_same_file(tmp_path):
    target = _pack(tmp_path, "meshes\\" + SUIT)
    assert target in ac._third_party_ube_covered_armos(tmp_path)


def test_switched_off_the_path_is_compared_as_written(tmp_path, monkeypatch):
    target = _pack(tmp_path, "meshes\\" + SUIT)
    monkeypatch.setenv(OFF, "1")
    assert target not in ac._third_party_ube_covered_armos(tmp_path)


def test_the_plain_path_is_unchanged(tmp_path):
    """Control: the usual spelling was, and stays, a claim."""
    target = _pack(tmp_path, SUIT)
    assert target in ac._third_party_ube_covered_armos(tmp_path)


def test_a_missing_mesh_is_still_no_claim(tmp_path):
    """Skipping is the dangerous direction: the claim still needs the mesh."""
    target = _pack(tmp_path, "meshes\\" + SUIT, ship_mesh=False)
    assert target not in ac._third_party_ube_covered_armos(tmp_path)


def test_the_switch_is_part_of_the_cache_key(tmp_path, monkeypatch):
    target = _pack(tmp_path, "meshes\\" + SUIT)
    assert target in ac._third_party_ube_covered_armos(tmp_path)
    monkeypatch.setenv(OFF, "1")
    assert target not in ac._third_party_ube_covered_armos(tmp_path)
