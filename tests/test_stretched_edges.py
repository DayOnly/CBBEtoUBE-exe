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

"""The LAST-STAGE stretch metric, and the instrument checks it must pass.

The tool exists because `damage_ledger` measured FLOW and flow disagreed with
outcome on the one pair where both were measured. A metric that replaces it has
to be pinned on the claims it makes: that BENDING is free, that STRETCHING is
not, that a rigid transform reads exactly zero, and that the short-edge trap
which inflated a belt's mean by 75% is visible rather than silent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from scripts.analysis import stretched_edges as se   # noqa: E402


# ------------------------------------------------------------------ fixtures

def _grid(nx: int = 12, ny: int = 12, step: float = 1.0):
    """A flat triangulated grid: enough edges to clear MIN_EDGES."""
    xs, ys = np.meshgrid(np.arange(nx) * step, np.arange(ny) * step, indexing="ij")
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(nx * ny)], axis=1)
    t = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            b = a + 1
            c = a + ny
            d = c + 1
            t += [[a, b, c], [b, d, c]]
    return v.astype(np.float64), np.asarray(t, dtype=np.int64)


def _rot_z(deg: float) -> np.ndarray:
    r = np.radians(deg)
    return np.array([[np.cos(r), -np.sin(r), 0.0],
                     [np.sin(r), np.cos(r), 0.0],
                     [0.0, 0.0, 1.0]])


# -------------------------------------------------------- the edge set itself

def test_shared_edge_is_counted_once():
    """Two triangles over a shared edge have FIVE distinct edges, not six.

    Counting the shared one twice weights a mesh's interior against its
    boundary for no reason, and would make the rate depend on tessellation.
    """
    tris = [[0, 1, 2], [1, 3, 2]]
    assert len(se.unique_edges(tris)) == 5


def test_edge_direction_does_not_matter():
    """The same edge wound both ways is ONE edge."""
    assert len(se.unique_edges([[0, 1, 2], [2, 1, 0]])) == 3


# ------------------------------------------------- INSTRUMENT CHECKS (must be 0)

def test_source_against_itself_reads_exactly_zero():
    """The negative control. If this is not 0.000 the tool measures noise."""
    v, t = _grid()
    stretched, scored, rate, dev = se.edge_stats(v, v, t)
    assert stretched == 0
    assert rate == 0.0
    assert dev == 0.0
    assert scored == len(se.unique_edges(t))


def test_rigid_transform_reads_exactly_zero():
    """A rotated + translated shape preserves every edge length.

    This is the check the project's crumple work states outright: a shape that
    was just rigidified MUST read 0.000. Any metric that needs a fitted frame
    fails it on a thin strap; edge length does not.
    """
    v, t = _grid()
    out = v @ _rot_z(37.0).T + np.array([120.0, -45.0, 8.5])
    stretched, _, rate, dev = se.edge_stats(v, out, t)
    assert stretched == 0
    assert rate == 0.0
    assert dev == pytest.approx(0.0, abs=1e-12)


def test_bending_is_free():
    """A garment BENDS around a bigger body, and bending must not score.

    Half the grid is rotated about the hinge column it shares with the other
    half, so every edge keeps its length and only the dihedral changes.
    """
    v, t = _grid(nx=12, ny=12)
    out = v.copy()
    hinge_x = v[:, 0].max() / 2.0
    far = v[:, 0] > hinge_x
    rel = out[far] - np.array([hinge_x, 0.0, 0.0])
    r = np.radians(30.0)
    rot = np.array([[np.cos(r), 0.0, np.sin(r)],
                    [0.0, 1.0, 0.0],
                    [-np.sin(r), 0.0, np.cos(r)]])
    out[far] = rel @ rot.T + np.array([hinge_x, 0.0, 0.0])
    stretched, _, rate, dev = se.edge_stats(v, out, t)
    # Edges that cross the hinge are shortened, never lengthened -- so the
    # STRETCH count, which is what ships, stays zero.
    assert stretched == 0
    assert rate == 0.0
    # ...and the deviation row still sees the fold, which is why both are
    # reported: they are not the same claim.
    assert dev > 0.0


# ------------------------------------------------- POSITIVE CONTROL (must fire)

def test_uniform_scale_fires_on_every_edge():
    """Doubling every edge is ratio 2.0 everywhere: rate 1.0, deviation 1.0."""
    v, t = _grid()
    stretched, scored, rate, dev = se.edge_stats(v, v * 2.0, t)
    assert stretched == scored
    assert rate == 1.0
    assert dev == pytest.approx(1.0)


def test_threshold_is_exclusive_and_tunable():
    """1.5x exactly is NOT over 1.5x, and the threshold is a parameter."""
    v, t = _grid()
    assert se.edge_stats(v, v * 1.5, t)[0] == 0
    assert se.edge_stats(v, v * 1.5, t, ratio=1.4)[0] == len(se.unique_edges(t))


# ----------------------------------------------------- THE SHORT-EDGE TRAP

def test_the_deviation_row_is_length_weighted_not_a_raw_mean():
    """1% of one belt's edges inflated its RAW mean deviation by 75%.

    Dividing by the AUTHORED length means a 0.02u edge pulled to 0.06u scores
    3.0, so a handful of tiny edges drags a raw mean anywhere it likes. Here a
    single column of the author's mesh is 100x short; the raw mean goes to ~3,
    which would read as a catastrophically distorted garment, while the
    length-weighted mean the tool reports stays near the ~4% the long edges
    actually moved. If this ever inverts, the reported number is the wrong one.
    """
    v, t = _grid(nx=12, ny=12, step=1.0)
    src = v.copy()
    src[src[:, 0] == 0.0, 0] = 0.99          # one column of 0.01u authored edges
    stretched, scored, rate, dev = se.edge_stats(src, v, t)

    e = se.unique_edges(t)
    ls = np.linalg.norm(src[e[:, 0]] - src[e[:, 1]], axis=1)
    lo = np.linalg.norm(v[e[:, 0]] - v[e[:, 1]], axis=1)
    raw = float(np.mean(np.abs(lo / ls - 1.0)))

    assert stretched > 0, "the COUNT row must see the short-edge blow-up"
    assert rate < 0.10, "and it is still a small fraction of the mesh"
    assert raw > 1.0, "the raw mean is wrecked by those few edges"
    assert dev < 0.1, "the length-weighted mean is not"
    assert dev < raw / 10.0


# --------------------------------------------------------- REFUSALS (counted)

def test_degenerate_authored_edges_are_dropped_not_scored_as_infinite():
    """A zero-length authored edge would divide to infinity. It is excluded."""
    v, t = _grid()
    src = v.copy()
    e = se.unique_edges(t)
    src[e[0, 1]] = src[e[0, 0]]          # collapse one authored edge
    st = se.edge_stats(src, v, t)
    assert st is not None
    assert st[1] < len(e), "the degenerate edge must leave the denominator"
    assert np.isfinite(st[3])


def test_too_few_edges_returns_none_rather_than_a_rate():
    """One stretched edge out of nine is 11%, which would swing the median."""
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]])
    assert se.edge_stats(v, v * 3.0, [[0, 1, 2], [1, 3, 2]]) is None


def test_vert_count_mismatch_returns_none():
    """A retopologised shape has no vertex correspondence to measure."""
    v, t = _grid()
    assert se.edge_stats(v, v[:-1], t) is None


def test_body_and_proxy_names_are_excluded_before_the_source_lookup():
    """`BaseShape` is OURS. Asking the author for it refuses every body-swap
    piece in the pack -- the exact trap that made an earlier harness score one
    convert path and call it the pack."""
    assert "BaseShape" in se.BODY_NAMES
    assert "SkirtCol" in se.PROXY_NAMES


# ------------------------------------------------------- population accounting

def test_weight_halves_collapse_to_one_garment():
    """`_0` and `_1` are ONE garment at two weights. A pooled total dominated
    by one big mesh counted twice has already inverted a verdict here."""
    assert se._garment("a/b/robe_0.nif") == "a/b/robe"
    assert se._garment("a/b/robe_1.nif") == "a/b/robe"
    assert se._garment("a/b/robe.nif") == "a/b/robe"


def test_a_valued_flag_eats_its_value():
    """`--ratio 2.0 <arm>` must leave ONE positional.

    Two other tools in this toolkit printed help and exited 2 on a valid
    invocation because the VALUE stayed in the positionals and was read as an
    arm directory.
    """
    assert se.main(["--ratio", "2.0"]) == 2      # no arms left -> usage
    assert se.main(["--limit", "5"]) == 2


def test_missing_arm_directory_is_refused():
    assert se.main([str(REPO_ROOT / "does-not-exist")]) == 2


def test_empty_population_exits_3_not_0(tmp_path, capsys):
    """0/0 IS NOT A PASS, and it is not a FAILURE either -- 3 says
    `nothing measured` so a driver can tell it from `measured and clean`."""
    arm = tmp_path / "arm"
    (arm / "meshes" / "!UBE").mkdir(parents=True)
    with pytest.raises(SystemExit) as ex:
        se.main([str(arm)])
    assert ex.value.code == 3
    assert "0/0 is not a pass" in capsys.readouterr().out


class _S:
    def __init__(self, name):
        self.name = name


def test_a_duplicate_shape_name_drops_every_copy_not_all_but_one():
    """`name -> shape` silently keeps the last one. Names are how the author's
    shape is found, so an ambiguous name is unresolvable either way -- and a
    THIRD copy must not quietly re-admit the name."""
    assert se.duplicate_names([_S("a"), _S("b")]) == set()
    assert se.duplicate_names([_S("a"), _S("a")]) == {"a"}
    assert se.duplicate_names([_S("a"), _S("a"), _S("a"), _S("b")]) == {"a"}
