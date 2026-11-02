"""#tri-follow: switched off by default, and its row editing keeps the .tri's own shape."""
import numpy as np

from src import tri_follow as tf_
from src.tri import TriMorph, TriShape


def test_on_by_default_and_a_no_op_without_a_pair(tmp_path, monkeypatch):
    assert tf_.TRI_FOLLOW_FLOOR and tf_.TRI_FOLLOW_CHORD and tf_.TRI_FOLLOW_EDGE
    assert tf_.follow_pair(tmp_path / "piece_1.nif") is None          # nothing on disk
    for nm in ("TRI_FOLLOW_FLOOR", "TRI_FOLLOW_CHORD", "TRI_FOLLOW_EDGE"):
        monkeypatch.setattr(tf_, nm, False)
    assert tf_.follow_pair(tmp_path / "piece_1.nif") is None          # all off


def test_nipple_local_sliders_are_never_touched():
    assert tf_._nipple_slider("NippleLength") and tf_._nipple_slider("AreolaeSizeBig")
    assert not tf_._nipple_slider("BreastsTBD") and not tf_._nipple_slider("Amazon")


def test_set_row_replaces_an_existing_row_and_adds_a_missing_one():
    ts = TriShape("Top", [TriMorph("Amazon", np.array([0, 1]), np.array([[0, 0, 1.0], [0, 0, 1.0]]))])
    by = {m.name.lower(): m for m in ts.morphs}
    new = np.zeros((3, 3)); new[2] = (0, 0, 0.5)
    tf_._set_row(ts, by, "Amazon", new)                 # verts 0/1 now zero -> dropped from the sparse row
    assert list(ts.morphs[0].idx) == [2] and np.allclose(ts.morphs[0].delta, [[0, 0, 0.5]])
    tf_._set_row(ts, by, "Peachy", new)
    assert [m.name for m in ts.morphs] == ["Amazon", "Peachy"] and list(ts.morphs[1].idx) == [2]


def test_dense_ignores_out_of_range_rows():
    m = TriMorph("X", np.array([0, 5]), np.array([[1.0, 0, 0], [0, 1.0, 0]]))
    D = tf_._dense(m, 3)
    assert D.shape == (3, 3) and np.allclose(D[0], [1, 0, 0]) and not D[1:].any()
