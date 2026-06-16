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

"""Unit tests for parsing RaceMenu's authoritative overlay slot registrations
(AddWarPaint / AddBodyPaint / AddHandPaint / AddFeetPaint)."""
from src import overlay_slots as osl


def test_normalize_script_texpath():
    # .psc string literals DOUBLE their backslashes -> must collapse to one slash
    assert osl.normalize_script_texpath(
        "Actors\\\\Character\\\\Overlays\\\\CO\\\\00 Body.dds"
    ) == "textures/actors/character/overlays/co/00 body.dds"
    # single backslash form + leading slash
    assert osl.normalize_script_texpath(
        "\\Actors\\Character\\Overlays\\x.dds"
    ) == "textures/actors/character/overlays/x.dds"
    # the alternate 'Character Assets' overlay root is preserved
    assert osl.normalize_script_texpath(
        "Actors\\\\Character\\\\Character Assets\\\\Overlays\\\\w.dds"
    ) == "textures/actors/character/character assets/overlays/w.dds"


def test_iter_paint_calls_slots():
    src = '''Event OnWarpaintRequest()
    AddWarPaint("CO 01 Face Mystic", "Actors\\\\Character\\\\Overlays\\\\CO\\\\01 Head M.dds")
EndEvent
Event OnBodyPaintRequest()
    AddBodyPaint("CO 00 Body Lusaria", "Actors\\\\Character\\\\Overlays\\\\CO\\\\00 Body.dds")
EndEvent
Event OnHandPaintRequest()
    AddHandPaint("CO 00 Hands Lusaria", "Actors\\\\Character\\\\Overlays\\\\CO\\\\00 Hands.dds")
EndEvent
Event OnFeetPaintRequest()
    AddFeetPaint("CO 00 Feet Lusaria", "Actors\\\\Character\\\\Overlays\\\\CO\\\\00 Body.dds")
EndEvent'''
    got = dict()
    for slot, rel in osl.iter_paint_calls(src):
        got.setdefault(rel, set()).add(slot)
    body = "textures/actors/character/overlays/co/00 body.dds"
    head = "textures/actors/character/overlays/co/01 head m.dds"
    hands = "textures/actors/character/overlays/co/00 hands.dds"
    assert got[head] == {"head"}
    assert got[hands] == {"hands"}
    # the SAME body texture is registered as body AND feet -> multi-slot
    assert got[body] == {"body", "feet"}
