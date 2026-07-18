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

"""Cross-shape seam reconciliation math. Pure arrays, no pynifly."""
import numpy as np

from src.seam_reconcile import reconcile_seam_groups


def _body_line():
    # body surface along +X at z; outward normal +X. Verts at x=0.
    return np.array([[0.0, 0.0, float(z)] for z in range(70, 90)])


def test_opened_seam_is_welded_to_the_outward_member():
    # Two shapes share a seam point that was coincident in source (both ~x=1.0,
    # gap 0). In output the 'skirt' got inflated to x=2.5 while the 'top' stayed
    # at x=1.4 -> a 1.1u gap. Weld must move BOTH to the OUTWARD one (x=2.5).
    src = {"top": np.array([[1.0, 0.0, 80.0]]),
           "skirt": np.array([[1.0, 0.0, 80.0]])}
    out = {"top": np.array([[1.4, 0.0, 80.0]]),
           "skirt": np.array([[2.5, 0.0, 80.0]])}
    new, st = reconcile_seam_groups(out, src, body_verts=_body_line())
    assert st["groups_welded"] == 1
    # both end at the outward (skirt) position -> seam closed
    assert np.allclose(new["top"][0], [2.5, 0.0, 80.0])
    assert np.allclose(new["skirt"][0], [2.5, 0.0, 80.0])
    # PUSH-OUT ONLY: the moved (top) vert went OUTWARD (greater body distance),
    # never toward the body.
    assert new["top"][0][0] >= out["top"][0][0]


def test_closed_seam_is_left_alone():
    # coincident in source AND still together in output (spread < min) -> no-op.
    src = {"a": np.array([[1.0, 0.0, 80.0]]), "b": np.array([[1.0, 0.0, 80.0]])}
    out = {"a": np.array([[1.05, 0.0, 80.0]]), "b": np.array([[1.10, 0.0, 80.0]])}
    new, st = reconcile_seam_groups(out, src, body_verts=_body_line())
    assert st["groups_welded"] == 0
    assert np.allclose(new["a"], out["a"]) and np.allclose(new["b"], out["b"])


def test_non_coincident_source_verts_are_not_welded():
    # far apart in SOURCE (not a seam) -> never welded even if close in output.
    src = {"a": np.array([[1.0, 0.0, 80.0]]), "b": np.array([[1.0, 0.0, 60.0]])}
    out = {"a": np.array([[2.5, 0.0, 80.0]]), "b": np.array([[2.4, 0.0, 80.0]])}
    new, st = reconcile_seam_groups(out, src, body_verts=_body_line())
    assert st["groups_welded"] == 0


def test_same_shape_coincident_verts_are_not_welded():
    # two coincident verts WITHIN one shape are not a cross-shape seam.
    src = {"a": np.array([[1.0, 0.0, 80.0], [1.0, 0.0, 80.0]])}
    out = {"a": np.array([[1.0, 0.0, 80.0], [2.5, 0.0, 80.0]])}
    new, st = reconcile_seam_groups(out, src, body_verts=_body_line())
    assert st["groups_welded"] == 0


def test_inputs_are_not_mutated():
    src = {"top": np.array([[1.0, 0.0, 80.0]]),
           "skirt": np.array([[1.0, 0.0, 80.0]])}
    out = {"top": np.array([[1.4, 0.0, 80.0]]),
           "skirt": np.array([[2.5, 0.0, 80.0]])}
    out_top_before = out["top"].copy()
    reconcile_seam_groups(out, src, body_verts=_body_line())
    assert np.allclose(out["top"], out_top_before)   # original dict untouched


def test_fallback_without_body_uses_max_source_displacement():
    # no body given -> outward = the member that moved farthest from its source.
    src = {"top": np.array([[1.0, 0.0, 80.0]]),
           "skirt": np.array([[1.0, 0.0, 80.0]])}
    out = {"top": np.array([[1.4, 0.0, 80.0]]),     # moved 0.4
           "skirt": np.array([[2.5, 0.0, 80.0]])}   # moved 1.5 -> outward
    new, st = reconcile_seam_groups(out, src, body_verts=None)
    assert st["groups_welded"] == 1
    assert np.allclose(new["top"][0], [2.5, 0.0, 80.0])


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} seam-reconcile tests passed")
    sys.exit(0)
