"""inflate_census reads each converted file against references of its OWN weight.

Both of its references were pinned to weight 1 for every file: the UBE body a
phase-1 piece is measured on, and the CBBE body its author's offset is read
against when the source bundles no body. A `_0` file was therefore scored in
the weight-1 frame on both sides. They now come from canonical_body -- the
zeroed bodies the game loads -- at the file's own weight.
"""
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts.analysis import inflate_census as ic  # noqa: E402


def _grid(z=0.0):
    """A 4x4 vertex sheet with its triangles: enough for every metric."""
    v = np.array([[x, y, z] for y in range(4) for x in range(4)], dtype=np.float64)
    t = []
    for y in range(3):
        for x in range(3):
            a = y * 4 + x
            t += [[a, a + 1, a + 4], [a + 1, a + 5, a + 4]]
    return v, np.array(t, dtype=np.int64)


def test_references_follow_the_files_own_weight(tmp_path, monkeypatch):
    asked = []

    def fake_reference(kind, weight):
        asked.append((kind, weight))
        return _grid(0.0)

    monkeypatch.setattr(ic, "_reference", fake_reference)
    monkeypatch.setattr(ic, "_load", lambda p: {"garment": _grid(0.5)})
    for w in ("0", "1"):
        out = tmp_path / f"piece_{w}.nif"
        src = tmp_path / f"source_{w}.nif"
        out.write_bytes(b"x")
        src.write_bytes(b"x")
        rows, why = ic.score_nif(out, src)
        assert why is None and rows
    # phase-1 UBE basis AND the author's CBBE body, each at the file's weight
    assert asked == [("ube", "_0"), ("cbbe", "_0"), ("ube", "_1"), ("cbbe", "_1")]
