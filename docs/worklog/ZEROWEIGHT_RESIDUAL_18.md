# The 18 zero-weight bones that survived the fix, and what they have in common

Measured 2026-09-02 on the pack built by exe `10639cfd` (git `f6d87a0`), the
first carrying `#family-weight-invariant`. Pack-wide the class went
**225 -> 18** (163 -> 15 shapes), but the gate still exits 1.

I predicted 0. That was an over-extrapolation from one mod (a 38-NIF outfit,
21 -> 0), which happened to contain only the class the fix covers.

## They are all ONE class: a bone the AUTHOR held on a handful of vertices

Checked against the source meshes:

    Pauldron    `NPC COM [COM ]`   source: 2 weighted verts   ours: 0
    Top_Wrap    `SkirtBBone02`     source: 1 weighted vert    ours: 0

and in both sources there are **no author-side zero-weight bones at all**, so
these are not inherited authored orphans. We lost weight the author had.

Within a shape, the stranded bone is consistently the one holding the FEWEST
vertices -- usually the distal end of a chain (`Breast03`, `SkirtRBone03`) or a
marginal member (`Breast02` where `Breast01` dominates):

    Cuirass1st  survivors L/R Breast01 6,6 and L/R Breast03 2,2  -> Breast02 lost
    ClothF.001  survivors 1,1,1                                   -> Breast01 lost
    ArmorF1p    survivors 5,5,1                                   -> Breast02 lost
    LeatherArmor survivors 136,136                                -> Breast02 lost
    BaseArmor   survivors SkirtRBone02 82, SkirtRBone01 38        -> SkirtRBone03 lost

**A 1-2 vertex influence has no statistical presence**, so any pass that
re-derives weights from the body drops it.

## Two sub-mechanisms, one signature

**Out-bid (6 of 9 sampled).** The shape has weighted siblings and the vertices
the family holds are SATURATED at 4 influences -- 272/272, 12/12, 3/3, 736/736,
11/11 (the skirt case 66/106). There was no free slot, so the save's
four-largest rule discarded the smallest. This is the mechanism
`#family-weight-invariant` fixes, reaching it by a path the invariant does not
guard: it is gated on `len(rows)` and only protects vertices the limb-motion
pass actually MATCHED.

**No family presence (3 of 9).** `SkirtBBone02`, `NPC COM`, `NPC Head` have no
weighted relative in the shape at all -- the whole influence was the one or two
verts, and losing them leaves nothing behind.

## Why the current fix does not reach them

`#family-weight-invariant` lives inside `_match_limb_motion_to_body` and guards
`NEW[rows]`. Four other weight-write sites remain uncapped
(`_match_coincident_cross_shape_skin`, `_cap_weight_roughness_to_author`,
`_hold_weights_at_smp_boundary`, `_sync_weight_partner_jiggle`), and none of
them consults what the SOURCE held.

## The fix this points at

Not another per-site cap. The invariant that covers all five sites at once is:

    a write must never leave a bone EMPTY that the SOURCE weighted

That is checkable at one place (the final write), needs no per-pass knowledge,
and is strictly stronger than the family rule -- which only knows what the
vertex currently has, not what the author intended. `_install_skin`'s
`surviving` list is the natural home; it already computes exactly this set, but
from the post-cap map rather than from the source.

CAUTION, recorded before anyone builds it: `_cap_weights_map`'s docstring warns
that AUTHORED SMP skins carry deliberate zero-weight constraint bones that are
load-bearing ("dropping them collapses the skirt"). The two sources checked here
have none, but the guard must exempt authored-skin pieces the same way
`preserve_authored_skin` already does.

## Severity

Unchanged in kind from the 225: `#zeroweight-bone-desync`, the equip-CTD class.
But the residual is 8% of what shipped on 2026-08-28, and every case is a bone
carrying 1-2 verts of the author's weight, so the geometric loss is negligible
(the same argument measured in ZEROWEIGHT_BONE_PRODUCER.md: 0.004% of a shape's
weight mass). The bone-list bookkeeping is the whole defect.
