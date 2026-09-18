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

"""Pins `scripts/analysis/morph_sweep.py` -- the MORPH axis of an A/B.

The tool is a rebuild of an UNTRACKED, UNTESTED scratch script that the
consolidation plan nevertheless named as half the gate for its three biggest
geometry items. The whole point of promoting it is that its exclusion rules are
now pinned instead of assumed, so the cases below are the traps, not coverage
for its own sake:

  * a piece that does not really cover the band scores a perfect 0.000 clip
    because nothing of it is there -- "0/0 IS NOT A PASS";
  * pieces pair by PATH, never by basename (two mods ship `cuirass_1.nif`);
  * an arm that failed to measure a piece must be COUNTED, not dropped.
"""
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "scripts" / "analysis"))

from morph_sweep import MIN_COVER, aggregate, _nifs   # noqa: E402


def _m(morph, cov, bind=0.0):
    return {"morph": morph, "bind": bind, "morph_cov": cov, "bind_cov": cov}


def test_a_thin_piece_is_excluded_and_counted():
    """0/0 IS NOT A PASS. A garment that does not reach the band reads 0.000
    clip in BOTH arms, which is a perfect score for being absent."""
    res = {"a": {"ctrl": _m(0.0, MIN_COVER - 0.1), "cand": _m(0.0, 50.0)},
           "b": {"ctrl": _m(1.0, 50.0), "cand": _m(2.0, 50.0)}}
    rows, unreadable, thin = aggregate(["a", "b"], res)
    assert (len(rows), unreadable, thin) == (1, 0, 1)
    assert rows[0][0] == "b"


def test_thin_in_EITHER_arm_is_enough_to_exclude():
    """The candidate can be the one that stopped covering the band -- that is a
    finding, but it is not a clip comparison."""
    res = {"a": {"ctrl": _m(0.0, 50.0), "cand": _m(0.0, MIN_COVER - 0.1)}}
    rows, _u, thin = aggregate(["a"], res)
    assert (rows, thin) == ([], 1)


def test_an_unmeasurable_piece_is_counted_not_dropped():
    """An arm that failed to convert or read half the sample would otherwise
    read as a clean win."""
    res = {"a": {"ctrl": None, "cand": _m(1.0, 50.0)},
           "b": {"ctrl": _m(1.0, 50.0)},                 # candidate missing
           "c": {"ctrl": _m(1.0, 50.0), "cand": _m(1.0, 50.0)}}
    rows, unreadable, _t = aggregate(["a", "b", "c"], res)
    assert (len(rows), unreadable) == (1, 2)


def test_delta_is_candidate_minus_control():
    """Sign convention: positive delta = the candidate clips MORE under morph."""
    res = {"a": {"ctrl": _m(1.0, 50.0), "cand": _m(3.5, 50.0)}}
    rows, _u, _t = aggregate(["a"], res)
    assert rows[0][3] == 2.5


def test_pieces_pair_by_path_not_basename(tmp_path):
    """Two mods ship `cuirass_1.nif`. Keyed by basename they collide and one
    silently scores against the other's numbers."""
    for mod in ("modA", "modB"):
        p = tmp_path / "meshes" / "!UBE" / mod / "cuirass_1.nif"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"NIF")
    found = _nifs(str(tmp_path))
    assert len(found) == 2
    assert sorted(found) == ["meshes/!ube/moda/cuirass_1.nif",
                             "meshes/!ube/modb/cuirass_1.nif"]


def test_staging_copies_are_not_scored(tmp_path):
    """`_bsa_staging` holds extracted SOURCES, not converter output. Scoring
    them doubles the population with meshes this build never wrote."""
    for sub in ("meshes/!UBE/x", "_bsa_staging/meshes/x"):
        p = tmp_path / sub / "thing_1.nif"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"NIF")
    assert list(_nifs(str(tmp_path))) == ["meshes/!ube/x/thing_1.nif"]


def test_only_the_high_weight_is_scored(tmp_path):
    """`_0` and `_1` are ONE garment at two weights; scoring both double-counts
    it. Halve every shape count."""
    for name in ("thing_0.nif", "thing_1.nif", "thing.nif"):
        p = tmp_path / "meshes" / "!UBE" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"NIF")
    assert list(_nifs(str(tmp_path))) == ["meshes/!ube/thing_1.nif"]


def test_valued_flags_do_not_leak_into_the_positionals():
    """`--band bust` filtered on the `--` prefix alone leaves `bust` behind as a
    third positional, and the tool prints its help and exits 2 -- a WORKING
    invocation that looks like a usage error. Bit both this tool and the
    acceptance gate's `--follow-top` on 2026-09-06."""
    from morph_sweep import positionals
    assert positionals(["ctrl", "cand"]) == ["ctrl", "cand"]
    assert positionals(["ctrl", "cand", "--band", "bust"]) == ["ctrl", "cand"]
    assert positionals(["ctrl", "cand", "--jobs", "8", "--limit", "5",
                        "--tol", "0.1", "--band", "butt"]) == ["ctrl", "cand"]


def test_the_acceptance_gate_survives_its_own_valued_flag():
    """Same bug, same day, other tool: `--follow-top 3` must not be read as a
    third positional. Exercises the GATE'S OWN parser -- re-implementing it here
    would pin a copy and let the real one rot."""
    import acceptance as acc
    assert acc.positionals(["ctrl", "cand"]) == ["ctrl", "cand"]
    assert acc.positionals(
        ["ctrl", "cand", "--follow-top", "3"]) == ["ctrl", "cand"]
    assert acc.positionals(
        ["ctrl", "cand", "--weights-only", "--geometry",
         "--follow-top", "5"]) == ["ctrl", "cand"]
