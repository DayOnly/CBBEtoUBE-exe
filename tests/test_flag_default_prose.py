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

"""A flag default written into prose is a DATED claim. Pin it to the code.

`_lift_chain_roots_off_body` carried "Default OFF -- see CHAIN_REST_LIFT" for
FOUR WEEKS after that flag was flipped ON, on the very pass that ships the
in-game butt-clip fix. Nothing failed, because nothing was checking: the code
was right and only the sentence describing it was wrong, which is the failure
mode the project's own rule (resolve a default from the CODE, never from a
remembered sentence) exists to catch -- and that rule had no enforcement inside
the repo itself.

So: resolve every boolean pass flag by importing the modules in a SCRUBBED
subprocess (this shell's `CBBE2UBE_*` must not reach the answer -- the same
trap the parity harness guards), then re-read every nearby sentence that
claims a default and require the two to agree.

DELIBERATELY CONSERVATIVE about what counts as a claim, because a false
positive here is a test that fails on a correct comment and gets deleted:

  * the ON/OFF token must be UPPERCASE or hyphenated (`Default OFF`,
    `DEFAULT ON`, `default-off opt-ins`, `off by default`). Lowercase bare
    "defaults on" is prose like "the normals are zeroed at defaults on this
    path", not a claim.
  * exactly ONE known flag may appear in the window, so a sentence about flag A
    sitting next to a condition on flags B and C is not attributed to B or C.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _flag_surface():
    """Imported lazily: `scripts/` is not a package on every path setup."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.analysis import flag_surface
    return flag_surface

# The single-line binding form. NO LONGER how `_bindings()` finds flags -- kept
# only so the control below can show what it misses.
_BIND = re.compile(
    r'^(_?[A-Z][A-Z0-9_]+)\s*=\s*\(?\s*(?:not\s+)?_flag\("([A-Z0-9_]+)",\s*'
    r'(?:True|False)\)', re.M)

# A default CLAIM. `default` is matched case-insensitively but ON/OFF must be
# UPPERCASE or hyphenated, so ordinary lowercase prose ("the normals are zeroed
# at defaults on this path") is not read as a claim.
_CLAIM_ON = re.compile(
    r"(?:\b(?i:default(?:s|ed)?)[-_ ]?ON\b"
    r"|\b(?i:default-on)\b"
    r"|\b(?i:on\s+by\s+default)\b"
    r"|\b(?i:enabled\s+by\s+default)\b"
    r"|\b(?i:ships?)\s+ON\b)")
_CLAIM_OFF = re.compile(
    r"(?:\b(?i:default(?:s|ed)?)[-_ ]?OFF\b"
    r"|\b(?i:default-off)\b"
    r"|\b(?i:off\s+by\s+default)\b"
    r"|\b(?i:disabled\s+by\s+default)\b"
    r"|\b(?i:ships?)\s+OFF\b"
    r"|\b(?i:still)\s+OFF\b)")

# A HYPOTHETICAL or HISTORICAL claim is not a statement about today.
# "it must never default ON without a counter-metric" is a design rule and
# "was opt-in / default OFF when built" is a changelog. Both are correct prose
# and neither may fail this test, so a claim preceded by one of these words on
# its own line does not count.
_NOT_A_CLAIM = re.compile(
    r"(?i:\b(?:never|not|n't|must|should|shall|may|would|could|cannot|"
    r"can't|if|unless|was|were|used\s+to|until|before|historically|"
    r"no\s+longer|instead|rather|why|whether|to)\b)")
_LOOKBACK = 44      # characters before the match, on its own line

WINDOW = 2          # lines either side of the mention


def _claim_side(text: str):
    """"ON" / "OFF" / None -- the default this text CLAIMS, if any.

    A match whose immediately preceding words make it hypothetical or
    historical is discarded rather than counted, and a text claiming BOTH is
    ambiguous (two flags' comments abutting) and claims neither.
    """
    sides = set()
    for side, rx in (("ON", _CLAIM_ON), ("OFF", _CLAIM_OFF)):
        for m in rx.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            back = text[max(line_start, m.start() - _LOOKBACK):m.start()]
            if _NOT_A_CLAIM.search(back):
                continue
            sides.add(side)
    return sides.pop() if len(sides) == 1 else None


def _bindings() -> dict[str, list[str]]:
    """{module dotted name -> [constant, ...]} for every `_flag(...)` binding.

    PARSED, via `flag_surface.declared_all()`, not matched. This used to use the
    single-line `_BIND` regex below while its own sibling's docstring already
    recorded that pattern as broken: "the single-line pattern missed 14 of 152
    flags ... six are written in forms one line cannot hold". Six bindings were
    therefore invisible to a test whose entire job is to catch a constant
    drifting away from the prose beside it -- among them
    COHERENCE_REPAIR_OUTSIDE_BODY, whose prose then went stale unnoticed.

    The population floor could not see the gap either: 142 of 148 resolving is
    comfortably over `>= 100`. A floor catches a collapse, never a shortfall.
    """
    out: dict[str, list[str]] = {}
    for row in _flag_surface().declared_all():
        const = row.get("const")
        if not const:
            continue          # read inline, never bound -- no constant to resolve
        out.setdefault("src." + row["module"], []).append(const)
    return {m: sorted(set(v)) for m, v in sorted(out.items())}


def _resolve(bindings: dict[str, list[str]]) -> dict[str, bool]:
    """Import each module with every `CBBE2UBE_*` stripped and read the values.

    A subprocess, so an override in THIS shell cannot leak into the answer and
    make a wrong sentence look right.
    """
    code = ["import sys, json", "sys.path.insert(0, %r)" % str(REPO_ROOT),
            "out = {}"]
    for mod, names in bindings.items():
        code.append("import %s as _m" % mod)
        code.append("out.update({n: getattr(_m, n, None) for n in %r})" % names)
    code.append("print(json.dumps(out))")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    r = subprocess.run([sys.executable, "-c", "\n".join(code)],
                       capture_output=True, text=True, env=env,
                       cwd=str(REPO_ROOT))
    if r.returncode != 0:
        pytest.skip("could not import the converter in a clean subprocess:\n"
                    + r.stderr[-800:])
    vals = json.loads(r.stdout.strip().splitlines()[-1])
    return {k: v for k, v in vals.items() if isinstance(v, bool)}


def _claims(state: dict[str, bool]):
    """Every (file, line, flag, claimed, actual) prose claim about a default."""
    names = sorted(state, key=len, reverse=True)
    word = {n: re.compile(r"\b" + re.escape(n) + r"\b") for n in names}
    files = (sorted((REPO_ROOT / "src").glob("*.py"))
             + sorted((REPO_ROOT / "scripts").rglob("*.py")))
    for f in files:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            here = [n for n in names if word[n].search(line)]
            if not here:
                continue
            lo, hi = max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)
            win = "\n".join(lines[lo:hi])
            # exactly one known flag in the window, or the claim is ambiguous
            mentioned = {n for n in names if word[n].search(win)}
            if len(mentioned) != 1:
                continue
            flag = mentioned.pop()
            side = _claim_side(win)
            if side is None:
                continue
            yield (f.relative_to(REPO_ROOT).as_posix(), i + 1, flag,
                   side == "ON", state[flag])


@pytest.fixture(scope="module")
def state():
    b = _bindings()
    assert b, "0 flag bindings found -- the binding regex no longer matches"
    s = _resolve(b)
    assert len(s) >= 100, (
        "resolved only %d boolean flags; 0/0 is not a pass" % len(s))
    return s


def test_every_flag_binding_in_src_is_resolvable(state):
    """The population floor. A regex that stops matching would silently make
    this whole file measure nothing."""
    assert len(state) >= 100


def test_the_checker_still_finds_claims_to_check(state):
    """If the claim regexes stop matching, every other test here passes
    vacuously. Require a real population of AGREEING claims."""
    found = list(_claims(state))
    assert len(found) >= 5, (
        "found only %d default claims in the tree -- the claim regexes have "
        "probably stopped matching; 0/0 is not a pass" % len(found))


def test_no_comment_claims_a_default_the_code_contradicts(state):
    wrong = [(f, ln, flag, claimed, actual)
             for f, ln, flag, claimed, actual in _claims(state)
             if claimed != actual]
    if wrong:
        msg = "\n".join(
            "  %s:%d  %s -- prose says %s, the binding resolves %s"
            % (f, ln, flag, "ON" if c else "OFF", "ON" if a else "OFF")
            for f, ln, flag, c, a in wrong)
        raise AssertionError(
            "a comment states a flag default that the code contradicts. "
            "Fix the SENTENCE (the binding is the authority):\n" + msg)


def test_claim_reader_rejects_prose_that_is_not_a_claim():
    """Pins the conservatism. EVERY string here is real text from this tree
    that produced a false positive while the checker was being written; a
    looser reader fails on a correct comment and then gets deleted."""
    for s in ("the normals are zeroed at defaults on this path",
              "defaults on the copy path differ",
              "turn it off by hand",
              "the repair is on for phase 2",
              # a design rule about a flag, not a claim about it
              "but it must never default ON without a counter-metric",
              "do not let it default ON",
              # purely historical -- says nothing about today
              "was default OFF until 2026-08-11",
              # two flags' comments abutting: claims both, so claims neither
              "#a -- DEFAULT ON\n#b -- Default OFF"):
        assert _claim_side(s) is None, s


def test_claim_reader_still_recognises_a_real_claim():
    """The other half: a reader that recognised nothing would make every
    check above pass vacuously."""
    for s, want in (("Default OFF -- see the constant", "OFF"),
                    ("DEFAULT ON since 2026-08-11", "ON"),
                    ("unreachable default-off opt-ins", "OFF"),
                    ("the repair is off by default", "OFF"),
                    ("ON by default, judged in game", "ON"),
                    ("it ships OFF", "OFF"),
                    # a current claim beside its own changelog: the historical
                    # half is discarded, the current half still reads
                    ("DEFAULT ON (was opt-in / default OFF when built)", "ON")):
        assert _claim_side(s) == want, s


def test_it_catches_the_bug_it_was_written_for():
    """`_lift_chain_roots_off_body` said "Default OFF -- see CHAIN_REST_LIFT"
    for four weeks after that flag went ON. Pinned on the literal text so the
    property survives the comment being reworded again."""
    stale = ("    Mutates `chain` in place (ROOT entries only) and returns the\n"
             "    number of chains lifted. Default OFF -- see CHAIN_REST_LIFT.\n")
    assert _claim_side(stale) == "OFF"          # the claim is read
    # ... and it disagrees with the flag as the code actually resolves it
    assert "CHAIN_REST_LIFT" in stale


# ---------------------------------------------------------- the OPT-IN banner

_OPT_IN = re.compile(r"OPT-IN,?\s*`?(CBBE2UBE_[A-Z0-9_]+)=1`?")


def _opt_in_claims(state):
    """`--- #tag -- OPT-IN, `CBBE2UBE_X=1` ---` banners, paired with the truth.

    A DIFFERENT shape of claim from the one above and invisible to it: there is
    no ON/OFF token anywhere in the sentence, so `_claim_side` returns None and
    the window is skipped. But "opt-in via this env var" says default-OFF as
    plainly as "Default OFF" does, and it NAMES the variable, so there is none
    of the attribution ambiguity the window heuristic exists to avoid.

    Both stale ones were section BANNERS -- the first line a reader sees when
    they scroll to a flag -- and both were contradicted by a correcting line
    ~40 lines further down, which is exactly the part a reader skips.
    """
    for f in sorted((REPO_ROOT / "src").glob("*.py")):
        if "__pycache__" in f.parts:
            continue
        for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = _OPT_IN.search(line)
            if not m:
                continue
            env = m.group(1)
            const = env[len("CBBE2UBE_"):]
            kill = "CBBE2UBE_NO_" + const
            # The claim is "OFF unless you set `env`". It is false when the
            # flag is really bound as a kill switch (default ON).
            actual_on = state.get(const)
            yield (f.relative_to(REPO_ROOT).as_posix(), i, const, env, kill,
                   actual_on)


def test_the_opt_in_banner_reader_finds_something(state):
    """The genuine opt-ins keep this from passing vacuously."""
    found = list(_opt_in_claims(state))
    assert len(found) >= 3, (
        "found only %d OPT-IN banners -- the regex has stopped matching; "
        "0/0 is not a pass" % len(found))


def test_no_OPT_IN_banner_names_a_flag_that_defaults_ON(state):
    """#phase1-bust-clearance and #phase1-antipoke both carried
    `OPT-IN, CBBE2UBE_X=1` banners while resolving default-ON through
    `CBBE2UBE_NO_X`. The env var in the banner did not exist. The project's
    rule is that the CODE is the authority on a default, so a lying comment
    breaks the rule at its source -- and PASS_MAP.md had copied both."""
    wrong = [(f, ln, const, env)
             for f, ln, const, env, kill, on in _opt_in_claims(state)
             if on is True]
    if wrong:
        msg = "\n".join(
            "  %s:%d  banner says OPT-IN via %s, but %s resolves ON"
            % (f, ln, env, const) for f, ln, const, env in wrong)
        raise AssertionError(
            "a section banner advertises an opt-in env var for a flag that is "
            "really default-ON. Fix the BANNER:\n" + msg)


def test_an_opt_in_banner_for_a_REAL_opt_in_is_not_flagged(state):
    """Mutation control: the check must not simply reject every banner."""
    rows = list(_opt_in_claims(state))
    assert any(on is False for *_rest, on in rows), (
        "no genuinely default-off opt-in banner survives the check, which "
        "means it is rejecting all of them rather than the stale ones")


def test_the_parse_is_a_strict_superset_of_the_single_line_pattern():
    """Control for the repoint above. `_bindings()` used the `_BIND` regex while
    `flag_surface.declared_all()`'s own docstring already recorded that pattern
    as missing six bindings one line cannot hold -- a split binding, two float
    ternaries, and reads with no constant at all.

    Asserting "the parse finds MORE" rather than a fixed count, so this stays
    true as flags come and go. The named six are spelled out because they are
    the ones that were actually invisible, and one of them
    (COHERENCE_REPAIR_OUTSIDE_BODY) had gone stale unnoticed while it was.
    """
    parsed = {c for v in _bindings().values() for c in v}
    regexed = set()
    for f in sorted((REPO_ROOT / "src").glob("*.py")):
        regexed.update(m[0] for m in _BIND.findall(
            f.read_text(encoding="utf-8", errors="replace")))

    assert regexed - parsed == set(), (
        "the single-line pattern sees bindings the parse does not -- the parse "
        "was supposed to be a superset:\n  " + "\n  ".join(sorted(regexed - parsed)))
    assert len(parsed) > len(regexed), (
        "the parse no longer finds more than the regex; either every "
        "multi-line binding was rewritten, or _bindings() has been repointed "
        "back at the pattern")
    for name in ("COHERENCE_REPAIR_OUTSIDE_BODY", "WEIGHT_PARTNER_JIGGLE_SYNC",
                 "BODY_LOOKUP_PREFERS_BASESHAPE", "SPLIT_COL_DECLARED_BONES"):
        assert name in parsed, f"{name} is bound in src/ and must be scored"
