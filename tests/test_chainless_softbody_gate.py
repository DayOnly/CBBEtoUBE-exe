"""Chainless-softbody gate (nif_convert._generate_hdt_xml_for_dst, only_loose).

A piece whose source shipped NO authored physics XML AND whose carriers have NO
detectable physics-chain bones (skinned only to standard skeleton + breast/butt
bones -- e.g. a rigid fur drape) must NOT get a GENERATED soft-body: with no
chains the generator emits per-vertex-shapes with ZERO constraints, which FSMP
simulates as unconstrained mass points that diverge into a spiky exploded mass.
The gate returns None (keep the piece rigid/kinematic) on the only_loose path.
A carrier set WITH real chain bones still generates. #fur-auto-smp
"""
import pytest

from src import nif_convert as nc
from src import hdt_xml_gen


class _Shape:
    def __init__(self, name, bones, verts=None):
        self.name = name
        self.bone_names = list(bones)
        self.verts = verts if verts is not None else [(0.0, 0.0, float(i))
                                                       for i in range(40)]


class _Nif:
    def __init__(self, shapes):
        self.shapes = shapes


def _wire(monkeypatch, carriers):
    """Make _generate_hdt_xml_for_dst see `carriers` and never touch pynifly/fp."""
    nif = _Nif(carriers)
    monkeypatch.setattr(nc, "_pynifly",
                        lambda: type("P", (), {"NifFile": staticmethod(
                            lambda filepath: nif)}))
    monkeypatch.setattr(nc, "_is_first_person_mesh", lambda *a, **k: False)
    monkeypatch.setattr(nc, "_pick_bodytri_carriers",
                        lambda nf, exclude_body=False: carriers)
    # never let the tight-softbody gate drop our carriers (test the chain gate)
    monkeypatch.setattr(nc, "_carrier_is_body_conforming", lambda *a, **k: False)
    wrote = {}
    monkeypatch.setattr(hdt_xml_gen, "write_armor_hdt_xml",
                        lambda path, shapes, **k: wrote.update(
                            path=path, chains=k.get("chains")))
    return wrote


# fur: standard skeleton + breast/butt bones only -> detect_physics_chains == []
FUR_BONES = ["NPC L Clavicle [LClv]", "NPC L UpperArm [LUar]", "NPC Spine1 [Spn1]",
             "L Breast01", "L Breast02", "NPC L Butt", "NPC Pelvis [Pelv]"]
# skirt-style underscore chain (detect_physics_chains groups Skirt_NN)
CHAIN_BONES = ["Skirt_00", "Skirt_01", "Skirt_02", "Skirt_03", "NPC Pelvis [Pelv]"]


def _meshdir(tmp_path):
    d = tmp_path / "meshes" / "armor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_chainless_generate_kept_rigid(tmp_path, monkeypatch):
    wrote = _wire(monkeypatch, [_Shape("Mod_FUR", FUR_BONES)])
    assert hdt_xml_gen.detect_physics_chains(FUR_BONES) == []
    out = nc._generate_hdt_xml_for_dst(_meshdir(tmp_path) / "ModArmor - Fur_1.nif",
                                       only_loose=True)
    assert out is None
    assert "path" not in wrote          # nothing generated


def test_chain_carrier_still_generates(tmp_path, monkeypatch):
    wrote = _wire(monkeypatch, [_Shape("Skirt", CHAIN_BONES)])
    assert hdt_xml_gen.detect_physics_chains(CHAIN_BONES)   # non-empty
    out = nc._generate_hdt_xml_for_dst(_meshdir(tmp_path) / "skirt_1.nif",
                                       only_loose=True)
    assert out is not None
    assert wrote.get("chains")          # chains passed to the writer


def test_chainless_cloth_only_regen_is_gated(tmp_path, monkeypatch):
    """A cloth-only NIF (no body collider) with NO detectable chains must be gated
    even on the REGEN path (only_loose=False). detect_physics_chains can't see
    space-separated custom chains, so such a piece would otherwise ship an
    unconstrained per-vertex soft-body with no collider = an FSMP explosion.
    #chainless-cloth-only"""
    wrote = _wire(monkeypatch, [_Shape("Mod_FUR", FUR_BONES)])
    assert hdt_xml_gen.detect_physics_chains(FUR_BONES) == []
    out = nc._generate_hdt_xml_for_dst(_meshdir(tmp_path) / "regen_1.nif",
                                       only_loose=False)
    assert out is None                 # gated -> static, not an exploding soft-body
    assert "path" not in wrote


def test_chain_carrier_regen_still_generates(tmp_path, monkeypatch):
    """The gate must NOT over-reach: a regen piece WITH detectable chains still
    generates physics (only chainless+no-collider is suppressed)."""
    wrote = _wire(monkeypatch, [_Shape("Skirt", CHAIN_BONES)])
    out = nc._generate_hdt_xml_for_dst(_meshdir(tmp_path) / "regen2_1.nif",
                                       only_loose=False)
    # not gated here: it proceeds to write (or hits a later gate), never our gate
    assert out is not None or "path" in wrote


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
