# Changelog

## 1.2 — 2026-08-02

The first release since 1.1.1. Everything here shipped as internal 1.2.x
builds that were never published, folded into one release rather than
presented as eight pending versions.

The theme is **measuring fit correctly, then fixing what the measurement
exposed**. Most of 1.1.x's fit work was steered by two metrics that turned out
to be anti-correlated with what the game actually shows, so a run could score
better and look worse. Replacing them uncovered a frame bug that had been
corrupting the entire phase-2 pass chain, and several long-standing fit defects
turned out to be metrics that did not measure what their name claimed.

### Changed — four fit options are now ON by default

These shipped as opt-in switches nobody was turning on, so the out-of-the-box
conversion was missing fixes that had already been proven. All four have since
run over a full pack and been used in game, so they are the defaults now:

* **Judge chest movement by the outfit's own weighting, not its name.** Decides
  how much a top may move by whether its author weighted the chest at all,
  instead of guessing the material from the file name and textures. It only ever
  adds movement to pieces nothing was helping.
* **Chest follow ratio.** A fitted soft top now tracks the body's breast motion
  by the amount its own clearance requires, rather than a flat cap that left it
  following about a third of the body. Metal keeps the conservative cap.
* **Bust clearance on collider-only SMP armour.** Armour whose physics config
  names it only as a collider was getting no bust clearance at all, so the body
  pushed straight through it. Measured 6.3% → 3.3% exposed on one such cuirass.
* **Fit robes and dresses that declare their own physics.** Draping pieces were
  skipped wholesale; the skip now applies only to those that ship no physics
  file of their own.

Each keeps an escape hatch, and the Armor tab is the place to use it:
`CBBE2UBE_NO_SOURCE_FOLLOW`, `CBBE2UBE_NO_CHEST_FOLLOW`,
`CBBE2UBE_NO_SMP_ANTIPOKE`, `CBBE2UBE_NO_DRAPE_XML_GATE`.

If a robe crashes on equip, untick "Fit robes/dresses that declare their own
physics" first — that is its known failure mode. If stiff armour starts looking
rubbery, untick the chest follow ratio.

**Not** promoted: keeping mostly-rigid armour skinned. It changes physics and
has no in-game verdict yet, so it stays opt-in on the Armor tab.

### Fixed — the converter was not deterministic across processes

Iterating a set of strings orders by hash, which varies per process, and that
order reached the output three ways: the order `setShapeWeights` was called
(deciding which influence survived an overflowing row), the morph order inside
a generated .tri, and the 4-influence cap's tie-break on exactly-equal weights
(symmetric bones tie). Same input now produces the same bytes: verified across
PYTHONHASHSEED 0, 2, 5 and 7 over all 46 emitted artifacts.

### Fixed — a fit pass that raised was indistinguishable from one that did nothing

Every fit pass runs inside a catch so one bad piece cannot abort a 4000-piece
batch. The catch was silent, so a pass that threw simply vanished -- which had
already cost three wrong verdicts. Failures are now recorded and travel back to
the caller in the conversion report; grep it for PASS FAILED. Swallowed mesh
SAVES report the same way.

### Fixed — fitted pieces shipped standing off the body

Phase 1 carried TWO `inflate_armor_outward` call sites and NO conform; phase 2
had both. A phase-1 piece — the large majority of files — got clearance added
with nothing to reel it back to the author's fit. `conform_to_source_standoff`
now runs in phase 1 too, using the CBBE base body the warp is already keyed on
as the source reference (phase-1 pieces frequently ship no inline body, which is
part of why they are phase 1).

`conform_to_source_standoff` also deliberately left tight cloth looser than
authored — flooring at `min_clearance` and reeling a skin-hugging vert only
`blend_tight` of the way back — because the source was fitted to the smaller
3BA body. True at bust/belly/butt, false at a shoulder or sternum. Both limiters
now ramp off with the body's outward morph amplitude.

Measured per-vertex over 8.1M verts of the shipped pack (loose AND BSA sources):
verts the author placed at 0.10-0.25u shipped +0.346u further out (p90 +1.00u),
the largest push of any band; loose verts (>1u) moved +0.034u. The tighter the
author fitted it, the more it was inflated.

Env: `CBBE2UBE_PHASE1_CONFORM=1` (DEFAULT OFF pending the clipping A/B),
`CBBE2UBE_NO_STATIC_AUTHORED_FIT=1`, `CBBE2UBE_STATIC_AUTHORED_AMP` (2.0),
`CBBE2UBE_STATIC_AUTHORED_MIN` (0.06).

A related ordering effect, traced rather than guessed. Per-pass trace of one
armour's chest band: authored 0.197 → warp 0.265 → inflate 0.490 → **conform
0.235** → `_smooth_warp_grooves` **0.322**. The conform reaches the authored
fit; groove-smoothing then runs and pushes part of it back out, because that
pass is one-sided by design and can never pull toward the body.

Reordering conform after groove-smooth was the obvious fix, and it was built and
measured: it does tighten the chest band (0.322 → 0.283) but costs +2.267u of
arm crinkle, because the output's smoothness comes from groove-smooth smoothing
the *cumulative* field last. So it was reverted, and the fit problem was solved
instead by bounding groove-smooth's outward motion at the authored standoff.
Recorded here so the reorder is not attempted again.

### Fixed — physics chains whose anchor node was dropped (pull-to-origin)

An HDT-SMP chain hangs off a kinematic anchor bone. Those anchors carry ZERO
skin weight, so no shape's bone list mentions them and the rebuild — which
carries the bones the skin references — has no reason to keep them. A separate
pass exists to preserve them from the physics XML. It was computing the
preservation set two ways too narrowly, and each way lost a different armour:

* it harvested only `<bone name=...>` declarations, so a chain whose anchor
  appears ONLY as a `<generic-constraint>` `bodyA`/`bodyB` was never seen. One
  shipped skirt XML declares 124 bones and names 81 constraint bodies, 18 of
  them never declared.
* it cleared candidates with `_is_skeleton_bone`, which matches its body-part
  keyword list as UNANCHORED SUBSTRINGS. The custom chain bone `LArmA 01`
  contains "arm", so it was treated as a bone the actor supplies. No skeleton
  has it.

Either way FSMP cannot resolve the anchor, places it at the origin, and the
chain hanging off it is dragged there — sleeves stretching from the shoulder to
the ground, skirts collapsing.

The preservation set is now built from every bone the XML references by any
route, minus the ones the ACTOR'S skeleton actually supplies — a lookup against
the real skeleton rather than a guess from the name. `_is_skeleton_bone` is
deliberately left alone: it also drives weighting, leg-rigid detection and the
jiggle strip, and a blanket change to it is what regressed working cuirasses in
June.

Measured over the shipped pack as a source→output differential: of 548 output
NIFs carrying a physics XML, 239 dropped at least one XML-referenced node, but
75 of the 87 dropped names are real actor bones where dropping is correct. **12
names were unresolvable, across 18 NIFs in 3 armour sets** — all three now keep
their anchors, recreated at the exact source bind (delta 0.0). An unaffected
piece is unchanged: identical shapes, vertices, weights and node transforms.

The postflight check that was supposed to catch this had both blind spots too:
it read only declarations, and it warned about every declared bone missing from
the NIF including the resolvable ones — ~45 lines per file, which is how the six
that mattered stayed invisible. It now reports only genuinely unresolvable
bones, and counts constraint bodies as references.

### Fixed — the pass chain was computing against a misplaced garment

`shape_body_offset` adds a shape's NiAVObject translation to put its verts into
body space. For a skinned shape whose verts are *already* in body space and
whose `global_to_skin` is identity, that translation is not a correction — it is
a displacement. One piece was being shoved 40 units sideways before a single fit
pass ran, so all twelve phase-2 passes measured, pushed, and inflated against a
garment that was not where they thought it was.

The offset is now checked geometrically: if applying it moves the shape
*further* from the body than leaving it alone, it is discarded. On the piece
that had resisted diagnosis for three months, this alone took bust clipping from
**8.87% to 0.00%**, with the new targeted push pass firing on nothing — there
was nothing left to fix.

This is also why several earlier "the fix didn't work" results were misleading:
the fixes were fine, the geometry they were applied to was not.

### Fixed — groove smoothing pulled the bust apex onto the skin

`_smooth_warp_grooves` does a roughness-weighted Laplacian smooth of the
CBBE→UBE displacement field to remove indent lines. Over a convex feature that
field peaks at the apex, and smoothing a peak flattens it — pulling the garment
back onto the body. The roughness weighting made it worse, because roughness is
highest at that same apex, so the pass smoothed hardest exactly where flattening
does the most damage.

A per-pass trace found it regressed fit on **13 of 42 shapes** (net +1052
exposed verts, worst +394) while improving fit **zero** times.

It is now one-sided: a vert may be smoothed along the surface or away from the
body, never toward it. On the reproduction case, 31 → 161 exposed verts becomes
31 → 21, while 81% of the smoothing motion is retained — the pass still does its
job. `CBBE2UBE_GROOVE_ONESIDED=0` restores the old behaviour for comparison.

### Fixed — correctness

- Coverage sidecars shipped pre-prune FormIDs, so the merge emitted **zero**
  links. An INI mask hid it. Record objects are now held across
  `prune_unused_masters`.
- `convert_one_armor.py` now takes the **same** path as a batch run. It had
  diverged (different vertex scale, slots resolving to 0), which made
  single-piece validation quietly untrustworthy.
- A diagnostic print can no longer abort a conversion.
- BSA-packed sources resolve in `find_source`.

### Fixed — the only pull-in pass was silently skipped

`conform_to_source_standoff` reels an over-projected garment back onto the body;
every other pass nudges outward. On some pieces it never ran, because **two body
detectors disagreed**. `classify_shapes` identified a shape as the body and
dropped it for the swap; `_is_body_pynifly_shape` then refused the same shape for
carrying fewer than 40 bones — a BodySlide-output inline body only carries the
bones its surviving verts touch, and the affected pieces ship one with 26. The
source-body reference stayed `None` and the gate never opened.

No exception, no warning — the pass was simply absent. It was found only because
the per-pass standoff trace below was built to chase the report.

Measured on the affected piece: strap-line standoff **2.40u → 1.72u**, against a
**1.79u maximum** across 42 shapes where the pass did run. Clipping unchanged at
0.00%, so the gap closed without trading it for skin poking through.

**Scope, measured by a full sweep rather than a sample:** the predicate matches
103 of 482 source pieces. Of those, 22 reach the converted output — the other 81
are overwhelmingly HIMBO/male bodies (77), which the female-only policy never
converts. Converting all 22 then showed **8 of them route to phase 1**, the copy
path, which never reaches `conform` at all: the scan tested for a body
`classify_shapes` could name, but phase-2 routing has further conditions, so it
over-matched. **The real list is 14 pieces**, dominated by the vanilla-lineage
armours (hide, imperial, stormcloak, draugr, Ysgramor) whose BodySlide output
emits a low-bone inline body. The ones that dropped off are cloaks, boots,
panties and a corset — consistent with them not being body-swap pieces.

Verified across that set rather than sampled: **48 of 48 armed shapes now run
`conform`**, with a control piece that already ran it still doing so.

> An earlier draft of this entry said "rare, not systemic: 42 of 42 armed shapes
> in a 9-mod census already ran the pass". **That was wrong.** The census drew
> nine pieces that all happened to have detectable bodies; a sample that lands
> entirely in one state cannot establish a rate, and it was read as if it had.
> The full sweep replaced it. The 1.2.1 commit message still carries the wrong
> figure and is left alone rather than rewriting pushed history.

The fix is a fallback reached only when the strict detector finds nothing, so it
cannot alter pieces that already work — verified byte-identical on one.

### Fixed — telemetry that misreported itself

- **`shipped`** added to chain records. `final` is the *rejected* measurement
  when a rollback fires; on the first pack-wide run that read as 174 exposed
  verts against 101 actually shipped, and made a run with zero regressions look
  like 20 shapes ended worse.
- **`path`** added to every record. `nif` is a bare filename and filenames repeat
  across mods — three ship a `cuirassmedium_1.nif` — so a record could not be
  traced to the piece it described.
- The `conform` and chain-verify call sites now **record** their exceptions
  instead of `except Exception: pass`. Both made a failure indistinguishable from
  "nothing to do".

### Fixed — a dense garment raised MemoryError mid-run

```
MemoryError: Unable to allocate 825 MiB for shape (36,061,621, 3)
```

`_pairs` emits one row per (ray, triangle) candidate, so cost scales with
rays × candidates. The sparse formulation is far cheaper than the dense one it
replaced — which is why it was introduced — but it was never *bounded*, and it
was described as "memory-safe" anyway.

It failed **twice, and only once visibly**: `record_torso_bands` recorded a
`standoff_band_error` because it is built to record its own failures, while
`ChainGuard.exposed()` swallowed the identical error into a `-1` that surfaced
as the single word `unmeasurable`. Same armour, same cause.

Fixed once in the shared cast (`cast_chunked`, chunked `clipping()`), used by
the standoff record, the torso bands, the pass trace and the chain guard. Rays
are independent, so a chunk boundary cannot change a result — asserted across
chunk sizes that do not divide the ray count evenly, and against the dense
reference so the calibrated anchor cannot drift. `CBBE2UBE_RAY_CHUNK` tunes it.

### Fixed — the bust requirement was measured on the wrong thing (#bust-surface-req)

`conform_to_source_standoff` asked whether each garment **vertex** stood `req`
clear of the body. The defect is the **surface**: the tightest point sits in a
triangle interior — on the failing piece, 50 of the 50 tightest spots, a median
1.607u from any vertex — so a surface can sag 0.855u below vertices that all
pass. `req - worst` was therefore negative at every nipple vert (mean -0.921),
the push-out never fired, and `req` was never read at all: raising it by 3.5u
moved delivered clearance by 0.04u.

The requirement is now evaluated per garment triangle over the body points that
project **inside** it. The inside test is load-bearing — a point beside a
triangle is not covered by it, and charging it inflated the whole chest
(0.434 -> 2.297u) when tried. `CBBE2UBE_NO_BUST_SURFACE_REQ=1` disables.

Measured across **112 installed BodySlide presets**, poking presets **19 -> 1**
(the survivor is one named "Too Big"). On the reported piece and preset:
-0.082u with 65 poking verts -> +0.220u with none, for +0.024u of torso fit and
+0.000 crinkle.

### Fixed — the bust neighbourhood never reached past its own vertices (#bust-neighbourhood-spacing)

`BUST_NEIGHBORHOOD_RADIUS = 4.0` was effectively dead: with `k=6` and a 0.359u
body the sample only ever reached 0.673u, so the 4.0u filter never removed
anything and the real neighbourhood was set by `k`. Garment spacing is
0.71-1.21u, so a tip poking between two garment verts was never sampled. The
sample is now sized by each vertex's own local spacing, with `k` raised enough
to reach it and today's `k` nearest retained as an exact subset (so a garment
finer than the body is unchanged). `CBBE2UBE_NO_BUST_SPACING=1` disables.

### Fixed — the requirement ignored body morphs (#bust-morph-residual)

`req` was a bind-pose number while the character in game is morphed, and a UBE
nipple travels up to 5.35u at runtime. The armour follows via its own BODYTRI,
so what survives is the *residual* between the body point that pokes and the
garment vert covering it — zero for a slider that merely inflates, positive for
one that reshapes. That residual is added to `req`, weighted by nipple weight so
it costs the torso nothing. `CBBE2UBE_NO_BUST_MORPH_RESIDUAL=1` disables.

### Fixed — groove smoothing gave back the clearance the conform had won

Two independent defects in `_smooth_warp_grooves`, which runs after the conform:

- It is outward-only, so on a vert the conform had just reeled in it could only
  hand clearance back (traced: chest 0.235 -> 0.322u). Its outward motion is now
  bounded at the authored standoff. `CBBE2UBE_NO_GROOVE_CAP=1` disables.
- Its *tangential* motion reshapes triangles, dropping the interpolated surface
  over a tip between verts even though no vert moves inward (0.284 -> 0.161u).
  The smoothing is now held back over a protrusion.
  `CBBE2UBE_NO_GROOVE_HOLD=1` disables.

### Fixed — the torso under-followed every spine bend (under-bust clipping)

The third instance of the same body-rig mismatch, and the cause of the under-bust
clipping that only appeared in motion. The garment parks its spine mass on the
wrong vertebra — measured in the under-bust band of a vanilla cuirass, normalised:

    garment   Spine 0.121   Spine1 0.727   Spine2 0.151
    body      Spine 0.098   Spine1 0.492   Spine2 0.410

`Spine2` sits furthest up the chain and so accumulates the most rotation. Carrying
0.151 of it against the body's 0.410 makes the garment under-travel every spine
bend by ~20%, and the skin slides out from under the bust. Like the other two, it
is invisible at bind pose. All three spine bones are managed together because the
defect is the split between them, not missing mass.

    under-bust band       follow median      verts below 0.7
    spine forward lean    0.814 -> 1.099      24.3% -> 0%
    spine twist           0.793 -> 1.162      24.3% -> 0%
    spine side bend       0.840 -> 1.016      24.3% -> 0%
    sprint                0.832 -> 1.091      24.3% -> 0%
    bust, bow draw (p10)  0.720 -> 1.048

**Pass ordering is now load-bearing.** Every family-scoped match rescales the bones
it does not manage, so the spine and arm passes contend for rows where their bands
overlap and whichever runs last wins. Spine runs first: measured, spine-last costs
the armhole 1.106 → 1.076, while arm-last costs the under-bust nothing, because
under-bust rows carry no arm-family weight and are skipped anyway. A test asserts
the ordering per convert path.

`CBBE2UBE_NO_SPINE_MOTION_MATCH=1` disables it.

### Fixed — the shoulder walked out through the armhole in motion

CBBE and UBE rig the shoulder differently: CBBE's `UpperArm` weight stops at
z 99.7 and the shoulder above it is Clavicle-only, while UBE carries `UpperArm`
up to z 110.1. A CBBE-authored garment bakes in the CBBE convention, so on a UBE
body it arrives with ZERO `UpperArm` weight across the armhole over skin that has
0.179 there. The shoulder then moves and the garment does not — follow p10 was
literally 0.000, meaning those verts did not move at all — and the body emerged
under the arm and down the side during animation. Bind-pose clearance cannot see
any of this, which is why it survived every clearance pass.

Not a converter leak: the source garment and the source body both measure 0.000
there. It is a body-rig mismatch, so it applied to every CBBE-sourced piece
covering the shoulder.

Built as the ARM instance of the existing leg-motion match — the same defect at
the hip — by parameterising that pass by bone family and Z band rather than
copying it, so the 4-influence cap and the weight-write invariants cannot drift
apart between limbs. `Clavicle` is rebalanced alongside `UpperArm`, because the
defect is mass sitting on the wrong bone of the pair, not just missing mass.
Weights only: no vertex moves on any shape, so bind-pose fit is untouched.

    armhole, arms forward   follow 0.642 -> 1.106,  38.4% -> 0.8% of verts under 0.5
    armhole, arms crossed          0.684 -> 1.127,  35.0% -> 0.8%
    side, arms crossed             1.016 -> 1.015,   9.6% -> 3.2%
    under-bust / bust              unchanged (specificity control)

`CBBE2UBE_NO_ARM_MOTION_MATCH=1` disables it.

### Fixed — the leg-motion match was silently doing nothing on part of the pack

The same over-broad gate. `_shape_has_hdt_smp_rigging` flags a shape when 40%+ of
its bones are unknown to the body — but the injected body declares 36 bones and a
garment routinely carries 50–70, including plain skeleton bones like
`UpperarmTwist2`. On any piece that also has a physics XML, the leg-motion match
therefore returned without touching anything.

Measured over 400 converted pieces: the gate fires on the main garment of 141
(36%); 35 (9.0%) also have a physics XML. Of the leg-bearing shapes in that state,
39 were gated out of the pass entirely and every one had rows that survive a
per-row test. So the shape gate was costing real work rather than protecting a
physics chain.

The leg pass now uses the same per-row fallback the arm pass does: where the
shape-level heuristic fires, rewrite only verts whose entire weight sits on bones
the body also has. Such a row provably carries no authored chain bone. The
collider and soft-body skips are untouched and still unconditional, which is what
keeps the documented soft-body cases correct — those shapes are declared
per-vertex soft bodies and declining to rewrite them is right.

    hip band, follow ratio      median          verts below 0.5
    dragonbone cuirass          0.882 -> 1.119   21.8% -> 12.0%
    draugr chain                1.006 -> 1.092    9.6% ->  3.4%
    hide cuirass (light)        0.000 -> 0.157   90.2% -> 82.2%

No band on any probe got worse. Weights only: zero vertex movement on both weights
of all three probes and across all 15 golden pieces, and the weight-sum invariant
is unchanged.

### Changed — the Armor settings tab is readable again

It had grown to 38 of the tool's 43 settings and rendered about 3.7 screens
tall, but the bulk of that was not controls: 86% of the text on the tab was
always-visible explanation, one paragraph per setting.

Each setting now shows a single line, with the full text on hover. Nothing was
shortened or deleted -- those explanations carry measured numbers and in-game
caveats recorded nowhere else, so they moved rather than shrank.

The seven numeric tuning knobs now hide behind "Show advanced", which is what
the underlying field was always for; it had never been connected to anything.
There is also a live search over the settings, and the window remembers its
size between launches.

Groups were re-cut from six to eight by what a setting actually acts on: the
physics-chain options had been split across two groups, one group had grown to
16 unrelated settings, and three settings sat under the wrong heading.
"Convert vanilla armor" moved to the Run tab, beside the mod selection it
extends -- it adds a source to the run rather than changing how a garment is
fitted.

Net effect: 3.7 screens to 2.1, with every word still reachable.

### Changed — a failed pass or a failed write now reaches the report

Every fit pass and every mesh/sidecar write ran inside a silent catch, so a
pass that RAISED was indistinguishable from one that ran and did nothing.
30 such handlers for passes and 19 for writes are now 0: failures are recorded
and travel back in the conversion report. Grep it for PASS FAILED.

### Changed — the fit metric

- **Added** the validated clipping test: a body vert is clipping when the
  garment sits *behind* the skin (ray out escapes, ray in hits a
  same-facing triangle). Calibrated against a user-confirmed clean/dirty pair:
  reads 0.00% on the clean armour, 8.87% on the one that clips.
- **Added STANDOFF** as the counter-metric. Clipping has no upper bound, so an
  over-inflated garment scores a perfect 0.0% — which is exactly how
  over-inflation reached the user twice without any number complaining. Anchor
  from the clean armour: median 1.15u, p90 1.52u.
- **Retired** `bust_verdict.py` and `postflight_1_2.py` off the discredited
  signed-distance path.
- **Retired the ray cone from `underbust_census.py`**, its last live consumer.
  The `surrounded` / `partial` / `bare` columns are replaced by `clipping`;
  rows written before 2026-07-29 are not comparable on those columns. Its
  negative control turned out to be blind to the very bug it was written for —
  a garment below z 80 produces no ray hits in *either* direction, so
  transposing the two directions left it passing. A positive control (densest
  band coverage must read mostly COVERED) was added, and verified to fail under
  a deliberately inverted metric while the negative control still passed.
- **Deleted** `mesh_penetration.containment`, `poke_report`, and `cone_dirs`
  (−155 lines).
  Both were anti-correlated with in-game ground truth and had zero callers and
  zero tests. `surface_penetration` stays: only its *sign* was bad, and the
  census uses its distance. The reasoning that discredited them is preserved in
  `docs/METRICS.md`, and the four metric requirements written for `poke_report`
  moved onto `clipping_report`, which actually satisfies them.
- **Added** an orientation gate that removes a false positive under morph
  (an inward ray striking the far side of the garment past a cut rim) without
  moving the calibration anchors.

### Added — a fit contract over the whole pass chain

The pipeline now states, per shape: **diagnose** what was wrong before anything
ran, **treat**, then **verify** the result and roll back to the best intermediate
state if the chain as a whole made things worse.

Applied to the chain rather than to each pass, because the per-pass version is
measurably the wrong contract. Over 48 traced shapes, exactly one pass ever
regressed bust fit (`conform`, 5 times), every one of those was recovered
downstream, and **0 of 48 shapes ended worse than they started** (total exposure
8138 → 245). `conform_to_source_standoff` pulls IN by design and later passes
push back out; reverting it would have blocked a correct pass and biased every
garment looser — which is the over-inflation reported twice from the game.

Cost: **two measurements per armed shape** instead of eleven. Checkpoints are
array copies, not measurements, so the chain remembers every pass and only pays
to inspect them when the final verify actually fails.

What it deliberately does not do is skip passes when the entry diagnosis looks
clean. Bind-pose clipping is blind to animation — "at rest" in game is an
animated pose — so gating passes on it would trade a measurable defect for an
unmeasurable one.

The per-pass guards on the anti-poke and the soft-cloth inflate were removed in
favour of this: neither ever regressed across the traced sample, and the chain
verify covers the outcome for a quarter of the measurements.

### Added — instrumentation, because the chain was speculative

Twelve passes computed against the body, all assumed the garment was in body
space, none asserted it, and nothing between them measured whether a pass
helped. That is how one frame error corrupted all twelve in silence.

- `src/fit_metrics.py` — the canonical in-converter metrics module.
- **Frame precondition**: every phase-2 shape's frame choice is checked and
  recorded when suspect.
- **`ChainGuard`** — the contract described above. An earlier `FitGuard` guarded
  two individual passes and was removed within this same release once the trace
  showed neither ever regressed; the chain verify covers the outcome for a
  quarter of the measurements.
- **`PassTracer`** (`CBBE2UBE_PASS_TRACE=1`) — before/after at every pass
  boundary with shared measurements, so N passes cost N+1 measurements. This is
  what found the groove-smoothing regression, and what showed that guarding each
  pass was the wrong contract.
- **`minimum_push`** — conditional, one-sided targeted push for residual
  exposure. Exits having moved nothing on ~94% of shapes, never moves
  chain-driven (SMP) verts, and reverts on regression.

### Added — physics and follow

- **Bust collider split** and **torso jiggle graft** now default ON, both
  confirmed in game.
- Chest-follow: the material ceiling caps *this pass's graft*, never the
  pass-through, fixing a conflict where the torso graft and the follow pass
  together scored worse (0.325) than either alone (now 0.786).

### Added — per-pass STANDOFF trace (`CBBE2UBE_STANDOFF_TRACE=1`, default off)

The existing trace measures clipping and is blind to the opposite defect. This
reports standoff per pass in 3u slabs up the torso, reading the snapshots the
chain already keeps, so it needs no re-conversion. Slabs rather than one window:
a single median over z105–114 read identically for all nine arms of a bisect
because hit density varies ~10× across it.

### Added — standoff recorded up the whole torso, not just the bust front

The ceiling guards `z 90–102`, and that was the only region any pack-wide record
had ever covered. A gap reported in game sat at **z 108–114**, so "1.31u median,
within ceiling" was an accurate statement about a region the user was not
looking at. The under-bust had been an open lead for weeks with no numbers
behind it at all.

Four bands are now recorded per shape — `underbust` (z 78–90), `bust` (90–102),
`upperchest` (102–108), `strap` (108–114) — **separately, never merged**. Hit
density varies ~10× up the torso, so a single median over the whole range is
pinned by whichever slab has the most covered skin; a nine-arm bisect that
aggregated `z 105–114` read identically for every arm for exactly that reason.

**No verdict on the new bands.** `over` stays on the bust record alone, because
it is the only band with an anchor confirmed correct in game. Standoff rises
monotonically up the torso — measured 1.17u at the bust to 1.94u at the strap
line — so applying the bust ceiling higher up would manufacture failures on
nearly every garment. These ship as data; the next full run is what produces
enough of them to calibrate against.

The calibrated bust record is untouched, mask and all, since the 1.15u/1.52u
anchor depends on its exact definition. The bands use the sparse `_ClipTester`
path rather than `standoff()`, whose dense formulation reached 15 GB measuring
several bands on one cuirass; a test asserts the two agree to 1e-6 on the same
index, so the mixed implementation is verified rather than assumed.

### Added — `scripts/analysis/audit_sink.py`

One reader for `standoff_audit.jsonl`. Nothing in the repo read the frame, chain
or band records, so every analysis was hand-written — more than fifty times in a
day, each re-deciding which field to trust. It refuses to repeat four specific
mistakes: it reads `shipped` and never `final`; it reports measurement
**failures first** rather than burying them under clean averages; it excludes
first-person viewmodels from standoff *and states how many*; and it prints
percentiles rather than inventing ceilings for bands that have no calibrated
anchor.

### Performance — skeleton bone resolution

The normalised skeleton bone set was rebuilt on every membership test
(1.9M redundant calls over five pieces). Cached; about 5-6%, output identical.

### Performance — the ray cast, which is what made the contract affordable

Three exact optimisations, each verified against brute-force Möller-Trumbore
rather than against a previous run's numbers:

- candidate pruning moved off lists-of-Python-lists onto a C-level sparse
  distance matrix (**4.0×**);
- the ball query is bucketed by triangle radius, so one 6u triangle no longer
  sets the search radius for the whole mesh (a further **1.6×**);
- a ray-line cull before the intersection test — 4% of candidate pairs survive
  it (**2.0×** on the cast).

A region measurement went from **391 ms to ~120 ms**. End-to-end conversion time
is unchanged within run-to-run noise: fit measurement simply is not a
significant fraction of a piece's conversion cost, which is the point — the
contract is free in practice.

### Performance

- Canonical body skin and skeleton are cached: **133s → 30s** per armor.
- Ray casting gained an exact range cull and early-out over triangle blocks:
  **6.4h → 2.1h** on a full census.
- The incremental-rebuild floor now includes a hash of every `CBBE2UBE_*`
  environment variable and the NIF-relevant arguments, so changing a setting
  correctly invalidates stale output instead of silently reusing it.

### Performance — stop measuring garments that cannot be hit

`ChainGuard` armed on the **body** region size, which is a constant (the UBE
band is 5249 verts against a floor of 50), so *every* phase-2 shape armed. Of
4382 armed shapes in the previous run, only 1605 covered the bust band —
**2777 (63%) measured and found nothing**, and `record_standoff` ran the full
measurement before discarding it, on the dense path.

`garment_reaches()` gates all three call sites on bounding-box overlap, which is
conservative by construction: a garment's box contains all its triangles, so a
box that does not overlap cannot hold one that does. It can only admit work,
never skip a real hit, and it fails **open**. The risk is one-sided, so the test
asserts directly that whenever the gate says skip, the ray cast finds zero hits.

`record_standoff` also moved off the dense `standoff()` onto the sparse path,
with a test pinning that the recorded median still matches the dense result on
the calibrated mask.

> **Not yet measured end-to-end.** These remove work; what fraction of
> conversion time that is has not been profiled. No speedup is claimed.

### Removed — #groove-nipple-hold

It held the groove smoothing back over a protrusion, and earned that while it was
the only thing protecting the tip. Once the surface requirement landed it became
actively harmful. Ablated across all 112 installed BodySlide presets:

    hold ON    1 preset still poking, nipple clearance +0.220u
    hold OFF   0 presets poking,      nipple clearance +0.528u

with identical torso fit and identical crinkle on every shape. Deleting it
cleared the last holdout.

### Read this before trusting the numbers

The fit figures below come from the mesh harness, in bind pose, on a specific
body. They are necessary but **not sufficient** — bind-pose metrics are blind to
what animation does, and the worst remaining clipping lives on SMP/soft-body
cloth that no skin pass can reach. In-game verification is still the gate.

### Tooling and project

- Report intake (issue templates, diagnostics zip), CI on contributor lanes,
  and CI coverage for Python 3.10 — the runtime the shipped exe actually uses.
- The mod-agnostic tracked-content policy is now enforced by a test rather than
  trusted.

### Known issues

- Butt clipping ~11.6%: needs a physics `can-collide-with-tag` change, not a
  mesh pass.
- Under-bust side (z 80–86) is unresolved; the metric's resolution there is only
  ~10–14 verts.
- Loose robe chests can still bake in roughly +3u of over-inflation from the
  static warp/clearance path.
- The chain-gate on finalize remains reverted.
