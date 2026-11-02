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

"""#zap-alpha-archives (audit 2026-10-20 F06).

`#zap-alpha` read loose textures only; an archive-packed diffuse counted as fully
opaque cover, so the skin under its see-through texels was deleted (wound-overlay
outfits lost arms, torso and legs). The texture is now read from the archive the
game draws it from, through the converter's own archive index.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src import auto_convert as ac
from src import body_zap as bz
from src import overlay_transfer as ot
from src import paths as P
from tests.test_bsa_seek_read import _write_bsa

KEY = "textures/armor/x/wound_d.dds"


def _mod(root, mod, archive, payload):
    _write_bsa(root / mod / archive, [(r"textures\armor\x", "wound_d.dds", payload, None),
                                      (r"meshes\armor\x", "cuirass_1.nif", b"nif", None)])
    return root / mod


def test_a_dds_index_lists_textures_and_the_later_plugin_wins(tmp_path):
    a = _mod(tmp_path, "A", "A - Textures.bsa", b"from-A")
    b = _mod(tmp_path, "B", "B - Textures.bsa", b"from-B")
    # MO2 priority A first, but B's plugin loads later: the game draws B's copy.
    idx = ac._BsaMeshIndex([a, b], None, skip_bsa=ac._BsaMeshIndex._SKIP_BSA,
                           plugin_order=["A.esp", "B.esp"], ext=".dds")
    assert idx.read_bytes(KEY) == b"from-B"
    assert not idx.contains("armor/x/cuirass_1.nif")


def test_the_mesh_index_is_unchanged(tmp_path):
    a = _mod(tmp_path, "A", "A.bsa", b"tex")
    idx = ac._BsaMeshIndex([a], None)
    assert idx.contains("armor/x/cuirass_1.nif")
    assert not idx.contains(KEY)


def test_listings_of_other_file_types_are_not_shared(tmp_path):
    a = _mod(tmp_path, "A", "A.bsa", b"tex")
    meshes = ac._BsaMeshIndex([a], None)
    meshes.contains("armor/x/cuirass_1.nif")
    tex = ac._BsaMeshIndex([a], None, ext=".dds")
    assert not tex.adopt_listing(meshes)


def _layout(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "discover_layout", lambda: SimpleNamespace(game_data_dirs=[]))
    monkeypatch.setattr(P, "mods_root", lambda: tmp_path / "mods")
    monkeypatch.setattr(P, "enabled_mods_ordered", lambda lay: ["A"])
    monkeypatch.setattr(P, "overwrite_dir", lambda lay: None)
    monkeypatch.setattr(ot, "find_texconv", lambda: "texconv")
    bz._ALPHA_TEX.clear()
    dst = tmp_path / "out" / "meshes" / "armor" / "x" / "cuirass_1.nif"
    dst.parent.mkdir(parents=True)
    return dst


def test_an_archive_only_texture_is_decoded(monkeypatch, tmp_path):
    dst = _layout(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(bz, "_archive_texture", lambda key, lay, mr, order: b"DDS-bytes")

    def decode(src, tc, work):
        seen["bytes"] = Path(src).read_bytes()
        return np.full((4, 4, 4), 7, np.uint8)
    monkeypatch.setattr(ot, "dds_to_rgba", decode)
    a = bz._texture_alpha("armor/x/wound_d.dds", dst)
    assert seen["bytes"] == b"DDS-bytes"
    assert a is not None and a.shape == (4, 4) and int(a[0, 0]) == 7


def test_a_loose_texture_wins_over_the_archive(monkeypatch, tmp_path):
    dst = _layout(monkeypatch, tmp_path)
    loose = tmp_path / "mods" / "A" / KEY
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"loose")
    monkeypatch.setattr(bz, "_archive_texture",
                        lambda *a: (_ for _ in ()).throw(AssertionError("archive read with a loose copy")))
    monkeypatch.setattr(ot, "dds_to_rgba", lambda src, tc, work: np.zeros((2, 2, 4), np.uint8))
    assert bz._texture_alpha("armor/x/wound_d.dds", dst) is not None


def test_off_switch_reads_no_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(bz, "ZAP_ALPHA_ARCHIVES", False)
    assert bz._archive_texture(KEY, SimpleNamespace(game_data_dirs=[]), tmp_path, ["A"]) is None
    dst = _layout(monkeypatch, tmp_path)
    assert bz._texture_alpha("armor/x/wound_d.dds", dst) is None
