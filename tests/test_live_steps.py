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

"""#live-steps -- the window's per-step thumbnails.

What must hold: the worker side only COPIES (the stage hook hands the chain's
arrays on untouched, so the output is the same bytes with the panel on or off),
it records nothing when the folder is not named, it never raises into a
conversion, and the pictures flag the steps a person should look at.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import numpy as np
import pytest

from src import gui, gui_live, gui_settings, live_steps
from tests import _converter_sources
from src.nif_convert_telemetry import make_stage_hook

ROOT = Path(__file__).resolve().parent.parent


def _sphere(r=10.0, c=(0.0, 0.0, 100.0), n=24):
    """A closed UV sphere with OUTWARD winding."""
    th = np.linspace(0, np.pi, n)
    ph = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = np.array([[r * np.sin(t) * np.cos(p), r * np.sin(t) * np.sin(p), r * np.cos(t)]
                  for t in th for p in ph], np.float32) + np.asarray(c, np.float32)
    T = []
    for i in range(n - 1):
        for j in range(n):
            a, b = i * n + j, i * n + (j + 1) % n
            cc, d = a + n, b + n
            T += [(a, cc, b), (b, cc, d)]
    T = np.array(T, np.int32)
    # make the winding outward whatever the construction gave
    nrm = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    if ((V[T].mean(1) - V.mean(0)) * nrm).sum(1).mean() < 0:
        T = T[:, [0, 2, 1]]
    return V, T


@pytest.fixture
def live_on(tmp_path, monkeypatch):
    d = tmp_path / "live"
    monkeypatch.setenv("CBBE2UBE_LIVE_STEPS", str(d))
    live_steps.begin()
    yield d
    live_steps.begin()


def _result(dst, status="converted (body-swap)"):
    return types.SimpleNamespace(src_path=Path("src/a_1.nif"), dst_path=Path(dst),
                                 status=status, reason="")


# ---------------------------------------------------------------- worker side

def test_off_without_the_folder(monkeypatch):
    monkeypatch.delenv("CBBE2UBE_LIVE_STEPS", raising=False)
    V, T = _sphere()
    assert live_steps.shape("Cloth", T, V) is None
    assert live_steps.flush(_result("x_1.nif")) is None


def test_stage_hook_hands_arrays_on_untouched(live_on):
    """Output bytes cannot change: the recorder copies, it never writes back."""
    V, T = _sphere()
    rec = live_steps.shape("Cloth", T, V)
    seen = []
    hook = make_stage_hook(dump=types.SimpleNamespace(checkpoint=lambda l, v: seen.append(v)),
                           live=rec)
    w = V + 1.0
    before = w.copy()
    hook("warp", w)
    w += 5.0                       # a later pass mutating its own array in place
    hook("conform", w)
    assert seen[0] is w and seen[1] is w
    assert np.array_equal(w, before + 5.0)
    slot = live_steps._SHAPES["Cloth"]
    assert np.allclose(slot["warp"], before)          # a copy, not a view
    assert np.allclose(slot["fit"], before + 5.0)


def test_flush_round_trip(live_on, tmp_path):
    V, T = _sphere()
    rec = live_steps.shape("Cloth", T, V)
    rec("warp", V + [0, 1, 0])
    rec("antipoke", V + [0, 2, 0])
    rec("bad shape", np.zeros((3, 3)))                # wrong length: ignored
    out = live_steps.flush(_result(tmp_path / "a_1.nif"), failures=["PASS FAILED x"],
                           zap={"deleted_tris": 7}, zap_pending=False)
    assert out is not None and out.suffix == ".npz" and out.parent == live_on
    assert not list(live_on.glob("*.tmp"))
    r = live_steps.read_record(out)
    assert r["meta"]["failures"] == ["PASS FAILED x"]
    assert r["meta"]["zap"] == {"deleted_tris": 7}
    (name, t, e, w, f), = r["shapes"]
    assert name == "Cloth" and np.array_equal(t, T)
    assert np.allclose(w - e, [0, 1, 0]) and np.allclose(f - e, [0, 2, 0])
    assert live_steps._SHAPES == {}                   # cleared for the next piece


def test_flush_never_raises_and_caps_the_backlog(live_on, monkeypatch):
    live_on.mkdir(parents=True)
    for i in range(live_steps.MAX_PENDING):
        (live_on / f"{i:020d}-1.npz").write_bytes(b"")
    V, T = _sphere()
    live_steps.shape("Cloth", T, V)
    assert live_steps.flush(_result("a_1.nif")) is None   # nobody reading: stop adding
    monkeypatch.setattr(live_steps.np, "savez", lambda *a, **k: 1 / 0)
    for p in live_on.glob("*.npz"):
        p.unlink()
    live_steps.shape("Cloth", T, V)
    assert live_steps.flush(_result("a_1.nif")) is None   # a failing save is swallowed


def test_skipped_piece_without_shapes_writes_nothing(live_on):
    assert live_steps.flush(_result("a_1.nif", status="skipped")) is None
    assert not live_on.exists() or not list(live_on.iterdir())


def test_both_convert_paths_feed_the_recorder():
    src = "\n".join(_converter_sources.texts().values())
    assert len(re.findall(r"make_stage_hook\([^)]*live=live_steps\.shape\(", src, re.S)) == 2
    worker = (ROOT / "src" / "auto_convert.py").read_text(encoding="utf-8")
    body = worker[worker.index("def _nif_convert_worker"):worker.index("def _warmup_worker")]
    assert "live_steps.begin()" in body
    # the record is written after the zap, so the Zap step shows the zapped body
    assert body.index("zap_pair(") < body.index("live_steps.flush(")


# ---------------------------------------------------------------- pictures

def _record(entry, warp, fit, tris, **meta):
    m = {"status": "converted", "dst": "", "failures": [], "zap": None,
         "zap_pending": False, "shapes": ["Cloth"]}
    m.update(meta)
    return {"meta": m, "shapes": [("Cloth", tris, entry, warp, fit)]}


def test_steps_and_flags():
    V, T = _sphere(r=12)
    calm = _record(V, V + [0, 0.5, 0], V + [0, 1.0, 0], T)
    th = live_steps.render_piece(calm, None)
    assert [t["step"] for t in th] == ["Source", "Warp", "Fit", "Weights", "Zap"]
    assert not any(t["bad"] for t in th[:3])
    assert th[0]["mask"].any() and th[1]["rgb"].shape == (150, 112, 3)
    assert "0.5u" in th[1]["note"]

    jump = V.copy()
    jump[0] += [0, 0, live_steps.JUMP_U + 5]
    th = live_steps.render_piece(_record(V, V, jump, T), None)
    assert th[2]["bad"] and "JUMP" in th[2]["note"]

    nan = V.copy()
    nan[3] = np.nan
    th = live_steps.render_piece(_record(V, nan, nan, T), None)
    assert th[1]["bad"] and th[1]["note"] == "verts not finite"

    th = live_steps.render_piece(_record(V, V, V, T, failures=["PASS FAILED body-zap (X: y)"]), None)
    assert th[-1]["bad"] and th[-1]["note"].startswith("PASS FAILED body-zap")


def test_weights_and_zap_from_the_written_file():
    BV, BT = _sphere(r=10)
    CV, CT = _sphere(r=12)
    rec = _record(CV, CV, CV, CT)
    names = ["NPC Spine2 [Spn2]"]
    best = np.zeros(len(CV), np.int32)
    best[:5] = -1                                      # five verts with no weight
    whole = {"body": [(BV, BT)], "cloth": [(CV, CT, best, names)]}
    th = live_steps.render_piece(rec, whole)
    assert th[3]["bad"] and th[3]["note"].startswith("5 unweighted")
    assert not th[4]["bad"]                            # body whole: no hole in view

    # The zap deleted the body's front, and the cloth covers nothing: a hole a
    # camera sees.
    keep = BT[~np.isin(np.arange(len(BT)), _front_ids(BV, BT))]
    th = live_steps.render_piece(_record(BV, BV, BV, BT[:0]),
                                 {"body": [(BV, keep)], "cloth": []})
    assert th[4]["bad"] and "hole in view" in th[4]["note"]
    # The same deletion under cloth is the zap working.
    th = live_steps.render_piece(rec, {"body": [(BV, keep)], "cloth": [(CV, CT, best * 0, names)]})
    assert not th[4]["bad"]


def _front_ids(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    return np.nonzero(n[:, 1] > 0)[0]


def test_zap_note_while_the_partner_weight_is_pending():
    V, T = _sphere()
    th = live_steps.render_piece(_record(V, V, V, T, zap_pending=True),
                                 {"body": [(V, T)], "cloth": []})
    assert th[4]["note"] == "zap runs after the other weight"


def test_compose_is_a_ppm_on_the_theme_background():
    V, T = _sphere()
    t = live_steps.render_piece(_record(V, V, V, T), None)[0]
    ppm = live_steps.compose(t, "#102030")
    head, data = ppm.split(b"\n", 1)
    assert head == b"P6 112 150 255" and len(data) == 112 * 150 * 3
    px = np.frombuffer(data, np.uint8).reshape(150, 112, 3)
    assert tuple(px[0, 0]) == (0x10, 0x20, 0x30)


def test_front_view_puts_the_characters_right_on_screen_left():
    V = np.array([[10.0, 0, 100], [-10.0, 0, 100]], np.float32)
    px, _py, dz, _s = live_steps.project(V, (-20, 20, 80, 120), 100, 100)
    assert px[0] < px[1]
    assert dz[0] == -V[0, 1]                           # nearer = larger y (camera on +Y)


# ---------------------------------------------------------------- the window

def test_setting_is_on_by_default_and_window_only():
    s = next(x for x in gui_settings.SETTINGS if x.key == "live_steps")
    assert s.default is True and s.env is None and s.tab == "Diagnostics"


def test_the_folder_is_never_inherited_and_set_only_by_the_run():
    off = gui.child_env({}, None, "log", base_env={"CBBE2UBE_LIVE_STEPS": "stale"})
    assert "CBBE2UBE_LIVE_STEPS" not in off
    src = (ROOT / "src" / "gui.py").read_text(encoding="utf-8")
    w = src[src.index("    def _worker("):src.index("    def _start(")]
    assert w.index("env = child_env(") < w.index('env[LIVE_ENV] = state["live_dir"]') < w.index("subprocess.Popen(")
    lock = src[src.index("    def _lock_for_run("):src.index("    def _finish(")]
    assert 'live_panel.start(_ld)' in lock and '.get("live_steps", True)' in lock


def test_a_weight_pair_shares_a_row():
    assert gui_live.row_key(r"C:\o\meshes\a\cuirass_0.nif") == gui_live.row_key(r"C:\o\meshes\a\cuirass_1.nif")
    assert gui_live.row_key("boots.nif") != gui_live.row_key("cuirass.nif")


def test_folder_helpers(tmp_path):
    for n in ("00000000000000000002-1.npz", "00000000000000000001-1.npz", ".x.tmp", "keep.txt"):
        (tmp_path / n).write_bytes(b"")
    got = gui_live.newest_records(str(tmp_path), {"00000000000000000002-1.npz"})
    assert [Path(p).name for p in got] == ["00000000000000000001-1.npz"]
    gui_live.clear_folder(str(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


# ---------------------------------------------------------------- the large 3D view

def test_step_timing_holds_a_flagged_step_longer():
    assert gui_live.step_at(0.0, [False] * 5) == (0, 0.0)
    assert gui_live.step_at(gui_live.STEP_S + 0.1, [False] * 5)[0] == 1
    flags = [False, True, False, False, False]
    assert gui_live.step_at(gui_live.STEP_S + gui_live.FLAG_S - 0.1, flags)[0] == 1
    assert gui_live.step_at(gui_live.STEP_S + gui_live.FLAG_S + 0.1, flags)[0] == 2
    loop = 4 * gui_live.STEP_S + gui_live.FLAG_S
    assert gui_live.step_at(loop + 0.1, flags)[0] == 0          # round and round


@pytest.mark.skipif(not __import__("src.live_view3d", fromlist=["x"]).available(),
                    reason="native/liveview.dll not built")
def test_turntable_frames():
    from src import live_view3d
    BV, BT = _sphere(r=10)
    CV, CT = _sphere(r=12)
    rec = _record(CV, CV + [0, 0.5, 0], CV + [0, 1, 0], CT)
    whole = {"body": [(BV, BT)], "cloth": [(CV, CT, np.zeros(len(CV), np.int32), ["NPC Spine2 [Spn2]"])]}
    scenes = live_view3d.scenes_for(rec, whole)
    assert set(scenes) == {"Source", "Warp", "Fit", "Weights", "Zap"}
    bg = (0.1, 0.1, 0.1)
    a = scenes["Fit"].render(0.0, 80, 100, bg)
    assert a.shape == (100, 80, 3) and a.dtype == np.uint8
    edge = a[0, 0].astype(int)
    assert abs(edge - 25).max() <= 25                          # the theme background at the corner
    assert (a != edge).any(axis=2).sum() > 500                 # the piece is drawn
    # no body, no written file: the Source step alone still draws
    only = live_view3d.scenes_for(rec, None)
    assert set(only) == {"Source", "Warp", "Fit"}
    assert np.array_equal(only["Source"].render(0.3, 60, 80, bg),
                          only["Source"].render(0.3, 60, 80, bg))   # same turn, same frame


@pytest.mark.skipif(not __import__("src.live_view3d", fromlist=["x"]).available(),
                    reason="native/liveview.dll not built")
def test_the_rasteriser_keeps_the_nearest_surface():
    from src import live_view3d
    dll = live_view3d._dll()
    X = np.array([0, 20, 0, 0, 20, 0], np.float32)
    Y = np.array([0, 0, 20, 0, 0, 20], np.float32)
    Z = np.array([5, 5, 5, 2, 2, 2], np.float32)               # the second triangle is nearer
    A = np.array([[1], [1], [1], [7], [7], [7]], np.float32)
    T = np.array([[0, 1, 2], [3, 4, 5]], np.int32)
    zb = np.full(400, np.inf, np.float32)
    ab = np.zeros(400, np.float32)
    dll.lv_raster(X, Y, Z, A.ravel().copy(), 1, 6, T, 2, 20, 20, zb, ab)
    assert zb[2 * 20 + 2] == pytest.approx(2.0) and ab[2 * 20 + 2] == pytest.approx(7.0)
    assert np.isinf(zb[19 * 20 + 19])                          # outside both triangles
