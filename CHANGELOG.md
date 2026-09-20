# Changelog

## Unreleased

### Development only — the seat-error scorer chooses its frame by evidence and stops scoring proxies as garment

`scripts/analysis/seat_error_vs_author.py` reported a **mean seat error of
9.1811u** against a median of 0.3466u. Over its own 4499 paired shapes the top
44 carried **95.7%** of that total, at up to 2473u — and the meshes were fine.

The cause was an assumed coordinate frame. A source NIF stores verts in the
shape's skin frame and needs `_verts_skin_to_world`; a converted output already
stores world verts, so applying the same rule to both transforms the output
twice. `snugness_census.py` records this and says assuming it "made every
number it printed void". Most shapes have an identity transform and are
unaffected, which is why only the collider and helper shapes blew up: one of
them has raw vertices at z 90.6–118.6, exactly where a neck scarf belongs,
while the doubled transform spread them over z −319.9…144.3.

Neither arm is uniformly one frame or the other — measured over a 220-file
sample, our own output wanted `raw` on 14 shapes and `world` on 26, with 281
ties — so `_pick_frame` now measures both candidates per shape per arm and
keeps whichever lands nearer that arm's body, the approach `snugness_census`
already uses. A shape neither frame can place is excluded and reported;
in practice none now are.

A shape is treated as a proxy only when it **both** renders nothing **and**
says so in its name. "Renders nothing" alone is the idiom `bust_gap_score` and
`morph_clip_test` use, and measuring its exclusions is what showed it is not
sufficient here: of 666 non-rendering shapes, 72 carried no proxy token and
were plainly garment — a cuirass, greaves, pants, a sash, four shawl parts. A
garment textured from the plugin's alternate-texture list has empty embedded
paths and is not a proxy. Requiring both signals keeps those 72 and still
skips 594, and every skipped name was checked in the other direction too: all
54 are colliders, proxies or virtual helpers, with no `Color`/`Refined`-style
false match. `collar` is excluded from the token list deliberately — `col` is a
real token and a collar is a real garment part.

The run prints what it skipped, what it kept, and which frames it chose, so
neither population can shrink unnoticed.

    mean     9.1811u -> 0.4269u
    median   0.3466u -> 0.3505u   (the median was always the robust read)
    scored   4499    -> 4053

The shipped meshes are not implicated and nothing in the converter changes.
Guarded by `tests/test_seat_error_transform_guard.py` (19 tests) and mutation
pairs `SEG-a`..`SEG-h`.

### Fixed — a leg plate's crotch panel no longer swings into the buttock on every stride

Every pass that decides a garment vertex's thigh/pelvis split aimed it at the
body vertices nearest to that vertex: the body-swap reskin first, then the
fitted-cloth conform, the leg-plate butt rebalance and the limb-motion push-up.
On a coarse crotch panel the nearest skin is the inner thigh, less than a unit
away, while the same panel passes over the buttock cleft a few units further
on, skin that never moves with a leg. One leather greave's rear gusset, 75% on
the right thigh as the author weighted it, was reskinned to 42% right thigh
over skin that is 100% pelvis, and a 50-degree swing of that leg carried the
panel 2.5u into the lower buttock and the top of the inner thigh (869 body
vertices lost more than 0.5u of clearance; the left leg moved nothing). Of the
224 body-swap pieces whose crotch band a garment covers, 55 lose more than 1u
under a single 45-degree swing, and 50 of those came from a source whose author
shipped only a partial body under the garment, so the author never saw it.

Each of those passes now aims at the skin the vertex actually covers, the body
vertices whose nearest garment vertex it is, weighted by how close that skin
is, inside the crotch and hip band. `CBBE2UBE_NO_COVERED_SKIN_TARGET=1`
(settings window: "Leg plates follow the skin they cover") restores the
nearest-vertex targets. Measured through the
built exe on the reported set, at the shipped recipe and again at the defaults
(identical numbers): under a single 45-degree swing of the right thigh, the
clearance lost over the covered crotch band falls from 1.00u to 0.36u (90th
percentile), covered skin newly inside the garment from 7.5% to 2.8%, and
vertices losing more than 0.5u from 18.9% to 7.6%. No vertex moves, no bone is
newly emptied, the acceptance gate passes on every scored row, and with the
switch off the new build reproduces the previous one byte for byte on all 24
game files. Over four more mods (13 body-swap pieces with a covered crotch
band) one piece improves from 2.36u to 0.64u, three improve slightly and none
worsens; the seven robes among them do not change, because a shape on the
draping-name list keeps its authored skin by the standing physics rule. The
census behind the class sizes is the new `scripts/analysis/single_swing_census.py`.

### Development only — commits are made on lanes, the hooks refuse to run without the denylist, and a clone sets itself up with one command

`main` and `testing` now change only by a merged pull request: a ruleset on
GitHub refuses direct pushes, force-pushes and deletions, requires the three
`pytest` lanes and allows merge commits only, because a squash or rebase merge
re-hashes the commit the exe's stamp names. On the client side the pre-commit
hook refuses a commit on either branch and a commit in the primary checkout on
any branch: work is committed on a lane, one branch in its own linked worktree,
which `scripts/lane.py new <name>` creates beside the checkout with the asset
denylist copied in and the build environment joined, so a rebuild there
reproduces. A checkout without the denylist used to run the other three rules
and skip the asset-name rule in silence (measured: a file and a message naming
a real asset were committed and pushed with exit 0); all three hooks now refuse
and say why, and a linked worktree reads the primary checkout's copy. One
environment variable each allows a deliberate exception without switching every
rule off. `scripts/onboard.py` configures a fresh clone (the hooks path,
fast-forward-only pulls, an LF-only checkout, the blame ignore file) and reports
what only the person can supply. CONTRIBUTING.md and docs/RELEASING.md describe
the flow; pull requests target `testing`. Nothing in the converter changes.

### Development only — the suite passes on a runner's one-commit checkout and on every interpreter lane

The first CI run of this history failed on all three lanes for reasons that
never showed on a maintainer's machine. The runner checks out a single commit,
so the release-gate test that reads the tracked exe's stamp could not find the
commit the stamp names; the workflow now fetches the full history of the ref it
tests, and only that ref, before the suite runs. And two tests that read the
executable's bundled modules ran on the 3.11 and 3.12 lanes, where that
bytecode cannot be read; both now skip there, as the other gate tests already
did, and still run on the 3.10 lane, which is the interpreter the exe is frozen
with. Nothing in the converter changes.

### Fixed — the end of a run no longer holds every mesh pair in memory until it finishes

Two checks at the end of a run (the `_0`/`_1` jiggle sync and the parity
check) kept every mesh pair they had opened in memory until the run finished:
about 55 MB per pair, so the main process grew from 1.4 GB to 3.6 GB on a
76-file mod and would have grown without limit on a large pack, at the moment
the end-of-run sweep starts its own worker pool. Each pair is released as soon
as it has been checked, one clean-up runs when the batch ends, and the log now
prints the free commit charge after the batch beside the machine line it
already prints at the start.

### Development only — the one decorative kill switch is retired, and the census says what the rest wait on

`CBBE2UBE_BACK_RESIDUAL_QUIET` silenced two `[back-residual]` log lines and
nothing documented, tested or set it; it is gone, and the two lines always
print. The retirement census now prints, beside every kill switch it leaves
standing, the record it is waiting on (or `UNRECORDED`), and a test keeps that
table and the census in step both ways. Nothing changes in any output file.

### Development only — every setting is read the one way, so the flag censuses see all of them

Seventy-seven `CBBE2UBE_*` names were still read straight off the
environment, bypassing the flag helpers and the three censuses that parse
them. Every boolean and numeric read now goes through the helpers; the twelve
that remain raw are paths and strings, listed by a test that fails on any
other. Nothing changes at the defaults or under the settings window, which
writes `1`, `0` or unsets. A hand-written recipe changes only at edge values the
helpers already document: an unrecognised word means off, an empty value
means the default, and a number that does not parse stops the run instead of
being silently replaced.

### Changed — the progress bar moves inside a large mod, and Cancel says cancelled

The bar used to move once per mod, so a mod with seventy files after a few
small ones looked hung for its whole duration, and it jumped a whole mod ahead
the moment one started. It now fills with that mod's files as they finish, the
status line carries a time estimate for the mod it is on, and the estimate for
the run stays as it was. Pressing Cancel used to end with "Finished with exit
code 1 - check the log for errors/warnings" and a popup; it now says the run
was cancelled at your request, opens no popup, and the Results tab shows what
finished before the stop.

### Changed — every warning says what happened, what it means and what to do

A `!!` line in the run log now has the same shape everywhere: what happened
and where, then a line saying what it means for the run, then a `FIX:` line
when there is something to do. The 69 warnings the batch runner and the mesh
writer can print were rewritten to it, and no Python error text (`OSError(13,
'Permission denied')`) reaches the log or the end-of-run popup any more: an
error reads as `OSError: Permission denied`. `docs/WARNINGS.md` lists every
warning with its meaning and fix, generated from the code so it cannot drift.

### Development only — both convert paths now run end to end in the test suite

One synthetic conversion per path (copy, and body-swap with an inline body)
goes through the batch worker's door and reads the written NIF back; a pass
that dies must show up in the result by name. The golden check on real meshes
is written into docs/RELEASING.md as a release step. Nothing in the converter
changed; the exe is rebuilt only because one source comment was corrected.

### Changed — each worker holds the body's slider data in a tenth of the memory

The UBE body's slider deltas (its `.osd` file) are parsed once by every worker
and kept for the whole run. They were kept as one Python tuple per vertex
offset; now they are two arrays per slider, holding exactly the bytes the file
holds. Measured on the shipped UBE body file (202 sliders, 805,112 offsets,
10.75 MB): 166 MB of committed memory per worker as tuples, 10 MB as arrays,
and the parse takes 6 ms instead of 181 ms. In a converter run with 15 workers,
a worker's committed memory right after pre-warm went from 245 MB to
84 MB, and it stayed lower for the rest of the run.

Nothing about the output changes: every NIF, `.tri` and physics XML written for
a 92-file mod is byte-identical before and after (46 weight pairs, 38 `.tri`
files), and the body-morph maps the fit passes read are bit-identical to the
old loops. The memory guard still prices a worker at about 2 GB, so the number
of workers you get is unchanged; the room is headroom, not more workers.

The same change makes the `.tri` reader and writer about twenty times faster:
the converted pack's largest morph file (27 MB) now parses in 43 ms instead
of 941 ms and writes in 137 ms instead of 2.8 s, holding 110 MB instead of
742 MB while it does, and the bytes written are identical to before
(measured on the two largest `.tri` files the pack produced).

### Changed — the tool keeps everything in its own folder; nothing goes to AppData

Your exclusions list is saved as `CBBEtoUBE_exclusions.json` next to
`CBBEtoUBE.exe` again, beside the settings file and the run logs. Since 1.1 it
had lived in `%LOCALAPPDATA%\CBBEtoUBE`, so replacing the tool folder did not
reset it — reported by a user who did exactly that and found their old
exclusions still in place.

**If you are upgrading from 1.1 – 1.4.1, your exclusions start empty.** The old
file is not read, moved or deleted. Before an All-mods run the Run tab asks you
to review the list, as it does every session; tick the mods to skip again
there. The old `%LOCALAPPDATA%\CBBEtoUBE` folder is no longer used, and you can
delete it.

Nothing else is written under AppData either — and that includes the Windows
temp folder:

- overlay transfer's working files are made in an `_overlay_work` folder inside
  the output mod while a pass runs, and removed when it ends;
- the setup check's archive lookup writes nothing at all;
- the run log is written only next to the exe. If that folder cannot be
  written to, the run now says so and keeps no log file, instead of quietly
  putting one in the temp folder.

For modlist authors: an exclusions file in the tool folder travels with that
folder, so a compile that includes the folder ships it.

### Fixed — checking something after a failed run could erase that run's log

`CBBEtoUBE_previous_run.log` is the file to attach when a run dies. But *any*
use of the exe moved the logs along, including `CBBEtoUBE.exe --help`, a
mistyped command, or `validate`. Measured: after a failed run, one `--help`
followed by one typo left no copy of the failure in any file beside the exe.

Now only a real run moves the logs aside: Run in the window, or `auto`,
`convert` or `merge` from a command line. Everything else writes its output to
`CBBEtoUBE_cli.log` and leaves the run logs alone.

A run started from a command line also moves the previous
`CBBEtoUBE_last_failures.json` aside as it starts, as the window already did.
Before, a run killed part-way left the *previous* run's failure summary next to
its own fresh log, as if the two belonged together.

One case still moves the logs: a real command with a mistyped option, such as
`auto --wrokers 4`. The failed run's log then survives as
`CBBEtoUBE_previous_run.log` until a second such mistake.

### Fixed — stopping a run from outside the window left its workers running

If the process doing a conversion was stopped any way other than the window's
own Cancel — End task in Task Manager, a script or launcher killing it, or a
crash — its worker processes kept running. Measured: after such a kill, both
workers of a two-worker run were still alive minutes later, holding 811–944 MB
of memory each and keeping `CBBEtoUBE.exe` locked so it could not be replaced —
and still writing converted meshes for up to 52 seconds, into an output that no
report describes.

The conversion process now puts itself, and every worker it starts, into a
Windows job that ends with it, so the workers stop the moment it does, however
it stops. The window's own Cancel already worked and is unchanged. If Windows
ever refuses, the run log says so.

### Fixed — `convert` outside a modlist reported success, or crashed half-way

Command line only; the window always runs `auto`.

`auto` has always refused to start when it cannot find the MO2 mods folder.
`convert` did not. Run from outside a modlist, it could not check other mods
for existing UBE patches and found no UBE body reference, and then either
finished with `=== all clear ===` and exit code 0 having converted nothing, or
converted a real mod's meshes without the body reference and crashed at the
coverage step (exit code 1, no report). Measured both ways.

`convert` now refuses the same way `auto` does, with exit code 2. To convert a
loose folder outside a modlist on purpose, pass `--ube-body-ref`: the run then
says it could not check other mods for UBE patches, and counts that as a
warning instead of finishing "all clear".

### Added (development only) — a release gate that checks the exe against its tag

`python scripts/release_gate.py stamp-check <tag>` reads the build stamp out of
the exe's bundled modules and checks it against git: the stamp names the
commit the exe was built from, that commit is in the tag's history, nothing
the build reads changed between that commit and the tag, and the exe,
`src/version.py`, the first dated heading here and the tag name all give the
same version. With `--asset <zip>` it also checks the uploaded release zip
against the tagged bundle, file for file. Both published 1.4 releases pass;
the rule this replaces, "the stamp names the tag's parent", rejects both of
them. A new workflow runs the check when a tag is pushed, and again with the
zip when a release is published. `deployed-diff <folder>` compares an
installed tool folder with the build the same way.

`scripts/deploy_exe.ps1` also stops overwriting files the tool keeps beside
itself. Measured on a scratch copy: its list of protected files let a deploy
replace the installed `CBBEtoUBE_settings.json.bak`,
`CBBEtoUBE_gui_session.log`, `CBBEtoUBE_cli.log` and the glow debug log with
copies left in the build folder.

### Added (development only) — the exe rebuilds byte for byte, and the release workflow checks that it does

`scripts/build_exe.ps1` now pins the build time to the commit's own time
(`SOURCE_DATE_EPOCH`), and the build stamp records the value. Measured at the
last rebuild of this tree, three builds of one commit: with the clock free, a
build differed from the tracked bundle in the exe alone — 180 bytes: two
timestamps in the PE header, the checksum and the stamp — plus the two files
derived from it; with the clock pinned, two builds were identical across all
1,121 files. The release gate gains a `build time` clause that reads the pinned
value back out of the exe and refuses a build whose value is not its commit's
time; a build from before this rule reads NOT CHECKED there and needs a rebuild
before it is tagged.

The release workflow gains a second job. On a tag it builds the exe again on a
GitHub runner, from the commit the stamp names and with the interpreter version
the stamp names, then compares the result with the tagged bundle
(`python scripts/release_gate.py rebuild-check <tag> <folder>`): the exe, the
file list, the repository's own files and the hash-locked libraries must be
identical; files that come from the runner's own CPython installation are
reported but not judged. The rebuilt exe must also answer `--version`. Not
measured yet: the job on GitHub itself, which runs only on a tag push.

`release-markers.json` lists, for each fix a release claims, one module or one
name that exists only with that fix, and what must never ship (`psutil`, the
test framework, the release tooling). `python scripts/release_gate.py
bundle-scan <tag, exe or folder>` reads the exe's bundled modules and its entry
script and checks both lists, so a release cannot ship without the fixes it
claims; the workflow runs it on the tagged exe and on the rebuilt one.

### Fixed — every armour conversion kept a copy of the body in memory until the run ended

Each conversion that swaps in the UBE body loads the body again, and a speed
cache kept every one of those copies — the whole body mesh — alive for as long
as the worker ran. The memory planning assumes a worker's use stays roughly
level; this made it climb with every body-swap conversion.

Measured on a 16-piece outfit converted 4 times in one process: memory in
use after each run went 844 / 1273 / 1707 / 2152 MB, and the number of body copies
held rose 12 / 22 / 32 / 42. With the fix it went 395 / 397 / 428 / 435 MB,
with 2 held. The cache now remembers the body file instead of the loaded
copy, so a body loaded again from the same file reuses the work and the copies
are freed. The converted armour is byte-for-byte the same.

### Added — the exe can say which build it is, and the tool folder lists its own files

`CBBEtoUBE.exe --version` (or `-V`) prints the version, the commit the exe was
built from, the build time, and the Python and library versions it was built
with, then exits without writing, rotating or truncating anything. The exe is a
window program, so send the output somewhere to read it:
`CBBEtoUBE.exe --version > version.txt`. Measured on a copy of the 1.4.1 exe:
`--version` was rejected with exit code 2 and still wrote a 185-byte
`CBBEtoUBE_last_run.log` beside the exe. On a build of this change it exits 0
with the build line and the toolchain line, and the folder holds the same 1,129
files before and after.

The Details tab in the exe's Properties (and PowerShell's `VersionInfo`) now
shows the product and file version; on 1.4.1 every field was blank. The tool
folder gains two files: `VERSION.txt`, one line naming the build, and
`SHA256SUMS`, a checksum for every other file. Running `sha256sum -c SHA256SUMS`
in the folder checks a download (Git Bash has `sha256sum`), and
`python scripts/release_gate.py manifest-check <folder>` also names files an
older version left behind, ignoring the settings, exclusions and logs the tool
keeps beside itself. Measured on that build: the clean folder passed on 1,128
program files; a planted leftover file was named, and so was
`_internal/base_library.zip` with one byte changed.

For people building the exe: the build stamp is now written by
`CBBEtoUBE.spec` itself, through `scripts/build_identity.py`, so a direct
`pyinstaller CBBEtoUBE.spec` stamps the build correctly. Before, only
`scripts/build_exe.ps1` wrote it, and a direct PyInstaller run reused whatever
stamp an earlier build had left. The stamp's dirty flag now covers everything
the build reads (`src`, the entry script, the spec, `.pynifly`, `assets`, the
licence files and `scripts/build_identity.py`); it used to look at `src`, the
spec and the entry script only. The release gate also checks the tagged bundle
against its own `SHA256SUMS` and `VERSION.txt`, and the release workflow runs
`sha256sum -c` over the published zip.

### Changed (development only) — tracked text is LF everywhere outside the build and vendor folders

28 tracked text files were still CRLF or mixed: 4 in `src`, 7 tests, 15
scripts, one research script and `docs/PIPELINE.md`. `.gitattributes` keeps
whatever a tool writes, so each was one save away from a whole-file diff, and
that had already happened. Measured with `git show --numstat`: the 2026-09-02
change to `src/nif_convert.py` touched 28,745 lines, of which 41 were real, and
`src/gui_settings.py` (3,228 for 16), `src/auto_convert.py` (11,266 for 12) and
`src/gui.py` (5,506 for 16) went the same way within nine days. The 28 files are
converted in a commit of line endings only, in which every Python file compiles
to identical bytecode. The pre-commit hook and the test suite now refuse a CRLF
in tracked text outside `dist/` and `.pynifly/`, and `.git-blame-ignore-revs`
names the conversion so GitHub's blame skips it (for local blame, run
`git config blame.ignoreRevsFile .git-blame-ignore-revs`). `scripts/split_move.py` and
`scripts/extract_loop.py` asserted that `src/nif_convert.py` was CRLF and so
stopped before doing anything; they now keep the file's own line endings.

### Changed (development only) — two reporting guards now run the code they guard

The test that a dead worker in the end-of-run vertex-colour sweep reaches the
user read the branch's source text. Measured on a copy with that branch made
unreachable (`and False`): the old test still passed. A new test runs
`_cmd_convert` with the sweep reporting a dead worker and checks the printed
line, a warning count one higher than an identical clean run, and the
failures-file entry; it fails when the branch cannot fire or when any of the
three is dropped. The warning printed when the failures file cannot be written
had no test; it has one now.

### Fixed — a run with warnings no longer ends "all clear", and a warning is not called a failure

Measured on a source run of `convert` with an unreadable settings file and no
SkyPatcher installed: the log printed both warnings, then `=== all clear ===`
with exit code 0, and the failures file held one entry, "merge did not run, so
no coverage was generated", which the GUI would have titled "1 item(s) failed
to convert". Now a settings file that could not be read, a missing SkyPatcher,
a check for other mods' UBE patches that could not run, and race coverage that
was not generated or came back incomplete each count as a warning in the
end-of-run tally and appear in the failures file. Every entry there says
whether it is a `failure` or a `warning`; the end-of-run window is titled and
worded from that, and the status bar no longer says "Done - success" over
warnings. Warnings do not change the exit code. The checks shown before a run
gain a "Settings file" row that says when the file could not be read, before a
run or a save writes the defaults over it.
Measured with the same command after the change: `=== 0 failure(s), 3
warning(s) ===`, exit code 0, and three `warning` entries in the failures
file (SkyPatcher, the settings file, race coverage).

### Fixed — the diagnostics zip holds the run logs, the failure lists and the page-file setting

**Help ▸ Save diagnostics zip** packed the report, the log panel, settings,
exclusions, the MO2 layout and a setup check, and none of the files the
reporting guide asks for: not `CBBEtoUBE_previous_run.log` (the one to send
when a run died), not the failure lists, not the output mod's
`conversion_report.json` or `conversion_settings.json`. After restarting the
GUI following a dead run its log panel is empty, so the zip held no log at all.
It now adds each of those files that exists, and a `machine.txt` with RAM, the
commit limit, CPU threads and whether the page file is system-managed, a fixed
size or off: the first thing a memory error needs answered.

### Fixed — reading one mesh from a game archive no longer loads the whole archive

Converting armor whose meshes exist only inside an archive (the vanilla sweep,
or a mod that ships its meshes packed) read the entire archive into memory to
extract one file, and kept it there for the rest of the batch. Measured on the
base-game mesh archive (1.15 GB): reading one 35 KB armour mesh added 1,100 MB
of private memory. The converter now reads just that file's bytes from disk:
measured the same way, the same read adds 0.8 MB.

### Fixed — refreshing the mod list no longer keeps every plugin in the settings window's memory

To find which mods carry armor, the settings window parses every enabled plugin
and keeps only the names and counts, but the parsed plugins themselves stayed in
memory for as long as the window was open. They are released as soon as the
list is built. Measured on the game's five master plugins (347 MB on disk):
holding their parses cost 87 MB of private memory, and releasing them
returned 79 MB.

### Changed (development only) — any import of the package caps BLAS threads

The converter itself was already capped, but a script, census or test that
imported `src.nif_convert` directly paid the full OpenBLAS thread arena in
commit charge (measured on a 24-thread machine: 1,520 MB for that one import,
against 43 MB through the converter's own import path). `src/__init__.py` now applies the
cap before any module of the package can import numpy.

### Fixed — warnings printed while a mesh converts reach the run log

Meshes convert in separate worker processes, and what those processes printed
never reached `CBBEtoUBE_last_run.log`; under the settings window it went
nowhere at all. That included every warning a fitting step prints about a shape
it had to drop or a write that failed half-way, and the traceback of a mesh that
failed to convert, which arrived as one line. Each worker's output now travels
back with its results and is written to the run log, and a failed mesh's full
traceback is written there too.

### Added — `CBBEtoUBE.exe check-setup`, and the exe calls itself CBBEtoUBE

The setup checks were only in the settings window, so someone whose window would
not open, or who runs the tool from a command line, could not run them.
`CBBEtoUBE.exe check-setup > setup.txt` now writes the same checks, one line
each, and exits with code 1 if any check fails. Measured before on a source run:
the command was rejected with exit code 2, and the usage and error lines named
the program `auto_convert`; they now name the exe.

### Changed (development only) — the asset-name check reads whole files, and the exe's bundled modules

The public-repo name check compared its denylist with one line at a time, so a
name wrapped across a line break passed, while the docstring holding it is
compiled as one string and ships whole inside the exe. Measured with the local
denylist: two tracked files carried a name that way, and the tracked exe's
bundled modules carried one of them (15,571 string constants checked). The
check now searches whole files and whole commit messages, the pre-commit hook
checks the string constants of the exe's bundled modules whenever the exe is
staged, and both names are replaced with the recorded stand-in.

### Changed (development only) — no unused imports in the converter modules, and a test that keeps it so

Splitting the converter into modules left 762 imports that pyflakes reported as
unused, which hid any real one. 680 of them were reached by nothing and are
deleted; the other 82 are names tests and callers reach through
`src/nif_convert.py`, and are now declared in its `__all__`. A test fails on any
new unused import in `src/`.

### Changed (development only) — comments about flags are checked against the code in every spelling

Comments in `src/nif_convert.py` still described four default-ON flags as opt-in
or default OFF, and one told the reader to set two environment variables that no
code reads any more. The check for stale opt-in headers read one of the ways a
flag is bound (10 of the 145 bindings), so it saw none of them. It now parses
every binding, the comments are corrected, and the comment audit that found the
dead variable names is a tracked tool (`scripts/analysis/comment_audit.py`)
whose counts of dead variable names and dead helper names may only fall.

### Fixed — messages and docs no longer point at controls that do not exist

The note about options added since your settings were last saved said to open the
GUI's Settings tab, and the setup check's fix for it said "Open Settings"; there is
no Settings tab. The bug-report form said to click Export diagnostics, and README
and CONTRIBUTING named a Report button; those are **Help ▸ Save diagnostics zip**
and the **Results** tab. The check for such names now reads through line wraps and
bold text, covers USING.md and the tool's own messages, and fails on a documented
command-line flag that no parser defines.

### Changed — the issue forms ask the questions that decide a diagnosis

The conversion-problem form now asks whether clipping happens standing still, only
in motion, or only when zoomed out, and for the body preset and weight. Both forms
ask whether the converter was launched through MO2. A bug report can no longer skip
the machine and memory details without saying they do not apply, and the version
examples say 1.4.1. The problem report copied from the GUI asks the same two
questions as the form.

### Fixed — a run removes the temp files an interrupted run left behind

A run that was killed mid-write left the temp file each unfinished write was using
next to the output. The finished file itself was always safe, but the randomly
named temps were never overwritten and piled up, and no scan looked at them. A
run now removes the converter's own temp files left by an earlier interrupted
run, counts them as a warning, and the Results tab shows how many.

### Fixed — a failed physics-XML write is recorded instead of shipping the old physics silently

When rewriting a piece's physics XML failed (a locked or read-only file, a full
disk), four passes swallowed the error: the piece shipped with its previous physics
and nothing said so. Three passes that restore a mesh backup after a failed check
also printed "RESTORED original" even when the restore itself failed. All seven now
record the failure with the run's other pass failures, the restore message says when
the restore failed, and the checks that forbid silent write failures cover these
writes and name the right file and function.

### Changed (development only) — the acceptance gate takes a repeat control

`scripts/analysis/acceptance.py --repeat <dir>` names a second arm of the control
code. The pieces the two control arms disagree on are listed as noise; the gate
refuses to judge (exit 4) unless `--allow-noise` is passed, and then removes them
from both arms before every row is scored. Each arm's count of pieces whose physics
XML resolves by a cross-directory stem match is printed beside them.

### Changed (development only) — the vendored PyNifly says what it is

`.pynifly/UPSTREAM.md` records the upstream release the vendored PyNifly came from
(V25.9), the DLL's hash and the version it reports, and the ten local changes to the
`pyn` package as patches against that release. A test checks the record against the
files, so a changed DLL or a new local edit cannot go unrecorded.

### Changed (development only) — a push is checked commit by commit

The public-repo checks ran only when a commit was created, so a rebased,
cherry-picked or merged commit could reach a push without them. A `pre-push` hook
now checks every commit the push would publish -- message, author and committer
addresses, and each file it adds or changes -- and a `pre-merge-commit` hook runs
the pre-commit checks on merges. Both hooks now refuse when a git command they
need fails; they used to read its empty output as nothing to check and let the
commit or push through.

### Changed (development only) — tests run only under pytest

Six test files also called their own tests when imported, so each of those tests
ran during collection as well as under pytest, and left a directory in tests/ that
nothing cleaned up. The calls are gone and a test fails on any new one. A docs
test that looked for "scratchpad" in a tool's absolute path now reads its path
inside the repository, so it no longer fails in a clone under a folder of that
name.

### Changed (development only) — dead code removed, and a test keeps it out

vulture ran nowhere, so unreferenced code only accumulated. Removed: a module
nothing imported (the remains of a deleted offset experiment); functions only
their own tests called -- a nude-body collider generator the converter never
attached, a slot-49-to-slot-32 promotion with its payload helper, an EDID name
synthesiser, and a physical-memory probe the worker count did not use; a nested
helper nothing called; and five never-read locals. A test now runs vulture over
src/ and fails on anything new; the names kept on purpose are listed with their
reason in vulture_whitelist.py.

### Added (development only) — a test pins what the rollback guards cannot see

The collider and bust passes roll a write back if extra data disappeared, but
they read extra data with a walk that stops at the first block the NIF library
cannot build, so a BODYTRI behind such a block is invisible to them. A test now
builds exactly that shape and shows the walk missing the BODYTRI that the
index-based reader finds. No converter change: the guards are left as they are
on purpose, and no piece in the acceptance set has such a shape.

### Fixed (development only) — a placeholder elsewhere on a line no longer hides a real path

The public-repo check skipped an absolute local path whenever a placeholder such
as `<local-path>` or `MO2Root` appeared anywhere on the same line, so a working
note quoting a real instance path passed. It now looks for the placeholder inside
the path itself. The one tracked line that newly fails is redacted, and a test
fixture that used a real modlist folder name now uses a made-up one.

### Added — Help ▸ Check for a newer version, and Check setup names the build

The Help menu now opens the Releases page, and Check setup (like `check-setup`
and the diagnostics zip) lists the build you are running, so the two can be
compared. The tool still never goes online: your browser does the checking. The
README gains an install section for the release zip.

### Changed (development only) — the exe is built from a locked toolchain

The build asked for "numpy 1.24 or newer" and installed whichever PyInstaller was
current, so two builds of the same source could ship different libraries and
nothing recorded which ones. A `requirements-build.lock` now pins every build
library with its hash; the build script installs that lock into its own
environment and refuses to build when the environment drifts from it, and CI's
production-runtime lane installs the same lock.

### Added (development only) — the over-follow claim can be read both ways at once

A 2026-08-01 note measured the garment moving 1.25 to 3.7 times further than the
body per slider and proposed rescaling every converted morph file; a later
reading on another piece, pairing each garment vertex with the body vertex it
covers, came back at 1.00. A new development tool prints both readings side by
side over a whole arm, so the class is settled on data before anything is
designed on top of it.

### Changed (development only) — CI runs read-only, on pinned actions

Neither workflow said what token scope it needed, so the test workflow inherited
the repository default, and both resolved moving action tags. They now declare
read-only permissions and pin each action to a commit, a Dependabot config asks
to be told when those pins age, and SECURITY.md says how to report a
vulnerability privately.

### Fixed — four documented switches could not be reached from the GUI

MO2 does not pass your environment to the program it starts, so a `CBBE2UBE_*`
variable never reaches a run launched from MO2 — and four switches the README
documents had no row in the window, which made them unreachable on the supported
path. They now appear under *Show advanced*: the bust anti-poke's spacing-aware
search, the slider-residual bust cover, the soft-body cloth clearance, and the
one-sided groove cleanup. The last of those was also read straight from the
environment, so no check could see it either.

### Added — the download carries its own instructions

The zip shipped the program and nothing to read: the licence and third-party
notices ride inside `_internal/`, and the usage and reporting guides were not in
it at all. `USING.md` and `REPORTING.md` now sit beside `CBBEtoUBE.exe`, and the
build's own `SHA256SUMS` lists them like every other file.

### Fixed — the bundle carries the licence texts it says it carries

The notices file stated that full licence texts ship with each library. Three did
not: CPython's, SciPy's and Tcl/Tk's were nowhere in the download, and SciPy
leaves no version behind in the bundle at all. The build now copies those three
into `_internal/licenses/`, the notice says where each text is rather than
asserting they all ride along, and a test reads the executable's own contents and
fails if it carries something the notice does not name.

### Fixed — a run that dies leaves its own report, marked incomplete

The output mod's `conversion_report.json` and `conversion_settings.json` were
written only after the last mod. A run that died mid-batch left neither, while
the previous run's finished report stayed in the output root — and the Results
tab showed that old scoreboard as the last run. Measured before the change: a
killed run's output root held only the staging folders.

Now the settings file and an empty report are written before the first mod,
and the report is rewritten after every mod, marked `"complete": false` until
the end-of-run write. The Results tab heads such a report **INCOMPLETE run**
with its date and says how many of the planned mods it covers; the problem
report's last-run block says the same. The settings file now names the worker
count that ran. The report, the settings file and `CBBEtoUBE_last_failures.json`
are written atomically, so a death during a write cannot leave a torn file.
Older readers of the report see one extra field and ignore it.

### Added (development only) — a mutation gate that proves the guards can fail

Every guard in the repository was proven by hand with a throwaway driver, and
those hand-runs are what found the guards that could not fail. The driver is
now tracked: `python scripts/mutation_gate.py run` applies each of the
40 seeded mutations (`scripts/mutation_pairs.py`, the pairs measured when
each guard landed) in a detached git worktree it creates and removes
itself, and each must turn its named tests red. An anchor that no longer
matches reads NOT_APPLIED rather than "still green"; a pair whose named
tests stay green reads MISSED; either fails the gate. It never touches the
checkout, and refuses to. Measured on the release machine: 40 pairs in
879 s — and its first full run found one seeded pair gone stale the same
day it was measured, which is the point. A workflow runs it on demand;
`docs/RELEASING.md` puts it after the last source commit of a release.

## 1.4.1 — 2026-09-11

### Fixed — memory errors on machines with a small or disabled page file

Reported against 1.4. **Nothing in 1.4 caused it** — every mechanism below is
byte-identical in 1.3, and the largest one has been there since 1.2. More
people ran it, and more of them had the machine setting that turns it from
"slow" into "failed".

The tool imports numpy and scipy, and doing so **committed 1,516 MB of memory
in every process** on a 24-thread machine, of which about 97% was maths-library
thread scratch space that is reserved at load and never read. Measured on one
box, same interpreter, before and after:

| | memory committed | memory actually used |
|---|---|---|
| starting Python | 5.9 MB | 11.1 MB |
| loading the converter | **1,516.3 MB** | 54.0 MB |
| after this fix | **37.9 MB** | 51.1 MB |

The "actually used" column barely moves, which is why this never showed up as
high memory usage and went unnoticed for four releases. But **Windows fails an
allocation against your RAM plus your page file**, not against free RAM, and
reserved memory counts in full. Each worker is a separate process paying the
whole amount, so a pool of 8 plus the two helper processes reserved about
15 GB before reading a single mesh. With a system-managed page file that is
merely wasteful. With the page file disabled or pinned small — advice that
circulates widely as a performance tweak — it is the entire failure.

Also fixed, all three found by auditing the same path:

- **One fit step allocated up to 3.7 GB on a single garment.** It measures
  distance from the body to the garment's cut edges, in slices of body
  vertices — but the edge side of that was unbounded, so the cost grew with
  the garment. Across a local 383-mesh sample (not shipped): half peak under
  56 MiB, but the worst reaches 3.71 GiB on a shape with 16,217 edges. Now
  capped at 64 MiB for every mesh. **Output is unchanged, bit for bit** —
  each result depends only on its own body vertex, so the slice size cannot
  alter a number, and the tests check that against slices from 1 to 100,000.
- **It failed silently when it did run out.** The error was caught as a
  generic per-shape failure, so the piece shipped without that fit step, the
  run carried on, and the word "MemoryError" never reached you — while the
  actual exhaustion surfaced in some other worker, possibly on another mod.
- **The final cleanup pass ignored the memory budget.** It sized its own
  process pool on CPU count alone, so on a 16-thread machine it started 14
  fresh processes where the budgeted pool had run 8 — with no setting to
  lower it, at the very end of a run, and falling back to a slow serial walk
  if it died, so the run still reported success.

### Fixed — a run that died of memory could report success, and erased its own evidence

Two separate silences, both found by asking what a user could actually *send*
after a failure rather than by reading the conversion code.

**The final cleanup pass reported success after a worker was killed.** It runs
at the very end of a conversion and starts a fresh set of processes to do it —
the likeliest moment in a whole run for one to be killed for memory. That
failure was caught by a catch-all that quietly redid the work one file at a
time and returned a clean result: nothing printed, nothing counted, nothing in
the failure summary. The output was still correct, so this was never a
correctness bug — but a run that nearly died looked identical to one that
sailed through. It now says so, counts as a warning, and lands in the failure
summary, naming the setting that fixes it.

**The run log was deleted before each run.** It is the only file that survives
a hard out-of-memory death: it is written line by line as the run goes, so it
holds everything up to the instant the process died, including the new machine
and memory-plan lines. Every other artefact — the report, the settings dump,
the summary — is written when a batch *finishes* and simply does not exist
afterwards.

Both the command-line tool and the settings window destroyed it at startup, the
window by deleting it outright. So the advice the tool itself gives after a
memory death — run again with fewer workers — erased the evidence of the
failure it was reacting to. The previous run's log and failure summary are now
kept alongside the current ones as `CBBEtoUBE_previous_run.log` and
`CBBEtoUBE_previous_failures.json`. If a run dies, that pair is what to attach
to a report.

### Changed — the memory budget is now measured, not estimated

The worker count is bounded by how much memory Windows will let the run commit.
Setting that bound needed a number nobody had: what one worker actually uses
mid-run. It has now been measured against a real 3,800-mod pack, sampling each
process from outside:

| | |
|---|---:|
| worker, idle after start-up | 0.36 GB |
| worker, converting | 0.6 – 1.3 GB |
| worker, peak on a sampled population | 1.05 – 1.98 GB |
| worker, peak on the largest single mesh (14 MB) | 3.79 GB |
| the converter process itself, peak | 2.7 – 2.8 GB |
| the settings window, after listing your mods | 0.71 GB |

The first release of this guard priced a worker at 1.0 GB, a figure reached by
subtracting the memory the thread-cap fix had just removed. That was **1.7 to
3.8 times too low**, and it was too low in both directions that matter: it let
the tool start too many workers, and it understated the projection, which
suppressed the low-memory warning that was supposed to catch the rest. It also
charged the converter process and the settings window as if each were one more
worker; the converter alone peaks at nearly three times that.

The cap reads how much memory Windows will let the run commit **at the moment
the tool starts**, so a machine that is merely busy — a browser open, a game
running — can also get a smaller pool than its RAM alone would allow. The
arithmetic is printed, and the Worker processes box on the Run tab overrides it.

### Changed — the worker count no longer promises more memory than you have

The count is capped by RAM as well as CPUs, but it **rounded up**: a machine
reporting 15.85 GB was granted 8 workers at 2 GB each, i.e. 101% of the
machine, before Windows, MO2 or the tool's own two processes took a byte. It
now rounds down, and a second cap reads the page-file headroom and lowers the
count when the run would not fit. With the page file off the pool shrinks
instead of failing; on any machine, a large amount already committed by other
programs lowers it too.

The 2 GB-per-worker figure stays. It bounds a worker's *working set*, which the
same mid-run measurement put at 0.6 – 1.3 GB steady and 1.53 – 1.69 GB at peak,
so 2 GB errs about 1.2× toward safety — not the 2× an earlier draft of this note
claimed by subtracting the scratch space the thread-cap fix removed. That
subtraction compared two different quantities and was wrong; lowering the budget
on it would have broken the cap.

### Added — a run now says what machine it ran on

A memory report was previously impossible to act on: no log line, no report
field and no setup check recorded how much RAM, how many CPU threads or how
large a page file the machine had. The one line that would have said anything
was printed only when the worker count was chosen automatically — which never
happens from the GUI, because it always passes the count explicitly.

- **Setup check** gained a memory row, so a tight machine is flagged *before*
  a three-hour run rather than during it.
- **Every run** prints its RAM, thread count, page-file limit and projected
  memory use, and warns when the run wants more than is comfortably free.
- **`conversion_report.json`** carries the same figures in a `machine` block.
- **A worker killed for memory** now says so and names the setting that fixes
  it, instead of reporting the tool as broken.
- **The bug-report form** asks for RAM, threads and the page-file setting.

If a run still dies: lower **Worker processes** on the Run tab, or set the
page file back to system-managed. The second is the better fix.


### Fixed — small skirts were shipping with no collision at all

A garment whose physics cloth needs a collision shape gets one built for it: a
simplified copy of the garment, cheap enough for the simulation to test every
frame. Building it means thinning the mesh down to a few hundred points.

For any cloth piece with fewer than about 1500 points there was nothing to thin
— it was already small enough — and the thinning step handled that case by
discarding **everything**. The piece then shipped with no collision shape, and
nothing in the run said so. On the last pack that was **eight skirts with no
collision, and a ninth whose collision shape was a single triangle** stretched
across the whole garment.

In game that reads as a skirt that ignores the legs it is wrapped around, or
one that snags on a shape far larger than itself. Small pieces now keep their
mesh as their collision shape, which is the correct answer when there is
nothing to remove, and the step refuses to return an empty result silently.

The number of collision shapes in a pack fell from 28 to 2 between the last two
builds, and **most of that fall was correct**: two guards added on 2026-09-04
decline a shape that would have been cloned from the ground plane, or that
would have contained the very chain it exists to collide with. Eighteen of the
twenty-six were those guards working. These nine were not, and they are what
this fixes.

Found by scoring the shipped pack rather than by reading the code: the only
test covering that step used a mesh comfortably above the size that broke it.
It now sweeps across the boundary.

**The 1.4 download already has this fix** — the binary was rebuilt after the
1.4 pack was scored but before the release was published (source fix
`6241bee`, rebuilt into the shipped exe by `6f80b20`, both ancestors of the
`v1.4` tag), and only its changelog entry was left uncredited. It is recorded here so the release it shipped in is written down
somewhere. You do not need 1.4.1 for it.

What does still apply: armour converted from here on gets its collision
shapes; armour converted before that rebuild does not, so those nine pieces
stay uncollided until you convert them again.

## 1.4 — 2026-09-09

Everything below shipped in the pack built on 2026-09-09: 163 sources (162 mods
plus the base game and its DLC), **3673 meshes** under `meshes/!UBE`, **no
failures**. The headline is the chest — armour left sitting inside the body at
the bust is halved, by the first change measured to move the standoff back
toward where the author put it rather than further away.

This release accumulated over four packs, each built and judged before the next
was started. 2026-08-22 (163 sources, 3673 meshes, no failures), judged
*"everything looks as it should"*. 2026-08-26, adding the two chest fixes and
the layer-ride body floor. 2026-09-09, adding the four entries at the end of
this section — every number quoted for them was measured on the finished pack
rather than predicted: sliders dead on 54 outfits went to 0, the two wrongly
deleted armour pieces are present, and no outfit ships mismatched halves. That
pack was judged in game and the verdict was that **armour "sits too far off the
body"**. It is recorded against the pack rather than pinned on any one change:
nothing measured since points at a single cause, and the standoff has several.

The release pack is the answer to that verdict, and the only thing it changes is
the layer fix described at the end of this section. Measured on the finished
pack: armour the converter could not write, meshes that lost their morphs,
`_0`/`_1` pairs that diverged, and collision proxies containing the very chain
they exist to collide with are all **zero**; two bones carrying no weight
survive on one shape out of 9,653, unchanged from the pack before it. **This
pack has not been judged in game yet.**

### Fixed (on by default) — armour converted by copying is no longer left inside the body

Armour that gets a new body built for it has always been pushed back out
wherever the body would otherwise come through. Armour converted by *copying* —
about three quarters of a modlist — never had that step at all, so whatever the
fit left inside the body is what shipped.

It was reported as trousers sitting inside the backside. Measured on that piece:
the author's own version had **no** part of the seat inside their body, and ours
had **59%** of it, up to nine tenths of a unit deep. With the fix, none.

The same repair the other path uses now runs here, in the same place, and the
step that restores rigid plates afterwards runs with it. It only ever pushes
outward, so it cannot pull cloth into the body; it leaves physics-simulated
cloth where the simulation wants it; and physics files are untouched.

Measured across two armour sets (86 heavy plate pieces, and 40 with HDT-SMP
cloth) before being turned on: fewer vertices inside the body on every piece
that had any, no piece worse, chest fit unchanged, and no armour left carrying a
broken bone. One outfit was checked in game. Setting: "Push armour out of the
body on pieces converted by copying" (Armor → Fit and clearance), now on.

Known and unexplained: on large torso pieces, slightly less of the seat is
covered than before (about three points). It is not a uniform loss — coverage
rises on other pieces — but if something looks like it has pulled away from skin
it used to cover, turn this off and say so.

### Fixed (on by default) — armour no longer ships bones that carry no weight

A vertex can be held by at most four bones, and when a fifth turns up the file
format keeps the four largest and silently discards the rest. One of the
skinning passes could give a vertex a new bone that pushed an existing one out
of that four — and where that was the last place the bone held any weight, the
bone stayed listed on the armour carrying nothing at all. That is the state
that can crash the game as the piece is equipped; the 2026-08-28 build left 225
such bones across 163 shapes.

The pass now keeps the bones a vertex already has and gives newcomers only the
slots that are genuinely free, so nothing can be displaced this way. Measured
on a 38-mesh armour set: 21 stranded bones down to none, and 361 vertices whose
weights did not add up down to none. No vertex moves, and how the armour
follows the body at the bust is unchanged. Setting: "Fix stray broken vertices
on layered garments" (Armor → Fit and clearance), now on.

This rode into the 2026-09-09 pack and so is covered by that pack's verdict
below; nothing in the report singled it out either way.

### Changed (on by default) — stacking layered armour no longer pushes it into the body

When an outfit's layers are stacked on each other at write time (the "layer
ride"), the ride used to be allowed to push a layer through the body: on the
worst piece 102 of its 110 inside-body vertices were put there by the ride,
after the fit had already finished clean. The ride now refuses any move that
would take a vertex inside the body. 19 pieces improved, none regressed.
Setting: "Stop layered armour being pushed into the body as it is stacked"
(Advanced).

### Fixed — a broken pass no longer ships as a clean one

A 2026-09-01 audit of the code — a working note, so it lives on the `testing`
branch only — found fourteen places where a failing step was swallowed and the
result read as "nothing to do": the chest-chord fix and its surface-deficit
sibling returned "no demand" on any error; three smoothing helpers returned
their input untouched; the shader transplant in the shape writer could ship a
glow shape white and static; the copy path discarded a shape's whole fit chain,
its reskin and its z-fight fix without a word, and shipped a shape with its
SOURCE vertices and skin under a "converted" status when the fitted copy
failed; the physics-XML copy, the stripped-chain-bone check and the output
validator's z-fight census all failed silently. Every one of these now records
the failure on the piece's report line (`PASS FAILED ...`, `UNFITTED ...`),
exactly as the body-swap path already did. No converted vertex, weight or
physics byte changes — only what is reported.

Two new tests pin this: one raises inside each helper and asserts the record
appears; the other forbids any function that is itself a failure label from
swallowing its own body (which is how three of the fourteen hid from the
existing guard).

### Added (off by default) — copied armour can get the nipple-shaped chest clearance

Armour that gets a body built into it has its chest clearance ramp up
toward the nipple, where a live body preset pushes hardest. Armour converted
by copying — a quarter of a pack — ran the same fit step with a flat
clearance instead, so one rule was two algorithms, and a verdict on one kind
of armour said nothing about the other. With this on, the copied kind gets
the same ramp from the same body. OFF by default: it moves chest vertices on
copied armour, so it needs a paired measurement and an in-game look first.
Turn it on with "Shape the chest clearance around the nipple on copied
armour" (Advanced). The switch "Read the author's real fit, not a flat one"
now says that it reaches only armour with a built-in body — copied armour
already reads the author's fit that way.

### Added (development only) — the fit stage table, and a test that holds both paths to it

`FIT_STAGES` in `nif_convert.py` now states, in order, which fit passes each
convert path runs per shape, and — for every stage that runs on one path
only, or is called with different arguments on the two — the recorded
reason (the audit finding or the parity note). A new test derives the real
call sequence from both lifted loops and fails when they disagree with the
table: a stage missing from one path, out of order, or called with
different keywords without a reason. The table runs nothing — the two paths
guard their stages on different state and treat a failing stage differently
on purpose — so this is a contract, not a driver; it turns "a fix landed on
one path only" into a red test.

### Changed (development only) — the two per-shape fit loops are now functions of their own

Each convert path ran its per-shape fit chain as a loop inlined in a
1,500 / 2,300-line orchestrator. Those loops are now `_fit_shapes_copy` and
`_fit_shapes_swap`, lifted out verbatim: each takes one context object
carrying exactly the orchestrator locals it read (16 and 30 of them), and
the loop text itself is unchanged, so a diff of the two functions is now the
complete, readable list of where the two paths differ — the audit's finding
that "every fix has to land twice" has a place to be looked at. The pass map
splices each loop's rows back in at its call site, so it still reads as one
ordered chain per path. Every converted vertex identical.

### Changed (development only) — first module split out of the converter

The run-telemetry recorders (`_note_pass_failure`, `_note_pass_effect`, the
per-piece and per-process summaries) and their state now live in
`src/nif_convert_telemetry.py`, imported by name into `nif_convert` so every
existing caller, test and tool keeps its `nc.<name>` handle and the state
objects stay the same instances. A pure leaf: nothing in it calls back into
the converter. Proven by the golden set: every converted vertex identical.
The skin-frame helpers (skin space <-> world space, the ebony-cuirass fix)
followed the same way into `src/nif_convert_skinframe.py`, and body /
ShapeData / preset discovery with the mod-tree lookups and their caches into
`src/nif_convert_bodyrefs.py`, BODYTRI / morph-TRI generation with the
body-morph caches into `src/nif_convert_trigen.py`, the whole per-shape fit
chain (warp, inflate, conform, anti-poke, snap, panel rigidity and the knobs
those passes take as defaults) into `src/nif_convert_fitgeom.py`, and the
bust work (nipple map, breast chain levers, chest follow target, bust
collider split, bust-plate follow sync) into `src/nif_convert_bust.py`,
layered cloth (depth separation, stack grouping, the layer ride and order
repair, the cross-shape seam weld) into `src/nif_convert_layers.py`, every
skinning and weight pass (the reskin, scale-bone graft, the motion matches,
the weight conform, roughness cap, SMP boundary hold, jiggle sync and
transfer, the bone predicates) into `src/nif_convert_weights.py`, and the
HDT-SMP physics work (skeleton caches, physics-XML lookup / bind / sanitise /
harden, chain anchors, collider proxies, the physics finaliser) into
`src/nif_convert_physics.py`, and the NIF writer with its per-shape repairs
(shape copy with shader transplant, skin installation, the fresh re-author,
partition normalisation, shape-order restore, coherence / normal / winding
repairs, vertex-colour flags, z-fight detection and the output validator)
into `src/nif_convert_writer.py`. `nif_convert.py` went from 30,419 lines to
13,931 at the split, and ships at 15,210 as the rest of this release landed
on it: the two convert paths and their orchestration.
Anything a moved function still needs from `nif_convert` is looked up when
it runs, not when it is imported, and `nif_convert` re-executes every split
module when it is itself reloaded — so flags, caches and the tests' way of
flipping them all behave exactly as before. A test now refuses any
test that patches a moved function on `nif_convert` when the code that
calls it lives in the sibling — the one way a move can pass the suite while
a test silently stops faking what it thinks it fakes; tests that need such
a fake patch every module that binds the name through one helper.

### Changed (development only) — the guards now watch a declared list of files, not one

Every structural guard on the converter — the generated pass map, the
two-path parity walker and the three scans for swallowed failures — resolved
"the converter" to the single file `src/nif_convert.py`. A function moved to
a sibling module would have dropped out of all of them with the suite still
green. They now read one declared module list (`scripts/pass_map.py`), keep
a floor of rows and reachable helpers per entry point so a loss is loud, and
a new test pins the list against the files on disk, forbids module-level
state in a sibling, and imports each module on its own. The parity test also
stopped re-splitting the 1.2 MB source per function, which was costing about
half the suite's running time. This is the groundwork for splitting the
file; nothing has moved yet.

### Added — a run now says which build it is and what it resolved every setting to

Twelve different builds have shipped calling themselves "1.3", and the only
configuration a run wrote down was the list of environment overrides — so a
default promoted in code and a setting silently lost looked identical in the
log. Every run now prints, right after the flag echo: a build line (version,
git commit, dirty flag, build time, sha256 of the exe) and the effective
value of every setting that differs from its default; `convert` prints it
too, not only `auto`. The same block goes into `conversion_report.json`
(`run_config`) and into a new `conversion_settings.json` next to the summary,
so a pack carries its own recipe. `scripts/build_exe.ps1` writes the stamp
just before PyInstaller runs, and now pins the build's hash seed as well as
the runtime's.

### Fixed — the settings file can no longer be lost quietly

`install_mo2_entry.ps1 -InstallTools` deleted the whole tools folder —
settings, backups and logs — before copying the bundle; it now goes through
`deploy_exe.ps1`, which never deletes what is already there and takes a
timestamped snapshot of the settings file first (newest ten kept). The GUI
saved the settings file in place on every control change; a torn write
loaded as pure defaults and the next toggle wrote those defaults back. It
now writes atomically, keeps the previous good file as `.bak`, and a
malformed file is announced at the top of the run instead of read as
"all defaults".

### Fixed — a single-piece convert now fails the way the batch does

`scripts/convert_one_armor.py` exited 0 whatever happened to the piece, and
two A/B tools trusted that exit code, so a shape whose fit had failed could be
scored as geometry. It now exits 3 when a shape was dropped and 4 when a pass
failed or a shape shipped unfitted, after both weights have been converted.
Five gate tools that reported PASS on an empty measured set now exit 3 with
"measured NOTHING", and the tool index no longer credits a tool with a
population floor because its docstring mentions one.

### Added (off by default) — robes with long sleeves are no longer mistaken for gauntlets

Armour whose sleeves reach the hands is weighted to the hand bones, exactly as
a gauntlet is — and the converter was treating it as one. A mistaken piece
skips the entire fitting stage: no conforming to the body, no pushing clear of
it. It ships wherever the first rough pass left it.

The worst chest-clipping piece in the whole pack turned out to be one of these:
a robe that received three fitting steps instead of eleven, and showed skin
through the chest on every body preset.

Telling the two apart is simple, and it mirrors a check already here for legs
(a full-length trouser was being mistaken for a boot the same way). A gauntlet
is a tube around the forearm and carries no weight at all on the spine; a robe
hangs from it. Across 173 pieces every real gauntlet, glove and boot measured
exactly zero spine weight, so none of them is touched. Three sleeved garments
are.

On the worst one the chest went from 8.9% of the area showing skin to none at
all, on every preset tested, and it now sits at a normal fitted distance from
the body instead of being pulled too tight. Real gauntlets and boots came out
of the change without a single vertex moved.

OFF by default, pending an in-game verdict. Turn it on with "Treat a
long-sleeved robe as clothing, not as a gauntlet".

### Added — a switch for a step that never had one

*Limit groove smoothing to the author's own clearance* (Advanced) is ON, as it
has always been, and now appears as a checkbox.

After the converter smooths the creases the body presses into cloth, this step
stops that smoothing pushing a vertex further out than the author's own garment
sat. It shipped on from the day it was written and had **no switch at all**, so
no build could be run without it and nothing could measure what it was worth.

Now that one can: over 252 shapes it changes how much skin shows on **three**
of them, moves the average garment by under a hundredth of a unit, and slightly
increases the number of places the surface folds through itself. Its effect
also *shrinks* when the step feeding it is given correct information, which is
what you would expect from a limit that has been reading a blank input.

Nothing changes in this build unless you untick it. It is left on because
turning it off is a change to how armour is shaped, and that deserves its own
build and its own look in game rather than riding along with everything else.

### Added (off by default) — physics files that name what does not exist are trimmed

Ninety-four converted meshes point at a physics file that names shapes or
bones the mesh does not carry. The physics engine tolerates that (the cloth
still swings), but every collider guard in the converter then runs against
an empty set, so nothing it is meant to protect is protected. With this on,
the converter removes the entries that cannot resolve and keeps the rest,
and records what it removed. OFF by default, pending an in-game verdict.
Turn it on with "Repair broken physics files some armour mods ship".

### Fixed — a physics warning that cried wolf

The check for "this armour's physics file names a bone that doesn't exist" was
looking only at the bones the armour is skinned to, and not at the rest of the
skeleton stored in the file. The first link of a physics chain normally carries
no skin weight at all, so healthy chains were being reported as broken: 1839 of
4449 warnings across a third of the affected pieces were false. Those warnings
exist to make a handful of real problems visible, which they cannot do buried
in noise.

### Added — the chest fix, on armour converted by copying

The chest fix below only runs on armour that gets a new body built into it.
About three quarters of the pack is converted by COPYING the original mesh
instead, and none of those pieces were getting any of it.

Measured across the pack on 2026-08-27, the defect is just as common on the
copied pieces as on the rebuilt ones — 28 percent of pieces either way — so
roughly fifty pieces carry it today with nothing applied. The exclusion had
been recorded as "out of scope", which was never the same as "these are fine".

This applies only the push-away half of the fit, never the pull-in half:
pulling copied pieces in toward the body was measured separately and made
clipping worse, so that stays off.

Across three copied pieces and five body presets: clipping fell by up to 5.5
points, nothing got worse anywhere, and every preset that was already clean
stayed at zero. The armour did not balloon to get it — the widest gap shrank on
all three pieces.

It shipped off by default while it waited for an in-game verdict, and is ON by
default in this release — see the promotion entry at the end of these notes. The
switch is "Keep the bust covered on armour converted by copying" (Armor ▸ Fit and
clearance) if you want it off.

### Fixed — skin showing through the chest on about half of body presets

Reported on one cuirass: the breast came through the chest armour on some body
presets while others looked perfectly fine, and the heavy version of the same
armour showed a milder form of the same thing. Nothing looked wrong with the
character standing at rest and no preset applied, which is why several earlier
attempts to find this came back empty-handed.

The armour does follow the body's sliders, and every corner of it follows
correctly. What fails is the flat surface BETWEEN three correct corners. One
triangle of that cuirass spans about seven units across the breast, and its
three corners follow points on the body that a preset can move three and a half
units differently from each other. The flat triangle cuts the corner across a
breast the preset has just made rounder, and skin appears between corners that
are each individually in exactly the right place.

The fit now accounts for that, so the surface between the corners gets the
clearance too, instead of only the corners.

Measured across six pieces and fourteen body presets: 39 cases improved, none
got worse, and all 22 that were already clean stayed exactly as they were. On
the reported cuirass the chest went from 2.9% of the band showing skin to 0.8%,
and the fuller body presets improved most — which is where it was reported.

### Fixed — straightened armour plates could sink into the body between corners

Layered armour is straightened back into plates after fitting, and that is only
allowed while the plate stays clear of the body. But the check looked at the
plate's CORNERS and nothing else, and it let a corner standing well clear drop
all the way down to touching. Two corners at zero with a curved body between
them puts the surface itself inside the body, and the anti-poke pass then had to
repair the damage afterwards.

The check now looks at the middle of each triangle as well. It can only ever
make the straightening gentler, never stronger.

On a college robe this took the chest from 13.0% showing skin to 3.5%, and its
at-rest clipping from 1.5% to none — while the armour sits slightly TIGHTER to
the body than before, not further out. A body preset that had been clipping that
robe comes out clean.

### Not changed — a related switch that measurement says to leave alone

*Keep plates straight without pressing them into the body* (Advanced) stays off. It is the
obvious companion to the fix above, so it was re-tested against it, because its
original verdict predates it. It still helps one piece and hurts two.

### Fixed — some outfits shipped a collision shape the physics engine could not use

The converter builds a hidden collision copy of a garment so the bust has
something to collide against. On a few outfits that copy inherited a bone the
outfit's own physics file never mentions — a finger bone on a coat, an arm-twist
bone on a cuirass. The physics engine cannot resolve a bone it was not told
about, and the piece falls off the character.

Ten such copies across nine outfits. Six are repaired by moving the weight onto
the nearest bone the physics file DOES mention, which is invisible in the bind
pose: an untold bone is not simulated either way, so nothing moves. The other
four had no usable bone to move to, and for those the hidden copy is simply not
made any more — those outfits go back to the arrangement their own author ships,
which is the one their mod is known to run. Those four lose the bust collision
improvement; that is the trade, and a falling outfit is worse.

Only copies the converter itself creates are touched. Where an author's own
shape carries an unmentioned bone, that is their arrangement and it is left
alone.

### Added — two switches so a change can be tested on its own

Neither changes anything by default.

*Reel over-projected cloth back to its authored clearance* has always run on
body-replacing outfits and still does — but it could not be switched off, so it
could never be tested. It now can. It moves more than any other fitting step and
keeps the least of what it does, while three quarters of your armour runs no
equivalent at all and looks right, so whether it still earns its place is a fair
question. (This is a DIFFERENT pass from "Conform fitted cloth to body", which
already had a switch. The names are unhelpfully similar.)

*Also keep plates straight on gauntlets, boots and heels* is off, and new.
Gauntlets and boots take a separate route through the converter that skips the
plate-straightening entirely. Measured on 51 such shapes, the pass would find
568 plates there — so it is real work being skipped, not nothing to do. It is off
because it has no in-game verdict yet: turn it on for a build of its own, look at
your gauntlets and boots, and report back. Fingers and toes are left alone.

### Added — the run now records which change touched which outfit

When something looks wrong in game, the useful question is which of the build's
changes could have caused it. Each change now leaves a mark on every piece it
alters, and those marks survive into the run report. "What broke" and "what
changed" are reported separately, because a change that alters three hundred
pieces and breaks none is working, while one that alters nothing is not
reaching anything.

### Fixed — eight armours had no cloth physics at all

Eight pieces shipped a physics file the engine could not read, so they simply had
no physics in game — two of them cloaks, which is where you would notice first.

The cause was an encoding round trip. When the converter adjusted an author's
physics file it read the text using Windows' regional default and wrote it back
as UTF-8. For a file that begins with the invisible marker most editors put there,
that marker was re-encoded into six bytes of garbage sitting *before* the opening
tag, and the file stopped being valid XML. Nothing reported it, because the file
was still there and still looked fine in a text editor.

Fixed by reading and writing the file in the same encoding. If you keep old
output, those eight files are repaired by reconverting.

Ten other unreadable physics files remain and are **not** ours — they end with
stray text the original mod author left in. We copy those faithfully rather than
silently rewriting someone else's file, and they have no physics in the source
mod either.

### Fixed — "keep layered armour plates straight" only worked on a quarter of your armour

Armour built as one piece with many rigid plates gets each plate held straight
rather than bent around the body. That only ran on armours the converter rebuilds
around a new body — about a quarter of a typical pack. The other three quarters
silently went without, so the same setting behaved differently from piece to
piece with nothing to explain why.

It now runs on both kinds. Only the first half moved: the second half exists to
undo damage from a pass that does not run on the other kind, so copying it across
would have invented work rather than matching behaviour.

### Changed — the defaults are now the settings that were actually tested

Four options had been switched on in testing for weeks while shipping switched
off, which meant a fresh install produced something nobody had ever looked at.
They are now on by default: keeping cloth clear of the body on mixed physics
garments, keeping layered plates straight, keeping plate stacks aligned, and
placing each physics chain's anchor separately.

One option from the same set was deliberately left off — it was measured, made no
difference to the problem it targeted, and made two garments worse.

### Fixed — a checkbox for an on-by-default option could not turn it off

Turning something off in the GUI wrote nothing at all, which for a newly
on-by-default option meant "leave it on". Any such checkbox would have been inert.
Fixed before it could ship.

### Fixed — seven passes could fail without saying so

Seven places could hit an error and continue in complete silence, so a pass that
BROKE looked exactly like a pass that had nothing to do. Three of them wrapped
code that already reported failures carefully, and threw that reporting away.
They now record, and the run report lists them.


### Fixed (on by default) — body sliders did nothing on 54 outfits at high weight

An outfit ships as two meshes, one for each end of the weight slider, and the
two share a single morph file built from the light one. Authors routinely give
the two meshes' pieces DIFFERENT internal names, and when they differ the heavy
mesh names nothing in the morph file it points at — so its sliders move
nothing. The heavy mesh is the one that matters, because characters sit near
the top of the weight range.

Measured before the fix: **54 of 1536 outfits** affected — 42 lost every
slider, 12 lost some. After: **0**. The morph file now carries both meshes'
names, so each finds its own.

### Fixed (on by default) — a piece of armour could be deleted for being named like a body

The converter strips placeholder bodies that some outfits ship inside them. It
recognised them BY NAME ALONE. One outfit's cuirass and arms happened to carry
such a name while being real armour, and were deleted — the two halves of that
piece ended up with different vertex counts, which is how it was found.

The name test now also requires the piece to be wearing body skin, which is
what the sibling test beside it had always done. Checked across every source in
a 3567-source pack: 34 pieces match by name, 32 are genuine placeholder bodies
and are still stripped, and the 2 that are real armour now survive.

### Fixed — two identical runs could produce different physics

A piece with no physics file of its own could be given one belonging to an
unrelated garment, purely because that file happened to be the only matching
name in the output folder AT THE MOMENT that piece was converted. The folder is
being written while the run reads it, so the answer changed with timing: two
runs of identical code produced different meshes.

The two places that caused it no longer guess by filename. **This is a partial
fix and the rest is deliberate**: a third place still uses the filename search,
because refusing it there does not make it find the right file — it makes it
find none, and a check that cannot run stops filtering at all. Measured cost of
changing that one: 421 of 769 meshes moved, 62 by bone count. It needs a
decision, not a quiet edit.

### Changed — five settings that were already on are now on by DEFAULT

Five options the shipped recipe had been switching on for months were still
written in the code as off-by-default. Anyone reading the code saw one answer
and the pack shipped the other. The code now says what the pack does for those
five.

**Nothing about a conversion changes** — the settings were already on. The
settings file was then written back out with all five named explicitly, so that
rolling back to a build older than this one cannot quietly revert them: an older
build reads the file and honours it. The backup files beside the settings record
every step.

**That protection does not survive the settings window, and you should know it.**
Saving settings writes back only the values that differ from the code default,
so the moment this build saves them, those five names drop out of the file
again — measured on the release pack: the file the conversion ran with named all
six, and the file five minutes later named one. Nothing about a conversion
changes either way, because on this build all five ARE the default. It matters
only if you roll back to a build older than this one, and then only if the file
has not been saved since. If you want that guarantee, keep a copy of the file
that names them.

One setting is deliberately NOT in agreement: the layer fix below is off in the
code and on in the settings file, because it is a trade this pack takes and not
one every pack should. That disagreement is intentional and should not be
"tidied" away.

(The slider fix above is a sixth setting turned on in this build, but it is a
new one rather than a promotion — it was never in the recipe.)

### Changed (on in the shipped recipe, off by default) — half as much armour is left inside the body at the bust

Near the end of a conversion the layers of an outfit are put back in the order
their author gave them — the shirt inside the belts, the belts inside the coat.
Three small repairs then run *again* as the piece is written out: un-buckling a
strap, evening out one that has been stretched, and pulling back detail that has
been blown up. On a belt those repairs move three quarters of its vertices, so
whatever the ordering step had just settled, they unsettled.

This build runs those repairs first and the ordering last, so nothing moves a
vertex after the layers have been put in order. The switch has been in the
settings window since August with only the one reported garment behind it, and
was absent from this changelog entirely. It has now been measured across ten
mods (479 meshes), and **the pack is built with it on**. Setting: "Let the layer
fix have the last word" (Armor → Fit and clearance), now on in the shipped
settings file.

What it buys, on that population:

- Armour left sitting inside the body at the bust: **108 vertices down to 56** —
  halved.
- On the worst tenth of pieces, the share of the bust band coming through the
  armour with the body at rest fell from **3.3% to 1.3%**.
- Vertices lit as though the surface faced inward: 2612 down to 2447, **6.3%
  fewer**.
- The bust ends up very slightly *closer* to where the author put it, not
  further off.

On the cuirass this was reported against, the bust band coming through at rest
fell from **5.3% to 3.6%**, and the write-out step that had been burying **14
bust vertices in the body** now buries none. That last figure is one piece, not
a class: two more pieces of the same kind were checked and neither had any
vertices buried that way to begin with.

**What it costs, and it is not nothing.** Seven pieces end up tighter over the
nipple than they were — the middle of the range there goes from 1.07 units of
clearance to 1.03. On the worst tenth of pieces, the share of edges stretched
further than the author left them rises from 1.70% to 1.72%. Places where the
surface folds through itself rise 0.03% across the pack, 30542 to 30552.

And the honest limit: **the number of pieces that clip did not fall at all.** The
same 23 pieces come through at rest and the same 46 come through under a body
preset, out of 92 scored in both configurations — what changed is how much they
come through, and under a live preset that improved by only about a percent.
This is a fix for how armour sits at rest far more than for what a full slider
does to it.

The offline scoring calls those four measures a regression and it is being
shipped anyway, deliberately: it is the only change measured this cycle that
moves how much armour ends up inside the body at all, the losses are fractions
of a percent on surface quality, and it points the same way as the report that
started it — armour standing too far off the body — by closing the gap to the
author rather than widening it. Until this build the scoring could not see
clipping under a body preset at all, so a change that improved it and cost
anything else could only ever be marked down.

**Out of the box it is still off.** The default in the code is unchanged, because
on those four measures it does not earn a default; the pack is built with it
ticked because on this pack the trade is worth taking. If you convert mods
yourself with a fresh install you are not running the configuration the pack was
built with — tick it on the Armor tab to match, or leave it off if you would
rather keep the surface exactly as it was.

**No in-game verdict on this one yet.** It is on so that this reconvert produces
one. The reported cuirass is improved, not fixed. If something looks pinched or
flattened over the chest where it did not before, untick it and say so.

## 1.3 — 2026-08-19

Everything below shipped in the pack built on 2026-08-19: 162 mods, 3931
meshes, no failures. The headline is that conversion is now **reproducible** —
converting the same mod twice produces the same bytes — which is what makes
every measurement in this release trustworthy rather than approximate.

### Fixed — converting the same mod twice now produces the same meshes

Two identical runs disagreed on 29 of 84 meshes, by up to 3.4 units. Nothing was
wrong with either result; the converter simply had no reason to pick the same one
twice. That made measuring any change impossible — a fix worth half a unit could
not be told apart from the noise — and it meant a rebuild of your armour could
differ from the one you approved.

The cause was the order the converter walked its own internal name lists. That
order is deliberately unpredictable in Python, it changes every time the program
starts, and here it decided the order things were written into the file. Two
sources of it are now closed: the order is fixed for the whole program, and the
handful of places where it reached the output are sorted.

**Underneath sat a worse defect that this uncovered.** Every armour is built in
two weight variants, and three internal caches stored a body measurement under a
key that could not tell the two apart. Whichever variant a worker happened to
build first pinned that measurement for every piece it built afterwards — so the
low-weight version of an outfit was routinely fitted against the *high-weight*
body's shape. It affected 14 of 84 meshes on the test mod, every one of them the
low-weight variant, and moved a fitted torso by as much as 2.2 units. Slim
characters were getting armour cleared for a body they do not have.

Converting the same mod twice now gives byte-identical results, and the same is
true across the 16 parallel workers a real run uses — with one source of
disagreement still open, described under 1.4 in "two identical runs could
produce different physics".

### Fixed — a single vertex bending the wrong way

Reported in game on a leather panel above and below its belts: one vertex pulled
against the surface around it, poking through the layer over it.

A vertex can be attached to at most four bones. Making an outfit follow the body
fills nearly every vertex to that limit, and where the result is a near-tie for
the fourth place, neighbouring vertices can end up keeping *different* bones. The
surface then bends at the waist while one vertex in the middle of it bends at the
chest.

Weighting is now held to the smoothness the original author gave it, and eased
back toward its neighbours only where ours came out rougher than theirs. It is
measured against the author rather than against perfect smoothness on purpose:
flattening everything would erase the panel edges and seams the author put there
deliberately. On the reported outfit this fixed 802 of 830 rough vertices, and the
belts, buckles and metal buttons — already smooth — were left completely
untouched. Nothing moves; only weighting changes.

Two likelier explanations were measured and ruled out first: the way vertices are
paired to the body is not at fault here, and no vertex was shipping underweighted.

### Added — four more settings that could not be switched on

Same class as the two below: finished work the settings file had no entry for, so
no run could reach it however it was described elsewhere.

- **Cap how far the upper-back allowance may push.** The allowance that keeps the
  upper back covered raises cloth in one step, and where the garment already sat
  close it overshot — the measured piece stood 2.12 units off the back where its
  author put about half that. Capping it brought the standoff to 1.16 while
  showing *less* skin than before, not more. Roughly the worst tenth of pieces.
- **Match a top's twist-follow to the body**, and **let rigid bust plates ride the
  breast chain.** Both are experiments with real measurements behind them and no
  in-game verdict, because until now no run could turn them on to get one. Both
  ship off.
- **Keep weighting as smooth as the author made it** — the fix above, on by
  default.

### Fixed — an empty setting could stop the run

One numeric setting crashed the converter outright if it was set to an empty
value rather than left alone. It has a row in the settings window, so this was
reachable by hand-editing a recipe. It now falls back to its default like every
other setting.

### Fixed — physics cloth no longer falls away from the body, and the collider that caused it is kept

Reported in game as legs sinking away from the body and falling forever, on
vanilla iron armour. The standing workaround was to switch the added buttock
collider off, which also gave up the buttock clipping it exists to fix. Both are
kept now.

**The cause was the collider's bone list, not its shape.** The collider copies the
body's skinning wholesale, and that drags in the body's jiggle bones. The piece's
own physics file never declares those bones, so half the collider's influences had
no counterpart in the system it was registered into. Every collider the *author*
ships is weighted only to bones that file declares — that is the rule the
generated one broke. It now hands each undeclared bone's weight to the nearest
declared parent on the actual skeleton, which leaves the collider in exactly the
same place (the geometry comes out identical to the vertex) while giving the
simulation something it can resolve. It fires only where the rule is actually
broken: the piece this work was originally judged on redirects nothing, because
its file already declares those bones.

Found by removing the collider's *declaration* while leaving the shape in the
mesh. The cloth settled, which put the fault in the registration rather than the
geometry — no amount of measuring the mesh would have shown that.

**A second, separate defect: every generated collider shipped with broken binds.**
Adding a bone to a shape silently resets the bind transforms already on it, so
building them one at a time left only the last bone correct — **174 of 176 shipped
colliders**, sitting a median 72 units from the body at runtime against 0.02u for
the authored ones. Now built the way the rest of the converter does it. Real and
worth fixing on its own, but it was *not* what made the cloth fall; both had to be
fixed.

**The collider also sat too far off the skin.** Its 0.6u standoff had been tuned
by eye against a collider that was misplaced at runtime the whole time, so that
tuning measured nothing. It is now taken from where each piece's own rear cloth
actually rests, capped so it can only ever come down, and floored so it never sits
inside the skin. On a close-fitting piece that reported an inflated rear it drops
to 0; on a flared skirt it is unchanged.

### Added — two settings that were previously impossible to switch on

Both were finished, verified in game, and unreachable: the converter reads its
settings file, and a setting with no entry there cannot be turned on however it is
described elsewhere. A full reconvert would have quietly run without them.

- **Place each physics chain's anchor separately.** Anchors are placed in one pass
  for a whole outfit, and that pass gave up entirely if any one chain hung from the
  upper body. An outfit mixing a hip chain with a small chest chain — common — got
  nothing placed, and every chain landed about 69 units low, at floor level.
  Measured on vanilla iron armour: 38 chain nodes, most below the ground.
- **Give the fitted parts of physics outfits body clearance.** Clearance is skipped
  for anything carrying physics rigging, because moving simulated cloth fights the
  simulation — but one piece often holds both a simulated skirt and an ordinary
  fitted top, and the whole piece was skipped on account of the skirt. Across the
  pack, **392 of 482 skipped pieces are mixed like this, 57% of their vertices
  carry no physics weight at all, and 198 pieces have chest cloth that no clearance
  could reach.** Only vertices with no physics weight are moved; every simulated
  vertex is put back exactly where the simulation expects it.

The anti-poke bust target is adjustable now as well. Raising it loosens the bust
on most pieces, so it is left at its existing value.

### Fixed — bust layers no longer swing through each other, and a sheer layer sits on the skin again

Two defects on the chest, both reported in game, both now default behaviour.

**The layers moved independently.** A garment's weights are matched to the body's
so it travels with it — right for arms and legs, wrong for the bust, where an
author deliberately gives an inner layer *more* breast follow than the body so it
hugs. Every layer was converging on the body's own share instead, so an inner
layer out-travelled the one over it and swung out through it whenever the breasts
moved. Measured on one robe: the author's 0.681 / 0.465 / 0.463 shipped as
0.355 / 0.229 / 0.083. The first write is innocent — two *post-write* weight
passes took them. Those passes now refuse to lower a row's breast share, which is
the rule one of them already stated for its own bone family. Restores
0.676 / 0.519 / 0.481 and **moves no vertex at all**, so every bind-pose fit
stays exactly as it was. Verified in game under motion, the only place it shows.

**A skin-tight layer stood off the body.** A sheer, alpha-blended bodysuit its
author holds a median 0.121u off the skin was shipping at 0.79u — far enough that
it reads as a dark shell over the chest instead of a tint on it. The chest
conform carries its own clearance pair, separate from the anti-poke target
everyone reaches for and unreachable from it, and that pair is what pins the
layer there; lowered from 0.3/0.9 to 0.12/0.3. The measured per-slider morph
charge and the anti-poke's own bust target are unchanged, so the headroom that
protects against poke-through is still there.

Both were gated per region against the previously shipped mesh across seven
pieces, counting garment vertices driven *inside* the body. No region worse on
any piece; most better — one robe's shoulder 96 → 48, its arms 60 → 44, its bust
11 → 2. All three settings are on the Armor tab and can be turned back off.

### Fixed — sub-millimetre detail on metal fittings was being blown up

Reported on a belt's buckles. The fit stretched authored edges of 0.02–0.04u — a
tight seam, the rim of a stud — to as much as **0.41u, an 18× blow-up**, which
reads as a spike or a tear on a small plate. The seam weld misses these because it
welds *coincident* verts, and 0.023u is far above its tolerance while still being
detail that must move as one piece.

Such an edge may now stretch only in proportion to the garment's own growth. The
correction moves both ends toward each other by equal and opposite amounts, so
every corrected edge keeps its midpoint and a fitting cannot be relocated — only
un-stretched. Worst blow-up on the reported piece **+0.391u → +0.087u**, touching
23 vertices of 2938 and nothing else on the garment.

Found only after the edge-distortion metric was checked for contamination: 1% of
that shape's edges were inflating its mean score by 75%. See `docs/METRICS.md`.

Opt-in as `CBBE2UBE_SHORT_EDGE_CAP` pending a pack-wide verdict.

### Changed — a layer can follow the surface point it rests on

`Ride layers on reference` moved each layer by the inverse-distance blend of its 8
nearest reference *vertices*, so a fitting followed its neighbourhood's average
motion rather than the motion of the spot it actually sits on. Where the surface
beneath moves unevenly, those differ, and the gap drifts.

It can now use the barycentric correspondence instead — the exact surface point,
taken from the author's geometry and replayed on the output. Unlike the k=1 ride
this does not reintroduce spikes, because a point on a triangle moves continuously
where a nearest-vertex snap jumps: spikiness **12.8 → 11.3**, and every fitting on
the reported piece moved toward its authored depth (the top plate 0.170 → 0.139
against an authored 0.132).

Opt-in as `CBBE2UBE_LAYER_RIDE_BARY`. It does not hold the authored distance
*exactly*, and `docs/PIPELINE.md` records why that is not reachable from here.

### Fixed — belts and straps came through crumpled

Reported in game as belts "visibly distorting". Edge length is what separates the
two things that look similar: a belt wrapped round a wider body **bends**, and
bending keeps every edge the length the author drew, while **stretching** does
not. On the reported piece the strap's edges ran from **0.52× to 1.51×** the
author's, a mean deviation of 0.219 against 0.060 for the chest plate on the same
garment, with 78% of its edges off by more than 5%.

The repair gives every edge the length its **neighbourhood agrees on**, so the
garment's overall growth survives untouched — that strap's median edge is 1.025,
and a belt on a wider waist genuinely should be slightly longer. Only the spread
is removed: deviation 0.219 → 0.128, with arm and bust follow unchanged and the
worst inter-shape clip down 65%.

Three refusals keep it a repair rather than a resurfacing: a shape must be
measurably crumpled to qualify (16.2% of shapes pack-wide), a shape whose median
edge is more than 1.5× the author's is **mis-scaled rather than crumpled** and is
left alone, and small fittings — studs, buckles, rivets — are excluded, because a
cluster of separate objects has a scale that legitimately varies between them.

Opt-in as `CBBE2UBE_STRAP_SCALE_UNIFORM` pending an in-game verdict.

**Not a regression:** 1.2 measures 0.224 on the same strap. It has always looked
like this.

### Changed — the options you were actually being judged on are now the defaults

Three fit options shipped **opt-in and off** while every in-game verdict on this
project was given on a build that had them forced **on**. Production output was
therefore not the output anyone had looked at: on one five-layer test piece the
difference reached **1.38u**.

Each was re-decided by measurement rather than by restoring the status quo, and
they did not all come out the same way.

**Reconcile stacked layers — now ON.** Verified in game before the default was
set: back, sleeves and bust all confirmed good on the reported piece, with arm
follow identical to this option off and bust follow better than off. Off-switch
`CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD`.

**Cap isolated warp fliers — now ON.** Judged on a quantity it does not optimise:
it caps a vertex's deviation from its neighbours, so it was scored on the
**absolute** irregularity of the final surface, for the vertices it actually
touched, against the author's own value for those vertices. It acts hardest
exactly where the defect is worst — on a steel cuirass the skirt's worst spike
fell from 10.34u to 6.44u, with four more shapes improved. Where it does not
help it costs about 0.03u. A 3.9u spike is a visible broken vertex; 0.03u is not.
Off-switch `CBBE2UBE_NO_WARP_DELTA_OUTLIER`.

**Cap the warp push at the garment's own shell — stays OFF.** Measured the same
way and it did not earn its cost: zero vertices touched on every shape of that
cuirass carrying a large spike, marginal gains where it does fire, two shapes
regressed — and it alone accounted for the 1.38u geometry change above. Still
available as `CBBE2UBE_WARP_PUSH_SHELL_CAP`.

**A reconvert is required** for any of this to reach an existing pack.

### Fixed — chain-driven skirts (and vests, robes, belts) stretched to the floor

Reported after 1.3-alpha as skirts looking "stretched". The physics chain a
garment hangs from was being written **detached and at the character's origin**,
so every vertex weighted to it was dragged down between the feet.

Measured on a chain-driven skirt, 1.2 → 1.3-alpha → fixed:

| chain bone | 1.2 | 1.3-alpha | fixed |
|---|---|---|---|
| root | z 79.80 | z 11.57 | z 80.48 |
| next | z 84.01 | z **0.00** | z 84.69 |
| next | z 85.75 | z **0.00** | z 86.44 |

7,379 of that shape's 9,290 vertices hang off those bones.

The cause was a deleted line. `_precreate_custom_bone_chains` used to recreate a
chain's anchor when it was missing; that was removed along with a genuinely
unfixable case (an anchor that already exists cannot be corrected in place).
But the chain writer can only attach a bone once its **parent** exists — so with
the anchor gone the first bone never qualifies, the loop gives up, and the skin
installer then creates every chain bone flat at the origin. One missing anchor
costs the entire chain, not one bone.

Censused over a real 1,886-piece output: **44 of 446 chain-carrying pieces, 594
bones, 167,920 vertices** — skirts, two vests, a Skaal torso, Creation Club
robes, belts and accessories.

The repair only ever creates an anchor that is **absent**, so it is a no-op
wherever the chain already survived: an unaffected cuirass reconverts with
0.000000 vertex movement, 0.0% of weight mass moved and no bone changes. Nothing
about geometry or skinning changes on the repaired pieces either — the fix
touches only the bone hierarchy.

Escape hatch `CBBE2UBE_NO_CHAIN_ANCHOR_RECREATE`. The regression it repairs
shipped with no flag of its own, which made it far more expensive to find; this
one is switchable so it can be A/B'd.

**A reconvert is required** for this to reach an existing pack.

### Fixed — layers of a multi-layer garment clipped through each other

Reported after 1.3-alpha on a cuirass built from five stacked pieces: the layers
began passing through one another (not through the body).

`Match armour skinning to the body it covers` copies the covered body's whole
weight row into each shape **independently**, and two layers stacked 1-2u apart
did not get the same answer — so they stopped deforming together. Measured as
the mean weight-row difference between stacked vertex pairs, 1.2 → 1.3-alpha:
0.190 → 0.309 on the two main layers, and 0.027 → 0.204 on the worst pair.
Switching the pass off restored every pair to its 1.2 value, which is what
identified it rather than merely implicating it.

The mechanism is that the pass casts a ray from each vertex along **its own**
normal, so stacked layers land on different body triangles by construction. Each
stacked group now resolves the body through one shared anchor, and it does so
**only where the layers actually overlap**, through the innermost layer it can
reach. Four of the five pairs sit at or below their 1.2 values (0.013 / 0.000 /
0.000 / 0.035), with follow intact rather than merely claimed: on the reported
piece arm follow on the outer layer is identical to this pass off (3741.2) and
bust follow on the plate is better than off (1064.2 against 1008.9). The fifth
pair is worse than 1.2 (0.259 against 0.190), and the reason is that the two
layers end up on **different bust chains**: the outer layer keeps the author's
two bones (L/R Breast01) while its neighbour receives the body's three-segment
chain (Breast01/02/03). The author had every layer of that garment on the same
two bones, so they moved together; on different chains they cannot. Sharing an
anchor does not fix that, because it is the bone set and not the pairing.

Both locality conditions were learned the hard way. A first version substituted
the shared anchor for **every** vertex of a grouped shape, so a sleeve 20u from
the chest plate borrowed a torso anchor; in game that read as bound sleeves, a
bust that stopped following and a back that tracked the torso rigidly. It scored
*better* on divergence while doing so, which is why every divergence figure here
is quoted next to a follow figure.

Groups are detected by **coverage**, not contact: a layer shadows a large share
of its neighbour, while a buckle or a trim strip only touches one along its
border. Colliders, soft-body shapes and injected body parts are kept out of a
group entirely.

Single-layer pieces are untouched — the leather cuirass whose fit was confirmed
in game reconverts byte-identical, 0.000 vertex movement and 0.0% of weight mass
moved.

**On by default**, verified in game before that default was set. Escape hatch
`CBBE2UBE_NO_FULL_WEIGHT_LAYER_GUARD`. The stricter variant that also forces a
stacked group onto a shared bone basis is **off by default**: it drives the
divergence lower still, but destroys ~87% of the jiggle follow and makes
body-follow 7x worse than either baseline.

**A reconvert is required** for this to reach an existing pack.

### Changed — a fit pass can no longer fail without saying so

Most fit passes end by returning the number of vertices they changed, where 0
means "nothing qualified". Seven of them wrapped their final save in a handler
that swallowed the error and returned 0 anyway — so a **lost write looked
exactly like a clean no-op**: the pass reported success, the run report showed
nothing unusual, and the piece shipped unmodified.

That is not a hypothetical failure mode. Two defects reached players through it:
a pairing step that raised on every call while producing a byte-identical mesh,
and the chain bug fixed above.

Those seven now report through the run's normal failure channel, so a failed
save appears as a `PASS FAILED` line instead of silence. Two further handlers
the audit flagged were left alone — they already report by another route.

Nothing about the output changes: these paths only execute when a save has
already failed.

### Changed — faster: the morph-TRI lookup is no longer repeated

Seven passes ask whether a shape is covered by the source mod's own morph TRI,
and each question re-read and re-parsed the whole file. Profiled on a five-layer
outfit: twelve reads costing 14.0s, now 0.95s.

Honest about the scale of this: the saving inside that lookup is measured and
solid, but a single before/after run showed only a ~1% change in total
conversion time, with unrelated work drifting by more than that between the two
runs. Treat it as one repeated cost removed, not a promise of a faster
reconvert. Output verified unchanged.

## 1.3-alpha — 2026-08-10

**Pre-release.** Everything below is built and tested, and the headline fits are
confirmed in game, but this build has not had a full play-through. Treat it as a
testing build: keep your previous output mod so you can roll back.

**A reconvert is required** for any of this to reach an existing pack — nothing
rewrites meshes that are already built.

### Fixed — forearm and calf armour was invisible on UBE

A piece whose ONLY biped slot is 34 (forearms) or 38 (calves) got no UBE
armature from anywhere, so it equipped and rendered nothing. Two winner-scan
passes divide coverage by slot: the non-body pass skips anything with a
deforming slot, and the body pass handled only torso plus pure hands/feet,
leaving the rest to the per-source builder. Unified coverage then made the
winner scan the sole generator and stopped merging the per-source patches — so
the fallback that arrangement depended on no longer ran, and forearms and calves
fell between the two.

Measured on a real pack: **all 51 slot-34-only and all 7 slot-38-only armours**
had no link. Reported as "the arms are equippable but invisible".

Admitting them needed no new safety gate — minting already requires a
DefaultRace armature and a converted mesh, which is what keeps a non-body mesh
from being handed UBE body races. Checked against the worst case in the pack, a
modder's tower shield parked on the calves slot: still correctly excluded.

### Changed — four fit/physics options are now ON by default

All four were built default-OFF pending an in-game verdict, and all four have
now had one. **A reconvert is required for any of this to reach an existing
pack** — nothing rewrites meshes that are already built.

* **Match armour skinning to the body it covers.** A CBBE-authored garment
  carries CBBE weighting on a UBE body, so the body slides out from under it as
  you move. This copies the covered body's whole weight vector on the vertices
  that hug it. Bust exposure in motion 50.9% → 3.1% on the test piece; it moves
  no vertices, so resting fit is unchanged. Now at full strength (1.0), which
  was held back only because a garment deforming exactly like skin could be
  wrong for rigid plate — a glass cuirass at 1.0 has since been judged good in
  game beside the leather.
* **Lift physics chains out of the body.** The fix for the long-standing
  buttock clip. A skirt's chain bones keep their source rest position while the
  body grows, so on a fuller body they end up inside it and the solver pulls the
  cloth in every frame while collision pushes out — which is why more collision
  never finished the job. Each affected chain's root is moved out until no bone
  of it rests inside the body. Trade-off: the free-hanging lower skirt moves out
  by the same amount (0.5u on the test piece).
* **Add the missing rear collision surface**, and **let the visible skirt
  collide, not just its proxy.** These attack the same defect from the body and
  cloth sides; both fire only on pieces measured to need them.

Escape hatches, and the Armor tab is the place to use them:
`CBBE2UBE_NO_FULL_WEIGHT_MATCH`, `CBBE2UBE_NO_CHAIN_REST_LIFT`,
`CBBE2UBE_NO_BUTT_COLLIDER_PATCH`, `CBBE2UBE_NO_SKIRT_PROXY_REBUILD`.

If a skirt now looks held too far off the hips, untick "Lift physics chains out
of the body" first.

### Fixed — an armature link can no longer go missing in silence

Every converted armour records a link that attaches its UBE armature to the
armour itself. If that link is lost between the per-mod patch and the SkyPatcher
INI, the piece is **equippable and invisible** — and nothing said so: the mesh
converts, the report says "converted", the plugin carries the armature, and no
other output mentions the piece again. A pack shipped with one mod's 114 links
absent, found only when a user reported a single invisible arm piece.

The merge now accounts for every link and prints the result:

```
armature links: N recorded -> M emitted (x duplicate, y render-identical, z unresolved)
```

`duplicate` (the same armature claimed by several mods, added once) and
`render-identical` (two identical armatures on one armour would render the mesh
twice) are by design. **`unresolved` should be zero**; anything else prints a
warning naming the consequence. An unreadable link sidecar is named rather than
swallowed, and the four numbers must sum to the recorded total, so a future drop
path cannot hide behind the accounting meant to catch it.

### Added — other new options in this build

Not covered above, and previously absent from this changelog entirely:

* **Keep the upper back covered when the body morphs** (`back_morph_residual`,
  **on**) and **base clearance on reshaping, not just growth**
  (`clearance_differential`, **on**). Both are measurement-driven fit
  corrections. **Neither has an in-game verdict recorded yet** — they are on
  because the offline numbers support them, and this is a pre-release partly so
  that gets tested. `CBBE2UBE_NO_BACK_MORPH_RESIDUAL=1` /
  `CBBE2UBE_NO_CLEARANCE_DIFFERENTIAL=1` turn them off.
* **Four warp guards, all default OFF**: stop the warp flinging a lone vertex
  (`warp_delta_outlier`), never push a vertex through its own armour
  (`warp_push_shell_cap`), stop the warp shearing big triangles
  (`warp_shear_limit`), and stop the conform folding the surface
  (`conform_fold_guard`).

  > **Worth knowing before you convert.** Every conversion verified in game so
  > far — including the ones behind the fit claims above — ran with
  > `warp_delta_outlier` and `warp_push_shell_cap` **ON**, because they are
  > ticked in the maintainer's settings. They ship **off**, so an out-of-the-box
  > run is not the configuration that was tested. Ticking both on the Armor tab
  > reproduces the validated build. Making them the default is deliberately
  > deferred to the 1.3 release rather than decided in a pre-release.

### Known issues

* **The fur-set gloves and a few NPC-skin pieces still get no armature.** 30
  slot-34 pieces in one fur mod, plus draugr-beard and Creation Club items, are
  unlinked for a *different* reason than the forearm fix above and are not
  addressed here.
* **A skirt may now sit further off the hips.** The chain rest-pose lift moves
  the free-hanging part of a chain out by the same amount as the part it
  rescues (0.5u on the test piece). If a skirt looks held away from the body,
  untick "Lift physics chains out of the body" and reconvert that mod.

## 1.2 — 2026-08-02

The first release since 1.1.1. Everything here shipped as internal 1.2.x
builds that were never published, folded into one release rather than
presented as eight pending versions.

The theme is **measuring fit correctly, then fixing what the measurement
exposed**. Most of 1.1.x's fit work was steered by two metrics that turned out
to be anti-correlated with what the game actually shows, so a run could score
better and look worse. Replacing them uncovered a frame bug that had been
corrupting the entire phase-2 pass chain, and several long-standing fit defects
turned out to be metrics that did not measure what their name claimed.

### Changed — four fit options are now ON by default

These shipped as opt-in switches nobody was turning on, so the out-of-the-box
conversion was missing fixes that had already been proven. All four have since
run over a full pack and been used in game, so they are the defaults now:

* **Judge chest movement by the outfit's own weighting, not its name.** Decides
  how much a top may move by whether its author weighted the chest at all,
  instead of guessing the material from the file name and textures. It only ever
  adds movement to pieces nothing was helping.
* **Chest follow ratio.** A fitted soft top now tracks the body's breast motion
  by the amount its own clearance requires, rather than a flat cap that left it
  following about a third of the body. Metal keeps the conservative cap.
* **Bust clearance on collider-only SMP armour.** Armour whose physics config
  names it only as a collider was getting no bust clearance at all, so the body
  pushed straight through it. Measured 6.3% → 3.3% exposed on one such cuirass.
* **Fit robes and dresses that declare their own physics.** Draping pieces were
  skipped wholesale; the skip now applies only to those that ship no physics
  file of their own.

Each keeps an escape hatch, and the Armor tab is the place to use it:
`CBBE2UBE_NO_SOURCE_FOLLOW`, `CBBE2UBE_NO_CHEST_FOLLOW`,
`CBBE2UBE_NO_SMP_ANTIPOKE`, `CBBE2UBE_NO_DRAPE_XML_GATE`.

If a robe crashes on equip, untick "Fit robes/dresses that declare their own
physics" first — that is its known failure mode. If stiff armour starts looking
rubbery, untick the chest follow ratio.

**Not** promoted: keeping mostly-rigid armour skinned. It changes physics and
has no in-game verdict yet, so it stays opt-in on the Armor tab.

### Fixed — the converter was not deterministic across processes

Iterating a set of strings orders by hash, which varies per process, and that
order reached the output three ways: the order `setShapeWeights` was called
(deciding which influence survived an overflowing row), the morph order inside
a generated .tri, and the 4-influence cap's tie-break on exactly-equal weights
(symmetric bones tie). Same input now produces the same bytes: verified across
PYTHONHASHSEED 0, 2, 5 and 7 over all 46 emitted artifacts.

### Fixed — a fit pass that raised was indistinguishable from one that did nothing

Every fit pass runs inside a catch so one bad piece cannot abort a 4000-piece
batch. The catch was silent, so a pass that threw simply vanished -- which had
already cost three wrong verdicts. Failures are now recorded and travel back to
the caller in the conversion report; grep it for PASS FAILED. Swallowed mesh
SAVES report the same way.

### Fixed — fitted pieces shipped standing off the body

Phase 1 carried TWO `inflate_armor_outward` call sites and NO conform; phase 2
had both. A phase-1 piece — the large majority of files — got clearance added
with nothing to reel it back to the author's fit. `conform_to_source_standoff`
now runs in phase 1 too, using the CBBE base body the warp is already keyed on
as the source reference (phase-1 pieces frequently ship no inline body, which is
part of why they are phase 1).

`conform_to_source_standoff` also deliberately left tight cloth looser than
authored — flooring at `min_clearance` and reeling a skin-hugging vert only
`blend_tight` of the way back — because the source was fitted to the smaller
3BA body. True at bust/belly/butt, false at a shoulder or sternum. Both limiters
now ramp off with the body's outward morph amplitude.

Measured per-vertex over 8.1M verts of the shipped pack (loose AND BSA sources):
verts the author placed at 0.10-0.25u shipped +0.346u further out (p90 +1.00u),
the largest push of any band; loose verts (>1u) moved +0.034u. The tighter the
author fitted it, the more it was inflated.

Env: `CBBE2UBE_PHASE1_CONFORM=1` (DEFAULT OFF pending the clipping A/B),
`CBBE2UBE_NO_STATIC_AUTHORED_FIT=1`, `CBBE2UBE_STATIC_AUTHORED_AMP` (2.0),
`CBBE2UBE_STATIC_AUTHORED_MIN` (0.06).

A related ordering effect, traced rather than guessed. Per-pass trace of one
armour's chest band: authored 0.197 → warp 0.265 → inflate 0.490 → **conform
0.235** → `_smooth_warp_grooves` **0.322**. The conform reaches the authored
fit; groove-smoothing then runs and pushes part of it back out, because that
pass is one-sided by design and can never pull toward the body.

Reordering conform after groove-smooth was the obvious fix, and it was built and
measured: it does tighten the chest band (0.322 → 0.283) but costs +2.267u of
arm crinkle, because the output's smoothness comes from groove-smooth smoothing
the *cumulative* field last. So it was reverted, and the fit problem was solved
instead by bounding groove-smooth's outward motion at the authored standoff.
Recorded here so the reorder is not attempted again.

### Fixed — physics chains whose anchor node was dropped (pull-to-origin)

An HDT-SMP chain hangs off a kinematic anchor bone. Those anchors carry ZERO
skin weight, so no shape's bone list mentions them and the rebuild — which
carries the bones the skin references — has no reason to keep them. A separate
pass exists to preserve them from the physics XML. It was computing the
preservation set two ways too narrowly, and each way lost a different armour:

* it harvested only `<bone name=...>` declarations, so a chain whose anchor
  appears ONLY as a `<generic-constraint>` `bodyA`/`bodyB` was never seen. One
  shipped skirt XML declares 124 bones and names 81 constraint bodies, 18 of
  them never declared.
* it cleared candidates with `_is_skeleton_bone`, which matches its body-part
  keyword list as UNANCHORED SUBSTRINGS. The custom chain bone `LArmA 01`
  contains "arm", so it was treated as a bone the actor supplies. No skeleton
  has it.

Either way FSMP cannot resolve the anchor, places it at the origin, and the
chain hanging off it is dragged there — sleeves stretching from the shoulder to
the ground, skirts collapsing.

The preservation set is now built from every bone the XML references by any
route, minus the ones the ACTOR'S skeleton actually supplies — a lookup against
the real skeleton rather than a guess from the name. `_is_skeleton_bone` is
deliberately left alone: it also drives weighting, leg-rigid detection and the
jiggle strip, and a blanket change to it is what regressed working cuirasses in
June.

Measured over the shipped pack as a source→output differential: of 548 output
NIFs carrying a physics XML, 239 dropped at least one XML-referenced node, but
75 of the 87 dropped names are real actor bones where dropping is correct. **12
names were unresolvable, across 18 NIFs in 3 armour sets** — all three now keep
their anchors, recreated at the exact source bind (delta 0.0). An unaffected
piece is unchanged: identical shapes, vertices, weights and node transforms.

The postflight check that was supposed to catch this had both blind spots too:
it read only declarations, and it warned about every declared bone missing from
the NIF including the resolvable ones — ~45 lines per file, which is how the six
that mattered stayed invisible. It now reports only genuinely unresolvable
bones, and counts constraint bodies as references.

### Fixed — the pass chain was computing against a misplaced garment

`shape_body_offset` adds a shape's NiAVObject translation to put its verts into
body space. For a skinned shape whose verts are *already* in body space and
whose `global_to_skin` is identity, that translation is not a correction — it is
a displacement. One piece was being shoved 40 units sideways before a single fit
pass ran, so all twelve phase-2 passes measured, pushed, and inflated against a
garment that was not where they thought it was.

The offset is now checked geometrically: if applying it moves the shape
*further* from the body than leaving it alone, it is discarded. On the piece
that had resisted diagnosis for three months, this alone took bust clipping from
**8.87% to 0.00%**, with the new targeted push pass firing on nothing — there
was nothing left to fix.

This is also why several earlier "the fix didn't work" results were misleading:
the fixes were fine, the geometry they were applied to was not.

### Fixed — groove smoothing pulled the bust apex onto the skin

`_smooth_warp_grooves` does a roughness-weighted Laplacian smooth of the
CBBE→UBE displacement field to remove indent lines. Over a convex feature that
field peaks at the apex, and smoothing a peak flattens it — pulling the garment
back onto the body. The roughness weighting made it worse, because roughness is
highest at that same apex, so the pass smoothed hardest exactly where flattening
does the most damage.

A per-pass trace found it regressed fit on **13 of 42 shapes** (net +1052
exposed verts, worst +394) while improving fit **zero** times.

It is now one-sided: a vert may be smoothed along the surface or away from the
body, never toward it. On the reproduction case, 31 → 161 exposed verts becomes
31 → 21, while 81% of the smoothing motion is retained — the pass still does its
job. `CBBE2UBE_GROOVE_ONESIDED=0` restores the old behaviour for comparison.

### Fixed — correctness

- Coverage sidecars shipped pre-prune FormIDs, so the merge emitted **zero**
  links. An INI mask hid it. Record objects are now held across
  `prune_unused_masters`.
- `convert_one_armor.py` now takes the **same** path as a batch run. It had
  diverged (different vertex scale, slots resolving to 0), which made
  single-piece validation quietly untrustworthy.
- A diagnostic print can no longer abort a conversion.
- BSA-packed sources resolve in `find_source`.

### Fixed — the only pull-in pass was silently skipped

`conform_to_source_standoff` reels an over-projected garment back onto the body;
every other pass nudges outward. On some pieces it never ran, because **two body
detectors disagreed**. `classify_shapes` identified a shape as the body and
dropped it for the swap; `_is_body_pynifly_shape` then refused the same shape for
carrying fewer than 40 bones — a BodySlide-output inline body only carries the
bones its surviving verts touch, and the affected pieces ship one with 26. The
source-body reference stayed `None` and the gate never opened.

No exception, no warning — the pass was simply absent. It was found only because
the per-pass standoff trace below was built to chase the report.

Measured on the affected piece: strap-line standoff **2.40u → 1.72u**, against a
**1.79u maximum** across 42 shapes where the pass did run. Clipping unchanged at
0.00%, so the gap closed without trading it for skin poking through.

**Scope, measured by a full sweep rather than a sample:** the predicate matches
103 of 482 source pieces. Of those, 22 reach the converted output — the other 81
are overwhelmingly HIMBO/male bodies (77), which the female-only policy never
converts. Converting all 22 then showed **8 of them route to phase 1**, the copy
path, which never reaches `conform` at all: the scan tested for a body
`classify_shapes` could name, but phase-2 routing has further conditions, so it
over-matched. **The real list is 14 pieces**, dominated by the vanilla-lineage
armours (hide, imperial, stormcloak, draugr, Ysgramor) whose BodySlide output
emits a low-bone inline body. The ones that dropped off are cloaks, boots,
panties and a corset — consistent with them not being body-swap pieces.

Verified across that set rather than sampled: **48 of 48 armed shapes now run
`conform`**, with a control piece that already ran it still doing so.

> An earlier draft of this entry said "rare, not systemic: 42 of 42 armed shapes
> in a 9-mod census already ran the pass". **That was wrong.** The census drew
> nine pieces that all happened to have detectable bodies; a sample that lands
> entirely in one state cannot establish a rate, and it was read as if it had.
> The full sweep replaced it. The 1.2.1 commit message still carries the wrong
> figure and is left alone rather than rewriting pushed history.

The fix is a fallback reached only when the strict detector finds nothing, so it
cannot alter pieces that already work — verified byte-identical on one.

### Fixed — telemetry that misreported itself

- **`shipped`** added to chain records. `final` is the *rejected* measurement
  when a rollback fires; on the first pack-wide run that read as 174 exposed
  verts against 101 actually shipped, and made a run with zero regressions look
  like 20 shapes ended worse.
- **`path`** added to every record. `nif` is a bare filename and filenames repeat
  across mods — three ship a `cuirassmedium_1.nif` — so a record could not be
  traced to the piece it described.
- The `conform` and chain-verify call sites now **record** their exceptions
  instead of `except Exception: pass`. Both made a failure indistinguishable from
  "nothing to do".

### Fixed — a dense garment raised MemoryError mid-run

```
MemoryError: Unable to allocate 825 MiB for shape (36,061,621, 3)
```

`_pairs` emits one row per (ray, triangle) candidate, so cost scales with
rays × candidates. The sparse formulation is far cheaper than the dense one it
replaced — which is why it was introduced — but it was never *bounded*, and it
was described as "memory-safe" anyway.

It failed **twice, and only once visibly**: `record_torso_bands` recorded a
`standoff_band_error` because it is built to record its own failures, while
`ChainGuard.exposed()` swallowed the identical error into a `-1` that surfaced
as the single word `unmeasurable`. Same armour, same cause.

Fixed once in the shared cast (`cast_chunked`, chunked `clipping()`), used by
the standoff record, the torso bands, the pass trace and the chain guard. Rays
are independent, so a chunk boundary cannot change a result — asserted across
chunk sizes that do not divide the ray count evenly, and against the dense
reference so the calibrated anchor cannot drift. `CBBE2UBE_RAY_CHUNK` tunes it.

### Fixed — the bust requirement was measured on the wrong thing (#bust-surface-req)

`conform_to_source_standoff` asked whether each garment **vertex** stood `req`
clear of the body. The defect is the **surface**: the tightest point sits in a
triangle interior — on the failing piece, 50 of the 50 tightest spots, a median
1.607u from any vertex — so a surface can sag 0.855u below vertices that all
pass. `req - worst` was therefore negative at every nipple vert (mean -0.921),
the push-out never fired, and `req` was never read at all: raising it by 3.5u
moved delivered clearance by 0.04u.

The requirement is now evaluated per garment triangle over the body points that
project **inside** it. The inside test is load-bearing — a point beside a
triangle is not covered by it, and charging it inflated the whole chest
(0.434 -> 2.297u) when tried. `CBBE2UBE_NO_BUST_SURFACE_REQ=1` disables.

Measured across **112 installed BodySlide presets**, poking presets **19 -> 1**
(the survivor is one named "Too Big"). On the reported piece and preset:
-0.082u with 65 poking verts -> +0.220u with none, for +0.024u of torso fit and
+0.000 crinkle.

### Fixed — the bust neighbourhood never reached past its own vertices (#bust-neighbourhood-spacing)

`BUST_NEIGHBORHOOD_RADIUS = 4.0` was effectively dead: with `k=6` and a 0.359u
body the sample only ever reached 0.673u, so the 4.0u filter never removed
anything and the real neighbourhood was set by `k`. Garment spacing is
0.71-1.21u, so a tip poking between two garment verts was never sampled. The
sample is now sized by each vertex's own local spacing, with `k` raised enough
to reach it and today's `k` nearest retained as an exact subset (so a garment
finer than the body is unchanged). `CBBE2UBE_NO_BUST_SPACING=1` disables.

### Fixed — the requirement ignored body morphs (#bust-morph-residual)

`req` was a bind-pose number while the character in game is morphed, and a UBE
nipple travels up to 5.35u at runtime. The armour follows via its own BODYTRI,
so what survives is the *residual* between the body point that pokes and the
garment vert covering it — zero for a slider that merely inflates, positive for
one that reshapes. That residual is added to `req`, weighted by nipple weight so
it costs the torso nothing. `CBBE2UBE_NO_BUST_MORPH_RESIDUAL=1` disables.

### Fixed — groove smoothing gave back the clearance the conform had won

Two independent defects in `_smooth_warp_grooves`, which runs after the conform:

- It is outward-only, so on a vert the conform had just reeled in it could only
  hand clearance back (traced: chest 0.235 -> 0.322u). Its outward motion is now
  bounded at the authored standoff. `CBBE2UBE_NO_GROOVE_CAP=1` disables.
- Its *tangential* motion reshapes triangles, dropping the interpolated surface
  over a tip between verts even though no vert moves inward (0.284 -> 0.161u).
  The smoothing is now held back over a protrusion.
  `CBBE2UBE_NO_GROOVE_HOLD=1` disables.

### Fixed — the torso under-followed every spine bend (under-bust clipping)

The third instance of the same body-rig mismatch, and the cause of the under-bust
clipping that only appeared in motion. The garment parks its spine mass on the
wrong vertebra — measured in the under-bust band of a vanilla cuirass, normalised:

    garment   Spine 0.121   Spine1 0.727   Spine2 0.151
    body      Spine 0.098   Spine1 0.492   Spine2 0.410

`Spine2` sits furthest up the chain and so accumulates the most rotation. Carrying
0.151 of it against the body's 0.410 makes the garment under-travel every spine
bend by ~20%, and the skin slides out from under the bust. Like the other two, it
is invisible at bind pose. All three spine bones are managed together because the
defect is the split between them, not missing mass.

    under-bust band       follow median      verts below 0.7
    spine forward lean    0.814 -> 1.099      24.3% -> 0%
    spine twist           0.793 -> 1.162      24.3% -> 0%
    spine side bend       0.840 -> 1.016      24.3% -> 0%
    sprint                0.832 -> 1.091      24.3% -> 0%
    bust, bow draw (p10)  0.720 -> 1.048

**Pass ordering is now load-bearing.** Every family-scoped match rescales the bones
it does not manage, so the spine and arm passes contend for rows where their bands
overlap and whichever runs last wins. Spine runs first: measured, spine-last costs
the armhole 1.106 → 1.076, while arm-last costs the under-bust nothing, because
under-bust rows carry no arm-family weight and are skipped anyway. A test asserts
the ordering per convert path.

`CBBE2UBE_NO_SPINE_MOTION_MATCH=1` disables it.

### Fixed — the shoulder walked out through the armhole in motion

CBBE and UBE rig the shoulder differently: CBBE's `UpperArm` weight stops at
z 99.7 and the shoulder above it is Clavicle-only, while UBE carries `UpperArm`
up to z 110.1. A CBBE-authored garment bakes in the CBBE convention, so on a UBE
body it arrives with ZERO `UpperArm` weight across the armhole over skin that has
0.179 there. The shoulder then moves and the garment does not — follow p10 was
literally 0.000, meaning those verts did not move at all — and the body emerged
under the arm and down the side during animation. Bind-pose clearance cannot see
any of this, which is why it survived every clearance pass.

Not a converter leak: the source garment and the source body both measure 0.000
there. It is a body-rig mismatch, so it applied to every CBBE-sourced piece
covering the shoulder.

Built as the ARM instance of the existing leg-motion match — the same defect at
the hip — by parameterising that pass by bone family and Z band rather than
copying it, so the 4-influence cap and the weight-write invariants cannot drift
apart between limbs. `Clavicle` is rebalanced alongside `UpperArm`, because the
defect is mass sitting on the wrong bone of the pair, not just missing mass.
Weights only: no vertex moves on any shape, so bind-pose fit is untouched.

    armhole, arms forward   follow 0.642 -> 1.106,  38.4% -> 0.8% of verts under 0.5
    armhole, arms crossed          0.684 -> 1.127,  35.0% -> 0.8%
    side, arms crossed             1.016 -> 1.015,   9.6% -> 3.2%
    under-bust / bust              unchanged (specificity control)

`CBBE2UBE_NO_ARM_MOTION_MATCH=1` disables it.

### Fixed — the leg-motion match was silently doing nothing on part of the pack

The same over-broad gate. `_shape_has_hdt_smp_rigging` flags a shape when 40%+ of
its bones are unknown to the body — but the injected body declares 36 bones and a
garment routinely carries 50–70, including plain skeleton bones like
`UpperarmTwist2`. On any piece that also has a physics XML, the leg-motion match
therefore returned without touching anything.

Measured over 400 converted pieces: the gate fires on the main garment of 141
(36%); 35 (9.0%) also have a physics XML. Of the leg-bearing shapes in that state,
39 were gated out of the pass entirely and every one had rows that survive a
per-row test. So the shape gate was costing real work rather than protecting a
physics chain.

The leg pass now uses the same per-row fallback the arm pass does: where the
shape-level heuristic fires, rewrite only verts whose entire weight sits on bones
the body also has. Such a row provably carries no authored chain bone. The
collider and soft-body skips are untouched and still unconditional, which is what
keeps the documented soft-body cases correct — those shapes are declared
per-vertex soft bodies and declining to rewrite them is right.

    hip band, follow ratio      median          verts below 0.5
    dragonbone cuirass          0.882 -> 1.119   21.8% -> 12.0%
    draugr chain                1.006 -> 1.092    9.6% ->  3.4%
    hide cuirass (light)        0.000 -> 0.157   90.2% -> 82.2%

No band on any probe got worse. Weights only: zero vertex movement on both weights
of all three probes and across all 15 golden pieces, and the weight-sum invariant
is unchanged.

### Changed — the Armor settings tab is readable again

It had grown to 38 of the tool's 43 settings and rendered about 3.7 screens
tall, but the bulk of that was not controls: 86% of the text on the tab was
always-visible explanation, one paragraph per setting.

Each setting now shows a single line, with the full text on hover. Nothing was
shortened or deleted -- those explanations carry measured numbers and in-game
caveats recorded nowhere else, so they moved rather than shrank.

The seven numeric tuning knobs now hide behind "Show advanced", which is what
the underlying field was always for; it had never been connected to anything.
There is also a live search over the settings, and the window remembers its
size between launches.

Groups were re-cut from six to eight by what a setting actually acts on: the
physics-chain options had been split across two groups, one group had grown to
16 unrelated settings, and three settings sat under the wrong heading.
"Convert vanilla armor" moved to the Run tab, beside the mod selection it
extends -- it adds a source to the run rather than changing how a garment is
fitted.

Net effect: 3.7 screens to 2.1, with every word still reachable.

### Changed — a failed pass or a failed write now reaches the report

Every fit pass and every mesh/sidecar write ran inside a silent catch, so a
pass that RAISED was indistinguishable from one that ran and did nothing.
30 such handlers for passes and 19 for writes are now 0: failures are recorded
and travel back in the conversion report. Grep it for PASS FAILED.

### Changed — the fit metric

- **Added** the validated clipping test: a body vert is clipping when the
  garment sits *behind* the skin (ray out escapes, ray in hits a
  same-facing triangle). Calibrated against a user-confirmed clean/dirty pair:
  reads 0.00% on the clean armour, 8.87% on the one that clips.
- **Added STANDOFF** as the counter-metric. Clipping has no upper bound, so an
  over-inflated garment scores a perfect 0.0% — which is exactly how
  over-inflation reached the user twice without any number complaining. Anchor
  from the clean armour: median 1.15u, p90 1.52u.
- **Retired** `bust_verdict.py` and `postflight_1_2.py` off the discredited
  signed-distance path.
- **Retired the ray cone from `underbust_census.py`**, its last live consumer.
  The `surrounded` / `partial` / `bare` columns are replaced by `clipping`;
  rows written before 2026-07-29 are not comparable on those columns. Its
  negative control turned out to be blind to the very bug it was written for —
  a garment below z 80 produces no ray hits in *either* direction, so
  transposing the two directions left it passing. A positive control (densest
  band coverage must read mostly COVERED) was added, and verified to fail under
  a deliberately inverted metric while the negative control still passed.
- **Deleted** `mesh_penetration.containment`, `poke_report`, and `cone_dirs`
  (−155 lines).
  Both were anti-correlated with in-game ground truth and had zero callers and
  zero tests. `surface_penetration` stays: only its *sign* was bad, and the
  census uses its distance. The reasoning that discredited them is preserved in
  `docs/METRICS.md`, and the four metric requirements written for `poke_report`
  moved onto `clipping_report`, which actually satisfies them.
- **Added** an orientation gate that removes a false positive under morph
  (an inward ray striking the far side of the garment past a cut rim) without
  moving the calibration anchors.

### Added — a fit contract over the whole pass chain

The pipeline now states, per shape: **diagnose** what was wrong before anything
ran, **treat**, then **verify** the result and roll back to the best intermediate
state if the chain as a whole made things worse.

Applied to the chain rather than to each pass, because the per-pass version is
measurably the wrong contract. Over 48 traced shapes, exactly one pass ever
regressed bust fit (`conform`, 5 times), every one of those was recovered
downstream, and **0 of 48 shapes ended worse than they started** (total exposure
8138 → 245). `conform_to_source_standoff` pulls IN by design and later passes
push back out; reverting it would have blocked a correct pass and biased every
garment looser — which is the over-inflation reported twice from the game.

Cost: **two measurements per armed shape** instead of eleven. Checkpoints are
array copies, not measurements, so the chain remembers every pass and only pays
to inspect them when the final verify actually fails.

What it deliberately does not do is skip passes when the entry diagnosis looks
clean. Bind-pose clipping is blind to animation — "at rest" in game is an
animated pose — so gating passes on it would trade a measurable defect for an
unmeasurable one.

The per-pass guards on the anti-poke and the soft-cloth inflate were removed in
favour of this: neither ever regressed across the traced sample, and the chain
verify covers the outcome for a quarter of the measurements.

### Added — instrumentation, because the chain was speculative

Twelve passes computed against the body, all assumed the garment was in body
space, none asserted it, and nothing between them measured whether a pass
helped. That is how one frame error corrupted all twelve in silence.

- `src/fit_metrics.py` — the canonical in-converter metrics module.
- **Frame precondition**: every phase-2 shape's frame choice is checked and
  recorded when suspect.
- **`ChainGuard`** — the contract described above. An earlier `FitGuard` guarded
  two individual passes and was removed within this same release once the trace
  showed neither ever regressed; the chain verify covers the outcome for a
  quarter of the measurements.
- **`PassTracer`** (`CBBE2UBE_PASS_TRACE=1`) — before/after at every pass
  boundary with shared measurements, so N passes cost N+1 measurements. This is
  what found the groove-smoothing regression, and what showed that guarding each
  pass was the wrong contract.
- **`minimum_push`** — conditional, one-sided targeted push for residual
  exposure. Exits having moved nothing on ~94% of shapes, never moves
  chain-driven (SMP) verts, and reverts on regression.

### Added — physics and follow

- **Bust collider split** and **torso jiggle graft** now default ON, both
  confirmed in game.
- Chest-follow: the material ceiling caps *this pass's graft*, never the
  pass-through, fixing a conflict where the torso graft and the follow pass
  together scored worse (0.325) than either alone (now 0.786).

### Added — per-pass STANDOFF trace (`CBBE2UBE_STANDOFF_TRACE=1`, default off)

The existing trace measures clipping and is blind to the opposite defect. This
reports standoff per pass in 3u slabs up the torso, reading the snapshots the
chain already keeps, so it needs no re-conversion. Slabs rather than one window:
a single median over z105–114 read identically for all nine arms of a bisect
because hit density varies ~10× across it.

### Added — standoff recorded up the whole torso, not just the bust front

The ceiling guards `z 90–102`, and that was the only region any pack-wide record
had ever covered. A gap reported in game sat at **z 108–114**, so "1.31u median,
within ceiling" was an accurate statement about a region the user was not
looking at. The under-bust had been an open lead for weeks with no numbers
behind it at all.

Four bands are now recorded per shape — `underbust` (z 78–90), `bust` (90–102),
`upperchest` (102–108), `strap` (108–114) — **separately, never merged**. Hit
density varies ~10× up the torso, so a single median over the whole range is
pinned by whichever slab has the most covered skin; a nine-arm bisect that
aggregated `z 105–114` read identically for every arm for exactly that reason.

**No verdict on the new bands.** `over` stays on the bust record alone, because
it is the only band with an anchor confirmed correct in game. Standoff rises
monotonically up the torso — measured 1.17u at the bust to 1.94u at the strap
line — so applying the bust ceiling higher up would manufacture failures on
nearly every garment. These ship as data; the next full run is what produces
enough of them to calibrate against.

The calibrated bust record is untouched, mask and all, since the 1.15u/1.52u
anchor depends on its exact definition. The bands use the sparse `_ClipTester`
path rather than `standoff()`, whose dense formulation reached 15 GB measuring
several bands on one cuirass; a test asserts the two agree to 1e-6 on the same
index, so the mixed implementation is verified rather than assumed.

### Added — `scripts/analysis/audit_sink.py`

One reader for `standoff_audit.jsonl`. Nothing in the repo read the frame, chain
or band records, so every analysis was hand-written — more than fifty times in a
day, each re-deciding which field to trust. It refuses to repeat four specific
mistakes: it reads `shipped` and never `final`; it reports measurement
**failures first** rather than burying them under clean averages; it excludes
first-person viewmodels from standoff *and states how many*; and it prints
percentiles rather than inventing ceilings for bands that have no calibrated
anchor.

### Performance — skeleton bone resolution

The normalised skeleton bone set was rebuilt on every membership test
(1.9M redundant calls over five pieces). Cached; about 5-6%, output identical.

### Performance — the ray cast, which is what made the contract affordable

Three exact optimisations, each verified against brute-force Möller-Trumbore
rather than against a previous run's numbers:

- candidate pruning moved off lists-of-Python-lists onto a C-level sparse
  distance matrix (**4.0×**);
- the ball query is bucketed by triangle radius, so one 6u triangle no longer
  sets the search radius for the whole mesh (a further **1.6×**);
- a ray-line cull before the intersection test — 4% of candidate pairs survive
  it (**2.0×** on the cast).

A region measurement went from **391 ms to ~120 ms**. End-to-end conversion time
is unchanged within run-to-run noise: fit measurement simply is not a
significant fraction of a piece's conversion cost, which is the point — the
contract is free in practice.

### Performance

- Canonical body skin and skeleton are cached: **133s → 30s** per armor.
- Ray casting gained an exact range cull and early-out over triangle blocks:
  **6.4h → 2.1h** on a full census.
- The incremental-rebuild floor now includes a hash of every `CBBE2UBE_*`
  environment variable and the NIF-relevant arguments, so changing a setting
  correctly invalidates stale output instead of silently reusing it.

### Performance — stop measuring garments that cannot be hit

`ChainGuard` armed on the **body** region size, which is a constant (the UBE
band is 5249 verts against a floor of 50), so *every* phase-2 shape armed. Of
4382 armed shapes in the previous run, only 1605 covered the bust band —
**2777 (63%) measured and found nothing**, and `record_standoff` ran the full
measurement before discarding it, on the dense path.

`garment_reaches()` gates all three call sites on bounding-box overlap, which is
conservative by construction: a garment's box contains all its triangles, so a
box that does not overlap cannot hold one that does. It can only admit work,
never skip a real hit, and it fails **open**. The risk is one-sided, so the test
asserts directly that whenever the gate says skip, the ray cast finds zero hits.

`record_standoff` also moved off the dense `standoff()` onto the sparse path,
with a test pinning that the recorded median still matches the dense result on
the calibrated mask.

> **Not yet measured end-to-end.** These remove work; what fraction of
> conversion time that is has not been profiled. No speedup is claimed.

### Removed — #groove-nipple-hold

It held the groove smoothing back over a protrusion, and earned that while it was
the only thing protecting the tip. Once the surface requirement landed it became
actively harmful. Ablated across all 112 installed BodySlide presets:

    hold ON    1 preset still poking, nipple clearance +0.220u
    hold OFF   0 presets poking,      nipple clearance +0.528u

with identical torso fit and identical crinkle on every shape. Deleting it
cleared the last holdout.

### Read this before trusting the numbers

The fit figures below come from the mesh harness, in bind pose, on a specific
body. They are necessary but **not sufficient** — bind-pose metrics are blind to
what animation does, and the worst remaining clipping lives on SMP/soft-body
cloth that no skin pass can reach. In-game verification is still the gate.

### Tooling and project

- Report intake (issue templates, diagnostics zip), CI on contributor lanes,
  and CI coverage for Python 3.10 — the runtime the shipped exe actually uses.
- The mod-agnostic tracked-content policy is now enforced by a test rather than
  trusted.

### Known issues

- Butt clipping ~11.6%: needs a physics `can-collide-with-tag` change, not a
  mesh pass.
- Under-bust side (z 80–86) is unresolved; the metric's resolution there is only
  ~10–14 verts.
- Loose robe chests can still bake in roughly +3u of over-inflation from the
  static warp/clearance path.
- The chain-gate on finalize remains reverted.
