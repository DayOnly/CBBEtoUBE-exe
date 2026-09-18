# The clearance solve measures its feather in EDGES, not in distance

*2026-09-05. Population: 6 mods, 184 NIFs written, 660 garment shapes,
`PYTHONHASHSEED=1`, every arm from `src_parity_convert.py`.*

## The question

Both authored floors (`#authored-inflate`, `#authored-antipoke`) buy a real fit
win and were reverted to default OFF because they cost surface: **+440 folds
(+2.7%), +134 inverted (+6.5%)** against a deployed baseline of 16355 / 2060.
The rise was known to concentrate on a handful of loose dresses, and the
standing prescription was to find the per-shape property separating the 48
pieces that improved from the 44 that worsened, and gate on it.

Two hypotheses were on the table:

* **(a)** a per-shape property, gate on it;
* **(b)** `#unified-offset` — fold the four clearance passes into one solve,
  feathered ONCE, on the thesis that the folds come from *sequential feathered
  pushes compounding*.

## (b) is refuted, and it is refuted by arithmetic

Feathering is a LINEAR operator. N feathered pushes summing to T therefore
produce exactly the same field as one feathered push of T, so "feathered once"
cannot by itself change the fold field. Measured on one flat patch through
`offset_field`'s own `solve`/`feather` — the `#unified-offset` prescription
verbatim:

| edge | scheme | reach (u) | max gradient per world unit |
|---|---|---|---|
| 1.000 | 4x sequential feather | 1.669 | 0.479 |
| 1.000 | 1x solve, feathered once | 1.669 | 0.479 |
| 0.125 | 4x sequential feather | 1.332 | 4.111 |
| 0.125 | 1x solve, feathered once | 1.332 | 4.111 |
| 0.125 | 1x solve, **reach-scaled** | 1.644 | **0.504** |

Sequential and unified are **bit-identical**. What *does* move the number is
reach. So `#unified-offset` would not have removed this fold class: written as
specified it carries the same defect, because `offset_field.feather` is the same
unweighted Laplacian at a fixed iteration count. Its other four claimed
properties (order-independence, one budget per vert, pinning as a weight) are
untouched by this result — only the fold argument is.

## (a) The property is not per shape. It is per VERTEX, and it is STRAIN.

Binning every vertex by how far the floors moved it **in units of its own local
edge length**:

| strain (move / local edge) | verts moved | net folds | net per 1k moved |
|---|---|---|---|
| < 0.25 | 597635 | +153 | 0.26 |
| 0.25 - 0.5 | 93876 | +2 | 0.02 |
| 0.5 - 1 | 64750 | -80 | -1.24 |
| 1 - 2 | 27616 | +154 | 5.58 |
| 2 - 4 | 8414 | +164 | 19.49 |
| 4 - 8 | 2200 | +15 | 6.82 |
| > 8 | 329 | +32 | 97.26 |

Verts pushed more than one edge length are **4.9% of the moved population and
carry 83% of the net fold rise**, and the rate climbs monotonically. The same
bins by ABSOLUTE move are flat and non-monotonic (1.45 -> 22.81 -> *down* to
6.21), so magnitude is not the axis; strain is.

And the two costs separate. Over the bust band, a cap at one edge length keeps
**84%** of the standoff gain the floors buy, and a cap at two keeps **96%** —
while removing 83% and 48% of the fold cost respectively.

## The producer defect

`_solve_clearance_field` is a screened Jacobi,
`u_i <- (sum_j u_j) / (deg_i + lam)`. Its steady state decays over
`sqrt(deg/lam)` **EDGES** — about 3.5 at deg 6, lam 0.5. Its reach in WORLD
units is therefore that edge count times the edge LENGTH, so a mesh cut ten
times finer gets a tenth of the feather. One flat 20x20u patch, same constraint,
tessellation the only variable:

| edge | reach (u) | max gradient per world unit |
|---|---|---|
| 1.000 | 4.387 | 0.500 |
| 0.500 | 2.693 | 0.905 |
| 0.250 | 1.903 | 1.712 |
| 0.125 | 1.578 | **3.290** |

**Every one of those rows reports `converged=True` at the default 256
iterations.** The pass is not iteration-starved, so `#smooth-reach`'s remedy —
more rings, quadratic in the tessellation ratio — is inert here. That knob was
written for the SCALAR feather, and both callers of that sit below a
`#clearance-field` early return which fires whenever the solve succeeds, with
`CLEARANCE_FIELD_SOLVE` and `CLEARANCE_FIELD_INFLATE` both default ON. So on the
shipped path nothing has ever compensated this solve for tessellation.

In FEM terms it is a units mismatch: `deg` (unit edge weights) is the
dimensionless 2D Laplacian, while `lam` carries an area and must scale as `h^2`.
Written that way the world reach is `sqrt(deg/lam)` with the edge length
cancelling. `#field-screen-physical` scales `lam` by each vertex's own edge
length squared, **clamped at 1 so it can only ever lengthen reach** — a mesh at
or above the reference tessellation is bit-identical to today.

It is also where the defect lives that matters: with a SMOOTH constraint the
ratio is 1.00 in both arms. The tessellation dependence appears only at the
BOUNDARY between constrained and free verts — which is exactly where a clearance
requirement stops, and exactly the rim the authored floor makes more ragged by
relaxing some verts and not their neighbours.

## A feather may not be wider than the surface it crosses

The first build of the fix was measured to *hurt* the finest shapes. Grouped by
the surface's own size — bands taken from the physics (`sqrt(deg/lam)*ref_edge`
is 3.46u), not fitted:

| sqrt(area) | shapes | folds before | delta | |
|---|---|---|---|---|
| < 3.46u (smaller than the feather) | 28 | 535 | **+81** | **+15.1%** |
| 3.46 - 8u | 63 | 1453 | +38 | +2.6% |
| 8 - 16u | 107 | 2010 | **-500** | **-24.9%** |
| > 16u | 462 | 8549 | -231 | -2.7% |

Asking for a physical reach is right only where there is somewhere to spread the
displacement TO. On a piece smaller than the reach the solve couples the whole
part at once, the constraint still pins some of it, and the two fight — on pins,
strings, buckles and small metal trim, which are the parts seen closest.
`sqrt(area)` is what separates them: graph diameter and vertex count come back
with no positive band at all, and a bounding-box diagonal is not monotone (a
garment is a SHELL, so its box is thin whatever its surface measures). Area is
also the only candidate in the same units as the reach, over the surface the
feather actually travels across. `#field-reach-fits-the-piece` floors the scale
at the point where the decay length equals the piece's own size.

Measured, floors OFF in both arms, against the deployed control:

| edge band | shapes | folds before | no guard | GUARDED |
|---|---|---|---|---|
| < 0.2 | 31 | 1388 | **+155** | **-280** |
| 0.2 - 0.5 | 75 | 2892 | -520 | -511 |
| 0.5 - 0.8 | 93 | 2425 | -200 | -199 |
| 0.8 - 1.2 | 75 | 1919 | -29 | -29 |
| > 1.2 | 386 | 3923 | -18 | -18 |
| **TOTAL** | 660 | 12547 | **-612** | **-1037** |

The finest band flips from an 11% regression to a 20% improvement, and every
other band is unchanged to within a fold or two — the guard is inert wherever the
reach already fits, which is what keeps it from being a global re-tune wearing a
guard's name. The worst single riser (a pin, edge 0.123, 2.4u tall) goes +251 to
+23.

## Cost

None. Clean back-to-back on one mod, nothing else running: **control 158.7s,
candidate 152.8s**. The solver does 1.6x the work (mean 63 -> 100 iterations) and
it does not show, because the solve is a small share of convert time and
run-to-run variance is larger. `_field_screen_scale` itself is 2.4ms per shape,
about 1s per pack.

The synthetic worst case is 10x slower but does not occur: fine tessellation and
large vertex counts are anti-correlated in real garments.

## Review pass -- what a second look found

**The `convert_nif_phase2` guard site was wired blind.** `LAYER_ORDER_LAST` is
default OFF, so that call never ran on this population and its FRAME was never
checked -- and a guard measuring a garment in one frame against a body in another
does not fail loudly, it pushes verts by whatever the frame offset is. Forced the
flag on and measured: garment-to-body median **1.86u** there against **1.79u** at
the proven site. Same working frame; the wiring is sound, just dormant.

**The ring count was driven by `scale.min()` -- one vertex.** Over 444 shapes
that sent 247 of them (56%) straight to the 4096 cap, while the same shapes'
median scale asked for no increase at all; median/min is 15x at p50 and 715x at
p90. It costs nothing today because the solve's `tol` break ends it at 100
iterations on average and 543 at worst, so neither bound binds -- but on a
population where `tol` is not reached early it is a 40x bill on behalf of the
finest 1% of a mesh. Now driven off p5, and **verified byte-identical** (1 of 184
files differs, at 0.000000000u, the known serialization noise floor).

**Not changed, deliberately.** `np.add.at` in `_field_screen_scale` is ~0.7% of
convert time and swapping it for `bincount` could reorder float summation, which
is not worth a determinism risk at that size. The per-shape `cKDTree` in the
repair guard is ~1s per mod, and threading a prebuilt tree through would couple
two passes for that.

## Per-island area: built, measured, REJECTED

"A component is not an object" argues the guard should measure a vertex against
its own island rather than its shape, and the population agrees it would matter
-- 60% of shapes carry more than one island, 27% would get a different verdict
per vertex, one boot holding 43 islands whose total reads 16.29u against a
smallest of 0.53u. Built it (islands over the welded graph) and measured:

| | folds | inverted | bust gap | pen |
|---|---|---|---|---|
| floors OFF, per shape | **15318** | **1793** | - | - |
| floors OFF, per island | 15649 | 1910 | - | - |
| candidate, per shape | 15899 | **1902** | +0.431 | 13 |
| candidate, per island | **15740** | 1961 | +0.431 | 13 |

It buys **nothing** on the fit -- gap and penetration identical to three decimals
on both paths -- is clearly worse without the floors, and is a trade with them
(-159 folds for +59 INVERTED, the side the author ships at zero), at 74% more
cost in the helper. So the damage this guard stops is a property of the SHAPE
being small, not of a small island inside a large shape: a stud on a boot has the
boot's solve around it. Kept per shape, and pinned by a test so the rejected
reading is not re-derived.


## What this does NOT close

* The residual fine/coarse gradient ratio floors at ~2.1 however long the solve
  is allowed to run. That is the disc constraint's own kink, which a finer mesh
  legitimately resolves more steeply, not the screen — a smooth constraint
  scores 1.00.
* `#smooth-reach` remains default OFF and remains unmeasured *on its own pass*.
  This work only establishes that it cannot help the field solve.
* `#smooth-reach` remains default OFF and unmeasured *on its own pass*.

## The bust-band penetration is not the floors either

The floors' 5 extra bust-band penetrating verts are all on ONE piece, across
three layered shells. They are NOT tangential sliding: they move 0.308u along the
body normal against 0.067u across it, so the `_relax_conform_field` failure mode
does not apply. Traced with `CBBE2UBE_STAGE_DUMP`, signed standoff along the body
normal:

| stage | Torso v1615 (candidate) | Torso v1615 (control) | Shell 4 v121 |
|---|---|---|---|
| s06_panel_rigidity | -0.3223 | -0.6457 | -1.0497 |
| s07_antipoke | +0.9555 | +1.0652 | +0.3061 |
| panel_rigidity_post / chain_blend / min_push / seam_weld | +0.9555 | +1.0652 | +0.3061 |
| **s12_coherence_repair** | **-0.0214** | **+0.2003** | **-1.0018** |

The garment is held clear by the whole fit chain and then the LAST pass,
`_repair_coherence_collapse`, moves it 0.98u–1.31u inward, through the skin. It
does this **in both arms** — the control's vert survives only because it entered
0.11u higher. So the authored floor does not create the penetration; it spends
the margin that was absorbing a body-blind repair.

And body-blind is literal: `_repair_coherence_collapse(src_verts, out_verts,
tris)` took no body at all. It smooths the displacement field across a buckled
patch and restores the patch's MEAN displacement — which keeps the garment where
the fit put it *on average* while redistributing it per vertex, so a vertex the
anti-poke pushed clear on purpose is just a large outward displacement to be
averaged away. Nothing runs after it to catch that.

`#coherence-repair-outside-body` hands it the body and clamps it one-sidedly: a
vertex may not be moved from outside the body to inside, and one already inside
may not be driven deeper. Everything else the repair asks for is granted. Two of
its three call sites get the body; `_copy_shape` has none in scope and stays a
documented gap rather than a silent half-fix.
