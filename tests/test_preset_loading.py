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

"""Preset loading: the WHOLE preset, and resolved EXACTLY.

WHY A PRESET AT ALL. Scoring sliders one at a time was the wrong population --
a preset engages ~180 of them together and their residuals ADD, so a combination
pokes where no single slider does. `--preset` exists to score what ships.

WHY EXACT MATCHING. A single slider can afford the loose suffix rule `_match`
uses. A whole preset cannot: 'Butt' would silently bind to 'BaseShapeBigButt' and
the run would apply the wrong morph while looking perfectly healthy. Anything
needing the loose rule is reported as FUZZY, never folded in quietly.
"""
import json

import pytest

from scripts.analysis.morph_clip_test import _load_preset, _resolve_preset

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SliderPresets>
  <Preset name="T" set="X">
    <SetSlider name="Boobs" size="big" value="80"/>
    <SetSlider name="Boobs" size="small" value="10"/>
    <SetSlider name="Butt" size="big" value="50"/>
    <SetSlider name="Zeroed" size="big" value="0"/>
  </Preset>
</SliderPresets>
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_bodyslide_xml_takes_the_big_side_as_a_fraction(tmp_path):
    pv, kind = _load_preset(_write(tmp_path, "p.xml", _XML))
    assert "bodyslide" in kind
    assert pv["Boobs"] == pytest.approx(0.80), "must take the weight-100 side"
    assert pv["Butt"] == pytest.approx(0.50)
    assert pv["Zeroed"] == pytest.approx(0.0)


def test_racemenu_jslot_sums_its_per_mod_keys(tmp_path):
    doc = {"bodyMorphs": [
        {"name": "Boobs", "keys": [{"key": "a.esp", "value": 0.25},
                                   {"key": "b.esp", "value": 0.5}]},
        {"name": "Butt", "keys": [{"key": "a.esp", "value": 0.1}]}]}
    pv, kind = _load_preset(_write(tmp_path, "p.jslot", json.dumps(doc)))
    assert "jslot" in kind
    assert pv["Boobs"] == pytest.approx(0.75), "skee SUMS the per-mod keys"
    assert pv["Butt"] == pytest.approx(0.1)


def test_resolution_is_exact_and_never_binds_butt_to_bigbutt():
    """THE hazard. Loose suffix matching would resolve 'Butt' onto
    'BaseShapeBigButt' and silently morph the wrong thing."""
    bm = {"BaseShapeBigButt": None, "BaseShapeButt": None, "BaseShapeBoobs": None}
    sel, missing, fuzzy = _resolve_preset({"Butt": 0.5, "Boobs": 0.8}, bm)
    assert sel["BaseShapeButt"] == pytest.approx(0.5)
    assert "BaseShapeBigButt" not in sel
    assert not missing and not fuzzy


def test_zero_valued_sliders_are_not_engaged():
    sel, missing, fuzzy = _resolve_preset({"Boobs": 0.0}, {"BaseShapeBoobs": None})
    assert not sel, "a slider at 0 changes nothing and must not count as engaged"


def test_unresolvable_sliders_are_reported_not_dropped():
    """A preset built for a different slider set is a COVERAGE fact -- 68 of
    Punk UBE's 183 engaged sliders do not exist on the UBE body. Silently
    dropping them would make a 62%-coverage run look complete."""
    sel, missing, fuzzy = _resolve_preset({"NotAThing": 0.9}, {"BaseShapeBoobs": None})
    assert not sel
    assert missing == ["NotAThing"]
