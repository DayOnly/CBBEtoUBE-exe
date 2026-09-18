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

"""Code in src/ must be reached from src/, the entry point or a script. #dead-code-ratchet

vulture ran in no test and no CI step, so unreferenced code only accumulated.
Measured 2026-09-15 (vulture 2.16, 60% confidence, scripts counted as callers):
29 findings under src/ -- a module nothing imported, functions only their own
tests called, a nested helper nothing called, never-read locals -- and the rest
were writes a Windows API or the NIF file reads, or tables the suite pins. Those
are listed, each with its reason, in vulture_whitelist.py.

A function only a test calls reads as shipped behaviour while describing nothing
the exe does, so tests are deliberately NOT counted as callers.
"""
import re
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
CONFIDENCE = "60"
_FINDING = re.compile(r"^(.+?):(\d+): unused (\w+) '([^']+)' \((\d+)% confidence")


def vulture(paths, cwd, confidence=CONFIDENCE):
    """(returncode, findings, output). vulture exits 0 when clean and 3 with
    findings; anything else -- not installed, a syntax error -- is no verdict."""
    r = subprocess.run([sys.executable, "-m", "vulture", *paths, "--min-confidence", confidence],
                       cwd=str(cwd), capture_output=True, text=True)
    found = [m.groups() for m in map(_FINDING.match, r.stdout.splitlines()) if m]
    return r.returncode, found, r.stdout + r.stderr


def in_src(findings):
    return [f"{p}:{ln} unused {kind} {name!r}" for p, ln, kind, name, _c in findings
            if p.replace("\\", "/").startswith("src/")]


def test_nothing_in_src_is_dead():
    rc, found, out = vulture(["src", "scripts", "cbbe_to_ube_main.py", "vulture_whitelist.py"], PROJ)
    assert rc in (0, 3), f"vulture did not run (rc {rc}); pip install vulture==2.16\n{out[-800:]}"
    dead = in_src(found)
    assert not dead, ("dead code under src/. Delete it -- or, if a Windows API, the NIF file "
                      "or the suite really reads it, whitelist it WITH that reason:\n  "
                      + "\n  ".join(dead))


def test_the_ratchet_sees_a_planted_dead_function(tmp_path):
    """Control: a never-called function is a 60%-confidence finding, so the ratchet
    runs at 60. At 80 -- the level first proposed for this step -- it is not
    reported at all. Findings outside src/ are not the ratchet's business."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "planted.py").write_text("def _probe():\n    pass\n", encoding="utf-8")
    rc, found, out = vulture(["src"], tmp_path)
    assert rc == 3 and [(k, n) for _p, _l, k, n, _c in found] == [("function", "_probe")], out
    assert in_src(found) and in_src([("src\\x.py", "1", "function", "f", "60")])
    assert not in_src([("scripts/x.py", "1", "function", "f", "60")])
    rc80, found80, out80 = vulture(["src"], tmp_path, confidence="80")
    assert rc80 == 0 and not found80, out80


def test_every_whitelist_entry_names_something_that_exists():
    """An entry for a deleted name is a standing exemption that can only hide a
    future finding under that name (the whitelist's own header says so)."""
    names = set()
    for line in (PROJ / "vulture_whitelist.py").read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            names.add(code.split(".")[-1])
    assert len(names) >= 15, f"only {len(names)} whitelist names parsed"
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for d in ("src", "scripts", "tests") for p in (PROJ / d).rglob("*.py")
                       if "__pycache__" not in p.parts)
    missing = sorted(n for n in names if not re.search(rf"(?<!\w){re.escape(n)}(?!\w)", corpus))
    assert not missing, f"whitelist entries for names nothing in src/scripts/tests has: {missing}"
