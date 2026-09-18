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

"""A test file must not run its own tests when it is imported. #collection-side-effects

Six files ended with their test functions called at module level -- 16 calls,
most handing over a directory beside this folder in place of tmp_path. Each of
those tests ran during COLLECTION (where a failure is a collection error that
hides the whole file), ran again under pytest, and left a directory in tests/
that nothing removed: 25 of them on 2026-09-15, 16 last written on 2026-07-05.
"""
import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
_TMP_BESIDE_TESTS = re.compile(r"__file__\).*/\s*[\"']_tmp_")


def module_level_test_calls(source: str) -> "list[tuple[int, str]]":
    """(line, name) for every `test_*(...)` call made at module level."""
    out = []
    for node in ast.parse(source).body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id.startswith("test_")):
            out.append((node.lineno, node.value.func.id))
    return out


def _test_files():
    files = sorted(TESTS.glob("test_*.py"))
    assert len(files) >= 250, f"only {len(files)} test files seen -- the glob drifted"
    return files


def test_no_test_file_calls_its_tests_at_import():
    found = [f"{p.name}:{ln} {name}()" for p in _test_files()
             for ln, name in module_level_test_calls(p.read_text(encoding="utf-8"))]
    assert not found, ("these test files run tests at import, during collection:\n  "
                       + "\n  ".join(found))


def test_no_test_writes_a_directory_beside_the_tests():
    here = Path(__file__).name
    found = [p.name for p in _test_files() if p.name != here
             and _TMP_BESIDE_TESTS.search(p.read_text(encoding="utf-8"))]
    assert not found, f"these tests write a _tmp_ directory into tests/: {found}"


def test_the_checks_find_what_they_are_for():
    """Controls: a planted module-level call and a planted directory are found,
    and a call made INSIDE a function is not."""
    planted = "def test_x(tmp_path):\n    pass\n\n\ntest_x(None)\n"
    assert module_level_test_calls(planted) == [(5, "test_x")]
    assert module_level_test_calls("def test_x():\n    test_y()\n") == []
    assert _TMP_BESIDE_TESTS.search('test_x(\n    Path(__file__).resolve().parent / "_tmp_x")')
