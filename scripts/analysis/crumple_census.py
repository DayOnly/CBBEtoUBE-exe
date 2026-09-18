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

"""PACK CENSUS: thin-rim crumple. Connected patches of appreciable area whose
triangle normals went from COHERENT in the source to SCATTERED in the output.

The metric lives in `_census_common.coherence_patches` -- one implementation,
shared with the repair A/B, because two predicates for one concept drift.

Three metrics were tried and the first two each produced a confident FALSE
result: per-edge dihedral flagged sliver triangles moving 0.5u (invisible in
game, and a whole pass-bisect was run against it), and per-triangle rotation
with an area filter HID the real defect, which is many small triangles summing
to a visible patch.

Writes a JSON baseline (hit list + clean list) for `coherence_repair_ab.py` to
sample from.

CONTROLS (both ABORT):
  * NEGATIVE -- the detector MUST fire on a known-bad mesh. Point `--probe` at
    one. If pieces were hand-deployed with the repair already applied, their
    live copies CANNOT crumple: pass the pre-repair backup via `--probe-file`,
    and count those pieces as an explicit exclusion rather than letting the
    denominator quietly shrink.
  * OVER-FIRE -- some pieces MUST come back clean.

Usage:
  python scripts/analysis/crumple_census.py [--out baseline.json]
      [--probe <substr>] [--probe-file <path to a known-bad .nif>]
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from scripts.analysis import _census_common as C     # noqa: E402
from pyn import pynifly                              # noqa: E402
import numpy as np                                   # noqa: E402


def shapes_of(p):
    """Verts baked to world space -- see `_census_common.baked_verts` for why."""
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        v = np.asarray(s.verts, float)
        t = s.transform
        R = np.asarray(t.rotation, float).reshape(3, 3)
        M = R * float(getattr(t, "scale", 1.0))
        if abs(np.linalg.det(M)) > 1e-9:
            v = v @ M.T + np.asarray(t.translation, float)
        out[s.name] = (v, np.asarray(s.tris))
    return out


def main() -> int:
    argv = sys.argv[1:]

    def opt(f, d=None):
        return argv[argv.index(f) + 1] if f in argv else d

    out_json = Path(opt("--out", "crumple_baseline.json"))
    probe_file = opt("--probe-file")

    mods, _prof, ube = C.layout()
    out_mod = ube.parent.parent

    print("indexing source meshes...", flush=True)
    index = defaultdict(list)
    for mod in sorted(d for d in mods.iterdir() if d.is_dir()):
        if mod == out_mod:
            continue
        mroot = mod / "meshes"
        if not mroot.is_dir():
            continue
        for dirpath, _dn, fnames in os.walk(mroot):
            for f in fnames:
                if f.lower().endswith(".nif"):
                    rel = os.path.relpath(os.path.join(dirpath, f),
                                          mroot).lower()
                    index[rel].append(Path(dirpath) / f)
    print(f"indexed {len(index)} distinct source rel-paths", flush=True)

    out_nifs = sorted(ube.rglob("*.nif"))
    print(f"population: {len(out_nifs)} output .nif", flush=True)

    no_source = unreadable = clean = 0
    hits = []
    for op in out_nifs:
        rel = str(op.relative_to(ube)).lower()
        cands = index.get(rel, [])
        if not cands:
            no_source += 1
            continue
        try:
            osh = shapes_of(op)
        except Exception:
            unreadable += 1
            continue
        best = None
        for c in cands:
            try:
                ssh = shapes_of(c)
            except Exception:
                continue
            shared = [n for n in osh if n in ssh
                      and n.casefold() not in C.BODY_SHAPE_NAMES
                      and len(ssh[n][0]) == len(osh[n][0])
                      and len(ssh[n][1]) == len(osh[n][1])]
            if best is None or len(shared) > len(best[1]):
                best = (ssh, shared)
        if best is None or not best[1]:
            no_source += 1
            continue
        ssh, shared = best
        found = []
        for name in shared:
            sv, st = ssh[name]
            pats, _tris = C.coherence_patches(sv, osh[name][0], st)
            for p in pats:
                found.append(dict(p, shape=name))
        if found:
            found.sort(key=lambda d: -d["area"])
            hits.append((str(op.relative_to(ube)), found))
        else:
            clean += 1

    print()
    print(f"  no usable source (excluded)  : {no_source}")
    print(f"  unreadable (excluded)        : {unreadable}")
    print(f"  clean                        : {clean}")
    print(f"  HAVE a crumpled patch        : {len(hits)}")
    print(f"  ---- accounted: "
          f"{no_source+unreadable+clean+len(hits)} / {len(out_nifs)}")

    hit_set = {h for h, _ in hits}
    clean_paths = [str(p.relative_to(ube)) for p in out_nifs
                   if str(p.relative_to(ube)) not in hit_set
                   and str(p.relative_to(ube)).lower() in index]
    # Data BEFORE assertions: a control failure must not destroy a long run.
    json.dump({"hits": [{"piece": h, "patches": f} for h, f in hits],
               "clean": clean_paths}, open(out_json, "w"), indent=1)
    print(f"\nwrote {out_json}: {len(hits)} hits, {len(clean_paths)} clean")

    thin = [f for _h, fs in hits for f in fs if f["thin"] < C.THIN_EXTENT]
    print(f"patches on THIN features (<{C.THIN_EXTENT}u): {len(thin)} of "
          f"{sum(len(f) for _h, f in hits)}")
    print("\nworst 25 pieces by patch area:")
    for h, fs in sorted(hits, key=lambda x: -x[1][0]["area"])[:25]:
        f = fs[0]
        print(f"  {h[:52]:52} {f['shape'][:13]:13} {f['tris']:4}t "
              f"a{f['area']:7.1f} rot{f['rot']:5.1f} thin{f['thin']:5.2f} "
              f"coh{f['cs']:4.2f}->{f['co']:4.2f}")

    assert clean > 0, "OVER-FIRE CONTROL FAILED: nothing came back clean"
    if probe_file:
        pf = Path(probe_file)
        assert pf.is_file(), f"CONTROL UNAVAILABLE: {pf} missing"
        # Resolve the source by the probe's FULL meshes-relative path, not its
        # basename. Matching on basename alone picks the first `cuirassf_1.nif`
        # in the index, which belongs to a DIFFERENT armour: the vert counts
        # then disagree, every shape is skipped, and the control reports the
        # detector as broken when the detector is fine. That false alarm cost a
        # real investigation on 2026-08-05 -- dozens of pieces share a filename.
        rel = pf.name
        for suf in (".pre-coh-bak", ".pre-keepjig-bak"):
            if rel.endswith(suf):
                rel = rel[:-len(suf)]
                break
        else:
            rel = rel.split(".nif")[0] + ".nif"
        try:
            key = str(pf.parent.relative_to(ube) / rel).lower()
        except ValueError:
            key = rel.lower()
        assert key in index, (
            f"CONTROL UNAVAILABLE: no source indexed for {key!r}. The probe "
            f"must sit under the output root so its meshes-relative path can "
            f"be derived.")
        bsh, ssh = shapes_of(pf), shapes_of(index[key][0])
        n_pat = 0
        for n in bsh:
            if n not in ssh or n.casefold() in C.BODY_SHAPE_NAMES:
                continue
            sv, st = ssh[n]
            if len(sv) != len(bsh[n][0]):
                continue
            pats, _ = C.coherence_patches(sv, bsh[n][0], st)
            n_pat += len(pats)
        assert n_pat > 0, (
            "NEGATIVE CONTROL FAILED: the detector finds NO crumple on the "
            "known-bad mesh -- it has stopped working, so every 'clean' above "
            "is vacuous")
        print(f"\ncontrols OK: detector finds {n_pat} patch(es) on the "
              f"known-bad control mesh; {clean} pieces clean")
    else:
        print(f"\nNOTE: no --probe-file given, so the NEGATIVE control did not "
              f"run. {clean} clean pieces only prove the detector is not "
              f"firing on everything; they do not prove it still fires at all.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
