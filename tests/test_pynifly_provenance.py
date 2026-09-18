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

"""What is vendored in .pynifly/ must be exactly what .pynifly/UPSTREAM.md says.
#pynifly-provenance

The DLL parses every NIF the converter touches, and the vendored `pyn` package is
neither the release its DLL reports nor any documented later state: 10 of its 18
files carry local edits. Until this record, nothing said which. Every check below
works from the tracked files alone -- each local change is stored as a patch, and
reversing it must reproduce the upstream file's git blob id exactly.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / ".pynifly"


def blob_id(data: bytes) -> str:
    """git's blob id (what `git hash-object --no-filters` prints)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def split(data: bytes) -> "list[bytes]":
    """Lines with their own endings (CRLF kept), splitting on LF only."""
    return [ln for ln in re.split(rb"(?<=\n)", data) if ln]


_HUNK = re.compile(rb"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_EOL = b"\\ No newline at end of file"


def parse_patch(diff: bytes):
    """[(old_start, old_count, new_start, new_count, [(tag, line)])] of a unified
    diff, honouring `\\ No newline at end of file` on the line before it."""
    hunks, cur = [], None
    for line in split(diff):
        m = _HUNK.match(line)
        if m:
            cur = (int(m.group(1)), int(m.group(2) or 1),
                   int(m.group(3)), int(m.group(4) or 1), [])
            hunks.append(cur)
        elif cur is not None and line.startswith(_NO_EOL):
            tag, text = cur[4][-1]
            cur[4][-1] = (tag, text[:-1] if text.endswith(b"\n") else text)
        elif cur is not None and line[:1] in (b" ", b"-", b"+"):
            cur[4].append((line[:1], line[1:]))
    return hunks


def unapply(new: "list[bytes]", hunks) -> "list[bytes]":
    """Reverse a unified diff: the patched lines back to the original. Raises
    ValueError if any hunk does not match exactly where it says it applies."""
    out, pos = [], 0
    for _os, _oc, ns, nc, body in hunks:
        idx = ns - 1 if nc else ns
        new_side = [ln for tag, ln in body if tag in (b" ", b"+")]
        old_side = [ln for tag, ln in body if tag in (b" ", b"-")]
        if idx < pos or new[idx:idx + len(new_side)] != new_side:
            raise ValueError(f"hunk at +{ns},{nc} does not match")
        out += new[pos:idx] + old_side
        pos = idx + len(new_side)
    return out + new[pos:]


def _record():
    text = (VENDOR / "UPSTREAM.md").read_text(encoding="utf-8")
    block = re.search(r"## Record\s+```text\n(.*?)```", text, re.S)
    assert block, "UPSTREAM.md has no ## Record block"
    keys, files = {}, {}
    for line in block.group(1).splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "file":
            files[parts[1]] = dict(zip(parts[2::2], parts[3::2]))
        else:
            keys[parts[0]] = " ".join(parts[1:])
    return keys, files


def test_the_record_covers_every_vendored_file():
    _keys, files = _record()
    on_disk = sorted(p.name for p in (VENDOR / "pyn").iterdir() if p.is_file())
    assert len(on_disk) >= 18, f"only {len(on_disk)} vendored files -- the scan drifted"
    assert sorted(files) == on_disk, "a vendored file is not in the record, or vice versa"
    referenced = sorted(r["patch"] for r in files.values() if "patch" in r)
    stored = sorted(f"patches/{p.name}" for p in (VENDOR / "patches").glob("*.diff"))
    assert referenced == stored, "a stored patch is not referenced, or vice versa"


def test_every_vendored_file_is_the_recorded_bytes():
    _keys, files = _record()
    wrong = [n for n, r in files.items()
             if blob_id((VENDOR / "pyn" / n).read_bytes()) != r["local"]]
    assert not wrong, (f"vendored files changed without updating the record: {wrong}. "
                       "Regenerate the patches and .pynifly/UPSTREAM.md together.")


def test_each_local_change_reverses_to_the_upstream_file():
    _keys, files = _record()
    patched = 0
    for name, row in sorted(files.items()):
        local = (VENDOR / "pyn" / name).read_bytes()
        if "patch" not in row:
            assert row["local"] == row["upstream"], f"{name} differs from upstream with no patch"
            continue
        patched += 1
        back = b"".join(unapply(split(local), parse_patch((VENDOR / row["patch"]).read_bytes())))
        assert blob_id(back) == row["upstream"], f"{row['patch']} does not reverse to upstream"
    assert patched >= 1, "no patch was checked -- 0/0 is not a pass"


def test_the_dll_is_the_recorded_build():
    keys, _files = _record()
    assert hashlib.sha256((VENDOR / "NiflyDLL.dll").read_bytes()).hexdigest() == keys["dll_sha256"]


@pytest.mark.skipif(sys.platform != "win32", reason="NiflyDLL.dll is a Windows DLL")
def test_the_dll_reports_the_recorded_version():
    keys, _files = _record()
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from pyn import niflydll as d\n"
            "p = d.nifly.getVersion()\n"
            "print('%%d.%%d.%%d' %% (p[0], p[1], p[2]))\n") % str(VENDOR)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.strip().splitlines()[-1] == keys["dll_version"]


def test_the_patch_reader_refuses_a_hunk_that_does_not_match():
    """Control: a reverse-apply that ignored its context would accept any patch."""
    name, row = next((n, r) for n, r in sorted(_record()[1].items()) if "patch" in r)
    lines = split((VENDOR / row["patch"]).read_bytes())
    first_context = next(i for i, ln in enumerate(lines)
                         if ln.startswith(b" ") and i > 2)
    lines[first_context] = b" this line is in no version of the file\n"
    with pytest.raises(ValueError):
        unapply(split((VENDOR / "pyn" / name).read_bytes()), parse_patch(b"".join(lines)))
