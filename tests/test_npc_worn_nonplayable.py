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

"""#npc-worn-nonplayable -- non-playable armour a female NPC WEARS is converted.

Selection skipped every armature only non-playable armour references, meaning
gore and effect "armour" applied by script. But follower and quest outfits are
flagged non-playable too, and the coverage step deliberately gives non-playable
body and hands/feet armour a UBE armature -- so it drew the unconverted CBBE
mesh on a UBE actor. A non-playable ARMO counts as worn now when the WINNING
record of a female NPC of a UBE-capable race reaches it through its default or
sleep outfit or its inventory, via outfits and leveled lists. Script-applied
gore is reached by no outfit, so it stays out (tests/test_gore_filter.py), and
a form any NPC or race uses as its skin never counts, however it is reached."""
import struct

import pytest

from src import esp, paths
from src.esp import encode_subrecord, encode_zstring
from src import auto_convert as ac

OFF = "CBBE2UBE_NO_NPC_WORN_NONPLAYABLE"
DEFAULT_RACE = 0x00000019
NORD = 0x00013746                     # Skyrim.esm NordRace (master index 0)
BODY = 1 << 2
NONPLAYABLE = 0x04


def _rec(sig, fid, payload, flags=0):
    return esp.Record(sig=sig, flags=flags, formid=fid, payload=payload)


def _sub(sig, data):
    return encode_subrecord(sig, data)


def _arma(fid, mesh):
    return _rec(b"ARMA", fid, _sub(b"EDID", encode_zstring(f"AA{fid:X}"))
                + _sub(b"BOD2", struct.pack("<II", BODY, 0))
                + _sub(b"RNAM", struct.pack("<I", DEFAULT_RACE))
                + _sub(b"MOD3", encode_zstring(mesh)))


def _armo(fid, arma, nonplayable=True):
    return _rec(b"ARMO", fid, _sub(b"EDID", encode_zstring(f"AR{fid:X}"))
                + _sub(b"BOD2", struct.pack("<II", BODY, 0))
                + _sub(b"MODL", struct.pack("<I", arma))
                + _sub(b"DATA", struct.pack("<If", 10, 1.0)),
                flags=NONPLAYABLE if nonplayable else 0)


def _otft(fid, *items):
    return _rec(b"OTFT", fid, _sub(b"EDID", encode_zstring(f"OT{fid:X}"))
                + _sub(b"INAM", b"".join(struct.pack("<I", i) for i in items)))


def _lvli(fid, *items):
    p = _sub(b"EDID", encode_zstring(f"LI{fid:X}"))
    for i in items:
        p += _sub(b"LVLO", struct.pack("<HHIHH", 1, 0, i, 1, 0))
    return _rec(b"LVLI", fid, p)


def _npc(fid, *, female=True, race=NORD, doft=None, soft=None, cnto=(),
         wnam=None, template=None, use_traits=False):
    acbs = struct.pack("<IhhHHHHhHhH", 1 if female else 0, 0, 0, 1, 0, 0, 100, 35,
                       1 if use_traits else 0, 0, 0)
    p = _sub(b"EDID", encode_zstring(f"NPC{fid:X}")) + _sub(b"ACBS", acbs)
    p += _sub(b"RNAM", struct.pack("<I", race))
    if template is not None:
        p += _sub(b"TPLT", struct.pack("<I", template))
    if wnam is not None:
        p += _sub(b"WNAM", struct.pack("<I", wnam))
    for item in cnto:
        p += _sub(b"CNTO", struct.pack("<Ii", item, 1))
    if doft is not None:
        p += _sub(b"DOFT", struct.pack("<I", doft))
    if soft is not None:
        p += _sub(b"SOFT", struct.pack("<I", soft))
    return _rec(b"NPC_", fid, p)


def _race(fid, edid, wnam=None):
    p = _sub(b"EDID", encode_zstring(edid))
    if wnam is not None:
        p += _sub(b"WNAM", struct.pack("<I", wnam))
    return _rec(b"RACE", fid, p)


def _plugin(path, masters=("Skyrim.esm",), **groups):
    path.parent.mkdir(parents=True, exist_ok=True)
    esp.ESP(header=esp.TES4Header(masters=list(masters)),
            groups=[esp.Group(label=k.encode().ljust(4, b"_")[:4], records=v)
                    for k, v in groups.items()]).save(path)
    return path


# The outfit mod: a non-playable dress, a non-playable gore limb, a playable
# pair of boots, an outfit -> leveled list -> dress, and the female Nord who
# wears the outfit.
DRESS_AA, DRESS, GORE_AA, GORE = 0x01000800, 0x01000900, 0x01000801, 0x01000901
BOOTS_AA, BOOTS = 0x01000802, 0x01000902
OUTFIT, LIST, WEARER = 0x01000A00, 0x01000B00, 0x01000C00


def _outfit_mod(tmp_path, npc=None, name="Outfit Mod", boots=True, more_npcs=()):
    mod = tmp_path / "mods" / name
    (mod / "meshes" / "armor" / "outfit").mkdir(parents=True)
    for m in ("dress_1.nif", "severed_1.nif", "boots_1.nif"):
        (mod / "meshes" / "armor" / "outfit" / m).write_bytes(b"x")
    _plugin(mod / "Outfit.esp",
            ARMA=[_arma(DRESS_AA, "armor\\outfit\\dress_1.nif"),
                  _arma(GORE_AA, "armor\\outfit\\severed_1.nif")]
            + ([_arma(BOOTS_AA, "armor\\outfit\\boots_1.nif")] if boots else []),
            ARMO=[_armo(DRESS, DRESS_AA), _armo(GORE, GORE_AA)]
            + ([_armo(BOOTS, BOOTS_AA, nonplayable=False)] if boots else []),
            OTFT=[_otft(OUTFIT, LIST)], LVLI=[_lvli(LIST, DRESS)],
            NPC_=[npc if npc is not None else _npc(WEARER, doft=OUTFIT)]
            + list(more_npcs))
    return mod


def _worn(*plugins):
    return ac._npc_worn_armos(list(plugins))


def test_a_dress_a_female_npc_wears_is_converted_and_gore_is_not(tmp_path):
    mod = _outfit_mod(tmp_path)
    worn = _worn(mod / "Outfit.esp")
    assert ("outfit.esp", DRESS & 0xFFFFFF) in worn     # outfit -> list -> dress
    assert ("outfit.esp", GORE & 0xFFFFFF) not in worn
    bases = ac._player_armor_mesh_bases(mod, npc_worn_armos=worn)
    assert bases == {"armor/outfit/dress", "armor/outfit/boots"}, (
        "gore reached by no outfit stays out")
    # Without the set, the old rule: every non-playable armature skipped.
    assert ac._player_armor_mesh_bases(mod) == {"armor/outfit/boots"}


def test_worn_admitted_names_the_armatures_it_kept(tmp_path):
    mod = _outfit_mod(tmp_path)
    got: set = set()
    ac._player_armor_mesh_bases(mod, npc_worn_armos=_worn(mod / "Outfit.esp"),
                                worn_admitted=got)
    assert got == {("outfit.esp", DRESS_AA)}


@pytest.mark.parametrize("npc, why", [
    (_npc(WEARER, female=False, doft=OUTFIT), "a male wearer"),
    (_npc(WEARER, race=0x01000D00, doft=OUTFIT), "a race UBE does not cover"),
    (_npc(WEARER, wnam=DRESS), "the NPC's SKIN, not something it wears"),
], ids=["male", "other-race", "skin"])
def test_what_does_not_count_as_worn(tmp_path, npc, why):
    mod = _outfit_mod(tmp_path, npc=npc)
    worn = _worn(mod / "Outfit.esp")
    assert ("outfit.esp", DRESS & 0xFFFFFF) not in worn, why
    assert ac._player_armor_mesh_bases(mod, npc_worn_armos=worn) == {
        "armor/outfit/boots"}, why


@pytest.mark.parametrize("npc, why", [
    (_npc(WEARER, soft=OUTFIT), "the sleep outfit"),
    (_npc(WEARER, cnto=(LIST,)), "the inventory, through a leveled list"),
], ids=["sleep-outfit", "inventory"])
def test_what_counts_as_worn(tmp_path, npc, why):
    mod = _outfit_mod(tmp_path, npc=npc)
    assert ("outfit.esp", DRESS & 0xFFFFFF) in _worn(mod / "Outfit.esp"), why


# A traits template: the wearer's own record says male of another race; its
# sex and race come from the template chain.
TPL_A, TPL_B = 0x01000E00, 0x01000E01


def _templated(template=TPL_A):
    return _npc(WEARER, female=False, race=0x01000D00, template=template,
                use_traits=True, doft=OUTFIT)


@pytest.mark.parametrize("more, counts, why", [
    ([_npc(TPL_A)], True, "a female Nord template"),
    ([_npc(TPL_A, female=False, race=0x01000D00, template=TPL_B, use_traits=True),
      _npc(TPL_B)], True, "a chain of two, ending in a female Nord"),
    ([_npc(TPL_A, female=False)], False, "a male template"),
    ([], False, "a template that is no NPC here (a leveled NPC list): unknown sex"),
    ([_npc(TPL_A, template=TPL_B, use_traits=True),
      _npc(TPL_B, template=TPL_A, use_traits=True)], False, "a cycle"),
], ids=["female", "chain", "male", "leveled-list", "cycle"])
def test_a_templated_npc_counts_only_as_what_its_template_is(tmp_path, more,
                                                              counts, why):
    """The rule is armour a FEMALE NPC wears. A templated NPC whose sex cannot be
    known -- a leveled-NPC template -- is not a female wearer. Counting every
    templated NPC let 48 creature-cavity bodies, animal costumes and male
    bosses' gear through on a real load order."""
    mod = _outfit_mod(tmp_path, npc=_templated(), more_npcs=more)
    assert (("outfit.esp", DRESS & 0xFFFFFF) in _worn(mod / "Outfit.esp")) is counts, why


# Review 2026-09-24: not following WNAM was not enough. On a real load order the
# template rule reached 23 skins through templated NPCs' own outfits and
# inventories (skeleton, dragon and wraith skins among them), and a skeleton
# skin's DefaultRace armature was planned as armour. The templated wearer here
# resolves to a female, so the walk reaches the dress by that route.
TEMPLATED = _templated()


@pytest.mark.parametrize("skin_user", ["npc", "race"])
@pytest.mark.parametrize("wearer", [TEMPLATED, _npc(WEARER, cnto=(LIST,))],
                         ids=["template", "inventory"])
def test_a_skin_is_never_worn_however_it_is_reached(tmp_path, wearer, skin_user):
    """The dress is ALSO the skin (WNAM) of an NPC or a race in another plugin --
    a male creature NPC, so no wearer rule is involved. The walk still reaches
    the dress through the wearer's outfit or inventory; it must not count."""
    mod = _outfit_mod(tmp_path, npc=wearer,
                      more_npcs=[_npc(TPL_A)] if wearer is TEMPLATED else ())
    outfit = mod / "Outfit.esp"
    # Negative control: without the skin user the walk DOES reach it, so the
    # assertion below cannot pass because nothing was reached.
    assert ("outfit.esp", DRESS & 0xFFFFFF) in _worn(outfit)
    # In Skins.esp master 1 is Outfit.esp, so the dress keeps its formid.
    user = (_npc(0x02000800, female=False, race=0x02000801, wnam=DRESS)
            if skin_user == "npc" else _race(0x02000801, "SkeletonRace", wnam=DRESS))
    skins = _plugin(tmp_path / "Skins.esp", masters=("Skyrim.esm", "Outfit.esp"),
                    **{("NPC_" if skin_user == "npc" else "RACE"): [user]})
    worn = _worn(outfit, skins)
    assert ("outfit.esp", DRESS & 0xFFFFFF) not in worn, "a skin is never worn"
    assert ac._player_armor_mesh_bases(mod, npc_worn_armos=worn) == {
        "armor/outfit/boots"}


@pytest.mark.parametrize("edid, counts", [("UBE_NordRace", True),
                                           ("SkeeverRace", False)],
                         ids=["ube-race", "other-race"])
def test_a_race_a_ube_plugin_adds_counts_by_its_editor_id(tmp_path, edid, counts):
    """The wearer is in a third plugin whose master 1 defines the race and whose
    master 2 is the outfit mod; the same race under another editor ID is the
    negative control."""
    races = _plugin(tmp_path / "Races.esp", masters=(), RACE=[_race(0x00000D62, edid)])
    mod = _outfit_mod(tmp_path, npc=_npc(WEARER, female=False, doft=OUTFIT))
    wearer = _plugin(tmp_path / "Wearer.esp",
                     masters=("Skyrim.esm", "Races.esp", "Outfit.esp"),
                     NPC_=[_npc(0x03000800, race=0x01000D62, doft=0x02000A00)])
    worn = _worn(races, mod / "Outfit.esp", wearer)
    assert (("outfit.esp", DRESS & 0xFFFFFF) in worn) is counts


def test_only_the_winning_npc_record_counts(tmp_path):
    """A replacer that re-dresses the NPC takes the old outfit out of the game."""
    mod = _outfit_mod(tmp_path)
    redress = _plugin(tmp_path / "Redress.esp", masters=("Skyrim.esm", "Outfit.esp"),
                      NPC_=[_npc(0x01000C00)])        # the same NPC, no outfit
    assert ("outfit.esp", DRESS & 0xFFFFFF) not in _worn(mod / "Outfit.esp", redress)
    # ...and the order is the load order: the replacer loaded FIRST loses.
    assert ("outfit.esp", DRESS & 0xFFFFFF) in _worn(redress, mod / "Outfit.esp")


def test_an_unreadable_plugin_is_skipped(tmp_path):
    mod = _outfit_mod(tmp_path)
    junk = tmp_path / "Junk.esp"
    junk.write_bytes(b"not a plugin")
    assert ("outfit.esp", DRESS & 0xFFFFFF) in _worn(junk, mod / "Outfit.esp")


# --- the batch: built once, switched off by the flag, threaded into selection ---

@pytest.fixture
def load_order(tmp_path, monkeypatch):
    mod = _outfit_mod(tmp_path)
    lay = paths.Layout(mods_root=tmp_path / "mods", instance_dir=tmp_path)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["Outfit.esp"])
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda l: ["Outfit Mod"])
    monkeypatch.setattr(paths, "plugin_file_index",
                        lambda l: {"outfit.esp": mod / "Outfit.esp"})
    monkeypatch.delenv(OFF, raising=False)
    ac._NPC_WORN_CACHE.clear()
    ac._ARMOR_MOD_DIRS_CACHE.clear()
    yield mod
    ac._NPC_WORN_CACHE.clear()
    ac._ARMOR_MOD_DIRS_CACHE.clear()


def test_the_batch_set_is_built_once(load_order, monkeypatch):
    calls = []
    real = ac._npc_worn_armos
    monkeypatch.setattr(ac, "_npc_worn_armos", lambda p: calls.append(1) or real(p))
    first = ac._batch_npc_worn_armos()
    assert ac._batch_npc_worn_armos() is first
    assert len(calls) == 1 and ("outfit.esp", DRESS & 0xFFFFFF) in first


def test_the_off_switch_reads_nothing(load_order, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    monkeypatch.setattr(ac, "_npc_worn_armos",
                        lambda p: pytest.fail("switched off, no plugin may be read"))
    assert ac._batch_npc_worn_armos() is None


def test_coverage_reads_the_set_with_the_conversion_switch_set(load_order, monkeypatch):
    """The coverage race-list rule has its own switch: this one turns off only
    the conversion. #coverage-human-race-list"""
    monkeypatch.setenv(OFF, "1")
    assert ac._batch_npc_worn_armos() is None
    worn = ac._batch_npc_worn_armos(for_coverage=True)
    assert ("outfit.esp", DRESS & 0xFFFFFF) in worn


def test_coverage_shares_the_conversion_cache(load_order, monkeypatch):
    """Both ask once per load order: coverage must not re-read every plugin."""
    calls = []
    real = ac._npc_worn_armos
    monkeypatch.setattr(ac, "_npc_worn_armos", lambda p: calls.append(1) or real(p))
    first = ac._batch_npc_worn_armos()
    assert ac._batch_npc_worn_armos(for_coverage=True) is first
    assert len(calls) == 1


def test_selection_admits_the_outfit_mod(load_order, monkeypatch, capsys):
    mods = load_order.parent
    kw = dict(require_arma=True, enabled_ordered=["Outfit Mod"])
    sel = ac._find_armor_mod_dirs(mods, **kw)
    assert [(c["name"], c["armor_nifs"]) for c in sel] == [("Outfit Mod", 2)]
    assert "1 armature(s) in 1 mod(s) kept" in capsys.readouterr().out
    monkeypatch.setenv(OFF, "1")                       # the memo key carries it
    assert [(c["name"], c["armor_nifs"]) for c in
            ac._find_armor_mod_dirs(mods, **kw)] == [("Outfit Mod", 1)]


def test_a_mod_whose_only_armour_an_npc_wears_is_a_source(tmp_path, monkeypatch):
    """The eligibility test itself takes the set: a follower mod whose every
    piece is its non-playable outfit becomes a source."""
    mod = _outfit_mod(tmp_path, boots=False)
    worn = _worn(mod / "Outfit.esp")
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda: worn)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    sel = ac._find_armor_mod_dirs_uncached(mod.parent, require_arma=True,
                                           enabled_ordered=["Outfit Mod"])
    assert [(c["name"], c["armor_nifs"]) for c in sel] == [("Outfit Mod", 1)]
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda: None)
    assert ac._find_armor_mod_dirs_uncached(mod.parent, require_arma=True,
                                            enabled_ordered=["Outfit Mod"]) == []


def test_a_loose_replacer_of_worn_vanilla_armour_is_located(tmp_path, monkeypatch):
    """The vanilla sweep's mesh keys take the set too, so a loose replacer of a
    worn non-playable vanilla piece is located and converted -- not the
    archive original the game no longer draws."""
    data = tmp_path / "Game" / "Data"
    master = _plugin(data / "Skyrim.esm", masters=(),
                     ARMA=[_arma(0x000800, "clothes\\vanilla\\robe_1.nif")],
                     ARMO=[_armo(0x000900, 0x000800)], OTFT=[_otft(0x000A00, 0x000900)],
                     NPC_=[_npc(0x000C00, race=NORD & 0xFFFFFF, doft=0x000A00)])
    rep = tmp_path / "mods" / "Robe Replacer" / "meshes" / "clothes" / "vanilla"
    rep.mkdir(parents=True)
    (rep / "robe_1.nif").write_bytes(b"x")
    lay = paths.Layout(mods_root=tmp_path / "mods", game_data_dirs=[data])
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.delenv("CBBE2UBE_NO_VANILLA_SWEEP", raising=False)
    mods = tmp_path / "mods"
    for worn, located in ((_worn(master), True), (None, False)):
        monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda w=worn: w)
        ac._BATCH_MESH_INDEX.clear()
        ac._find_armor_mod_dirs_uncached(mods, require_arma=True,
                                         enabled_ordered=["Robe Replacer"])
        idx = ac._BATCH_MESH_INDEX.get(str(mods).lower(), {})
        assert ("clothes/vanilla/robe_1.nif" in idx) is located
    ac._BATCH_MESH_INDEX.clear()


def test_the_batch_threads_the_set_into_every_source():
    """WIRING GUARD, as for #skip-already-ube: every test above calls the planner
    directly, so dropping the kwarg at the batch's convert call would leave them
    green while no source ever received the set."""
    import inspect
    src = inspect.getsource(ac._cmd_convert)
    assert "else _batch_npc_worn_armos())" in src     # built once, per batch
    assert "npc_worn_armos=batch_npc_worn," in src     # ...and handed to each source


def test_a_direct_convert_asks_the_vfs_for_the_worn_piece(load_order, tmp_path,
                                                          monkeypatch):
    """WIRING, behavioural: `convert` run on its own -- no source selection in
    this process -- builds its own VFS mesh index from the keys the planner names
    for each source. If that planning call does not get the set, a worn piece's
    key is never asked for, so a loose replacement in another mod is never
    located and the source's own copy is converted instead of the one the game
    loads. Stops at the index build and reads the keys it was asked for; the
    playable boots are the control that the build ran at all."""
    import argparse
    from src import discovery

    class _Asked(BaseException):      # `_cmd_convert` catches every Exception here
        pass

    def _index(mods_root, enabled, target_keys=None, skip_mods=None, **kw):
        raise _Asked(set(target_keys or ()))
    monkeypatch.setattr(discovery, "build_mesh_index", _index)
    monkeypatch.setenv(paths.MODS_ROOT_ENV, str(load_order.parent))
    monkeypatch.delenv(paths.GAME_DATA_ENV, raising=False)
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(tmp_path / "run.log"))
    ns = argparse.Namespace(
        sources=[load_order], output=tmp_path / "out", esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)
    worn = ac._batch_npc_worn_armos()
    for batch_set, located in ((worn, True), (None, False)):
        monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda w=batch_set: w)
        ac._BATCH_MESH_INDEX.clear()          # no selection ran in this process
        with pytest.raises(_Asked) as got:
            ac._cmd_convert(ns)
        keys = got.value.args[0]
        assert "armor/outfit/boots_1.nif" in keys
        assert ("armor/outfit/dress_1.nif" in keys) is located


def test_the_convert_step_plans_the_worn_piece(load_order, tmp_path, monkeypatch):
    """WIRING: auto_convert_mod hands the set to the planner. Stops at the
    resolver, which receives the planned bases."""
    class _Planned(Exception):
        pass

    def _stop(bases, *a, **k):
        raise _Planned(sorted(bases))
    monkeypatch.setattr(ac, "_resolve_armor_meshes", _stop)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    for worn, want in ((ac._batch_npc_worn_armos(),
                        ["armor/outfit/boots", "armor/outfit/dress"]),
                       (None, ["armor/outfit/boots"])):
        with pytest.raises(_Planned) as got:
            ac.auto_convert_mod(load_order, tmp_path / "out", ube_body_ref_path=ref,
                                master_data_dirs=[], npc_worn_armos=worn)
        assert got.value.args[0] == want
