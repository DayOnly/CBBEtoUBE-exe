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

"""Every workflow declares read-only permissions and pins its actions. #workflow-hardening

TWO THINGS THIS STOPS, neither visible in a passing CI run. A workflow with no
`permissions:` block inherits the repository default, so a changed default -- or a
new workflow copied from this one -- silently gets a WRITE token on a public
repository. And `uses: actions/checkout@v5` resolves a MOVING tag: whoever can
move that tag runs code in this repository's CI, with that token.

Parsed as TEXT, not YAML: PyYAML is not installed here or in CI (the issue-form
tests take the same route), and the two properties are lexical anyway.
"""
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJ / ".github" / "workflows"
_USES = re.compile(r"^\s*-?\s*uses:\s*(?P<action>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@(?P<ref>\S+)"
                   r"(?:\s*#\s*(?P<comment>.*))?$", re.MULTILINE)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def unpinned(text):
    """[(action, ref)] for every `uses:` that is not a 40-character commit sha
    carrying a version comment."""
    out = []
    for m in _USES.finditer(text):
        ref, comment = m.group("ref"), (m.group("comment") or "").strip()
        if not _SHA.match(ref) or not comment.startswith("v"):
            out.append((m.group("action"), ref))
    return out


def write_scopes(text):
    """Scopes granted write in a top-level permissions block."""
    block = re.search(r"(?m)^permissions:\s*$((?:\n[ \t]+.*|\n\s*)*)", text)
    if block is None:
        return ["(no top-level permissions block)"]
    return [ln.strip() for ln in block.group(1).splitlines() if ": write" in ln]


def _workflows():
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert len(files) >= 2, f"only {len(files)} workflow(s) found -- the glob drifted"
    return files


def test_every_workflow_grants_only_read():
    bad = {p.name: write_scopes(p.read_text(encoding="utf-8")) for p in _workflows()}
    bad = {n: v for n, v in bad.items() if v}
    assert not bad, f"a workflow inherits or grants write permissions: {bad}"


def test_every_action_is_pinned_to_a_commit():
    seen, bad = 0, {}
    for p in _workflows():
        text = p.read_text(encoding="utf-8")
        seen += len(_USES.findall(text))
        loose = unpinned(text)
        if loose:
            bad[p.name] = loose
    assert seen >= 4, f"only {seen} `uses:` line(s) seen -- the pattern drifted"
    assert not bad, ("an action resolves a moving tag; pin it to the commit sha with a "
                     f"version comment: {bad}")


def test_the_checks_see_what_they_are_for():
    """Controls: the shapes these tests exist to catch are caught."""
    assert unpinned("      - uses: actions/checkout@v5\n") == [("actions/checkout", "v5")]
    assert unpinned("      - uses: actions/checkout@" + "a" * 40 + "\n"), "a pin with no version comment"
    assert not unpinned("      - uses: actions/checkout@" + "a" * 40 + "  # v5\n")
    assert write_scopes("on: push\njobs: {}\n") == ["(no top-level permissions block)"]
    assert write_scopes("permissions:\n  contents: write\n") == ["contents: write"]
    assert write_scopes("permissions:\n  contents: read\n") == []
