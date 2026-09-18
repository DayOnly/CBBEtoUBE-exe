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

"""The report format is a user-facing contract, in two places at once.

It gets pasted into a chat box AND filed as a GitHub issue, so the things worth
pinning down are the ones that break silently in one channel but not the other:
non-ASCII that survives a browser and mangles a console, a symptom list that
drifts out of step with the issue form, and a template URL that 404s because a
form was renamed.
"""
from pathlib import Path

import pytest

from src import report_template as rt

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"


def _sample_report():
    return {
        "output_mod": r"C:\mods\CBBEtoUBE Auto",
        "source_mods": 37,
        "converted_ok": 35,
        "armor_nifs": 412,
        "esp_patches": 35,
        "nif_errors": 0,
        "hard_failures": 2,
        "load_failures": 0,
        "vfs_resolved": 12,
        "zero_mesh_mods": ["ModA", "ModB"],
        "zero_mesh_dup_mods": [],
        "failed_mods": [{"name": "ModC", "error": "RuntimeError('boom')"}],
        "weight_partner_warnings": ["armor_x"],
    }


class TestAsciiSafety:
    """The template crosses a cp1252 console, the Tk clipboard, and a zip.

    Any one of those turns a stray em dash into mojibake or a
    UnicodeEncodeError, and it does it on the user's machine, not ours.
    """

    @pytest.mark.parametrize("kind", ["bug", "conversion", "feature"])
    def test_rendered_report_is_pure_ascii(self, kind):
        text = rt.build_report("1.1.1", kind=kind, report=_sample_report())
        text.encode("ascii")  # raises UnicodeEncodeError if it ever regresses

    def test_survives_a_windows_console_codepage(self):
        text = rt.build_report("1.1.1", report=_sample_report())
        assert text.encode("cp1252").decode("cp1252") == text

    def test_module_source_is_ascii(self):
        """Guards the literals, not just today's rendered output."""
        src = (REPO_ROOT / "src" / "report_template.py").read_bytes()
        assert all(b < 128 for b in src), "report_template.py must stay ASCII"


class TestContent:
    def test_fills_in_what_the_tool_already_knows(self):
        text = rt.build_report("1.1.1", report=_sample_report())
        assert "1.1.1" in text
        assert "37" in text and "412" in text

    def test_surfaces_failures_and_invisibility_risk(self):
        text = rt.build_report("1.1.1", report=_sample_report())
        assert "ModC" in text
        assert "ModA" in text                      # zero-mesh mods
        assert "weight-partner" in text            # invisibility risk signal

    def test_quiet_when_there_is_nothing_to_report(self):
        """Empty problem lists stay out, or they bury the real signal."""
        clean = dict(_sample_report(),
                     zero_mesh_mods=[], failed_mods=[],
                     weight_partner_warnings=[], zero_mesh_dup_mods=[])
        text = rt.build_report("1.1.1", report=clean)
        assert "weight-partner" not in text
        assert "FAILED:" not in text

    def test_usable_before_any_conversion_has_run(self):
        text = rt.build_report("1.1.1", report=None)
        assert "no conversion_report.json" in text
        assert "1.1.1" in text

    def test_tolerates_a_malformed_report(self):
        """A truncated or hand-edited json must not stop a report going out."""
        text = rt.build_report("1.1.1", report={"source_mods": "not-an-int"})
        assert "?" in text

    def test_names_the_privacy_risk_of_the_zip(self):
        text = rt.build_report("1.1.1", diagnostics_zip="d.zip")
        assert "before posting it publicly" in text

    def test_offers_both_routes_not_just_one(self):
        """A reporter who can't find the right channel just gives up."""
        text = rt.build_report("1.1.1")
        assert rt.DISCUSSIONS_URL in text
        assert "/issues/new" in text

    def test_tells_chat_users_to_use_a_code_fence(self):
        """Without the fence Discord eats the indentation and the checkboxes."""
        assert "code fence" in rt.build_report("1.1.1")

    def test_prereq_checklist_present_for_problems_not_features(self):
        assert "SkyPatcher installed" in rt.build_report("1.1.1", kind="conversion")
        assert "SkyPatcher installed" not in rt.build_report("1.1.1", kind="feature")


class TestDocsMatchReality:
    """REPORTING.md shows a sample of the report, and users trust it.

    A sample that drifts from what the button actually produces is worse than
    no sample: it teaches a format nobody is asked for.
    """

    def test_every_section_heading_in_the_doc_sample_is_real(self):
        doc = (REPO_ROOT / "REPORTING.md").read_text(encoding="utf-8")
        rendered = rt.build_report("1.1.1", kind="conversion")
        headings = [ln for ln in rendered.splitlines()
                    if ln and ln == ln.upper() and not ln.startswith(" ")
                    and ln[0].isalpha()]
        assert headings, "no section headings found - did the format change?"
        for h in headings:
            assert h in doc, f"REPORTING.md sample is missing section: {h}"

    def test_doc_documents_the_prereq_checklist_verbatim(self):
        doc = (REPO_ROOT / "REPORTING.md").read_text(encoding="utf-8")
        for p in rt._PREREQS:
            assert p in doc, f"REPORTING.md missing prerequisite line: {p}"


class TestIssueUrls:
    @pytest.mark.parametrize("kind", ["bug", "conversion", "feature"])
    def test_points_at_a_template_that_exists(self, kind):
        """A renamed form would otherwise 404 the one link users click."""
        name = rt._TEMPLATES[kind]
        assert (TEMPLATE_DIR / name).is_file(), f"missing {name}"
        assert f"template={name}" in rt.issue_url(kind)

    def test_prefills_the_version_field(self):
        assert "version=1.1.1" in rt.issue_url("bug", "1.1.1")

    def test_version_is_url_encoded(self):
        assert " " not in rt.issue_url("bug", "1.1 beta").split("version=")[1]

    def test_unknown_kind_falls_back_to_the_chooser(self):
        assert rt.issue_url("nonsense").endswith("/new/choose")


def _form_items(name):
    """The `body:` items of an issue form -- type, id, label, placeholder, whether
    the field is required, and each option with its own `required` -- read as
    TEXT, because PyYAML is not a dependency here or in CI."""
    items, cur, in_options = [], None, False
    for line in (TEMPLATE_DIR / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("  - type: "):
            cur = {"type": line.split(":", 1)[1].strip(), "id": None, "label": None,
                   "placeholder": None, "required": False, "options": [],
                   "option_required": []}
            items.append(cur)
            in_options = False
            continue
        if cur is None:
            continue
        s = line.strip()
        if line.startswith("    id: "):
            cur["id"] = s.split(":", 1)[1].strip()
        elif line.startswith("      label: "):
            cur["label"] = s.split(":", 1)[1].strip()
        elif line.startswith("      placeholder: "):
            cur["placeholder"] = s.split(":", 1)[1].strip().strip('"')
        elif line.startswith("      options:"):
            in_options = True
        elif in_options and line.startswith("        - "):
            opt = s[2:]
            cur["options"].append(opt[len("label: "):] if opt.startswith("label: ") else opt)
        elif line.startswith("          required: "):
            cur["option_required"].append(s.endswith("true"))
        elif line.startswith("      required: "):
            cur["required"] = s.endswith("true")
        elif s and len(line) - len(line.lstrip()) <= 6:
            in_options = False
    return items


def _item(form, id_):
    return next((i for i in _form_items(form) if i["id"] == id_), None)


class TestIssueFormsAskTheDecisiveQuestions:
    """REPORTING.md says STANDING STILL or MOVING changes a clipping diagnosis more
    than anything else in a report, and asks which body slider and weight; USING.md's
    first rule is to launch through MO2. A form that does not ask gets the report
    nobody can diagnose. #report-questions"""

    def test_the_form_reader_sees_the_fields_both_forms_already_had(self):
        """Control: a parser that found nothing would pass every check below."""
        conv = {i["id"] for i in _form_items("conversion_problem.yml")}
        bug = {i["id"] for i in _form_items("bug_report.yml")}
        assert {"version", "symptom", "describe", "prereqs", "diagnostics"} <= conv, conv
        assert {"version", "how_run", "what_happened", "machine", "diagnostics"} <= bug, bug
        assert len(_item("conversion_problem.yml", "symptom")["options"]) >= 9

    def test_the_conversion_form_asks_standing_moving_or_zoomed_out(self):
        motion = _item("conversion_problem.yml", "motion")
        assert motion is not None, "the conversion form has no `motion` question"
        assert motion["type"] == "dropdown" and motion["required"]
        doc = (REPO_ROOT / "REPORTING.md").read_text(encoding="utf-8").replace("*", "").lower()
        options = " | ".join(motion["options"]).lower()
        for state in ("standing still", "only in motion", "only when zoomed out"):
            assert state in doc, f"REPORTING.md no longer says {state!r}"
            assert state in options, f"the motion question has no {state!r} answer"

    def test_the_conversion_form_asks_the_body_preset_and_weight(self):
        preset = _item("conversion_problem.yml", "preset")
        assert preset is not None and preset["type"] == "input"
        assert "weight" in preset["label"].lower()

    @pytest.mark.parametrize("form", ["bug_report.yml", "conversion_problem.yml"])
    def test_both_forms_ask_whether_it_was_launched_through_mo2(self, form):
        launch = _item(form, "launch")
        assert launch is not None and launch["type"] == "dropdown" and launch["required"]
        answers = [o.lower() for o in launch["options"]]
        assert any("through mo2" in a for a in answers) and any("outside mo2" in a for a in answers)

    def test_a_memory_report_cannot_skip_the_machine_block(self):
        """GitHub forms cannot make a field conditionally required, so a REQUIRED
        checkbox makes the reporter fill in the block or say it does not apply."""
        gated = [o for i in _form_items("bug_report.yml") if i["type"] == "checkboxes"
                 for o, req in zip(i["options"], i["option_required"])
                 if req and "Machine and memory" in o]
        assert gated, "nothing makes a memory report fill in Machine and memory"

    @pytest.mark.parametrize("form", ["bug_report.yml", "conversion_problem.yml"])
    def test_the_version_example_is_this_version(self, form):
        from src.version import __version__
        assert _item(form, "version")["placeholder"] == __version__

    def test_the_pasted_report_asks_the_same_two_questions(self):
        conversion = rt.build_report("1.4.1", kind="conversion")
        assert "standing still / only in motion / only when zoomed out" in conversion
        assert "body preset and weight" in conversion
        assert "standing still" not in rt.build_report("1.4.1", kind="bug")


class TestSymptomsMatchTheIssueForm:
    """Two intakes, one vocabulary.

    If the dropdown and the pasted template drift apart, the same bug gets
    filed under two different names and stops being searchable as one thing.
    """

    def test_every_symptom_appears_in_the_conversion_form(self):
        form = (TEMPLATE_DIR / "conversion_problem.yml").read_text(encoding="utf-8")
        for symptom in rt.SYMPTOMS:
            # The form is authored with typographic dashes; compare on the
            # stable part of each label rather than on punctuation.
            head = symptom.split(" - ")[0].split(" (")[0]
            assert head in form, f"symptom missing from issue form: {head}"


def test_a_report_from_a_run_that_died_says_so_first():
    """#report-checkpoint: the last-run block leads with the fact, not the counts."""
    from src import report_template as rt
    partial = {"complete": False, "source_mods": 3, "sources_planned": 7, "converted_ok": 3}
    lines = rt._run_stats(partial)
    assert "RUN DID NOT FINISH" in lines[0] and "3 of 7" in lines[0], lines
    finished = dict(partial, complete=True)
    assert not any("DID NOT FINISH" in ln for ln in rt._run_stats(finished))
    assert not any("DID NOT FINISH" in ln for ln in rt._run_stats({"source_mods": 3}))
