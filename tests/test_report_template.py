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

    def test_prereq_checklist_present_for_problems_not_features(self):
        assert "SkyPatcher installed" in rt.build_report("1.1.1", kind="conversion")
        assert "SkyPatcher installed" not in rt.build_report("1.1.1", kind="feature")


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
