"""Guard for #177: a draping cape/cloak that rides the HAIR/head slots (31/41/43)
so it hides the hair (a hood+cape, e.g. Traveling Mage's TMage_Cape /
TMage_CapeHair) must still be admitted as a convertible armour piece. The slot
allowlist excludes hair slots to skip wigs/beards, which silently dropped the
cape -> it rendered un-converted (CBBE-shaped, no physics) on the UBE body.
Plain wigs/hair (no cloak keyword) must stay excluded."""
import struct
from src import esp
from src.esp import encode_subrecord, encode_zstring
from src.auto_convert import _player_armor_mesh_bases

DEFAULT_RACE = 0x00000019            # Skyrim.esm DefaultRace


def _bits(*slots):
    v = 0
    for s in slots:
        v |= 1 << (s - 30)
    return v


def _arma(fid, edid, slot_bits, mesh):
    payload = (
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot_bits, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT_RACE))
        + encode_subrecord(b"MOD3", encode_zstring(mesh))
    )
    return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)


def _write(tmp, armas):
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=armas)])
    e.save(tmp / "TMage.esp")


def test_hair_slot_cape_is_admitted(tmp_path):
    _write(tmp_path, [
        _arma(0x801, "BodyTMage", _bits(32),
              "Fuse00\\Armor\\TMage\\TMage_Body_Female_1.nif"),
        _arma(0x802, "HoodTMage", _bits(31, 41, 43),   # cape on HAIR slots
              "Fuse00\\Armor\\TMage\\TMage_Cape_Female_1.nif"),
        _arma(0x803, "WigThing", _bits(31),            # plain wig
              "Fuse00\\Armor\\TMage\\TMageWig_Female_1.nif"),
    ])
    bases = _player_armor_mesh_bases(tmp_path, include_candidate_slots=True)
    assert any("tmage_body" in b for b in bases), bases    # body (slot 32) kept
    assert any("tmage_cape" in b for b in bases), bases    # hair-slot cape admitted
    assert not any("tmagewig" in b for b in bases), bases  # plain wig excluded


def test_non_cloak_on_hair_slot_stays_excluded(tmp_path):
    # Same hair slots, but the mesh isn't named like a cloak -> still excluded
    # (so we don't start converting actual hoods/hair).
    _write(tmp_path, [
        _arma(0x801, "HoodTMage", _bits(31, 41, 43),
              "Fuse00\\Armor\\TMage\\TMage_Hood_Female_1.nif"),
    ])
    bases = _player_armor_mesh_bases(tmp_path, include_candidate_slots=True)
    assert not any("tmage_hood" in b for b in bases), bases


def test_stormcloak_folder_not_a_false_positive(tmp_path):
    # A HELMET in a 'Stormcloaks' folder: the PATH contains the substring
    # 'cloak' ("storm-CLOAK-s") but the FILENAME is 'Helmet' -> must NOT be
    # admitted as a cloak (matching basename, not full path).
    _write(tmp_path, [
        _arma(0x801, "GuardsHelmet", _bits(31, 42),
              "OpenFaceHelmets\\Stormcloaks\\Helmet_1.nif"),
    ])
    bases = _player_armor_mesh_bases(tmp_path, include_candidate_slots=True)
    assert not any("helmet" in b for b in bases), bases
