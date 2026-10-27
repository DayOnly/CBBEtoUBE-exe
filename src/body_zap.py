"""#body-zap (ON when presets are found; CBBE2UBE_NO_BODY_ZAP=1 keeps the whole body): delete the injected UBE body's triangles that the armour hides.

Why: a slot-32 piece ships the full 29298-vert UBE `BaseShape`, while hand-built UBE armour ships the body with the
hidden skin removed. Skin that is not there cannot clip under a preset; on the 118-piece sample our fit with the
authors' own deletion matches or beats theirs (Natural bust/torso/crotch/thigh 127/89/751/31 vs 137/211/745/87),
and without it we expose 360/371/1065/105. This is our own rule, from the armour itself -- no source body, no
BodySlide code.

The rule, per weight pair (`_0` and `_1` get the SAME triangles: the game blends the two meshes):
  * a body vertex is NEEDED when some view sees it with no cover garment between it and the body's far side,
    in the bind pose or under a preset, at either weight (`native/zraster.c`: 48 orthographic views, 0.6u pixels,
    only views within 70 deg of the skin normal);
  * EVERY direction counts, from below included (user decision 10-18: assume all angles are visible; a below-
    horizon cut and a floor rule were measured and removed -- both deleted groin skin under a dress that a camera
    low enough can see);
  * cover = the piece's own shapes minus hidden shapes, physics colliders, and any vertex with > 30% of its weight
    on bones that are neither the body's nor `NPC ` skeleton bones (SMP cloth swings away from the skin);
  * leg skin below z 45 is always needed (legs leave robe hems when walking: motion holes 1.03% -> 0.54% of
    the body on the real walk/run);
  * skin seen only under a preset is kept only within 1u of skin seen at bind (#zap-preset-keep);
  * a deleted vertex within 2u of skin visible at bind is kept (edge band);
  * a triangle goes when 2 of its 3 corners are deletable;
  * a FIRST-PERSON mesh (by name, or one the plugins use only as a first-person model, #zap-fp-armature) keeps
    all arm skin (arm weight >= 0.2) and loses everything else: the first-person camera only ever sees the arms.
With the zap on, #zap-lean also skips the fit passes that only kept cloth off the deleted skin (see ZAP_LEAN), and
#zap-leg-push then lifts the cloth over the leg / hip skin the zap keeps where it shows through at bind.
Triangles only: every vertex stays, so morphs (the .tri), weights and every "body == reference" check are untouched.
Runs after both weights are written (the hook is `auto_convert._nif_convert_worker`); it always starts from the
full injected triangle list, so a re-run gives the same answer.
"""
from __future__ import annotations

import ctypes
import functools
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .envflags import flag as _flag, knob as _knob

BODY_ZAP = not _flag("CBBE2UBE_NO_BODY_ZAP", False)
# the presets the zap holds under, one XML path per line, beside the exe (local to this machine, like the
# warp / breast tables): CBBE2UBE_ZAP_PRESETS (';'-separated) wins when set
ZAP_PRESETS_NAME = "CBBEtoUBE_zap_presets.txt"
ZAP_VIEWS = _knob("CBBE2UBE_ZAP_VIEWS", 48, int)
ZAP_PIXEL = _knob("CBBE2UBE_ZAP_PIXEL", 0.6)
ZAP_VIEW_ANGLE = _knob("CBBE2UBE_ZAP_VIEW_ANGLE", 70.0)
ZAP_BAND = _knob("CBBE2UBE_ZAP_BAND", 2.0)
ZAP_LEG_Z = _knob("CBBE2UBE_ZAP_LEG_Z", 45.0)
ZAP_FREE_SHARE = 0.3
# #zap-preset-keep: skin hidden at bind but seen under a preset is kept only within this many units of skin seen AT
# BIND (an opening or edge the preset can really expose); deeper inside the covered area it goes, as hand-built
# zaps do. Fit sample, zap alone: bust Natural 182 -> 129, TooBig 1130 -> 691 (authors 137 / 862); holes 1.56 ->
# 1.62% of the body; layer crossings unchanged. Unlimited = keep all preset-visible skin (the old rule).
# 1u since 2026-10-18 (was 3u): on 70 zapped pieces (fit sample + layered) Natural / Too Big bust exposure
# 580 / 1420 -> 480 / 1291 (authors 367 / 2280), holes 1.91 -> 1.95% of the body.
ZAP_PRESET_KEEP_R = _knob("CBBE2UBE_ZAP_PRESET_KEEP_R", 1.0)
# #zap-lean (ON with the zap; CBBE2UBE_NO_ZAP_LEAN=1 keeps every pass): on a piece that gets the injected (and so
# zapped) body, skip the passes whose only job was skin the zap deletes -- post-warp inflate, phase-2 conform and the
# bust / back / drape morph residuals (the layer ride runs again since 10-19, #zap-keep-ride: layer-over-layer work). Fit sample, 30 zapped pieces: median distance to the
# authors 0.369 -> 0.320u, bust layer flips 672/406/1235 -> 474/279/1097, Natural crotch exposure 305 -> 216; the
# other 88 pieces keep every pass and were byte-for-byte unaffected.
ZAP_LEAN = not _flag("CBBE2UBE_NO_ZAP_LEAN", False)
# #zap-nipple-flat (ON with the zap; CBBE2UBE_NO_ZAP_NIPPLE_FLAT=1 keeps them): where the zap deletes ALL the skin
# within ZAP_NIPPLE_R of a nipple apex, the garment over that nipple drops the NIPPLE-LOCAL sliders (nipple length,
# show-up, areola erection / size, puffy, perkiness ... -- sliders that move the body only at the nipple core,
# `sliderset_gen._nipple_local`): no skin is left there to poke, so the cloth stays flat over the breast. All 949
# author pairs: on the 349 garment shapes that cover a nipple the authors carry exactly two nipple sliders
# (NippleDispertionSides, NippleHeight -- whole-nipple position, NOT nipple-local; kept here too) and none of the
# other 17 (0 of 349); ours carried them at 0.1-0.5u each, stacking into a point on the cloth under a preset.
# The zap's own visibility ignores the nipple-local sliders, so dropping them cannot change which skin goes.
ZAP_NIPPLE_FLAT = not _flag("CBBE2UBE_NO_ZAP_NIPPLE_FLAT", False)
# #zap-nipple-envelope (DEFAULT ON since 2026-10-19; CBBE2UBE_NO_ZAP_NIPPLE_ENVELOPE=1 off): the other side of
# #zap-nipple-flat. 200 zapped nipple-covering author pairs: pieces poking only under a preset 30 -> 18 (w1), 4 -> 0 (w0);
# a reported hide cuirass 76/160/109 skin verts through (N/TB/ShyGirl) -> 0.
# Where the zap KEEPS nipple skin under the cloth, every cloth vert whose triangles cover kept nipple skin rises
# (along the apex normal) under each NIPPLE-LOCAL slider at least as far as the fastest-rising kept skin it covers
# -- `sliderset_gen`'s #nipple-morph-envelope rule, checked at BOTH weights. The shared .tri is generated from the
# `_0` geometry only (`_tri_is_owning_variant`), so the generator's envelope covers the `_0` nipple and misses the
# `_1` one by up to 2u (in game: a nipple through a leather cuirass at weight 100); fine garments only get the
# low-pass bridge, which rises LESS than the tip. Here both weights are on disk. Only adds outward motion.
ZAP_NIPPLE_ENVELOPE = not _flag("CBBE2UBE_NO_ZAP_NIPPLE_ENVELOPE", False)
# #zap-nipple-hide (DEFAULT ON since 2026-10-20; CBBE2UBE_NO_ZAP_NIPPLE_HIDE=1 off): user rule 10-19 -- armour that covers the
# nipple without a see-through gap carries no nipple bulge. Where the zap deleted ALL the skin in the ring 2.5-5u
# around an apex, the nipple core goes too (it would otherwise come through the flattened cloth), and every cover
# shape's own bump over it is lowered onto the curvature of its ring (`_flatten_cloth_bump`). A reported top kept a
# +0.1-0.26u point (inherited from the source sculpt) on every preset: #nipple-bridge, which removes it elsewhere, sits
# inside the anti-poke block that #zap-warp-only skips.
# 63 fully covered zapped author pairs: cloth bump over the nipple w1 bind/N/FP/TB/ShyGirl 0.66/0.60/0.48/0.84/1.23 ->
# 0.42/0.24/0.23/0.47/0.63u; bust skin better on 27 piece-weights, worse on none; crossings and fit unchanged.
ZAP_NIPPLE_HIDE = not _flag("CBBE2UBE_NO_ZAP_NIPPLE_HIDE", False)
ZAP_HIDE_CORE_R = 2.5
# The lowering never brings the cloth within ZAP_HIDE_KEEP_GAP of skin the zap keeps (nor closer than it already was).
ZAP_HIDE_KEEP_GAP = 0.1
# #zap-nipple-poke (CBBE2UBE_NO_ZAP_NIPPLE_POKE=1 off): within ZAP_NIPPLE_R of an apex, skin seen only because it pokes
# THROUGH cover cloth -- cloth within ZAP_POKE_DEPTH under it along its own normal (or level with it) -- counts as
# covered. In game 10-22: pointed nipples on coarse plates at rest, one plate vertex over the apex 1-2u above its ring.
# The source and the authors' UBE builds run the plate straight over the breast, 0.5-2.6u UNDER its tip, the poking
# nipple deleted; ours kept that skin (seen from oblique views) and #min-push lifted the plate over it (~+1u, the warp
# another 0.2-0.6u). With the skin gone the view sees the cloth that lay just under it. Pairs with #min-push-zap-nipple.
# REJECTED 10-22: lowering a coarse plate's point onto its ring after the fact (ring fits 0.6-1.25 edges wide, or the
# body's breast + the ring's stand-off): vertex for vertex against 240 author tips it traded points for dents at every
# width (points > 0.3u 141 -> 89..124, dents 1 -> 17..55).
ZAP_NIPPLE_POKE = not _flag("CBBE2UBE_NO_ZAP_NIPPLE_POKE", False)
ZAP_POKE_DEPTH = 4.0      # Too Big pushes the tip 3.1-3.7u through a plate that clears it by 0.5u at rest
ZAP_POKE_LEVEL = 0.3      # cloth this far ABOVE the skin still counts: level with it, seen edge-on
# #zap-nipple-hide-covered (CBBE2UBE_NO_ZAP_NIPPLE_HIDE_COVERED=1 = the whole ring must go): a nipple core no pose shows
# is hidden too when skin in its ring is kept. A cup edge 4-5u from the apex keeps the skin above it (visible, right)
# plus its ZAP_BAND, and that alone refused the lowering: the witch top kept a +0.4u bump over a nipple no pose sees.
ZAP_NIPPLE_HIDE_COVERED = not _flag("CBBE2UBE_NO_ZAP_NIPPLE_HIDE_COVERED", False)
# #zap-alpha (DEFAULT ON since 2026-10-20; CBBE2UBE_NO_ZAP_ALPHA=1 off): an alpha-tested or alpha-blended cover shape hides the
# skin only where its texture is opaque (in game: a strap sleeve over the upper arm, transparent between the straps,
# and the arm skin behind it deleted -- the arms missing; 76% of that skin sat behind alpha 0). Archive-only textures:
# #zap-alpha-archives.
# 27 zapped author pairs with deleted skin behind see-through texels: 4542 -> 527 verts. The skin it keeps is drawn where
# the authors draw it too (skin over straps that sit inside the body shows, Too Big bust 583 -> 1073 vs the authors' 1101).
ZAP_ALPHA = not _flag("CBBE2UBE_NO_ZAP_ALPHA", False)
# #zap-alpha-archives (DEFAULT ON since 2026-10-21; CBBE2UBE_NO_ZAP_ALPHA_ARCHIVES=1 = loose textures only): a texture
# that is not loose anywhere is read from the load order's archives, the copy the game draws (loose first, then the
# archive whose plugin loads last: the converter's own archive index). Audit 2026-10-20 F06: an archive-only diffuse
# counted as fully opaque, so skin under see-through texels was deleted (two wound-overlay outfits lost ~2000 verts of
# arms / torso / legs each; 53 zapped pieces at risk, 6 visible).
ZAP_ALPHA_ARCHIVES = not _flag("CBBE2UBE_NO_ZAP_ALPHA_ARCHIVES", False)
# #zap-leg-push (DEFAULT ON since 2026-10-21; CBBE2UBE_NO_ZAP_LEG_PUSH=1 off): after the zap, lift the cloth over the
# leg and hip skin the zap KEEPS (z < ZAP_LEG_PUSH_Z) where that skin shows through it at bind -- `minimum_push`'s
# measured, smallest lift (the bust's #push-tri), run on all cover shapes at once so each poke lifts the outermost
# layer beneath the skin and the layer order holds; seam-coincident verts take the largest lift; simulated verts
# never move. The leg keep rule (#zap-leg) keeps lower-leg skin for robe hems in motion, and #zap-lean skips the
# anti-poke that used to lift the cloth off it: robes and skirts sat 0.3u (p50) inside the UBE leg. Audit 2026-10-20
# F25: legs / hips strict poke at bind 3797 / 1399 u^2 vs the authors' 370 / 320 on 284 pairs.
ZAP_LEG_PUSH = not _flag("CBBE2UBE_NO_ZAP_LEG_PUSH", False)
# 80 since 2026-10-21 (was 70): the kept skin between the hips and the belly (the crotch mound, the hip tops) had no
# lift at all and the 70 cut moved poke just above it (two pieces). 357-piece replay: torso strict poke at bind
# 2084 -> 1780 u^2 (25 better / 0 worse; the authors 1685), Too Big 6867 -> 6537 (17 / 1); legs, hips, holes flat;
# walk/run skin 1 better / 0 worse, layer flips 2 worse (+10, +12). 90 went further (bind 1476, 37 / 0) but Too Big
# 3 worse, one piece 0 -> 21 u^2.
ZAP_LEG_PUSH_Z = _knob("CBBE2UBE_ZAP_LEG_PUSH_Z", 80.0)
# Only skin with its cloth at most this far under it is lifted for (and the lift is capped just above it): deeper
# hits are the far side of a hem or an inner layer through the leg, and lifting toward them raised tents that showed
# MORE skin (2026-10-21: one dress's legs 9 -> 47 u^2 at bind, p50 depth 4.6u).
ZAP_LEG_PUSH_MAX_DEPTH = _knob("CBBE2UBE_ZAP_LEG_PUSH_MAX_DEPTH", 1.0)
# #zap-leg-push-room (OPT-IN, CBBE2UBE_ZAP_LEG_PUSH_ROOM=<u>, e.g. 0.2): a lift stops that far short of simulated
# cloth or a collider above the lifted vert. The lift never moves simulated cloth, so a plate lifted toward a physics
# skirt / dress (or off a collider) closed the gap and the two swapped order in walk/run (layer flips worse on 4
# pieces, better on none). NOT ON: the physics cloth and the body-shaped colliders dip to the skin exactly where the
# hips need the lift -- 0.2u (21 pieces change): flips 3 better / 0 worse but hip poke at bind 400 -> 817 u^2 (12
# worse); sim cloth only (CBBE2UBE_ZAP_LEG_PUSH_ROOM_COLLIDERS=0): 9 of 21 worse (2026-10-21).
ZAP_LEG_PUSH_ROOM = _knob("CBBE2UBE_ZAP_LEG_PUSH_ROOM", 0.0)
# #zap-leg-push-sim (OPT-IN, CBBE2UBE_ZAP_LEG_PUSH_SIM=1): the simulated verts of the lifted cloth move with it.
# #zap-leg-push-stack (OPT-IN, CBBE2UBE_ZAP_LEG_PUSH_STACK=1): a layer resting above a lifted one rides its lift
# (#stack-push's rule), physics dresses the XML lists as colliders included (no cover: they swing off the skin).
# Both tried 2026-10-21 against the walk/run layer flips the lift adds under physics dresses (two steel cuirasses):
# the flips stayed -- the offline walk/run poses physics cloth on its own still bones while the lifted plate follows
# the leg, so any plate lift reads as more crossings and only the game can judge it. SIM: hips at bind 400 -> 224 u^2
# but walk/run skin worse on 2 (one dress 1007 -> 1097); STACK: no measurable effect. Counting the dresses as COVER
# instead: hips 400 -> 555 (9 worse) -- wrong, they swing off the skin.
ZAP_LEG_PUSH_SIM = _flag("CBBE2UBE_ZAP_LEG_PUSH_SIM", False)
ZAP_LEG_PUSH_STACK = _flag("CBBE2UBE_ZAP_LEG_PUSH_STACK", False)
ZAP_LEG_PUSH_ROOM_COLLIDERS = bool(_knob("CBBE2UBE_ZAP_LEG_PUSH_ROOM_COLLIDERS", 1, int))
# #zap-fp-armature (DEFAULT ON since 2026-10-21; CBBE2UBE_NO_ZAP_FP_ARMATURE=1 = the name rule alone): a mesh the
# plugins' ARMAs use only as a FIRST-PERSON model (MOD4/MOD5, never MOD2/MOD3) is zapped as a viewmodel (arms kept,
# the rest of the injected body gone) whatever its file is called. The name rule alone (`1stperson` / `firstperson`,
# or a `1st` prefix with a third-person twin) missed 39 dedicated first-person NIFs (`*_1st`, `1stp*`, `*fp*`,
# `*_1person`, 21 pieces) that kept the whole body: torso and breasts in first-person view (audit 2026-10-20 F05).
ZAP_FP_ARMATURE = not _flag("CBBE2UBE_NO_ZAP_FP_ARMATURE", False)
ZAP_NIPPLE_R = 5.0     # = sliderset_gen.NIPPLE_MORPH_BRIDGE_R_OUT: past this a nipple-local slider moves no skin
_LEAN = [False]


def lean() -> bool:
    """True while a zapped piece is being converted with #zap-lean on."""
    return _LEAN[0]


class lean_for:
    """`with lean_for(active):` -- #zap-lean for the conversion inside."""
    def __init__(self, active: bool):
        self.active = bool(active)

    def __enter__(self):
        self.prev = _LEAN[0]; _LEAN[0] = self.active
        return self

    def __exit__(self, *exc):
        _LEAN[0] = self.prev
        return False


ZAP_ARM_MIN = 0.2
SELFTOL = 1.0
ARM_BONE_KEYS = ("Clavicle", "UpperArm", "Upperarm", "Forearm", "Hand")
BODY_NAME = "BaseShape"


# ---------------------------------------------------------------- the native renderer
@functools.lru_cache(maxsize=1)
def _dll():
    """zraster.dll beside the exe (frozen) or in native/ (source). None when absent: then no zap."""
    here = Path(__file__).resolve().parent
    cands = [Path(getattr(sys, "_MEIPASS", "")) / "zraster.dll", here.parent / "native" / "zraster.dll"]
    for c in cands:
        if c.is_file():
            d = ctypes.CDLL(str(c))
            f = np.ctypeslib.ndpointer(np.float32, flags="C_CONTIGUOUS")
            i = np.ctypeslib.ndpointer(np.int32, flags="C_CONTIGUOUS")
            u = np.ctypeslib.ndpointer(np.uint8, flags="C_CONTIGUOUS")
            d.zr_needed.argtypes = [f, ctypes.c_int, i, ctypes.c_int, f, ctypes.c_int, i, ctypes.c_int, f, f,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_float, u, ctypes.c_int, ctypes.c_void_p, f,
                                    ctypes.c_float]
            d.zr_needed.restype = ctypes.c_int
            return d
    return None


def fib_dirs(n: int) -> np.ndarray:
    """n view directions spread evenly over the sphere (z up)."""
    k = np.arange(n) + 0.5
    ph = np.arccos(1 - 2 * k / n); th = np.pi * (1 + 5 ** 0.5) * k
    return np.stack([np.cos(th) * np.sin(ph), np.sin(th) * np.sin(ph), np.cos(ph)], 1)


def view_dirs() -> np.ndarray:
    return fib_dirs(int(ZAP_VIEWS))


def _basis(d):
    a = np.array([0, 0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0, 0])
    u = np.cross(a, d); u /= np.linalg.norm(u)
    return u, np.cross(d, u)


def needed(B, BT, Gvs, Gts, dirs, acc=None):
    """OR into `acc` (uint8 per body vertex) the vertices some view sees uncovered in this pose."""
    dll = _dll()
    B = np.ascontiguousarray(B, np.float32); BT = np.ascontiguousarray(BT, np.int32)
    if Gvs:
        offs = np.cumsum([0] + [len(V) for V in Gvs])
        GV = np.ascontiguousarray(np.concatenate(Gvs), np.float32)
        GT = np.ascontiguousarray(np.concatenate([np.asarray(T) + offs[k] for k, T in enumerate(Gts)]), np.int32)
    else:
        GV = np.zeros((1, 3), np.float32); GT = np.zeros((0, 3), np.int32)
    px = float(ZAP_PIXEL)
    allp = np.concatenate([B, GV]); c = allp.mean(0); R = float(np.linalg.norm(allp - c, axis=1).max()) + 2
    nx = int(2 * R / px) + 2
    mats = np.zeros((len(dirs), 9), np.float32); off = np.zeros((len(dirs), 3), np.float32)
    for k, d in enumerate(dirs):
        u, v = _basis(d); M = np.stack([u, v, -d], 1) / px
        mats[k] = M.reshape(-1); o = R / px - c @ M; o[2] = 0; off[k] = o
    fn = np.cross(B[BT[:, 1]] - B[BT[:, 0]], B[BT[:, 2]] - B[BT[:, 0]])
    front_sign = 1 if ((B[BT].mean(1) - B.mean(0)) * fn).sum(1).mean() >= 0 else -1
    vn = np.zeros_like(B)
    for k in range(3):
        np.add.at(vn, BT[:, k], fn)
    vn = np.ascontiguousarray(vn / (np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12) * front_sign, np.float32)
    if acc is None:
        acc = np.zeros(len(B), np.uint8)
    from .work_counts import WORK
    WORK["zap_views"] += len(dirs)         # #unit-timing
    rc = dll.zr_needed(B, len(B), BT, len(BT), GV, len(GV), GT, len(GT), mats, off, len(dirs), nx,
                       np.float32(SELFTOL / px), acc, int(front_sign), vn.ctypes.data,
                       np.ascontiguousarray(dirs, np.float32), np.float32(np.cos(np.radians(float(ZAP_VIEW_ANGLE)))))
    if rc != 0:
        raise MemoryError("zraster: out of memory")
    return acc


# ---------------------------------------------------------------- presets and morphs
@functools.lru_cache(maxsize=None)
def _preset(path: str, big: bool) -> "tuple[tuple[str, float], ...]":
    side = "big" if big else "small"; out = {}
    for pr in ET.parse(path).getroot().iter("Preset"):
        for s in pr.iter("SetSlider"):
            if (s.get("size") or "").lower() == side and s.get("name"):
                out[s.get("name").lower()] = float(s.get("value", "0")) / 100.0
        break
    return tuple(sorted((k, v) for k, v in out.items() if abs(v) > 1e-9))


def preset_paths() -> "list[str]":
    """The BodySlide presets the zap must hold under: CBBE2UBE_ZAP_PRESETS (';'-separated XML paths), else the
    ZAP_PRESETS_NAME list in the tool folder (one path per line, '#' comments). Missing files are skipped."""
    raw = os.environ.get("CBBE2UBE_ZAP_PRESETS", "").strip()
    if raw:
        cand = raw.split(";")
    else:
        from .paths import tool_dir
        f = tool_dir() / ZAP_PRESETS_NAME
        try:
            cand = [l.split("#", 1)[0] for l in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []
        except OSError:
            cand = []
    return [p.strip() for p in cand if p.strip() and os.path.isfile(p.strip())]


def active() -> bool:
    """The zap runs only with presets to hold under: a bind-only zap would delete skin a preset shows. Without
    them the body stays whole and #zap-lean stays off (every fit pass runs, as before the zap)."""
    return bool(BODY_ZAP and preset_paths() and _dll() is not None)


def _morphed(V, tri_shape, sliders):
    if tri_shape is None or not sliders:
        return V
    vals = dict(sliders); d = np.zeros_like(V)
    for m in tri_shape.morphs:
        v = vals.get(m.name.lower())
        if v is None:
            continue
        i = np.asarray(m.idx, int); ok = (i >= 0) & (i < len(V))
        np.add.at(d, i[ok], np.asarray(m.delta, float)[ok] * v)
    return V + d


# ---------------------------------------------------------------- the piece
def _free_verts(shape, body_bones) -> np.ndarray:
    n = len(shape.verts); free = np.zeros(n); tot = np.zeros(n)
    for b, lst in (shape.bone_weights or {}).items():
        fb = (b not in body_bones) and not b.startswith("NPC ")
        for vi, wt in lst:
            tot[vi] += wt
            if fb:
                free[vi] += wt
    return free / np.maximum(tot, 1e-9) > ZAP_FREE_SHARE


def _arm_weight(shape, n) -> np.ndarray:
    w = np.zeros(n)
    for b, lst in (shape.bone_weights or {}).items():
        if any(k in b for k in ARM_BONE_KEYS):
            for vi, wt in lst:
                w[vi] += wt
    return w


@functools.lru_cache(maxsize=4)
def full_body_tris(weight_suffix: str):
    """(tris, partition_tris) of the injected BaseShape BEFORE any zap: the UBE reference body's triangles plus the
    pubic-hole fill `_inject_ube_baseshape` adds -- recomputed here the same way, so a re-run starts from the
    whole body."""
    from . import nif_convert as nc
    ref = nc._find_ube_femalebody(weight=weight_suffix)
    s = next(x for x in nc._pynifly().NifFile(str(ref)).shapes if x.name == BODY_NAME)
    V = np.asarray(s.verts, np.float64); T = np.asarray(s.tris, np.int64)
    tris = T
    if s.normals is not None:
        fixed, _ = nc._repair_degenerate_normals(V, T, np.asarray(s.normals, np.float64))
        sealed, n_loops = nc._close_pubic_holes(V, T, fixed)
        if n_loops > 0:
            tris = np.asarray(sealed, np.int64)
    return tris, V


def _first_person_viewmodel(path: Path) -> bool:
    """A first-person viewmodel NIF, by name: `1stperson` / `firstperson` in the stem, or a `1st` prefix with a
    third-person twin (the same stem without it) in the same folder. `nif_convert._is_first_person_mesh` cannot be
    used here: it relies on a viewmodel carrying no injected body, and phase 2 injects one (so its arms render)."""
    def base(st):
        st = st.lower()
        return st[:-2] if st[-2:] in ("_0", "_1") else st
    st = base(path.stem)
    if "1stperson" in st or "firstperson" in st:
        return True
    if st.startswith("1st"):
        rest = st[3:].lstrip("_- ")
        try:
            return any(base(q.stem) == rest for q in path.parent.glob("*.nif"))
        except OSError:
            return False
    return False


def _tri_keys(T) -> np.ndarray:
    T = np.sort(np.asarray(T, np.int64), axis=1)
    return (T[:, 0] << 42) | (T[:, 1] << 21) | T[:, 2]


def _pair_paths(dst_path: Path) -> "dict[str, Path]":
    st = dst_path.stem
    for suf in ("_0", "_1"):
        if st.endswith(suf):
            base = st[: -len(suf)]
            return {w: dst_path.with_name(base + w + dst_path.suffix) for w in ("_1", "_0")
                    if dst_path.with_name(base + w + dst_path.suffix).is_file()}
    return {"": dst_path} if dst_path.is_file() else {}


def _nipple_apexes(body, full) -> "list[int]":
    """Body vertex index of each nipple apex (the morph generator's own rule), [] when the body has no breast-tip
    weights."""
    from types import SimpleNamespace
    from .nif_convert_trigen import _nipple_morph_bridge_inputs
    shim = SimpleNamespace(verts=body.verts, tris=full, bone_weights=getattr(body, "bone_weights", None))
    return [int(ap) for ap, _n, _a in (_nipple_morph_bridge_inputs(shim) or [])]


def _nipple_local_names(body_tri, V, apexes) -> "set[str]":
    """Lower-cased names of the body morphs that are NIPPLE-LOCAL (see `sliderset_gen._nipple_local`)."""
    from . import sliderset_gen as sg
    if body_tri is None or not apexes:
        return set()
    dm = np.min([np.linalg.norm(V - V[a], axis=1) for a in apexes], axis=0)
    core = np.flatnonzero(dm < sg.NIPPLE_MORPH_CORE_R)
    far = np.flatnonzero((dm >= sg.NIPPLE_MORPH_FAR_R[0]) & (dm < sg.NIPPLE_MORPH_FAR_R[1]))
    out = set()
    for m in body_tri.morphs:
        if not len(m.idx):
            continue
        D = np.zeros_like(V); i = np.asarray(m.idx, int); ok = (i >= 0) & (i < len(V))
        D[i[ok]] = np.asarray(m.delta, float)[ok]
        # the ratio alone also passes a slider that barely touches the breast (a forearm slider: core ~1e-6u, far
        # band 0): it must move the body MOST at a nipple, and by a real amount there
        mag = np.linalg.norm(D, axis=1)
        if mag.max() < 0.01 or dm[int(np.argmax(mag))] >= ZAP_NIPPLE_R:
            continue
        if sg._nipple_local(D, core, far):
            out.add(m.name.lower())
    return out


def _flatten_nipples(tf, V, kept, apexes, local_names, garment_verts) -> int:
    """#zap-nipple-flat: drop the nipple-local morph rows of every garment vert on the side of an apex whose skin
    within ZAP_NIPPLE_R is all deleted. Returns the number of rows dropped (0 = the .tri is unchanged)."""
    used = np.zeros(len(V), bool); used[np.asarray(kept, int).ravel()] = True
    gone = [a for a in apexes if not used[np.linalg.norm(V - V[a], axis=1) < ZAP_NIPPLE_R].any()]
    if not gone or not local_names:
        return 0
    A = V[apexes]; dropped = 0
    for ts in tf.shapes:
        if ts.name == BODY_NAME or ts.name not in garment_verts:
            continue
        G = garment_verts[ts.name]
        near = np.argmin(np.linalg.norm(G[:, None, :] - A[None], axis=2), axis=1)   # each vert's own apex side
        flat = np.isin(np.asarray(apexes)[near], gone)
        for m in ts.morphs:
            if m.name.lower() not in local_names or not len(m.idx):
                continue
            i = np.asarray(m.idx, int); ok = (i >= 0) & (i < len(G))
            drop = np.zeros(len(i), bool); drop[ok] = flat[i[ok]]
            if drop.any():
                dropped += int(drop.sum()); m.idx = m.idx[~drop]; m.delta = m.delta[~drop]
    return dropped


# #zap-once: a pair's two weights are written one after the other and the zap is called after each; zapping the
# first one alone is thrown away when its partner lands (3 renders and 3 rewrites for a pair instead of 2 and 2).
# While the partner is still to come -- missing, or older than this file by more than ZAP_PARTNER_FRESH_S (a
# previous run's output about to be replaced) -- wait for it; the partner's turn zaps both. Output unchanged.
ZAP_PARTNER_FRESH_S = 1800.0


def _envelope_kept_nipples(tf, loaded, kept, collider_names) -> int:
    """#zap-nipple-envelope: lift cloth .tri rows so the cloth over KEPT nipple skin rises at least as far as that skin
    under every nipple-local slider, at each weight. Returns the number of (row, slider) lifts (0 = .tri unchanged)."""
    from types import SimpleNamespace
    from . import nif_convert as nc
    from . import sliderset_gen as sg
    from .nif_convert_trigen import _nipple_morph_bridge_inputs
    from .tri import TriMorph
    tri = {s.name: s for s in tf.shapes}
    bt = tri.get(BODY_NAME)
    if bt is None:
        return 0
    lifted = 0
    for w, (nf, _p) in loaded.items():
        body = next((s for s in nf.shapes if s.name == BODY_NAME), None)
        if body is None:
            continue
        full, _ = full_body_tris(w or "_1")
        BV = np.asarray(body.verts, np.float64)
        drawn = np.zeros(len(BV), bool); drawn[np.asarray(kept, np.int64).ravel()] = True
        nb = _nipple_morph_bridge_inputs(SimpleNamespace(verts=body.verts, tris=full, bone_weights=body.bone_weights))
        if not nb:
            continue
        dm = np.min([np.linalg.norm(BV - BV[ap], axis=1) for ap, _n, _a in nb], axis=0)
        if not drawn[dm < ZAP_NIPPLE_R].any():
            continue                                   # no kept nipple skin at this weight
        local_idx = np.flatnonzero(dm < sg.NIPPLE_MORPH_CORE_R)
        far_idx = np.flatnonzero((dm >= sg.NIPPLE_MORPH_FAR_R[0]) & (dm < sg.NIPPLE_MORPH_FAR_R[1]))
        bufs = []
        for bm in bt.morphs:
            if not len(bm.idx):
                continue
            buf = np.zeros_like(BV); i = np.asarray(bm.idx, np.int64); ok = (i >= 0) & (i < len(BV))
            buf[i[ok]] = bm.delta[ok]
            if sg._nipple_local(buf, local_idx, far_idx):
                bufs.append((bm.name, buf))
        if not bufs:
            continue
        # the kept nipple skin, each point with its own apex's normal (the direction the envelope lifts along)
        sk = np.flatnonzero(drawn & (dm < ZAP_NIPPLE_R))
        apn = np.asarray([n for _ap, n, _a in nb], np.float64)
        side = np.argmin(np.stack([np.linalg.norm(BV[sk] - BV[ap], axis=1) for ap, _n, _a in nb]), axis=0)
        skn = apn[side]
        bn = np.zeros_like(BV); fn = np.cross(BV[full[:, 1]] - BV[full[:, 0]], BV[full[:, 2]] - BV[full[:, 0]])
        for k in range(3):
            np.add.at(bn, full[:, k], fn)
        bn /= np.linalg.norm(bn, axis=1, keepdims=True) + 1e-12
        for s in nf.shapes:
            ts = tri.get(s.name)
            if ts is None or s.name in nc.UBE_BODY_INJECT_NAMES or s.name in collider_names or not len(s.tris):
                continue
            try:
                if int(s.flags) & 1:
                    continue
            except Exception:
                pass
            av = np.asarray(s.verts, np.float64); T = np.asarray(s.tris, np.int64).reshape(-1, 3)
            # the cloth TRIANGLE over each kept skin point (a coarse plate spans the nipple with corners far outside
            # the vertex envelope's reach, and its flat interior is what the nipple comes through)
            tst = _fm().ClipTester(av, T, tmax=6.0)
            O = BV[sk] - 0.5 * bn[sk]
            ri, ti = tst._pairs_segment(O, bn[sk])   # #cast-segment-ball
            t, who = tst._cast(O, bn[sk], ri, ti, len(O), want_tri=True)
            hit = np.flatnonzero(np.isfinite(t))
            if not len(hit):
                continue
            P = O[hit] + t[hit, None] * bn[sk][hit]
            tri_i = T[who[hit]]
            a, b_, c = av[tri_i[:, 0]], av[tri_i[:, 1]], av[tri_i[:, 2]]
            v0, v1, v2 = b_ - a, c - a, P - a
            d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
            d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
            den = d00 * d11 - d01 * d01; den[np.abs(den) < 1e-12] = 1e-12
            wb = (d11 * d20 - d01 * d21) / den; wc = (d00 * d21 - d01 * d20) / den
            bary = np.clip(np.stack([1.0 - wb - wc, wb, wc], 1), 0.0, 1.0)
            pts, nrm = sk[hit], skn[hit]
            by_name = {m.name: m for m in ts.morphs}
            for name, buf in bufs:
                gm = by_name.get(name)
                D = np.zeros_like(av)
                if gm is not None:
                    i = np.asarray(gm.idx, np.int64); ok = (i >= 0) & (i < len(av)); D[i[ok]] = gm.delta[ok]
                rise = np.einsum("ij,ij->i", buf[pts], nrm)
                have = np.einsum("ij,ij->i", np.einsum("ik,ikj->ij", bary, D[tri_i]), nrm)
                need = rise - have
                bad = need > 1e-4
                if not bad.any():
                    continue
                # raise all three corners of an under-rising triangle by what it lacks: a uniform corner lift moves
                # every interior point by exactly that much (max-combined per corner, along its apex normal)
                lift = np.zeros(len(av)); dirn = np.zeros_like(av)
                for k in range(3):
                    np.maximum.at(lift, tri_i[bad, k], need[bad])
                    dirn[tri_i[bad, k]] = nrm[bad]
                rows = np.flatnonzero(lift > 0)
                D[rows] += dirn[rows] * lift[rows, None]
                lifted += int(len(rows))
                keep = np.flatnonzero(np.linalg.norm(D, axis=1) > 0)
                if gm is None:
                    ts.morphs.append(TriMorph(name, keep, D[keep])); by_name[name] = ts.morphs[-1]
                else:
                    gm.idx, gm.delta = keep, D[keep]
    return lifted


def _flatten_cloth_bump(V, T, BV, full, apexes, kept_skin=None):
    """#zap-nipple-hide: over each hidden nipple, lower a cloth shape's own bump onto the quadric its ring (2.5-5u from
    the apex, along the apex normal) continues over the core -- `nif_convert_fitgeom._nipple_bridge`'s shape without its
    morph-demand lift (the skin it made room for is deleted). Lower only, smoothstep-faded out by 2.5u, outward-facing
    wall only, and only a garment fine enough to draw a nipple (median core edge <= NIPPLE_BRIDGE_MAX_EDGE); a coarse
    plate gets no point to lower once #min-push leaves the zapped nipple alone (#min-push-zap-nipple). The lowering is
    cut back where it would bring the cloth onto `kept_skin` (body points the zap keeps). Returns the new verts or None."""
    from . import nif_convert as nc
    V = np.array(V, np.float64, copy=True); T = np.asarray(T, np.int64).reshape(-1, 3)
    bn = np.zeros_like(BV); fn = np.cross(BV[full[:, 1]] - BV[full[:, 0]], BV[full[:, 2]] - BV[full[:, 0]])
    for k in range(3):
        np.add.at(bn, full[:, k], fn)
    bn /= np.linalg.norm(bn, axis=1, keepdims=True) + 1e-12
    vn = np.zeros_like(V); gf = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    for k in range(3):
        np.add.at(vn, T[:, k], gf)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12
    changed = False
    r_in, r_full = ZAP_HIDE_CORE_R, ZAP_HIDE_CORE_R - 1.0
    for a in apexes:
        A, nA = BV[a], bn[a]
        ea = np.cross(nA, [0.0, 0.0, 1.0])
        if np.linalg.norm(ea) < 1e-6:
            ea = np.cross(nA, [1.0, 0.0, 0.0])
        ea /= np.linalg.norm(ea); eb = np.cross(nA, ea)
        rel = V - A; u, w, h = rel @ ea, rel @ eb, rel @ nA; r = np.hypot(u, w)
        cand = (r < ZAP_NIPPLE_R) & (h > -4.0) & (h < 4.0)
        if int(cand.sum()) < 15:
            continue
        d = vn @ nA
        sg = 1.0 if float(d[cand].sum()) >= 0.0 else -1.0
        cand &= (sg * d) > 0.3
        ann, core = cand & (r >= r_in), cand & (r < r_in)
        if int(ann.sum()) < 12 or int(core.sum()) < 3:
            continue
        tc = T[core[T].any(axis=1)]
        el = np.linalg.norm(V[tc[:, [0, 1, 2]]] - V[tc[:, [1, 2, 0]]], axis=2)
        if float(np.median(el)) > float(nc.NIPPLE_BRIDGE_MAX_EDGE):
            continue
        q = lambda uu, ww: np.c_[np.ones_like(uu), uu, ww, uu * uu, uu * ww, ww * ww]
        coef, *_ = np.linalg.lstsq(q(u[ann], w[ann]), h[ann], rcond=None)
        target = q(u[core], w[core]) @ coef
        t = np.clip((r_in - r[core]) / max(r_in - r_full, 1e-6), 0.0, 1.0)
        dh = t * t * (3.0 - 2.0 * t) * (np.minimum(target, h[core]) - h[core])
        ci = np.flatnonzero(core)
        if kept_skin is not None and len(kept_skin) and float(np.abs(dh).max(initial=0.0)) > 1e-4:
            dh = dh * _keep_skin_scale(V, T, ci, dh, nA, A, ea, eb, kept_skin, ZAP_NIPPLE_R + 2.0)
        if float(np.abs(dh).max(initial=0.0)) > 1e-4:
            V[ci] += nA[None, :] * dh[:, None]; changed = True
    return V if changed else None


def _keep_edge_band(de, bind_vis, Vref, poked=None) -> np.ndarray:
    """Un-delete the skin within ZAP_BAND of skin visible at bind (a margin round every opening). Skin let go for
    poking through cloth (`poked`, #zap-nipple-poke) stays deleted: that cloth lies just under it, not a hole -- the
    band brought it back beside visible skin and it poked through again (357 replay: 5 pieces, up to 48 u^2)."""
    if not (bind_vis.any() and de.any() and float(ZAP_BAND) > 0):
        return de
    dk, _ = cKDTree(Vref[bind_vis]).query(Vref[de])
    band = np.flatnonzero(de)[dk < float(ZAP_BAND)]
    if poked is not None:
        band = band[~poked[band]]
    de = de.copy(); de[band] = False
    return de


def _poking_through(B, BT, Gvs, Gts, idx, n) -> np.ndarray:
    """Bool per body vertex: the vertices `idx` that have cover cloth within ZAP_POKE_DEPTH UNDER them along their own
    outward normal (the skin pokes through that cloth) -- #zap-nipple-poke."""
    B = np.asarray(B, np.float64); BT = np.asarray(BT, np.int64)
    fn = np.cross(B[BT[:, 1]] - B[BT[:, 0]], B[BT[:, 2]] - B[BT[:, 0]])
    vn = np.zeros_like(B)
    for k in range(3):
        np.add.at(vn, BT[:, k], fn)
    vn /= np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12
    if ((B[BT].mean(1) - B.mean(0)) * fn).sum(1).mean() < 0:
        vn = -vn                                          # outward
    out = np.zeros(n, bool)
    idx = np.asarray(idx, np.int64)
    O = B[idx] + float(ZAP_POKE_LEVEL) * vn[idx]; D = -vn[idx]
    for V, T in zip(Gvs, Gts):
        if not len(T):
            continue
        tst = _fm().ClipTester(np.asarray(V, np.float64), np.asarray(T, np.int64), tmax=float(ZAP_POKE_DEPTH + ZAP_POKE_LEVEL) + 1.0)
        t = tst.cast(O, D)
        out[idx[np.isfinite(t) & (t <= float(ZAP_POKE_DEPTH + ZAP_POKE_LEVEL))]] = True
    return out


def _keep_skin_scale(V, T, ci, dh, nA, A, ea, eb, kept_skin, reach) -> float:
    """Largest share (1, 1/2, 1/4 ... or 0) of the lowering `dh` on verts `ci` that keeps the cloth, measured along
    `nA` over each kept skin point under a moved triangle, ZAP_HIDE_KEEP_GAP above it -- or no closer than it already
    was where it started closer."""
    P = np.asarray(kept_skin, np.float64)
    rel = P - A; pu, pw, ph = rel @ ea, rel @ eb, rel @ nA
    sel = np.hypot(pu, pw) < reach
    if not sel.any():
        return 1.0
    pu, pw, ph = pu[sel], pw[sel], ph[sel]
    moved = np.zeros(len(V), bool); moved[ci] = True
    tm = T[moved[T].any(axis=1)]
    gu, gw = (V - A) @ ea, (V - A) @ eb
    x1, y1, x2, y2, x3, y3 = gu[tm[:, 0]], gw[tm[:, 0]], gu[tm[:, 1]], gw[tm[:, 1]], gu[tm[:, 2]], gw[tm[:, 2]]
    den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    ok = np.abs(den) > 1e-9; den = np.where(ok, den, 1.0)
    # barycentric of every (skin point, moved triangle) pair in the apex-plane projection
    l1 = ((y2 - y3)[None] * (pu[:, None] - x3[None]) + (x3 - x2)[None] * (pw[:, None] - y3[None])) / den[None]
    l2 = ((y3 - y1)[None] * (pu[:, None] - x3[None]) + (x1 - x3)[None] * (pw[:, None] - y3[None])) / den[None]
    l3 = 1.0 - l1 - l2
    pi, ti = np.nonzero(ok[None] & (l1 >= 0) & (l2 >= 0) & (l3 >= 0))
    if not len(pi):
        return 1.0
    L = np.stack([l1[pi, ti], l2[pi, ti], l3[pi, ti]], 1)
    H = (V - A) @ nA
    D = np.zeros(len(V)); D[ci] = dh
    tri = tm[ti]
    gap0 = (L * H[tri]).sum(1) - ph[pi]; dd = (L * D[tri]).sum(1)
    floor = np.minimum(gap0, ZAP_HIDE_KEEP_GAP)
    s = 1.0
    for _ in range(8):
        if np.all(gap0 + s * dd >= floor - 1e-6):
            return s
        s *= 0.5
    return 0.0


def _fm():
    from types import SimpleNamespace
    from . import fit_metrics as fm
    return SimpleNamespace(ClipTester=fm._ClipTester)


def partner_pending(dst_path, expect_partner: bool) -> bool:
    """True while `dst_path`'s weight partner, which this conversion will also write, is not on disk yet."""
    if not expect_partner:
        return False
    dst_path = Path(dst_path); st = dst_path.stem
    if st[-2:] not in ("_0", "_1"):
        return False
    other = dst_path.with_name(st[:-2] + ("_1" if st.endswith("_0") else "_0") + dst_path.suffix)
    try:
        return (not other.is_file()) or other.stat().st_mtime < dst_path.stat().st_mtime - ZAP_PARTNER_FRESH_S
    except OSError:
        return False


# #zap-prune-morphs (ON with the zap; CBBE2UBE_NO_ZAP_PRUNE_MORPHS=1 keeps them): once a pair is final, the body's
# morph rows on vertices no kept triangle uses are dropped from the .tri -- an undrawn vertex can move however it
# likes. On the fit sample 62% of the body rows sat on undrawn vertices: zapped pieces' .tri 235 -> 115 MB.
# Runs only at the END of a pair unit (both weights written, the zap final), never between the two weights.
ZAP_PRUNE_MORPHS = not _flag("CBBE2UBE_NO_ZAP_PRUNE_MORPHS", False)


def prune_undrawn_body_morphs(dst_path) -> int:
    """Drop the injected body's .tri rows on vertices neither weight draws. Returns rows dropped (0 = untouched)."""
    from . import nif_convert as nc
    from .tri import TriFile
    dst_path = Path(dst_path); files = _pair_paths(dst_path)
    tri_path = dst_path.with_name((dst_path.stem[:-2] if dst_path.stem[-2:] in ("_0", "_1") else dst_path.stem) + ".tri")
    if not files or not tri_path.is_file():
        return 0
    pyn = nc._pynifly(); used = None
    for _w, p in files.items():
        body = next((s for s in pyn.NifFile(filepath=str(p)).shapes if s.name == BODY_NAME), None)
        if body is None:
            return 0
        u = np.zeros(len(body.verts), bool); t = np.asarray(body.tris, np.int64)
        if len(t):
            u[t.ravel()] = True
        if used is not None and len(used) != len(u):
            return 0
        used = u if used is None else (used | u)
    if used is None or used.all():
        return 0                                   # not zapped: every vertex is drawn
    tf = TriFile.load(tri_path); dropped = 0
    for ts in tf.shapes:
        if ts.name != BODY_NAME:
            continue
        for m in ts.morphs:
            if not len(m.idx):
                continue
            i = np.asarray(m.idx, int); keep = (i >= 0) & (i < len(used))
            keep[keep] = used[i[keep]]
            if not keep.all():
                dropped += int((~keep).sum()); m.idx = m.idx[keep]; m.delta = m.delta[keep]
    if dropped:
        tf.save(tri_path)
    return dropped


# ---------------------------------------------------------------- #zap-alpha: see-through cover
_ALPHA_TEX: dict = {}
_TEX_ARCHIVES: list = []          # [the load order's .dds archive index], built on the first archive-only texture


def _archive_texture(key, lay, mr, order) -> "bytes | None":
    """#zap-alpha-archives: the bytes of `key` (lowercase 'textures/...') from the load order's archives, the copy the
    game draws (the converter's archive index: the archive whose plugin loads last wins), or None."""
    if not ZAP_ALPHA_ARCHIVES or mr is None:
        return None
    if not _TEX_ARCHIVES:
        from . import auto_convert as ac
        _TEX_ARCHIVES.append(ac._BsaMeshIndex(
            ac._load_order_bsa_dirs(mr, order, lay.game_data_dirs), None,
            skip_bsa=ac._BsaMeshIndex._SKIP_BSA, plugin_order=ac._bsa_plugin_order(lay), ext=".dds"))
    return _TEX_ARCHIVES[0].read_bytes(key)


def _texture_alpha(rel, dst_path):
    """The alpha channel of a texture as the game resolves it (loose: mod priority, then the overwrite and the game
    Data folders; else the load order's archives, #zap-alpha-archives), decoded with texconv into a work folder inside
    the output mod. None when it cannot be read (not found, no texconv): the caller then treats the shape as opaque."""
    key = str(rel).replace("\\", "/").lower().lstrip("/")
    if not key.startswith("textures/"):
        key = "textures/" + key
    if key in _ALPHA_TEX:
        return _ALPHA_TEX[key]
    a = None
    try:
        from . import paths as P
        from . import overlay_transfer as ot
        lay = P.discover_layout(); mr = P.mods_root(); order = P.enabled_mods_ordered(lay) or []
        cands = [Path(mr) / m / key for m in order] if mr is not None else []
        ow = P.overwrite_dir(lay)
        if ow is not None:
            cands.insert(0, Path(ow) / key)
        cands += [Path(d) / key for d in (lay.game_data_dirs or [])]
        src = next((c for c in cands if c.is_file()), None)
        tc = ot.find_texconv()
        packed = _archive_texture(key, lay, mr, order) if src is None and tc is not None else None
        if (src is not None or packed) and tc is not None:
            import shutil
            out = Path(dst_path)
            while out.parent != out and out.name.lower() != "meshes":
                out = out.parent
            if out.name.lower() != "meshes":
                raise RuntimeError("no meshes folder above the written piece; no work folder for texconv")
            work = ot._new_work_dir(out.parent, "zap_alpha_")
            try:
                if src is None:                         # archive copy: texconv reads files
                    src = work / "archive_texture.dds"
                    src.write_bytes(packed)
                a = np.ascontiguousarray(ot.dds_to_rgba(src, tc, work)[..., 3])
            finally:
                shutil.rmtree(work, ignore_errors=True)
                try:
                    work.parent.rmdir()                 # the shared work folder, once empty
                except OSError:
                    pass
    except Exception as e:
        from . import nif_convert as nc
        nc._note_pass_failure("zap-alpha/texture", e, dst_path)
        a = None
    if len(_ALPHA_TEX) > 8:
        _ALPHA_TEX.pop(next(iter(_ALPHA_TEX)))
    _ALPHA_TEX[key] = a
    return a


def _opaque_tris(shape, T, dst_path):
    """#zap-alpha: for an alpha-tested / alpha-blended cover shape, the triangles that are opaque at every sample (the
    corners, the edge midpoints, the centre) under its own threshold; None = treat the whole shape as cover."""
    ap = getattr(shape, "alpha_property", None)
    if ap is None:
        return None
    try:
        f = int(ap.properties.flags); thr = int(getattr(ap.properties, "threshold", 128))
    except Exception:
        return None
    if not (f & 0x200 or f & 1):
        return None
    if not (f & 0x200):
        thr = 128                                   # blended: half-transparent and below lets the skin through
    tex = (shape.textures or {}).get("Diffuse") if hasattr(shape, "textures") else None
    if not tex:
        return None
    A = _texture_alpha(tex, dst_path)
    _uv = getattr(shape, "uvs", None)
    uv = np.asarray(_uv if _uv is not None else [], np.float64)
    if A is None or len(uv) == 0:
        return None
    H, W = A.shape
    w = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [.5, .5, 0], [0, .5, .5], [.5, 0, .5], [1 / 3, 1 / 3, 1 / 3]])
    S = np.einsum("sk,tkj->tsj", w, uv[T])          # (tris, 7, 2)
    wrap = lambda t: np.where((t >= 0.0) & (t <= 1.0), t, t % 1.0)   # tile outside 0..1; 1.0 is the far edge, not 0
    x = np.clip(wrap(S[..., 0]) * W, 0, W - 1).astype(np.int64)
    y = np.clip(wrap(S[..., 1]) * H, 0, H - 1).astype(np.int64)
    return (A[y, x] >= thr).all(axis=1)


# ---------------------------------------------------------------- #zap-leg-push: cloth over kept leg / hip skin
def _body_normals(BV, T) -> np.ndarray:
    """Outward unit vertex normals of the body (the same outward test `needed` uses)."""
    fn = np.cross(BV[T[:, 1]] - BV[T[:, 0]], BV[T[:, 2]] - BV[T[:, 0]])
    sign = 1.0 if ((BV[T].mean(1) - BV.mean(0)) * fn).sum(1).mean() >= 0 else -1.0
    vn = np.zeros_like(BV)
    for k in range(3):
        np.add.at(vn, T[:, k], fn)
    return sign * vn / (np.linalg.norm(vn, axis=1, keepdims=True) + 1e-12)


def _weld_max_lift(V, d, chain) -> np.ndarray:
    """#zap-leg-push: a seam (coincident verts within or across shapes) takes the largest lift of its verts, or it
    opens; simulated verts keep 0."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    d = np.array(d, np.float64, copy=True)
    pairs = cKDTree(V).query_pairs(1e-4, output_type="ndarray")
    if len(pairs):
        n = len(V)
        _, lab = connected_components(coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)),
                                      directed=False)
        mag = np.linalg.norm(d, axis=1)
        idx = np.unique(pairs.ravel())
        best = {}
        for i in idx:
            if lab[i] not in best or mag[i] > mag[best[lab[i]]]:
                best[lab[i]] = i
        d[idx] = d[[best[lab[i]] for i in idx]]
    d[np.asarray(chain, bool)] = 0.0
    return d


def _room_cap(nf, gV, d, chain, coll, cur, body_bones) -> "tuple[np.ndarray, int]":
    """#zap-leg-push-room: `d` with each lift shortened so the vert stays ZAP_LEG_PUSH_ROOM under the first
    simulated cloth (any shape's triangles with a simulated vert) or collider shape its lift runs into; the number of
    verts capped."""
    from . import nif_convert as nc
    from . import fit_metrics as fm
    CV, CT, off = [], [], 0
    for s in nf.shapes:
        if s.name in nc.UBE_BODY_INJECT_NAMES or not len(s.tris):
            continue
        try:
            hidden = bool(int(s.flags) & 1)
        except Exception:
            hidden = False
        T = np.asarray(s.tris, np.int64).reshape(-1, 3)
        if not ((hidden or s.name in coll) and ZAP_LEG_PUSH_ROOM_COLLIDERS):
            T = T[_free_verts(s, body_bones)[T].any(1)]
        if len(T):
            CV.append(np.asarray(cur.get(s.name, s.verts), np.float64)); CT.append(T + off); off += len(CV[-1])
    L = np.linalg.norm(d, axis=1)
    lift = np.flatnonzero((L > 1e-4) & ~np.asarray(chain, bool))
    if not CV or not len(lift):
        return d, 0
    room = float(ZAP_LEG_PUSH_ROOM)
    t = fm._ClipTester(np.concatenate(CV), np.concatenate(CT), tmax=float(L.max()) + room + 0.1).cast(
        gV[lift], d[lift] / L[lift, None], tmin=1e-3)
    t = np.nan_to_num(np.asarray(t, np.float64), nan=np.inf, posinf=np.inf)
    allow = np.clip(t - room, 0.0, None)
    cap = allow < L[lift]
    d = d.copy()
    d[lift[cap]] *= (allow[cap] / L[lift[cap]])[:, None]
    return d, int(cap.sum())


def _stack_lift(Vs, d, chain, BV, BN) -> np.ndarray:
    """#zap-leg-push-stack: the extra lift (concatenated like `d`) that tops each layer up to the lift of a layer it
    rests above -- `nif_convert._stack_push_p1` on the per-shape lifts; `chain` verts never move."""
    from types import SimpleNamespace
    from . import nif_convert as nc
    jobs, start = [], 0
    for V in Vs:
        dv = d[start:start + len(V)]
        src = SimpleNamespace()
        nc._STACK_AP[id(src)] = (V, V + dv, np.asarray(chain[start:start + len(V)], bool))
        jobs.append({"src": src, "verts": V + dv})
        start += len(V)
    try:
        nc._stack_push_p1(jobs, BV, BN)
    finally:
        for j in jobs:
            nc._STACK_AP.pop(id(j["src"]), None)
    return np.concatenate([j.get("stack_extra", np.zeros((len(V), 3))) for j, V in zip(jobs, Vs)])


def _push_kept_legs(nf, w, kept, coll, cur) -> "tuple[dict, dict]":
    """#zap-leg-push for one weight file: ({shape name: new verts}, stats). `kept` = the body triangles the zap keeps,
    `coll` = collider shapes (never moved), `cur` = verts another zap step already replaced ({name: verts})."""
    from . import nif_convert as nc
    from . import fit_metrics as fm
    body = next(s for s in nf.shapes if s.name == BODY_NAME)
    fw, _ = full_body_tris(w or "_1")
    BV = np.asarray(body.verts, np.float64)
    region = np.zeros(len(BV), bool)
    region[np.unique(np.asarray(kept, np.int64))] = True
    region &= BV[:, 2] < float(ZAP_LEG_PUSH_Z)
    if int(region.sum()) < fm.PUSH_MIN_REGION:
        return {}, {"skipped": "region too small"}
    body_bones = set(body.bone_names or [])
    names, Vs, Ts, Ns, chain, off = [], [], [], [], [], 0
    for s in nf.shapes:
        if s.name in nc.UBE_BODY_INJECT_NAMES or not len(s.tris):
            continue
        if s.name in coll:
            continue
        try:
            if int(s.flags) & 1:
                continue
        except Exception:
            pass
        V = np.asarray(cur.get(s.name, s.verts), np.float64)
        T = np.asarray(s.tris, np.int64).reshape(-1, 3)
        src_n = np.asarray(s.normals, np.float64) if getattr(s, "normals", None) is not None else None
        names.append(s.name); Vs.append(V); Ts.append(T + off)
        Ns.append(np.asarray(nc._recompute_vertex_normals(V, T, source_normals=src_n), np.float64))
        chain.append(_free_verts(s, body_bones)); off += len(V)
    if not names:
        return {}, {"skipped": "no cloth"}
    gV = np.concatenate(Vs); gT = np.concatenate(Ts); ch = np.concatenate(chain)
    if ZAP_LEG_PUSH_SIM:
        ch = np.zeros_like(ch)                          # #zap-leg-push-sim: physics cloth is a layer like any other
    new, st = fm.minimum_push(gV, gT, np.concatenate(Ns), BV, fw, _body_normals(BV, fw), is_chain=ch, region=region,
                              max_depth=float(ZAP_LEG_PUSH_MAX_DEPTH),
                              max_total=float(ZAP_LEG_PUSH_MAX_DEPTH) + 0.1)   # shallow poke needs no more
    if not st.get("moved"):
        return {}, st
    d = new - gV
    if ZAP_LEG_PUSH_ROOM:
        d, st["room_capped"] = _room_cap(nf, gV, d, ch, coll, cur, body_bones)
    if ZAP_LEG_PUSH_STACK and nc._STACK_PUSH:
        # #zap-leg-push-stack: a visible collider-listed shape (a physics dress) is no cover -- it swings off the skin
        # -- but where it rests just above a lifted layer it rides the lift, so the two keep their order
        pas = [(s.name, np.asarray(cur.get(s.name, s.verts), np.float64), _free_verts(s, body_bones))
               for s in nf.shapes if s.name in coll and len(s.tris) and not (int(getattr(s, "flags", 0) or 0) & 1)]
        if pas:
            names = names + [n for n, _, _ in pas]; Vs = Vs + [V for _, V, _ in pas]
            pch = np.concatenate([np.zeros(len(V), bool) if ZAP_LEG_PUSH_SIM else c for _, V, c in pas])
            gV = np.concatenate([gV] + [V for _, V, _ in pas]); ch = np.concatenate([ch, pch])
            d = np.concatenate([d, np.zeros((len(pch), 3))])
        d = d + _stack_lift(Vs, d, ch, BV, _body_normals(BV, fw))
    d = _weld_max_lift(gV, d, ch)
    out, start = {}, 0
    for nm, V in zip(names, Vs):
        dv = d[start:start + len(V)]; start += len(V)
        if np.abs(dv).max() > 1e-4:
            out[nm] = V + dv
    st["shapes"] = len(out)
    return out, st


def zap_pair(dst_path, first_person: bool = False) -> "dict | None":
    """Zap the injected body of `dst_path` and its weight partner (whichever exist). Returns a summary dict, or
    None when the piece has no injected reference body or the renderer is missing."""
    from . import nif_convert as nc
    from .tri import TriFile
    if _dll() is None:
        nc._note_pass_failure("body-zap", RuntimeError("zraster.dll not found; body left whole"), dst_path)
        return None
    dst_path = Path(dst_path); files = _pair_paths(dst_path)
    if not files:
        return None
    pyn = nc._pynifly(); dirs = view_dirs()
    tri_path = dst_path.with_name((dst_path.stem[:-2] if dst_path.stem[-2:] in ("_0", "_1") else dst_path.stem) + ".tri")
    tf = TriFile.load(tri_path) if tri_path.is_file() else None
    tri = {s.name: s for s in tf.shapes} if tf is not None else {}
    presets = preset_paths()
    loaded, need, bind_vis = {}, None, None
    apexes, nip_local, gverts, poked = None, set(), {}, None
    # #zap-fp-armature: the planner's answer (an ARMA uses this mesh only as a first-person model) or the name rule
    first_person = bool(first_person) and ZAP_FP_ARMATURE; arm = None
    for w, p in files.items():
        nf = pyn.NifFile(filepath=str(p))
        body = next((s for s in nf.shapes if s.name == BODY_NAME), None)
        full, Vref = full_body_tris(w or "_1")
        if body is None or len(body.verts) != len(Vref):
            return None
        cur = np.asarray(body.tris, np.int64)
        if len(cur) > len(full) or not np.isin(_tri_keys(cur), _tri_keys(full)).all():
            return None   # not the injected reference body (or a zap of it)
        loaded[w] = (nf, p)
        first_person = first_person or _first_person_viewmodel(Path(p))
        if arm is None:
            arm = _arm_weight(body, len(Vref))
        if apexes is None:
            apexes = _nipple_apexes(body, full) if ZAP_NIPPLE_FLAT else []
            nip_local = _nipple_local_names(tri.get(BODY_NAME), np.asarray(Vref, np.float64), apexes)
        if first_person:
            # A first-person piece keeps the arms and drops the rest whatever the views see (below), so the
            # cloth set and the per-pose renders are never consulted. #zap-fp-no-render
            gverts.update({s.name: np.asarray(s.verts, np.float64) for s in nf.shapes if s.name not in gverts})
            continue
        try:
            coll = set(nc._hdt_collider_shape_names(p, nif=nf, stem_scan=nc._dst_xml_stem_scan()))
        except Exception:
            coll = set()
        body_bones = set(body.bone_names or [])
        G = []
        for s in nf.shapes:
            if s.name in nc.UBE_BODY_INJECT_NAMES or not len(s.tris):
                continue
            try:
                hidden = bool(int(s.flags) & 1)
            except Exception:
                hidden = False
            if hidden or s.name in coll:
                continue
            T = np.asarray(s.tris, np.int64); fv = _free_verts(s, body_bones); T = T[~fv[T].any(1)]
            if ZAP_ALPHA and len(T):
                _om = _opaque_tris(s, T, p)
                if _om is not None:
                    T = T[_om]
            if len(T):
                G.append((s.name, np.asarray(s.verts, np.float64), T))
        gverts.update({s.name: np.asarray(s.verts, np.float64) for s in nf.shapes if s.name not in gverts})
        BV = np.asarray(body.verts, np.float64); BT = full
        # nipple-local sliders left out: #zap-nipple-flat drops them from the cloth afterwards, and the answer must
        # not depend on whether that already happened (a re-run reads the flattened .tri)
        poses = [()] + [tuple(kv for kv in _preset(pp, w != "_0") if kv[0] not in nip_local) for pp in presets]
        zone = None
        if ZAP_NIPPLE_POKE and apexes and not first_person:
            zone = np.min([np.linalg.norm(BV - BV[a], axis=1) for a in apexes], axis=0) < ZAP_NIPPLE_R
        for k, sl in enumerate(poses):
            Bp = _morphed(BV, tri.get(BODY_NAME), sl)
            Gp = [_morphed(V, tri.get(nm), sl) for nm, V, T in G]
            nd = needed(Bp, BT, Gp, [T for _, _, T in G], dirs).astype(bool)
            if zone is not None and (nd & zone).any():
                pk = _poking_through(Bp, BT, Gp, [T for _, _, T in G], np.flatnonzero(nd & zone), len(BV))
                poked = pk if poked is None else (poked | pk)
                nd &= ~pk
            need = nd if need is None else (need | nd)
            if k == 0:
                bind_vis = nd if bind_vis is None else (bind_vis | nd)
    full, Vref = full_body_tris(next(iter(files)) or "_1")
    if first_person:
        need = arm >= ZAP_ARM_MIN                       # arms: all kept; the rest: gone
        de = ~need
    else:
        if np.isfinite(float(ZAP_PRESET_KEEP_R)):
            pre_only = need & ~bind_vis
            if pre_only.any():
                if bind_vis.any():
                    dk, _ = cKDTree(Vref[bind_vis]).query(Vref[pre_only])
                    far = np.flatnonzero(pre_only)[dk >= float(ZAP_PRESET_KEEP_R)]
                else:
                    far = np.flatnonzero(pre_only)
                need = need.copy(); need[far] = False
        need = need | (Vref[:, 2] < float(ZAP_LEG_Z))
        de = ~need
        de = _keep_edge_band(de, bind_vis, Vref, poked)
    hide_apex = []
    if ZAP_NIPPLE_HIDE and not first_person and apexes:
        for a in apexes:
            d = np.linalg.norm(Vref - Vref[a], axis=1)
            ring = (d >= ZAP_HIDE_CORE_R) & (d < ZAP_NIPPLE_R)
            core = d < ZAP_HIDE_CORE_R
            if ring.any() and de[ring].all():
                de = de.copy(); de[core] = True; hide_apex.append(int(a))
            elif ZAP_NIPPLE_HIDE_COVERED and core.any() and not need[core].any():
                de = de.copy(); de[core] = True; hide_apex.append(int(a))
    gone = de[full].sum(1) >= 2
    kept = full[~gone]
    out = {"files": len(loaded), "deleted_tris": int(gone.sum()), "kept_tris": int(len(kept)),
           "first_person": first_person, "poses": 1 + len(presets), "nipple_rows_dropped": 0}
    if tf is not None and apexes:
        n = _flatten_nipples(tf, np.asarray(Vref, np.float64), kept, apexes, nip_local, gverts)
        if n:
            tf.save(tri_path); out["nipple_rows_dropped"] = n
    if tf is not None and ZAP_NIPPLE_ENVELOPE and not first_person:
        try:
            coll = set()
            for _w, (_nf, _p) in loaded.items():
                coll |= set(nc._hdt_collider_shape_names(_p, nif=_nf, stem_scan=nc._dst_xml_stem_scan()))
        except Exception:
            coll = set()
        n = _envelope_kept_nipples(tf, loaded, kept, coll)
        if n:
            tf.save(tri_path); out["nipple_rows_lifted"] = n
    vert_over = {}
    if hide_apex:
        try:
            coll = set()
            for _w, (_nf, _p) in loaded.items():
                coll |= set(nc._hdt_collider_shape_names(_p, nif=_nf, stem_scan=nc._dst_xml_stem_scan()))
        except Exception:
            coll = set()
        for w, (nf, p) in loaded.items():
            body = next(s for s in nf.shapes if s.name == BODY_NAME)
            fw, _ = full_body_tris(w or "_1")
            BV = np.asarray(body.verts, np.float64)
            kept_skin = BV[np.unique(np.asarray(kept, np.int64).ravel())] if len(kept) else BV[:0]
            for s in nf.shapes:
                if s.name in nc.UBE_BODY_INJECT_NAMES or s.name in coll or not len(s.tris):
                    continue
                try:
                    if int(s.flags) & 1:
                        continue
                except Exception:
                    pass
                new = _flatten_cloth_bump(np.asarray(s.verts, np.float64), np.asarray(s.tris, np.int64), BV, fw,
                                          hide_apex, kept_skin=kept_skin)
                if new is not None:
                    vert_over.setdefault(w, {})[s.name] = new
        out["nipples_hidden"] = len(hide_apex)
        out["cloth_flattened"] = sum(len(v) for v in vert_over.values())
        # REJECTED 10-20: also lowering each slider's outward motion over the core onto its ring quadric (8 pieces:
        # the rest-pose bump went, the preset bump did not -- Too Big 0.72 -> 1.20u on the reported top).
    if ZAP_LEG_PUSH and not first_person:
        coll = set()
        for _w, (_nf, _p) in loaded.items():
            try:
                coll |= set(nc._hdt_collider_shape_names(_p, nif=_nf, stem_scan=nc._dst_xml_stem_scan()))
            except Exception:
                pass
        for w, (nf, p) in loaded.items():
            try:
                lifted, st = _push_kept_legs(nf, w, kept, coll, vert_over.get(w) or {})
            except Exception as e:
                nc._note_pass_failure("zap-leg-push", e, p)
                continue
            if lifted:
                vert_over.setdefault(w, {}).update(lifted)
                out.setdefault("leg_push", {})[w] = {k: st.get(k) for k in
                                                     ("moved", "exposed_before", "exposed_after", "max_push",
                                                      "room_capped")}
    for w, (nf, p) in loaded.items():
        cur = next(s for s in nf.shapes if s.name == BODY_NAME)
        if (len(cur.tris) == len(kept) and np.array_equal(np.asarray(cur.tris, np.int64), kept)
                and not vert_over.get(w)):
            continue                                       # already zapped exactly so
        if not nc._reauthor_nif_fresh(p, nif=nf, override_tris_by_name={BODY_NAME: kept},
                                      override_verts_by_name=vert_over.get(w) or None,
                                      override_partition_tris_by_name={BODY_NAME: [0] * len(kept)}):
            nc._note_pass_failure("body-zap", RuntimeError("rebuild failed; body left whole"), p)
            return None
    return out
