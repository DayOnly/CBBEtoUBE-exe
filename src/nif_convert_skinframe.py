"""Shape skin-frame reconciliation: skin space <-> world space.

Split out of nif_convert.py on 2026-09-01 (split step 2, a pure leaf: numpy
only). A shape stores its verts in SKIN space plus a `global_to_skin`
transform; most armour has the identity, some carries a real offset, and a
fit that compares skin-space verts against a world-space body matches the
wrong anatomy (the ebony cuirass class). Imported by name into nif_convert,
so `nc._verts_skin_to_world` and friends keep working for every caller,
script and test.
"""
from __future__ import annotations

import sys

import numpy as np

# ----- Shape skin-frame reconciliation --------------------------------------
# Shapes store verts in SKIN space with a global_to_skin (world->skin) transform.
# Most armor has an identity g2s (skin == world), but shapes with a translation
# (e.g. Ebony cuirass -64.7u Z) store verts far from their body anatomy. The fit
# pipeline must compare armor verts against the body in WORLD space, or warps
# match the wrong region. Fix: lift verts to world before warp/inflate/conform,
# lower back to skin for output. Identity-g2s shapes are a no-op.

def _g2s_is_identity(g2s, eps=1e-4) -> bool:
    try:
        t = g2s.translation
        if abs(t[0]) > eps or abs(t[1]) > eps or abs(t[2]) > eps:
            return False
        if abs(float(g2s.scale) - 1.0) > eps:
            return False
        R = g2s.rotation
        for i in range(3):
            for j in range(3):
                if abs(R[i][j] - (1.0 if i == j else 0.0)) > eps:
                    return False
        return True
    except Exception as _ge:
        # Unknown -> treat as identity (keeps current behavior), but SAY SO:
        # this predicate gates every skin-to-world lift, and a pynifly API
        # drift here silently reads every offset shape as identity -- the
        # warp-against-air / invisible-armor class (audit 2026-07-28). "No
        # effect" and "never measured" must not print the same nothing.
        print(f"  WARN: _g2s_is_identity could not read the transform "
              f"({_ge!r}) -- ASSUMING identity", file=sys.stderr)
        return True


def _shape_global_to_skin(shape):
    """Return the shape's global_to_skin TransformBuf, or None if unavailable."""
    try:
        return shape.global_to_skin
    except Exception:
        return None


def _verts_skin_to_world(verts_skin: np.ndarray, g2s) -> np.ndarray:
    """Inverse of global_to_skin: skin-space verts -> world. No-op if identity."""
    if g2s is None or _g2s_is_identity(g2s):
        return verts_skin
    R = np.asarray(g2s.rotation, np.float64)
    t = np.asarray(g2s.translation, np.float64)
    sc = float(g2s.scale) or 1.0
    return ((np.asarray(verts_skin, np.float64) - t) @ R) / sc


def _verts_world_to_skin(verts_world: np.ndarray, g2s) -> np.ndarray:
    """Apply global_to_skin: world verts -> skin space. No-op if identity."""
    if g2s is None or _g2s_is_identity(g2s):
        return verts_world
    R = np.asarray(g2s.rotation, np.float64)
    t = np.asarray(g2s.translation, np.float64)
    sc = float(g2s.scale) or 1.0
    return (np.asarray(verts_world, np.float64) @ R.T) * sc + t
