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

"""[DESIGN: bust collider split] -- a bust garment that is ITS OWN per-triangle
collider can never carry jiggle: grafting onto it closes the cloth->collider
feedback loop that tore the breasts off in game (the torso-graft revert). The
split mirrors the arrangement well-behaved vanilla siblings author by hand: a
separate hidden collider clone keeps the garment's rigid weights, the physics
XML is repointed at the clone, and the garment becomes reachable by the stock
torso graft.

Validated end-to-end against the hand-built in-game-good artifact: production
output follows the bust at 0.660 vs the artifact's 0.643 (proximity follow
metric), with the already-split sibling and a non-collider garment as
untouched negative controls.

These tests pin the ORDER (the expensive lessons) and the gate structure:
  * pass 1 (shape) BEFORE _finalize_hdt_physics -- the clone must exist when
    the XML is hardened against the NIF, and the clone is added IN PLACE
    (a rebuild from shapes drops BODYTRI + the physics link);
  * pass 2 (XML) AFTER _finalize_hdt_physics -- which overwrites the on-disk
    XML with the authored copy, silently undoing any earlier rewrite -- and
    BEFORE _transfer_body_jiggle_to_fitted, which reads that XML to decide
    what is a collider;
  * detection is by MEASURED WEIGHT (follow ratio), never bone presence.
"""
import importlib
import inspect
import os
import re

import pytest

import src.nif_convert as nc


@pytest.fixture(autouse=True)
def _clean_module():
    yield
    for v in ("CBBE2UBE_TORSO_JIGGLE", "CBBE2UBE_NO_TORSO_JIGGLE",
              "CBBE2UBE_NO_BUST_COLLIDER_SPLIT"):
        os.environ.pop(v, None)
    importlib.reload(nc)


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(nc)


# --- flags -------------------------------------------------------------------

def test_split_and_graft_ship_on_together_and_die_together(monkeypatch):
    """The split's only purpose is to let the torso graft reach the garment; on
    its own it is output churn. One flag (TORSO_JIGGLE, default ON since 1.2)
    decides the whole fix; the split's own switch exists only for bisection."""
    m = _reload(monkeypatch, CBBE2UBE_TORSO_JIGGLE=None)
    assert m.BUST_COLLIDER_SPLIT is True
    assert m.TORSO_JIGGLE_TRANSFER is True
    m2 = _reload(monkeypatch, CBBE2UBE_TORSO_JIGGLE="0")
    assert m2.TORSO_JIGGLE_TRANSFER is False
    assert m2._bust_split_candidates("x_1.nif", None) == []


def test_kill_switch(monkeypatch):
    m = _reload(monkeypatch, CBBE2UBE_TORSO_JIGGLE=None,
                CBBE2UBE_NO_BUST_COLLIDER_SPLIT="1")
    assert m.BUST_COLLIDER_SPLIT is False
    assert m._bust_split_candidates("x_1.nif", None) == []


def test_xml_pass_is_gated_like_the_shape_pass():
    src = inspect.getsource(nc._split_bust_collider_xml)
    for flag in ("BUST_COLLIDER_SPLIT", "TORSO_JIGGLE_TRANSFER",
                 "TRANSFER_BODY_JIGGLE"):
        assert flag in src, f"XML pass must gate on {flag}"


# --- the ordering lessons ----------------------------------------------------

def test_both_call_sites_order_shape_finalize_xml_graft():
    """BODYTRI -> _finalize_hdt_physics -> XML rewrite, at BOTH pipeline sites.
    Finalize re-saves the NIF and OVERWRITES the XML with the authored copy: a
    clone added after it is at risk, an XML rewrite before it is silently
    undone -- undoing it restored the exact configuration that tore breasts
    off. And the graft must run last of the four, reading the rewritten XML."""
    src = inspect.getsource(nc)
    shape_calls = [m.start() for m in re.finditer(
        r"(?<!def )_split_bust_collider_shape\(dst_path", src)]
    xml_calls = [m.start() for m in re.finditer(
        r"(?<!def )_split_bust_collider_xml\(dst_path\)", src)]
    fin_calls = [m.start() for m in re.finditer(
        r"(?<!def )_finalize_hdt_physics\(dst_path", src)]
    graft_calls = [m.start() for m in re.finditer(
        r"(?<!def )_transfer_body_jiggle_to_fitted\(dst_path", src)]
    assert len(shape_calls) == 1 and len(xml_calls) == 1
    for sc, xc in zip(shape_calls, xml_calls):
        fins = [f for f in fin_calls if sc < f < xc]
        assert fins, "pass 1 must run BEFORE finalize, pass 2 AFTER it"
        grafts = [g for g in graft_calls if g > xc]
        assert grafts and min(grafts) - xc < 2000, (
            "the jiggle graft must follow the XML rewrite at the same site")


def test_shape_pass_adds_in_place_never_rebuilds():
    """Rebuilding a NIF from its shapes drops ALL extra data -- BODYTRI loss
    shows in game as 'ignores morphs, body reverts to its _0 version'."""
    src = inspect.getsource(nc._split_bust_collider_shape)
    assert "initialize(" not in src, "must not author a fresh NIF"
    assert "_reauthor" not in src, "must not reauthor"
    assert "atomic_nif_save(nf, dst_path)" in src


def test_shape_pass_snapshots_shape_level_extra_data_and_restores():
    """BODYTRI lives on its CARRIER SHAPE, not the root (verified against the
    hand-built artifact). The invariant snapshot must cover both, and any loss
    must restore the original bytes rather than ship a morph-dead NIF."""
    src = inspect.getsource(nc._split_bust_collider_shape)
    assert "for s_ in nf_.shapes" in src, "snapshot must include shape extra"
    assert "atomic_write_bytes(dst_path, backup)" in src, "must restore on loss"
    i = src.index("atomic_write_bytes(dst_path, backup)")
    assert "if not ok:" in src[:i], "restore must be the failure branch"


def test_xml_pass_gates_the_three_invariants_together():
    """split / morph / physics TOGETHER, never one at a time -- checking only
    the .xml reference passed while the morph link was missing."""
    src = inspect.getsource(nc._split_bust_collider_xml)
    assert '"HDT Skinned Mesh Physics Object" not in extra' in src
    assert "col not in shapes or name not in shapes" in src


def test_xml_pass_is_byte_preserving():
    """errors='replace' manufactures U+FFFD mojibake on re-encode; latin-1
    round-trips every byte and the rewritten names are ASCII."""
    src = inspect.getsource(nc._split_bust_collider_xml)
    assert 'errors="replace"' not in src
    assert '"latin-1"' in src


# --- detection: the class property -------------------------------------------

def test_detection_measures_weight_not_bone_presence():
    """Bone PRESENCE is not follow -- a garment can carry all six breast bones
    at 0.15 of the body's drive and still fail in game. The gate must be the
    follow RATIO against the body underneath."""
    src = inspect.getsource(nc._bust_split_candidates)
    assert "_BREAST_BONE_RE" in src
    assert "_BUST_SPLIT_FOLLOW_FLOOR" in src
    assert "bone_names" not in src, "no bone-presence tests in detection"


def test_detection_excludes_bodies_helpers_and_foreign_xml_structures():
    src = inspect.getsource(nc._bust_split_candidates)
    assert "_is_inline_body_name(name)" in src, "never the body"
    assert "0x1" in src, "Hidden shapes are collider helpers, not garments"
    assert "s.textures" in src, "textureless shapes are not rendered garments"
    # a name referenced beyond its per-triangle decl (constraints, pairs) is a
    # structure the fix was never validated on
    assert 'txt.count(f\'"{name}"\') != decl_uses' in src


def test_follow_floor_sits_below_the_validated_anchor():
    """The hand-built in-game-good artifact follows at 0.643; a garment already
    following at that level must never be re-split. Hide pre-fix reads 0.0."""
    assert 0.0 < nc._BUST_SPLIT_FOLLOW_FLOOR < 0.643


# --- functional: the XML rewrite ---------------------------------------------

class _ED:
    def __init__(self, name):
        self.name = name


class _Shape:
    def __init__(self, name):
        self.name = name

    def extra_data(self):
        return []


class _Root:
    def __init__(self, eds):
        self._eds = eds

    def extra_data(self):
        return self._eds


class _Nif:
    def __init__(self, shapes, eds):
        self.shapes = [_Shape(n) for n in shapes]
        self.rootNode = _Root([_ED(n) for n in eds])


def _fake_pyn(nif):
    class _P:
        @staticmethod
        def NifFile(filepath=None):
            return nif
    return _P


def test_xml_rewrite_repoints_only_cloned_garments(tmp_path, monkeypatch):
    m = _reload(monkeypatch, CBBE2UBE_TORSO_JIGGLE="1")
    xml = tmp_path / "piece.xml"
    xml.write_text(
        '<per-triangle-shape name="Garment">\n'
        '<per-triangle-shape name="LegHelper">\n', encoding="utf-8")
    nif = _Nif(["Garment", "GarmentCol", "LegHelper", "BaseShape"],
               ["HDT Skinned Mesh Physics Object"])
    monkeypatch.setattr(m, "_pynifly", lambda: _fake_pyn(nif))
    n = m._split_bust_collider_xml(tmp_path / "piece_1.nif")
    out = xml.read_text(encoding="utf-8")
    assert n == 1
    assert '<per-triangle-shape name="GarmentCol">' in out
    assert '<per-triangle-shape name="LegHelper">' in out, (
        "a collider with no clone must keep its decl")
    assert 'name="Garment">' not in out


def test_xml_rewrite_refuses_without_the_physics_link(tmp_path, monkeypatch):
    m = _reload(monkeypatch, CBBE2UBE_TORSO_JIGGLE="1")
    xml = tmp_path / "piece.xml"
    before = '<per-triangle-shape name="Garment">\n'
    xml.write_text(before, encoding="utf-8")
    nif = _Nif(["Garment", "GarmentCol"], [])          # physics link MISSING
    monkeypatch.setattr(m, "_pynifly", lambda: _fake_pyn(nif))
    assert m._split_bust_collider_xml(tmp_path / "piece_1.nif") == 0
    assert xml.read_text(encoding="utf-8") == before
