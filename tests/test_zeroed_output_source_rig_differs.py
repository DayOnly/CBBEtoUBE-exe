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

"""#zos-rig-differs (GitHub #28): a source and its build that carry different physics rigs are named.

The rule's physics check is a presence test, so a source and a build that both name an
XML read as "physics unchanged" even when the build was rebuilt on another rig (an SMP
rebuild of a plain skirt: 81 custom nodes against 302 on the reported piece). The
decision does not change; what was missing is the saying. A piece that moves or stays
across a re-rig is named in the log and in the report.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import zeroed_body as zb  # noqa: E402
from tests.test_zeroed_output_source import BASE_MOD, OUT_MOD, STEM, Selection, _touch  # noqa: E402
from tests.test_zeroed_output_source_body_only_diff import (  # noqa: E402
    BODY_TEX, CLOTH_TEX, GARMENT, _owner, _pair)

PHYS = b"nif HDT Skinned Mesh Physics Object x.xml"


def _chain(prefix, n):
    return {f"{prefix} {i}" for i in range(n)}


# --- the comparison ---------------------------------------------------------------------

def test_a_re_rig_is_described_with_its_counts():
    old, new = _chain("FFront", 81) | _chain("Keep", 5), _chain("obiobiskirt", 302) | _chain("Keep", 5)
    text = discovery._zos_rig_text(old, new, set())
    assert text == "86 custom node(s) in today's source, 307 in the build, 5 shared"


def test_the_same_rig_is_not_a_re_rig():
    nodes = _chain("Skirt", 60)
    assert discovery._zos_rig_text(nodes, set(nodes), set()) == ""


def test_a_handful_of_different_nodes_is_not_a_re_rig():
    # nothing shared, so only the minimum count of differing nodes can say no
    assert discovery._zos_rig_text(_chain("A", 4), _chain("B", 4), set()) == ""


def test_a_mostly_shared_rig_is_not_a_re_rig_however_many_nodes_differ():
    base = _chain("Skirt", 80)
    assert discovery._zos_rig_text(base, base | _chain("Extra", 12), set()) == ""


def test_skeleton_nodes_and_nif_names_are_not_part_of_the_rig():
    skel = {f"npc bone {i}" for i in range(40)}
    a = {f"NPC Bone {i}" for i in range(40)} | {"x.nif"}
    b = {"Scene Root"} | _chain("Chain", 3)
    # only the chain and the root differ, and the skeleton and the .nif name drop out
    assert discovery._zos_rig_text(a, b, skel) == ""


def test_no_custom_nodes_at_all_is_nothing_to_say():
    assert discovery._zos_rig_text(set(), set(), set()) == ""


def test_an_unreadable_file_says_nothing(monkeypatch):
    def boom(p):
        raise RuntimeError("cannot open")
    monkeypatch.setattr(zb, "_nif_rig_nodes", boom)
    assert discovery._zos_rig_difference("a.nif", "b.nif") == ""


# --- through the rule ---------------------------------------------------------------------

def _physics_pair(tmp_path, monkeypatch, *, source, build, src_nodes, build_nodes,
                  both_physics=True, **tex):
    sel = _pair(tmp_path, monkeypatch, source=source, build=build, **tex)
    for w in ("_0", "_1"):
        _touch(sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif", PHYS)
        _touch(sel.mods / OUT_MOD / "meshes" / f"{STEM}{w}.nif", PHYS if both_physics else b"nif")
    calls = []

    def nodes(p):
        calls.append(str(p))
        return set(build_nodes if OUT_MOD.lower() in str(p).lower() else src_nodes)
    monkeypatch.setattr(zb, "_nif_rig_nodes", nodes)
    monkeypatch.setattr(discovery, "_zos_skeleton_names", lambda: set())
    return sel, calls


OLD_RIG, NEW_RIG = _chain("FFront", 81), _chain("obiobiskirt", 302)
BODY_ONLY = dict(source={**GARMENT, "VirtualBody": 10}, build={**GARMENT, "VirtualBody": 12})


def test_a_moved_piece_on_another_rig_is_named_in_the_log_and_the_report(tmp_path, monkeypatch, capsys):
    sel, _ = _physics_pair(tmp_path, monkeypatch, src_nodes=OLD_RIG, build_nodes=NEW_RIG, **BODY_ONLY)
    assert _owner(sel) == {OUT_MOD}
    err = capsys.readouterr().err
    expect = f"moved: 81 custom node(s) in today's source, 302 in the build, 0 shared"
    assert f"[zeroed-output-source] rig differs on meshes/{STEM} ({expect})" in err
    assert discovery.zeroed_output_source_report()["rig_differs"] == {STEM: expect}


def test_a_kept_piece_on_another_rig_is_named_too(tmp_path, monkeypatch, capsys):
    sel, _ = _physics_pair(
        tmp_path, monkeypatch, src_nodes=OLD_RIG, build_nodes=NEW_RIG,
        source={"Cuirass": 5, "Pants": 4}, build=GARMENT)
    assert _owner(sel) == {BASE_MOD}
    assert discovery.zeroed_output_source_report()["rig_differs"][STEM].startswith("kept: ")
    assert f"rig differs on meshes/{STEM} (kept: " in capsys.readouterr().err


def test_a_re_rig_does_not_change_the_decision(tmp_path, monkeypatch):
    # the same piece with the same rig on both sides moves exactly as it does with a re-rig
    sel, _ = _physics_pair(tmp_path, monkeypatch, src_nodes=OLD_RIG, build_nodes=OLD_RIG, **BODY_ONLY)
    same = _owner(sel)
    discovery._ZOS_LAST.clear()
    sel2, _ = _physics_pair(tmp_path / "b", monkeypatch, src_nodes=OLD_RIG, build_nodes=NEW_RIG, **BODY_ONLY)
    assert _owner(sel2) == same == {OUT_MOD}


def test_the_same_rig_is_not_listed(tmp_path, monkeypatch):
    sel, _ = _physics_pair(tmp_path, monkeypatch, src_nodes=OLD_RIG, build_nodes=OLD_RIG, **BODY_ONLY)
    assert _owner(sel) == {OUT_MOD}
    assert discovery.zeroed_output_source_report()["rig_differs"] == {}


def test_a_piece_with_no_physics_on_either_side_is_not_read_for_a_rig(tmp_path, monkeypatch):
    sel = _pair(tmp_path, monkeypatch, **BODY_ONLY)
    calls = []
    monkeypatch.setattr(zb, "_nif_rig_nodes", lambda p: calls.append(p) or set())
    assert _owner(sel) == {OUT_MOD}
    assert calls == []


def test_the_report_carries_an_empty_rig_list_when_nothing_differs(tmp_path, monkeypatch):
    sel = _pair(tmp_path, monkeypatch, source=GARMENT, build=GARMENT)
    _owner(sel)
    assert discovery.zeroed_output_source_report()["rig_differs"] == {}
