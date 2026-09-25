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

"""#constraint-group-scan -- a constraint inside a <constraint-group> counts.

The validator asked `root.find("generic-constraint")`, which sees DIRECT
children of <system> and one constraint kind. Chains are usually written inside
<constraint-group> blocks (the converter's own generator does it), so a rigged
skirt was reported as "the piece has NO constraints ... it needs a rigged chain
first" -- advice that is wrong, and wrong in the direction that hides the fix.
The same narrow read left stiffspring/conetwist constraint bodies out of the
unresolvable-bone check, and the SMP disable script's substring test missed
those kinds too (and took `<generic-constraint-default>` for a constraint).

A genuinely unconstrained cloth + collider must still read as unconstrained.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
from src import hdt_xml_gen as hx  # noqa: E402
import disable_unconstrained_smp as dus  # noqa: E402

OFF = "CBBE2UBE_NO_CONSTRAINT_GROUP_SCAN"
PELVIS = "NPC Pelvis [Pelv]"


def _system(constraints: str) -> str:
    # The cloth names no body tag, so the validator must say whether the piece
    # is constrained -- that sentence is what this module is about.
    return ('<system>'
            f'<bone name="{PELVIS}"/>'
            '<per-triangle-shape name="VirtualBody">'
            '<tag>body</tag><can-collide-with-tag>cloth1</can-collide-with-tag>'
            '</per-triangle-shape>'
            '<per-vertex-shape name="Skirt"><tag>cloth1</tag>'
            '<can-collide-with-tag>ground</can-collide-with-tag>'
            '</per-vertex-shape>'
            f'{constraints}'
            '</system>')


def _why(tmp_path, constraints):
    p = tmp_path / "skirt.xml"
    p.write_text(_system(constraints), encoding="utf-8")
    hits = [w for w in hx.validate_armor_hdt_xml(p, [PELVIS])
            if "no body-ish can-collide-with-tag" in w]
    assert len(hits) == 1, hits
    if "IS constrained" in hits[0]:
        return "constrained"
    assert "has NO constraints" in hits[0], hits[0]
    return "unconstrained"


GROUPED = ('<constraint-group>'
           f'<generic-constraint bodyA="{PELVIS}" bodyB="{PELVIS}"/>'
           '</constraint-group>')


def test_a_constraint_inside_a_group_counts(tmp_path, monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    assert _why(tmp_path, GROUPED) == "constrained"


@pytest.mark.parametrize("kind", ["stiffspring-constraint",
                                  "conetwist-constraint"])
def test_every_constraint_kind_counts(tmp_path, monkeypatch, kind):
    monkeypatch.delenv(OFF, raising=False)
    xml = f'<{kind} bodyA="{PELVIS}" bodyB="{PELVIS}"/>'
    assert _why(tmp_path, xml) == "constrained"


def test_a_genuinely_unconstrained_pair_is_still_caught(tmp_path, monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    assert _why(tmp_path, "") == "unconstrained"


def test_an_empty_group_is_not_a_constraint(tmp_path, monkeypatch):
    """An empty group holds no spring; calling it constrained would advise
    adding body collision to the crash pattern."""
    monkeypatch.delenv(OFF, raising=False)
    xml = ('<generic-constraint-default><linearLowerLimit x="0" y="0" z="0"/>'
           '</generic-constraint-default><constraint-group></constraint-group>')
    assert _why(tmp_path, xml) == "unconstrained"


def test_switched_off_the_group_reads_unconstrained_again(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    assert _why(tmp_path, GROUPED) == "unconstrained"


def test_a_grouped_generated_chain_reads_constrained(tmp_path, monkeypatch):
    """The converter's own generator writes chain constraints inside a group."""
    monkeypatch.delenv(OFF, raising=False)
    bones = ["NPC Pelvis [Pelv]", "Skirt_01", "Skirt_02", "Skirt_03"]
    out = tmp_path / "gen.xml"
    hx.write_armor_hdt_xml(out, [("Skirt", bones)], "VirtualBody",
                           chains=hx.detect_physics_chains(bones))
    text = out.read_text(encoding="utf-8")
    assert "<constraint-group>" in text and "<generic-constraint " in text
    import xml.etree.ElementTree as ET
    root = ET.fromstring(text)
    assert root.find("generic-constraint") is None      # only nested
    assert hx._constraint_elements(root)


def _unresolved(tmp_path, kind):
    p = tmp_path / "chain.xml"
    p.write_text('<system>'
                 f'<bone name="{PELVIS}"/>'
                 '<constraint-group>'
                 f'<{kind} bodyA="{PELVIS}" bodyB="PhantomAnchor_00"/>'
                 '</constraint-group>'
                 '</system>', encoding="utf-8")
    return [w for w in hx.validate_armor_hdt_xml(p, [PELVIS])
            if "PhantomAnchor_00" in w]


def test_a_stiffspring_body_is_checked_for_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    assert _unresolved(tmp_path, "stiffspring-constraint")


def test_switched_off_only_generic_bodies_are_checked(tmp_path, monkeypatch):
    monkeypatch.setenv(OFF, "1")
    assert not _unresolved(tmp_path, "conetwist-constraint")
    assert _unresolved(tmp_path, "generic-constraint")


# -- scripts/disable_unconstrained_smp.py --------------------------------------

PAIR = ('<per-vertex-shape name="Skirt"/>'
        '<per-triangle-shape name="VirtualBody"/>')


def test_the_disable_script_keeps_a_stiffspring_chain(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    text = f'<system>{PAIR}<constraint-group><stiffspring-constraint bodyA="a" bodyB="b"/></constraint-group></system>'
    assert not dus.is_broken_collision_pair(text)


def test_the_disable_script_does_not_take_a_default_for_a_constraint(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    text = f'<system>{PAIR}<generic-constraint-default/></system>'
    assert dus.is_broken_collision_pair(text)


def test_the_disable_script_still_catches_the_crash_pair(monkeypatch):
    monkeypatch.delenv(OFF, raising=False)
    assert dus.is_broken_collision_pair(f"<system>{PAIR}</system>")
    grouped = f'<system>{PAIR}<constraint-group><generic-constraint bodyA="a" bodyB="b"/></constraint-group></system>'
    assert not dus.is_broken_collision_pair(grouped)


def test_the_disable_script_switched_off_is_the_old_test(monkeypatch):
    monkeypatch.setenv(OFF, "1")
    stiff = f'<system>{PAIR}<stiffspring-constraint bodyA="a" bodyB="b"/></system>'
    assert dus.is_broken_collision_pair(stiff)
    default = f'<system>{PAIR}<generic-constraint-default/></system>'
    assert not dus.is_broken_collision_pair(default)


def test_the_disable_script_leaves_a_stiffspring_chain_in_place(tmp_path, monkeypatch):
    """End to end: --apply renames the crash pair and keeps the chain."""
    monkeypatch.delenv(OFF, raising=False)
    (tmp_path / "chain.xml").write_text(
        f'<system>{PAIR}<conetwist-constraint bodyA="a" bodyB="b"/></system>',
        encoding="utf-8")
    (tmp_path / "crash.xml").write_text(f"<system>{PAIR}</system>",
                                        encoding="utf-8")
    r = subprocess.run([sys.executable, "-B",
                        str(_REPO / "scripts" / "disable_unconstrained_smp.py"),
                        str(tmp_path), "--apply"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "chain.xml").is_file()
    assert not (tmp_path / "crash.xml").exists()
