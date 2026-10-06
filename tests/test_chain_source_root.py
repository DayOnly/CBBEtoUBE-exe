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

"""#chain-source-root -- a source root named after its file is the written root.

A source NIF whose root node is called `BodyM_0.nif` (and its `_1` partner
`BodyM_1.nif`) had that root recreated by `_precreate_custom_bone_chains` as an
ordinary node, one per weight under two different names, so the two weight files
carried different node sets (#33). Found on a real cuirass in a full conversion;
the written chain kept every position and parent, only the stray node went.
"""
import pytest

from src import nif_convert as nc
from src import nif_convert_physics as ph
from tests.synthetic_nif import pynifly_available

pytestmark = pytest.mark.skipif(not pynifly_available(), reason="needs pynifly")

PELVIS = "NPC Pelvis [Pelv]"


def _src(tmp_path, root_name):
    """A source: a skinned shape on one skirt bone hanging off the root, plus a
    pelvis, saved and read back from disk."""
    pyn = nc._pynifly()
    p = tmp_path / "src_0.nif"
    nf = pyn.NifFile()
    nf.initialize("SKYRIMSE", str(p), root_name=root_name)
    sh = nf.createShapeFromData("Skirt", [(0, 0, 10), (1, 0, 10), (0, 1, 10)], [(0, 1, 2)],
                                [(0, 0)] * 3, [(0, 0, 1)] * 3)
    idt = pyn.TransformBuf()
    idt.set_identity()
    nf.add_node(PELVIS, idt, parent=None)
    low = pyn.TransformBuf()
    low.set_identity()
    low.translation = (0.0, 0.0, 10.0)                # under the waist band
    nf.add_node("SkirtBone01", low, parent=None)
    sh.skin()
    sh.add_bone("SkirtBone01")
    sh.set_skin_to_bone_xform("SkirtBone01", idt)
    sh.setShapeWeights("SkirtBone01", [(0, 1.0), (1, 1.0), (2, 1.0)])
    nf.save()
    return pyn.NifFile(filepath=str(p))


def _dst(tmp_path):
    pyn = nc._pynifly()
    d = pyn.NifFile()
    d.initialize("SKYRIMSE", str(tmp_path / "dst_0.nif"))
    return d


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(nc, "CHAIN_SOURCE_ROOT", True)


def test_a_file_named_root_is_never_recreated(tmp_path):
    src = _src(tmp_path, "BodyM_0.nif")
    dst = _dst(tmp_path)
    ph._precreate_custom_bone_chains(dst, src, ["SkirtBone01"])
    assert "BodyM_0.nif" not in dst.nodes
    assert "SkirtBone01" in dst.nodes


def test_with_the_switch_off_the_root_is_recreated_as_before(tmp_path, monkeypatch):
    """The control: the stray node the fix removes."""
    monkeypatch.setattr(nc, "CHAIN_SOURCE_ROOT", False)
    src = _src(tmp_path, "BodyM_0.nif")
    dst = _dst(tmp_path)
    ph._precreate_custom_bone_chains(dst, src, ["SkirtBone01"])
    assert "BodyM_0.nif" in dst.nodes


class _Xf:
    def __init__(self, t=(0.0, 0.0, 0.0), scale=1.0):
        self.translation = t
        self.scale = scale
        self.rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class _Root:
    def __init__(self, name, xf):
        self.name = name
        self.transform = xf


class _Nif:
    def __init__(self, root=None, nodes=()):
        self.rootNode = root
        self.nodes = {n: object() for n in nodes}


def test_the_root_is_the_written_root_only_when_its_transform_is_identity(tmp_path):
    """A root that moves its children keeps the old behaviour: their local
    transforms are relative to it."""
    dst = _Nif(nodes=("Scene Root",))
    assert ph._chain_source_root(_Nif(_Root("BodyM_0.nif", _Xf((0.0, 0.0, 5.0)))), dst) is None
    assert ph._chain_source_root(_Nif(_Root("BodyM_0.nif", _Xf(scale=2.0))), dst) is None
    assert ph._chain_source_root(_Nif(_Root("BodyM_0.nif", _Xf())), dst) == "BodyM_0.nif"


def test_a_scene_root_source_is_left_alone(tmp_path):
    src = _src(tmp_path, "Scene Root")
    assert ph._chain_source_root(src, _dst(tmp_path)) is None


def test_a_file_named_identity_root_is_recognised(tmp_path):
    src = _src(tmp_path, "BodyM_0.nif")
    assert ph._chain_source_root(src, _dst(tmp_path)) == "BodyM_0.nif"


def test_the_switch_is_on_by_default():
    from tests import _converter_sources as _cs
    assert 'not _flag("CBBE2UBE_NO_CHAIN_SOURCE_ROOT", False)' in _cs.whole_text()
