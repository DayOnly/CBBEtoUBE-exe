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

"""Layered cloth takes BUTT jiggle, and nothing else (#layered-cloth-butt-follow).

`#layered-cloth-skin` kept a multi-layer cloth stack off every body-follow graft.
In game that left a quilted skirt rigid over a bouncing butt while the trousers
under it followed, and skin showed through the skirt on the swinging leg's cheek.
The carve-out lets the jiggle graft give such a shape the BUTT region only, on a
piece with no physics XML. Breast weight on layered cloth is what ballooned the
chest in game (0.66 and 0.15), so breast -- and belly with it -- stays closed.

The region names below are LITERALS on purpose: a set derived from the code under
test would agree with any mutation of it.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import src.nif_convert as nc  # noqa: E402
from src import nif_convert_weights as ncw  # noqa: E402
from tests import _converter_sources as _cs  # noqa: E402

MIN = nc._CONFORM_MIN_JIGGLE_VERTS
_BONE = {"breast": "L Breast01", "butt": "NPC L Butt", "belly": "NPC Belly"}


def _weights(**per_region):
    """{bone: [(vert, weight)]} with N verts over 0.1 for each named region."""
    return {_BONE[k]: [(i, 0.5) for i in range(n)] for k, n in per_region.items() if n}


def _old_gate(bw):
    """The #region-jiggle-gate exactly as the pass computed it inline before the
    helper existed. A non-layered shape must get the same answer from the helper."""
    have = {kw: 0 for kw in ("breast", "butt", "belly")}
    for b, pairs in bw.items():
        kw = ncw._jiggle_region_of(b)
        if kw is not None:
            have[kw] += sum(1 for _vi, w in pairs if float(w) > 0.1)
    return {kw for kw, c in have.items() if c >= MIN}


class _Nif:
    pass


# ------------------------------------------------------------------ the flag

def test_flag_defaults_ON_and_the_kill_switch_is_honoured(monkeypatch):
    assert nc.LAYERED_CLOTH_BUTT_JIGGLE is True
    monkeypatch.setenv("CBBE2UBE_NO_LAYERED_CLOTH_BUTT_JIGGLE", "1")
    reloaded = importlib.reload(nc)
    try:
        assert reloaded.LAYERED_CLOTH_BUTT_JIGGLE is False
    finally:
        monkeypatch.delenv("CBBE2UBE_NO_LAYERED_CLOTH_BUTT_JIGGLE", raising=False)
        importlib.reload(nc)


def test_the_carve_out_is_the_butt_and_only_the_butt():
    """Breast weight on layered cloth ballooned the chest in game. Widening this
    tuple is widening a rule an in-game failure wrote."""
    assert tuple(nc._LAYERED_CLOTH_JIGGLE_REGIONS) == ("butt",)


# ------------------------------------------- which pieces the carve-out reaches

def _regions(monkeypatch, *, names, flag=True, has_xml=False):
    calls = []

    def _xml(path, nif=None):
        calls.append(path)
        return has_xml
    monkeypatch.setattr(nc, "LAYERED_CLOTH_BUTT_JIGGLE", flag)
    monkeypatch.setattr(nc, "_piece_has_hdt_xml", _xml)
    return ncw._layered_cloth_jiggle_regions("piece_1.nif", _Nif(), names), calls


def test_a_piece_with_no_xml_opens_the_butt(monkeypatch):
    got, _ = _regions(monkeypatch, names={"Cuirass_A", "Cuirass_B"})
    assert got == frozenset({"butt"})


def test_a_piece_with_a_physics_xml_keeps_the_blanket_skip(monkeypatch):
    """Every SMP interaction behind #layered-cloth-skin needed an XML."""
    got, calls = _regions(monkeypatch, names={"Cuirass_A", "Cuirass_B"}, has_xml=True)
    assert got == frozenset()
    assert calls, "the XML must actually be consulted"


def test_the_kill_switch_restores_the_blanket_skip_without_reading_the_xml(monkeypatch):
    got, calls = _regions(monkeypatch, names={"Cuirass_A", "Cuirass_B"}, flag=False)
    assert got == frozenset()
    assert calls == [], "switched off, the pass must do no extra work"


def test_a_piece_without_layered_cloth_reads_no_xml(monkeypatch):
    got, calls = _regions(monkeypatch, names=set())
    assert got == frozenset()
    assert calls == []


# --------------------------------------------------- the regions a shape may take

def test_a_layered_shape_with_no_jiggle_may_take_the_butt_only():
    closed = ncw._jiggle_regions_closed(_weights(), frozenset({"butt"}))
    assert closed == {"breast", "belly"}


def test_a_layered_shape_that_already_follows_the_butt_is_skipped():
    """All three closed -> the pass's `continue`: nothing is re-grafted over the
    author's (or an earlier pass's) butt weight."""
    closed = ncw._jiggle_regions_closed(_weights(butt=MIN), frozenset({"butt"}))
    assert closed == {"breast", "butt", "belly"}


def test_a_layered_shape_never_gets_breast_even_where_the_body_has_it():
    """The per-vert filter as the pass applies it, over a body vert that carries
    all three regions."""
    closed = ncw._jiggle_regions_closed(_weights(), frozenset({"butt"}))
    body_vert = {"L Breast01": 0.6, "NPC L Butt": 0.2, "NPC Belly": 0.3,
                 "NPC Pelvis [Pelv]": 0.5}
    grafted = {b for b, w in body_vert.items()
               if ncw._is_physics_jiggle_scale_bone(b) and w > 1e-3
               and ncw._jiggle_region_of(b) not in closed}
    assert grafted == {"NPC L Butt"}, grafted


@pytest.mark.parametrize("regions", [
    {}, {"breast": 295, "belly": 33}, {"butt": MIN}, {"butt": MIN - 1},
    {"breast": MIN, "butt": MIN, "belly": MIN}, {"belly": MIN - 1, "breast": 2}])
def test_a_shape_that_is_not_layered_gets_the_old_answer(regions):
    """Everything that is not layered cloth must be untouched by the change."""
    bw = _weights(**regions)
    assert ncw._jiggle_regions_closed(bw) == _old_gate(bw)
    assert ncw._jiggle_regions_closed(bw, None) == _old_gate(bw)


# ------------------------------------------------------------ the pass wiring

def test_the_pass_decides_the_carve_out_in_one_place():
    src = _cs.source(nc._transfer_body_jiggle_to_fitted)
    assert "layered_regions = _layered_cloth_jiggle_regions(dst_path, nf, layered_cloth_names)" in src
    assert "already = _jiggle_regions_closed(bw, layered_regions if layered else None)" in src


def test_the_pass_still_skips_layered_cloth_when_the_carve_out_is_closed():
    src = _cs.source(nc._transfer_body_jiggle_to_fitted)
    i = src.index("if (s.name in softbody_names")
    skip = src[i:src.index("continue", i)]
    assert "(layered and not layered_regions)" in skip
