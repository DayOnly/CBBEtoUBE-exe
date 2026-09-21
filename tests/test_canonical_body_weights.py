"""A weight-0 garment must be measured against a weight-0 body.

`canonical_body` picks ONE reference body per side, and every census that pairs
a garment with a reference body goes through it. It used to take no weight, so a
tool that widened its population to both weights would have scored every `_0`
file against the weight-1 body: the wrong frame, a believable number, never an
error. Several census tools are declared blind spots for exactly that reason.

The rule pinned here: weight 0 is the SIBLING of the chosen weight-1 file, from
the same directory -- never a second, independent lookup (which can land on a
different preset) and never a silent fall-back to weight 1.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import canonical_body as cb  # noqa: E402


class _Shape:
    def __init__(self, name, n):
        self.name = name
        self.verts = [(0.0, 0.0, 0.0)] * n


class _FakeNif:
    """Stands in for pynifly: the body is the largest shape."""

    def __init__(self, _path):
        self.shapes = [_Shape("BaseShape", 10), _Shape("tiny", 1)]


def _pair(tmp_path, stem, both=True):
    d = tmp_path / "mod" / "meshes" / "Body"
    d.mkdir(parents=True)
    p1 = d / (stem + "_1.nif")
    p1.write_bytes(b"x")
    p0 = d / (stem + "_0.nif")
    if both:
        p0.write_bytes(b"x")
    return p1, p0


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(cb, "_CACHE", {})
    monkeypatch.setattr(cb.pynifly, "NifFile", _FakeNif)


def test_weight_sibling_is_the_same_directorys_weight0_file(tmp_path):
    p1, p0 = _pair(tmp_path, "femalebody")
    got = Path(cb.weight_sibling(p1, "_0"))
    assert got == p0
    assert got.parent == p1.parent


def test_weight_sibling_leaves_weight1_alone(tmp_path):
    p1, _ = _pair(tmp_path, "femalebody")
    assert Path(cb.weight_sibling(p1, "_1")) == p1


def test_a_missing_sibling_raises_and_never_falls_back_to_weight1(tmp_path):
    """The whole point. Handing back the weight-1 body would measure a `_0`
    garment in the wrong frame and print a believable number."""
    p1, _ = _pair(tmp_path, "femalebody", both=False)
    with pytest.raises(FileNotFoundError):
        cb.weight_sibling(p1, "_0")


def test_an_unknown_weight_is_refused(tmp_path):
    p1, _ = _pair(tmp_path, "femalebody")
    with pytest.raises(ValueError):
        cb.weight_sibling(p1, "_2")


def test_a_path_that_is_not_weight1_is_refused(tmp_path):
    p = tmp_path / "femalebody.nif"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        cb.weight_sibling(p, "_0")


def test_canonical_cbbe_keys_its_cache_by_weight(tmp_path, monkeypatch):
    p1, p0 = _pair(tmp_path, "femalebody")
    monkeypatch.setattr(cb.glob, "glob", lambda *a, **k: [str(p1)])
    assert Path(cb.canonical_cbbe(tmp_path)[0]) == p1
    assert Path(cb.canonical_cbbe(tmp_path, weight="_0")[0]) == p0


def test_canonical_ube_keys_its_cache_by_weight(tmp_path, monkeypatch):
    p1, p0 = _pair(tmp_path, "femalebody_tangent")
    from src import auto_convert as ac
    monkeypatch.setattr(ac, "_find_ube_body_ref", lambda *a, **k: p1)
    assert Path(cb.canonical_ube()[0]) == p1
    assert Path(cb.canonical_ube(weight="_0")[0]) == p0


def test_the_default_call_is_the_weight1_call(tmp_path, monkeypatch):
    """Every existing caller passes no weight and must get exactly what it got."""
    p1, _ = _pair(tmp_path, "femalebody")
    monkeypatch.setattr(cb.glob, "glob", lambda *a, **k: [str(p1)])
    assert cb.canonical_cbbe(tmp_path) == cb.canonical_cbbe(tmp_path, weight="_1")
    assert Path(cb.canonical_cbbe(tmp_path)[0]) == p1
