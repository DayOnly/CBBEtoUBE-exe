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

"""Build identity: what a build stamps into the exe and leaves beside it.

#build-identity. Used by CBBEtoUBE.spec AT BUILD TIME and by
scripts/release_gate.py. The stamp used to be written by scripts/build_exe.ps1
before PyInstaller ran, so a direct `pyinstaller CBBEtoUBE.spec` silently reused
whatever src/_build_stamp.py an earlier build had left -- and one committed exe
shipped with a stale stamp that way. The spec now asks this module.

  BUILD_INPUTS        everything the build reads, in ONE place
  build_stamp()       git commit, dirty flag over BUILD_INPUTS, build time, the
                      SOURCE_DATE_EPOCH it was pinned to, toolchain versions
  write_stamp()       src/_build_stamp.py from that
  version_from_source(), version_tuple(), version_line()
  version_resource()  the PE version resource for EXE(version=...)
  write_manifest()    VERSION.txt and SHA256SUMS in the bundle folder
  check_manifest()    a bundle's files against its SHA256SUMS

Standard library only at import time; PyInstaller is imported only by
version_resource()."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRODUCT = "CBBEtoUBE"
MANIFEST = "SHA256SUMS"
VERSION_FILE = "VERSION.txt"

# Everything the build reads. The dirty flag is computed over this list and the
# release gate's "inputs unchanged" clause compares it between the stamp and the
# tag; tests/test_release_gate.py checks it against the paths the spec names.
# This file is on it because the spec imports it.
BUILD_INPUTS = ("src", "cbbe_to_ube_main.py", "CBBEtoUBE.spec", ".pynifly",
                "LICENSE", "THIRD-PARTY-NOTICES.md", "USING.md", "REPORTING.md",
                "assets", "scripts/build_identity.py")


def _git(repo, *args) -> "str | None":
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def toolchain() -> dict:
    """Versions of what the bundle is built from, for a stranger's bug report."""
    out = {"python": sys.version.split()[0]}
    for name in ("PyInstaller", "numpy", "scipy", "lz4"):
        try:
            out[name.lower()] = str(getattr(__import__(name), "__version__", "?"))
        except Exception:
            out[name.lower()] = None
    return out


def build_stamp(repo=REPO, *, now: "float | None" = None) -> dict:
    """The dict written to src/_build_stamp.py as BUILD."""
    git = _git(repo, "rev-parse", "--short", "HEAD") or "nogit"
    status = _git(repo, "status", "--porcelain", "--", *BUILD_INPUTS)
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    pinned = int(epoch) if epoch.isdigit() else None
    if now is None:
        now = pinned if pinned is not None else time.time()
    tools = toolchain()
    return {
        "git": git,
        "dirty": bool(status),
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        # The epoch the build was pinned to; None for a wall-clock build.
        # scripts/build_exe.ps1 pins it to the commit's own time, which makes
        # two builds of one commit byte-identical (MEASURED 2026-09-16: 0 of
        # 1,121 bundle files differ; unpinned, the exe differs in 180 bytes),
        # and the release gate's "build time" clause reads it back so a
        # runner's rebuild can be compared with the tracked exe.
        # #reproducible-build
        "source_date_epoch": pinned,
        "pyinstaller": tools.get("pyinstaller"),
        "toolchain": tools,
    }


def write_stamp(path, stamp: dict) -> Path:
    """src/_build_stamp.py: one literal dict, so the release gate can read it
    back out of the exe by walking bytecode, without running it."""
    lines = ["# GENERATED at build time by scripts/build_identity.py (called "
             "from CBBEtoUBE.spec) -- gitignored, do not edit.",
             "BUILD = {"]
    lines += [f"    {key!r}: {value!r}," for key, value in stamp.items()]
    lines.append("}")
    path = Path(path)
    path.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    return path


def version_from_source(source: "bytes | str | None") -> "str | None":
    """`__version__` from the text of src/version.py, without importing it."""
    if source is None:
        return None
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and any(getattr(t, "id", None) == "__version__"
                        for t in node.targets)):
            return node.value.value
    return None


def version_tuple(version: str) -> tuple:
    """The four numbers Windows wants: "1.4.1" -> (1, 4, 1, 0); a pre-release
    suffix is dropped ("1.3-alpha" -> (1, 3, 0, 0))."""
    core = re.split(r"[-+ ]", version.strip(), maxsplit=1)[0]
    numbers = [min(int(n), 65535) for n in re.findall(r"\d+", core)][:4]
    return tuple(numbers + [0] * (4 - len(numbers)))


def version_line(version: str, stamp: dict) -> str:
    """The one line of VERSION.txt: `CBBEtoUBE 1.4.2 @ 1a2b3c4 2026-...Z`."""
    git = str(stamp.get("git")) + ("+dirty" if stamp.get("dirty") else "")
    return f"{PRODUCT} {version} @ {git} {stamp.get('built_utc')}"


def version_resource(version: str, stamp: dict):
    """The PE version resource: what Explorer's Details tab and PowerShell's
    `(Get-Item CBBEtoUBE.exe).VersionInfo` show."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
        VarStruct, VSVersionInfo)
    numbers = version_tuple(version)
    git = str(stamp.get("git")) + ("+dirty" if stamp.get("dirty") else "")
    strings = [
        ("CompanyName", "DayOnly"),
        ("FileDescription", "CBBE/3BA to UBE armor converter"),
        ("FileVersion", f"{version} ({git})"),
        ("InternalName", PRODUCT),
        ("LegalCopyright", "Copyright (C) 2026 DayOnly. GPL-3.0-or-later."),
        ("OriginalFilename", f"{PRODUCT}.exe"),
        ("ProductName", PRODUCT),
        ("ProductVersion", version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in strings])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(bundle, line: str) -> tuple:
    """Write VERSION.txt, then SHA256SUMS listing every file in the bundle
    (VERSION.txt included, SHA256SUMS itself not), in `sha256sum -c` format."""
    bundle = Path(bundle)
    (bundle / VERSION_FILE).write_bytes((line + "\n").encode("utf-8"))
    rows = sorted((p.relative_to(bundle).as_posix(), p) for p in bundle.rglob("*")
                  if p.is_file())
    text = "".join(f"{_sha256(p)}  {rel}\n" for rel, p in rows if rel != MANIFEST)
    (bundle / MANIFEST).write_bytes(text.encode("utf-8"))
    return bundle / VERSION_FILE, bundle / MANIFEST


def check_manifest(manifest_text: str, files: dict, sha256_of) -> dict:
    """Compare a manifest with the files that are actually there.

    `files` is the set of relative paths present (SHA256SUMS itself ignored);
    `sha256_of(rel)` returns the hex digest of one of them. Returns the lists
    "malformed", "missing", "different" and "unlisted" -- all empty when the
    bundle is exactly what was built."""
    listed, malformed = {}, []
    for number, row in enumerate(manifest_text.splitlines(), 1):
        if not row.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", row)
        if not m:
            malformed.append(f"line {number}")
            continue
        listed[m.group(2)] = m.group(1)
    present = set(files) - {MANIFEST}
    return {
        "malformed": malformed,
        "missing": sorted(set(listed) - present),
        "different": sorted(r for r in set(listed) & present
                            if sha256_of(r) != listed[r]),
        "unlisted": sorted(present - set(listed)),
    }
