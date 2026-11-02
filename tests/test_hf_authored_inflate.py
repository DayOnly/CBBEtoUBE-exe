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

"""#hf-authored-inflate (audit 2026-10-20 F21).

The gauntlet / boot branch inflated by a flat 0.6u with no floor and shipped gloves and boots ~0.3u looser than the
authors. Both branches now hand `inflate_armor_outward` the main chain's authored-floor inputs.
"""
import inspect

import numpy as np

from src import nif_convert as nc


def test_both_hand_foot_branches_pass_the_authored_floor():
    for fn in (nc._fit_shapes_copy, nc._fit_shapes_swap):
        src = inspect.getsource(fn)
        i = src.index("magnitude=ARMOR_INFLATION_MAGNITUDE_HANDS_FEET")
        call = src[src.rindex("inflate_armor_outward(", 0, i):src.index(".astype", i)]
        assert "**_hfa" in call, fn.__name__
        assert "HF_AUTHORED_INFLATE" in src[:i] and "src_armor_verts=hf_orig" in src[:i]


def _sheet(y):
    xs = np.linspace(-3, 3, 7)
    X, Z = np.meshgrid(xs, xs)
    return np.stack([X.ravel(), np.full(X.size, float(y)), Z.ravel() + 80.0], 1)


def test_the_floor_only_lowers_the_flat_push():
    body = _sheet(0.0); bn = np.tile([0.0, 1.0, 0.0], (len(body), 1))
    glove = _sheet(0.1)
    flat = nc.inflate_armor_outward(glove, body, magnitude=0.6, close_threshold=4.0, body_normals=bn)
    src_body = _sheet(0.0); src_glove = _sheet(0.1)            # the author had it 0.1u off their body
    floored = nc.inflate_armor_outward(glove, body, magnitude=0.6, close_threshold=4.0, body_normals=bn,
                                       src_armor_verts=src_glove, src_body_verts=src_body, src_body_normals=bn)
    assert np.all(floored[:, 1] <= flat[:, 1] + 1e-6)
    assert floored[:, 1].max() < flat[:, 1].max()
