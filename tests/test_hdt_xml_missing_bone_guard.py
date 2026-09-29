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

"""#hdt-xml-unresolvable-bones (issue #32): an authored physics XML that drives
whole chains this mesh never had is not shipped.

A cuirass converted from a retexture mod's loose mesh carried the SMP mod's
physics pointer but none of that mod's front-tasset chain nodes. The pointer
resolved through the VFS to an XML whose `SkirtF 4..7` chains (six links each)
exist in no mesh and not on the actor skeleton; the tassets fell through the
floor. `_HDT_REGEN_MISSING_BONES` said so (True), but the phases consult it
only behind a scan of the SOURCE mod's own tree, and the finalize -- the pass
that resolved the pointer and copied the file -- never asked.

The count is over bones of chains with NO resolvable link, not over every
bone that resolves nowhere: shared authored XMLs routinely declare the union
of several meshes' rigs (the SMP mod's own cuirass has 30 such bones, the
sixth link of 16 five-link chains), FSMP drops those harmlessly, and counting
them would decline 72 of the pack's 153 authored XMLs.

Pinned here: resolution is against the output NIF (skin bones and nodes), the
source NIF and the actor skeleton; tail links are not a phantom chain; the
guard needs a skeleton to judge; and at the threshold the finalize ships the
generator's file instead of the author's, recorded as an EFFECT (the piece
converted; a `PASS FAILED` line scored it broken), which the parent turns into
one run WARNING per source naming each piece.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import nif_convert as nc                 # noqa: E402
from src import nif_convert_physics as ph         # noqa: E402
from src import nif_convert_telemetry as tel      # noqa: E402
from tests import _converter_sources as _cs       # noqa: E402

# One object, so `_actor_can_resolve_bone`'s identity-keyed cache is stable.
SKELETON = {"npc pelvis [pelv]", "npc spine [spn0]", "npc l thigh [lthg]",
            "npc r thigh [rthg]", "npc l hand [lhnd]", "npc r hand [rhnd]",
            "npc root [root]", "npc l breast", "npc r breast"}

NIF_BONES = ["NPC Pelvis [Pelv]", "NPC Spine [Spn0]", "Skirt 1_00", "Skirt 1_01",
             "Skirt 2_00", "Skirt 2_01"]


def _chain(prefix, links, first=0):
    return [f"{prefix}_{i:02d}" for i in range(first, first + links)]


def _xml(bones):
    decl = "".join(f'<bone name="{b}"><mass>1</mass></bone>' for b in bones)
    return (f'<?xml version="1.0" encoding="UTF-8"?><system>{decl}'
            f'<per-vertex-shape name="Skirt"><tag>Fabric</tag></per-vertex-shape>'
            f'</system>')


class _Shape:
    def __init__(self, name, bones):
        self.name = name
        self.bone_names = list(bones)
        self.verts = [(0.0, 0.0, 50.0)]


class _Nif:
    def __init__(self, bones, nodes=()):
        self.shapes = [_Shape("Skirt", bones)]
        self.nodes = {n: object() for n in nodes}


@pytest.fixture(autouse=True)
def _skeleton(monkeypatch):
    _cs.patch(monkeypatch, "_actor_skeleton_bone_names", lambda: SKELETON)
    monkeypatch.setattr(nc, "_SKELETON_BONES_NORM_CACHE", None)
    monkeypatch.setattr(nc, "_HDT_REGEN_MISSING_BONES", 8)
    tel._begin_piece_pass_log()
    yield
    tel._begin_piece_pass_log()


def _unshippable(tmp_path, monkeypatch, xml, nif=None, src=None):
    p = tmp_path / "authored.xml"
    p.write_text(xml, encoding="utf-8")
    _cs.patch(monkeypatch, "_open_source_nif",
              lambda *_a, **_k: src if src is not None else _Nif(NIF_BONES))
    return ph._authored_hdt_xml_unshippable(p, nif or _Nif(NIF_BONES), tmp_path / "s.nif")


# ------------------------------------------------------------- the count

def test_twelve_bones_in_two_whole_chains_fire_the_guard(tmp_path, monkeypatch):
    """20 bones: 6 in the NIF, 2 on the skeleton only, 12 in two six-link
    chains no link of which exists anywhere."""
    nowhere = _chain("SkirtF 4", 6) + _chain("SkirtF 5", 6)
    xml = _xml(NIF_BONES + ["NPC L Hand [LHnd]", "NPC R Hand [RHnd]"] + nowhere)
    assert ph._hdt_xml_unresolvable_bones(xml, NIF_BONES) == sorted(nowhere)
    got = _unshippable(tmp_path, monkeypatch, xml)
    assert got is not None
    assert got["chains"] == ["SkirtF 4", "SkirtF 5"]
    assert sorted(got["bones"]) == sorted(nowhere)
    assert got["nowhere"] == 12


def test_three_missing_bones_do_not(tmp_path, monkeypatch):
    nowhere = _chain("SkirtF 4", 3)
    xml = _xml(NIF_BONES + nowhere)
    assert ph._hdt_xml_unresolvable_bones(xml, NIF_BONES) == nowhere
    assert [c.prefix for c in ph._hdt_xml_phantom_chains(xml, NIF_BONES)] == ["SkirtF 4"]
    assert _unshippable(tmp_path, monkeypatch, xml) is None


def test_tail_links_of_chains_the_mesh_has_are_not_a_phantom_chain(tmp_path, monkeypatch):
    """The SMP mod's own mesh: 16 chains of five links under an XML that
    declares six. Sixteen bones resolve nowhere and FSMP drops each harmlessly;
    the authored XML must ship."""
    have = [b for i in range(1, 17) for b in _chain(f"Skirt {i}", 5)]
    declared = [b for i in range(1, 17) for b in _chain(f"Skirt {i}", 6)]
    xml = _xml(declared)
    nif = _Nif(have)
    assert len(ph._hdt_xml_unresolvable_bones(xml, have)) == 16
    assert ph._hdt_xml_phantom_chains(xml, have) == []
    assert _unshippable(tmp_path, monkeypatch, xml, nif=nif, src=nif) is None


def test_scattered_bones_that_are_not_chains_do_not_count(tmp_path, monkeypatch):
    """A shared XML's stabiliser groups and one-off names: nowhere, not chains."""
    loose = [f"Stabilizer {c} 0{i}" for c in "CDEFGH" for i in (2, 3, 4)] + ["Ghost"]
    xml = _xml(NIF_BONES + loose)
    assert len(ph._hdt_xml_unresolvable_bones(xml, NIF_BONES)) == 19
    assert ph._hdt_xml_phantom_chains(xml, NIF_BONES) == []
    assert _unshippable(tmp_path, monkeypatch, xml) is None


def test_bones_on_the_skeleton_but_not_in_the_nif_are_not_missing():
    """A runtime-physics XML legitimately drives skeleton bones the armour is
    not skinned to -- the over-fire that got the 2026-07-28 re-check reverted."""
    on_skel = ["NPC L Thigh [LThg]", "NPC R Thigh [RThg]", "NPC L Hand [LHnd]",
               "NPC R Hand [RHnd]", "NPC Root [Root]", "NPC L Breast",
               "NPC R Breast", "NPC Pelvis [PElv]"]     # spacing / case differ
    xml = _xml(NIF_BONES + on_skel + ["NPC L Foot [Lft ]"])
    # The one bone in NEITHER: `NPC L Foot` is not on this fake skeleton.
    assert ph._hdt_xml_unresolvable_bones(xml, NIF_BONES) == ["NPC L Foot [Lft ]"]


def test_a_chain_the_source_nif_carries_is_not_phantom(tmp_path, monkeypatch):
    """A chain the source mesh has can come back through the framework
    re-import; only a chain no mesh ever had counts."""
    src_chain = _chain("Stabilizer 1", 6) + _chain("Stabilizer 2", 6)
    xml = _xml(NIF_BONES + src_chain)
    assert ph._hdt_xml_phantom_chains(xml, NIF_BONES, src_chain) == []
    assert _unshippable(tmp_path, monkeypatch, xml, src=_Nif(NIF_BONES + src_chain)) is None
    assert _unshippable(tmp_path, monkeypatch, xml) is not None, "control"


def test_a_node_counts_as_the_nif_having_the_link():
    """A chain's root link carries no skin weights; it is a NODE."""
    xml = _xml(_chain("SkirtF 4", 6) + _chain("SkirtF 5", 6))
    assert len(ph._hdt_xml_phantom_chains(xml, [])) == 2
    nif = _Nif([], nodes=["SkirtF 4_00", "SkirtF 5_00"])
    assert ph._hdt_xml_phantom_chains(xml, ph._nif_bone_and_node_names(nif)) == []


def test_constraint_only_links_are_seen():
    """bodyA/bodyB of a generic-constraint need not be declared."""
    links = _chain("SkirtF 4", 6)
    cons = "".join(f'<generic-constraint bodyA="{a}" bodyB="{b}"/>'
                   for a, b in zip(links, links[1:]))
    xml = f'<system><bone name="{links[0]}"/>{cons}</system>'
    assert [c.prefix for c in ph._hdt_xml_phantom_chains(xml, NIF_BONES)] == ["SkirtF 4"]
    assert ph._hdt_xml_unresolvable_bones(xml, NIF_BONES) == links


def test_without_a_skeleton_the_guard_does_not_judge(tmp_path, monkeypatch):
    _cs.patch(monkeypatch, "_actor_skeleton_bone_names", lambda: set())
    xml = _xml(_chain("SkirtF 4", 6) + _chain("SkirtF 5", 6))
    assert ph._hdt_xml_unresolvable_bones(xml, NIF_BONES) == []
    assert ph._hdt_xml_phantom_chains(xml, NIF_BONES) == []
    assert _unshippable(tmp_path, monkeypatch, xml) is None


# ---------------------------------------------------------- the finalize

from tests.synthetic_nif import (                 # noqa: E402
    build_skinned_shape_nif, pynifly_available)

PTR = "HDT Skinned Mesh Physics Object"
SIBLING_PTR = "Meshes\\!UBE\\brand\\piece\\skirt.xml"
PHANTOM = _chain("SkirtF 4", 6) + _chain("SkirtF 5", 6)


def _pointer(nif_path):
    nf = nc._pynifly().NifFile(filepath=str(nif_path))
    for ed in nf.rootNode.extra_data():
        if getattr(ed, "name", None) == PTR:
            return ed.string_data
    return None


def _piece(tmp_path, monkeypatch, authored_text):
    (tmp_path / "Src").mkdir()
    src = build_skinned_shape_nif(tmp_path / "Src" / "skirt_1.nif", name="Skirt")
    piece_dir = tmp_path / "Out" / "meshes" / "!UBE" / "brand" / "piece"
    piece_dir.mkdir(parents=True)
    dst = build_skinned_shape_nif(piece_dir / "skirt_1.nif", name="Skirt")
    (tmp_path / "Authored").mkdir()
    authored = tmp_path / "Authored" / "AuthoredSkirt.xml"
    authored.write_text(authored_text, encoding="utf-8")
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk", lambda p, nif=None: authored)
    _cs.patch(monkeypatch, "_find_cbbe_base_body", lambda *a, **k: None)
    _cs.patch(monkeypatch, "_find_ube_femalebody", lambda *a, **k: None)
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    return dst, src, piece_dir / "skirt.xml", authored


def _recorded(label):
    """The guard's line on this piece: an effect, never a pass failure."""
    assert not [e for e in nc._piece_pass_failures()
                if "hdt_xml_unresolvable" in e or label in e], (
        "a decline is the guard doing its job, not a failed pass")
    return [e for e in nc._piece_pass_effects()
            if e.startswith(f"CHANGED BY {label} ")]


@pytest.mark.skipif(not pynifly_available(), reason="pynifly native lib unavailable")
def test_the_finalize_does_not_ship_an_authored_xml_at_the_threshold(
        tmp_path, monkeypatch):
    """Two whole phantom chains, generator declines: the sibling is the
    generator's empty config, the NIF points at it, and the run summary names
    the guard."""
    dst, src, sibling, authored = _piece(tmp_path, monkeypatch, _xml(PHANTOM))
    _cs.patch(monkeypatch, "_generate_hdt_xml_for_dst", lambda *a, **k: None)
    assert ph._finalize_hdt_physics(dst, src) is True
    assert sibling.is_file()
    text = sibling.read_text(encoding="utf-8")
    assert "SkirtF 4_00" not in text, "the authored XML was shipped"
    assert "<system" in text and "Auto-generated" in text
    assert _pointer(dst) == SIBLING_PTR
    lines = _recorded(ph.HDT_XML_DECLINED_TAG)
    assert lines, "the guard left no line"
    assert "AuthoredSkirt.xml not shipped: 12 bone(s) in 2 whole chain(s)" in lines[0]
    assert "'SkirtF 4', 'SkirtF 5'" in lines[0]
    assert "ships with no physics" in lines[0]


@pytest.mark.skipif(not pynifly_available(), reason="pynifly native lib unavailable")
def test_the_finalize_prefers_the_regenerated_xml_when_the_generator_writes_one(
        tmp_path, monkeypatch):
    dst, src, sibling, authored = _piece(tmp_path, monkeypatch, _xml(PHANTOM))

    def _regen(dst_path, only_loose=False):
        sibling.write_text(_xml(["NPC Spine [Spn0]"]), encoding="utf-8")
        return SIBLING_PTR
    _cs.patch(monkeypatch, "_generate_hdt_xml_for_dst", _regen)
    assert ph._finalize_hdt_physics(dst, src) is True
    text = sibling.read_text(encoding="utf-8")
    assert "SkirtF" not in text and "NPC Spine [Spn0]" in text
    assert _pointer(dst) == SIBLING_PTR
    assert "physics regenerated" in _recorded(ph.HDT_XML_DECLINED_TAG)[0]


@pytest.mark.skipif(not pynifly_available(), reason="pynifly native lib unavailable")
def test_below_the_threshold_the_authored_xml_ships_as_before(tmp_path, monkeypatch):
    """The control: one three-link phantom chain is under the threshold."""
    dst, src, sibling, authored = _piece(tmp_path, monkeypatch, _xml(_chain("SkirtF 4", 3)))
    assert ph._finalize_hdt_physics(dst, src) is True
    assert "SkirtF 4_02" in sibling.read_text(encoding="utf-8")
    assert not _recorded(ph.HDT_XML_DECLINED_TAG)


@pytest.mark.skipif(not pynifly_available(), reason="pynifly native lib unavailable")
def test_tail_links_alone_ship_the_authored_xml(tmp_path, monkeypatch):
    """Sixteen missing sixth links on chains the source has: shipped."""
    declared = [b for i in range(1, 17) for b in _chain(f"Skirt {i}", 6)]
    have = [b for i in range(1, 17) for b in _chain(f"Skirt {i}", 5)]
    dst, src, sibling, authored = _piece(tmp_path, monkeypatch, _xml(declared))
    _cs.patch(monkeypatch, "_open_source_nif", lambda *_a, **_k: _Nif(have))
    assert ph._finalize_hdt_physics(dst, src) is True
    assert "Skirt 16_05" in sibling.read_text(encoding="utf-8")
    assert not _recorded(ph.HDT_XML_DECLINED_TAG)


# ------------------------------------------------ the run's warning (parent)

from src import auto_convert as ac                # noqa: E402


class _Res:
    def __init__(self, dst, reason):
        self.dst_path = Path(dst)
        self.reason = reason
        self.status = "converted (copy)"


def _declined(xml="SharedRig.xml"):
    return (f"CHANGED BY {ph.HDT_XML_DECLINED_TAG} ({xml} not shipped: 24 "
            f"bone(s) in 4 whole chain(s) ('SkirtF 4', 'SkirtF 5', 'SkirtF 6', "
            f"'SkirtF 7') no link of which exists in the converted NIF, its "
            f"source or the actor skeleton, 48 bones resolve nowhere in all -- "
            f"physics regenerated on the chain bones the piece has)")


def test_the_parent_reads_one_decline_per_piece_from_reason():
    """`_0` and `_1` share one XML: one piece. Other fragments are ignored."""
    res = [_Res("out/tassetcuirassf_0.nif", "a; " + _declined()),
           _Res("out/tassetcuirassf_1.nif", _declined() + "; CHANGED BY #other (x)"),
           _Res("out/cuirass_1.nif", _declined("OtherRig.xml")),
           _Res("out/boots_1.nif", "PASS FAILED something (RuntimeError: y)")]
    got = ac.authored_xml_declines(res)
    assert [p for p, _d in got] == ["tassetcuirassf", "cuirass"]
    assert got[0][1].startswith("SharedRig.xml not shipped: 24 bone(s) in 4 whole chain(s)")
    assert got[0][1].endswith("the piece has")
    assert got[1][1].startswith("OtherRig.xml not shipped")
    assert ac.count_pass_failures(res) == {"something": 1}, (
        "the decline must not read as a failed pass")


def test_a_decline_is_a_run_WARNING_naming_the_piece(monkeypatch, capsys):
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    res = [_Res("out/tassetcuirassf_0.nif", _declined()),
           _Res("out/tassetcuirassf_1.nif", _declined())]
    ac._report_authored_xml_declines("Some Mod", res)
    out = capsys.readouterr().out
    assert "1 piece(s) did not get their authored physics XML: tassetcuirassf" in out
    assert "tassetcuirassf: SharedRig.xml not shipped: 24 bone(s) in 4 whole chain(s)" in out
    assert len(ac._RUN_FAILURES) == 1
    e = ac._RUN_FAILURES[0]
    assert e["severity"] == "warning" and e["source"] == "Some Mod"
    assert e["kind"] == "authored physics XML not shipped"
    assert "tassetcuirassf" in e["detail"]
    assert ac._run_tally() == (0, 1)


def test_no_decline_no_warning(monkeypatch, capsys):
    monkeypatch.setattr(ac, "_RUN_FAILURES", [])
    ac._report_authored_xml_declines("Some Mod", [_Res("out/x_1.nif", "")])
    assert ac._RUN_FAILURES == [] and capsys.readouterr().out == ""
