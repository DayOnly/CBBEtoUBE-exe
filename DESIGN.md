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
     tier-0 base (the New-Leather tier fix stands).
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
`[DESIGN: Leg-plate bend / butt-jiggle conform]` and the `newleather-working-recipe`
memory.

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

### Jiggle clearance (experimental, default off)

**Why.** HDT-SMP softbody swings breast/butt/belly *past* the rest surface at
runtime, so cloth cleared only for the static envelope still gets hit mid-bounce.
**How.** Adds clearance scaled by local jiggle-bone weight, capped small. Off by
default because it loosens fit in the exact zones people most want tight.

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
New Leather Armor it was correct but unneeded once the inner-thigh clip proved to be a
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
been removed. (The two fallback coverage ESPs are a separate, opt-in path and do
still emit ARMO overrides.)

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
