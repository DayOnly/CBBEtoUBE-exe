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

"""Guards for stale ESL split-piece cleanup in merge_patches_split.

Two things are being pinned, and the second matters as much as the first
because this code DELETES FILES in the user's output mod:

1. A run that drops back under the ESL cap (N pieces -> 1) must remove the
   orphaned `...2.esp` / `...3.esp`. Previously the cleanup lived only in the
   split branch, after an early `return` taken by the single-piece path, so the
   orphans survived holding the PREVIOUS run's records -- while the user had
   been told to enable every piece, and the post-merge passes (which glob the
   whole piece family) rewrote them and reported them "validated clean".

2. Cleanup must only ever remove OUR OWN numbered pieces. The original glob was
   `<stem>*<suffix>`, which also matches a user's `<stem>_backup.esp`.
"""
import struct

import pytest

from src import esp, ube_patcher


def _minimal_patch(path, master="Skyrim.esm"):
    """Smallest plugin merge_patches will accept: a TES4 with one master."""
    e = esp.ESP(header=esp.TES4Header(masters=[master]), groups=[])
    e.save(path)
    return path


def _run_merge(tmp_path, out_name="Combined.esp", owns=True):
    src = _minimal_patch(tmp_path / "a UBE patch.esp")
    return ube_patcher.merge_patches_split(
        [src], tmp_path / out_name, esl_flag=True,
        author="t", description="t", master_data_dirs=None,
        owns_output_dir=owns)


def test_orphaned_pieces_removed_when_run_drops_to_one_piece(tmp_path):
    """N pieces -> 1 piece must not leave the old pieces behind."""
    stale2 = tmp_path / "Combined2.esp"
    stale3 = tmp_path / "Combined3.esp"
    stale2.write_bytes(b"stale")
    stale3.write_bytes(b"stale")

    _run_merge(tmp_path)

    assert (tmp_path / "Combined.esp").is_file()
    assert not stale2.exists(), "orphaned Combined2.esp survived a 1-piece run"
    assert not stale3.exists(), "orphaned Combined3.esp survived a 1-piece run"


def test_cleanup_never_touches_non_numbered_siblings(tmp_path):
    """The cleanup deletes files in a user's mod folder, so it must match only
    `<stem><digits><suffix>` -- never an arbitrary `<stem>*<suffix>`."""
    keep = {
        tmp_path / "Combined_backup.esp": b"user backup",
        tmp_path / "Combined - Copy.esp": b"user copy",
        tmp_path / "CombinedNotes.esp": b"user notes",
    }
    for p, data in keep.items():
        p.write_bytes(data)
    doomed = tmp_path / "Combined2.esp"
    doomed.write_bytes(b"stale")

    _run_merge(tmp_path)

    for p, data in keep.items():
        assert p.is_file(), f"cleanup deleted an unrelated file: {p.name}"
        assert p.read_bytes() == data, f"cleanup modified {p.name}"
    assert not doomed.exists(), "the genuine stale piece was not removed"


def test_onam_is_classified_as_a_formid_subrecord():
    """ONAM (art object) carries a FormID. While it was unclassified,
    prune_unused_masters could drop a master referenced ONLY by ONAM and
    renumber the rest around it, leaving the ref pointing at the wrong record.
    The coverage passes already strip ONAM for exactly this reason."""
    assert b"ONAM" in ube_patcher.FORMID_SINGLE_SUBRECORD_SIGS
    payload = b"ONAM" + struct.pack("<H", 4) + struct.pack("<I", 0x01000ABC)
    found = list(ube_patcher._iter_formids_in_payload(payload))
    assert found, "ONAM FormID not yielded by _iter_formids_in_payload"


def test_cleanup_is_skipped_when_the_caller_does_not_own_the_directory(tmp_path):
    """merge_patches_split is also the standalone `merge` subcommand's entry
    point, where -o is an arbitrary user path. Deleting `<stem><digits>.esp`
    siblings there would destroy files this tool never wrote."""
    mine = tmp_path / "Combined2.esp"
    mine.write_bytes(b"user file, not ours")
    _run_merge(tmp_path, owns=False)
    assert mine.is_file(), "deleted a numbered sibling in a user-chosen dir"
    assert mine.read_bytes() == b"user file, not ours"
