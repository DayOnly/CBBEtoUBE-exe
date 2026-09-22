# docs

Reference material for people working **on** the converter. Anything aimed at
people *using* it stays at the repo root: `README.md`, `USING.md`,
`REPORTING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `SECURITY.md`. `RELEASING.md` lives here: it is for maintainers.

| file | what it is |
|---|---|
| [PASS_MAP.md](PASS_MAP.md) | **GENERATED.** Every module-level pass call in `nif_convert.py`, in source order, per convert path, with the flag/knob guards wrapping each one. Regenerate with `python scripts/pass_map.py`; `tests/test_pass_map.py` fails if it goes stale. Indexes PASSES only — it names no tools. |
| [TOOL_MAP.md](TOOL_MAP.md) | **GENERATED — read the file for the count, do not quote one from here.** The tracked measurement tools, grouped by WHAT THEY READ, each with its gate exit codes and whether it asserts a population floor. **Check here before hand-rolling a probe.** That rule used to point at PASS_MAP, which indexes no tools — so 38 of the 54 analysis tools were named in no document at all. Regenerate with `python scripts/tool_map.py`. |
| [PIPELINE.md](PIPELINE.md) | **What runs, in what order, and what each step can and cannot reach** — plus the rules for changing a pass and the dead ends not to retry. Orderings are extracted from source, not remembered. Start here if you are about to edit a pass. |
| [DESIGN.md](DESIGN.md) | How the pipeline works and, more usefully, **why** each pass exists — including the fit contract and the failures that motivated it. Start here if you are asking why something is the way it is. |
| [METRICS.md](METRICS.md) | Which measurements are trustworthy, which were wrong, and what replaced them. Dated audit log. **Read the checklist at the top before adding a metric** — and the bind-pose blindness note: bind clipping is 0.000% at every stage of the chain on a piece running 2-7% under a body preset, so most columns here cannot see a preset-dependent defect at all. |
| [DESIGN_JIGGLE.md](DESIGN_JIGGLE.md) | How armour follows breast/butt/belly physics, and why the sliders do not deliver the ratio they name. |
| [RELEASING.md](RELEASING.md) | How a rebuild and a push have to be ordered and why, the release gate's clauses, the mutation gate and the golden check. Read before rebuilding the tracked `dist/` bundle or tagging a release. |
| [WARNINGS.md](WARNINGS.md) | **GENERATED.** Every warning the converter can print, what it means and what to do. Regenerate with `python scripts/warning_surface.py`; `tests/test_user_warnings.py` fails if it goes stale. |

## worklog/

Dated investigation records. They are kept because the *reasoning* is worth more
than the conclusion — several of them exist to stop a wrong idea being
re-derived — but they are **snapshots, not current state**. Where a worklog and
`DESIGN.md` or `METRICS.md` disagree, the latter win.

This table indexes ALL of them. It listed 9 of 33 until 2026-09-09, which left
the two largest documents in the repo reachable only by knowing they were there.

### Current cycle — bust fitment and the 1.4 recipe

| file | what it is |
|---|---|
| [worklog/2026-09-09_NEXT_RECONVERT.md](worklog/2026-09-09_NEXT_RECONVERT.md) | **Start here before a reconvert.** The recipe as measured, what each setting buys and costs, the control figures to re-score against afterwards, and the three levers not to turn on. Its author-relative figures (bust gap vs author) were read through the old reference-body lookup, not the zeroed build the game loads (and that file was replaced on 2026-09-15, so what they measured is unknown) — re-baseline them with the current harness rather than re-score against them. |
| [worklog/2026-09-09_BUST_FITMENT.md](worklog/2026-09-09_BUST_FITMENT.md) | The session behind that recipe: where a bust push goes to die, the morphed-clip gate rows, and four defects found in the gate change made the same day. Its bust-gap-vs-author figures predate the zeroed reference body: do not compare them with current numbers. |
| [worklog/2026-09-02_PHASE1_ANTIPOKE_POPULATION.md](worklog/2026-09-02_PHASE1_ANTIPOKE_POPULATION.md) | `#phase1-antipoke` population A/B — and the measurement bug that voided the first one. |
| [worklog/2026-09-04_NIPPLE_OUTLINE_THROUGH_PLATE.md](worklog/2026-09-04_NIPPLE_OUTLINE_THROUGH_PLATE.md) | Nipple outlines through plate, traced end to end. The fix belongs on the inner layer, not the plate. |
| [worklog/2026-09-05_FIELD_SCREEN_PHYSICAL.md](worklog/2026-09-05_FIELD_SCREEN_PHYSICAL.md) | The clearance solve measures its feather in EDGES, not distance — why a feather larger than the square root of the area does nothing. |

### Audits

| file | what it is |
|---|---|
| [worklog/2026-09-01_AUDIT.md](worklog/2026-09-01_AUDIT.md) | **The largest document here.** 121 findings from 12 parallel read-only passes over the tree, each carrying the A/B recipe that must run before it is acted on. |
| [worklog/2026-09-02_AUDIT_STEP3_STEP4.md](worklog/2026-09-02_AUDIT_STEP3_STEP4.md) | Its steps 3 and 4: docs currency, and which guards survive a module split. |
| [worklog/2026-08-02_AUDIT_MAIN_HISTORY.md](worklog/2026-08-02_AUDIT_MAIN_HISTORY.md) | Audit of what `main`'s history carries, and the history-rewrite that removed local-only files. |
| [worklog/2026-08-02_AUDIT_MAIN_LAYOUT.md](worklog/2026-08-02_AUDIT_MAIN_LAYOUT.md) | Audit of the repository layout on `main`. |
| [worklog/2026-08-02_AUDIT_GH_TESTING.md](worklog/2026-08-02_AUDIT_GH_TESTING.md) | Audit of the GitHub-side testing setup (CI, templates). |

### Pass consolidation and the two convert paths

| file | what it is |
|---|---|
| [worklog/2026-09-06_CONSOLIDATION_PLAN.md](worklog/2026-09-06_CONSOLIDATION_PLAN.md) | The execution kit. **Read §5–6 first**: several items in §2 were refuted or closed by measuring them. Its bust-gap-vs-author figures predate the zeroed reference body. |
| [worklog/2026-07-27_PLAN_PASS_CONSOLIDATION.md](worklog/2026-07-27_PLAN_PASS_CONSOLIDATION.md) | The earlier pass-interaction study and six-step plan. Step 1 done, step 3 partly; carries a status note on what drifted. |
| [worklog/2026-09-03_UNIFY_STEP0_AND_DEDUP.md](worklog/2026-09-03_UNIFY_STEP0_AND_DEDUP.md) | Two-path unification step (0), and the mechanical de-duplication — including why the two hooks were not duplicates. |
| [worklog/2026-09-02_F010_COPY_PATH_VERDICT.md](worklog/2026-09-02_F010_COPY_PATH_VERDICT.md) | F010, copy-path early panel rigidity: a real win that still ships OFF, because it strands zero-weight bones. |
| [worklog/2026-09-02_BUTT_COPY_PATH_TROUSERS.md](worklog/2026-09-02_BUTT_COPY_PATH_TROUSERS.md) | Copy-path trousers buried 59% of the butt band and the author's did not. Reproduced, measured, attributed. |

### Fit, clearance and distortion

| file | what it is |
|---|---|
| [worklog/2026-07-28_HIDE_ARMOR_ZERO_CLIP.md](worklog/2026-07-28_HIDE_ARMOR_ZERO_CLIP.md) | Working document for one armour that resisted diagnosis for three months. |
| [worklog/2026-09-02_F011_TRUE_TARGET.md](worklog/2026-09-02_F011_TRUE_TARGET.md) | F011(b): re-measuring `#groove-authored-cap` against a target that is not zero. |
| [worklog/2026-09-03_F011_BLEND_TIGHT.md](worklog/2026-09-03_F011_BLEND_TIGHT.md) | F011(c): re-validating `CONFORM_BLEND_TIGHT` against that same real target. It survives at 0.30. |
| [worklog/2026-09-03_F011_COLLIDER_CONTRACT.md](worklog/2026-09-03_F011_COLLIDER_CONTRACT.md) | F011: measuring the collider blocker by CONTRACT rather than by size. |
| [worklog/2026-09-02_F011_COLLIDER_DELTA.md](worklog/2026-09-02_F011_COLLIDER_DELTA.md) | Why that collider "regression" is decimator noise — never gate on a proxy vertex count. |
| [worklog/2026-09-03_F068_WARP_FIELD_WITH_TRUE_TARGET.md](worklog/2026-09-03_F068_WARP_FIELD_WITH_TRUE_TARGET.md) | F068: the warp field's rejection WAS confounded, and the combination is a trade rather than a win. |
| [worklog/2026-07-27_LESSONS.md](worklog/2026-07-27_LESSONS.md) | What the July clipping work taught, mostly about measurement discipline. Its §4 ("skin passes cannot reach the worst clipping") was true of skin passes and is no longer the whole picture — the chain rest-pose lift reaches part of that population. |

### Physics, colliders and skinning

| file | what it is |
|---|---|
| [worklog/2026-08-10_BUTT_CLIP_CHAIN_REST.md](worklog/2026-08-10_BUTT_CLIP_CHAIN_REST.md) | Why a skirt clipped the buttocks for months, the two fixes the numbers killed first, and the cross-chain constraint caveat on shifting chain roots. |
| [worklog/2026-09-04_SMP_DRESS_PHYSICS.md](worklog/2026-09-04_SMP_DRESS_PHYSICS.md) | Physics of four mage dresses: three clean, one real defect. |
| [worklog/2026-09-04_SMP_SCAN_AND_THE_EXTREMITY_GAP.md](worklog/2026-09-04_SMP_SCAN_AND_THE_EXTREMITY_GAP.md) | Full scan of an HDT-SMP conversion, and an extremity fix that FAILED. |
| [worklog/2026-09-06_TRI_VARIANT_COLLISION.md](worklog/2026-09-06_TRI_VARIANT_COLLISION.md) | The TRI out-of-bounds entries: two defects, and a metric that hid both. |
| [worklog/2026-09-04_WEIGHT_PAIR_PARITY_AND_BODYTRI_CARRIER.md](worklog/2026-09-04_WEIGHT_PAIR_PARITY_AND_BODYTRI_CARRIER.md) | Weight-pair parity, and the BODYTRI carrier convention that collapses authored tags to one. |
| [worklog/2026-09-02_ZEROWEIGHT_BONE_PRODUCER.md](worklog/2026-09-02_ZEROWEIGHT_BONE_PRODUCER.md) | The `#zeroweight-bone-desync` producer: postwrite weight writes are uncapped. |
| [worklog/2026-09-02_ZEROWEIGHT_RESIDUAL_18.md](worklog/2026-09-02_ZEROWEIGHT_RESIDUAL_18.md) | The 18 zero-weight bones that survived the fix, and what they have in common. |
| [worklog/2026-09-06_LAST_CARRIER_HOLD.md](worklog/2026-09-06_LAST_CARRIER_HOLD.md) | The residual zero-weight bones: the CAP is the eviction, not something missing from it. |

### Determinism, speed and the GUI

| file | what it is |
|---|---|
| [worklog/2026-09-06_PAIR_UNIT_DISPATCH.md](worklog/2026-09-06_PAIR_UNIT_DISPATCH.md) | `#pair-unit-dispatch` — the weight-tail nondeterminism was a `_0`/`_1` pair race. |
| [worklog/2026-07-30_OPTIMIZATION_LOG.md](worklog/2026-07-30_OPTIMIZATION_LOG.md) | Conversion speed: baseline measurements, the ceiling on each idea, and the hypotheses that turned out wrong. **Open.** |
| [worklog/2026-08-01_PLAN_GUI_ARMOR_TAB.md](worklog/2026-08-01_PLAN_GUI_ARMOR_TAB.md) | Plan for the GUI Armor tab — which toggles a user can actually reach, and why that matters. |

## Not in the repository

Some working notes are gitignored because they name specific third-party mods
and modlists, which the tracked-content policy forbids (enforced by
`tests/test_public_repo_hygiene.py`, not merely trusted):
`CLIPPING_LOG.md`, `DESIGN_JIGGLE_PLAN.md`, `ARMOR_WORKLIST.md`, and the
per-machine audit reports. If a doc here refers to one of those by name, it is
referring to a file that exists only on the author's machine.
