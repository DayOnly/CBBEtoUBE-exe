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

r"""#reconcile-loaded-winner -- a colour set on one of our `!UBE\` paths is
indexed against the copy the game loads also when our output HAS a NIF there
but another mod's loose copy outranks it in MO2.

THE DEFECT. #reconcile-loaded-mesh asked for the game's copy only when our NIF
was missing, on the premise that #skip-built-ube-path always moved our copy out
first. A held base, a partly shipped base, a failed supersede move, the switch,
or an earlier run's copy leave ours on disk; a UBE BodySlide output above our
output in MO2 is then what the game draws, while the set was indexed against
ours. `CBBE2UBE_NO_RECONCILE_LOADED_WINNER=1` uses our own NIF whenever it
exists, as before.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, ube_patcher as up           # noqa: E402
from tests.synthetic_nif import build_skinned_shapes_nif        # noqa: E402
from tests.test_alttex_exact_provenance import GREEN, RED, TAN   # noqa: E402
from tests.test_reconcile_loaded_mesh import (                   # noqa: E402
    BUILD, MODEL, REL, SET, _modlist, _mo3s, _plugin, _put, _shapes,
    needs_pynifly)

OFF = "CBBE2UBE_NO_RECONCILE_LOADED_WINNER"
OURS = ["hood", "Skirt", "Outer", "BaseShape"]


@pytest.fixture(autouse=True)
def _switches_unset(monkeypatch):
    for k in (OFF, "CBBE2UBE_NO_RECONCILE_LOADED_MESH"):
        monkeypatch.delenv(k, raising=False)


def _game_loads(monkeypatch, hit, outranks):
    """The lookup answers `hit` for every path; `outranks` says whether that
    copy outranks our output's. Records what each was asked."""
    asked, ranked = [], []

    def lookup(meshes_root):
        def copy(model):
            asked.append(model)
            return hit

        def over(model):
            ranked.append(model)
            return outranks
        copy.outranks_output = over
        return copy
    monkeypatch.setattr(up, "_loaded_mesh_lookup", lookup)
    return asked, ranked


def _both(tmp_path):
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             _shapes(OURS))
    return build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))


# --- the reconcile ---------------------------------------------------------------

@needs_pynifly
def test_an_outranked_nif_of_ours_is_indexed_against_the_winner(
        monkeypatch, tmp_path, capsys):
    build = _both(tmp_path)
    asked, ranked = _game_loads(monkeypatch, (build, None), True)
    plugin = _plugin(tmp_path / "out", SET)
    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes") == 1
    assert _mo3s(plugin) == [("Skirt", RED, 12), ("Outer", GREEN, 10),
                             ("hood", TAN, 0)]
    assert asked == [MODEL] and ranked == [MODEL]
    err = capsys.readouterr().err
    assert ("0 model(s) not in this output indexed against the copy the game "
            "loads") in err
    assert ("1 model(s) this output ships but another mod's copy outranks in "
            "MO2, indexed against that copy") in err


@needs_pynifly
def test_a_nif_of_ours_the_game_loads_is_used_as_before(monkeypatch, tmp_path):
    build = _both(tmp_path)
    asked, ranked = _game_loads(monkeypatch, (build, None), False)
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("Skirt", RED, 1), ("Outer", GREEN, 2),
                             ("hood", TAN, 0)]
    assert asked == [] and ranked == [MODEL]


@needs_pynifly
def test_switched_off_our_nif_is_used_whenever_it_exists(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    build = _both(tmp_path)
    asked, ranked = _game_loads(monkeypatch, (build, None), True)
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("Skirt", RED, 1), ("Outer", GREEN, 2),
                             ("hood", TAN, 0)]
    assert asked == [] and ranked == []


@needs_pynifly
def test_with_the_loaded_mesh_rule_off_nothing_is_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("CBBE2UBE_NO_RECONCILE_LOADED_MESH", "1")
    build = _both(tmp_path)
    asked, ranked = _game_loads(monkeypatch, (build, None), True)
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin)[0] == ("Skirt", RED, 1)
    assert asked == [] and ranked == []


def _junk_build(tmp_path) -> Path:
    """Another mod's copy that is no NIF at all; ours is a good one."""
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             _shapes(OURS))
    junk = tmp_path / "builder" / REL
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_bytes(b"not a nif")
    return junk


OURS_INDEXED = [("Skirt", RED, 1), ("Outer", GREEN, 2), ("hood", TAN, 0)]


@needs_pynifly
@pytest.mark.parametrize("not_found", [False, True],
                         ids=["unreadable", "not-found"])
def test_an_outranking_copy_that_cannot_be_read_falls_back_to_ours(
        monkeypatch, tmp_path, capsys, not_found):
    """The set is indexed against our own NIF, as with the rule off -- never
    left in the source's order -- and the problem is its own class, not
    another mod's copy of a model 'not in this output'."""
    junk = _junk_build(tmp_path)
    _game_loads(monkeypatch, None if not_found else (junk, None), True)
    plugin = _plugin(tmp_path / "out", SET)
    problems = []
    assert up.reconcile_alt_texture_indices(
        plugin, tmp_path / "out" / "meshes", problems=problems) == 1
    assert _mo3s(plugin) == OURS_INDEXED
    assert problems == [(up.ALTTEX_OUTRANKING_COPY_UNREADABLE, [MODEL])]
    err = capsys.readouterr().err
    assert ("1 model(s) where another mod's copy outranks ours but could not "
            "be read, indexed against ours") in err
    assert "this output ships but another mod's copy outranks" not in err
    assert "0 found nowhere" in err


@needs_pynifly
def test_standalone_the_outranking_copy_is_said_on_its_own_line(
        monkeypatch, tmp_path, capsys):
    junk = _junk_build(tmp_path)
    _game_loads(monkeypatch, (junk, None), True)
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    err = capsys.readouterr().err
    assert ("!! alt-texture reconcile: 1 model(s) where another mod's copy "
            "outranks ours but could not be read -> indexed against ours") in err
    assert "not in this output" not in err.split("!!", 1)[1]


def test_a_path_that_is_not_ours_is_never_ranked(monkeypatch, tmp_path):
    asked, ranked = _game_loads(monkeypatch, None, True)
    _put(tmp_path / "out" / "meshes" / REL)
    plugin = _plugin(tmp_path / "out", SET, model=REL)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert asked == [] and ranked == []


# --- which loose copy outranks our output: MO2 priority ------------------------------

def _outranks(tmp_path, monkeypatch, order):
    mods = _modlist(tmp_path, monkeypatch, order=order)
    look = up._loaded_mesh_lookup(mods / "Out" / "meshes")
    return mods, look.outranks_output


REL_PATH = Path("meshes") / "!UBE" / REL


def test_a_mod_above_our_output_outranks_it(monkeypatch, tmp_path):
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Out", "Low"))
    _put(mods / "Out" / REL_PATH, b"OURS")
    assert not over(MODEL)                        # ours alone
    _put(mods / "Low" / REL_PATH, b"LOW")
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Out", "Low"))
    assert not over(MODEL)                        # a mod below: ours wins
    _put(mods / "High" / REL_PATH, b"HIGH")
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Out", "Low"))
    assert over(MODEL)


def test_the_overwrite_outranks_our_output(monkeypatch, tmp_path):
    mods, over = _outranks(tmp_path, monkeypatch, ("Out", "Low"))
    _put(tmp_path / "overwrite" / REL_PATH, b"BUILT")
    mods, over = _outranks(tmp_path, monkeypatch, ("Out", "Low"))
    assert over(MODEL)


def test_an_archived_copy_never_outranks_our_loose_file(monkeypatch, tmp_path):
    """The stand-in archive lists boots_1.nif: loose beats archived."""
    mods, over = _outranks(tmp_path, monkeypatch, ("Out", "Low"))
    assert not over("!UBE\\Clothes\\Travel\\boots_1.nif")


def test_with_our_output_not_enabled_nothing_is_known_to_outrank_it(
        monkeypatch, tmp_path):
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Low"))
    _put(mods / "High" / REL_PATH, b"HIGH")
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Low"))
    assert not over(MODEL)


def test_without_an_overwrite_folder_the_mods_above_still_count(
        monkeypatch, tmp_path):
    mods, over = _outranks(tmp_path, monkeypatch, ("High", "Out", "Low"))
    _put(mods / "High" / REL_PATH, b"HIGH")
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: None)
    look = up._loaded_mesh_lookup(mods / "Out" / "meshes")
    assert look.outranks_output(MODEL)
    _put(mods / "Low" / REL_PATH, b"LOW")
    (mods / "High" / REL_PATH).unlink()
    look = up._loaded_mesh_lookup(mods / "Out" / "meshes")
    assert not look.outranks_output(MODEL)
