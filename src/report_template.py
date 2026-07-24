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

"""One problem-report format, for both intakes.

Reports arrive two ways -- pasted into a chat channel, or filed as a GitHub
issue -- and a report that is useful in one is useful in the other. So the
format is defined ONCE here and rendered as plain text: the GUI copies it to
the clipboard, and it is also dropped into the diagnostics zip so the zip
carries its own cover sheet.

Deliberately plain text, not Markdown: it has to survive a paste into a chat
box that may or may not render Markdown, and a fenced block in a GitHub issue
either way. Fixed-width labels keep it readable in both.

ASCII ONLY in the emitted text. It gets printed to a Windows console (cp1252),
copied through the Tk clipboard, and written into a zip -- an em dash turns
into a mojibake smear or a UnicodeEncodeError somewhere along that path.

Anything the tool already knows is filled in, because a field the user has to
look up is a field that comes back blank. Everything else is left as an
explicit placeholder rather than omitted, so a half-filled report still shows
which questions went unanswered.
"""
from __future__ import annotations

REPO_URL = "https://github.com/DayOnly/CBBEtoUBE-exe"
ISSUES_URL = f"{REPO_URL}/issues"
DISCUSSIONS_URL = f"{REPO_URL}/discussions"

# Issue-form template filenames, keyed by the report kind used across the GUI.
_TEMPLATES = {
    "bug": "bug_report.yml",
    "conversion": "conversion_problem.yml",
    "feature": "feature_request.yml",
}

# Mirrors the `symptom` dropdown in .github/ISSUE_TEMPLATE/conversion_problem.yml.
# Kept in sync deliberately: the pasted report and the filed issue should use the
# same words for the same problem, so the two intakes stay searchable together.
SYMPTOMS = (
    "Armor is invisible / does not render",
    "Clipping or poke-through (body pushes through the armor)",
    "Gaps, seams, or a neck/wrist/ankle mismatch",
    "Physics wrong - no jiggle, or cloth behaves badly",
    "Body morphs (RaceMenu sliders) do not follow the armor",
    "High heels - character floats or sinks",
    "RaceMenu overlay (tattoo / body paint) is misaligned",
    "Armor was skipped and never converted",
    "Something else",
)

_PREREQS = (
    "SkyPatcher installed",
    "iEnableArmorPatching=1 in SKSE/Plugins/SkyPatcher.ini",
    "every CBBE_to_UBE_Combined*.esp enabled",
    "UBE + UBE_AllRace.esp, RaceCompatibility, RaceMenu installed",
    "output mod last in load order and winning its file conflicts",
)

_KIND_LABEL = {
    "bug": "Bug - the converter errored",
    "conversion": "Conversion problem - output looks wrong in game",
    "feature": "Feature request",
}


def issue_url(kind: str = "bug", version: str | None = None) -> str:
    """Direct 'file this issue' link, with the right form preselected.

    GitHub issue forms prefill from query params named after the field `id`, so
    `version` arrives already filled. One less field for the reporter, and one
    less report that says 'latest' instead of a number.
    """
    template = _TEMPLATES.get(kind)
    if template is None:
        return f"{ISSUES_URL}/new/choose"
    url = f"{ISSUES_URL}/new?template={template}"
    if version:
        from urllib.parse import quote
        url += f"&version={quote(str(version))}"
    return url


def _run_stats(report: dict | None) -> list[str]:
    """The LAST RUN block, read from conversion_report.json."""
    if not report:
        return ["  (no conversion_report.json found - run a conversion first)"]

    def n(key):
        v = report.get(key)
        return v if isinstance(v, int) else "?"

    lines = [
        f"  source mods:     {n('source_mods')}",
        f"  converted ok:    {n('converted_ok')}",
        f"  armor nifs:      {n('armor_nifs')}",
        f"  esp patches:     {n('esp_patches')}",
        f"  hard failures:   {n('hard_failures')}",
        f"  nif errors:      {n('nif_errors')}",
        f"  load failures:   {n('load_failures')}",
    ]
    # Only surface the problem lists when they are non-empty -- a wall of
    # "0 / none" pushes the real signal off the bottom of a chat message.
    zero = report.get("zero_mesh_mods") or []
    if zero:
        lines.append(f"  zero-mesh mods:  {len(zero)} ({', '.join(map(str, zero[:5]))}"
                     + (", ..." if len(zero) > 5 else "") + ")")
    dup = report.get("zero_mesh_dup_mods") or []
    if dup:
        lines.append(f"  dup-collision:   {len(dup)} ({', '.join(map(str, dup[:5]))}"
                     + (", ..." if len(dup) > 5 else "") + ")")
    warn = report.get("weight_partner_warnings") or []
    if warn:
        lines.append(f"  weight-partner warnings: {len(warn)}  <- invisibility risk")
    failed = report.get("failed_mods") or []
    for f in failed[:5]:
        if isinstance(f, dict):
            lines.append(f"  FAILED: {f.get('name', '?')}: {f.get('error', '')}"[:200])
    if len(failed) > 5:
        lines.append(f"  ... and {len(failed) - 5} more failed mods")
    return lines


def build_report(version: str,
                 kind: str = "conversion",
                 symptom: str | None = None,
                 report: dict | None = None,
                 diagnostics_zip: str | None = None) -> str:
    """Render the plain-text report. Safe to paste anywhere."""
    out: list[str] = []
    add = out.append

    add("CBBEtoUBE problem report")
    add("========================")
    add(f"Version:  {version}")
    add(f"Type:     {_KIND_LABEL.get(kind, kind)}")
    if kind == "conversion":
        add(f"Symptom:  {symptom or '<pick one: ' + SYMPTOMS[0] + ' / ...>'}")
    add("")

    add("WHAT HAPPENS")
    add("  <what you see, on which armor, and when>")
    add("")

    add("AFFECTED ARMOR / SOURCE MOD")
    add("  <armor piece, and the mod it came from>")
    add("")

    if kind != "feature":
        add("PREREQUISITES  (put an x in the brackets once checked)")
        for p in _PREREQS:
            add(f"  [ ] {p}")
        add("")

    add("LAST RUN")
    out.extend(_run_stats(report))
    add("")

    add("DIAGNOSTICS")
    if diagnostics_zip:
        add(f"  [x] {diagnostics_zip}")
        add("      attach this file to the issue, or upload it to the chat")
    else:
        add("  [ ] CBBEtoUBE_diagnostics_<timestamp>.zip  (GUI -> Export diagnostics)")
    add("      it holds your MO2 paths, profile name, and load-order mod names -")
    add("      look it over before posting it publicly.")
    add("")

    # Both routes, every time. A reporter who picks the wrong one costs a
    # round trip; a reporter who cannot find either just gives up.
    add("WHERE TO SEND THIS")
    add("  want it fixed  -> " + issue_url(kind, version))
    add("  want an answer -> " + DISCUSSIONS_URL)
    add("  pasting into chat or a discussion? wrap this in a ``` code fence,")
    add("  or the indentation and checkboxes collapse.")

    return "\n".join(out)
