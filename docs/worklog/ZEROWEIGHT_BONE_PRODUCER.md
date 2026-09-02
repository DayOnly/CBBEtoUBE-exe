# The `#zeroweight-bone-desync` producer: postwrite weight writes are uncapped

Traced 2026-09-02, out of the F010 campaign. `testing @ 2f95195`.

`#skin-influence-cap` was landed against *"59 zero-weight bones across 42 shapes
pack-wide"* and went default ON. **The current pack has 225 across 163 shapes,
and `verify_zero_weight_bones.py` exits 1.** This is why.

## The mechanism

`_cap_and_renormalise_rows`' own docstring states the premise: *"the NIF format
holds 4 influences per vertex, and the save resolves an overflow ITSELF —
keeping the largest 4 and NOT renormalising (measured)"*.

So a weight can pass every check the writing pass makes and still not land:

1. **`_install_skin`** (`nif_convert_writer.py:188/204`) caps to 4 influences,
   computes `surviving`, `add_bone`s only bones that carry weight, and writes
   `L Breast03 = 0.02432` on vertex 117. Correct at this point.
2. A **postwrite** pass rewrites the shape's weights, filtered only by
   `NEW > _WRITE_MIN` (1e-4). It writes `L Breast03 = 0.00020` — which clears
   the filter, so it is written.
3. It does **not** re-apply the 4-influence cap and does **not** re-check
   `surviving`. Vertex 117 now carries five influences, `L Breast03` smallest.
4. The save keeps the largest four. `L Breast03` is dropped — leaving a bone in
   the shape's bone list with an empty weight list, i.e. absent from the
   regenerated skin-partition palette, so a per-vertex index can run past it on
   equip.

Measured on disk, vertex 117 — exactly four influences, summing to 1.00002:

    NPC Spine2 [Spn2]      0.882812
    NPC L Clavicle [LClv]  0.057373
    L Breast01             0.033203
    NPC L UpperArm [LUar]  0.026627
    (L Breast03 = 0.00020 was the 5th, and is gone)

## The site inventory

Seven weight-write sites outside `_install_skin`. **Five apply no cap:**

    NO CAP  _match_limb_motion_to_body          weights.py:2531   <- proven producer
              shared by five families: leg / arm / spine / spine_twist / full
    NO CAP  _match_coincident_cross_shape_skin       :3150
    NO CAP  _cap_weight_roughness_to_author          :3370
    NO CAP  _hold_weights_at_smp_boundary            :3646
    NO CAP  _sync_weight_partner_jiggle              :3907
    capped  _match_rigid_leg_bend_to_body            :1568
    capped  _transfer_body_jiggle_to_fitted          :4264

Only the `family='full'` path (`_match_full_weights_to_body`,
`#full-weight-match`, default ON since 2026-08-11) was traced to a specific
stranded bone. **The other four are implicated by the shape of the write, not
individually proven.**

## Attribution control

Same piece, same flags, plus `CBBE2UBE_NO_FULL_WEIGHT_MATCH=1`:

    flag ON                        L Breast03 = 0 entries, R Breast03 = 0   STRANDED
    flag ON + NO_FULL_WEIGHT_MATCH L Breast03 = 1 entry,   R Breast03 = 2   clean

## Shipped-pack census

The 2026-08-28 pack, built at defaults + `phase1_bust_clearance`:

    3673 meshes / 9704 shapes -> 225 zero-weight bones on 163 shapes   (gate exits 1)
      142 of 225 are breast bones (L/R Breast01/02/03)
      remainder: Spine1 13, L/R Butt 17, Thigh 10, UpperarmTwist2 9, Belly 5, ...

The bone families map onto the limb-motion families — full/spine → breast and
spine, leg → butt and thigh, arm → upperarm twist — which is what the tool's
"group by bone name names the responsible pass" heuristic is for.

**`--limit` defaults to 300 and takes the FIRST 300 by glob order, not a sample.**
The default run reported 28; the pack figure is 225. Always pass `--all`.

## How it was traced (reusable recipe)

Wrap `pynifly.NiShape.add_bone` and `setShapeWeights` — every shape class
inherits the `NiShape` versions, so one patch point covers all — recording the
calling frame and the `family` local, then run one piece through
`scripts/convert_one_armor.py`, which hands the same work-item to the same
`_nif_convert_worker` the batch uses. The source run reproduced the frozen exe's
output bone-for-bone, so it is faithful for identifying a code path.

`convert_one_armor.py` refuses a slots=0 run; pass `--esp <the plugin whose ARMA
names the mesh>`.

## Two recorded claims this corrects

* `_match_full_weights_to_body`'s docstring says *"why it is default OFF"*. The
  flag comment and the runtime both say **ON**, since 2026-08-11.
* `FAMILY_WEIGHT_INVARIANT`'s comment justifies its separate flag as *"it adds
  no bones, so it cannot strand one unweighted"*. That reasoning is false: a
  pass need not add a bone to strand one — demoting an existing bone's last
  weight to fifth place strands it just as well.

## The fix is not decided

`_cap_weights_map` / `_cap_and_renormalise_rows` already live in the same module
and are exactly what `_install_skin` uses. But **capping alone is not
sufficient**: it makes the write agree with the save, while the emptied bone
still sits in the shape's bone list. A complete fix needs either

* **(a)** the pass not to demote a bone's last surviving weight, or
* **(b)** a write-time invariant — never write a shape that lists a bone
  carrying no weight — which is `surviving` enforced at *every* write rather
  than a cleanup pass bolted on afterwards.

Both change geometry-adjacent output and need golden + an A/B + an in-game
verdict before landing. Recorded, not implemented.
