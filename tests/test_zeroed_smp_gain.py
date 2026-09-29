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

r"""#zeroed-smp-gain -- a verified zeroed BodySlide build brings its SMP physics.

THE CASE (2026-09-25). #zeroed-output-source refused every build whose physics
differed from today's source. For a set of vanilla-armour pieces today's source
is a static prebuilt mesh (the within-tier body match swapped out the SMP loose
mesh for its bespoke body), and the user's zeroed build of the SMP design --
verified vertex for vertex, carrying the zeroed body -- was refused for its
physics alone: CBBE wearers got a skirt with physics, UBE wearers a static one.
Now a physics GAIN is taken when both sides go body-swap, the build's XML
resolves, parses and holds a constraint anywhere in its tree, and nothing the
XML names is a stripped body its collision-proxy re-import would bring back.

OPT-IN since 2026-09-26 (user decision after seeing it in game): the SMP builds
sit looser on the body than the static pieces, so by default such pieces keep
today's source; `CBBE2UBE_ZEROED_SMP_GAIN=1` takes the build. The old
off-switch `CBBE2UBE_NO_ZEROED_SMP_GAIN` is no longer read. The tests below run
opted in unless they say otherwise.

#smp-gain-collision-partner (same day): the conversion prunes the XML blocks of
shapes it drops, and one build's only body collider went that way, leaving its
simulated skirt proxy nothing to collide with. A gain now also needs every
simulated shape the pruned XML keeps to have a partner under FSMP's rules.
The prune is the conversion's own, line by line; a prune that breaks the XML,
or takes every simulated shape while the chain still swings a drawn mesh, is
refused too. `CBBE2UBE_NO_SMP_GAIN_COLLISION_PARTNER=1` drops that rule.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import discovery  # noqa: E402
from src import nif_io  # noqa: E402
from tests.test_zeroed_output_source import (  # noqa: E402
    BASE_MOD, OUT_MOD, STEM, Selection, _touch)

ON = "CBBE2UBE_ZEROED_SMP_GAIN"
OLD_OFF = "CBBE2UBE_NO_ZEROED_SMP_GAIN"
BODY_TEX = "textures\\actors\\character\\female\\femalebody_1.dds"
CLOTH_TEX = "textures\\armor\\test\\skirt.dds"


PARTNER_OFF = "CBBE2UBE_NO_SMP_GAIN_COLLISION_PARTNER"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.delenv(OLD_OFF, raising=False)
    monkeypatch.delenv(PARTNER_OFF, raising=False)


class _Shape:
    """What the converter's body detectors read off a shape."""

    def __init__(self, name, nverts=4, nbones=2, zspan=10.0, diffuse=CLOTH_TEX,
                 bones=None):
        self.name = name
        self.verts = [(0.0, 0.0, 0.0)] + [(0.0, 0.0, float(zspan))] * (nverts - 1)
        self.bone_names = list(bones) if bones else [f"NPC Bone{i}" for i in range(nbones)]
        self.tris = [(0, 1, 2)]
        self.textures = {"Diffuse": diffuse}
        self._backing = None


def _named_body():
    return _Shape("3BA")                      # swapped by name


def _skin_body(name="Body", bones=None):
    """A body-skin body the detector finds by texture and size, not by name,
    skinned to skeleton bones -- one of them a bone the XML names."""
    bones = bones or ["NPC Pelvis [Pelv]"] + [f"NPC Bone{i}" for i in range(21)]
    return _Shape(name, nverts=600, zspan=103.0, diffuse=BODY_TEX, bones=bones)


def _xml(body='<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>',
         shapes=('Skirt',)):
    decl = "".join(f'<per-vertex-shape name="{n}"><margin>1</margin></per-vertex-shape>'
                   for n in shapes)
    return (f'<?xml version="1.0" encoding="UTF-8"?><system>'
            f'<bone name="Skirt 01"/>{decl}{body}</system>').encode()


def _verdict(today=None, build=None, xml=None):
    today = [_named_body(), _Shape("Cuirass")] if today is None else today
    build = [_named_body(), _Shape("Skirt")] if build is None else build
    return discovery._smp_gain_verdict(today, build, _xml() if xml is None else xml)


# --- the rules, one weight ---------------------------------------------------------

def test_a_constrained_build_on_the_body_swap_path_brings_its_physics():
    assert _verdict() is None


def test_a_constraint_inside_a_constraint_group_counts():
    grouped = _xml(body='<constraint-group><generic-constraint bodyA="Skirt 01" '
                        'bodyB="NPC Pelvis [Pelv]"/></constraint-group>')
    assert _verdict(xml=grouped) is None


@pytest.mark.parametrize("tag", ["stiffspring-constraint", "conetwist-constraint"])
def test_the_other_constraint_kinds_count(tag):
    assert _verdict(xml=_xml(body=f'<{tag} bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>')) is None


def test_an_xml_without_a_constraint_is_refused():
    why = _verdict(xml=_xml(body="<constraint-group></constraint-group>"))
    assert why == "its physics XML has no constraint"


def test_an_unresolved_xml_is_refused():
    assert discovery._smp_gain_verdict(
        [_named_body()], [_named_body(), _Shape("Skirt")], None) == \
        "its physics XML does not resolve"


def test_an_xml_that_does_not_parse_is_refused():
    assert _verdict(xml=b"<system><bone name='x'></system") == "its physics XML does not parse"


def test_a_source_the_converter_copies_keeps_it():
    """Today's source with no body goes down the copy path; the build's body
    would move it to body-swap -- the pass-chain change rule 5 guards."""
    assert _verdict(today=[_Shape("Cuirass")]) == \
        "today's source has no body the converter swaps"


def test_a_build_the_converter_copies_keeps_todays_source():
    assert _verdict(build=[_Shape("Skirt")]) == "the build has no body the converter swaps"


def test_a_body_skin_shape_the_swap_misses_is_refused():
    """A body shape the detector declines would ship as cloth. Since
    #skin-stub-drop every VISIBLE skinned body-skin shape is a body, so the
    shape the swap can still miss is a HIDDEN one (flag bit 0) that is too
    thinly boned for the size-gated heuristic."""
    loose = _Shape("CBBE", nverts=1333, nbones=10, zspan=60.0, diffuse=BODY_TEX)
    loose.flags = 1
    assert _verdict(build=[_named_body(), loose, _Shape("Skirt")]) == \
        "the build's body 'CBBE' would ship as cloth"


def test_a_small_skin_slice_is_not_a_body():
    """Bare hands on hand bones: the body detector leaves them (#skin-stub-drop
    keeps extremities), and the bespoke-body size rule must not call them a
    body that would ship as cloth either."""
    hands = _Shape("Hands", nverts=46, zspan=5.0, diffuse=BODY_TEX,
                   bones=["NPC L Hand [LHnd]", "NPC R Hand [RHnd]",
                          "NPC L Finger00 [LF00]", "NPC R Finger00 [RF00]"])
    assert _verdict(build=[_named_body(), hands, _Shape("Skirt")]) is None


def test_an_xml_named_body_the_reimport_brings_back_is_refused():
    """The XML registers the body as a collider under a name the re-import's
    body-name filter does not know: it would come back as a hidden second body."""
    why = _verdict(build=[_skin_body("Body"), _Shape("Skirt")],
                   xml=_xml(shapes=("Skirt", "Body")))
    assert why == "its physics XML would bring back the stripped body 'Body'"


def test_an_xml_named_body_the_reimport_skips_is_taken():
    """A registered '3BA' body is dropped and never re-imported: the cloth
    collides with the injected UBE body instead."""
    assert _verdict(build=[_named_body(), _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "3BA"))) is None


def test_the_xml_name_is_matched_case_for_case():
    """The re-import looks the name up case for case, so an XML 'Body' finds
    nothing in a build whose body is 'body'."""
    assert _verdict(build=[_skin_body("body"), _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "Body"))) is None


def test_a_stripped_body_that_carries_a_driven_bone_is_refused():
    """The re-import also brings back a shape that carries a physics bone the
    XML drives, when nothing else carries it."""
    body = _skin_body("Body", bones=["NPC Pelvis [Pelv]", "Skirt 01"] +
                      [f"NPC Bone{i}" for i in range(20)])
    assert _verdict(build=[body, _Shape("Skirt")]) == \
        "its physics XML would bring back the stripped body 'Body'"


def test_a_stripped_body_on_skeleton_bones_only_is_taken():
    """Skeleton bones resolve on the actor, so the re-import never needs a
    shape for them -- even one the XML names ('NPC Pelvis [Pelv]' here)."""
    assert _verdict(build=[_skin_body("Body"), _Shape("Skirt")]) is None


def test_an_xml_named_exposed_skin_slice_is_refused():
    """A body-skin slice big enough for the exposed-skin swap is stripped too."""
    slice_ = _Shape("Cleavage", nverts=320, zspan=20.0, diffuse=BODY_TEX)
    why = _verdict(build=[_named_body(), slice_, _Shape("Skirt")],
                   xml=_xml(shapes=("Skirt", "Cleavage")))
    assert why == "its physics XML would bring back the stripped body 'Cleavage'"


def test_an_xml_named_skin_stub_is_refused():
    """#skin-stub-drop: a visible body-skin patch on torso bones is stripped
    at any size (issue #30), so an XML that names it would bring it back."""
    stub = _Shape("Collar", nverts=46, zspan=5.0, diffuse=BODY_TEX)
    why = _verdict(build=[_named_body(), stub, _Shape("Skirt")],
                   xml=_xml(shapes=("Skirt", "Collar")))
    assert why == "its physics XML would bring back the stripped body 'Collar'"


def test_an_xml_named_skin_decal_on_hand_bones_is_taken():
    """Bare hands share the body diffuse but stay in the output (the injected
    body has none), so the re-import has nothing to bring back."""
    decal = _Shape("Hands", nverts=46, zspan=5.0, diffuse=BODY_TEX,
                   bones=["NPC L Hand [LHnd]", "NPC R Hand [RHnd]"])
    assert _verdict(build=[_named_body(), decal, _Shape("Skirt")],
                    xml=_xml(shapes=("Skirt", "Hands"))) is None


# --- rule e: the simulated shapes keep a partner after the prune ---------------------
#
# The case: the XML's only body collider is 'Body', the build names its body
# 'body'. Rule d lets it through; the conversion prunes the 'Body' block; the
# skirt proxy, which collides only with the tag that block carried, is left
# colliding with nothing.

LONE = ("its simulated shape 'Proxy' would collide with nothing once the shapes "
        "the conversion drops are pruned")


def _block(name, tags=("Collision",), can=("Fabric",), no=(), kind="per-triangle-shape"):
    inner = "".join(f"<tag>{t}</tag>" for t in tags)
    inner += "".join(f"<can-collide-with-tag>{t}</can-collide-with-tag>" for t in can)
    inner += "".join(f"<no-collide-with-tag>{t}</no-collide-with-tag>" for t in no)
    return f'<{kind} name="{name}">{inner}</{kind}>'


def _proxy_xml(*blocks, proxy=None, bones='<bone name="Skirt 01"><mass>1</mass></bone>'):
    proxy = proxy if proxy is not None else _block("Proxy", tags=("Fabric",),
                                                   can=("Collision",))
    return (f'<?xml version="1.0" encoding="UTF-8"?><system>{bones}{proxy}'
            + "".join(blocks)
            + '<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>'
              '</system>').encode()


def _proxy(bones=("Skirt 01",)):
    return _Shape("Proxy", bones=bones)                  # driven by the chain bone


def _butt_col(name="ButtCol"):
    return _Shape(name)                                  # skeleton bones: kinematic


def _partner_verdict(xml, *shapes):
    return discovery._smp_gain_verdict([_named_body(), _Shape("Cuirass")],
                                       [_skin_body("body"), _proxy(), *shapes], xml)


def test_a_gain_whose_only_collider_is_pruned_is_refused():
    assert _partner_verdict(_proxy_xml(_block("Body"))) == LONE


def test_a_collider_on_the_swapped_body_is_pruned():
    """A '3BA' block passes rule d (the re-import skips that name), but the body
    swap removes the shape, so the block goes and cannot catch the proxy."""
    assert discovery._smp_gain_verdict(
        [_named_body(), _Shape("Cuirass")], [_named_body(), _proxy()],
        _proxy_xml(_block("3BA"))) == LONE


def test_a_gain_that_keeps_a_butt_collider_is_taken():
    assert _partner_verdict(_proxy_xml(_block("Body"), _block("ButtCol")),
                            _butt_col()) is None


def test_switched_off_a_partnerless_gain_is_taken(monkeypatch):
    monkeypatch.setenv(PARTNER_OFF, "1")
    assert _partner_verdict(_proxy_xml(_block("Body"))) is None


def test_the_prune_matches_the_shape_name_case_for_case():
    """The conversion keeps a block only under the NIF's exact name."""
    assert _partner_verdict(_proxy_xml(_block("ButtCol")), _butt_col("buttcol")) == LONE


def test_a_collider_that_refuses_the_cloth_is_no_partner():
    """FSMP asks both shapes: this one only collides with hair."""
    assert _partner_verdict(_proxy_xml(_block("ButtCol", can=("Hair",))),
                            _butt_col()) == LONE


def test_an_empty_can_list_allows_every_tag_but_its_no_collide_tags():
    assert _partner_verdict(_proxy_xml(_block("ButtCol", can=())), _butt_col()) is None
    assert _partner_verdict(_proxy_xml(_block("ButtCol", can=(), no=("Fabric",))),
                            _butt_col()) == LONE


def test_tags_compare_without_case():
    assert _partner_verdict(_proxy_xml(_block("ButtCol", tags=("collision",),
                                              can=("FABRIC",))),
                            _butt_col()) is None


def test_the_shape_is_not_its_own_partner():
    own = _block("Proxy", tags=("Fabric",), can=("Fabric",))
    assert _partner_verdict(_proxy_xml(proxy=own)) == LONE


def test_a_shape_the_nif_does_not_skin_is_no_partner():
    """FSMP builds no collision body for a shape with no skin."""
    bare = _Shape("ButtCol")
    bare.bone_names = []
    assert _partner_verdict(_proxy_xml(_block("ButtCol")), bare) == LONE


def test_a_kinematic_shape_needs_no_partner():
    """Skinned to massless skeleton bones, the proxy does not simulate."""
    assert discovery._smp_gain_verdict(
        [_named_body(), _Shape("Cuirass")],
        [_skin_body("body"), _proxy(bones=("NPC Pelvis [Pelv]",))],
        _proxy_xml(_block("Body"))) is None


def test_the_unnamed_bone_default_gives_an_undeclared_bone_its_mass():
    """An undeclared skin bone takes the unnamed default as it stands when the
    shape is read: after a massive default it simulates, before one it does not."""
    heavy = "<bone-default><mass>1</mass></bone-default>"
    assert _partner_verdict(_proxy_xml(_block("Body"), bones=heavy)) == LONE
    late = (f'<?xml version="1.0"?><system>'
            f'{_block("Proxy", tags=("Fabric",), can=("Collision",))}{heavy}'
            f'<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>'
            f'</system>').encode()
    assert _partner_verdict(late) is None


@pytest.mark.parametrize("wrap", [lambda c: c,
                                  lambda c: f"<constraint-group>{c}</constraint-group>"],
                         ids=["top-level", "in-a-group"])
def test_a_constraint_makes_its_undeclared_bones_first(wrap):
    """A constraint met before the bone's declaration creates the bone from the
    massless default; FSMP keeps the first one, so the later mass never lands."""
    early = wrap('<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>')
    assert _partner_verdict(_proxy_xml(_block("Body"), bones=early +
                                       '<bone name="Skirt 01"><mass>1</mass></bone>')) is None


def test_bone_names_compare_without_case():
    """The skin's 'Skirt 01' is the XML's 'skirt 01': the game folds case."""
    assert _partner_verdict(_proxy_xml(_block("Body"), bones='<bone name="skirt 01">'
                                       '<mass>1</mass></bone>')) == LONE


def test_a_bone_takes_its_named_template_mass():
    bones = ('<bone-default name="cloth"><mass>2</mass></bone-default>'
             '<bone name="Skirt 01" template="cloth"/>')
    assert _partner_verdict(_proxy_xml(_block("Body"), bones=bones)) == LONE


# --- rule e replays the conversion's OWN prune: line by line ------------------------
#
# The conversion's prune reads a line's FIRST shape tag and keeps or drops the
# whole line, so whatever shares a line with a dropped block goes with it.

BONE = '<bone name="Skirt 01"><mass>1</mass></bone>'
PROXY = _block("Proxy", tags=("Fabric",), can=("Collision",))
CHAIN = '<generic-constraint bodyA="Skirt 01" bodyB="NPC Pelvis [Pelv]"/>'
SWING = ("its cloth would swing with no collision shape once the shapes the "
         "conversion drops are pruned")


def _lines_xml(*lines, tail=CHAIN + "\n</system>"):
    """An XML laid out one entry per line, as authors mostly write them."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<system>\n'
            + "\n".join(lines) + "\n" + tail + "\n").encode()


def test_a_partner_on_its_own_line_survives_the_prune():
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("Body"), _block("ButtCol")),
                            _butt_col()) is None


def test_a_partner_on_a_pruned_blocks_line_goes_with_it():
    """'Body' opens the line, so the conversion drops the line -- ButtCol too."""
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("Body") + _block("ButtCol")),
                            _butt_col()) == LONE


def test_a_pruned_block_after_a_kept_one_on_its_line_stays_and_builds_nothing():
    """ButtCol opens the line, so the line stays; 'Body' is still no shape."""
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("ButtCol") + _block("Body")),
                            _butt_col()) is None
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("ButtCol", can=("Hair",))
                                       + _block("Body")), _butt_col()) == LONE


def test_a_bone_on_a_pruned_blocks_line_goes_with_it():
    """The chain bone's mass is declared on the dropped line: once pruned, the
    bone is only made by the proxy, from the massless default -- nothing swings."""
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("Body"))) == LONE
    assert _partner_verdict(_lines_xml(_block("Body") + BONE, PROXY)) is None


def test_a_prune_that_breaks_the_xml_is_refused():
    """The dropped line held the closing tag: FSMP would load no physics at all."""
    assert (_partner_verdict(_lines_xml(BONE, PROXY, _block("ButtCol"),
                                        tail=CHAIN + "\n" + _block("Body") + "</system>"),
                             _butt_col())
            == "its physics XML would not parse once the shapes the conversion "
               "drops are pruned")


@pytest.mark.parametrize("layout", [
    _lines_xml(BONE, PROXY, _block("Body"), _block("ButtCol")),
    _lines_xml(BONE, PROXY, _block("Body") + _block("ButtCol")),
    _lines_xml(BONE, PROXY, _block("ButtCol") + _block("Body")),
    b"\xef\xbb\xbf" + _lines_xml(_block("Body") + BONE, PROXY).replace(b"\n", b"\r\n"),
    _lines_xml(BONE, PROXY, _block("ButtCol")),
    _lines_xml(BONE, '<per-triangle-shape name="Body">', "<tag>Collision</tag>",
               "</per-triangle-shape>", PROXY),
], ids=["own-lines", "shared-line", "kept-first", "bom-crlf", "nothing-pruned",
        "block-over-lines"])
def test_the_model_prunes_exactly_what_the_conversion_writes(tmp_path, monkeypatch,
                                                            layout):
    from types import SimpleNamespace
    from src import nif_convert_physics as phys
    monkeypatch.setattr(phys, "_actor_skeleton_bone_names", lambda: set())
    p = tmp_path / "skirt.xml"
    p.write_bytes(layout)
    nif = SimpleNamespace(shapes=[_proxy(), _butt_col()], nodes={})
    phys._harden_hdt_xml_for_fsmp(p, nif)
    assert p.read_bytes() == phys._hdt_xml_shape_pruned(layout, {"Proxy", "ButtCol"})


def test_switched_off_the_line_prune_refuses_nothing(monkeypatch):
    monkeypatch.setenv(PARTNER_OFF, "1")
    assert _partner_verdict(_lines_xml(BONE, PROXY, _block("Body") + _block("ButtCol")),
                            _butt_col()) is None


# --- rule e: a prune that takes EVERY simulated shape --------------------------------

def _vacuous(proxy, *extra):
    """The simulated proxy's block shares 'Body''s line and goes with it; the
    chain bone keeps its mass, and the kept ButtCol is kinematic."""
    return discovery._smp_gain_verdict(
        [_named_body(), _Shape("Cuirass")],
        [_skin_body("body"), proxy, _butt_col(), *extra],
        _lines_xml(BONE, _block("Body") + PROXY, _block("ButtCol")))


def _hidden_proxy():
    class _Hidden:
        flags = 0x1                                      # the Hidden bit
    proxy = _proxy()
    proxy._backing = _Hidden()
    return proxy


def test_a_prune_that_takes_every_simulated_shape_leaves_the_cloth_swinging():
    """The proxy mesh itself is drawn and rides the swinging bone."""
    assert _vacuous(_proxy()) == SWING


def test_a_hidden_mesh_on_the_chain_is_no_swinging_cloth():
    assert _vacuous(_hidden_proxy()) is None
    assert _vacuous(_hidden_proxy(), _Shape("Skirt", bones=("Skirt 01",))) == SWING


def test_a_mesh_without_triangles_is_no_swinging_cloth():
    bare = _proxy()
    bare.tris = []
    assert _vacuous(bare) is None


def test_a_mesh_off_the_chain_is_no_swinging_cloth():
    """What is drawn rides no simulated bone: the chain moves no mesh."""
    assert _vacuous(_hidden_proxy(),
                    _Shape("Tassel", bones=("Tassel 01", "NPC Pelvis [Pelv]"))) is None
    assert _vacuous(_hidden_proxy(), _Shape("Tassel", bones=("skirt 01",))) == SWING


def test_switched_off_a_swinging_cloth_is_taken(monkeypatch):
    monkeypatch.setenv(PARTNER_OFF, "1")
    assert _vacuous(_proxy()) is None


def test_an_xml_the_author_wrote_with_no_simulated_shape_is_not_judged():
    """No shape simulated before the prune either: the author's own design."""
    assert _partner_verdict(_lines_xml(BONE, _block("ButtCol")), _butt_col(),
                            _Shape("Skirt", bones=("Skirt 01",))) is None


# --- the selection -------------------------------------------------------------------

def _gain_world(tmp_path, monkeypatch, *, xml=None, build_marker=(True, True),
                today_marker=(False, False)):
    sel = Selection(tmp_path, monkeypatch, today="other-shapes")
    mark = b"nif HDT Skinned Mesh Physics Object meshes\\armor\\test\\skirt.xml"
    for w, on in zip(("_0", "_1"), build_marker):
        _touch(sel.mods / OUT_MOD / "meshes" / f"{STEM}{w}.nif", mark if on else b"nif")
    for w, on in zip(("_0", "_1"), today_marker):
        _touch(sel.mods / BASE_MOD / "meshes" / f"{STEM}{w}.nif", mark if on else b"nif")

    def fake_nif(p):
        own = OUT_MOD in str(p)
        return nif_io.Nif(path=Path(p), shapes=[_named_body(),
                                                _Shape("Skirt" if own else "Cuirass")])

    xml = xml or {}
    monkeypatch.setattr(discovery, "_gain_nif", fake_nif)
    monkeypatch.setattr(discovery, "_gain_xml",
                        lambda p: xml.get(str(p)[-6:-4], _xml()))
    return sel


def test_opted_in_the_build_with_physics_is_taken(tmp_path, monkeypatch, capsys):
    sel = _gain_world(tmp_path, monkeypatch)
    assert set(Selection.owners(sel.index()).values()) == {OUT_MOD}
    assert "(1 with its SMP physics)" in capsys.readouterr().err


def test_by_default_the_piece_keeps_todays_source(tmp_path, monkeypatch, capsys):
    """The SMP builds sit looser on the body: the gain is opt-in."""
    sel = _gain_world(tmp_path, monkeypatch)
    monkeypatch.delenv(ON, raising=False)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert "its physics would change: 1" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "no", "off"])
def test_the_opt_in_set_to_no_keeps_todays_source(tmp_path, monkeypatch, capsys,
                                                   value):
    sel = _gain_world(tmp_path, monkeypatch)
    monkeypatch.setenv(ON, value)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert "its physics would change: 1" in capsys.readouterr().err


def test_the_old_off_switch_does_not_veto_the_opt_in(tmp_path, monkeypatch, capsys):
    """CBBE2UBE_NO_ZEROED_SMP_GAIN asked for today's default and is no longer
    read: left in a recipe, it cannot silently defeat the opt-in."""
    sel = _gain_world(tmp_path, monkeypatch)
    monkeypatch.setenv(OLD_OFF, "1")
    assert set(Selection.owners(sel.index()).values()) == {OUT_MOD}
    assert "(1 with its SMP physics)" in capsys.readouterr().err


def test_the_old_off_switch_alone_keeps_todays_source(tmp_path, monkeypatch, capsys):
    """What a recipe that still sets the old switch asked for, it gets."""
    sel = _gain_world(tmp_path, monkeypatch)
    monkeypatch.delenv(ON, raising=False)
    monkeypatch.setenv(OLD_OFF, "1")
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert "its physics would change: 1" in capsys.readouterr().err


def test_a_physics_loss_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, build_marker=(False, False),
                      today_marker=(True, True))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_build_with_physics_at_one_weight_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, build_marker=(False, True))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_source_with_physics_at_one_weight_keeps_it(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch, today_marker=(True, False))
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


def test_a_refusal_at_one_weight_keeps_the_pair(tmp_path, monkeypatch, capsys):
    sel = _gain_world(tmp_path, monkeypatch, xml={"_0": _xml(body="")})
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert ("it would gain physics, but its physics XML has no constraint: 1"
            in capsys.readouterr().err)


def test_a_partnerless_gain_keeps_todays_source_and_says_why(tmp_path, monkeypatch,
                                                            capsys):
    """The build's skirt simulates on its own bone and nothing can catch it."""
    lone = (b'<?xml version="1.0"?><system><bone name="NPC Bone0"><mass>1</mass></bone>'
            b'<per-vertex-shape name="Skirt"><tag>Fabric</tag></per-vertex-shape>'
            b'<generic-constraint bodyA="NPC Bone0" bodyB="NPC Pelvis [Pelv]"/></system>')
    sel = _gain_world(tmp_path, monkeypatch, xml={"_0": lone, "_1": lone})
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}
    assert ("it would gain physics, but its simulated shape 'Skirt' would collide "
            "with nothing once the shapes the conversion drops are pruned: 1"
            in capsys.readouterr().err)


def test_an_unreadable_mesh_keeps_todays_source(tmp_path, monkeypatch):
    sel = _gain_world(tmp_path, monkeypatch)

    def broken(p):
        raise RuntimeError("cannot open")

    monkeypatch.setattr(discovery, "_gain_nif", broken)
    assert set(Selection.owners(sel.index()).values()) == {BASE_MOD}


# --- the XML the build points at -----------------------------------------------------

def test_the_xml_is_the_one_the_nifs_own_pointer_names(tmp_path):
    from tests.synthetic_nif import build_shape_nif, pynifly_available
    if not pynifly_available():
        pytest.skip("pynifly unavailable")
    from src import nif_convert as nc
    pyn = nc._pynifly()
    mod = tmp_path / "mods" / OUT_MOD
    nif_path = build_shape_nif(_touch(mod / "meshes" / "armor" / "test" / "skirt_1.nif"))
    nif = pyn.NifFile(filepath=str(nif_path))
    pyn.NiStringExtraData.New(nif, name="HDT Skinned Mesh Physics Object",
                              string_value="meshes\\armor\\test\\physics\\skirt.xml",
                              parent=nif.rootNode)
    nif.save()
    _touch(mod / "meshes" / "armor" / "test" / "physics" / "skirt.xml", _xml())
    _touch(mod / "meshes" / "armor" / "test" / "skirt.xml", b"<other/>")
    assert discovery._gain_xml(nif_path) == _xml()
