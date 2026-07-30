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

| 07-30 | ray-line cull via the Lagrange identity (no cross, no sqrt) | 108.8 s | **94.4 s** | cProfile, same file |

**Cumulative on that file: 176.3 s → 94.4 s (−46%).**

The cull tested `|w x d| / |d| <= trad`, computing a cross product and two norms
over EVERY candidate pair before rejecting 96% of them. `|w x d|^2 ==
|w|^2|d|^2 - (w.d)^2` gives the identical test from three dot products over
`(n,)` arrays — no `(n,3)` temporaries, no sqrt. `cross` (8.9 s) and most of
`norm` (5.8 s) left the profile entirely. Verified hit-for-hit against the
unculled reference across **9,042,432 candidate pairs**.

### Chunk size: a measured non-result worth keeping

`RAY_CHUNK` was picked at 512 without measuring. Timing 256 → unchunked on a
98k-triangle garment: **~2% spread in speed**, but peak candidate pairs swing
**2.8 M → 92 M**. Chunking is therefore essentially free and buys a 33× memory
margin; the `MemoryError` (36 M pairs) cannot recur at any of these sizes. No
change made — the default was already right, and now that is known rather than
assumed.

### What is left, and why it needs a different kind of fix

`_cast` 22.1% and `_pairs` 14.4% still lead, and one full-band cast on that
garment is **~11 s**: 5,249 rays × 98k triangles → 92 M candidate pairs before
the cull. That is inherent to a ball query of radius `tmax` around every ray
origin, so further micro-optimisation has little left to give.

The remaining win is **not doing the work twice**. Per shape the same garment is
cast against overlapping ray sets by `record_standoff` (band mask, tmax 12), the
`bust` torso band (front-slab mask, tmax 12 — largely the same skin), the chain
contract (tmax 5, twice) and `minimum_push` (tmax 5). Deduplicating the
overlapping measurements, and sharing one tester per shape, is worth more than
anything left inside the cast. Not attempted yet.

Also now visible: `generate_armor_tri` at 9.5% — BODYTRI morph generation, a
different subsystem, untouched by any of this.
