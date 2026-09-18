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

"""Every release is built from the locked toolchain, and the lock matches the
artefact that shipped. #build-lock

requirements.txt pinned nothing (numpy>=1.24, scipy>=1.10, lz4>=4.0) and
build_exe.ps1 installed "the latest PyInstaller" into whatever interpreter was on
PATH, so nothing recorded -- or enforced -- which libraries produced a release. A
silent numpy or scipy change is a geometry change until a parity run says
otherwise, and scipy ships no dist-info in the bundle, so without the lock its
version is unrecoverable from the artefact.
"""
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
LOCK = PROJ / "requirements-build.lock"
BUNDLE = PROJ / "dist" / "CBBEtoUBE" / "_internal"
BUILD_SCRIPT = PROJ / "scripts" / "build_exe.ps1"
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_DIST_INFO = re.compile(r"^(.+)-([0-9][^-]*)\.dist-info$")


def locked(text=None):
    """{name: {"version": v, "hashes": [...]}} from the lock file."""
    assert text is not None or LOCK.is_file(), (
        "requirements-build.lock is missing: nothing records which libraries a "
        "release was built from")
    out, cur = {}, None
    for line in (text if text is not None else LOCK.read_text(encoding="utf-8")).splitlines():
        # pip ignores everything after "#", so a commented-out hash is NOT a hash.
        # Reading the raw line counted one, which made "a pin lost its hash"
        # invisible to the check below.
        line = line.split("#", 1)[0].strip()
        m = _PIN.match(line)
        if m:
            cur = m.group(1).lower().replace("_", "-")
            out[cur] = {"version": m.group(2), "hashes": []}
        if cur:
            out[cur]["hashes"].extend(_HASH.findall(line))
    return out


def _requirements():
    names = []
    for line in (PROJ / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"[<>=!~]", line, 1)[0].strip().lower())
    return names


def test_every_requirement_is_pinned_with_a_hash():
    lock = locked()
    assert len(lock) >= 8, f"the lock parsed as {len(lock)} package(s): {sorted(lock)}"
    for name in _requirements() + ["pyinstaller", "setuptools"]:
        assert name in lock, f"{name} is a build input but the lock does not pin it"
        assert lock[name]["hashes"], f"{name} is pinned without a --hash line"
    for name, row in lock.items():
        assert row["hashes"], f"{name} is pinned without a --hash line"


def test_the_lock_matches_the_bundle_that_shipped():
    """The artefact a stranger receives, not the tree: the versions in the
    bundle's dist-info must be the versions the lock installs. scipy has no
    dist-info there -- the lock is its only record -- so it is checked against
    the build stamp below when one exists."""
    lock = locked()
    matched = {}
    for d in BUNDLE.iterdir():
        m = _DIST_INFO.match(d.name)
        if m and m.group(1).lower().replace("_", "-") in lock:
            matched[m.group(1).lower().replace("_", "-")] = m.group(2)
    # numpy and lz4 BY NAME, not a count: the count was 3 until the build moved
    # into the locked environment, which dropped setuptools' dist-info (with the
    # rest of the build machine's leakage) out of the bundle. These two are the
    # libraries the exe ships and computes with.
    assert {"numpy", "lz4"} <= set(matched), (
        f"the bundle no longer names numpy and lz4 in its own dist-info: {matched}")
    for name, version in sorted(matched.items()):
        assert lock[name]["version"] == version, (
            f"the bundle ships {name} {version} but the lock pins "
            f"{lock[name]['version']} -- a rebuild would not reproduce it")
    assert "scipy" in lock, "scipy has no dist-info in the bundle; the lock is its only record"

    stamp = PROJ / "src" / "_build_stamp.py"
    if stamp.is_file():          # gitignored: written by a build, so CI has none
        ns = {}
        exec(compile(stamp.read_text(encoding="utf-8"), str(stamp), "exec"), ns)
        for name, version in ns["BUILD"].get("toolchain", {}).items():
            if name in lock:
                assert lock[name]["version"] == version, (
                    f"the last build used {name} {version}, the lock pins "
                    f"{lock[name]['version']}")


def test_the_build_script_builds_from_the_locked_environment():
    """A build from whatever `python` is on PATH is the drift this item exists to
    stop, so the script must create its environment FROM the lock, with hashes,
    and run PyInstaller out of that environment."""
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    for needle in ("requirements-build.lock", "--require-hashes", ".venv-build",
                   "BUILD REFUSED"):
        assert needle in text, f"build_exe.ps1 no longer mentions {needle!r}"
    assert "& $venvPy -m PyInstaller" in text, (
        "the build must run PyInstaller from the locked environment")
    assert "& python -m PyInstaller" not in text, (
        "the build still runs PyInstaller from whatever python is on PATH")


def test_the_lock_parser_reads_pins_and_catches_a_loose_line():
    """Control: the parser finds a pinned package with its hash, and a version
    range or a missing hash is visible to the checks above."""
    good = "numpy==2.2.6 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    assert locked(good) == {"numpy": {"version": "2.2.6", "hashes": ["a" * 64]}}
    assert locked("numpy>=1.24\n") == {}
    assert locked("lz4==4.4.5\n")["lz4"]["hashes"] == []
    # a commented-out hash is not a hash: pip ignores the line, and counting it
    # made "this pin lost its hash" invisible
    assert locked("lz4==4.4.5\n    # --hash=sha256:" + "b" * 64 + "\n")["lz4"]["hashes"] == []
