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

"""#dst-xml-no-stem-scan (2026-09-25): a question about a DESTINATION NIF's
physics never takes the filename fallback.

The bust-split callers stopped taking it on 2026-09-09 (#hdt-xml-race). Every
other destination query still did: the collider / soft-body sets the body-follow
weight passes skip, the drape-skip XML gate, the re-author's authored-skin set,
the bust-plate sync, the collider conform, the self-intersection overrides. For
a converted piece with NO physics pointer, the fallback globbed the output mod --
a tree the run is still writing and never cleans -- and took another garment's
same-stem XML. Which of the piece's shapes the passes then left alone depended on
write order and on what an earlier run left behind.

Two halves are pinned here with REAL NIFs and a real output tree:
  * a pointer-less destination next to another garment's same-stem XML protects
    NOTHING, and the answer no longer depends on whether that XML exists;
  * a destination's OWN pointer still resolves (and an unresolved one still
    recovers from the source-bound copy, not from a neighbour).
Then every destination pass is driven and asked HOW it looked physics up.
CBBE2UBE_NO_DST_XML_NO_STEM_SCAN=1 restores the fallback everywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import nif_convert as nc                 # noqa: E402
from src import nif_convert_physics as ph         # noqa: E402
from src import nif_convert_weights as nw         # noqa: E402
from tests import _converter_sources as _cs       # noqa: E402
from tests.synthetic_nif import (                 # noqa: E402
    build_skinned_shape_nif, pynifly_available)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")

SWITCH = "CBBE2UBE_NO_DST_XML_NO_STEM_SCAN"
PTR = "HDT Skinned Mesh Physics Object"
# Another garment's physics: it registers a shape that happens to share the
# piece's name, which is exactly how the live case bit.
NEIGHBOUR_XML = ('<system><per-triangle-shape name="Cloth"/>'
                 '<per-vertex-shape name="Cloth"/></system>')
OWN_XML = ('<system><per-triangle-shape name="Cloth"/>'
           '<bone name="NPC Spine [Spn0]"/></system>')


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """No memo, no bound source text and no switch may leak between tests."""
    monkeypatch.delenv(SWITCH, raising=False)
    nc._HDT_XML_INDEX_CACHE.clear()
    ph._hdt_xml_cache_clear()
    nc._PIECE_HDT_XML_TEXT = None
    yield
    nc._HDT_XML_INDEX_CACHE.clear()
    ph._hdt_xml_cache_clear()
    nc._PIECE_HDT_XML_TEXT = None


def _out_tree(tmp_path, *, neighbour: bool, pointer: "str | None" = None,
              own_xml: bool = False):
    """An OUTPUT mod: one converted piece (a real skinned NIF, shape `Cloth`),
    optionally another garment's same-stem XML in a different directory, and
    optionally a physics pointer / the piece's own XML. Returns the NIF path."""
    root = tmp_path / "OutputMod" / "meshes" / "!UBE"
    piece_dir = root / "brand" / "piece"
    piece_dir.mkdir(parents=True)
    nif = build_skinned_shape_nif(piece_dir / "cuirass_1.nif", name="Cloth")
    if neighbour:
        other = root / "someone" / "else"
        other.mkdir(parents=True)
        (other / "cuirass.xml").write_text(NEIGHBOUR_XML, encoding="utf-8")
    if own_xml:
        (piece_dir / "cuirass.xml").write_text(OWN_XML, encoding="utf-8")
    if pointer is not None:
        pyn = nc._pynifly()
        nf = pyn.NifFile(filepath=str(nif))
        from pyn.pynifly import NiStringExtraData  # type: ignore
        NiStringExtraData.New(nf, name=PTR, string_value=pointer,
                              parent=nf.rootNode)
        nf.save()
    return nif


def _dst_answer(nif):
    """What the destination-side passes see: the protection sets exactly as a
    pass asks for them, plus the drape gate's XML question."""
    nc._HDT_XML_INDEX_CACHE.clear()
    ph._hdt_xml_cache_clear()
    ss = nc._dst_xml_stem_scan()
    return (nc._hdt_collider_shape_names(nif, stem_scan=ss),
            nc._hdt_softbody_shape_names(nif, stem_scan=ss),
            nc._piece_has_hdt_xml(nif, stem_scan=ss))


# ------------------------------------------------------ the pointer-less piece

def test_a_pointerless_destination_next_to_a_same_stem_xml_protects_nothing(tmp_path):
    """THE FIX. Another garment's `cuirass.xml` sits in the output tree and names
    this piece's shape. The piece declares no physics, so nothing is protected."""
    nif = _out_tree(tmp_path, neighbour=True)
    assert _dst_answer(nif) == (set(), set(), False)


def test_switched_off_the_neighbour_xml_is_taken_again(tmp_path, monkeypatch):
    """The control that proves the fixture really triggers the race: with the
    switch set, the same piece takes the neighbour's shape names."""
    monkeypatch.setenv(SWITCH, "1")
    nif = _out_tree(tmp_path, neighbour=True)
    assert _dst_answer(nif) == ({"Cloth"}, {"Cloth"}, True)


def test_the_answer_no_longer_depends_on_what_else_is_in_the_tree(tmp_path):
    """Determinism: the same piece, with and without the neighbour written yet,
    gets one answer."""
    alone = _dst_answer(_out_tree(tmp_path / "a", neighbour=False))
    crowded = _dst_answer(_out_tree(tmp_path / "b", neighbour=True))
    assert alone == crowded == (set(), set(), False)


def test_the_source_side_still_takes_the_fallback(tmp_path):
    """Only the destination refuses it: a SOURCE query (the default) still finds
    an unpointed config by stem, as it did."""
    nif = _out_tree(tmp_path, neighbour=True)
    assert nc._hdt_collider_shape_names(nif) == {"Cloth"}


# ---------------------------------------------------------- its own pointer

def test_a_destinations_own_pointer_still_resolves(tmp_path):
    """The legitimate destination route is untouched: the piece's own pointer is
    read first and its XML answers, neighbour or not."""
    nif = _out_tree(tmp_path, neighbour=True, own_xml=True,
                    pointer=r"Meshes\!UBE\brand\piece\cuirass.xml")
    col, sb, has = _dst_answer(nif)
    assert col == {"Cloth"} and sb == set() and has is True


def test_an_unresolved_pointer_recovers_from_the_source_not_a_neighbour(tmp_path):
    """A declared pointer that does not resolve yet recovers from the copy bound
    off the SOURCE (#xml-source-of-truth), never from a neighbour's XML."""
    nif = _out_tree(tmp_path, neighbour=True,
                    pointer=r"Meshes\!UBE\brand\piece\cuirass.xml")
    nc._PIECE_HDT_XML_TEXT = '<system><per-triangle-shape name="Bound"/></system>'
    col, sb, has = _dst_answer(nif)
    assert col == {"Bound"} and sb == set() and has is True


# ------------------------------------------------ every destination pass asks

def _spy(monkeypatch):
    """Record the `stem_scan` every physics lookup is asked with; answer with
    'no physics' so each pass carries on as for a plain garment."""
    seen: list = []

    def _col(p, nif=None, stem_scan=True):
        seen.append(("collider", stem_scan))
        return set()

    def _sb(p, nif=None, stem_scan=True):
        seen.append(("softbody", stem_scan))
        return set()

    def _has(p, nif=None, stem_scan=True):
        seen.append(("has_xml", stem_scan))
        return False

    _cs.patch(monkeypatch, "_hdt_collider_shape_names", _col)
    _cs.patch(monkeypatch, "_hdt_softbody_shape_names", _sb)
    _cs.patch(monkeypatch, "_piece_has_hdt_xml", _has)
    return seen


def _stub_bodies(monkeypatch):
    """Just enough body reference for each pass to reach its physics lookup."""
    from scipy.spatial import cKDTree
    v = np.zeros((4, 3))
    _cs.patch(monkeypatch, "_body_conform_ref",
              lambda w: (v, np.zeros((4, 1)), ["NPC Spine [Spn0]"], cKDTree(v)))
    _cs.patch(monkeypatch, "_body_conform_normals", lambda w: None)
    _cs.patch(monkeypatch, "_body_leg_detail_ref", lambda w: ({}, True))
    _cs.patch(monkeypatch, "_body_jiggle_ref",
              lambda w: ({"NPC L Breast": None}, True))
    _cs.patch(monkeypatch, "_body_selfint_ref",
              lambda w: (v, np.zeros((4, 3)), cKDTree(v)))
    _cs.patch(monkeypatch, "_source_morph_tri_shape_names",
              lambda p: {"Robe"})
    monkeypatch.setattr(nc, "COLLIDER_SHRINKWRAP", True)
    monkeypatch.setattr(nc, "SELFINT_REPAIR", True)
    monkeypatch.setattr(nc, "LAYERED_CLOTH_BUTT_JIGGLE", True)


def _open(p):
    return nc._pynifly().NifFile(filepath=str(p))


_PASSES = {
    "conform-weights": lambda p: nw._conform_weights_core(_open(p), p, "_1"),
    "leg-bend": lambda p: nc._match_rigid_leg_bend_to_body(p, nc.BIPED_SLOT32_BIT),
    "limb-motion": lambda p: nc._match_limb_motion_to_body(p, nc.BIPED_SLOT32_BIT),
    "limb-tri-skip": lambda p: nw._limb_morph_tri_skip(p, _open(p), p, True, True),
    "coincident-skin": lambda p: nc._match_coincident_cross_shape_skin(p, src_nif_path=p),
    "layered-jiggle": lambda p: nw._layered_cloth_jiggle_regions(p, _open(p), {"Cloth"}),
    "jiggle-transfer": lambda p: nc._transfer_body_jiggle_to_fitted(p, nc.BIPED_SLOT32_BIT),
    "bust-plate-sync": lambda p: nc._sync_bust_plate_follow_postwrite(p),
    "collider-conform": lambda p: nc._conform_collider_to_body(p),
    "selfint": lambda p: nc._selfint_overrides(_open(p), p, p),
    "reauthor": lambda p: nc._reauthor_nif_fresh(p),
}


def _drive(tmp_path, monkeypatch, name):
    seen = _spy(monkeypatch)
    _stub_bodies(monkeypatch)
    nif = build_skinned_shape_nif(tmp_path / "piece_1.nif", name="Cloth")
    try:
        _PASSES[name](nif)
    except Exception:
        pass            # a stubbed body may stop the pass AFTER its lookup
    return seen


@pytest.mark.parametrize("name", sorted(_PASSES))
def test_every_destination_pass_refuses_the_filename_fallback(tmp_path, monkeypatch, name):
    seen = _drive(tmp_path, monkeypatch, name)
    assert seen, f"{name} never reached its physics lookup -- the stubs drifted"
    assert all(ss is False for _k, ss in seen), (name, seen)


@pytest.mark.parametrize("name", sorted(_PASSES))
def test_switched_off_every_destination_pass_takes_it_again(tmp_path, monkeypatch, name):
    monkeypatch.setenv(SWITCH, "1")
    seen = _drive(tmp_path, monkeypatch, name)
    assert seen and all(ss is True for _k, ss in seen), (name, seen)


def test_a_reauthor_reads_the_chain_bones_of_its_own_nif_without_the_fallback(
        tmp_path, monkeypatch):
    """The re-author rebuilds from the DESTINATION file itself, so the chain-anchor
    seed and the per-shape chain precreate read that file's physics too."""
    seen = []
    real = ph._read_source_hdt_xml_text

    def _rec(p, nif=None, stem_scan=True):
        seen.append(stem_scan)
        return real(p, nif=nif, stem_scan=stem_scan)

    _cs.patch(monkeypatch, "_read_source_hdt_xml_text", _rec)
    nif = _out_tree(tmp_path, neighbour=True)
    assert nc._reauthor_nif_fresh(nif)
    assert seen and all(ss is False for ss in seen), seen


def _record_reads(monkeypatch):
    seen = []
    real = ph._read_source_hdt_xml_text

    def _rec(p, nif=None, stem_scan=True):
        seen.append(stem_scan)
        return real(p, nif=nif, stem_scan=stem_scan)

    _cs.patch(monkeypatch, "_read_source_hdt_xml_text", _rec)
    return seen


def test_a_bust_split_clone_reads_its_chain_bones_without_the_fallback(
        tmp_path, monkeypatch):
    """The bust split clones a shape of the DESTINATION into itself; the clone's
    chain-bone read is a destination query too."""
    from src import nif_convert_bust as nb
    monkeypatch.setattr(nb, "_bust_split_candidates",
                        lambda dst, nf, src_path=None: ["Cloth"])
    monkeypatch.setattr(nc, "SPLIT_COL_DECLARED_BONES", False)
    seen = _record_reads(monkeypatch)
    nif = _out_tree(tmp_path, neighbour=True)
    nb._split_bust_collider_shape(nif)
    assert seen and all(ss is False for ss in seen), seen


def test_the_shape_copy_passes_the_choice_down_to_the_chain_read(
        tmp_path, monkeypatch):
    """`_copy_shape` -> `_install_skin` -> `_precreate_custom_bone_chains`: the
    caller's answer must reach the read, both ways."""
    nif = _out_tree(tmp_path, neighbour=True)
    for flag in (False, True):
        seen = _record_reads(monkeypatch)
        src = _open(nif)
        new = nc._pynifly().NifFile()
        new.initialize("SKYRIMSE", str(tmp_path / f"copy_{flag}.nif"))
        nc._copy_shape(src.shapes[0], new, xml_stem_scan=flag)
        assert seen and all(ss is flag for ss in seen), (flag, seen)
