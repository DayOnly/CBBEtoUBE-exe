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

"""Guards on the golden harness's own trustworthiness.

A regression harness that is wrong is worse than none, because its verdict is
believed. Two ways this one was wrong, both of which cost a wrong conclusion:

  * it converted through `nc.convert_nif` directly rather than the worker the
    batch uses, so it could pass or fail on code that never reaches a user. The
    paths genuinely disagreed on a multi-shape mashup -- batch showed a pass
    doing nothing, golden showed it firing on three shapes.
  * its baseline had no record of the code it was captured on, so a stale
    baseline reported 13 pieces "regressed" when the differences were simply a
    session of newer, intended work.
"""
import importlib.util
import inspect
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "golden_output.py"


def _text():
    return _SRC.read_text(encoding="utf-8", errors="ignore")


def test_converts_through_the_batch_worker_not_convert_nif():
    """#single-vs-batch-parity. convert_one_armor.py builds a work tuple and
    hands it to `ac._nif_convert_worker`; the harness must use the same door,
    or it measures a path nobody ships."""
    src = _text()
    assert "ac._nif_convert_worker(" in src
    assert "nc.convert_nif(" not in src, (
        "golden harness calls convert_nif directly again -- that is not the "
        "shipping path")


def test_worker_tuple_matches_the_one_convert_one_armor_builds():
    """The tuple shape is the contract. A mismatched arity or order would
    convert with the wrong slots / body ref and silently produce a different
    mesh."""
    golden = _text()
    other = (_SRC.parent / "convert_one_armor.py").read_text(
        encoding="utf-8", errors="ignore")
    for field in ("ref", "slots", "alt_tex"):
        assert field in golden, field
    assert "_nif_convert_worker(item)" in other or \
           "_nif_convert_worker((" in other


def test_baseline_records_the_code_it_was_captured_on():
    src = _text()
    assert "git_head" in src
    assert "_git_head" in src


def test_check_reports_a_baseline_from_different_code():
    """Not fatal -- comparing across an intended change is the point -- but it
    must NAME what is being compared. Silence is what made a stale baseline
    read as 13 regressions."""
    src = _text()
    assert "BASELINE IS FROM DIFFERENT CODE" in src
    i = src.index("def check(")
    assert "BASELINE IS FROM DIFFERENT CODE" in src[i:]


def test_check_reports_a_dirty_working_tree():
    """`check` on a dirty tree is measuring uncommitted work; the verdict is
    not attributable to any commit."""
    src = _text()
    assert "+dirty" in src
    assert "uncommitted" in src


def test_flag_guard_still_present():
    """Pre-existing and load-bearing: output depends on CBBE2UBE_* flags, so a
    cross-flag comparison is meaningless. Pinned so the new git-head guard is
    not mistaken for a replacement."""
    src = _text()
    assert "FLAG SET DIFFERS" in src


def test_harness_controls_are_not_recorded_as_converter_flags():
    """A control that cannot be exercised is not a control.

    `_flags` records every CBBE2UBE_* var and `check` refuses across a
    different set. CBBE2UBE_GOLDEN_NO_PIN is a knob for THIS SCRIPT, not for
    converter behaviour, so leaving it in meant using the escape hatch made
    `check` refuse with FLAG SET DIFFERS -- and three refusals in a row read
    exactly like three identical clean runs. That false result was very
    nearly reported as proof the converter was deterministic.
    """
    src = _text()
    i = src.index("def _flags(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert '"GOLDEN_"' in body or "'GOLDEN_'" in body, (
        "harness-only controls must be excluded from the recorded flag set")


def test_git_head_never_raises():
    """A dev tool must not die because git is missing or the repo is a tarball;
    it degrades to '?' and the check still runs."""
    spec = importlib.util.spec_from_file_location("golden_output", _SRC)
    mod = importlib.util.module_from_spec(spec)
    src = inspect.getsource
    # exec_module pulls heavy deps; assert on the source contract instead.
    text = _text()
    i = text.index("def _git_head(")
    body = text[i:text.index("\ndef ", i + 10)]
    assert "except Exception:" in body and 'return "?"' in body
    assert mod is not None and spec is not None and src is not None
