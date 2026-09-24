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

"""#bsa-only-sources -- a mod whose armour meshes live only in an ARCHIVE is a
conversion source.

Source selection counted a mod's armour meshes in loose files only (its own
folder, then every enabled mod's). A mod that ships its meshes in a .bsa found
none and was dropped before conversion could run -- although the convert step
resolves meshes from archives -- and nothing in the log said so. On a real
modlist that was 9 mods, 48 NIFs, 66 armatures the coverage step minted over an
unconverted CBBE mesh. The gate now also asks a lookup-only archive index over
the same folders the convert step lists, and names the mods it still drops."""
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import paths

OFF = "CBBE2UBE_NO_BSA_ONLY_SOURCES"


class _Archive:
    """Stand-in for bsa_strings.BSAArchive: contents by archive file name."""
    CONTENTS: dict = {}
    LISTED: list = []

    def __init__(self, path, eager=True):
        self.path = Path(path)

    def list_files(self):
        _Archive.LISTED.append(self.path.name)
        return list(_Archive.CONTENTS.get(self.path.name.lower(), []))

    def read_file(self, name):
        return b"NIFDATA"


@pytest.fixture
def archives(monkeypatch):
    import src.bsa_strings as bs
    monkeypatch.setattr(bs, "BSAArchive", _Archive)
    _Archive.CONTENTS = {}
    _Archive.LISTED = []
    # Hermetic: no host modlist, no game Data dir, no NPC scan.
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: paths.Layout())
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES", raising=False)
    ac._SELECTION_BSA_INDEX.clear()
    ac._ARMOR_MOD_DIRS_CACHE.clear()
    yield _Archive
    ac._SELECTION_BSA_INDEX.clear()
    ac._ARMOR_MOD_DIRS_CACHE.clear()


def _modlist(tmp_path, monkeypatch, *, archived=True):
    """mods/Fur Armour: a plugin, NO loose mesh, its armour in 'Fur Armour.bsa'
    (or in nothing at all); mods/Other: an unrelated enabled mod."""
    mods = tmp_path / "mods"
    fur = mods / "Fur Armour"
    fur.mkdir(parents=True)
    (fur / "FurArmour.esp").write_bytes(b"TES4")
    (fur / "Fur Armour.bsa").write_bytes(b"BSA")
    (mods / "Other").mkdir()
    if archived:
        _Archive.CONTENTS["fur armour.bsa"] = ["meshes\\armor\\furarmour\\coat_1.nif",
                                            "meshes\\armor\\furarmour\\coat_0.nif"]
    # The ESP gate itself is covered elsewhere; here it names the armour base.
    monkeypatch.setattr(
        ac, "_player_armor_mesh_bases",
        lambda d, **kw: {"armor/furarmour/coat"} if d.name == "Fur Armour" else set())
    return mods


def _select(mods, **kw):
    return ac._find_armor_mod_dirs_uncached(
        mods, require_arma=True, enabled_ordered=["Fur Armour", "Other"], **kw)


def test_a_mod_whose_armour_is_only_in_an_archive_is_a_source(
        tmp_path, monkeypatch, archives):
    sel = {c["name"]: c for c in _select(_modlist(tmp_path, monkeypatch))}
    assert "Fur Armour" in sel, "an archive-only armour mod must be selected"
    assert sel["Fur Armour"]["armor_nifs"] == 1          # one base found


def test_the_off_switch_drops_it_as_before(tmp_path, monkeypatch, archives, capsys):
    monkeypatch.setenv(OFF, "1")
    sel = _select(_modlist(tmp_path, monkeypatch))
    assert "Fur Armour" not in {c["name"] for c in sel}
    assert archives.LISTED == [], "switched off, no archive may be read"
    assert "found nowhere" not in capsys.readouterr().out, (
        "switched off, the run log must be today's")


def test_a_mod_whose_armour_is_nowhere_is_dropped_and_named(
        tmp_path, monkeypatch, archives, capsys):
    # Negative control: the archive gate must discriminate, not admit anything
    # with a plugin. The archive exists but holds no armour of this mod.
    sel = _select(_modlist(tmp_path, monkeypatch, archived=False))
    assert "Fur Armour" not in {c["name"] for c in sel}
    out = capsys.readouterr().out
    assert "armour meshes found nowhere" in out and "- Fur Armour" in out, (
        "a mod dropped at this gate used to vanish from every log")


def test_every_mod_dropped_at_the_gate_is_named(tmp_path, monkeypatch, archives,
                                                capsys):
    """Twelve mods whose armour is nowhere: each one is named. The log is the
    only place such a mod ever appears, so a capped list ("... and 2 more")
    would leave the ones past the cut as silent as before."""
    mods = tmp_path / "mods"
    names = [f"Lost Armour {i:02d}" for i in range(12)]
    for n in names:
        (mods / n).mkdir(parents=True)
        (mods / n / "Lost.esp").write_bytes(b"TES4")
    monkeypatch.setattr(ac, "_player_armor_mesh_bases",
                        lambda d, **kw: {f"armor/lost/{d.name[-2:]}"})
    assert ac._find_armor_mod_dirs_uncached(
        mods, require_arma=True, enabled_ordered=names) == []
    out = capsys.readouterr().out
    assert "found nowhere: 12 mod(s)" in out
    assert [n for n in names if f"    - {n}\n" not in out] == []


def test_a_loose_mesh_needs_no_archive(tmp_path, monkeypatch, archives):
    """The archive index is lazy: a mod whose mesh is loose in another mod is
    selected without reading one archive table."""
    mods = _modlist(tmp_path, monkeypatch)
    built = mods / "Other" / "meshes" / "armor" / "furarmour" / "coat_1.nif"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"x")
    assert "Fur Armour" in {c["name"] for c in _select(mods)}
    assert archives.LISTED == []


def test_selection_never_writes_and_the_convert_step_adopts_its_listing(
        tmp_path, monkeypatch, archives):
    mods = _modlist(tmp_path, monkeypatch)
    _select(mods)
    sel_idx = ac._SELECTION_BSA_INDEX[str(mods).lower()]
    assert sel_idx._staging is None, "selection's index must be lookup-only"
    assert sel_idx.extract("armor/furarmour/coat_1.nif") is None     # refuses to write
    assert not any(p.is_file() and p.suffix == ".nif" for p in tmp_path.rglob("*"))
    listed = len(archives.LISTED)
    assert listed == 1
    # The convert step's index over the SAME folders takes that listing...
    stg = tmp_path / "out" / "_bsa_staging"
    batch = ac._BsaMeshIndex(ac._load_order_bsa_dirs(mods, ["Fur Armour", "Other"], []),
                             stg)
    assert batch.adopt_listing(sel_idx)
    assert batch.contains("armor/furarmour/coat_1.nif")
    assert len(archives.LISTED) == listed, "adopted: no second archive scan"
    # ...and still extracts, into its own staging folder.
    got = batch.extract("armor/furarmour/coat_1.nif")
    assert got is not None and got[0].is_file() and stg in got[0].parents


def test_a_listing_of_other_folders_is_not_adopted(tmp_path, monkeypatch, archives):
    mods = _modlist(tmp_path, monkeypatch)
    _select(mods)
    sel_idx = ac._SELECTION_BSA_INDEX[str(mods).lower()]
    other = ac._BsaMeshIndex(ac._load_order_bsa_dirs(mods, ["Other"], []), None)
    assert not other.adopt_listing(sel_idx)
    assert other._index is None


def test_the_archive_folders_are_the_convert_steps(tmp_path):
    """One definition of the folder list: enabled mods by priority, then the game
    Data dir(s) last, never twice."""
    mods = tmp_path / "mods"
    data = tmp_path / "Game" / "Data"
    got = ac._load_order_bsa_dirs(mods, ["High", "Low"], [data, mods / "High"])
    assert got == [mods / "High", mods / "Low", data]


def test_the_memo_key_carries_the_switch(tmp_path, monkeypatch, archives):
    """A GUI refresh and the following convert share one selection pass; toggling
    the switch in between must not return the other setting's list."""
    mods = _modlist(tmp_path, monkeypatch)
    kw = dict(require_arma=True, enabled_ordered=["Fur Armour", "Other"])
    on = ac._find_armor_mod_dirs(mods, **kw)
    monkeypatch.setenv(OFF, "1")
    off = ac._find_armor_mod_dirs(mods, **kw)
    assert "Fur Armour" in {c["name"] for c in on}
    assert "Fur Armour" not in {c["name"] for c in off}
