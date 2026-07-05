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

"""Guards for the 'Add UBE copy' overlay mode: it must insert a `UBE <name>`
duplicate AddXPaint ONLY for overlays that were baked, keep the original call
untouched, and point the copy at the ube/ variant path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import overlay_transfer as ot


def test_ube_variant_rel():
    assert (ot._ube_variant_rel(
        "textures/actors/character/overlays/set/x.dds")
        == "textures/actors/character/overlays/set/ube/x.dds")
    assert ot._ube_variant_rel("x.dds") == "ube/x.dds"


def test_ube_variant_scriptpath_double_backslash():
    sp = "Actors\\\\Character\\\\Overlays\\\\Set\\\\tribal.dds"
    assert (ot._ube_variant_scriptpath(sp)
            == "Actors\\\\Character\\\\Overlays\\\\Set\\\\UBE\\\\tribal.dds")


def test_add_ube_paint_lines_only_baked_and_preserves_original():
    text = (
        "Function OnInit()\n"
        '    AddBodyPaint("Tribal", "Actors\\\\Character\\\\Overlays'
        '\\\\Set\\\\tribal.dds")\n'
        '    AddBodyPaint("Other", "Actors\\\\Character\\\\Overlays'
        '\\\\Set\\\\other.dds")\n'
        "EndFunction\n"
    )
    baked = {"textures/actors/character/overlays/set/tribal.dds"}
    new, n = ot.add_ube_paint_lines(text, baked)
    assert n == 1
    # original untouched
    assert ('    AddBodyPaint("Tribal", "Actors\\\\Character\\\\Overlays'
            '\\\\Set\\\\tribal.dds")') in new
    # UBE copy inserted, indentation kept, points at the ube/ variant
    assert ('    AddBodyPaint("UBE Tribal", "Actors\\\\Character\\\\Overlays'
            '\\\\Set\\\\UBE\\\\tribal.dds")') in new
    # the un-baked overlay gets NO copy
    assert '"UBE Other"' not in new


def test_add_ube_paint_lines_handles_all_paint_types_and_trailing_args():
    text = ('AddHandPaint("Ink", "Actors\\\\Character\\\\Overlays'
            '\\\\h.dds", False)\n')
    baked = {"textures/actors/character/overlays/h.dds"}
    new, n = ot.add_ube_paint_lines(text, baked)
    assert n == 1
    # trailing arg (False) preserved on the copy
    assert ('AddHandPaint("UBE Ink", "Actors\\\\Character\\\\Overlays'
            '\\\\UBE\\\\h.dds", False)') in new


def test_is_male_overlay():
    # a male marker outside the fixed prefix -> male (skip)
    assert ot._is_male_overlay(
        "textures/actors/character/overlays/set/malebody.dds") is True
    assert ot._is_male_overlay(
        "Actors\\Character\\Overlays\\Male\\tribal.dds") is True
    # 'female' must NOT be read as 'male'
    assert ot._is_male_overlay(
        "textures/actors/character/overlays/set/femalebody.dds") is False
    # neutral overlay -> not male
    assert ot._is_male_overlay(
        "textures/actors/character/overlays/set/tribal.dds") is False


def test_list_overlay_mods_orders_by_discovery_and_dedupes(monkeypatch):
    # list_overlay_mods just flattens discover_overlays' sources to unique mod
    # names in discovery (== load-priority) order.
    monkeypatch.setattr(ot, "discover_overlays", lambda *a, **k: {
        "body": {"a.dds": ("loose", "p", "ModA"),
                 "b.dds": ("loose", "p", "ModB")},
        "hands": {"c.dds": ("bsa", "arc", "int", "ModA")},   # dup ModA
        "feet": {"d.dds": ("loose", "p", "ModC")},
    })
    assert ot.list_overlay_mods(object()) == ["ModA", "ModB", "ModC"]


def test_auto_convert_list_overlay_mods_wrapper(monkeypatch, tmp_path):
    # Regression: the GUI picker's scan goes through auto_convert.list_overlay_mods,
    # which imports overlay_transfer LAZILY. It once referenced overlay_transfer at
    # module scope -> NameError -> a broad except turned it into [] ("no overlay
    # mods found") even though the modlist had plenty. Assert it maps names through.
    from src import auto_convert
    from src import paths
    from src import overlay_transfer as ot2
    mods = tmp_path / "mods"
    mods.mkdir()
    monkeypatch.setattr(paths, "discover_layout", lambda *a, **k: object())
    monkeypatch.setattr(paths, "export_to_env", lambda *a, **k: None)
    monkeypatch.setattr(paths, "mods_root", lambda: mods)
    monkeypatch.setattr(ot2, "list_overlay_mods",
                        lambda layout, skip_mods=(): ["ModX", "ModY"])
    assert auto_convert.list_overlay_mods() == [{"name": "ModX"}, {"name": "ModY"}]


def test_scan_ube_native_overlay_domain_is_empty():
    # The mesh/shape UBE detector is armor-only (overlays are textures, no
    # meshes) -- overlays must short-circuit to [] without touching pynifly.
    from src import auto_convert
    assert auto_convert.scan_ube_native("overlay") == []
