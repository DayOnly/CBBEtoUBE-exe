# CBBEtoUBE — Design & Rationale

This document holds the **why** and the **how** for the converter's non-obvious
subsystems. Inline code comments cover **what** a piece of code does; when they
need to explain a design decision, a failure mode, or the history behind a
value, they point here instead of carrying a paragraph:

```python
# Flat clearance floor on the lower leg (flex zone).  [DESIGN: Flex-zone standoffs]
```

Each `[DESIGN: <heading>]` tag matches a heading below. Keep the tags stable —
they are the link. When you change behavior, update the matching section here.

The converter turns a CBBE / 3BA-authored armor NIF into one that fits and
morphs on the **UBE** body. UBE is a different, generally larger body, so armor
authored for CBBE sits partly inside and partly outside it and has to be
re-fitted, re-skinned, and re-cleared.

---

## The governing rule: prevent the defect, do not add a pass that repairs it

**A pass that exists to clean up after another pass is a design failure, not a
fix.** When a stage produces something wrong, the change belongs in the stage
that produced it — or in what that stage is allowed to do — not in a new stage
downstream. Read this before adding any pass whose justification begins "an
earlier pass leaves…".

This is the rule the chain most often breaks, and it costs three ways.

**1. The repair's budget cannot be spent on anything else.**
`panel_rigidity` drove 491 verts into the body and the anti-poke push was spent
undoing that, so the push could never be tightened to respect the author's
standoff. Making panel rigidity body-aware (`#panel-rigid-early-clearance`) did
not just remove a repair — it freed the anti-poke to do its actual job. The
repair was not merely redundant; it was occupying the budget.

> **CORRECTION 2026-08-26 — THE RULE STANDS, THIS EXAMPLE DOES NOT.**
> `#panel-rigid-early-clearance` was re-measured under a metric that applies
> the body preset's morph, and it is **1 better / 2 worse**: it helps one piece
> and regresses two, one of which had a clean preset that starts clipping. It
> is still DEFAULT OFF and should stay there. The paragraph above is right that
> the repair was occupying the anti-poke's budget; it is wrong that freeing the
> budget was a net win on this evidence.
>
> WHY, and it sharpens the rule rather than weakening it: the body-blind pass
> was not only wasting budget, it was incidentally pushing panels OUTWARD, and
> that push is MORPH HEADROOM. Guarding the producer removed the waste AND the
> headroom together. **Fixing the producer is still correct — but measure what
> else the producer's mistake was accidentally buying you.**
>
> A cleaner example of the same rule from the same work:
> `#panel-rigid-surface-guard` fixed `_rigidify_within_clearance`'s own
> clearance test (it checked a panel's CORNERS and let a corner standing 1.5u
> clear sink to zero) instead of adding a pass to undo the result. A hold that
> *did* run downstream to repair it was built the same day, measured, and
> DELETED — it regressed a piece the in-place guard leaves untouched.

**2. Repairs fight each other, and the chain oscillates.**
`PASS_MAP.md` and the damage ledger show stages alternating between penetration
and stretch, each fixing one by causing the other. The observable outcome is not
"mostly fixed" — it is a chain whose final state depends on which repair ran
last. **Judge the FINAL stage, never the flow.**

**3. A downstream repair chases a defect it cannot corner.**
Worked end to end on 2026-08-24, on zero-weight bones (a bone left in a shape's
list with no weight drops out of the skin-partition palette — an equip CTD):

- `_match_limb_motion_to_body` stranded 10 of 12 on the traced piece. Guarding
  that one pass took it to **0 — and the total did not move.** The roughness cap
  then stranded 7 and the coincident match 5. Every pass that caps a row to four
  influences can strand a bone, so a per-pass guard is a game with no last move.
- A single final sweep is the better architecture, and it still caps at **19%**:
  45% of stranded bones sit on shapes with no index-pairable author, and 36% were
  minted by a graft the author never weighted, so there is no authored row to
  restore and `pynifly` exposes no `remove_bone` to take them out.

Both attempts were reverted. The fix that would actually work is at the source:
**do not let a graft add a bone whose weight cannot survive four influences
downstream.** A sink-side repair could never have reached the 81%.

### Two rules paid for on 2026-08-25

**DECLINING TO WRITE SOMETHING DOES NOT PROTECT IT.** The coincident-skin match
excluded a bone from its write list precisely so it would be "left entirely
alone", and the bone was emptied anyway: the native skin buffer holds FOUR
influences, so writing the other four evicts the untouched fifth. A guard phrased
as *"we simply won't touch it"* is only sound if nothing else writes that slot —
and here something always does. The same shape of error is available to any pass
that reasons about its own writes without modelling the buffer they land in.

**A PASS THAT REMOVES SOMETHING MUST SAY WHAT IT REMOVED.**
`_harden_hdt_xml_for_fsmp` prunes physics-XML blocks whose shape is not in the
converted NIF. That is correct — FSMP cannot attach to a shape that is not there
— but a pruned `<per-triangle-shape>` is a COLLIDER the cloth no longer bounces
off, and it was deleted in silence. It took an in-game screenshot to discover
that one cuirass had lost the two surfaces its skirt drapes over, because the
authored XML named `Tassets`/`Pants` while the mesh calls that shape `Tasset`
and has no `Pants` at all. Deletion is a legitimate act; doing it quietly is
not. Report through `_note_pass_failure`, which counts into
`conversion_report.json` (that survives a lost run log) and lands a per-piece
line in the per-mod report — so a class becomes countable rather than
discoverable one screenshot at a time.

### What to do instead

- Give the producing pass the information it lacked. Most of these defects are a
  stage acting blind — `panel_rigidity` could not see the body; the jiggle graft
  cannot see the cap that will evict it; the butt-collider donor test could not
  see that hands and feet are body bones, because it used the injected BODY
  MESH's bone list and UBE ships hands and feet as separate meshes.
- **Beware a set that stands in for a concept.** "The body's bones" and "the
  bones of the body mesh" differ by six, and that gap cost one variant its butt
  collider entirely. When a set is a proxy for an idea, write down which idea,
  and check the two still agree.
- Constrain what a pass may do, rather than repairing what it did. A pass that
  cannot create the defect needs no cleanup and no budget for one.
- If a repair is genuinely the cheapest correct option, say so **in its
  docstring, with the measurement** that shows prevention was considered and
  costed. `_rigidify_within_clearance` earns its place that way; a pass that
  cannot make that argument does not.
- Before adding a repair, **attribute the defect to a producer by measurement**,
  and name it. Attribution by reasoning has been wrong here repeatedly — blaming
  `_match_full_weights_to_body` for what its inner `_match_limb_motion_to_body`
  did, and (2026-08-25) blaming an old exe for what `#smp-boundary-weight-hold`
  caused.

  Two tracked ways to do it, in increasing cost:

  * **Pass-arm A/B.** Convert the affected piece with `scripts/convert_one_armor.py`
    once per arm, disabling ONE pass per arm, and score each arm the same way.
    This is what settled BUG-14 in four arms. Read the slot mask off the ARMA —
    a slots=0 run silently disables every slot-gated pass and is not comparable.
  * **Paired output A/B.** `scripts/analysis/zero_weight_pair_ab.py` for the
    zero-weight class: it pairs two builds' output per (shape, bone) and reports
    a delta, which is the only form in which a metric with a large pre-existing
    population means anything.

  **RUN A REPEAT CONTROL BEFORE BLAMING A FLAG.** Two arms of IDENTICAL code and
  identical flags have been observed producing different meshes, so a small arm
  difference is not by itself evidence about the flag under test. The mechanism is
  known and is still only half fixed: a piece with no physics file of its own
  resolves one by FILENAME against the DESTINATION tree, which the run is
  concurrently writing, and the per-worker index memoises whatever the tree
  looked like when that worker first looked. **104 garments currently resolve
  their physics that way** — measure the population with
  `scripts/analysis/hdt_xml_resolution_census.py`. A repeat control is a second
  arm at the SAME settings as the first; anything that differs between those two
  is noise, and only what exceeds it can be attributed.

  **A pass being NECESSARY in an A/B is not proof it is the actor** — it may only
  change rows so a later pass evicts. Narrowing further means wrapping the inner
  pass and re-scoring the WRITTEN nif after each stage.

  This bullet used to name `passaudit/zeroweight_trace.py` as "the template".
  That file was an untracked session tool and is GONE, so the governing rule
  pointed at something the reader could not open — the failure mode this
  document spends a section warning about. See `docs/TOOL_MAP.md` for what is
  actually tracked and runnable.

### The honest exception

Some defects are structural and have no producer to fix: an SMP `<per-vertex-shape>`
must keep its authored rig, so the layer beside it diverges by construction
(`#smp-boundary-weight-hold`). That is a boundary condition, not a pass cleaning
up after a pass — and the docstring says so.

---

## Pipeline overview

`convert_nif()` chooses one of two paths from the source shapes:

- **Armor-only** (no inline body shape) → a body-aware **rebuild**: warp each
  shape onto the UBE body, re-skin it near the surface, push it clear.
- **Inline body or exposed body-skin** → **phase-2 body-swap**
  (`convert_nif_phase2`): drop the source body/skin, inject the full UBE
  `BaseShape`, then re-fit the armor around it.

On top of the fit, a series of per-shape passes handle clearance, layered-cloth
ordering, leg-plate conform, physics-cloth preservation, and morph data. The
sections below cover each.

### Scheduling a batch (`#global-schedule`)

A batch used to convert one source at a time: its units went to the shared
pool and the run waited for that source's slowest unit before planning the
next. On the 09-24 All-mods run (154 sources, 3312 NIFs, 15 workers) the NIF
phase was 71.3 of 87 minutes and the workers were busy 24.7% of it: 89 sources
of under 15 NIFs took 38 minutes, and each 7-14 MB piece (125-222 s) held 12-14
workers idle behind it.

`_auto_convert_mod_steps` is a generator that pauses once, after planning,
with its `_NifPhase` (the work items and the result it fills). Everything a
source decides is decided before that pause -- its claims on output paths
(first writer wins, in source order), the #skip-built-ube-path supersede moves
and the patch ESP -- and nothing after it reads another source's output, so:

- **Plan every source first**, in source order, exactly as before.
- **One schedule** (`_GlobalNifSchedule`): all units, largest source bytes first.
  Units are keyed like `_pair_units` but over the whole batch
  (`_global_units`): a base two sources plan (one ships the `_1`, a later one
  the `_0`) shares the XML and `.tri` at the destination, so the later half is
  its own unit and starts only after the earlier SOURCE has finished -- the
  order one source at a time gave those files, and never two workers on one
  pair (#pair-unit-dispatch).
- **Memory-aware admission.** Peak commit tracks source size (~0.36 GB floor
  plus ~236 MB per source MB). A unit that fits the per-worker budget the pool
  was sized with starts whenever a worker is free; a heavier one starts only
  when no other heavy unit is running or the live free RAM and free commit,
  less a 2 GB reserve, cover its peak plus the running heavy units' peaks. That
  double-counts what those have already taken: it errs toward waiting, and
  lighter units fill the pool meanwhile.
- **Finish in source order.** A source's post-conversion steps (load check,
  VirtualBody re-hide, postflight invariants, report) run once all its units
  are in and every earlier source has finished, with the same content as
  before; the result list and the per-source report checkpoint keep source
  order.
- **Retries.** A dead worker's in-flight units, and any unit whose answer shows
  a MemoryError (raised, or caught inside a fit pass), are re-run one item at a
  time once nothing else is in flight; only the re-run is delivered. An
  out-of-memory answer is never kept -- its patch ESP already points at the
  planned NIF.
- **Supersede guard.** Planned first, a later source's supersede would move an
  earlier source's base BEFORE that source wrote it (one source at a time it
  moved the fresh conversion). A base an earlier source claimed this run is
  held back (`_split_claimed_supersedes`), so both orders keep that conversion.
- **Progress.** One `[progress] 1 1` marker, then `[progress-nif] <done> <total>`
  over every source's files: the window's bar fills once.

`CBBE2UBE_NO_GLOBAL_SCHEDULE=1` converts one source at a time again (and moves
a claimed base again); `--workers 1` and `--plugins-only` never use it.

**Measured 2026-09-25**, full All-mods runs from source into scratch outputs
each seeded with a copy of the live output, PYTHONHASHSEED=1, 15 workers,
151 sources / 3216 NIFs, on a box other jobs kept at ~100% CPU:

| | one source at a time | one schedule | one schedule, again |
|---|---|---|---|
| wall | 5552 s | 2259 s | 2882 s |
| NIF conversion | 5002 s (sum per source) | 1756 s (+64 s planning) | 1892 s |
| converter peak commit (process tree) | 16.9 GB | 20.3 GB | 20.0 GB |

7124 of 7125 compared files byte-identical across all three (every NIF, TRI,
XML, ESP, INI and sidecar). The one that differs is `standoff_audit.jsonl`:
worker appends to it are not atomic and tear 14-16 lines a run -- it differs
between the two one-schedule runs as much as against the old schedule, and the
live file already carried 58 torn lines. The #hdt-xml-race destination-stem
class (175 NIFs, 88 garments, in this output) did not differ. Heavy units at
once: up to 3-4; memory re-runs: 0; supersedes held back: 0.
The finishes all land at the end (every source's smallest unit runs last), so
a run that dies in the NIF phase checkpoints no source finished.

---

## The fit contract (1.2)

Until 1.2 the chain was **speculative**. Twelve phase-2 passes compute against
the body; every one assumed the garment shared its coordinate frame, none
asserted it; and nothing between them measured whether a pass had helped. Two
failures follow directly from that shape, and both happened:

- a single bad transform displaced one shape by 40 units, and all twelve passes
  computed against a garment that was not where they thought it was — for three
  months, with every pass still reporting success;
- each pass caps only its OWN contribution, so the push stack is individually
  bounded and jointly unbounded. Over-inflated meshes shipped twice.

Per shape, the chain now states:

| step | cost | what it does |
|---|---|---|
| frame precondition | ~free | discard a transform offset that moves the shape AWAY from the body |
| diagnose | 1 measurement | exposed skin before anything runs |
| *(each pass)* | array copy | checkpoint — **no** measurement |
| verify | 1 measurement | if the chain as a whole regressed, ship the best checkpoint |

**Two measurements per armed shape, not one per pass.** Checkpoints are copies
(microseconds against ~120ms), so the chain can afford to remember every pass and
pay to inspect them only when the verify fails.

### Why the contract is on the CHAIN, not each pass

"Reject any pass that measures worse than its input" was the obvious design and
the pass trace (`CBBE2UBE_PASS_TRACE=1`) refutes it. Over 48 traced shapes:

- exactly **one** pass ever regressed bust fit — `conform`, 5 times;
- **all 5** were recovered downstream;
- **0 of 48** shapes ended worse than they started (total exposure 8138 → 245).

`conform_to_source_standoff` pulls IN by design and later passes push back out.
Reverting it per-pass would have blocked a correct pass five times and biased
every garment looser — which is precisely the over-inflation reported from the
game. **Intermediate regressions are how the chain works.** This is also why the
per-pass guards on the anti-poke and the soft-cloth inflate were removed in 1.2:
neither ever regressed, and the chain verify covers the outcome for a quarter of
the measurements.

### What it deliberately does NOT do

It does not skip passes when the entry diagnosis looks clean. Bind-pose clipping
is blind to animation — "at rest" in game is an animated pose, and the anti-poke
exists for morphs and motion this metric cannot see. Gating passes on a
bind-pose number trades a measurable defect for an unmeasurable one. The chain
measures whether the passes *collectively* helped; it does not decide which ones
to run.

### Two metrics, because clipping has no upper bound

An over-inflated garment scores a perfect 0.0% clipping — nothing pokes through a
balloon. **Standoff** is the counter-metric, anchored on a piece confirmed correct
in game (median 1.15u, p90 1.52u). Never read one without the other. See
`METRICS.md` for the calibration and for the metrics this replaced.

### Telemetry is a file

Conversion fans across a process pool, and in the frozen windowed exe a worker's
`print()` can be discarded outright — a clean log is not evidence of a clean run.
Frame corrections, chain verdicts and standoff distributions append to
`standoff_audit.jsonl` at the output mod root. Failed measurements are recorded
too: one that errored must not look like one that found nothing.

Records carry `entry`, `final` and **`shipped`**. Read `shipped`, not `final`:
when a rollback fires, `final` is the measurement that was *rejected*. On the
first pack-wide run that distinction was 174 exposed verts versus 101 actually
shipped, and made a run with **zero** regressions read as "20 shapes ended worse".

### The pass that ran on nothing

`conform_to_source_standoff` is the only pass that reels an over-projected
garment back onto the body; everything else nudges outward. It was silently
skipped on some pieces because **two body detectors disagreed**:
`classify_shapes` → `_looks_like_inline_body` identified a shape as the body and
dropped it for the swap, while `_is_body_pynifly_shape` refused the same shape
for having fewer than 40 bones — a BodySlide-output inline body only carries the
bones its surviving verts touch, and the hide cuirasses ship one with 26. So
`src_body_v_p2` stayed `None` and the gate never opened.

Nothing recorded it: no exception, no warning, the pass simply absent from the
trace. It surfaced only because the per-pass **standoff** trace was added to
chase a gap reported in game. Cost on the affected piece: 2.40u of standoff at
the strap line, against a 1.79u maximum across 42 shapes where the pass ran;
with the fallback it lands at 1.72u with clipping unchanged at 0.00%.

The fix is a fallback to the shapes `classify_shapes` already named, reached
only when the strict detector returns nothing — so it cannot change which shape
is picked where that detector already answers (verified byte-identical on a
piece that previously worked).

**Scope: 14 pieces.** 103 of 482 source pieces match the predicate; 22 of those
reach the converted output (the rest are HIMBO/male bodies the female-only
policy never converts); and converting all 22 showed **8 route to phase 1**, the
copy path, which never reaches `conform` at all — the scan tested for a body
`classify_shapes` could name, but phase-2 routing has further conditions, so it
over-matched. It concentrates in the vanilla-lineage armours — hide, imperial,
stormcloak, draugr, Ysgramor — whose BodySlide output emits an inline body
carrying only the bones its surviving verts touch.

Each narrowing came from measuring rather than reasoning, and each was smaller
than the last: **103 → 22 → 14**. Verified on the final set: **48 of 48 armed
shapes run `conform`**, with a control that already ran it still doing so.

> **A sampling lesson, not a footnote.** This was first reported as "rare — 42 of
> 42 armed shapes in a 9-mod census ran the pass". The census drew nine pieces
> that all happened to have a detectable body. A sample landing entirely in one
> state establishes no rate at all, and it was read as if it established a low
> one. The real figure is 21.4%, found by sweeping every phase-2 piece. When the
> question is "how often", sample only if the sample can observe both outcomes —
> otherwise sweep.

---

## Source selection (which mesh feeds the conversion)

Before any fitting, `discovery.build_mesh_index` decides WHICH mod provides each
armour mesh, resolving through the full MO2 VFS. The provider matters as much as the
fit: an armour is authored FLUSH on whatever body it was built against, and the
converter conforms it onto the UBE body -- so if the chosen source was built on a body
whose proportions differ from UBE, the piece is born gapping or clipping before a
single pass runs. Three rules encode this:

1. **Tier: deprioritise BodySlide OUTPUTS** (`#bodyslide-source`). A 3BA/HIMBO/NSFW
   BodySlide output was taken to be the mesh morphed to a specific PRESET; feeding it
   into a UBE conversion would bake the wrong body's shape in (squashed layers ->
   clipping, seen on one layered leather armour in 2026-07 -- while the CBBE reference
   was itself a preset body). 3 tiers, MO2 priority within each: (0) base/replacers,
   (1) UBE outputs, (2) other-body outputs. A BodySlide output still wins a mesh
   nothing else provides. Rule 3 overrides the tier where the output is PROVEN to be
   the zeroed build.

2. **Within a tier: prefer the CANONICAL-body source over a BESPOKE-body source**
   (`#body-match-source`). Some mods (an HDT-SMP "vanilla armours" pack, a retexture)
   bundle their OWN body -- often a slim/large preset that is NOT the canonical 3BA
   body. A soft-body band authored flush on a +9.88u big-bust bundled body is kept at
   its source position (the converter does not warp physics cloth) and stands off the
   +5.74u UBE bust -> the Fur Cuirass +1.77u breast gap. So when a same-tier challenger
   bundles the canonical `3BA` body and the incumbent bundles ONLY a bespoke body, the
   challenger wins -- it converts flush. `_body_provenance(path)` returns
   `(has_canonical, has_bespoke)`:
   - **canonical** = a shape named `3BA`.
   - **bespoke** = a body-skin-textured shape (diffuse matches `femalebody`/`malebody`/
     …) that is NOT canonical AND is a real body: `>= 500` verts and `>= 35u` z-range.
     The size floor is essential -- it rejects an exposed-skin SLICE (baked hand/neck
     skin on a robe, body-tex'd but ~46 verts / z-range 5) which must NOT count as a
     bundled body.
   The swap fires ONLY when `incumbent == (canonical=False, bespoke=True)` and the
   challenger has a canonical body. Three guards fall out of that:
   - a source that bundles NO body (a physics robe: cloth + collision, no body-skin
     shape) is `(False, False)` -> never swapped, so its SMP physics is preserved;
   - the incumbent already having a canonical body is `(True, …)` -> never swapped, so
     MO2 priority decides among body-standard sources;
   - the rule is WITHIN-tier only, so it can never promote a tier-2 output over a
     tier-0 base (the earlier source-tier fix stands).
   Open failure -> `None` -> treated as unknown, never a swap basis. Opt out with
   `CBBE2UBE_NO_BODYMATCH_SELECT=1`. Measured pack impact: 42/2165 meshes re-source;
   the Fur Cuirass band standoff drops +1.77u -> +0.59u. The tier-2 3BA-OUTPUT source
   has both physics AND a matching body but promoting it would need overriding the tier
   system -> deferred (rule 3 now does, where the output is proven the zeroed build).

3. **Take the VERIFIED zeroed build over a mod's own meshes** (`#zeroed-output-source`,
   2026-09-22). Rule 1's premise fails both ways: BodySlide builds from the ShapeData
   project, not from a mod's loose meshes, and those loose meshes may be made for
   ANOTHER body. One armour's were made for the vanilla body; the fit, which starts
   from the zeroed CBBE body, kept their shape as the author's gap -- bust +3.58u off
   the UBE body against the author's +0.82u, inner thigh +0.35u at weight 0 against
   +0.90u: inflated breasts, inner-thigh and butt clipping in game. The user's
   BodySlide output of that armour was its zeroed 3BA build to 0.000u; converted from
   it the bust sits at +1.41u and the inner thigh at +0.99u. A post-pass
   (`discovery._prefer_zeroed_outputs`) re-points a piece at the BodySlide output that
   provides the zeroed CBBE body the fit uses, ONLY when all of these hold, and
   otherwise leaves rule 1's answer alone:
   - the fit's CBBE reference at both weights IS that zeroed body (found by content --
     the folder the zeroed-body resolver verified it in -- so a male or UBE output is
     never a candidate);
   - both weights of the output are the zeroed build of ONE slider set, every shape
     vertex for vertex within `GARMENT_TOL` (`zeroed_body.zeroed_garment`: defaults per
     weight, a default-on zap deletes its vertices, a morph index past the shape is
     skipped -- both as BodySlide builds; anything it cannot check is a refusal);
   - today's source is a tier-0 mod whose meshes are NOT already that build;
   - both sides agree on whether the piece declares an HDT physics XML;
   - the build has the SAME shapes, with the same vertex counts, as today's source at
     both weights. A build that bundles the 3BA body sends the piece down the
     body-swap path instead of the copy path; on four pieces of one armour overhaul
     that path change moved up to 4.2u at a weight whose source geometry barely
     differed and exposed 2-10% more of the body in poses. Only the geometry is this
     rule's to change.
   Measured on one real modlist: 289 pieces considered, 118 moved (all as whole weight
   pairs, all to the one verified folder); 171 kept -- 106 other shapes, 25 already
   the build, 21 would change physics, 19 not a verified zeroed build. With the switch
   off the index is identical to the old one on all 2,205 keys. Posed (zeroed UBE
   body, 400 samples per region), 3 of 4 moved samples clip no more than before; a
   vest whose own mesh was 2.6u oversized at the bust gained breast-side exposure
   (<=1.3% -> 12.5%): its zeroed build is snug by design. `GARMENT_TOL` is 1e-3, not the body's
   1e-4: BodySlide adds weight 0's seam defaults in single precision, and 34 genuine
   builds sat 1.0-1.4e-4 off; every preset or stale build seen was 0.25u or more off.
   Off with `CBBE2UBE_NO_ZEROED_OUTPUT_SOURCE=1` (settings: "Take armour from the
   zeroed BodySlide build").
   **The check remembers its loose-file answers for one call** (`#zeroed-probe-memo`,
   2026-09-25). The garment check asked `_Vfs.winner` ~3,100 times (~930 distinct
   paths), and each question stat'ed every one of ~3,300 overwrite/mod/Data folders:
   5.4M stats, 72% of the step. `_prefer_zeroed_outputs` now opens a
   `zeroed_body.probe_memo()` scope; inside it an answer is kept per (folder list,
   exact path), and a path of more than one part is probed only in the folders that
   have its first part as a folder (one `_ci_join` per folder per first part, in
   priority order). A one-part path is a file at the root, so it is never filtered.
   The answers are the plain probe's -- same `_ci_join`, same order, same case rules --
   as long as the folders do not change while the scope is open, so the scope is one
   call and is dropped on return: the GUI's long-lived process never answers from a
   memo taken before the user re-ran BodySlide. Live, read-only, in separate
   processes: 264.8 s -> 69.6 s; the mesh index is identical key for key (4,562
   entries, 244 re-pointed) and so is the verdict line (122 moved / 179 kept).
   `CBBE2UBE_NO_ZEROED_PROBE_MEMO=1` probes every folder per question again.

### Which mods and pieces are sources at all (2026-09-24)

The rules above pick a PROVIDER for a mesh the converter already plans. Four gates
before them decided whether a piece was planned at all, and each dropped real
armour in silence while the unified coverage step still minted a UBE armature for
it -- so a UBE actor drew the unconverted CBBE mesh. Census of one modlist: 279
such armatures. Each gate has its own off-switch; with all four set, selection on
that modlist is identical to before (138 sources, 2,059 pieces, 3,290 meshes, every
provider the same). With all four on: 153 sources, +78/-2 pieces, +139/-2 meshes,
100 of the 279 armatures now draw a converted mesh; no child piece, no skin and no
already-UBE path among the additions.

- **Archive-only mods** (`#bsa-only-sources`, `CBBE2UBE_NO_BSA_ONLY_SOURCES`). The
  source gate counted a mod's armour meshes in loose files only; the convert step
  resolves from archives too. A mod with nothing loose now also asks a lookup-only
  `_BsaMeshIndex` (it can never write) over the same folders the convert step
  lists; the convert step adopts that listing instead of reading ~260 archive
  tables again. Every mod still dropped here is named in the log (uncapped: it is
  in no report). +9 mods, 37 pieces,
  49 meshes, 66 armatures.
- **Texture archives** (`#texture-archive-meshes`,
  `CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES`). The index skipped every archive with
  "texture" in its name; a large content mod keeps all its meshes in its
  "- Textures" archive, and the substring also caught "Retexture" armour archives.
  +143 archives listed (0.1 s), +21 meshes, 2 male stand-ins no longer needed, 16
  armatures; 21 pieces of one set move to the higher-priority retexture archive,
  the copy the game loads. Voice, sound and facegen archives stay skipped; the
  setup check still reads only the vanilla archives. The switch restores the old
  DEFAULT list only: the coverage step's existence lookup names its own list, and
  a mesh in a texture archive still loads in game, switch or not.
- **Skin-named armour** (`#nude-basename-path`, `CBBE2UBE_NO_NUDE_BASENAME_PATH`).
  A model named like the nude body (`femalebody`, ...) was skin wherever it sat; a
  pair of pants is named that. The path alone is not the fix: of 114 armatures with
  such a model outside `actors\character\`, 108 are follower, race or unused body
  variants, and a path-only rule admitted three of those bodies as armour. So
  outside that folder the model is armour only when a PLAYABLE armour record WITH
  A NAME in its plugin uses it -- exactly the other 6 (the pants and five
  first-person robe and cuirass models). +5 pieces, 10 meshes, 1 armature.
- **Outfits only NPCs wear** (`#npc-worn-nonplayable`,
  `CBBE2UBE_NO_NPC_WORN_NONPLAYABLE`). An armature only non-playable armour used was
  skipped as gore. Follower and quest outfits are non-playable too, and coverage
  deliberately covers non-playable body and hands/feet armour. It is kept now when
  the WINNING record of a female NPC of a vanilla playable, vampire or UBE race
  reaches one of its armour records through her default or sleep outfit or her
  inventory, via outfits and leveled lists (`_npc_worn_armos`: four groups of every
  active plugin, read by seeking past the rest, 8-11 s, built once per batch and
  passed to every planning call like `ube_covered_armos`, including the VFS index a
  direct `convert` builds). Not followed: the worn skin (WNAM). An NPC whose traits
  come from a template takes its sex and race from the template chain, through NPC
  records; a leveled-NPC-list template has no single answer and does not count. The
  first cut counted every templated NPC as a possible wearer: 48 of its 125 new
  meshes came only that way (creature-cavity bodies, animal costumes, male bosses'
  gear), and that route also reached 23 creature skins -- so every form any NPC_ or
  RACE record names as its skin is removed from the set as well, however it was
  reached. Script-applied gore is reached by no outfit, so it stays out.
  +7 mods, 43 armatures kept in 14 sources, 37 pieces, 60 meshes, 17 of the census
  armatures. Known edges: wound meshes in a victim's inventory are converted;
  anything handed out by a script or a distributor at run time is still not seen.
  The switch covers the conversion only; the coverage race-list rule reads the
  same set under its own switch (`#coverage-human-race-list`).
- **A UBE race is one UBE_AllRace defines** (`#ube-race-by-plugin`,
  `CBBE2UBE_NO_UBE_RACE_BY_PLUGIN`). The worn walk above first recognised a UBE
  race by an editor ID starting `ube_`; UBE_AllRace.esp's 18 races are all
  `00UBE_...`, so it matched none, and the 13 winning NPC records on them (12
  female) were not wearers. The test is the race's identity the walk already
  carries -- its DEFINING plugin is `ube_allrace.esp` -- the identity the coverage
  passes and the loose-mesh index use; a patch that overrides the race last does
  not change it. No editor-ID fallback is kept for a renamed or merged UBE
  master: the coverage passes mint UBE armatures by that plugin name, so such an
  order is not served anyway, and a name test would let in any plugin's race
  that is merely named like one. The worn-set cache key carries the switch. Live: 9,675 -> 9,686 forms
  (+11, -0): 8 armours whose every record is playable, 2 outfits, 1 weapon -- no
  non-playable armour, so selection and coverage are unchanged (coverage replay
  byte-identical; switch set: worn set and replay identical to the parent).
- **Playable is the WINNING record's flag** (`#selection-winner-playable`,
  `CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE`). Every test above read an armour's
  non-playable flag from the scanned plugin's own ARMO record; the game uses the
  last loaded one, and the coverage passes already judge that. On one modlist
  338 of 13,303 armours flip between the defining and the winning record (313
  to non-playable, mostly balance patches taking a set out of the game; 25 to
  playable). `_batch_armo_winner_nonplayable` builds
  {(defining plugin, low id) -> winner non-playable or deleted} once per load
  order: the ARMO group of every active plugin (`_read_plugin_groups`, 0.6 s),
  resolved through the ROOT-only index (`paths._plugin_file_index_root`, the
  files the game loads) whatever the index switch says -- the recursive walk
  hands back our own un-loaded copies for 22 names. `_player_armor_mesh_bases`
  takes it as `armo_winner_nonplayable` at every planning call (selection
  strict + candidate slots, the vanilla-sweep keys, a direct `convert`'s VFS
  keys, the convert step) and replaces the record's flag with it; an identity
  the map does not know keeps the record's flag. The worn test and the named
  playable item test (`#nude-basename-path`) read the same flag. Admission is
  still per plugin: an armature no same-plugin armour references is admitted as
  before, and a playable winner in another plugin does not admit one here.
  Edges: a deleted winner is not playable; an override in an ESL/ESM-flagged
  plugin keys on its master through the master list; a TNAM variant keeps its
  own flag (all 4,106 live variants carry their own models); an armour whose
  defining plugin is not loaded is keyed on that name and the last loaded record
  wins; the legacy BODT non-playable bit (0x10) is NOT read -- on that modlist
  only 10 creature skins carry it, and the skin rules drop those. Its own switch
  and cache, NOT inside `_batch_npc_worn_armos`: the NPC-worn switch or a failed
  worn read does not turn it off. Cache key: mods folder, mod order, plugin
  order, the switch -- not the files' stat, so a regenerated patch plugin in one
  GUI session is read on the next load-order change or restart. Fails open: no
  readable load order, no plugin file found or a read error gives the old rule
  and one warning per load order; an unreadable plugin is skipped and named. The
  switch is in the `_find_armor_mod_dirs` memo key. Live (plan census, 153
  sources + Data): sources 153 -> 150, weight bases 2,034 -> 2,008 (-27 bases /
  43 files, +1 base / 1 file), worn-kept armatures 43 -> 54 (the worn rule now
  sees winners made non-playable). A repeat census of the parent and the lane
  with the switch set both give the parent's plan exactly. The -27: a fur set's
  pieces (6), a pirate follower's outfits (7 -- its source loses 8, but one
  first-person torso is still planned by another source), a clothing set (7),
  a mod's first-person models (6: five cuirasses or body armour, one robe),
  and the vanilla male boots a follower
  replacer's boots fell back to (1) -- every carrier of their armours male or
  none. The +1 is a DLC effect mesh (a whole outfit
  plus a skeleton, slot 61) a skeleton mod makes playable: its skin is bound to
  thigh and calf bones, so the candidate-slot crash guard keeps it and it is
  converted like body cloth; coverage already mints a UBE armature for that
  armour and points it at the converted mesh once it exists. Coverage itself is
  unchanged (it judges winners and reads the output folder): replay
  byte-identical. But 59 minted coverage armatures (88 model slots: 67 body
  pass, 21 non-body) point at a converted mesh the plan no longer makes; they
  keep the copy an earlier run left (the output is never swept); on a fresh
  output the coverage rules treat them as unconverted (their source mesh, or no
  body armature under the world-mesh rule). No female wearer of any of them on
  that modlist (winning NPC, outfit, leveled list, quest alias and script
  references checked).

---

## Fitting: warp + re-skin

**Why.** BodySlide bakes armor to a specific body at slider-zero. On the bigger
UBE body those verts land in the wrong place, and the armor's bone weights are
for the CBBE body's skinning, so it deforms wrong at runtime.

**How.** Two levers:

- **Warp** moves each vert by the measured CBBE→UBE body deformation, so the
  armor follows the body's shape change while keeping the artist's drape. Where
  no CBBE/UBE body pair is available it falls back to a *snap-outside* heuristic
  (push verts that ended up inside the body back out to a small standoff).
- **M6 proximity re-skin** blends the injected UBE body's bone weights onto
  armor verts near the surface (full at the skin, fading out with distance).
  This is what makes single-bone "rigid prop" pieces morph with the body
  instead of hanging static, and what lets the armor track the UBE skeleton.

Rigid attachments (dagger, scabbard, pauldron — one bone holds most of the
weight) deliberately get a *low* re-skin rate so they keep tracking their parent
bone instead of smearing across the body.

**Warp internals.** The per-vert delta is IDW-interpolated (1/d², K-nearest) from
the body, so the nearest body region dominates. A distance falloff zeroes the warp
far from the body — otherwise a gauntlet's fingertips get dragged by the wrist
delta and lose their pose. And an *upper-body standoff damp* stops rigid stand-off
geometry (stiff collars, high pauldrons) from inheriting the full delta: the body
broadens at the chest/shoulders CBBE→UBE and the warp would shear those pieces
outward, so the damp fades it where a vert is both high-Z and far from the body.
(Armor still sits a touch tighter than hand-built UBE armor because BodySlide adds
an outward inflation when it builds UBE armor that the warp doesn't replicate — the
inflation post-pass handles that.)

**Re-skin vs source skin `[DESIGN: Morph-TRI reskin]`.** The M6 re-skin's K-NN
body-bone blend can be unstable under animation (equip fly/spike, even CTD on
dense shapes). So when a shape ships its *own* source morph TRI — the author
already built RaceMenu/BodySlide morphs for it — the converter keeps its stable
source skin and skips the body-blend re-skin, preserving TRI-morph fidelity.

A BodySlide TRI only supplies **body-slider** morphs (a static per-character shape
offset), not leg/butt *flex during animation*. That flex-follow comes from SEPARATE
passes, and what each gives an exempted morph-TRI shape is set per pass:

- the leg-detail graft (`_match_rigid_leg_bend_to_body`,
  `[DESIGN: Leg-plate bend / butt-jiggle conform]`) gives it FrontThigh / RearThigh
  but withholds RearCalf, which bent a flap tip at calf height (`#morphtri-no-leg-graft`,
  narrowed by `#morphtri-thigh-graft`);
- the leg-motion match reaches it (`#leg-motion-morphtri`), except draping-named
  shapes (robe, dress, ...), which the leg passes skip by name; the spine and arm
  instances keep the exemption;
- the butt / belly / breast jiggle graft reaches it (`#morphtri-keep-jiggle`).

Withholding all three from BodySlide-built trousers left the author's CBBE pelvis
weight at the back of the thigh and no thigh jiggle, and the trailing thigh opened
in a sprint in game.

History (2026-07-08): a change tried ALSO grafting scale bones inside the re-skin
path for morph-TRI shapes (`CBBE2UBE_MORPHTRI_SCALE`), on the theory the exemption
dropped them. It was wrong — the leg-conform already provides them — and grafting a
second time onto a shape driven by its own body-slider TRI over-responded and caused
a coverage regression (body poked through the thigh). It is now **opt-in, default
OFF**; the default is the clean exemption (untouched source skin). See
`[DESIGN: Leg-plate bend / butt-jiggle conform]`.

Note this hinges on which source copy wins the VFS: a base mod may ship no TRI
while a BodySlide-output override at the same path ships one. The exe resolves to
the load-order winner (the copy the game loads), so the TRI is seen when it will
actually be present at runtime. Shapes with no source TRI take the full re-skin,
which grafts the scale bones as part of the blend.

### Reference bodies: BodySlide's zeroed builds, as the game loads them

**Why.** The warp moves each vert by the CBBE→UBE body deformation near it, so it
is only as right as the two bodies. Garments built in BodySlide sit on the
*zeroed* build — the slider set's ShapeData base mesh plus its slider defaults for
that weight (`small` for `_0`, `big` for `_1`) — and that is the build the game
loads; the preset arrives at runtime as body morphs. Discovery by name (an
18,436-vert femalebody, preferring a CBBE/3BA-named mod and skipping mods named
like a BodySlide output on the assumption they hold a UBE body) picked the 3BA body
mod's own femalebody on a real modlist: a preset build the game never loads, up to
1.97u off the zeroed body over 16,061 torso vertices, whose weight morph grows the
bust ~1u. The warp therefore moved weight-1 garments ~0.6u further in than
weight-0 ones. Most fit passes re-fit that away; pieces that skip them kept it —
six measured (a cloak, a cape, belt bags, a book, a front pouch, a skirt front);
five shipped their weight-1 version 0.25-0.47u closer than weight 0 (median), the
cape 0.03u.

**How** (`src/zeroed_body.py`, `#zeroed-body-refs`). Per body — 3BA
`femalebody_{0,1}`, UBE `!UBE/Body/femalebody_tangent_{0,1}` — find the slider
sets that BUILD that output path; keep the right family by base topology (18,436 /
29,298 verts: a BHUNP or CBBE SE set builds the same femalebody path); build the
zeroed geometry the way BodySlide does; find the file the game loads there (MO2
overwrite, enabled mods by priority, game Data); accept it only if it IS that
build to 1e-4u on every vertex, with weight 0 from the same folder as weight 1.
Otherwise `ZeroedBodyError`, and the lookup falls back to discovery by name with
a `!! no zeroed <KIND> body at weight N: … -- falling back to discovery by
name` line (once per body and weight in each process). A GUI run does not reach
that fallback: its Reference bodies window starts on the body the game loads,
flagged as not zeroed, and asks before using it. Never by mod name. Every
converter lookup of the CBBE or UBE reference body goes through it -- the warp's
FROM and TOWARD bodies, the body injected under a body-swap garment, and the
passes that read them (the copy path's authored-standoff reads, weight transfer,
the collision-proxy warp, the UBE-native scan, the body overlay rebake, Check
setup). The preset bake is the exception (`_find_user_preset_body`, the user's
build). An explicit `CBBE2UBE_CBBE_BODY_0/_1` / `CBBE2UBE_UBE_BODY[_0/_1]` wins
first, logged as `[body-ref] …` once per process; one naming a missing file is reported,
not silently replaced. Off-switch: `CBBE2UBE_NO_ZEROED_BODY_REFS=1` ("Fit against
the zeroed BodySlide bodies").

Measured: on the six pieces weight 1 − weight 0 is now 0.000u (median) on every
one, and the off-switch reproduces the shipped pack exactly. Over the 15-piece
golden set (weight 1) garment verts near the body move median 0.000u, p05 −0.19u,
p95 +0.51u, bust median +0.014u; off-switch identical 15/15. No in-game verdict
yet.

**The Reference bodies dialog** (`src/body_choice.py`, `gui.build_body_dialog`)
lets the user confirm or change both bodies before a GUI run. The choice reaches
the child as the four `CBBE2UBE_*_BODY_0/_1` overrides for that run only (never
`os.environ`); a kind left blank writes them EMPTY, so an inherited override the
dialog refused cannot win. It is skipped for a dry run and with the off-switch
set, so the off-switch stays a clean control.

**Deliberately NOT these bodies:** the body-swap preset bake and the chain
rest-pose lift read `_find_user_preset_body` — the user's installed UBE build,
preset included — because the bake adds (build − template) to the garment and the
lift clears the body the player wears. That lookup still walks mod folders by name
and does not skip disabled mods (known, not fixed).

**Body mods are excluded by the files they ship, not by the fit.** `auto` skips
any mod folder shipping either body file above (`auto_convert._body_mod_names`).
It used to exclude the folders the body lookups returned, so the exclusion moved
with the reference: on the zeroed bodies the 3BA body mod fell out of it (its
collision-body NIFs would have entered All-mods runs), and a dialog pick could
change which mods converted.

---

## Clearance & anti-poke

`clear_armor_outside_body()` is the anti-poke stage of the per-shape fit chain
(after warp, inflate and conform) and pushes armor clear of the injected UBE
body so the live actor morph can't punch through. It is NOT the last vertex
op: panel rigidity, softcloth, rebury, chain blend, min-push, seam weld and the
cross-shape passes run after it, and the write-time layer ride can put verts
back inside (that is what `#ride-body-floor` exists for — see PIPELINE §2c and
the traced chain in PASS_MAP). Push-out only; it never pulls cloth in. Several
terms stack into one
required-clearance value per vert:

### Adaptive clearance

**Why.** A flat clearance everywhere makes loose/thick armor float off the body.
**How.** Clearance scales with how much the body actually *grows* at that vert
under runtime morphs (slider/bodygen amplitude): tight in static zones (sternum,
back, sides drop to a small base), full clearance only where the body inflates
(breast, belly, butt).

### Flex-zone standoffs

**Why.** The adaptive map keys on *morph* amplitude, but some zones barely morph
yet **flex** hard during animation, so they get shrunk to the static floor
(~0.25u) and then punch through mid-motion. Two measured cases:

- **Rear butt / upper-thigh** — leg armor hugs the butt with a razor-thin rest
  gap; at rest it's fine, but the thigh swings back on the stride and the
  gluteal fold deforms past the sliver. Low jiggle-weight, so jiggle clearance
  can't reach it either.
- **Calf / lower leg** — barely body-morphs, so it shrinks to ~0.25u, but the
  knee/calf flex every step. (Measured 0.25u at z30–35 → in-game clip.)

**How.** A flat minimum standoff over the affected band, enforced geometrically
by the nearest body vert's position (and, for the rear, its facing). It only
raises verts already below the floor, so well-fit armor is untouched, and it's
push-out only. The rear term gates on rear-facing normals; the calf term is
all-round (the calf bulges at the back and the shin extends at the front).

### thin-rim crumple (default ON)

**Why.** A THIN feature — a hem rim, a seam ridge — buckles when neighbouring
verts take *different* displacements, even sub-unit ones that every
absolute-magnitude guard in the pipeline passes. Measured: **0.41u of
differential wrecks a 2.2u rim**, while the same displacement across a panel is
invisible. The defect is the vert-to-vert *differential*, not the warp, which is
why no clearance or push cap ever caught it.

**The metric is COHERENCE COLLAPSE, not rotation or area.** Per-triangle angle
between source and output face normal; cluster the turned triangles into
connected patches; a patch is BROKEN when its area-weighted `|mean normal|`
falls from `>= COHERENCE_SRC_MIN` to `<= COHERENCE_OUT_MAX`. A legitimate refit
turns every normal in a patch *together* and keeps `|mean|` long — only a crumple
scatters them. Judging by **rotation** alone flags 53% of the pack; judging by
per-triangle **area** misses the defect entirely, because it is many small
triangles summing to one visible patch.

**The repair.** Smooth the DISPLACEMENT FIELD (`out - src`) across the patch with
its boundary held fixed, then re-apply. The patch's mean displacement is
preserved, so the garment stays exactly where the fit put it and
clearance/standoff are unchanged — only the differential that buckles a thin
feature is removed. A strip thinner than `COHERENCE_THIN` is moved **rigidly**
rather than smoothed, and gates on how far coherence *fell* rather than its
absolute value, because a rim that reorients coherently is still a defect.

`CBBE2UBE_NO_COHERENCE_REPAIR=1` is the hatch; the gates are the
`CBBE2UBE_COHERENCE_*` knobs.

### Jiggle clearance (default ON)

**Why.** HDT-SMP softbody swings breast/butt/belly *past* the rest surface at
runtime, so cloth cleared only for the static envelope still gets hit mid-bounce.
**How.** Adds clearance scaled by local jiggle-bone weight, capped small. It
loosens fit slightly in the exact zones people most want tight, which is why it
was originally opt-in; it has been ON by default since 2026-07-10. Turn it off
with `CBBE2UBE_NO_JIGGLE_CLEARANCE=1`.

### Push-field smoothing (default off)

**Why.** Each vert is pushed along its own nearest-body normal, so neighbours get
different magnitudes and the cloth turns faceted/crinkled exactly where clearance
was applied. **How.** Feather the push scalar over the mesh adjacency, floored at
the raw push so it never re-opens a poke. Off by default: in-game it raised the
inner layer of a multi-layer garment toward an unpushed outer one and collapsed
the gap between them. Re-enable once the smoothing is made gap-aware.

### Layered anti-poke floors (default off)

**Why.** Stacked garments are anti-poked independently against the same clearance
map, so where both bind (high-morph bust/butt) they converge to the same standoff
— coincident surfaces, inter-layer z-fighting, inner pokes through outer.
**How.** Rank a NIF's body-layer shapes innermost-first by median distance to the
body and give layer *i* an extra `i * EPSILON` floor (capped), so bound layers
stay separated; single-layer NIFs are unchanged. Off by default (same in-game
finding as smoothing). Median-ranking is coarse — see **Layered cloth** for the
per-vert source-order approach that handles draping layers.

---

## Skin-to-bone (STB) preservation -- the add_bone footgun

<!-- anchor: [DESIGN: Skin-to-bone (STB) preservation -- the add_bone footgun] -->


**This is the single most dangerous invariant in the mesh pipeline.**

**Why it bites.** In pynifly, `add_bone()` (and `setShapeWeights`) **resets every
existing bone's skin-to-bone transform to identity**. A shape's verts are
positioned for their bones' real bind transforms (e.g. Pelvis at Z≈−69); with an
identity STB there is no valid bind pose, so at runtime the verts skin to the
origin and the whole piece **collapses / flies**. It looks fine in every static
mesh check (geometry, weights, partitions all valid) — it only detonates when
the engine skins it.

**The rule.** Any pass that calls `add_bone` on a shape that already has weighted
bones **must**:

1. Save every existing bone's STB *before* the first `add_bone`.
2. If any existing STB can't be read (can't be restored), **bail** the graft for
   that shape rather than ship an identity-wiped real bone.
3. Restore the saved STBs *after* the last `add_bone`/`setShapeWeights`, and
   re-set the new bones' STBs too (setShapeWeights zeroes those as well).
4. Restore on *every* exit path, including early "nothing grafted" bails —
   `add_bone` may have already run.

`_match_rigid_leg_bend_to_body` is the reference implementation.
`_transfer_body_jiggle_to_fitted` once set only the *new* bones' STBs and left
the originals wiped — it collapsed the pants on the weight-0 mesh while the
weight-1 mesh (which had the jiggle bones grafted by an earlier pass, so it
skipped `add_bone`) survived. That weight-only asymmetry is the classic symptom.

**Diagnostic.** When something is runtime-only and every structural check says
"correct," measure the STBs: compute `|STB @ vert|` (each vert's distance from
its bone origin). A wiped shape shows verts sitting ~2× farther from their bone
than a working baseline; an all-identity STB set is `[0,0,0]` translations.

### Zero-weight bones desync the partition palette

A sibling skinning footgun. A bone that's `add_bone`'d but left carrying **no
weight** (e.g. the genital/anus bones the re-skin propagates onto armor that doesn't
use them) stays in the shape's bone **list**, while the GPU skin-partition **palette**
is built from weighted bones only. The per-vertex bone indices reference the longer
list and run *past* the shorter palette → out-of-bounds read on equip → CTD. So prune
zero-weight bones **before** `add_bone`, keeping list == palette. Authored SMP
colliders are the exception: their skin is already self-contained and consistent, so
stripping bones from *them* is what desyncs it.

---

## Weight-pair (_0/_1) consistency

**Why.** The engine interpolates the `_0` and `_1` weight meshes vertex-by-vertex
for body weight, so they must stay in lockstep: same shape set, same vertex
counts, same vertex order, and consistent skinning. Any per-shape decision that
is *weight-sensitive* can desync them.

**Two failure modes seen:**

- **Shape-set mismatch → explosion.** The exposed-body-skin test is geometric and
  weight-sensitive: a baked bare-leg skin slice qualified at `_1` but not `_0`, so
  `_1` body-swapped (dropped the slice, injected the UBE body) while `_0` copied
  (kept it). Different shape sets → the morph interpolates unrelated meshes →
  verts fly. **Fix:** decide on the *pair* — union the exposed-skin decision over
  `_0` and `_1` so both take the same path.
- **STB desync → collapse.** See the STB section; the same pass wiped one weight
  and not the other.

**The rule.** Any shape drop / inject / classify decision must be reconciled
across the `_0`/`_1` pair, or verified weight-invariant.

---

## Phase-2 body-swap

**Why.** Some armor bakes a slice of the nude body (open-cleavage skin, bare
lower legs) or ships a full inline body. That geometry can't morph or connect to
the neck on its own and must *be* the body. **How.** Drop the source body/skin
shapes and inject the full UBE `BaseShape` (+ `VirtualBody`) — the UBE reference
body or an explicit override, see *Reference bodies* — then re-fit the armor
around it. Exposed-skin slices are detected by geometric coincidence with the CBBE
reference body surface (a shape whose verts overwhelmingly sit on the body *is*
the body). This detection is weight-sensitive — see weight-pair consistency.

---

## Layered cloth

**Why.** A multi-layer outfit (corset over shirt, skirt over leggings) is
authored with a specific radial stacking. The per-shape warp pushes every inner
layer to about the same standoff off the bigger UBE body, collapsing that order,
so inner layers poke through outer ones.

**How (what exists).** `_separate_abdomen_layered_cloth_depth` restores the
*source* stacking: it classifies which shape is above which using the source
body frame (immune to the warp), binds each vert to its source-above/below
partners, and lifts inner→outer so leapfrogging is structurally impossible.

**Known limitation.** It measures the gap in the *source* frame, so it enforces
the source *order* but not extra separation, and it can't see divergence the UBE
fit introduces in the *output* frame. On body-swap armor with no inline source
body, the classification frame is unreliable (different reference bodies
disagree on which layer is outer), so an output-frame "tuck the under-layer in"
pass was prototyped and **pulled** — a wrong over/under call would push the outer
layer inward and worsen clipping. Fixing this needs a reliable per-vert
layer-order signal on the body-swap path.

**Layer-coherent jiggle `[DESIGN: Layer-coherent jiggle]` (opt-in, default off).**
The above fixes the *static* stacking; motion is a separate problem. Jiggle is
proximity-grafted, so the INNER cloth layer (closer to the body) gets MORE butt/belly
jiggle than the OUTER layer over it, out-swings it, and punches through during
motion. `_sync_abdomen_layered_cloth_weights` (`CBBE2UBE_ABDO_JIGGLE_SYNC`) picks the
OUTERMOST waist layer as authority and rewrites each inner layer's nearby verts to
the authority's *jiggle* weights only — the receiver keeps its own base (thigh/pelvis)
skin, rescaled to conserve mass, so leg deformation is untouched (a full-weight
replace, tried first, moved the inner-thigh skin and clipped). Sibling of the chest
`_sync_chest_layered_cloth_weights`. Default off pending cross-armor validation; on
a layered leather cuirass it was correct but unneeded once the inner-thigh clip proved to be a
pre-existing pose limit. No `add_bone` beyond copying the authority's already-valid
bones+xforms, so the STB footgun does not apply.

**Butt follow on layered cloth `#layered-cloth-butt-follow` (default on).** A
name-detected layer stack (`Cuirass_A/_B/_C`) keeps its source skin in every graft
pass (`#layered-cloth-skin`): breast weight on such cloth ballooned a chest in game.
The butt is the one exception. A quilted skirt with no butt weight stayed still
while the trousers under it and the body bounced, and skin showed through it on
the swinging leg. The jiggle graft now gives layered cloth the BUTT region only,
through the same gates as any other garment, and only on a piece with no physics
XML. `CBBE2UBE_NO_LAYERED_CLOTH_BUTT_JIGGLE=1` restores the blanket skip.

---

## Leg-plate bend / butt-jiggle conform

**Why.** A rigid leg plate skinned mostly to Thigh/Calf doesn't track the UBE
body's finer leg deformation, so it lags or clips as the leg bends. **How.**
Graft the UBE body's detail leg bones (front/rear thigh, rear calf) and a small,
capped share of its butt jiggle onto the plate, anchored so the grafted bones'
bind transforms match the body's. The same matched-and-capped graft mirrors onto
the chest (breast-jiggle bones anchored to Spine2, self-gating to the front where
the body carries breast weight). The cap matters most there: breast jiggle is
~10× the butt's, so a full match would make a metal cuirass bounce like flesh —
the cap keeps it mostly rigid (partial follow = less poke, not a soft chest). Strength tapers from full at the knee to
partial in the thigh, so the larger-radius upper plate isn't over-rotated into a
rest-pose bulge. It never moves a vert (rest pose identical) and never adds a
jiggle bone (the plate stays rigid). The grafted bone's skin-to-bone transform is
re-anchored to the *armor's own* Thigh/Calf bind, not copied from the body —
copying the body's absolute STB onto armor with a different bind convention tore
verts apart (an in-game explosion). All of this adds bones — see the STB footgun.

**Fitted (non-rigid) cloth** that hugs a jiggling region but carries none of its
own jiggle stays rigid while the body bounces through it (the "clip when moving"
class). Two sibling passes: `_conform_fitted_to_body` blends a hugging garment's
*existing* weights toward the body's where it already jiggles;
`_transfer_body_jiggle_to_fitted` grafts a capped share of the body's jiggle onto
one that lacks it. Both gate on hugging + leg/jiggle-dominant geometry, and both add
bones (STB footgun applies).

---

## Bust collider split

**Why.** Some authors reuse the bust garment itself as the piece's per-triangle
SMP collider (their skirt/tassel chains rest on it). A shape that IS its own
collider can never carry jiggle: grafting breast motion onto it closes a
feedback loop — cloth moves collider, collider pushes cloth — and in game the
breasts tore off the body (the revert that kept the torso graft off for a
release). The well-behaved siblings in the same source family solve this by
hand: a SEPARATE hidden collider shape carries the support role, leaving the
bust garment free to follow the breast. Diffing a working sibling against the
failing piece is what found this, after three theory-driven fixes failed.

**How.** Two order-critical passes at both pipeline sites. Pass 1, before
`_finalize_hdt_physics`: clone the garment IN PLACE as `<name>Col` — hidden
(flags 15), textureless, keeping the garment's CURRENT rigid weights so the
resting chains see identical support. In place matters: rebuilding a NIF from
its shapes drops ALL extra data (BODYTRI + the physics link; in game "ignores
morphs, body reverts to its _0 shape"). The pass snapshots root- AND
shape-level extra data (BODYTRI lives on its carrier shape) and byte-restores
the file if anything is lost. Pass 2, after the finalize (which overwrites the
on-disk XML with the authored copy — an earlier rewrite is silently undone)
and before the jiggle graft (which reads that XML): repoint each
`per-triangle-shape` decl at the clone, gating the split/morph/physics
invariants together. The stock torso graft then reaches the garment with no
bypass, because it is no longer a collider.

**Detection is measured, never named.** A candidate is a RENDERED per-triangle
collider (textured, not Hidden) covering the bust band whose breast FOLLOW
RATIO against the body underneath is below 0.5 — weight, not bone presence: a
garment can carry all six breast bones at 0.15 of the body's drive and still
fail in game. Names with XML roles beyond the per-triangle decl (constraints,
pairs) are skipped as unvalidated structure. Bodies, hidden helpers and
already-split pieces are excluded by construction.

**Status.** In-game validated on the motivating vanilla cuirass (production
output reproduces the hand-built artifact: follow 0.660 vs anchor 0.643, same
per-bone distribution as the body). Census over the shipped pack: 33 pieces in
the split class. `CBBE2UBE_NO_BUST_COLLIDER_SPLIT=1` disables the split;
`CBBE2UBE_TORSO_JIGGLE=0` disables the whole fix (split included — a split
with no graft is inert output churn).

---

## HDT-SMP physics-cloth preservation

**Why.** Authored SMP cloth (per-vertex softbody) and SMP colliders (per-triangle)
carry a self-contained, already-consistent skin that the runtime physics reads
directly. The converter's skin/jiggle passes (re-skin, scale-bone graft, jiggle
transfer, leg conform) would rewrite that skin — adding bones or stripping
weights — and desync the partition/bone palette the SMP engine reads, causing an
out-of-bounds read and an **equip CTD**, or a collapsing/drifting sim.

**The rule.** Every skin-modifying pass must **skip** authored SMP shapes —
both softbody and collider. Detect them structurally (physics extradata / the
softbody/collider shape sets), not just by name, because bone-driven SMP cloth
uses ordinary skeleton bones and won't trip a name check.

**Globally-configured cloth.** Some draping cloth (robes, cloaks) is driven by a
*runtime-global* HDT-SMP config with no per-mesh XML, so there's nothing structural
to detect. These are skipped by garment-name keyword (robe/cloak/cape/gown/…) in the
conform/graft passes as a fallback — grafting UBE scale bones onto them crashed the
SMP update on equip (skin-data OOB, the "robes" CTD). "skirt" is deliberately
excluded: metal tassets are rigid plates that legitimately want the conform.

### Custom physics-bone chains

When a NIF is rebuilt, pynifly re-adds each skinned bone flat under the root with an
identity transform. Standard skeleton bones are fine — the game resolves their real
position by name from the actor skeleton. But armor-specific physics bones (a skirt's
bone chain, cape/cloak/tail bones) aren't in the actor skeleton, so a flat identity
node pins their verts to the world origin and the skirt collapses through the floor.
Fix: recreate those bones' nodes with their *source* local transforms and parent
links, anchored to the standard bone they hang off. `_is_skeleton_bone` tells the two
apart by prefix/keyword — a leading `_` marks an armor-specific chain even when the
name contains a body-part keyword.

### Chain rest-pose lift (default ON)

**Why.** Recreating a chain at its *source* bind is right for the rig and wrong
for the body: the bones keep CBBE-era positions while the body grows to UBE, so
on a fuller body a skirt's chain bones end up **inside** it. HDT-SMP resolves an
equilibrium — `generic-constraint` pulls each bone back toward its rest pose
while collision pushes out — so an inside rest pose drags the cloth in every
frame and it settles part-way inside. It looks identical standing still and
moving, which is why it read as neither a follow problem nor a clearance one,
and why three collider passes each helped and none finished: they add push
against a pull nothing addressed.

Measured on the test cuirass: 2 of 63 chain bones inside the **built** body, and
8 of 63 inside it under the player's RaceMenu preset, mean 0.900u / max 2.000u.

**How.** Translate each affected chain's **ROOT** outward along the body normal
until no bone of that chain rests inside. Roots only — displacing a root moves
the chain rigidly (worst inter-bone change 0.000000u), while warping bones
individually changes rest lengths and is how a chain explodes.

**The margin is the body's own outward morph amplitude**, capped. The converter
never sees the player's preset, and 6 of the 8 penetrations exist only under it,
so the pass clears the room the body still has to grow. Adaptive clearance takes
20% of that amplitude for garment verts because those verts morph too; a chain
bone has no morph channel, so it takes all of it. The cap is the real engagement
rule: uncapped, the belly's amplitude (to 8.7u) recruited FRONT chains measured
+3.63u clear of the skin.

**Cost.** The lift is rigid, so the free-hanging lower chain moves out too —
0.5u on the test piece. That is the counter-metric to watch, and the reason this
is the first toggle to untick if a skirt looks held off the hips.

**A caveat before shifting roots differentially.** "A root shift is rigid" holds
*within* a chain. It does not follow that chains are independent: on the test
piece 74 of 130 `generic-constraint`s are cross-chain, stitching ten skirt
panels into a hoop, and lifting six of them by three different amounts changed
28 inter-panel rest distances by up to 1.651u. That is safe **there** for a
reason worth re-checking elsewhere — every cross-chain constraint uses
`frameInLerp`, so FSMP derives its rest frame from the bones at load, while
every explicit-`frameInA` constraint is intra-chain and those change 0.000000u.
Read the emitted XML for that pairing before assuming it.

Full history, including the two candidate fixes the numbers killed first, is in
`docs/worklog/2026-08-10_BUTT_CLIP_CHAIN_REST.md` — a working note, so it lives on the
`testing` branch only (see "Where the hard-won detail lives" below).

---

## BODYTRI / body-morph generation

**Why.** RaceMenu / BodyMorph applies body sliders to a shape via a `.tri` file
named in the NIF's `BODYTRI` extra-data. The tri must match the NIF's shapes and
vertex layout, and it must exist.

**How.** The converter auto-generates a **per-armor** `.tri` from the CBBE source
+ UBE body slider (OSD) data and writes it next to the mesh, so each converted
mod is self-contained. The generic body tri (`femalebody_tangent.tri`) is only a
legacy fallback, written when no armor-relative path can be derived.

The BODYTRI goes on a **single carrier** shape, not every shape: NioOverride reads
only the first BODYTRI in a NIF, so tagging them all shifts the carrier to whatever
textured shape iterates first and the real cloth silently stops morphing. Rigid
single-bone pieces still morph — the M6 re-skin re-weights them to multiple body
bones, so they follow via ordinary bone-driven skinning rather than BodyMorph.

**Shape flags for morphing.** NioOverride silently refuses to morph an alpha-having
shape whose NiAVObject flags lack **bit 19** (`0x80000`, the alpha-sorter): the
NiAlphaProperty alone isn't enough — the renderer must also be told to sort the shape
into the transparent pass, and without it the shape sits in an inconsistent state that
BodyMorph skips. Hand-built UBE armor sets flags = `0x8000E` (bits 1/2/3 "SelectiveUpdate"
+ bit 19) on nearly every shape, so the converter uses `0x8000E` uniformly; on opaque
shapes bit 19 is just ignored by the renderer, so it costs nothing. (An earlier
split-by-alpha-state version left some alpha-false cloth at `0xE` and it didn't morph
in-game.)

**Gotcha (test harness).** The BODYTRI path written into the NIF is derived by
finding `meshes` in the *destination* path. Converting to a scratch folder with
no `meshes` segment silently produces the fallback body-tri — an artifact of the
test setup, not a real conversion bug.

---

## Delivery: SkyPatcher-only

**Why.** Overriding vanilla/master ARMO records to point at UBE armatures caused
load-order and value/weight conflicts. **How.** SkyPatcher `armorAddonsToAdd`
INI links are the sole delivery path: for each ARMO that references a converted
armature, a link (ARMO → minted UBE ARMA) is recorded in a `.skypatcher.json`
sidecar; no ESP ARMO override is emitted. The legacy ARMO-override machinery has
been removed. (The winner-scan coverage passes still emit ARMO overrides, but
their output is folded into the Combined family rather than shipped as separate
plugins — see "Unified coverage" below.)

### The sidecar FormID invariant

**A sidecar records the FULL, POST-PRUNE FormID of each minted armature.** The merge
resolves every link by exact FormID (`merged_rec_by_key[(patch, fid)]`), so a sidecar
holding anything else resolves to nothing.

The trap is that `prune_unused_masters` drops unreferenced masters and remaps every
record's **master byte in place**, so a FormID captured as an `int` before it goes
stale. Hold the **Record object** and read `rec.formid` after the save. Emit the INI
from that same object — the INI masks to 24 bits (SkyPatcher names the plugin
separately), so it stays correct across the remap and therefore **cannot detect the
drift**. One source, or they diverge silently.

The failure mode is total and quiet: zero links, the merge deletes the previous INI as
stale (correctly — it points at reassigned FormIDs), and nothing is delivered, while
the ESL flag, split, master count and ARMA total all report normally. Any test covering
code downstream of prune must assert a master was **actually dropped**
(`len(saved.header.masters) < len(masters)`), or prune is a no-op and a stale int
passes by accident.

### Coverage patches size themselves to the ESL cap

`_partition_patches_for_esl` bin-packs whole patches and cannot split one, so a single
coverage patch minting more than 2048 own records used to force its merged piece down
to a full ESP. The coverage generators therefore emit **numbered pieces of their own**
(`_emit_coverage_pieces`), each within the cap.

Chunking is **by target (ARMO), never by armature**, so an ARMO's whole add-set stays
in one piece and yields exactly **one** `filterByArmors` line. Whether SkyPatcher
accumulates duplicate lines for one armor or takes the last is unverified, and this is
the only delivery path. The cost is that an armature shared across a chunk boundary is
minted twice — measured at ~2%.

### Per-source patch names (`#source-patch-rename`)

A per-source patch is `<stem> (CBBEtoUBE src).esp` in `_unmerged_patches`, with its
`.skypatcher.json`, `.espgen.json` and `.male_fallbacks.json` beside it. It used to be
`<stem> UBE patch.esp` — the name hand-made UBE patches use: on the live modlist 22 of
our un-loaded copies shared a name with another mod's active plugin, and the recursive
plugin index read ours in their place. The new name collides with none of the 3292 root
plugins there. The coverage pieces keep `UBE_Mod*Coverage* UBE patch.esp` (ours, never
colliding). No sidecar embeds the patch's file name, so the set renames without loss.

- **Migration** runs at the start of every `_cmd_convert` (full run, `--only-mods`,
  `--plugins-only`): each old-named set not starting `UBE_Mod` is renamed, sidecars
  first and the ESP last, a failure putting the moved sidecars back; when the new name
  already exists the old set is deleted (the folder is ours). Idempotent. A patch it
  cannot move is a warning, recorded and counted.
- **The gate.** `'*UBE patch.esp'` gated the whole post-conversion block —
  female-model restore, coverage and the merge — not only the fallback merge. A fresh
  output holds no file of that name any more, so the gate
  (`_merge_gate_patch_paths`) takes per-source patches of either name and coverage
  pieces. The live folder could not catch this: last run's coverage pieces matched.
- **The fallback merge** (`_per_source_patch_paths`) never takes an old-named and a
  renamed file for the same source, and keeps the order the old names sorted in.
- `--plugins-only` looks for the renamed snapshot and falls back to an old-named one,
  replaying it in place. `_find_source_esps` skips both names, for the root-write mode
  (`--unmerged-patch-subdir .`), which also prints that the renamed plugins must be
  enabled in MO2.
- **Root-write guards** (`#rename-guards`). In the root-write mode (`''` or `.`) the
  patches sit beside whatever else the folder holds, and a `<x> UBE patch.esp` there
  can be another mod's plugin; renaming it drops it out of MO2's plugin list. The
  parent only ever read such files. So the root is migrated only when
  `_is_our_own_output(output)` holds (a conversion report, or an INI with our header),
  and then only an ESP with our `.espgen.json` beside it is renamed or, beside its
  renamed twin, deleted. Anything else keeps its name and is listed in a NOTE (an
  older build of ours that wrote no snapshot is named there too, to delete by hand).
  The `_unmerged_patches` subfolder is ours by name and is migrated as before. No
  switch of its own: the feature is unmerged and the rename's switch turns it off.

`CBBE2UBE_NO_SOURCE_PATCH_RENAME=1` makes new runs write and read the old names and
migrates nothing. **The migration is one-way**: the switch does not rename files back.
On a folder a run already migrated, the switch (and an older exe) sees no per-source
patches — the gate still opens on the coverage pieces, but `--plugins-only` finds no
snapshot and skips every source, and a coverage failure leaves the fallback merge
empty. A full run regenerates the old-named set; the next run without the switch then
deletes the old set wherever the renamed one exists.

### What the coverage passes leave alone

The winner scan is the sole generator over every armour in the load order, so it
also decides what it must NOT touch. Three rules, each reported in game on one
follower (converted male Ebony boots on her UBE body):

- **Armour an excluded mod defines** (`#exclude-owned-coverage`). `--exclude-mods`
  used to remove a mod from the sources and nothing else. Owned means the ARMO's
  DEFINING plugin ships in that mod's folder — of four readings, the only one that
  caught all 14 of her minted armours (an overhaul patch wins 11 of their
  overrides, and a BodySlide output supplies most of her meshes). Armour withheld
  this way that no mod covers is named in a warning. The window's Select-mods run
  passes its exclusion list as `--coverage-exclude-mods`, because coverage covers
  the whole load order on every run. `CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE=1` covers
  it again.
  - **Only its body pieces** (`#exclude-body-only`, the user's call 2026-09-24).
    The body pass withholds everything the mod owns. The non-body pass withholds
    an owned armour only when (1) another enabled mod (not our output) patches
    it, read WITHOUT our patch reader: a raw text scan of every SkyPatcher armor
    INI at any depth for a line adding addons that names it (`plugin|formid` in
    any spelling, a full `FE` ESL form, or its EditorID), or a loaded plugin's
    override of it that adds an armature -- so the exclusion stays the fallback
    for a refit `_third_party_ube_covered_armos` misses; or (2) ANY armature it
    would mint (race-list ones too) is conversion territory by the planner's own
    test: BOD2 (else the armour's slots) on `_BODY_SLOT_BITS`, a
    `_CLOAK_MESH_KEYWORDS` model, a `_BODY_CANDIDATE_SLOT_BITS` slot whose loose
    world mesh is body-fit or unreadable (archive-only reads as body), or a model
    redirected to a converted or twin UBE mesh whose source path ships in the
    excluded mod's folder or its archives (a converted shared path does not hide
    a piece); or (3) the modlist cannot be read. Otherwise it is minted like any
    other armour and named in a NOTE (`exclusion_nonbody_kept`): it draws the
    model its armature names, which is never a converted copy of a mesh the
    excluded mod ships, but IS the `!UBE\` copy of a shared path another mod's
    conversion covers -- so the NOTE says "no mesh converted for that mod", not
    "its own mesh". Live replay with one
    follower excluded: +2 links (eyeglasses, a helmet), refit recognised or not;
    with the text scan removed and the reader off it was +5, three of them the
    refit's own pieces drawn twice. `CBBE2UBE_NO_EXCLUDE_BODY_ONLY=1` withholds
    all of it again. The report (`_report_coverage_holds`) reads
    `exclusion_nonbody_held`: a piece held because another mod patches it
    (`named by <mod>`, `<plugin> adds an armature`; `_held_for_another_patch`)
    is listed on its own line as left to that mod's patch and taken out of the
    "no UBE armature from any mod" warning, which told the user to un-exclude
    the mod (norec replay: the refit's helmet, pouch and wig were listed there).
- **Armour a SkyPatcher-delivered UBE patch already covers**
  (`#skypatcher-patch-recognition`). SkyPatcher reads INIs nested inside its type
  folders and recommends a subfolder for a plugin-named INI; the check read
  `armor/*.ini` only. A UBE patch may also add an armature that keeps the source
  mesh on the UBE races (a helmet, a wig): an added armature whose primary race is
  a UBE_AllRace race counts when its mesh is a loose file and the patch's UBE
  addons cover every biped slot of the armour. The ESP half keeps the `!UBE\` path
  test. `CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION=1` restores the flat, path-only
  read.
- **What another mod's UBE armature on the winning record already draws**
  (`#coverage-third-party-drawn`, with `#root-plugin-index` and
  `#coverage-keep-better-first-person`, 2026-09-25). Both passes used to skip an
  armour when ANY armature named a UBE race, except the body pass, which minted
  over every slot-32 armour anyway; and the exclusion scan's plugin half excluded
  any armour whose plugin (winning or not, active or not) listed a `!UBE\`
  armature. So a body armour whose hand-made patch draws the file we convert drew
  it twice, and an armour whose patch names a mesh that exists nowhere drew
  nothing. Now, in both passes, an armature T on the WINNING armour record
  qualifies when (a) it names a UBE_AllRace race of `UBE_RACE_FIDS_24` (primary or
  additional), (b) its female world mesh (MOD3, else MOD2) is live in the GAME
  view (`_game_view_mesh_resolver`: loose in overwrite, an enabled mod -- our own
  output included, the game loads it -- or Data; or in an archive the profile's
  Skyrim.ini lists or an ACTIVE plugin loads by name; a mesh this run converted
  counts too; not `_mesh_exists_anywhere`, which leaves our output out and reads
  unloaded archives; with no game view only this run's meshes count, so ours is
  minted -- a double draw, never an armour left with nothing), and (c) when the ARMOUR has slot 32, 34 or 38, that mesh is
  under `!UBE\`. (c) is judged per ARMOUR, not per armature, as the census
  measured it: the gloves armature of a cuirass-and-gloves patch on the original
  path does not draw our gloves. Each armature S we would mint is drawn by a
  qualifier of the same file (compared without `!UBE\`, `meshes\`, `.nif` and a
  `_0`/`_1` suffix, whatever its slots), else by slots (`#r9-fallback-safe`,
  below). The qualifiers' races come off S's: S
  is minted only for the UBE races no qualifier draws (the first of them
  primary), and not at all when none is left; one minted record serves every
  armour that lists it, so it targets the union, all races as soon as one armour
  needs them all. The first-person guard (`#coverage-keep-better-first-person`)
  does not let T draw S when S's MOD5 was converted and T's MOD5 is not under
  `!UBE\` -- empty included, the worse first person
  (`CBBE2UBE_NO_COVERAGE_KEEP_BETTER_FIRST_PERSON=1`). The coverage step asks the
  exclusion scan for its SkyPatcher half only (`halves=("ini",)`): an armature an
  INI adds is on no armour record; the conversion planner keeps both halves. The
  plugin index (`paths.plugin_file_index`) holds ROOT plugin files only --
  overwrite > enabled mods by MO2 priority > Data, every mod root in name order
  with no modlist -- because the recursive walk read our own un-loaded
  per-source copies in `_unmerged_patches` for 22 third-party plugin names; the
  two only work together (root-only alone left a helmet whose UBE mesh exists
  nowhere with no armature; the rule on the old index read our old mints as
  third-party armatures, +308 links). The NPC-worn cache keys on the index mode
  and `validate` looks masters up in the index's priority order. Live replay:
  -2 links (one dress drawn twice from the same file), +2 (two pouches whose UBE
  and source meshes exist nowhere, a no-op in game); 375 armours drawn by
  another mod's armature, 2 kept by the first-person guard; the same with one
  mod excluded. One switch, `CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN=1`, restores
  the blanket skip, the slot-32 exemption, both exclusion halves and the
  recursive index -- byte-identical to before.
- **Past the file match, a qualifier draws an armature of ours only when its
  slots are the SAME, and only when nothing ties** (`#r9-fallback-safe`,
  2026-09-25, inside `#coverage-third-party-drawn` and behind its switch). The
  first cut handed an unused qualifier to the first S, in the armour's list
  order, whose BOD2 merely OVERLAPPED it, and the body pass judged only what the
  female-guard, world-mesh and nude-skin rules had left. So another mod's UBE
  cuirass whose BOD2 also lists slot 33 "drew" our gloves, and the twin of a
  cuirass a guard had dropped did the same: the gloves were not minted and had
  no armature at all on UBE actors -- the one outcome this project forbids. Now
  (1) the file match runs over every armature of the armour that
  names no UBE race, whatever its primary race (a race-list one too), not only
  the ones left to mint, so a qualifier that is the
  UBE version of a dropped piece is used up by it; and (2) an unused qualifier
  draws S only when its BOD2 slot set EQUALS S's (the armature's own, else the
  armour's), it is the only such qualifier for S, and no other armature without
  a twin (ours to mint or dropped) has that slot set. Why equality: overlap and
  containment both let a multi-slot piece (a cuirass that also claims the hands
  slot) stand for a single-slot one it does not draw, and a subset qualifier
  (gloves) cannot stand for a cuirass-and-gloves armature either; only the same
  slots say the two pieces occupy the same place on the body. Why ties mint: a
  wrong guess skips a piece, the right guess saves only a double draw, so an
  undecidable pairing is minted -- and the answer no longer depends on list
  order. Two qualifiers that split S's races by slot are minted over too (a
  double draw). The file match, the race subtraction and the first-person guard
  are unchanged. Live replay: of 377 armours with a qualifier none reaches the
  slot rule and none has a qualifier used up only by a dropped armature; output
  byte-identical with the rule on and with the switch set.
- **A female slot is never filled from a converted MALE mesh when it names a mesh
  of its own** (`#coverage-female-guard`). It keeps its source path (hands, feet,
  accessories), or the armature is not minted (slot-32 body, like an unconverted
  vest). A source with no female model, or a female path that exists nowhere
  (loose or in any archive), keeps the male mesh — what the engine draws, and the
  female-only selection's own rule for a dead path. Kept, not-minted and dead-path
  slots are counted and warned; the no-female-model case is not.
  `CBBE2UBE_NO_COVERAGE_FEMALE_GUARD=1` restores the fallback.
- **A dead female slot draws the vanilla female counterpart, else the male mesh**
  (`#coverage-female-standin`, the user's rule 09-25). A MOD3 (paired with MOD2)
  or MOD5 (paired with MOD4) whose path exists nowhere and was not converted
  looks its paired male SOURCE path up in a counterpart map built once per pass
  from the winner scan (`_female_standin_resolver`): armatures a game master or a
  `cc*.esl/.esm` DEFINES, winning record DefaultRace-primary, MOD2 -> MOD3 and
  MOD4 -> MOD5 kept apart. Exactly one distinct female path (13 MOD3 and 12 MOD5
  male keys are ambiguous on the live load order), and converted (`_ube_exists`):
  the slot is written `!UBE\` + it and its MO?T/MO?S are dropped. This outranks
  the converted male mesh, and it also runs where the male mesh was NOT
  converted — the planner pools MOD3 and MOD5 (census backlog 7), so a
  follower cuirass with a dead world mesh and a live first-person one converted
  only the first-person female; `_world_mesh_converted` therefore counts a dead
  MOD3 with a stand-in as converted, or that cuirass stays unminted. One
  resolver is handed to both; `_converted_model_exists` is not hooked.
  Otherwise the male mesh: converted (the guard's dead path), or — on a
  NON-BODY armature (`_nonbody_male_as_is`: its BOD2, else its armours' slots,
  outside `_BODY_SLOT_BITS`, no cloak-named model, the male mesh not skinned to
  a body-fit bone) — the unconverted male path as it is. The mesh is read
  where the game loads it, loose or out of an archive, into memory
  (`_mesh_exists_anywhere(...).body_fit`, a raw SSE header + skin-instance
  reader, `_nif_bytes_body_fit`; it agreed with the pynifly test on 600/600
  sampled live meshes); unreadable, no slots, or no lookup keeps the dead path.
  Neither goes into `male_fallback_log`, so `restore_female_models` never undoes
  them. Live replay: 10 slots take a stand-in (the 8 former male-mesh slots, a
  cuirass that was not drawn at all, its gloves' dead path), 3 non-body pieces
  (two hoods, a helmet) draw their male mesh; 43 dead slots stay, 42 with no
  male mesh at all and one body-fit underskin — a NOTE counts them, grouped by
  why the male mesh was not drawn instead: `_file_declined` tags each slot whose
  male mesh exists with `_dead_kept_why` (`body`: a body slot or a body-fit male
  mesh; `cloak`; `unread`: no slots, no reader, or an unreadable mesh), a
  report-only mirror of `_nonbody_male_as_is`'s refusals kept apart so the rule's
  lines stay put. Each group prints three slots and its own "more".
  **Known limitation: non-ASCII model paths** (review 09-25, measured). The
  game reads a model path as cp1252 bytes; `esp.encode_zstring` writes UTF-8
  for every string we mint. `rebuild_arma_payload`'s main branch decodes the
  source path as UTF-8 with errors ignored (a cp1252 `é` is dropped, so the
  written path names another file), and the stand-in and as-is branches decode
  cp1252 -- what their lookups check -- and write UTF-8 (`é` becomes two bytes).
  So no minted model path round-trips a non-ASCII byte: the new branches differ
  in HOW the path breaks, not whether, and the as-is slot is then counted as
  drawing a mesh it will not find. It is the old class, one fix for all of it
  (write the source bytes verbatim, cp1252 for paths we compose). Live: 0 of
  the 3,945 armature rebuilds of a coverage replay carry a non-ASCII model byte;
  1 of 8,914 winning armatures in the load order does (a creature's, never
  minted). A male mesh
  in a female slot that is NOT dead (a female slot naming the male path, or no
  female model) is outside the rule and untouched. Nested under the guard.
  `CBBE2UBE_NO_COVERAGE_FEMALE_STANDIN=1`.
- **The loose half of that lookup is one listing** (`#loose-mesh-index`, 09-25).
  `_mesh_exists_anywhere` checked `<dir>\meshes\<path>` in every loose folder
  (overwrite, enabled mods by priority, game Data: ~3,300 live) before it asked
  the archives, so each archived or dead path cost ~3,300 file checks; the
  stand-in's dead-path questions made the live coverage replay 128 s instead of
  ~33 s (94 s inside the lookup, 1,934 questions). Now `_LooseMeshIndex` walks
  every loose `meshes` subtree once, on the first question, in that order, and
  maps each file's lower-case path to the FIRST folder that has it (`body_fit`
  reads that copy). Same answers as the file check: links and junctions are
  followed (a link back into its own ancestry is not listed again); a missing
  `meshes` folder is nothing; a folder that cannot be listed, or holds a
  non-ASCII or near-MAX_PATH name, leaves its paths to the per-folder file check,
  as does any path Windows resolves beyond a listing (`_listing_can_answer`:
  `.`/`..`, trailing dot/space, `~` short names, device names, non-ASCII). The
  archive fallback is unchanged. Live: 143,356 paths (~21 MB) listed in ~1 s;
  every one of the 1,533 distinct paths the replay asked got the same answer
  (exists, `body_fit`, first folder) as the per-path probe; output byte-identical.
  `CBBE2UBE_NO_LOOSE_MESH_INDEX=1` probes per path again.

### What a coverage armature may draw

The body pass mints an armature per winning DefaultRace armature, and until
09-24 three kinds of minted slot still drew a CBBE mesh on the UBE body. Each
rule is judged per ARMATURE (its own BOD2, else the armour's), counted, and
reported through `_report_coverage_holds`:

- **A body armature needs a converted WORLD mesh** (`#coverage-world-mesh`).
  Admission was "any of MOD2..MOD5 converted", so a converted first-person mesh
  admitted a torso whose MOD3 was CBBE. Now a slot-32 armature is minted when
  MOD3 was converted, or MOD3 is absent, empty or dead (exists nowhere) and MOD2
  was converted — the same male-for-absent-female rule the female guard keeps.
  It runs after the female guard, so the guard's counts are unchanged. Live: 87
  armatures on 89 links, all with only first-person meshes converted (62 links
  through one shared vanilla first-person torso); 88 armours lose their only
  armature, 62 of them children's clothing (the user skips children entirely).
  Hands/feet and slots 34/38 keep their source mesh by design and are not
  touched. `CBBE2UBE_NO_COVERAGE_WORLD_MESH=1`.
  *Reporting* (`#world-mesh-partial-report`, review 09-24): an armour whose
  torso armature is withheld but whose hands/feet armature is minted stays a
  target, so it was in neither the "left without one" count nor the names, yet
  nothing draws its slot 32 on UBE. The body pass records it
  (`world_mesh_partial`) when no minted armature left draws slot 32 and it is
  still a target at the end (a later rule that empties it reports it instead);
  the warning counts it ("drawn without the body piece") and names it
  ("no body piece:"). Both name lists put adults first, a child piece being
  what source selection already calls one (`_is_child_content_asset`, by
  EditorID). Live replay: 1 partial armour; of the 80 full drops 19 are
  children's clothing by that test, and they had filled all five named lines;
  now the five are adults' pieces (not all outfits). The test is by name only,
  so a child's unique clothes named after the child still sort with the adults.
  Report only: every plugin and sidecar byte-identical, so no switch.
- **Nude hands and feet draw the UBE body's own** (`#coverage-nude-skin`). A
  slot-33/37 armature whose MOD3 is `femalehands`/`femalefeet` under
  `actors\character\character assets\` (folder AND exact basename: a basename
  test alone catches real armour named after the body) is pointed at
  `!UBE\Hands\femalehands_tangent_<w>.nif` / `!UBE\Feet\femalefeet_tangent_<w>.nif`,
  the paths UBE's own naked armatures use, with MO3T dropped — only when that
  mesh resolves (loose, overwrite or archive); otherwise it is not minted. An
  armour that also lists a nude torso is a race skin: its nude parts are not
  minted, since a UBE actor wears UBE's own skin. The validator's `missing-nif`
  check looked only in our output and would have called these a startup-crash
  risk; `validate_patch(mesh_resolves=...)` now accepts a path the coverage step
  pointed outside on purpose and checked. Live: 4 armatures, 2 redirected (an
  NPC costume's bare-feet boots and bare-hand gloves), 2 on a skin. The report
  counts emptied armours per reason (skin or unresolved), and an armature a
  skin skips but an item mints is not listed as left out.
  `CBBE2UBE_NO_COVERAGE_NUDE_SKIN=1`.
- **A hand-made UBE twin** (`#coverage-ube-twin`). Where our output has no
  `!UBE\<path>` but a third-party mod ships one loose (never our output, never
  an excluded mod, never MO2's overwrite), the minted slot points there. As
  built it only moved where a slot points and never admitted an armature;
  since #skip-built-ube-path (below, on by default) the body pass also admits
  an armature whose mesh is such a twin. The census moved 8
  links: the 2 it was written for (boots and gloves reusing a UBE-patched
  armour's armatures) plus 4 dismembered-body addons and 2 links of one choker
  from the user's UBE BodySlide build -- every one a UBE version of the same
  mesh. `CBBE2UBE_NO_COVERAGE_UBE_TWIN=1` turns it off, and #skip-built-ube-path
  with it (the 54 meshes left to builders are converted again).
- **A `meshes\` model path is written `!UBE\X`** (`#twin-path-strip-meshes`).
  An armature may spell its model `meshes\X.nif`; the engine reads it as X, so
  the twin lookup takes the folder off and finds `meshes\!UBE\X.nif`. The
  rebuild put `!UBE\` in front of the RAW path -- `!UBE\meshes\X.nif`, read as
  `meshes\!UBE\meshes\X.nif`, a minted path that exists nowhere (the missing-nif
  rule's load-crash cause). `_ube_twin_slots` recorded the same string, so the
  piece validator whitelisted it, and the postflight resolver took the folder off
  again and called it resolved. Now `_strip_meshes_prefix` runs wherever a
  `!UBE\` path is composed from a source path: `rebuild_arma_payload`
  (`strip_meshes_prefix`, passed by both coverage passes), `_ube_twin_slots`,
  `_converted_model_exists` (so it asks what the lookup asks -- which also means a
  body armature spelt `meshes\X` whose mesh WE converted is now admitted and
  minted `!UBE\X`; before, it was not admitted at all; live: 0) and
  `restore_female_models` (it checked the stripped file, wrote the raw path). The
  postflight judges the string written: a `!UBE\meshes\...` path is looked up
  as written (`twin(..., as_written=True)`). The per-source patch is not touched:
  its converted check keeps the raw path, so it never prefixes one (live: 12
  such slots in the deployed plugins, 0 with a converted mesh). Live coverage
  replay: 0 slots at default settings (byte-identical to the parent), because
  #claim-meshes-prefix claims the one case; with
  `CBBE2UBE_NO_CLAIM_MESHES_PREFIX=1`, 1 armature / 2 slots (a softbody pack's
  nude suit, MOD3 + MOD5) move from `!UBE\meshes\...` to `!UBE\...`, where the
  user's UBE BodySlide build ships it; nothing else changes. Switch set:
  byte-identical to the parent in both settings. Female re-check sidecars: 0 of
  29 entries prefixed. `CBBE2UBE_NO_TWIN_PATH_STRIP_MESHES=1`.
- **A mesh another mod already built for UBE is not converted**
  (`#skip-built-ube-path`). #skip-already-ube judges "already UBE" by the armour
  records of the SAME plugin; a refit plugin overriding only armatures sends its
  meshes to conversion, and our output (above the builder in MO2) then replaces
  the hand-made UBE mesh in game. Now the planner leaves a weight base to its
  builder when EVERY variant planned for it has a built twin
  (`_third_party_ube_twin_lookup`, the twin rule's own lookup, so it runs only
  with #coverage-ube-twin on), and moves an earlier run's copy with its `.tri`
  and `.xml` to `_superseded\` -- the output folder is never cleaned and a stale
  copy would still win the path. The body pass admits an armature whose mesh is
  such a twin (`_admits`), or the armour records the builder's patch does not
  reach would lose coverage. Live: 54 meshes left to builders (14 where ours had
  won the path, 40 where the user's UBE BodySlide build already did); coverage
  replay 9381 -> 9382 links, 0 removed, 0 re-pointed, +1 armour newly drawn (an
  enchanted outfit reusing a UBE-patched armature). Without #claim-meshes-prefix
  the twin admission also minted a second nude suit on a softbody pack's own UBE
  one (+2 links) -- that commit is why it does not.
  `CBBE2UBE_NO_SKIP_BUILT_UBE_PATH=1`.
- **A superseded base leaves `meshes\` whole, and the partner fill stays out of
  it** (`#supersede-whole-base`). The move above took only the variants the run
  planned. A source shipping only `_1` plans only `x_1`, so the `x_0` an earlier
  run's partner fill wrote stayed in `meshes\!UBE`, and after the batch
  `_complete_weight_partners` copied it back to `x_1` -- the builder's path,
  beating the hand-made mesh again on every run (the `.tri` gone, so it morphed
  with the builder's). Now `_supersede_whole_bases` moves every `_0`/`_1` of a
  superseded weighted base in our output with the `.tri` and `.xml` (an
  unweighted mesh moves alone: it has no partner), the planner records the base
  on `superseded_weight_bases`, and the batch's fill skips those bases, fill and
  refresh both. A move that fails (a file held open) is **rolled back**, the base
  is left whole and a warning names it. Why not leave the moved half: a whole
  stale base is our old conversion with its own morphs -- the wrong mesh, but a
  consistent one, exactly the state before #skip-built-ube-path, and the next
  run moves it; half a base pairs our NIF with the builder's `.tri` (it morphs
  the wrong vertices) and the fill completes it from our half. A file that cannot
  go back either is named in a second warning. Live: 27 superseded bases (the
  54 meshes), every source ships both weights, so both variants were planned and
  0 bases had a partner left for the fill -- the change moves nothing on the
  reported modlist today; it bites on the first `_1`-only source whose builder
  sits below our output. `CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE=1` moves only the
  planned variants again, silently.
- **Wigs** (`#coverage-wigs`, the user's call 2026-09-24). The non-body pass
  treats a hair-only (31/41) armour as headgear with a gold value or the
  ArmorHelmet keyword; wigs have neither, so 100+ playable wigs were invisible
  on UBE. Playable in the winning record with a non-empty FULL now counts too
  (`_is_playable_named`), and every DefaultRace armature is minted as for a
  helmet -- including the hidden `_CollisionbodyX` SMP collider most wigs carry,
  which is a BodySlide build of the 3BA body: 0.38u median / 1.05u p95 from the
  UBE body surface (0.27 / 0.68 on 3BA), and `convert_nif` only copies it (its
  physics link is not rebuilt), so it is minted with its own mesh. Live: 101
  wigs, 196 links (95 hair + collider, 6 hair only), 0 removed; depends on
  #coverage-beast-variant, without which 6 wigs also drew their Khajiit
  variant. `CBBE2UBE_NO_COVERAGE_WIGS=1`.
- **A beast-only variant is not minted** (`#coverage-beast-variant`). Both
  passes minted every DefaultRace-primary armature for all UBE races. A beast
  patch often adds its variant as primary DefaultRace with only Khajiit/Argonian
  (+ vampire) additional races; the playable races carry no armor race, so no
  human draws it, but a UBE actor drew it over the human armature. Such an
  armature (additional races non-empty, every one a Skyrim.esm beast race) is
  skipped in both passes; one with no additional races is unchanged. Live: 37
  armatures, 58 links removed, 0 added; 3 Argonian-only items lose their only
  link (invisible on UBE, as on every human race). `CBBE2UBE_NO_COVERAGE_BEAST_VARIANT=1`.
- **The mannequin race does not make a beast variant human**
  (`#beast-variant-non-actor`). A beast patch's variant often lists the Khajiit
  races AND Skyrim.esm ManikinRace (so a mannequin can display it). Mannequins
  ARE actors that wear armour (25 NPC_ records live); what makes the race safe
  to ignore is that no playable or UBE-race actor has it, and the source
  armature is untouched, so mannequins still display the item. The beast
  test read the mannequin race as a non-beast race, so the variant was minted
  and a UBE actor drew an armature no human draws. `_is_beast_variant` now
  drops the races in `_NON_ACTOR_RACES_24` (plugin-qualified: Skyrim.esm
  `0x10760A` only) before judging, and needs at least one race left
  (`bool(actor)`), so an armature listing ONLY the mannequin race is not a
  variant and is minted as before. A FIXED list, not the RACE Immobile flag:
  stationary enemy races and turret or totem races carry that flag too and can
  wear armour. A mod's own display race is not covered unless added. Live
  replay: 30 armatures, 30 links removed (wig and earring variants, all
  non-body; each armour's only link, and none has an armature a human draws,
  so they are invisible on UBE as on every human race), 0 added, 0 re-pointed;
  the body pass is byte-identical. The beast report line says how many also
  listed the mannequin race. Nested, with its own switch:
  `CBBE2UBE_NO_COVERAGE_BEAST_VARIANT=1` turns the whole rule off, which is not
  the parent's output. `CBBE2UBE_NO_BEAST_VARIANT_NON_ACTOR=1`.
- **A body armour's hood rides with it** (`#coverage-body-accessory`). The body
  pass kept only armatures with a converted mesh or a hands/feet slot; the
  non-body pass skips any armour with a deforming slot. A robe's hood armature
  (31/41/43) was covered by neither, so the hood was missing on UBE. Now a
  DefaultRace armature of the armour whose own BOD2 names slots, none of them a
  deforming, body or candidate conversion slot, and whose mesh is not cloak-named
  (it was never a conversion candidate, so its own mesh is what it draws), is
  minted with the deforming ones (UBE-primary, own mesh -- what the non-body
  pass does for the same hood worn alone); it never mints alone, so an armour
  the world-mesh or female rules leave out stays out. Live replay: 110 links on
  109 armours added, 0 removed, 0 re-pointed, 26 armatures (hoods and hats, plus
  helmets and costume heads built into body armour; a body-fitted cape stays out).
  `CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY=1`.
- **A riding hood obeys the race rules** (`#accessory-race-guard`). The
  accessory list above was built after the beast rule took its variants out of
  `to_mint`, and tested only DefaultRace and slots, so a beast-only hood variant
  came back as an accessory (two hoods on a UBE actor; the beast switch had no
  effect on it) and a hood already naming a UBE race got a second UBE copy over
  it. The list now skips the pass's own `_bv` (so
  `CBBE2UBE_NO_COVERAGE_BEAST_VARIANT=1` still brings the variant back) and any
  armature with a UBE race (`v[4]`), as the race-list rule already does (HRL-ze).
  The non-body pass had neither gap: it drops `_bv` and skips the whole armour
  when any armature carries a UBE race. The guard here is per ARMATURE, so it
  does not catch a hand-made UBE hood shipped as a separate UBE-primary armature
  next to the DefaultRace human hood (`v[3]` is not DefaultRace, so it is never an
  accessory candidate, and the human hood still rides along): that is the
  per-armature "already drawn" test of the R9 double-draw rule, still open.
  Live: 0 of the 26 accessory armatures is either, so the output is unchanged
  today. `CBBE2UBE_NO_ACCESSORY_RACE_GUARD=1`.
- **A robe's draped cape rides with it** (`#coverage-body-cloak`). The
  accessory list above skipped every cloak-named armature, on the worry that an
  unconverted cloak is body-fitted cloth drawing its CBBE fit. But the
  conversion's crash guard drops a cloak on a free slot (35) whose mesh has no
  body-fit bone, so a worn robe with such a cape drew the robe on UBE and
  nothing for the cape -- the one cloak-named piece of the modlist a UBE actor
  drew nothing for (the other unconverted cloaks are drawn on their own mesh by
  the non-body pass, or covered by another mod). A cloak-named DefaultRace
  armature is now admitted as an accessory when every world model it names
  (MOD2, MOD3) is, in the copy the game loads, skinned and bound to no
  thigh/calf/butt/breast/belly bone (`_nif_bytes_unfitted_skin`, read through
  `_mesh_exists_anywhere(...).unfitted_skin`); an unskinned, body-fitted,
  missing or unreadable mesh, or a lookup with no reader, keeps it out (fail
  closed), so the worry above still holds for body-fitted cloth. It is minted
  UBE-primary on its own mesh, as the non-body pass mints the same class of
  cloak worn alone. Beast variants and armatures already naming a UBE race stay
  out even with `CBBE2UBE_NO_ACCESSORY_RACE_GUARD=1`: no parent output drew a
  cape here, so that switch has nothing to restore. The dead-armature rule
  still judges it; the report counts it among the accessories. Live replay:
  9834 -> 9835 links, +1 (the robe -> its slot-35 cape armature, own mesh,
  UBE-primary), 0 removed, 0 re-pointed; the non-body pass is byte-identical.
  `CBBE2UBE_NO_COVERAGE_BODY_CLOAK=1` (nested under
  `CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY=1`).
- **An armature whose meshes exist nowhere is not minted**
  (`#coverage-dead-armature`). The body pass admits a hands/feet armature by
  its slot alone and the non-body pass keeps a piece's source mesh, so an
  armature whose every named mesh is missing from the whole modlist was minted
  too: a link that draws nothing for anyone (its source armature draws nothing
  either). Now an armature that names at least one non-empty MOD2..MOD5 path,
  none of them alive, is not minted. A path is alive when this run converted it
  (`_converted_model_exists`, `meshes\` stripped), a third-party mod ships its
  `!UBE\` twin (the twin lookup), or it or a weight sibling (`_weight_siblings`:
  `_0`, `_1`, suffixless) exists per `_mesh_exists_anywhere` (overwrite, enabled
  mods, game Data, any archive but voice/sound/facegen). An armature naming no
  mesh (a slot placeholder that hides a body part) is never dead, nor is one
  #coverage-nude-skin points at the UBE body's own hand or foot, nor one whose
  minted copy draws a mesh it does not name (`_dead_slot_draws`, only when
  #coverage-female-standin is in play): a dead female slot the rebuild fills
  with the vanilla stand-in -- the pass's own `_female_standin_resolver`,
  keyed on the male path whether or not that exists -- or whose paired male
  path exists per the lookup the rebuild asks (the "male as it is" branch;
  asked of a body piece too, the lenient side). A hood the rule drops is not
  counted in `body_accessory` either, so the report does not call it both
  "drawn on UBE with the body" and "not minted". It is the LAST
  filter on `to_mint` in both passes -- after DefaultRace, beast, race-list,
  third-party and the exclusion hold in the non-body pass; after the female
  guard, world-mesh, nude-skin, body-accessory and third-party rules in the body
  pass -- so a hood or race-list armature is judged too and every other rule's
  counts are unchanged but for the dead ones themselves (the race-list,
  exclusion-kept and wig lists name only what is minted; the accessory list
  drops a dead hood but, as before this rule, still names a hood another mod's
  UBE armature already draws); an armour
  left with nothing is `dead_dropped` and gets
  no link. The verdict is memoised per armature (`_dead_armature_judge`). The
  lookup is its own (`_dead_armature_lookup`): the female guard's or world-mesh
  rule's copy when one was built, else one built for it -- those are None when
  their switches are off. No lookup (the modlist cannot be read) fails OPEN:
  every armature is minted as before, with a warning. The lookup lists every
  archive in the folders, loaded by an active plugin or not, so a mesh only in
  an unloaded archive reads as alive: the lenient side (minted as before). Live
  replay: 41 source armatures (city-guard boots, gauntlets, helmets and shields
  of one overhaul, a mod's decal armatures, satchels and pouches, divine
  amulets, horns, an eyepatch), 9882 -> 9834 links (48 removed, 0 added, 0
  re-pointed, 0 from an armature with a live path); 47 armours lose their only
  link. `CBBE2UBE_NO_COVERAGE_DEAD_ARMATURE=1`.
- **An armature whose primary race is not DefaultRace** (`#coverage-human-race-list`).
  Both passes minted only DefaultRace-primary armatures. An armature re-authored
  with an Argonian or a custom primary that lists the human and mer races as
  additional races (re-made rings and amulets, a custom race's shields and
  knight's boots) draws on a vanilla human woman and on nothing of a UBE race.
  Where the old rule admitted NO armature of an armour, `_race_list_admits` now
  takes an armature with another primary when: the WINNING armour record is
  playable or in the NPC-worn set (`_batch_npc_worn_armos`, handed to both passes
  as `npc_worn_armo_abs`); the armour is no RACE's or NPC_'s WNAM (read in the
  passes' own plugin read, `_collect_skins`); the female world mesh (MOD3, else
  MOD2) is neither absent nor an effect (`effects\` or a file named `fx*`); and
  it names DefaultRace or a vanilla human/mer race or vampire variant. The minted
  armature targets the UBE counterpart of each vanilla race it names
  (`UBE_RACE_FOR_VANILLA_24`: `<X>Race` -> `00UBE_<X>Race`, checked against
  UBE_AllRace.esp's EditorIDs and UBE's own race-compatibility pairing), all 16
  only when DefaultRace is among them -- a Wood-Elf-only piece stays Wood-Elf-only.
  In the body pass the rule asks the DefaultRace rule's own mesh test
  (`_mesh_admits`): a hands/feet armature keeps its source primary and races
  through `coverage_arma_race_targeting`, with only the mapped UBE races added (a
  custom primary whose plugin the patch cannot master falls back to the mapped
  UBE primary rather than dangle); a body armature still needs a converted mesh.
  Those body meshes are never converted -- source selection keeps DefaultRace
  armatures only (`_player_armor_mesh_bases`), and nothing shows these
  archive-shipped meshes are CBBE-shaped -- so the body pieces stay uncovered, as
  before; widening that is the conversion side's call. Live replay: 9492 -> 9773
  links, +281 on 281 armours (275 accessories, 6 hands/feet), 154 armatures (151
  to all 16 UBE races, 3 to 15 -- their source lists no Nord vampire), 0 removed,
  0 re-pointed; the non-body coverage now fills two ESL pieces. 6 of the
  accessories are slot-35 cloaks drawn with their own mesh, as the pass already
  draws 18 DefaultRace cloaks. The filters kept out 46 armours a bare race-list
  rule would take (43 non-playable that no NPC wears -- severed heads, a
  creature's weapon dummy, props -- 2 with no world mesh, 1 skin); the mesh test
  holds 30 more (naked skins no record links, a custom race's cuirasses, a
  slot-38 wig, an effect veil). Switch set: byte-identical to the parent.
  `CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST=1`. The worn set is asked for with
  `for_coverage=True`, which skips `CBBE2UBE_NO_NPC_WORN_NONPLAYABLE` (same
  cache): that switch turns off only the conversion of worn non-playable armour.
  It first emptied this rule's worn set too, so one switch changed two features.
  Only the "NPC-worn off, race-list on" setting changes: live, 280 -> 281
  race-listed armours (one worn non-playable neck piece, 1 armature in the
  second non-body ESL), and that setting's coverage is now byte-identical to
  the default. Default and both-switches-set: unchanged. A pure decoupling, so it
  has no switch of its own. A failed read of the worn set now names both
  consequences in its warning.
- **A claim path written with `meshes\` in front** (`#claim-meshes-prefix`). The
  engine reads `meshes\!UBE\x_1.nif` and `!UBE\x_1.nif` as one file; the
  SkyPatcher half of the claim test stripped the folder, the plugin half did not.
  Live: 10 more armours claimed, all a softbody pack's own UBE nude suits, none
  of them linked by us, 0 claims lost; #skip-already-ube then drops the suit's 2
  meshes from conversion (the user's UBE BodySlide build of it wins the path in
  game). `CBBE2UBE_NO_CLAIM_MESHES_PREFIX=1`.

---

## Effect-shader glow overlays

Some armor (e.g. Daedric) carries additive glow decals as separate shapes with a
`BSEffectShaderProperty`, riding on top of a solid plate. Three things break if the
converter treats them like normal cloth:

- **Equip/render CTD.** The UBE body-blend re-skin re-skins the decal to body bones it
  never had (and scale bones), and a skinned `BSEffectShaderProperty` CTDs the engine
  (`call [rax+0x28]`, garbage pointer). Fix: glow shapes keep their **source skin
  verbatim** (skeleton bones only, matching the proven-good vanilla decal), ignoring the
  re-skin. Dropping scale bones alone wasn't enough — the re-skin's other body bones had
  to go too.
- **Clipping.** The decal must move exactly with the plate it sits on, so it's made to
  **ride** the plate (inherit its post-fit vertex displacement) instead of being fit
  independently and drifting off it.
- **Lost glow.** The glow's animation-controller chain (and buffer/vertex-fade) must be
  transplanted onto the copied shape, or the effect renders static or white.

---

## Where the hard-won detail lives

Running logs that complement this doc. These are working notes rather than
product documentation, so they live on the `testing` branch only — `main`
carries the tool itself. Code comments citing them by shorthand (e.g.
"CLIPPING_LOG C1", "ROBUSTNESS_AUDIT L3") point at these:

- `CLIPPING_LOG.md` — in-game clipping/crash finds and their diagnoses.
- `ROBUSTNESS_AUDIT_*.md`, `CONVERTER_AUDIT_*.md` — point-in-time audits.
- `DESIGN_P*.md`, `DESIGN_PROPOSALS.md` — design-only proposals, not built.
- `CHANGES_*.md` — per-investigation change notes.
- `LOCAL_ASSET_SAMPLES.md` — maps the synthetic mod/asset names used in tracked
  comments and test fixtures back to the real assets they stand in for. Tracked
  content is kept mod-agnostic; this preserves the provenance of a measurement or
  a regression case without publishing it. **Add a row in the same change as any
  new substitution.**

These are gitignored, and `tests/test_public_repo_hygiene.py` asserts they stay
untracked — the repo is public and every one of them names specific mods.

---

## Pose-driven clipping: what moves, what fixes it, and what does not

Everything the converter did about clipping was solved at BIND pose — an A-pose nobody
stands in. Measuring under a pose set (`scripts/analysis/multipose_clip_test.py`) showed a class
of failure no bind-pose metric can see: armour that is clean at rest loses coverage the
moment the actor moves. On a rigid cuirass, 11.0% of covered breast is exposed by a
spine twist; on a skirted piece, 14.7% of covered butt by a sprint; on a vanilla
cuirass, 83.7% of covered thigh by a crouch.

### The measure is a REGRESSION, not an exposure level

Body verts COVERED at bind and EXPOSED under a pose, as a fraction of the covered set.
Raw exposure cannot be compared across garments — a bikini is 90% exposed by design and
a robe 0%, neither of which is a defect. Each garment is its own baseline. The same
principle separates a defect from a neckline: exposure is only a defect when the
garment is right there (within ~2u) AND well inside its own boundary, which is why
`classify_exposure` splits poke / neckline / uncovered.

### Two levers, and they are not equal

**Clearance** (push the garment out) works but pays in volume. A uniform push took
breast exposure 11.0% -> 2.5% — while moving every vertex, which reads as baggy. A
targeted, exposure-driven demand (`research/pose_clearance.py` — moved out of
`src/` because it does not ship) reaches 3.5% while moving
0.9-2.3% of the garment, and is default OFF pending calibration.

**Deformation matching** (give the garment the body's weights so the two deform
together) is strictly better and costs NOTHING in volume — the bind shape is
byte-identical, only the motion changes:

| region / pose | authored | clearance, best | deformation matching |
|---|---|---|---|
| thigh / crouch | 83.7% | (cannot reach it) | **5.4%** |
| breast / spine twist | 11.0% | 3.5% | **0.1%** |
| butt / sprint | 14.7% | 8.0% | **2.8%** |

The converter already has this as the M6 body-blend reskin. It is bounded two ways:
transferring weights onto authored physics cloth replaces its chain weights and the
skirt stops swinging, and the reskin's K-NN blend has a history of equip fly/spike
instability. Both call sites are therefore gated on `not _shape_has_hdt_smp_rigging`.

**Superseded in practice by the full-vector weight match (default ON).** The
per-family motion matches that followed this section each manage one bone family
and rescale the rest, so each buys a pose by selling another. The full-vector
match copies the covered body's whole weight row on hugging verts, so there is
nothing left to pay with: breast_side under a swing 12.81% → 3.52% and front
bust under a sprint 50.91% → 3.12%, with belly/butt/thigh unchanged and **zero**
vertex movement. Because it manages every shared bone, nothing may run after it —
a test pins the family-match order (leg → spine → arm → spine-twist →
full-vector). It is still a *skin* pass, so it inherits every exclusion above and
cannot reach simulated cloth; the chain rest-pose lift is what reaches that.

### Why the reskin does not run on most armour

Two gates, in sequence. A source that bundles an inline body routes to phase 2, so
phase 1's reskin is never reached. Phase 2's gate then ends in `not _is_morph_tri` —
excluding any shape carrying a source BodySlide morph TRI, which in a BodySlide-built
pack is nearly everything. `CBBE2UBE_RESKIN_KEEP=1` overrides it.

The stated reasons are a morph desync (the TRI is keyed to the source skin) and the
equip-fly instability. On the piece measured, the desync half is NOT reproduced: the
output TRI is regenerated post-reskin (it differs between reskin off and on), and
morph-follow under a full breast slider is identical either way (4.5% -> 12.1% in both).
The instability half can only be settled in game.

### A caveat about the numbers above

They come from `scripts/convert_one_armor.py`, which does not reproduce the auto
pipeline exactly — see METRICS.md. The divergence is small (mean 0.006u) and the
effects here are large, but a single-piece measurement is not a pack guarantee.

### What no offline metric here can see

Runtime physics (SMP cloth goes where the simulation puts it), BodyMorph/OBody
inflation beyond the fitted body, and equip-time instability. A full breast slider
takes exposure 4.5% -> 12.1% on a piece whose pose behaviour is clean — the morph path
is a separate, unexamined class, and on that piece it is the larger one.
