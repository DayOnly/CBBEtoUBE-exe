# P2 — Automated fit-report postflight (detailed design)

Turn "play the game, find fit problems, log them" into a ranked problem list the
converter emits every run. Advisory, not a gate. Grounded in the 2026-07-09 metrics.

Status: DESIGN ONLY. Implement after the in-flight reconvert (avoids CPU contention).

---

## 1. Data flow (why it's cheap and parallel-safe)

The converter fans meshes out to worker processes; each returns a `ConvertResult`
(`nif_convert.py:2078`) that the main process aggregates (it already buckets
`dropped_shapes`). So fit metrics ride the SAME channel — no shared state, no re-read:

1. Add one field to `ConvertResult`:
   ```python
   fit_metrics: dict = field(default_factory=dict)   # {} unless a body-swap torso piece
   ```
2. The worker computes metrics from the **in-memory final verts** it just produced
   (BaseShape = injected UBE body; the armor shapes' post-pass override verts) and sets
   `result.fit_metrics`. No extra disk read.
3. The main process, after the convert loop, collects every non-empty `fit_metrics`,
   ranks, writes `FIT_REPORT.md` next to the output, and prints a one-line summary.

Cost: a few KD-tree queries per body-swap armor on verts already in RAM — negligible vs
the convert. Default-ON is affordable (unlike re-reading 3000 NIFs). `--no-fit-report`
opts out; `CBBE2UBE_NO_FIT_REPORT=1` mirrors it.

**Where the worker computes it:** at the end of `convert_nif_phase2`, once every shape's
final override verts exist and BaseShape is injected — the same place `[clip-risk]`
telemetry already runs (`nif_convert.py` ~11459). Guard: only when there's a `BaseShape`
and `biped_slots & (SLOT32|SLOT49)` (a body-swap torso piece); else leave `fit_metrics={}`.

---

## 2. Metrics (baseline-free, calibrated this session)

All measured in RENDER space (apply each shape's transform — bundled bodies are authored
shifted). Body = `BaseShape`; armor = non-BaseShape, non-collision, non-virtualground
shapes. Per armor emit:

| key | definition | flag threshold | class |
|-----|-----------|----------------|-------|
| `breast_gap` | covered-mean gap, body breast band z100-108 \|x\|<12 y>0, → nearest armor along body normal (only where armor within 6u) | `> 1.5u` | over-inflation / bundled-body mismatch |
| `butt_gap` | same, butt band z66-82 back (y<0) | `> 1.5u` | under-coverage (Hide/Imperial class) |
| `cut_in` | deepest armor vert INSIDE body over torso z70-115 (signed dist < 0, nearest body within 8u) | `< -1.0u` | armor cutting into body |
| `crinkle` | max per-edge displacement-jump / mean-moved (spikiness) on armor edges | `> 8` (advisory) | boundary crinkle |

Numbers are calibrated: Fur Cuirass `breast_gap` 2.12 (bad) → 1.16 (fixed); a −1.6u
greaves poke was the worst `cut_in` seen; crinkle spikiness ~3.7 benign vs ~20 real.
`crinkle` is the noisiest — emit it but mark advisory (offline crinkle has both missed and
false-flagged before; see the museum bust-pass revert).

Reuse the math already written and calibrated in `scripts/armor_clip_diag.py`
(`crinkle`, `off_target`, `thin_clearance`) — refactor its core measures into a small
`fit_metrics(body_v, body_n, armor_shapes)` helper importable by BOTH the converter and
the script, so there's one source of truth.

---

## 3. Report format

`FIT_REPORT.md` in the output mod root (next to the meshes), overwritten each run:

```
# Fit report — <pass timestamp from args, not Date.now>
482 body-swap torso pieces checked · 37 flagged

## Flagged (worst-first)
| worst | armor | breast_gap | butt_gap | cut_in | crinkle |
|------:|-------|-----------:|---------:|-------:|--------:|
|  2.4  | armor/imperial/f/cuirassheavy_1.nif |  +0.3 | +2.4 | -0.2 | 3.1 |
|  ...  |
```

`worst` = the single most-severe axis (normalised so gaps, cut-in, crinkle are
comparable), used for the ranking. Console at run end:

```
fit report: 37/482 torso pieces flagged (worst breast_gap +2.4u: <armor>) -> FIT_REPORT.md
```

The report supersedes the manual CLIPPING_LOG triage table for the "find problems" step —
the log keeps its role for DIAGNOSED root causes + fixes.

---

## 4. False-positive discipline

The metrics are geometry-relative (armor-vs-its-own-body), so they're far less prone to
the "skimpy armor exposes body" false positives that plagued the exposure triage. Still:

- **1st-person / actor meshes**: skip by path (`1stperson`, `/m/` male, `actors/`) — same
  filter the triage learned to need.
- **By-design open armor** (a bra genuinely has no butt coverage): `butt_gap` there is
  real "no armor" not "lifted armor". Distinguish by REQUIRING armor within 6u of the body
  vert before counting a gap (already in the metric) — no armor nearby → not counted, so an
  uncovered butt doesn't read as a gap. This is the key guard.
- **Advisory framing**: the report says "candidates to check", never "these are broken".
  It complements, not replaces, an eyeball — offline metrics have missed real in-game
  issues (crinkles) and flagged cosmetic ones (distance z-fight). Document prominently.

---

## 5. Test plan

- Unit: `fit_metrics()` on a synthetic body + a) a flush band (gaps≈0, cut_in≈0),
  b) a lifted band (breast_gap high), c) a band pushed inside (cut_in negative),
  d) a crinkled edge (spikiness high). Assert each axis fires on its own case only.
- Plumbing: a `ConvertResult` with `fit_metrics` set aggregates into the report; empty
  `fit_metrics` (armor-only piece) is skipped.
- Report writer: deterministic ordering, timestamp passed in (no `Date.now`), atomic write.
- Regression: default-on adds `fit_metrics` but does NOT change output NIFs → the two byte-
  identical goldens stay identical (fit-check is read-only on the verts).

---

## 6. Staged implementation (post-reconvert)

1. Extract `fit_metrics()` core into a shared module (`src/fit_metrics.py`), refactor
   `armor_clip_diag.py` to import it (no behaviour change; keep its CLI). +unit tests.
2. Add `ConvertResult.fit_metrics`; populate it at the phase-2 finalisation point behind
   the body-swap guard. Prove goldens byte-identical.
3. Aggregate + write `FIT_REPORT.md` + console summary in `auto_convert` after the loop.
   `--no-fit-report` / `CBBE2UBE_NO_FIT_REPORT`.
4. Calibrate thresholds against the first post-fix reconvert (compare the report's flags to
   what we already know is bad/good), tune, lock in.

Effort: medium. Risk: low (read-only on verts; advisory output; goldens protected).
