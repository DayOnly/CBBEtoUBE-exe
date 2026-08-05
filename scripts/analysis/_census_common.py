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

"""Shared floor for the pre-reconvert censuses.

ONE implementation of each shared concept, on purpose. Two predicates for one
concept drift apart and then disagree silently -- that has already cost this
project a wrong verdict (see the conform-skip note in the project docs), so the
coherence-collapse detector and the population rule live here and nowhere else.

Nothing in here hardcodes a path: the layout comes from the MO2 instance the
converter itself discovers.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict, deque
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                    # noqa: E402
from src import discovery, paths                      # noqa: E402

# Shape names that are a BODY, not a garment. Excluded from garment metrics.
BODY_SHAPE_NAMES = {"baseshape", "3ba", "cbbe", "femalebody", "body", "ubebody"}

# The validated crumple metric. Rotation alone fires on 53% of the pack; a
# per-triangle area filter hides the defect entirely (it is many small triangles
# summing to a visible patch). Judge a patch by its TOTAL area and by whether
# its normals went from COHERENT to SCATTERED.
MIN_PATCH_AREA = 4.0
ROT_DEG = 30.0
SRC_COHERENT = 0.70
OUT_SCATTERED = 0.30
THIN_EXTENT = 3.0


def layout():
    """(mods_root, profile_dir, out_mod_ube_root). Raises with a clear message
    rather than silently measuring an empty tree."""
    lay = paths.discover_layout()
    if not lay.mods_root or not lay.mods_root.is_dir():
        raise SystemExit(
            "no MO2 mods root found. Set CBBE2UBE_MO2_INI to the instance's "
            "ModOrganizer.ini, or run from inside the instance.")
    prof = None
    if lay.instance_dir and lay.selected_profile:
        prof = Path(lay.instance_dir) / "profiles" / lay.selected_profile
    if prof is None or not prof.is_dir():
        raise SystemExit("no MO2 profile directory found; cannot read the "
                         "priority order that decides file conflicts.")
    out_mod = os.environ.get("CBBE2UBE_OUT_MOD", "CBBEtoUBE Auto")
    return lay.mods_root, prof, lay.mods_root / out_mod / "meshes" / "!UBE"


def converted_population(mods_root, profile_dir, ube_out, *, wide=True):
    """The meshes that ACTUALLY converted: the converter's own discovery rule
    intersected with "an output NIF exists at the same meshes-relative path".

    Returns (population, n_discovered). The difference is an exclusion the
    caller MUST report -- male meshes under the female-only policy, filtered
    slots, coexistence skips, hard failures.

    The female-only policy is plugin-driven (ARMA model paths). Re-deriving it
    here would be a second detector for one concept, so the OUTPUT ARTEFACT is
    used as the authority instead.

    `wide` widens the path prefixes to all of `meshes\\`. The default prefixes
    cover only armor/clothes/DLC/CC, and a census that used them silently
    dropped most of its sample as "no winning source".
    """
    kw = {"path_prefixes": ("meshes\\",)} if wide else {}
    out_mod_name = ube_out.parent.parent.name
    found = discovery.find_winning_nifs(mods_root, profile_dir,
                                        skip_mods=(out_mod_name,),
                                        classify=False, **kw)
    keep = [w for w in found if (ube_out / meshes_rel(w.relative_path)).is_file()]
    return keep, len(found)


def meshes_rel(relative_path) -> Path:
    """Drop a leading `meshes\\` so the path can be joined onto the output root."""
    rel = Path(str(relative_path))
    parts = rel.parts
    return Path(*parts[1:]) if parts and parts[0].lower() == "meshes" else rel


def baked_verts(shape):
    """Verts in body space.

    Cross-file vertex comparison is VOID unless the frames are reconciled: some
    sources ship every shape at a large non-identity node scale, which the
    converter bakes. Comparing unbaked source against baked output invented a
    2808-area defect on a piece that is fine.

    NOTE `src.nif_io.load_nif` returns a light wrapper with NO `.transform` --
    the raw pynifly shape hangs off `_backing`. Reading it off the wrapper
    raises AttributeError on every shape, and a census that swallowed that into
    an exclusion counter reported a gate reaching zero shapes.
    """
    v = np.asarray(shape.verts, float)
    t = getattr(shape, "_backing", shape).transform
    R = np.asarray(t.rotation, float).reshape(3, 3)
    M = R * float(getattr(t, "scale", 1.0))
    if abs(np.linalg.det(M)) > 1e-9:
        v = v @ M.T + np.asarray(t.translation, float)
    return v


def face_normals(v, t):
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a[:, None], 1e-12), 0.5 * a


def coherence_patches(src_verts, out_verts, tris):
    """Qualifying coherence-collapse patches between a source and an output.

    Returns a list of dicts and the set of triangle indices in any patch.

    A legitimate refit turns every normal in a patch TOGETHER, so the
    area-weighted |mean normal| stays long; only a crumple scatters them.

    CAUTION: the SHIPPED repair is deliberately WIDER than this for thin strips
    -- it gates them on how far coherence FELL (`COHERENCE_THIN_DROP`) rather
    than on the absolute value here. So geometry the repair legitimately touches
    can be invisible to this detector. That is a different predicate, not
    over-fire.
    """
    st = np.asarray(tris).reshape(-1, 3)
    ns, As = face_normals(src_verts, st)
    no, Ao = face_normals(out_verts, st)
    rot = np.degrees(np.arccos(np.clip((ns * no).sum(axis=1), -1, 1)))
    tgt = np.where(rot > ROT_DEG)[0]
    found, tri_set = [], set()
    if not len(tgt):
        return found, tri_set
    e2t = defaultdict(list)
    for ti in tgt:
        a, b, c = st[ti]
        for e in ((a, b), (b, c), (a, c)):
            e2t[tuple(sorted(e))].append(ti)
    seen = set()
    for ti in tgt:
        if ti in seen:
            continue
        q, comp = deque([ti]), []
        seen.add(ti)
        while q:
            cur = q.popleft()
            comp.append(cur)
            a, b, c = st[cur]
            for e in ((a, b), (b, c), (a, c)):
                for nb in e2t[tuple(sorted(e))]:
                    if nb not in seen:
                        seen.add(nb)
                        q.append(nb)
        area = float(Ao[comp].sum())
        if area < MIN_PATCH_AREA:
            continue
        ws, wo = As[comp][:, None], Ao[comp][:, None]
        cs = float(np.linalg.norm((ns[comp] * ws).sum(0) / max(ws.sum(), 1e-9)))
        co = float(np.linalg.norm((no[comp] * wo).sum(0) / max(wo.sum(), 1e-9)))
        if not (cs >= SRC_COHERENT and co <= OUT_SCATTERED):
            continue
        pts = out_verts[st[comp].reshape(-1)]
        ext = pts.max(axis=0) - pts.min(axis=0)
        found.append({"tris": len(comp), "area": area,
                      "rot": float(rot[comp].mean()),
                      "thin": float(ext.min()), "cs": cs, "co": co})
        tri_set |= {int(x) for x in comp}
    return found, tri_set
