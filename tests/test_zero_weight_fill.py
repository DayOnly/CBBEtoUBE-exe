"""Regression guard for _fill_zero_weight_verts (#zeroweight).

Verts with no bone weight skin to the origin -> a spike to (0,0,0). The fix gives
each zero-weight vert the weights of its nearest weighted vert. Seen on guard-armor
reskins (qwib) and decoration/1st-person shapes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402


def _wsum(weights, n):
    ws = [0.0] * n
    for _bn, pairs in weights.items():
        for i, w in pairs:
            ws[int(i)] += float(w)
    return ws


def test_fills_zero_weight_vert_from_nearest():
    # vert 0 + 1 weighted; vert 2 (closest to vert 1) carries NO weight
    verts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (11.0, 0.0, 0.0)]
    weights = {"BoneA": [(0, 1.0)], "BoneB": [(1, 1.0)]}
    out = nc._fill_zero_weight_verts(weights, verts)
    ws = _wsum(out, 3)
    assert ws[2] > 0.9                                  # vert 2 now weighted
    assert any(i == 2 for i, _w in out.get("BoneB", []))  # borrowed nearest (BoneB)


def test_noop_when_all_weighted():
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    weights = {"BoneA": [(0, 1.0), (1, 1.0)]}
    out = nc._fill_zero_weight_verts(weights, verts)
    assert out is weights                               # unchanged (no-op)
