# The residual zero-weight bones: the CAP is the eviction, not something missing from it

`#zeroweight-bone-desync` is the equip-CTD class: a bone left in a shape's bone
list carrying no weight is absent from the regenerated skin-partition palette, so
a per-vertex bone index can run past that palette. The 2026-09-06 pack ships
**12 across 10 shapes of 9652**.

The record put the residual on the five UNCAPPED post-write sites and named
`_match_limb_motion_to_body` the actor (`ZEROWEIGHT_RESIDUAL_18.md`,
`project_uncapped_postwrite_weight_writes`). **That attribution was wrong**, and
this is the correction.

## Bisected, on the piece that ships one

a third-party cuirass (`<mod>/armorf`), shape `Top_Wrap`. The author holds
`SkirtBBone02` on exactly ONE vertex:

    SOURCE  Top_Wrap  SkirtBBone02  rows=1  live=1  [(757, 0.03467)]
    OUTPUT  Top_Wrap  SkirtBBone02  rows=0  live=0  []

Three arms, one harness (`convert_one_armor.py`, the batch's own worker,
`PYTHONHASHSEED=1`, the five live flags):

    defaults                          zero-weight 2   SkirtBBone02 live 0
    CBBE2UBE_NO_LEG_BEND_MATCH=1      zero-weight 0   SkirtBBone02 live 1
    CBBE2UBE_NO_FULL_WEIGHT_MATCH=1   zero-weight 2   (not the actor)

So the producer is **`_match_rigid_leg_bend_to_body`** — one of the two sites the
record calls *capped*. Wrapping `setShapeWeights` on that shape gives the whole
sequence:

    _install_skin                  SkirtBBone02  rows=1   v757 = 0.03467
        row@v757  Pelvis 0.7912 | R Thigh 0.1014 | Spine 0.0728 | Skirt 0.0347
    _match_rigid_leg_bend_to_body  NPC R Butt    rows=4   v757 = 0.04747
    _match_limb_motion_to_body     SkirtBBone02  rows=0   (already gone)
        row@v757  Pelvis 0.8623 | Spine 0.1294 | R Butt 0.0475 | R Thigh 0.0081

The leg-bend pass grafts `NPC R Butt` onto that vertex, the row goes to five
influences, and `_cap_and_renormalise_rows` keeps the largest four. The author's
0.03467 is the smallest, so it loses — and it was that bone's only weight in the
shape. `_match_limb_motion_to_body` merely writes the empty list afterwards.

## Why the guard already there could not work

The leg-bend pass carries a "NEVER EMPTY A BONE" check that declines to WRITE a
bone the cap zeroed everywhere, leaving the stale weight in the file. That is
precisely the move the record already says fails:

> capping the write, or leaving the stale weight, or writing the old value back
> ALL still lose the contest. Only refusing the newcomer its slot saves the bone.

`setShapeWeights` MERGES and the SAVE resolves an overflowing row itself, so the
fifth influence is written over the stale one and the eviction just moves to save
time.

## `#last-carrier-hold`

`_cap_and_renormalise_rows` takes `incumbents` — the bones each row ALREADY HELD
before the calling pass touched it. A row may not evict an incumbent whose LAST
carrier it is in order to seat a bone that row did not have. Between two
incumbents, weight decides exactly as before. Omit `incumbents` and the function
is byte-identical to its old self, so `_install_skin`'s cap and the jiggle
transfer are untouched until someone opts them in.

It is the rule `#family-weight-invariant` already states — keep the influences a
vertex already HAS, spend only free slots on newcomers — applied at the shared
cap instead of inside one pass.

### Two wrong shapes on the way, both measured

**Wider: prefer last carriers for the smallest surviving slot.** It never
displaced a dominant bone, so it looked safe. Its A/B showed **2771 weight rows**
changed, 2643 on ONE SMP collider shape, worst per-influence delta 0.849, and
that was read as a CASCADE (a cap survivor flipping a vertex into a later pass's
matched set). **THAT READING IS NOT SUPPORTED -- corrected the same day.** Two
IDENTICAL arms at pool scale differ by 1093 weight rows on that same shape and
two others, worst 0.849, 0 vertices moved: it is the record's known weight-tail
nondeterminism (`project_converter_nondeterministic_on_vfs_mods`, "residual 53
rows on `_1` SMP pieces"), and the leg family is what varies. So the wide form's
2771 rows were mostly noise, and the narrowed form's 250 rows are BELOW the
noise floor. The narrowed design stands on the rule it implements
(`#family-weight-invariant`'s: incumbents keep their slots, newcomers take free
ones) and on the bones it fixed -- not on that row count. That residual was
bisected the same night -- a `_0`/`_1` PAIR RACE on the shared physics XML --
and removed by `#pair-unit-dispatch` (`PAIR_UNIT_DISPATCH_2026_09_06.md`); the
pool now equals serial byte for byte.

**Narrower but wrong: `hold_against=to_add`, the pass's graft list.** It fixed
nothing. `NPC R Butt` is already in the shape's bone list and is new only to that
VERTEX. Newcomer is a PER-ROW notion.

**And it has to be wired to the FIRST cap call.** The leg-bend pass caps twice —
once over a provisional palette before `to_add` is decided, once over the final
one. The graft is already in `vw` at the first call, so that is where the
five-influence row first appears; wiring only the re-cap fixed nothing.

## Measured

Both arms from one harness, `PYTHONHASHSEED=1`, the five live flags echoed as
`active flags (5)`, the A/B override passed as a trailing `VAR=VALUE` arg.

**The kill switch is exact.** `CBBE2UBE_NO_LAST_CARRIER_HOLD=1` reproduces the
pre-change build byte-for-byte on every file of the traced piece.

**The class, where it could be reproduced:**

    piece                          bone            control -> hold
    the traced cuirass             SkirtBBone02      2 -> 0   (both weights)
    a second mod's adventurer set  NPC COM [COM ]    2 -> 0   (both weights)

Two of the four offender classes, 6 of the 12 shipped bones, and two unrelated
bone families. The other two classes could not be reproduced: `fattorsof1stp` is
a no-suffix BSA-staged file the single-piece harness cannot drive, and a
`--only-mods` run of the follower mod converts its `bodyf_0` with **no**
zero-weight bone in either arm — a different source resolution than the full-pack
run, so that occurrence is not reproducible outside a full reconvert. **Not
claimed as covered.**

**Blast radius, 184-NIF acceptance population (pool-scale arms):**

    files with ANY vertex moved       0
    worst vertex delta                0.000000000000 u
    weight rows compared              3 293 771
    weight rows that DIFFER           250   (0.0076%)   <- BELOW the pool noise
    worst per-influence delta         0.118164              floor (see above)

**Acceptance, control vs hold, every figure identical:**

    folds                    15974 / 15974        (bar <= 15974)
    inverted                  1937 / 1937         (bar <=  1937)
    bust gap body-swap      +0.682 / +0.682
    bust gap copy path      +0.194 / +0.194
    bust-band penetration       12 / 12
    tip clearance p50        1.244 / 1.244
    tip clearance p05        0.454 / 0.454
    pieces tighter at the tip                0 of 36

**POSED, which is the only metric a weights-only change can move.**
`follow_bands.py` on the file whose weight rows changed most (a vanilla DLC
body piece, 179 rows on its generated `ButtCol`), XPMSSE female skeleton: every
leg band identical to three decimals in both arms; on `ButtCol` the medians are
identical (hip/crouch 1.041, hip/sprint 1.046 vs 1.047) and the `<0.7` fractions
move by one or two vertices out of 129-212. At the noise floor -- and, as the
same night's pair-race work showed, those 179 rows WERE the noise: that
`ButtCol` is one of the five shapes that flip between two pool outcomes on
identical code (`PAIR_UNIT_DISPATCH_2026_09_06.md`). Nothing here was this
change's doing.

Suite 2672 passed / 2 skipped, exit captured directly. pyflakes 0 undefined
names.

## A measurement trap this run walked into

The first candidate arm reported 6 native access violations and four 1-byte NIFs
against a clean control. **It was the disk.** C: had 0 bytes free; the control
arm had already filled it. Freeing space and re-running gave 0 access violations,
0 unreadable files and the same 14 `PASS FAILED` lines the control has. Compare
the FAILURE PROFILE of the two arms before believing a corruption finding —
identical `PASS FAILED` counts are what say the arms are comparable at all.

## Still open

`_match_rigid_leg_bend_to_body` is the only producer traced to a specific bone.
The other four uncapped write sites remain implicated by pattern only, and the
two classes above are unverified. The honest gate stays the paired A/B
(`zero_weight_pair_ab.py`), not the absolute count.
