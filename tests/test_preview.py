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

"""Smoke tests for src/preview.py — pure-numpy rendering path.

These don't load any NIF / TRI; they exercise the parts of the
renderer that have logic worth testing: BMP round-trip, the color
ramp, the projection bounds, and a black-box render through the
view pipeline using a synthetic shape.

Run: python -m pytest tests/test_preview.py -v
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import preview


# ---------------------------------------------------------------------------
# BMP round-trip
# ---------------------------------------------------------------------------

def _read_bmp(path: Path) -> np.ndarray:
    """Tiny BMP reader — just enough to verify what write_bmp produced.
    Assumes 24bpp, BI_RGB, positive-height (bottom-up)."""
    d = path.read_bytes()
    assert d[:2] == b"BM", "not a BMP"
    pixel_off = struct.unpack_from("<I", d, 10)[0]
    W = struct.unpack_from("<i", d, 18)[0]
    H = struct.unpack_from("<i", d, 22)[0]
    bpp = struct.unpack_from("<H", d, 28)[0]
    assert bpp == 24, f"expected 24bpp, got {bpp}"
    row_bytes = W * 3
    pad = (-row_bytes) & 3
    arr = np.frombuffer(d[pixel_off:], dtype=np.uint8)
    arr = arr.reshape(H, row_bytes + pad)[:, :row_bytes]
    arr = arr.reshape(H, W, 3)
    # BMP rows are stored bottom-up; flip to top-down.
    return arr[::-1].copy()


def test_bmp_roundtrip(tmp_path):
    """A pixel pattern survives write_bmp + a manual read."""
    np.random.seed(0)
    src = np.random.randint(0, 256, size=(13, 27, 3), dtype=np.uint8)
    p = tmp_path / "rt.bmp"
    preview.write_bmp(p, src)
    got = _read_bmp(p)
    assert got.shape == src.shape
    np.testing.assert_array_equal(got, src)


def test_bmp_row_padding(tmp_path):
    """A width that forces row padding (not a multiple of 4 px) still
    round-trips cleanly. Width=27 means row_bytes=81 -> pad=3."""
    src = np.tile(
        np.array([[10, 20, 30]], dtype=np.uint8), (5, 27, 1))
    p = tmp_path / "pad.bmp"
    preview.write_bmp(p, src)
    got = _read_bmp(p)
    np.testing.assert_array_equal(got, src)


# ---------------------------------------------------------------------------
# Color ramp
# ---------------------------------------------------------------------------

def test_color_ramp_endpoints():
    """0 -> blue, vmax/2 -> yellow, vmax -> red. Slack on exact values
    so the hand-tuned color stops can shift without breaking tests."""
    mags = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    bgr = preview.magnitude_to_bgr(mags, vmax=1.0)
    # 0 -> blue: high B, low R
    assert bgr[0, 0] > bgr[0, 2], f"expected blue at 0, got BGR={bgr[0]}"
    # 0.5 -> yellow: high G and high R, low B
    assert bgr[1, 1] > 150 and bgr[1, 2] > 150 and bgr[1, 0] < 100, \
        f"expected yellow at mid, got BGR={bgr[1]}"
    # 1 -> red: high R, low B
    assert bgr[2, 2] > bgr[2, 0], f"expected red at 1, got BGR={bgr[2]}"


def test_color_ramp_clips_above_vmax():
    """Magnitudes past vmax saturate at pure red (clip, not wrap)."""
    bgr = preview.magnitude_to_bgr(
        np.array([1.0, 2.0, 100.0], dtype=np.float32), vmax=1.0)
    # All three rows should sit at the same end-of-ramp color.
    np.testing.assert_array_equal(bgr[0], bgr[1])
    np.testing.assert_array_equal(bgr[0], bgr[2])


def test_color_ramp_handles_zero_vmax():
    """vmax=0 must not divide-by-zero — every vert maps to blue end."""
    bgr = preview.magnitude_to_bgr(
        np.array([0.0, 1.0, 5.0], dtype=np.float32), vmax=0.0)
    # All identical (clamped to 0 normalization).
    assert (bgr == bgr[0]).all()


# ---------------------------------------------------------------------------
# Global UV bounds
# ---------------------------------------------------------------------------

def test_global_uv_bounds_covers_all_views():
    """The union bounds must contain every per-view projection."""
    rng = np.random.default_rng(42)
    verts = rng.uniform(-20, 20, size=(200, 3)).astype(np.float32)
    umin, umax, vmin, vmax = preview._global_uv_bounds(verts)
    for view in preview.VIEWS:
        u, v = preview.project_2d(verts, view)
        assert u.min() >= umin - 1e-6, (
            f"view {view}: u.min={u.min()} < umin={umin}")
        assert u.max() <= umax + 1e-6, (
            f"view {view}: u.max={u.max()} > umax={umax}")
        assert v.min() >= vmin - 1e-6
        assert v.max() <= vmax + 1e-6


def test_global_uv_bounds_handles_long_y_axis():
    """If a NIF is long along Y (e.g. an arrow at rest), the SIDE view
    span must be reflected in the bounds — not silently clipped."""
    # Stretched along Y, narrow along X.
    verts = np.array([
        [-1, -50, 0], [+1, +50, 0], [-1, +50, 0], [+1, -50, 0],
    ], dtype=np.float32)
    umin, umax, vmin, vmax = preview._global_uv_bounds(verts)
    # Side view u = -Y; range = [-50, +50]. Front/back u = ±X; range
    # = [-1, +1]. Union: [-50, +50].
    assert umin <= -50 + 1e-6
    assert umax >= 50 - 1e-6


# ---------------------------------------------------------------------------
# View renderer
# ---------------------------------------------------------------------------

def test_render_view_draws_something():
    """A shape with a few verts produces non-background pixels."""
    verts = np.array([
        [0, 0, 0], [10, 0, 50], [-10, 0, 50], [0, 0, 100],
    ], dtype=np.float32)
    mag = np.array([0.0, 0.5, 1.0, 2.0], dtype=np.float32)
    shape = preview._PreviewShape("test", verts, mag)
    bounds = preview._global_uv_bounds(verts)
    img = preview._render_view(
        [shape], preview.VIEW_FRONT,
        panel_size=(80, 120),
        bounds=bounds,
        color_vmax=2.0,
    )
    bg = np.array(preview.BG_COLOR, dtype=np.uint8)
    non_bg = (img != bg).any(axis=2)
    assert non_bg.sum() >= 4, (
        f"expected at least 4 non-bg pixels, got {non_bg.sum()}")


def test_render_view_high_mag_wins_overlap():
    """When a high-mag vert and a low-mag vert from different shapes
    project to the same pixel, the high-mag color must be on top —
    that's the whole QA point. Regression guard for the previous
    bug where shape iteration order determined the visible color.

    Two verts per shape, one shared (0,0,50) overlap target + one
    anchor at a corner so bounds are non-degenerate."""
    anchor = [-50, 0, 0]
    target = [0, 0, 50]
    v = np.array([anchor, target], dtype=np.float32)
    high = preview._PreviewShape(
        "hot", v, np.array([0.0, 10.0], dtype=np.float32))   # red at target
    low = preview._PreviewShape(
        "cold", v, np.array([0.0, 0.1], dtype=np.float32))   # blue at target

    bounds = preview._global_uv_bounds(np.vstack([high.verts, low.verts]))
    # Render twice with reversed shape order; both must contain a
    # red-leaning pixel at the same image position (the shared target).
    bg = np.array(preview.BG_COLOR, dtype=np.uint8)
    red_locations = []
    for shapes in ([high, low], [low, high]):
        img = preview._render_view(
            shapes, preview.VIEW_FRONT,
            panel_size=(80, 80), bounds=bounds, color_vmax=10.0)
        non_bg = (img != bg).any(axis=2)
        ys, xs = np.where(non_bg)
        assert len(ys) > 0, f"no painted pixel for order={shapes}"
        # Red-leaning: B-channel < R-channel by a clear margin.
        red_mask = img[ys, xs, 0].astype(int) + 30 < img[ys, xs, 2].astype(int)
        assert red_mask.any(), (
            f"order={shapes}: no red-leaning pixel; "
            f"first painted BGR={img[ys[0], xs[0]].tolist()}")
        # Record (y, x) of one red pixel — must match across orders.
        first = np.argmax(red_mask)
        red_locations.append((int(ys[first]), int(xs[first])))
    # The high-mag overlap pixel must land in the same image cell
    # whether 'hot' or 'cold' is rendered first.
    assert red_locations[0] == red_locations[1], (
        f"red overlap drifted with shape order: {red_locations}")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def test_resolve_bodytri_walks_to_meshes_ancestor(tmp_path):
    """Given a NIF inside <root>/meshes/foo/bar.nif and a BODYTRI
    string 'foo/morphs.tri', the resolver should find
    <root>/meshes/foo/morphs.tri."""
    meshes = tmp_path / "meshes" / "foo"
    meshes.mkdir(parents=True)
    nif = meshes / "bar.nif"; nif.write_bytes(b"")
    tri = meshes / "morphs.tri"; tri.write_bytes(b"")
    resolved = preview._resolve_bodytri(nif, "foo\\morphs.tri")
    assert resolved == tri


def test_resolve_bodytri_returns_none_for_missing(tmp_path):
    """If the referenced TRI doesn't exist, return None — caller
    surfaces that as the 'BODYTRI not resolvable' warning."""
    meshes = tmp_path / "meshes" / "foo"
    meshes.mkdir(parents=True)
    nif = meshes / "bar.nif"; nif.write_bytes(b"")
    assert preview._resolve_bodytri(nif, "foo\\nope.tri") is None


# Note: _copy_external_tri_into_output and _find_armor_tri_ref were
# deleted on 2026-05-25 along with the prebuilt-UBE-armor-TRI-reuse
# path. Their tests went with them. The converter now always
# auto-generates the BODYTRI/TRI from CBBE source + UBE body OSD.


# ---------------------------------------------------------------------------
# Body-delta warp
# ---------------------------------------------------------------------------

def test_warp_moves_armor_with_body():
    """When the body delta is uniform +Z (everything moved up 1u),
    armor verts should also move +1u in Z. Sanity check that the
    K-NN + IDW pipeline preserves a constant deformation field."""
    from src.nif_convert import warp_armor_by_body_delta

    # Synthetic body: 10x10 grid of verts at z=0, x and y in [-5, 5]
    xs, ys = np.meshgrid(np.linspace(-5, 5, 10), np.linspace(-5, 5, 10))
    cbbe_body = np.column_stack([xs.ravel(), ys.ravel(),
                                  np.zeros(100)]).astype(np.float64)
    # Uniform body delta: +1u in Z
    delta = np.zeros_like(cbbe_body); delta[:, 2] = 1.0

    armor = np.array([[0, 0, 2], [3, -2, 1], [-4, 4, 0]],
                     dtype=np.float64)
    warped = warp_armor_by_body_delta(armor, cbbe_body, delta)

    # Armor should have moved +1u in Z, X/Y unchanged.
    expected = armor.copy(); expected[:, 2] += 1.0
    np.testing.assert_allclose(warped, expected, atol=1e-4)


def test_warp_handles_local_deformation():
    """When body delta is localized (e.g. only one region moved),
    armor verts near that region should move with it, but verts far
    away should be unaffected."""
    from src.nif_convert import warp_armor_by_body_delta

    # Two body clusters: one at (0,0,0) and one at (50,0,0)
    body_a = np.array([[0, 0, 0], [0.1, 0.1, 0], [-0.1, 0, 0]],
                      dtype=np.float64)
    body_b = np.array([[50, 0, 0], [50.1, 0.1, 0], [49.9, 0, 0]],
                      dtype=np.float64)
    cbbe_body = np.vstack([body_a, body_b])

    # Only cluster A moves (+5u in X); cluster B static.
    delta = np.zeros_like(cbbe_body)
    delta[:3, 0] = 5.0  # cluster A verts move +5 X

    # Armor verts: one near A, one near B
    armor = np.array([[0, 0, 1], [50, 0, 1]], dtype=np.float64)
    warped = warp_armor_by_body_delta(armor, cbbe_body, delta, k=3)

    # Armor near A: should move ~+5 X (averaged over 3 A-cluster verts)
    assert warped[0, 0] - armor[0, 0] > 4.5, (
        f"armor near A should move ~+5 X, got delta={warped[0, 0] - armor[0, 0]:.2f}")
    # Armor near B: should be unaffected
    assert abs(warped[1, 0] - armor[1, 0]) < 0.5, (
        f"armor near B should not move, got delta={warped[1, 0] - armor[1, 0]:.2f}")


def test_warp_k_equals_1():
    """K=1 nearest-neighbor — armor inherits the closest body vert's
    delta exactly. Verifies the k=1 branch."""
    from src.nif_convert import warp_armor_by_body_delta

    cbbe_body = np.array([[0, 0, 0], [10, 0, 0]], dtype=np.float64)
    delta = np.array([[1, 0, 0], [-3, 0, 0]], dtype=np.float64)

    # Armor vert at (1,0,1) is closer to body vert 0 -> +1 X.
    # Armor vert at (9,0,1) is closer to body vert 1 -> -3 X.
    armor = np.array([[1, 0, 1], [9, 0, 1]], dtype=np.float64)
    warped = warp_armor_by_body_delta(armor, cbbe_body, delta, k=1)
    np.testing.assert_allclose(warped[0], [2, 0, 1], atol=1e-4)
    np.testing.assert_allclose(warped[1], [6, 0, 1], atol=1e-4)


# ---------------------------------------------------------------------------
# HDT-SMP XML generator (Phase A)
# ---------------------------------------------------------------------------

def test_classify_bone_threshold():
    """Skeleton + 3BA scale bones get threshold 1.0; everything else 0.3."""
    from src.hdt_xml_gen import classify_bone_threshold
    # Scale bones
    assert classify_bone_threshold("L Breast01") == 1.0
    assert classify_bone_threshold("NPC L Butt") == 1.0
    assert classify_bone_threshold("NPC Belly") == 1.0
    assert classify_bone_threshold("Clitoral1") == 1.0
    # Skeleton bones
    assert classify_bone_threshold("NPC Spine [Spn0]") == 1.0
    assert classify_bone_threshold("NPC Pelvis [Pelv]") == 1.0
    assert classify_bone_threshold("NPC L Thigh [LThg]") == 1.0
    # Custom / unknown bones get the low threshold
    assert classify_bone_threshold("obiobiobi_boob_R_01") == 0.3
    assert classify_bone_threshold("SkirtF 1_01") == 0.3


def test_generate_armor_hdt_xml_basic():
    """Generator produces parseable XML with the right structure."""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import generate_armor_hdt_xml

    cloth_shapes = [
        ("Cuirass", ["NPC Spine [Spn0]", "NPC Pelvis [Pelv]",
                     "L Breast01", "R Breast01"]),
        ("Tasset_2", ["NPC Pelvis [Pelv]", "NPC L Butt", "NPC R Butt",
                      "NPC L Thigh [LThg]"]),
    ]
    xml_str = generate_armor_hdt_xml(cloth_shapes,
                                       body_collision_shape_name="VirtualBody")
    # Parses cleanly
    root = ET.fromstring(xml_str)
    assert root.tag == "system"
    # Bones declared (union of both shapes' bones, deduped)
    bone_names = {b.get("name") for b in root.findall("bone")}
    assert "NPC Spine [Spn0]" in bone_names
    assert "NPC Pelvis [Pelv]" in bone_names
    assert "L Breast01" in bone_names
    assert "NPC L Butt" in bone_names
    # Body shape exists with correct tag
    body_shape = root.find("per-triangle-shape")
    assert body_shape is not None
    assert body_shape.get("name") == "VirtualBody"
    assert body_shape.find("tag").text == "body"
    # Body can collide with both cloth tags
    body_collide_tags = {t.text for t in body_shape.findall("can-collide-with-tag")}
    assert body_collide_tags == {"cloth1", "cloth2"}
    # Two cloth shapes
    cloths = root.findall("per-vertex-shape")
    assert len(cloths) == 2
    assert cloths[0].get("name") == "Cuirass"
    assert cloths[1].get("name") == "Tasset_2"
    # Each cloth collides with body
    for c in cloths:
        tags = [t.text for t in c.findall("can-collide-with-tag")]
        assert "body" in tags


def test_generate_armor_hdt_xml_weight_thresholds():
    """Scale bones get threshold 1.0, custom bones get 0.3 in output."""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import generate_armor_hdt_xml

    cloth_shapes = [
        ("Skirt", ["NPC Pelvis [Pelv]", "L Breast01", "SkirtChain_01"]),
    ]
    xml_str = generate_armor_hdt_xml(cloth_shapes)
    root = ET.fromstring(xml_str)

    cloth = root.find("per-vertex-shape")
    thresholds = {wt.get("bone"): float(wt.text)
                  for wt in cloth.findall("weight-threshold")}
    assert thresholds["NPC Pelvis [Pelv]"] == 1.0
    assert thresholds["L Breast01"] == 1.0
    assert thresholds["SkirtChain_01"] == 0.3


def test_generate_armor_hdt_xml_handles_special_chars():
    """Bone names with [], &, etc. get XML-escaped."""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import generate_armor_hdt_xml

    # The "NPC L Thigh [LThg]" style bracket names are standard.
    cloth_shapes = [("Pants", ["NPC L Thigh [LThg]", "NPC R Thigh [RThg]"])]
    xml_str = generate_armor_hdt_xml(cloth_shapes)
    # If escape is broken this raises ParseError
    root = ET.fromstring(xml_str)
    bones = {b.get("name") for b in root.findall("bone")}
    assert "NPC L Thigh [LThg]" in bones
    assert "NPC R Thigh [RThg]" in bones


def test_write_armor_hdt_xml_round_trip(tmp_path):
    """Write + re-parse produces equivalent structure."""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import write_armor_hdt_xml

    out = tmp_path / "test_armor.xml"
    write_armor_hdt_xml(
        out,
        [("Cuirass", ["NPC Spine [Spn0]", "L Breast01"])],
        body_collision_shape_name="VirtualBody",
    )
    assert out.is_file()
    root = ET.parse(out).getroot()
    assert root.tag == "system"
    assert len(root.findall("per-vertex-shape")) == 1


def test_validate_armor_hdt_xml_clean(tmp_path):
    """A correctly generated XML validates clean against its source bones."""
    from src.hdt_xml_gen import write_armor_hdt_xml, validate_armor_hdt_xml

    bones = ["NPC Spine [Spn0]", "NPC Pelvis [Pelv]", "L Breast01"]
    out = tmp_path / "ok.xml"
    write_armor_hdt_xml(out, [("Cuirass", bones)])
    warnings = validate_armor_hdt_xml(out, bones)
    assert warnings == [], f"unexpected warnings: {warnings}"


def test_validate_armor_hdt_xml_missing_bones(tmp_path):
    """If the XML references bones the NIF doesn't have, validator flags it."""
    from src.hdt_xml_gen import write_armor_hdt_xml, validate_armor_hdt_xml

    xml_bones = ["NPC Spine [Spn0]", "L Breast01", "BogusBone_99"]
    out = tmp_path / "bad.xml"
    write_armor_hdt_xml(out, [("Cuirass", xml_bones)])
    # NIF only has these — validator must flag BogusBone_99
    nif_bones = {"NPC Spine [Spn0]", "L Breast01"}
    warnings = validate_armor_hdt_xml(out, nif_bones)
    assert any("BogusBone_99" in w for w in warnings), (
        f"expected BogusBone_99 warning, got: {warnings}")


def test_validate_armor_hdt_xml_missing_file(tmp_path):
    """Validator reports the file-not-found case cleanly."""
    from src.hdt_xml_gen import validate_armor_hdt_xml
    warnings = validate_armor_hdt_xml(tmp_path / "does_not_exist.xml",
                                       ["NPC Spine [Spn0]"])
    assert any("missing on disk" in w for w in warnings)


def test_pick_body_collision_shape_name():
    """Prefer VirtualBody, fall back to BaseShape, None if neither."""
    from src.hdt_xml_gen import pick_body_collision_shape_name
    assert pick_body_collision_shape_name(
        ["Cuirass", "VirtualBody", "BaseShape"]) == "VirtualBody"
    assert pick_body_collision_shape_name(["Cuirass", "BaseShape"]) == "BaseShape"
    assert pick_body_collision_shape_name(["Cuirass", "OnlyArmor"]) is None


def test_unconstrained_collision_pair_predicate():
    """The generated-XML gate that prevents the FSMP equip-CTD: a cloth +
    body collider with NO chain is the crash pattern (skip); cloth-only
    (no collider) and any constrained chain are stable (emit)."""
    from src.nif_convert import _is_unconstrained_collision_pair
    from src.hdt_xml_gen import PhysicsChain
    # cloth + per-triangle body collider, no chain -> crash pattern (skip)
    assert _is_unconstrained_collision_pair("VirtualBody", [])
    assert _is_unconstrained_collision_pair("BaseShape", None)
    # cloth-only (no body collider) -> no pair to diverge against -> keep
    assert not _is_unconstrained_collision_pair(None, [])
    assert not _is_unconstrained_collision_pair(None, None)
    # constrained chain (even WITH a collider) is properly simulated -> keep
    chain = [PhysicsChain(prefix="Skirt 1",
                          bones=["Skirt 1_00", "Skirt 1_01", "Skirt 1_02"])]
    assert not _is_unconstrained_collision_pair("VirtualBody", chain)
    assert not _is_unconstrained_collision_pair(None, chain)


def test_is_high_velocity_bone():
    """Breast / butt / belly + legs flagged; spine/clavicle/anatomy not."""
    from src.hdt_xml_gen import is_high_velocity_bone
    # Breast physics chain
    assert is_high_velocity_bone("L Breast01")
    assert is_high_velocity_bone("R Breast03")
    # Butt scale bones
    assert is_high_velocity_bone("NPC L Butt")
    assert is_high_velocity_bone("NPC R Butt")
    # Belly
    assert is_high_velocity_bone("NPC Belly")
    # Legs ARE high-velocity now: a loose skirt/robe tunnels through the
    # thighs/calves when walking -> widened leg collision skin. #cloth-clip
    assert is_high_velocity_bone("NPC L Thigh [LThg]")
    assert is_high_velocity_bone("NPC R Calf [RClf]")
    # NOT high-velocity — should not get expanded margin
    assert not is_high_velocity_bone("NPC Spine [Spn0]")
    assert not is_high_velocity_bone("NPC L Clavicle [LClv]")
    assert not is_high_velocity_bone("Clitoral1")  # anatomy, not high-velocity


def test_detect_physics_chains_basic():
    """Group bones by <prefix>_NN; chains with <2 bones rejected."""
    from src.hdt_xml_gen import detect_physics_chains
    bones = [
        "NPC Spine [Spn0]",       # not a chain
        "Skirt 1_00", "Skirt 1_01", "Skirt 1_02",  # chain "Skirt 1"
        "Skirt 2_00", "Skirt 2_01",                 # chain "Skirt 2"
        "obiobiobi_FR_00", "obiobiobi_FR_01",
        "obiobiobi_FR_02", "obiobiobi_FR_03",      # chain "obiobiobi_FR"
        "NPC L Pauldron",         # not a chain (no _NN suffix)
        "Single 1_00",            # <2 bones; rejected
    ]
    chains = detect_physics_chains(bones)
    chain_prefixes = sorted(c.prefix for c in chains)
    assert chain_prefixes == ["Skirt 1", "Skirt 2", "obiobiobi_FR"]
    skirt1 = next(c for c in chains if c.prefix == "Skirt 1")
    assert skirt1.bones == ["Skirt 1_00", "Skirt 1_01", "Skirt 1_02"]


def test_detect_physics_chains_skips_breast_pattern():
    """L Breast01/02/03 match _NN by coincidence but aren't physics
    chains — the body's master XML constrains them. Detection skips."""
    from src.hdt_xml_gen import detect_physics_chains
    bones = ["L Breast01", "L Breast02", "L Breast03",
             "R Breast01", "R Breast02"]
    chains = detect_physics_chains(bones)
    assert chains == []


def test_detect_physics_chains_orders_by_index():
    """Chain bones are sorted by their _NN index, not lexicographically.
    Important: _10 sorts BEFORE _2 lexicographically but AFTER numerically."""
    from src.hdt_xml_gen import detect_physics_chains
    bones = ["Chain 1_10", "Chain 1_02", "Chain 1_01", "Chain 1_00"]
    chains = detect_physics_chains(bones)
    assert len(chains) == 1
    assert chains[0].bones == ["Chain 1_00", "Chain 1_01",
                                "Chain 1_02", "Chain 1_10"]


def test_generate_xml_emits_chain_physics():
    """When chains are passed, XML includes:
       - anchor bones with mass=0
       - dynamic bones with mass>0 (via bone-default)
       - <constraint-group> with sequential <generic-constraint>
       - cloth weight-threshold references at 0.3"""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import generate_armor_hdt_xml, PhysicsChain
    chains = [PhysicsChain(prefix="Skirt 1", bones=[
        "Skirt 1_00", "Skirt 1_01", "Skirt 1_02",
    ])]
    cloth_shapes = [(
        "Skirt",
        ["NPC Pelvis [Pelv]", "Skirt 1_00", "Skirt 1_01", "Skirt 1_02"],
    )]
    xml_str = generate_armor_hdt_xml(cloth_shapes, chains=chains)
    root = ET.fromstring(xml_str)

    # All chain bones declared
    bone_names = {b.get("name") for b in root.findall("bone")}
    assert "Skirt 1_00" in bone_names
    assert "Skirt 1_01" in bone_names
    assert "Skirt 1_02" in bone_names

    # At least one <constraint-group> with 2 generic-constraints
    # (3 bones -> 2 sequential constraints)
    groups = root.findall("constraint-group")
    assert len(groups) >= 1
    constraints = groups[0].findall("generic-constraint")
    assert len(constraints) == 2
    # bodyA / bodyB pair points at consecutive chain bones
    pairs = [(c.get("bodyA"), c.get("bodyB")) for c in constraints]
    assert ("Skirt 1_01", "Skirt 1_00") in pairs
    assert ("Skirt 1_02", "Skirt 1_01") in pairs

    # Cloth weight-threshold for chain bone = 0.3, for body bone = 1.0
    cloth = root.find("per-vertex-shape")
    thresholds = {wt.get("bone"): float(wt.text)
                  for wt in cloth.findall("weight-threshold")}
    assert thresholds["Skirt 1_00"] == 0.3
    assert thresholds["Skirt 1_02"] == 0.3
    assert thresholds["NPC Pelvis [Pelv]"] == 1.0


def test_generate_xml_emits_margin_multiplier_on_high_velocity_bones():
    """High-velocity bones get a <margin-multiplier> inline; others
    stay as self-closing <bone> tags."""
    import xml.etree.ElementTree as ET
    from src.hdt_xml_gen import (generate_armor_hdt_xml,
                                  HIGH_VELOCITY_MARGIN_MULTIPLIER)
    cloth_shapes = [(
        "Cuirass",
        ["NPC Spine [Spn0]", "L Breast01", "NPC L Butt", "NPC L Thigh [LThg]"],
    )]
    xml_str = generate_armor_hdt_xml(cloth_shapes)
    root = ET.fromstring(xml_str)
    # Walk top-level bone declarations
    bones_by_name = {b.get("name"): b for b in root.findall("bone")}
    # Spine: self-closing, no children
    assert len(list(bones_by_name["NPC Spine [Spn0]"])) == 0
    # L Breast01: has margin-multiplier child
    breast_mm = bones_by_name["L Breast01"].find("margin-multiplier")
    assert breast_mm is not None
    assert float(breast_mm.text) == HIGH_VELOCITY_MARGIN_MULTIPLIER
    # NPC L Butt: same
    butt_mm = bones_by_name["NPC L Butt"].find("margin-multiplier")
    assert butt_mm is not None
    assert float(butt_mm.text) == HIGH_VELOCITY_MARGIN_MULTIPLIER
    # NPC L Thigh: legs are high-velocity now -> HAS margin-multiplier
    # (skirt-clips-through-legs fix). Spine (asserted above) is the
    # no-multiplier negative case.
    thigh_mm = bones_by_name["NPC L Thigh [LThg]"].find("margin-multiplier")
    assert thigh_mm is not None
    assert float(thigh_mm.text) == HIGH_VELOCITY_MARGIN_MULTIPLIER
