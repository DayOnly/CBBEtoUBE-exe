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

"""#build-identity -- scripts/build_identity.py and `CBBEtoUBE.exe --version`.

Measured on the 1.4.1 exe: `--version` was rejected with exit 2 AND still wrote
a run log beside the exe; the PE version resource was blank; the stamp named
no toolchain; and the stamp was written by build_exe.ps1, so a direct
`pyinstaller CBBEtoUBE.spec` reused a stale one (a committed exe once shipped
that way). No PyInstaller build happens here -- the frozen exe is checked by a
scratch build when this changes."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_identity as bi
from scripts import release_gate as rg

REPO = Path(__file__).resolve().parents[1]


# --- versions ---------------------------------------------------------------

@pytest.mark.parametrize("text, want", [
    ("1.4.1", (1, 4, 1, 0)), ("1.4", (1, 4, 0, 0)), ("1.3-alpha", (1, 3, 0, 0)),
    ("2", (2, 0, 0, 0)), ("1.2.3.4.5", (1, 2, 3, 4)), ("1.99999", (1, 65535, 0, 0)),
])
def test_version_tuple(text, want):
    assert bi.version_tuple(text) == want


def test_version_from_source_reads_without_importing():
    src = '"""doc"""\nimport os\n__version__ = "1.4.2"\n'
    assert bi.version_from_source(src) == "1.4.2"
    assert bi.version_from_source(src.encode()) == "1.4.2"
    assert bi.version_from_source("x = 1\n") is None
    assert bi.version_from_source(None) is None


def test_the_tracked_version_file_parses():
    v = bi.version_from_source((REPO / "src" / "version.py").read_bytes())
    assert v and bi.version_tuple(v)[0] >= 1


# --- the stamp ------------------------------------------------------------------

def _g(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=gate", "-c",
                        "user.email=gate", "-c", "commit.gpgsign=false",
                        "-c", "core.hooksPath=no-hooks", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    r = tmp_path / "repo"
    for rel in ("src/app.py", "assets/icon.ico", ".pynifly/pyn/x.py", "docs/notes.md"):
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original\n", encoding="utf-8")
    _g(r, "init", "-q", "-b", "main")
    _g(r, "add", "-A")
    _g(r, "commit", "-q", "-m", "source")
    return r


def test_a_clean_tree_stamps_clean(repo):
    stamp = bi.build_stamp(repo, now=0)
    assert stamp["git"] == _g(repo, "rev-parse", "--short", "HEAD")
    assert stamp["dirty"] is False
    assert stamp["built_utc"] == "1970-01-01T00:00:00Z"


@pytest.mark.parametrize("rel", ["src/app.py", "assets/icon.ico", ".pynifly/pyn/x.py"])
def test_a_change_to_any_build_input_stamps_dirty(repo, rel):
    """The old check looked at src, the spec and the entry point only -- a
    changed DLL wrapper or icon built a "clean" exe."""
    (repo / rel).write_text("changed\n", encoding="utf-8")
    assert bi.build_stamp(repo, now=0)["dirty"] is True


def test_a_change_outside_the_build_inputs_stays_clean(repo):
    (repo / "docs" / "notes.md").write_text("changed\n", encoding="utf-8")
    assert bi.build_stamp(repo, now=0)["dirty"] is False


def test_source_date_epoch_pins_the_build_time(repo, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1757894400")
    assert bi.build_stamp(repo)["built_utc"] == "2025-09-15T00:00:00Z"


def test_the_stamp_records_the_epoch_it_was_pinned_to(repo, monkeypatch):
    """Two builds of one commit differed in the exe alone, by 180 bytes -- two PE
    timestamps, the checksum and the stamp; with SOURCE_DATE_EPOCH pinned they
    were identical across all 1,121 bundle files (MEASURED 2026-09-16). The
    stamp records the value so the release gate can check it is the commit's
    own time, and a rebuild elsewhere can reproduce the exe. #reproducible-build"""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1757894400")
    stamp = bi.build_stamp(repo)
    assert stamp["source_date_epoch"] == 1757894400
    assert stamp["built_utc"] == "2025-09-15T00:00:00Z"
    monkeypatch.delenv("SOURCE_DATE_EPOCH")
    assert bi.build_stamp(repo, now=0)["source_date_epoch"] is None


def test_the_build_script_pins_the_epoch_to_the_commit():
    """The value is HEAD's commit time, so the release workflow's rebuild of the
    stamp commit reproduces the tracked bundle; a value already in the
    environment is kept."""
    ps1 = (REPO / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    assert "log -1 --format=%ct HEAD" in ps1, "the epoch no longer comes from the commit"
    assert "$env:SOURCE_DATE_EPOCH = $epoch" in ps1
    assert ps1.index("SOURCE_DATE_EPOCH") < ps1.index("-m PyInstaller"), (
        "the epoch must be set before PyInstaller runs")


def test_the_stamp_names_its_toolchain(repo):
    tools = bi.build_stamp(repo, now=0)["toolchain"]
    assert tools["python"] == sys.version.split()[0]
    assert {"pyinstaller", "numpy", "scipy", "lz4"} <= set(tools)


def test_the_release_gate_reads_back_what_the_build_writes(repo, tmp_path):
    """The gate reads the stamp out of the exe by walking bytecode. A stamp it
    cannot walk -- the nested toolchain dict, say -- would make every future
    release read "no build stamp"."""
    stamp = bi.build_stamp(repo, now=0)
    path = bi.write_stamp(tmp_path / "_build_stamp.py", stamp)
    code = compile(path.read_text(encoding="ascii"), str(path), "exec")
    assert rg.module_constants(code).get("BUILD") == stamp


def test_the_spec_generates_the_stamp_itself():
    """So a direct `pyinstaller CBBEtoUBE.spec` cannot reuse a stale stamp."""
    spec = (REPO / "CBBEtoUBE.spec").read_text(encoding="utf-8")
    for call in ("build_identity.build_stamp(", "build_identity.write_stamp(",
                 "version=build_identity.version_resource(",
                 "build_identity.write_manifest("):
        assert call in spec, f"the spec no longer calls {call}"
    ps1 = (REPO / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    assert "BUILD = {" not in ps1, "build_exe.ps1 still writes its own stamp"


def test_the_gate_and_the_build_share_one_input_list():
    assert rg.BUILD_INPUTS is bi.BUILD_INPUTS


def test_the_running_build_reports_its_toolchain(monkeypatch):
    from src import build_info
    tools = {"python": "3.10.6", "pyinstaller": "6.20.0"}
    monkeypatch.setattr(build_info, "_BUILD", {"git": "1a2b3c4", "toolchain": tools})
    assert build_info.stamp()["toolchain"] == tools


# --- the version resource --------------------------------------------------

def test_the_version_resource_carries_the_version():
    pytest.importorskip("PyInstaller")
    stamp = {"git": "1a2b3c4", "dirty": False, "built_utc": "x"}
    res = bi.version_resource("1.4.2", stamp)
    assert res.ffi.fileVersionMS == (1 << 16) | 4 and res.ffi.fileVersionLS == 2 << 16
    strings = {s.name: s.val for s in res.kids[0].kids[0].kids}
    assert strings["ProductVersion"] == "1.4.2"
    assert strings["FileVersion"] == "1.4.2 (1a2b3c4)"
    assert strings["OriginalFilename"] == "CBBEtoUBE.exe"


# --- the manifest -----------------------------------------------------------

def _bundle(root):
    files = {"CBBEtoUBE.exe": b"exe", "_internal/a.dll": b"dll", "LICENSE": b"gpl"}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _check(root):
    files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    return bi.check_manifest((root / bi.MANIFEST).read_text(encoding="utf-8"), files,
                             lambda rel: bi._sha256(root / rel))


def test_a_fresh_manifest_checks_clean(tmp_path):
    root = _bundle(tmp_path / "CBBEtoUBE")
    version_file, manifest = bi.write_manifest(root, "CBBEtoUBE 1.4.2 @ 1a2b3c4 x")
    assert version_file.read_text(encoding="utf-8") == "CBBEtoUBE 1.4.2 @ 1a2b3c4 x\n"
    listed = manifest.read_text(encoding="utf-8").splitlines()
    assert len(listed) == 4 and all(bi.MANIFEST not in row for row in listed)
    assert _check(root) == {"malformed": [], "missing": [], "different": [], "unlisted": []}
    bi.write_manifest(root, "CBBEtoUBE 1.4.2 @ 1a2b3c4 x")      # into a folder that has one
    assert _check(root) == {"malformed": [], "missing": [], "different": [], "unlisted": []}


@pytest.mark.parametrize("change, bucket", [
    ("tamper", "different"), ("delete", "missing"), ("add", "unlisted")])
def test_a_changed_bundle_fails_its_manifest(tmp_path, change, bucket):
    root = _bundle(tmp_path / "CBBEtoUBE")
    bi.write_manifest(root, "CBBEtoUBE 1.4.2 @ 1a2b3c4 x")
    if change == "tamper":
        (root / "_internal" / "a.dll").write_bytes(b"dlL")
    elif change == "delete":
        (root / "LICENSE").unlink()
    else:
        (root / "_internal" / "stale_from_an_older_build.dll").write_bytes(b"old")
    assert _check(root)[bucket], f"{change} was not noticed"


# --- --version --------------------------------------------------------------

def test_version_answers_and_writes_nothing(tmp_path):
    """Run a copy of the entry script in an empty folder (the log folder is the
    script's own). Before: exit 2 from argparse, and a log file appeared."""
    shutil.copy2(REPO / "cbbe_to_ube_main.py", tmp_path / "cbbe_to_ube_main.py")
    env = dict(os.environ, PYTHONPATH=str(REPO), CBBE2UBE_NO_PAUSE="1")
    env.pop("CBBE2UBE_RUN_LOG", None)
    r = subprocess.run([sys.executable, str(tmp_path / "cbbe_to_ube_main.py"), "--version"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path),
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    words = r.stdout.split()
    assert words[:1] == ["build"] and words[2:3] == ["@"], r.stdout
    assert words[1] == bi.version_from_source((REPO / "src" / "version.py").read_bytes())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cbbe_to_ube_main.py"], (
        "--version wrote beside the exe")
