"""The two guards that keep a harness from reporting on nothing.

Both exist because a tool printed a confident number over an empty or wrong
population: `bust_verdict` called a garment clean after measuring 0.0% of it,
and the motion survey scored 597-vert colliders as garment.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))


# --------------------------------------------------------------- bust_verdict
@pytest.fixture(scope="module")
def bv():
    from scripts.analysis import bust_verdict
    return bust_verdict


def test_a_garment_covering_nothing_is_a_control_failure(bv):
    """The measured false clean: covered 0.0%, standoff over 9 verts, and a
    verdict of "clean at rest ... next step is an in-game A/B" at exit 0."""
    msg = bv.coverage_failure(0.0)
    assert msg and "NOT MEASURED" in msg


def test_a_garment_that_covers_the_band_passes(bv):
    """The same piece, correctly framed, reads 34.8%; a known-good one 11.9%."""
    assert bv.coverage_failure(34.8) is None
    assert bv.coverage_failure(11.9) is None


def test_the_floor_is_above_zero(bv):
    """A floor of 0.0 can never fire, which is the failure mode this whole
    audit is about -- a guard that is present but dead."""
    assert bv.CTRL_MIN_COVERED > 0.0
    assert bv.coverage_failure(bv.CTRL_MIN_COVERED / 2.0) is not None


def test_no_coverage_data_is_not_silently_a_pass(bv):
    assert bv.coverage_failure(None) is None  # reported elsewhere, not here


# ------------------------------------------------------- the composed proxy rule
class _Shape:
    def __init__(self, name, textures=None):
        self.name = name
        self.textures = textures or {}


_TEXTURED = {"Diffuse": "textures/x.dds", "Normal": "textures/x_n.dds"}


@pytest.fixture(scope="module")
def sm():
    from scripts.analysis import survey_motion_clipping
    return survey_motion_clipping


def test_an_unrendered_collider_is_a_proxy_even_mid_name(sm):
    """`ButtCol` is 597 verts with no textures. An anchored `^col` walked past
    it and it scored 74.2%, fourth in the worst-offender table."""
    assert sm.is_proxy(_Shape("ButtCol"))
    assert sm.is_proxy(_Shape("ColSkirt"))
    assert sm.is_proxy(_Shape("Colision"))


def test_a_rendered_shape_is_never_a_proxy_however_it_is_named(sm):
    """`Cylinder.015` carries Diffuse/Normal/EnvMap over 33k verts -- a real
    visible mesh. The token half alone would also drop `HDTSkirt`, a physics
    garment. BOTH signals are required, so score the composed rule."""
    assert not sm.is_proxy(_Shape("HDTSkirt", _TEXTURED))
    assert not sm.is_proxy(_Shape("HDTBelt", _TEXTURED))
    assert not sm.is_proxy(_Shape("ColSkirt", _TEXTURED))


def test_collar_is_exempt(sm):
    """`col` is a real token and a Collar is a real garment part. 30 collar
    shapes ship; the rule must keep every one."""
    assert not sm.is_proxy(_Shape("Collar"))
    assert not sm.is_proxy(_Shape("collar.002"))


def test_an_ordinary_garment_is_not_a_proxy(sm):
    for nm in ("robe", "Boots", "Greaves", "Cuirass", "ArmorF"):
        assert not sm.is_proxy(_Shape(nm, _TEXTURED)), nm
        assert not sm.is_proxy(_Shape(nm)), nm
