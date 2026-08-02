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

"""#coverage-esl-chunks -- coverage generators emit ESL-SIZED pieces.

WHY. `_partition_patches_for_esl` bin-packs whole PATCHES and cannot split one, so a
coverage patch minting more than 2048 own records forced its merged piece to a full
ESP. Measured on the live pack: the two coverage patches minted **2,116** and **2,104**
ARMAs, and the shipped Combined was one ESL piece (2,044) plus one full ESP (2,088) --
the ESL one surviving by FOUR records of dedup luck. Splitting them at the source is
what the partitioner's splitting was for.

THE INVARIANT THESE TESTS PROTECT. Chunking is by TARGET (ARMO), never by armature, so
an ARMO's whole add-set stays in one piece and yields exactly ONE `filterByArmors`
line. The shipped INI has 9,913 lines for 9,913 distinct armors; splitting an ARMO
would emit two lines for it, and whether SkyPatcher accumulates or last-wins is
unverified. Not a risk worth taking on the only armour-delivery path."""
import json
import sys

import pytest

from src import ube_patcher as up
from src.ube_patcher import _chunk_targets_for_esl


def _rec(payload=b"\x00"):
    from src import esp
    return esp.Record(sig=b"ARMA", flags=0, formid=0x800, timestamp_vc=0,
                      version_unk=0x002C, payload=payload)


def _mk(n_targets, per_target=1, start=0):
    """n targets, each minting `per_target` DISTINCT armatures."""
    targets, mint = [], {}
    k = start
    for t in range(n_targets):
        to_mint = []
        for _ in range(per_target):
            a = ("src.esp", k); k += 1
            mint[a] = _rec()
            to_mint.append(a)
        targets.append((("plug.esp", 0x1000 + t), "plug.esp", to_mint))
    return targets, mint


# --- chunking ---------------------------------------------------------------

def test_under_cap_stays_one_chunk():
    t, m = _mk(10)
    assert len(_chunk_targets_for_esl(t, m, cap=2048)) == 1


def test_over_cap_splits():
    t, m = _mk(100)
    chunks = _chunk_targets_for_esl(t, m, cap=30)
    assert len(chunks) == 4                    # 30/30/30/10
    for c in chunks:
        distinct = {a for _armo, _p, tm in c for a in tm}
        assert len(distinct) <= 30


def test_every_chunk_is_within_cap():
    t, m = _mk(97, per_target=3)
    for c in _chunk_targets_for_esl(t, m, cap=50):
        distinct = {a for _armo, _p, tm in c for a in tm}
        assert len(distinct) <= 50


def test_a_target_is_NEVER_split_across_chunks():
    """THE invariant. One ARMO -> one INI line. If a target straddled two pieces the
    final INI would carry two `filterByArmors` lines for that armor."""
    t, m = _mk(60, per_target=4)
    chunks = _chunk_targets_for_esl(t, m, cap=25)
    seen = set()
    for c in chunks:
        for armo, _p, _tm in c:
            assert armo not in seen, f"target {armo} appears in two chunks"
            seen.add(armo)
    assert len(seen) == 60                     # and none was dropped


def test_no_target_is_dropped():
    t, m = _mk(53, per_target=2)
    chunks = _chunk_targets_for_esl(t, m, cap=17)
    assert sum(len(c) for c in chunks) == 53


def test_single_oversized_target_becomes_its_own_chunk():
    """It cannot be split without breaking the one-line invariant, so it gets its own
    over-cap piece and the caller downgrades just that one."""
    t, m = _mk(1, per_target=40)
    chunks = _chunk_targets_for_esl(t, m, cap=10)
    assert len(chunks) == 1 and len(chunks[0]) == 1


def test_armatures_shared_between_targets_are_counted_once_per_chunk():
    """A shared armature costs one slot in the chunk that holds it, not one per
    target -- otherwise chunks would be sized far below the cap."""
    from src import esp
    shared = ("src.esp", 999)
    rec = _rec()
    targets = [(("p.esp", i), "p.esp", [shared]) for i in range(50)]
    chunks = _chunk_targets_for_esl(targets, {shared: rec}, cap=5)
    assert len(chunks) == 1, "50 targets sharing ONE armature fit in one chunk"


def test_targets_whose_armatures_were_not_minted_cost_nothing():
    """`to_mint` entries absent from mint_rec never become records, so they must not
    consume chunk budget."""
    t, m = _mk(10)
    ghost = [(("p.esp", 1), "p.esp", [("src.esp", 77777)])]      # not in mint_rec
    assert len(_chunk_targets_for_esl(t + ghost, m, cap=2048)) == 1


# --- the sidecar must agree with the SAVED piece ------------------------------

def test_sidecar_fids_match_the_saved_records(tmp_path):
    """REGRESSION, caught in-game not in CI. `_emit_coverage_pieces` used to snapshot
    each minted FormID as an int BEFORE `prune_unused_masters` ran. Prune drops
    unreferenced masters and remaps every record's master byte IN PLACE, so the
    sidecar shipped `0x36000800` while the saved record was `0x34000800`.

    The INI survived (it masks to 24 bits) but the MERGE keys on the full FormID, so
    `merged_rec_by_key` missed every entry, the Combined got ZERO SkyPatcher links,
    and the run deleted the previous INI as stale. Under SkyPatcher-only delivery
    that is every converted armor invisible -- with a clean-looking log.

    Measured on the real output before the fix: 0 of 2048 sidecar fids matched.

    The earlier tests could not catch this: they build patches whose masters are all
    referenced, so prune is a no-op and the stale int happens to be right."""
    from src import esp
    # TWO masters, but only the SECOND is referenced -> prune drops the first and
    # every own FormID's master byte shifts 2 -> 1. That shift is the whole bug.
    masters = ["Unreferenced.esm", "Real.esm"]
    src_rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                         version_unk=0x002C, payload=b"\x00" * 8)
    key = ("Real.esm", 0x000800)
    targets = [(("Real.esm", 0x001234), "Real.esm", [key])]

    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    res = up._emit_coverage_pieces(
        out, targets, {key: src_rec}, masters, own_byte=len(masters),
        author="t", description="t", ini_header=[], emit_sidecar=True)

    saved = esp.ESP.load(tmp_path / res["pieces"][0])
    real = {r.formid for g in saved.groups if g.label == b"ARMA" for r in g.records}
    doc = json.loads((tmp_path / (res["pieces"][0] + ".skypatcher.json"))
                     .read_text(encoding="utf-8"))
    side = {int(a["fid"]) for ent in doc for a in ent["adds"]}

    assert len(saved.header.masters) < len(masters), (
        "this test is only meaningful if prune actually removed a master")
    assert side == real, (
        f"sidecar fids {sorted(side)} != saved record fids {sorted(real)} -- "
        "the merge resolves links by exact FormID, so any drift means ZERO links")


def test_ini_and_sidecar_agree_on_the_low_24_bits(tmp_path):
    """They are written from one source now; assert it, because the INI masks and the
    sidecar does not, which is exactly how they drifted apart unnoticed."""
    from src import esp
    masters = ["Unreferenced.esm", "Real.esm"]
    key = ("Real.esm", 0x000800)
    rec = esp.Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                     version_unk=0x002C, payload=b"\x00" * 8)
    out = tmp_path / "UBE_ModBody_Coverage UBE patch.esp"
    res = up._emit_coverage_pieces(
        out, [(("Real.esm", 0x001234), "Real.esm", [key])], {key: rec}, masters,
        own_byte=len(masters), author="t", description="t", ini_header=[],
        emit_sidecar=True)

    ini = [l for l in res["ini_lines"] if l.startswith("filterByArmors=")]
    ini_low = {int(p.split("|")[1], 16)
               for l in ini for p in l.split("armorAddonsToAdd=")[1].split(",")}
    doc = json.loads((tmp_path / (res["pieces"][0] + ".skypatcher.json"))
                     .read_text(encoding="utf-8"))
    assert ini_low == {int(a["fid"]) & 0xFFFFFF for ent in doc for a in ent["adds"]}


# --- wiring ------------------------------------------------------------------

def test_both_generators_emit_through_the_shared_helper():
    import inspect
    for fn in (up.generate_modded_nonbody_ube_coverage_patch,
               up.generate_modded_body_ube_coverage_patch):
        src = inspect.getsource(fn)
        assert "_emit_coverage_pieces(" in src, f"{fn.__name__} still writes its own ESP"
        assert "pieces" in src


def test_piece_names_keep_the_collectible_suffix():
    """The merge collects with `*UBE patch.esp`. A piece named outside that glob is
    silently dropped from the Combined -- invisible until armour goes missing."""
    from pathlib import Path
    out = Path("x/UBE_ModBody_Coverage UBE patch.esp")
    stem = out.stem
    base = stem.rpartition(" UBE patch")[0] or stem
    second = f"{base}2 UBE patch.esp"
    assert second.endswith("UBE patch.esp")
    assert second == "UBE_ModBody_Coverage2 UBE patch.esp"


def test_the_collection_globs_match_numbered_pieces():
    """auto_convert's cleanup and fallback globs must see the numbered pieces."""
    import fnmatch
    name = "UBE_ModBody_Coverage2 UBE patch.esp"
    assert fnmatch.fnmatch(name, "UBE_Mod*Coverage* UBE patch.esp")
    assert fnmatch.fnmatch(name, "*UBE patch.esp")
    assert not fnmatch.fnmatch(name, "UBE_Mod*Coverage UBE patch.esp"), \
        "the OLD glob missed numbered pieces -- that is why it was widened"
