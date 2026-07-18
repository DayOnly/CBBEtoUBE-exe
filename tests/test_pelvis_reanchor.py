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

"""Regression: a bone-driven garment chain whose top bone hangs off a NIF-ROOT
node ("BodyM_1.nif") instead of a skeleton bone tracks the actor ROOT (feet) at
runtime, so the waist garment disconnects. `_reanchor_nif_root_chains` re-parents
that root onto NPC Pelvis while PRESERVING its global position (descendant bind +
STB unchanged). Confirmed in-game (a bone-driven fur-skirt armour). #pelvis-reanchor
"""
import numpy as np
import pytest

from tests.synthetic_nif import pynifly_available
import src.nif_convert as nc  # noqa: E402

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")

PELVIS_Z = 68.9


def _xf(pyn, t=(0.0, 0.0, 0.0)):
    b = pyn.TransformBuf()
    b.set_identity()
    b.translation = (float(t[0]), float(t[1]), float(t[2]))
    return b


def _rig(tmp_path, *, root="BodyM_1.nif", skirt_z=74.6, top_parent=None):
    """A NIF with NPC Pelvis (z=68.9), a `root` node at origin, and one garment
    bone at height `skirt_z` parented to `top_parent or root`."""
    pyn = nc._pynifly()
    nif = pyn.NifFile()
    nif.initialize("SKYRIMSE", str(tmp_path / "rig.nif"))
    nif.add_node("NPC Pelvis [Pelv]", _xf(pyn, (0, 0, PELVIS_Z)), parent=None)
    nif.add_node(root, _xf(pyn), parent=None)
    nif.add_node("GarmentBone01", _xf(pyn, (0, 11.9, skirt_z)),
                 parent=top_parent or root)
    nif.save()
    return pyn.NifFile(filepath=str(tmp_path / "rig.nif"))


def _chain(sn, root, bone="GarmentBone01"):
    c = {bone: (sn[bone].transform, root)}
    if root in sn:
        c[root] = (sn[root].transform, None)
    return c


def test_nif_root_waist_chain_reanchored_to_pelvis(tmp_path):
    # The garment BONE (parent = nif-root) is lifted onto Pelvis; the root node
    # itself (Pelvis's own ancestor) is left alone.
    nif = _rig(tmp_path)
    sn = nif.nodes
    chain = _chain(sn, "BodyM_1.nif")
    anchors: set = set()
    n = nc._reanchor_nif_root_chains(chain, anchors, sn)
    assert n == 1
    xf, par = chain["GarmentBone01"]
    assert par == "NPC Pelvis [Pelv]"
    assert "NPC Pelvis [Pelv]" in anchors
    assert chain["BodyM_1.nif"][1] is None            # root untouched
    # New local under Pelvis keeps the bone's GLOBAL z (74.6): local z = 74.6 - 68.9.
    assert abs(float(np.asarray(xf.translation)[2]) - (74.6 - PELVIS_Z)) < 0.2


def test_high_chain_not_reanchored(tmp_path):
    # A bone hanging off a nif-root but at HEAD height is out of the waist band.
    nif = _rig(tmp_path, skirt_z=140.0)
    sn = nif.nodes
    chain = _chain(sn, "BodyM_1.nif")
    anchors: set = set()
    assert nc._reanchor_nif_root_chains(chain, anchors, sn) == 0
    assert chain["GarmentBone01"][1] == "BodyM_1.nif"   # untouched


def test_skeleton_anchored_chain_untouched(tmp_path):
    # Bone parented to a real skeleton bone -> parent not a nif-root -> left alone.
    nif = _rig(tmp_path, root="NPC Spine2 [Spn2]", skirt_z=74.6)
    sn = nif.nodes
    chain = {"GarmentBone01": (sn["GarmentBone01"].transform, "NPC Spine2 [Spn2]")}
    anchors: set = set()
    assert nc._reanchor_nif_root_chains(chain, anchors, sn) == 0
    assert chain["GarmentBone01"][1] == "NPC Spine2 [Spn2]"


def test_disabled_by_env(tmp_path, monkeypatch):
    # The precreate call site honours CBBE2UBE_NO_PELVIS_REANCHOR; the helper still
    # works when called directly, but the flag must read as off.
    monkeypatch.setenv("CBBE2UBE_NO_PELVIS_REANCHOR", "1")
    import importlib
    mod = importlib.reload(nc)
    assert mod.PELVIS_REANCHOR_CHAINS is False
    monkeypatch.delenv("CBBE2UBE_NO_PELVIS_REANCHOR", raising=False)
    importlib.reload(mod)
