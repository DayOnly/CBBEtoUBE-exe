# Audit: GitHub testing (2026-08-02)

Scope: the CI setup at `.github/workflows/tests.yml`, what it does and does not
cover, and the repository state it runs against. Written after pushing the
Armor-tab work to `testing`.

**The workflow itself is in good shape.** 24 of the last 25 runs green, a
3-version matrix, `fail-fast: false`, concurrency cancellation, and a
`pynifly` load check placed before the suite so the one dependency pip cannot
supply fails loudly instead of taking the suite down confusingly. Windows-only
is correctly justified in a comment (`.pynifly/NiflyDLL.dll` is a compiled
Windows DLL). Contributor lanes are included in the `push` trigger for a stated
reason.

The findings below are about what surrounds it.

---

## 1. The default branch is 131 commits stale — HIGH

    main          behind testing by 131, ahead by 0
    mari/testing  behind testing by 131, ahead by 0

`testing` is the real trunk. `main` is the repository's **default branch** and
this repo is **public**, so anyone who lands on it, clones it, or reads its
source is looking at code 131 commits old.

It has a second effect that matters here: **CI has never run on `main`.** All 25
recent runs are on `testing`, because nothing is pushed to `main`. The branch
that represents the project to the outside world is also the one branch with no
test signal.

Either fast-forward `main` periodically, or make `testing` the default branch so
the visible branch is the tested one.

## 2. No branch protection — MEDIUM

`GET /branches/main/protection` returns 404: not protected. A public repo whose
default branch has no required status check can take a merge that never passed
CI. The workflow already runs on `pull_request`, so requiring it is a settings
change, not new work.

## 3. CI never builds the exe — MEDIUM

The workflow runs `pytest` only. The **shipped artifact is a PyInstaller
freeze**, and freeze-time breakage is a different failure class from test
breakage: a missing hidden import or an un-bundled data file passes every test
and fails only when someone runs `scripts/build_exe.ps1` by hand.

`dist/` is tracked, so the broken artifact would be what users get. A build step
(even build-only, no publish) would close this. It is the largest remaining
coverage gap now that the GUI is exercised.

## 4. The GUI had no build coverage — FIXED (`eb38c8e`)

`src/gui.py` is ~2270 lines. There were five `test_gui_*.py` files, and **none
of them constructed a widget** — they covered module-level pure helpers (name
matching, ETA, theme contrast, process kill). Every widget-building path was
unexercised, so a mistake in the settings renderer could only be found by
launching the exe by hand.

`launch_gui` had carried the hooks for this the whole time (`auto_close_ms`,
`_smoke_settings`) and nothing used them.

`tests/test_gui_smoke.py` now builds the window, and ships its own control: a
planted fault on the renderer's path must make the build RAISE. A smoke test
that cannot fail is worse than none, because it reads as coverage.

Two things learned writing it, both recorded in the file:

* it must launch against an **empty modlist** (`CBBE2UBE_MODS_ROOT` /
  `CBBE2UBE_GAME_DATA` pointed at temp dirs), or the launch-time preflight scans
  the real mod tree — 105s for three tests, and machine-dependent in CI. With
  the fixture: 1.7s.
* a third test counting bound controls by patching `tk.Variable.trace_add` was
  **not** added: patching that globally reaches ttk's internals and the window
  stopped closing, so the test hung rather than failed. A hanging test in CI is
  worse than a missing one.

It skips cleanly where no display exists, so it cannot become the reason CI goes
red on a headless runner.

**Confirmed it actually RUNS on the runner, rather than skipping into a false
green.** The skip guard makes "passed" ambiguous, so the counts settle it: local
`1631 passed, 1 skipped` and CI `1631 passed, 1 skipped` on all three Python
versions. Had the two GUI tests skipped, CI would read `1629 passed, 3 skipped`.
The single skip is the pre-existing ESP round-trip test that needs a real
plugin. So `windows-latest` does provide a usable display and the GUI is now
genuinely covered.

## 5. Repository weight — MEDIUM, and it only grows

    .git            290 MB
    working tree    286 MB
    commits touching dist/CBBEtoUBE/CBBEtoUBE.exe: 103

The tracked exe is ~8 MB and has been recommitted 103 times. Git stores each as
a new blob (a compiled binary does not delta well), and **every clone pays the
whole history** — including every CI run, three times per matrix build.

Tracking the shipped exe is a deliberate choice and there are good reasons for
it, but the cost compounds. GitHub Releases (or git-lfs) would keep the artifact
available without putting each rebuild in every clone. Worth deciding
deliberately rather than by default.

## 6. Dependency and action updates are unmanaged — LOW

No `dependabot.yml`. Actions are pinned to moving major tags
(`actions/checkout@v5`, `actions/setup-python@v6`) rather than commit digests.
That is normal practice and fine for this repo's risk profile; noted for
completeness, not urgency.

---

## What I would do, in order

1. Fast-forward `main` (or switch the default branch to `testing`) — cheap, and
   it fixes both the stale public face and the untested default branch.
2. Require the `tests` check on the default branch.
3. Add a build-only exe job, so the artifact users receive is covered by the
   same signal the source is.
4. Decide deliberately about `dist/` in git.
