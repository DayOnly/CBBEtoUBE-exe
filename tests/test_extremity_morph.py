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

"""#extremity-morph (audit 2026-10-20 F13).

Glove / boot verts weighted to hand / foot bones had their morph damped toward 0 (the body has no hand or foot), while
the bare hands and feet grow under a preset. They now follow UBE's own hands / feet slider data by their extremity
share.
"""
import numpy as np

from src import sliderset_gen as sg
from src.osd import OsdFile, OsdMorph

HAND = np.array([[30.0, 0, 80], [31.0, 0, 80], [32.0, 0, 80], [33.0, 0, 80]])
BODY = np.array([[20.0, 0, 80], [21.0, 0, 80], [22.0, 0, 80]])


def _refs():
    osd = OsdFile(version=1, morphs=[
        OsdMorph("HandsBiggerHands", np.arange(4), np.tile([0.0, 0.0, 0.5], (4, 1))),
        OsdMorph("HandsClaws", np.arange(4), np.tile([0.0, 0.3, 0.0], (4, 1)))])
    return [("Hands", HAND, osd)]


def _rows(ef, refs):
    body = OsdFile(version=1, morphs=[OsdMorph("BaseShapeBiggerHands", np.arange(3), np.tile([0.0, 0.0, 0.2], (3, 1)))])
    glove = np.vstack([HAND + [0, 0.5, 0], BODY + [0, 0.5, 0]])
    tri = sg.generate_armor_tri({"Glove": glove}, BODY, body, include_body_shapes=False,
                                armor_vert_extremity_fractions={"Glove": np.r_[np.full(4, ef), np.zeros(3)]},
                                extremity_refs=refs)
    sh = next((s for s in tri.shapes if s.name == "Glove"), None)
    out = {}
    for m in (sh.morphs if sh else []):
        d = np.zeros((7, 3)); d[np.asarray(m.idx)] = np.asarray(m.delta); out[m.name] = d
    return out


def test_finger_verts_follow_the_ube_hand():
    r = _rows(1.0, _refs())
    assert np.allclose(r["BiggerHands"][:4, 2], 0.5), "the hand's own motion, not the forearm's"
    assert np.allclose(r["BiggerHands"][4:, 2], 0.2), "the limb share still follows the body"
    assert np.allclose(r["Claws"][:4, 1], 0.3) and np.allclose(r["Claws"][4:], 0.0), "a hands-only slider gets a row"


def test_the_wrist_blends_by_its_share():
    r = _rows(0.5, _refs())
    z = r["BiggerHands"][:4, 2]                 # half the hand's 0.5, plus half of what the body share gives (<= 0.2)
    assert np.all(z >= 0.5 * 0.5 - 1e-6) and np.all(z <= 0.5 * 0.5 + 0.5 * 0.2 + 1e-6)


def test_without_refs_or_switched_off_fingers_stay_damped(monkeypatch):
    assert np.allclose(_rows(1.0, None)["BiggerHands"][:4], 0.0)
    monkeypatch.setattr(sg, "_EXTREMITY_MORPH", False)
    r = _rows(1.0, _refs())
    assert np.allclose(r["BiggerHands"][:4], 0.0) and "Claws" not in r


def test_far_from_any_hand_vert_stays_damped():
    far = [("Hands", HAND + [0, 50, 0], _refs()[0][2])]
    assert np.allclose(_rows(1.0, far)["BiggerHands"][:4], 0.0)
