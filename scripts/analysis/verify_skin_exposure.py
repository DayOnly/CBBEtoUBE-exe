"""DEVELOPMENT TOOL -- not part of the shipped converter.

Lives in `scripts/`, which PyInstaller does not bundle, and has no GUI setting
or CLI surface in the exe. It exists to measure and verify the converter during
development; nothing in the user-facing tool depends on it.

Ray-based skin exposure: is a patch of BODY visible through the armour?

Marches from each body vertex along its own outward normal and asks whether any
armour triangle blocks it (Moller-Trumbore). Unambiguous by construction.

Why not the obvious alternatives -- both were tried and both gave wrong answers:

  * NEAREST-VERTEX DISTANCE says how far the closest armour VERTEX is. It cannot
    say inside or outside, and it ignores the surface between vertices.
  * SIGNED DISTANCE VIA THE NEAREST TRIANGLE'S NORMAL flips sign unpredictably on a
    cuirass, because a shell has front AND inner faces: a body vertex sitting safely
    inside the cup is near both, and whichever sample happens to be nearest decides
    the sign. It reported 135/1104 nipple verts "outside" the leather while a simple
    y-extent check showed the armour reaching 2.4u IN FRONT of the nipple.

Read-only. Usable at rest or on posed (bounced) meshes.
"""
import numpy as np


def ray_blocked(origins, dirs, V, tris, tmax=25.0):
    """True where the ray from origins[i] along dirs[i] hits any triangle."""
    a = V[tris[:, 0]]
    e1 = V[tris[:, 1]] - a
    e2 = V[tris[:, 2]] - a
    out = np.zeros(len(origins), bool)
    for i in range(len(origins)):
        d = dirs[i]
        p = np.cross(d, e2)                       # (n_tris, 3)
        det = np.einsum('ij,ij->i', e1, p)        # per-triangle determinant
        ok = np.abs(det) > 1e-9
        if not ok.any():
            continue
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        t0 = origins[i] - a
        u = np.einsum('ij,ij->i', t0, p) * inv
        q = np.cross(t0, e1)
        v = (q @ d) * inv
        t = np.einsum('ij,ij->i', e2, q) * inv
        hit = (ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
               & (t > 1e-4) & (t < tmax))
        out[i] = hit.any()
    return out


def exposed_fraction(VB, NB, idx, Vw, tris):
    """Fraction of body verts `idx` whose outward ray escapes the armour."""
    blocked = ray_blocked(VB[idx], NB[idx], Vw, tris)
    return float((~blocked).mean()), int((~blocked).sum())
