# Changelog

## Unreleased

### Fixed — converting the same mod twice now produces the same meshes

Two identical runs disagreed on 29 of 84 meshes, by up to 3.4 units. Nothing was
wrong with either result; the converter simply had no reason to pick the same one
twice. That made measuring any change impossible — a fix worth half a unit could
not be told apart from the noise — and it meant a rebuild of your armour could
differ from the one you approved.

The cause was the order the converter walked its own internal name lists. That
order is deliberately unpredictable in Python, it changes every time the program
starts, and here it decided the order things were written into the file. Two
sources of it are now closed: the order is fixed for the whole program, and the
handful of places where it reached the output are sorted.

**Underneath sat a worse defect that this uncovered.** Every armour is built in
two weight variants, and three internal caches stored a body measurement under a
key that could not tell the two apart. Whichever variant a worker happened to
build first pinned that measurement for every piece it built afterwards — so the
low-weight version of an outfit was routinely fitted against the *high-weight*
body's shape. It affected 14 of 84 meshes on the test mod, every one of them the
low-weight variant, and moved a fitted torso by as much as 2.2 units. Slim
characters were getting armour cleared for a body they do not have.

Converting the same mod twice now gives byte-identical results, and the same is
true across the 16 parallel workers a real run uses.

### Fixed — a single vertex bending the wrong way

Reported in game on a leather panel above and below its belts: one vertex pulled
against the surface around it, poking through the layer over it.

A vertex can be attached to at most four bones. Making an outfit follow the body
fills nearly every vertex to that limit, and where the result is a near-tie for
the fourth place, neighbouring vertices can end up keeping *different* bones. The
surface then bends at the waist while one vertex in the middle of it bends at the
chest.

Weighting is now held to the smoothness the original author gave it, and eased
back toward its neighbours only where ours came out rougher than theirs. It is
measured against the author rather than against perfect smoothness on purpose:
flattening everything would erase the panel edges and seams the author put there
deliberately. On the reported outfit this fixed 802 of 830 rough vertices, and the
belts, buckles and metal buttons — already smooth — were left completely
untouched. Nothing moves; only weighting changes.

Two likelier explanations were measured and ruled out first: the way vertices are
paired to the body is not at fault here, and no vertex was shipping underweighted.

### Added — four more settings that could not be switched on

Same class as the two below: finished work the settings file had no entry for, so
no run could reach it however it was described elsewhere.

- **Cap how far the upper-back allowance may push.** The allowance that keeps the
  upper back covered raises cloth in one step, and where the garment already sat
  close it overshot — the measured piece stood 2.12 units off the back where its
  author put about half that. Capping it brought the standoff to 1.16 while
  showing *less* skin than before, not more. Roughly the worst tenth of pieces.
- **Match a top's twist-follow to the body**, and **let rigid bust plates ride the
  breast chain.** Both are experiments with real measurements behind them and no
  in-game verdict, because until now no run could turn them on to get one. Both
  ship off.
- **Keep weighting as smooth as the author made it** — the fix above, on by
  default.

### Fixed — an empty setting could stop the run

One numeric setting crashed the converter outright if it was set to an empty
value rather than left alone. It has a row in the settings window, so this was
reachable by hand-editing a recipe. It now falls back to its default like every
other setting.

### Fixed — physics cloth no longer falls away from the body, and the collider that caused it is kept

Reported in game as legs sinking away from the body and falling forever, on
vanilla iron armour. The standing workaround was to switch the added buttock
collider off, which also gave up the buttock clipping it exists to fix. Both are
kept now.

**The cause was the collider's bone list, not its shape.** The collider copies the
body's skinning wholesale, and that drags in the body's jiggle bones. The piece's
own physics file never declares those bones, so half the collider's influences had
no counterpart in the system it was registered into. Every collider the *author*
ships is weighted only to bones that file declares — that is the rule the
generated one broke. It now hands each undeclared bone's weight to the nearest
declared parent on the actual skeleton, which leaves the collider in exactly the
same place (the geometry comes out identical to the vertex) while giving the
simulation something it can resolve. It fires only where the rule is actually
broken: the piece this work was originally judged on redirects nothing, because
its file already declares those bones.

Found by removing the collider's *declaration* while leaving the shape in the
mesh. The cloth settled, which put the fault in the registration rather than the
geometry — no amount of measuring the mesh would have shown that.

**A second, separate defect: every generated collider shipped with broken binds.**
Adding a bone to a shape silently resets the bind transforms already on it, so
building them one at a time left only the last bone correct — **174 of 176 shipped
colliders**, sitting a median 72 units from the body at runtime against 0.02u for
the authored ones. Now built the way the rest of the converter does it. Real and
worth fixing on its own, but it was *not* what made the cloth fall; both had to be
fixed.

**The collider also sat too far off the skin.** Its 0.6u standoff had been tuned
by eye against a collider that was misplaced at runtime the whole time, so that
tuning measured nothing. It is now taken from where each piece's own rear cloth
actually rests, capped so it can only ever come down, and floored so it never sits
inside the skin. On a close-fitting piece that reported an inflated rear it drops
to 0; on a flared skirt it is unchanged.

### Added — two settings that were previously impossible to switch on

Both were finished, verified in game, and unreachable: the converter reads its
settings file, and a setting with no entry there cannot be turned on however it is
described elsewhere. A full reconvert would have quietly run without them.

- **Place each physics chain's anchor separately.** Anchors are placed in one pass
  for a whole outfit, and that pass gave up entirely if any one chain hung from the
  upper body. An outfit mixing a hip chain with a small chest chain — common — got
  nothing placed, and every chain landed about 69 units low, at floor level.
  Measured on vanilla iron armour: 38 chain nodes, most below the ground.
- **Give the fitted parts of physics outfits body clearance.** Clearance is skipped
  for anything carrying physics rigging, because moving simulated cloth fights the
  simulation — but one piece often holds both a simulated skirt and an ordinary
  fitted top, and the whole piece was skipped on account of the skirt. Across the
  pack, **392 of 482 skipped pieces are mixed like this, 57% of their vertices
  carry no physics weight at all, and 198 pieces have chest cloth that no clearance
  could reach.** Only vertices with no physics weight are moved; every simulated
  vertex is put back exactly where the simulation expects it.

The anti-poke bust target is adjustable now as well. Raising it loosens the bust
on most pieces, so it is left at its existing value.

### Fixed — bust layers no longer swing through each other, and a sheer layer sits on the skin again

Two defects on the chest, both reported in game, both now default behaviour.

**The layers moved independently.** A garment's weights are matched to the body's
so it travels with it — right for arms and legs, wrong for the bust, where an
author deliberately gives an inner layer *more* breast follow than the body so it
hugs. Every layer was converging on the body's own share instead, so an inner
layer out-travelled the one over it and swung out through it whenever the breasts
moved. Measured on one robe: the author's 0.681 / 0.465 / 0.463 shipped as
0.355 / 0.229 / 0.083. The first write is innocent — two *post-write* weight
passes took them. Those passes now refuse to lower a row's breast share, which is
the rule one of them already stated for its own bone family. Restores
0.676 / 0.519 / 0.481 and **moves no vertex at all**, so every bind-pose fit
stays exactly as it was. Verified in game under motion, the only place it shows.

**A skin-tight layer stood off the body.** A sheer, alpha-blended bodysuit its
author holds a median 0.121u off the skin was shipping at 0.79u — far enough that
it reads as a dark shell over the chest instead of a tint on it. The chest
conform carries its own clearance pair, separate from the anti-poke target
everyone reaches for and unreachable from it, and that pair is what pins the
layer there; lowered from 0.3/0.9 to 0.12/0.3. The measured per-slider morph
charge and the anti-poke's own bust target are unchanged, so the headroom that
protects against poke-through is still there.

Both were gated per region against the previously shipped mesh across seven
pieces, counting garment vertices driven *inside* the body. No region worse on
any piece; most better — one robe's shoulder 96 → 48, its arms 60 → 44, its bust
11 → 2. All three settings are on the Armor tab and can be turned back off.

### Fixed — sub-millimetre detail on metal fittings was being blown up

Reported on a belt's buckles. The fit stretched authored edges of 0.02–0.04u — a
tight seam, the rim of a stud — to as much as **0.41u, an 18× blow-up**, which
reads as a spike or a tear on a small plate. The seam weld misses these because it
welds *coincident* verts, and 0.023u is far above its tolerance while still being
detail that must move as one piece.

Such an edge may now stretch only in proportion to the garment's own growth. The
correction moves both ends toward each other by equal and opposite amounts, so
every corrected edge keeps its midpoint and a fitting cannot be relocated — only
un-stretched. Worst blow-up on the reported piece **+0.391u → +0.087u**, touching
23 vertices of 2938 and nothing else on the garment.

Found only after the edge-distortion metric was checked for contamination: 1% of
that shape's edges were inflating its mean score by 75%. See `docs/METRICS.md`.

Opt-in as `CBBE2UBE_SHORT_EDGE_CAP` pending a pack-wide verdict.

### Changed — a layer can follow the surface point it rests on

`Ride layers on reference` moved each layer by the inverse-distance blend of its 8
nearest reference *vertices*, so a fitting followed its neighbourhood's average
motion rather than the motion of the spot it actually sits on. Where the surface
beneath moves unevenly, those differ, and the gap drifts.

It can now use the barycentric correspondence instead — the exact surface point,
taken from the author's geometry and replayed on the output. Unlike the k=1 ride
this does not reintroduce spikes, because a point on a triangle moves continuously
where a nearest-vertex snap jumps: spikiness **12.8 → 11.3**, and every fitting on
the reported piece moved toward its authored depth (the top plate 0.170 → 0.139
against an authored 0.132).

Opt-in as `CBBE2UBE_LAYER_RIDE_BARY`. It does not hold the authored distance
*exactly*, and `docs/PIPELINE.md` records why that is not reachable from here.

### Fixed — belts and straps came through crumpled

Reported in game as belts "visibly distorting". Edge length is what separates the
two things that look similar: a belt wrapped round a wider body **bends**, and
bending keeps every edge the length the author drew, while **stretching** does
not. On the reported piece the strap's edges ran from **0.52× to 1.51×** the
author's, a mean deviation of 0.219 against 0.060 for the chest plate on the same
garment, with 78% of its edges off by more than 5%.

The repair gives every edge the length its **neighbourhood agrees on**, so the
garment's overall growth survives untouched — that strap's median edge is 1.025,
and a belt on a wider waist genuinely should be slightly longer. Only the spread
is removed: deviation 0.219 → 0.128, with arm and bust follow unchanged and the
worst inter-shape clip down 65%.

Three refusals keep it a repair rather than a resurfacing: a shape must be
measurably crumpled to qualify (16.2% of shapes pack-wide), a shape whose median
edge is more than 1.5× the author's is **mis-scaled rather than crumpled** and is
left alone, and small fittings — studs, buckles, rivets — are excluded, because a
cluster of separate objects has a scale that legitimately varies between them.

Opt-in as `CBBE2UBE_STRAP_SCALE_UNIFORM` pending an in-game verdict.

**Not a regression:** 1.2 measures 0.224 on the same strap. It has always looked
like this.

### Changed — the options you were actually being judged on are now the defaults

Three fit options shipped **opt-in and off** while every in-game verdict on this
project was given on a build that had them forced **on**. Production output was
therefore not the output anyone had looked at: on one five-layer test piece the
difference reached **1.38u**.

Each was re-decided by measurement rather than by restoring the status quo, and
they did not all come out the same way.

**Reconcile stacked layers — now ON.** Verified in game before the default was
set: back, sleeves and bust all confirmed good on the reported piece, with arm
follow identical to this option off and bust follow better than off. Off-switch
`CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD`.

**Cap isolated warp fliers — now ON.** Judged on a quantity it does not optimise:
it caps a vertex's deviation from its neighbours, so it was scored on the
**absolute** irregularity of the final surface, for the vertices it actually
touched, against the author's own value for those vertices. It acts hardest
exactly where the defect is worst — on a steel cuirass the skirt's worst spike
fell from 10.34u to 6.44u, with four more shapes improved. Where it does not
help it costs about 0.03u. A 3.9u spike is a visible broken vertex; 0.03u is not.
Off-switch `CBBE2UBE_NO_WARP_DELTA_OUTLIER`.

**Cap the warp push at the garment's own shell — stays OFF.** Measured the same
way and it did not earn its cost: zero vertices touched on every shape of that
cuirass carrying a large spike, marginal gains where it does fire, two shapes
regressed — and it alone accounted for the 1.38u geometry change above. Still
available as `CBBE2UBE_WARP_PUSH_SHELL_CAP`.

**A reconvert is required** for any of this to reach an existing pack.

### Fixed — chain-driven skirts (and vests, robes, belts) stretched to the floor

Reported after 1.3-alpha as skirts looking "stretched". The physics chain a
garment hangs from was being written **detached and at the character's origin**,
so every vertex weighted to it was dragged down between the feet.

Measured on a chain-driven skirt, 1.2 → 1.3-alpha → fixed:

| chain bone | 1.2 | 1.3-alpha | fixed |
|---|---|---|---|
| root | z 79.80 | z 11.57 | z 80.48 |
| next | z 84.01 | z **0.00** | z 84.69 |
| next | z 85.75 | z **0.00** | z 86.44 |

7,379 of that shape's 9,290 vertices hang off those bones.

The cause was a deleted line. `_precreate_custom_bone_chains` used to recreate a
chain's anchor when it was missing; that was removed along with a genuinely
unfixable case (an anchor that already exists cannot be corrected in place).
But the chain writer can only attach a bone once its **parent** exists — so with
the anchor gone the first bone never qualifies, the loop gives up, and the skin
installer then creates every chain bone flat at the origin. One missing anchor
costs the entire chain, not one bone.

Censused over a real 1,886-piece output: **44 of 446 chain-carrying pieces, 594
bones, 167,920 vertices** — skirts, two vests, a Skaal torso, Creation Club
robes, belts and accessories.

The repair only ever creates an anchor that is **absent**, so it is a no-op
wherever the chain already survived: an unaffected cuirass reconverts with
0.000000 vertex movement, 0.0% of weight mass moved and no bone changes. Nothing
about geometry or skinning changes on the repaired pieces either — the fix
touches only the bone hierarchy.

Escape hatch `CBBE2UBE_NO_CHAIN_ANCHOR_RECREATE`. The regression it repairs
shipped with no flag of its own, which made it far more expensive to find; this
one is switchable so it can be A/B'd.

**A reconvert is required** for this to reach an existing pack.

### Fixed — layers of a multi-layer garment clipped through each other

Reported after 1.3-alpha on a cuirass built from five stacked pieces: the layers
began passing through one another (not through the body).

`Match armour skinning to the body it covers` copies the covered body's whole
weight row into each shape **independently**, and two layers stacked 1-2u apart
did not get the same answer — so they stopped deforming together. Measured as
the mean weight-row difference between stacked vertex pairs, 1.2 → 1.3-alpha:
0.190 → 0.309 on the two main layers, and 0.027 → 0.204 on the worst pair.
Switching the pass off restored every pair to its 1.2 value, which is what
identified it rather than merely implicating it.

The mechanism is that the pass casts a ray from each vertex along **its own**
normal, so stacked layers land on different body triangles by construction. Each
stacked group now resolves the body through one shared anchor, and it does so
**only where the layers actually overlap**, through the innermost layer it can
reach. Four of the five pairs sit at or below their 1.2 values (0.013 / 0.000 /
0.000 / 0.035), with follow intact rather than merely claimed: on the reported
piece arm follow on the outer layer is identical to this pass off (3741.2) and
bust follow on the plate is better than off (1064.2 against 1008.9). The fifth
pair is worse than 1.2 (0.259 against 0.190), and the reason is that the two
layers end up on **different bust chains**: the outer layer keeps the author's
two bones (L/R Breast01) while its neighbour receives the body's three-segment
chain (Breast01/02/03). The author had every layer of that garment on the same
two bones, so they moved together; on different chains they cannot. Sharing an
anchor does not fix that, because it is the bone set and not the pairing.

Both locality conditions were learned the hard way. A first version substituted
the shared anchor for **every** vertex of a grouped shape, so a sleeve 20u from
the chest plate borrowed a torso anchor; in game that read as bound sleeves, a
bust that stopped following and a back that tracked the torso rigidly. It scored
*better* on divergence while doing so, which is why every divergence figure here
is quoted next to a follow figure.

Groups are detected by **coverage**, not contact: a layer shadows a large share
of its neighbour, while a buckle or a trim strip only touches one along its
border. Colliders, soft-body shapes and injected body parts are kept out of a
group entirely.

Single-layer pieces are untouched — the leather cuirass whose fit was confirmed
in game reconverts byte-identical, 0.000 vertex movement and 0.0% of weight mass
moved.

**On by default**, verified in game before that default was set. Escape hatch
`CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD`. The stricter variant that also forces a
stacked group onto a shared bone basis is **off by default**: it drives the
divergence lower still, but destroys ~87% of the jiggle follow and makes
body-follow 7x worse than either baseline.

**A reconvert is required** for this to reach an existing pack.

### Changed — a fit pass can no longer fail without saying so

Most fit passes end by returning the number of vertices they changed, where 0
means "nothing qualified". Seven of them wrapped their final save in a handler
that swallowed the error and returned 0 anyway — so a **lost write looked
exactly like a clean no-op**: the pass reported success, the run report showed
nothing unusual, and the piece shipped unmodified.

That is not a hypothetical failure mode. Two defects reached players through it:
a pairing step that raised on every call while producing a byte-identical mesh,
and the chain bug fixed above.

Those seven now report through the run's normal failure channel, so a failed
save appears as a `PASS FAILED` line instead of silence. Two further handlers
the audit flagged were left alone — they already report by another route.

Nothing about the output changes: these paths only execute when a save has
already failed.

### Changed — faster: the morph-TRI lookup is no longer repeated

Seven passes ask whether a shape is covered by the source mod's own morph TRI,
and each question re-read and re-parsed the whole file. Profiled on a five-layer
outfit: twelve reads costing 14.0s, now 0.95s.

Honest about the scale of this: the saving inside that lookup is measured and
solid, but a single before/after run showed only a ~1% change in total
conversion time, with unrelated work drifting by more than that between the two
runs. Treat it as one repeated cost removed, not a promise of a faster
reconvert. Output verified unchanged.

## 1.3-alpha — 2026-08-10

**Pre-release.** Everything below is built and tested, and the headline fits are
confirmed in game, but this build has not had a full play-through. Treat it as a
testing build: keep your previous output mod so you can roll back.

**A reconvert is required** for any of this to reach an existing pack — nothing
rewrites meshes that are already built.

### Fixed — forearm and calf armour was invisible on UBE

A piece whose ONLY biped slot is 34 (forearms) or 38 (calves) got no UBE
armature from anywhere, so it equipped and rendered nothing. Two winner-scan
passes divide coverage by slot: the non-body pass skips anything with a
deforming slot, and the body pass handled only torso plus pure hands/feet,
leaving the rest to the per-source builder. Unified coverage then made the
winner scan the sole generator and stopped merging the per-source patches — so
the fallback that arrangement depended on no longer ran, and forearms and calves
fell between the two.

Measured on a real pack: **all 51 slot-34-only and all 7 slot-38-only armours**
had no link. Reported as "the arms are equippable but invisible".

Admitting them needed no new safety gate — minting already requires a
DefaultRace armature and a converted mesh, which is what keeps a non-body mesh
from being handed UBE body races. Checked against the worst case in the pack, a
modder's tower shield parked on the calves slot: still correctly excluded.

### Changed — four fit/physics options are now ON by default

All four were built default-OFF pending an in-game verdict, and all four have
now had one. **A reconvert is required for any of this to reach an existing
pack** — nothing rewrites meshes that are already built.

* **Match armour skinning to the body it covers.** A CBBE-authored garment
  carries CBBE weighting on a UBE body, so the body slides out from under it as
  you move. This copies the covered body's whole weight vector on the vertices
  that hug it. Bust exposure in motion 50.9% → 3.1% on the test piece; it moves
  no vertices, so resting fit is unchanged. Now at full strength (1.0), which
  was held back only because a garment deforming exactly like skin could be
  wrong for rigid plate — a glass cuirass at 1.0 has since been judged good in
  game beside the leather.
* **Lift physics chains out of the body.** The fix for the long-standing
  buttock clip. A skirt's chain bones keep their source rest position while the
  body grows, so on a fuller body they end up inside it and the solver pulls the
  cloth in every frame while collision pushes out — which is why more collision
  never finished the job. Each affected chain's root is moved out until no bone
  of it rests inside the body. Trade-off: the free-hanging lower skirt moves out
  by the same amount (0.5u on the test piece).
* **Add the missing rear collision surface**, and **let the visible skirt
  collide, not just its proxy.** These attack the same defect from the body and
  cloth sides; both fire only on pieces measured to need them.

Escape hatches, and the Armor tab is the place to use them:
`CBBE2UBE_NO_FULL_WEIGHT_MATCH`, `CBBE2UBE_NO_CHAIN_REST_LIFT`,
`CBBE2UBE_NO_BUTT_COLLIDER_PATCH`, `CBBE2UBE_NO_SKIRT_PROXY_REBUILD`.

If a skirt now looks held too far off the hips, untick "Lift physics chains out
of the body" first.

### Fixed — an armature link can no longer go missing in silence

Every converted armour records a link that attaches its UBE armature to the
armour itself. If that link is lost between the per-mod patch and the SkyPatcher
INI, the piece is **equippable and invisible** — and nothing said so: the mesh
converts, the report says "converted", the plugin carries the armature, and no
other output mentions the piece again. A pack shipped with one mod's 114 links
absent, found only when a user reported a single invisible arm piece.

The merge now accounts for every link and prints the result:

```
armature links: N recorded -> M emitted (x duplicate, y render-identical, z unresolved)
```

`duplicate` (the same armature claimed by several mods, added once) and
`render-identical` (two identical armatures on one armour would render the mesh
twice) are by design. **`unresolved` should be zero**; anything else prints a
warning naming the consequence. An unreadable link sidecar is named rather than
swallowed, and the four numbers must sum to the recorded total, so a future drop
path cannot hide behind the accounting meant to catch it.

### Added — other new options in this build

Not covered above, and previously absent from this changelog entirely:

* **Keep the upper back covered when the body morphs** (`back_morph_residual`,
  **on**) and **base clearance on reshaping, not just growth**
  (`clearance_differential`, **on**). Both are measurement-driven fit
  corrections. **Neither has an in-game verdict recorded yet** — they are on
  because the offline numbers support them, and this is a pre-release partly so
  that gets tested. `CBBE2UBE_NO_BACK_MORPH_RESIDUAL=1` /
  `CBBE2UBE_NO_CLEARANCE_DIFFERENTIAL=1` turn them off.
* **Four warp guards, all default OFF**: stop the warp flinging a lone vertex
  (`warp_delta_outlier`), never push a vertex through its own armour
  (`warp_push_shell_cap`), stop the warp shearing big triangles
  (`warp_shear_limit`), and stop the conform folding the surface
  (`conform_fold_guard`).

  > **Worth knowing before you convert.** Every conversion verified in game so
  > far — including the ones behind the fit claims above — ran with
  > `warp_delta_outlier` and `warp_push_shell_cap` **ON**, because they are
  > ticked in the maintainer's settings. They ship **off**, so an out-of-the-box
  > run is not the configuration that was tested. Ticking both on the Armor tab
  > reproduces the validated build. Making them the default is deliberately
  > deferred to the 1.3 release rather than decided in a pre-release.

### Known issues

* **The fur-set gloves and a few NPC-skin pieces still get no armature.** 30
  slot-34 pieces in one fur mod, plus draugr-beard and Creation Club items, are
  unlinked for a *different* reason than the forearm fix above and are not
  addressed here.
* **A skirt may now sit further off the hips.** The chain rest-pose lift moves
  the free-hanging part of a chain out by the same amount as the part it
  rescues (0.5u on the test piece). If a skirt looks held away from the body,
  untick "Lift physics chains out of the body" and reconvert that mod.

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
