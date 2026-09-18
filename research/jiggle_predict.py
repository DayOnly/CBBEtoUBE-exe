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

"""Offline prediction of physics-MOTION (jiggle) clip risk between a fitted
garment and the body it is worn on -- decided at conversion time, no game run.

WHY THIS IS COMPUTABLE OFFLINE
    In a 3BA-style body the visible breast/butt/belly jiggle is bone-driven
    linear-blend skinning: the simulated virtual bones translate within bounded
    constraint limits, and the body mesh is weighted to them. So a body vertex's
    PEAK outward excursion under jiggle is just

        body_exc(v) = sum over jiggle bones b of  w_body(v, b) * E[b]

    where E[b] is the bone's peak excursion magnitude in game units. A garment
    that carries the same jiggle-bone weights follows the motion by the same law,
    so its excursion is garment_exc(g) = sum_b w_garment(g, b) * E[b].

    The body pokes through the garment during motion where the body surface can
    reach past the garment's STATIC standoff faster than the garment moves with
    it:

        margin(g) = body_exc(nearest body vert) - garment_exc(g) - standoff(g)

    margin > 0 predicts a motion-clip. The body is KNOWN at conversion time, so
    this needs no worn-combo and no game run.

WHAT IT IS AND IS NOT
    It is a WORST-CASE, KINEMATIC bound: it uses the peak excursion (bounded by
    constraint limits, not the actual per-animation motion) and ignores collision
    damping and any SMP reaction in the garment -- so it OVER-predicts on the safe
    side. It is relative to ONE jiggle config, captured in E[]. It tells you what
    CAN clip, not what WILL clip in a given pose. Use it to TARGET a remedy, then
    confirm in-game.

    E[] is the single calibration input. Derive it from one in-game capture with
    calibrate_excursion(); the module DEFAULTS are conservative placeholders, not
    measurements -- do not treat predictions as quantitative until E[] is set.

This module is pure (arrays in, arrays out) so it is unit-testable without
pynifly or the game. The thin NIF-loading wrappers live in the diagnostic runner.
"""
from __future__ import annotations

import numpy as np

# Mirrors nif_convert.PHYSICS_JIGGLE_SCALE_KEYWORDS -- the bones whose runtime
# motion is physics jiggle (not slider morph). Kept local so this module has no
# heavy import. "glute" catches the spread of butt-bone naming.
JIGGLE_BONE_KEYWORDS = ("breast", "butt", "belly", "glute")

# Peak OUTWARD excursion (game units) at FULL (1.0) bone weight, per jiggle zone.
# CALIBRATION CONSTANTS -- placeholders until set from an in-game capture via
# calibrate_excursion(). Override per call; never rely on these for a real number.
DEFAULT_EXCURSION = {"breast": 2.0, "glute": 1.5, "butt": 1.5, "belly": 1.0}


def is_jiggle_bone(name: str, keywords=JIGGLE_BONE_KEYWORDS) -> bool:
    low = (name or "").lower()
    return any(k in low for k in keywords)


def excursion_for_bone(bone_name: str, excursion=DEFAULT_EXCURSION) -> float:
    """Peak excursion scalar for a bone name = the largest matching zone value
    (a bone matching multiple keywords takes the worst-case)."""
    low = (bone_name or "").lower()
    best = 0.0
    for kw, e in excursion.items():
        if kw in low and float(e) > best:
            best = float(e)
    return best


def jiggle_field(bone_weights, n_verts: int, *, excursion=DEFAULT_EXCURSION,
                 keywords=JIGGLE_BONE_KEYWORDS) -> np.ndarray:
    """Per-vertex peak jiggle excursion magnitude (game units) for a skinned
    mesh. `bone_weights` maps bone_name -> iterable of (vert_idx, weight). Sums
    w*E over jiggle bones -- a vertex is normally dominated by a single jiggle
    bone, so the sum is a tight upper bound (and a strict one when zones overlap).
    Non-jiggle bones contribute nothing. Returns float64 array length n_verts."""
    field = np.zeros(int(n_verts), dtype=np.float64)
    if not bone_weights:
        return field
    for bone, pairs in bone_weights.items():
        if not is_jiggle_bone(bone, keywords):
            continue
        e = excursion_for_bone(bone, excursion)
        if e <= 0.0:
            continue
        for vi, w in pairs:
            iv = int(vi)
            if 0 <= iv < field.size:
                field[iv] += float(w) * e
    return field


def predict_clip(garment_verts, garment_bone_weights, body_verts, body_normals,
                 body_field, *, excursion=DEFAULT_EXCURSION,
                 keywords=JIGGLE_BONE_KEYWORDS, max_body_dist: float = 10.0):
    """Per garment vertex motion-clip prediction against a known body.

    Args:
        garment_verts:        (N,3) garment verts in BODY space.
        garment_bone_weights: {bone -> [(vert_idx, w)]} for the garment.
        body_verts:           (V,3) body verts in BODY space.
        body_normals:         (V,3) outward body normals.
        body_field:           (V,) per-body-vert jiggle excursion (jiggle_field).
        max_body_dist:        garment verts farther than this from the body are
                              not body-fitted (loose drape) -> not predicted.

    Returns a dict of (N,) arrays:
        margin      body_exc - garment_exc - standoff  (>0 predicts a clip)
        standoff    signed distance of the vert outside the body along its
                    nearest body normal (negative = already inside the body)
        body_exc    body jiggle excursion sampled at the nearest body vert
        garment_exc the garment's own jiggle-follow excursion at the vert
        dist        distance to the nearest body vert
    Far verts get margin = -inf (excluded). Requires scipy.
    """
    from scipy.spatial import cKDTree
    gv = np.asarray(garment_verts, dtype=np.float64)
    bv = np.asarray(body_verts, dtype=np.float64)
    bn = np.asarray(body_normals, dtype=np.float64)
    n = len(gv)
    gexc = jiggle_field(garment_bone_weights, n, excursion=excursion,
                        keywords=keywords)
    if n == 0 or len(bv) == 0:
        z = np.zeros(n)
        return {"margin": np.full(n, -np.inf), "standoff": z, "body_exc": z,
                "garment_exc": gexc, "dist": np.full(n, np.inf)}
    tree = cKDTree(bv)
    dist, idx = tree.query(gv)
    nrm = bn[idx]
    standoff = ((gv - bv[idx]) * nrm).sum(axis=1)
    body_exc = np.asarray(body_field, dtype=np.float64)[idx]
    margin = body_exc - gexc - standoff
    margin = np.where(dist <= max_body_dist, margin, -np.inf)
    return {"margin": margin, "standoff": standoff, "body_exc": body_exc,
            "garment_exc": gexc, "dist": dist}


def summarize(pred, *, clip_eps: float = 0.0, underweight_frac: float = 0.5):
    """Roll a predict_clip() result into a per-armor report, SEPARATING the two
    kinds of flagged vert (margin > clip_eps) -- this distinction is the whole
    point, because they need different fixes:

      n_static  standoff < 0: the vert is ALREADY inside the body at rest. This
                is a STATIC clip (the anti-poke's job) -- or, in concave zones
                (cleavage/armpit/crotch), a nearest-normal artifact and inner-
                layer geometry. NOT a jiggle problem. Reported, not the headline.
      n_jiggle  standoff >= 0: clear at rest, clipped only when the body jiggles
                out past it. This is the real motion-clip target. Its CAUSE:
                  under-weight   garment_exc < underweight_frac * body_exc
                                 -> the garment isn't following jiggle; fix the
                                 weight transfer (rigid plate lands here too --
                                 it is SUPPOSED not to jiggle, so its remedy is
                                 clearance/collision, not weighting).
                  standoff-tight it DOES follow but sits too close -> clearance.
    `cause` reflects the JIGGLE bucket (static-only if no jiggle verts).
    """
    margin = np.asarray(pred["margin"], dtype=np.float64)
    standoff = np.asarray(pred["standoff"], dtype=np.float64)
    finite = np.isfinite(margin)
    risk = finite & (margin > clip_eps)
    n_eval = int(finite.sum())
    n_clip = int(risk.sum())
    out = {"n_eval": n_eval, "n_clip": n_clip, "n_static": 0, "n_jiggle": 0,
           "clip_frac": (n_clip / n_eval) if n_eval else 0.0,
           "jiggle_frac": 0.0, "cause": "none"}
    if n_clip == 0:
        return out
    static = risk & (standoff < 0.0)
    jig = risk & (standoff >= 0.0)
    out["n_static"] = int(static.sum())
    out["n_jiggle"] = int(jig.sum())
    out["jiggle_frac"] = (out["n_jiggle"] / n_eval) if n_eval else 0.0
    if not jig.any():
        out["cause"] = "static-only"
        return out
    body_exc = np.asarray(pred["body_exc"], dtype=np.float64)[jig]
    g_exc = np.asarray(pred["garment_exc"], dtype=np.float64)[jig]
    safe_body = np.where(body_exc > 1e-9, body_exc, 1e-9)
    underweight = g_exc < (underweight_frac * safe_body)
    out["max_jiggle_margin"] = float(margin[jig].max())
    out["frac_underweight"] = float(underweight.mean())
    out["cause"] = ("under-weight" if underweight.mean() >= 0.5
                    else "standoff-tight")
    return out


def calibrate_excursion(measured_apex_disp: float,
                        apex_body_weight: float = 1.0) -> float:
    """Back out a jiggle bone's peak excursion E from ONE in-game capture.

        E = measured peak apex displacement / body jiggle-weight at that apex

    Capture procedure (per zone -- breast is the usual one):
      1. With your reference jiggle config active, pose so the zone is at peak
         outward excursion vs its rest position (a jump/land or a bounce frame).
      2. Read the apex's outward displacement from rest in game units (a
         rest-vs-peak screenshot pair, or a mesh-overlay readout).
      3. The body's jiggle weight at the apex vertex is ~1.0; pass it if known.
    Returns E in game units to put in the excursion dict for predict_clip().
    """
    if apex_body_weight <= 0.0:
        return 0.0
    return float(measured_apex_disp) / float(apex_body_weight)
