# The butt clip on skirted cuirasses — `#chain-rest-pose-inside-body`

Closes the reference `../PIPELINE.md` §7 makes to this tag. Test piece
throughout: `armor/studded/female/body`, source a 3BA BodySlide output, preset
Punk UBE at weight 100. Every number below was produced by running the pass, not
by reasoning about its gates.

## The defect

A skirted cuirass clips the buttock **identically at standstill and in motion**.
That combination is the whole diagnosis: a follow or pose problem appears only
when the actor moves, and a clearance problem would show in a bind-pose number.
This showed in neither.

Chain bone globals move **0.000000u** through the conversion (all 63 bones,
source vs output) while the body grows to UBE proportions. So the rest pose the
HDT solver pulls toward every frame ends up inside the body:

| measured against | bones inside | mean | max |
|---|---|---|---|
| the BUILT UBE body | 2 / 63 | 0.778u | 0.778u |
| that body under the player's preset | 8 / 63 | 0.900u | 2.000u |

Always the `_01` segment at the fullest part of the buttock. `_02..04` hang clear
(+0.3 to +7.6u); the front and stabilizer chains clear everywhere.

`generic-constraint` pulls each bone back toward its rest pose while collision
pushes out, so an inside rest pose drags the cloth in continuously. That is why
three collider passes each helped and none could finish — they add push against
a pull nothing addressed.

**An earlier note put the penetration at ~3.2u.** That compared a bone's `y`
against the body's rearmost point *anywhere* in the band rather than the surface
at the bone's own location. 2.000u is the honest figure. It does not change the
diagnosis and it does change the size of the fix.

## Two candidate fixes that the numbers killed

**Sample a different delta field.** `#chain-body-shift` already shifts roots, by
the generic CBBE-template → UBE-body field. That field spans only 0.743u of
rearward growth at the butt while this armour crosses 3.091u, because its own
bundled body is 2.35u slimmer than the CBBE template there. So the obvious fix
was "use the piece's own source body instead". Measured per root, it gives
**0.9× the generic field, not 4×** — averaging over a chain spanning z38–78
drowns the butt band either way. A better field does not fix a pass whose
problem is that one vector must serve a whole chain.

**Restore each bone's authored clearance** (the bone-space analogue of
`conform_to_source_standoff`). Wrong criterion twice over: the largest losses are
on the FRONT skirt (2.96u) where nothing clips, and **10 of 63 bones were already
inside the source body**, because an author routinely runs a skirt's bones down
the inside of its cloth. The two bones that actually clip are not in the top 16.

Note for anyone re-measuring against a source body: this one ships **all 6463
normals zero**, so a signed distance taken from stored normals reads exactly
+0.000 for every bone and looks like a clean measurement. Derive from triangles.

## What shipped — `#chain-rest-outside-body` (default OFF at first; ON since 08-11)

`_lift_chain_roots_off_body`, `CBBE2UBE_NO_CHAIN_REST_LIFT`, GUI-exposed. Lifts each
garment chain's ROOT along the body's outward normal until no bone of that chain
rests inside the body. Criterion, margin and caps:

* **the margin is the body's own outward MORPH amplitude**
  (`_cached_body_morph_amplitude`, the map adaptive clearance already uses). The
  converter never sees the player's RaceMenu preset, and 6 of the 8 penetrations
  exist only under it. Over the at-risk bones that map reads mean 1.009u against
  an actual Punk UBE growth of mean 0.879u / max 1.615u. Adaptive clearance takes
  20% of that amplitude for garment verts because those verts morph too; a chain
  bone has no morph channel, so it takes all of it.
* **that margin is CAPPED** (`CHAIN_LIFT_WANT_MAX`, 1.75u), and the cap is the
  pass's real engagement rule. Without it the belly's amplitude — which runs to
  8.7u — drove a wanted clearance of 6.79u and recruited two FRONT chains
  measured **+3.63u clear of the skin**, for a confident 2.0u push on cloth with
  nothing wrong with it. The `flare` counter-metric caught it at +8.5u.
* **the lift is capped** at 2.0u, above the 1.868u the worst chain needs here.

Result on the test piece: 6 of 14 chains lift (the rear ones), by 0.919–1.868u.

    bones inside the morphed body      8/63  ->  2/63
    depth                              mean 0.900 max 2.000 -> mean 0.131 max 0.142
    free-hanging chain standoff        +2.412u -> +2.907u   (the cost)
    every shape's verts, all 15 golden pieces        0.000000u
    parent-child rest length                        0.000000u
    XML and TRI                                     byte-identical

## The thing to check before extending this

**"A root shift is rigid" is only true WITHIN a chain, and the chains here are
not independent.** 74 of 130 `generic-constraint`s are cross-chain: the skirt is
a hoop of ten panels stitched to their neighbours at the `_01` and `_02` rings.
Lifting six of them by three different amounts changed 28 inter-panel rest
distances by up to **1.651u**.

That is safe here, for a reason worth checking rather than assuming:

* every **cross-chain** constraint uses `frameInLerp`, so FSMP derives its rest
  frame from the bones AT LOAD and the ±1u linear limits are measured from
  wherever the panels then sit;
* every constraint with an explicit `frameInA` is **intra-chain**, and those rest
  lengths change 0.000000u.

A differential lift under explicit cross-chain frames would fight the solver.
Read the emitted XML for that pairing before shifting roots differentially.

## Status — CONFIRMED IN GAME, and DEFAULT ON since 2026-08-11

The user judged the piece the defect was reported against, in motion, with the
lift, the butt patch and the skirt proxy all present: **"perfect"**. A glass
cuirass carrying the full-vector match at strength 1.0 was judged in the same
pass, which cleared the last unmeasured objection to that pass (rigid plate
deforming like skin). All four are now default ON with `CBBE2UBE_NO_*` hatches.

**The numeric defaults moved with the toggles** — `FULL_WEIGHT_STRENGTH` 0.6 →
1.0 and `BUTT_COLLIDER_OFFSET` 0.2 → 0.6. Those are the values that were built
and judged; flipping a toggle while leaving the knob at its old value would
default the pack to a recipe nobody has looked at, which is the
"shipped default is not the validated configuration" trap the 2026-07-27 audit
recorded. Proved closed: a conversion with NO feature flags reproduces the
deployed, judged build to **0.000000u** on every shape and node, with
byte-identical XML and TRI.

**A reconvert is required** for any of this to reach an existing pack.

**The open risk is reach, not correctness.** The lift fires on 6 of the 12
chain-bearing golden pieces (34 chains) where the butt patch fires on 1 of 15,
and ONE piece carried the verdict. Two pieces (`fitted-dress`, `hide-collider`)
hit `CHAIN_LIFT_MAX`, meaning the criterion wanted more than it is allowed —
those are the first places to look if a skirt is reported standing off the hips.
