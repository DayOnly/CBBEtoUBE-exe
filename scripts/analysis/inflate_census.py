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

"""PACK CENSUS: does `inflate_armor_outward` still earn its place?

On one five-layer top, removing it measured neutral-to-better on every number
including the morph clip it exists for. That is ONE PIECE, and inflate is a
pack-wide safety pass, so this runs the same A/B over the whole source
population before any default moves.

BOTH ARMS ARE PRODUCED THE SAME WAY -- the real `convert` subcommand, same env,
differing only in `CBBE2UBE_INFLATION_MAGNITUDE`. Comparing a batch output
against a single-mesh conversion has already produced one false regression here
(1650 vs 1966 read as damage that did not exist).

TARGET (what removing it should improve)
    edge deviation vs the author, LENGTH-weighted   UNIFORMITY, not size
    dihedral, area-weighted                         roughness

    READ `edge_dev` CORRECTLY. It is the length-weighted mean |ratio - MEDIAN
    ratio|, so it scores how UNEVENLY the garment was stretched, not how far
    it ended up from the author. A garment scaled uniformly by 1.5x scores
    ZERO -- perfect on this column while being half again too big. It was
    described here as "fidelity to the original", which it is not, and for a
    census of INFLATE that gap matters: making things bigger is the pass's
    job, so its most obvious failure mode is invisible in the target column
    and only the standoff counters below can see it. Uneven stretch is what
    smears a texture, so the metric is right for warp-field-style damage and
    wrong for uniform growth.

COUNTER (what it could destroy -- inflate exists for headroom, so these decide)
    standoff p10 / median                           how much room is left
    verts INSIDE the body                           bind clipping
    verts within 0.1u of the body                   about to clip under morph

    ALL THREE COUNTERS ARE NEAREST-BODY-VERTEX measurements: the signed
    distance from a garment vertex to the closest body VERTEX, along that
    vertex's normal. That is the same staircase the warp field was moved off,
    and it misreads across a concave crease or a coarsely tessellated region,
    so these numbers are NOT sound as absolute clearances. They are used here
    only in a PAIRED A/B where both arms are measured identically and the bias
    cancels. Quoting one of them on its own -- "this garment has N verts
    inside the body" -- is a claim this census cannot support; use the ray
    cone in `mesh_penetration.py` for that.

The counters are the point. A pass whose removal improves fidelity while
quietly halving the clearance the garment needs when the body morphs at runtime
is a regression that every bind-pose number will call a win.

POPULATION DISCIPLINE. Every exclusion is counted and printed. A shrinking
denominator is not a clean result, and 0/0 is not a pass.

NO RESOLUTION FLOOR IS REPORTED, AND ONE IS NEEDED BEFORE ANY VERDICT.
This census prints medians of per-shape deltas without ever stating how large
a delta has to be to mean anything. The pipeline is known to move hundreds of
vertices by ~0.25u on an ALGEBRAIC NO-OP, so a small median difference between
arms can be entirely float noise. `mesh_penetration.noise_floor` exists for
exactly this and is used by `bust_verdict` and `underbust_census` -- not here,
because these columns are edge/dihedral/standoff rather than ray exposure.

The equivalent control for this harness is `morphtri_gate_ab`'s: convert the
SAME arm twice and score it against itself. Whatever spread that produces is
the floor, and any between-arm delta below it is not a result. Run it on a
handful of mods before reading a verdict off this census.

One mod is converted, scored and DELETED before the next, so disk stays bounded.
Writes incremental JSON so a partial run is still reportable with its exact
denominator.

    python scripts/analysis/inflate_census.py --population pop.json
        --out census.json [--limit N] [--workers N]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                      # noqa: E402
from scipy.spatial import cKDTree                       # noqa: E402
from pyn import pynifly                                 # noqa: E402

BODY_NAMES = {"baseshape", "3ba", "cbbe", "femalebody", "body", "ubebody"}

# The flags the deployed build runs with. Both arms get these; only the
# inflation magnitude differs, or the census is measuring something else.
# Inherits the caller's MO2 layout (CBBE2UBE_MO2_INI / CBBE2UBE_MODS_ROOT) --
# no path is baked in, so this runs against any instance.
BASE_ENV = {
    # Telemetry OFF. Applied to BOTH arms, so it cannot bias the comparison,
    # and VERIFIED not to change geometry: same flags twice moved 0 verts
    # (max 0.000000u), and audit-off vs audit-on also moved 0 verts over 56
    # shapes. The NIF hashes DO differ either way -- the writer is
    # nondeterministic in non-geometry bytes -- so this had to be checked on
    # vertex data with a same-flags control, not on file hashes.
    "CBBE2UBE_NO_STANDOFF_AUDIT": "1",
    "CBBE2UBE_STRAP_SCALE_UNIFORM": "1",
    "CBBE2UBE_SHORT_EDGE_CAP": "1",
    "CBBE2UBE_LAYER_RIDE_BARY": "1",
    "CBBE2UBE_SURFACE_WARP_FIELD": "1",
    "CBBE2UBE_FAMILY_WEIGHT_INVARIANT": "1",
    "CBBE2UBE_LAYER_ORDER_LAST": "1",
    "CBBE2UBE_SRC_NORMAL_FIX": "1",
}


def _load(p):
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        v = np.asarray(s.verts, float)
        t = s.transform
        R = np.asarray(t.rotation, float).reshape(3, 3)
        M = R * float(getattr(t, "scale", 1.0))
        if abs(np.linalg.det(M)) > 1e-9:
            v = v @ M.T + np.asarray(t.translation, float)
        out[s.name] = (v, np.asarray(s.tris).reshape(-1, 3))
    return out


def _vnorm(v, t):
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    fn = n / np.maximum(a[:, None], 1e-12)
    acc = np.zeros_like(v)
    for k in range(3):
        np.add.at(acc, t[:, k], fn * a[:, None])
    return acc / np.maximum(np.linalg.norm(acc, axis=1)[:, None], 1e-12)


def _dihedral(v, t):
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    a = np.linalg.norm(n, axis=1)
    fn = n / np.maximum(a[:, None], 1e-12)
    ar = 0.5 * a
    e2t = defaultdict(list)
    for ti, (x, y, z) in enumerate(t):
        for e in ((x, y), (y, z), (x, z)):
            e2t[(min(e), max(e))].append(ti)
    p = [q for q in e2t.values() if len(q) == 2]
    if not p:
        return None
    p = np.asarray(p)
    i, j = p[:, 0], p[:, 1]
    ang = np.degrees(np.arccos(np.clip(np.einsum('ij,ij->i', fn[i], fn[j]),
                                       -1, 1)))
    w = ar[i] + ar[j]
    return float((ang * w).sum() / max(w.sum(), 1e-9))


def _edge_dev(sv, ov, t):
    e = np.unique(np.sort(np.vstack(
        [t[:, [0, 1]], t[:, [1, 2]], t[:, [0, 2]]]), axis=1), axis=0)
    ls = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
    lo = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
    m = ls > 1e-6
    if not m.any():
        return None
    r, w = lo[m] / ls[m], ls[m]
    return float((w * np.abs(r - np.median(r))).sum() / w.sum())


_REF = {}
_SRCIDX = {}


def source_index():
    """meshes-relative path -> the WINNING source NIF on disk.

    The source mesh is NOT reliably a loose file inside the source mod: the
    converter resolves it through the VFS, so a mod whose meshes come from a
    BodySlide output (or from a BSA) has none of them in its own folder.
    Assuming otherwise silently dropped the FIDELITY half of this census --
    `edge deviation: no data` across an entire 394-NIF mod -- while the
    clearance half looked complete. Use the converter's own resolver.
    """
    if "m" not in _SRCIDX:
        from src import discovery, paths
        lay = paths.discover_layout()
        paths.export_to_env(lay)
        prof = Path(lay.instance_dir) / "profiles" / lay.selected_profile
        idx = {}
        for w in discovery.find_winning_nifs(
                Path(paths.mods_root()), prof, skip_mods=("CBBEtoUBE Auto",),
                classify=False, path_prefixes=("meshes\\",)):
            idx[str(w.relative_path).lower()] = Path(w.source_path)
        _SRCIDX["m"] = idx
        print(f"  source index: {len(idx)} winning NIFs", flush=True)
    return _SRCIDX["m"]


def cbbe_reference():
    """The CBBE base body -- the body a garment was authored against when its
    own NIF carries no inline body (every phase-1 piece). Without it the
    AUTHORED OFFSET, which is the whole point of `#authored-inflate`, could only
    be computed for phase 2."""
    if "c" not in _REF:
        from src import nif_convert as nc, paths
        lay = paths.discover_layout()
        paths.export_to_env(lay)
        p = nc._find_cbbe_base_body("_1")
        if p is None:
            raise RuntimeError("no CBBE base body found")
        m = _load(p)
        k = max(m, key=lambda k: len(m[k][0]))
        _REF["c"] = m[k]
    return _REF["c"]


def ube_reference():
    """The UBE body the converter itself fits to.

    PHASE 1 pieces carry no injected `BaseShape`, so an in-file body is not a
    basis for them -- and phase 1 is not a rounding error (one shipped pack:
    201 of 311 physics candidates). Excluding them would drop most of the
    population and quietly change what the census is about, since `inflate`
    runs on the phase-1 chain too. Measure them against the same reference the
    converter used."""
    if "b" not in _REF:
        # The census process needs the layout too, not just the subprocesses it
        # spawns -- without it discovery returns None and every phase-1 piece
        # lands in the exclusion ledger for the wrong reason.
        from src import nif_convert as nc, auto_convert as ac, paths
        lay = paths.discover_layout()
        paths.export_to_env(lay)
        p = ac._find_ube_body_ref() or nc._find_ube_femalebody("_1")
        if p is None:
            raise RuntimeError("no UBE body reference found")
        m = _load(p)
        k = max(m, key=lambda k: len(m[k][0]))
        _REF["b"] = m[k]
    return _REF["b"]


def score_nif(out_path, src_path):
    """Per-NIF metrics, or a reason string for the exclusion ledger."""
    try:
        out = _load(out_path)
    except Exception as e:
        return None, f"output unreadable: {type(e).__name__}"
    body = next((k for k in out if k.lower() in BODY_NAMES), None)
    if body is not None:
        bv, bt = out[body]
        phase = 2
    else:
        try:
            bv, bt = ube_reference()
        except Exception as e:
            return None, f"no body basis: {e!r}"
        phase = 1
    bn, tree = _vnorm(bv, bt), cKDTree(bv)
    src = {}
    if src_path and Path(src_path).is_file():
        try:
            src = _load(src_path)
        except Exception:
            src = {}
    # The body the AUTHOR fitted to: their own inline body when the source NIF
    # ships one, else the CBBE base. Needed for the authored-offset error --
    # the metric `#authored-inflate` actually targets. Edge deviation cannot
    # see it: a uniform outward offset barely changes any edge RATIO, so a
    # census without this reads a real change as "no fidelity effect".
    sbody = None
    if src:
        k = next((k for k in src if k.lower() in BODY_NAMES), None)
        try:
            sv_, st_ = src[k] if k else cbbe_reference()
            sbody = (sv_, _vnorm(sv_, st_), cKDTree(sv_))
        except Exception:
            sbody = None

    rows = []
    for name, (v, t) in out.items():
        if name.lower() in BODY_NAMES or len(v) < 12 or not len(t):
            continue
        _, i = tree.query(v, workers=-1)
        s = np.einsum('ij,ij->i', v - bv[i], bn[i])
        row = {
            "shape": name, "verts": int(len(v)),
            "standoff_p10": float(np.percentile(s, 10)),
            "standoff_p50": float(np.percentile(s, 50)),
            "inside": int((s < -0.05).sum()),
            "grazing": int((s < 0.10).sum()),
            "dihedral": _dihedral(v, t),
            "phase": phase,
        }
        sv = src.get(name, (None,))[0]
        if sv is not None and sv.shape == v.shape:
            row["edge_dev"] = _edge_dev(sv, v, t)
            if sbody is not None:
                bv2, bn2, tr2 = sbody
                _, j = tr2.query(sv, workers=-1)
                authored = np.einsum('ij,ij->i', sv - bv2[j], bn2[j])
                row["auth_err"] = float(np.median(np.abs(s - authored)))
        rows.append(row)
    if not rows:
        return None, "no garment shapes"
    return rows, None


def convert(mod_dir, out_dir, arm_env, workers):
    """`arm_env` is the ONLY difference between the arms. Everything else comes
    from BASE_ENV, so the census cannot accidentally measure two changes at
    once.

    Returns (returncode, broken). A ZERO EXIT IS NOT SUCCESS: the converter
    reports `errors during shape copy` for a shape it failed to build and
    still exits 0, so that arm holds a broken mesh which scores like any
    other. Reading a broken pass as a legitimate measurement has cost this
    project three wrong verdicts. The output was already being captured here
    and then thrown away, so the signal was present and unread.
    """
    env = dict(os.environ)
    env.update(BASE_ENV)
    env.update(arm_env)
    cmd = [sys.executable, "-m", "src.auto_convert", "convert", str(mod_dir),
           "-o", str(out_dir), "--no-textures", "--no-auto-merge",
           "--workers", str(workers)]
    r = subprocess.run(cmd, cwd=str(_REPO), env=env,
                       capture_output=True, text=True, timeout=10800)
    blob = f"{r.stdout or ''}\n{r.stderr or ''}".lower()
    broken = blob.count("errors during shape copy")
    # A pool worker's prints can be discarded before reaching stdout, so a
    # traceback that surfaces only on stderr has to count as well.
    if "traceback (most recent call last)" in blob:
        broken += 1
    return r.returncode, broken


def main() -> int:
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    def arm(flag, default):
        """--arm-on / --arm-off as comma-separated KEY=VALUE."""
        raw = opt(flag)
        if not raw:
            return dict(default)
        out = {}
        for part in raw.split(","):
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
        return out

    arm_on = arm("--arm-on", {"CBBE2UBE_INFLATION_MAGNITUDE": "0.7"})
    arm_off = arm("--arm-off", {"CBBE2UBE_INFLATION_MAGNITUDE": "0"})
    print(f"ARM 'on' : {arm_on}")
    print(f"ARM 'off': {arm_off}")
    if arm_on == arm_off:
        print("the two arms are identical; nothing would be measured")
        return 2
    pop = json.loads(Path(opt("--population")).read_text())
    out_json = Path(opt("--out", "inflate_census.json"))
    limit = int(opt("--limit", "0") or 0)
    workers = int(opt("--workers", "6") or 6)
    if limit:
        pop = pop[:limit]

    scratch = Path(opt("--scratch") or tempfile.gettempdir()) / "inflate_census"
    scratch.mkdir(parents=True, exist_ok=True)

    results, excl = [], defaultdict(int)
    n_mods_ok = 0
    # RESUME. A census over 160 mods takes hours; without this, stopping to
    # change anything throws the finished work away, which is a strong
    # incentive to leave it running badly. Mods already present in the output
    # are skipped and their rows carried forward.
    pop_total = len(pop)          # BEFORE resume filters it, or the report
                                  # understates its own denominator
    resume = opt("--resume")
    if resume and Path(resume).is_file():
        prev = json.loads(Path(resume).read_text())
        results = prev.get("rows") or []
        for k, v in (prev.get("exclusions") or {}).items():
            excl[k] += v
        seen = {r["mod"] for r in results}
        n_mods_ok = len(seen)
        before = len(pop)
        pop = [m for m in pop if m["name"] not in seen]
        print(f"RESUME: {len(results)} shape-pairs from {n_mods_ok} mods kept; "
              f"{before - len(pop)} of {before} mods skipped, {len(pop)} to go")
        if prev.get("arm_on") and prev.get("arm_on") != arm_on:
            print(f"  REFUSING: the saved run used arms {prev.get('arm_on')} / "
                  f"{prev.get('arm_off')}; resuming with different arms would "
                  f"mix two experiments in one file.")
            return 2
    for mi, m in enumerate(pop, 1):
        mod = Path(m["dir"])
        a, b = scratch / "on", scratch / "off"
        for d in (a, b):
            shutil.rmtree(d, ignore_errors=True)
        print(f"[{mi}/{len(pop)}] {m['name']}  ({m['nifs']} NIFs)", flush=True)
        try:
            ra, bra = convert(mod, a, arm_on, workers)
            rb, brb = convert(mod, b, arm_off, workers)
        except subprocess.TimeoutExpired:
            excl["mod: conversion timed out"] += m["nifs"]
            continue
        if ra != 0 or rb != 0:
            excl[f"mod: convert exited {ra}/{rb}"] += m["nifs"]
            continue
        if bra or brb:
            # EXCLUDED, not scored. A shape the converter failed to build is
            # not a measurement of the flag under test, and if one arm breaks
            # more often than the other the bias runs in that arm's favour.
            excl["mod: errors during shape copy (exit was still 0)"] += \
                m["nifs"]
            print(f"    EXCLUDED: shape-copy errors on/off = {bra}/{brb}")
            continue
        seen = 0
        for pa in sorted(a.rglob("*.nif")):
            rel = pa.relative_to(a)
            pb = b / rel
            if not pb.is_file():
                excl["nif: missing from the OFF arm"] += 1
                continue
            # the winning source, by the same meshes-relative path
            srcp = None
            parts = rel.parts
            if "meshes" in parts:
                sub = Path(*parts[parts.index("meshes") + 1:])
                if sub.parts and sub.parts[0] == "!UBE":
                    sub = Path(*sub.parts[1:])
                key = str(Path("meshes") / sub).lower()
                srcp = source_index().get(key)
                if srcp is None:
                    cand = mod / "meshes" / sub
                    srcp = cand if cand.is_file() else None
                if srcp is None:
                    excl["nif: source mesh not resolvable (fidelity only)"] += 1
            ra_, why_a = score_nif(pa, srcp)
            rb_, why_b = score_nif(pb, srcp)
            if ra_ is None or rb_ is None:
                excl[f"nif: {why_a or why_b}"] += 1
                continue
            ba = {r["shape"]: r for r in rb_}
            for r in ra_:
                o = ba.get(r["shape"])
                if o is None or o["verts"] != r["verts"]:
                    excl["shape: absent or vert-count differs between arms"] += 1
                    continue
                results.append({"mod": m["name"], "nif": str(rel),
                                "on": r, "off": o})
                seen += 1
        n_mods_ok += 1
        print(f"    scored {seen} shape-pairs   (running total "
              f"{len(results)})", flush=True)
        out_json.write_text(json.dumps(
            {"mods_scored": n_mods_ok, "mods_total": pop_total,
             "arm_on": arm_on, "arm_off": arm_off,
             "exclusions": dict(excl), "rows": results}, indent=1))
        for d in (a, b):
            shutil.rmtree(d, ignore_errors=True)
    print(f"\nDONE. {n_mods_ok}/{pop_total} mods, {len(results)} shape-pairs")
    for k, v in sorted(excl.items(), key=lambda kv: -kv[1]):
        print(f"  EXCLUDED {v:6d}  {k}")

    # DID THE MANIPULATION TAKE EFFECT? The guard at the top only proves the
    # two arm DICTS differ, which a misspelled flag name passes: both arms
    # then run identical code, every pair matches, and the census reports "no
    # difference" as though it were a finding. That false negative is
    # indistinguishable from a real null result unless it is checked for --
    # and it is the exact mistake that shipped a wrong single-piece build
    # earlier in this project, caught only by a separate control.
    if results:
        same = sum(1 for r in results if r.get("on") == r.get("off"))
        pct = 100.0 * same / len(results)
        print(f"\nMANIPULATION CHECK: {same}/{len(results)} pairs identical "
              f"({pct:.1f}%)")
        if pct > 99.0:
            print("  ABORT-LEVEL: the arms are effectively the same mesh. "
                  "Either the flag is inert or it never reached the "
                  "converter (misspelled name, read at import, wrong "
                  "process). DO NOT read this as 'the flag makes no "
                  "difference'.")
            return 3
        print("  the flag reaches the mesh, so a null result would be real")
    else:
        print("\nNOTHING WAS SCORED -- 0/0 is not a clean result")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
