"""A piece that loses its BODYTRI must be COUNTED, not just mentioned.

Such a piece still converts -- the mesh is fine, it simply has no body morphs
and stops following the player's sliders in game. Because its status stays
"converted", every counter in the summary read clean while 4 pieces shipped
that way on a 161-mod run. The detail existed only in a per-mod .txt reason.
"""
from pathlib import Path
from types import SimpleNamespace

from src import auto_convert


def _res(name, reason, status="converted (body-swap)"):
    return SimpleNamespace(src_path=Path(name), reason=reason, status=status)


def _mod(*results):
    m = auto_convert.AutoConvertResult.__new__(auto_convert.AutoConvertResult)
    m.nif_results = list(results)
    return m


def test_counts_a_lost_tri():
    m = _mod(_res("a_1.nif", "shape op failures: auto-TRI (body-morph "
                             "unavailable: OutputLockedError(...))"),
             _res("b_1.nif", ""))
    assert m.nif_morph_losses == 1
    assert [r.src_path.name for r in m.nif_morph_loss_results] == ["a_1.nif"]


def test_counts_a_failed_bodytri_injection():
    m = _mod(_res("c_1.nif", "HDT/BODYTRI injection failed (OSError()) -- "
                             "piece may lack cloth physics / body-morph"))
    assert m.nif_morph_losses == 1


def test_unrelated_warnings_are_not_counted():
    """Must not fire on the ordinary z-fight / HDT-XML chatter, or the counter
    is noise and gets ignored -- which is how the real one stayed invisible."""
    m = _mod(_res("d_1.nif", "z-fight risk: Belt ? Thong share 4 verts"),
             _res("e_1.nif", "HDT XML failed to parse: not well-formed"),
             _res("f_1.nif", None))
    assert m.nif_morph_losses == 0
