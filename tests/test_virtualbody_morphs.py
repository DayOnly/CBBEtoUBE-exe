"""#virtualbody-morphs: an authored VirtualBody collider is an armour shape for the TRI (it gets its own morphs, as
every other collider does); only the injected BaseShape is the verbatim body."""
import types

import numpy as np

from src import nif_convert as nc
from src import nif_convert_trigen as tg


def _nif(monkeypatch):
    monkeypatch.setattr(nc, "shape_body_offset", lambda s: np.zeros(3))
    monkeypatch.setattr(nc, "_extremity_vert_fraction", lambda s, n: None)
    sh = lambda name, n: types.SimpleNamespace(name=name, verts=np.zeros((n, 3)), global_to_skin=None)
    return types.SimpleNamespace(filepath=None, shapes=[sh("VirtualBody", 9687), sh("Corset", 40), sh("BaseShape", 29298)])


def test_an_authored_virtualbody_gets_morphs(monkeypatch):
    armour, body, _ef = tg._collect_tri_inputs(_nif(monkeypatch), 29298)
    assert "VirtualBody" in armour and "Corset" in armour
    assert body == {"BaseShape"}
    assert tg._VIRTUALBODY_MORPHS is True


def test_the_kill_switch_restores_the_old_split(monkeypatch):
    monkeypatch.setattr(tg, "_VIRTUALBODY_MORPHS", False)
    armour, body, _ef = tg._collect_tri_inputs(_nif(monkeypatch), 29298)
    assert "VirtualBody" not in armour and body == {"BaseShape", "VirtualBody"}



def test_the_refresh_never_takes_the_collider_for_the_body(monkeypatch, tmp_path):
    """#virtualbody-morphs: with no BaseShape, `ube_body_shape` falls back to VirtualBody, and the late TRI refresh
    regenerated the whole TRI against that 996-vert collider (the tome shipped every visible shape scrambled). The
    refresh must take the injected BaseShape or the reference body it is handed, never the collider."""
    import src.sliderset_gen as sg
    from tests import _converter_sources as _cs
    from tests.test_tri_reimport_refresh import _mk, _FakeShape
    tri = tmp_path / "piece.tri"; tri.write_bytes(b"PIRT\x00\x00")
    shapes = [_FakeShape("Cloth", np.zeros((4, 3))), _FakeShape("VirtualBody", np.ones((9, 3)))]
    saved = _mk(monkeypatch, shapes, ["Cloth"], generated=["Cloth", "VirtualBody"])
    monkeypatch.setattr(nc, "UBE_BODY_INJECT_NAMES", ("BaseShape", "VirtualBody"))
    _cs.patch(monkeypatch, "_surface_track_inputs", lambda *a, **k: (None, None))
    for fn in ("_nipple_morph_bridge_inputs", "_morph_body_tris"):
        _cs.patch(monkeypatch, fn, lambda *a, **k: None)
    seen = {}

    def _gen(av, body, osd, **k):
        seen["n"] = len(body)
        return type("T", (), {"shapes": [_FakeShape(n, None) for n in ("Cloth", "VirtualBody")]})()
    monkeypatch.setattr(sg, "generate_armor_tri", _gen)
    ref = _FakeShape("BaseShape", np.zeros((29298, 3)))
    assert nc._refresh_armor_tri_after_reimport(tri, tri, fallback_body_shape=ref) is True
    assert seen["n"] == 29298, "regenerated against the collider, not the body"
    assert "VirtualBody" in saved["names"]
