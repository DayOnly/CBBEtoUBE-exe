# F011(b): re-measuring `#groove-authored-cap` against a target that is not zero

2026-09-02. Blocker (a) was retired earlier today (`F011_COLLIDER_DELTA.md`:
the 461->467 proxy count carries no information). This is blocker (b).

## Three things established before any convert ran

### 1. The premise holds on a real population, measured with the CODE's detector

`_SRC_NORMAL_FIX` matters only where the source body's stored normals are
unusable. On the body-swap-heavy clothes sample used here:

```
pieces with an inline body (nc._looks_like_inline_body) : 114
  normals ALL-ZERO                                      : 114
  populated                                             :   0
```

Every one. A first pass at this used a name heuristic ("body" in the shape
name) and reported the exact opposite — 12 pieces, 0 zeroed — because it was
scoring shapes that are not the inline body at all. **The code's own detector
is the only one whose answer means anything**; a private re-definition of the
population produced a confident inversion of the result, which is the same
trap as measuring "simulated verts moved" with a hand-rolled chain-weight rule.

### 2. The same function reads this array TWICE and disagrees with itself

Inside `convert_nif_phase2`:

- `nif_convert.py:13513` — `src_body_n_p2` is set from
  `_body_normals_or_compute(...) if _SRC_NORMAL_FIX else getattr(shape, "normals", None)`.
  At defaults that is the **all-zero** array, and it is what
  `conform_to_source_standoff` and `#groove-authored-cap` consume.
- `nif_convert.py:13705` — **the same variable is rebound**, unconditionally,
  to `_body_normals_or_compute(cbbe_body_shape)`, with the comment *"Source
  body normals often zeroed in BodySlide output; compute from tris"*, for the
  chest/abdomen passes.

So one function already treats the recompute as correct and necessary, then
feeds the raw zeros to the passes above it. This is not "an opt-in that has
not been judged"; it is an inconsistency inside a single function, and it is
the strongest rule-1 argument available (`feedback_prevent_dont_patch`).

### 3. What the zeros actually do to the cap

`#groove-authored-cap` (`nif_convert_fitgeom.py:363`) computes the authored
clearance as

```python
s_auth = ((src - sbv[si]) * sbn[si]).sum(axis=1)
cap    = np.maximum(s_in, s_auth)
```

With `sbn` all zero, `s_auth` is **0 everywhere**, so the cap collapses to
`max(s_in, 0)`: groove smoothing may never push a vertex further out than it
already was. Meanwhile conform, fed the same zeros, has read every source
garment as skin-tight and pulled loose drape inward. **Two passes are blind on
one array, and the second one is what stops the first from being undone.**
That is why the cap cannot be judged at defaults: it is being measured in a
world where its own input is a constant.

### Reachability note

`GROOVE_AUTHORED_CAP` is `not _flag("CBBE2UBE_NO_GROOVE_CAP", False)` — default
ON — and `grep -c CBBE2UBE_NO_GROOVE_CAP src/gui_settings.py` is **0**. It has
no Setting row, so no deployed run can switch it and no user can. Whatever the
measurement says, that needs resolving: a default-ON pass with no row is the
`feedback_deployed_build_runs_at_defaults` class.

## The measurement

Four arms, one harness (`exe_parity_convert.py`, the DEPLOYED exe), same mod,
overrides applied after `apply_env` so each arm is the shipped recipe plus one
change:

| arm | `SRC_NORMAL_FIX` | `#groove-authored-cap` |
|---|---|---|
| `ctrl` | off | on (default) |
| `fix` | **on** | on |
| `fix_nocap` | **on** | **off** |
| `nocap` | off | **off** |

`ctrl` vs `nocap` reproduces the historical measurement (cap judged against a
zero target); `fix` vs `fix_nocap` is the same question with a real one.

Scoring is per shape, both weights, on a mask built ONCE from the control arm
(body verts within 8u of the control garment) so a garment that moves cannot
change its own scoring region — and as a boolean mask, because `clip_stats`
and `standoff_stats` call `flatnonzero` internally and silently score the
wrong region when handed indices.

## Results

All four arms: exit 0, 74 NIFs each, through the DEPLOYED exe (git `a6098ec`,
sha `cf9fd6e7`). Arms proved live two ways -- each output's own
`conversion_settings.json` records `src_normal_fix` True/True/False/False, and
the preserved per-arm run log echoes `NO_GROOVE_CAP=1` for both cap arms
(that echo is the ONLY proof available for the cap, which has no Setting row
and so no line in the settings artifact). Geometry differs on 56/74 NIFs for
the normal fix and 58/74 for the cap, so neither arm is a no-op.

### Surface quality: `fold_census`, 34 NIFs, BOTH weights

The first run of this used the census as it stood, which globbed `*_1.nif`
only. Correcting the tool (commit `e90f6d1`) changed the numbers and removed a
baseline I had been about to quote, so the corrected population is the one
reported here:

| arm | `SRC_NORMAL_FIX` | cap | folded | inverted |
|---|---|---|---|---|
| `ctrl` | off | on | 10976 | 703 |
| `fix` | **on** | on | **10267** | **367** |
| `fix_nocap` | **on** | off | **10142** | 379 |
| `nocap` | off | off | 10736 | 514 |

1. **The producer fix is a clear surface-quality win on its own.** 10976 ->
   10267 folded (-709, -6.5%) and 703 -> 367 inverted (**-48%**). The
   improvement is BROAD, not one piece carrying it: every one of the twelve
   worst pieces improves, and the sharpest are whole-garment robes (one
   necromancer-style robe 114 -> 14 inverted, a merchant torso 39 -> 4). This
   is the fix behaving as rule 1 predicts -- conform stops making bogus
   pull-ins, so the repair passes below it have less to undo.

2. **The cap costs folds in BOTH regimes, by about the same amount** -- +240
   with a zero target (`nocap` 10736 -> `ctrl` 10976), +125 with a real one
   (`fix_nocap` 10142 -> `fix` 10267). So the answer to F011(b) on this axis is
   that A TRUE TARGET DOES NOT RESCUE THE CAP. It halves the cap's cost, which
   is consistent with the mechanism (a real `s_auth` is a looser bound than
   `max(s_in, 0)`), but the cap is still net negative for surface quality.

`pieces with folds` is 28/34 on every arm: this is a change in fold COUNT
within the same pieces, not pieces crossing in or out of the defect.

### There is NO author baseline for this sample, and the first one was fake

The uncorrected census printed "AUTHOR CONTROL over 31 paired shapes, author
folded 67", which invites the reading "the author ships 67 folds where we ship
10976". **That comparison would have been between two disjoint sets of
assets.** All 31 paired shapes were `1stperson` meshes -- arms-only meshes that
the corrected population excludes. With those gone, the sample pairs ZERO
shapes, because every garment that actually ships here was VFS-resolved from
OTHER mods and is not under the `--source-root`.

So the fold counts above have no author comparison, and the arm-to-arm deltas
are the only thing they support. That is enough for F011(b), which is a
question about two flags, not about absolute quality.

### Fit: clip and standoff, 252 shapes, paired mask

`clip_stats` / `standoff_stats` over a mask built ONCE from the control arm
(body verts within 8u of the control garment) and reused for every arm, capped
at 1500 verts by a deterministic stride. The cap is what makes a 4-arm run
finish; because the SAME subsample scores every arm, the comparison stays
paired and the deltas keep their meaning -- only the absolute percentages gain
sampling noise.

| comparison | standoff: shapes moved >0.01u | median | p90 | max | clip: shapes moved >0.02pp |
|---|---|---|---|---|---|
| `fix` vs `ctrl` (producer fix) | 152 / 252 | **+0.0048u** | +0.128u | +1.067u | 20 / 252 |
| `nocap` vs `ctrl` (cap off, zero target) | 92 / 252 | +0.0021u | +0.037u | +0.250u | 6 / 252 |
| `fix_nocap` vs `fix` (cap off, real target) | 62 / 252 | +0.0009u | +0.014u | +0.260u | 3 / 252 |

**THIS IS NOT THE F010 PATTERN, and the counts alone would have said it was.**
Counting shapes, `fix` reads "standoff further on 110, closer on 42" and the
summed delta is +8.7u -- which looks exactly like the inflation that killed the
copy-path change. The magnitudes say otherwise: F010 moved p50 standoff
1.414 -> 1.863u, **+0.45u on every piece**; this moves a MEDIAN of +0.0048u,
five thousandths of a unit, with the sum carried by a thin tail. Clipping is
unchanged on 232 of 252 shapes with a median delta of exactly 0.

So the producer fix costs essentially nothing in fit and buys 6.5% fewer folds
and 48% fewer inverted triangles. A count of better/worse shapes is the right
headline when the effect is uniform (F010) and actively misleading when it is
not; both belong in the table.

One thing this population CANNOT settle: whether the outward tail is right.
Conform's defect is that a zero target reads every source garment as skin-tight
and reels loose drape INWARD, so cloth moving outward under the fix is the
expected correction -- but "further from the body" can only be scored as good
or bad against the AUTHOR's standoff, and this sample has no author baseline
(above). The fold metric carries the argument precisely because it needs no
baseline.

### The cap: near-inert on fit, and MORE inert once the target is real

`#groove-authored-cap` changes clipping on **3 of 252** shapes with a real
target (6 with a zero one) and moves standoff by a median of 0.0009u. Its
remaining effect SHRINKS as the target becomes real -- 92 shapes -> 62, p90
0.037u -> 0.014u -- which is what the mechanism predicts, since a real
`s_auth` is a looser bound than `max(s_in, 0)`.

Put beside the folds: the cap buys nothing measurable in fit and costs 125
folds with a real target. **F011(b) is answered: a true target does not rescue
`#groove-authored-cap`.** It is a candidate for default-off or deletion -- as
its own change, not folded into the producer flip, and noting it is currently
unreachable (no Setting row).

### REPLICATED on a second, unrelated population

The fold result above is one mod of clothes and robes. Repeated on the
collider-bearing population used for the contract measurement -- 102 NIFs of
vanilla armour resolved through an overriding ESP, a different asset class
entirely:

| population | folded | inverted |
|---|---|---|
| clothes, 34 NIFs | 10976 -> 10267 (**-6.5%**) | 703 -> 367 (-48%) |
| vanilla armour, 102 NIFs | 12415 -> 11740 (**-5.4%**) | 640 -> 575 (-10%) |

Folds move the same way and by a similar fraction, on 102 pieces that share no
assets with the first sample. That is the producer fix's surface-quality win
replicating, not a property of one author's robes.

The INVERTED gain does not replicate in magnitude -- 48% against 10%. That is
consistent with what inverted triangles are: robes have long, loosely draped
spans that cross themselves when conform reels them inward, and plate does not.
Reporting the 48% alone would have implied a general result the second
population does not support.

### Scope caveat on the cap result: WHICH PATH was measured

`#groove-authored-cap` is NOT equally live on the two paths, and the sample
here is body-swap heavy (38 of 49 NIFs on the mod converted). The copy path
builds its source-body normals with `_cached_cbbe_body_normals`, which
recomputes UNCONDITIONALLY -- `_SRC_NORMAL_FIX` appears nowhere in it -- so on
the copy path (four fifths of a pack) the cap has ALWAYS had a real target.
Phase 2 is the only place it reads zeros.

That makes `fix_nocap` vs `fix` the right analogue for copy-path behaviour, and
it is the arm that says the cap changes clipping on 3 of 252 shapes. But it is
an analogue measured on swap-heavy pieces, not a copy-path measurement. **The
cap's near-inertness is established for the body-swap path and only inferred
for the copy path**, and a copy-path-only population would settle it. Do not
delete the cap on the strength of this alone.

## Where F011 stands

**Blocker (a) retired** (`F011_COLLIDER_DELTA.md`), **(b) answered here.**

NOT done, and F011 must not be promoted without them:

* **(c) the conform constants have not been re-validated with the fix on.** The
  constant's own comment asks for this and it is untouched.
* **The collider CONTRACT is still unmeasured** -- `proxy_encloses_chain` = 0
  paired against control, the declared-bone audit, and proxy cloth coverage.
  Retiring the vertex-count "regression" did not answer whether the flag is
  safe for colliders; it only established that the number cited carried no
  information.
* **One mod, one shape class.** Clothes and robes, body-swap heavy. Not plate,
  not physics-heavy, and the anti-poke/SMP interaction is unexercised.
* **No in-game verdict.** The pack currently under test does NOT have this flag
  on, so nothing in game speaks to it yet.


