# Nipple outlines through plate: traced end to end

Reported in game 2026-09-03: "nipples on certain armor create outlines through
the plate". Every link below is measured, and the first four fixes tried were
all inert -- the trail is worth keeping because the obvious suspect was wrong.

## The defect, measured against the author

Lift = mean standoff over the nipple minus mean standoff over the SAME shape's
flat chest. Ours against the UBE body, the author's against their CBBE body,
because only the RELATIONSHIP is comparable across two different bodies.

| shape | AUTHOR | ours | over-lift |
|---|---|---|---|
| `chest_plate` | **-0.059u** | +0.114u | +0.174u |
| `top` (inner) | **-0.139u** | +0.105u | +0.243u |

The author's garment sits CLOSER at the nipple than across its own flat chest.
Ours pushes out. correlation(nipple weight, extra standoff vs author) =
+0.44 / +0.50.

## Four things that are NOT the cause

Each fired (geometry changed, provenance confirmed per arm) and each left the
lift where it was:

| lever | plate lift |
|---|---|
| control | +0.1143u |
| `#bust-authored-nipple-cap` (built for this) | +0.1145u |
| `_SRC_NORMAL_FIX=1` (F011's producer fix) | +0.1110u |
| `CONFORM_BUST_CLEAR=0.3` (the documented-but-never-applied ceiling) | +0.1142u |
| `NO_BUST_MORPH_RESIDUAL=1` (the second nipple charge) | +0.1144u |

All four act on conform's bust requirement, and the stage dump shows why they
could not work: **conform REDUCES the lift** (-0.059). The requirement is
applied as `move = max(move, req - worst)`, so lowering `req` only bites when
the conform's own movement is smaller -- and it is not.

## The stage table, which is what actually answered it

`CBBE2UBE_STAGE_DUMP`, `chest_plate`, weight 1:

    s00_entry                -0.1100u          <- the author's own tuck
    s01_bake_preset          -0.0018u  +0.1082 <- erases the tuck
    s02_warp                 +0.0536u  +0.0554
    s03_inflate              +0.2167u  +0.1631 <- push
    s04_conform              +0.1576u  -0.0591 <- conform PULLS BACK
    s05_groove_smooth        +0.1593u  +0.0018
    s06_panel_rigidity       +0.0036u  -0.1557 <- recovery
    s07_antipoke             +0.1826u  +0.1789 <- push
    s08_panel_rigidity_post  +0.0295u  -0.1530 <- recovery
    s09/s10                  +0.0295u
    WRITTEN NIF              +0.1143u  +0.0848 <- AFTER the chain

**38% of the defect happens after the chain ends**, where a stage dump is
blind by construction.

## The write-time half, isolated exactly

    ctrl                        plate +0.1143u
    CBBE2UBE_NO_LAYER_RIDE=1    plate +0.0293u   <- its own end-of-chain value
    CBBE2UBE_RIDE_BODY_FLOOR=0  plate +0.1143u   <- not the floor
    CBBE2UBE_PANEL_RIGIDITY=1.0 plate +0.1091u, inner top +0.1047 -> +0.0810

Disabling the layer ride returns the plate to +0.0293u against a chain that
ended at +0.0295u. That is an identity, not a correlation: **the ride is the
entire gap.**

## The mechanism, and why the ride is not the culprit

1. `inflate` (+0.243) and `antipoke` (+0.222) push the INNER `top` out at the
   nipple.
2. Panel rigidity recovers the plate well (-0.153) and the inner top badly
   (-0.067). The inner top ends the chain still bulging, at +0.1044u.
3. The layer ride lifts the plate to sit over that layer, and the plate
   inherits its bulge: +0.1143 vs the inner top's +0.1044.

**The ride is doing its job.** It exists to stop the plate sinking into the
cloth beneath, and disabling it is recorded here as making surface roughness
worse. It is the transmitter, not the cause.

## Where the fix belongs

On the INNER layer's bulge, not on the plate and not on the ride. Fix the
producer and the ride carries a correct value up for free. `PANEL_RIGIDITY=1.0`
taking the inner top down 23% -- from a knob not aimed at the nipple at all --
is independent evidence that this bulge is recoverable.

NOT a recommendation to raise `PANEL_RIGIDITY`: it is global, it touched 12 of
16 files in this piece, and it is the designed counterweight to the anti-poke,
so running it at full strength changes that balance pack-wide to fix a chest
artifact.

## Standing corrections this turned up

* `#bust-authored-nipple-cap` (committed 614e090, default OFF) is **measured
  inert on this defect**. It is a sound guard on its own terms -- the ramp may
  not demand more room than the author left -- but it does not fix what it was
  built for. Its commit message implies otherwise.
* The comment governing chest standoff records "DEFAULTS MOVED 2026-08-13,
  0.3/0.9 -> 0.12/0.3". The floor did move to 0.12; **the ceiling is still
  0.9**, and `git log -S` finds no commit that ever set it to 0.3. Measured
  worth on this defect: nothing. Fix the comment, not the constant.
* `BUST_MORPH_RESIDUAL` is a second nipple-weighted charge, default ON, with
  **zero GUI rows**, and its gate saturates at nipple weight 0.1.

## Method notes

* Two exe arms run CONCURRENTLY raced on the shared run-state file, so one
  arm's "active flags" echo came from the other. The output's own
  `conversion_settings.json` was unaffected and gave true provenance. Do not
  run concurrent arms.
* The stage dump's frame is checked against the written NIF before any number
  is read from it; disagreement would make every distance in the table void.

## The fix built: `#ride-outward-cap` (OFF) -- real, partial, and bounded

The mirror of `#ride-body-floor`: that one stops the ride putting cloth INTO
the body, this stops it hauling cloth OUT past what the rider's own fit chain
decided. It pulls the rider back toward its own standoff but never closer to
the geometry beneath than `RIDE_MIN_GAP` (0.05u), so non-crossing is kept.

A/B from source, both arms, 21229 verts held on the reported piece:

| axis | control | cap on |
|---|---|---|
| plate lift (author -0.059u) | +0.1143u | **+0.0888u** (-22%) |
| inner `top` lift | +0.1047u | +0.1047u (base layer, correctly untouched) |
| nipple poke-through | 0.0000% | 0.0000% |
| layer gap at the nipple | 0.0933u | 0.0503u |

**It recovers 22%, not the 74% that removing the ride entirely gives, and the
third row says why: the pull-back hit its own non-crossing floor.** The plate
came back until it was 0.05u from the layer beneath and stopped. It cannot
reach its own fit standoff because the bulged inner layer is physically in the
way.

So this CONFIRMS the diagnosis rather than completing the fix. Lowering
`RIDE_MIN_GAP` would buy more lift by spending the last of the separation
margin -- and the minimum gap on this piece is already 0.0000u somewhere even
in the control, so that is not a trade worth making.

**The inner layer's bulge remains the binding constraint**, and it resisted
every lever tried (see the table above): reducing any one push hands the work
to the next pass. That is the damage-ledger oscillation, and it needs a
different kind of change than a knob -- which is why nothing further was built
here.
