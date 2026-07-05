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

"""#xedit5: five generalized override-path fixes (from five real xEdit records).
1 KSIZ recomputed when the winner's KWDA is adopted (count/array desync);
2 record flags carried (Non-Playable was dropped -> NPC armor became playable);
3 every FormID subrecord remapped in per-source overrides (EITM misrouted);
4 winner RNAM adopted when resolvable (base race regressed the winner's);
5 localized-source FULL/DESC never copied raw (LSTRING ids as zstrings)."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import esp
from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
    encode_zstring, iter_subrecords
from src import ube_patcher as up

DEFAULT = 0x00000019
BODY = up._BIPED_SLOT_BODY_BIT
OWN = 1 << 24                       # source own byte (masters=[Skyrim.esm])


# ---------- fixes 1 + 4: _overlay_winner_stats -----------------------------

def _payload(rnam, kwda, ksiz=None, extra=b""):
    p = encode_subrecord(b"EDID", encode_zstring("X"))
    p += encode_subrecord(b"RNAM", struct.pack("<I", rnam))
    p += encode_subrecord(b"KSIZ", struct.pack("<I",
                                               ksiz if ksiz is not None
                                               else len(kwda)))
    p += encode_subrecord(b"KWDA", b"".join(struct.pack("<I", k) for k in kwda))
    p += encode_subrecord(b"DATA", struct.pack("<If", 10, 1.0))
    return p + extra


def _subs(payload):
    return {s: d for s, d in iter_subrecords(payload)}


def test_overlay_adopts_kwda_with_ksiz_and_rnam():
    merged = ["Skyrim.esm", "Mod.esp"]
    base = _payload(rnam=0x00013742, kwda=[0x00000AAA])       # 1 keyword, race A
    # winner: masters=[Skyrim.esm], own byte 1; 3 skyrim-space keywords + DefaultRace
    win = up._WinnerRecord(
        "Win.esp", ["Skyrim.esm"],
        _payload(rnam=DEFAULT, kwda=[0xBBB, 0xCCC, 0xDDD]), False, 0x4)
    out = up._overlay_winner_stats(base, win, merged)
    s = _subs(out)
    assert struct.unpack("<I", s[b"KSIZ"])[0] == 3, "KSIZ must match adopted KWDA"
    assert len(s[b"KWDA"]) == 12
    assert struct.unpack("<I", s[b"RNAM"])[0] == DEFAULT, \
        "winner's resolvable RNAM must be adopted"
    print("  test_overlay_adopts_kwda_with_ksiz_and_rnam OK")


def test_overlay_skips_unresolvable_keywords_keeps_consistent_base():
    merged = ["Skyrim.esm", "Mod.esp"]
    base = _payload(rnam=0x00013742, kwda=[0xAAA, 0xBBB])
    # winner has a keyword in its OWN space (top byte == own byte 1) -> whole
    # KWDA skipped; base KSIZ/KWDA stay consistent (2/2).
    win = up._WinnerRecord(
        "Win.esp", ["Skyrim.esm"],
        _payload(rnam=DEFAULT, kwda=[0xBBB, (1 << 24) | 0x801]), False, 0)
    out = up._overlay_winner_stats(base, win, merged)
    s = _subs(out)
    assert struct.unpack("<I", s[b"KSIZ"])[0] == 2
    assert len(s[b"KWDA"]) == 8, "base keywords kept when winner unresolvable"
    assert struct.unpack("<I", s[b"RNAM"])[0] == DEFAULT, \
        "RNAM adoption is independent of the KWDA skip"
    print("  test_overlay_skips_unresolvable_keywords_keeps_consistent_base OK")


# ---------- fixes 2 + 3 + 5: per-source override construction ---------------

ARMO_NONPLAYABLE = 0x00000004


def _build_source(tmp, localized):
    tmp.mkdir(parents=True, exist_ok=True)
    ESP(header=TES4Header(masters=[], num_records=0, next_object_id=0x900,
                          version=1.7, flags=0x1), groups=[]).save(tmp / "Skyrim.esm")
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7),
        groups=[]).save(tmp / "UBE_AllRace.esp")
    arma = Record(sig=b"ARMA", flags=0, formid=OWN | 0x800, timestamp_vc=0,
                  version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring("BodyAA"))
        + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MOD3", encode_zstring("armor/x/body_0.nif"))))
    full = (struct.pack("<I", 0x1234) if localized
            else encode_zstring("Nice Cuirass"))
    desc = (struct.pack("<I", 0x5678) if localized
            else encode_zstring("d"))
    armo = Record(sig=b"ARMO", flags=ARMO_NONPLAYABLE, formid=OWN | 0x801,
                  timestamp_vc=0, version_unk=0x2C, payload=(
        encode_subrecord(b"EDID", encode_zstring("ArmorNiceCuirass"))
        + encode_subrecord(b"FULL", full)
        + encode_subrecord(b"EITM", struct.pack("<I", OWN | 0x900))  # own ench
        + encode_subrecord(b"BOD2", struct.pack("<II", BODY, 0))
        + encode_subrecord(b"RNAM", struct.pack("<I", DEFAULT))
        + encode_subrecord(b"MODL", struct.pack("<I", OWN | 0x800))
        + encode_subrecord(b"DESC", desc)
        + encode_subrecord(b"DATA", struct.pack("<If", 100, 5.0))))
    hdr_flags = 0x80 if localized else 0
    ESP(header=TES4Header(masters=["Skyrim.esm"], num_records=0,
                          next_object_id=0x900, version=1.7, flags=hdr_flags),
        groups=[Group(label=b"ARMA", records=[arma]),
                Group(label=b"ARMO", records=[armo])]).save(tmp / "Mod.esp")
    return tmp / "Mod.esp"


def _gen(tmp, localized):
    src = _build_source(tmp, localized)
    out = tmp / "out.esp"
    up.generate_ube_patch(src, out, master_data_dirs=[tmp],
                          converted_rel_paths={"armor/x/body_0.nif"})
    e = ESP.load(out)
    armo = next(r for g in e.groups if g.label == b"ARMO" for r in g.records)
    return e, armo


def test_override_carries_record_flags_and_remaps_eitm(tmp_path):
    e, armo = _gen(tmp_path, localized=False)
    assert armo.flags & ARMO_NONPLAYABLE, \
        "Non-Playable record flag must be carried onto the override"
    s = _subs(armo.payload)
    eitm = struct.unpack("<I", s[b"EITM"])[0]
    mod_idx = next(i for i, m in enumerate(e.header.masters)
                   if m.lower() == "mod.esp")
    assert (eitm >> 24) == mod_idx and (eitm & 0xFFFFFF) == 0x900, \
        f"EITM must remap into patch space, got {eitm:08X}"
    # non-localized FULL/DESC copied as-is
    assert s[b"FULL"].rstrip(b"\x00") == b"Nice Cuirass"
    print("  test_override_carries_record_flags_and_remaps_eitm OK")


def test_override_synthesizes_full_drops_desc_for_localized(tmp_path):
    e, armo = _gen(tmp_path, localized=True)
    s = _subs(armo.payload)
    assert b"DESC" not in s, "localized DESC (LSTRING id) must be dropped"
    assert s[b"FULL"].endswith(b"\x00") and len(s[b"FULL"]) > 4, \
        f"FULL must be a synthesized zstring, got {s[b'FULL']!r}"
    assert b"Nice Cuirass" in s[b"FULL"], s[b"FULL"]
    print("  test_override_synthesizes_full_drops_desc_for_localized OK")
