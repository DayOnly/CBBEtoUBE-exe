# Rebuilding and pushing

The tracked `dist/` bundle is part of this repository, and `scripts/release_gate.py`
checks it against the tree it claims to come from. Most of the rules below exist
because the gate caught something; each one names what it caught.

## The ordering rule

The gate's `build commit` clause requires **the exe commit's first parent to be
the commit the stamp names**. The stamp is written into the exe at build time and
records the `HEAD` the build saw.

So a rebuild is not "one of the commits" — it is **the last one**:

1. commit every source, test and documentation change first;
2. rebuild;
3. commit the bundle immediately, touching nothing else.

Rebuilding before a source commit lands puts a commit between the stamp and the
exe, and the gate reads `the exe was last set by <a>, whose first parent is <b>,
but the stamp names <c>`. The only fix is another rebuild, so the order is worth
getting right the first time.

## The dirty flag is narrower than the working tree

`build_identity.build_stamp()` computes `dirty` over `BUILD_INPUTS` only — `src`,
`cbbe_to_ube_main.py`, `CBBEtoUBE.spec`, `.pynifly`, `LICENSE`,
`THIRD-PARTY-NOTICES.md`, `USING.md`, `REPORTING.md`, `assets` and
`scripts/build_identity.py`.

A `dist/` left dirty by a previous build therefore does **not** make the next
stamp dirty, and there is no need to stash or revert it before rebuilding. Check
what actually matters with:

    git status --porcelain -- src cbbe_to_ube_main.py CBBEtoUBE.spec .pynifly \
        LICENSE THIRD-PARTY-NOTICES.md USING.md REPORTING.md assets \
        scripts/build_identity.py

Empty output means the stamp will read `dirty: False`.

If the spec starts reading a new file, add it to `BUILD_INPUTS` in the same
change. `tests/test_release_gate.py` checks that list against the paths the spec
names, so a forgotten entry fails the suite rather than shipping a build whose
dirty flag lies.

## The mutation gate, after the last source commit

    python scripts/mutation_gate.py run

Every seeded mutation (`scripts/mutation_pairs.py`) is applied in a detached
worktree and must turn its named tests red. An anchor that no longer matches
reads NOT_APPLIED and fails the gate, because a test that stays green on a
mutation that was never applied proves nothing; a MISSED pair is a guard that
has become decoration -- fix the guard, never the pair. It is minutes of
pytest: run it once per release, after the last source commit and before the
rebuild, and from the mutation-gate workflow on demand. `tests/test_mutation_gate.py`
keeps every anchor and test id current between releases.

## The golden check, on the maintainer machine

    set CBBE2UBE_MO2_INI=<instance>\ModOrganizer.ini
    set PYTHONHASHSEED=1
    python scripts/golden_output.py check --tol 0.001

Fifteen pieces from the live mod list are converted through the batch worker
(the same door the batch and the tests use) and compared, shape by shape, with
the baseline in `golden/`: vertex positions against the tolerance, the bone
list, per-bone weight totals, and the bytes of the `.tri` and physics XML
written beside each piece. It runs where the mutation gate runs -- after the
last source commit of a release, before the rebuild -- and it is the only step
that reads a real mesh through the whole pass chain. `golden/` is gitignored
because its piece list names third-party meshes, so this step lives on the
maintainer machine and not in CI: the runner has no game and no pieces.

An unintended diff is a regression. An intended one must be explainable shape
by shape, and then `capture` re-baselines. `check` refuses across a different
`CBBE2UBE_*` flag set and says so when the baseline was captured on another
commit; keep the shell free of converter flags for both runs. What it cannot
see: a class the piece list does not cover (the base-game set unless
`golden/pieces.json` points it at more), and anything a float on another
machine would round differently -- it is a same-machine, same-toolchain check.

MEASURED 2026-09-16 on c43a54b: `capture` took 225 s; `check` on the same
tree read 15 of 15 ok in 237 s and exited 0; the same check on a
copy of that tree with a 0.2 u shift planted in the shape copy read REGRESSION
on 15 of 15 pieces (`verts moved max=0.2000u`) in 235 s and exited 1.
The synthetic counterpart, one conversion per convert path through the batch
door on a sphere, runs in the suite and in CI:
`tests/test_convert_paths_through_the_batch_door.py`.

## Rebuilding

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_exe.ps1

The script builds from `.venv-build`, created from `requirements-build.lock` with
pip's hash-checking mode. It compares `pip freeze --all` against the lock before
building and refuses on any drift — that refusal is the point of the lock, so
treat it as a finding, not an obstacle. Plain `pip freeze` hides `setuptools`,
which is why the check uses `--all` and an explicit ignore list.

Building from an unlocked interpreter is what put five packages from the build
machine into the bundle that the notices file never mentioned.

## The build reproduces, and CI checks that it does

`scripts/build_exe.ps1` pins `SOURCE_DATE_EPOCH` to HEAD's commit time, and the
stamp records the value. Measured on 2026-09-16, three builds of one commit in a
detached worktree: with the clock free, a build differed from the tracked bundle
in the exe alone — 180 bytes: two PE header timestamps, the checksum and the
stamp — plus the two files derived from it; with the clock pinned, two builds
were identical across all 1,121 files. That is what lets the release workflow
build the exe again on a runner and compare, instead of trusting the tracked
one.

`stamp-check` gained a `build time` clause for it: the stamp's epoch must be the
stamp commit's own commit time. A build from before the epoch was pinned reads
NOT CHECKED there, which like every other NOT CHECKED means "rebuild before
tagging". Set `SOURCE_DATE_EPOCH` yourself and the script keeps your value —
and the gate then refuses the build, because a rebuild elsewhere cannot know it.

On a tag push the `rebuild` job reads the stamp (`rebuild-plan`), checks out the
stamp commit, installs the interpreter version the stamp names, builds with the
locked toolchain and runs:

    python scripts/release_gate.py rebuild-check <tag> dist/CBBEtoUBE

The exe, the file list, the repository's own files and the hash-locked libraries
must be byte-identical to the tagged bundle. Files that come from the runner's
CPython installation — `python310.dll`, the stdlib extension modules,
`base_library.zip`, Tcl/Tk and the licence texts copied from it — are reported,
not judged: a runner's CPython is not the release machine's build of the same
version. Whether the exe itself reproduces across machines is not measured yet;
the first tag after this change will say.

## Release markers

`release-markers.json` lists, for each fix a release claims, one module or one
name that exists only with that fix, and what must never ship (`psutil`, the
test framework, the release tooling). `bundle-scan` reads the exe's bundled
modules and its entry script — where the log rotation lives — and checks both
lists; the workflow runs it on the tagged exe and on the rebuilt one.

    python scripts/release_gate.py bundle-scan dist/CBBEtoUBE
    python scripts/release_gate.py bundle-scan v1.4.1

When a CHANGELOG entry claims a fix, add a marker for it in the same change,
with `since` set to the version that will carry it. A marker claimed by a
version after the exe's is reported as NOT CHECKED, so an older tag scans clean
on what it actually claims -- and a version that claims none reads NOT CHECKED
rather than passing on nothing. The list must always be able to fail:
`tests/test_release_gate.py` checks every marker against the tracked exe, and
the gate refuses a list with nothing to find or nothing to refuse.

## Checking the result before committing it

    python scripts/release_gate.py manifest-check dist/CBBEtoUBE
    python scripts/release_gate.py bundle-scan dist/CBBEtoUBE

Then commit the bundle and check the whole gate against the commit:

    python scripts/release_gate.py stamp-check HEAD

Before a release, every clause must PASS. `release asset` reads NOT CHECKED until
a zip exists; pass `--asset <zip>` once it is uploaded. Anything else NOT CHECKED
or FAIL means the bundle and the tree disagree, and the answer is a rebuild.

## The bundle is scanned like any other tracked file

`dist/` is **not** exempt from the public-repo content rules. It is committed
PyInstaller output — exactly where a build-machine path would bake in — and it was
once skipped wholesale, leaving 1,126 tracked files invisible to the rules.

Vendored trees listed in `repo_hygiene.VENDOR_EMAIL_TREES` are exempt from the
**email** rule only, because upstream licence texts and package metadata carry
their own authors' addresses. The local-path rule still reads every line of those
files, which is the rule that actually protects `dist/`. If a rebuild starts
shipping a new vendored tree and the pre-commit hook refuses it for upstream
addresses, add that tree to `VENDOR_EMAIL_TREES` with the measurement in the
comment — do not reach for `SKIP_PREFIXES`, which switches off the path rule too.

## Pushing

`pre-push` scans **every commit the push would publish**, not just the tip, because
a commit can reach a push without ever passing `pre-commit` (`git rebase`,
`git merge`, `git commit --no-verify`). A push of a long unpushed run therefore
takes tens of seconds and can block on a commit whose content was fixed later.

When it blocks, read the report before deciding:

- if the flagged text is **already public** at the same place on the remote, the
  push adds another blob, not a new name, and `--no-verify` is defensible;
- if it is not, rewriting is the honest fix — but every rewrite re-hashes the
  commits after it, and this project's SHAs are cited in documentation, release
  notes and its own notes. `CONTRIBUTING.md` explains why older commit messages
  are left alone.

Never quote a flagged asset name when reporting a block. Mask it.

## Tagging a release

`version` compares the exe's version, `src/version.py` and the first dated
CHANGELOG heading, and when the ref is a tag it compares the tag name too. Bump
all three, rebuild last as above, then tag the rebuild commit.
