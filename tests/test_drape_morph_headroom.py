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

"""#drape-morph-headroom: drape cloth moves under a body slider only by the
growth that actually reaches it.

Reported in game: a fur-skirt cuirass's flaps tilted visibly outward on a
character with a runtime body preset. The stored mesh hung exactly as authored;
the .tri gave the free-hanging hem (10u off the thigh) the thigh's FULL growth
(hem p50 3.8u on one slider, more than the body's own 1.8u there), so every
nonzero slider pushed the flaps out.
"""
import numpy as np

from src import sliderset_gen as sg


def _scale(gap, grow):
    u = np.array([[1.0, 0.0, 0.0]])
    d = np.array([[grow, 0.0, 0.0]])
    return float(sg._drape_headroom_scale(d, d, u, np.array([gap]))[0])


def test_hugging_cloth_keeps_the_exact_copy():
    assert _scale(1.0, 3.0) == 1.0
    assert _scale(sg._MATCH_NEAR, 3.0) == 1.0


def test_drape_cloth_does_not_follow_growth_that_cannot_reach_it():
    assert _scale(10.0, 3.0) == 0.0


def test_drape_cloth_takes_only_the_excess_once_the_body_reaches_it():
    gap, grow = 10.0, 12.0
    want = (grow - (gap - sg._DRAPE_CONTACT_CLEAR)) / grow
    assert abs(_scale(gap, grow) - want) < 1e-9


def test_a_shrinking_body_does_not_pull_drape_cloth_in():
    assert _scale(10.0, -3.0) == 0.0


def test_the_rule_blends_in_between_near_and_far():
    mid = 0.5 * (sg._MATCH_NEAR + sg._MATCH_FAR)
    s = _scale(mid, 1.0)
    assert 0.0 < s < 1.0


def test_generate_armor_tri_holds_a_free_hanging_hem(monkeypatch):
    """End to end: a body plane grows 3u outward; a hugging patch follows it, a
    hem hanging 10u off it does not."""
    from src.osd import OsdFile, OsdMorph
    xs = np.linspace(-5, 5, 6)
    body = np.array([[x, 0.0, z] for x in xs for z in xs], np.float64)
    delta = np.tile(np.array([0.0, 3.0, 0.0], np.float32), (len(body), 1))
    osd = OsdFile(version=1, morphs=[OsdMorph(name="BaseShapeGrow",
                                   idx=np.arange(len(body), dtype=np.int32),
                                   delta=delta)])
    hug = body + np.array([0.0, 1.0, 0.0])
    hem = body + np.array([0.0, 10.0, 0.0])
    tri = sg.generate_armor_tri({"Hug": hug, "Hem": hem}, body, osd,
                                include_body_shapes=False)
    by = {s.name: s for s in tri.shapes}
    hug_m = next(m for m in by["Hug"].morphs if m.name == "Grow")
    assert np.allclose(np.asarray(hug_m.delta)[:, 1], 3.0, atol=1e-3)
    assert not any(m.name == "Grow" for m in by.get("Hem", type("x", (), {"morphs": []})()).morphs)
