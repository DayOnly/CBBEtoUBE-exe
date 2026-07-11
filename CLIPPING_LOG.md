# CBBE→UBE Converted-Armor Clipping Log

Running log of clipping / poke-through issues found **in-game** on the UBE-converted
output, for converter diagnosis + fixes. Started 2026-07-07 (post unified-coverage /
3c). Add new finds under **Open**; move to **Fixed** with the fix reference.

## How to read an entry
- **Zone + condition** is the key signal:
  - **Standstill / at rest** → **STATIC clearance** problem: the armor geometry sits
    inside the UBE body at the bind pose. Fix class = anti-poke / adaptive outward
    clearance (inflate armor over the UBE butt/belly/hip/breast).
  - **During movement only** → **DYNAMIC / conform** problem: the armor doesn't track
    the UBE bone flex (rigid plate not following thigh/knee/butt bend), or a
    physics/jiggle mismatch. Fix class = rigid-leg-bend match / weight-conform /
    jiggle transfer.
  - **Only when zoomed OUT** → likely depth **z-fighting at distance**, not real clip
    (do the zoom test first — cosmetic, clearance won't fix it).
- Note the exact body region (rear thigh, belt back, breast, etc.) and whether it's
  the ARMOR poking through the body or the BODY poking through the armor.

## Open

### Falmer Slayer Bodysuit -- body pokes through the REAR (butt/rear-thigh) -- REAL, diagnosed 2026-07-10
The actual reported defect (the bare-breast thing below is by design). Standing still, large
preset: red body skin shows through the tan skintight suit at the legs/abdomen, worst at the
REAR. Measured (SURFACE metric, confirmed NOT a nearest-vertex artifact -- vertex and surface
agree):
    abdomen     9% of covered body verts outside the suit, worst -0.82u
    front thigh 7%,  -0.56u
    rear thigh  28%, -1.63u   <-- bad
    hip/butt    28%, -1.81u   <-- bad
Front is minor; the REAR is the failure. The suit IS conformed (moved 0.63u from its 3BA
source) and its deepest butt point clears the body, so it is under-covering the rounded
butt/rear-thigh SLOPES, not grossly undersized.
**ROOT CAUSE:** the suit is HDT-SMP rigged (`_shape_has_hdt_smp_rigging -> True`), and the
anti-poke clearance pass (`clear_armor_outside_body`) DELIBERATELY SKIPS SMP-rigged shapes --
gate at ~nif_convert.py:11582 `... and not _shape_has_hdt_smp_rigging(...)`. The skip is
intentional (pushing an SMP cloth's verts disturbs its physics rest shape), so the conform
runs but the outward clearance guarantee never does. This is the known cloth-to-body declip
tension ([[project_cloth_to_body_declip_research]]), not an oversight.
**FIX PATH (not yet built -- physics risk, needs in-game validation):** allow a LIMITED,
outward-only anti-poke on SMP suits -- only where the body actually pokes (rear), small
max_push, front-and-rounded-slope only -- gated behind a default-OFF flag so it doesn't
change every SMP cloth in the pack at once. Alt: improve conform coverage for SMP. Either
needs a reconvert + in-game check that the suit's physics still behaves.
STATIC (standing still) so it's clearance/coverage, not a jiggle-only fault. Screenshot on file.

### Falmer Slayer Bodysuit -- breasts exposed above it (reported 2026-07-10) -- BY DESIGN, NOT A DEFECT
Whiterun, standing still, large preset. The suit covers legs + abdomen; the breasts are bare.
USER CONFIRMED (twice) the bodysuit is NOT meant to cover the breasts -- it is a strapless /
underbust piece, and bare breast skin above it is CORRECT. This matches the earlier finding
that the source `FalmerSlayerBodysuit` mesh stops around the underbust (no chest geometry).
So there is nothing for the converter to fix on this piece: it converts the geometry it is
given, and that geometry does not include the chest. The breast coverage in that outfit
comes from a SEPARATE piece (the Falmer Slayer Chestplate). NON-ISSUE -- do not chase it.
(Recorded only so it is not re-reported as a bug.)

### 0d. "Armor too small for the breast" -- the adaptive clearance cap sat BELOW the bust target
Reported in-game after 0b/0c landed: the cuirass reads too small at the breast, at rest AND
in motion. "Both states" is the tell -- a defect present at rest that merely persists through
motion is STATIC CLEARANCE, not a dynamic conform or morph-tracking problem.

**PINNED: WHERE THE BREAST ACTUALLY IS.** z **90-102**, apex ~95-96 (UBE body, feet z~11).
NOT z 99-112 -- that is the UPPER CHEST. Every "breast" number in entries 0/0b/0c above was
measured on the upper chest by mistake, which is exactly why they all looked clean. Verified
two independent ways: the body's front-most vertex is at z=95, and the strongest verts of
`BreastsBigger`/`BreastsTBD` are at z=96. `ANTIPOKE_BUST_CLEAR`'s own `bust_z=(84,100)` was
right all along. Measure the breast at z90-102 or the defect is invisible.

**Root cause.** `clear_armor_outside_body` has two paths. The legacy one gives the bust zone
a ramp up to `ANTIPOKE_BUST_CLEAR = 1.0`. The newer adaptive one REPLACES it with
`clip(base + factor*amp, base, ADAPTIVE_CLEARANCE_MORPH_MAX)` -- and the cap was **0.8**,
*below* the 1.0 bust target. So the pass that was meant to REFINE bust clearance silently
gave the breast LESS room than the fixed code it superseded. The ramp wanted 0.95 mean /
1.32 peak there and was clipped 72% of the time.

**Measured before the fix** (steel cuirass, TRUE breast band, signed clearance along the body
normal): rest mean +0.66u, **min -1.49u, 8% of breast verts already poking AT REST**; with a
slider applied, 13% poking, deepest -1.98u. Disproven en route: it is not nearest-vertex
quantisation (a full surface-barycentric transfer moved the worst vert only -0.44u -> -0.31u
and changed the poke count 19 -> 19), and the injected `BaseShape` is byte-identical to the
user's built body, so the armor IS fitted to the body being worn.

**Fix (#bust-clearance-floor).** Floor the bust zone by the legacy ramp using the same
`np.maximum` pattern `rear_standoff` already used ("a FLOOR on req, never stacks"), and lift
`ADAPTIVE_CLEARANCE_MORPH_MAX` clear of the bust target. Gated on the body's nipple weight,
which is measured nonzero ONLY at z90-102, 100% front-facing, and **exactly 0 on the back**
-- so rear/side clearance cannot move. Env dial `CBBE2UBE_CLEARANCE_MORPH_MAX` (no rebuild).
Check with `scripts/verify_bust_clearance.py`: breast should show no poking verts, and rear
mean clearance must stay tight (a jump there means the nipple gate leaked and every armor
went baggy at the back).

### 0c. POST-RECONVERT VERIFICATION 2026-07-10 (offline; user in-game still pending)
`scripts/verify_motion_match.py` over 1223 meshes:
- **Ratio 1.00** on all three problem armors, every driving slider: Noble Dark Leather
  `Cuirass_A/B`; Steel Plate `Cuirass` (was 0.85), `SteelArmor.012` (was 1.48), `Collar`;
  Falmer `chestplate`/`gorget`.
- **Rigid-prop shear: 0 hits.** The artifact predicted from ratio-1.0 (a scabbard straddling
  the hugging + drape zones bending) did NOT materialise.
- **43 shapes carry no TriShape at all** -- `ColLegs`, `HDTSkirt`, `ColBelt`, `Dress1`,
  `VirtualBody`, `Proxy`, `Stabilizer`: collision proxies + HDT softbody, excluded by design.
  These show as "ratio 0.00" in a naive scan; that is NO morph, not a wrong morph.
- **Only 3 genuinely off-ratio shapes**, all marginal drape cloth at the `_MATCH_NEAR` seam:
  `CloakF` 1.11, two `Skirt` 0.90. Cosmetically negligible.
Also confirmed: Noble Dark Leather has NO auto SMP xml and 0 jiggle bones on the cloth
(crash + balloon fixes held through the reconvert).

### 0b. RESOLVED 2026-07-10 -- the rule is "armor moves as the body it covers" (ratio 1.0)
Entry 0 below diagnosed the symptom correctly but the FIX was wrong twice. Final rule:
an armor vert copies the delta of the body vertex it COVERS, so clearance is preserved
-- the body then can't poke through and the armor can't balloon past it. Measured, both
failure modes in-game on one steel cuirass + one leather cuirass:
- plain IDW (old): dilutes a fitted cuirass to **0.59-0.81x** -> body bursts through.
- regional-peak "protrusion-follow" (my first fix): overshoots to **2.0-2.2x** -> armor balloons.
- nearest-copy (ratio **1.00**): correct. Subsumes the stand-off plate case -- preserving
  clearance means nothing can poke through, so no regional peak is needed.
Drape cloth far off the body keeps the smoothed IDW average (a skirt must not snap to
whichever leg vert is nearest): weight eases 1.0 -> 0.0 between `_MATCH_NEAR` (4u) and
`_MATCH_FAR` (10u). `_motion_match_weight` in `src/sliderset_gen.py`.

**Second defect, same symptom.** After the above, the steel cuirass STILL poked. The
**overlay-band morph-sync** was overwriting the breast plates' exact tracking with their
under-layer's deltas. The tell: `Collar` (5589 verts, too LARGE to be classed a "band")
held 1.00 while every band candidate was rewritten -- `Cuirass` 0.85, `SteelArmor.012`
1.48. That pass only existed because bare IDW diluted each shape by its OWN stand-off, so
band and layer moved differently and the band re-sank; motion-match makes every hugging
shape 1.0, so hugging band + hugging layer are ALREADY lockstep. Gated the sync to bands
genuinely lifted off the body.

**Traps recorded:** (1) the sliders that inflate the chest are mostly NOT breast-named --
`Donaught` 2.65, `TBD 2.0` 2.38, `Amazon` 1.79, `Peachy`, `Juicy_body`, `SternumDepth`.
Never filter morphs by name. (2) A ratio computed against a near-zero denominator is
meaningless -- `SteelArmor.012` "2.37x" was 0.16u vs 0.07u. Check ABSOLUTE motion too.
Check with `scripts/verify_motion_match.py`. WATCH: rigid props (scabbard/sword) that
straddle the hugging + drape zones can now SHEAR (near verts move fully, far verts don't).

### 0. Breast-covering STANDOFF plates under-follow the breast slider (ROOT CAUSE, measured 2026-07-09)
Two user reports — Falmer Slayer chestplate "clipping all around the breasts" and
Steel Plate cuirass "clips at the under breasts" — are ONE root cause, measured across
both armors. **Body pokes through the plate at the breasts when the breast slider is up.**

**Mechanism (measured, not guessed).** The per-armor BODYTRI (`generate_armor_tri`)
propagates each body slider delta to armor verts by nearest-body-vertex IDW. The breast
is a PROTRUDING VOLUME that inflates; a plate covering the surrounding chest sits at
standoff (2-4u) and its own nearest body vert is on the low-morph upper chest, so
pointwise transfer leaves it ~static while the apex balloons out through it.
- Perfect inverse correlation standoff→follow (Steel Plate Cuirass2, BreastsBigger, body=0.94):
  `SteelArmor.015_2` standoff 4.4u→0.61 · `Collar` 3.5u→0.49 · `Cuirass` 2.2u→0.63 ·
  `SteelArmor.012` 3.2u→**0.08**.  Fur (soft, hugs body, ~0 standoff)→1.02 (tracks fine).
- Nearest-vertex delta for the .012 plate verts (z≈106) = **0.02**, but the apex peak
  within 5u = 0.58, within 8u = 1.29. The signal is REGIONAL, not local.
- **Simple fixes disproven by measurement:** nearest-bias / K1-lift did nothing
  (11%→12%) because the nearest body vert itself under-morphs. No nearest-vertex scheme
  (the converter's whole TRI approach) can capture a non-local volume inflation.
- Physics RULED OUT (SMP-off "changed nothing"). Belly/butt follow fine (broad, local).
**Fix candidates (need a reconvert to validate):** (A) regional protrusion-clearance in
the morph — plate verts covering the breast get an outward delta from the regional breast
expansion (visibly "follows"; but rigid steel ballooning looks wrong + higher risk); (B)
damp the HIDDEN covered-body breast morph under a rigid non-following plate so the breast
is CONTAINED by the plate instead of poking through (physically correct for rigid steel —
a breastplate holds the breast in; lower risk; user still sees full slider on exposed
skin + soft armor). Leaning B for rigid plates. See memory `project_breast_standoff_morph_follow`.

### 1. Noble Dark Leather  — reported 2026-07-07
Female, viewed from behind (Whiterun). Outfit = leather cuirass + metal shoulder
pauldrons + quilted/metal hip skirt-belt + thigh guards + greaves.
- **Rear thighs — clip DURING MOVEMENT (dynamic).** The thigh/leg armor doesn't
  track the UBE rear-thigh flex while running → body shows through at the back of
  the thighs on the stride.
  - Likely class: rigid leg-plate not following the thigh bend (rear-thigh /
    rear-calf conform). Related prior work: `project_rigid_leg_knee_conform`,
    `project_boot_far_thigh_fade`.
- **Back of belt — clips through AT STANDSTILL (static).** The rear belt / skirt
  band sits inside the UBE butt/hip at rest → body pokes through the back of the belt.
  - Likely class: missing outward clearance over the UBE butt at the bind pose.
    Related prior work: `project_adaptive_armor_clearance`, `project_antipoke_refinements`.
- Mod: "New Leather Armor" by furexarot (Nexus 132069, CBBE 3BA/HIMBO). Mesh =
  `!UBE\narmor\leathersuitn\dcuirass_1.nif` (d = dark variant).
- DIAGNOSED (shape inspect): thigh armor = shape `Greaves` (z25-79). The leg-bend
  conform (_match_rigid_leg_bend_to_body) DID run on it (has FrontThigh/RearThigh/
  RearCalf grafts) -- NOT a skip bug. Rear-thigh clip during movement = the conform's
  thigh match is PARTIAL by design (z-tapered: FULL at knee -> partial in thigh, to
  avoid over-rotating the larger-radius plate into a rest-pose bulge). So this is a
  TUNING tradeoff, not a missing pass.
  - Fix options: (a) strengthen rear-thigh follow (risk: bulge on other armor);
    (b) small rear-thigh OUTWARD clearance on the plate (fix clip w/o touching the
    bend match). Belt-back (Buckles_01/Cuirass_B rear hip band, z84-97) = separate
    STATIC anti-poke clearance.
- FIX ATTEMPT 1 (user chose "strengthen bend follow"): raised `_LEG_BEND_THIGH_STRENGTH`
  0.40 -> 0.60 (nif_convert.py:5081). HONEST CAVEATS: (1) also tried raising the cutoff
  66 -> 72 but MEASURED it INEFFECTIVE (z66-72 body verts are butt/pelvis-dominant, not
  leg -> _leg_deform_match_vert's leg-vert gate skips them; that region is genuinely
  butt) -> REVERTED to 66. (2) Can't validate the 0.60 strength offline (re-running the
  idempotent pass on the already-converted mesh doesn't reproduce a fresh conversion).
  (3) The rear-thigh DETAIL-bone (RearThigh/FrontThigh) weight on the plate is inherently
  TINY (~1.0 sum / 246 entries), so strengthening the blend may move it only modestly.
  -> The strength bump is low-risk but UNCERTAIN; needs reconvert + in-game. If not enough,
  the OUTWARD-CLEARANCE approach (declined) may actually be MORE effective here (adds
  physical clearance, sidesteps the weak-follow). Suite 615.
- FINAL DIAGNOSIS (after 4 failed fix attempts, all REVERTED): NOT follow, NOT
  coverage, NOT clearance. It is a DRAPE-PROJECTION WEIGHTING MISMATCH. The under-butt
  is covered at rest by a loosely-draping loincloth flap (shapes Cuirass_A + Greaves,
  ~3u off the body). The converter weights each flap vert by its NEAREST 3D body vert
  -- which for a flap draping 3u BEHIND the butt is the PROTRUDING BUTT CHEEK (high
  butt-jiggle ~16%), NOT the RECESSED GLUTEAL FOLD the flap actually covers (~8%). So
  the flap bounces with the cheek while the fold it covers moves less -> on the stride
  they diverge -> the fold shows red. Confirmed: source flap = Pelvis50/Thigh50/Butt0;
  converted = Pelvis37/Thigh31/Butt32 (matched to the cheek, not the fold).
  - FAILED attempts (all reverted, none changed the in-game clip): (1) _LEG_BEND_THIGH_
    STRENGTH 0.40->0.60 (follow already matched body 0.36 vs 0.36); (2) _LEG_BEND_CUTOFF_Z
    66->72 (butt-dominant body verts skip the leg-vert gate); (3) general clearance
    (rejected -- movement-only, would loosen rest fit); (4) butt-jiggle graft use
    bd[b]*mass instead of full[b] (no change -- bd IS the high cheek value).
  - REAL FIX (not done): weight draping cloth by the surface it COVERS (project along
    the drape/inward direction), not its nearest 3D vert. Substantial nif_convert change,
    CANNOT validate offline (dynamic/animation). SHELVED as a known-hard edge case.
- Status: SHELVED (known-hard drape-projection). Belt-back STATIC clip: not separately
  pursued (the "belt" IS this same under-butt flap).

### 2. New Leather Armor "pants" (Greaves) — reported 2026-07-07
CORRECTION to entry 1: the movement clip is the PANTS (shape `Greaves`, the full-leg
piece z25-79, 55% leg-bone weight), NOT the loincloth flap (entry 1 chased the wrong
shape). Diagnosis (measured): the pants already TRACK the body's bones almost exactly
at the rear butt/thigh (pants Pelvis69/Thigh17/Butt2 vs body Pelvis71/Thigh16/Butt3 --
so "strengthen follow" was always a no-op), but the rest gap there is a razor-thin
0.40u. Standing still it's fine; mid-stride the body's butt/rear-thigh deforms past the
sliver and shows. Low jiggle-weight zone, so the JIGGLE_CLEARANCE term can't reach it.
- FIX (user chose "targeted rear-butt clearance"): new REAR_STANDOFF term in
  clear_armor_outside_body (nif_convert.py) -- a FLAT minimum standoff (1.0u) enforced
  GEOMETRICALLY where the nearest body vert is rear-facing (normal.y < -0.15, front=+Y)
  and at butt/upper-thigh height (z 45-80 on the injected UBE body). Raises `req` only
  where the current gap is below the floor, so well-fitted armor is untouched; front
  fit unchanged. Default ON, CBBE2UBE_NO_REAR_STANDOFF=1 disables, magnitude via
  CBBE2UBE_REAR_BUTT_STANDOFF. A/B verified: rear-butt p10 clearance 0.53u -> 1.00u,
  FRONT unchanged. Suite 615.
- Status: fix DEPLOYED + IN-GAME CONFIRMED (no explosion, no collapse, rear-thigh clean).

### 2b. Calf clip + layer clip (refinement, reported 2026-07-07 after 2/3/3b fixed)
- CALF (FIXED): the pants clipped near the calves during motion -- measured min clearance
  0.25u at z30-35 (== ADAPTIVE_CLEARANCE_BASE). The lower leg is a low-MORPH but high-FLEX
  zone, so the adaptive clearance shrank it to the static floor, but the knee/calf bend
  punches through mid-stride. FIX = new CALF_STANDOFF term in clear_armor_outside_body: a
  flat 0.6u floor over the lower-leg band (z20-46), all-round, raising only sub-floor
  verts. Verified 0.25u -> 0.60u, suite 615, deployed. CBBE2UBE_NO_CALF_STANDOFF=1 /
  CBBE2UBE_CALF_STANDOFF tune. Mirrors REAR_STANDOFF (flex zone the morph-amp misses).
- LAYER-CLIP (DEFERRED): leggings (Greaves) vs skirt/bodysuit (Cuirass_A/B) measured
  1327 verts poking ~0.94u through the over-layer at z60-77. `_separate_abdomen_layered_
  cloth_depth` restores SOURCE order but only in the source frame, so it misses the UBE-fit
  divergence. Built a bounded OUTPUT-frame tuck-under (push under-layer in, floored at 0.5u
  body clearance) but REMOVED it: the over/under CLASSIFICATION is UNRELIABLE for this
  phase-2 armor -- two source-frame proxies (Nevernude CBBE body vs CBBE warp body)
  DISAGREED on which of Greaves/Cuirass_A is outer, so a tuck could push the WRONG layer
  and worsen clipping. NEEDS: user to pinpoint WHICH pieces clip into which (then target
  that pair with a known over/under), or a reliable per-vert layer-order signal on the
  body-swap path. Not shipped rather than risk a mis-classifying regression.

### 3. EQUIP EXPLOSION on the same armor — reported 2026-07-07 (FIXED)
Equipping the re-converted leather armor detonated the mesh (verts flung to spikes).
NOT geometry (stored bboxes normal) and NOT the rear-standoff (geometry-only, runs
last). ROOT CAUSE = a `_0`/`_1` SHAPE-SET MISMATCH: `_exposed_body_skin_shape_names`
flagged the baked bare-lower-leg skin shape `Calves` as exposed body skin at weight
`_1` but NOT at `_0` (the CBBE-coincidence test is weight-sensitive). So `_1` routed to
phase-2 body-swap (drop `Calves`, inject full UBE `BaseShape`) while `_0` took the
phase-1 copy (keep `Calves`, no body). The engine's BodyMorph interpolates `_0`<->`_1`
vertex-by-vertex; mismatched shape sets -> garbage verts -> explosion. The working
`.bak` body-swapped BOTH weights (both had `BaseShape`), which is correct.
- FIX: make the exposed-skin decision WEIGHT-PAIR-CONSISTENT -- convert_nif now unions
  `_exposed_body_skin_shape_names` over both `_0` and `_1` (loads the sibling nif) so the
  pair always takes the same path. Verified: both weights now "body-swap", IDENTICAL
  shape sets + vert counts. Deployed live; exe rebuild in progress. General class = any
  weight-variant armor with a borderline baked-skin slice (bare leg / open cleavage).
- LESSON: any per-weight-sensitive shape decision (drop/inject/classify) MUST be
  reconciled across the `_0`/`_1` pair or the morph explodes. body_names (inline-body
  heuristic) is coarser but shares the risk -- watch it.

### 3b. Post-explosion-fix COLLAPSE — ROOT-CAUSED + FIXED (2026-07-07)
**SOLVED.** The pants (`Greaves`) collapsed because its skin-to-bone (STB) transforms
were WIPED to identity ([0,0,0]) on the `_0` weight (`.bak`/`_1` correct: Pelvis Z=-68.9,
Thigh [-13.5,2,67.9]). Identity STBs = no bind pose -> verts skin to the origin/fly ->
collapse. Bisect (env flags) pinned the wiper to `_transfer_body_jiggle_to_fitted`
(CBBE2UBE_NO_JIGGLE_TRANSFER restored the STB). ROOT: that pass does `add_bone`(L/R Butt)
+ `setShapeWeights`, both of which pynifly RESETS every existing bone's STB to identity,
but it only set the NEW jiggle bones' STBs -- it never SAVED/RESTORED the existing
(Pelvis/Thigh/Spine) STBs, unlike its sibling `_match_rigid_leg_bend_to_body` which does.
Only `_0` hit it: `_1` had the Butt bones already added by an earlier pass (so
jiggle-transfer saw them as existing -> `if not new_bones: continue` -> no add_bone), while
`_0`'s degenerate-tri repair shifted which verts graft -> jiggle-transfer was the one
add_bone'ing them -> wipe. FIX: added the save/restore-existing-STBs pattern (mirror of
_match_rigid_leg_bend) to `_transfer_body_jiggle_to_fitted`, incl. restoring before the
`if not safe: continue` bail (add_bone had already run). Verified: `_0` Greaves max
bone-local dist 80u->38u (== `.bak`), Pelvis STB 0->68.9, both weights match, suite 615.
GENERAL: latent for ANY leg armor where jiggle-transfer is the pass that first grafts a
jiggle bone. Deployed live + exe rebuild. Detail below (superseded 3b-original).

### 3b-original (SUPERSEDED — kept for the wrong-turn record)
After the pair-union fix stopped the explosion, the re-converted armor's PANTS collapsed
at runtime (pull-to-plane) + legs showed bare (red skin viz). Could NOT reproduce or
explain offline. The re-converted `_0`/`_1` NIFs + generated `dcuirass.tri` measure
byte-correct + topology-consistent vs the working `.bak` in EVERY checked property:
geometry (no exploded verts), skin bind transforms (valid), <=4 bones/vert, weight sums
1.0, 0 unweighted verts, identity node transforms, identical skin partitions, identical
block types (no physics/SMP extradata), matching shape sets, tri morph indices all
in-bounds (max = vertcount-1/shape). WRONG TURN: my first "cause" was BODYTRI=
`femalebody_tangent.tri` -- a TEST-HARNESS ARTIFACT of converting to a TEMP dst with no
`meshes` segment (BODYTRI relpath is derived by finding `meshes` in the DST, nif_convert
:10950); a real `meshes` dst correctly emits `dcuirass.tri`. So the collapse is a RUNTIME
factor invisible to static analysis (candidate: `_0`/`_1` vertex-ORDER divergence sharing
one tri -- NOT verified). CAUTION: the OLD exe that built the working pack was OVERWRITTEN
by the 10:15 rebuild -- working output for THIS armor exists ONLY as the `.bak` NIFs; the
rest of the pack is untouched old-exe output.
- STATUS: working baseline RESTORED (`.bak` NIFs + a valid topology-matched tri, indices
  verified in-bounds). REAR_STANDOFF pants fix (entry 2) is correct in the mesh but
  UNVALIDATED in-game (rides on the collapsing body-swap). Explosion pair-union fix is
  correct + general + in the exe. Do NOT trust hand-converted single files (direct
  convert_nif hits path/context artifacts) -- validate via the exe pipeline.

### 4. Reconvert re-exposed the rear-thigh clip — ROOT-CAUSED + FIXED (2026-07-08)
After a full pack reconvert the rear-thigh clip (entry 1/2) came back, and the
REAR_STANDOFF fix looked un-applied. Long chase blamed a "frozen exe vs interpreted
source" float non-determinism (exe grafted FrontThigh ~3x weaker: 0.70% vs source
2.08%). **That was a red herring** — the two runs had DIFFERENT inputs.
- ROOT CAUSE: the reskin gate skips a shape whose SOURCE ships its own BodySlide
  morph TRI (`s.name in src_morph_shapes`, via `_source_morph_tri_shape_names`,
  which checks for `<stem>.tri` next to the source nif). The exe resolves
  `dcuirass_1.nif` through the MO2 VFS to the **BodySlide-output override** (
  `Authoria - Bodyslide Output - 3BA`), which HAS `dcuirass.tri` beside it → Greaves
  classified as morph-TRI → the WHOLE reskin block skipped → **no FrontThigh/RearThigh/
  RearCalf/Butt scale bones** → rear-thigh clip during movement. The earlier "source"
  run pointed at the base mod (no `.tri`) → reskin ran → scale bones present. Same
  code, different resolved source.
- WHY THE EXEMPTION WAS WRONG: a BodySlide TRI supplies **body-slider** morphs only
  (static shape), NOT animation bone-flex follow. The animation scale bones are a
  separate concern; skipping them loses movement-follow.
- FIX (`nif_convert.py`, phase-2 reskin block): morph-TRI shapes still KEEP their
  stable source skin (TRI fidelity) but now the scale-bone graft (`add_scale_bone_
  weights`) runs on that source skin. The graft only overrides the skin if it actually
  added a scale bone; else the true source skin flows through unchanged. Non-morph-TRI
  shapes unchanged (full reskin, as before). See `[DESIGN: Morph-TRI reskin]`.
- VERIFIED: clean deployed exe on New Leather Armor → Greaves FrontThigh 0.70%→**1.60%**,
  RearThigh 0.65%, RearCalf 0.11%, Butt 1.17% (was ftBASE=0 / all scale bones absent);
  full-reskin branch (base mod) still 2.08%; suite 615; golden A/B held; exe byte-verified.
- Status: **FIXED + DEPLOYED**, pending in-game confirm after reconvert. General class:
  ANY leg/body armor whose winning source copy ships a BodySlide TRI (very common —
  every BodySlide-built mod) was silently losing its animation scale bones.

### 5. Hide Cuirass (vanilla) — big-butt exposure — reported 2026-07-08
Female, front + back (Whiterun). Vanilla Hide armor (bodice + short fur skirt).
**Measured** (`scripts/armor_clip_diag.py`): **BUTT 81–84% exposed** (body pokes past
the skirt at rest — the skirt sits below/inside the large UBE butt), CHEST only 4–5%.
- **Zone + condition:** BUTT, at rest → STATIC coverage gap. The `CuirassLight` shape
  has **0% butt-bone weight** and doesn't reach around the butt at bind.
- **Class:** NOT the 3BA-source bug (Hide is a vanilla-sweep armor, unchanged this
  session). This is "large UBE butt preset vs a short/revealing armor the converter
  doesn't inflate enough to cover" — the anti-poke / softcloth-butt-inflation not
  reaching a big butt. Same on cuirasslight/medium/heavy variants (81/81/83%).
- Unknown new-vs-always (user is triaging, not yet A/B'd vs pre-reconvert).
- Status: **OPEN**. Likely a broader class — see triage table below.

### 5t. Full-pack butt/chest-exposure triage (2026-07-08)
Scanned all 1219 converted body-swapped `_1` meshes (`scratchpad\triage_scan.py`) for
body-through-armor exposure (butt z66–82, chest z92–108), then classified
(`triage_classify.py`). 148 raw flags → after dropping 1st-person/actor false positives
and by-design skimpy tops/bras, **the real signal is one recurring class**: full-coverage
vanilla-style cuirasses/dresses where the large UBE **butt** pokes through the rear at
rest (chest mostly fine). Hide (entry 5) is the archetype. Ranked worst-first:

| butt% | chest% | armor | note |
|------:|-------:|-------|------|
| 100 | 100 | armor/falmer/falmerarmorf | falmer (skimpy-ish, verify) |
| 100 | 85 | clothes/volsCloaks/hoodedF | cloak (open front?) |
| 86 | 0 | falmerslayer/FalmerSlayerChestplate | chestplate, no rear plate |
| 77 | 3 | creationclub asvsse001 extravagantrobe01b | robe SHOULD cover — real |
| 73 | 18 | dlc01 falmerheavy cuirass | full cuirass — real |
| 72 | 18 | armor/aom/lamae/lamaedress | dress — real |
| 70 | 5 | armor/hide/f/cuirasslight | **entry 5 archetype** |
| 69 | 8 | armor/imperial/f/cuirassmedium | full cuirass — real |
| 69 | 22 | armor/studded/male/body | full cuirass — real |
| 69 | 22 | armor/crbex/yor/yordress | dress — real |
| 68 | 25 | armor/imperial/f/cuirassheavy | full cuirass — real |
| 68 | 4 | armor/hide/f/cuirassheavy(chieftain) | full cuirass — real |
| 68 | 11 | armor/imperial/f/cuirasslight | full cuirass — real |
| 68 | 5 | armor/witchhunt/witch/ashwitchdress | dress — real |
| 66 | 12 | armor/revenant/queen/queendress | dress — real |
| 66 | 0 | dbm museumarmor 1stexplorersgarb_f | explorer garb — real |

- **By-design skimpy** (butt uncovered on purpose — NOT converter bugs): AsurasBra,
  Cimmerian Top, Sigrin onlyTop, Traveler's Romper Bra, FVO Top, Zamorian Thief Top,
  AuriLL Top, Ruby flower Top. Skip unless user reports one specifically.
- **False positives** (1st-person/actor bodies — no worn butt geometry): falmer/aom
  actor bodies, all the `*1st*`/`*fp*`/`*1person*` meshes. Ignore.
- **Read:** these are almost all vanilla/DLC/CC full cuirasses+dresses — a STATIC rear
  coverage class, one fix (stronger softcloth-butt inflation / rear anti-poke reach on
  full-torso armor) would address most of them at once. NOT yet A/B'd vs pre-reconvert;
  Hide is unchanged this session so this is a pre-existing limit, not a new regression.

### 6. Fur Cuirass (bandit fur band/bra) — OVER-inflation, breast gaps — reported 2026-07-08
Female (Whiterun). Item "Fur Cuirass" = `REQ_Light_Fur_Body_Kilt` (Requiem for the
Indifferent) → vanilla ARMA `BanditCuirass1AA` → mesh **`armor\bandit\body1f_1.nif`**.
A skimpy strapless fur band (bra) + fur kilt. User: "overinflates all around and creates
gaps especially at the breasts."
- **Zone + condition:** breast band, at rest → armor stands OFF the body (hollow gap).
  This is the **OPPOSITE** of the Hide/butt under-coverage class (entry 5).
- **Measured** (`scratchpad\fur_confirm.py`): the output band shape `Top` (212 verts)
  = the **3BA-output** source `Top` (212 verts). In the SOURCE, `Top` is **flush** on
  the body (standoff mean **+0.00**, max +0.00). In the OUTPUT, the same `Top` stands
  **+1.77u mean / +3.72u max** off the UBE body — the converter moved the band verts
  outward (~1.56u NN). **Converter-ADDED standoff.**
- **NOT the 3BA-source bug.** Ruled out by measurement: the 3BA source's bust reach is
  5.70u ≈ UBE's 5.74u, and the band was flush — the source is a GOOD source here. The
  gap is purely the converter's **outward inflation** lifting a tight conformed bra off
  the body. (Aside: build22's source deprioritization keys on the substring "bodyslide
  output"; this modlist also has output mods named "Authoria - Vanilla Bodyslides" and
  "CBBE 3BA Vanilla Outfits Redone - Prebuilt" that the heuristic does NOT catch — but
  those weren't used here and wouldn't have helped, since the 3BA source is fine.)
- **ROOT CAUSE (confirmed by bisection 2026-07-09) — NOT softcloth, NOT anti-poke.**
  Both were toggled off with ZERO change (`scratchpad\ab_fur.py`). The chain:
  1. The winning source is **HDT SMP Vanilla Armors** (build22 tier-0; the 3BA output is
     tier-2). It bundles its OWN body `BanditBody1` whose bust reaches **+9.88u** — a
     big-bust preset (the 3BA/vanilla-bodyslide sources bundle a normal +5.70u body).
  2. The fur band `Top` is authored FLUSH on that +9.88 body.
  3. `Top` is HDT-SMP soft-body cloth. The converter deliberately does NOT warp softbody
     verts (would disturb the sim) — measured, it moved `Top` only **0.26u** at the
     breast, leaving it at its source position (hugging +9.88).
  4. UBE bust is **+5.74u** (4.1u smaller), so the source-positioned band is left standing
     off. The softcloth pass only pushes cloth OUT where the body POKES; nothing pulls a
     too-loose band IN → the +1.77u gap.
  So: **a source that bundles a big-bust preset body + softbody cloth kept at source
  position + no pull-in-when-loose = the gap.** Same FAMILY as the New-Leather 3BA-source
  bug (source built to a mismatched preset), surfacing on softbody cloth.
- **Two disproven fixes (do not retry):** (a) softcloth clearance tuning — softcloth
  isn't even acting here. (b) lowering `_BODY_HEURISTIC_MIN_VERTS`/`_MIN_BONES` so
  `BanditBody1` (1397 verts / 22 bones) is recognized as the ref body — no change,
  because softbody `Top` doesn't use the body-referenced warp at all.
- **FIX DESIGN:** make the softcloth pass **bidirectional + source-offset-preserving** for
  softbody bust/butt bands — pull the band IN toward the UBE body to the SAME clearance it
  hugged its bundled source body (`BanditBody1`), keep pushing OUT on poke. The source
  offset is the gate that protects draping robes (they hug loosely -> keep their drape).
  Needs: (1) detect the bundled body-skin shape regardless of poly count (texture
  `_shape_diffuse_is_body_skin` is the reliable signal — True only for `BanditBody1`),
  handling its shifted transform space; (2) extend `_inflate_cloth_over_bust_butt`.
  This touches the CTD-sensitive HDT-SMP path (cf. crash C1) -> requires golden A/B +
  suite + an in-game CTD test on reconvert.
- **Class = "source bundles a mismatched-preset body; softbody band kept at source
  position stands off the UBE body."** Fur Cuirass + siblings body2f/body3f; overlaps
  the 6t thin-band list.
- **FIX (2026-07-09, source-selection) — `discovery.build_mesh_index`.** User chose the
  low-risk source-selection route over touching the CTD-sensitive physics path. New
  WITHIN-TIER rule: when a mesh has multiple providers in the winning tier, and the
  incumbent bundles a BESPOKE body (a body-skin-textured, full-body-sized shape that is
  NOT the canonical '3BA') while a same-tier challenger bundles the canonical '3BA' body,
  the challenger wins — it converts flush where the bespoke-body source stands off. Gated
  so it does NOT fire on: a different tier (tier still dominates -> New-Leather safe), a
  source bundling NO body (physics robes keep winning -> SMP preserved), or an exposed-
  skin SLICE (baked hand/neck skin; rejected by a 500-vert / z-range-35 floor). Opt out
  `CBBE2UBE_NO_BODYMATCH_SELECT=1`. Measured end-to-end: fur band standoff **+1.77 ->
  +0.59u mean** (covered +2.12 -> +1.16), now matching the pipeline's normal fit. Pack
  blast radius: **42 of 2165 meshes** re-source (36 fur-class from HDT SMP Vanilla Armors,
  6 from a retexture bundling a full body); College Mage Robes / Miraak / Steel retexture
  correctly NOT swapped (skin-slices only). Suite 622 (+3 tests). **Trade-off:** the
  canonical source is a merged, non-physics mesh -> affected fur-class bands lose HDT-SMP
  jiggle (fit fixed, no jiggle -- acceptable for a fur band; the user chose fit).
- Status: **FIXED IN CODE (source-selection), pending reconvert + in-game.** Exe rebuilt
  + deployed. NOT the tier-2 3BA-output (which has physics AND fit) -- that would need
  overriding the tier system; deferred.

### 6t. Full-pack over-inflation triage (class 2, 2026-07-08)
Companion to 5t. Scanned all 1219 converted meshes (`scratchpad\standoff_scan2.py`) for
the Fur-Cuirass class: a THIN band/bra (shell < 1.8u) standing OFF the UBE body at the
breast (mean under-band gap > 1.2u) = converter over-inflation. Source-free +
thickness-gated so it does NOT false-flag thick plate (a plate stands off but isn't thin).
29 flagged; correctly catches the Fur Cuirass (`armor/bandit/body1f_1.nif`) + siblings.

| gap(u) | shell(u) | armor | note |
|-------:|---------:|-------|------|
| 2.37 | 0.56 | armor/studded/male/body | thin, lifted — also in 5t (butt) |
| 2.22 | 0.55 | armor/generaltulius/generaltuliusf | |
| 2.22 | 0.49 | armor/imperial/f/cuirassmedium | also 5t |
| 2.04 | 0.94 | armor/imperial/f/cuirassheavy | also 5t |
| 1.90 | 0.76 | dlc01/armor/dawnguard/dawnguardbody3f | |
| 1.80 | 1.22 | armor/bandit/body3f | **Fur Cuirass sibling** |
| 1.76 | 0.81 | armor/bandit/body2f | **Fur Cuirass sibling** |
| 1.76 | 1.12 | creationclub asvsse001 commonrobe02 | |
| 1.69 | 1.08 | son6of6tredis Argonian fem/Pants | full-body suit |
| 1.64 | 0.95 | **armor/bandit/body1f** | **Fur Cuirass (entry 6)** |
| 1.64 | 1.00 | armor/revenant/queen/queendress | also 5t |
| 1.63 | 0.87 | armor/imperial/f/cuirasslight | also 5t |
| 1.51 | 0.95 | witch/shamanrobe, aom/hag/hagrobe(v1) | |
| 1.49 | 1.37 | armor/studded/female/body | also 5t |
| 1.46 | 1.10 | pulcharmsolis/redoran watchman/armorf | |
| 1.38 | 1.51 | dlc01 falmerheavy cuirass | also 5t |
| 1.35 | 1.22 | clothes/forswornarmor/forswornarmorf | |
| 1.2–1.33 | ~1.3 | NordWar SonsOfSkyrim guard armors (Solitude/Whiterun/Falkreath), fineclothes01, psiijicrobes, hide cuirassmedium, Kad_KhajiitMonk RobesF | borderline — verify by eye |

- **One false positive:** `qwib/Traveler/Upgraded/Female/FP` (gap 1.71) — `FP` = 1st-person,
  slipped the name filter. Ignore.
- **Cross-class overlap:** imperial (all 3), studded, queendress, falmerheavy, hide
  cuirassmedium appear in BOTH 5t (butt under-coverage) and 6t (breast over-inflation) —
  those full cuirasses have a two-sided fit error: the softcloth inflation over-lifts the
  chest while the rear still under-covers. A single inflation-tuning fix (don't inflate
  where the source hugged; inflate more at the rear) could address both directions.
- **Read:** gap ~1.6u+ with shell < 1.0u (top ~12 rows) = clean thin-band over-lift, most
  fixable/most visible. The 1.2–1.3u tail is borderline (may be normal cloth drape) —
  verify by eye before treating as a bug.
- Status: **OPEN** (triage). Same suspected culprit as entry 6 (softcloth breast/butt
  inflation default-on 2026-07-06 + anti-poke). NOT yet A/B'd vs a pre-graduation build.

## Crashes (CTD)

### C1. Equip CTD — SMP cloth bone-graft palette desync  — reported 2026-07-07
- Crash log: `crash-2026-07-07-12-06-09.log`. `EXCEPTION_ACCESS_VIOLATION`
  (`movups xmm0,[rax+r10]`) in `hdtsmp64.dll` MainHooks::Update, reading skin
  partition data of `BSTriShape "robes"` (BODYTRI, under "HDT Skinned Mesh Physics
  Object" node). Crashed on equipping a converted cuirass.
- Armor: `!UBE\MrTT\Gift\Armor\cuirassmediumfi_1.nif` (source BSA-packed).
- ROOT CAUSE: the converter grafted **8 UBE scale/jiggle bones** (L/R FrontThigh,
  RearThigh, RearCalf, Butt) onto the AUTHORED SMP cloth shapes: `robes` 11->19
  bones, `robes001` 7->14 bones. These shapes are HDT-SMP-driven; adding skeleton
  bones desyncs the skin partition/bone palette -> SMP update reads OOB on equip.
  The unchanged collider shape `NPC R UpperarmTwist2 [RUt2]` (46v/3b, same
  source->converted) is innocent.
- CLASS: same family as the "skin/jiggle passes skip authored SMP" fix, but a
  DETECTION GAP — `robes`/`robes001` are bone-driven SMP cloth not caught by the
  softbody-name guard, so `_match_rigid_leg_bend_to_body` / `_transfer_body_jiggle_
  to_fitted` (the FrontThigh/RearThigh/RearCalf/Butt grafters) still ran on them.
- FIX DIRECTION: detect SMP-managed shapes structurally (parent node has "HDT
  Skinned Mesh Physics Object" extradata, or shape is in the SMP physics tree) and
  skip the bone-graft passes for them — not just by softbody-name. Mesh-pipeline
  change (nif_convert) + reconversion. NOT related to unified coverage (mesh
  conversion unchanged; unified coverage only made this armor equippable so the
  latent crash surfaced).
- Status: **FIXED IN CODE (pending reconvert).** Confirmed the graft left CONSISTENT
  skin data (19 bones all weighted, no >4-weight verts) -- so the crash is HDT-SMP
  (runtime-global config) rejecting the grafted UBE scale bones, not a malformed
  partition. Since there's no per-armor SMP XML to detect (global config), the fix
  adds DRAPING-CLOTH garment names (robe/cloak/cape/dress/gown/sarong/loincloth) to
  `_CONFORM_SKIP_NAMES` (nif_convert.py:5001) so the 3 graft passes skip them. Held
  "skirt" out (metal tassets are rigid plates that want the conform). Verified:
  robes/robes001 SKIP, thighguard/greaves/legplate/metal-skirt still graft. Suite 615.
- FOLLOW-UP (2026-07-08, with the entry-4 morph-TRI fix): the new keep-source-skin
  branch grafts scale bones via the PHASE-2 RESKIN add_scale, which the C1 fix did NOT
  guard (it only gated the 3 conform passes). A draping SMP robe that ALSO ships a
  BodySlide TRI would newly take that branch and get scale bones -> re-open this CTD.
  Closed defensively: the keep-src-skin scale graft now also skips `_CONFORM_SKIP_NAMES`
  (`_drape_skip`) -> draping morph-TRI cloth keeps its source skin, no scale bones, as
  before. Leg armor (greaves/calves/cuirass) is unaffected (not draping-named). In
  build17 source (suite 615); NOT in the build16 exe currently reconverting -- that exe
  has the narrow window open for draping-morph-TRI cloth until a build17 reconvert.

## Fixed
_(none yet)_

### C2. Equip CTD -- HDT-SMP jiggle bones grafted onto multi-layer cloth (FIXED 2026-07-09)
- Crash: `crash-2026-07-09-09-58-57.log`. `EXCEPTION_ACCESS_VIOLATION` in
  `hdtsmp64.dll MainHooks::Update` on equip, faulting on `Cuirass_A`/`Cuirass_B` of the
  New Leather / Noble Dark Leather cuirass (`narmor/leathersuitn/dcuirass`).
- Root cause: the body-follow graft passes (M6 reskin + `_conform_fitted_to_body` +
  `_match_rigid_leg_bend_to_body` + `_transfer_body_jiggle_to_fitted`) graft the body's
  HDT-SMP JIGGLE bones (Breast01/02/03, Butt, Belly) onto bone-driven multi-layer cloth
  cuirass shapes. A runtime SMP config drives that cloth by those body bones -> FSMP
  OOB-crash. Not detected as SMP (no NIF marker; weighted to body bones, not custom
  chains) so the softbody/collider/garment-chain skips all missed it. NOT the body-match
  fix (New Leather sources from base, unchanged; mesh byte-identical to the `.looksgood`).
- Fix: `_layered_cloth_shape_names` (2+ sibling shapes sharing a base stem + short layer
  suffix, e.g. Cuirass_A/_B/_C) -> EVERY graft pass skips them, keeping SOURCE skin.
  Prevention at every site (pynifly can't cleanly remove a bone after; a single post-strip
  gets undone by later passes). Verified dcuirass+d1st Cuirass_A/B/C -> 0 grafted SMP.
  Pack blast radius: 14 meshes (New Leather + accessories/dresses). Escape hatch
  CBBE2UBE_NO_LAYERED_CLOTH_SKIN=1. Suite 628. Exe rebuilt + deployed (hash-verified).
- Status: **FIXED IN CODE + deployed. Pending in-game equip test (reconvert first).**
