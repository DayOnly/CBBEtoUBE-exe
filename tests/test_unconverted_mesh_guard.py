"""Postflight guard: validate_patch flags an ARMA linked to a SOURCE mesh when a
converted !UBE mesh EXISTS for it (the coverage-routing regression that once
shipped 204 broken armors -- armour wears the un-converted source on the UBE
body). #unconverted-mesh-linked. A source path with NO !UBE mesh (a real vanilla/
accessory) must NOT flag, and a correctly-redirected !UBE path must not either.
"""
import struct

from src import esp
from src.esp import encode_subrecord, encode_zstring
from src import ube_patcher


def _save_arma(tmp_path, mod3_path):
    payload = (encode_subrecord(b"EDID", encode_zstring("TestAA"))
               + encode_subrecord(b"BOD2", struct.pack("<II", 1 << 2, 0))
               + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))
               + encode_subrecord(b"MOD3", encode_zstring(mod3_path)))
    arma = esp.Record(sig=b"ARMA", flags=0, formid=(1 << 24) | 0x800,
                      timestamp_vc=0, version_unk=0x2C, payload=payload)
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"],
                                      next_object_id=0xFFFFFF),
                groups=[esp.Group(label=b"ARMA", records=[arma])])
    p = tmp_path / "t.esp"
    e.save(p)
    return p


def _touch(meshes_root, rel):
    f = meshes_root / rel.replace("\\", "/")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x00")


def test_source_path_with_converted_mesh_flags(tmp_path):
    meshes = tmp_path / "meshes"
    # the converted mesh EXISTS at !UBE\<path> but the ARMA points to the source path
    _touch(meshes, "!UBE/armor/foo/bar_1.nif")
    p = _save_arma(tmp_path, "armor\\foo\\bar_1.nif")
    w = ube_patcher.validate_patch(p, meshes_root=meshes)
    assert any(x.startswith("unconverted-mesh-linked") for x in w), w
    # and it is registered as a build-failing (CTD-tier) finding
    assert "unconverted-mesh-linked" in ube_patcher._POSTFLIGHT_CTD_PREFIXES


def test_source_path_without_converted_mesh_is_clean(tmp_path):
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    # a real vanilla/accessory source path with NO !UBE mesh -> must NOT flag
    p = _save_arma(tmp_path, "armor\\iron\\helmet.nif")
    w = ube_patcher.validate_patch(p, meshes_root=meshes)
    assert not any(x.startswith("unconverted-mesh-linked") for x in w), w


def test_correct_ube_path_is_clean(tmp_path):
    meshes = tmp_path / "meshes"
    _touch(meshes, "!UBE/armor/foo/bar_1.nif")
    # correctly redirected to the !UBE mesh -> no unconverted flag
    p = _save_arma(tmp_path, "!UBE\\armor\\foo\\bar_1.nif")
    w = ube_patcher.validate_patch(p, meshes_root=meshes)
    assert not any(x.startswith("unconverted-mesh-linked") for x in w), w


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
