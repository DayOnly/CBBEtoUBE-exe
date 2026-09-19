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

"""`scripts/analysis/seat_error_vs_author.py` -- the unplaced-shape guard.

WHAT WENT WRONG. The scorer reported a MEAN seat error of 9.1811u against a
median of 0.3466u. Measured over its own 4499 paired shapes, 44 of them (the
top 1%) carried 95.7% of that total, at up to 2473u -- and the meshes were
fine. `_world` failed to resolve the transform of SMP collider and HDT helper
shapes, scattering their vertices; the RAW vertices of one such shape sit at
z 90.6-118.6 exactly where a neck scarf belongs, while `_world` spread them
over z -319.9..144.3. An unguarded mean over that is not a fit number.

WHY NOT A NAME LIST. The obvious fix -- reuse the converter's structural keys
("baseshape", "3ba", "virtual", "col", "ground", "ref") -- does NOT work, and
measuring it is what showed that: excluding by name left the mean at 7.3669u
and the maximum at 2473u, because the worst offenders are named `Cylinder.00N`
and `HDTBag` and match no key. 146 shapes are implausibly placed; only 130 are
reachable by name. The guard has to be on the PLACEMENT, not the name.
"""
import re
from pathlib import Path

import numpy as np

from scripts.analysis import seat_error_vs_author as se


_SRC = Path(se.__file__).read_text(encoding="utf-8")


def test_a_shape_the_transform_did_not_place_is_excluded():
    # the real failure: ~2477u from the body
    assert se._unplaced(np.full(64, 2476.95))


def test_a_normally_seated_shape_is_kept():
    # a garment sits fractions of a unit off the body it was fitted to
    assert not se._unplaced(np.full(64, 0.35))


def test_a_garment_that_legitimately_floats_is_kept():
    # the widest legitimate shape measured over the pack sat 28.69u out --
    # a cape or a flared skirt is not a transform failure.
    assert not se._unplaced(np.full(64, 28.69))


def test_the_threshold_sits_inside_the_measured_empty_gap():
    """Not tuned: the population has NOTHING between 28.69u and 103.14u, so
    every threshold in that band yields the same partition. If this ever fails,
    the gap has closed and the number needs re-measuring, not nudging."""
    assert 28.69 < se._MAX_PLAUSIBLE_OFF < 103.14


def test_the_guard_applies_to_BOTH_arms():
    """The author's mesh and ours are transformed by the same `_world`, so a
    guard on one arm only would keep scoring the other against a scattered
    shape."""
    assert "_unplaced(a_off)" in _SRC
    assert "_unplaced(o_off)" in _SRC


_SAMPLE = [("meshes/a/x_1.nif", "Cylinder.002", 2476.95, "ours"),
           ("meshes/a/y_0.nif", "ColBack", 2316.86, "ours"),
           ("meshes/b/z_1.nif", "HDTBag", 1846.86, "author")]


def test_nothing_is_reported_when_nothing_was_excluded():
    assert se._report_unplaced([]) == []


def test_excluded_shapes_are_reported_not_silently_dropped():
    """A scorer that quietly drops 162 shapes is how a population shrinks
    without anyone noticing. Asserted on the RETURNED lines, not on a substring
    of the source: the first version of this test accepted the word "EXCLUDED"
    anywhere in the file, which the module docstring satisfied by itself, and
    the mutation gate caught it as MISSED."""
    lines = se._report_unplaced(_SAMPLE)
    assert lines, "three excluded shapes must produce a report"
    head = lines[0]
    assert "3" in head                      # how many
    assert "50" in head                     # against what threshold
    assert "not resolve" in head            # why
    assert "NOT implicated" in head         # and that the mesh is not to blame


def test_the_report_names_the_worst_offenders_largest_first():
    lines = se._report_unplaced(_SAMPLE)
    body = "".join(lines[1:])
    assert "Cylinder.002" in body and "ColBack" in body and "HDTBag" in body
    assert body.index("Cylinder.002") < body.index("ColBack") < body.index("HDTBag")


def test_a_long_report_is_truncated_but_says_how_many_it_held_back():
    many = [(f"m/{i}.nif", f"S{i}", 100.0 + i, "ours") for i in range(9)]
    lines = se._report_unplaced(many, top=5)
    assert sum(1 for line in lines if line.startswith("      ") and "..." not in line) == 5
    assert "and 4 more" in lines[-1]


def test_the_guard_does_not_implicate_the_mesh():
    """The RAW vertices are correct. This is a scoring guard, and the docstring
    has to say so or the next reader files an output bug."""
    assert re.search(r"shipped mesh is NOT implicated|shipped mesh is fine",
                     _SRC)


def test_no_machine_specific_paths_or_mod_names():
    """Tracked content: no modlist path, no drive letter, no mod name.

    The drive pattern is built rather than written as an escape, because
    the first version of this test asked for FOUR literal backslashes and
    could never fire -- a check that cannot fail is decoration."""
    # the lookbehind is load-bearing: without it this matches the "s:/"
    # in the licence header's https:// URL and the test fails on itself.
    drive = re.compile("(?<![A-Za-z])[A-Za-z]:["
                       + re.escape(chr(92)) + "/]")
    assert drive.search("x = \"C:" + chr(92) * 2 + "Users\"")   # control: it fires
    assert not drive.search(_SRC)
    assert "Modlists" not in _SRC
    assert "Users" not in _SRC
