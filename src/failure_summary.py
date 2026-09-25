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

"""What a run's failures file says, in words a person reads. #run-warnings

`CBBEtoUBE_last_failures.json` lists everything a user must hear about after a
run. Every entry used to be worded as a FAILURE: a run whose only entry was
"no race coverage was generated" opened a popup titled "1 item(s) failed to
convert" that told the user the armour was invisible, over a log that ended
"=== all clear ===". Each entry now carries a `severity` -- "failure" (did not
convert) or "warning" (converted, but needs attention) -- and the popup and
the status line are worded from it here. Tk-free, so it is tested without a
display."""
from __future__ import annotations

FAILURE, WARNING = "failure", "warning"


def severity_of(entry: dict) -> str:
    """An entry written before severities existed is a failure."""
    return WARNING if (entry or {}).get("severity") == WARNING else FAILURE


def count_of(entry: dict) -> int:
    """How many problems one entry stands for: its `count`, else 1. An entry
    for a whole class (N patch-validator hits) carries N. #one-tally"""
    try:
        return max(0, int((entry or {}).get("count", 1)))
    except (TypeError, ValueError):
        return 1


def counts(entries) -> tuple:
    """(failures, warnings) -- the same numbers the run log's end-of-run tally
    prints, because the run counts its tally from these entries. #one-tally"""
    failures = warnings = 0
    for e in entries or []:
        if severity_of(e) == WARNING:
            warnings += count_of(e)
        else:
            failures += count_of(e)
    return failures, warnings


# FAILED entries whose output WAS written: what went wrong is what the run
# wrote, not a conversion that did not happen. #popup-per-kind
# The popup said every FAILED item "did NOT convert -- their armor keeps its
# previous state", which was wrong for a Combined ESP the run built and then
# found NOT safe to load, and for a mesh written with a crash defect: the user
# was told nothing had changed about a file that had. Each such kind has its
# own sentence; every other failure is a conversion that did not happen.
WRITTEN_BUT_BROKEN = {
    "load-breaking plugin issue": (
        "the Combined ESP was built, but it is NOT safe to load; keep it "
        "disabled until the issues listed are fixed"),
    "CTD-class mesh issue": (
        "the mesh was written, but it can crash the game when equipped"),
    "output mesh unreadable": (
        "the mesh was written, but it cannot be read back"),
    "partial mesh (shape dropped)": (
        "the mesh was written with a part of it left out"),
    "merge failed": (
        "the meshes converted, but no Combined ESP was built this run"),
    "merge skipped": (
        "the meshes converted, but no Combined ESP was built this run"),
}


def _failure_split(entries) -> "tuple[int, dict]":
    """(count of failures that did not convert, {written-but-broken kind:
    count} in first-seen order). #popup-per-kind"""
    not_converted, broken = 0, {}
    for e in entries or []:
        if severity_of(e) == WARNING:
            continue
        kind = (e or {}).get("kind", "")
        if kind in WRITTEN_BUT_BROKEN:
            broken[kind] = broken.get(kind, 0) + count_of(e)
        else:
            not_converted += count_of(e)
    return not_converted, broken


def popup_title(entries) -> str:
    _failures, warnings = counts(entries)
    not_converted, broken = _failure_split(entries)
    parts = []
    if not_converted:
        parts.append(f"{not_converted} item(s) failed to convert")
    if broken:
        parts.append(f"{sum(broken.values())} problem(s) in what was written")
    if not parts:
        return f"{warnings} warning(s) from this run"
    if warnings:
        parts.append(f"{warnings} warning(s)")
    return ", ".join(parts)


def popup_intro(entries) -> str:
    failures, warnings = counts(entries)
    not_converted, broken = _failure_split(entries)
    parts = []
    if not_converted:
        parts.append("Items marked FAILED did NOT convert this run — their armor "
                     "keeps its previous state (or is invisible on UBE actors)."
                     + ("" if broken else " Everything else converted normally."))
    for kind in broken:
        parts.append(f"Items marked FAILED: {kind} — {WRITTEN_BUT_BROKEN[kind]}.")
    if warnings:
        parts.append(("Items marked WARNING converted" if failures
                      else "Everything converted")
                     + ", but needs your attention before you play; each line "
                       "says why.")
    parts.append("Details are also in the log and the coverage report.")
    return " ".join(parts)


def popup_lines(entries) -> list:
    """Grouped by source mod, failures before warnings within a source."""
    by_source: dict = {}
    for e in entries or []:
        by_source.setdefault(e.get("source", "?"), []).append(e)
    lines = []
    for source, group in by_source.items():
        lines.append(source)
        for e in sorted(group, key=lambda e: severity_of(e) == WARNING):
            tag = "WARNING" if severity_of(e) == WARNING else "FAILED"
            detail = f" — {e['detail']}" if e.get("detail") else ""
            lines.append(f"    [{tag}: {e.get('kind', '?')}] {e.get('item', '')}{detail}")
        lines.append("")
    return lines


def status_line(rc: int, entries, cancelled: bool = False) -> str:
    """The GUI status bar once a run has finished.

    A cancelled run is what the user asked for: no exit-code wording, and
    whatever the killed child left in the failures file does not make it a
    failed run. #cancel-says-so"""
    if cancelled:
        return ("Cancelled - the run was stopped at your request. The Results "
                "tab shows what finished before that; the log says where it stopped.")
    failures, warnings = counts(entries)
    if rc != 0:
        return f"Finished with exit code {rc} - check the log for errors/warnings."
    not_converted, _broken = _failure_split(entries)
    if not_converted:
        return (f"Done (exit 0), but {not_converted} item(s) did not convert - "
                "see the list that opened and the log.")
    if failures:                                    # #popup-per-kind
        return (f"Done (exit 0), but {failures} problem(s) in what was written "
                "- see the list that opened and the log.")
    if warnings:
        return (f"Done with {warnings} warning(s) (exit 0) - see the list that "
                "opened and the log before you play.")
    return "Done - success (exit 0). See the Results tab for coverage notes."
