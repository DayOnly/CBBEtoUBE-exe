# docs

Reference material for people working **on** the converter. Anything aimed at
people *using* it stays at the repo root: `README.md`, `USING.md`,
`REPORTING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`.

| file | what it is |
|---|---|
| [PASS_MAP.md](PASS_MAP.md) | **GENERATED.** Every module-level pass call in `nif_convert.py`, in source order, per convert path, with the flag/knob guards wrapping each one. Regenerate with `python scripts/pass_map.py`; `tests/test_pass_map.py` fails if it goes stale. Indexes PASSES only — it names no tools. |
| [TOOL_MAP.md](TOOL_MAP.md) | **GENERATED — read the file for the count, do not quote one from here.** The tracked measurement tools, grouped by WHAT THEY READ, each with its gate exit codes and whether it asserts a population floor. **Check here before hand-rolling a probe.** That rule used to point at PASS_MAP, which indexes no tools — so 38 of the 54 analysis tools were named in no document at all. Regenerate with `python scripts/tool_map.py`. |
| [PIPELINE.md](PIPELINE.md) | **What runs, in what order, and what each step can and cannot reach** — plus the rules for changing a pass and the dead ends not to retry. Orderings are extracted from source, not remembered. Start here if you are about to edit a pass. |
| [DESIGN.md](DESIGN.md) | How the pipeline works and, more usefully, **why** each pass exists — including the fit contract and the failures that motivated it. Start here if you are asking why something is the way it is. |
| [METRICS.md](METRICS.md) | Which measurements are trustworthy, which were wrong, and what replaced them. Dated audit log. **Read the checklist at the top before adding a metric.** |
| [DESIGN_JIGGLE.md](DESIGN_JIGGLE.md) | How armour follows breast/butt/belly physics, and why the sliders do not deliver the ratio they name. |

## Working notes

Dated investigation records -- measured studies, open plans, and the reasoning
behind decisions that a summary would lose -- are kept on the **`testing`**
branch under `docs/worklog/`, not here.

They are deliberately not on `main`: they are snapshots of work in progress
rather than a description of the shipped tool, and where a worklog disagrees
with `DESIGN.md` or `METRICS.md`, those win. If you are tracing why a pass
exists or why an approach was abandoned, look there.

## Not in the repository

Some working notes are gitignored because they name specific third-party mods
and modlists, which the tracked-content policy forbids (enforced by
`tests/test_public_repo_hygiene.py`, not merely trusted):
`CLIPPING_LOG.md`, `DESIGN_JIGGLE_PLAN.md`, `ARMOR_WORKLIST.md`, and the
per-machine audit reports. If a doc here refers to one of those by name, it is
referring to a file that exists only on the author's machine.
