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

r"""Guards for the UBE-native geometry backstop.

The `!UBE\` path gate catches converted output and UBE-native mods that follow
the convention. A UBE-native mod shipping at NORMAL paths looks exactly like a
CBBE source, and converting it refits an already-UBE mesh onto the UBE body a
second time. Geometry decides: compare the most body-hugging sampled mesh to the
UBE and CBBE reference bodies.

THE SAFETY PROPERTY, and the reason for the asymmetric thresholds: a false
"ube" verdict SKIPS a real CBBE mod, leaving its armor unfitted in game. A miss
merely costs a double-convert. So an inconclusive fit must stay "unknown".
Verified against real mods: the same outfit shipped in both flavours separates
cleanly (UBE build dUBE=0.00/dCBBE=0.49 -> ube; 3BA build dUBE=0.37/dCBBE=0.00
-> cbbe), and no CBBE mod on a 160-source modlist was skipped.
"""
import pytest

from src import auto_convert


@pytest.fixture
def fit(monkeypatch):
    """Stub the per-mesh fit so the DECISION RULE is what is under test."""
    def _set(du, dc):
        monkeypatch.setattr(auto_convert, "_mod_armor_nifs",
                            lambda d, n=6: ["fake.nif"])
        monkeypatch.setattr(auto_convert, "_mesh_body_fit",
                            lambda p, u, c: (du, dc))
    return _set


def _verdict(tmp_path):
    return auto_convert._ube_native_verdict(tmp_path, object(), object())


def test_decisive_ube_hug_is_high_confidence(tmp_path, fit):
    fit(0.00, 0.49)                     # sits ON the UBE body
    v, c, _s = _verdict(tmp_path)
    assert (v, c) == ("ube", "high")


def test_decisive_cbbe_hug_is_high_confidence(tmp_path, fit):
    fit(0.37, 0.00)                     # sits ON the CBBE body
    v, c, _s = _verdict(tmp_path)
    assert (v, c) == ("cbbe", "high")


def test_ambiguous_fit_never_yields_a_verdict(tmp_path, fit):
    """Both bodies about equally far: a bulky or off-body mesh. Must stay
    unknown -- this is the case that would otherwise skip a real CBBE mod."""
    fit(1.57, 1.58)
    v, c, _s = _verdict(tmp_path)
    assert (v, c) == ("unknown", "low")


def test_far_from_both_bodies_is_unknown(tmp_path, fit):
    fit(9.21, 9.37)                     # loose drape, hugs nothing
    v, c, _s = _verdict(tmp_path)
    assert v == "unknown"


def test_close_to_ube_but_not_decisively_is_unknown(tmp_path, fit):
    """Near the UBE body but CBBE is nearly as near -> no separation, so no
    verdict. Guards the ratio half of the rule, not just the distance half."""
    # BOTH values must clear the distance gate (_UBE_HUG_DIST=0.15) or this
    # test never reaches the ratio check and silently guards nothing --
    # 0.10 vs 0.11 is inside the hug distance but only 1.1x apart, under
    # the 1.5x separation the rule demands.
    fit(0.10, 0.11)
    v, c, _s = _verdict(tmp_path)
    assert v == "unknown"


def test_unreadable_mod_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_convert, "_mod_armor_nifs", lambda d, n=6: [])
    v, c, s = _verdict(tmp_path)
    assert (v, c) == ("unknown", "low") and "no readable body mesh" in s[0]


def test_missing_directory_is_unknown_not_a_crash():
    v, c, _s = auto_convert._ube_native_verdict(None, object(), object())
    assert v == "unknown"


def test_opt_out_flag_exists():
    """The backstop can misjudge; users need an escape hatch."""
    p = auto_convert._build_parser()
    ns = p.parse_args(["auto", "--no-ube-native-scan"])
    assert ns.no_ube_native_scan is True
    assert p.parse_args(["auto"]).no_ube_native_scan is False
