"""Angle-B softcloth-vs-antipoke split: _shape_bust_is_softbody_driven.

A per-vertex soft-body always counts as bust-driven (whole shape simulated). An
HDT-rigged robe whose chains drive only the SKIRT has a RIGID body-skinned bust
(low breast-band chain-weight fraction) -> must NOT be treated as bust-driven, so
it goes through the normal anti-poke instead of the softcloth inflation that
balloons a rigid bust (a loose monk robe: 60% chain bones overall but bust
chain-fraction 0.01, +3.2u). #softcloth-bust-driven-gate
"""
import numpy as np

from src import nif_convert as nc


class _Shape:
    def __init__(self, name, verts, bone_weights):
        self.name = name
        self.verts = [tuple(map(float, v)) for v in verts]
        self.bone_weights = bone_weights


def _bust_verts(n=40):
    # verts inside the breast band: z 92-100, front y>4, |x|<13
    xs = np.linspace(-8, 8, n)
    return [(float(x), 7.0, 95.0) for x in xs]


BODY = {"NPC Spine2 [Spn2]", "L Breast01", "R Breast01"}


def test_per_vertex_softbody_always_driven():
    sh = _Shape("Stocking", _bust_verts(), {"NPC Spine2 [Spn2]": [(i, 1.0) for i in range(40)]})
    assert nc._shape_bust_is_softbody_driven(sh, BODY, {"Stocking"}) is True


def test_rigid_bust_body_skinned_not_driven():
    # bust fully weighted to a BODY bone -> chain fraction 0 -> anti-poke, not softcloth
    sh = _Shape("Torso", _bust_verts(),
                {"NPC Spine2 [Spn2]": [(i, 1.0) for i in range(40)]})
    assert nc._shape_bust_is_softbody_driven(sh, BODY, set()) is False


def test_chain_driven_bust_is_driven():
    # bust weighted to a CHAIN bone (not in body) above threshold -> softcloth
    sh = _Shape("Robe", _bust_verts(),
                {"Cloth 3": [(i, 1.0) for i in range(40)]})
    assert nc._shape_bust_is_softbody_driven(sh, BODY, set()) is True


def test_mostly_body_small_chain_below_threshold_not_driven():
    # 10% chain at the bust (< 0.20 default) -> rigid -> anti-poke (the loose-robe case)
    bw = {"NPC Spine2 [Spn2]": [(i, 0.9) for i in range(40)],
          "SkirtChain_04": [(i, 0.1) for i in range(40)]}
    sh = _Shape("Torso", _bust_verts(), bw)
    assert nc._shape_bust_is_softbody_driven(sh, BODY, set()) is False


def test_no_bust_keeps_softcloth():
    # A skirt/no-bust shape has no bust to balloon -> keep softcloth (True), so its
    # BUTT-band handling is preserved (the gate only strips a real RIGID bust).
    sh = _Shape("Skirt", [(0.0, 7.0, 95.0)] * 3, {"Cloth 3": [(0, 1.0)]})
    assert nc._shape_bust_is_softbody_driven(sh, BODY, set()) is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
