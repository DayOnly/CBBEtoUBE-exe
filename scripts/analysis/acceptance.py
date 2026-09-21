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

"""THE GATE. Two arms in, one verdict out, exit code = the verdict.

    python -m scripts.analysis.acceptance <control_dir> <candidate_dir>
                            [--follow-top N] [--no-follow] [--no-clip]
                            [--repeat <control_repeat_dir> [--allow-noise]]

REPEAT CONTROL. `--repeat` names a SECOND arm of the control code. The NIFs the
two control arms disagree on are noise, not the change -- one physics-XML lookup
still reads the destination tree while the run is writing it (#hdt-xml-race),
and that has already put a coin flip into a real A/B. They are printed; the gate
refuses to judge them (exit 4) unless `--allow-noise` is given, and then removes
them from BOTH arms before every row is scored. Each arm's count of pieces whose
physics XML resolves by a cross-directory stem match is printed as info.
#repeat-control

Runs every scorer this project judges a change on, over the SAME two output
dirs, and prints one table: metric | control | candidate | verdict. A candidate
passes only if it is NO WORSE than the control on every row. The scorers'
own reports are printed in full above the table so nothing is hidden behind
the summary.

Rows and their rule (tolerance 0.005u on the float rows):

    folds / inverted            pack_census        candidate <= control
    TRI out of bounds           pack_census        candidate <= control
    BODYTRI on the body         pack_census        candidate >= control
    untagged cloth / parity     pack_census        candidate <= control
    tri names NO / SOME shape   pack_census        candidate <= control
      the NIF has                                  (DEAD SLIDERS: a tri that
                                                    names a shape the NIF does
                                                    not have morphs nothing)
    bust gap, PER PATH          bust_gap_score     |candidate| <= |control|
                                                   (CLOSER TO THE AUTHOR --
                                                    the gap is signed, `ours
                                                    - author`, so this row
                                                    judges fidelity and the
                                                    two rows below are its
                                                    clipping counter-metric)
    bust-band penetration       bust_gap_score     candidate <= control
    tip clearance p50 / p05     nipple_clearance   candidate >= control
    pieces tighter at the tip   nipple_clearance   0
    the four rows above, at     bust_gap_score,    the SAME rules, over the
      WEIGHT 0 (bust gap per    nipple_clearance   `_0` files, each against the
      path, bust-band pen,                         bodies weight 0 was built on.
      tip p50 / p05, pieces                        SKIPPED when the scorer did
      with less tip room)                          not measure weight 0 -- never
                                                   ok then, and never FAIL
    stretch rate p50 / p90      stretched_edges    candidate <= control
    edge deviation p50          stretched_edges    candidate <= control
      ^ shapes / garments,                         info (a garment is TWO
        pooled stretched edges                     shapes, and a pooled count
                                                   is a mesh-size ranking)
    zero-weight NEWLY EMPTIED   zero_weight_pair_ab  0  (the paired delta;
                                                     the absolute count is
                                                     reported, not gated)
    posed follow, worst delta   follow_bands       <= 0.010 median, on the
                                                   files whose weights moved
                                                   most (needs a skeleton NIF)
    morph clip > 0.05% (pieces) band_class_census  candidate <= control
    morph clip > 1.0%  (pieces) band_class_census  candidate <= control
    bind  clip > 0.05% (pieces) band_class_census  candidate <= control
    morph clip p50 / p90        band_class_census  candidate <= control
    bind  clip p90              band_class_census  candidate <= control
      ^ the counts are PREVALENCE (how many pieces clip) and the percentiles
        are MAGNITUDE (how much). A count cannot see a piece clipping LESS:
        5.316% -> 3.585% crosses no threshold and moves no count.
      ^ pieces scored in BOTH                      info (the denominator, plus a
        arms, per-arm scored,                      divergence warning and the
        biased-sample warning                      census's own bias flag)

THE ONLY MORPHED ROWS THIS GATE HAS. The first two are taken under a body
preset; the third is the BIND number from the same census run, kept beside them
so a change that trades bind for morph shows both. Everything above them reads the
BIND pose: `pack_census` reads `.tri` offsets for bounds and naming only, and
`follow_bands` poses a SKELETON, which is not a slider. Until 2026-09-09 the
gate therefore had NO row for body-through-garment under a preset -- the form
every in-game bust report arrives in -- so a candidate that IMPROVED morphed
clipping and cost any surface row could only ever FAIL, the benefit having
nowhere to appear. Judged as SHARES of the scored population for the reason the
stretch rows are rates: a count moves when the population moves.

COUNTED OVER THE PIECES BOTH ARMS SCORED. Each arm's census re-applies its own
per-piece exclusions to its OWN geometry, so a candidate that pushes garments off
the body drops the very pieces that were clipping out of its denominator and the
share improves for a reason that is not quality. The rows intersect first (the
fix `stretched_edges` already uses) and then COUNT, which also gets them an exact
comparison -- the gate's 0.005 tolerance applies to non-integer floats, and on a
share it swallows whole pieces once the population passes ~200.

They need `CBBE2UBE_CLIP_PRESET` (a BodySlide preset xml -- machine-local, so it
cannot be named here) and cost one census per arm. `CBBE2UBE_CLIP_EVERY` strides
the population, but NOTE the census's own floor: stride too far and it ABORTs and
the rows SKIP while the gate still exits 0. `--no-clip` skips them. Unset or
unavailable, they print SKIPPED with the reason rather than vanishing.

THREE THINGS THIS REFUSES TO DO, each because it produced a wrong verdict here:

  * SCORE AN EMPTY OR MISMATCHED POPULATION. Files present in one arm only are
    counted and named; zero common files exits 2. "0/0" is not a pass.
  * TRUST AN ARM THAT DID NOT FINISH. A 1-byte or unreadable NIF in either arm
    exits 3 before any metric runs -- the first candidate arm of
    #last-carrier-hold showed six native crashes and four corrupt files, and it
    was the disk at 0 bytes free, not the change. Compare arms only when both
    are whole.
  * LET AN UNPARSED ROW READ AS A PASS. A scorer whose output the regex could
    not find prints UNPARSED and fails the gate.

The vertex/weight delta is reported, not gated: a geometry change is SUPPOSED
to move vertices. What it must not do is move the rows above. A weights-only
change must show 0 verts moved, and that IS asserted when `--weights-only` is
given.

Every arm should come from `parity_convert` (or the batch CLI) so both share
one code state, one seed and one settings file; this gate cannot tell a code
difference from a recipe difference.

Reference, so a number has a scale: the 2026-09-06 build on the 184-NIF
acceptance population scored folds 15974, inverted 1937, bust gap +0.682
(body-swap) / +0.194 (copy), pen 12, tip 1.244 / 0.454, newly emptied 0.
Added 2026-09-08 on the same population: stretch rate p50 0.1495% / p90
2.0217%, edge deviation p50 0.0331, over 598 shapes = 299 garments.
"""
from __future__ import annotations

import atexit
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / ".pynifly"))

import logging  # noqa: E402
logging.disable(logging.WARNING)

WRITE_MIN = 1e-4
TOL = 0.005
FOLLOW_TOL = 0.010

# EVERY MEASURED COLUMN IN THIS PROJECT'S SCORERS IS SIGNED. Standoff, tip
# clearance and follow are all signed by construction (follow is a dot product
# over a magnitude), and an unsigned capture does not FAIL on a negative -- it
# simply stops matching the line, so the row reads UNPARSED or, worse, the cell
# silently leaves the population. Counts are the only unsigned things here.
_NUMCOL = r"([+-]?\d+(?:\.\d+)?)"
_TIP_ROW = r"^%s\s+" + r"\s+".join([_NUMCOL] * 3) + r"\s*$"


# ----------------------------------------------------------------- population

def _nifs(arm: str) -> dict[str, str]:
    root = os.path.join(arm, "meshes")
    out = {}
    for p in glob.glob(os.path.join(root, "**", "*.nif"), recursive=True):
        if "_bsa_staging" in p or "1stperson" in os.path.basename(p).lower():
            continue
        out[os.path.relpath(p, root).replace("\\", "/")] = p
    return out


def _digest(path: str) -> bytes:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).digest()


def noise_set(ctrl: str, repeat: str) -> "list[str]":
    """NIFs (relative to meshes/) on which two arms of the SAME code differ:
    different bytes, or present in only one of them. #repeat-control"""
    a, r = _nifs(ctrl), _nifs(repeat)
    noise = set(a) ^ set(r)
    noise |= {rel for rel in set(a) & set(r) if _digest(a[rel]) != _digest(r[rel])}
    return sorted(noise)


def filtered_arm(arm: str, exclude, dst: str) -> str:
    """`arm` rebuilt at `dst` without the NIFs named in `exclude` (relative to
    meshes/), hard-linked where the volume allows and copied where not. Every
    scorer globs an arm directory, so a filtered arm keeps the noise out of every
    row without a change to any scorer. #repeat-control"""
    skip = {os.path.normcase(os.path.join(arm, "meshes", *rel.split("/")))
            for rel in exclude}
    for root, _dirs, files in os.walk(arm):
        rel_root = os.path.relpath(root, arm)
        os.makedirs(os.path.join(dst, rel_root), exist_ok=True)
        for name in files:
            src = os.path.join(root, name)
            if os.path.normcase(src) in skip:
                continue
            out = os.path.join(dst, rel_root, name)
            try:
                os.link(src, out)
            except OSError:
                shutil.copy2(src, out)
    return dst


def _scratch_beside(arm: str) -> str:
    """A path for a filtered copy of `arm` on the arm's own volume (hard links do
    not cross volumes), removed when the process exits."""
    parent = os.path.dirname(os.path.abspath(arm)) or "."
    d = tempfile.mkdtemp(prefix=".acceptance_noise_", dir=parent)
    atexit.register(shutil.rmtree, d, True)
    return os.path.join(d, os.path.basename(os.path.abspath(arm)))


def _xml_race_at_risk(arm: str) -> str:
    """How many of the arm's pieces resolve their physics XML by a cross-directory
    stem match (hdt_xml_resolution_census), as info. Never raises."""
    try:
        from scripts.analysis import hdt_xml_resolution_census as hx
        rows, _dropped = hx.scan(arm, 0)
        return str(sum(1 for r in rows if r.get("cls") == "AT RISK"))
    except BaseException as e:          # the census can refuse a tiny arm by exiting
        return f"SKIPPED ({type(e).__name__})"


def repeat_control(ctrl: str, cand: str, repeat: str, allow_noise: bool):
    """Print the REPEAT CONTROL block. Return the NIFs to exclude from both arms,
    or None when the repeat is not exact and `--allow-noise` was not given."""
    noise = noise_set(ctrl, repeat)
    print("\nREPEAT CONTROL (two arms of the control code)  #repeat-control")
    print(f"  repeat     : {len(_nifs(repeat))} NIF(s)  {repeat}")
    print(f"  differing  : {len(noise)}")
    for rel in noise[:20]:
        print(f"      noise: {rel}")
    if len(noise) > 20:
        print(f"      ... and {len(noise) - 20} more")
    for lab, arm in (("control", ctrl), ("repeat", repeat), ("candidate", cand)):
        print(f"  physics XML at risk, {lab:<9}: {_xml_race_at_risk(arm)}  (info)")
    if noise and not allow_noise:
        print("  THE REPEAT CONTROL IS NOT EXACT -- a row could be judging the coin "
              "flip, not the change. Refusing (exit 4); --allow-noise judges the "
              "rest with these NIFs removed from both arms.")
        return None
    return noise


def _whole(arm: str) -> list[str]:
    """Files that cannot be a finished conversion: tiny or unreadable."""
    from pyn import pynifly
    bad = []
    for rel, p in _nifs(arm).items():
        try:
            if os.path.getsize(p) < 1024:
                bad.append(f"{rel}: {os.path.getsize(p)} bytes")
                continue
            pynifly.NifFile(filepath=p)
        except Exception as e:
            bad.append(f"{rel}: {e!r}"[:120])
    return bad


# ------------------------------------------------------------ vertex / weight

def _rows_of(shape):
    out = {}
    for b in (shape.bone_names or []):
        try:
            pairs = shape.bone_weights[b]
        except Exception:
            continue
        for i, w in pairs:
            if w > WRITE_MIN:
                out.setdefault(int(i), {})[b] = float(w)
    return out


def delta(ctrl: str, cand: str, common: list[str]):
    """(files moved, worst vertex delta, rows differing, rows total, worst
    influence delta, [(rows changed, rel)...])."""
    import numpy as np
    from pyn import pynifly
    a_all, b_all = _nifs(ctrl), _nifs(cand)
    moved = 0
    worst_v = 0.0
    rows_diff = rows_total = 0
    worst_w = 0.0
    per_file = []
    for rel in common:
        pa, pb = a_all[rel], b_all[rel]
        if hashlib.sha256(open(pa, "rb").read()).digest() == \
                hashlib.sha256(open(pb, "rb").read()).digest():
            continue
        A = {s.name: s for s in pynifly.NifFile(filepath=pa).shapes}
        B = {s.name: s for s in pynifly.NifFile(filepath=pb).shapes}
        f_moved = False
        f_rows = 0
        for n in set(A) & set(B):
            va = np.asarray(A[n].verts, np.float64)
            vb = np.asarray(B[n].verts, np.float64)
            if va.shape == vb.shape and va.size:
                d = float(np.abs(va - vb).max())
                worst_v = max(worst_v, d)
                f_moved = f_moved or d > 0
            ra, rb = _rows_of(A[n]), _rows_of(B[n])
            keys = set(ra) | set(rb)
            rows_total += len(keys)
            for i in keys:
                x, y = ra.get(i, {}), rb.get(i, {})
                if x != y:
                    rows_diff += 1
                    f_rows += 1
                    for bn in set(x) | set(y):
                        worst_w = max(worst_w,
                                      abs(x.get(bn, 0.0) - y.get(bn, 0.0)))
        moved += f_moved
        if f_rows:
            per_file.append((f_rows, rel))
    per_file.sort(reverse=True)
    return moved, worst_v, rows_diff, rows_total, worst_w, per_file


# ------------------------------------------------------------------- scorers

def _run(mod: str, *args: str) -> str:
    env = dict(os.environ)
    env.setdefault("PYTHONHASHSEED", "1")
    r = subprocess.run([sys.executable, "-m", mod, *args], capture_output=True,
                       text=True, cwd=str(_REPO), env=env)
    out = r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")
    print(f"\n{'=' * 78}\n$ python -m {mod} {' '.join(args)}\n{'=' * 78}")
    print(out.rstrip())
    return out


def _num(text: str, pat: str):
    m = re.search(pat, text, re.M)
    return float(m.group(1)) if m else None


# The exact line `bust_gap_score` prints instead of a table when a path has
# fewer than 5 shapes. Module level so a test can exercise it without a
# subprocess -- and so rewording it on one side is caught on the other.
SKIP_NOTE = re.compile(r"\s*(body-swap|copy path): only (\d+) shapes"
                       r" -- not reported")

# WEIGHT 0. `bust_gap_score` and `nipple_clearance` print the `_0` files in a
# block of their own after the gated weight-1 report, worded so none of the
# weight-1 patterns here can land on it (every arm label prefixed `w0 `, paths
# named "weight 0 / ...", "less room" in place of "tighter"). These are the
# patterns that read it -- module level for the same reason as SKIP_NOTE.
W0_SECTION = re.compile(r"\s*weight 0 / (body-swap|copy path): \d+ shapes\s*$")
W0_SKIP_NOTE = re.compile(r"\s*weight 0 / (body-swap|copy path): (\d+) shapes,"
                          r" too few to report")
W0_NOT_MEASURED = re.compile(r"weight 0 NOT MEASURED|nothing was measured at "
                             r"weight 0|weight 0: nothing measured")


def census(arm: str) -> dict:
    t = _run("scripts.analysis.pack_census", arm)
    return {
        "folds": _num(t, r"^\s*FOLDED\s*:\s*(\d+)"),
        "inverted": _num(t, r"^\s*INVERTED\s*:\s*(\d+)"),
        "tri_oob": _num(t, r"^\s*TRI offsets out of bounds\s*:\s*(\d+)"),
        "bodytri_on_body": _num(t, r"^\s*BODYTRI present on the body\s*:\s*(\d+)/"),
        "untagged_cloth": _num(t, r"^\s*NIFs with an untagged cloth shape\s*:\s*(\d+)"),
        "pair_mismatch": _num(t, r"^\s*pairs with a vert-count mismatch\s*:\s*(\d+)"),
        # DEAD SLIDERS. A tri that names a shape the NIF does not have morphs
        # NOTHING, and the gate could not see it: on the 2026-09-06 pack that
        # was 47 NIFs, 42 of them losing EVERY morph, and the whole class rode
        # through every verdict this gate has ever given.
        "tri_names_partial": _num(
            t, r"^\s*SOME tri shapes the NIF does not have\s*:\s*(\d+)"),
        "tri_names_dead": _num(
            t, r"^\s*NO tri shape the NIF has\s*:\s*(\d+)"),
        # The population behind those two. Not gated -- reported so a run that
        # scored almost nothing cannot read as "clean".
        "tri_names_scored": _num(
            t, r"^\s*NIFs scored \(their own BODYTRI resolved\)\s*:\s*(\d+)"),
    }


def bust_gap(ctrl: str, cand: str) -> dict:
    t = _run("scripts.analysis.bust_gap_score", f"{ctrl}=control", f"{cand}=candidate")
    out = {}
    section = section0 = None
    for line in t.splitlines():
        if "body-swap only" in line:
            section = "swap"
        elif "copy path only" in line:
            section = "copy"
        # "NOT MEASURED" IS NOT "FAILED". `bust_gap_score` refuses a path with
        # fewer than 5 shapes and prints this instead of a table. Without
        # catching it the gate saw None, called the row UNPARSED and FAILED the
        # arm -- a false FAIL on a population that was simply too small, which
        # is the very distinction `_census_common.require_population` exists to
        # keep ("0/0 is not a pass" cuts BOTH ways). Recorded so the row can say
        # SKIPPED with its count, and still never read as ok.
        ns = SKIP_NOTE.match(line)
        if ns:
            out["skipped_" + ("swap" if ns.group(1) == "body-swap" else "copy")] \
                = int(ns.group(2))
        # `bust p50` is a SIGNED standoff too -- the same scorer's `worst`
        # column is a minimum over the same quantity and is printed with a
        # sign. An unsigned pattern here would drop the whole row on an arm bad
        # enough to push the MEDIAN inside the body, i.e. exactly the arm this
        # gate must not wave through. `pen` is a count and stays unsigned.
        m = re.match(r"\s*(control|candidate)\s+bust p50\s+" + _NUMCOL +
                     r"\s+gap p50\s+" + _NUMCOL + r"\s+pen\s+(\d+)", line)
        if m and section:
            out[f"gap_{section}_{m.group(1)}"] = float(m.group(3))
            out[f"pen_{section}_{m.group(1)}"] = float(m.group(4))
        # WEIGHT 0, read with its OWN section: the weight-1 `section` above is
        # sticky and is still set when the weight-0 block begins.
        s0 = W0_SECTION.match(line)
        if s0:
            section0 = "swap" if s0.group(1) == "body-swap" else "copy"
        n0 = W0_SKIP_NOTE.match(line)
        if n0:
            out["w0_skipped_" + ("swap" if n0.group(1) == "body-swap"
                                 else "copy")] = int(n0.group(2))
        m0 = re.match(r"\s*w0 (control|candidate)\s+bust p50\s+" + _NUMCOL +
                      r"\s+gap p50\s+" + _NUMCOL + r"\s+pen\s+(\d+)", line)
        if m0 and section0:
            out[f"w0_gap_{section0}_{m0.group(1)}"] = float(m0.group(3))
            out[f"w0_pen_{section0}_{m0.group(1)}"] = float(m0.group(4))
    if W0_NOT_MEASURED.search(t):
        out["w0_bust_skipped"] = True
    return out


# `nipple_clearance` prints `arm  p50  p05  min`, and EVERY ONE OF THOSE CAN BE
# NEGATIVE -- a negative tip clearance IS the poke-through this row exists to
# catch. The pattern used to require `[\d.]+` per column, so a single piece with
# a tip inside the body made the whole LINE fail to match: p50 and p05 went
# missing, both rows read UNPARSED, and the gate failed the arm with a parse
# error instead of the defect. The gate was blind exactly when it mattered.


def tip(ctrl: str, cand: str) -> dict:
    t = _run("scripts.analysis.nipple_clearance", f"{ctrl}=control", f"{cand}=candidate")
    out = {}
    for lab in ("control", "candidate"):
        m = re.search(_TIP_ROW % lab, t, re.M)
        if m:
            out[f"tip_p50_{lab}"] = float(m.group(1))
            out[f"tip_p05_{lab}"] = float(m.group(2))
            out[f"tip_min_{lab}"] = float(m.group(3))
    # The scorer's own refusal when no piece covers the tip in every arm. An
    # empty population is NOT a failing one -- see the same distinction on the
    # bust-gap rows and in `_census_common.require_population`.
    if "0/0 IS NOT A PASS" in t:
        out["tip_skipped"] = True
    out["tighter"] = _num(t, r"tighter\s+(\d+)")
    # WEIGHT 0: the `w0 `-prefixed arm rows and the "less room" count of the
    # scorer's weight-0 block. An unmeasured weight 0 is an unjudged row.
    for lab in ("control", "candidate"):
        m = re.search(_TIP_ROW % ("w0 " + lab), t, re.M)
        if m:
            out[f"w0_tip_p50_{lab}"] = float(m.group(1))
            out[f"w0_tip_p05_{lab}"] = float(m.group(2))
            out[f"w0_tip_min_{lab}"] = float(m.group(3))
    if W0_NOT_MEASURED.search(t):
        out["w0_tip_skipped"] = True
    out["w0_less_room"] = _num(t, r"less room\s+(\d+)")
    return out


def zero_weight(ctrl: str, cand: str) -> dict:
    t = _run("scripts.analysis.zero_weight_pair_ab", "--before", ctrl, "--after", cand)
    return {"newly_emptied": _num(t, r"NEWLY EMPTIED[^:]*:\s*(\d+)"),
            "rescued": _num(t, r"rescued[^:]*:\s*(\d+)"),
            "both_empty": _num(t, r"both empty[^:]*:\s*(\d+)")}


# THE LAST-STAGE STRETCH ROW. `damage_ledger` counted stretched edges per PASS
# from stage dumps, and flow disagreed with outcome the one time both were
# measured: `SURFACE_WARP_FIELD` cut the stretch `warp` CREATES by 77% and the
# shipped mesh came out 26% MORE stretched, because `conform` did more work
# downstream. So this row judges the WRITTEN NIF against the AUTHOR's mesh.
# Columns are magnitudes and counts, genuinely unsigned -- `_NUMCOL` is used
# anyway, because the three scorers that got this wrong did not FAIL on a
# negative, they stopped matching the line and trimmed their own population.
_STRETCH_ROW = (r"^\s+%s\s+(\d+)\s+" + _NUMCOL + r"%%\s+" + _NUMCOL
                + r"%%\s+" + _NUMCOL + r"%%\s+" + _NUMCOL + r"\s*$")


def stretch(ctrl: str, cand: str) -> dict:
    t = _run("scripts.analysis.stretched_edges",
             f"{ctrl}=control", f"{cand}=candidate")
    out: dict = {}
    for lab in ("control", "candidate"):
        m = re.search(_STRETCH_ROW % lab, t, re.M)
        if m:
            out[f"pooled_{lab}"] = float(m.group(1))
            out[f"rate_p50_{lab}"] = float(m.group(2))
            out[f"rate_p90_{lab}"] = float(m.group(3))
            out[f"rate_max_{lab}"] = float(m.group(4))
            out[f"dev_p50_{lab}"] = float(m.group(5))
    out["scored"] = _num(t, r"shapes scored in EVERY arm\s*:\s*(\d+)")
    out["garments"] = _num(t, r"distinct garments behind them\s*:\s*(\d+)")
    # The scorer's own refusal. An empty population is NOT a failing one -- the
    # same distinction the bust-gap and tip rows already carry.
    if "0/0 is not a pass" in t:
        out["skipped"] = True
    return out


# --- MORPHED CLIP ------------------------------------------------------------
# THE GAP THIS CLOSES. Every other scorer here reads the BIND pose. `pack_census`
# touches `.tri` offsets only for bounds and naming, `follow_bands` poses a
# SKELETON (not a morph), and nothing else applies a slider at all -- so until
# 2026-09-09 the gate had NO row for body-through-garment under a body preset,
# which is the form every in-game bust report arrives in. `morph_clip_test`'s own
# header says it: "Every clip number in this project is taken at BIND POSE, and
# bind pose is not what ships."
#
# The consequence was structural, not cosmetic: a candidate that IMPROVED
# morphed clipping and cost any surface or penetration row could only ever FAIL,
# because the benefit had nowhere to appear. `#panel-rigid-keep-clearance` is the
# worked example -- morph clip 50% -> 45% of scored pieces, and 10 FAIL rows.
#
# SHARES, NOT COUNTS -- the rule `stretched_edges` already follows. A count moves
# when the scored population moves, so a piece dropping out would read as a
# quality change. Both arms are strided identically (sorted, `--every N`), and
# the scored counts print as info so a reader can see when they diverge anyway.
#
# MACHINE-LOCAL BY NECESSITY. The preset is a modlist file, so it cannot be named
# in tracked content. Set `CBBE2UBE_CLIP_PRESET`; without it these rows SKIP
# LOUDLY rather than vanishing, because a row that silently disappears reads as
# a pass.

def _clip_arm(arm: str, preset: str, every: str, out_json: str) -> dict:
    """One arm's PER-PIECE census rows, or a NAMED reason there are none.

    Returns the rows, not shares. The shares have to be taken over the pieces
    BOTH arms scored -- see `clip()`.
    """
    t = _run("scripts.analysis.band_class_census", "--pack",
             os.path.join(arm, "meshes", "!UBE"), "--preset", preset,
             "--band", "bust", "--every", every, "--floor", "20",
             "--out", out_json)
    if "ABORT" in t:
        m = re.search(r"^ABORT: (.+)$", t, re.M)
        return {"skip": (m.group(1).strip() if m else "the census aborted")}
    # The census's OWN warning that its exclusions have biased the sample. It
    # prints this and still reports; passing it through unnoticed is how a
    # biased subset reads as a clean row.
    biased = "HIGH: treat every share below as a biased subset" in t
    try:
        with open(out_json, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:                                   # noqa: BLE001
        return {"skip": "could not read the census json (%s)" % type(e).__name__}
    root = os.path.join(arm, "meshes")
    rows = {}
    for r in doc.get("rows", []):
        nif = str(r.get("nif") or "")
        if not nif:
            continue
        try:
            rel = os.path.relpath(nif, root).replace("\\", "/").lower()
        except ValueError:
            rel = os.path.basename(nif).lower()
        rows[rel] = r
    if not rows:
        return {"skip": "the census json carried no rows"}
    return {"rows": rows, "scored": int(doc.get("scored", len(rows))),
            "biased": biased}


def clip(ctrl: str, cand: str) -> dict:
    preset = os.environ.get("CBBE2UBE_CLIP_PRESET", "").strip()
    # ORDER MATTERS: "is not a file" also contains "CLIP_PRESET", so the
    # not-a-file case must be distinguishable from the unset case -- a
    # configured-but-broken path reading as "nobody set it up" is how the rows
    # stay dark run after run.
    if not preset:
        return {"skip": "SKIPPED: CLIP_PRESET_UNSET -- set CBBE2UBE_CLIP_PRESET "
                        "(a BodySlide preset xml) to score morphed clip"}
    if not os.path.isfile(preset):
        return {"skip": "SKIPPED: CLIP_PRESET_MISSING -- CBBE2UBE_CLIP_PRESET "
                        "is set but is not a file: %s" % preset}
    every = os.environ.get("CBBE2UBE_CLIP_EVERY", "1").strip() or "1"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        a = _clip_arm(ctrl, preset, every, os.path.join(td, "ctrl.json"))
        b = _clip_arm(cand, preset, every, os.path.join(td, "cand.json"))
    for lab, arm in (("control", a), ("candidate", b)):
        if "skip" in arm:
            return {"skip": "SKIPPED: %s census -- %s" % (lab, arm["skip"])}

    # THE POPULATION MUST BE THE INTERSECTION, and this is not a nicety.
    #
    # Each arm's census rglobs its OWN tree and re-applies its own per-piece
    # exclusions against its OWN geometry -- a piece whose band coverage falls
    # under the census's minimum is dropped from THAT arm only. So a candidate
    # that pushes garments off the body removes pieces from its denominator,
    # and the pieces it removes are exactly the ones that were clipping: the
    # share improves for a reason that is not quality. Scored 92 against 41
    # scored `ok` on all three rows with only an `info` row beside it.
    #
    # `stretched_edges` already solved this by reporting "shapes scored in
    # EVERY arm". Same fix: intersect, then count.
    common = sorted(set(a["rows"]) & set(b["rows"]))
    if len(common) < 20:
        return {"skip": "SKIPPED: only %d piece(s) scored in BOTH arms "
                        "-- 0/0 IS NOT A PASS" % len(common)}

    def counts(rows):
        bind = sum(1 for k in common if float(rows[k].get("bind", 0.0)) > 0.05)
        m05 = sum(1 for k in common if float(rows[k].get("morph", 0.0)) > 0.05)
        m10 = sum(1 for k in common if float(rows[k].get("morph", 0.0)) > 1.0)
        # MAGNITUDE, beside the prevalence.
        #
        # The three counts above measure HOW MANY pieces clip. They are blind to
        # HOW MUCH: a piece improving 5.316% -> 3.585% stays above both
        # thresholds and moves no count, so a change that halved the clip on
        # every piece in the pack without taking any of them under a threshold
        # would score as no change at all. Measured: `#layer-order-last` cut the
        # reported piece 33% and the count rows read 46/46, 34/34, 23/23.
        #
        # p50 and p90 over the SHARED pieces, which is the shape `stretched_edges`
        # already uses for its rates -- and for the same reason, that a
        # percentile is comparable between arms while a pooled total is a
        # mesh-size ranking.
        mo = sorted(float(rows[k].get("morph", 0.0)) for k in common)
        bi = sorted(float(rows[k].get("bind", 0.0)) for k in common)

        def pct(v, q):
            if not v:
                return None
            i = min(len(v) - 1, max(0, int(round((len(v) - 1) * q))))
            return round(v[i], 4)

        return {"bind": bind, "morph05": m05, "morph10": m10,
                "morph_p50": pct(mo, 0.50), "morph_p90": pct(mo, 0.90),
                "bind_p90": pct(bi, 0.90)}

    # COUNTS, not shares -- and that is only correct BECAUSE the denominator is
    # now fixed by construction. Counts also get an exact comparison: `add()`
    # applies its 0.005 tolerance to non-integer floats only, and on a share
    # that tolerance silently swallows whole pieces once the population passes
    # ~200 (a share is an exact k/n ratio with no float noise to absorb).
    return {"preset": os.path.basename(preset), "every": every,
            "common": len(common),
            "scored_ctrl": a["scored"], "scored_cand": b["scored"],
            "biased": bool(a.get("biased") or b.get("biased")),
            "ctrl": counts(a["rows"]), "cand": counts(b["rows"])}


def follow(ctrl: str, cand: str, rels: list[str]) -> dict:
    """Worst change in posed follow MEDIAN over (shape, band, pose), on the
    files whose weights moved most. None when no skeleton is configured.

    TWO MEASURES, because one of them cannot judge a geometry change:

      `follow_worst_delta`  max |ctrl - cand|. A NO-CHANGE guard, and the right
            rule for a refactor that claims to move nothing. It is DIRECTION
            BLIND by construction, so it fails any intentional geometry change
            that re-pairs weight rows -- even one that makes follow BETTER.
      `follow_net_ideal`    sum of (|cand-1| - |ctrl-1|). Follow of 1.0 is the
            garment travelling exactly with the body, so distance from 1.0 is
            the error and this is signed: negative = closer to ideal overall.
            With `better`/`worse` cell counts beside it.

    Measured on `#src-normal-fix` 2026-09-06: worst_delta 0.063 read as a flat
    FAIL, while the net was -0.027 over 255 cells (79 better / 89 worse / 87
    unchanged) -- a wash with one bust cell worse, not the regression the single
    number implied. A geometry item judged on the no-change row alone is being
    judged on whether it did anything at all.
    """
    if "CBBE2UBE_SKELETON_NIF" not in os.environ:
        return {"follow_worst_delta": None, "follow_note":
                "SKIPPED: set CBBE2UBE_SKELETON_NIF (a skeleton_female.nif) to "
                "score posed follow; a weights change is otherwise unjudged"}
    worst = 0.0
    scored = 0
    net = 0.0
    better = worse = 0
    # THE MEDIAN AND p10 COLUMNS ARE SIGNED, and the old pattern demanded
    # `[\d.]+`. `follow_bands` computes
    #     follow = dot(garment_displacement, body_direction) / |body_displacement|
    # -- a DOT PRODUCT over a magnitude, so a garment moving against the body
    # gives a NEGATIVE ratio. An unsigned column pattern did not fail such a
    # line, it simply did not MATCH it, so that cell never entered `tables` and
    # was silently dropped from the comparison. The cells most likely to be
    # negative are the pathological ones -- a garment travelling the wrong way
    # under pose -- so the gate was discarding its own worst evidence and
    # quietly shrinking `scored` while reporting a clean number.
    row = re.compile(r"^\s*(\w[\w ]*?)\s{2,}(.+?)\s{2,}(\d+)\s+"
                     + _NUMCOL + r"\s+" + _NUMCOL + r"\s+")
    for rel in rels:
        tables = {}
        for lab, arm in (("control", ctrl), ("candidate", cand)):
            p = os.path.join(arm, "meshes", rel)
            t = _run("scripts.analysis.follow_bands", p)
            shape = None
            for line in t.splitlines():
                m = re.match(r"\s*shape `(.+?)`", line)
                if m:
                    shape = m.group(1)
                    continue
                m = row.match(line)
                if m and shape and not line.strip().startswith("band"):
                    tables.setdefault(lab, {})[(shape, m.group(1).strip(),
                                                m.group(2).strip())] = float(m.group(4))
        a, b = tables.get("control", {}), tables.get("candidate", {})
        for k in set(a) & set(b):
            worst = max(worst, abs(a[k] - b[k]))
            d = abs(b[k] - 1.0) - abs(a[k] - 1.0)     # + = further from ideal
            net += d
            if d < 0:
                better += 1
            elif d > 0:
                worse += 1
            scored += 1
    return {"follow_worst_delta": worst if scored else None,
            "follow_net_ideal": net if scored else None,
            "follow_better": better, "follow_worse": worse,
            "follow_note": f"{scored} (shape, band, pose) cells compared over "
                           f"{len(rels)} file(s)"}


# --------------------------------------------------------------------- table

def verdict_table(c: dict, d: dict, g: dict, tp: dict, zw: dict, fo: dict,
                  weights_only: bool,
                  geometry: bool = False,
                  st: "dict | None" = None,
                  cl: "dict | None" = None) -> tuple[list[tuple], bool]:
    rows = []
    ok_all = True

    def add(name, a, b, rule):
        nonlocal ok_all
        if a is None or b is None:
            rows.append((name, a, b, "UNPARSED"))
            ok_all = False
            return
        if rule == "le":
            ok = b <= a + (TOL if isinstance(a, float) and not float(a).is_integer() else 0)
        elif rule == "ge":
            ok = b >= a - TOL
        elif rule == "author":
            # DIRECTIONAL, and it is the whole point of this rule: the value is
            # a SIGNED distance from the author (`ours - author`), so the goal
            # is |value| -> 0, not value -> +inf. Judged on the MAGNITUDE.
            ok = abs(b) <= abs(a) + TOL
        elif rule == "zero":
            ok = b == 0
        else:
            ok = True
        rows.append((name, a, b, "ok" if ok else "FAIL"))
        ok_all = ok_all and ok

    add("folds", c["ctrl"]["folds"], c["cand"]["folds"], "le")
    add("inverted", c["ctrl"]["inverted"], c["cand"]["inverted"], "le")
    add("TRI offsets out of bounds", c["ctrl"]["tri_oob"], c["cand"]["tri_oob"], "le")
    add("BODYTRI on the body", c["ctrl"]["bodytri_on_body"], c["cand"]["bodytri_on_body"], "ge")
    add("untagged cloth shapes", c["ctrl"]["untagged_cloth"], c["cand"]["untagged_cloth"], "le")
    add("pair vert-count mismatch", c["ctrl"]["pair_mismatch"], c["cand"]["pair_mismatch"], "le")
    # A tri naming a shape the NIF does not have moves nothing at runtime.
    # Judged, because it is a shipped defect the gate was blind to.
    add("tri names NO shape the NIF has",
        c["ctrl"]["tri_names_dead"], c["cand"]["tri_names_dead"], "le")
    add("tri names SOME shape the NIF lacks",
        c["ctrl"]["tri_names_partial"], c["cand"]["tri_names_partial"], "le")
    rows.append(("  ^ NIFs scored for it", c["ctrl"].get("tri_names_scored"),
                 c["cand"].get("tri_names_scored"), "info"))
    # THIS ROW USED TO FIRE BACKWARDS ON ITS OWN GOAL. `gap = ours - author`
    # (bust_gap_score prints the definition under its table), and the rule was
    # `candidate >= control` -- so an arm that moved the armour FURTHER from
    # what the author built scored ok, and an arm that CLOSED the gap scored
    # FAIL. It was recorded as "read this row by hand" for a day; on the A1
    # retune it duly scored a +0.498 -> +0.548 widening as ok.
    #
    # The one-sided rule had a real argument behind it -- a bigger gap is safer
    # from clipping -- but the gate already carries that argument as its OWN
    # rows: `bust-band pen` (le) and `tip clearance` (ge), directly below. This
    # row's job is AUTHOR FIDELITY, which is what the project's standing rule
    # asks for: measure the authored distance and restore it.
    # A PATH THE SCORER REFUSED IS NOT A PATH THAT FAILED. `bust_gap_score`
    # declines a path with fewer than 5 shapes; without this the row read
    # UNPARSED and failed the arm on a population that was merely too small.
    # SKIPPED never reads as ok either -- it says, with the count, that this
    # path went unjudged, which is what "0/0 is not a pass" means in both
    # directions.
    def add_path(name, key, rule, pre=""):
        a, b = (g.get(pre + "gap_%s_control" % key),
                g.get(pre + "gap_%s_candidate" % key))
        n = g.get(pre + "skipped_" + key)
        if a is None and b is None and n is not None:
            rows.append((name, "-", "%d shapes (<5)" % n, "SKIPPED"))
            return
        add(name, a, b, rule)

    add_path("bust gap vs author, body-swap", "swap", "author")
    add_path("bust gap vs author, copy path", "copy", "author")
    # Direction stays VISIBLE beside the judged magnitude: |gap| alone cannot
    # tell "closed toward the author" from "crossed past them and penetrated".
    for _lab, _k in (("body-swap", "swap"), ("copy path", "copy")):
        _a, _b = g.get("gap_%s_control" % _k), g.get("gap_%s_candidate" % _k)
        if _a is not None and _b is not None:
            rows.append(("  ^ signed move (%s)" % _lab, round(_a, 4),
                         round(_b, 4), "info"))

    def add_pen(name, key, pre=""):
        a, b = (g.get(pre + "pen_%s_control" % key),
                g.get(pre + "pen_%s_candidate" % key))
        n = g.get(pre + "skipped_" + key)
        if a is None and b is None and n is not None:
            rows.append((name, "-", "%d shapes (<5)" % n, "SKIPPED"))
            return
        add(name, a, b, "le")

    add_pen("bust-band pen, body-swap", "swap")
    add_pen("bust-band pen, copy path", "copy")
    if tp.get("tip_skipped") and tp.get("tip_p50_control") is None:
        # No piece covers the tip in EVERY arm: the scorer refused, so this is
        # an unjudged row, not a failed one. Still never `ok`.
        for _n in ("tip clearance p50", "tip clearance p05",
                   "pieces tighter at the tip"):
            rows.append((_n, "-", "no piece covers the tip", "SKIPPED"))
    else:
        add("tip clearance p50", tp.get("tip_p50_control"), tp.get("tip_p50_candidate"), "ge")
        add("tip clearance p05", tp.get("tip_p05_control"), tp.get("tip_p05_candidate"), "ge")
        add("pieces tighter at the tip", 0, tp.get("tighter"), "zero")
        # The WORST single tip, reported beside the percentiles. A negative
        # value is a tip inside the body; the percentiles can both improve
        # while one piece goes through, so the extreme must stay visible.
        _ma, _mb = tp.get("tip_min_control"), tp.get("tip_min_candidate")
        if _ma is not None and _mb is not None:
            rows.append(("  ^ worst single tip", round(_ma, 4), round(_mb, 4),
                         "info" if _mb >= _ma - TOL else "FAIL"))
            ok_all = ok_all and _mb >= _ma - TOL
    # WEIGHT 0 -- the same bust and tip rows over the `_0` files, each against
    # the bodies weight 0 was built on. Until 2026-09-21 this gate could not see
    # weight 0 at all: both scorers read `_1` only, so a change that regressed
    # the weight-0 half PASSED here, and one that repaired it had nowhere to
    # show. Same rules as weight 1. A weight the scorer did not measure reads
    # SKIPPED with its reason -- never ok, and never FAIL.
    if (g.get("w0_bust_skipped") and g.get("w0_gap_swap_control") is None
            and g.get("w0_gap_copy_control") is None):
        for _n in ("bust gap vs author, body-swap, weight 0",
                   "bust gap vs author, copy path, weight 0",
                   "bust-band pen, body-swap, weight 0",
                   "bust-band pen, copy path, weight 0"):
            rows.append((_n, "-", "weight 0 not measured", "SKIPPED"))
    else:
        add_path("bust gap vs author, body-swap, weight 0", "swap", "author",
                 pre="w0_")
        add_path("bust gap vs author, copy path, weight 0", "copy", "author",
                 pre="w0_")
        add_pen("bust-band pen, body-swap, weight 0", "swap", pre="w0_")
        add_pen("bust-band pen, copy path, weight 0", "copy", pre="w0_")
    if tp.get("w0_tip_skipped") and tp.get("w0_tip_p50_control") is None:
        for _n in ("tip clearance p50, weight 0", "tip clearance p05, weight 0",
                   "pieces with less room at the tip, weight 0"):
            rows.append((_n, "-", "weight 0 not measured", "SKIPPED"))
    else:
        add("tip clearance p50, weight 0", tp.get("w0_tip_p50_control"),
            tp.get("w0_tip_p50_candidate"), "ge")
        add("tip clearance p05, weight 0", tp.get("w0_tip_p05_control"),
            tp.get("w0_tip_p05_candidate"), "ge")
        add("pieces with less room at the tip, weight 0", 0,
            tp.get("w0_less_room"), "zero")
        _ma, _mb = tp.get("w0_tip_min_control"), tp.get("w0_tip_min_candidate")
        if _ma is not None and _mb is not None:
            rows.append(("  ^ worst single tip, weight 0", round(_ma, 4),
                         round(_mb, 4),
                         "info" if _mb >= _ma - TOL else "FAIL"))
            ok_all = ok_all and _mb >= _ma - TOL
    # MORPHED CLIP. Two of the three rows are taken under a body preset; the
    # BIND row beside them is the unmorphed number the census reports from the
    # same run, kept because a change that trades bind for morph should show
    # both. Counted over the pieces BOTH arms scored -- see `clip()`.
    if cl is None or "skip" in cl:
        # A SHORT tag in the cell, the FULL reason in the footer. The census's
        # own refusal runs to three lines and shoved the table out of
        # alignment; truncating it in the row while dropping it entirely would
        # be the worse trade, so it moves below.
        _why = (cl or {}).get("skip", "SKIPPED (--no-clip)")
        _short = ("no preset" if "CLIP_PRESET_UNSET" in _why
                  else "preset missing" if "CLIP_PRESET_MISSING" in _why
                  else "census floor" if "floor is" in _why
                  else "no shared pieces" if "scored in BOTH" in _why
                  else "--no-clip" if "--no-clip" in _why
                  else "not measured")
        for _n in ("morph clip > 0.05% (pieces)", "morph clip > 1.0% (pieces)",
                   "bind clip > 0.05% (pieces)"):
            rows.append((_n, "-", _short, "SKIPPED"))
    else:
        add("morph clip > 0.05% (pieces)",
            cl["ctrl"]["morph05"], cl["cand"]["morph05"], "le")
        add("morph clip > 1.0% (pieces)",
            cl["ctrl"]["morph10"], cl["cand"]["morph10"], "le")
        add("bind clip > 0.05% (pieces)",
            cl["ctrl"]["bind"], cl["cand"]["bind"], "le")
        # MAGNITUDE beside prevalence -- the counts above cannot see a piece
        # clipping LESS, only a piece crossing a threshold.
        add("morph clip p50 (% of band)",
            cl["ctrl"]["morph_p50"], cl["cand"]["morph_p50"], "le")
        add("morph clip p90 (% of band)",
            cl["ctrl"]["morph_p90"], cl["cand"]["morph_p90"], "le")
        add("bind clip p90 (% of band)",
            cl["ctrl"]["bind_p90"], cl["cand"]["bind_p90"], "le")
        rows.append(("  ^ pieces scored in BOTH arms", cl["common"],
                     cl["common"], "info"))
        # Each arm's OWN scored count. Equal counts do not prove equal
        # populations, but a divergence is the first sign the candidate's
        # geometry changed which pieces the census would even look at -- the
        # reason these rows are counted over the intersection at all.
        if cl["scored_ctrl"] != cl["scored_cand"]:
            rows.append(("  ^ census scored per arm", cl["scored_ctrl"],
                         cl["scored_cand"], "info"))
        if cl.get("biased"):
            rows.append(("  ^ census called its own sample BIASED", "-",
                         "see the census output", "info"))
    # THE LAST STAGE, not the flow. Judged as a per-shape RATE and never as the
    # pooled count: a pooled total is a mesh-size ranking, and reading one is
    # how a 6-better / 6-worse / 22-unchanged wash was once written up as a
    # regression, on a delta ONE garment carried at both its body weights.
    if st is None:
        rows.append(("stretched edges vs author", "-",
                     "scorer not run", "SKIPPED"))
    elif st.get("skipped"):
        rows.append(("stretched edges vs author", "-",
                     "no shape scored in both arms", "SKIPPED"))
    else:
        add("stretch rate p50 (% of edges)", st.get("rate_p50_control"),
            st.get("rate_p50_candidate"), "le")
        add("stretch rate p90 (% of edges)", st.get("rate_p90_control"),
            st.get("rate_p90_candidate"), "le")
        # Length-weighted, so it does not share the count's exposure to short
        # authored edges. If the two disagree, the COUNT is the contaminated one.
        add("edge deviation p50 (len-weighted)", st.get("dev_p50_control"),
            st.get("dev_p50_candidate"), "le")
        # The WORST single shape, beside the percentiles -- same reason the
        # worst single tip is reported: both percentiles can improve while one
        # piece goes badly wrong, and that piece is the one worth looking at.
        rows.append(("  ^ worst single shape rate", st.get("rate_max_control"),
                     st.get("rate_max_candidate"), "info"))
        rows.append(("  ^ shapes / garments scored",
                     st.get("scored"), st.get("garments"), "info"))
        rows.append(("  ^ pooled stretched edges", st.get("pooled_control"),
                     st.get("pooled_candidate"), "info"))
    add("zero-weight NEWLY EMPTIED", 0, zw.get("newly_emptied"), "zero")
    if fo.get("follow_worst_delta") is not None:
        # THE NO-CHANGE GUARD. Right for a refactor, direction-blind for a
        # geometry change -- see `follow()`. In `--geometry` mode it drops to
        # info and the signed net below becomes the judged row instead.
        _fok = fo["follow_worst_delta"] <= FOLLOW_TOL
        rows.append(("posed follow, worst median delta", 0.0,
                     round(fo["follow_worst_delta"], 4),
                     "ok" if _fok else ("info" if geometry else "FAIL")))
        if not geometry:
            ok_all = ok_all and _fok
        if fo.get("follow_net_ideal") is not None:
            _net = fo["follow_net_ideal"]
            rows.append((
                "posed follow, net distance from ideal", 0.0, round(_net, 4),
                ("ok" if _net <= 0 else ("FAIL" if geometry else "info"))))
            if geometry:
                ok_all = ok_all and _net <= 0
            rows.append(("posed follow, cells better / worse", "-",
                         f"{fo.get('follow_better', 0)} / "
                         f"{fo.get('follow_worse', 0)}", "info"))
    else:
        rows.append(("posed follow", "-", "-", "SKIPPED"))
    if weights_only:
        rows.append(("verts moved (weights-only claim)", 0, d[0],
                     "ok" if d[0] == 0 else "FAIL"))
        ok_all = ok_all and d[0] == 0
    else:
        rows.append(("files with a vertex moved (reported)", "-", d[0], "info"))
    rows.append(("weight rows differing (reported)", "-",
                 f"{d[2]} of {d[3]}", "info"))
    return rows, ok_all


# Flags that consume the NEXT argument. Filtering positionals on the `--`
# prefix alone left `--follow-top N`'s N behind as a third positional, so a
# VALID invocation printed the help and exited 2 -- a usage error that reads as
# a broken gate. Found 2026-09-06 when the same shape of bug bit `morph_sweep`.
_VALUED_FLAGS = ("--follow-top", "--repeat")


def positionals(argv: "list[str]") -> "list[str]":
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in _VALUED_FLAGS:
            skip = True
            continue
        if a.startswith("--"):
            continue
        out.append(a)
    return out


def main(argv: list[str]) -> int:
    args = positionals(argv)
    if len(args) != 2:
        print(__doc__)
        return 2
    ctrl, cand = args
    weights_only = "--weights-only" in argv
    # An INTENTIONAL geometry change re-pairs weight rows, so the no-change
    # follow guard fails it whichever way follow moved. In this mode that row
    # drops to info and the SIGNED net distance from ideal follow is judged
    # instead. Never use it for a change that claims to move nothing.
    geometry = "--geometry" in argv
    no_follow = "--no-follow" in argv
    # The clip rows cost a full census per arm, so a quick structural A/B
    # can opt out -- and the rows then say SKIPPED rather than vanishing.
    no_clip = "--no-clip" in argv
    follow_top = 3
    if "--follow-top" in argv:
        follow_top = int(argv[argv.index("--follow-top") + 1])
    repeat = argv[argv.index("--repeat") + 1] if "--repeat" in argv else None

    a, b = _nifs(ctrl), _nifs(cand)
    common = sorted(set(a) & set(b))
    print("POPULATION")
    print(f"  control    : {len(a)} NIF(s)  {ctrl}")
    print(f"  candidate  : {len(b)} NIF(s)  {cand}")
    print(f"  common     : {len(common)}")
    print(f"  control-only  : {len(set(a) - set(b))}  candidate-only: {len(set(b) - set(a))}")
    for rel in sorted((set(a) ^ set(b)))[:10]:
        print(f"      unpaired: {rel}")
    if not common:
        print("  NOTHING TO COMPARE -- 0/0 IS NOT A PASS")
        return 2

    if repeat:
        excluded = repeat_control(ctrl, cand, repeat, "--allow-noise" in argv)
        if excluded is None:
            return 4
        if excluded:
            ctrl = filtered_arm(ctrl, excluded, _scratch_beside(ctrl))
            cand = filtered_arm(cand, excluded, _scratch_beside(cand))
            a, b = _nifs(ctrl), _nifs(cand)
            common = sorted(set(a) & set(b))
            print(f"  excluded from every row below: {len(excluded)} NIF(s); "
                  f"common now {len(common)}")
            if not common:
                print("  NOTHING TO COMPARE -- 0/0 IS NOT A PASS")
                return 2

    print("\nARM INTEGRITY (the disk-full trap)")
    bad = {"control": _whole(ctrl), "candidate": _whole(cand)}
    for lab, lst in bad.items():
        print(f"  {lab:<10}: {len(lst)} tiny/unreadable NIF(s)")
        for x in lst[:5]:
            print(f"      {x}")
    if any(bad.values()):
        print("  AN ARM DID NOT FINISH -- refusing to score it. Check free disk.")
        return 3

    print("\nVERTEX / WEIGHT DELTA")
    d = delta(ctrl, cand, common)
    print(f"  files with a vertex moved : {d[0]}")
    print(f"  worst vertex delta        : {d[1]:.9f} u")
    print(f"  weight rows differing     : {d[2]} of {d[3]}"
          f"  ({100.0 * d[2] / max(d[3], 1):.4f}%)")
    print(f"  worst per-influence delta : {d[4]:.6f}")
    for n, rel in d[5][:8]:
        print(f"      {n:6d} rows  {rel}")

    c = {"ctrl": census(ctrl), "cand": census(cand)}
    g = bust_gap(ctrl, cand)
    tp = tip(ctrl, cand)
    zw = zero_weight(ctrl, cand)
    st = stretch(ctrl, cand)
    cl = None if no_clip else clip(ctrl, cand)
    top = [rel for _n, rel in d[5][:follow_top]]
    # Name the reason: a skip must never read as a flag the caller did not
    # pass. No differing rows = no file to pose, which is the identical-arm
    # case and is a legitimate skip.
    if no_follow:
        fo = {"follow_worst_delta": None, "follow_note": "SKIPPED (--no-follow)"}
    elif not top:
        fo = {"follow_worst_delta": None,
              "follow_note": "SKIPPED: no weight row differs, nothing to pose"}
    else:
        fo = follow(ctrl, cand, top)

    rows, ok = verdict_table(c, d, g, tp, zw, fo, weights_only, geometry,
                             st, cl)
    print(f"\n{'=' * 78}\nVERDICT   (candidate must be no worse than control on every row)\n{'=' * 78}")
    print(f"  {'metric':<38}{'control':>12}{'candidate':>14}   verdict")
    for name, x, y, v in rows:
        print(f"  {name:<38}{str(x):>12}{str(y):>14}   {v}")
    if cl and "skip" not in cl:
        print(f"  morph clip: preset {cl['preset']}, --every {cl['every']}"
              f", {cl['common']} piece(s) scored in BOTH arms")
    elif cl and "skip" in cl:
        print("  morph clip: " + cl["skip"])
    print(f"\n  posed follow: {fo.get('follow_note')}")
    print(f"  zero-weight absolute (reported): rescued {zw.get('rescued')}, "
          f"both-empty {zw.get('both_empty')}")
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
