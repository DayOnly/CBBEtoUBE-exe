"""#stack-push: the copy path's anti-poke moves a layered stack as one, and the morphs are built from where each
layer sat before the push (#stack-push morph-pre)."""
import types

import numpy as np

from src import nif_convert as nc
from src import nif_convert_trigen as tg


def _body(n=21, step=0.5):
    """a flat skin patch on y = 0 facing +y"""
    xs, zs = np.meshgrid(np.arange(n) * step, np.arange(n) * step)
    B = np.c_[xs.ravel(), np.zeros(xs.size), zs.ravel()]
    return B, np.tile([0.0, 1.0, 0.0], (len(B), 1))


def _layer(B, y):
    return B + np.array([0.0, y, 0.0])


def _job(name, pre, ap, post=None):
    src = types.SimpleNamespace(name=name)
    nc._STACK_AP[id(src)] = (pre, ap, None)
    return {"src": src, "verts": ap if post is None else post}


def _centre(V):
    return int(np.argmin(np.linalg.norm(V[:, [0, 2]] - V[:, [0, 2]].mean(0), axis=1)))


def test_a_single_layer_keeps_its_own_push():
    B, N = _body(); pre = _layer(B, 0.2); ap = _layer(B, 0.7)
    j = _job("only", pre, ap)
    assert nc._stack_push_p1([j], B, N) == 0
    assert np.array_equal(j["verts"], ap)
    nc._STACK_AP.clear()


def test_a_layer_above_rides_the_push_of_the_layer_beneath():
    B, N = _body()
    inner = _job("inner", _layer(B, 0.2), _layer(B, 0.7))            # pushed 0.5 out of the skin
    outer = _job("outer", _layer(B, 1.2), _layer(B, 1.2))            # clear already: no push of its own
    assert nc._stack_push_p1([inner, outer], B, N) > 0
    c = _centre(B)
    assert abs(outer["verts"][c, 1] - 1.7) < 1e-6                    # moved WITH the layer beneath: gap kept
    assert np.allclose(inner["verts"], _layer(B, 0.7))               # the inner layer keeps exactly its own push
    assert abs(outer["stack_extra"][c, 1] - 0.5) < 1e-6              # what the morphs must not see
    nc._STACK_AP.clear()


def test_a_near_coincident_layer_is_not_carried():
    B, N = _body()
    inner = _job("inner", _layer(B, 0.20), _layer(B, 0.70))
    twin = _job("twin", _layer(B, 0.25), _layer(B, 0.25))           # 0.05 above: one surface, not a stack
    nc._stack_push_p1([inner, twin], B, N)
    assert np.allclose(twin["verts"], _layer(B, 0.25))
    nc._STACK_AP.clear()


def test_the_morphs_are_built_from_before_the_push(monkeypatch, tmp_path):
    """`_collect_tri_inputs` subtracts the recorded push, so a layer's slider motion is what it was unpushed."""
    assert nc._STACK_PUSH_MORPH_PRE is True
    V = np.random.default_rng(0).uniform(-5, 5, (40, 3)); off = np.zeros_like(V); off[:, 1] = 0.3
    dst = tmp_path / "x_1.nif"
    shape = types.SimpleNamespace(name="Cloth", verts=V + off, global_to_skin=None)
    nif = types.SimpleNamespace(filepath=str(dst), shapes=[shape])
    monkeypatch.setattr(nc, "shape_body_offset", lambda s: np.zeros(3))
    monkeypatch.setattr(nc, "_extremity_vert_fraction", lambda s, n: None)
    monkeypatch.setitem(nc._STACK_PUSH_OFFSETS, (nc._stack_key(dst), "Cloth"), off)
    armour, _body_in, _ef = tg._collect_tri_inputs(nif, 29298)
    assert np.allclose(armour["Cloth"], V)


def test_the_kill_switches_parse(monkeypatch):
    """CBBE2UBE_NO_STACK_PUSH and CBBE2UBE_NO_STACK_PUSH_MORPH_PRE turn the push / the pre-push morphs off."""
    from src.envflags import flag
    for env in ("CBBE2UBE_NO_STACK_PUSH", "CBBE2UBE_NO_STACK_PUSH_MORPH_PRE"):
        monkeypatch.setenv(env, "1")
        assert flag(env, False) is True
        monkeypatch.delenv(env)
        assert flag(env, False) is False
    assert nc._STACK_PUSH is True and nc._STACK_PUSH_MORPH_PRE is True
