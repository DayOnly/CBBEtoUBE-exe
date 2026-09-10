"""A swallowed pass exception must reach the per-piece failure list.

Each helper below wraps its whole body in ``except Exception`` and returns a
neutral value on a raise, which the caller reads as "nothing to do". Until
2026-09-01 that handler was mute, so a broken pass shipped as a clean one.
These tests raise INSIDE the try (an object that ``np.asarray`` cannot
convert) and assert the recorder saw it. The structural guard in
``test_no_silent_pass_failures.py`` cannot see these sites because their try
bodies call no PASS_HINTS helper; this is the behavioural check for them.
"""
from __future__ import annotations

import numpy as np
import pytest

from src import nif_convert as nc


class _Unconvertible:
    """np.asarray(..., dtype=...) raises on this; never silently coerces."""

    def __array__(self, *a, **k):
        raise RuntimeError("injected")


@pytest.fixture
def clean_recorder():
    before = list(nc._PASS_FAILURES_THIS_PIECE)
    nc._PASS_FAILURES_THIS_PIECE.clear()
    yield
    nc._PASS_FAILURES_THIS_PIECE[:] = before


def _recorded(label: str) -> bool:
    return any(e.startswith(f"PASS FAILED {label} ") or f"PASS FAILED {label}(" in e
               for e in nc._piece_pass_failures())


_BAD = _Unconvertible()
_V = np.zeros((4, 3))
_T = np.array([[0, 1, 2], [1, 2, 3]])
_ONES = np.ones(4)

CASES = [
    # (label, call) -- every call raises inside the helper's own try.
    ("_smooth_push_field", lambda: nc._smooth_push_field(_ONES, _ONES, _BAD)),
    ("_smooth_vertex_field", lambda: nc._smooth_vertex_field(_V, _BAD)),
    ("_smooth_warp_grooves", lambda: nc._smooth_warp_grooves(_BAD, _V, _V)),
    ("_surface_deficit", lambda: nc._surface_deficit(
        _V, _BAD, _V, _V, np.ones(4, bool), _ONES, None, (0.0, 1.0))),
    ("_bust_morph_chord_req", lambda: nc._bust_morph_chord_req(
        _V, _BAD, None, _V, _V, np.ones(4, bool), (0.0, 1.0), None, [0])),
]


@pytest.mark.parametrize("label,call", CASES, ids=[c[0] for c in CASES])
def test_raise_inside_helper_is_recorded(clean_recorder, label, call):
    result = call()
    # The helper still returns its neutral value: the pass degrades, the
    # piece is not lost ...
    assert result is None or isinstance(result, np.ndarray)
    # ... and the degrade is on the record.
    assert _recorded(label), nc._piece_pass_failures()


def test_no_raise_records_nothing(clean_recorder):
    """Negative control: the recorder is not tripped by a clean call."""
    out = nc._smooth_vertex_field(_V, _T)
    assert isinstance(out, np.ndarray)
    assert nc._piece_pass_failures() == []


def test_validator_says_when_it_did_not_run(monkeypatch, tmp_path):
    """validate_dst_nif must not return an EMPTY list when its z-fight check
    raised -- empty reads as clean."""
    class _Shape:
        """Textured (so the z-fight census picks it) with no skin (so the
        weight checks skip it); reading its verts raises INSIDE the census."""
        name = "X"
        textures = {"Diffuse": "x.dds"}
        bone_names = []
        bone_weights = {}

        @property
        def verts(self):
            raise RuntimeError("injected")

        def __getattr__(self, item):
            return None

    class _Nif:
        def __init__(self, *a, **k):
            self.shapes = [_Shape()]

        def __getattr__(self, item):
            return None

    class _Pyn:
        NifFile = _Nif

    dst = tmp_path / "x_1.nif"
    dst.write_bytes(b"")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(nc, "_pynifly", lambda: _Pyn())
        warnings = nc.validate_dst_nif(dst)
    assert any("z-fight check NOT run" in w for w in warnings), warnings
