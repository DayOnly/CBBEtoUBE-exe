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

r"""#wig-exclude-keep -- an excluded mod's wig keeps its coverage in the body
pass exactly when the non-body pass would keep it.

THE DEFECT. #exclude-body-only (the user's call) withholds only an excluded
mod's BODY pieces: the non-body pass keeps a non-body piece no other mod
patches (`_excluded_piece_holds`). #wig-body-pass mints a wig whose armour also
says a deforming slot in the BODY pass, and that pass withholds everything an
excluded mod owns -- so such a wig was always withheld and named in the
'no UBE armature from any mod' warning, while the same wig on a hair-only
armour was kept.

THE RULE. A wig the body pass mints alone is a non-body piece: the same keep
test decides it. An armour a deforming armature was admitted for is withheld
whole, as before. `CBBE2UBE_NO_WIG_EXCLUDE_KEEP=1` withholds the wig again.
"""
import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from tests.test_exclude_body_only import _ini
from tests.test_wig_body_pass import (ARMOUR, BODY, BODY_MESH, HAIR, WIG,
                                      WOODELF, WOODELF_VAMP, _arma, _body,
                                      _wig, _world)

OFF = "CBBE2UBE_NO_WIG_EXCLUDE_KEEP"
BODY_ONLY_OFF = "CBBE2UBE_NO_EXCLUDE_BODY_ONLY"
WIG_BODY_OFF = "CBBE2UBE_NO_WIG_BODY_PASS"
UNARMATURED = "have no UBE armature from any mod"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, BODY_ONLY_OFF, WIG_BODY_OFF):
        monkeypatch.delenv(k, raising=False)


def _excluded(tmp_path, monkeypatch, armas=None, *, probe=True, **kw):
    """The body pass with the wig's mod excluded, over a modlist whose only
    enabled mods are `mods/Follower Mod` and `mods/Refit Mod`."""
    mods = tmp_path / "mods"
    monkeypatch.setattr(
        ac, "_exclusion_keep_probe",
        (lambda: ac._ExclusionKeepProbe(mods, ["Follower Mod", "Refit Mod"]))
        if probe else (lambda: None))
    world = _world(tmp_path, armas or [_wig()], **kw.pop("world", {}))
    return _body(tmp_path, world, withheld_armo_abs={ARMOUR}, **kw)


def _kept(st):
    return [a for a, _e in st["exclusion_nonbody_kept"]]


def _held(st):
    return [a for a, _e in st["withheld"]]


# ------------------------------------------------------------------ the rule

def test_an_excluded_mods_wig_is_kept_with_its_own_mesh(tmp_path, monkeypatch):
    st, minted = _excluded(tmp_path, monkeypatch)
    assert [m["mod3"] for m in minted] == [WIG], "its own mesh, nothing converted"
    assert _held(st) == [] and _kept(st) == [ARMOUR]
    assert [a for a, _e in st["wigs"]] == [ARMOUR]


def test_the_kept_wig_is_a_note_not_an_unarmatured_warning(tmp_path, monkeypatch, capsys):
    st, _minted = _excluded(tmp_path, monkeypatch)
    ac._report_coverage_holds([st])
    text = capsys.readouterr().out
    assert UNARMATURED not in text
    assert "1 non-body armour(s) of an excluded mod" in text
    assert "FollowerWig" in text and "mod.esp|000810" in text


def test_a_race_listed_wig_is_kept_for_its_races(tmp_path, monkeypatch):
    """The live shape: a Wood-Elf-primary wig listing the Wood Elf vampire."""
    st, minted = _excluded(
        tmp_path, monkeypatch, [_wig(primary=WOODELF, extra=(WOODELF_VAMP,))])
    assert [m["mod3"] for m in minted] == [WIG]
    assert _kept(st) == [ARMOUR] and _held(st) == []


# ------------------------------------------------------------- withheld still

def test_a_wig_another_mods_patch_names_is_left_to_it(tmp_path, monkeypatch, capsys):
    _ini(tmp_path / "mods", "Refit Mod",
         "filterByArmors=Mod.esp|810:armorAddonsToAdd=Refit.esp|800\n")
    st, minted = _excluded(tmp_path, monkeypatch)
    assert minted == [] and _held(st) == [ARMOUR] and _kept(st) == []
    assert st["exclusion_body_held"] == [(ARMOUR, "FollowerWig", "named by Refit Mod")]
    ac._report_coverage_holds([st])
    text = capsys.readouterr().out
    assert UNARMATURED not in text and "patched by Refit Mod" in text


def test_no_modlist_withholds_the_wig(tmp_path, monkeypatch):
    st, minted = _excluded(tmp_path, monkeypatch, probe=False)
    assert minted == [] and _held(st) == [ARMOUR] and _kept(st) == []
    assert st["exclusion_body_held"] == []


def test_an_armour_with_a_deforming_armature_is_withheld_whole(tmp_path, monkeypatch):
    """Control: a converted body armature was admitted, so the hair armature
    rides along as an accessory -- a body piece of an excluded mod, withheld
    as before, wig included."""
    st, minted = _excluded(
        tmp_path, monkeypatch, [_arma(0x02000801, BODY, BODY_MESH), _wig()],
        world={"slots": HAIR | BODY}, conv={BODY_MESH.replace("\\", "/")})
    assert minted == [] and _held(st) == [ARMOUR] and _kept(st) == []


def test_a_dead_wig_is_not_counted_as_kept(tmp_path, monkeypatch):
    st, minted = _excluded(tmp_path, monkeypatch, dead_mesh_exists=lambda p: False)
    assert minted == [] and _held(st) == [] and _kept(st) == []
    assert [a for a, _e in st["dead_dropped"]] == [ARMOUR]


# ------------------------------------------------------------- the switches

def test_switched_off_the_wig_is_withheld_again(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    st, minted = _excluded(tmp_path, monkeypatch)
    assert minted == [] and _held(st) == [ARMOUR] and _kept(st) == []


def test_the_exclusion_rule_switch_turns_it_off_too(tmp_path, monkeypatch):
    monkeypatch.setenv(BODY_ONLY_OFF, "1")
    st, minted = _excluded(tmp_path, monkeypatch)
    assert minted == [] and _held(st) == [ARMOUR] and _kept(st) == []


def test_without_the_wig_rule_the_wig_is_neither_minted_nor_withheld(tmp_path, monkeypatch):
    monkeypatch.setenv(WIG_BODY_OFF, "1")
    st, minted = _excluded(tmp_path, monkeypatch)
    assert minted == [] and _held(st) == [] and _kept(st) == []


def test_the_switch_reads_its_flag(monkeypatch):
    assert up._wig_exclude_keep()
    monkeypatch.setenv(OFF, "1")
    assert not up._wig_exclude_keep()
