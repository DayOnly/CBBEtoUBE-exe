# F010 — copy-path early panel rigidity: the answer is still OFF

Audit step 6, increment 4, first campaign. Run 2026-09-02 against
`testing @ 3300e66`, deployed exe sha256 `f7752501…`.

The 2026-09-01 audit asked for one thing before `#panel-rigid-early-clearance`
could get a per-path default: **re-run the copy-path A/B on the current tree**,
because every number supporting the copy-path win predated `#bust-morph-chord`,
`#ride-body-floor`, `#panel-rigid-surface-guard` and `#phase1-bust-clearance`.

That re-run is done. **The win reproduces. The flag still must not ship.**

## Method

Both arms through one harness — `scratchpad_handoff_2026_08_13/exe_parity_convert.py`
on the deployed exe, live settings (`phase1_bust_clearance` only), the ON arm
adding `CBBE2UBE_PANEL_RIGID_EARLY_CLEAR=1`.

Population: **`New Legion` — 86 NIFs, 86 copy / 0 body-swap.** A pure copy-path
mod, so the global flag's effect there *is* the copy-path effect and no
stratification is needed. Nine torso pieces × 26 UBE presets = 234 scored arms
through `scripts/analysis/preset_ab_score.py --band bust`.

**The arm fired**, recorded two independent ways:

* `conversion_settings.json` — same `build.git` (`3300e66`) on both arms,
  `non_default` differing by exactly `panel_rigid_early_clear`. This file did
  not exist before today's build; it is what makes the claim checkable.
* Vertex compare — **112,984 verts moved across 38 of 86 pieces, worst 3.9221u.**

## Result

    CLIPPING (bust band, 234 piece×preset arms)
      improved >0.05 : 108
      unchanged      : 125
      regressed >0.05:   1   (Chainmail / Natural-Looking UBE, 0.524 -> 0.588)
      BROKE A CLEAN PRESET: 0        <- preset_ab_score's priority-1 bar

    STANDOFF (the counter-metric)      ROSE ON 9 OF 9 PIECES
      mean p50  1.414 -> 1.863  (+0.449)
      mean p90  2.429 -> 2.734  (+0.305)
      worst     ArmorOld  p50 0.912 -> 1.699  (+0.787)

    ZERO-WEIGHT BONES                  control 0  ->  ON 16, across 8 shapes

## Why it is still OFF

**The blocker is the bones.** `verify_zero_weight_bones.py` reports the control
arm **clean at 0**, and the ON arm stranding `L Breast03` + `R Breast03` on
eight `Torso` shapes — every one a `1stperson*` mesh. That is the
`#zeroweight-bone-desync` class: a bone in the shape's list but absent from the
regenerated skin-partition palette, so a per-vertex bone index can run past it
on equip.

The mechanism is the recorded one — the 4-influence cap emptying a bone off its
last vertex — and the control is sitting one vertex from the edge:

    control : L Breast01 15 entries | R Breast01 17 | R Breast02 1 | R Breast03 1
    flag ON : L Breast01 16 entries | R Breast01 19 | L Breast03 0 | R Breast03 0

So the flag both empties bones that held a single weight *and* adds an
`L Breast03` that is never weighted. `_copy_shape`'s `surviving` guard computes
correctly at write time, so the add happens downstream of it. **The producing
pass was not traced** — that trace is the open item if this is ever re-opened.

**The standoff is the supporting finding, not the blocker.** Every piece moves
outward, which is the mechanism working as designed (less rigidity → less
penetration → the garment sits further off). But `standoff_audit`'s header
exists because clipping has no upper bound, and its calibration puts a
correctly-fitted garment at p50 1.15 / p90 1.52. Keep the caveat attached: that
figure comes from *one* piece, so cross-piece comparison here is indicative
rather than decisive.

## What this corrects

`memory/project_panel_rigid_early_clearance.md`'s copy-path record — "6 better /
0 worse, BIND −65%" — is **not refuted**. It was clipping-only. Neither
counter-check was run at the time: standoff was not paired, and
`verify_zero_weight_bones.py` was not run on either arm. The clipping win is
real and is larger than that record; it simply is not sufficient on its own.

The OFF default no longer rests on a body-swap verdict inherited by a copy-path
population. It now has its own copy-path evidence, which is what F010 asked for.

## Harness change this campaign required

`preset_ab_score.py` refused any piece without an injected `BaseShape`, i.e.
every copy-path piece — so the one harness that names regressions was
structurally blind to ~78% of the pack, including the half where this flag shows
its largest gain. `morph_clip_test.py` has resolved that since the chord census
by falling back to the UBE template body; that fallback is now in
`preset_ab_score.Piece` too, with the body source printed on every piece row and
a guard that aborts a pair whose two arms resolve *different* reference bodies.
Harness-only: it cannot move a vertex.

## Do not

* Do not re-open this on the clipping number. It is real, it is large, and it is
  not sufficient — that is the finding.
* Do not score a copy-path A/B without `verify_zero_weight_bones.py` on **both**
  arms. It cost nothing here and it is the only check that found the blocker.
