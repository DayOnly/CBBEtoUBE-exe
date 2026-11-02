"""Four exact speed-ups inside generate_armor_tri (2026-10-28) give the SAME arrays as the code they replace.

#tri-live-rows       the slider loop computes only rows that reach a moved body vert
#lockstep-groups     the lockstep's row selections are built once per TRI
#stack-prep-culled   the nipple stack tests each point (edge) only against the triangles (edges) that can hold
                     (cross) it -- thin / tiny triangles and nearly parallel edges are never culled
(#overlap-sync-vectorised is inline in generate_armor_tri; captured real calls pin it, see the changelog.)

The references below are the replaced code, verbatim, so a later edit of the new code is still checked against
what the converter did before these changes.
"""
import numpy as np
import pytest

from src import sliderset_gen as sg
from src.sliderset_gen import NIPPLE_MORPH_STACK_R, _stack_tri_frame  # noqa: F401 (used by the reference)


# ------------------------------------------------------------------ the replaced code, verbatim

def _stack_bary(px, py, x, y, fr):
    d0x, d0y, d1x, d1y, ok, safe = fr
    qx, qy = px - x[:, 0], py - y[:, 0]
    wb = (qx * d1y - qy * d1x) / safe
    wc = (d0x * qy - d0y * qx) / safe
    wa = 1.0 - wb - wc
    return wa, wb, wc, ok & (wa >= -1e-6) & (wb >= -1e-6) & (wc >= -1e-6)


def _old_nipple_stack_pair(frames, out_n, in_n, shapes_tris, gap):
    """One (outer, inner) pair of `_nipple_morph_stack_prep`: (rows, corners,
    bary, normals) in key order, or None. #stack-prep-columns"""
    cols = {k: [] for k in ("r", "c0", "c1", "c2", "hx", "xa", "xb", "b0", "b1", "b2", "f")}

    def put(r, c0, c1, c2, b0, b1, b2, f, hx=0, xa=0, xb=0):
        n = len(r)
        for k, v in (("r", r), ("c0", c0), ("c1", c1), ("c2", c2), ("b0", b0), ("b1", b1), ("b2", b2)):
            cols[k].append(np.broadcast_to(np.asarray(v), (n,)).copy())
        for k, v in (("hx", hx), ("xa", xa), ("xb", xb), ("f", f)):
            cols[k].append(np.broadcast_to(np.asarray(v, dtype=np.int64), (n,)).copy())

    ot_all = np.asarray(shapes_tris[out_n], dtype=np.int64).reshape(-1, 3)
    t_all = np.asarray(shapes_tris[in_n], dtype=np.int64).reshape(-1, 3)
    for fi, (nA, loc) in enumerate(frames):
        ou, ow, oh = loc[out_n]
        rows = np.flatnonzero((np.hypot(ou, ow) < NIPPLE_MORPH_STACK_R)
                              & (oh > -1.0) & (oh < 6.0))
        if not len(rows):
            continue
        otsel = ot_all[np.hypot(ou[ot_all].mean(1), ow[ot_all].mean(1)) < NIPPLE_MORPH_STACK_R + 3.0]
        ofr = _stack_tri_frame(ou[otsel], ow[otsel]) if len(otsel) else None
        iu, iw, ih = loc[in_n]
        tsel = t_all[np.hypot(iu[t_all].mean(1), iw[t_all].mean(1)) < NIPPLE_MORPH_STACK_R + 3.0]
        # an outer vert over an inner triangle
        if len(tsel):
            x, y, z = iu[tsel], iw[tsel], ih[tsel]
            fr = _stack_tri_frame(x, y)
            for r in rows:
                wa, wb, wc, inside = _stack_bary(ou[r], ow[r], x, y, fr)
                if not inside.any():
                    continue
                hz = wa * z[:, 0] + wb * z[:, 1] + wc * z[:, 2]
                below = inside & (oh[r] - hz >= 0.0) & (oh[r] - hz <= gap)
                if not below.any():
                    continue
                # the layer surface nearest under the vert
                k = int(np.flatnonzero(below)[np.argmin(np.abs(oh[r] - hz[below]))])
                put([int(r)], tsel[k, 0], tsel[k, 1], tsel[k, 2], wa[k], wb[k], wc[k], fi)
        # an inner vert under an outer triangle: every corner clears it
        if ofr is not None:
            x, y, z = ou[otsel], ow[otsel], oh[otsel]
            irows = np.flatnonzero((np.hypot(iu, iw) < NIPPLE_MORPH_STACK_R)
                                   & (ih > -1.0) & (ih < 6.0))
            for j in irows:
                wa, wb, wc, inside = _stack_bary(iu[j], iw[j], x, y, ofr)
                if not inside.any():
                    continue
                hz = wa * z[:, 0] + wb * z[:, 1] + wc * z[:, 2]
                above = inside & (hz - ih[j] >= 0.0) & (hz - ih[j] <= gap)
                if above.any():
                    cs = otsel[np.flatnonzero(above)].ravel()     # k ascending, corners in order
                    put(cs, int(j), int(j), int(j), 1.0, 0.0, 0.0, fi)
        # an inner EDGE crossing an outer edge: the lifted inner layer
        # peaks along its edges, and a flat outer face spanning such a
        # ridge dips below it between the points held above. Both outer
        # endpoints clear the inner edge where the two edges cross.
        if len(tsel) and ofr is not None:
            ie = np.unique(np.sort(tsel[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), 1), axis=0)
            oe = np.unique(np.sort(otsel[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), 1), axis=0)
            p0 = np.c_[ou[oe[:, 0]], ow[oe[:, 0]]]
            p1 = np.c_[ou[oe[:, 1]], ow[oe[:, 1]]]
            r_ = p1 - p0
            for c, d in ie:
                q0 = np.array([iu[c], iw[c]])
                s_ = np.array([iu[d], iw[d]]) - q0
                den = r_[:, 0] * s_[1] - r_[:, 1] * s_[0]
                ok = np.abs(den) > 1e-12
                safe = np.where(ok, den, 1.0)
                qp = q0 - p0
                t_ = (qp[:, 0] * s_[1] - qp[:, 1] * s_[0]) / safe        # along outer
                u_ = (qp[:, 0] * r_[:, 1] - qp[:, 1] * r_[:, 0]) / safe  # along inner
                hit = ok & (t_ > 1e-6) & (t_ < 1 - 1e-6) & (u_ > 1e-6) & (u_ < 1 - 1e-6)
                ks = np.flatnonzero(hit)
                if not len(ks):
                    continue
                a_, b_ = oe[ks, 0], oe[ks, 1]
                ho = (1 - t_[ks]) * oh[a_] + t_[ks] * oh[b_]
                hi = (1 - u_[ks]) * ih[c] + u_[ks] * ih[d]
                keep = (ho - hi >= 0.0) & (ho - hi <= gap)
                if not keep.any():
                    continue
                ks, a_, b_ = ks[keep], a_[keep], b_[keep]
                # keyed by the crossing (the outer edge too): one outer vert's
                # edges can cross one inner edge at several points, and each is a
                # separate floor. Both endpoints of each crossing, in that order.
                o = np.stack([a_, b_], 1).ravel()
                ab_a, ab_b = np.repeat(a_, 2), np.repeat(b_, 2)
                uu = np.repeat(u_[ks], 2)
                put(o, int(c), int(d), int(d), 1.0 - uu, uu, 0.0, fi, hx=1, xa=ab_a, xb=ab_b)
    if not cols["r"]:
        return None
    C = {k: np.concatenate(v) for k, v in cols.items()}
    seq = np.arange(len(C["r"]))
    order = np.lexsort((seq, C["xb"], C["xa"], C["hx"], C["c2"], C["c1"], C["c0"], C["r"]))
    key = np.stack([C[k][order] for k in ("r", "c0", "c1", "c2", "hx", "xa", "xb")], 1)
    first = np.ones(len(order), bool)
    first[1:] = (key[1:] != key[:-1]).any(1)
    sel = order[first]
    nA_all = np.asarray([nA for nA, _loc in frames])
    return (C["r"][sel].astype(np.int64),
            np.stack([C["c0"][sel], C["c1"][sel], C["c2"][sel]], 1).astype(np.int64),
            np.stack([C["b0"][sel], C["b1"][sel], C["b2"][sel]], 1).astype(np.float64),
            nA_all[C["f"][sel]])


def _old_layer_lockstep_apply(pending, prep):
    order, info = prep
    for n in order:
        if n not in info or n not in pending:
            continue
        rows, under, corners, bary, vn = info[n]
        cur = pending[n]
        lift = np.zeros(len(cur))
        for u in set(under):
            if u not in pending:
                continue
            m = np.array([x == u for x in under])
            du = (pending[u][corners[m]] * bary[m][:, :, None]).sum(1)
            want = (du * vn[rows[m]]).sum(1)
            have = (cur[rows[m]] * vn[rows[m]]).sum(1)
            lift[rows[m]] = np.maximum(lift[rows[m]], want - have)
        lift = np.clip(lift, 0.0, None)
        if (lift > 0).any():
            pending[n] = cur + vn * lift[:, None]
    return pending


# ------------------------------------------------------------------ #tri-live-rows

@pytest.mark.parametrize("live_frac", [0.0, 0.03, 0.4, 1.0])
def test_live_rows_give_the_full_gather(live_frac):
    rng = np.random.default_rng(1)
    buf = np.zeros((5000, 3))
    live = rng.random(5000) < live_frac
    buf[live] = rng.normal(size=(int(live.sum()), 3))
    idx = rng.integers(0, 5000, size=(3000, 16))
    w = rng.random((3000, 16))
    full = np.einsum("nkj,nk->nj", buf[idx], w)
    got = sg._live_rows(idx, buf.any(axis=1), lambda s: np.einsum("nkj,nk->nj", buf[idx[s]], w[s]), (3000, 3))
    assert np.array_equal(got, full)
    kn = rng.normal(size=(3000, 3))
    full_co = np.einsum("nkj,nj->nk", buf[idx], kn).max(axis=1)
    got_co = sg._live_rows(idx, buf.any(axis=1),
                           lambda s: np.einsum("nkj,nj->nk", buf[idx[s]], kn[s]).max(axis=1), (3000,))
    assert np.array_equal(got_co, full_co)      # == ignores the sign of a zero, which the int16 TRI cannot store


# ------------------------------------------------------------------ #lockstep-groups

def _lock_case(rng):
    names = ["inner", "mid", "outer"]
    pending = {n: rng.normal(size=(400, 3)) for n in names}
    info = {}
    for n in ("mid", "outer"):
        rows = rng.integers(0, 400, 600)                # repeated rows on purpose: the last write must still win
        under = [str(rng.choice(["inner", "mid", "gone"])) for _ in rows]
        info[n] = (rows, under, rng.integers(0, 400, (600, 3)), rng.random((600, 3)), rng.normal(size=(400, 3)))
    return pending, (["mid", "outer"], info)


def test_lockstep_groups_give_the_old_apply_bit_for_bit():
    for seed in range(6):
        pending, prep = _lock_case(np.random.default_rng(seed))
        old = _old_layer_lockstep_apply({k: v.copy() for k, v in pending.items()}, prep)
        new = sg._layer_lockstep_apply({k: v.copy() for k, v in pending.items()}, prep,
                                       sg._layer_lockstep_groups(prep))
        inside = sg._layer_lockstep_apply({k: v.copy() for k, v in pending.items()}, prep)   # groups built inside
        for k in old:
            assert np.array_equal(old[k], new[k]) and np.array_equal(old[k], inside[k])
            assert old[k].tobytes() == new[k].tobytes()


# ------------------------------------------------------------------ #stack-prep-culled

def _grid(rng, n, h0, jitter, sliver=False):
    """A layer over the apex: an n x n grid in (u, w) -- exactly parallel and collinear edges throughout -- at
    height ~h0; `jitter` moves the verts; `sliver` squashes a band into near-degenerate and tiny triangles."""
    g = np.linspace(-NIPPLE_MORPH_STACK_R - 2, NIPPLE_MORPH_STACK_R + 2, n)
    u, w = np.meshgrid(g, g)
    u, w = u.ravel(), w.ravel()
    if jitter:
        u = u + rng.normal(scale=jitter, size=u.shape)
        w = w + rng.normal(scale=jitter, size=w.shape)
    if sliver:
        band = np.abs(w) < 1.5
        w = np.where(band, w * 1e-4, w)                         # near-degenerate rows of triangles
        u = np.where(band & (np.abs(u) < 1.0), u * 1e-3, u)     # tiny ones in the middle
    h = h0 + 0.6 * np.sin(u / 2) * np.cos(w / 3) + rng.normal(scale=0.2, size=u.shape)
    t = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = i * n + j, i * n + j + 1, (i + 1) * n + j, (i + 1) * n + j + 1
            t += [(a, b, d), (a, d, c)]
    return (u, w, h), np.asarray(t, np.int64)


@pytest.mark.parametrize("seed", range(10))
def test_stack_pair_culled_gives_the_old_entries_bit_for_bit(seed):
    rng = np.random.default_rng(seed)
    n1, n2 = int(rng.integers(12, 30)), int(rng.integers(12, 30))
    loc_in, t_in = _grid(rng, n1, 0.5, 0.0 if seed % 2 else 0.15, sliver=seed % 3 == 0)
    loc_out, t_out = _grid(rng, n2, 1.6, 0.0 if seed % 4 == 1 else 0.2, sliver=seed % 3 == 1)
    frames = [(np.array([0, 0, 1.0]), {"in": loc_in, "out": loc_out})]
    if seed % 2:   # a second apex frame, shifted
        frames.append((np.array([0, 0.2, 1.0]) / np.linalg.norm([0, 0.2, 1.0]),
                       {"in": (loc_in[0] - 1.3, loc_in[1] + 0.7, loc_in[2]),
                        "out": (loc_out[0] - 1.3, loc_out[1] + 0.7, loc_out[2])}))
    tris = {"in": t_in, "out": t_out}
    found = 0
    for out_n, in_n in (("out", "in"), ("in", "out")):
        old = _old_nipple_stack_pair(frames, out_n, in_n, tris, sg.NIPPLE_MORPH_STACK_GAP)
        new = sg._nipple_stack_pair(frames, out_n, in_n, tris, sg.NIPPLE_MORPH_STACK_GAP)
        assert (old is None) == (new is None)
        if old is not None:
            found += len(old[0])
            for a, b in zip(old, new):
                assert a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()
    assert found, "the case must produce floors, or it proves nothing"


def test_a_nearly_parallel_pair_far_apart_is_never_culled():
    """The crossing test's rounding can only pass for nearly parallel edges: those pairs are kept even when far
    apart, every other far pair is dropped."""
    iu = np.array([0.0, 1.0]); iw = np.array([0.0, 0.0])
    ou = np.array([50.0, 51.0, 50.0, 50.0]); ow = np.array([1e-9, 1e-9, 0.0, 1.0])
    e, k = sg._stack_cand_edges(iu, iw, np.array([[0, 1]]), ou, ow, np.array([[0, 1], [2, 3]]))
    assert list(zip(e.tolist(), k.tolist())) == [(0, 0)]
