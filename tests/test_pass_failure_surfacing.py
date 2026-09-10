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

"""A pass that RAISED and was swallowed still converts the piece, so it lands in
no error bucket -- the run reads as clean while a pass may have failed on EVERY
piece. That is the "a BROKEN pass reads as a failed design" trap.

The aggregate must be built from the per-piece `reason` string, because that is
the only channel that crosses the process-pool boundary: `pass_failure_summary()`
reads the WORKER's module state and would report an empty dict in the parent.
"""
from pathlib import Path

from src.nif_convert import ConvertResult
from src.auto_convert import AutoConvertResult


def _cr(name, reason=""):
    return ConvertResult(src_path=Path(name), dst_path=Path(name + ".out"),
                         status="converted (copy)", reason=reason)


def _report(tmp_path, results):
    acr = AutoConvertResult(source_dir=tmp_path, output_dir=tmp_path,
                            nif_results=results)
    rep = tmp_path / "report.txt"
    acr.write_report(rep)
    return rep.read_text()


# The exact string `_note_pass_failure` builds.
def _pf(label, exc="RuntimeError", msg="boom"):
    return f"PASS FAILED {label} ({exc}: {msg})"


def test_a_swallowed_pass_failure_is_surfaced(tmp_path):
    txt = _report(tmp_path, [_cr("a.nif", _pf("_cap_weights_map"))])
    assert "pass failures" in txt
    assert "_cap_weights_map" in txt


def test_failures_are_counted_per_pass(tmp_path):
    results = [_cr(f"{i}.nif", _pf("_cap_weights_map")) for i in range(3)]
    results.append(_cr("x.nif", _pf("repair_collapsed_tris/selfint")))
    txt = _report(tmp_path, results)
    assert "4 across 2 pass(es)" in txt
    assert "3 x  _cap_weights_map" in txt
    assert "1 x  repair_collapsed_tris/selfint" in txt


def test_a_clean_run_reports_no_pass_failures(tmp_path):
    """It must not fire on ordinary reasons -- otherwise it is noise and gets
    ignored, which is the same as not reporting it."""
    txt = _report(tmp_path, [_cr("a.nif", "heel transplant unsafe — used original mesh"),
                             _cr("b.nif", "")])
    assert "pass failures" not in txt


def test_several_failures_on_ONE_piece_are_all_counted(tmp_path):
    """`reason` joins with '; ' -- the parser must not stop at the first one."""
    joined = "; ".join([_pf("_cap_weights_map"), _pf("_cap_push_at_own_shell")])
    txt = _report(tmp_path, [_cr("a.nif", joined)])
    assert "2 across 2 pass(es)" in txt


def test_a_message_containing_a_semicolon_does_not_invent_a_pass(tmp_path):
    """The recorded exception text can itself contain '; ' (the undeclared-bone
    guard formats a dict). Splitting on it must not manufacture a label."""
    noisy = "PASS FAILED registered_shape_undeclared_bones (RuntimeError: " \
            "x.nif: shapes carry undeclared bones: {'A': ['b1']; 'C': ['d2']})"
    txt = _report(tmp_path, [_cr("a.nif", noisy)])
    assert "1 across 1 pass(es)" in txt
    assert "registered_shape_undeclared_bones" in txt


def test_the_report_does_not_build_this_from_worker_module_state():
    """Guard the REASON this is built from `reason`.

    `pass_failure_summary()` reads `nif_convert`'s module-level counters, which
    live in the WORKER process. The parent never sees them, so a report built on
    that helper would print nothing and read as "no failures" -- a guard that
    cannot fail. Pin the implementation choice so nobody "simplifies" it back.
    """
    import ast
    import inspect
    import textwrap
    # AST, not a substring: `write_report` DOCUMENTS why it avoids that helper,
    # so the name legitimately appears in a comment. Only a real CALL is a bug.
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(AutoConvertResult.write_report)))
    called = {getattr(c.func, "id", None) or getattr(c.func, "attr", None)
              for c in ast.walk(tree) if isinstance(c, ast.Call)}
    assert "pass_failure_summary" not in called, (
        "write_report is reading worker-side counters; in the parent those are "
        "empty, so the section would silently report nothing")
    assert "count_pass_failures" in called, (
        "the aggregate must come from the per-piece reason, via the shared "
        "helper")


def test_the_pack_wide_summary_rolls_up_every_mod(tmp_path):
    """A pass failing on every piece would be spread across ~162 per-mod files
    and read as noise in each. The roll-up is what makes it obvious."""
    from src.auto_convert import write_conversion_summary
    mods = []
    for name in ("ModA", "ModB"):
        d = tmp_path / name
        d.mkdir()
        acr = AutoConvertResult(
            source_dir=d, output_dir=tmp_path,
            nif_results=[_cr("a.nif", _pf("_cap_weights_map")),
                         _cr("b.nif", _pf("_cap_weights_map"))])
        mods.append((d, acr, None))
    out = write_conversion_summary(tmp_path, mods)
    assert out is not None, "the summary file was not written at all"
    txt = out.read_text()
    assert "swallowed PASS FAILURES: 4 across 1 pass(es) and 2 mod(s)" in txt
    assert "4 x  _cap_weights_map" in txt


def test_the_summary_is_still_written_when_nothing_failed(tmp_path):
    """GUARD THE BLANKET EXCEPT. `write_conversion_summary` wraps everything in
    `except Exception: return None`, so a throw in the new section would not
    surface as an error -- the whole summary file would just silently vanish.
    That exact shape (a NameError swallowed into a missing report) has happened
    in this function before."""
    from src.auto_convert import write_conversion_summary
    d = tmp_path / "ModA"
    d.mkdir()
    acr = AutoConvertResult(source_dir=d, output_dir=tmp_path,
                            nif_results=[_cr("a.nif")])
    out = write_conversion_summary(tmp_path, [(d, acr, None)])
    assert out is not None, "summary vanished -- the new section raised"
    assert "PASS FAILURES" not in out.read_text()


def test_the_json_health_panel_carries_pass_failures(tmp_path):
    """The GUI reads conversion_report.json, so the signal has to reach there
    too -- a number that only exists in a .txt nobody opens is not surfaced."""
    import json
    from src.auto_convert import write_conversion_report_json
    d = tmp_path / "ModA"
    d.mkdir()
    acr = AutoConvertResult(
        source_dir=d, output_dir=tmp_path,
        nif_results=[_cr("a.nif", _pf("_cap_weights_map")),
                     _cr("b.nif", _pf("registered_shape_undeclared_bones"))])
    out = write_conversion_report_json(tmp_path, [(d, acr, None)])
    assert out is not None, "the json vanished -- the new field raised"
    rep = json.loads(out.read_text())
    assert rep["pass_failures"] == {"_cap_weights_map": 1,
                                    "registered_shape_undeclared_bones": 1}


def test_the_json_is_still_written_when_nothing_failed(tmp_path):
    """Same blanket-except hazard as the .txt summary."""
    import json
    from src.auto_convert import write_conversion_report_json
    d = tmp_path / "ModA"
    d.mkdir()
    acr = AutoConvertResult(source_dir=d, output_dir=tmp_path,
                            nif_results=[_cr("a.nif")])
    out = write_conversion_report_json(tmp_path, [(d, acr, None)])
    assert out is not None
    assert json.loads(out.read_text())["pass_failures"] == {}
