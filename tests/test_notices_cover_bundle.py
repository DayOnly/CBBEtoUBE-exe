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

"""The notice names everything the bundle carries, and those texts are in it. #bundled-licences

The notice said "full license texts for these are distributed with each library".
MEASURED against the artefact: CPython's, SciPy's and Tcl's were nowhere in the
bundle -- SciPy ships no dist-info at all, so nothing recorded even its version --
while the claim read as settled. This checks both directions against the built
bundle rather than against the sentence:

  * every non-standard-library package inside the executable is named in the
    notice (the bundle is the authority on what ships);
  * the licence files the notice points at exist and are not empty.
"""
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

BUNDLE = PROJ / "dist" / "CBBEtoUBE"
NOTICE = PROJ / "THIRD-PARTY-NOTICES.md"
LICENSES = BUNDLE / "_internal" / "licenses"
# The import name is not always the name a human wrote in the notice.
_NAMED_AS = {"pyn": "PyNifly", "lz4": "python-lz4"}
_OURS = {"src"}
_TEXTS = {
    "CPython-LICENSE.txt": "PYTHON SOFTWARE FOUNDATION LICENSE",
    "SciPy-LICENSE.txt": "SciPy",
    # Tcl/Tk's text names its copyright holders rather than opening with the
    # usual BSD warranty sentence, so match on one of them.
    "Tcl-Tk-license.terms": "Scriptics Corporation",
}


def bundled_top_level():
    """Top-level non-stdlib packages inside the executable, read from its own
    module archive -- not from a list someone maintains by hand."""
    import pytest

    from scripts import release_gate as rg
    try:
        _off, index = rg._pyz_index((BUNDLE / "CBBEtoUBE.exe").read_bytes())
    except rg.ArchiveError as e:
        # The archive holds bytecode for the interpreter the exe was frozen
        # with; the 3.11 and 3.12 CI lanes cannot read it, as the release-gate
        # tests already say for themselves.
        pytest.skip(f"cannot read the tracked exe with this interpreter: {e}")
    tops = {str(name).split(".")[0] for name in index}
    return sorted(t for t in tops
                  if t not in sys.stdlib_module_names and not t.startswith("_")
                  and t not in _OURS)


def listed_components():
    """The names the notice LISTS as components: the bold name in each bullet, and
    the name each section heading opens with (PyNifly has a section of its own).
    A passing mention in prose is not an entry -- otherwise "SciPy ships no
    dist-info" would satisfy a search for SciPy after its entry had been deleted,
    and so would OpenBLAS's line, which names NumPy and SciPy in its text."""
    text = NOTICE.read_text(encoding="utf-8")
    out = {m.group(1).strip().lower()
           for m in re.finditer(r"(?m)^\s*-\s+\*\*(.+?)\*\*", text)}
    for m in re.finditer(r"(?m)^#{2,}\s+(.+)$", text):
        out.add(m.group(1).split("—")[0].strip().lower())
    return out


def test_the_notice_names_every_package_in_the_bundle():
    packages = bundled_top_level()
    assert len(packages) >= 3, f"only {packages} read out of the bundle -- that is not a bundle"
    listed = listed_components()
    assert len(listed) >= 5, f"only {sorted(listed)} parsed out of the notice's list"
    missing = [p for p in packages
               if not any(e == _NAMED_AS.get(p, p).lower()
                          or e.startswith(_NAMED_AS.get(p, p).lower()) for e in listed)]
    assert not missing, (
        f"the executable carries {missing}, which THIRD-PARTY-NOTICES.md does not list")


def test_the_licence_texts_the_notice_points_at_are_in_the_bundle():
    assert LICENSES.is_dir(), (
        "the bundle has no _internal/licenses/ -- rebuild it (scripts/build_exe.ps1)")
    for name, marker in _TEXTS.items():
        p = LICENSES / name
        assert p.is_file(), f"the notice points at licenses/{name}, which is not in the bundle"
        body = p.read_text(encoding="utf-8", errors="replace")
        assert len(body) > 400, f"licenses/{name} is {len(body)} characters -- that is not a licence"
        assert marker.lower() in body.lower(), f"licenses/{name} does not read like the text it claims"
    for pattern, what in (("numpy-*.dist-info/LICENSE.txt", "NumPy"),
                          ("lz4-*.dist-info/licenses/LICENSE", "python-lz4")):
        assert list((BUNDLE / "_internal").glob(pattern)), (
            f"{what}'s own licence is no longer in the bundle, but the notice says it is")


def test_the_notice_still_says_where_they_are():
    """A pointer nobody maintains is how the old claim went stale."""
    text = NOTICE.read_text(encoding="utf-8")
    for name in _TEXTS:
        assert name in text, f"the notice no longer tells a reader where {name} is"
    assert re.search(r"visualstudio\.microsoft\.com/license-terms", text), (
        "the Microsoft runtime's terms are referenced by URL, since they ship with the "
        "redistributable and not with this bundle")
