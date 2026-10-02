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

r"""#arma-model-order -- GitHub issue #27 ("also noticed").

One ARMA of 3,862 in a full run (the coverage pass's `UBE_MBD_*`) was written
with its model subrecords as MOD2, MO2T, MOD4, MO4T, MOD3, MOD5. The Creation Kit
and xEdit order is MOD2, MO2T, MO2S, MOD3, MO3T, MO3S, MOD4, MO4T, MO4S, MOD5,
MO5T, MO5S. A Mutagen-based tool reads a record in any other order as missing its
male world and first-person models, and drops them when it rewrites the plugin.

Cause: `rebuild_arma_payload` synthesises a missing MOD3 / MOD5 from the converted
male mesh and appended it after everything already written. Pinned here: the
synthesised models land in place, only the model subrecords move, and a payload
that was already in order comes back byte for byte.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import esp                                              # noqa: E402
from src.ube_patcher import _arma_model_order, rebuild_arma_payload  # noqa: E402

MODT = struct.pack("<III", 0, 0, 0)         # a valid, empty texture-hash block


def _payload(*subs) -> bytes:
    return b"".join(esp.encode_subrecord(sig, data) for sig, data in subs)


def _sigs(payload: bytes):
    return [sig for sig, _d in esp.iter_subrecords(payload)]


def _z(text: str) -> bytes:
    return esp.encode_zstring(text)


def test_a_synthesised_female_and_first_person_model_land_in_place():
    """The reported shape: a male world model and a male first-person model with
    their hash blocks, and no female ones. Both are synthesised from the converted
    male meshes."""
    src = _payload((b"EDID", _z("Arma")), (b"RNAM", struct.pack("<I", 0x19)),
                   (b"MOD2", _z("armor/a/body_0.nif")), (b"MO2T", MODT),
                   (b"MOD4", _z("armor/a/1st_0.nif")), (b"MO4T", MODT))
    out = rebuild_arma_payload(src, new_primary_rnam=0x12345678,
                               new_additional_race_fids=[0x111],
                               converted_nif_exists=lambda p: True)
    assert _sigs(out) == [b"EDID", b"RNAM", b"MOD2", b"MO2T", b"MOD3",
                          b"MOD4", b"MO4T", b"MOD5", b"MODL"], _sigs(out)


def test_a_payload_already_in_order_comes_back_unchanged():
    src = _payload((b"EDID", _z("Arma")), (b"MOD2", _z("a.nif")), (b"MO2T", MODT),
                   (b"MOD3", _z("b.nif")), (b"MO3T", MODT), (b"MOD4", _z("c.nif")),
                   (b"MO4T", MODT), (b"MOD5", _z("d.nif")), (b"MODL", b"\x01\x00\x00\x00"))
    assert _arma_model_order(src) == src


def test_only_the_model_subrecords_change_places():
    src = _payload((b"EDID", _z("Arma")), (b"MOD4", _z("c.nif")), (b"DNAM", b"\x01\x02"),
                   (b"MOD3", _z("b.nif")), (b"MODL", b"\x07\x00\x00\x00"))
    assert _sigs(_arma_model_order(src)) == [b"EDID", b"MOD3", b"DNAM", b"MOD4", b"MODL"]


def test_each_model_keeps_its_own_hash_block_and_data():
    src = _payload((b"MOD4", _z("four")), (b"MO4T", MODT + b"4444"), (b"MOD3", _z("three")),
                   (b"MO3T", MODT + b"3333"), (b"MOD2", _z("two")))
    got = list(esp.iter_subrecords(_arma_model_order(src)))
    assert [s for s, _d in got] == [b"MOD2", b"MOD3", b"MO3T", b"MOD4", b"MO4T"]
    assert dict(got)[b"MO3T"].endswith(b"3333") and dict(got)[b"MO4T"].endswith(b"4444")
    assert dict(got)[b"MOD3"] == _z("three")
