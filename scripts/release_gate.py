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

"""Release gate: does a tagged build come from the tree it is tagged on?

    python scripts/release_gate.py stamp-check <tag or commit> [--asset <zip>]
    python scripts/release_gate.py deployed-diff <installed tool folder>
    python scripts/release_gate.py manifest-check <installed tool folder>
    python scripts/release_gate.py rebuild-plan <tag or commit>
    python scripts/release_gate.py rebuild-check <tag or commit> <fresh build folder>
    python scripts/release_gate.py bundle-scan <tag, commit, exe or tool folder> [--markers <json>]

#release-gate. Every release so far was checked by hand, and the rule carried in
the project's notes was wrong: "the exe's stamp names the tag's parent". v1.4
and v1.4.1 are both MERGE-tagged -- the tag is a merge whose second parent is
the commit that added the rebuilt exe -- so that rule fails on both published
releases while both are correct. The rule that holds, checked here:

  stamp readable     the exe's build stamp, read out of its bundled module
                     archive. Never a byte search: every module in the archive
                     is compressed on its own, so a grep finds nothing at all.
  build commit       the stamp names the FIRST PARENT of the newest commit that
                     changed the exe (reachable from the ref), and is not dirty.
  in history         that commit is an ancestor of the ref. A build made before
                     an amend or rebase fails here: rebuild after either.
  inputs unchanged   everything the build reads (BUILD_INPUTS) is identical
                     between the stamp's commit and the ref.
  build time         the stamp's SOURCE_DATE_EPOCH is the stamp commit's own
                     commit time, so a rebuild of that commit reproduces the
                     exe byte for byte (NOT CHECKED for a build made before
                     the build time was pinned: rebuild before tagging).
  version            the exe's version == src/version.py == the first dated
                     CHANGELOG heading == the tag name without "v" (the last
                     only when the ref is a tag, e.g. not for a pre-tag HEAD).
  manifest           the bundle's own SHA256SUMS matches its files, and
                     VERSION.txt names the exe's version (NOT CHECKED for a
                     build older than the manifest).
  release asset      with --asset: every file of the release zip's bundle is
                     the tracked dist/ file at the ref, byte for byte.

`deployed-diff` compares an installed tool folder with dist/ file by file,
ignoring only the state files the tool writes beside itself
(STATE_FILE_GLOBS), and reports the installed exe's stamp. `manifest-check`
needs no repository: it checks a folder against the SHA256SUMS the build left
in it.

`rebuild-plan` prints, as key=value lines a workflow can read, what a rebuild
of the exe at a ref needs: the commit to check out, the epoch and the Python
the stamp names, and whether that can reproduce it at all. `rebuild-check`
compares a fresh build folder with the tracked bundle at a ref: the exe, the
file list, the repository's own files and the hash-locked libraries must be
identical; files that come from the interpreter installation the build ran on
are reported, not judged (#reproducible-build). `bundle-scan` checks an exe
against release-markers.json: for every fix a release claims, a module or a
name that exists only with it must be in the exe, and what must never ship
must not be. The entry script is read as well as the PYZ (#release-markers).

Stdlib only, and nothing from the exe is executed: module constants are read by
walking their bytecode. The exe's modules are bytecode for the Python it was
built with (3.10), so the stamp can only be read by that version; any other is
refused (exit 2), never reported as a pass.

Exit codes: 0 every checked clause passed; 1 a clause failed; 2 the gate could
not run (unknown ref, unreadable archive, wrong Python, no git)."""
from __future__ import annotations

import argparse
import dis
import fnmatch
import hashlib
import importlib.util
import json
import marshal
import re
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUNDLE = "dist/CBBEtoUBE"
EXE = f"{BUNDLE}/CBBEtoUBE.exe"
MARKERS = "release-markers.json"     # read by bundle-scan; docs/RELEASING.md says how

# What the build reads, the manifest names and version parsing live in
# scripts/build_identity.py, which the spec imports: one list for the build's
# dirty flag and for this gate. tests/test_release_gate.py checks the list
# against the paths the spec names. #build-identity
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts.build_identity import (  # noqa: E402
    BUILD_INPUTS, MANIFEST, VERSION_FILE, check_manifest, version_from_source,
    version_tuple)

# The files the tool writes beside its own exe. They are never part of a
# bundle, so an installed folder may hold them. MUST equal $stateFiles in
# scripts/deploy_exe.ps1 -- a test pins the two lists together.
STATE_FILE_GLOBS = (
    "CBBEtoUBE_settings.json",
    "CBBEtoUBE_settings.json.bak*",
    "CBBEtoUBE_settings.json.prebuild-*",
    "CBBEtoUBE_exclusions.json",
    "CBBEtoUBE_*.log",
    "CBBEtoUBE_*failures.json",
)

PASS, FAIL, NOT_CHECKED = "PASS", "FAIL", "NOT CHECKED"


class GateError(RuntimeError):
    """The gate could not run at all (exit 2). Never a verdict."""


class ArchiveError(GateError):
    """The executable's bundled module archive could not be read."""


# ---------------------------------------------------------------- the archive

_COOKIE_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
_COOKIE = struct.Struct("!8sIIII64s")      # magic, length, toc offset/length, ...
_TOC_ENTRY = struct.Struct("!IIIIBc")      # entry len, offset, size, raw size, zlib, type


def _carchive(exe: bytes):
    """(archive start offset, [(typecode, name, offset, length, uncompressed
    length, zlib flag)]) for the outer archive of a PyInstaller executable."""
    ck = exe.rfind(_COOKIE_MAGIC)
    if ck < 0 or ck + _COOKIE.size > len(exe):
        raise ArchiveError("not a PyInstaller executable (no archive trailer)")
    _magic, arch_len, toc_off, toc_len, _pyver, _lib = _COOKIE.unpack_from(exe, ck)
    start = ck + _COOKIE.size - arch_len
    toc = exe[start + toc_off:start + toc_off + toc_len]
    entries, pos = [], 0
    while pos < len(toc):
        elen, eoff, dlen, raw, zflag, typecode = _TOC_ENTRY.unpack_from(toc, pos)
        if elen < _TOC_ENTRY.size:
            raise ArchiveError("corrupt archive table of contents")
        name = toc[pos + _TOC_ENTRY.size:pos + elen].rstrip(b"\0").decode("utf-8", "replace")
        entries.append((typecode, name, eoff, dlen, raw, zflag))
        pos += elen
    return start, entries


def _pyz_index(exe: bytes):
    """(offset of the PYZ archive in `exe`, {module name: (type, offset, length)})."""
    start, entries = _carchive(exe)
    pyz = None
    for typecode, _name, eoff, dlen, _raw, zflag in entries:
        if typecode == b"z":
            pyz = (eoff, dlen, zflag)
    if pyz is None:
        raise ArchiveError("the executable has no PYZ module archive")
    if pyz[2]:
        raise ArchiveError("the PYZ archive is itself compressed (unsupported)")
    base, end = start + pyz[0], start + pyz[0] + pyz[1]
    if exe[base:base + 4] != b"PYZ\0":
        raise ArchiveError("PYZ archive magic mismatch")
    bytecode_magic = exe[base + 4:base + 8]
    if bytecode_magic != importlib.util.MAGIC_NUMBER:
        raise ArchiveError(
            f"the exe's modules are bytecode for a different Python (magic "
            f"{bytecode_magic!r}; this is {sys.version.split()[0]}) -- run the "
            "gate with the interpreter the exe was built with")
    (pyz_toc_off,) = struct.unpack_from("!i", exe, base + 8)
    return base, dict(marshal.loads(exe[base + pyz_toc_off:end]))


def read_pyz_modules(exe: bytes, names) -> dict:
    """{name: code object, or None if absent} for modules in a PyInstaller
    executable's PYZ archive. Nothing is executed."""
    base, pyz_toc = _pyz_index(exe)
    out = {}
    for name in names:
        entry = pyz_toc.get(name)
        if entry is None:
            out[name] = None
            continue
        _type, off, length = entry
        out[name] = marshal.loads(zlib.decompress(exe[base + off:base + off + length]))
    return out


def read_entry_scripts(exe: bytes) -> dict:
    """{name: code object} for the scripts the bootloader runs, the entry point
    among them. They sit in the OUTER archive, not the PYZ, so a scan of the
    PYZ alone never sees cbbe_to_ube_main.py -- and the 1.4.1 log rotation is
    defined there. PyInstaller's own bootstrap and runtime-hook scripts (pyi*)
    are left out: they are not ours to claim. Nothing is executed."""
    _pyz_index(exe)          # the bytecode-version check, before any marshal.loads
    start, entries = _carchive(exe)
    out = {}
    for typecode, name, eoff, dlen, _raw, zflag in entries:
        if typecode != b"s" or name.startswith("pyi"):
            continue
        data = exe[start + eoff:start + eoff + dlen]
        out[name] = marshal.loads(zlib.decompress(data) if zflag else data)
    return out


def code_names(code) -> set:
    """Every identifier and string constant of a code object and the code
    objects nested in it: function, class and attribute names, globals, dict
    keys, argument names. What a release marker `name` is looked up in."""
    out = set(code.co_names) | set(code.co_varnames)
    for const in code.co_consts:
        if isinstance(const, str):
            out.add(const)
        elif hasattr(const, "co_consts"):
            out |= code_names(const)
    return out


def _string_constants(code):
    """Every str constant of a code object and of the code objects nested in it
    -- docstrings included, which is where a wrapped source line ends up whole."""
    for const in code.co_consts:
        if isinstance(const, str):
            yield const
        elif hasattr(const, "co_consts"):
            yield from _string_constants(const)


def string_constant_hits(modules: dict, pattern) -> list:
    """[(module, matched text)] for `pattern` over every string constant of
    `modules` ({name: code object or None}). #bundle-names"""
    hits = []
    for name in sorted(modules):
        code = modules[name]
        if code is None:
            continue
        for s in _string_constants(code):
            m = pattern.search(s)
            if m:
                hits.append((name, m.group()))
    return hits


def bundle_name_hits(exe: bytes, pattern, packages=("src", "pyn")) -> list:
    """`string_constant_hits` over the exe's own bundled modules. The PYZ is
    zlib-compressed per module, so no text scan of the exe ever sees inside it.
    Raises ArchiveError when the archive cannot be read. #bundle-names"""
    _base, toc = _pyz_index(exe)
    names = [n for n in toc if n.split(".")[0] in packages]
    return string_constant_hits(read_pyz_modules(exe, names), pattern)


_NO_OPS = {"NOP", "RESUME", "CACHE", "EXTENDED_ARG", "PRECALL"}


def module_constants(code) -> dict:
    """Top-level `NAME = <literal>` assignments of a module code object,
    recovered by walking its bytecode rather than running it. The walk stops at
    the first instruction that is not part of building a literal, so a name
    assigned from anything else -- or after it -- is simply not reported."""
    stack: list = []
    out: dict = {}
    for ins in dis.get_instructions(code):
        op = ins.opname
        if op in _NO_OPS:
            continue
        if op == "LOAD_CONST":
            stack.append(ins.argval)
        elif op == "BUILD_CONST_KEY_MAP":
            keys = stack.pop()
            vals = stack[len(stack) - ins.arg:]
            del stack[len(stack) - ins.arg:]
            stack.append(dict(zip(keys, vals)))
        elif op == "BUILD_MAP":
            items = stack[len(stack) - 2 * ins.arg:]
            del stack[len(stack) - 2 * ins.arg:]
            stack.append(dict(zip(items[0::2], items[1::2])))
        elif op in ("BUILD_TUPLE", "BUILD_LIST"):
            items = stack[len(stack) - ins.arg:]
            del stack[len(stack) - ins.arg:]
            stack.append(tuple(items) if op == "BUILD_TUPLE" else items)
        elif op == "STORE_NAME" and stack:
            out[ins.argval] = stack.pop()
        else:
            break
    return out


def read_stamp(exe: bytes):
    """(BUILD dict or None, version string or None) from an exe's bytes."""
    mods = read_pyz_modules(exe, ("src._build_stamp", "src.version"))
    stamp, ver = mods["src._build_stamp"], mods["src.version"]
    build = module_constants(stamp).get("BUILD") if stamp is not None else None
    version = module_constants(ver).get("__version__") if ver is not None else None
    return (build if isinstance(build, dict) else None,
            version if isinstance(version, str) else None)


def inspect_exe(exe: bytes) -> dict:
    """What `bundle-scan` reads from an exe: every PYZ module name, the names
    inside our own modules and the entry script, and the version. #release-markers"""
    _base, toc = _pyz_index(exe)
    ours = sorted(n for n in toc if n.split(".")[0] == "src")
    names = {n: code_names(c) for n, c in read_pyz_modules(exe, ours).items()
             if c is not None}
    names.update({n: code_names(c) for n, c in read_entry_scripts(exe).items()})
    _build, version = read_stamp(exe)
    return {"modules": set(toc), "names": names, "version": version}


# ------------------------------------------------------------------------ git

def _git(repo, *args, ok=(0,)) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    except FileNotFoundError as e:
        raise GateError("git is not installed") from e
    if r.returncode not in ok:
        err = r.stderr.decode("utf-8", "replace").strip()
        raise GateError(f"git {' '.join(args)}: {err}")
    return r


def _text(repo, *args) -> str:
    return _git(repo, *args).stdout.decode("utf-8", "replace").strip()


def _commit_of(repo, ref) -> "str | None":
    r = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
             ok=(0, 1, 128))
    sha = r.stdout.decode("ascii", "replace").strip()
    return sha if r.returncode == 0 and sha else None


def _is_tag(repo, ref) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}",
                ok=(0, 1, 128)).returncode == 0


def _file_at(repo, commit, path) -> "bytes | None":
    # cat-file, not `git show`: Windows git stat()s a `<commit>:<path>` argument
    # to `show` as though it were a file, and fails "Filename too long" once the
    # repository sits a little under 200 characters deep -- MEASURED on a correct
    # chain in a deep temp folder, where git's own objects were still readable.
    # The plumbing command never treats the argument as a path.
    r = _git(repo, "cat-file", "blob", f"{commit}:{path}", ok=(0, 128))
    return r.stdout if r.returncode == 0 else None


_DATED_HEADING = re.compile(r"^## +(\S+) +[—–-] +\d{4}-\d{2}-\d{2}\s*$", re.M)


def first_dated_heading(changelog: str) -> "str | None":
    """The version of the first `## <version> - <date>` heading. `## Unreleased`
    carries no date, so it is skipped by construction."""
    m = _DATED_HEADING.search(changelog or "")
    return m.group(1) if m else None


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _build_time_verdict(repo, stamp_commit, build) -> tuple:
    """Is the build pinned to its commit's own time? Then a rebuild of that
    commit reproduces it byte for byte, and the release workflow can compare
    a runner's build with the tracked exe (`rebuild-check`). #reproducible-build"""
    epoch = (build or {}).get("source_date_epoch")
    commit_time = _text(repo, "log", "-1", "--format=%ct", stamp_commit)
    if epoch is None:
        return (NOT_CHECKED, "build time",
                "the stamp carries no SOURCE_DATE_EPOCH -- built before the build "
                "time was pinned, so no rebuild reproduces it; rebuild before tagging")
    if str(epoch) != commit_time:
        return (FAIL, "build time",
                f"the stamp's SOURCE_DATE_EPOCH {epoch} is not {stamp_commit[:7]}'s "
                f"commit time {commit_time}, so a rebuild of that commit cannot "
                "reproduce the exe; rebuild with scripts/build_exe.ps1")
    return (PASS, "build time",
            f"SOURCE_DATE_EPOCH {epoch} is {stamp_commit[:7]}'s commit time, so a "
            "rebuild of that commit reproduces the exe")


# ----------------------------------------------------------------- the clauses

def stamp_check(ref, repo=REPO, *, asset=None, read=read_stamp) -> list:
    """[(status, clause, detail)] for `ref`. Raises GateError if it cannot run."""
    repo = Path(repo)
    commit = _commit_of(repo, ref)
    if commit is None:
        raise GateError(f"{ref!r} is not a commit in {repo}")
    exe_commit = _text(repo, "log", "-1", "--format=%H", commit, "--", EXE)
    exe = _file_at(repo, commit, EXE) if exe_commit else None
    if exe is None:
        raise GateError(f"{EXE} does not exist at {ref}")
    build, version = read(exe)
    verdicts = []

    def add(status, clause, detail):
        verdicts.append((status, clause, detail))

    def short(sha):
        return sha[:7] if sha else "-"

    if build:
        add(PASS, "stamp readable",
            f"git {build.get('git')}, dirty {build.get('dirty')}, built "
            f"{build.get('built_utc')}, version {version}")
    else:
        add(FAIL, "stamp readable", "the exe carries no build stamp")

    stamp_git = (build or {}).get("git")
    stamp = _commit_of(repo, stamp_git) if stamp_git else None
    parents = _text(repo, "show", "-s", "--format=%P", exe_commit).split()
    first_parent = parents[0] if parents else None
    if stamp is None:
        add(FAIL, "build commit",
            f"the stamp names {stamp_git!r}, which is not a commit here")
    elif stamp != first_parent:
        add(FAIL, "build commit",
            f"the exe was last set by {short(exe_commit)}, whose first parent "
            f"is {short(first_parent)}, but the stamp names {short(stamp)}")
    elif build.get("dirty"):
        add(FAIL, "build commit",
            f"the stamp names {short(stamp)} but was built from a DIRTY tree")
    else:
        add(PASS, "build commit",
            f"the exe was last set by {short(exe_commit)}; its first parent "
            f"{short(first_parent)} is the stamp")

    if stamp is None:
        add(FAIL, "in history", "no stamp commit to look for")
    elif _git(repo, "merge-base", "--is-ancestor", stamp, commit,
              ok=(0, 1)).returncode == 0:
        add(PASS, "in history", f"{short(stamp)} is an ancestor of {ref}")
    else:
        add(FAIL, "in history",
            f"{short(stamp)} is not in {ref}'s history -- a build made before "
            "an amend or rebase; rebuild")

    if stamp is None:
        add(FAIL, "inputs unchanged", "no stamp commit to compare with")
    else:
        changed = _text(repo, "diff", "--name-only", stamp, commit, "--",
                        *BUILD_INPUTS).splitlines()
        if changed:
            add(FAIL, "inputs unchanged",
                f"{len(changed)} build input(s) changed after the build: "
                + ", ".join(changed[:8]) + (" ..." if len(changed) > 8 else ""))
        else:
            add(PASS, "inputs unchanged",
                f"identical between {short(stamp)} and {ref}")

    if stamp is None:
        add(FAIL, "build time", "no stamp commit to compare with")
    else:
        add(*_build_time_verdict(repo, stamp, build))

    sources = {
        "exe": version,
        "src/version.py": version_from_source(_file_at(repo, commit, "src/version.py")),
        "CHANGELOG": first_dated_heading(
            (_file_at(repo, commit, "CHANGELOG.md") or b"").decode("utf-8", "replace")),
    }
    tagged = _is_tag(repo, ref)
    if tagged:
        sources["tag"] = ref[1:] if ref.startswith("v") else ref
    if None in sources.values() or len(set(sources.values())) != 1:
        add(FAIL, "version",
            "; ".join(f"{k} {v!r}" for k, v in sources.items()))
    else:
        add(PASS, "version",
            f"{version} in {', '.join(sources)}"
            + ("" if tagged else f" ({ref} is not a tag, so no tag name was compared)"))

    add(*_manifest_verdict(repo, commit, version))

    if asset is None:
        add(NOT_CHECKED, "release asset",
            "pass --asset <release zip> once the release is uploaded")
    else:
        add(*_asset_verdict(repo, commit, Path(asset)))
    return verdicts


def _bundle_tree(repo, commit) -> dict:
    """{path inside the bundle: git blob id} at `commit`."""
    tree = {}
    listing = _git(repo, "ls-tree", "-r", "-z", commit, "--", BUNDLE).stdout
    for item in listing.decode("utf-8", "replace").split("\0"):
        if not item:
            continue
        meta, path = item.split("\t", 1)
        _mode, kind, oid = meta.split()
        if kind == "blob":
            tree[path[len(BUNDLE) + 1:]] = oid
    return tree


def _blob_sha256(repo, oids) -> dict:
    """{blob id: sha256} for many blobs through one `git cat-file --batch`."""
    oids = list(oids)
    try:
        proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                              input=b"".join(o.encode("ascii") + b"\n" for o in oids),
                              capture_output=True)
    except FileNotFoundError as e:
        raise GateError("git is not installed") from e
    if proc.returncode != 0:
        raise GateError("git cat-file --batch: "
                        + proc.stderr.decode("utf-8", "replace").strip())
    out, pos, digests = memoryview(proc.stdout), 0, {}
    for oid in oids:
        end = proc.stdout.index(b"\n", pos)
        header = proc.stdout[pos:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise GateError(f"git cat-file --batch: unexpected {bytes(header)!r}")
        size = int(header[2])
        digests[oid] = hashlib.sha256(out[end + 1:end + 1 + size]).hexdigest()
        pos = end + 1 + size + 1
    return digests


def _stated_version(text: str) -> "str | None":
    """The version VERSION.txt names: `CBBEtoUBE 1.4.2 @ 1a2b3c4 ...`."""
    words = (text or "").split()
    return words[1] if len(words) >= 2 else None


def _manifest_verdict(repo, commit, version) -> tuple:
    tree = _bundle_tree(repo, commit)
    if MANIFEST not in tree:
        return (NOT_CHECKED, "manifest",
                f"no {MANIFEST} in this build -- it predates the manifest")
    text = (_file_at(repo, commit, f"{BUNDLE}/{MANIFEST}") or b"").decode("utf-8", "replace")
    digests = _blob_sha256(repo, sorted(set(tree.values())))
    problems = check_manifest(text, set(tree), lambda rel: digests[tree[rel]])
    stated = _stated_version(
        (_file_at(repo, commit, f"{BUNDLE}/{VERSION_FILE}") or b"").decode("utf-8", "replace"))
    parts = [f"{len(v)} {k}: {', '.join(v[:5])}" for k, v in problems.items() if v]
    if stated != version:
        parts.append(f"{VERSION_FILE} names {stated!r} but the exe is {version!r}")
    if parts:
        return FAIL, "manifest", "; ".join(parts)
    return (PASS, "manifest",
            f"all {len(tree) - 1} files match {MANIFEST}; {VERSION_FILE} names {stated}")


def _asset_verdict(repo, commit, zip_path) -> tuple:
    tree = _bundle_tree(repo, commit)
    if not tree:
        return FAIL, "release asset", f"no bundle files under {BUNDLE} at the ref"
    prefix = Path(BUNDLE).name + "/"
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise GateError(f"cannot open the release asset {zip_path}: {e}") from e
    got, outside = {}, []
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.filename.startswith(prefix):
                got[info.filename[len(prefix):]] = _git_blob_id(zf.read(info))
            else:
                outside.append(info.filename)
    missing = sorted(set(tree) - set(got))
    extra = sorted(set(got) - set(tree))
    differ = sorted(p for p in set(tree) & set(got) if tree[p] != got[p])
    if missing or extra or differ or outside:
        parts = [f"{len(label)} {name}: {', '.join(label[:5])}"
                 for name, label in (("missing", missing), ("extra", extra),
                                     ("different", differ),
                                     ("outside the bundle folder", outside))
                 if label]
        return FAIL, "release asset", "; ".join(parts)
    return (PASS, "release asset",
            f"all {len(tree)} bundle files in {zip_path.name} are the tagged ones")


# -------------------------------------------------- rebuild-plan / rebuild-check

def rebuild_plan(ref, repo=REPO, *, read=read_stamp) -> dict:
    """What a rebuild of the exe at `ref` needs, for the release workflow: the
    commit to check out (the stamp's), the epoch and the Python it names, and
    whether a rebuild can reproduce the exe at all. #reproducible-build"""
    repo = Path(repo)
    commit = _commit_of(repo, ref)
    if commit is None:
        raise GateError(f"{ref!r} is not a commit in {repo}")
    exe = _file_at(repo, commit, EXE)
    if exe is None:
        raise GateError(f"{EXE} does not exist at {ref}")
    build, _version = read(exe)
    stamp_git = (build or {}).get("git")
    stamp = _commit_of(repo, stamp_git) if stamp_git else None
    if stamp is None:
        raise GateError(f"the stamp names {stamp_git!r}, which is not a commit here")
    status, _clause, reason = _build_time_verdict(repo, stamp, build)
    tools = build.get("toolchain") or {}
    return {"commit": stamp, "epoch": build.get("source_date_epoch"),
            "python": tools.get("python"), "pyinstaller": tools.get("pyinstaller"),
            "reproducible": status == PASS, "reason": reason}


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def plan_lines(plan: dict) -> list:
    """`key=value` lines: what $GITHUB_OUTPUT takes, and a shell can read."""
    return [f"{key}={_plain(value)}" for key, value in plan.items()]


def _is_interpreter_file(rel: str) -> bool:
    """A bundle file that comes from the CPython installation a build ran on --
    its DLL and runtime, its extension modules, the stdlib archive built from
    its Lib/, Tcl/Tk with their data, and the licence texts copied from it --
    rather than from this repository or a hash-locked wheel. `rebuild-check`
    reports a difference here without judging it: a runner's CPython is not the
    release machine's build of the same version. #reproducible-build"""
    parts = rel.split("/")
    if parts[0] != "_internal" or len(parts) < 2:
        return False
    if len(parts) == 2:
        name = parts[1]
        if name == "NiflyDLL.dll":                 # vendored in .pynifly: ours
            return False
        return name.lower().endswith((".dll", ".pyd")) or name == "base_library.zip"
    if parts[1] == "licenses":
        return parts[2].startswith(("CPython", "Tcl"))
    return parts[1] in ("_tcl_data", "_tk_data", "tcl8")


def rebuild_check(ref, folder, repo=REPO) -> list:
    """A fresh build folder against the tracked bundle at `ref`, byte for byte.
    The exe, the file list, the repository's own files and the hash-locked
    libraries must be identical; interpreter files are reported, not judged.
    The folder's own SHA256SUMS is not compared (manifest-check reads it), and
    the state files the tool writes beside itself are ignored. #reproducible-build"""
    repo, folder = Path(repo), Path(folder)
    commit = _commit_of(repo, ref)
    if commit is None:
        raise GateError(f"{ref!r} is not a commit in {repo}")
    want = _bundle_tree(repo, commit)
    if not want:
        raise GateError(f"no bundle files under {BUNDLE} at {ref}")
    exe_rel = Path(EXE).name
    if not (folder / exe_rel).is_file():
        raise GateError(f"no build at {folder}")
    want.pop(MANIFEST, None)
    have = {r: p for r, p in _files(folder).items()
            if r != MANIFEST and not _is_state_file(r)}
    digests = _blob_sha256(repo, sorted(set(want.values())))
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    differ = sorted(r for r in set(want) & set(have)
                    if _sha256(have[r]) != digests[want[r]])
    program = [r for r in differ if r != exe_rel and not _is_interpreter_file(r)]
    interp = [r for r in differ if _is_interpreter_file(r)]
    n_program = sum(1 for r in want if r != exe_rel and not _is_interpreter_file(r))
    n_interp = sum(1 for r in want if _is_interpreter_file(r))
    verdicts = []
    if exe_rel in differ:
        note = ""
        try:
            tracked, _v = read_stamp(_file_at(repo, commit, EXE) or b"")
            fresh, _v = read_stamp((folder / exe_rel).read_bytes())
            if tracked and fresh:
                note = (f"; stamps: tracked git {tracked.get('git')} epoch "
                        f"{tracked.get('source_date_epoch')}, rebuilt git "
                        f"{fresh.get('git')} epoch {fresh.get('source_date_epoch')}")
        except ArchiveError:
            pass
        verdicts.append((FAIL, "exe", f"{exe_rel} differs from the tracked exe at {ref}{note}"))
    elif exe_rel in extra:
        verdicts.append((FAIL, "exe", f"no {exe_rel} is tracked at {ref}"))
    else:
        verdicts.append((PASS, "exe", f"{exe_rel} is byte-identical to the tracked exe at {ref}"))
    if missing or extra:
        parts = [f"{len(v)} {name}: {', '.join(v[:5])}"
                 for name, v in (("missing", missing), ("extra", extra)) if v]
        verdicts.append((FAIL, "file set", "; ".join(parts)))
    else:
        verdicts.append((PASS, "file set", f"the same {len(want)} files"))
    if program:
        verdicts.append((FAIL, "program files",
                         f"{len(program)} of {n_program} differ: " + ", ".join(program[:8])
                         + (" ..." if len(program) > 8 else "")))
    else:
        verdicts.append((PASS, "program files",
                         f"all {n_program} repository and locked-library files identical"))
    if interp:
        verdicts.append((NOT_CHECKED, "interpreter files",
                         f"{len(interp)} of {n_interp} differ ({', '.join(interp[:5])}"
                         f"{' ...' if len(interp) > 5 else ''}): the interpreter this build "
                         "ran on is not the release machine's build; the clauses above are "
                         "what a rebuild proves"))
    else:
        verdicts.append((PASS, "interpreter files", f"all {n_interp} identical"))
    return verdicts


# -------------------------------------------------------------- deployed-diff

def _is_state_file(rel: str) -> bool:
    """A file the tool writes beside its exe (top level only)."""
    return "/" not in rel and any(fnmatch.fnmatch(rel, g) for g in STATE_FILE_GLOBS)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def deployed_diff(folder, dist=None) -> list:
    folder = Path(folder)
    dist = Path(dist) if dist is not None else REPO / BUNDLE
    if not (dist / "CBBEtoUBE.exe").is_file():
        raise GateError(f"no build at {dist}")
    if not folder.is_dir():
        raise GateError(f"not a folder: {folder}")
    want = {r: p for r, p in _files(dist).items() if not _is_state_file(r)}
    have_all = _files(folder)
    have = {r: p for r, p in have_all.items() if not _is_state_file(r)}
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    differ = sorted(r for r in set(want) & set(have)
                    if want[r].stat().st_size != have[r].stat().st_size
                    or _sha256(want[r]) != _sha256(have[r]))
    verdicts = []
    if missing or extra or differ:
        parts = [f"{len(v)} {name}: {', '.join(v[:5])}"
                 for name, v in (("missing", missing), ("extra", extra),
                                 ("different", differ)) if v]
        verdicts.append((FAIL, "files", "; ".join(parts)))
    else:
        state = len(have_all) - len(have)
        verdicts.append((PASS, "files",
                         f"all {len(want)} program files identical to {dist} "
                         f"({state} state file(s) ignored)"))
    exe = folder / "CBBEtoUBE.exe"
    try:
        build, version = read_stamp(exe.read_bytes())
        if build:
            verdicts.append((PASS, "installed build",
                             f"version {version} @ {build.get('git')}"
                             + (" (dirty)" if build.get("dirty") else "")))
        else:
            verdicts.append((FAIL, "installed build", "the installed exe has no build stamp"))
    except (OSError, ArchiveError) as e:
        verdicts.append((NOT_CHECKED, "installed build", str(e)))
    return verdicts


def manifest_check(folder) -> list:
    """An installed tool folder against the SHA256SUMS its build left in it,
    ignoring only the state files the tool writes beside itself. No repo."""
    folder = Path(folder)
    manifest = folder / MANIFEST
    if not manifest.is_file():
        raise GateError(f"no {MANIFEST} in {folder} -- a build older than the "
                        "manifest, or not a tool folder")
    files = {r for r in _files(folder) if not _is_state_file(r)}
    problems = check_manifest(manifest.read_text(encoding="utf-8"), files,
                              lambda rel: _sha256(folder / rel))
    parts = [f"{len(v)} {k}: {', '.join(v[:5])}" for k, v in problems.items() if v]
    stated = _stated_version((folder / VERSION_FILE).read_text(encoding="utf-8")
                             if (folder / VERSION_FILE).is_file() else "")
    if parts:
        return [(FAIL, "manifest", "; ".join(parts))]
    return [(PASS, "manifest",
             f"all {len(files) - 1} program files match {MANIFEST}; "
             f"{VERSION_FILE} names {stated}")]


# ---------------------------------------------------------------- bundle-scan

def load_markers(path) -> dict:
    """release-markers.json: {"present": [...], "absent": [...]}. Each marker
    names a `module` (in the PYZ) or a `name` (in our modules or the entry
    script) and says what it `claims`; a present marker says `since` which
    version claims it. A list with nothing to find or nothing to refuse is
    refused itself: a scan that cannot fail proves nothing. #release-markers"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GateError(f"cannot read the marker list {path}: {e}") from e
    for kind in ("present", "absent"):
        rows = data.get(kind) if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise GateError(f"{path.name}: '{kind}' must list at least one marker -- a "
                            "scan with nothing to find, or nothing to refuse, proves nothing")
        for row in rows:
            ok = (isinstance(row, dict) and ("module" in row) != ("name" in row)
                  and isinstance(row.get("claims"), str) and row["claims"].strip()
                  and (kind == "absent" or isinstance(row.get("since"), str)))
            if not ok:
                raise GateError(f"{path.name}: a marker names a `module` or a `name`, says "
                                "what it `claims`, and a present one says `since` which "
                                f"version: {row!r}")
    return data


def _label(marker: dict) -> str:
    return marker.get("module") or marker.get("name")


def _marker_found(marker: dict, info: dict) -> "str | None":
    """Where the marker is in the exe, or None: the module name, or the
    unit(s) whose names hold it."""
    if "module" in marker:
        return marker["module"] if marker["module"] in info["modules"] else None
    units = sorted(u for u, names in info["names"].items() if marker["name"] in names)
    return ", ".join(units[:3]) if units else None


def _target_exe(target, repo) -> bytes:
    """The exe bytes at `target`: an exe path, a tool folder, or a git ref."""
    p = Path(str(target))
    if p.is_file():
        return p.read_bytes()
    if (p / Path(EXE).name).is_file():
        return (p / Path(EXE).name).read_bytes()
    commit = _commit_of(repo, str(target))
    if commit is None:
        raise GateError(f"{target!r} is neither an exe, a tool folder nor a commit in {repo}")
    exe = _file_at(repo, commit, EXE)
    if exe is None:
        raise GateError(f"{EXE} does not exist at {target}")
    return exe


def bundle_scan(target, markers=None, repo=REPO, *, inspect=inspect_exe) -> list:
    """The exe at `target` against the release marker list. #release-markers"""
    repo = Path(repo)
    rules = load_markers(Path(markers) if markers else repo / MARKERS)
    info = inspect(_target_exe(target, repo))
    version = info.get("version")
    have = version_tuple(version) if version else None
    claimed, later, missing = [], [], []
    for m in rules["present"]:
        if have is not None and version_tuple(m["since"]) > have:
            later.append(m)
        elif _marker_found(m, info):
            claimed.append(m)
        else:
            missing.append(m)
    verdicts = []
    if missing:
        verdicts.append((FAIL, "present markers",
                         f"{len(missing)} of {len(claimed) + len(missing)} marker(s) this "
                         "version claims are not in the exe: "
                         + "; ".join(f"{_label(m)} ({m['claims']})" for m in missing[:5])))
    elif not claimed:
        first = min(later, key=lambda m: version_tuple(m["since"]))["since"]
        verdicts.append((NOT_CHECKED, "present markers",
                         f"none of the {len(later)} marker(s) is claimed by version {version} "
                         f"-- nothing to check; the earliest claim is {first}"))
    else:
        verdicts.append((PASS, "present markers",
                         f"all {len(claimed)} marker(s) this version claims are in the exe"))
    if later:
        verdicts.append((NOT_CHECKED, "later markers",
                         f"{len(later)} marker(s) claimed by a version after {version}: "
                         + ", ".join(f"{_label(m)} (since {m['since']})" for m in later[:5])))
    shipped = [(m, where) for m in rules["absent"] for where in [_marker_found(m, info)] if where]
    if shipped:
        verdicts.append((FAIL, "absent markers",
                         f"{len(shipped)} of {len(rules['absent'])} marker(s) that must never "
                         "ship are in the exe: "
                         + "; ".join(f"{_label(m)} in {where} ({m['claims']})"
                                     for m, where in shipped[:5])))
    else:
        verdicts.append((PASS, "absent markers",
                         f"none of the {len(rules['absent'])} marker(s) that must never "
                         "ship is in the exe"))
    return verdicts


# ------------------------------------------------------------------------ CLI

def _report(title: str, verdicts: list) -> int:
    print(title)
    width = max(len(clause) for _, clause, _ in verdicts)
    for status, clause, detail in verdicts:
        print(f"  {status:<11}  {clause:<{width}}  {detail}")
    failed = [c for s, c, _ in verdicts if s == FAIL]
    unchecked = [c for s, c, _ in verdicts if s == NOT_CHECKED]
    if failed:
        print(f"VERDICT: FAIL ({', '.join(failed)})")
        return 1
    print("VERDICT: PASS"
          + (f" -- NOT CHECKED: {', '.join(unchecked)}" if unchecked else ""))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="release_gate",
        description="Check a tagged build against the chain it claims to come from.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("stamp-check", help="check the exe at a tag or commit")
    sc.add_argument("ref", help="tag (v1.4.1) or, before tagging, a commit (HEAD)")
    sc.add_argument("--asset", type=Path,
                    help="the uploaded release zip, to compare file for file")
    dd = sub.add_parser("deployed-diff",
                        help="compare an installed tool folder with dist/")
    dd.add_argument("folder", type=Path)
    dd.add_argument("--dist", type=Path, help=f"default: {BUNDLE} in this repo")
    mc = sub.add_parser("manifest-check",
                        help="check a tool folder against its own SHA256SUMS")
    mc.add_argument("folder", type=Path)
    rp = sub.add_parser("rebuild-plan",
                        help="what a rebuild of the exe at a ref needs, as key=value lines")
    rp.add_argument("ref", help="tag or commit")
    rc = sub.add_parser("rebuild-check",
                        help="compare a fresh build folder with the tracked bundle at a ref")
    rc.add_argument("ref", help="tag or commit")
    rc.add_argument("folder", type=Path, help="the fresh build's tool folder")
    bs = sub.add_parser("bundle-scan",
                        help="check an exe against the release marker list")
    bs.add_argument("target", help="tag or commit, or the path of an exe or tool folder")
    bs.add_argument("--markers", type=Path, help=f"default: {MARKERS} in this repo")
    args = p.parse_args(argv)
    try:
        if args.cmd == "stamp-check":
            return _report(f"release gate: stamp-check {args.ref}",
                           stamp_check(args.ref, asset=args.asset))
        if args.cmd == "rebuild-plan":
            for line in plan_lines(rebuild_plan(args.ref, repo=REPO)):
                print(line)
            return 0
        if args.cmd == "rebuild-check":
            return _report(f"release gate: rebuild-check {args.ref} {args.folder}",
                           rebuild_check(args.ref, args.folder, repo=REPO))
        if args.cmd == "bundle-scan":
            return _report(f"release gate: bundle-scan {args.target}",
                           bundle_scan(args.target, args.markers, repo=REPO))
        if args.cmd == "manifest-check":
            return _report(f"release gate: manifest-check {args.folder}",
                           manifest_check(args.folder))
        return _report(f"release gate: deployed-diff {args.folder}",
                       deployed_diff(args.folder, args.dist))
    except GateError as e:
        print(f"release gate could not run: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
