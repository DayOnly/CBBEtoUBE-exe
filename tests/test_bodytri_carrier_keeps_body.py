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

"""#bodytri-all-shapes means the BODY *and* every cloth shape -- on EVERY path.

Two body-swap NIFs in the pack shipped with no BODYTRI on their `BaseShape` at
all; that is the whole of the census's "BODYTRI present on the body: 589/591".

TRACED, not guessed. At BODYTRI time the offending NIF held exactly two shapes:
`BaseShape` and one cloth shape whose name contains "chain" -- a
NON_CLOTH_SHAPE_KEYWORD. So `candidates` came out empty (the cloth
keyword-excluded, the body in `BODYTRI_CARRIER_EXCLUDE`) and the
carrier-of-last-resort branch returned that one cloth shape, dropping the body.
The body-first `head` was only ever prepended on the branch that HAD cloth
candidates.

Preference 1 in `_pick_bodytri_carriers` is the body precisely because
NioOverride morphs every shape in the TRI when the BODYTRI sits on the body and
often skips shapes when it sits on cloth -- so this dropped exactly the carrier
that matters most.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.nif_convert as nc  # noqa: E402
from src.nif_convert_trigen import _pick_bodytri_carriers  # noqa: E402


class _Shape:
    def __init__(self, name, verts=100, textures=None):
        self.name = name
        self.verts = [(0.0, 0.0, 0.0)] * verts
        self.textures = {"Diffuse": "t.dds"} if textures is None else textures


class _Nif:
    def __init__(self, shapes):
        self.shapes = shapes


@pytest.fixture(autouse=True)
def _no_extremity(monkeypatch):
    """The extremity test reads bone weights these stubs do not have."""
    monkeypatch.setattr(nc, "_shape_is_extremity_dominant", lambda s: False)


def _names(nif, **kw):
    return [s.name for s in _pick_bodytri_carriers(nif, **kw)]


def test_the_body_survives_the_carrier_of_last_resort():
    """The regression itself: every cloth shape keyword-excluded."""
    nif = _Nif([_Shape("cuirasschain_1", 2291), _Shape("BaseShape", 29298)])
    assert _names(nif, all_cloth=True) == ["BaseShape", "cuirasschain_1"], (
        "a body-swap NIF whose only cloth is keyword-excluded still needs the "
        "BODYTRI on its body")


def test_the_body_survives_the_hand_foot_fallback(monkeypatch):
    """The other single-shape return path, for a gauntlet/boot NIF."""
    monkeypatch.setattr(nc, "_shape_is_extremity_dominant",
                        lambda s: s.name == "HandsF")
    nif = _Nif([_Shape("HandsF", 900), _Shape("BaseShape", 29298)])
    assert _names(nif, all_cloth=True) == ["BaseShape", "HandsF"]


def test_a_body_with_no_cloth_at_all_is_still_tagged():
    nif = _Nif([_Shape("BaseShape", 29298)])
    assert _names(nif, all_cloth=True) == ["BaseShape"]


def test_exclude_body_still_excludes_the_body_on_those_paths():
    """The HDT-XML generator calls with `exclude_body=True` on purpose: the body
    is the kinematic COLLIDER, and picking it there made the injected body flop
    as soft-body cloth while the real cape got no physics. The fix must not
    reach that caller."""
    nif = _Nif([_Shape("cuirasschain_1", 2291), _Shape("BaseShape", 29298)])
    assert _names(nif, all_cloth=True, exclude_body=True) == ["cuirasschain_1"]


def test_the_ordinary_paths_are_unchanged():
    """Body first, then cloth, with no duplicate -- and the single-carrier call
    still returns just the body."""
    nif = _Nif([_Shape("Robe", 3000), _Shape("Belt", 400),
                _Shape("BaseShape", 29298)])
    got = _names(nif, all_cloth=True)
    assert got[0] == "BaseShape"
    assert sorted(got[1:]) == ["Belt", "Robe"]
    assert got.count("BaseShape") == 1
    assert _names(nif) == ["BaseShape"]
