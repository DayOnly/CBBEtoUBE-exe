# `#pair-unit-dispatch` — the weight-tail nondeterminism was a PAIR RACE

2026-09-06, evening. Found while validating the consolidation plan's gate on
two identical arms; the gate failed, and the failure was the instrument
finding a real defect.

## What the three identical arms showed

Three arms, same source, same live settings, `PYTHONHASHSEED=1`, 16 workers,
the 184-NIF acceptance population, 14 `PASS FAILED` lines and 0 access
violations in each. Per-file sha256 across the three, then a per-shape weight
row diff on the files that differed:

    common files                     144
    files differing in ANY arm         5
    files with a vertex moved          0

    shape (class)                    odd arm   rows   worst   other two arms
    bust-split hidden collider clone   B        725   0.8486  byte-identical
    ButtCol proxy                      B        189   0.0659  byte-identical
    ButtCol proxy                      A        179   0.1182  byte-identical
    ButtCol proxy                      C         44   0.0073  byte-identical
    ButtCol proxy                      C         20   0.0002  byte-identical

Every differing shape is a `_1` collider proxy. Every one has exactly ONE odd
arm and the other two agree on it to the byte. The worst delta, 0.8486, recurs
to four decimals in every odd arm — two outcomes, not a distribution. Bimodal,
which is what the 08-19 note already called it ("the same worst-file delta
recurs across independent pool pairs — a branch that depends on per-worker
in-process state"). It was not in-process state. It was the sibling.

On the clone, the odd outcome is a REWEIGHT: thigh rows +63% / +51%, pelvis
mass +9%, the skirt-chain bones' mass down ~5x, clavicles and arms untouched.
The leg-family match ran on a collider. The majority outcome kept the authored
weights, which is what a collider is supposed to keep (`#smp-collider-graft`).

## The mechanism, read from the code

`_finalize_physics_and_motion_match` runs, in this order:

1. `_split_bust_collider_shape` — adds the hidden collider clone to the NIF;
2. `_finalize_hdt_physics` — **copies the AUTHORED xml over the destination
   `<stem>.xml`** (`atomic_copy(src_xml, dst_xml_disk)`), then hardens it;
3. `_split_bust_collider_xml` — re-adds the clone as a per-triangle collider;
   then the collider patches;
4. the weight matches — each asks `_hdt_collider_shape_names(dst_path)`, which
   resolves the DESTINATION nif's physics pointer and reads that xml **from
   disk, as it is at that moment**.

`_0` and `_1` share one `<stem>.xml` (the weight suffix is stripped when the
xml path is built). With the pair on two workers, a `_1` at step 4 whose
sibling `_0` is between step 2 and step 3 reads an xml with no clone in it,
gets a collider set without the clone, and the leg-bend match reweights it.
Then `_0` reaches step 3, the xml gets its clone back, and the FINAL xml is
identical across arms — which is why diffing outputs never showed it and why
`--workers 1`, where a pair runs in sequence, is byte-identical run to run.

BUG-00 (`#xml-source-of-truth`) fixed the fail-OPEN half of this: a MISSING
xml used to disarm every collider guard, and now falls back to the text bound
from the source. An xml that is PRESENT but at the wrong stage was the other
half. The four `ButtCol` cases are the same shape of race on the authored
collider: the finalize and the patches rewrite the shared file several times
and a concurrent `_1` reads whichever version is there.

## The fix — scheduling, not a guard

`src/auto_convert.py`: `_pair_units(items)` groups the work items by
weight-agnostic DESTINATION key, first-appearance order, list order within;
`_run_unit(fn, unit)` runs a unit on one worker in sequence; `_NifPool`
submits units instead of items and, when a unit's worker dies, re-runs that
unit's items one at a time in list order. The list order is `_1`, `_0`, then
the no-suffix model — what `_resolve_armor_meshes` emits and what the serial
path runs — so the pool now reproduces serial's sequence for exactly the files
that share state and nothing else changes. No per-file converter code moved.

Why not a stale-read guard inside each weight pass: that patches the symptom
in N places and leaves the shared file racing for the next reader
(`feedback_prevent_dont_patch`). The shared xml and tri are the design; the
invariant that makes the design correct is "a pair is converted in sequence",
and the pool is the one place that invariant can be stated.

## Proof

Same population, same live settings, `PYTHONHASHSEED=1`. The serial arm was
run from a frozen copy of the tree so no edit could leak into it. Every file
under `meshes/` is compared — NIF, TRI and XML — by sha1:

    two fixed 16-worker arms                          296 files    0 differ
    fixed 16-worker arm vs `--workers 1` serial arm   296 files    0 differ
    serial arm vs a PRE-fix 16-worker arm             296 files    1 differs
        (the generated ButtCol of one `_1`: 189 rows, worst 0.066)
    the five formerly bimodal shapes                  one outcome in every
                                                      fixed arm — serial's

The pool now reproduces the canonical serial output byte for byte on the whole
acceptance population, at 16 workers, twice. The serial arm took ~70 min; a
fixed pool arm takes ~8 min under load (~5 min alone), so weight A/Bs are back
at pool scale.

`acceptance.py` on the two fixed arms: `files with a vertex moved 0`, `weight
rows differing 0 of 0`, folds 15974 = 15974, inverted 1937 = 1937, no bone lost
its last carrier. Its first run also FAILED, correctly: launched without
`CBBE2UBE_CONFIG` / `CBBE2UBE_MO2_INI` in the shell, the bust-gap and tip
scorers could not find the UBE body and their rows came back UNPARSED — the
pinned behaviour. The gate needs all three env vars (config, MO2 ini,
skeleton) or it fails by design. The re-run with the full environment is
recorded in the consolidation plan's §1.

One residue, log-only: a `PASS FAILED: hdt_xml_shape_dropped` note on one
`_0` conversion appears in one fixed arm and not the other while every byte
of output is identical. The finalize copies the authored XML unconditionally
and prunes blocks the NIF lacks, so the note ought to fire on both siblings
every time. Not chased: it is a note count, not an output, and the arms are
the proof above.

## What this corrects in the record

* `LAST_CARRIER_HOLD_2026_09_06.md`: the wide form's "2771-row cascade" on a
  collider shape was this race, and the narrowed form's 250 rows are below its
  floor. The narrowed design stands on the rule, not on that count.
* `project_converter_nondeterministic_on_vfs_mods`: "residual 53 weight rows"
  was the same class, measured on a smaller population; the mechanism is now
  known and removed rather than characterised.
* Weight A/Bs no longer need `--workers 1` once both arms run this pool;
  `parity_convert --workers N` stays as the reference mode.
* Ships with the next exe (alongside the `_complete_weight_partners` refresh).

## Tests

`tests/test_pair_unit_dispatch.py` (6): grouping keeps list order and joins
the no-suffix model to its pair; units are keyed on the destination; one
submit per unit and one result per item; a crashed unit re-runs in list order
and errors only the crasher; and end to end with real spawned workers, each
pair lands on one PID with `_1` finishing before `_0` starts.
`tests/test_acceptance_gate.py::test_the_arm_forwards_workers_to_the_converter`
pins the harness's `--workers` pass-through.
