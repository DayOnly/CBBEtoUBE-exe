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

## THE RUN HAPPENED — 2026-09-09, and the recipe held

Finished 23:55. **163 sources (162 mods plus the base game and its DLC), 3673
meshes, 0 failures, 43 warnings.** The pack records what built it, so none of
this is inferred:

    build      1.3 @ 4080558, dirty=false, PyInstaller 6.20.0
               built_utc 2026-09-10T00:35:12Z, exe sha256 e63f751eed7f
    settings   the deployed json, status ok, sha256 190cb065c7bc
    non_default  {"layer_order_last": true}      <- the ONE change, as written
    effective    layer_order_last, authored_antipoke, authored_inflate,
                 coherence_repair_outside_body, field_screen_physical,
                 phase1_bust_clearance, pair_tri_names  -- all True

Read that block out of `<pack>/conversion_report.json` -> `run_config` before
trusting any figure below. It is the only thing that proves the pack you are
scoring is the pack the recipe describes.

**The release exe is NOT the exe that built this pack.** 1.4 was cut after the
run, so the shipped binary is a later build of the same behaviour. That is the
1.3 pattern too; it matters only if you are bisecting.

### What the always-run layer said

    postreconvert_audit.py   exit 1
      armor_nifs 3664, pack_nifs 3673, converted_ok 163, physics_xmls 180
      hard_failures / nif_errors / nif_morph_losses / load_failures        0
      weight_partner_warnings / proxy_encloses_chain / xml_bom_double      0
      xml_unparseable 10          (the AUTHORS' own, expected)
      pass_failure:hdt_xml_shape_dropped   160 -> 168     REGRESSED
      skirt_proxies                         28 ->   2     changed (neutral)

    verify_zero_weight_bones.py --all
      3673 meshes / 9653 shapes -> 2 zero-weight bones on 1 shape
      unchanged from the pack before it

**The baseline that audit compares against is from 2026-08-28 and TWO packs
old.** A delta against it is not this run's doing without further evidence.
Re-baseline with `--record` only once the pack is judged, never on a pack the
converter might still be writing.

### The 43 warnings are ONE false alarm

All 43 are `master-ordering ... (load-order/FormID resolution crash)` on the
per-source patches in `<pack>/_unmerged_patches/`. A plugin in a subfolder is
not in `Data/`, so none of them is ever loaded. 229 checked on disk, exactly 43
mis-ordered — a 1:1 match. The three plugins that DO ship are correctly ordered
and `postflight_validate_combined` returns 0 CTD / 0 soft.

**Set `CBBE2UBE_MO2_INI` before classifying master order or the answer
inverts:** `_is_esm_tier_master` opens each master to read its TES4 flags, and
with no plugin index every ESL-flagged `.esp` reads as regular. Full write-up
and the proposed fix are in the master-ordering memory.

### Still to score against the control figures below

The control figures were taken on the 10-mod / 479-NIF acceptance ARM, not on a
whole pack, so a pack-wide number is NOT comparable to them. Scope to the same
population or say plainly that you did not.

## VERIFY AFTER THE RUN

Control figures for this population, so a regression is visible. **The tool is
named on every row, because it was not and that was nearly a wrong verdict:**
`fold_census` and `pack_census` both print `FOLDED`/`INVERTED` and they are NOT
the same metric -- `pack_census` skips the generated collision proxies
(`VirtualBody`, `VirtualGround`, `SkirtCol`, `ButtCol`), `fold_census` has no
proxy handling at all, so it scores decimated proxies as surface defects. On the
release pack it reported MORE inverted verts over a STRICTLY SMALLER population
(26380 over 2424 NIFs, against 22355 over 2960). **These rows are `pack_census`,
which is what `acceptance.py` parses. Never cross-cite the two.**

`fold_census` carries a second trap: its population comes from
`standoff_audit.output_nifs()`, which globs only `*_1.nif`/`*_0.nif`, so every
single-weight mesh is dropped silently -- 507 of the pack's 2960, and its
printed population line reads as full coverage. Both of the pack's dead-slider
pieces are single-weight, i.e. exactly the class it cannot see.

    folds 30542   inverted 2612   BODYTRI on the body 118      pack_census
    bust gap body-swap 0.553 / copy 0.197                     bust_gap_score
    bust-band pen body-swap 108 / copy 51                     bust_gap_score
    tip clearance p50 1.071 / p05 0.105                       nipple_clearance
    stretch rate p50 0.1089 / p90 1.7021                      stretched_edges
    edge deviation p50 0.0308                                 stretched_edges
    morph clip 46 / 34 pieces, bind clip 23, over 92 scored   band_class_census
    morph clip p50 0.0759 / p90 8.6961   bind clip p90 3.3064 band_class_census

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
