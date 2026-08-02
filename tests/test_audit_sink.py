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

"""The sink reader has to get four things right, all learned the hard way.

Every one of these tests exists because the corresponding mistake was actually
made while reading this telemetry by hand:

  * reading `final` instead of `shipped` turned 101 shipped exposed verts into
    174 and made a zero-regression run look like 20 shapes regressed;
  * a summary that led with averages buried the two shapes whose measurement
    had CRASHED -- they read as clean rather than as unmeasured;
  * including first-person viewmodels in standoff put a 9.78u outlier at the top
    and inflated the over-ceiling rate;
  * applying the bust ceiling to the other bands would manufacture failures,
    because standoff genuinely differs by band.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import audit_sink as A  # noqa: E402


def _cap():
    out = []
    return out, out.append


def _rows():
    return [
        {"kind": "chain", "path": "!UBE/a/x_1.nif", "shape": "Torso",
         "entry": 3, "final": 5, "shipped": 0,
         "outcome": "ROLLED BACK to conform (3->5, kept 0)",
         "rolled_back_to": "conform"},
        {"kind": "chain", "path": "!UBE/a/y_1.nif", "shape": "Top",
         "entry": 10, "final": 2, "shipped": 2, "outcome": "ok (10->2)",
         "rolled_back_to": None},
        {"kind": "chain", "path": "!UBE/a/z_1.nif", "shape": "Bad",
         "entry": -1, "final": 0, "shipped": -1, "outcome": "unmeasurable"},
        {"kind": "standoff_band_error", "path": "!UBE/a/z_1.nif",
         "shape": "Bad", "error": "MemoryError: boom"},
        {"kind": "frame", "path": "!UBE/a/x_1.nif", "shape": "Torso",
         "offset": [-40.0, 0.0, 0.0], "raw_reach": 2.1,
         "offset_reach": 21.0},
        {"kind": "standoff_band", "path": "!UBE/a/x_1.nif", "shape": "Torso",
         "band": "strap", "median": 1.5, "n": 100},
        {"kind": "standoff_band", "path": "!UBE/1stperson/w_1.nif",
         "shape": "V", "band": "strap", "median": 9.8, "n": 100},
        {"path": "!UBE/a/x_1.nif", "shape": "Torso", "median": 3.0,
         "p90": 3.4, "max": 4.0, "over": True, "n": 90},
        {"path": "!UBE/1stperson/w_1.nif", "shape": "V", "median": 9.9,
         "p90": 10.0, "max": 11.0, "over": True, "n": 90},
    ]


def test_reports_shipped_not_final():
    """THE field mistake. entry 3+10=13; shipped 0+2=2; final would say 5+2=7."""
    out, p = _cap()
    A.report(_rows(), out=p)
    txt = "\n".join(out)
    assert "entry 13 -> SHIPPED 2" in txt, txt
    assert "SHIPPED worse than entry: 0" in txt


def test_failures_are_reported_before_the_clean_numbers():
    out, p = _cap()
    A.report(_rows(), out=p)
    txt = "\n".join(out)
    i_fail = txt.index("MEASUREMENT FAILURES")
    assert "1 recorded error(s), 1 unmeasurable" in txt
    assert "not 'clean'" in txt
    for later in ("CHAIN over", "STANDOFF BY BAND", "CALIBRATED BUST"):
        assert txt.index(later) > i_fail, f"{later} printed before failures"


def test_first_person_is_excluded_and_the_count_is_stated():
    out, p = _cap()
    A.report(_rows(), out=p)
    txt = "\n".join(out)
    assert "1 first-person record(s) excluded" in txt
    assert "9.8" not in txt.split("STANDOFF BY BAND")[1].split("CALIBRATED")[0]


def test_no_verdict_is_invented_for_the_new_bands():
    out, p = _cap()
    A.report(_rows(), out=p)
    txt = "\n".join(out)
    assert "no verdict on any band but `bust`" in txt


def test_frame_corrections_are_listed_with_their_path():
    out, p = _cap()
    A.report(_rows(), out=p)
    txt = "\n".join(out)
    assert "FRAME corrections" in txt and "!UBE/a/x_1.nif" in txt


def test_clean_run_says_so_rather_than_staying_silent():
    rows = [r for r in _rows()
            if r.get("kind") not in ("standoff_band_error",)
            and r.get("outcome") != "unmeasurable"]
    out, p = _cap()
    A.report(rows, out=p)
    assert "no recorded measurement failures" in "\n".join(out)


def test_torn_lines_cost_one_record_not_the_file(tmp_path):
    f = tmp_path / "s.jsonl"
    good = json.dumps({"kind": "chain", "path": "a", "shape": "s",
                       "entry": 0, "final": 0, "shipped": 0, "outcome": "ok"})
    f.write_text(good + "\n{\"kind\": \"chain\", \"entry\"\n" + good + "\n",
                 encoding="utf-8")
    rows, torn = A.load(f)
    assert len(rows) == 2 and torn == 1


def test_empty_sink_is_an_error_not_a_pass(tmp_path):
    (tmp_path / "standoff_audit.jsonl").write_text("", encoding="utf-8")
    assert A.main([str(tmp_path)]) == 3


def test_missing_sink_reports_rather_than_raising(tmp_path):
    assert A.main([str(tmp_path)]) == 2


def test_older_runs_are_skipped_by_default(tmp_path):
    f = tmp_path / "standoff_audit.jsonl"
    rows = _rows() + [{"kind": "chain", "nif": "old.nif", "shape": "Old",
                       "entry": 999, "final": 999, "outcome": "ok"}]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert A.main([str(f)]) == 0          # the stale row must not be mixed in
