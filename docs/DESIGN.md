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
and the converted-mesh set its patch ESP is built from -- and nothing after it
reads another source's output, so:

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
  before; the result list keeps source order. The patch ESP and its
  `.espgen.json` snapshot are written here too (`_write_source_patches`, from
  inputs planning fixed), not at planning: a run cancelled or killed in the NIF
  phase leaves no patch or snapshot naming a NIF it had not yet written, so a
  `--plugins-only` refresh after it replays the previous run's. A source with
  no armour resolved pauses as well, with no units, so its `--copy-textures`
  copy lands in source order (the later source's file wins, as before).
- **Checkpoints.** No source can finish before the phase ends (each waits for
  its own smallest unit and every earlier source), so the report checkpoint is
  also written when a source's planning fails (the failure is on disk when it
  happens) and when every NIF of a source is in, with a `nif_phase` record:
  files converted so far and the sources whose NIFs are all in.
- **Retries.** A dead worker's in-flight units, and any unit whose answer shows
  a MemoryError (raised, or caught inside a fit pass), are re-run one item at a
  time once nothing else is in flight; only the re-run is delivered. An
  out-of-memory answer is never kept -- the source's patch ESP points at the
  planned NIF.
- **The vanilla sweep's self-heal.** When the sweep's planning fails, its
  serial retry (no pool, in-process) runs at the sweep's turn to finish --
  after every source before it, where one source at a time ran it -- not
  during planning, ahead of their NIFs and outside the chain their shared
  bases keep.
- **Supersede guard.** Planned first, a later source's supersede would move an
  earlier source's base BEFORE that source wrote it, and the earlier source
  would then write its half beside the builder's. On this schedule a base an
  earlier source claimed this run is held back (`_split_claimed_supersedes`)
  and that conversion stays. One source at a time -- the switch set, or
  `--workers 1` -- the earlier copy is already written and moves out with the
  rest, as it always did.
- **Progress.** One `[progress] 1 1` marker, then `[progress-nif] <done> <total>`
  over every source's files: the window's bar fills once, and with a single
  bar the window takes the per-file estimate as the run's (`gui._nif_status`).

`CBBE2UBE_NO_GLOBAL_SCHEDULE=1` converts one source at a time again, all of the
above included; `--workers 1` (no shared pool) and `--plugins-only` never use
the batch-wide schedule, so they behave as they did without it.

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
worker appends to it were not atomic then (#atomic-audit-append, below, has
since made each record land whole) and tore 14-16 lines a run -- it differs
between the two one-schedule runs as much as against the old schedule, and the
live file already carried 58 torn lines. The #hdt-xml-race destination-stem
class (175 NIFs, 88 garments, in this output) did not differ. Heavy units at
once: up to 3-4; memory re-runs: 0; supersedes held back: 0.
The finishes all land at the end (every source's smallest unit runs last):
first finish 1718 s into a 1756 s schedule, which is why the checkpoint
records the phase itself and a source's patch waits for its finish.

#### Folder spelling (`#planned-folders`)

Windows keeps the spelling a folder was created with, and a worker used to
create its piece's folder when it started converting it. When pieces spell one
folder differently, the folder was named by whichever piece reached it first,
on both schedules. For example, a loose mod file sits under `meshes\Armor\`
while an archive's pieces sit under lowercase `armor\`. One source at a time,
it depended on which of a source's concurrently started pieces made the
folder first. Both measured one-at-a-time runs gave `Armor`, but nothing
fixed that order, so both schedules now follow plan order rather
than copying one observed spelling. On the batch-wide
schedule it was the largest-first order across sources. Now each source
creates its pieces' folders at the end of its planning
(`_make_planned_folders`), in plan order, before any NIF converts. The first
source to plan a folder (sources plan highest MO2 priority first, the
vanilla sweep last) spells it, and within a source the first piece in plan
order does. Both schedules plan every source in source order, so they spell
every folder alike on every run. A folder that exists already keeps its
spelling. A folder the plan made that is still empty once the batch is done
(only a skipped piece asked for it) is removed then, not at its source's
finish. On the batch-wide schedule another source may still be writing into
it, and a later source must find it spelled as planned.
`CBBE2UBE_NO_PLANNED_FOLDERS=1` leaves folder creation to the workers again.
No NIF's bytes depend on it, so the `--incremental` fingerprint leaves it out.

**Measured 2026-09-26**, subset runs into fresh scratch outputs, 6 sources
including the vanilla sweep, 767 NIFs, 15 workers, PYTHONHASHSEED=1. All six
arms have the same 1303 compared files byte for byte and the same 164 folders.
The parent named `meshes\!UBE\armor` (485 files) in lowercase on the batch-wide
schedule and `Armor` one source at a time. Now both schedules name it `armor`,
which matches the parent's batch-wide run, and every file path and folder
matches case-sensitively. The sweep plans 304 pieces there: 296 spell it
`armor` (archive paths) and 8 spell it `Armor` (one loose-file set). The first
planned piece is lowercase (unit 1). The first `Armor` piece is unit 12, so
both sit inside the 15 units one source at a time starts together, and the
parent's `Armor` was that race's outcome. With the switch set, each schedule
named the folder the way the parent did on that schedule.

#### Report order (`#plan-order-results`)

A source's NIF results arrive in completion order. One source at a time that
order is a worker race. On the batch-wide schedule it is the largest-first
order. The report lists built from these results inherited the arrival order:
`pass_effects`, `pass_failure_pieces` and the per-source piece lists. And
because the batch-wide schedule writes a source's patch at its finish, the
patch validator's notes landed after the NIF notes. Now each source's results
are sorted into plan order once its NIFs are in (`_in_plan_order`). A result
is placed by its destination, or by its source file when it names no
destination. Ties are broken by status and reason. On the batch-wide schedule
the patch notes are moved to where one source at a time writes them.
`CBBE2UBE_NO_PLAN_ORDER_RESULTS=1` keeps the arrival order. Measured on the
same six arms: `conversion_report.json`, the failures file and every
per-source report are identical between the two schedules (run stamps and
timing notes aside). With the switch set, `conversion_report.json` matched the
parent's on each schedule. The per-source piece lists differed in order
between two runs of the parent's own code path, which is the race this
change removes.

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

**Every record lands whole** (`#atomic-audit-append`, `src/atomic_io.append_whole`).
All pool workers append to the one sink. A text-mode append on Windows is
seek-to-end then write, and a raw `O_APPEND` `os.write` is no better (measured:
8 processes x 500 records of up to 30 KB tore ~600-800 lines per trial either
way), so two workers could write at one offset and splice two records. A full
run tore 14-16 lines and lost ~30 records, a different set each run; the live
sink held 58 splice artefacts: 51 unparseable lines and 7 blank ones (a record
overwritten at its own offset by one exactly its length minus the newline
leaves only the newline, so a blank line is a lost record too; the readers
count both). Each record is now ONE write of the whole encoded line
under an exclusive cross-process lock: a byte-range lock at offset
0x7FFFFFFF_00000000 (`LockFileEx`; `flock` elsewhere), far past any data, so it
needs no second file and the OS drops it if a worker dies holding it. A lock
that cannot be taken still writes the record, unlocked. Per-worker files merged
by the parent were rejected: the sink is also written with no batch parent
(single converts, several converter processes sharing one
`CBBE2UBE_STANDOFF_LOG`) and appends across runs by design. The bytes are the
old writer's (UTF-8, `\n` as `os.linesep`). Record ORDER is still arrival order
across workers; the MULTISET is what is deterministic, so compare two sinks as
sorted lines. The glow diagnostic log, the only other file workers append to,
uses the same writer. The readers (`audit_sink.load`, `physics_rest_depth
.read_lift_log`, `survival_report.load`, `survival_sweep`) count and report the
torn lines of older sinks. `CBBE2UBE_NO_ATOMIC_AUDIT_APPEND=1` restores the old
unlocked writer.

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
   The filter asks EVERY folder, including those below the winner the plain probe
   never reaches, and `Path.is_dir` re-raises a PermissionError rather than answering
   False; so a folder whose check raises is kept in (`_may_have_dir`), and the plain
   per-folder probe decides -- it raises only if it gets that far, exactly as it
   would with no filter (ZPM-i, ZPM-j).
   The answers are the plain probe's -- same `_ci_join`, same order, same case rules --
   as long as the folders do not change while the scope is open, so the scope is one
   call and is dropped on return: the GUI's long-lived process never answers from a
   memo taken before the user re-ran BodySlide. Live, read-only, in separate
   processes: 264.8 s -> 69.6 s; the mesh index is identical key for key (4,562
   entries, 244 re-pointed) and so is the verdict line (122 moved / 179 kept).
   `CBBE2UBE_NO_ZEROED_PROBE_MEMO=1` probes every folder per question again.
   **A physics GAIN is taken** (`#zeroed-smp-gain`, 2026-09-25, user decision). The
   physics rule and the same-shapes rule held back every vanilla-armour piece whose SMP
   loose mesh rule 2 had swapped for a static prebuilt one: the user's zeroed build of
   the SMP design was verified and refused for its physics alone, so CBBE wearers got a
   skirt with physics and UBE wearers a static one. When today's source declares no
   physics and the verified build declares it at both weights, the build is taken and
   the same-shapes rule is waived, if at each weight: both sides carry a body the
   converter swaps (`_looks_like_inline_body`), so both go body-swap and the path change
   the same-shapes rule guards cannot happen; every sizeable body-skin shape of the
   build is such a body (else a body ships as cloth); the build's own XML pointer
   resolves (the converter's resolver), parses, and holds a generic, stiffspring or
   conetwist constraint anywhere in the tree -- nested in a `<constraint-group>`
   counts; a census that read it with `find` called five constrained XMLs
   unconstrained; and no shape the XML names, nor one carrying a non-skeleton bone it
   drives, is a stripped body the collision-proxy re-import would bring back
   (`_is_inline_body_name` lets it through) -- a hidden second body, the equip CTD the
   re-import's own comment names. And every SIMULATED collision shape must still have
   a partner once the conversion has pruned the XML (`#smp-gain-collision-partner`,
   same day): `_harden_hdt_xml_for_fsmp` drops every shape block whose name, case for
   case, is not in the converted NIF, and after the rule above every stripped body the
   XML names is one the re-import skips, so the gate prunes the XML to the build's
   shapes minus the stripped ones and replays the rest as FSMP reads them -- a shape
   simulates when a skin bone has mass (bone templates in document order; a bone first
   met undeclared, in a shape or a constraint, takes the unnamed default as it stands
   then), and two shapes collide only when each allows the other's tags (can-collide
   list, or, when empty, not a no-collide tag; tags without case). The converter's own
   later colliders cannot rescue a partnerless shape: the butt collider clones a
   surviving kinematic block's tags, the chest collider is off by default. The prune
   the gate replays is the conversion's OWN (`_hdt_shape_prune`, which
   `_harden_hdt_xml_for_fsmp` walks too, so the two cannot disagree): it is line-based
   -- a line's first shape tag decides it and the line goes whole -- so a partner block,
   a bone or a constraint that shares a line with a dropped block goes with it, and a
   pruned text that no longer parses is refused. When the prune takes EVERY simulated
   shape while the XML still makes a drawn shape's skin bone simulate, the chain would
   swing that cloth with no collision shape at all: refused too (an XML the author wrote
   with no simulated shape is not this rule's to judge). Live, these two refinements
   move nothing: the plan is the same on all 4,562 keys. The case: the
   Imperial light and medium cuirasses name their body `body`, their XML's only body
   collider is `Body`; the prune removed it and the skirt's `Proxy`, which collides
   only with the tag `Body` carried, collided with nothing -- worse than the static
   source. A physics loss or a one-weight change still keeps today's source. Live,
   read-only: 17 pieces / 34 index keys move (139 moved, 162 kept), nothing else in
   the plan changes. One plugin record changes with them, through the unchanged
   post-merge alt-texture reconcile (full run, the only alt-texture difference
   across the Combined's ARMAs): a Forsworn-armour retexture's per-source ARMA
   carries an MO3S of 4 entries (`armor`, `bottom`, `Feather Cape`,
   `ForswornUnderwear`); the static prebuilt mesh (`BaseUndies`, `BaseArmor`) has
   none of those names, so the set was emptied, while the zeroed SMP build has all
   four and keeps them at 1, 6, 0, 7 (same order at `_0` and `_1`) -- UBE wearers
   now get the retexture's textures, as CBBE wearers do. The replay ESPs and
   sidecars the commit measured are byte-identical; the Combined is where it
   shows. With `CBBE2UBE_NO_ZEROED_SMP_GAIN=1` the index and the verdict
   line are the old ones on all 4,562 keys, and with
   `CBBE2UBE_NO_SMP_GAIN_COLLISION_PARTNER=1` they are the 19-piece set before the
   partner rule. Refused: the Imperial heavy cuirass (its XML registers the body as
   `Body`), the Imperial light and medium cuirasses (no partner left), a build whose
   body the detector misses, a source with no body (copy path). Converted into scratch,
   every admitted file carries a constrained XML (54-150 constraints), no unresolved
   pointer and no re-imported body, and every simulated shape keeps a partner. The
   prune still removes XML shapes on 6 of the 34 files and reports each as
   `hdt_xml_shape_dropped`: the iron light cuirass (both weights) loses cloth `Pauldron
   Belt`, collider `PBelt Col` and 6 weight-threshold bones -- none of them in its build,
   the XML is shared with the heavy cuirass; the two farm robes (both weights) lose the
   per-vertex `3BA`, the body the swap replaces, and keep `Colision` and
   `VirtualGround` for their skirt. Every other bone the XML names and the output lacks
   is missing from the build too (the author's XML serves several variants). At bind
   the bust clips no more than before (three pieces 0.1-6.3% -> 0%; median standoff
   1.28u -> 1.33u, p90 1.52u -> 1.80u over the 17). The butt band clips at bind on 13
   of the 17 (0.07-13%); it clips MORE than before on 11 of them (0.75-13%) and less
   on two (bandit body 3, Falmer). All of it is behind SMP-simulated skirt verts, which
   the static harness cannot place -- the converter lifts their chains off the body
   and SMP moves them; in game is the verdict.

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

### Reading an archive entry (2026-09-25)

- **An embedded name is inside the entry's size** (`#bsa-embed-name-end`). An
  archive with flag 0x100 starts every file block with its path (a length byte
  and the name), and the file record's size covers the whole block. The
  compressed branch of `BSAArchive.read_file` ended there; the uncompressed one
  read `size` bytes from after the name, returning 1 + len(name) bytes of the
  next file. Checked on the vanilla Textures0 (0x107): each LZ4 frame fits
  [after the name, offset + size) exactly and no shorter slice decodes. Live
  census: 444 archives, 26 with embedded names, 0 uncompressed entries in them,
  so nothing the converter reads changes and there is no switch.
- **Two archives, one mesh: the game's copy** (`#bsa-load-order-winner`,
  `CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER`). `_BsaMeshIndex` took the first archive
  in MO2 priority. The game loads `<plugin>.bsa` and `<plugin> - Textures.bsa`
  with their plugin, and an archive loaded later overrides; MO2's priority
  decides only between loose files and between two archive files of the same
  name. So the archive whose plugin loads LATER now wins (`plugin_order=` from
  `_bsa_plugin_order`, the active plugins in load order). An archive no plugin
  loads -- the INI-listed base-game archives, which load before any plugin's,
  or a stray the game never opens -- ranks below every plugin-loaded one and
  keeps the MO2 order among its kind. The batch index, the source-selection
  index (whose listing the batch adopts only under the same plugin order) and
  the coverage step's existence lookup take it; the per-plugin owner lookup and
  the setup check ask only whether a mesh exists. Live: 406 archives (390
  plugin-loaded), 4,221 mesh paths in more than one, 436 change winner (405 of
  them a particle patch against a weather plugin), none extracted by the last
  run, converted into `!UBE` or armour; replays byte-identical.

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

**The end-of-run pair walk is one walk** (`#tail-fold`, 2026-09-25). After the
last source, the batch parent repairs one-weight jiggle bones
(`#weight-partner-jiggle-sync`) and then checks every pair for a scale-bone
divergence (`#slot0-weight-partner`, detect-only). These were two serial walks
over every pair of the whole output, each loading both files: 150 s and 72 s on
the reported modlist. `_postflight_weight_partner_fold` does both per pair on
one load. The check reads the state the sync leaves, so it takes the sync's
open files only when the sync left them as they are on disk (it changed no
vert and recorded no failure); a pair the sync wrote, or saved on one side and
failed on the other, is read from disk again. That reread is load-bearing: the
open copy still reads the bones and weights from before the graft, so it would
report the divergence the sync had just repaired. Grouping, order, skips,
failure records and the findings are the serial passes'. If the check raises on a pair, the fold
stops checking and keeps syncing, and the run gives the same "parity scan
skipped" warning: the serial check died there too, after the sync had
finished. If the pairs cannot even be listed, the two serial passes run (nothing
has been touched yet); a fold that raises anyway is reported as a sync that
raised, and the check then walks the pairs on its own.
`CBBE2UBE_NO_TAIL_FOLD=1` runs the serial passes.

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

### A "Data\" physics pointer resolves (`#physics-data-prefix`, default ON)

The NIF's `HDT Skinned Mesh Physics Object` string names the authored XML
relative to Data (`meshes\...\x.xml`). Some authors write it relative to the
game folder, `Data\meshes\...`. No mod folder holds a `data` folder, so
`_resolve_data_rel_in_vfs` missed it, the piece failed CLOSED
(`hdt_xml_unresolved`, every shape protected) and shipped with no pointer.

The resolver tries the raw rel first (a mod packaged as `<mod>/data/meshes/...`
keeps its file), then the rel with ONE leading `data` segment removed (any
case). The strip lives in `_resolve_data_rel_in_vfs`, not in the security
helper `_safe_data_rel`, and the stripped rel passes `_safe_data_rel` again:
removing the head can expose a drive letter (`Data\C:\...`), which pathlib
would otherwise join as an absolute path. No archive is read, so a pointer into
another mod's tree that exists only in a BSA stays unresolved.

Live, measured on the run's own counter: `hdt_xml_unresolved` 30 -> 12, and 18
output NIFs (loose and archive-staged, two armour sets and two cloaks) gain an
authored XML. Restoring the XML also re-engages the ordinary physics handling
on them: registered-shape protection, collider split clones, and re-imported
hidden collision shapes (a cloak's `VirtualBody`, which is CBBE-shaped).
`CBBE2UBE_NO_PHYSICS_DATA_PREFIX=1` restores the old miss.

### A destination query never takes the filename fallback (`#dst-xml-no-stem-scan`, default ON)

`_read_source_hdt_xml_text` falls back, when a NIF has no pointer, to a same-stem
or keyword-matched XML anywhere in the mod tree the NIF lives in
(`_find_hdt_xml_for_armor`). Against a SOURCE mod that is useful and static.
Against the OUTPUT mod it is a race (`#hdt-xml-race`): the run is still writing
XMLs there, the folder is never cleaned between runs, and `_mod_xml_index`
memoises whatever each worker saw first. The bust-split callers stopped taking it
on 2026-09-09; every other destination query still did, so a pointer-less piece
could take another garment's XML as its collider / soft-body sets and the
body-follow passes skipped whichever of its shapes that XML named -- or not,
depending on write order.

Every destination-side query now passes `stem_scan=_dst_xml_stem_scan()`: the
collider / soft-body sets of the five body-follow weight passes, the drape-skip
XML gate and the layered-cloth jiggle gate (`_piece_has_hdt_xml`), the
re-author's authored-skin set and its chain-anchor seed and chain precreate
(threaded through `_copy_shape` -> `_install_skin` as `xml_stem_scan`), the
bust-split clone's chain read, `_sync_bust_plate_follow_postwrite`,
`_conform_collider_to_body` and `_selfint_overrides`. The destination's own
pointer is still read first; an unresolved one still recovers from the
source-bound copy (`#xml-source-of-truth`) or fails closed. The declared-bone
guard (`_audit_registered_shape_declared_bones`) keeps its recorded decision and
still scans; it only reports.

Live (read-only census of the 3,342 output NIFs): 58 pointer-less NIFs answered
differently, 2 of them in their collider / soft-body sets (one skirt pair, whose
`panties` another skirt's XML registered) and 56 only in the drape / layered-cloth
XML question. 22 of those garments have a loose source (the other 7 are
archive-only) and were converted into an empty output tree and into one seeded
with the live XMLs: the parent differed between the two on the skirt pair only,
and the lane gave identical bytes in both on 22 of 22. The lane also equals the
parent's empty-tree output on 22 of 22, measured in two steps: 9 when the change
was made (the parent's empty-tree arm ran on those 9 only, although the commit
message says all), and the other 13 in a later same-day session (parent empty
and seeded trees plus the lane's empty tree, all byte-identical, and equal to
the lane's earlier runs of both trees).
`CBBE2UBE_NO_DST_XML_NO_STEM_SCAN=1` restores the fallback.

### The finalize owns the physics pointer (`#finalize-repoint`, default ON)

`_finalize_hdt_physics` copies the authored XML to `<stem>.xml` beside the NIF,
and every later XML edit lands in that copy: the sanitiser, the opt-in chest body
collider, the FSMP hardening, static chains, the bust split, the butt and skirt
collider proxies. The finalize only ever ADDED the `HDT Skinned Mesh Physics
Object` string, though, so a pointer an earlier step had set survived it: the
phase-1 or phase-2 keyword / stem match (`_find_hdt_xml_for_armor`, which names
the SOURCE mod's file) or the author's own string kept by a verbatim copy. FSMP
then loaded the untouched author file and ignored every edit.

Now, when THIS call wrote the sibling, a pointer naming any other file is
repointed at it. A pointer already naming the sibling (case and slash direction
do not count) is left byte-identical, and a sibling the call did not write (no
authored source XML, a failed copy, a leftover from an earlier run) never
captures the pointer. pynifly cannot set an existing string extra-data block
(nifly's `setBlock` is unimplemented for it), and FSMP reads only the FIRST
pointer, so adding a second one is no answer either: the header string-table
entry the block refers to is rewritten in place (`_repoint_physics_pointer`).
It is refused unless exactly one entry spells the old pointer, and the table's
max-length field is updated.

nifly writes each string once however many blocks use it, so that entry can also
be another block's string: a shape's extra-data, a node's name, a second root
extra-data. Rewriting it would change those blocks too. The first guard re-read
only shape names and root extra-data after the write, so a shape-level string
sharing the entry was rewritten and reported as a success. pynifly offers no way
to list every string field of every block type, so the guard asks nifly instead:
`_pointer_only_variant` builds the same NIF with ONLY the pointer block moved to
a new, appended string (in 20.2.0.7 a `NiStringExtraData` is two u32 string
indices, and the header's block-size table locates it), and `_nifly_resave`
loads and saves both through nifly, which rebuilds the string table from what its
blocks refer to. The in-place rewrite is written only when the two re-save to the
same bytes, i.e. no other block nifly knows refers to the entry. Otherwise, or
when the pointer block is not found exactly once, the NIF is left untouched and
the refusal is recorded as a pass failure. The re-save temps sit beside the NIF
under the orphan-temp sweep's name pattern and are removed. Live (read-only, the
306 pointer NIFs of the 3,342): the check passes on all 306, and an independent
pynifly walk of every node / shape name and extra-data string finds no other use
of any pointer string, so default output is unchanged.

The same switch gates phase 2's physics pointer on hand/foot pieces. Phase 1 never
gives a slot-33/37 piece a pointer, found or generated, and the shared tail skips
the finalize for them; phase 2 still attached one, which then shipped the source
mod's XML with no finalize at all. Now phase 2 applies the same gate to the found
XML and to the generator.

Live (read-only census of the 3,342 output NIFs): 306 carry a pointer, 304 name
their own sibling, and 2 (both weights of one skirt) name the source mod's XML
while the finalized sibling sits unreferenced beside them. The validator already
said so in the report ("HDT XML referenced but not found on disk"). No hand/foot
NIF carries a pointer today. `CBBE2UBE_NO_FINALIZE_REPOINT=1` restores both old
behaviours.

### The opt-in chest body collider needs a constraint (`#body-collider-constraint-gate`)

`_ensure_cloth_body_collider` (opt-in, `CBBE2UBE_BODY_COLLIDER=1`) registers
BaseShape as a per-triangle collider for a per-vertex cloth that names a body tag
no chest-height collider carries. It never looked for a constraint, so on an XML
with none it built exactly the unconstrained-collision-pair equip CTD
(`_is_unconstrained_collision_pair`; `validate_armor_hdt_xml` says the same of
adding body collision). It now declines an XML that declares no
`generic-constraint` / `stiffspring-constraint` / `conetwist-constraint` (the
`#constraint-group-scan` set), and says so: on stderr, and as a
`#body-collider-constraint-gate` DECLINED line in the piece's conversion record
(`_note_pass_effect`), the way the bust-split decline is recorded -- a user who
opted in gets no chest collider and the report says why. Default output is
unchanged: the collider is off by default.

### The validator reads past a junk tail and a default xmlns

`validate_armor_hdt_xml` parsed strictly, so an authored XML with text after
`</system>` ("failed to parse") or a default `xmlns` on `<system>` ("root tag !=
'system'") skipped every bone and collision check, though FSMP reads both files
fine. It now strips the tail with `sanitise_hdt_xml_bytes` before parsing (and
names it in one warning), and drops the `{uri}` prefix from every tag. Damage
INSIDE the root still fails to parse. Report text only.

Live, over the 306 pointer NIFs: 34 NIFs gain their checks (20 junk-tail, 14
namespaced), and 272 read exactly as before. The new warnings are 546 "references
bone ... in NEITHER the NIF nor the actor skeleton" (the 20 junk-tail NIFs; their
shared author XMLs drive chain bones those pieces lack), 24 "body shape has no
can-collide-with-tag", 20 tail notes, 8 "no per-triangle-shape" and 4 "cloth shape
declares no body-ish can-collide-with-tag".

`#hdt-xml-sanitise` itself stays opt-in (a user call). Its stated reason was
wrong and is corrected in the code, the GUI and the tests: every converter
consumer of these files is regex-based and FSMP stops at `</system>`, so the
junk blinds nothing; the flag is hygiene and diagnostics. A successful repair is
now recorded as a pass EFFECT at both sites, not as "PASS FAILED
hdt_xml_sanitised".

### A constraint counts wherever it sits (`#constraint-group-scan`, default ON)

FSMP has three constraint elements (`generic-constraint`,
`stiffspring-constraint`, `conetwist-constraint`), and an XML may put any of
them at the top level or inside a `<constraint-group>`; this converter's own
generator writes every chain constraint inside one. `validate_armor_hdt_xml`
asked `root.find("generic-constraint")` (direct children, one kind), so a
grouped chain read as unconstrained, and its constraint-body resolution check
read `generic-constraint` bodies only. Now both walk the whole tree for all
three kinds. An empty `<constraint-group>` is not a constraint (it holds no
spring), and neither is `<generic-constraint-default>`.

What the old read decided: only the wording of the report warning for a cloth
that names no body collide tag ("IS constrained, body collision can be added"
vs "NO constraints, needs a rigged chain first"). No converter decision uses
it: every physics decision reads constraint bodies with a whole-file
`body[AB]=` regex, and `_is_unconstrained_collision_pair` is fed the
generator's own chains. So it explains none of the pieces that ship static
although their source has SMP. Live: 75 of 137 output XMLs are constrained
only inside a group; all 26 "NO constraints" report lines (10 XMLs with 13
cloth-shape warnings, each at both weights) named one, and go to 0. Over the 361 source XMLs of the load
order, 1290 such warnings drop to 2 (the two cloths that are really
unconstrained). No XML in the load order uses the other two kinds, so the
bone check reports nothing new today.

`scripts/disable_unconstrained_smp.py` used a text test (`"<generic-constraint"`
anywhere). It saw nested constraints, but it took a `-default` block for a
constraint and missed the other two kinds, which would rename away the physics
of a stiffspring or conetwist chain. It now matches the three element names
exactly; on all 498 XMLs above its verdict is unchanged.
`CBBE2UBE_NO_CONSTRAINT_GROUP_SCAN=1` restores both old reads.

### How the physics census counts (`scripts/analysis/physics_cloth_health.py`)

The census MODELS how FSMP loads a piece's physics, because every other
reading counts a different population. It is a model built from FSMP's reader
and collision code, not a run of the engine: each rule below is one it
replays, and the list at the end is what it does not.

- **The NIF's own pointer, and nothing else.** FSMP's `scanBBP` takes the first
  string extra-data named `HDT Skinned Mesh Physics Object` on the ROOT node
  (engine strings: no case). A NIF without one has no physics in game. The
  census resolves the pointer with `_resolve_data_rel_in_vfs`, so it follows
  `#physics-data-prefix`. It never uses `_read_source_hdt_xml_text`: that
  helper falls back to a same-stem XML by filename, which is right for
  choosing a source config and wrong for counting what ships.
- **The XML's bytes.** A UTF-8 byte-order mark parses; read as locale text it
  is junk before the root. Junk after `</system>` is ignored (FSMP stops at the
  root's end tag), and a default `xmlns` on `<system>` renames nothing for
  FSMP, so namespaces are stripped. A root other than `<system>` loads nothing.
  An XML the run cannot read (locked, denied) is its own bucket, `xml
  unreadable`, and the report says its counts are short; it is not a fact
  about the piece, so it is not in the "declared but no system loads" total.
- **What simulates: bone mass, not element kind.** A `per-vertex-shape` and a
  `per-triangle-shape` differ only in collision geometry. A shape simulates
  when any of its skin bones is dynamic (mass > 0) and is a kinematic collider
  when all are mass 0. Masses are replayed in document order, as the reader
  does: `<bone-default name extends>` copies a template and overrides its
  `<mass>` (with no name it replaces the unnamed default for what follows);
  `<bone template>` takes that template plus its own `<mass>`, first
  declaration wins; a skin bone or constraint body not declared before its
  first use is created from the unnamed default in force at that point. Bone,
  shape and template names are engine strings and compare without case (an
  XML's `NPC Pelvis [PElv]` is the skin's `[Pelv]`). A shape that is not a
  skinned NIF shape with vertices builds nothing.
- **Cloth moved by bones alone.** FSMP keeps a system that has any bone
  (`SkinnedMeshSystem::valid()` is `!m_bones.empty()`), not only one with a
  shape, and `writeTransform` drives every bone it created with mass > 0. So
  an XML of bones and constraints with no simulated shape (none declared, the
  declared ones absent from the NIF, or every one kinematic) still swings the
  NIF mesh skinned to those bones, and that mesh has no collision geometry: it
  reaches no partner, the strongest no-reach case. Such a piece is measured,
  as cloth "simulated by bones, no dynamic collision shape"; its moved shapes
  are the NIF shapes skinned directly to a bone the replay creates with mass >
  0, the injected body excluded (a shape on an undeclared child node of such a
  bone is not seen; `physics_rest_depth.py` follows the node tree). No
  constraint is required, because FSMP moves an unconstrained dynamic bone too;
  the row's constrained flag splits the two, as for any cloth. A piece whose
  dynamic bones carry no NIF shape is counted apart (`bones simulate, no NIF
  shape skinned to one`), and so is one where no bone simulates: both are
  "declared but nothing visible moves", beside "declared but no system loads"
  (pointer unresolved, XML unparseable, root not `<system>`).
- **FSMP's collision rule.** `needsCollision` never pairs two kinematic
  shapes. Otherwise `canCollideWith` runs both ways and both must allow it. One
  side allows the other when the other carries a tag in its
  `can-collide-with-tag` list, or, when that list is EMPTY, when the other
  carries none of its `no-collide-with-tag` tags. Every kind pairs with every
  kind (vertex-vertex, vertex-triangle, triangle-triangle). `<shared>` is
  checked in `SkyrimBody::canCollideWith` before the tags: `external` refuses
  any partner on the same skeleton, and every shape of one file is on one
  skeleton, so an `external` shape pairs with NOTHING in its own file;
  `public`, `internal` (same skeleton) and `private` (same file) all allow an
  in-file pair. The census models exactly that. None of the 154 XMLs the
  09-24 pack's pointers resolve to uses `external` (private 297, public 16,
  internal 12), so no count moves. `<shared>` and the tags are read
  untrimmed: FSMP reads both with `readText`, which returns the element's
  value without trimming (only its number and bool readers trim), and compares
  `<shared>` exactly, so ` external ` is an unknown value and falls back to
  public. `GetValue` itself is outside the FSMP source this was checked
  against; none of the 1880 such elements in those XMLs is padded, so no
  count moves either way.
- **What is the body.** A body collider is a KINEMATIC shape (a moving one is
  never the body) that either carries a tag in `BODY_TAGS`, or is a body
  STAND-IN: its name or a tag contains a body-part token (`BODY_TOKENS`:
  body, vbd, leg, feet, foot, butt, thigh, calf, arms, hand, pant, torso,
  breast, belly) AND more than half its skin WEIGHT is on the actor's body
  bones (an `NPC ` bone other than the root and COM). Both are needed: a
  ground plane tagged `legs` rides the root only, and a greaves or belt
  collider on body bones names no body part. By weight, not bone count: a
  leg collider can list six skirt bones that carry 6% of its weight. The
  tokens come from the kinematic partners live cloth reaches: `vbody`, `vbd`,
  VirtualBody/Legs/Feet/Butt/Arms/Hands, CollisionLegs, ButtCol, LegsCol,
  PantsC, ColPants. The only body-named partners the skin test turns away
  there are ground planes on the root.
- **Every constraint kind.** `generic-constraint`, `stiffspring-constraint` and
  `conetwist-constraint`, at the top level or inside a `constraint-group`, all
  constrain, except one between two kinematic bones, which FSMP skips. The
  unconstrained crash pair is unconstrained cloth that actually reaches a
  partner.

Measured on the 09-24 pack (3342 NIFs), old reading -> this one: 364 pieces
"with physics" -> 306 with a pointer (58 NIFs of 29 garments had borrowed a
same-stem XML); 94 "unparseable" -> 0 (22 byte-order mark, 58 junk after the
root, 14 namespaced root); pieces with simulated cloth 97 -> 247 (124
garments); unconstrained crash pair 56 -> 0; named collider absent 6 -> 0.

The same pack, the second 09-25 version -> this one (NIFs / garments):
measured 247 / 124 -> 285 / 143, the 38 / 19 added being cloth simulated by
bones with no dynamic collision shape (24 / 12 whose XML declares no shape, 14
/ 7 whose every shape is kinematic), every one constrained. "Declares no
shape" 41 / 21 and "every shape kinematic" 18 / 9 leave the exclusions: 38
measured, and 21 / 11 whose dynamic bones carry no NIF shape are counted apart;
none is left where no bone simulates. Declared but no system loads: 0. Cloth
reaching no partner in its file: 4 / 2 -> 42 / 21 (the 38 plus the same 4).
Cloth reaching no kinematic body collider: 190 / 95 -> 134 / 67. On the
pieces with a collision shape it is 190 / 95 -> 96 / 48: 102 / 51 pieces have
cloth whose only body collider is a stand-in. Of the 134, 38 / 19 are the
bone-moved cloth, 70 / 35 have cloth that reaches only
kinematic shapes that are not the body (60 / 30 a collider on body bones named
for no body part -- `Collision`, `Col`, `Greaves` -- and 10 / 5 only ground
planes), and 26 / 13 have a simulated shape that reaches no kinematic shape at
all. Every one is constrained; the unconstrained crash pair stays 0. Stored
cloth vertices inside the body, counting only the vertices FSMP moves (weight
> 0 on a dynamic bone): 47 of 96 measurable pieces (26 of 48 garments; 189
with no injected body) -- 45 of the 86 with a collision shape and 2 of the 10
bone-moved. A shape is cloth when ANY of its skin bones is dynamic, so the
first reading here (66 of 86 -> 76 of 96) also counted its vertices on
kinematic bones alone, rigid mesh the solver never moves: 8 of the 10
bone-moved pieces got into the row only that way (68 of 96 with the rule on
those alone). `physics_rest_depth.py` reads this population, so it now
measures the 38 as well (243 -> 281 measured, 275 ranked once its frame gate
lists 10 apart, below); two of those garments (four NIFs) rest more than 0.5u
inside, both past 1.5u on a few vertices, and no earlier row moved.

Numbers from before 2026-09-25 are not comparable with these, and neither are
the first 09-25 version's, which took every per-vertex shape for cloth and
every per-triangle shape for a collider (117 / 59 simulated, 14 / 7 reaching
nothing: 12 of the 14 were only kinematic body helpers, and the other 2 had
cloth whose partner is a per-vertex shape), nor the second's no-partner and
no-body rows, which left out cloth moved by bones and knew the body only by
tag.

Not modelled: colliders another worn piece brings; shape-name physics from
`defaultBBPs.xml` and its shape-name remapping; XMLs that exist only in an
archive; bone renames; per-bone filters (`can-/no-collide-with-bone`,
`weight-threshold`); `disable-tag`; whether a declared bone's node exists; and
a mesh hanging on an undeclared child node of a dynamic bone. The body test is
by name and skin, not by where a shape lies, so an armour collider named for a
body part would count as the body. Its depth row reads the STORED positions
of the vertices FSMP moves against the injected body, so it cannot see
`#chain-rest-lift`; the rest-pose depth is the next section's tool. A piece
whose body is there but whose cloth has no vertex weighted to a dynamic bone
is counted on that row, neither measurable nor unknown (0 on the 09-24 pack).

### Rest-pose depth of simulated cloth (`scripts/analysis/physics_rest_depth.py`)

Cloth that STARTS inside the body is not reliably pushed out by FSMP, so where
simulated cloth rests matters. The census's depth row and the first "cloth at
rest inside the body" numbers read stored vertices. The engine draws
`sum_b w_b * G_b * S_b * v`, and `#chain-rest-lift` moves chain ROOT NODES
(`G_b`) while leaving the skin (`S_b`) and the vertices alone, so a
stored-vertex depth cannot see the lift at all. This tool measures the drawn
position.

- **The frame.** FSMP merges the armour's node tree into the actor skeleton by
  name. A bone the skeleton has is the skeleton's node, never the armour's copy
  (the converter writes those flat and the game ignores them); any other node
  hangs from its nearest ancestor the skeleton has, by the armour's local
  transforms, and a node on the armour root hangs from the skeleton root (the
  armour root's own transform is not used). The skeleton is the load-order
  winner of the converter's skeleton paths. Control: the body skinned through
  the same model equals its stored vertices (measured 0.0000u).
- **What is cloth.** The census's bone-mass replay: a bone FSMP creates with
  mass > 0, or any armour node hanging below one. A vertex is cloth above 5%
  weight on those bones, on every skinned shape (named in the XML or not),
  except the injected body. Visible cloth is ranked; collision proxies and
  helpers (the survey's composed proxy rule) are counted apart.
- **The body.** The user's BodySlide build per weight, weight 0 taken as the
  weight-1 file's sibling: the body the lift clears. On the measured list that
  build is the zeroed UBE body (BodySlide's saved preset for it is the zeroed
  one), the same file the zeroed-body lookup returns. Runtime morphs are not
  applied.
- **Depth.** To the closest point on the body's triangles, signed by the
  interpolated outward normal there (positive = inside); normals from the
  triangles, turned outward by signed volume. Controls, any failure exits 3:
  joints deep in the pelvis and limbs read inside and far points outside (these
  do not use the normals), and the body's own vertices moved 0.5u in and out
  read +0.5 / -0.5 (median error 0.0009u). 12.4% of the body's vertices sit in
  features thinner than the push, almost all on the genital midline slit, and
  are left out of that check; cloth there reads unreliably.
- **The lift, read from the file.** Each ARMOUR bone with skin, moving or
  kinematic (the converter lifts a chain root whether or not the XML simulates
  the chain), and each moving skeleton bone: its rest position minus its bind
  position, bones that moved by one vector grouped. A LIFT is exactly what the
  pass does: one rigid translation of a node the converter lifts (a garment
  node hanging off a skeleton-named one, `_chain_root_subtrees`' rule), every
  skinned bone below it moved by the same vector, of 0.05u up to the 2.0u
  cap. An offset that grows along a chain, flips sign, or passes the cap is
  the node tree disagreeing with the skin; it is counted apart and never
  called a lift. The run's `standoff_audit.jsonl` is a cross-check only: it
  appends across runs.
- **Listed apart, not ranked** (both depths shown). A file the converter's own
  frame check refuses: the tool runs `_chain_frame_ok` itself, with its 0.5u
  tolerance, on the chain bones' node-tree positions (every skin bone but a
  hard skeleton bone the actor's skeleton has -- a garment or soft-body bone
  the skeleton carries is checked at the skeleton's node, where the game puts
  it; the lift read back under a bone taken off again) against the skin. The
  written file's skeleton nodes are flat, so its own global-to-skin is not the
  frame the converter checked in; the skin is read in the bind frame the depth
  uses. Like the converter's check it reads every skinned shape of the file,
  a body-named one too: a source's own body helper keeps its name in the
  written file, and its skin can refuse the file (it is never measured or
  read back for a lift). The converter lifts nothing on a file it refuses,
  so such a file carries no lift. And cloth resting more than the lift cap + 0.5u (2.5u) from
  where it was skinned: no pass does that (hard skeleton-named cloth bones,
  which the check does not cover), so the model is in question there.
- **Bone-moved cloth** (the census's rows with no simulated collision shape) is
  measured and ranked like the rest, and marked.
- **Groups.** Bones grouped by the vector they moved are keyed by their root
  node, and `root#1`, `root#2`... when siblings under one node moved
  differently and several groups share it: no group is ever dropped.
- **Inputs and output.** Exit 2 with one line on an unreadable or unusable
  body or skeleton (no shapes or nodes, a body with no triangles, a body skin
  bone the skeleton lacks), and on a skeleton or body NAMED (`--skeleton`,
  `CBBE2UBE_SKELETON_NIF`, `--body`) that is no file: it is never replaced by
  another. `--json` is written on every exit: "status" ("ok", "controls
  FAILED", "nothing measured", or "input error" with the one-line "reason"),
  the controls when they ran, and depth rows only when every control passed.
  It is first written "incomplete", so a run that crashes leaves that at the
  path and never an earlier run's rows.

Measured on the 09-24 pack, read-only: 285 pieces with simulated cloth, 275
(138 garments) ranked and 10 (5 garments) listed apart, all 10 refused by the
converter's frame check (worst 5.00u, 4.15u, 2.40u and 0.77u: exactly the
pieces and worst readings the run log records refused), 4 of them also with
cloth resting 4.00u and 4.28u off its skin. 38 / 19 of the ranked are
bone-moved cloth.
Visible cloth deeper
than 0.5u at rest: 81 pieces / 41 garments; deeper than 1.5u: 37 / 20 (the
bone-moved: 4 / 2, both past 1.5u). Hidden helpers: 29 / 16 and 7 / 4.

What `#chain-rest-lift` did: 205 pieces (103 garments) carry a lift, 989
chains, median 0.94u, 130 at the 2.0u cap. On the earlier 243-piece population
that is 187 pieces and 959 chains (126 at the cap). The first reading there
said 193 pieces, 1077 chains, median 1.04u, 148 at the cap: it took any bone
off its bind for a lift, which added 136 node-tree disagreements on 6 of the
refused pieces (every reading past the cap among them), and it missed 18
lifts on 10 pieces whose lifted chain is kinematic. The tool itself counts
140 disagreement groups on those 6 pieces: keyed by root alone, 8 groups on
the 4 refused vest pieces (4 of them disagreements) were overwritten by a
sibling group under the same node, and it printed 136. Log cross-check: the
log names exactly the lifted chains on all 205 pieces (magnitudes within
0.0001u) and has no lift the files lack, and it records refused exactly the
10 pieces the tool refuses. Two of them (1 cloak garment) the converter
refused on the source's body-helper shape, whose genital bones sat 4.15u off
where the skeleton puts them; that shape keeps its body name in the written
file, and the tool's check left it out as the body -- ranking the two -- until
it also put body-named shapes into the frame check. That is not exactly the
converter's view: the converter checks each SOURCE file on its own, so the
UBE body it injects later never joins a garment's check, while the tool,
reading the written file, now checks the injected body too. The refusal
verdicts still match the log on all 285 pieces, but the printed 'over N
bones' count can run higher than the log's (e.g. 137 against 120). They rest
clear (-0.40u) and carry no lift, so no depth or lift count moved.

Against the first reading (stored vertices), the depth at the bind position
reproduces it, so the change is the node tree: of the 7 garments first
reported deeper than 1.5u, 4 now read below 1.5u -- two clear it well (2.05u to
0.91u, 1.50u to 0.86u) and two only drop below the line and still rest 1.30u
and 1.39u inside -- and one of the 14 reported deeper than 0.5u rests clear
(1.38u to 0.01u).

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

### Shapes that share a name (`#dup-shape-names`, `#override-contract`)

**Why.** A NIF's shape names need not be unique, and a layered fur coat ships six
shells all named `fur` (2915 and 965 verts). Most per-shape passes key their data
by name -- the self-intersection repair and the re-author that commits it, the
stacked-layer motion plan, the coincident-skin, roughness and SMP-boundary passes,
the authored-order restore, the TRI generator -- so the last `fur` won. Converted
alone at 80f78c7 the coat came out CORRUPT: one shell's 2915 positions went to all
six, the four small shells kept 1687 triangles indexing that foreign mesh, and
their UVs past 965 were non-finite and differed run to run. That last part is
memory: `_copy_shape` never checked its documented "same length" contract, and
pynifly's `createShapeFromData` sizes the shape from the verts but copies UVs from
the source, so it read past the 965-entry buffer (in another run the same read
raised, and the passes were skipped for every shell instead). The TRI carried ONE
`fur` block, indexed up to 964, for shells the body-morph code matches BY NAME.

**How -- two layers.**
- `#override-contract` (`_copy_shape`): `override_verts` and `override_normals`
  must have one row per source vert and every `override_tris` index must be a vert
  of the shape (and fit uint16), else ValueError -- every caller already treats a
  raising copy as a failed shape. The triangle COUNT is free: the phase-2
  injection APPENDS ~366 pubic-fill triangles to BaseShape. `_reauthor_nif_fresh`
  also declines an override whose name matches several shapes and commits the
  rest; that is the only guard for two SAME-size shells (the length test cannot
  see them). `CBBE2UBE_NO_OVERRIDE_CONTRACT=1`.
- `#dup-shape-names` (`_uniquify_source_shape_names`): when a source is loaded,
  the k-th shape of a shared name becomes `name:k` (skipping a name already
  taken; the first keeps its name). Shape ORDER never changes, since an ARMA
  alternate texture binds by index (BUG-09). The rename is a pure function of the
  file, so every pass that re-opens the source (`_open_source_nif`) gets the same
  names; it touches only pynifly's cached name, never the block (NiflyDLL cannot
  SET a loaded BSTriShape), and re-points pynifly's by-name dict.
  `CBBE2UBE_NO_DUP_SHAPE_NAMES=1`.

**The new names ship.** A TRI block is applied to the shape of the same name, so
unique names are the only way each shell gets morphs indexed for its own verts.
Left as authored and reported (`dup-shape-names/kept`): a name the source's
physics XML uses, matched case-insensitively (FSMP binds shapes by name), and
every group when a declared XML cannot be read; and a name the converter reads as
a body (the inline-body names, the 3BA family, BaseShape / VirtualBody), whose
meaning is the name.

**Measured.** The coat converted in scratch: 7 shapes `fur, fur:1 .. fur:5,
coat` at the source's vert counts, finite UVs, 7 TRI blocks each indexed within
its own shape, no pass failure. Live: 0 of the 3,365 source files the plan
converts share a name (so no current output changes); 1 of 3,342 files in the
deployed output does, the coat, which the plan no longer converts.

**Our own ESPs' colour variants: `#alttex-dup-occurrence`.** The alternate-
texture reconcile (`ube_patcher._reindex_alt_texture_payload`) rebuilds each
MO?S set of our merged plugin from the converted NIF's {name: index} map and kept
one entry per case-insensitive name, so a set that addresses six same-named
shells by source index kept one entry, bound to the first shell. Now each entry
of a name the rename split goes to its own shell. How it knows which shell is
`#alttex-exact-provenance` (below); the two layout-guessing cuts that came first
are kept only behind its switch. A repeated source index is one shell and keeps
one entry. Authored name bytes are kept, as `#alttex-case` does: the engine
binds by index.

**Exact provenance: `#alttex-exact-provenance`.** The rename is a pure function
of the SOURCE file (`_dup_shape_rename_plan` over its shape names, plus the
physics-XML and body keep rules), so the reconcile re-derives it instead of
guessing from the converted NIF. For each converted NIF a set names that
carries a `name:k` beside `name` (case-insensitively, k a plain integer from 1:
`_split_name_candidates`), that a set names one shape name in more than once
(case-insensitively: `_repeated_entry_names`, `#alttex-set-provenance` below;
a set in ANY piece of the merged plugin, and two entries with no name,
`#alttex-batch-ambiguity` below), that carries one name under two spellings
(`Fur` and `fur`: `_case_variant_names`, `#alttex-case-provenance` below), or
that carries one name twice in one spelling -- two or more unnamed shapes
included (`_literal_duplicate_names`, `#alttex-batch-ambiguity`); any other
NIF never reads a source:
1. find its source, read-only, the way the convert step found it
   (`_alttex_source_paths`): our path is `!UBE\` + the source's meshes path;
   the full-VFS winner from the batch's own index (`auto_convert.
   _BATCH_MESH_INDEX`) or, outside a batch, `discovery.build_mesh_index` over
   the enabled mods with the output mod skipped -- a key the batch's index
   lacks is looked up that second way too, which finds a mod's own loose mesh
   where the convert step's source-local tier found it; else the load-order
   archives (`_BsaMeshIndex` over `_load_order_bsa_dirs`, lookup-only), read as
   the copy the convert step extracted to `<output>\_bsa_staging` and taken
   only while its bytes are the archive's. Not searched: a source folder that
   is not an enabled mod (the convert step's local tier can read one) -- that
   NIF falls back, below;
2. read it and replay the converter's own rename on it
   (`_read_alttex_source` calls `_uniquify_source_shape_names`: same keep
   rules, same XML reader) -- the exact map source 3D index -> shipped name;
3. check it is the mesh that was converted (`_alttex_binding`): every
   converted name that looks renamed, or is a renamed shell's, is one this
   rename gives, and each renamed shell present in the converted NIF is there
   once, with its source shell's vertex and triangle counts and its UVs
   within 2^-10 (`_same_shell_print`) -- so is a shape named once like a
   renamed shell in another case (`Fur` beside `fur`, `fur:1`;
   `#alttex-case-provenance`). MEASURED: all 446 non-body shapes of
   the reported modlist's converted pack that share a name with their source
   shape keep the counts and the exact UV bytes; the one six-shell source
   there, read from an archive and converted in scratch, kept the counts and
   moved its UVs by up to 2.4e-4 (half-float rounding), and bound;
4. bind (`_bind_by_source`): an entry whose name is a renamed shell's (old or
   new name, case-insensitive) goes source 3D index -> shipped name -> that
   name's index in the converted NIF (so does an author's shape named like
   one in another case, e.g. `Fur` beside the `fur` shells). An entry of a
   group the rename left as authored -- or of any other name the source or
   the reconcile shows is shared: a case-variant group, a name a set of the
   NIF repeats, a name the NIF carries twice -- goes source 3D index -> the
   converted shape of that name (exact spelling) with that source shell's
   print (`_kept_group_shells`, below). The EMPTY name is such a name: the
   rename never renames unnamed shapes, so two or more of them are a group
   left as authored (`#alttex-batch-ambiguity`). Dropped: an entry whose index is not
   a shape of the entry's own name, a shell the converted NIF lacks (a failed
   copy -- never shifted onto a neighbour), and a second entry for one shell.
   Every other entry -- a name that neither the source nor the reconcile
   shows is shared -- keeps the match by name, one per name.

No source, one that cannot be read, or one that fails step 3: every name that
may be split (both `name` and `name:k`), every name a set of that NIF repeats
and every name the converted NIF carries twice (the empty name included)
loses its entries for that
NIF, and the run prints how many NIFs fell back. Dropping, not the old
one-entry-per-name rule, because that rule keeps the set's FIRST-LISTED entry
on the one shape the name finds (the first shell of a renamed family, the LAST
of a literal duplicate), and the first-listed entry can address any shell: a
wrong colour is worse than
a base colour, and without the source nothing tells which entry is the first
shell's. Real case: the six-shell source's 12 colour sets list its shells out
of index order (1, 2, 0, ... or 2, 1, 0, ...), so one entry per name binds
shell 1's or shell 2's entry to shell 0. Those sets happen to use one texture
set for all six shells, so there it only costs five shells their colour; a set
with a texture set per shell would recolour shell 0 wrongly. The exact binding,
against the scratch conversion, keeps all six entries of all 12 sets on their
own shells. So a colour is missed when a shell was lost, when an entry's index
disagrees with its name, when the source is missing or changed, or when the
converted shapes of a kept or otherwise shared name cannot be told apart.
Re-derived after the empty name joined `#alttex-batch-ambiguity` (below),
with every switch on, by following each entry through the reconcile. The
empty name is a name like any other on every step: two unnamed shapes are a
name carried twice, two entries with no name are a repeat, and two unnamed
source shells are a group left as authored. An entry of a NIF that loads
either (a) binds by name, when the NIF's source is not read -- it shows no
`name:k`, no second spelling, no name twice, and no set in any piece repeats
a name -- or when it is read and the entry's name is not one the source or
the reconcile shows is shared (then the converted NIF carries it at most
once); (b) is dropped: a name the converted NIF lacks, every entry of a
shared name when the source is missing, unreadable or not the mesh
converted, and what step 4 drops; or (c)
binds by source index and print (step 4). So a colour can still land on
another shell of the same name (case-insensitively, the empty name
included) in exactly two cases:
- (c) two shells identical in vertex count, triangle count and UVs that only
  their names tell apart -- two renamed shells (`fur`, `fur:1`) or two
  spellings (`Fur`, `fur`) -- which the mod swapped after the conversion:
  the print cannot see the swap (two unnamed shells have no names to tell
  them apart, so their entries are dropped instead);
- (a) a name the source shares, the empty name included, whose other shells
  were ALL lost in conversion, so the converted NIF carries it once (no
  `name:k`, no second spelling, no literal duplicate, one unnamed shape),
  and no set of that NIF in any piece of the merged plugin names it twice,
  when the source was not read (nothing else about the NIF reads it) or does
  not match: the name's one entry goes to the surviving shape by name, which
  is the lost shell's colour when the entry was written for a lost shell.
With the source read and matched only the first can happen: every name the
source shares -- renamed, kept as authored, shared up to case, or the empty
name of two or more unnamed shells -- binds by index and print, or is
dropped.
Not in the list, because the entry is never rebound: a converted NIF that
exists but does not load keeps its set's source indices unchanged (the run
reports it).
Precondition: the reconcile runs once, on a freshly merged plugin -- every
ESL-split piece of it together -- whose sets still carry the source's
indices (stale overflow pieces are removed by the merge); it is not meant to
run again on its own output, whose indices are the converted NIF's.
`CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1` binds by the converted NIF's layout
(`#alttex-family-strict`, below), byte for byte as before.

**The set's own repeat, and groups kept as authored: `#alttex-set-provenance`.**
Exact provenance first read a source only when the converted NIF showed
`name:k` beside `name`. Two holes, each able to put a colour on the WRONG
shell. (1) A family that lost EVERY renamed shell (a two-shell `fur` that lost
`fur:1`) looks unrenamed, so no source was read and one entry per name put the
set's first-listed entry -- possibly the lost shell's colour -- on the
surviving `fur` (or on the author's `Fur` beside it). A set that names one
name more than once proves the source had same-named shells, so such a NIF's
source is now read too, and the lost shell's entry is dropped as in step 4;
with no source that matches, the repeated names' entries are dropped, and so
are those of any other set of that NIF for the same names. Every armature's
sets are scanned for repeats BEFORE any NIF is loaded: a NIF is loaded and
cached once, so loading it while only an earlier armature (naming the name
once) had been seen would read no source for a repeat in a later one. (2) A
group the rename left as authored (the physics XML names it, a declared XML
cannot be read, or a body name) ships its literal duplicate names, and one entry per name
put the first-listed entry on the LAST shape of the name
(`{name: index}` keeps the last). With the source read, `_kept_group_shells`
matches each converted shape of such a name to the source shape of that exact
name with its `_shape_print`; the name binds only when every converted shape
of it matches exactly one source shape and no two match the same one, so two
identical shells, or a converted shape no source shell explains (a body the
conversion replaced), drop that name's entries instead. A source shell nothing
matches was lost: its entries are dropped. With no source that matches, a
name the converted NIF carries twice loses its entries.
Measured on the reported modlist: the deployed merged plugins' sets are already
reconciled (one entry per name), name no name twice, and a reconcile of them
reads no source and changes nothing. The pre-reconcile per-source sets (1,352
naming 262 of our NIFs) repeat a name in 12 sets, all for the one six-shell
coat still in the output from before the rename (six literal `fur` shapes).
Its source now renames them, so it is not the mesh converted: a fresh merge
against that stale mesh drops the one `fur` entry each of those 12 sets kept
(the first-listed colour, bound to the last shell) -- a colour missed.
Converted again, the coat ships `fur` .. `fur:5` and binds all six entries of
each set (above). Its shells have two prints (two of 2,915 verts, four of 965,
with identical UVs), so as a kept group its `fur` entries would be dropped.
`CBBE2UBE_NO_ALTTEX_SET_PROVENANCE=1` (read only with exact provenance on)
reads a source only for a NIF that shows `name:k` and keeps one entry per name
for a kept group, byte for byte as `#alttex-exact-provenance` first shipped.

**Names shared only up to case: `#alttex-case-provenance`.** The reconcile's
own match by name is case-insensitive and keeps the FIRST spelling the
converted NIF carries (`ci_index`), but the rename works on exact names (an
author's `Fur` beside `fur` is never renamed), and `#alttex-set-provenance`
grouped the source's shells by exact name. So a set naming `fur` and `Fur`
read the source, found neither a renamed nor a kept group, and fell back to
one entry per name: the first-listed entry on the first spelling, possibly
the other shell's colour -- worse than with no source, which drops the name.
A set naming `fur` once read no source and did the same. Now, with the
source switched on:
- a converted NIF carrying one name under two spellings
  (`_case_variant_names`) has its source read, like a set's repeat;
- the reconcile passes the names it knows are shared (`ambiguous`: a set of
  the NIF repeats it, or the NIF carries it twice, case-insensitively) into
  the binding, and `_kept_group_shells` groups the source's shells
  case-insensitively and takes every such name the rename did not split, even
  one the source has once. Each entry of those names binds source 3D index ->
  the converted shape with that shell's exact shipped spelling and print, or
  is dropped: one whose index is not a shell of its name, a lost shell, a
  second entry for one shell, and every entry of a name whose converted shapes
  are not each matched to exactly one source shell (a second converted `fur`
  no source shell explains, two literal duplicates with one print). Two
  spellings with one print still bind: each spelling is one shell's shipped
  name, as with renamed shells;
- a shape named once like a renamed shell in another case (`Fur` beside
  `fur`, `fur:1`) must carry its source shell's print, as the renamed shells
  must; otherwise the source is not the mesh converted (step 3).
So every name the reconcile knows was shared binds exactly or is dropped,
whether or not a source was found. The cases left are those listed under
`#alttex-exact-provenance`, once `#alttex-batch-ambiguity` (below) closed three
more. Every armature's sets are still scanned for
repeats before any NIF is loaded (a repeat in a later armature reads the
source; test and Pair ASP-m).
Measured on the reported modlist (all 3,345 loadable converted NIFs of the
deployed output): one mesh, in both weight halves, carries a name under two
spellings -- a small collision shape and the feet -- and no colour set names
it; the one literal duplicate is the stale coat above. Against the parent,
the reconcile of the deployed plugins (2 ESPs), the fresh merge of the 190
pre-reconcile patches and the coverage replay (3 ESPs, 3 sidecars, stats;
9,926 links) are byte-identical: no live set names a case variant, and the
stale coat's sets were already dropped (its source does not match).
`CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE=1` (read only with set provenance on)
groups by exact name, reads no source for two spellings alone and binds such a
`Fur` by name, byte for byte as `#alttex-set-provenance` first shipped.

**One view of the whole merged plugin: `#alttex-batch-ambiguity`.** Three
holes left after `#alttex-case-provenance`, each able to put a colour on the
WRONG shell:
- `reconcile_alt_texture_indices_all` reconciled each ESL-split piece
  (`<stem>.esp`, `<stem>2.esp`, ...) on its own, rebuilding the repeats, the
  converted NIFs, the shared names and the bindings per piece. The merge
  splits by patch, so two armatures on one NIF can land in different pieces:
  a set in one piece repeating `fur` proves the NIF's `fur` was shared, yet a
  set in the other naming `fur` once read no source and bound by name -- a
  lost shell's colour on the surviving `fur`, with the source found or not.
  Now `_reconcile_alt_texture_pieces` takes every piece at once: every
  piece's sets are scanned for repeats before any NIF is loaded, and one
  view (NIF cache, prints, shared names, bindings) serves every piece, so
  each source is looked up and read once. Each piece that changed is saved;
  a piece that does not load raises after the others are reconciled and
  saved (the caller reports the failure, as before).
- A converted NIF carrying one name twice in one spelling (a group the rename
  kept as authored, or a mesh converted before the rename) read its source
  only when a set repeated a name, another name looked renamed, or a name had
  two spellings; otherwise a set naming the name once put its entry on the
  LAST shape of the name. Now such a NIF (`_literal_duplicate_names`) has its
  source read, and the name binds by print or is dropped, as a repeat's does.
- The EMPTY name was never taken as shared: the rename never renames
  unnamed shapes, `_literal_duplicate_names` and `_repeated_entry_names`
  skipped it, and `_kept_group_shells` built no group for it. So an entry
  with no name on a NIF carrying two or more unnamed shapes went to the LAST
  of them by name -- even with the source read (for another name) and
  matched: source and converted NIF `coat, fur, fur, '' (A), '' (B)` with
  `fur` kept by physics, the entry written for A (index 3) landed on B
  (index 4). Now the empty name is a name like any other: two unnamed shapes
  in the converted NIF, or two entries with no name in a set of it
  (`_repeated_entry_names(unnamed=True)`), read the source; two or more
  unnamed source shells are a group left as authored, so each entry with no
  name binds source 3D index -> the unnamed converted shape with that
  shell's print when every unnamed converted shape matches exactly one
  unnamed source shell, and is dropped otherwise (two unnamed shells with
  one print, a lost shell, an index that is not an unnamed shell's); with no
  source that matches, those entries are dropped. One unnamed shape, with no
  set naming no-name twice, still binds by name, as before.
`reconcile_alt_texture_indices` on one plugin is the same function over one
piece, so a single-piece merge reconciles as before. What is left is the
list under `#alttex-exact-provenance`.
Measured on the reported modlist, against the parent (8317cd7), on git
archives: the path a fresh run takes -- `merge_patches_split` then this
reconcile -- over the coverage patches the replay writes (3 in, 2 pieces),
over the deployed coverage patches (2 in, 2 pieces) and over the per-source
fallback merge (188 in, 3 pieces) is byte-identical (7/7 files); the
per-patch reconcile of the 190 pre-reconcile patches and the coverage replay
(3 ESPs, 3 sidecars, stats) are byte-identical too. On the freshly merged
pieces 9, 2 and 60 models are named from more than one piece, and none of the
4, 4 and 3 models a set repeats a name for is among them; the one literal
duplicate is the stale coat, whose fresh sets already read its source (it
does not match, so its `fur` entries are dropped). The only change is
outside the reconcile's precondition: reconciling the deployed, already
reconciled plugins again drops the one `Fur` entry of 6 of the stale coat's
sets (index 5, its last `fur`), which a fresh merge already drops -- a
colour missed.
The empty name, measured the same way against 5c4061f: none of the 3,398
pre-reconcile sets, the 1,458 deployed sets or the 1,510 sets the coverage
replay writes has an entry with no name, and none of the 3,342 converted NIFs
of the deployed output (262 of them named by a set) has an unnamed shape; the
fresh-merge, deployed-reconcile, per-patch and coverage replays are
byte-identical, with the same reconcile report lines.
`CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY=1` (read only with case provenance on)
reconciles each piece on its own, reads no source for a literal duplicate
alone and binds an entry with no name by name, byte for byte as
`#alttex-case-provenance` first shipped.

**A path our output does not carry: `#reconcile-loaded-mesh`.** The reconcile
looked each model up only under our output's `meshes`. Since
`#skip-built-ube-path` and `#supersede-whole-base` a base another mod ships
built for UBE (usually the user's own UBE BodySlide build) is left to it and
our earlier copy moves to `_superseded\`, so the reconcile found no NIF and
kept the source plugin's CBBE-era indices -- recolouring whatever shape of the
build sits at that index. Now a model under `!UBE\` with no NIF in our output
is indexed against the copy the game loads, found by the coverage step's own
read-only lookup (`_mesh_exists_anywhere(output).loaded_copy`: the first loose
file by MO2 priority with overwrite first and our output left out, then the
archive by plugin load order, `#bsa-load-order-winner`); an archived copy is
read from a temporary file in `_bsa_staging` that is deleted at once. That
mesh is another mod's, not our conversion, so the provenance chain above is
not used for it: none of its names is our rename, and our source is never
read to bind it (a `fur` beside a `fur:1` in it is that author's naming, not a
split, so nothing is dropped as "may be split"). Its entries bind by name
(case-insensitive, as always); the entries of a name it carries twice or in two
spellings, and of a name any set on that model repeats (`_repeated_entry_names`,
the empty name included), are dropped -- the same "a colour missed, not
guessed" rule as a no-source provenance NIF. Our own NIF, when present, is
used exactly as before, and a path that is not ours (a male or vanilla mesh:
its set was authored for the mesh the game loads) is never looked up. A path
found nowhere keeps its set as authored, as before, and the run log counts it.
Census on the reported modlist's full run (b5c8113 output, read-only): 1,496
alt-texture subrecords name a model, 606 of them in our output; of the 890
that are not, 879 are not `!UBE\` paths (left alone) and 11 are `!UBE\` paths,
all loose in another mod, none archived, none found nowhere: 8 already right
for that mesh (5 on a witch hat, 3 first-person sets of a travel outfit), 3
wrong (the outfit's torso: `Skirt` 13, the build's `collision body`, instead
of 12). Replayed on a copy of that run's Combined pieces with its meshes: the
3 sets become `Skirt` 12 and no other subrecord changes (3 more ARMA records
fixed than the parent's replay); with `CBBE2UBE_NO_RECONCILE_LOADED_MESH=1`
the pieces are byte-identical to the parent's (76a9b3c) replay.

**The layout guess, behind the switch: `#alttex-family-strict`.** Without the
source the reconcile cannot know which shapes the rename made;
the first cut took any `name` beside a `name:k` and bound by rank in NIF order.
That put colours on the WRONG shell twice over: a middle shell lost to a failed
copy (the partial NIF ships, `dropped_shapes`) moved every later shell's colour
one shell down, and an authored `x:1` before `x` took the only `x` entry. The
rename (`_dup_shape_rename_plan`) keeps the first shape's name and calls the
k-th of the rest `name:k` in the author's order, skipping a name already
taken. So a name binds by occurrence only when BOTH hold:
- layout: its shapes are `name`, `name:1` .. `name:n`, no suffix missing (no
  `name:0`), in that NIF order with `name` first;
- count: the set's entries for the name address exactly n+1 distinct source
  shells.
Otherwise that name falls back to one entry per name, as before. The count
check catches an authored `name:k` the set does not name: with the author's
`fur:1` and two `fur` shells the NIF reads `fur, fur:1, fur:2` (the rename's
layout), but the set names two shells for three shapes. NOT exact, which is why
it is no longer the default: a lost TRAILING shell passes both checks when the
set names only some shells (`fur, fur:1` left of three, entries for shells 0
and 2: the last shell's colour lands on `fur:1`) or when an authored `fur:1`
the set does not name sits among them; and the fallback's one entry is the
set's first-listed, which can be another shell's colour.
`CBBE2UBE_NO_ALTTEX_FAMILY_STRICT=1` (read only with
`CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1`) restores the first cut (any `name`
beside `name:k`, rank in NIF order, entries past the last shell dropped).

The switch is its own, `CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE=1` (one entry per
name, the parent of all five), so the rename can be kept while this is ruled
out; it is nested under `CBBE2UBE_NO_DUP_SHAPE_NAMES`, because without the
rename a `name:k` beside `name` can only be the author's. Third-party ESPs keep
their indices and are unaffected. Live: no converted NIF a set names carries a
renamed shape today; the one NIF a set reads a source for is the stale coat
measured under `#alttex-set-provenance`.

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

### A minted armature's model paths keep their bytes (`#arma-path-bytes`)

The game reads an armature's MOD2-5 strings in cp1252. `rebuild_arma_payload`
read them as UTF-8 with errors ignored and wrote UTF-8 back, even for a path it
left unchanged, so an accented byte vanished and the armature named a mesh that
exists nowhere; the converted-mesh lookup was asked about the same wrong path.
`restore_female_models` compared and rewrote through that round trip, and
`_redirect_mod3` wrote UTF-8. All three now use one codec: cp1252 with
`surrogateescape`, so the five bytes cp1252 leaves undefined come back too. An
unchanged path is written as the bytes it had; a redirected one is `!UBE\` plus
them. The female-guard and stand-in lookups keep their cp1252 read. Live census:
0 of 9,350 model paths the coverage passes hand over, 0 armature paths in the
source plugins (one weapon model has such a byte), 0 in the 29 male-fallback
sidecars; replay byte-identical. `CBBE2UBE_NO_ARMA_PATH_BYTES=1`.

### A plugin name SkyPatcher would split gets no line (`#skypatcher-name-guard`)

The merge is the only writer of the INI (the coverage generators' own `ini_lines`
are never written; their links reach it through the sidecars). It wrote
`filterByArmors=<plugin>|<id>:armorAddonsToAdd=<Combined>|<id>,...` with both
file names as they are. SkyPatcher splits a line as `_skypatcher_fields` /
`_skypatcher_forms` model it: `;` starts a comment, `:` separates pairs, `,` the
forms of a list, `|` a plugin from its FormID. (`=` splits a pair ONCE, at its
first `=`, so an `=` inside a plugin name reads back whole and is not guarded;
guarding it dropped working lines.) A
plugin named `Armors, Extra.esp` became two forms that resolve to nothing, and
the armour lost its UBE armature behind a line that looks fine. There is no
other name to deliver it by: an EditorID target needs a runtime EditorID cache,
and a load-order-indexed FormID goes stale when the order changes. So, like a
link with no merged record, such an armour gets no line: its links are counted
in the reconciliation (`sp_dropped_unsafe_name`, part of the balance), and
`_report_skypatcher_unsafe_names` names each plugin once in a run warning and
the failures file, with the fix (rename the plugin). A Combined name with such a
character drops every line the same way; then the run names the Combined
(`sp_unsafe_output_names`, fix: another `--merged-name`) and names an armour
plugin only when its own name splits too. Live: 1 of 3,254 active plugins has a
comma, and no armour of it is in the INI (9,211 lines, 192 plugins named, none
with a separator), so the INI is unchanged. `CBBE2UBE_NO_SKYPATCHER_NAME_GUARD=1`
writes such lines again.

### The post-merge passes touch only the merge's own files (`#piece-family-match`)

`reconcile_alt_texture_indices_all`, `dedup_armo_armature_refs_all`,
`fix_spurious_hand_slot`, `resort_masters_all` and `postflight_validate_combined`
globbed `<stem>*<suffix>`: a `<stem> - Copy.esp` or `<stem>_backup.esp` the user
kept in the output folder was loaded, rewritten through `ESP.save` and reported
"validated clean" on every run. `_drop_stale_pieces` already matched only
`<stem><digits><suffix>` for its deletes. All six now share
`_combined_piece_tail` ("" for the Combined, the digits of a split piece, None
for anything else; case-blind, as the folder is): the passes walk
`_combined_piece_family`, and the delete still spares the Combined itself. Live:
the output folder holds the Combined and one piece and nothing else of that stem;
merge replay byte-identical. `CBBE2UBE_NO_PIECE_FAMILY_MATCH=1` makes the five
passes glob the broad family again; the delete stays narrow either way.

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
the only delivery path.

**Armours that share an armature share a piece (`#esl-chunk-dedup`).** Filling the
pieces armour by armour in scan order minted an armature once per piece whenever two
armours using it fell on either side of a boundary: live, 36 of the non-body coverage's
2,093 distinct armatures (2,129 records). The two copies were identical, and the
merge's record dedup cannot fold them: the first piece fills a whole Combined piece by
itself, and ESL pieces never master each other. Now armours linked by a shared
armature (directly or through a chain) form one group, each group goes whole into the
first piece with room, and armours keep scan order inside a piece (a run that fits one
piece is byte-identical). The alternative, an armour in one piece naming an armature
minted in another (a SkyPatcher line allows it), was not taken: the merge folds each
coverage piece into whichever Combined piece has room and resolves links within that
piece only. Only a group needing more than the cap is still split in scan order; the
largest live group needs 85. Live: non-body 2,129 -> 2,093 records (2,048 + 45 instead
of 2,048 + 81), every armour's links in the coverage sidecars identical, the Combined
still 2 pieces with 3,878 own records instead of 3,912 -- 34, not 36: 2 of the 36
duplicates already sat in the second non-body piece, where the merge's record dedup had
folded them (Combined2 merge collapses 16 -> 14). 15 armatures minted by both the body and the non-body pass
remain (separate patches; out of this rule's reach). `CBBE2UBE_NO_ESL_CHUNK_DEDUP=1`
restores the scan-order fill.

**Pieces never increase.** Placing whole groups is bin packing with items that cannot
be split, so it can leave a piece short that the scan-order fill would top up across a
group boundary: cap 5 and three groups of three armatures need three pieces whole, but
two in scan order (one repeated record). One more plugin to enable is a worse cost than
a few duplicate records, so both fills are computed and the grouped one is kept only
when it needs no more pieces than the scan-order fill; otherwise the scan-order fill is
used unchanged, duplicates and all. Duplicates are dropped only when that costs no
extra piece. At the same number of pieces the fill that mints fewer records wins: a
group over the cap is split in scan order inside the grouped fill, and that split can
repeat MORE armatures than the plain scan-order fill does (cap 4: [2,11], [10], [1,6],
[3,6,11] mints 8 records grouped against 7 in scan order, 2 pieces each). A full tie
(same pieces, same records) keeps the grouping. The guarantee covers the COVERAGE
pieces only: the merge's own first-fit-decreasing packing of those pieces with the
other patches into Combined pieces is a separate step and can, near the limit, need a
Combined piece more than it did with the scan-order fill (review probe: cap 5, a
3-record patch plus [2,8,10], [4,11], [1,11] packs into 3 Combined pieces against 2).
Live the Combined stays 2 pieces either way. A finer rule --
split only the group that straddles the boundary -- was not taken: it would keep some
of the savings only in the case where whole groups lose, it is more code with its own
piece-count proof to carry, and the live load order never reaches that case (2 non-body
pieces either way, so today's output is the grouped fill, byte-identical to before the
rule).

After the merge TWO armours link to a different record, the two duplicates the merge
had folded (its dedup key, `_ARMA_DEDUP_SKIP_SIGS`, ignores EDID and MO2T..MO5T); with
the grouping both move into the first non-body piece beside their armature's own copy:
- a circlet (the item of one mod, on a vanilla circlet armature) links to its own
  non-body record (`UBE_MNB_<armature>`) where it had collapsed onto another NON-body
  record -- the copy of that mod's own circlet armature, minted in the same second
  non-body piece, identical but for the MO2T/MO3T texture hashes. No body-pass circlet
  record exists; an earlier version of this paragraph said body-pass, because the A/B
  tool that found it compared records with EDID dropped;
- a shield (one mod's item on a vanilla shield armature) links to the non-body record
  where it had collapsed onto the body pass's copy of the same armature
  (`UBE_MBD_<armature>`), identical but for the EDID -- invisible to that A/B tool.
Mesh, races, slots, race-model list and alternate textures are identical in both
pairs, so nothing changes in game.

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

### Old conversions leave the output (`#stale-output-sweep`)

The output folder is never cleaned, so a base an earlier run converted stays in
`meshes\!UBE` after the planner stops making it, wins its path in game (our mod sits
high in MO2), and counts as converted for coverage. Live (2026-09-25): 37 bases, 103
files, 128 MB -- third-party covered (9), made non-playable by the winning record (27,
14 of them from sources selection no longer picks), the male mesh the female-only
rule drops (1). The census's first design, "every file no claim of this run owns", was
refuted: the planner fails OPEN to a smaller plan (a failed NPC-outfit read drops 6
sources and 48 bases female NPCs wear, under any size brake), so absence cannot tell a
lost piece from a dropped one. What ships (`src/stale_sweep.py`, glue in
`auto_convert._stale_output_sweep*`):

- **A manifest, every `auto` run** (`_conversion_manifest.json`): weight base ->
  source mod for every claim (`AutoConvertResult.claimed_weight_bases`, recorded where
  `claimed_dst_paths` takes the path), per-source patch -> source, run stamp, build. An
  earlier entry is carried while its file is on disk and no claim of this run takes
  it. A base no run recorded never moves in the run that finds it -- the first run
  after shipping is report-only by construction. A manifest that is not valid JSON,
  has another layout, or holds a key or value that is not a string is no manifest
  (named warning; the finish writes it anew).
- **Hand-placed files: no provenance signal.** Adoption (below) records an unrecorded
  base on a positive reason alone, and the next full run can move it. Nothing tells a
  file a user placed by hand from one an earlier run wrote: the converter writes no
  marker of its own into a NIF (its extra data is BODYTRI and the HDT-SMP path, which
  hand-made meshes carry too), a per-source `.espgen.json` snapshot and
  `conversion_report_<mod>.txt` are rewritten from the current plan each time the
  source runs (the dropped base is the one they stop listing), and coverage points
  the old Combined at any `!UBE` mesh on disk. So a hand-placed file moves exactly
  when it sits at the weight base of an armature a source this run drops for a
  positive reason, and then only from the second full run on; every other
  hand-placed file never moves (it is listed).
- **Positive reasons only.** `_player_armor_mesh_bases(drop_reasons=)` names, for a
  base an armature names but the plan leaves out, the rule that did it:
  `non-playable`, `third-party covered` (the `#claim-meshes-prefix` case is one),
  `female-only`; `auto_convert_mod` adds `built twin`. A skipped armature is read on
  through every other test and dropped after them, so the reason is the one that took
  it out; asking for reasons never changes the plan (live: the 151 sources plan the
  census plan exactly). A recorded base moves when its source is removed, disabled or
  excluded, or its source gave one of those reasons -- this run, or, for a source
  present and enabled that selection no longer picks, its planner asked at sweep time
  with the batch's inputs. A source that ran and resolved nothing HOLDS. An unrecorded
  base a source of this run gives a reason for is recorded ("adopted") and can move on
  a later run: live, the first run records 24 of the 37; the 13 of the two sources
  selection dropped whole stay listed (nothing names their source).
- **PLAN_COMPLETE** (`_stale_plan_gaps`): report-only, with a named warning, when the
  batch mesh index was not built, the archive index was not built or skipped an
  archive or folder (`_BsaMeshIndex.skipped`), the NPC-worn set is None while its
  switch is on, the playability map is None or could not read a plugin
  (`_armo_winner_unreadable`), the vanilla sweep did not run or claimed nothing, a
  source did not run or failed, the per-result merge-gate fields show a mesh error,
  plugin-patch failure, unreadable output or crash-class issue, or any problem-level
  warning was printed from the start of `auto` to the end of the batch
  (`user_warnings.problem_count`; a worker process's own warnings are not counted).
- **Where and when.** Full `auto` run of all mods only (never `--only-mods`,
  `--plugins-only`, the bare `convert`), inside `auto_merge and merge_blockers == 0`,
  after the partner fill (a whole-base move takes the fill copies along) and before
  the female-model restore and coverage, which read `meshes\!UBE`. Claimed bases and
  `superseded_weight_bases` are never candidates.
- **Moves.** The whole base -- bare, `_0`, `_1` `.nif`, and the `.tri`/`.xml` named
  after the stem (`x.nif.tri` goes with `x.nif_1.nif`) -- to
  `_superseded\<run stamp>\` at its relative path, all or nothing with rollback; a
  stamp folder is never reused; a file whose real path leaves the output holds its
  base; other file types are listed, never touched. A per-source patch set (ESP last)
  moves only when its recorded source is gone, or was not selected, claims nothing and
  all its recorded bases move; never in the root-write mode. The brake: more than
  max(10, 2.5% of the output's bases) is report-only (live: 37 of 1,973, limit 49).
- **A sidecar moves with the base it was named from** (`#sweep-sidecar-base`). The
  converter names a piece's `.tri` and `.xml` after the NIF stem with ONE `_0`/`_1`
  taken off (`_finalize_hdt_physics`, `_generate_hdt_xml_for_dst`, the phase-1
  `tri_stem`), so `x_1_0.nif`/`x_1_1.nif` (base `x_1`) own `x_1.tri`/`x_1.xml`.
  `stale_sweep.base_key` read a sidecar's stem back as the mesh `<stem>.nif` and
  so took a SECOND suffix off, filing `x_1.xml` under `x` (of `x_0.nif`/`x_1.nif`).
  In a folder holding both bases (sources ship that), moving a stale `x` took the
  live `x_1` piece's physics XML and morph TRI with it -- no SMP and no body morphs
  in game -- and a stale `x_1` left its own behind. The stem is now read back as
  `<stem>_1.nif`, the key of the NIFs the converter derives the sidecar from.
  `x.nif.tri` still goes with `x.nif_1.nif`. Live: the output holds no double-suffix
  mesh, so the inventory (1,973 bases, 5,469 files) is identical either way.
  `CBBE2UBE_NO_SWEEP_SIDECAR_BASE=1`: the old reading.
- **No per-source patch left in place names a moved mesh**
  (`stale_sweep.hold_for_staying_patches`). A patch set this run did not write and
  does not move is merged by any later run whose coverage fails, in a run that has no
  record of this one's moves -- a missing-mesh crash if one of its armatures names a
  moved base. So a base move is held when a staying set is recorded for its source
  (all or nothing per source: the old set was written from an older plan, and a
  `--plugins-only` run that selects the source regenerates it from its old snapshot),
  when one of a staying set's armatures names the base (covers unrecorded sets and
  another source's patch), or, when a staying set cannot be read, for every base. The
  patch decisions then run again (a set that moved because all its source's bases
  moved may have to stay) until nothing changes. Patch sets move before bases; a set
  that fails to move holds its source's bases and the ones it names. A patch written
  this run names only what this run planned (`converted_rel_paths`), never a stale
  base. Chosen over "move the source's bases and patch set together" alone because
  that leaves unrecorded sets unguarded. Live: with a full record it holds nothing
  (the three stale sets move with their bases); on the second real run, when those
  three sets are still unrecorded, it holds 1 of the 24 adopted bases -- a male mesh
  one of them names, which 772ccf1 moved into a latent missing-mesh crash.
- **Transactional.** The moves are PENDING until the merge: kept only when the new
  Combined was written from coverage alone and none of its pieces names a moved base;
  otherwise every file the journal lists goes back (a torn base's stray file too),
  since an old Combined pointing at a moved `!UBE` mesh is a missing-mesh crash. When
  coverage fails or comes back empty, every file
  goes back BEFORE the per-source fallback lists the patches
  (`_stale_output_sweep_failover`), and the female-model restore runs again (it
  re-points only to a mesh on disk, so the second pass ends where one pass over the
  whole folder does): the fallback Combined is the one a run without the sweep writes.
  An exception out of `_cmd_convert` puts them back from `_cmd_auto`; a journal a
  killed run left unsettled is put back at the start of the next `auto` run of any
  kind (below). Report: `_superseded\stale_output_report.json` and the log.
- **The read-back reads the merge's own files** (`#sweep-piece-family`).
  `stale_sweep.combined_references` globbed `<stem>*.esp`, so a user's
  `<stem> - Copy.esp` or `<stem>_backup.esp` of an older Combined -- which names the
  old meshes -- put every move back on every run, and an unreadable one did the same
  ("could not be read back"). The sweep landed before `#piece-family-match` narrowed
  every other post-merge reader, and no merge carried the change over. It now walks
  `ube_patcher._combined_piece_family(<Combined>, ".esp")` (the Combined and its
  numbered split pieces), so `CBBE2UBE_NO_PIECE_FAMILY_MATCH=1` reaches it too. Such
  a copy is not the merge's file: the game loads it only if the user enables it, and
  then it duplicates every record of the Combined as well. Live: the output holds
  only `Combined.esp` and `Combined2.esp`; the read-back over every base on disk
  finds the same 3,284 model paths either way. `CBBE2UBE_NO_SWEEP_PIECE_FAMILY=1`:
  every `<stem>*.esp`, as before.
- **A killed run's moves go back on the next run of any kind**
  (`#sweep-recover-every-run`). The GUI's Cancel is `taskkill /T /F`, so the
  `finally` in `_cmd_auto` never runs and the journal stays `moving` / `waiting for
  the merge`. Only the next full run's sweep used to put it back: a Select-mods,
  `--plugins-only`, merge-off or failed-source run in between left the old Combined
  naming meshes in `_superseded\`, and its manifest -- which carries an earlier
  entry only while its file is on disk -- dropped the moved bases, so the full run
  that finally put them back found them "not recorded as converted by any run" and
  held them for good. `_stale_recover_at_start` now runs at the top of every
  `_cmd_convert` that carries the sweep context (before any per-source patch or
  `meshes\!UBE` is read, before the manifest is written, with the sweep off too):
  a `NOTE:` line and a warning entry in the one tally per stamp folder; a file that
  cannot go back is a problem warning and a failure entry. NOTE, not a problem,
  because nothing about the plan shrank: the full run after a kill still moves
  what it decides. And `build_manifest` carries an earlier entry whose file a
  journal still in `moving` / `waiting for the merge` / `partly put back...` lists
  and that still sits in its stamp folder (`stale_sweep.stranded_files`), so a
  file that comes back later is still ours; a move the merge kept drops out as
  before. Live replay: no journal exists (the moves have not run live), so the
  output is byte-identical to the parent either way.
  `CBBE2UBE_NO_SWEEP_RECOVER_EVERY_RUN=1`: only the full run's sweep puts them
  back, and a run before it drops their record.
- **Isolated.** Any exception in the decisions, the moves or the report puts back
  every file the journal lists (the stamp folder is new, so a file there is one this
  run moved -- including one whose own roll-back failed), forgets the adoptions, warns,
  and returns: restore, coverage and merge run as without the sweep.

Coverage replay with the 37 bases gone (repro8): 9,835 -> 9,826 links, 9 removed, 0
added, 189 re-pointed, 198 armours; 0 a woman can wear changes visibly. Switched off
(`CBBE2UBE_NO_STALE_OUTPUT_SWEEP=1`) nothing is read, written, moved or reported;
`CBBE2UBE_STALE_OUTPUT_SWEEP_REPORT_ONLY=1` lists and records but never moves.

### The vanilla-coverage warning reads what ships (`#vanilla-links-delivered`)

After the merge the run warns when a vanilla sweep ran and nothing vanilla got
linked (`_vanilla_links_check`). It summed the sweep source's own patch links, but
with the winner-scan coverage as the sole generator those per-source patches stay
unmerged: a coverage change that dropped every vanilla armour still passed, and the
run printed "vanilla coverage: 0" as a plain line. There the count of vanilla/DLC
targets in the delivered INI now decides. The fallback merge ships the per-source
patches and keeps the sweep source's own count (a mod's link to a vanilla record
would mask a dead sweep in the delivered one). Output-neutral: only the warning
changes, so it has no switch.

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
  - **A wig the body pass mints alone** (`#wig-exclude-keep`, 2026-09-25).
    `#wig-body-pass` mints a wig whose ARMOUR also says a deforming slot in the
    body pass, which withholds everything an excluded mod owns: such a wig was
    always withheld and named in the "no UBE armature from any mod" warning,
    while the same wig on a hair-only armour (non-body pass) was kept. A wig is
    a non-body piece, so the user's rule holds in both passes: when the body
    pass's `to_mint` is the wig-only mint (`_wig_here`), after the
    third-party-drawn exit, it asks `_excluded_piece_holds` with the owned
    armour's records (defining record and overrides, collected in Pass 1 only
    while this rule is live). No reason: minted like any wig (own mesh; the
    guard, dead-armature and third-party rules after it) and reported with the
    non-body pass's kept pieces (`exclusion_nonbody_kept`, counted after the
    dead-armature rule). A reason: withheld as before; one naming another mod's
    patch goes to `exclusion_body_held`, so the report lists it as left to that
    patch. An armour a deforming armature was admitted for (a hood or wig
    riding along with a converted body) is never asked -- withheld whole, as
    before. Live replay with the one live wig's follower mod excluded (repro8,
    skipbuilt, PYTHONHASHSEED=1): the parent withholds the wig (9836 -> 9835
    links, named in the warning); this rule keeps it (+1 link, 0 removed, 0
    full-record changes; the output is byte-identical to the run with no
    exclusion; the report's NOTE lists 2 kept pieces, the warning is gone). No
    exclusion, or the switch set: all 3 ESPs and 3 sidecars byte-identical to
    the parent. `CBBE2UBE_NO_WIG_EXCLUDE_KEEP=1` withholds such a wig again;
    nested under `CBBE2UBE_NO_EXCLUDE_BODY_ONLY` and
    `CBBE2UBE_NO_WIG_BODY_PASS` (off, the wig is not minted at all, so it is
    neither kept nor withheld).
- **Armour a SkyPatcher-delivered UBE patch already covers**
  (`#skypatcher-patch-recognition`). SkyPatcher reads INIs nested inside its type
  folders and recommends a subfolder for a plugin-named INI; the check read
  `armor/*.ini` only. A UBE patch may also add an armature that keeps the source
  mesh on the UBE races (a helmet, a wig): an added armature whose primary race is
  a UBE_AllRace race counts when its mesh is a loose file and the patch's UBE
  addons cover every biped slot of the armour. The ESP half keeps the `!UBE\` path
  test. `CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION=1` restores the flat, path-only
  read.
  **The `!UBE\` addons pass the same test** (`#third-party-ini-slot-check`,
  2026-09-25). A line adding a `!UBE\` armature excluded its targets outright, so a
  cape-only UBE addon on a cuirass hid the cuirass, and an addon whose plugin is
  unchecked in the load order hid its targets although SkyPatcher adds nothing
  for it. Now an addon counts only when its plugin is in the active load order
  (`active_plugins`, from `paths.active_plugins_ordered`; unknown = no check), and
  a target is excluded only when the slots of all its counted UBE addons, of
  either kind, cover every slot of the armour. Live: 11 lines, all loaded and
  slot-complete, 0 armours move. `CBBE2UBE_NO_THIRD_PARTY_INI_SLOT_CHECK=1`
  excludes on any `!UBE\` addon again.
  **The armour's slots are its winner's** (`#third-party-ini-winner-slots`,
  2026-09-25). The slots came from plugins in enabled mod folders only (a union
  over every record, loaded or not), so a complete `!UBE\` refit of an armour no
  mod overrides (vanilla or DLC in the game's Data folder) read as unknown and
  was covered again: two bodies, the harm this exclusion exists to prevent. Now
  the slots are the BOD2 of the load-order winner: `active_plugins` walked from
  the last, each name resolved to the file the game loads through
  `plugin_index` (`paths.plugin_file_index`, the #root-plugin-index: overwrite >
  enabled mods > game Data). Mod-folder plugins reuse the records read for the
  addons; a plugin outside the mods folder is read only when its TES4 names the
  armour's plugin as a master (or is it); a plugin in our own output or a
  skipped folder is passed over. When the winner cannot be read (no load order
  or index, the record in no loaded plugin, no BOD2, or an unreadable plugin
  above it that may hold it), a target a `!UBE\` addon names is excluded as
  before the slot check -- never newly covered on unknown slots -- and both
  callers print how many (`_print_unchecked_ube`); one only a UBE-race addon
  names stays covered, as that test always did. Live: all 11 targets read from
  their winner, 0 unchecked, 0 armours move.
  `CBBE2UBE_NO_THIRD_PARTY_INI_WINNER_SLOTS=1` restores the mod-folder union and
  covers a target whose slots are unknown.
  **The walk always reads the root index** (`#winner-walk-root-index`,
  2026-09-25). Both callers handed over `paths.plugin_file_index`, which is the
  root-only index only while `#root-plugin-index` is on; with
  `CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN=1` it is the legacy recursive walk,
  which can resolve a name to an unloaded copy in a higher-priority mod's
  subfolder (`optional\`, `_unmerged_patches\`). `_records_of` reads nothing from
  a mod-folder file the scan did not read, so the winner was missed, the slots
  were unknown, and a cape-only `!UBE\` addon hid its cuirass. Now both callers
  pass `_winner_walk_plugin_index`: `paths.plugin_file_index` while the root
  index is on (the same files), `paths._plugin_file_index_root` otherwise.
  Unreadable plugins above the record: one whose TES4 header reads and neither
  is nor masters the armour's plugin cannot hold the record and is passed over;
  otherwise the slots are unknown. Defaults are unchanged by construction.
  `CBBE2UBE_NO_WINNER_WALK_ROOT_INDEX=1` hands over `paths.plugin_file_index`
  again.
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
  one (+2 links) -- that commit is why it does not. Its effect on colour
  variants was missed: the post-merge alt-texture reconcile looked only in our
  output, so a set on a left-to-its-builder path kept its CBBE-era indices
  (full run: 3 of 11 such sets wrong, a torso's `Skirt` on the build's
  `collision body`); `#reconcile-loaded-mesh` (alt-texture section) indexes
  them against the copy the game loads.
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
- **A wig on a deforming armour** (`#wig-body-pass`, under the same user
  call). An armour with any of slots 32/33/34/37/38 goes to the body pass,
  which mints an armature only with a converted mesh; the wig rule lived in
  the non-body pass alone. So a playable, named wig whose ARMOUR also says
  slot 38 (its one armature says 31/41; nothing draws 38 -- an authoring slip,
  and on vanilla it only unequips calf items) drew nothing on UBE. Now, when
  the DefaultRace, race-list and mesh rules admitted no armature of such an
  armour, an armature whose OWN BOD2 is hair slots only is taken: DefaultRace
  first (every UBE race), else through `_race_list_admits` with the hair test
  as `arma_ok` (the race-list rule's own switch applies), own mesh, never a
  beast variant (`_bv`) or an armature naming a UBE race. The third-party and
  dead-armature rules apply as to any armature; the accessory rule does not
  fire on a wig alone (a wig is no deforming armature to ride with). When a
  deforming armature IS admitted the old rules already take a DefaultRace hair
  armature as an accessory, so the wig step runs only on an empty `to_mint`
  and nothing admitted before moves. Census at the parent (every hair-only
  armature of an armour in the body pass): 26 rows -- 10 Argonian hood or hat
  variants of hooded robes (Argonian primary, no human race: never taken; the
  human hood rides as an accessory), 2 non-playable armours whose DefaultRace
  helmet already rides along, 13 unnamed race or creature skins (creature
  parts, a unicorn horn, follower skins with their SMP hair -- one already
  UBE), and exactly 1 wig: playable, named, Wood Elf primary (+ vampire),
  armour 31+38, worn by a follower. Live replay: 9835 -> 9836 links, +1
  (UBE Wood Elf + vampire, own mesh), 0 removed, 0 re-pointed, 0 full-record
  changes. `CBBE2UBE_NO_WIG_BODY_PASS=1`; `CBBE2UBE_NO_COVERAGE_WIGS=1` turns
  it off too. NOT done: a nude-torso item that is no race's skin (one named
  ring swaps in the vanilla naked torsos, slots 32+36). VERIFIED (record
  read, code, live replay): its armatures are the seven Skyrim.esm naked
  torsos (DefaultRace `NakedTorso` among them) plus the ring's own slot-36
  armature (Argonian primary, listing the human races), none of them a hand
  or foot. No rule admits any of them: the DefaultRace rule and the race-list
  rule both ask the converted-mesh test (`_mesh_admits`), and neither the
  naked torso nor the ring mesh was converted, so `to_mint` stays empty and
  the armour never becomes a target (it is in no body-pass list; 0 links).
  `#coverage-nude-skin` is NOT the blocker: it acts only on hand/foot
  armatures (`_parts`, filtered by `_BIPED_SLOT_HANDS_FEET_BITS`;
  `_UBE_BODY_PART_MESH` holds hands and feet) and never sees this armour.
  INFERRED, not checked in game: the seven torsos are Skyrim.esm-defined, and
  the codebase's own notes (the comment above `_HAIR_ONLY_SLOTS`; the removed
  vanilla race patch in `auto_convert`) say the runtime race dispatcher
  (RaceCompatibility / RaceDispatcher) extends such armatures to the UBE
  races. So a UBE actor wearing it most likely shows the vanilla naked torso
  mesh (not a UBE body mesh) and no ring -- the ring's armature comes from an
  add-on master, names no UBE race, and whether the dispatcher extends it is
  unchecked. A fix needs a UBE torso redirect for the naked torso and an
  own-mesh rule for a non-deforming race-list armature on a body armour -- a
  new policy, left to the user; population 1.
- **The headgear test's keyword id** (`#armorhelmet-kw-fix`, 2026-09-25). The
  hair-only headgear test (`_hair_only_armo_is_equippable_headgear`) compared
  keywords with 0x06BBD9, which in Skyrim.esm is the KYWD `ArmorMaterialElven`;
  `ArmorHelmet` is 0x06C0EE (both read from Skyrim.esm). It now reads 0x06C0EE,
  still only from Skyrim.esm. The other hard-coded form ids in `src/` were
  checked against their defining plugins and are right: DefaultRace 0x19, the
  ten playable races and their vampire variants, the four beast races,
  ManakinRace 0x10760A (Skyrim.esm), the 16 `00UBE_` races (UBE_AllRace.esp).
  Census at the parent (every armour the non-body pass asks the test about):
  417 asked, 229 carry ArmorHelmet, 2 carry the elven keyword (both have a gold
  value and ArmorHelmet: no flip). 7 flip, all to headgear: 6 non-playable,
  unnamed helmets nobody wears whose armatures are creature-race primary with
  only creature races (no DefaultRace or race-list rule takes them: 0 links),
  and 1 playable named invisible helmet the wig rule already covered, now
  counted as headgear instead of a wig. Live replay: all 3 ESPs and sidecars
  byte-identical to the parent, with and without an exclusion; with
  `CBBE2UBE_NO_COVERAGE_WIGS=1` the fix adds exactly that helmet (+1 link).
  `CBBE2UBE_NO_ARMORHELMET_KW_FIX=1` reads the old id.
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
- **Per-race siblings draw only for their own races** (`#coverage-race-subset`).
  The generalisation of the beast rule to human races. Authors split one piece
  into DefaultRace-primary armatures on the same slots by race: the human races,
  Orc only, the three elves, or a mod's own race only. By the same model (an
  actor matches an armature only through a race it lists) each vanilla race
  draws one of them, but both passes minted each for all 16 UBE races, so a UBE
  actor drew all of them at once. `_race_subset_split` groups an armour's
  DefaultRace armatures to mint by overlapping slots (their BOD2, else the
  armour's) and changes a group only when a member lists a vanilla human race.
  A member listing only non-human races (a mod's own race, an elder race, the
  mannequin race) is not minted; a beast-only one, judged with the mannequin
  race ignored as #coverage-beast-variant judges it, is left to that rule.
  When the human-listing members' UBE counterparts (`_ube_races_for_race_list`)
  are pairwise disjoint and not all 16, each member is minted for its own. The
  races no member claims go to the members that list no race (the DefaultRace
  default), else to every human-listing member (what they draw today), so no
  UBE race loses its draw; a lone armature claims none and keeps all 16.
  Overlapping lists are layered pieces the base game draws together, and are
  unchanged (a near-full robe list missing one vampire race beside a full one,
  live). So is an armour where the race-list rule or
  #coverage-third-party-drawn chose the races. A minted
  armature is one record shared by every armour that lists it, so
  `_RaceSubset` targets the union, and all 16 as soon as one armour mints it
  unsplit. Live census (both passes, every armour with two or more minted links):
  19 same-slot groups; 11 layered with full lists (unchanged), 1 near-full plus
  full (unchanged), 6 disjoint per-race splits (circlets split humans/Orc/elves
  on 3 armours, a helmet split 14 races + Orc, 2 mesh-less FX-slot armatures
  split humans/Orc+beasts, whose elves stay on both: 3 + 1 + 2) and 1 robe with
  a sibling for a mod's own race. Replay: 7 armours, 9835 -> 9834 links (that
  robe sibling), 15 links narrowed (armour x armature), drawn from 7 source
  armatures minted as 9 records (the two FX armatures in both passes), 0
  re-pointed; every (armour, UBE race) that drew before still draws. Re-counted
  on the grown modlist (replay of 94340ee vs 217e870): a second helmet split 14
  races + Orc joins, so 8 armours, 9927 -> 9926 links, 17 links narrowed from 9
  source armatures minted as 11 records. `CBBE2UBE_NO_COVERAGE_RACE_SUBSET=1`.
  The report and the pass stats (`_RaceSubset.stats`) are read from the FINAL
  targets, after the union: an armour counts as split per race only when one
  of its minted armatures draws for fewer than 16 races (a drop alone, or a
  narrowing another armour's unsplit use widens back, does not), and an
  armature is "left off" only when neither pass mints it (`race_subset_minted`
  carries each pass's minted set to `_report_coverage_holds`). The live report
  line went 8 -> 7 armours (the robe's drop is its own line); output bytes are
  unchanged.
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
  `_mesh_exists_anywhere(...).unfitted_skin`). That is the whole bone test: a
  skin bone whose name contains one of `_BODYFIT_BONE_MARKERS` (case-insensitive
  substring) keeps the cape out, and every other bone -- pelvis, spine, neck,
  head, clavicles, arms, feet, a cape's own bones -- is allowed. It is not a
  "back and shoulders only" test, on purpose: it is the crash guard's own list,
  so it admits exactly the cloaks the guard left unconverted, and the one live
  cape is bound to the pelvis, all three spine bones, the clavicles, the
  pauldrons and the upper arms (adding `pelvis` to the list would drop it and
  bring the defect back; pinned by CBC-i). An unskinned, body-fitted,
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

## What a run reports, and which settings it runs with

- **The tally is the record** (`#one-tally`). `_cmd_convert` counted its
  end-of-run `N failure(s), M warning(s)` in two integers beside
  `_RUN_FAILURES`, and five classes raised the integers without an entry: a
  load-breaking issue on the final Combined ESP (and its other postflight
  issues, incl. missing-nif), a mesh missing its `_0`/`_1` partner, a
  VirtualBody re-hide, patch-validator hits, and in `auto` a failed overlay
  transfer. The GUI reads only the failures file, so a Combined CTD ended
  "exit code 2 - check the log" with no list. Every counted class now goes
  through `_record_failure`, and the tally is counted from the record
  (`_run_tally`, `failure_summary.counts`), so the two cannot disagree. A class
  of N is one entry carrying `count: N` (40 validator hits are one popup line);
  `count` is written only when it is not 1, so single entries keep their old
  format. The exit code is unchanged in every case (a failure was already
  counted wherever one is now recorded); only the log's numbers and the file
  change: an unreadable output mesh counts per file, and "merge skipped" counts.
  `auto`'s post-convert failures are the failures recorded after `_cmd_convert`.
- **Dry run writes nothing** (`#dry-run-writes-nothing`). `auto
  --overlays-only --list-only` ran the overlay transfer: the overlays-only branch
  returned before the list-only check. It now lists what would be remapped
  (`overlay_transfer.plan_overlays`, the same source rules as the transfer) and
  returns. The window's Dry run with both toggles drops `--convert-overlays`.
- **The overlay Dry run lists for the mode the run would use**
  (`#dry-run-copy-mode`). Under `--overlay-copy` ("Add UBE copy") the real pass
  bakes only overlays a RaceMenu paint script registers whose texture is found,
  and skips everything without texconv, PapyrusCompiler or the Papyrus base
  (`Scripts.zip`); the list showed the replace mode's set either way. The list
  now names its mode, uses `plan_overlay_copies` under copy mode (the same
  filter as the pass, `_copy_call_wanted`, and a test that the pass bakes what
  the plan lists), and says when a missing tool would make the real run skip
  every overlay (`copy_mode_tool_gap` / `replace_mode_tool_gap`, read-only).
  Reviewed on 0081b20, the list still named overlays the pass skips: a region
  whose CBBE/UBE reference mesh is missing or unreadable (the copy pass skipped
  it with a bare `continue`, replace mode logged it), and every overlay when
  Scripts.zip holds no `TESV_Papyrus_Flags.flg`. Each check is now one
  function both sides call. `_load_ref_meshes` is the region decision:
  `build_region_correspondence` builds from it, and `region_ref_gap` asks it
  without the projection, so `plan_region_gaps` (both modes) prints "!! the
  real run would SKIP this region: ..." under the region. `papyrus_base_gap`
  reads the zip's name list for the flags file; `_assemble_papyrus_imports`
  and `copy_mode_tool_gap` both call it (a zip that cannot be opened raises in
  the pass as extracting it did, and the Dry run says it cannot be opened). The
  copy pass now logs "!! overlay copy: SKIP region '<r>' (<why>) -- N
  overlay(s)" and names the missing flags file. The drift-guard test runs the
  real correspondence (a stub mesh reader over a small grid) and the real
  Papyrus-base assembly on a real zip, with a missing and an unreadable
  reference mesh and a zip without the flags file.
- **A bad settings import changes nothing** (`#settings-import-guard`).
  `load_values` turns an absent, torn or foreign file into pure defaults by
  design; Import used it and saved the defaults over the recipe.
  `load_for_import` refuses a file that is not a JSON object holding at least one
  registered key (or `_known_settings`, so an all-defaults export still imports).
  Export and Reset/Import now read `save_values`' False. `_coerce` reads a
  hand-edited bool string with the environment's words (`"false"`, `"0"` are OFF).
- **The saved settings reach every run** (`#settings-everywhere`). A headless
  `CBBEtoUBE.exe auto` / `convert` now applies `CBBEtoUBE_settings.json` (the one
  beside the exe) at the entry point, before the converter is imported; a variable
  already set in the environment wins; the log says `effective settings: from
  <file> -- set ...` and `conversion_settings.json` records it
  (`settings_applied`). The window's child and the two parity harnesses carry
  `CBBE2UBE_SETTINGS_APPLIED` and are not re-applied. The window's own helpers
  (Check setup, the mod lists, the UBE-mesh scan) run under
  `gui_settings.SettingsOverlay`: the child's environment for the duration of the
  call, then the window's restored (shared by overlapping helpers; the last one
  out restores). `CBBE2UBE_NO_HEADLESS_SETTINGS=1` makes a headless run ignore
  the file, as before. `python -m src.auto_convert` does not go through the entry
  point and still reads only the environment.
- **The window's child says who applied its settings** (`#settings-source-line`).
  The child goes through the entry point like every `auto`, and the marker was
  recorded as a skip, so every window run logged `settings file NOT applied
  (already applied by the settings window)` -- the line people read to see
  that their settings reached the run. The marker case is now recorded as
  `{"by": "the settings window"}` (log: `effective settings: from the settings
  window`); `NOT applied` is kept for a file that really was not (switched off,
  absent, torn).
- **A bad Worker processes value cannot strand the window**
  (`#workers-box-guard`). The box is free text; `_launch` locked the window
  (running, Convert off, selection locked, bar spinning) and only then read
  `int(workers_var.get())`, which raised on `''` or `abc`: no worker thread, no
  `_DONE`, and 'Converting...' until a restart. Convert now refuses a value that
  is not a whole number of at least 1 (`parse_workers`), and `start_run` builds
  the arguments before anything is locked and puts the window back if the
  lock or the start raises.
- **A dry run keeps the run log** (`#dry-run-keeps-the-run-log`). `auto
  --list-only` / `--dry-run` (or an abbreviation argparse accepts) converts
  nothing, so `_log_target` sends it to `CBBEtoUBE_cli.log` with no rotation,
  like `validate` (`#run-log-only-for-runs`). The window follows the same rule
  (`run_log_plan`, `prepare_child_log`): it tails the cli log, removes the old
  one first so the tail cannot stream the previous command, rotates nothing and
  opens no failures popup. Two dry runs after a dead run used to rotate the
  dead run's log out of `CBBEtoUBE_previous_run.log`.
- **The Select list offers only what a run converts**
  (`#select-list-ube-native`). `_cmd_auto` drops high-confidence UBE-native
  mods before `--only-mods`; the Select list did not, so a ticked one ended
  "NOT FOUND" and exit 2, pointing back at the list. `_ube_native_hits` is the
  one decision: `list_convertible_mods(mark_ube_native=True)` marks those mods
  and the Select list leaves them out, naming them in the log panel. The
  Exclusions list and the UBE-mesh scan still list every mod (they exist to
  find these). `--only-mods` on a dropped mod now says it was dropped and names
  `--no-ube-native-scan`. The window has no setting for that switch, on
  purpose: the scan guards against double-converting.
  WHICH BODIES (`#select-list-bodies`): the list judges in the window, before
  any run, with the reference bodies the converter finds on its own -- what the
  Reference bodies dialog starts on. The run judges again in its child with
  the bodies confirmed in that dialog, which exist only once Convert is
  pressed, and `_body_trees` caches the window's pair for the session. With the
  dialog's starting pick the two agree; with another pick they can differ (a
  listed mod skipped, or a left-out mod the run would convert). Not unified on
  purpose -- the list cannot know a pick not yet made -- so the Refresh button's
  tooltip and the log line naming the left-out mods say it.
- **Refresh rescans** (`#mod-scan-rescan`). `_ARMOR_MOD_DIRS_CACHE` is keyed on
  the mods root, the enabled set and some switches, never on mod contents, and
  nothing cleared it; it was built for an in-process Convert that no longer
  exists. The window's Refresh and Exclusions lists pass `rescan=True`, so a mod
  updated in MO2 while the window is open is read again; the UBE-mesh scan right
  after the Exclusions list still reuses that scan.
- **A tool folder that cannot be written is said out loud**
  (`#read-only-tool-folder`). The window shows a run only by tailing its log,
  and the child's "could not write the log" note went to a DEVNULL stderr, so a
  run in a protected folder showed nothing but "finished (exit N)". The window
  probes the folder at start (`folder_write_error`: one probe file, created and
  deleted, since `os.access` says yes to a protected folder) and warns in the
  log panel; a run whose log never appears says so (`tail_child_log`); a failed
  settings or exclusions save (the saves' False was never read) says so in the
  status line every time and in a popup once per kind (`SaveNotice`). Choices
  still apply for the session.
- **The window keeps its own log** (`#gui-session-log-kept`). The window tees to
  `CBBEtoUBE_gui_session.log`, a different file from the run log, but `_worker`
  still called `release_log_tee` before each run, for a shared-handle clash
  that no longer exists; after the first run every window-side traceback went
  nowhere. It no longer releases, and the Tk root's
  `report_callback_exception` (`tk_error_reporter`) writes a callback's
  traceback to the session log and the log panel.
- **The window's wiring is tested through the window** (`#gui-wiring`). Every
  fix above was tested through a module-level helper and none through the Tk
  closure that calls it: reverting Refresh's `rescan`/`mark_ube_native`, the
  run worker's `dry_run` and its dropped `release_log_tee`, and the
  `report_callback_exception` hook left every GUI test green, and so did
  dropping the saved settings from Refresh, the overlay list, the exclusions
  lister, the UBE-mesh scan and the diagnostics zip. `tests/test_gui_wiring.py`
  builds the real window in a child interpreter, presses those buttons and
  menu entries and Convert (dry and real) with recorders in place of the
  helpers and a stand-in conversion child, and checks what each closure passed
  and under which settings. Pair LOW-s now puts `release_log_tee` back in the
  worker, where the bug was.
- **The end-of-run popup words each kind** (`#popup-per-kind`). Every FAILED
  entry read "did NOT convert -- their armor keeps its previous state", which
  #one-tally made wrong for the Combined ESP's load-breaking issues: the plugin
  was built and is unsafe to load. `failure_summary.WRITTEN_BUT_BROKEN` names
  the failure kinds whose output WAS written (the load-breaking plugin, a
  CTD-class or unreadable mesh, a partial mesh, a merge that failed or was
  skipped) with a sentence each; the title counts them as problems in what was
  written, and every other failure keeps the old wording. Reviewed on 0081b20:
  a merge that failed or was skipped wrote NO Combined ESP, so counting it as
  a problem in what was written was wrong too. Those two kinds moved to
  `failure_summary.NOT_WRITTEN` (same sentence); the title and the status line
  say "no Combined ESP was built" for them and count only the written-but-broken
  kinds as problems in what was written (`_own_sentence_parts`). The stale-output
  sweep's "stale sweep put back failed" (moved files that could not go back to
  `meshes\!UBE`) was never added and read as a conversion that did not happen --
  the opposite of the truth: the run converted, and a plugin may name a mesh that
  now sits in `_superseded\`, a missing-mesh crash. `failure_summary.MOVED_NOT_PUT_BACK`
  gives it its own sentence (move the listed files back by hand before playing),
  and the title and status line say "moved meshes were not all put back"
  (`#sweep-put-back-wording`). Still a failure in the one tally. Words only, no
  switch; the run's files do not change.
- **The --incremental fingerprint skips launch plumbing**
  (`#fingerprint-skips-plumbing`). It hashed every `CBBE2UBE_*` variable,
  including ones that cannot change a mesh, so a scripted re-run
  (`CBBE2UBE_NO_PAUSE=1`) or a pinned log reconverted everything.
  `_FINGERPRINT_PLUMBING` leaves out NO_PAUSE, RUN_LOG, CONFIG, EXCLUSIONS,
  SETTINGS_APPLIED and NO_HEADLESS_SETTINGS (the applied settings are hashed as
  their own variables; exclusions choose mods, which the fingerprint already
  leaves out). The layout overrides (MO2_INI, MODS_ROOT, GAME_DATA, OUT_MOD) stay
  in: a different game Data or mods folder can change a mesh. This changes when
  an incremental run reconverts, never what a NIF becomes.
  `CBBE2UBE_NO_FINGERPRINT_SKIPS_PLUMBING=1` hashes every variable again.
  THE SURVEY (2026-09-25): every `CBBE2UBE_*` name read under `src/` and the
  entry point (~530) is either plumbing -- a launch or UI detail, a log or sink
  path, a worker count or memory budget, a thread count -- or treated as
  output. The plumbing, each with its reason in `_FINGERPRINT_PLUMBING_WHY`:
  NO_PAUSE, RUN_LOG, GLOW_LOG, STANDOFF_LOG, CONFIG, EXCLUSIONS,
  SETTINGS_APPLIED, NO_HEADLESS_SETTINGS, WORKER_MEM_GB, OVERLAY_WORKERS.
  Worker counts qualify because output does not depend on the pool size since
  `#pair-unit-dispatch` (a weight pair is one unit on one worker;
  `tests/test_pair_unit_dispatch.py` pins it; a 16-worker run matched
  `--workers 1` on 296 files); WORKER_MEM_GB only picks that count, and
  OVERLAY_WORKERS is the overlay pass's thread count, one texture per thread.
  Treated as output and hashed: every tuning knob and `NO_*` switch; the layout
  and the body and tool paths (UBE_BODY*, CBBE_BODY*, UBE_TEMPLATE, UBE_OSD,
  TEXCONV, PAPYRUS_COMPILER); and the diagnostics switches (DEBUG_*, *_DEBUG,
  *_TRACE, *_AUDIT, STAGE_DUMP, FIELD_STATS, NIPPLE_PROBE, BACK_DUMP_DISP,
  NO_STANDOFF_AUDIT) with RAY_CHUNK and NO_ZEROED_PROBE_MEMO, which run code
  inside the conversion that no test proves byte-neutral. Hashing too much
  costs a reconvert; leaving out too much reuses a stale mesh.
  THE SURVEY IS MACHINE-CHECKED (reviewed on 0081b20: the hand survey missed
  `CBBE2UBE_ANTIPOKE_SURFACE_QUIET`, which only silences two trace lines of the
  opt-in #antipoke-surface-req pass, and fit none of the documented groups).
  QUIET is now plumbing; `tests/test_fingerprint_survey.py` runs that pass with
  the trace on and off and gets the same garment. The groups became data:
  `_FINGERPRINT_HASHED_GROUPS` in `auto_convert` -- `switch` (every name read
  through `_flag`), `knob` (through `_knob`), and three listed groups: `layout
  and paths`, `diagnostic` and `not proven neutral`, each with its reason. The
  test parses every string constant and f-string under `src/` and the entry
  point (504 names on 2026-09-25: 255 knobs, 205 switches, 16 diagnostics, 15
  layout and paths, 11 plumbing, 2 not proven neutral) and fails on a name in
  neither the plumbing table nor a group, on a listed name nothing reads any
  more, and on a name both left out and hashed. A name holding a word from
  `_FINGERPRINT_LISTED_ONLY` (QUIET, LOG, TRACE, DEBUG, WORKER, MEMO, ...)
  never joins `switch` or `knob` by how it is read: it must be listed, so a new
  verbosity switch is judged rather than hashed by default. Planted reads in a
  copy of a module are the negative control. The diagnostics that ADD printed
  lines (DEBUG, TRACE, PROBE, STATS ...) stay hashed on purpose: someone who
  turns one on wants the conversion to run, and left out, an --incremental run
  would reuse every NIF and print nothing. Only a switch that silences lines
  can be plumbing.
- **A mod folder name with a comma is one name** (`#whole-mod-names`). The
  window passes every name as its own `--exclude-mods` / `--only-mods` /
  `--coverage-exclude-mods` / `--overlay-*-mods` flag, and `_split_mod_arg`
  split every value on commas, so "Armor, Clothing Pack" became two names that
  matched nothing: excluded, it was still converted and covered. The rule: a
  value that is exactly the name of a folder in the mods root is one name; any
  other value is split on commas, so `--exclude-mods "a,b"` from a shell still
  means two mods, and a flag repeated with comma lists still works. (Splitting
  only when a flag is given once would have broken that mixed form, and still
  split a single comma folder the window passes.) The mods root is read only
  when a value holds a comma. `CBBE2UBE_NO_WHOLE_MOD_NAMES=1` splits every value
  again.

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
