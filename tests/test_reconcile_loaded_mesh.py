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

r"""#reconcile-loaded-mesh -- a colour set on one of our `!UBE\` paths that our
output has no NIF at is indexed against the copy of that path the GAME loads.

THE DEFECT. The alt-texture reconcile looked each ARMA model up only in our
output's meshes folder. Since #skip-built-ube-path and #supersede-whole-base a
base the user's own BodySlide build ships is left to that build, so the
reconcile found no NIF and kept the source plugin's CBBE-era indices: a
colour variant recoloured the wrong shape of the build the game loads (on the
reported modlist a skirt colour landed on the build's collision body). The
copy the game loads is found read-only, as the coverage step finds it (loose
by MO2 priority, overwrite first, our output left out, then the archives). It
is another mod's mesh, so its entries bind by name -- never through OUR source
-- and a name it carries twice, or a set repeats, loses its entries.
`CBBE2UBE_NO_RECONCILE_LOADED_MESH=1` leaves such a set untouched.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auto_convert as ac, esp, ube_patcher as up      # noqa: E402
from tests.synthetic_nif import (build_skinned_shapes_nif,       # noqa: E402
                                 pynifly_available, uv_sphere)
from tests.test_alttex_exact_provenance import (                 # noqa: E402
    BLUE, GREEN, RED, TAN, _alt, _parse)

OFF = "CBBE2UBE_NO_RECONCILE_LOADED_MESH"
REL = r"Clothes\Travel\torso_1.nif"
MODEL = "!UBE\\" + REL
needs_pynifly = pytest.mark.skipif(not pynifly_available(),
                                   reason="pynifly native lib not available")


@pytest.fixture(autouse=True)
def _switch_unset(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


def _shapes(names):
    return [(nm, *uv_sphere(10.0 + i)) for i, nm in enumerate(names)]


# The source plugin's set, authored against a CBBE mesh whose 'Skirt' was 3D
# index 13; the build the game loads has it at 12 (its own body at 13).
SET = [("Skirt", RED, 13), ("Outer", GREEN, 10), ("hood", TAN, 0)]
BUILD = ["hood", "a", "b", "c", "d", "e", "f", "g", "h", "i", "Outer", "j",
         "Skirt", "BaseShape"]


def _plugin(out: Path, entries, model=MODEL) -> Path:
    payload = (esp.encode_subrecord(b"EDID", b"TravelAA\x00")
               + esp.encode_subrecord(b"MOD3", model.encode() + b"\x00")
               + esp.encode_subrecord(b"MO3S", _alt(entries)))
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                     version_unk=0x002C, payload=payload)
    plugin = out / "Combined.esp"
    out.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
            groups=[esp.Group(label=b"ARMA", records=[rec])]).save(plugin)
    return plugin


def _mo3s(plugin: Path):
    payload = esp.ESP.load(plugin).groups[0].records[0].payload
    return _parse(next(d for s, d in esp.iter_subrecords(payload)
                       if s == b"MO3S"))


def _game_loads(monkeypatch, hit):
    """The lookup answers `hit` for every path, and records what it was asked."""
    asked = []

    def lookup(meshes_root):
        def copy(model):
            asked.append(model)
            return hit
        return copy
    monkeypatch.setattr(up, "_loaded_mesh_lookup", lookup)
    return asked


def _no_source_read(monkeypatch):
    def refuse(meshes_root, keys):
        raise AssertionError("a loaded third-party mesh must not read OUR source")
    monkeypatch.setattr(up, "_alttex_source_paths", refuse)


# --- the fix -----------------------------------------------------------------

@needs_pynifly
def test_a_path_left_to_another_build_is_indexed_against_that_build(
        monkeypatch, tmp_path, capsys):
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    asked = _game_loads(monkeypatch, (build, None))
    plugin = _plugin(tmp_path / "out", SET)
    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes") == 1
    assert _mo3s(plugin) == [("Skirt", RED, 12), ("Outer", GREEN, 10),
                             ("hood", TAN, 0)]
    assert asked == [MODEL]
    assert "1 model(s) not in this output indexed against the copy the game " \
           "loads" in capsys.readouterr().err


@needs_pynifly
def test_the_switch_leaves_the_set_as_authored(monkeypatch, tmp_path):
    monkeypatch.setenv(OFF, "1")
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    asked = _game_loads(monkeypatch, (build, None))
    plugin = _plugin(tmp_path / "out", SET)
    before = plugin.read_bytes()
    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes") == 0
    assert plugin.read_bytes() == before
    assert asked == []


@needs_pynifly
def test_our_own_nif_wins_and_the_game_copy_is_never_asked(monkeypatch, tmp_path):
    build_skinned_shapes_nif(tmp_path / "out" / "meshes" / "!UBE" / REL,
                             _shapes(["hood", "Skirt", "Outer", "BaseShape"]))
    other = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    asked = _game_loads(monkeypatch, (other, None))
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("Skirt", RED, 1), ("Outer", GREEN, 2),
                             ("hood", TAN, 0)]
    assert asked == []


def test_a_path_found_nowhere_keeps_its_set_and_is_counted(
        monkeypatch, tmp_path, capsys):
    asked = _game_loads(monkeypatch, None)
    plugin = _plugin(tmp_path / "out", SET)
    before = plugin.read_bytes()
    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes") == 0
    assert plugin.read_bytes() == before
    assert asked == [MODEL]
    assert "1 found nowhere -> kept as authored" in capsys.readouterr().err


def test_a_path_that_is_not_ours_is_never_looked_up(monkeypatch, tmp_path):
    """A source path (a male mesh, a vanilla one) was never our conversion:
    its set was authored for the mesh the game loads and stays as it is."""
    asked = _game_loads(monkeypatch, None)
    plugin = _plugin(tmp_path / "out", SET, model=REL)
    before = plugin.read_bytes()
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert plugin.read_bytes() == before
    assert asked == []


# --- a third-party mesh is not our conversion ---------------------------------

@needs_pynifly
def test_a_third_party_mesh_binds_by_name_without_our_source(monkeypatch,
                                                             tmp_path):
    """'fur' beside 'fur:1' looks like our rename; in another mod's mesh it
    is that author's own name. Our source is never read to bind it, and
    nothing is dropped as 'may be split'."""
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL,
                                     _shapes(["fur:1", "coat", "fur"]))
    _game_loads(monkeypatch, (build, None))
    _no_source_read(monkeypatch)
    plugin = _plugin(tmp_path / "out", [("fur", RED, 5), ("coat", TAN, 0)])
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("fur", RED, 2), ("coat", TAN, 1)]


@needs_pynifly
def test_a_name_the_loaded_mesh_carries_twice_loses_its_entries(monkeypatch,
                                                                tmp_path):
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL,
                                     _shapes(["Skirt", "hood", "skirt"]))
    _game_loads(monkeypatch, (build, None))
    _no_source_read(monkeypatch)
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("hood", TAN, 1)]


@needs_pynifly
def test_a_name_a_set_repeats_loses_its_entries(monkeypatch, tmp_path):
    """Two 'Skirt' entries prove the source had two skirts; which one the
    build's single 'Skirt' is, is not known."""
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    _game_loads(monkeypatch, (build, None))
    plugin = _plugin(tmp_path / "out", [("Skirt", RED, 13), ("Skirt", BLUE, 14),
                                        ("hood", TAN, 0)])
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin) == [("hood", TAN, 0)]


@needs_pynifly
def test_an_archived_copy_is_read_from_a_temporary_file_that_is_removed(
        monkeypatch, tmp_path):
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    _game_loads(monkeypatch, (None, build.read_bytes()))
    plugin = _plugin(tmp_path / "out", SET)
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert _mo3s(plugin)[0] == ("Skirt", RED, 12)
    staging = tmp_path / "out" / "_bsa_staging"
    assert not staging.exists(), "a staging folder it made is removed again"


@needs_pynifly
def test_an_existing_staging_folder_is_kept(monkeypatch, tmp_path):
    build = build_skinned_shapes_nif(tmp_path / "builder" / REL, _shapes(BUILD))
    _game_loads(monkeypatch, (None, build.read_bytes()))
    plugin = _plugin(tmp_path / "out", SET)
    staging = tmp_path / "out" / "_bsa_staging"
    staging.mkdir(parents=True)
    (staging / "keep.nif").write_bytes(b"x")
    up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes")
    assert sorted(p.name for p in staging.iterdir()) == ["keep.nif"]


def test_an_unreadable_game_copy_is_named_as_another_mods(
        monkeypatch, tmp_path, capsys):
    """Another mod's copy that will not load is not one of our converted NIFs:
    it gets its own line, and the set stays as authored."""
    junk = tmp_path / "builder" / REL
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_bytes(b"not a nif")
    _game_loads(monkeypatch, (junk, None))
    plugin = _plugin(tmp_path / "out", SET)
    before = plugin.read_bytes()
    assert up.reconcile_alt_texture_indices(plugin, tmp_path / "out" / "meshes") == 0
    assert plugin.read_bytes() == before
    err = capsys.readouterr().err
    assert "the copy the game loads (another mod's) could not be read" in err
    assert "converted NIF(s) failed to load" not in err


# --- the copy the game loads: the coverage step's own lookup ------------------

class _Archive:
    """Stands in for the load-order archive index."""
    data = {"!ube/clothes/travel/torso_1.nif": b"ARCHIVED",
            "!ube/clothes/travel/boots_1.nif": b"ARCHIVED BOOTS"}

    def __init__(self, dirs, staging, **kw):
        assert staging is None, "the lookup never extracts"

    def contains(self, key):
        return key in self.data

    def read_bytes(self, key):
        return self.data.get(key)


def _modlist(tmp_path, monkeypatch, order=("Out", "High", "Low")):
    mods = tmp_path / "mods"
    for n in order:
        (mods / n).mkdir(parents=True, exist_ok=True)
    (tmp_path / "overwrite").mkdir(exist_ok=True)

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered", lambda lay: list(order))
    monkeypatch.setattr(ac.paths, "overwrite_dir", lambda lay: tmp_path / "overwrite")
    monkeypatch.setattr(ac, "_BsaMeshIndex", _Archive)
    return mods


def _put(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_the_copy_the_game_loads_is_loose_by_priority_then_archived(
        monkeypatch, tmp_path):
    mods = _modlist(tmp_path, monkeypatch)
    rel = Path("meshes") / "!UBE" / REL
    _put(mods / "Out" / rel, b"OURS")               # our output: left out
    high = _put(mods / "High" / rel, b"HIGH")
    _put(mods / "Low" / rel, b"LOW")
    look = up._loaded_mesh_lookup(mods / "Out" / "meshes")
    assert look(MODEL) == (high, None)
    over = _put(tmp_path / "overwrite" / rel, b"BUILT")
    look = up._loaded_mesh_lookup(mods / "Out" / "meshes")
    assert look(MODEL) == (over, None)             # MO2's overwrite first
    assert look("!UBE\\Clothes\\Travel\\boots_1.nif") == (None, b"ARCHIVED BOOTS")
    assert look("!UBE\\Clothes\\Travel\\gloves_1.nif") is None


def test_no_modlist_means_no_lookup(monkeypatch, tmp_path):
    def broken(*a, **k):
        raise RuntimeError("no MO2 instance")
    monkeypatch.setattr(ac.paths, "discover_layout", broken)
    assert up._loaded_mesh_lookup(tmp_path / "out" / "meshes") is None
