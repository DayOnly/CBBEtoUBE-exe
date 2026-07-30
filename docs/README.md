# docs

Reference material for people working **on** the converter. Anything aimed at
people *using* it stays at the repo root: `README.md`, `USING.md`,
`REPORTING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`.

| file | what it is |
|---|---|
| [DESIGN.md](DESIGN.md) | How the pipeline works and, more usefully, **why** each pass exists — including the fit contract and the failures that motivated it. Start here. |
| [METRICS.md](METRICS.md) | Which measurements are trustworthy, which were wrong, and what replaced them. Dated audit log. **Read the checklist at the top before adding a metric.** |
| [DESIGN_JIGGLE.md](DESIGN_JIGGLE.md) | How armour follows breast/butt/belly physics, and why the sliders do not deliver the ratio they name. |

## worklog/

Dated investigation records. They are kept because the *reasoning* is worth more
than the conclusion — several of them exist to stop a wrong idea being
re-derived — but they are **snapshots, not current state**. Where a worklog and
`DESIGN.md` or `METRICS.md` disagree, the latter win.

| file | what it is |
|---|---|
| [worklog/PLAN_PASS_CONSOLIDATION.md](worklog/PLAN_PASS_CONSOLIDATION.md) | Measured pass-interaction study and a six-step plan. Step 1 done, step 3 partly; carries a status note on what drifted. |
| [worklog/LESSONS_2026_07_27.md](worklog/LESSONS_2026_07_27.md) | What the July clipping work taught, mostly about measurement discipline. |
| [worklog/HIDE_ARMOR_ZERO_CLIP.md](worklog/HIDE_ARMOR_ZERO_CLIP.md) | Working document for one armour that resisted diagnosis for three months. |

## Not in the repository

Some working notes are gitignored because they name specific third-party mods
and modlists, which the tracked-content policy forbids (enforced by
`tests/test_public_repo_hygiene.py`, not merely trusted):
`CLIPPING_LOG.md`, `DESIGN_JIGGLE_PLAN.md`, `ARMOR_WORKLIST.md`, and the
per-machine audit reports. If a doc here refers to one of those by name, it is
referring to a file that exists only on the author's machine.
