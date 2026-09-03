# Copy-path pants bury 59% of the butt band — and the author's did not

Reported in game 2026-09-02 ("trousers sit inside the backside"), on the
pack built by exe `10639cfd`. Reproduced, measured, and attributed.

## The numbers

`<the reported trousers>_1.nif`, butt band (`body_zones.butt_mask`), garment
verts whose nearest body vertex they sit BEHIND:

    AUTHOR's CBBE pants vs the CBBE body        0 of 2594   ( 0.0%)   worst 0.000u
    OURS as shipped        vs the UBE body   1535 of 2589   (59.3%)   worst 0.929u
    OURS both new flags off                  1535 of 2589   (59.3%)   worst 0.929u

**The author's fit is perfectly clean. We introduce all of it.** This is not
`#rebury-authored` preserving a buried under-layer -- there was nothing buried.

## NOT the new flags, measured not reasoned

Both arms from one harness (`exe_parity_convert`, deployed exe), arm A
reproducing the shipped pack vertex-for-vertex before comparing:

    A: live settings (as shipped)                          1535 inside
    B: + NO_FAMILY_WEIGHT_INVARIANT=1 + PHASE1_BUST_CLEARANCE=0   1535 inside

Identical. Consistent with what each flag can do by construction:
`phase1_bust_clearance` calls conform with `blend=0.0`, which its own comment
defines as push-OUT only (`max(move, deficit)` from a zero base), so it cannot
pull a vert inward; and `#family-weight-invariant` moves no vertices at all
(measured: 0 of 294 shapes).

## The mechanism, straight off the FIT_STAGES contract

The piece is COPY PATH (no injected `BaseShape`). `FIT_STAGES` records which
body-relative passes each path runs:

    snap        snap_armor_outside_body          BOTH
    antipoke    clear_armor_outside_body         SWAP ONLY
    inflate     _inflate_cloth_over_bust_butt    SWAP ONLY   <<<

The pass named for the bust AND BUTT is body-swap only, and so is the anti-poke
that repairs penetration. A copy-path piece gets `snap` and nothing else, and
`snap` is evidently not sufficient here.

That is exactly the recorded asymmetry: butt bind-pose penetration is **75% of
copy pieces vs 32% of swap** (`project_butt_band_not_chord`, 768 examined /
250 scored), i.e. BUG-02 / fit-chain coverage. This piece is a clean, small,
physics-free instance of it -- the pants carry NO physics XML (only the two
skirts do), so nothing here is a collision or SMP question.

## Why this is a good test case

Better than anything currently on file for BUG-02:
  * the author's own fit measures 0.0%, so the target is unambiguous;
  * no physics on the piece, so no SMP confound;
  * copy path, small (14.4k verts), one shape, converts in one mod;
  * the defect is 59.3% -- large enough that any real fix shows immediately.

## What would fix it (not built, not measured)

The obvious candidate is giving the copy path a body-relative repair it
currently lacks -- either `clear_armor_outside_body` or
`_inflate_cloth_over_bust_butt`. BOTH are geometry changes on 78% of the pack
and neither has ever been measured on the copy path. Note F010's warning
attached to this exact area: the copy path's early panel rigidity looked like a
uniform win on clipping and still could not ship, because it inflated standoff
on 9 of 9 pieces and stranded bones. Any copy-path repair needs the same
paired metrics (clip + standoff + zero-weight bones), not clipping alone.
