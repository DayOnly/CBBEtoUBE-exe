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


def counts(entries) -> tuple:
    """(failures, warnings)."""
    entries = list(entries or [])
    warnings = sum(1 for e in entries if severity_of(e) == WARNING)
    return len(entries) - warnings, warnings


def popup_title(entries) -> str:
    failures, warnings = counts(entries)
    if failures and warnings:
        return f"{failures} item(s) failed to convert, {warnings} warning(s)"
    if failures:
        return f"{failures} item(s) failed to convert"
    return f"{warnings} warning(s) from this run"


def popup_intro(entries) -> str:
    failures, warnings = counts(entries)
    parts = []
    if failures:
        parts.append("Items marked FAILED did NOT convert this run — their armor "
                     "keeps its previous state (or is invisible on UBE actors). "
                     "Everything else converted normally.")
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
    if failures:
        return (f"Done (exit 0), but {failures} item(s) did not convert - see the "
                "list that opened and the log.")
    if warnings:
        return (f"Done with {warnings} warning(s) (exit 0) - see the list that "
                "opened and the log before you play.")
    return "Done - success (exit 0). See the Results tab for coverage notes."
