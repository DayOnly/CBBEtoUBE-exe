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

"""The physics census reads exactly what FSMP reads.

Five differences made its numbers unquotable: it borrowed another garment's XML
by filename when the NIF had no pointer of its own; it read the XML as locale
text, so a UTF-8 byte-order mark made the file "unparseable"; a default
`xmlns` on <system> did the same; it treated one side naming a body tag as
enough to collide, where FSMP needs both sides to allow it and reads an empty
can-collide list as "everything"; and only a generic constraint counted as a
constraint. Each is pinned here on a small fixture."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

import pytest                                                    # noqa: E402
from scripts.analysis import physics_cloth_health as pch         # noqa: E402
from src import nif_convert                                      # noqa: E402

MARK = b"HDT Skinned Mesh Physics Object"

CLOTH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<system>
  <per-vertex-shape name="Skirt">
    <tag>cloth</tag>
    <can-collide-with-tag>body</can-collide-with-tag>
  </per-vertex-shape>
  <per-triangle-shape name="BodyCol">
    <tag>body</tag>
  </per-triangle-shape>
  <generic-constraint bodyA="a" bodyB="b"/>
</system>
"""


class _Ed:
    def __init__(self, name, value):
        self.name = name
        self.string_data = value


class _Root:
    def __init__(self, eds):
        self._eds = eds

    def extra_data(self):
        return iter(self._eds)


class _Shape:
    def __init__(self, name):
        self.name = name
        self.verts = []
        self.tris = []


class _Nif:
    def __init__(self, pointer=None, shapes=("Skirt", "BodyCol")):
        eds = [_Ed("HDT Skinned Mesh Physics Object", pointer)] if pointer else []
        self.rootNode = _Root(eds)
        self.shapes = [_Shape(n) for n in shapes]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_NO_PHYSICS_DATA_PREFIX", raising=False)
    nif_convert._VFS_DATA_REL_MEMO.clear()
    yield
    nif_convert._VFS_DATA_REL_MEMO.clear()


def _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML, xml_rel="armor/x/phys.xml"):
    """One mod: meshes/armor/x/cuirass_0.nif and an XML at `xml_rel`."""
    mods = tmp_path / "mods"
    nif = mods / "Mod" / "meshes" / "armor" / "x" / "cuirass_0.nif"
    nif.parent.mkdir(parents=True)
    nif.write_bytes(b"\x00" + MARK + b"\x00")
    xml = mods / "Mod" / "meshes" / xml_rel
    xml.parent.mkdir(parents=True, exist_ok=True)
    xml.write_bytes(xml_bytes)
    monkeypatch.setattr(nif_convert._paths, "mods_root", lambda: mods)
    return nif, xml


POINTER = "meshes\\armor\\x\\phys.xml"


# --------------------------------------------- the NIF's own pointer, only

def test_a_nif_without_its_own_pointer_has_no_physics(tmp_path, monkeypatch):
    """An XML with the NIF's stem sits right beside it. FSMP never reads it:
    no pointer, no physics. The old census counted 58 such NIFs."""
    nif, _xml = _mod(tmp_path, monkeypatch, xml_rel="armor/x/cuirass.xml")
    row, reason = pch.classify(nif, _Nif(pointer=None))
    assert row is None
    assert reason == pch.NO_POINTER


def test_the_pointer_is_followed_not_the_filename(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    same_stem = nif.parent / "cuirass.xml"
    same_stem.write_bytes(b"<system/>")          # no cloth: must not be read
    row, reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert reason is None
    assert [c["name"] for c in row["cloth"]] == ["Skirt"]


def test_a_data_prefixed_pointer_resolves(tmp_path, monkeypatch):
    """The converter's #physics-data-prefix handling, not a private copy."""
    nif, _xml = _mod(tmp_path, monkeypatch)
    row, reason = pch.classify(nif, _Nif(pointer="Data\\" + POINTER))
    assert reason is None and row is not None


def test_a_pointer_to_nothing_is_unresolved_not_absent(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    row, reason = pch.classify(nif, _Nif(pointer="meshes\\nowhere\\x.xml"))
    assert row is None
    assert reason == pch.UNRESOLVED


def test_the_name_is_matched_without_case():
    nif = _Nif()
    nif.rootNode = _Root([_Ed("hdt skinned mesh physics object", "a.xml")])
    assert pch.physics_pointer(nif) == "a.xml"


def test_a_name_in_the_file_but_no_root_pointer_is_its_own_bucket(
        tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    monkeypatch.setattr(pch, "_open_nif", lambda p: _Nif(pointer=None))
    rows, skip, _g, _n = pch.scan([nif], tmp_path)
    assert rows == []
    assert skip == Counter({pch.MARKER_ONLY: 1})


def test_a_file_without_the_name_is_never_opened(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    nif.write_bytes(b"\x00no physics here\x00")

    def _boom(p):
        raise AssertionError("opened a NIF that cannot carry a pointer")
    monkeypatch.setattr(pch, "_open_nif", _boom)
    rows, skip, _g, _n = pch.scan([nif], tmp_path)
    assert rows == [] and skip == Counter({pch.NO_POINTER: 1})


# --------------------------------------------------- the XML, as bytes

def test_a_bom_xml_parses(tmp_path, monkeypatch):
    """11 authored XMLs start with a UTF-8 byte-order mark. Read as locale
    text the mark became junk before the root and the file 'unparseable'."""
    nif, _xml = _mod(tmp_path, monkeypatch,
                     xml_bytes=b"\xef\xbb\xbf" + CLOTH_XML)
    row, reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert reason is None and row is not None


def test_junk_after_the_root_is_ignored_and_counted(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch,
                     xml_bytes=CLOTH_XML + b"undefined</xml>")
    notes = Counter()
    row, reason = pch.classify(nif, _Nif(pointer=POINTER), notes)
    assert reason is None and row is not None
    assert notes[pch.REPAIRED] == 1


def test_a_default_namespace_is_invisible_to_fsmp(tmp_path, monkeypatch):
    """A validator schema's xmlns on <system>: FSMP matches element names as
    written; ElementTree would make every one `{uri}name`."""
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b"<system>", b'<system xmlns="urn:validator">'))
    row, reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert reason is None
    assert [c["name"] for c in row["cloth"]] == ["Skirt"]
    assert [k["name"] for k in row["colliders"]] == ["BodyCol"]


def test_a_root_other_than_system_loads_nothing(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b"system>", b"physics>"))
    row, reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row is None
    assert reason == pch.NOT_SYSTEM


def test_cloth_named_but_absent_from_the_nif_is_dead(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    row, reason = pch.classify(nif, _Nif(pointer=POINTER, shapes=("BodyCol",)))
    assert row is None
    assert reason == pch.CLOTH_ABSENT


# ------------------------------------------------- FSMP's collision rule

def _s(tags=(), can=(), no=()):
    return {"name": "s", "tags": set(tags), "can": set(can), "no": set(no)}


def test_an_empty_can_collide_list_means_everything():
    cloth = _s(tags=["cloth"])
    col = _s(tags=["body"])
    assert pch.collides(cloth, col)


def test_an_empty_list_still_honours_no_collide():
    cloth = _s(tags=["cloth"], no=["body"])
    col = _s(tags=["body"])
    assert not pch.collides(cloth, col)


def test_both_sides_must_allow_it():
    """The cloth names the collider's tag; the collider refuses the cloth."""
    cloth = _s(tags=["cloth"], can=["body"])
    col = _s(tags=["body"], can=["skirt"])
    assert pch.allows(cloth, col)
    assert not pch.collides(cloth, col)


def test_tags_compare_without_case(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b"<can-collide-with-tag>body<", b"<can-collide-with-tag>Body<"))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert pch.cloth_reach(row) == {"Skirt": (True, True)}


def test_every_constraint_kind_counts(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b"generic-constraint", b"stiffspring-constraint"))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is True


def test_no_constraint_of_any_kind_is_unconstrained(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b'  <generic-constraint bodyA="a" bodyB="b"/>\n', b""))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is False


# ---------------------------------------------------------------- report

def _row(path, *, constrained=True, cloth_can=("body",), col_tags=("body",),
         named=1):
    cloth = {"name": "Skirt", "tags": {"cloth"}, "can": set(cloth_can),
             "no": set()}
    cols = [{"name": "Col", "tags": set(col_tags), "can": set(), "no": set()}]
    return {"path": path, "garment": pch.garment_of(path),
            "constrained": constrained, "cloth": [cloth],
            "colliders_named": named, "colliders": cols, "pen": None}


def _fault(out, label):
    line = next(ln for ln in out.splitlines() if label in ln)
    return line.split(":", 1)[1].split()[0]


def test_unconstrained_cloth_that_reaches_nothing_is_not_the_crash_pair(capsys):
    rows = [_row("a/skirt_0.nif", constrained=False, cloth_can=("ground",))]
    assert pch.report(rows, Counter(), {}, 1) == 0
    assert _fault(capsys.readouterr().out, "unconstrained collision pair") == "0"


def test_unconstrained_cloth_that_reaches_a_collider_is(capsys):
    rows = [_row("a/skirt_0.nif", constrained=False)]
    pch.report(rows, Counter(), {}, 1)
    assert _fault(capsys.readouterr().out, "unconstrained collision pair") == "1"


def test_a_collider_named_but_missing_is_counted(capsys):
    rows = [_row("a/skirt_0.nif", named=2)]
    pch.report(rows, Counter(), {}, 1)
    assert _fault(capsys.readouterr().out,
                  "collider named but ABSENT") == "1"


def test_both_weight_halves_are_one_garment(capsys):
    rows = [_row("a/skirt_0.nif", cloth_can=("ground",)),
            _row("a/skirt_1.nif", cloth_can=("ground",))]
    pch.report(rows, Counter(), {}, 2)
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if "reaching NO collider" in ln)
    assert line.split(":", 1)[1].split()[:3] == ["2", "/", "1"]


def test_an_empty_population_exits_3():
    with pytest.raises(SystemExit) as ex:
        pch.report([], Counter({pch.NO_POINTER: 5}), {}, 5)
    assert ex.value.code == 3
