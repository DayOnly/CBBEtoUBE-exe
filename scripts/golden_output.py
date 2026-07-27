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

"""DEVELOPMENT TOOL -- golden-output regression harness. Not part of the shipped exe.

WHY THIS EXISTS. The vertex passes push the mesh 2.27x further than it ends up moving,
and `inflate_armor_outward` / `conform_to_source_standoff` are near-exact opposites
(cos -0.949 over 35k verts). Consolidating that is worth doing and cannot be attempted
safely today, because nothing would notice a 0.2u regression across a fifth of the
mesh. This is that safety net.

    python scripts/golden_output.py capture      # record the baseline
    python scripts/golden_output.py check        # re-convert and diff
    python scripts/golden_output.py check --tol 0.001

`check` exits non-zero on any regression, so it can gate a refactor.

WHAT IT COMPARES, per shape: vertex positions (max/mean displacement, not just a
hash -- a 1e-7 float wobble must read differently from a 0.2u shift), the bone list,
and per-bone weight totals.

TWO DISTINCTIONS IT IS BUILT TO MAKE
  * CODE change vs INPUT change. Source NIFs are hashed into the manifest. If a mod
    updated, the diff says so instead of blaming the converter.
  * FLAGS. Output depends on them, so the manifest records the CBBE2UBE_* environment
    and `check` refuses to compare across a different flag set.

THE PIECE SET. `_DEFAULT_PIECES` is base-game only, so this file stays mod-agnostic.
Drop a `golden/pieces.json` (gitignored) to point it at your own inventory -- worth
doing, because soft-body, layered-cloth and multi-shape classes only exist in
third-party outfits and a base-game-only run does not cover them.

TWO TRAPS THIS HARNESS AVOIDS, both found the hard way on 2026-07-27:
  * biped slots. `convert_one_armor.py` resolves slots from the mod's ESP, and a
    BodySlide-output mod has none -> slots=0 -> `clear_armor_outside_body` (gated on
    slot 32/49) silently never runs. Every piece below carries an EXPLICIT slot mask.
  * the `meshes/` ancestor. Physics-XML resolution walks up to a directory literally
    named `meshes`; without one, `_hdt_collider_shape_names` returns an empty set and
    every collider protection quietly no-ops. Output is written to
    `<out>/meshes/<subdir>/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import numpy as np                                          # noqa: E402
from pyn import pynifly                                     # noqa: E402
from src import nif_convert as nc, paths, auto_convert as ac  # noqa: E402

GOLDEN = _REPO / "golden"
SLOT_BODY = nc.BIPED_SLOT32_BIT
SLOT_HANDS = nc.BIPED_SLOT33_BIT if hasattr(nc, "BIPED_SLOT33_BIT") else 1 << 3
SLOT_FEET = nc.BIPED_SLOT37_BIT if hasattr(nc, "BIPED_SLOT37_BIT") else 1 << 7

# (label, mesh subdir under meshes/, stem, biped slot mask, why it is in the set)
#
# The set below is BASE-GAME ONLY, so this file stays mod-agnostic (the repo is
# public). A base-game-only set cannot cover every class that behaves differently --
# soft-body, layered cloth, and multi-shape mashups come from third-party outfits --
# so point the harness at your own inventory with `golden/pieces.json`, which is
# gitignored for the same reason `golden/` is:
#
#     [["label", "mesh/subdir", "stem", 4, "why this piece is in the set"], ...]
#
# Slot masks are ints: use the SLOT_BODY / SLOT_HANDS / SLOT_FEET values above. A
# piece whose source is not installed is skipped with a notice, so a partial set is
# fine -- the harness compares what it can resolve.
_DEFAULT_PIECES = [
    ("chainweld-studded", "armor/studded/female",  "body",            SLOT_BODY,
     "cuirass welded to its own physics skirt (#chain-welded-torso)"),
    ("plain-glass",       "armor/glass/f",         "cuirass",         SLOT_BODY,
     "ordinary rigid cuirass -- the common case"),
    ("hide-collider",     "armor/hide/f",          "cuirasslight",    SLOT_BODY,
     "vanilla cuirass that IS its own bust collider"),
    ("robes-thalmor",     "clothes/thalmor",       "thalmorrobesf",   SLOT_BODY,
     "robe with NO physics XML -- the C1 class the name skip protects"),
    ("gloves",            "armor/studded/female",  "gloves",          SLOT_HANDS,
     "hands: the fine-animation branch that skips most passes"),
    ("boots",             "armor/studded/female",  "boots",           SLOT_FEET,
     "feet: same branch, different mask"),
]

_PIECES_JSON = GOLDEN / "pieces.json"


def _load_pieces():
    """Local inventory if the user supplied one, else the base-game default set."""
    try:
        raw = json.loads(_PIECES_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return list(_DEFAULT_PIECES)
    except (OSError, ValueError) as exc:
        # Loud, not silent: falling back would quietly shrink the covered classes and
        # a passing `check` would then mean far less than it appears to.
        raise SystemExit(f"{_PIECES_JSON} is unreadable ({exc}) -- fix or remove it.")
    out = []
    for i, row in enumerate(raw):
        if len(row) != 5:
            raise SystemExit(
                f"{_PIECES_JSON} entry {i} has {len(row)} field(s), expected 5: "
                "[label, subdir, stem, slot_mask_int, why]")
        out.append((row[0], row[1], row[2], int(row[3]), row[4]))
    return out


PIECES = _load_pieces()


def _mods_root():
    return paths.discover_layout().mods_root


def _resolve_source(sub: str, stem: str):
    """LAST enabled mod providing meshes/<sub>/<stem>_1.nif, as the VFS resolves it."""
    lay = paths.discover_layout()
    en = paths.enabled_mods(lay)
    hit = None
    for d in sorted(_mods_root().iterdir()):
        if not d.is_dir() or d.name == "CBBEtoUBE Auto":
            continue
        if en is not None and d.name not in en:
            continue
        if (d / "meshes" / sub.replace("/", os.sep) / f"{stem}_1.nif").is_file():
            hit = d
    return hit


def _file_sig(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _flags() -> dict:
    skip = ("MO2_INI", "MODS_ROOT", "GAME_DATA", "CONFIG", "OUT_MOD", "NO_PAUSE")
    return {k: v for k, v in sorted(os.environ.items())
            if k.startswith("CBBE2UBE_") and str(v).strip()
            and not any(s in k for s in skip)}


def _convert(sub: str, stem: str, slots: int, out_root: Path):
    mod = _resolve_source(sub, stem)
    if mod is None:
        return None, None
    src = mod / "meshes" / sub.replace("/", os.sep) / f"{stem}_1.nif"
    dst_dir = out_root / "meshes" / sub.replace("/", os.sep)   # the meshes/ ancestor
    dst_dir.mkdir(parents=True, exist_ok=True)
    ref = str(ac._find_ube_body_ref())
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        nc.convert_nif(str(src), str(dst_dir / f"{stem}_1.nif"),
                       ube_body_ref_path=ref, biped_slots=slots)
    return src, dst_dir / f"{stem}_1.nif"


def _fingerprint(nif_path: Path) -> dict:
    """Per-shape verts + a weight fingerprint, in a form that supports MAGNITUDE diffs."""
    nf = pynifly.NifFile(filepath=str(nif_path))
    shapes = {}
    for s in nf.shapes:
        try:
            v = np.asarray(s.verts, np.float32)
        except Exception:
            continue
        bones = sorted(s.bone_names or [])
        wsum = []
        for b in bones:
            try:
                wsum.append(float(sum(w for _i, w in s.bone_weights[b])))
            except Exception:
                wsum.append(float("nan"))
        shapes[s.name] = {"verts": v, "bones": bones,
                          "wsum": np.asarray(wsum, np.float64)}
    return shapes


def capture() -> int:
    GOLDEN.mkdir(exist_ok=True)
    man = {"flags": _flags(), "pieces": {}}
    try:
        man["converter_version"] = (
            (_REPO / "src" / "version.py").read_text(encoding="utf-8")
            .split('__version__ = "')[1].split('"')[0])
    except Exception:
        man["converter_version"] = "?"
    work = GOLDEN / "_work"
    ok = 0
    for label, sub, stem, slots, why in PIECES:
        src, dst = _convert(sub, stem, slots, work / label)
        if dst is None or not dst.is_file():
            print(f"  SKIP {label:<20} (source not found: {sub}/{stem})")
            continue
        fp = _fingerprint(dst)
        np.savez_compressed(
            GOLDEN / f"{label}.npz",
            **{f"{n}::verts": d["verts"] for n, d in fp.items()},
            **{f"{n}::wsum": d["wsum"] for n, d in fp.items()},
            **{f"{n}::bones": np.array(d["bones"], dtype=object)
               for n, d in fp.items()})
        man["pieces"][label] = {
            "subdir": sub, "stem": stem, "slots": slots, "why": why,
            "source_mod": src.parts[src.parts.index("mods") + 1]
            if "mods" in src.parts else "?",
            "source_sig": _file_sig(src),
            "shapes": {n: int(len(d["verts"])) for n, d in fp.items()},
        }
        ok += 1
        print(f"  captured {label:<20} {len(fp)} shape(s), "
              f"{sum(len(d['verts']) for d in fp.values())} verts")
    (GOLDEN / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    # The converted NIFs are scratch -- only the fingerprints are the baseline.
    # Keeping them cost 105 MB per run.
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nbaseline: {ok}/{len(PIECES)} piece(s) -> {GOLDEN}")
    print(f"flags recorded: {man['flags'] or 'none'}")
    return 0 if ok else 1


def check(tol: float) -> int:
    if not (GOLDEN / "manifest.json").is_file():
        print("no baseline -- run `capture` first")
        return 2
    man = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    now = _flags()
    if now != man["flags"]:
        print("FLAG SET DIFFERS from the baseline -- output is not comparable.")
        print(f"  baseline: {man['flags']}")
        print(f"  now     : {now}")
        return 2
    work = GOLDEN / "_check"
    bad = src_changed = 0
    for label, sub, stem, slots, _why in PIECES:
        rec = man["pieces"].get(label)
        gold = GOLDEN / f"{label}.npz"
        if rec is None or not gold.is_file():
            continue
        src, dst = _convert(sub, stem, slots, work / label)
        if dst is None or not dst.is_file():
            print(f"  {label:<20} FAIL  conversion produced nothing")
            bad += 1
            continue
        if _file_sig(src) != rec["source_sig"]:
            print(f"  {label:<20} SOURCE CHANGED -- the input mesh differs, "
                  f"not the converter. Re-capture to re-baseline.")
            src_changed += 1
            continue
        g = np.load(gold, allow_pickle=True)
        cur = _fingerprint(dst)
        names = sorted({k.split("::")[0] for k in g.files})
        worst = []
        for n in names:
            if n not in cur:
                worst.append(f"shape MISSING: {n}")
                continue
            gv, cv = g[f"{n}::verts"], cur[n]["verts"]
            if gv.shape != cv.shape:
                worst.append(f"{n}: vert count {gv.shape[0]} -> {cv.shape[0]}")
                continue
            d = np.linalg.norm(cv.astype(np.float64) - gv.astype(np.float64), axis=1)
            if d.max() > tol:
                worst.append(f"{n}: verts moved  max={d.max():.4f}u "
                             f"mean={d[d>tol].mean():.4f}u  n={(d>tol).sum()}")
            gb = [str(x) for x in g[f"{n}::bones"]]
            if gb != cur[n]["bones"]:
                add = sorted(set(cur[n]["bones"]) - set(gb))
                rem = sorted(set(gb) - set(cur[n]["bones"]))
                worst.append(f"{n}: bones +{add} -{rem}")
            elif len(g[f"{n}::wsum"]) == len(cur[n]["wsum"]):
                wd = np.abs(cur[n]["wsum"] - g[f"{n}::wsum"])
                if np.nanmax(wd) > 1e-3:
                    i = int(np.nanargmax(wd))
                    worst.append(f"{n}: weight total on '{gb[i]}' "
                                 f"changed by {wd[i]:.4f}")
        if worst:
            bad += 1
            print(f"  {label:<20} REGRESSION")
            for w in worst[:6]:
                print(f"      {w}")
            if len(worst) > 6:
                print(f"      ... and {len(worst)-6} more")
        else:
            print(f"  {label:<20} ok")
    shutil.rmtree(work, ignore_errors=True)      # scratch NIFs, ~105 MB per run
    print()
    if src_changed:
        print(f"{src_changed} piece(s) had a CHANGED SOURCE mesh -- not a code "
              f"regression; re-run `capture` to re-baseline those.")
    if bad:
        print(f"FAIL: {bad} piece(s) regressed (tolerance {tol}u)")
        return 1
    print(f"PASS: output identical to the baseline (tolerance {tol}u)")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    mode = argv[0] if argv else ""
    tol = float(argv[argv.index("--tol") + 1]) if "--tol" in argv else 1e-4
    if mode == "capture":
        return capture()
    if mode == "check":
        return check(tol)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
