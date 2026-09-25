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

r"""#coverage-third-party-drawn + #coverage-keep-better-first-person -- another
mod's UBE armature on the WINNING armour record is judged by what it draws.

THE DEFECT. Both coverage passes skipped an armour whenever any of its
armatures named a UBE race -- except the body pass, which minted over every
slot-32 armour anyway. So a body armour whose hand-made UBE patch draws the
very file we convert was drawn twice, and an armour whose UBE patch names a
mesh that exists nowhere was left with nothing (the plugin half of the
exclusion scan read our own un-loaded copies as that patch and hid it too).

Now an armature T counts as drawing when it names UBE races, its female world
mesh is live in the game view, and -- for an armour with slot 32/34/38 -- that
mesh is under `!UBE\`. Our armature S is drawn by a T of the same file, else by
the one unused T whose slots equal S's when nothing else ties for it (a T that
draws a guard-dropped armature's file is used up by it); T's races are
subtracted, so S is minted for the UBE races no T draws. The first-person guard keeps S when our converted
first-person mesh would otherwise give way to a CBBE one or none.
`CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN=1` restores the old rules (and the
recursive plugin index, tests/test_plugin_file_index_priority.py);
`CBBE2UBE_NO_COVERAGE_KEEP_BETTER_FIRST_PERSON=1` drops the guard.
"""
import struct
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import paths
from src import ube_patcher as up
from src.esp import (ESP, TES4Header, Group, Record, encode_subrecord,
                     encode_zstring, iter_subrecords)

OFF = "CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN"
KBF_OFF = "CBBE2UBE_NO_COVERAGE_KEEP_BETTER_FIRST_PERSON"
DEFAULT = 0x00000019                  # Skyrim.esm DefaultRace
NORD = 0x00013746                     # Skyrim.esm NordRace
HEAD, BODY, HANDS, AMULET = 1 << 0, 1 << 2, 1 << 3, 1 << 5
UBE = list(up.UBE_RACE_FIDS_24)
ARMOUR = ("mod.esp", 0x801)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv(KBF_OFF, raising=False)


def _save(path, masters, groups):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=masters, num_records=0, next_object_id=0x900,
                          version=1.7, flags=0), groups=groups).save(path)
    return Path(path)


def _arma(formid, slots, primary, extra=(), models=None):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", primary))
    for sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
        if models and sig in models:
            p += encode_subrecord(sig, encode_zstring(models[sig]))
    for r in extra:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _armo(formid, slots, armatures):
    q = encode_subrecord(b"EDID", encode_zstring("Piece"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armatures:
        q += encode_subrecord(b"MODL", struct.pack("<I", a))
    return Record(sig=b"ARMO", flags=0, formid=formid, payload=q)


def _ours(low, slots, mod3, mod5=None):
    return dict(low=low, slots=slots, mod3=mod3, mod5=mod5)


def _theirs(low, slots, mod3, mod5=None, races=UBE):
    return dict(low=low, slots=slots, mod3=mod3, mod5=mod5, races=races)


def _world(tmp_path, slots, ours, theirs, *, later_drops=False):
    """Skyrim, UBE_AllRace, Mod.esp (our source armatures + the armour) and a
    third-party Patch.esp whose override of the armour adds its UBE armatures.
    `later_drops`: a later plugin's override lists our armatures only."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    src = [_arma(0x01000000 | o["low"], o["slots"], DEFAULT,
                 models={b"MOD3": o["mod3"],
                         **({b"MOD5": o["mod5"]} if o["mod5"] else {})})
           for o in ours]
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=src),
                 Group(label=b"ARMO", records=[_armo(0x01000801, slots,
                                                     [a.formid for a in src])])])
    tp = []
    for t in theirs:
        races = [(1 << 24) | r for r in t["races"]] or [NORD]
        tp.append(_arma(0x03000000 | t["low"], t["slots"], races[0], races[1:],
                        models={b"MOD3": t["mod3"],
                                **({b"MOD5": t["mod5"]} if t["mod5"] else {})}))
    arms = [0x02000000 | o["low"] for o in ours] + [a.formid for a in tp]
    patch = _save(tmp_path / "Patch.esp", ["Skyrim.esm", "UBE_AllRace.esp", "Mod.esp"],
                  [Group(label=b"ARMA", records=tp),
                   Group(label=b"ARMO", records=[_armo(0x02000801, slots, arms)])])
    order = [sky, ube, mod, patch]
    if later_drops:
        order.append(_save(tmp_path / "Later.esp", ["Skyrim.esm", "Mod.esp"],
                           [Group(label=b"ARMO", records=[_armo(
                               0x01000801, slots,
                               [0x01000000 | o["low"] for o in ours])])]))
    return order


def _run(tmp_path, world, *, body=False, conv=(), live=lambda p: True, **extra):
    name = "UBE_ModBody_Coverage UBE patch.esp" if body else \
        "UBE_ModNonBody_Coverage UBE patch.esp"
    out = tmp_path / name
    kw = dict(converted_rel_paths=set(conv), exclude_names={out.name.lower()},
              master_data_dirs=[tmp_path], cover_all=True, preserve_textures=True,
              mesh_live=live, **extra)
    if body:
        st = up.generate_modded_body_ube_coverage_patch(
            out, world, cover_hands_feet=True, **kw)
    else:
        st = up.generate_modded_nonbody_ube_coverage_patch(out, world, **kw)
    return st, _minted(out)


def _minted(out):
    """Every minted armature: its female meshes and the UBE races it targets
    (UBE_AllRace low ids, primary first)."""
    if not out.is_file():
        return []
    e = ESP.load(out)
    ube = [m.lower() for m in e.header.masters].index("ube_allrace.esp")
    got = []
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            m = {"races": []}
            for s, d in iter_subrecords(r.payload):
                if s in (b"MOD3", b"MOD5"):
                    m[s.decode().lower()] = d.rstrip(b"\x00").decode()
                elif s in (b"RNAM", b"MODL") and len(d) == 4:
                    f = struct.unpack("<I", d)[0]
                    if f >> 24 == ube and (f & 0xFFFFFF) not in m["races"]:
                        m["races"].append(f & 0xFFFFFF)
            got.append(m)
    return sorted(got, key=lambda m: m.get("mod3", ""))


DRESS = r"Armor\Dress\Dress_1.nif"
DRESS_CONV = {"armor/dress/dress_1.nif"}
HELMET = r"Armor\Helmet\Helmet_1.nif"


# ------------------------------------------------------------ what draws

def test_the_same_file_drawn_by_their_ube_armature_is_not_minted_again(tmp_path):
    """TPD-a: a body armour whose hand-made UBE patch draws the file we
    converted was drawn twice (the slot-32 exemption)."""
    world = _world(tmp_path, BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, "!UBE\\" + DRESS)])
    st, minted = _run(tmp_path, world, body=True, conv=DRESS_CONV)
    assert minted == []
    assert st["armo_targets"] == 0
    assert st["third_party_drawn"] == [(ARMOUR, "Piece")]


def test_an_excluded_armour_theirs_draws_is_not_called_withheld(tmp_path):
    """--exclude-mods: an armour another mod's armature draws whole is not
    'left without an armature from any mod'; one it does not draw still is."""
    world = _world(tmp_path, BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, "!UBE\\" + DRESS)])
    st, minted = _run(tmp_path, world, body=True, conv=DRESS_CONV,
                      withheld_armo_abs={ARMOUR})
    assert minted == [] and st["withheld"] == []
    assert st["third_party_drawn"] == [(ARMOUR, "Piece")]
    world = _world(tmp_path / "b", BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, DRESS)])       # draws the CBBE body
    st, minted = _run(tmp_path / "b", world, body=True, conv=DRESS_CONV,
                      withheld_armo_abs={ARMOUR})
    assert minted == [] and st["withheld"] == [(ARMOUR, "Piece")]
    assert st["third_party_drawn"] == [] and st["third_party_partial"] == []


def test_a_weight_variant_and_the_meshes_folder_are_the_same_file(tmp_path):
    """On another slot, so only the file can match."""
    world = _world(tmp_path, 1 << 19, [_ours(0x8A0, 1 << 19, r"Armor\Belt_1.nif")],
                   [_theirs(0x900, 1 << 23, r"meshes\!UBE\Armor\Belt_0.nif")])
    st, minted = _run(tmp_path, world)
    assert minted == [] and st["third_party_drawn"] == [(ARMOUR, "Piece")]


def test_a_third_party_mesh_that_exists_nowhere_still_mints_ours(tmp_path):
    """TPD-b: their armature draws nothing, so the old skip left the armour
    invisible on UBE actors."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path, world, live=lambda p: False)
    assert [m["mod3"] for m in minted] == [HELMET]
    assert minted[0]["races"] == UBE
    assert st["third_party_drawn"] == []


def test_a_mesh_this_run_converted_is_live(tmp_path):
    """TPD-h: the game view after this run includes our own output."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path, world, conv={"armor/helmet/helmet_1.nif"},
                      live=lambda p: False)
    assert minted == [] and st["third_party_drawn"] == [(ARMOUR, "Piece")]


def test_a_lookup_that_cannot_tell_mints_ours(tmp_path):
    """No game view (a caller that passes none): skipping ours is the dangerous
    direction -- an armour left with nothing -- so theirs is not taken to draw,
    unless it draws a mesh this run converted."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path, world, live=None)
    assert [m["mod3"] for m in minted] == [HELMET]
    world = _world(tmp_path / "b", HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path / "b", world, live=None,
                      conv={"armor/helmet/helmet_1.nif"})
    assert minted == []


def test_a_re_slotted_copy_of_the_same_file_draws_ours(tmp_path):
    """TPD-c: a UBE patch that moved the piece to another slot still draws the
    same file -- slots are not asked when the file matches."""
    world = _world(tmp_path, 1 << 19, [_ours(0x8A0, 1 << 19, r"Armor\Belt_1.nif")],
                   [_theirs(0x900, 1 << 23, r"!UBE\Armor\Belt_1.nif")])
    st, minted = _run(tmp_path, world)
    assert minted == []


def test_a_body_armours_ube_armature_on_the_original_path_does_not_draw(tmp_path):
    r"""TPD-d: on a body armour their armature must draw a `!UBE\` mesh; the
    original path is the CBBE body."""
    world = _world(tmp_path, BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, DRESS)])
    st, minted = _run(tmp_path, world, body=True, conv=DRESS_CONV)
    assert [m["mod3"] for m in minted] == ["!UBE\\" + DRESS]
    assert st["third_party_drawn"] == []


def test_hands_on_the_original_path_draw(tmp_path):
    """TPD-j: gloves need no UBE body fit, so their original path counts."""
    gloves = r"Armor\Gloves_1.nif"
    world = _world(tmp_path, HANDS, [_ours(0x8A0, HANDS, gloves)],
                   [_theirs(0x900, HANDS, gloves)])
    st, minted = _run(tmp_path, world, body=True)
    assert minted == [] and st["third_party_drawn"] == [(ARMOUR, "Piece")]


def test_dead_gloves_of_theirs_still_mint_ours(tmp_path):
    """The body pass skipped a hands/feet armour with any UBE armature too."""
    gloves = r"Armor\Gloves_1.nif"
    world = _world(tmp_path, HANDS, [_ours(0x8A0, HANDS, gloves)],
                   [_theirs(0x900, HANDS, "!UBE\\" + gloves)])
    st, minted = _run(tmp_path, world, body=True, live=lambda p: False)
    assert [m["mod3"] for m in minted] == [gloves]


def test_the_path_rule_is_judged_per_armour(tmp_path):
    """Condition (c) reads the ARMOUR's slots: gloves of a cuirass-and-gloves
    armour on the original path do not draw ours."""
    gloves = r"Armor\Gloves_1.nif"
    world = _world(tmp_path, BODY | HANDS,
                   [_ours(0x8A0, BODY, DRESS), _ours(0x8A1, HANDS, gloves)],
                   [_theirs(0x900, BODY, "!UBE\\" + DRESS),
                    _theirs(0x901, HANDS, gloves)])
    st, minted = _run(tmp_path, world, body=True,
                      conv=DRESS_CONV | {"armor/gloves_1.nif"})
    assert [m["mod3"] for m in minted] == ["!UBE\\" + gloves]
    assert st["third_party_partial"] == [(ARMOUR, "Piece")]


def test_their_races_are_left_out_of_ours(tmp_path):
    """TPD-e: a UBE armature for 15 of the 16 UBE races draws them; ours is
    minted for the one left, which becomes its primary."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET, races=UBE[:-1])])
    st, minted = _run(tmp_path, world)
    assert [m["races"] for m in minted] == [[UBE[-1]]]
    assert st["third_party_partial"] == [(ARMOUR, "Piece")]


def test_their_races_come_off_a_body_armature_too(tmp_path):
    world = _world(tmp_path, BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, "!UBE\\" + DRESS, races=UBE[2:])])
    st, minted = _run(tmp_path, world, body=True, conv=DRESS_CONV)
    assert [m["races"] for m in minted] == [UBE[:2]]


def test_an_armature_shared_with_an_armour_they_do_not_patch_keeps_every_race(tmp_path):
    """One minted record serves every armour that lists it: the union."""
    sky = _save(tmp_path / "Skyrim.esm", [], [])
    ube = _save(tmp_path / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    src = _arma(0x010008A0, HEAD, DEFAULT, models={b"MOD3": HELMET})
    mod = _save(tmp_path / "Mod.esp", ["Skyrim.esm"],
                [Group(label=b"ARMA", records=[src]),
                 Group(label=b"ARMO", records=[_armo(0x01000801, HEAD, [0x010008A0]),
                                               _armo(0x01000802, HEAD, [0x010008A0])])])
    races = [(1 << 24) | r for r in UBE[:-1]]
    tp = _arma(0x03000900, HEAD, races[0], races[1:],
               models={b"MOD3": "!UBE\\" + HELMET})
    patch = _save(tmp_path / "Patch.esp", ["Skyrim.esm", "UBE_AllRace.esp", "Mod.esp"],
                  [Group(label=b"ARMA", records=[tp]),
                   Group(label=b"ARMO", records=[_armo(0x02000801, HEAD,
                                                       [0x020008A0, 0x03000900])])])
    st, minted = _run(tmp_path, [sky, ube, mod, patch])
    assert [m["races"] for m in minted] == [UBE]
    assert st["armo_targets"] == 2


def test_only_the_piece_they_do_not_draw_is_minted(tmp_path):
    """TPD-f: their helmet is drawn by theirs; our amulet, on a slot their
    armature does not touch, is minted."""
    world = _world(tmp_path, HEAD | AMULET,
                   [_ours(0x8A0, HEAD, HELMET), _ours(0x8A1, AMULET, r"Armor\Amulet_1.nif")],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [r"Armor\Amulet_1.nif"]
    assert st["third_party_partial"] == [(ARMOUR, "Piece")]


def test_a_matched_qualifier_is_not_reused_by_slot_overlap(tmp_path):
    """TPD-g: their helmet draws our helmet (same file); our hood, another file
    on the same slot, is not drawn by it as well."""
    world = _world(tmp_path, HEAD,
                   [_ours(0x8A0, HEAD, HELMET), _ours(0x8A1, HEAD, r"Armor\Hood_1.nif")],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [r"Armor\Hood_1.nif"]


def test_a_qualifier_two_armatures_tie_for_draws_neither(tmp_path):
    """#r9-fallback-safe: a UBE armature on another file with the slots of two
    of ours cannot say which one it replaces, so both are minted (the
    prototype handed it to the first one listed)."""
    world = _world(tmp_path, HEAD,
                   [_ours(0x8A0, HEAD, r"Armor\A_1.nif"), _ours(0x8A1, HEAD, r"Armor\B_1.nif")],
                   [_theirs(0x900, HEAD, r"!UBE\Armor\Other_1.nif")])
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [r"Armor\A_1.nif", r"Armor\B_1.nif"]
    assert st["third_party_drawn"] == [] and st["third_party_partial"] == []


# ------------------------------------------------ #r9-fallback-safe

GLOVES = r"Armor\Dress\Gloves_1.nif"
GLOVES_CONV = {"armor/dress/gloves_1.nif"}


def test_their_cuirass_on_the_gloves_slot_too_does_not_draw_our_gloves(tmp_path):
    """The review's case: their UBE cuirass (another file) lists slots 32 and
    33. It overlaps our cuirass and our gloves but equals neither, so both are
    minted -- the prototype handed it to whichever was listed first, and the
    gloves had no armature on UBE actors when that was the gloves."""
    for order in (0, 1):
        ours = [_ours(0x8A0, BODY, DRESS), _ours(0x8A1, HANDS, GLOVES)]
        world = _world(tmp_path / str(order), BODY | HANDS, ours[::1 - 2 * order],
                       [_theirs(0x900, BODY | HANDS, r"!UBE\Armor\Theirs\Top_1.nif")])
        st, minted = _run(tmp_path / str(order), world, body=True,
                          conv=DRESS_CONV | GLOVES_CONV)
        assert [m["mod3"] for m in minted] == ["!UBE\\" + DRESS, "!UBE\\" + GLOVES]
        assert st["third_party_drawn"] == []


def test_slots_that_only_overlap_or_contain_do_not_draw(tmp_path):
    """Equality, not overlap or containment: a lone pair of gloves beside their
    armature on slots 32+33 (a superset), and a cuirass-and-gloves armature of
    ours beside their gloves (a subset), are both minted."""
    world = _world(tmp_path / "sup", HANDS, [_ours(0x8A0, HANDS, GLOVES)],
                   [_theirs(0x900, BODY | HANDS, r"!UBE\Armor\Theirs\Top_1.nif")])
    st, minted = _run(tmp_path / "sup", world, body=True, conv=GLOVES_CONV)
    assert [m["mod3"] for m in minted] == ["!UBE\\" + GLOVES]
    world = _world(tmp_path / "sub", BODY | HANDS, [_ours(0x8A0, BODY | HANDS, DRESS)],
                   [_theirs(0x900, HANDS, r"!UBE\Armor\Theirs\Gloves_1.nif")])
    st, minted = _run(tmp_path / "sub", world, body=True, conv=DRESS_CONV)
    assert [m["mod3"] for m in minted] == ["!UBE\\" + DRESS]


def test_the_exact_slots_still_draw(tmp_path):
    """One of ours, one unused qualifier of exactly its slots on another file:
    drawn, as before."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, r"!UBE\Armor\Other_1.nif")])
    st, minted = _run(tmp_path, world)
    assert minted == [] and st["third_party_drawn"] == [(ARMOUR, "Piece")]


def test_two_qualifiers_for_one_armature_draw_nothing(tmp_path):
    """Two unused UBE armatures of ours's exact slots on other files: which one
    is its version is no answer either, so ours is minted."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, r"!UBE\Armor\Other_1.nif"),
                    _theirs(0x901, HEAD, r"!UBE\Armor\Other2_1.nif")])
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [HELMET]
    assert minted[0]["races"] == UBE


def test_the_list_order_does_not_decide(tmp_path):
    """The same armour with its armatures listed the other way round mints the
    same pieces (a tie, a lone match and a piece with no match)."""
    got = []
    for order in (0, 1):
        ours = [_ours(0x8A0, HEAD, r"Armor\A_1.nif"), _ours(0x8A1, HEAD, r"Armor\B_1.nif"),
                _ours(0x8A2, AMULET, r"Armor\Amulet_1.nif"),
                _ours(0x8A3, 1 << 19, r"Armor\Belt_1.nif")]
        world = _world(tmp_path / str(order), HEAD | AMULET | (1 << 19),
                       ours[::1 - 2 * order],
                       [_theirs(0x900, HEAD, r"!UBE\Armor\Other_1.nif"),
                        _theirs(0x901, AMULET, r"!UBE\Armor\Other_2.nif")])
        st, minted = _run(tmp_path / str(order), world)
        got.append([m["mod3"] for m in minted])
    assert got[0] == got[1] == [r"Armor\A_1.nif", r"Armor\B_1.nif",
                                r"Armor\Belt_1.nif"]


# A cuirass whose female world mesh was not converted (only its first person
# was): #coverage-world-mesh drops it before the split.
DROPPED = [_ours(0x8A0, BODY, DRESS, r"Armor\Dress\Dress1st_1.nif")]
DROPPED_CONV = {"armor/dress/dress1st_1.nif"}


def test_the_twin_of_a_guard_dropped_cuirass_does_not_draw_our_gloves(tmp_path):
    """Their UBE version of the dropped cuirass (its file) is used up by it,
    whatever slots it lists: our gloves stay minted -- the prototype left the
    armour with no armature and called it drawn by theirs."""
    for tag, slots in (("both", BODY | HANDS), ("hands", HANDS)):
        world = _world(tmp_path / tag, BODY | HANDS,
                       DROPPED + [_ours(0x8A1, HANDS, GLOVES)],
                       [_theirs(0x900, slots, "!UBE\\" + DRESS)])
        st, minted = _run(tmp_path / tag, world, body=True,
                          conv=DROPPED_CONV | GLOVES_CONV)
        assert [m["mod3"] for m in minted] == ["!UBE\\" + GLOVES], tag
        assert minted[0]["races"] == UBE
        assert st["third_party_drawn"] == []


def test_a_guard_dropped_armature_ties_for_its_slots(tmp_path):
    """Their UBE armature on another file with the slots of the dropped
    cuirass AND of our other cuirass may be the dropped one's: ours is minted."""
    world = _world(tmp_path, BODY,
                   DROPPED + [_ours(0x8A1, BODY, r"Armor\Dress\Under_1.nif")],
                   [_theirs(0x900, BODY, r"!UBE\Armor\Theirs\Top_1.nif")])
    st, minted = _run(tmp_path, world, body=True,
                      conv=DROPPED_CONV | {"armor/dress/under_1.nif"})
    assert [m["mod3"] for m in minted] == [r"!UBE\Armor\Dress\Under_1.nif"]
    assert st["third_party_drawn"] == []


def test_a_winning_override_that_drops_their_armature_mints_ours(tmp_path):
    """TPD-l: only the WINNING armour record's armatures draw."""
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)], later_drops=True)
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [HELMET]


def test_an_armature_with_no_ube_race_is_no_qualifier(tmp_path):
    world = _world(tmp_path, HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET, races=[])])
    st, minted = _run(tmp_path, world)
    assert [m["mod3"] for m in minted] == [HELMET]


def test_switched_off_the_old_rules_are_back(tmp_path, monkeypatch):
    """TPD-m: the non-body pass skips any armour with a UBE armature (a dead
    one included); the body pass mints over a slot-32 one."""
    monkeypatch.setenv(OFF, "1")
    assert up._coverage_third_party_drawn() is False
    world = _world(tmp_path / "nb", HEAD, [_ours(0x8A0, HEAD, HELMET)],
                   [_theirs(0x900, HEAD, "!UBE\\" + HELMET)])
    st, minted = _run(tmp_path / "nb", world, live=lambda p: False)
    assert minted == [] and st["third_party_drawn"] == []
    world = _world(tmp_path / "bd", BODY, [_ours(0x8A0, BODY, DRESS)],
                   [_theirs(0x900, BODY, "!UBE\\" + DRESS)])
    st, minted = _run(tmp_path / "bd", world, body=True, conv=DRESS_CONV)
    assert [m["mod3"] for m in minted] == ["!UBE\\" + DRESS]
    assert minted[0]["races"] == UBE


# ---------------------------------------------- #coverage-keep-better-first-person

FP_CONV = DRESS_CONV | {"armor/dress/dress1st_1.nif"}
OUR_FP = r"Armor\Dress\Dress1st_1.nif"


def _fp_world(tmp_path, their_mod5):
    return _world(tmp_path, BODY, [_ours(0x8A0, BODY, DRESS, OUR_FP)],
                  [_theirs(0x900, BODY, "!UBE\\" + DRESS, their_mod5)])


def test_ours_is_kept_when_theirs_has_a_cbbe_first_person(tmp_path):
    """KBF-a: skipping ours would leave the player's arms on the CBBE mesh."""
    st, minted = _run(tmp_path, _fp_world(tmp_path, OUR_FP), body=True, conv=FP_CONV)
    assert [m["mod5"] for m in minted] == ["!UBE\\" + OUR_FP]
    assert st["third_party_kept_first_person"] == [(ARMOUR, "Piece", "mod.esp|8A0")]
    assert st["third_party_drawn"] == []


def test_ours_is_skipped_when_theirs_has_a_ube_first_person(tmp_path):
    """KBF-b"""
    st, minted = _run(tmp_path, _fp_world(tmp_path, "!UBE\\" + OUR_FP), body=True,
                      conv=FP_CONV)
    assert minted == [] and st["third_party_kept_first_person"] == []


def test_ours_is_kept_when_theirs_has_no_first_person(tmp_path):
    """KBF-c: an empty first person is the worse one (challenge of the census)."""
    st, minted = _run(tmp_path, _fp_world(tmp_path, None), body=True, conv=FP_CONV)
    assert len(minted) == 1
    assert st["third_party_kept_first_person"] == [(ARMOUR, "Piece", "mod.esp|8A0")]


def test_an_unconverted_first_person_of_ours_keeps_nothing(tmp_path):
    st, minted = _run(tmp_path, _fp_world(tmp_path, OUR_FP), body=True, conv=DRESS_CONV)
    assert minted == [] and st["third_party_kept_first_person"] == []


def test_switched_off_the_guard_skips_ours(tmp_path, monkeypatch):
    """KBF-d: the plain rule."""
    monkeypatch.setenv(KBF_OFF, "1")
    assert up._coverage_keep_better_first_person() is False
    st, minted = _run(tmp_path, _fp_world(tmp_path, OUR_FP), body=True, conv=FP_CONV)
    assert minted == [] and st["third_party_drawn"] == [(ARMOUR, "Piece")]


# ------------------------------------------------------------ game view

class _FakeArchive:
    LISTS: dict = {}

    def __init__(self, path, eager=True):
        self.name = Path(path).name.lower()

    def list_files(self, prefix=""):
        return [n for n in self.LISTS.get(self.name, []) if n.startswith(prefix)]


@pytest.fixture
def game(tmp_path, monkeypatch):
    """An MO2 instance: an enabled mod with an active plugin's archive, an
    enabled mod whose archive no active plugin loads, a disabled mod, the
    overwrite, the Data folder with the vanilla archive the INI names, and our
    output folder (not in the modlist)."""
    inst = tmp_path / "inst"
    mods = inst / "mods"
    prof = inst / "profiles" / "P"
    prof.mkdir(parents=True)
    (prof / "modlist.txt").write_text("+Loaded\n+Unloaded\n-Disabled\n",
                                      encoding="utf-8")
    (prof / "plugins.txt").write_text("*Loaded.esp\nUnloaded.esp\n", encoding="utf-8")
    (prof / "Skyrim.ini").write_text(
        "[Archive]\nsResourceArchiveList=Vanilla - Meshes.bsa\n", encoding="utf-8")
    data = tmp_path / "Game" / "Data"
    ow = inst / "overwrite"
    out = tmp_path / "Our Output"
    for d in (mods / "Loaded", mods / "Unloaded", mods / "Disabled", data, ow, out):
        d.mkdir(parents=True)
    for f in (mods / "Loaded" / "Loaded.bsa", mods / "Unloaded" / "Unloaded.bsa",
              data / "Vanilla - Meshes.bsa"):
        f.write_bytes(b"")

    def _loose(root, rel):
        p = root / "meshes" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    _loose(mods / "Loaded", "a/loose.nif")
    _loose(mods / "Disabled", "a/disabled.nif")
    _loose(ow, "a/overwrite.nif")
    _loose(data, "a/data.nif")
    _loose(out, "!UBE/a/ours_1.nif")
    _FakeArchive.LISTS = {
        "loaded.bsa": ["meshes/a/in_loaded.nif"],
        "unloaded.bsa": ["meshes/a/in_unloaded.nif"],
        "vanilla - meshes.bsa": ["meshes/a/in_vanilla.nif"]}
    monkeypatch.setattr("src.bsa_strings.BSAArchive", _FakeArchive)
    lay = paths.Layout(mods_root=mods, instance_dir=inst, selected_profile="P",
                       game_data_dirs=[data], overwrite_dir=ow)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "mods_root", lambda: mods)
    return ac._game_view_mesh_resolver(out)


@pytest.mark.parametrize("model, live", [
    (r"a\loose.nif", True),
    (r"meshes\a\loose.nif", True),
    (r"a\overwrite.nif", True),
    (r"a\data.nif", True),
    (r"!UBE\a\ours_1.nif", True),          # our output: the game loads it too
    (r"a\in_loaded.nif", True),            # an active plugin's archive
    (r"a\in_vanilla.nif", True),           # an archive the INI names
    (r"a\in_unloaded.nif", False),         # no active plugin loads it
    (r"a\disabled.nif", False),            # a disabled mod
    (r"a\nowhere.nif", False),
    ("", False),
], ids=["loose", "meshes-prefix", "overwrite", "data", "our-output",
        "active-archive", "ini-archive", "unloaded-archive", "disabled-mod",
        "nowhere", "empty"])
def test_the_game_view(game, model, live):
    """TPD-i"""
    assert game(model) is live


def test_no_modlist_no_game_view(monkeypatch):
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    monkeypatch.setattr(paths, "mods_root", lambda: None)
    assert ac._game_view_mesh_resolver(None) is None


# ------------------------------------------------------------ report

def test_drawn_armours_are_reported(capsys):
    ac._report_coverage_holds([
        {"third_party_drawn": [(ARMOUR, "Piece")],
         "third_party_partial": [(("mod.esp", 0x802), "Other")],
         "third_party_kept_first_person": [(ARMOUR, "Piece", "mod.esp|8A0")]}])
    out = capsys.readouterr().out
    assert ("1 armour(s) already drawn on UBE by another mod's armature -- ours "
            "not minted") in out
    assert "Piece  (mod.esp|000801)" in out
    assert "1 armour(s) partly drawn on UBE by another mod's armature" in out
    assert "Other  (mod.esp|000802)" in out
    assert "keeps our armature mod.esp|8A0 beside another mod's UBE one" in out


# ------------------------------------------------------------ the coverage step

def _emit(tmp_path, monkeypatch, *, real_index=False):
    """Drive the real coverage step with both passes stubbed; return what the
    exclusion scan and each pass were handed."""
    inst = tmp_path / "inst"
    mods = inst / "mods"
    prof = inst / "profiles" / "P"
    prof.mkdir(parents=True)
    (prof / "modlist.txt").write_text("+Our Output\n+Their UBE Patch\n",
                                      encoding="utf-8")
    _save(mods / "Our Output" / "_unmerged_patches" / "X UBE patch.esp", ["Skyrim.esm"], [])
    theirs = _save(mods / "Their UBE Patch" / "X UBE patch.esp", ["Skyrim.esm"], [])
    out = mods / "Our Output"
    (out / "meshes" / "!UBE").mkdir(parents=True)
    (out / "meshes" / "!UBE" / "x_1.nif").write_bytes(b"x")
    patches = out / "_unmerged_patches"
    lay = paths.Layout(mods_root=mods, instance_dir=inst, selected_profile="P")
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "active_plugins_ordered", lambda l: ["X UBE patch.esp"])
    if not real_index:
        monkeypatch.setattr(ac.paths, "plugin_file_index",
                            lambda l: {"x ube patch.esp": theirs})
    seen = {"covered": []}

    def _covered(*a, **k):
        seen["covered"].append(k.get("halves", ("ini", "esp")))
        return set()
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", _covered)
    monkeypatch.setattr(ac, "_mesh_exists_anywhere", lambda output: None)
    monkeypatch.setattr(ac, "_third_party_ube_twin_lookup", lambda *a, **k: None)
    monkeypatch.setattr(ac, "_batch_npc_worn_armos", lambda **k: None)
    marker = lambda p: True
    monkeypatch.setattr(ac, "_game_view_mesh_resolver", lambda output: marker)

    def _fake(name):
        def run(out_path, ordered, **k):
            seen[name] = (k.get("mesh_live"), [Path(p) for p in ordered])
            return {"armo_targets": 1, "minted_armas": 1}
        return run
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_nonbody_ube_coverage_patch",
                        _fake("nb"))
    monkeypatch.setattr(ac.ube_patcher, "generate_modded_body_ube_coverage_patch",
                        _fake("bd"))
    ac._emit_unified_coverage_patches(out, patches, [], "CBBE_to_UBE_Combined.esp")
    return seen, marker, theirs


def test_the_step_asks_for_the_skypatcher_half_and_hands_the_game_view(
        tmp_path, monkeypatch):
    """TPC-a: the passes judge plugin armatures themselves."""
    seen, marker, _t = _emit(tmp_path, monkeypatch)
    assert seen["covered"] == [("ini",)]
    assert seen["nb"][0] is marker and seen["bd"][0] is marker


def test_switched_off_the_step_asks_for_both_halves(tmp_path, monkeypatch):
    """TPC-b: the switch selects the whole old behaviour, never half of it."""
    monkeypatch.setenv(OFF, "1")
    seen, _m, _t = _emit(tmp_path, monkeypatch)
    assert seen["covered"] == [("ini", "esp")]
    assert seen["nb"][0] is None and seen["bd"][0] is None


def test_the_step_reads_the_plugin_the_game_loads(tmp_path, monkeypatch):
    """RPI-i: our un-loaded copy of a name sits in our own (higher-priority)
    output; the winner scan reads the third-party root plugin."""
    seen, _m, theirs = _emit(tmp_path, monkeypatch, real_index=True)
    assert seen["nb"][1] == [theirs] and seen["bd"][1] == [theirs]


def _pack_mod(tmp_path):
    from tests.test_claim_meshes_prefix import _pack, SUIT
    return _pack(tmp_path, SUIT)


def test_the_plugin_half_is_left_out_on_request(tmp_path, monkeypatch):
    """TPC-c/d: the planner's call (both halves, the default) still excludes a
    plugin-patched armour; the coverage step's call does not -- and one cached
    answer never serves the other."""
    monkeypatch.setattr(ac, "_UBE_COVERED_CACHE", {})
    target = _pack_mod(tmp_path)
    assert target in ac._third_party_ube_covered_armos(tmp_path)
    assert target not in ac._third_party_ube_covered_armos(tmp_path, halves=("ini",))
    assert target in ac._third_party_ube_covered_armos(tmp_path, halves=("ini", "esp"))


# ------------------------------------------------------------ other callers

def test_the_worn_set_is_rebuilt_when_the_index_mode_flips(tmp_path, monkeypatch):
    """RPI-g: a long-lived GUI process can flip the switch between two runs."""
    lay = paths.Layout(mods_root=tmp_path, instance_dir=tmp_path)
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: lay)
    monkeypatch.setattr(paths, "enabled_mods_ordered", lambda l: ["A"])
    monkeypatch.setattr(paths, "active_plugins_ordered", lambda l: ["A.esp"])
    monkeypatch.setattr(paths, "plugin_file_index", lambda l: {"a.esp": tmp_path / "A.esp"})
    monkeypatch.setattr(ac, "_NPC_WORN_CACHE", {})
    calls = []
    monkeypatch.setattr(ac, "_npc_worn_armos", lambda p: calls.append(1) or frozenset())
    ac._batch_npc_worn_armos(for_coverage=True)
    ac._batch_npc_worn_armos(for_coverage=True)
    assert len(calls) == 1
    monkeypatch.setenv(OFF, "1")
    ac._batch_npc_worn_armos(for_coverage=True)
    assert len(calls) == 2


@pytest.mark.parametrize("switched_off", [False, True])
def test_validate_looks_up_masters_in_priority_order(tmp_path, monkeypatch, switched_off):
    """RPI-h: the index's order is the game's; the legacy walk kept sorted()."""
    if switched_off:
        monkeypatch.setenv(OFF, "1")
    seen = {}

    def _fake(path, **kw):
        seen["dirs"] = kw.get("master_data_dirs")
        return []
    mod = tmp_path / "mod"
    _save(mod / "Combined.esp", ["Skyrim.esm"], [])
    high, low = tmp_path / "Z High", tmp_path / "A Low"
    idx = {"b.esm": high / "B.esm", "a.esm": low / "A.esm"}
    monkeypatch.setattr(ac.ube_patcher, "validate_patch", _fake)
    monkeypatch.setattr(ac.paths, "discover_layout", lambda: None)
    monkeypatch.setattr(ac.paths, "plugin_file_index", lambda l: idx)
    args = type("A", (), {"mod_dir": str(mod), "meshes_root": None, "no_nifs": True})()
    ac._cmd_validate(args)
    assert seen["dirs"] == ([low, high] if switched_off else [high, low])
