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

"""The 2026-10-17 morph defaults, learned from 949 author-built UBE pieces:
#match-idw-kernel (a hugging vert samples IDW over its 10 nearest body verts),
#match-idw-floor (but never moves outward less than the skin it covers),
#morph-layer-lockstep on every slider at a 1.5u reach, and #breast-field, a
LOCAL table that is used when present and never shipped."""
import importlib
import types

import numpy as np
import pytest

import src.sliderset_gen as sg
from src.osd import OsdFile, OsdMorph


def _fresh(mp, **env):
    """Reload the module under a clean set of the switches this file is about."""
    for k in ("CBBE2UBE_MATCH_IDW_K", "CBBE2UBE_NO_MATCH_IDW_FLOOR",
              "CBBE2UBE_NO_MORPH_LAYER_LOCKSTEP", "CBBE2UBE_MORPH_LAYER_LOCKSTEP_REACH",
              "CBBE2UBE_BREAST_FIELD"):
        mp.delenv(k, raising=False)
    for k, v in env.items():
        mp.setenv(k, v)
    return importlib.reload(sg)


def test_defaults():
    with pytest.MonkeyPatch.context() as mp:
        m = _fresh(mp)
        assert int(m.MATCH_IDW_K) == 10
        assert m.MATCH_IDW_FLOOR
        assert m._MORPH_LAYER_LOCKSTEP
        assert float(m.MORPH_LAYER_LOCKSTEP_REACH) == 1.5
    importlib.reload(sg)


def _plane_run(mp, **env):
    """A dense plane whose row z == 5 moves +1 outward; one vert hugs it at 1u."""
    m = _fresh(mp, **env)
    verts, moved = [], []
    for z in range(0, 11):
        for x in (-2, -1, 0, 1, 2):
            verts.append((float(x), 0.0, float(z)))
            moved.append(z == 5)
    bv, moved = np.asarray(verts, np.float64), np.asarray(moved)
    osd = OsdFile(version=1, morphs=[OsdMorph.from_offsets(
        "BaseShapeBump", [(int(i), 0.0, 1.0, 0.0) for i in np.where(moved)[0]])])
    tri = m.generate_armor_tri({"Plate": np.asarray([(0.0, 1.0, 5.0)])}, bv, osd,
                               body_shape_name="BaseShape", include_body_shapes=False)
    out = np.zeros(3)
    for ts in tri.shapes:
        for mo in ts.morphs:
            for vi, dx, dy, dz in mo.offsets:
                out[:] = (dx, dy, dz)
    return out


def test_the_floor_keeps_a_hugging_vert_level_with_its_skin():
    """The plain 10-neighbour average dilutes a narrow bulge (the in-game failure
    #body-motion-match was built against); the floor restores the skin's own
    outward motion along its normal."""
    with pytest.MonkeyPatch.context() as mp:
        plain = _plane_run(mp, CBBE2UBE_NO_MATCH_IDW_FLOOR="1")
        floored = _plane_run(mp)
    importlib.reload(sg)
    assert plain[1] < 0.9                     # the case is armed: the average lags
    assert np.allclose(floored, [0.0, 1.0, 0.0], atol=1e-6)


def _table(tmp_path, nb, name="t.npz"):
    p = tmp_path / name
    np.savez(p, nb=nb, Juicy_breasts=np.full((nb, 3), 0.25, np.float32))
    return p


def test_the_table_is_local_and_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "_BREAST_FIELD_OFF", False)
    monkeypatch.setenv("CBBE2UBE_BREAST_FIELD", str(tmp_path / "missing.npz"))
    assert sg.breast_field_path() is None and sg._breast_field(55) is None
    p = _table(tmp_path, 55)
    monkeypatch.setenv("CBBE2UBE_BREAST_FIELD", str(p))
    f = sg._breast_field(55)
    assert sorted(f) == ["Juicy_breasts"] and np.allclose(f["Juicy_breasts"], 0.25)
    assert sg._breast_field(56) is None        # a table for another body is never applied
    monkeypatch.setattr(sg, "_BREAST_FIELD_OFF", True)
    assert sg._breast_field(55) is None        # CBBE2UBE_NO_BREAST_FIELD wins


def test_the_table_reaches_armour_only(tmp_path, monkeypatch):
    """The table moves the armour under its slider; the body's own morph is the OSD's."""
    bv = np.asarray([(float(x), 0.0, float(z)) for z in range(4) for x in range(4)])
    osd = OsdFile(version=1, morphs=[OsdMorph.from_offsets(
        "BaseShapeJuicy_breasts", [(i, 0.0, 1.0, 0.0) for i in range(len(bv))])])
    plate = {"Plate": np.asarray([(1.5, 0.5, 1.5)])}

    def run():
        tri = sg.generate_armor_tri(plate, bv, osd, body_shape_name="BaseShape",
                                    include_body_shapes=True)
        got = {}
        for ts in tri.shapes:
            for mo in ts.morphs:
                got[ts.name] = np.asarray([o[1:] for o in mo.offsets])
        return got

    monkeypatch.setattr(sg, "_BREAST_FIELD_OFF", True)
    off = run()
    monkeypatch.setattr(sg, "_BREAST_FIELD_OFF", False)
    monkeypatch.setenv("CBBE2UBE_BREAST_FIELD", str(_table(tmp_path, len(bv))))
    on = run()
    assert np.allclose(on["Plate"] - off["Plate"], [0.25, 0.25, 0.25], atol=1e-5)
    body = [k for k in on if k != "Plate"]
    assert body and all(np.allclose(on[k], off[k]) for k in body)


def test_the_fingerprint_sees_the_table(tmp_path, monkeypatch):
    from src import auto_convert as ac
    args = types.SimpleNamespace()
    monkeypatch.setattr(sg, "_BREAST_FIELD_OFF", False)
    monkeypatch.setenv("CBBE2UBE_BREAST_FIELD", str(tmp_path / "none.npz"))
    absent = ac._nif_config_fingerprint(args)
    p = _table(tmp_path, 55, "a.npz")
    monkeypatch.setenv("CBBE2UBE_BREAST_FIELD", str(p))
    a = ac._nif_config_fingerprint(args)
    np.savez(p, nb=55, Juicy_breasts=np.full((55, 3), 0.5, np.float32))
    b = ac._nif_config_fingerprint(args)
    # the path env differs between the first two; the third differs only in the bytes
    assert len({absent, a, b}) == 3
