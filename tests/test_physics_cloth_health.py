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

"""The physics census models what FSMP reads; these pin the modelled rules.

Five differences made its numbers unquotable: it borrowed another garment's XML
by filename when the NIF had no pointer of its own; it read the XML as locale
text, so a UTF-8 byte-order mark made the file "unparseable"; a default
`xmlns` on <system> did the same; it treated one side naming a body tag as
enough to collide, where FSMP needs both sides to allow it and reads an empty
can-collide list as "everything"; and only a generic constraint counted as a
constraint. Each is pinned here on a small fixture.

What simulates is decided the way FSMP decides it, by bone mass and not by
element kind: a kinematic helper is a collider whatever its kind, a dynamic
per-triangle shape is cloth, a per-vertex partner counts, two kinematic shapes
never pair, and an XML the run cannot read is counted, not fatal."""
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
  <bone name="NPC Pelvis"/>
  <bone name="Skirt 1"><mass>1.5</mass></bone>
  <per-vertex-shape name="Skirt">
    <tag>cloth</tag>
    <can-collide-with-tag>body</can-collide-with-tag>
  </per-vertex-shape>
  <per-triangle-shape name="BodyCol">
    <tag>body</tag>
  </per-triangle-shape>
  <generic-constraint bodyA="Skirt 1" bodyB="NPC Pelvis"/>
</system>
"""

# Skin bones of each fixture shape: the skirt hangs off a dynamic chain bone,
# every helper sits on body bones only.
SKIN = {"Skirt": ("Skirt 1", "NPC Pelvis"), "Cape": ("Skirt 1",),
        "BodyCol": ("NPC Pelvis",), "VirtualBody": ("NPC Pelvis",),
        "Ground": ("NPC Pelvis",)}


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
        self.bone_names = list(SKIN.get(name, ("NPC Pelvis",)))
        self.verts = [(0.0, 0.0, 0.0)]
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
    assert [k["name"] for k in row["shapes"]] == ["Skirt", "BodyCol"]


def test_a_root_other_than_system_loads_nothing(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b"system>", b"physics>"))
    row, reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row is None
    assert reason == pch.NOT_SYSTEM


def test_shapes_named_but_absent_from_the_nif_are_dead(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch)
    row, reason = pch.classify(nif, _Nif(pointer=POINTER, shapes=("Other",)))
    assert row is None
    assert reason == pch.SHAPES_ABSENT


def test_an_unreadable_xml_is_counted_not_fatal(tmp_path, monkeypatch):
    """A locked or denied XML: the read fails after the pointer resolved. The
    run counts it in its own bucket and goes on to the next NIF."""
    nif, _xml = _mod(tmp_path, monkeypatch)
    other = nif.parent / "cuirass_1.nif"
    other.write_bytes(nif.read_bytes())
    monkeypatch.setattr(pch, "_open_nif", lambda p: _Nif(pointer=POINTER))
    real = pch.resolve_pointer

    def _locked(ptr, nif_path):
        # A directory passes for the resolved path and fails on read, the way
        # a file held open with no sharing does.
        return tmp_path if nif_path.name == "cuirass_0.nif" else real(
            ptr, nif_path)
    monkeypatch.setattr(pch, "resolve_pointer", _locked)
    rows, skip, _g, _n = pch.scan([nif, other], tmp_path)
    assert skip == Counter({pch.XML_UNREADABLE: 1})
    assert [r["path"] for r in rows] == [
        str(other.relative_to(tmp_path)).replace("\\", "/")]


# ------------------------------------------ what simulates: bone mass, not kind

def _xml(shapes, bones=b'  <bone name="NPC Pelvis"/>\n'
                        b'  <bone name="Skirt 1"><mass>1.5</mass></bone>\n',
         tail=b""):
    return (b"<system>\n" + bones + shapes + tail + b"</system>\n")


def _classify(tmp_path, monkeypatch, xml, shapes):
    nif, _x = _mod(tmp_path, monkeypatch, xml_bytes=xml)
    return pch.classify(nif, _Nif(pointer=POINTER, shapes=shapes))


def test_a_kinematic_per_vertex_helper_is_a_collider_not_cloth(
        tmp_path, monkeypatch):
    """A per-vertex body proxy on mass-0 body bones is what the skirt collides
    WITH. The first 09-25 census took it for the cloth and the per-triangle
    skirt for its collider."""
    row, reason = _classify(tmp_path, monkeypatch, _xml(
        b'  <per-vertex-shape name="VirtualBody"><tag>body</tag>'
        b'</per-vertex-shape>\n'
        b'  <per-triangle-shape name="Skirt"><tag>cloth</tag>'
        b'</per-triangle-shape>\n'), ("VirtualBody", "Skirt"))
    assert reason is None
    assert [c["name"] for c in row["cloth"]] == ["Skirt"]
    assert pch.cloth_reach(row) == {"Skirt": (True, True)}


def test_a_dynamic_per_triangle_shape_is_cloth(tmp_path, monkeypatch):
    """An XML with no per-vertex shape at all still simulates its skirt."""
    row, reason = _classify(tmp_path, monkeypatch, _xml(
        b'  <per-triangle-shape name="Skirt"><tag>cloth</tag>'
        b'</per-triangle-shape>\n'
        b'  <per-triangle-shape name="BodyCol"><tag>body</tag>'
        b'</per-triangle-shape>\n'), ("Skirt", "BodyCol"))
    assert reason is None
    assert [c["name"] for c in row["cloth"]] == ["Skirt"]


def test_an_xml_whose_shapes_are_all_kinematic_simulates_no_cloth(
        tmp_path, monkeypatch):
    row, reason = _classify(tmp_path, monkeypatch, _xml(
        b'  <per-vertex-shape name="VirtualBody"><tag>body</tag>'
        b'</per-vertex-shape>\n'), ("VirtualBody",))
    assert row is None
    assert reason == pch.NO_DYNAMIC


def test_a_per_vertex_partner_counts(tmp_path, monkeypatch):
    """Vertex-vertex is a collision pair: a per-vertex body proxy is a real
    body collider for per-vertex cloth."""
    row, _reason = _classify(tmp_path, monkeypatch, _xml(
        b'  <per-vertex-shape name="Skirt"><tag>cloth</tag>'
        b'</per-vertex-shape>\n'
        b'  <per-vertex-shape name="VirtualBody"><tag>body</tag>'
        b'</per-vertex-shape>\n'), ("Skirt", "VirtualBody"))
    assert pch.cloth_reach(row) == {"Skirt": (True, True)}


def _k(name, dynamic, tags=("body",)):
    return {"name": name, "tags": set(tags), "can": set(), "no": set(),
            "dynamic": dynamic}


def test_two_kinematic_shapes_never_pair():
    """FSMP needsCollision drops a kinematic-kinematic pair before any tag
    test; a dynamic shape with the same tags does pair."""
    assert not pch.pair_collides(_k("a", False), _k("b", False))
    assert pch.pair_collides(_k("a", True), _k("b", False))


def test_a_simulated_shape_tagged_body_is_not_the_body():
    """Two simulated shapes may collide, but a moving shape is not the body:
    the body-collider row wants a kinematic body-tagged partner."""
    row = {"cloth": [_k("Skirt", True, ("cloth",)), _k("Cape", True)]}
    row["shapes"] = row["cloth"]
    assert pch.cloth_reach(row)["Skirt"] == (True, False)


def test_bone_names_compare_without_case(tmp_path, monkeypatch):
    """The XML declares the pelvis `[PElv]`, the skin says `[Pelv]`: one
    engine string, so the declared mass-0 bone is the skin bone and the
    later mass-1 default does not reach it."""
    xml = _xml(b'  <per-triangle-shape name="Ground"><tag>ground</tag>'
               b'</per-triangle-shape>\n',
               bones=b'  <bone name="NPC PELVIS"/>\n'
                     b'  <bone-default><mass>1</mass></bone-default>\n')
    row, reason = _classify(tmp_path, monkeypatch, xml, ("Ground",))
    assert reason == pch.NO_DYNAMIC


def test_an_undeclared_skin_bone_takes_the_default_in_force(
        tmp_path, monkeypatch):
    """FSMP creates a skin bone it has not seen from the unnamed bone-default
    as it stands when the shape is read: after a mass-1 default the shape
    simulates, before it the shape is kinematic."""
    shape = (b'  <per-triangle-shape name="Cape"><tag>cloth</tag>'
             b'</per-triangle-shape>\n')
    default = b'  <bone-default><mass>1</mass></bone-default>\n'
    row, reason = _classify(tmp_path, monkeypatch,
                            _xml(default + shape, bones=b""), ("Cape",))
    assert reason is None and [c["name"] for c in row["cloth"]] == ["Cape"]
    nif2 = tmp_path / "second"
    nif2.mkdir()
    nif_convert._VFS_DATA_REL_MEMO.clear()
    row, reason = _classify(nif2, monkeypatch,
                            _xml(shape + default, bones=b""), ("Cape",))
    assert reason == pch.NO_DYNAMIC


def test_a_bone_template_carries_its_mass(tmp_path, monkeypatch):
    """`<bone template=T>` takes T's mass; `extends` copies the base first."""
    xml = _xml(b'  <per-triangle-shape name="Cape"><tag>cloth</tag>'
               b'</per-triangle-shape>\n',
               bones=b'  <bone-default name="base"><mass>2</mass>'
                     b'</bone-default>\n'
                     b'  <bone-default name="chain" extends="base">'
                     b'<linearDamping>0.5</linearDamping></bone-default>\n'
                     b'  <bone name="Skirt 1" template="chain"/>\n')
    row, reason = _classify(tmp_path, monkeypatch, xml, ("Cape",))
    assert reason is None and [c["name"] for c in row["cloth"]] == ["Cape"]


def test_a_constraint_between_two_kinematic_bones_does_not_count(
        tmp_path, monkeypatch):
    """FSMP skips it with a warning, so the cloth is still unconstrained."""
    nif, _xml_path = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b'bodyA="Skirt 1"', b'bodyA="NPC Spine"'))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is False


def test_rest_pose_depth_finds_a_shape_named_in_another_case():
    """The XML's `skirt` is the NIF's `Skirt` to FSMP. A case-exact lookup
    dropped it from the depth check, so a sunk skirt read as clean."""
    body = _Shape("BaseShape")
    body.verts = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0),
                  (-1.0, 1.0, 0.0)]
    body.tris = [(0, 1, 2), (0, 2, 3)]
    skirt = _Shape("Skirt")
    skirt.verts = [(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]   # one each side
    nif = _Nif(shapes=())
    nif.shapes = [body, skirt]
    out = pch._penetration(nif, ["skirt"])
    assert list(out) == ["skirt"]
    assert out["skirt"][1:] == (1, 2)


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


def test_a_constraint_inside_a_group_counts(tmp_path, monkeypatch):
    """A <constraint-group> is a container: its member constraints join the
    bones, so the cloth is constrained; an EMPTY group joins nothing."""
    line = b'  <generic-constraint bodyA="Skirt 1" bodyB="NPC Pelvis"/>\n'
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        line, b"  <constraint-group>\n  " + line + b"  </constraint-group>\n"))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is True
    nif2 = tmp_path / "second"
    nif2.mkdir()
    nif_convert._VFS_DATA_REL_MEMO.clear()
    nif, _xml = _mod(nif2, monkeypatch, xml_bytes=CLOTH_XML.replace(
        line, b"  <constraint-group/>\n"))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is False


def test_no_constraint_of_any_kind_is_unconstrained(tmp_path, monkeypatch):
    nif, _xml = _mod(tmp_path, monkeypatch, xml_bytes=CLOTH_XML.replace(
        b'  <generic-constraint bodyA="Skirt 1" bodyB="NPC Pelvis"/>\n', b""))
    row, _reason = pch.classify(nif, _Nif(pointer=POINTER))
    assert row["constrained"] is False


# ---------------------------------------------------------------- report

def _row(path, *, constrained=True, cloth_can=("body",), col_tags=("body",),
         named=2):
    cloth = {"name": "Skirt", "tags": {"cloth"}, "can": set(cloth_can),
             "no": set(), "dynamic": True}
    col = {"name": "Col", "tags": set(col_tags), "can": set(), "no": set(),
           "dynamic": False}
    return {"path": path, "garment": pch.garment_of(path),
            "constrained": constrained, "cloth": [cloth],
            "shapes_named": named, "shapes": [cloth, col], "pen": None}


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


def test_a_shape_named_but_missing_is_counted(capsys):
    rows = [_row("a/skirt_0.nif", named=3)]
    pch.report(rows, Counter(), {}, 1)
    assert _fault(capsys.readouterr().out,
                  "shape named but ABSENT") == "1"


def test_an_unreadable_xml_is_announced(capsys):
    """No-data must look different: the run says its counts are short."""
    pch.report([_row("a/skirt_0.nif")], Counter({pch.XML_UNREADABLE: 1}),
               {pch.XML_UNREADABLE: {"b/cape"}}, 2)
    assert "could not be READ" in capsys.readouterr().out


def test_both_weight_halves_are_one_garment(capsys):
    rows = [_row("a/skirt_0.nif", cloth_can=("ground",)),
            _row("a/skirt_1.nif", cloth_can=("ground",))]
    pch.report(rows, Counter(), {}, 2)
    line = next(ln for ln in capsys.readouterr().out.splitlines()
                if "reaching NO partner" in ln)
    assert line.split(":", 1)[1].split()[:3] == ["2", "/", "1"]


def test_an_empty_population_exits_3():
    with pytest.raises(SystemExit) as ex:
        pch.report([], Counter({pch.NO_POINTER: 5}), {}, 5)
    assert ex.value.code == 3
