"""Run telemetry: which pass FAILED and which change TOUCHED a piece.

Split out of nif_convert.py on 2026-09-01 (audit step "monolith seams",
module 1 -- a pure leaf: nothing here calls back into the converter). The
recorders are imported BY NAME into nif_convert, so `nc._note_pass_failure`,
`nc._PASS_FAILURES_THIS_PIECE` and the rest keep working for every caller,
test and tool. The four state objects are mutated in place and never
rebound, so both modules see the same dict/list instances.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- broken pass vs failed design -------------------------------------------
# Every fit pass is called inside `try: ... except Exception: pass`, so a pass
# that RAISES is indistinguishable from a pass that ran and did nothing. That
# has cost three wrong verdicts: a stale keyword argument raised TypeError on
# every build, the pass never ran, and the measurements read as "the design does
# not work". The catch itself is right -- one bad piece must not abort a
# 4000-piece batch -- but SILENCE is not.
#
# `_note_pass_failure` records the failure and prints it once per (pass, piece).
# Grep the build log for `PASS FAILED` before believing any measurement.
_PASS_FAILURES: "dict[str, int]" = {}
# Per-conversion, because the process-wide dict above CANNOT reach the caller:
# the batch runs on ProcessPoolExecutor, so every worker has its OWN module
# instance and the parent's copy stays empty no matter how many passes failed.
# stderr is no better -- the frozen exe discards pool-worker output, a
# documented trap here ("clean log =/= clean run"). The only channel that
# survives the worker boundary is the returned ConvertResult, so failures ride
# home in `reason`, which auto_convert already writes into the report and the
# failures file.
_PASS_FAILURES_THIS_PIECE: "list[str]" = []


def _note_pass_failure(label: str, exc: BaseException, dst=None) -> None:
    """Record a swallowed fit-pass exception. NEVER raises: the caller is an
    except-handler, and a recorder that can throw would turn a survivable pass
    failure into a lost piece."""
    try:
        key = f"{label}: {type(exc).__name__}"
        _PASS_FAILURES[key] = _PASS_FAILURES.get(key, 0) + 1
        entry = f"PASS FAILED {label} ({type(exc).__name__}: {exc})"
        if entry not in _PASS_FAILURES_THIS_PIECE:
            _PASS_FAILURES_THIS_PIECE.append(entry)
        if _PASS_FAILURES[key] <= 3:      # once per kind, not per piece
            where = f" on {Path(dst).name}" if dst else ""
            print(f"  PASS FAILED: {label}{where} -- {exc!r}", file=sys.stderr)
    except Exception:
        pass


# ---- WHICH CHANGE TOUCHED THIS PIECE (#change-attribution) ------------------
# The failure channel above answers "what broke". This answers the other
# question a build raises: when a piece looks wrong in game, WHICH of the
# changes in that build could have done it?
#
# Without this the answer is guesswork. The 2026-08-22 build shipped two
# behaviour changes and its own notes had to say "these are the only two
# candidates, in order of blast radius" -- a build with three would not have
# been attributable at all.
#
# Rides `reason` for the same reason failures do: the module counters live in
# the WORKER process and the parent never sees them, so `reason` is the only
# channel that crosses the pool boundary. NEVER raises -- a recorder that can
# throw would turn a successful pass into a lost piece.
_PASS_EFFECTS: "dict[str, int]" = {}
_PASS_EFFECTS_THIS_PIECE: "list[str]" = []


def _note_pass_effect(tag: str, detail: str = "", dst=None) -> None:
    """Record that `tag` CHANGED something on this piece.

    `tag` is the change's own hashtag (`#collider-declared-bones`), not a
    function name: the question being answered is "which CHANGE did this", and
    one change can span several functions.
    """
    try:
        _PASS_EFFECTS[tag] = _PASS_EFFECTS.get(tag, 0) + 1
        entry = f"CHANGED BY {tag}" + (f" ({detail})" if detail else "")
        if entry not in _PASS_EFFECTS_THIS_PIECE:
            _PASS_EFFECTS_THIS_PIECE.append(entry)
    except Exception:
        pass


def _piece_pass_effects() -> "list[str]":
    return list(_PASS_EFFECTS_THIS_PIECE)


def pass_effect_summary() -> "dict[str, int]":
    """{tag -> pieces touched} for THIS PROCESS. For the BATCH read
    `ConvertResult.reason` -- a worker's copy of this dict never reaches the
    parent, the same trap `pass_failure_summary` documents."""
    return dict(_PASS_EFFECTS)


def _begin_piece_pass_log() -> None:
    """Reset the per-conversion lists. Called at the ONE entry point
    (`convert_nif`) -- phase 2 is reached THROUGH it, so resetting there as well
    would discard everything phase 1 recorded."""
    del _PASS_FAILURES_THIS_PIECE[:]
    del _PASS_EFFECTS_THIS_PIECE[:]


def _piece_pass_failures() -> "list[str]":
    """FAILURES ONLY. Effects have their own accessor on purpose -- folding them
    in here would make every caller of a function named `..._failures` silently
    also report successes, which is the exact confusion this pair exists to
    prevent."""
    return list(_PASS_FAILURES_THIS_PIECE)


def pass_failure_summary() -> "dict[str, int]":
    """{`pass: ExcType` -> count} for THIS PROCESS. Meaningful for an in-process
    convert; for the BATCH read ConvertResult.reason instead -- a worker's copy
    of this dict never reaches the parent."""
    return dict(_PASS_FAILURES)


# Lazy pynifly import — used by phase 2. Phase 1 doesn't need it.


def make_stage_hook(*, tracer=None, chain=None, surv=None, dump=None,
                    skip_none: bool = False):
    """ONE pass-boundary hook for BOTH convert paths, with the per-path
    contract stated instead of implied by which copy you happen to read.

    Step (0) of the two-path unification (audit F103). The two paths each had
    their own hook -- `_stage` on the body-swap path, `_stage_p1` on the copy
    path -- and F103's own verifier flagged the trap: **the two do not have the
    same contract.** Phase 2's feeds `_tracer` and `_chain`, and `_chain` is
    the rollback checkpoint that wraps seam-weld / coherence / strap /
    short-edge. The copy path's feeds only survival and dump. A shared runner
    that quietly hands the copy path a chain checkpoint would give it ROLLBACK
    SEMANTICS IT HAS NEVER HAD -- a behaviour change wearing a refactor's
    clothes. So the recorders are parameters: a path gets exactly what it
    passes, and gaining one has to be written down.

    `skip_none` is the second difference, and it is NOT cosmetic. The copy
    path's hook returned early on a None snapshot; phase 2's did not, so phase
    2 would call `chain.checkpoint(label, None)`. Folding that guard in for
    everyone would change when the rollback chain records a checkpoint. It
    stays per-path, defaulting to phase 2's behaviour, and is flagged here as
    an unexamined difference rather than silently normalised.

    Both paths bind their recorders BEFORE defining the hook and never rebind
    them afterwards (verified 2026-09-03 over every assignment to the six
    names), so capturing them here is equivalent to the closure and default-arg
    capture this replaces. If that ever stops being true, this factory must be
    called after the last rebinding, or the capture will go stale.

    Order is preserved exactly: tracer, chain, survival, dump.
    """
    def _stage(label, v):
        if skip_none and v is None:
            return
        if tracer is not None:
            tracer.mark(label, v)
        if chain is not None:
            chain.checkpoint(label, v)
        if surv is not None:
            surv.checkpoint(label, v)
        if dump is not None:
            dump.checkpoint(label, v)
    return _stage
