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

"""#tool-folder-only -- the tool keeps nothing outside its own folder.

A user replaced the tool folder entirely and found their exclusions still there:
from 1.1 to 1.4.1 the exclusions file lived under %LOCALAPPDATA%. The decision
(2026-09-15) is that nothing the tool writes goes under AppData at all -- and
the Windows temp folder is inside AppData too -- so replacing the folder resets
the tool completely.

These guards cover the SHIPPED code: the entry point and src/. Developer tools
under scripts/ are not bundled into the exe and are not covered. The exclusions
file itself is pinned in tests/test_exclusions.py."""
import ast
import importlib.util
import os
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# tempfile functions that pick a folder themselves unless given `dir`, mapped to
# the positional index of their `dir` parameter.
_DIR_ARG = {"mkdtemp": 2, "mkstemp": 2, "TemporaryDirectory": 2,
            "NamedTemporaryFile": 6, "TemporaryFile": 6,
            "SpooledTemporaryFile": 7}
# tempfile functions that always resolve to the temp folder.
_ALWAYS_TEMP = {"gettempdir", "gettempdirb", "mktemp"}
# Environment variables that name a folder under AppData.
_APPDATA_ENV = {"LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "TMPDIR"}


def _appdata_hits(source: str, label: str) -> "list[str]":
    """Every place in `source` that can reach AppData or the temp folder."""
    tree = ast.parse(source)
    mod_aliases: "set[str]" = set()
    fn_aliases: "dict[str, str]" = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "tempfile":
                    mod_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "tempfile":
            for a in node.names:
                fn_aliases[a.asname or a.name] = a.name
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = None
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in mod_aliases):
                name = f.attr
            elif isinstance(f, ast.Name) and f.id in fn_aliases:
                name = fn_aliases[f.id]
            if name in _ALWAYS_TEMP:
                hits.append(f"{label}:{node.lineno} tempfile.{name}()")
            elif name in _DIR_ARG:
                has_dir = (any(k.arg == "dir" for k in node.keywords)
                           or len(node.args) > _DIR_ARG[name])
                if not has_dir:
                    hits.append(f"{label}:{node.lineno} tempfile.{name}() "
                                "without dir=")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and node.value in _APPDATA_ENV):
            hits.append(f"{label}:{node.lineno} {node.value!r}")
    return hits


def _shipped_sources() -> "list[Path]":
    return [REPO / "cbbe_to_ube_main.py"] + sorted((REPO / "src").glob("*.py"))


def test_the_scanner_can_fail():
    """A detector that reports zero is the kind most likely to be broken."""
    bad = {
        "gettempdir": "import tempfile\ntempfile.gettempdir()\n",
        "mkdtemp without dir": "import tempfile\ntempfile.mkdtemp(prefix='x')\n",
        "aliased module": "import tempfile as t\nt.mkstemp()\n",
        "from-import alias": "from tempfile import mkdtemp as m\nm()\n",
        "env read": "import os\nos.environ.get('LOCALAPPDATA')\n",
        "env subscript": "import os\nos.environ['TEMP']\n",
    }
    for label, src in bad.items():
        assert _appdata_hits(src, label), f"the scanner missed: {label}"
    good = ["import tempfile\ntempfile.mkstemp(dir='d')\n",
            "import tempfile as t\nt.mkdtemp('', 'x', 'd')\n",
            "x = 'TEMPLATE'\n"]
    for src in good:
        assert not _appdata_hits(src, "good"), f"false positive: {src!r}"


def test_shipped_code_writes_nothing_under_appdata():
    files = _shipped_sources()
    assert len(files) > 30, f"only {len(files)} source files found -- wrong root?"
    hits = []
    for p in files:
        hits += _appdata_hits(p.read_text(encoding="utf-8"), p.name)
    assert not hits, ("shipped code reaches AppData or the temp folder:\n  "
                      + "\n  ".join(hits))


def test_the_vendored_temp_helpers_stay_unreachable():
    """pynifly's tmp_filepath() writes under the temp folder. It is reached only
    through its tmp_copy() and its xmltools (HKX conversion), and the converter
    calls neither. Pin that, rather than patch a vendored library."""
    vendored = REPO / ".pynifly" / "pyn" / "niflytools.py"
    if vendored.is_file():      # the premise: that helper really uses %TEMP%
        assert "gettempdir" in vendored.read_text(encoding="utf-8")
    for p in _shipped_sources():
        text = p.read_text(encoding="utf-8")
        for name in ("xmltools", "tmp_copy", "tmp_filepath"):
            assert name not in text, f"{p.name} now reaches pynifly's {name}"


def _entry_module():
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_tool_folder", REPO / "cbbe_to_ube_main.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_run_log_has_no_temp_folder_fallback():
    m = _entry_module()
    temp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    cands = [os.path.normcase(os.path.abspath(c))
             for c in m._log_dir_candidates()]
    assert cands, "the run log has no folder at all"
    assert temp not in cands, "the run log still falls back to the temp folder"


def test_an_unwritable_tool_folder_says_there_is_no_log(monkeypatch, capsys,
                                                         tmp_path):
    """No fallback must not mean no explanation."""
    m = _entry_module()
    missing = tmp_path / "no" / "such" / "folder"
    monkeypatch.setattr(m, "_log_dir_candidates", lambda: [str(missing)])
    monkeypatch.delenv("CBBE2UBE_RUN_LOG", raising=False)
    monkeypatch.setattr(m.sys, "argv", ["CBBEtoUBE.exe", "auto"])
    before = (m.sys.stdout, m.sys.stderr)
    try:
        m._install_log_tee()
        assert (m.sys.stdout, m.sys.stderr) == before, (
            "the console streams were replaced although no log was opened")
    finally:
        m.release_log_tee()
    assert "keeps no log file" in capsys.readouterr().err
    assert not missing.exists()


def test_the_setup_check_index_refuses_to_write(monkeypatch, tmp_path):
    """The setup check builds its BSA index with no staging folder. It only asks
    contains(), but an index without a folder must refuse to extract, so nothing
    can ever be written for it. It used to name a folder under %TEMP%."""
    from src import auto_convert as ac
    from src import bsa_strings

    class _Archive:
        def __init__(self, *a, **k):
            pass

        def read_file(self, name):
            return b"nif bytes"

    monkeypatch.setattr(bsa_strings, "BSAArchive", _Archive)
    key = "armor/x/y_1.nif"
    hit = (tmp_path / "a.bsa", "meshes/armor/x/y_1.nif")

    # Control: WITH a staging folder the same fake really does write, so the
    # None below is the guard and not an artefact of the fake.
    control = ac._BsaMeshIndex([], tmp_path / "staging")
    control._index = {key: hit}
    got = control.extract(key)
    assert got is not None and Path(got[0]).is_file()

    lookup_only = ac._BsaMeshIndex([], None)
    lookup_only._index = {key: hit}
    before = sorted(tmp_path.rglob("*"))
    assert lookup_only.extract(key) is None
    assert sorted(tmp_path.rglob("*")) == before, "a lookup-only index wrote"


def test_the_glow_debug_log_defaults_to_the_tool_folder(monkeypatch, tmp_path):
    from src import atomic_io, paths
    shape = types.SimpleNamespace(
        name="Glow", properties=types.SimpleNamespace(controllerID=7,
                                                      shaderPropertyID=7))
    nif = types.SimpleNamespace(shapes=[shape])
    monkeypatch.setenv("CBBE2UBE_DEBUG_GLOW_CTRL", "1")
    monkeypatch.delenv("CBBE2UBE_GLOW_LOG", raising=False)
    monkeypatch.setattr(paths, "tool_dir", lambda: tmp_path)
    atomic_io._debug_glow_controller_check(nif, tmp_path / "out.nif")
    assert (tmp_path / "CBBEtoUBE_glowdebug.log").is_file()


def test_overlay_work_folders_live_in_the_output_mod(tmp_path):
    from src import overlay_transfer as ot
    out = tmp_path / "Output Mod"
    a = ot._new_work_dir(out, "ube_feet_")
    b = ot._new_work_dir(out, "ube_pex_")
    assert a.parent == b.parent == out / "_overlay_work"
    ot._drop_work_dir(a)
    assert not a.exists() and b.is_dir(), "dropping one pass removed another"
    ot._drop_work_dir(b)
    assert not (out / "_overlay_work").exists(), (
        "the empty work folder was left behind in the output mod")
    assert out.is_dir()
