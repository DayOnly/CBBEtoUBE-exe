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

"""A/B TWO BUILDS OF A PIECE under many body presets, and name the REGRESSIONS.

    python scripts/analysis/preset_ab_score.py --preset-dir <dir>
        LABEL=<off.nif>,<on.nif> [LABEL2=...] [--band bust] [--eps 0.05]
    python scripts/analysis/preset_ab_score.py --preset <p.xml> [--preset ...]
        LABEL=<off.nif>,<on.nif>

WHY A DEDICATED HARNESS. A fit change is easy to score on the piece and the
preset that motivated it, and this project has shipped that mistake: an
attempt looked like a clean win for exactly as long as it was scored on the one
pair it was built for. The question that decides a default flip is not "did the
target improve" but "did ANYTHING get worse", and that needs the same pair run
against presets nobody was thinking about.

WHAT COUNTS AS A REGRESSION, in priority order:
  1. a preset that was CLEAN (off <= 0.05%) and is not any more. This is the bar
     three earlier attempts on the morphed-bust class FAILED, and it is the one
     that matters -- pushing a preset that currently looks right toward clipping
     is not shippable however much it buys elsewhere.
  2. any preset where ON is worse by more than `--eps`.

STANDOFF IS PRINTED PER ARM AND IS PRESET-INDEPENDENT, because clipping has no
upper bound: a garment three units off the body scores 0.00%. `standoff_audit`'s
own header records that a bust probe tuned on clipping alone shipped
OVERINFLATED twice, because nothing in the harness could see it. Read the pair.

BOTH ARMS MUST COME FROM ONE HARNESS. Scoring a flag arm against the shipped
pack compares two BUILDS as well as two flags.

ASSERT THE ARM FIRED. An ON arm identical to its OFF arm is a broken arm until
proven otherwise -- a flag whose env var names a plain literal instead of a
`_flag()`/`_knob()` is silently inert, and reads exactly like "no effect".
"""
import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

from src import paths                                          # noqa: E402
try:
    paths.export_to_env(paths.discover_layout())
except Exception as _le:                                       # noqa: BLE001
    print(f"WARNING: could not resolve an MO2 layout ({_le}); set "
          f"CBBE2UBE_MO2_INI if the body OSD fails to resolve", file=sys.stderr)

import numpy as np                                             # noqa: E402
from scripts.analysis import standoff_audit as sa              # noqa: E402
from scripts.analysis import morph_clip_test as mct            # noqa: E402
import src.nif_convert as nc                                   # noqa: E402


def _dense(offsets, n):
    """Vectorised `morph_clip_test._sparse_to_dense`.

    The original loops in Python over every offset; called once per shape per
    slider that is millions of iterations on a many-shape piece and it dominates
    the run. `_selftest_dense` asserts EQUALITY with the original before any
    measuring starts -- a faster function that is not the SAME function is a
    different metric, and this aborts rather than report one.
    """
    d = np.zeros((n, 3), np.float64)
    a = np.asarray(offsets, dtype=np.float64)
    if a.size == 0:
        return d
    a = a.reshape(-1, 4)
    idx = a[:, 0].astype(np.int64)
    ok = (idx >= 0) & (idx < n)
    d[idx[ok]] = a[ok, 1:4]
    return d


def _selftest_dense() -> None:
    import random
    rng = random.Random(11)
    for n in (1, 23, 700):
        offs = [(rng.randrange(-2, n + 2), rng.random(), rng.random(),
                 rng.random()) for _ in range(max(1, n // 3))]
        if not np.array_equal(mct._sparse_to_dense(offs, n), _dense(offs, n)):
            raise SystemExit("ABORT: the vectorised densify disagrees with "
                             "morph_clip_test._sparse_to_dense")


class Piece:
    """One converted NIF, with its garment morphs densified ON DEMAND."""

    def __init__(self, path):
        self.path = Path(path)
        nf = nc._pynifly().NifFile(filepath=str(path))
        self.body = next((s for s in nf.shapes if s.name == "BaseShape"), None)
        if self.body is None:
            raise SystemExit(f"ABORT: {self.path.name} has no injected "
                             f"BaseShape (copy-path piece); there is no body "
                             f"in the file to morph against")
        self.bV = mct._world(self.body)
        self.bT = np.asarray(self.body.tris, np.int64).reshape(-1, 3)
        bN = np.asarray(self.body.normals, np.float64)
        self.bN = bN / np.clip(np.linalg.norm(bN, axis=1, keepdims=True),
                               1e-9, None)
        stem = self.path.stem
        for suf in ("_0", "_1"):
            if stem.endswith(suf):
                stem = stem[:-2]
        tri = self.path.parent / f"{stem}.tri"
        tshapes = {}
        if tri.is_file():
            try:
                from src.tri import TriFile
                tshapes = {sh.name: {m.name: m for m in sh.morphs}
                           for sh in TriFile.load(tri).shapes}
            except Exception:
                tshapes = {}
        self.parts, T, off = [], [], 0
        for s in nf.shapes:
            nm = s.name or ""
            if nm == "BaseShape" or nc._is_inline_body_name(nm):
                continue
            if int(getattr(s, "flags", 0) or 0) & 0x1:
                continue
            if not any(v for v in (s.textures or {}).values()):
                continue
            gV = mct._aligned(s, self.bV)
            self.parts.append((nm, gV, tshapes.get(nm, {}), {}))
            T.append(np.asarray(s.tris, np.int64).reshape(-1, 3) + off)
            off += len(gV)
        if not self.parts:
            raise SystemExit(f"ABORT: {self.path.name} has no rendered shape")
        self.Vb = np.concatenate([p[1] for p in self.parts])
        self.T = np.concatenate(T)

    def morphed(self, sel):
        out = []
        for _nm, gV, morphs, cache in self.parts:
            dG = np.zeros_like(gV)
            for k, val in sel.items():
                mk = cache.get(("m", k), "MISS")
                if mk == "MISS":
                    mk = mct._match(k, morphs.keys())
                    cache[("m", k)] = mk
                if mk is None:
                    continue
                arr = cache.get(mk)
                if arr is None:
                    arr = _dense(morphs[mk].offsets, len(gV))
                    cache[mk] = arr
                dG += arr * val
            out.append(gV + dG)
        return np.concatenate(out)


def main() -> int:
    _selftest_dense()
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset-dir", help="score every .xml/.jslot in here")
    ap.add_argument("--preset", action="append", default=[],
                    help="score this preset (repeatable)")
    ap.add_argument("--band", default="bust", choices=sorted(mct.BANDS))
    ap.add_argument("--eps", type=float, default=0.05,
                    help="a change smaller than this is not called either way")
    ap.add_argument("pairs", nargs="+", help="LABEL=<off.nif>,<on.nif>")
    a = ap.parse_args()

    presets = list(a.preset)
    if a.preset_dir:
        presets += [str(p) for p in sorted(Path(a.preset_dir).iterdir())
                    if p.suffix.lower() in (".xml", ".jslot")]
    if not presets:
        print("ABORT: no presets given (--preset or --preset-dir)",
              file=sys.stderr)
        return 3
    print(f"{len(presets)} preset(s), {len(a.pairs)} piece(s), "
          f"band {a.band}\n", flush=True)

    cache, rows, regressions, improvements = {}, [], [], []
    for spec in a.pairs:
        label, both = spec.split("=", 1)
        off_p, on_p = both.split(",", 1)
        off, on = Piece(off_p), Piece(on_p)
        bV, bT, bN = off.bV, off.bT, off.bN
        idx = np.flatnonzero(mct.BANDS[a.band](bV))
        if len(idx) < 20:
            print(f"{label}: SKIPPED, {a.band} band has {len(idx)} verts")
            continue
        so = {}
        for nm, pc in (("off", off), ("on", on)):
            t = sa.ClipTester(pc.Vb, pc.T, tmax=12.0).standoff(bV, bN, idx)
            so[nm] = ((float(np.median(t)), float(np.percentile(t, 90)))
                      if len(t) else (float("nan"), float("nan")))
        print(f"### {label}   standoff p50/p90  off {so['off'][0]:.3f}/"
              f"{so['off'][1]:.3f}   on {so['on'][0]:.3f}/{so['on'][1]:.3f}",
              flush=True)

        for pre in presets:
            key = (str(pre), len(bV))
            if key not in cache:
                bmk = ("bm", len(bV))
                if bmk not in cache:
                    mct._sparse_to_dense = _dense    # same result, vectorised
                    cache[bmk] = mct.body_morphs(len(bV))[0]
                bm = cache[bmk]
                sel, _m, _f = mct._resolve_preset(mct._load_preset(pre)[0], bm)
                dB = np.zeros_like(bV)
                for k in sel:
                    dB += bm[k] * sel[k]
                cache[key] = (dB, sel)
            dB, sel = cache[key]
            if np.linalg.norm(dB, axis=1).max() < 1e-4:
                continue                       # this preset moves nothing here
            bVm = bV + dB
            vaM = sa.vert_areas(bVm, bT)
            r = {nm: sa.ClipTester(pc.morphed(sel), pc.T).report(
                     bVm, bT, bN, idx, vaM, oriented=True)["clipping_pct"]
                 for nm, pc in (("off", off), ("on", on))}
            delta = r["on"] - r["off"]
            name = Path(pre).stem
            rows.append((label, name, r["off"], r["on"], delta))
            print(f"    {name[:38]:<40}{r['off']:>8.3f}{r['on']:>8.3f}"
                  f"{delta:>+9.3f}", flush=True)
            if delta > a.eps:
                regressions.append((label, name, r["off"], r["on"], delta,
                                    r["off"] <= 0.05))
            elif delta < -a.eps:
                improvements.append((label, name, r["off"], r["on"], delta))
        del off, on

    if not rows:
        print("ABORT: nothing was scored", file=sys.stderr)
        return 3
    print(f"\n{'=' * 78}\nscored {len(rows)} (piece, preset) arms")
    print(f"IMPROVED by >{a.eps}: {len(improvements)}")
    print(f"unchanged          : "
          f"{len(rows) - len(improvements) - len(regressions)}")
    print(f"REGRESSED by >{a.eps}: {len(regressions)}")
    broke = [x for x in regressions if x[5]]
    print(f"  of which BROKE A CLEAN PRESET: {len(broke)}   <- the bar")
    if regressions:
        print(f"\n{'piece':<16}{'preset':<34}{'off':>8}{'on':>8}{'delta':>9}")
        for lbl, pre, o, n, dl, clean in sorted(regressions,
                                                key=lambda x: -x[4]):
            print(f"{lbl:<16}{pre[:33]:<34}{o:>8.3f}{n:>8.3f}{dl:>+9.3f}"
                  f"{'  <<< WAS CLEAN' if clean else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
