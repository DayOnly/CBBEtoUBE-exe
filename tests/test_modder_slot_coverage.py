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

"""Coverage + crash-guard for lower-body cloth on ambiguous modder slots (#165).

DDV Ruby Flower binds its pants to biped slot 44 and its skirt to slot 47 —
free modder slots that the strict `_BODY_SLOT_BITS` allowlist excludes (44 is
beard/mouth, 47 is backpack in vanilla). So those lower-body pieces were never
converted and the game kept loading the CBBE-shaped originals under the UBE
morph. The fix admits those slots as armour-mesh CANDIDATES, but only keeps a
candidate-slot mesh if its NIF is skinned to body-fit bones — preserving the
allowlist's crash protection (an unskinned accessory given a UBE body race CTDs
at actor setup).
"""
from src import auto_convert


def _bit(slot):
    return 1 << (slot - 30)


def test_candidate_slots_cover_pants44_skirt47():
    assert auto_convert._BODY_CANDIDATE_SLOT_BITS & _bit(44)  # Ruby pants
    assert auto_convert._BODY_CANDIDATE_SLOT_BITS & _bit(47)  # Ruby skirt


def test_candidate_slots_are_NOT_in_strict_allowlist():
    # The whole point: 44/47 must stay OUT of the strict set (crash protection),
    # only admitted via the candidate path + body-fit gate.
    for slot in (44, 45, 47, 48, 59, 61):
        assert not (auto_convert._BODY_SLOT_BITS & _bit(slot)), slot


def test_strict_and_candidate_sets_are_disjoint():
    assert (auto_convert._BODY_SLOT_BITS
            & auto_convert._BODY_CANDIDATE_SLOT_BITS) == 0


class _FakeShape:
    def __init__(self, bones):
        self.bone_names = bones


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def _patch_loader(monkeypatch, nif):
    from src import nif_io
    monkeypatch.setattr(nif_io, "load_nif", lambda p: nif)


def test_bodyfit_skin_detects_lowerbody_cloth(monkeypatch):
    # pants/skirt: weighted to thighs/butt/pelvis -> body cloth -> KEEP
    _patch_loader(monkeypatch, _FakeNif([
        _FakeShape(["NPC L Thigh [LThg]", "NPC R Calf [RClf]", "NPC L Butt"])]))
    assert auto_convert._nif_has_bodyfit_skin("pants.nif") is True


def test_bodyfit_skin_rejects_head_accessory(monkeypatch):
    # a beard on slot 44: head bones only -> NOT body cloth -> DROP (crash guard)
    _patch_loader(monkeypatch, _FakeNif([
        _FakeShape(["NPC Head [Head]", "NPC Neck [Neck]"])]))
    assert auto_convert._nif_has_bodyfit_skin("beard.nif") is False


def test_bodyfit_skin_rejects_spine_only_backpack(monkeypatch):
    # a backpack/cloak on slot 47: spine only (deliberately excluded) -> DROP
    _patch_loader(monkeypatch, _FakeNif([
        _FakeShape(["NPC Spine2 [Spn2]", "WeaponBack"])]))
    assert auto_convert._nif_has_bodyfit_skin("backpack.nif") is False


def test_bodyfit_skin_failsafe_on_load_error(monkeypatch):
    # fail safe: a NIF we can't read is treated as NOT body cloth (don't convert)
    from src import nif_io

    def boom(_p):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(nif_io, "load_nif", boom)
    assert auto_convert._nif_has_bodyfit_skin("x.nif") is False
