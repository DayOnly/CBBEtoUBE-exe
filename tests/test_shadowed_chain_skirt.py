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

"""#shadowed-chain-skirt -- a chain-driven skirt losing its physics to a
higher-ranked sibling shape. See clipping log F1.

`_pick_bodytri_carriers` returns exactly ONE shape (correct for BODYTRI, which
wants a single morph carrier). The HDT-XML generator reused it as its cloth
classifier, so on a multi-shape garment only the top-ranked shape was considered
-- a skirt carrying physics chain bones was silently dropped, chain detection saw
nothing, the chainless gate returned None, and the piece shipped with NO physics.
Measured: a dress with shapes {Corset, Dress} lost its 12-bone skirt chain because
"corset" outranks "dress" in CLOTH_KEYWORDS, while sibling dresses with {Top,
Skirt} emitted physics normally.
"""
import inspect

import src.nif_convert as nc


class _FakeShape:
    def __init__(self, name, bones=(), textured=True, verts=100):
        self.name = name
        self.bone_names = list(bones)
        self.textures = {"d": "x.dds"} if textured else {}
        self.verts = [(0.0, 0.0, 0.0)] * verts
        self.bone_weights = {b: [] for b in bones}


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


SKIRT_CHAIN = ["SkirtFBone01", "SkirtFBone02", "SkirtFBone03",
               "SkirtBBone01", "SkirtBBone02", "SkirtBBone03"]


def test_candidate_helper_returns_every_cloth_shape_not_just_one():
    """The regression: the picker narrows to one shape, so the second cloth
    shape -- the one with the chain -- was never seen."""
    nif = _FakeNif([_FakeShape("Corset", ["NPC Spine [Spn0]"]),
                    _FakeShape("Dress", SKIRT_CHAIN)])
    names = {s.name for s in nc._cloth_candidate_shapes(nif)}
    assert names == {"Corset", "Dress"}
    # ...whereas the BODYTRI picker still yields exactly one (unchanged contract)
    assert len(nc._pick_bodytri_carriers(nif, exclude_body=True)) == 1


def test_candidate_helper_excludes_body_and_untextured():
    nif = _FakeNif([_FakeShape("BaseShape", ["NPC Spine [Spn0]"]),
                    _FakeShape("VirtualBody", []),
                    _FakeShape("Naked", [], textured=False),
                    _FakeShape("Skirt", SKIRT_CHAIN)])
    assert {s.name for s in nc._cloth_candidate_shapes(nif)} == {"Skirt"}


def test_generator_adds_back_only_chain_carrying_shapes():
    """Deliberately narrow: a shadowed shape is restored ONLY if it carries real
    physics chains. Adding chainless shapes would invent soft-body for rigid
    pieces -- the #fur-auto-smp / #chainless-cloth-only explosion class."""
    src = inspect.getsource(nc._generate_hdt_xml_for_dst)
    assert "_cloth_candidate_shapes" in src
    assert "detect_physics_chains" in src
    i = src.index("_cloth_candidate_shapes")
    window = src[i:i + 400]
    assert "detect_physics_chains" in window, (
        "the add-back loop must gate on detected chains, not add every shape")


def test_chain_skirt_physics_is_off_by_default():
    """Shipped ON once and a common-clothes dress's skirt COLLAPSED in game --
    every structural check passed (all 12 chain bones present as nodes, all
    referenced by the XML, parenting identical to sibling dresses that emit fine),
    so the generated collision-only XML is not reliable on an arbitrary chain and
    we cannot predict which. A collapse is worse than the clipping it fixes."""
    import src.nif_convert as nc_
    assert nc_.CHAIN_SKIRT_PHYSICS is False
    src = inspect.getsource(nc_._generate_hdt_xml_for_dst)
    assert "if CHAIN_SKIRT_PHYSICS:" in src, (
        "the shadowed-chain add-back must be gated -- ungated it invents physics "
        "that collapses")


def test_chain_skirt_physics_env_opt_in(monkeypatch):
    import importlib
    import src.nif_convert as nc_
    monkeypatch.setenv("CBBE2UBE_CHAIN_SKIRT_PHYSICS", "1")
    try:
        assert importlib.reload(nc_).CHAIN_SKIRT_PHYSICS is True
    finally:
        monkeypatch.delenv("CBBE2UBE_CHAIN_SKIRT_PHYSICS", raising=False)
        importlib.reload(nc_)


def test_inert_chain_allows_leg_motion_match():
    """#inert-chain-leg-motion. A chain only matters if something DRIVES it, which
    means an XML. With no physics XML the chain bones are inert and the garment is
    plain skinning, so the leg-motion match must be allowed to track the leg --
    otherwise the leg walks through a skirt that never moves. Measured on the
    kinematic dress: 113 -> 37 newly-exposed verts."""
    src = inspect.getsource(nc._match_limb_motion_to_body)
    assert "_piece_has_physics_xml" in src
    # Target the CALL, not the mention in the explanatory comment above it.
    i = src.index("if _shape_has_hdt_smp_rigging(")
    before = src[max(0, i - 300):i]
    assert "if _piece_has_physics_xml:" in before, (
        "the smp-rigging skip must be conditional on the piece actually having "
        "physics; unconditional, it refuses to help any inert-chain garment")
    # unknown -> conservative (keep skipping), never fail open
    assert "_piece_has_physics_xml = True" in src


def test_chainless_gate_still_present():
    """The guard that keeps the converter from inventing physics for rigid
    pieces must survive this change."""
    src = inspect.getsource(nc._generate_hdt_xml_for_dst)
    assert "chainless-softbody gate" in src
    assert "_is_unconstrained_collision_pair" in src
