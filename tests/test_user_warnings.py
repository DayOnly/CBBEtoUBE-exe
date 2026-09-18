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

"""#user-warnings -- one shape for every warning a user reads, no Python repr
in the log or the popup, and docs/WARNINGS.md pinned to the calls.

The lint here is negative-controlled: a copy of the batch module with one
injected `print(f"!! x {e!r}")` must be flagged, or the lint proves nothing.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import warning_surface as ws                       # noqa: E402
from src import auto_convert as ac                              # noqa: E402
from src import preflight as pf                                 # noqa: E402
from src.user_warnings import NOTE, PROBLEM, plain_error, warn  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LINTED = ("src/auto_convert.py", "src/nif_convert_writer.py", "src/user_warnings.py")


# --- the helper ------------------------------------------------------------------

def test_warn_prints_what_where_consequence_and_fix_in_the_house_shape(capsys):
    text = warn("could not write the report", where="under out",
                consequence="a run that dies leaves no report",
                fix="check that the folder is writable")
    out = capsys.readouterr().out
    assert out == ("  !! could not write the report -- under out\n"
                   "     a run that dies leaves no report\n"
                   "     FIX: check that the folder is writable\n")
    assert text == out.rstrip("\n")


def test_a_note_and_a_block_indent_keep_the_shape(capsys):
    warn("three options were added", level=NOTE, indent="\n  ")
    warn("POSTFLIGHT: 2 issues", consequence="listed below", indent="\n")
    out = capsys.readouterr().out
    assert out == ("\n  NOTE: three options were added\n"
                   "\n!! POSTFLIGHT: 2 issues\n   listed below\n")
    assert PROBLEM == "!!", "USING.md tells the user to look for this marker"


def test_plain_error_is_the_type_and_the_message_never_a_repr():
    # errno 13 is a PermissionError: the type the user sees is the real one
    assert plain_error(OSError(13, "Permission denied")) == "PermissionError: [Errno 13] Permission denied"
    assert plain_error(RuntimeError("boom")) == "RuntimeError: boom"
    assert plain_error(KeyError("k")) == "KeyError: 'k'"
    assert plain_error(ValueError()) == "ValueError"
    assert "(" not in plain_error(RuntimeError("boom")).replace("boom", "")


# --- no repr reaches a user --------------------------------------------------------

_REPR = re.compile(r"!r\}|\brepr\(")


def reprs_in_user_text(path: Path) -> list[int]:
    """Lines of `print(`, `warn(` or `_record_failure(` calls whose source
    carries a Python repr."""
    import ast
    src = path.read_text(encoding="utf-8")
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("print", "warn", "_record_failure"):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if _REPR.search(seg):
            hits.append(node.lineno)
    return hits


def test_no_user_facing_print_or_warning_carries_a_python_repr():
    bad = {rel: reprs_in_user_text(REPO / rel) for rel in LINTED}
    assert all(not v for v in bad.values()), {k: v for k, v in bad.items() if v}


def test_the_repr_lint_catches_an_injected_one(tmp_path):
    """The positive control: the lint must flag what it exists to flag."""
    copy = tmp_path / "auto_convert.py"
    shutil.copy(REPO / "src" / "auto_convert.py", copy)
    assert reprs_in_user_text(copy) == []
    copy.write_bytes(copy.read_bytes() + b'\n\ndef _planted(e):\n    print(f"!! x {e!r}")\n')
    assert reprs_in_user_text(copy) != [], "the lint let a planted repr through"


# --- the surface -----------------------------------------------------------------

def test_the_warning_surface_is_current_and_actionable():
    rows = ws.scan()
    assert len(rows) >= ws.FLOOR, f"only {len(rows)} warn() calls found -- the scan lost its population"
    assert ws.unactionable(rows) == [], (
        "a problem warning must say what it means or what to do: "
        + str([(r["module"], r["line"], r["what"]) for r in ws.unactionable(rows)]))
    want = ws.render(rows)
    have = ws.OUT.read_text(encoding="utf-8")
    assert have == want, (
        "docs/WARNINGS.md is out of date. Regenerate with "
        "`python scripts/warning_surface.py` and read the diff -- a changed surface "
        "means a warning was added, removed or reworded.")


def test_check_mode_exits_zero_when_current():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "warning_surface.py"),
                        "--check"], capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_generator_refuses_an_unactionable_warning(tmp_path):
    """A `!!` line with neither a consequence nor a fix is the class this
    exists to remove; the generator must not render it into the document."""
    fake = tmp_path / "src"
    fake.mkdir()
    (fake / "auto_convert.py").write_text(
        'from .user_warnings import warn\n\n\ndef f():\n    warn("something failed")\n',
        encoding="utf-8")
    (fake / "nif_convert_writer.py").write_text("", encoding="utf-8")
    rows = ws.scan(tmp_path)
    assert [r["what"] for r in rows] == ["something failed"]
    assert ws.unactionable(rows) == rows


# --- the recorder feeds the popup plain words ----------------------------------------

def _ns(sources, output):
    return argparse.Namespace(
        sources=[Path(s) for s in sources], output=Path(output), esp_name=None,
        no_textures=True, copy_textures=False, ube_body_ref=None, workers=1,
        unmerged_patch_subdir="_unmerged_patches", auto_merge=False,
        merged_name="CBBE_to_UBE_Combined.esp", render_previews=False,
        mods_root=None, no_winner_rebase=True, armo_winner_index=None,
        incremental=False, plugins_only=False)


def test_a_source_that_raises_is_recorded_in_plain_words(tmp_path, monkeypatch, capsys):
    """The failures file wording reaches the end-of-run popup verbatim, so the
    record must say `RuntimeError: the disk went away`, not the Python repr."""
    base = tmp_path
    mods = base / "mods"
    mods.mkdir()
    mod = base / "SomeMod"
    mod.mkdir()
    out = base / "out"
    settings = base / "CBBEtoUBE_settings.json"
    settings.write_text("{}", encoding="utf-8")
    for var in ("CBBE2UBE_MO2_INI", "CBBE2UBE_GAME_DATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CBBE2UBE_MODS_ROOT", str(mods))
    monkeypatch.setenv("CBBE2UBE_CONFIG", str(settings))
    monkeypatch.setenv("CBBE2UBE_RUN_LOG", str(base / "run.log"))
    monkeypatch.setattr(ac.paths, "enabled_mods", lambda lay: set())
    monkeypatch.setattr(ac, "_third_party_ube_covered_armos", lambda *a, **k: set())
    monkeypatch.setattr(pf, "_locate_in_mods_or_data", lambda *a, **k: base / "SkyPatcher.dll")
    monkeypatch.setattr(pf, "_skypatcher_armor_patching", lambda p: True)

    def _dies(source_dir, *a, **k):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(ac, "auto_convert_mod", _dies)
    ac._cmd_convert(_ns([mod], out))
    log = capsys.readouterr().out
    fails = json.loads((base / "CBBEtoUBE_last_failures.json").read_text(encoding="utf-8"))
    mine = [f for f in fails["failures"] if f["kind"] == "source failed"]
    assert mine and mine[0]["detail"] == "RuntimeError: the disk went away", mine
    assert "!! FAILED: RuntimeError: the disk went away" in log
    assert "RuntimeError('the disk went away')" not in log
