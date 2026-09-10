# Next reconvert — the recipe

Measured 2026-09-09 on the 10-mod / 479-NIF acceptance population. Mod names and
machine paths live in the population memory, not here.

## APPLIED 2026-09-09 -- THE RECIPE IS ALREADY SET

    Armor -> "Let the layer fix have the last word"     ON      (layer_order_last)

Written into the deployed settings json; backup
`CBBEtoUBE_settings.json.bak-20260909-pre-layerorderlast`. The GUI maps every
registered setting to its env var for the run, so the deployed exe picks it up
with no rebuild.

**`flag_retirement --recipe` NOW REPORTS 1 DISAGREEMENT, AND IT IS DELIBERATE:**

    LAYER_ORDER_LAST   code=False   live=True

That is the whole point of the change -- the code default stays OFF because the
flag fails four gate rows, and the recipe turns it on because the trade is worth
taking on this pack. **Do not "resolve" it by editing either side.** If a future
session wants them to agree, the decision to promote or drop has to be made
first, on the numbers below.

Everything else stays as it is. The five flags already in the recipe
(`authored_antipoke`, `authored_inflate`, `coherence_repair_outside_body`,
`field_screen_physical`, `phase1_bust_clearance`) all equal the code default —
leave them; they are written down only so an older exe cannot silently revert
them.

## WHAT IT BUYS AND COSTS

    bust-band penetration, body-swap   108 -> 56    HALVED
    bind clip p90 (% of band)        3.306 -> 1.289  -61%
    inverted verts                    2612 -> 2447   -6.3%
    bust gap vs author, body-swap    0.553 -> 0.543  closer
    morph clip p90                   8.696 -> 8.587  -1.2%

    tip clearance p50                1.071 -> 1.032  FAIL
    pieces tighter at the tip             0 -> 7     FAIL
    stretch rate p90                 1.702 -> 1.723  FAIL  (+1.2%)
    folds                            30542 -> 30552  FAIL  (+0.03%)
    posed follow, worst median            0 -> 0.056 FAIL  (net -0.183, better)

**The gate says FAIL on those four rows.** It is still the recommendation: it is
the only change measured this session that moves penetration at all, the losses
are fractions of a percent on surface rows, and it points the same way as the
in-game report ("sits too far off the body") by closing the author gap.

The real risk is the tip: 7 pieces end tighter over the nipple. That is the same
objection the authored floors carry, and the tip harness is known to score a
narrower region than the fix acts on.

## ON THE REPORTED CUIRASS (BUG-16)

    bind clip      5.316 -> 3.585    shallow 3.966 -> 1.685   buried 0.555 -> 0.125
    write time drove 14 bust verts INSIDE the body; now 0

## VERIFY AFTER THE RUN

Control figures for this population, so a regression is visible:

    folds 30542   inverted 2612   BODYTRI on the body 118
    bust gap body-swap 0.553 / copy 0.197
    bust-band pen body-swap 108 / copy 51
    tip clearance p50 1.071 / p05 0.105
    stretch rate p50 0.1089 / p90 1.7021    edge deviation p50 0.0308
    morph clip 46 / 34 pieces, bind clip 23 pieces, over 92 scored
    morph clip p50 0.0759 / p90 8.6961   bind clip p90 3.3064

Score with `scripts/analysis/acceptance.py`; set `CBBE2UBE_CLIP_PRESET` or the
three clip rows skip. `CBBE2UBE_CLIP_EVERY` above 2 makes the census abort and
the rows go dark while the run still passes.

## DO NOT TURN THESE ON

    #panel-rigid-keep-clearance   FAILS 10 rows; penetration WORSE (108 -> 114)
    #antipoke-surface-req         fires and is fully absorbed by the pass after
                                  it; 0 verts change in the written NIF
    authored floors OFF           FAILS 5 rows and gives back the copy-path gap
                                  (+0.197 -> +0.329), for ~1-2 pieces of clip

## STILL OPEN

  * `_repair_coherence_collapse` at the `_copy_shape` site takes NO body, so
    `#coherence-repair-outside-body` is a no-op there. Known gap, unfixed.
  * The gate's tip rows score `>= 0.75 * max` nipple weight; the fix acts from
    `>= 0.50 * max` plus a radius and ring dilation. A change confined to that
    band moves the fix and not the row.
  * BUG-16 is improved, not closed.
