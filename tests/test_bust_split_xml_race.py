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

"""WHY THE BUST-COLLIDER SPLIT IS A COIN FLIP  (2026-09-08/09)

Two arms of IDENTICAL code and flags produced different meshes: one garment's
`_0` and `_1` gained a hidden `CuirassCol` clone in one run and not the other,
moving six breast and two butt jiggle bones onto a different shape. Reproduced
with a repeat control, then narrowed here to a mechanism that needs no arm to
demonstrate.

THE CHAIN, each link measured on the real piece:

  * its SOURCE nif carries no physics link at all -- no
    `HDT Skinned Mesh Physics Object` string and no `.xml` string in its bytes
    -- so `_read_source_hdt_xml_disk` returns None on it, deterministically;
  * its OUTPUT nif declares none either (0 root extra-data blocks);
  * so resolution falls through to `_find_hdt_xml_for_armor`, which globs the
    mod tree the path lives in -- and the path handed to it is the DESTINATION;
  * `_mod_xml_index` memoises that glob per mod root, and its docstring states
    the assumption that makes it safe: "The XML set is static for the duration
    of a conversion run." **That is true of a SOURCE mod and false of the
    destination, which the run is actively writing XMLs into.**

The tests below are the last link: the resolver's answer for one fixed NIF
changes with nothing but how many same-stem XMLs exist in the tree at the
moment it looks. On the real piece that count runs from 0 to 6 during a
conversion, and 1 is inside that range.

NOTHING IS FIXED HERE. The repair changes which physics XML a piece resolves,
which is a behaviour change on the class that tore breasts off in game
(2026-07-26); it needs a decision, not a quiet edit.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".pynifly"))

from src import nif_convert as nc                 # noqa: E402
from src import nif_convert_physics as ph         # noqa: E402


def _tree(tmp_path, xml_dirs):
    """A mod root with `meshes/`, one armour NIF, and a `cuirass.xml` in each
    named directory. Returns (mod_root, nif_path)."""
    root = tmp_path / "OutputMod"
    piece = root / "meshes" / "!UBE" / "brand" / "stuff" / "eb" / "f"
    piece.mkdir(parents=True)
    nif = piece / "cuirass_0.nif"
    nif.write_bytes(b"not a real nif")
    for d in xml_dirs:
        p = root / "meshes" / "!UBE" / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "cuirass.xml").write_text(
            '<system><per-triangle-shape name="Cuirass"/></system>',
            encoding="utf-8")
    return root, nif


def _resolve(tmp_path, xml_dirs):
    nc._HDT_XML_INDEX_CACHE.clear()
    root, nif = _tree(tmp_path, xml_dirs)
    return ph._find_hdt_xml_for_armor(nif, source_mod_root=root)


# ------------------------------------------------- the count decides the answer

def test_no_same_stem_xml_resolves_to_nothing(tmp_path):
    assert _resolve(tmp_path, []) is None


def test_exactly_ONE_same_stem_xml_is_taken_from_ANOTHER_directory(tmp_path):
    """THE RACE, in one line. The piece's own directory has no XML, so this
    resolves an UNRELATED garment's config purely because it was, at that
    instant, the only file in the tree with a matching stem."""
    got = _resolve(tmp_path, ["someone/else/entirely"])
    assert got is not None
    assert "else" in str(got).replace("\\", "/")


def test_SIX_same_stem_xmls_resolve_to_nothing_again(tmp_path):
    """And once the run has written the rest, the same lookup for the same NIF
    stops matching. Nothing about the piece changed -- only the clock."""
    dirs = ["a/one", "b/two", "c/three", "d/four", "e/five", "f/six"]
    assert _resolve(tmp_path, dirs) is None


def test_the_answer_changes_with_the_TREE_and_not_the_piece(tmp_path):
    """The three cases above, stated as the property that matters: one fixed
    NIF, three different answers, decided by what else exists yet."""
    answers = [
        _resolve(tmp_path / "a", []),
        _resolve(tmp_path / "b", ["someone/else/entirely"]),
        _resolve(tmp_path / "c", ["a/one", "b/two", "c/three"]),
    ]
    assert answers[1] is not None
    assert answers[0] is None and answers[2] is None
    assert len({str(x) for x in answers}) > 1


def test_an_xml_in_the_pieces_OWN_directory_wins_and_is_stable(tmp_path):
    """The safe case, kept so the finding is not read as "the resolver is
    always wrong": a config beside the NIF is preferred and is unaffected by
    how many others exist."""
    own = "brand/stuff/eb/f"          # the directory the NIF itself lives in
    alone = _resolve(tmp_path / "x", [own])
    crowd = _resolve(tmp_path / "y", [own, "other/place", "third/place"])
    assert alone is not None and crowd is not None
    assert Path(str(alone)).parent.name == Path(str(crowd)).parent.name == "f"


# ------------------------------------------- the assumption that makes it unsafe

def test_the_index_is_memoised_so_a_worker_freezes_one_snapshot(tmp_path):
    """Each worker keeps whatever the tree looked like when it first asked, so
    two workers in one run can hold different views of the same directory."""
    nc._HDT_XML_INDEX_CACHE.clear()
    root, _nif = _tree(tmp_path, ["first/place"])
    first = ph._mod_xml_index(root)
    (root / "meshes" / "!UBE" / "second").mkdir(parents=True)
    (root / "meshes" / "!UBE" / "second" / "cuirass.xml").write_text(
        "<system/>", encoding="utf-8")
    again = ph._mod_xml_index(root)
    assert again == first, "the memo is what freezes the snapshot"
    nc._HDT_XML_INDEX_CACHE.clear()
    assert len(ph._mod_xml_index(root)) == len(first) + 1


def test_the_index_docstring_still_states_the_assumption_that_is_false_here():
    """Drift guard. The mechanism above is only a bug BECAUSE the index assumes
    a static tree; if that sentence is ever rewritten, this finding needs
    re-reading rather than silently surviving."""
    src = Path(ph.__file__).read_text(encoding="utf-8", errors="replace")
    i = src.index("def _mod_xml_index(")
    doc = src[i:i + 900]
    assert "static for the duration" in doc


# ================================================================= THE FIX

def test_a_destination_caller_refuses_the_filename_fallback():
    """#hdt-xml-race, fixed 2026-09-09. The stem scan is safe against a SOURCE
    mod, whose tree is static, and unsafe against the DESTINATION, which the run
    is still writing into. So the callers holding a destination path say so."""
    src = Path(ph.__file__).read_text(encoding="utf-8", errors="replace")
    assert "if xml_disk is None and stem_scan:" in src, (
        "the fallback is no longer gated -- the race is back")


def test_the_bust_split_callers_refuse_it_and_the_GUARD_does_not():
    """Which destination callers are gated is the whole decision, so both halves
    are pinned.

    The two BUST-SPLIT callers refuse the fallback: that is the race I
    root-caused, and refusing it makes them read no XML, which is the right
    answer for a piece that has none.

    The declared-bone GUARD does NOT refuse it, deliberately. Refusing there
    does not make it read the RIGHT xml -- it makes it read none, and the guard
    then reports "cannot check" and stops filtering bones at all. Measured on a
    769-NIF arm: gating it moved 421 NIFs, 62 of them by bone count. A
    wrong-but-active guard and an inactive guard are both wrong, and swapping
    one for the other is not a fix.
    """
    from tests import _converter_sources as _cs
    gated, ungated = [], []
    for path, text in _cs.texts().items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            # An ASSIGNMENT, not a mention: the docstrings name it too.
            if ("= _read_source_hdt_xml_text(" not in line
                    and "= _nc()._read_source_hdt_xml_text(" not in line):
                continue
            window = chr(10).join(lines[i:i + 3])
            if "dst_path" not in window:
                continue
            (gated if "stem_scan=False" in window else ungated).append(path.name)
    assert sorted(gated) == ["nif_convert_bust.py", "nif_convert_bust.py"], gated
    assert ungated == ["nif_convert_physics.py"], ungated


def test_the_guard_call_site_records_WHY_it_is_not_gated():
    """An un-gated site that looks like an oversight will be "fixed" by the next
    reader. It carries the number instead."""
    src = Path(ph.__file__).read_text(encoding="utf-8", errors="replace")
    assert "421 NIFs, 62 of them by BONE" in src


def test_the_memo_key_carries_stem_scan():
    """Two callers can ask about the same path and mean different questions. A
    memo that conflated them would hand a destination caller the very answer the
    parameter exists to refuse."""
    src = Path(ph.__file__).read_text(encoding="utf-8", errors="replace")
    assert "_st.st_size, stem_scan)" in src


def test_the_source_side_stem_scan_is_UNCHANGED():
    """The fallback earns its keep on the source side: it catches authored
    configs the keyword map misses, and without it those fall back to a
    generated XML with worse physics. Only the destination refuses it."""
    import inspect
    sig = inspect.signature(ph._read_source_hdt_xml_text)
    assert sig.parameters["stem_scan"].default is True
