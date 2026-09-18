# Audit steps 3 and 4 — docs currency, and the guards that survive a split

2026-09-02. Continues `2026-09-01_AUDIT.md` (121 findings) after steps 1 and 2.

## Step 3 (docs/comments that would mislead an edit) — CLOSED

Ten findings, checked one at a time against the cited line. **Nine were already
fixed on 2026-09-01**; each was verified, not assumed:

| finding | cited artifact | state |
|---|---|---|
| F027 | `_conform_cords_to_host` named as a live pass in PIPELINE | gone (0 hits, function deleted) |
| F028 | on-disk list omits 4 passes; "three weight passes" paragraph | all 4 present; paragraph now reads "four" and places the hold |
| F029 | §2b skin/bone order wrong on both paths | rewritten as two lanes; precreate documented at WRITE time |
| F030 | DESIGN "runs last, after every vertex op" | phrase gone |
| F031 | six OPT-IN headers over default-True flags | 0 stale of 44 default-True flags |
| F032 | CHANGELOG quotes a non-existent setting name | now the literal GUI label |
| F033 | `ride_body_floor` unrecorded / mis-recorded | recorded in CHANGELOG:8 and PIPELINE §6 |
| F034 | docstring cites `_SPINE_FLANK_BONES` | symbol gone from the tree |
| F035 | `convert_nif` docstring says "verbatim file copy" | now describes the body-aware rebuild |
| F089 | `conform_to_body` row describes geometry | now "Match fitted cloth's skin weights", weights-only tooltip |

**What was actually left to do was not an instance, it was the class.** F031's
own stated fix asks for the header scan to become a test. Added to
`tests/test_promoted_defaults.py`:

- `test_no_default_on_flag_still_advertises_itself_as_opt_in` — walks every
  module-level `_flag` binding across the declared modules, resolves its
  runtime value, and reads the first line of the comment block above it. A
  header saying *opt-in* / *default off* WITHOUT a current-state phrase
  (`default on`, `was opt`, `promoted`) over a True flag fails.
- `test_the_stale_header_scan_can_actually_fail` — the control. Feeds the scan
  a synthetic bare OPT-IN banner over a real promoted flag (must be reported)
  and the corrected form (must not). Without it the first test is a scan that
  returns zero for unknown reasons.

Calibration mattered: the first draft flagged three flags whose headers already
read `DEFAULT ON since 2026-08-26 (was opt-in =1)` — it was matching the
retrospective clause. A guard that fires on a correctly documented promotion
would be reverted the first time someone promoted a flag.

Why this class is worth a permanent test rather than ten fixes: the banner is
what a reader sees first and what the generated `PASS_MAP.md` index reproduces
verbatim, while the "DEFAULT ON since ..." correction sits 20-40 lines lower. A
maintainer who trusts it sets `CBBE2UBE_<FLAG>=1` for an A/B and gets **an arm
identical to its control**, which reads as "the change does nothing" — the
broken-arm trap this project hit twice on 2026-09-02 alone.

## Step 4 (split preconditions) — F005 closed

The nine-module split has already happened, so F005 is no longer a
precondition; it is live exposure. Six structure guards were coupled to the
single filename `nif_convert.py`, and a pass that moved to a sibling would be
dropped by five of them **with the suite still green**.

Measured today:

- `scripts/pass_map.py` now carries `CONVERTER_MODULES` (12 files incl.
  `src/fit_metrics.py`), and `tests/test_split_preconditions.py` pins that list
  against `src/nif_convert*.py` on disk.
- Four of the five test-side guards already route through
  `tests/_converter_sources`. `test_deterministic_output` scopes the whole
  `src/` directory, which is fine.
- Item (6) landed: `minimum_push` — defined in `src/fit_metrics.py`, called on
  the body-swap path — now has a row. It had none.

### Two residual single-file guards, both mine from earlier today

The `#phase1-antipoke` parity assertions read `inspect.getfile(nc)` and grep it
for a flag name. They fail CLOSED on a move (a false failure, not a vacuous
pass), so this is brittleness rather than blindness — but the question "is this
site still behind its flag" is about the converter, not about one file. Both
now go through a `_all_source()` helper with the same scope as `_module_ast`.

### The gap that was real: the control, and two unfloored columns

`test_split_preconditions.py` already had per-entry row floors and a parity
reach floor. What it did not have:

1. **A third entry with no floor at all.** `_finalize_physics_and_motion_match`
   — the tail BOTH paths call — was unfloored, and it is the entry that
   collapses hardest (29 rows -> 1). Floor 24.
2. **No floor on the `guarded by` column.** It is filled from flag constants,
   so it can empty while every row survives, leaving a full-length map that
   reports every pass as unconditional. Floor 70 (85 today).
3. **No control.** The floors had never been seen to fail.

`test_the_row_floors_can_actually_fire` undeclares every sibling module —
exactly what an extraction that forgets to declare its new module looks like —
re-renders, and asserts all three floors fire:

```
convert_nif                          144 ->  58   (86 lost)
convert_nif_phase2                   175 ->  63  (112 lost)
_finalize_physics_and_motion_match    29 ->   1   (28 lost)
TOTAL                                348 -> 122  (226 lost, 65%)
```

**Two thirds of the pass map depends on that one declaration**, and before
today nothing proved the floors were set low enough to catch losing it. The
control also asserts the exact set of entries that fired, so a floor quietly
raised past the point of usefulness fails too.

This is the class `2026-08-02_AUDIT_MAIN_LAYOUT.md` already paid for: *"Moving those 35
files silently removed 29 tests, and the suite stayed green."*

## Not done

F006 (the `nc.*` namespace as test API) and F087 (whole-file `getsource(nc)`
pins) remain open; both are about the *next* extraction rather than the ones
already made. F049/F050 (leaf modules) are superseded — those leaves are split.
