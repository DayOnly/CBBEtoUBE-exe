"""A flag's comment header must not call it OPT-IN once its default is True.

Six headers said "OPT-IN, CBBE2UBE_X=1" or "default OFF" above a flag bound
to True (2026-09-01 audit). A maintainer reading the header decides how to
test, what a defaults-only convert contains, and whether a knob is reachable
— a stale header sends every one of those decisions the wrong way, and the
generated PASS_MAP copies the banner text verbatim.

Rule: within the contiguous comment block directly above a
`NAME = _flag("CBBE2UBE_...", True)` binding, the words OPT-IN / opt-in /
default OFF / DEFAULT OFF may not appear unless the same line also says
"was" / "since" / "promoted" (a history note is fine; a present-tense claim
is not).
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from src import nif_convert as nc

_BIND = re.compile(r'^([A-Z_]+)\s*=\s*_flag\("(CBBE2UBE_[A-Z0-9_]+)",\s*True\)')
_STALE = re.compile(r"OPT-IN|opt-in|default OFF|DEFAULT OFF")
_HISTORY = re.compile(r"\bwas\b|\bwere\b|\bsince\b|promot|no longer|\bran\b", re.I)
MAX_BLOCK = 60


def stale_headers(lines: list[str]) -> list[tuple[int, str, str]]:
    out = []
    for i, line in enumerate(lines):
        m = _BIND.match(line)
        if not m:
            continue
        j = i - 1
        while j >= 0 and i - j <= MAX_BLOCK and (lines[j].startswith("#") or not lines[j].strip()):
            if _STALE.search(lines[j]) and not _HISTORY.search(lines[j]):
                out.append((j + 1, m.group(1), lines[j].strip()[:90]))
            j -= 1
    return out


def _source_lines():
    return Path(inspect.getfile(nc)).read_text(encoding="utf8").split("\n")


def test_no_default_on_flag_has_an_opt_in_header():
    found = stale_headers(_source_lines())
    assert not found, (
        "these comment headers say OPT-IN / default OFF above a flag whose "
        "default is True:\n  "
        + "\n  ".join(f"nif_convert.py:{ln} ({flag}): {txt}" for ln, flag, txt in found)
        + "\nRewrite the header to state the current default (keep the history "
          "as 'was opt-in ... since <date>').")


def test_the_check_can_actually_fail():
    lines = ["# #thing -- OPT-IN, `CBBE2UBE_THING=1`.", "THING = _flag(\"CBBE2UBE_THING\", True)"]
    assert [(1, "THING")] == [(ln, f) for ln, f, _ in stale_headers(lines)]
    ok = ["# #thing -- DEFAULT ON since 2026-09-01 (was opt-in).", lines[1]]
    assert stale_headers(ok) == []
