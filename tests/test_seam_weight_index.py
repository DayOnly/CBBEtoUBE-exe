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

"""Seam-skin unification: index the weights instead of rescanning them.

`_set_override_vert_weights` rebuilt EVERY bone's weight list to remove one
vertex, and the matching read scanned every (vert, weight) pair to find one
vertex. Unifying N seam verts was therefore O(N x total pairs). Profiled on a
real copy-path armour: 39,024 calls, 98.8 s of list rebuilding in a 186.9 s
conversion. Indexed, that file converts in 8.78 s with a bit-identical
skin-weight digest.

THE RISK IS THE FORMAT, not the arithmetic. `weights` is `{bone: [(vi, w)...]}`
everywhere else in the converter; the index form is a dict and exists only
inside `_match_seam_skinning`. If it ever leaked out, downstream code would
iterate a dict of ints and produce silently wrong skinning rather than crash --
so `test_index_form_never_escapes` is the one that matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import nif_convert as nc  # noqa: E402


def _osk(weights):
    return {"bones": list(weights), "xforms": {}, "weights": weights}


def test_round_trip_preserves_every_pair():
    osk = _osk({"A": [(0, 0.25), (3, 0.75)], "B": [(1, 1.0)]})
    nc._weights_to_index(osk)
    assert osk["weights"] == {"A": {0: 0.25, 3: 0.75}, "B": {1: 1.0}}
    nc._weights_from_index(osk)
    assert osk["weights"] == {"A": [(0, 0.25), (3, 0.75)], "B": [(1, 1.0)]}


def test_round_trip_is_idempotent():
    osk = _osk({"A": [(2, 0.5), (1, 0.5)]})
    nc._weights_to_index(osk)
    nc._weights_to_index(osk)          # already indexed: must not corrupt
    nc._weights_from_index(osk)
    nc._weights_from_index(osk)        # already list form
    assert osk["weights"] == {"A": [(1, 0.5), (2, 0.5)]}


def test_setting_a_vertex_replaces_it_across_every_bone():
    """The behaviour the old list rebuild implemented, at O(1)."""
    osk = _osk({"A": [(5, 0.4), (6, 0.6)], "B": [(5, 0.6)], "C": [(7, 1.0)]})
    nc._weights_to_index(osk)
    nc._set_override_vert_weights(osk, 5, {"B": 0.3, "D": 0.7}, {"D": "XF"})
    nc._weights_from_index(osk)
    assert osk["weights"]["A"] == [(6, 0.6)], "vert 5 not removed from A"
    assert osk["weights"]["B"] == [(5, 0.3)], "vert 5 not overwritten in B"
    assert osk["weights"]["C"] == [(7, 1.0)], "an untouched bone changed"
    assert osk["weights"]["D"] == [(5, 0.7)], "new bone not created"
    assert "D" in osk["bones"] and osk["xforms"]["D"] == "XF"


def test_zero_and_negative_weights_are_dropped():
    osk = _osk({"A": [(1, 1.0)]})
    nc._weights_to_index(osk)
    nc._set_override_vert_weights(osk, 1, {"A": 0.0, "B": -0.5, "C": 0.9}, {})
    nc._weights_from_index(osk)
    assert osk["weights"]["A"] == []
    assert "B" not in osk["weights"]
    assert osk["weights"]["C"] == [(1, 0.9)]


def test_index_form_never_escapes(monkeypatch):
    """THE test. A dict leaking into `override_skin` would not crash -- it
    would produce silently wrong skinning."""
    plates = [
        {"override_skin": _osk({"A": [(0, 1.0)], "B": [(1, 1.0)]}), "src": None},
        {"override_skin": _osk({"A": [(0, 1.0)], "B": [(1, 1.0)]}), "src": None},
    ]
    clusters = [[(0, 0), (1, 0)], [(0, 1), (1, 1)]]
    n = nc._match_seam_skinning(plates, clusters)
    assert n > 0, "nothing was unified; the test would be vacuous"
    for p in plates:
        for bn, pairs in p["override_skin"]["weights"].items():
            assert isinstance(pairs, list), f"{bn} left in index form"
            for item in pairs:
                assert isinstance(item, tuple) and len(item) == 2


def test_unified_verts_get_the_same_weighting_on_both_plates():
    a = _osk({"A": [(0, 1.0)]})
    b = _osk({"B": [(0, 1.0)]})
    plates = [{"override_skin": a, "src": None}, {"override_skin": b,
                                                  "src": None}]
    nc._match_seam_skinning(plates, [[(0, 0), (1, 0)]])
    wa = {bn: dict(v) for bn, v in a["weights"].items() if v}
    wb = {bn: dict(v) for bn, v in b["weights"].items() if v}
    assert wa == wb, f"seam verts diverge: {wa} vs {wb}"
    assert abs(sum(next(iter(wa.values())).values()
                   if len(wa) == 1 else
                   [w for d in wa.values() for w in d.values()]) - 1.0) < 1e-9


def test_a_cluster_with_no_usable_member_is_skipped():
    plates = [{"override_skin": None, "src": None}]
    assert nc._match_seam_skinning(plates, [[(0, 0)]]) == 0
