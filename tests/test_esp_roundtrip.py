"""Round-trip ESP read + write and assert content survives.

We can't always get byte-identity (the original may use compression flags
we don't preserve, or have minor encoding quirks). What we DO check:

  1. After load + save + reload, the TES4 master list is preserved.
  2. Every top-level GRUP that was present is still present.
  3. Every record's FormID, signature, and subrecord (sig, data) sequence
     is preserved.

This is a real correctness gate before we trust the writer.
"""
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import esp


SAMPLES = [
    PROJ / "samples" / "m1" / "kozakowy_vampire" / "ube"
         / "[TOTOxKozakowy] Kozakowy's Vampire Armor UBE v1.0"
         / "KozakowyVampireArmor UBE patch.esp",
    PROJ / "samples" / "m1" / "eve_sunfire" / "ube"
         / "Obi - Eve's Sunfire Armor UBE patch.esp",
]
# Optional extra patch ESP to round-trip: set CBBE2UBE_TEST_ESP to any UBE
# patch ESP path. Skipped (is_file guard below) when unset.
_extra_esp = os.environ.get("CBBE2UBE_TEST_ESP")
if _extra_esp:
    SAMPLES.append(Path(_extra_esp))


def subrecord_signature_sequence(payload: bytes) -> list[tuple[bytes, int, bytes]]:
    """Return [(sig, len(data), data)] for every subrecord, ignoring XXXX
    expansion details so we can compare semantically."""
    return [(sig, len(data), data) for sig, data in esp.iter_subrecords(payload)]


def assert_roundtrip(path: Path) -> None:
    print(f"\n>>> {path.name}")
    src = esp.ESP.load(path)
    tmp = path.parent / (path.stem + ".rt-tmp.esp")
    src.save(tmp)
    dst = esp.ESP.load(tmp)

    # 1. masters preserved
    assert src.header.masters == dst.header.masters, \
        f"masters changed: {src.header.masters} -> {dst.header.masters}"
    print(f"  masters OK ({len(src.header.masters)})")

    # 2. group set preserved
    src_labels = {g.label for g in src.groups}
    dst_labels = {g.label for g in dst.groups}
    assert src_labels == dst_labels, f"group set changed: {src_labels} -> {dst_labels}"
    print(f"  groups OK ({sorted(l.decode() for l in src_labels)})")

    # 3. records and subrecords preserved
    for sg, dg in zip(src.groups, dst.groups):
        assert len(sg.records) == len(dg.records), \
            f"{sg.label!r}: record count {len(sg.records)} -> {len(dg.records)}"
        for sr, dr in zip(sg.records, dg.records):
            assert sr.sig == dr.sig
            assert sr.formid == dr.formid, \
                f"FormID drift: {sr.formid:#010x} -> {dr.formid:#010x}"
            ss = subrecord_signature_sequence(sr.payload)
            ds = subrecord_signature_sequence(dr.payload)
            assert ss == ds, f"subrecord drift in {sr.sig!r} {sr.formid:#010x}"
    print(f"  records OK ({sum(len(g.records) for g in src.groups)} total)")

    # Clean up
    tmp.unlink()


for p in SAMPLES:
    if not p.is_file():
        print(f"SKIP (missing): {p}")
        continue
    assert_roundtrip(p)

print("\nALL PASSED")
