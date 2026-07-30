# Conversion speed — measurements, dead ends, and what is left

Opened 2026-07-30. A full-pack reconvert takes about two hours, which is not
viable. This is the running record: every number here came from a measurement,
and every hypothesis that turned out wrong is kept rather than deleted, because
two of them were mine and both looked obvious at the time.

**Mod-agnostic by policy.** Specific mod names live in a local, gitignored note;
targets here are described by shape (file count, phase split, cost).

---

## Rules for this log

1. **Measure before optimising.** Stated first because it was violated twice on
   day one — telemetry was added without timing it, then two speed hypotheses
   were pursued before profiling anything.
2. **No speedup is claimed without an A/B on the same input.** "It removes work"
   is not "it is faster"; what fraction of wall clock the work represented has
   to be measured.
3. **Record the ceiling, not just the change.** An optimisation worth 20% should
   say so up front, so nobody expects 2×.
4. **A wrong hypothesis stays in the log.** Deleting it invites re-deriving it.

---

## Baseline — full pack, 2026-07-30 (v1.2.2)

Measured from the run's own per-mod timing notes.

| | |
|---|---|
| NIF conversion phase | **103.8 min** |
| files | **3,653** |
| worker-seconds consumed | **22.1 worker-hours** |
| **mean per-file CPU** | **21.7 s** |
| pool | one shared `_NifPool`, 16 workers, warm across mods |
| ideal wall at 16 workers, one queue | **82.8 min** |

Other phases (discovery, ESP generation, merge, postflight) make up the
remainder of the ~2 hours and have not been measured yet.

---

## Ruled out

### ~~Telemetry is not the cause~~ — WRONG, overturned by the first profile
The fit measurements were the first suspect, and this section originally
dismissed them: a chain measurement is ~120 ms against a 21.7 s mean per file,
so "the whole measurement stack cannot be the two-hour problem".

**That reasoning was wrong.** Measurement cost scales with mesh size, and the
mean hides the files that matter. Profiling one expensive conversion (70k verts,
11 shapes, 176.3 s) put **fit_metrics at roughly half the wall clock**:
`_rim_distance` 25%, `_cast` 13%, `_pairs` 8.5%, plus the `norm`/`cross`/`reduce`
they call. Reasoning from an average about a distribution with a 15x tail is
the same mistake as judging the torso by the bust band.

Still worth having fixed, and both shipped in 1.2.2b:
- 63% of armed shapes were measuring nothing at all — arming tested the *body*
  region size, which is a constant, so every phase-2 shape armed regardless of
  whether the garment was near the band;
- `record_standoff` was on the dense formulation, and a dense garment raised
  `MemoryError` at 36 M ray-triangle pairs.

### The pool is NOT rebuilt per mod
Second hypothesis, also wrong. `auto_convert` already creates **one shared
`_NifPool` for the whole batch**, so per-worker caches (pynifly, body OSD,
CBBE→UBE delta) persist across mods. That optimisation predates this log.

---

## The real decomposition

**Batching is worth at most ~20%.** Work is dispatched *per mod* and
`run_batch` blocks, so parallelism is capped by each mod's file count — a
six-file mod uses six of sixteen workers and the rest idle until it finishes.

| | |
|---|---|
| batches using fewer than 16 workers | **92 of 131** |
| wall spent in them | **42.3 min**, 623 files |
| recoverable by one cross-mod queue | **~21 min (20%)** |

**Per-file cost dominates.** Even with perfect scheduling the floor is ~83 min,
because the average file costs 21.7 s of CPU and the worst cost far more:

| s/file | files | phase split | note |
|---|---|---|---|
| 333.6 | 6 | 4 copy, 2 body-swap | 15× the mean |
| 277.3 | 14 | 12 copy, 2 body-swap | |
| 201.1 | 8 | 6 copy, 2 body-swap | |
| 175.7 | 36 | 26 copy, 10 body-swap | |

**The 10 most expensive batches are 26% of all worker-seconds**, and they are
*also* under-filled — a slow mod is slow twice over. Every one of them has a
small body-swap count against a large copy count, which points at phase 2
(`convert_nif_phase2`) rather than the copy path, but that is an inference and
has not been profiled.

---

## Plan, in order

1. **Profile one expensive file.** A 333 s file against a 21.7 s mean is a 15×
   outlier and nothing is known about where that time goes — warp, reskin, SMP,
   layered-cloth passes, or something else entirely. This decides everything
   after it. *(Queued: needs a quiet machine.)*
2. **Cross-mod batching** — one queue, fixed worker pool, no per-mod barrier.
   Worth ~21 min, disproportionately helps the slow mods because they are the
   under-filled ones. Architectural: ESP generation is currently interleaved
   with conversion per mod.
3. **Per-file optimisation**, aimed by step 1 rather than guessed.
4. **Measure the non-NIF phases.** ~2 h total against 104 min of NIF work means
   roughly a third of the run is unaccounted for and has never been timed.

---

## Results

| date | change | before | after | measured how |
|---|---|---|---|---|
| — | *(baseline)* | — | 103.8 min pack | run log timing notes |
| 07-30 | restrict `_rim_distance` + reach KD query to the push region | **176.3 s** | **108.8 s** | cProfile, same file, same settings |

**One file, −38%.** `minimum_push` computed rim distance and nearest-garment
reach for **every** body vert (~29k), then masked the result down to the push
region (~5k) — so ~82% of that work was discarded. Both are now computed on the
region's verts only. `_rim_distance` fell 44.01 s → 6.08 s (25% → 5.6% of wall).

Exactness was established *before* timing: 6 randomised trials assert the
restricted computation selects an identical vert set. A speedup that changes the
selection would be a behaviour change wearing a performance costume.

Remaining on that file: `_cast` 17.8%, `_pairs` 11.8%, `generate_armor_tri`
8.0%. The ray-cast pair still dominates and is the next target.
