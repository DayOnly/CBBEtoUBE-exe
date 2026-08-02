# Jiggle: what we know, and why the sliders lie

> The work plan built on these measurements is `DESIGN_JIGGLE_PLAN.md`, a
> **local working note that is not in this repository** — gitignored, like
> `CLIPPING_LOG.md`. It was written here as a link, which resolved only on the
> author's machine and was dead in every clone; recorded as plain text now.

How armor comes to follow the body's breast/butt/belly physics, what each knob
actually does, and a proposal to make the numbers mean what they say.

Everything here is measured on a real conversion of this modlist (2026-07-26),
not inferred. Where a number is an estimate it says so.

---

## 1. The problem in one table

Three passes graft jiggle. Each scales it differently, and **no slider states the
ratio it delivers** — the number the user sets is not the number the armor gets.

| pass | the slider | body's jiggle at a typical vert | **actually delivered** |
|---|---|---|---|
| chest match | strength `1.0`, documented "full match -> tracks body" | 0.427 | **0.351** |
| butt match | strength `1.0`, same wording | 0.126 | **1.000** |
| jiggle transfer | factor `0.85`, "fraction of the body's local jiggle" | — | **0.661** |

The same nominal `1.0` yields 0.35 on the chest and 1.00 on the butt. Not a design
decision: both share an absolute cap of `0.15`, and the body's median breast weight
(0.427) is over three times its median butt weight (0.126), so the cap binds hard in
one place and never binds in the other.

**What "ratio" means here.** A garment vert weighted `w` on a breast bone follows
that bone's motion by `w`. If the body vert underneath carries 0.43 and the armor
carries 0.15, the armor travels roughly a third as far as the flesh — and the flesh
emerges through it. Ratio 1.0 is the armor tracking the body exactly.

---

## 2. The three writers

### `_conform_fitted_to_body` -> `_chest_match_vert` / `_butt_match_vert`

For a shape that is ALREADY body-fitted. Draws jiggle weight from an anchor bone
(`NPC Spine2` for chest, `NPC Pelvis` for butt), so mass is conserved and the pass is
idempotent.

```
want = min(body_jiggle_total * strength, cap, available_anchor_mass)
```

| knob | default | effect |
|---|---|---|
| `_CHEST_JIGGLE_STRENGTH` / `_BUTT_JIGGLE_STRENGTH` | 1.0 | multiplies the body's weight |
| `_CHEST_JIGGLE_CAP` / `_BUTT_JIGGLE_CAP` | 0.15 | **absolute** ceiling on the total |
| `_CHEST_JIGGLE_PERBONE` | 0.09 | per-bone ceiling, tightens the chest further |
| `_CHEST_Z_LO/HI`, `_BUTT_Z_LO/HI` | 88-102 / 60-78 | absolute world-Z trapezoid |
| `_CHEST_RAMP`, `_BUTT_RAMP` | 4.0 | trapezoid edge softness |
| `_CHEST_PROX`, `_BUTT_PROX` | 5.0 | max distance to the body vert |

The cap is what actually governs the chest: strength is 1.0 and never binds.

### `_transfer_body_jiggle_to_fitted`

For a shape with NO jiggle of its own — grafts the bones outright rather than
rebalancing existing ones.

```
grafted = body_jiggle * factor * closeness        closeness = 1 - d / 6.0
```

| knob | default | effect |
|---|---|---|
| `_JIGGLE_TRANSFER_FACTOR` | 0.85 | fraction of the body's weight |
| `_CONFORM_VERT_PROX` | 6.0 | distance at which `closeness` reaches 0 |

No cap. Instead the ratio decays linearly with distance, so **the slider is an upper
bound reached only by a vert touching the body**. Measured mean closeness on actually
grafted verts: **0.778**, so `0.85` delivers `0.661`.

**Its reach is small.** Of 488 shapes sampled, it can touch **16 (3.28%)**:

| exclusion | shapes |
|---|---|
| doesn't hug the body (`fit >= 0.90`) | 221 |
| already jiggles | 121 |
| not leg-dominant | 59 |
| chain garment | 54 |
| non-identity global-to-skin | 17 |

Consequence, measured: changing the factor `0.85 -> 1.0` across a full reconvert
produced **no change at all** — the same 130,237 sampled verts returned identical
percentiles. Pack-wide jiggle weight comes overwhelmingly from the other two writers
and from source-authored weighting.

### `_install_skin` (the main reskin)

Carries whatever the body-blend produces. Not a "jiggle knob", but it is where most
armor jiggle actually originates, which is why the two knobs above move so little.

---

## 3. What the pack actually looks like

Armor jiggle weight / body jiggle weight underneath, 130,237 grafted verts over 300
meshes:

| p10 | p50 | p90 | mean |
|---|---|---|---|
| 0.435 | **1.044** | 1.658 | 1.125 |

70.8% of verts track at >= 0.95; 17.9% below 0.75. So the pack median is already
close to 1:1 — the lagging tail, not the median, is the problem, and the two sliders
above barely reach it.

Values ABOVE 1.0 are not automatically wrong: the armor sits outside the body, so a
vert further from the bone travels further under the same rotation. A ratio slightly
over 1 can be exactly right. This is why "set everything to 1.0" is not obviously the
target — see the open question in §5.

---

## 4. Proposal: make the slider the real number

One concept, expressed the same way everywhere: **follow ratio** — how much of the
body's local jiggle the armor reproduces. `1.0` = tracks the body exactly.

1. **Express caps as ratios, not absolute weights.** `cap = 0.15` becomes
   `follow = 0.35` for the chest and `follow = 1.0` for the butt — the values they
   already deliver today. Same behaviour, but the number is now legible, and the
   accidental chest/butt asymmetry becomes visible instead of emergent.

2. **Take `closeness` out of the transfer ratio.** Make it a plateau: full ratio for
   a vert that hugs the body, feathering to 0 only over the last unit or two of the
   proximity window. Then `factor` IS the achieved ratio for the verts that matter,
   instead of an upper bound nothing reaches. (Feathering must stay — a hard cutoff
   at 6.0 units would put a seam in the weighting.)

3. **Retire `strength`.** With caps expressed as ratios it is redundant: it has been
   1.0 since it was introduced and never binds.

Result: three knobs (`chest follow`, `butt follow`, `transfer follow`), each meaning
the same thing, each stating what it delivers. Defaults chosen to reproduce today's
output exactly, so adopting the model changes nothing until a slider is moved.

## 5. Open questions

- ~~**Is 1.0 the right target?**~~ **ANSWERED by the census.** Not a fixed number at
  all: the target is *derived per shape* from clearance and the body's own jiggle
  (`required follow = 1 - clearance/(bounce x body_jiggle)`). Shapes meeting their
  requirement clip 2.2% of the time; shapes short by 0.25-0.50 clip 92% of the time.
  Typical requirement is ~0.45-0.66, well under 1.0.
- ~~**Why is the chest capped at all?**~~ **PARTLY ANSWERED.** The cap is now a
  material-aware ceiling over a derived requirement (`#chest-follow-ratio`). But the
  census removed its geometric excuse: metal and soft are indistinguishable (clearance
  1.58 vs 1.55, required follow 0.66 vs 0.61). The ceiling is an AESTHETIC choice —
  jiggling steel looks wrong — not a measured necessity.
- ~~**What the ceiling actually blocks is UNLABELLED armour, not metal.**~~
  **SUPERSEDED — material is the wrong axis entirely (`#source-follow`, 2026-07-27).**
  It was true that 129 of the 182 ceiling-blocked shapes are `unknown` rather than
  metal, and that nothing can classify them (diffuse tokens are `armor`/`chest`/`body`;
  shader numerics scored 76.0% against a 76.3% base rate). But the fix is not a better
  classifier. Joining 581 output shapes back to their source mesh:

  | source bust weighting | n | output follow | requirement | short |
  |---|---|---|---|---|
  | weighted (≥ 0.5) | 279 | 1.454 | 0.634 | **0.7%** |
  | unweighted (< 0.5) | 302 | 0.349 | 0.646 | **69.9%** |

  Same requirement either way — so it is not geometry, it is whether the author
  weighted the bust, and the ones they didn't ARE the clipping population. Within the
  unweighted group material separates nothing (requirement 0.634/0.662/0.640 for
  soft/rigid/unknown; soft has *more* clearance than rigid, 1.97 vs 1.55).

  And the ceiling never enforced its own rationale: **27.8% of "rigid" shapes already
  ship above follow 1.0** — chainmail, daedric plate, dwarven mail — reaching it
  through their own source weighting, which the ceiling never touched. The ceiling
  caps the *graft*, never the pass-through, so it only ever bites garments that need
  help. Proven end-to-end: with the `studded` keyword neutralised, source-follow alone
  takes the reported cuirass from 0.338 to 0.793 — the keyword's exact result — and
  skin under motion from 71.2% to 8.8%.
- **The transfer pass's 3.28% reach.** The `fit >= 0.90` gate excludes 221 of 488
  shapes. That threshold, not the factor, is the lever on how much this pass does.
- **A whole-shape gate cannot judge a welded shape.** A cuirass modelled as one piece
  with its own physics skirt scores 0.50 whole-shape fit and 0.69 over its rigid verts;
  every gate in every pass reads the first number. `#chain-welded-torso` judges these
  on the rigid verts, and the same question applies to any other whole-shape gate —
  `fit >= 0.90`, `chain_frac <= 0.05`, and the leg-dominance test all read totals over
  shapes that may be two garments welded together.
