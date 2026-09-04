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
