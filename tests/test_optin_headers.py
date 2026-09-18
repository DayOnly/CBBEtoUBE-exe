"""A flag's comment header must not call it OPT-IN once its default is True.

Six headers said "OPT-IN, CBBE2UBE_X=1" or "default OFF" above a flag bound
to True (2026-09-01 audit). A maintainer reading the header decides how to
test, what a defaults-only convert contains, and whether a knob is reachable
— a stale header sends every one of those decisions the wrong way, and the
generated PASS_MAP copies the banner text verbatim.

Rule: within the contiguous comment block directly above a binding whose
default is ON, the words OPT-IN / opt-in / default OFF / DEFAULT OFF may not
appear unless the same line also says "was" / "since" / "promoted" (a history
note is fine; a present-tense claim is not).

WIDENED 2026-09-15. #comment-rot. The rule matched ONE spelling --
`NAME = _flag("CBBE2UBE_...", True)` on one line -- which is 10 of the 145
bindings in src/. The 84 kill switches (`NAME = not _flag("CBBE2UBE_NO_...",
False)`, default ON) and the 66 bindings split across lines were never read,
and four of those carried exactly the stale header this file exists to stop.
Bindings are now PARSED, in every src module that binds a flag, and a floor
keeps the parse from quietly going back to a subset.

Three rules keep it from failing on correct prose, each taken from a real
header in this tree:
  * a block above SEVERAL consecutive bindings is shared, so an opt-in line is
    stale only if every binding in the group defaults ON (a banner can belong to
    the default-OFF flag bound on the line below a default-ON one);
  * a line that also claims ON ("Default ON rather than opt-in", "On by default
    WHILE the pass is opt-in") claims neither;
  * a line naming ANOTHER constant ("the opt-in ANTIPOKE_SMOOTH +
    LAYERED_ANTIPOKE floors") is about that constant, not this binding.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_STALE = re.compile(r"OPT-IN|opt-in|default OFF|DEFAULT OFF")
_HISTORY = re.compile(r"\bwas\b|\bwere\b|\bsince\b|promot|no longer|\bran\b|\bmoved\b", re.I)
_ALSO_ON = re.compile(r"\bdefault[- ]on\b|\bon by default\b", re.I)
_CONSTANT = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
MAX_BLOCK = 60

# 2026-09-15: 145 bindings, 97 default ON, 66 split across lines. A floor
# catches a collapse, never a shortfall, so these sit near the real counts.
MIN_BINDINGS, MIN_DEFAULT_ON, MIN_MULTI_LINE = 120, 80, 50


def bindings(source: str) -> list[tuple[int, int, str, bool]]:
    """(first line, last line, constant, default ON) for every module-level
    `NAME = _flag("ENV", bool)` or `NAME = not _flag("ENV", bool)`, however many
    lines it spans and however it is parenthesised."""
    out = []
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        value, negated = node.value, False
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.Not):
            value, negated = value.operand, True
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "_flag" and len(value.args) >= 2
                and isinstance(value.args[1], ast.Constant)
                and isinstance(value.args[1].value, bool)):
            continue
        out.append((node.lineno, node.end_lineno, node.targets[0].id,
                    value.args[1].value != negated))
    return out


def _own_names(group) -> set[str]:
    own = {name for _first, _last, name, _on in group}
    bare = {n.lstrip("_") for n in own}
    return own | {"CBBE2UBE_" + n for n in bare} | {"CBBE2UBE_NO_" + n for n in bare}


def stale_headers(source: str) -> list[tuple[int, str, str]]:
    lines = source.split("\n")
    groups = []
    for b in bindings(source):
        if groups and b[0] == groups[-1][-1][1] + 1:
            groups[-1].append(b)
        else:
            groups.append([b])
    out = []
    for group in groups:
        if not all(on for _first, _last, _name, on in group):
            continue
        own = _own_names(group)
        i = group[0][0] - 1
        j = i - 1
        while j >= 0 and i - j <= MAX_BLOCK and (lines[j].startswith("#") or not lines[j].strip()):
            line = lines[j]
            if (_STALE.search(line) and not _HISTORY.search(line)
                    and not _ALSO_ON.search(line)
                    and not any(m.group() not in own for m in _CONSTANT.finditer(line))):
                out.append((j + 1, group[0][2], line.strip()[:90]))
            j -= 1
    return out


def _flag_modules() -> list[Path]:
    return sorted(p for p in (REPO / "src").glob("*.py")
                  if "_flag(" in p.read_text(encoding="utf-8"))


def test_no_default_on_flag_has_an_opt_in_header():
    found = [(p.name, ln, flag, txt) for p in _flag_modules()
             for ln, flag, txt in stale_headers(p.read_text(encoding="utf-8"))]
    assert not found, (
        "these comment headers say OPT-IN / default OFF above a flag whose "
        "default is True:\n  "
        + "\n  ".join(f"src/{name}:{ln} ({flag}): {txt}" for name, ln, flag, txt in found)
        + "\nRewrite the header to state the current default (keep the history "
          "as 'was opt-in ... since <date>').")


def test_the_guard_sees_the_flag_surface():
    """The floor. The old single-spelling pattern saw 10 bindings, and nothing
    noticed; a parse that regressed to a subset must fail here, not pass."""
    rows = [b for p in _flag_modules() for b in bindings(p.read_text(encoding="utf-8"))]
    on = sum(1 for _first, _last, _name, is_on in rows if is_on)
    multi = sum(1 for first, last, _name, _on in rows if last > first)
    assert len(rows) >= MIN_BINDINGS and on >= MIN_DEFAULT_ON and multi >= MIN_MULTI_LINE, (
        f"parsed {len(rows)} bindings ({on} default ON, {multi} multi-line); floors "
        f"{MIN_BINDINGS}/{MIN_DEFAULT_ON}/{MIN_MULTI_LINE} -- the guard has stopped "
        "seeing the flag surface")


def test_the_check_can_actually_fail():
    """Every spelling a default-ON flag is bound with must be read."""
    for src in (
            '# #thing -- OPT-IN, `CBBE2UBE_THING=1`.\nTHING = _flag("CBBE2UBE_THING", True)\n',
            '# #thing -- OPT-IN.\nTHING = not _flag("CBBE2UBE_NO_THING", False)\n',
            '# #thing -- default OFF\nTHING = (\n    not _flag("CBBE2UBE_NO_THING", False))\n'):
        assert [(ln, f) for ln, f, _t in stale_headers(src)] == [(1, "THING")], src


def test_correct_prose_is_not_flagged():
    """Each is the shape of a real, CORRECT header in this tree."""
    kill = 'THING = not _flag("CBBE2UBE_NO_THING", False)\n'
    for src in (
            "# #thing -- DEFAULT ON since 2026-09-01 (was opt-in).\n" + kill,
            "# which is what moved this from opt-in to default.\n" + kill,
            "# Default ON rather than opt-in because the gate is narrow.\n" + kill,
            "# Per-shape trace. On by default WHILE the pass is opt-in.\n" + kill,
            "# a bisect over the opt-in ANTIPOKE_SMOOTH + LAYERED_ANTIPOKE floors\n" + kill,
            "# --- #pair -- EXPERIMENT, default OFF ---\n" + kill
            + 'OTHER = _flag("CBBE2UBE_OTHER", False)\n',
            '# #other -- OPT-IN, `CBBE2UBE_OTHER=1`.\nOTHER = _flag("CBBE2UBE_OTHER", False)\n'):
        assert stale_headers(src) == [], src
