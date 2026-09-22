# Measurement audit

Which of our metrics give accurate answers, which gave wrong ones, and what
replaced them. Audited 2026-07-26 after several conclusions had to be withdrawn.

**The rule this produced:** validate a metric with a POSITIVE control before
trusting it. A metric that reports "no problem" is indistinguishable from a metric
that cannot see the problem, and we shipped decisions on that ambiguity three times
in one day.

**Tool names below are not all in the repo.** Several metrics were first written
as one-off session tools that were never tracked; the sections keep them because
the *metric* is the durable part, but a bare name like `layerfollow.py` is not a
file you can run. Anything under `scripts/analysis/` is real and tracked;
anything else is marked at its section.

---

## Sound

### Ray-based skin exposure — `scripts/analysis/verify_skin_exposure.py`
March from each body vertex along its own outward normal; if no armour triangle
blocks it, that skin is visible. Unambiguous by construction — no sign to guess.

Validated on real geometry before use:

| control | expected | measured |
|---|---|---|
| lower legs (armour ends above them) | ~100% | **100.0%** |
| shins | ~100% | **100.0%** |
| mid-chest under the cuirass | 0% | **0.0%** |
| belly under the cuirass | 0% | **0.0%** |
| upper back under the cuirass | 0% | **0.0%** |

Plus 9 synthetic tests with analytically known answers (`tests/test_skin_exposure.py`),
including the two cases that broke its predecessors: a ray through the gap between
two plates, and a double-sided shell.

### Follow ratio (armour jiggle weight / body jiggle weight underneath)
Cross-validated: it orders three armours exactly as the ray test does.

| armour | follow | skin visible at 3u bounce | at 6u |
|---|---|---|---|
| hide cuirass | 0.00 | 33.3% | 100% |
| leather replacer | 0.25 | 0.0% | 17.5% |
| leatherdark | 1.46 | 0.0% | **0.0%** |

Two independent methods agreeing on the ordering is the reason to trust either.
Restrict to armour verts within ~4u of the body; beyond that the nearest body vert
is not what drives the armour vert and the ratio is meaningless.

### Zero-weight bone detection — `scripts/analysis/verify_zero_weight_bones.py`
A bone in a shape's list carrying no weight above the write threshold. Directly
observable, no inference. Measured 59 across 42 shapes.

### Vertex counts, triangle edge lengths, bone lists, z extents
Direct properties. Used to sanity-check the inferential metrics, and the y-extent
check is what exposed the broken signed-normal metric.

---

## Wrong, and what replaced them

### Signed distance via the nearest triangle's normal — **REPLACED**
**Symptom:** reported 135/1110 nipple verts "outside" the leather cuirass.
**Truth:** 0/1110. The armour's surface sits 2.4u IN FRONT of the nipple.
**Cause:** a cuirass is a shell with a front face and an inner face. A body vertex
sitting safely inside the cup is near BOTH, and whichever sample happens to be
nearest decides the sign — so the sign is essentially arbitrary there.
**Fix:** ray casting (above). **Caught by:** an independent y-extent check
disagreeing. Any conclusion drawn from the signed-normal numbers is void.

### Master-tier classification by file extension — **FIXED**
**Symptom:** "12 and 15 master-ordering violations in the loaded ESPs" — a crash-class
alarm.
**Truth:** 0. **Cause:** an ESL-flagged `.esp` is master-tier while looking regular;
`auto_convert` documents this exact false positive.
**Fix:** read the TES4 header flags (`ESM 0x01 | ESL 0x200`). See
`project_plugin_tier_classification`.

### Weight-partner divergence by vertex count — **FIXED IN CODE**
**Symptom:** 19 warnings, none actionable.
**Truth:** all 17 measurable divergences peaked at <= 0.114 weight (median 0.025) —
inert bones a graft brushed at 2%, scored identically to a bone at 90%.
**Fix:** `_weight_partner_scale_divergence` now also requires `weight_min=0.10` peak
weight on the present side. Calibrated so 0.025 goes silent and 0.114 still warns.

### `ray_blocked` first implementation — **FIXED DURING THE BUILD**
Möller-Trumbore determinant used a 1-D einsum subscript (`'ij,j->i'`) against a
per-triangle array; raised immediately rather than returning a wrong answer. Now
`'ij,ij->i'`. Recorded because a crash is the good failure mode — the metrics above
failed silently, which is far worse.

### Signed distance taken from a mesh's STORED normals — **FOOTGUN, 2026-08-11**
A source mod's bundled body shipped **all 6463 vertex normals as zero**. Every
signed distance computed as `(p - v) · n` against it therefore returned exactly
`+0.000` — for every point, in every band — and read as a clean, precise
measurement rather than as no measurement at all. It survived a whole round of
analysis and produced a confident wrong conclusion ("the author placed these
bones exactly on the surface") before the constant-zero pattern gave it away.

**Rule: derive normals from the triangles, or assert `|n| ≈ 1` before using
them.** The converter already knows this on the write side —
`_body_normals_or_compute` exists precisely because "BodySlide body outputs
frequently ship ZERO/absent vertex normals" — so the analysis side has no excuse.
A result that is *exactly* zero to full precision across a whole population is
almost never a measurement; check the operand before believing it.

### A census over an EMPTY population — **FOOTGUN, 2026-08-11**
A verification conversion aborted (the single-piece script *requires* `--esp` or
`--slots` and errors rather than guessing). It left no output. The census then
walked that empty directory and printed `0 broken / 0 bones / 0 verts` — which
is character-for-character what a successful fix looks like. It was one step
from being reported as "the fix generalises".

**Rule: a scan must refuse an empty population, not score it.** Any harness that
reports a rate must print its denominator and abort when it is zero. Same family
as the shrinking-denominator rule below: 0/0 is not a pass.

### Diffing against a STALE artifact — **FOOTGUN, 2026-08-11**
Two censuses and a "the skirt has no physics XML" finding were all produced
against an output mod that had not been rebuilt in three weeks. The live output
is a **different mod directory**; the stale one still existed, still parsed, and
still answered every question plausibly.

**Rule: establish which artifact is live before measuring it** — newest mtime
across candidates, or the path the tool is actually configured to write. A mod
folder full of valid NIFs is not evidence that they are *this build's* NIFs.

### "Flipped normals" counted a PASS FIRING, not a defect — **RETRACTED, 2026-08-11**
`normcheck.py` (never tracked; a session tool, and the finding below retracted it
anyway) reports a vertex as *flipped* when its stored normal disagrees with
the one its triangles imply. That reads like a shading defect. It is not: the
stored normal **is** the triangle-implied recompute, after
`_recompute_vertex_normals` sign-aligns it to the source. So the count is exactly
the number of vertices where that sign-alignment fired, and the source scores 0 by
construction rather than by being clean.

It was used all session as a quality number — "belts 332 → 84 → 50" — including as
evidence for a change that was then partly justified by it. On the reported strap
**all 84 were boundary verts**, which is the one case the sign-align exists to
handle, so the "defect" may well have been the repair working.

**Rule: before quoting a count as quality, ask what a passing score would require.**
If the answer is "the pass never fired", it is an activity counter. Two independent
detectors here disagreed 40× (2 vs 82) — that gap was the tell, and the cheaper
detector was the honest one.

### Surface roughness cannot see crumple on a TEXTURED shape — **INSUFFICIENT, 2026-08-11**
Absolute Laplacian |v − mean(1-ring)| is a sound quantity (it caught the warp
outlier defect). It is the wrong instrument for "is this strap crumpled", because
the author's own strap is studded and textured: 451 of its 3234 verts already
exceed the 0.5 threshold. Output 631 vs source 451 is a real difference, but the
signal is buried in authored detail, and every parameter that removed it touched
17–65% of the shape — a resurfacing, not a repair.

**What worked instead: edge length.** A belt bends, and bending preserves every
edge; stretching does not. Frame-free, no rigid fit, so — unlike every Kabsch-based
test on this project — it does not degenerate on a 2u-wide strap where the 1-ring
is nearly collinear. It separated the strap (mean |ratio−1| **0.219**, edges from
0.517× to 1.514×) from the chest plate on the same garment (**0.060**), and read
exactly **0.000** on a shape a rigid pass had just made isometric, which is the
sanity check that the instrument works.

**Rule: pick the metric that a correct-but-different shape scores well on.** A
textured surface must be allowed to be bumpy; nothing is allowed to have its edges
halved.

**...but WEIGHT IT BY LENGTH — FOOTGUN, 2026-08-11.** The ratio divides by the
authored length, so a 0.02u edge stretched to 0.06u scores 3.0. On the reported
buckle **1% of edges (29 of 2938) inflated its mean deviation by 75%**: raw 0.364,
short edges dropped 0.209, length-weighted **0.174**. Every belt number quoted
during that session was the raw one, including a "the buckle is worse than the
strap ever was" comparison that does not survive the correction.

Not a general rule that short edges are noise: a chest plate on the same garment
is 10.8% short edges and its raw and weighted values agree to 0.002. The
contamination needs short edges AND large stretch together — which is itself a
signal worth looking at, since those edges are real geometry being blown up.
Report both; if they disagree, the raw one is wrong.

---

## Sound

### Inter-layer follow divergence — `layerfollow.py` (2026-08-11) — **TOOL GONE, metric stands**
> Never tracked; a session tool. Nothing in `scripts/analysis/` replaces it —
> `follow_bands.py` measures garment-vs-BODY follow, not layer-vs-layer. Rebuild
> from the definition below if the question comes back.
For each vertex of layer A, the nearest vertex of layer B; if within 2.0u they
are STACKED, and the metric is the mean L1 distance between their weight rows in
a common bone basis. 0 = the two points deform identically; 2 = completely
different bones. This is what catches "layers clipping into other layers", which
**no position metric can see** — the stored vertices are correct and the layers
separate only once animated.

Its counter-metric is mandatory and lives beside it: total breast/butt/belly
weight per shape, plus the mean row distance to the body underneath. A change
can drive divergence to zero by making every layer follow nothing, and one did.

### Weighted chain bones flat at the origin — `chaincensus.py` (2026-08-11) — **TOOL GONE, metric stands**
> Never tracked. `scripts/analysis/chain_flag_census.py` is a different question
> (which pieces a given chain flag can reach), not this one.
A bone some shape is WEIGHTED to, that the actor cannot resolve, whose node is
parented to `Scene Root` at identity. Broken by definition: a real chain bone
has a position on the body, and at the origin it drags its verts to the
character's feet. Self-contained — needs no source mapping, so it is independent
of which mod a piece came from.

Controlled both ways before it was trusted: **0 broken** on a known-good 1.2
build of a piece, **23 bones / 22,306 verts** on the known-broken build.

---

## Sound, but over-interpreted

### Nearest-vertex distance to the body ("bust clearance")
The source of every `min gap` / `% verts < 0.5u` number produced this session. It is
an accurate DISTANCE and nothing more:

- it cannot say inside or outside;
- it ignores the surface BETWEEN vertices (densified sampling found the surface
  0.6u closer than the vertex metric implied on one cuirass);
- it says nothing about motion, and the defect being chased is a motion defect.

The numbers are not wrong; the conclusions drawn from them were. "min gap improved
0.137 -> 0.250" is true and does not mean the armour stopped clipping.

### Bind-pose measurement generally
Three of the day's four failed fixes were chosen from rest-pose numbers. The body in
game is animated and physics-driven; the SMP config permits the breast chain up to
6.0u of travel against ~1.0u of clearance. Rest-pose evidence cannot rank a fix for a
motion defect. See `project_antipoke_vertex_blind`.

---

## Checklist for the next metric

1. **Positive control** — a case that MUST report a problem. Without it, "0%" is
   unfalsifiable.
2. **Negative control** — a case that must report nothing.
3. **Break the metric and confirm the controls scream.** A control that cannot
   fail is not a control. Negate the normals, invert the sign, disable the pass —
   whatever the failure mode is — and check that a control actually goes red.
   Added 2026-07-29 after a negative control that had guarded a census for a week
   turned out to pass just as happily with the ray sense transposed: the garment
   it used produced no hits in EITHER direction, so there was nothing for the
   inversion to change. Third occurrence of a passing control measuring nothing.
4. **A second, independent method** on the same input. Disagreement means at least
   one is wrong; agreement is the only real evidence.
5. **Synthetic tests** with analytically known answers.
6. **State what the metric cannot see**, next to the number it reports.
7. **Ship a counter-metric when the metric is one-sided.** Clipping has no upper
   bound, so "0.0%" is also what a balloon reads. Anything with a floor but no
   ceiling needs a paired measurement or it will be optimised off a cliff.
8. **A sample that lands entirely in one state establishes no rate.** When the
   question is "how often does X happen", the sample must be able to observe
   both outcomes — otherwise sweep the population. Added 2026-07-30: a 9-mod
   census reported "42 of 42 shapes ran the pass", which was read as "the bug is
   rare". A full sweep of all 482 phase-2 pieces put it at **21.4%**. The census
   was not wrong about its 42 shapes; it was never capable of answering the
   question asked of it.
9. **Say which population, and prove the filter.** Of those 103 matches only 22
   reach the output — the rest are HIMBO/male bodies the female-only policy
   never converts. Quoting either number without the filter misleads, in
   opposite directions. See also the repeated failure of censuses that counted
   never-converted clutter as results.
10. **Measure against the body the population was built on, and prove which
   file that is.** A reference picked by name or path is a guess. Added
   2026-09-21: `canonical_cbbe` ("3BA in the path, then the shortest path")
   picked the 3BA body mod's own preset build, while the game loads BodySlide's
   zeroed build and 248 of 340 paired source garments bundle that one
   bit-exact. Every author-side number, the crotch-band lead included, moved
   when the reference did. Resolve the file the game loads, check it is the
   build you assume, and say which file each number was read on.

---

# 2026-07-27 — rear/penetration metric rebuilt, and a repeat offence

## The same wrong metric got rebuilt from scratch

`scripts/analysis/mesh_penetration.surface_penetration` decides inside/outside from the nearest
TRIANGLE's normal — which is the metric already recorded above as **REPLACED**. It was
re-derived from first principles, given eight passing unit tests on synthetic spheres,
and run over the whole pack before anyone re-read this file.

It failed exactly as documented: a garment is a shell with an outer and an inner face,
a body vertex inside the cup is near BOTH, and the nearer face decides the sign
arbitrarily. Symptom this time was a **~20–30% "poking" floor in EVERY body region**,
which no one sees in game. Winding was clean (agreement 0.992, min 0.941 over 314
armors), so orientation was never the issue — the shell is.

**Synthetic tests did not catch it.** A closed sphere has no second face near the
sample point, so every unit test passed. The failure needs a real shell to appear.
That is the lesson worth keeping: *a positive control has to include the geometry the
metric will actually meet.*

`surface_penetration` is kept for its UNSIGNED DISTANCE, which is sound. Its sign must
not be used.

## Sound: ray exposure, and a census built on it

`scripts/analysis/mesh_penetration.ray_exposure` — the ray test listed first under
**Sound** above, reimplemented as a library function: unambiguous by construction,
and it is what a player sees. Positive controls in
`tests/test_mesh_penetration.py`: enclosed body **0.0%** exposed, uncovered body
**100%**, a garment hanging 4u away still reads 0% (it blocks the ray), and a one-sided
hole localises to that side.

Sanity check on real geometry, the check that settles it: a full-length robe reads
**0.0% exposed in every region**. The nearest-vertex metric claimed 29.5% butt
poke-through on that same mesh.

`scripts/analysis/collect_penetration_census.py` — full census, one row per armor, six regions,
400-vertex sample per region, fixed seed. Records `pct_exposed`, `pct_exposed_near`
(exposed AND garment within 2u — the defect signature, as opposed to skin bare by
design), unsigned distance percentiles, SMP-rigged flags, and the discredited
nearest-vertex number so the disagreement stays queryable.

### Result over 314 armors (first-person viewmodels excluded)

| region | exposed mean | median | exposed&near mean | median | old metric |
|---|---|---|---|---|---|
| breast | 11.6% | 4.0% | 6.8% | 2.8% | 5.5% |
| **upper chest** | 27.8% | 14.2% | **20.4%** | **12.8%** | 8.3% |
| belly | 23.7% | 18.5% | 9.0% | 5.8% | 8.4% |
| **butt** | 12.8% | **0.0%** | **4.6%** | **0.0%** | 22.4% |
| lower back | 25.6% | 17.4% | 9.2% | 5.5% | 12.1% |
| thigh | 21.6% | 0.0% | 5.4% | 0.0% | 14.4% |

Armors with >5% exposed-and-near: upper chest **187/303 (62%)**, belly 164 (52%),
lower back 160 (51%), breast 120 (40%), thigh 61 (20%), **butt 50/313 (16%)**.

**This reverses the earlier finding.** The butt is the LEAST affected region by the
sound metric — median exactly 0.0% — where the discredited one ranked it worst at
22.4% and justified a rear-clearance feature that was built and reverted. The real hot
spot is the **upper chest / neckline**.

**Known limitation:** the viewmodel exclusion matches `1st` / `firstperson`, so stems
using `_fp_`, `FP` or `1person` still slip through and rank high by construction.
Ignore those rows or widen the filter before quoting a worst-offender list.

## Exposure is COVERAGE, not a defect — the poke/neckline/uncovered split

> **CORRECTED 2026-07-28 — the rim-distance form below is UNSOUND on boundary-heavy
> garments.** It decides poke-vs-neckline by distance to the garment's open boundary.
> On a cuirass where 61% of verts lie on a boundary, every exposed vert is inside the
> rim threshold and the rule CANNOT return "poke" — it reported 0.0% poke on armour the
> user could see the body coming through. Use a CONTAINMENT test instead: cast a cone
> of rays (10 dirs, 50°) round the exposed vert's outward normal and count how many are
> blocked by garment; surrounded = poke. On the same mesh that read 0.0%, containment
> found **9.0% of exposed verts strictly surrounded and a further 31% partial**. Before
> trusting any classifier, check what fraction of the input already satisfies its
> threshold.
>
> (An earlier draft of this note said "49%". That was strictly-surrounded and partial
> added together and quoted as one number; the strict figure is 9.0%. Keep the two
> separate — partial means *some* garment nearby, not body-through-armour.)
>
> **The containment cone recommended here was itself discredited and deleted on
> 2026-07-29** — anti-correlated with in-game ground truth (see "Discredited and
> DELETED" below). The validated replacement is `clipping_report`.

The upper chest looked like the worst region in the census above (62% of armors over
5% "exposed and garment within 2u"). Almost all of it is **garment design**.

`pct_exposed_near` is not sufficient on its own: at a NECKLINE the garment IS within
2u — just below the rim. Splitting on distance to the garment's open boundary
(`boundary_points`, edges used by exactly one triangle) separates the cases:

| class | test | meaning |
|---|---|---|
| **poke** | exposed, garment within 2u, **>4u from any rim** | garment all around it, body coming through — the defect |
| neckline | exposed, garment within 2u, near the rim | the garment ends here — design |
| uncovered | exposed, no garment within 2u | bare skin by design |

Over the 187 flagged armors: **poke 0.4% mean, neckline 13.6%, uncovered 30.0%.
Only 6 of 187 exceed 5% poke; 1 exceeds 15%.**

**Rim distance ALONE does not work** and was tried first: it is large both deep inside
coverage AND completely outside the garment, so it scored a towel and a bra at 100%
poke. Both conditions are required. `classify_exposure` enforces that, and
`test_rim_distance_alone_would_misclassify_a_bare_body` pins it.

**Conclusion for the upper chest: there is no systemic defect to fix.** The population
signal was coverage. Six armors have a genuine poke-through and are individually
actionable; a pass aimed at the region as a whole would be tuning against garment
design — the same mistake as the reverted rear-clearance feature, one level subtler.

> **OVERTURNED 2026-07-28 by the containment census — see below.** That conclusion
> rested on the rim-distance classifier corrected above, which structurally could not
> return "poke" here. Re-measured with containment, the upper chest is the **worst**
> region in the pack, not a clean one. That reversal rested on the ray cone and
> was itself voided on 2026-07-29 (below), so neither verdict on the upper chest
> rests on a sound metric now.

---

# 2026-07-28 — pose metrics, and a limit on the single-piece harness

## Sound: pose-induced coverage regression

`scripts/analysis/multipose_clip_test.py` over `scripts/analysis/pose_set.py`. Body verts COVERED at bind
and EXPOSED under a pose, as a fraction of the covered set — self-baselining, so a
bikini and a robe are comparable. Exposure itself is the ray test already listed as
sound; the addition is that the body MOVES.

Controls: identity pose reproduces the bind mesh to 0.000000u and is asserted on every
run; every region must be driven by poses that can actually move it (a test pins that
the chest is driven by torso/arm bones, not legs — the blind spot that made a previous
harness report the chest as clean).

Positive control on the metric's own claim: a full-length robe reads 0.0% exposed in
every region, where the discredited nearest-vertex metric claimed 29.5%.

## Sound: source-vs-converted delta

`scripts/analysis/source_delta_census.py`. Level metrics cannot separate "the converter broke
it" from "the author made it that way" — a confound that produced four withdrawn
conclusions in one day, including a 30-armour worst-offender list that was mostly
armour already behaving that way (top thigh failures: converted 83.3%, SOURCE 82.6%).

Two requirements, both learned by getting them wrong first:
* **Canonical bodies on both sides.** Pairing each garment with whatever body its own
  NIF bundles is biased three ways — sources bundle DIFFERENT bodies, some bundle none,
  and every "pick the body" heuristic tried picked the wrong shape (a full-length robe
  out-spans a real body; a `Stabilizer` at z −47.5 redefines the floor; the UBE body has
  head bones but no foot/hand bones while a robe has both).
* **Source chosen by garment SHAPE-NAME overlap**, not "the last mod providing this
  path". Two mods can ship one path with different geometry; picking the wrong one
  inverted a measured delta from +0.7 to +83.3 — from "authored" to "our fault".

> **CORRECTED 2026-09-21 — the canonical CBBE body was itself a wrong pick.**
> `canonical_cbbe` took "3BA in the path, then the shortest path", which on the
> shipped modlist is the 3BA body mod's own `femalebody`: a preset build the game
> never loads (a BodySlide output wins that path). It sits up to 1.97u off the
> body the game loads over 16,061 torso verts, and its weight-1 morph grows the
> bust ~1u THROUGH garments built on the zeroed body (248 of 340 paired source
> NIFs bundle the zeroed build bit-exact). Canonical now means BodySlide's zeroed
> build as the game loads it (`src/zeroed_body.py`, via `canonical_body`); the UBE
> side resolves to the same file as before. The source-side re-run on that body is
> recorded in the 2026-09-21 reference-body entry at the end of this file. The
> requirement stands, made stricter: canonical AND the body the game loads.

## LIMIT: `convert_one_armor.py` does not exactly reproduce the auto pipeline

The single-piece harness is the basis of most measurements here, and memory described
it as faithful. Measured on one cuirass: **750 of 4833 garment verts differ from the
pack's own output, up to 0.4195u (mean 0.0057u)**.

Ruled out, each by producing byte-identical output: the GUI settings the CLI does not
read (4 flags are ON in the live settings file), the source mod (only one ships that
mesh), and the biped slot mask (`0x4` vs the real `0x114`). Another piece DID reproduce
exactly, so it is piece-dependent — more likely a specific pass than a global ordering
effect.

**Consequence:** treat single-piece numbers as indicative, not as pack truth. Effects
of a few tenths of a percent are inside this noise; the large ones measured here
(11.0% → 0.2%) are not. Diffing the `convert_nif` call arguments between the two paths
is the obvious next step.

> **RESOLVED 2026-07-29.** The harness now builds the batch's own work item and
> hands it to the same `auto_convert._nif_convert_worker` — one conversion path,
> not two (`scripts/convert_one_armor.py` docstring; pinned by
> `tests/test_single_batch_parity.py`). The differences it removed: slots
> resolved to 0 for a mod with no ESP, silently disabling every slot-gated pass
> (a slots=0 run is now a hard error); an output root without `!UBE`, which
> changes the relative paths baked into the NIF; and alt-texture shape names
> never passed. The consequence above applies to single-piece numbers taken
> before that date.

## Reminder: the pose harness poses but does NOT morph

Stated in its own docstring and worth repeating because it bounds every number above.
On a piece whose pose behaviour is clean, a full breast slider takes exposure
4.5% → 12.1%. The morph path is a separate class, unexamined, and on that piece the
larger one.

> **EXAMINED 2026-08-26, and it is the larger class on more than that piece.**
> `morph_clip_test.py --preset` applies a real BodySlide/RaceMenu preset to the
> BODY AND THE GARMENT together. Over a 6-piece x 14-preset grid and a 90-piece
> pack census, the finding is structural rather than incidental:
>
> **BIND-POSE CLIPPING IS 0.000% AT EVERY STAGE OF THE CHAIN on a piece whose
> morphed clipping runs 2-7%.** Not small — ZERO, at entry, warp, inflate,
> conform, groove-smooth, panel-rigidity, anti-poke, seam-weld and in the
> written NIF. So every bind-pose column in this document, and every stage
> ledger built on one, is not merely a best case for this class; it is
> identically blind to it. Five separate metrics returned clean against a user
> who could see the defect for exactly this reason.
>
> Pack census, body-swap pieces only (the copy path has no injected body to
> morph against, so it is out of scope, NOT healthy): **21 of 90 are clean at
> bind and clip under a preset** — replicated at 23 of 100 on a second run
> through a different densify implementation. The metric DISCRIMINATES: 69 of
> 90 are not in the class.
>
> Split that bucket before quoting it. It mixes three defects: follow ~1 with
> low chain weight (the chord class), follow ~0 (a morph-FOLLOW gap, a
> different bug), and high chain share (physics cloth, which this model states
> outright it does not cover). The census tail — a 72.8% dress — turned out to
> be follow 0.00 on SMP cloth, and its CLEAN preset clipped MORE than its
> clipping one, so it was never this class at all.
>
> **Ask FIRST whether a report is preset-dependent.** If it is, no bind-pose
> number is evidence either way.

---

## ~~Sound~~: containment census over the rigid population

> **OVERTURNED AND DELETED 2026-07-29 — see "the ray cone was never sound" below.**
> `containment()` no longer exists. It is anti-correlated with in-game ground
> truth: it scores the armour the user confirms CLEAN *worse* than the one that
> visibly clips. The RANKING below is therefore not usable either — the caveat
> in this section was too weak, not too strong. Kept for the record chain.

`containment()` in `scripts/analysis/mesh_penetration.py`, 199 fully-rigid armors, bind pose,
10 rays at 50° half-angle, tmax 6u. Of **exposed** body verts, the fraction with
garment around them:

| region | strictly surrounded | + partial | armors >2% of region verts |
|---|---|---|---|
| **breast** | **7.5%** | 29.3% | 21 / 199 |
| **upper_chest** | **7.1%** | 34.4% | **55 / 199** |
| belly | 3.3% | 16.1% | 12 / 199 |
| lower_back | 2.1% | 12.1% | 9 / 199 |
| butt | 1.3% | 6.3% | 6 / 199 |
| thigh | 1.0% | 6.9% | 6 / 199 |

**The chest is 5–7× worse than any other region** and 8 of the 10 worst individual
scores are `upper_chest`. This reproduces the in-game report (chest underside clipping
in most poses including idle; butt and thighs fine) from an independent measurement.

`tmax` is **not** a sensitive knob: the confirmed piece reads 9.0% strictly surrounded
at both 6.0 and 40.0. It only shuffles partial↔bare.

### Two footguns, both of which produced wrong numbers before this run

**Ray sense.** `ray_exposure` returns True = ESCAPED; `rays_hit` returns True = **HIT**.
`containment()` uses the former, so its `~` is correct — copying that line into a script
built on `rays_hit` inverts the metric. Symptom: 99.6–100% "surrounded" and 0.0% bare
everywhere, including a pants mesh scoring 100% breast poke. **Positive controls cannot
catch this.** Every run must carry a NEGATIVE control: a garment that physically cannot
cover a region must read 100% bare. Two are pinned (pants and underwear scored at the
breast) and both read 0.0% surrounded / 100% bare.

**Sampling dilution — the subtler one, and it limits the table above.** The census
samples 400 verts per region, which averages a narrow-band defect into nothing. The
piece confirmed by eye scores `breast exposed=40 poke=2` at region level, while a
targeted under-bust analysis of the SAME mesh finds **155 exposed / 14 strictly
surrounded**. So the percentages above are **floors, not magnitudes** — only the
RANKING is usable. Do not quote "0.7% of breast verts poke" as a health figure.

All bind pose, so all of it is a best case; see the pose-regression section above.

## ~~Sound~~: narrow-band pass over the breast UNDER-CURVE

> **METRIC REPLACED 2026-07-29.** The band location, the dilution arithmetic and
> the closed-vs-open split below all still hold — they are geometry, not metric.
> The `surrounded` / `partial` / `bare` COLUMNS do not: they were the same ray
> cone, and it is discredited. `underbust_census.py` now reports `clipping` from
> the validated test, so rows written before 2026-07-29 are not comparable on
> those columns. Its negative control was also blind; see below.

`scripts/analysis/underbust_census.py`. The region census ranks but cannot size, because the
under-curve is only **484 of 3674** verts the `breast` selector accepts — 13.2%, a
**~7.6× dilution**. That predicts the observed gap on the piece confirmed by eye:
region level scored `exposed=40 poke=2`, the band scores `exposed=80 surrounded=13`.

**Band located by measurement, not assumption.** Sweeping 2u z-slabs on the canonical
UBE body for where the surface turns from facing forward to facing down puts the
under-curve at **z 90–94** (mean nz −0.41/−0.39, down-and-forward 38.6%/41.1%), with
the apex at z 94–96 (max y 8.16) — agreeing with the independently pinned "apex ~95".

**A thing that looked like a second cause and is not:** the `breast` selector's
`ny > 0.3` keeps **78.8%** of the under-curve. The band's mean `ny` of +0.18 is over
the whole z-slab, not over the under-curve subset. Dilution is the whole story.

199 rigid armors, bind pose, 612-vert band:

| | verts | of exposed |
|---|---|---|
| band | 121,788 | |
| exposed | 7,705 (6.3% of band) | |
| **surrounded** | **491** | **6.4%** |
| partial | 1,439 | 18.7% |
| bare | 5,775 | 75.0% |

**Split closed vs open garments, or the number means nothing.** A low-cut top exposing
the under-curve is design; a closed one doing it is the defect. Splitting at 15% band
exposure: closed garments run **9.5%** surrounded-of-exposed against open ones at
**5.0%** — the defect concentrates where a garment claims to cover, which is the right
sign and is the reason the confirmed piece ranks 5th and 8th here while ranking only
12th and 15th on raw band percentage.

**Actionable population: 16 of 199 (8%)** — closed garments with ≥5 surrounded verts.
Seven show the purest signature, every exposed under-curve vert surrounded (28/28,
14/14, 12/12, 8/8, 3/3, 1/1, 1/1): a fully-closed garment with the body coming through.

**This is NARROWER than the region census, not wider — the expectation going in was
wrong.** Region-level `upper_chest` flagged 55/199; the band flags 16. Both are real
and they are different defects: the region test counts poke anywhere in z 99–112,
including necklines, straps and collars, while the band isolates the under-curve
specifically. Do not treat the 55 as a superset of the 16.

Negative control is enforced in-script and aborts the run: a garment entirely below
z 80 scored at the under-curve must read 100% bare (two picked from the population by
geometry, both PASS at 612/612). Bind pose only, so still a best case.

---

# 2026-07-29 — the ray cone was never sound, and what replaced it

## Discredited and DELETED: signed distance and the ray cone

Calibrated against user-supplied in-game ground truth on a pair the user judged by
eye: `CuirassLight` **CLEAN**, `CuirassMedium` **CLIPS**.

| metric | clean armour | clipping armour | verdict |
|---|---|---|---|
| signed distance (`surface_penetration` sign) | 32.4% | 23.9% | **inverted** |
| ray cone (`containment`) | 7.6% | lower | **inverted** |
| **clipping test** (`clipping_report`) | **0.00%** | **8.87%** | separates |

Both old metrics score the CLEAN armour **worse** than the clipping one, at every
depth threshold. The cause is structural, not a tuning problem: neither can separate
*"skin is outside the garment SURFACE"* from *"skin is outside the garment's
COVERAGE"*, so a small or open garment scores terribly by design and the figure is
dominated by rim geometry. No threshold rescues that.

`containment()`, `poke_report()` and `cone_dirs()` were **deleted** rather than
re-tuned (-155 lines). `surface_penetration` stays: only its *sign* was bad, its
distance is sound and the census uses it.

**Every conclusion on this page that rests on the cone is void**, including the
"upper chest is the worst region" reversal recorded on 2026-07-28. The 2026-07-27
`ray_exposure` work is unaffected — that measures coverage, and coverage is what it
was read as.

## The question the validated test asks instead

**Is the garment BEHIND the skin?** If a ray along the body's outward normal escapes
but the ray along the INWARD normal hits garment, the garment lies between the skin
and the body interior — the skin has come through it. Skin merely beside an open
edge escapes in both directions and is simply UNCOVERED, which is a cut, not a
defect. That distinction is exactly the one the cone could not make.

Area-weighted over the union of visible garment shapes, orientation-gated (the hit
triangle must face the same way as the skin, which removes a false positive under
large morphs where sagging skin passes the cut rim of a cup).

## STANDOFF — the counter-metric that was missing entirely

Clipping has **no upper bound**. An over-inflated garment scores a perfect 0.0%
because nothing pokes through a balloon — which is how over-inflation reached the
user twice with no number complaining. Standoff measures how far off the body the
finished garment actually sits, anchored on the confirmed-clean armour: median
**1.15u**, p90 **1.52u**. The over-inflated probe read median **2.88u** at 0.21%
clipping. **Never report clipping without standoff beside it.**

## A negative control that could not fail

`underbust_census.py` asserted that a garment entirely below z 80 reads 0 clipping
and ~100% uncovered, and claimed this pinned the ray sense. **It does not.** Such a
garment produces no ray hits in EITHER direction, so transposing the two directions
leaves both numbers untouched. Verified by re-running the census with the normals
negated: the negative control passed regardless.

A POSITIVE control now carries that job — the garment with the densest band coverage,
picked by geometry rather than by the metric (picking it by the metric would be
circular), must read mostly COVERED. Under the same inversion it collapses
**100.0% → 0.0%** and fails. The run aborts if either control cannot be built.

**Generalised** as checklist item 3 above: break the thing a control guards and
confirm it screams before trusting it.

## Cost, since it decides what is affordable

A region measurement was 1.004s and is now ~120ms, from three exact optimisations,
each verified against brute-force Möller-Trumbore rather than against a previous
run's numbers: a C-level sparse distance matrix instead of lists of Python lists
(4.0×), radius-tiered ball queries so one large triangle stops setting the search
radius for the whole mesh (1.6×), and a ray-line cull before the intersection test
that 4% of pairs survive (2.0× on the cast).

That is what makes an in-converter fit contract affordable at all — but the contract
still measures twice per shape, not once per pass, and for a reason that is about
correctness rather than cost: see `DESIGN.md`.

# 2026-09-09 — the gate judged one row backwards, and a stage it could not see

Two findings about the ACCEPTANCE GATE rather than a single metric: the gate
turns a metric into a verdict, so one that scores the wrong direction launders a
regression into a pass.

## Wrong: the bust-gap row rewarded drifting AWAY from the author

`bust gap vs author` is a SIGNED distance, `ours - author`. It was judged with
the ordinary `ge` rule — candidate must be `>=` control — which is right for a
row where bigger is better and exactly wrong for this one: the goal is
`|gap| -> 0`, so a candidate that drifted from +0.682 to +0.900 scored `ok`
while moving further from the thing it is measured against.

The rule is now its own comparator:

```
elif rule == "author":
    ok = abs(b) <= abs(a) + TOL
```

and the SIGNED pair is still printed beside the judged magnitude, because
`|gap|` alone cannot distinguish "closed toward the author" from "crossed past
them and penetrated" — the same two cases the rear/penetration rebuild above had
to separate.

**Every bust-gap verdict taken before this is void in the direction that
matters**: a row that read `ok` may have been a regression. The re-score of the
first flag put through the corrected gate flipped its bust-gap row from `ok` to
FAIL and took the overall verdict from 2 failing rows to 3.

The same row was independently blind to DEAD SLIDERS: a piece whose morph never
fires has no gap to measure, so it contributed nothing and could not fail.

> **2026-09-21 — the author side of this row was read off the wrong body.**
> `bust_gap_score` takes the author from `canonical_body.canonical_cbbe`, which
> until then picked the 3BA body mod's own preset `femalebody` — a build the
> game never loads — instead of BodySlide's zeroed build. On the shipped pack
> (677 shapes at weight 1 in both runs) the author's bust standoff p50 moves
> 1.104 → 1.416u and the gap p50 +0.624 → +0.297u (body-swap +0.738 → +0.471,
> copy +0.485 → +0.094); at weight 0 the author moves 1.310 → 1.255u and the gap
> the OTHER way, +0.325 → +0.392u. Our side moves only through which shapes
> qualify, at equal count (path split 352/325 → 354/323; bust p50 1.888 →
> 1.876u), and penetration not at all (3410 verts). The `|gap|` rule stands.
> Gap VALUES from before — the +0.682 and +0.900 above included — are not
> comparable with values after. A verdict is most at risk where a gap sits
> near zero, and the copy-path gap now does (+0.094u): under that rule (TOL
> 0.005) a uniform inward move of more than ~0.19u now reads FAIL on the
> copy-path row, where the old body allowed ~0.98u.

## The gate could not see the LAST stage

The working notes (kept on the `testing` branch under `docs/worklog/`, not
here) record that the pass chain OSCILLATES — a pass introduces a defect
and a later pass cleans it up — so a metric read off a stage dump
describes an intermediate mesh nobody ships. The gate had no measurement of
edge stretch on the WRITTEN file at all.

`scripts/analysis/stretched_edges.py` closes it. It judges the written NIF
against the AUTHOR's own mesh (resolved through `canonical_body.find_source`,
BSA fallback included), never a stage dump. Three rows are judged `le`:

```
stretch rate p50 (% of edges)        candidate <= control
stretch rate p90 (% of edges)        candidate <= control
edge deviation p50 (len-weighted)    candidate <= control
```

**The judged rows are per-shape RATES; the pooled count is INFO only.** A rate
is comparable between arms whatever the population does; a pooled count moves
when the number of scored shapes moves, so judging it would let a shape-count
change read as a quality change. `shapes / garments scored` and
`pooled stretched edges` print for the reader and score nothing.

Reference figures on the acceptance population: rate p50 0.1495%, p90 2.0217%,
edge deviation p50 0.0331, over 598 shapes = 299 garments, 0 sources
unresolved. A garment is TWO shapes — halve any shape count before comparing it
with a garment count.

---

# 2026-09-20 — the harness assumed a coordinate frame, and four tools inherited it

`seat_error_vs_author` was found double-transforming its converted arm. That is
a defect in one tool; the question this audit asked is whether it is a CLASS.
It is. Every tracked consumer of `_verts_skin_to_world` outside the converter
was read, and four of the seven were placing shapes by assumption.

## The population, and why the first answer was wrong

7246 shapes in 2605 NIFs of the shipped pack (both weights, no `_bsa_staging`,
no first-person), 0 unreadable.

The first cut counted NON-IDENTITY TRANSFORMS and found 534, 7.4% of the pack.
**That number is not the defect and must not be quoted as one.** A converted
shape may genuinely store skin-space verts, and then the transform is correct.
Splitting by which frame actually lands the shape on the body:

| | shapes | naive `_world()` is |
|---|---|---|
| genuinely stored in skin | 403 | correct |
| agrees either way | 47 | harmless |
| **already on the body** | **84** | **wrong — throws it off** |

Median throw for those 84 is 11.3u, max 2496.7u, across 56 files.

**No name filter substitutes for the measurement.** Of the 84, ZERO match the
proxy-name idiom and ZERO are `BaseShape`. They are `robe`, `Boots`,
`Gauntlets`, `ArmorF`. The class is NOT colliders, which is what the first
reading of the `seat_error` fix suggested. 78 of the 84 RENDER, so the
"renders nothing" filter three tools rely on catches 6 of them.

## Verdicts

| tool | verdict | the number |
|---|---|---|
| `bust_verdict.py` | **BROKEN → fixed** | 6 of 486 subjects; a shipped robe read covered 0.0%, standoff over 9 verts, and returned "clean at rest ... next step is an in-game A/B" at exit 0 with all four controls passing. Correctly framed: covered 34.8%, 459 verts, 0.43% clipping. |
| `survey_motion_clipping.py` | **BROKEN → fixed** | four silent exclusions; 55 colliders scored as garment, 463 of 5939 shapes dropped with no trace, `skipped` never printed, and `*_1.nif` only so half the pack was never surveyed. |
| `postflight_1_2.py` | **BROKEN → fixed** | 78 of the 84 survive its collider and not-rendered filters. NOT on the suspect list — found by following the call sites. |
| `qa_conform_audit.py` | **BROKEN (latent) → fixed** | 1 of the 84 changes class; 0 jiggle-strip suspects missed, 0 `matched_frac` corrupted, hug error median 0.053. Small because 60 of the 84 are RIGID on weight alone, decided before `hug` is read — not because the code was right. |
| `chain_flag_census.py` | **BROKEN (latent) → fixed** | same insufficient "renders" filter; placed garments before it placed the body. |
| `phase1_antipoke_population_ab.py` | **BROKEN (latent) → fixed** | same. |
| `snugness_census.py` | **SOUND** (frame) | already chooses by evidence, documents the exact trap, and asserts its reference stands upright. Nothing to do for the frame. Its AUTHOR body was a separate defect, fixed 2026-09-21: it read the 3BA body mod's own preset `femalebody`, and on BodySlide's zeroed build its body-swap reading flips from "LOOSER than authored" to "fit preserved" (median ratio 1.202 → 1.058; the copy path only crosses the tool's 1.15 line, 1.150 → 1.141). The right body also exposes 18 body stand-in shapes among those scored (authored standoff ~0: they sit on that body), 14 of them filling its loosest-15 — an exclusion gap, OPEN; excluding them moves the medians 0.001-0.002. |
| `collect_fit_dataset.py` | **SOUND** | records `ident` as a COLUMN instead of branching on it, so a consumer can filter. |
| `scripts/tool_audit.py` | **SOUND, scope-limited** | clean on all tracked tools — but it only audits SOURCE-PARSING tools for a population floor. Every defect above is in a NIF-READING tool, which it does not look at. A clean `tool_audit` is not evidence about this class. |

## What actually found these

Not the frame. Three of the four were found by asking **what does this tool
refuse to report on, and does it say so** — the exclusion, not the arithmetic.

- An exclusion that prints only a COUNT, or no count at all. `survey`'s
  `skipped` list was built and then discarded; its identity check excluded 7.8%
  of the pack with no line anywhere.
- **A control that cannot fail is not a control.** `bust_verdict` has four, and
  every one passed on a garment sitting 42u off the body: the calf control
  expects ~0% and got it, the body-vs-itself control never touches the garment,
  and the equality selftest compared two identical zeros. The file's own note
  above the ORIENTATION control says a control that can be skipped is not a
  control — it had been applied to the body and never to the garment.
- **Score the COMPOSED rule, never its components.** `Cylinder.015` reads like
  a collider and carries a full texture set over 33k verts; `ButtCol` reads like
  a garment suffix and has none. Token alone and rendering alone each pick the
  wrong one.

## The guard, and the one that generalises

`standoff_audit.pick_frame` places a shape by proximity, so the decision exists
once instead of five times. But the frame fix only removes a CAUSE. The guard
that removes the class is `bust_verdict`'s COVERAGE control: a garment covering
less than 1% of the band it is judged on has not been measured, and 0.00%
clipping is then indistinguishable from a perfect fit — whatever put it there.

Mutation pairs TFF-a..e, all CAUGHT. The chooser is armed in BOTH directions on
purpose: a pair arming only one would pass for a chooser hardcoded to the other.

**No in-game verdict is owed.** Every change here is to a measurement tool;
none can move a vertex.

## Still open — closed 2026-09-20, bar one

`morph_clip_test._aligned`, `snugness_census.pick_frame` and the fix on PR #9
were three more copies of this decision, held back until #9 landed. It did, and
PR #12 folded two of them: `seat_error_vs_author` and `snugness_census` now
delegate to `pick_frame`, their output byte-identical on the shipped pack
before and after (seat error over 4053 paired shapes, snugness over 171).
`morph_clip_test._aligned` stays out on purpose: it has no agree band (under a
0.25u disagreement it takes the nearer frame where `pick_frame` returns raw),
and it is verified-safe for the acceptance gate, so folding it in means
re-running the GATE, not just the suite. Its docstring says why.

## The second class the same day — half the pack, undeclared

Same audit, different heuristic: **an exclusion you cannot explain.** The frame
class was about placing a shape wrongly. This one is about never looking at it.

`standoff_audit.output_nifs` was written because validation scripts glob
`*_1.nif` only, and weight 0 is a SEPARATELY AUTHORED mesh, not a scaled copy.
Its own docstring records the measurement and the count at the time:

    bust-front clipping, one cuirass:  weight 1  4.52%    weight 0  9.48%
    "Fifteen validation scripts in this repo independently glob *_1.nif only"

Counted 2026-09-20: **23 tools enumerate the pack that way, and 21 never say
so.** On the shipped pack that is **1032 of 2064 NIFs — exactly 50.0%**, and by
the measurement above the missing half is the WORSE half. The number went from
fifteen to twenty-three with nobody deciding it should.

**The fix is NOT "always read both weights", and that distinction carries the
design.** `morph_sweep` scopes to `_1` for a real reason — `_0` and `_1` are one
garment at two weights, so scoring both double-counts a per-garment RATE — and
it says so in its docstring. That is all that is asked: declare the population.
Using `output_nifs` counts as declaring it.

So `tool_audit.py` grew a fourth section and `tests/test_pack_population_declared.py`
freezes the 21 by name. It is a RATCHET, not a cleanup: it fails when a new tool
joins the list, or when a listed one stops qualifying and the list starts lying
about the tree. Rewriting 21 populations blind would change what every one of
them reports with nothing to validate the new numbers against — and several feed
leads that are currently open.

Mutation pairs HPK-a and HPK-b, both CAUGHT. HPK-a mutates the DETECTOR rather
than the list, because a detector that matched nothing would leave every
assertion in that file passing forever — which is precisely the failure this
audit exists to find.

### What did NOT survive triage, recorded so it is not re-run

**"Reports a MEAN with no MEDIAN"** sounds like the heuristic that found
`seat_error` (9.18 vs 0.35). Swept over every tool it yields 13 hits and
roughly 3 are real: most are `(d < PROX).mean()`, which is a PROPORTION, where
the mean is exactly right and a median would be 0 or 1. Of the four genuine
distribution means, `golden_output` and `verify_chain_shift` both print `max`
beside the mean, so the tail is already visible. **Do not mechanize this one** —
at ~30% precision it is a reading aid, not a gate.

The one candidate worth measuring was `find_morph_follow_gaps.py`, which
THRESHOLDS on a mean (`follow < 0.15` over covered verts) and reports no spread.
A garment whose covered verts sat half at 0.30 and half at 0.00 would pass that
test with half the piece carrying no follow at all.

**Measured, and REFUTED.** Over 500 pieces, mirroring the tool's own scoring:
288 zone rows scored, 186 flagged, 102 passed as clean — and of those 102,
**zero** carry no-follow on more than 40% of their covered verts. The
distribution is not bimodal in this population, the mean is hiding nothing, and
the threshold stands. Recorded because the hunch is plausible and cheap to
re-form: it has been checked. Do not re-open it without a population where that
number is not zero.

# 2026-09-20 — six tools could not report failure, and four of them write to disk

The frame audit closed on a question it could not answer about itself: `bust_verdict`
returned "clean at rest ... next step is an in-game A/B" at **exit 0** on a robe 42u
from the body. Nothing consumed that exit code — a person read the sentence. So the
follow-up question is not "which tools are wrong" but **which tools are incapable of
being wrong**.

## The census

An AST pass over the 101 script paths named in `docs/TOOL_MAP.md`, collecting every
`sys.exit` / `exit` / `raise SystemExit` argument and flagging files where every
argument is a constant `0`/`None`:

| | count |
|---|---|
| library modules (no `__main__`) | 15 |
| runnable tools that CAN exit nonzero | 80 |
| **runnable tools that CANNOT** | **6** |

The predicate misses `argparse`'s `.error()`, which exits 2. Re-checked by hand:
one of the six (`build_body_collider_proxy`) has one, and it is a *usage* guard.
None of the six could report a **semantic** failure. An uncaught exception still
exits nonzero, so these tools failed loudly when they crashed and silently when
they did nothing — which is the wrong way round.

## Measured before the fix, each pointed at an empty pack

```
scan_output_health          rc=0  "=== SCAN DONE ==="
disable_unconstrained_smp   rc=0  "(dry-run; pass --apply to rename)"
build_body_collider_proxy   rc=0  "processed 0 NIFs"
strip_nude_handfeet         rc=0  "need esp path"
scan_nude_skin_chain        rc=0  "FATAL: no UBE_AllRace.esp found"
```

The last one prints the word FATAL and returns success. The first prints an
affirmative all-clear over zero NIFs.

## Verdicts

| tool | verdict | the number |
|---|---|---|
| `scan_output_health` | **BROKEN** | `=== SCAN DONE ===` over 0 NIFs, rc=0; a wrong `out_dir` = a healthy pack |
| `scan_nude_skin_chain` | **BROKEN** | two `FATAL` paths, both `return` → rc=0 |
| `disable_unconstrained_smp` | **BROKEN** | rglob on a missing dir yields nothing, raises nothing → "0 to disable"; all renames failing → rc=0 |
| `strip_nude_handfeet` | **BROKEN** | no ARMA group → every loop empty → `--apply` re-serialises a deployed ESP and prints "saved" |
| `build_body_collider_proxy` | **BROKEN** | "processed N" counted NIFs VISITED; NIF and XML halves could diverge unreported |
| `augment_nude_tri` | **BROKEN** | `seam_n` — the control the whole method rests on — printed and never acted on |

`TOOL_MAP.md`'s `gate` column now reads a real exit code for all six, where it
read `—` for every one of them before.

## What the fix is, and what it is not

None of the six is invoked by another script or by CI — the consumer is a **person
reading a terminal**. So an exit code alone would not have helped; the no-data case
also has to *look* different from the clean case. Both halves were applied: the
existing shared `require_population` (exit 3, "0/0 is not a pass") where there is a
countable population, and verdict lines that carry the population instead of a bare
`DONE`.

**Not done, deliberately:** `scan_nude_skin_chain`'s coverage matrix is not graded
into an exit code. `--MISSING` cells are legitimate mid-build, and turning them red
would change what an existing run reports with no measurement behind it.

**Named, not fixed:** `augment_nude_tri.nif_verts` reads `shape.verts` RAW. Body and
part are separate NIFs with their own transforms, so a non-identity global-to-skin on
either drives `seam_n` to 0 — the `standoff_audit.pick_frame` class again, reached
from the other side (a tool that *should* normalise and never did). Measuring it needs
a nude build; the new guard at least makes the symptom impossible to miss.

## A landmine found on the way

Six tools carry `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`. The new
wrapper **owns** that buffer and closes it when garbage-collected, under whoever else
holds it. Importing one inside pytest closes the global capture file and every later
test dies on "I/O operation on closed file" — which is how the first draft of these
tests came to assert on an empty string while only the exit code was really checked.
Both the no-seam path and the nothing-augmented path exit 3, so without the message
the test could not tell which guard had fired.

All six converted to `sys.stdout.reconfigure()`, which mutates the existing stream
instead and is a no-op on one that does not support it; non-ascii output verified
unchanged. The other three — `fit_audit`, `scan_morph_issues`,
`sanity_check_converted` — had no test importing them *yet*; the first one to do so
would have hit it.

Ratcheted, with the **mechanism pinned rather than assumed**: a test wraps a
`BytesIO`, drops the wrapper, collects, and asserts the buffer is closed. If CPython
ever stops doing that the test fails and says the ratchet's reason is gone, instead
of a banned string outliving the justification nobody can any longer explain.
`mutation_pairs.py` is exempt — CAC-k arms this ratchet by carrying the banned idiom
as a replacement string — and the exemption asserts the file really is the pair
catalogue before skipping it. That collision was caught by the ratchet itself, and
the gate correctly refused to judge anything while the baseline was red.

## The control

Every guard was checked in the direction that matters *and* the direction that does
not: a directory holding a properly constrained XML still exits 0, a plugin that does
have a nude-skin ARMA still strips and saves, and a seam-coincident part still
transfers and writes. A guard that turns healthy runs red is worse than the bug.

11 mutation pairs, CAC-a..k, all CAUGHT.

## Found while converting the three: `fit_audit.py` has never run

Smoke-testing the three tools converted off the stdout wrapper turned up a
different defect in one of them, and it is the opposite of this lane's class:

```
fit_audit.py  rc=1  AttributeError: module 'src.sliderset_gen'
                    has no attribute 'MORPH_SHAPE_CAP'
```

Line 306 at the time (310 since the stdout fix), before any work. Reproduced
on unmodified `testing`, so it is not
this lane's doing. `MORPH_SHAPE_CAP` appears **nowhere else in the repo** —
not in `sliderset_gen`, not anywhere — and `git log -S` finds nothing because
the constant predates the clean-slate re-root. **The tool has not run since.**

Verdict: **BROKEN — dead reference.** Deliberately NOT patched. `cap` feeds
`morphing = set(tri_order[:cap])`, which is what makes `CUT_NO_FALLBACK` and
`CUT_HIGH_MORPH` fire at all. The nearest real constant, `tri.TRI_MAX_SHAPES
= 0xFFFF`, is the PIRT header's encoding ceiling, not a per-NIF morph cap;
substituting it would let every shape morph, so both checks would never fire
again — a tool that runs, reports, and cannot find anything. That is strictly
worse than one that crashes, and it is the exact failure this audit exists to
remove.

The open question is not "what number goes there" but **whether the per-NIF
morph cap still exists as a concept in this codebase**. `sliderset_gen:413`
still orders shapes so that "if the per-NIF morph cap ever fires, low-impact
rigid props are dropped first", so something believes in it. Resolve that
before assigning a value.

# 2026-09-20 — conform CANNOT manufacture the too-close population it is blamed for

Read-only follow-up to the open caveat on the crotch-band lead, which read:
*"reading `conform_to_source_standoff` at defaults it should be a no-op on
already-too-close verts, so either the stage covers more or the two measure
offset differently; resolve before editing that function."*

**Resolved: the two measure offset differently. The stage does not cover more.**

`authored_offset_ledger` counts "too CLOSE" as `err < 0`, where `err = ours −
authored`. It attributes the growth 24.1% → 64.8% to the `conform` stage. But at
defaults the arithmetic bounds the result:

```
tight   = max(min(s_src, s_cur), min_clear_v)        # min_clearance = 0.25
blend_v = blend_tight + _b*(1 - blend_tight)         # blend_tight = 0.3, so blend_v <= 1
target  = s_cur + (tight - s_cur) * blend_v
move    = min(target - s_cur, 0.0)                   # pull IN only
```

When `move < 0` we need `tight < s_cur`, which forces `min(s_src, s_cur) = s_src`,
hence `tight >= s_src`; and `blend_v <= 1` gives `target >= tight`. So
**`target >= s_src` always** — conform can never land a vertex closer to the body
than the author's own standoff, as conform measures it.

Brute-forced over 2,000,000 random `(s_src, s_cur)` pairs on `[-2, 6]u`, replicating
`nif_convert_fitgeom.py:1469-1537` exactly:

| | |
|---|---|
| verts conform MOVED | 920,618 of 2,000,000 (46.0%) |
| of those, ending closer than the author (`err_after < 0`) | **0** |
| worst `err_after` among moved verts | **+0.000000** (bound is tight, never crossed) |
| pushed from not-too-close INTO too-close | **0** |
| already too close, and moved | **0** — max `abs(move)` = 0.000000 |

The bust anti-poke block further down the same function pushes **out**, so it cannot
create `err < 0` either, and `conform_margin` only raises `tight`.

**So do NOT edit `conform_to_source_standoff` on the strength of the ledger's
attribution.** The number to chase is the disagreement between the two
measurements, not the function. Candidates, in the order they are cheap to check:

1. conform derives `s_src` from its **own** nearest-neighbour correspondence
   (`cKDTree(src_body_verts).query(src_cloth)`); the ledger pairs independently, so
   the same vertex can carry two different "authored" values.
2. the ledger measures the **shipped** garment — after anti-poke, panel rigidity and
   coherence repair — not conform's immediate output.
3. the stage dump labelled `conform` may bracket neighbouring passes, so the label
   attributes more than the function.

This does not touch the finding itself: we still sit −0.432u deeper at p05 over
n=97. It removes one candidate mechanism, and it removes the temptation to "fix" a
function whose arithmetic already forbids the defect.

> **SUPERSEDED 2026-09-21 — the −0.432u was read on the wrong reference body.**
> Re-measured with the source arm on the zeroed body the game loads: p05
> **−0.917u** over n=98 (89 deeper / 1 shallower by more than 0.25u), bulk p50 **−0.159u**
> (closer, not looser). The bound above is arithmetic, not a measurement, and
> stands. See "the CBBE reference body was the wrong body" (2026-09-21) below.

# 2026-09-20 — eighteen of the 21 half-pack tools resolved, and one of them was right all along

The ratchet (`tests/test_pack_population_declared.py`) froze 21 tools that
enumerate the pack with a `*_1.nif` glob and never say so. Freezing stops the
count growing; it fixes nothing. This is the first tranche actually resolved,
in small groups with the tool's output captured on the shipped pack BEFORE and
AFTER each change.

**The rule is DECLARE, not "always both weights".** Ten of the eighteen here
read both weights now. One was already correct and only needed to say so; SEVEN
are declared blind spots that a glob swap would have corrupted, because they
measure every file against a body pinned to weight 1. Finding those is the
result, not a failure to convert them.

## The population, and why the ratio is not 2.0

    raw rglob("*_1.nif") on the shipped pack        1295
      of those, first-person (1stperson OR 1stp)     263
    output_nifs(root) default, BOTH weights         2064   (1032 + 1032)
    output_nifs(root, weights="1")                  1032

So a tool that already filtered first-person moves **1039 -> 2064 = x1.987**
(+1032 weight-0, MINUS 7 short-prefix `1stp*` meshes), and a tool that did not
filter at all moves **1295 -> 2064 = x1.594**. Every count below is one of
those two cases; where a headline moved differently, the reason is stated.

**A second, uncounted bug fell out of this.** Four of the five hand-rolled the
first-person test as `"1stperson" not in f.lower()`; the fifth,
`sanity_check_converted`, had no such filter AT ALL. `output_nifs` uses the
regex `1stperson|1stp`, and the short prefix is real: it leaked 7 weight-1
meshes into the four that filtered, and first-person meshes accounted for **94
of the 336 rows in `sanity_check_converted`'s body-slot population**. Routing
the enumeration through the shared helper fixes that for free and puts the rule
in one place instead of five.

## Verdicts

| tool | verdict | headline before -> after |
|---|---|---|
| `verify_weight_invariant` | BOTH WEIGHTS | bad-sum verts **868 -> 2469** |
| `verify_bodymatch` | BOTH WEIGHTS | re-sourced 18 -> 36; cut-in **0 -> 1** |
| `verify_layered_cloth` | BOTH WEIGHTS | layered 12 -> 24; grafted 0 -> 0 |
| `verify_motion_match` | `_1` ONLY, CORRECT | findings **byte-identical** |
| `sanity_check_converted` | BOTH WEIGHTS | body-slot NIFs 336 -> 484 |
| `find_morph_follow_gaps` | BOTH WEIGHTS | zone-flags 386 -> 772, **different sets** |
| `find_overinflation` | BOTH WEIGHTS | flagged 9 -> 15; weight 0 NOT the worse half |
| `verify_bust_clearance` | BOTH WEIGHTS | **poking 54 -> 116**; measured 221 -> 441 |
| `bust_gap_score` | **`_1` ONLY, DECLARED BLIND SPOT** | AST byte-identical (docstring only) |
| `registered_bone_audit` | BOTH WEIGHTS, first-person kept | checked 144 -> 288; violations 1 -> 2 (one garment) |
| `postreconvert_audit` | BOTH WEIGHTS, first-person kept | skirt_proxies 6 -> 12; encloses 0 -> 0 |
| `band_class_census` | **`_1` ONLY, DECLARED BLIND SPOT** | AST byte-identical; 3 pins, 5 gate rows |
| `nipple_clearance` | **`_1` ONLY, DECLARED BLIND SPOT** | AST byte-identical; gate |
| `collect_fit_dataset` | **`_1` ONLY, DECLARED BLIND SPOT** | AST byte-identical |
| `source_delta_census` | **`_1` ONLY, DECLARED BLIND SPOT** | AST byte-identical; both sides pinned |
| `single_swing_census` | **`_1` ONLY, DECLARED BLIND SPOT** (live lead) | AST byte-identical; lead unmoved |
| `snugness_census` | **`_1` ONLY, DECLARED BLIND SPOT** (live lead) | AST byte-identical; lead unmoved |
| `collect_penetration_census` | BOTH WEIGHTS | rows 236 -> 472; **w0 worse in all 6 regions** |

> **UPDATED 2026-09-21:** four of the seven declared blind spots now measure
> weight 0 on its own bodies -- `bust_gap_score`, `nipple_clearance`,
> `collect_fit_dataset`, `source_delta_census` (see "weight-aware reference
> bodies" below). `band_class_census`, `single_swing_census` and
> `snugness_census` are still `_1` only.

## `verify_weight_invariant` — it was half a gate

    meshes         1039 -> 2064   (x1.99)
    shapes         3134 -> 6242   (x1.99)
    bad-sum verts   868 -> 2469   (x2.84)   <- the finding
    worst dev     +0.171 -> +0.173
    exit code         1 -> 1

The file count doubled and the defect count nearly TRIPLED. Weight 0 carries
about **1601 of the 2469** offending verts against weight 1's 868 — roughly
1.8x the damage on the half that was never read. Spot-checked per piece first,
using the repo's own `check_weight_invariant`, and it held there too: on the
two worst pairs, 984 bad verts at weight 0 vs 420 at weight 1, and 593 vs 426.

This tool EXITS 1. It was gating the build over half its own defect population.

## `verify_bodymatch` — a real defect was hiding at weight 0

    scanned              1039 -> 2064   (x1.99)
    re-sourced             18 ->   36   (exactly x2)
    cut in over an area     0 ->    1

The re-sourced set doubling exactly is itself a result: the body-match rule
re-sources both halves of every pair, as it should. The new flag is a weight-0
draugr cuirass cutting in over **12 vertices**; its `_1` partner reads 6, under
the `>=8` area threshold. The piece sat just below the flag line on the half
that was measured and above it on the half that was not, and the tool printed
"the rest sit clean" over it.

## `verify_layered_cloth` — a clean negative worth having

    scanned        1039 -> 2064
    layered meshes   12 ->   24   (exactly x2)
    grafted SMP       0 ->    0

The failure this hunts is a stale exe or an incremental skip, and both are
per-FILE: a `_0` half could have kept the graft that CTDs FSMP while its `_1`
was rebuilt. It did not. That is now measured instead of assumed.

## `verify_motion_match` — `_1` only is CORRECT, and now says so

The rubric's per-file heuristic points at "both weights" and is WRONG here.
What the tool measures is not the `_1` mesh, it is the per-garment BODYTRI:

* `#tri-write-once` — the tri stem drops the weight suffix, so `x_0.nif` and
  `x_1.nif` derive the SAME `x.tri`.
* Confirmed on the shipped pack: **1889 `.tri` files, ZERO carrying a `_0`/`_1`
  suffix.** One table per garment pair.
* `_tri_is_owning_variant` records that **`_0` owns it**, measured.
* The tool builds the tri path by stripping `_1.nif`, so the glob is a PAIRING
  KEY onto that one shared file.

Enumerating `_0` too would re-open the identical table and print every row
twice. Worse, the hugging mask is taken against a body pinned to weight 100, so
feeding `_0` meshes through it would measure a low-weight garment against a
high-weight body and manufacture false hits.

Routed through `output_nifs(root, weights="1")` anyway, to own the first-person
rule in one place:

    scanned  1039 -> 1032        (-7 short-prefix `1stp*`)
    hugging shapes off ratio       12 -> 12
    rigid props at shear risk       0 ->  0

**The whole before/after diff is ONE LINE, the scan count.** Every finding is
byte-identical — the proof that the 7 dropped meshes were arms-only and
contributed nothing.

DECLARED BLIND SPOT, not fixed: the hug mask is weight-dependent, so a shape
that hugs only on the LOW-weight silhouette is never scored. Closing that needs
a second body load, not a glob change.

## `sanity_check_converted` — reconciles to zero residual

    body-slot NIFs (--max 100000)   336 -> 484
    all pass                        336 -> 484
    exit code                         0 ->   0

336 -> 484 is x1.44, neither of the two expected ratios, and it reconciles
exactly:

    BEFORE  336 = 242 non-first-person + 94 FIRST-PERSON
    AFTER   484 = 242 weight-1         + 242 weight-0, first-person 0

The weight-1 set is **identical across the change: 0 rows added, 0 removed.**
The delta is exactly -94 first-person +242 weight-0. This tool had NO
first-person filter at all, so **28% of what it sanity-checked as body-slot
armor was first-person arm meshes**, while the entire weight-0 half went
unchecked. It is the gate you run INSTEAD of loading a savegame.

## The two triage tools — and a result that points the other way

Both measure per-shape against the body injected into the SAME NIF, so a `_0`
file brings its own weight-0 body and its own separately authored garment.
Neither loads a weight-pinned reference body and neither parses a preset, so
the population swap alone is sound. Both keep the `/m/` male-path exclusion.

    find_morph_follow_gaps   scanned 1038 -> 2062   zone-flags 386 -> 772
    find_overinflation       scanned 1038 -> 2062   flagged      9 ->  15

`find_morph_follow_gaps`: the total doubled, **but the sets are not the same.**
In the printed worst-ranked rows, 10 zone-flags exist ONLY at weight 0 and 2
ONLY at weight 1. Equal counts, different content — weight 0 was not a copy of
what was already reported, it was 386 unexamined observations that happen to
number the same. The tool's own first line names the morph it is about as
`_0<->_1`, the weight slider, so reading one weight was measuring the
follow-through of one silhouette and calling it the pack's.

`find_overinflation`: **weight 0 is NOT the worse half here, and that is the
result.**

    weight-1 flag set   IDENTICAL before and after (all 9 kept, none added)
    flagged at BOTH      6
    flagged at w1 only   3
    flagged at w0 only   0

Every weight-0 flag belongs to a piece already flagged at weight 1, and three
pieces stand off at weight 1 while sitting clean at weight 0. The class thesis
— "the missing half is the WORSE half" — was measured on bust-front CLIPPING.
It does not transfer to STANDOFF. The gain here is coverage, and an unchanged
weight-1 set proving no regression; it is not a bigger number.

Why 2062 and not 2064: `output_nifs` returns 2064 and the `/m/` filter removes
2 male-path meshes. Before was 1038 for the same reason. 1038 -> 2062 is
x1.986, i.e. +1031 weight-0 MINUS the 7 leaked `1stp*` meshes — and those 7
contributed no flags, which is why both weight-1 halves are unchanged.

## `verify_bust_clearance` — the tool this rule was written about

`output_nifs`' own docstring cites bust-front clipping at **4.52% on weight 1
against 9.48% on weight 0**, and this tool's check 1, "body poking through at
the breast", IS that measurement. The check most known to be worse at weight 0
was the one still never run there.

Safe to swap the population alone: the body is the BaseShape injected into THAT
SAME NIF — the file says so itself at `_SKIP` ("The armor's own injected body is
the reference") — so a `_0` file measures its own separately authored garment
against its own weight-0 body. No preset, no weight-pinned reference body.

    scanned                     1039 -> 2064   (x1.987)
    body-armor meshes measured   221 ->  441
    mean breast clearance      +1.18u -> +1.13u
    mean back clearance        +1.14u -> +1.12u   (over 220 -> 439)
    1) POKING THROUGH AT BREAST   54 ->  116 armors
    2) REAR CLEARANCE LEAKED      44 ->   89 armors

**Poking went 54 -> 116 — MORE than double — and it splits exactly: weight 1
still contributes 54, weight 0 contributes 62.** Weight 0 carries **1.15x** the
bust-front poke-through of weight 1. That is the class thesis confirmed on the
metric it was originally measured on. Rear leak splits 43 + 46 = 89.

EVERY COUNT RECONCILES TO ZERO RESIDUAL. 441 is not 2x221 because of the 7
short-prefix `1stp*` meshes the hand-rolled filter leaked: run each through the
tool's own `_measure`, and **exactly one** returns a result. It reads breast
frac 0.0053 (under the 0.01 poking threshold) and back +2.45u (over the 1.5u
leak threshold), so it sat in the rear-leak list and NOT the poking list —
which is exactly how the two headlines move:

    weight 1 measured   221 -> 220   (-1, the dropped first-person cuirass)
    weight 0 measured           221
                                ---
                                441

Weight 0 ends with ONE MORE measurable mesh than weight 1: one garment carries
a qualifying BaseShape and panel in its `_0` file but not its `_1`.

### METHOD NOTE — rc=0 is not evidence that a tool reported anything

The first BEFORE capture of this tool returned **rc=0 with only the header line
and no traceback.** It prints its results in one block at the end, and that
block was lost; a stderr RuntimeWarning was the only other output. Taken at face
value it would have produced a confidently wrong before/after. Re-run in the
foreground with `PYTHONUNBUFFERED=1` it produces all 58 lines. **Check that the
output is COMPLETE, not just that the exit code is 0** — this is the same
family as the closed cannot-abort class, arriving from the opposite direction:
there the tool said "clean" over nothing; here it said nothing at all, cleanly.

## Three more — and the one that must NOT be glob-swapped

**`bust_gap_score`: `_1` ONLY, DECLARED AS A BLIND SPOT — not a correct scoping.**
It counts penetrating vertices per shape, so a `_0` mesh can be independently
defective, and a change landing on `_0` reads as NOT FIRED — the tool's own
defect 1 ("SCORE THE POPULATION THE CHANGE CAN REACH") on the weight axis. But
it fails the safe-to-swap test twice, both verified in code:

* the AUTHOR body — `canonical_body.canonical_cbbe` globs `femalebody_1.nif` only
* the COPY-PATH body — `_body_for(nf, "_1")` in `measure_arm`

Either would put every `_0` row in the wrong frame as a believable wrong number.
It is also a GATE instrument — `acceptance.py` reads two of its rows — so
widening re-baselines both with nothing to validate against. Declared, with the
fix path written into the docstring.

Neutrality PROVED for a docstring-only change: the AST with the module docstring
removed is byte-identical before and after (`3728ab2d06458b9e...`, 43862). A
control copy with one token changed hashes differently (`b890d2480fb0cf34...`),
so the check does detect code changes.

**`registered_bone_audit`: BOTH WEIGHTS, first-person KEPT.**

    enumerated        1295 -> 2590   (exactly x2)
    pieces checked     144 ->  288   (exactly x2)
    violating shapes     1 ->    2

The 2 is **one garment at both weights**: a robe whose generated `VirtualGround`
shape carries an undeclared `NPC Root` bone in its `_0` and its `_1`. Weight 0
surfaced the partner of a known defect, not a new one — said plainly because
the SHAPES table prints "2 VirtualGround", which reads as two defects.

First-person is kept on purpose: `output_nifs` excludes it for a BUST-COVERAGE
reason, which has nothing to do with a physics bone.

OPEN, independent of this change: this gate **already exits 1 on the shipped
pack at weight 1 alone.** Its docstring's last measurement (10 over 174, then 0
after the fix) predates the current pack.

**`postreconvert_audit`: BOTH WEIGHTS for the proxy rows, first-person kept.**
The entire before/after diff is one line — `skirt_proxies 6 -> 12`. Every other
row is byte-identical, including `proxy_encloses_chain` 0 -> 0: the zero-target
row that can exit 1 now covers both weights and stays clean. No baseline file
existed yet, so nothing could falsely "regress".

### SAFETY — a measuring tool that writes into what it measures

`registered_bone_audit` writes its JSON **beside the pack, unconditionally** —
for the shipped pack, inside the read-only modlist instance, and BEFORE its
verdict prints. Every run here went through a wrapper that keeps all paths
intact (physics-XML resolution depends on the NIF's real location, so a junction
was ruled out), redirects that one write to the scratchpad, and refuses any
other write into the instance. The wrapper was self-tested on a fake path first,
and the instance was checked afterwards: the file was never created there.
Flagged as its own task — where the report *should* go is a design decision.

## Four more declared blind spots — the swap-safety test decides it

A keyword scan flagged weight-1 pin signals in ALL SEVEN remaining expensive
tools. That was not evidence: a pin only matters if a `_0` file is actually
measured against it. Each tool got an independent trace of every reference it
measures a garment against, plus an adversarial pass trying to refute the
verdict. All verdicts survived, and three of the seven turned out SAFE — the
keyword hits were per-weight calls taking a variable, not literal pins.

**The rule that falls out:** a population swap is sound only when every
reference is either taken from the NIF itself (its own injected `BaseShape`) or
resolved from the file's own weight suffix. `canonical_body` is the recurring
trap — `canonical_ube()` and `canonical_cbbe()` take no weight, cache ONE body
each, and resolve to `femalebody_tangent_1.nif` / `femalebody_1.nif`.

| tool | pin a `_0` file would hit | consumer |
|---|---|---|
| `band_class_census` | copy-path template `_find_ube_femalebody("_1")`; preset `big` side only; `--every` stride | 5 acceptance rows |
| `nipple_clearance` | tip rays from `canonical_ube()`, in-file body discarded | 3 acceptance rows |
| `collect_fit_dataset` | `body_context()` -> `_find_ube_femalebody("_1")`, built once | none |
| `source_delta_census` | BOTH sides: `canonical_ube()` and `canonical_cbbe()`, plus pose inputs | none |

**Measured, not asserted — the stride trap.** On the shipped pack all 1032
garments have both weights, and after a naive widening:

    --every 2   1032 picked, 100.0% `_0`
    --every 3    688 picked,  50.0% `_0`
    --every 4    516 picked, 100.0% `_0`

Every change here is docstring-only and proved neutral by AST equality with the
module docstring removed (band_class `889ca57f`, nipple `cfbef515`, fit_dataset
`1ba22813`, source_delta `2b298377`).

**Ratchet hole checked and found empty.** The detector clears any tool whose
text merely contains `output_nifs`. Audited every tool still globbing `*_1.nif`:
none is cleared by a bare mention — each is on the list, calls `output_nifs`,
or declares in its docstring. The ratchet's own test treats an IMPORT as
declaring, so the looseness is by design.

## The last four — two live-lead declarations, one conversion, one rule confirmed

**The two live-lead censuses are declared WITHOUT moving the lead.**
`single_swing_census` and `snugness_census` feed the open crotch-band
investigation, so their population was not touched. Both are blind spots, not
correct scopings — per-file and per-shape units respectively, neither a rate the
two weights share — and both are measured against weight-1 bodies:

* `single_swing_census` — converted side uses the in-file body (fine), but the
  SOURCE side is `canonical_body.canonical_cbbe()` (`femalebody_1.nif`, one key);
* `snugness_census` — `nc._find_cbbe_base_body("_1")` and
  `nc._find_ube_femalebody("_1")` at the call sites. Both helpers already take a
  weight and cache per weight (`cbbe{weight}`, `ube{weight}`), so its fix is
  two call sites; the `canonical_body` tools need that module changed first.

Both changes are docstring-only and AST byte-identical, so **nothing moved**: the
lead stands at -0.432u deeper than the author at p05 over n=97 (70 deeper / 2
shallower), bulk p50 +0.206u further off, single-swing class at 1 clean residual
of 224. TOOL_MAP byte-identical too.

> **SUPERSEDED 2026-09-21.** Both censuses now read the author on BodySlide's
> zeroed body (`snugness_census` through `canonical_body`, still `_1` only).
> On it the lead reads p05 **-0.917u** over n=98 (89 deeper / 1 shallower by more than 0.25u) and
> bulk p50 **-0.159u**: an INWARD offset across the band, not a looser bulk over
> a deeper tail. The recorded bulk +0.206u does not reproduce by the
> recomputation's method even on the old body (+0.169u). The single-swing
> residual (1 of 224) is a converted-side count and is unchanged.

**`collect_penetration_census`: BOTH WEIGHTS.** Body from the same NIF, no
external reference, verified first-hand. First-person left to its OWN stem rule
(broader than `output_nifs`' regex), so the change is the weight axis only.

    scanned 1295 -> 2590   rows 236 -> 472   skipped 1059 -> 2118   (all exactly x2)

All 236 weight-1 rows are BYTE-IDENTICAL before and after. Paired over 236
garments, pieces with >5% of a region exposed-and-near:

    region        w1        w0        median dist_p50 w1 / w0
    breast        55/233    59/233    1.258 / 1.240
    upper_chest  137/234   144/234    1.322 / 1.276
    belly         44/235    46/235    1.208 / 1.197
    butt          17/235    19/235    2.949 / 2.684
    lower_back    56/235    58/235    1.431 / 1.418
    thigh         25/234    26/234    1.620 / 1.612

**Weight 0 is worse in all six regions** (334 -> 352 region-flags, +5.4%) and
closer to the body at the median in all six. Modest, and the regions share
garments so it is not six independent votes — but the direction is uniform, and
it sides with `verify_bust_clearance` (clipping) rather than `find_overinflation`
(standoff).

## What was NOT done, and why — the three left on the list

* `fit_audit.py` — **BLOCKED.** Dead: AttributeError at line 310
  (`sliderset_gen.MORPH_SHAPE_CAP` exists nowhere). No before/after is possible.
* `multipose_census.py` — **convertible, deferred.** References are safe (body
  from the NIF itself). Two costs kept it out: ~1.2-1.5 h before and ~2.5-3 h
  after, and a DOWNSTREAM consumer — `underbust_census` reads its default
  `multipose_census.jsonl` and uses an under-curve band calibrated on the
  weight-1 body, so widening this silently widens that consumer too. Needs its
  own change that checks the band on `_0`. Keep its own first-person stem rule
  (broader than `output_nifs`') on top of any swap.
* `phase1_antipoke_population_ab.py` — **references safe (the body resolves per
  weight), deferred.** It is an A/B of an OFF build against an ON build; the
  shipped pack is only the ON arm, so a real before/after needs a full reconvert
  with `CBBE2UBE_NO_PHASE1_ANTIPOKE=1`. And its aggregate tallies count PIECES, so
  a naive widening makes each garment vote twice — the tallies need splitting by
  weight first.
Neither deferred tool is declared: both are safe to widen, so calling them blind
spots would misstate them. The ratchet keeps them visible, which is its job.

## OPEN, found while converting: `output_nifs`' first-person filter misses a SUFFIX form

Its regex is `1stperson|1stp`, which is anchored on the marker appearing as a
PREFIX. On the shipped pack **34 meshes (17 per weight) carry `1st` as a name
SUFFIX** (`...f1st_1.nif`, `..._1st_1.nif`) and all 34 pass straight through.

**It is NOT a one-line regex fix, and the tempting fix is wrong.** Measured the
z-span of all 17 weight-1 candidates (UBE feet ~z11, head ~z114):

    FULL-BODY  span ~103u, 29k-34k verts     8 of 17
    arms-only  span 35-47u, 533-4290 verts   9 of 17

So 9 really are first-person meshes leaking into fit censuses — exactly the
trap `output_nifs`' own docstring describes ("a first-person mesh, arms only,
no torso, flagged at 8.47u standoff — a meaningless number"). But the other 8
are genuine full-body armors that merely have `1st` in the name, and excluding
them by name would silently drop real meshes from every tool that now shares
this helper.

**The discriminator has to be GEOMETRIC (torso span / body coverage), not
nominal.** Left OPEN deliberately: changing `output_nifs` now would move every
number in this entry, and it needs its own before/after and its own mutation
pair.

Checked and REFUTED along the way: the two `_1st` rows among
`find_overinflation`'s weight-1 flags are NOT false positives — both are
full-body meshes (span 103.3u, ~30k verts), so the flags stand.

## Correction to the record

`registered_bone_audit.py` and `phase1_antipoke_population_ab.py` have been
described as LIBRARY MODULES. They are not. Neither has a `__main__` guard, but
both are top-level scripts with module-level code that reads `sys.argv`, and
neither is imported anywhere. They run as `python <path> [pack]`, so a CLI
before/after capture IS possible for them.

KNOWN_HALF_PACK: **21 -> 3.** HPK-a and HPK-b both CAUGHT.

# 2026-09-21 — weight-aware reference bodies: the gate now sees weight 0

The half-pack work (above) left seven tools DECLARED as blind spots: each
measured every file against a reference body pinned to weight 1, so widening
its population would have scored `_0` garments in the wrong frame. Most of them
traced to one module. This fixes the root cause, converts the tools that
depended on it, and teaches the acceptance gate to judge weight 0.

## The root cause, and the rule that replaces it

`canonical_body.canonical_ube()` / `canonical_cbbe()` took no weight and cached
ONE body each. They now take `weight="_1"` (default, unchanged) or `"_0"`, and
resolve weight 0 through one rule, `weight_sibling`:

* **Weight 0 is the SIBLING of the chosen weight-1 file, from the same
  directory** -- not a second lookup. Each body is chosen by a rule (3BA first,
  shortest path; first mod with a tangent output); running it again for weight
  0 can land on a different preset in a modlist with two body mods, which is
  exactly what `nif_convert_bodyrefs._find_user_preset_body` does (an
  independent glob per weight).
* **It never falls back.** A missing sibling raises. `nif_convert.
  _weight_matched_ube_ref` DOES return the weight-1 body when the sibling is
  missing -- right for the converter, which must inject something; wrong for a
  measurement, which would print a believable number in the wrong frame.

Control: the default calls are byte-identical before and after. And each
weight-0 body is a genuine variant, not a stray copy -- identical topology,
different geometry:

    CBBE  18436/18436 verts, all move, max 1.74u, mean radius 10.00 -> 9.47
    UBE   29298/29298 verts, 1800 move, max 0.91u, mean radius  9.57 -> 9.56

That also bounds what the old pin cost on this pack: up to 1.74u over the whole
SOURCE body, up to 0.91u over ~6% of the CONVERTED body.

> **SUPERSEDED 2026-09-21 for the CBBE side.** "3BA first, shortest path"
> picked the 3BA body mod's own femalebody, a preset build the game never
> loads, and the CBBE line above is ITS weight morph. The body the game loads
> -- BodySlide's zeroed build, now what `canonical_cbbe` returns through
> `src/zeroed_body.py` -- differs between its weights on 1262 verts, max
> 0.72u: the neck, wrist and ankle seams; the torso is identical at both. The
> UBE line stands (same file). So do the sibling and never-fall-back rules;
> `zeroed_body` enforces the sibling rule itself.

**Copy-path bodies use the converter's own resolver instead.** For a copy-path
garment the right reference is the body the converter FITTED it to, and every
tier of `nc._find_ube_femalebody(weight)` is weight-specific (it never returns
the other weight). On this pack it resolves the same file as the sibling rule;
all four UBE resolutions share one directory.

## Two gate instruments, and the gate

The weight-1 output of both scorers is byte-for-byte unchanged, because
`acceptance.py` parses it. Weight 0 follows in its own block, worded so the
gate's patterns cannot land on it -- and the patterns are different per tool:

* `nipple_clearance` -- the gate takes the FIRST `^control|candidate` row and
  the FIRST `tighter N`, and ANY "0/0 IS NOT A PASS" marks the whole tip row
  unmeasured. Printed in the weight-0 block, that phrase would switch the tip
  gate off for weight 1 too.
* `bust_gap_score` -- the dangerous one. The parser's section is STICKY and its
  LAST row match wins, so a matching weight-0 row would silently OVERWRITE a
  gated weight-1 value.

Both use a `w0 ` label prefix and their own wording, pinned by tests that run
the REAL parser. Measured on the shipped pack (control = candidate = the pack):

    bust_gap_score            weight 1    weight 0
      population (shapes)        677         675
      author standoff p50     1.104u      1.310u
      gap to author p50       +0.624u     +0.325u
      penetrating verts         3410        3464   (+1.6%)

    nipple_clearance          weight 1    weight 0
      pieces covering tip        424         422
      tip clearance p50       1.379u      1.221u   (-11%)
      tip clearance p05       0.296u      0.216u   (-27%)

In both the weight-1 report is BYTE-IDENTICAL before and after, and fed through
the real parser the AFTER output yields the same weight-1 keys as the BEFORE
output (8 and 7 keys).

> **SUPERSEDED 2026-09-21 — the `bust_gap_score` AUTHOR figures above were read on
> the wrong reference body.** On the zeroed body: author standoff p50
> **1.416u** (w1) / **1.255u** (w0), gap to author p50 **+0.297u** / **+0.392u**,
> weight-0 population 675 -> 677. Weight 0 now sits FURTHER from the author
> than weight 1 does, not closer. Penetrating verts (3410 / 3464) are
> converted-side and unchanged; `nipple_clearance` reads the UBE body only,
> which resolves to the same file, and was not re-run.

**The gate now judges weight 0** under the same rules as weight 1: bust gap per
path, bust-band penetration, tip p50 / p05, pieces with less tip room, worst
single tip. A candidate worse at weight 0 ALONE fails. An unmeasured weight
reads SKIPPED with its reason -- never ok, never FAIL -- but a weight-0 row that
simply vanished still reads UNPARSED and fails, because treating "no weight-0
keys" as SKIPPED would let a silent parse failure through.

## collect_fit_dataset

Both weights, first-person kept (it is a column), each against its own body
via the converter's resolver. 2897 -> 5794 shape rows; all 2897 weight-1 rows
identical to the pre-change run with the new `weight` field removed. Paired by
garment and shape (2788 pairs; 109 shapes carry different names at the two
weights and cannot pair):

                       median w1   median w0
    bust clear p10       1.028u      0.970u
    bust clear p50       1.735u      1.626u
    butt clear p50       1.239u      1.229u

## source_delta_census

Both sides of the delta posed on the file's OWN weight: `canonical_cbbe(w)` for
the source, `canonical_ube(w)` for the converted side. Population: 235 armours
per weight -> 470 files, 468 scored (one per weight has no converted garment
shapes), 0 skipped for want of a body. CONTROL: all 234 weight-1 rows are
IDENTICAL to the pre-change run with the new `weight` field removed.

                              weight 1    weight 0
    scored                       234         234
    regressions > 5pt             55          59
      > 8pt / > 10pt / > 15pt  34/25/16    34/28/16
    gated clean                   52          49
    no matched source             13          14

**No weight-0 penalty in pose-induced exposure.** Paired by garment (234
pairs), 44 regress at both weights, 15 at weight 0 only, 11 at weight 1 only --
and 8 of those 15 and 6 of those 11 sit within 1pt of the 5pt line. Over the
167 pairs with a source at both weights, worst delta w0 - w1 has median
+0.03pt; weight 0 is worse in 85, better in 76 (sign test p = 0.53).

> **SUPERSEDED 2026-09-21 — the source side was read on the wrong reference body.**
> Re-run with the source on the body the game loads (converted side identical;
> see the 2026-09-21 reference-body entry below): regressions >5pt **61** (w1) /
> **60** (w0), >8 / >10 / >15pt 35/30/23 and 32/27/16; gated clean and no
> matched source unchanged. Paired: 52 regress at both weights, 8 at weight 0
> only, 9 at weight 1 only; worst delta w0 - w1 median +0.00pt, weight 0 worse
> in 50, better in 80 (sign test p = 0.01). Still no weight-0 penalty -- if
> anything weight 0 regresses less. Not 1:1 with the table above: 26 more
> regions pass the joint coverage floor.

**The one region that moves is the source side learning to see the bust.**
breast_side is the worst region of 3 weight-1 regressions and 14 weight-0 ones,
but the CONVERTED side does not change between weights (median conv w0 - w1
0.00pt, coverage 287 -> 287 breast, 300 -> 300 breast_side). The SOURCE side
does, and through its denominator: coverage is a ray test (a body vert is
covered when its outward ray hits the garment), and at weight 1 the CBBE
reference body pokes through most source garments at the bust.

    garments with < 150 of 300 sampled verts covered, source side
                     weight 1    weight 0     median covered w1 -> w0
    breast            98/143      12/143           83 -> 291
    breast_side       67/148       6/148          157 -> 295

So weight 0 is the first half where both sides see the whole bust, and the
breast_side rise is where the census can now look, not evidence the converter
does worse at weight 0. The flip side is OPEN: the weight-1 bust deltas have
always been measured over a minority of bust verts on the source side -- the
ones the reference body happens not to poke through -- so weight-1 bust
regressions may be undercounted. The weight-1 CBBE body is the larger of the two
(mean radius 10.00 vs 9.47); whether it is larger than the bodies those garments
were built for is not measured.

> **RESOLVED 2026-09-21 — it was the wrong body.** The weight-1 CBBE reference was
> the 3BA body mod's own preset build, not the body the garments were built on
> (248 of 340 paired source NIFs bundle the zeroed build bit-exact). On the body
> the game loads, weight 1 sees the bust as well as weight 0 -- under 150 of 300
> covered: breast 16/157 (w1) vs 19/157 (w0), median 291 -> 291; breast_side
> 10/153 vs 15/153, median 295 -> 287. breast_side is now the worst region of 8
> regressions at EACH weight (was 3 / 14). The weight-1 undercount flagged above
> was real in direction: weight-1 regressions 55 -> 61, under the comparability
> caveat above.

`--limit` counts FILES, so a limited run now covers about half as many
garments, each at both weights.

## What all four instruments say about weight 0

Every tool that can see it agrees: **at weight 0 the bust sits closer to the
body** -- less tip room (-11% p50, -27% p05), lower bust clearance, slightly more
penetration -- while sitting closer to what the author built. The butt barely
moves. `find_overinflation` (standoff) is still the exception from the half-pack
work: weight 0 is not the worse half there.

> **CORRECTED 2026-09-21.** "while sitting closer to what the author built" came
> from `bust_gap_score`'s author column on the wrong reference body and is
> REVERSED on the zeroed body (gap p50 +0.297u w1, +0.392u w0). Tip room, bust
> clearance and penetration are converted-side and stand. `source_delta_census`
> on the game-loaded source body still shows no weight-0 penalty.

## Method notes

* **Exit codes must stay literal in `main()`.** `tool_map` reads a tool's gate
  column from the int returns of `main`; a refactor that passed `rc` through
  silently dropped exit 1 from `bust_gap_score`'s row. Caught on regeneration.
* **`git stash --keep-index` recorded a staged file too** and conflicted on pop.
  Each commit's tree was then proved in a temporary detached worktree holding
  exactly the staged contents, which cannot collide with the working tree.

## Not done yet

* `band_class_census` (five acceptance rows) -- the code path is clear (template
  per weight, the preset's `small` side for weight 0, `--every` stepping by
  garment pair), but its runs need `CBBE2UBE_CLIP_PRESET`, a machine-local
  BodySlide preset that is not configured on this machine, and choosing one is
  the user's call.
* `single_swing_census` / `snugness_census` feed the open crotch-band lead;
  widening them would move numbers it has recorded. Left declared.

# 2026-09-21 — the CBBE reference body was the wrong body, and the author-side numbers moved

Found chasing the weight-1 source-coverage collapse in `source_delta_census`
(above). `canonical_body.canonical_cbbe` ("3BA in the path, then the shortest
path") picked the 3BA body mod's own `femalebody`: a build at some preset that
the game never loads. The converter's `_find_cbbe_base_body` makes the same
pick; changing what the converter fits against is a behaviour change, made
separately because it needs an in-game verdict. **No author-side number read
through either before this was taken on the body the game loads -- the
crotch-band lead included.** From 2026-09-15, when the 3BA body mod's file was
last replaced, they were read on its preset build; before then, on contents
nobody recorded, so older figures are not comparable in either direction.

## The finding

* **The game loads the BodySlide output's `femalebody`** (it wins that path),
  and that file IS the zeroed build: the slider set's ShapeData base shape plus
  every slider at its default for that weight, 0.00000u on every vertex at both
  weights. The game's UBE body is the same kind of build: template +
  `NipplesShowUp` 100 at both weights + `SkinnyMorph` 100 at weight 0. A zeroed
  preset lists no sliders, so the defaults are what it builds.
* **The picked body sits up to 1.97u off the zeroed body over 16,061 torso
  vertices**, and its weight morph grows the bust ~1u, so at weight 1 it grows
  THROUGH garments built on the zeroed body.
* **248 of 340 paired source NIFs bundle the zeroed body bit-exact.** Not all:
  the BSA-sourced garments (18 weight-1 rows) bundle a third body about 1.6u
  outside both at the bust, so the zeroed body is the right reference for the
  majority, not for every garment.
* How the game's body is determined: the race skin ARMA's model path -> the VFS
  winner of that path (overwrite, then enabled mods from highest priority down,
  then the game's Data folder) -> runtime body morphs on top, which are in no
  file.

## The fix -- harness only, no converter behaviour changes

`src/zeroed_body.py` finds each body's slider sets by what they BUILD, keeps the
family by base topology (3BA 18,436 verts, UBE 29,298), builds the zeroed
geometry, finds the file the game loads at that path, and accepts it only if it
IS that build to 1e-4u on every vertex. Otherwise `ZeroedBodyError` -- a
`FileNotFoundError`, so callers skip; nothing substitutes another body. Weight 0
must come from the same folder as weight 1. `canonical_cbbe` / `canonical_ube`
return it per weight, and `snugness_census`, `seat_error_vs_author` and
`inflate_census` now read the author there instead of through the converter's
helper. `paths.overwrite_dir()` reads MO2's overwrite from the ini.

On this pack the UBE reference resolves to the same file as before; only the
CBBE side moved. So `nipple_clearance`, `collect_fit_dataset` and
`band_class_census` (UBE side only) are unaffected, and so is
`authored_offset_ledger` (the source's own bundled body).

Mutation pairs ZBW-a..l, PWO-a, CBZ-a and ICW-a all CAUGHT. ZBW-l was MISSED
first: its test derived its threshold from the tolerance it guards, so loosening
one loosened the other; it now pins a literal. CBW-b is retired with the
per-weight cache it guarded. Suite 3725 passed / 2 skipped. **No in-game verdict
is owed for this change** -- it cannot move a vertex.

## Before -> after, shipped pack, only the reference body changed

    instrument                                      wrong body        zeroed body
    bust_gap_score   AUTHOR bust standoff p50  w1     1.104u            1.416u
                                               w0     1.310u            1.255u
                     gap to author p50         w1    +0.624u           +0.297u
                       body-swap / copy              +0.738 / +0.485   +0.471 / +0.094
                                               w0    +0.325u           +0.392u
                       body-swap / copy              +0.485 / +0.210   +0.528 / +0.283
    snugness_census  median ratio   body-swap         1.202             1.058
                                    copy              1.150             1.141
                     p90 ratio      body-swap         1.934             1.540
                                    copy              1.430             1.418
                     reading, body-swap           LOOSER than authored  fit preserved
                     (copy crosses the tool's 1.15 line by 0.009)
    seat_error_vs_author   mean / median          0.4269 / 0.3505u  0.4124 / 0.3625u
    single_swing_census    SOURCE loss p90, median    0.70u             0.56u
                           SOURCE pieces over 1u      66                39  (converted 38)
                           worse / better by 0.3u     11 / 60           28 / 37
    crotch-band lead       author bind p05, median   -0.416u           +0.054u
                           ours - author p05         -0.432u  (n=97)   -0.917u  (n=98)
                             deeper / shallower       70 / 2            89 / 1   (difference over 0.25u)
                           bulk p50, ours - author   +0.169u           -0.159u
    inflate_census         moved to the zeroed body, NOT re-measured

Populations and controls. `bust_gap_score`: 677 shapes at weight 1 in both runs
(path split 352/325 -> 354/323), 675 -> 677 at weight 0; penetrating verts
identical (3410 / 3464), and the converted bust p50 moved only through that
population shift (1.888 -> 1.876 at w1, 1.724 -> 1.721 at w0).
`snugness_census` (still `_1` only): copy 1146 -> 1121 shapes, body-swap
811 -> 796, "no hugging region" 355 -> 395 -- the hug mask is taken against the
reference. `seat_error_vs_author`: 4053 paired shapes over 2064 NIFs in both
runs. `single_swing_census`: the CONVERTED side is identical (n=224; loss p90
p50 0.43, p90 1.23, max 2.88u); the source rows are the 200 with a matched
source. The crotch-band lead is that census's paired bind clearance with both
arms over 90% of their own band. Recomputed on the old body it reproduces the
recorded -0.432u / n=97 exactly, but its bulk reads +0.169u where +0.206u was
recorded by a different method, so +0.169u is the like-for-like before.

**What moved, in words.** Weight 1 was the damaged half. On the zeroed body the
author's weight-1 bust standoff reads 0.31u larger -- the wrong body's weight
morph had grown the bust into the garments -- so our weight-1 gap to the author
halves (+0.624 -> +0.297; copy path +0.485 -> +0.094). Weight 0 went the OTHER
way (+0.325 -> +0.392): the recorded "weight 0 sits closer to the author" is
REVERSED. The body-swap path no longer reads looser than the author (median
ratio 1.202 -> 1.058: "fit preserved").
The crotch band is an INWARD offset across the band, about twice the recorded
depth, not a looser bulk over a buried tail: the "uniformity defect" reading is
WITHDRAWN. The author does not bury the crotch band (+0.054u); the old body
poked through it. Conform still cannot produce the offset (pull-in only, never
closer than its own `s_src`, above). A new candidate producer is the converter's
warp keyed on the wrong CBBE body, which sits ~+0.34u outside the zeroed body at
the crotch.

## `source_delta_census` on the body the game loads

Re-run on the pre-lane code with only `canonical_cbbe` replaced by the
game-loaded file -- the same file `zeroed_body` resolves (max deviation
3.8e-6u). 470 files seen, 468 rows in each run, both weights.

**CONTROL, stated exactly.** The converted side is identical on all 2431 regions
both runs score, and `conv_worst` is identical on all 468 rows. The literal
row-for-row comparison is NOT identical, and only through region MEMBERSHIP: the
`MIN_COV = 30` gate (`source_delta_census.py:228`) is JOINT -- a region is scored
only when BOTH sides cover 30 sampled verts -- so a different source body moves
regions in (37) and out (11). Gated clean (101) and no matched source (27) are
unchanged.

Summed source coverage over the shared regions, weight 1:

    breast        17469 -> 39378   +125%
    breast_side   24259 -> 39987    +65%
    belly         27171 -> 40430    +49%
    butt          36076 -> 41998    +16%
    lower_back    36946 -> 41569    +12.5%
    upper_chest / upper_back / thigh      +1% to +3%

Weight 0 is flat (all within about 3%) except belly, +33%. **The weight-1
"source coverage collapse" was the reference body -- confirmed.**

    regressions > 5pt               wrong body   game body
      weight 1                          55           61
      weight 0                          59           60
      > 8 / > 10 / > 15pt, weight 1   34/25/16     35/30/23
      > 8 / > 10 / > 15pt, weight 0   34/28/16     32/27/16

**NOT comparable 1:1:** 26 more regions now pass the coverage floor, so part of
the rise is regions that were never scored before. Paired by garment, weight 0
is worse in 50 and better in 80 (sign test p = 0.01): still no weight-0 penalty.

Caveat on both runs alike: the census poses garments through
`multipose_clip_test.analyse_with_body`, which reads each garment shape's raw
verts (`posed_clip_test.read_skin`) with no frame check -- the frame-assumption
class. The before/after comparison stands, because both runs read the same
garments the same way; absolute per-garment numbers carry the caveat until
that reader chooses its frame by evidence.

## OPEN: `snugness_census` now scores body helpers as garment

On the zeroed body 18 shapes read an authored standoff under 0.05u (none did on
the old body), and they top the LOOSEST-15 list at ratios 5-14.7: body
stand-ins -- 7 `3BA Ref`, 7 `VirtualBody`, one `collision body`, three more --
that sit ON the zeroed body. Dropping those 18 rows moves the path medians by
at most 0.002 (copy 1.141 -> 1.140, body-swap 1.058 -> 1.056), so the headline
reading stands; the loosest list does not. Same class `seat_error_vs_author`
closed by excluding untextured proxies. NOT fixed.

## Method: golden baselines drift -- capture the parent in the same session

Golden baselines captured about 16:35-16:50 passed an off-switch check at
16:54. At about 19:30 the SAME commits, checked against their OWN baselines,
regressed on one piece, deterministically: `softbody-dress`, `Top`'s weight total
on `NPC L UpperArm` changed 0.0024 / 0.0021, no vertex moved. No instance file
changed and the glow debug variables were ruled out; the cause is unknown.
**To isolate one commit, capture the parent's baseline in the SAME session and
check the child against it.** A baseline from hours earlier can fail on drift
alone.

## Still owed

* The in-game verdict on the converter change, which is made separately.
  Nothing measured here has one.
* `inflate_census`: moved to the zeroed body, not re-measured.
* The `snugness_census` helper shapes above.
* `single_swing_census` / `snugness_census` are still `_1` only; widening them
  is its own change.
* The crotch-band lead's conform test is still an in-game A/B with
  `CBBE2UBE_NO_PHASE2_CONFORM=1`. The converter change removes the wrong-body
  warp keying; whether that moves the lead is unmeasured.


# 2026-09-21 — the converter fits against the zeroed bodies

The converter made the harness's wrong pick too: `_find_cbbe_base_body` skipped
every mod named like a BodySlide output and landed on the 3BA body mod's own
preset `femalebody`, up to 1.97u off the zeroed body the game loads. With the
converter's own warp code that moves weight-1 garments ~0.6u further in than
weight 0 (the zeroed-keyed control predicts 0.000). Downstream passes erase most
of it; pieces that skip them ship it whole. Measured on the shipped pack: the
w1-w0 bust shift survives at median -0.008u over 161 flat body-swap stems and
-0.032u over 68 flat copy stems (the #00-keyed warp predicts -0.60u); a tail of
22 of 103 flat copy stems and 8 of 162 flat body-swap stems ships it nearly whole,
-0.26u to -0.92u (capes, cloaks, scarves, tomes, pouches, belts, skirts). The six
pieces below come from that tail; which pass erases the shift for the rest is not
measured. The harness entry of the same day
records the finding; this one records the converter change.

## What changed

**906580e.** The CBBE body the warp morphs FROM, the UBE body it morphs TOWARD
and the UBE body injected under body-swap garments come from
`src/zeroed_body.py` first: BodySlide's zeroed builds, as the game loads them.
An explicit override still wins. If the game's body is not a zeroed build, the
old discovery by name runs and the log says so once per process ("!! no zeroed
CBBE body at weight N: ... -- falling back to discovery by name"); a GUI run
instead starts its Reference bodies window on the game's body, flagged. Every
converter lookup of the two reference bodies goes through the resolver (weight
transfer, the collision-proxy warp, the UBE-native scan, the overlay rebake
too); only the body-mod exclusion set was compared before and after -- the
UBE-native skip list was not. Off-switch
`CBBE2UBE_NO_ZEROED_BODY_REFS=1`, also the Settings toggle "Fit against the
zeroed BodySlide bodies" (Paths / Bodies, advanced). The UBE side resolves to
the same files as before on this modlist, now by MO2 priority rather than first
alphabetical match.

**7876496.** Pressing Convert opens a "Reference bodies" window (skipped for a
dry run and when the zeroed bodies are switched off): one dropdown per body,
starting on the verified zeroed build and listing every other provider as
`[zeroed]` / `[NOT zeroed, off by up to N.NNu]` / `[not checked]`; other-family
bodies, half pairs, unreadable and missing files are listed with a reason, not
offered. The choice reaches that run's child environment only. The body injected
under body-swap armour now follows the UBE choice and overrides -- it ignored
every override before, the Settings "UBE body reference NIF" included. All-mods
runs skip body mods by the race-body files they ship
(`auto_convert._body_mod_names`), not by which body the fit uses; 906580e alone
would have let the 3BA body mod's 10 collision-body NIFs into All-mods runs.
**Deliberately NOT following the pick:** the preset bake and the physics
chain-rest lift keep `_find_user_preset_body`, because they need the user's
preset, which a zeroed body lacks. That lookup walks mod folders alphabetically,
disabled mods included -- a separate, pre-existing defect, not fixed here.

## Measured

Tail pieces, both weights, through the batch worker -- weight-1 minus weight-0
along the UBE normal, median / p05:

                     shipped            switch OFF    ZEROED (default)
    cloak           -0.361 / -0.851     identical     +0.000 / +0.000
    cape            -0.028 / -0.518     identical     +0.000 / -0.102
    belt bags       -0.308 / -0.945     identical     +0.000 / +0.000
    book            -0.471 / -0.814     identical     +0.000 / +0.000
    front pouch     -0.247 / -0.314     identical     +0.000 / +0.000
    skirt front     -0.325 / -0.885     identical     +0.000 / -0.000

"identical" = the same numbers AND vertex counts as the shipped pack: the
harness reproduces production, and the switch restores it exactly.

Golden set, 15 pieces at weight 1, baseline captured at the lane's base:

    REPEAT CONTROL   two captures of unchanged code: 0 arrays differ
    OFF-SWITCH       CBBE2UBE_NO_ZEROED_BODY_REFS=1: identical 15/15 (1e-4u)
    BLAST RADIUS     all 15 move -- intended. Garment verts within 4u of the
                     body (n=117,842): median +0.000u, p05 -0.188, p95 +0.509;
                     27% move out >0.05u, 17% in; bust median +0.014u
                     (`layered-legion` +0.495, `hide-collider` +0.222).
                     Three shapes gain one bone each (a thigh / skirt bone).

**7876496, same-hour controls.** The earlier baselines drifted (see the harness
entry), so fresh baselines were captured within the hour from 906580e (zeroed on)
and from 019e0d3 with the off-switch set, and 7876496 was checked against each:
**15/15 identical both.** Suite 3780 passed / 2 skipped; full mutation gate 202
caught, 0 missed, 0 not applied; ZBR-a..e and BDP-a..ac CAUGHT.

The rebuild's release markers gained `src.zeroed_body`, `src.body_choice` and
`_body_mod_names` (since 1.4.2). The version string is still 1.4.1, so
`release_gate`'s bundle-scan reports the later markers NOT CHECKED and cannot by
itself show the new code shipped; a direct `_marker_found` probe did, and
returned ABSENT on the previous build.

**Owed: an acceptance-gate run on a full pack conversion, and the in-game
verdict.** Nothing here has one.
