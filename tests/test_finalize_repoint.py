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

"""#finalize-repoint (2026-09-25): the physics finalize owns the NIF's physics
pointer, and phase 2 gives a hand/foot piece none.

`_finalize_hdt_physics` copies the authored XML to `<stem>.xml` beside the NIF,
and every later XML pass edits THAT copy. It only ever ADDED the pointer, so one
an earlier step had set -- a keyword / stem match naming the SOURCE mod's file,
or the author's own string kept by a verbatim copy -- survived, and FSMP loaded
the untouched author file. Pinned here with REAL NIFs:
  * a pointer naming another file is repointed at the sibling this call wrote,
    and the sibling is the file carrying the finalize's edits;
  * a pointer already naming the sibling (any case) is left byte-identical;
  * a sibling NOT written by this call (no source XML, or a failed copy) never
    captures the pointer;
  * phase 2 attaches no physics pointer, found or generated, to a slot-33/37
    piece -- the gate phase 1 always had.
CBBE2UBE_NO_FINALIZE_REPOINT=1 restores both old behaviours.
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
from tests.synthetic_nif import (                 # noqa: E402
    build_skinned_shape_nif, build_skinned_shapes_nif, pynifly_available,
    uv_sphere)

pytestmark = pytest.mark.skipif(not pynifly_available(),
                                reason="pynifly native lib unavailable")

SWITCH = "CBBE2UBE_NO_FINALIZE_REPOINT"
PTR = "HDT Skinned Mesh Physics Object"
SOURCE_PTR = "Meshes\\brand\\piece\\AuthoredSkirt.xml"
SIBLING_PTR = "Meshes\\!UBE\\brand\\piece\\skirt.xml"
# The authored XML names a collider the converted mesh does not have, so the
# FSMP hardening prunes it: that pruning is the finalize edit that must reach
# the file the NIF points at.
AUTHORED = ('<?xml version="1.0"?>\n<system>\n'
            '\t<per-vertex-shape name="Skirt">\n\t\t<tag>Fabric</tag>\n'
            '\t</per-vertex-shape>\n'
            '\t<per-triangle-shape name="GoneCollider">\n\t\t<tag>Col</tag>\n'
            '\t</per-triangle-shape>\n</system>\n')


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.delenv("CBBE2UBE_MO2_INI", raising=False)
    # No actor skeleton (bone pruning stays off) and no body pair to warp with.
    monkeypatch.setattr(nc, "_SKELETON_BONES_CACHE", set())
    _cs.patch(monkeypatch, "_find_cbbe_base_body", lambda *a, **k: None)
    _cs.patch(monkeypatch, "_find_ube_femalebody", lambda *a, **k: None)
    tel._begin_piece_pass_log()
    yield
    tel._begin_piece_pass_log()


def _pointer(nif_path) -> "str | None":
    nf = nc._pynifly().NifFile(filepath=str(nif_path))
    for ed in nf.rootNode.extra_data():
        if getattr(ed, "name", None) == PTR:
            return ed.string_data
    return None


def _set_pointer(nif_path, value: str) -> None:
    pyn = nc._pynifly()
    nf = pyn.NifFile(filepath=str(nif_path))
    from pyn.pynifly import NiStringExtraData  # type: ignore
    NiStringExtraData.New(nf, name=PTR, string_value=value, parent=nf.rootNode)
    nf.save()


def _piece(tmp_path, monkeypatch, *, pointer: "str | None",
           source_xml: bool = True, stale_sibling: bool = False):
    """An output piece `skirt_1.nif` (shape `Skirt`) with an optional pointer,
    its source NIF, and -- when `source_xml` -- an authored XML the source
    resolves to. Returns (dst, src, sibling)."""
    (tmp_path / "Src").mkdir()
    src = build_skinned_shape_nif(tmp_path / "Src" / "skirt_1.nif", name="Skirt")
    piece_dir = tmp_path / "Out" / "meshes" / "!UBE" / "brand" / "piece"
    piece_dir.mkdir(parents=True)
    dst = build_skinned_shape_nif(piece_dir / "skirt_1.nif", name="Skirt")
    if pointer is not None:
        _set_pointer(dst, pointer)
    sibling = piece_dir / "skirt.xml"
    if stale_sibling:
        sibling.write_text("<system></system>\n", encoding="utf-8")
    authored = None
    if source_xml:
        (tmp_path / "Authored").mkdir()
        authored = tmp_path / "Authored" / "AuthoredSkirt.xml"
        authored.write_text(AUTHORED, encoding="utf-8")
    _cs.patch(monkeypatch, "_read_source_hdt_xml_disk",
              lambda p, nif=None: authored)
    return dst, src, sibling


# ------------------------------------------------------------ the repoint

def test_a_pointer_set_before_the_finalize_is_repointed_at_the_sibling(
        tmp_path, monkeypatch):
    dst, src, sibling = _piece(tmp_path, monkeypatch, pointer=SOURCE_PTR)
    assert ph._finalize_hdt_physics(dst, src) is True
    assert _pointer(dst) == SIBLING_PTR
    # ...and the file it now names is the one the finalize edited.
    text = sibling.read_text(encoding="utf-8")
    assert 'name="Skirt"' in text
    assert "GoneCollider" not in text
    assert any("#finalize-repoint" in e for e in tel._piece_pass_effects())


def test_the_rewritten_header_stays_consistent(tmp_path, monkeypatch):
    """The pointer lives in the header string table; the entry is rewritten in
    place, so its length prefix and the table's max-length field must match."""
    import struct
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer="Meshes\\x.xml")
    ph._finalize_hdt_physics(dst, src)
    data = dst.read_bytes()
    maxlen_at, entries = ph._nif_header_string_table(data)
    assert SIBLING_PTR.encode() in [s for _o, s in entries]
    assert struct.unpack_from("<I", data, maxlen_at)[0] == max(
        len(s) for _o, s in entries)
    assert _pointer(dst) == SIBLING_PTR


def test_a_string_another_block_shares_is_never_rewritten(tmp_path, monkeypatch):
    """If the pointer's header entry is also some other block's string, the
    rewrite would change that block too: the re-read catches it, the original
    bytes go back, and the refusal is recorded."""
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer=SOURCE_PTR)
    pyn = nc._pynifly()
    nf = pyn.NifFile(filepath=str(dst))
    from pyn.pynifly import NiStringExtraData  # type: ignore
    NiStringExtraData.New(nf, name="Other", string_value=SOURCE_PTR,
                          parent=nf.rootNode)
    nf.save()
    ph._finalize_hdt_physics(dst, src)
    assert _pointer(dst) == SOURCE_PTR
    assert any("repoint" in f for f in tel._piece_pass_failures())
    assert not any("#finalize-repoint" in e for e in tel._piece_pass_effects())


def test_a_pointer_already_naming_the_sibling_is_left_byte_identical(
        tmp_path, monkeypatch):
    same = "meshes/!ube/Brand/Piece/SKIRT.xml"     # case and slashes differ
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer=same)
    before = dst.read_bytes()
    assert ph._finalize_hdt_physics(dst, src) is True
    assert _pointer(dst) == same
    assert dst.read_bytes() == before
    assert not any("#finalize-repoint" in e for e in tel._piece_pass_effects())


def test_a_piece_without_a_pointer_still_gets_the_sibling(tmp_path, monkeypatch):
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer=None)
    assert ph._finalize_hdt_physics(dst, src) is True
    assert _pointer(dst) == SIBLING_PTR


def test_a_sibling_this_call_did_not_write_never_captures_the_pointer(
        tmp_path, monkeypatch):
    """No authored source XML: the `<stem>.xml` on disk is from somewhere else
    (a generator, an earlier run), so the pointer is not this call's to move."""
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer=SOURCE_PTR,
                                source_xml=False, stale_sibling=True)
    ph._finalize_hdt_physics(dst, src)
    assert _pointer(dst) == SOURCE_PTR


def test_a_failed_copy_never_captures_the_pointer(tmp_path, monkeypatch):
    dst, src, _sibling = _piece(tmp_path, monkeypatch, pointer=SOURCE_PTR,
                                stale_sibling=True)

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ph, "atomic_copy", _boom)
    ph._finalize_hdt_physics(dst, src)
    assert _pointer(dst) == SOURCE_PTR


def test_switched_off_the_pointer_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    dst, src, sibling = _piece(tmp_path, monkeypatch, pointer=SOURCE_PTR)
    assert ph._finalize_hdt_physics(dst, src) is True
    assert _pointer(dst) == SOURCE_PTR
    assert sibling.is_file()                 # the copy itself is unchanged


# ------------------------------------------------------ the phase-2 gate

def _phase2(tmp_path, monkeypatch, slots: int, *, found: bool):
    """Run the real body-swap path on a two-shape source (a garment and an
    inline body). The XML finder and the generator are observed, not run."""
    v, t, n = uv_sphere(5.0)
    src = build_skinned_shapes_nif(tmp_path / "Src" / "meshes" / "a" / "wear_1.nif",
                                   [("Wear", v, t, n), ("3BA", v, t, n)])
    ref = build_skinned_shape_nif(tmp_path / "ref.nif", name="BaseShape")
    dst = tmp_path / "Out" / "meshes" / "a" / "wear_1.nif"
    dst.parent.mkdir(parents=True)
    generated = []
    _cs.patch(monkeypatch, "_find_hdt_xml_for_armor",
              lambda p, *a, **k: SOURCE_PTR if found else None)
    _cs.patch(monkeypatch, "_source_hdt_needs_missing_chain_bones",
              lambda *a, **k: False)

    def _gen(p, *a, **k):
        generated.append(p)
        return None
    _cs.patch(monkeypatch, "_generate_hdt_xml_for_dst", _gen)
    nc.convert_nif_phase2(src, dst, ube_body_ref_path=ref, biped_slots=slots,
                          fit_armor=False, reskin_armor=False,
                          auto_gen_tri=False, inject_baseshape=False)
    return _pointer(dst), generated


@pytest.mark.parametrize("slots", [nc.BIPED_SLOT33_BIT, nc.BIPED_SLOT37_BIT])
def test_phase2_attaches_no_found_pointer_to_a_hand_or_foot_piece(
        tmp_path, monkeypatch, slots):
    ptr, _gen = _phase2(tmp_path, monkeypatch, slots, found=True)
    assert ptr is None


def test_phase2_still_attaches_a_found_pointer_to_a_body_piece(
        tmp_path, monkeypatch):
    ptr, _gen = _phase2(tmp_path, monkeypatch, nc.BIPED_SLOT32_BIT, found=True)
    assert ptr == SOURCE_PTR


def test_phase2_generates_no_physics_for_a_hand_piece(tmp_path, monkeypatch):
    _ptr, gen = _phase2(tmp_path, monkeypatch, nc.BIPED_SLOT33_BIT, found=False)
    assert gen == []


def test_phase2_still_generates_for_a_body_piece(tmp_path, monkeypatch):
    _ptr, gen = _phase2(tmp_path, monkeypatch, nc.BIPED_SLOT32_BIT, found=False)
    assert len(gen) == 1


def test_switched_off_phase2_attaches_it_again(tmp_path, monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    ptr, _gen = _phase2(tmp_path, monkeypatch, nc.BIPED_SLOT33_BIT, found=True)
    assert ptr == SOURCE_PTR
