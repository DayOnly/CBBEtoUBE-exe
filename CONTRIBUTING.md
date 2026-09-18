# Contributing to CBBEtoUBE

## Reporting a problem

See **[REPORTING.md](REPORTING.md)** for the full guide. In short: if you want
it fixed, [open an issue](https://github.com/DayOnly/CBBEtoUBE-exe/issues); if
you want an answer, [start a discussion](https://github.com/DayOnly/CBBEtoUBE-exe/discussions).

There are two issue templates, and picking the right one matters because they
ask for different evidence:

- **Bug report** — the converter itself errored, hung, or produced nothing.
- **Conversion problem** — the run finished, but a piece is invisible, clips,
  lost its physics, or an overlay landed wrong.

### Attach the diagnostics zip

In the GUI, choose **Help ▸ Save diagnostics zip**. It writes
`CBBEtoUBE_diagnostics_<timestamp>.zip` next to your output folder, containing:

| File | What it is |
| --- | --- |
| `REPORT.txt` | The problem report, already filled in |
| `gui_log.txt` | What the GUI's log panel showed this session (empty after a restart) |
| `CBBEtoUBE_last_run.log`, `CBBEtoUBE_previous_run.log` | The last run's log, and the one before it — the one that matters if a run died |
| `CBBEtoUBE_last_failures.json`, `CBBEtoUBE_previous_failures.json` | What those runs reported as failures and warnings |
| `conversion_report.json`, `conversion_settings.json` | The output mod's report, and the settings that run used |
| `machine.txt` | RAM, commit limit, CPU threads, and whether the page file is system-managed, a fixed size or off |
| `settings.json` | Your saved conversion settings |
| `exclusions.json` | Any armors you excluded |
| `layout.json` | The discovered MO2 mods root, profile, and game data dirs |
| `preflight.txt` | A fresh **Check setup** run |

A file that no run has written yet is left out. That zip answers most of the
first round of questions on its own. **Look at it
before you attach it** — the layout snapshot contains your MO2 paths and profile
name, and the run log names the mods in your load order.

Other artifacts worth attaching, all written by a normal run:

- `CBBEtoUBE_last_run.log` and `CBBEtoUBE_last_failures.json` — next to the exe.
- `conversion_report.json`, `conversion_summary.txt`, and
  `conversion_report_<mod>.txt` — at the output mod root. The GUI's **Results**
  tab reads the first of these as a health scoreboard.

There is no automatic crash upload and there will not be one: the shipped exe
excludes the `ssl` extension on purpose (OpenSSL 1.1.x is GPL-incompatible and
this project vendors GPL-3.0 PyNifly), so the binary cannot make a network
request at all. Reporting is deliberately manual and file-based.

## Working on the code

### Before you add a pass, read the governing rule

**A pass that exists to clean up after another pass is a design failure, not a
fix.** If your justification starts "an earlier pass leaves…", the change
belongs in the pass that produced the problem, or in what that pass is allowed
to do. `docs/DESIGN.md` opens with the rule, the three ways a repair pass costs
you, and the measurements behind it — including a guard that took its target
pass from 10 defects to 0 while the pack-wide total did not move at all, because
the defect simply relocated to the next pass that caps a weight row.

Name the producer with a measurement before writing anything: convert the
affected piece once per arm with `scripts/convert_one_armor.py`, disabling ONE
pass per arm, and score every arm the same way — or, for the zero-weight class,
pair two builds' output with `scripts/analysis/zero_weight_pair_ab.py`. Score
the WRITTEN NIF, not an intermediate. Run a repeat control at identical settings
first: two identical arms have been seen to differ, so anything smaller than
that gap is noise. `docs/DESIGN.md` carries the full version of this rule and
`docs/TOOL_MAP.md` lists what is tracked and runnable. Attribution by reasoning
has been wrong here more than once.

```bash
git clone https://github.com/DayOnly/CBBEtoUBE-exe
cd CBBEtoUBE-exe
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pytest pyflakes vulture==2.16
git config core.hooksPath .githooks
python -m pytest -q
```

**That `core.hooksPath` line is not optional housekeeping.** This repository is
public, and four kinds of thing must never reach a commit: a file that is
local-only by policy (they name specific mods), an absolute path identifying
your machine or modlist, a personal email address, and the name of a real
third-party asset — in a file's content or in its filename. The hooks in
`.githooks/` refuse a commit that carries any of them.

The suite checks the same rules — but only *after* a commit exists, and by then
it is too late: a file removed from the tip is still fetched by every clone and
still served by SHA. Removing three such files took a full history rewrite, a
force-push to the default branch, and a support ticket for the objects GitHub
kept serving afterwards. The hook turns that into one blocked commit.

A commit can also reach a push without passing through `git commit`: `git rebase`
and `git cherry-pick` run neither hook, and a merge commit runs `commit-msg` but
not `pre-commit`. So `pre-push` checks every commit the push would publish -- its
message, its author and committer addresses, and each file it adds or changes --
and `pre-merge-commit` runs the pre-commit checks on a merge.

Hook config is per-clone and git cannot enable it for you, which is why it is a
setup step rather than something the repo does silently. The rules themselves
live in `scripts/repo_hygiene.py`, imported by both the hooks and the tests so
the two cannot drift.

The fourth rule needs one thing you have to supply: a list of the real asset
names to refuse. That list cannot live in a public repo, so it is read from an
untracked, gitignored `.asset-denylist` at the repo root — one name per line,
`#` comments allowed, `re:` for a raw pattern. **Without that file the check
runs against nothing**, and both the hook and the test say so rather than
reporting a clean tree. Substitute a real name in tracked content the way the
existing fixtures do, and keep a local record of what stands in for what.

Messages written before that hygiene work predate the asset-name rule and have
not been rewritten. Rewriting them would re-hash every commit and every tag, and
those hashes are cited in docs, releases and the project's own notes — so an old
message may still name something the rule now refuses. The rule governs
everything written since, which is what the hooks enforce.

Also set your commit identity to your GitHub noreply address — author email is
public on every commit and the hook will refuse a personal one:

```bash
git config user.email YOUR_USERNAME@users.noreply.github.com
```

`pynifly` is **not on PyPI** — it is vendored in `.pynifly/` (the `pyn` package
plus `NiflyDLL.dll`) and added to `sys.path` at import time. `NiflyDLL.dll` is a
Windows binary, so the suite is Windows-only.

The suite is ~3,400 tests and takes three to five minutes depending on the
machine. Run it before you push; CI runs the same command on `windows-latest`.
Capture the exit code directly -- piping pytest into anything reports the pipe's
status, not pytest's own.

Rebuilding the tracked `dist/` bundle and pushing have an order that the release
gate enforces; [docs/RELEASING.md](docs/RELEASING.md) says what it is and why.

### Pull requests

Branch off `main`, and open the PR against `main`. Keep the diff scoped to one
change — this codebase encodes a lot of hard-won geometry behaviour, and a small
diff is far easier to reason about against a symptom nobody can reproduce
without the exact modlist.

Two things that are easy to get wrong here:

- **Line endings.** Tracked text outside `dist/` and `.pynifly/` is LF-only;
  the pre-commit hook and the test suite refuse a CRLF. `.gitattributes` sets
  `* -text` so git never converts a file behind your back, and the repo expects
  `core.autocrlf=false` (`git config core.autocrlf false`). Write files as bytes
  or with `newline="\n"`: Python's text mode on Windows writes CRLF.
- **Behaviour changes need a test.** Almost every fit-correction pass in
  `docs/DESIGN.md` exists because of a specific in-game failure, and the test suite is
  what stops the next change from reintroducing it.

If your change alters how armor is fitted, say in the PR **what you verified in
game** — which armor, on which body. Structural tests catch structural problems;
they do not catch a mesh that is technically valid and visibly wrong.

## Development measurement tools

Not part of the shipped converter. `scripts/` is not bundled by PyInstaller and
none of these have a GUI setting or CLI surface in the exe — they exist to measure
and verify the converter while working on it.

| script | what it answers |
|---|---|
| `scripts/analysis/verify_skin_exposure.py` | Is a patch of body skin actually visible through the armour? Ray-based, validated with positive/negative controls. |
| `scripts/analysis/survey_motion_clipping.py` | Which shapes let skin show once the body MOVES, pack-wide. |
| `scripts/analysis/collect_fit_dataset.py` | Full-pack census: one JSONL row per shape, ~53 fields (fit, clearance, follow ratio, required follow, exposure curves for chest AND butt, shader numerics, mesh density, weight health). |
| `scripts/analysis/verify_zero_weight_bones.py` | Bones left in a shape with no weight — the `#zeroweight-bone-desync` equip-CTD class. |
| `scripts/analysis/verify_weight_invariant.py` | Rows that do not sum to 1.0. |

**Measure before concluding.** `docs/METRICS.md` records which measurement methods proved
sound and which gave wrong answers, with the controls used to tell them apart. Read it
before trusting a number from any of the above — two metrics in it looked fine and were
not, and each produced a wrong conclusion that shipped into the notes before it was
caught.

Dataset output (`fit_dataset*.jsonl`, `fit_census*.jsonl`) is gitignored: it is a
generated artefact, sometimes large, and should be regenerated rather than committed.
Each dataset's first line is a header record holding the converter version, timestamp,
active flags and the constants in force — two datasets are only comparable if you know
what produced them.

## License

CBBEtoUBE is GPL-3.0 (see [LICENSE](LICENSE)) — it vendors GPL-3.0 PyNifly, so
the whole is distributed under GPL-3.0. Contributions are accepted under the
same license.
