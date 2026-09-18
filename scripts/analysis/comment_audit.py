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

"""Comment audit: find comments that CONTRADICT the code beside them.

    python -m scripts.analysis.comment_audit [--check A,B,C,D] [--full]

Reading 30k lines and forming an opinion is not an audit. These are the classes
that have actually cost this project wrong answers, each detected exhaustively:

  A. POLARITY   a flag comment saying OPT-IN / default OFF (or DEFAULT ON) that
                disagrees with how the flag actually reads its env var. This
                exact contradiction was read as fact twice on 2026-07-27 and
                reported to the user as "the fix is written but not enabled" --
                wrong both times ([[project_weight_write_invariant]]).
  B. GHOST ENV  a CBBE2UBE_* var named in a comment that no code reads.
  C. GHOST SYM  a `_function` named in a comment that does not exist.
  D. GHOST FILE a .py/.md path named in a comment that is not on disk.

Reports only; changes nothing. It lived untracked in a handoff folder until
2026-09-15; `tests/test_comment_audit.py` now RATCHETS checks A-C (their counts
may fall, never rise) so a retired flag or a renamed helper cannot quietly leave
a comment pointing at nothing. Check D is not ratcheted: it resolves a file
reference against whatever exists under the checkout, generated outputs
included, so its count differs between machines. #comment-rot

POPULATION FLOOR. Every check here parses source, and a parser that stops
seeing its population reports "0 findings", which reads exactly like a clean
tree. The audit refuses (exit 2, "measured NOTHING") when it sees fewer than
MIN_FILES files or no comment lines at all, and check A prints how many flag
comment blocks it actually scored.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent

MIN_FILES = 50

TITLES = {
    "A": "POLARITY -- comment contradicts how the flag reads its env var",
    "B": "GHOST ENV VAR -- named in a comment, read by no code",
    "C": "GHOST SYMBOL -- named in a comment, defined nowhere",
    "D": "GHOST FILE -- referenced in a comment, absent from disk",
}

# A comment may name a flag / symbol / file that no code uses ON PURPOSE, and
# that is often the most valuable prose in the file. "THREE TOGGLES DELETED
# HERE, 2026-08-16. DO NOT REBUILD THEM." and "REMOVED 2026-07-27:
# `HARDEN_AUTHORED_PHYSICS` / `_harden_physics_params`" both name dead things
# precisely so nobody re-adds them, and project_disproven_toggles_removed says
# never to suggest setting them. Flagging those as defects trains the reader to
# dismiss the whole audit. Only a comment presenting the thing as CURRENTLY
# USABLE is a finding.
# SHARED BY CHECKS B, C AND D -- all three had the same false-positive class.
_HISTORY = re.compile(
    r"\b(deleted|removed|retired|was\s+opt-in|used\s+to|used\s+to\s+claim|"
    r"formerly|no\s+longer|obsolete|superseded|disproven|do\s+not\s+rebuild|"
    r"corrected|this\s+row\s+wrote|this\s+named|renamed)\b", re.I)

# The binding idiom: `NAME = _flag("CBBE2UBE_X", default)` or `NAME = not
# _flag(...)` (a kill switch). Until 2026-09-16 this read the `os.environ.get`
# spelling, which by then only four bindings still used -- so check A judged
# four blocks and called the other hundred-odd clean by never reading them.
FLAG_RE = re.compile(
    r"^(?P<name>[A-Z_][A-Z0-9_]*)\s*=\s*\(?\s*(?:not\s+)?_flag\(\s*"
    r"[\"'](?P<env>CBBE2UBE_[A-Z0-9_]+)[\"']", re.M)
OFF_WORDS = re.compile(r"\bOPT-IN\b|\bdefault(?:s)? OFF\b|\bDEFAULT OFF\b"
                       r"|\boff by default\b", re.I)
ON_WORDS = re.compile(r"\bDEFAULT ON\b|\bdefault(?:s)? ON\b|\bon by default\b"
                      r"|\bopt-out\b", re.I)
# A sentence that merely MENTIONS the other state is not a claim about this
# flag. These are the phrasings that burned the first version.
NEGATED = re.compile(
    r"never default ON|not default[- ]ON|the fix above is already default ON"
    r"|was DEFAULT (?:ON|OFF)|used to|no longer|history|moved twice|then OFF"
    r"|back ON|restores the|would|if it were", re.I)


def _near_history(ln, hist_lines, span=6):
    """A deletion note governs its whole block, not just its own line."""
    return any(abs(ln - h) <= span for h in hist_lines)


def files_of(repo: Path) -> list[Path]:
    return sorted((repo / "src").glob("*.py")) + sorted((repo / "scripts").rglob("*.py"))


def comments_of(path):
    """[(lineno, text)] for every comment AND docstring line."""
    out = []
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except Exception:
        pass
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    ln = getattr(node, "lineno", 1)
                    for i, line in enumerate(d.splitlines()):
                        out.append((ln + i, line))
    except Exception:
        pass
    return src, out


def _real_flag_values(repo: Path) -> dict:
    """ASK THE MODULE, do not parse the idiom. The first version of check A
    decided the default by looking for `not in (` and called FIVE flags
    contradictory -- every one a false positive, because it could not see
    `os.environ.get(VAR, "1")` (a default-ON flag written with `in`). Importing
    under a cleaned environment is the ground truth and has no idioms to miss.

    It IMPORTS AND RELOADS src modules, so run check A in its own process: a
    reload inside a test run leaves other modules holding stale classes."""
    import importlib
    import os as _os
    _saved = {k: v for k, v in _os.environ.items() if k.startswith("CBBE2UBE_")}
    for k in _saved:
        del _os.environ[k]
    sys.path.insert(0, str(repo))
    real = {}
    try:
        for modname in ("nif_convert", "auto_convert", "fit_metrics",
                        "overlay_transfer", "ube_patcher", "hdt_xml_gen"):
            try:
                m = importlib.import_module(f"src.{modname}")
                importlib.reload(m)
                for attr in dir(m):
                    v = getattr(m, attr)
                    if isinstance(v, bool) and attr.isupper() or (
                            isinstance(v, bool) and attr.startswith("_")):
                        real[(modname, attr)] = v
            except Exception as e:
                print(f"  (could not import src.{modname}: {e!r})",
                      file=sys.stderr)
    finally:
        _os.environ.update(_saved)
    return real


def collect(repo: Path = _REPO, checks: str = "ABCD") -> dict:
    """{"files": n, "comments": n, "polarity_scored": n,
    "findings": {check: [(path, line, what, why)]}}"""
    files = files_of(repo)
    all_src = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}
    blob = "\n".join(all_src.values())
    comments = {p: comments_of(p)[1] for p in files}
    findings = defaultdict(list)

    # A module's own BASENAME is a citable name -- comments say `standoff_audit`,
    # `underbust_census`, `bust_verdict`, `verify_skin_exposure`. Those are
    # FILES, not identifiers, so an AST walk of the code never sees them and
    # they read as ghosts. `tests/` is not scanned (the audit reads src/ +
    # scripts/), so a comment citing a TEST BY NAME read as a ghost too -- and
    # "a cited test that does not exist" is a defect this project has actually
    # had, so the check must be able to tell the two apart.
    module_names = ({p.stem for p in files}
                    | {p.stem for p in (repo / "tests").rglob("test_*.py")})

    # ------------------------------------------------------------ A. polarity
    # `scored` is check A's population: flag comment blocks it actually judged
    # (135 on 2026-09-16, once FLAG_RE read the helper idiom). A check that
    # judged nothing must say so rather than report a clean 0.
    scored = 0
    if "A" in checks:
        real = _real_flag_values(repo)
        for path, src in all_src.items():
            modname = path.stem
            lines = src.splitlines()
            for m in FLAG_RE.finditer(src):
                name = m.group("name")
                if (modname, name) not in real:
                    continue
                default_on = real[(modname, name)]
                ln = src[:m.start()].count("\n") + 1
                i, block = ln - 2, []
                while i >= 0 and i < len(lines) and lines[i].lstrip().startswith("#"):
                    block.append(lines[i])
                    i -= 1
                text = "\n".join(reversed(block))
                if not text:
                    continue
                scored += 1
                claim_lines = [l for l in text.splitlines()
                               if (OFF_WORDS.search(l) or ON_WORDS.search(l))
                               and not NEGATED.search(l)]
                if not claim_lines:
                    continue
                says_off = any(OFF_WORDS.search(l) for l in claim_lines)
                says_on = any(ON_WORDS.search(l) for l in claim_lines)
                if says_off and says_on:
                    continue
                if says_off and default_on:
                    findings["A"].append(
                        (path, ln, name,
                         f"IS default ON, comment says OPT-IN/OFF: "
                         f"{claim_lines[0].strip()[:90]}"))
                if says_on and not default_on:
                    findings["A"].append(
                        (path, ln, name,
                         f"IS default OFF, comment says ON: "
                         f"{claim_lines[0].strip()[:90]}"))

    # --------------------------------------------------------- B. ghost envvar
    if "B" in checks:
        # THIS CHECK WAS VOID FROM THE `_flag()` MIGRATION UNTIL 2026-08-22.
        # It recognised only `os.environ...` and `env="..."`, but the codebase
        # collapsed every flag read to the `_flag()` / `knob()` idiom
        # (project_flag_audit_2026_08_18). Every flag written the CURRENT way
        # therefore read as a ghost: 78 findings, of which
        # CBBE2UBE_PHASE1_CONFORM was the clearest disproof -- it is literally
        # `_flag("CBBE2UBE_PHASE1_CONFORM", False)`.
        # A ghost-hunter blind to the language the project actually writes
        # reports the whole codebase as ghosts, which is WORSE than no check: a
        # 78-item list gets skimmed and dismissed, and the real ghosts hide in it.
        read_envs = set(re.findall(r"os\.environ(?:\.get)?\(?\s*[\"']"
                                   r"(CBBE2UBE_[A-Z0-9_]+)[\"']", blob))
        read_envs |= set(re.findall(r"env=[\"'](CBBE2UBE_[A-Z0-9_]+)[\"']", blob))
        # the current idiom: _flag("X", ...) / flag("X", ...) / knob("X", ...)
        read_envs |= set(re.findall(r"\b_?(?:flag|knob)\(\s*[\"']"
                                    r"(CBBE2UBE_[A-Z0-9_]+)[\"']", blob))
        # any OTHER bare string literal equal to the env name: spec tuples,
        # os.environ[...] subscripts, monkeypatch.setenv, build-spec entries.
        # A name that appears as a literal anywhere in code is not a ghost.
        read_envs |= set(re.findall(r"[\"'](CBBE2UBE_[A-Z0-9_]+)[\"']", blob))
        # PREFIXES built by f-string, e.g. `os.environ.get(f"CBBE2UBE_UBE_BODY{w}")`
        # -- the comment legitimately writes the RESOLVED names
        # (`CBBE2UBE_UBE_BODY_0 / _1`) which appear nowhere as a literal.
        dyn = set(re.findall(r"f[\"'](CBBE2UBE_[A-Z0-9_]*)\{", blob))
        for path in files:
            # a deletion note applies to the whole block, not just its own line
            hist_lines = {ln for ln, t in comments[path] if _HISTORY.search(t)}
            for ln, text in comments[path]:
                near_history = any(abs(ln - h) <= 6 for h in hist_lines)
                for env in set(re.findall(r"\b(CBBE2UBE_[A-Z0-9_]+)\b", text)):
                    if env in read_envs or near_history:
                        continue
                    # an f-string-built PREFIX covers every resolved name under it
                    if any(env.startswith(d) for d in dyn):
                        continue
                    # a FAMILY WILDCARD -- `CBBE2UBE_SELFINT_*` / a trailing `_`
                    # -- is satisfied by any real member of that family
                    if (env.endswith("_")
                            and any(e.startswith(env) for e in read_envs)):
                        continue
                    findings["B"].append((path, ln, env,
                                          "named in a comment as if LIVE; no code "
                                          "reads it (and no deletion note nearby)"))

    # --------------------------------------------------------- C. ghost symbol
    if "C" in checks:
        # `defined` USED TO BE FIVE REGEXES over the source blob, which knew
        # about defs, classes and assignments but NOT about function PARAMETERS
        # or attributes. Comments legitimately name a parameter (`data_dirs`,
        # `extra_body_osds`, `body_inject_names`), so 122 of the findings were
        # correct prose describing the argument right below them.
        #
        # Collect every identifier the AST actually contains instead. A comment
        # name is a ghost only if it appears NOWHERE in code -- not as a def,
        # class, variable, parameter, attribute, keyword or string.
        defined = set(module_names)
        for tp in (repo / "tests").rglob("test_*.py"):
            try:
                for tn in ast.walk(ast.parse(tp.read_text(
                        encoding="utf-8", errors="replace"))):
                    if isinstance(tn, (ast.FunctionDef, ast.ClassDef)):
                        defined.add(tn.name)
            except SyntaxError:
                pass
        for txt in all_src.values():
            try:
                t = ast.parse(txt)
            except SyntaxError:
                defined |= set(re.findall(r"\b(\w+)\b", txt))
                continue
            for n in ast.walk(t):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                    defined.add(n.name)
                elif isinstance(n, ast.Name):
                    defined.add(n.id)
                elif isinstance(n, ast.arg):
                    defined.add(n.arg)
                elif isinstance(n, ast.Attribute):
                    defined.add(n.attr)
                elif isinstance(n, ast.keyword) and n.arg:
                    defined.add(n.arg)
                elif isinstance(n, ast.alias):
                    defined.add((n.asname or n.name).split(".")[-1])
                elif isinstance(n, ast.ImportFrom) and n.module:
                    # `from scripts.analysis.verify_skin_exposure import
                    # ray_blocked` -- the MODULE name is a real thing a comment
                    # may cite, and it is not an identifier anywhere in the AST.
                    for part in n.module.split("."):
                        defined.add(part)
        # WIDENED + DE-NOISED 2026-08-22.
        #
        # It matched ONLY leading-underscore names, so `groove_smooth` -- cited
        # in three comments as though it were a pass, when it is a stage LABEL
        # passed to `_stage(...)` -- was structurally invisible. That ghost fed
        # a WRONG root cause into BUG-02 ("the copy path never runs
        # groove-smooth"). Meanwhile `_0` / `_1` (weight suffixes, not symbols)
        # produced most of the 65+ findings, so the real ones were unreadable in
        # the noise.
        #
        # Now: any backticked snake_case identifier is a candidate, and it is a
        # ghost only if it is neither DEFINED nor present as a STRING LITERAL
        # anywhere in the codebase (a stage label or tag is a real thing, even
        # though it is not a function).
        literals = set(re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]{4,})[\"']", blob))
        noise = re.compile(r"^_[01]$|^_+$")
        for path in files:
            hist_lines = {ln for ln, t in comments[path] if _HISTORY.search(t)}
            for ln, text in comments[path]:
                if _near_history(ln, hist_lines):
                    continue        # a deliberate deletion note
                cands = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))
                cands |= set(re.findall(r"\b(_[a-z][a-z0-9_]{4,})\(", text))
                for sym in cands:
                    base = sym.rstrip("()")
                    if noise.match(base) or "_" not in base or len(base) < 6:
                        continue          # weight suffixes and bare words
                    if base in defined or base in literals:
                        continue
                    findings["C"].append((path, ln, base,
                                          "named in a comment; neither defined nor "
                                          "present as a string literal"))

    # ----------------------------------------------------------- D. ghost file
    if "D" in checks:
        for path in files:
            hist_lines = {ln for ln, t in comments[path] if _HISTORY.search(t)}
            for ln, text in comments[path]:
                if _near_history(ln, hist_lines):
                    continue        # a deliberate deletion note
                for ref in set(re.findall(r"`([\w./\\-]+\.(?:py|md|json|xml))`", text)):
                    cand = [repo / ref, repo / "src" / ref, repo / "scripts" / ref,
                            repo / "docs" / ref, path.parent / ref]
                    if not any(c.exists() for c in cand) and not list(
                            repo.rglob(Path(ref).name)):
                        findings["D"].append((path, ln, ref,
                                              "referenced in a comment; not on disk"))

    return {"files": len(files),
            "comments": sum(len(v) for v in comments.values()),
            "polarity_scored": scored,
            "findings": {k: findings.get(k, []) for k in checks}}


def main(argv: list[str]) -> int:
    checks = "ABCD"
    if "--check" in argv:
        checks = argv[argv.index("--check") + 1].replace(",", "").upper()
    full = "--full" in argv
    res = collect(_REPO, checks)
    if res["files"] < MIN_FILES or res["comments"] == 0:
        print(f"comment audit measured NOTHING: {res['files']} file(s), "
              f"{res['comments']} comment line(s) -- 0/0 is not a pass "
              f"(population floor: {MIN_FILES} files)")
        return 2
    if "A" in checks:
        print(f"check A scored {res['polarity_scored']} flag comment block(s)")
    total = 0
    for k in checks:
        rows = res["findings"][k]
        total += len(rows)
        print(f"\n{'=' * 78}\n{k}. {TITLES[k]}   ({len(rows)} finding(s))\n{'=' * 78}")
        shown = rows if full else rows[:40]
        for path, ln, what, why in sorted(shown, key=lambda r: (str(r[0]), r[1])):
            print(f"  {path.relative_to(_REPO).as_posix()}:{ln}")
            print(f"      {what} -- {why}")
        if len(rows) > len(shown):
            print(f"  ... {len(rows) - len(shown)} more (pass --full)")
    print(f"\nTOTAL {total} finding(s) across {res['files']} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
