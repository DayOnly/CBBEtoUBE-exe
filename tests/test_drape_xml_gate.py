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

"""#drape-xml-gate -- narrow the draping-garment NAME skip to pieces that declare no
physics XML.

WHY THE NAME LIST EXISTS AT ALL, because it looks removable and is not. Draping cloth
is often driven by a runtime-GLOBAL HDT-SMP config with no per-mesh XML, so there is
nothing structural to detect. Grafting UBE scale bones onto such cloth crashed the SMP
update on equip -- C1, `EXCEPTION_ACCESS_VIOLATION` in `hdtsmp64.dll` reading skin
partition data of `BSTriShape "robes"`, STILL FILED UNDER CRASHES, NOT FIXED. And
`_CHEST_JIGGLE_BONES` are exactly that bone class.

Two claims that made the list look redundant were both measured FALSE:
  - "the XML gate supersedes it" -- no: the class it covers has no XML BY DEFINITION;
  - "the derived requirement self-limits loose garments" -- measured 2026-07-26:
    p10 0.57 / p50 0.66 / p90 0.94, 0 of 9 got nothing. Capes do self-limit
    (req p50 0.059); robes and dresses do NOT (0.539 / 0.646, 3-4% at zero).

So this flag does NOT retire the list. It narrows it to the population that needs it:
a piece that SHIPS an XML has its physics authored and declared, the structural gates
read that same file, and a shape absent from it is not simulated.

Measured over 400 name-excluded shapes: 239 already caught by a structural gate (the
name adds nothing); of the 161 doing real work, 57 belong to pieces WITH an XML and 104
to pieces without -- 22 freed / 13 kept on the clipping subset.

NOT airtight: C1's cloth was BONE-driven and could in principle be simulated through
`<bone>` constraints without being named in the XML. `LEG_CHAIN_GUARD` does not close
that (it skips CUSTOM-bone verts; C1's robe used SKELETON bones). Ships OFF; the
failure mode is an equip CTD, so it is judged by equipping robes."""
import importlib
import os

import pytest

import src.nif_convert as nc


@pytest.fixture(autouse=True)
def _clean():
    yield
    os.environ.pop("CBBE2UBE_DRAPE_XML_GATE", None)
    importlib.reload(nc)


# --- the flag ------------------------------------------------------------------

def test_defaults_off(monkeypatch):
    """The failure mode is a crash on equip. Off unless asked for."""
    monkeypatch.delenv("CBBE2UBE_DRAPE_XML_GATE", raising=False)
    assert importlib.reload(nc).DRAPE_SKIP_XML_GATED is False


def test_opt_in(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    assert importlib.reload(nc).DRAPE_SKIP_XML_GATED is True


# --- the two groups -------------------------------------------------------------

def test_the_split_is_exhaustive_and_disjoint():
    """`_CONFORM_SKIP_NAMES` must remain exactly the union -- other code and tests
    still read it, and a key silently dropped from both halves would be a hole."""
    assert set(nc._CONFORM_SKIP_NAMES) == (set(nc._CONFORM_SKIP_STRUCTURAL)
                                           | set(nc._CONFORM_SKIP_DRAPING))
    assert not (set(nc._CONFORM_SKIP_STRUCTURAL) & set(nc._CONFORM_SKIP_DRAPING))


def test_draping_group_holds_the_C1_names():
    for k in ("robe", "cloak", "cape", "dress", "gown", "sarong", "loincloth"):
        assert k in nc._CONFORM_SKIP_DRAPING


def test_structural_group_keeps_col(monkeypatch):
    """'col' looks like a bug -- it eats real collars -- but it is load-bearing by
    accident: in a real load order the collar shapes it catches belong to CLOAKS whose
    physics comes from a GLOBAL config, so freeing them walks straight into C1. It
    stays STRUCTURAL, where the XML gate never relaxes it."""
    assert "col" in nc._CONFORM_SKIP_STRUCTURAL
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    m = importlib.reload(nc)
    assert "col" in m._conform_skip_keys(piece_has_hdt_xml=True)


# --- the gate itself -------------------------------------------------------------

def test_flag_off_always_uses_the_full_list(monkeypatch):
    """REGRESSION. With the flag off the shipped behaviour is unchanged for every
    piece, whether or not it declares an XML."""
    monkeypatch.delenv("CBBE2UBE_DRAPE_XML_GATE", raising=False)
    m = importlib.reload(nc)
    for has in (True, False, None):
        assert set(m._conform_skip_keys(has)) == set(m._CONFORM_SKIP_NAMES)


def test_piece_with_an_xml_drops_the_draping_names(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    m = importlib.reload(nc)
    keys = m._conform_skip_keys(piece_has_hdt_xml=True)
    assert set(keys) == set(m._CONFORM_SKIP_STRUCTURAL)
    assert "robe" not in keys and "dress" not in keys


def test_piece_without_an_xml_KEEPS_them(monkeypatch):
    """The C1 class. This is the whole point of the gate."""
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    m = importlib.reload(nc)
    assert set(m._conform_skip_keys(piece_has_hdt_xml=False)) == set(m._CONFORM_SKIP_NAMES)


def test_UNKNOWN_is_treated_as_no_xml(monkeypatch):
    """An unanswered question must not relax a CTD guard. `None` reaches the predicate
    whenever a caller could not determine the piece's physics."""
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    m = importlib.reload(nc)
    assert set(m._conform_skip_keys(piece_has_hdt_xml=None)) == set(m._CONFORM_SKIP_NAMES)


def test_unreadable_piece_reports_no_xml(tmp_path, monkeypatch):
    """`_piece_has_hdt_xml` must fail CLOSED: an unreadable or absent NIF answers
    False, which keeps the full list."""
    monkeypatch.setenv("CBBE2UBE_DRAPE_XML_GATE", "1")
    m = importlib.reload(nc)
    assert m._piece_has_hdt_xml(tmp_path / "does_not_exist.nif") is False
    bad = tmp_path / "junk.nif"
    bad.write_bytes(b"not a nif")
    assert m._piece_has_hdt_xml(bad) is False


# --- wired into every pass that reads the list ------------------------------------

def test_all_graft_passes_use_the_gated_keys():
    """A pass left on the raw list would keep skipping robes while its siblings stop,
    which is worse than either choice made consistently."""
    import inspect
    for fn in (nc._conform_weights_core, nc._match_rigid_leg_bend_to_body,
               nc._transfer_body_jiggle_to_fitted):
        src = inspect.getsource(fn)
        assert "_conform_skip_keys(" in src, f"{fn.__name__} still uses the raw list"


def test_the_orphan_predicate_takes_the_piece_state():
    import inspect
    src = inspect.getsource(nc._conform_orphans_shape)
    assert "_conform_skip_keys(piece_has_hdt_xml)" in src


def test_the_reskin_drape_guard_is_deliberately_NOT_gated():
    """`_drape_skip` guards a DIFFERENT graft -- the phase-2 reskin's scale-bone add,
    closed defensively as the C1 follow-up. It is left on the full list on purpose:
    it is not part of the clipping harm this flag targets, and narrowing two crash
    guards at once would make an in-game CTD impossible to attribute."""
    import inspect
    src = inspect.getsource(nc)
    i = src.index("_drape_skip = ")
    assert "_CONFORM_SKIP_NAMES" in src[i:i + 200]
