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

"""Does GATED actually mean LOST? The morph-TRI gate, settled on the artefact.

`morphtri_gate_census.py` counts shapes the gate EXCLUDES. That is an upper
bound -- each gated pass has preconditions of its own, so exclusion is not proof
the pass would have fired. This measures what actually changes.

SAME BUILD, ONE FLAG: `CBBE2UBE_MORPHTRI_LEG_GRAFT=1` restores the old
behaviour. Anything differing between the two outputs is exactly what the gate
took away. Conversion goes through `convert_one_armor.py`, so it is the batch
code path (#single-vs-batch-parity) and never `convert_nif` by hand.

CONTROLS:
  * POSITIVE (aborts) -- the piece the gate was built on MUST differ between
    arms. If it does not, the flag is not reaching the conversion and every
    "no change" is meaningless.
  * NONDETERMINISM (aborts) -- the SAME arm converted twice must be identical.
    The NIF writer is nondeterministic in other respects, so without this a
    weight-entry delta proves nothing.

RESULT 2026-08-05: 5 of 6 sample pieces lose the whole breast/butt/belly bone
set on their main garment shape (up to 31740 weight entries). Geometry never
moves -- the gate is skinning-only. The same-flag repeat was identical in every
field, so the deltas are entirely the flag.

Usage:
  python scripts/analysis/morphtri_gate_ab.py [<meshes-rel stem> ...]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from scripts.analysis import _census_common as C     # noqa: E402
import numpy as np                                   # noqa: E402
from pyn import pynifly                              # noqa: E402
from src import discovery                            # noqa: E402

_REPO = Path(__file__).resolve().parent.parent.parent
JIGGLE = ("breast", "butt", "belly")

# Vanilla / DLC pieces only, so the default sample names no third-party mod.
DEFAULT_SAMPLE = [
    (r"armor\orcish\cuirassf",               "POSITIVE CONTROL"),
    (r"clothes\farmclothes03\farmerrobef",   "butt+belly at risk"),
    (r"armor\bandit\body1f",                 "soft top directly over the bust"),
    (r"clothes\monk\monkrobes_f",            "robe"),
    (r"armor\dragonbone\dragonbonearmorf",   "heavy plate + collision shape"),
]


def load(p):
    nf = pynifly.NifFile(filepath=str(p))
    out = {}
    for s in nf.shapes:
        w = {}
        for bn, pairs in (getattr(s, "bone_weights", None) or {}).items():
            pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
            w[bn] = {int(i): float(x) for i, x in pl}
        out[s.name] = (np.asarray(s.verts, float), set(s.bone_names or []), w)
    return out


def convert(mods, w, stem, outdir, env_extra):
    sub = str(Path(*Path(str(w.relative_path)).parts[1:-1]))
    env = dict(os.environ)
    env.update(env_extra)
    r = subprocess.run([sys.executable, "scripts/convert_one_armor.py",
                        str(mods / w.provider_mod), sub, stem, str(outdir)],
                       cwd=str(_REPO), env=env, capture_output=True, text=True)
    return r.returncode, outdir / "meshes" / "!UBE" / sub, r.stdout + r.stderr


def diff(a, b):
    """(rows, total changed units). a = OLD arm, b = SHIPPED arm."""
    rows, total = [], 0
    for n in sorted(set(a) & set(b)):
        va, ba, wa = a[n]
        vb, bb, wb = b[n]
        lost_j = sorted(x for x in ba - bb
                        if any(k in x.lower() for k in JIGGLE))
        nvw = 0
        mx = 0.0
        for bn in set(wa) | set(wb):
            da, db = wa.get(bn, {}), wb.get(bn, {})
            for i in set(da) | set(db):
                d = abs(da.get(i, 0.0) - db.get(i, 0.0))
                if d > 1e-6:
                    nvw += 1
                    mx = max(mx, d)
        moved = (int((np.linalg.norm(va - vb, axis=1) > 1e-4).sum())
                 if va.shape == vb.shape else -1)
        total += max(moved, 0) + nvw + len(lost_j)
        rows.append((n, len(va), moved, nvw, mx, lost_j,
                     len(ba - bb) - len(lost_j), len(bb - ba)))
    return rows, total


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sample = ([(a, "user-supplied") for a in args] if args else DEFAULT_SAMPLE)
    mods, prof, ube = C.layout()
    tmp = Path(os.environ.get("CBBE2UBE_AB_DIR", _REPO / "_ab_morphtri"))
    wins = discovery.find_winning_nifs(mods, prof, skip_mods=(ube.parent.parent.name,),
                                       path_prefixes=("meshes\\",), classify=False)

    results = []
    for relstem, why in sample:
        stem = Path(relstem).name
        cands = [w for w in wins
                 if str(w.relative_path).lower().endswith(
                     (relstem + "_0.nif").lower())]
        if not cands:
            print(f"!! {relstem}: not in the winning set -- SKIPPED")
            continue
        w = cands[0]
        print(f"\n=== {relstem}   [{why}]")
        on = convert(mods, w, stem, tmp / "gateon", {})
        off = convert(mods, w, stem, tmp / "gateoff",
                      {"CBBE2UBE_MORPHTRI_LEG_GRAFT": "1"})
        if on[0] or off[0]:
            tail = (on[2] or off[2]).strip().splitlines()[-1:]
            print(f"    CONVERT FAILED {on[0]}/{off[0]}: {tail}")
            continue
        fn = f"{stem}_1.nif"
        rows, total = diff(load(off[1] / fn), load(on[1] / fn))
        for n, nv, moved, nvw, mx, lost_j, lost_o, gained in rows:
            flag = "  <== JIGGLE BONES LOST" if lost_j else ""
            print(f"    {n[:20]:20} v{nv:6} moved {moved:6} wchg {nvw:6} "
                  f"max {mx:.4f} | lost jiggle {lost_j} | lost other {lost_o} "
                  f"| gained {gained}{flag}")
        results.append((relstem, total, w, stem))

    print("\n" + "=" * 74)
    for relstem, total, _w, _s in results:
        print(f"  {relstem[:52]:52} total changed units: {total}")

    assert results, "NOTHING CONVERTED -- census is measuring nothing"
    ctrl = results[0]
    assert ctrl[1] > 0, (
        "POSITIVE CONTROL FAILED: the flag changed NOTHING on the control "
        "piece -- CBBE2UBE_MORPHTRI_LEG_GRAFT is not reaching the conversion, "
        "so every 'no change' above is meaningless")

    # Nondeterminism control: the same arm, twice, must be identical.
    rep = convert(mods, ctrl[2], ctrl[3], tmp / "repeat", {})
    assert not rep[0], "nondeterminism control failed to convert"
    fn = f"{ctrl[3]}_1.nif"
    _rows, drift = diff(load(tmp / "gateon" / "meshes" / "!UBE"
                             / str(Path(*Path(str(ctrl[2].relative_path)).parts[1:-1]))
                             / fn),
                        load(rep[1] / fn))
    assert drift == 0, (
        f"NONDETERMINISM CONTROL FAILED: the same arm converted twice differs "
        f"by {drift} units. Every delta reported above is suspect.")
    print(f"\ncontrols OK: flag moved {ctrl[1]} units on the control piece; "
          f"same-arm repeat drift = 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
