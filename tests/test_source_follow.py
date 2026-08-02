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

"""#source-follow -- classify by what the OUTFIT AUTHOR did, not by what it is called.

Replaces guessing the material from names and texture paths. Measured over 581
bust-covering shapes joined from converted output back to the source mesh:

    source bust weighting | n   | output follow | requirement | short | skin@5u
    WEIGHTED   (>= 0.5)   | 279 |     1.454     |    0.634    |  0.7% |  0.031
    UNWEIGHTED (<  0.5)   | 302 |     0.349     |    0.646    | 69.9% |  0.525

The requirement is the SAME for both, so this is not geometry -- it is entirely
whether the author weighted the bust, and the ones they didn't ARE the clipping
population. Material does not separate it: within the unweighted group the
requirement is 0.634 / 0.662 / 0.640 for soft / rigid / unknown, and soft has MORE
clearance than rigid (1.97 vs 1.55), the opposite of the story the ceiling tells.

THE EXPERIMENT THAT SETTLED IT. On the reported cuirass
(`armor/studded/female/body_1.nif`, `bodyREVISE`), source-converted with the
`studded` keyword NEUTRALISED so name matching could not help:

    keyword neutralised, no source-follow   follow 0.338   71.2% skin under motion
    keyword neutralised + source-follow     follow 0.793    8.8% skin under motion
    keyword ACTIVE (reference)              follow 0.793

Source-follow reproduces the keyword's result EXACTLY, with no material knowledge.

Ships OFF. It only ever RAISES a ceiling, and only on shapes whose author left the
bust unweighted -- `True` and `None` both fall through to today's material path."""
import importlib
import os

import pytest

import src.nif_convert as nc

_ENV = ("CBBE2UBE_NO_SOURCE_FOLLOW", "CBBE2UBE_SOURCE_WEIGHTED_MIN",
        "CBBE2UBE_CHEST_FOLLOW_UNWEIGHTED", "CBBE2UBE_CHEST_FOLLOW_UNKNOWN")


@pytest.fixture(autouse=True)
def _clean():
    yield
    for k in _ENV:
        os.environ.pop(k, None)
    importlib.reload(nc)


class _Shape:
    def __init__(self, name="Cuirass", textures=None):
        self.name = name
        self.textures = textures or {}


def _ctx(armor_breast, body_breast, count):
    """(vw, body_w, idx_k, band) for `count` verts each carrying the given weights."""
    vw = [{"L Breast01": armor_breast, "NPC Spine2 [Spn2]": 1.0 - armor_breast}
          for _ in range(count)]
    body_w = [{"L Breast01": body_breast, "NPC Spine2 [Spn2]": 1.0 - body_breast}
              for _ in range(count)]
    idx_k = [[i] for i in range(count)]
    return vw, body_w, idx_k, list(range(count))


# --- the flag ----------------------------------------------------------------

def test_defaults_on(monkeypatch):
    """Promoted to default ON in 1.2. It only ever ADDS movement to pieces
    nothing was helping, which is why it went first."""
    monkeypatch.delenv("CBBE2UBE_NO_SOURCE_FOLLOW", raising=False)
    assert importlib.reload(nc).SOURCE_FOLLOW_CEILING is True


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_SOURCE_FOLLOW", "1")
    assert importlib.reload(nc).SOURCE_FOLLOW_CEILING is False


# --- measuring the authored weighting ------------------------------------------

def test_reads_the_ratio_of_armor_to_body_bust_weight():
    vw, body_w, idx_k, band = _ctx(0.20, 0.40, 40)
    assert abs(nc._shape_bust_follow(vw, body_w, idx_k, band) - 0.5) < 1e-9


def test_an_unweighted_bust_reads_zero():
    vw, body_w, idx_k, band = _ctx(0.0, 0.40, 40)
    assert nc._shape_bust_follow(vw, body_w, idx_k, band) == 0.0


def test_too_few_bust_verts_is_UNKNOWN_not_zero():
    """Under the floor the answer is None, which falls through to the material path.
    Returning 0.0 would read 'author left it rigid' from a handful of verts and lift
    the ceiling on a shape nobody measured."""
    vw, body_w, idx_k, band = _ctx(0.0, 0.40, 5)
    assert nc._shape_bust_follow(vw, body_w, idx_k, band) is None


def test_body_verts_with_no_bust_weight_are_ignored():
    """Dividing by a body weight of 0 would be meaningless; those verts drop out, and
    if too few survive the answer is None rather than a number built from noise."""
    vw, body_w, idx_k, band = _ctx(0.20, 0.0, 40)
    assert nc._shape_bust_follow(vw, body_w, idx_k, band) is None


def test_matches_breast_bones_by_NAME_not_by_our_own_bone_list():
    """An author may have used a different breast-bone scheme than the one this
    converter grafts. Restricting to `_CHEST_JIGGLE_BONES` would read those shapes as
    unweighted and re-graft a bust that already tracks the body perfectly well."""
    vw = [{"CustomMod L Breast Root": 0.40} for _ in range(40)]
    body_w = [{"L Breast01": 0.40} for _ in range(40)]
    idx_k = [[i] for i in range(40)]
    got = nc._shape_bust_follow(vw, body_w, idx_k, list(range(40)))
    assert got is not None and abs(got - 1.0) < 1e-9


# --- how the ceiling reacts -----------------------------------------------------

def test_unweighted_source_lifts_the_ceiling(monkeypatch):
    """THE point. A shape whose author left the bust unweighted gets its geometric
    requirement allowed through instead of a ceiling picked from its file name."""
    monkeypatch.delenv("CBBE2UBE_NO_SOURCE_FOLLOW", raising=False)
    m = importlib.reload(nc)
    s = _Shape("Cuirass", textures={"Diffuse": "textures/armor/steelplate.dds"})
    assert m._chest_follow_for_shape(s, source_weighted=False) == m._CHEST_FOLLOW_UNWEIGHTED
    assert m._chest_follow_for_shape(s, source_weighted=False) > m._CHEST_FOLLOW_RIGID


def test_weighted_source_falls_through_to_the_material_path(monkeypatch):
    """Already tracks the body (measured 1.454, 0.7% short). Nothing to do, and the
    flag must not change it."""
    monkeypatch.delenv("CBBE2UBE_NO_SOURCE_FOLLOW", raising=False)
    m = importlib.reload(nc)
    s = _Shape("Cuirass", textures={"Diffuse": "textures/armor/steelplate.dds"})
    assert m._chest_follow_for_shape(s, source_weighted=True) == m._CHEST_FOLLOW_RIGID


def test_unknown_source_falls_through_to_the_material_path(monkeypatch):
    """Too few bust verts to judge -> today's behaviour, not a guess."""
    monkeypatch.delenv("CBBE2UBE_NO_SOURCE_FOLLOW", raising=False)
    m = importlib.reload(nc)
    s = _Shape("Cuirass", textures={"Diffuse": "textures/armor/steelplate.dds"})
    assert m._chest_follow_for_shape(s, source_weighted=None) == m._CHEST_FOLLOW_RIGID


def test_flag_off_ignores_the_measurement_entirely(monkeypatch):
    """REGRESSION -- with the flag off the shipped path must be bit-for-bit unchanged
    no matter what the measurement said."""
    monkeypatch.setenv("CBBE2UBE_NO_SOURCE_FOLLOW", "1")
    m = importlib.reload(nc)
    s = _Shape("Cuirass", textures={"Diffuse": "textures/armor/steelplate.dds"})
    for sw in (True, False, None):
        assert m._chest_follow_for_shape(s, source_weighted=sw) == m._CHEST_FOLLOW_RIGID


def test_it_can_only_ever_RAISE_a_ceiling(monkeypatch):
    """Safety property: for every material the lifted ceiling is >= the material one,
    so enabling the flag cannot make a garment follow the body LESS than it does now."""
    monkeypatch.delenv("CBBE2UBE_NO_SOURCE_FOLLOW", raising=False)
    m = importlib.reload(nc)
    for dif in ("steelplate.dds", "impleather.dds", "piece_001.dds"):   # rigid/soft/unknown
        s = _Shape("Cuirass", textures={"Diffuse": f"textures/armor/{dif}"})
        assert (m._chest_follow_for_shape(s, source_weighted=False)
                >= m._chest_follow_for_shape(s, source_weighted=None))


# --- wired in -------------------------------------------------------------------

def test_the_measurement_reads_the_SOURCE_not_the_converted_state():
    """The measurement asks what the AUTHOR weighted, so it must not read weight
    OUR OWN passes wrote.

    Reading the converted shape was equivalent until the torso jiggle graft went
    default-ON and started running BEFORE this pass: its weight then read as
    authorship, flipping a shape from 'unweighted' (ceiling lifted to the full
    geometric requirement) to 'weighted' (capped at the material ceiling).
    MEASURED on a metal cuirass: 0.616 with both features vs 0.792 from the
    chest pass alone. #chest-follow-passthrough"""
    import inspect
    tgt = inspect.getsource(nc._chest_follow_target)
    assert "src_vw if src_vw is not None else vw" in tgt, (
        "the source weighting must be preferred over the converted state")
    # and the source map must come from the SOURCE nif, by name + vert count
    smap = inspect.getsource(nc._source_bust_weight_map)
    assert "NifFile(filepath=str(src_nif_path))" in smap
    assert "len(ss.verts) != n_verts" in smap, (
        "a topology mismatch must fall back, not mis-map weights")
    # wired from both pipeline sites
    whole = inspect.getsource(nc)
    assert whole.count("src_nif_path=src_path") == 2, (
        "both conversion paths must pass the source through")


def test_the_measurement_uses_the_same_verts_as_the_requirement():
    """Both answer a question about the same surface. Deriving the requirement from
    one set of bust verts and the authored weighting from another would let a shape
    be judged unweighted on verts the graft never sizes. Now structural: one
    `_chest_band` feeds the requirement, the source measurement AND the
    achieved-follow check."""
    import inspect
    tgt = inspect.getsource(nc._chest_follow_target)
    assert "band = _chest_band(" in tgt
    assert tgt.count("band") >= 3
    assert "_chest_band(" in inspect.getsource(nc._match_rigid_leg_bend_to_body), (
        "the deferral's achieved-follow check must judge the same verts")
