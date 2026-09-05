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

"""Butt collider patch (#butt-collider-patch) -- the long-unfixed butt clip.

The buttocks come through a skirted cuirass at standstill AND in motion. It is
not skinning: 84% of the garment there is HDT-SMP chain cloth, so the SIMULATION
decides where it sits, and what it collides with is the armour's own collider --
which is CBBE-sized on a UBE-sized body (rearmost -9.55 vs the body's -12.43).

THREE VERT-MOVING FIXES WERE BUILT AND ALL FAILED, each measured:
  * nearest-point projection   closed 0.12u of the 2.89u gap
  * standoff enforcement       nothing (the collider is already OUTSIDE)
  * radial shrink-wrap         moved 50 verts, ALL on the legs; not one rear
                               vert moved rearward
because the collider carries only 10 rear verts in the whole band z62-72 and
none at the apex. There is nothing there to move, so the fix ADDS geometry.

Measured result: butt surface with no collider within 3u 38.1% -> 7.8%,
rearmost collider -11.43 -> -12.63 against a body at -12.43.
"""
import importlib
import inspect

import src.nif_convert as nc
from tests import _converter_sources as _cs  # source text across the split modules


def test_flag_defaults_on_since_the_equip_test_passed():
    """It adds a collision shape and an XML collision declaration, which is the
    equip-CTD surface, so it shipped OFF until equip-tested. It equipped clean
    in game (2026-08-10) and the piece carrying it was judged good (08-11), so
    it is ON.

    The OFFSET default moved with it. 0.6 is the value that was deployed and
    judged; leaving the knob at the original 0.2 while flipping the toggle would
    default the pack to a recipe nobody has looked at."""
    assert nc.BUTT_COLLIDER_PATCH is True
    assert nc._BUTT_COL_OFFSET == 0.6


def test_flag_opts_out(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_BUTT_COLLIDER_PATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.BUTT_COLLIDER_PATCH is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_BUTT_COLLIDER_PATCH", raising=False)
        importlib.reload(nc)


def test_wired_into_both_convert_paths():
    src = _cs.source(nc)
    assert src.count("_add_butt_collider_patch(dst_path)") >= 2


def test_donor_must_be_a_KINEMATIC_collider():
    """THE BUG THIS PINS, caught only by reading the emitted XML.

    "First declared per-triangle-shape" picked this piece's `Proxy`, which is
    CHAIN-DRIVEN and carries `<tag>Fabric</tag>` +
    `<no-collide-with-tag>Fabric</no-collide-with-tag>`. The patch shipped tagged
    as cloth that the skirt is explicitly FORBIDDEN to collide with -- a
    perfectly well-formed, completely inert collider. Cloning from `Collision`
    instead gives tag=Collision / can-collide-with=Fabric, which is what makes
    the skirt rest on it.
    """
    src = _cs.source(nc._add_butt_collider_patch)
    assert "all(_is_body_bone(b) for b in bwd)" in src, (
        "the donor must be selected by being KINEMATIC, not by declaration order")
    assert "if donor is None:" in src


def test_hands_and_feet_COUNT_as_body_bones():
    """THE BUG THIS PINS, measured on the piece that produced an in-game report.

    The kinematic test used the INJECTED BODY MESH's bone list as the set of
    "body bones". UBE ships hands and feet as SEPARATE meshes, so that list has
    neither: across three bodies the CBBE body carries 51 bones and the UBE body
    45, and the six missing are exactly `NPC L/R Hand`, `NPC L/R Foot` and
    `NPC L/R UpperarmTwist2` -- every one of them in the actor skeleton.

    So any collider touching a hand or a foot read as non-kinematic, and a leg
    garment usually touches a foot. The heavy cuirass therefore received NO butt
    collider at all: its only candidate, `Pants`, was rejected solely for using
    `NPC L Foot` and `NPC R Foot`. With the family admitted it becomes the donor
    and the piece gains 191 rear collider verts in the buttock band, against the
    27 that `Pants` alone contributed.
    """
    src = _cs.source(nc._add_butt_collider_patch)
    for fam in ("hand", "foot", "toe", "upperarmtwist2"):
        assert f'"{fam}"' in src, f"the {fam!r} family is not admitted"
    assert "_BODY_ADJACENT" in src


def test_the_widening_did_NOT_reach_for_the_actor_skeleton():
    """THE TRAP THE FIX HAD TO AVOID, and why it is a keyword family.

    "A body bone is one the actor skeleton declares" is the obvious widening and
    it is WRONG here: this modlist's skeleton declares `SkirtFBone01`, so that
    test would accept a CHAIN-DRIVEN shape as a body collider -- exactly the
    failure `test_donor_must_be_a_KINEMATIC_collider` above exists to prevent.
    Measured before choosing the rule: 649 skeleton nodes, `SkirtFBone01` among
    them.
    """
    src = _cs.source(nc._add_butt_collider_patch)
    assert "_actor_skeleton_bone_names" not in src, (
        "the donor test must not widen to the whole actor skeleton -- it "
        "declares chain bones like SkirtFBone01")


def test_it_clones_the_donor_block_rather_than_authoring_one():
    """An invented collision block is how collision-pair equip-CTDs happen.
    Cloning carries margin / penetration / tag / can-collide-with-tag /
    no-collide-with-bone / weight-threshold across verbatim."""
    src = _cs.source(nc._add_butt_collider_patch)
    assert "re.escape(donor)" in src
    assert 'replace(f\'name="{donor}"\'' in src


def test_it_only_fires_where_the_gap_is_MEASURED():
    """A piece whose collider already covers the buttocks must be untouched --
    otherwise this ships extra collision geometry to 3897 meshes on spec."""
    src = _cs.source(nc._add_butt_collider_patch)
    assert "uncovered < _BUTT_COL_MIN_UNCOVERED" in src
    assert nc._BUTT_COL_MIN_UNCOVERED > 0 and nc._BUTT_COL_GAP > 0


def test_all_or_nothing_with_a_byte_restore():
    """Same contract as the bust split: rebuilding a NIF from shapes drops ALL
    extra data (BODYTRI + the physics link). If anything is lost, the original
    bytes go back and the patch is skipped."""
    src = _cs.source(nc._add_butt_collider_patch)
    assert "backup = p.read_bytes()" in src
    assert "atomic_write_bytes(p, backup)" in src
    assert "pre_extra <= _all_extra(nf2)" in src
    assert "pre_shapes <= post" in src


def test_patch_sits_OUTSIDE_the_skin():
    """Cloth should rest ON the body, not inside it. A zero or negative offset
    puts the collision surface level with or under the skin."""
    assert nc._BUTT_COL_OFFSET > 0.0


def test_decimation_keeps_ORIGINAL_verts():
    """Representatives must be original body vertices, or weights, skin-to-bone
    transforms and g2s cannot be copied across and the patch needs re-rigging --
    which is where add_bone/STB damage comes from."""
    src = _cs.source(nc._cluster_decimate)
    assert "argmin" in src, "representative = the vert nearest the cell centroid"
    doc = (nc._cluster_decimate.__doc__ or "")
    assert "original" in doc.lower()


# --- #skirt-proxy-rebuild ----------------------------------------------------

def test_skirt_proxy_defaults_on_since_it_was_judged_in_motion():
    """A rank above ButtCol in risk -- ButtCol is KINEMATIC and cannot
    destabilise the sim, while a cloth proxy is chain-driven and a bad one can
    balloon, collapse, or pull to the origin. All three of those look wrong IN
    MOTION, which is the check it was held back for and the check it passed
    (2026-08-11, user verdict on the piece carrying it)."""
    assert nc.SKIRT_PROXY_REBUILD is True


def test_skirt_proxy_wired_into_both_convert_paths():
    src = _cs.source(nc)
    assert src.count("_add_skirt_collider_proxy(dst_path)") >= 2


def test_skirt_proxy_donor_must_be_CHAIN_DRIVEN():
    """The MIRROR of ButtCol's rule, and it matters just as much: cloning a
    kinematic block here would tag the cloth as a body collider and it would
    collide with the wrong set entirely. ButtCol needs a kinematic donor; this
    needs a Fabric one."""
    src = _cs.source(nc._add_skirt_collider_proxy)
    assert "_chain_mass(s_).max() > 1e-3" in src
    assert "if donor is None:" in src


def test_skirt_proxy_leaves_the_AUTHORED_proxy_alone():
    """It ADDS. The authored `Proxy` supports the skirt elsewhere (it spans
    z37.7-72.6) and replacing a working chain proxy is how a stable sim gets
    destabilised. Fabric shapes carry no-collide-with-tag Fabric, so the new
    proxy cannot fight the old one."""
    src = _cs.source(nc._add_skirt_collider_proxy)
    assert "createShapeFromData" in src
    for forbidden in ("set_verts", "override_verts"):
        assert forbidden not in src, "must not modify the authored proxy"


def test_skirt_proxy_only_fires_where_the_cloth_is_UNREPRESENTED():
    src = _cs.source(nc._add_skirt_collider_proxy)
    assert "unrep < _SKIRT_PROXY_MIN_UNREPRESENTED" in src
    assert nc._SKIRT_PROXY_MIN_UNREPRESENTED > 0 and nc._SKIRT_PROXY_GAP > 0


def test_skirt_proxy_all_or_nothing_with_byte_restore():
    src = _cs.source(nc._add_skirt_collider_proxy)
    assert "backup = p.read_bytes()" in src
    assert "atomic_write_bytes(p, backup)" in src
    assert "pre_extra <= _all_extra(nf2)" in src


def test_skirt_proxy_sources_from_SIMULATED_verts_only():
    """A proxy built from the rigid part of a garment would be pinned to the
    body and could not represent cloth at all."""
    src = _cs.source(nc._add_skirt_collider_proxy)
    assert "cm >= _SKIRT_PROXY_CHAIN_MIN" in src
    assert 0.0 < nc._SKIRT_PROXY_CHAIN_MIN <= 1.0


# --- #proxy-weight-invariant -------------------------------------------------

def _weight_pair_mesh():
    """A thin tube -- the shape of a skirt -- and the SAME mesh under a body
    weight morph: identical topology and indexing, different positions. That is
    exactly the relationship between an armour's `_0` and `_1` files."""
    import numpy as np
    nu, nv = 40, 30
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0, 30, nv)
    U, V = np.meshgrid(u, v, indexing="ij")
    P = np.stack([np.cos(U) * 8, np.sin(U) * 3, V], -1).reshape(-1, 3)
    tris = []
    for i in range(nu):
        for j in range(nv - 1):
            a = i * nv + j
            b = ((i + 1) % nu) * nv + j
            tris += [[a, b, a + 1], [b, b + 1, a + 1]]
    P1 = P.copy()
    P1[:, 0] *= 1.06
    P1[:, 1] *= 1.09
    P1 += 0.15 * np.sin(P[:, 2:3] / 7.0)
    return P, P1, np.asarray(tris, dtype=np.int64)


def test_topo_decimation_is_IDENTICAL_across_the_weight_pair():
    """Skyrim blends `_0` and `_1` PER VERTEX and one `.tri` serves the pair, so
    a proxy that differs across the two ships a blend over mismatched vertex
    arrays AND morph offsets addressing vertices the other weight lacks.
    Measured on the shipped pack: 14 of 28 pairs carrying a generated skirt
    proxy disagreed. This is the property that makes that impossible."""
    import numpy as np
    from src import nif_convert_physics as ph
    P0, P1, tris = _weight_pair_mesh()
    r0, _l0, t0 = ph._topo_decimate(P0, tris, 300)
    r1, _l1, t1 = ph._topo_decimate(P1, tris, 300)
    assert len(r0) == len(r1), f"vertex COUNT differs: {len(r0)} vs {len(r1)}"
    assert np.array_equal(np.asarray(r0), np.asarray(r1)), \
        "same count but different SOURCE vertices -- the blend would lerp " \
        "between points that do not correspond"
    assert np.array_equal(t0, t1), "triangles differ across the pair"
    assert len(r0) > 0 and len(t0) > 0, "decimator produced nothing to compare"


def test_the_position_grid_is_NOT_invariant_so_the_test_above_can_fail():
    """The control. A property test that nothing can fail proves nothing --
    this pins that the weight morph really does move the legacy decimator, so
    the assertion above is doing work."""
    import numpy as np
    from src import nif_convert_physics as ph
    P0, P1, tris = _weight_pair_mesh()
    r0, _l0, t0 = ph._cluster_decimate(P0, tris, 300)
    r1, _l1, t1 = ph._cluster_decimate(P1, tris, 300)
    same = (len(r0) == len(r1)
            and np.array_equal(np.asarray(r0), np.asarray(r1))
            and np.array_equal(t0, t1))
    assert not same, \
        "the position grid came out invariant on this mesh -- pick a harsher " \
        "morph, or the invariance test above is vacuous"


def test_topo_decimation_keeps_ORIGINAL_verts():
    """Same contract as the legacy decimator: representatives must be original
    vertex indices, or weights, skin-to-bone transforms and g2s cannot be
    copied across and the proxy needs re-rigging."""
    import numpy as np
    from src import nif_convert_physics as ph
    P0, _P1, tris = _weight_pair_mesh()
    reps, lab, _t = ph._topo_decimate(P0, tris, 300)
    reps = np.asarray(reps)
    assert reps.min() >= 0 and reps.max() < len(P0)
    assert len(np.unique(reps)) == len(reps), "a vertex represents two cells"
    assert lab.min() >= 0, "a vertex was left unassigned"


def test_proxy_weight_invariant_is_OPT_IN_until_it_has_a_verdict():
    """It moves geometry on every piece carrying a generated proxy."""
    assert nc.PROXY_WEIGHT_INVARIANT is False
