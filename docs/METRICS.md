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

---

# 2026-07-27 — rear/penetration metric rebuilt, and a repeat offence

## The same wrong metric got rebuilt from scratch

`scripts/mesh_penetration.surface_penetration` decides inside/outside from the nearest
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

`scripts/mesh_penetration.ray_exposure` — march each body vertex along its own outward
normal; if no garment triangle blocks it, that vertex is visible from outside.
Unambiguous by construction, and it is what a player sees. Positive controls in
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
> region in the pack, not a clean one.

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

**Generalised:** a control that cannot fail is not a control. Before trusting one,
break the thing it guards and confirm it screams. This is the third time on this
project that a passing control was measuring nothing.

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

Two findings about the ACCEPTANCE GATE rather than about a single metric. Both
matter more than a metric bug, because the gate is what turns a metric into a
verdict, and a gate that scores the wrong direction launders a regression into
a pass.

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
| `snugness_census.py` | **SOUND** | already chooses by evidence, documents the exact trap, and asserts its reference stands upright. Nothing to do. |
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

## Still open

`morph_clip_test._aligned`, `snugness_census.pick_frame` and the fix on PR #9
are three more copies of this decision, left alone deliberately: #9 is open and
rebasing under it would re-hash the commit the exe stamp names. Fold them into
`pick_frame` once #9 lands.

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

Line 306, before any work. Reproduced on unmodified `testing`, so it is not
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

# 2026-09-20 — eight of the 21 half-pack tools resolved, and one of them was right all along

The ratchet (`tests/test_pack_population_declared.py`) froze 21 tools that
enumerate the pack with a `*_1.nif` glob and never say so. Freezing stops the
count growing; it fixes nothing. This is the first tranche actually resolved,
in small groups with the tool's output captured on the shipped pack BEFORE and
AFTER each change.

**The rule is DECLARE, not "always both weights".** Seven of the eight here
read both weights now. The eighth was already correct and only needed to say so
— and finding that is the result, not a failure to convert it.

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

## What was NOT done, and why

* `fit_audit.py` is **DEAD and stays on the list.** It raises AttributeError at
  line 310 (`sliderset_gen.MORPH_SHAPE_CAP` exists nowhere in the repo) on
  every run, so no before/after is possible. Recorded BLOCKED, not fixed.
* `single_swing_census` and `snugness_census` feed the live crotch-band lead and
  were deliberately left alone: changing their population would move the lead's
  numbers mid-investigation.
* **`band_class_census` must NOT be converted by a glob swap.** Three things in
  it are pinned to weight 1 and would manufacture numbers: the copy-path
  template body (`_find_ube_femalebody("_1")`, and the copy path is most of the
  pack), the preset (`_load_preset` takes the `big` / weight-100 side only), and
  `--every N`, which strides a SORTED list where `foo_0` and `foo_1` are
  adjacent — so `--every 2` would silently become a weight-0-only census.

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

KNOWN_HALF_PACK: **21 -> 13.** HPK-a and HPK-b both CAUGHT.
