# docs

Reference material for people working **on** the converter. Anything aimed at
people *using* it stays at the repo root: `README.md`, `USING.md`,
`REPORTING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`.

| file | what it is |
|---|---|
| [PIPELINE.md](PIPELINE.md) | **What runs, in what order, and what each step can and cannot reach** — plus the rules for changing a pass and the dead ends not to retry. Orderings are extracted from source, not remembered. Start here if you are about to edit a pass. |
| [DESIGN.md](DESIGN.md) | How the pipeline works and, more usefully, **why** each pass exists — including the fit contract and the failures that motivated it. Start here if you are asking why something is the way it is. |
| [METRICS.md](METRICS.md) | Which measurements are trustworthy, which were wrong, and what replaced them. Dated audit log. **Read the checklist at the top before adding a metric.** |
| [DESIGN_JIGGLE.md](DESIGN_JIGGLE.md) | How armour follows breast/butt/belly physics, and why the sliders do not deliver the ratio they name. |

## worklog/

<!-- MERGE NOTE -- DO NOT CARRY THIS SECTION TO `main`.
     `main` deliberately ships WITHOUT docs/worklog/ (see the merge commit
     "Merge testing into main (1.2.6), without the development worklog"), and
     carries its own "## Working notes" section here instead, which points at
     this branch in prose and links nothing. Taking the table below onto main
     would publish one broken link per row. Keep main's version of THIS section
     when merging; everything else in this file merges normally. -->

Dated investigation records. They are kept because the *reasoning* is worth more
than the conclusion — several of them exist to stop a wrong idea being
re-derived — but they are **snapshots, not current state**. Where a worklog and
`DESIGN.md` or `METRICS.md` disagree, the latter win.

| file | what it is |
|---|---|
| [worklog/BUTT_CLIP_CHAIN_REST.md](worklog/BUTT_CLIP_CHAIN_REST.md) | Why a skirt clipped the buttocks for months, the two fixes the numbers killed first, and the cross-chain constraint caveat on shifting chain roots. |
| [worklog/PLAN_PASS_CONSOLIDATION.md](worklog/PLAN_PASS_CONSOLIDATION.md) | Measured pass-interaction study and a six-step plan. Step 1 done, step 3 partly; carries a status note on what drifted. |
| [worklog/LESSONS_2026_07_27.md](worklog/LESSONS_2026_07_27.md) | What the July clipping work taught, mostly about measurement discipline. Its §4 ("skin passes cannot reach the worst clipping") was true of skin passes and is no longer the whole picture — the chain rest-pose lift reaches part of that population. |
| [worklog/HIDE_ARMOR_ZERO_CLIP.md](worklog/HIDE_ARMOR_ZERO_CLIP.md) | Working document for one armour that resisted diagnosis for three months. |
| [worklog/OPTIMIZATION_LOG.md](worklog/OPTIMIZATION_LOG.md) | Conversion speed: baseline measurements, the ceiling on each idea, and the hypotheses that turned out wrong. **Open.** |
| [worklog/PLAN_GUI_ARMOR_TAB.md](worklog/PLAN_GUI_ARMOR_TAB.md) | Plan for the GUI Armor tab — which toggles a user can actually reach, and why that matters. |
| [worklog/AUDIT_MAIN_HISTORY.md](worklog/AUDIT_MAIN_HISTORY.md) | Audit of what `main`'s history carries, and the history-rewrite that removed local-only files. |
| [worklog/AUDIT_MAIN_LAYOUT.md](worklog/AUDIT_MAIN_LAYOUT.md) | Audit of the repository layout on `main`. |
| [worklog/AUDIT_GH_TESTING.md](worklog/AUDIT_GH_TESTING.md) | Audit of the GitHub-side testing setup (CI, templates). |

## Not in the repository

Some working notes are gitignored because they name specific third-party mods
and modlists, which the tracked-content policy forbids (enforced by
`tests/test_public_repo_hygiene.py`, not merely trusted):
`CLIPPING_LOG.md`, `DESIGN_JIGGLE_PLAN.md`, `ARMOR_WORKLIST.md`, and the
per-machine audit reports. If a doc here refers to one of those by name, it is
referring to a file that exists only on the author's machine.
