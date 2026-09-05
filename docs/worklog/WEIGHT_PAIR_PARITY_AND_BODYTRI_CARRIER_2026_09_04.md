# Weight-pair parity + the BODYTRI carrier convention (2026-09-04)

Two independent findings from one session. Neither has an in-game verdict yet.

## 1. The generated collision proxy DISAGREES ACROSS THE WEIGHT PAIR

Skyrim blends an armour's `_0` and `_1` meshes per vertex, and ONE `.tri`
serves the pair. Both assume the two files share a topology.

Census of every `_0`/`_1` pair in the shipped output:

| population | pairs | vert-count mismatches |
|---|---|---|
| our output | 1536 | **15** (+2 tri-count-only) |
| hand-authored control | 12 | **0** |

**16 of the 17 are the generated skirt collision proxy.** The generated butt
patch is clean at 41/41, so this is not "generated proxies are unstable" -- it
is one pass. Restricted to pairs that carry a skirt proxy: **14 of 28 (50%)**,
the coin-flip signature of an unstable grid.

### Cause, isolated

`_cluster_decimate` grids on `v.min(0)` and the SPAN of the verts handed to it.
The two weights are the same garment at two body weights, so the cell size and
the cell membership both differ.

The inputs are otherwise identical. Rebuilding the pass's own source pick,
chain mask, triangle set and vertex set for all 14 mismatched pairs: **13 of 14
are byte-identical at both weights**, and the 14th differs only in triangle
ORDER (same set, same checksum). Positions are the only weight-dependent input,
and the decimator is the only pass that reads them.

### Consequences already shipping

- a weight blend over mismatched vertex arrays;
- morph offsets addressing vertices the other weight does not have. Two pieces
  ship a `.tri` whose proxy entry indexes vertex 478 of a 428-vertex shape.

### Fix: `#proxy-weight-invariant` (default OFF)

`_topo_decimate` clusters on the EDGE GRAPH, which is identical across the
pair: strided seeds over a breadth-first traversal, a graph-Voronoi partition,
two Lloyd steps, then fragment absorption. Every decision reads `tris` and
vertex indices only. Representatives stay ORIGINAL vertices, so weights,
skin-to-bone transforms and g2s still copy across with no re-rigging.

Proxy triangles are emitted in a canonical order because the SOURCE triangle
order is itself not stable across the pair.

**A/B, one mod, both arms from source (the flag does not exist in the deployed
exe), run sequentially so they cannot race on the run-state file:**

| arm | pairs | vert mismatches | TRI offsets out of bounds |
|---|---|---|---|
| control (position grid) | 19 | 2 | 2 |
| `PROXY_WEIGHT_INVARIANT=1` | 19 | **0** | **0** |

The control aborts, so the metric can fail.

### The quality trade, measured

Exact point-to-triangle distance from every cloth vertex to the proxy surface
(no sampling bias -- a per-triangle sample would reward the denser mesh):

| | verts | tris | cloth within GAP |
|---|---|---|---|
| position grid | ~460 | 830-1400 | 99.7% |
| invariant, x1.0 cells | ~500 | 500-650 | 95.5% |
| invariant, x1.5 cells | 720-840 | 810-1080 | 98.5% |
| invariant, x2.0 cells | ~900 | 810-1550 | 98.6% |

x1.5 is the shipped operating point: it recovers the coverage with FEWER
triangles than the grid it replaces, so it costs less collision work, not more.
x2.0 buys nothing and is not monotonic.

The grid's remaining 1.2pt is partly not real. **The grid welds
topologically DISCONNECTED patches into one cluster in up to 77% of its
clusters** (65/443, 288/376, 323/417, 287/446 on four pieces); the invariant
partition does it in **0**. A welded cluster's triangles bridge the gap between
the front and back of the cloth, and points on both surfaces score as covered
by that bridge. The proxy is denser because it is stitched shut.

**OWED: an in-game verdict.** It moves geometry on every piece carrying a
generated proxy.

## 2. The BODYTRI carrier docstring does not describe carrier choice

`_pick_bodytri_carriers` prefers the BODY shape and cites "the dominant
hand-built convention (88/93 sampled slot-32 UBE NIFs)".

Re-measured. Population: every hand-authored UBE `_1.nif` (our own output
excluded) that has a BaseShape AND at least one BODYTRI. Exclusions counted:
177 had no BaseShape (not body-slot), 21 had no BODYTRI, 0 failed to load.

| carrier convention | n | share |
|---|---|---|
| EVERY cloth shape, body excluded | 15 | 83% |
| ONE cloth shape, body excluded | 3 | 16% |
| BaseShape | **0** | **0%** |

**18 of 18 exclude the body.** Whatever the 88/93 figure counted, it was not
which shape carries the BODYTRI. The docstring also contradicts itself --
"NioOverride ... applies per-name morphs to every shape in the TRI" and "on a
cloth shape NioOverride often skips other shapes" cannot both hold.

This does not by itself explain the reported symptom, and the mechanism is
still unknown. It removes the stated prior AGAINST testing the author
convention, which was the only reason the test was ranked low.

`#bodytri-carrier-cloth` now tags the garment and excludes the body. Its first
run was a NO-OP: it was wired only into the copy path, and the reported pieces
take the body-swap path. It is now wired at both phase-2 sites, and the check
asserts the arm fired (carriers on a body shape must be 0) rather than assuming
it.

**OWED: the in-game verdict.**

## 3. CORRECTION: section 2's census was a TOOL ARTEFACT, and the real bug is a write-time collapse

The "18 of 18 exclude the body" result above is **wrong**. It was measured with
`pynifly`'s `shape.extra_data()`, which walks by index and **stops at the first
block it cannot build**. A BodySlide body carries a `NiIntegersExtraData`
`LOCKEDNORM` at index 0, so every body shape reported ZERO extra data --
BODYTRI included. The bias landed on exactly one class of shape, which is why
the result looked like a clean systematic finding instead of noise.

Enumerating properly, `get_extra_data(target_index=i)` over `extraDataCount`:

| shape | extra data |
|---|---|
| nude body `BaseShape` | `LOCKEDNORM` + `BODYTRI` |
| hand-built armour `BaseShape` | `LOCKEDNORM` + `BODYTRI` x2 |
| that armour's 14 cloth shapes | `BODYTRI` on every one |
| our body-slot output | **exactly one BODYTRI, on one shape** |

The authored arrangement is the body **and** every cloth shape.

### The producer bug

The write-time re-author in `nif_convert_writer.py` captured a single
`(bodytri_str, bodytri_owner)`, broke out of both loops, and re-attached that
one. **Every other BODYTRI in the NIF was destroyed on save**, and the survivor
was whichever shape came first in `shape_order`. Whatever
`_pick_bodytri_carriers` returned was therefore irrelevant -- one pass silently
undoing another. That is why all 399 body-slot NIFs in the output carry exactly
one tag, and why the cloth-carrier arm measured as a no-op on the pieces that
route through this writer.

Fixed to capture and re-attach ALL of them, enumerating by index. The
BODYTRI-absence validator in the same file had the same unsound scan and is
fixed the same way -- its own comment describes the class it was missing:
"ignores morphs, body reverts to its `_0` shape".

### The model this supports

If NioOverride morphs a shape only when THAT shape carries a BODYTRI:

| tagged | body morphs | cloth morphs | a FULL-COVERAGE garment looks |
|---|---|---|---|
| body only | yes | no | unmorphed cloth over a correct but HIDDEN body |
| cloth only | no | yes | morphed cloth over base skin |
| body + cloth | yes | yes | correct -- what authors ship |

It predicts the measured in-game result that the cloth-only arm changed
nothing (it swaps which half is broken), and it explains why the defect reads
as specific to full-length garments: on a smaller piece the correctly-morphed
body is what you actually see, so a body-only tag hides its own defect.

`#bodytri-all-shapes` (default OFF) ships the authored arrangement. Verified on
one mod: 12 of 12 body-slot NIFs carry BODYTRI on the body, 0 NIFs have an
untagged cloth shape. `#bodytri-carrier-cloth` (cloth only, body excluded) is
REFUTED in game.

### Still open on the injected body

- no `LOCKEDNORM` (both reference files carry one);
- **366 triangles the reference body does not have** -- a strict superset, no
  duplicates, all in z 64.6..70.9 (the groin). The nude body and the
  hand-built armour agree on 56752; we ship 57118.

## 4. IN-GAME VERDICT: the sliders are FIXED

Confirmed in game after a full restart. The root cause was the write-time
BODYTRI collapse in section 3, not the carrier choice. Both carrier arms read
as no-ops because neither survived the writer.

`#bodytri-all-shapes` and the writer fix ship the authored arrangement. Verified
on one mod: 12 of 12 body-slot NIFs carry BODYTRI on the body, 0 NIFs have an
untagged cloth shape (metal trim included -- excluding it would leave the trim
unmorphed on a morphing garment, so it would detach).

## 5. The physics gap: FOUR causes, one of them ours

Reported alongside the slider fix as "physics not there on armors that should
have them". First, a control: the deployed build was diffed against the
previous one over every physics property -- root HDT reference, XML presence,
declared per-vertex / per-triangle shapes, constraint and bone counts, hidden
flags, and XML-declares-a-missing-shape. **0 of 40 files differed.** The slider
work did not touch physics.

The gap then splits, and only the last is a converter defect:

1. **No `[SMP]` outfit exists upstream.** The mod ships each physics-capable
   piece as TWO BodySlide outfits, plain and `[SMP]`; only the `[SMP]`
   ShapeData reference mesh carries the HDT root reference. 8 of 36 outfits had
   physics. Three variants have no `[SMP]` version at all -- nothing can restore
   it.
2. **The plain outfit was the one built.** Three further pieces have an `[SMP]`
   outfit available, but the BodySlide output the converter consumed was built
   from the plain one, so there was no physics reference to carry. A rebuild
   fixes it; the converter cannot.
3. **A "dropped" accessory is not broken.** The crash guard drops non-body
   accessories on ambiguous modder slots, and this was initially misread as
   physics loss. Race coverage mints a UBE-race ARMA pointing at the piece's
   ORIGINAL mesh, which keeps its own HDT reference -- verified: all 169 of the
   mod's ARMOs are SkyPatcher-covered, and the dropped piece's coverage ARMA
   points at the original path. Dropping costs a UBE-shaped refit, not physics
   and not visibility. **The note that led to the misreading gave a bare count;
   it now names the meshes and states that they still ship.**
4. **A pruned XML block was correct.** One piece's authored XML declares a
   collider that neither the source NOR our output has. We prune the dangling
   block and report `hdt_xml_shape_dropped`. Pruning is right: a dangling
   per-triangle-shape can make FSMP reject the whole XML.
5. **OURS: the `_0`/`_1` collider parity defect of section 1.** Two of the three
   pieces that do have physics carried it. `#proxy-weight-invariant` deployed;
   the mod now reports 0 vert-count mismatches and 0 out-of-bounds TRI offsets.

### Note on run-to-run byte variance

Two re-converts at identical flags produced 2 of 65 files differing at the byte
level, with **0 shapes differing in vertices, triangles, bone sets or weight
VALUES** -- one shape's bone LIST order differed. That is the known residual
serialization nondeterminism, not a geometry change.

## 6. THE DRESS PHYSICS CAUSE: the proxy cloned the KINEMATIC GROUND PLANE

Found by taking a piece the user confirmed WORKS and diffing it against a
broken one. That comparison, not further reasoning about the broken piece,
is what produced the answer.

    WORKS  -- every collider: undeclared bones 0; generated proxy <tag>Fabric</tag>
    BROKEN -- generated proxy <tag>ground</tag>, cloned from a 4-vertex ground plane

`_add_skirt_collider_proxy` clones its XML block from a donor per-triangle
shape. Its own comment says the donor must be CHAIN-DRIVEN, because "cloning a
kinematic block here would tag the cloth as a body collider and it would
collide with the wrong things". **The test did not implement the comment.** It
asked only whether the shape carries weight on a bone the BODY lacks -- and a
kinematic ground plane passes, because it is weighted to a skeleton root the
body shape has no weights on.

So the generated proxy inherited the ground block verbatim:

| | tag | margin | penetration | shared |
|---|---|---|---|---|
| ours | `ground` | 1 | prenetration 4 | -- |
| authored | `Fabric` | 0 | -1 | private |

A 589-vertex "ground" was planted inside the character's skirt, on a piece
whose own body collider carries `no-collide-with-tag ground`.

**Census over the shipped pack:** 28 pieces carry a generated proxy; **17 (61%)
were cloned from a non-simulated donor, 12 of them tagged `ground`.** The rest
of the bad tags are `virtuallegs`, `VirtualGroundBelt`, `ColBody`, `Collision`.
This pass is DEFAULT ON and was cleared on ONE piece.

### The fix: `#donor-must-be-simulated`

A bone is chain-driven only if the XML actually SIMULATES it -- it appears as a
`generic-constraint` `bodyA`/`bodyB`. A skeleton root never does. Require a bone
that is BOTH outside the body's own bone set AND a constraint body; where none
qualifies, DECLINE the proxy, which is what the authored file ships for such a
piece.

**Result: the generated XML is now byte-identical to the authored XML** on all
three affected pieces of the reported mod. The one remaining XML difference
anywhere in that mod is the deliberate prune of a dangling collider block whose
shape neither the source nor the output has.

Residual, untriaged: one piece has a valid (simulated) donor whose tag is still
`ground`.

## 7. The coherence gate: one knob REJECTED, one mechanism found MISSING

### `COHERENCE_THIN_AREA_SCALE` 1.0 -> 0.15: censused, REJECTED

The knob shipped inert with "set 0.15 to re-enable ... until censused". Censused:

    pieces 10 better / 8 worse
    folds 10358 -> 10261 (-0.9%)    inverted 843 -> 845
    the REPORTED piece 982 -> 992   (worse)

Leave it at 1.0.

### `#coherence-kink` was documented but NEVER IMPLEMENTED

`_repair_coherence_collapse` carried a full comment for the kink test --
rationale, a worked example, and the instruction to smooth rather than
rigidify. The detector did not exist: `kink` was assigned `False` and never set
`True`, and there were no `COHERENCE_KINK_*` knobs. Its only live effect was on
`if thin_extent < THIN and not kink`.

That mattered because neither surviving gate can see a COHERENT rotation. On the
reported straps: coherence **0.96 -> 0.90** (a drop of 0.05, against a
`COHERENCE_THIN_DROP` of 0.30) with an output coherence of 0.90 (against a
`COHERENCE_OUT_MAX` of 0.30), while the strips turned **68-113 degrees** against
neighbours at 26-48. Every patch fell through both gates.

**A correction worth recording:** the first hypothesis was that the ABSOLUTE
area floor (4.0) excluded finely-tessellated trim. It does not apply here -- two
of the damaged patches measure 8.76 and 8.39, well clear of the floor, and still
failed. The blocking criterion was the coherence definition, not the area.

Implemented as the comment specifies: area-weighted mean turn of the patch
versus the ring of triangles touching it, gated on BOTH an absolute turn
(`COHERENCE_KINK_DEG` 40) and a ratio to its own neighbourhood
(`COHERENCE_KINK_RATIO` 2.0); repaired by smoothing. DEFAULT OFF.

    pieces 9 better / 1 worse / 30 unchanged
    folds 10358 -> 10053 (-2.9%)    inverted 843 -> 783 (-7%)
    the reported shape   239 -> 200 folds (-16%)
    the reported BAND     88 ->  92 (+4)

So: a real win on the class and on the shape, and NOT the fix for the reported
band -- the softcloth levers did that (111 -> 88 against an author baseline of
37). Both are honest partial results.

### Measurement note

Pin a region to a FIXED VERTEX INDEX SET taken from the SOURCE. Re-binning the
moved output positions measures vertices MIGRATING across band edges, not
damage -- doing that reported a different band figure here until it was fixed.

## 8. THE STRAP FIX: folds were the wrong metric, STANDOFF was the right one

Reported again in game after sections 6 and 7 had both moved the fold count.
That is the signal that the metric is wrong, not that the fix was too small.

Measured against the AUTHOR's own build, strap verts to the nearest body vert:

| | median | p90 | max |
|---|---|---|---|
| author (vs the body it was fit to) | 0.32u | 0.48u | **0.83u** |
| ours | 0.34u | 2.24u | **2.55u** |

**The median was already right.** Most of the strap seats correctly; the TAIL is
the defect -- verts standing 1.9-2.5u off the body where the author never
exceeds 0.83u, which is exactly "bumps along the collarbone".

### Cause, explicit in the code

`_inflate_cloth_over_bust_butt` computes `need = clear - standoff` with `clear`
= `_SOFTCLOTH_BUST_CLEAR` = 1.8u, which is BUST JIGGLE HEADROOM. So a strap
vert sitting correctly at 0.3u is **lifted 1.5u**. Headroom is a property of the
BUST, not of every vert the bust band's radius happens to reach -- and the band
is body-space z 93-118, so the collarbone is inside it.

### `#softcloth-seated-cap` (knob, default 0.0 = off)

A vert already OUTSIDE the body may gain at most `SOFTCLOTH_SEATED_CAP` of extra
lift; a vert the body genuinely penetrates is still pushed out to cover, which
is what the pass exists for. Strictly reduces push.

| build | standoff p90 | max | folds region | folds all | inverted |
|---|---|---|---|---|---|
| author bar | 0.48 | 0.83 | 37 | 45 | 0 |
| before | 1.29 | 1.87 | 92 | 200 | 40 |
| **cap 0.3** | **0.66** | **1.04** | **41** | **116** | 26 |
| cap 0.6 | 0.86 | 1.34 | 49 | 148 | 45 |

0.3 lands on the authored bar on every column. Pack: **6 better, 0 worse, 34
unchanged**, folds -4.6%, inverted 783 -> 768.

**LESSON.** When a fold metric improves and the user still reports the defect,
the defect is a POSITION relationship, not a surface-quality one. Measure the
AUTHORED distance and restore it. Three passes were tuned against fold counts
here before the authored standoff was measured -- and that one measurement
named both the cause and its correct magnitude in a single step.

## 9. SHIPPED: promoted, registered, built, deployed

All three defects were confirmed in game, so the seven flags carrying them were
promoted to default ON -- each to the codebase's `CBBE2UBE_NO_*` kill-switch
idiom, each with a `Setting()` row.

**The registration was the load-bearing half.** Six of the seven had NO
`Setting()` row, so they were env-var only: invisible to the GUI and unreachable
from the settings json. The build the user judged good could not be reproduced
by a normal run. A fix nobody can turn on has not shipped.

The suite caught the promotion honestly rather than rubber-stamping it:
`test_no_default_on_flag_still_advertises_itself_as_opt_in` failed on three
stale "OPT-IN, default OFF" headers, and this session's own
`test_proxy_weight_invariant_is_OPT_IN_until_it_has_a_verdict` failed because
the verdict now existed.

**Verified at pure defaults, not asserted.** The deployed exe reconverted the
reported mod with no env overrides; its `conversion_settings.json` records the
build, `dirty=false`, the exe sha, and all seven reading True with one unrelated
non-default. Parity 0/0/0, BODYTRI on the body 12/12, physics present, the
ground-tagged proxy gone. Against the source build judged good in game: 62 of 65
byte-identical, the other 3 geometrically identical (0.000000u; bone-list ORDER
only).

### The risk that was named and then measured

`#softcloth-seated-cap` trades bust jiggle headroom for a seated strap, and that
trade was flagged when it shipped. Measured afterwards, cap OFF vs 0.3 over the
breast band: **2 shapes changed at all, worst median loss 0.021u, and the
MINIMUM standoff -- the number that decides poke-through -- was identical**
(0.188 -> 0.188, 0.219 -> 0.219). It trimmed excess lift where cloth was already
clear, not clearance where the body is close. Caveat recorded: on that mod
softcloth moves 0 verts in the breast core, so it is a weak test of the bust
case; the pack census now carries a `--headroom` mode that answers it properly.

## 10. A GATE THAT CANNOT FIRE: the audit

`#coherence-kink` was found by accident. `scripts/analysis/dead_gate_audit.py`
finds the class -- a local assigned only a constant then read as a condition; a
`_flag`/`_knob` constant nothing references; a live constant with no `Setting()`
row.

It found one: `post_merge_failures`, assigned 0, read by both its warning and
the exit code, never incremented. Less alarming than the name suggests -- the
standalone coverage phases it guarded had been removed and unified coverage
moved somewhere whose rc IS checked -- so nothing live was being swallowed. But
its comment still claimed the guard was live, and the opt-in overlay transfer
did exit 0 on failure. It now counts.

Pinned by `tests/test_dead_gate_audit.py` at 0 and 0, with a CONTROL asserting
the checker finds >200 flag constants -- a checker returning an empty list would
pass both assertions while measuring nothing.
