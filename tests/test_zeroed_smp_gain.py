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

r"""#zeroed-smp-gain -- a verified zeroed BodySlide build brings its SMP physics.

THE CASE (2026-09-25). #zeroed-output-source refused every build whose physics
differed from today's source. For a set of vanilla-armour pieces today's source
is a static prebuilt mesh (the within-tier body match swapped out the SMP loose
mesh for its bespoke body), and the user's zeroed build of the SMP design --
verified vertex for vertex, carrying the zeroed body -- was refused for its
physics alone: CBBE wearers got a skirt with physics, UBE wearers a static one.
Now a physics GAIN is taken when both sides go body-swap, the build's XML
resolves, parses and holds a constraint anywhere in its tree, and nothing the
XML names is a stripped body its collision-proxy re-import would bring back.
`CBBE2UBE_NO_ZEROED_SMP_GAIN=1` keeps such pieces on today's source.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import nif_io  # noqa: E402
from tests.test_zeroed_output_source import (  # noqa: E402
    BASE_MOD, OUT_MOD, STEM, Selection, _touch)

OFF = "CBBE2UBE_NO_ZEROED_SMP_GAIN"
BODY_TEX = "textures\\actors\\character\\female\\femalebody_1.dds"
CLOTH_TEX = "textures\\armor\\test\\skirt.dds"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)


class _Shape:
    """What the converter's body detectors read off a shape."""

    def __init__(self, name, nverts=4, nbones=2, zspan=10.0, diffuse=CLOTH_TEX,
                 bones=None):
        self.name = name
        self.verts = [(0.0, 0.0, 0.0)] + [(0.0, 0.0, float(zspan))] * (nverts - 1)
        self.bone_names = list(bones) if bones else [f"NPC Bone{i}" for i in range(nbones)]
        self.textures = {"Diffuse": diffuse}
        self._backing = None


def _named_body():
    return _Shape("3BA")                      # swapped by name


def _skin_body(name="Body", bones=None):
    """A body-skin body the detector finds by texture and size, not by name,
    skinned to skeleton bones -- one of them a bone the XML names."""
    bones = bones or ["NPC Pelvis [Pelv]"] + [f"NPC Bone{i}" for i in range(21)]
    return _Shape(name, nverts=600, zspan=103.0, diffuse=BODY_TEX, bones=bones)


def _xml(body='<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>',
         shapes=('Skirt',)):
    decl = "".join(f'<per-vertex-shape name="{n}"><margin>1</margin></per-vertex-shape>'
                   for n in shapes)
    return (f'<?xml version="1.0" encoding="UTF-8"?><system>'
            f'<bone name="Skirt 01"/>{decl}{body}</system>').encode()


def _verdict(today=None, build=None, xml=None):
    today = [_named_body(), _Shape("Cuirass")] if today is None else today
    build = [_named_body(), _Shape("Skirt")] if build is None else build
    return discovery._smp_gain_verdict(today, build, _xml() if xml is None else xml)


# --- the rules, one weight ---------------------------------------------------------

def test_a_constrained_build_on_the_body_swap_path_brings_its_physics():
    assert _verdict() is None


def test_a_constraint_inside_a_constraint_group_counts():
    grouped = _xml(body='<constraint-group><generic-constraint bodyA="Skirt 01" '
                        'bodyB="NPC Pelvis [Pelv]"/></constraint-group>')
    assert _verdict(xml=grouped) is None


@pytest.mark.parametrize("tag", ["stiffspring-constraint", "conetwist-constraint"])
def test_the_other_constraint_kinds_count(tag):
    assert _verdict(xml=_xml(body=f'<{tag} bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>')) is None


def test_an_xml_without_a_constraint_is_refused():
    why = _verdict(xml=_xml(body="<constraint-group></constraint-group>"))
    assert why == "its physics XML has no constraint"


def test_an_unresolved_xml_is_refused():
    assert discovery._smp_gain_verdict(
        [_named_body()], [_named_body(), _Shape("Skirt")], None) == \
        "its physics XML does not resolve"


def test_an_xml_that_does_not_parse_is_refused():
    assert _verdict(xml=b"<system><bone name='x'></system") == "its physics XML does not parse"


def test_a_source_the_converter_copies_keeps_it():
    """Today's source with no body goes down the copy path; the build's body
    would move it to body-swap -- the pass-chain change rule 5 guards."""
    assert _verdict(today=[_Shape("Cuirass")]) == \
        "today's source has no body the converter swaps"


def test_a_build_the_converter_copies_keeps_todays_source():
    assert _verdict(build=[_Shape("Skirt")]) == "the build has no body the converter swaps"


def test_a_body_skin_shape_the_swap_misses_is_refused():
    """A body shape too thinly boned for the detector would ship as cloth."""
    loose = _Shape("CBBE", nverts=1333, nbones=10, zspan=60.0, diffuse=BODY_TEX)
    assert _verdict(build=[_named_body(), loose, _Shape("Skirt")]) == \
        "the build's body 'CBBE' would ship as cloth"


def test_a_small_skin_slice_is_not_a_body():
    hands = _Shape("Hands", nverts=46, nbones=10, zspan=5.0, diffuse=BODY_TEX)
    assert _verdict(build=[_named_body(), hands, _Shape("Skirt")]) is None


def test_an_xml_named_body_the_reimport_brings_back_is_refused():
    """The XML registers the body as a collider under a name the re-import's
    body-name filter does not know: it would come back as a hidden second body."""
    why = _verdict(build=[_skin_body("Body"), _Shape("Skirt")],
                   xml=_xml(shapes=("Skirt", "Body")))
    assert why == "its physics XML would bring back the stripped body 'Body'"


def test_an_xml_named_body_the_reimport_skips_is_taken():
    """A registered '3BA' body is dropped and never re-imported: the cloth
    collides with the injected UBE body instead."""
    assert _verdict(build=[_named_body(), _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "3BA"))) is None


def test_the_xml_name_is_matched_case_for_case():
    """The re-import looks the name up case for case, so an XML 'Body' finds
    nothing in a build whose body is 'body'."""
    assert _verdict(build=[_skin_body("body"), _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "Body"))) is None


def test_a_stripped_body_that_carries_a_driven_bone_is_refused():
    """The re-import also brings back a shape that carries a physics bone the
    XML drives, when nothing else carries it."""
    body = _skin_body("Body", bones=["NPC Pelvis [Pelv]", "Skirt 01"] +
                      [f"NPC Bone{i}" for i in range(20)])
    assert _verdict(build=[body, _Shape("Skirt")]) == \
        "its physics XML would bring back the stripped body 'Body'"


def test_a_stripped_body_on_skeleton_bones_only_is_taken():
    """Skeleton bones resolve on the actor, so the re-import never needs a
    shape for them -- even one the XML names ('NPC Pelvis [Pelv]' here)."""
    assert _verdict(build=[_skin_body("Body"), _Shape("Skirt")]) is None


def test_an_xml_named_exposed_skin_slice_is_refused():
    """A body-skin slice big enough for the exposed-skin swap is stripped too."""
    slice_ = _Shape("Cleavage", nverts=320, zspan=20.0, diffuse=BODY_TEX)
    why = _verdict(build=[_named_body(), slice_, _Shape("Skirt")],
                   xml=_xml(shapes=("Skirt", "Cleavage")))
    assert why == "its physics XML would bring back the stripped body 'Cleavage'"


def test_an_xml_named_skin_decal_is_taken():
    """Below the exposed-skin floor a body-skin shape stays in the output, so
    the re-import has nothing to bring back."""
    decal = _Shape("Hands", nverts=46, zspan=5.0, diffuse=BODY_TEX)
    assert _verdict(build=[_named_body(), decal, _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "Hands"))) is None


# --- the selection -------------------------------------------------------------------

def _gain_world(tmp_path, monkeypatch, *, xml=None, build_marker=(True, True),
                today_marker=(False, False)):
    sel = Selection(tmp_path, monkeypatch, today="other-shapes")
    mark = b"nif HDT Skinned Mesh Physics Object meshes\\armor\\test\\skirt.xml"
    for w, on in zip(("_0", "_1"), build_marker):
        _touch(sel.mods / OUT_MOD / "meshes" / f"{STEM}{w}.nif", mark if on else b"nif")
    for w, on in zip(("_0", "_1"), today_marker):
        _touch(sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif", mark if on else b"nif")

    def fake_nif(p):
        own = OUT_MOD in str(p)
        return nif_io.Nif(path=Path(p), shapes=[_named_body(),
                                                _Shape("Skirt" if own else "Cuirass")])

    xml = xml or {}
    monkeypatch.setattr(discovery, "_gain_nif", fake_nif)
    monkeypatch.setattr(discovery, "_gain_xml",
                        lambda p: xml.get(str(p)[-6:-4], _xml()))
    return sel


def test_the_build_with_physics_is_taken_by_default(tmp_path, monkeypatch, capsys):
    sel = _gain_world(tmp_path, monkeypatch)
    assert set(Selection.owners(sel.index()).values()) == {OUT_MOD}
    assert "(1 with its SMP physics)" in capsys.readouterr().err


def test_switched_off_the_piece_keeps_todays_source(tmp_path, monkeypatch, capsys):
    sel = _gain_world(tmp_path, monkeypatch)
    monkeypatch.setenv(OFF, "1")
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert "its physics would change: 1" in capsys.readouterr().err


def test_a_physics_loss_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, build_marker=(False, False),
                      today_marker=(True, True))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_build_with_physics_at_one_weight_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, build_marker=(False, True))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_source_with_physics_at_one_weight_keeps_it(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, today_marker=(True, False))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_refusal_at_one_weight_keeps_the_pair(tmp_path, monkeypatch, capsys):
    sel = _gain_world(tmp_path, monkeypatch, xml={"_0": _xml(body="")})
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert ("it would gain physics, but its physics XML has no constraint: 1"
            in capsys.readouterr().err)


def test_an_unreadable_mesh_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch)

    def broken(p):
        raise RuntimeError("cannot open")

    monkeypatch.setattr(discovery, "_gain_nif", broken)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


# --- the XML the build points at -----------------------------------------------------

def test_the_xml_is_the_one_the_nifs_own_pointer_names(tmp_path):
    from tests.synthetic_nif import build_shape_nif, pynifly_available
    if not pynifly_available():
        pytest.skip("pynifly unavailable")
    from src import nif_convert as nc
    pyn = nc._pynifly()
    mod = tmp_path / "mods" / OUT_MOD
    nif_path = build_shape_nif(_touch(mod / "meshes" / "armor" / "test" / "skirt_1.nif"))
    nif = pyn.NifFile(filepath=str(nif_path))
    pyn.NiStringExtraData.New(nif, name="HDT Skinned Mesh Physics Object",
                              string_value="meshes\\armor\\test\\physics\\skirt.xml",
                              parent=nif.rootNode)
    nif.save()
    _touch(mod / "meshes" / "armor" / "test" / "physics" / "skirt.xml", _xml())
    _touch(mod / "meshes" / "armor" / "test" / "skirt.xml", b"<other/>")
    assert discovery._gain_xml(nif_path) == _xml()
