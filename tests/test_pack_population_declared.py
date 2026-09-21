"""A tool may survey half the pack. It may not do so SILENTLY.

`standoff_audit.output_nifs` exists because weight 0 is a separately authored
mesh, not a scaled copy of weight 1 -- on one measured cuirass, bust-front
clipping read 4.52% at weight 1 and 9.48% at weight 0. Its docstring counted
FIFTEEN scripts globbing `*_1.nif` only. It is 21 now.

This does NOT demand both weights. `morph_sweep` scopes to `_1` for a real
reason -- `_0` and `_1` are one garment at two weights, so scoring both
double-counts a per-garment rate -- and says so, which is all that is asked.

It is a RATCHET, not a cleanup: the 21 below are frozen debt, named so they are
visible, and the test fails when a NEW tool joins them or when a listed one
stops qualifying. Fixing one means deleting its line here.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts import tool_audit  # noqa: E402


# Frozen 2026-09-20. To remove a name: make the tool use `output_nifs`, or say
# in its docstring which weights it reads and why -- then delete the line.
KNOWN_HALF_PACK = frozenset({
    "scripts/analysis/band_class_census.py",
    "scripts/analysis/bust_gap_score.py",
    "scripts/analysis/collect_fit_dataset.py",
    "scripts/analysis/collect_penetration_census.py",
    "scripts/analysis/find_morph_follow_gaps.py",
    "scripts/analysis/find_overinflation.py",
    "scripts/analysis/fit_audit.py",
    "scripts/analysis/multipose_census.py",
    "scripts/analysis/nipple_clearance.py",
    "scripts/analysis/phase1_antipoke_population_ab.py",
    "scripts/analysis/postreconvert_audit.py",
    "scripts/analysis/registered_bone_audit.py",
    "scripts/analysis/single_swing_census.py",
    "scripts/analysis/snugness_census.py",
    "scripts/analysis/source_delta_census.py",
    "scripts/analysis/verify_bust_clearance.py",
    "scripts/analysis/verify_motion_match.py",
    "scripts/sanity_check_converted.py",
})


def _current():
    return {p.relative_to(_REPO).as_posix()
            for p in tool_audit.half_pack_tools()}


def test_no_new_tool_surveys_half_the_pack_silently():
    new = _current() - KNOWN_HALF_PACK
    assert not new, (
        "these tools enumerate the pack with a `*_1.nif` glob and never say "
        "so, which reports on half the shipped output as if it were all:\n  "
        + "\n  ".join(sorted(new))
        + "\n\nUse standoff_audit.output_nifs, or state in the docstring "
          "which weights it reads and why.")


def test_the_frozen_list_does_not_rot():
    """A name that no longer qualifies must leave the list, or the list stops
    describing the tree and the ratchet silently loosens."""
    gone = KNOWN_HALF_PACK - _current()
    assert not gone, (
        "these no longer survey half the pack silently -- delete them from "
        "KNOWN_HALF_PACK:\n  " + "\n  ".join(sorted(gone)))


def test_the_detector_finds_the_thing_it_is_looking_for(tmp_path):
    """CONTROL. A detector that matched nothing would make both tests above
    pass forever, which is the exact failure this whole audit is about."""
    bad = tmp_path / "silent_tool.py"
    bad.write_text('"""No weight scoping stated."""\n'
                   'import glob\n'
                   'files = glob.glob("out/**/*_1.nif", recursive=True)\n',
                   encoding="utf-8")
    assert tool_audit.surveys_half_the_pack(bad)


def test_the_detector_accepts_a_tool_that_declares_its_scoping(tmp_path):
    """The rule is DECLARE, not "always both weights"."""
    ok = tmp_path / "declared_tool.py"
    ok.write_text('"""Scores a rate per garment.\n\n'
                  '`_1` only: `_0` and `_1` are one garment at two weights.\n'
                  '"""\n'
                  'import glob\n'
                  'files = glob.glob("out/**/*_1.nif", recursive=True)\n',
                  encoding="utf-8")
    assert not tool_audit.surveys_half_the_pack(ok)


def test_using_output_nifs_counts_as_declaring(tmp_path):
    ok = tmp_path / "helper_tool.py"
    ok.write_text('"""Uses the shared enumerator."""\n'
                  'import glob\n'
                  'from scripts.analysis.standoff_audit import output_nifs\n'
                  'files = glob.glob("out/**/*_1.nif", recursive=True)\n',
                  encoding="utf-8")
    assert not tool_audit.surveys_half_the_pack(ok)


def test_a_tool_that_does_not_enumerate_is_not_flagged(tmp_path):
    """Mentioning `_1.nif` in a usage line is not enumerating the pack."""
    ok = tmp_path / "single_file_tool.py"
    ok.write_text('"""python x.py <piece_1.nif>"""\n'
                  'import sys\n'
                  'p = sys.argv[1]\n', encoding="utf-8")
    assert not tool_audit.surveys_half_the_pack(ok)
