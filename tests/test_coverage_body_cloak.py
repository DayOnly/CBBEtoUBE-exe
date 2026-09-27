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

r"""#coverage-body-cloak -- a robe's draped cape is drawn on UBE with the robe.

THE DEFECT. #coverage-body-accessory skips every cloak-named armature (the
planner admits a cloak for conversion by name, so an unconverted one might be
body-fitted cloth). But the conversion's crash guard drops a cloak on a free
slot (35) whose mesh has no body-fit bone, so a worn robe with such a cape drew
the robe on a UBE actor and nothing for the cape.

THE RULE. A cloak-named DefaultRace armature rides along with the robe when
every world mesh it names (the copy the game loads) is skinned and bound to no
thigh/calf/butt/breast/belly bone -- UBE-primary, its own mesh. Unskinned,
body-fitted or unreadable stays out; so does a beast variant or an armature
that already names a UBE race, whatever the race-guard switch says.
`CBBE2UBE_NO_COVERAGE_BODY_CLOAK=1` leaves every cloak out again.
"""
import struct

import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from src.esp import Group, Record, encode_subrecord, encode_zstring
from tests.test_coverage_ube_twin import DEFAULT, _minted, _save

OFF = "CBBE2UBE_NO_COVERAGE_BODY_CLOAK"
ACC_OFF = "CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY"
GUARD_OFF = "CBBE2UBE_NO_ACCESSORY_RACE_GUARD"
BODY = 1 << 2                                   # slot 32
CLOAK = 1 << 5                                  # slot 35, a free slot
NORD, KHAJIIT, KHAJIIT_V = 0x00013746, 0x00013745, 0x00088845
UBE_RACE = 0x01005734                            # UBE_AllRace.esp, master index 1
ROBE = r"clothes\robes\robe_1.nif"
DRAPE = r"clothes\robes\capef_1.nif"            # skinned to the spine only
FITTED = r"clothes\robes\capethigh_1.nif"       # skinned to a thigh too
STATIC = r"clothes\robes\capestatic_1.nif"      # no skin at all
BEAST = r"clothes\robes\capekhajiit_1.nif"      # a drape, beast races only
HIPS = r"clothes\robes\capehips_1.nif"          # pelvis, spine and upper arms
SPINE = ("NPC Spine2 [Spn2]", "NPC R Clavicle [RClv]")
THIGH = ("NPC Spine [Spn0]", "NPC L Thigh [LThg]")
# The bones the one cape of the reported modlist this rule draws is bound to
# (a subset): the rule tests for thigh/calf/butt/breast/belly bones only, the
# same list the conversion's crash guard used when it left the cape unconverted.
PELVIS_ARMS = ("NPC Pelvis [Pelv]", "NPC Spine [Spn0]", "NPC Spine2 [Spn2]",
               "NPC L UpperArm [LUar]", "NPC R UpperarmTwist1 [RUt1]")

needs_pynifly = pytest.mark.skipif(
    not __import__("tests.synthetic_nif", fromlist=["x"]).pynifly_available(),
    reason="pynifly not available")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    for k in (OFF, ACC_OFF, GUARD_OFF):
        monkeypatch.delenv(k, raising=False)


def _skinned(tmp_path, name, bones):
    from tests.synthetic_nif import build_skinned_shape_nif
    return build_skinned_shape_nif(tmp_path / name, bones=bones).read_bytes()


def _unskinned(tmp_path, name):
    from tests.synthetic_nif import build_shape_nif
    return build_shape_nif(tmp_path / name).read_bytes()


@pytest.fixture
def lookup(tmp_path, monkeypatch):
    """The real lookup (`_mesh_exists_anywhere`) over one loose mod holding
    the capes the tests draw."""
    nifs = tmp_path / "nifs"
    nifs.mkdir()
    files = {DRAPE: _skinned(nifs, "d.nif", SPINE),
             FITTED: _skinned(nifs, "f.nif", THIGH),
             STATIC: _unskinned(nifs, "s.nif"),
             BEAST: _skinned(nifs, "b.nif", SPINE),
             HIPS: _skinned(nifs, "h.nif", PELVIS_ARMS)}
    mods = tmp_path / "mods"
    for rel, data in files.items():
        f = mods / "Cape Mod" / "meshes" / rel.replace("\\", "/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    (mods / "Out").mkdir()

    class _Lay:
        game_data_dirs = []
    monkeypatch.setattr(ac.paths, "discover_layout", lambda *a, **k: _Lay())
    monkeypatch.setattr(ac.paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ac.paths, "enabled_mods_ordered",
                        lambda lay: ["Out", "Cape Mod"])
    return ac._mesh_exists_anywhere(mods / "Out")


def _arma(formid, slots, path, races=(NORD,)):
    p = encode_subrecord(b"EDID", encode_zstring(f"AA{formid:X}"))
    p += encode_subrecord(b"BOD2", struct.pack("<II", slots, 0))
    p += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    p += encode_subrecord(b"MOD2", encode_zstring(path))
    p += encode_subrecord(b"MOD3", encode_zstring(path))
    for r in races:
        p += encode_subrecord(b"MODL", struct.pack("<I", r))
    return Record(sig=b"ARMA", flags=0, formid=formid, payload=p)


def _pass(tmp_path, capes, mesh_exists):
    """A robe (converted) with `capes`, through the body pass."""
    world = tmp_path / "world"
    sky = _save(world / "Skyrim.esm", [], [])
    ube = _save(world / "UBE_AllRace.esp", ["Skyrim.esm"], [])
    armas = [_arma(0x02000800, BODY, ROBE)] + capes
    q = encode_subrecord(b"EDID", encode_zstring("CapedRobe"))
    q += encode_subrecord(b"BOD2", struct.pack("<II", BODY | CLOAK, 0))
    q += encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
    for a in armas:
        q += encode_subrecord(b"MODL", struct.pack("<I", a.formid))
    armo = Record(sig=b"ARMO", flags=0, formid=0x02000810, payload=q)
    mod = _save(world / "Mod.esp", ["Skyrim.esm", "UBE_AllRace.esp"],
                [Group(label=b"ARMA", records=armas),
                 Group(label=b"ARMO", records=[armo])])
    out = world / "UBE_ModBody_Coverage UBE patch.esp"
    st = up.generate_modded_body_ube_coverage_patch(
        out, [sky, ube, mod], converted_rel_paths={ROBE.replace("\\", "/")},
        exclude_names={out.name.lower()}, master_data_dirs=[world],
        cover_all=True, cover_hands_feet=True, preserve_textures=True,
        mesh_exists=mesh_exists)
    return st, out


def _mod3(out):
    return sorted(m[b"MOD3"] for m in _minted(out))


# ------------------------------------------------------------------ the rule

@needs_pynifly
def test_the_draped_cape_rides_with_the_robe(tmp_path, lookup):
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, DRAPE)], lookup)
    minted = _minted(out)
    assert sorted(m[b"MOD3"] for m in minted) == sorted(["!UBE\\" + ROBE, DRAPE])
    cape = [m for m in minted if m[b"MOD3"] == DRAPE][0]
    assert cape[b"MOD2"] == DRAPE, "the cape keeps its own mesh, both slots"
    assert st["body_accessory"] == ["mod.esp|801"]
    assert st["body_cloak"] == ["mod.esp|801"]


@needs_pynifly
def test_a_cape_with_thigh_weights_stays_out(tmp_path, lookup):
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, FITTED)], lookup)
    assert _mod3(out) == ["!UBE\\" + ROBE], "an unconverted body fit is not drawn"
    assert st["body_accessory"] == [] and st["body_cloak"] == []


@needs_pynifly
def test_a_cape_weighted_to_the_pelvis_and_arms_rides_with_the_robe(tmp_path, lookup):
    """Only thigh/calf/butt/breast/belly bones keep a cape out: one bound to
    the pelvis, spine and upper arms is what the crash guard left unconverted,
    so nothing else draws it on UBE."""
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, HIPS)], lookup)
    assert _mod3(out) == sorted(["!UBE\\" + ROBE, HIPS])
    assert st["body_cloak"] == ["mod.esp|801"]


@needs_pynifly
def test_an_unskinned_cape_stays_out(tmp_path, lookup):
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, STATIC)], lookup)
    assert _mod3(out) == ["!UBE\\" + ROBE]
    assert st["body_cloak"] == []


@needs_pynifly
def test_a_cape_the_game_cannot_load_stays_out(tmp_path, lookup):
    missing = r"clothes\robes\capemissing_1.nif"
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, missing)], lookup)
    assert _mod3(out) == ["!UBE\\" + ROBE]


def test_without_a_mesh_reader_no_cape_is_taken(tmp_path):
    """A lookup that cannot read meshes: fail closed."""
    def exists(model):
        return True
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, DRAPE)], exists)
    assert _mod3(out) == ["!UBE\\" + ROBE]


@needs_pynifly
def test_a_beast_cape_variant_stays_out_even_with_the_race_guard_off(
        tmp_path, lookup, monkeypatch):
    """The human cape rides along; its Khajiit variant does not -- and the
    accessory race guard's switch does not bring it back, since no parent
    output ever drew a cape here."""
    monkeypatch.setenv(GUARD_OFF, "1")
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, DRAPE),
                               _arma(0x02000802, CLOAK, BEAST, (KHAJIIT, KHAJIIT_V))],
                    lookup)
    assert _mod3(out) == sorted(["!UBE\\" + ROBE, DRAPE])
    assert st["body_cloak"] == ["mod.esp|801"]
    assert st["beast_variant_skipped"] == ["mod.esp|802"]


@needs_pynifly
def test_a_cape_that_already_names_a_ube_race_stays_out(tmp_path, lookup, monkeypatch):
    monkeypatch.setenv(GUARD_OFF, "1")
    st, out = _pass(tmp_path, [_arma(0x02000801, CLOAK, DRAPE, (NORD, UBE_RACE))],
                    lookup)
    assert _mod3(out) == ["!UBE\\" + ROBE], "it already draws on UBE"
    assert st["body_cloak"] == []


@needs_pynifly
@pytest.mark.parametrize("switch", [OFF, ACC_OFF])
def test_switched_off_the_output_is_as_before(tmp_path, lookup, monkeypatch, switch):
    """Either switch: the same bytes as a pass that never admits a cape (a
    lookup with no mesh reader)."""
    def exists(model):
        return lookup(model)
    capes = [_arma(0x02000801, CLOAK, DRAPE)]
    _st, before = _pass(tmp_path / "before", capes, exists)
    monkeypatch.setenv(switch, "1")
    st, after = _pass(tmp_path / "after", capes, lookup)
    assert st["body_cloak"] == []
    assert after.read_bytes() == before.read_bytes()


# ---------------------------------------------------------- reading the mesh

@needs_pynifly
def test_the_mesh_reader_tells_a_drape_from_body_cloth(tmp_path):
    assert ac._nif_bytes_unfitted_skin(_skinned(tmp_path, "d.nif", SPINE)) is True
    assert ac._nif_bytes_unfitted_skin(_skinned(tmp_path, "f.nif", THIGH)) is False
    assert ac._nif_bytes_unfitted_skin(_unskinned(tmp_path, "s.nif")) is False
    assert ac._nif_bytes_unfitted_skin(b"not a nif") is None


@needs_pynifly
def test_the_lookup_reads_the_copy_the_game_loads(lookup):
    assert lookup.unfitted_skin(DRAPE) is True
    assert lookup.unfitted_skin("Meshes\\" + DRAPE.upper()) is True
    assert lookup.unfitted_skin(FITTED) is False
    assert lookup.unfitted_skin(STATIC) is False
    assert lookup.unfitted_skin(r"clothes\robes\capemissing_1.nif") is None


# ------------------------------------------------------------------ the report

def test_the_capes_are_reported(capsys):
    ac._report_coverage_holds([{"body_accessory": ["mod.esp|801"],
                                "body_cloak": ["mod.esp|801"]}])
    assert "1 of them a cape draped from the spine" in capsys.readouterr().out
