# Full scan of an HDT-SMP conversion, and an extremity fix that FAILED

2026-09-04. User: "I think something is wrong." A full structural, physics and
fit comparison of one converted HDT-SMP mod against its author's own meshes.

## What is CORRECT (recorded so it is not re-investigated)

* **Physics is intact.** Body collision (`VirtualBody`) is preserved on every
  piece and `SkirtCol` proxies are correctly added. The one collider block lost
  is a **dangling reference the AUTHOR shipped** -- their tome XML names a
  `VirtualGround` their own mesh does not contain -- which the converter pruned
  and reported as `hdt_xml_shape_dropped`. Correct behaviour.
* **No skin weight was lost.** 66 bones were dropped from shapes, but every
  affected vertex still sums to ~1.0: the reskin redistributed, it did not
  damage.
* **The 36 "missing" shapes are by design** -- exactly 12 body-swap NIFs x the
  3 CBBE body shapes replaced by the UBE BaseShape.

## The defect: a glove's lining is 4.633u inside the hands it covers

Measured against the mesh a glove actually covers, not against the body:

    9_gloves_inner    AUTHOR 0 verts inside the Hands, worst -0.000u
                      OURS 226 verts inside,           worst +4.633u

The author's lining rests exactly on the hands and never penetrates. Ours has
fingers over four units through it.

**CAUSE.** The converter REPLACES the CBBE hand skin with a UBE Hands mesh of
different topology (6374 -> 15500 verts) and never refits the garment covering
it. The fine-animation (hand/foot) sub-branch runs only `warp_hf` and
`inflate_hf` -- verified from a stage dump, two stages and stop -- and every
push in it is damped by `(1 - hf_ef)`, which is ~0 at the digits.

That damping is CORRECT and must not simply be removed: the UBE reference body
(`femalebody_tangent*.nif`) is a single BaseShape with **ZERO finger and toe
bones**, so there is nothing at the digits to fit against. Fitting them to the
body would be fitting them to the wrist.

## A METRIC ERROR OF MINE, corrected here

The scan first reported "4636 verts inside the body" pack-wide for this mod.
For digit-region vertices that number is an ARTEFACT: it measures fingers
against a body that has no fingers, so the nearest body point is the wrist.
The honest figure is the one above, measured against the Hands mesh -- fewer
vertices, far deeper, and unambiguous.

## THE FIX FAILED. Both halves. Reverted.

`#hf-antipoke`, default OFF, added the body push-out to the sub-branch and then
a second push against the injected UBE Hands/Feet mesh weighted by `hf_ef`.

**Half 1 -- push against the body: INERT.** It moved 1504 verts of the glove
lining and changed penetration by 0.000u. The vertices inside carry hf_ef
**0.994** (deepest 1%: **1.000**), so the digit damping reduces the push to
**zero exactly where the penetration is**:

    shape             hf_ef on inside verts    push weight (1-hf_ef)
    9_gloves_inner            0.994                    0.006
    8_stocking                0.969                    0.031
    7_BootsShortL             0.982                    0.018

**Half 2 -- push against the Hands mesh: HARMFUL.**

    shape                    author  ctrl   fix   ctrl deep   fix deep
    9_gloves_inner                0   226   220     4.633u     4.666u
    9_gloves_inner_metal          0     0    68    -0.080u    +0.507u

It barely moved its target and INTRODUCED 68 verts of penetration on the metal
layer that had none. Pushing a garment that WRAPS a limb away from that limb's
surface has no single outward direction, and the solve drove one layer into the
mesh while trying to free another. The boots case could not even be scored --
the cover shape my slot detection named is not in the NIF.

**Reverted in full.** An opt-in that is inert at best and harmful at worst is
worse than no flag: it invites someone to turn it on. The diagnosis is kept;
the code is not.

## What a real fix needs

Not a push. The digit region needs to be FITTED to the replacement mesh -- a
correspondence between the author's hand and the UBE hand, so the glove is
re-derived on the new topology the way the body-delta warp re-derives the rest
of the garment. That is the producer: we swap the hands, so we owe a refit of
what covers them. A clearance push cannot substitute for a missing fit, which
is what both halves above proved.

Until then the defect stands and is recorded. It affects gauntlets, boots and
stockings on the copy path -- the pieces whose covering geometry sits over a
mesh we replaced.


# CORRECTION (same day): the defect above was a METRIC ARTEFACT

Everything above the line was measured with NEAREST-POINT NORMALS. That measure
is unreliable on thin features, and a hand is nothing but thin features. Two
validated tests say the glove defect does not exist:

    RAY clip metric (validated)   ours 0.000% -- no skin through the glove
    parity ray containment        0 of 120 sampled "inside" verts are inside

So "226 verts up to 4.633u inside the hands" was **fabricated by the metric**,
and so was the author's contrasting 0 -- their glove simply sits where no
normals flip. The extremity branch's two-stage gap is REAL, but it is not
producing a visible defect, and the fix was correctly reverted for a better
reason than the one first given.

## Re-scored with the validated ray metric

| shape | ray clip% | nearest-point had claimed |
|---|---|---|
| `8_stocking` | **0.000%** | 127 verts inside |
| `10_panty` | 0.005% | 233 |
| `2_dress` | 0.217% | 117 |
| `1_dress` | **1.383%** | 529 |

Gloves, stockings and panty: artefact. Only the dresses survive.

## The author baseline was ALSO void, by F011

The first author comparison read "clip 0.000%, covered 0.00%" -- a dress
covering none of its own body. Cause: **the author's inline 3BA body has
all-zero normals** (max length 0.000000), so every ray was cast along a zero
vector. That is F011's defect voiding my own probe. With
`_body_normals_or_compute`:

    AUTHOR  1_dress   clip 0.000%   covered 15.38%
    OURS    1_dress   clip 1.383%   covered 14.87%

Comparable coverage, so this comparison is finally sound: **the author's dress
lets no skin through and ours does.**

## And that one is on SIMULATED cloth, at bind pose

    dress verts nearest the clipping body verts: 78.1% simulated
    shape average:                               28.8%

2.7x concentrated on exactly the vertices the anti-poke SKIPS on purpose,
because HDT-SMP owns them at runtime. The `VirtualBody` collider they collide
against is preserved in our output (verified). Bind-pose clip on simulated
cloth is the case [[project_antipoke_vertex_blind]] records as UNSOUND.

## Conclusion

**No confirmed visible defect in this conversion.** Everything reduces to
either a nearest-point artefact on thin features, or bind-pose clipping on
cloth the solver resolves at runtime. No code change is justified by this scan.

Worth an eye in game on the dress specifically, since 1.383% is above the
0.768% that was once judged "not noticeable" -- but that is a look, not a fix.

## The lesson, which is the durable output

THREE separate metric errors in one investigation, each producing a confident
wrong number:

1. **seat error on hanging props** -- a belt bag scored 0.888u while moving
   0.0008u. Standoff-from-body is meaningless for geometry that does not touch
   the body.
2. **"inside the body" for digits** -- measured fingers against a reference
   body that has ZERO finger bones, so the nearest point is the wrist.
3. **nearest-point normals on thin features** -- fabricated 226 penetrating
   verts and a 4.633u depth that the ray test puts at zero.

Use the RAY for containment and clipping. Use nearest-point normals only where
the body is thick, and never on hands, feet or fingers.
