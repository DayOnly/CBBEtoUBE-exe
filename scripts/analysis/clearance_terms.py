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

"""WHICH TERM SETS THE BUST CLEARANCE  #clearance-term-audit

    python -m scripts.analysis.clearance_terms <arm console log> ...

Reads the `[clear-terms]` lines an arm prints with
`CBBE2UBE_CLEARANCE_TERM_AUDIT=1` and answers the question five inert-knob
measurements were each an indirect attempt at: on a bust vertex, which term is
the ARGMAX of `req` in `clear_armor_outside_body`?

WHY THIS IS THE QUESTION. `req` is built from three FLOOR terms and two ADDITIVE
ones, and only a floor term can be the argmax. A knob on a term that never wins
cannot move the mesh no matter what it is set to -- which is exactly what
`ANTIPOKE_FLAT_CLEAR`, `ANTIPOKE_BUST_CLEAR`, `CLEARANCE_BASE`,
`JIGGLE_CLEARANCE_MAX` and `INFLATION_MAGNITUDE` each turned out to be, one arm
at a time. `at_cap` answers the follow-up: a winner pinned at `adaptive_cap` can
only be lowered by the CAP, never by that term's base or factor.

The log is the transport because the JSONL sink cannot be trusted for this --
pool workers append without a line-atomic write, so lines TEAR mid-file and the
tear count differs between two arms of one code state (see `audit_sink.load`).

Exit 0 = read and reported, 2 = bad arguments, 3 = no `[clear-terms]` line in
any input (0/0 is not a pass -- the arm was probably not armed; grep it for
`active flags` and for `CLEARANCE_TERM_AUDIT`).
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from src.nif_convert_fitgeom import CLEARANCE_TERMS          # noqa: E402
from scripts.analysis._census_common import require_population  # noqa: E402

# Built from the printer's own term list, so renaming a term cannot leave this
# reader quietly matching nothing.
_ROW = re.compile(
    r"\[clear-terms\]\s+bust=(\d+)\s+at_cap=(\d+)\s+(.*?)\s+"
    r"req_p50=([+-]?\d+(?:\.\d+)?)\s+relax_p50=([+-]?\d+(?:\.\d+)?)\s+"
    r"worst_p50=([+-]?\d+(?:\.\d+)?)\s+missing=(\S+)\s+relax=(\S+)")
_EMPTY = re.compile(r"\[clear-terms\]\s+bust=0\b")
_TERM = re.compile(r"([a-z-]+)=(\d+)")
# WHY the authored relaxation took what it took. `off` means the authored block
# never ran for that shape, which is a different answer from "it ran and bound
# nothing" and must not be folded into it.
_SPLIT = re.compile(r"([a-z][a-z-]*):(\d+)")
# The budget a BUST-FLOOR knob has. Printed on its own line so an older arm's
# log (which has no such line) reads as "not measured", never as zero budget.
_EXHEAD = re.compile(
    r"\[clear-exempt\]\s+real=(\d+)\s+moot=(\d+)\s+"
    r"head_p50=([+-]?\d+(?:\.\d+)?)\s+head_sum=([+-]?\d+(?:\.\d+)?)")
_HEAD = re.compile(
    r"\[clear-head\]\s+wins=(\d+)\s+at_ceiling=(\d+)\s+"
    r"head_p50=([+-]?\d+(?:\.\d+)?)\s+head_sum=([+-]?\d+(?:\.\d+)?)")


def parse(text: str):
    """(rows, shapes_with_no_bust_vertex, headroom_rows).

    One `rows` entry per shape. `headroom_rows` comes from a SEPARATE
    printed line, so an arm that predates it yields `[]` -- which the
    report must render as NOT MEASURED, never as a zero budget.
    """
    rows = []
    for m in _ROW.finditer(text):
        counts = {t: int(n) for t, n in _TERM.findall(m.group(3))
                  if t in CLEARANCE_TERMS}
        rows.append({
            "bust": int(m.group(1)),
            "at_cap": int(m.group(2)),
            "counts": counts,
            "req_p50": float(m.group(4)),
            "relax_p50": float(m.group(5)),
            "worst_p50": float(m.group(6)),
            "missing": ([] if m.group(7) == "none"
                        else m.group(7).split(",")),
            "relax_off": m.group(8) == "off",
            "split": {k.replace("-", "_"): int(v)
                      for k, v in _SPLIT.findall(m.group(8))},
        })
    heads = [{"wins": int(a), "at_ceiling": int(b),
              "head_p50": float(c), "head_sum": float(d)}
             for a, b, c, d in _HEAD.findall(text)]
    ex = [{"real": int(a), "moot": int(b),
           "head_p50": float(c), "head_sum": float(d)}
          for a, b, c, d in _EXHEAD.findall(text)]
    return rows, len(_EMPTY.findall(text)), heads, ex


def report(rows, empty, heads=(), exheads=(), out=print) -> None:
    verts = sum(r["bust"] for r in rows)
    won: Counter = Counter()
    for r in rows:
        for t, n in r["counts"].items():
            won[t] += n
    missing: Counter = Counter()
    for r in rows:
        for t in r["missing"]:
            missing[t] += 1
    at_cap = sum(r["at_cap"] for r in rows)
    out("=" * 74)
    out("WHICH TERM SETS THE BUST CLEARANCE   #clearance-term-audit")
    out("=" * 74)
    out("  shapes reporting a bust band              : %d" % len(rows))
    out("  shapes with NO vertex in the band         : %d   (excluded)" % empty)
    out("  bust vertices judged                      : %d" % verts)
    out("")
    out("  ARGMAX over the three FLOOR terms (additive terms cannot win):")
    for t in CLEARANCE_TERMS:
        n = won.get(t, 0)
        out("    %-12s %8d  %5.1f%%%s"
            % (t, n, 100.0 * n / max(verts, 1),
               "" if n else "   <== NEVER WINS: a knob on it cannot move the mesh"))
    out("")
    out("  verts whose winner sits ON adaptive_cap   : %d   (%.1f%%)"
        % (at_cap, 100.0 * at_cap / max(verts, 1)))
    out("      ^ only the CAP can lower these; base/factor knobs cannot.")
    out("")
    out("  terms not computed at all, per shape:")
    for t in CLEARANCE_TERMS:
        out("    %-12s %d of %d shapes" % (t, missing.get(t, 0), len(rows)))
    if rows:
        med = sorted(r["req_p50"] for r in rows)[len(rows) // 2]
        rel = sorted(r["relax_p50"] for r in rows)[len(rows) // 2]
        wor = sorted(r["worst_p50"] for r in rows)[len(rows) // 2]
        out("")
        out("  median over shapes: req %.4fu   authored relaxation %.4fu"
            "   worst standoff %.4fu" % (med, rel, wor))
    # WHY the relaxation took what it took. A bare 0.0000u cannot tell the tip
    # exemption (BY DESIGN) from a floor that could not bind (a DEFECT), and
    # the two want opposite work.
    sp: Counter = Counter()
    for r in rows:
        for k, n in r.get("split", {}).items():
            sp[k] += n
    off = sum(1 for r in rows if r.get("relax_off"))
    tot = sum(sp.values())
    out("")
    out("  `#authored-antipoke` ON THE BUST BAND, per vertex:")
    # "Allowed but changed nothing" is FOUR answers and only ONE is a defect.
    # Reporting them as one bucket reads as a defect four times its size.
    for k, why in (("exempt", "tip exemption REFUSED it -- BY DESIGN"),
                   ("relaxed", "it actually lowered the requirement"),
                   ("already_out", "garment already met the need -- push is 0 anyway"),
                   ("floor_auth", "the AUTHOR is already >= our need -- nothing to relax"),
                   ("floor_bust", "the BUST RAMP blocks it -- BY DESIGN"),
                   ("floor_amp", "the MORPH-HEADROOM floor blocks it <== THE DEFECT CLASS"),
                   ("floor_none", "held, but no component explains it"),
                   ("no_bind", "held (components not reported by that arm)"),
                   ("other", "allowed, changed nothing, nothing was high enough")):
        n = sp.get(k, 0)
        if n == 0 and k in ("no_bind", "floor_none", "other"):
            continue          # silent only when EMPTY; a non-zero residue shows
        out("    %-12s %8d  %5.1f%%   %s"
            % (k, n, 100.0 * n / max(tot, 1), why))
    out("    shapes where the authored block never ran : %d of %d"
        % (off, len(rows)))
    # THE BUDGET, before any arm is spent. A knob on the bust floor cannot
    # remove more than the distance from that term down to the RUNNER-UP.
    out("")
    if not heads:
        out("  BUST-FLOOR KNOB BUDGET                    : NOT MEASURED")
        out("      ^ this arm predates the `[clear-head]` line; re-run it.")
    else:
        wins = sum(h["wins"] for h in heads)
        ceil = sum(h["at_ceiling"] for h in heads)
        tot = sum(h["head_sum"] for h in heads)
        p50 = sorted(h["head_p50"] for h in heads if h["wins"])
        out("  BUST-FLOOR KNOB BUDGET  (bust_req - runner-up, where it wins)")
        out("    verts the bust floor wins               : %d" % wins)
        out("    of those, pinned at BUST_CLEAR          : %d   (%.1f%%)"
            % (ceil, 100.0 * ceil / max(wins, 1)))
        out("        ^ FLAT_CLEAR and NIPPLE_GAIN are clipped away on these;")
        out("          only the CEILING is live there.")
        out("    median headroom per winning vert        : %.4fu"
            % (p50[len(p50) // 2] if p50 else 0.0))
        out("    TOTAL headroom over the population      : %.2fu-verts" % tot)
        out("      ^ this is the WHOLE budget every bust-floor knob shares.")
    # A SHARE IS NOT A BUDGET. The exemption covers most of the band; what it
    # COSTS is only the part where the relaxation would have moved something.
    out("")
    if not exheads:
        out("  TIP-EXEMPTION BUDGET                      : NOT MEASURED")
        out("      ^ this arm predates the `[clear-exempt]` line; re-run it.")
    else:
        real = sum(h["real"] for h in exheads)
        moot = sum(h["moot"] for h in exheads)
        tot = sum(h["head_sum"] for h in exheads)
        p50 = sorted(h["head_p50"] for h in exheads if h["real"])
        out("  TIP-EXEMPTION BUDGET  (what `#authored-nipple-exempt` refuses)")
        out("    exempt verts it actually protects       : %d" % real)
        out("    exempt verts where it costs NOTHING     : %d   (%.1f%% of exempt)"
            % (moot, 100.0 * moot / max(real + moot, 1)))
        out("        ^ the relaxation would not have moved these anyway.")
        out("    median refused per protected vert       : %.4fu"
            % (p50[len(p50) // 2] if p50 else 0.0))
        out("    TOTAL refused over the population       : %.2fu-verts" % tot)
        out("      ^ the WHOLE budget any change to the exemption can spend.")


def main(argv=None) -> int:
    args = [a for a in (argv if argv is not None else sys.argv[1:])
            if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    text = ""
    for a in args:
        p = Path(a)
        if not p.is_file():
            print("MISSING: %s" % a)
            return 2
        text += p.read_text(encoding="utf-8", errors="replace")
    rows, empty, heads, exheads = parse(text)
    # A run whose every shape reported `bust=0` measured nothing about the
    # bust, which is not the same as measuring it and finding it clean.
    require_population(rows, "shapes reporting a bust band")
    report(rows, empty, heads, exheads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
