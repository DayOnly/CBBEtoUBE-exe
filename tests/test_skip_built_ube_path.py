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

r"""#skip-built-ube-path -- a mesh another mod already ships BUILT for UBE at
the path the converter would write is left to that mod.

THE DEFECT. #skip-already-ube judges "already UBE" by the armour records of the
SAME plugin. A refit plugin that overrides only the armatures of a mage set sent
its meshes to conversion, and our copies -- above the hand-made UBE set in MO2 --
replaced it in game. Live pack: 54 of our meshes at a path another mod ships;
ours won 14 of them.

THE RULE. Per weight base: every variant planned for a base has a built twin, or
the base is converted as before (their `_1` beside our `_0` pairs two meshes).
An earlier run's copy moves out of `meshes\` with its `.tri` and `.xml`. A body
armature whose mesh is such a twin is admitted like a converted one.
`CBBE2UBE_NO_SKIP_BUILT_UBE_PATH=1` converts them again.
"""
import pytest

from src import auto_convert as ac
from src import ube_patcher as up
from tests.test_coverage_ube_twin import BODY, _body, _twin_of
from tests.test_npc_worn_nonplayable import load_order  # noqa: F401 (fixture)

SWITCH = "CBBE2UBE_NO_SKIP_BUILT_UBE_PATH"
TWIN = "CBBE2UBE_NO_COVERAGE_UBE_TWIN"
TORSO = r"patrol\armor\cuirass_1.nif"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv(TWIN, raising=False)


# ---------------------------------------------------------------- the switch

def test_it_is_on_unless_switched_off(monkeypatch):
    assert up._skip_built_ube_path() is True
    monkeypatch.setenv(SWITCH, "1")
    assert up._skip_built_ube_path() is False


def test_it_needs_the_twin_rule(monkeypatch):
    """Without the twin rule nothing would point coverage at the builder's mesh,
    so leaving it unconverted would leave the CBBE path drawn."""
    monkeypatch.setenv(TWIN, "1")
    assert up._skip_built_ube_path() is False


# ---------------------------------------------------------------- the planner

def _pairs(*rels):
    return [(None, r) for r in rels]


def test_a_fully_built_base_is_left_to_its_builder():
    twin = _twin_of("armor/set/cuirass_1.nif", "armor/set/cuirass_0.nif", mod="Set UBE")
    got = ac._built_ube_twins(
        _pairs("armor/set/cuirass_1.nif", "armor/set/cuirass_0.nif",
               "armor/set/boots_1.nif", "armor/set/boots_0.nif"), twin)
    assert got == {"armor/set/cuirass_1.nif": "Set UBE",
                   "armor/set/cuirass_0.nif": "Set UBE"}


def test_half_a_pair_is_converted_whole():
    """Their `_1` beside our `_0` would pair two different meshes."""
    twin = _twin_of("armor/set/cuirass_1.nif")
    assert ac._built_ube_twins(
        _pairs("armor/set/cuirass_1.nif", "armor/set/cuirass_0.nif"), twin) == {}


def test_a_single_weight_mesh_is_left_alone_too():
    twin = _twin_of("armor/set/earring.nif")
    assert ac._built_ube_twins(_pairs("armor/set/earring.nif"), twin) == {
        "armor/set/earring.nif": "A UBE Patch"}


def test_no_lookup_converts_everything():
    assert ac._built_ube_twins(_pairs("armor/set/cuirass_1.nif"), None) == {}


def test_an_earlier_copy_moves_out_of_meshes(tmp_path):
    out = tmp_path / "Out"
    root = out / "meshes" / "!UBE"
    d = root / "armor" / "set"
    d.mkdir(parents=True)
    for n in ("cuirass_0.nif", "cuirass_1.nif", "cuirass.tri", "cuirass.xml",
              "boots_1.nif", "boots.tri"):
        (d / n).write_bytes(n.encode())
    moved = ac._supersede_built_ube_outputs(
        out, root, ["armor/set/cuirass_1.nif", "armor/set/cuirass_0.nif"])
    assert moved == 4
    left = sorted(p.name for p in d.iterdir())
    assert left == ["boots.tri", "boots_1.nif"], "only that base moves"
    sup = out / "_superseded" / "meshes" / "!UBE" / "armor" / "set"
    assert (sup / "cuirass_1.nif").read_bytes() == b"cuirass_1.nif"
    assert (sup / "cuirass.tri").is_file() and (sup / "cuirass.xml").is_file()
    assert ac._supersede_built_ube_outputs(
        out, root, ["armor/set/cuirass_1.nif"]) == 0, "nothing left to move"


def test_the_planner_leaves_the_built_piece_and_converts_the_rest(
        load_order, tmp_path, monkeypatch):
    """Behavioural: the planned work skips the dress pair another mod built,
    converts the boots, and moves the earlier dress copy aside first."""
    class _First(BaseException):
        pass
    dress = ["armor/outfit/dress_1.nif", "armor/outfit/dress_0.nif"]
    boots = ["armor/outfit/boots_1.nif", "armor/outfit/boots_0.nif"]
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress + boots])
    seen = []

    def _worker(item):
        seen.append(item[1])
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    out = tmp_path / "out"
    stale = out / "meshes" / "!UBE" / "armor" / "outfit" / "dress_1.nif"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    with pytest.raises(_First):
        ac.auto_convert_mod(load_order, out, ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1,
                            built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
    assert seen and "boots" in seen[0].name
    assert not stale.exists()
    assert (out / "_superseded" / "meshes" / "!UBE" / "armor" / "outfit"
            / "dress_1.nif").read_bytes() == b"old"


def test_without_the_lookup_the_planner_converts_the_built_piece(
        load_order, tmp_path, monkeypatch):
    """Control: the same plan with no lookup converts the dress first."""
    class _First(BaseException):
        pass
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, "armor/outfit/dress_1.nif")])
    seen = []

    def _worker(item):
        seen.append(item[1])
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    with pytest.raises(_First):
        ac.auto_convert_mod(load_order, tmp_path / "out", ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1, built_ube_twin=None)
    assert seen and "dress_1" in seen[0].name


def test_another_output_prefix_converts_the_built_piece(
        load_order, tmp_path, monkeypatch):
    r"""The rule is about `meshes\!UBE\<rel>`, the path the builder ships. A
    plan that writes under another prefix collides with nothing it ships, so a
    built twin must not make the planner skip: the dress is converted first,
    exactly as with no lookup."""
    class _First(BaseException):
        pass
    dress = ["armor/outfit/dress_1.nif", "armor/outfit/dress_0.nif"]
    boots = ["armor/outfit/boots_1.nif", "armor/outfit/boots_0.nif"]
    src = tmp_path / "src.nif"
    src.write_bytes(b"x")
    monkeypatch.setattr(ac, "_resolve_armor_meshes",
                        lambda *a, **k: [(src, r) for r in dress + boots])
    seen = []

    def _worker(item):
        seen.append(item[1])
        raise _First()
    monkeypatch.setattr(ac, "_nif_convert_worker", _worker)
    ref = tmp_path / "ube_ref.nif"
    ref.write_bytes(b"x")
    with pytest.raises(_First):
        ac.auto_convert_mod(load_order, tmp_path / "out", ube_body_ref_path=ref,
                            master_data_dirs=[], nif_workers=1,
                            ube_path_prefix="!UBE_Other",
                            built_ube_twin=_twin_of(*dress, mod="Outfit UBE"))
    assert seen and "dress" in seen[0].name
    assert "!UBE_Other" in seen[0].parts


# ------------------------------------------------ the batch hands the lookup over

CUIRASS = ["armor/set/cuirass_1.nif", "armor/set/cuirass_0.nif"]
BOOTS = ["armor/set/boots_1.nif", "armor/set/boots_0.nif"]


def _handed_to_planner(tmp_path, monkeypatch, **kw):
    """Run the real `convert` step and return the lookup it handed the planner
    of its one source (None = no lookup)."""
    from tests.test_exclude_owned_coverage import run_convert
    converted, _ = run_convert(tmp_path, monkeypatch, **kw)
    assert len(converted) == 1, "the source was never planned"
    return converted[0]["built_ube_twin"]


def test_the_batch_hands_its_lookup_to_the_planner(tmp_path, monkeypatch):
    """Behavioural: `convert` builds the lookup over the modlist and the
    planner gets it, so a base another mod ships built is left to that mod."""
    lookup = _handed_to_planner(tmp_path / "on", monkeypatch,
                                built={"Set UBE": CUIRASS})
    assert lookup is not None, "the planner got no lookup"
    assert ac._built_ube_twins(_pairs(*CUIRASS, *BOOTS), lookup) == {
        r: "Set UBE" for r in CUIRASS}
    monkeypatch.setenv(SWITCH, "1")
    assert _handed_to_planner(tmp_path / "off", monkeypatch,
                              built={"Set UBE": CUIRASS}) is None, \
        "switched off, the planner must get no lookup"


def test_an_excluded_mods_build_never_makes_the_planner_skip(tmp_path, monkeypatch):
    """Behavioural: `--exclude-mods` reaches the planner's lookup. A build
    shipped by a mod the user excluded is not relied on -- coverage never
    points at it -- so the planner converts that base; another mod's build
    is still left to it."""
    lookup = _handed_to_planner(
        tmp_path, monkeypatch, exclude=["Set UBE"],
        built={"Set UBE": CUIRASS, "Boots UBE": BOOTS})
    assert ac._built_ube_twins(_pairs(*CUIRASS, *BOOTS), lookup) == {
        r: "Boots UBE" for r in BOOTS}


# ---------------------------------------------------------------- coverage

def test_a_built_twin_admits_a_body_armature(tmp_path):
    """The planner left the torso to its builder; the armour records the
    builder's own patch does not reach still get it."""
    st, minted = _body(tmp_path, {b"MOD3": TORSO}, BODY, set(), _twin_of(TORSO))
    assert st["armo_targets"] == 1
    assert minted[0][b"MOD3"] == "!UBE\\" + TORSO


def test_switched_off_a_twin_admits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    st, minted = _body(tmp_path, {b"MOD3": TORSO}, BODY, set(), _twin_of(TORSO))
    assert minted == [] and st["armo_targets"] == 0


def test_without_the_twin_rule_a_twin_admits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(TWIN, "1")
    st, minted = _body(tmp_path, {b"MOD3": TORSO}, BODY, set(), _twin_of(TORSO))
    assert minted == [] and st["armo_targets"] == 0


def test_no_twin_admits_nothing(tmp_path):
    """Negative control: the lookup is asked, not assumed."""
    st, minted = _body(tmp_path, {b"MOD3": TORSO}, BODY, set(), _twin_of())
    assert minted == [] and st["armo_targets"] == 0
