"""Adaptive (morph-aware) armor clearance in inflate_armor_outward.

Clearance must be tight where the body is STATIC (low morph amplitude) and grow
toward the uniform cap only where the body can morph OUTWARD (breast/butt/belly).
Provably <= the uniform magnitude everywhere, so it can never add poke-through.
"""
import numpy as np
from src import nif_convert


def _push_distance(before, after, normal):
    """Signed push along the (unit) body normal."""
    return float(np.dot((after - before)[0], normal))


def test_adaptive_clearance_tightens_static_keeps_morph():
    # Two body verts far apart, each with an outward +Z normal.
    body = np.array([[0.0, 0.0, 0.0],     # static region
                     [50.0, 0.0, 0.0]],   # morph-heavy region
                    dtype=np.float64)
    normals = np.array([[0.0, 0.0, 1.0],
                        [0.0, 0.0, 1.0]], dtype=np.float64)
    # Morph amplitude: vert 0 static (0u), vert 1 morphs 4u outward.
    amp = np.array([0.0, 4.0], dtype=np.float64)

    # One armor vert sitting right on each body vert (so falloff = full).
    armor_static = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    armor_morph = np.array([[50.0, 0.0, 0.0]], dtype=np.float64)

    cap = 0.7
    base = nif_convert.ADAPTIVE_CLEARANCE_BASE
    factor = nif_convert.ADAPTIVE_CLEARANCE_MORPH_FACTOR

    # Pin morph_max == cap so this test isolates the base/cap clamp (the
    # morph-zone expansion above the slot magnitude is covered separately).
    out_s = nif_convert.inflate_armor_outward(
        armor_static, body, magnitude=cap, close_threshold=2.0,
        body_normals=normals, morph_amplitude=amp, morph_max=cap)
    out_m = nif_convert.inflate_armor_outward(
        armor_morph, body, magnitude=cap, close_threshold=2.0,
        body_normals=normals, morph_amplitude=amp, morph_max=cap)

    push_s = _push_distance(armor_static, out_s, normals[0])
    push_m = _push_distance(armor_morph, out_m, normals[1])

    # Static vert pushed by ~base (tight), morph vert pushed to the cap.
    assert abs(push_s - base) < 1e-3, f"static push {push_s} != base {base}"
    assert abs(push_m - cap) < 1e-3, f"morph push {push_m} != cap {cap}"
    # Adaptive must never exceed the uniform cap.
    assert push_s <= cap + 1e-6 and push_m <= cap + 1e-6
    # And static must be strictly tighter than the morph zone.
    assert push_s < push_m
    print("  test_adaptive_clearance_tightens_static_keeps_morph OK")


def test_adaptive_falls_back_to_uniform_without_amp():
    body = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    normals = np.array([[0.0, 0.0, 1.0]], dtype=np.float64)
    armor = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    out = nif_convert.inflate_armor_outward(
        armor, body, magnitude=0.7, close_threshold=2.0,
        body_normals=normals, morph_amplitude=None)
    push = _push_distance(armor, out, normals[0])
    assert abs(push - 0.7) < 1e-3, f"uniform push {push} != 0.7"
    print("  test_adaptive_falls_back_to_uniform_without_amp OK")


def test_disable_flag_restores_uniform(monkeypatch=None):
    body = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    normals = np.array([[0.0, 0.0, 1.0]], dtype=np.float64)
    amp = np.array([4.0], dtype=np.float64)
    armor = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    saved = nif_convert.ADAPTIVE_CLEARANCE_ENABLED
    try:
        nif_convert.ADAPTIVE_CLEARANCE_ENABLED = False
        out = nif_convert.inflate_armor_outward(
            armor, body, magnitude=0.7, close_threshold=2.0,
            body_normals=normals, morph_amplitude=amp)
    finally:
        nif_convert.ADAPTIVE_CLEARANCE_ENABLED = saved
    push = _push_distance(armor, out, normals[0])
    assert abs(push - 0.7) < 1e-3, "disable flag should restore uniform magnitude"
    print("  test_disable_flag_restores_uniform OK")


def test_morph_zone_clearance_exceeds_slot_magnitude():
    # The body can morph a vert outward 4u; the cuirass can't follow vertex
    # morphs (non-carrier limitation), so it needs clearance ABOVE the flat slot
    # magnitude there. morph_max lifts the cap so the morph zone gets it.
    body = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    normals = np.array([[0.0, 0.0, 1.0]], dtype=np.float64)
    amp = np.array([4.0], dtype=np.float64)
    armor = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
    slot_mag = 0.7
    out = nif_convert.inflate_armor_outward(
        armor, body, magnitude=slot_mag, close_threshold=2.0,
        body_normals=normals, morph_amplitude=amp, morph_max=1.1,
        base_magnitude=0.25, morph_factor=0.20)
    push = _push_distance(armor, out, normals[0])
    assert push > slot_mag + 1e-3, \
        f"morph-zone clearance {push} must exceed slot magnitude {slot_mag}"
    assert abs(push - (0.25 + 0.20 * 4.0)) < 1e-3, f"got {push}"
    assert push <= 1.1 + 1e-6, "must not exceed morph_max cap"
    print("  test_morph_zone_clearance_exceeds_slot_magnitude OK")


test_adaptive_clearance_tightens_static_keeps_morph()
test_adaptive_falls_back_to_uniform_without_amp()
test_disable_flag_restores_uniform()
test_morph_zone_clearance_exceeds_slot_magnitude()
