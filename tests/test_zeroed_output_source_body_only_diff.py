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

"""#zos-body-only-diff: #zeroed-output-source takes the verified zeroed build
when it differs from today's source ONLY in body shapes.

The defect (2026-09-29): rule 5 read "the same shapes, every one", so a build
that bundled the 3BA body against a source that ships a small skin stub kept
the source. On one modlist's 1.5 run that held back 105 planned pieces, 104 of
which reached the output from the mod's loose mesh; over 72 such torso pieces
the loose mesh sits a median 0.54u (breast) / 1.46u (belly) further off the
zeroed CBBE body than the build (the author's preset baked in), and the copy
path carried that into the UBE output -- inflated in game. 53 of the 105
differed from the build only in body shapes: a placeholder underwear body, a
`*_skin` / `*Body*` / chest patch of 46-870 verts on a body-skin diffuse, or a
VirtualBody / VirtualGround proxy. The 2026-09-22 measurement behind the
original rule (four pieces of one armour overhaul: the body-swap path moved
up to 4.2u and exposed 2-10% more) stands for a GARMENT shape that differs:
that still keeps the source. And every keep is now logged by name.

The selection fixture is tests/test_zeroed_output_source.py's; the shapes and
diffuse of each side are stubbed, as there.
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import zeroed_body as zb  # noqa: E402
from tests.test_zeroed_output_source import (  # noqa: E402
    BASE_MOD, OUT_MOD, STEM, Selection)

BODY_TEX = "textures\\actors\\character\\female\\femalebody_1.dds"
CLOTH_TEX = "textures\\armor\\test\\cuirass.dds"
DIFFER = "the build's shapes differ from today's source"

GARMENT = {"Cuirass": 4, "Pants": 4}


def _shapes(counts, fill):
    return {n: np.full((k, 3), fill, dtype=np.float64) for n, k in counts.items()}


def _pair(root, monkeypatch, *, source, build, src_tex=None, build_tex=None):
    """Today's source and the verified build, as {shape: vertex count}; the
    source geometry is off the build's, so it never 'already is' the build.
    `src_tex` / `build_tex`: diffuse per shape ("" = no shader)."""
    sel = Selection(root, monkeypatch)
    src, bld = _shapes(source, 1.0), _shapes(build, 0.0)
    monkeypatch.setattr(zb, "zeroed_garment", lambda stem, files, dirs=None:
                        zb.ZeroedGarment("Test Armor", 0.0, {w: bld for w in ("_0", "_1")}))
    monkeypatch.setattr(zb, "_nif_shapes", lambda p: dict(src))
    tex = {"src": dict(src_tex or {}), "build": dict(build_tex or {})}
    monkeypatch.setattr(zb, "_nif_diffuse", lambda p: tex[
        "build" if OUT_MOD.lower() in str(p).lower() else "src"])
    return sel


def _owner(sel):
    return set(Selection.owners(sel.index()).values())


# --- (a) a skin stub against the build's 3BA cut ------------------------------------

def test_a_source_skin_stub_against_the_builds_3ba_cut_moves_to_the_build(tmp_path, monkeypatch, capsys):
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "FemaleUnderwearBody:0": 3},
                build={**GARMENT, "3BA": 6},
                src_tex={"FemaleUnderwearBody:0": BODY_TEX, "Cuirass": CLOTH_TEX},
                build_tex={"3BA": BODY_TEX, "Cuirass": CLOTH_TEX})
    assert _owner(sel) == {OUT_MOD}
    err = capsys.readouterr().err
    assert (f"[zeroed-output-source] moved meshes/{STEM}: the build differs from "
            f"today's source in body shapes only -- 3BA -/6, FemaleUnderwearBody:0 3/-") in err
    rep = discovery.zeroed_output_source_report()
    assert rep["moved"] == [STEM]
    assert rep["moved_over_body_shapes"] == {STEM: "3BA -/6, FemaleUnderwearBody:0 3/-"}
    assert "1 piece(s) now converted" in err and "(1 over body-shape differences)" in err


def test_a_bespoke_stub_name_is_a_body_by_its_skin_diffuse(tmp_path, monkeypatch):
    """`robes_skin`, `Female_chest`, `EbonyBody`: nothing in the name the
    converter knows; the diffuse says skin."""
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "robes_skin": 46, "Female_chest": 120},
                build={**GARMENT, "CBBE": 870},
                src_tex={"robes_skin": BODY_TEX, "Female_chest": BODY_TEX},
                build_tex={"CBBE": BODY_TEX})
    assert _owner(sel) == {OUT_MOD}


# --- (b) a garment shape differing keeps the source, the old reason -----------------

def test_a_garment_shape_that_differs_keeps_todays_source(tmp_path, monkeypatch, capsys):
    sel = _pair(tmp_path, monkeypatch,
                source={"Cuirass": 5, "Pants": 4, "FemaleUnderwearBody:0": 3},
                build={**GARMENT, "3BA": 6},
                src_tex={"FemaleUnderwearBody:0": BODY_TEX, "Cuirass": CLOTH_TEX},
                build_tex={"3BA": BODY_TEX, "Cuirass": CLOTH_TEX})
    assert _owner(sel) == {BASE_MOD}
    err = capsys.readouterr().err
    assert f"[zeroed-output-source] kept meshes/{STEM}: {DIFFER} -- Cuirass 5/4" in err
    assert f"{DIFFER}: 1" in err


def test_a_garment_shape_on_one_side_only_keeps_todays_source(tmp_path, monkeypatch):
    """A `Greaves` the build has and the source lacks is another design, whatever
    the body shapes do."""
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "FemaleUnderwearBody:0": 3},
                build={**GARMENT, "3BA": 6, "Greaves": 9},
                src_tex={"FemaleUnderwearBody:0": BODY_TEX},
                build_tex={"3BA": BODY_TEX, "Greaves": CLOTH_TEX})
    assert _owner(sel) == {BASE_MOD}
    rep = discovery.zeroed_output_source_report()
    assert rep["kept"] == {DIFFER: [STEM]}
    assert rep["kept_shape_diffs"] == {STEM: "Greaves -/9"}


def test_a_stub_on_a_cloth_diffuse_is_not_a_body(tmp_path, monkeypatch):
    """The #body-name-prefix case: a CUIRASS an author named like the
    placeholder body, on armour diffuse. Its diffuse decides, as the swap's own
    detector decides -- kept."""
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "FemaleUnderwearBody:0": 300},
                build={**GARMENT, "3BA": 6},
                src_tex={"FemaleUnderwearBody:0": CLOTH_TEX},
                build_tex={"3BA": BODY_TEX})
    assert _owner(sel) == {BASE_MOD}


def test_an_unreadable_diffuse_keeps_the_source_with_the_old_reason(tmp_path, monkeypatch, capsys):
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "skin": 46},
                build={**GARMENT, "3BA": 6})

    def boom(p):
        raise RuntimeError("cannot open")
    monkeypatch.setattr(zb, "_nif_diffuse", boom)
    assert _owner(sel) == {BASE_MOD}
    assert f"kept meshes/{STEM}: {DIFFER} -- 3BA -/6, skin 46/-" in capsys.readouterr().err


# --- (c) a proxy differing alone -----------------------------------------------------

def test_a_virtualbody_proxy_count_difference_alone_moves_to_the_build(tmp_path, monkeypatch):
    sel = _pair(tmp_path, monkeypatch,
                source={**GARMENT, "VirtualBody": 10},
                build={**GARMENT, "VirtualBody": 12, "VirtualGround": 4})
    assert _owner(sel) == {OUT_MOD}
    assert discovery.zeroed_output_source_report()["moved_over_body_shapes"] == {
        STEM: "VirtualBody 10/12, VirtualGround -/4"}


# --- (d) every keep is named -----------------------------------------------------------

def test_every_keep_is_logged_by_name_with_its_reason(tmp_path, monkeypatch, capsys):
    sel = Selection(tmp_path, monkeypatch, verified=False)
    assert _owner(sel) == {BASE_MOD}
    err = capsys.readouterr().err
    assert (f"[zeroed-output-source] kept meshes/{STEM}: the output is not a "
            f"verified zeroed build") in err
    assert discovery.zeroed_output_source_report()["kept"] == {
        "the output is not a verified zeroed build": [STEM]}


def test_the_report_is_a_copy_and_empty_before_a_pass(tmp_path, monkeypatch):
    discovery._ZOS_LAST.clear()
    assert discovery.zeroed_output_source_report() == {}
    sel = _pair(tmp_path, monkeypatch, source=GARMENT, build=GARMENT)
    assert _owner(sel) == {OUT_MOD}
    rep = discovery.zeroed_output_source_report()
    rep["moved"].append("x")
    assert discovery.zeroed_output_source_report()["moved"] == [STEM]


# --- the classifier is the converter's own ------------------------------------------------

def test_body_type_is_decided_by_the_converters_own_classifiers():
    body = discovery._zos_body_type
    assert body("3BA", []) and body("3BA_Vagina", []) and body("3BA Ref", [])
    assert body("VirtualBody", []) and body("VirtualGround", []) and body("BaseShape", [])
    assert body("FemaleUnderwearBody:0", [])            # no diffuse: the placeholder prefix
    assert not body("FemaleUnderwearBody:0", [CLOTH_TEX])
    for n in ("CBBE", "Fem", "FemBody", "robes_skin", "Female_chest", "EbonyBody",
              "BodyStorm", "Clipped Body_outfit", "Labia", "Panty"):
        assert body(n, [BODY_TEX]), n
    for n in ("Greaves", "Torso", "d", "Cuirass", "Top", "Corset", "Tassets", "Stabilizer"):
        assert not body(n, [CLOTH_TEX]), n
        assert not body(n, []), n
    assert not body("skin", [BODY_TEX, CLOTH_TEX])     # every side that ships it must say skin
