# P1 — Unified source-selection scoring (detailed design)

Replace the two stacked source heuristics (tier deprioritisation + within-tier
body-match) with ONE measured score per candidate, so the converter picks the source
that both fits UBE AND keeps intended features (physics). Grounded in the 2026-07-09
Fur Cuirass + the 2026-07-08 New Leather cases.

Status: DESIGN ONLY. Higher risk than P2 (pack-wide re-selection). Implement + calibrate
after the reconvert, behind a flag, with New-Leather + Fur-Cuirass as golden cases.

---

## 1. Why the current two heuristics aren't enough

`build_mesh_index` today does: (a) tier-sort — BodySlide outputs lose to base
(`#bodyslide-source`, fixed New Leather); (b) within-tier — a canonical-`3BA`-body source
beats a bespoke-body source (`#body-match-source`, fixed Fur Cuirass). Both are PROXIES
and they can't express the pack's actual best option:

- The Fur Cuirass's ideal source is the tier-2 `Bodyslide Output - 3BA`: it has a body
  that matches UBE (+5.70u ≈ +5.74u) AND the SMP physics. Tier demotes it, so we land on
  a merged, non-physics source — flush but **no jiggle**. Neither heuristic can say
  "this output is actually the best because its body matches and it has physics."
- `3BA`-name is a proxy for "body matches target"; a MEASURED deviation is exact and
  generalises (HIMBO/slim/large presets, non-`3BA` bodies).
- Tier and body-match can DISAGREE (the 3BA-output case): tier says demote, body says keep.

## 2. The scoring model

For each mesh, gather ALL enabled providers (not first-wins), score each, pick the max:

    score(c) =  W_BODY * body_term(c)      # bundled body's match to the UBE target
              + W_PHYS * phys_term(c)       # intended physics preserved
              + W_OUT  * out_term(c)        # residual BodySlide-output penalty
              + W_PRIO * prio_term(c)       # MO2-priority tiebreak

- **body_term** = `-mean_deviation(bundled_body, UBE_ref)` — see §3. `0` when the source
  bundles no body (a physics robe: cloth + collision only) → it's never penalised on body,
  so physics survives. THIS is the term that must carry the load (below).
- **phys_term** = `1` if the candidate ships SMP/HDT physics (collision/proxy shapes or
  HDT-SMP bone chains — reuse `_hdt_collider_shape_names` / `_shape_has_hdt_smp_rigging`),
  else `0`.
- **out_term** = `0` non-output, `-1` UBE output, `-2` other-body output (today's tiers,
  as a residual weight — a tiebreak toward base when bodies match equally, NOT the primary
  signal).
- **prio_term** = `-mo2_index` (higher MO2 priority wins ties).

## 3. The crux: body_term must be FULL-BODY deviation, not just bust

The tier penalty is really "a BodySlide output may bake a body that MISMATCHES the target."
`body_term` should MEASURE exactly that — and it must capture more than bust, or it will
REGRESS New Leather. New Leather's clipping was layers SQUASHED by a preset body; if that
preset happened to share UBE's bust, a bust-only `body_term` would score it fine and let
the bad output win again. So:

    body_term(c) = -mean over sampled body regions (torso z70-100, hips z66-82,
                   thighs z45-66, bust z100-110) of nearest-vertex distance between the
                   candidate's bundled body and the UBE reference body, both render-space.

A preset that squashes/inflates the body deviates across regions → penalised (New Leather
output loses to base). A preset that matches UBE (Fur Cuirass 3BA-output) → ~0 deviation →
not penalised, and its physics wins it. This makes `body_term` SUBSUME the tier intent;
`out_term` drops to a small residual. The UBE reference is the same body ref the convert
uses (`_find_ube_body_ref` / `_cached_ube_body_verts`), so "matches the target" is literal.

## 4. Calibration — two hard golden cases the weights MUST satisfy

Tune `W_*` on real data so BOTH hold simultaneously:

1. **New Leather** (`narmor/leathersuitn/dcuirass`) → **base** ("New Leather Armor").
   The `Bodyslide Output - 3BA` bakes a mismatched preset → high `body_term` penalty →
   base wins even though the output may have physics. Guards the 2026-07-08 fix.
2. **Fur Cuirass** (`armor/bandit/body1f`) → **`Bodyslide Output - 3BA`** (matching body
   +5.70u ≈ UBE + physics), NOT the merged non-physics Redone source. Recovers jiggle.

Plus: the whole 42-mesh body-match set (must not regress into gaps), physics robes (no
body → keep their SMP source), and a full-pack A/B (scoring on vs current) reviewed for
surprises. Initial weight sketch to calibrate from: `W_BODY=1.0` per unit deviation,
`W_PHYS≈1.5`, `W_OUT≈1.0`, `W_PRIO≈0.01`. Meaning: a >1.5u body mismatch dominates; among
well-matched bodies, physics beats a one-tier output penalty; priority only breaks ties.
The exact values come from making the two goldens pass with margin.

## 5. Implementation

- `build_mesh_index(..., target_body_ref: Path | None = None)`. When `None` (or
  `CBBE2UBE_SOURCE_SCORING` off), fall back to TODAY's tier + body-match path unchanged —
  so existing callers, tests, and the two byte-identical goldens are untouched until the
  flag flips. When provided, use scoring.
- Collect candidates per rel only where there's a genuine contest (>1 provider); a
  single-provider mesh skips scoring entirely (cost bound — same as `_body_provenance`
  today: open + measure only contested sources, cache per path).
- `_score_candidate(path, ube_ref_verts)` → float. Reuses: render-space body extraction
  (this session), region-sampled deviation (new, small), physics detection (existing).
- Pass `target_body_ref` from `auto_convert` (`_find_ube_body_ref()` is already resolved
  there and threaded as `ube_body_ref_path`).
- Weights live in named constants with env overrides for calibration
  (`CBBE2UBE_SRCSCORE_W_BODY`, …).

Supersedes the P0 body-match + tier rules when on; they remain the fallback. Once P1 is
proven in-game, retire the two heuristics (as the SkyPatcher migration retired the ARMO
machinery).

## 6. Test plan

- Unit (synthetic candidates, monkeypatched open): matching-body+physics beats
  mismatched-body; no-body physics source kept over a canonical source; a mismatched-body
  output loses to a matched-body base; priority breaks exact ties.
- Golden calibration tests: New Leather → base, Fur → 3BA-output, on synthetic mimics with
  the measured deviations baked in, so the WEIGHTS are regression-locked.
- Backward-compat: `target_body_ref=None` reproduces today's selection byte-for-byte
  (reuse the existing tier + body-match tests unchanged).
- Full-pack A/B script (extend `verify_bodymatch.py`): list every mesh whose source
  changes scoring-on vs current, for human review before committing.

## 7. Risks

- **New-Leather regression** if `body_term` under-measures the squash — mitigated by the
  full-body (multi-region) deviation and the golden calibration test. HIGHEST risk.
- **Perf**: deviation needs the candidate body + a KD-tree vs the UBE ref per contested
  source. Bounded to contests, cached; acceptable (same profile as today's provenance
  opens). Measure on the real pack; if hot, sample fewer region verts.
- **Weight brittleness**: empirical weights can mis-rank an unseen pack. Keep env
  overrides; ship the A/B script so a user can inspect the diff; default the flag OFF until
  proven, then ON.
- **Pack-wide churn**: a full reconvert re-sources more than 42 meshes. Stage it: flag off
  → prove goldens identical → flag on in a branch → A/B review → in-game spot-check the
  Fur Cuirass (jiggle back?) + New Leather (still clean?) before making it default.

## 8. Staged implementation (post-reconvert)

1. `_score_candidate` + region-deviation helper + physics detection wiring. Unit tests.
2. `build_mesh_index` candidate-collection + scoring path behind `target_body_ref`/flag;
   fallback unchanged. Backward-compat + golden-identical proven.
3. Calibrate weights against New Leather + Fur goldens; lock with regression tests.
4. Full-pack A/B review; in-game spot-check; flip default; later retire P0/tier heuristics.

Effort: medium-high. The measurement pieces exist; the new work is the region-deviation
metric, candidate scoring, and — the real cost — weight calibration + validation.
