"""The run log a person reads, and the detail log the analysis tools grep.
#readable-run-log

A full run printed 23,616 lines (1.7 MB) and 88% of them were per-shape pass
lines from the NIF workers -- coincident skin 3,056, coherence-repair 2,520,
phase1-antipoke 1,868, ... -- with the lines that matter (failures, warnings,
coverage, the tally) buried under them; the tally sat on line 23,610.

So a run writes TWO files (the run-log tee in cbbe_to_ube_main installs both):

  CBBEtoUBE_last_run.log         what a person reads: the run summary at the
                                 TOP and the BOTTOM, per mod a short block with
                                 one line per piece, and every problem line.
  CBBEtoUBE_last_run_detail.log  EVERYTHING, in order: each line the old run
                                 log carried, word for word, plus the lines
                                 above. Grep this one for pass lines.

What goes where is decided at ONE place, `echo_unit`: a NIF unit's printed
output (all of it per-piece pass detail) goes to the detail log only, except
the lines `KEEP_RX` matches, which go to both; the unit becomes one line in its
mod's block. Everything else is printed as before and reaches both files.

Stable for the tools, in the main log: "[progress-nif] N M", "PASS FAILED:
<name> on <file> -- ...", "=== N failure(s), M warning(s) ===",
"weight-partner completion: ...", "[zeroed-output-source] ...". The
"body-zap: <file>: ..." lines are in the detail log only (the piece line says
what the zap removed).

CBBE2UBE_RUN_LOG_VERBOSE=1 (Diagnostics > "Write every pass line into the run
log") writes the main log as before: no split, no piece lines, no summary.
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter

from .envflags import flag as _flag

#: The last line the run writes to the main log before the summary is put at
#: its top. The window stops reading the file there: the rewrite shifts every
#: byte, and a reader past this point would read the shifted tail twice.
END_MARKER = "[run-log-end]"
DETAIL_SUFFIX = "_detail"

# A line from a worker that a person must see as it happens, in both logs.
KEEP_RX = re.compile(
    r"PASS FAILED|^\s*!!|\bWARN(?:ING)?\b|\bERROR\b|Traceback|MemoryError"
    r"|out of memory|^\s*(?:File \"|\w*Error: )")
# A Python warning ("x.py:12: RuntimeWarning: ...") and the source line the
# warnings module prints under it.
_PYWARN_RX = re.compile(r"^\S.*:\d+: \w*Warning: ")
_TAG_RX = re.compile(r"^\s*\[([#A-Za-z][^\]]{0,48})\]")
_HEAD_RX = re.compile(r"^\s*(#?[A-Za-z][^:\[\]]{0,48}?):\s")
_ZAP_TRIS_RX = re.compile(r"body-zap: [^:]+: (\d+) of (\d+) body triangles")
_DECLINED_RX = re.compile(r"^\s*\[([^\]]+)\][^\n]*\bDECLINED\b")
# Not passes: echoes of setup a worker repeats for every piece.
_NOT_A_PASS = {"body-ref", "zeroed-body", "progress", "progress-nif"}


def verbose() -> bool:
    """CBBE2UBE_RUN_LOG_VERBOSE=1: the main log carries every line, as before."""
    return _flag("CBBE2UBE_RUN_LOG_VERBOSE", False)


def split_active() -> bool:
    """True when the main log is the readable one: a run-log tee with a detail
    file is installed and the verbose switch is off. Read at each call: the
    settings file is applied after the tee is installed."""
    return (not verbose()) and getattr(sys.stdout, "detail_enabled", False) is True


def detail_path(main_path: str) -> str:
    """CBBEtoUBE_last_run.log -> CBBEtoUBE_last_run_detail.log (any .log path)."""
    stem, ext = os.path.splitext(main_path)
    return stem + DETAIL_SUFFIX + (ext or ".log")


def detail(text: str) -> None:
    """Write `text` to the detail log only, when the split is on; otherwise
    print it as before (no tee, a capture buffer, or the verbose switch)."""
    if not text:
        return
    if split_active():
        sys.stdout.write_detail(text if text.endswith("\n") else text + "\n")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def detail_print(*lines: str) -> None:
    """`print` for lines a person does not need: the detail log only."""
    detail("".join(f"{ln}\n" for ln in lines))


# ---- one unit's output -> one piece line ------------------------------------

def pass_name(line: str) -> "str | None":
    """The pass a worker line comes from: '[coherence-repair] Top: ...' ->
    'coherence-repair', '  roughness cap: smoothed ...' -> 'roughness cap',
    'body-zap: x.nif: ...' -> 'body-zap'. None for a line that names none."""
    s = line.strip()
    if not s:
        return None
    m = _TAG_RX.match(s)
    if m:
        tag = m.group(1).strip()
        if re.fullmatch(r"[\d/ ]+", tag):
            return None                     # "[3/3228] 0.4 NIF/s ..."
        return re.sub(r"\d+", "N", tag)     # "[phase1-antipoke]" stays itself
    m = _HEAD_RX.match(s)
    if m and not s.startswith(("PASS FAILED", "File ")):
        return m.group(1).strip()
    return None


class UnitDigest:
    """What one unit's printed output says, in a form a piece line can use."""
    __slots__ = ("passes", "lines", "zap", "declined", "kept", "pywarn")

    def __init__(self):
        self.passes: "Counter[str]" = Counter()   # pass -> lines, first-seen order
        self.lines = 0
        self.zap: "list[tuple[int, int]]" = []    # (removed, of) per zapped file
        self.declined: "list[str]" = []
        self.kept: "list[str]" = []               # KEEP_RX lines, as printed
        self.pywarn = 0


def digest(text: str) -> UnitDigest:
    d = UnitDigest()
    after_pywarn = False
    for ln in (text or "").splitlines():
        if not ln.strip():
            continue
        d.lines += 1
        if _PYWARN_RX.match(ln):
            d.pywarn += 1
            after_pywarn = True
            continue
        if after_pywarn and ln.startswith("  "):
            after_pywarn = False            # the source line under the warning
            continue
        after_pywarn = False
        if KEEP_RX.search(ln):
            d.kept.append(ln)
        m = _ZAP_TRIS_RX.search(ln)
        if m:
            d.zap.append((int(m.group(1)), int(m.group(2))))
        m = _DECLINED_RX.match(ln)
        if m and m.group(1) not in d.declined:
            d.declined.append(m.group(1))
        p = pass_name(ln)
        if p and p not in _NOT_A_PASS:
            d.passes[p] += 1
    return d


# Per-run state, in the parent: filled by echo_unit, read by piece_lines and
# the summary. Keyed by id() of the ConvertResult objects; each entry holds its
# result too, so an id is never reused while the entry exists (a unit re-run
# after running out of memory drops its first results).
_DIGESTS: "dict[int, tuple[object, UnitDigest]]" = {}
PASS_PIECES: "Counter[str]" = Counter()     # pass -> units whose output named it
PASS_LINES: "Counter[str]" = Counter()      # pass -> lines
_TOTALS = {"units": 0, "lines": 0, "pywarn": 0, "kept": 0}


def reset() -> None:
    _DIGESTS.clear()
    PASS_PIECES.clear()
    PASS_LINES.clear()
    for k in _TOTALS:
        _TOTALS[k] = 0


def echo_unit(text: str, results=()) -> None:
    """Print what one NIF unit's worker printed. Split on: the whole block to
    the detail log, its KEEP_RX lines to both logs, and a digest kept for the
    piece line. Split off: printed whole, as before."""
    if not text:
        return
    if not split_active():
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        return
    d = digest(text)
    detail(text)
    if d.kept:
        sys.stdout.write_main("".join(f"{ln}\n" for ln in d.kept))
    for r in results or ():
        _DIGESTS[id(r)] = (r, d)
    _TOTALS["units"] += 1
    _TOTALS["lines"] += d.lines
    _TOTALS["pywarn"] += d.pywarn
    _TOTALS["kept"] += len(d.kept)
    for p, n in d.passes.items():
        PASS_PIECES[p] += 1
        PASS_LINES[p] += n
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _status_word(r) -> str:
    st = str(getattr(r, "status", "") or "")
    m = re.match(r"converted \((.+)\)$", st)
    if m:
        return m.group(1)
    if st == "converted":
        return "converted"
    reason = str(getattr(r, "reason", "") or "").split("; ")[0]
    return f"{st.upper() if st == 'error' else st}: {reason[:90]}" if reason else st


def _piece_name(results) -> str:
    """'armor/bandit/bodyf (_0 _1)': the destination base, its weight suffixes."""
    from pathlib import Path
    paths = [getattr(r, "dst_path", None) or getattr(r, "src_path", None)
             for r in results]
    names = [Path(str(p)) for p in paths if p]
    if not names:
        return "?"
    p = names[0]
    parts = [x for x in p.parts]
    low = [x.lower() for x in parts]
    if "meshes" in low:
        parts = parts[len(low) - low[::-1].index("meshes"):]
    base = "/".join(parts[:-1] + [p.stem])
    sufs = sorted({n.stem[-2:] for n in names if n.stem[-2:] in ("_0", "_1")})
    if sufs and base[-2:] in ("_0", "_1"):
        base = base[:-2]
    return base + (f" ({' '.join(sufs)})" if sufs else "")


def _piece_line(results, d: "UnitDigest | None", indent: str) -> str:
    words = sorted({_status_word(r) for r in results})
    bits = ["/".join(words)]
    if d is not None:
        if d.zap:
            bits.append(f"zap {max(z[0] for z in d.zap)}/{d.zap[0][1]} tris")
        names = [p for p in d.passes if p != "body-zap"]
        if names:
            shown = ", ".join(names[:6])
            more = f" +{len(names) - 6}" if len(names) > 6 else ""
            bits.append(f"{len(names)} pass(es): {shown}{more}")
        if d.declined:
            bits.append("declined: " + ", ".join(d.declined[:3]))
    fails = []
    for r in results:
        for part in str(getattr(r, "reason", "") or "").split("; "):
            if part.startswith("PASS FAILED"):
                fails.append(part.split(" (")[0][len("PASS FAILED "):])
    if fails:
        bits.append("!! pass failed: " + ", ".join(sorted(set(fails))[:3]))
    return f"{indent}{_piece_name(results)}  -- " + " | ".join(bits)


def _digest_of(r) -> "UnitDigest | None":
    entry = _DIGESTS.get(id(r))
    return entry[1] if entry is not None and entry[0] is r else None


def piece_lines(nif_results, indent: str = "      ") -> "list[str]":
    """One line per piece (the weight variants of one base, converted as one
    unit), in path order. Empty when the split is off."""
    if not split_active() or not nif_results:
        return []
    groups: "dict[object, list]" = {}
    order: "list[object]" = []
    for r in nif_results:
        d = _digest_of(r)
        # A unit that printed nothing has no digest: its weight variants are
        # still one piece.
        key = id(d) if d is not None else ("quiet", _piece_name([r]).split(" (")[0].lower())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    rows = []
    for key in order:
        rs = groups[key]
        rows.append((_piece_name(rs).lower(),
                     _piece_line(rs, _digest_of(rs[0]), indent)))
    return [line for _k, line in sorted(rows)]


# ---- the summary ------------------------------------------------------------

_SECTION_RX = re.compile(r"^(===|---) (.+?)(?: ===| ---)?\s*$")


class RunObserver:
    """Watches every line the run prints (the tee feeds it): section starts
    with their time, problem and note lines, and the few lines the summary
    quotes. Never raises into the writer."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.t0 = clock()
        self.sections: "list[tuple[float, str]]" = []
        self.problems: "list[str]" = []
        self.notes: "list[str]" = []
        self.quotes: "dict[str, str]" = {}
        self._buf = ""

    def feed(self, s: str) -> None:
        try:
            self._buf += s
            if "\n" not in self._buf:
                return
            *full, self._buf = self._buf.split("\n")
            for ln in full:
                self._line(ln)
        except Exception:
            pass

    def _line(self, ln: str) -> None:
        s = ln.strip()
        if not s or s.startswith(("[progress", END_MARKER)):
            return
        m = _SECTION_RX.match(s)
        if m:
            self.sections.append((self._clock(), m.group(2)))
            for key in ("auto:", "NIF conversion done:", "NIF conversion:"):
                if m.group(2).startswith(key) and key not in self.quotes:
                    self.quotes[key] = m.group(2)
            if re.match(r"\d+ failure\(s\), \d+ warning\(s\)$|all clear$", m.group(2)):
                self.quotes["tally"] = m.group(2)
            return
        if s.startswith("!!"):
            if s not in self.problems:
                self.problems.append(s)
        elif s.startswith("NOTE:"):
            if s not in self.notes:
                self.notes.append(s)
        elif s.startswith("coverage report:"):
            self.quotes["coverage"] = s.split(":", 1)[1].strip()

    def time_table(self, now: "float | None" = None) -> "list[tuple[str, float]]":
        """(section kind, seconds) for the run's sections, largest first. The
        per-mod sections ('--- [3/149] planning ...') are summed into one."""
        now = self._clock() if now is None else now
        out: "dict[str, float]" = {}
        marks = self.sections + [(now, None)]
        if self.sections:
            out["reading the modlist"] = self.sections[0][0] - self.t0
        for (t, title), (t2, _n) in zip(marks, marks[1:]):
            out[section_kind(title)] = out.get(section_kind(title), 0.0) + (t2 - t)
        return sorted(out.items(), key=lambda kv: -kv[1])


def section_kind(title: str) -> str:
    """'[3/149] planning 'Mod'' -> 'planning (each mod)'; 'NIF conversion: 3228
    file(s) ...' -> 'NIF conversion'; '0 failure(s), 2 warning(s)' -> 'end'."""
    if re.match(r"\d+ failure\(s\)|all clear$", title):
        return "end"
    if title.startswith("auto:"):
        return "batch set-up"
    per_mod = re.search(r"\[\d+/\d+\]", title) is not None
    kind = re.sub(r"\[\d+/\d+\]\s*", "", title)
    kind = re.split(r"[:(']|\s\d", kind)[0].strip() or title
    return kind + (" (each mod)" if per_mod else "")


def _hms(sec: float) -> str:
    sec = int(round(max(0.0, sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _wrap_items(items: "list[str]", width: int = 108, indent: str = "      ") -> "list[str]":
    lines, cur = [], ""
    for it in items:
        if cur and len(cur) + 3 + len(it) > width:
            lines.append(indent + cur)
            cur = it
        else:
            cur = f"{cur} · {it}" if cur else it
    if cur:
        lines.append(indent + cur)
    return lines


def summary_lines(observer: "RunObserver | None", failures=(), *, rc=None,
                  main_path: str = "", detail: str = "") -> "list[str]":
    """The run summary, the same block at the top and the bottom of the main
    log. `failures` is the run's recorded list (auto_convert._RUN_FAILURES),
    the one the tally and the window's popup are counted from."""
    ob = observer or RunObserver()
    fails = [f for f in failures if f.get("severity", "failure") == "failure"]
    warns = [f for f in failures if f.get("severity") == "warning"]
    bar = "=" * 30
    out = [f"{bar} RUN SUMMARY {bar}"]
    tally = ob.quotes.get("tally") or (f"{len(fails)} failure(s), {len(warns)} warning(s)")
    out.append(f"  result   : {tally}" + (f" (exit {rc})" if rc is not None else ""))
    if "auto:" in ob.quotes:
        out.append(f"  mods     : {ob.quotes['auto:'][len('auto:'):].strip()}")
    if "NIF conversion done:" in ob.quotes:
        out.append(f"  meshes   : {ob.quotes['NIF conversion done:'][len('NIF conversion done:'):].strip()}")
    table = ob.time_table()
    if table:
        total = sum(v for _k, v in table)
        parts = [f"{k} {_hms(v)}" for k, v in table[:6] if v >= 1]
        out.append(f"  time     : {_hms(total)} in all"
                   + (" -- " + ", ".join(parts) if parts else ""))

    def _entry(f):
        n = f" (x{f['count']})" if f.get("count") else ""
        det = f" -- {f['detail'][:160]}" if f.get("detail") else ""
        return f"      - {f.get('kind', '?')}{n}: {f.get('source', '')} / {f.get('item', '')}{det}"

    for title, rows in (("failures", fails), ("warnings", warns)):
        out.append(f"  {title:9s}: {len(rows) if rows else 'none'}")
        out += [_entry(f) for f in rows[:25]]
        if len(rows) > 25:
            out.append(f"      ... and {len(rows) - 25} more (CBBEtoUBE_last_failures.json)")
    if ob.problems:
        out.append(f"  problems printed during the run ({len(ob.problems)}), first line of each:")
        out += [f"      {p[:200]}" for p in ob.problems[:20]]
        if len(ob.problems) > 20:
            out.append(f"      ... and {len(ob.problems) - 20} more (search the log for '!!')")
    if ob.notes:
        out.append(f"  notes ({len(ob.notes)}):")
        out += [f"      {p[:200]}" for p in ob.notes[:10]]
    if PASS_PIECES:
        out.append(f"  passes   : pieces each pass changed, of {_TOTALS['units']} "
                   f"converted unit(s); {_TOTALS['lines']} pass line(s) are in the detail log")
        out += _wrap_items([f"{p} {n}" for p, n in
                            sorted(PASS_PIECES.items(), key=lambda kv: (-kv[1], kv[0]))])
    if _TOTALS["pywarn"]:
        out.append(f"  python   : {_TOTALS['pywarn']} RuntimeWarning(s) from numeric code "
                   "(detail log)")
    if ob.quotes.get("coverage"):
        out.append(f"  coverage : {ob.quotes['coverage']}")
    if main_path:
        out.append(f"  run log  : {main_path}")
    if detail:
        out.append(f"  detail   : {detail}  (every pass line; grep this one)")
    out.append("=" * (len(bar) * 2 + 13))
    return out


def prepend_to_file(path: str, lines: "list[str]") -> bool:
    """Put `lines` at the top of the file at `path`, in place (the window may
    still hold it open for reading, which forbids a rename on Windows). True
    when written. The caller has closed every writer of the file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            body = f.read()
        # The marker has served its reader. A text-mode writer on Windows
        # wrote "\r\n"; the head keeps the file's own line ends.
        body = re.sub(r"(?m)^" + re.escape(END_MARKER) + r"\r?\n", "", body, count=1)
        nl = "\r\n" if "\r\n" in body else "\n"
        head = nl.join(lines) + nl + nl
        with open(path, "r+", encoding="utf-8", newline="") as f:
            f.seek(0)
            f.write(head + body)
            f.truncate()
        return True
    except Exception:
        return False
