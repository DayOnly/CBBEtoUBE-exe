# Pass consolidation plan — the execution kit

> ## READ FIRST — §1–4 ARE THE ORIGINAL PLAN, §5–6 ARE WHAT MEASURING IT FOUND
>
> Several items below were refuted, closed or reframed on 2026-09-06 evening.
> Do not execute §2 from the top without reading §5 and §6.
>
> | item | status after measurement |
> | --- | --- |
> | D1, E1, B1, C1, D2 | **change NOTHING in the shipped pack.** D2 closed (nothing to do); B1's premise **refuted** |
> | A1 conform target | folds −7.5%, inverted −24%, two small bust rows fail. **No knob fixes it.** Already has a good in-game verdict (2026-08-16) |
> | A2 warp field | **CLOSED** — re-tested with a true target, still loses |
> | A3 one IDW | larger than scoped: the two IDWs sit on **different reference clouds** |
> | A4 write-time repairs | premise **confirmed**, unbuilt |
> | A5 ride discard | needs an in-game plan that does not exist. Do not start |
> | over-standoff | **mostly morph headroom** (§6). A global clearance cut clips worse under morph and the ramp **cannot be split**; only an amplitude-gated BASE can work |
>
> Three tools this plan and the 09-01 audit named as standard practice were NOT
> in the toolkit: `morph_sweep` (now tracked), `damage_ledger` (still not — it
> reads stage dumps), `exe_parity_convert` (now tracked). A gate that names a
> tool nobody has is not a gate.
>
> Two gate defects found by using it: the posed-follow row was direction-blind
> (fixed — `--geometry`), and the **bust-gap row is one-sided** — it scores
> *closing* the author gap as a FAIL, and did so twice. Read that row by hand
> until it is made directional.

Written 2026-09-06 for the next session. Everything below was resolved from
the code and the measured record on that date; every item carries the file and
function it touches, the gate that judges it, and the number it has to beat.
Read this file, `docs/PASS_MAP.md`, and the three memory files named in §0.
Do not re-derive the survey.

**What "consolidate" has to mean here.** Two consolidations were built. The one
that WORKED (`#clearance-field`, shipped, verified in game, folds −14.5%
pack-wide) replaced per-vertex pushes with one JOINT SOLVE — a different
operator. The one that FAILED (`#unified-offset`, 50% worse) merged passes by
type and lost on REACH: inflate touches 75% of a shape's vertices, a floor 5.6%.
So a collapse means "solve jointly", never "call once". And the damage ledger's
rule stands: a per-stage improvement is not a result — `SURFACE_WARP_FIELD` cut
warp's stretch 77% and the SHIPPED mesh came out 26% worse because conform did
more work downstream. Every item is judged at the last stage, by the gate.

---

## 0. Start of session — 10 minutes, in this order

1. Read `project_current_state` (memory), then this file, then
   `project_consolidation_plan` (memory: it holds the acceptance population's
   mod names and the machine-local paths, which tracked content may not).
2. **The in-game verdict on exe `b5ea8cf6` comes before any geometry item.**
   Ask for it if it has not been given. It covers three things at once — the
   09-05 clearance work, the TRI/BODYTRI fixes, and `#last-carrier-hold` — so if
   it is bad, bisect against `deploy_backup/CBBEtoUBE.exe.5ce7006f` first.
3. Confirm the tree: `git status --short` should list the same uncommitted
   files the memory names; `python -m pytest -q` green with the exit captured
   directly (`; echo PYTEST_EXIT=$?` — a pipe masks it); `python -m pyflakes
   src/*.py | grep -c undefined` = 0.
4. Confirm the deployed exe is the one you think:
   `Get-FileHash <ini 23\binary=> -Algorithm SHA256` → `B5EA8CF6...`.
5. Confirm free disk on C: > 5 GB. The scratchpad arms are ~650 MB each; a
   full disk produced six native crashes that looked like a code defect.
6. Set the harness environment once:
   `CBBE2UBE_CONFIG` = the settings json beside the DEPLOYED exe,
   `CBBE2UBE_MO2_INI` = the live MO2 ini, `CBBE2UBE_SKELETON_NIF` = a
   `skeleton_female.nif` (all three paths are in the memory file).
   `PYTHONHASHSEED=1` on every invocation. **All three, for the GATE as well
   as the arms**: without the config and ini the bust-gap and tip scorers
   cannot find the UBE body, their rows come back UNPARSED, and the gate fails
   by design (measured 2026-09-06 on two byte-identical arms).

---

## 1. The gate — one command, one verdict

Every A/B in this plan is two arms from ONE harness, scored by ONE command:

    python -m scripts.analysis.parity_convert <ctrl_dir> "<mods>"                  # control
    python -m scripts.analysis.parity_convert <cand_dir> "<mods>" CBBE2UBE_X=1     # candidate
    python -m scripts.analysis.acceptance <ctrl_dir> <cand_dir> [--weights-only]

`parity_convert` strips every `CBBE2UBE_*` from the shell, applies the live
settings, pins the seed, and echoes `OVERRIDE :` — grep the converter's own
`active flags (N):` line to prove the arm was armed. `acceptance` runs
`pack_census`, `bust_gap_score`, `nipple_clearance`, `zero_weight_pair_ab` and
`follow_bands` (on the files whose weights moved most), prints their full
reports, then one table. Exit 0 = pass, 1 = a row is worse or UNPARSED,
2 = empty population, 3 = an arm has a corrupt/tiny NIF (do not score it).

The rows and rules:

    folds, inverted, TRI OOB, untagged cloth, pair mismatch   candidate <= control
    BODYTRI on the body                                       candidate >= control
    bust gap body-swap / copy                                 candidate >= control − 0.005u
    bust-band penetration, per path                           candidate <= control
    tip clearance p50 / p05                                   candidate >= control − 0.005u
    pieces tighter at the tip                                 0
    zero-weight NEWLY EMPTIED (paired)                        0
    posed follow, worst median delta                          <= 0.010

Current build on the 184-NIF acceptance population (both arms identical):
folds **15974**, inverted **1937**, bust gap **+0.682** body-swap / **+0.194**
copy, pen **12**, tip **1.244 / 0.454**, newly emptied **0**. Those are the
numbers a control arm should reproduce before you trust a candidate.

**Validated on identical arms 2026-09-06 (evening).** The gate's first
end-to-end run, on two arms of the SAME code, failed on `posed follow` with
1093 weight rows differing — a real defect, not the gate: the pool converted
a `_0`/`_1` pair on two workers while they share one physics XML, so a `_1`'s
weight passes could read that XML mid-rewrite and reweight a collider (five
`_1` collider shapes bimodal per run, worst 0.849, 0 vertices). Fixed in the
pool as `#pair-unit-dispatch` (`docs/worklog/PAIR_UNIT_DISPATCH_2026_09_06.md`):
two fixed 16-worker arms and a `--workers 1` serial arm are byte-identical
over all 296 files under `meshes/`. The gate itself, re-run on the fixed pool
arm against the serial arm with the full environment: **exit 0, every row
parsed and equal** — folds 15974, inverted 1937, BODYTRI 56/56, bust gap
0.682 / 0.194, pen 12 / 0, tip 1.244 / 0.454, tighter 0, newly emptied 0,
vertices moved 0, weight rows 0 of 0, posed follow skipped because no row
differs. That is what an identical-arm run looks like. Consequences for this
plan:

* `parity_convert ... --workers 1` exists as the reference mode; it is no
  longer required for weight A/Bs, since the pool now equals serial.
* Any pool-scale WEIGHT number measured before that fix is contaminated at
  the ~1000-row / 0.85 level on collider shapes. `#last-carrier-hold`'s
  "2771-row cascade" and its "250 rows" were both inside that noise; the
  design stands on its rule. Re-measure before citing any earlier row count.
* The fix is in source only; it ships with the next exe (with the
  `_complete_weight_partners` refresh). Until then the PACK is built by the
  racing pool: a verdict on `b5ea8cf6` cannot be pinned on a collider's leg
  weights without checking which outcome that pack got.

Geometry items additionally need: `morph_sweep.py` (the morph axis, which
bind-pose numbers cannot see — it was exactly what rejected the early-clearance
consolidation after the bind-pose numbers passed) and the in-game report.

---

## 2. Items, in execution order

Effort: S < ½ day, M ≈ 1 day, L ≈ 2–3 days, XL = its own build.

### Step 0 — DONE 2026-09-06: the pool must equal serial before any weight item

**`#pair-unit-dispatch`** (`src/auto_convert.py`: `_pair_units`, `_run_unit`,
`_NifPool._run_parallel`). Every weight variant of a base is one unit on one
worker, in the serial order (`_1`, `_0`, no-suffix). Found by the gate itself
on identical arms; proven by three-way per-shape diffs (one odd arm per shape,
the other two byte-identical) and closed by pool == serial over 296 files.
Tests: `tests/test_pair_unit_dispatch.py` (6, one with real spawned workers).
Ships with the next exe. Nothing per-file changed, so it is byte-identical
against SERIAL and not against a pre-fix pool arm — do not "A/B" it that way.

### Step 1 — byte-identical work, ships with any verdict cycle

**E1. Flag-surface census and retirement.** `python -m scripts.analysis.flag_surface`
(**CORRECTED 2026-09-09: the surface is 152 flags, not 138 -- the census could
not see 14 of them, see section 26. After the one retirement in section 25:
138 bound in the monolith, 75 kill switches, 57 unreachable.** The pre-09-09
figures below are the monolith-only count: 138 boolean flags, 89 ON / 49 off,
76 in kill-switch form, 58 unreachable from
the GUI, 16 of those default-off opt-ins -- resolved from the code on
2026-09-06; the first draft of the census read 122 because it could not see the
16 private-prefixed flags, which is its own lesson). Retire a flag only when ALL hold: default ON; an
in-game verdict on record for the feature; the switch unused since; the OFF
branch reproduces a measured-WORSE state. Each retirement = delete the `else`
branch, its tests, its `Setting` row; prove byte-identical on a BUST piece and a
CHAIN piece with `node_parity` (a vertex diff is blind to chain passes). Judge
each candidate against `project_flag_audit_2026_08_18` (17 verdicts) and
`project_disproven_toggles_removed` (what was already cut and why). Also decide
the 20 GUI bools that are default-off, unticked, and carry tooltips citing
measured wins: promote or retire, never leave. Effort M. Gate: byte-identical.

**D1. Memo digests recomputed per call.** `nif_convert_trigen.py:107`
`_cached_body_morph_differential` (24 calls, 1.5s on a 31.8s piece) and `:168`
`_cached_body_morph_amplitude` (63 calls, 0.7s) key on `_body_array_digest(...)`
of a 29298×3 array, so every "hit" first hashes the body. Cache the digest by
array identity for the piece. Effort S. Gate: byte-identical (golden), then the
profile: `python -m cProfile -o piece.prof scripts/convert_one_armor.py ...`.

**D2. `_bust_morph_chord_req`** (`nif_convert_fitgeom.py:1871`, 10 calls, 2.1s):
check whether the body-side morph stack work is recomputed per shape. Effort S.
Gate: byte-identical.

Together D1+D2 are ~10% of a body-swap piece. **RETRACTED 2026-09-08 -- see
section 24. BOTH are no-ops: D2 had nothing to fix, and D1's 1.5s + 0.7s are
the cached functions' own compute, while the hashing it blamed is 0.035s of a
31.2s piece. The caches HIT: 22 of 24 and 35 of 36 calls.** Do NOT chase: body-reference
discovery (11% here, amortised once per worker in the pool), NIF I/O (6%, and
one-open-many-passes loses per-pass atomicity), the `ChainGuard` casts (already
the consolidated form).

### Step 2 — A1, the root of the third oscillation. Its own build, its own verdict.

**A1. Fix conform's target.** `conform_to_source_standoff` reads the authored
standoff along the SOURCE body's stored normals, which BodySlide ships all-zero
(100% of named inline bodies in the modlist), so it aims every vertex at 0.
It is the most-cancelled pass on the body-swap path (moves 0.70u, keeps 0.32)
BECAUSE downstream passes correct a wrong target; the ledger shows the fix
takes it from creating 476 penetrations / 1831 stretched edges to 46 / 540, and
the in-game verdict on the one piece it was tried on was good ("pauldrons
work"). The constants were "tuned over dozens of in-game cycles WITH the zeroed
normals", so the flip and the retune are one change.

* Where: `src/nif_convert.py:1963` `_SRC_NORMAL_FIX` (default False → True);
  retune candidates `CONFORM_BLEND_TIGHT` (`fitgeom.py:360`, 0.3),
  `CONFORM_BUST_CLEARANCE` (`:48`, 0.9), and `conform_to_source_standoff`'s
  `tight_standoff 1.0 / loose_standoff 4.0 / max_pull 4.0` (`fitgeom.py:1274`).
  Read the `_SRC_NORMAL_FIX` comment block first — it holds the ledger.
* Steps: (1) flip alone, run the gate — expect bust gap to MOVE (that is the
  arm-fired assertion; if nothing moves the flag did not reach the body-swap
  path); (2) sweep `CONFORM_BLEND_TIGHT` and `max_pull` on the acceptance
  population, judged on the gate + `morph_sweep`; (3) build, deploy, in game.
* Expected: body-swap bust gap closer to the author, penetration flat or better,
  folds flat. Blast radius ~20% of vertices modlist-wide — nothing else rides
  on this build.
* If it fails: keep the flag off, record the table, stop the chain here.
  A2 and A3 depend on it.
* Effort M (+ the verdict wait).

### Step 3 — on top of A1

**A2. Re-test `SURFACE_WARP_FIELD`** (`nif_convert.py:2186`, default False; the
warp + groove_smooth pair is the ledger's largest untouched oscillation). Its
rejection — shipped stretch +26% — was measured with conform aiming at zero;
the record itself says re-test with a true target. Effort S (the flag exists).
Gate: the acceptance table plus stretched-edge count at the LAST stage
(`damage_ledger.py`), never the per-stage flow. If it still loses, it stays off
and `groove_smooth` keeps its job (deleting it costs +15% stretch — measured).

**A3. One IDW instead of two.** `bake_preset_into_armor` (`nif_convert.py:5465`)
and `warp_armor_by_body_delta` (`fitgeom.py:895`) are both k-NN IDW resamplings
of a body-delta field (preset−template, then CBBE→UBE); phase 2 runs them back
to back. Resample the SUMMED field once. Untested per the record; expected
byte-close, not identical (the second IDW today samples at already-moved
positions). Effort S–M. Gate: golden at tolerance, then the acceptance table.
Phase-2 only (22% of the pack), so measure on body-swap pieces.

**B1. Family matches vs the full-vector match.** In
`_finalize_physics_and_motion_match` the four family passes run
(`nif_convert_weights.py:1741` leg, `:1760` arm, `:1782` spine, `:1799` twist),
then `_match_full_weights_to_body` (`:1823`) runs LAST and at strength 1.0
writes `NEW = BF` on every row it accepts — so the families' output survives
only on rows the full match REFUSES. Compute the full match's row selection
first (`_match_limb_motion_to_body`, the `_sel` masks near `:2320`/`:2425`) and
run the families only on refused rows. Families reach 18.7% of garment shapes
(keep them — "obsolete" was refuted by census). Effort M. Gate: weight rows on
accepted vertices must be IDENTICAL by construction (`--weights-only`), newly
emptied 0, `follow_bands` on the changed files. The order leg→spine→arm→twist→
full is pinned by a test and is load-bearing; do not reorder.

### Step 4 — the write-time double run, then the ride

**A4. Run the three geometry repairs once, with the body.**
`_repair_coherence_collapse`, `_uniformise_local_scale`, `_cap_short_edge_stretch`
run in the phase-2 shape loop AND again inside `_copy_shape`
(`nif_convert_writer.py:1785`) at write time, and the write-time copy has NO
BODY (`#coherence-repair-outside-body`'s recorded gap). Naive suppression of the
second run measured WORSE — it cleans up crumple the ride/order passes create
between the two runs. So the consolidation is: one run, AFTER the cross-shape
passes, WITH the body. `LAYER_ORDER_LAST` (`nif_convert.py:11918`, default
False) is the existing half-move; read its comment. Effort M. Gate: the table
(folds/inverted/pen are the rows this can move) + `morph_sweep`.

**A5. The layer ride's discard.** `_ride_layers_on_reference` ends in
`cur[mask] = SOURCE + disp_of_layer_beneath`: every ridden vertex's fit-chain
result is thrown away, and it runs after the last stage checkpoint (a 7×
perturbation amplifier — a 22-vertex stage difference became 2257 shipped).
The ORDER fix (`#authored-ride-order`) shipped; the DISCARD did not change.
Highest value, highest risk: a ride on the FITTED position instead of the source
is a design change, the romper verdict failed once, and the deleted
`RIDE_INEQUALITY`/`RIDE_BODY_CLAMP` variants must not be rebuilt. Do this only
with `morph_sweep` + an in-game plan, and only after A4. Effort L.

### Step 5 — structural, its own build

**C1. The stage table (audit F103).** Three sub-tables over a `FitContext` /
`ShapeState` — per-shape geometry, cross-shape, post-write tail — ~3,800 lines
re-expressed as ~30 stage functions. Vert-neutral by design; the verifier's
confidence is LOW. **SIZED 2026-09-09, section 28: the measured overlap
between the two entry functions is 126 shape-identical lines over 11 runs,
longest 22 -- not 3,800 lines of parallel structure. C1's case therefore
rests on the ~30-stage reorganisation, NOT on removing that duplication.** Step (0) already landed (`make_stage_hook`, commit `0181bc8`)
and pinned the one trap: the copy path must NOT acquire the body-swap path's
`_chain` ROLLBACK semantics (rollback fires only when a later verify fails, so a
naive merge passes every golden check and changes 78% of the pack invisibly).
Order copied from the current labels verbatim, no reordering. Effort XL. Gate:
golden byte-identical at tolerance 0, `test_stage_hook_contract`, PASS_MAP
regenerated. **The 12 phase-2-only passes stay phase-2-only** — giving the copy
path the ~7 body-clearance passes is a pack-wide behaviour campaign (it has to
DECIDE the anti-poke/softcloth asymmetry), not this refactor.

---

## 3. Closed — measured, do not re-run

    inflate as a FLOOR                50% worse; reach 5.6% vs 75%      project_unified_offset_field
    inflate deletion                  census refuted, inside-verts 4.1:1 project_conform_target_was_zero
    groove_smooth deletion            +15% stretch, 0 shapes better     project_pass_damage_ledger
    panel_rigidity + antipoke early   morph 1 better / 2 worse          project_panel_rigid_early_clearance
    unified-offset feathering         linear -> bit-identical           project_unified_offset_cannot_fix_folds
    per-island field reach            worse surface, +74% cost          project_clearance_field_screen
    ramped nipple exemption           tip p05 worse, pen 12 -> 465      project_authored_floors_cost_nipple_clearance
    suppress the 2nd write-time run   worse (they clean the ride)       docs/PIPELINE.md §2c
    joint layer solve / ride ineq. /  deleted 2026-08-16/17             project_disproven_toggles_removed
      ride body clamp
    "family matches are obsolete"     refuted, 18.7% reach              project_family_matches_obsolete
    conform + anti-poke merge         -0.099 interaction, no fight      project_unified_offset_field
    last-carrier hold, WIDE form      reorders incumbents; its A/B       LAST_CARRIER_HOLD_2026_09_06.md
                                        number was the pair-race noise

---

## 4. Rules that void a result (each has cost a verdict)

* Both arms from ONE harness, same settings file, same seed; the override as a
  trailing `VAR=VALUE`, proven by the `active flags` echo.
* Assert the pass FIRED, off geometry, and that its mask selected something —
  two guards this month ran and selected 1 vertex of 1700.
* Pair to source BY PATH, never by a name near a log hit.
* Judge the WRITTEN NIF and the LAST stage; a stage dump ends before the ride.
* Weight by PIECE, not by row — one many-shape garment writes a pooled median.
* Halve every shape count: `_0` and `_1` are one garment twice.
* Check the arm finished (exit 3) and the disk before believing corruption.
* Check file MTIMES against the deploy before attributing a pack defect —
  `_complete_weight_partners` used to leave `_0`s from old builds in place
  (fixed in source; the fix ships with the next exe).
* A memory's flag default is a dated claim. `python -m scripts.analysis.flag_surface`.
* Two arms of the SAME code must be byte-identical before an A/B means
  anything. When they are not, run a THIRD: a shape with exactly one odd arm
  and two identical ones is a race between writers, not noise — find the
  shared file. (The 09-06 pair race hid inside "nondeterminism" for 18 days.)
* The gate refuses to pass an UNPARSED row. If bust gap or tip come back
  `None`, a scorer could not find the body: the shell is missing
  `CBBE2UBE_CONFIG` / `CBBE2UBE_MO2_INI`. Fix the shell, not the gate.
* Bind pose is not the shipped condition: `morph_sweep`, `follow_bands`, then
  the user's report, which is ground truth and is never reclassified.
* No mod names or absolute local paths in tracked content. Population names
  and machine paths live in memory only.

---

## 5. 2026-09-06 (evening 2) — measured status, and what the plan got wrong

**THE VERDICT MODEL CHANGED.** There is no in-game verdict available any more;
the user cannot test in game and directed that the data checks carry it. Three
items in §2 (A1, A4, A5) are specified as "its own build, its own verdict".
That verdict does not exist, so each needs an explicit decision rather than a
wait. E1, D1, D2 and C1 are unaffected — they are gated BYTE-IDENTICAL.

### The split this plan did not make: does the item change the SHIPPED PACK?

    D1  memo digests        NO  — byte-identical, speed only
    D2  chord req           NO  — and there is nothing to do, see below
    E1  flag retirement     NO  — deletes dead branches
    B1  family/full match   NO  — "identical by construction" is its own gate
    C1  stage table         NO  — vert-neutral refactor
    A1  conform target      YES — ~20% of verts modlist-wide
    A2  warp field          YES — depends on A1
    A3  one IDW             YES — the plan itself says "byte-close, NOT identical"
    A4  write-time repairs  YES
    A5  ride discard        YES — needs an in-game plan it cannot have; do not start

So five of the ten remaining items cannot improve a reconvert at all. Putting
them in a reconvert cycle buys the pack nothing and risks a regression for no
product gain — ship them whenever, judged on their byte-identical gates.

### D2 is CLOSED: there was nothing to fix

`_bust_morph_chord_req` receives `morph_stack`, the body arrays and the KD-tree
from its caller; the body-side stack is `_cached_body_morph_stack`, which has
keyed on `(path, n_verts)` since 2026-08-18. Nothing is recomputed per shape —
the 2.1s over 10 calls is the genuine per-shape barycentric/morph work. (The
docstring on `_cached_body_morph_differential` still described that cache's old
path-only key IN THE PRESENT TENSE and sent this investigation looking for a
bug that was closed three weeks earlier; corrected.)

### THE PLAN'S OWN GATE COULD NOT BE RUN — half of it is now fixed

§2 gates A1, A2 and A4 on `morph_sweep`, and A2/A4 additionally on
`damage_ledger.py`. NEITHER TOOL WAS IN THE TOOLKIT. Both lived only in an
untracked 2026-08-13 handoff folder, carrying hardcoded machine paths and keyed
to a `runs/<arm>__<piece>` layout nothing else produces; that folder's own
README marks them "Not tracked, not tested". A plan cannot gate its three
biggest items on instruments that do not exist.

  * **`scripts/analysis/morph_sweep.py` — BUILT, tracked, 9 tests.** Rebuilt
    against the ARM DIRECTORIES `parity_convert` produces, so it drops into the
    same workflow as `acceptance`. Shells out to the tracked `morph_clip_test`.
    Pins the three traps: `MIN_COVER` (0/0 is not a pass), pair BY PATH, `_1`
    only (halve every shape count), every exclusion counted.
  * **`damage_ledger` — STILL NOT PROMOTED.** It reads STAGE DUMPS, and the
    standing rule is to judge the WRITTEN NIF at the LAST stage, so promoting it
    as-is would enshrine the weaker instrument. A2/A4 need a last-stage
    stretched-edge count instead. Unbuilt; this is the remaining gate gap.

### The gate's follow row was DIRECTION BLIND — fixed

`acceptance.follow` scored `max |ctrl - cand|`: a NO-CHANGE guard, correct for a
refactor, and by construction unable to tell an improvement from a regression.
It therefore fails EVERY intentional geometry change, whichever way follow moved.
Added: `follow_net_ideal`, the signed sum of `|cand-1| - |ctrl-1|` (follow 1.0 =
the garment travelling exactly with the body, so distance from 1.0 is the error)
plus better/worse cell counts, and an `--geometry` mode that judges the signed
net and drops the no-change row to info. Default behaviour is unchanged.

It earned its keep immediately — see the A1 retune below, where the blind metric
IMPROVED (0.063 -> 0.043) while the signed net went from -0.027 to +0.117.

Also fixed in both tools: a valued flag (`--band`, `--follow-top`) left its
VALUE in the positional list, so a valid invocation printed the help and exited
2 — a usage error that reads as a broken gate.

### A1 MEASURED — promising, NOT proven, does not ship

Two arms on the acceptance population, both from one harness, `active flags`
echoed (6 and 7), 184 NIFs each.

    row                       control   A1 (flip)   A1 + blend_tight 0.15
    folds                       15974      14774     14756      -7.5%
    inverted                     1937       1464      1447       -24%
    bust gap, body-swap         +0.682     +0.696    +0.697      ok
    tip clearance p50/p05   1.244/0.454  1.247/0.466  1.257/0.496 ok
    bust-band pen, body-swap       12         14        14       FAIL
    pieces tighter at the tip       0          6         7       FAIL
    posed follow, worst          0.000      0.063     0.043
    posed follow, NET ideal      0.000     -0.027    +0.117      (0.3 better)
    zero-weight newly emptied       0          0         0       ok

The arm fired: bust gap moved and 56 files moved verts, so the flag reached the
body-swap path. The fold and inverted wins are large and consistent. EVERY
regression is bust-side, which is coherent: with a true target conform reels
bust verts toward the author's TIGHTER standoff, and the pack already ships
+0.467u (p50) over the author there.

`CONFORM_BLEND_TIGHT` 0.3 -> 0.15 is the WRONG DIRECTION: folds/inverted barely
move, tip-tighter goes 6 -> 7, and net follow goes from a small improvement to a
real regression. Keep 0.3. The untried levers are `CONFORM_BUST_CLEAR` (0.9) and
`max_pull`/`tight_standoff`/`loose_standoff` — but those three are FUNCTION
DEFAULTS, not env knobs, so they cannot be swept by a trailing `VAR=VALUE` at
all. Giving them knobs (byte-identical when unset) is the prerequisite for
finishing the retune.

Caution from the record before spending arms on `CONFORM_BUST_CLEAR`: the bust
requirement is largely OVERWRITTEN downstream — `s07_antipoke` re-pushes after
conform (+0.691u on the traced piece), the `#snugness-two-pushes` lesson from
the other side.

**A1 stays default OFF.** It fails two real rows and its own file says the flip
and the retune are one change.

### The three conform knobs — ADDED (the retune was unsweepable without them)

`tight_standoff` (1.0), `loose_standoff` (4.0) and `max_pull` (4.0) were BARE
LITERALS in `conform_to_source_standoff`'s signature. Every A/B here is two arms
of one code state differing only by a trailing `VAR=VALUE`, so a constant with
no env name cannot be moved by an arm at all — which is why the A1 retune had
only one usable lever and it happened to be the wrong one.

Now module-level `_knob`s beside `CONFORM_BLEND_TIGHT`, matching the file's own
idiom, used as the parameter defaults:

    CBBE2UBE_CONFORM_TIGHT_STANDOFF   1.0
    CBBE2UBE_CONFORM_LOOSE_STANDOFF   4.0
    CBBE2UBE_CONFORM_MAX_PULL         4.0

`tests/test_conform_knobs.py` (4) pins the two ways this could be worthless: the
default drifting off the literal it replaced (which would silently change the
shipped fit for everyone), and a CALL SITE passing the parameter explicitly,
which would make an arm that sets the env measure nothing. All three call sites
take the defaults — checked at source level over the whole package, and the
guard is verified able to fire.

Direction, from the pass's own docstring: bust-band cloth is pushed OUT to at
least `bust_clearance`, so RAISING it targets both A1 failures (bust-band
penetration, pieces tighter at the tip). The trade to watch is standoff — the
pack already ships +0.467u (p50) over the author at the bust, so buying
penetration with clearance can walk into the over-standoff defect class instead.
`max_pull` is the other side of the same question: it caps how far conform may
pull a vert INWARD, so lowering it should reduce penetration without adding
outward push.

### B1 — PREMISE REFUTED. Do not build it as written.

§2 says: "`_match_full_weights_to_body` runs LAST and at strength 1.0 writes
`NEW = BF` on every row it accepts — so the families' output survives only on
rows the full match REFUSES. Compute the full match's row selection first and
run the families only on refused rows." Its gate is "weight rows on accepted
vertices must be IDENTICAL by construction".

`_FULL_WEIGHT_STRENGTH` is indeed 1.0, so the blend line really is `NEW[rows] =
BF[rows]`. But the surrounding code reads the garment's CURRENT weights `G` —
i.e. exactly what the families just wrote — in THREE places, and two of them
decide the row set:

  * `foreign = sum over G[:, j] for bones the UBE body does not have`, and
    `_sel` includes `(foreign <= 1e-4)`;
  * `_garm_arm = G[:, arm_cols].sum(axis=1)` feeds the limb-boundary gate
    `_limb_ok` (#full-weight-limb-boundary);
  * on ACCEPTED rows, `#breast-follow-keep` (DEFAULT ON since 2026-08-15)
    restores the authored breast mass from `G` and funds it from the non-breast
    bones — `_g_br = G[rows][:, _bcol].sum(axis=1)`, and where the blend zeroed
    the family outright it falls back to `G` wholesale.

So the row SELECTION is not independent of the families (making "compute the
selection first" circular), and the OUTPUT on accepted rows is not independent
of them either. Skipping the families on accepted rows changes `G`, which moves
both. It is NOT identical by construction, and the column it would move is the
BREAST — the thing `#breast-follow-keep` exists to protect after authored breast
follow was already dragged to the body's once
([[project_authored_breast_follow_lost]]).

If B1 is ever revisited it needs a different framing: measure the actual overlap
first (how many family-written rows the full match accepts), and treat any
change as a geometry/weights item with a real A/B, not as a free refactor.

### A3 — the two IDWs are on DIFFERENT reference clouds

§2 says both are "k-NN IDW resamplings of a body-delta field ... Resample the
SUMMED field once", expecting byte-close. They cannot simply be summed:

    warp_armor_by_body_delta(armor, CBBE_body_verts, delta CBBE->UBE)
    bake_preset_into_armor(armor, UBE_TEMPLATE_body_verts, user_preset - template)

The first does k-NN against the CBBE body, the second against the UBE template
body — two different point clouds, so there is no common index the two fields
can be added on. Summing needs one of them re-expressed on the other's
reference, which is a new approximation rather than a consolidation. The warp
also applies a min-standoff floor, an upper-chest damp and an area/shear
limiter AFTER its IDW, none of which fold into a single resample.

Not refuted, but materially larger than "Effort S–M" implies. Measure the
correspondence error of re-expressing the preset delta on the CBBE body BEFORE
committing to it.

### A4 — premise CONFIRMED

The write-time site (`nif_convert_writer._copy_shape`) calls
`_repair_coherence_collapse`, `_uniformise_local_scale` and
`_cap_short_edge_stretch` with `(_sv2, ov, src_shape.tris)` only — NO BODY
argument. So the second run really is body-blind, as
[[project_coherence_repair_body_blind]] records. A4's framing stands; it remains
a geometry item needing the gate + morph axis.

### THE `CONFORM_BUST_CLEAR` SWEEP IS INERT — measured, not argued

Two arms on top of `SRC_NORMAL_FIX=1`, `CONFORM_BUST_CLEAR` at 1.2 and 1.5,
`active flags (7)` echoed on both:

    arm_ctrl vs arm_bc12 :  66 of 184 NIFs differ
    arm_bc12 vs arm_bc15 :   0 of 184 NIFs differ      <== the knob does NOTHING

Raising the bust clearance floor from 1.2 to 1.5 does not move a single byte of
a single mesh. The comparison itself is sound — the same loop finds 66 differing
files against the control — so this is real inertness, not a broken check.

WHY, and the record predicted it: `s07_antipoke` re-pushes the bust AFTER
conform (+0.691u on the traced piece), and `#snugness-two-pushes` records that a
floor on one of two pushes is undone by the other. The docstring on
`CONFORM_BUST_CLEARANCE` says the same thing from the LOWERING side ("a lower
conform requirement is overwritten ... MEASURED INERT ON THE FINAL MESH"). It is
inert raising it too: by the time conform's floor could bind, the downstream
push has already put the cloth further out than 1.5.

CONSEQUENCE FOR A1: **the bust-clearance lever cannot fix A1's two failing
rows.** Do not spend more arms on it. The remaining candidate is
`CONFORM_MAX_PULL`, which acts on the other side of the trade — it caps how far
conform may pull a vert INWARD rather than pushing it out, so it is not
subsumed by a later outward push. That is the arm that matters.

### `CONFORM_MAX_PULL` IS a live lever — unlike the bust floor

    arm_ctrl vs arm_bc12 (SRC_NORMAL_FIX + bust floor)  : 66 of 184 differ
    arm_ctrl vs arm_mp20 (SRC_NORMAL_FIX + max_pull 2.0): 66 of 184 differ
    arm_bc12 vs arm_mp20                                : 24 of 184 differ

So `CONFORM_MAX_PULL=2.0` moves 24 files that the bust floor could not move at
all, which is the mechanism working as designed: the floor is an OUTWARD push
that a later pass has already exceeded, while `max_pull` caps the INWARD pull
and nothing downstream restores it. (66 = the `SRC_NORMAL_FIX` blast radius
itself, byte-level; the gate's "56 files with a vertex moved" is the narrower
vertex-only count.)

### CORRECTION: A1 ALREADY HAS A GOOD IN-GAME VERDICT

Earlier in this session A1 was described as blocked on a verdict that no longer
exists. That is WRONG, and [[project_conform_target_was_zero]] says so plainly:

> **CONFIRMED IN GAME 2026-08-16: "pauldrons work."** ... That is the in-game
> verdict this constant's own comment has been waiting for before flipping the
> default -- the retune it asks for is now unblocked.

Corroborated independently: another mod's UBE conversion of the same piece puts
the pauldron at z 109.37; ours WITH the fix lands 109.34, ours WITHOUT sits 0.6u
low at 108.76. So A1 is not waiting on a verdict — it is waiting on a clean
PACK-WIDE census, which is exactly the two bust rows measured here.

And the regression recorded on that same piece was **"verts inside the body
124 -> 127"** — the SAME class as the bust-band penetration row (12 -> 14). It
was present when the piece was judged GOOD in game. That does not make the
pack-wide row a pass, but it does mean the failing row is a known, previously
accepted characteristic of this change rather than a new surprise.

### Why the bust rows will not yield to a knob

`req = clip(BUST_FLAT_CLEARANCE + nipw * gain, BUST_FLAT_CLEARANCE, bust_clearance)`
— `bust_clearance` is only the UPPER CLIP BOUND, and the ramp peaks near 0.683
against a 0.9 default, so it never binds. `conform_to_source_standoff`'s own
comment already lists the outcome: "Five clearance knobs are now measured inert
on that piece — inflate magnitude, ANTIPOKE_FLAT_CLEAR, CBBE2UBE_BUST_CLEAR,
this ramp, and CLEARANCE_MORPH_MAX ... The gap is +0.467u over the AUTHOR at p50
across 296 shapes on 88% of them, and **closing it needs the producer changed,
not a knob turned.**"

That comment should have been read before the two bust-floor arms were spent.
The arms did add something it lacks — it says "on that piece", and the sweep
shows byte-level inertness across 184 files — but the direction was predictable.

### A1's two FAIL rows, read as DISTRIBUTIONS rather than as single numbers

The gate is zero-tolerance on both, deliberately. But the magnitudes decide
whether this is a trade worth making, and they are small:

    bust-band penetration    12 -> 14   PEN VERTS (not pieces), over 43
                                        body-swap shapes. +2 vertices.
                                        worst penetration -5.591 -> -5.649u.
    pieces tighter at tip    6 tighter / 15 LOOSER / 15 flat,
                                        median delta +0.0003u,
                                        worst single tightening -0.0709u
                                        against a tip clearance of ~1.24u (~5%),
                                        and p05 IMPROVED 0.454 -> 0.466.

Against: folds 15974 -> 14774 (-1200 folded verts) and inverted 1937 -> 1464
(-473 inverted triangles), pack-wide on the acceptance population.

The one row that is a genuine directional loss is AUTHOR FIDELITY at the bust:
over all 69 scored shapes the gap (ours - author, author p50 0.932u) widens
+0.498 -> +0.548. The gate scores that "ok" because its bust-gap rule is
ONE-SIDED — `candidate >= control - 0.005`, i.e. a bigger gap is treated as
safer from clipping. That rule cannot see over-standoff, which is a recorded
defect class ([[project_bodystock_standoff_defect]], +0.467u over the author on
88% of shapes). Same shape of blindness as the follow row had. A future gate
change should make the bust-gap row directional too — toward the author, not
merely away from the body.

Arm-fired assertion, off geometry: 27 of 69 shapes moved their bust standoff by
more than 0.01u. The flag reached the body-swap path.

### INFLATE MAGNITUDE IS INERT UNDER A TRUE TARGET TOO — hypothesis refuted

`project_conform_target_was_zero` asks for this explicitly: "The 'inflate cannot
be unified' verdict should be RE-TESTED with a true target before it is
trusted." And `CONFORM_BLEND_TIGHT`'s comment records the no-op with the reason
attached: "INFLATION_MAGNITUDE 0.7 -> 0.35 is a bit-identical no-op BECAUSE
CONFORM PULLS BACK whatever inflate pushes out." If that reason were the whole
story, fixing conform's target should have made inflate movable again.

It did not:

    arm_bc12 (SRC_NORMAL_FIX)  vs  arm_inf35 (SRC_NORMAL_FIX + INFLATION 0.35)
        0 of 184 NIFs differ

Byte-identical, pack-wide, WITH the true target. So the recorded REASON is
wrong even though the observation is right: it is not conform that cancels
inflate. The standoff is set by a FLOOR downstream — `s07_antipoke` — and a
floor absorbs any change in what arrives at it: push less with inflate and
antipoke simply pushes further to reach the same floor. That is
`#snugness-two-pushes` (inflate +0.87u THEN antipoke +0.44u) seen from the
control side.

CONSEQUENCE: bust standoff cannot be moved by conform's bust clearance, by the
nipple ramp, or by inflate magnitude. All three sit upstream of a floor that
overwrites them. **The only lever that can move it is the floor itself**, i.e.
the anti-poke clearance — which is what "closing it needs the producer changed,
not a knob turned" means in practice.

Tally of this session's four levers on A1's bust rows:

    CONFORM_BUST_CLEAR 1.2   0 of 184 differ vs A1     INERT (clip ceiling)
    CONFORM_BUST_CLEAR 1.5   0 of 184 differ vs A1     INERT
    INFLATION_MAGNITUDE 0.35 0 of 184 differ vs A1     INERT (floor absorbs it)
    CONFORM_MAX_PULL 2.0    24 of 184 differ vs A1     LIVE  <- the only one

### The gate itself is deterministic — verified for free

`bc15` was byte-identical to `bc12` (0 of 184 files), so gating both was
redundant work. It bought one thing worth keeping: the two verdict tables are
IDENTICAL line for line. Two byte-identical arms produce an identical gate
table, so a difference between two gate runs is a difference in the ARMS, never
in the instrument. Worth re-checking whenever a scorer changes.

### THE SWEEP IS CONCLUSIVE: no conform knob fixes A1's bust rows

`CONFORM_MAX_PULL=2.0` was the only live lever of the four, and its gate says it
does not touch the rows it was chosen for:

    row                        control      A1      A1+max_pull 2.0
    folds                        15974    14774    14782
    inverted                      1937     1464     1466
    bust-band pen, body-swap        12       14       14       still FAIL
    pieces tighter at the tip        0        6        6       still FAIL
    posed follow, NET             0.000   -0.027   -0.043
    tip p50 / p05          1.244/0.454  1.247/0.466  (unchanged pattern)

24 files move and neither failing row shifts by one unit, while folds and
inverted get marginally WORSE than A1 alone. So `max_pull` is not the lever
either — it caps the inward pull on verts that were not the ones penetrating.

**All four levers are exhausted.** Three (`CONFORM_BUST_CLEAR` at 1.2 and 1.5,
`INFLATION_MAGNITUDE` 0.35) are byte-identical to A1; the fourth moves geometry
elsewhere. A1's bust rows are not a conform problem and cannot be tuned out at
this layer. They belong to the ANTI-POKE FLOOR, which is the producer every
piece of the record points at.

### So what IS A1's status?

Not gate-clean, and not fixable by knob. The decision is therefore a TRADE, and
these are the actual magnitudes:

    WINS    folded verts     15974 -> 14774   (-1200, -7.5%)
            inverted tris     1937 -> 1464    (-473,  -24%)
            tip clearance    p50 +0.003, p05 +0.012; 15 pieces LOOSER
            posed follow     net -0.027 toward ideal
            morph axis       10 better / 8 worse / 24 same, median 0.000

    LOSSES  bust pen verts      12 -> 14      (+2 verts over 43 shapes)
            tips tighter         6 pieces, worst -0.0709u of ~1.24u (~5%)
            author fidelity   gap +0.498 -> +0.548 at the bust (69 shapes,
                              author p50 0.932u) -- the one directional loss,
                              and the gate's one-sided bust-gap rule scores it
                              "ok" because a bigger gap is safer from clipping

Plus an in-game verdict already on record (2026-08-16, "pauldrons work"), taken
on a piece whose own recorded regression was "verts inside the body 124 -> 127"
-- the same class as the bust-pen row, present and accepted at that time.

This is a judgement call about a change that shifts ~20% of verts modlist-wide,
so it belongs to the user, not to the gate. Recorded here; NOT flipped.

### A2 — RE-TESTED WITH A TRUE TARGET, STILL LOSES. CLOSED, stays OFF.

Step 3 said the `SURFACE_WARP_FIELD` rejection (+26% shipped stretch) "was
measured with conform aiming at zero; the record itself says re-test with a true
target". Done: `SRC_NORMAL_FIX=1 SURFACE_WARP_FIELD=1`, judged against
`SRC_NORMAL_FIX=1` alone so the conform fix does not swamp the warp effect.

    row                        A1        A1 + SURFACE_WARP_FIELD
    folds                    14774      14658    ok  (-116)
    inverted                  1464       1420    ok  (-44)
    bust gap, copy path      0.194      0.187    FAIL
    bust-band pen               14         14    ok
    tip p50 / p05      1.247/0.466  1.245/0.462  ok (marginal)
    pieces tighter at tip        0         10    FAIL
    posed follow, NET        0.000     +0.361    FAIL  (87 better / 82 worse)
    files with a vert moved      -        144    (A1 alone moves 56)

It buys 116 folded verts and 44 inverted triangles and pays with a large net
follow regression, ten tighter tips, and a blast radius nearly 3x A1's. The
plan's own disposition applies: "If it still loses, it stays off and
`groove_smooth` keeps its job (deleting it costs +15% stretch -- measured)."

**A2 is CLOSED.** Do not re-test it again without a new mechanism; the true
target was the last open reason to doubt the original rejection, and it did not
change the answer.

---

## 6. THE INFLATE ABLATION KNOB DOES NOT ABLATE (2026-09-06, evening 4)

Chasing why `INFLATION_MAGNITUDE=0.35` came back byte-identical, against a code
comment that puts the whole +0.637u standoff on `inflate`, turned up a broken
instrument rather than a broken hypothesis.

`inflate_armor_outward`, adaptive path — and it is ALWAYS the adaptive path,
because `ADAPTIVE_CLEARANCE_ENABLED` is a hardcoded `True` and the OSD amplitude
map is always found:

    cap          = max(float(magnitude), float(morph_max))
    per_vert_mag = clip(BASE + MORPH_FACTOR * amp, BASE, cap)

`magnitude` enters ONLY through that `max`. With `morph_max` 1.1, every value at
or below 1.1 drops out of the arithmetic completely:

    magnitude 0.0  -> cap 1.1 -> clip(0.25 + 0.20*amp, 0.25, 1.1)
    magnitude 0.35 -> cap 1.1 -> IDENTICAL
    magnitude 0.7  -> cap 1.1 -> IDENTICAL      <- the shipping default
    magnitude 1.5  -> cap 1.5 -> different      <- BELT only

Three consequences, all of which invalidate work already in the record:

1. **`CBBE2UBE_INFLATION_MAGNITUDE` cannot ablate the pass.** Its own comment
   said "0 = disable" and offered itself as the one-command ABLATION lever.
   At 0 the pass still pushes every hugging vert BASE..1.1u. **Inflate has
   never actually been ablated**, so "inflate is irreplaceable" and every
   "inflate magnitude is inert" line rests on a knob that cannot reach it.
2. **`_slot_aware_inflation_magnitude` is a no-op on 4 of its 5 branches.**
   SLOT49 0.5, HANDS_FEET 0.6, SKIRT 0.7 and the 0.7 default are all below
   morph_max, so they produce identical per-vert magnitudes. Only BELT (1.5)
   binds. A per-slot tuning surface that tunes one slot.
3. **The real producer of pack-wide standoff is `BASE` (0.25) and
   `MORPH_FACTOR` (0.20)** — and neither had an env name, so no arm in this
   project's history could move them. That is why every attempt to close the
   +0.467u author gap failed: the levers reached for were all downstream
   spectators.

Fixed: both are now `CBBE2UBE_CLEARANCE_BASE` and
`CBBE2UBE_CLEARANCE_MORPH_FACTOR`, defaults unchanged (0.25 / 0.20).
`tests/test_adaptive_clearance_knobs.py` (10) pins the trap itself — that 0.0,
0.35 and 0.7 give the same push, that 0 does not disable the pass, that only a
magnitude above morph_max binds, and that the slot table is inert on four
branches. Both misleading comments corrected in place.

Note the shape of the mistake, because it is the third instance this session:
a knob that reads as live (it has an env name, it appears in `active flags`, it
is documented as an ablation lever) but is arithmetically unreachable. The same
shape as `CONFORM_BUST_CLEAR` (an upper clip bound under a ramp that never
reaches it) and as the follow row (a metric that could not see direction).
**Before trusting any knob result, check the arithmetic path from the knob to
the number it is supposed to move.**

### THE TWO PUSHES HAVE ONE SHARED SOURCE — which is why every knob was inert

`#snugness-two-pushes` records that standoff is `inflate` THEN `antipoke`, and
that a floor on one is undone by the other. The reason no knob could move it:
BOTH passes take their shape from the SAME three constants.

    inflate_armor_outward   per_vert_mag = clip(BASE + FACTOR*amp, BASE,
                                               max(magnitude, MORPH_MAX))
    clear_armor_outside_body   adaptive_base   = ADAPTIVE_CLEARANCE_BASE
                               adaptive_factor = ADAPTIVE_CLEARANCE_MORPH_FACTOR
                               adaptive_cap    = ADAPTIVE_CLEARANCE_MORPH_MAX
                               req = clip(adaptive_base + adaptive_factor*amp, ...)

So the full tally of why each attempted lever did nothing:

    INFLATION_MAGNITUDE      never reaches the arithmetic (<= MORPH_MAX)
    ANTIPOKE_FLAT_CLEAR      on the adaptive path it survives ONLY inside the
                             bust zone, as the floor of `bust_req`; outside the
                             bust the requirement is BASE + FACTOR*amp
    CONFORM_BUST_CLEAR       an upper clip bound on a ramp that peaks below it
    CONFORM_MAX_PULL         caps INWARD pull; does not touch either push
    BASE / FACTOR            move BOTH pushes at once -- and had no env name

That is the whole "two pushes" problem in one line: they are not two independent
tunings that fight each other, they are one tuning applied twice. Lowering BASE
and FACTOR lowers inflate's push AND the anti-poke floor that would otherwise
restore it, which is exactly what a single-push knob could never do.

The over-standoff experiment (`CLEARANCE_BASE=0.15 CLEARANCE_MORPH_FACTOR=0.10`,
sized from the measured +0.449u breast excess) is therefore the first attempt at
this defect that can actually move it. Its failure mode is equally clear: the
ramp exists to buy morph headroom, so the gate's pen/tip rows and `morph_sweep`
are the counter-metrics that decide whether the trade is real.

#### Prediction, written BEFORE the arm was scored

From the pass's own arithmetic at the breast (measured amp mean 3.48):

    control    clip(0.25 + 0.20*3.48, 0.25, 1.1) = 0.946u of push
    candidate  clip(0.15 + 0.10*3.48, 0.15, 1.1) = 0.498u
    reduction                                      0.448u

Measured bust standoff p50 is 1.381u against an AUTHOR p50 of 0.932u, i.e. a
gap of +0.498u. If the inflate/anti-poke push is the producer, that reduction
should land the gap near ZERO:

    PREDICTED   bust gap p50   +0.498  ->  roughly +0.05
    PREDICTED   bust p50        1.381  ->  roughly 0.93 (the author's own value)

Counter-metrics that decide whether the trade is acceptable, not whether the
model is right: bust-band penetration and tip clearance (bind pose), and
`morph_sweep` (runtime growth, the reason the ramp exists at all).

If the gap does NOT move by roughly this much, the model is wrong and something
downstream re-establishes the standoff -- in which case find THAT before
turning anything else.

#### Why the downside is bounded: the AUTHORED floor catches it

`_authored_floor_amp_room` allocates the authored floors' headroom by the SAME
rule -- `ADAPTIVE_CLEARANCE_BASE + ADAPTIVE_CLEARANCE_MORPH_FACTOR * amp` -- and
both `#authored-inflate` and `#authored-antipoke` are ON in the live recipe:

    floor = max(authored, ARMOR_TO_SKIN_BUFFER + amp_room)

At the bust that comment measures the floor at 0.939u against an independently
measured authored standoff of 0.948u over 38 shapes. So when BASE/FACTOR come
down, `amp_room` shrinks and the `max` hands the floor to `authored` -- the
push falls toward the AUTHOR'S OWN standoff and stops there, rather than
continuing down into the body. The knob and the safety net share a rule, so
lowering the knob does not disarm the net.

That is the structural reason to expect convergence toward the author rather
than a clipping blowout, and it is checkable: if the gap lands near zero AND
penetration/tip hold, the authored floor did its job.

#### RESULT — the prediction was WRONG on magnitude, and that is the finding

`CLEARANCE_BASE=0.15 CLEARANCE_MORPH_FACTOR=0.10`, `active flags (7)` echoed,
132 of 184 NIFs differ (the largest reach of any lever tested this session).

    row                      control   candidate
    folds                      15974      15164    -810  (-5.1%)
    inverted                    1937       1718    -219  (-11%)
    bust-band pen, body-swap      12         10    BETTER
    posed follow, NET          0.000     -0.377    110 better / 53 worse
    bust gap, body-swap       +0.682     +0.598    CLOSER to the author
    bust gap, copy path       +0.194     +0.181    CLOSER to the author
    tip clearance p50/p05  1.244/0.454  1.172/0.371
    pieces tighter at tip          0         28

PREDICTED gap +0.498 -> ~+0.05. ACTUAL +0.498 -> +0.446. The arithmetic removes
0.448u of push at the breast; the SHIPPED standoff moved ~0.05-0.08u. The
authored floors absorb roughly 90% of it -- the mechanism predicted, nowhere
near the magnitude predicted.

**So the ramp is NOT the main producer of the over-standoff.** Halving BASE and
FACTOR closes at most ~18% of the +0.467u author gap. The remaining ~0.6u comes
from something else, and this is the first measurement that BOUNDS it: whatever
produces the residual, it is not `ADAPTIVE_CLEARANCE_BASE/FACTOR` and it is not
any of the four levers tested before them. That is a real narrowing of the
search and it cost one arm.

**THE GATE FIRED BACKWARDS ON ITS OWN GOAL.** `bust gap` is ruled
`candidate >= control - 0.005`, so CLOSING the author gap -- the entire point of
the over-standoff work -- scores FAIL on both paths. This is the one-sided row
flagged earlier in this session, now caught firing against the intended
direction. It must become directional (distance from the AUTHOR, signed) exactly
as the follow row was, or every future author-fidelity result will read as a
regression. Until then, read that row by hand.

The genuine cost is the TIP: p50 1.244 -> 1.172, p05 0.454 -> 0.371 (-18% at the
worst 5%), 28 pieces tighter. That is the morph headroom the ramp exists to buy,
being spent. Whether it becomes visible clipping is what `morph_sweep` decides.

#### THE MORPH AXIS REJECTS IT — and that is the real answer to over-standoff

`morph_sweep`, bust band, 90 `_1` meshes, 0 unpaired, 48 excluded for thin band
coverage, **42 scored**:

    better  9   worse 18   unchanged 15   median +0.0135
    worst  +3.581 clip%   (a sleeved cuirass 41.927 -> 45.508)
    next   +2.122, +1.546, +1.386, +1.386, +1.111 ...
    best   -0.920

For comparison, A1 on the same axis: 10 better / 8 worse, median 0.000, worst
+0.349. This arm is twice the regressions and ten times the worst case.

So the bind-pose gate looked strong -- folds -810, inverted -219, penetration
12 -> 10, posed follow net -0.377, author gap closer on BOTH paths -- and the
RUNTIME axis says the pack clips worse on 18 of 42 scored pieces. That is
exactly the failure this plan warns about: "the morph axis ... was exactly what
rejected the early-clearance consolidation after the bind-pose numbers passed".

**CONCLUSION, and it reframes the whole over-standoff class:** the +0.467u the
pack ships over the author is NOT mostly waste. A large part of it is morph
headroom doing its job -- the body grows at runtime and the ramp is the
insurance. Lowering the ramp buys bind-pose fidelity and pays in runtime
clipping, one-for-one.

So `#bodystock-standoff-defect` cannot be closed by turning the ramp down at
all, at any setting that keeps the wins. A real fix has to reduce standoff
WITHOUT reducing headroom, which means it must be selective -- lower only where
the body does NOT grow (the amplitude map already measures exactly that:
butt/back/thigh amp <= 0.85 and 0% cap-clipped, while breast/belly/sternum are
20-72% clipped). A global cut is the one shape of fix that cannot work.

**REJECTED: `CLEARANCE_BASE=0.15 / MORPH_FACTOR=0.10`.** Do not ship it. The
knobs stay (they are the only handles on this pass and they are needed to prove
exactly this), defaults unchanged.

#### THE RAMP IS ALREADY ZONE-SELECTIVE — move its two terms separately

The rejected arm cut BASE and FACTOR together, which spends morph headroom
everywhere. But `clip(BASE + FACTOR*amp, BASE, cap)` separates cleanly:

    BASE    dominates where amp ~ 0 -- the STATIC zones. Measured on the UBE
            body (the ADAPTIVE_CLEARANCE_MORPH_MAX comment): butt / back /
            thigh sit at amp <= 0.85 and are 0% cap-clipped. The body does not
            grow there, so clearance held there is not insurance -- it is just
            distance from the author.
    FACTOR  is the slope serving the GROWING zones: breast / belly / sternum,
            20-72% cap-clipped. THIS is the morph headroom, and the rejected
            arm is what spending it looks like.

So the zone-selective fix the morph result argues for does not need new code --
it needs the two terms moved separately, which is possible for the first time
now that both have env names. `CLEARANCE_BASE=0.15` with FACTOR left at 0.20
reclaims standoff exactly where the body cannot grow into it.

PREDICTION, written before the arm: the author gap closes by less than the
0.084 the double cut bought; tip clearance moves less than -0.072/-0.083; and
the morph axis comes back roughly flat rather than 9 better / 18 worse. If the
morph axis regresses anyway, BASE is reaching the growing zones too and the
ramp cannot be split -- which would itself be worth knowing, because it would
mean the static/morph zone separation the amplitude map reports does not
survive into the push.

#### BASE-ONLY (0.15, FACTOR left at 0.20) — bind-pose result

`active flags (6)`, `CLEARANCE_BASE=0.15` present and `CLEARANCE_MORPH_FACTOR`
absent, 106 files moved, 39 of 69 shapes moved bust standoff.

    row                    control   BASE-only   (double cut, for scale)
    folds                    15974      15455    (15164)
    inverted                  1937       1892    (1718)
    bust gap, body-swap     +0.682     +0.660    (+0.598)
    bust gap, copy path     +0.194     +0.204    (+0.181)
    bust-band pen              12         13     (10)
    tip p50 / p05      1.244/0.454  1.252/0.441  (1.172/0.371)
    pieces tighter              0         17     (28)
    posed follow, NET       0.000     +0.028     (-0.377)

Two of the three predictions hold: the author gap closes less (0.022 vs 0.084)
and the tip costs less (17 pieces vs 28, p05 -0.013 vs -0.083). But the WINS
shrank faster than the COSTS did, and two rows go the wrong way relative to the
double cut -- penetration 12 -> 13 (the double cut IMPROVED it to 10) and posed
follow net +0.028 (the double cut was -0.377).

So splitting the ramp does not buy a free region on the bind-pose side: cutting
BASE alone gives about two thirds of the fold win, a quarter of the author-gap
win, and turns two rows that the double cut had improved. The morph axis is what
decides whether it is nonetheless the SAFE half.

#### BASE-ONLY morph axis — the ramp CANNOT be split with these knobs

    arm                       better  worse  unchanged  median   worst
    double cut (BASE+FACTOR)      9     18         15  +0.0135  +3.581
    BASE-only                    10      9         23  +0.0000  +1.469
    A1 (for scale)               10      8         24   0.000   +0.349

BASE-only halves the morph damage of the double cut, so the prediction was
directionally right -- and WRONG that it would be flat. It still regresses 9 of
42 scored pieces at up to +1.469 clip%.

THE REASON, and the prediction's error was structural: in
`clip(BASE + FACTOR*amp, BASE, cap)`, **BASE is an additive offset on EVERY
vertex**, not a static-zone term. It raises clearance in the growing zones as
well, just less steeply than FACTOR does. The amplitude map's clean separation
(butt/back/thigh 0% cap-clipped vs breast/belly/sternum 20-72%) describes where
the CAP binds -- it does NOT mean the two terms address disjoint vertex sets.
So there is no combination of these two knobs that reclaims static-zone standoff
without spending morph headroom.

**BOTH SETTINGS REJECTED. Nothing shipped.** BASE-only additionally has the
weaker bind-pose case: two thirds of the fold win, a quarter of the author-gap
win, penetration 12 -> 13 (the double cut improved it to 10) and posed-follow
net +0.028 (the double cut -0.377).

WHAT A REAL FIX WOULD TAKE, now specified by measurement rather than guessed:
the BASE term has to be AMPLITUDE-GATED in the pass itself -- a static floor
that ramps DOWN as amp rises, so that low-amp verts lose clearance and high-amp
verts keep every unit of it. That is a code change to
`inflate_armor_outward` / `clear_armor_outside_body`, not a knob, and it should
be judged by exactly this harness: the acceptance gate for bind pose, and
`morph_sweep` for the axis that rejected both attempts here.

Cost of this whole line: 4 arms. Value: the over-standoff class is no longer a
mystery -- it is morph headroom, its producer is identified, its knobs exist for
the first time, and the shape of the only fix that can work is specified.

---

## 7. Cleanup pass, 2026-09-06 night (during the reconvert)

Behaviour-neutral unless stated. Suite green throughout; substantive pyflakes
across `src/`, `scripts/` and `tests/` went **9 -> 0**.

### Real defects, not tidying

* **TWO DEAD KILL SWITCHES.** `CBBE2UBE_NO_REAR_STANDOFF` and
  `CBBE2UBE_NO_CALF_STANDOFF` were applied in `nif_convert.py` as
  `if _flag(...): REAR_STANDOFF = 0.0` -- but the constant is IMPORTED from
  `nif_convert_fitgeom`, and `clear_armor_outside_body` binds it as a PARAMETER
  DEFAULT at fitgeom import time. Rebinding this module's name reached nothing.
  Proven with both env vars set: `nc.REAR_STANDOFF` 0.0 while
  `fitgeom.REAR_STANDOFF` and the pass default stayed 1.0 (calf 0.0 vs 0.6).
  Now applied at the DEFINITION; 4 tests including a guard that fails if any
  module rebinds them again. **The rule: a constant that moves modules takes its
  switches with it.** Neither switch appeared in `flag_surface` either -- the
  census binds NAMED constants and these were bare `if _flag(...)` blocks.
* **A stale "it is unbuilt" claim.** `CONFORM_BUST_CLEARANCE`'s comment said the
  narrower-nipple-ramp fix "is unbuilt"; it shipped weeks ago as
  `#nipple-ramp-sharpness` / `BUST_NIPPLE_SHARPNESS`, and `_conform_to_body`
  quoted the stale line as current. Both corrected.
* **A test that could not prove its own point.** `test_adaptive_clearance` read
  `factor` and never used it, so nothing checked the CLAMP actually bound --
  `push_m == cap` could have been a coincidence. Now asserted.
* **A mis-named metric.** `collect_fit_dataset`'s `edge_mean` averages ONE edge
  per triangle (corner 0->1), not all three. Documented, deliberately NOT
  changed: every stored row already holds that definition.

### The third missing tool, and the root cause

`exe_parity_convert` -- cited by `AUDIT_2026_09_01.md` as "rule 5", the standard
proof step, for finding after finding -- existed only in
`scratchpad_handoff_2026_08_13/`, which is **GITIGNORED**. So it ran fine on one
machine and existed nowhere else. Promoted to `scripts/analysis/`, machine paths
resolved from the live ini, 8 tests.

`tests/test_docs_reference_real_tools.py` now fails when a doc tells the reader
to run something not in the repo, and separately when a named tool resolves INTO
a scratchpad. It immediately found five more references left behind by the move
into `scripts/analysis/` (`armor_clip_diag`, `collect_fit_dataset`,
`collect_penetration_census`, `underbust_census`, `exe_parity_convert`) -- all
corrected. It tests EXISTENCE, not `git ls-files`: this tree is deliberately
uncommitted for long stretches, so index membership would fail for every new
tool and prove nothing.

### Audits that came back CLEAN (worth knowing, so nobody re-runs them)

* **141 flags, ZERO stale default claims.** Every "default ON/OFF" comment
  agrees with the value `flag_surface` resolves from code. The five apparent
  hits were one comment block documenting the flag ABOVE the binding.
* **271 knob/flag bindings, ZERO never-referenced.** There is no blunt-dead
  knob; the `INFLATION_MAGNITUDE` defect is the subtler ARITHMETIC kind, which
  needs per-knob reasoning rather than a scan.
* **No weak test assertions.** The four tests that looked assert-less use
  `numpy.testing.assert_*`; the rest are legitimate must-not-raise smoke tests.

### One left open

49 tests assert ONLY inside a loop over a discovered collection, so they pass
vacuously if it is empty -- "0/0 is not a pass" turned on the suite itself. Most
are safe (fixtures and module-level literals that cannot empty). A population
floor was added to `test_pass_map.test_both_entry_points_are_covered`, which
guards a GENERATED doc and is exactly the kind that rots. The rest are recorded
rather than churned; the audit is `scratchpad/vacuous_test_audit.py`.

## 8. RECONVERT RESULT + a new class: 54 garments whose `_1` half has DEAD sliders

The 2026-09-06 reconvert finished: 3672 of 3673 NIFs rewritten (the 1 is
covered below and is not stale). Both standing predictions were checked.

### The two predictions

| prediction | result |
|---|---|
| zero-weight bones 6 -> 2 | **EXACT.** `verify_zero_weight_bones.py --all` over 3673 meshes / 9652 shapes: **2** bones, **1** shape, both breast bones. The 4 that sat on stale files are gone; the 2 real ones remain. |
| `_0`/`_1` pairs >1 DAY apart 11 -> 0 | **11 -> 1, and the 1 is a FALSE POSITIVE.** Its halves are BYTE-IDENTICAL (same md5). |

**The metric was wrong, not the fix.** `_complete_weight_partners` refreshes a
filled partner only when the content actually differs -- "already a current
copy; leave the mtime alone". A pair can therefore be weeks apart on disk and
current in content, and an mtime-only row reports a permanent false positive
that no run can clear and that gets re-investigated every time. The census row
now compares BYTES and reports the identical ones as a counted exclusion:

    pairs >1 DAY apart AND differing           : 0   OK
      excluded: >1 day apart but BYTE-IDENTICAL: 1   (current copies the
                                                      refresh deliberately
                                                      left alone)

### The new class: the weight pair shares one TRI, but not its shape names

Measured over the written pack, whole population, every exclusion counted:

| | pairs |
|---|---|
| `_0`/`_1` pairs examined | 1536 |
| sharing ONE tri | 1532 |
| excluded, no BODYTRI on either half | 4 |
| halves whose shape names AGREE | 1482 |
| **halves whose shape names DIFFER** | **54** |
| of those, `_1` loses EVERY morph | 42 |
| of those, `_1` loses SOME morphs | 12 |
| `_0` loses anything | **0** |

Authors routinely name the two halves' shapes differently -- `BodyF_0`/`BodyF_1`,
`BootsF:0`/`BootsF:1`, `Robe_0`/`Robe_1`, `Dress`/`Dress1`. We generate ONE tri
per garment from the `_0` half (correct, and measured -- see
`_tri_is_owning_variant`) and point BOTH halves at it. The `_1` half then names
nothing in the tri it points at, so its sliders move nothing.

**Where it gets through.** `_tri_fits_variant` is the guard that stops a NIF
pointing at a tri that does not fit it -- and it short-circuits the weight pair:

    if (_variant_suffix(stem) in ("_0", "_1")
            and _variant_suffix(owner.stem) in ("_0", "_1")):
        return True                      # the weight pair, by design

Its docstring states the assumption: "`_0` and `_1` are the same mesh at two
weights and share the TRI by design". That is true of the GEOMETRY and false of
the SHAPE NAMES in 3.5% of pairs. The guard checks per-shape vertex counts for
every other variant and checks nothing here.

**Why `_1` is the half that matters.** `_complete_weight_partners`' own comment
records that the common case is actors near weight 100, i.e. the `_1` half.

**The fix is viable and the precondition was measured, not assumed.** For 53 of
the 54, the halves have the SAME shape count and the SAME per-shape vertex
counts, so one delta table serves both names: emit each morph table under the
partner's name as well, in the one tri. No new files, no BODYTRI string change.

The 54th (`shape count 5 vs 4`) is blocked, and is the SAME piece the census
already reports under `pairs with a vert-count mismatch`. One piece, two
symptoms; it needs its own look and must not be folded into the class fix.

**NOT YET BUILT.** It changes shipped tri bytes, so it needs a build, a deploy
and a reconvert of at least the affected pieces. Verdict owed before that.

### Also standing from this pack

* `pairs with a vert-count mismatch : 1` -- one cuirass, 5 shapes vs 4. The
  census names it; it is the same piece as the blocked 54th above.
* `BaseShape shapes at another vert count : 2` -- one authored shape named
  `BaseShape` at 3618 verts. Correctly flagged as not-the-body.
* 4 pieces still ship a `SkirtCol`, 0 of them tagged ground/body: the
  ground-donor defect is gone, 4 legacy proxies remain.
* BODYTRI present on 591/591 body-slot NIFs; 0 untagged cloth shapes; 0 TRI
  offsets out of bounds; 0 BODYTRI pointing at a missing tri.

## 9. `#pair-tri-names` BUILT AND MEASURED. DEFAULT OFF, verdict owed.

The §8 defect now has a fix, wired into both convert paths, proven through the
BATCH path on real affected pieces. **Nothing is flipped**: `PAIR_TRI_NAMES`
resolves False in a clean import, and the control arm proves that costs nothing.

### What it does

`generate_armor_tri` gained `also_named={my shape -> the partner's name for
it}`, and emits each morph table a SECOND time under that name. One tri, both
halves resolve. No new files, no BODYTRI string change, no geometry.

The map is built by `nif_convert_trigen.pair_shape_aliases` -- pure, aligned BY
INDEX, and **failing closed**: different shape counts, or any index whose vert
counts disagree, refuses the whole pair, because one delta table can only serve
both names when the topology matches. A partner name that is already one of my
own shapes is dropped so an alias can never shadow a real shape's morphs.

`pair_alias_map` reads the two halves from the SOURCE side, paired BY PATH
through `variant_sources` -- the same VFS/BSA chain that resolved the file being
converted, so it sees ACROSS MODS. It cannot read the partner's destination:
`_0` owns the tri and converts first, so the `_1` destination does not exist
when the owner writes it.

### The A/B, one harness, both arms from `parity_convert`

Population: the mod carrying 5 of the 54 affected pairs, 44 NIFs, 38 tris.
`active flags (5)` vs `(6)` with `PAIR_TRI_NAMES=1` echoed -- the arm is armed.

    row                                   A (off)   B (on)
    NIFs common / byte-DIFFERENT            44/0      44/0     no geometry moves
    tris common / byte-DIFFERENT            38/5      38/5     exactly the 5 affected
    whole-tree files added or removed          0         0
    halves losing EVERY morph                  5         0     <== the fix
    halves losing SOME morphs                  0         0

The only other files that differ are the run's own report/settings (they record
the flag) and `standoff_audit.jsonl`, which is line-ORDER only -- identical as a
set, i.e. worker interleave, not content.

`pair_alias_map` was also exercised directly on the four loose source pairs and
returns exactly the expected maps (`{'BodyF:0': 'BodyF:1', 'RobeF:0':
'RobeF:1'}` and so on), and `{}` with the flag off.

### What is owed before it can be promoted

* An arm on a population covering more of the 54 (this one covered 5).
* The pack-wide prediction to check after a reconvert: **`halves whose shape
  names DIFFER` stays 54 while `pairs where a half loses its morph` goes
  54 -> 1** (the one refused pair is the 5-shapes-vs-4 piece).
* A decision. It changes shipped `.tri` bytes on 53 pieces, so it needs a
  build, a deploy and a reconvert to reach the game.

`tests/test_pair_tri_names.py` (16) pins the rule, every refusal, the emission,
that the written tri round-trips (PIRT bytes 4-5 are the shape count, which an
alias grows), that the flag ships OFF, and that BOTH call sites pass the map --
a fix wired into one of the two convert paths is a fix for half the pack.

## 10. ACCURACY PASS: three things that measured the wrong thing

### (a) THE GATE FIRED BACKWARDS ON ITS OWN GOAL -- FIXED

`bust_gap_score` prints `gap = ours - author`, SIGNED. The gate ruled it
`candidate >= control`, so an arm that moved armour FURTHER from what the
author built scored **ok**, and one that CLOSED the gap scored **FAIL**. It had
been recorded as "read this row by hand"; on the A1 retune it duly scored a
`+0.498 -> +0.548` widening as ok.

The one-sided rule had a real argument -- a bigger gap is safer from clipping --
but the gate already carries that argument as its OWN rows (`bust-band pen`,
`tip clearance`). So the gap row now judges AUTHOR FIDELITY on the MAGNITUDE:

    bust gap vs author, PER PATH    |candidate| <= |control|
      ^ signed move (per path)      info -- direction stays visible, because
                                    |gap| cannot tell "closed toward the
                                    author" from "crossed past them"

A test that asserted the OLD direction was deleted; five now pin the new one,
including the both-ways noise tolerance and the crossing-past case.

### (b) THE GATE COULD NOT SEE DEAD SLIDERS -- FIXED

The whole §8 class -- 47 NIFs whose tri names shapes the NIF does not have --
rode through every verdict this gate has ever given, because `census()` never
parsed those rows. Now judged:

    tri names NO shape the NIF has      candidate <= control
    tri names SOME shape the NIF lacks  candidate <= control
      ^ NIFs scored for it              info (the population behind them)

Both verified against REAL census output (35 / 12 / 2947 on the 09-06 pack).

Two new tests protect the reading itself, which nothing did before: a verbatim
census fixture that every gate regex must match EXACTLY ONCE, and a drift-proof
check that each regex's row LABEL still appears in `pack_census`'s source --
with a mutation control, so a rename fails the suite instead of turning up as
an UNPARSED row on an arm that cost half an hour.

### (c) `--only-mods` SENT THE READER TO THE WRONG LIST -- FIXED

The refusal said "Run `scan` or the GUI 'Refresh mod list' for the exact
names". `scan` lists mods that merely LOOK like armour; this flag filters the
plugin-driven CONVERSION candidates. A mod can be in one and not the other,
and following that message cost three arms. It now names the right list
(`list_convertible_mods`, which mirrors the filter exactly) and prints near
misses in their real casing -- on the name that actually failed, the correct
one is the first suggestion.

### Also measured, NOT changed: how blind the rollback guard is

The three `_all_extra` snapshots use the stopping accessor. Counted against the
index-enumerated reader:

    SOURCE   1200 NIFs / 5319 shapes    1 block: 865 (46 blind)
                                        2 blocks: 16 (10 blind, 62%)
                                        hidden: BODYTRI x10, unnamed x56
    OUTPUT   1200 NIFs / 2885 shapes    4 blind (0.14%)

Real, rare, and concentrated on the SOURCE side. **A correction to the record:**
the block that stops the walk does not read as `LOCKEDNORM` -- in every blind
case its name comes back `None`, because an unbuildable block has no readable
name. Still not swapped: it is a SAFETY guard, so the number that decides it is
the ROLLBACK RATE over a real convert, before and after, which needs an arm.

### (d) "NOT MEASURED" WAS BEING SCORED AS "FAILED" -- FIXED

`bust_gap_score` declines a path with fewer than 5 shapes and prints
`only N shapes -- not reported` instead of a table. The gate then found no
numbers for that path, called the row UNPARSED and FAILED the arm -- a false
FAIL on a population that was simply too small, and the exact distinction
`require_population` exists to keep ("0/0 is not a pass" cuts BOTH ways).

Both the gap and the penetration row for a declined path now read
`SKIPPED  N shapes (<5)`. They never read `ok`, so an unjudged path stays
visible. A row that vanished for any OTHER reason still fails -- pinned by a
mutation control, and the skip note is matched against the scorer's own format
string so rewording one side breaks the suite.

### THE CORRECTED STALE-PAIR ROW, VERIFIED ON THE REAL PACK

The census was re-run end to end after the change:

    pairs >1 DAY apart AND differing           : 0   OK
      excluded: >1 day apart but BYTE-IDENTICAL: 1   (current copies the
                                                      refresh deliberately
                                                      left alone)

So the standing prediction **11 -> 0 is met**, with the one mtime-skewed pair
counted as an exclusion rather than a defect.

Also read off that run, and worth knowing before the next attribution: this
pack scores **folds 244550 / inverted 21554**, against 244463 / 21487 on the
b5ea8cf6 pack -- +87 and +67, or 0.036%. The pack was rewritten by a different
exe and the stale-partner refresh rewrote four `_0` files that had been frozen
since 08-22, so a delta of that size is expected; it is NOT a surface
regression to chase. The gate's reference numbers in `acceptance`'s docstring
are for the 184-NIF acceptance population, not this one -- do not compare them.

## 11. THE SIGN BUG, in three of the gate's four scorers

Every measured column in this project is SIGNED -- standoff, tip clearance and
follow all are, by construction. The gate captured three of them with an
UNSIGNED pattern, and an unsigned pattern does not FAIL on a negative: it stops
matching the line.

### (a) tip clearance -- blind exactly when it mattered

`nipple_clearance` prints `arm  p50  p05  min`, and a negative `min` IS the
poke-through the row exists to catch. One piece with a tip inside the body made
the whole LINE fail to match, so `p50` and `p05` went missing, BOTH rows read
UNPARSED, and the gate failed the arm with a parse error instead of the defect.

Fixed, and the worst single tip is now reported beside the percentiles AND
judged -- the percentiles can both improve while one piece goes through.

### (b) posed follow -- the SILENT one, and the worst

`follow_bands` computes

    follow = dot(garment_displacement, body_direction) / |body_displacement|

a dot product over a magnitude, so a garment travelling AGAINST the body scores
NEGATIVE. The gate's row pattern required an unsigned number, so such a line did
not fail -- **it simply did not match, the cell never entered the comparison,
and `scored` quietly shrank.** The gate reported a clean number over a
population it had trimmed, and the cells most likely to be negative are the
pathological ones. It was discarding its own worst evidence.

Pinned with the OLD pattern kept in the test as the control: if the old pattern
ever matches the sample line, the bug being pinned is not the bug that existed.

### (c) bust p50 -- the same shape, fixed pre-emptively

The same scorer's `worst` column is a minimum over the same quantity and is
printed signed, so an arm bad enough to push the MEDIAN inside the body would
have dropped the row. Costs nothing to accept a sign; counts stay unsigned.

All three now use one `_NUMCOL`, defined once beside the tolerances with the
reason written next to it. `morph_sweep`'s five columns were checked and are
genuinely unsigned (percentages and counts).

## 12. A ROOT CAUSE IN THE RECORD THAT DID NOT SURVIVE RE-MEASUREMENT

The record explained `pairs with a vert-count mismatch: 1` as the author naming
the same two shapes `HOOD`/`Body5_fem` vs `FemaleUnderwearBody:0`/`:0_1`, with
the inline-body classifier body-swapping one half. Read off the CURRENT pack:

    _0  robes, robes001, NPC R UpperarmTwist1 [RUt1], HOOD, Body5_fem
    _1  robes, robes001, NPC R UpperarmTwist2 [RUt2], BaseShape

There is no `FemaleUnderwearBody` in either half, and `Body5_fem` is a 337-vert
shape on `armsf.dds` -- an ARMS piece -- failing every inline-body gate (Z 37.2
< 70, bones 21 < 40, verts 337 < 4000, no body diffuse). The recorded cause does
not fit the data.

What IS measured: `_1` carries the injected `BaseShape` so it took the BODY-SWAP
path and `_0` did not; `HOOD`, a 1473-vert garment on `cuirassfi.dds`, is
present at `_0` and absent at `_1` -- the `#164` shape that
`_BODY_SKIN_TEXTURE_MARKERS`' own comment warns about. The third shape is named
after a DIFFERENT BONE in each half at a matching 46 verts, which is also why
`pair_shape_aliases` refuses this pair on shape count.

Only the two SOURCE halves can settle it, and they are BSA-packed
(`find_winning_nifs` scans loose files only and returns 0 for this path), so
they must be staged the way `auto_convert` does. **Until then, do not re-assert
a cause for this pair.**

### SETTLED 2026-09-08 -- AND THE RETRACTION ABOVE WAS ITSELF WRONG

The source halves did not need staging: the converter had already staged them,
and its own `_bsa_staging` tree still holds both. Read off THE SOURCE:

    SOURCE _0   robes 482, robes001 332, NPC R UpperarmTwist1 [RUt1] 46,
                HOOD 1473 (cuirassfi.dds), Body5_fem 337 (armsf.dds)
    SOURCE _1   robes 482, robes001 332, NPC R UpperarmTwist2 [RUt2] 46,
                FemaleUnderwearBody:0 1473 (cuirassfi.dds),
                FemaleUnderwearBody:0_1 337 (armsf.dds)

Both halves ship FIVE shapes and the same five pieces of geometry. The author
simply NAMED two of them differently at `_1`. **The `FemaleUnderwearBody` names
the retraction says are in neither half are in the SOURCE `_1`** -- the
retraction read them off the OUTPUT, where they no longer exist because we
consumed them. The ORIGINAL recorded cause was right; the correction was the
error. Re-measure the right file before retracting a cause.

**THE MECHANISM, read off the code.** `_looks_like_inline_body` matches
`BODY_SHAPE_NAME_PREFIXES` (`femaleunderwearbody`, `femalebody`) with
`name.lower().startswith(...)` and `return True` -- **before any geometry or
texture gate**. That is the exact opposite of the `3BA`-family branch a few
lines below it, which its own comment says needs "the name PLUS a full-body
geometry gate" because a name alone over-fires. So both the 1473-vert and the
337-vert shape are classified as bodies, stripped, and replaced by ONE injected
`BaseShape`: 5 shapes in, 4 out.

**IT IS NOT A PARITY ROW, IT IS LOST ARMOUR.** The two stripped shapes carry
`cuirassfi.dds` and `armsf.dds` -- the cuirass and the arms. The `_1` half is
the one actors near weight 100 use, and it ships without them.

**THE CLASS, measured, with its exclusion counted.** Over all 1037 BSA sources
the converter staged for this pack, 0 read failures: 26 shapes match the
prefixes, **24 correctly** (every one on `femalebody_1.dds`, a real placeholder
body) and **2 wrongly** -- and both of the 2 are this one file. Loose-file
sources are NOT in that tree and are NOT measured here.

So the separating property is already in the file: `_shape_diffuse_is_body_skin`
divides the 24 from the 2 perfectly on this population. Gating the prefix branch
on it is a CLASS fix by a measurable property, not a single-piece patch. **NOT
BUILT** -- it changes shipped geometry on the affected pieces (the `_1` half
would keep its five shapes and take the copy path), so it needs a decision, a
build and a reconvert.

## 13. LOOSE ENDS TIED (2026-09-08)

**The `#pair-tri-names` prediction now has a TRACKED checker.** The 54/42/12
numbers and the "54 -> 1" prediction were produced by a probe in a gitignored
scratchpad -- the exact trap that hid three "mandatory" tools here before.
Promoted as `scripts/analysis/weight_pair_tri_names.py` (exit 0 clean / 1
defects / 3 nothing measured), with `tests/test_weight_pair_tri_names.py` (15).

**Its own test caught a 0/0 bug in it, the same day.** The population floor was
on `pairs examined`, which counts exclusions, so a run whose every pair failed
to load printed `PIECES WHERE ONE HALF'S MORPH IS DEAD : 0   OK` and exited
clean. Floored on `pairs SCORED` now, with both counters printed and pinned
apart by a test. An earlier draft also fell back to walking whatever directory
it was handed when neither expected root existed -- a mistyped path would have
produced a confident number about the wrong files. It refuses instead.

**Regenerated:** `docs/TOOL_MAP.md` (83 tools). `docs/PASS_MAP.md` current.

### State at hand-off

    suite                 2829 passed / 2 skipped, exit captured directly
    pyflakes undefined    0
    deployed exe          91037895252D (09-06 22:20) -- PREDATES the source
    committed             NOTHING; tree UNCOMMITTED on `testing`

Every `src/` change since the deploy is behaviour-neutral at defaults:
comment/docstring corrections, two dead constants removed (proven zero
references), a DEFAULT-OFF flag, and a `--only-mods` message. The one call the
convert paths always make (`pair_alias_map`) returns `{}` and reads NOTHING
with the flag off, pinned by a test that fails if it opens a NIF.

## 14. A1 RE-SCORED THROUGH THE CORRECTED GATE (2026-09-08)

The 09-07/08 accuracy pass changed four of the gate's rows, so every verdict
given before it was given by a different gate. A1 (`_SRC_NORMAL_FIX`, resolved
from CODE as default **off**) was the one whose recorded verdict most obviously
depended on a row that has since been fixed, so it was re-run from scratch:
two fresh arms from `parity_convert` on the 184-NIF acceptance population,
`active flags (5)` vs `(6)` with `SRC_NORMAL_FIX=1` echoed, both arms exit 0,
186 NIFs each, scored with `--geometry` (A1 is an intentional geometry change,
so the signed follow net is the judged row and the no-change guard is info).

**IT DOES NOT FLIP. IT FAILS ON ONE ROW MORE THAN BEFORE.**

    row                              control   candidate   verdict   vs record
    folds                              15974       14774   ok        same
    inverted                            1937        1464   ok        same
    TRI offsets out of bounds              0           0   ok
    BODYTRI on the body                 56/56       56/56  ok
    untagged cloth shapes                  0           0   ok
    pair vert-count mismatch               0           0   ok
    tri names NO shape the NIF has         0           0   ok        NEW ROW
    tri names SOME shape the NIF lacks     0           0   ok        NEW ROW
      ^ NIFs scored for it               143         143   info
    bust gap vs author, body-swap      +0.682      +0.696  FAIL      WAS `ok`
    bust gap vs author, copy path      +0.194      +0.194  ok        same
    bust-band pen, body-swap              12          14   FAIL      same
    bust-band pen, copy path               0           0   ok
    tip clearance p50                  1.244       1.247   ok        same
    tip clearance p05                  0.454       0.466   ok        same
    pieces tighter at the tip               0           6  FAIL      same
      ^ worst single tip               0.001       0.001   info      NEW ROW
    zero-weight NEWLY EMPTIED               0           0  ok        same
    posed follow, worst median delta     0.0       0.063   info
    posed follow, net from ideal         0.0      -0.055   ok        was -0.027
    posed follow, cells better/worse        -     83 / 93  info
    files with a vertex moved               -          56  info
    weight rows differing                   -  314277 of 2308974     info

    RESULT: FAIL  (exit 1)

**THE CORRECTION MOVED A1 THE WRONG WAY, and that is the answer.** The old rule
`candidate >= control` on a SIGNED `ours - author` scored a WIDENING gap as ok;
the fixed rule judges `|candidate| <= |control|`, and A1 widens the body-swap
gap from +0.682 to +0.696. The row that had been quietly crediting A1 now
charges it. Three FAIL rows, and all three are the SAME bust-side defect the
conform sweep already proved no knob reaches: further from the author, +2
penetrating verts, 6 tips tighter.

Two rows are new evidence rather than re-runs. The dead-slider rows the gate
could not previously see are **0 / 0 in both arms**, so that class does not
touch this population and cannot explain anything here -- 143 NIFs were scored
for it, so the 0 is a measurement and not an empty set. The follow net moved
from the recorded -0.027 to -0.055 because the sign fix stopped the gate
discarding negative follow cells; it is still A1's one clean directional win.

**A1's trade is now strictly worse than the record states.** Wins unchanged:
-1200 folded verts, -473 inverted, tip p50 and p05 both up. Losses: the same +2
penetrating verts and 6 tighter tips, PLUS author fidelity at the bust, which is
now a judged FAIL rather than a hand-read note. **The call is still the user's;
nothing was flipped, and both arms were deleted after scoring.**

### The stretch row, which no verdict has ever had before

`stretched_edges` (section 15) was run over the same two arms:

    row                          control    candidate
    stretch rate p50            0.1495%      0.1589%   worse
    stretch rate p90            2.0217%      2.0468%   worse
    stretch rate max           45.2396%     52.3323%   worse (reported)
    edge deviation p50, len-wt   0.0331       0.0289   BETTER
    shapes better / worse / same          88 / 97 / 413
    population                  598 shapes = 299 garments, every exclusion counted

The count and the deviation DISAGREE, which is exactly the case the metric's own
rule covers: the count divides by the authored length and is therefore exposed
to short authored edges, and the length-weighted deviation is not. Read
together, the median shape's bulk distortion IMPROVES by 13% under A1 while the
tail of edges past 1.5x gets marginally worse on a 0.15% base, and per shape it
is a wash. It neither rescues A1 nor sinks it further.

## 15. THE `damage_ledger` GATE GAP IS CLOSED

`scripts/analysis/stretched_edges.py`, tracked, `tests/test_stretched_edges.py`
(16), wired into `acceptance` as three judged rows plus two info rows.

**Why not `damage_ledger` itself.** It reads STAGE DUMPS and counts, per pass,
edges past 1.5x their ENTRY length -- a FLOW ledger. Flow and outcome have
already disagreed outright here: `SURFACE_WARP_FIELD` cut the stretch `warp`
CREATES by 77% and the shipped mesh came out 26% MORE stretched, because
`conform` did more work downstream. The standing rule is to judge the WRITTEN
NIF at the LAST stage, and this does. The reference is the AUTHOR's own mesh,
resolved by `canonical_body.find_source` (garment names, BSA fallback), so an
edge that leaves the chain at 1.4x is counted whether one pass did it or six did
a little each.

**The four population traps it is built against**, each of which has already
produced a wrong number on this project:

* a POOLED total is carried by one big mesh -- so every judged row is a
  per-shape RATE and the pooled count is info, pinned by a test asserting that a
  pooled-only regression must NOT fail the gate;
* `_0` and `_1` are one garment counted twice -- the distinct-garment count is
  printed beside the shape count (598 shapes = 299 garments here);
* dividing by the authored length lets short edges lie (1% of one belt's edges
  inflated its raw mean by 75%) -- so a LENGTH-WEIGHTED deviation is judged
  beside the count, and a test pins that the raw mean and the weighted one
  diverge by more than 10x on a short-edge mesh;
* an unpaired population makes a missing shape look like a win -- only shapes
  scored in EVERY arm are compared, and the drops are counted and named.

Instrument checks live in the tests, both directions: source-vs-itself and a
rigid transform must read EXACTLY 0.000 (a rigidified shape is this project's
stated control), a bend must not count as stretch, and a uniform 2x scale must
read rate 1.0 and deviation 1.0. Exit 3 on an empty population -- 0/0 is not a
pass, and the mods root is resolved LAZILY so an empty arm says that rather than
crashing on machine configuration.

Exclusions on the acceptance arm, all counted: 90 body/proxy shapes (ours, not
the author's), 48 that render nothing, 8 under 64 authored edges, 6 whose author
vert count differs. **0 NIFs failed to resolve an author source.**

## 16. THE `_all_extra` ROLLBACK RATE -- MEASURED, AND THE PACK CANNOT ANSWER IT

The three rollback guards restore the pre-patch bytes and skip the patch when
`pre_extra <= post_extra` fails. Section 10 left the deciding number as "the
ROLLBACK RATE over a real convert, which needs an arm". Both A1 arms are real
converts, and their console output carries every one of those lines.

    guard                                        files reaching it   rollbacks
    butt collider   `_add_butt_collider_patch`          16              0
    bust-collider split `_split_bust_collider_shape`     6              0
    skirt proxy     `_add_skirt_collider_proxy`          0         NOT MEASURED

Identical in both arms. **22 guarded writes, 0 rollbacks.** The third guard was
never exercised on this population, so it is a counted exclusion, not a pass.

**THE PACK'S OWN REPORTS CANNOT WIDEN THIS, and reading them as 0 would be the
0/0 trap.** All 163 `conversion_report_*.txt` from the 09-06 full reconvert
contain **zero** `RESTORED original` lines -- and also zero `[butt-col]`, zero
`[bust-split]` and zero `WARN:` lines of any kind. Those reports do not capture
the converter's console at all, so their 0 is the absence of the channel, not
the absence of the event. A pack-wide rate needs a full convert with stdout and
stderr captured; until then the measured rate is 0 of 22 over 186 NIFs, and the
guard stays unswapped.

### State at hand-off (2026-09-08, second pass)

    suite                 2857 passed / 2 skipped, exit captured DIRECTLY
    pyflakes undefined    0
    deployed exe          91037895252D (09-06 22:20) -- still PREDATES the source
    arms                  both A1 arms DELETED after scoring
    committed             NOTHING; tree UNCOMMITTED on `testing`

Nothing in `src/` changed in this pass. The additions are all harness:
`scripts/analysis/stretched_edges.py`, `tests/test_stretched_edges.py` (16),
three judged rows and two info rows in `acceptance` with 10 new gate tests, and
`docs/TOOL_MAP.md` regenerated to 84 tools.

**One consequence to hold in mind before the next A1 read.** With the stretch
row wired in, A1 fails FIVE rows rather than three: the two new FAILs are the
count-based `stretch rate p50 / p90`, whose length-weighted companion PASSES.
The gate is deliberately conservative there -- "no worse on every row" -- but
the disagreement between those two rows is information, not noise, and the
metric's own rule says the count is the contaminated one when they diverge.

### STILL OWED

* A DECISION on A1, now that the row that was crediting it charges it instead.
* A DECISION on `#pair-tri-names`, and a WIDER arm first: the measured A/B
  covered 5 of the 54 affected pairs.
* A DECISION on gating `BODY_SHAPE_NAME_PREFIXES` behind
  `_shape_diffuse_is_body_skin` (section 12) -- it changes shipped geometry.
* A pack-wide rollback rate, which needs a full convert with the console
  captured (section 16).
* The loose-file half of the name-prefix class census (section 12 measured only
  the 1037 staged BSA sources).

## 17. `#pair-tri-names` -- THE WIDER ARM (2026-09-08). 48 -> 0, ZERO geometry.

Section 9 owed "an arm on a population covering more of the 54 (this one
covered 5)". Done, and it covers **48 of the 54**.

**Finding the population was itself a trap, and the fixed message caught it.**
The 54 affected pairs resolve to 8 mods -- but by whose BSA holds the SOURCE
MESH, which is NOT the conversion-candidate list. `--only-mods` on those eight
matched 0 of 162 candidates and refused, naming `list_convertible_mods()`; the
quest mods' armours are conversion candidates under the mods carrying their
PLUGINS. That refusal message was rewritten on 09-07 after the old one cost
three arms, and it did its job the first time it was tested for real.

Resolution of the 54, with nothing dropped: **54 of 54 resolved** to a
providing mod (loose or BSA), then the candidate mods carrying those plugins
were converted. Coverage was then MEASURED on the arm by the tracked checker
rather than assumed: 105 pairs scored, **48 whose halves' shape names differ**.

### The A/B, one harness, both arms from `parity_convert`

`active flags (5)` vs `(6)` with `PAIR_TRI_NAMES=1` echoed. Both arms exit 0,
1502 NIFs each.

    row                                        A (off)    B (on)
    NIFs common / byte-DIFFERENT                769/0     769/0    no geometry
    TRI files common / byte-DIFFERENT           659/48    659/48   exactly the 48
    files added or removed (whole tree)             0         0
    halves whose shape names DIFFER                48        48    unchanged
    PIECES WHERE ONE HALF'S MORPH IS DEAD          48         0    <== the fix
    checker exit                                    1         0

Every other differing file is the run's own bookkeeping: the six per-mod
reports, `conversion_report.json`, `conversion_summary.txt` and
`conversion_settings.json` -- whose entire diff is the flag being recorded
(`pair_tri_names: false -> true`, plus the env echo).

**AND ONE FILE THAT IS NOT EVIDENCE OF ANYTHING.** `standoff_audit.jsonl`
differed too, and it is NOT the flag: the sink is appended by pool workers with
no line-atomic append, so lines TEAR. Both arms have torn lines and the counts
differ (1 vs 2), MID-FILE -- line 355 of 1763 in one arm, 419 and 747 of 1764
in the other. Parsed, both arms hold the SAME 1762 records; the one record
"missing" from arm B is the record arm B tore. `audit_sink.load`'s docstring
claimed only the LAST line could be mid-write; corrected in source, with the
consequence written down: **this file cannot serve as an A/B artefact.**

### What is still owed on it

* the DECISION -- it changes shipped `.tri` bytes on 53 pieces, so it needs a
  build, a deploy and a reconvert;
* after that reconvert, the pack-wide prediction from §9 to check with the
  tracked checker: `halves whose shape names DIFFER` stays 54 while
  `pairs where a half loses its morph` goes **54 -> 1** (the one refused pair
  is the 5-shapes-vs-4 piece, which §12 now explains).

### The rollback rate does NOT widen on this population

Section 16's denominator stays at 22. These 1502 NIFs added **zero** guard
invocations: `[butt-col]`, `[bust-split]` and `[skirt-proxy]` appear 0 times in
either arm's console, and the string `collider` appears 0 times at all, while
the same logs carry 1580 `[panel-rigidity]` and 781 `[phase1-antipoke]` lines --
so the console IS being captured and this population simply has no collider
work. A counted exclusion, not a pass.

## 18. A SECOND NONDETERMINISM, FOUND BY A REPEAT CONTROL (2026-09-08)

The `#pair-tri-names` A/B on the remaining mods (section 19) came back with
**2 byte-different NIFs** where the first wide arm had 0. The flag only adds
alias tables to a `.tri`, so that could not be the flag -- and it was not.

### The control that settled it

Two arms of IDENTICAL code and IDENTICAL flags, same population, same seed:

    arm            NIFs common   byte-DIFFERENT   `[bust-split]` fires
    control 1            325           2 (vs 2)                     0
    control 2            325           2 (vs 1)                     2

The two controls differ from each other on exactly the same 2 NIFs, and the
whole difference is that `_split_bust_collider_shape` fired in one run and not
the other, on one garment's `_0` and `_1`.

### What actually diverges

Same piece, same output path, the two control arms:

    shape `Cuirass`   3985 verts, VERTEX BYTES IDENTICAL (same md5) in both
    its bone list     44 bones in the arm with no split, 36 in the arm with it
    the 8 that move   L/R Breast01-03 and NPC L/R Butt

So the geometry is identical and the JIGGLE BONES are on a different shape:
with the split they go to the hidden `CuirassCol` clone, without it they are
grafted straight onto `Cuirass`. That is the split's own design working -- the
nondeterminism is in WHETHER IT RUNS.

**A RETRACTION INSIDE THIS SECTION.** Its first draft also said "its physics
XML 22258 bytes, IDENTICAL in both arms". That file was located by BASENAME and
is a DIFFERENT piece's `cuirass.xml` -- the pack contains six of them in six
directories, and NONE of them is beside this garment. The same basename mistake
the record warns about elsewhere ("pair to source by PATH, never basename"),
made while investigating a bug.

### What IS established about the cause

* the SOURCE NIF carries **no physics link at all** -- its bytes contain no
  `HDT Skinned Mesh Physics Object` string and no `.xml` string, and
  `_read_source_hdt_xml_disk` returns None on it deterministically (3 of 3);
* so `_bust_split_xml_text` always falls through to its second branch, which
  reads the DESTINATION -- and the pass's own docstring says the destination
  pointer "does not resolve yet" at that point in the pipeline;
* `_body_conform_ref` is RULED OUT: its failure prints a loud WARN per weight
  and that line appears **zero times in any of the four arms**, while other
  worker prints from the same runs do appear;
* no pass failure was recorded in either arm (both report the same 2 unrelated
  ones), so the split did not crash -- its GATE evaluated differently;
* the SHIPPED pack has no XML beside this piece and no split clone, i.e. the
  build that produced the pack landed on the no-split side.

**What is NOT established**: which file that second branch actually resolved in
the arms where the split DID fire. It found two per-triangle colliders from
somewhere. Proving that needs instrumentation and another arm pair, and given
the basename retraction above it must not be guessed at.

### Why this matters beyond one garment

* **It contaminates A/Bs.** Any arm pair covering this piece can show a
  spurious 2-NIF difference that reads as "the change moved geometry".
* **The shipped pack's content for this piece is a coin flip** -- either the
  jiggle bones sit on the garment or on a hidden collider clone, and those are
  different physics.
* It is a SECOND, distinct source from `#pair-unit-dispatch`, which was fixed
  and verified pool-equals-serial over 296 files. That fix is not in question;
  this is somewhere else.

**The repeat control is what found it.** The first wide arm had 0 differing
NIFs and would have shipped the claim "this flag never moves geometry" without
qualification; the second population happened to contain the one piece that
flips. Run the control.

## 19. `#pair-tri-names`: THE REST OF THE 54 (2026-09-08)

A second A/B on the three mods carrying the pairs the first arm missed.
`active flags (5)` vs `(6)`, both exit 0, 444 NIFs each.

    row                                        A (off)    B (on)
    NIFs common / byte-DIFFERENT                 325/2     325/2   <-- section 18,
                                                                   NOT the flag
    TRI files common / byte-DIFFERENT            162/6     162/6
    files added or removed                           0         0
    halves whose shape names DIFFER                  5         5
    PIECES WHERE ONE HALF'S MORPH IS DEAD            5         1
    checker exit                                     1         1

**Union with the first arm: 53 of the 54 affected pairs measured, and the fix
clears all but one.** 48 -> 0 on the first, 5 -> 1 on this one.

**AND THE SURVIVOR IS THE PIECE FROM SECTION 12** -- the same garment whose `_1`
half loses its cuirass and its arms to the ungated name-prefix strip.

### A correction: the reason it survives is NOT what section 9 recorded

Section 9 said `pair_shape_aliases` refuses that pair "on shape COUNT". Read off
the SOURCE, both halves have FIVE shapes with matching per-shape vert counts, so
the alias map is BUILT and accepted -- and the checker proves it, because the
piece goes from `_1 dead: ALL` to `_1 dead: SOME`. The aliases that landed did
their job; the two that cannot are for shapes THE OUTPUT DOES NOT HAVE, because
the name-prefix strip deleted them. The shape-count refusal was read off our own
OUTPUT, the same mistake section 12 corrects.

**So the two items are one item.** Fixing `#body-name-prefix` would give this
pair its five shapes back, and `#pair-tri-names` would then clear it too --
making the pack-wide prediction **54 -> 0**, not 54 -> 1. Neither is built.

### A geometry-neutrality claim that now has a control behind it

On a third, small population (one mod, 54 NIFs) run three times -- control,
control, candidate: **control vs control 0 NIFs differ, control vs candidate 0
NIFs differ and 2 tris change.** That is the flag's claim tested against its own
repeat control, on a population where the nondeterministic piece does not fire.

## 20. ATTACKING THE ANTI-POKE FLOOR: MEASURE THE ARGMAX FIRST (2026-09-08)

Five clearance knobs have been measured INERT on this population, one arm at a
time, and each time the reason was worked out afterwards: the knob's term was
never the winner. `#clearance-term-audit` asks that question directly and
before the arm -- on a bust vertex, which term is the ARGMAX of `req` in
`clear_armor_outside_body`?

TELEMETRY ONLY, DEFAULT OFF, and proven inert twice over: the arm with it armed
is byte-identical to the control across **184 NIFs and 92 tris**, and the
control on the edited source reproduces **folds 15974 / inverted 1937** -- the
recorded control numbers to the digit.

### The result, 74 shapes with a bust band, 454 excluded, 49395 bust verts

    ARGMAX over the three FLOOR terms      verts     share
      amp-ramp    clip(BASE + FACTOR*amp)  35256     71.4%
      bust-floor  clip(FLAT + nipw*GAIN)   10335     20.9%
      morph-diff  min(BASE + differential)   3804      7.7%

    winner sits exactly ON `adaptive_cap`  24656     49.9%
    median req 1.1498u   median current standoff 0.9014u
    median authored relaxation on the bust  0.0000u

Every term was computed on every one of the 74 shapes -- no shape is missing a
term, so none of these shares is an artefact of a term not running.

### Three things this settles

**1. THE BUST FLOOR GOVERNS ONE FIFTH OF THE BAND, NOT THE BAND.**
`ANTIPOKE_FLAT_CLEAR` and `ANTIPOKE_BUST_CLEAR` can only reach 20.9% of bust
vertices, which is why `FLAT_CLEAR` moved the population p50 by 0.002u while
dominating on the single piece it was bisected on. That was recorded as
"textbook single-piece over-fit"; it is more precisely a term that wins on 21%
of the band.

**2. HALF THE BAND IS PINNED AT THE CAP, so no BASE or FACTOR knob can move it.**
49.9% of bust verts have their winning term sitting exactly on `adaptive_cap`.
For those, `CLEARANCE_BASE` and `CLEARANCE_MORPH_FACTOR` are arithmetically
incapable of changing the answer -- only `CLEARANCE_MORPH_MAX` can. That is the
whole explanation for "`CLEARANCE_MORPH_MAX` 1.1 -> 0.4 is the only cheap win",
now with the fraction attached rather than inferred from an arm.

**3. `#authored-antipoke` RELAXES A MEDIAN OF 0.0000u ON THE BUST -- and TWO
THIRDS OF THAT ZERO IS BY DESIGN.** The median alone reads as a dead floor. It
is not, and the decomposition says so (added the same day, because "relaxed
0.0000u" covers situations that want OPPOSITE work):

    exempt        31846   64.5%   the TIP EXEMPTION refused it -- BY DESIGN
    relaxed       10885   22.0%   it actually lowered the requirement
    already_out    6167   12.5%   garment already met the need -- push is 0
    floor_auth      497    1.0%   the AUTHOR is already >= our need
    floor_bust        0    0.0%   the bust ramp blocking it -- BY DESIGN
    floor_amp         0    0.0%   the morph-headroom floor blocking it
    floor_none        0    0.0%   held with no component explaining it
    other             0    0.0%
    shapes where the authored block never ran: 0 of 74

The buckets sum to 49395 -- EXACTLY the bust-vertex count, so this is a
partition and no share is double-counted.

**TWO CORRECTIONS TO THIS SECTION'S EARLIER DRAFTS.**

FIRST, it called the zero "a fixable DEFECT". It is not: `in_bust` is gated on
nipple weight > 0 and the exemption on > 0.5, so the median bust vertex here IS
a tip-exempt one. The median was reporting the exemption, not a dead floor.

SECOND -- and this is the one that closes the lead -- the draft after that put
13.5% in a single `no_bind` bucket and called THAT the defect class. Split
properly, "allowed but changed nothing" is four different answers and **none of
them is a defect**: 12.5% is a garment that already meets the requirement, so
the push is zero and no floor matters; 1.0% is the author already sitting at or
above our requirement, so there is nothing to relax toward. **`floor_amp` --
the morph-headroom term, the one that stood above every reachable value until
2026-09-05 -- blocks the relaxation on ZERO bust vertices.** So does the bust
ramp. That 09-05 fix holds, and there is no residue: `floor_none` and `other`
are both 0.

**CONSEQUENCE: `#authored-antipoke` IS ALREADY DOING EVERYTHING IT CAN ON THE
BUST.** Every vertex it does not relax is one it is forbidden to relax (the tip
exemption) or one where relaxing would change nothing. The over-standoff is
entirely on the `req` side.

**AND THAT REFRAMES THE WHOLE ITEM.** `#authored-nipple-exempt` exists because
the author fitted over a CBBE bust and their spacing under-provisions UBE's at
the tip -- it was added after measuring 26 of 36 tip-covering pieces getting
TIGHTER with the floors armed. So the pass deliberately refuses to pull the
garment toward the author on two thirds of the band, and that refusal is
protecting exactly the over-standoff being complained about. Relaxing it trades
author fidelity at the bust against the gate's `pieces tighter at the tip` row
-- which is the same trade A1 fails on. The over-standoff at the bust is not
one bug; it is two deliberate protections (morph headroom at the cap, tip
clearance at the exemption) meeting.

### What that means for the next move, and it is a trade, not a bug

The only lever that reaches the majority of the band is the CAP -- and the cap
is the morph headroom. A global ramp cut was already measured and REJECTED
because the morph axis went 9 better / 18 worse while the bind pose improved,
and the prescription was "any fix must be ZONE-SELECTIVE". The audit says where
zone-selective has to bite to matter at all: the amp-ramp AT THE CAP, on the
bust -- which is also the zone with the most morph amplitude to reserve
headroom for (breast amplitude p50 3.944u). So the cheap version of this fix is
the version already rejected.

Three candidates the audit makes newly measurable, none built, in the order
their evidence supports:

* ~~the 13.5% `no_bind`~~ **CLOSED, MEASURED EMPTY.** Decomposed, it is 12.5%
  "the push is zero anyway" and 1.0% "the author is already looser". The
  morph-headroom floor blocks NOTHING on the bust. Do not re-open this without
  a new mechanism.
* ~~the 20.9% the BUST FLOOR owns~~ **CLOSED by section 22.** Swept at its
  MAXIMUM effect, not at plausible values: budget 0.035u per bust vert, all of
  it on the COPY path, body-swap gap unmoved to the thousandth, and taking it
  costs 8 gate rows including +12 penetrating verts and 11 tighter tips.
* **the tip exemption's 64.5%** -- MEASURED IN SECTION 23, and it is the only
  one of the three that works: 0.072u of the body-swap gap, against 0.149u of
  median tip clearance and 19 tighter tips. A DECISION, not a defect, and NOT
  tunable -- shrinking the protected region buys nothing.

**Nothing was flipped, and the audit ships OFF.** `scripts/analysis/clearance_terms.py`
reads the arm's console (`tests/test_clearance_term_audit.py`, 15). The console
is the transport because the JSONL sink tears lines under the pool (section 18).

## 21. `#body-name-prefix`: THE WHOLE POPULATION, LOOSE AND ARCHIVED (2026-09-08)

Section 12's census covered only the sources the converter had STAGED out of
BSAs and named the loose half as an uncounted exclusion. A number over one half
of a population is not a number over the population, so it was redone properly:
`scripts/analysis/inline_body_name_strip.py`, tracked,
`tests/test_inline_body_name_strip.py` (11), resolving every source the way the
pipeline does (`canonical_body.find_source`, garment names, BSA fallback) over
the whole shipped pack.

    distinct author sources read                : 3567
      not scored: no author source resolved     :   69
      not scored: output has no garment shape   :   37

    shapes matching BODY_SHAPE_NAME_PREFIXES    :   34
      from LOOSE sources                        :   10   (0 WRONG)
      from ARCHIVED sources                     :   24   (2 WRONG)

    body-skin diffuse -> CORRECT strip          :   32
    garment diffuse   -> WRONG strip            :    2   <== real armour deleted

**THE CLASS IS EXACTLY TWO SHAPES ON ONE GARMENT, PACK-WIDE.** The loose half
adds 10 prefix hits and NOT ONE wrong strip, so the earlier BSA-only figure did
not understate the defect -- it is genuinely one piece on this modlist. The
1473-vert cuirass and the 337-vert arms of that garment's `_1` half, both on
armour diffuse, both deleted and replaced by one injected body.

**The separating property holds over the whole population**: all 32 correct
strips carry a body-skin diffuse and both wrong ones do not, so gating the
prefix branch on `_shape_diffuse_is_body_skin` -- the test the detector's own
general heuristic already applies -- would fix the class and touch nothing else.

**Still NOT BUILT** (it changes shipped geometry), and it is now known to be
worth more than one garment's cuirass: section 19 shows the same piece is also
the one pair `#pair-tri-names` cannot clear, so fixing this makes that fix's
pack-wide prediction **54 -> 0** instead of 54 -> 1.

The tool floors its population on SOURCES READ, not on prefix hits: a clean pack
has zero hits and that is a PASS, while a run that resolved no source has
measured nothing. Conflating those is the 0/0 bug the pair-tri checker shipped
with for a day.

### State at hand-off (2026-09-08, third pass)

    suite                 2933 passed / 2 skipped, exit captured DIRECTLY
    pyflakes undefined    0
    TOOL_MAP / PASS_MAP   current (86 tools)
    zero-weight bones     2 on 1 shape, both breast bones on the same
                          first-person cloth shape as before -- UNCHANGED,
                          exit captured directly (a pipe masks it)
    deployed exe          91037895252D (09-06 22:20) -- still PREDATES the source
    arms                  ALL DELETED after scoring (27 arms this session)
    committed             NOTHING; tree UNCOMMITTED on `testing`

The one `src/` change is `#clearance-term-audit`, DEFAULT OFF telemetry, and it
is proven inert two ways rather than asserted: the armed arm is byte-identical
to the control over 184 NIFs and 92 tris, and the control on the edited source
reproduces **folds 15974 / inverted 1937** -- the recorded control numbers to
the digit. Plus one corrected docstring in `audit_sink.load`.

### STILL OWED

* **A1, AND NOW THE ANTI-POKE FLOOR TOO** -- the user chose "attack the
  anti-poke floor first". Sections 20, 22 and 23 measured all three of its
  levers at MAXIMUM effect. Two are empty (the authored relaxation has no defect
  in it; the bust floor cannot move the body-swap gap at all). The third, the
  tip exemption, is the ONLY thing measured this session that moves that gap --
  0.072u of it -- and it costs 0.149u of median tip clearance and 19 tighter
  tips. It is NOT tunable: shrinking the protected region buys nothing.
  **So the bust over-standoff has no free fix left, only a trade**, and it is
  the same trade A1 fails on. Both decisions are the user's and both are open.
* `src/` now carries TWO behaviour-neutral additions: `#clearance-term-audit`
  (default-off telemetry) and `CBBE2UBE_NIPPLE_GAIN` (a knob on what was a bare
  literal). Both proven inert unset by a control reproducing folds 15974 /
  inverted 1937 exactly.
* **`#pair-tri-names`** -- 53 of 54 pairs now measured, all but one cleared. The
  survivor needs `#body-name-prefix`. Decision still owed on flipping it.
* **`#body-name-prefix`** -- class now measured over the WHOLE population (2
  wrong strips of 34 hits, one garment). Decision owed; changes geometry.
* **The bust-split nondeterminism (section 18)** -- reproduced, not root-caused.
  Two fail-open exits in `_bust_split_candidates` are the suspects.
* A pack-wide rollback rate, which needs a full convert with the console
  captured. The 1502-NIF arms added ZERO guard invocations.

## 22. THE BUST FLOOR'S 20.9%: SWEPT, AND CLOSED (2026-09-08)

Section 20's second lead. The bust floor is
`clip(FLAT_CLEAR + nipw * NIPPLE_GAIN, FLAT_CLEAR, BUST_CLEAR)` and it is the
argmax on 20.9% of bust vertices, so unlike the other two levers it is reachable
WITHOUT spending morph headroom.

**`ANTIPOKE_NIPPLE_GAIN` WAS A BARE LITERAL** and therefore unsweepable -- an
A/B can only move a NAMED env, the same prerequisite that blocked the conform
retune until three knobs were added. It is now `CBBE2UBE_NIPPLE_GAIN`, default
1.5, and all three defaults are pinned by a test that resolves them in a
SCRUBBED subprocess.

### The budget, measured BEFORE any sweep arm

`#clearance-term-audit` now reports what a bust-floor knob can possibly remove:
the distance from that term down to the RUNNER-UP, on the verts where it wins.
No knob can spend more than that, whatever it is set to.

    verts the bust floor wins                 10335
    of those, pinned at BUST_CLEAR              121   (1.2%)
    median headroom per winning vert         0.0913u
    TOTAL headroom over the population       1747u-verts  (0.035u per bust vert)

**Two things fall out before an arm is spent.** The gap to the author is
+0.682u on the body-swap path, so the ENTIRE bust-floor budget is about 5% of
it. And only 1.2% of the winning verts sit on `BUST_CLEAR`, so the ceiling is
live on almost nothing -- which is a SECOND and better reason for the recorded
"`ANTIPOKE_BUST_CLEAR` is inert" than the one on file (it is not merely that the
morph term dominates; the ramp rarely reaches the ceiling at all).

### Spending the WHOLE budget, scored by the gate

One arm with `NIPPLE_GAIN=0.0 FLAT_CLEAR=0.25` -- the bust ramp removed and its
floor cut to a third, i.e. the most any bust-floor knob could ever do. Control
reproduces folds 15974 / inverted 1937 / gap +0.682 / +0.194 / pen 12 / 0 /
tip 1.244 / 0.454 exactly, so the new knob is proven inert unset.

    row                              control   candidate   verdict
    bust gap vs author, body-swap     +0.682      +0.682   ok  <== DID NOT MOVE
    bust gap vs author, copy path     +0.194      +0.132   ok  (the only win)
    inverted                            1937        1932   ok
    folds                              15974       16081   FAIL
    bust-band pen, body-swap              12          21   FAIL
    bust-band pen, copy path               0           3   FAIL
    tip clearance p50 / p05      1.244/0.454 1.236/0.418   FAIL
    pieces tighter at the tip                0          11   FAIL
    stretch rate p90                  2.0217      2.0586   FAIL
    posed follow, net from ideal         0.0        +0.07   FAIL

    RESULT: FAIL, 8 rows

**THE BODY-SWAP BUST GAP DID NOT MOVE BY A THOUSANDTH.** That is the path
carrying the +0.682u, and the bust floor cannot reach it: its 20.9% of wins is
concentrated on the copy path, where the amplitude ramp is lower. The whole
budget buys 0.062u on the copy path and nothing where the problem is.

### CLOSED. Do not re-sweep these three knobs.

`FLAT_CLEAR`, `BUST_CLEAR` and `NIPPLE_GAIN` are now measured together at their
maximum effect, not one at a time at plausible values -- which is how
`FLAT_CLEAR` earned its "moved the p50 by 0.002u" note without anyone knowing
whether the term ever won. It wins on a fifth of the band, that fifth is worth
0.035u averaged over the band, it is all on the copy path, and taking it costs
+12 penetrating verts and 11 tighter tips.

Both of section 20's cheap leads are now closed by measurement: the relaxation
side has no defect in it (section 20), and the bust floor cannot pay for itself
(here). What remains is the tip exemption's 64.5% -- and this sweep is a preview
of what touching it costs, because 11 tighter tips is exactly the row it owns.

## 23. THE TIP EXEMPTION'S 64.5%: THE ONLY LEVER THAT WORKS, AND A REAL TRADE

Section 20's third and last lead, and the first thing measured this session that
moves the body-swap bust gap at all.

### A SHARE IS NOT A BUDGET -- measured first, as with the bust floor

`#authored-nipple-exempt` covers 64.5% of the bust band, but on a vertex the
relaxation would not have moved anyway, refusing it costs nothing. Collapsing
those together is exactly the mistake the `no_bind` bucket made. So the audit
now splits it:

    exempt verts it actually protects        26747   (84.0% of exempt)
    exempt verts where it costs NOTHING       5099   (16.0%)
    median refused per protected vert       0.2038u
    TOTAL refused                        6122u-verts  (0.124u per bust vert)

**That is 3.5x the entire bust-floor budget** (0.035u per bust vert, section 22)
and about 18% of the +0.682u gap to the author. This one is real.

### Spending all of it: exemption OFF (`AUTHORED_NIPPLE_EXEMPT=1.01`)

At a fraction above 1.0 no body vertex qualifies as a tip, `_nipple_tip_mask`
returns None, and its own docstring says that "leaves the caller on exactly the
unexempted behaviour". NOTE THE REACH: this knob feeds BOTH authored floors
(`nif_convert.py:5454` for inflate and `nif_convert_fitgeom.py` for anti-poke),
so the arm is not anti-poke alone.

    row                              control   candidate   verdict
    bust gap vs author, body-swap     +0.682      +0.610   ok  <== MOVES, at last
    bust gap vs author, copy path     +0.194      +0.194   ok
    inverted                            1937        1885   ok   -52
    posed follow, net from ideal         0.0      -0.046   ok
    stretch p90 / pooled edges       2.0217/24141  2.0037/23816  ok
    folds                              15974       16016   FAIL  +42
    bust-band pen, body-swap              12          13   FAIL  +1
    tip clearance p50                  1.244       1.095   FAIL  -0.149u
    tip clearance p05                  0.454       0.411   FAIL  -0.043u
    pieces tighter at the tip               0          19   FAIL

    RESULT: FAIL, 4 rows

**Compare the three levers at their maximum effect**, which is the whole point
of measuring budgets before sweeping:

    lever                    body-swap gap    FAIL rows   what it costs
    authored relaxation      -- no defect --      --      (section 20, empty)
    bust floor (whole)        +0.682 unmoved       8      +12 pen, 11 tips
    tip exemption (whole)     +0.682 -> +0.610     4      -0.149u tip p50, 19 tips

### AND THE TRADE IS NOT TUNABLE -- the cost and the purpose are the SAME verts

A third arm at `AUTHORED_NIPPLE_EXEMPT=0.75` shrinks the exempted region without
removing it:

    bust gap vs author, body-swap     +0.682      +0.682   UNMOVED
    folds                              15974       15876   ok   -98
    inverted                            1937        1927   ok   -10
    tip clearance p50 / p05      1.244/0.454 1.252/0.454   ok   (slightly BETTER)
    bust-band pen                       12/0        12/0   ok
    pieces tighter at the tip               0           1   FAIL
    posed follow, net from ideal         0.0       +0.083  FAIL

Shrinking the region buys **nothing at all** on the gap. So the fidelity the
exemption refuses is concentrated on the verts closest to the tip -- the ones
still exempted at 0.75 -- which is precisely where the exemption exists, because
the author fitted over a CBBE bust whose tip under-provisions UBE's. **You
cannot buy author fidelity by making the protected region smaller; only by
removing protection at the tip itself.**

That also rules out the obvious "middle setting". The 0.75 arm is a marginal
surface improvement (-98 folds, -10 inverted, tip p50 slightly up) that buys no
fidelity and still fails on one tighter tip and on posed follow.

### Status: A DECISION, not a defect

All three leads from section 20 are now measured. Two were empty. This one
works and is a straight trade: **0.072u of author fidelity at the bust against
0.149u of median tip clearance and 19 pieces tighter at the tip.** The gate
fails it, and the gate is right to -- `pieces tighter at the tip` is a
zero-tolerance row precisely because a nipple through a cuirass was a reported
in-game defect, not a metric.

Nothing was flipped; `AUTHORED_NIPPLE_EXEMPT` stays at 0.5. Whether 0.072u of
bust fidelity is worth 19 tighter tips is the user's call, and it is the same
shape of call as A1 -- which is not a coincidence, since A1's three FAIL rows
are the same bust-side rows.

## 24. D1 IS CLOSED: ITS PREMISE WAS A MIS-ATTRIBUTED PROFILE (2026-09-08)

D1 said: `_cached_body_morph_differential` (24 calls, 1.5s of a 31.8s piece) and
`_cached_body_morph_amplitude` (63 calls, 0.7s) key on `_body_array_digest` of a
29298x3 array, "so every HIT first hashes the body". Proposed fix: memoise the
digest by array identity. Effort S, gate byte-identical then the profile.

**BUILT, GATED, AND REVERTED, because the profile says the premise is wrong.**

### What the profile actually attributes

Same piece, same plugin, one process, `cProfile`:

    function                            calls   tottime   cumtime
    _cached_body_morph_differential        24     0.884     1.466
    _cached_body_morph_amplitude           63     0.276     0.710
    _cached_body_morph_stack               32     0.022     0.175
    _body_array_digest                    111     0.001     0.035

**The 1.5s and the 0.7s are the CACHED FUNCTIONS' own compute. The digest is
0.035s of a 31.22s piece -- 0.11%.** D1 read the two cached functions' cumtime
and attributed it to the hashing inside them.

### And the caches HIT, which is the other half of the premise

Counted directly rather than inferred, by wrapping both functions for one piece:

    differential : 24 calls,  2 MISSES   (one real compute per weight half)
    amplitude    : 36 calls,  1 MISS

So 22 of 24 and 35 of 36 calls return from cache. The 1.5s is two legitimate
computations of a 29298-vert differential field, not repeated hashing.

### The memo was built and gated anyway, and it earns 0.1%

Identity-keyed memo, entry holding the array (an id can be reused once its
array is freed -- the rule `_AUTHORED_SRC_TREE` already had to learn), bounded
at 16 slots, plus a default-off in-place-mutation self-check.

    gate                    two arms, 184 NIFs and 92 tris BYTE-IDENTICAL
    self-check              ARMED in all 16 workers, 0 stale digests
    profile                 111 digest calls -> 5 actual hashes
    saving                  0.035s -> 0.002s on a 31.2s piece  (0.1%)

**REVERTED.** 0.1% does not justify carrying an identity cache -- with an
in-place-mutation assumption -- in front of the one function that exists to
prevent a cache-key poisoning class. The class it guards flipped ~22 physics
pieces per pool scheduling and was invisible in serial runs.

### The lesson, which is D2's lesson again

D2 was closed because a stale docstring sent a reader hunting a bug that was
not there. D1 is closed because a profile line was read as the cost of the
thing INSIDE it. Both plan items promised ~10% of a body-swap piece between
them; both are no-ops. **Before optimising a cache, count its MISSES** -- the
count took one instrumented run and would have closed this item without the
build.

Where the piece's time actually goes is unchanged and already recorded: fit
chain 37%, weight tail 27%, copy/install/reauthor 15%, body-ref discovery 11%
(amortised per worker), NIF I/O 6%. D1 and D2 are not in that list, and the
plan's "D1+D2 are ~10% of a body-swap piece" should be read as retracted.

## 25. E1 JUDGED: 76 KILL SWITCHES, ONE RETIREMENT (2026-09-08)

E1's census has existed since 09-06 and its numbers have held through two
re-runs. What was never done is the part the census cannot do: JUDGE the
candidates. Done here, and the answer is much smaller than "retire the dead
weight" implies.

### The mechanical half, as a tracked tool

`scripts/analysis/flag_retirement.py` computes the two conditions a count CAN
decide -- default ON in a scrubbed environment, and no reference outside the
flag's own binding -- and disqualifies anything the LIVE settings json throws.
It never says "retire this": whether a feature has an in-game verdict on record,
and whether its OFF branch is measured WORSE, are judgements against the record.

    kill-switch bindings                        76
    resolve ON in a clean env                   75
    resolve OFF -- not candidates                1   (excluded)
    thrown by the live settings json             0   (excluded)
    STILL STANDING after the mechanical checks   9
    referenced elsewhere, judge case by case    66

The one that resolves OFF is `LAYER_ORDER_REPAIR_ENABLED`, and it is the
shadow-force case the source already documents: the clearance-field default
reassigns it after declaration. Not a new defect -- but worth noting that the
census now SURFACES it instead of a reader having to know.

### The nine, judged against the record

    flag                    verdict on record?   OFF measured worse?   call
    _BUTT_MATCH             YES, in game         YES                   RETIRE
    PROXY_ENCLOSE_GUARD     a user report        contract, not measured KEEP
    GROOVE_SMOOTH_ENABLED   no                   A TRADE, not worse     KEEP
    BUST_MORPH_RESIDUAL     no                   NEUTRAL (+-0.005u)     keep
    _TIGHT_SOFTBODY_GATE    PENDING in game      no                     keep
    BUST_SPACING_AWARE      none on record       none                   keep
    BUST_SURFACE_REQ        none on record       none                   keep
    STATIC_AUTHORED_FIT     none on record       none                   keep
    BACK_RESIDUAL_VERBOSE   n/a -- VERBOSITY, not a pass                n/a

Three of those are worth stating in full because they look retirable and are
not:

* **`GROOVE_SMOOTH_ENABLED`** has the most complete evidence of any flag here,
  and it argues for KEEPING the switch. Measured with it off: stretched edges
  1151 -> 1329 (+15%), 0 shapes better / 25 worse / 35 unchanged -- but fit
  IMPROVES (0.257u -> 0.253u, clipping 147 -> 143). The OFF branch is a live
  TRADE, not a worse state, and the switch is the only way to reproduce it.
* **`PROXY_ENCLOSE_GUARD`** is a safety gate that was verified to DISCRIMINATE
  (declines 11 of 71, still builds on clean pieces). Its OFF state is "a stale
  collision surface", which the pass's own contract calls worse than none --
  but that is a contract, not a measurement, and a guard's kill switch has
  diagnostic value.
* **`BACK_RESIDUAL_VERBOSE`** is a log-quieting switch. The retirement rule asks
  whether the OFF branch reproduces a measured-worse STATE; a verbosity flag has
  no state. The rule does not fit it, and it should not be forced through.

### The one retirement

`CBBE2UBE_NO_BUTT_MATCH` meets all four conditions. The record carries an
IN-GAME verdict: the user-approved build of the piece that has one requires the
butt-match ON, and turning it off "made it WORSE ... and cost ~10 deploy
rounds", with the record calling that a misdiagnosis rather than a trade. The
switch has no GUI row, no reference outside its binding, no test, and the live
recipe does not set it.

It was two conjunctions, not an `else` branch:

    _do_jiggle = _BUTT_MATCH and _BUTT_JIGGLE and _BUTT_JIGGLE_STRENGTH > 0.0
    bgi = (_butt_match_strength(zi) if (_BUTT_MATCH and di <= _BUTT_PROX) ...)

Both lost the always-true conjunct; the rebalance is unchanged and still
tunable through `_BUTT_Z_LO/_HI/_RAMP/_STRENGTH/_PROX`. Census after:
**138 flags (was 139), 75 kill switches (was 76), 57 unreachable (was 58).**

### What E1 is actually worth

**One flag of seventy-six.** The 66 "referenced elsewhere" are referenced
because they have GUI rows, tests and worklog entries -- which is what a live
switch looks like, not dead weight. The nine that survive the mechanical filter
mostly fail on the record, and two of them fail because the evidence says the
switch EARNS its place.

So E1 should not be planned as a cleanup sweep. The remaining half of the item
-- "decide the 20 GUI bools that are default-off, unticked, and carry tooltips
citing measured wins: promote or retire, never leave" -- is a different and
probably larger question, and it is untouched.

## 26. THE FLAG CENSUS COULD NOT SEE 14 OF ITS OWN FLAGS (2026-09-09)

Going after E1's other half -- "the GUI bools that are default-off, unticked,
and carry tooltips citing measured wins: promote or retire, never leave" -- ran
straight into the census underneath it.

**The surface is 152 flags, not 138.** `flag_surface` matched bindings with a
single-line regex over `src/nif_convert.py` alone, and missed 14 for two
unrelated reasons:

    eight live in modules the census never opened   fitgeom 4, layers 3, physics 1
    six are written in forms one line cannot hold   a binding split across lines,
                                                    an `X = (0.0 if _flag(...))`
                                                    ternary, and five `_flag`
                                                    reads used INLINE as a
                                                    condition with no constant

That is the same class as the first draft missing 16 private-prefixed flags --
the trap the file's own docstring says it is built against -- and it reappeared
because `nif_convert.py` stopped being the whole surface at the 2026-09-01
split. **One of the 14 is turned ON by the live recipe.**

FIXED by parsing instead of matching: `flag_surface.declared_all()` walks the
AST of every flag-binding module, maps each `_flag` call to the Assign that
encloses it (so a ternary still reports its constant), and reports `const=None`
for an inline read rather than dropping it for having the wrong shape. The
regex is KEPT as a control and the suite asserts CONTAINMENT -- the parse must
find everything it finds, and strictly more. Verified: 152 call sites, 152
distinct env names, nothing regex-only, 14 newly visible, 5 inline.

## 27. THE CODE DEFAULT DISAGREES WITH THE SHIPPED RECIPE ON FIVE FLAGS

`flag_retirement --recipe` compares every binding's resolved default against
the DEPLOYED settings json. This makes mechanical a trap that has cost verdicts
from both directions: read only the code and you are wrong about the pack, read
only a memory and you are wrong twice, because a memory's default is dated.

    flag                            code    live    module
    AUTHORED_ANTIPOKE               False   True    nif_convert
    AUTHORED_INFLATE                False   True    nif_convert
    COHERENCE_REPAIR_OUTSIDE_BODY   False   True    nif_convert
    PHASE1_BUST_CLEARANCE           False   True    nif_convert
    FIELD_SCREEN_PHYSICAL           False   True    nif_convert_fitgeom

    settings keys no `_flag` binding claims : 0

**All five of the live recipe's non-default flags are default-OFF in the code.**
Every one of them ships ON. `FIELD_SCREEN_PHYSICAL` was invisible to the census
until section 26, so the drift could not even have been listed before today.

**This is E1's second half, answered for five flags: the recipe has already
promoted them.** "Promote or retire, never leave" is not a hypothetical for
these -- they are promoted in practice and unpromoted in the code, which is the
worst of both, because the code is what a reader consults.

Two of them carry a documented history that makes the split sharper, not
softer. `AUTHORED_ANTIPOKE` and `AUTHORED_INFLATE` were promoted to default ON
on 2026-09-05 and REVERTED the same hour on the user's call ("we can't use that
in its current state"), on a measured +2.7% folds / +6.5% inverted. The code
carries that reversal. The deployed recipe does not. **Every bust number
measured this session was taken with both floors ARMED**, which is stated in
sections 20 and 22 and is only correct because the recipe, not the code, decides
what runs.

**NOT RESOLVED HERE, and it is a decision, not a defect to fix quietly:** either
the code defaults move to match what ships, or the recipe stops setting them.
Doing neither leaves five flags whose documented default is not the one in use.

`settings keys no binding claims: 0` is the other half of that check and it is
clean -- the settings file has no dead entries.

## 28. F102 RE-MEASURED, AND ITS PROBE IS TRACKED NOW (2026-09-09)

F102's own verifier set the next step: "the dup_probe counts (24 runs, 276
lines, 0.346) were not re-run -- treat the numbers as unverified", and "the
probe counts should be regenerated and attached before the refactor is scoped".
C1 is that refactor, so this is the gate in front of it.

**THE PROBE WAS GONE.** `dup_probe.py` lived in an untracked scratchpad and does
not exist anywhere in the tree or in git history -- the same trap that hid three
"mandatory" tools before, and it means F102's three numbers were unreproducible
by anyone. Rebuilt as `scripts/analysis/two_path_dup.py`, tracked, 13 tests.

**F102'S EVIDENCE NO LONGER RESOLVES EITHER.** Every line number in it
(L6622, L28432, L29553...) is against the PRE-SPLIT file. `f03466b` cut
`nif_convert.py` from ~29.5k lines to 15k on the same day the audit is dated, so
the cited blocks are at different addresses and some now live in sibling
modules. The finding has to be re-derived, not looked up.

### Re-measured, with the rule written down

    copy path      convert_nif                             552 code lines
    body-swap path convert_nif_phase2 + _fit_shapes_swap  1451 code lines
    runs at >= 6 lines

    measure    runs   lines   ratio
    literal       6      46   0.083
    shape        11     126   0.228

    runs that are a PURE RENAME (0 lines identical)  : 0
    runs MIXED  (some lines identical, some not)     : 9

`literal` is the same text; `shape` is the same code with every identifier
collapsed, which is what a refactor would have to merge. **Not comparable to
F102's 24 / 276 / 0.346** -- different tool, unrecorded normalisation, and a
tree that has since been split. Quote these, not those.

### The finding itself STANDS, and is sharper than the count

**Nine of the eleven shape-identical runs are MIXED** -- some lines byte-for-byte
the same, others not -- and NOT ONE is a pure rename. That is exactly F102's
claim: a reader cannot tell a deliberate difference from a missed port, because
every shared block has both. The longest is 22 lines with 13 identical; the next
is 17 lines with only 1.

### Two wrong numbers this tool produced before it produced a right one

Worth recording, because both were the instrument's fault and both were caught
by its own tests rather than by reading it:

* **It reported ZERO literal duplication** while an eleven-line block was
  identical word for word. The two paths sit at different nesting depths, so
  comparing raw indented lines can never match them. Fixed by stripping leading
  whitespace for the literal compare; a test now asserts the indented compare
  misses it and the stripped one finds it.
* **It deleted every line carrying a trailing comment**, which removes real code
  from both sides and under-reports the duplication it exists to measure. Fixed
  to cut the comment rather than the line; the population moved 548 -> 552 and
  1434 -> 1451 lines.

### What this means for C1

C1 is scoped as ~3,800 lines becoming ~30 stage functions, Effort XL, verifier
confidence LOW. The measured overlap between the two entry functions is **126
shape-identical lines across 11 runs**, of which the largest is 22. That is the
duplication a stage table would actually merge in these two functions -- it is
not 3,800 lines of parallel structure, and the refactor's case has to be made on
the ~30-stage reorganisation, not on removing this. **The number to argue C1
from is now measurable and tracked; before today it was a scratchpad memory.**
