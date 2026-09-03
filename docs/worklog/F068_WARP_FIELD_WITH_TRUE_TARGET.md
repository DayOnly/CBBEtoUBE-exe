# F068: the warp field's rejection WAS confounded -- and the combination is a trade, not a win

2026-09-03. F068 says `SURFACE_WARP_FIELD` was rejected on a final-stage number
taken while conform's target was identically zero, and that the two have never
been run together. Both halves are true. The answer is more interesting than
"it was wrong".

## The 2x2

One harness, deployed exe, same mod, four arms, provenance confirmed per arm
from its own `conversion_settings.json`:

| arm | warp field | normal fix | folded | vs author | inverted | seat error |
|---|---|---|---|---|---|---|
| `ctrl` | off | off | 10976 | 9380 | 703 | 0.3806u |
| `fix` | off | **ON** | 10267 | 8671 | **367** | 0.3085u |
| `warp` | **ON** | off | **7464** | 5868 | 647 | 0.3820u |
| `both` | **ON** | **ON** | **6791** | **5195** | 530 | **0.3027u** |

"vs author" is folds in excess of the author's own 1596 in the same 234 shapes.
The author ships **zero** inverted triangles, so every inverted count here is
ours.

Metrics that did not exist when F068 was written: `fold_census` scored one
weight in a population that included first-person meshes, and the author
baseline could not be found at all for a VFS-resolved mod (both fixed 09-03).
Seat error against the author is new.

## What it says

**1. The confound is real, and it is LARGE on folds.** `SURFACE_WARP_FIELD`
removes **32%** of folds (10976 -> 7464), and 37% of the excess over the
author. That is by far the biggest fold lever measured in this campaign -- three
times the conform fix's own -6.5%. A pass that does this was rejected, and the
number it was rejected on was taken downstream of a stage that was broken.

**2. But it is NOT a seat-error lever, alone.** `warp` scores 0.3820u against
control's 0.3806u -- very slightly WORSE. So the rejection was not purely an
artefact of the confound: on fidelity to the author, the field on its own does
nothing. It only helps once conform can see a real target (`fix` 0.3085 ->
`both` 0.3027u, **-1.9%**).

**3. The combination trades one defect class for another.** `both` is the best
arm on folds (-38%) and on seat error (0.3027u), and the WORST of the two
fixed arms on inverted triangles: **530 against `fix`'s 367, +44%**. Against an
author who ships none.

That is the ledger pattern this project already records -- each pass fixing one
defect by causing the other. It is not an argument against the field; it is an
argument that the choice is PER SHAPE, which is exactly what F068's own step (2)
proposes: compute both fields, keep the one with lower scale-free edge
deviation per shape.

## Verdict

**F068's premise is upheld and its step (2) is now priced.** The per-shape field
guard is worth building: the two fields are demonstrably better on different
defect classes, so a global default cannot be right for both. The payoff to aim
at is the `both` column's folds (-38%) WITHOUT its inverted regression.

**No default should change on this measurement.** A blanket
`SURFACE_WARP_FIELD=1` buys a large fold win and pays 163 extra inverted
triangles, and inverted triangles are the defect the author never ships.

Not measured here: clipping and standoff across the 2x2, the fine
physics-chain regression class the census names (this sample has little chain
cloth), and any in-game verdict.
