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

"""#weight-pair-provider (audit 2026-10-20 F20).

`build_mesh_index` picked each weight file's winner on its own, so a mod that
ships ONE weight split a pair across two designs (`_1` from a retexture, `_0` from
the base mod). Both weights now come from the first-ranked provider that ships both.
"""
from pathlib import Path

import pytest

from src import discovery

OFF = "CBBE2UBE_NO_WEIGHT_PAIR_PROVIDER"
K0, K1 = "clothes/farm/shoes_0.nif", "clothes/farm/shoes_1.nif"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    monkeypatch.setenv("CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE", "1")
    # A retexture's shoes are another mesh (237 verts); every other mod ships the 249-vert design.
    monkeypatch.setattr(discovery, "_mesh_signature", lambda p: (
        {"Shoes": (237, 314)} if Path(p).parents[3].name == "Retexture" else {"Shoes": (249, 330)}))


def _ship(mods: Path, mod: str, *weights):
    for w in weights:
        p = mods / mod / "meshes" / "clothes" / "farm" / f"shoes{w}.nif"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")


def _mod(p: Path) -> str:
    return p.parents[3].name


def test_a_one_weight_mod_does_not_split_the_pair(tmp_path):
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["Retexture", "Base"], target_keys={K0, K1})
    assert _mod(idx[K0]) == _mod(idx[K1]) == "Base"


def test_the_first_ranked_mod_shipping_both_wins(tmp_path):
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "Patch", "_0", "_1")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["Retexture", "Patch", "Base"], target_keys={K0, K1})
    assert _mod(idx[K0]) == _mod(idx[K1]) == "Patch"


def test_a_pair_from_one_mod_is_untouched(tmp_path):
    mods = tmp_path / "mods"
    _ship(mods, "High", "_0", "_1")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["High", "Base"], target_keys={K0, K1})
    assert _mod(idx[K0]) == _mod(idx[K1]) == "High"


def test_a_hotfix_of_one_weight_is_kept(tmp_path):
    # Same mesh, one weight fixed: the split pair is one design, as the game draws it.
    mods = tmp_path / "mods"
    _ship(mods, "Hotfix", "_0")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["Hotfix", "Base"], target_keys={K0, K1})
    assert (_mod(idx[K0]), _mod(idx[K1])) == ("Hotfix", "Base")


def test_an_unreadable_mesh_leaves_the_files(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "_mesh_signature", lambda p: None)
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["Retexture", "Base"], target_keys={K0, K1})
    assert (_mod(idx[K0]), _mod(idx[K1])) == ("Base", "Retexture")


def test_no_mod_shipping_both_leaves_the_files(tmp_path):
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "B", "_0")
    idx = discovery.build_mesh_index(mods, ["Retexture", "B"], target_keys={K0, K1})
    assert (_mod(idx[K0]), _mod(idx[K1])) == ("B", "Retexture")


def test_alternates_follow_the_new_winner(tmp_path):
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "Base", "_0", "_1")
    _ship(mods, "Older", "_0", "_1")
    alt = {}
    idx, win, seen = {}, {}, {}
    for m in ("Retexture", "Base", "Older"):
        for k in (K0, K1):
            p = mods / m / "meshes" / k
            if p.exists():
                seen.setdefault(k, []).append((m, p, 0))
                if k not in idx:
                    idx[k], win[k] = p, 0
    assert discovery._resolve_weight_pairs(idx, win, seen, alt) == ["clothes/farm/shoes"]
    assert [m for m, _ in alt[K1]] == ["Older"] and [m for m, _ in alt[K0]] == ["Older"]


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    mods = tmp_path / "mods"
    _ship(mods, "Retexture", "_1")
    _ship(mods, "Base", "_0", "_1")
    idx = discovery.build_mesh_index(mods, ["Retexture", "Base"], target_keys={K0, K1})
    assert (_mod(idx[K0]), _mod(idx[K1])) == ("Base", "Retexture")
