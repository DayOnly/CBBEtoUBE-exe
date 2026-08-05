"""#tri-reimport-refresh -- the BODYTRI must cover shapes that the LATE passes
re-import into the NIF after the TRI was first written.

The defect: pass 1 drops textureless collision / physics-framework shapes, the
TRI is generated from the saved NIF without them, and only afterwards does
`_finalize_hdt_physics` re-import them. At runtime BodyMorph then moves the
cloth to the player's preset while its collider and stabiliser stay at base
shape, and the physics chain sags off them.
"""
import numpy as np
import pytest

from src import nif_convert as nc


class _FakeShape:
    def __init__(self, name, verts):
        self.name = name
        self.verts = verts


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def _mk(monkeypatch, nif_shapes, tri_shape_names, *, generated):
    """Wire the helper's collaborators. `generated` = shape names the
    regenerated TRI would contain."""
    saved = {}

    class _FakeTriShape:
        def __init__(self, name):
            self.name = name

    class _FakeTri:
        def __init__(self, names):
            self.shapes = [_FakeTriShape(n) for n in names]

    monkeypatch.setattr(nc, "_pynifly",
                        lambda: type("M", (), {"NifFile": staticmethod(
                            lambda filepath: _FakeNif(nif_shapes))}))
    monkeypatch.setattr(nc, "_find_ube_body_osd", lambda: "osd")
    monkeypatch.setattr(nc, "_cached_osd_load", lambda p: object())
    monkeypatch.setattr(nc, "shape_body_offset", lambda s: np.zeros(3))
    monkeypatch.setattr(nc, "_extremity_vert_fraction", lambda s, n: None)
    monkeypatch.setattr(nc, "_pick_bodytri_carriers", lambda nf: [])
    monkeypatch.setattr(nc, "UBE_BODY_INJECT_NAMES", {"BaseShape"})

    import src.tri as tri_mod
    monkeypatch.setattr(tri_mod.TriFile, "load",
                        staticmethod(lambda p: _FakeTri(tri_shape_names)))
    import src.sliderset_gen as sg
    monkeypatch.setattr(sg, "generate_armor_tri",
                        lambda *a, **k: _FakeTri(generated))
    monkeypatch.setattr(nc, "atomic_tri_save",
                        lambda tri, path: saved.update(
                            names=[s.name for s in tri.shapes]))
    return saved


@pytest.fixture
def tri_file(tmp_path):
    p = tmp_path / "piece.tri"
    p.write_bytes(b"PIRT\x00\x00")
    return p


def test_reimported_collider_gets_morphs(monkeypatch, tri_file):
    """The regression: NIF has a re-imported collider the TRI lacks."""
    shapes = [_FakeShape(n, np.zeros((4, 3))) for n in
              ("BaseShape", "Cloth", "ColProxy", "Stabilizer")]
    # guard-the-guard: the fixture must actually reproduce the gap, or this
    # test would pass against the unfixed code too.
    assert {"ColProxy", "Stabilizer"} - {"BaseShape", "Cloth"}, \
        "fixture does not reproduce the missing-shape gap"

    saved = _mk(monkeypatch, shapes, ["BaseShape", "Cloth"],
                generated=["BaseShape", "Cloth", "ColProxy", "Stabilizer"])
    assert nc._refresh_armor_tri_after_reimport(tri_file, tri_file) is True
    assert "ColProxy" in saved["names"] and "Stabilizer" in saved["names"]


def test_noop_when_tri_already_complete(monkeypatch, tri_file):
    """No re-import happened -> must not rewrite (and must not pay the K-NN)."""
    shapes = [_FakeShape(n, np.zeros((4, 3))) for n in ("BaseShape", "Cloth")]

    def _boom(*a, **k):
        raise AssertionError("regenerated a TRI that needed no refresh")

    saved = _mk(monkeypatch, shapes, ["BaseShape", "Cloth"], generated=[])
    import src.sliderset_gen as sg
    monkeypatch.setattr(sg, "generate_armor_tri", _boom)
    assert nc._refresh_armor_tri_after_reimport(tri_file, tri_file) is False
    assert saved == {}


def test_noop_when_regen_gains_nothing(monkeypatch, tri_file):
    """A shape whose deltas all fall under min_delta legitimately stays out --
    do NOT rewrite the TRI just because it is still absent."""
    shapes = [_FakeShape(n, np.zeros((4, 3))) for n in
              ("BaseShape", "Cloth", "VirtualGround")]
    saved = _mk(monkeypatch, shapes, ["BaseShape", "Cloth"],
                generated=["BaseShape", "Cloth"])
    assert nc._refresh_armor_tri_after_reimport(tri_file, tri_file) is False
    assert saved == {}


def test_missing_tri_path_is_noop(monkeypatch, tmp_path):
    assert nc._refresh_armor_tri_after_reimport(tmp_path / "x.nif", None) is False
    assert nc._refresh_armor_tri_after_reimport(
        tmp_path / "x.nif", tmp_path / "nope.tri") is False
