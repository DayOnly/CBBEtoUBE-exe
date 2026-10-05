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

"""#skirt-proxy-below-hip (GitHub #34) -- the skirt proxy is built from the skirt.

The generated `SkirtCol` takes its geometry from "the largest rendered shape that
is actually simulated". Counted over the whole shape, a robe's shoulder pauldrons
(474 chain-weighted verts at z 105-117) outvoted its skirt (306), and the skirt's
collision proxy was built at the shoulders. The count now looks below the hip top
first; a piece with no simulated cloth down there keeps the old choice.
"""
import inspect

import numpy as np
import pytest

from src import nif_convert as nc
from tests import _converter_sources as _cs  # patch on every module that binds a name
from src import nif_convert_physics as ph


class _Shape:
    def __init__(self, name, n, z, cloth=True):
        self.name = name
        self.verts = [(0.0, 0.0, float(z))] * n
        self.mass = np.full(n, 1.0 if cloth else 0.0)


def _mass(s):
    return s.mass


def _pick(shapes, decls=()):
    sh, n = ph._skirt_proxy_source(shapes, set(decls), _mass)
    return (sh.name if sh is not None else None), n


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(nc, "SKIRT_PROXY_BELOW_HIP", True)


def test_a_skirt_beats_larger_pauldrons():
    """THE REPORTED CASE: pauldrons 474 at the shoulder, skirt 306 below the hip."""
    assert _pick([_Shape("Pauldrons", 474, 110), _Shape("Skirt", 306, 50)]) == ("Skirt", 306)


def test_with_the_switch_off_the_largest_cloth_wins_as_before(monkeypatch):
    monkeypatch.setattr(nc, "SKIRT_PROXY_BELOW_HIP", False)
    assert _pick([_Shape("Pauldrons", 474, 110), _Shape("Skirt", 306, 50)]) == ("Pauldrons", 474)


def test_a_piece_whose_cloth_all_hangs_at_the_chest_keeps_its_choice():
    """No shape has enough cloth below the hip: the old count decides, so a
    5-vertex scrap never wins on a tie at zero."""
    assert _pick([_Shape("Chest", 272, 95), _Shape("Coat", 5, 96)]) == ("Chest", 272)


def test_too_little_cloth_below_the_hip_does_not_take_over():
    assert _pick([_Shape("Chest", 272, 95), _Shape("Hem", 39, 60)]) == ("Chest", 272)


def test_exactly_the_minimum_below_the_hip_takes_over():
    assert _pick([_Shape("Chest", 272, 95), _Shape("Hem", 40, 60)]) == ("Hem", 40)


def test_the_hip_top_itself_counts_as_below():
    from src.body_zones import HIP_Z
    assert _pick([_Shape("Chest", 272, 95), _Shape("Hem", 50, HIP_Z[1])]) == ("Hem", 50)


def test_unsimulated_verts_are_not_counted():
    assert _pick([_Shape("Chest", 100, 95),
                  _Shape("Trousers", 500, 40, cloth=False)]) == ("Chest", 100)


def test_xml_colliders_and_the_body_are_never_the_source():
    body = next(iter(nc.UBE_BODY_INJECT_NAMES))
    assert _pick([_Shape("Col", 900, 50), _Shape(body, 900, 50),
                  _Shape("Skirt", 60, 50)], decls=("Col",)) == ("Skirt", 60)


def test_no_candidate_gives_none():
    assert _pick([]) == (None, -1)


def test_the_switch_is_on_by_default():
    assert 'not _flag("CBBE2UBE_NO_SKIRT_PROXY_BELOW_HIP", False)' in _cs.whole_text()


def test_the_proxy_builder_takes_its_source_from_this_rule():
    src = inspect.getsource(ph._add_skirt_collider_proxy)
    assert "_skirt_proxy_source(nf.shapes, set(decls), _chain_mass," in src
    assert "body_z=_body_z" in src
    assert "shape_body_offset(sh_, body_verts=_base_v)" in src


def test_heights_are_judged_in_body_space():
    """The reported pauldrons store their verts at z -6..5 under a +112 transform:
    by their stored height they would count as hip-level cloth."""
    paul = _Shape("Pauldrons", 474, 0.0)
    paul.offset = 112.0
    skirt = _Shape("Skirt", 306, 50)
    skirt.offset = 0.0

    def body_z(s):
        return np.asarray(s.verts)[:, 2] + s.offset

    sh, n = ph._skirt_proxy_source([paul, skirt], set(), _mass, body_z=body_z)
    assert (sh.name, n) == ("Skirt", 306)
    sh_raw, _ = ph._skirt_proxy_source([paul, skirt], set(), _mass)
    assert sh_raw.name == "Pauldrons", "control: stored heights call the pauldrons hip-level"


def test_a_hidden_stabiliser_is_never_the_source():
    """The reported robe's hidden `Stabilizer` carries 396 chain verts at the hip,
    more than the skirt's 306: it is physics scaffolding, not cloth."""
    stab = _Shape("Stabilizer", 396, 50)
    stab.flags = 0x1
    assert _pick([stab, _Shape("Skirt", 306, 50)]) == ("Skirt", 306)
