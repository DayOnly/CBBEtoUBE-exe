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
      MyArmor UBE patch.esp        # new ESP via ube_patcher
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

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import ube_patcher, nif_convert, paths, discovery


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
      [alt_texture_shape_names]
    """
    src, dst, ube_body_ref_path, biped_slots = item[:4]
    alt_tex = item[4] if len(item) > 4 else None
    try:
        return nif_convert.convert_nif(
            src, dst,
            ube_body_ref_path=ube_body_ref_path,
            biped_slots=biped_slots,
            alt_texture_shape_names=alt_tex,
        )
    except Exception as e:
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
                print(f"    !! warm-up task failed: {e!r}")
    finally:
        manager.shutdown()
    total = time.perf_counter() - t0
    if init_times:
        print(f"    warm-up done in {total:.1f}s "
              f"(per-worker init avg {sum(init_times)/len(init_times):.1f}s, "
              f"max {max(init_times):.1f}s, "
              f"{len(pids)} distinct worker PID(s))")


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
        # Injectable for tests; defaults to a real ProcessPoolExecutor.
        self._factory = pool_factory or (
            lambda: ProcessPoolExecutor(max_workers=self.max_workers))
        self.pool = None
        self.rebuilds = 0
        self._ensure()

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
        death: the crasher surfaces as an error result, all others convert."""
        fn = fn or _nif_convert_worker
        items = list(work_items)
        if not items:
            return
        remaining = self._run_parallel(items, on_result, fn)
        if remaining:
            self._run_isolated(remaining, on_result, fn)

    def _run_parallel(self, items, on_result, fn):
        """Submit all items at once; deliver every result that completed cleanly,
        and return the items whose futures broke (the crasher + any in-flight
        bystanders) for isolated recovery. Rebuilds the pool if anything broke."""
        self._ensure()
        try:
            fut_to_item = {self.pool.submit(fn, it): it for it in items}
        except Exception:
            # Pool already broken at submit time -> nothing ran; recover all.
            self._rebuild()
            return items
        remaining = []
        broke = False
        for fut in as_completed(fut_to_item):
            it = fut_to_item[fut]
            try:
                on_result(fut.result())
            except Exception:
                broke = True          # worker death: this item didn't complete
                remaining.append(it)
        if broke:
            self._rebuild()
        return remaining

    def _run_isolated(self, items, on_result, fn):
        """Re-run the uncertain items one at a time on a healthy pool. With a
        single item in flight, a pool break unambiguously blames THAT item, so we
        error exactly the crasher and rebuild for the next. Bounded by
        GIVE_UP_AFTER consecutive crashes (systemic failure)."""
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
                on_result(self.pool.submit(fn, it).result())
                consec_crashes = 0
            except Exception as e:
                on_result(nif_convert.ConvertResult(
                    src_path=it[0], dst_path=None, status="error",
                    reason=f"worker process died (isolated convert): "
                           f"{type(e).__name__}: {e}"))
                self._rebuild()
                consec_crashes += 1
                if consec_crashes >= self.GIVE_UP_AFTER:
                    give_up = True


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
            print(f"  WARNING: {len(candidates)} body-ref candidates under "
                  f"{root}; scanning only the first 1500 by priority -- a UBE "
                  f"body in a deeply-nested non-'ube' path could be missed.",
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
        lines = [
            f"CBBE-to-UBE auto-conversion report (v{_app_version})",
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
        if self.nif_load_failures:
            lines.append(f"  ! load failures : {len(self.nif_load_failures)} "
                         f"(re-load the output via pynifly failed)")
        if self.nif_invariant_warnings:
            lines.append(f"  ! invariant warn: {len(self.nif_invariant_warnings)} "
                         "(zero-vert / over-cap partition on final output)")
            for w in self.nif_invariant_warnings:
                lines.append(f"      {w}")
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
            parts_lower = [s.lower() for s in p.parts]
            if any("backup" in s or "ube" in s for s in parts_lower):
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


# Structured record of everything that FAILED to convert this run, mirrored
# from the console summary as it prints. Written to
# CBBEtoUBE_last_failures.json next to the run log every run (empty list on a
# clean run so a reader can never see a PREVIOUS run's failures) -- the GUI
# shows it as an end-of-run popup; CLI users have the same info in the log.
_RUN_FAILURES: "list[dict]" = []


def _record_failure(kind: str, source, item, detail: str = "") -> None:
    _RUN_FAILURES.append({
        "kind": str(kind), "source": str(source),
        "item": str(item), "detail": str(detail)[:400]})


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
    try:
        _failures_file_path().write_text(
            _json.dumps({"failures": _RUN_FAILURES}, indent=1),
            encoding="utf-8")
    except OSError:
        pass


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
        print(f"  WARNING: {why}.")
        print("  SkyPatcher delivers ALL converted armor -- there is no ESP")
        print("  fallback. Without it every converted piece is INVISIBLE in-game.")
        print(f"  FIX: {fix}")
        print("  (Converting anyway: the output stays valid, no reconvert needed")
        print("   once SkyPatcher is in place.)")
        print("  " + "!" * 70)
        return False
    except Exception as e:
        print(f"  (SkyPatcher preflight skipped: {e!r})")
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
                        else f"{src_esp.stem} UBE patch.esp")
        out_esp = esp_out_dir / cur_out_name
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
            result.esp_gen_failures.append((src_esp.name, repr(e)))
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
) -> AutoConvertResult:
    """Run the full M2 + M3 phase 1 pipeline on a single CBBE armor mod.

    Args:
      source_dir: a CBBE armor mod folder (the kind MO2 would install)
      output_dir: where to write the UBE conversion mod folder
      output_esp_name: filename for the patch ESP (default:
        `<source_esp_stem> UBE patch.esp`)
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
    armor_bases = _player_armor_mesh_bases(
        source_dir, include_candidate_slots=True,
        mesh_resolves=_female_mesh_resolves)
    # Resolve through the full MO2 VFS so meshes in BodySlide-output / replacer /
    # patch mods are found. Falls back to source-local when no VFS index is given.
    # Sweep: NEVER fall into the ESP-less "convert every NIF the folder ships"
    # path — under the game Data dir that would convert every loose vanilla
    # mesh (skeletons, clutter, creatures).
    if _sweep_esps and not armor_bases:
        resolved_pairs = []
        result.notes.append(
            "vanilla sweep: no DefaultRace armour ARMAs resolved — nothing planned")
    else:
        resolved_pairs = _resolve_armor_meshes(
            armor_bases, mesh_vfs_index, meshes_root, all_nif_paths)
    # Single weight-agnostic slot resolver, shared by the crash guard below and
    # the work-item builder later. Built once from the source ESPs; a `_0` file
    # the ARMA never named inherits its `_1` partner's slots. #slot0-weight-partner
    try:
        _raw_slot_map = ube_patcher.build_nif_slot_map(
            _sweep_esps or _find_source_esps(source_dir))
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
        if _guard_dropped:
            result.notes.append(
                f"crash guard: dropped {_guard_dropped} non-body accessory "
                "mesh(es) on ambiguous modder slots (not body-skinned)")
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
                cur_out_name = f"{src_esp.stem} UBE patch.esp"
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

        work_items: list[tuple] = []
        skipped_collisions: list[tuple[Path, Path]] = []
        skipped_incremental = 0
        for src, rel in resolved_pairs:
            slot_bits = slot_bits_for(rel)
            dst = nif_dst_root / Path(rel)
            # SECURITY: `rel` can derive from a mod-controlled ARMA model path /
            # BSA name; refuse `..`/absolute traversal outside the output meshes.
            if not paths.is_within_dir(nif_dst_root, dst):
                print(f"  !! refusing traversal output path for {rel!r}",
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
                print(f"    {src.name!r} -> '{rel_disp}'  (earlier mod wins)")
            if len(skipped_collisions) > 5:
                print(f"    ... and {len(skipped_collisions) - 5} more")
            result.notes.append(
                f"NIF collisions skipped: {len(skipped_collisions)} "
                "(earlier source mod won the output path)")

        planned_output_nifs = {it[1] for it in work_items}

        if nif_workers is None:
            nif_workers = max(1, (os.cpu_count() or 4) - 1)
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

            for dst in planned_output_nifs:
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
                except Exception:
                    pass
        except ImportError:
            pass

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

def _build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="auto_convert",
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
                         help="filename for the patch ESP (default: '<stem> UBE patch.esp'). "
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
            "- armor renders empty); armo-missing-full (ARMO override has "
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
                             "double-convert and break them.")
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
        # Split zero-mesh mods: collision-skipped = duplicate source (armor IS
        # converted under another mod); zero-resolved = likely still missing.
        def _collision_skipped(r):
            return any("collision" in n.lower() for n in (r.notes or []))
        zero_all = [(s, r) for s, r in ok if len(r.nif_results) == 0]
        zero_dup = [(s, r) for s, r in zero_all if _collision_skipped(r)]
        zero = [(s, r) for s, r in zero_all if not _collision_skipped(r)]

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

        L.append("per-mod detail")
        for s, r in ok:
            if len(r.nif_results) == 0:
                flag = ("  (0 NIFs - all collision-skipped; converted under "
                        "another source)" if _collision_skipped(r)
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
                                 weight_warnings=None) -> "Path | None":
    """Machine-readable sibling of conversion_summary.txt, for the GUI health
    panel. Same batch stats plus the postflight invisibility-risk signal
    (weight-partner divergence). Best-effort; never raises."""
    import json
    try:
        ok = [(s, r) for s, r, e in results if r is not None and e is None]
        failed = [(s, e) for s, r, e in results if e is not None]

        def _collision(r):
            return any("collision" in n.lower() for n in (r.notes or []))
        zero_all = [(s, r) for s, r in ok if len(r.nif_results) == 0]
        zero_dup = [s.name for s, r in zero_all if _collision(r)]
        zero = [s.name for s, r in zero_all if not _collision(r)]
        rep = {
            "output_mod": str(output_dir),
            "source_mods": len(results),
            "converted_ok": len(ok),
            "hard_failures": len(failed),
            "armor_nifs": sum(len(r.nif_results) for _, r in ok),
            "esp_patches": sum(len(r.output_esps) for _, r in ok),
            "nif_errors": sum(r.nif_errors for _, r in ok),
            "load_failures": sum(len(r.nif_load_failures) for _, r in ok),
            "vfs_resolved": sum(r.vfs_other_mod_count for _, r in ok),
            "zero_mesh_mods": zero,
            "zero_mesh_dup_mods": zero_dup,
            "failed_mods": [{"name": s.name, "error": repr(e)}
                            for s, e in failed],
            "weight_partner_warnings": list(weight_warnings or []),
        }
        out = Path(output_dir) / "conversion_report.json"
        out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        return out
    except Exception:
        return None


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

def _emit_unified_coverage_patches(output, patches_dir, master_data_dirs,
                                   merged_name) -> "tuple[bool, int]":
    """Step 3b: run the winner-scan coverage passes as the PRIMARY generator and
    drop their patch ESPs + `.skypatcher.json` sidecars into the patches dir, so
    the auto-merge folds them straight into the Combined family (the merge dedups
    links by (armo, src) and collapses byte-identical ARMAs). This makes the
    separate ModBody/ModNonBody plugins unnecessary.

    Returns (ok, total_armo_targets). ok is True ONLY if BOTH coverage passes ran
    to completion (the body pass may be legitimately skipped when there are no
    converted !UBE meshes yet). The caller must use the coverage-only merge ONLY
    when ok AND total_targets > 0 -- otherwise fall back to merging the per-source
    patches, so a failed/empty winner scan can never yield an empty or partial
    Combined with the per-source coverage silently dropped. Best-effort: any
    failure is logged and reported via ok=False."""
    total_targets = 0
    try:
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
        for _cp in patches_dir.glob("UBE_Mod*Coverage UBE patch.esp*"):
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
            return (False, 0)
        excl = {"vanilla_ube_race_compat.esp",
                "ube_modbody_coverage ube patch.esp",
                "ube_modnonbody_coverage ube patch.esp"}
        excl |= _combined_output_names(merged_name, ordered)
        print("\n--- unified coverage: winner-scan patches -> merge "
              "(folding into Combined) ---")
        # Converted-mesh set FIRST: both coverage passes need it so a piece whose
        # OWN mesh was converted points at the !UBE\ mesh, not source. #mnb-converted-redirect
        ube_root = Path(output) / "meshes" / "!UBE"
        conv_rel = {n.relative_to(ube_root).as_posix().lower()
                    for n in ube_root.rglob("*.nif")} if ube_root.is_dir() else set()
        nb_out = patches_dir / "UBE_ModNonBody_Coverage UBE patch.esp"
        nb = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
            nb_out, ordered, converted_rel_paths=conv_rel,
            exclude_armo_abs=None, exclude_names=excl,
            master_data_dirs=master_data_dirs, cover_all=True,
            preserve_textures=True, emit_sidecar=True)
        total_targets += int(nb.get("armo_targets") or 0)
        print(f"  non-body: minted {nb.get('minted_armas')} | "
              f"targets {nb.get('armo_targets')}")
        if conv_rel:
            bd_out = patches_dir / "UBE_ModBody_Coverage UBE patch.esp"
            bd = ube_patcher.generate_modded_body_ube_coverage_patch(
                bd_out, ordered, converted_rel_paths=conv_rel,
                exclude_armo_abs=None, exclude_names=excl,
                master_data_dirs=master_data_dirs,
                cover_all=True, cover_hands_feet=True, preserve_textures=True,
                emit_sidecar=True)
            total_targets += int(bd.get("armo_targets") or 0)
            print(f"  body+hands/feet: minted {bd.get('minted_armas')} | "
                  f"targets {bd.get('armo_targets')} | "
                  f"src-primary HF via preserved-race mint")
        return (True, total_targets)
    except Exception as e:
        print(f"  !! unified coverage emission failed (continuing with "
              f"per-source coverage): {e!r}")
        return (False, total_targets)


def _cmd_convert(args):
    _RUN_FAILURES.clear()   # fresh failure record for this run
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
        print(f"  (path auto-discovery note: {_e!r})")

    _warn_if_skypatcher_missing()

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

    if len(sources) > 1 and args.esp_name:
        print("warning: --esp-name is ignored when converting multiple "
              "sources (each source gets its own ESP name derived from "
              "its source ESP filename).")

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
        if pool_workers is None:
            pool_workers = max(1, (os.cpu_count() or 4) - 1)
        shared_pool = _NifPool(pool_workers, args.ube_body_ref)
        print(f"  batch worker pool: {pool_workers} workers "
              f"(shared across all sources, self-healing on worker crash)")
        try:
            shared_pool.prewarm()
        except Exception as e:
            print(f"  !! pre-warm failed (non-fatal): {e!r}")

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
                            _src, include_candidate_slots=True):
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
              f"{_e!r})")
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
            _bsa_dirs = [Path(_bmr) / n for n in _bord]
            _bsa_dirs += [Path(d) for d in (_blay.game_data_dirs or [])
                          if Path(d) not in _bsa_dirs]
            _BATCH_BSA_INDEX = _BsaMeshIndex(
                _bsa_dirs, Path(output) / "_bsa_staging")
    except Exception:
        _BATCH_BSA_INDEX = None

    # Incremental floor = newest of (converter source code, UBE body ref).
    # Any code or body change invalidates every cached output. Opt-in.
    incremental_floor = None
    if getattr(args, "incremental", False):
        try:
            code_mtime = _incremental_code_mtime()
            ref_mtime = 0.0
            _ref = args.ube_body_ref or _find_ube_body_ref()
            if _ref and Path(_ref).is_file():
                ref_mtime = Path(_ref).stat().st_mtime
            incremental_floor = max(code_mtime, ref_mtime)
            print(f"  incremental mode ON (reuse outputs newer than "
                  f"source + {time.strftime('%Y-%m-%d %H:%M', time.localtime(incremental_floor))})")
        except Exception as e:
            print(f"  !! incremental floor calc failed (full convert): {e!r}")
            incremental_floor = None

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
            print(f"\n--- [{i}/{len(sources)}] converting {_disp!r} ---")
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
                    print(f"!! vanilla sweep failed ({_e1!r}) — retrying "
                          "SERIALLY (no worker pool; slower, but immune to "
                          "pool-environment failures)...")
                    claimed_dst_paths.clear()
                    claimed_dst_paths.update(_claims_before)
                    r = _convert_one(src, _pool=None, _workers=1)
                    print("  vanilla sweep serial retry SUCCEEDED")
                results.append((src, r, None))
            except Exception as e:
                results.append((src, None, e))
                print(f"!! conversion failed: {e!r}")
    finally:
        if shared_pool is not None:
            shared_pool.shutdown()
        _BATCH_BSA_INDEX = None   # release BSA archives after the batch

    print(f"\n=== batch auto-conversion done ({len(results)} mod(s)) ===")

    # Guarantee both _0 and _1 exist: a missing weight partner breaks the piece
    # at that body weight. Fill any single-weight base from its present partner.
    try:
        _filled = _complete_weight_partners(output)
        if _filled:
            print(f"  weight-partner completion: filled {_filled} missing "
                  "_0/_1 partner mesh(es) (would otherwise break at one weight)")
    except Exception as _e:
        print(f"  (weight-partner completion skipped: {_e!r})")

    # merge_blockers: hard ESP-generation failures -> block auto-merge.
    # overall_failures: merge_blockers + NIF errors + load failures -> non-zero exit.
    # overall_warnings: validator notes -> surfaced loudly, don't fail exit.
    merge_blockers = 0
    overall_failures = 0
    overall_warnings = 0
    for src, r, err in results:
        _is_sweep_src = bool(_vanilla_sweep_esps(src))
        print("\n  " + ("Vanilla sweep (base game + DLC)" if _is_sweep_src
                        else src.name))
        if err is not None:
            if _is_sweep_src:
                # A dead sweep = no vanilla coverage THIS RUN (the pre-sweep
                # state); the 100+ mod patches are complete and merging them
                # must not be held hostage. Loud + non-zero exit, not a blocker.
                print(f"    !! VANILLA SWEEP FAILED: {err!r}")
                print("       merge proceeds WITHOUT vanilla coverage — mod "
                      "armor is unaffected. Rerun just the sweep afterwards "
                      "with --only-mods vanilla (GUI: Select mods -> "
                      "'vanilla').")
                _record_failure("vanilla sweep failed",
                                "Vanilla sweep (base game + DLC)",
                                "whole source", repr(err))
            else:
                print(f"    !! FAILED: {err!r}")
                merge_blockers += 1
                _record_failure("source failed", src.name,
                                "whole source", repr(err))
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
            print(f"    !! CONVERSION ERRORS on {r.nif_errors} NIF(s):")
            for er in r.nif_error_results:
                print(f"       {er.src_path.name}: {er.reason}")
                _record_failure("mesh failed", src.name,
                                er.src_path.name, er.reason)
            overall_failures += r.nif_errors
        if r.nif_load_failures:
            print(f"    !! LOAD FAILURES on {len(r.nif_load_failures)} output NIFs")
            for p in r.nif_load_failures:
                print(f"       {p}")
                _record_failure("output mesh unreadable", src.name, p)
            overall_failures += 1
        if r.nif_invariant_warnings:
            # CTD-class, symmetric with the merged-ESP postflight: a zero-vert
            # shape is invisible and an over-cap shape left in <=1 partition
            # hard-CTDs on equip. Fail the build, don't just warn.
            print(f"    !! POSTFLIGHT NIF (CTD-class): "
                  f"{len(r.nif_invariant_warnings)} invariant issue(s) "
                  "(zero-vert / over-cap partition -> equip CTD):")
            for w in r.nif_invariant_warnings:
                print(f"       {w}")
                _record_failure("CTD-class mesh issue", src.name, w)
            overall_failures += len(r.nif_invariant_warnings)
        if r.virtualbody_rehide_failures:
            print(f"    !! VirtualBody re-hide: "
                  f"{len(r.virtualbody_rehide_failures)} NIF(s) may show a "
                  "visible body-double:")
            for w in r.virtualbody_rehide_failures:
                print(f"       {w}")
            overall_warnings += len(r.virtualbody_rehide_failures)
        # ESP generation failure: that ESP's ARMA/ARMO absent from merge (invisible).
        # Non-zero exit, but NOT a merge_blocker (one bad ESP shouldn't lose the rest).
        if r.esp_gen_failures:
            print(f"    !! ESP GENERATION FAILED for {len(r.esp_gen_failures)} "
                  f"source ESP(s) -> their armor is ABSENT from the merge "
                  f"(invisible in-game): {r.esp_gen_failures}")
            for _f in r.esp_gen_failures:
                _name, _why = (_f if isinstance(_f, (list, tuple)) and
                               len(_f) == 2 else (_f, ""))
                _record_failure("plugin patch failed", src.name, _name, _why)
            overall_failures += len(r.esp_gen_failures)
        if r.esp_skipped_no_armor:
            print(f"    {r.esp_skipped_no_armor} source ESP(s) skipped: no armor "
                  f"(landscape/quest/patch ESPs) — not a failure")
        if r.nif_partial:
            print(f"    !! PARTIAL: {r.nif_partial} NIF(s) dropped a shape "
                  f"(invisible piece in-game) — see the coverage report's "
                  f"PARTIAL section")
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
            print(f"    !! PATCH VALIDATOR: {len(validator_hits)} warning(s)")
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
            print(f"\n!! POSTFLIGHT weight-partners: {len(_wp_miss)} body mesh(es) "
                  "missing a _0/_1 partner (invisible at one body weight):")
            for _w in _wp_miss:
                print(f"     {_w}")
            overall_warnings += len(_wp_miss)
    except Exception as _wpe:
        print(f"  !! postflight weight-partner scan skipped: {_wpe!r}")

    # Postflight: flag `_0`/`_1` partners whose converted scale-bone set diverges
    # (per-file metadata leaking to one weight -> the two morph differently; e.g.
    # the #slot0-weight-partner slot-0 bug). Read-only NIF pass; disable for speed
    # with CBBE2UBE_NO_WEIGHT_PARITY_CHECK=1.
    _wp_div: "list" = []          # reused by the JSON health report below
    if (os.environ.get("CBBE2UBE_NO_WEIGHT_PARITY_CHECK", "").strip().lower()
            not in ("1", "true", "yes", "on")):
        try:
            _wp_div = _postflight_weight_partner_divergence(output)
            if _wp_div:
                print(f"\n!! POSTFLIGHT weight-partner parity: {len(_wp_div)} "
                      "shape(s) convert differently at _0 vs _1 (per-weight "
                      "metadata leak; both weights should morph identically):")
                for _d in _wp_div[:20]:
                    print(f"     {_d}")
                if len(_wp_div) > 20:
                    print(f"     ... and {len(_wp_div) - 20} more")
                overall_warnings += len(_wp_div)
        except Exception as _wpe2:
            print(f"  !! postflight weight-partner parity scan skipped: {_wpe2!r}")

    # --- Vertex-color shader-flag sanitize ---
    # Clear Vertex_Colors/Vertex_Alpha shader flags on shapes with no color buffer.
    # Our rebuild path drops source colors but inherits flags; a flag on a missing
    # buffer crashes the engine on load. Idempotent.
    meshes_out = output / "meshes"
    if meshes_out.is_dir():
        print("\n--- sanitizing vertex-color shader flags ---")
        try:
            vc = nif_convert.sanitize_output_vertex_color_flags(meshes_out)
            print(f"  scanned {vc['files']} nifs; "
                  f"fixed {vc['shapes_fixed']} shape(s) in "
                  f"{vc['files_changed']} file(s)")
        except Exception as e:
            print(f"!! vertex-color sanitize failed: {e!r}")

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
            patch_paths = sorted(patches_dir.glob("*UBE patch.esp"))
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
                    print(f"  !! female-model restore failed (continuing "
                          f"with male fallbacks): {e!r}")
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
                _cov_ok, _cov_targets = _emit_unified_coverage_patches(
                    output, patches_dir, batch_master_data_dirs,
                    args.merged_name)
                _cov_only = sorted(
                    patches_dir.glob("UBE_Mod*Coverage UBE patch.esp"))
                # Use coverage as the SOLE generator ONLY when it fully ran and
                # actually covered something; otherwise merge the per-source
                # patches so a failed/empty winner scan can't drop all coverage.
                if _cov_ok and _cov_targets > 0 and _cov_only:
                    print(f"  [unified/3c] merging {len(_cov_only)} winner-scan "
                          f"coverage patch(es) ({_cov_targets} armors) as the "
                          "SOLE generator (per-source patches left unmerged)")
                    patch_paths = _cov_only
                else:
                    print(f"  !! [unified] coverage empty/incomplete "
                          f"(ok={_cov_ok}, targets={_cov_targets}) -- merging "
                          "per-source patches instead")
                    patch_paths = sorted(patches_dir.glob("*UBE patch.esp"))
                merged_out = output / args.merged_name
                print(f"\n--- auto-merging {len(patch_paths)} patch(es) "
                      f"into {merged_out.name} ---")
                try:
                    stats = ube_patcher.merge_patches_split(
                        patch_paths, merged_out, esl_flag=True,
                        master_data_dirs=batch_master_data_dirs,
                    )
                    print(f"  merged ESP: {merged_out}")
                    print(f"  ESL flag  : {stats.get('esl_flagged')}")
                    if stats.get('split_pieces', 1) > 1:
                        print(f"  SPLIT     : {stats['split_pieces']} ESL pieces "
                              f"-> {', '.join(stats.get('pieces', []))} "
                              "(enable ALL of them)")
                    if stats.get('downgraded_to_full_esp'):
                        print(f"  !! {stats.get('own_arma_records')} new ARMAs "
                              f"exceed the {stats.get('esl_slots_max')}-record "
                              "ESL cap -> shipped as a NON-ESL full ESP "
                              "(consumes one load-order slot; position it to win)")
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
                    if _sp_lines:
                        _sp_ini_path = (output / "SKSE" / "Plugins"
                                        / "SkyPatcher" / "armor"
                                        / (merged_out.stem + ".ini"))
                        from .atomic_io import atomic_write_bytes
                        _sp_hdr = [
                            "; cbbe-to-ube FULL SKYPATCHER: adds each converted",
                            "; armor's minted UBE armature(s) at runtime to the",
                            "; LOAD-ORDER-WINNING record -- no ESP overrides.",
                        ]
                        atomic_write_bytes(_sp_ini_path, ("\n".join(
                            _sp_hdr + _sp_lines) + "\n").encode("utf-8"))
                        print(f"  FULL SKYPATCHER: {stats.get('skypatcher_targets')} "
                              f"armor record(s) covered via "
                              f"{_sp_ini_path.name} (no ESP overrides)")
                        # Vanilla-coverage assertion: crashes are caught by
                        # the sweep pass's own isolation, but a SILENT hole
                        # (sweep ran, linked nothing) would only show up as
                        # invisible armor in-game. Count links whose target
                        # record lives in a vanilla/DLC master and warn when
                        # the sweep is enabled yet none landed.
                        _van = {m.lower() for m in
                                ube_patcher.VANILLA_DLC_MASTERS}
                        _van_links = 0
                        for _l in _sp_lines:
                            if not _l.startswith("filterByArmors="):
                                continue
                            _t = _l.split("=", 1)[1].split("|", 1)[0]
                            if _t.lower() in _van:
                                _van_links += 1
                        print(f"  vanilla coverage: {_van_links} vanilla/DLC "
                              "armor record(s) linked")
                        # Precise form: mod-driven links to vanilla records
                        # (bugfix-patch overrides) would mask a dead sweep in
                        # the count above, so assert on the SWEEP SOURCE's own
                        # link contribution when one ran this batch.
                        _sweep_links = None
                        for _rsrc, _r, _rerr in results:
                            if not _vanilla_sweep_esps(_rsrc):
                                continue
                            _sweep_links = 0
                            if _rerr is None and _r is not None:
                                for _st in (_r.esp_stats_list or []):
                                    _sweep_links += int(_st.get(
                                        "skypatcher_link_targets", 0) or 0)
                        if _sweep_links == 0:
                            print("  !! the VANILLA SWEEP ran but linked 0 "
                                  "records — vanilla armor no mod overrides "
                                  "will be invisible on UBE actors. Check "
                                  "the VANILLA SWEEP pass above for errors, "
                                  "or rerun just the sweep (Select mods -> "
                                  "'vanilla').")
                    # Reconcile alt-texture 3D indices against the converted NIFs.
                    # Shape reordering during the NIF merge shifts MO2S/MO3S indices;
                    # reconcile ALL split pieces (overflow also carries alt-texture sets).
                    try:
                        nfix = ube_patcher.reconcile_alt_texture_indices_all(
                            merged_out, output / "meshes")
                        print(f"  alt-texture reconcile: fixed {nfix} ARMA(s)")
                    except Exception as e:
                        print(f"  !! alt-texture reconcile failed: {e!r}")
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
                        print(f"  !! hands-slot fix failed: {e!r}")
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
                        print(f"  !! armature dedup failed: {e!r}")
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
                        print(f"  !! master re-sort failed: {e!r}")
                    # POSTFLIGHT: re-validate the FINAL Combined (+ ESL split
                    # pieces) AFTER the merge/winner-rebase/reconcile/hands-fix
                    # mutations. validate_patch ran per-SOURCE only; a structural
                    # break those passes introduce on the loaded plugin is
                    # otherwise invisible until an in-game CTD / invisible armor.
                    try:
                        _pf = ube_patcher.postflight_validate_combined(
                            merged_out, output / "meshes",
                            master_data_dirs=batch_master_data_dirs)
                        if _pf["ctd"] or _pf["soft"]:
                            print(f"  !! POSTFLIGHT: {len(_pf['ctd'])} "
                                  f"load-breaking + {len(_pf['soft'])} other "
                                  "issue(s) on the FINAL Combined:")
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
                        print(f"  !! postflight validation skipped: {_pfe!r}")
                except Exception as e:
                    print(f"!! auto-merge failed: {e!r}")
                    _record_failure("merge failed", "Combined ESP",
                                    args.merged_name, repr(e))
                    overall_failures += 1
            else:
                print(f"\n  (no patches found in {patches_dir} — "
                      "skipping auto-merge)")
    elif args.auto_merge and merge_blockers > 0:
        print(f"\n!! auto-merge SKIPPED: {merge_blockers} mod(s) failed ESP "
              "generation. Fix those before merging — the Combined ESP "
              "would otherwise be built from incomplete patches.")
        _record_failure("merge skipped", "Combined ESP", args.merged_name,
                        f"{merge_blockers} source(s) failed ESP generation")

    if args.render_previews:
        from . import preview
        print("\n--- rendering morph previews ---")
        try:
            preview_results = preview.render_all_previews(output)
        except Exception as e:
            print(f"!! preview render failed: {e!r}")
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
            print(f"  !! {len(broken_bodytri)} NIF(s) reference a BODYTRI "
                  f"that doesn't exist on disk:")
            for r in broken_bodytri[:8]:
                print(f"       {r['nif']}  ->  {r['bodytri_string']}")
            if len(broken_bodytri) > 8:
                print(f"       ... and {len(broken_bodytri) - 8} more")
        print(f"  worst morph delta across batch: {max_delta_batch:.2f}u")
        print(f"  preview dir: {preview_dir}")

    summary_path = write_conversion_summary(output, results)
    if summary_path is not None:
        print(f"\n  coverage report: {summary_path}")
    write_conversion_report_json(output, results, weight_warnings=_wp_div)

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
# Excludes already-UBE content, body/BodySlide mods, our own output, and
# khajiit/beast-race body/fur-overlay mods (target is human female UBE body).
_NONSOURCE_NAME_HINTS = ("ube", "bodyslide output", "cbbetoube",
                         "khajiit", "ohmes", "fur morph", "fur_morph")

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
# body mod; don't convert it. Real armour pieces are never named femalebody etc.
_BODY_SKIN_BASENAMES = frozenset({
    "femalebody", "malebody", "femalehands", "malehands",
    "femalefeet", "malefeet",
    "1stpersonfemalebody", "1stpersonmalebody",
    "1stpersonfemalehands", "1stpersonmalehands",
})


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


def _complete_weight_partners(output_dir: "str | Path") -> int:
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
    Returns the number of partners filled. Meshes shipped weight-agnostic
    (``name.nif`` with no ``_0``/``_1``) don't match and are untouched."""
    import re as _re
    ube_root = Path(output_dir) / "meshes" / "!UBE"
    if not ube_root.is_dir():
        return 0
    groups: "dict[tuple, dict]" = {}
    for p in ube_root.glob("**/*.nif"):
        m = _re.match(r"(.*)_([01])\.nif$", p.name, _re.IGNORECASE)
        if m:
            groups.setdefault((str(p.parent).lower(), m.group(1).lower()),
                              {})[m.group(2)] = p
    filled = 0
    for have in groups.values():
        if "0" in have and "1" in have:
            continue
        present = have.get("1") or have.get("0")
        miss_w = "0" if "1" in have else "1"
        miss = present.parent / _re.sub(
            r"_[01]\.nif$", f"_{miss_w}.nif", present.name, flags=_re.IGNORECASE)
        if miss.exists():
            continue
        try:
            from .atomic_io import atomic_copy
            atomic_copy(present, miss)
            filled += 1
        except OSError:
            pass
    return filled


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


def _weight_partner_scale_divergence(
        shapes0, shapes1, base_label: str,
        present_min: int = 8, absent_max: int = 1) -> "list[str]":
    """Compare the scale-bone weighting of SAME-NAMED shapes across a `_0`/`_1`
    pair and report only a true PRESENCE/ABSENCE leak: a bone substantially
    present (>= `present_min` verts) in one weight and effectively ABSENT
    (<= `absent_max` verts) in the other. That gross asymmetry is the signature
    of per-file metadata (slot bits, ...) leaking to ONE weight -- the two are
    the same garment and must morph identically. Deliberately does NOT flag a
    smooth slim-vs-curvy gradient (e.g. 7 vs 50 verts): the graft reaches
    slightly different vert counts at each body weight, which is expected, not a
    bug. Pure + duck-typed so it's unit-testable without a real NIF.
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
        only0, only1 = [], []
        for bn in set(c0) | set(c1):
            n0, n1 = c0.get(bn, 0), c1.get(bn, 0)
            if n0 >= present_min and n1 <= absent_max:
                only0.append(bn)
            elif n1 >= present_min and n0 <= absent_max:
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
        try:
            n0 = pyn.NifFile(filepath=str(byw["0"]))
            n1 = pyn.NifFile(filepath=str(byw["1"]))
        except Exception:
            continue
        try:
            label = byw["1"].relative_to(meshes).as_posix()
        except Exception:
            label = f"{base}_1.nif"
        out.extend(_weight_partner_scale_divergence(
            list(n0.shapes), list(n1.shapes), label))
    return out


_BATCH_BSA_INDEX = None   # set per-batch by _cmd_convert; lazy BSA mesh resolver


class _BsaMeshIndex:
    """Load-order-wide fallback resolver: extracts armour meshes from mod BSAs
    (bespoke-armor mods, quest mods, ...) when they aren't loose anywhere. Consulted only
    after the VFS + source-local lookups miss. Lazy: BSA scan on first miss only.
    Texture/voice/sound BSAs are skipped. Archive data buffers are released after
    listing; only BSAs with needed meshes are re-opened for extraction."""

    _SKIP_BSA = ("texture", "voice", " sound", "sounds", "- snd", "facegen")

    def __init__(self, enabled_mod_dirs, staging_dir,
                 bsa_name_prefixes=None):
        self._dirs = list(enabled_mod_dirs)   # MO2 priority order (highest first)
        self._staging = Path(staging_dir)
        self._index = None                    # rel_lower -> (bsa_path, internal_name)
        self._open: dict = {}                 # bsa_path -> BSAArchive (extract cache)
        self._out: dict = {}                  # rel_lower -> (Path, rel) | None
        # Optional archive-name allowlist (lowercase prefixes). The setup-check
        # probe uses it to scan ONLY the vanilla archives: under MO2's usvfs
        # the game Data dir lists EVERY enabled mod's BSAs (330 vs 6 observed).
        self._name_prefixes = ([p.lower() for p in bsa_name_prefixes]
                               if bsa_name_prefixes else None)

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
                if any(k in bsa.name.lower() for k in self._SKIP_BSA):
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
        from .bsa_strings import BSAArchive
        bsa_path, internal = hit
        arch = self._open.get(bsa_path)
        if arch is None:
            try:
                arch = BSAArchive(bsa_path)
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
            print(f"  !! BSA extract: refusing traversal path {internal!r}",
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


def _player_armor_mesh_bases(mod_dir: Path,
                             include_candidate_slots: bool = False,
                             mesh_resolves=None) -> "set[str]":
    """Weight-agnostic rel-path keys of every mesh a DefaultRace ARMA in this mod
    points at as an armor piece (biped slot is not hair-only).

    `mesh_resolves`: optional ``(weight_base_key) -> bool`` predicate (True if that
    mesh actually exists to convert). Enables the FEMALE-ONLY policy -- with a female
    model that resolves, the male model is skipped (UBE is a female body; a female
    actor never renders the male mesh). The male model is kept only for a male-only
    piece, or when the female model is a dead path (so the female ARMA can redirect to
    the converted male). ``None`` keeps the legacy "convert every slot".

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
        _ARMO_NONPLAYABLE = 0x00000004
        playable_ref: "set[int]" = set()
        any_ref: "set[int]" = set()
        for g in e.groups:
            if g.label != b"ARMO":
                continue
            for arec in g.records:
                _play = not (arec.flags & _ARMO_NONPLAYABLE)
                for s, d in _esp.iter_subrecords(arec.payload):
                    if s == b"MODL" and len(d) == 4:
                        rf = _struct.unpack("<I", d)[0]
                        any_ref.add(rf)
                        if _play:
                            playable_ref.add(rf)
        for g in e.groups:
            if g.label != b"ARMA":
                continue
            for rec in g.records:
                # Gore/effect: this ARMA is referenced ONLY by non-playable
                # ARMO(s) in this plugin -> not player-equippable -> don't convert.
                if rec.formid in any_ref and rec.formid not in playable_ref:
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
                    if base.rsplit("/", 1)[-1] in _BODY_SKIN_BASENAMES:
                        continue  # nude body skin — not an armour piece
                    if _is_child_content_asset(m):
                        continue  # child clothing reached via an adult-named ARMA
                    bases.add(base)
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


# Full-VFS mesh index built once during source selection; reused by the convert
# step to avoid a second modlist walk. Keyed by lowercased mods_root.
_BATCH_MESH_INDEX: "dict[str, dict]" = {}

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
            os.environ.get("CBBE2UBE_NO_VANILLA_SWEEP", "") == "1")
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
        if any(h in nl for h in _NONSOURCE_NAME_HINTS):
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
        armor_bases = _player_armor_mesh_bases(mod_dir)  # STRICT = eligibility
        if not armor_bases:
            continue  # no player-equippable armour piece -> not a source
        # Broaden to ambiguous modder slots for VFS coverage on mods already
        # eligible via a standard body slot. The crash guard drops non-body-skinned.
        cov_bases = _player_armor_mesh_bases(mod_dir, include_candidate_slots=True)
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
    if os.environ.get("CBBE2UBE_NO_VANILLA_SWEEP", "") != "1":
        try:
            _swlay = paths.discover_layout()
            for _dd in (_swlay.game_data_dirs or [])[:1]:
                for b in _player_armor_mesh_bases(
                        Path(_dd), include_candidate_slots=True):
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

    for mod_dir, armor_bases in pending_vfs:
        c = sum(1 for b in armor_bases
                if any(f"{b}{suf}.nif" in vfs for suf in ("_1", "_0", "")))
        if c == 0:
            continue  # armour meshes genuinely don't exist anywhere
        candidates.append({
            "name": mod_dir.name, "path": mod_dir, "armor_nifs": c,
            "esps": sum(1 for _ in mod_dir.rglob("*.esp"))})

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
    exclude = {output.name}
    for _bf in (nif_convert._find_cbbe_base_body("_1"),
                nif_convert._find_ube_femalebody("_1"),
                _find_ube_body_ref()):
        try:
            if _bf is not None:
                exclude.add(Path(_bf).resolve().relative_to(
                    mr.resolve()).parts[0])
        except Exception:
            pass
    cands = _find_armor_mod_dirs(
        mr, extra_exclude_names=exclude, enabled_names=enabled,
        require_arma=True, enabled_ordered=paths.enabled_mods_ordered(lay),
        index_skip_mods={output.name}, progress=progress)
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
    if (os.environ.get("CBBE2UBE_NO_VANILLA_SWEEP", "") != "1"
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
    """Up to `limit` of a mod's own body/armor NIFs, the body mesh first (it hugs
    the reference body most tightly). Head/face/1st-person meshes are dropped."""
    picks: list = []
    try:
        for nif in mod_dir.rglob("*.nif"):
            rel = str(nif.relative_to(mod_dir)).lower().replace("/", "\\")
            if "meshes\\" not in rel:
                continue
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
        best = None            # (winner_dist, verdict, dUBE, dCBBE)
        mod_dir = (mr / name) if mr is not None else None
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
            verdict, conf, signals = "unknown", "low", ["no readable body mesh"]
        else:
            w, du, dc = best
            # A verdict requires a DECISIVE hug: some shape sits right on one body
            # (near dist < the hug threshold) AND that body is clearly the closer
            # one. Bulky/off-body meshes drift toward the larger UBE body, so an
            # inconclusive fit stays "unknown" -> a CBBE mod is never mislabelled.
            lo, hi = min(du, dc), max(du, dc)
            if lo < _UBE_HUG_DIST and hi >= lo * _FIT_RATIO:
                verdict, conf = ("ube" if du < dc else "cbbe"), "high"
            else:
                verdict, conf = "unknown", "low"
            signals = [f"shape fit: dUBE={du:.2f}, dCBBE={dc:.2f}"]
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

    # Exclude body mods (CBBE + UBE): 3BA-rigged, pass the content filter, but
    # ARE the body, not armour. Derive names from discovered NIFs — no hardcoding.
    exclude = {output.name}
    for _bf in (nif_convert._find_cbbe_base_body("_1"),
                nif_convert._find_ube_femalebody("_1"),
                _find_ube_body_ref()):
        try:
            if _bf is not None:
                exclude.add(Path(_bf).resolve().relative_to(
                    mr.resolve()).parts[0])
        except Exception:
            pass
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
        candidates = [c for c in candidates if c["name"].lower() in wanted]
        missing = sorted(wanted - {c["name"].lower() for c in candidates})
        print(f"  --only-mods: {len(candidates)}/{before} mod(s) selected"
              + (f"; NOT FOUND: {missing}" if missing else ""))
        if not candidates and not _sweep_only_requested:
            print("error: --only-mods matched no discovered armor mods. Run "
                  "`scan` or the GUI 'Refresh mod list' for the exact names.")
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
    if (os.environ.get("CBBE2UBE_NO_VANILLA_SWEEP", "") != "1"
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
            print(f"  !! vanilla sweep DISABLED this run: {_sw_why}")
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
    )
    rc = _cmd_convert(conv)
    # The post-merge coverage phases below generate the REQUIRED race-compat /
    # mod-coverage ESPs, but they run AFTER rc was fixed by the convert above.
    # Track their failures so an exception there still fails the run instead of
    # silently exiting 0 with armor that's invisible on UBE races.
    post_merge_failures = 0

    # Vanilla race coverage (Vanilla_UBE_Race_Compat.esp) REMOVED 2026-07-03:
    # RaceCompatibility SKSE / RaceDispatcher does this race + nude-skin dispatch
    # at runtime, so the static patch was redundant (and could conflict with the
    # dispatcher). RaceCompatibility the MOD is still a UBE prereq. The patch and
    # its generators have been removed.

    # Mod-defined non-body coverage: overhauls re-armature vanilla helmets/jewelry
    # with their own ARMAs listing only vanilla races -> invisible on UBE actors.
    # Runtime race dispatch only covers vanilla ARMAs, not these mod-defined ones.
    # Mints a UBE-primary ARMA per item + a SkyPatcher INI that adds it at runtime.
    # FULL SKYPATCHER: ARMOs the Combined INI already links must be EXCLUDED
    # from both coverage passes (ESP armature lists no longer reflect runtime
    # coverage; re-covering doubles the armature -> body renders twice /
    # UBE-primary hands mints -> invisible gauntlets). #fsp-dedup
    _fsp_linked_abs: "set[tuple[str, int]]" = set()
    try:
        _mstem = Path(getattr(args, "merged_name",
                              "CBBE_to_UBE_Combined.esp")).stem
        _comb_ini = (output / "SKSE" / "Plugins" / "SkyPatcher" / "armor"
                     / (_mstem + ".ini"))
        if _comb_ini.is_file():
            for _l in _comb_ini.read_text(encoding="utf-8").splitlines():
                if not _l.startswith("filterByArmors="):
                    continue
                # Parse each line in ISOLATION: one malformed line must not wipe
                # the whole exclude set. An empty set re-enables double-coverage
                # mod-wide (double body render / UBE-primary hands -> invisible
                # gauntlets), so a single bad line is skipped, not fatal. #fsp-dedup
                try:
                    _t = _l.split("=", 1)[1].split(":", 1)[0]
                    _pl, _lo = _t.rsplit("|", 1)
                    _fsp_linked_abs.add((_pl.lower(), int(_lo, 16)))
                except Exception:
                    continue
    except Exception:
        _fsp_linked_abs = set()

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
            print(f"  !! overlay transfer skipped: {e!r}")

    # Pre-flight: missing hands/feet .tri makes them stay CBBE-shaped while the
    # body morphs UBE (built without 'Build Morphs'). Surface the warning loudly.
    try:
        morph_warns = nif_convert.check_ube_nude_morph_files()
        if morph_warns:
            print("\n  !! UBE nude-skin morph check:")
            for w in morph_warns:
                print(f"     - {w}")
    except Exception:
        pass

    _enable = f"'{output.name}' + its Combined ESP(s)"
    if post_merge_failures:
        print(f"\n  !! {post_merge_failures} post-merge coverage phase(s) FAILED "
              "-- some armor may be invisible on UBE races (see errors above).")
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
    # Self-heal a stale/mis-sorted master list before validating (no-op if clean).
    try:
        _nrs = ube_patcher.resort_masters_all(
            Path(stats.get('output', args.output)), master_data_dirs=_mdd)
        if _nrs:
            print(f"  master re-sort: repaired {_nrs} mis-ordered piece(s)")
    except Exception as _e:
        print(f"  !! master re-sort failed: {_e!r}")
    # Postflight the FINAL merged output (+ any ESL split pieces) -- this is the
    # exact artifact the validator exists to guard (master-ordering / ESL-overflow
    # / malformed-MODT CTD class). The integrated auto/convert path already does
    # this; the standalone `merge` subcommand must not skip it.
    try:
        _pf = ube_patcher.postflight_validate_combined(
            Path(stats.get('output', args.output)), master_data_dirs=_mdd)
        if _pf["ctd"]:
            print(f"  !! POSTFLIGHT CTD on merged output: {len(_pf['ctd'])} "
                  "load-breaking issue(s) -- NOT safe to load:")
            for _piece, _w in _pf["ctd"]:
                print(f"       {_piece}: {_w}")
            return 2
        if _pf["soft"]:
            print(f"  postflight: {len(_pf['soft'])} soft warning(s) "
                  "(invisible/cosmetic, non-fatal)")
    except Exception as _pfe:
        print(f"  !! postflight validation skipped: {_pfe!r}")
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

    total_warnings = 0
    failing = 0
    for esp_path in esps:
        warnings = ube_patcher.validate_patch(
            esp_path, meshes_root=meshes_root, check_nifs=check_nifs)
        if warnings:
            failing += 1
            total_warnings += len(warnings)
            print(f"\n  !! {esp_path.name}")
            for w in warnings:
                print(f"     {w}")
        else:
            print(f"  [OK] {esp_path.name}")
    print("\n=== validation summary ===")
    print(f"  ESPs checked     : {len(esps)}")
    print(f"  ESPs with issues : {failing}")
    print(f"  total warnings   : {total_warnings}")
    return 0 if failing == 0 else 1


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "scan":
        return _cmd_scan(args)
    if args.cmd == "discover-body-ref":
        return _cmd_discover_body_ref(args)
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
