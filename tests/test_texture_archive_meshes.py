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

"""#texture-archive-meshes -- an archive named "... Textures" or "... Retexture"
is read for meshes.

The archive index skipped every archive whose name contained "texture". A large
content mod keeps ALL its meshes in "<name> - Textures.bsa", and the substring
also caught armour archives named "... Retexture SE.bsa", so those meshes never
resolved: on a real modlist a suffixless boots mesh the game draws was never
converted (only a loose weight pair beside it was), and a female mesh that
missed dragged its male mesh into the plan instead. Voice, sound and facegen
archives stay skipped; the setup check still reads only the vanilla archives."""
from pathlib import Path

import pytest

from src import auto_convert as ac
from src import preflight

OFF = "CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES"
BOOTS = "bs/armor/iron/boots.nif"


class _Archive:
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
    monkeypatch.delenv(OFF, raising=False)
    return _Archive


def _archive(folder: Path, name: str, *files: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"BSA")
    _Archive.CONTENTS[name.lower()] = ["meshes\\" + f.replace("/", "\\") for f in files]


def test_meshes_in_a_textures_archive_are_indexed(tmp_path, archives):
    mod = tmp_path / "Big Content"
    _archive(mod, "Big Content - Textures.bsa", BOOTS)
    _archive(mod, "Big Content.bsa")                  # scripts and sound only
    assert ac._BsaMeshIndex([mod], None).contains(BOOTS)


def test_a_retexture_archive_is_indexed_too(tmp_path, archives):
    mod = tmp_path / "Armour Retexture"
    _archive(mod, "Armour Retexture SE.bsa", "armor/set/cuirass_1.nif")
    assert ac._BsaMeshIndex([mod], None).contains("armor/set/cuirass_1.nif")


def test_voice_sound_and_facegen_archives_are_still_skipped(tmp_path, archives):
    # Negative control: the fix is ONE word, not "read every archive".
    mod = tmp_path / "Quest"
    _archive(mod, "Quest - Voices.bsa", "a/voice_1.nif")
    _archive(mod, "Quest - Sounds.bsa", "a/sound_1.nif")
    _archive(mod, "Quest - FaceGen.bsa", "a/face_1.nif")
    idx = ac._BsaMeshIndex([mod], None)
    assert not any(idx.contains(k) for k in
                   ("a/voice_1.nif", "a/sound_1.nif", "a/face_1.nif"))
    assert archives.LISTED == [], "a skipped archive must not even be listed"


def test_the_off_switch_skips_texture_archives_again(tmp_path, archives, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mod = tmp_path / "Big Content"
    _archive(mod, "Big Content - Textures.bsa", BOOTS)
    _archive(mod, "Armour Retexture SE.bsa", "armor/set/cuirass_1.nif")
    idx = ac._BsaMeshIndex([mod], None)
    assert not idx.contains(BOOTS)
    assert not idx.contains("armor/set/cuirass_1.nif")


def test_the_off_switch_leaves_a_callers_own_skip_list_alone(
        tmp_path, archives, monkeypatch):
    """The switch restores the OLD DEFAULT; a caller that names its own list
    (the coverage step's existence lookup) keeps it, switch or not."""
    monkeypatch.setenv(OFF, "1")
    mod = tmp_path / "Big Content"
    _archive(mod, "Big Content - Textures.bsa", BOOTS)
    _archive(mod, "Big Content - Voices.bsa", "a/voice_1.nif")
    own = ac._BsaMeshIndex([mod], None, skip_bsa=("voice",))
    assert own.contains(BOOTS)
    assert not own.contains("a/voice_1.nif")
    assert not ac._BsaMeshIndex([mod], None).contains(BOOTS), \
        "control: the default list still skips it under the switch"


def test_the_higher_priority_archive_wins_a_shared_path(tmp_path, archives):
    """A retexture mod above its base mod ships the same mesh path; the index
    takes the higher-priority copy -- the one the game loads."""
    high = tmp_path / "Retexture"
    low = tmp_path / "Base"
    _archive(high, "Set Retexture SE.bsa", "armor/set/cuirass_1.nif")
    _archive(low, "Set.bsa", "armor/set/cuirass_1.nif")
    idx = ac._BsaMeshIndex([high, low], None)
    assert idx.contains("armor/set/cuirass_1.nif")
    assert idx._index["armor/set/cuirass_1.nif"][0].name == "Set Retexture SE.bsa"


def test_an_index_never_adopts_a_listing_made_under_the_other_rule(
        tmp_path, archives, monkeypatch):
    mod = tmp_path / "Big Content"
    _archive(mod, "Big Content - Textures.bsa", BOOTS)
    monkeypatch.setenv(OFF, "1")
    old_rule = ac._BsaMeshIndex([mod], None)
    old_rule.contains(BOOTS)                           # scans, without the archive
    monkeypatch.delenv(OFF)
    new_rule = ac._BsaMeshIndex([mod], tmp_path / "stg")
    assert not new_rule.adopt_listing(old_rule)
    assert new_rule.contains(BOOTS)


def test_the_setup_check_still_reads_only_the_vanilla_archives(
        tmp_path, archives, monkeypatch):
    """The setup-check probe filters by archive-name prefix, because under MO2 the
    game Data dir lists every enabled mod's archives. Allowing "texture" names
    must not widen what it reads: the vanilla texture archives and a mod's
    texture archive stay out, and the vanilla mesh archive still answers."""
    data = tmp_path / "Data"
    _archive(data, "Skyrim - Meshes0.bsa", "armor/iron/cuirass_1.nif")
    _archive(data, "Skyrim - Textures0.bsa", "armor/iron/boots_1.nif")
    _archive(data, "Some Mod - Textures.bsa", "armor/iron/gauntlets_1.nif")
    monkeypatch.setattr(ac, "_preflight_vanilla_sweep", lambda d: (True, "ok"))
    monkeypatch.setattr(ac, "_player_armor_mesh_bases",
                        lambda d, **kw: {"armor/iron/cuirass", "armor/iron/boots",
                                         "armor/iron/gauntlets"})
    ok, why = preflight._probe_vanilla_sweep(data)
    assert ok, why
    assert "1/3 sampled meshes" in why, why
    assert archives.LISTED == ["Skyrim - Meshes0.bsa"]
