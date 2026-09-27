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

"""#selection-winner-playable -- source selection reads an armour's playable
flag from its WINNING record, the one the game uses.

The planner read the flag from each scanned plugin's own ARMO record. A later
plugin that makes an armour non-playable (a balance patch taking a set out of
the game) left it planned and converted; one that makes a non-playable armour
playable left it skipped. The coverage passes already judge the winner. Now a
map {(defining plugin, low id) -> winner non-playable or deleted}, built once
per load order from the plugins the game loads, replaces the record's flag; an
armour the map does not know keeps its record's flag. The worn rule
(#npc-worn-nonplayable) is judged against the same flag, and which armatures a
plugin admits is still judged per plugin.
`CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE=1` reads each record's own flag again."""
import argparse
import struct

import pytest

from src import auto_convert as ac
from src import esp, paths
from tests.test_npc_worn_nonplayable import _arma, _armo, _rec, _sub

OFF = "CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE"
WORN_OFF = "CBBE2UBE_NO_NPC_WORN_NONPLAYABLE"
INDEX_OFF = "CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN"
NONPLAYABLE, DELETED, ESL = 0x04, 0x20, 0x200

# Armour.esp: master 0 Skyrim.esm, so its own records carry index 1 -- and so
# do the overrides in a plugin whose masters are Skyrim.esm, Armour.esp.
AA, AR = 0x01000800, 0x01000900
IDENT = ("armour.esp", AR & 0xFFFFFF)
CUIRASS = "armor/set/cuirass"
BOOTS_AA, BOOTS = 0x01000810, 0x01000910


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(WORN_OFF, raising=False)
    ac._ARMO_WINNER_CACHE.clear()
    yield
    ac._ARMO_WINNER_CACHE.clear()


def _plugin(path, masters=("Skyrim.esm",), flags=0, **groups):
    path.parent.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=list(masters), flags=flags),
            groups=[esp.Group(label=k.encode(), records=v)
                    for k, v in groups.items()]).save(path)
    return path


def _armour_mod(tmp_path, playable=True, armos=None, armas=None, boots=False):
    """A mod whose plugin defines one cuirass armature and its armour. `boots`
    adds a playable pair no patch touches: the control that the mod is read."""
    mod = tmp_path / "mods" / "Armour Mod"
    (mod / "meshes" / "armor" / "set").mkdir(parents=True)
    for m in ("cuirass_1.nif", "variant_1.nif", "boots_1.nif"):
        (mod / "meshes" / "armor" / "set" / m).write_bytes(b"x")
    armas = armas or [_arma(AA, "armor\\set\\cuirass_1.nif")]
    armos = armos or [_armo(AR, AA, nonplayable=not playable)]
    if boots:
        armas = armas + [_arma(BOOTS_AA, "armor\\set\\boots_1.nif")]
        armos = armos + [_armo(BOOTS, BOOTS_AA, nonplayable=False)]
    _plugin(mod / "Armour.esp", ARMA=armas, ARMO=armos)
    return mod


def _patch(tmp_path, nonplayable, record_flags=0, name="Patch.esp",
           plugin_flags=0, folder=None):
    """A later plugin that overrides the cuirass's armour (same formid)."""
    rec = _armo(AR, AA, nonplayable=nonplayable)
    rec = _rec(b"ARMO", AR, rec.payload, flags=rec.flags | record_flags)
    return _plugin((folder or tmp_path) / name, masters=("Skyrim.esm", "Armour.esp"),
                   flags=plugin_flags, ARMO=[rec])


def _winner(*plugins):
    flags, bad = ac._armo_winner_nonplayable(list(plugins))
    assert bad == []
    return flags


# --- the rule, through the planner ---------------------------------------------

def test_a_winner_that_makes_it_non_playable_takes_it_out(tmp_path):
    """(a) Defined playable, overridden non-playable, worn by nobody."""
    mod = _armour_mod(tmp_path)
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=True))
    assert flags == {IDENT: True}
    assert ac._player_armor_mesh_bases(mod) == {CUIRASS}, "control: own record"
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == set()


def test_a_non_playable_winner_an_npc_wears_is_still_planned(tmp_path):
    """(b) The worn rule is judged against the winner's flag: the scanned record
    is playable, so its own flag would never ask the worn set."""
    mod = _armour_mod(tmp_path)
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=True))
    kept: set = set()
    assert ac._player_armor_mesh_bases(
        mod, armo_winner_nonplayable=flags, npc_worn_armos=frozenset({IDENT}),
        worn_admitted=kept) == {CUIRASS}
    assert kept == {("armour.esp", AA)}


def test_a_winner_that_makes_it_playable_brings_it_in(tmp_path):
    """(c) Defined non-playable, overridden playable."""
    mod = _armour_mod(tmp_path, playable=False)
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=False))
    assert flags == {IDENT: False}
    assert ac._player_armor_mesh_bases(mod) == set(), "control: own record"
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == {CUIRASS}


def test_a_deleted_winner_is_not_playable(tmp_path):
    """(d) A deleted override takes the armour out of the game, whatever its
    playable bit says."""
    mod = _armour_mod(tmp_path)
    flags = _winner(mod / "Armour.esp",
                    _patch(tmp_path, nonplayable=False, record_flags=DELETED))
    assert flags == {IDENT: True}
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == set()


def test_only_the_last_loaded_record_counts(tmp_path):
    """Load order decides: the same override loaded BEFORE the plugin it
    overrides is not the winner."""
    mod = _armour_mod(tmp_path)
    patch = _patch(tmp_path, nonplayable=True)
    assert _winner(patch, mod / "Armour.esp") == {IDENT: False}


def test_an_armour_whose_defining_plugin_is_not_loaded(tmp_path):
    """The defining plugin is not in the load order (or holds no record of the
    armour): the first loaded record stands in for it, and the LAST loaded one
    still wins -- keyed on the defining plugin's name, the identity the
    planner asks by."""
    first = _patch(tmp_path, nonplayable=False, name="First.esp")
    last = _patch(tmp_path, nonplayable=True, name="Last.esp")
    assert _winner(first, last) == {IDENT: True}
    assert _winner(last, first) == {IDENT: False}


def test_an_esl_flagged_override_lands_on_its_masters_armour(tmp_path):
    """(e) The identity is the DEFINING plugin through the master list, whatever
    the overriding plugin's ESL/ESM flag; its own new armour keys on itself."""
    mod = _armour_mod(tmp_path)
    own = _rec(b"ARMO", 0x02000901, _armo(0x02000901, AA).payload, flags=NONPLAYABLE)
    over = _armo(AR, AA, nonplayable=True)
    light = _plugin(tmp_path / "Light.esp", masters=("Skyrim.esm", "Armour.esp"),
                    flags=ESL, ARMO=[over, own])
    flags = _winner(mod / "Armour.esp", light)
    assert flags == {IDENT: True, ("light.esp", 0x000901): True}
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == set()


def test_a_template_variant_keeps_its_own_flag(tmp_path):
    """(f) A variant (TNAM) keeps its own models and its own flag: a winner that
    takes its template out of the game does not take the variant with it."""
    tpl_aa, var_aa, tpl, var = 0x01000800, 0x01000801, 0x01000900, 0x01000901
    variant = _armo(var, var_aa, nonplayable=False)
    variant = _rec(b"ARMO", var, variant.payload + _sub(b"TNAM", struct.pack("<I", tpl)))
    mod = _armour_mod(tmp_path, armos=[_armo(tpl, tpl_aa, nonplayable=False), variant],
                      armas=[_arma(tpl_aa, "armor\\set\\cuirass_1.nif"),
                             _arma(var_aa, "armor\\set\\variant_1.nif")])
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=True))
    assert flags == {IDENT: True, ("armour.esp", 0x000901): False}
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == {
        "armor/set/variant"}


@pytest.mark.parametrize("playable, want", [(True, {CUIRASS}), (False, set())],
                         ids=["playable-record", "non-playable-record"])
def test_an_armour_the_map_does_not_know_keeps_its_own_flag(tmp_path, playable, want):
    """(g) Not every scanned plugin is in the load order (a disabled one, a
    folder converted on its own): its armour keeps its record's flag."""
    mod = _armour_mod(tmp_path, playable=playable)
    flags = {("other.esp", 0x000900): True, ("other.esp", 0x000901): False}
    assert ac._player_armor_mesh_bases(mod, armo_winner_nonplayable=flags) == want


def test_the_map_reads_only_the_armour_group(tmp_path):
    """Every active plugin is read; only its ARMO group is parsed. A plugin that
    cannot be read is named, and the rest still count."""
    mod = _armour_mod(tmp_path)
    junk = tmp_path / "Junk.esp"
    junk.write_bytes(b"not a plugin")
    flags, bad = ac._armo_winner_nonplayable(
        [mod / "Armour.esp", junk, _patch(tmp_path, nonplayable=True)])
    assert flags == {IDENT: True} and bad == ["Junk.esp"]


# --- the batch: the game's view, its own switch, built once, fails open --------

@pytest.fixture
def load_order(tmp_path, monkeypatch):
    """The armour mod (a cuirass, and boots no patch touches), and a patch mod
    loaded after it that makes the cuirass non-playable. Plugin files resolve
    the way the game loads them."""
    mod = _armour_mod(tmp_path, boots=True)
    _patch(tmp_path, nonplayable=True, folder=tmp_path / "mods" / "Patch Mod")
    lay = paths.Layout(mods_root=tmp_path / "mods", instance_dir=tmp_path)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "active_plugins_ordered",
                        lambda l: ["Armour.esp", "Patch.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered",
                        lambda l: ["Patch Mod", "Armour Mod"])
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    ac._ARMOR_MOD_DIRS_CACHE.clear()
    yield mod
    ac._ARMOR_MOD_DIRS_CACHE.clear()


def _sources(mods):
    """(mod, meshes it would convert) per selected source."""
    return [(c["name"], c["armor_nifs"]) for c in ac._find_armor_mod_dirs(
        mods, require_arma=True, enabled_ordered=["Patch Mod", "Armour Mod"])]


def _lo_map(cuirass_nonplayable):
    return {IDENT: cuirass_nonplayable, ("armour.esp", BOOTS & 0xFFFFFF): False}


def test_selection_follows_the_winner_and_the_switch(load_order, monkeypatch):
    """(h)+(i) The patch makes the cuirass non-playable, so selection counts the
    boots alone. Switched off it counts both again -- in the same process: the
    switch is part of the selection memo key, so a GUI refresh then convert
    never reuses the other rule's list."""
    mods = load_order.parent
    assert _sources(mods) == [("Armour Mod", 1)]
    monkeypatch.setenv(OFF, "1")
    assert _sources(mods) == [("Armour Mod", 2)]
    monkeypatch.delenv(OFF)
    assert _sources(mods) == [("Armour Mod", 1)]


def test_a_mod_whose_only_armour_the_winner_takes_out_is_no_source(tmp_path,
                                                                    monkeypatch):
    """The eligibility test itself takes the map, not only the mesh count."""
    mod = _armour_mod(tmp_path)
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=True))
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    for got, want in ((flags, []), (None, [("Armour Mod", 1)])):
        monkeypatch.setattr(ac, "_batch_armo_winner_nonplayable", lambda g=got: g)
        assert [(c["name"], c["armor_nifs"]) for c in ac._find_armor_mod_dirs_uncached(
            mod.parent, require_arma=True, enabled_ordered=["Armour Mod"])] == want


def test_switched_off_nothing_is_read(load_order, monkeypatch):
    """(h) The off-switch is the old rule: no map, no plugin read."""
    monkeypatch.setenv(OFF, "1")
    monkeypatch.setattr(ac, "_armo_winner_nonplayable",
                        lambda p: pytest.fail("switched off, no plugin may be read"))
    assert ac._batch_armo_winner_nonplayable() is None


def test_eligibility_itself_follows_the_winner(tmp_path, monkeypatch):
    """A mod is a source only by a body-slot piece the winner keeps playable.
    Here that is the cuirass alone -- its playable skirt sits on an ambiguous
    modder slot (44), which counts only once the mod is a source -- so a patch
    that takes the cuirass out makes the mod no source at all."""
    skirt_aa, skirt = 0x01000820, 0x01000920
    skirt_arma = _arma(skirt_aa, "armor\\set\\variant_1.nif")
    skirt_arma = _rec(b"ARMA", skirt_aa, skirt_arma.payload.replace(
        struct.pack("<II", 1 << 2, 0), struct.pack("<II", 1 << 14, 0)))
    mod = _armour_mod(tmp_path,
                      armas=[_arma(AA, "armor\\set\\cuirass_1.nif"), skirt_arma],
                      armos=[_armo(AR, AA, nonplayable=False),
                             _armo(skirt, skirt_aa, nonplayable=False)])
    flags = _winner(mod / "Armour.esp", _patch(tmp_path, nonplayable=True))
    assert ac._player_armor_mesh_bases(mod, include_candidate_slots=True,
                                       armo_winner_nonplayable=flags) == {
        "armor/set/variant"}, "control: the skirt alone is planned"
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    for got, want in ((flags, []), (None, [("Armour Mod", 2)])):
        monkeypatch.setattr(ac, "_batch_armo_winner_nonplayable", lambda g=got: g)
        assert [(c["name"], c["armor_nifs"]) for c in ac._find_armor_mod_dirs_uncached(
            mod.parent, require_arma=True, enabled_ordered=["Armour Mod"])] == want


def test_the_vanilla_sweep_keys_follow_the_winner(tmp_path, monkeypatch):
    """The vanilla sweep's mesh keys take the map too: a vanilla piece a patch
    takes out of the game is not located, so its loose replacer is not read."""
    data = tmp_path / "Game" / "Data"
    _plugin(data / "Skyrim.esm", masters=(),
            ARMA=[_arma(0x000800, "clothes\\vanilla\\robe_1.nif")],
            ARMO=[_armo(0x000900, 0x000800, nonplayable=False)])
    rep = tmp_path / "mods" / "Robe Replacer" / "meshes" / "clothes" / "vanilla"
    rep.mkdir(parents=True)
    (rep / "robe_1.nif").write_bytes(b"x")
    lay = paths.Layout(mods_root=tmp_path / "mods", game_data_dirs=[data])
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    monkeypatch.delenv("CBBE2UBE_NO_VANILLA_SWEEP", raising=False)
    mods = tmp_path / "mods"
    for got, located in (({("skyrim.esm", 0x000900): True}, False), (None, True)):
        monkeypatch.setattr(ac, "_batch_armo_winner_nonplayable", lambda g=got: g)
        ac._BATCH_MESH_INDEX.clear()
        ac._find_armor_mod_dirs_uncached(mods, require_arma=True,
                                         enabled_ordered=["Robe Replacer"])
        idx = ac._BATCH_MESH_INDEX.get(str(mods).lower(), {})
        assert ("clothes/vanilla/robe_1.nif" in idx) is located
    ac._BATCH_MESH_INDEX.clear()


def test_the_npc_worn_switch_does_not_turn_it_off(load_order, monkeypatch):
    """(j) Its own switch: CBBE2UBE_NO_NPC_WORN_NONPLAYABLE=1 turns off the worn
    rule only."""
    monkeypatch.setenv(WORN_OFF, "1")
    assert ac._batch_armo_winner_nonplayable() == _lo_map(True)
    assert _sources(load_order.parent) == [("Armour Mod", 1)]


def test_the_map_is_built_once_per_load_order(load_order, monkeypatch):
    calls = []
    real = ac._armo_winner_nonplayable
    monkeypatch.setattr(ac, "_armo_winner_nonplayable",
                        lambda p: calls.append(1) or real(p))
    first = ac._batch_armo_winner_nonplayable()
    assert ac._batch_armo_winner_nonplayable() is first and len(calls) == 1
    # The user moves the patch above the armour: a new map, the armour playable.
    monkeypatch.setattr(paths, "active_plugins_ordered",
                        lambda l: ["Patch.esp", "Armour.esp"])
    assert ac._batch_armo_winner_nonplayable() == _lo_map(False)
    assert len(calls) == 2


def test_it_reads_the_plugin_the_game_loads(load_order, tmp_path, monkeypatch):
    """Only ROOT plugin files load. A higher-priority mod holding a copy of the
    patch in a subfolder (our own un-loaded per-source copies live that way)
    must not stand in for it -- even with the legacy recursive index switched
    back on for the other callers."""
    ours = tmp_path / "mods" / "Ours"
    _patch(tmp_path, nonplayable=False, folder=ours / "_unmerged_patches")
    monkeypatch.setattr(paths, "enabled_mods_ordered",
                        lambda l: ["Ours", "Patch Mod", "Armour Mod"])
    monkeypatch.setenv(INDEX_OFF, "1")
    lay = paths.discover_layout()
    # Control: the legacy index does resolve the name to the subfolder copy.
    assert paths.plugin_file_index(lay)["patch.esp"].parent.name == "_unmerged_patches"
    assert ac._batch_armo_winner_nonplayable() == _lo_map(True)


def test_a_failed_build_fails_open_and_says_so(load_order, monkeypatch, capsys):
    """(k) A read error: each record's own flag decides, as before, with one
    warning per load order."""
    calls = []

    def _boom(p):
        calls.append(1)
        raise OSError("disk gone")
    monkeypatch.setattr(ac, "_armo_winner_nonplayable", _boom)
    assert ac._batch_armo_winner_nonplayable() is None
    out = capsys.readouterr().out
    assert "!! could not read which armour the load order makes playable" in out
    assert "OSError: disk gone" in out
    assert ac._batch_armo_winner_nonplayable() is None and len(calls) == 1
    assert "!!" not in capsys.readouterr().out, "warned once"
    assert _sources(load_order.parent) == [("Armour Mod", 2)], "the old rule"


@pytest.mark.parametrize("names", [None, ["Missing.esp"]],
                         ids=["unreadable-order", "no-plugin-file"])
def test_a_modlist_without_a_readable_load_order_fails_open(load_order, monkeypatch,
                                                            capsys, names):
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: names)
    assert ac._batch_armo_winner_nonplayable() is None
    assert "!! could not read which armour the load order makes playable" in (
        capsys.readouterr().out)


def test_an_unreadable_plugin_is_named(load_order, tmp_path, monkeypatch, capsys):
    (tmp_path / "mods" / "Junk Mod").mkdir()
    (tmp_path / "mods" / "Junk Mod" / "Junk.esp").write_bytes(b"not a plugin")
    monkeypatch.setattr(paths, "active_plugins_ordered",
                        lambda l: ["Armour.esp", "Junk.esp", "Patch.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered",
                        lambda l: ["Junk Mod", "Patch Mod", "Armour Mod"])
    assert ac._batch_armo_winner_nonplayable() == _lo_map(True)
    assert "!! 1 active plugin(s) could not be read for armour playability: Junk.esp" in (
        capsys.readouterr().out)


def test_no_modlist_reads_nothing_and_says_nothing(monkeypatch, capsys):
    """A folder converted on its own has no load order: the old rule, silently."""
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    assert ac._batch_armo_winner_nonplayable() is None
    assert "!!" not in capsys.readouterr().out


# --- wiring: the convert step gets the same map ---------------------------------

def test_the_convert_step_plans_by_the_winner(load_order, tmp_path, monkeypatch):
    """auto_convert_mod hands the map to its planner. Stops at the resolver,
    which receives the planned bases."""
    class _Planned(Exception):
        pass

    def _stop(bases, *a, **k):
        raise _Planned(sorted(bases))
    monkeypatch.setattr(ac, "_resolve_armor_meshes", _stop)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    for flags, want in ((ac._batch_armo_winner_nonplayable(), ["armor/set/boots"]),
                        (None, ["armor/set/boots", CUIRASS])):
        with pytest.raises(_Planned) as got:
            ac.auto_convert_mod(load_order, tmp_path / "out", ube_body_ref_path=ref,
                                master_data_dirs=[], armo_winner_nonplayable=flags)
        assert got.value.args[0] == want


def _convert_args(tmp_path, src):
    return argparse.Namespace(
        sources=[src], output=tmp_path / "out", esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


class _Stop(BaseException):           # `_cmd_convert` catches every Exception
    pass


@pytest.fixture
def direct_convert(load_order, tmp_path, monkeypatch):
    """`convert` on its own: no source selection ran in this process."""
    monkeypatch.setenv(paths.MODS_ROOT_ENV, str(load_order.parent))
    monkeypatch.delenv(paths.GAME_DATA_ENV, raising=False)
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    ac._BATCH_MESH_INDEX.clear()
    yield lambda: ac._cmd_convert(_convert_args(tmp_path, load_order))
    ac._BATCH_MESH_INDEX.clear()


@pytest.mark.parametrize("off", [False, True], ids=["on", "switched-off"])
def test_a_direct_convert_asks_the_vfs_by_the_winner(direct_convert, monkeypatch, off):
    """The VFS index is asked for the meshes the planner names: not the cuirass
    the winner takes out (the boots are the control); switched off, both."""
    from src import discovery

    def _index(mods_root, enabled, target_keys=None, skip_mods=None, **kw):
        raise _Stop(set(target_keys or ()))
    monkeypatch.setattr(discovery, "build_mesh_index", _index)
    if off:
        monkeypatch.setenv(OFF, "1")
    with pytest.raises(_Stop) as got:
        direct_convert()
    assert "armor/set/boots_1.nif" in got.value.args[0]
    assert ("armor/set/cuirass_1.nif" in got.value.args[0]) is off


@pytest.mark.parametrize("off", [False, True], ids=["on", "switched-off"])
def test_a_direct_convert_hands_each_source_the_map(direct_convert, monkeypatch, off):
    from src import discovery
    monkeypatch.setattr(discovery, "build_mesh_index", lambda *a, **k: {})

    def _convert(src, output, **kw):
        raise _Stop(kw.get("armo_winner_nonplayable", "not passed"))
    monkeypatch.setattr(ac, "auto_convert_mod", _convert)
    if off:
        monkeypatch.setenv(OFF, "1")
    with pytest.raises(_Stop) as got:
        direct_convert()
    assert got.value.args[0] == (None if off else _lo_map(True))
