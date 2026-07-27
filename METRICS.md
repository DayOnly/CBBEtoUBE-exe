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
