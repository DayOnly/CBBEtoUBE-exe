# #phase1-antipoke: the population A/B, and a measurement bug that voided the first one

2026-09-02. `New Legion` (86 NIFs, **86 copy / 0 body-swap**, so the copy path is
isolated by construction), both arms through one harness on a fresh `dist` build
of `35a1dfe`. The deployed tool was left alone -- it predates the flag and an arm
run on it is VOID by construction.

## A MEASUREMENT BUG FIRST, because it voided everything measured before it

`standoff_audit.clip_stats` and `standoff_stats` call `np.flatnonzero(mask)`
**internally**: they take a BOOLEAN MASK. The first scorer passed
`np.flatnonzero(band(bV))` -- already-converted indices -- so `flatnonzero` ran
on an index array and selected a meaningless vertex set.

Proof on one shape (`PantsPenitus`, a trouser shape):

    band   passing INDICES      passing a BOOL MASK
    bust   covered 35.3%        covered  0.0%     <- trousers cover no bust
    butt   covered 35.4%        covered  3.3%

Identical-looking bands were the tell. **Everything clip/standoff/coverage
reported before this fix was void**, including a "coverage dropped 75.8 ->
71.9%" that was raised twice as the main risk and simply was not real.

Unaffected, because they never went through that API: the penetration counts
(own boolean mask + signed distance), the vertex-movement totals, and the
zero-weight-bone gate.

## The result

    BUST band, 15 (shape) arms        BUTT band, 11 (shape) arms
      inside   1 better / 0 worse       inside  11 better / 0 worse
      clip     0 / 0 (already 0.000)    clip     4 better / 0 worse
      standoff 8 OUT / 0 in             standoff 2 OUT / 8 in
      coverage 0 dropped                coverage 7 dropped (~3pt, torso pieces)

    zero-weight bones   0 -> 0 on BOTH arms
    arm fired           155,364 verts moved on 42 of 86 pieces, worst 3.55u

Per-shape highlights:

    ArmorPenitusF  PauldronsPenitus  butt  clip 32.258% -> 0.265%   inside 18 -> 0
    ArmorPenitusF  d                 butt  clip  3.470% -> 0.000%   inside 89 -> 11
    ArmorF         PantsPenitus_1    butt  clip  4.546% -> 0.000%   inside 34 -> 0
    ArmorOfficerF  Armor001          butt  inside 45 -> 0
    DragonArmorF   Torso001          butt  inside 67 -> 2

## Why this is NOT F010

F010 -- the last copy-path repair attempted here -- improved clipping uniformly
and still could not ship, because standoff inflated on **9 of 9** pieces (mean
+0.449u) and it stranded 16 zero-weight bones. This one:

* moves standoff **INWARD** on 8 of 11 butt arms. That is the fix working, not a
  regression: verts that were buried contributed nothing, and now sit just
  outside as short hits, which pulls the median down.
* drifts the bust out by only 0.03-0.23u on 8 of 15, several times TOWARD the
  1.15u "correctly fitted" calibration (0.912 -> 1.141 on one torso).
* strands **no** bones (0 -> 0).

**The bust band is essentially untouched** -- clipping 0.000 throughout before
and after, coverage 100% -> 100%, zero drops. That was the open risk when the
only evidence was a pair of trousers: this pass moves the whole shape, so it
could have undone `#bust-morph-chord` and the surface guard. It does not.

## What is still not established

* **Butt coverage drops ~3 points on the big torso pieces** (82.7 -> 80.0,
  82.8 -> 80.2, 83.0 -> 79.9). Coverage moves BOTH ways elsewhere
  (11.3 -> 38.8 on a pauldron, 19.9 -> 22.6, 77.5 -> 78.5), so it is not a
  uniform loss, but the torso drop is unexplained. A garment pushed out can let
  a grazing ray miss it; whether any of that is visible is unknown.
* ONE mod. `New Legion` is heavy plate; soft-cloth and layered outfits are not
  represented, and the physics paths (mixed-cloth restore, SMP push cap) are
  built but UNEXERCISED here -- New Legion has no physics XML.
* In game: only the ruby-flower pants have been looked at ("looks good").
