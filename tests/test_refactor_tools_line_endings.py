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

"""#lf-only: the two refactoring tools read the real src/nif_convert.py.

scripts/split_move.py and scripts/extract_loop.py both asserted that the file
was CRLF. It has been LF since 2026-09-02, so each stopped on its first line of
work. Each is run here with arguments that fail just after the file is parsed
-- a name that does not exist -- so nothing is ever written."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "nif_convert.py"


def _run(script, *args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / script), *args],
                          capture_output=True, text=True, cwd=str(REPO), timeout=300)


def test_split_move_reads_the_real_file():
    before = SRC.read_bytes()
    r = _run("split_move.py", "--module", "nif_convert_probe", "--step", "probe",
             "--doc", "probe", "--names", "__no_such_function__", "--dry")
    assert "not found at module level: ['__no_such_function__']" in r.stderr, r.stderr[-2000:]
    assert SRC.read_bytes() == before
    assert not (REPO / "src" / "nif_convert_probe.py").exists()


def test_extract_loop_reads_the_real_file():
    before = SRC.read_bytes()
    r = _run("extract_loop.py", "--fn", "__no_such_function__", "--loop-line", "1",
             "--name", "probe", "--doc", "probe", "--dry")
    assert "KeyError: '__no_such_function__'" in r.stderr, r.stderr[-2000:]
    assert SRC.read_bytes() == before
