# CBBEtoUBE — Design & Rationale

This document holds the **why** and the **how** for the converter's non-obvious
subsystems. Inline code comments cover **what** a piece of code does; when they
need to explain a design decision, a failure mode, or the history behind a
value, they point here instead of carrying a paragraph:

```python
# Flat clearance floor on the lower leg (flex zone).  [DESIGN: Flex-zone standoffs]
```

Each `[DESIGN: <heading>]` tag matches a heading below. Keep the tags stable —
they are the link. When you change behavior, update the matching section here.

The converter turns a CBBE / 3BA-authored armor NIF into one that fits and
morphs on the **UBE** body. UBE is a different, generally larger body, so armor
authored for CBBE sits partly inside and partly outside it and has to be
re-fitted, re-skinned, and re-cleared.

---

## Pipeline overview

`convert_nif()` chooses one of two paths from the source shapes:

- **Armor-only** (no inline body shape) → a body-aware **rebuild**: warp each
  shape onto the UBE body, re-skin it near the surface, push it clear.
- **Inline body or exposed body-skin** → **phase-2 body-swap**
  (`convert_nif_phase2`): drop the source body/skin, inject the full UBE
  `BaseShape`, then re-fit the armor around it.

On top of the fit, a series of per-shape passes handle clearance, layered-cloth
ordering, leg-plate conform, physics-cloth preservation, and morph data. The
sections below cover each.

---

## The fit contract (1.2)

Until 1.2 the chain was **speculative**. Twelve phase-2 passes compute against
the body; every one assumed the garment shared its coordinate frame, none
asserted it; and nothing between them measured whether a pass had helped. Two
failures follow directly from that shape, and both happened:

- a single bad transform displaced one shape by 40 units, and all twelve passes
  computed against a garment that was not where they thought it was — for three
  months, with every pass still reporting success;
- each pass caps only its OWN contribution, so the push stack is individually
  bounded and jointly unbounded. Over-inflated meshes shipped twice.

Per shape, the chain now states:

| step | cost | what it does |
|---|---|---|
| frame precondition | ~free | discard a transform offset that moves the shape AWAY from the body |
| diagnose | 1 measurement | exposed skin before anything runs |
| *(each pass)* | array copy | checkpoint — **no** measurement |
| verify | 1 measurement | if the chain as a whole regressed, ship the best checkpoint |

**Two measurements per armed shape, not one per pass.** Checkpoints are copies
(microseconds against ~120ms), so the chain can afford to remember every pass and
pay to inspect them only when the verify fails.

### Why the contract is on the CHAIN, not each pass

"Reject any pass that measures worse than its input" was the obvious design and
the pass trace (`CBBE2UBE_PASS_TRACE=1`) refutes it. Over 48 traced shapes:

- exactly **one** pass ever regressed bust fit — `conform`, 5 times;
- **all 5** were recovered downstream;
- **0 of 48** shapes ended worse than they started (total exposure 8138 → 245).

`conform_to_source_standoff` pulls IN by design and later passes push back out.
Reverting it per-pass would have blocked a correct pass five times and biased
every garment looser — which is precisely the over-inflation reported from the
game. **Intermediate regressions are how the chain works.** This is also why the
per-pass guards on the anti-poke and the soft-cloth inflate were removed in 1.2:
neither ever regressed, and the chain verify covers the outcome for a quarter of
the measurements.

### What it deliberately does NOT do

It does not skip passes when the entry diagnosis looks clean. Bind-pose clipping
is blind to animation — "at rest" in game is an animated pose, and the anti-poke
exists for morphs and motion this metric cannot see. Gating passes on a
bind-pose number trades a measurable defect for an unmeasurable one. The chain
measures whether the passes *collectively* helped; it does not decide which ones
to run.

### Two metrics, because clipping has no upper bound

An over-inflated garment scores a perfect 0.0% clipping — nothing pokes through a
balloon. **Standoff** is the counter-metric, anchored on a piece confirmed correct
in game (median 1.15u, p90 1.52u). Never read one without the other. See
`METRICS.md` for the calibration and for the metrics this replaced.

### Telemetry is a file

Conversion fans across a process pool, and in the frozen windowed exe a worker's
`print()` can be discarded outright — a clean log is not evidence of a clean run.
Frame corrections, chain verdicts and standoff distributions append to
`standoff_audit.jsonl` at the output mod root. Failed measurements are recorded
too: one that errored must not look like one that found nothing.

Records carry `entry`, `final` and **`shipped`**. Read `shipped`, not `final`:
when a rollback fires, `final` is the measurement that was *rejected*. On the
first pack-wide run that distinction was 174 exposed verts versus 101 actually
shipped, and made a run with **zero** regressions read as "20 shapes ended worse".

### The pass that ran on nothing

`conform_to_source_standoff` is the only pass that reels an over-projected
garment back onto the body; everything else nudges outward. It was silently
skipped on some pieces because **two body detectors disagreed**:
`classify_shapes` → `_looks_like_inline_body` identified a shape as the body and
dropped it for the swap, while `_is_body_pynifly_shape` refused the same shape
for having fewer than 40 bones — a BodySlide-output inline body only carries the
bones its surviving verts touch, and the hide cuirasses ship one with 26. So
`src_body_v_p2` stayed `None` and the gate never opened.

Nothing recorded it: no exception, no warning, the pass simply absent from the
trace. It surfaced only because the per-pass **standoff** trace was added to
chase a gap reported in game. Cost on the affected piece: 2.40u of standoff at
the strap line, against a 1.79u maximum across 42 shapes where the pass ran;
with the fallback it lands at 1.72u with clipping unchanged at 0.00%.

The fix is a fallback to the shapes `classify_shapes` already named, reached
only when the strict detector returns nothing — so it cannot change which shape
is picked where that detector already answers (verified byte-identical on a
piece that previously worked).

**Scope: 14 pieces.** 103 of 482 source pieces match the predicate; 22 of those
reach the converted output (the rest are HIMBO/male bodies the female-only
policy never converts); and converting all 22 showed **8 route to phase 1**, the
copy path, which never reaches `conform` at all — the scan tested for a body
`classify_shapes` could name, but phase-2 routing has further conditions, so it
over-matched. It concentrates in the vanilla-lineage armours — hide, imperial,
stormcloak, draugr, Ysgramor — whose BodySlide output emits an inline body
carrying only the bones its surviving verts touch.

Each narrowing came from measuring rather than reasoning, and each was smaller
than the last: **103 → 22 → 14**. Verified on the final set: **48 of 48 armed
shapes run `conform`**, with a control that already ran it still doing so.

> **A sampling lesson, not a footnote.** This was first reported as "rare — 42 of
> 42 armed shapes in a 9-mod census ran the pass". The census drew nine pieces
> that all happened to have a detectable body. A sample landing entirely in one
> state establishes no rate at all, and it was read as if it established a low
> one. The real figure is 21.4%, found by sweeping every phase-2 piece. When the
> question is "how often", sample only if the sample can observe both outcomes —
> otherwise sweep.

---

## Source selection (which mesh feeds the conversion)

Before any fitting, `discovery.build_mesh_index` decides WHICH mod provides each
armour mesh, resolving through the full MO2 VFS. The provider matters as much as the
fit: an armour is authored FLUSH on whatever body it was built against, and the
converter conforms it onto the UBE body -- so if the chosen source was built on a body
whose proportions differ from UBE, the piece is born gapping or clipping before a
single pass runs. Two rules encode this:

1. **Tier: deprioritise BodySlide OUTPUTS** (`#bodyslide-source`). A 3BA/HIMBO/NSFW
   BodySlide output is the mesh morphed to a specific PRESET; feeding it into a UBE
   conversion bakes the wrong body's shape in (squashed layers -> clipping; the New
   Leather Armor bug). 3 tiers, MO2 priority within each: (0) base/replacers, (1) UBE
   outputs, (2) other-body outputs. A BodySlide output still wins a mesh nothing else
   provides.

2. **Within a tier: prefer the CANONICAL-body source over a BESPOKE-body source**
   (`#body-match-source`). Some mods (an HDT-SMP "vanilla armours" pack, a retexture)
   bundle their OWN body -- often a slim/large preset that is NOT the canonical 3BA
   body. A soft-body band authored flush on a +9.88u big-bust bundled body is kept at
   its source position (the converter does not warp physics cloth) and stands off the
   +5.74u UBE bust -> the Fur Cuirass +1.77u breast gap. So when a same-tier challenger
   bundles the canonical `3BA` body and the incumbent bundles ONLY a bespoke body, the
   challenger wins -- it converts flush. `_body_provenance(path)` returns
   `(has_canonical, has_bespoke)`:
   - **canonical** = a shape named `3BA`.
   - **bespoke** = a body-skin-textured shape (diffuse matches `femalebody`/`malebody`/
     …) that is NOT canonical AND is a real body: `>= 500` verts and `>= 35u` z-range.
     The size floor is essential -- it rejects an exposed-skin SLICE (baked hand/neck
     skin on a robe, body-tex'd but ~46 verts / z-range 5) which must NOT count as a
     bundled body.
   The swap fires ONLY when `incumbent == (canonical=False, bespoke=True)` and the
   challenger has a canonical body. Three guards fall out of that:
   - a source that bundles NO body (a physics robe: cloth + collision, no body-skin
     shape) is `(False, False)` -> never swapped, so its SMP physics is preserved;
   - the incumbent already having a canonical body is `(True, …)` -> never swapped, so
     MO2 priority decides among body-standard sources;
   - the rule is WITHIN-tier only, so it can never promote a tier-2 output over a
     tier-0 base (the earlier source-tier fix stands).
   Open failure -> `None` -> treated as unknown, never a swap basis. Opt out with
   `CBBE2UBE_NO_BODYMATCH_SELECT=1`. Measured pack impact: 42/2165 meshes re-source;
   the Fur Cuirass band standoff drops +1.77u -> +0.59u. The tier-2 3BA-OUTPUT source
   has both physics AND a matching body but promoting it would need overriding the tier
   system -> deferred.

---

## Fitting: warp + re-skin

**Why.** BodySlide bakes armor to a specific body at slider-zero. On the bigger
UBE body those verts land in the wrong place, and the armor's bone weights are
for the CBBE body's skinning, so it deforms wrong at runtime.

**How.** Two levers:

- **Warp** moves each vert by the measured CBBE→UBE body deformation, so the
  armor follows the body's shape change while keeping the artist's drape. Where
  no CBBE/UBE body pair is available it falls back to a *snap-outside* heuristic
  (push verts that ended up inside the body back out to a small standoff).
- **M6 proximity re-skin** blends the injected UBE body's bone weights onto
  armor verts near the surface (full at the skin, fading out with distance).
  This is what makes single-bone "rigid prop" pieces morph with the body
  instead of hanging static, and what lets the armor track the UBE skeleton.

Rigid attachments (dagger, scabbard, pauldron — one bone holds most of the
weight) deliberately get a *low* re-skin rate so they keep tracking their parent
bone instead of smearing across the body.

**Warp internals.** The per-vert delta is IDW-interpolated (1/d², K-nearest) from
the body, so the nearest body region dominates. A distance falloff zeroes the warp
far from the body — otherwise a gauntlet's fingertips get dragged by the wrist
delta and lose their pose. And an *upper-body standoff damp* stops rigid stand-off
geometry (stiff collars, high pauldrons) from inheriting the full delta: the body
broadens at the chest/shoulders CBBE→UBE and the warp would shear those pieces
outward, so the damp fades it where a vert is both high-Z and far from the body.
(Armor still sits a touch tighter than hand-built UBE armor because BodySlide adds
an outward inflation when it builds UBE armor that the warp doesn't replicate — the
inflation post-pass handles that.)

**Re-skin vs source skin `[DESIGN: Morph-TRI reskin]`.** The M6 re-skin's K-NN
body-bone blend can be unstable under animation (equip fly/spike, even CTD on
dense shapes). So when a shape ships its *own* source morph TRI — the author
already built RaceMenu/BodySlide morphs for it — the converter keeps its stable
source skin and skips the body-blend re-skin, preserving TRI-morph fidelity.

A BodySlide TRI only supplies **body-slider** morphs (a static per-character shape
offset), not leg/butt *flex during animation*. That flex-follow is added by a
SEPARATE pass — the leg-conform / butt-match (`_match_rigid_leg_bend_to_body`,
`[DESIGN: Leg-plate bend / butt-jiggle conform]`), which grafts UBE scale bones
(FrontThigh / RearThigh / RearCalf / Butt) and runs **regardless** of the morph-TRI
exemption. So an exempted morph-TRI leg shape still gets its animation follow from
that pass — it does NOT lose its scale bones.

History (2026-07-08): a change tried ALSO grafting scale bones inside the re-skin
path for morph-TRI shapes (`CBBE2UBE_MORPHTRI_SCALE`), on the theory the exemption
dropped them. It was wrong — the leg-conform already provides them — and grafting a
second time onto a shape driven by its own body-slider TRI over-responded and caused
a coverage regression (body poked through the thigh). It is now **opt-in, default
OFF**; the default is the clean exemption (untouched source skin). See
`[DESIGN: Leg-plate bend / butt-jiggle conform]`.

Note this hinges on which source copy wins the VFS: a base mod may ship no TRI
while a BodySlide-output override at the same path ships one. The exe resolves to
the load-order winner (the copy the game loads), so the TRI is seen when it will
actually be present at runtime. Shapes with no source TRI take the full re-skin,
which grafts the scale bones as part of the blend.

---

## Clearance & anti-poke

`clear_armor_outside_body()` runs **last**, after every vertex op, and pushes
armor clear of the injected UBE body so the live actor morph can't punch
through. Push-out only; it never pulls cloth in. Several terms stack into one
required-clearance value per vert:

### Adaptive clearance

**Why.** A flat clearance everywhere makes loose/thick armor float off the body.
**How.** Clearance scales with how much the body actually *grows* at that vert
under runtime morphs (slider/bodygen amplitude): tight in static zones (sternum,
back, sides drop to a small base), full clearance only where the body inflates
(breast, belly, butt).

### Flex-zone standoffs

**Why.** The adaptive map keys on *morph* amplitude, but some zones barely morph
yet **flex** hard during animation, so they get shrunk to the static floor
(~0.25u) and then punch through mid-motion. Two measured cases:

- **Rear butt / upper-thigh** — leg armor hugs the butt with a razor-thin rest
  gap; at rest it's fine, but the thigh swings back on the stride and the
  gluteal fold deforms past the sliver. Low jiggle-weight, so jiggle clearance
  can't reach it either.
- **Calf / lower leg** — barely body-morphs, so it shrinks to ~0.25u, but the
  knee/calf flex every step. (Measured 0.25u at z30–35 → in-game clip.)

**How.** A flat minimum standoff over the affected band, enforced geometrically
by the nearest body vert's position (and, for the rear, its facing). It only
raises verts already below the floor, so well-fit armor is untouched, and it's
push-out only. The rear term gates on rear-facing normals; the calf term is
all-round (the calf bulges at the back and the shin extends at the front).

### thin-rim crumple (default ON)

**Why.** A THIN feature — a hem rim, a seam ridge — buckles when neighbouring
verts take *different* displacements, even sub-unit ones that every
absolute-magnitude guard in the pipeline passes. Measured: **0.41u of
differential wrecks a 2.2u rim**, while the same displacement across a panel is
invisible. The defect is the vert-to-vert *differential*, not the warp, which is
why no clearance or push cap ever caught it.

**The metric is COHERENCE COLLAPSE, not rotation or area.** Per-triangle angle
between source and output face normal; cluster the turned triangles into
connected patches; a patch is BROKEN when its area-weighted `|mean normal|`
falls from `>= COHERENCE_SRC_MIN` to `<= COHERENCE_OUT_MAX`. A legitimate refit
turns every normal in a patch *together* and keeps `|mean|` long — only a crumple
scatters them. Judging by **rotation** alone flags 53% of the pack; judging by
per-triangle **area** misses the defect entirely, because it is many small
triangles summing to one visible patch.

**The repair.** Smooth the DISPLACEMENT FIELD (`out - src`) across the patch with
its boundary held fixed, then re-apply. The patch's mean displacement is
preserved, so the garment stays exactly where the fit put it and
clearance/standoff are unchanged — only the differential that buckles a thin
feature is removed. A strip thinner than `COHERENCE_THIN` is moved **rigidly**
rather than smoothed, and gates on how far coherence *fell* rather than its
absolute value, because a rim that reorients coherently is still a defect.

`CBBE2UBE_NO_COHERENCE_REPAIR=1` is the hatch; the gates are the
`CBBE2UBE_COHERENCE_*` knobs.

### Jiggle clearance (default ON)

**Why.** HDT-SMP softbody swings breast/butt/belly *past* the rest surface at
runtime, so cloth cleared only for the static envelope still gets hit mid-bounce.
**How.** Adds clearance scaled by local jiggle-bone weight, capped small. It
loosens fit slightly in the exact zones people most want tight, which is why it
was originally opt-in; it has been ON by default since 2026-07-10. Turn it off
with `CBBE2UBE_NO_JIGGLE_CLEARANCE=1`.

### Push-field smoothing (default off)

**Why.** Each vert is pushed along its own nearest-body normal, so neighbours get
different magnitudes and the cloth turns faceted/crinkled exactly where clearance
was applied. **How.** Feather the push scalar over the mesh adjacency, floored at
the raw push so it never re-opens a poke. Off by default: in-game it raised the
inner layer of a multi-layer garment toward an unpushed outer one and collapsed
the gap between them. Re-enable once the smoothing is made gap-aware.

### Layered anti-poke floors (default off)

**Why.** Stacked garments are anti-poked independently against the same clearance
map, so where both bind (high-morph bust/butt) they converge to the same standoff
— coincident surfaces, inter-layer z-fighting, inner pokes through outer.
**How.** Rank a NIF's body-layer shapes innermost-first by median distance to the
body and give layer *i* an extra `i * EPSILON` floor (capped), so bound layers
stay separated; single-layer NIFs are unchanged. Off by default (same in-game
finding as smoothing). Median-ranking is coarse — see **Layered cloth** for the
per-vert source-order approach that handles draping layers.

---

## Skin-to-bone (STB) preservation -- the add_bone footgun

<!-- anchor: [DESIGN: Skin-to-bone (STB) preservation -- the add_bone footgun] -->


**This is the single most dangerous invariant in the mesh pipeline.**

**Why it bites.** In pynifly, `add_bone()` (and `setShapeWeights`) **resets every
existing bone's skin-to-bone transform to identity**. A shape's verts are
positioned for their bones' real bind transforms (e.g. Pelvis at Z≈−69); with an
identity STB there is no valid bind pose, so at runtime the verts skin to the
origin and the whole piece **collapses / flies**. It looks fine in every static
mesh check (geometry, weights, partitions all valid) — it only detonates when
the engine skins it.

**The rule.** Any pass that calls `add_bone` on a shape that already has weighted
bones **must**:

1. Save every existing bone's STB *before* the first `add_bone`.
2. If any existing STB can't be read (can't be restored), **bail** the graft for
   that shape rather than ship an identity-wiped real bone.
3. Restore the saved STBs *after* the last `add_bone`/`setShapeWeights`, and
   re-set the new bones' STBs too (setShapeWeights zeroes those as well).
4. Restore on *every* exit path, including early "nothing grafted" bails —
   `add_bone` may have already run.

`_match_rigid_leg_bend_to_body` is the reference implementation.
`_transfer_body_jiggle_to_fitted` once set only the *new* bones' STBs and left
the originals wiped — it collapsed the pants on the weight-0 mesh while the
weight-1 mesh (which had the jiggle bones grafted by an earlier pass, so it
skipped `add_bone`) survived. That weight-only asymmetry is the classic symptom.

**Diagnostic.** When something is runtime-only and every structural check says
"correct," measure the STBs: compute `|STB @ vert|` (each vert's distance from
its bone origin). A wiped shape shows verts sitting ~2× farther from their bone
than a working baseline; an all-identity STB set is `[0,0,0]` translations.

### Zero-weight bones desync the partition palette

A sibling skinning footgun. A bone that's `add_bone`'d but left carrying **no
weight** (e.g. the genital/anus bones the re-skin propagates onto armor that doesn't
use them) stays in the shape's bone **list**, while the GPU skin-partition **palette**
is built from weighted bones only. The per-vertex bone indices reference the longer
list and run *past* the shorter palette → out-of-bounds read on equip → CTD. So prune
zero-weight bones **before** `add_bone`, keeping list == palette. Authored SMP
colliders are the exception: their skin is already self-contained and consistent, so
stripping bones from *them* is what desyncs it.

---

## Weight-pair (_0/_1) consistency

**Why.** The engine interpolates the `_0` and `_1` weight meshes vertex-by-vertex
for body weight, so they must stay in lockstep: same shape set, same vertex
counts, same vertex order, and consistent skinning. Any per-shape decision that
is *weight-sensitive* can desync them.

**Two failure modes seen:**

- **Shape-set mismatch → explosion.** The exposed-body-skin test is geometric and
  weight-sensitive: a baked bare-leg skin slice qualified at `_1` but not `_0`, so
  `_1` body-swapped (dropped the slice, injected the UBE body) while `_0` copied
  (kept it). Different shape sets → the morph interpolates unrelated meshes →
  verts fly. **Fix:** decide on the *pair* — union the exposed-skin decision over
  `_0` and `_1` so both take the same path.
- **STB desync → collapse.** See the STB section; the same pass wiped one weight
  and not the other.

**The rule.** Any shape drop / inject / classify decision must be reconciled
across the `_0`/`_1` pair, or verified weight-invariant.

---

## Phase-2 body-swap

**Why.** Some armor bakes a slice of the nude body (open-cleavage skin, bare
lower legs) or ships a full inline body. That geometry can't morph or connect to
the neck on its own and must *be* the body. **How.** Drop the source body/skin
shapes and inject the full UBE `BaseShape` (+ `VirtualBody`), then re-fit the
armor around it. Exposed-skin slices are detected by geometric coincidence with
the CBBE body surface (a shape whose verts overwhelmingly sit on the body *is*
the body). This detection is weight-sensitive — see weight-pair consistency.

---

## Layered cloth

**Why.** A multi-layer outfit (corset over shirt, skirt over leggings) is
authored with a specific radial stacking. The per-shape warp pushes every inner
layer to about the same standoff off the bigger UBE body, collapsing that order,
so inner layers poke through outer ones.

**How (what exists).** `_separate_abdomen_layered_cloth_depth` restores the
*source* stacking: it classifies which shape is above which using the source
body frame (immune to the warp), binds each vert to its source-above/below
partners, and lifts inner→outer so leapfrogging is structurally impossible.

**Known limitation.** It measures the gap in the *source* frame, so it enforces
the source *order* but not extra separation, and it can't see divergence the UBE
fit introduces in the *output* frame. On body-swap armor with no inline source
body, the classification frame is unreliable (different reference bodies
disagree on which layer is outer), so an output-frame "tuck the under-layer in"
pass was prototyped and **pulled** — a wrong over/under call would push the outer
layer inward and worsen clipping. Fixing this needs a reliable per-vert
layer-order signal on the body-swap path.

**Layer-coherent jiggle `[DESIGN: Layer-coherent jiggle]` (opt-in, default off).**
The above fixes the *static* stacking; motion is a separate problem. Jiggle is
proximity-grafted, so the INNER cloth layer (closer to the body) gets MORE butt/belly
jiggle than the OUTER layer over it, out-swings it, and punches through during
motion. `_sync_abdomen_layered_cloth_weights` (`CBBE2UBE_ABDO_JIGGLE_SYNC`) picks the
OUTERMOST waist layer as authority and rewrites each inner layer's nearby verts to
the authority's *jiggle* weights only — the receiver keeps its own base (thigh/pelvis)
skin, rescaled to conserve mass, so leg deformation is untouched (a full-weight
replace, tried first, moved the inner-thigh skin and clipped). Sibling of the chest
`_sync_chest_layered_cloth_weights`. Default off pending cross-armor validation; on
a layered leather cuirass it was correct but unneeded once the inner-thigh clip proved to be a
pre-existing pose limit. No `add_bone` beyond copying the authority's already-valid
bones+xforms, so the STB footgun does not apply.

---

## Leg-plate bend / butt-jiggle conform

**Why.** A rigid leg plate skinned mostly to Thigh/Calf doesn't track the UBE
body's finer leg deformation, so it lags or clips as the leg bends. **How.**
Graft the UBE body's detail leg bones (front/rear thigh, rear calf) and a small,
capped share of its butt jiggle onto the plate, anchored so the grafted bones'
bind transforms match the body's. The same matched-and-capped graft mirrors onto
the chest (breast-jiggle bones anchored to Spine2, self-gating to the front where
the body carries breast weight). The cap matters most there: breast jiggle is
~10× the butt's, so a full match would make a metal cuirass bounce like flesh —
the cap keeps it mostly rigid (partial follow = less poke, not a soft chest). Strength tapers from full at the knee to
partial in the thigh, so the larger-radius upper plate isn't over-rotated into a
rest-pose bulge. It never moves a vert (rest pose identical) and never adds a
jiggle bone (the plate stays rigid). The grafted bone's skin-to-bone transform is
re-anchored to the *armor's own* Thigh/Calf bind, not copied from the body —
copying the body's absolute STB onto armor with a different bind convention tore
verts apart (an in-game explosion). All of this adds bones — see the STB footgun.

**Fitted (non-rigid) cloth** that hugs a jiggling region but carries none of its
own jiggle stays rigid while the body bounces through it (the "clip when moving"
class). Two sibling passes: `_conform_fitted_to_body` blends a hugging garment's
*existing* weights toward the body's where it already jiggles;
`_transfer_body_jiggle_to_fitted` grafts a capped share of the body's jiggle onto
one that lacks it. Both gate on hugging + leg/jiggle-dominant geometry, and both add
bones (STB footgun applies).

---

## Bust collider split

**Why.** Some authors reuse the bust garment itself as the piece's per-triangle
SMP collider (their skirt/tassel chains rest on it). A shape that IS its own
collider can never carry jiggle: grafting breast motion onto it closes a
feedback loop — cloth moves collider, collider pushes cloth — and in game the
breasts tore off the body (the revert that kept the torso graft off for a
release). The well-behaved siblings in the same source family solve this by
hand: a SEPARATE hidden collider shape carries the support role, leaving the
bust garment free to follow the breast. Diffing a working sibling against the
failing piece is what found this, after three theory-driven fixes failed.

**How.** Two order-critical passes at both pipeline sites. Pass 1, before
`_finalize_hdt_physics`: clone the garment IN PLACE as `<name>Col` — hidden
(flags 15), textureless, keeping the garment's CURRENT rigid weights so the
resting chains see identical support. In place matters: rebuilding a NIF from
its shapes drops ALL extra data (BODYTRI + the physics link; in game "ignores
morphs, body reverts to its _0 shape"). The pass snapshots root- AND
shape-level extra data (BODYTRI lives on its carrier shape) and byte-restores
the file if anything is lost. Pass 2, after the finalize (which overwrites the
on-disk XML with the authored copy — an earlier rewrite is silently undone)
and before the jiggle graft (which reads that XML): repoint each
`per-triangle-shape` decl at the clone, gating the split/morph/physics
invariants together. The stock torso graft then reaches the garment with no
bypass, because it is no longer a collider.

**Detection is measured, never named.** A candidate is a RENDERED per-triangle
collider (textured, not Hidden) covering the bust band whose breast FOLLOW
RATIO against the body underneath is below 0.5 — weight, not bone presence: a
garment can carry all six breast bones at 0.15 of the body's drive and still
fail in game. Names with XML roles beyond the per-triangle decl (constraints,
pairs) are skipped as unvalidated structure. Bodies, hidden helpers and
already-split pieces are excluded by construction.

**Status.** In-game validated on the motivating vanilla cuirass (production
output reproduces the hand-built artifact: follow 0.660 vs anchor 0.643, same
per-bone distribution as the body). Census over the shipped pack: 33 pieces in
the split class. `CBBE2UBE_NO_BUST_COLLIDER_SPLIT=1` disables the split;
`CBBE2UBE_TORSO_JIGGLE=0` disables the whole fix (split included — a split
with no graft is inert output churn).

---

## HDT-SMP physics-cloth preservation

**Why.** Authored SMP cloth (per-vertex softbody) and SMP colliders (per-triangle)
carry a self-contained, already-consistent skin that the runtime physics reads
directly. The converter's skin/jiggle passes (re-skin, scale-bone graft, jiggle
transfer, leg conform) would rewrite that skin — adding bones or stripping
weights — and desync the partition/bone palette the SMP engine reads, causing an
out-of-bounds read and an **equip CTD**, or a collapsing/drifting sim.

**The rule.** Every skin-modifying pass must **skip** authored SMP shapes —
both softbody and collider. Detect them structurally (physics extradata / the
softbody/collider shape sets), not just by name, because bone-driven SMP cloth
uses ordinary skeleton bones and won't trip a name check.

**Globally-configured cloth.** Some draping cloth (robes, cloaks) is driven by a
*runtime-global* HDT-SMP config with no per-mesh XML, so there's nothing structural
to detect. These are skipped by garment-name keyword (robe/cloak/cape/gown/…) in the
conform/graft passes as a fallback — grafting UBE scale bones onto them crashed the
SMP update on equip (skin-data OOB, the "robes" CTD). "skirt" is deliberately
excluded: metal tassets are rigid plates that legitimately want the conform.

### Custom physics-bone chains

When a NIF is rebuilt, pynifly re-adds each skinned bone flat under the root with an
identity transform. Standard skeleton bones are fine — the game resolves their real
position by name from the actor skeleton. But armor-specific physics bones (a skirt's
bone chain, cape/cloak/tail bones) aren't in the actor skeleton, so a flat identity
node pins their verts to the world origin and the skirt collapses through the floor.
Fix: recreate those bones' nodes with their *source* local transforms and parent
links, anchored to the standard bone they hang off. `_is_skeleton_bone` tells the two
apart by prefix/keyword — a leading `_` marks an armor-specific chain even when the
name contains a body-part keyword.

### Chain rest-pose lift (default ON)

**Why.** Recreating a chain at its *source* bind is right for the rig and wrong
for the body: the bones keep CBBE-era positions while the body grows to UBE, so
on a fuller body a skirt's chain bones end up **inside** it. HDT-SMP resolves an
equilibrium — `generic-constraint` pulls each bone back toward its rest pose
while collision pushes out — so an inside rest pose drags the cloth in every
frame and it settles part-way inside. It looks identical standing still and
moving, which is why it read as neither a follow problem nor a clearance one,
and why three collider passes each helped and none finished: they add push
against a pull nothing addressed.

Measured on the test cuirass: 2 of 63 chain bones inside the **built** body, and
8 of 63 inside it under the player's RaceMenu preset, mean 0.900u / max 2.000u.

**How.** Translate each affected chain's **ROOT** outward along the body normal
until no bone of that chain rests inside. Roots only — displacing a root moves
the chain rigidly (worst inter-bone change 0.000000u), while warping bones
individually changes rest lengths and is how a chain explodes.

**The margin is the body's own outward morph amplitude**, capped. The converter
never sees the player's preset, and 6 of the 8 penetrations exist only under it,
so the pass clears the room the body still has to grow. Adaptive clearance takes
20% of that amplitude for garment verts because those verts morph too; a chain
bone has no morph channel, so it takes all of it. The cap is the real engagement
rule: uncapped, the belly's amplitude (to 8.7u) recruited FRONT chains measured
+3.63u clear of the skin.

**Cost.** The lift is rigid, so the free-hanging lower chain moves out too —
0.5u on the test piece. That is the counter-metric to watch, and the reason this
is the first toggle to untick if a skirt looks held off the hips.

**A caveat before shifting roots differentially.** "A root shift is rigid" holds
*within* a chain. It does not follow that chains are independent: on the test
piece 74 of 130 `generic-constraint`s are cross-chain, stitching ten skirt
panels into a hoop, and lifting six of them by three different amounts changed
28 inter-panel rest distances by up to 1.651u. That is safe **there** for a
reason worth re-checking elsewhere — every cross-chain constraint uses
`frameInLerp`, so FSMP derives its rest frame from the bones at load, while
every explicit-`frameInA` constraint is intra-chain and those change 0.000000u.
Read the emitted XML for that pairing before assuming it.

Full history, including the two candidate fixes the numbers killed first, is in
`docs/worklog/BUTT_CLIP_CHAIN_REST.md` — a working note, so it lives on the
`testing` branch only (see "Where the hard-won detail lives" below).

---

## BODYTRI / body-morph generation

**Why.** RaceMenu / BodyMorph applies body sliders to a shape via a `.tri` file
named in the NIF's `BODYTRI` extra-data. The tri must match the NIF's shapes and
vertex layout, and it must exist.

**How.** The converter auto-generates a **per-armor** `.tri` from the CBBE source
+ UBE body slider (OSD) data and writes it next to the mesh, so each converted
mod is self-contained. The generic body tri (`femalebody_tangent.tri`) is only a
legacy fallback, written when no armor-relative path can be derived.

The BODYTRI goes on a **single carrier** shape, not every shape: NioOverride reads
only the first BODYTRI in a NIF, so tagging them all shifts the carrier to whatever
textured shape iterates first and the real cloth silently stops morphing. Rigid
single-bone pieces still morph — the M6 re-skin re-weights them to multiple body
bones, so they follow via ordinary bone-driven skinning rather than BodyMorph.

**Shape flags for morphing.** NioOverride silently refuses to morph an alpha-having
shape whose NiAVObject flags lack **bit 19** (`0x80000`, the alpha-sorter): the
NiAlphaProperty alone isn't enough — the renderer must also be told to sort the shape
into the transparent pass, and without it the shape sits in an inconsistent state that
BodyMorph skips. Hand-built UBE armor sets flags = `0x8000E` (bits 1/2/3 "SelectiveUpdate"
+ bit 19) on nearly every shape, so the converter uses `0x8000E` uniformly; on opaque
shapes bit 19 is just ignored by the renderer, so it costs nothing. (An earlier
split-by-alpha-state version left some alpha-false cloth at `0xE` and it didn't morph
in-game.)

**Gotcha (test harness).** The BODYTRI path written into the NIF is derived by
finding `meshes` in the *destination* path. Converting to a scratch folder with
no `meshes` segment silently produces the fallback body-tri — an artifact of the
test setup, not a real conversion bug.

---

## Delivery: SkyPatcher-only

**Why.** Overriding vanilla/master ARMO records to point at UBE armatures caused
load-order and value/weight conflicts. **How.** SkyPatcher `armorAddonsToAdd`
INI links are the sole delivery path: for each ARMO that references a converted
armature, a link (ARMO → minted UBE ARMA) is recorded in a `.skypatcher.json`
sidecar; no ESP ARMO override is emitted. The legacy ARMO-override machinery has
been removed. (The winner-scan coverage passes still emit ARMO overrides, but
their output is folded into the Combined family rather than shipped as separate
plugins — see "Unified coverage" below.)

### The sidecar FormID invariant

**A sidecar records the FULL, POST-PRUNE FormID of each minted armature.** The merge
resolves every link by exact FormID (`merged_rec_by_key[(patch, fid)]`), so a sidecar
holding anything else resolves to nothing.

The trap is that `prune_unused_masters` drops unreferenced masters and remaps every
record's **master byte in place**, so a FormID captured as an `int` before it goes
stale. Hold the **Record object** and read `rec.formid` after the save. Emit the INI
from that same object — the INI masks to 24 bits (SkyPatcher names the plugin
separately), so it stays correct across the remap and therefore **cannot detect the
drift**. One source, or they diverge silently.

The failure mode is total and quiet: zero links, the merge deletes the previous INI as
stale (correctly — it points at reassigned FormIDs), and nothing is delivered, while
the ESL flag, split, master count and ARMA total all report normally. Any test covering
code downstream of prune must assert a master was **actually dropped**
(`len(saved.header.masters) < len(masters)`), or prune is a no-op and a stale int
passes by accident.

### Coverage patches size themselves to the ESL cap

`_partition_patches_for_esl` bin-packs whole patches and cannot split one, so a single
coverage patch minting more than 2048 own records used to force its merged piece down
to a full ESP. The coverage generators therefore emit **numbered pieces of their own**
(`_emit_coverage_pieces`), each within the cap.

Chunking is **by target (ARMO), never by armature**, so an ARMO's whole add-set stays
in one piece and yields exactly **one** `filterByArmors` line. Whether SkyPatcher
accumulates duplicate lines for one armor or takes the last is unverified, and this is
the only delivery path. The cost is that an armature shared across a chunk boundary is
minted twice — measured at ~2%.

---

## Effect-shader glow overlays

Some armor (e.g. Daedric) carries additive glow decals as separate shapes with a
`BSEffectShaderProperty`, riding on top of a solid plate. Three things break if the
converter treats them like normal cloth:

- **Equip/render CTD.** The UBE body-blend re-skin re-skins the decal to body bones it
  never had (and scale bones), and a skinned `BSEffectShaderProperty` CTDs the engine
  (`call [rax+0x28]`, garbage pointer). Fix: glow shapes keep their **source skin
  verbatim** (skeleton bones only, matching the proven-good vanilla decal), ignoring the
  re-skin. Dropping scale bones alone wasn't enough — the re-skin's other body bones had
  to go too.
- **Clipping.** The decal must move exactly with the plate it sits on, so it's made to
  **ride** the plate (inherit its post-fit vertex displacement) instead of being fit
  independently and drifting off it.
- **Lost glow.** The glow's animation-controller chain (and buffer/vertex-fade) must be
  transplanted onto the copied shape, or the effect renders static or white.

---

## Where the hard-won detail lives

Running logs that complement this doc. These are working notes rather than
product documentation, so they live on the `testing` branch only — `main`
carries the tool itself. Code comments citing them by shorthand (e.g.
"CLIPPING_LOG C1", "ROBUSTNESS_AUDIT L3") point at these:

- `CLIPPING_LOG.md` — in-game clipping/crash finds and their diagnoses.
- `ROBUSTNESS_AUDIT_*.md`, `CONVERTER_AUDIT_*.md` — point-in-time audits.
- `DESIGN_P*.md`, `DESIGN_PROPOSALS.md` — design-only proposals, not built.
- `CHANGES_*.md` — per-investigation change notes.
- `LOCAL_ASSET_SAMPLES.md` — maps the synthetic mod/asset names used in tracked
  comments and test fixtures back to the real assets they stand in for. Tracked
  content is kept mod-agnostic; this preserves the provenance of a measurement or
  a regression case without publishing it. **Add a row in the same change as any
  new substitution.**

These are gitignored, and `tests/test_public_repo_hygiene.py` asserts they stay
untracked — the repo is public and every one of them names specific mods.

---

## Pose-driven clipping: what moves, what fixes it, and what does not

Everything the converter did about clipping was solved at BIND pose — an A-pose nobody
stands in. Measuring under a pose set (`scripts/analysis/multipose_clip_test.py`) showed a class
of failure no bind-pose metric can see: armour that is clean at rest loses coverage the
moment the actor moves. On a rigid cuirass, 11.0% of covered breast is exposed by a
spine twist; on a skirted piece, 14.7% of covered butt by a sprint; on a vanilla
cuirass, 83.7% of covered thigh by a crouch.

### The measure is a REGRESSION, not an exposure level

Body verts COVERED at bind and EXPOSED under a pose, as a fraction of the covered set.
Raw exposure cannot be compared across garments — a bikini is 90% exposed by design and
a robe 0%, neither of which is a defect. Each garment is its own baseline. The same
principle separates a defect from a neckline: exposure is only a defect when the
garment is right there (within ~2u) AND well inside its own boundary, which is why
`classify_exposure` splits poke / neckline / uncovered.

### Two levers, and they are not equal

**Clearance** (push the garment out) works but pays in volume. A uniform push took
breast exposure 11.0% -> 2.5% — while moving every vertex, which reads as baggy. A
targeted, exposure-driven demand (`research/pose_clearance.py` — moved out of
`src/` because it does not ship) reaches 3.5% while moving
0.9-2.3% of the garment, and is default OFF pending calibration.

**Deformation matching** (give the garment the body's weights so the two deform
together) is strictly better and costs NOTHING in volume — the bind shape is
byte-identical, only the motion changes:

| region / pose | authored | clearance, best | deformation matching |
|---|---|---|---|
| thigh / crouch | 83.7% | (cannot reach it) | **5.4%** |
| breast / spine twist | 11.0% | 3.5% | **0.1%** |
| butt / sprint | 14.7% | 8.0% | **2.8%** |

The converter already has this as the M6 body-blend reskin. It is bounded two ways:
transferring weights onto authored physics cloth replaces its chain weights and the
skirt stops swinging, and the reskin's K-NN blend has a history of equip fly/spike
instability. Both call sites are therefore gated on `not _shape_has_hdt_smp_rigging`.

**Superseded in practice by the full-vector weight match (default ON).** The
per-family motion matches that followed this section each manage one bone family
and rescale the rest, so each buys a pose by selling another. The full-vector
match copies the covered body's whole weight row on hugging verts, so there is
nothing left to pay with: breast_side under a swing 12.81% → 3.52% and front
bust under a sprint 50.91% → 3.12%, with belly/butt/thigh unchanged and **zero**
vertex movement. Because it manages every shared bone, nothing may run after it —
a test pins the family-match order (leg → spine → arm → spine-twist →
full-vector). It is still a *skin* pass, so it inherits every exclusion above and
cannot reach simulated cloth; the chain rest-pose lift is what reaches that.

### Why the reskin does not run on most armour

Two gates, in sequence. A source that bundles an inline body routes to phase 2, so
phase 1's reskin is never reached. Phase 2's gate then ends in `not _is_morph_tri` —
excluding any shape carrying a source BodySlide morph TRI, which in a BodySlide-built
pack is nearly everything. `CBBE2UBE_RESKIN_KEEP=1` overrides it.

The stated reasons are a morph desync (the TRI is keyed to the source skin) and the
equip-fly instability. On the piece measured, the desync half is NOT reproduced: the
output TRI is regenerated post-reskin (it differs between reskin off and on), and
morph-follow under a full breast slider is identical either way (4.5% -> 12.1% in both).
The instability half can only be settled in game.

### A caveat about the numbers above

They come from `scripts/convert_one_armor.py`, which does not reproduce the auto
pipeline exactly — see METRICS.md. The divergence is small (mean 0.006u) and the
effects here are large, but a single-piece measurement is not a pack guarantee.

### What no offline metric here can see

Runtime physics (SMP cloth goes where the simulation puts it), BodyMorph/OBody
inflation beyond the fitted body, and equip-time instability. A full breast slider
takes exposure 4.5% -> 12.1% on a piece whose pose behaviour is clean — the morph path
is a separate, unexamined class, and on that piece it is the larger one.
