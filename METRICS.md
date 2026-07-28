# Measurement audit

Which of our metrics give accurate answers, which gave wrong ones, and what
replaced them. Audited 2026-07-26 after several conclusions had to be withdrawn.

**The rule this produced:** validate a metric with a POSITIVE control before
trusting it. A metric that reports "no problem" is indistinguishable from a metric
that cannot see the problem, and we shipped decisions on that ambiguity three times
in one day.

---

## Sound

### Ray-based skin exposure — `scripts/verify_skin_exposure.py`
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

### Zero-weight bone detection — `scripts/verify_zero_weight_bones.py`
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
3. **A second, independent method** on the same input. Disagreement means at least
   one is wrong; agreement is the only real evidence.
4. **Synthetic tests** with analytically known answers.
5. **State what the metric cannot see**, next to the number it reports.

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

`scripts/collect_penetration_census.py` — full census, one row per armor, six regions,
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

`scripts/multipose_clip_test.py` over `scripts/pose_set.py`. Body verts COVERED at bind
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

`scripts/source_delta_census.py`. Level metrics cannot separate "the converter broke
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

---

## Sound: containment census over the rigid population

`containment()` in `scripts/mesh_penetration.py`, 199 fully-rigid armors, bind pose,
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
