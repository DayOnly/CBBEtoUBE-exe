# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.  See <https://www.gnu.org/licenses/>.

"""Overlay packs that (A) register via lowercase AddBodypaint( calls and/or
(B) ship textures OUTSIDE the standard overlay roots must still be discovered
and transferred. Regression for the Bitchcraft-tattoo case."""
import src.overlay_slots as osl
import src.overlay_transfer as ot
import src.paths as P


# --- Bug A: case-insensitive script pre-filter -----------------------------
def test_script_pre_filter_is_case_insensitive():
    # Papyrus is case-insensitive; lowercase 'p' is valid and works in-game.
    assert osl._script_has_paint('AddBodypaint("n", "Actors\\X\\a.dds")')
    assert osl._script_has_paint('addBODYPAINT("n", "y")')
    assert osl._script_has_paint('AddWarPaint("n", "y")')
    assert not osl._script_has_paint("nothing relevant in this body")


def test_parser_yields_from_lowercase_call():
    calls = list(osl.iter_paint_calls(
        r'AddBodypaint("BCT - X", "Actors\\Cridow Bitchcraft\\x.dds")'))
    assert calls == [("body", "textures/actors/cridow bitchcraft/x.dds")]


# --- Bug A+B together: discover a custom-path, lowercase-registered overlay -
def test_discover_collects_script_registered_custom_path(tmp_path, monkeypatch):
    mods = tmp_path / "mods"
    mod = mods / "FakeTattoos"
    texrel = "textures/actors/faketattoos/chest.dds"
    (mod / "textures/actors/faketattoos").mkdir(parents=True)
    (mod / texrel).write_bytes(b"DDS \x00")          # dummy; discovery is path-only
    (mod / "scripts/source").mkdir(parents=True)
    (mod / "scripts/source/fake.psc").write_text(
        "ScriptName Fake extends RaceMenuBase\n"
        "Function OnInit()\n"
        '    AddBodypaint("Fake Chest", "Actors\\\\FakeTattoos\\\\chest.dds")\n'
        "EndFunction\n")

    monkeypatch.setattr(P, "mods_root", lambda: mods)
    monkeypatch.setattr(P, "enabled_mods_ordered", lambda layout=None: ["FakeTattoos"])
    osl._slot_map_cache.clear()

    by_region = ot.discover_overlays(layout=None)
    # custom path (outside _OVERLAY_ROOTS) is discovered AND routed to body via
    # the script slot map -- both the lowercase-call fix and the custom-path
    # collection fix are required for this to pass.
    assert texrel in by_region["body"], by_region
    assert by_region["body"][texrel][0] == "loose"


def test_no_custom_path_packs_leaves_root_discovery_unchanged(tmp_path, monkeypatch):
    # A standard-root overlay with NO script still discovered; registered_nonroot
    # empty -> the wider 'textures' listing is skipped (cheap path preserved).
    mods = tmp_path / "mods"
    mod = mods / "StdOverlays"
    rel = "textures/actors/character/overlays/00 body.dds"
    (mod / "textures/actors/character/overlays").mkdir(parents=True)
    (mod / rel).write_bytes(b"DDS \x00")
    monkeypatch.setattr(P, "mods_root", lambda: mods)
    monkeypatch.setattr(P, "enabled_mods_ordered", lambda layout=None: ["StdOverlays"])
    osl._slot_map_cache.clear()
    by_region = ot.discover_overlays(layout=None)
    # present in some region (classifier/ambiguous rules decide which); the point
    # is the standard root path is still collected.
    assert any(rel in v for v in by_region.values()), by_region
