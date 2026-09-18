# Bust fitment, 2026-09-09 — what the pack reads now, and where a push goes to die

No mod names or machine paths here: this file is tracked and the remote is
public. Pieces are named by their converted subpath only where that is already
public API.

## 0. The state everything below was measured against

    HEAD            a3c0608 (testing), working tree docs/tests + a comment-only src edit
    deployed exe    a22f094, dirty False
    recipe          EMPTY before this session -- only `_known_settings` and the
                    window geometry. The CODE DEFAULTS **were** the shipped
                    configuration, so `flag_surface` output was the recipe.
    suite           2995 passed / 2 skipped, exit captured directly
    pyflakes        undefined 0

**The recipe was re-asserted** on the user's call: the five promoted flags are
written back explicitly, `flag_retirement --recipe` reports 0 disagreements and
0 unclaimed keys, LF preserved, previous file backed up. Effective configuration
is unchanged; what is removed is the silent-revert trap where an exe older than
`a22f094` would default all five off with nothing on disk to object.

## 1. The pack census, re-run on the 09-09 pack

Same tool, same preset, same `--every 2` as the 08-27 and 08-28 runs, so the
three are comparable. The census's own control arm was run first and reproduced
the CLI (preset resolves 183 engaged / 115 resolved / 62.8% in all three).

    ALL                    08-26      08-28      09-09
    examined                 768        768        768
    scored                   257        258        259
      excluded band_not_covered 500     499        498
      excluded no_rendered_shape 10      10         10
      excluded band_too_small     1       1          1
    bind  > 0.05%       34 (13%)   31 (12%)   18  (7%)
    MORPH > 0.05%      104 (40%)  101 (39%)   83 (32%)
    MORPH > 1.0%        66 (26%)   66 (26%)   46 (18%)
    CLASS               72 (28%)   72 (28%)   65 (25%)
      chord candidate       55         55         62
      follow gap            13         13          0
      physics cloth          4          4          3
    worst piece           74.4%      74.4%      32.0%

The gain concentrates on the COPY path: bind clip 24 (21%) -> 6 (5%), morph>1%
34 (30%) -> 18 (16%). **The `follow gap` bucket is empty for the first time**,
which is why `chord candidate` rises while the class as a whole falls: the
pieces that used not to follow at all now do, and re-sort into the chord bucket.

Attribution caveat, and it is not a small one: the 09-09 pack differs from the
08-28 pack by the whole `a22f094` build, not by one flag. Nothing here is
attributable to a single change.

## 2. `#phase1-bust-clearance` did NOT shrink the chord class — already measured

The 2026-08-28 pre-reconvert baseline directory holds BOTH arms of exactly this
A/B, and its `BASELINE.json` records that the two packs differ by ONE setting.
So the number the handoff called missing has existed since 2026-08-29:

    copy path                 flag OFF     flag ON
    CLASS                     31 (28%)     31 (27%)     UNMOVED
      chord candidate              25           25      UNMOVED
    bind  > 0.05%             24 (21%)     21 (19%)     moved
    MORPH > 0.05%             53 (47%)     50 (44%)     moved
    MORPH > 1.0%              34 (30%)     34 (30%)     unmoved
    swap path, every row       identical            correct: it is copy-only

The charge reaches the copy path and does real work on BIND penetration and on
the worst pieces' magnitude. It does not move the class it was built for.

**That A/B has no repeat control** -- it predates the XML-resolution race being
known -- so a 3-piece delta is not safely above noise. Do not quote it as a
precise size.

## 3. The copy path's anti-poke is BUST-BLIND, and no flag fixes it

The phase-1 call site passes no `body_nipple` and no `morph_amplitude`. Inside
`clear_armor_outside_body`, `_in_bust` is assigned only under
`if body_nipple is not None`, so on the copy path there is:

  * no bust floor,
  * no adaptive morph ramp (`req` falls to a flat `ANTIPOKE_FLAT_CLEAR`),
  * no jiggle term, no authored relaxation, no layer `req_extra`.

`#phase1-nipple-map` does NOT close this. Its only use site is the copy-path
CONFORM, ~400 lines after the anti-poke call, and its own comment says "both
copy-path conform sites". So the gap survives with the flag on.

This is the cleanest available explanation for the two paths carrying OPPOSITE
defects -- body-swap over-standoff (+0.682 gap) against copy-path
under-clearance (historically 21% bind clip) -- and for why one knob keeps
moving them in opposite directions.

## 4. `#antipoke-surface-req` — BUILT, MEASURED, DEFAULT OFF, and the reason

The third site of the vertex-vs-surface gap. `#bust-surface-req` closed it in
the conform, `#panel-rigid-surface-guard` in panel rigidity; the anti-poke --
the pass whose whole job is "no body through the garment" -- still asks per
VERTEX. Reuses `_surface_deficit` unchanged, which until now had exactly ONE
caller despite a docstring saying it is region-agnostic and exists to have two.

Reachability was checked BEFORE building, because this band's history is full of
floors that could not bind:

    tris reaches the pass?   YES -- `CLEARANCE_FIELD_SOLVE` resolves ON, and the
                             call site passes tris when SMOOTH **or** FIELD is on
    `_surface_deficit` reusable as-is?   YES, all inputs in scope
    reported piece on a path that gets a nipple map?   YES, it is body-swap

THE SURFACE IS HELD TO ITS OWN LOW BAR (0.1u), NOT TO `req`. `req` on a bust
vertex runs ~1.15u, and a surface sags below the vertices spanning it, so
holding the surface to `req` would lift those vertices PAST `req` and the term
would act as a standoff ramp -- against a pack whose reported in-game defect is
"sits too far off the body". Held to its own bar the push is bounded by the SAG,
so the term is exactly zero wherever the body is not coming through. That is the
zone selectivity this band requires; a global ramp is already measured to make
clipping WORSE under morph.

Unit behaviour, on a triangle whose corners all pass the vertex test:

    body pokes through the surface     OFF 0 verts   ON 3 verts, exactly 0.200u
    surface already clear              OFF 0 verts   ON 0 verts

**IT FIRES ON THE REAL PIECE AND CHANGES NOTHING.** Armed, the reported cuirass
logs `raised 15 vert(s), max +0.5000u over 495 bust vert(s)` -- and the WRITTEN
NIF is identical: 0 of 8867 verts moved on the shape carrying 100% of the clip
area. Every downstream pass reports byte-identical telemetry in both arms.

`DisplacementSurvival` names the absorber rather than leaving it inferred:

    pass                  moved   survival  frac_cancelled  frac_kept  cancelled_by
    antipoke               8540     0.7279       13.1%         62.9%   panel_rigidity_post -0.2359
    panel_rigidity_post    7613     1.0364        0.1%         98.8%   min_push
    conform                3937     0.3056       29.1%          6.0%   panel_rigidity      -0.5398
    inflate                8867     0.7015       46.9%         40.3%   conform             -0.7433
    warp                   8867     0.5562       35.2%         33.7%   panel_rigidity      -0.5731

The anti-poke keeps 63% of its verts and loses 13.1% outright; the surface verts
land in that 13.1%. **The anti-poke is the wrong site while a re-rigidification
runs after it** -- the `#pass-damage-ledger` oscillation, with a number.

THE GENERAL FORM, and it is the useful part: every guard downstream of the
anti-poke is a ZERO-CROSSING guard, not a clearance-preserving one.
`#panel-rigid-surface-guard`'s floor is `min(clear_of(Q), 0.0) - 1e-4`, so a
point standing 0.5u clear may be pulled to 0.000u and still pass;
`#coherence-repair-outside-body` clamps one-sidedly about the BODY, not about
the clearance. So any clearance an early pass adds is free to be spent by the
passes after it, down to but not through the skin. That is a candidate
explanation for why the vertex ramp has to ask 1.15u to deliver 0.90u -- the
over-standoff as compensation for cancellation. **Unproven; stated as the
hypothesis it is.**

Kept default OFF, with the trace on while it is opt-in, because an armed pass
that silently does nothing is this band's recurring failure and the INERT reason
deserves to print. `tests/test_antipoke_surface_req.py` (9) pins the default in
a scrubbed subprocess, the firing, the DISCRIMINATION, both inert paths, that
the knobs are not bare literals, and a mutation control proving the firing
assertion can fail.

## 5. THE REPORTED NIPPLE DEFECT HAS REGRESSED 2.6x, AND THE CAUSE IS MEASURED

The reported cuirass, same tool and band that recorded it on 2026-08-29:

                              recorded 08-29     shipped 09-09
    BIND clip                      2.040             5.316

The single-piece harness reproduces the shipped pack EXACTLY (5.316 / 9.365), so
the arms below are comparable to what ships.

Arming BOTH authored floors off -- never one, the pairing rule -- returns it:

                        floors ON (shipped)   floors OFF
    BIND clip                 5.316              2.045
      coincident              0.795              1.265
      shallow                 3.966              0.781
      BURIED                  0.555              0.000
    cover%                   94.68              97.95
    MORPH clip                9.365              6.680

**2.045 against 2.040 on record.** The floors relax clearance toward the
author's tighter spacing, which is what closes the bust gap -- and at the nipple
it buries surface. `buried` going 0.555 -> 0.000 is the whole finding in one row.

This is the SAME trade as the tip exemption, seen from the other end: author
fidelity against tip clearance. It is one piece; the population gate is the
instrument that turns it into a decision, and the floors' own population record
was taken on the GAP metric, never on bind clip for this class.

**Nothing was flipped on this evidence.** The floors were reverted on the user's
call on 2026-09-05 and re-enabled on 09-06; the decision is theirs and it is now
one they can take on a number.

## 6. Do not re-derive these

  * The `--every 2` bust class census is
    `scripts/analysis/band_class_census.py`, NOT `pack_census.py`.
    `pack_census` has no band selection, applies no morph, casts no ray, and
    never labels a convert path; its only bust code is a `--headroom` block
    with z 90-102 re-derived by hand rather than taken from `body_zones`.
    **The census was PROMOTED out of a gitignored scratchpad on 2026-09-09**
    (it had been cited as toolkit by three sessions while unreachable). Old and
    new were run over the same 150-candidate sample first: 49 scored rows each,
    **0 differing**. Its `--control` no longer needs local knowledge, and its
    floor message was reflowed because the split string made the generated
    `TOOL_MAP` advertise the tool as UNFLOORED.
  * `band_class_census.py` splits paths by the NAME `BaseShape` alone.
    `bust_gap_score` uses name AND vert count, which is the safer test -- the
    pack holds 2 authored `BaseShape` shapes at a non-modal vert count.
  * A pipe masks the exit code. `grep ... | head` reports head's status; the
    checker's own exit has to be captured directly.

## 7. THE POPULATION GATE SAYS **FAIL** -- AND IT CANNOT SEE THE DEFECT

Three arms, 186 NIFs each, from the batch harness at the live recipe.

**THE REPEAT CONTROL IS EXACT.** Two arms at identical settings scored against
each other: every row equal, `files with a vertex moved: 0`, RESULT PASS. So the
noise floor on this population is ZERO and anything the candidate moves is the
override. The control also reproduces the recorded reference exactly -- folds
15974, inverted 1937, gap +0.682 / +0.194, pen 12/0, tip 1.244 / 0.454.

**BOTH AUTHORED FLOORS OFF -- RESULT: FAIL, 6 rows.** The candidate's echo,
`active flags (2): NO_AUTHORED_ANTIPOKE=1, NO_AUTHORED_INFLATE=1`, is the proof
it was armed.

    row                              control   candidate   verdict
    folds                            15974.0     15358.0   ok    (-616)
    inverted                          1937.0      1788.0   ok    (-149)
    bust gap vs author, body-swap      0.682       0.660   ok
    bust gap vs author, COPY PATH      0.194       0.304   FAIL
    bust-band pen, both paths           12/0        12/0   ok
    tip clearance p50                  1.244       1.205   FAIL
    tip clearance p05                  0.454       0.466   ok
    PIECES TIGHTER AT THE TIP              0          12   FAIL
    stretch rate p50 (% of edges)     0.1495      0.1706   FAIL
    stretch rate p90 (% of edges)     2.0217      2.4423   FAIL
    edge deviation p50                0.0331      0.0329   ok
    posed follow, worst median delta       0       0.055   FAIL
    files with a vertex moved              -         112   info

So turning the floors off is NOT a clean win even before coverage is considered:
it buys surface (folds -3.9%, inverted -7.7%) and the tip TAIL, and it gives back
the copy path's entire gap win (+0.194 -> +0.304, i.e. back to the old build's
+0.297) plus the tip MEDIAN and 12 pieces tighter at the tip.

### AND THE GATE'S POPULATION IS BLIND TO THE CLASS

The reported piece is not in the acceptance population at all. Matching the
census's bind-clip class against the arm BY PATH TAIL -- never by basename, which
repeats across mods and scored 5 by pure coincidence:

    bind-clip class (> 0.05%) on the 09-09 pack        18 pieces
    of those present in the acceptance population       2
    of those with the REPORTED defect's profile         0

The one substantial member that IS in the population behaves differently and is
unmoved by the flag (8.832 -> 8.918), because its clip is `buried 7.967` -- a
garment sitting inside the body -- against the reported piece's `shallow 3.966 /
buried 0.555`, a surface grazing the nipple. Different defect, same row.

**THE GATE MEASURES THIS CHANGE'S COSTS AND NONE OF ITS BENEFIT.** That is a
property of the POPULATION, not of the gate's rows, and it is the finding to act
on: 9 of the 18 come from ONE author's mod family, which is absent from the
acceptance set entirely. Every bust verdict taken on this population inherits the
blind spot.

### CONCLUSION, and it is deliberately narrow

**Do NOT turn the authored floors off globally.** The gate fails them on six
rows, the copy-path regression alone is larger than the reported win, and the
in-game verdict on this pack ("sits too far off the body") points the same way.

The defect needs a TARGETED fix, and the gate needs population coverage before it
can judge one. Two concrete next steps, in order:

  1. **Add class members to the acceptance population.** Until it contains
     pieces with `shallow` bind clip at the nipple, no arm can show a benefit
     here and every candidate will score as pure cost.
  2. **The tip harness does NOT score the region the fix acts on, though its
     docstring says it does.** Measured on the UBE body (wmax 0.5635):

         >= 0.75*max   what `nipple_clearance` SCORES        1020 verts
         >= 0.50*max   where `#authored-nipple-exempt` STARTS 1294 verts
         the 0.50-0.75 band, scored by NEITHER                 274 verts (+27%)

     and the converter then DILATES its mask by `AUTHORED_NIPPLE_RADIUS` 2.0u
     and `AUTHORED_NIPPLE_RINGS` 2, so the protected region is wider still. The
     harness docstring claims its mask "is the SAME mask `#authored-nipple-exempt`
     exempts in the converter ... this harness has to score the exact region the
     fix acts on". It is not the same mask, in three independent ways. Fix the
     claim or the constant before tuning `AUTHORED_NIPPLE_EXEMPT` again.

     **CORRECTION, and it retracts an earlier line in this file.** A previous
     draft said the reported piece's escaping verts "sit BELOW the tip metric's
     sampling band", reading BUG-16's raw `nipple weight > 0.5` against the
     metric's `0.75 * max` as if max were 1.0. **The body's max is 0.5635**, so
     those verts sit at ~89-96% of max -- INSIDE the set the metric scores, not
     below it. The two metrics disagree on that piece while looking at the SAME
     verts, so the disagreement is a REDISTRIBUTION (the floors push the tip
     further out on average while opening local dips the body comes through),
     not a sampling gap. The exemption is also firing there (`tipw=374.0`), so
     it is not a threading gap either.

## 8. THE POPULATION WAS EXTENDED, AND THE ANSWER CHANGED SHAPE

Acting on section 7: the acceptance population went from 6 mods / 184 NIFs to
10 mods / 479 NIFs, chosen to carry the bust bind-clip class. **Coverage of that
class went 2 of 18 -> 14 of 18, and ALL FIVE shallow-profile members are now in,
including the reported piece.** Mod names live in the population memory, not
here.

**THE REPEAT CONTROL IS EXACT ON THE BIGGER POPULATION TOO** -- every row equal,
`files with a vertex moved: 0`, PASS. So the noise floor is still zero and the
candidate's deltas are still the override.

### The new reference figures. THE OLD ONES DO NOT TRANSFER.

    row                          OLD pop (184)   NEW pop (479)
    scored NIFs / garments          143 / 299       349 / 512
    folds                             15974           30542
    inverted                           1937            2612
    BODYTRI on the body                  56             118
    bust gap, body-swap               0.682           0.553
    bust gap, copy path               0.194           0.197
    bust-band pen, body-swap             12             108
    bust-band pen, copy path              0              51
    tip clearance p50                 1.244           1.071
    tip clearance p05                 0.454           0.105
    stretch rate p50 / p90    0.1495 / 2.0217  0.1089 / 1.7021
    edge deviation p50               0.0331          0.0308

**The population was hiding the defect, and the numbers say how much.**
Bust-band penetration goes 12 -> 108 and 0 -> 51; tip clearance p05 collapses
0.454 -> 0.105. Those pieces were always in the pack -- the gate simply had none
of them. Any pre-09-09 figure quoted against a post-09-09 arm is a false
comparison.

### Re-scoring the authored floors on a gate that can SEE the class

    row                        OLD pop verdict        NEW pop verdict
    folds                      15974 -> 15358  ok     30542 -> 29792  ok
    inverted                    1937 ->  1788  ok      2612 ->  2545  ok
    bust gap, body-swap         0.682 -> 0.660  ok     0.553 -> 0.563  FAIL
    bust gap, copy path         0.194 -> 0.304  FAIL   0.197 -> 0.329  FAIL
    bust-band pen, body-swap       12 ->    12  ok      108 ->   102  ok
    bust-band pen, copy path        0 ->     0  ok       51 ->    50  ok
    tip clearance p50           1.244 -> 1.205  FAIL   1.071 -> 1.078  ok
    tip clearance p05           0.454 -> 0.466  ok     0.105 -> 0.100  ok
    pieces tighter at the tip       0 ->    12  FAIL      0 ->    28  FAIL
    stretch rate p50           0.1495 -> 0.1706 FAIL  0.1089 -> 0.1072 ok
    stretch rate p90           2.0217 -> 2.4423 FAIL  1.7021 -> 1.9157 FAIL
    posed follow, worst median      0 -> 0.055  FAIL       0 -> 0.055  FAIL
    RESULT                     FAIL, 6 rows           FAIL, 5 rows

**FOUR ROWS FLIPPED, and they are the ones that matter here:**

  * **tip clearance p50 flipped FAIL -> ok.** On the blind population, removing
    the floors looked like it made the median tip WORSE (1.244 -> 1.205). On the
    population that actually contains the class it makes it BETTER
    (1.071 -> 1.078). The old reading was an artefact of which pieces were in
    the room.
  * **bust-band penetration now IMPROVES on BOTH paths** (108 -> 102, 51 -> 50).
    It could not move before: the old population's pen was 12 and 0, so there
    was essentially nothing there to fix.
  * **stretch p50 flipped FAIL -> ok.**
  * **bust gap body-swap flipped ok -> FAIL** (0.553 -> 0.563). The added pieces
    are ones where the floors genuinely buy author fidelity.

### THE VERDICT IS UNCHANGED, THE REASONS ARE NOT

**RESULT: FAIL, 5 rows. Do NOT turn the authored floors off globally** -- the
recommendation from section 7 survives a gate that can see the defect, which is
the strongest form it has been tested in.

But the objection is now a DIFFERENT one, and it is sharper. Removing the floors
buys penetration and the tip MEDIAN, and costs:

    author fidelity on BOTH paths   +0.010 body-swap, +0.132 copy
    28 pieces TIGHTER AT THE TIP    (was 12 on the blind population)
    stretch p90, posed follow

**`pieces tighter at the tip: 28` is the row to attack.** It is gated at zero,
it is the largest single objection, and it is a REDISTRIBUTION rather than a
uniform loss -- the median tip improves while 28 pieces get tighter. A fix that
protected those 28 would leave a candidate failing on author fidelity alone,
which is the trade the user already has in front of them for the tip exemption.

## 9. THE REDISTRIBUTION HYPOTHESIS IS REFUTED, PER-PIECE AND PER-RAY

Section 8 left "the floors lift the tip on average while opening local dips" as
the standing explanation for the reported piece reading BETTER on bind clip and
WORSE on tip clearance with the floors off. **It is wrong.** Tested on the
reported cuirass with the tip harness's own primitives, two arms differing only
by both authored floors.

CONTROL FIRST: the rays are cast from the CANONICAL body, and the piece's
injected `BaseShape` is BYTE-IDENTICAL to it in both arms (29298 verts,
max|delta| 0.00000). So ray i is the same body vertex in both arms and the
pairing is exact, not approximate.

### Paired on the rays that strike garment in BOTH arms

    delta = OFF - ON        tighter 156 | looser 0 | flat 0   mean -0.3364u

**Not mixed. 156 of 156 move the same way**, which is the opposite of what
redistribution predicts. And the effect is MONOTONE in the starting clearance:

    quartile of ON clearance     ON p50    mean delta (OFF - ON)
      Q1 tightest                0.3492          -0.2483
      Q2 mid                     0.4629          -0.3110
      Q3 mid                     0.5447          -0.3521
      Q4 loosest                 0.6543          -0.4341

The floors lift EVERY ray and lift the already-loosest ones MOST. That is a
uniform outward push with a gradient, not a reshuffle.

### So cast the ray the harness never casts

A tip the body has pushed THROUGH the garment has no garment in FRONT of it: its
ray misses, and it leaves the sample silently. Casting inward as well partitions
every tip vertex:

    arm                    garment IN FRONT      body EMERGED      uncovered
    floors ON  (defaults)    371 (36.4%)          649 (63.6%)          0
    floors OFF (candidate)   473 (46.4%)          547 (53.6%)          0

    fixed by removing the floors  (emerged ON -> in front OFF)   317
    caused by removing them       (in front ON -> emerged OFF)   215
    NET                                                          +102 covered

**THE MECHANISM IS A CLEARANCE-vs-COVERAGE TRADE, not a redistribution.** The
floors hold the garment ~0.37u further off the tip, and from further off the
same patch has garment in front of 102 FEWER tip vertices -- so the body emerges
around it. That is why the SAME arm reads better on tip clearance and worse on
bind clip (5.316 vs 2.045), and the two metrics were never in conflict.

**KEEP THIS CAVEAT ATTACHED:** the 63.6% is a property of this tip-ray
construction over 1020 apex vertices. It is NOT comparable to a bind-clip
percentage, which is an area share of the whole z90-102 band. Only the DELTAS
between arms are the result here.

### AND THE GATED ROW COMPARES TWO DIFFERENT SAMPLES

`clearance()` returns `d[hit]`, so the per-piece delta is a difference of
medians over each arm's OWN surviving rays. A piece whose garment covers MORE of
the nipple admits more (tighter) rays and its median falls -- **it scores as
"tighter" for getting better.** On this piece:

    per-piece delta, as the gate computes it   -0.1424u   (n 371 vs 473)
    over the SAME rays                         -0.3665u

Direction survived, size did not, and the coverage change was invisible.

**FIXED WITHOUT TOUCHING THE VERDICT.** `clearance_full()` returns the
unfiltered `(distance, hit)` pair; `clearance()` is unchanged, because every tip
figure on record was taken with its filtering. The harness now prints a PAIRED
block as INFO -- per-piece closer/further/level over shared rays, plus the
"rays with garment IN FRONT" count per arm. **Its vocabulary is deliberately
distinct**: `acceptance.tip()` greps `tighter\s+(\d+)` over the whole output
and takes the FIRST match, so a second "tighter N" line would silently re-gate
the run on the wrong number -- and the row it feeds is gated at zero.
`tests/test_nipple_clearance_mask.py` pins that the block cannot emit either
word, that the gate's regex still resolves to the unpaired count, and that
`clearance()` still filters.

**WHAT THIS DOES NOT SETTLE.** Whether the paired view changes the population
verdict is UNMEASURED -- it needs the extended arms rebuilt, and only the
reported piece was available here. The 28-tighter-tips row stands until then.

## 10. ARMS REBUILT AND RE-SCORED. THE ROW IS RIGHT; MY METRIC FINDING IS SMALL.

Section 9 left one thing unmeasured: whether the paired view changes the
population verdict. It does not.

**THE WHOLE 10-MOD BUILD IS REPRODUCIBLE ACROSS A SESSION RESTART.** Three arms
rebuilt from scratch; the repeat control and the verdict reproduce the recorded
figures to EVERY DECIMAL -- folds 30542 / inverted 2612, gap 0.553 / 0.197, pen
108 / 51, tip 1.071 / 0.105, stretch 0.1089 / 1.7021, and the candidate's
0.563 / 0.329 / 28 / 1.9157 / 0.055. Repeat control PASS, 0 files moved.

### The paired view, at population scale

    UNPAIRED (the gated row)  tighter 28   looser 32   flat 16   median +0.0013u
    PAIRED   (shared rays)    closer  29   further 33   level 14   median +0.0013u
    tip rays with garment in front   control 67559 -> candidate 67875  (+316)

    counted TIGHTER by the gate                     28
      GENUINELY tighter on the SAME rays            28
      coverage artifact                              0
    counted LOOSER by the gate                      32
      actually tighter on the same rays (hidden)     0

**ALL 28 ARE REAL. No artifacts, no hidden regressions in either direction.**

**SO THE METRIC DEFECT IS TRUE AND SMALL.** It bites only where a garment
partially covers the tip, and population coverage moves 0.47% (+316 of 67559).
Of 76 scored pieces, 68 have all 1020 rays in front in BOTH arms, where the
paired and unpaired deltas are IDENTICAL by construction. The handful that
differ:

    piece                              unpaired    paired   rays C -> D
    the REPORTED cuirass                -0.1424   -0.3665    371 -> 473
    its loincloth                       -0.1343   -0.3402    384 -> 504
    a cap (coverage LOST)               -0.0273   -0.0614    407 -> 357
    a dress, partial cover              -0.1229   -0.1111    253 -> 258
    its sibling dress                   -0.0930   -0.0964    352 -> 371
    a second cuirass                    -0.0105   -0.0128   1020 -> 1003

It changes MAGNITUDES, never a verdict. The fix stays (it is correct, it is now
visible, and on the reported piece the gated row understates by 2.6x), but it
does NOT reopen the decision, and nothing here should be quoted as if it did.

### THE VERDICT IS UNCHANGED, AND NOW IT HAS SURVIVED THE SHARPER TEST

**RESULT: FAIL, 5 rows. Do NOT turn the authored floors off globally.** The
largest objection -- 28 pieces tighter at the tip -- is not an artifact of how
the row is computed. It is the real geometry.

**AND THE TRADE LANDS ON THE REPORTED PIECE ITSELF.** The reported cuirass has
the LARGEST paired tip loss in the whole population (-0.3665u) and is the piece
whose bind clip the floors-off arm most improves (5.316 -> 2.045). The defect
the user reported and the gate's biggest objection are the same garment pulling
in opposite directions. Any fix here has to buy coverage at the tip WITHOUT
spending tip clearance -- which is what `#antipoke-surface-req` was aimed at
(section 4) before `panel_rigidity_post` ate it.

## 11. `#panel-rigid-keep-clearance` -- BUILT, MEASURED, FAILS. DEFAULT 0.0.

Section 4 left the anti-poke's push being spent by `panel_rigidity_post`. This
is the lever for that, and the population refuses it.

### The budget, measured before building anything

Stage dump on the reported cuirass, bust band, 477 verts, using the SAME
one-nearest-vertex measure the guard itself uses:

    s07_antipoke              p50 1.8141   p05 1.1654   min 0.6719
    s08_panel_rigidity_post   p50 1.7581   p05 0.3930   min 0.0007
    s09..s12                  no further change to bust clearance at all

    anti-poke ADDS   238.32 u-verts     this pass SPENDS 94.42 (40%)
    334 of 477 verts (70%) lose clearance; p50 0.1907u, max 1.2862u

The median barely moves while the TAIL is destroyed -- p05 -66%, minimum 0.0007u
-- which is exactly what `floor = min(clear_of(Q), 0.0) - 1e-4` permits.

### The lever, and its safety property

`PANEL_RIGID_KEEP_CLEARANCE` raises the floor to `clear_of(Q) * keep` on
vertices OUTSIDE the body, applied at BOTH floors (vertex test and surface
samples). **At 0.0, `where(c > 0, c*0, c)` IS `minimum(c, 0)`** -- the OFF path
is unchanged arithmetically, not by measurement. It only ever REFUSES
rigidification, never pushes, so it stays out of the `#panel-rigid-bust-hold`
class that was removed for breaking a clean preset.

Confirmed at population scale: the control arm built with this code in place
reproduces every recorded reference figure exactly (folds 30542, inverted 2612,
gap 0.553 / 0.197, pen 108 / 51, tip 1.071 / 0.105, stretch 0.1089 / 1.7021).

### ONE PIECE SAID YES. THE POPULATION SAID NO.

    keep    pre bind    pre morph       keep=0.5 through the GATE
    0.0       5.316       9.365         folds        30542 -> 30796  FAIL
    0.25      4.513       7.921         inverted      2612 ->  2762  FAIL
    0.50      4.320       7.371         gap body-swap 0.553 -> 0.562 FAIL
    0.75      4.039       6.825         pen body-swap  108 ->   114  FAIL
                                        pen copy        51 ->    74  FAIL
                                        tip p50      1.071 -> 1.046  FAIL
                                        tighter tips     0 ->     8  FAIL
                                        stretch p50/p90        both  FAIL
                                        posed follow     0 -> 0.068  FAIL
                                        RESULT: FAIL, 10 rows

A four-arm monotone trend on one garment was still one garment
([[feedback_no_single_piece_fixes]]) -- and this file had already quoted that
rule before walking into it.

### THE PART THAT IS NOT RESOLVED, AND IS NOT ALLOWED TO BE HAND-WAVED

The morph-clip census over both arms says `keep=0.5` IMPROVES clipping:

    ALL n=92 scored        control      keep=0.5
    bind  > 0.05%         23 (25%)     18 (20%)
    MORPH > 0.05%         46 (50%)     41 (45%)
    swap multi-shape median clipping   0.251% -> 0.039%

So the gate's PENETRATION rows rise while the census's CLIP rows fall. A draft
of this section explained that away as "they measure opposite directions --
penetration is garment-inside-body, clip is body-through-garment, so both can
move". **THAT WAS PUT TO AN ADVERSARIAL CHECK AND REFUTED.** Read as code:
`bust_gap_score` counts garment verts with `signed < -1e-4` (cloth behind skin);
`standoff_audit` scores `clip = in_hit & ~out_hit & same` (garment surface under
that skin vertex). **They flag the SAME relation with the SAME sign of bad**,
from two different point sets. There is no directional licence here.

They can still legitimately diverge, and the candidate reasons are specific:
clip requires `~out_hit`, so on a layered piece cloth can sit under skin while
an outer layer still answers the outward ray; clip is BODY-AREA weighted with a
5u truncation, an occlusion gate and a normal-agreement test, while penetration
is an unweighted GARMENT-vert count with no distance cap; and the two build
their bands differently. **None of those has been measured on this arm, so the
divergence is UNEXPLAINED and must be quoted that way.**

**THE DECISION DOES NOT DEPEND ON IT.** Even discounting both penetration rows
entirely, the candidate still fails on folds, inverted, bust gap, tip p50,
pieces tighter at the tip, both stretch rows and posed follow. **DEFAULT STAYS
0.0.**

### AND A GAP IN THE GATE, VERIFIED

The acceptance gate runs exactly six scorers -- `pack_census`,
`bust_gap_score`, `nipple_clearance`, `zero_weight_pair_ab`, `stretched_edges`,
`follow_bands` -- and **not one of them applies a body preset or slider morph.**
`follow_bands` poses a SKELETON, which is not a morph. So the gate has NO
morphed-clip row at all, while four tracked tools that measure it
(`morph_clip_test`, `band_class_census`, `morph_sweep`, `preset_ab_score`) are
wired into nothing.

`morph_clip_test`'s own header says it: "Every clip number in this project is
taken at BIND POSE, and bind pose is not what ships."

**CONSEQUENCE: a candidate that improves morphed clipping -- the defect the user
actually reports -- and costs any surface or penetration row can only ever FAIL,
because the benefit has no row to appear in.** That is the same shape as the
population blind spot in section 7, one level up: there the population could not
see the class, here the ROW SET cannot see the metric. Whether to wire a
morph-clip row into the gate is a decision, not a cleanup: it would change what
every future verdict means.

## 12. THE MORPH-CLIP ROW IS WIRED INTO THE GATE

Section 11 verified the gap: the gate ran six scorers and NOT ONE applied a
slider, so it had no row for body-through-garment under a body preset -- the
form every in-game bust report arrives in. That is now closed.

    morph clip > 0.05% (share)   band_class_census   candidate <= control
    morph clip > 1.0%  (share)   band_class_census   candidate <= control
    bind  clip > 0.05% (share)   band_class_census   candidate <= control
      ^ pieces scored for clip                       info (the denominators)

**SHARES, NOT COUNTS** -- the rule the stretch rows already follow. A count
moves when the scored population moves, so a piece dropping out would read as a
quality change. Rounded to 4 dp for display AND comparison: a raw share printed
as 17 significant figures and ran the control and candidate cells together into
one unreadable number, and 4 dp is finer than one piece in any population this
gate scores (1 of 92 is 0.0109).

**IT SKIPS LOUDLY, NEVER VANISHES.** The preset is a modlist file and cannot be
named in tracked content, so the rows need `CBBE2UBE_CLIP_PRESET`. Unset, not a
file, a census ABORT, or an ASYMMETRIC refusal where only one arm scored -- each
prints SKIPPED with its reason and never reads as ok. A row that disappears
reads as a pass.

**THE PARSER ANCHORS ON THE `ALL` BLOCK.** The census prints `swap`, then
`copy`, then `ALL`; a first-match parser would score one convert path as if it
were the whole pack (14/58 instead of 23/92). Pinned by a test that asserts the
swap numbers specifically are NOT what comes back.

### THE ROW IS DETERMINISTIC -- the test that decides whether it stays

Two arms at identical settings, clip rows armed:

    morph clip > 0.05% (share)     0.5      0.5     ok
    morph clip > 1.0%  (share)  0.36956  0.36956    ok
    bind  clip > 0.05% (share)    0.25     0.25     ok
      ^ pieces scored for clip       92       92    info
    RESULT: PASS

Exactly equal on all three, and every pre-existing row still reproduces its
reference. A row that varied between identical arms would fail the gate at
random, and would have come straight back out.

### REFERENCE FIGURES (extended population, `--every 1`, one preset)

    pieces scored for clip   92
    morph clip > 0.05%       0.5000    morph clip > 1.0%   0.3696
    bind  clip > 0.05%       0.2500

### COST, AND THE KNOBS -- AND THE STRIDE HAS A FLOOR

One census per arm -- roughly +12 min per arm at `--every 1` on 479 NIFs, so a
full gate run goes from ~15 to ~40 minutes. `CBBE2UBE_CLIP_EVERY` strides the
population (both arms identically, so they stay comparable) and `--no-clip`
skips the rows for a quick structural A/B.

**BUT STRIDING TOO FAR SILENTLY TURNS THE ROWS OFF, AND THAT IS BY DESIGN.**
Measured on this population:

    --every 1    92 scored    rows JUDGED          measured
    --every 2    ~46 scored   probably judged      NOT MEASURED, inferred by
                                                   halving; the floor is 40, so
                                                   it sits close to the edge
    --every 4    19 scored    census ABORTS,       measured
                              rows SKIP, PASSES

At `--every 4` the census refuses -- "only 19 scored, floor is 40 -- this census
cannot distinguish 'nothing is wrong' from 'I measured nothing'" -- the three
rows print SKIPPED, and the arm passes on its other rows. That is correct (a
skip is not a fail, and 0/0 is not a pass) and it is a trap worth naming: a
reader striding for speed can lose the rows without noticing unless they read
the footer. **`--every 1` is the only stride MEASURED to work here.** 2 is inferred and untested
and lands near the floor; 4 is measured to disable the rows. Measure before relying on 2.

The skip REASON is printed under the table rather than in the cell -- the
census's refusal is three lines long and shoved the verdict table out of
alignment. The cell carries a short tag (`no preset`, `census floor`,
`--no-clip`, `not measured`); the full reason survives in the footer.

### WHAT THIS CHANGES ABOUT EVERY VERDICT ALREADY ON RECORD

**Every gate verdict in this project was taken without this row.** That does not
invalidate them -- the rows they failed on were real -- but it does mean a
candidate rejected PURELY on surface or penetration rows was rejected by a gate
that could not see the benefit it claimed. Two on this page qualify:

  * `#panel-rigid-keep-clearance` (section 11) -- morph clip 50% -> 45% of
    scored pieces, 10 FAIL rows.
  * the authored floors (sections 7-8) -- the reported piece's bind clip
    5.316 -> 2.045, 5 FAIL rows.

Neither is re-scored here: it needs both arms rebuilt per candidate. Doing so is
now a one-command follow-through, and the row set it would be judged against is
finally the one the defect is expressed in.

## 13. `min_push` IS NOT THE SURVIVING SITE EITHER -- AND THE CONTRADICTION IS RESOLVED

Section 4 left the anti-poke's push being eaten by `panel_rigidity_post`, and
section 11 killed the lever that tried to stop it. The obvious next site was
`min_push`, which runs LATER and already reports repairing exposure. Read end to
end, it is not the answer, and finding out settled a contradiction this file had
been carrying as unexplained.

### THE CONTRADICTION, RESOLVED

The log says `[min-push] chest_low_mesh.042: 8 vert(s), exposed 151 -> 0` while
the written NIF measures 5.316% bind clip on the same band. **It is not a
predicate difference.** `fit_metrics._ClipTester.clipping` computes
`isfinite(i_t) & ~isfinite(o) & same`; `standoff_audit.ClipTester.report`
computes the identical predicate and THEN deletes in-hits that fail a
body-occlusion cast. The two are NESTED -- min_push's clip set is a strict
SUPERSET -- so on identical points and identical geometry, min_push reading 0
would force the audit to read 0 as well. The difference has to be points or
geometry, and the code supports both:

  * **POINTS.** min_push subtracts two gates the audit never applies:
    `rim_d > PUSH_RIM_MARGIN` (2.0u from that ONE shape's own boundary edges --
    and every split UV seam reads as a boundary) and `reach <= PUSH_MAX_REACH`
    (3.0u to the nearest garment VERTEX, blind to triangle interiors). BUG-16
    records a 4.0u triangle edge on this shape, so the rim margin excludes
    roughly half an edge length around every boundary.
  * **GEOMETRY.** min_push runs MID-STACK. Roughly ten vertex-moving passes
    follow it: seam weld, coherence repair, strap scale, short edge, z-fight
    fix, cleavage/abdomen separation, **the layer ride TWICE**, the layer-order
    repair, degenerate-tri repair and the cross-plate weld.
  * **WEIGHTING.** 151 and 0 are unweighted vertex COUNTS; 5.316% is AREA
    weighted. A handful of survivors on large apex triangles is worth several
    percent on its own.

**AND min_push CANNOT CHARGE A CLEARANCE DEFICIT AT ALL.** Its test requires the
OUTWARD ray to ESCAPE. A garment covering the bust but sagging to 0.338u of
clearance gives a finite outward hit, so nothing is "exposed", `exposed_before`
is 0 and the pass returns its input unchanged. It repairs poke-THROUGH only. It
is structurally the same blind spot `#bust-surface-req` was built for, which is
why it is not the site for a surface test even though its inputs are in scope
(`_surface_deficit` is already imported at that call site; only `in_bust` would
need constructing).

### THE REAL FINDING: THE ZERO-CROSSING GUARD IS A CLASS WITH THREE MEMBERS

Section 4 hypothesised that "every guard downstream of the anti-poke is a
zero-crossing guard" and marked it UNPROVEN. It is now enumerated:

    src/nif_convert_fitgeom.py   _rigidify_within_clearance
                                 floor = np.minimum(clear_of(Q), 0.0) - 1e-4
    src/nif_convert_layers.py:1617   the LAYER RIDE
                                 _floor = np.minimum(_fd, 0.0) - 1e-4
    src/nif_convert_writer.py:1196   coherence repair
                                 floor = np.minimum(s0, 0.0)

All three promise only "no deeper than it already was", so a vertex the fit
chain left 0.5u clear may legally be pulled to the skin by any of them. All
three are nearest-body-VERTEX signed distances, so all three are blind to the
surface-through-triangle case in the first place.

**AND THE REPO ALREADY BLAMES THE RIDE.** `src/nif_convert.py` records "end of
the fit chain 0 verts inside / the file that ships 110 / the ride causes 102 of
the 110", and `#layer-ride-body-floor` says the same from the other end: the
clipping is the layer ride, at WRITE TIME, where stage dumps cannot see it.

So the pass that actually decides this is the LAYER RIDE, not the anti-poke and
not panel rigidity. **NOT YET MEASURED:** the ride's own clearance budget, the
way section 11 measured `panel_rigidity_post`'s (238.32 added / 94.42 spent).
That is the next thing to measure, and `#panel-rigid-keep-clearance` failing at
its site is a warning against assuming the same lever works at this one --
`#layer-ride` is also on record as making things WORSE when simply turned off.

## 14. THE AUTHORED FLOORS, RE-SCORED THROUGH A GATE THAT CAN SEE THE DEFECT

    morph clip > 0.05% (pieces)    46 -> 45   ok
    morph clip > 1.0%  (pieces)    34 -> 32   ok
    bind  clip > 0.05% (pieces)    23 -> 22   ok
      ^ 92 piece(s) scored in BOTH arms

    still FAIL: gap body-swap 0.553 -> 0.563, gap copy 0.197 -> 0.329,
                28 tighter tips, stretch p90, posed follow
    RESULT: FAIL, the same 5 rows as before

**THE VERDICT DID NOT CHANGE. THE DECISION DID.** Before, floors-off was five
FAIL rows and no visible benefit -- the gate could not represent what it bought.
It is now a MEASURED TRADE: three clip rows better, five other rows worse. That
is a basis for a judgement instead of an artefact of what the instrument could
see.

**Keep the size honest: one to two pieces in 92 on each clip row.** Real,
directional, and small.

RUN TWICE, and the second run is the one to quote. The first used the
share-based rows, which had the population defect described below; both arms
happened to score 92, so the answer held, but equal SIZE is not equal
MEMBERSHIP. The re-run with the intersection fix reports "92 piece(s) scored in
BOTH arms" and gives the same direction on all three rows.

## 15. AN ADVERSARIAL REVIEW OF MY OWN GATE WORK FOUND FOUR REAL DEFECTS

Worth recording as findings against the work in section 12, not as process
notes -- three of them would have produced wrong verdicts.

**1. THE TWO ARMS SCORED INDEPENDENT POPULATIONS (high).** Each arm's census
rglobs its own tree and re-applies its own per-piece exclusions against its OWN
geometry. A piece whose band coverage falls under the census minimum is dropped
from THAT arm only -- so a candidate that pushes garments off the body removes
pieces from its denominator, and the pieces it removes are the ones that were
clipping. Demonstrated by driving the table directly: control scored 92 against
candidate scored 41 read **ok on all three rows**, with only an `info` row
beside it. `stretched_edges` had already solved this by reporting "shapes scored
in EVERY arm". FIXED: the rows now intersect on the piece path first, and the
gate refuses below 20 shared pieces.

**2. THE TOLERANCE SWALLOWED WHOLE PIECES (medium).** `add()` applies TOL=0.005
to non-integer floats, and a share is an exact k/n ratio with no float noise to
absorb. At 200 scored a one-piece regression reads ok; at 768 three extra
clipping pieces read ok and only the fourth FAILs. FIXED by counting over the
now-fixed denominator: `add()` gives integers an exact comparison. My own
comment made it worse by reassuring that "4 dp is finer than one piece" -- true
of the ROUNDING, silent about the comparison, which was 50x coarser.

**3. A LYING HEADER (low, but the expensive class).** "THE ONLY ROWS HERE TAKEN
UNDER A BODY MORPH" sat above three rows, one of which -- `bind clip` -- is a
BIND-pose number from the same census run. Corrected to say two morphed rows and
one bind row, and why the bind row is kept beside them.

**4. TWO OPERATOR ERRORS RENDERED IDENTICALLY (medium).** The skip tag tested
`"CLIP_PRESET" in reason` first, and the not-a-file refusal contains that
substring -- so a configured-but-BROKEN preset path (renamed file, wrong drive,
stale env) rendered exactly like an unconfigured environment, and the rows would
stay dark run after run while the reader concluded "expected". Worse, the test
`test_each_skip_reason_gets_its_own_tag` asserted they were distinct and never
exercised the broken-path case -- **the test name was itself the false claim.**
FIXED with explicit `CLIP_PRESET_UNSET` / `CLIP_PRESET_MISSING` markers.

Also surfaced: the census prints its own "HIGH: treat every share below as a
biased subset" warning when too many pieces error out, and nothing parsed it --
a biased subset read as a clean row. The gate now carries that flag through as
an info row. And a dead `_CLIP_ALL` regex left by the rewrite was removed.

## 16. WRITE TIME PUTS 14 BUST VERTS INSIDE A BODY THE FIT CHAIN HAD LEFT CLEAR

Section 13 identified the layer ride as the pass that actually decides this, on
the strength of the repo's own note ("the ride causes 102 of the 110"). Measured
directly, on the reported cuirass's clip-carrying shape.

**THE RIDE RUNS AT WRITE TIME, WHERE A STAGE DUMP CANNOT SEE IT.** The last
checkpoint is `s12_coherence_repair`; the file that ships is written after. So
the budget is (written NIF) minus (last stage), taken with the same
one-nearest-vertex signed distance the guards themselves use.

    shape 8867 verts, 501 in the bust band
    verts moved at write time    2932 of 8867   (max 1.8865u)
    bust verts moved              272 of 501

    bust clearance          p50       p05       min
      s12_coherence_repair  1.7805    0.4115    0.0088
      WRITTEN               1.9825    0.3103   -0.8074

    bust verts LOSING clearance    47 (9.4%)   total 12.28 u-verts
    bust verts GAINING            225          total 92.25 u-verts
    **bust verts INSIDE the body   0  ->  14**

**Write time moves a THIRD of the shape, raises the median, and drives 14 bust
verts from outside the body to inside, up to 0.81u deep.** It is not a uniform
loss -- 225 verts gain -- which is the ride's signature: it rides layers onto
the reference layer, pushing most out and a minority through.

### AND THAT SHOULD NOT BE POSSIBLE

The ride carries a body floor (`src/nif_convert_layers.py:1617`):

    _floor = np.minimum(_fd, 0.0) - 1e-4
    _short = np.clip(_floor - _sd, 0.0, None)

For a vertex the fit chain left OUTSIDE (`_fd > 0`) the floor is about zero, so
the ride should be unable to push it below the skin. Fourteen finished at
negative clearance anyway, the worst at -0.8074u. **Either the floor does not
cover these vertices, or something after it moves them.** That is a guard not
holding, which is a different and more tractable defect than the zero-crossing
design issue in section 13 -- a guard that concedes clearance by design is a
trade; a guard that fails to hold its own floor is a bug.

**NOT YET ESTABLISHED:** which of the two it is. Candidates from the source, in
the order the code makes them plausible: the floor's `_apply` mask not covering
these verts; the floor running on only one of the ride's two invocations; `_fd`
measured in a different frame from the geometry it is applied to; the block
sitting inside a `try/except` that swallows a failure; or a later write-time
pass (layer-order repair, degenerate-tri repair, cross-plate weld) moving them
again afterwards. Tracing that is a READ, not an arm.

### AND IT DOES NOT GENERALISE -- CHECKED BEFORE BUILDING ANYTHING ON IT

Three more pieces from the SAME mod, all in the pack's bind-clip class, same
harness, same measure:

    piece                         bust verts   losing   INSIDE at write time
    the reported cuirass                 501       47      0 -> 14
    a second cuirass                     384        0      0 ->  0
    a third                              260        6      0 ->  0
    a fourth                               0        -      (no bust coverage on
                                                            the shape sampled)

**One piece in three shows the defect; two show write time putting NOTHING
inside.** So "write time drives bust verts into the body" is NOT established as
a class on this evidence -- it is established on the REPORTED piece, which is
worth having, but it is one piece.

**AND THE SAMPLE IS ITSELF WEAK**, which has to be said rather than left for a
reader to discover: the probe picks each NIF's LARGEST rendering shape, and on
the fourth piece that chose a shape with zero bust coverage -- so it did not
sample the clip-carrying shape at all. A proper attempt would pick the shape by
bust coverage, or score every rendering shape. Until then this is one measured
piece and three weak negatives, not a population.

This is the third time this session a one-piece result has invited a theory --
`#panel-rigid-keep-clearance` was monotone across four arms on one garment and
failed the population on 10 rows. The rule earns its keep again.

## 17. `#layer-order-last` ERASES THE WRITE-TIME BURIAL. ONE PIECE SO FAR.

Section 16 measured write time driving 14 bust verts into a body the fit chain
had left clear, and left open which pass did it. Tracing the write-time chain
answered it, and the answer is a flag that already exists.

### WHAT THE CHAIN ACTUALLY IS (two corrections to my own notes)

  * **THE RIDE IS CALLED TWICE BUT ONLY ONE RUN SHIPS.** The first is a
    baseline probe for `#panel-rigid-ride`; a snapshot is taken before it and
    restored in a `finally` -- "Leaving the baseline ride's output in place
    would silently ship a double-ridden stack". Sections 13 and 16 said "runs
    TWICE" without that qualification. Corrected.
  * **THE LAST THING TO TOUCH A VERTEX IS NOT THE RIDE.** It is the SECOND run
    of the per-shape geometry repairs inside `_copy_shape` -- coherence repair,
    strap scale, short-edge cap -- which happens after the whole cross-shape
    chain. The caller says so itself: "the second run happens at WRITE time,
    after the cross-shape chain, so it is the last thing to touch the verts",
    and "the second group moves 74% of the belt's vertices by up to 0.600u".
  * And that second coherence-repair call passes **no body**:
    `_repair_coherence_collapse(_sv2, ov, src_shape.tris)`. So
    `#coherence-repair-outside-body` -- ON in the recipe -- is a NO-OP there.
    `project_coherence_repair_body_blind` already recorded this as a KNOWN GAP
    left "documented rather than half-fixed".

### `#layer-order-last` SKIPS THAT SECOND RUN, AND IT IS ALREADY BUILT

`LAYER_ORDER_LAST` (default OFF) sets `skip_geometry_repair` on the phase-2
copy, on the principle the caller states: "A pass that RESTORES A RELATIONSHIP
has to have the last word on position." It is NOT a disproven toggle -- the
toggle audit explicitly KEPT it ("different defects; the last also moves the
geometry repairs earlier"), and `feedback_deployed_build_runs_at_defaults`
already warned that "`LAYER_ORDER_LAST` matters most: with it OFF, `_copy_shape`
re-runs the geometry repairs at WRITE time and they are the last thing to touch
a vert, so a cross-shape fix can be partly undone."

**Nobody had measured it against the bust defect.** On the reported cuirass:

    write time (last stage -> written NIF)      OFF          ON
      bust clearance min                     -0.8074      0.0000
      bust verts INSIDE the body             0 -> 14      0 -> 0
      bust clearance p50 / p05         1.9825/0.3103   unchanged

    bust clip                    BIND   shallow  buried   MORPH(a)  MORPH(b)
      OFF                       5.316    3.966   0.555     9.365     4.924
      ON                        3.585    1.685   0.125     7.705     3.290
                                 -33%     -58%    -77%      -18%      -33%

**The burial is eliminated (14 -> 0, worst clearance clamped at exactly 0.0000
rather than 0.81u through), and every clip column improves -- most of all the
SHALLOW column, which is the profile of the reported defect.** The median and
p05 clearance do not move, so this is not a standoff ramp.

### ONE PIECE. NOTHING IS CLAIMED YET.

This is the largest single-piece result in this file, which is exactly when it
should be trusted least. Three times this session a one-piece result has invited
a theory: `#panel-rigid-keep-clearance` was monotone across four arms on one
garment and failed the population on 10 rows; the write-time burial in section
16 did not reproduce on two of three siblings. **A population arm is building.
Until it is scored, this is one measured piece and nothing more.**

Known costs to watch when it lands, from the caller's own comment: skipping the
second repair run leaves `_repair_layer_order`'s output in place, and the record
says that pass "leaves 669 wrong-side verts ... and 1068 (240) ship" -- so layer
ORDER is the thing this could plausibly trade away, along with the crumple and
short-edge repairs the second run also performs.

## 18. `#layer-order-last` THROUGH THE POPULATION GATE: FAIL, 4 ROWS -- AND THE
## BIGGEST PENETRATION WIN ON RECORD HERE

    folds                     30542 -> 30552   FAIL   (+10 of 30542, +0.03%)
    inverted                   2612 ->  2447   ok     (-165, -6.3%)
    bust gap, body-swap       0.553 -> 0.543   ok     (CLOSER to the author)
    bust gap, copy path       0.197 -> 0.197   ok
    bust-band pen, body-swap    108 ->    56   ok     (-48%)
    bust-band pen, copy path     51 ->    51   ok
    tip clearance p50         1.071 -> 1.032   FAIL
    tip clearance p05         0.105 -> 0.104   ok
    pieces tighter at the tip     0 ->     7   FAIL
    morph clip > 0.05%           46 ->    46   ok     (UNCHANGED)
    morph clip > 1.0%            34 ->    34   ok     (UNCHANGED)
    bind  clip > 0.05%           23 ->    23   ok     (UNCHANGED)
    stretch rate p50         0.1089 -> 0.1089  ok
    stretch rate p90         1.7021 -> 1.7232  FAIL
    edge deviation p50       0.0308 -> 0.0308  ok
    posed follow, worst           0 -> 0.056   FAIL
    posed follow, net             0 -> -0.183  ok     (better)
    RESULT: FAIL, 4 rows

**BUST-BAND PENETRATION ON THE BODY-SWAP PATH HALVES, 108 -> 56.** Nothing else
tried in this file moves that row at all: the authored floors managed 108 -> 102,
`#panel-rigid-keep-clearance` made it WORSE at 114. Inverted also falls 6.3% and
the author gap improves. The four failures are the tip median, 7 tighter tips,
stretch p90, and posed follow -- and folds is +10 in 30542 (+0.03%), which fails
only because counts compare exactly.

### THE CLIP ROWS DID NOT MOVE, AND THAT IS A DEFECT IN THE ROW I BUILT

On the reported piece this flag takes bind clip 5.316 -> 3.585 (-33%) and the
shallow column 3.966 -> 1.685 (-58%). **The gate's clip rows read 46/46, 34/34,
23/23 -- completely unchanged.**

They are not wrong; they are the wrong question. They COUNT PIECES ABOVE A
THRESHOLD, so they measure PREVALENCE and are blind to SEVERITY: a piece
improving 33% stays above 0.05% and above 1.0%, and the count does not move. A
change that halves the clip on every piece in the pack without taking any of
them under a threshold would score as no change at all.

**So section 12's claim that the gate can now see the benefit is only half
true.** It can see a piece stop clipping; it cannot see a piece clip less. Fixing
that means adding a MAGNITUDE row -- median or pooled bust clip area over the
shared pieces, which `band_class_census` already computes per piece and the json
already carries. NOT BUILT: the gate has been changed once today and reviewed
once, and a second row change deserves the same review rather than being bolted
on at the end of a session.

### WHERE THIS LEAVES THE FLAG

The best-performing bust candidate in this file, and still a FAIL. It is a
genuine trade: half the body-swap penetration and 165 fewer inverted verts,
against the tip median, 7 tighter tips, stretch p90 and posed follow. It is also
already-built, already-sanctioned (the toggle audit KEPT it), default OFF, and
in the GUI -- so it costs nothing to leave off and is a one-setting experiment to
turn on.

**NOT RECOMMENDED BLIND.** The tip rows failing is the same objection the
authored floors carry, and this file has established that the tip harness scores
a narrower region than the fix acts on. Whether half the penetration is worth
seven tighter tips is the user's call, and it is now a call with numbers on both
sides.

## 19. THE MAGNITUDE ROWS WORK -- AND THE RECIPE

Section 18 found the clip COUNT rows blind to severity. Magnitude rows added
(p50/p90 over the SHARED pieces, the shape `stretched_edges` uses for its
rates), and validated on the candidate the counts could not see:

    morph clip p50 (% of band)   0.0759 -> 0.0759   unchanged
    morph clip p90 (% of band)   8.6961 -> 8.5874   -1.2%
    bind  clip p90 (% of band)   3.3064 -> 1.2892   **-61%**

    for comparison, the COUNT rows on the same arms:  46/46, 34/34, 23/23

**`bind clip p90` falls 61% where every count reads flat.** The rows earn their
place; the gate can now see a piece clipping LESS, not only a piece crossing a
threshold. A test constructs the blind spot directly -- thirty pieces each
halving their clip, none crossing a threshold -- and asserts the counts stay
identical while the percentiles move.

### THE RECIPE FOR THE NEXT RECONVERT

`docs/worklog/2026-09-09_NEXT_RECONVERT.md`. One change: `layer_order_last` ON. It is the
only thing measured in this file that moves bust-band penetration (108 -> 56),
it cuts bind clip p90 61%, drops inverted 6.3% and closes the author gap, and it
costs four gate rows -- the tip median, 7 tighter tips, stretch p90 +1.2% and
folds +0.03%. The gate says FAIL; the recommendation stands anyway, and says so
in those terms rather than hiding behind the verdict.

Everything else measured here is a DO-NOT: `#panel-rigid-keep-clearance` (10
rows, penetration worse), `#antipoke-surface-req` (fully absorbed, 0 verts in
the written NIF), authored floors OFF (5 rows, gives back the copy-path gap).
