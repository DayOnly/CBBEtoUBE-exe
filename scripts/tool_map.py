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

"""GENERATE the tool map: which measurement harness already answers a question.

    python scripts/tool_map.py            # write docs/TOOL_MAP.md
    python scripts/tool_map.py --check    # exit 1 if the file is out of date

WHY THIS EXISTS. The standing rule is "check before hand-rolling a probe", and
the note pointing at where to check said `docs/PASS_MAP.md` indexes every
measurement harness. **It does not** -- PASS_MAP is generated from
`nif_convert.py` alone and contains the string "scripts/analysis" zero times.
On 2026-08-25 that was measured: **38 of the 54 tracked analysis tools were
named in no document at all.** So the index the rule depends on was never
written, and a reader following the rule would find nothing and conclude nothing
existed.

That is the same class this project has paid for twice already: a
cross-reference the reader cannot follow still reads as corroboration
(`project_comment_audit_2026_08_17` found six, and the promoted-defaults record
was cited from four places while existing nowhere). The fix is the same one that
worked for the pass map: GENERATE it, and pin it with a test, so it cannot rot
into the thing it is warning about.

WHAT IT IS ORGANIZED BY. Not by filename -- by **what each tool reads**. The
question a reader actually arrives with is "is there already something that
measures the shipped pack / our source / a run log", and filenames answer that
badly (`fold_census` and `crumple_census` read different things; `verify_*` is
three unrelated jobs).

ONLY TRACKED TOOLS ARE INDEXED, and that is a hygiene rule, not laziness. The
scratchpad harnesses hard-code absolute modlist paths, so rendering their
docstrings into a TRACKED doc would push local paths into a public repo -- the
exact failure `repo_hygiene` exists to prevent. The rendered output is scanned
with `repo_hygiene.scan_text` before it is written, and the generator REFUSES
rather than emitting something the pre-commit hook would then have to catch.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import repo_hygiene                           # noqa: E402

OUT = REPO / "docs" / "TOOL_MAP.md"
ROOTS = ("scripts", "scripts/analysis")

# Modules that are not tools: imported helpers and build/install plumbing. Each
# is listed rather than pattern-matched so that adding one is a deliberate act.
NOT_A_TOOL = {
    "scripts/__init__.py",
    "scripts/analysis/__init__.py",
    "scripts/analysis/_census_common.py",
    "scripts/repo_hygiene.py",
    "scripts/hook_precommit.py",
    "scripts/hook_commitmsg.py",
    "scripts/make_icon.py",
}

# THE CLASSIFIER MATCHES ITS OWN MARKER TABLE. The first run filed this file
# under "reads the run log", because READS below contains the literal
# "last_run.log" and the scan is a substring search over the whole source. A
# doc generator is not a measurement harness anyway, so both are excluded --
# but they are NAMED in the rendered output rather than hidden, because a tool
# silently missing from an index is the failure this index exists to fix.
DOC_GENERATORS = {
    "scripts/pass_map.py",
    "scripts/tool_map.py",
}

# What a tool READS, in the order a reader cares about. First match wins, so the
# most specific marker has to come first: a tool that opens the pack AND parses
# our source is a pack tool with a source-shaped implementation detail.
READS = (
    ("the run log", ("last_run.log", "RUN_LOG", "run_log")),
    # `scan_output_health.py` reads the pack through `sys.argv[1]` with a
    # CBBE2UBE_MODS_ROOT fallback and named no marker in the first three, so it
    # was filed under "reads nothing on disk" -- a tool that scans every mesh in
    # the output. Env var and output-mod name added because a tool can reach the
    # pack without ever calling `discover_layout`.
    ("the shipped pack", ("discover_layout", "mods_root", "OUT_MOD",
                          "CBBE2UBE_OUT_MOD", "CBBE2UBE_MODS_ROOT",
                          "CBBEtoUBE Auto")),
    ("NIFs given to it", ("pynifly", "NifFile")),
    ("our own source", ("nif_convert.py", "SRC.read_text", "ast.parse")),
    ("a cached measurement", ("json.load", "loads(")),
)


# Paragraphs that are house BOILERPLATE, not a description of what a tool
# measures. Six tools open with one, and the first render duly published
# "DEVELOPMENT TOOL -- not part of the shipped converter" as what six different
# tools measure. The disclaimer is deliberate and stays in the source; the
# right place to know it is not a purpose is here, not in six edited files.
# TWO KINDS, and conflating them produced garbage. A PREFIX may carry the real
# purpose after it ("DEVELOPMENT TOOL -- golden-output regression harness"), so
# it is stripped and the remainder kept. A WHOLE-PARAGRAPH disclaimer never
# does, and stripping its opening words left three tools described as
# ", which PyInstaller does not bundle, and has no GUI setting".
SKIP_PARA_RE = re.compile(
    r"^\s*(?:Lives in `scripts/`"
    r"|Not part of the (?:shipped )?(?:converter|exe)\b)",
    re.IGNORECASE)

DISCLAIMER_RE = re.compile(
    r"^\s*DEVELOPMENT TOOL\b\s*(?:--)?\s*",
    re.IGNORECASE)

# What is left of a disclaimer line once the prefix is gone and is still not a
# purpose, e.g. "DEVELOPMENT TOOL -- not part of the shipped converter."
_EMPTY_AFTER_STRIP = re.compile(
    r"^\s*(?:not part of the [^.]*\.?|\.)?\s*$", re.IGNORECASE)


def _paragraphs(doc: str) -> list[str]:
    out, cur = [], []
    for line in doc.strip().splitlines():
        if line.strip():
            cur.append(line.strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _purpose(doc: str) -> str:
    """The first sentence that actually says what the tool measures.

    Skips a leading boilerplate disclaimer, but keeps any real description
    carried ON the disclaimer line -- `golden_output.py` opens with
    "DEVELOPMENT TOOL -- golden-output regression harness", where everything
    after the dashes is the purpose.
    """
    if not doc:
        return "(no docstring)"
    for para in _paragraphs(doc):
        if SKIP_PARA_RE.match(para):
            continue
        m = DISCLAIMER_RE.match(para)
        if m:
            rest = para[m.end():].lstrip(" -.")
            if _EMPTY_AFTER_STRIP.match(rest):
                continue                      # pure boilerplate: next paragraph
            para = rest
        # Cut at the first sentence end that is not an abbreviation-ish dot.
        s = re.search(r"(?<=[a-z0-9)\]`])\.(?:\s|$)", para)
        if s:
            para = para[:s.end() - 1]
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            return para
    return "(no description beyond boilerplate)"


def _usage(doc: str) -> list[str]:
    """Indented `python ...` invocation lines from the docstring."""
    out = []
    for line in (doc or "").splitlines():
        s = line.strip()
        if s.startswith("python ") and s not in out:
            out.append(s)
    return out


def _reads(text: str) -> str:
    for label, markers in READS:
        if any(m in text for m in markers):
            return label
    return "-"


def _int_const(node) -> "int | None":
    """An int literal that is NOT a bool.

    `isinstance(True, int)` is True in Python, so a plain int check collected
    `return True` from a helper predicate as "exit code True" -- which is what
    the first render of this map printed for `fit_audit.py`. A gate column that
    reports a boolean as an exit status is worse than an empty one.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    return None


def _exits(tree) -> list[int]:
    """Non-zero exit codes this tool can produce, so a gate is visible.

    Only `sys.exit`/`SystemExit` anywhere, plus returns from a top-level
    `main()`. An earlier version took a return from ANY function, which is how
    a helper's `return True` became a gate -- most functions in these tools
    return values, and almost none of those values are exit codes.
    """
    codes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in ("exit", "SystemExit") and node.args:
                v = _int_const(node.args[0])
                if v is not None:
                    codes.add(v)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    v = _int_const(sub.value)
                    if v is not None:
                        codes.add(v)
    return sorted(c for c in codes if c)


def _has_floor(text: str) -> bool:
    """Does it refuse to report a suspiciously empty result?

    The property `tool_audit.py` is about, surfaced per tool: a census that
    cannot tell "nothing is wrong" from "I measured nothing" will eventually
    report the second as the first.
    """
    probes = ("measured NOTHING", "measured nothing", "empty set",
              "NOTHING WAS", "no survival records", "is not a pass",
              "population floor", "MIN_COVER")
    return any(p in text for p in probes)


def scan(repo: Path = REPO) -> list[dict]:
    """Every tracked tool under ROOTS, sorted, as plain dicts."""
    out = []
    for root in ROOTS:
        d = repo / root
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            rel = f.relative_to(repo).as_posix()
            if rel in NOT_A_TOOL or rel in DOC_GENERATORS:
                continue
            if root == "scripts" and f.parent.name != "scripts":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            doc = ast.get_docstring(tree) or ""
            out.append({
                "path": rel,
                "purpose": _purpose(doc),
                "reads": _reads(text),
                "usage": _usage(doc),
                "exits": _exits(tree),
                "floor": _has_floor(text),
            })
    return sorted(out, key=lambda e: e["path"])


def render(entries: list[dict]) -> str:
    by_reads: dict[str, list[dict]] = {}
    for e in entries:
        by_reads.setdefault(e["reads"], []).append(e)

    L: list[str] = []
    L.append("# Tool map — which harness already answers this question")
    L.append("")
    L.append("<!-- GENERATED by scripts/tool_map.py. Do not edit by hand: "
             "tests/test_tool_map.py regenerates this and fails if it "
             "differs. -->")
    L.append("")
    L.append(f"**{len(entries)}** tracked measurement tools. "
             "**Check here before hand-rolling a probe.**")
    L.append("")
    L.append("The standing rule says to check an index before writing a new "
             "measurement. That index used to be described as `docs/PASS_MAP.md`, "
             "which indexes the CONVERSION PASSES and no tools at all. This is "
             "the tool index; PASS_MAP remains the pass index.")
    L.append("")
    L.append("Grouped by **what each tool reads**, because that is the axis a "
             "reader arrives on. Filenames are not: `verify_*` covers three "
             "unrelated jobs.")
    L.append("")
    L.append("`gate` lists the non-zero exit codes a tool can return — a tool "
             "with one can be chained into a gate. `floor` marks a tool that "
             "refuses to report a suspiciously empty result, the property "
             "`scripts/tool_audit.py` exists to check; a census WITHOUT one "
             "cannot tell \"nothing is wrong\" from \"I measured nothing\".")
    L.append("")
    L.append("The untracked scratchpad harnesses are deliberately NOT indexed: "
             "they hard-code absolute modlist paths, and rendering them here "
             "would put local paths into a public repo.")
    L.append("")
    L.append("Two more tracked scripts are excluded as doc GENERATORS rather "
             "than measurements, and are named here so that neither goes "
             "missing: "
             + ", ".join(f"`{p}`" for p in sorted(DOC_GENERATORS)) + ".")
    L.append("")

    for label, _ in list(READS) + [("-", ())]:
        group = by_reads.get(label)
        if not group:
            continue
        L.append(f"## Reads {label}" if label != "-"
                 else "## Reads nothing on disk (pure helpers and drivers)")
        L.append("")
        L.append("| tool | gate | floor | what it measures |")
        L.append("|---|---|---|---|")
        for e in group:
            gate = ", ".join(str(c) for c in e["exits"]) or "—"
            floor = "yes" if e["floor"] else "—"
            purpose = e["purpose"].replace("|", "\\|")
            L.append(f"| `{e['path']}` | {gate} | {floor} | {purpose} |")
        L.append("")

    L.append("## Invocations, as each tool documents itself")
    L.append("")
    L.append("Only tools whose docstring carries a `python ...` line. A tool "
             "missing from this list is not necessarily undocumented — it may "
             "simply not state a usage line.")
    L.append("")
    for e in entries:
        if not e["usage"]:
            continue
        L.append(f"- `{e['path']}`")
        for u in e["usage"]:
            L.append(f"  - `{u}`")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if docs/TOOL_MAP.md is out of date")
    a = ap.parse_args()

    entries = scan()
    text = render(entries)

    # REFUSE rather than emit something the pre-commit hook would catch. One
    # definition of the rule, two callers -- the principle repo_hygiene itself
    # is built on.
    bad = repo_hygiene.scan_text(OUT.relative_to(REPO).as_posix(), text)
    if bad:
        print("REFUSING to write: the rendered map would violate the "
              "public-repo rules.")
        for b in bad:
            print("   " + b)
        return 2

    if a.check:
        have = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if have != text:
            print("docs/TOOL_MAP.md is out of date -- run "
                  "`python scripts/tool_map.py`")
            return 1
        print(f"docs/TOOL_MAP.md is current ({len(entries)} tools)")
        return 0

    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO).as_posix()}  ({len(entries)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
