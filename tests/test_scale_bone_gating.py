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

"""Guards for the scale-bone tracking layer + the GPU bone-cap that together
fix the Traveling Mage equip CTD and the Forsworn cloth "far from body" (#166,
reverted/superseded by the #167 regression fix).

- Cloth carries NO per-shape BODYTRI (single carrier is the body shape, #114),
  so it does NOT morph via the per-armor TRI — the 3BA scale bones are its only
  runtime body-tracking layer. They must be added to cloth (not gated away).
- No converted shape may exceed Skyrim's per-partition GPU bone cap (~80) or it
  overruns the bone-matrix palette at draw -> BSBatchRenderer access violation.
- The cap must rank by LOCAL dominance (max per-vert weight) so a concentrated
  physics/skirt bone (e.g. a robe skirt chain) survives while a thinly-spread
  scale tail is dropped. Total-weight ranking evicted the TMage skirt physics
  bones -> no sway + hem distortion.
"""
from src import nif_convert as nc


def test_cloth_scale_bones_not_gated_away():
    # The reskin must transfer scale bones (flag off) AND cloth gets the
    # add_scale_bone_weights pass (broad gate). Both are the body-tracking layer.
    assert nc.RESKIN_EXCLUDE_SCALE_BONES is False
    assert nc.ADD_SCALE_BONES_TO_CLOTH is True


def test_bone_cap_keeps_locally_dominant_drops_thin_tail():
    # 100 bones: a few CONCENTRATED bones (dominant on a handful of verts, like a
    # skirt physics chain) + many THIN scale tails (small weight on every vert).
    # The cap must keep the concentrated ones and drop the thin tails.
    skirt = {f"Skirt{i}": [(i, 0.75)] for i in range(6)}        # max 0.75, local
    thin = {f"Scale{i}": [(v, 0.02) for v in range(50)]         # max 0.02, spread
            for i in range(94)}
    weights = {**skirt, **thin}
    bones = list(weights)
    xforms = {b: object() for b in bones}
    nb, nx, nw = nc._cap_skin_bone_count(bones, xforms, weights, limit=78)
    assert len(nb) == 78
    # every concentrated skirt bone survives (locally dominant)
    for i in range(6):
        assert f"Skirt{i}" in nb, "skirt physics bone wrongly evicted"
    # xforms trimmed in lock-step
    assert set(nx) == set(nb)
    # per-vert weights renormalized to 1.0
    psum = {}
    for prs in nw.values():
        for vi, w in prs:
            psum[vi] = psum.get(vi, 0.0) + w
    assert all(abs(t - 1.0) < 1e-6 for t in psum.values())


def test_bone_cap_heaviest_per_vert_survives():
    n = 100
    bones = [f"B{i}" for i in range(n)]
    weights = {f"B{i}": [(0, 0.001)] for i in range(n)}
    weights["B0"] = [(0, 1.0)]            # dominant on vert 0 -> must be kept
    xforms = {f"B{i}": object() for i in range(n)}
    nb, nx, nw = nc._cap_skin_bone_count(bones, xforms, weights, limit=78)
    assert len(nb) == 78
    assert "B0" in nb
    tot = sum(w for prs in nw.values() for _, w in prs)
    assert abs(tot - 1.0) < 1e-6
    assert set(nx) == set(nb)


def test_bone_cap_noop_under_limit():
    bones = ["A", "B"]
    weights = {"A": [(0, 0.5)], "B": [(0, 0.5)]}
    xforms = {"A": object(), "B": object()}
    nb, nx, nw = nc._cap_skin_bone_count(bones, xforms, weights, limit=78)
    assert nb is bones and nw is weights   # unchanged object (fast path)
