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

"""#release-gate -- scripts/release_gate.py, checked on chains it must pass and
chains it must fail.

The git clauses run on a throwaway repository built in tmp_path, with the
exe's stamp stubbed, so each defect is one deliberate commit. The archive
reader runs on the tracked dist exe and is compared with PyInstaller's own
reader where that is installed. No PyInstaller build happens here."""
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import release_gate as rg

REPO = Path(__file__).resolve().parents[1]
PASS, FAIL, NOT_CHECKED = rg.PASS, rg.FAIL, rg.NOT_CHECKED


def _g(repo, *args):
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=gate", "-c", "user.email=gate",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
         "-c", "core.hooksPath=no-hooks", *args],
        capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def _write(repo, rel, data):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _commit(repo, message):
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", message)
    return _g(repo, "rev-parse", "HEAD")


@pytest.fixture
def chain(tmp_path):
    """source commit S, then the rebuild commit B (adds the exe) on top of it."""
    return _build_chain(tmp_path / "repo")


def _build_chain(repo):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo.mkdir(parents=True)
    _g(repo, "init", "-q", "-b", "testing")
    _write(repo, "src/app.py", "print('app')\n")
    _write(repo, "src/version.py", '__version__ = "1.0"\n')
    _write(repo, "CBBEtoUBE.spec", "# spec\n")
    _write(repo, "CHANGELOG.md",
           "# Changelog\n\n## Unreleased\n\n## 1.0 — 2026-01-01\n\nFirst.\n")
    source = _commit(repo, "source")
    _write(repo, rg.EXE, b"fake exe built from the source commit")
    build = _commit(repo, "rebuild the exe")
    return SimpleNamespace(repo=repo, source=source, build=build)


def _stamp(git, *, dirty=False, version="1.0"):
    return lambda exe: ({"git": git[:7], "dirty": dirty,
                         "built_utc": "2026-01-01T00:00:00Z"}, version)


def _status(verdicts):
    return {clause: status for status, clause, _ in verdicts}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path-length rules")
def test_the_gate_reads_files_wherever_git_itself_can(tmp_path):
    """Windows git stat()s a `<commit>:<path>` argument to `git show` as though
    it were a file, and fails "Filename too long" a little under 200 characters
    deep -- while git still writes and reads its own objects there. The gate
    failed a correct chain that way. A repository 198 characters deep sits in
    that window: past the stat limit, short of git's own object-path limit."""
    pad = 198 - len(str(tmp_path / "p" / "repo")) + 1
    if pad < 1:
        pytest.skip(f"the temp folder is already {len(str(tmp_path))} characters deep")
    repo = tmp_path / ("p" * pad) / "repo"
    assert len(str(repo)) == 198
    deep = _build_chain(repo)
    _g(deep.repo, "tag", "v1.0", deep.build)
    got = _status(rg.stamp_check("v1.0", deep.repo, read=_stamp(deep.source)))
    assert FAIL not in got.values(), got


# --- the git clauses ----------------------------------------------------------

def test_a_correct_chain_passes(chain):
    _g(chain.repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", chain.repo, read=_stamp(chain.source)))
    assert got == {"stamp readable": PASS, "build commit": PASS,
                   "in history": PASS, "inputs unchanged": PASS,
                   "build time": NOT_CHECKED,
                   "version": PASS, "manifest": NOT_CHECKED,
                   "release asset": NOT_CHECKED}


def test_the_merge_tag_shape_of_the_published_releases_passes(chain):
    """v1.4 and v1.4.1 are merges whose SECOND parent added the exe."""
    repo = chain.repo
    _g(repo, "checkout", "-q", "-b", "main", chain.source)
    _write(repo, "README.md", "docs only\n")
    _commit(repo, "docs on main")
    _g(repo, "merge", "-q", "--no-ff", "-m", "merge testing into main", "testing")
    merge = _g(repo, "rev-parse", "HEAD")
    _g(repo, "tag", "v1.0", merge)
    parents = _g(repo, "show", "-s", "--format=%P", merge).split()
    # Control: the old hand rule, "the stamp names the tag's parent", rejects
    # this correct release -- neither parent is the source commit.
    assert chain.source not in parents and parents[1] == chain.build
    verdicts = rg.stamp_check("v1.0", repo, read=_stamp(chain.source))
    assert all(s != FAIL for s, _, _ in verdicts), verdicts


def test_a_source_change_after_the_build_fails_inputs(chain):
    _write(chain.repo, "src/app.py", "print('changed after the rebuild')\n")
    later = _commit(chain.repo, "source change after the rebuild")
    _g(chain.repo, "tag", "v1.0", later)
    got = _status(rg.stamp_check("v1.0", chain.repo, read=_stamp(chain.source)))
    assert got["inputs unchanged"] == FAIL
    assert got["build commit"] == PASS, "the stamp is right; the TAG is wrong"


def test_a_change_outside_the_build_inputs_does_not_fail(chain):
    _write(chain.repo, "docs/notes.md", "not read by the build\n")
    later = _commit(chain.repo, "docs after the rebuild")
    _g(chain.repo, "tag", "v1.0", later)
    got = _status(rg.stamp_check("v1.0", chain.repo, read=_stamp(chain.source)))
    assert got["inputs unchanged"] == PASS


def test_a_stamp_naming_the_wrong_commit_fails(chain):
    _g(chain.repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", chain.repo, read=_stamp(chain.build)))
    assert got["build commit"] == FAIL


def test_a_dirty_build_fails(chain):
    _g(chain.repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", chain.repo,
                                 read=_stamp(chain.source, dirty=True)))
    assert got["build commit"] == FAIL


def test_a_build_made_before_an_amend_fails(chain):
    repo = chain.repo
    _g(repo, "checkout", "-q", "-b", "elsewhere", chain.source)
    _write(repo, "src/app.py", "print('amended away')\n")
    ghost = _commit(repo, "the commit that was amended")
    _g(repo, "checkout", "-q", "testing")
    _g(repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", repo, read=_stamp(ghost)))
    assert got["in history"] == FAIL
    assert got["build commit"] == FAIL


def test_a_stamp_that_names_no_commit_fails(chain):
    _g(chain.repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", chain.repo,
                                 read=lambda exe: ({"git": "nogit"}, "1.0")))
    assert got["build commit"] == FAIL and got["inputs unchanged"] == FAIL


def test_an_exe_without_a_stamp_fails(chain):
    _g(chain.repo, "tag", "v1.0", chain.build)
    got = _status(rg.stamp_check("v1.0", chain.repo, read=lambda exe: (None, "1.0")))
    assert got["stamp readable"] == FAIL


def test_every_version_source_must_agree(chain):
    repo = chain.repo
    _g(repo, "tag", "v1.0", chain.build)
    _g(repo, "tag", "v2.0", chain.build)
    assert _status(rg.stamp_check("v1.0", repo, read=_stamp(chain.source)))["version"] == PASS
    assert _status(rg.stamp_check(
        "v1.0", repo, read=_stamp(chain.source, version="0.9")))["version"] == FAIL
    assert _status(rg.stamp_check("v2.0", repo, read=_stamp(chain.source)))["version"] == FAIL


def test_a_changelog_heading_that_disagrees_fails(chain):
    repo = chain.repo
    _write(repo, "CHANGELOG.md", "# Changelog\n\n## 1.1 — 2026-02-01\n")
    later = _commit(repo, "changelog heading ahead of the version")
    _g(repo, "tag", "v1.0", later)
    assert _status(rg.stamp_check("v1.0", repo, read=_stamp(chain.source)))["version"] == FAIL


def test_the_first_dated_heading_skips_unreleased():
    text = ("# Changelog\n\n## Unreleased\n\n### Fixed — something\n\n"
            "## 1.4.1 — 2026-09-11\n\n## 1.4 — 2026-09-09\n")
    assert rg.first_dated_heading(text) == "1.4.1"
    assert rg.first_dated_heading("# Changelog\n\n## Unreleased\n") is None


def test_a_pre_tag_run_on_a_commit_says_no_tag_name_was_compared(chain):
    verdicts = rg.stamp_check(chain.build, chain.repo, read=_stamp(chain.source))
    status, _, detail = next(v for v in verdicts if v[1] == "version")
    assert status == PASS and "not a tag" in detail


def test_an_unknown_ref_cannot_run(chain):
    with pytest.raises(rg.GateError):
        rg.stamp_check("v9.9", chain.repo, read=_stamp(chain.source))


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def test_the_release_asset_must_be_the_tagged_bundle(chain, tmp_path):
    _g(chain.repo, "tag", "v1.0", chain.build)
    exe = (chain.repo / rg.EXE).read_bytes()
    good = tmp_path / "good.zip"
    _zip(good, {"CBBEtoUBE/CBBEtoUBE.exe": exe})
    got = _status(rg.stamp_check("v1.0", chain.repo, asset=good,
                                 read=_stamp(chain.source)))
    assert got["release asset"] == PASS
    for name, files in {
            "a changed byte": {"CBBEtoUBE/CBBEtoUBE.exe": exe + b"!"},
            "an extra file": {"CBBEtoUBE/CBBEtoUBE.exe": exe,
                              "CBBEtoUBE/stray.dll": b"x"},
            "a missing file": {"CBBEtoUBE/other.txt": b"x"},
            "a file outside the bundle": {"CBBEtoUBE/CBBEtoUBE.exe": exe,
                                          "README.txt": b"x"}}.items():
        bad = tmp_path / f"bad {name}.zip"
        _zip(bad, files)
        got = _status(rg.stamp_check("v1.0", chain.repo, asset=bad,
                                     read=_stamp(chain.source)))
        assert got["release asset"] == FAIL, name


# --- the archive reader ---------------------------------------------------------

def _tracked_exe_stamp():
    exe = REPO / rg.EXE
    if not exe.is_file():
        pytest.skip("no dist build in this checkout")
    try:
        return exe, rg.read_stamp(exe.read_bytes())
    except rg.ArchiveError as e:
        pytest.skip(f"cannot read the tracked exe with this interpreter: {e}")


def test_the_reader_finds_the_stamp_in_the_tracked_exe():
    _, (build, version) = _tracked_exe_stamp()
    assert build and {"git", "dirty", "built_utc"} <= set(build)
    assert isinstance(version, str) and version


def test_the_reader_agrees_with_pyinstallers_own_reader():
    """The control: an independent reader. Executing the modules is fine HERE,
    in a test, on our own tracked build -- the gate itself never does."""
    exe, (build, version) = _tracked_exe_stamp()
    if importlib.util.find_spec("PyInstaller") is None:
        pytest.skip("PyInstaller is not installed: no independent reader")
    from PyInstaller.archive.readers import CArchiveReader
    pyz = CArchiveReader(str(exe)).open_embedded_archive("PYZ.pyz")
    ns = {}
    exec(pyz.extract("src._build_stamp"), ns)
    assert ns["BUILD"] == build
    ns = {}
    exec(pyz.extract("src.version"), ns)
    assert ns["__version__"] == version


def test_the_reader_refuses_what_is_not_a_pyinstaller_exe():
    with pytest.raises(rg.ArchiveError):
        rg.read_stamp(b"MZ -- an executable with no archive in it")


def test_a_different_python_is_refused_never_passed(monkeypatch, capsys):
    """The exe's modules are bytecode for ONE Python. Under any other the gate
    must stop with exit 2 -- not guess, and not report a pass."""
    exe, _ = _tracked_exe_stamp()
    monkeypatch.setattr(importlib.util, "MAGIC_NUMBER", b"\x00\x01\r\n")
    with pytest.raises(rg.ArchiveError, match="different Python"):
        rg.read_stamp(exe.read_bytes())
    tag = "v1.4.1"
    have = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify",
                           "--quiet", f"refs/tags/{tag}"], capture_output=True)
    if have.returncode != 0:
        pytest.skip(f"{tag} is not in this checkout")
    assert rg.main(["stamp-check", tag]) == 2
    captured = capsys.readouterr()
    assert "could not run" in captured.err and "VERDICT: PASS" not in captured.out


def test_the_bytecode_walk_reads_literals_and_stops_at_anything_else():
    code = compile("'doc'\nA = 'x'\nB = {'k': False, 'n': 3}\nC = len('y')\n"
                   "D = 'after a call'\n", "m", "exec")
    got = rg.module_constants(code)
    assert got["A"] == "x" and got["B"] == {"k": False, "n": 3}
    assert "C" not in got and "D" not in got, "the walk ran code or went past it"


@pytest.mark.parametrize("tag", ["v1.4", "v1.4.1"])
def test_the_published_releases_pass(tag):
    have = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify",
                           "--quiet", f"refs/tags/{tag}"], capture_output=True)
    if have.returncode != 0:
        pytest.skip(f"{tag} is not in this checkout")
    try:
        verdicts = rg.stamp_check(tag, REPO)
    except rg.ArchiveError as e:
        pytest.skip(f"cannot read the exe with this interpreter: {e}")
    assert all(s != FAIL for s, _, _ in verdicts), verdicts


# --- the lists that must not drift ------------------------------------------

def test_build_inputs_cover_every_path_the_spec_names():
    spec = (REPO / "CBBEtoUBE.spec").read_text(encoding="utf-8")
    paths = set()
    with warnings.catch_warnings():
        # The spec's own comments hold Windows paths ("\C..."); parsing them is
        # the spec's business, not a defect in this test.
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(spec)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.replace("\\", "/")
            s = s[2:] if s.startswith("./") else s
            if s and s != "." and "\n" not in s and (REPO / s).exists():
                paths.add(s)
    assert len(paths) >= 4, f"only {sorted(paths)} parsed -- the check proves nothing"
    uncovered = [p for p in sorted(paths)
                 if not any(p == i or p.startswith(i + "/") for i in rg.BUILD_INPUTS)]
    assert not uncovered, f"the spec reads {uncovered}, which BUILD_INPUTS omits"


def test_state_file_globs_match_the_deploy_script():
    text = (REPO / "scripts" / "deploy_exe.ps1").read_text(encoding="utf-8")
    m = re.search(r"\$stateFiles\s*=\s*@\((.*?)\)", text, re.S)
    assert m, "deploy_exe.ps1 no longer defines $stateFiles"
    assert set(re.findall(r'"([^"]+)"', m.group(1))) == set(rg.STATE_FILE_GLOBS)


def _names_the_tool_writes_beside_itself():
    """Taken from the code that writes them wherever it exposes the name."""
    spec = importlib.util.spec_from_file_location(
        "_entry_probe_gate", REPO / "cbbe_to_ube_main.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    from src import exclusions, gui_settings
    logs = {entry._log_target(argv, "")[0] for argv in ([], ["auto"], ["--help"])}
    settings = gui_settings.config_path().name
    return sorted(logs | {n.replace("_last_", "_previous_") for n in logs}
                  | {entry._FAILURES_NAME,
                     entry._FAILURES_NAME.replace("_last_", "_previous_"),
                     exclusions.config_path().name, settings,
                     settings + ".bak", settings + ".bak-20260915",
                     settings + ".prebuild-20260915-010101",
                     "CBBEtoUBE_glowdebug.log"})


def test_every_file_the_tool_writes_beside_itself_is_state(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_EXCLUSIONS", raising=False)
    monkeypatch.delenv("CBBE2UBE_CONFIG", raising=False)
    names = _names_the_tool_writes_beside_itself()
    assert len(names) >= 10, names
    assert [n for n in names if not rg._is_state_file(n)] == []


def test_program_files_are_not_state():
    for rel in ("CBBEtoUBE.exe", "_internal/python310.dll", "LICENSE",
                "_internal/CBBEtoUBE_settings.json"):
        assert not rg._is_state_file(rel), rel


def test_the_ci_gate_runs_on_the_python_the_exe_was_built_with():
    wf = (REPO / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")
    assert "scripts/release_gate.py stamp-check" in wf
    dlls = [m.group(1) for p in (REPO / rg.BUNDLE / "_internal").glob("python3*.dll")
            if (m := re.fullmatch(r"python3(\d+)\.dll", p.name))]
    if not dlls:
        pytest.skip("no dist build in this checkout")
    m = re.search(r'python-version:\s*"(\d+\.\d+)"', wf)
    assert m and m.group(1) == f"3.{dlls[0]}"


# --- the manifest ---------------------------------------------------------------

def _with_manifest(chain, version_line="CBBEtoUBE 1.0 @ abc1234 x", *, tamper=False):
    from scripts import build_identity as bi
    bundle = chain.repo / rg.BUNDLE
    (bundle / "_internal").mkdir(parents=True, exist_ok=True)
    (bundle / "_internal" / "lib.dll").write_bytes(b"a library")
    bi.write_manifest(bundle, version_line)
    if tamper:
        (bundle / "_internal" / "lib.dll").write_bytes(b"a library, changed")
    return _commit(chain.repo, "rebuild with a manifest")


def test_a_bundle_matching_its_manifest_passes(chain):
    _g(chain.repo, "tag", "v1.0", _with_manifest(chain))
    assert _status(rg.stamp_check("v1.0", chain.repo,
                                  read=_stamp(chain.source)))["manifest"] == PASS


def test_a_bundle_that_differs_from_its_manifest_fails(chain):
    _g(chain.repo, "tag", "v1.0", _with_manifest(chain, tamper=True))
    status, _, detail = next(v for v in rg.stamp_check(
        "v1.0", chain.repo, read=_stamp(chain.source)) if v[1] == "manifest")
    assert status == FAIL and "different" in detail


def test_a_version_file_naming_another_version_fails(chain):
    _g(chain.repo, "tag", "v1.0", _with_manifest(chain, "CBBEtoUBE 0.9 @ abc1234 x"))
    assert _status(rg.stamp_check("v1.0", chain.repo,
                                  read=_stamp(chain.source)))["manifest"] == FAIL


def test_manifest_check_needs_no_repository(tmp_path):
    from scripts import build_identity as bi
    folder = tmp_path / "installed"
    (folder / "_internal").mkdir(parents=True)
    (folder / "CBBEtoUBE.exe").write_bytes(b"exe")
    (folder / "_internal" / "lib.dll").write_bytes(b"dll")
    bi.write_manifest(folder, "CBBEtoUBE 1.4.2 @ abc1234 x")
    (folder / "CBBEtoUBE_settings.json").write_bytes(b"{}")     # state: ignored
    assert _status(rg.manifest_check(folder)) == {"manifest": PASS}
    (folder / "_internal" / "left_from_an_older_build.dll").write_bytes(b"old")
    status, _, detail = rg.manifest_check(folder)[0]
    assert status == FAIL and "unlisted" in detail
    (folder / bi.MANIFEST).unlink()
    with pytest.raises(rg.GateError):
        rg.manifest_check(folder)


# --- deployed-diff ------------------------------------------------------------

def _tree(root, files):
    for rel, data in files.items():
        _write(root, rel, data)


def test_an_identical_install_with_its_state_files_passes(tmp_path):
    files = {"CBBEtoUBE.exe": b"exe", "_internal/a.dll": b"dll"}
    dist, inst = tmp_path / "dist", tmp_path / "installed"
    _tree(dist, files)
    _tree(inst, {**files, "CBBEtoUBE_settings.json": b"{}",
                 "CBBEtoUBE_cli.log": b"help", "CBBEtoUBE_exclusions.json": b"{}"})
    got = rg.deployed_diff(inst, dist)
    assert _status(got)["files"] == PASS
    assert _status(got)["installed build"] == NOT_CHECKED   # a fake exe


@pytest.mark.parametrize("change", ["different", "missing", "extra"])
def test_an_install_that_differs_fails(tmp_path, change):
    files = {"CBBEtoUBE.exe": b"exe", "_internal/a.dll": b"dll"}
    dist, inst = tmp_path / "dist", tmp_path / "installed"
    _tree(dist, files)
    installed = dict(files)
    if change == "different":
        installed["_internal/a.dll"] = b"dlL"          # same size, one byte
    elif change == "missing":
        del installed["_internal/a.dll"]
    else:
        installed["_internal/left_from_an_older_build.dll"] = b"old"
    _tree(inst, installed)
    status, _, detail = next(v for v in rg.deployed_diff(inst, dist) if v[1] == "files")
    assert status == FAIL and change in detail


# --- build time: the build reproduces ------------------------------------------

def _commit_time(repo, commit):
    return int(_g(repo, "log", "-1", "--format=%ct", commit))


def _in_git_checkout():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet",
                           "HEAD"], capture_output=True).returncode == 0


def _stamp_pinned(git, epoch, **kw):
    """A stamp carrying SOURCE_DATE_EPOCH, as every build since the epoch rule does."""
    base = _stamp(git, **kw)

    def read(exe):
        build, version = base(exe)
        return {**build, "source_date_epoch": epoch,
                "toolchain": {"python": "3.10.6", "pyinstaller": "6.20.0"}}, version
    return read


def test_a_build_pinned_to_its_commits_time_passes_build_time(chain):
    read = _stamp_pinned(chain.source, _commit_time(chain.repo, chain.source))
    assert _status(rg.stamp_check("HEAD", chain.repo, read=read))["build time"] == PASS


def test_a_build_pinned_to_any_other_time_fails_build_time(chain):
    """A rebuild pins the epoch to the commit's time; a build pinned anywhere
    else can never be reproduced by one, so the gate refuses rather than hopes."""
    read = _stamp_pinned(chain.source, _commit_time(chain.repo, chain.source) + 60)
    status, _, detail = next(v for v in rg.stamp_check("HEAD", chain.repo, read=read)
                             if v[1] == "build time")
    assert status == FAIL and "cannot reproduce" in detail


def test_a_build_from_before_the_epoch_rule_is_not_checked_not_failed(chain):
    """Every build before 2026-09-16, the published tags included: the clause
    asks for a rebuild before tagging and fails nothing."""
    verdicts = rg.stamp_check("HEAD", chain.repo, read=_stamp(chain.source))
    got = _status(verdicts)
    assert got["build time"] == NOT_CHECKED and FAIL not in got.values()
    assert "rebuild before tagging" in next(d for _, c, d in verdicts if c == "build time")


# --- rebuild-plan -------------------------------------------------------------

def test_rebuild_plan_names_the_stamp_commit_and_what_built_it(chain):
    epoch = _commit_time(chain.repo, chain.source)
    plan = rg.rebuild_plan("HEAD", chain.repo, read=_stamp_pinned(chain.source, epoch))
    assert plan["commit"] == chain.source and plan["epoch"] == epoch
    assert plan["python"] == "3.10.6" and plan["reproducible"] is True
    lines = rg.plan_lines(plan)
    assert f"commit={chain.source}" in lines and "reproducible=true" in lines
    assert "python=3.10.6" in lines and all("\n" not in ln for ln in lines)


def test_rebuild_plan_says_when_no_rebuild_can_reproduce(chain):
    plan = rg.rebuild_plan("HEAD", chain.repo, read=_stamp(chain.source))
    assert plan["reproducible"] is False and "SOURCE_DATE_EPOCH" in plan["reason"]
    assert {"reproducible=false", "epoch="} <= set(rg.plan_lines(plan))


def test_rebuild_plan_on_the_tracked_exe(capsys):
    """The real thing, as the workflow calls it: every key the job reads is there."""
    _, (build, _version) = _tracked_exe_stamp()
    if not _in_git_checkout():
        pytest.skip("not a git checkout")
    assert rg.main(["rebuild-plan", "HEAD"]) == 0
    got = dict(ln.split("=", 1) for ln in capsys.readouterr().out.splitlines() if "=" in ln)
    assert {"commit", "epoch", "python", "pyinstaller", "reproducible", "reason"} <= set(got)
    assert re.fullmatch(r"[0-9a-f]{40}", got["commit"])
    assert got["python"] == build["toolchain"]["python"]
    assert got["reproducible"] in ("true", "false")


# --- rebuild-check: a fresh build against the tracked bundle --------------------

_BUNDLE = {
    "CBBEtoUBE.exe": b"exe", "USING.md": b"docs", "VERSION.txt": b"CBBEtoUBE 1.0 @ x",
    "_internal/NiflyDLL.dll": b"ours, vendored", "_internal/LICENSE": b"gpl",
    "_internal/numpy/core.pyd": b"from the locked wheel",
    "_internal/python310.dll": b"the runner's cpython", "_internal/_ctypes.pyd": b"stdlib ext",
    "_internal/base_library.zip": b"stdlib pyc", "_internal/tcl8/init.tcl": b"tcl",
    "_internal/licenses/CPython-LICENSE.txt": b"psf",
    "_internal/licenses/SciPy-LICENSE.txt": b"bsd",
}


def _commit_bundle(chain, files):
    bundle = chain.repo / rg.BUNDLE
    _tree(bundle, {**files, rg.MANIFEST: b"the tracked manifest: never compared"})
    return _commit(chain.repo, "rebuild")


def _fresh(tmp_path, files):
    folder = tmp_path / "fresh"
    _tree(folder, {**files, rg.MANIFEST: b"its own manifest: never compared",
                   "CBBEtoUBE_settings.json": b"{}"})        # state: ignored
    return folder


def test_a_rebuild_identical_to_the_tracked_bundle_passes(chain, tmp_path):
    _commit_bundle(chain, _BUNDLE)
    got = _status(rg.rebuild_check("HEAD", _fresh(tmp_path, _BUNDLE), chain.repo))
    assert got == {"exe": PASS, "file set": PASS, "program files": PASS,
                   "interpreter files": PASS}


@pytest.mark.parametrize("rel, clause", [
    ("CBBEtoUBE.exe", "exe"),
    ("USING.md", "program files"), ("VERSION.txt", "program files"),
    ("_internal/NiflyDLL.dll", "program files"), ("_internal/LICENSE", "program files"),
    ("_internal/numpy/core.pyd", "program files"),
    ("_internal/licenses/SciPy-LICENSE.txt", "program files"),
])
def test_a_rebuild_differing_in_what_source_or_lock_determine_fails(chain, tmp_path, rel, clause):
    _commit_bundle(chain, _BUNDLE)
    folder = _fresh(tmp_path, {**_BUNDLE, rel: _BUNDLE[rel] + b"!"})
    status, _, detail = next(v for v in rg.rebuild_check("HEAD", folder, chain.repo)
                             if v[1] == clause)
    assert status == FAIL and rel in detail


@pytest.mark.parametrize("rel", ["_internal/python310.dll", "_internal/_ctypes.pyd",
                                 "_internal/base_library.zip", "_internal/tcl8/init.tcl",
                                 "_internal/licenses/CPython-LICENSE.txt"])
def test_a_rebuild_on_another_cpython_build_is_reported_not_judged(chain, tmp_path, rel):
    """The runner's CPython is not the release machine's build of the same
    version; what a rebuild proves is the source-to-exe link, judged above."""
    _commit_bundle(chain, _BUNDLE)
    folder = _fresh(tmp_path, {**_BUNDLE, rel: _BUNDLE[rel] + b"!"})
    verdicts = rg.rebuild_check("HEAD", folder, chain.repo)
    got = _status(verdicts)
    assert got["interpreter files"] == NOT_CHECKED and FAIL not in got.values()
    assert rel in next(d for _, c, d in verdicts if c == "interpreter files")


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_a_rebuild_with_a_different_file_list_fails(chain, tmp_path, change):
    _commit_bundle(chain, _BUNDLE)
    files = dict(_BUNDLE)
    if change == "missing":
        del files["_internal/numpy/core.pyd"]
    else:
        files["_internal/left_over.dll"] = b"old"
    status, _, detail = next(v for v in rg.rebuild_check("HEAD", _fresh(tmp_path, files), chain.repo)
                             if v[1] == "file set")
    assert status == FAIL and change in detail


def test_interpreter_files_are_the_bundles_cpython_parts_and_nothing_of_ours():
    ours = ["CBBEtoUBE.exe", "USING.md", "_internal/NiflyDLL.dll", "_internal/LICENSE",
            "_internal/assets/CBBEtoUBE.ico", "_internal/numpy/core.pyd",
            "_internal/numpy.libs/openblas.dll", "_internal/lz4/_frame.pyd",
            "_internal/licenses/SciPy-LICENSE.txt"]
    theirs = ["_internal/python310.dll", "_internal/VCRUNTIME140.dll", "_internal/_ctypes.pyd",
              "_internal/base_library.zip", "_internal/tcl8/8.6/init.tcl",
              "_internal/_tk_data/tk.tcl", "_internal/licenses/CPython-LICENSE.txt",
              "_internal/licenses/Tcl-Tk-license.terms"]
    assert [r for r in ours if rg._is_interpreter_file(r)] == []
    assert [r for r in theirs if not rg._is_interpreter_file(r)] == []


def test_the_tracked_bundle_is_a_rebuild_of_itself():
    """Control on real data: the tracked folder against its own commit passes
    every clause, and the interpreter bucket is real, not empty."""
    _tracked_exe_stamp()
    if not _in_git_checkout():
        pytest.skip("not a git checkout")
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", rg.BUNDLE],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        pytest.skip("dist/ has uncommitted changes: a fresh build not yet committed")
    verdicts = rg.rebuild_check("HEAD", REPO / rg.BUNDLE, REPO)
    assert _status(verdicts) == {"exe": PASS, "file set": PASS, "program files": PASS,
                                 "interpreter files": PASS}, verdicts
    interp = [r for r in rg._files(REPO / rg.BUNDLE) if rg._is_interpreter_file(r)]
    assert any(re.fullmatch(r"_internal/python3\d+\.dll", r) for r in interp)
    assert len(interp) >= 20 and "_internal/NiflyDLL.dll" not in interp


# --- bundle-scan: release markers -----------------------------------------------

def _inspector(modules, names, version="1.0"):
    return lambda exe: {"modules": set(modules),
                        "names": {u: set(n) for u, n in names.items()}, "version": version}


def _marker_file(tmp_path, present, absent):
    p = tmp_path / "markers.json"
    p.write_text(json.dumps({"present": present, "absent": absent}), encoding="utf-8")
    return p


_PRESENT = [{"module": "src.fix", "since": "1.0", "claims": "the fix"},
            {"name": "_rotate_previous", "since": "1.0", "claims": "log rotation"}]
_ABSENT = [{"module": "psutil", "claims": "never a dependency"}]
_GOOD = _inspector({"src.fix", "src.version"}, {"cbbe_to_ube_main": {"_rotate_previous"}})


def test_an_exe_with_every_claimed_marker_and_no_forbidden_one_passes(chain, tmp_path):
    got = rg.bundle_scan("HEAD", _marker_file(tmp_path, _PRESENT, _ABSENT), chain.repo,
                         inspect=_GOOD)
    assert _status(got) == {"present markers": PASS, "absent markers": PASS}


def test_a_claimed_marker_missing_from_the_exe_fails_by_name_and_claim(chain, tmp_path):
    bad = _inspector({"src.fix"}, {"cbbe_to_ube_main": set()})
    status, _, detail = rg.bundle_scan("HEAD", _marker_file(tmp_path, _PRESENT, _ABSENT),
                                       chain.repo, inspect=bad)[0]
    assert status == FAIL and "_rotate_previous" in detail and "log rotation" in detail


def test_a_forbidden_marker_that_shipped_fails_by_name(chain, tmp_path):
    bad = _inspector({"src.fix", "psutil"}, {"cbbe_to_ube_main": {"_rotate_previous"}})
    verdicts = rg.bundle_scan("HEAD", _marker_file(tmp_path, _PRESENT, _ABSENT), chain.repo,
                              inspect=bad)
    status, _, detail = next(v for v in verdicts if v[1] == "absent markers")
    assert status == FAIL and "psutil" in detail


def test_a_marker_claimed_by_a_later_version_is_not_checked(chain, tmp_path):
    """An older tag scans clean on what it actually claims."""
    present = _PRESENT + [{"name": "job_object", "since": "2.0",
                           "claims": "workers die with the parent"}]
    verdicts = rg.bundle_scan("HEAD", _marker_file(tmp_path, present, _ABSENT), chain.repo,
                              inspect=_GOOD)
    assert _status(verdicts) == {"present markers": PASS, "later markers": NOT_CHECKED,
                                 "absent markers": PASS}
    assert "job_object" in next(d for _, c, d in verdicts if c == "later markers")


def test_a_version_that_claims_no_marker_is_not_checked_not_passed(chain, tmp_path):
    """0 of 0 is not a pass: v1.4 predates every marker, and the scan says so."""
    old = _inspector({"src.fix", "src.version"}, {"cbbe_to_ube_main": {"_rotate_previous"}},
                     version="0.9")
    verdicts = rg.bundle_scan("HEAD", _marker_file(tmp_path, _PRESENT, _ABSENT), chain.repo,
                              inspect=old)
    got = _status(verdicts)
    assert got["present markers"] == NOT_CHECKED and got["later markers"] == NOT_CHECKED
    assert "nothing to check" in next(d for _, c, d in verdicts if c == "present markers")


@pytest.mark.parametrize("present, absent, why", [
    ([], _ABSENT, "nothing to find"),
    (_PRESENT, [], "nothing to refuse"),
    ([{"module": "src.fix", "since": "1.0"}], _ABSENT, "claims"),
    ([{"module": "src.fix", "name": "x", "since": "1.0", "claims": "both"}], _ABSENT, "module"),
    ([{"module": "src.fix", "claims": "no since"}], _ABSENT, "since"),
])
def test_a_marker_list_that_could_not_fail_is_refused(chain, tmp_path, present, absent, why):
    with pytest.raises(rg.GateError, match=why):
        rg.bundle_scan("HEAD", _marker_file(tmp_path, present, absent), chain.repo,
                       inspect=_GOOD)


def test_the_inspector_reads_the_entry_script_as_well_as_the_pyz():
    """The 1.4.1 log rotation is defined in cbbe_to_ube_main.py, which the
    bootloader runs from the outer archive: a PYZ-only scan never sees it."""
    exe, _ = _tracked_exe_stamp()
    info = rg.inspect_exe(exe.read_bytes())
    assert "cbbe_to_ube_main" in info["names"]
    assert "_rotate_previous" in info["names"]["cbbe_to_ube_main"]
    assert not any(u.startswith("pyi") for u in info["names"]), (
        "PyInstaller's own scripts are not ours to claim")
    assert "src.version" in info["modules"] and info["version"]


def test_every_marker_holds_on_the_tracked_exe():
    """The list cannot drift: every present marker is in the tracked exe and no
    absent one is -- whatever `since` says, because the tree is at least as new
    as any claim in it. And the list stays big enough to mean something."""
    exe, _ = _tracked_exe_stamp()
    info = rg.inspect_exe(exe.read_bytes())
    rules = rg.load_markers(REPO / rg.MARKERS)
    missing = [rg._label(m) for m in rules["present"] if not rg._marker_found(m, info)]
    shipped = [rg._label(m) for m in rules["absent"] if rg._marker_found(m, info)]
    assert not missing and not shipped, (missing, shipped)
    assert len(rules["present"]) >= 12 and len(rules["absent"]) >= 4, "the list shrank"


def test_the_published_release_carries_what_it_claims_and_not_what_came_later():
    have = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet",
                           "refs/tags/v1.4.1"], capture_output=True)
    if have.returncode != 0:
        pytest.skip("v1.4.1 is not in this checkout")
    try:
        verdicts = rg.bundle_scan("v1.4.1", repo=REPO)
    except rg.ArchiveError as e:
        pytest.skip(f"cannot read the exe with this interpreter: {e}")
    got = _status(verdicts)
    assert got["present markers"] == PASS and got["absent markers"] == PASS, verdicts
    assert got["later markers"] == NOT_CHECKED, (
        "the markers of work since 1.4.1 are claimed by a later version")


def test_bundle_scan_reads_a_folder_an_exe_or_a_ref():
    exe, _ = _tracked_exe_stamp()
    for target in (exe, exe.parent):
        assert FAIL not in _status(rg.bundle_scan(target, repo=REPO)).values()
    with pytest.raises(rg.GateError):
        rg.bundle_scan(exe.parent / "no-such-thing", repo=REPO)


def test_the_release_workflow_rebuilds_the_exe_and_checks_the_result():
    """Mode A of the release build: rebuild on a runner from the stamp commit,
    with the interpreter the stamp names, and compare with the tagged bundle."""
    wf = (REPO / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")
    for needle in ("release_gate.py rebuild-plan",
                   "python-version: ${{ steps.plan.outputs.python }}",
                   "git checkout --detach", "build_exe.ps1 -Clean",
                   "release_gate.py manifest-check dist/CBBEtoUBE",
                   "release_gate.py rebuild-check", "release_gate.py bundle-scan",
                   "CBBEtoUBE.exe --version"):
        assert needle in wf, f"the release workflow no longer runs {needle!r}"
    assert (wf.index("rebuild-plan") < wf.index("git checkout --detach")
            < wf.index("build_exe.ps1 -Clean") < wf.index('rebuild-check "$REF"'))
    assert 'bundle-scan "$REF_NAME"' in wf and "bundle-scan dist/CBBEtoUBE/CBBEtoUBE.exe" in wf, (
        "the tagged exe and the rebuilt one")
