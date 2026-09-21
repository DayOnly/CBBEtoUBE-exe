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

"""Tools that reported SUCCESS having examined nothing.

An AST census over the 101 script paths in docs/TOOL_MAP.md found six runnable
tools with no nonzero exit path at all (the sixth, build_body_collider_proxy,
has only an argparse usage guard). Pointed at an empty pack, every one of them
returned 0:

    scan_output_health          rc=0  "=== SCAN DONE ==="
    disable_unconstrained_smp   rc=0  "(dry-run; pass --apply to rename)"
    build_body_collider_proxy   rc=0  "processed 0 NIFs"
    strip_nude_handfeet         rc=0  "need esp path"
    scan_nude_skin_chain        rc=0  "FATAL: no UBE_AllRace.esp found"

The last one prints the word FATAL and exits 0. The first prints an
affirmative all-clear over zero NIFs -- the same shape as the bust_verdict
false clean, where a human read "clean at rest" off a tool that had measured
nine vertices of a garment 42u from the body.

These tests spawn the real tools and assert on the REAL exit code, because the
exit code is the thing that was wrong. Asserting on a sliced-out predicate
would pass while the tool still returned 0.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _run(rel_script, *args, env_extra=None, cwd=None):
    """Run a tool as a subprocess; return (rc, combined output)."""
    env = dict(os.environ)
    env.pop("CBBE2UBE_MODS_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(_REPO / rel_script), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(cwd or _REPO), timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


@pytest.fixture()
def empty_pack(tmp_path):
    """An output-mod layout with no meshes and no ESP -- the wrong-dir case."""
    (tmp_path / "meshes").mkdir()
    return tmp_path


# ------------------------------------------------------- scan_output_health
def test_health_scan_over_zero_nifs_is_not_a_pass(empty_pack):
    """THE MEASURED DEFECT: rc=0 and "=== SCAN DONE ===" over an empty dir."""
    rc, out = _run("scripts/scan_output_health.py", empty_pack)
    assert rc != 0, "a health scan that read no NIFs must not report success"
    assert rc == 3, "3 = measured nothing, distinct from 1 = found defects"
    assert "0/0 is not a pass" in out


def test_health_scan_never_prints_a_bare_all_clear(empty_pack):
    """The old line said DONE whether it had read 2064 NIFs or none.

    Any verdict line must now carry the population, so "clean" cannot be read
    off a run that had nothing to be clean about.
    """
    rc, out = _run("scripts/scan_output_health.py", empty_pack)
    assert "=== SCAN DONE ===" not in out
    assert "SCAN CLEAN" not in out


def test_a_real_defect_outranks_an_incomplete_scope(tmp_path):
    """A skipped INVISIBLE pass must not downgrade a LOAD-FAIL to "fix paths".

    Precedence matters: this exact ordering was wrong in the first draft of
    the fix and reported a genuine corrupt NIF as exit 3.
    """
    meshes = tmp_path / "meshes" / "!UBE"
    meshes.mkdir(parents=True)
    (meshes / "broken_1.nif").write_bytes(b"not a nif")   # forces LOAD-FAIL
    rc, out = _run("scripts/scan_output_health.py", tmp_path)
    assert rc == 1, "found a defect -> 1, even though INVISIBLE could not run"
    assert "SCAN FOUND DEFECTS" in out
    assert "INVISIBLE pass NOT RUN" in out, "the skipped pass is still named"


def test_the_verdict_line_states_the_population(tmp_path):
    meshes = tmp_path / "meshes" / "!UBE"
    meshes.mkdir(parents=True)
    (meshes / "broken_1.nif").write_bytes(b"not a nif")
    _rc, out = _run("scripts/scan_output_health.py", tmp_path)
    assert "1 NIF(s)" in out, "the count must appear in the verdict, not just the log"


# ---------------------------------------------------- scan_nude_skin_chain
def test_fatal_does_not_exit_zero(tmp_path):
    """It printed FATAL and `return`ed. A shell read that as success."""
    (tmp_path / "mods").mkdir()
    (tmp_path / "profiles").mkdir()
    rc, out = _run("scripts/analysis/scan_nude_skin_chain.py",
                   env_extra={"CBBE2UBE_MODS_ROOT": str(tmp_path)})
    assert "FATAL" in out, "fixture no longer reaches the FATAL path"
    assert rc != 0, "a tool that prints FATAL must not report success"
    assert rc == 3


def test_the_fatal_guard_is_reachable_and_not_dead(tmp_path):
    """Self-check on the fixture: a guard that never fires proves nothing.

    If the tool ever stops looking under CBBE2UBE_MODS_ROOT this fixture would
    silently stop exercising the guard, and the test above would pass for the
    wrong reason.
    """
    (tmp_path / "mods").mkdir()
    (tmp_path / "profiles").mkdir()
    _rc, out = _run("scripts/analysis/scan_nude_skin_chain.py",
                    env_extra={"CBBE2UBE_MODS_ROOT": str(tmp_path)})
    assert "UBE_AllRace.esp providers on disk: 0" in out
