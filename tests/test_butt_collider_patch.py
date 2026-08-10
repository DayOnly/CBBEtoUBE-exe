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


def test_flag_defaults_off():
    """The riskiest change in the project: it adds a collision shape and an XML
    collision declaration, which is the equip-CTD surface. It does not get a
    default until it has been equip-tested in game."""
    assert nc.BUTT_COLLIDER_PATCH is False


def test_flag_opts_in(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_BUTT_COLLIDER_PATCH", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.BUTT_COLLIDER_PATCH is True
    finally:
        monkeypatch.delenv("CBBE2UBE_BUTT_COLLIDER_PATCH", raising=False)
        importlib.reload(nc)


def test_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
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
    src = inspect.getsource(nc._add_butt_collider_patch)
    assert "all(b in _body_bones for b in bwd)" in src, (
        "the donor must be selected by being KINEMATIC, not by declaration order")
    assert "if donor is None:" in src


def test_it_clones_the_donor_block_rather_than_authoring_one():
    """An invented collision block is how collision-pair equip-CTDs happen.
    Cloning carries margin / penetration / tag / can-collide-with-tag /
    no-collide-with-bone / weight-threshold across verbatim."""
    src = inspect.getsource(nc._add_butt_collider_patch)
    assert "re.escape(donor)" in src
    assert 'replace(f\'name="{donor}"\'' in src


def test_it_only_fires_where_the_gap_is_MEASURED():
    """A piece whose collider already covers the buttocks must be untouched --
    otherwise this ships extra collision geometry to 3897 meshes on spec."""
    src = inspect.getsource(nc._add_butt_collider_patch)
    assert "uncovered < _BUTT_COL_MIN_UNCOVERED" in src
    assert nc._BUTT_COL_MIN_UNCOVERED > 0 and nc._BUTT_COL_GAP > 0


def test_all_or_nothing_with_a_byte_restore():
    """Same contract as the bust split: rebuilding a NIF from shapes drops ALL
    extra data (BODYTRI + the physics link). If anything is lost, the original
    bytes go back and the patch is skipped."""
    src = inspect.getsource(nc._add_butt_collider_patch)
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
    src = inspect.getsource(nc._cluster_decimate)
    assert "argmin" in src, "representative = the vert nearest the cell centroid"
    doc = (nc._cluster_decimate.__doc__ or "")
    assert "original" in doc.lower()


# --- #skirt-proxy-rebuild ----------------------------------------------------

def test_skirt_proxy_flag_defaults_off():
    """A rank above ButtCol in risk: ButtCol is KINEMATIC and cannot destabilise
    the sim, while a cloth proxy is chain-driven and a bad one can balloon,
    collapse, or pull to the origin."""
    assert nc.SKIRT_PROXY_REBUILD is False


def test_skirt_proxy_wired_into_both_convert_paths():
    src = inspect.getsource(nc)
    assert src.count("_add_skirt_collider_proxy(dst_path)") >= 2


def test_skirt_proxy_donor_must_be_CHAIN_DRIVEN():
    """The MIRROR of ButtCol's rule, and it matters just as much: cloning a
    kinematic block here would tag the cloth as a body collider and it would
    collide with the wrong set entirely. ButtCol needs a kinematic donor; this
    needs a Fabric one."""
    src = inspect.getsource(nc._add_skirt_collider_proxy)
    assert "_chain_mass(s_).max() > 1e-3" in src
    assert "if donor is None:" in src


def test_skirt_proxy_leaves_the_AUTHORED_proxy_alone():
    """It ADDS. The authored `Proxy` supports the skirt elsewhere (it spans
    z37.7-72.6) and replacing a working chain proxy is how a stable sim gets
    destabilised. Fabric shapes carry no-collide-with-tag Fabric, so the new
    proxy cannot fight the old one."""
    src = inspect.getsource(nc._add_skirt_collider_proxy)
    assert "createShapeFromData" in src
    for forbidden in ("set_verts", "override_verts"):
        assert forbidden not in src, "must not modify the authored proxy"


def test_skirt_proxy_only_fires_where_the_cloth_is_UNREPRESENTED():
    src = inspect.getsource(nc._add_skirt_collider_proxy)
    assert "unrep < _SKIRT_PROXY_MIN_UNREPRESENTED" in src
    assert nc._SKIRT_PROXY_MIN_UNREPRESENTED > 0 and nc._SKIRT_PROXY_GAP > 0


def test_skirt_proxy_all_or_nothing_with_byte_restore():
    src = inspect.getsource(nc._add_skirt_collider_proxy)
    assert "backup = p.read_bytes()" in src
    assert "atomic_write_bytes(p, backup)" in src
    assert "pre_extra <= _all_extra(nf2)" in src


def test_skirt_proxy_sources_from_SIMULATED_verts_only():
    """A proxy built from the rigid part of a garment would be pinned to the
    body and could not represent cloth at all."""
    src = inspect.getsource(nc._add_skirt_collider_proxy)
    assert "cm >= _SKIRT_PROXY_CHAIN_MIN" in src
    assert 0.0 < nc._SKIRT_PROXY_CHAIN_MIN <= 1.0
