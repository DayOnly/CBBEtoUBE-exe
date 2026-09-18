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

"""#diagnostics-zip -- what Help > Save diagnostics zip packs.

Read in src/gui.py before this change: REPORT.txt, the log panel, settings,
exclusions, layout and preflight, and no run log, failures file, output-mod
report or page-file setting -- the files REPORTING.md asks a reporter for. The
zip is built in a Tk worker, so the collection lives in src/diagnostics.py and
is tested here with planted files."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = ("REPORTING.md", "CONTRIBUTING.md")
_MEMORY = {"total_gb": 32.0, "avail_gb": 12.0, "commit_limit_gb": 64.0, "commit_free_gb": 40.0}


def _diag():
    from src import diagnostics
    return diagnostics


@pytest.mark.parametrize("value, want", [
    (None, "unknown"),
    ([], "none -- the page file is OFF"),
    (["?:\\pagefile.sys"], "system-managed on every drive"),
    (["c:\\pagefile.sys 0 0"], "system-managed: c:\\pagefile.sys"),
    (["c:\\pagefile.sys 40960 40960", "d:\\pagefile.sys 1024 4096"],
     "fixed size: c:\\pagefile.sys 40960-40960 MB; d:\\pagefile.sys 1024-4096 MB"),
    (["c:\\pagefile.sys 0 0", "d:\\pagefile.sys 1024 4096"],
     "mixed: d:\\pagefile.sys 1024-4096 MB; c:\\pagefile.sys system-managed"),
])
def test_the_page_file_mode_is_read_the_way_windows_writes_it(value, want):
    assert _diag().page_file_mode(value).startswith(want)


def test_the_zip_carries_what_the_reporting_guide_asks_for(tmp_path):
    d = _diag()
    tool, out = tmp_path / "tool", tmp_path / "out"
    tool.mkdir()
    out.mkdir()
    planted = {}
    for folder, names in ((tool, d.TOOL_FILES), (out, d.OUTPUT_FILES)):
        for name in names:
            planted[name] = f"contents of {name}".encode()
            (folder / name).write_bytes(planted[name])
    (tool / "CBBEtoUBE_settings.json").write_bytes(b"{}")     # packed elsewhere
    got = d.collect(tool, out, memory=_MEMORY, paging_files=["?:\\pagefile.sys"])
    assert {k: v for k, v in got.items() if k != d.MACHINE} == planted
    for must in ("CBBEtoUBE_previous_run.log", "CBBEtoUBE_last_failures.json",
                 "conversion_report.json", "conversion_settings.json"):
        assert must in got, f"{must} is not in the zip"
    text = got[d.MACHINE].decode("utf-8")
    assert "page file: system-managed on every drive" in text, text
    assert "commit limit (RAM + page file): 64.0 GB, 40.0 GB free now" in text, text


def test_a_file_no_run_has_written_is_left_out(tmp_path):
    d = _diag()
    (tmp_path / "CBBEtoUBE_last_run.log").write_bytes(b"log")
    got = d.collect(tmp_path, tmp_path / "no_output_yet", memory={}, paging_files=[])
    assert set(got) == {"CBBEtoUBE_last_run.log", d.MACHINE}
    assert "page file: none" in got[d.MACHINE].decode("utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="the page file setting is in the Windows registry")
def test_machine_txt_reads_this_machine(tmp_path):
    text = _diag().collect(tmp_path, None)["machine.txt"].decode("utf-8")
    assert "page file: " in text and "unknown" not in text, text
    assert "RAM: ?" not in text, text


def test_the_gui_packs_what_collect_returns():
    gui = (REPO / "src" / "gui.py").read_text(encoding="utf-8")
    assert "diagnostics.collect(" in gui


@pytest.mark.parametrize("doc", DOCS)
def test_the_docs_list_every_file_the_zip_adds(doc):
    d = _diag()
    text = (REPO / doc).read_text(encoding="utf-8")
    missing = [n for n in d.TOOL_FILES + d.OUTPUT_FILES + (d.MACHINE,) if f"`{n}`" not in text]
    assert not missing, f"{doc} does not list {missing} among the zip's contents"
