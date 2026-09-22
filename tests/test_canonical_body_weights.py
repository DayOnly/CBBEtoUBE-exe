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
    monkeypatch.setattr(cb.pynifly, "NifFile", _FakeNif)


def _fake_zeroed(calls):
    """Stands in for src.zeroed_body.zeroed_body: records each request and
    names the file after it, so a test can see which body came back."""
    from src import zeroed_body as zb

    def fake(kind, weight="_1", **_kw):
        calls.append((kind, weight))
        return zb.ZeroedBody(Path(f"{kind}_femalebody{weight}.nif"), kind.upper(),
                             "set", 0.0)
    return fake


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


def test_canonical_bodies_are_the_zeroed_bodies_at_each_weight(monkeypatch):
    """Both sides come from the zeroed-body resolver, each at its own weight --
    never from a body picked by mod name or path length."""
    from src import zeroed_body as zb
    calls = []
    monkeypatch.setattr(zb, "zeroed_body", _fake_zeroed(calls))
    assert cb.canonical_cbbe(weight="_0") == ("cbbe_femalebody_0.nif", "CBBE")
    assert cb.canonical_cbbe() == ("cbbe_femalebody_1.nif", "CBBE")
    assert cb.canonical_ube(weight="_0") == ("ube_femalebody_0.nif", "UBE")
    assert cb.canonical_ube() == ("ube_femalebody_1.nif", "UBE")
    assert calls == [("cbbe", "_0"), ("cbbe", "_1"), ("ube", "_0"), ("ube", "_1")]


def test_the_default_call_is_the_weight1_call(monkeypatch):
    """Every existing caller passes no weight and must get weight 1."""
    from src import zeroed_body as zb
    monkeypatch.setattr(zb, "zeroed_body", _fake_zeroed([]))
    assert cb.canonical_cbbe() == cb.canonical_cbbe(weight="_1")


def test_no_zeroed_body_is_an_error_never_another_body(monkeypatch):
    """What callers catch to skip a weight: a FileNotFoundError."""
    from src import zeroed_body as zb

    def refuse(kind, weight="_1", **_kw):
        raise zb.ZeroedBodyError("not a zeroed build")
    monkeypatch.setattr(zb, "zeroed_body", refuse)
    with pytest.raises(FileNotFoundError, match="not a zeroed build"):
        cb.canonical_cbbe(weight="_0")
