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

"""fix_spurious_hand_slot: clear biped slot 33 (Hands) from a forearm/wrist
bracer that claims it but ships no hand geometry (-> it would hide the nude
hands and draw nothing = invisible hands on UBE actors; the classic
forearm-vambrace case). Must NOT touch a real glove/gauntlet (mesh has hand geometry) nor a
full-body suit/skin (claims slot 32 Body; its hands come from the suit)."""
import struct

from src import esp, ube_patcher
from src.esp import encode_subrecord, encode_zstring

DEFAULT_RACE = 0x00000019


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


def _armo(fid, edid, slot_bits, arma_fids):
    payload = (
        encode_subrecord(b"EDID", encode_zstring(edid))
        + encode_subrecord(b"BOD2", struct.pack("<II", slot_bits, 0))
    )
    for af in arma_fids:
        payload += encode_subrecord(ube_patcher.ARMO_ARMATURE_SIG,
                                    struct.pack("<I", af))
    payload += encode_subrecord(b"DATA", struct.pack("<If", 100, 1.0))
    return esp.Record(sig=b"ARMO", flags=0, formid=fid, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)


def _slots_of(payload):
    for sig, d in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(d) >= 4:
            return struct.unpack_from("<I", d, 0)[0]
    return None


def _armo_slots(esp_path, fid):
    e = esp.ESP.load(esp_path)
    for g in e.groups:
        if g.label == b"ARMO":
            for r in g.records:
                if r.formid == fid:
                    return _slots_of(r.payload)
    return None


def _arma_slots(esp_path, fid):
    e = esp.ESP.load(esp_path)
    for g in e.groups:
        if g.label == b"ARMA":
            for r in g.records:
                if r.formid == fid:
                    return _slots_of(r.payload)
    return None


def test_clear_slot33_payload_only_touches_hands_bit():
    # 0x18 = slots 33|34 -> clearing 33 leaves 0x10 (34).
    payload = encode_subrecord(b"BOD2", struct.pack("<II", _bits(33, 34), 7))
    out, changed = ube_patcher.clear_slot33_from_bod2_payload(payload)
    assert changed
    assert _slots_of(out) == _bits(34)
    # the trailing struct (armor_type=7) is preserved verbatim
    for sig, d in esp.iter_subrecords(out):
        if sig == b"BOD2":
            assert struct.unpack_from("<I", d, 4)[0] == 7
    # idempotent: no slot 33 -> no change
    out2, changed2 = ube_patcher.clear_slot33_from_bod2_payload(out)
    assert not changed2


def test_fix_spurious_hand_slot(tmp_path, monkeypatch):
    meshes = tmp_path / "meshes"
    fracs = {}

    def mk(rel, frac):
        p = meshes / rel.replace("/", "\\")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00")          # dummy so .is_file() passes
        fracs[str(p)] = frac
        return rel

    bracer = mk("!UBE/Mod/vambraces_1.nif", 0.02)   # forearm bracer, no hands
    glove = mk("!UBE/Mod/gauntlets_1.nif", 0.95)    # real glove, full hands
    torso = mk("!UBE/Mod/body_1.nif", 0.0)          # body suit torso mesh

    monkeypatch.setattr(ube_patcher, "_nif_max_hand_weight_fraction",
                        lambda p: fracs.get(str(p)))

    armas = [
        _arma(0x801, "BracerAA", _bits(33, 34), bracer),
        _arma(0x802, "GloveAA", _bits(33), glove),
        _arma(0x803, "SuitAA", _bits(32, 33, 37), torso),
    ]
    armos = [
        _armo(0x901, "Bracer", _bits(33, 34), [0x801]),     # -> STRIP 33
        _armo(0x902, "Glove", _bits(33), [0x802]),          # real hands -> KEEP
        _armo(0x903, "BodySuit", _bits(32, 33, 37), [0x803]),  # slot32 -> KEEP
    ]
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=armas),
                        esp.Group(label=b"ARMO", records=armos)])
    combined = tmp_path / "CBBE_to_UBE_Combined.esp"
    e.save(combined)

    ube_patcher._HAND_WEIGHT_FRAC_CACHE.clear()
    stats = ube_patcher.fix_spurious_hand_slot(combined, meshes)

    # Only the handless bracer (ARMO + its ARMA) is stripped.
    assert stats["armos_fixed"] == 1
    assert stats["armas_fixed"] == 1
    assert _armo_slots(combined, 0x901) == _bits(34)        # 33 cleared, 34 kept
    assert _arma_slots(combined, 0x801) == _bits(34)
    # Real glove untouched.
    assert _armo_slots(combined, 0x902) == _bits(33)
    assert _arma_slots(combined, 0x802) == _bits(33)
    # Body suit (claims slot 32) untouched — its hands come from the suit.
    assert _armo_slots(combined, 0x903) == _bits(32, 33, 37)
    assert _arma_slots(combined, 0x803) == _bits(32, 33, 37)


def test_fix_spurious_hand_slot_arma_level_vambrace(tmp_path, monkeypatch):
    # The ARMO is correctly a forearm piece (slot 34, NOT slot 33), but one of
    # its armatures carries a stray [33,34] over a handless mesh (the converted
    # vambrace). Pass 1 (ARMO-gated) can't see it; the ARMA-level
    # pass must strip slot 33 from the armature so the nude hands render.
    meshes = tmp_path / "meshes"
    fracs = {}

    def mk(rel, frac):
        p = meshes / rel.replace("/", "\\")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00")
        fracs[str(p)] = frac
        return rel

    vamb = mk("!UBE/Mod/vambraces_1.nif", 0.02)        # handless forearm piece
    handgaunt = mk("!UBE/Mod/handgauntlet_1.nif", 0.90)  # real hand+forearm glove

    monkeypatch.setattr(ube_patcher, "_nif_max_hand_weight_fraction",
                        lambda p: fracs.get(str(p)))

    armas = [
        _arma(0x811, "VambAA", _bits(33, 34), vamb),       # stray 33 -> STRIP
        _arma(0x812, "GauntAA", _bits(33, 34), handgaunt),  # real hands -> KEEP
    ]
    armos = [
        _armo(0x911, "Vambrace", _bits(34), [0x811]),      # ARMO has NO slot 33
        _armo(0x912, "HandGauntlet", _bits(34), [0x812]),
    ]
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=armas),
                        esp.Group(label=b"ARMO", records=armos)])
    combined = tmp_path / "CBBE_to_UBE_Combined.esp"
    e.save(combined)

    ube_patcher._HAND_WEIGHT_FRAC_CACHE.clear()
    stats = ube_patcher.fix_spurious_hand_slot(combined, meshes)

    assert stats["armas_fixed"] == 1
    assert _arma_slots(combined, 0x811) == _bits(34)        # stray 33 cleared
    assert _arma_slots(combined, 0x812) == _bits(33, 34)    # real hands kept
    assert _armo_slots(combined, 0x911) == _bits(34)        # ARMO untouched


def test_fix_spurious_hand_slot_failsafe_on_unreadable_mesh(tmp_path, monkeypatch):
    # Mesh exists but can't be measured (frac None) -> assume hands -> never strip.
    meshes = tmp_path / "meshes"
    p = meshes / "!UBE\\Mod\\mystery_1.nif"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00")
    monkeypatch.setattr(ube_patcher, "_nif_max_hand_weight_fraction",
                        lambda _p: None)
    armas = [_arma(0x801, "MysteryAA", _bits(33, 34), "!UBE/Mod/mystery_1.nif")]
    armos = [_armo(0x901, "Mystery", _bits(33, 34), [0x801])]
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]),
                groups=[esp.Group(label=b"ARMA", records=armas),
                        esp.Group(label=b"ARMO", records=armos)])
    combined = tmp_path / "CBBE_to_UBE_Combined.esp"
    e.save(combined)
    ube_patcher._HAND_WEIGHT_FRAC_CACHE.clear()
    stats = ube_patcher.fix_spurious_hand_slot(combined, meshes)
    assert stats["armos_fixed"] == 0
    assert _armo_slots(combined, 0x901) == _bits(33, 34)   # unchanged
