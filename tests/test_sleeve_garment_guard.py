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

"""`#sleeve-garment-not-extremity` -- a long-sleeved torso garment is not a
gauntlet.

THE DEFECT. A robe whose cuffs reach the hands carries `NPC L/R Hand` and
clears the extremity vert-cluster test exactly as a gauntlet does. It is then
routed down the rigid hand/foot branch, which `continue`s before the cloth
pass, so the piece never gets conform, panel-rigidity or anti-poke. Measured on
the worst chest-clipping piece in the pack (`dbmhighelfleatherdusterf`): its
whole stage chain is entry -> warp_hf -> inflate_hf -> shipped, and it ships at
8.899% bind clip over the bust band.

THIS IS THE ARM SIBLING of `#leg-garment-not-extremity`, which exists for the
identical reason on legs ("pants whose cuffs overlap the feet clear it on the
same fraction a real boot does") and discriminates on PELVIS mass, because a
boot never reaches the pelvis. A gauntlet never reaches the SPINE.

WHAT THESE TESTS HOLD, worst-consequence first:
  * a REAL GAUNTLET is never reclassified. Reclassifying handwear is the
    expensive direction -- `#boot-pelvis-only` records a classifier change here
    regressing 58 footwear shapes once already;
  * `hand > 0` is required, because every bad candidate in the measured
    population carried spine weight with NO hand weight (a knife scabbard
    handle at spine 9777 / hand 0, an embedded `3BA Ref` body);
  * the 2x margin holds, because `3BA Ref` sits at ratio 1.03 and a bare
    `spine > hand` would flip a reference body;
  * the guard is MONOTONE -- it can only ever withdraw an extremity
    classification, never add one;
  * default OFF.
"""
import numpy as np
import pytest

from src import nif_convert as nc


class _Shape:
    """Minimal stand-in: the gate reads `name`, `verts` and `bone_weights`."""

    def __init__(self, name, n, weights):
        self.name = name
        self.verts = np.zeros((n, 3), np.float32)
        # {bone: [(vert_index, weight), ...]}
        self.bone_weights = weights
        self.bone_names = list(weights)


def _uniform(bone, n, w=1.0):
    return [(i, w) for i in range(n)]


# THE FIXTURES CARRY A VERT CLUSTER, NOT A UNIFORM WEIGHT. The gate first
# requires verts MAJORITY-controlled by an extremity bone (`ext > tot * 0.5`),
# which a uniformly-weighted shape can never satisfy -- a first cut of these
# tests used flat weights, never reached the guard at all, and "passed" for the
# wrong reason on two cases. Each shape below reproduces the measured ratio AND
# a real cuff/hand cluster, so the guard is genuinely exercised.

def _gauntlet(n=400):
    """A tube around the forearm: all hand, no spine."""
    return _Shape("Gauntlets", n, {
        "NPC L Hand [LHnd]": _uniform("h", n),
        "NPC L Forearm [LLar]": _uniform("f", n, 0.4),
    })


def _sleeved_robe(n=1220, cuff=40):
    """A torso garment whose cuffs reach the hands. Mirrors the measured
    HighElfRobe: a small hand-dominant cuff cluster on a spine-hung body,
    spine/hand ~ 11 (measured 7.69)."""
    return _Shape("HighElfRobe", n, {
        "NPC L Hand [LHnd]": [(i, 1.0) for i in range(cuff)],
        "NPC Spine1 [Spn1]": [(i, 0.36) for i in range(n)],
    })


def _scabbard(n=9777, cluster=200):
    """A knife-scabbard handle: spine mass, ZERO HAND mass. It reaches this
    gate through a FOOT bone (the gate's extremity set is hand/finger/thumb/
    foot/toe), which is why the guard weighs HAND mass specifically -- a bare
    `spine > hand` rule flips this and puts a rigid prop on the cloth path."""
    return _Shape("handle_golden", n, {
        "NPC L Foot [Lft ]": [(i, 1.0) for i in range(cluster)],
        "NPC Spine2 [Spn2]": [(i, 1.0) for i in range(n)],
    })


def _ref_body(n=31923, hands=15000):
    """An embedded reference body: a real hand cluster AND heavy spine, at the
    measured ratio 1.03. The 2x margin is the only thing keeping it out."""
    return _Shape("3BA Ref", n, {
        "NPC L Hand [LHnd]": [(i, 1.0) for i in range(hands)],
        "NPC Spine1 [Spn1]": [(i, 0.484) for i in range(n)],
    })


def test_flag_is_default_off():
    assert nc.SLEEVE_GARMENT_NOT_EXTREMITY is False


def test_real_gauntlet_is_never_reclassified(monkeypatch):
    """The expensive direction. A gauntlet has zero spine mass, so no margin
    can flip it."""
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
    assert nc._shape_has_fine_animation_bones(_gauntlet()) is True


def test_sleeved_robe_is_withdrawn_from_extremity(monkeypatch):
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
    assert nc._shape_has_fine_animation_bones(_sleeved_robe()) is False


def test_off_by_default_leaves_the_robe_misrouted(monkeypatch):
    """The OFF path must be exactly today's behaviour, or the flag is not a
    no-op and the A/B that justified it is void."""
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", False)
    assert nc._shape_has_fine_animation_bones(_sleeved_robe()) is True


def test_spine_mass_without_hand_mass_is_not_a_sleeve(monkeypatch):
    """A scabbard handle hangs off the spine and never reaches an arm. Without
    the `hand > 0` term this flips, and a rigid prop lands on the cloth path."""
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
    assert nc._shape_has_fine_animation_bones(_scabbard()) is True


def test_margin_keeps_a_reference_body_out(monkeypatch):
    """`3BA Ref` measures spine/hand 1.03. A bare `spine > hand` flips it; the
    2x margin must not."""
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_SPINE_MARGIN", 2.0)
    assert nc._shape_has_fine_animation_bones(_ref_body()) is True


def test_margin_is_the_thing_doing_the_work(monkeypatch):
    """Control: at margin 1.0 the reference body DOES flip. If this stops
    failing, the margin has stopped being load-bearing and the test above
    would pass for the wrong reason."""
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
    monkeypatch.setattr(nc, "SLEEVE_GARMENT_SPINE_MARGIN", 1.0)
    assert nc._shape_has_fine_animation_bones(_ref_body()) is False


def test_guard_is_monotone(monkeypatch):
    """It may only ever WITHDRAW an extremity classification. Anything the gate
    calls non-extremity with the guard OFF must stay non-extremity with it ON."""
    plain = _Shape("Cloth", 200, {"NPC Spine1 [Spn1]": _uniform("s", 200)})
    for shape in (plain, _gauntlet(), _sleeved_robe(), _scabbard(), _ref_body()):
        monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", False)
        off = nc._shape_has_fine_animation_bones(shape)
        monkeypatch.setattr(nc, "SLEEVE_GARMENT_NOT_EXTREMITY", True)
        on = nc._shape_has_fine_animation_bones(shape)
        assert not (on and not off), f"{shape.name} was ADDED to extremity"
