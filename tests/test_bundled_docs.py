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

"""The zip carries its own instructions, beside the exe. #bundled-docs

The download shipped no docs at all: LICENSE and the third-party notices ride
along as PyInstaller `datas`, which land in `_internal/` -- a folder of 1100
machine files that no one opens -- and USING.md and REPORTING.md were not in the
bundle in any form. So a user who downloaded the zip had the program and no
instructions, and the README they were told to read lives in a repository they
may never visit.

Two halves, because either alone can pass while the promise is broken:
  * the SPEC copies them into the bundle root, BEFORE the manifest is written, so
    SHA256SUMS lists them (a file the manifest does not know about is exactly what
    `manifest-check` reports on an overlay upgrade);
  * the BUILT bundle actually has them, and its own manifest names them.
"""
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
SPEC = PROJ / "CBBEtoUBE.spec"
BUNDLE = PROJ / "dist" / "CBBEtoUBE"
DOCS = ("USING.md", "REPORTING.md")


def test_the_docs_exist_to_ship():
    """Control: the two files this promises are in the tree at all."""
    for name in DOCS:
        assert (PROJ / name).is_file(), f"{name} is not in the repository"


def test_the_spec_copies_them_in_before_the_manifest():
    text = SPEC.read_text(encoding="utf-8")
    copy_at = text.find("_BUNDLED_DOCS")
    manifest_at = text.find("write_manifest(")
    assert copy_at != -1, "the spec no longer copies any doc into the bundle"
    assert manifest_at != -1, "the spec no longer writes a manifest"
    assert copy_at < manifest_at, (
        "the docs are copied AFTER SHA256SUMS is written, so the manifest will not "
        "list them and manifest-check will report them as unexpected files")
    listed = re.search(r"_BUNDLED_DOCS\s*=\s*\(([^)]*)\)", text)
    assert listed, "the spec's doc list is no longer readable"
    for name in DOCS:
        assert name in listed.group(1), f"the spec no longer ships {name}"


def test_the_built_bundle_carries_them_and_names_them_in_its_manifest():
    manifest = BUNDLE / "SHA256SUMS"
    assert manifest.is_file(), (
        "the tracked bundle has no SHA256SUMS -- rebuild it (scripts/build_exe.ps1)")
    names = {line.split(None, 1)[1].strip().replace("\\", "/")
             for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()}
    assert len(names) > 1000, f"the manifest lists only {len(names)} file(s)"
    for name in DOCS:
        assert (BUNDLE / name).is_file(), f"the built bundle does not carry {name}"
        assert name in names, f"{name} is in the bundle but not in its SHA256SUMS"
