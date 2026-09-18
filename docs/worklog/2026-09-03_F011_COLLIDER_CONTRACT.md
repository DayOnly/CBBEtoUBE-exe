# F011: the collider blocker, measured by CONTRACT rather than by size

2026-09-03. Completes what `2026-09-02_F011_COLLIDER_DELTA.md` deliberately left open.

That earlier note established only a negative: the recorded "461 -> 467 verts"
regression carries no information, because the decimator accepts anything
within +/-15% of target and re-lands every cell boundary when the cloth moves.
It explicitly did NOT establish that `_SRC_NORMAL_FIX` is safe for colliders --
that needed the proxy's actual CONTRACT measured. This is that measurement.

## Getting a population that can answer the question

**Two samples in a row were 0/0 for this.** The clothes mod that answered
F011(b) generates ZERO collider proxies, and a second mod -- picked by grepping
the per-mod reports for an output path prefix -- generated zero as well. A
sample that cannot exercise the mechanism produces a clean result that means
nothing, which is the failure `feedback_census_population` exists for.

The population used here was traced the other way round: take an actual
collider-bearing NIF out of the shipped pack, then find which mod's report
names that file. It yields 198 NIFs carrying **54 generated proxies**
(8 `SkirtCol`, 46 `ButtCol`) -- confirmed present in the CONTROL arm before
either arm was scored.

Arms: `ctrl` vs `CBBE2UBE_SRC_NORMAL_FIX=1`, one harness, deployed exe
(`a6098ec` / `cf9fd6e7`). Each arm's own `conversion_settings.json` records
`src_normal_fix` False / True.

## 1. Does the fix cost us proxies? NO.

The enclose-guard DECLINES a proxy that would contain the chain nodes it exists
to collide with, and it declines BEFORE the shape is created -- so enclosure in
shipped output is 0 by construction on both arms, and measuring it there would
be a tautology. The contract question is whether the fix causes more DECLINES,
because a declined proxy means the piece ships with no collider at all.

    generated proxies, ctrl : 8 SkirtCol + 46 ButtCol = 54
    generated proxies, fix  : 8 SkirtCol + 46 ButtCol = 54
    appearing / disappearing: 0 / 0

## 2. Does the fix break the declared-bone invariant? NO.

A collider may only carry bones its physics XML DECLARES; violating that is the
collapse class. Scored on the authoritative population (the converter's own
view of what the XML registers, not a name list):

    registered shapes scored   : 274      274
    carrying undeclared bones  : 10       10     <- and all 10 are the AUTHOR's
    UNDECLARED BONES WE ADDED  : 0        0
      of those, generated colliders : 0    0
      of those, authored shapes     : 0    0

Identical on both arms.

## 3. The vertex count, now with a real sample

    54 paired proxies
      identical vertex count : 51
      changed                :  3     +7, +4, -2   (mean +0.2)

This is the recorded "regression" reproduced IN KIND -- one `SkirtCol` moves
+7, close to the 461 -> 467 (+6) that was filed as "BREAKS skirt colliders" --
and simultaneously refuted as a signal: the changes go in BOTH directions, and
94% of proxies do not move at all. A systematic defect does not produce -2.

## Verdict

**F011's collider blocker is retired.** On this population `_SRC_NORMAL_FIX`
costs no proxy, adds no undeclared bone to any collider, and moves the
generated vertex count on 3 of 54 proxies bidirectionally.

What it does NOT establish: that no OTHER mod's proxies behave differently.
This is one collider-bearing population, not the pack. But the specific claim
that has blocked the flag since 2026-08-24 -- that it breaks skirt colliders --
does not survive contact with the contract metrics.

Remaining F011 blocker: **(c), the conform constants have still not been
re-validated with the fix on**, and there is still no in-game verdict.
