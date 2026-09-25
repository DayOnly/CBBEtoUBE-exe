# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""End-to-end: CBBE armor mod folder -> UBE conversion mod folder.

Combines M2 (ESP generation) + M3 phase 1 (NIF copy / skip body-containing
files) into a single entry point.

Input layout (a normal CBBE armor mod):

    SourceMod/
      MyArmor.esp
      meshes/
        path/to/Armor_0.nif
        path/to/Armor_1.nif
        ...
      textures/...                 # optional, copied verbatim

Output layout (drop into MO2):

    OutputMod/
      MyArmor (CBBEtoUBE src).esp  # new ESP via ube_patcher
      meshes/
        !UBE/
          path/to/Armor_0.nif      # M3 phase 1 copy (if no inline body)
          path/to/Armor_1.nif
      conversion_report.txt        # which files copied / skipped / why

NIFs that contain inline 3BA body shapes are listed in the report as
"PHASE 2 NEEDED" — the rest of the conversion goes through. That gives
a partially-working mod (everything except chest pieces shows up
correctly on UBE characters), which is the realistic M4 in-game test.
"""
from __future__ import annotations

import gc
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# MUST precede the `nif_convert` import below -- that is what pulls in numpy and
# scipy, and the cap only has any effect before their FIRST import. About 1.5 GB
# of per-process Windows commit charge on a 24-thread box rides on these two
# lines; see src/blas_env.py for the measurement. #blas-thread-cap
from .blas_env import cap_blas_threads

cap_blas_threads()

from . import ube_patcher, nif_convert, paths, discovery, nif_io  # noqa: E402
from . import child_lifetime  # noqa: E402
from .user_warnings import NOTE, plain_error, warn  # noqa: E402
from .envflags import flag as _flag, knob as _knob  # noqa: E402


# ---------- Multiprocessing worker -------------------------------------
#
# Each subprocess loads pynifly + UBE body OSD/ref lazily on its first
# NIF conversion. Module-level cache survives across NIFs in the same
# worker, so the heavy OSD parse (~11 MB) and UBE ref load are
# amortized over all NIFs that worker handles.
#
# Argument is a single picklable tuple (Windows uses `spawn`, which
# pickles work items across the process boundary). Result is a
# ConvertResult — also picklable.


def _nif_convert_worker(item: tuple) -> "nif_convert.ConvertResult":
    """Run convert_nif on one NIF inside a subprocess.

    Args (in tuple form for ProcessPoolExecutor compatibility):
      src_path, dst_path, ube_body_ref_path, biped_slots,
      [alt_texture_shape_names], [variant_sources]

    `variant_sources` maps a weight suffix (`"_0"` / `"_1"` / `""`) to the
    resolved SOURCE path of that variant of the same stem. It has to be
    computed in the PARENT, where the mod list / VFS / BSA index live: a worker
    is a spawned process that inherits none of them, and the destination
    siblings it could see instead are being written by this same batch.
    #tri-variant-collision
    """
    src, dst, ube_body_ref_path, biped_slots = item[:4]
    alt_tex = item[4] if len(item) > 4 else None
    variant_sources = item[5] if len(item) > 5 else None
    try:
        return nif_convert.convert_nif(
            src, dst,
            ube_body_ref_path=ube_body_ref_path,
            biped_slots=biped_slots,
            alt_texture_shape_names=alt_tex,
            variant_sources=variant_sources,
        )
    except Exception as e:
        # The whole traceback goes to this worker's output, which rides home with
        # the result into the run log; the reason stays one line for the report
        # and the failures file. It crossed the pool as that one line alone, with
        # no frame to say where. #worker-output
        import traceback
        warn(f"{Path(src).name}: conversion raised",
             consequence="the traceback follows; the piece is counted as an error",
             file=sys.stderr)
        traceback.print_exc()
        return nif_convert.ConvertResult(
            src_path=src, dst_path=None,
            status="error",
            # Distinct from "skipped" (benign no-op); errors count as failures.
            reason=f"error: {type(e).__name__}: {e}",
        )


def _warmup_worker(barrier, ube_body_ref_path: "str | None") -> "tuple[int, float]":
    """Eagerly load pynifly + UBE refs in this worker so the first
    real NIF doesn't pay the cold-start cost. Called once per worker
    via `_prewarm_pool` before real work begins.

    The barrier forces 1-task-per-worker distribution so the pool
    dispatcher hands one task to each distinct worker. Returns (os.getpid(), elapsed_seconds).
    """
    import os
    from time import perf_counter
    t0 = perf_counter()
    try:
        osd_path = nif_convert._find_ube_body_osd()
        if osd_path is not None:
            nif_convert._cached_osd_load(osd_path)
    except Exception:
        pass
    if ube_body_ref_path is not None:
        try:
            nif_convert._cached_ube_body_verts(Path(ube_body_ref_path))
        except Exception:
            pass
    try:
        cbbe_p = nif_convert._find_cbbe_base_body("_1")
        ube_fb = nif_convert._find_ube_femalebody("_1")
        if cbbe_p and ube_fb:
            nif_convert._cached_cbbe_to_ube_delta(cbbe_p, ube_fb)
    except Exception:
        pass
    elapsed = perf_counter() - t0
    # Block until every worker has claimed an init task -- but WITH a timeout, so
    # a sibling worker dying mid-warm-up (native crash before it reaches the
    # barrier) can't wedge the survivors forever. The first waiter to time out
    # breaks the barrier and releases all the rest with BrokenBarrierError.
    try:
        barrier.wait(timeout=300)
    except Exception:
        pass  # broken/timed-out barrier: warm-up is best-effort, just proceed
    return (os.getpid(), elapsed)


def _prewarm_pool(
        pool: "ProcessPoolExecutor",
        num_workers: int,
        ube_body_ref_path: "str | Path | None",
) -> None:
    """Submit one warm-up task per worker and wait for all to complete.

    Without this, first-mod throughput is dominated by serial cold-start cost
    (import pynifly, load DLL, parse body OSD, load body ref NIF). Pre-warming
    runs these loads in parallel before any real conversion work hits the queue.
    """
    import multiprocessing
    if num_workers <= 0:
        return
    print(f"  pre-warming {num_workers} workers...")
    t0 = time.perf_counter()
    # Manager-backed Barrier survives the spawn-mode pickle boundary
    # and is shared across all worker processes. Manager itself runs
    # in a separate process and is torn down at the end of the warm-up.
    # Manager-backed Barrier survives the spawn-mode pickle boundary.
    manager = multiprocessing.Manager()
    try:
        # Barrier survives spawn-mode pickle boundary; shared across all workers.
        barrier = manager.Barrier(num_workers)
        ube_str = str(ube_body_ref_path) if ube_body_ref_path else None
        futures = [
            pool.submit(_warmup_worker, barrier, ube_str)
            for _ in range(num_workers)
        ]
        pids: set[int] = set()
        init_times: list[float] = []
        for fut in as_completed(futures):
            try:
                pid, elapsed = fut.result()
                pids.add(pid)
                init_times.append(elapsed)
            except Exception as e:
                warn(f"warm-up task failed: {plain_error(e)}",
                     consequence="the first real piece on that worker pays the cold start",
                     indent="    ")
    finally:
        manager.shutdown()
    total = time.perf_counter() - t0
    if init_times:
        print(f"    warm-up done in {total:.1f}s "
              f"(per-worker init avg {sum(init_times)/len(init_times):.1f}s, "
              f"max {max(init_times):.1f}s, "
              f"{len(pids)} distinct worker PID(s))")


def _pair_units(items: "list[tuple]") -> "list[list[tuple]]":
    """Group work items into the UNITS one worker converts in sequence.

    A `_0` and its `_1` (and a no-suffix `foo.nif`) SHARE files at the
    destination: one physics XML (`<stem>.xml` -- the finalize copies the
    authored one over it, then the collider patches rewrite it) and one `.tri`.
    The weight passes read that XML through `dst_path` to learn which shapes
    are colliders. With the pair on two workers, a `_1` whose leg-bend match
    reads the XML inside the window where its sibling's finalize has just
    restored the authored copy -- split collider not yet re-added -- sees no
    collider there and reweights it. Measured 2026-09-06 on three identical
    16-worker arms of the acceptance population: 5 shapes of 144 files bimodal,
    every one a `_1` collider proxy, 0 vertices moved, the same 0.8486 worst
    delta in each odd arm, while `--workers 1` is byte-identical run to run.
    One unit per base, in list order, gives the pool serial's sequence for
    exactly the files that share state and changes nothing else.
    #pair-unit-dispatch

    Keyed on the DESTINATION (`item[1]`), weight-agnostic. Units keep
    first-appearance order and list order within -- `_1`, `_0`, then the
    no-suffix model, which is the order `_resolve_armor_meshes` emits and the
    serial path runs.
    """
    units: "dict[str, list]" = {}
    order: "list[str]" = []
    for it in items:
        key = _weight_base_key(str(it[1]))
        if key not in units:
            units[key] = []
            order.append(key)
        units[key].append(it)
    return [units[k] for k in order]


class _UnitResults(list):
    """The results of one unit, plus what the worker printed while producing
    them (`output`). Still a list, so every caller that iterates results is
    unchanged. #worker-output"""
    output = ""


def _captured(call):
    """Run `call()` with stdout and stderr captured; returns (value, text).

    A pool worker's own stdout is NOT the run log: the log is a tee the parent
    installs after freeze_support(), which a spawned worker never reaches, and
    under the GUI a worker inherits a null device. So every WARN a pass printed
    in a worker was lost, in the launch mode users use. The parent prints the
    returned text, and there it reaches the log. #worker-output"""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        value = call()
    return value, buf.getvalue()


def _run_unit(fn, unit: "list[tuple]") -> list:
    """One worker, one unit, in order: one result per item. Module-level so the
    spawn-mode pool can pickle it. #pair-unit-dispatch

    What the conversions printed comes back as the list's `.output`.
    #worker-output"""
    values, text = _captured(lambda: [fn(it) for it in unit])
    results = _UnitResults(values)
    results.output = text
    return results


def _run_one(fn, item):
    """The isolated re-run's form of `_run_unit`: one item, with its printed
    output attached to the result as `worker_output`. #worker-output"""
    result, text = _captured(lambda: fn(item))
    try:
        result.worker_output = text
    except AttributeError:
        pass
    return result


def _echo_worker_output(text) -> None:
    """Print what a worker printed, here in the parent, where it reaches the log."""
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        try:
            sys.stdout.flush()
        except Exception:
            pass


class _NifPool:
    """Self-healing wrapper around the batch-shared NIF-conversion process pool.

    A native pynifly C++ crash kills a worker process, which `ProcessPoolExecutor`
    can't catch -- it marks the WHOLE pool broken, so every later `submit` raises.
    With one batch-global pool that previously meant a single bad NIF poisoned the
    rest of its mod AND every subsequent mod (all their meshes erroring out,
    invisible in-game). This wrapper rebuilds the pool after a break and re-runs
    the not-yet-completed items in ISOLATION (one at a time) so the true crasher
    is identified with certainty and dropped, while every innocent NIF still
    converts. The same object is threaded through the whole batch, so the rebuilt
    pool carries forward -- no cross-mod cascade.
    """

    # Stop rebuilding once this many ISOLATED items crash back-to-back: that's a
    # systemic failure (e.g. a broken pynifly DLL), not one poison NIF, so further
    # rebuilds are pointless churn -- error the rest and move on.
    GIVE_UP_AFTER = 5

    def __init__(self, max_workers, ube_body_ref_path=None, *,
                 pool_factory=None):
        self.max_workers = max(1, int(max_workers))
        self._ube_ref = ube_body_ref_path
        # Injectable for tests; defaults to a real ProcessPoolExecutor whose
        # workers die with this process. #workers-die-with-parent
        self._factory = pool_factory or self._new_process_pool
        self.pool = None
        self.rebuilds = 0
        self._ensure()

    def _new_process_pool(self):
        # BEFORE the pool exists, so every worker it ever spawns -- the pool
        # rebuilt after a crash included -- is born inside the job and dies
        # with this process however it ends. #workers-die-with-parent
        child_lifetime.tie_children_to_this_process()
        return ProcessPoolExecutor(max_workers=self.max_workers)

    def _ensure(self):
        if self.pool is None:
            self.pool = self._factory()

    def prewarm(self):
        self._ensure()
        _prewarm_pool(self.pool, self.max_workers, self._ube_ref)

    def _rebuild(self):
        old = self.pool
        self.pool = None
        if old is not None:
            try:
                old.shutdown(wait=False)   # workers already dead; don't block
            except Exception:
                pass
        self.rebuilds += 1
        self._ensure()

    def shutdown(self):
        if self.pool is not None:
            try:
                self.pool.shutdown(wait=True)
            except Exception:
                pass
            self.pool = None

    def run_batch(self, work_items, on_result, *, fn=None):
        """Run `fn` (default `_nif_convert_worker`) over every item, calling
        `on_result(ConvertResult)` exactly once per item. Survives worker process
        death: the crasher surfaces as an error result, all others convert.

        Items are dispatched as UNITS (`_pair_units`): every weight variant of
        one base runs on ONE worker, in list order, so a `_1` and its `_0` are
        never converted concurrently. #pair-unit-dispatch"""
        fn = fn or _nif_convert_worker
        items = list(work_items)
        if not items:
            return
        remaining = self._run_parallel(items, on_result, fn)
        if remaining:
            self._run_isolated(remaining, on_result, fn)

    def _run_parallel(self, items, on_result, fn):
        """Submit every unit at once; deliver every result that completed
        cleanly, and return the items of the units whose futures broke (the
        crasher, its unit-mates, any in-flight bystanders) for isolated
        recovery -- in the original list order, so a pair still re-runs in
        sequence. Rebuilds the pool if anything broke."""
        self._ensure()
        units = _pair_units(items)
        try:
            fut_to_unit = {self.pool.submit(_run_unit, fn, u): u for u in units}
        except Exception:
            # Pool already broken at submit time -> nothing ran; recover all.
            self._rebuild()
            return items
        broken: "list[list]" = []
        for fut in as_completed(fut_to_unit):
            unit = fut_to_unit[fut]
            try:
                results = fut.result()
            except Exception:
                broken.append(unit)   # worker death: nothing in this unit is certain
                continue
            _echo_worker_output(getattr(results, "output", ""))
            for r in results:
                on_result(r)
        if broken:
            self._rebuild()
        lost = {id(it) for u in broken for it in u}
        return [it for it in items if id(it) in lost]

    def _run_isolated(self, items, on_result, fn):
        """Re-run the uncertain items one at a time on a healthy pool. With a
        single item in flight, a pool break unambiguously blames THAT item, so we
        error exactly the crasher and rebuild for the next. Bounded by
        GIVE_UP_AFTER consecutive crashes (systemic failure)."""
        import functools
        consec_crashes = 0
        give_up = False
        for it in items:
            if give_up:
                on_result(nif_convert.ConvertResult(
                    src_path=it[0], dst_path=None, status="error",
                    reason="worker pool unrecoverable (systemic crash); "
                           "NIF not attempted"))
                continue
            self._ensure()
            try:
                res = self.pool.submit(functools.partial(_run_one, fn), it).result()
                _echo_worker_output(getattr(res, "worker_output", ""))
                on_result(res)
                consec_crashes = 0
            except Exception as e:
                # A worker killed by the OS for memory raises BrokenProcessPool
                # with no Python traceback, and the wording here ("died",
                # "systemic crash") was written for a broken pynifly DLL -- so
                # an out-of-memory death read to the user as the TOOL being
                # broken, and nothing pointed at the one lever that fixes it.
                # Attach the machine's live memory reading instead of guessing.
                # #commit-headroom
                on_result(nif_convert.ConvertResult(
                    src_path=it[0], dst_path=None, status="error",
                    reason=f"worker process died (isolated convert): "
                           f"{type(e).__name__}: {e}{_memory_hint()}"))
                self._rebuild()
                consec_crashes += 1
                if consec_crashes >= self.GIVE_UP_AFTER:
                    give_up = True


def _memory_hint() -> str:
    """Live memory reading to append to a worker-death message. #commit-headroom

    A worker killed by Windows for memory leaves NO Python exception -- the
    parent sees only BrokenProcessPool -- so the only way to tell that death
    apart from a native crash is to say what memory looked like when it
    happened. Returns "" when it cannot read, never raises: a diagnostic must
    not be able to turn one failed NIF into a failed run."""
    try:
        st = _memory_status()
        if not st or st.get("commit_free_gb") is None:
            return ""          # cannot read -- say nothing rather than guess
        if st["commit_free_gb"] > 4.0:
            return ""          # plenty free; this death was not about memory
        return (f" [low memory at the time: {st['avail_gb']:.1f} GB RAM and "
                f"{st['commit_free_gb']:.1f} GB commit charge free -- lower "
                f'"Worker processes" on the Run tab and try again]')
    except Exception:
        return ""


def _incremental_code_mtime() -> float:
    """Newest mtime of the converter's own code -- the `--incremental` reuse
    floor (a code change must invalidate every cached output).

    In a frozen onedir build the `src/*.py` sources are compiled into the PYZ
    and are NOT present on disk, so `glob('*.py')` yields nothing and the floor
    would silently collapse to the body-ref mtime alone -- letting a redeployed
    exe reuse meshes built by the OLD logic. Stat the executable instead: its
    mtime moves on every redeploy, which is exactly the 'code changed' signal.
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).stat().st_mtime
        except OSError:
            return 0.0
    return max((p.stat().st_mtime for p in Path(__file__).parent.glob("*.py")),
               default=0.0)


# Args that can change the BYTES of a converted NIF. Deliberately a small,
# justified allow-list rather than "every arg": over-inclusion is safe for
# correctness but makes incremental reuse useless as a DEFAULT (every
# `--only-mods X` iteration would invalidate the whole cache), and a useless
# default is one users turn off.
#
# NOT included, and why: `workers`/`incremental`/`list_only`/`plugins_only`/
# `render_previews` never touch mesh maths; `merged_name`/`esp_name`/
# `no_auto_merge`/`unmerged_patch_subdir` are ESP-only (verified: nif_convert
# imports neither ube_patcher nor esp/gui code).
#
# Mod-SELECTION flags (`only_mods`, `exclude_mods`, `overlay_mods`,
# `overlay_exclude_mods`) are also excluded: they change WHICH NIFs get
# produced, not what any single NIF's bytes are, so an already-converted
# up-to-date NIF stays valid regardless. CAVEAT, stated rather than hidden:
# output paths are first-writer-wins across source mods
# (`claimed_dst_paths`), so a different selection could in principle hand a
# contested path to a different source mod. That is a PRE-EXISTING property of
# incremental reuse, not something this fingerprint introduces, and folding
# selection in would cost the default its usefulness. A full (non-incremental)
# run remains the answer when source-mod priority changes.
_NIF_RELEVANT_ARGS = (
    "ube_body_ref",        # the fit target itself
    "convert_overlays",    # changes the converted mesh set + overlay handling
    "overlay_copy",
    "overlay_skip_male",
    "overlays_only",
    "no_textures",         # texture handling can reach paths baked into a NIF
    "copy_textures",
    "no_ube_native_scan",  # changes which meshes are treated as already-UBE
)


def _nif_config_fingerprint(args) -> str:
    """Stable hash of every setting that can change a converted NIF's bytes.

    WHY THIS EXISTS. The `--incremental` floor only ever compared mtimes:
    source NIF, converter code, body ref. But `nif_convert.py` reads **323
    distinct `CBBE2UBE_*` environment variables** (re-counted 2026-08-18; it
    was 135 when this was written, which is the point) -- clearance margins,
    follow ratios, jiggle strengths, pass on/off switches -- and NONE of them
    were in the floor. Flip one, re-run incrementally, and the
    converter reports "reusing N up-to-date NIFs" while the setting you changed
    never reached a single mesh. That is this project's documented dominant
    failure mode (a check that comes back clean because it measured nothing)
    wearing a new hat, and it is the thing that blocked making reuse a default.

    Env vars are collected BY PREFIX, never from a hand-maintained list, and
    that is why the count above tripling did not break anything. A literal
    list would drift the first time someone adds a flag --
    exactly the staleness that made the four-module whitelist in the project
    notes wrong (it predated `nif_convert` importing `fit_metrics`). A prefix
    scan cannot go stale.
    """
    import hashlib

    parts = []
    for k in sorted(os.environ):
        if k.startswith("CBBE2UBE_"):
            parts.append(f"env:{k}={os.environ[k]}")
    for name in _NIF_RELEVANT_ARGS:
        if hasattr(args, name):
            parts.append(f"arg:{name}={getattr(args, name)!r}")
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def _config_stamp_mtime(output_root, fingerprint: str) -> float:
    """Fold the config fingerprint into the mtime-based floor.

    Rather than bolt a second comparison onto the reuse check, this reuses the
    machinery that already works: a stamp file whose mtime moves ONLY when the
    fingerprint changes. A changed setting therefore makes the floor newer than
    every cached NIF and invalidates all of them, through exactly the same
    `dst.mtime > floor` test as a code edit.

    Written only when the content differs -- rewriting an identical stamp would
    bump its mtime every run and defeat reuse entirely (the failure mode where
    the optimisation silently never applies).
    """
    stamp = Path(output_root) / "_incremental_config.sha256"
    try:
        if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == fingerprint:
            return stamp.stat().st_mtime
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fingerprint + "\n", encoding="utf-8")
        return stamp.stat().st_mtime
    except OSError:
        # Unreadable/unwritable stamp must FAIL SAFE: return "now" so the floor
        # invalidates everything and the run converts fully, never silently
        # reuses against an unknown config.
        return time.time()


def _combined_output_names(merged_name: str, plugin_names_or_paths) -> "set[str]":
    """Lower-cased names of ALL our merged-Combined outputs to exclude from a
    load-order winner scan: the base `--merged-name` plus every ESL-split piece
    (`<stem>2.esp`, `<stem>3.esp`, ...) present in the given plugin list.

    Uses the REAL merged name (never a hardcoded default) so a custom
    `--merged-name` and its split pieces are still excluded -- otherwise a
    coverage/winner pass reads the Combined's own overrides as load-order
    winners and mis-covers. Accepts an iterable of plugin names or Paths.
    """
    stem = Path(merged_name).stem.lower()
    names = {merged_name.lower()}
    for n in plugin_names_or_paths:
        nl = Path(n).name.lower()
        if nl.startswith(stem) and nl.endswith(".esp"):
            names.add(nl)
    return names


def _body_mod_names(mods_root: Path) -> "set[str]":
    """Mod folders that ship a race body -- the CBBE 3BA or the UBE body file
    BodySlide builds (src/zeroed_body.KINDS) -- so an `auto` run skips them:
    they are 3BA-rigged and pass the content filter, but they ARE the body,
    not armour. #body-mod-exclusion

    By what a folder SHIPS, not by which body the fit uses. The exclusion used
    to be the folders of the three body lookups, so it moved with the
    reference: once the fit used BodySlide's zeroed build, the 3BA body mod
    itself fell out of the exclusion and its collision-body NIFs entered
    All-mods runs; and a Reference bodies pick changed which mods converted,
    while the GUI's mod list (built without the pick) could not show it."""
    from . import zeroed_body as _zb
    out = set()
    try:
        dirs = [d for d in mods_root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for kind, (out_path, out_file, _verts, _label) in _zb.KINDS.items():
        for w in ("_0", "_1"):
            parts = [p for p in f"{out_path}/{out_file}{w}.nif".split("/") if p]
            for d in dirs:
                if _zb._ci_join(d, parts) is not None:
                    out.add(d.name)
    return out


def _find_ube_body_ref(search_roots: list[Path] | None = None) -> Path | None:
    """Scan MO2 mods folders for the best UBE body reference NIF —
    preferring sources that DON'T have the user's BodySlide preset
    baked into them. Such sources let RaceMenu apply slider deltas
    to the injected BaseShape from a clean baseline, matching how
    the actor's femalebody.nif morphs at runtime.

    Priority order:
      1. A published UBE conversion mod's BodySlide ShapeData NIF
         (the source NIF that BodySlide BUILDS from — un-morphed).
         Heuristic: path contains 'CalienteTools/BodySlide/ShapeData'
         AND has BaseShape + VirtualBody.
      2. The UBE 2.0 Release Body.nif template — has BaseShape only.
         No VirtualBody, but body injection only needs BaseShape;
         convert_nif_phase2 handles missing VirtualBody gracefully.
      3. Any NIF with BaseShape (>=20k v) + VirtualBody (>=10k v).
         Last-resort fallback (will be preset-baked on the user's
         build, which means RaceMenu may double-morph at runtime —
         minor visual issue but still functional).

    Why preset-baked refs hurt:
      * Phase 2 body-swap injects BaseShape verbatim from the ref.
      * RaceMenu/BodyMorph applies slider deltas to the actor's
        equipped slot-32 piece at runtime.
      * If the injected BaseShape was already preset-baked, the
        runtime deltas add ON TOP of the preset → double-morphed
        body shape → cloth's TRI deltas (propagated from template
        body OSD) no longer match the body's actual displacement
        → loincloth and other tight cloth clip into the body.
    """
    if search_roots is None:
        # BodySlide's zeroed UBE build as the game loads it -- chosen by MO2
        # priority and verified, not the first mod alphabetically that ships
        # the path (#zeroed-body-refs). An explicit search_roots (tests, other
        # instances) keeps the scan below.
        from .nif_convert_bodyrefs import _ube_body_override, _zeroed_ref
        # An explicit UBE body (the Reference bodies dialog, the settings
        # picker, CBBE2UBE_UBE_BODY_1) is injected too -- not only aimed at.
        override = _ube_body_override("_1")
        if override is not None:
            return override
        zeroed = _zeroed_ref("ube", "_1")
        if zeroed is not None:
            return zeroed
        # Portable: the auto-discovered MO2 mods root (no hardcoded paths).
        mr = paths.mods_root()
        search_roots = [mr] if mr is not None else []
    # Highest priority: the user's BodySlide-output UBE body (preset baked into
    # BaseShape). Injecting it avoids double-morphing at runtime. Located by
    # scanning for the !UBE\Body tangent output — never by a fixed mod name.
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            mod_dirs = sorted(d for d in root.iterdir() if d.is_dir())
        except OSError:
            mod_dirs = []
        for mod in mod_dirs:
            cand = (mod / "meshes" / "!UBE" / "Body"
                    / "femalebody_tangent_1.nif")
            if cand.is_file():
                return cand
    # Lazy import: keep auto_convert importable without pynifly when body-swap isn't needed.
    proj_root = Path(__file__).resolve().parent.parent
    pn = str(proj_root / ".pynifly")
    if pn not in sys.path:
        sys.path.insert(0, pn)
    try:
        from pyn import pynifly  # type: ignore
    except ImportError:
        return None

    def _check(p: Path):
        """Return (has_base, has_virtual) or None on parse failure."""
        try:
            nf = pynifly.NifFile(filepath=str(p))
            shapes = {s.name: len(s.verts) for s in nf.shapes}
            return (
                shapes.get("BaseShape", 0) >= 20000,
                shapes.get("VirtualBody", 0) >= 10000,
            )
        except Exception:
            return None

    shapedata_with_both: Path | None = None
    shapedata_base_only: Path | None = None
    template_p: Path | None = None
    any_match: Path | None = None

    for root in search_roots:
        if not root.is_dir():
            continue
        candidates = list(root.rglob("*.nif"))
        # Sort: 'UBE' in path first, then path depth.
        candidates.sort(
            key=lambda p: (
                0 if ("ube" in str(p).lower() or "!ube" in str(p).lower())
                else 1,
                len(p.parts),
            )
        )
        if len(candidates) > 1500:
            warn(f"{len(candidates)} body-ref candidates", where=f"under {root}",
                 consequence="scanning only the first 1500 by priority -- a UBE body "
                             "in a deeply-nested non-'ube' path could be missed",
                 fix="set the UBE body reference explicitly if the wrong body is picked",
                 file=sys.stderr)
        for p in candidates[:1500]:
            r = _check(p)
            if r is None:
                continue
            has_base, has_virtual = r
            if not has_base:
                continue
            pathstr = str(p).lower()
            is_shapedata = (
                "calientetools" in pathstr and "shapedata" in pathstr
            )
            is_template = "release body.nif" in pathstr
            if is_shapedata and has_virtual and shapedata_with_both is None:
                shapedata_with_both = p
            elif is_template and template_p is None:
                template_p = p
            elif is_shapedata and shapedata_base_only is None:
                shapedata_base_only = p
            elif has_virtual and any_match is None:
                any_match = p

    return shapedata_with_both or template_p or shapedata_base_only or any_match


def count_pass_failures(nif_results) -> dict:
    """Swallowed pass failures, counted per pass name.

    Read from the per-piece `reason` string ON PURPOSE.
    `nif_convert.pass_failure_summary()` reads that module's counters, which
    live in the WORKER process; the parent never sees them, so a report built on
    it would report nothing and read as "no failures". `reason` is the only
    channel that crosses the pool boundary -- see `_piece_pass_failures`.

    NEVER RAISES. `write_conversion_summary` wraps everything in a blanket
    `except Exception: return None`, so a throw here would not surface as an
    error -- it would silently delete the whole summary file. That exact shape
    (a NameError swallowed into a missing report) has happened here before.
    """
    out: dict = {}
    for r in nif_results or ():
        for part in (getattr(r, "reason", "") or "").split("; "):
            part = part.strip()
            if not part.startswith("PASS FAILED "):
                continue          # a fragment of some other reason; ignore
            label = part[len("PASS FAILED "):].split(" (", 1)[0].strip()
            if label:
                out[label] = out.get(label, 0) + 1
    return out


def count_pass_failure_pieces(nif_results) -> dict:
    """{pass name -> [pieces it failed on]}. The counts' missing half.

    `count_pass_failures` returns "hdt_xml_unresolved: 30" and throws the piece
    names away, so answering "WHICH 30, and is any of them ours?" meant trying
    to re-derive the population from the pack afterwards. That was attempted on
    2026-08-23 and the attempt was MIS-SCOPED in a way worth recording: the
    probe looked at pack NIFs that DECLARE a physics XML, but a piece whose XML
    never resolved ships WITHOUT a pointer -- so the failing population was
    invisible to the filter by construction, and the probe returned a confident
    "0 unresolved". The filter was the population, again.

    The converter already knows the answer at the moment it fails. Recording it
    costs nothing and replaces a census that cannot be scoped correctly from
    outside.

    NEVER RAISES, for the same reason as its siblings.
    """
    out: dict = {}
    for r in nif_results or ():
        try:
            name = Path(str(getattr(r, "dst_path", "") or "")).name
        except Exception:
            name = ""
        for part in (getattr(r, "reason", "") or "").split("; "):
            part = part.strip()
            if not part.startswith("PASS FAILED "):
                continue
            label = part[len("PASS FAILED "):].split(" (", 1)[0].strip()
            if label:
                out.setdefault(label, [])
                if name and name not in out[label]:
                    out[label].append(name)
    return out


def count_pass_effects(nif_results) -> dict:
    """{change tag -> [pieces it touched]}. The SIBLING of the failures counter.

    WHY IT EXISTS, and it is not hypothetical. `nif_convert._note_pass_effect`
    records WHICH CHANGE altered a piece, riding the same `reason` channel
    failures use -- and the 2026-08-23 reconvert proved that half a mechanism is
    none of it. The worker recorded its effects correctly; nothing on the PARENT
    side ever read them, so they reached neither the log nor the report, and
    `change_attribution.py` reported "none recorded" on a run where the change
    demonstrably fired (18 violations -> 0). The failures counter had this
    collector from the start; the effects one did not.

    Returns the PIECE NAMES, not merely a count: "which change touched
    something" is only actionable if it can name what to go and look at.

    NEVER RAISES, for the same reason as `count_pass_failures`: the caller sits
    inside a blanket `except Exception: return None` that would turn a throw
    into a silently missing report rather than a visible error.
    """
    out: dict = {}
    for r in nif_results or ():
        try:
            name = Path(str(getattr(r, "dst_path", "") or "")).name
        except Exception:
            name = ""
        for part in (getattr(r, "reason", "") or "").split("; "):
            part = part.strip()
            if not part.startswith("CHANGED BY "):
                continue          # a fragment of some other reason; ignore
            tag = part[len("CHANGED BY "):].split(" (", 1)[0].strip()
            if tag:
                out.setdefault(tag, [])
                if name and name not in out[tag]:
                    out[tag].append(name)
    return out


def _pack_pass_failures(ok) -> dict:
    """`count_pass_failures` rolled up across `[(source_dir, AutoConvertResult)]`.

    Never raises, for the same reason as `count_pass_failures`: both callers sit
    inside a blanket `except Exception: return None` that would turn a throw into
    a silently missing report file rather than a visible error.
    """
    out: dict = {}
    for _s, r in ok or ():
        for label, n in count_pass_failures(
                getattr(r, "nif_results", None)).items():
            out[label] = out.get(label, 0) + n
    return out


def _pack_pass_effects(ok) -> dict:
    """`count_pass_effects` rolled up across the pack. Never raises."""
    return _roll_up_named(ok, count_pass_effects)


def _pack_pass_failure_pieces(ok) -> dict:
    """`count_pass_failure_pieces` rolled up across the pack. Never raises."""
    return _roll_up_named(ok, count_pass_failure_pieces)


def _roll_up_named(ok, fn) -> dict:
    """{key -> merged, de-duplicated piece list} across mods. Never raises."""
    out: dict = {}
    for _s, r in ok or ():
        for key, pieces in fn(getattr(r, "nif_results", None)).items():
            out.setdefault(key, [])
            for p in pieces:
                if p not in out[key]:
                    out[key].append(p)
    return out


@dataclass
class AutoConvertResult:
    source_dir: Path
    output_dir: Path
    # Primary ESP fields — backward compat; prefer source_esps / output_esps.
    source_esp: Path | None = None
    output_esp: Path | None = None
    esp_stats: dict = field(default_factory=dict)
    # All source ESPs + corresponding output patches (same length, same order).
    source_esps: list[Path] = field(default_factory=list)
    output_esps: list[Path] = field(default_factory=list)
    esp_stats_list: list[dict] = field(default_factory=list)
    nif_results: list[nif_convert.ConvertResult] = field(default_factory=list)
    textures_copied: int = 0
    notes: list[str] = field(default_factory=list)
    nif_load_failures: list[Path] = field(default_factory=list)
    # Source ESPs whose patch generation raised -> their ARMA/ARMO is absent
    # from the merge. Tracked separately so it counts toward the failure total.
    esp_gen_failures: list[str] = field(default_factory=list)
    # Source ESPs skipped because they have no ARMA group (no armor at all).
    # Large bundle mods ship many landscape/quest/patch ESPs alongside a few
    # armour ones; these have nothing to convert and are NOT failures.
    esp_skipped_no_armor: int = 0
    # Armour meshes resolved from a DIFFERENT mod via the VFS (BodySlide output /
    # replacer / patch). Surfaced in the coverage report.
    vfs_other_mod_count: int = 0
    # {weight-agnostic dest base -> the weight suffixes the SOURCE actually
    # ships}. `_complete_weight_partners` needs it to tell a `_0` it FILLED
    # from one the converter genuinely produced. Empty = no knowledge, which
    # that function treats as "never refresh". #stale-weight-partner
    source_weight_variants: dict = field(default_factory=dict)
    # Weight bases (same keys) this run left to a mod that ships them built for
    # UBE. The partner fill must never write into one. #supersede-whole-base
    superseded_weight_bases: set = field(default_factory=set)
    # Postflight per-NIF invariant violations on the FINAL output (zero-vertex
    # shapes; over-cap single-partition shapes). Surfaced + counted as warnings.
    nif_invariant_warnings: list = field(default_factory=list)
    # VirtualBody re-hide failures on the FINAL output: a failed re-hide leaves a
    # VISIBLE VirtualBody (the "blue body double"). Surfaced + counted as a
    # warning (a visible defect, not a CTD).
    virtualbody_rehide_failures: list = field(default_factory=list)

    @property
    def nif_converted(self) -> int:
        return sum(1 for r in self.nif_results if r.status.startswith("converted"))

    @property
    def nif_skipped(self) -> int:
        return sum(1 for r in self.nif_results if r.status.startswith("skipped"))

    @property
    def nif_errors(self) -> int:
        """NIFs whose conversion raised an exception. Distinct from benign skips."""
        return sum(1 for r in self.nif_results if r.status == "error")

    @property
    def nif_error_results(self) -> "list[nif_convert.ConvertResult]":
        return [r for r in self.nif_results if r.status == "error"]

    # A piece that fails to write its BODYTRI still CONVERTS -- the mesh is
    # fine, it just has no body morphs, so it stops following the player's
    # sliders. That is a visible defect in game, and it was invisible in every
    # counter here: a 161-mod run reported "0 hard failures / 0 nif errors"
    # while 4 pieces had silently lost their TRI to a locked-rename race. The
    # detail sat in a per-mod .txt reason that nobody reads. Count it.
    _MORPH_LOSS_MARKERS = ("auto-TRI", "body-morph unavailable",
                           "BODYTRI injection failed")

    @property
    def nif_morph_loss_results(self) -> "list[nif_convert.ConvertResult]":
        """Converted NIFs that came out WITHOUT working body morphs."""
        return [r for r in self.nif_results
                if any(m in (r.reason or "") for m in self._MORPH_LOSS_MARKERS)]

    @property
    def nif_morph_losses(self) -> int:
        return len(self.nif_morph_loss_results)

    @property
    def nif_partial(self) -> int:
        """NIFs that converted but dropped >=1 shape (invisible piece in-game)."""
        return sum(1 for r in self.nif_results
                   if getattr(r, "dropped_shapes", None))

    @property
    def nif_copy_count(self) -> int:
        return sum(1 for r in self.nif_results if r.status == "converted (copy)")

    @property
    def nif_swap_count(self) -> int:
        return sum(1 for r in self.nif_results if r.status == "converted (body-swap)")

    def write_report(self, path: Path) -> None:
        from .version import __version__ as _app_version
        try:
            from .build_info import stamp_line as _stamp_line
            _build = _stamp_line()
        except Exception:
            _build = f"build {_app_version}"
        lines = [
            f"CBBE-to-UBE auto-conversion report (v{_app_version})",
            f"{_build}",
            f"source : {self.source_dir}",
            f"output : {self.output_dir}",
            "",
            f"ESP ({len(self.source_esps)} patched)",
        ]
        esps_to_report = (
            list(zip(self.source_esps, self.output_esps, self.esp_stats_list))
            if self.source_esps else (
                [(self.source_esp, self.output_esp, self.esp_stats)]
                if self.source_esp is not None else []
            )
        )
        for src_e, out_e, stats in esps_to_report:
            lines.append(f"  source         : {src_e}")
            lines.append(f"  output         : {out_e}")
            for k, v in (stats or {}).items():
                if k == "output":  # already printed above
                    continue
                lines.append(f"  {k:15}: {v}")
            lines.append("")
        lines.append("")
        lines.append(f"NIFs ({len(self.nif_results)} total)")
        lines.append(f"  copy            : {self.nif_copy_count}")
        lines.append(f"  body-swap       : {self.nif_swap_count}")
        lines.append(f"  skipped         : {self.nif_skipped}")
        if self.nif_errors:
            lines.append(f"  ! errors        : {self.nif_errors} "
                         f"(conversion raised an exception)")
            for r in self.nif_error_results:
                lines.append(f"      {r.src_path.name}: {r.reason}")
        if self.nif_morph_losses:
            lines.append(f"  ! NO BODY MORPH : {self.nif_morph_losses} "
                         f"(converted, but the .tri was not written -- these "
                         f"will NOT follow body sliders in game)")
            for r in self.nif_morph_loss_results:
                lines.append(f"      {r.src_path.name}")
        if self.nif_load_failures:
            lines.append(f"  ! load failures : {len(self.nif_load_failures)} "
                         f"(re-load the output via pynifly failed)")
        if self.nif_invariant_warnings:
            lines.append(f"  ! invariant warn: {len(self.nif_invariant_warnings)} "
                         "(zero-vert / over-cap partition on final output)")
            for w in self.nif_invariant_warnings:
                lines.append(f"      {w}")
        # A pass that RAISED and was swallowed still converted the piece, so it
        # shows up in NO bucket above -- the run reads as clean while a pass may
        # have failed on every single piece. That is the "a BROKEN pass reads as
        # a failed design" trap, and it has cost verdicts here before.
        #
        # `nif_convert.pass_failure_summary()` CANNOT serve this: it reads the
        # WORKER's module state, which the parent process never sees, so calling
        # it here would report an empty dict and read as "no failures". The
        # per-piece `reason` string is the only channel that crosses the pool
        # boundary (see `_piece_pass_failures`), so aggregate from that.
        pass_fails = count_pass_failures(self.nif_results)
        if pass_fails:
            lines.append(f"  ! pass failures : {sum(pass_fails.values())} "
                         f"across {len(pass_fails)} pass(es) -- the piece still "
                         f"converted, so these are NOT counted as errors")
            for label, n in sorted(pass_fails.items(), key=lambda kv: (-kv[1], kv[0])):
                lines.append(f"      {n:>5} x  {label}")
        lines.append(f"  textures copied : {self.textures_copied}")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            for n in self.notes:
                lines.append(f"  - {n}")

        skipped = [r for r in self.nif_results if r.status.startswith("skipped")]
        if skipped:
            lines.append("")
            lines.append("PHASE 2 NEEDED (inline body shape — won't be visible until phase 2 ships):")
            for r in skipped:
                rel = r.src_path.relative_to(self.source_dir) if self.source_dir in r.src_path.parents else r.src_path
                lines.append(f"  - {rel}   reason={r.reason}")

        converted = [r for r in self.nif_results if r.status.startswith("converted")]
        if converted:
            lines.append("")
            lines.append("Converted NIFs:")
            for r in converted:
                rel = r.src_path.relative_to(self.source_dir) if self.source_dir in r.src_path.parents else r.src_path
                lines.append(f"  - {rel}   shapes={r.armor_shapes}")

        # Validation warnings from successful conversions (zero-weight verts,
        # stale TRI entries, etc.) — non-fatal but surfaced for the user.
        warned = [r for r in self.nif_results
                  if r.status.startswith("converted") and r.reason]
        if warned:
            lines.append("")
            lines.append("Validation warnings:")
            for r in warned:
                rel = r.src_path.relative_to(self.source_dir) if self.source_dir in r.src_path.parents else r.src_path
                lines.append(f"  - {rel}")
                for w in r.reason.split("; "):
                    lines.append(f"      ! {w}")

        # PARTIAL conversions: converted but a shape was dropped -> absent/invisible.
        partial = [r for r in self.nif_results
                   if getattr(r, "dropped_shapes", None)]
        if partial:
            lines.append("")
            lines.append("PARTIAL conversions (shapes DROPPED -> invisible in-game):")
            for r in partial:
                rel = r.src_path.relative_to(self.source_dir) if self.source_dir in r.src_path.parents else r.src_path
                lines.append(f"  - {rel}   dropped={r.dropped_shapes}")

        path.write_text("\n".join(lines), encoding="utf-8")


def _find_meshes_root(source_dir: Path) -> Path | None:
    """Locate the `meshes/` directory inside a source mod folder.

    Some mods put meshes directly at the top level; others nest one level
    (e.g. under a "Data/" or per-version folder).
    """
    candidates = list(source_dir.rglob("meshes"))
    # Pick the shallowest (closest to source_dir) that's actually a directory
    candidates = sorted(
        [c for c in candidates if c.is_dir()],
        key=lambda p: len(p.parts),
    )
    return candidates[0] if candidates else None


def _find_textures_root(source_dir: Path) -> Path | None:
    candidates = list(source_dir.rglob("textures"))
    candidates = sorted(
        [c for c in candidates if c.is_dir()],
        key=lambda p: len(p.parts),
    )
    return candidates[0] if candidates else None


def _echo_active_experiment_flags() -> None:
    """Print every `CBBE2UBE_*` that is actually set for THIS run, at the top.

    #settings-did-not-apply. The GUI reads `CBBEtoUBE_settings.json` at STARTUP and
    hands the variables to the run, so a settings file edited while the GUI is
    already open has NO effect -- that run silently uses the old in-memory state.
    A full ~1h reconvert was once spent testing a flag that never got set, and there
    was no way to tell from the log afterwards: the only evidence was the ABSENCE of
    a pass's own message, which is indistinguishable from the pass having nothing to
    do. Echoing what is actually in the environment turns "did my setting apply?"
    into a fact you can read at the top of the log instead of an inference.

    Deliberately prints the raw environment rather than the settings file: the
    environment is what the conversion actually reads, and the whole failure mode was
    the two disagreeing."""
    try:
        skip = ("MO2_INI", "MODS_ROOT", "GAME_DATA", "CONFIG", "OUT_MOD", "NO_PAUSE")
        act = {k: v for k, v in os.environ.items()
               if k.startswith("CBBE2UBE_") and str(v).strip()
               and not any(s in k for s in skip)}
        if act:
            print(f"\n  active flags ({len(act)}): "
                  + ", ".join(f"{k[9:]}={v}" for k, v in sorted(act.items())))
        else:
            print("\n  active flags: none (all defaults)")
        # The env echo shows OVERRIDES only: a default promoted in code and a
        # setting silently lost print the same line. These say which build
        # this is and what every setting RESOLVES to.
        from . import build_info
        for ln in build_info.echo_lines():
            print(ln)
    except Exception:
        pass          # never let a diagnostic line break a run
    _warn_unseen_settings()


def _warn_unseen_settings() -> None:
    """Name any option this build added that the saved settings have never seen.

    The flag echo above reports what the run HAS. It cannot report what the run is
    MISSING, because an option absent from `CBBEtoUBE_settings.json` means "at its
    default" -- which in the log is identical to one deliberately switched off.

    That gap cost a full reconvert on 2026-07-27: two options built that day shipped
    default-OFF, the settings file predated them, the echo listed only the older
    flags, and an hour of conversion produced none of the intended work. Nothing in
    the output was wrong -- it simply was not the run that was asked for. Printed
    beside the echo so both halves of "what am I actually running" appear together,
    at the top, before any work starts."""
    try:
        from . import gui_settings as gs
        baseline, new = gs.unseen_settings()
        if new:
            warn(f"{len(new)} option(s) were added to this build since "
                 "your settings were last saved.", level=NOTE, indent="\n  ")
            print("        They run at their DEFAULT, which is not the same as you "
                  "having chosen it:")
            for s in new[:8]:
                print(f"          - {s.label}  (default: {s.default})")
            if len(new) > 8:
                print(f"          ... and {len(new) - 8} more")
            print("        Review them on the GUI's tabs; any settings change saves, "
                  "and that save records them.")
        elif not baseline:
            warn("your saved settings predate new-option tracking, so an "
                 "option added later", level=NOTE, indent="\n  ")
            print("        cannot be told apart from one you left off. Save settings "
                  "once to record a baseline.")
    except Exception:
        pass          # never let a diagnostic line break a run


def _discover_master_data_dirs(source_dir: Path) -> list[Path]:
    """Auto-discover directories that may contain master ESMs and UBE race plugins.

    Walks up from `source_dir` (typically `mods/<modname>/`) looking for
    `Stock Game/Data` or `Game Root/Data`, and adds every sibling mod folder so
    UBE race plugins (KhajiitUBE.esp, etc.) are found.

    Returns existing directories in priority order; empty list if none found.
    """
    candidates: list[Path] = []
    for parent_depth in range(1, 4):
        try:
            base = source_dir
            for _ in range(parent_depth):
                base = base.parent
        except Exception:
            continue
        for sub in ("Stock Game/Data", "Game Root/Data", "Data"):
            d = base / sub
            if d.is_dir() and (d / "Skyrim.esm").is_file():
                if d not in candidates:
                    candidates.append(d)
    # Sibling mod folders so UBE race discovery sees KhajiitUBE.esp etc.
    try:
        mods_root = source_dir.parent
        if mods_root.is_dir() and mods_root.name.lower() == "mods":
            for sibling in mods_root.iterdir():
                if sibling.is_dir() and sibling != source_dir:
                    if sibling not in candidates:
                        candidates.append(sibling)
    except (OSError, PermissionError):
        pass
    return candidates


def _find_source_esps(source_dir: Path) -> list[Path]:
    """Find ALL plausible CBBE armor ESPs in a mod folder.

    Returns every .esp/.esm/.esl not in a backup/UBE subfolder, sorted by
    (depth, name). Patching ALL of them is necessary: mods that ship multiple
    ESPs with disjoint ARMA/ARMO sets need every one covered, or some armor
    categories have no UBE armature and render invisible on UBE characters.
    """
    # Facegen dirs are named after the source plugin (facegeom\Plugin.esp\)
    # so rglob("*.esp") can match a directory — skip anything under these paths.
    _NON_PLUGIN_PARTS = {"meshes", "textures", "facegendata", "facegeom",
                         "facetint"}
    # Scan .esm/.esl too: bespoke armor mods (quest mods, bespoke-armor masters, ...) ship
    # as masters. Vanilla/DLC ESMs are excluded by filename; CC armor ESLs are
    # valid sources (the cc* "Alternative Armors" series ships 3BA builds).
    _MASTER_SKIP = {m.lower() for m in ube_patcher.VANILLA_DLC_MASTERS}
    _MASTER_SKIP.add("_resourcepack.esl")
    candidates = []
    for _ext in ("*.esp", "*.esm", "*.esl"):
        for p in source_dir.rglob(_ext):
            if not p.is_file():
                continue  # e.g. a facegen subfolder literally named "<plugin>.esp"
            name_lower = p.name.lower()
            if name_lower in _MASTER_SKIP:
                continue  # vanilla/DLC master -> handled by the vanilla path
            # Test the MOD-RELATIVE path only. `p` comes from an absolute
            # rglob, so p.parts carries the drive and every ancestor -- a
            # modlist living under any folder named e.g. "backup" or "UBE"
            # matched on the ancestor and returned ZERO source plugins for
            # EVERY mod, which is total silent loss: no patches, no coverage.
            try:
                parts_lower = [s.lower() for s in p.relative_to(source_dir).parts]
            except ValueError:
                parts_lower = [s.lower() for s in p.parts]
            # Skip OUR OWN generated patch ESPs by their actual naming
            # convention ("<source> UBE patch.esp"), and plugins sitting in a
            # UBE subfolder. Both were previously caught by testing every path
            # part for the SUBSTRING "ube", which was far too broad: it also
            # swallowed the containing MOD FOLDER, silently making every
            # "... - UBE" mod unreachable (duplicating the now-retired name
            # hint) and skipping unrelated mods whose name merely contains the
            # letters, e.g. a "Custom Cubemaps" folder. Already-UBE MESHES are
            # gated precisely by _is_already_ube_model on the model path instead.
            if name_lower.endswith("ube patch.esp"):
                continue
            # ...and by the name they carry now, '<source> (CBBEtoUBE src).esp':
            # at a mod root in the legacy root-write mode. #source-patch-rename
            if name_lower.endswith(_SRC_PATCH_SUFFIX.lower()):
                continue
            if any(s in ("ube", "!ube") or "backup" in s for s in parts_lower):
                continue
            if any(s in _NON_PLUGIN_PARTS for s in parts_lower):
                continue  # a plugin buried under meshes\/textures\ isn't a plugin
            candidates.append(p)
    candidates.sort(key=lambda p: (len(p.parts), p.name.lower()))
    return candidates


def _vanilla_sweep_esps(source_dir: Path) -> "list[Path]":
    """Vanilla sweep: when the source folder IS the game Data dir (identified
    by Skyrim.esm at its root), the source plugins are the vanilla/DLC masters
    themselves, in load order.

    Vanilla armor coverage used to be INCIDENTAL: a vanilla mesh converted only
    when some mod in the load order happened to carry an override of its ARMA
    (e.g. a bugfix patch), so any piece nobody overrides was never converted,
    got no UBE armature, and rendered invisible on UBE actors. Passing the game
    Data dir as the LAST (lowest-priority) source makes the base game itself a
    source mod: every deforming DefaultRace ARMA is planned, meshes resolve
    through the normal VFS -> loose -> BSA chain, and merge-time link dedup
    keeps the mod-source link wherever both cover the same armor.

    Returns [] for a normal mod folder (no Skyrim.esm at the root).
    """
    if not (source_dir / "Skyrim.esm").is_file():
        return []
    return [source_dir / m for m in ube_patcher.VANILLA_DLC_MASTERS
            if (source_dir / m).is_file()]


def _vanilla_links_check(sp_lines, results, coverage_sole: bool):
    """(vanilla/DLC links in the delivered INI, whether the sweep is DEAD).

    The vanilla-coverage assertion. Crashes are caught by the sweep pass's own
    isolation, but a SILENT hole (the sweep ran, nothing vanilla got linked)
    would only show up as invisible armour in game. Dead is only ever claimed
    when a vanilla sweep source ran this batch.

    #vanilla-links-delivered (2026-09-25): which count decides depends on what
    the game loads. With the winner-scan coverage as the SOLE generator the
    per-source patches are left unmerged, so the sweep source's own link count
    scores a file nothing loads: a coverage change that dropped every vanilla
    armour still passed. There the DELIVERED count decides. In the fallback
    merge the per-source patches are what ships, and the sweep source's own
    contribution stays the precise form (mod-driven links to vanilla records
    would mask a dead sweep in the delivered count)."""
    van = {m.lower() for m in ube_patcher.VANILLA_DLC_MASTERS}
    van_links = 0
    for ln in sp_lines:
        if not ln.startswith("filterByArmors="):
            continue
        if ln.split("=", 1)[1].split("|", 1)[0].lower() in van:
            van_links += 1
    sweep_links = None
    for rsrc, r, rerr in results:
        if not _vanilla_sweep_esps(rsrc):
            continue
        sweep_links = 0
        if rerr is None and r is not None:
            for st in (r.esp_stats_list or []):
                sweep_links += int(st.get("skypatcher_link_targets", 0) or 0)
    if sweep_links is None:
        return van_links, False            # no sweep this batch: nothing to assert
    if coverage_sole:
        return van_links, van_links == 0
    return van_links, sweep_links == 0


# Structured record of everything that FAILED to convert this run, mirrored
# from the console summary as it prints. Written to
# CBBEtoUBE_last_failures.json next to the run log every run (empty list on a
# clean run so a reader can never see a PREVIOUS run's failures) -- the GUI
# shows it as an end-of-run popup; CLI users have the same info in the log.
_RUN_FAILURES: "list[dict]" = []


def _record_failure(kind: str, source, item, detail: str = "",
                    severity: str = "failure") -> None:
    """`severity` is "failure" (it did not convert) or "warning" (it converted,
    but the user must hear about it). The GUI words its end-of-run popup from
    it (src/failure_summary.py). #run-warnings"""
    _RUN_FAILURES.append({
        "kind": str(kind), "source": str(source),
        "item": str(item), "detail": str(detail)[:400],
        "severity": str(severity)})


def _failures_file_path() -> Path:
    """Next to the run log: the one location the GUI and the frozen exe agree
    on (CBBE2UBE_RUN_LOG's dir when a parent pinned it, else exe/repo dir)."""
    pinned = os.environ.get("CBBE2UBE_RUN_LOG", "").strip()
    if pinned:
        return Path(pinned).parent / "CBBEtoUBE_last_failures.json"
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "CBBEtoUBE_last_failures.json"
    return Path(__file__).resolve().parent.parent / "CBBEtoUBE_last_failures.json"


def _write_failures_file() -> None:
    import json as _json
    from .atomic_io import atomic_write_bytes
    try:
        # Atomic, like the report: a death during this write used to leave a
        # torn file where the GUI's popup expected a list. #report-checkpoint
        atomic_write_bytes(_failures_file_path(),
                           _json.dumps({"failures": _RUN_FAILURES}, indent=1)
                           .encode("utf-8"))
    except OSError as _e:
        # THE FAILURE REPORT ITSELF. Swallowing this means a run with failures
        # looks exactly like a clean one to anything reading the file -- the
        # worst place in the pipeline to be quiet.
        import sys as _sys
        warn(f"could not write the failures file ({plain_error(_e)})",
             consequence=f"{len(_RUN_FAILURES)} recorded failure(s) will not appear there",
             fix="check that the folder beside the exe is writable",
             file=_sys.stderr)


def _warn_if_settings_file_malformed() -> bool:
    """True, with a warning recorded, when the settings file did not parse.

    The run's own echo says "SETTINGS FILE MALFORMED ... running at DEFAULTS",
    and until #run-warnings that was all: measured 2026-09-15, the run then
    ended "=== all clear ===" with exit 0 -- a whole reconvert at the wrong
    settings, reported clean. Never raises."""
    try:
        from . import build_info
        sf = build_info.settings_file_status()
    except Exception:
        return False
    if sf.get("status") != "malformed":
        return False
    _record_failure("settings file unreadable", "CBBEtoUBE_settings.json",
                    str(sf.get("path")),
                    "this run used DEFAULT settings; restore "
                    "CBBEtoUBE_settings.json.bak before the next save cements them",
                    severity="warning")
    return True


def _warn_if_skypatcher_missing() -> bool:
    """Warn UP FRONT when SkyPatcher can't deliver the armor we're about to build.

    SkyPatcher is a HARD dependency with no ESP fallback (`ube_patcher.
    _full_skypatcher_enabled` is unconditionally True): if the DLL is absent, or
    iEnableArmorPatching=0, then EVERY converted piece is invisible in-game and
    the only symptom is "the converter did nothing". The GUI surfaces this via
    preflight on launch, but the CLI/one-click paths never ran any check, so a
    headless run could spend an hour producing output that cannot load.

    Warn rather than abort: the meshes and plugins we emit are still correct, and
    the user can install SkyPatcher afterwards without reconverting. Returns True
    when delivery looks viable. Never raises -- a probe failure must not take
    down a conversion.
    """
    try:
        from . import preflight as _pf
        lay = paths.discover_layout()
        mr = getattr(lay, "mods_root", None)
        dd = getattr(lay, "game_data_dirs", []) or []
        enabled = paths.enabled_mods(lay)
        rel_dll, rel_ini = "SKSE/Plugins/SkyPatcher.dll", "SKSE/Plugins/SkyPatcher.ini"
        found = bool(_pf._locate_in_mods_or_data(mr, enabled, dd, rel_dll))
        armor_on = _pf._skypatcher_armor_patching(
            _pf._locate_in_mods_or_data(mr, enabled, dd, rel_ini))
        if found and armor_on is not False:
            return True
        why = ("SkyPatcher.dll not found in any enabled mod or the game Data"
               if not found else
               "SkyPatcher found but iEnableArmorPatching=0 in SkyPatcher.ini")
        fix = ("Install SkyPatcher and enable it."
               if not found else
               "Set iEnableArmorPatching=1 in SKSE/Plugins/SkyPatcher.ini.")
        print("  " + "!" * 70)
        warn(f"SkyPatcher: {why}",
             consequence="SkyPatcher delivers ALL converted armor -- there is no ESP "
                         "fallback. Without it every converted piece is INVISIBLE "
                         "in-game.",
             fix=f"{fix} (Converting anyway: the output stays valid, no reconvert "
                 "needed once SkyPatcher is in place.)")
        print("  " + "!" * 70)
        _record_failure("SkyPatcher not ready", "SkyPatcher",
                        "every converted piece", f"{why}. {fix}",
                        severity="warning")
        return False
    except Exception as e:
        print(f"  (SkyPatcher preflight skipped: {plain_error(e)})")
        return True


def _preflight_vanilla_sweep(data_dir: Path) -> "tuple[bool, str]":
    """Cheap viability check for the vanilla sweep, run BEFORE the batch.

    A sweep that would die mid-run must instead be disabled UP FRONT with one
    clear message: it is source #last, so a late crash costs the user hours of
    conversion before they learn anything (and on unknown layouts — different
    mod managers, stock-game variants — the Data dir is the least predictable
    input we touch). Returns (ok, reason); reason is printable on failure.
    All work here is reused by the real run via the ESP parse cache.
    """
    try:
        esps = _vanilla_sweep_esps(data_dir)
        if not esps:
            return False, f"no Skyrim.esm at {data_dir}"
        from . import esp as _esp
        e = _esp.ESP.load_cached(esps[0])
        if e.group(b"ARMA") is None:
            return False, f"{esps[0].name} parses but has no ARMA group"
        bases = _player_armor_mesh_bases(data_dir,
                                         include_candidate_slots=True)
        if not bases:
            return False, ("no DefaultRace armour ARMAs resolved from the "
                           "vanilla masters")
        return True, f"{len(esps)} master(s), {len(bases)} armour mesh base(s)"
    except Exception as e:
        return False, f"preflight error: {e!r}"


# --- per-source patch names -------------------------------------------------------
# #source-patch-rename (2026-09-25): our per-source patch was '<stem> UBE patch.esp',
# the name hand-made UBE patches use too. Measured on a live modlist, 22 of our
# un-loaded copies shared a name with another mod's active plugin, and a plugin
# index that walked subfolders read ours in place of theirs. '<stem> (CBBEtoUBE
# src).esp' collides with none of 3292 root plugins. The coverage pieces keep
# 'UBE_Mod*Coverage* UBE patch.esp': they are ours and never collided.
_SRC_PATCH_SUFFIX = " (CBBEtoUBE src).esp"
_LEGACY_SRC_PATCH_SUFFIX = " UBE patch.esp"
# Everything written beside a per-source patch ESP. None of them embeds the
# patch's file name, so the set renames without loss.
_SRC_PATCH_SIDECARS = (".skypatcher.json", ".espgen.json", ".male_fallbacks.json")


def _source_patch_rename_on() -> bool:
    """#source-patch-rename: name our per-source patches '<stem> (CBBEtoUBE
    src).esp' and migrate the old names? CBBE2UBE_NO_SOURCE_PATCH_RENAME=1 keeps
    '<stem> UBE patch.esp' and migrates nothing."""
    return not _flag("CBBE2UBE_NO_SOURCE_PATCH_RENAME", False)


def _source_patch_name(stem: str) -> str:
    """The file name of the per-source patch made from the plugin `stem`."""
    if _source_patch_rename_on():
        return f"{stem}{_SRC_PATCH_SUFFIX}"
    return f"{stem}{_LEGACY_SRC_PATCH_SUFFIX}"


def _legacy_source_patch_stem(name: str) -> "str | None":
    """The source stem of an OLD-named per-source patch ('<stem> UBE patch.esp'),
    or None for anything else -- a coverage piece ('UBE_Mod*') included."""
    if name.lower().startswith("ube_mod"):
        return None
    if not name.lower().endswith(_LEGACY_SRC_PATCH_SUFFIX.lower()):
        return None
    return name[:-len(_LEGACY_SRC_PATCH_SUFFIX)] or None


def _new_source_patch_stem(name: str) -> "str | None":
    """The source stem of a NEW-named per-source patch, or None."""
    if not name.lower().endswith(_SRC_PATCH_SUFFIX.lower()):
        return None
    return name[:-len(_SRC_PATCH_SUFFIX)] or None


def _patches_dir_of(output, unmerged_patch_subdir) -> Path:
    """Where the per-source patches live: the subfolder, or the output root in
    the legacy root-write mode ('' or '.')."""
    if unmerged_patch_subdir and unmerged_patch_subdir not in (".", "/"):
        return Path(output) / unmerged_patch_subdir
    return Path(output)


def _per_source_patch_paths(patches_dir: Path) -> "list[Path]":
    """The per-source patches the FALLBACK merge takes (coverage failed or
    empty). Never an old-named and a new-named file for the same source: a
    leftover '<stem> UBE patch.esp' (a rename that failed) is taken only when no
    '<stem> (CBBEtoUBE src).esp' exists, or that source's armatures would be
    merged twice. Kept in the order the old names sorted in, so the merge
    numbers the records as it did before the rename."""
    legacy = [q for q in sorted(patches_dir.glob("*UBE patch.esp"))
              if not q.name.startswith("UBE_Mod")]
    if not _source_patch_rename_on():
        return legacy
    new = sorted(patches_dir.glob("*" + _SRC_PATCH_SUFFIX))
    have = {(_new_source_patch_stem(q.name) or "").lower() for q in new}
    keep = [q for q in legacy
            if (_legacy_source_patch_stem(q.name) or q.name).lower() not in have]

    def _as_legacy(q: Path) -> Path:
        stem = _new_source_patch_stem(q.name)
        return q if stem is None else q.with_name(f"{stem}{_LEGACY_SRC_PATCH_SUFFIX}")
    return sorted(new + keep, key=_as_legacy)


def _merge_gate_patch_paths(patches_dir: Path) -> "list[Path]":
    """What the post-conversion block (female-model restore, coverage, merge)
    needs on disk to run at all. It used to be '*UBE patch.esp', which every
    per-source patch matched; the renamed ones do not, so on a fresh output that
    glob found nothing and the whole block -- coverage and the Combined -- was
    skipped. A per-source patch of either name, or a coverage piece, counts."""
    old = sorted(patches_dir.glob("*UBE patch.esp"))
    if not _source_patch_rename_on():
        return old
    return sorted(set(old) | set(patches_dir.glob("*" + _SRC_PATCH_SUFFIX)))


def _migrate_source_patch_names(patches_dir: Path, *,
                                require_sidecar: bool = False) -> dict:
    """Rename every old-named per-source patch ('<stem> UBE patch.esp', not a
    'UBE_Mod*' coverage piece) and its sidecars to '<stem> (CBBEtoUBE src).esp'.
    When the new name already exists the old set is stale and is deleted (the
    output folder is ours). Idempotent: a second call finds nothing. A file that
    cannot be moved or deleted is returned in `failed` as (name, error); the
    ESP moves LAST and a sidecar that fails puts back the ones already moved, so
    a source's set is never split across two names.

    `require_sidecar` (the root-write mode, where the folder can hold other
    plugins): only an ESP with our '.espgen.json' snapshot beside it is ours to
    rename or delete; any other is left under its own name and listed in `left`.
    #rename-guards

    Returns {'renamed': n, 'removed': n, 'failed': [(name, error), ...],
    'left': [name, ...]}."""
    out = {"renamed": 0, "removed": 0, "failed": [], "left": []}
    if not _source_patch_rename_on():
        return out
    try:
        if not Path(patches_dir).is_dir():
            return out
        olds = sorted(Path(patches_dir).glob("*UBE patch.esp"))
    except OSError as e:
        out["failed"].append((str(patches_dir), plain_error(e)))
        return out
    for esp in olds:
        stem = _legacy_source_patch_stem(esp.name)
        if stem is None or not esp.is_file():
            continue
        if require_sidecar and not Path(str(esp) + ".espgen.json").is_file():
            out["left"].append(esp.name)
            continue
        new = esp.with_name(f"{stem}{_SRC_PATCH_SUFFIX}")
        if new.is_file():
            try:
                for f in [esp] + [Path(str(esp) + s) for s in _SRC_PATCH_SIDECARS]:
                    if f.is_file():
                        f.unlink()
                out["removed"] += 1
            except OSError as e:
                out["failed"].append((esp.name, plain_error(e)))
            continue
        moved: "list[tuple[Path, Path]]" = []
        try:
            for s in _SRC_PATCH_SIDECARS:
                src = Path(str(esp) + s)
                if src.is_file():
                    dst = Path(str(new) + s)
                    os.replace(src, dst)
                    moved.append((src, dst))
            os.replace(esp, new)
            out["renamed"] += 1
        except OSError as e:
            for src, dst in reversed(moved):
                try:
                    os.replace(dst, src)
                except OSError:
                    pass
            out["failed"].append((esp.name, plain_error(e)))
    return out


def _migrate_source_patch_names_at_start(output, unmerged_patch_subdir) -> int:
    """Run the per-source patch migration at the start of a run, print what it
    did, and record each patch it could not move as a warning. Returns the
    number of warnings. #source-patch-rename

    In the root-write mode (--unmerged-patch-subdir '' or '.') the patches sit
    beside whatever else the output folder holds, and a '<x> UBE patch.esp'
    there may be another mod's plugin: renaming it would drop it out of MO2's
    plugin list. So the root is migrated only when the folder is this tool's
    output (`_is_our_own_output`), and even then only a patch with our
    '.espgen.json' snapshot beside it; anything else is left alone with a NOTE.
    #rename-guards"""
    if not _source_patch_rename_on():
        return 0
    pdir = _patches_dir_of(output, unmerged_patch_subdir)
    root_write = pdir == Path(output)
    if root_write and not _is_our_own_output(output):
        try:
            olds = [q.name for q in sorted(pdir.glob("*UBE patch.esp"))
                    if _legacy_source_patch_stem(q.name) is not None]
        except OSError:
            olds = []
        if olds:
            print(f"  [migrate] NOTE: {pdir} holds no conversion report of this "
                  f"tool, so its {len(olds)} '<plugin>{_LEGACY_SRC_PATCH_SUFFIX}' "
                  "file(s) are left under their own names")
        return 0
    res = _migrate_source_patch_names(pdir, require_sidecar=root_write)
    if res["left"]:
        shown = ", ".join(res["left"][:5]) + (", ..." if len(res["left"]) > 5 else "")
        print(f"  [migrate] NOTE: left {len(res['left'])} "
              f"'<plugin>{_LEGACY_SRC_PATCH_SUFFIX}' file(s) at the mod root "
              "under their own names: no .espgen.json beside them, so this tool "
              f"did not write them ({shown}). If an older version of this tool "
              "did, delete them by hand.")
    if res["renamed"] or res["removed"]:
        print(f"  [migrate] renamed {res['renamed']} per-source patch(es) to "
              f"'<plugin>{_SRC_PATCH_SUFFIX}'"
              + (f"; removed {res['removed']} old copy(ies) already renamed"
                 if res["removed"] else ""))
        if pdir == Path(output):
            # Root-write mode: these files ARE plugins MO2 loads, so the old
            # names drop out of its plugin list.
            print("  [migrate] the per-source patches sit at the mod root "
                  "(--unmerged-patch-subdir '.'): enable the renamed plugins "
                  "in MO2; the old names are gone")
    for name, err in res["failed"]:
        warn(f"could not rename the old-named per-source patch {name}: {err}",
             where=f"in {pdir}",
             consequence="it keeps its old name; the merge uses it only while "
                         "no renamed copy of it exists",
             fix="close any program holding the file and run again")
        _record_failure("rename failed", "per-source patch", name, err,
                        severity="warning")
    return len(res["failed"])


def refresh_mod_esp(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    output_esp_name: "str | None" = None,
    unmerged_patch_subdir: str = "_unmerged_patches",
    master_data_dirs: "list[Path] | None" = None,
) -> "AutoConvertResult":
    """ESP-only refresh (`--plugins-only`): regenerate this mod's patch ESP(s)
    from the `.espgen.json` snapshots the last full run wrote, skipping ALL
    mesh work. Mirrors auto_convert_mod's ESP-gen tail (kept in sync). Safe
    under FULL SKYPATCHER: patch content depends only on the source ARMAs +
    the converted-mesh set, both captured in the snapshot; the runtime INI
    applies to whatever record wins the load order. Mods without a snapshot
    (never fully converted) are skipped with a note."""
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    result = AutoConvertResult(source_dir=source_dir, output_dir=output_dir)
    if master_data_dirs is None:
        master_data_dirs = _discover_master_data_dirs(source_dir)
    bsa_mesh_rel_paths = None
    if _BATCH_BSA_INDEX is not None:
        try:
            if _BATCH_BSA_INDEX._index is None:
                _BATCH_BSA_INDEX._scan()
            bsa_mesh_rel_paths = _BATCH_BSA_INDEX._index
        except Exception:
            bsa_mesh_rel_paths = None
    src_esps = _vanilla_sweep_esps(source_dir) or _find_source_esps(source_dir)
    if not src_esps:
        result.notes.append("no source ESP found — skipping ESP generation")
        return result
    result.source_esps = src_esps
    result.source_esp = src_esps[0]
    esp_out_dir = (output_dir / unmerged_patch_subdir
                   if unmerged_patch_subdir not in (".", "/")
                   else output_dir)
    esp_out_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    for src_esp in src_esps:
        cur_out_name = (output_esp_name
                        if output_esp_name is not None and len(src_esps) == 1
                        else _source_patch_name(src_esp.stem))
        out_esp = esp_out_dir / cur_out_name
        snap_p = Path(str(out_esp) + ".espgen.json")
        if (not snap_p.is_file() and _source_patch_rename_on()
                and (output_esp_name is None or len(src_esps) != 1)):
            # #source-patch-rename: a snapshot still under the old name (its
            # migration failed) is replayed in place, so the patch stays beside
            # its own sidecars.
            _legacy = esp_out_dir / f"{src_esp.stem}{_LEGACY_SRC_PATCH_SUFFIX}"
            if Path(str(_legacy) + ".espgen.json").is_file():
                out_esp = _legacy
                snap_p = Path(str(out_esp) + ".espgen.json")
        if not snap_p.is_file():
            result.notes.append(
                f"plugins-only: no espgen snapshot for {src_esp.name} -> "
                "skipped (run a full convert first)")
            continue
        try:
            snap = _json.loads(snap_p.read_text(encoding="utf-8"))
        except Exception as e:
            result.notes.append(f"plugins-only: bad snapshot for "
                                f"{src_esp.name}: {e!r} -> skipped")
            continue
        try:
            from . import esp as _esp
            if _esp.ESP.load_cached(src_esp).group(b"ARMA") is None:
                result.esp_skipped_no_armor += 1
                continue
        except Exception:
            pass
        try:
            stats = ube_patcher.generate_ube_patch(
                src_esp, out_esp,
                master_data_dirs=master_data_dirs,
                body_mesh_rel_paths=set(snap.get("body_mesh_rel_paths") or []) or None,
                bsa_mesh_rel_paths=bsa_mesh_rel_paths,
                converted_rel_paths=set(snap.get("converted_rel_paths") or []),
            )
            out_path = Path(stats.get("output", out_esp))
            result.output_esps.append(out_path)
            result.esp_stats_list.append(stats)
            if result.output_esp is None:
                result.output_esp = out_path
                result.esp_stats = stats
        except Exception as e:
            result.esp_gen_failures.append((src_esp.name, plain_error(e)))
    return result


def auto_convert_mod(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    output_esp_name: str | None = None,
    ube_path_prefix: str = "!UBE",
    copy_textures: bool = False,
    ube_body_ref_path: str | Path | None = None,
    master_data_dirs: list[Path] | None = None,
    nif_workers: int | None = None,
    # Cross-mod output-path tracking for collision protection. When a
    # batch processes multiple source mods (e.g. a vanilla-replacer mod +
    # HDT-SMP Vanilla Armors), they often ship NIFs at the SAME vanilla
    # path (`meshes/armor/iron/f/cuirasslight_1.nif`). Without this
    # tracking, the later mod's NIF silently overwrites the earlier
    # mod's output and the user sees the wrong appearance in-game.
    # First-writer-wins: source mods earlier in the batch command line
    # claim their output paths; later mods skip colliding paths with a
    # warning. Pass a SHARED set from `_cmd_convert` so claims persist
    # across mods. None = no protection (legacy single-source behavior).
    claimed_dst_paths: "set[Path] | None" = None,
    # An externally-managed ProcessPoolExecutor to reuse across multiple
    # `auto_convert_mod` calls. Pass one from `_cmd_convert` so workers
    # stay warm across mods — the pynifly DLL, UBE body ref NIF, body
    # OSD, and CBBE->UBE delta are all per-process caches that get
    # destroyed when a pool tears down. Sharing the pool keeps those
    # caches hot, cutting ~1-2s of init cost per worker per mod.
    # If None, a fresh pool is created and torn down within this call.
    nif_pool: "_NifPool | None" = None,
    # Where to write the per-source UBE patch ESP. Relative to
    # output_dir. Default `_unmerged_patches/` keeps individual
    # patches off MO2's plugin-scanner radar (MO2 only loads .esp
    # files from the mod root, not subfolders). The user runs the
    # `merge` command afterward to produce the merged ESP at the
    # mod root, which is the actual plugin they enable.
    # Pass "" or "." to write at root (legacy behavior).
    unmerged_patch_subdir: str = "_unmerged_patches",
    # Full-VFS mesh index {meshes-relative path (lower, /) -> winning abs file}
    # across ALL enabled mods, built once by the caller (discovery.build_mesh_
    # index). Lets the converter find an armour's meshes even when they live in
    # a DIFFERENT mod than its ESP (BodySlide output / replacer / patch) — the
    # coverage fix so we stop missing armour that the source folder lacks.
    # None => fall back to the source mod's own meshes only (legacy behavior).
    mesh_vfs_index: "dict[str, Path] | None" = None,
    # Incremental re-conversion: when set (a unix mtime), a destination NIF is
    # REUSED (conversion skipped) if it already exists and is newer than BOTH
    # its source NIF and this floor. The floor = max(converter-code mtime, UBE
    # body-ref mtime) so any code or body change invalidates every cached
    # output. None => always convert (default, safest). Opt-in via --incremental.
    incremental_floor: "float | None" = None,
    # Armors a THIRD-PARTY mod has already UBE-patched, as
    # {(defining plugin lowercase, formid low24)} from
    # `_third_party_ube_covered_armos`. Built ONCE by the caller (the scan is
    # cached but reads every enabled plugin). Pieces owned entirely by such an
    # armor are skipped before conversion instead of being converted and then
    # suppressed at the coverage stage. None => convert everything. #skip-already-ube
    ube_covered_armos: "set[tuple[str, int]] | None" = None,
    # Forms a female NPC of a UBE-capable race wears or carries, as
    # {(defining plugin lowercase, formid low24)} from `_npc_worn_armos`. Built
    # ONCE by the caller (it reads every active plugin). A non-playable armour in
    # it is converted like playable armour. None => non-playable armour is never
    # converted (the old rule). #npc-worn-nonplayable
    npc_worn_armos: "frozenset[tuple[str, int]] | None" = None,
    # Is an armour's WINNING record non-playable (or deleted)? Same identity,
    # from `_batch_armo_winner_nonplayable`, built ONCE by the caller. Replaces
    # each scanned record's own playable flag. None => the record's own flag
    # (the old rule). #selection-winner-playable
    armo_winner_nonplayable: "dict[tuple[str, int], bool] | None" = None,
    # Which THIRD-PARTY mod ships a built UBE mesh at `meshes\!UBE\<path>`
    # (`_third_party_ube_twin_lookup`, built once by the caller). A mesh another
    # mod already built for UBE is left to it, and an earlier run's copy is moved
    # out of meshes\. None => convert it anyway (the old rule). #skip-built-ube-path
    built_ube_twin: "callable[[str], str | None] | None" = None,
) -> AutoConvertResult:
    """Run the full M2 + M3 phase 1 pipeline on a single CBBE armor mod.

    Args:
      source_dir: a CBBE armor mod folder (the kind MO2 would install)
      output_dir: where to write the UBE conversion mod folder
      output_esp_name: filename for the patch ESP (default:
        `<source_esp_stem> (CBBEtoUBE src).esp`; `<stem> UBE patch.esp`
        with CBBE2UBE_NO_SOURCE_PATCH_RENAME=1)
      ube_path_prefix: top-level folder under meshes/ for the converted NIFs
        (the UBE convention is `!UBE`; flagged as a config in case it changes)
      copy_textures: copy the source mod's textures/ tree verbatim into the
        output (default False). Normally OFF: the converted NIFs keep the
        original Data-relative texture paths, so the engine resolves them from
        the source mods via the MO2 VFS -- the same mechanism BSA-archived
        textures already rely on. Copying duplicates gigabytes and, landing at
        the output mod's high priority, overrides standalone retexture mods.

    Returns an AutoConvertResult with stats + nif-level details.
    """
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"source mod folder does not exist or is not a directory: "
            f"{source_dir}  (note: pass a native Windows path like "
            f"'<drive>:\\\\...\\\\mods\\\\<ModName>' — gitbash-style "
            f"'/c/...' paths are not converted automatically on Windows)"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    if ube_body_ref_path is None:
        ube_body_ref_path = _find_ube_body_ref()
    elif ube_body_ref_path is not None:
        ube_body_ref_path = Path(ube_body_ref_path)
        if not ube_body_ref_path.is_file():
            raise FileNotFoundError(f"UBE body ref not found: {ube_body_ref_path}")

    result = AutoConvertResult(source_dir=source_dir, output_dir=output_dir)
    if ube_body_ref_path is None:
        result.notes.append(
            "No UBE body ref found via auto-discovery. NIFs with inline body "
            "shapes will be skipped (phase 2 disabled). Pass --ube-body-ref "
            "to enable."
        )
    else:
        result.notes.append(f"UBE body ref: {ube_body_ref_path}")

    # --- ESPs ---
    if master_data_dirs is None:
        master_data_dirs = _discover_master_data_dirs(source_dir)
        if master_data_dirs:
            result.notes.append(
                f"master ESM scan dirs: {[str(d) for d in master_data_dirs]}")
    # Vanilla sweep source: the game Data dir with the vanilla/DLC masters as
    # its plugins. All _find_source_esps call sites below must use this list
    # (that helper deliberately skips vanilla masters for normal mod folders).
    _sweep_esps = _vanilla_sweep_esps(source_dir)
    if _sweep_esps:
        result.notes.append(
            "vanilla sweep source: game Data dir, plugins = "
            + ", ".join(p.name for p in _sweep_esps))
    # Resolve the planned NIF set (armour pieces only). Computed once; both the
    # ESP patcher (to gate ARMA redirects on existing converted paths) and the
    # NIF conversion step consume it. VFS-resolved so it's valid even for mods
    # whose meshes live in a different mod than their ESP.
    # Sweep: NEVER scan Data\meshes as "source-local". Launched from MO2 the
    # exe runs INSIDE the usvfs VFS, so Data\meshes is the merged view of
    # every enabled mod — enormous, not what the base game ships, and ghost
    # entries abort the walk with FileNotFoundError (2026-07-04 in-game
    # round: the Data source died exactly here). Winner meshes come from the
    # VFS index; vanilla meshes from the BSA fallback.
    if _sweep_esps:
        meshes_root = None
    else:
        meshes_root = _find_meshes_root(source_dir)
    all_nif_paths = (sorted(meshes_root.rglob("*.nif"))
                     if meshes_root is not None else [])
    # Source-local mesh keys (lowercase meshes-rel) for the female-resolves check.
    _local_keys = set()
    if meshes_root is not None:
        for _p in all_nif_paths:
            _local_keys.add(_p.relative_to(meshes_root).as_posix().lower())

    def _female_mesh_resolves(base: str) -> bool:
        # True if any weight variant of `base` exists to convert (full-VFS winner,
        # source-local, or BSA-packed). Mirrors _resolve_armor_meshes' lookup so the
        # female-only selection agrees with what actually converts: that resolver
        # extracts from BSAs too, so a BSA-packed female mesh must count here or a
        # perfectly convertible piece drags its male mesh into the plan (every
        # vanilla-sweep mesh is BSA-packed).
        for suf in ("_1", "_0", ""):
            key = f"{base}{suf}.nif"
            if mesh_vfs_index is not None and key in mesh_vfs_index:
                return True
            if key in _local_keys:
                return True
            if _BATCH_BSA_INDEX is not None and _BATCH_BSA_INDEX.contains(key):
                return True
        return False

    # include_candidate_slots: also admit lower-body cloth on ambiguous modder
    # slots (44/47/...). The crash guard below drops any non-body-skinned ones.
    # mesh_resolves enables the female-only policy (skip the male mesh when a female
    # mesh exists; keep male for male-only or dead-female-path pieces).
    _worn_admitted: "set[tuple[str, int]]" = set()
    armor_bases = _player_armor_mesh_bases(
        source_dir, include_candidate_slots=True,
        mesh_resolves=_female_mesh_resolves,
        ube_covered_armos=ube_covered_armos,
        armo_winner_nonplayable=armo_winner_nonplayable,
        npc_worn_armos=npc_worn_armos, worn_admitted=_worn_admitted)
    if _worn_admitted:
        # Say so: these pieces used to be skipped in silence. #npc-worn-nonplayable
        _worn_msg = (f"{len(_worn_admitted)} non-playable armature(s) converted "
                     "because a female NPC wears or carries their armour")
        print(f"  {_worn_msg}")
        result.notes.append(_worn_msg)
    # Resolve through the full MO2 VFS so meshes in BodySlide-output / replacer /
    # patch mods are found. Falls back to source-local when no VFS index is given.
    #
    # THE FALLBACK IS FOR PLUGIN-LESS MODS ONLY. `_resolve_armor_meshes` treats an
    # empty `armor_bases` as "this source has no ESP to classify by -> convert every
    # NIF it ships". That is right for a loose-mesh replacer, and WRONG for any mod
    # whose plugin we could read, because empty then means "the ESP was read and
    # selected nothing" -- and everything `_player_armor_mesh_bases` filters out
    # lives in that difference: the DefaultRace/RNAM gate, the body-slot allowlist,
    # the nude-body-skin and child-content filters, the female-only policy, and
    # (since #skip-already-ube) armors another mod has already UBE-patched.
    #
    # Keying the guard on "no armour bases" alone therefore inverted the coexistence
    # gate: a mod whose armors were ALL third-party-covered came back with an empty
    # set and converted its ENTIRE meshes tree instead of nothing -- more orphan
    # output than before the gate existed, with the female-only policy bypassed.
    # So gate on whether a plugin was actually READ. #esp-less-fallback-only
    _src_esps = _sweep_esps or _find_source_esps(source_dir)
    if _skip_esp_less_fallback(armor_bases, _src_esps):
        resolved_pairs = []
        result.notes.append(
            "vanilla sweep: no DefaultRace armour ARMAs resolved — nothing planned"
            if _sweep_esps else
            "plugin read but no convertible armour selected — nothing planned "
            "(already-UBE-patched, non-playable, not DefaultRace, wrong slot, "
            "child/body-skin, or already UBE-shaped)")
    else:
        resolved_pairs = _resolve_armor_meshes(
            armor_bases, mesh_vfs_index, meshes_root, all_nif_paths)
    # Single weight-agnostic slot resolver, shared by the crash guard below and
    # the work-item builder later. Built once from the source ESPs; a `_0` file
    # the ARMA never named inherits its `_1` partner's slots. #slot0-weight-partner
    try:
        _raw_slot_map = ube_patcher.build_nif_slot_map(_src_esps)
    except Exception:
        _raw_slot_map = {}
    slot_bits_for = _make_slot_resolver(_raw_slot_map)
    # Crash guard for ambiguous modder slots (44/47/...): keep only standard-slot
    # meshes OR those whose NIF is skinned to body-fit bones. Unskinned accessories
    # on these slots given a UBE body race CTD at actor setup. Must run before
    # converted_rel_paths so the patcher never UBE-tags a dropped mesh.
    if resolved_pairs:
        _kept_pairs = []
        _guard_dropped = 0
        _nonstd_kept: list[str] = []   # cape/cloak on a non-standard slot
        _guard_names: list[str] = []   # WHICH meshes the guard dropped
        for _gsrc, _grel in resolved_pairs:
            _gslot = slot_bits_for(_grel)
            if (_gslot & _BODY_SLOT_BITS) != 0 or _nif_has_bodyfit_skin(_gsrc):
                _kept_pairs.append((_gsrc, _grel))
                # Surface draping meshes kept on non-standard slots (cape/cloak
                # on hair slots admitted by the cloak rule) for visibility.
                if not (_gslot & (_BODY_SLOT_BITS | _BODY_CANDIDATE_SLOT_BITS)):
                    _nonstd_kept.append(_weight_base_key(_grel))
            else:
                _guard_dropped += 1
                _guard_names.append(_weight_base_key(_grel))
        if _guard_dropped:
            # NAME them. A bare count invites the wrong inference: a dropped
            # accessory still SHIPS -- race coverage points a UBE-race ARMA at
            # its ORIGINAL mesh, which keeps its own HDT physics reference --
            # so "dropped" costs a UBE-shaped refit, NOT visibility and NOT
            # physics. Reading a count alone, that is easy to get backwards.
            _gu = sorted(set(_guard_names))
            result.notes.append(
                f"crash guard: dropped {_guard_dropped} non-body accessory "
                "mesh(es) on ambiguous modder slots (not body-skinned; they "
                "still ship via race coverage on their original mesh): "
                + ", ".join(_gu[:10]) + (" ..." if len(_gu) > 10 else ""))
        if _nonstd_kept:
            _u = sorted(set(_nonstd_kept))
            result.notes.append(
                f"converted {len(_u)} draping mesh(es) on non-standard slots "
                f"(cape/cloak on hair/back, kept by body-fit skin): "
                + ", ".join(_u[:8]) + (" ..." if len(_u) > 8 else ""))
        resolved_pairs = _kept_pairs
    # Count meshes resolved from a different mod (VFS broadening) for the coverage note.
    _other_mod = 0
    for _abs, _ in resolved_pairs:
        if meshes_root is None:
            _other_mod += 1
        else:
            try:
                _abs.relative_to(meshes_root)
            except ValueError:
                _other_mod += 1
    result.vfs_other_mod_count = _other_mod
    if _other_mod:
        result.notes.append(
            f"VFS resolve: {_other_mod}/{len(resolved_pairs)} armour mesh(es) "
            "found in OTHER mods (BodySlide output / replacer / patch)")
    # Meshes-relative paths (lowercase /) of NIFs that WILL exist at !UBE\.
    # The patcher only redirects an ARMA if its path is in this set.
    # Heeled boots: the heel (NiFloatExtraData "HH_OFFSET") is transplanted back
    # at the binary level after conversion, so heeled boots ARE included here.
    # Exception: heeled NIFs whose binary layout can't be round-tripped are
    # excluded (ESP-only original mesh) so the heel still works.
    from src import hh_offset
    converted_rel_paths = set()
    _heeled_esp_only = 0
    for _abs, _rel in resolved_pairs:
        _heeled = False
        try:
            with open(_abs, "rb") as _fh:
                _heeled = hh_offset.contains_hh_offset(_fh.read(262144))
        except OSError:
            pass
        if _heeled and hh_offset.read_hh_offset(_abs) is None:
            _heeled_esp_only += 1   # heeled but unparseable -> ESP-only
            continue
        converted_rel_paths.add(_rel.lower())
    if _heeled_esp_only:
        result.notes.append(
            f"{_heeled_esp_only} heeled mesh(es) parser-unsupported -> ESP-only "
            "(original mesh kept so the heel survives)")
    # Source-local mesh paths for the master-ESM body-coverage scan in the
    # patcher. Source-local on purpose: describes what THIS mod replaces, not
    # the whole VFS. Used for loose-mesh replacers whose ESP has no records for
    # the vanilla armors they replace.
    body_mesh_rel_paths: set[str] = set()
    if meshes_root is not None:
        for _nif in all_nif_paths:
            body_mesh_rel_paths.add(
                _nif.relative_to(meshes_root).as_posix().lower())

    # BSA mesh index: used only for the non-body accessory passthrough gate so
    # BSA-packed accessories are recognised as "shipped" and get UBE coverage.
    # NOT fed to the body-coverage scan (a raw BSA body mesh on a UBE torso
    # would be wrong-shaped). The passthrough keeps the original path -> crash-safe.
    bsa_mesh_rel_paths = None
    if _BATCH_BSA_INDEX is not None:
        try:
            if _BATCH_BSA_INDEX._index is None:
                _BATCH_BSA_INDEX._scan()
            bsa_mesh_rel_paths = _BATCH_BSA_INDEX._index
        except Exception:
            bsa_mesh_rel_paths = None

    src_esps = _sweep_esps or _find_source_esps(source_dir)
    if not src_esps:
        result.notes.append("no source ESP found — skipping ESP generation")
    else:
        result.source_esps = src_esps
        result.source_esp = src_esps[0]  # backward compat
        # Route unmerged patches into a subfolder so MO2's plugin scanner
        # ignores them; only the merged Combined ESP at the mod root is active.
        if unmerged_patch_subdir and unmerged_patch_subdir not in (".", "/"):
            esp_out_dir = output_dir / unmerged_patch_subdir
            esp_out_dir.mkdir(parents=True, exist_ok=True)
        else:
            esp_out_dir = output_dir
        for i, src_esp in enumerate(src_esps):
            # Honor `output_esp_name` only for single-ESP mods. With
            # multiple ESPs we use the auto-generated stem to keep
            # each output distinct.
            if output_esp_name is not None and len(src_esps) == 1:
                cur_out_name = output_esp_name
            else:
                # '<stem> (CBBEtoUBE src).esp'. #source-patch-rename
                cur_out_name = _source_patch_name(src_esp.stem)
            out_esp = esp_out_dir / cur_out_name
            # Skip ESPs with no armor addons (no ARMA group) entirely. Big bundle
            # mods (merged xEdit output, overhaul patch packs) carry many
            # landscape/navmesh/quest/patch ESPs with no armour -- attempting a
            # patch for them only raises "no ARMA group", which would be
            # miscounted as a failure claiming "armor absent / invisible" for
            # armour that never existed. A benign skip, not a failure.
            try:
                from . import esp as _esp
                if _esp.ESP.load_cached(src_esp).group(b"ARMA") is None:
                    result.esp_skipped_no_armor += 1
                    continue
            except Exception:
                pass  # unreadable -> let generate_ube_patch surface the real error
            try:
                stats = ube_patcher.generate_ube_patch(
                    src_esp, out_esp,
                    master_data_dirs=master_data_dirs,
                    body_mesh_rel_paths=body_mesh_rel_paths,
                    bsa_mesh_rel_paths=bsa_mesh_rel_paths,
                    converted_rel_paths=converted_rel_paths,
                )
                out_path = Path(stats.get("output", out_esp))
                result.output_esps.append(out_path)
                result.esp_stats_list.append(stats)
                # ESP-refresh snapshot: the per-mod inputs generate_ube_patch
                # needs besides live master dirs. `--plugins-only` replays the
                # ESP phase from these in minutes (no NIF work) -- safe under
                # FULL SKYPATCHER because patch content depends only on source
                # ARMAs + the converted-mesh set (see refresh_mod_esp).
                try:
                    import json as _json
                    from .atomic_io import atomic_write_bytes
                    # Atomic so a crash/kill mid-write can't leave a torn snapshot
                    # that a later --plugins-only refresh would silently skip
                    # (dropping that source's armor from the re-merge).
                    atomic_write_bytes(
                        Path(str(out_esp) + ".espgen.json"),
                        _json.dumps({
                            "source_esp": str(src_esp),
                            "converted_rel_paths": sorted(converted_rel_paths or []),
                            "body_mesh_rel_paths": sorted(body_mesh_rel_paths or []),
                        }).encode("utf-8"))
                except OSError:
                    pass
                # Backward compat: primary fields = first successful patch
                if result.output_esp is None:
                    result.output_esp = out_path
                    result.esp_stats = stats
                for w in stats.get("validation_warnings", []) or []:
                    result.notes.append(
                        f"!! patch validator ({src_esp.name}): {w}")
            except Exception as e:
                result.notes.append(
                    f"ESP generation failed for {src_esp.name}: {e}")
                result.esp_gen_failures.append(src_esp.name)

    # --- NIFs ---
    # Output paths planned for this call; scoped to THIS mod so the post-conversion
    # load check doesn't re-read every prior mod's outputs.
    planned_output_nifs: "set[Path]" = set()
    if not resolved_pairs:
        result.notes.append("no convertible armour meshes resolved")
    else:
        # Scan source ESPs' ARMA records for slot-49 meshes (skirts / hip cloth)
        # so the converter can bump inflation for them. Use source ESPs (pre-rewrite)
        # so paths line up with `rel` in the work items.
        # Slot bits come from the shared weight-agnostic `slot_bits_for` resolver
        # built once above (so `_0` and `_1` convert with identical slots).
        # #slot0-weight-partner

        if armor_bases:
            print(f"  armour filter: converting {len(resolved_pairs)} ARMA "
                  f"model NIF(s) ({_other_mod} resolved from other mods); "
                  f"source mod ships {len(all_nif_paths)} mesh(es) total")
        nif_dst_root = output_dir / "meshes" / ube_path_prefix

        # Shape names targeted by alt-texture sets (color variants). The NIF
        # converter protects these from the morph-cap merge so TXST by name lands.
        try:
            _alt_src_esps = _sweep_esps or _find_source_esps(source_dir)
            alt_tex_shape_names = ube_patcher.collect_alt_texture_shape_names(
                _alt_src_esps) if _alt_src_esps else set()
        except Exception:
            alt_tex_shape_names = set()
        if alt_tex_shape_names:
            print(f"  protecting {len(alt_tex_shape_names)} alt-texture-target "
                  f"shape(s) from merge (color variants)")

        # #tri-variant-collision -- WHO WRITES EACH `.tri`, resolved the way
        # SOURCES are resolved. `foo.nif`, `foo_0.nif` and `foo_1.nif` all
        # derive `foo.tri`, so exactly one of them may write it and the others
        # may only point at it when their vertex counts agree.
        #
        # The first fix for this asked the filesystem NEXT TO the source, and
        # was INERT on the real pack: the competing variant routinely lives in
        # a DIFFERENT MOD (a vanilla BSA ships the no-suffix model alone while a
        # body-replacer mod ships the `_0`/`_1` pair), and a BSA-resolved source
        # is staged ALONE, so the probe saw no sibling and let every variant
        # claim the TRI. `resolved_pairs` is the correct source of truth because
        # it came out of the SAME mod list / VFS / BSA chain that found the file
        # being converted, so it sees across mods by construction.
        #
        variant_sources_by_base = _variant_sources_by_base(resolved_pairs)
        # Same map, reduced to the SUFFIXES, for the batch-level partner fill.
        for _b, _vs in variant_sources_by_base.items():
            result.source_weight_variants.setdefault(_b, set()).update(_vs)

        work_items: list[tuple] = []
        skipped_collisions: list[tuple[Path, Path]] = []
        skipped_incremental = 0
        # #skip-built-ube-path: a mesh another mod already ships BUILT for UBE at
        # the very path we would write is left to that mod -- every weight
        # variant of it or none, since their `_1` beside our `_0` would be a
        # mismatched pair. {rel: the mod that ships it}.
        built_elsewhere = (_built_ube_twins(resolved_pairs, built_ube_twin)
                           if ube_path_prefix == "!UBE" else {})
        skipped_built: list[tuple[str, str]] = []
        for src, rel in resolved_pairs:
            slot_bits = slot_bits_for(rel)
            # Last line of defence against double-conversion. The real gate is in
            # _player_armor_mesh_bases, but a mesh can reach here by other routes
            # (BSA fallback, the convert-everything path when a mod exposes no
            # armour bases), and the failure is silent: converting an already-UBE
            # mesh writes it to `!UBE\!UBE\...` and refits a UBE mesh onto the UBE
            # body a second time. Cheap to assert, so assert it.
            if _is_already_ube_model(rel):
                continue
            if rel in built_elsewhere:
                skipped_built.append((rel, built_elsewhere[rel]))
                continue
            dst = nif_dst_root / Path(rel)
            # SECURITY: `rel` can derive from a mod-controlled ARMA model path /
            # BSA name; refuse `..`/absolute traversal outside the output meshes.
            if not paths.is_within_dir(nif_dst_root, dst):
                warn(f'refusing traversal output path for "{rel}"',
                     consequence="the source names a path outside the output mod; "
                                 "the file was skipped",
                     file=sys.stderr)
                continue
            # First-writer wins: skip paths already claimed by an earlier source mod.
            if claimed_dst_paths is not None:
                key = dst.resolve()
                if key in claimed_dst_paths:
                    skipped_collisions.append((src, dst))
                    continue
                claimed_dst_paths.add(key)
            # Incremental: reuse an up-to-date NIF. The floor includes converter-code
            # + body-ref mtime, so any logic/body change forces a full re-convert.
            if incremental_floor is not None:
                try:
                    if (dst.is_file()
                            and dst.stat().st_mtime > src.stat().st_mtime
                            and dst.stat().st_mtime > incremental_floor):
                        skipped_incremental += 1
                        continue
                except OSError:
                    pass  # fall through to convert on any stat failure
            work_items.append((
                src, dst,
                str(ube_body_ref_path) if ube_body_ref_path else None,
                int(slot_bits),
                alt_tex_shape_names,
                variant_sources_by_base.get(_weight_base_key(rel)),
            ))
        if skipped_incremental:
            print(f"  incremental: reusing {skipped_incremental} up-to-date "
                  "converted NIF(s) (unchanged source + converter)")
            result.notes.append(
                f"incremental reuse: {skipped_incremental} NIF(s) skipped")
        if skipped_collisions:
            print(f"  collision protection: skipping {len(skipped_collisions)} "
                  f"NIFs already claimed by an earlier source mod")
            for src, dst in skipped_collisions[:5]:
                rel_disp = dst.relative_to(output_dir).as_posix()
                print(f"    '{src.name}' -> '{rel_disp}'  (earlier mod wins)")
            if len(skipped_collisions) > 5:
                print(f"    ... and {len(skipped_collisions) - 5} more")
            result.notes.append(
                f"NIF collisions skipped: {len(skipped_collisions)} "
                "(earlier source mod won the output path)")
        if skipped_built:
            _stuck: list = []
            _moved = _supersede_built_ube_outputs(
                output_dir, nif_dst_root, [r for r, _m in skipped_built],
                failed=_stuck)
            if _supersede_whole_base():
                # The fill after the batch must not put our copy back at the
                # builder's path, moved or (a move failed) left whole.
                result.superseded_weight_bases.update(
                    _weight_base_key(r) for r, _m in skipped_built)
            from collections import Counter as _Counter
            _by_mod = _Counter(m for _r, m in skipped_built)
            print(f"  built UBE version elsewhere: {len(skipped_built)} mesh(es) "
                  "left to the mod that already ships them built for UBE"
                  + (f"; {_moved} copy(ies) from an earlier run moved out of meshes\\"
                     if _moved else ""))
            for _m, _k in _by_mod.most_common(5):
                print(f"    {_k:4d}  {_m}")
            result.notes.append(
                f"built UBE version elsewhere: {len(skipped_built)} NIF(s) not "
                f"converted, {_moved} earlier copy(ies) superseded")
            _left = [s for s in _stuck if not s[2]]
            _torn = [s for s in _stuck if s[2]]
            if _left:
                _names = (", ".join(f"{s[0]} ({s[1]})" for s in _left[:5])
                          + (f" and {len(_left) - 5} more" if len(_left) > 5 else ""))
                warn(f"{len(_left)} piece(s) from an earlier run could not be moved "
                     f"out of meshes\\ (a file is in use): {_names}",
                     consequence="each was left whole, with its .tri and physics, "
                                 "so our old conversion still replaces the hand-made "
                                 "UBE version in game",
                     fix="close the program holding the file (the game, NifSkope, "
                         "Outfit Studio) and run again")
            if _torn:
                _names = (", ".join(f"{s[0]} ({', '.join(s[2])})" for s in _torn[:5])
                          + (f" and {len(_torn) - 5} more" if len(_torn) > 5 else ""))
                warn(f"{len(_torn)} piece(s) from an earlier run were only partly "
                     f"moved out of meshes\\ and could not be put back: {_names}",
                     consequence="the named files are in _superseded\\ while the rest "
                                 "of the piece is still in meshes\\, so the piece can "
                                 "draw with the wrong morphs",
                     fix="close the program holding the files and run again, or "
                         "move the named files back from _superseded\\")
            for s in _stuck:
                result.notes.append(f"built UBE version elsewhere: {s[0]} not moved "
                                    f"out of meshes\\ ({s[1]})")

        planned_output_nifs = {it[1] for it in work_items}

        if nif_workers is None:
            nif_workers = default_worker_count()
        nif_workers = max(1, min(nif_workers, len(work_items)))

        t_start = time.perf_counter()
        # Serial ONLY when there's no shared pool to isolate crashes: a single-mesh
        # mod (or forced 1 worker) run in-process gives a native pynifly crash the
        # power to abort the WHOLE batch. When a warm shared `nif_pool` exists, route
        # even a single NIF through it so the pool's BrokenProcessPool self-heal
        # contains the crasher to one worker. #single-mesh-isolation
        if nif_pool is None and (nif_workers == 1 or len(work_items) <= 1):
            # No shared pool + tiny job -> serial in-process (avoids pool spin-up).
            for item in work_items:
                r = _nif_convert_worker(item)
                result.nif_results.append(r)
        else:
            if len(work_items) > 1:
                print(f"  NIF conversion: {len(work_items)} files across "
                      f"{nif_workers} workers...")
            done = 0
            last_print = t_start

            def _on_result(r):
                nonlocal done, last_print
                result.nif_results.append(r)
                done += 1
                now = time.perf_counter()
                # Progress noise only for real multi-file jobs (single-mesh mods
                # routed here for isolation stay quiet).
                if len(work_items) > 1 and (
                        now - last_print >= 5.0 or done == len(work_items)):
                    rate = done / max(now - t_start, 1e-9)
                    eta = (len(work_items) - done) / max(rate, 1e-9)
                    print(f"    [{done}/{len(work_items)}] "
                          f"{rate:.1f} NIF/s  ETA {eta:.0f}s")
                    # The same progress as a machine marker for the window's
                    # bar, at the same cadence; the window strips it from the
                    # visible log. Format: "[progress-nif] <done> <total>".
                    # #per-file-progress
                    print(f"[progress-nif] {done} {len(work_items)}", flush=True)
                    last_print = now

            # The pool self-heals: a worker PROCESS death (native pynifly crash ->
            # BrokenProcessPool) is recovered by rebuilding and re-running the
            # not-yet-done items in isolation, so only the true crasher is dropped
            # -- not the rest of this mod, and (because the shared _NifPool
            # persists) not every subsequent mod in the batch.
            if isinstance(nif_pool, _NifPool):
                nif_pool.run_batch(work_items, _on_result)
            else:
                _local_pool = _NifPool(nif_workers)
                try:
                    _local_pool.run_batch(work_items, _on_result)
                finally:
                    _local_pool.shutdown()
        elapsed = time.perf_counter() - t_start
        if len(work_items) > 0:
            rate = len(work_items) / max(elapsed, 1e-9)
            result.notes.append(
                f"NIF conversion: {len(work_items)} files in "
                f"{elapsed:.1f}s ({rate:.1f}/s) with {nif_workers} worker(s)")

    # --- textures ---
    # Sweep: never texture-copy from the Data dir (same usvfs merged-view /
    # ghost-entry hazard as the meshes scan; vanilla textures load from BSAs).
    if copy_textures and not _sweep_esps:
        tex_root = _find_textures_root(source_dir)
        if tex_root is not None:
            tex_dst = output_dir / "textures"
            count = 0
            skipped_current = 0
            for f in tex_root.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(tex_root)
                out = tex_dst / rel
                        # Skip if size and mtime match — any content change changes one or both.
                try:
                    if out.is_file():
                        src_stat = f.stat()
                        dst_stat = out.stat()
                        if (src_stat.st_size == dst_stat.st_size
                                and src_stat.st_mtime <= dst_stat.st_mtime):
                            skipped_current += 1
                            continue
                except OSError:
                    pass  # fall through to copy on any stat failure
                out.parent.mkdir(parents=True, exist_ok=True)
                # Atomic: a kill / ENOSPC / locked dst mid-copy must never leave a
                # truncated DDS (corrupt/garbage texture) in the deployed output.
                from .atomic_io import atomic_copy
                atomic_copy(f, out)
                count += 1
            result.textures_copied = count
            if skipped_current:
                result.notes.append(
                    f"textures: {count} copied, "
                    f"{skipped_current} skipped (already current)")

    # NOTE: slot-49 cloth morphs via `add_scale_bone_weights` in nif_convert.py
    # (3BA scale bones). Promoting slot-49 ARMAs to slot 32 was tried and broke.

    # --- post-conversion load check + VirtualBody hide ---
    # Re-load each output NIF to catch loader rejections, and apply the VirtualBody
    # Hidden flag. Scoped to THIS run's planned_output_nifs (not the whole output
    # tree) so it doesn't re-read prior mods' outputs in a batch.
    meshes_out = output_dir / "meshes"
    if meshes_out.is_dir() and planned_output_nifs:
        try:
            pn = str(Path(__file__).resolve().parent.parent / ".pynifly")
            if pn not in sys.path:
                sys.path.insert(0, pn)
            from pyn import pynifly  # type: ignore
            from . import nif_convert as _nc  # for _hide_virtual_body

            for dst in sorted(planned_output_nifs):  # sorted: #deterministic-set-iteration
                if not dst.is_file():
                    # Conversion produced nothing (already recorded); not a load failure.
                    continue
                try:
                    nf_check = pynifly.NifFile(filepath=str(dst))
                except Exception:
                    result.nif_load_failures.append(dst)
                    continue
                try:
                    if _nc._hide_virtual_body(nf_check):
                        from .atomic_io import atomic_nif_save
                        atomic_nif_save(nf_check, dst)
                except Exception as _vbe:
                    # A failed re-hide/save can leave a VISIBLE VirtualBody (the
                    # "blue body double"); count it as a warning, don't bury it.
                    result.virtualbody_rehide_failures.append(
                        f"{dst.name}: {_vbe!r} (risk of a visible body-double)")
                # Postflight per-NIF invariants on the FINAL reloaded bytes.
                try:
                    result.nif_invariant_warnings.extend(
                        _nif_invariant_issues(
                            dst.name, nf_check.shapes,
                            _nc.SKIN_PARTITION_BONE_CAP))
                except Exception as _ive:
                    # A postflight check that RAISES reports no warnings, which
                    # reads identically to a NIF that passed. Record the failure
                    # in the same list the warnings go to, so the report cannot
                    # imply this file was checked when it was not.
                    result.nif_invariant_warnings.append(
                        f"{dst.name}: postflight invariant check FAILED to run "
                        f"({_ive!r}) -- this file is UNCHECKED, not clean")
        except ImportError as _ie:
            # Without pynifly the ENTIRE post-conversion load check is skipped:
            # no load-rejection detection, no VirtualBody re-hide, no postflight.
            # Silence here makes an unverified run look like a verified one.
            import sys as _sys
            warn(f"post-conversion load check skipped -- pynifly unavailable "
                 f"({plain_error(_ie)})",
                 consequence="output was NOT re-loaded or verified",
                 file=_sys.stderr)

    # --- report ---
    report_name = f"conversion_report_{source_dir.name}.txt"
    # Sanitize to a valid Windows filename
    for bad in '<>:"/\\|?*':
        report_name = report_name.replace(bad, "_")
    result.write_report(output_dir / report_name)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _program_name() -> str:
    """What usage and error lines call this program: the exe's own file name when
    frozen, else CBBEtoUBE. It was hard-coded "auto_convert", a module name no
    user ever typed. #check-setup"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return "CBBEtoUBE"


def _build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog=_program_name(),
        description="CBBE-to-UBE auto-converter for Skyrim armor mods.",
    )
    sub = p.add_subparsers(dest="cmd")

    convert = sub.add_parser(
        "convert",
        help="convert one or more CBBE mod folders into a shared output mod",
        description="Convert one or more CBBE armor mod folders. With "
                    "multiple sources, all NIFs go through one process — "
                    "the UBE body OSD + body ref are loaded once and "
                    "cached across the batch, which is ~30s faster per "
                    "extra armor than running `convert` separately. "
                    "Use --output (or -o) to specify the shared output "
                    "mod folder; the legacy positional `source output` "
                    "form is still accepted when there's only one source.")
    convert.add_argument(
        "sources", type=Path, nargs="+",
        help="One or more CBBE armor mod folders. When more than one is "
             "given, the LAST positional is treated as the output dir "
             "UNLESS --output is provided.")
    convert.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output UBE conversion mod folder (required when more than "
             "one source is given; otherwise inferred from the last "
             "positional, mirroring the legacy `source output` form).")
    convert.add_argument("--esp-name", default=None,
                         help="filename for the patch ESP (default: '<stem> (CBBEtoUBE src).esp'). "
                              "Ignored when converting multiple sources — each gets its own ESP.")
    convert.add_argument("--no-textures", action="store_true",
                         help="(Default behavior now.) Don't copy source textures.")
    convert.add_argument("--copy-textures", action="store_true",
                         help="Copy source textures into the output (off by "
                              "default; textures resolve via the MO2 VFS).")
    convert.add_argument("--ube-body-ref", type=Path, default=None,
                         help="UBE body reference NIF (contains BaseShape + "
                              "VirtualBody). Auto-discovered from MO2 mods "
                              "folder if not provided.")
    convert.add_argument("--workers", type=int, default=None,
                         help="Number of parallel NIF-conversion worker "
                              "processes. Default: cpu_count() - 1. "
                              "Pass 1 to disable multiprocessing (serial).")
    convert.add_argument("--unmerged-patch-subdir",
                         default="_unmerged_patches",
                         help="Subfolder under --output where the per-source "
                              "UBE patch ESPs land. Defaults to "
                              "`_unmerged_patches/` so MO2 doesn't auto-load "
                              "them as plugins (only the root-level Combined "
                              "ESP should be the active plugin). Pass an "
                              "empty string or '.' to keep them at root "
                              "(legacy behavior).")
    # Auto-merge runs by default after the batch — most workflows
    # want a single Combined ESP at the mod root for MO2 to pick up.
    convert.add_argument("--no-auto-merge", dest="auto_merge",
                         action="store_false", default=True,
                         help="Skip the final merge step. Individual patches "
                              "stay in --unmerged-patch-subdir; you can run "
                              "`merge` manually later.")
    convert.add_argument("--merged-name", default="CBBE_to_UBE_Combined.esp",
                         help="Filename for the auto-merged Combined ESP at "
                              "the mod root (default: CBBE_to_UBE_Combined.esp).")
    # --no-winner-rebase was REMOVED. The winner rebase only ever adjusted ARMO
    # OVERRIDES; under SkyPatcher-only delivery the Combined emits no ARMO
    # records at all, so there is nothing to rebase. The flag had decayed into a
    # no-op that was never read, while still advertising behaviour the tool no
    # longer has.
    convert.add_argument("--exclude-mods", action="append", default=None,
                         metavar="NAME",
                         help="Mods whose armour the coverage step must leave "
                              "alone (repeat the flag or comma-separate). `auto` "
                              "passes its own --exclude-mods here; with "
                              "`convert` the sources are named, so this only "
                              "affects coverage.")
    convert.add_argument("--plugins-only", action="store_true",
                         dest="plugins_only",
                         help="ESP-only refresh: regenerate patch ESPs + merge "
                              "+ SkyPatcher INI + coverage from the last full "
                              "run's espgen snapshots. No mesh work (minutes, "
                              "not hours). Mods never fully converted are "
                              "skipped with a note.")
    convert.add_argument("--incremental", action="store_true",
                         help="Reuse already-converted NIFs that are newer than "
                              "their source AND the converter code/body ref "
                              "(skips the ~3s/NIF refit). Big speedup on "
                              "re-runs; a code or body change forces a full "
                              "re-convert automatically.")
    convert.add_argument("--render-previews", action="store_true",
                         help="After conversion, render a 3-view (front/side/back) "
                              "BMP per output NIF into "
                              "<output>/_previews/<rel>.bmp. Each vert is colored "
                              "by max morph-delta magnitude across all sliders, so "
                              "shapes that don't morph (no BODYTRI coverage) show "
                              "as gray and shapes with broken-correspondence "
                              "deltas show as bright red. Catches morph issues "
                              "before in-game test.")

    scan = sub.add_parser("scan", help="pre-flight: list candidate CBBE armor mods")
    scan.add_argument("mods_root", type=Path,
                      help="MO2 mods/ root (e.g. <modlist>\\mods)")
    scan.add_argument("--limit", type=int, default=50,
                      help="max number of candidates to print")

    sub.add_parser("discover-body-ref",
                   help="find a UBE NIF with BaseShape + VirtualBody")

    sub.add_parser(
        "check-setup",
        help="print the GUI's setup checks, one per line; exit 1 if any fails",
        description="Run the same checks as the GUI's Check setup and print one "
                    "line per check; exit code 1 if any check fails. The exe is a "
                    "window program, so redirect the output to read it: "
                    "CBBEtoUBE.exe check-setup > setup.txt")

    merge = sub.add_parser(
        "merge",
        help="combine multiple UBE patch ESPs into one ESL-flagged ESP",
        description="Merge two or more existing UBE patch ESPs into a "
                    "single ESL-flagged ESP. The combined ESP loads ALL "
                    "the per-mod ARMA/ARMO additions through one plugin "
                    "slot, freeing up load-order slots and avoiding ESM "
                    "ordering issues. Each input patch's source ESP "
                    "becomes a master of the combined patch.")
    merge.add_argument("patches", type=Path, nargs="+",
                       help="Two or more existing UBE patch ESPs to combine.")
    merge.add_argument("-o", "--output", type=Path, required=True,
                       help="Output path for the combined patch ESP.")
    merge.add_argument("--no-esl-flag", action="store_true",
                       help="Don't set the ESL flag (use if record count "
                            "exceeds 2048 or you want a regular ESP).")
    merge.add_argument("--author", default="cbbe-to-ube merger",
                       help="TES4.CNAM author string (default: 'cbbe-to-ube merger').")
    merge.add_argument("--description", default="Merged UBE compatibility patches",
                       help="TES4.SNAM description string.")

    val = sub.add_parser(
        "validate",
        help="run structural + NIF-existence checks on an output mod folder",
        description=(
            "Walk every .esp under <mod_dir> and run the patch validator on "
            "each, then report per-patch warnings and an overall pass/fail "
            "summary. Useful as a pre-flight check before launching the "
            "game. Warning categories (stable string prefixes, grep-able): "
            "modl-after-data (Skyrim ignores armatures after DATA - replacer "
            "armor renders empty); master-ordering (.esm after .esp in "
            "master list - ESL crash); next-object-id (header lies about "
            "max own FormID - dynamic-record collision); esl-overflow (ESL "
            "flag set but > 2048 own ARMAs); formid-zero (record uses 0 - "
            "reserved for player); formid-out-of-range (FormID master byte "
            ">= master list length - guaranteed crash on equip); missing-nif "
            "(ARMA MOD3/MOD5 path points to a !UBE\\ NIF that isn't on disk "
            "- the engine reads a freed/garbage model path; this is "
            "startup-CTD cause #1, not merely invisible armour); "
            "armo-missing-full (ARMO override has "
            "no FULL subrecord - inventory UI silently hides the item)."
        ))
    val.add_argument("mod_dir", type=Path,
                     help="An output mod folder containing one or more "
                          "UBE patch ESPs (and ideally meshes/!UBE/...).")
    val.add_argument("--meshes-root", type=Path, default=None,
                     help="Override the meshes/ directory used for NIF-"
                          "existence checks. Defaults to <mod_dir>/meshes.")
    val.add_argument("--no-nifs", action="store_true",
                     help="Skip the NIF-existence check (structural only).")

    # One-click full pipeline — the default when no subcommand is given (so
    # the standalone .exe / MO2 executable entry runs it with zero args).
    auto_p = sub.add_parser(
        "auto",
        help="one-click: auto-discover the modpack, convert ALL CBBE/3BA "
             "armor, merge, and emit vanilla race coverage (no args needed)",
        description="The standalone entry point. Discovers the MO2 mods root "
                    "+ game Data, scans every mod for CBBE/3BA armor, converts "
                    "them all into one output mod, merges into a single "
                    "ESL-flagged Combined ESP, and adds vanilla race coverage. "
                    "Run with no arguments for a full conversion.")
    auto_p.add_argument("-o", "--output", type=Path, default=None,
                        help="Output mod folder (default: "
                             "<mods>/CBBEtoUBE Auto).")
    auto_p.add_argument("--workers", type=int, default=None,
                        help="Parallel worker processes (default cpu-1).")
    auto_p.add_argument("--no-textures", action="store_true",
                        help="(Default behavior now.) Don't copy source textures "
                             "into the output; resolve them from source mods via "
                             "the MO2 VFS.")
    auto_p.add_argument("--copy-textures", action="store_true",
                        help="Copy the source mods' textures into the output mod "
                             "(self-contained output). Off by default -- textures "
                             "resolve from the source mods via the VFS, saving the "
                             "~17 GB duplicate and keeping retextures live.")
    auto_p.add_argument("--merged-name", default="CBBE_to_UBE_Combined.esp",
                        help="Filename of the merged Combined ESP.")
    auto_p.add_argument("--no-auto-merge", dest="auto_merge",
                        action="store_false", default=True,
                        help="Do NOT merge the per-source UBE patch ESPs into "
                             "one Combined ESP; leave them in _unmerged_patches/ "
                             "for you to merge/load yourself.")
    # --no-winner-rebase removed here too; see the note on the convert parser.
    auto_p.add_argument("--plugins-only", action="store_true",
                        dest="plugins_only",
                        help="ESP-only refresh from the last full run's espgen "
                             "snapshots (no mesh work).")
    auto_p.add_argument("--incremental", action="store_true",
                        help="Reuse up-to-date converted NIFs (skip the refit) "
                             "for a fast re-run; a code or body change forces a "
                             "full re-convert automatically.")
    # --no-modded-nonbody REMOVED with the standalone coverage plugins: it
    # gated the legacy ModBody/ModNonBody emission, which no longer exists.
    # Coverage is now always folded into the Combined family.
    auto_p.add_argument("--no-ube-native-scan", action="store_true",
                        dest="no_ube_native_scan",
                        help="Skip the geometry check that drops mods whose "
                             "armor already fits the UBE body. That check "
                             "prevents double-converting UBE-native armor "
                             r"that does not use the !UBE\ path convention; "
                             "use this if it ever misjudges a CBBE mod.")
    auto_p.add_argument("--convert-overlays", action="store_true",
                        help="OPT-IN: rebake CBBE/3BA body overlays (RaceMenu "
                             "tattoos / body paints) into UBE UV space so they "
                             "align on the UBE body. Writes loose DDS at the "
                             "original texture paths (RaceMenu loads them via "
                             "load order; no ESP). Needs texconv. Off by default.")
    auto_p.add_argument("--overlays-only", action="store_true",
                        help="Run ONLY the body-overlay -> UBE UV transfer and "
                             "skip the armor conversion / merge / coverage "
                             "entirely. Use to refresh overlays without a full "
                             "(slow) armor reconvert. Implies --convert-overlays.")
    auto_p.add_argument("--overlay-copy", action="store_true",
                        help="Overlay mode: instead of OVERWRITING each overlay "
                             "(which changes it for every body), ADD a separate "
                             "\"UBE <name>\" copy to the RaceMenu list so the "
                             "original still works on non-UBE races. Needs the "
                             "Papyrus compiler (CBBE2UBE_PAPYRUS_COMPILER).")
    auto_p.add_argument("--overlay-skip-male", action="store_true",
                        help="Skip MALE overlays when converting (converting them "
                             "to the female UBE UV does not work).")
    auto_p.add_argument("--overlay-mods", action="append", default=None,
                        metavar="MOD",
                        help="Convert overlays ONLY from these mods (repeat the "
                             "flag or comma-separate). Others keep their original "
                             "overlays. Omit to convert every mod's overlays.")
    auto_p.add_argument("--list-only", "--dry-run", action="store_true",
                        dest="list_only",
                        help="Discover + print the armor mods that WOULD be "
                             "converted, then exit (no conversion). Use to "
                             "preview the source set before a full rebuild.")
    auto_p.add_argument("--only-mods", action="append", default=None,
                        metavar="NAME",
                        help="Reconvert ONLY these armor mods (exact mod-folder "
                             "name; repeat the flag or comma-separate). The merge "
                             "still rebuilds the Combined ESP over ALL patches in "
                             "_unmerged_patches/, so unselected mods keep their "
                             "existing patch + meshes. Requires a prior full run "
                             "to have populated _unmerged_patches/.")
    auto_p.add_argument("--exclude-mods", action="append", default=None,
                        metavar="NAME",
                        help="Never convert these armor mods on an All-mods run "
                             "(repeat the flag or comma-separate). Use for mods "
                             "already built for UBE -- converting them would "
                             "double-convert and break them. The armour they "
                             "define gets no coverage armature either.")
    auto_p.add_argument("--coverage-exclude-mods", action="append", default=None,
                        metavar="NAME",
                        help="Give the armour these mods define no coverage "
                             "armature, without changing which mods are "
                             "converted (repeat or comma-separate). The window "
                             "passes its exclusion list here on a Select-mods "
                             "run, because coverage covers the whole load order "
                             "on every run.")
    auto_p.add_argument("--overlay-exclude-mods", action="append", default=None,
                        metavar="MOD",
                        help="Never convert overlays from these mods (repeat or "
                             "comma-separate). Their overlays keep their originals.")

    # Graphical front-end (Tkinter). Drives the same `auto` pipeline on a
    # background thread; see src/gui.py. No args.
    sub.add_parser(
        "gui",
        help="launch the graphical interface (a window over the `auto` flow)")

    return p


def _is_duplicate_source(r) -> bool:
    """A mod that produced no mesh because an EARLIER source already converted the
    same paths -- the armour exists, so this is not a miss and must never be reported
    as one."""
    return any("collision" in n.lower() for n in (r.notes or []))


def _split_zero_mesh_mods(ok: list) -> "tuple[list, list]":
    """Mods that produced NO converted mesh, split by WHY: `(duplicates, misses)`.

    This lived in THREE places: the text summary and the JSON report each built the
    split with their own copy of the predicate (`_collision_skipped` / `_collision`),
    and the per-mod detail loop called a third. Identical today, but a fix to one
    would silently miss the others and the two reports would then disagree about how
    many mods failed -- the kind of divergence nobody notices until the numbers get
    quoted at each other.

    Consolidating this actually surfaced that third caller: removing one copy left it
    dangling, and `write_conversion_summary`'s blanket `except Exception: return None`
    swallowed the NameError into a silently missing report file. A test caught it."""
    zero_all = [(s, r) for s, r in ok if len(r.nif_results) == 0]
    return ([(s, r) for s, r in zero_all if _is_duplicate_source(r)],
            [(s, r) for s, r in zero_all if not _is_duplicate_source(r)])


def write_conversion_summary(output_dir: Path, results: list) -> Path | None:
    """Write a batch coverage report (`conversion_summary.txt`) at the output root.

    `results` is `[(source_dir, AutoConvertResult | None, error | None)]`.
    Best-effort: never raises. Returns the written path, or None on failure.
    """
    try:
        n_mods = len(results)
        ok = [(s, r) for s, r, e in results if r is not None and e is None]
        failed = [(s, e) for s, r, e in results if e is not None]
        tot_nifs = sum(len(r.nif_results) for _, r in ok)
        tot_copy = sum(r.nif_copy_count for _, r in ok)
        tot_swap = sum(r.nif_swap_count for _, r in ok)
        tot_skip = sum(r.nif_skipped for _, r in ok)
        tot_err = sum(r.nif_errors for _, r in ok)
        tot_loadfail = sum(len(r.nif_load_failures) for _, r in ok)
        tot_vfs_other = sum(r.vfs_other_mod_count for _, r in ok)
        tot_patches = sum(len(r.output_esps) for _, r in ok)
        zero_dup, zero = _split_zero_mesh_mods(ok)

        L: list[str] = []
        L.append("CBBE -> UBE batch conversion summary")
        L.append(f"output mod : {output_dir}")
        L.append("")
        L.append(f"source mods processed : {n_mods}")
        L.append(f"  converted ok        : {len(ok)}")
        L.append(f"  hard failures       : {len(failed)}")
        L.append("")
        L.append("totals across batch")
        L.append(f"  armour NIFs written : {tot_nifs} "
                 f"(copy {tot_copy} / body-swap {tot_swap} / skipped {tot_skip})")
        L.append(f"  ESP patches         : {tot_patches}")
        L.append(f"  resolved from OTHER mods (VFS broadening): {tot_vfs_other}")
        L.append(f"  NIF conversion errors: {tot_err}")
        L.append(f"  output load failures : {tot_loadfail}")
        L.append("")

        if zero:
            L.append(f"** {len(zero)} selected mod(s) produced ZERO meshes and "
                     "NOTHING resolved (most likely still missing in-game — "
                     "check these):")
            for s, _ in zero:
                L.append(f"     - {s.name}")
            L.append("")
        if zero_dup:
            L.append(f"{len(zero_dup)} duplicate source mod(s) wrote 0 NIFs "
                     "because every mesh was already converted under another "
                     "source (collision / first-writer-wins) — these are NOT "
                     "missing, the armour IS converted:")
            for s, _ in zero_dup:
                L.append(f"     - {s.name}")
            L.append("")
        if failed:
            L.append(f"** {len(failed)} mod(s) failed outright:")
            for s, e in failed:
                L.append(f"     - {s.name}: {e!r}")
            L.append("")

        # PACK-WIDE swallowed pass failures. The per-mod reports carry this too,
        # but a pass that fails on every piece would be spread across ~162 files
        # and read as noise in each one. Rolled up here it is one line, and a
        # systematically broken pass becomes obvious instead of invisible.
        pack_fails = _pack_pass_failures(ok)
        if pack_fails:
            L.append(f"** swallowed PASS FAILURES: {sum(pack_fails.values())} "
                     f"across {len(pack_fails)} pass(es) and {len(ok)} mod(s).")
            L.append("   These pieces still CONVERTED, so they are in no error "
                     "count above -- but the pass did not do its job.")
            for _label, _n in sorted(pack_fails.items(),
                                     key=lambda kv: (-kv[1], kv[0])):
                L.append(f"     {_n:>6} x  {_label}")
            L.append("")

        L.append("per-mod detail")
        for s, r in ok:
            if len(r.nif_results) == 0:
                flag = ("  (0 NIFs - all collision-skipped; converted under "
                        "another source)" if _is_duplicate_source(r)
                        else "  ** 0 meshes (nothing resolved)")
            else:
                flag = ""
            L.append(f"  {s.name}{flag}")
            L.append(f"     ESPs : {len(r.source_esps)} source "
                     f"-> {len(r.output_esps)} patch")
            extra = ""
            if r.nif_errors:
                extra += f"  errors:{r.nif_errors}"
            if r.nif_load_failures:
                extra += f"  load-fail:{len(r.nif_load_failures)}"
            L.append(f"     NIFs : {len(r.nif_results)} "
                     f"(copy {r.nif_copy_count}/swap {r.nif_swap_count}"
                     f"/skip {r.nif_skipped}){extra}")
            if r.vfs_other_mod_count:
                L.append(f"     VFS  : {r.vfs_other_mod_count} mesh(es) from "
                         "other mods (BodySlide/replacer/patch)")
            L.append(f"     tex  : {r.textures_copied} file(s)")

        out = output_dir / "conversion_summary.txt"
        out.write_text("\n".join(L) + "\n", encoding="utf-8")
        return out
    except Exception:
        return None


def write_conversion_report_json(output_dir, results,
                                 weight_warnings=None,
                                 workers=None,
                                 orphan_temps_removed=0,
                                 *, planned=None,
                                 complete=True) -> "Path | None":
    """Machine-readable sibling of conversion_summary.txt, for the GUI health
    panel. Same batch stats plus the postflight invisibility-risk signal
    (weight-partner divergence). Best-effort; never raises.

    `complete=False` writes the same report for the run SO FAR -- the
    checkpoint `_cmd_convert` rewrites after every source -- so a run that
    dies leaves its own scoreboard, marked incomplete, instead of the previous
    run's finished one. `planned` is how many sources the batch set out to
    convert. Written atomically: a death during the write leaves the previous
    checkpoint whole, never a torn file. #report-checkpoint"""
    import json
    from .atomic_io import atomic_write_bytes
    try:
        ok = [(s, r) for s, r, e in results if r is not None and e is None]
        failed = [(s, e) for s, r, e in results if e is not None]

        _dup, _miss = _split_zero_mesh_mods(ok)
        zero_dup = [s.name for s, _r in _dup]
        zero = [s.name for s, _r in _miss]
        rep = {
            # First, so a reader sees it before any count: False means the run
            # had not finished when this was written. A reader that predates
            # the key treats its absence as a finished run, which is what every
            # older report was.
            "complete": bool(complete),
            "output_mod": str(output_dir),
            "source_mods": len(results),
            "sources_planned": (len(results) if planned is None else int(planned)),
            "converted_ok": len(ok),
            "hard_failures": len(failed),
            "armor_nifs": sum(len(r.nif_results) for _, r in ok),
            "esp_patches": sum(len(r.output_esps) for _, r in ok),
            "nif_errors": sum(r.nif_errors for _, r in ok),
            # Converted but WITHOUT body morphs -- not an "error", and so absent
            # from every counter above until 4 pieces shipped that way unseen.
            "nif_morph_losses": sum(r.nif_morph_losses for _, r in ok),
            "nif_morph_loss_pieces": sorted(
                str(n.src_path.name)
                for _, r in ok for n in r.nif_morph_loss_results)[:50],
            "load_failures": sum(len(r.nif_load_failures) for _, r in ok),
            "vfs_resolved": sum(r.vfs_other_mod_count for _, r in ok),
            "zero_mesh_mods": zero,
            "zero_mesh_dup_mods": zero_dup,
            "failed_mods": [{"name": s.name, "error": repr(e)}
                            for s, e in failed],
            "weight_partner_warnings": list(weight_warnings or []),
            # Passes that RAISED and were swallowed. Same shape as
            # nif_morph_losses above and for the same reason: the piece still
            # converted, so it is absent from every counter above -- and a pass
            # broken on every piece would otherwise look like a design that
            # simply does nothing.
            "pass_failures": _pack_pass_failures(ok),
            # WHAT CHANGED, beside what BROKE. A build carrying several changes
            # cannot be debugged from a bad in-game report unless each change
            # says which pieces it touched -- and the run log cannot carry it,
            # because those are worker prints and the frozen exe drops them.
            # This is the only durable channel. Reported SEPARATELY from
            # failures on purpose: a change with many effects and no failures is
            # working, one with no effects is not reaching anything.
            "pass_effects": _pack_pass_effects(ok),
            # WHICH pieces each pass failed on, beside how many. `pass_failures`
            # stays a {name: count} map because the GUI and the post-reconvert
            # audit read that shape; this is additive.
            "pass_failure_pieces": _pack_pass_failure_pieces(ok),
            # Temp files an INTERRUPTED earlier run left, removed at this run's
            # start. #orphan-temps
            "orphan_temps_removed": int(orphan_temps_removed or 0),
        }
        # Attribution: which build, which settings (RESOLVED, not just the
        # env overrides), which settings file. Also written on its own as
        # conversion_settings.json so a pack carries its recipe with it.
        try:
            from . import build_info
            rep["run_config"] = build_info.run_config(workers=workers)
            # The sidecar is written at batch start and at the end; a
            # checkpoint carries the block inside the report only.
            if complete:
                build_info.write_run_config(output_dir, workers=workers)
        except Exception as _e:
            rep["run_config"] = {"error": f"{type(_e).__name__}: {_e}"}
        out = Path(output_dir) / "conversion_report.json"
        atomic_write_bytes(out, json.dumps(rep, indent=2, default=str).encode("utf-8"))
        return out
    except Exception:
        return None


def _checkpoint_report(output_dir, results, *, planned, workers,
                       orphan_temps_removed=0) -> "Path | None":
    """conversion_report.json for the run SO FAR, marked incomplete: one small
    atomic write after every source. #report-checkpoint"""
    out = write_conversion_report_json(
        output_dir, results, workers=workers,
        orphan_temps_removed=orphan_temps_removed, planned=planned,
        complete=False)
    if out is None:
        warn("could not write the conversion_report.json checkpoint",
             where=f"under {output_dir}",
             consequence="a run that dies now leaves no report",
             fix="check that the output folder is writable and not open elsewhere")
    return out


def _stamp_run_start(output_dir, *, planned, workers, orphan_temps_removed=0) -> None:
    """conversion_settings.json and an EMPTY report, marked incomplete, before
    the first source converts. #report-checkpoint

    Both used to be written after the last source. MEASURED 2026-09-15: a run
    killed mid-batch left neither -- its output root held only the staging
    folders -- while the previous run's finished conversion_report.json stayed
    where the Results tab reads it, so the old scoreboard was shown as this
    run's. The settings depend on nothing the batch produces, and an empty
    report replaces the stale one before any source runs. Never raises."""
    from . import build_info
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    except OSError as _e:
        warn(f"could not create the output folder: {plain_error(_e)}",
             where=str(output_dir),
             consequence="nothing can be written there",
             fix="check the path and its permissions")
    if build_info.write_run_config(output_dir, workers=workers) is None:
        warn("could not write conversion_settings.json", where=f"under {output_dir}",
             consequence="the output mod will not record which build and settings made it")
    _checkpoint_report(output_dir, [], planned=planned, workers=workers,
                       orphan_temps_removed=orphan_temps_removed)


def verify_output(output_dir) -> dict:
    """Re-check an EXISTING output mod without reconverting: read the last
    conversion_report.json and re-run the weight-partner (invisibility-risk)
    scan against the current meshes. For the GUI 'Verify output' button."""
    import json
    out = Path(output_dir)
    res: dict = {"output_mod": str(out), "exists": out.is_dir()}
    try:
        rj = out / "conversion_report.json"
        if rj.is_file():
            res["report"] = json.loads(rj.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        res["weight_partner_warnings"] = _postflight_weight_partner_divergence(out)
    except Exception:
        res["weight_partner_warnings"] = []
    return res


# NOTE: _unified_coverage_on() was REMOVED. Unified coverage used to be
# opt-in via an env var or a `UNIFIED_COVERAGE` sentinel file next to the
# exe -- and because it defaulted OFF and failed SILENTLY to the legacy
# model, losing that file (a /MIR deploy, a modlist update) silently
# reverted the whole coverage model with no error. It is now the only
# model, so there is nothing to toggle and nothing to lose.

_UBE_COVERED_CACHE: dict = {}
# {same cache key -> {mod name: how many ARMOs it already covers for UBE}}.
# Diagnostics only -- nothing in the convert path reads it.
_UBE_COVERED_BY_MOD: dict = {}

# Written as a comment into every SkyPatcher INI we emit, and read back by
# `_is_our_own_output`.
SKYPATCHER_INI_MARKER = "cbbe-to-ube"

# Header markers this tool has written across its versions. Recognising OUR
# OWN output cannot depend on the current marker alone: a modlist can hold
# output from an OLDER build, and that build stamped a different header.
_OUR_INI_MARKERS = (SKYPATCHER_INI_MARKER, "ube converter")


def _is_our_own_output(mod_dir) -> bool:
    """True if this mod folder is output from THIS tool -- ANY version, ANY
    folder name.

    Without this, a leftover output mod from an earlier run reads as a
    third-party UBE provider covering thousands of armors, and we suppress our
    own coverage wholesale -- converting everything and delivering none of it.
    Measured on a real modlist: one stale output mod alone pushed the exclusion
    set from 336 to 8873 and would have suppressed 8318 of 10056 links.

    The primary test is STRUCTURAL -- a conversion report at the mod root is a
    file only this tool writes, and it has been written under every version, so
    it identifies old output that predates any header marker. The INI-header
    check is the fallback for output whose report was deleted."""
    md = Path(mod_dir)
    try:
        if (md / "conversion_report.json").is_file():
            return True
        if next(md.glob("conversion_report_*.txt"), None) is not None:
            return True
        for ini in md.glob("SKSE/Plugins/SkyPatcher/armor/*.ini"):
            try:
                head = ini.read_text(encoding="utf-8", errors="replace")[:400]
            except OSError:
                continue
            low = head.lower()
            if any(m in low for m in _OUR_INI_MARKERS):
                return True
    except OSError:
        pass
    return False


def _skypatcher_fields(line: str) -> dict:
    r"""Parse one SkyPatcher INI line into {key: value}.

    The grammar is `key=value:key=value:...` with `;` starting a comment. Three
    things make naive parsing wrong, and all three were reproduced against real
    syntax:
      * a fully commented-out line still contains `filterByArmors=`, so without
        stripping comments a disabled rule is read as live;
      * `filterByArmorsExcluded` is a real filter meaning the OPPOSITE, and it
        has `filterByArmors` as a prefix -- so keys must match exactly;
      * operations may legally precede filters, so the targets are not always
        in the first segment.
    """
    line = line.split(";", 1)[0].strip()
    if not line:
        return {}
    out: dict = {}
    for seg in line.split(":"):
        if "=" not in seg:
            continue
        k, v = seg.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _skypatcher_forms(value: str):
    """Yield (plugin lowercase, formid low24) for a comma-separated form list.

    A full load-indexed ESL form (`FE012800`) masks to a different low24 than
    the record's own index, so it simply will not match -- under-detecting,
    never falsely excluding. EditorID-form targets are skipped likewise."""
    for t in (value or "").split(","):
        t = t.strip()
        if "|" not in t:
            continue
        pl, fid = t.rsplit("|", 1)
        try:
            yield (pl.strip().lower(), int(fid, 16) & 0xFFFFFF)
        except ValueError:
            continue


def _third_party_ube_covered_armos(mods_root, enabled_names=None,
                                   skip_mods=(), halves=("ini", "esp"),
                                   active_plugins=None) -> set:
    r"""ARMOs that ANOTHER mod already gives a UBE armature.

    Returned as {(defining plugin lowercase, formid low24)} -- the same
    identity `exclude_armo_abs` takes in both coverage generators.

    WHY: adding our armature to an armor that is ALREADY covered for UBE means
    the actor renders TWO bodies for that slot (z-fighting / doubled cloth).
    A hand-made UBE patch is also almost always a better fit than an automatic
    conversion, so the right behaviour is to leave that armor entirely alone and
    coexist rather than compete. Measured on a real modlist: 347 ARMOs already
    carried a third-party UBE armature and we were double-covering 143 of them.

    Two delivery mechanisms are detected, because UBE patches use both:
      * ESP/ESL: an ARMA whose model path is under `!UBE\`, and any ARMO that
        references it (the armor's own record, or an override of a master's).
      * SkyPatcher: another mod's `armorAddonsToAdd` INI lines, which name their
        targets directly -- the same mechanism this tool uses.

    #skypatcher-patch-recognition (2026-09-23). The SkyPatcher half read only
    `armor/*.ini`, but SkyPatcher nests freely inside its type folders and
    recommends a subfolder for a plugin-named INI. A follower's hand-made UBE
    refit lives in `armor/<plugin>/<plugin>.esp.ini`: 11 lines, 0 read, and every
    piece was double-covered in game (the report: male boots on her). Two of
    its addons reuse the source mesh on the UBE races (a helmet and a wig need
    no refit), so no `!UBE\` path could ever name them. Now the INIs are read
    recursively, and an added addon also counts when its PRIMARY race is a
    UBE_AllRace race and its mesh is a loose file -- but only when the UBE
    addons a target receives cover every biped slot of that armour, because a
    cape added to a cuirass must not stop the cuirass being covered. Measured
    on the live modlist: 11 of 11 of that patch's targets, 0 other armours.
    The ESP half keeps the path test (the same race test there moves 37 other
    armours, most of one body mod's plugin, unverified).
    CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION=1 restores `armor/*.ini` and the
    path test alone.

    `halves` (#coverage-third-party-drawn, 2026-09-25): which delivery
    mechanisms add their targets -- "ini" (SkyPatcher lines) and/or "esp"
    (plugin ARMOs). The coverage step asks for the INI half alone while that
    rule is on: it judges the armatures on each WINNING armour record itself,
    with the mesh they draw, which the plugin half cannot (it reads every root
    plugin's ARMOs, winning or not, and excludes on a `!UBE\` path alone). The
    INI half stays: an armature an INI adds is on no armour record. The
    conversion planner keeps both. The plugin files are read either way -- the
    INI half needs their UBE armatures.

    #third-party-ini-slot-check (2026-09-25): an INI line adding a `!UBE\`
    armature excluded its targets outright, with neither check the race test
    makes. A cape-only UBE addon on a cuirass hid the cuirass (no torso on UBE
    actors), and so did an addon whose plugin is unchecked in the load order
    (SkyPatcher adds nothing then, and neither did we). Now both kinds of UBE
    addon go through one test: only addons whose plugin is in `active_plugins`
    (the loaded plugin names; None = unknown, no check) count, and a target is
    excluded only when their slots cover every slot of the armour. An armour no
    enabled mod's plugin defines (vanilla, not overridden) cannot be checked and
    stays covered. Measured on the live modlist: 11 INI lines, all active, all
    slot-complete -- 0 armours move. CBBE2UBE_NO_THIRD_PARTY_INI_SLOT_CHECK=1
    excludes on any `!UBE\` addon again, loaded or not.

    Best-effort and CACHED per (root, skip) -- an unreadable plugin is skipped,
    never fatal: failing to detect coverage costs a double-render, while a
    crash here would cost the whole run."""
    import struct as _struct
    from . import esp as _esp   # module scope has no esp import
    if mods_root is None:       # no modlist: no other mods to have patched it
        return set()            # #convert-needs-a-modlist
    recognise = not _flag("CBBE2UBE_NO_SKYPATCHER_PATCH_RECOGNITION", False)
    # #claim-meshes-prefix (2026-09-24): an ARMA may spell its model path with
    # the `meshes\` folder in front -- the engine reads it either way. The
    # SkyPatcher half stripped it; the plugin half did not, so a softbody pack's
    # own UBE nude suit (`meshes\!UBE\SexLab\...`) never read as a UBE claim.
    # CBBE2UBE_NO_CLAIM_MESHES_PREFIX=1 compares the path as written again.
    strip_meshes = not _flag("CBBE2UBE_NO_CLAIM_MESHES_PREFIX", False)
    # enabled_names belongs in the key: it changes the result, and in the
    # long-lived GUI process the modlist can change between two scans. So do
    # the two switches.
    want_ini = "ini" in halves
    want_esp = "esp" in halves
    key = (str(mods_root), tuple(sorted(skip_mods)),
           None if enabled_names is None else tuple(sorted(enabled_names)),
           recognise, strip_meshes)
    key += (want_ini, want_esp)     # the halves change the result too
    # #third-party-ini-slot-check: its switch and the load order it reads.
    slot_check = not _flag("CBBE2UBE_NO_THIRD_PARTY_INI_SLOT_CHECK", False)
    active = (None if active_plugins is None
              else {str(n).lower() for n in active_plugins})
    key += (slot_check, None if active is None else tuple(sorted(active)))
    if key in _UBE_COVERED_CACHE:
        return _UBE_COVERED_CACHE[key]
    covered: set = set()
    ube_armas: set = set()      # abs identity of every UBE armature found
    pending_ini: list = []      # (mod name, ini text) judged in a 2nd pass
    by_mod: dict = {}           # who supplied each exclusion (magnitude guard)

    def _add(ident, mod_name):
        if ident not in covered:
            covered.add(ident)
            by_mod[mod_name] = by_mod.get(mod_name, 0) + 1

    root = Path(mods_root)
    skip = {s.lower() for s in skip_mods}

    # SAFETY GATE for the MOD3 test below. SKIPPING IS THE DANGEROUS DIRECTION:
    # a false "already covered" removes an armour from the only delivery path
    # there is and it renders NOTHING, while a false negative merely
    # double-covers. So only believe a third-party UBE claim when the mesh it
    # names actually EXISTS.
    #
    # Measured 2026-08-22 over the live order: of the 259 coverages the old
    # MOD4 test missed, 242 have the mesh present and 17 do NOT. Without this
    # gate those 17 would go straight from "converted" to "invisible" the first
    # time the corrected slot test ran.
    #
    # LOOSE FILES ONLY, and the asymmetry is deliberate: a mesh that lives only
    # in a BSA reads as unresolved here and therefore falls through to
    # CONVERTING, which is the safe direction. Built lazily and once -- the
    # whole function is memoised per (root, skip, enabled).
    _ube_mesh_index: "set[str] | None" = None

    def _ube_mesh_resolves(model_rel: str) -> bool:
        nonlocal _ube_mesh_index
        if _ube_mesh_index is None:
            idx: "set[str]" = set()
            try:
                for _md in root.iterdir():
                    if not _md.is_dir() or _md.name.lower() in skip:
                        continue
                    if enabled_names is not None and _md.name not in enabled_names:
                        continue
                    if _is_our_own_output(_md):
                        continue
                    for _sub in ("meshes", "Meshes"):
                        _d = _md / _sub
                        if not _d.is_dir():
                            continue
                        for _p in _d.rglob("*.nif"):
                            _rel = str(_p.relative_to(_d)).lower().replace("\\", "/")
                            if _rel.startswith("!ube/"):
                                idx.add(_rel)
                        break
            except Exception:
                pass            # an unreadable tree must not fail the scan
            _ube_mesh_index = idx
        rel = model_rel.lower().replace("\\", "/").lstrip("/")
        if strip_meshes and rel.startswith("meshes/"):
            rel = rel[len("meshes/"):]          # #claim-meshes-prefix
        return rel in _ube_mesh_index
    try:
        mod_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        _UBE_COVERED_CACHE[key] = covered
        return covered
    mod_dirs = [md for md in mod_dirs
                if md.name.lower() not in skip
                and (enabled_names is None or md.name in enabled_names)
                and not _is_our_own_output(md)]   # our own output, any folder name
    # (a) SkyPatcher INIs -- DEFERRED. A line's targets only count if the
    # armature it adds is itself UBE, and that armature can live in any mod, so
    # the INIs cannot be judged until every plugin has been read. Read first so
    # the plugin pass knows which armours' slots the race test needs.
    _ini_glob = ("SKSE/Plugins/SkyPatcher/armor/**/*.ini" if recognise
                 else "SKSE/Plugins/SkyPatcher/armor/*.ini")
    for md in mod_dirs:
        try:
            inis = sorted(md.glob(_ini_glob))
        except OSError:
            # A recursive walk can meet an unreadable or over-long folder. That
            # costs this mod's INIs, never the whole scan: an exception here
            # empties EVERY exclusion and double-covers all third-party patches.
            inis = []
        for ini in inis:
            try:
                pending_ini.append((md.name,
                                    ini.read_text(encoding="utf-8",
                                                  errors="replace")))
            except OSError:
                continue
    ini_targets: set = set()     # armours an INI line names (race test only)
    race_armas: dict = {}        # abs -> (BOD2 slots, MOD3): UBE_AllRace-primary
    armo_slots: dict = {}        # abs -> union of BOD2 slots over its records
    ube_arma_slots: dict = {}    # abs -> BOD2 slots of a `!UBE\` armature
    if recognise or slot_check:
        for _mn, _txt in pending_ini:
            for _ln in _txt.splitlines():
                _f = _skypatcher_fields(_ln)
                if _f.get("armorAddonsToAdd") and _f.get("filterByArmors"):
                    ini_targets.update(_skypatcher_forms(_f["filterByArmors"]))
    for md in mod_dirs:
        # (b) plugins that define a UBE ARMA and an ARMO pointing at it
        for pl in list(md.glob("*.esp")) + list(md.glob("*.esm")) + list(md.glob("*.esl")):
            try:
                e = _esp.ESP.load(pl)
            except Exception:
                continue
            masters = [m.lower() for m in e.header.masters]
            own = pl.name.lower()

            def _abs(formid):
                mi = formid >> 24
                return ((masters[mi] if mi < len(masters) else own),
                        formid & 0xFFFFFF)

            ube_fids = set()
            for g in e.groups:
                if g.label != b"ARMA":
                    continue
                for r in g.records:
                    for sig, dd in _esp.iter_subrecords(r.payload):
                        # MOD3 ONLY -- the FEMALE WORLD model. Testing ONE slot
                        # is right (accepting any of the four marked an armour
                        # "already covered" on the strength of a `!UBE\` path in
                        # a slot that is never drawn on the body -- REPORTED IN
                        # GAME as an invisible colour variant whose MOD3 was
                        # still the CBBE path). Testing MOD4 was the WRONG one.
                        #
                        # THE SLOTS ARE MOD2 male world, MOD3 FEMALE WORLD, MOD4
                        # male first person, MOD5 female first person. Corrected
                        # 2026-08-22 FROM THE DATA, not from a comment: across
                        # our own 4822 minted ARMA records the `!UBE\` path sits
                        # in MOD3 (93.7%) and MOD5 (93.8%) while MOD2/MOD4 carry
                        # `...\Male\...` paths, and the examples name themselves
                        # (`..._F_1.nif` vs `Male\1stPersonbody_1.nif`). This
                        # converter is female-only, so the slot carrying `!UBE`
                        # IS the female slot. Cross-checked against Mutagen:
                        # WorldModel[0]/[1] = MOD2/MOD3, FirstPersonModel[0]/[1]
                        # = MOD4/MOD5.
                        #
                        # MEASURED COST OF THE OLD TEST, over the live order:
                        #   !UBE in BOTH MOD3 and MOD4   79   caught, by luck
                        #   !UBE in MOD3 ONLY           259   MISSED
                        #   !UBE in MOD4 ONLY             0   caught nothing new
                        # So MOD4 found nothing MOD3 does not, and missed 77% of
                        # real third-party female coverage -- which is why 226
                        # of our meshes still shadow a hand-made UBE conversion.
                        if sig == b"MOD3":
                            s = dd.rstrip(bytes(1)).decode("cp1252", "replace")
                            if _is_already_ube_model(s) and _ube_mesh_resolves(s):
                                ube_fids.add(r.formid)
                                ube_armas.add(_abs(r.formid))
                                break
                    if slot_check and r.formid in ube_fids:
                        # #third-party-ini-slot-check: the slots it draws on.
                        # Read by two plugins, only the slots both give count.
                        for sig, dd in _esp.iter_subrecords(r.payload):
                            if sig in (b"BOD2", b"BODT") and len(dd) >= 4:
                                _us = _struct.unpack_from("<I", dd)[0]
                                _ua = _abs(r.formid)
                                ube_arma_slots[_ua] = (
                                    ube_arma_slots.get(_ua, _us) & _us)
                                break
                    if recognise:
                        # #skypatcher-patch-recognition: an armature whose
                        # PRIMARY race is a UBE_AllRace race, whatever its mesh
                        # path. Judged only where an INI line adds it.
                        _rn, _bod, _m3 = None, 0, ""
                        for sig, dd in _esp.iter_subrecords(r.payload):
                            if sig == b"RNAM" and len(dd) >= 4:
                                _rn = _abs(_struct.unpack_from("<I", dd)[0])
                            elif sig in (b"BOD2", b"BODT") and len(dd) >= 4:
                                _bod = _struct.unpack_from("<I", dd)[0]
                            elif sig == b"MOD3":
                                _m3 = dd.rstrip(bytes(1)).decode("cp1252", "replace")
                        if _rn is not None and _rn[0] == "ube_allrace.esp":
                            race_armas[_abs(r.formid)] = (_bod, _m3)
            if ini_targets:
                for g in e.groups:
                    if g.label != b"ARMO":
                        continue
                    for r in g.records:
                        _a = _abs(r.formid)
                        if _a not in ini_targets:
                            continue
                        for sig, dd in _esp.iter_subrecords(r.payload):
                            if sig in (b"BOD2", b"BODT") and len(dd) >= 4:
                                armo_slots[_a] = (armo_slots.get(_a, 0)
                                                  | _struct.unpack_from("<I", dd)[0])
                                break
            if not ube_fids:
                continue
            for g in e.groups:
                if g.label != b"ARMO":
                    continue
                for r in g.records:
                    hit = False
                    for sig, dd in _esp.iter_subrecords(r.payload):
                        if sig == b"MODL" and len(dd) == 4:
                            if _struct.unpack("<I", dd)[0] in ube_fids:
                                hit = True
                                break
                    if not hit:
                        continue
                    if want_esp:        # #coverage-third-party-drawn: halves
                        _add(_abs(r.formid), md.name)

    # The race test's mesh gate: the same loose-files-only rule as
    # `_ube_mesh_resolves`, for a path outside `!UBE\`. Asked only for the
    # addons an INI line names, so a per-path probe beats indexing every mesh.
    _loose_seen: dict = {}

    def _race_mesh_resolves(model: str) -> bool:
        rel = model.replace("\\", "/").lstrip("/")
        if rel.lower().startswith("meshes/"):
            rel = rel[7:]
        if not rel:
            return False
        if _is_already_ube_model(rel):
            return _ube_mesh_resolves(rel)
        if rel.lower() not in _loose_seen:
            def _is_file(p):
                try:
                    return p.is_file()
                except OSError:
                    return False      # unreadable: not proof the mesh exists
            _loose_seen[rel.lower()] = any(
                _is_file(md / "meshes" / rel) for md in mod_dirs)
        return _loose_seen[rel.lower()]

    # (a, second pass) Now that every UBE armature is known, judge the INIs.
    race_cover: dict = {}        # target -> [slots its race addons cover, mod]
    for mod_name, txt in (pending_ini if want_ini else ()):
        for line in txt.splitlines():
            fields = _skypatcher_fields(line)
            if not fields:
                continue
            addons = fields.get("armorAddonsToAdd")
            targets = fields.get("filterByArmors")
            if not addons or not targets:
                continue
            _forms = list(_skypatcher_forms(addons))
            if slot_check and active is not None:
                # #third-party-ini-slot-check: SkyPatcher adds nothing from a
                # plugin that is not loaded, so neither addon kind counts.
                _forms = [a for a in _forms if a[0] in active]
            # The armature being ADDED must itself be UBE. Without this any
            # armorAddonsToAdd line in the modlist -- a cape addon, a heels
            # addon, another body's compat patch -- would permanently remove
            # its targets from the only delivery path there is.
            _ube_hit = any(a in ube_armas for a in _forms)
            if _ube_hit and not slot_check:
                # Switched off: any `!UBE\` addon hides every target outright.
                for t in _skypatcher_forms(targets):
                    _add(t, mod_name)
                continue
            # #skypatcher-patch-recognition: a UBE-race addon counts toward
            # the slots it covers; the target is judged once every line
            # has been read (a patch may add its pieces on several lines).
            # #third-party-ini-slot-check: a `!UBE\` addon goes through the
            # same slot test.
            _slots, _hit = 0, False
            for a in _forms:
                if a in ube_armas:
                    _slots |= ube_arma_slots.get(a, 0)
                    _hit = True
                elif recognise:
                    _ra = race_armas.get(a)
                    if _ra is not None and _race_mesh_resolves(_ra[1]):
                        _slots |= _ra[0]
                        _hit = True
            if _hit:
                for t in _skypatcher_forms(targets):
                    _rc = race_cover.setdefault(t, [0, mod_name])
                    _rc[0] |= _slots
    # A target counts only when its UBE addons cover EVERY slot the armour
    # claims. An armour no enabled plugin defines cannot be checked, and is left
    # to be covered (double-covering is the safe direction).
    for t, (_slots, mod_name) in race_cover.items():
        _need = armo_slots.get(t)
        if _need is not None and not (_need & ~_slots):
            _add(t, mod_name)

    # Per-mod attribution, kept for diagnostics. The exclusion set alone answers
    # "how many armors are already UBE-covered" but not "BY WHAT" -- and that is
    # the question to answer before deciding whether a provider is a hand-made
    # patch worth keeping or a replacer to disable. Recorded from the SAME scan,
    # so it can never disagree with the set it explains.
    _UBE_COVERED_BY_MOD[key] = dict(by_mod)
    if by_mod:
        top, n = max(by_mod.items(), key=lambda kv: kv[1])
        # A single mod supplying most of a large exclusion set is the shape of
        # the self-detection failure this guards: output from an older build
        # read as a third-party provider suppressed 8318 of 10056 links.
        if len(covered) >= 2000 and n >= 0.4 * len(covered):
            warn(f"[unified] {n} of {len(covered)} 'already UBE' armors come from a "
                 f"single mod ({top})",
                 consequence="if that is converter output rather than a hand-made "
                             "patch, coverage is being suppressed wrongly",
                 fix="check that mod before trusting this run")
    _UBE_COVERED_CACHE[key] = covered
    return covered


def _print_coverage_warnings(label: str, stats: dict) -> None:
    """Surface a coverage pass's validator warnings.

    The standalone coverage blocks printed these; when coverage moved inside
    the merge the key was simply ignored, silently losing the diagnostics for
    the ONLY coverage model.

    `missing-nif` USED TO BE FILTERED OUT HERE, on the reasoning that it "fires
    in bulk on retexture mods that ship no meshes of their own". That rationale
    describes SOURCE paths -- and the check no longer counts those: it skips
    anything without the `!UBE` path prefix, so it now reports only paths
    TOOL produced pointing at meshes THIS TOOL did not write. That is the
    condition behind startup-CTD cause #1 (an ARMA aimed at an absent !UBE
    hood -> EXCEPTION_ACCESS_VIOLATION when an actor wearing it loads), which
    the project notes say never to dismiss. Measured on the live shipped
    Combined ESPs: 0 occurrences, so it is not noisy today either. Printed."""
    try:
        ws = [w for w in (stats.get("validation_warnings") or [])]
    except Exception:
        return
    if not ws:
        return
    warn(f"{label} coverage validator: {len(ws)} warning(s)",
         consequence="the lines below name what it found in the generated race "
                     "coverage; read them before trusting this run's coverage")
    for w in ws[:5]:
        print(f"       {w}")
    if len(ws) > 5:
        print(f"       ... and {len(ws) - 5} more")


def _armos_defined_by_mods(mods_root, mod_names, ordered_plugin_paths,
                           missing: "list | None" = None) -> "tuple[set, dict]":
    r"""#exclude-owned-coverage: the ARMOs an `--exclude-mods` mod OWNS, i.e. those
    whose DEFINING plugin ships in that mod's folder.

    Returned as {(plugin lowercase, formid low24)} -- the identity both coverage
    generators key armours by -- plus {mod folder: count}.

    WHY. `--exclude-mods` only ever removed a mod from the SOURCES; the winner
    scan still minted armatures for its armour. Reported in game: a follower
    excluded because a hand-made UBE refit exists wore converted male Ebony
    boots, minted for her boots over a mesh another mod's conversion left behind.

    Ownership is the DEFINING plugin, the user's call (2026-09-23) after a census
    of four readings over that follower's 14 minted armours: defining plugin 14;
    the load-order WINNING override 3 (an overhaul patch wins the rest, as it wins
    78% of all coverage links in that modlist); the mesh the game loads 2 (a 3BA
    BodySlide output supplies her meshes); the converter's loose source index 11.
    A game master never sits in a mod folder here, so vanilla and DLC armour are
    never withheld, and the `vanilla` pseudo-name (the vanilla-sweep switch) is
    ignored. Only ACTIVE plugins count, read from the copy the game loads.

    A folder whose name holds a comma arrives split by `_split_mod_arg` (the
    CLI's comma separator); it matches when every piece of its name was given.
    Names that match no folder are appended to `missing`, for a warning."""
    from . import esp as _esp   # module scope has no esp import
    owned: set = set()
    per_mod: dict = {}
    wanted = {str(n).strip().lower() for n in (mod_names or ()) if str(n).strip()}
    wanted.discard("vanilla")
    if mods_root is None or not wanted:
        return owned, per_mod

    def _named(d: Path) -> bool:
        n = d.name.lower()
        if n in wanted:
            return True
        parts = {p.strip() for p in n.split(",") if p.strip()}
        return len(parts) > 1 and parts <= wanted
    loaded = {Path(p).name.lower(): Path(p) for p in ordered_plugin_paths}
    try:
        folders = sorted(d for d in Path(mods_root).iterdir()
                         if d.is_dir() and _named(d))
    except OSError:
        return owned, per_mod
    if missing is not None:
        found = set()
        for d in folders:
            found.add(d.name.lower())
            found.update(p.strip() for p in d.name.lower().split(","))
        missing.extend(sorted(wanted - found))
    for md in folders:
        for pl in sorted(list(md.glob("*.esp")) + list(md.glob("*.esm"))
                         + list(md.glob("*.esl"))):
            src = loaded.get(pl.name.lower())
            if src is None:
                continue          # not active: its armour is not in the game
            try:
                e = _esp.ESP.load(src)
            except Exception:
                continue
            own_byte = len(e.header.masters)
            name = src.name.lower()
            for g in e.groups:
                if g.label != b"ARMO":
                    continue
                for r in g.records:
                    if (r.formid >> 24) < own_byte:
                        continue      # an override of a master's armour
                    ident = (name, r.formid & 0xFFFFFF)
                    if ident not in owned:
                        owned.add(ident)
                        per_mod[md.name] = per_mod.get(md.name, 0) + 1
    return owned, per_mod


# #exclude-body-only: a SkyPatcher `Plugin.esp|FormID` form anywhere on a line --
# the plugin name runs back to the previous `=`, `,` or `:` (a name may hold
# spaces), the FormID may carry `0x` and leading zeros.
_INI_FORM_TOKEN = None


class _ExclusionKeepProbe:
    r"""#exclude-body-only: what the non-body coverage pass asks before it gives
    an excluded mod's non-body armour its own mesh on UBE actors. Three
    questions, each answered from the files the game reads, never from our
    structured patch reader:

    `named(armo_abs, edids)` -- the enabled mod (not our output) with a
    SkyPatcher armor INI line, at any depth under `SKSE\Plugins\SkyPatcher\armor`,
    that adds armour addons and names the armour: `plugin|formid` in any
    spelling the reader accepts (leading zeros, `0x`, a full `FE` ESL form) or
    one of its EditorIDs. A raw text scan: when our reader misses a hand-made
    refit, the exclusion still keeps the refit's pieces ours-free. None if none.

    `excluded_source(model, plugin)` -- does `model` ship loose in, or in an
    archive of, an enabled mod folder that holds `plugin` at its root (the
    excluded mod that defines the armour)? A converted mesh of that path is
    the excluded mod's own mesh, converted before it was excluded.

    `body_fit(model)` -- the loose copy the game loads (MO2 overwrite, then mods
    by priority, then the game's Data), read: True/False, or None when it is not
    loose (archive-only) or cannot be read. The caller fails closed on None.

    Built by `_exclusion_keep_probe`; every answer is cached for the run."""

    def __init__(self, mods_root, enabled_order, overwrite=None, data_dirs=()):
        root = Path(mods_root)
        self._dirs = [root / n for n in (enabled_order or ())]
        self._loose = (([Path(overwrite)] if overwrite is not None else [])
                       + self._dirs + [Path(d) for d in (data_dirs or ())])
        self._forms: "dict[tuple, str] | None" = None
        self._words: "dict[str, str]" = {}
        self._owners: dict = {}
        self._bsa: dict = {}
        self._fit: dict = {}

    @staticmethod
    def _is_file(p: Path) -> bool:
        try:
            return p.is_file()
        except OSError:
            return False

    @staticmethod
    def _rel(model: str) -> str:
        rel = str(model or "").replace("\\", "/").lstrip("/").lower()
        return rel[7:] if rel.startswith("meshes/") else rel

    def _scan(self) -> None:
        global _INI_FORM_TOKEN
        import re as _re
        if _INI_FORM_TOKEN is None:
            _INI_FORM_TOKEN = _re.compile(
                r"([^=,:|\r\n]+?\.(?:esp|esm|esl))\s*\|\s*(?:0x)?([0-9a-f]+)",
                _re.IGNORECASE)
        self._forms = {}
        for md in self._dirs:
            try:
                inis = sorted(md.glob("SKSE/Plugins/SkyPatcher/armor/**/*.ini"))
            except OSError:
                inis = []
            if not inis or _is_our_own_output(md):
                continue
            for ini in inis:
                try:
                    txt = ini.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in txt.splitlines():
                    low = line.lower()
                    if "armoraddonstoadd" not in low:
                        continue
                    for pl, hx in _INI_FORM_TOKEN.findall(low):
                        pl = pl.strip()
                        v = int(hx, 16)
                        self._forms.setdefault((pl, v & 0xFFFFFF), md.name)
                        if len(hx) >= 8 and (v >> 24) == 0xFE:
                            self._forms.setdefault((pl, v & 0xFFF), md.name)
                    for w in _re.split(r"[=,:|;\s]+", low):
                        if w:
                            self._words.setdefault(w, md.name)

    def named(self, armo_abs, edids=()) -> "str | None":
        if self._forms is None:
            self._scan()
        hit = self._forms.get((armo_abs[0].lower(), armo_abs[1] & 0xFFFFFF))
        if hit is not None:
            return hit
        for e in edids or ():
            if e and e.lower() in self._words:
                return self._words[e.lower()]
        return None

    def excluded_source(self, model: str, plugin: str) -> bool:
        rel = self._rel(model)
        if not rel:
            return False
        pl = str(plugin).lower()
        if pl not in self._owners:
            self._owners[pl] = [d for d in self._dirs if self._is_file(d / pl)]
        owners = self._owners[pl]
        if any(self._is_file(d / "meshes" / rel) for d in owners):
            return True
        if pl not in self._bsa:
            self._bsa[pl] = _BsaMeshIndex(
                owners, None,
                skip_bsa=("voice", " sound", "sounds", "- snd", "facegen"))
        return bool(owners) and self._bsa[pl].contains(rel)

    def body_fit(self, model: str) -> "bool | None":
        rel = self._rel(model)
        if not rel:
            return None
        if rel not in self._fit:
            data = None
            for d in self._loose:
                f = d / "meshes" / rel
                if self._is_file(f):
                    try:
                        data = f.read_bytes()
                    except OSError:
                        data = None
                    break
            self._fit[rel] = None if data is None else _nif_bytes_body_fit(data)
        return self._fit[rel]


def _exclusion_keep_probe() -> "_ExclusionKeepProbe | None":
    """#exclude-body-only: the probe over the active MO2 instance, or None when
    the modlist cannot be read -- the non-body pass then withholds every owned
    armour, as without the rule."""
    try:
        lay = paths.discover_layout()
        mr = paths.mods_root()
        order = paths.enabled_mods_ordered(lay)
    except Exception:
        return None
    if mr is None or not order:
        return None
    try:
        ow = paths.overwrite_dir(lay)
    except Exception:
        ow = None
    return _ExclusionKeepProbe(mr, order, overwrite=ow,
                               data_dirs=list(getattr(lay, "game_data_dirs", None) or ()))


def _loose_mesh_index_on() -> bool:
    r"""#loose-mesh-index (2026-09-25): does `_mesh_exists_anywhere` answer its
    loose-file questions from one listing of every loose `meshes` folder? Yes,
    by default. The per-path probe it replaces checked `<dir>\meshes\<path>` in
    every loose folder (~3,300 on the reported modlist) before it asked the
    archives, so each archived or dead path cost ~3,300 file checks; the dead-
    path questions of #coverage-female-standin made the coverage step ~4x slower.
    Same answers either way. CBBE2UBE_NO_LOOSE_MESH_INDEX=1 probes per path again."""
    return not _flag("CBBE2UBE_NO_LOOSE_MESH_INDEX", False)


# Windows device names: `nul.nif` may open the device, never a listed file.
_WIN_DEVICE_NAMES = frozenset(
    ["con", "prn", "aux", "nul"] + [f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)])


def _listing_can_answer(rel: str) -> bool:
    r"""#loose-mesh-index: is `rel` (lower-case, `/`-separated) a path a folder
    listing answers exactly as a file check on disk would? Windows resolves more
    than a listing shows -- `.`/`..`, a trailing dot or space, an 8.3 short name
    (`~`), a device name, non-ASCII case folding -- so such a path is checked on
    disk instead."""
    if not rel or not rel.isascii():
        return False
    for part in rel.split("/"):
        if (not part or part[-1] in ". " or "~" in part
                or any(c in ':*?"<>|' or c < " " for c in part)
                or part.split(".")[0].rstrip(" ") in _WIN_DEVICE_NAMES):
            return False
    return True


class _LooseMeshIndex:
    r"""#loose-mesh-index: every file under each loose folder's `meshes`, keyed
    by its lower-case path below `meshes`, to the FIRST folder (in the order
    given -- MO2 overwrite, then mods by priority, then the game Data) that has
    it. Listed once, on the first question. Links and junctions are followed,
    as a file check follows them. Whatever a listing cannot answer exactly is
    left to a file check (`first` returns `ASK`): a folder that could not be
    listed, or listed a non-ASCII or very long name, a link back into its own
    ancestry, and any path `_listing_can_answer` refuses."""

    ASK = object()

    def __init__(self, loose_dirs):
        self._dirs = [str(d) for d in loose_dirs]
        self._first: "dict[str, int] | None" = None
        self._unlisted: "set[str]" = set()

    def _build(self) -> None:
        import stat as _stat
        first: "dict[str, int]" = {}
        unlisted: "set[str]" = set()
        for i, d in enumerate(self._dirs):
            # (folder, its lower-case path below meshes + "/", linked folders above it)
            stack = [(os.path.join(d, "meshes"), "", frozenset())]
            top = True
            while stack:
                path, pre, links = stack.pop()
                try:
                    it = os.scandir(path)
                except (FileNotFoundError, NotADirectoryError):
                    if not top:
                        unlisted.add(pre)
                    top = False
                    continue          # no meshes folder: nothing loose here
                except OSError:
                    unlisted.add(pre)  # unreadable: a file check answers
                    top = False
                    continue
                top = False
                try:
                    with it:
                        for e in it:
                            if not e.name.isascii() or len(e.path) >= 250:
                                unlisted.add(pre)
                                continue
                            low = e.name.lower()
                            if e.is_dir():
                                sub = links
                                if e.is_symlink() or (getattr(
                                        e.stat(follow_symlinks=False),
                                        "st_file_attributes", 0)
                                        & _stat.FILE_ATTRIBUTE_REPARSE_POINT):
                                    st = os.stat(e.path)
                                    key = (st.st_dev, st.st_ino)
                                    if key in links:
                                        unlisted.add(pre + low + "/")
                                        continue
                                    sub = links | {key}
                                stack.append((e.path, pre + low + "/", sub))
                            elif e.is_file():
                                first.setdefault(pre + low, i)
                except OSError:
                    unlisted.add(pre)
        self._first, self._unlisted = first, unlisted

    def first(self, rel: str):
        """Index of the first folder holding `rel` loose, None if none does,
        or `ASK` when only a file check can tell."""
        if not _listing_can_answer(rel):
            return self.ASK
        if self._first is None:
            self._build()
        if self._unlisted:
            cut = rel.rfind("/")
            while True:
                if rel[:cut + 1] in self._unlisted:
                    return self.ASK
                if cut < 0:
                    break
                cut = rel.rfind("/", 0, cut)
        return self._first.get(rel)


def _mesh_exists_anywhere(output) -> "callable[[str], bool] | None":
    r"""#coverage-female-guard: does a source mesh exist ANYWHERE the game reads
    it -- loose in an enabled mod or the game Data, or in any archive? A mesh in a
    texture-named BSA still loads, so only the voice/sound/facegen archives are
    skipped. The batch index lists texture archives too by default
    (#texture-archive-meshes); this lookup passes its own skip list, so
    CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES does not change what it sees (one real
    female mesh in the reported modlist lives in a texture archive).

    Both lookups are lazy: one listing of the loose `meshes` folders on the
    first question (#loose-mesh-index; a per-path probe with
    CBBE2UBE_NO_LOOSE_MESH_INDEX=1), and one table scan of the archives on the
    first path not found loose. None when the modlist cannot be read -- the
    guard then treats every named path as present."""
    try:
        lay = paths.discover_layout()
        mr = paths.mods_root()
        order = paths.enabled_mods_ordered(lay)
    except Exception:
        return None
    if mr is None or not order:
        return None
    out_name = Path(output).name.lower()
    dirs = [Path(mr) / n for n in order if n.lower() != out_name]
    # MO2's overwrite holds whatever BodySlide built through MO2.
    try:
        _ow = paths.overwrite_dir(lay)
    except Exception:
        _ow = None
    loose_dirs = ([Path(_ow)] if _ow is not None else []) + dirs
    dirs += [Path(d) for d in (lay.game_data_dirs or []) if Path(d) not in dirs]
    loose_dirs += [d for d in dirs if d not in loose_dirs]
    bsa = _BsaMeshIndex(dirs, None,
                        skip_bsa=("voice", " sound", "sounds", "- snd", "facegen"))
    seen: dict = {}
    index = _LooseMeshIndex(loose_dirs) if _loose_mesh_index_on() else None

    def _is_file(p: Path) -> bool:
        try:
            return p.is_file()
        except OSError:
            return False      # an unreadable folder costs itself, not the pass

    def _loose_first(rel: str) -> "int | None":
        """Position in `loose_dirs` of the first folder holding `rel` loose."""
        if index is not None:
            hit = index.first(rel)
            if hit is not index.ASK:
                return hit
        for i, d in enumerate(loose_dirs):
            if _is_file(d / "meshes" / rel):
                return i
        return None

    def exists(model: str) -> bool:
        rel = str(model or "").replace("\\", "/").lstrip("/").lower()
        if rel.startswith("meshes/"):
            rel = rel[7:]
        if not rel:
            return False
        if rel not in seen:
            seen[rel] = _loose_first(rel) is not None or bsa.contains(rel)
        return seen[rel]

    fit: dict = {}
    unfitted: dict = {}

    def _read_meshes(model: str, cache: dict, judge) -> "bool | None":
        """`judge` of the copy of `model` the game loads (the first loose file
        in priority order, else the archive that lists it), read into memory,
        never extracted; cached in `cache` by path. None when it cannot be
        found or read -- the caller fails closed."""
        rel = str(model or "").replace("\\", "/").lstrip("/").lower()
        if rel.startswith("meshes/"):
            rel = rel[7:]
        if not rel:
            return None
        if rel not in cache:
            data = None
            i = _loose_first(rel)
            if i is not None:
                try:
                    data = (loose_dirs[i] / "meshes" / rel).read_bytes()
                except OSError:
                    data = None
            if data is None:
                data = bsa.read_bytes(rel)
            cache[rel] = None if data is None else judge(data)
        return cache[rel]

    def body_fit(model: str) -> "bool | None":
        """#coverage-female-standin: is that copy of `model` skinned to a
        body-fit bone?"""
        return _read_meshes(model, fit, _nif_bytes_body_fit)

    def unfitted_skin(model: str) -> "bool | None":
        """#coverage-body-cloak: is that copy of `model` skinned, with no skin
        bound to a body-fit bone (a cape draped from the spine)?"""
        return _read_meshes(model, unfitted, _nif_bytes_unfitted_skin)
    exists.body_fit = body_fit
    exists.unfitted_skin = unfitted_skin
    return exists


def _dead_armature_lookup(output, *built) -> "callable[[str], bool] | None":
    r"""#coverage-dead-armature: the lookup both coverage passes judge a dead
    armature with -- `_mesh_exists_anywhere(output)`. The first of `built`
    that is not None is that same lookup already built for another rule (the
    female guard's, the world-mesh one's), so the modlist is listed once; one
    is built when none is. None when the rule is off, or when the modlist
    cannot be read: then every armature is minted, as before, and a warning
    says so."""
    if not ube_patcher._coverage_dead_armature():
        return None
    look = next((b for b in built if b is not None), None)
    if look is None:
        look = _mesh_exists_anywhere(output)
    if look is None:
        warn("[unified] could not list the meshes the modlist has, so armour "
             "whose meshes exist nowhere could not be told apart",
             consequence="every armature is given a UBE armature, as before, "
                         "including ones that draw nothing")
    return look


# The game's own archive lists when the profile's Skyrim.ini names none: the
# Skyrim SE defaults, read from a stock profile INI.
_DEFAULT_RESOURCE_ARCHIVES = (
    "Skyrim - Misc.bsa", "Skyrim - Shaders.bsa", "Skyrim - Interface.bsa",
    "Skyrim - Animations.bsa", "Skyrim - Meshes0.bsa", "Skyrim - Meshes1.bsa",
    "Skyrim - Sounds.bsa", "Skyrim - Voices_en0.bsa", "Skyrim - Textures0.bsa",
    "Skyrim - Textures1.bsa", "Skyrim - Textures2.bsa", "Skyrim - Textures3.bsa",
    "Skyrim - Textures4.bsa", "Skyrim - Textures5.bsa", "Skyrim - Textures6.bsa",
    "Skyrim - Textures7.bsa", "Skyrim - Textures8.bsa", "Skyrim - Patch.bsa")


def _profile_resource_archives(lay) -> "list[str]":
    """The archives the game's INI tells it to load (`sResourceArchiveList` and
    `...List2` under [Archive] of the MO2 profile's Skyrim.ini), else the SE
    defaults. Lowercased names."""
    import configparser
    names: "list[str]" = []
    try:
        ini_path = (Path(lay.instance_dir) / "profiles" / lay.selected_profile
                    / "Skyrim.ini")
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        cp.read(ini_path, encoding="utf-8-sig")   # option names come back lower-case
        for k in ("sresourcearchivelist", "sresourcearchivelist2"):
            if cp.has_option("Archive", k):
                names += [x.strip() for x in cp.get("Archive", k).split(",")
                          if x.strip()]
    except Exception:
        names = []
    return [n.lower() for n in (names or _DEFAULT_RESOURCE_ARCHIVES)]


def _game_view_mesh_resolver(output=None) -> "callable[[str], bool] | None":
    r"""#coverage-third-party-drawn: is a mesh LIVE in the game view -- what the
    game loads once this run is done? A loose file in MO2's overwrite, an
    ENABLED mod (our own output folder included: the game loads it too), the
    game Data folder, or a file in an archive the game LOADS: one the profile's
    Skyrim.ini lists, or `<plugin>.bsa` / `<plugin> - Textures.bsa` of an
    ACTIVE plugin, found at the root of overwrite, an enabled mod (MO2 priority)
    or Data. An archive no active plugin loads does not count, and nor does a
    disabled mod. Voice, sound and facegen archives are not read: they hold no
    armour.

    Not `_mesh_exists_anywhere`: that one leaves our output out and reads every
    archive at a mod's root, loaded or not -- the right question for a female
    slot's source mesh, the wrong one for "does this armature draw anything".
    Both lookups are lazy: a per-path probe of the loose folders, and one table
    scan of the loaded archives on the first path not found loose. None when
    the modlist cannot be read."""
    try:
        lay = paths.discover_layout()
        mr = paths.mods_root()
        order = paths.enabled_mods_ordered(lay)
        plugins = paths.active_plugins_ordered(lay) or []
    except Exception:
        return None
    if mr is None or not order:
        return None
    try:
        _ow = paths.overwrite_dir(lay)
    except Exception:
        _ow = None
    roots: "list[Path]" = [Path(_ow)] if _ow is not None else []
    roots += [Path(mr) / n for n in order]
    if output is not None and all(str(Path(output)).lower() != str(r).lower()
                                  for r in roots):
        roots.append(Path(output))
    roots += [Path(d) for d in (lay.game_data_dirs or [])]
    seen: dict = {}
    archived: "list[set[str] | None]" = [None]

    def _is_file(p: Path) -> bool:
        try:
            return p.is_file()
        except OSError:
            return False      # an unreadable folder costs itself, not the pass

    def _archive_meshes() -> "set[str]":
        from .bsa_strings import BSAArchive
        want = dict.fromkeys(_profile_resource_archives(lay))
        for n in plugins:
            stem = str(n).rsplit(".", 1)[0].lower()
            want[stem + ".bsa"] = None
            want[stem + " - textures.bsa"] = None
        skip = ("voice", " sound", "sounds", "- snd", "facegen")
        found: dict = {}          # archive name -> the copy the game loads
        for r in roots:
            try:
                ents = os.listdir(r)
            except OSError:
                continue
            for e in ents:
                el = e.lower()
                if el in want and el not in found and not any(k in el for k in skip):
                    found[el] = r / e
        out: "set[str]" = set()
        for f in found.values():
            try:
                names = BSAArchive(f, eager=False).list_files("meshes/")
            except Exception:
                continue          # an unreadable archive costs itself only
            out.update(n[len("meshes/"):] for n in names if n.endswith(".nif"))
        return out

    def live(model: str) -> bool:
        rel = str(model or "").replace("\\", "/").strip().lstrip("/").lower()
        if rel.startswith("meshes/"):
            rel = rel[7:]
        if not rel:
            return False
        if rel not in seen:
            hit = any(_is_file(r / "meshes" / rel) for r in roots)
            if not hit:
                if archived[0] is None:
                    try:
                        archived[0] = _archive_meshes()
                    except Exception:
                        archived[0] = set()
                hit = rel in archived[0]
            seen[rel] = hit
        return seen[rel]
    return live


def _mod_name_excluded(name: str, wanted: "set[str]") -> bool:
    """Is this mod folder one of the run's exclusions? The folder name, or --
    for a name with a comma, which the CLI splits -- every piece of it. Same
    matching as `_armos_defined_by_mods`."""
    n = name.lower()
    if n in wanted:
        return True
    parts = {p.strip() for p in n.split(",") if p.strip()}
    return len(parts) > 1 and parts <= wanted


def _third_party_ube_twin_lookup(output, exclude_mods=()) \
        -> "callable[[str], str | None] | None":
    r"""#coverage-ube-twin: which THIRD-PARTY mod ships a loose
    `meshes\!UBE\<path>` -- the hand-made UBE version of a source mesh the
    converter did not convert? Returns that mod's folder name (MO2's highest
    priority first) or None; None as a whole when the modlist cannot be read.

    Third party: an enabled mod that is neither our output (its folder name, or
    `_is_our_own_output` for an old output under any name) nor one the run
    excludes -- an excluded mod is one the user took out of this tool's hands.
    Loose files only, and not MO2's overwrite: a hand-made patch ships in its
    own folder, and a stray converter output in overwrite must never read as
    one. Measured on the live pack: 579 such meshes in a handful of mods, so the
    index is built once, on the first question, over those mods' `!UBE` folders
    alone."""
    try:
        lay = paths.discover_layout()
        mr = paths.mods_root()
        order = paths.enabled_mods_ordered(lay)
    except Exception:
        return None
    if mr is None or not order:
        return None
    out_name = Path(output).name.lower()
    wanted = {str(n).strip().lower() for n in (exclude_mods or ()) if str(n).strip()}
    index: "dict[str, str] | None" = None

    def _build() -> "dict[str, str]":
        idx: dict = {}
        for name in order:              # highest priority first: first one wins
            if name.lower() == out_name or _mod_name_excluded(name, wanted):
                continue
            ube = Path(mr) / name / "meshes" / "!UBE"
            try:
                if not ube.is_dir() or _is_our_own_output(Path(mr) / name):
                    continue
                files = list(ube.rglob("*.nif"))
            except OSError:
                continue                # an unreadable folder costs itself only
            for f in files:
                idx.setdefault(f.relative_to(ube).as_posix().lower(), name)
        return idx

    def twin(model: str, as_written: bool = False) -> "str | None":
        # `model` is a SOURCE model path: `meshes\X` is read by the engine as
        # `X`, so its twin is `meshes\!UBE\X`. `as_written` asks for the path
        # after `!UBE\` exactly as a minted slot spells it -- the postflight
        # judges `!UBE\meshes\X` as `meshes\!UBE\meshes\X`. #twin-path-strip-meshes
        nonlocal index
        rel = str(model or "").replace("\\", "/").lstrip("/").lower()
        if rel.startswith("meshes/") and not as_written:
            rel = rel[7:]
        if not rel:
            return None
        if index is None:
            index = _build()
        return index.get(rel)
    return twin


def _built_ube_twins(resolved_pairs, built_ube_twin) -> "dict[str, str]":
    r"""#skip-built-ube-path: {rel: mod} for the planned meshes another mod
    already ships BUILT at `meshes\!UBE\<rel>` (`built_ube_twin` is
    `_third_party_ube_twin_lookup`, the same lookup coverage points at them
    with). Judged per weight base: a base is left to that mod only when EVERY
    variant planned for it has a built twin -- their `_1` beside our `_0` would
    pair two different meshes. No lookup (switched off, no modlist) -> {}."""
    if built_ube_twin is None:
        return {}
    by_base: dict = {}
    for _src, rel in resolved_pairs:
        by_base.setdefault(_weight_base_key(rel), []).append(rel)
    out: dict = {}
    for rels in by_base.values():
        who = [built_ube_twin(r) for r in rels]
        if all(who):
            out.update(zip(rels, who))
    return out


def _supersede_built_ube_outputs(output_dir, nif_dst_root, rels,
                                 failed=None) -> int:
    r"""#skip-built-ube-path: move an earlier run's copy of each mesh left to its
    builder -- with its base's `.tri` morphs and `.xml` physics -- out of
    `meshes\` to `_superseded\meshes\!UBE\...` in the output mod. The output
    folder is never cleaned and our mod sits above the builder in MO2, so a
    stale copy would still win the path in game, and a stale `.tri` beside the
    builder's NIF would morph the wrong vertices. Moved, not deleted: the folder
    is inert to the game and undoing it is a copy back. Returns files moved.

    #supersede-whole-base: the whole base moves or none of it -- see
    `_supersede_whole_bases`; `failed` collects the bases that stayed.
    Switched off, only the planned variants move and a failure is silent."""
    if _supersede_whole_base():
        return _supersede_whole_bases(output_dir, nif_dst_root, rels, failed)
    root = Path(output_dir)
    dest_root = root / "_superseded"
    moved = 0
    seen: set = set()
    for rel in rels:
        dst = Path(nif_dst_root) / Path(rel)
        stem = dst.stem
        if stem.endswith(("_0", "_1")):
            stem = stem[:-2]
        for f in (dst, dst.with_name(stem + ".tri"), dst.with_name(stem + ".xml")):
            key = str(f).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                if not f.is_file():
                    continue
                to = dest_root / f.relative_to(root)
                to.parent.mkdir(parents=True, exist_ok=True)
                os.replace(f, to)
                moved += 1
            except (OSError, ValueError):
                continue    # a file that cannot move stays, unreported
    return moved


def _supersede_whole_base() -> bool:
    r"""#supersede-whole-base (2026-09-24): does superseding a base move EVERY
    weight variant of it in our output (not only the ones this run planned),
    all or nothing, and keep the partner fill out of it? Yes, by default.

    A source that ships only `_1` plans only `x_1`, so the old move left the
    `x_0` an earlier run's partner fill had written in `meshes\`; after the
    batch the fill copied that stale `x_0` back to `x_1` -- the builder's path,
    beating the hand-made mesh again on every run. A move that failed (a file in
    use) was silent and left half a base behind, which the fill then completed.
    CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE=1 moves only the planned variants again."""
    return not _flag("CBBE2UBE_NO_SUPERSEDE_WHOLE_BASE", False)


def _supersede_whole_bases(output_dir, nif_dst_root, rels, failed=None) -> int:
    r"""#supersede-whole-base: move each weight base `rels` names out of
    `meshes\` WHOLE -- the planned files, the `_0`/`_1` partner of a weighted
    one (a copy the partner fill made is one), and the base's `.tri` and `.xml`.

    All or nothing per base: when one file cannot move, the ones already moved
    go back, and the base is appended to `failed` as (base rel, error, [files
    that could not be put back]). A whole stale base is our old conversion with
    its own morphs -- still the wrong mesh, but a consistent one the next run
    moves; half a base pairs our NIF with the builder's `.tri` (it morphs the
    wrong vertices) and the fill completes it. Returns files moved."""
    root = Path(output_dir)
    dest_root = root / "_superseded"
    groups: dict = {}       # (folder, stem) -> [files, planned first]
    for rel in rels:
        dst = Path(nif_dst_root) / Path(rel)
        stem = dst.stem
        weighted = stem.endswith(("_0", "_1"))
        if weighted:
            stem = stem[:-2]
        key = (str(dst.parent).lower(), stem.lower())
        g = groups.setdefault(key, {"rel": rel, "files": []})
        cand = [dst]
        if weighted:
            cand += [dst.with_name(stem + "_0.nif"), dst.with_name(stem + "_1.nif")]
        cand += [dst.with_name(stem + ".tri"), dst.with_name(stem + ".xml")]
        for f in cand:
            if all(str(f).lower() != str(h).lower() for h in g["files"]):
                g["files"].append(f)
    moved = 0
    for g in groups.values():
        done: list = []
        try:
            for f in g["files"]:
                if not f.is_file():
                    continue
                to = dest_root / f.relative_to(root)
                to.parent.mkdir(parents=True, exist_ok=True)
                os.replace(f, to)
                done.append((f, to))
        except (OSError, ValueError) as e:
            torn = []
            for f, to in reversed(done):
                try:
                    os.replace(to, f)
                except OSError:
                    torn.append(f.name)
            if failed is not None:
                failed.append((_weight_base_key(g["rel"]),
                               plain_error(e), sorted(torn)))
            continue
        moved += len(done)
    return moved


def _outside_ube_mesh_resolver(output) -> "callable[[str], bool] | None":
    r"""The post-merge validator's `mesh_resolves`: does a `!UBE\` path the
    coverage step pointed OUTSIDE our output load from another mod? Only the two
    kinds it writes on purpose -- the UBE body's own hands/feet
    (#coverage-nude-skin, resolved like `_mesh_exists_anywhere`) and, while the
    twin rule is on (the default), a hand-made twin (#coverage-ube-twin). Anything else under `!UBE\` that our
    output lacks is still a missing mesh. The question is only "does it load",
    so an excluded mod's copy counts here. None when both are off; the lookups
    are built on the first question, which a clean Combined never asks."""
    nude = ube_patcher._coverage_nude_skin()
    twin_on = ube_patcher._coverage_ube_twin()
    if not (nude or twin_on):
        return None
    strip = ube_patcher._twin_path_strip_meshes()
    built: dict = {}

    def resolves(path: str) -> bool:
        p = str(path or "")
        if nude and ube_patcher.is_ube_body_part_path(p):
            if "any" not in built:
                built["any"] = _mesh_exists_anywhere(output)
            return bool(built["any"] and built["any"](p))
        if twin_on and p[:5].lower() == "!ube\\":
            if "twin" not in built:
                built["twin"] = _third_party_ube_twin_lookup(output)
            rest = p[5:]
            if strip and ube_patcher._strip_meshes_prefix(rest) != rest:
                # Judged as written: `!UBE\meshes\X` loads meshes\!UBE\meshes\X,
                # not the twin at meshes\!UBE\X. #twin-path-strip-meshes
                return bool(built["twin"] and built["twin"](rest, as_written=True))
            return bool(built["twin"] and built["twin"](p[5:]))
        return False
    return resolves


def _report_coverage_holds(stats: "list[dict]") -> None:
    """Say what the two coverage passes held back, in counts and a few names:
    armour of an excluded mod left without an armature (#exclude-owned-coverage),
    left to another mod that patches it, or still drawn as a non-body piece
    with no mesh converted for that mod (#exclude-body-only),
    female slots that did not take a converted MALE mesh
    (#coverage-female-guard), body armatures whose world mesh was not converted
    (#coverage-world-mesh), nude hands/feet swapped for the UBE body's own or
    left out (#coverage-nude-skin), slots pointed at a hand-made UBE twin
    (#coverage-ube-twin), hoods drawn with their body armour
    (#coverage-body-accessory), armour drawn through an armature whose
    primary race is not DefaultRace (#coverage-human-race-list), and
    armatures whose meshes exist nowhere (#coverage-dead-armature). Silent
    when there is nothing to say."""
    withheld = [w for s in stats for w in (s.get("withheld") or [])]
    excl_kept = [w for s in stats for w in (s.get("exclusion_nonbody_kept") or [])]
    kept = [k for s in stats for k in (s.get("female_kept") or [])]
    dead = [k for s in stats for k in (s.get("female_dead_male") or [])]
    # #coverage-female-standin: dead female slots given the vanilla female
    # counterpart, a non-body piece's own male mesh, or left dead.
    standin = [k for s in stats for k in (s.get("female_standin") or [])]
    as_is = [k for s in stats for k in (s.get("female_male_nonbody") or [])]
    dead_kept = [k for s in stats for k in (s.get("female_dead_kept") or [])]
    skipped = [k for s in stats for k in (s.get("female_guard_skipped") or [])]
    dropped = [d for s in stats for d in (s.get("female_guard_dropped") or [])]
    wskip = [k for s in stats for k in (s.get("world_mesh_skipped") or [])]
    # Adults named first: children's clothing (skipped on purpose) filled every
    # named line live and hid the adult outfits. A child piece is what source
    # selection already calls one, by name. #world-mesh-partial-report
    wdrop = sorted((d for s in stats for d in (s.get("world_mesh_dropped") or [])),
                   key=lambda d: _is_child_content_asset(d[1]))
    # Still drawn, but with no body piece: the hands/feet armature was minted.
    wpart = sorted((d for s in stats for d in (s.get("world_mesh_partial") or [])),
                   key=lambda d: _is_child_content_asset(d[1]))
    nred = [k for s in stats for k in (s.get("nude_redirected") or [])]
    nskip = [k for s in stats for k in (s.get("nude_skipped") or [])]
    ndrop = [d for s in stats for d in (s.get("nude_dropped") or [])]
    twins = [k for s in stats for k in (s.get("ube_twin") or [])]
    accs = [k for s in stats for k in (s.get("body_accessory") or [])]
    beasts = sorted({k for s in stats for k in (s.get("beast_variant_skipped") or [])})
    nonactor = sorted({k for s in stats for k in (s.get("beast_variant_non_actor") or [])})
    wigs = [w for s in stats for w in (s.get("wigs") or [])]
    listed = [k for s in stats for k in (s.get("race_listed") or [])]
    # #coverage-third-party-drawn
    tp_drawn = [k for s in stats for k in (s.get("third_party_drawn") or [])]
    tp_part = [k for s in stats for k in (s.get("third_party_partial") or [])]
    tp_kept = [k for s in stats
               for k in (s.get("third_party_kept_first_person") or [])]
    # #exclude-body-only: a piece held because another mod patches it (adds
    # armatures to it) is left to that mod's patch -- named on its own line,
    # not among the pieces with no UBE armature from any mod. Body pieces too:
    # the body pass records the ones a SkyPatcher patch names.
    left_to: dict = {}
    for s in stats:
        for armo_abs, edid, why in ((s.get("exclusion_nonbody_held") or [])
                                    + (s.get("exclusion_body_held") or [])):
            mod = ube_patcher._held_for_another_patch(why)
            if mod is not None:
                left_to[tuple(armo_abs)] = (edid, mod)
    withheld = [w for w in withheld if tuple(w[0]) not in left_to]
    if withheld:
        warn(f"[unified] {len(withheld)} armour(s) of an excluded mod have no UBE "
             "armature from any mod",
             where="--exclude-mods",
             consequence="they are not drawn on UBE-race actors",
             fix="take the mod off the exclusion list to have them covered, or "
                 "install a UBE patch for it")
        for (pl, fid), edid in withheld[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(withheld) > 5:
            print(f"       ... and {len(withheld) - 5} more")
    if left_to:
        print(f"  [unified] {len(left_to)} armour(s) of an excluded mod are left to "
              "the other mod that patches them (it adds armatures to them; this "
              "tool does not check that those draw on UBE-race actors)")
        for ((pl, fid), (edid, mod)) in list(left_to.items())[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})  patched by {mod}")
        if len(left_to) > 5:
            print(f"       ... and {len(left_to) - 5} more")
    if excl_kept:
        # #exclude-body-only: information -- the user's rule, working as meant.
        # A kept piece draws the model its armature names: never a converted
        # copy of the excluded mod's own mesh, but a shared path another mod's
        # conversion covers does draw that converted copy.
        warn(f"[unified] {len(excl_kept)} non-body armour(s) of an excluded mod "
             "are still drawn on UBE-race actors, with no mesh converted for "
             "that mod",
             where="--exclude-mods",
             consequence="no other mod patches them and they are not body pieces, "
                         "so each draws the mesh its armature names (converted "
                         "only where another mod's conversion shares the path)",
             level=NOTE)
        for (pl, fid), edid in excl_kept[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(excl_kept) > 5:
            print(f"       ... and {len(excl_kept) - 5} more")
    if kept or skipped:
        warn(f"[unified] {len(kept)} female model slot(s) kept their own unconverted "
             f"mesh, and {len(skipped)} body armature(s) were not minted "
             f"({len(dropped)} armour(s) left without one), rather than take a "
             "converted MALE mesh",
             consequence="those pieces wear their unconverted mesh on UBE, or are "
                         "not drawn on UBE-race actors, until their female mesh "
                         "is converted",
             fix="convert the mod that ships the female mesh")
        for k in kept[:5]:
            print(f"       {k['slot']} {k['kept']}  (not {k['male']})")
        if len(kept) > 5:
            print(f"       ... and {len(kept) - 5} more")
        for (pl, fid), edid in dropped[:5]:
            print(f"       not covered: {edid or '?'}  ({pl}|{fid:06X})")
    if dead:
        # A NOTE: this is the behaviour from before the guard, kept on purpose.
        warn(f"[unified] {len(dead)} female model slot(s) name a mesh that exists "
             "nowhere, so they keep the converted MALE mesh",
             consequence="the piece is drawn with the male mesh on UBE; with its "
                         "own path it would not be drawn at all",
             level=NOTE)
        for k in dead[:5]:
            print(f"       {k['slot']} {k['dead']}  (-> {k['male']})")
        if len(dead) > 5:
            print(f"       ... and {len(dead) - 5} more")
    if standin:
        print(f"  [unified] {len(standin)} female model slot(s) name a mesh that "
              "exists nowhere and draw the vanilla female counterpart of their "
              "male mesh instead")
        for k in standin[:5]:
            print(f"       {k['slot']} {k['orig']}  (-> {k['standin']})")
        if len(standin) > 5:
            print(f"       ... and {len(standin) - 5} more")
    if as_is:
        print(f"  [unified] {len(as_is)} female model slot(s) of non-body pieces "
              "name a mesh that exists nowhere and draw their own male mesh")
        for k in as_is[:5]:
            print(f"       {k['slot']} {k['orig']}  (-> {k['male_as_is']})")
        if len(as_is) > 5:
            print(f"       ... and {len(as_is) - 5} more")
    if dead_kept:
        warn(f"[unified] {len(dead_kept)} female model slot(s) name a mesh that "
             "exists nowhere and have nothing to draw instead",
             consequence="those pieces are not drawn on UBE-race actors, as on "
                         "any female actor",
             level=NOTE)
        # One group per reason the male mesh was not drawn instead (the pass
        # tags each slot, `_dead_kept_why`), each with a few slots; a group's
        # "more" is what that group did not print. #coverage-female-standin
        live = [k for k in dead_kept if k.get("male_live")]
        for group, what in (
                ([k for k in dead_kept if not k.get("male_live")],
                 "have no male mesh either"),
                ([k for k in live if k.get("why") == "body"],
                 "are body pieces whose male mesh was not converted"),
                ([k for k in live if k.get("why") == "cloak"],
                 "are capes or cloaks whose male mesh was not converted"),
                ([k for k in live if k.get("why") not in ("body", "cloak")],
                 "have a male mesh that was not converted and could not be read "
                 "or judged")):
            if not group:
                continue
            print(f"       {len(group)} {what}:")
            for k in group[:3]:
                print(f"         {k['slot']} {k['dead_kept']}  ({k['arma']})")
            if len(group) > 3:
                print(f"         ... and {len(group) - 3} more")
    if wskip:
        warn(f"[unified] {len(wskip)} body armature(s) were not minted because their "
             f"female world mesh was not converted ({len(wdrop)} armour(s) left "
             f"without one, {len(wpart)} drawn without the body piece)",
             consequence="those body pieces are not drawn on UBE-race actors, rather "
                         "than draw their unconverted CBBE mesh on the UBE body",
             fix="convert the mod that ships the female mesh")
        for (pl, fid), edid in wdrop[:5]:
            print(f"       not covered: {edid or '?'}  ({pl}|{fid:06X})")
        if len(wdrop) > 5:
            print(f"       ... and {len(wdrop) - 5} more")
        for (pl, fid), edid in wpart[:5]:
            print(f"       no body piece: {edid or '?'}  ({pl}|{fid:06X})")
        if len(wpart) > 5:
            print(f"       ... and {len(wpart) - 5} more")
    if nred:
        print(f"  [unified] {len(nred)} hand/foot armature(s) that drew the nude CBBE "
              "hands or feet now draw the UBE body's own")
    # Armours are counted per reason (a dropped entry is (armo, edid, why)):
    # one count for both overstated the skins by every unresolved item.
    unres = [k for k in nskip if k.get("why") == "unresolved"]
    unres_drop = [d for d in ndrop if d[2] == "unresolved"]
    if unres:
        warn(f"[unified] {len(unres)} hand/foot armature(s) draw the nude CBBE hands "
             "or feet, and the UBE body's own hands/feet were not found, so they were "
             f"not minted ({len(unres_drop)} armour(s) left without one)",
             consequence="those pieces are not drawn on UBE-race actors",
             fix="build the UBE body's hands and feet in BodySlide")
        for k in unres[:5]:
            print(f"       {k['arma']}")
        for (pl, fid), edid, _why in unres_drop[:5]:
            print(f"       not covered: {edid or '?'}  ({pl}|{fid:06X})")
    skins = [k for k in nskip if k.get("why") == "skin"]
    skin_drop = [d for d in ndrop if d[2] == "skin"]
    if skins or skin_drop:
        # A skin's armature minted for a costume item is not in `skins`, but the
        # skin itself is still left without one -- so either count prints.
        print(f"  [unified] {len(skins)} nude hand/foot armature(s) of a race skin "
              f"were not minted ({len(skin_drop)} armour(s) left without one): a UBE "
              "actor wears UBE's own skin")
    if twins:
        print(f"  [unified] {len(twins)} model slot(s) point at a hand-made UBE mesh "
              "another mod ships")
        for k in twins[:5]:
            print(f"       {k['slot']} {k['path']}  ({k.get('mod') or '?'})")
        if len(twins) > 5:
            print(f"       ... and {len(twins) - 5} more")
    if accs:
        print(f"  [unified] {len(accs)} hood/accessory armature(s) of body armour "
              "drawn on UBE with the body (their own mesh)")
    # #coverage-body-cloak: the capes among them, which conversion skipped.
    capes = [k for s in stats for k in (s.get("body_cloak") or [])]
    if capes:
        print(f"  [unified]   {len(capes)} of them a cape draped from the spine "
              "(no body-fit bones, so it was not converted)")
    if beasts:
        print(f"  [unified] {len(beasts)} beast-race variant armature(s) left off UBE "
              "actors (they list only Argonian/Khajiit races; no human draws them)")
        # #beast-variant-non-actor: say when the mannequin race was ignored.
        # Mannequins are actors that wear armour; what the race lacks is a
        # playable or UBE-race member, so it cannot make a human draw these.
        if nonactor:
            print(f"       {len(nonactor)} of them also "
                  f"{'lists' if len(nonactor) == 1 else 'list'} the mannequin race "
                  "(Skyrim.esm ManikinRace), ignored when judging: no playable or "
                  "UBE-race actor has it (mannequins still display the item)")
    # #coverage-race-subset
    by_race = {tuple(a) for s in stats for a in (s.get("race_subset") or [])}
    other_race = sorted({k for s in stats for k in (s.get("race_subset_dropped") or [])})
    if by_race:
        print(f"  [unified] {len(by_race)} armour(s) with a separate armature per race "
              "(one for humans, one for Orcs, ...): each drawn on UBE only for the "
              "UBE versions of the races it lists")
        if other_race:
            print(f"       {len(other_race)} armature(s) made only for other races "
                  "(a mod's own race), or for none its siblings leave, left off UBE "
                  "actors: no human draws them")
    if wigs:
        print(f"  [unified] {len(wigs)} playable wig(s) drawn on UBE as headgear "
              "(their own mesh and collider)")
        for (pl, fid), edid in wigs[:3]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(wigs) > 3:
            print(f"       ... and {len(wigs) - 3} more")
    if listed:
        print(f"  [unified] {len(listed)} armour(s) whose only human-drawing "
              "armature has another primary race are now drawn on UBE (race "
              "list mapped)")
        for (pl, fid), edid in listed[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(listed) > 5:
            print(f"       ... and {len(listed) - 5} more")
    if tp_drawn:
        print(f"  [unified] {len(tp_drawn)} armour(s) already drawn on UBE by another "
              "mod's armature -- ours not minted")
        for (pl, fid), edid in tp_drawn[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(tp_drawn) > 5:
            print(f"       ... and {len(tp_drawn) - 5} more")
    if tp_part:
        print(f"  [unified] {len(tp_part)} armour(s) partly drawn on UBE by another "
              "mod's armature -- ours minted only for the pieces or races it leaves out")
        for (pl, fid), edid in tp_part[:5]:
            print(f"       {edid or '?'}  ({pl}|{fid:06X})")
        if len(tp_part) > 5:
            print(f"       ... and {len(tp_part) - 5} more")
    # #coverage-dead-armature: distinct armatures (one can serve both passes),
    # counted by the plugin that defines them.
    dead_arm = sorted({k for s in stats for k in (s.get("dead_armature_skipped") or [])})
    dead_drop = [d for s in stats for d in (s.get("dead_dropped") or [])]
    if dead_arm:
        warn(f"[unified] {len(dead_arm)} armature(s) name only meshes that exist "
             "nowhere (drawn by nobody, the source included) -- not minted "
             f"({len(dead_drop)} armour(s) left without one)",
             consequence="those pieces draw nothing on any actor, UBE or not; "
                         "installing the mod that ships their meshes and running "
                         "again covers them",
             level=NOTE)
        per: dict = {}
        for k in dead_arm:
            pl = k.rsplit("|", 1)[0]
            per[pl] = per.get(pl, 0) + 1
        for pl, n in sorted(per.items(), key=lambda t: (-t[1], t[0]))[:8]:
            print(f"       {n:>4}  {pl}")
        if len(per) > 8:
            print(f"       ... and {len(per) - 8} more plugin(s)")
    for (pl, fid), edid, arma in tp_kept:
        # #coverage-keep-better-first-person: one line each (2 on a real order).
        print(f"  [unified] note: {edid or '?'} ({pl}|{fid:06X}) keeps our armature "
              f"{arma} beside another mod's UBE one -- theirs has no converted "
              "first-person mesh, ours does")


def _emit_unified_coverage_patches(output, patches_dir, master_data_dirs,
                                   merged_name,
                                   exclude_mods=()) -> "tuple[bool, int, bool]":
    r"""Step 3b: run the winner-scan coverage passes as the PRIMARY generator and
    drop their patch ESPs + `.skypatcher.json` sidecars into the patches dir, so
    the auto-merge folds them straight into the Combined family (the merge dedups
    links by (armo, src) and collapses byte-identical ARMAs). This makes the
    separate ModBody/ModNonBody plugins unnecessary.

    Returns (ok, total_armo_targets, body_ran). ok is True if the passes that ran
    completed without error. The caller must use the coverage-only merge ONLY
    when ok AND total_targets > 0 -- otherwise fall back to merging the per-source
    patches, so a failed/empty winner scan can never yield an empty or partial
    Combined with the per-source coverage silently dropped. Best-effort: any
    failure is logged and reported via ok=False.

    body_ran is False when the body/hands-feet pass was SKIPPED for want of
    converted `!UBE\` meshes. That is legitimate on a fresh output, but it makes
    the coverage PARTIAL -- it carries no body links at all -- so the caller must
    not then treat it as the sole generator and discard the per-source patches,
    which in that state are the only thing carrying body coverage.

    `exclude_mods`: the run's --exclude-mods. Armour those mods define gets no
    armature from either pass (#exclude-owned-coverage, `_armos_defined_by_mods`);
    CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE=1 covers it again, as before. They are
    never a source of a hand-made UBE twin either (#coverage-ube-twin)."""
    total_targets = 0
    try:
        # Leave armors alone that ANOTHER mod already patched for UBE --
        # adding a second armature renders two bodies, and a hand-made UBE
        # patch beats an automatic conversion anyway. Coexist, do not compete.
        # #coverage-third-party-drawn: the passes judge the armatures on each
        # winning armour record themselves, so only the SkyPatcher half is
        # asked for here -- what an INI adds is on no armour record. Tied to
        # the same switch, so no half-and-half combination can be selected.
        _tpd = ube_patcher._coverage_third_party_drawn()
        try:
            _uba_lay = paths.discover_layout()
            _ube_excl = _third_party_ube_covered_armos(
                paths.mods_root(),
                enabled_names=paths.enabled_mods(_uba_lay),
                skip_mods={Path(output).name},
                halves=("ini",) if _tpd else ("ini", "esp"),
                # #third-party-ini-slot-check: an INI addon counts only
                # when its plugin is loaded
                active_plugins=paths.active_plugins_ordered(_uba_lay))
        except Exception as _e:
            # Detection failure must never stop coverage -- but say so, or a
            # silently-empty exclusion set looks exactly like "nothing to skip".
            _ube_excl = set()
            warn(f"[unified] could not scan for existing UBE patches ({plain_error(_e)})",
                 consequence="not excluding any")
        if _ube_excl:
            print(f"  [unified] {len(_ube_excl)} armor(s) already have a UBE "
                  "patch from another mod -- leaving those alone")
        # Remove any STALE coverage from a prior run BEFORE regenerating: the
        # standalone ESP+INI (SkyPatcher applies every INI in the folder even if
        # the ESP is disabled -> double-cover) AND the prior coverage PATCHES in
        # the patches dir (a mid-run failure below must not leave a stale coverage
        # patch for the fallback merge to pick up).
        _outp = Path(output)
        for _stem in ("UBE_ModBody_Coverage", "UBE_ModNonBody_Coverage"):
            for _p in (_outp / f"{_stem}.esp",
                       _outp / f"{_stem}.esp.skypatcher.json",
                       _outp / "SKSE" / "Plugins" / "SkyPatcher" / "armor"
                       / f"{_stem}.ini"):
                try:
                    if _p.is_file():
                        _p.unlink()
                        print(f"  [unified] removed stale {_p.name}")
                except OSError:
                    pass
        # `*` after Coverage catches the numbered ESL pieces the coverage generators
        # now emit ("...Coverage2 UBE patch.esp"). Without it a stale piece from a
        # LARGER previous run survives and keeps delivering its old links, because
        # SkyPatcher applies every INI in the folder. #coverage-esl-chunks
        for _cp in patches_dir.glob("UBE_Mod*Coverage* UBE patch.esp*"):
            try:
                _cp.unlink()
            except OSError:
                pass
        lay = paths.discover_layout()
        names = paths.active_plugins_ordered(lay)
        fidx = paths.plugin_file_index(lay)
        ordered = [Path(fidx[n.lower()]) for n in (names or [])
                   if n.lower() in fidx]
        if not ordered:
            return (False, 0, False)    # (ok, targets, body_ran) -- the caller
                                        # unpacks three. #convert-needs-a-modlist
        excl = {"vanilla_ube_race_compat.esp",
                "ube_modbody_coverage ube patch.esp",
                "ube_modnonbody_coverage ube patch.esp"}
        excl |= _combined_output_names(merged_name, ordered)
        print("\n--- unified coverage: winner-scan patches -> merge "
              "(folding into Combined) ---")
        # #exclude-owned-coverage: armour an excluded mod defines is left alone.
        _withheld_abs: set = set()
        if exclude_mods and not _flag("CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE", False):
            try:
                _unfound: list = []
                _withheld_abs, _per_mod = _armos_defined_by_mods(
                    paths.mods_root(), exclude_mods, ordered, missing=_unfound)
                print(f"  [unified] --exclude-mods: {len(_withheld_abs)} armour(s) "
                      f"defined by {len(_per_mod)} excluded mod(s) get no "
                      "armature from this run")
                if _unfound:
                    warn(f"[unified] {len(_unfound)} excluded mod name(s) match no "
                         f"mod folder: {', '.join(_unfound[:5])}",
                         consequence="their armour is covered as if they were not "
                                     "excluded",
                         fix="use the mod's folder name exactly as MO2 shows it")
            except Exception as _e:
                _withheld_abs = set()
                warn(f"[unified] could not list the excluded mods' armour "
                     f"({plain_error(_e)})",
                     consequence="coverage may give an excluded mod's armour an "
                                 "armature")
        # Converted-mesh set FIRST: both coverage passes need it so a piece whose
        # OWN mesh was converted points at the !UBE\ mesh, not source. #mnb-converted-redirect
        ube_root = Path(output) / "meshes" / "!UBE"
        conv_rel = {n.relative_to(ube_root).as_posix().lower()
                    for n in ube_root.rglob("*.nif")} if ube_root.is_dir() else set()
        # #coverage-female-guard: which female paths are dead (exist nowhere).
        _fexists = (_mesh_exists_anywhere(output)
                    if ube_patcher._coverage_female_guard() else None)
        # #coverage-world-mesh (is an unconverted female path dead?) and
        # #coverage-nude-skin (do the UBE body's own hands/feet resolve?) ask the
        # same lookup; built once, and only when one of them is on.
        _mexists = None
        if ube_patcher._coverage_world_mesh() or ube_patcher._coverage_nude_skin():
            _mexists = (_fexists if ube_patcher._coverage_female_guard()
                        else _mesh_exists_anywhere(output))
        # #coverage-ube-twin: a hand-made UBE mesh a third-party mod ships where
        # we converted none -- never our output, never an excluded mod.
        _twin = (_third_party_ube_twin_lookup(output, exclude_mods)
                 if ube_patcher._coverage_ube_twin() else None)
        # #coverage-human-race-list: the armour female NPCs wear -- a
        # non-playable piece of it the race-list rule may take. Built once per
        # load order (an `auto` run's source selection already has). Asked
        # for_coverage: the rule's own switch is read on the line below, and
        # CBBE2UBE_NO_NPC_WORN_NONPLAYABLE turns off only the conversion.
        _worn = (_batch_npc_worn_armos(for_coverage=True)
                 if ube_patcher._coverage_human_race_list() else None)
        # #coverage-third-party-drawn: does another mod's UBE armature draw
        # anything? Its mesh must be live in the game view.
        _live = _game_view_mesh_resolver(output) if _tpd else None
        # #coverage-dead-armature: its own lookup, whatever the rules above use.
        _dead = _dead_armature_lookup(output, _fexists, _mexists)
        nb_out = patches_dir / "UBE_ModNonBody_Coverage UBE patch.esp"
        nb = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
            nb_out, ordered, converted_rel_paths=conv_rel,
            exclude_armo_abs=_ube_excl, exclude_names=excl,
            master_data_dirs=master_data_dirs, cover_all=True,
            preserve_textures=True, emit_sidecar=True,
            withheld_armo_abs=_withheld_abs, female_mesh_exists=_fexists,
            mesh_live=_live,
            dead_mesh_exists=_dead,
            ube_twin_exists=_twin, npc_worn_armo_abs=_worn)
        total_targets += int(nb.get("armo_targets") or 0)
        print(f"  non-body: minted {nb.get('minted_armas')} | "
              f"targets {nb.get('armo_targets')}")
        _print_coverage_warnings("non-body", nb)
        _held = [nb]
        if conv_rel:
            bd_out = patches_dir / "UBE_ModBody_Coverage UBE patch.esp"
            bd = ube_patcher.generate_modded_body_ube_coverage_patch(
                bd_out, ordered, converted_rel_paths=conv_rel,
                exclude_armo_abs=_ube_excl, exclude_names=excl,
                master_data_dirs=master_data_dirs,
                cover_all=True, cover_hands_feet=True, preserve_textures=True,
                emit_sidecar=True, withheld_armo_abs=_withheld_abs,
                female_mesh_exists=_fexists, mesh_exists=_mexists,
                mesh_live=_live,
                dead_mesh_exists=_dead,
                ube_twin_exists=_twin, npc_worn_armo_abs=_worn)
            total_targets += int(bd.get("armo_targets") or 0)
            print(f"  body+hands/feet: minted {bd.get('minted_armas')} | "
                  f"targets {bd.get('armo_targets')} | "
                  f"src-primary HF via preserved-race mint")
            _print_coverage_warnings("body", bd)
            _held.append(bd)
        _report_coverage_holds(_held)
        if not conv_rel:
            print("  body+hands/feet: SKIPPED -- no converted !UBE meshes "
                  "found, so this coverage carries NO body links")
        return (True, total_targets, bool(conv_rel))
    except Exception as e:
        warn(f"unified coverage emission failed: {plain_error(e)}",
             consequence="continuing with per-source coverage")
        return (False, total_targets, False)


def _sweep_orphan_temps_at_start(output, run_started: float) -> int:
    """Remove the temp files an interrupted earlier run left under `output`,
    print and record the count as a warning, and return it. #orphan-temps

    A hard kill mid-write leaves the writer's temp beside a destination that is
    still absent or previous-complete. The fixed `.nifsave.tmp` names are
    overwritten by the next run, but mkstemp's random ones pile up, and every
    output scan globs *.nif, so nothing saw them. Only temps older than this run
    are touched: a newer one can belong to a write in progress. Never raises."""
    from . import atomic_io
    try:
        removed = atomic_io.sweep_orphan_temps(output, run_started)
    except Exception as e:
        warn(f"could not sweep orphaned temp files: {plain_error(e)}",
             where=f"under {output}",
             consequence="partial files from an interrupted run may remain")
        return 0
    if removed:
        try:
            first = removed[0].relative_to(output).as_posix()
        except ValueError:
            first = removed[0].name
        warn(f"removed {len(removed)} orphaned temp file(s) that an interrupted run left",
             where=f"under {output} (e.g. {first})",
             consequence="those were partial writes a killed run never finished; "
                         "nothing of yours was touched")
        _record_failure("orphaned temp files removed", "output mod", str(output),
                        f"{len(removed)} temp file(s) from writes a killed run "
                        f"never finished, e.g. {first}", severity="warning")
    return len(removed)


def _cmd_convert(args):
    _RUN_FAILURES.clear()   # fresh failure record for this run
    _run_started = time.time()   # temps older than this are orphans. #orphan-temps
    # Same echo `auto` prints: the verdict harnesses run THIS subcommand, and
    # a run has to say what it was carrying before anything can abort.
    _echo_active_experiment_flags()
    # Export discovered layout to env so spawned workers inherit it without re-scanning.
    try:
        _layout = paths.discover_layout()
        paths.export_to_env(_layout)          # mods_root + game_data -> env
        if getattr(args, "mods_root", None):  # explicit CLI override wins
            os.environ[paths.MODS_ROOT_ENV] = str(args.mods_root)
        if paths.mods_root() is not None:
            print(f"  mods root: {paths.mods_root()}")
        if _layout.game_data_dirs:
            print(f"  game Data: {_layout.game_data_dirs[0]}")
    except Exception as _e:
        print(f"  (path auto-discovery note: {plain_error(_e)})")

    # NO MODLIST, NO RUN -- unless the caller supplied what the modlist would.
    # #convert-needs-a-modlist
    # `auto` has always refused to start without the MO2 mods folder; `convert`
    # carried on. Measured outside a modlist: the already-UBE scan died with a
    # TypeError, no UBE body reference was found, and the run either ended
    # "all clear" with exit 0 having converted nothing (empty source) or wrote
    # a real mod's meshes without the body reference and crashed at the
    # coverage step (exit 1, no report). --ube-body-ref still allows a
    # deliberate conversion of a loose folder.
    _mr = paths.mods_root()
    if ((_mr is None or not Path(_mr).is_dir())
            and not getattr(args, "ube_body_ref", None)):
        print("error: could not locate the MO2 mods folder. Run this from "
              "inside the modpack, set CBBE2UBE_MODS_ROOT, or pass "
              "--ube-body-ref to convert a folder outside a modlist.")
        return 2

    # Each returns whether its check came back clean. One that did not is
    # printed, recorded as a warning and counted in the tally. #run-warnings
    _skypatcher_ok = _warn_if_skypatcher_missing()
    _settings_malformed = _warn_if_settings_file_malformed()

    sources = list(args.sources)
    output = args.output
    if output is None:
        if len(sources) < 2:
            print("error: missing output dir. Use `-o OUTPUT SRC1 [SRC2 ...]` "
                  "or the legacy form `SRC OUTPUT`.")
            return 2
        # Legacy form: last positional is the output.
        output = sources[-1]
        sources = sources[:-1]
        # Heuristic safety: warn if both look like source mod dirs.
        if (output / "meshes").is_dir() or (output / "Meshes").is_dir():
            print(f"warning: --output not given; using last positional {output} "
                  f"as output dir (legacy `SRC OUTPUT` form). Pass `-o {output}` "
                  "explicitly to silence this.")

    _orphans_removed = _sweep_orphan_temps_at_start(output, _run_started)
    # Before any per-source patch is written or read (a full run, --only-mods
    # and --plugins-only all come through here). #source-patch-rename
    _rename_failures = _migrate_source_patch_names_at_start(
        output, getattr(args, "unmerged_patch_subdir", "_unmerged_patches"))

    if len(sources) > 1 and args.esp_name:
        print("warning: --esp-name is ignored when converting multiple "
              "sources (each source gets its own ESP name derived from "
              "its source ESP filename).")

    # THE MACHINE, ON EVERY PATH. #commit-headroom
    # Printed before the branch, not inside the pooled arm: the serial
    # (`--workers 1`) and `--plugins-only` paths build no pool, and those are
    # exactly the runs a user is told to try after a memory error -- so the run
    # they submit as evidence would have been the one log with no machine in
    # it. `--workers 1` still runs one in-process converter plus the GUI, which
    # is 2 processes, so the figures remain meaningful.
    _planned_workers = (args.workers if args.workers is not None
                        else default_worker_count())
    if getattr(args, "plugins_only", False) or (
            args.workers is not None and args.workers <= 1):
        for _ln in describe_memory_plan(_planned_workers):
            print(f"  {_ln}")

    # One shared pool for the whole batch: per-worker caches (pynifly, body OSD,
    # CBBE->UBE delta) persist across mods instead of being rebuilt per mod.
    if getattr(args, "plugins_only", False):
        shared_pool = None  # ESP-only refresh: no NIF work, don't spawn workers
        print("  --plugins-only: ESP refresh from espgen snapshots "
              "(no mesh work, no worker pool)")
    elif args.workers is not None and args.workers <= 1:
        shared_pool = None  # serial path; auto_convert_mod handles it
    else:
        pool_workers = args.workers
        _auto_workers = pool_workers is None
        if _auto_workers:
            pool_workers = default_worker_count()
        shared_pool = _NifPool(pool_workers, args.ube_body_ref)
        # The old "; capped by RAM (...)" suffix is GONE, not moved. It
        # attributed every reduction to RAM even when the commit guard was what
        # bound, and printed the RAM as `{:.0f}` (32) directly above the
        # `{:.1f}` (31.8) that describe_memory_plan prints -- two adjacent
        # lines disagreeing about the same machine. The lines below say what
        # the machine is and what the run intends to use, on EVERY run rather
        # than only when the count was chosen automatically. #commit-headroom
        print(f"  batch worker pool: {pool_workers} workers "
              f"(shared across all sources, self-healing on worker crash)")
        # UNCONDITIONAL, and that is the whole point. #commit-headroom
        # Everything above is inside `if _auto_workers`, which is ALWAYS FALSE
        # on a GUI run -- gui.py appends `--workers` on every launch -- so the
        # RAM cap's only user-visible evidence was invisible to its entire
        # audience, and a submitted log could not say what pool ran or on what
        # machine. A memory-error report is undiagnosable without these numbers.
        for _ln in describe_memory_plan(pool_workers):
            print(f"  {_ln}")
        try:
            shared_pool.prewarm()
        except Exception as e:
            warn(f"pre-warm failed (non-fatal): {plain_error(e)}",
                 consequence="the first pieces pay the cold start")

    # First-writer wins: shared set so later sources can't overwrite earlier outputs.
    claimed_dst_paths: set[Path] = set()

    # Resolve master/Data dirs once for the batch (result is identical per source).
    # Clearing first ensures the patcher's caches don't carry over from a prior run.
    ube_patcher.clear_batch_caches()
    batch_master_data_dirs = (_discover_master_data_dirs(sources[0])
                              if sources else None)
    if batch_master_data_dirs:
        print(f"  master/Data search: {len(batch_master_data_dirs)} dir(s) "
              "(resolved once for the batch)")

    # Non-playable armour a female NPC of a UBE-capable race wears or carries is
    # converted like playable armour. Built once for the batch -- `auto` built it
    # during source selection in this same process, so this is a cache hit
    # there. None when switched off, without a load order, or on an ESP-only
    # refresh (which plans no meshes). #npc-worn-nonplayable
    batch_npc_worn = (None if getattr(args, "plugins_only", False)
                      else _batch_npc_worn_armos())
    # An armour's playable flag is its WINNING record's; same cache as source
    # selection. None when switched off, without a load order, or on an
    # ESP-only refresh. #selection-winner-playable
    batch_winner_np = (None if getattr(args, "plugins_only", False)
                       else _batch_armo_winner_nonplayable())

    # Meshes another mod already ships BUILT for UBE at the path we would write
    # are left to it. The same lookup coverage points at them with, so an
    # excluded mod's build is never relied on. None when switched off or on an
    # ESP-only refresh. #skip-built-ube-path
    batch_built_ube = (
        None if (getattr(args, "plugins_only", False)
                 or not ube_patcher._skip_built_ube_path())
        else _third_party_ube_twin_lookup(
            output, _split_mod_arg(getattr(args, "exclude_mods", None)) or ()))

    # Full-VFS mesh index built once for the batch. Maps each armour mesh to the
    # MO2-priority winner across all enabled mods so BodySlide-built / replacer /
    # patch meshes in OTHER mods are found and converted.
    mesh_vfs_index = None
    try:
        _lay = paths.discover_layout()
        _enabled_ordered = paths.enabled_mods_ordered(_lay)
        _mr = paths.mods_root()
        # Reuse the index built during source selection (superset of selected sources).
        # Falls back to building one when `convert` is invoked directly.
        if _mr is not None:
            mesh_vfs_index = _BATCH_MESH_INDEX.get(str(Path(_mr)).lower())
        if mesh_vfs_index is not None:
            print(f"  VFS mesh index: reusing {len(mesh_vfs_index)} located "
                  "armour mesh path(s) from source selection "
                  "(no second modlist walk)")
        elif _enabled_ordered and _mr is not None:
            _target_keys: "set[str]" = set()
            for _src in sources:
                try:
                    for _b in _player_armor_mesh_bases(
                            _src, include_candidate_slots=True,
                            armo_winner_nonplayable=batch_winner_np,
                            npc_worn_armos=batch_npc_worn):
                        _target_keys.update(
                            (f"{_b}_0.nif", f"{_b}_1.nif", f"{_b}.nif"))
                except Exception:
                    pass
            if _target_keys:
                mesh_vfs_index = discovery.build_mesh_index(
                    Path(_mr), _enabled_ordered,
                    target_keys=_target_keys,
                    skip_mods={Path(output).name})
                print(f"  VFS mesh index: located {len(mesh_vfs_index)} of "
                      f"{len(_target_keys)} referenced armour mesh path(s) "
                      f"across {len(_enabled_ordered)} enabled mods")
    except Exception as _e:
        print(f"  (VFS mesh index unavailable -> source-local meshes only: "
              f"{plain_error(_e)})")
        mesh_vfs_index = None

    # BSA fallback: when an armour mesh isn't loose anywhere, extract it from
    # load-order BSAs (bespoke-armor mod BSAs, etc.). Lazy: only scans on an actual miss.
    global _BATCH_BSA_INDEX
    _BATCH_BSA_INDEX = None
    try:
        _blay = paths.discover_layout()
        _bord = paths.enabled_mods_ordered(_blay)
        _bmr = paths.mods_root()
        if _bmr is not None and _bord:
            # Game Data dir(s) LAST: the vanilla mesh archives back the sweep,
            # but any mod BSA shipping the same path wins (first hit in _scan),
            # matching MO2 priority.
            _bsa_dirs = _load_order_bsa_dirs(_bmr, _bord, _blay.game_data_dirs)
            _BATCH_BSA_INDEX = _BsaMeshIndex(
                _bsa_dirs, Path(output) / "_bsa_staging")
            # Source selection may already have listed these archives to find
            # mods whose armour lives only in them; take that listing rather
            # than read ~260 archive tables a second time. #bsa-only-sources
            _sel_bsa = _SELECTION_BSA_INDEX.pop(str(Path(_bmr)).lower(), None)
            if _BATCH_BSA_INDEX.adopt_listing(_sel_bsa):
                print(f"  BSA fallback index: reusing {len(_BATCH_BSA_INDEX._index)} "
                      "mesh path(s) listed during source selection "
                      "(no second archive scan)")
    except Exception:
        _BATCH_BSA_INDEX = None

    # Incremental floor = newest of (converter source code, UBE body ref,
    # CONFIG FINGERPRINT). The fingerprint closes the gap that kept this
    # opt-in: every CBBE2UBE_* env var and the NIF-relevant args were invisible
    # to a pure-mtime floor, so changing one and re-running incrementally
    # reported "reusing N NIFs" while the change reached nothing.
    incremental_floor = None
    if getattr(args, "incremental", False):
        try:
            code_mtime = _incremental_code_mtime()
            ref_mtime = 0.0
            _ref = args.ube_body_ref or _find_ube_body_ref()
            if _ref and Path(_ref).is_file():
                ref_mtime = Path(_ref).stat().st_mtime
            _fp = _nif_config_fingerprint(args)
            cfg_mtime = _config_stamp_mtime(output, _fp)
            incremental_floor = max(code_mtime, ref_mtime, cfg_mtime)
            _why = "code" if code_mtime >= max(ref_mtime, cfg_mtime) else (
                "body ref" if ref_mtime >= cfg_mtime else "config")
            print(f"  incremental mode ON (reuse outputs newer than "
                  f"source + {time.strftime('%Y-%m-%d %H:%M', time.localtime(incremental_floor))}"
                  f"; floor set by {_why}, config {_fp[:12]})")
        except Exception as e:
            warn(f"incremental floor calc failed: {plain_error(e)}",
                 consequence="doing a full convert instead")
            incremental_floor = None

    # #skip-already-ube: armors ANOTHER mod has already UBE-patched are skipped
    # BEFORE conversion, not converted-then-suppressed at the coverage stage.
    # Computed once here (the scan is cached, so the coverage stage's later call
    # is free) because it reads every enabled plugin. Never fatal: on failure we
    # convert everything, which is the old behaviour.
    # A check that did not run is a WARNING, not silence: the tally below
    # must not read "all clear" when armour another mod already patched may
    # have been converted again. #convert-needs-a-modlist
    _ube_scan_skipped = False
    if paths.mods_root() is None:
        # Only reachable with --ube-body-ref (the guard above refuses the rest).
        batch_ube_covered = None
        _ube_scan_skipped = True
        warn("no MO2 mods folder: cannot check other mods for existing UBE patches",
             consequence="everything is converted, including armour another mod already patched",
             fix="run from inside the modlist, or point the Paths tab at its ModOrganizer.ini")
    else:
        try:
            _skip_ube_lay = paths.discover_layout()
            batch_ube_covered = _third_party_ube_covered_armos(
                paths.mods_root(),
                enabled_names=paths.enabled_mods(_skip_ube_lay),
                skip_mods={Path(output).name},
                # #third-party-ini-slot-check
                active_plugins=paths.active_plugins_ordered(_skip_ube_lay))
            if batch_ube_covered:
                print(f"  {len(batch_ube_covered)} armor(s) already UBE-patched "
                      "by another mod -- those pieces will NOT be converted")
        except Exception as _e:
            batch_ube_covered = None
            _ube_scan_skipped = True
            warn(f"could not scan for existing UBE patches ({plain_error(_e)})",
                 consequence="converting everything, including armour another mod "
                             "already patched")

    # THE RECIPE AND AN EMPTY SCOREBOARD BEFORE THE FIRST SOURCE, then the
    # scoreboard again after every source (the `finally` below), so a run that
    # dies leaves its own report, marked incomplete. #report-checkpoint
    _stamp_run_start(output, planned=len(sources), workers=_planned_workers,
                     orphan_temps_removed=_orphans_removed)
    results = []
    try:
        for i, src in enumerate(sources, 1):
            # Vanilla sweep = its own PASS: distinct header + progress label,
            # and (below) its failure never blocks the merge -- a dead sweep
            # just means no vanilla coverage this run, mod armor unaffected.
            _is_sweep_src = bool(_vanilla_sweep_esps(src))
            _disp = "Vanilla sweep (base game + DLC)" if _is_sweep_src else src.name
            # Machine-parseable progress marker for the GUI (determinate bar +
            # ETA). Format: "[progress] <done> <total> <name>". The GUI hides it
            # from the visible log; the human line below stays.
            print(f"[progress] {i} {len(sources)} {_disp}", flush=True)
            if _is_sweep_src:
                print("\n=== VANILLA SWEEP pass: base game + DLC as the "
                      "lowest-priority source ===")
            print(f"\n--- [{i}/{len(sources)}] converting '{_disp}' ---")
            def _convert_one(_src, *, _pool, _workers):
                return auto_convert_mod(
                    _src, output,
                    output_esp_name=(args.esp_name if len(sources) == 1 else None),
                    # Default: DON'T copy textures. The converted NIFs keep the
                    # original (Data-relative) texture paths, so the engine
                    # resolves them from the SOURCE mods via the MO2 VFS -- the
                    # same path BSA-archived textures already use successfully.
                    # Copying duplicated ~17 GB AND, because the copy lands at the
                    # output mod's high priority, silently overrode standalone
                    # retexture mods. Opt back in with --copy-textures. #no-tex-copy
                    copy_textures=(bool(getattr(args, "copy_textures", False))
                                   and not bool(getattr(args, "no_textures", False))),
                    ube_body_ref_path=args.ube_body_ref,
                    nif_workers=_workers,
                    nif_pool=_pool,
                    unmerged_patch_subdir=args.unmerged_patch_subdir,
                    claimed_dst_paths=claimed_dst_paths,
                    master_data_dirs=batch_master_data_dirs,
                    mesh_vfs_index=mesh_vfs_index,
                    incremental_floor=incremental_floor,
                    ube_covered_armos=batch_ube_covered,
                    npc_worn_armos=batch_npc_worn,
                    armo_winner_nonplayable=batch_winner_np,
                    built_ube_twin=batch_built_ube,
                )

            try:
                if getattr(args, "plugins_only", False):
                    r = refresh_mod_esp(
                        src, output,
                        output_esp_name=(args.esp_name
                                         if len(sources) == 1 else None))
                    results.append((src, r, None))
                    continue
                # Sweep self-heal: snapshot output-path claims so a crashed
                # first attempt (claims made, files unwritten) can't make the
                # retry skip its own meshes as "collisions".
                _claims_before = (set(claimed_dst_paths)
                                  if _is_sweep_src else None)
                try:
                    r = _convert_one(src, _pool=shared_pool,
                                     _workers=args.workers)
                except Exception as _e1:
                    if not _is_sweep_src:
                        raise
                    # The shared worker pool is the one component with known
                    # environment-sensitive failure modes, and the sweep is
                    # the LAST source — a slow serial retry delays nothing
                    # else. A deterministic planning bug just fails again fast.
                    warn(f"vanilla sweep failed ({plain_error(_e1)})",
                         consequence="retrying SERIALLY (no worker pool; slower, but "
                                     "immune to pool-environment failures)...",
                         indent="")
                    claimed_dst_paths.clear()
                    claimed_dst_paths.update(_claims_before)
                    r = _convert_one(src, _pool=None, _workers=1)
                    print("  vanilla sweep serial retry SUCCEEDED")
                results.append((src, r, None))
            except Exception as e:
                results.append((src, None, e))
                warn(f"conversion failed: {plain_error(e)}",
                     consequence="the run stopped; the log above says where",
                     fix="fix the cause and run again", indent="")
            finally:
                # Whatever happened to this source -- converted, failed, or a
                # death on its way out -- the report on disk now describes the
                # run up to here. #report-checkpoint
                _checkpoint_report(output, results, planned=len(sources),
                                   workers=_planned_workers,
                                   orphan_temps_removed=_orphans_removed)
    finally:
        if shared_pool is not None:
            shared_pool.shutdown()
        _BATCH_BSA_INDEX = None   # release BSA archives after the batch
        # One full collection at the phase boundary: the batch leaves cyclic
        # garbage behind (MEASURED 2026-09-17: 1,445 -> 874 MB in the parent
        # on a 76-NIF mod), and the postflight passes should start from the
        # live state, not on top of it. #postflight-release
        gc.collect()

    print(f"\n=== batch auto-conversion done ({len(results)} mod(s)) ===")

    # Guarantee both _0 and _1 exist: a missing weight partner breaks the piece
    # at that body weight. Fill any single-weight base from its present partner.
    try:
        # UNION across every source: if ANY mod ships a real `_0` for a base,
        # that `_0` is authoritative and must never be overwritten.
        _src_variants: dict = {}
        # `_errs`, not `_e`: five `except ... as _e` handlers live in this same
        # function, and an unused loop variable of that name collides with them
        # -- which is why pyflakes reported the "unused _e" against one of the
        # HANDLERS (whose `_e` is used) instead of against this line.
        _left_to_builder: set = set()
        for _s, _r, _errs in results:
            for _b, _sufs in getattr(_r, "source_weight_variants", {}).items():
                _src_variants.setdefault(_b, set()).update(_sufs)
            _left_to_builder |= getattr(_r, "superseded_weight_bases", set())
        _filled, _refreshed = _complete_weight_partners(
            output, source_variants=_src_variants, skip_bases=_left_to_builder)
        if _filled:
            print(f"  weight-partner completion: filled {_filled} missing "
                  "_0/_1 partner mesh(es) (would otherwise break at one weight)")
        if _refreshed:
            print(f"  weight-partner completion: refreshed {_refreshed} STALE "
                  "filled partner(s) left by an earlier build")
    except Exception as _e:
        print(f"  (weight-partner completion skipped: {plain_error(_e)})")

    # merge_blockers: hard ESP-generation failures -> block auto-merge.
    # overall_failures: merge_blockers + NIF errors + load failures -> non-zero exit.
    # overall_warnings: validator notes -> surfaced loudly, don't fail exit.
    merge_blockers = 0
    overall_failures = 0
    # Run-level warnings found before the batch: printed where they were found,
    # recorded there or here, counted now. #run-warnings
    if _ube_scan_skipped:
        _record_failure("check skipped", "existing UBE patches", "already-UBE scan",
                        "other mods could not be checked for UBE patches, so armor "
                        "one of them already patched may have been converted again",
                        severity="warning")
    overall_warnings = (int(_ube_scan_skipped) + int(not _skypatcher_ok)
                        + int(_settings_malformed)
                        # recorded at the start AND counted here. #orphan-temps
                        + int(bool(_orphans_removed))
                        # one per per-source patch left under its old name.
                        # #source-patch-rename
                        + _rename_failures)
    for src, r, err in results:
        _is_sweep_src = bool(_vanilla_sweep_esps(src))
        print("\n  " + ("Vanilla sweep (base game + DLC)" if _is_sweep_src
                        else src.name))
        if err is not None:
            if _is_sweep_src:
                # A dead sweep = no vanilla coverage THIS RUN (the pre-sweep
                # state); the 100+ mod patches are complete and merging them
                # must not be held hostage. Loud + non-zero exit, not a blocker.
                warn(f"VANILLA SWEEP FAILED: {plain_error(err)}",
                     consequence="vanilla armour that no mod overrides is not linked "
                                 "this run, so it is invisible on UBE actors",
                     indent="    ")
                print("       merge proceeds WITHOUT vanilla coverage — mod "
                      "armor is unaffected. Rerun just the sweep afterwards "
                      "with --only-mods vanilla (GUI: Select mods -> "
                      "'vanilla').")
                _record_failure("vanilla sweep failed",
                                "Vanilla sweep (base game + DLC)",
                                "whole source", plain_error(err))
            else:
                warn(f"FAILED: {plain_error(err)}",
                     consequence="this source did not convert; nothing of it is in the output",
                     indent="    ")
                merge_blockers += 1
                _record_failure("source failed", src.name,
                                "whole source", plain_error(err))
            overall_failures += 1
            continue
        if r.source_esps:
            print(f"    source ESPs: {len(r.source_esps)}")
            for i, (src_e, out_e) in enumerate(
                    zip(r.source_esps, r.output_esps)):
                print(f"      [{i}] {src_e.name} -> {out_e.name}")
        else:
            print(f"    source ESP : {r.source_esp}")
            print(f"    output ESP : {r.output_esp}")
        print(f"    masters    : {r.esp_stats.get('masters')}")
        print(f"    NIFs       : {len(r.nif_results)} total — "
              f"{r.nif_copy_count} copy, {r.nif_swap_count} body-swap, "
              f"{r.nif_skipped} skipped")
        if r.nif_errors:
            warn(f"CONVERSION ERRORS on {r.nif_errors} NIF(s):",
                 consequence="each mesh below did not convert and is absent from the output",
                 indent="    ")
            for er in r.nif_error_results:
                print(f"       {er.src_path.name}: {er.reason}")
                _record_failure("mesh failed", src.name,
                                er.src_path.name, er.reason)
            overall_failures += r.nif_errors
        if r.nif_load_failures:
            warn(f"LOAD FAILURES on {len(r.nif_load_failures)} output NIFs",
                 consequence="the files below were written but cannot be read back; "
                             "they will not load in game",
                 indent="    ")
            for p in r.nif_load_failures:
                print(f"       {p}")
                _record_failure("output mesh unreadable", src.name, p)
            overall_failures += 1
        if r.nif_invariant_warnings:
            # CTD-class, symmetric with the merged-ESP postflight: a zero-vert
            # shape is invisible and an over-cap shape left in <=1 partition
            # hard-CTDs on equip. Fail the build, don't just warn.
            warn(f"POSTFLIGHT NIF (CTD-class): {len(r.nif_invariant_warnings)} "
                 "invariant issue(s)",
                 consequence="a zero-vert or over-cap partition crashes the game on "
                             "equip; the run is reported as failed; listed below",
                 fix="do not ship those meshes",
                 indent="    ")
            for w in r.nif_invariant_warnings:
                print(f"       {w}")
                _record_failure("CTD-class mesh issue", src.name, w)
            overall_failures += len(r.nif_invariant_warnings)
        if r.virtualbody_rehide_failures:
            warn(f"VirtualBody re-hide: {len(r.virtualbody_rehide_failures)} NIF(s) "
                 "may show a visible body-double",
                 consequence="listed below",
                 indent="    ")
            for w in r.virtualbody_rehide_failures:
                print(f"       {w}")
            overall_warnings += len(r.virtualbody_rehide_failures)
        # ESP generation failure: that ESP's ARMA/ARMO absent from merge (invisible).
        # Non-zero exit, but NOT a merge_blocker (one bad ESP shouldn't lose the rest).
        if r.esp_gen_failures:
            warn(f"ESP GENERATION FAILED for {len(r.esp_gen_failures)} source ESP(s)",
                 consequence="their armor is ABSENT from the merge (invisible in-game): "
                             f"{r.esp_gen_failures}",
                 fix="read the reason beside each plugin and fix the source, then run again",
                 indent="    ")
            for _f in r.esp_gen_failures:
                _name, _why = (_f if isinstance(_f, (list, tuple)) and
                               len(_f) == 2 else (_f, ""))
                _record_failure("plugin patch failed", src.name, _name, _why)
            overall_failures += len(r.esp_gen_failures)
        if r.esp_skipped_no_armor:
            print(f"    {r.esp_skipped_no_armor} source ESP(s) skipped: no armor "
                  f"(landscape/quest/patch ESPs) — not a failure")
        if r.nif_partial:
            warn(f"PARTIAL: {r.nif_partial} NIF(s) dropped a shape",
                 consequence="an invisible piece in-game",
                 fix="see the coverage report's PARTIAL section",
                 indent="    ")
            _record_failure("partial mesh (shape dropped)", src.name,
                            f"{r.nif_partial} mesh(es)",
                            "see conversion report, PARTIAL section")
            overall_failures += r.nif_partial
        # Validator warnings: surfaced loudly but don't block the merge or fail exit.
        validator_hits = []
        for stats in (r.esp_stats_list or
                      ([r.esp_stats] if r.esp_stats else [])):
            for w in stats.get("validation_warnings", []) or []:
                validator_hits.append(w)
        if validator_hits:
            warn(f"PATCH VALIDATOR: {len(validator_hits)} warning(s)",
                 consequence="listed below; the patch still loads, but check each "
                             "before trusting it",
                 indent="    ")
            for w in validator_hits:
                print(f"       {w}")
            overall_warnings += len(validator_hits)
        print(f"    Textures   : {r.textures_copied} files copied")
        if r.notes:
            for n in r.notes:
                # Validator notes already surfaced above — don't double-print.
                if n.startswith("!! patch validator"):
                    continue
                print(f"    note: {n}")
    print(f"\n  Combined output mod: {output}")

    # Postflight: scan the WHOLE output tree for body meshes missing a _0/_1
    # partner (the !UBE fixer above only covers !UBE; general armor is uncovered).
    try:
        _wp_miss = _postflight_missing_weight_partners(output)
        if _wp_miss:
            warn(f"POSTFLIGHT weight-partners: {len(_wp_miss)} body mesh(es) "
                 "missing a _0/_1 partner",
                 consequence="invisible at one body weight; listed below",
                 indent="\n")
            for _w in _wp_miss:
                print(f"     {_w}")
            overall_warnings += len(_wp_miss)
    except Exception as _wpe:
        warn(f"postflight weight-partner scan skipped: {plain_error(_wpe)}",
             consequence="missing _0/_1 partners were not checked this run")

    # Postflight REPAIR, and it runs BEFORE the detector below so that detector
    # reports the state that actually ships. The jiggle graft's fit gate is
    # judged per FILE, but `_0` and `_1` are ONE garment: ~20 pieces straddle
    # the threshold and end up with belly or butt jiggle at one body weight
    # only. This gives the deficient weight its partner's bone.
    # #weight-partner-jiggle-sync
    # #tail-fold: the sync and the parity check below share one walk, each
    # pair loaded once; None -> the two serial passes run, as before. A fold
    # that raises is a sync that raised: warned below, and the check then
    # walks the pairs on its own.
    _wp_fold = None
    try:
        _wp_fold = _postflight_weight_partner_fold(
            output, check=not _flag("CBBE2UBE_NO_WEIGHT_PARITY_CHECK", False))
        _wp_sync = (_wp_fold[0] if _wp_fold is not None
                    else _postflight_sync_weight_partner_jiggle(output))
        if _wp_sync:
            print(f"\n  weight-partner jiggle sync: {_wp_sync} vert(s) given "
                  f"their partner's scale bone")
    except Exception as _wps:
        warn(f"postflight weight-partner jiggle sync skipped: {plain_error(_wps)}",
             consequence="the two weights of a pair may jiggle differently")

    # Postflight: flag `_0`/`_1` partners whose converted scale-bone set diverges
    # (per-file metadata leaking to one weight -> the two morph differently; e.g.
    # the #slot0-weight-partner slot-0 bug). Read-only NIF pass; disable for speed
    # with CBBE2UBE_NO_WEIGHT_PARITY_CHECK=1.
    _wp_div: "list" = []          # reused by the JSON health report below
    if not _flag("CBBE2UBE_NO_WEIGHT_PARITY_CHECK", False):
        try:
            _wp_div = (_wp_fold[1] if _wp_fold is not None
                       else _postflight_weight_partner_divergence(output))
            if isinstance(_wp_div, Exception):
                # The fold's check died on a pair, as the serial one would.
                _wp_err, _wp_div = _wp_div, []
                raise _wp_err
            if _wp_div:
                warn(f"POSTFLIGHT weight-partner parity: {len(_wp_div)} shape(s) convert "
                     "differently at _0 vs _1",
                     consequence="a per-weight metadata leak: both weights should morph "
                                 "identically; listed below",
                     indent="\n")
                for _d in _wp_div[:20]:
                    print(f"     {_d}")
                if len(_wp_div) > 20:
                    print(f"     ... and {len(_wp_div) - 20} more")
                overall_warnings += len(_wp_div)
        except Exception as _wpe2:
            warn(f"postflight weight-partner parity scan skipped: {plain_error(_wpe2)}",
                 consequence="_0/_1 parity was not checked this run")

    # What the end of the batch costs, in the log beside the start-of-run
    # "machine:" line's figures: PARENT_COMMIT_GB prices this process's
    # postflight peak, and this is the reading that checks it on every run
    # (the second pool spawns next). #postflight-release
    _st_end = _memory_status() or {}
    _pm_end = _process_private_mb()
    if _st_end.get("commit_limit_gb") is not None:
        print(f"\n  after the batch: this process "
              f"{(_pm_end or 0.0) / 1024:.2f} GB of commit charge; page file "
              f"{_st_end['commit_free_gb']:.1f} GB free of "
              f"{_st_end['commit_limit_gb']:.1f} GB commit limit")

    # --- Vertex-color shader-flag sanitize ---
    # Clear Vertex_Colors/Vertex_Alpha shader flags on shapes with no color buffer.
    # Our rebuild path drops source colors but inherits flags; a flag on a missing
    # buffer crashes the engine on load. Idempotent.
    meshes_out = output / "meshes"
    if meshes_out.is_dir():
        print("\n--- sanitizing vertex-color shader flags ---")
        try:
            # Budget this pool the SAME way as the conversion pool. Left to
            # itself the sweep picks `min(16, cpu_count - 2)` on CPU count
            # alone (nif_convert_writer.py), so on a 16-thread box it spawned
            # 14 fresh processes where the RAM-capped pool had run 8 -- and the
            # user had no way to lower it: no CLI flag, no GUI control, no env
            # var. Worst of all it fires at the END, after hours of work, and
            # its own `except Exception` falls back to serial, so an
            # out-of-memory death here left the run "successful" and silent.
            # `pool_workers` is bound only on the pooled branch above, so
            # re-derive rather than reach for it. #worker-mem-budget
            _sanitize_workers = (args.workers if args.workers is not None
                                 else default_worker_count())
            vc = nif_convert.sanitize_output_vertex_color_flags(
                meshes_out, workers=_sanitize_workers)
            print(f"  scanned {vc['files']} nifs; "
                  f"fixed {vc['shapes_fixed']} shape(s) in "
                  f"{vc['files_changed']} file(s)")
            if vc.get("pool_error"):
                # Printed, counted and recorded. Silence here is what made an
                # out-of-memory death at the end of a multi-hour run look like
                # a clean finish. #commit-headroom
                warn(vc['pool_error'],
                     consequence="the vertex-colour sweep fell back to running one file at "
                                 "a time and finished; nothing was lost",
                     fix="if this repeats, lower the worker count in Settings")
                overall_warnings += 1
                # A WARNING, not a failure: the sweep fell back to serial and
                # finished. Recorded as a failure, the GUI popup titled it
                # "1 item(s) failed to convert" and told the user the armour
                # was invisible. #run-warnings
                _record_failure("worker pool died", "vertex-colour sweep",
                                "end-of-run sweep", vc["pool_error"],
                                severity="warning")
        except Exception as e:
            warn(f"vertex-color sanitize failed: {plain_error(e)}",
                 consequence="vertex colours were left as the source had them", indent="")

    # Did unified coverage actually run? It now lives INSIDE the merge, so
    # every path that skips the merge also skips coverage -- and coverage
    # silently not happening is invisible until armor turns up missing
    # in game. Tracked so the tail can say so out loud.
    _coverage_ran = False
    # --- Auto-merge into Combined ESP ---
    # Merge all per-source UBE patch ESPs into one ESL-flagged ESP at the mod root.
    # Only the merged ESP should be visible to MO2's scanner; per-source patches in
    # the subdir are not auto-loaded, avoiding duplicate-record CTDs.
    if args.auto_merge and merge_blockers == 0:
        if args.unmerged_patch_subdir and args.unmerged_patch_subdir not in (".", "/"):
            patches_dir = output / args.unmerged_patch_subdir
        else:
            patches_dir = output
        if patches_dir.is_dir():
            # This gates the WHOLE block -- female-model restore, coverage and
            # the merge -- so it must see the renamed per-source patches too.
            # #source-patch-rename
            patch_paths = _merge_gate_patch_paths(patches_dir)
            if patch_paths:
                # Female-model re-check before merge: per-mod patches may have
                # fallen back to a male model at patch time; re-point any ARMA
                # whose female mesh is now on disk. Must run before the merge.
                try:
                    _fmr = ube_patcher.restore_female_models(
                        patches_dir, output)
                    if _fmr.get("models_restored"):
                        print(f"\n--- female-model restore: re-pointed "
                              f"{_fmr['models_restored']} ARMA model(s) in "
                              f"{_fmr['patches_changed']} patch(es) to "
                              "converted FEMALE meshes (male fallback no "
                              "longer needed) ---")
                except Exception as e:
                    warn(f"female-model restore failed: {plain_error(e)}",
                         consequence="continuing with male fallbacks")
                # UNIFIED COVERAGE (3b/3c): emit winner-scan coverage patches
                # AFTER female-model restore (so it never touches their sidecar
                # fids). 3c = the winner-scan is the SOLE generator: merge ONLY
                # the coverage patches (they cover a proven superset of the
                # per-source records), so the Combined has ~half the ARMAs, far
                # fewer ESL pieces, and no orphan duplicates. The per-source
                # patches stay in _unmerged_patches (unmerged / not loaded).
                # SAFETY: if the emit produced no coverage patches (failure),
                # fall back to merging everything so the Combined is never empty.
                # Unified coverage is the ONLY coverage model. The old
                # standalone ModBody/ModNonBody plugins are gone.
                _cov_ok, _cov_targets, _cov_body = \
                    _emit_unified_coverage_patches(
                        output, patches_dir, batch_master_data_dirs,
                        args.merged_name,
                        exclude_mods=_split_mod_arg(
                            getattr(args, "exclude_mods", None)) or ())
                _coverage_ran = True
                if not _cov_ok:
                    # Record it. Coverage failing silently is the worst outcome
                    # here: mod-defined helmets/circlets/jewelry and body
                    # variants go INVISIBLE on UBE actors, and without this the
                    # run exits 0 with nothing in the failures file to explain
                    # it. (The old standalone passes recorded a failure; when
                    # coverage moved inside the merge that accounting was lost.)
                    _record_failure("coverage", output, "unified coverage",
                                    f"winner-scan incomplete (targets={_cov_targets})",
                                    severity="warning")
                    overall_warnings += 1      # recorded AND counted #run-warnings
                _cov_only = sorted(
                    patches_dir.glob("UBE_Mod*Coverage* UBE patch.esp"))
                # Use coverage as the SOLE generator ONLY when it fully ran and
                # actually covered something; otherwise merge the per-source
                # patches so a failed/empty winner scan can't drop all coverage.
                # ...and only when it carries BODY links. With no converted
                # !UBE meshes the body pass is skipped, and taking the sole
                # branch there would discard the per-source patches that are
                # the only remaining source of body coverage.
                _cov_sole = bool(_cov_ok and _cov_targets > 0 and _cov_only
                                 and _cov_body)
                if _cov_sole:
                    print(f"  [unified/3c] merging {len(_cov_only)} winner-scan "
                          f"coverage patch(es) ({_cov_targets} armors) as the "
                          "SOLE generator (per-source patches left unmerged)")
                    patch_paths = _cov_only
                else:
                    warn(f"[unified] coverage empty/incomplete (ok={_cov_ok}, "
                         f"targets={_cov_targets}, body={_cov_body})",
                         consequence="merging per-source patches instead")
                    # EXCLUDE any coverage patch still on disk. The glob
                    # "*UBE patch.esp" also matches
                    # "UBE_Mod*Coverage UBE patch.esp", so a partial run --
                    # non-body pass wrote its patch, body pass threw -- would
                    # merge per-source AND coverage links for the same armors,
                    # doubling the armature (body renders twice). The unified
                    # path is all-or-nothing; the fallback is per-source ONLY,
                    # and one file per source (never an old-named and a renamed
                    # copy of the same one). #source-patch-rename
                    patch_paths = _per_source_patch_paths(patches_dir)
                merged_out = output / args.merged_name
                print(f"\n--- auto-merging {len(patch_paths)} patch(es) "
                      f"into {merged_out.name} ---")
                try:
                    stats = ube_patcher.merge_patches_split(
                        patch_paths, merged_out, esl_flag=True,
                        master_data_dirs=batch_master_data_dirs,
                        # the pipeline owns this output mod, so stale
                        # numbered pieces from a previous run are ours
                        # to remove
                        owns_output_dir=True,
                    )
                    print(f"  merged ESP: {merged_out}")
                    print(f"  ESL flag  : {stats.get('esl_flagged')}")
                    if stats.get('split_pieces', 1) > 1:
                        print(f"  SPLIT     : {stats['split_pieces']} ESL pieces "
                              f"-> {', '.join(stats.get('pieces', []))} "
                              "(enable ALL of them)")
                    if stats.get('downgraded_to_full_esp'):
                        warn(f"{stats.get('own_arma_records')} new ARMAs exceed the "
                             f"{stats.get('esl_slots_max')}-record ESL cap",
                             consequence="shipped as a NON-ESL full ESP (consumes one "
                                         "load-order slot)",
                             fix="position it to win")
                    print(f"  masters   : {len(stats.get('masters', []))}")
                    print(f"  ARMA total: {stats.get('total_arma_records')} "
                          f"(own: {stats.get('own_arma_records')}"
                          f"/{stats.get('esl_slots_max')} ESL slots)")
                    print(f"  ARMO total: {stats.get('total_armo_records')} "
                          f"(dedup: {stats.get('armo_duplicates_merged', 0)} "
                          "duplicates merged)")
                    # FULL SKYPATCHER: the merge translated the per-patch link
                    # sidecars into armorAddonsToAdd lines against final
                    # Combined FormIDs -- write the runtime INI. The Combined
                    # then carries NO third-party overrides.
                    _sp_lines = stats.get("skypatcher_ini_lines") or []
                    _sp_ini_path = (output / "SKSE" / "Plugins"
                                    / "SkyPatcher" / "armor"
                                    / (merged_out.stem + ".ini"))
                    if not _sp_lines and _sp_ini_path.is_file():
                        # SkyPatcher applies EVERY INI in this folder. A run
                        # that merged fine but produced no links would leave
                        # the PREVIOUS run's INI in place, pointing at FormIDs
                        # the rewritten Combined has since reassigned -- so
                        # armatures attach to whatever now holds those IDs.
                        try:
                            _sp_ini_path.unlink()
                            print("  FULL SKYPATCHER: no links this run -- "
                                  f"removed stale {_sp_ini_path.name}")
                        except OSError as _e:
                            warn(f"could not remove stale {_sp_ini_path.name}: {plain_error(_e)}",
                                 consequence="an old SkyPatcher ini may still apply beside "
                                             "the new one",
                                 fix="delete it by hand")
                    if _sp_lines:
                        from .atomic_io import atomic_write_bytes
                        _sp_hdr = [
                            f"; {SKYPATCHER_INI_MARKER} FULL SKYPATCHER: adds each converted",
                            "; armor's minted UBE armature(s) at runtime to the",
                            "; LOAD-ORDER-WINNING record -- no ESP overrides.",
                        ]
                        atomic_write_bytes(_sp_ini_path, ("\n".join(
                            _sp_hdr + _sp_lines) + "\n").encode("utf-8"))
                        print(f"  FULL SKYPATCHER: {stats.get('skypatcher_targets')} "
                              f"armor record(s) covered via "
                              f"{_sp_ini_path.name} (no ESP overrides)")
                        for _rl in ube_patcher.report_link_reconciliation(stats):
                            print(_rl)
                        # Vanilla-coverage assertion (`_vanilla_links_check`):
                        # the delivered count decides when coverage is the
                        # sole generator. #vanilla-links-delivered
                        _van_links, _sweep_dead = _vanilla_links_check(
                            _sp_lines, results, _cov_sole)
                        print(f"  vanilla coverage: {_van_links} vanilla/DLC "
                              "armor record(s) linked")
                        if _sweep_dead and _cov_sole:
                            warn("the VANILLA SWEEP ran but the delivered "
                                 "coverage links 0 vanilla/DLC records",
                                 consequence="vanilla armor will be invisible on "
                                             "UBE actors",
                                 fix="check the unified coverage step above for "
                                     "errors, or rerun just the sweep (Select mods "
                                     "-> 'vanilla')")
                        elif _sweep_dead:
                            warn("the VANILLA SWEEP ran but linked 0 records",
                                 consequence="vanilla armor no mod overrides will be "
                                             "invisible on UBE actors",
                                 fix="check the VANILLA SWEEP pass above for errors, or "
                                     "rerun just the sweep (Select mods -> 'vanilla')")
                    # Reconcile alt-texture 3D indices against the converted NIFs.
                    # Shape reordering during the NIF merge shifts MO2S/MO3S indices;
                    # reconcile ALL split pieces (overflow also carries alt-texture sets).
                    try:
                        nfix = ube_patcher.reconcile_alt_texture_indices_all(
                            merged_out, output / "meshes")
                        print(f"  alt-texture reconcile: fixed {nfix} ARMA(s)")
                    except Exception as e:
                        warn(f"alt-texture reconcile failed: {plain_error(e)}",
                             consequence="colour variants may bind to the wrong shape",
                             fix="check the affected armour's variants in game")
                    # Clear slot 33 (Hands) from forearm bracers that claim it but have
                    # no hand geometry — else they hide nude hands and draw nothing.
                    # Mesh-driven: real gloves/gauntlets are never touched.
                    try:
                        hf = ube_patcher.fix_spurious_hand_slot(
                            merged_out, output / "meshes")
                        if hf.get("armos_fixed") or hf.get("armas_fixed"):
                            print(f"  hands-slot fix: {hf['armos_fixed']} ARMO + "
                                  f"{hf['armas_fixed']} ARMA un-tagged "
                                  "(handless forearm armor claiming slot 33)")
                    except Exception as e:
                        warn(f"hands-slot fix failed: {plain_error(e)}",
                             consequence="hand pieces may keep their source slot")
                    # Dedup redundant own-ARMA armature refs: a body-armor ARMO that
                    # ended up with two converter-minted UBE ARMAs of the SAME race +
                    # meshes renders the body-swap mesh TWICE (doubled / blown-out /
                    # double-morphed -> "doesn't fit / doesn't conform" in-game).
                    try:
                        ndd = ube_patcher.dedup_armo_armature_refs_all(merged_out)
                        if ndd:
                            print(f"  armature dedup: removed {ndd} redundant "
                                  "UBE armature ref(s) (double body-swap render)")
                    except Exception as e:
                        warn(f"armature dedup failed: {plain_error(e)}",
                             consequence="duplicate armatures may remain in the Combined ESP")
                    # Self-heal a stale/mis-sorted master list (a master-tier
                    # plugin after a regular ESP = load-order/FormID CTD). No-op on
                    # a correctly-ordered piece; repairs a stale Combined an earlier
                    # run left mis-sorted (the merge_esl_overflow recurrence class).
                    try:
                        nrs = ube_patcher.resort_masters_all(
                            merged_out, master_data_dirs=batch_master_data_dirs)
                        if nrs:
                            print(f"  master re-sort: repaired {nrs} mis-ordered "
                                  "Combined piece(s) (master-tier-after-regular)")
                    except Exception as e:
                        warn(f"master re-sort failed: {plain_error(e)}",
                             consequence="the Combined ESP's masters may be out of order, "
                                         "and the game may refuse to load it",
                             fix="check the merged plugin's master list in xEdit")
                    # POSTFLIGHT: re-validate the FINAL Combined (+ ESL split
                    # pieces) AFTER the merge/winner-rebase/reconcile/hands-fix
                    # mutations. validate_patch ran per-SOURCE only; a structural
                    # break those passes introduce on the loaded plugin is
                    # otherwise invisible until an in-game CTD / invisible armor.
                    try:
                        _pf = ube_patcher.postflight_validate_combined(
                            merged_out, output / "meshes",
                            master_data_dirs=batch_master_data_dirs,
                            # the UBE body's own hands/feet and hand-made twins
                            # load from other mods (an excluded mod's mesh loads
                            # too, so no exclusions here). #coverage-nude-skin
                            mesh_resolves=_outside_ube_mesh_resolver(output))
                        if _pf["ctd"] or _pf["soft"]:
                            warn(f"POSTFLIGHT: {len(_pf['ctd'])} load-breaking + {len(_pf['soft'])} "
                                 "other issue(s) on the FINAL Combined",
                                 consequence="listed below; a load-breaking issue means the "
                                             "plugin is NOT safe to load")
                            for _n, _w in _pf["ctd"]:
                                print(f"       CTD  [{_n}] {_w}")
                            for _n, _w in _pf["soft"]:
                                print(f"       warn [{_n}] {_w}")
                            overall_failures += len(_pf["ctd"])
                            overall_warnings += len(_pf["soft"])
                        else:
                            print(f"  postflight: Combined "
                                  f"({len(_pf['pieces'])} piece(s)) validated clean")
                    except Exception as _pfe:
                        warn(f"postflight validation skipped: {plain_error(_pfe)}",
             consequence="the plugin was not checked for load-breaking issues")
                except Exception as e:
                    warn(f"auto-merge failed: {plain_error(e)}",
                         consequence="no Combined ESP this run; the per-source patches "
                                     "are still in the output", indent="")
                    _record_failure("merge failed", "Combined ESP",
                                    args.merged_name, plain_error(e))
                    overall_failures += 1
            else:
                print(f"\n  (no patches found in {patches_dir} — "
                      "skipping auto-merge)")
    elif args.auto_merge and merge_blockers > 0:
        warn(f"auto-merge SKIPPED: {merge_blockers} mod(s) failed ESP generation",
             consequence="the Combined ESP would otherwise be built from incomplete patches",
             fix="fix those mods (their errors are above) before merging",
             indent="\n")
        _record_failure("merge skipped", "Combined ESP", args.merged_name,
                        f"{merge_blockers} source(s) failed ESP generation")

    if not _coverage_ran:
        # Unified coverage is the ONLY coverage model and it is emitted as part
        # of the merge -- so no merge means NO race coverage for mod-defined
        # helmets/circlets/jewelry or body variants. They go INVISIBLE on UBE
        # actors. Say so loudly; never let this run look clean.
        print("")
        warn("NO RACE COVERAGE GENERATED",
             consequence="coverage is emitted as part of the merge, and the merge did "
                         "not run (--no-auto-merge, a failed source, or no patches "
                         "found); mod-defined helmets, circlets, jewelry and body "
                         "variants will be INVISIBLE on UBE actors until a run "
                         "completes the merge",
             fix="fix the failed source, or run again with the merge enabled")
        _record_failure("coverage", output, "unified coverage",
                        "merge did not run, so no coverage was generated",
                        severity="warning")
        # Counted too: measured 2026-09-15, this printed NO RACE COVERAGE
        # GENERATED and the run still ended "=== all clear ===". #run-warnings
        overall_warnings += 1

    if args.render_previews:
        from . import preview
        print("\n--- rendering morph previews ---")
        try:
            preview_results = preview.render_all_previews(output)
        except Exception as e:
            warn(f"preview render failed: {plain_error(e)}",
                 consequence="no preview images this run; the conversion itself is unaffected",
                 indent="")
            preview_results = []
        ok = sum(1 for r in preview_results if "error" not in r)
        err = sum(1 for r in preview_results if "error" in r)
            # BODYTRI string present but file not on disk (vs. legitimately absent).
        broken_bodytri = [
            r for r in preview_results
            if "error" not in r
            and r.get("bodytri_string")
            and not r.get("bodytri_resolved")
        ]
        max_delta_batch = max(
            (r.get("max_delta", 0.0) for r in preview_results
             if "error" not in r),
            default=0.0,
        )
        preview_dir = output.parent / f"{output.name} - Previews"
        print(f"  {ok} BMP(s) rendered, {err} failed")
        if broken_bodytri:
            warn(f"{len(broken_bodytri)} NIF(s) reference a BODYTRI that doesn't exist on disk",
                 consequence="those pieces will not follow body sliders; listed below")
            for r in broken_bodytri[:8]:
                print(f"       {r['nif']}  ->  {r['bodytri_string']}")
            if len(broken_bodytri) > 8:
                print(f"       ... and {len(broken_bodytri) - 8} more")
        print(f"  worst morph delta across batch: {max_delta_batch:.2f}u")
        print(f"  preview dir: {preview_dir}")

    summary_path = write_conversion_summary(output, results)
    if summary_path is not None:
        print(f"\n  coverage report: {summary_path}")
    write_conversion_report_json(
        output, results, weight_warnings=_wp_div,
        orphan_temps_removed=_orphans_removed,
        planned=len(sources),
        # What RAN, not what this machine would pick now. `pool_workers` is
        # unbound on the serial and --plugins-only branches, so re-derive the
        # same way the pool did. #commit-headroom
        workers=(args.workers if args.workers is not None
                 else default_worker_count()))

    if overall_failures or overall_warnings:
        print(f"\n=== {overall_failures} failure(s), "
              f"{overall_warnings} warning(s) ===")
    else:
        print("\n=== all clear ===")

    # Written every run (empty on a clean one) -- the GUI's end-of-run popup
    # reads it; an empty list means "nothing failed", never "no data".
    _write_failures_file()
    return 0 if overall_failures == 0 else 2


# Heuristics for "is this mod an armor mod we should convert?" — shared by
# `scan` and `auto` so they agree.
_ARMOR_PATH_HINTS = ("armor", "armour", "clothes", "clothing", "outfit",
                     "outfits", "weapons")  # weapons sneak in via shared paths
_ENV_PATH_HINTS = ("landscape", "architecture", "caves", "cave", "interiors",
                   "dungeons", "actors\\character\\character assets",
                   "static", "props", "creatures", "monsters", "vfx")
# Mod-name substrings that mark a mod as NOT a conversion source. Lowercased.
#
# HARD: never a source whatever they contain -- our own output and the
# BodySlide output the VFS resolves THROUGH. Converting either is a feedback
# loop, and no evidence inside them can change that.
_NONSOURCE_NAME_HINTS_HARD = ("bodyslide output", "cbbetoube",
                              "fur morph", "fur_morph")
# BEAST-RACE: body / fur-overlay / race mods, excluded because the target is the
# human female UBE body. Applied ONLY where there is no positive ARMA evidence,
# because these words also appear in the names of ordinary ARMOUR mods.
#
# "khajiit" silently dropped every khajiit ARMOUR mod in a 161-mod run, and the
# user reported it as "all khajiiti armor is invisible" -- an armour whose mesh
# was never converted, wearing an armature minted anyway. MEASURED over the live
# modlist: 35 enabled mods match one of these hints, and 33 of them have ZERO
# player-armour ARMA bases, so `require_arma` drops them WITHOUT any help from
# the name. The hint was therefore redundant for every mod it was written for
# and wrong for the two it was not.
#
# Same lesson as the retired "ube" hint below: a name is neither necessary nor
# sufficient. Evidence decides; the name only breaks ties where there is none.
_NONSOURCE_NAME_HINTS_BEAST = ("khajiit", "ohmes")
# Preserved as the union for the `scan` preview, which has no ESP parse and so
# has no evidence to weigh -- and for anything still importing this name.
_NONSOURCE_NAME_HINTS = _NONSOURCE_NAME_HINTS_HARD + _NONSOURCE_NAME_HINTS_BEAST
# "ube" was RETIRED from this list. It guarded against converting already-UBE
# armor, but it did so by matching the MOD NAME, which is neither necessary nor
# sufficient: mods shipping !UBE meshes under a name with no "ube" in it slipped
# straight through (measured: two on a real modlist), while an unrelated mod
# called "Custom Cubemaps" was excluded because "C-ube-maps" contains the
# substring. `_is_already_ube_model` replaces it with the exact signal -- the
# `!UBE\` path prefix that IS the UBE convention. Verified on a real pack before
# removing: every UBE armor mod keeps 100% of its meshes under !UBE, so the path
# gate covers everything the name hint did. The mods with meshes elsewhere are
# eyebrows, RaceMenu presets and NPC facegen, which the DefaultRace and
# body-slot gates already reject.


def _is_already_ube_model(model_path: str) -> bool:
    """True if an ARMA model path is ALREADY a converted/native UBE mesh.

    Converted output lives under `meshes\\!UBE\\...`, and UBE-native mods use the
    same convention, so a model whose first path segment is `!UBE` is UBE-shaped
    already. Refitting it onto the UBE body a second time double-converts it --
    and writes to `!UBE\\!UBE\\...`, which was found in real output.

    Path-based on purpose: an exact, deterministic signal needing no reference
    body and yielding no confidence level, unlike the name heuristic it replaces
    and the geometry-based `scan_ube_native` (GUI-advisory only)."""
    if not model_path:
        return False
    parts = [s for s in str(model_path).replace("\\", "/").split("/") if s]
    # Tolerate an authored leading "meshes\\" -- ARMA model paths are
    # meshes-relative, but real mods do ship the redundant prefix, and
    # nif_convert strips it elsewhere for exactly that reason.
    if parts and parts[0].strip().lower() == "meshes":
        parts = parts[1:]
    return bool(parts) and parts[0].strip().lower() == "!ube"

# Child-content mods: child-sized clothing for child NPCs. Matched as whole
# words so "kidskin" (a leather type) is never caught.
_CHILD_NAME_WORDS = frozenset({"kids", "kid", "children", "child"})


def _is_child_content_mod(name: str) -> bool:
    tokens = set("".join(c if c.isalnum() else " "
                         for c in name.lower()).split())
    return bool(tokens & _CHILD_NAME_WORDS)


# Child content reached by ASSET name rather than mod name. _is_child_content_mod
# only gates whole MOD FOLDERS, so it does nothing for the vanilla sweep (whose
# "mod" is the game Data dir) -- and vanilla ships child clothing bound to
# DefaultRace: ChildrenTorsoV01AA and ChildrenShoesAA both have RNAM 0x00000019
# and point at Clothes\ChildrenClothes\F\*. They therefore pass the DefaultRace
# gate legitimately and got converted. (ChildTorso01/02/03AA bind the child race
# 0x00013740 and were always correctly excluded -- only the DefaultRace-bound
# ones leak.) So gate on the ASSET too.
#
# Asset names are camelCase, so tokens must split on case boundaries as well as
# separators to see "ChildrenClothes" -> {children, clothes}. That splitting is
# deliberately NOT applied to mod folder names (which are space-separated and
# already work), and "kid" is deliberately NOT matched here: in a mesh path it is
# far more likely to be "kidskin" (a leather) or "kidney" than a child, and a
# camel split of "KidSkin" would false-positive. "child"/"children"/"kids" carry
# the signal with no such ambiguity.
_CHILD_ASSET_WORDS = frozenset({"child", "children", "kids"})


def _camel_tokens(text: str) -> "set[str]":
    """Lowercased tokens of `text`, split on BOTH non-alphanumerics and
    lowercase->uppercase boundaries, so 'Clothes\\ChildrenClothes\\F' yields
    {clothes, childrenclothes, children, f}."""
    out: "set[str]" = set()
    for word in "".join(c if c.isalnum() else " " for c in text).split():
        out.add(word.lower())
        start = 0
        for i in range(1, len(word) + 1):
            if i == len(word) or (word[i].isupper() and not word[i - 1].isupper()):
                if i > start:
                    out.add(word[start:i].lower())
                start = i
    return out


def _is_child_content_asset(*texts: "str | None") -> bool:
    """True if any of `texts` (an ARMA EDID, a model path) names child content."""
    return any(t and (_camel_tokens(t) & _CHILD_ASSET_WORDS) for t in texts)

# DefaultRace [RACE:00000019] in Skyrim.esm — the canonical humanoid race all
# player-equippable armor binds to. Creature/beast/custom races bind elsewhere.
_DEFAULT_RACE_LOW24 = 0x000019

# Biped slots that carry body-fitted geometry. Only these are converted; giving
# a non-body ARMA the UBE body races crashes the engine at actor setup (unskinned
# mesh used as a body-race armature -> ACCESS_VIOLATION). ALLOWLIST (not denylist)
# on purpose: a missed body slot means skipped armor (invisible), not a crash.
# Covers body(32), hands(33), forearms(34), feet(37), calves(38), modded chest
# (46/60), pelvis/skirt(49/52), legs(53-58). bit = 1 << (slot - 30).
_BODY_SLOT_BITS = sum(
    1 << (s - 30)
    for s in (32, 33, 34, 37, 38, 46, 49, 52, 53, 54, 55, 56, 57, 58, 60))

# Ambiguous modder slots: some mods put body cloth here (pants/skirts on 44/47),
# others put non-body accessories (beards, backpacks). Admitted as CANDIDATES only
# when the NIF is body-skinned (_nif_has_bodyfit_skin); unskinned accessories on
# these slots given a UBE body race CTD. Selection still uses the STRICT set.
_BODY_CANDIDATE_SLOT_BITS = sum(1 << (s - 30) for s in (44, 45, 47, 48, 59, 61))

# Draping capes/cloaks can ride hair/head slots (31/41/43) so equipping them
# hides the hair. The slot allowlist would otherwise exclude them; admit any mesh
# on an excluded slot whose filename contains a cloak keyword. The body-fit-skin
# crash guard drops mislabeled non-draping pieces.
_CLOAK_MESH_KEYWORDS = ("cape", "cloak", "mantle", "shroud", "cloth_cloak")

# Nude body skin basenames. A mod whose only DefaultRace ARMAs are these IS the
# body mod; don't convert it. The name alone does not make a model skin: real
# armour is sometimes named femalebody etc. (a pair of pants ships as
# femalebody_1.nif), so `_is_nude_body_skin_model` also reads where the model
# sits and what record uses it. #nude-basename-path
_BODY_SKIN_BASENAMES = frozenset({
    "femalebody", "malebody", "femalehands", "malehands",
    "femalefeet", "malefeet",
    "1stpersonfemalebody", "1stpersonmalebody",
    "1stpersonfemalehands", "1stpersonmalehands",
})

# Where the body skin lives: the game's character assets and every body or race
# mod that replaces them. #nude-basename-path
_BODY_SKIN_HOME = "actors/character/"


def _is_nude_body_skin_model(base: str, by_name_alone: bool = False,
                             named_item: bool = False) -> bool:
    """True if the weight-agnostic model key `base` is the nude body skin.

    #nude-basename-path (2026-09-24): the basename alone was the test, and "real
    armour pieces are never named femalebody" is false -- a pair of playable
    pants ships as `armor\\<set>\\pants\\femalebody_1.nif` and was never
    converted, while the coverage step gave it a UBE armature anyway, so the
    CBBE pants drew on the UBE body.

    The PATH alone is not the answer either. Measured over a real load order:
    114 armatures carry a skin-named model outside `actors\\character\\`, and
    108 of them are bodies -- 40 an NPC or race wears as its skin, 59 only
    nameless records use (a follower's unused body variants), 3 a named but
    non-playable record, 6 nothing. Taking the path alone admitted three such
    follower and race bodies as new "armour" (10 of the 15 bases it added were
    a live skin). The other 6 are armour: the pants and five robes and
    cuirasses whose first-person model is `1stpersonfemalebody`, each used by
    a PLAYABLE, NAMED armour record -- something a player can pick up and see
    by name, which a skin record never is.

    So a skin basename is skin under `actors\\character\\` (where the game and
    every body mod keep it), and anywhere else unless `named_item`: the
    armature is used by a playable armour record with a name (FULL) in its own
    plugin. `by_name_alone` is the old rule (CBBE2UBE_NO_NUDE_BASENAME_PATH=1)."""
    if base.rsplit("/", 1)[-1] not in _BODY_SKIN_BASENAMES:
        return False
    if by_name_alone or base.startswith(_BODY_SKIN_HOME):
        return True
    return not named_item


def _weight_agnostic_slot_map(nif_slot_map: "dict[str, int]") -> "dict[str, int]":
    """Fold a weight-specific NIF->slot-bits map (keyed by exact mesh path, which
    the ARMA gives only for the `_1` weight) into a weight-agnostic map keyed by
    `_weight_base_key`, OR-ing the slot bits of any `_0`/`_1` partners. Lets a
    `_0` file that the ARMA never named recover its slot bits from its `_1`
    partner, so slot-gated conversion behaves identically at both body weights.
    #slot0-weight-partner"""
    out: "dict[str, int]" = {}
    for k, v in (nif_slot_map or {}).items():
        bk = _weight_base_key(k)
        out[bk] = out.get(bk, 0) | int(v)
    return out


def _make_slot_resolver(nif_slot_map: "dict[str, int]"):
    """Return ``slot_bits_for(rel) -> int``: the exact-path slot bits, falling
    back to the weight-agnostic (OR'd ``_0``/``_1``) bits so a ``_0`` file the
    ARMA never named inherits its ``_1`` partner's slots.

    This is the SINGLE source of truth for NIF->slot lookups. Both the ambiguous-
    slot crash guard and the work-item builder call it, so a `_0` file can never
    silently convert with ``biped_slots=0`` and diverge from its `_1` partner
    (which zeroed every slot-gated path -- torso_parity, slot-aware inflation /
    reskin band / scale reach, the calf/foot-boot far-thigh exclusion). Slots are
    a per-garment property (Skyrim has no per-weight slots), so folding is
    correct by construction, not a heuristic. #slot0-weight-partner"""
    agnostic = _weight_agnostic_slot_map(nif_slot_map)

    def slot_bits_for(rel: str) -> int:
        return (nif_slot_map.get(rel.lower(), 0)
                or agnostic.get(_weight_base_key(rel), 0))

    return slot_bits_for


def _weight_base_key(rel: str) -> str:
    """Normalize a NIF path to a weight-agnostic key for matching an ARMA's
    model path against a file on disk: lowercase, forward slashes, no leading
    `meshes/`, no `.nif`, and no trailing `_0`/`_1` weight suffix (the engine
    derives `_0` from the `_1` the ARMA names, so both map to one base)."""
    s = rel.replace("\\", "/").lower().lstrip("/")
    if s.startswith("meshes/"):
        s = s[len("meshes/"):]
    if s.endswith(".nif"):
        s = s[:-4]
    if s.endswith("_0") or s.endswith("_1"):
        s = s[:-2]
    return s


def _meshes_rel(p: Path) -> str:
    """Path under the nearest ``meshes\\`` ancestor, forward-slash, ORIGINAL
    case (e.g. ``'armor/modname/ArmorPiece_1.nif'``). Falls
    back to the bare filename if no ``meshes`` component is present."""
    parts = p.parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == "meshes":
            return "/".join(parts[i + 1:])
    return p.name


def _complete_weight_partners(output_dir: "str | Path",
                              source_variants: "dict | None" = None,
                              skip_bases=()):
    """Safety net (#180): Skyrim needs BOTH ``_0`` and ``_1`` on disk for a
    weighted body mesh -- it derives the absent weight from the present one's
    PATH, so a missing partner makes the piece break / vanish at that body
    weight. A multi-source first-writer-wins collision (a path claimed at
    work-item build time whose sibling conversion then fails) can leave only one
    weight written. After the whole batch, scan the ``!UBE`` output and, for any
    base that has only one weight, COPY the present weight to the missing partner
    so the piece renders at every weight.

    The copied partner is identical geometry (no weight-morph between _0/_1 for
    those pieces) -- acceptable versus the missing-partner breakage, and the
    common case (the user's heavy-preset actors sit near weight 100, using _1).
    Meshes shipped weight-agnostic (``name.nif`` with no ``_0``/``_1``) don't
    match and are untouched.

    #stale-weight-partner. THE FILL USED TO HAPPEN ONCE, EVER: the copy was
    guarded by `if miss.exists(): continue`, so a partner written by an OLD
    build was never refreshed. Every later run rewrote the real half and left
    the copy alone, and the two halves of one piece came from converter builds
    WEEKS apart -- the engine blends between them by body weight. Measured on
    the 2026-09-06 pack: 11 of 1536 pairs were 12-15 DAYS apart, every one a
    piece whose source ships only `_1`. It also poisons any pack-wide census:
    4 of the 6 zero-weight bones in that pack sat on those stale files, so they
    read as the current build's defects and were not.

    `source_variants` ({dest base -> the weight suffixes the SOURCE ships}) is
    what makes refreshing SAFE. A partner is refreshed ONLY when the source has
    no such variant, i.e. it is a copy this function made rather than a mesh
    the converter produced. **Dropping the guard without that test would
    overwrite a legitimately converted low-weight `_0` with the high one,
    pack-wide, silently deleting the low-weight shape.** With no
    `source_variants` nothing is ever refreshed and the behaviour is exactly
    as before.

    `skip_bases` (same keys) are bases the run left to a mod that ships them
    built for UBE: nothing is filled or refreshed there, or our stale copy would
    land at the builder's path again. #supersede-whole-base

    Returns ``(filled, refreshed)``."""
    import re as _re
    ube_root = Path(output_dir) / "meshes" / "!UBE"
    if not ube_root.is_dir():
        return 0, 0
    groups: "dict[tuple, dict]" = {}
    for p in ube_root.glob("**/*.nif"):
        m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
        if m:
            groups.setdefault((str(p.parent).lower(), m.group(1).lower()),
                              {})[m.group(2)] = p
    from .atomic_io import atomic_copy
    filled = refreshed = 0
    for have in groups.values():
        both = "0" in have and "1" in have
        anchor = have.get("1") or have.get("0")
        try:
            base = _weight_base_key(anchor.relative_to(ube_root).as_posix())
        except ValueError:
            base = None
        if base is not None and base in skip_bases:
            continue              # left to its builder
        src_sufs = (source_variants or {}).get(base)
        if both:
            # Refresh a partner this function FILLED. Only the source can say
            # which one that is; without it, never touch an existing file.
            if not src_sufs:
                continue
            stale_w = next((w for w in ("0", "1")
                            if f"_{w}" not in src_sufs), None)
            if stale_w is None:
                continue          # the source ships both -> both are real
            real = have["1" if stale_w == "0" else "0"]
            stale = have[stale_w]
            try:
                if (stale.stat().st_size == real.stat().st_size
                        and stale.read_bytes() == real.read_bytes()):
                    continue      # already a current copy; leave the mtime alone
                atomic_copy(real, stale)
                refreshed += 1
            except OSError:
                pass
            continue
        present = anchor
        miss_w = "0" if "1" in have else "1"
        miss = present.parent / _re.sub(
            r"_[01]\.nif$", f"_{miss_w}.nif", present.name, flags=_re.IGNORECASE)
        if miss.exists():
            continue
        try:
            atomic_copy(present, miss)
            filled += 1
        except OSError:
            pass
    return filled, refreshed


def _nif_invariant_issues(nif_name, shapes, cap) -> "list[str]":
    """Postflight per-NIF invariant violations on the FINAL output: ZERO-vertex
    shapes (invisible/degenerate) and over-cap shapes left in <=1 partition (the
    GPU skin-partition split failed -> equip CTD). Returns issue strings. Pure +
    duck-typed so it's unit-testable without a real NIF."""
    issues: "list[str]" = []
    for s in shapes:
        nm = getattr(s, "name", "?")
        try:
            nv = len(s.verts)
        except Exception:
            nv = 1
        if nv == 0:
            issues.append(f"{nif_name} :: {nm}: ZERO-vertex shape "
                          "(invisible/degenerate)")
        nb = len(getattr(s, "bone_names", None) or [])
        npart = len(getattr(s, "partitions", None) or [])
        if nb > cap and npart <= 1:
            issues.append(f"{nif_name} :: {nm}: {nb} bones in {npart} "
                          f"partition(s) (> {cap}-bone GPU cap; split failed "
                          "-> equip CTD risk)")
    return issues


def _postflight_missing_weight_partners(output_dir) -> "list[str]":
    """Postflight: scan the WHOLE output mesh tree for a body-mesh base that has
    a `_0` but no `_1` (or vice versa). Skyrim derives the absent weight from the
    present one's PATH, so a missing partner makes the piece vanish at that body
    weight. DETECT-only (warn): unlike the !UBE fixer we must NOT copy a partner
    for general armor, where _0/_1 can legitimately differ by weight."""
    import re as _re
    meshes = Path(output_dir) / "meshes"
    if not meshes.is_dir():
        return []
    groups: "dict[tuple, set]" = {}
    for p in meshes.glob("**/*.nif"):
        m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
        if m:
            groups.setdefault((str(p.parent).lower(), m.group(1).lower()),
                              set()).add(m.group(2))
    out: "list[str]" = []
    for (parent, base), have in sorted(groups.items()):
        if len(have) == 1:
            present = next(iter(have))
            miss = "1" if present == "0" else "0"
            out.append(f"{base}_{present}.nif present but _{miss} MISSING in "
                       f"{parent} (invisible at body weight {miss})")
    return out


def _scale_bone_vert_counts(shape, eps: float = 1e-4) -> "dict[str, int]":
    """Per-scale-bone count of verts weighted above `eps` on a shape.
    #slot0-weight-partner"""
    from .nif_convert import _is_scale_bone
    bw = getattr(shape, "bone_weights", None) or {}
    out: "dict[str, int]" = {}
    for bn in getattr(shape, "bone_names", None) or []:
        if not _is_scale_bone(bn):
            continue
        pairs = bw.get(bn) or []
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        n = sum(1 for _, w in pl if w > eps)
        if n:
            out[bn] = n
    return out


def _scale_bone_peak_weights(shape, eps: float = 1e-4) -> "dict[str, float]":
    """Per-scale-bone HEAVIEST weight on a shape -- how much the bone actually
    moves the mesh, as opposed to how many verts it touches at all.

    The vert count alone cannot tell a real divergence from a rounding artefact.
    Measured on a full conversion: all 17 flagged divergences carried a peak weight
    of 0.114 or less (median 0.025) -- e.g. a collar shape 20 units above the bust
    picking up 22 verts of `L Breast01` at 2%, which moves nothing a player can see,
    yet scored identically to a bone at 90%.  #slot0-weight-partner"""
    from .nif_convert import _is_scale_bone
    bw = getattr(shape, "bone_weights", None) or {}
    out: "dict[str, float]" = {}
    for bn in getattr(shape, "bone_names", None) or []:
        if not _is_scale_bone(bn):
            continue
        pairs = bw.get(bn) or []
        pl = pairs.tolist() if hasattr(pairs, "tolist") else pairs
        peak = max((float(w) for _, w in pl if w > eps), default=0.0)
        if peak > 0.0:
            out[bn] = peak
    return out


def _weight_partner_scale_divergence(
        shapes0, shapes1, base_label: str,
        present_min: int = 8, absent_max: int = 1,
        weight_min: float = 0.10) -> "list[str]":
    """Compare the scale-bone weighting of SAME-NAMED shapes across a `_0`/`_1`
    pair and report only a true PRESENCE/ABSENCE leak: a bone substantially
    present (>= `present_min` verts) in one weight and effectively ABSENT
    (<= `absent_max` verts) in the other. That gross asymmetry is the signature
    of per-file metadata (slot bits, ...) leaking to ONE weight -- the two are
    the same garment and must morph identically. Deliberately does NOT flag a
    smooth slim-vs-curvy gradient (e.g. 7 vs 50 verts): the graft reaches
    slightly different vert counts at each body weight, which is expected, not a
    bug. Pure + duck-typed so it's unit-testable without a real NIF.

    `weight_min` additionally requires the bone to MOVE the mesh on the side it is
    present. Vert count alone proved to be pure noise: on a full conversion all 17
    divergences peaked at <= 0.114 (median 0.025) -- inert bones a graft brushed at
    2%, not metadata leaks -- so 19 warnings fired and not one was actionable.
    #slot0-weight-partner"""
    by0 = {getattr(s, "name", None): s for s in shapes0}
    issues: "list[str]" = []
    for s1 in shapes1:
        nm = getattr(s1, "name", None)
        s0 = by0.get(nm)
        if s0 is None:
            continue
        c0 = _scale_bone_vert_counts(s0)
        c1 = _scale_bone_vert_counts(s1)
        p0 = _scale_bone_peak_weights(s0)
        p1 = _scale_bone_peak_weights(s1)
        only0, only1 = [], []
        for bn in set(c0) | set(c1):
            n0, n1 = c0.get(bn, 0), c1.get(bn, 0)
            # ...and the bone must actually MOVE the mesh on the side it is present.
            # Without this the check fires on bones that are present-but-inert: every
            # divergence on a real conversion (17/17) peaked at <= 0.114, median
            # 0.025, so the warning was 100% noise and trained the reader to skip it.
            if n0 >= present_min and n1 <= absent_max and p0.get(bn, 0.0) >= weight_min:
                only0.append(bn)
            elif n1 >= present_min and n0 <= absent_max and p1.get(bn, 0.0) >= weight_min:
                only1.append(bn)
        if only0 or only1:
            det = []
            if only0:
                det.append(f"_0-only={sorted(only0)}")
            if only1:
                det.append(f"_1-only={sorted(only1)}")
            issues.append(f"{base_label} :: {nm}: scale-bone divergence between "
                          f"body weights ({'; '.join(det)})")
    return issues


def _postflight_weight_partner_divergence(output_dir) -> "list[str]":
    """Postflight: for each `_0`/`_1` pair that BOTH exist, flag same-named shapes
    whose substantial scale-bone set differs between the two weights. Catches
    per-file metadata (slot bits, ...) leaking to only one weight so the two
    convert differently -- the class of bug that let GTO `boots_0` keep the
    fade-inducing far-thigh scale bones while `boots_1` dropped them. DETECT-only
    (warn). Read-only NIF loads; skipped entirely on any pynifly failure.
    #slot0-weight-partner"""
    import re as _re
    meshes = Path(output_dir) / "meshes"
    if not meshes.is_dir():
        return []
    try:
        pyn = nif_convert._pynifly()
    except Exception:
        return []
    groups: "dict[tuple, dict]" = {}
    for p in meshes.glob("**/*.nif"):
        m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
        if m:
            groups.setdefault((str(p.parent), m.group(1)), {})[m.group(2)] = p
    out: "list[str]" = []
    for (parent, base), byw in sorted(groups.items()):
        if "0" not in byw or "1" not in byw:
            continue
        n0 = n1 = None
        try:
            n0 = pyn.NifFile(filepath=str(byw["0"]))
            n1 = pyn.NifFile(filepath=str(byw["1"]))
        except Exception:
            for _f in (n0, n1):
                if _f is not None:
                    nif_io.release_nif(_f)
            continue
        try:
            label = byw["1"].relative_to(meshes).as_posix()
        except Exception:
            label = f"{base}_1.nif"
        try:
            out.extend(_weight_partner_scale_divergence(
                list(n0.shapes), list(n1.shapes), label))
        finally:
            # Released by reference counting HERE, not at the next full
            # collection: this read-only pass held 23 MB per pair until then
            # in the batch parent (MEASURED 2026-09-17). #postflight-release
            nif_io.release_nif(n0)
            nif_io.release_nif(n1)
    return out


def _postflight_sync_weight_partner_jiggle(output_dir) -> int:
    """REPAIR the divergence `_postflight_weight_partner_divergence` detects:
    walk every `_0`/`_1` pair and give the deficient weight its partner's jiggle
    bone. The mechanism, the evidence for copying the partner's skin-to-bone
    xform, and why UNION rather than removal is the right resolution are all in
    `nif_convert._sync_weight_partner_jiggle`.

    Pairs are grouped exactly as the detector groups them, so the two agree on
    what a pair IS. Returns verts changed; a pair that raises is skipped rather
    than aborting the batch. #weight-partner-jiggle-sync"""
    import re as _re
    meshes = Path(output_dir) / "meshes"
    if not meshes.is_dir():
        return 0
    groups: "dict[tuple, dict]" = {}
    for p in meshes.glob("**/*.nif"):
        m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
        if m:
            groups.setdefault((str(p.parent), m.group(1)), {})[m.group(2)] = p
    total = 0
    for _key, byw in sorted(groups.items()):
        if "0" not in byw or "1" not in byw:
            continue
        try:
            total += nif_convert._sync_weight_partner_jiggle(
                byw["0"], byw["1"])
        except Exception:
            continue
    return total


def _tail_fold() -> bool:
    """#tail-fold (2026-09-25): does the end of the run sync the `_0`/`_1`
    jiggle bones and check the pairs for divergence in ONE walk, loading each
    pair once? Yes, by default.

    The two were serial walks over every pair of the whole output, each loading
    both files: 150 s for the sync and 72 s for the check on the reported
    modlist (3342 NIFs, read-only replay), in the batch parent after the last
    source. The check is detect-only and reads the state the sync leaves, so it
    can run on the files the sync already holds whenever the sync left them as
    they are on disk. CBBE2UBE_NO_TAIL_FOLD=1 runs the two serial walks again."""
    return not _flag("CBBE2UBE_NO_TAIL_FOLD", False)


def _pass_failures_noted() -> int:
    """How many pass failures this process has recorded so far. #tail-fold"""
    return sum(nif_convert.pass_failure_summary().values())


def _postflight_weight_partner_fold(output_dir, check: bool):
    """`_postflight_sync_weight_partner_jiggle`, then (when `check`)
    `_postflight_weight_partner_divergence`, in one walk. #tail-fold

    Returns (verts synced, divergence findings) -- the two serial passes'
    results, in their order -- or None when switched off or when the pairs
    cannot even be listed; the caller then runs the two serial passes, as
    before, and nothing has been touched yet. When the CHECK raises on a pair,
    the findings are that exception instead of a list: the serial check
    raised it too and reported nothing, while the sync had already walked every
    pair -- so the fold stops checking and goes on syncing.

    THE PAIR IS LOADED ONCE. The check reads the files the sync opened when
    the sync left them exactly as they are on disk: it changed no vert and
    recorded no failure. Otherwise (it wrote the pair, or saved one side and
    failed on the other) the pair is read from disk again, so the check
    always sees what ships, as the serial check did. The open copy cannot
    stand in for the disk after an edit: its shapes still read the bones and
    weights from before the graft (measured on a synthetic pair), so it would
    report the very divergence the sync just repaired. Pairs are
    grouped, ordered and skipped exactly as both serial passes do it, and a
    pair whose sync raises is skipped, as the serial sync skips it."""
    if not _tail_fold():
        return None
    import re as _re
    meshes = Path(output_dir) / "meshes"
    sync = bool(nif_convert.WEIGHT_PARTNER_JIGGLE_SYNC)
    try:
        if not meshes.is_dir() or not (sync or check):
            return 0, []
        # Grouped exactly as both serial passes group them.
        groups: "dict[tuple, dict]" = {}
        for p in meshes.glob("**/*.nif"):
            m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
            if m:
                groups.setdefault((str(p.parent), m.group(1)), {})[m.group(2)] = p
    except Exception:
        return None
    return _weight_partner_fold_walk(meshes, sorted(groups.items()), sync, check)


def _weight_partner_fold_walk(meshes, pairs, sync: bool, check: bool):
    """The walk behind `_postflight_weight_partner_fold`. #tail-fold"""
    from .nif_convert_weights import _sync_weight_partner_jiggle_loaded
    try:
        pyn, open_err = nif_convert._pynifly(), None
    except Exception as _pe:
        pyn, open_err = None, _pe
    total = 0
    out: "list[str]" = []
    check_err = None

    def _open(byw):
        nf: dict = {}
        try:
            if pyn is None:
                raise open_err
            nf["0"] = pyn.NifFile(filepath=str(byw["0"]))
            nf["1"] = pyn.NifFile(filepath=str(byw["1"]))
        except Exception:
            for _f in nf.values():
                nif_io.release_nif(_f)
            raise
        return nf

    for (_parent, base), byw in pairs:
        if "0" not in byw or "1" not in byw:
            continue
        try:
            nf = _open(byw)
        except Exception as _oe:
            if sync:
                # What the serial sync records for a pair it cannot open; the
                # serial check skipped such a pair silently.
                nif_convert._note_pass_failure(
                    "_sync_weight_partner_jiggle/open", _oe)
            continue
        try:
            reread = False
            if sync:
                failed_before = _pass_failures_noted()
                try:
                    n = _sync_weight_partner_jiggle_loaded(
                        byw["0"], byw["1"], nf)
                except Exception:
                    n, reread = 0, True
                total += n
                if n > 0 or _pass_failures_noted() != failed_before:
                    reread = True
            if not check:
                continue
            if reread:
                for _f in nf.values():
                    nif_io.release_nif(_f)
                nf = {}
                try:
                    nf = _open(byw)
                except Exception:
                    continue
            try:
                label = byw["1"].relative_to(meshes).as_posix()
            except Exception:
                label = f"{base}_1.nif"
            try:
                out.extend(_weight_partner_scale_divergence(
                    list(nf["0"].shapes), list(nf["1"].shapes), label))
            except Exception as _ce:
                # The serial check died here, reporting nothing; the serial
                # sync had finished. Check no more pairs; sync the rest.
                check_err, check = _ce, False
                if not sync:
                    break
        finally:
            # Released by reference counting per pair. #postflight-release
            for _f in nf.values():
                nif_io.release_nif(_f)
    return total, (check_err if check_err is not None else out)


_BATCH_BSA_INDEX = None   # set per-batch by _cmd_convert; lazy BSA mesh resolver


def _load_order_bsa_dirs(mods_root, enabled_ordered, game_data_dirs) -> "list[Path]":
    """The folders whose archives `_BsaMeshIndex` lists, in the order the first
    hit wins: every enabled mod by MO2 priority, then the game Data dir(s) LAST.
    The vanilla mesh archives back the sweep, but any mod BSA shipping the same
    path wins (first hit in _scan), matching MO2 priority. ONE definition, so
    source selection and the convert step index the same archives.
    #bsa-only-sources"""
    dirs = [Path(mods_root) / n for n in (enabled_ordered or ())]
    dirs += [Path(d) for d in (game_data_dirs or []) if Path(d) not in dirs]
    return dirs


class _BsaMeshIndex:
    """Load-order-wide fallback resolver: extracts armour meshes from mod BSAs
    (bespoke-armor mods, quest mods, ...) when they aren't loose anywhere. Consulted only
    after the VFS + source-local lookups miss. Lazy: BSA scan on first miss only.
    Voice/sound/facegen BSAs are skipped. Archive data buffers are released after
    listing; only BSAs with needed meshes are re-opened for extraction."""

    # #texture-archive-meshes (2026-09-24): "texture" used to lead this list, and
    # it hid real armour. A large content mod keeps ALL its meshes in
    # "<name> - Textures.bsa" (3,224 + 1,411 .nif in its two archives; the plain
    # "<name>.bsa" holds scripts and sound), and the substring also caught two
    # "... Retexture SE.bsa" armour archives. Measured on a real modlist: 143 more
    # archives listed (406, +4,506 mesh paths, 0.6 -> 0.7 s warm); 21 NIFs added
    # -- three suffixless world meshes the game draws (iron boots, two leather
    # pieces) and 18 first-person models -- 2 male fallbacks dropped because the
    # female mesh in such an archive now resolves, 16 armatures the coverage step
    # had been minting over an unconverted mesh now get a converted one, and 21
    # already-converted pieces of one armour set now come from the higher-priority
    # retexture archive -- the copy the game loads. No decision behind the old
    # entry was ever recorded; it looked like a cost shortcut.
    # CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES=1 skips them again.
    _SKIP_BSA = ("voice", " sound", "sounds", "- snd", "facegen")
    _SKIP_BSA_TEXTURE = ("texture",)

    def __init__(self, enabled_mod_dirs, staging_dir,
                 bsa_name_prefixes=None, skip_bsa=None):
        self._dirs = list(enabled_mod_dirs)   # MO2 priority order (highest first)
        # A caller asking "does this mesh exist AT ALL" passes its own skip list:
        # a mesh shipped in a texture-named archive still exists in game.
        if skip_bsa is not None:
            self._SKIP_BSA = tuple(skip_bsa)
        # None = lookup-only: extract() refuses, so the index can never write a
        # file (the setup check builds one this way). #tool-folder-only
        self._staging = Path(staging_dir) if staging_dir is not None else None
        self._index = None                    # rel_lower -> (bsa_path, internal_name)
        self._open: dict = {}                 # bsa_path -> BSAArchive (extract cache)
        self._out: dict = {}                  # rel_lower -> (Path, rel) | None
        # Optional archive-name allowlist (lowercase prefixes). The setup-check
        # probe uses it to scan ONLY the vanilla archives: under MO2's usvfs
        # the game Data dir lists EVERY enabled mod's BSAs (330 vs 6 observed).
        self._name_prefixes = ([p.lower() for p in bsa_name_prefixes]
                               if bsa_name_prefixes else None)
        # Read once, here: an index lists its archives under ONE rule for its
        # whole life. #texture-archive-meshes. The switch governs the DEFAULT
        # list only: a caller's own list is its whole rule, so the switch cannot
        # make the coverage step's "does this mesh exist in game" lookup stop
        # seeing texture archives (the two lanes met there, 2026-09-24).
        self._skip = (self._SKIP_BSA_TEXTURE + self._SKIP_BSA
                      if skip_bsa is None
                      and _flag("CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES", False)
                      else self._SKIP_BSA)

    def listing_key(self) -> tuple:
        """What this index's listing depends on: the archive folders in order and
        the two name filters. Two indexes with the same key list the same paths
        from the same archives. #bsa-only-sources"""
        return (tuple(str(d).lower() for d in self._dirs), self._skip,
                tuple(self._name_prefixes or ()))

    def adopt_listing(self, other) -> bool:
        """Take `other`'s archive listing instead of scanning again, when `other`
        has scanned and lists exactly what this index would. Extraction state is
        not shared: `other` may be lookup-only. Returns True if adopted.
        #bsa-only-sources"""
        if (other is None or other is self or other._index is None
                or self._index is not None
                or other.listing_key() != self.listing_key()):
            return False
        self._index = other._index
        return True

    def _scan(self) -> None:
        from .bsa_strings import BSAArchive
        import sys as _s
        self._index = {}
        n = 0
        for d in self._dirs:
            try:
                bsas = sorted(d.glob("*.bsa"))
            except Exception:
                continue
            for bsa in bsas:
                if any(k in bsa.name.lower() for k in self._skip):
                    continue
                if (self._name_prefixes is not None
                        and not any(bsa.name.lower().startswith(p)
                                    for p in self._name_prefixes)):
                    continue
                try:
                    arch = BSAArchive(bsa, eager=False)   # table-only: cheap list
                    files = arch.list_files()
                except Exception:
                    continue
                for f in files:
                    fl = f.lower().replace("\\", "/")
                    if not fl.endswith(".nif"):
                        continue
                    rel = fl[7:] if fl.startswith("meshes/") else fl
                    self._index.setdefault(rel, (bsa, f))
                arch._data = b""              # release the (table) buffer
                n += 1
        print(f"  BSA fallback index: scanned {n} mesh archive(s) -> "
              f"{len(self._index)} mesh path(s) available", file=_s.stderr)

    def contains(self, key: str) -> bool:
        """True if `key` (lowercase meshes-rel) is available for extraction.
        Index lookup only — nothing is extracted."""
        try:
            if self._index is None:
                self._scan()
            return key in self._index
        except Exception:
            return False

    def read_bytes(self, key: str) -> "bytes | None":
        """key = lowercase meshes-rel. The mesh's bytes from the archive that
        lists it, read into memory -- nothing is written, so a lookup-only index
        may use it. None if no archive lists it or it cannot be read.
        #coverage-female-standin"""
        try:
            if self._index is None:
                self._scan()
            hit = self._index.get(key)
            if hit is None:
                return None
            from .bsa_strings import BSAArchive
            arch = self._open.get(hit[0])
            if arch is None:
                arch = BSAArchive(hit[0], eager=False)   # table-only; seek-read
                self._open[hit[0]] = arch
            return arch.read_file(hit[1]) or None
        except Exception:
            return None

    def extract(self, key: str):
        """key = lowercase meshes-rel (e.g. 'armor/x/cuirass_1.nif').
        Returns (extracted_file, meshes_rel) or None."""
        if self._index is None:
            self._scan()
        if key in self._out:
            return self._out[key]
        hit = self._index.get(key)
        if hit is None:
            self._out[key] = None
            return None
        if self._staging is None:
            self._out[key] = None       # lookup-only index: never writes
            return None
        from .bsa_strings import BSAArchive
        bsa_path, internal = hit
        arch = self._open.get(bsa_path)
        if arch is None:
            try:
                # Table-only: read_file seeks to the entry. Opened eagerly, each
                # archive a mesh came from stayed in memory for the rest of the
                # batch (1.1 GB for the base-game mesh archive). #bsa-seek-read
                arch = BSAArchive(bsa_path, eager=False)
            except Exception:
                self._out[key] = None
                return None
            self._open[bsa_path] = arch
        try:
            data = arch.read_file(internal)
        except Exception:
            data = None
        if not data:
            self._out[key] = None
            return None
        rel = internal.replace("\\", "/")
        rel = rel[7:] if rel.lower().startswith("meshes/") else rel
        out = self._staging / "meshes" / rel
        # SECURITY: the BSA internal name is attacker-controlled; refuse any
        # `..`/absolute traversal that would write outside the staging dir.
        if not paths.is_within_dir(self._staging / "meshes", out):
            warn(f'BSA extract: refusing traversal path "{internal}"',
                 consequence="the archive entry points outside the extraction folder "
                             "and was skipped",
                 file=sys.stderr)
            self._out[key] = None
            return None
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        except Exception:
            self._out[key] = None
            return None
        self._out[key] = (out, rel)
        return self._out[key]


def _resolve_armor_meshes(
    armor_bases: "set[str]",
    mesh_vfs_index: "dict[str, Path] | None",
    meshes_root: "Path | None",
    all_nif_paths: "list[Path]",
) -> "list[tuple[Path, str]]":
    """Resolve each armour-piece mesh base to a concrete ``(source_file, rel)``
    pair, where ``rel`` is the ``meshes\\``-relative path (original case,
    forward-slash) the converted NIF will be written to under ``!UBE\\``.

    For each base, the three weight variants ``_1`` / ``_0`` / <none> are tried.
    Resolution prefers the **full-VFS winner** (``mesh_vfs_index``) — the file
    the GAME actually loads, which may live in a BodySlide-output / replacer /
    patch mod rather than the armour's own source folder (this is the coverage
    broadening). It falls back to the source mod's own meshes when no VFS index
    is supplied (tests, or an unreadable modlist).

    With NO ``armor_bases`` (an ESP-less / unclassifiable source) the legacy
    behaviour is kept: convert every NIF the source mod itself ships.
    """
    # source-local lookup: meshes-rel(lower) -> (abs, rel_original_case)
    local: "dict[str, tuple[Path, str]]" = {}
    if meshes_root is not None:
        for p in all_nif_paths:
            rel_real = p.relative_to(meshes_root).as_posix()
            local[rel_real.lower()] = (p, rel_real)

    if not armor_bases:
        return list(local.values())

    pairs: "list[tuple[Path, str]]" = []
    seen: "set[str]" = set()
    for base in sorted(armor_bases):
        for suf in ("_1", "_0", ""):
            key = f"{base}{suf}.nif"
            if key in seen:
                continue
            hit: "tuple[Path, str] | None" = None
            if mesh_vfs_index is not None:
                abs_src = mesh_vfs_index.get(key)
                if abs_src is not None:
                    hit = (abs_src, _meshes_rel(abs_src))
            if hit is None:
                hit = local.get(key)
            if hit is None and _BATCH_BSA_INDEX is not None:
                ex = _BATCH_BSA_INDEX.extract(key)
                if ex is not None:
                    hit = ex
            if hit is not None:
                seen.add(key)
                pairs.append(hit)
    return pairs


def _near_mod_names(missing: str, all_names: "list[str]", n: int = 3) -> "list[str]":
    """Mod names close to one `--only-mods` value that matched nothing.

    A bare "NOT FOUND" sent the reader to `scan`, which lists mods that merely
    LOOK like armour -- a different, wider set than the plugin-driven
    CONVERSION candidates this flag filters. A mod can appear in one and not
    the other, and chasing that cost three arms on 2026-09-07.

    Case-insensitive, and returns the names in their REAL casing so the answer
    can be pasted straight back into the flag.
    """
    import difflib
    low = {name.lower(): name for name in all_names}
    hits = difflib.get_close_matches(missing.lower(), list(low), n=n, cutoff=0.4)
    return [low[h] for h in hits]


def _variant_sources_by_base(
    resolved_pairs: "list[tuple[Path, str]]",
) -> "dict[str, dict[str, str]]":
    """Group resolved sources by weight-agnostic stem: {base -> {suffix -> src}}.

    WHO WRITES EACH `.tri`, answered where SOURCES are resolved. `foo.nif`,
    `foo_0.nif` and `foo_1.nif` all derive `foo.tri`, so exactly one of them may
    write it and the others may only point at it when their vertex counts agree
    (`_tri_is_owning_variant` / `_tri_fits_variant`). #tri-variant-collision

    THE FIRST FIX FOR THIS ASKED THE FILESYSTEM NEXT TO THE SOURCE, AND WAS
    INERT: the competing variant routinely ships in a DIFFERENT MOD -- a vanilla
    BSA ships the no-suffix model alone while a body-replacer ships the
    `_0`/`_1` pair -- and a BSA-resolved source is staged ALONE, so the probe saw
    no sibling and every variant claimed the TRI. The same five out-of-bounds
    entries shipped after it.

    `resolved_pairs` is the right source of truth because it came out of the
    SAME mod list / VFS / BSA chain that found the file being converted, so it
    sees across mods by construction. It has to be computed HERE, in the parent:
    a conversion worker is a spawned process that inherits none of those indices,
    and the destination siblings it could see instead are being written by this
    same batch.

    A VARIANT ONLY COUNTS IF IT IS ACTUALLY GOING TO BE CONVERTED. Deferring to
    a `_0` that yields nothing at the destination would leave the whole stem with
    no TRI and no BODYTRI -- morphs lost outright, which is worse than the
    collision -- so an already-UBE model is dropped here. Callers pass the
    CRASH-GUARDED pairs (a dropped accessory converts nothing and writes no TRI)
    but do not pre-apply the collision or incremental skips, whose destinations
    exist regardless of which source wrote them.
    """
    out: "dict[str, dict[str, str]]" = {}
    for src, rel in resolved_pairs:
        if _is_already_ube_model(rel):
            continue
        stem = Path(rel).stem
        suffix = stem[-2:] if stem.endswith(("_0", "_1")) else ""
        out.setdefault(_weight_base_key(rel), {})[suffix] = str(src)
    return out


# #worker-mem-budget -- steady-state private footprint of ONE conversion worker.
# Measured on a real 3800-mod pack mid-run: 25 worker processes held 58.9 GB of
# private bytes (~2.4 GB each). 2.0 is deliberately a little under the measured
# figure: workers do not all peak together, and the cap is a floor on headroom,
# not an allocation.
WORKER_MEM_BUDGET_GB = 2.0

# #commit-headroom -- WINDOWS COMMIT CHARGE, a different quantity from
# WORKER_MEM_BUDGET_GB above and used for a different job. The budget bounds RAM
# and decides whether the box THRASHES; these bound commit and decide whether an
# allocation FAILS.
#
# MEASURED 2026-09-11 against a real 3,849-mod pack, PrivateUsage sampled from
# outside each process, BLAS already capped. These are no longer derived by
# subtraction; the earlier 1.0 provisional figure WAS, and it was 1.7-3.8x too
# low:
#
#   worker steady floor                              0.36 GB
#   worker steady, converting                        0.6 - 1.3 GB
#   worker peak, 97-mesh sample                      1.05 - 1.18 GB
#   worker peak, 419-mesh sample                     1.66 - 1.98 GB
#   worker peak, largest single source (14 MB)       3.79 GB
#   parent peak (postflight, after workers exit)     2.7 - 2.8 GB
#     (3.57 GB on a 76-NIF mod by 2026-09-17: the two postflight passes kept
#     every pair they opened until a full collection, 55 MB per weight pair
#     and unbounded by the tree; released per pair since -- see
#     nif_io.release_nif -- the parent stays near its end-of-batch level,
#     and the "after the batch:" log line reads it on every run. 3.0 errs
#     high, which the note below says is the safe side.)
#   GUI, idle 0.15 GB / after one mod-list refresh   0.71 GB
#
# Peak tracks source mesh size at roughly 236 MB per source MB, so the 3.79 GB
# outlier is one 14 MB NIF and not the common case; 2.0 covers the sampled peaks
# without pricing every worker at the worst mesh in a pack.
#
# WHICH WAY IS SAFE, stated per consumer because they differ and an earlier
# version of this comment gave only one of them:
#   * as the DIVISOR in the guard below, erring low yields TOO MANY workers;
#   * as the MULTIPLIER in `memory_plan`, erring low UNDERSTATES the projection,
#     which suppresses the LOW MEMORY banner, the preflight warning and the
#     figure in conversion_report.json.
# Low is dangerous in both. Erring high only costs throughput.
WORKER_COMMIT_GB = 2.0

# The two processes that live for the whole run beside the pool. Priced
# explicitly because the old `+2 workers` term charged them WORKER_COMMIT_GB
# each, and they are not workers: the parent peaks in the postflight block
# AFTER every worker has exited, and the GUI holds its mod-list scan for the
# whole session.
PARENT_COMMIT_GB = 3.0
GUI_COMMIT_GB = 0.75

# Share of free commit the guard may plan against. 0.9, not 0.8: the constants
# above are now PEAK figures rather than optimistic ones, so a second large
# haircut would double-count the conservatism and strand throughput on exactly
# the machines that need it most.
COMMIT_PLAN_FRACTION = 0.9




def _process_private_mb() -> "float | None":
    """This process's commit charge (PrivateUsage) in MB; None off Windows or
    if the probe fails. The figure the end-of-run line and PARENT_COMMIT_GB
    talk about, measured the way the memory notes measure it."""
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        fn = k32.K32GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        c = _PMC()
        c.cb = ctypes.sizeof(_PMC)
        if not fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return c.PrivateUsage / 2**20
    except Exception:
        return None


def _memory_status() -> "dict | None":
    """Physical RAM and Windows COMMIT figures in GB, or None if unavailable.

    Stdlib only, ON PURPOSE: `psutil` is EXCLUDED from the frozen build
    (CBBEtoUBE.spec), so importing it here would work in a source run and blow up
    in the exe every user actually runs.

    Returns `total_gb`, `avail_gb`, `commit_limit_gb`, `commit_free_gb`. The
    commit pair is the one that decides whether a run DIES rather than merely
    slows down, and until 2026-09-11 nothing read it: Windows fails an
    allocation against the commit limit (physical RAM + page file), not against
    free RAM, so two machines with identical RAM behave completely differently
    depending on whether the page file is system-managed or pinned small. That
    is the difference between a user who reports a memory error and one who
    reports nothing. `ullAvailPageFile` is the amount that can still be
    committed; despite the name it is NOT "free space in pagefile.sys".
    #commit-headroom"""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MS()
            st.dwLength = ctypes.sizeof(_MS)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            g = 1024.0 ** 3
            return {"total_gb": st.ullTotalPhys / g,
                    "avail_gb": st.ullAvailPhys / g,
                    "commit_limit_gb": st.ullTotalPageFile / g,
                    "commit_free_gb": st.ullAvailPageFile / g}
        tot = (os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")) / 1024 ** 3
        return {"total_gb": tot, "avail_gb": None,
                "commit_limit_gb": None, "commit_free_gb": None}
    except Exception:
        return None


def default_worker_count() -> int:
    """Worker processes to run by default: bounded by CPUs AND by RAM.

    RATIONALE = avoid oversubscribing memory. `cpu_count() - 1` alone assumes
    RAM is free. It is not: MEASURED 2026-07-25 mid-reconvert on a 24-thread /
    32 GB box, the old default's 23 workers held 58.9 GB of private bytes
    against 31.8 GB physical, leaving 0.6 GB free while the OS paged ~36 GB out
    and faulted it back at 200-744 pages/sec. This cap keeps the pool inside
    real memory instead. #worker-mem-budget

    THROUGHPUT EFFECT IS UNPROVEN -- do not claim this is faster. A 20-mod /
    775-NIF A/B (23 vs 16, alternating, warm-up discarded, 2026-07-25) came back
    a TIE: 418.6s vs 428.2s median conversion, a 2.3% edge to 23 that is inside
    the noise band, and the wall-clock medians favoured the other arm. That test
    peaked at ~24 GB with ~8 GB free, i.e. it never reproduced the pressure --
    the footprint only builds to exhaustion over a full ~3800-mod run. So the
    paging is real and the slowdown is plausible but unmeasured; settling it
    needs two FULL runs. Treat this as insurance (and a genuine help on low-RAM
    machines), not a speed-up.

    KEEP 2.0. DO NOT LOWER IT. An earlier version of this docstring reasoned
    that the BLAS cap had removed ~1.5 GB of the 2.4 GB it was calibrated on, so
    it "errs about 2x toward safety". That was wrong, and acting on it would
    have broken the cap: 0.9 GB is a floor-plus-a-bit figure, while the quantity
    this constant bounds is WORKING SET -- measured 2026-09-11 at 0.6-1.3 GB
    steady and 1.53-1.69 GB peak. 2.0 errs about 1.2x safe, not 2x.

    The old 58.9 GB reading IS attributable after all: the default it was taken
    under was `cpu_count() - 1`, which on that 24-thread box is 23, so
    58.9/23 = 2.56 GB/worker -- corroborated independently by the pre-cap binary
    measuring 2.02-2.04 GB peak commit per worker. The "25 workers" in the
    header comment above is the misremembered figure.

    `--workers` (CLI) and the GUI spinbox still override this outright; the
    budget itself can be retuned with CBBE2UBE_WORKER_MEM_GB for A/B testing."""
    cpu = max(1, (os.cpu_count() or 4) - 1)
    try:
        budget = _knob("CBBE2UBE_WORKER_MEM_GB", float(WORKER_MEM_BUDGET_GB))
    except ValueError:
        budget = WORKER_MEM_BUDGET_GB
    if budget <= 0:
        return cpu
    st = _memory_status()
    gb = st["total_gb"] if st else None
    if not gb:
        return cpu                      # unknown RAM -> old behaviour
    # FLOOR, not round-to-nearest. The old `int(gb/budget + 0.5)` granted a
    # 15.85 GB machine 8 workers x 2.0 = 16.0 GB, i.e. 101% of physical RAM
    # before Windows, MO2, the GUI or the child converter had taken a byte. The
    # comment defending it -- that a "32 GB" box reports 31.8 GB and flooring
    # loses a worker to firmware reserve -- was true and beside the point:
    # rounding UP past the machine's own capacity to recover that worker is a
    # worse trade than losing it. #worker-mem-budget
    n = max(1, min(cpu, int(gb / budget)))

    # COMMIT GUARD. #commit-headroom  The cap above bounds RAM, which decides
    # whether the box THRASHES. This one bounds Windows commit charge, which
    # decides whether an allocation FAILS -- a different quantity, and the one
    # behind an actual memory error. It can only ever LOWER the count, so a
    # machine with a healthy page file sees no change; a machine with the page
    # file disabled or pinned small (common "gaming performance" advice) sees
    # the pool shrink to what it can actually commit. +2 covers the GUI process
    # and the child converter, which are live for the whole run. The 0.8 leaves
    # room for everything else the user is running.
    free_commit = (st or {}).get("commit_free_gb")
    # `is not None`: at exactly 0.0 committable, truthiness skipped the guard
    # and handed back the full RAM-capped pool on the one machine least able to
    # run it. Gated on commit_limit_gb too, so a probe that reports 0 for both
    # (a VM, a sandbox) falls back to the RAM cap instead of being pinned to a
    # single worker forever. #commit-headroom
    if (free_commit is not None and (st or {}).get("commit_limit_gb")
            and WORKER_COMMIT_GB > 0):
        # Solve need(w) <= fraction * free, pricing the parent and the GUI
        # separately rather than as two more workers.
        room = (free_commit * COMMIT_PLAN_FRACTION
                - PARENT_COMMIT_GB - GUI_COMMIT_GB)
        n = max(1, min(n, int(room / WORKER_COMMIT_GB)))
    return n


def memory_plan(workers: int) -> dict:
    """The machine's memory facts plus what this run intends to use.

    Kept separate from the printing so `run_config()` and the report can carry
    the same numbers into `conversion_report.json` -- a memory error is
    diagnosable only if the artefacts say how much RAM, how many CPU threads and
    how big the page file was, and before 2026-09-11 not one of them did.
    #commit-headroom"""
    st = _memory_status() or {}
    try:
        budget = _knob("CBBE2UBE_WORKER_MEM_GB", float(WORKER_MEM_BUDGET_GB))
    except ValueError:
        budget = WORKER_MEM_BUDGET_GB
    # The parent and the GUI priced at their own measured figures. They were
    # previously charged as two extra WORKERS, which understated them: the
    # parent alone peaks at 2.7-2.8 GB in the postflight block.
    need = (max(0, int(workers)) * max(WORKER_COMMIT_GB, 0.0)
            + PARENT_COMMIT_GB + GUI_COMMIT_GB)
    free_commit = st.get("commit_free_gb")
    return {"workers": int(workers),
            "cpu_count": os.cpu_count(),
            "budget_gb_per_worker": budget,
            "projected_commit_gb": need,
            "blas_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "total_ram_gb": st.get("total_gb"),
            "avail_ram_gb": st.get("avail_gb"),
            "commit_limit_gb": st.get("commit_limit_gb"),
            "commit_free_gb": free_commit,
            # `is not None`, NOT truthiness. A machine with 0.0 GB committable
            # is the most starved one there is, and `bool(0.0)` read that as
            # "no data" and silently dropped the warning. #commit-headroom
            # 0.95, ABOVE the COMMIT_PLAN_FRACTION the guard targets. At 0.7 it
            # fired by construction on every machine where the guard bound: the
            # guard leaves the pool at 0.8 of free commit, 0.8 > 0.7, so the run
            # told the user to "lower Worker processes" on a pool it had just
            # lowered for them. Above the guard's own target it fires only when
            # the count came from somewhere else -- an explicit --workers or the
            # GUI spinbox -- which is the only case where the advice is
            # actionable. #commit-headroom
            "tight": (free_commit is not None
                      and need > free_commit * 0.95)}


def describe_memory_plan(workers: int) -> "list[str]":
    """Human-readable form of `memory_plan` for the run log."""
    p = memory_plan(workers)
    out = []
    if p["total_ram_gb"]:
        _ram = f"{p['total_ram_gb']:.1f} GB RAM"
        if p["avail_ram_gb"]:
            _ram += f" ({p['avail_ram_gb']:.1f} GB free)"
        _pf = ""
        if p["commit_limit_gb"]:
            _pf = (f"; page file: {p['commit_limit_gb']:.1f} GB commit limit, "
                   f"{p['commit_free_gb']:.1f} GB free")
        out.append(f"machine: {_ram}, {p['cpu_count']} CPU threads{_pf}")
        _w = p["workers"]
        _ea = "" if _w == 1 else " each"
        out.append(f"memory plan: {_w} worker{'' if _w == 1 else 's'} at about "
                   f"{WORKER_COMMIT_GB:g} GB{_ea}, plus {PARENT_COMMIT_GB:g} GB "
                   f"for this process and {GUI_COMMIT_GB:g} GB for the window = "
                   f"roughly {p['projected_commit_gb']:.1f} GB of commit charge")
    if p["tight"]:
        out.append("!! LOW MEMORY: this run wants more Windows commit charge "
                   "than you have comfortably free.")
        # NAME NO NUMBER. "lower it to 4" was printed verbatim to machines
        # whose pool the guard had ALREADY cut to 1 or 2 -- telling a starved
        # user to RAISE their worker count, in the banner warning them about
        # memory. preflight.py phrases it the same way for the same reason.
        # At one worker there is nothing left to lower, and repeating the
        # advice anyway is how the first version of this banner told starved
        # users to RAISE their count. Below that floor the page file is the
        # only remedy, so say only that.
        if p["workers"] > 1:
            out.append("   If it dies with a memory error, lower \"Worker "
                       "processes\" on the Run tab and run again.")
        else:
            out.append("   This run is already down to a single worker, so "
                       "there is nothing left to lower.")
        out.append("   Setting your page file back to system-managed (System > "
                   "Advanced system settings > Performance >")
        out.append("   Settings > Advanced > Virtual memory > Change) "
                   + ("also fixes it, and is the better answer."
                      if p["workers"] > 1 else "is the fix."))
    return out


def _skip_esp_less_fallback(armor_bases, src_esps) -> bool:
    """True when an empty `armor_bases` means "the plugin was read and selected
    nothing" rather than "there is no plugin" -- in which case the ESP-less
    "convert every NIF the folder ships" fallback must NOT fire.
    #esp-less-fallback-only

    Keyed on a plugin actually PARSING, not merely existing. `_find_source_esps`
    is a bare glob, while `_player_armor_mesh_bases` swallows a parse failure and
    returns an empty set -- so a truncated or unsupported plugin looks exactly
    like "selected nothing". Gating on file presence alone would convert ZERO
    meshes for such a mod, where it previously converted every NIF it ships:
    silent total loss, under a note claiming the plugin was read.

    Parses are cached (`ESP.load_cached`), and the caller has already parsed
    these same files via `_player_armor_mesh_bases`, so this costs a dict hit."""
    if armor_bases or not src_esps:
        return False
    from . import esp as _esp
    for _p in src_esps:
        try:
            _esp.ESP.load_cached(_p)
            return True                 # a plugin genuinely parsed -> trust it
        except Exception:
            continue
    return False                        # nothing parsed -> treat as plugin-less


# ---------- #npc-worn-nonplayable: which armour NPCs actually wear ----------
#
# The vanilla playable races and their vampire variants (Skyrim.esm, low 24
# bits) -- the races a UBE body covers with no race mod. A UBE race is recognised
# by the plugin that DEFINES it (#ube-race-by-plugin); switched off, by the old
# editor ID prefix.
_UBE_CAPABLE_VANILLA_RACES = frozenset({
    0x013740, 0x013741, 0x013742, 0x013743, 0x013744,   # Argonian .. Imperial
    0x013745, 0x013746, 0x013747, 0x013748, 0x013749,   # Khajiit .. Wood Elf
    0x08883A, 0x08883C, 0x08883D, 0x088840, 0x088844,   # their vampire variants
    0x088845, 0x088846, 0x088884, 0x088794, 0x0A82B9,
})
# #ube-race-by-plugin: the UBE races are the RACE records UBE_AllRace.esp
# defines (their editor IDs begin "00UBE_", so the old "ube_" prefix test below
# never matched one). The same identity the coverage passes and the loose-mesh
# index use. The prefix is kept only for the off-switch.
_UBE_RACE_PLUGIN = "ube_allrace.esp"
_UBE_RACE_EDID_PREFIX = "ube_"
_ACBS_FEMALE = 0x00000001        # NPC_ ACBS flags, bit 0
_TPLT_USE_TRAITS = 0x0001        # NPC_ ACBS template-data flags (offset 18)
_RECORD_DELETED = 0x00000020
# {load-order key -> worn set}; one load order at a time. #npc-worn-nonplayable
_NPC_WORN_CACHE: "dict[tuple, frozenset]" = {}


def _read_plugin_groups(path, labels) -> "tuple[list[str], dict[bytes, list]]":
    """(masters, {label: [Record, ...]}) for the top-level groups named in
    `labels`, SEEKING past every other group unread. The NPC scan needs four
    small groups of every active plugin; cells and worldspaces are most of each
    file's bytes, and `ESP.load` would read and parse all of them. Raises on a
    file that is not a plugin; stops at a corrupt group size rather than spin."""
    from . import esp as _esp
    import struct as _struct
    out: "dict[bytes, list]" = {}
    with open(path, "rb") as fh:
        head = fh.read(24)
        if len(head) < 24 or head[:4] != b"TES4":
            raise ValueError(f"{Path(path).name}: no TES4 header")
        size = _struct.unpack_from("<I", head, 4)[0]
        tes4, _ = _esp.Record.parse(head + fh.read(size), 0)
        masters = list(_esp.TES4Header.parse_from_record(tes4).masters)
        while True:
            gh = fh.read(24)
            if len(gh) < 24 or gh[:4] != b"GRUP":
                break
            gsize = _struct.unpack_from("<I", gh, 4)[0]
            if gsize < 24:
                break
            label = gh[8:12]
            if label in labels:
                grp, _ = _esp.Group.parse(gh + fh.read(gsize - 24), 0)
                out.setdefault(label, []).extend(grp.records)
            else:
                fh.seek(gsize - 24, 1)
    return masters, out


def _ube_race_by_plugin() -> bool:
    """#ube-race-by-plugin: a UBE race is one UBE_AllRace.esp defines, not one
    whose editor ID starts with "ube_". CBBE2UBE_NO_UBE_RACE_BY_PLUGIN=1 turns
    it off."""
    return not _flag("CBBE2UBE_NO_UBE_RACE_BY_PLUGIN", False)


def _npc_worn_armos(plugin_paths) -> "frozenset[tuple[str, int]]":
    """Every form a female NPC of a UBE-capable race wears or carries, as
    {(defining plugin lowercase, formid low24)} -- the identity
    `_player_armor_mesh_bases` checks a non-playable ARMO against.
    #npc-worn-nonplayable

    `plugin_paths` is the active load order (last = conflict winner). Only the
    WINNING record of each NPC_, outfit (OTFT) and leveled item list (LVLI)
    counts: a replacer that re-dresses an NPC takes the old outfit out of the
    game. From each qualifying NPC the walk follows its default outfit (DOFT),
    sleep outfit (SOFT) and inventory (CNTO), through outfits and leveled lists
    to any depth. The worn skin (WNAM) is NOT followed -- that is the NPC's
    body, not something it wears over it.

    Qualifying = female (ACBS flag 0x1) and of a vanilla playable race, its
    vampire variant, or a UBE race: one DEFINED by UBE_AllRace.esp, whatever
    plugin overrides it last (#ube-race-by-plugin). The first cut tested the
    editor ID for "ube_", but UBE_AllRace's races are all "00UBE_..." -- on a
    real load order it matched none of its 18, and 13 winning NPC records on
    those races (12 female) were not read as wearers. Counting them took that
    set from 9,675 to 9,686 forms (2026-09-25): 8 armours, every record of each
    playable, 2 outfits and a weapon -- no non-playable armour, so what is
    converted and linked there did not change. With the switch
    CBBE2UBE_NO_UBE_RACE_BY_PLUGIN=1 that prefix test is back. An NPC whose
    traits come from a template (TPLT with the Use Traits flag) takes its sex
    and race from that template, followed through NPC records to the first one
    with its own traits. A leveled-NPC-list template has no single answer, so
    that NPC does NOT count: the user's rule is armour a FEMALE NPC wears. The
    first cut counted every templated NPC as a possible wearer; on a real load
    order 48 of its 125 new meshes came only that way -- creature-cavity
    bodies, animal costumes, and male bosses' and orders' gear -- and none of
    them through a known female.

    A SKIN is never worn, however it is reached: every form that any NPC_ or
    RACE record -- winning or not -- names as its skin (WNAM) is taken out of
    the set at the end (review, 2026-09-24). Not following WNAM was not enough:
    the first cut, which counted every templated NPC, still reached 23 skins
    -- templated NPCs carry skeleton, dragon, wraith and other creature skins
    in their own outfits and inventories -- and a skeleton skin's DefaultRace
    armature passed every later gate and was planned as armour. The template
    rule above reaches none of that load order's 1,387 skin forms, so today
    the subtraction is a guard and changes nothing.

    Measured on a real load order (3,254 active plugins, 2026-09-24): 9,675
    forms reached, 1,584 of them ARMOs; about 10 s including the plugin-file
    lookup with the plugins in the disk cache (7.6 s for the walk alone), 103 s
    in one cold full run. The first cut reached 15,763 forms, 15,740 once the
    skins were out; with WNAM followed and race-less NPCs counted -- the
    2026-09-24 report's two differences -- it reproduced that report's 3,783
    worn ARMOs exactly, 236 of them reachable only that way. An unreadable
    plugin is skipped. The set holds every reached form but the skins, not only
    armour -- callers test armour identities against it."""
    from . import esp as _esp
    import struct as _struct
    _wanted = (b"NPC_", b"OTFT", b"LVLI", b"RACE")
    npcs: "dict[tuple, tuple | None]" = {}   # winner: (female, race, traits-template, items)
    lists: "dict[tuple, tuple]" = {}         # OTFT/LVLI winner -> its entries
    race_edid: "dict[tuple, str]" = {}
    skins: "set[tuple[str, int]]" = set()    # any NPC_/RACE WNAM, any record
    for p in plugin_paths:
        try:
            masters, groups = _read_plugin_groups(p, _wanted)
        except Exception:
            continue
        lc = [m.lower() for m in masters]
        own = Path(p).name.lower()

        def _abs(fid, _lc=lc, _own=own):
            mi = fid >> 24
            return (_lc[mi] if mi < len(_lc) else _own, fid & 0xFFFFFF)

        for r in groups.get(b"NPC_", ()):
            rid = _abs(r.formid)
            if r.flags & _RECORD_DELETED:
                npcs[rid] = None
                continue
            female, race, tpl, tflags, items = False, None, None, 0, []
            for sig, d in _esp.iter_subrecords(r.payload):
                if sig == b"ACBS" and len(d) >= 20:
                    female = bool(_struct.unpack_from("<I", d, 0)[0] & _ACBS_FEMALE)
                    tflags = _struct.unpack_from("<H", d, 18)[0]
                elif sig == b"RNAM" and len(d) == 4:
                    race = _abs(_struct.unpack("<I", d)[0])
                elif sig in (b"DOFT", b"SOFT") and len(d) == 4:
                    items.append(_abs(_struct.unpack("<I", d)[0]))
                elif sig == b"CNTO" and len(d) >= 4:
                    items.append(_abs(_struct.unpack_from("<I", d, 0)[0]))
                elif sig == b"TPLT" and len(d) == 4:
                    _t = _struct.unpack("<I", d)[0]
                    tpl = _abs(_t) if _t else None
                elif sig == b"WNAM" and len(d) == 4:     # the NPC's own skin
                    skins.add(_abs(_struct.unpack("<I", d)[0]))
            npcs[rid] = (female, race,
                         tpl if tflags & _TPLT_USE_TRAITS else None,
                         tuple(items))
        for r in groups.get(b"OTFT", ()):
            ents: list = []
            if not r.flags & _RECORD_DELETED:
                for sig, d in _esp.iter_subrecords(r.payload):
                    if sig == b"INAM":
                        ents += [_abs(_struct.unpack_from("<I", d, i)[0])
                                 for i in range(0, len(d) - 3, 4)]
            lists[_abs(r.formid)] = tuple(ents)
        for r in groups.get(b"LVLI", ()):
            ents = []
            if not r.flags & _RECORD_DELETED:
                for sig, d in _esp.iter_subrecords(r.payload):
                    if sig == b"LVLO" and len(d) >= 8:
                        ents.append(_abs(_struct.unpack_from("<I", d, 4)[0]))
            lists[_abs(r.formid)] = tuple(ents)
        for r in groups.get(b"RACE", ()):
            for sig, d in _esp.iter_subrecords(r.payload):
                if sig == b"EDID":
                    race_edid[_abs(r.formid)] = d.rstrip(b"\x00").decode(
                        "cp1252", errors="replace")
                elif sig == b"WNAM" and len(d) == 4:     # the race's skin
                    skins.add(_abs(_struct.unpack("<I", d)[0]))

    by_plugin = _ube_race_by_plugin()

    def _ube_capable(race) -> bool:
        if race is None:
            return False
        if race[0] == "skyrim.esm" and race[1] in _UBE_CAPABLE_VANILLA_RACES:
            return True
        if by_plugin:                 # the race's identity: its DEFINING plugin
            return race[0] == _UBE_RACE_PLUGIN
        return race_edid.get(race, "").lower().startswith(_UBE_RACE_EDID_PREFIX)

    known: "dict[tuple, bool]" = {}

    def _female_wearer(rid, depth=0) -> bool:
        """Sex and race of `rid`, through its traits template chain. A leveled-
        NPC template, an unknown or deleted record and a cycle are not female."""
        if rid in known:
            return known[rid]
        npc = npcs.get(rid)
        if npc is None or depth > 16:
            return False
        female, race, tpl, _items = npc
        if tpl is not None:
            got = _female_wearer(tpl, depth + 1)
        else:
            got = female and _ube_capable(race)
        known[rid] = got
        return got

    worn: "set[tuple[str, int]]" = set()
    stack: list = []
    for rid, npc in npcs.items():
        if npc is not None and _female_wearer(rid):
            stack.extend(npc[3])
    while stack:                      # outfits and lists may nest or cycle
        it = stack.pop()
        if it in worn:
            continue
        worn.add(it)
        stack.extend(lists.get(it, ()))
    return frozenset(worn - skins)    # a skin is the body, never worn over it


def _batch_npc_worn_armos(for_coverage: bool = False
                          ) -> "frozenset[tuple[str, int]] | None":
    """`_npc_worn_armos` over the active load order, built once per load order
    per process: source selection and the convert step of one `auto` run share
    it, and a GUI refresh does not re-read every plugin while the mod and plugin
    order stay the same.

    None -- the old rule, every non-playable armour skipped -- when switched off
    (CBBE2UBE_NO_NPC_WORN_NONPLAYABLE=1) or when there is no load order to read.
    Never raises: a failed read is a warning and the old rule. #npc-worn-nonplayable

    `for_coverage`: the coverage step's race-list rule asks after reading its
    own switch (CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST), so the conversion switch
    is not read -- each switch turns off only its own feature. Same cache.
    #coverage-human-race-list"""
    if not for_coverage and _flag("CBBE2UBE_NO_NPC_WORN_NONPLAYABLE", False):
        return None
    try:
        lay = paths.discover_layout()
        names = paths.active_plugins_ordered(lay)
        if not names:
            return None
        # Keyed on what decides which plugin files load -- the mods folder, the
        # mod priority order and the plugin load order -- the way
        # `_third_party_ube_covered_armos` keys its scan. NOT on the files' own
        # stat: resolving the names to files walks every mod folder (~5 s of the
        # ~10 s on a 3,254-plugin order), which would make a hit cost half a miss.
        key = (str(lay.mods_root), tuple(paths.enabled_mods_ordered(lay) or ()),
               tuple(n.lower() for n in names))
        # And on the index mode (#root-plugin-index): its switch can flip in a
        # long-lived GUI process, and the two indexes can resolve a name to
        # different files.
        key += (paths.root_plugin_index_on(),)
        # And on the UBE-race rule (#ube-race-by-plugin): its switch decides
        # who counts as a wearer.
        key += (_ube_race_by_plugin(),)
        hit = _NPC_WORN_CACHE.get(key)
        if hit is not None:
            return hit
        t0 = time.time()
        fidx = paths.plugin_file_index(lay)
        ordered = [Path(fidx[n.lower()]) for n in names if n.lower() in fidx]
        if not ordered:
            return None
        worn = _npc_worn_armos(ordered)
        _NPC_WORN_CACHE.clear()
        _NPC_WORN_CACHE[key] = worn
        print(f"  NPC outfits: read {len(ordered)} active plugin(s) in "
              f"{time.time() - t0:.0f}s -- {len(worn)} form(s) worn or carried "
              "by female NPCs of UBE-capable races")
        return worn
    except Exception as e:
        warn(f"could not read which armour NPCs wear ({plain_error(e)})",
             consequence="non-playable armour that female NPCs wear is not "
                         "converted, and the coverage race-list rule does not "
                         "link it, this run")
        return None


# ---------- #selection-winner-playable: the playable flag the game uses ----------
#
# {load-order key -> {(defining plugin lowercase, formid low24) -> not playable}};
# one load order at a time. A failed build is cached as None so it warns once.
_ARMO_WINNER_CACHE: "dict[tuple, dict | None]" = {}
_ARMO_NONPLAYABLE_FLAG = 0x00000004   # ARMO record header flag


def _selection_winner_playable() -> bool:
    r"""#selection-winner-playable (2026-09-25): does source selection read an
    armour's playable flag from its WINNING record -- the one the game uses --
    instead of from the scanned plugin's own record? Yes, by default.
    CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE=1 reads each plugin's own record
    again. Its own switch: CBBE2UBE_NO_NPC_WORN_NONPLAYABLE does not touch it."""
    return not _flag("CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE", False)


def _armo_winner_nonplayable(plugin_paths) -> "tuple[dict, list[str]]":
    """({(defining plugin lowercase, formid low24) -> True when the WINNING
    record is non-playable or deleted}, [unreadable plugin names]) over
    `plugin_paths`, the active load order (last = winner). #selection-winner-playable

    Only the ARMO group of each plugin is read (`_read_plugin_groups`). The
    identity is the DEFINING plugin through the record's master list -- the
    same key `_player_armor_mesh_bases` and `_npc_worn_armos` use -- so an
    override in an ESL- or ESM-flagged plugin lands on its master's armour, and
    a record whose defining plugin holds no record of it is still keyed on that
    name: the last loaded record carrying it wins. Each record's own header
    flag decides; a template (TNAM) is not followed, since every variant keeps
    its own flag and models. The legacy BODT non-playable bit (0x10) is not
    read -- on the measured load order only creature skins carried it. An
    unreadable plugin is skipped and named, so its overrides do not count."""
    out: "dict[tuple[str, int], bool]" = {}
    bad: "list[str]" = []
    for path in plugin_paths:
        try:
            masters, groups = _read_plugin_groups(path, (b"ARMO",))
        except Exception:
            bad.append(Path(path).name)
            continue
        lc = [m.lower() for m in masters]
        own = Path(path).name.lower()
        for r in groups.get(b"ARMO", ()):
            mi = r.formid >> 24
            ident = (lc[mi] if mi < len(lc) else own, r.formid & 0xFFFFFF)
            out[ident] = bool(r.flags & (_ARMO_NONPLAYABLE_FLAG | _RECORD_DELETED))
    return out, bad


def _batch_armo_winner_nonplayable() -> "dict[tuple[str, int], bool] | None":
    r"""`_armo_winner_nonplayable` over the active load order as the GAME sees
    it -- plugin files at the root of overwrite, of an enabled mod and of the
    game Data folder (`paths._plugin_file_index_root`), whatever the index
    switch says: a recursive walk can hand back our own un-loaded copy of a
    plugin in place of the one the game loads. Built once per load order per
    process, like `_batch_npc_worn_armos`, but not inside it: that set's switch
    and failures must not turn this rule off.

    None -- every plugin's own record decides, as before -- when switched off
    (CBBE2UBE_NO_SELECTION_WINNER_PLAYABLE=1). FAILS OPEN with a warning: a
    modlist whose plugin order cannot be read, a load order no plugin file
    resolves for, or a read error gives None too, once per load order. With no
    modlist at all (a plain `convert` of a folder) there is no load order to
    read, so it is None without a warning. Never raises. #selection-winner-playable"""
    if not _selection_winner_playable():
        return None
    key = None
    try:
        lay = paths.discover_layout()
        if lay.mods_root is None:
            return None               # no modlist: nothing to read, nothing lost
        names = paths.active_plugins_ordered(lay) or []
        # Keyed on what decides which plugin files load -- the mods folder, the
        # mod priority order and the plugin load order -- and on the switch.
        key = (str(lay.mods_root), tuple(paths.enabled_mods_ordered(lay) or ()),
               tuple(n.lower() for n in names), _selection_winner_playable())
        if key in _ARMO_WINNER_CACHE:
            return _ARMO_WINNER_CACHE[key]
        t0 = time.time()
        fidx = paths._plugin_file_index_root(lay)
        ordered = [Path(fidx[n.lower()]) for n in names if n.lower() in fidx]
        if not ordered:
            raise ValueError("no active plugin file found" if names
                             else "the plugin load order could not be read")
        flags, bad = _armo_winner_nonplayable(ordered)
        _ARMO_WINNER_CACHE.clear()
        _ARMO_WINNER_CACHE[key] = flags
        print(f"  Armour playability: read the winning record of {len(flags)} "
              f"armour(s) in {len(ordered)} active plugin(s) in "
              f"{time.time() - t0:.1f}s")
        if bad:
            _names = ", ".join(bad[:5]) + (" ..." if len(bad) > 5 else "")
            warn(f"{len(bad)} active plugin(s) could not be read for armour "
                 f"playability: {_names}",
                 consequence="an armour those plugins override keeps the "
                             "playable flag of the record before them")
        return flags
    except Exception as e:
        warn(f"could not read which armour the load order makes playable "
             f"({plain_error(e)})",
             consequence="each plugin's own record decides whether its armour "
                         "is playable, as before this rule, this run")
        if key is not None:
            _ARMO_WINNER_CACHE.clear()
            _ARMO_WINNER_CACHE[key] = None
        return None


def _player_armor_mesh_bases(mod_dir: Path,
                             include_candidate_slots: bool = False,
                             mesh_resolves=None,
                             ube_covered_armos=None,
                             npc_worn_armos=None,
                             worn_admitted=None,
                             armo_winner_nonplayable=None) -> "set[str]":
    """Weight-agnostic rel-path keys of every mesh a DefaultRace ARMA in this mod
    points at as an armor piece (biped slot is not hair-only).

    `mesh_resolves`: optional ``(weight_base_key) -> bool`` predicate (True if that
    mesh actually exists to convert). Enables the FEMALE-ONLY policy -- with a female
    model that resolves, the male model is skipped (UBE is a female body; a female
    actor never renders the male mesh). The male model is kept only for a male-only
    piece, or when the female model is a dead path (so the female ARMA can redirect to
    the converted male). ``None`` keeps the legacy "convert every slot".

    `ube_covered_armos`: optional ``{(defining plugin lowercase, formid low24)}``
    of armors a THIRD-PARTY mod has already UBE-patched (from
    `_third_party_ube_covered_armos`). An ARMA whose every referencing ARMO IN
    THIS PLUGIN is in that set is skipped, so the piece is never converted at
    all. Without this the coexistence check only ran at the coverage/link stage:
    the meshes were converted and shipped, then suppressed -- burning conversion
    time and leaving orphan meshes in the output with no SkyPatcher link.
    #skip-already-ube

    `npc_worn_armos`: optional set, same identity, of forms a female NPC of a
    UBE-capable race wears or carries (`_npc_worn_armos`). A NON-PLAYABLE ARMO in
    it counts as worn, so an ARMA only non-playable armour references is kept
    when one of those ARMOs is in the set. None keeps the old rule (every such
    ARMA skipped). `worn_admitted`: optional set the caller passes to learn which
    armatures, as (plugin lowercase, ARMA formid), were kept ONLY for that
    reason and planned at least one mesh. #npc-worn-nonplayable

    `armo_winner_nonplayable`: optional map, same identity, from
    `_batch_armo_winner_nonplayable`: is the armour's WINNING record in the load
    order non-playable (or deleted)? When it knows an ARMO, its answer replaces
    the scanned record's own flag for every test here, the worn test included;
    an identity it does not know keeps the record's flag. None = each plugin's
    own record decides (the old rule). Which armatures a plugin's ARMOs admit is
    still judged per plugin. #selection-winner-playable

    `include_candidate_slots`: also admit ambiguous modder slots (44/45/47/48/59/61)
    used for body cloth. The crash guard in auto_convert_mod drops any non-body-skinned
    mesh on these slots. Default False = strict body-slot allowlist (for selection;
    beard/backpack-only mods are not pulled in).

    This is the single test for "equippable armor piece for the player"; facegen
    heads, clutter, ground models, and hair are excluded. The ESP parser doesn't
    decompress, so this is cheap."""
    from . import esp as _esp
    import struct as _struct
    bases: "set[str]" = set()
    # #nude-basename-path: the nude-skin basenames mean body skin only at the
    # body's own home (see `_is_nude_body_skin_model`). Read once per call.
    _skin_by_name_alone = _flag("CBBE2UBE_NO_NUDE_BASENAME_PATH", False)
    # Vanilla sweep: the game Data dir enumerates the vanilla/DLC masters
    # (_find_source_esps skips those by design for normal mod folders).
    for ep in (_vanilla_sweep_esps(mod_dir) or _find_source_esps(mod_dir)):
        try:
            e = _esp.ESP.load_cached(ep)  # read-only scan -> cached parse
        except Exception:
            continue
        masters = e.header.masters
        # Map which of THIS plugin's ARMAs are referenced by a PLAYABLE ARMO vs
        # only by non-playable one(s). Gore / decapitation / dismemberment /
        # effect "armors" (gore/decapitation mods, etc.) bind real DefaultRace
        # body-slot ARMAs but flag the ARMO non-playable -- they're applied on
        # death/by script, never equipped. We skip an ARMA whose every
        # referencing ARMO IN THIS PLUGIN is non-playable, so those gore meshes
        # aren't converted. An ARMA referenced by NO same-plugin ARMO (e.g. a
        # vanilla replacer whose ARMO lives in Skyrim.esm) is left in -- we can't
        # see the master ARMO's flag here, and those are real armour.
        #
        # #npc-worn-nonplayable (2026-09-24): "non-playable" is not "never worn".
        # Follower and quest outfits are flagged non-playable too, and the unified
        # coverage step deliberately gives non-playable body and hands/feet armour
        # a UBE armature -- so the skip here left that armature drawing the
        # unconverted CBBE mesh on a UBE actor. A non-playable ARMO that a female
        # NPC of a UBE-capable race WEARS or CARRIES (`npc_worn_armos`: a winning
        # NPC_'s default/sleep outfit or inventory, through outfits and leveled
        # lists) now counts as worn. Script-applied gore is reached by no outfit
        # or inventory, so it stays skipped, and no skin is ever in the set (a
        # skeleton skin got through before that rule). Measured on a real
        # modlist, with templated NPCs counted only through their template
        # chain: 43 armatures in 14 sources (7 of them new sources), 37 pieces,
        # 60 NIFs planned, 17 armatures the coverage step had minted over an
        # unconverted mesh; 0 child pieces. Accepted edge: wound meshes that sit
        # in a victim's inventory are converted too.
        _ARMO_NONPLAYABLE = 0x00000004
        playable_ref: "set[int]" = set()
        worn_ref: "set[int]" = set()      # non-playable, but an NPC wears it
        named_item_ref: "set[int]" = set()   # playable AND named (FULL)
        any_ref: "set[int]" = set()
        # #skip-already-ube: same shape as the playable/non-playable split above,
        # for armors a third-party mod has ALREADY UBE-patched. An ARMA is only
        # skipped when EVERY referencing ARMO in this plugin is covered -- a mesh
        # shared with an uncovered armor must still convert.
        _lc_masters = [m.lower() for m in masters]
        covered_ref: "set[int]" = set()
        uncovered_ref: "set[int]" = set()
        for g in e.groups:
            if g.label != b"ARMO":
                continue
            for arec in g.records:
                _play = not (arec.flags & _ARMO_NONPLAYABLE)
                _ident = None
                if (armo_winner_nonplayable or ube_covered_armos
                        or (npc_worn_armos and not _play)):
                    # Identity as `_third_party_ube_covered_armos` returns it: the
                    # DEFINING plugin (a master when this record is an override,
                    # else this plugin) + the low-24 formid.
                    _mi = arec.formid >> 24
                    _def = (_lc_masters[_mi] if _mi < len(_lc_masters)
                            else ep.name.lower())
                    _ident = (_def, arec.formid & 0xFFFFFF)
                # #selection-winner-playable: the flag the game uses is the
                # WINNING record's, not this plugin's (an override or a record
                # a later plugin overrides). An identity the map does not know
                # keeps this record's own flag. The worn test below is judged
                # against the same flag.
                if armo_winner_nonplayable and _ident in armo_winner_nonplayable:
                    _play = not armo_winner_nonplayable[_ident]
                _is_cov = bool(ube_covered_armos) and _ident in ube_covered_armos
                _worn = (not _play and bool(npc_worn_armos)
                         and _ident in npc_worn_armos)
                _refs: "list[int]" = []
                _named = False
                for s, d in _esp.iter_subrecords(arec.payload):
                    if s == b"MODL" and len(d) == 4:
                        _refs.append(_struct.unpack("<I", d)[0])
                    elif s == b"FULL" and d.strip(b"\x00"):
                        _named = True      # an item a player sees by name
                for rf in _refs:
                    any_ref.add(rf)
                    if _play:
                        playable_ref.add(rf)
                        if _named:
                            named_item_ref.add(rf)   # #nude-basename-path
                    elif _worn:
                        worn_ref.add(rf)
                    (covered_ref if _is_cov else uncovered_ref).add(rf)
        for g in e.groups:
            if g.label != b"ARMA":
                continue
            for rec in g.records:
                # Gore/effect: this ARMA is referenced ONLY by non-playable
                # ARMO(s) in this plugin -> not player-equippable -> don't convert
                # -- unless an NPC wears one of them. #npc-worn-nonplayable
                if (rec.formid in any_ref and rec.formid not in playable_ref
                        and rec.formid not in worn_ref):
                    continue
                _only_worn = (rec.formid in worn_ref
                              and rec.formid not in playable_ref)
                # #skip-already-ube: referenced ONLY by armors another mod has
                # already UBE-patched -> that mod owns this piece; converting it
                # would ship a mesh we then suppress at the coverage stage.
                if rec.formid in covered_ref and rec.formid not in uncovered_ref:
                    continue
                rnam = None
                slot = 0
                edid = ""
                female_models: "list[str]" = []   # MOD3 (world) + MOD5 (1st-person)
                male_models: "list[str]" = []      # MOD2 (world) + MOD4 (1st-person)
                for sig, sd in _esp.iter_subrecords(rec.payload):
                    if sig == b"EDID":
                        edid = sd.rstrip(b"\x00").decode("utf-8", errors="ignore")
                    elif sig == b"RNAM" and len(sd) == 4:
                        rnam = _struct.unpack("<I", sd)[0]
                    elif sig in (b"BOD2", b"BODT") and len(sd) >= 4:
                        slot = _struct.unpack_from("<I", sd, 0)[0]
                    elif sig in (b"MOD3", b"MOD5"):
                        female_models.append(sd.rstrip(b"\x00").decode(
                            "utf-8", errors="ignore"))
                    elif sig in (b"MOD2", b"MOD4"):
                        male_models.append(sd.rstrip(b"\x00").decode(
                            "utf-8", errors="ignore"))
                # FEMALE-ONLY conversion: UBE is a female body, so convert the FEMALE
                # model(s) and skip the male mesh (a female actor never renders it, and
                # refitting it to the female body would be wrong). Two exceptions keep
                # the male mesh: (1) a MALE-ONLY piece (no female model) -- a female
                # actor equipping it renders the male mesh, so it needs the UBE refit;
                # (2) the female model exists but its mesh DOESN'T RESOLVE (a dead
                # path) -- then the male mesh is the real one the female
                # ARMA gets redirected to, so it must convert. mesh_resolves==None
                # (callers without VFS context) keeps the legacy "convert both".
                if not female_models:
                    models = male_models
                elif mesh_resolves is None:
                    models = female_models + male_models
                elif any(mesh_resolves(_weight_base_key(m)) for m in female_models):
                    models = female_models
                else:
                    models = female_models + male_models
                if rnam is None:
                    continue
                mi = rnam >> 24
                # mi == len(masters) is a SELF-defined race: Skyrim.esm has an
                # EMPTY master list, so its own ARMAs land here and must pass
                # (the vanilla sweep). Any other plugin's self-defined race is
                # not DefaultRace. Mirrors ube_patcher._rnam_is_default_race.
                race_master = masters[mi] if mi < len(masters) else ep.name
                if (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24 or \
                   race_master.lower() != "skyrim.esm":
                    continue  # not bound to the humanoid player race
                _accept = _BODY_SLOT_BITS
                if include_candidate_slots:
                    _accept |= _BODY_CANDIDATE_SLOT_BITS
                if (slot & _accept) == 0:
                    # Admit draping capes/cloaks on otherwise-excluded slots.
                    # Match the FILENAME (not full path) to avoid false positives
                    # like "...\\Stormcloaks\\Helmet.nif".
                    if not any(_kw in m.replace("\\", "/").rsplit("/", 1)[-1].lower()
                               for m in models for _kw in _CLOAK_MESH_KEYWORDS):
                        continue  # no body-fitted slot (shield/helmet/hood/hair/
                                  # circlet/amulet/ring/ears) — don't convert
                if _is_child_content_asset(edid):
                    continue  # child clothing — not armour "for the player"
                for m in models:
                    if not m:
                        continue
                    base = _weight_base_key(m)
                    if _is_nude_body_skin_model(base, _skin_by_name_alone,
                                                rec.formid in named_item_ref):
                        continue  # nude body skin — not an armour piece
                    if _is_child_content_asset(m):
                        continue  # child clothing reached via an adult-named ARMA
                    if _is_already_ube_model(m):
                        continue  # already UBE-shaped — refitting would break it
                    bases.add(base)
                    if _only_worn and base and worn_admitted is not None:
                        worn_admitted.add((ep.name.lower(), rec.formid))
    bases.discard("")
    return bases


# Bone name fragments marking body-fitted cloth. A beard/shield/backpack/cloak
# carries none of these; pants/skirts/leg armor do. Spine/pelvis alone are
# excluded (cloaks/backpacks weight the spine but are not body cloth).
_BODYFIT_BONE_MARKERS = ("thigh", "calf", "butt", "breast", "belly")


def _nif_has_bodyfit_skin(nif_path: Path) -> bool:
    """True if any shape in the NIF is skinned to a body-fit bone (thigh/calf/
    butt/breast/belly). The crash guard for armour on ambiguous modder slots:
    only such meshes are real body cloth safe to convert + UBE-race tag.
    Best-effort: False on any load failure (fail safe = don't convert)."""
    try:
        from . import nif_io
        nif = nif_io.load_nif(Path(nif_path))
    except Exception:
        return False
    for s in nif.shapes:
        for b in (s.bone_names or []):
            bl = b.lower()
            if any(m in bl for m in _BODYFIT_BONE_MARKERS):
                return True
    return False


_SKIN_INSTANCE_BLOCKS = (b"NiSkinInstance", b"BSDismemberSkinInstance")


def _nif_bytes_body_fit(data: bytes) -> "bool | None":
    """`_nif_has_bodyfit_skin` for NIF bytes held in memory (a mesh read out of
    an archive is never written to disk): is any skin bound to a body-fit bone?
    None when the bytes are not an SSE NIF (20.2.0.7) this reader follows, so a
    caller can fail closed where the pynifly test fails open.

    Reads the header's string table and every skin instance's bone list (each
    bone's block starts with its name's string index) -- the bones pynifly
    reports as a shape's bone_names. #coverage-female-standin"""
    import struct as _st
    from .hh_offset import _parse
    try:
        p = _parse(data)
        if p["version"] != 0x14020007:
            return None
        blocks, strings = p["blocks"], p["strings"]
        for ti, blk in zip(p["bti"], blocks):
            if p["block_types"][ti] not in _SKIN_INSTANCE_BLOCKS:
                continue
            # Data, Skin Partition, Skeleton Root (refs), then the bone count.
            n = _st.unpack_from("<I", blk, 12)[0]
            for ref in _st.unpack_from("<%di" % n, blk, 16):
                if not 0 <= ref < len(blocks):
                    return None
                si = _st.unpack_from("<i", blocks[ref], 0)[0]
                if not 0 <= si < len(strings):
                    return None
                name = strings[si].decode("cp1252", "replace").lower()
                if any(m in name for m in _BODYFIT_BONE_MARKERS):
                    return True
        return False
    except Exception:
        return None


def _nif_bytes_unfitted_skin(data: bytes) -> "bool | None":
    """#coverage-body-cloak: is this NIF skinned (a skin instance on some
    shape) with no skin bound to a body-fit bone (`_BODYFIT_BONE_MARKERS`; any
    other bone, pelvis and arms included, is allowed) -- a cape or cloak the
    crash guard left unconverted? False for an unskinned mesh and for body-fitted
    cloth; None when the bytes are not an SSE NIF this reader follows, so the
    caller fails closed."""
    from .hh_offset import _parse
    fit = _nif_bytes_body_fit(data)
    if fit is None:
        return None
    try:
        p = _parse(data)
        skinned = any(p["block_types"][ti] in _SKIN_INSTANCE_BLOCKS
                      for ti in p["bti"])
    except Exception:
        return None
    return skinned and not fit


# Full-VFS mesh index built once during source selection; reused by the convert
# step to avoid a second modlist walk. Keyed by lowercased mods_root.
_BATCH_MESH_INDEX: "dict[str, dict]" = {}

# The lookup-only archive index source selection built to find mods whose
# armour lives only in archives; the convert step adopts (and pops) its listing
# when it would list the same archives. Keyed by lowercased mods_root.
# #bsa-only-sources
_SELECTION_BSA_INDEX: "dict[str, _BsaMeshIndex]" = {}

# Memo of _find_armor_mod_dirs results so the GUI Refresh and the subsequent
# Convert (same process) share one discovery pass. _BATCH_MESH_INDEX side-effect
# is set on the first call and persists through cache hits.
_ARMOR_MOD_DIRS_CACHE: "dict[tuple, list[dict]]" = {}


def _has_any_source_plugin(mod_dir: Path) -> bool:
    """True if the folder holds any .esp/.esm/.esl. Stops at the first match;
    does not descend into meshes/textures/facegen. Master/CC exclusion happens
    in _find_source_esps when the mod is actually parsed."""
    _exts = (".esp", ".esm", ".esl")
    _skip = {"meshes", "textures", "facegendata", "facegeom", "facetint"}
    for _root, dirs, files in os.walk(mod_dir):
        for f in files:
            d = f.rfind(".")
            if d != -1 and f[d:].lower() in _exts:
                return True
        dirs[:] = [d for d in dirs if d.lower() not in _skip]  # prune asset trees
    return False


def _find_armor_mod_dirs(mods_root: Path,
                         extra_exclude_names: "set[str] | None" = None,
                         enabled_names: "set[str] | None" = None,
                         require_arma: bool = False,
                         enabled_ordered: "list[str] | None" = None,
                         index_skip_mods: "set[str] | None" = None,
                         progress=None,
                         ) -> list[dict]:
    """Memoizing wrapper around _find_armor_mod_dirs_uncached so a GUI Refresh +
    the following Convert (same process, same inputs) reuse ONE scan. Returns a
    FRESH list on every call (callers, e.g. _cmd_auto, sort it in place).
    `progress` (a callable taking one status string) is NOT part of the cache
    key — it only matters on a cache miss."""
    _key = (str(mods_root).lower(), bool(require_arma),
            frozenset(n.lower() for n in (extra_exclude_names or set())),
            frozenset(enabled_names or ()),
            tuple(enabled_ordered or ()),
            frozenset(n.lower() for n in (index_skip_mods or set())),
            # The vanilla-sweep toggle changes which mesh keys get indexed into
            # the returned candidate set (see the union_all sweep branch), so it
            # must be part of the memo key or a mid-process toggle returns a
            # stale list.
            _flag("CBBE2UBE_NO_VANILLA_SWEEP", False),
            # Each of these four changes which mods are sources (or, for the
            # archive rule, which archives decide that), so a toggle between a
            # GUI refresh and the convert must not return the other list.
            # #bsa-only-sources #texture-archive-meshes #nude-basename-path
            # #npc-worn-nonplayable
            _flag("CBBE2UBE_NO_BSA_ONLY_SOURCES", False),
            _flag("CBBE2UBE_NO_TEXTURE_ARCHIVE_MESHES", False),
            _flag("CBBE2UBE_NO_NUDE_BASENAME_PATH", False),
            _flag("CBBE2UBE_NO_NPC_WORN_NONPLAYABLE", False),
            # Whose playable flag counts -- the winning record's or the scanned
            # plugin's -- decides sources too. #selection-winner-playable
            _selection_winner_playable())
    _cached = _ARMOR_MOD_DIRS_CACHE.get(_key)
    if _cached is not None:
        return list(_cached)
    _result = _find_armor_mod_dirs_uncached(
        mods_root, extra_exclude_names=extra_exclude_names,
        enabled_names=enabled_names, require_arma=require_arma,
        enabled_ordered=enabled_ordered, index_skip_mods=index_skip_mods,
        progress=progress)
    _ARMOR_MOD_DIRS_CACHE[_key] = list(_result)
    return _result


def _find_armor_mod_dirs_uncached(mods_root: Path,
                         extra_exclude_names: "set[str] | None" = None,
                         enabled_names: "set[str] | None" = None,
                         require_arma: bool = False,
                         enabled_ordered: "list[str] | None" = None,
                         index_skip_mods: "set[str] | None" = None,
                         progress=None,
                         ) -> list[dict]:
    """Scan an MO2 mods root for mods that ship player-equippable armor.

    A candidate has: at least one .esp, at least one game-mesh .nif (under a
    `meshes\\` folder, excluding environment/creature paths), and a name that
    isn't already-UBE / a body mod / our own output. Returns a list of
    {name, esps, armor_nifs, path} dicts sorted by NIF count (biggest first).
    Detection-driven, no per-armor hardcoding.

    `enabled_names`: if given, only mods whose folder name is in this set
    (the MO2 profile's enabled list) are considered — so a disabled mod is
    never converted.

    `require_arma` (used by `auto`): the mod must ship at least one mesh that a
    DefaultRace ARMA points at as an ARMOUR PIECE (see _player_armor_mesh_bases).
    That is the operational test for "an equippable armor piece for the player"
    — it admits jewelry/circlets and armor under any custom `meshes\\<author>\\`
    folder, while a mod that only adds creature/beast/custom-race gear, hair/
    wigs, NPC facegen, or non-ARMA clutter yields no such mesh and is dropped.
    The reported `armor_nifs` is the count of on-disk NIFs that match (the files
    that will actually be converted), so it scales with a big modlist's true
    armour content rather than every mesh the mod happens to ship.

    Without `require_arma` (the `scan` preview) we can't afford to parse every
    ESP, so we fall back to the conventional armor-path name heuristic to keep
    the candidate net sane."""
    excl = {n.lower() for n in (extra_exclude_names or set())}
    candidates: list[dict] = []
    try:
        mod_dirs = sorted(d for d in mods_root.iterdir() if d.is_dir())
    except OSError:
        return []

    def _name_ok(mod_dir: Path) -> bool:
        nl = mod_dir.name.lower()
        if nl in excl:
            return False
        if enabled_names is not None and mod_dir.name not in enabled_names:
            return False  # disabled in the active MO2 profile
        # The beast-race hints are a TIE-BREAKER, not a veto: under
        # `require_arma` the ARMA test is the evidence and it decides, so a
        # khajiit ARMOUR mod is admitted while a khajiit body/fur/race mod still
        # yields no armour base and is dropped below. The `scan` preview has no
        # ESP parse, hence no evidence, so there the name is all we have.
        _hints = (_NONSOURCE_NAME_HINTS_HARD if require_arma
                  else _NONSOURCE_NAME_HINTS)
        if any(h in nl for h in _hints):
            return False
        if _is_child_content_mod(mod_dir.name):
            return False  # child clothing — not armour "for the player"
        # A source plugin can be .esp OR a bespoke-armour master/.esl (quest mods,
        # bespoke-armor masters, ...). #179. SINGLE asset-pruned
        # walk (stops at first plugin) -- masters/CC are excluded downstream by
        # _find_source_esps, so a master-only folder still gets dropped.
        if not _has_any_source_plugin(mod_dir):
            return False
        return True

    if not require_arma:
        # scan/preview: conventional armor-path name heuristic (no ESP parse).
        for mod_dir in mod_dirs:
            if not _name_ok(mod_dir):
                continue
            armor_nifs = 0
            for nif in mod_dir.rglob("*.nif"):
                rel = str(nif.relative_to(mod_dir)).lower().replace("/", "\\")
                if any(seg in rel for seg in _ENV_PATH_HINTS):
                    continue
                if "meshes\\" not in rel:
                    continue  # not a game mesh (e.g. BodySlide ShapeData)
                if any(seg in rel for seg in _ARMOR_PATH_HINTS):
                    armor_nifs += 1
            if armor_nifs == 0:
                continue
            candidates.append({
                "name": mod_dir.name, "path": mod_dir, "armor_nifs": armor_nifs,
                "esps": sum(1 for _ in mod_dir.rglob("*.esp"))})
        candidates.sort(key=lambda c: c["armor_nifs"], reverse=True)
        return candidates

    def _prog(text: str) -> None:
        if progress is None:
            return
        try:
            progress(text)
        except Exception:
            pass

    # Non-playable armour a female NPC wears counts as armour for every test
    # below: eligibility, coverage keys and the vanilla sweep's keys, so the mesh
    # the game loads for it is located like any other. #npc-worn-nonplayable
    if not _flag("CBBE2UBE_NO_NPC_WORN_NONPLAYABLE", False):
        _prog("reading which armour NPCs wear…")     # ~11 s on a large order
    _npc_worn = _batch_npc_worn_armos()
    _worn_admitted: "set[tuple[str, int]]" = set()
    _worn_mods = 0
    # An armour's playable flag is its WINNING record's, for every test below.
    # #selection-winner-playable
    if _selection_winner_playable():
        _prog("reading which armour the load order makes playable…")
    _winner_np = _batch_armo_winner_nonplayable()

    # require_arma: a mod is a source if a DefaultRace ARMA equips an armour-slot
    # mesh. Count own-folder NIFs first (fast); mods whose meshes are BodySlide-
    # built or in another mod resolve via the VFS instead of being dropped.
    pending_vfs: "list[tuple[Path, set]]" = []
    union_all: "set[str]" = set()   # EVERY candidate's armour mesh keys
    for _mi, mod_dir in enumerate(mod_dirs):
        if _mi % 25 == 0:
            _prog(f"checking mod folders… {_mi}/{len(mod_dirs)}")
        if not _name_ok(mod_dir):
            continue
        armor_bases = _player_armor_mesh_bases(  # STRICT = eligibility
            mod_dir, npc_worn_armos=_npc_worn,
            armo_winner_nonplayable=_winner_np)
        if not armor_bases:
            continue  # no player-equippable armour piece -> not a source
        # Broaden to ambiguous modder slots for VFS coverage on mods already
        # eligible via a standard body slot. The crash guard drops non-body-skinned.
        _mod_worn: "set[tuple[str, int]]" = set()
        cov_bases = _player_armor_mesh_bases(
            mod_dir, include_candidate_slots=True,
            armo_winner_nonplayable=_winner_np,
            npc_worn_armos=_npc_worn, worn_admitted=_mod_worn)
        if _mod_worn:
            _worn_admitted |= _mod_worn
            _worn_mods += 1
        for b in cov_bases:
            union_all.update((f"{b}_0.nif", f"{b}_1.nif", f"{b}.nif"))
        own = 0
        for nif in mod_dir.rglob("*.nif"):
            if _weight_base_key(str(nif.relative_to(mod_dir))) in cov_bases:
                own += 1
        if own > 0:
            candidates.append({
                "name": mod_dir.name, "path": mod_dir, "armor_nifs": own,
                "esps": sum(1 for _ in mod_dir.rglob("*.esp"))})
        elif enabled_ordered:
            pending_vfs.append((mod_dir, cov_bases))
        # else: no modlist to resolve against -> legacy drop.

    # Vanilla sweep keys: the sweep source (game Data dir) is appended by
    # _cmd_auto AFTER this discovery, so its mesh keys must be indexed HERE or
    # sweep resolution falls through to the vanilla BSAs even when a loose
    # replacer (BodySlide-prebuilt vanilla armor at CBBE shape) ships the mesh
    # — the converter must refit the mesh the game actually loads.
    if not _flag("CBBE2UBE_NO_VANILLA_SWEEP", False):
        try:
            _swlay = paths.discover_layout()
            for _dd in (_swlay.game_data_dirs or [])[:1]:
                for b in _player_armor_mesh_bases(
                        Path(_dd), include_candidate_slots=True,
                        armo_winner_nonplayable=_winner_np,
                        npc_worn_armos=_npc_worn):
                    union_all.update((f"{b}_0.nif", f"{b}_1.nif", f"{b}.nif"))
        except Exception:
            pass

    # Build the VFS mesh index over ALL candidates so the convert step can reuse
    # it. Skip only the output mod, NOT body/BodySlide mods — those host most
    # armours' built female meshes and must be visible for mesh resolution.
    # Body-mod exclusion is a selection concern handled by `_name_ok`.
    _index_skip = {n.lower() for n in (index_skip_mods or set())}
    vfs: "dict" = {}
    if enabled_ordered and union_all:
        _prog(f"locating {len(union_all)} armour mesh path(s) across "
              f"{len(enabled_ordered)} enabled mods…")
        try:
            vfs = discovery.build_mesh_index(
                mods_root, enabled_ordered, target_keys=union_all,
                skip_mods=_index_skip)
        except Exception:
            vfs = {}
        _BATCH_MESH_INDEX[str(mods_root).lower()] = vfs

    # #bsa-only-sources (2026-09-24): a mod whose armour meshes are in no loose
    # file anywhere may still ship them in an ARCHIVE, and the convert step
    # resolves from archives (`_BsaMeshIndex`) -- but this gate only ever looked
    # at loose files, so such a mod was dropped here before conversion could
    # run, in silence: it appeared in no log and no report. Measured on a real
    # modlist: 9 mods (a fur armour set's update, a clothing set, a quest
    # overhaul and its hotfix, a scarf, cloaks, robes), 37 pieces, 49 NIFs
    # planned after the female-only rule, 66 armatures the coverage step had
    # been minting over an unconverted mesh; 0 child and 0 creature pieces
    # (DefaultRace only, and the child and non-playable gates still apply);
    # the other 138 sources plan exactly what they did. The index is lookup-only
    # (staging None: it can never write) over the SAME folders the convert step
    # lists, and lazy: it is read only when some mod finds nothing loose. The
    # convert step adopts its listing. CBBE2UBE_NO_BSA_ONLY_SOURCES=1 turns this
    # off, and with it the log of mods still dropped.
    _bsa_only = not _flag("CBBE2UBE_NO_BSA_ONLY_SOURCES", False)
    _sel_bsa = None
    if _bsa_only and pending_vfs:
        try:
            _sel_bsa = _BsaMeshIndex(
                _load_order_bsa_dirs(mods_root, enabled_ordered,
                                     paths.discover_layout().game_data_dirs),
                None)
            _SELECTION_BSA_INDEX[str(mods_root).lower()] = _sel_bsa
        except Exception:
            _sel_bsa = None
    _found_nowhere: "list[str]" = []
    for mod_dir, armor_bases in pending_vfs:
        c = sum(1 for b in armor_bases
                if any(f"{b}{suf}.nif" in vfs for suf in ("_1", "_0", "")))
        if c == 0 and _sel_bsa is not None:
            c = sum(1 for b in armor_bases
                    if any(_sel_bsa.contains(f"{b}{suf}.nif")
                           for suf in ("_1", "_0", "")))
        if c == 0:
            if _bsa_only:
                _found_nowhere.append(mod_dir.name)
            continue  # armour meshes genuinely don't exist anywhere
        candidates.append({
            "name": mod_dir.name, "path": mod_dir, "armor_nifs": c,
            "esps": sum(1 for _ in mod_dir.rglob("*.esp"))})
    if _found_nowhere:
        print(f"  armour meshes found nowhere: {len(_found_nowhere)} mod(s) equip "
              "armour whose meshes are in no enabled mod's loose files or "
              "archives, so they are not converted:")
        # EVERY name, uncapped: this line is the only place such a mod is ever
        # named -- it is in no report -- so a "... and N more" would leave the
        # ones past the cut as silent as before. 2 on a 3,254-plugin modlist.
        for n in _found_nowhere:
            print(f"    - {n}")
    if _worn_admitted:
        print(f"  non-playable armour a female NPC wears: {len(_worn_admitted)} "
              f"armature(s) in {_worn_mods} mod(s) kept for conversion "
              "(these were skipped before)")

    # Duplicate-plugin dedup: when the same filename ships in multiple mods, the
    # game loads only the highest-MO2-priority copy. Patching a lower-priority
    # copy emits overrides for the wrong FormIDs -> invisible armor on UBE.
    # Drop any candidate whose every top-level plugin is already claimed.
    if enabled_ordered:
        _prio = {n: i for i, n in enumerate(enabled_ordered)}  # lower index = wins

        def _top_plugins(mod_name: str) -> "set[str]":
            md = mods_root / mod_name
            try:
                return {p.name.lower() for p in md.iterdir()
                        if p.is_file()
                        and p.suffix.lower() in (".esp", ".esm", ".esl")}
            except OSError:
                return set()

        claimed: "set[str]" = set()
        kept: list[dict] = []
        dropped_dup: list[str] = []
        for c in sorted(candidates, key=lambda c: _prio.get(c["name"], 1 << 30)):
            plugs = _top_plugins(c["name"])
            if plugs and plugs <= claimed:
                dropped_dup.append(c["name"])
                continue
            claimed |= plugs
            kept.append(c)
        if dropped_dup:
            print(f"  duplicate-plugin dedup: dropped {len(dropped_dup)} "
                  "lower-priority source(s) whose plugin(s) a higher-priority "
                  "mod already provides (the game loads the higher copy):")
            for n in dropped_dup[:10]:
                print(f"    - {n}")
            if len(dropped_dup) > 10:
                print(f"    ... and {len(dropped_dup) - 10} more")
        candidates = kept

    candidates.sort(key=lambda c: c["armor_nifs"], reverse=True)
    return candidates


def _cmd_scan(args):
    """Walk an MO2 mods root and list mods that look like CBBE armor candidates."""
    mods_root: Path = args.mods_root
    if not mods_root.is_dir():
        print(f"not a directory: {mods_root}"); return 2
    candidates = _find_armor_mod_dirs(mods_root)
    print(f"Found {len(candidates)} mods that look like armor (ESP + NIFs under armor/clothes/outfits paths):")
    print(f"{'mod':<60} {'esps':>5} {'armor':>6}")
    print("-" * 80)
    for c in candidates[:args.limit]:
        print(f"{c['name']:<60} {c['esps']:>5} {c['armor_nifs']:>6}")
    if len(candidates) > args.limit:
        print(f"... ({len(candidates) - args.limit} more)")
    return 0


def list_convertible_mods(output_dir: "Path | None" = None,
                          progress=None) -> list:
    """Discover the armor mods the `auto` pipeline would convert, WITHOUT
    converting — for the GUI selection list. Mirrors `_cmd_auto`'s discovery
    EXACTLY so the names match what `--only-mods` filters against. Returns
    [{'name': str, 'nifs': int}] in load-priority order. Returns [] if the
    modpack layout can't be located."""
    lay = paths.discover_layout()
    paths.export_to_env(lay)
    mr = paths.mods_root()
    if mr is None or not mr.is_dir():
        return []
    output = output_dir if output_dir else (mr / "CBBEtoUBE Auto")
    enabled = paths.enabled_mods(lay)
    exclude = {output.name} | _body_mod_names(mr)
    try:
        cands = _find_armor_mod_dirs(
            mr, extra_exclude_names=exclude, enabled_names=enabled,
            require_arma=True, enabled_ordered=paths.enabled_mods_ordered(lay),
            index_skip_mods={output.name}, progress=progress)
    finally:
        # That scan parsed every enabled plugin into esp's cache, and this
        # returns only names and counts; the converter child parses what it
        # needs on its own. Kept, the parses stayed in the GUI process for the
        # whole session. #esp-cache-release
        from . import esp as _esp
        _esp.clear_load_cache()
        # Same for the archive listing selection may have read: only a convert
        # in THIS process could reuse it, and the GUI never converts in-process.
        # #bsa-only-sources
        _SELECTION_BSA_INDEX.pop(str(mr).lower(), None)
    prio = paths.enabled_mods_ordered(lay)
    if prio:
        rank = {name: i for i, name in enumerate(prio)}
        cands.sort(key=lambda c: rank.get(c["name"], len(rank)))

    def _n(c):                       # armor_nifs is a COUNT in _cmd_auto / scan
        v = c.get("armor_nifs", 0)
        return len(v) if isinstance(v, (list, tuple, set)) else int(v or 0)
    out = [{"name": c["name"], "nifs": _n(c)} for c in cands]
    # Vanilla sweep pseudo-source, LAST (mirrors its lowest-priority position
    # in _cmd_auto). The name must be exactly "vanilla" — that's the token
    # --only-mods special-cases — so Select-mods runs can rerun just the sweep.
    if (not _flag("CBBE2UBE_NO_VANILLA_SWEEP", False)
            and lay.game_data_dirs
            and _vanilla_sweep_esps(Path(lay.game_data_dirs[0]))):
        out.append({"name": "vanilla", "nifs": 0})
    return out


def list_overlay_mods() -> list:
    """Discover the enabled mods that provide convertible body/hands/feet
    overlays -- for the GUI overlay-mod picker. Returns [{'name': str}] in
    load-priority order, or [] if the modpack layout can't be located. Names
    match what `--overlay-mods` filters against. (No broad except: a real error
    should surface in the caller's log, not masquerade as 'no overlays found'.)"""
    from . import overlay_transfer          # imported lazily, as elsewhere here
    lay = paths.discover_layout()
    paths.export_to_env(lay)
    mr = paths.mods_root()
    if mr is None or not mr.is_dir():
        return []
    names = overlay_transfer.list_overlay_mods(
        lay, skip_mods={(mr / "CBBEtoUBE Auto").name})
    return [{"name": n} for n in names]


# UBE detection is SHAPE-based: a source mesh built for UBE hugs the UBE body,
# a CBBE/3BA mesh hugs the CBBE body -- so the mean nearest-neighbour distance of
# a mesh to each reference body tells which body it was built for. (Bone names do
# NOT work: CBBE 3BA physics meshes share UBE's front/rear-thigh scale bones.)
# The verdict is by RATIO -- one body has to be clearly closer than the other --
# not absolute distance, since armor sits slightly off the body it was built for.
_UBE_HUG_DIST = 0.15         # a mesh this close to a body is a decisive fit
_FIT_RATIO = 1.5             # the far body must be >=1.5x the near body to decide
_BODY_TREE_CACHE: dict = {}


def _largest_shape_verts(path):
    """Verts of a NIF's body/largest shape as an (N,3) float array, or None."""
    import numpy as np
    try:
        nf = nif_convert._pynifly().NifFile(filepath=str(path))
    except Exception:
        return None
    s = (next((x for x in nf.shapes if x.name in ("BaseShape", "3BA")), None)
         or max(nf.shapes, key=lambda x: len(x.verts), default=None))
    if s is None:
        return None
    try:
        return np.asarray(s.verts, dtype=np.float64)
    except Exception:
        return None


def _body_trees():
    """(ube_tree, cbbe_tree) KD-trees over the UBE and CBBE reference body verts,
    cached. (None, None) if either body can't be located/read."""
    if not _BODY_TREE_CACHE:
        res = (None, None)
        try:
            from scipy.spatial import cKDTree
            ube_p = nif_convert._find_ube_femalebody("_1")
            cbbe_p = nif_convert._find_cbbe_base_body("_1")
            ube_v = (nif_convert._cached_ube_body_verts(ube_p)[1]
                     if ube_p is not None else None)
            cbbe_v = _largest_shape_verts(cbbe_p) if cbbe_p is not None else None
            if (ube_v is not None and cbbe_v is not None
                    and len(ube_v) and len(cbbe_v)):
                res = (cKDTree(ube_v), cKDTree(cbbe_v))
        except Exception:
            res = (None, None)
        _BODY_TREE_CACHE["t"] = res
    return _BODY_TREE_CACHE["t"]


# Head/face/hair meshes sit nowhere near the body -> useless (and misleading) for
# a body-shape fit; drop them from the detector's sample.
_NONBODY_MESH_HINTS = ("head", "face", "hair", "brow", "eye", "scalp", "mouth",
                       "teeth", "tongue", "beard", "facegen", "tint")


def _mod_armor_nifs(mod_dir, limit: int):
    r"""Up to `limit` of a mod's own body/armor NIFs, the body mesh first (it
    hugs the reference body most tightly). Head/face/1st-person meshes are
    dropped.

    Meshes already under `!UBE\` are dropped too, and that exclusion is what
    makes a whole-mod verdict safe. Such a mesh is UBE-shaped by definition and
    is never converted (`_is_already_ube_model` skips it), so sampling one
    answers the wrong question -- and it would dominate the answer: it hugs the
    UBE body exactly, `_tier` ranks a body mesh first, and `!` sorts ahead of
    letters, so a mod shipping BOTH a hand-made `!UBE` variant and its CBBE
    meshes would be judged entirely on the variant and skipped whole, taking
    the CBBE meshes that did need converting with it."""
    picks: list = []
    try:
        for nif in mod_dir.rglob("*.nif"):
            rel = str(nif.relative_to(mod_dir)).lower().replace("/", "\\")
            if "meshes\\" not in rel:
                continue
            if _is_already_ube_model(rel.split("meshes\\", 1)[1]):
                continue        # already UBE -> never converted, so not evidence
            if any(seg in rel for seg in _ENV_PATH_HINTS):
                continue
            low = nif.name.lower()
            if "firstperson" in low or "1stperson" in low:
                continue        # 1st-person hand meshes -- not body-shaped
            if any(h in rel for h in _NONBODY_MESH_HINTS):
                continue        # head/face/hair -- far from the body
            picks.append(nif)
    except OSError:
        return []

    def _tier(p):
        n = p.name.lower()
        if "femalebody" in n or n.startswith("body") or "_body" in n:
            return 0            # the actual body mesh -- most reliable
        if any(k in n for k in ("cuirass", "dress", "robe", "outfit", "armor",
                                "greave", "leg", "pant", "skirt", "body")):
            return 1
        return 2
    picks.sort(key=_tier)
    return picks[:limit]


def _mesh_body_fit(nif_path, ube_tree, cbbe_tree):
    """(dUBE, dCBBE) for the BEST-fitting shape in a NIF -- the one that hugs a
    body most tightly (min nearest-neighbour distance). Checking every shape,
    not just the largest, lets a bulky outer garment's tight base/body layer
    still classify the mod. Verts subsampled for speed. None if unreadable."""
    import numpy as np
    try:
        nf = nif_convert._pynifly().NifFile(filepath=str(nif_path))
    except Exception:
        return None
    best = None            # (dUBE, dCBBE, min)
    for s in nf.shapes:
        try:
            v = np.asarray(s.verts, dtype=np.float64)
        except Exception:
            continue
        if not len(v):
            continue
        if len(v) > 3000:
            v = v[np.linspace(0, len(v) - 1, 3000).astype(int)]
        du = float(ube_tree.query(v, k=1)[0].mean())
        dc = float(cbbe_tree.query(v, k=1)[0].mean())
        w = min(du, dc)
        if best is None or w < best[2]:
            best = (du, dc, w)
    return (best[0], best[1]) if best is not None else None


def _ube_native_verdict(mod_dir, ube_tree, cbbe_tree, sample_per_mod=6):
    """Is this mod's armor shaped for UBE (already converted) or for CBBE?

    Returns (verdict, confidence, signals) with verdict in ube/cbbe/unknown.
    Compares the mod's most body-hugging sampled mesh against the UBE and CBBE
    reference bodies.

    DELIBERATELY CONSERVATIVE: a verdict is only issued when some shape sits
    right ON one body (nearest distance under the hug threshold) AND that body
    is clearly the closer of the two. Bulky or off-body meshes drift toward the
    larger UBE body, so an inconclusive fit stays 'unknown' -- a CBBE mod must
    never be mislabelled 'ube', because acting on that skips converting real
    armor and leaves it unfitted in game.

    Shared by the GUI advisory scan and the conversion pipeline's backstop so
    the two can never disagree."""
    best = None
    if mod_dir is not None and mod_dir.is_dir():
        for nif in _mod_armor_nifs(mod_dir, sample_per_mod):
            fit = _mesh_body_fit(nif, ube_tree, cbbe_tree)
            if fit is None:
                continue
            du, dc = fit
            w = min(du, dc)
            if best is None or w < best[0]:
                best = (w, du, dc)
            if w < _UBE_HUG_DIST:
                break       # decisive body hug -> stop early
    if best is None:
        return "unknown", "low", ["no readable body mesh"]
    w, du, dc = best
    lo, hi = min(du, dc), max(du, dc)
    if lo < _UBE_HUG_DIST and hi >= lo * _FIT_RATIO:
        return ("ube" if du < dc else "cbbe"), "high", [
            f"shape fit: dUBE={du:.2f}, dCBBE={dc:.2f}"]
    return "unknown", "low", [f"shape fit: dUBE={du:.2f}, dCBBE={dc:.2f}"]


def _drop_ube_native_candidates(candidates: list) -> list:
    """Drop candidates whose armor is ALREADY shaped for UBE.

    Takes and returns the pipeline's candidate dicts ({"name", "path", ...}).
    Extracted from `_cmd_auto` so the WIRING can be tested: the previous inline
    version called `Path(candidate_dict)`, which raised TypeError into its own
    bare `except` and meant the gate never ran once. Every existing test called
    `_ube_native_verdict` directly, so none of them noticed.

    Fails OPEN at every step -- no reference bodies, an unreadable mod, or any
    other error converts as normal. Wrongly skipping a real CBBE mod leaves its
    armor unfitted in game, which is far worse than double-converting one."""
    try:
        ube_tree, cbbe_tree = _body_trees()
    except Exception:
        return candidates
    if ube_tree is None or cbbe_tree is None:
        return candidates
    native = []
    for c in candidates:
        try:
            verdict, conf, signals = _ube_native_verdict(
                c["path"], ube_tree, cbbe_tree)
        except Exception:
            continue            # unreadable -> convert as normal
        if verdict == "ube" and conf == "high":
            native.append((c["name"], signals[0] if signals else ""))
    if not native:
        return candidates
    skip = {n for n, _s in native}
    print(f"  UBE-native scan: skipping {len(native)} mod(s) whose armor "
          "already fits the UBE body (converting them would double-convert):")
    for n, s in sorted(native):
        print(f"    - {n}  [{s}]")
    print("    (override with --no-ube-native-scan)")
    return [c for c in candidates if c["name"] not in skip]


def scan_ube_native(domain: str = "armor", sample_per_mod: int = 6,
                    progress=None) -> list:
    """Flag convertible ARMOR mods whose meshes are shaped for UBE (or another
    non-CBBE body) rather than CBBE/3BA -- converting those would break them.
    Compares each mod's most body-hugging mesh to the UBE vs CBBE reference body.

    Overlays are textures (no meshes) so they are NOT scanned here -- the GUI
    keeps the name heuristic for that domain. Returns
    [{'name', 'verdict': 'ube'|'cbbe'|'unknown', 'confidence': 'high'|'low',
    'signals': []}] in load-priority order. `progress(done, total, name)` per mod."""
    if domain != "armor":
        return []
    try:
        mods = list_convertible_mods()      # also exports the layout to env
    except Exception:
        return []
    ube_tree, cbbe_tree = _body_trees()
    if ube_tree is None or cbbe_tree is None:
        return []                           # no reference body -> no verdicts
    mr = paths.mods_root()
    out: list = []
    total = len(mods)
    for i, m in enumerate(mods):
        name = m["name"]
        if progress is not None:
            try:
                progress(i + 1, total, name)
            except Exception:
                pass
        verdict, conf, signals = _ube_native_verdict(
            (mr / name) if mr is not None else None,
            ube_tree, cbbe_tree, sample_per_mod)
        out.append({"name": name, "verdict": verdict,
                    "confidence": conf, "signals": signals})
    return out


def _split_mod_arg(vals):
    """Parse a repeatable + comma-separated --*-mods CLI arg into a list of mod
    names, or None when unset/empty."""
    if not vals:
        return None
    out = [n.strip() for chunk in vals for n in chunk.split(",") if n.strip()]
    return out or None


def _cmd_auto(args):
    """One-click full pipeline (no args required): auto-discover the modpack,
    find ALL CBBE/3BA armor mods, convert them into one output mod, merge into
    the Combined ESP, and emit vanilla race coverage. This is what the MO2
    executable button runs."""
    import argparse as _ap
    # FIRST thing in the log, before discovery and before anything can abort: a run
    # that dies early still has to say what flags it was carrying. #settings-did-not-apply
    _echo_active_experiment_flags()
    lay = paths.discover_layout()
    paths.export_to_env(lay)
    mr = paths.mods_root()
    if mr is None or not mr.is_dir():
        print("error: could not locate the MO2 mods folder. Run this from "
              "inside the modpack, or set CBBE2UBE_MODS_ROOT.")
        return 2
    print(f"  mods root: {mr}")
    if lay.game_data_dirs:
        print(f"  game Data: {lay.game_data_dirs[0]}")

    output = (args.output if getattr(args, "output", None)
              else mr / "CBBEtoUBE Auto")
    enabled = paths.enabled_mods(lay)
    if enabled is not None:
        print(f"  active profile: {len(enabled)} mod(s) enabled "
              f"(disabled mods skipped)")

    # OVERLAYS-ONLY: skip the armor convert / merge / coverage entirely and just
    # rebake body overlays into UBE UV. Lets you refresh overlays without a full
    # (slow) armor reconvert. Returns right after.
    if getattr(args, "overlays_only", False):
        from . import overlay_transfer
        print("\n--- OVERLAYS-ONLY: body overlay (tattoo) -> UBE UV transfer ---")
        ovl = overlay_transfer.convert_overlays(
            output, lay,
            overlay_mode=("copy" if getattr(args, "overlay_copy", False)
                          else "replace"),
            skip_male=getattr(args, "overlay_skip_male", False),
            only_mods=_split_mod_arg(getattr(args, "overlay_mods", None)),
            exclude_mods=_split_mod_arg(getattr(args, "overlay_exclude_mods", None)))
        if ovl.get("converted"):
            print(f"  *** {ovl['converted']} overlay(s) remapped to UBE UV under "
                  f"'{output.name}'. ENABLE it and ensure it WINS over the "
                  f"overlay mods so the loose textures override their BSAs. ***")
        return 0 if (ovl.get("converted")
                     or ovl.get("reason") == "none-found") else 1

    # Exclude body mods (CBBE + UBE) -- by what each folder ships, never by
    # which body the fit uses (see _body_mod_names).
    exclude = {output.name} | _body_mod_names(mr)
    # User exclusions (mods already built for UBE, or ones to leave alone).
    _user_excl = _split_mod_arg(getattr(args, "exclude_mods", None)) or []
    if _user_excl:
        exclude |= set(_user_excl)
        print(f"  --exclude-mods: skipping {len(_user_excl)} mod(s): "
              + ", ".join(sorted(_user_excl)))

    print("  scanning mods for player-equippable armor...")
    candidates = _find_armor_mod_dirs(
        mr, extra_exclude_names=exclude, enabled_names=enabled,
        require_arma=True, enabled_ordered=paths.enabled_mods_ordered(lay),
        # Skip only our output mod in the index (not body/BodySlide mods).
        index_skip_mods={output.name})
    if not candidates:
        print("error: found no equippable armor mods to convert.")
        return 2

    # BACKSTOP: drop mods whose armor is ALREADY shaped for UBE.
    #
    # The `!UBE\` path gate catches converted output and UBE-native mods that
    # follow the convention, but a UBE-native mod shipping at NORMAL paths
    # (meshesrmor\...) looks exactly like a CBBE source. Converting it refits
    # an already-UBE mesh onto the UBE body a second time and breaks it. This
    # used to be caught only by the mod NAME containing "ube" -- a heuristic
    # that both missed renamed mods and over-matched innocent ones.
    #
    # Geometry decides instead: compare the most body-hugging sampled mesh
    # against the UBE and CBBE reference bodies. Only a HIGH-confidence "ube"
    # verdict skips a mod -- an inconclusive fit stays "unknown" and converts
    # as normal, because wrongly skipping a real CBBE mod leaves its armor
    # unfitted in game. Needs both reference bodies; without them the scan
    # returns nothing and the pipeline is unchanged.
    if not getattr(args, "no_ube_native_scan", False):
        candidates = _drop_ube_native_candidates(candidates)

    # --only-mods: reconvert a subset. The merge still re-globs ALL patches in
    # _unmerged_patches/ so unselected mods keep their existing patch + meshes.
    only = getattr(args, "only_mods", None)
    _sweep_only_requested = False
    if only:
        wanted = {n.strip().lower()
                  for chunk in only for n in chunk.split(",") if n.strip()}
        # "vanilla" selects the vanilla sweep (a pseudo-source, not a mod dir).
        _sweep_only_requested = "vanilla" in wanted
        wanted.discard("vanilla")
        before = len(candidates)
        all_names = [c["name"] for c in candidates]
        candidates = [c for c in candidates if c["name"].lower() in wanted]
        missing = sorted(wanted - {c["name"].lower() for c in candidates})
        print(f"  --only-mods: {len(candidates)}/{before} mod(s) selected"
              + (f"; NOT FOUND: {missing}" if missing else ""))
        if missing:
            # THE OLD MESSAGE SENT THE READER TO THE WRONG LIST. `scan` prints
            # mods that merely LOOK like armour (ESP + NIFs under armour paths);
            # this filter matches the CONVERSION candidates, which is a
            # different, plugin-driven set. A mod can sit in `scan`'s table and
            # be absent here -- that cost three arms on 2026-09-07 -- so name
            # the right list and show the near misses instead of a bare refusal.
            for miss in missing:
                real = _near_mod_names(miss, all_names)
                if real:
                    print(f"    '{miss}' -- did you mean: "
                          + ", ".join(f"'{n}'" for n in real))
        if not candidates and not _sweep_only_requested:
            print("error: --only-mods matched no CONVERSION candidates. That is "
                  "NOT the same list as `scan`, which shows anything that looks "
                  "like armour; use the GUI 'Refresh mod list', or "
                  "`auto_convert.list_convertible_mods()`, which mirrors this "
                  "filter exactly.")
            return 2

    # Order by MO2 load priority (highest first) so the first-writer-wins collision
    # guard picks the same mesh the game would load.
    prio = paths.enabled_mods_ordered(lay)
    if prio:
        rank = {name: i for i, name in enumerate(prio)}
        candidates.sort(key=lambda c: rank.get(c["name"], len(rank)))
        print("  (sources ordered by MO2 load priority so conflict winners "
              "match in-game)")

    sources = [c["path"] for c in candidates]

    # VANILLA SWEEP: the base game itself is the LAST (lowest-priority) source.
    # Vanilla armor coverage used to be incidental — a vanilla mesh converted
    # only when some mod carried an override of its ARMA — so armor nobody
    # overrides was never converted and rendered invisible on UBE actors.
    # The sweep plans every deforming DefaultRace ARMA straight from the game
    # masters; meshes resolve via the vanilla BSAs, and merge-time link dedup
    # keeps mod-source links wherever both cover the same armor.
    # CBBE2UBE_NO_VANILLA_SWEEP=1 disables. Under --only-mods, the sweep runs
    # only when named explicitly ('vanilla').
    if (not _flag("CBBE2UBE_NO_VANILLA_SWEEP", False)
            and lay.game_data_dirs
            and (not only or _sweep_only_requested)
            # --exclude-mods vanilla: honored for the sweep too — a control
            # that silently no-ops is worse than none.
            and "vanilla" not in {n.lower() for n in (_user_excl or [])}):
        _sweep_dir = lay.game_data_dirs[0]
        _sw_ok, _sw_why = _preflight_vanilla_sweep(_sweep_dir)
        if _sw_ok:
            sources.append(_sweep_dir)
            print(f"  + vanilla sweep source (Skyrim + DLC masters): "
                  f"{_sweep_dir} ({_sw_why})")
        else:
            # Fail EARLY and loud: a sweep that can't plan on this layout
            # would otherwise die as the LAST source, hours in. Mod
            # conversion is unaffected; vanilla armor stays uncovered.
            warn(f"vanilla sweep DISABLED this run: {_sw_why}",
                 consequence="vanilla armour that no mod overrides stays unlinked, so it "
                             "is invisible on UBE actors until a run with the sweep")
            print("     (mod armor converts normally; vanilla armor no mod "
                  "overrides stays unconverted. Fix the game-Data path or "
                  "report this if the path looks right.)")
    print(f"\n=== auto: {len(sources)} armor mod(s) to convert ===")
    for c in candidates[:30]:
        print(f"  {c['name']}  ({c['armor_nifs']} NIFs)")
    if len(candidates) > 30:
        print(f"  ... and {len(candidates) - 30} more")

    if getattr(args, "list_only", False):
        print("\n--list-only: no conversion performed.")
        return 0

    # SkyPatcher-only: the Combined overrides no third-party records, so there is
    # no ARMO winner-rebase step (the whole winner-index build is gone).
    merged_name = getattr(args, "merged_name", "CBBE_to_UBE_Combined.esp")

    conv = _ap.Namespace(
        sources=sources, output=output, esp_name=None,
        no_textures=getattr(args, "no_textures", False),
        copy_textures=getattr(args, "copy_textures", False),
        ube_body_ref=None, workers=getattr(args, "workers", None),
        unmerged_patch_subdir="_unmerged_patches",
        auto_merge=getattr(args, "auto_merge", True),
        merged_name=merged_name,
        render_previews=False, mods_root=mr,
        incremental=getattr(args, "incremental", False),
        plugins_only=getattr(args, "plugins_only", False),
        # The coverage winner scan must leave the excluded mods' armour alone
        # too, not only skip their meshes -- on a Select-mods run as well, where
        # the window passes them as --coverage-exclude-mods. #exclude-owned-coverage
        exclude_mods=(_user_excl + (_split_mod_arg(
            getattr(args, "coverage_exclude_mods", None)) or [])) or None,
    )
    rc = _cmd_convert(conv)
    # Failures of anything that runs AFTER `rc` was fixed by the convert above,
    # so an exception down here still fails the run instead of exiting 0.
    #
    # THIS COUNTER WENT DEAD. It was written for the standalone race-compat and
    # mod-coverage phases that used to live below; both were removed (vanilla
    # race coverage 2026-07-03, and the `#fsp-dedup` block along with the
    # standalone coverage passes), unified coverage moved INSIDE `_cmd_convert`
    # where its rc IS checked, and nothing was left to increment it -- while
    # the comment went on claiming the guard was live. Found 2026-09-04 by an
    # audit for gates assigned a constant and never set: the same shape as the
    # `#coherence-kink` branch, which was documented in full and never
    # implemented.
    #
    # The one post-convert step that CAN still fail is the opt-in overlay
    # transfer, which caught its own exception and let the run exit 0. It now
    # counts, so `--convert-overlays` failing is visible in the exit code.
    post_merge_failures = 0

    # Vanilla race coverage (Vanilla_UBE_Race_Compat.esp) REMOVED 2026-07-03:
    # RaceCompatibility SKSE / RaceDispatcher does this race + nude-skin dispatch
    # at runtime, so the static patch was redundant (and could conflict with the
    # dispatcher). RaceCompatibility the MOD is still a UBE prereq. The patch and
    # its generators have been removed.

    # Mod-defined non-body coverage: overhauls re-armature vanilla helmets/jewelry
    # with their own ARMAs listing only vanilla races -> invisible on UBE actors.
    # NOTE: the #fsp-dedup exclude-set block that stood here is GONE with the
    # standalone coverage passes. It parsed the Combined INI to stop those
    # passes re-covering an already-linked ARMO. Unified coverage is now the
    # SOLE generator, so there is no second pass to exclude anything from,
    # and the set it built had no remaining reader.

    # OPT-IN: remap CBBE/3BA body overlays to UBE UV. Writes loose DDS at the
    # original texture paths so RaceMenu loads them via load order. Needs texconv.
    if getattr(args, "convert_overlays", False):
        try:
            from . import overlay_transfer
            print("\n--- body overlay (tattoo) -> UBE UV transfer ---")
            ovl = overlay_transfer.convert_overlays(
                output, lay,
                overlay_mode=("copy" if getattr(args, "overlay_copy", False)
                              else "replace"),
                skip_male=getattr(args, "overlay_skip_male", False),
                only_mods=_split_mod_arg(getattr(args, "overlay_mods", None)),
                exclude_mods=_split_mod_arg(getattr(args, "overlay_exclude_mods", None)))
            if ovl.get("converted"):
                print(f"  *** {ovl['converted']} overlay(s) remapped to UBE UV. "
                      f"ENABLE '{output.name}' and ensure it WINS over the "
                      f"overlay mods so the loose textures override their BSAs. "
                      f"***")
        except Exception as e:
            # Counted: the user explicitly asked for this with
            # --convert-overlays, so exiting 0 hides that the textures they
            # expect were never written.
            post_merge_failures += 1
            warn(f"overlay transfer FAILED: {plain_error(e)}",
                 consequence="overlays were not transferred this run")

    # Pre-flight: missing hands/feet .tri makes them stay CBBE-shaped while the
    # body morphs UBE (built without 'Build Morphs'). Surface the warning loudly.
    try:
        morph_warns = nif_convert.check_ube_nude_morph_files()
        if morph_warns:
            warn("UBE nude-skin morph check:",
                 consequence="listed below: a nude part without its morph file stays at "
                             "base shape while the body morphs",
                 fix="rebuild that part in BodySlide with 'Build Morphs' checked",
                 indent="\n  ")
            for w in morph_warns:
                print(f"     - {w}")
    except Exception:
        pass

    _enable = f"'{output.name}' + its Combined ESP(s)"
    if post_merge_failures:
        warn(f"{post_merge_failures} post-convert phase(s) FAILED",
             consequence="see the errors above; the run is reported as failed",
             indent="\n  ")
    # Re-write with any coverage-phase failures appended (same in-process
    # _RUN_FAILURES list _cmd_convert already wrote).
    _write_failures_file()
    print(f"\n=== auto: done — enable {_enable} in MO2. ===")
    return rc or (2 if post_merge_failures else 0)


def _cmd_discover_body_ref(args):
    p = _find_ube_body_ref()
    if p is None:
        print("No UBE body ref found. You'll need to point --ube-body-ref "
              "at a NIF containing both BaseShape (>=20k v) and VirtualBody "
              "(>=10k v). Try a BodySlide-built UBE armor NIF.")
        return 1
    print(p)
    return 0


def _cmd_merge(args):
    patches = [Path(p) for p in args.patches]
    if len(patches) < 2:
        print("error: merge needs at least 2 patches to combine.")
        return 2
    missing = [p for p in patches if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: not found: {p}")
        return 2
    print(f"merging {len(patches)} patch ESP(s) into {args.output} ...")
    # Discover master dirs so the merger can read real ESM flags (USSEP ordering etc).
    try:
        _lay = paths.discover_layout()
        paths.export_to_env(_lay)
        _mr = paths.mods_root()
        _mdd = _discover_master_data_dirs(
            (_mr / "_") if _mr else patches[0].parent)
    except Exception:
        _mdd = None
    stats = ube_patcher.merge_patches_split(
        patches, args.output,
        esl_flag=not args.no_esl_flag,
        author=args.author,
        description=args.description,
        master_data_dirs=_mdd,
    )
    print(f"  wrote     : {stats.get('output', args.output)}")
    print(f"  ESL flag  : {stats.get('esl_flagged', False)}")
    if stats.get('split_pieces', 1) > 1:
        print(f"  SPLIT     : {stats['split_pieces']} ESL pieces "
              f"-> {', '.join(stats.get('pieces', []))} (enable ALL)")
    masters = stats.get("masters", [])
    print(f"  masters   : {len(masters)}")
    print(f"  ARMA total: {stats.get('total_arma_records', '?')} "
          f"(own: {stats.get('own_arma_records', '?')}"
          f"/{stats.get('esl_slots_max', '?')} ESL slots)")
    print(f"  ARMO total: {stats.get('total_armo_records', '?')}"
          f" (dedup: {stats.get('armo_duplicates_merged', 0)} duplicates "
          "merged)")
    # FULL SKYPATCHER: write the armorAddonsToAdd INI next to the output
    # (same layout as the integrated path: <modroot>/SKSE/Plugins/SkyPatcher).
    _sp_lines = stats.get("skypatcher_ini_lines") or []
    if _sp_lines:
        _outp = Path(stats.get('output', args.output))
        _sp_ini_path = (_outp.parent / "SKSE" / "Plugins" / "SkyPatcher"
                        / "armor" / (_outp.stem + ".ini"))
        from .atomic_io import atomic_write_bytes
        atomic_write_bytes(_sp_ini_path,
                           ("\n".join(_sp_lines) + "\n").encode("utf-8"))
        print(f"  FULL SKYPATCHER: {stats.get('skypatcher_targets')} armor "
              f"record(s) -> {_sp_ini_path}")
        for _rl in ube_patcher.report_link_reconciliation(stats):
            print(_rl)
    # Self-heal a stale/mis-sorted master list before validating (no-op if clean).
    try:
        _nrs = ube_patcher.resort_masters_all(
            Path(stats.get('output', args.output)), master_data_dirs=_mdd)
        if _nrs:
            print(f"  master re-sort: repaired {_nrs} mis-ordered piece(s)")
    except Exception as _e:
        warn(f"master re-sort failed: {plain_error(_e)}",
             consequence="the Combined ESP's masters may be out of order, and the game "
                         "may refuse to load it",
             fix="check the merged plugin's master list in xEdit")
    # Postflight the FINAL merged output (+ any ESL split pieces) -- this is the
    # exact artifact the validator exists to guard (master-ordering / ESL-overflow
    # / malformed-MODT CTD class). The integrated auto/convert path already does
    # this; the standalone `merge` subcommand must not skip it.
    try:
        _merged = Path(stats.get('output', args.output))
        _pf = ube_patcher.postflight_validate_combined(
            _merged, master_data_dirs=_mdd,
            mesh_resolves=_outside_ube_mesh_resolver(_merged.parent))
        if _pf["ctd"]:
            warn(f"POSTFLIGHT CTD on merged output: {len(_pf['ctd'])} load-breaking issue(s)",
                 consequence="NOT safe to load; listed below")
            for _piece, _w in _pf["ctd"]:
                print(f"       {_piece}: {_w}")
            return 2
        if _pf["soft"]:
            print(f"  postflight: {len(_pf['soft'])} soft warning(s) "
                  "(invisible/cosmetic, non-fatal)")
    except Exception as _pfe:
        warn(f"postflight validation skipped: {plain_error(_pfe)}",
             consequence="the plugin was not checked for load-breaking issues")
    return 0


def _cmd_validate(args):
    mod_dir = Path(args.mod_dir)
    if not mod_dir.is_dir():
        print(f"error: not a directory: {mod_dir}")
        return 2
    esps = sorted(mod_dir.glob("*.esp"))
    if not esps:
        print(f"error: no .esp files in {mod_dir}")
        return 2

    check_nifs = not args.no_nifs
    if check_nifs:
        if args.meshes_root:
            meshes_root = Path(args.meshes_root)
            if not meshes_root.is_dir():
                print(f"warning: --meshes-root not a directory: {meshes_root}")
                meshes_root = None
        else:
            candidate = mod_dir / "meshes"
            meshes_root = candidate if candidate.is_dir() else None
    else:
        meshes_root = None

    print(f"validating {len(esps)} ESP(s) in {mod_dir}")
    if check_nifs and meshes_root:
        print(f"  meshes/ root: {meshes_root}")
    elif check_nifs:
        print("  NIF check: SKIPPED (no meshes/ folder found)")
    else:
        print("  NIF check: DISABLED via --no-nifs")

    # Master-tier classification needs to OPEN each master to read its TES4
    # flags, because an ESL-flagged .esp (ESPFE) is master-tier while looking
    # like a regular .esp. Without the search dirs `_is_esm_tier_master` cannot
    # open them and falls back to "regular", so every ESPFE master is
    # misclassified and any .esm/.esl later in the list trips a bogus
    # "master-ordering ... crash" -- on output whose order is actually correct.
    _mdd = None
    try:
        _vlay = paths.discover_layout()
        _vidx = paths.plugin_file_index(_vlay)
        if paths.root_plugin_index_on():
            # #root-plugin-index: in the index's priority order (highest first),
            # so a master name two folders ship resolves to the copy the game
            # loads.
            _mdd = list(dict.fromkeys(Path(p).parent for p in _vidx.values())) or None
        else:
            _mdd = sorted({Path(p).parent for p in _vidx.values()}) or None
    except Exception as _e:
        print(f"  note: no plugin index ({type(_e).__name__}); ESL-flagged .esp "
              "masters may be misreported as ordering errors")
    if _mdd:
        print(f"  master lookup: {len(_mdd)} dir(s)")

    total_warnings = 0
    failing = 0
    # The coverage step points some `!UBE\` slots at other mods' meshes on purpose
    # (the UBE body's own hands/feet; by default, hand-made twins). #coverage-nude-skin
    _outside = _outside_ube_mesh_resolver(mod_dir) if meshes_root else None
    for esp_path in esps:
        warnings = ube_patcher.validate_patch(
            esp_path, meshes_root=meshes_root, check_nifs=check_nifs,
            master_data_dirs=_mdd, mesh_resolves=_outside)
        if warnings:
            failing += 1
            total_warnings += len(warnings)
            warn(f"{esp_path.name}",
                 consequence=f"{len(warnings)} warning(s), listed below", indent="\n  ")
            for w in warnings:
                print(f"     {w}")
        else:
            print(f"  [OK] {esp_path.name}")
    print("\n=== validation summary ===")
    print(f"  ESPs checked     : {len(esps)}")
    print(f"  ESPs with issues : {failing}")
    print(f"  total warnings   : {total_warnings}")
    return 0 if failing == 0 else 1


def _cmd_check_setup(args) -> int:
    """`check-setup`: the GUI's Check setup without a window -- for a command
    line, or a user whose GUI will not start. #check-setup"""
    from . import preflight
    checks = preflight.run_checks()
    for line in preflight.format_checks(checks):
        print(line)
    return 1 if preflight.overall(checks) == preflight.FAIL else 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "scan":
        return _cmd_scan(args)
    if args.cmd == "discover-body-ref":
        return _cmd_discover_body_ref(args)
    if args.cmd == "check-setup":
        return _cmd_check_setup(args)
    if args.cmd == "merge":
        return _cmd_merge(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "gui":
        from .gui import launch_gui
        return launch_gui()
    if args.cmd == "auto":
        return _cmd_auto(args)
    if args.cmd == "convert":
        return _cmd_convert(args)
    # No subcommand → GUI (default when double-clicked or run by MO2).
    # The headless pipeline is still available as `auto`.
    from .gui import launch_gui
    return launch_gui()


if __name__ == "__main__":
    sys.exit(main())
