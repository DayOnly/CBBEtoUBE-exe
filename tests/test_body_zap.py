"""#body-zap: the visibility renderer and the rules that need no NIF."""
import numpy as np
import pytest

from src import body_zap as bz

pytestmark = pytest.mark.skipif(bz._dll() is None, reason="native/zraster.dll not built")


def _box(c, h):
    """closed axis box, outward winding."""
    x0, y0, z0 = np.asarray(c) - h; x1, y1, z1 = np.asarray(c) + h
    V = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], float)
    T = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return V, T


def _grid_box(c, h, n=6):
    """a box whose faces are subdivided, so it has interior vertices to test."""
    V, T = [], []
    for ax in range(3):
        for sgn in (-1, 1):
            u, v = [a for a in range(3) if a != ax]
            base = len(V)
            for i in range(n + 1):
                for j in range(n + 1):
                    p = np.array(c, float); p[ax] += sgn * h
                    p[u] += -h + 2 * h * i / n; p[v] += -h + 2 * h * j / n
                    V.append(p)
            for i in range(n):
                for j in range(n):
                    a = base + i * (n + 1) + j; b = a + 1; cc = a + n + 1; d = cc + 1
                    t1, t2 = [a, cc, b], [b, cc, d]
                    nrm = np.cross(V[t1[1]] - V[t1[0]], V[t1[2]] - V[t1[0]])
                    if nrm[ax] * sgn < 0:
                        t1, t2 = [a, b, cc], [b, d, cc]
                    T += [t1, t2]
    return np.array(V), np.array(T)


def test_uncovered_body_is_all_needed():
    B, BT = _grid_box((0, 0, 50), 10)
    nd = bz.needed(B, BT, [], [], bz.fib_dirs(48))
    assert nd.all()


def test_closed_shell_hides_the_body():
    B, BT = _grid_box((0, 0, 50), 10)
    G, GT = _box((0, 0, 50), 11)
    nd = bz.needed(B, BT, [G], [GT], bz.fib_dirs(48))
    assert not nd.any()


def test_open_top_shows_the_skin_under_the_opening():
    B, BT = _grid_box((0, 0, 50), 10)
    G, GT = _box((0, 0, 50), 11)
    keep = np.array([i for i, t in enumerate(GT) if not (G[t][:, 2] > 60).all()])   # cut the lid off
    nd = bz.needed(B, BT, [G], [GT[keep]], bz.fib_dirs(48)).astype(bool)
    assert nd[B[:, 2] > 59.9].any()            # the top face is seen through the opening
    assert not nd[B[:, 2] < 40.1].any()        # the bottom stays hidden


def test_a_lid_seen_from_its_undrawn_side_does_not_cover():
    """#zap-cull: a single-sided lid wound inside-out shows the viewer its back face, which the game does not draw."""
    B, BT = _grid_box((0, 0, 50), 10)
    G, GT = _box((0, 0, 50), 11)
    lid = np.array([(G[t][:, 2] > 60).all() for t in GT])
    GT = np.concatenate([GT[~lid], GT[lid][:, ::-1]])            # the lid faces into the box
    top = B[:, 2] > 59.9
    run = lambda cull: bz.needed(B, BT, [G], [GT], bz.fib_dirs(48), tri_acc=np.zeros(len(BT), np.uint8),
                                 gcull=cull).astype(bool)
    assert not run(None)[top].any()                             # no culling: the lid covers from both sides
    assert not run([False])[top].any()                          # double-sided: still covers
    assert run([True])[top].any()                               # single-sided: the top skin is seen
    assert not run([True])[B[:, 2] < 40.1].any()                # the walls and floor still face out and cover


def test_the_cull_does_not_need_the_triangle_pass():
    """#zap-cull must hold with #zap-tri-vis off too (no tri_acc passed)."""
    B, BT = _grid_box((0, 0, 50), 10)
    G, GT = _box((0, 0, 50), 11)
    lid = np.array([(G[t][:, 2] > 60).all() for t in GT])
    GT = np.concatenate([GT[~lid], GT[lid][:, ::-1]])
    assert bz.needed(B, BT, [G], [GT], bz.fib_dirs(48), gcull=[True]).astype(bool)[B[:, 2] > 59.9].any()


def test_the_nipple_poke_rule_and_the_experiment_switches_are_gone():
    gone = ("ZAP_NIPPLE_POKE", "_poking_through", "ZAP_VIEWS_BIND", "ZAP_VIEWS_PRESET", "ZAP_EDGE_LIFT_RECHECK",
            "ZAP_EDGE_LIFT_KEEP", "ZAP_EDGE_KEEP_R", "_edge_keep")
    assert not [n for n in gone if hasattr(bz, n)] and bz.ZAP_CULL


def _slit_lid(h=11, n=10, c=(0, 0, 50)):
    """a closed shell whose lid has one row of cells cut out across its middle: a slit 2h/n wide."""
    G, GT = _grid_box(c, h, n)
    top = (G[GT][:, :, 2] > c[2] + h - 1e-6).all(1)
    mid = np.abs(G[GT][:, :, 0].mean(1) - c[0]) < h / n
    return G, GT[~(top & mid)]


def test_a_slit_over_a_triangles_middle_is_seen_by_the_triangle_test():
    # #zap-tri-vis: the body's lid is two big triangles whose corners all sit under the shell; only their middle
    # shows through the slit. The vertex test sees nothing; the triangle test sees both.
    B, BT = _box((0, 0, 50), 10)
    G, GT = _slit_lid()
    ts = np.zeros(len(BT), np.uint8)
    nd = bz.needed(B, BT, [G], [GT], bz.fib_dirs(48), tri_acc=ts).astype(bool)
    lid = (B[BT][:, :, 2] > 59.9).all(1); floor = (B[BT][:, :, 2] < 40.1).all(1)
    assert not nd.any()
    assert ts[lid].all() and not ts[floor].any()   # (the slit's ends also show the side faces' tops, down the gap)


def test_the_triangle_test_leaves_the_vertex_answer_alone():
    B, BT = _grid_box((0, 0, 50), 10)
    G, GT = _slit_lid()
    a = bz.needed(B, BT, [G], [GT], bz.fib_dirs(48))
    b = bz.needed(B, BT, [G], [GT], bz.fib_dirs(48), tri_acc=np.zeros(len(BT), np.uint8))
    assert np.array_equal(a, b)


def test_a_seen_triangle_only_under_a_preset_stays_near_bind_skin(monkeypatch):
    monkeypatch.setattr(bz, "ZAP_PRESET_KEEP_R", 1.0)
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 0, 0], [6, 0, 0], [5, 1, 0], [0.5, 0, 0]], float)
    full = np.array([[0, 1, 2], [3, 4, 5], [6, 1, 2]])
    seen = np.array([True, True, True]); at_bind = np.array([True, False, False])
    keep = bz._seen_tri_keep(seen, at_bind, np.zeros(len(V), bool), full, V)
    assert keep.tolist() == [True, False, True]     # the far preset-only one goes, the one touching bind skin stays


def test_every_direction_counts_by_default():
    B, BT = _grid_box((0, 0, 6), 5)
    up = np.array([[0, 0, -1.0]])
    assert bz.needed(B, BT, [], [], up).astype(bool)[B[:, 2] < 1.5].mean() > 0.5   # seen from straight below


def test_pair_paths_reads_both_weights(tmp_path):
    for w in ("_0", "_1"):
        (tmp_path / f"cuirass{w}.nif").write_bytes(b"x")
    got = bz._pair_paths(tmp_path / "cuirass_1.nif")
    assert set(got) == {"_0", "_1"}


def test_first_person_viewmodel_by_name(tmp_path):
    from pathlib import Path
    for n in ("1stpersoncuirass_1.nif", "cuirass_1.nif", "1sttravelgarb_1.nif", "travelgarb_1.nif", "1stclassgown_1.nif"):
        (tmp_path / n).write_bytes(b"x")
    assert bz._first_person_viewmodel(tmp_path / "1stpersoncuirass_1.nif")
    assert bz._first_person_viewmodel(tmp_path / "1sttravelgarb_1.nif")      # its third-person twin is there
    assert not bz._first_person_viewmodel(tmp_path / "1stclassgown_1.nif")      # a name, no twin
    assert not bz._first_person_viewmodel(tmp_path / "cuirass_1.nif")


def test_a_zapped_body_still_reads_as_the_injected_body():
    full = np.array([[0, 1, 2], [2, 1, 3], [3, 4, 2]])
    assert np.isin(bz._tri_keys(full[1:]), bz._tri_keys(full)).all()
    assert not np.isin(bz._tri_keys(np.array([[0, 3, 4]])), bz._tri_keys(full)).all()


def test_lean_is_on_with_the_zap_and_scoped():
    assert bz.ZAP_LEAN is True                      # #zap-lean defaults on
    assert bz.lean() is False                       # only inside a zapped piece's conversion
    with bz.lean_for(True):
        assert bz.lean() is True
        with bz.lean_for(False):
            assert bz.lean() is False
        assert bz.lean() is True
    assert bz.lean() is False


def _nipple_tri():
    """a body of two nipples (x = +-10) on a grid, a nipple-local slider and a breast-shape slider, and a garment
    with one vert in front of each nipple."""
    from src.tri import TriFile, TriShape, TriMorph
    xs, zs = np.meshgrid(np.arange(-16, 17, 0.5), np.arange(84, 106, 0.5))
    V = np.c_[xs.ravel(), np.zeros(xs.size), zs.ravel()]
    apexes = [int(np.argmin(np.linalg.norm(V - [s * 10, 0, 95], axis=1))) for s in (1, -1)]
    d = np.min([np.linalg.norm(V - V[a], axis=1) for a in apexes], axis=0)
    loc = np.flatnonzero(d < 1.0)                          # moves the nipple core only
    shp = np.flatnonzero(d < 8.0)                          # moves the whole breast
    body = TriShape("BaseShape", [TriMorph("NippleLength", loc, np.tile([0, 0.5, 0], (len(loc), 1))),
                                  TriMorph("Breasts", shp, np.tile([0, 1.0, 0], (len(shp), 1)))])
    G = np.array([[10, 2, 95], [-10, 2, 95]], float)
    cloth = TriShape("Top", [TriMorph("NippleLength", [0, 1], [[0, 0.4, 0], [0, 0.4, 0]]),
                             TriMorph("Breasts", [0, 1], [[0, 1.0, 0], [0, 1.0, 0]])])
    return TriFile(1, [body, cloth]), V, apexes, G


def test_nipple_flat_drops_only_nipple_local_rows_over_deleted_skin():
    """#zap-nipple-flat: the cloth over a nipple whose skin is all gone loses the nipple-local slider, keeps the
    breast-shape one; over a nipple whose skin survives nothing changes."""
    tf, V, apexes, G = _nipple_tri()
    names = bz._nipple_local_names(tf.shapes[0], V, apexes)
    assert names == {"nipplelength"}
    # keep only skin far from the +x nipple: its disc is gone, the -x disc survives
    keep_v = np.flatnonzero(np.linalg.norm(V - V[apexes[0]], axis=1) >= bz.ZAP_NIPPLE_R + 1)
    kept = np.c_[keep_v[:-2], keep_v[1:-1], keep_v[2:]]
    n = bz._flatten_nipples(tf, V, kept, apexes, names, {"Top": G})
    assert n == 1
    m = {x.name: x for x in tf.shapes[1].morphs}
    assert list(m["NippleLength"].idx) == [1]              # -x side kept: its skin is still there
    assert list(m["Breasts"].idx) == [0, 1]                # breast shape untouched
    assert bz._flatten_nipples(tf, V, kept, apexes, names, {"Top": G}) == 0   # idempotent


def test_nipple_opaque_flattens_the_cloth_even_where_skin_is_kept():
    """#zap-nipple-opaque: a nipple with no see-through cloth near it (`force`) loses its cloth nipple sliders whatever
    skin the zap keeps there; the breast-shape slider stays."""
    tf, V, apexes, G = _nipple_tri()
    names = bz._nipple_local_names(tf.shapes[0], V, apexes)
    every = np.arange(len(V))
    kept = np.c_[every[:-2], every[1:-1], every[2:]]           # all skin kept: plain #zap-nipple-flat drops nothing
    assert bz._flatten_nipples(tf, V, kept, apexes, names, {"Top": G}) == 0
    n = bz._flatten_nipples(tf, V, kept, apexes, names, {"Top": G}, force=[apexes[1]])
    assert n == 1
    m = {x.name: x for x in tf.shapes[1].morphs}
    assert list(m["NippleLength"].idx) == [0]              # only the forced (-x) side lost its row
    assert list(m["Breasts"].idx) == [0, 1]


def test_nipple_opaque_is_on_and_preset_nipples_are_gone():
    assert bz.ZAP_NIPPLE_OPAQUE and not hasattr(bz, "ZAP_PRESET_NIPPLES")


def test_nipple_flat_is_on_with_the_zap():
    assert bz.ZAP_NIPPLE_FLAT is True


def test_a_slider_far_from_the_breast_is_not_nipple_local():
    """a slider that moves only the forearm passes the core/far RATIO (both ~0) -- it must not be called local."""
    from src.tri import TriMorph, TriShape
    tf, V, apexes, G = _nipple_tri()
    arm = np.flatnonzero(V[:, 0] > 15)                     # far from both nipples
    D = np.zeros((len(arm), 3)); D[:, 2] = 1.0
    core = np.flatnonzero(np.linalg.norm(V - V[apexes[0]], axis=1) < 0.6)[:1]
    body = TriShape("BaseShape", tf.shapes[0].morphs + [TriMorph("ForearmSize", np.r_[arm, core],
                                                                 np.r_[D, [[0, 1e-6, 0]]])])
    assert bz._nipple_local_names(body, V, apexes) == {"nipplelength"}


def test_zap_is_on_by_default_but_needs_presets(tmp_path, monkeypatch):
    """#body-zap is default ON, yet acts only with presets to hold under: a bind-only zap would delete skin a
    preset shows. Presets come from CBBE2UBE_ZAP_PRESETS, else the local list beside the exe."""
    from src import paths
    assert bz.BODY_ZAP is True
    monkeypatch.delenv("CBBE2UBE_ZAP_PRESETS", raising=False)
    monkeypatch.setattr(paths, "tool_dir", lambda: tmp_path)
    assert bz.preset_paths() == [] and bz.active() is False
    x = tmp_path / "a.xml"; x.write_text("<SliderPresets/>", encoding="utf-8")
    (tmp_path / bz.ZAP_PRESETS_NAME).write_text(f"# presets\n{x}\n{tmp_path / 'missing.xml'}\n", encoding="utf-8")
    assert bz.preset_paths() == [str(x)] and bz.active() is True
    monkeypatch.setenv("CBBE2UBE_ZAP_PRESETS", str(tmp_path / "missing.xml"))
    assert bz.preset_paths() == []                         # the variable wins, even when it finds nothing


def test_zap_waits_for_the_weight_partner(tmp_path):
    """#zap-once: while the partner this conversion will write is missing or a previous run's (older) file, wait;
    once it is fresh, or when no partner is expected, zap now."""
    import os, time
    one = tmp_path / "x_1.nif"; zero = tmp_path / "x_0.nif"; one.write_bytes(b"1")
    assert bz.partner_pending(one, True) is True                 # partner not written yet
    assert bz.partner_pending(one, False) is False               # single-weight piece: zap now
    zero.write_bytes(b"0"); old = time.time() - 2 * bz.ZAP_PARTNER_FRESH_S
    os.utime(zero, (old, old))
    assert bz.partner_pending(one, True) is True                 # a previous run's partner, about to be replaced
    os.utime(zero, None)
    assert bz.partner_pending(one, True) is False                # fresh partner: zap both now
    assert bz.partner_pending(tmp_path / "x.nif", True) is False # no weight suffix


def test_the_xml_resolver_takes_a_str_path(tmp_path):
    """#xml-path-str: a str path used to crash the resolver, which callers read as 'XML did not resolve' (fail closed)."""
    from src import nif_convert as nc
    (tmp_path / "meshes" / "a").mkdir(parents=True)
    (tmp_path / "meshes" / "a" / "x.xml").write_text("<system/>")
    nif = tmp_path / "meshes" / "a" / "x_1.nif"
    got = nc._resolve_safe_rel_in_vfs("meshes/a/x.xml", str(nif))
    assert got is not None and got == nc._resolve_safe_rel_in_vfs("meshes/a/x.xml", nif)
