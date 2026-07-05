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

"""Regression: a bone-driven HDT-SMP garment (skirt/flap) hangs from a chain of
CONSTRAINT bones that carry ZERO skin weight -- they exist only to drive the
physics, so no shape's bone list references them. The rebuilt output NIF only
carries bones some shape is skinned to, so those constraint NODES were dropped,
breaking the SMP chain -> the garment free-falls to the ground.

`_precreate_custom_bone_chains` must ALSO seed its bone set from the physics
XML's `<bone>` list so the zero-weight constraint bones get recreated at source
bind. #smp-constraint-bones (Travelling Merchant skirt, 1.0 play-test)
"""
import pytest

from tests.synthetic_nif import VERTS, TRIS, pynifly_available
import src.nif_convert as nc  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")


def _build_source_with_constraint_chain(path):
    """A skinned garment whose mesh is weighted ONLY to skeleton bones, plus a
    two-bone custom constraint chain (zero weight) parented onto the skeleton
    anchor -- the shape of a bone-driven SMP skirt."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(path))
    uvs = [(0.0, 0.0)] * len(VERTS)
    normals = [(0.0, 0.0, 1.0)] * len(VERTS)
    sh = nif.createShapeFromData("Low_Skirt", VERTS, TRIS, uvs, normals)
    sh.skin()
    for bn in ("NPC Pelvis [Pelv]", "NPC Spine [Spn0]"):
        sh.add_bone(bn)
    idt = pyn.TransformBuf()
    idt.set_identity()
    for bn in ("NPC Pelvis [Pelv]", "NPC Spine [Spn0]"):
        sh.set_skin_to_bone_xform(bn, idt)
    # ALL weight on skeleton bones -- the constraint bones stay zero-weight.
    sh.setShapeWeights("NPC Pelvis [Pelv]", [(i, 1.0) for i in range(len(VERTS))])
    # Zero-weight custom constraint chain, hung off the skeleton anchor.
    t1 = pyn.TransformBuf(); t1.set_identity(); t1.translation = (0.0, -11.0, 9.0)
    nif.add_node("CustomSkirtBone01", t1, parent="NPC Pelvis [Pelv]")
    t2 = pyn.TransformBuf(); t2.set_identity(); t2.translation = (0.0, -12.0, 48.0)
    nif.add_node("CustomSkirtBone02", t2, parent="CustomSkirtBone01")
    nif.save()
    return path


_XML = ('<?xml version="1.0"?><system>'
        '<per-triangle-shape name="Low_Skirt"/>'
        '<bone name="NPC Pelvis [Pelv]"/>'
        '<bone name="CustomSkirtBone01"/>'
        '<bone name="CustomSkirtBone02"/>'
        '</system>')


def _run_precreate(tmp_path, xml_text):
    pyn = nc._pynifly()
    src = _build_source_with_constraint_chain(tmp_path / "src.nif")
    snf = pyn.NifFile(filepath=str(src))
    dst = pyn.NifFile()
    dst.initialize("SKYRIMSE", str(tmp_path / "dst.nif"))
    # The surviving skeleton anchor is already in the rebuilt NIF (it's weighted).
    idt = pyn.TransformBuf(); idt.set_identity()
    dst.add_node("NPC Pelvis [Pelv]", idt, parent=None)
    shp = snf.shapes[0]
    nc._precreate_custom_bone_chains(dst, snf, list(shp.bone_names))
    dst.save()
    return set(pyn.NifFile(filepath=str(tmp_path / "dst.nif")).nodes.keys())


def test_zeroweight_xml_constraint_bones_are_recreated(tmp_path, monkeypatch):
    # With the physics XML naming them, the zero-weight chain must be rebuilt.
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text",
                        lambda p, nif=None: _XML)
    names = _run_precreate(tmp_path, _XML)
    assert "CustomSkirtBone01" in names, "SMP constraint bone dropped (skirt falls)"
    assert "CustomSkirtBone02" in names, "SMP constraint chain incomplete"


def test_constraint_chain_parent_link_survives_save(tmp_path, monkeypatch):
    # SMP walks the NIF hierarchy: the chain must reload parented, not flat.
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text",
                        lambda p, nif=None: _XML)
    pyn = nc._pynifly()
    _run_precreate(tmp_path, _XML)
    rl = pyn.NifFile(filepath=str(tmp_path / "dst.nif"))
    b1 = rl.nodes.get("CustomSkirtBone01")
    assert b1 is not None and b1.parent is not None
    assert b1.parent.name == "NPC Pelvis [Pelv]"


def test_no_xml_leaves_zeroweight_bones_dropped(tmp_path, monkeypatch):
    # Control: without a physics XML there's nothing to preserve them by, so the
    # zero-weight bones are (correctly) absent -- proving the XML seed is the
    # thing that saves the chain, not some incidental copy.
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text",
                        lambda p, nif=None: None)
    names = _run_precreate(tmp_path, None)
    assert "CustomSkirtBone01" not in names
    assert "CustomSkirtBone02" not in names
