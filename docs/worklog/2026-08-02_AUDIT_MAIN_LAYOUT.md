# Audit: main's layout (2026-08-02)

Ran right after `main` was brought level with `testing`. Question asked: what
makes this repository cluttered, and what can be sorted without breaking it.

Counted first, because the answer is not what the root directory suggests.

    dist       1126 tracked files      87% of everything tracked
    tests       180
    scripts      54
    src          36
    .pynifly     19
    root         13
    .github       6
    docs          5

---

## 1. `dist/` is the clutter — 87% of the repo — MEDIUM, and it is a decision

1126 of 1293 tracked files are the built exe and its bundled runtime. `.git` is
290 MB against a ~4 MB source tree, from **103 separate commits** of an ~8 MB
binary that git cannot delta-compress. Every clone and every CI run pays the
whole history, three times per matrix build.

Nothing else in this audit is within an order of magnitude of it.

**Not changed here, deliberately.** Tracking the shipped exe is how users get
the tool, and swapping to GitHub Releases (or git-lfs) changes distribution.
That is a product decision, not a tidy-up.

## 2. Two run artifacts sat in the root, unignored — FIXED

`ube_providers.txt` and `ube_replacers_to_disable.txt` are coverage-scan reports
a run drops in the repo root. Both name the **mods root path** and **list mod
names**, and neither was in `.gitignore` — so a single `git add .` would have
staged them into a public repo. They were sitting untracked in the working tree
through every `git status` of the last two sessions.

Now ignored, and added to the hygiene test's enforced set so the ignore entry
cannot quietly disappear.

## 3. Per-user GUI state was tracked — FIXED

`CBBEtoUBE_settings.json` was tracked at the root. The copy on `main` was `{}`,
so nothing leaked, but the file is app state: running from source writes to it,
which dirties the working tree, and a clone would carry whoever-committed-last's
chosen flags. The app treats an absent file as "all defaults", so there is
nothing to ship. Untracked and ignored.

## 4. `scripts/` was 54 files in one flat directory — FIXED

A contributor opening it saw build tooling, install helpers, conversion
entry points and 35 one-off measurement harnesses in a single undifferentiated
list. Split:

* `scripts/` — 19 operational: build/deploy/install, `convert_one_armor.py`,
  `golden_output.py`, the output-health and postflight checks.
* `scripts/analysis/` — 35 measurement harnesses: the census scripts, the pose
  and clip harnesses, the `verify_*` probes, `mesh_penetration`, `pose_engine`.

29 cross-imports, 13 documentation references and several path-based loaders
were rewritten. Verified by running the suite and by importing the moved modules
and executing one (`pose_engine.load_skeleton()` still resolves the same
649-node skeleton).

---

## The thing worth remembering from this

**Moving those 35 files silently removed 29 tests, and the suite stayed green.**

`tests/test_body_zones.py::test_no_script_redefines_a_breast_band` is
parametrised over `_SCRIPTS.glob("*.py")` — non-recursive. The moment the
harnesses moved into a subfolder they stopped being checked, and the run
reported `1604 passed` with no indication that it was covering 35 fewer files
than the run before it.

It is a POLICY test (no script may hardcode its own breast z-band, because that
is how the band drifted off the anatomy the first time), so losing it silently
is exactly the failure it exists to prevent.

Caught by diffing collected test node IDs against `origin/testing` in a
throwaway worktree, after noticing the total had fallen 1634 → 1605. **A test
count is a measurement; when it moves, something changed, and "everything
passed" does not tell you which direction.** Fixed with `rglob` and a test id
that now names the subfolder, so the next move shows up in the diff.

A second, smaller instance of the same lesson: a `git stash -u` / `git stash
pop` round-trip (used to capture a baseline) silently restored
`CBBEtoUBE_settings.json` to the index after it had been untracked. The new
hygiene test caught it on the next run.

---

## Left alone on purpose

* **Root markdown** — `README`, `USING`, `REPORTING`, `CONTRIBUTING`,
  `CHANGELOG`, `THIRD-PARTY-NOTICES`, `LICENSE`. Seven files, but every one is
  a file people expect at the root of a repository; moving them into `docs/`
  would trade a convention for a smaller listing.
* **`vulture_whitelist.py`** — root-level dev tooling. Moving it means moving
  whatever invokes it; not worth the churn for one file.
* **`.pynifly/`** — vendored dependency, correctly isolated.
