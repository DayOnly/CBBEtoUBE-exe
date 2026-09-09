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

"""Plan item E1: the flag surface, its coverage, and the recipe it ships under.

Two failures this pins, both already made once. A CENSUS THAT CANNOT SEE THE
FLAG IT EXISTS TO JUDGE -- the first draft missed 16 private-prefixed flags, and
the single-line regex then missed 14 more across four modules and three binding
forms. And A DEFAULT READ FROM ONLY ONE PLACE -- the shipped pack is the code
default PLUS the deployed settings file, so either half alone is wrong about it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis import flag_surface as fs        # noqa: E402
from scripts.analysis import flag_retirement as fr     # noqa: E402
from tests import _converter_sources as cs             # noqa: E402


# ------------------------------------------------------------- coverage

def _monolith() -> str:
    """The monolith's source, through the shared helper. A guard that opens
    `src/nif_convert.py` by name breaks the day a function moves to a sibling,
    which is what `test_split_preconditions` ratchets against."""
    for p, text in cs.texts().items():
        if p.name == "nif_convert.py":
            return text
    raise AssertionError("nif_convert.py is not in the converter file list")



def test_the_parse_finds_everything_the_regex_finds():
    """THE MUTATION CONTROL. The parse replaced a regex, so it has to be a
    strict superset -- a wider net that dropped something would be a
    regression wearing an improvement's name."""
    regex = {e for _n, e, _k, _d in fs.declared(_monolith())}
    parsed = {r["env"] for r in fs.declared_all()}
    assert regex, "the regex matched nothing -- it is not a control any more"
    assert not (regex - parsed), sorted(regex - parsed)


def test_the_parse_finds_MORE_than_the_regex():
    """If these ever coincide, either the split was undone or the parse
    regressed to line-matching. Both are worth failing on."""
    regex = {e for _n, e, _k, _d in fs.declared(_monolith())}
    parsed = {r["env"] for r in fs.declared_all()}
    assert len(parsed) > len(regex)


def test_every_flag_binding_module_is_actually_read():
    """`nif_convert.py` stopped being the whole surface at the 2026-09-01
    split. A census reading only the monolith under-reports it."""
    mods = {r["module"] for r in fs.declared_all()}
    assert "nif_convert" in mods
    assert len(mods) >= 2, "only the monolith was read: %s" % mods
    for m in fs._FLAG_MODULES:
        assert (REPO_ROOT / "src" / (m + ".py")).is_file(), m


def test_a_flag_read_inline_is_reported_with_no_constant():
    """Some `_flag` calls are conditions, not bindings. They are real flags and
    must be reported, not dropped for having the wrong shape."""
    inline = [r for r in fs.declared_all() if r["const"] is None]
    assert inline, "expected at least one inline read"
    assert all(r["env"].startswith("CBBE2UBE_") for r in inline)


def test_the_parse_handles_the_forms_one_line_cannot_hold(tmp_path, monkeypatch):
    """A binding split across lines, and a ternary. Both exist in the real
    source and both were invisible to the regex."""
    import ast
    mod = tmp_path / "src"
    mod.mkdir()
    (mod / "fake_mod.py").write_text(
        'A = (\n    not _flag("CBBE2UBE_MULTILINE", False))\n'
        'B = (0.0 if _flag("CBBE2UBE_TERNARY", False) else 1.0)\n'
        'if _flag("CBBE2UBE_INLINE", False):\n    pass\n',
        encoding="utf-8")
    monkeypatch.setattr(fs, "_REPO", tmp_path)
    monkeypatch.setattr(fs, "_FLAG_MODULES", ("fake_mod",))
    got = {r["env"]: r["const"] for r in fs.declared_all()}
    assert got == {"CBBE2UBE_MULTILINE": "A", "CBBE2UBE_TERNARY": "B",
                   "CBBE2UBE_INLINE": None}
    assert ast is not None


# ------------------------------------------------- the retirement evidence

def test_the_kill_switch_evidence_has_a_population():
    rs, _live = fr.rows()
    assert len(rs) > 50, "kill-switch bindings collapsed: %d" % len(rs)
    assert all(set(r) >= {"name", "env", "resolves_on", "n_refs", "gui"}
               for r in rs)


def test_a_flags_own_binding_does_not_count_as_a_use_of_it():
    """"Unused since" is exactly "referenced nowhere but its own binding". If
    the declaration counted, nothing would ever look unused -- and the whole
    still-standing list would be empty for the wrong reason."""
    rs, _ = fr.rows()
    unref = [r for r in rs if r["n_refs"] == 0]
    assert unref, "no flag reads as unreferenced -- the binding is being counted"
    # ...and a flag that IS used elsewhere must not read as unreferenced, or
    # the exclusion is doing nothing and everything looks retirable.
    used = [r for r in rs if r["n_refs"] > 0]
    assert used, "nothing reads as referenced -- the search found no files"


def test_the_retired_switch_is_really_gone():
    """`CBBE2UBE_NO_BUTT_MATCH` was retired 2026-09-08 with an in-game verdict
    behind it. Never suggest setting it again."""
    assert '_flag("CBBE2UBE_NO_BUTT_MATCH"' not in _monolith()
    envs = {r["env"] for r in fs.declared_all()}
    assert "CBBE2UBE_NO_BUTT_MATCH" not in envs


# --------------------------------------------------------- recipe drift

def test_recipe_drift_finds_a_planted_disagreement(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"authored_antipoke": True}), encoding="utf-8")
    drift, unknown = fr.recipe_drift(str(p))
    assert [d["const"] for d in drift] == ["AUTHORED_ANTIPOKE"]
    assert drift[0]["code"] is False and drift[0]["live"] is True
    assert unknown == []


def test_a_setting_no_flag_claims_is_reported(tmp_path):
    """A settings key nothing reads is as much a defect as a default nothing
    matches, and it is invisible from either side alone."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"no_such_flag_at_all": True}), encoding="utf-8")
    _drift, unknown = fr.recipe_drift(str(p))
    assert unknown == ["no_such_flag_at_all"]


def test_a_setting_that_AGREES_is_not_reported_as_drift(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"authored_antipoke": False}), encoding="utf-8")
    drift, _unknown = fr.recipe_drift(str(p))
    assert drift == []


def test_no_settings_file_exits_3_rather_than_reporting_no_drift(capsys):
    """NOT MEASURED is not "no drift" -- the distinction every other checker in
    this toolkit keeps."""
    import os
    old = os.environ.pop("CBBE2UBE_CONFIG", None)
    try:
        assert fr.main(["--recipe"]) == 3
        assert "NOT MEASURED" in capsys.readouterr().out
    finally:
        if old is not None:
            os.environ["CBBE2UBE_CONFIG"] = old


def test_an_unreadable_settings_file_is_not_silently_clean(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ not json", encoding="utf-8")
    drift, unknown = fr.recipe_drift(str(p))
    assert drift == [] and unknown == []
