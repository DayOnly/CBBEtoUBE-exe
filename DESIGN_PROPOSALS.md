# CBBEtoUBE — Design proposals (not yet implemented)

Forward-looking designs, grounded in what the 2026-07-09 Fur Cuirass session exposed.
Each is a PROPOSAL: problem → approach → risk → effort → priority. Nothing here is
built. Ordered by value.

---

## P1. Unified source-selection scoring (matures the whole source family)

**Problem.** Source selection is now two stacked heuristics in `build_mesh_index`:
(a) tier-deprioritise BodySlide outputs (`#bodyslide-source`), and (b) within-tier
prefer a canonical-`3BA` body over a bespoke body (`#body-match-source`). They work but
can't express a real preference the pack contains: the Fur Cuirass's *best* source is the
tier-2 `Bodyslide Output - 3BA` — it has BOTH a target-matching body AND the SMP physics —
yet the tier rule demotes it, so we land on a merged, **non-physics** source (fit fixed,
jiggle lost). The `3BA`-name test is also a proxy for "body matches the target"; a
*measured* bust deviation is more precise, and the tier/body-match rules can disagree
(exactly the 3BA-output case).

**Approach.** Replace the tier-sort + within-tier-swap with a per-candidate SCORE; pick
the max per mesh:

    score(c) =  w_body  * -|bundled_bust(c) - UBE_bust|     # measured, render-space; 0 if no bundled body
              + w_phys  *  has_physics(c)                    # SMP/HDT rigging present
              + w_tier  * -bodyslide_output_penalty(c)       # keep the layered-cuirass demotion, but as a weight
              + w_prio  * -mo2_priority_index(c)             # final tiebreak

Weights ordered so a real body mismatch (> ~1.5u) dominates everything (a gappy source
always loses), then physics, then the output penalty, then MO2 priority. `bundled_bust`
reuses the render-space body measurement from this session; `has_physics` = presence of
collision/proxy shapes or HDT-SMP bone chains. A source with no bundled body scores
neutral on `body`, so physics robes are never penalised there.

**What it fixes.** Fur Cuirass → the 3BA-output (matching body + physics) → flush AND
jiggle. The layered dark-leather cuirass → base still wins (the 3BA-output's body is a
*mismatched* preset → loses `body`). Generalises the layered-cuirass + Fur-Cuirass +
physics-preservation into one rule.

**Risk.** Pack-wide re-selection; must measure bundled-body bust for many candidates
(bounded to contested meshes, cached — same cost profile as today's `_body_provenance`);
weight calibration on real data; golden/suite/in-game. Medium-high. Gate behind a flag,
prove byte-identical with it off, A/B the swapped set with `verify_bodymatch.py`.

**Effort.** Medium-high. The measurement + physics-detect helpers mostly exist.

---

## P2. Automated fit-report postflight (replace manual in-game triage)

**Problem.** Fit problems (standoff, cut-in, crinkle, layer-clip) are currently found by
PLAYING the game — slow, incomplete, and the reason for the CLIPPING_LOG triage grind. The
converter already holds the body + armor verts in memory at convert time; it can detect
most of these itself.

**Approach.** After each body-swap armor converts, run a FIT CHECK (reuse the
`armor_clip_diag` metrics: breast/butt covered-standoff, torso penetration, edge crinkle,
inter-layer clip). Accumulate per-armor scores; at run end emit a ranked `FIT_REPORT.md`
next to the output, worst-first, with the failing axis per armor. It's ADVISORY (offline
metrics have missed in-game issues before — see the coarse-mesh cuirass bust-pass revert), never a
gate. Extends the existing `[clip-risk]` telemetry from one line to an aggregated report.

**What it fixes.** Turns "go through armors in-game finding problems" into a ranked list
the converter hands you every run. You work the list instead of hunting.

**Risk.** Low — read-only postflight, output unchanged. Main hazard is false positives, so
it's a report, not a build failure. Thresholds calibrated from this session's numbers
(covered-standoff > 1.5u = gap; penetration < -1.0u = cut-in; crinkle spikiness gate).

**Effort.** Medium. Metrics exist in `scripts/armor_clip_diag.py`; the work is wiring them
into the convert loop + aggregation + calibration.

---

## P3. Move runtime state out of the deploy dir (operational robustness)

**Problem.** The converter writes `CBBEtoUBE_exclusions.json` (user's per-mod skip list),
`_last_failures.json`, `_last_run.log`, and `UNIFIED_COVERAGE` INTO the deploy folder
next to the exe. A redeploy that mirrors the build dir (`robocopy /MIR`) deletes them —
which is exactly how the user's exclusions list was lost this session. User state living
in the artifact directory is fragile by design.

**Approach.** Resolve runtime state to a stable per-user location — `%LOCALAPPDATA%\
CBBEtoUBE\` (fallback: next to the exe if unwritable) — via one `_state_dir()` helper used
by `exclusions.py` and the run-log/failure writers. Migrate an existing deploy-dir file on
first run. Redeploys then never touch user state; deploy scripts can safely mirror.

**What it fixes.** No more clobbered exclusions on redeploy; cleaner deploy story.

**Risk.** Low. One-time migration; keep a read fallback to the old location for a release.

**Effort.** Low.

---

## P4. Softbody band re-conform (the general fix P1 can't always reach)

**Problem.** P1 (source selection) only helps when the pack HAS a better-matching source.
When the only source bundles a mismatched-preset body, the soft-body band is still kept at
its source position and stands off (the Fur Cuirass mechanism). The converter never pulls
a too-loose physics band IN toward the UBE body.

**Approach.** Make `_inflate_cloth_over_bust_butt` BIDIRECTIONAL and source-offset-
preserving: where the band stands off the target more than it hugged its BUNDLED source
body, pull it in to the same clearance; keep pushing OUT on poke. The bundled source body
is the reference (detect it via the body-skin texture, low-poly-tolerant), and the source
offset is the gate that protects draping robes (they hug loosely → keep their drape).

**Risk.** HIGH — edits the CTD-sensitive HDT-SMP path (cf. crash C1) and needs careful
coordinate-space handling (bundled bodies are authored in shifted space). Requires an
in-game CTD test on reconvert. This is why the session chose P1's source route first.

**Effort.** High. Design captured in CLIPPING_LOG entry 6 ("FIX DESIGN").

---

## Not proposed (considered, rejected/deferred)

- **Fine-grained incremental** (invalidate only meshes whose source or relevant code
  changed, vs the current code-mtime-invalidates-everything floor). Big win for iterative
  reconverts but needs per-mesh dependency tracking; high effort, deferred.
- **Apply body-match to `find_winning_nifs`** (the `build_mod` path). Out of the conversion
  path; skip unless `build_mod` is revived.
