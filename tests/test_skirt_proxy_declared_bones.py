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

"""`#collider-declared-bones` extended to the SkirtCol proxy (2026-08-17).

A registered collider may only carry bones the piece's OWN physics XML declares.
An influence with no rigid body in the system it is registered into is what
collapsed the cloth, bisected in game on ButtCol. That fix went to ButtCol only,
and the postrun audit still found 19 of 64 `SkirtCol` shapes carrying undeclared
bones.

The redirect is the same relabel-which-bone-holds-the-weight move, and it is
position-preserving because every bone's STB is its own bind inverse (verified
on a dragonbone cuirass: SkirtCol 47 bones / 4 undeclared -> 43 / 0, with ZERO
verts moved across the whole piece).

What this proxy needs and ButtCol does not: it is CHAIN-DRIVEN, so moving too
much of its mass onto kinematic ancestors leaves a proxy that no longer tracks
the cloth it proxies. Past `_SKIRT_PROXY_REDIRECT_MAX` it declines instead --
which is why the memory warned the ButtCol fix must not be copied across blindly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402

PELV = "NPC Pelvis [Pelv]"
LTWIST = "NPC L UpperarmTwist1 [LUt1]"
LUARM = "NPC L UpperArm [LUar]"


def test_a_decline_is_not_a_pass_failure():
    """A refusal is a correct outcome. Routed through `_note_pass_failure` it
    was counted and printed as a FAILURE, which teaches you to ignore the one
    channel that reports real breakage."""
    assert issubclass(nc._ColliderDeclined, Exception)
    assert nc._ColliderDeclined is not RuntimeError


# A synthetic ancestry, so these test THE LOGIC and not whether this machine
# happens to have a skeleton loaded. Skipping on a missing skeleton made the
# assertions decoration -- they never ran, which is indistinguishable from
# passing ([[feedback_method_traps]], test-harness hygiene).
_PARENTS = {
    LTWIST: LUARM,
    LUARM: "NPC L Clavicle [LClv]",
    "NPC L Clavicle [LClv]": "NPC Spine2 [Spn2]",
    "NPC Spine2 [Spn2]": PELV,
    "SkirtBBone01": PELV,
}


def test_undeclared_bone_walks_to_its_nearest_DECLARED_ancestor(monkeypatch):
    monkeypatch.setattr(nc, "_actor_skeleton_bone_parents", lambda: _PARENTS)
    # The XML declares UpperArm but not the twist -> the twist's weight lands
    # on UpperArm, the NEAREST declared ancestor, not on something further up.
    assert nc._nearest_declared_ancestor(LTWIST, {LUARM, PELV},
                                         {LUARM, PELV}) == LUARM
    # With UpperArm undeclared it must keep walking, not give up.
    assert nc._nearest_declared_ancestor(LTWIST, {PELV}, {PELV}) == PELV


def test_no_declared_ancestor_returns_None_so_the_caller_can_decline(monkeypatch):
    monkeypatch.setattr(nc, "_actor_skeleton_bone_parents", lambda: _PARENTS)
    # Nothing declared at all -> nowhere to put the weight.
    assert nc._nearest_declared_ancestor(LTWIST, set(), set()) is None
    # Declared but NOT available to weight onto -> still None. Both halves
    # matter: weighting to a bone the shape cannot bind skins it to the origin.
    assert nc._nearest_declared_ancestor(LTWIST, {LUARM}, set()) is None


def test_the_redirect_cap_exists_and_is_conservative():
    """The cap is what makes this safe on a CHAIN-DRIVEN proxy. If it ever
    reaches 1.0 the proxy could be fully relabelled onto kinematic bones and
    would stop following the cloth it exists to represent."""
    assert 0.0 < nc._SKIRT_PROXY_REDIRECT_MAX < 1.0
    assert nc._SKIRT_PROXY_REDIRECT_MAX <= 0.5


def test_the_proxy_is_still_gated_by_its_own_flag():
    # Reachability: a pass nobody can switch off is as bad as one nobody can
    # switch on ([[feedback_deployed_build_runs_at_defaults]]).
    from src import gui_settings as gs
    envs = {s.env for s in gs.SETTINGS if s.env}
    assert "CBBE2UBE_NO_SKIRT_PROXY_REBUILD" in envs
