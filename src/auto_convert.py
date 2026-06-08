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
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import ube_patcher, nif_convert, paths, discovery, esp


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
        # Distinct "error" status — NOT "skipped". A skipped NIF is a
        # benign no-op (e.g. no body shape to swap); a conversion that
        # raised is a real failure that must be counted as such, not
        # hidden in the skip bucket where the batch would report success.
        return nif_convert.ConvertResult(
            src_path=src, dst_path=None,
            status="error",
            reason=f"error: {type(e).__name__}: {e}",
        )


def _warmup_worker(barrier, ube_body_ref_path: "str | None") -> "tuple[int, float]":
    """Eagerly load pynifly + UBE refs in this worker so the first
    real NIF doesn't pay the cold-start cost. Called once per worker
    via `_prewarm_pool` before real work begins.

    The barrier forces 1-task-per-worker distribution: no task can
    return until all N have claimed a slot, so the pool dispatcher
    must hand one to each distinct worker process. Otherwise a fast
    worker might grab two warm-up tasks, leaving another worker cold.

    Returns (os.getpid(), elapsed_seconds).
    """
    import os
    from time import perf_counter
    t0 = perf_counter()
    # Touch each per-worker cache so the first real NIF in the first
    # mod doesn't trigger any of these multi-second loads itself.
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
    barrier.wait()  # block until every worker has claimed an init task
    return (os.getpid(), elapsed)


def _prewarm_pool(
        pool: "ProcessPoolExecutor",
        num_workers: int,
        ube_body_ref_path: "str | Path | None",
) -> None:
    """Submit one warm-up task per worker and wait for all to complete.

    Without this, first-mod throughput is dominated by serial cold-
    start cost as workers ramp up (each one has to import pynifly,
    load the DLL, parse the 11 MB body OSD, load the body ref NIF).
    Pre-warming runs these loads in parallel across all workers
    before any real conversion work hits the queue.
    """
    import multiprocessing
    if num_workers <= 0:
        return
    print(f"  pre-warming {num_workers} workers...")
    t0 = time.perf_counter()
    # Manager-backed Barrier survives the spawn-mode pickle boundary
    # and is shared across all worker processes. Manager itself runs
    # in a separate process and is torn down at the end of the warm-up.
    manager = multiprocessing.Manager()
    try:
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
    # Highest priority: the user's BodySlide-OUTPUT UBE body (preset
    # already baked into BaseShape). When this exists, injecting it
    # makes the armor body underneath match the user's UBE-preset
    # nude shape directly — no double-morph risk because we no longer
    # rely on runtime BodyMorph to apply the preset to BaseShape.
    # Found by scanning any mod for the !UBE\Body tangent output — never
    # by a fixed mod name.
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
    # Lazy import to keep auto_convert importable without pynifly when
    # body-swap isn't needed.
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
    # Primary source/output ESP — backward compat: first ESP from the
    # discovered set. New code should use source_esps / output_esps.
    source_esp: Path | None = None
    output_esp: Path | None = None
    esp_stats: dict = field(default_factory=dict)
    # ALL discovered source ESPs + their corresponding output patches.
    # Same length, same order. Empty list if no ESPs were found.
    source_esps: list[Path] = field(default_factory=list)
    output_esps: list[Path] = field(default_factory=list)
    esp_stats_list: list[dict] = field(default_factory=list)
    nif_results: list[nif_convert.ConvertResult] = field(default_factory=list)
    textures_copied: int = 0
    notes: list[str] = field(default_factory=list)
    nif_load_failures: list[Path] = field(default_factory=list)
    # How many of this mod's armour meshes the VFS resolved from a DIFFERENT
    # mod (BodySlide output / replacer / patch) — i.e. meshes the old
    # source-folder-only walk would have missed. Surfaced in the coverage
    # report so the user can see the broadening actually firing.
    vfs_other_mod_count: int = 0

    @property
    def nif_converted(self) -> int:
        return sum(1 for r in self.nif_results if r.status.startswith("converted"))

    @property
    def nif_skipped(self) -> int:
        return sum(1 for r in self.nif_results if r.status.startswith("skipped"))

    @property
    def nif_errors(self) -> int:
        """NIFs whose conversion raised an exception (status == 'error').
        These are real failures, distinct from benign skips."""
        return sum(1 for r in self.nif_results if r.status == "error")

    @property
    def nif_error_results(self) -> "list[nif_convert.ConvertResult]":
        return [r for r in self.nif_results if r.status == "error"]

    @property
    def nif_copy_count(self) -> int:
        return sum(1 for r in self.nif_results if r.status == "converted (copy)")

    @property
    def nif_swap_count(self) -> int:
        return sum(1 for r in self.nif_results if r.status == "converted (body-swap)")

    def write_report(self, path: Path) -> None:
        lines = [
            f"CBBE-to-UBE auto-conversion report",
            f"source : {self.source_dir}",
            f"output : {self.output_dir}",
            f"",
            f"ESP ({len(self.source_esps)} patched)",
        ]
        # Use the per-ESP list if it's populated (multi-ESP path); fall
        # back to legacy single-ESP fields otherwise.
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

        # Surface validation warnings from successful conversions —
        # captured in ConvertResult.reason by validate_dst_nif.
        # These don't fail the conversion but signal subtle issues
        # (zero-weight verts, >4 bone influences, stale TRI entries,
        # etc.) the user should know about.
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
    """Auto-discover directories that may contain master ESM files
    (Skyrim.esm, Dawnguard.esm, Update.esm, Dragonborn.esm, CC masters)
    AND mod folders that may contribute UBE-targeted RACE records
    (KhajiitUBE.esp, custom race patches, etc.).

    Heuristic for MO2 layouts:
      <modlist>/Stock Game/Data/        — base game install bundled with modlist
      <modlist>/Game Root/Data/          — Wabbajack default
      <modlist>/mods/<each mod>/         — mod overlays (some override masters,
                                            some define UBE race add-ons)
    Walks two parent levels up from `source_dir` (typically `mods/<modname>/`)
    looking for sibling `Stock Game/Data` or `Game Root/Data`, AND adds every
    sibling mod folder so UBE race discovery picks up Khajiit/Argonian/custom
    UBE race plugins.

    Returns directories that EXIST, in priority order. Empty list if
    nothing was found.
    """
    candidates: list[Path] = []
    # source_dir is typically <modlist>\mods\<modname>\
    # so source_dir.parent = mods\, source_dir.parent.parent = <modlist>\
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
    # Add every sibling mod folder (so UBE race discovery sees KhajiitUBE.esp
    # etc. that live as standalone mod folders, not in Stock Game/Data).
    # _scan_master_armos_referencing in ube_patcher.py only opens files
    # whose name matches an existing source master, so adding extra dirs
    # here doesn't slow down master ARMO scanning. _discover_ube_races
    # walks every .esp/.esm but only on dirs we pass — bounded by the
    # modlist size, typically <200 dirs.
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

    Heuristic: every .esp file not in a backup/UBE/extras subfolder.
    Returns the full list sorted by (depth, name) — shallowest first.

    Why all of them: some mods ship multiple ESPs that each contribute
    different ARMA/ARMO records. a multi-ESP vanilla-replacer mod is the
    canonical example: ships a main replacer ESP
    (70 ARMOs + 258 ARMAs covering vanilla cuirasses/gloves/boots) AND
    a separate AE-content ESP (8 records for Creation
    Club add-ons). If we patch only one, the user equips a vanilla
    cuirass and sees no armor on a UBE character — the un-patched ARMO
    has no UBE ARMA in its armature list, so no race-matching mesh is
    found.
    """
    # Path components that mean a matched "*.esp" is NOT a real plugin: facegen
    # is laid out as meshes\...\facegendata\facegeom\<SourcePlugin.esp>\ — a
    # DIRECTORY named after the source plugin, so rglob("*.esp") matches it and
    # then loading it as a file raises (PermissionError on the dir). Real source
    # ESPs live at the mod root, never under meshes\/textures\.
    _NON_PLUGIN_PARTS = {"meshes", "textures", "facegendata", "facegeom",
                         "facetint"}
    # Bespoke-armour CONTENT mods ship as .esm/.esl (Vigilant.esm, Legacy of the
    # Dragonborn.esm, Unslaad.esm, Glenmoril.esm, ...). Globbing only *.esp
    # silently skipped them -> their armour was never converted, no UBE armature
    # -> invisible on UBE actors (#179). So scan all three extensions. Vanilla/DLC
    # master ESMs + Creation Club content are NOT convert sources (the
    # vanilla-compat path handles vanilla/DLC); exclude them by FILENAME so they
    # are never picked up even if a "Stock Game"-style folder is scanned. The
    # existing "ube" path check already excludes our own outputs + UBE_AllRace.
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
            if (name_lower.startswith("cc")
                    and name_lower.endswith((".esm", ".esl"))):
                continue  # Creation Club content -> out of scope
            parts_lower = [s.lower() for s in p.parts]
            if any("backup" in s or "ube" in s for s in parts_lower):
                continue
            if any(s in _NON_PLUGIN_PARTS for s in parts_lower):
                continue  # a plugin buried under meshes\/textures\ isn't a plugin
            candidates.append(p)
    candidates.sort(key=lambda p: (len(p.parts), p.name.lower()))
    return candidates


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
    nif_pool: "ProcessPoolExecutor | None" = None,
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

    # Auto-discover UBE body ref if not provided. Saves the user from
    # manually finding a NIF with both BaseShape + VirtualBody.
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
    # Patch EVERY ESP in the source mod, not just the first one. Some
    # mods (notably a multi-ESP vanilla-replacer mod) ship multiple ESPs
    # that each define disjoint ARMA/ARMO sets — missing one means an
    # entire category of armors silently fails to appear on UBE
    # characters when equipped. See `_find_source_esps` docstring.
    # Auto-discover master ESM directories if not explicitly provided.
    # Replacer mods commonly override ARMA records whose ARMOs live in
    # Skyrim.esm; we need to walk the master to create ARMO overrides.
    if master_data_dirs is None:
        master_data_dirs = _discover_master_data_dirs(source_dir)
        if master_data_dirs:
            result.notes.append(
                f"master ESM scan dirs: {[str(d) for d in master_data_dirs]}")
    # Resolve meshes + the planned set of NIFs we WILL convert (armour-
    # piece models only — see _player_armor_mesh_bases). Computed ONCE here,
    # UNCONDITIONALLY (independent of whether the source ships an ESP),
    # because two later sections both consume it: the ESP patcher needs
    # converted_rel_paths/body_mesh_rel_paths to decide ARMA redirects (a
    # redirect to a mesh we never produced => ARMA points at a missing NIF
    # => game CRASHES on load), and the NIF section reuses resolved_pairs.
    # resolved_pairs may include meshes resolved from OTHER mods via the VFS
    # index, so it is valid even for an ESP-less / meshes-less source folder.
    meshes_root = _find_meshes_root(source_dir)
    # include_candidate_slots: also resolve lower-body cloth on ambiguous modder
    # slots (44/47/... — e.g. DDV Ruby Flower's pants/skirt). The crash guard
    # below drops any that aren't actually body-skinned. #165.
    armor_bases = _player_armor_mesh_bases(source_dir, include_candidate_slots=True)
    all_nif_paths = (sorted(meshes_root.rglob("*.nif"))
                     if meshes_root is not None else [])
    # Resolve each armour mesh the source ESP references through the FULL MO2
    # VFS (all enabled mods in priority order) so BodySlide-build / replacer /
    # patch meshes that live OUTSIDE this mod's own folder are found and
    # converted instead of silently missed. Falls back to source-local files
    # when no VFS index was supplied. resolved_pairs: [(source_file, rel)],
    # rel = meshes-relative path (original case, forward-slash).
    resolved_pairs = _resolve_armor_meshes(
        armor_bases, mesh_vfs_index, meshes_root, all_nif_paths)
    # Crash guard for the ambiguous modder slots admitted above (44/47/...):
    # KEEP a resolved mesh iff it's on a STANDARD body slot OR its NIF is
    # skinned to body-fit bones. This converts real lower-body cloth (DDV Ruby
    # Flower's pants=44 / skirt=47 — skinned to thighs/butt/belly) while
    # dropping non-body accessories that share those slots (a beard=44, a
    # backpack=47) — giving such an unskinned piece a UBE body race CTDs at
    # actor setup. Runs the (cheap) skin check ONLY for non-standard-slot
    # meshes, so standard armour is never loaded here. Must precede
    # converted_rel_paths so the patcher never UBE-tags a dropped mesh. #165.
    if resolved_pairs:
        try:
            _slot_map = ube_patcher.build_nif_slot_map(
                _find_source_esps(source_dir))
        except Exception:
            _slot_map = {}
        _kept_pairs = []
        _guard_dropped = 0
        # Weight-agnostic slot lookup. The ESP's ARMA names ONE weight (usually
        # `_1`; the engine derives `_0`), so the `_0` sibling's exact rel is NOT a
        # slot-map key. Without folding weights, `_0` reads slot 0, fails the
        # body-slot test, and -- for a non-body-fit piece like a gauntlet/glove/
        # cloak -- gets DROPPED, leaving only `_1` on disk => the piece is
        # INVISIBLE in game (Skyrim needs BOTH `_0` and `_1`). Fold both weights
        # to one base key so `_0` and `_1` are kept/dropped identically. [2026-06-01]
        _agnostic_slot: "dict[str, int]" = {}
        for _k, _v in _slot_map.items():
            _bk = _weight_base_key(_k)
            _agnostic_slot[_bk] = _agnostic_slot.get(_bk, 0) | _v
        _nonstd_kept: list[str] = []   # cape/cloak on a NON-standard slot (#177)
        for _gsrc, _grel in resolved_pairs:
            _gslot = (_slot_map.get(_grel.lower(), 0)
                      or _agnostic_slot.get(_weight_base_key(_grel), 0))
            if (_gslot & _BODY_SLOT_BITS) != 0 or _nif_has_bodyfit_skin(_gsrc):
                _kept_pairs.append((_gsrc, _grel))
                # A draping mesh kept on a slot that's in NEITHER the body nor
                # candidate allowlist (e.g. a hood-cape on hair slots 31/41/43,
                # admitted by the cloak rule + kept by its body-fit skin). Surface
                # it so this conversion is VISIBLE in the report and can't
                # silently regress the way the Traveling Mage cape did. #177
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
    # Count how many resolved from a DIFFERENT mod than this source (the
    # armour the source-local walk used to miss) — surfaced as a coverage note.
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
    # Normalized (meshes-relative, lowercase /) paths of the NIFs that WILL
    # exist at !UBE\<path>. The patcher only redirects an ARMA model here if
    # its path is in this set.
    #
    # Heeled boots/shoes carry the heel as a NiFloatExtraData "HH_OFFSET" that
    # pynifly drops on load. convert_nif now CONVERTS the boot (UBE-shaped) and
    # transplants the heel block back at the binary level (hh_offset module) —
    # so a heeled boot uses the !UBE mesh (include it here), gaining UBE morph
    # while keeping the heel. EXCEPTION: a heeled NIF whose binary layout our
    # parser can't round-trip can't be transplanted safely; convert_nif then
    # skips it (ESP-only original mesh), so we must EXCLUDE it here too.
    from src import hh_offset
    converted_rel_paths = set()
    _heeled_esp_only = 0
    for _abs, _rel in resolved_pairs:
        _heeled = False
        try:
            with open(_abs, "rb") as _fh:
                _heeled = b"HH_OFFSET" in _fh.read(262144)
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
    # body_mesh_rel_paths: ALL meshes the SOURCE mod ITSELF ships (relative,
    # lowercased /). Feeds the master-ESM mesh-path body-coverage scan in the
    # patcher (loose-mesh replacers like HDT-SMP Vanilla whose source ESP has
    # no records for the vanilla body armors it replaces) [#131]. Source-local
    # on purpose: it describes what THIS mod replaces, not the whole VFS.
    body_mesh_rel_paths: set[str] = set()
    if meshes_root is not None:
        for _nif in all_nif_paths:
            body_mesh_rel_paths.add(
                _nif.relative_to(meshes_root).as_posix().lower())

    # bsa_mesh_rel_paths: meshes available via load-order BSAs (Vigilant.bsa,
    # LegacyoftheDragonborn, Unslaad, Glenmoril, ...). Used ONLY by the non-body
    # accessory passthrough gate (_orig_mesh_on_disk) so BSA-packed accessories
    # (e.g. Vigilant cloaks) are recognised as "shipped" and get UBE coverage
    # (#4 invisible cloaks). NOT fed to the body-coverage scan -- a raw (un-
    # converted) BSA body mesh on a UBE torso would be wrong-shaped. The
    # passthrough KEEPS the original mesh path (no !UBE redirect), so the BSA
    # mesh loads fine -> crash-safe. Pass the index dict directly (membership
    # tests its keys) to avoid copying tens of thousands of paths per mod.
    bsa_mesh_rel_paths = None
    if _BATCH_BSA_INDEX is not None:
        try:
            if _BATCH_BSA_INDEX._index is None:
                _BATCH_BSA_INDEX._scan()
            bsa_mesh_rel_paths = _BATCH_BSA_INDEX._index
        except Exception:
            bsa_mesh_rel_paths = None

    src_esps = _find_source_esps(source_dir)
    if not src_esps:
        result.notes.append("no source ESP found — skipping ESP generation")
    else:
        result.source_esps = src_esps
        # Backward compat: also fill primary fields with first ESP.
        result.source_esp = src_esps[0]
        # Resolve target directory for individual (unmerged) patches.
        # Default routes them into a subfolder so MO2's plugin scanner
        # ignores them — only the merged Combined ESP at the mod root
        # gets auto-detected as a plugin.
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
                # Backward compat: primary fields = first successful patch
                if result.output_esp is None:
                    result.output_esp = out_path
                    result.esp_stats = stats
                # Surface structural warnings — these catch malformed
                # output (MODL-after-DATA, broken master order, etc.)
                # before the user gets to test in-game.
                for w in stats.get("validation_warnings", []) or []:
                    result.notes.append(
                        f"!! patch validator ({src_esp.name}): {w}")
            except Exception as e:
                result.notes.append(
                    f"ESP generation failed for {src_esp.name}: {e}")

    # --- NIFs --- (resolved_pairs computed once above; it may be VFS-resolved
    # from OTHER mods, so this no longer requires a source-local meshes/ dir)
    # Output NIFs THIS call plans to write (work-item dst paths). Drives the
    # post-conversion load check so it re-reads only this run's outputs instead
    # of the whole (batch-accumulated) output tree — O(this mod) not
    # O(sources x total). Empty when there's nothing to convert.
    planned_output_nifs: "set[Path]" = set()
    if not resolved_pairs:
        result.notes.append("no convertible armour meshes resolved")
    else:
        # Pre-scan source ESPs' ARMA records to discover which NIFs live
        # in slot 49 (skirts / loincloths / hip cloth). The converter
        # bumps inflation magnitude for those so they don't clip into
        # the body under bigger UBE morphs.
        # ARMA model paths are typically !UBE\-prefixed by the time the
        # patcher rewrites them — we scan the SOURCE ESPs here (before
        # rewriting) so paths line up with `rel` below. We also strip
        # the prefix defensively in the lookup.
        try:
            nif_slot_map = ube_patcher.build_nif_slot_map(src_esps)
        except Exception:
            nif_slot_map = {}

        # resolved_pairs (armour-piece models only) was computed above so the
        # ESP patcher could gate !UBE\ redirects on it. Report coverage.
        if armor_bases:
            print(f"  armour filter: converting {len(resolved_pairs)} ARMA "
                  f"model NIF(s) ({_other_mod} resolved from other mods); "
                  f"source mod ships {len(all_nif_paths)} mesh(es) total")
        nif_dst_root = output_dir / "meshes" / ube_path_prefix

        # Shape names targeted by an ESP alt-texture set (color variants).
        # The NIF converter protects these from the morph-cap merge so the
        # variant's TXST (applied to a shape BY NAME) still lands. Collected
        # once per mod from all its source ESPs; general, no per-armor logic.
        try:
            _alt_src_esps = _find_source_esps(source_dir)
            alt_tex_shape_names = ube_patcher.collect_alt_texture_shape_names(
                _alt_src_esps) if _alt_src_esps else set()
        except Exception:
            alt_tex_shape_names = set()
        if alt_tex_shape_names:
            print(f"  protecting {len(alt_tex_shape_names)} alt-texture-target "
                  f"shape(s) from merge (color variants)")

        # Build work items. ESP ARMA paths are stored relative to
        # Data\meshes\, in the source mod's pre-rewrite form (e.g.
        # "armor\iron\f\cuirass_1.nif"). We match on the lowercased
        # forward-slash form of `rel` from the source.
        work_items: list[tuple] = []
        skipped_collisions: list[tuple[Path, Path]] = []
        skipped_incremental = 0
        for src, rel in resolved_pairs:
            rel_key = rel.lower()
            slot_bits = nif_slot_map.get(rel_key, 0)
            dst = nif_dst_root / Path(rel)
            # Cross-mod collision check: if an earlier source mod already
            # claimed this output path, skip — first-writer wins.
            if claimed_dst_paths is not None:
                key = dst.resolve()
                if key in claimed_dst_paths:
                    skipped_collisions.append((src, dst))
                    continue
                claimed_dst_paths.add(key)
            # Incremental: reuse an up-to-date converted NIF instead of
            # re-running the (~3s) refit. Safe because the floor folds in the
            # converter-code + body-ref mtime, so any logic/body change forces
            # a full re-convert. The existing dst still feeds the ESP step.
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

        # Record this run's intended output paths for the post-conversion load
        # check below. Sourced from work_items (not result.nif_results) so it
        # also covers short-circuit paths that copy a file but leave
        # dst_path=None in their result record.
        planned_output_nifs = {it[1] for it in work_items}

        # Decide worker count. Default: cpu_count - 1 (leave one for
        # the main process + filesystem I/O). User can override.
        if nif_workers is None:
            nif_workers = max(1, (os.cpu_count() or 4) - 1)
        # Cap workers to the actual NIF count — no point spinning up
        # more workers than there are jobs.
        nif_workers = max(1, min(nif_workers, len(work_items)))

        t_start = time.perf_counter()
        if nif_workers == 1 or len(work_items) <= 1:
            # Serial path. Keeps the call-graph simple for small jobs
            # and avoids ProcessPool startup overhead.
            for item in work_items:
                r = _nif_convert_worker(item)
                result.nif_results.append(r)
        else:
            # Parallel path. Each worker is a fresh Python subprocess
            # that lazily loads pynifly + UBE body refs on first call
            # and reuses them for subsequent NIFs that worker handles.
            print(f"  NIF conversion: {len(work_items)} files across "
                  f"{nif_workers} workers...")
            done = 0
            last_print = t_start

            def _drain(p):
                nonlocal done, last_print
                futures = [p.submit(_nif_convert_worker, item)
                           for item in work_items]
                for fut in as_completed(futures):
                    r = fut.result()
                    result.nif_results.append(r)
                    done += 1
                    now = time.perf_counter()
                    if now - last_print >= 5.0 or done == len(work_items):
                        rate = done / max(now - t_start, 1e-9)
                        eta = (len(work_items) - done) / max(rate, 1e-9)
                        print(f"    [{done}/{len(work_items)}] "
                              f"{rate:.1f} NIF/s  ETA {eta:.0f}s")
                        last_print = now

            if nif_pool is not None:
                # Caller-managed pool: workers stay warm across mods,
                # caches (pynifly DLL, UBE body ref, OSD, delta map)
                # persist between calls.
                _drain(nif_pool)
            else:
                # Solo invocation: spawn a temporary pool.
                with ProcessPoolExecutor(max_workers=nif_workers) as pool:
                    _drain(pool)
        elapsed = time.perf_counter() - t_start
        if len(work_items) > 0:
            rate = len(work_items) / max(elapsed, 1e-9)
            result.notes.append(
                f"NIF conversion: {len(work_items)} files in "
                f"{elapsed:.1f}s ({rate:.1f}/s) with {nif_workers} worker(s)")

    # --- textures ---
    if copy_textures:
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
                # Skip-if-current: only copy when source size differs
                # from dst, OR source mtime is newer than dst mtime.
                # Color data is preserved because ANY content change at
                # the source will also change file size or mtime (DDS
                # writers always restamp the file). Iso-byte identical
                # files with matching mtimes are guaranteed not to need
                # re-copy. This cuts ~1-2 min off batches where the
                # texture trees are mostly unchanged between runs.
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
                shutil.copy2(f, out)
                count += 1
            result.textures_copied = count
            if skipped_current:
                result.notes.append(
                    f"textures: {count} copied, "
                    f"{skipped_current} skipped (already current)")

    # NOTE: slot-49 cloth's morph response comes from
    # `add_scale_bone_weights` in nif_convert.py (bone-driven scaling
    # via 3BA scale bones — Belly/Breast/Butt/Thigh). Earlier attempts
    # to promote slot-49 ARMAs to slot 32 (with/without body injection)
    # broke worse than they fixed; that experiment is gone.

    # --- post-conversion load check + universal VirtualBody hide ---
    # Re-load every output NIF via pynifly to catch any that we wrote
    # but the loader rejects (a dangling-block-ref kind of bug), AND
    # to apply the VirtualBody Hidden flag as a guaranteed
    # final-state property of the output mod.
    #
    # We iterate THIS run's planned output paths (`planned_output_nifs`, the
    # work-item dsts) rather than walking the whole output tree:
    #   * It still covers short-circuit convert paths (skipped, error,
    #     unsupported NIF subversion) that copy a file via shutil.copy2 but
    #     leave dst_path=None in their result record — work_items carries the
    #     intended dst regardless of how the per-NIF path resolved.
    #   * It does NOT re-read NIFs left by EARLIER mods in the same batch run.
    #     Walking the (batch-accumulated) directory re-loaded every prior mod's
    #     output once per source — O(sources x total) — for no benefit: each
    #     mod's NIFs already get the Hidden bit + load check in their OWN
    #     auto_convert_mod call. Scoping makes it O(this mod's outputs).
    #
    # Cheap: most NIFs don't even have a VirtualBody shape, so
    # _hide_virtual_body is a no-op and we don't re-save them.
    meshes_out = output_dir / "meshes"
    if meshes_out.is_dir() and planned_output_nifs:
        try:
            pn = str(Path(__file__).resolve().parent.parent / ".pynifly")
            if pn not in sys.path:
                sys.path.insert(0, pn)
            from pyn import pynifly  # type: ignore
            from . import nif_convert as _nc  # for _hide_virtual_body

            for dst in planned_output_nifs:
                # A planned dst with no file on disk means the conversion
                # produced nothing (already recorded as a convert failure) —
                # not a load failure, so skip rather than mis-flag it.
                if not dst.is_file():
                    continue
                try:
                    nf_check = pynifly.NifFile(filepath=str(dst))
                except Exception:
                    result.nif_load_failures.append(dst)
                    continue
                try:
                    if _nc._hide_virtual_body(nf_check):
                        nf_check.filepath = str(dst)
                        nf_check.save()
                except Exception:
                    pass  # best-effort
        except ImportError:
            pass

    # --- report ---
    # When batching multiple mods into the same output dir, suffix the
    # report with the source mod's name to avoid clobber. Single-source
    # uses the canonical `conversion_report.txt` for backwards compat.
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
    convert.add_argument("--no-winner-rebase", action="store_true",
                         help="Disable the #132 load-order winner rebase. By "
                              "default each merged ARMO adopts the load-order "
                              "WINNER's balance (Requiem armor rating/keywords/"
                              "name) instead of the bare master's; stats-only, "
                              "adds no masters.")
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

    vc = sub.add_parser(
        "vanilla-compat",
        help="emit an ESL patch extending vanilla non-body ARMAs to UBE races",
        description="Generate a standalone ESL patch that adds UBE races "
                    "to every vanilla non-body ARMA (helmet, gauntlets, "
                    "boots, jewelry, etc.). Fixes invisible vanilla armor "
                    "for UBE-race players — the same fix that per-mod "
                    "patches apply to CBBE-replaced armors, but for the "
                    "vanilla items no replacer covers. Skips body-slot "
                    "(slot 32) ARMAs since those need real CBBE-to-UBE "
                    "mesh conversion via the main converter path.")
    vc.add_argument("-o", "--output", type=Path, required=True,
                    help="Output path for the vanilla-compat patch ESP.")
    vc.add_argument("--data-dir", type=Path, action="append", default=None,
                    help="Additional data directory to search for master "
                         "ESMs and UBE_AllRace.esp. Can be passed multiple "
                         "times. If omitted, the modlist's Stock Game/Data "
                         "and sibling mod folders are auto-discovered.")
    vc.add_argument("--reference-mod", type=Path, default=None,
                    help="A source mod folder whose parent layout is used "
                         "to auto-discover master_data_dirs. Ignored if "
                         "--data-dir is given.")
    vc.add_argument("--include-cc", action="store_true",
                    help="Also scan Creation Club ccbgssse*.esm masters. "
                         "DEFAULT IS OFF — only include CC if you're sure "
                         "they're enabled in your load order. Declaring a "
                         "CC ESM as a master without it being loaded "
                         "crashes the game on startup.")

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
    auto_p.add_argument("--no-winner-rebase", action="store_true",
                        help="Disable the #132 load-order winner rebase "
                             "(default ON: merged ARMOs adopt the winner's "
                             "Requiem/overhaul balance; stats-only, no masters "
                             "added).")
    auto_p.add_argument("--incremental", action="store_true",
                        help="Reuse up-to-date converted NIFs (skip the refit) "
                             "for a fast re-run; a code or body change forces a "
                             "full re-convert automatically.")
    auto_p.add_argument("--no-vanilla-compat", action="store_true",
                        help="Skip the vanilla non-body race-coverage patch.")
    auto_p.add_argument("--no-modded-nonbody", action="store_true",
                        help="Skip the mod-defined non-body UBE coverage pass "
                             "(mint ESP + SkyPatcher INI for overhaul-"
                             "rearmatured helmets/circlets/jewelry).")
    auto_p.add_argument("--no-vanilla-bodies", action="store_true",
                        help="Skip standalone vanilla BODY armour conversion "
                             "(the base-game/loose vanilla cuirasses refit to "
                             "UBE). Leave ON to cover vanilla armor with no "
                             "replacer mod required.")
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
                             "to have populated _unmerged_patches/. Implies "
                             "skipping the vanilla-compat + vanilla-body steps "
                             "unless --force-vanilla is given.")
    auto_p.add_argument("--force-vanilla", action="store_true",
                        help="With --only-mods, ALSO regenerate the vanilla "
                             "race-coverage patch + standalone vanilla bodies "
                             "(otherwise skipped on an incremental run).")

    # Graphical front-end (Tkinter). Drives the same `auto` pipeline on a
    # background thread; see src/gui.py. No args.
    sub.add_parser(
        "gui",
        help="launch the graphical interface (a window over the `auto` flow)")

    return p


def write_conversion_summary(output_dir: Path, results: list) -> Path | None:
    """Write a batch coverage report (`conversion_summary.txt`) at the output
    mod root, aggregating every source's result.

    `results` is the `[(source_dir, AutoConvertResult | None, error | None)]`
    list `_cmd_convert` builds. The report's headline is the COVERAGE picture
    the user cares about: which mods converted, how many meshes each produced,
    how many were resolved from OTHER mods by the VFS broadening, and — most
    importantly — which selected mods produced ZERO meshes (the ones most
    likely still missing/invisible in-game, worth a manual look).

    Best-effort: never raises (a report failure must not fail the batch).
    Returns the written path, or None on failure.
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
        # Selected mods that produced no converted meshes. Split two ways so we
        # don't false-alarm: a mod whose meshes were all COLLISION-skipped is a
        # DUPLICATE SOURCE — an earlier-priority sibling already converted the
        # shared output path (first-writer-wins), so the armour IS converted and
        # is NOT missing. Only mods where nothing resolved are the real
        # "maybe still missing" set worth a manual look.
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


def _build_armo_winner_index_for_merge(patch_paths, layout, merged_name):
    """Build the #132 ARMO winner index for the merge: scan ACTIVE plugins in
    load order for the ARMOs our patches override, returning each ARMO's
    load-order winning record. Returns None if the load order can't be read
    (merge then proceeds without winner-rebase). Restricted to our target ARMOs
    for speed (~1-2s)."""
    ordered_names = paths.active_plugins_ordered(layout)
    if not ordered_names:
        return None
    file_index = paths.plugin_file_index(layout)
    ordered_paths = [file_index[n.lower()] for n in ordered_names
                     if n.lower() in file_index]
    if not ordered_paths:
        return None

    # Targets = absolute identities of every ARMO override in our patches.
    target_abs: set[tuple[str, int]] = set()
    our_names: set[str] = {merged_name.lower(),
                           "vanilla_ube_race_compat.esp"}
    for pp in patch_paths:
        pp = Path(pp)
        our_names.add(pp.name.lower())
        try:
            pe = esp.ESP.load(pp)
        except Exception:
            continue
        grp = next((g for g in pe.groups if g.label == b"ARMO"), None)
        if not grp:
            continue
        pm = pe.header.masters
        for rec in grp.records:
            target_abs.add(
                ube_patcher._record_abs_fid(rec.formid, pm, pp.name))
    if not target_abs:
        return None
    return ube_patcher.build_armo_winner_index(
        ordered_paths, exclude_names=our_names, target_abs=target_abs)


def _cmd_convert(args):
    # Portable path bootstrap: auto-discover the MO2 layout and EXPORT it to
    # os.environ so spawned worker processes (which re-import the converter
    # fresh) inherit the resolved mods root + game Data without re-scanning.
    # An explicit --mods-root (if the CLI provides one) overrides discovery.
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

    # Resolve sources + output. Two CLI forms supported:
    #   1. New form: -o OUTPUT SRC1 [SRC2 ...]
    #   2. Legacy single-source: SRC OUTPUT (last positional is output)
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

    # One shared pool for the whole batch. Spawning workers is the
    # second-biggest cost in the convert pipeline (after actual NIF
    # processing): each worker imports pynifly, loads its DLL, and on
    # first call also parses the UBE body NIF (~5 MB), the body OSD
    # (~11 MB), and computes the CBBE->UBE delta. Caching all of that
    # in worker-local memory and reusing the pool across mods turns
    # 23 × N cold-starts into 23 cold-starts total.
    if args.workers is not None and args.workers <= 1:
        shared_pool = None  # serial path; auto_convert_mod handles it
    else:
        pool_workers = args.workers
        if pool_workers is None:
            pool_workers = max(1, (os.cpu_count() or 4) - 1)
        shared_pool = ProcessPoolExecutor(max_workers=pool_workers)
        print(f"  batch worker pool: {pool_workers} workers "
              f"(shared across all sources)")
        # Pre-warm: force every worker to eagerly load pynifly + body
        # refs in parallel BEFORE real NIFs hit the queue. Without
        # this, the first mod's first ~20 NIFs run at single-worker
        # throughput while the rest of the pool slowly spins up.
        try:
            _prewarm_pool(shared_pool, pool_workers, args.ube_body_ref)
        except Exception as e:
            print(f"  !! pre-warm failed (non-fatal): {e!r}")

    # Shared output-path claim set for cross-mod collision protection.
    # First-writer wins: each `auto_convert_mod` call filters its work
    # items against this set so later sources can't silently overwrite
    # earlier sources' output. Source order on the command line is the
    # explicit precedence (sources listed earlier win on conflicts).
    claimed_dst_paths: set[Path] = set()

    # Resolve the master/Data search dirs ONCE for the whole batch. This walk
    # enumerates every sibling mod folder (thousands on a big modlist) and the
    # result is identical for every source (same mods root), so computing it
    # per-mod was pure waste. Passing it in also lets ube_patcher's batch
    # caches (parsed master ESMs, UBE-race scan, STRINGS resolver) hit on a
    # stable key. Fresh batch -> clear those caches first.
    ube_patcher.clear_batch_caches()
    batch_master_data_dirs = (_discover_master_data_dirs(sources[0])
                              if sources else None)
    if batch_master_data_dirs:
        print(f"  master/Data search: {len(batch_master_data_dirs)} dir(s) "
              "(resolved once for the batch)")

    # Build the full-VFS mesh index ONCE for the batch. Maps every armour mesh
    # the sources' ARMAs reference to the WINNING provider across ALL enabled
    # mods (MO2 priority order) — so armour whose meshes live in a DIFFERENT
    # mod than its ESP (BodySlide output, mesh/texture replacers, patches) is
    # found and converted instead of silently missed. Scoped to the referenced
    # meshes so the walk stays bounded + early-stops on huge modlists. [coverage]
    mesh_vfs_index = None
    try:
        _lay = paths.discover_layout()
        _enabled_ordered = paths.enabled_mods_ordered(_lay)
        _mr = paths.mods_root()
        # Reuse the index `_find_armor_mod_dirs` already built during source
        # selection (same modlist walk). It's scoped to EVERY candidate's
        # armour meshes — a superset of the selected sources' — so it covers
        # every mesh this convert will touch. Falls back to building one when
        # `convert` is invoked directly with no prior selection pass.
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

    # BSA fallback resolver (lazy): when an armour mesh isn't loose ANYWHERE,
    # pull it from the load-order BSAs (Vigilant.bsa etc.). Built once here so
    # every auto_convert_mod call in this batch shares one (lazily-scanned)
    # index; it only scans if a loose lookup actually misses. #179-bsa
    global _BATCH_BSA_INDEX
    _BATCH_BSA_INDEX = None
    try:
        _blay = paths.discover_layout()
        _bord = paths.enabled_mods_ordered(_blay)
        _bmr = paths.mods_root()
        if _bmr is not None and _bord:
            _BATCH_BSA_INDEX = _BsaMeshIndex(
                [Path(_bmr) / n for n in _bord],
                Path(output) / "_bsa_staging")
    except Exception:
        _BATCH_BSA_INDEX = None

    # Incremental floor: skip re-converting a NIF whose output is newer than its
    # source AND this floor. Floor = newest of (converter source code, UBE body
    # ref) so ANY code or body change invalidates every cached output. Opt-in.
    incremental_floor = None
    if getattr(args, "incremental", False):
        try:
            code_mtime = max(
                (p.stat().st_mtime for p in Path(__file__).parent.glob("*.py")),
                default=0.0)
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
            print(f"\n--- [{i}/{len(sources)}] converting {src.name!r} ---")
            try:
                r = auto_convert_mod(
                    src, output,
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
                    nif_workers=args.workers,
                    nif_pool=shared_pool,
                    unmerged_patch_subdir=args.unmerged_patch_subdir,
                    claimed_dst_paths=claimed_dst_paths,
                    master_data_dirs=batch_master_data_dirs,
                    mesh_vfs_index=mesh_vfs_index,
                    incremental_floor=incremental_floor,
                )
                results.append((src, r, None))
            except Exception as e:
                results.append((src, None, e))
                print(f"!! conversion failed: {e!r}")
    finally:
        if shared_pool is not None:
            shared_pool.shutdown(wait=True)
        _BATCH_BSA_INDEX = None   # release cached BSA archives after the batch

    print(f"\n=== batch auto-conversion done ({len(results)} mod(s)) ===")

    # Weight-partner safety net (#180): guarantee every converted body mesh has
    # BOTH _0 and _1 on disk (a missing partner = the piece breaks/vanishes at
    # that body weight). Fills any single-weight base from its present partner.
    try:
        _filled = _complete_weight_partners(output)
        if _filled:
            print(f"  weight-partner completion: filled {_filled} missing "
                  "_0/_1 partner mesh(es) (would otherwise break at one weight)")
    except Exception as _e:
        print(f"  (weight-partner completion skipped: {_e!r})")

    # Three distinct severities:
    #   merge_blockers   — hard ESP-generation failures (a whole mod
    #                      raised). These block the final ESP auto-merge,
    #                      because the merge would combine incomplete /
    #                      missing patches.
    #   overall_failures — anything that should make the process exit
    #                      non-zero: merge_blockers + per-NIF conversion
    #                      errors + output load failures. These do NOT
    #                      block the merge (a single bad mesh shouldn't
    #                      deprive the user of the whole Combined ESP).
    #   warnings         — non-fatal validator notes. Surfaced loudly but
    #                      neither block the merge nor fail the exit code.
    merge_blockers = 0
    overall_failures = 0
    overall_warnings = 0
    for src, r, err in results:
        print(f"\n  {src.name}")
        if err is not None:
            print(f"    !! FAILED: {err!r}")
            merge_blockers += 1
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
        # Per-NIF conversion errors (worker caught an exception). Real
        # failures — affect the exit code — but don't block the ESP merge.
        if r.nif_errors:
            print(f"    !! CONVERSION ERRORS on {r.nif_errors} NIF(s):")
            for er in r.nif_error_results:
                print(f"       {er.src_path.name}: {er.reason}")
            overall_failures += r.nif_errors
        if r.nif_load_failures:
            print(f"    !! LOAD FAILURES on {len(r.nif_load_failures)} output NIFs")
            for p in r.nif_load_failures:
                print(f"       {p}")
            overall_failures += 1
        # Surface ESP structural validator warnings prominently — these
        # catch malformed output before the user tries it in-game. They
        # are WARNINGS: surfaced loudly, but they neither block the
        # auto-merge nor fail the exit code. (Previously they did both,
        # which left the user with NO Combined ESP — worse than the
        # warned-about issue, since MO2 then loads nothing.)
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

    # --- Vertex-color shader-flag sanitize (CTD fix) --------------------
    # Final sweep over every converted NIF: clear Vertex_Colors/Vertex_Alpha
    # shader flags on shapes whose mesh has no vertex-color buffer. Our shape
    # rebuild paths drop the source colors but inherit its shader flags, and a
    # shader that reads a missing color buffer crashes the engine while
    # building the 3D model (deterministic startup CTD for player-worn gear).
    # See nif_convert.fix_vertex_color_shader_flags. Idempotent.
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

    # --- Auto-merge into Combined ESP -----------------------------------
    # Walks every *UBE patch.esp* under the unmerged subdir (or the
    # output root if subdir is disabled) and merges them all into a
    # single ESL-flagged ESP at the mod root. This is what the user
    # ultimately wants MO2 to load — having ONLY the merged ESP visible
    # at the mod root avoids MO2's plugin scanner auto-re-enabling the
    # per-source patches (which collides with the merged records and
    # CTDs on load).
    if args.auto_merge and merge_blockers == 0:
        if args.unmerged_patch_subdir and args.unmerged_patch_subdir not in (".", "/"):
            patches_dir = output / args.unmerged_patch_subdir
        else:
            patches_dir = output
        if patches_dir.is_dir():
            patch_paths = sorted(patches_dir.glob("*UBE patch.esp"))
            if patch_paths:
                merged_out = output / args.merged_name
                print(f"\n--- auto-merging {len(patch_paths)} patch(es) "
                      f"into {merged_out.name} ---")
                # #132: build the load-order winner index so each ARMO override
                # adopts the WINNER's balance (Requiem armor rating/keywords/
                # name) instead of the bare master's. Stats-only -> adds no
                # masters. Reuse a shared index passed by _cmd_auto (so the
                # vanilla-compat pass shares the same one scan); else build a
                # target-filtered one. Skipped if --no-winner-rebase / no order.
                winner_index = getattr(args, "armo_winner_index", None)
                if winner_index is None \
                        and not getattr(args, "no_winner_rebase", False):
                    try:
                        winner_index = _build_armo_winner_index_for_merge(
                            patch_paths, _layout, merged_out.name)
                        if winner_index:
                            print(f"  winner index: {len(winner_index)} "
                                  "load-order winners")
                    except Exception as e:
                        print(f"  !! winner-index build failed (skipping "
                              f"rebase): {e!r}")
                try:
                    stats = ube_patcher.merge_patches_split(
                        patch_paths, merged_out, esl_flag=True,
                        master_data_dirs=batch_master_data_dirs,
                        armo_winner_index=winner_index,
                    )
                    print(f"  merged ESP: {merged_out}")
                    print(f"  ESL flag  : {stats.get('esl_flagged')}")
                    if stats.get('split_pieces', 1) > 1:
                        print(f"  SPLIT     : {stats['split_pieces']} ESL pieces "
                              f"-> {', '.join(stats.get('pieces', []))} "
                              "(enable ALL of them)")
                    if stats.get('winner_rebased_armos'):
                        print("  #132 rebased: "
                              f"{stats.get('winner_rebased_armos')} ARMO "
                              "override(s) onto load-order winner stats")
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
                    # Fix stale alt-texture (color-variant) 3D names+indices:
                    # the NIF merge reorders/collapses shapes, so the ESP's
                    # MO2S/MO3S indices point at the wrong (or out-of-range)
                    # shapes -> variants recolor wrong. Reconcile against the
                    # converted NIFs so color variants land correctly.
                    try:
                        nfix = ube_patcher.reconcile_alt_texture_indices(
                            merged_out, output / "meshes")
                        print(f"  alt-texture reconcile: fixed {nfix} ARMA(s)")
                    except Exception as e:
                        print(f"  !! alt-texture reconcile failed: {e!r}")
                except Exception as e:
                    print(f"!! auto-merge failed: {e!r}")
                    overall_failures += 1
            else:
                print(f"\n  (no patches found in {patches_dir} — "
                      "skipping auto-merge)")
    elif args.auto_merge and merge_blockers > 0:
        print(f"\n!! auto-merge SKIPPED: {merge_blockers} mod(s) failed ESP "
              "generation. Fix those before merging — the Combined ESP "
              "would otherwise be built from incomplete patches.")

    if args.render_previews:
        from . import preview
        print(f"\n--- rendering morph previews ---")
        try:
            preview_results = preview.render_all_previews(output)
        except Exception as e:
            print(f"!! preview render failed: {e!r}")
            preview_results = []
        ok = sum(1 for r in preview_results if "error" not in r)
        err = sum(1 for r in preview_results if "error" in r)
        # Real bugs: BODYTRI string present but file isn't on disk.
        # (vs. legitimate "no BODYTRI string at all" for jewelry etc.)
        broken_bodytri = [
            r for r in preview_results
            if "error" not in r
            and r.get("bodytri_string")
            and not r.get("bodytri_resolved")
        ]
        # Worst displacement seen across the batch — outlier flag.
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

    # Coverage report: a single conversion_summary.txt at the output root with
    # the batch picture (per-mod counts, VFS-from-other-mods, and the list of
    # selected mods that produced ZERO meshes — the likely-still-missing set).
    summary_path = write_conversion_summary(output, results)
    if summary_path is not None:
        print(f"\n  coverage report: {summary_path}")

    # Final tally line so the user/CI sees severity at a glance.
    if overall_failures or overall_warnings:
        print(f"\n=== {overall_failures} failure(s), "
              f"{overall_warnings} warning(s) ===")
    else:
        print(f"\n=== all clear ===")

    return 0 if overall_failures == 0 else 2


# Heuristics for "is this mod an armor mod we should convert?" — shared by
# the `scan` (preview) and `auto` (full run) commands so they agree.
_ARMOR_PATH_HINTS = ("armor", "armour", "clothes", "clothing", "outfit",
                     "outfits", "weapons")  # weapons sneak in via shared paths
_ENV_PATH_HINTS = ("landscape", "architecture", "caves", "cave", "interiors",
                   "dungeons", "actors\\character\\character assets",
                   "static", "props", "creatures", "monsters", "vfx")
# Mod-name substrings that mark a mod as NOT a conversion SOURCE. Lowercased.
#   * already-UBE content, body/bodyslide mods, and our own output;
#   * khajiit / beast-race bodies and their fur-overlay morph systems — the
#     converter targets the human female UBE body, and the user scopes these
#     out ("ignore khajiit and half race"). Half-/beast races are also caught
#     by the DefaultRace gate below; these names belt-and-suspender the
#     fur-morph body overlay (which binds DefaultRace but isn't armor).
_NONSOURCE_NAME_HINTS = ("ube", "bodyslide output", "cbbetoube",
                         "khajiit", "ohmes", "fur morph", "fur_morph")

# Child-content mods (RS Children, Kids in Nirn, The Kids Are Alright, ...).
# Their clothing is child-SIZED and made for child NPCs, so even when it binds
# DefaultRace (player-equippable) it is not armour "for the player" — warping a
# child mesh onto the adult UBE body is nonsense. User: "not include kids".
# Matched as whole WORDS (not substrings) so a real-armour term like "kidskin"
# (a leather) is never caught.
_CHILD_NAME_WORDS = frozenset({"kids", "kid", "children", "child"})


def _is_child_content_mod(name: str) -> bool:
    tokens = set("".join(c if c.isalnum() else " "
                         for c in name.lower()).split())
    return bool(tokens & _CHILD_NAME_WORDS)

# DefaultRace ("DefaultRace" [RACE:00000019] in Skyrim.esm) — the engine's
# canonical humanoid race that essentially ALL player-equippable human armor
# binds its ARMA to. Creatures (HorseRace), beast forms (WerebearBeastRace),
# and custom playable races bind elsewhere, so "an ARMA whose primary race
# is DefaultRace" cleanly separates player armor from creature/race content.
# A structural engine constant, not a per-mod name.
_DEFAULT_RACE_LOW24 = 0x000019

# Biped slots that carry BODY-FITTED geometry — the ONLY items the converter
# should touch. We refit a mesh to the UBE body; that only makes sense for armour
# skinned to the body. An ARMA with NONE of these slots (a shield 39, helmet 30,
# hood/beard 30+44, hair 31/41, amulet 35, ring 36, circlet 42, ears 43, backpack
# 47, ...) has no body geometry — converting it is pointless AND crashes the game:
# the converter copies the (usually UNSKINNED) mesh and gives its ARMA the UBE
# body races, so at actor setup the engine processes an unskinned item as a
# body-race armature and dereferences missing skin data -> ACCESS_VIOLATION (the
# Beard Mask Fix hood + Falmer Slayer shield startup crashes). ALLOWLIST (not a
# denylist) on purpose: a body slot we forgot just means a rare armour is skipped
# (invisible), whereas a non-body slot we forgot would crash. Covers body(32),
# hands(33), forearms(34), feet(37), calves(38), modded chest(46/60), pelvis/
# skirt(49/52), and the leg slots(53-58). bit = 1 << (slot - 30).
_BODY_SLOT_BITS = sum(
    1 << (s - 30)
    for s in (32, 33, 34, 37, 38, 46, 49, 52, 53, 54, 55, 56, 57, 58, 60))

# AMBIGUOUS free modder slots: SOME mods put body cloth here (DDV Ruby Flower
# uses pants=44, skirt=47), OTHERS put non-body accessories (beards 44,
# backpacks 47, necklaces, ...). We admit these as armour-mesh CANDIDATES so
# the lower-body cloth gets converted instead of staying CBBE-shaped under a
# UBE morph — but a candidate-slot mesh is only actually kept (converted + UBE-
# race tagged) when its NIF is skinned to body-fit bones (_nif_has_bodyfit_skin).
# That preserves the _BODY_SLOT_BITS allowlist's crash protection: an unskinned
# accessory given a UBE body race CTDs at actor setup. Selection eligibility
# still uses the STRICT set, so beard/backpack-only mods aren't pulled in. #165.
_BODY_CANDIDATE_SLOT_BITS = sum(1 << (s - 30) for s in (44, 45, 47, 48, 59, 61))

# A draping cape / cloak can ride the HAIR / head slots (31 Hair, 41 Long Hair,
# 43 Ears) so that equipping it hides the hair — a hood+cape (e.g. Traveling
# Mage's TMage_Cape / TMage_CapeHair, #177). The slot allowlist excludes those
# slots to skip wigs / beards / facegen, so the cape was silently dropped and
# rendered un-converted (CBBE-shaped, no physics) on the UBE body. A cloak still
# drapes over the body and carries its own HDT physics, so it needs UBE
# conversion exactly like a slot-46 cloak. We admit a mesh on ANY otherwise-
# excluded slot when its model name marks it a cloak; the body-fit-skin crash
# guard in auto_convert_mod is the backstop that drops anything which isn't
# actually a body-draping mesh (a mislabeled amulet, etc.). Plain hair / wigs /
# hoods carry none of these keywords and stay excluded.
_CLOAK_MESH_KEYWORDS = ("cape", "cloak", "mantle", "shroud", "cloth_cloak")

# Mesh basenames (weight-suffix stripped) that are the NUDE BODY SKIN, not an
# armour piece: the bare body/hands/feet the body mod itself provides. A mod
# whose only DefaultRace ARMA models are these IS the body mod (CBBE/UBE/a
# follower's nude body) and must not be "converted"; an armour mod that also
# ships a nude-body ARMA still keeps its actual armour meshes. Real armour
# pieces are never named plain "femalebody"/"femalehands"/"femalefeet".
_BODY_SKIN_BASENAMES = frozenset({
    "femalebody", "malebody", "femalehands", "malehands",
    "femalefeet", "malefeet",
    "1stpersonfemalebody", "1stpersonmalebody",
    "1stpersonfemalehands", "1stpersonmalehands",
})


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
    case (e.g. ``'armory/Ruby flower/DDV - Ruby flower Top_1.nif'``). Falls
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
            shutil.copy2(present, miss)
            filled += 1
        except OSError:
            pass
    return filled


_BATCH_BSA_INDEX = None   # set per-batch by _cmd_convert; lazy BSA mesh resolver


class _BsaMeshIndex:
    """Load-order-wide FALLBACK resolver for armour meshes that aren't loose --
    extracts them from mod BSAs (Vigilant.bsa, Unslaad, Glenmoril, LOTD, ...) so
    BSA-packed armor mods convert instead of coming out invisible (#179-bsa).

    Consulted ONLY after the loose VFS + source-local lookups miss, so LOOSE
    meshes always win. LAZY: the (slow) BSA scan runs on the FIRST miss, so a
    fully-loose run never pays for it. Texture/voice/sound BSAs are skipped (they
    hold no armour meshes and are the huge ones). Memory: the index pass releases
    each archive's data buffer after listing; extraction re-opens (and caches)
    only the few BSAs that actually hold needed meshes."""

    _SKIP_BSA = ("texture", "voice", " sound", "sounds", "- snd", "facegen")

    def __init__(self, enabled_mod_dirs, staging_dir):
        self._dirs = list(enabled_mod_dirs)   # mod dirs, MO2 priority (highest first)
        self._staging = Path(staging_dir)
        self._index = None                    # rel_lower -> (bsa_path, internal_name)
        self._open: dict = {}                 # bsa_path -> BSAArchive (extract cache)
        self._out: dict = {}                  # rel_lower -> (Path, rel) | None (memo)

    def _scan(self) -> None:
        from .bsa_strings import BSAArchive
        import sys as _s
        self._index = {}
        n = 0
        for d in self._dirs:                  # priority order: first provider wins
            try:
                bsas = sorted(d.glob("*.bsa"))
            except Exception:
                continue
            for bsa in bsas:
                if any(k in bsa.name.lower() for k in self._SKIP_BSA):
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
                # Not loose anywhere -> try the load-order BSAs (Vigilant.bsa,
                # Unslaad, Glenmoril, LOTD, ...). Extracts to a staging dir and
                # returns (extracted_file, meshes_rel). #179-bsa
                ex = _BATCH_BSA_INDEX.extract(key)
                if ex is not None:
                    hit = ex
            if hit is not None:
                seen.add(key)
                pairs.append(hit)
    return pairs


def _player_armor_mesh_bases(mod_dir: Path,
                             include_candidate_slots: bool = False) -> "set[str]":
    """Weight-agnostic rel-path keys of every mesh that a DefaultRace ARMA in
    this mod points at AS AN ARMOR PIECE (its biped slot is not hair-only).

    `include_candidate_slots`: when True, ALSO admit meshes on the ambiguous
    free modder slots (_BODY_CANDIDATE_SLOT_BITS: 44/45/47/48/59/61) that some
    mods use for body cloth (pants/skirts). Used for COVERAGE (mesh resolution
    + the VFS index) on mods already selected via a standard body slot. The
    crash guard in auto_convert_mod then drops any candidate-slot mesh whose
    NIF isn't actually body-skinned. Default False = strict body-slot allowlist,
    which is what SELECTION uses (so beard/backpack-only mods aren't selected).

    This is the operational definition of "an equippable armor piece for the
    player": a mesh bound, via an ARMA, to the humanoid DefaultRace in an
    armour-category slot. It is the single signal used both to SELECT source
    mods and to pick WHICH NIFs inside a mod to convert — so facegen heads,
    loose clutter, ground models, and hair/wigs (none of which are DefaultRace
    armour-piece ARMA models) are skipped even when they ship alongside real
    armour. Empty result => the mod has no player armour => not a source.

    The ESP parser doesn't decompress, so this is cheap."""
    from . import esp as _esp
    import struct as _struct
    bases: "set[str]" = set()
    for ep in _find_source_esps(mod_dir):
        try:
            e = _esp.ESP.load_cached(ep)  # read-only scan -> cached parse
        except Exception:
            continue
        masters = e.header.masters
        for g in e.groups:
            if g.label != b"ARMA":
                continue
            for rec in g.records:
                rnam = None
                slot = 0
                edid = ""
                models: list[str] = []
                for sig, sd in _esp.iter_subrecords(rec.payload):
                    if sig == b"EDID":
                        edid = sd.rstrip(b"\x00").decode(
                            "latin-1", errors="ignore").lower()
                    elif sig == b"RNAM" and len(sd) == 4:
                        rnam = _struct.unpack("<I", sd)[0]
                    elif sig in (b"BOD2", b"BODT") and len(sd) >= 4:
                        slot = _struct.unpack_from("<I", sd, 0)[0]
                    elif sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
                        # Collect ALL model slots, not just female (MOD3/MOD5).
                        # Restricting to MOD3/MOD5 over-dropped real sources:
                        # some mods (e.g. HDT-SMP Vanilla Armors) bind their
                        # female armour mesh through the MOD2 slot, so a
                        # MOD3/MOD5-only filter found zero meshes and the whole
                        # mod was dropped from the source set. The NIF converter
                        # still refits only the female-shaped meshes; this set is
                        # just "which mods/NIFs are candidate armour pieces".
                        models.append(sd.rstrip(b"\x00").decode(
                            "utf-8", errors="ignore"))
                if rnam is None:
                    continue
                mi = rnam >> 24
                if (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24 or \
                   mi >= len(masters) or masters[mi].lower() != "skyrim.esm":
                    continue  # not bound to the humanoid player race
                _accept = _BODY_SLOT_BITS
                if include_candidate_slots:
                    _accept |= _BODY_CANDIDATE_SLOT_BITS
                if (slot & _accept) == 0:
                    # A draping cape/cloak may ride hair/head slots (so it hides
                    # the hair); it still needs UBE conversion. Admit it when a
                    # model FILENAME marks it a cloak — the body-fit-skin crash
                    # guard downstream drops any non-draping false positive. Match
                    # the FILENAME, not the full path: a folder like
                    # "...\\Stormcloaks\\Helmet.nif" contains the substring
                    # "cloak" but is a helmet — matching the basename ("helmet")
                    # avoids that false positive. Plain hair/wigs/hoods carry no
                    # cloak keyword in the filename and stay excluded.
                    if not any(_kw in m.replace("\\", "/").rsplit("/", 1)[-1].lower()
                               for m in models for _kw in _CLOAK_MESH_KEYWORDS):
                        continue  # no body-fitted slot (shield/helmet/hood/hair/
                                  # circlet/amulet/ring/ears) — don't convert
                for m in models:
                    if not m:
                        continue
                    base = _weight_base_key(m)
                    if base.rsplit("/", 1)[-1] in _BODY_SKIN_BASENAMES:
                        continue  # nude body skin — not an armour piece
                    bases.add(base)
    bases.discard("")
    return bases


# Skeleton-bone name fragments that mark a mesh as BODY-FITTED cloth (wraps the
# torso/legs): a piece skinned to any of these follows the body. Used by the
# candidate-slot crash guard to tell real body cloth (pants/skirts/leg armour —
# weighted to thighs/calves/butt/belly/breast) apart from the accessories that
# share an ambiguous modder slot: a beard skins to the head, a shield is
# unskinned, a backpack/cloak skins to the spine — NONE carry these bones.
# Deliberately excludes spine/pelvis alone (cloaks/backpacks weight the spine).
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


# Full-VFS mesh index built once during source selection (over every candidate
# mod's armour meshes) and reused by the conversion step, so the modlist's
# meshes are walked once per run instead of twice. Keyed by lowercased
# mods_root. Populated by `_find_armor_mod_dirs(require_arma=True)`; read by
# `_cmd_convert`, which falls back to building its own if this is empty (e.g. a
# direct `convert` invocation with no prior selection pass).
_BATCH_MESH_INDEX: "dict[str, dict]" = {}

# Memo of _find_armor_mod_dirs results, keyed by inputs. Lets the GUI's Refresh
# scan and the subsequent Convert run (SAME process) share ONE discovery pass
# instead of scanning twice (so the dedup line prints once). The _BATCH_MESH_INDEX
# side effect is set on the first (uncached) call and persists, so the convert
# step still finds it on a cache hit.
_ARMOR_MOD_DIRS_CACHE: "dict[tuple, list[dict]]" = {}


def _has_any_source_plugin(mod_dir: Path) -> bool:
    """True if the folder holds ANY .esp/.esm/.esl, via a SINGLE recursive walk
    that STOPS at the first match and does NOT descend into asset dirs (plugins
    live at the mod root / optional subfolders, never under meshes\\textures\\
    facegen -- the exact dirs _find_source_esps excludes). Replaces three full
    rglobs (one per extension) -> far cheaper for plugin-less mods, especially
    under MO2's VFS. Master/CC exclusion still happens in _find_source_esps when
    the mod is actually parsed, so a master-only folder still gets dropped."""
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
                         ) -> list[dict]:
    """Memoizing wrapper around _find_armor_mod_dirs_uncached so a GUI Refresh +
    the following Convert (same process, same inputs) reuse ONE scan. Returns a
    FRESH list on every call (callers, e.g. _cmd_auto, sort it in place)."""
    _key = (str(mods_root).lower(), bool(require_arma),
            frozenset(n.lower() for n in (extra_exclude_names or set())),
            frozenset(enabled_names or ()),
            tuple(enabled_ordered or ()),
            frozenset(n.lower() for n in (index_skip_mods or set())))
    _cached = _ARMOR_MOD_DIRS_CACHE.get(_key)
    if _cached is not None:
        return list(_cached)
    _result = _find_armor_mod_dirs_uncached(
        mods_root, extra_exclude_names=extra_exclude_names,
        enabled_names=enabled_names, require_arma=require_arma,
        enabled_ordered=enabled_ordered, index_skip_mods=index_skip_mods)
    _ARMOR_MOD_DIRS_CACHE[_key] = list(_result)
    return _result


def _find_armor_mod_dirs_uncached(mods_root: Path,
                         extra_exclude_names: "set[str] | None" = None,
                         enabled_names: "set[str] | None" = None,
                         require_arma: bool = False,
                         enabled_ordered: "list[str] | None" = None,
                         index_skip_mods: "set[str] | None" = None,
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
        # A source plugin can be .esp OR a bespoke-armour master/.esl (Vigilant.esm,
        # Legacy of the Dragonborn.esm, Unslaad.esm, ...). #179. SINGLE asset-pruned
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

    # require_arma (the `auto` run): a mod is a source if a DefaultRace ARMA
    # equips an armour-slot mesh. We count the matching NIFs in the mod's OWN
    # folder first (fast); but a mod whose armour meshes are BodySlide-built or
    # otherwise provided by ANOTHER mod (its meshes live in the Bodyslide-output
    # mod, a replacer, a patch) has a 0 own-folder count yet is STILL a valid
    # source. Those are resolved through the full MO2 VFS so they stop getting
    # dropped here (e.g. DDV Ruby Flower). Same coverage fix as the convert step.
    pending_vfs: "list[tuple[Path, set]]" = []
    union_all: "set[str]" = set()   # EVERY candidate's armour mesh keys
    for mod_dir in mod_dirs:
        if not _name_ok(mod_dir):
            continue
        armor_bases = _player_armor_mesh_bases(mod_dir)  # STRICT = eligibility
        if not armor_bases:
            continue  # no player-equippable armour piece -> not a source
        # Broaden to lower-body cloth on ambiguous modder slots (44/47/...) for
        # COVERAGE (the VFS index + own-count + pending resolve) — but only for a
        # mod ALREADY eligible via a standard body slot, so beard/backpack-only
        # mods aren't pulled in. The convert step's crash guard drops any
        # candidate mesh that isn't body-skinned. #165.
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

    # Build the full-VFS mesh index ONCE over EVERY candidate's armour meshes
    # (not just the pending ones) so the later conversion step reuses this exact
    # index instead of walking the modlist a second time. Stored in the module
    # cache keyed by mods_root for `_cmd_convert` to pick up. Covering all
    # candidates (⊇ the sources that get converted) guarantees the conversion
    # never misses a mesh by reusing this index.
    # CRITICAL: skip ONLY the output mod here, NOT `excl`. `excl` (the source-
    # selection exclude) also contains the BODY mods — including the BodySlide-
    # output mod, because that's where the UBE body ref lives. But the BodySlide-
    # output mod is exactly where most armours' BUILT FEMALE meshes are, so
    # skipping it from the RESOLUTION index makes every such armour resolve to
    # nothing (proven: DDV Ruby Flower resolved only its bandit meshes, all its
    # BodySlide-output pieces — Top/Boots/Gloves/Pants/Skirt — vanished). The
    # body-mod exclusion is a SELECTION concern (don't convert the body mod
    # itself), handled by `_name_ok`; mesh resolution must see every provider.
    _index_skip = {n.lower() for n in (index_skip_mods or set())}
    vfs: "dict" = {}
    if enabled_ordered and union_all:
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

    # --- Duplicate-plugin dedup: keep the LOAD-ORDER-WINNING copy ----------
    # When the SAME plugin filename ships in multiple enabled mods (e.g. three
    # copies of `_Fuse00_ArmorHelga.esp`), the game loads exactly ONE — the
    # highest-MO2-priority copy (first in `enabled_ordered`). We MUST patch that
    # copy: patching a lower-priority copy emits ARMA/ARMO overrides against the
    # wrong FormIDs and/or a record set the loaded ESP doesn't contain — the
    # records that exist only in the winning copy then have no UBE armature and
    # render INVISIBLE on the UBE race (the Helga "Unarmored Pants" bug: the
    # Pants ARMOs live only in the winning 'My fixes' copy). The mesh the winner
    # references still resolves through the VFS to its highest-priority provider,
    # so dropping the loser never loses meshes. Drop any candidate whose EVERY
    # top-level plugin is already claimed by a higher-priority candidate. #168
    if enabled_ordered:
        _prio = {n: i for i, n in enumerate(enabled_ordered)}  # lower = wins

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


def list_convertible_mods(output_dir: "Path | None" = None) -> list:
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
    # Same body-mod exclusion as _cmd_auto (CBBE base + UBE body + our output).
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
        index_skip_mods={output.name})
    prio = paths.enabled_mods_ordered(lay)
    if prio:
        rank = {name: i for i, name in enumerate(prio)}
        cands.sort(key=lambda c: rank.get(c["name"], len(rank)))

    def _n(c):                       # armor_nifs is a COUNT in _cmd_auto / scan
        v = c.get("armor_nifs", 0)
        return len(v) if isinstance(v, (list, tuple, set)) else int(v or 0)
    return [{"name": c["name"], "nifs": _n(c)} for c in cands]


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

    # Exclude the body mods themselves (the CBBE base + UBE body): they're
    # 3BA-rigged so they'd pass the content filter, but they ARE the body,
    # not equippable armor. Derive their folder names from the discovered
    # body NIFs — no hardcoded names.
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

    print("  scanning mods for player-equippable armor...")
    # Inclusive by design: every ENABLED mod that ships wearable armor (ESP
    # with ARMA records + armor-path female meshes), minus the body mods.
    # We do NOT gate on 3BA-rig content: source CBBE armor often lacks the
    # scale bones (the converter adds them), so that test wrongly drops real
    # armor — and retextured vanilla armor IS player-equippable, so it's in
    # scope too. Goal: ALL equippable armor for the player, completeness first.
    candidates = _find_armor_mod_dirs(
        mr, extra_exclude_names=exclude, enabled_names=enabled,
        require_arma=True, enabled_ordered=paths.enabled_mods_ordered(lay),
        # The shared mesh index must skip ONLY our own output mod — never the
        # body/BodySlide mods (which host most armours' built female meshes).
        index_skip_mods={output.name})
    if not candidates:
        print("error: found no equippable armor mods to convert.")
        return 2

    # --only-mods: incremental reconvert of a chosen subset. We still convert ONLY
    # these (fast — skips the expensive NIF conversion for everyone else), but the
    # downstream merge re-globs ALL patches in _unmerged_patches/, so unselected
    # mods keep their existing patch + meshes (the dir is never wiped). #179
    only = getattr(args, "only_mods", None)
    if only:
        wanted = {n.strip().lower()
                  for chunk in only for n in chunk.split(",") if n.strip()}
        before = len(candidates)
        candidates = [c for c in candidates if c["name"].lower() in wanted]
        missing = sorted(wanted - {c["name"].lower() for c in candidates})
        print(f"  --only-mods: {len(candidates)}/{before} mod(s) selected"
              + (f"; NOT FOUND: {missing}" if missing else ""))
        if not candidates:
            print("error: --only-mods matched no discovered armor mods. Run "
                  "`scan` or the GUI 'Refresh mod list' for the exact names.")
            return 2
        # Incremental: don't redo the (unchanged) vanilla coverage unless forced.
        if not getattr(args, "force_vanilla", False):
            args.no_vanilla_compat = True
            args.no_vanilla_bodies = True

    # Order sources by MO2 LOAD PRIORITY (highest first). The convert step's
    # cross-mod collision guard is "first-writer wins"; feeding it sources in
    # priority order makes that resolve a shared mesh path exactly as the game
    # does — the higher-priority mod (the one that overwrites in MO2) is the
    # one converted, the lower-priority duplicate is skipped. Without this the
    # order was by armour-NIF count, which could convert the LOSING mesh.
    prio = paths.enabled_mods_ordered(lay)
    if prio:
        rank = {name: i for i, name in enumerate(prio)}
        # stable sort: ties keep the existing NIF-count order; unknown mods last
        candidates.sort(key=lambda c: rank.get(c["name"], len(rank)))
        print("  (sources ordered by MO2 load priority so conflict winners "
              "match in-game)")

    sources = [c["path"] for c in candidates]
    print(f"\n=== auto: {len(sources)} armor mod(s) to convert ===")
    for c in candidates[:30]:
        print(f"  {c['name']}  ({c['armor_nifs']} NIFs)")
    if len(candidates) > 30:
        print(f"  ... and {len(candidates) - 30} more")

    if getattr(args, "list_only", False):
        print("\n--list-only: no conversion performed.")
        return 0

    # #132: build ONE load-order winner index up front and share it across BOTH
    # the per-mod merge AND the vanilla-compat pass (one plugin scan, not two).
    # Excludes our own active outputs so we never treat a prior Combined /
    # vanilla-compat as a "winner". Unfiltered so it covers vanilla master ARMOs
    # (vanilla-compat) as well as source ARMOs (merge).
    merged_name = getattr(args, "merged_name", "CBBE_to_UBE_Combined.esp")
    shared_winner_index = None
    if not getattr(args, "no_winner_rebase", False):
        try:
            ordered_names = paths.active_plugins_ordered(lay)
            if ordered_names:
                fidx = paths.plugin_file_index(lay)
                ordered_paths = [fidx[n.lower()] for n in ordered_names
                                 if n.lower() in fidx]
                shared_winner_index = ube_patcher.build_armo_winner_index(
                    ordered_paths,
                    exclude_names={merged_name.lower(),
                                   "vanilla_ube_race_compat.esp"})
                print(f"\n#132 winner index: {len(shared_winner_index)} "
                      "load-order ARMO winners (shared: merge + vanilla-compat)")
        except Exception as e:
            print(f"!! winner index build failed (rebase disabled): {e!r}")

    # Delegate conversion + merge to _cmd_convert with a complete namespace
    # (it re-runs path discovery harmlessly and propagates env to workers).
    conv = _ap.Namespace(
        sources=sources, output=output, esp_name=None,
        no_textures=getattr(args, "no_textures", False),
        copy_textures=getattr(args, "copy_textures", False),
        ube_body_ref=None, workers=getattr(args, "workers", None),
        unmerged_patch_subdir="_unmerged_patches", auto_merge=True,
        merged_name=merged_name,
        render_previews=False, mods_root=mr,
        no_winner_rebase=getattr(args, "no_winner_rebase", False),
        armo_winner_index=shared_winner_index,
        incremental=getattr(args, "incremental", False),
    )
    rc = _cmd_convert(conv)

    # Vanilla race coverage for non-body items no mod replaces (helmets,
    # jewelry, etc.). Body-slot vanilla armor is covered two ways: per-mod
    # mesh-path scan (for armor a replacer SOURCE ships) PLUS the standalone
    # vanilla-body pass below (so NO replacer mod is required).
    if not getattr(args, "no_vanilla_compat", False):
        vc_out = output / "Vanilla_UBE_Race_Compat.esp"
        data_dirs = _discover_master_data_dirs(sources[0])
        if data_dirs:
            # --- Standalone vanilla BODY armour (no replacer 'source' needed) ---
            # Resolve each vanilla slot-32 female mesh (loose override wins ->
            # base-game BSA fallback), refit it to UBE, and feed the resulting
            # converted paths into the vanilla-compat patch so it mints the
            # master-ARMO body coverage. A replacer, if installed, is used
            # automatically (loose override) but is OPTIONAL.
            vanilla_converted: set = set()
            if not getattr(args, "no_vanilla_bodies", False):
                try:
                    from . import vanilla_bsa_armor
                    prio_names = paths.enabled_mods_ordered(lay) or []
                    prio_dirs = [
                        (mr / (p.name if isinstance(p, Path) else p))
                        for p in prio_names]
                    prio_dirs = [d for d in prio_dirs if d.is_dir()]
                    # Skip meshes a real source already converted to !UBE
                    # (its per-mod patch already covers them).
                    already: set = set()
                    ube_root = output / "meshes" / "!UBE"
                    if ube_root.is_dir():
                        for _f in ube_root.rglob("*.nif"):
                            already.add(
                                _f.relative_to(ube_root).as_posix().lower())
                    print(f"\n--- standalone vanilla body armour -> "
                          f"{output.name}\\meshes\\!UBE ---")
                    vstats = vanilla_bsa_armor.convert_vanilla_bodies(
                        output, data_dirs, prio_dirs,
                        list(lay.game_data_dirs or []),
                        _find_ube_body_ref(), already_converted=already)
                    vanilla_converted = vstats.get("converted_rel_paths", set())
                except Exception as e:
                    print(f"  !! standalone vanilla body armour skipped: {e!r}")

            print(f"\n--- vanilla race-compat patch -> {vc_out.name} ---")
            try:
                stats = ube_patcher.generate_vanilla_race_compat_patch(
                    vc_out, data_dirs,
                    converted_rel_paths=(vanilla_converted or None),
                    armo_winner_index=shared_winner_index)
                print(f"  ARMA overrides: {stats.get('arma_overrides', 0)}"
                      f" | vanilla body UBE ARMAs: "
                      f"{stats.get('body_arma_minted', 0)}"
                      f" | body ARMO overrides: "
                      f"{stats.get('body_armo_overrides', 0)}")
                if stats.get('winner_rebased_armos'):
                    print(f"  #132 rebased: {stats.get('winner_rebased_armos')}"
                          " vanilla ARMO override(s) onto winner stats")
                # Fold per-race UBE NUDE-SKIN routing into the same patch so
                # non-Breton UBE races (Redguard/Nord/...) load the !UBE
                # hands/feet/body instead of the vanilla CBBE actor-asset
                # fallback. UBE_AllRace.esp is NOT modified.
                ube_esp = next(
                    (Path(d) / "UBE_AllRace.esp" for d in data_dirs
                     if (Path(d) / "UBE_AllRace.esp").is_file()), None)
                if ube_esp is not None:
                    try:
                        rs = ube_patcher.fold_ube_raceskin_skins(vc_out, ube_esp)
                        if rs.get("folded"):
                            print(f"  UBE race-skin routing: +{rs['folded']} "
                                  f"skin ARMAs for {rs['races']} UBE races "
                                  f"(non-Breton races -> !UBE hands/feet/body)")
                            # The fold re-saved VC AFTER generate_vanilla_race_
                            # compat_patch's own validate_patch, so re-validate
                            # the final folded file (no other backstop).
                            fold_warns = [
                                w for w in ube_patcher.validate_patch(
                                    vc_out, master_data_dirs=data_dirs)
                                if "missing-nif" not in w]
                            if fold_warns:
                                print(f"  !! VC validation after race-skin "
                                      f"fold: {fold_warns[:5]}")
                        else:
                            print(f"  UBE race-skin routing skipped: "
                                  f"{rs.get('reason')}")
                    except Exception as e:
                        print(f"  !! UBE race-skin fold failed: {e!r}")
            except Exception as e:
                print(f"  !! vanilla-compat skipped: {e!r}")
        else:
            print("  (no master data dirs found — skipping vanilla-compat)")

    # Mod-defined non-body coverage (the guard-helmet class): overhauls
    # (Requiem/Authoria) re-armature vanilla helmets/circlets/jewelry with their
    # OWN ArmorAddons that list only vanilla races -> invisible on UBE actors,
    # and vanilla-compat never touches a mod-defined ARMA. This pass mints a
    # UBE-primary ARMA per such item (tiny ESP, ~6 masters) + a SkyPatcher INI
    # that adds it at runtime (no ESP override -> no master-limit blowout).
    mnb_esp_generated = False
    if not getattr(args, "no_modded_nonbody", False):
        try:
            _names = paths.active_plugins_ordered(lay)
            _fidx = paths.plugin_file_index(lay)
            _ordered = [Path(_fidx[n.lower()]) for n in (_names or [])
                        if n.lower() in _fidx]
            if _ordered:
                mnb_esp = output / "UBE_ModNonBody_Coverage.esp"
                _md = _discover_master_data_dirs(sources[0])
                print(f"\n--- mod non-body UBE coverage -> {mnb_esp.name} "
                      "(+ SkyPatcher INI) ---")
                mnb = ube_patcher.generate_modded_nonbody_ube_coverage_patch(
                    mnb_esp, _ordered,
                    exclude_names={mnb_esp.name.lower(),
                                   "cbbe_to_ube_combined.esp",
                                   "vanilla_ube_race_compat.esp"},
                    master_data_dirs=_md)
                ini_lines = mnb.get("ini_lines") or []
                if ini_lines:
                    ini_path = (output / "SKSE" / "Plugins" / "SkyPatcher"
                                / "armor" / "UBE_ModNonBody_Coverage.ini")
                    ini_path.parent.mkdir(parents=True, exist_ok=True)
                    ini_path.write_text("\n".join(ini_lines) + "\n",
                                        encoding="utf-8")
                print(f"  minted UBE ARMAs: {mnb.get('minted_armas')} "
                      f"| items covered: {mnb.get('armo_targets')} "
                      f"| masters: {mnb.get('masters')} "
                      f"| ESL: {mnb.get('esl_flagged')}")
                if mnb_esp.exists() and mnb.get('armo_targets'):
                    mnb_esp_generated = True
                    print("  *** IMPORTANT: ENABLE "
                          "'UBE_ModNonBody_Coverage.esp' IN MO2. ***")
                    print("      Its SkyPatcher INI ships ACTIVE in this mod "
                          "and attaches armatures FROM that ESP at runtime. If "
                          "the ESP is left DISABLED, covered non-body armor "
                          "(helmets / circlets / jewelry) is invisible or "
                          "equippable-in-multiples on UBE actors.")
                _vw = [w for w in mnb.get('validation_warnings', [])
                       if 'missing-nif' not in w]
                if _vw:
                    print(f"  !! validator: {_vw[:5]}")
        except Exception as e:
            print(f"  !! mod non-body coverage skipped: {e!r}")

    # Mod-defined BODY coverage (the Requiem-variant class): overhauls add NEW
    # armor-variant ARMOs (e.g. "Orcish Light Cuirass" = REQ_Light_Orcish_Body)
    # that REUSE a vanilla armature whose mesh we DID convert, but the variant
    # ARMO itself was never overridden -> no UBE armature -> invisible on UBE.
    # This mints a UBE-primary ARMA (model redirected to the converted !UBE mesh)
    # per such item + a SkyPatcher INI that adds it at runtime. We INCLUDE the
    # Combined/Vanilla in the scan so armor those already cover is skipped.
    mbd_esp_generated = False
    if not getattr(args, "no_modded_nonbody", False):
        try:
            _names = paths.active_plugins_ordered(lay)
            _fidx = paths.plugin_file_index(lay)
            _ordered = [Path(_fidx[n.lower()]) for n in (_names or [])
                        if n.lower() in _fidx]
            _ube_root = output / "meshes" / "!UBE"
            _conv_rel = set()
            if _ube_root.is_dir():
                for _nif in _ube_root.rglob("*.nif"):
                    _conv_rel.add(_nif.relative_to(_ube_root).as_posix().lower())
            if _ordered and _conv_rel:
                mbd_esp = output / "UBE_ModBody_Coverage.esp"
                _md = _discover_master_data_dirs(sources[0])
                print(f"\n--- mod BODY UBE coverage -> {mbd_esp.name} "
                      "(+ SkyPatcher INI) ---")
                mbd = ube_patcher.generate_modded_body_ube_coverage_patch(
                    mbd_esp, _ordered, converted_rel_paths=_conv_rel,
                    exclude_names={mbd_esp.name.lower()},
                    master_data_dirs=_md)
                ini_lines = mbd.get("ini_lines") or []
                if ini_lines and mbd.get('armo_targets'):
                    ini_path = (output / "SKSE" / "Plugins" / "SkyPatcher"
                                / "armor" / "UBE_ModBody_Coverage.ini")
                    ini_path.parent.mkdir(parents=True, exist_ok=True)
                    ini_path.write_text("\n".join(ini_lines) + "\n",
                                        encoding="utf-8")
                print(f"  minted UBE ARMAs: {mbd.get('minted_armas')} "
                      f"| body items covered: {mbd.get('armo_targets')} "
                      f"| masters: {mbd.get('masters')} "
                      f"| ESL: {mbd.get('esl_flagged')}")
                if mbd_esp.exists() and mbd.get('armo_targets'):
                    mbd_esp_generated = True
                    print("  *** IMPORTANT: ENABLE 'UBE_ModBody_Coverage.esp' "
                          "IN MO2 -- mod-defined body variants (e.g. Requiem "
                          "'Orcish Light Cuirass') are INVISIBLE on UBE without "
                          "it. Its SkyPatcher INI ships active in this mod. ***")
                _vw = [w for w in mbd.get('validation_warnings', [])
                       if 'missing-nif' not in w]
                if _vw:
                    print(f"  !! validator: {_vw[:5]}")
        except Exception as e:
            print(f"  !! mod body coverage skipped: {e!r}")

    # Pre-flight: the UBE NUDE hands/feet morph (.tri) trap. The converter
    # doesn't build the nude race skin, but a missing hands/feet .tri (build
    # without 'Build Morphs') makes them stay CBBE-shaped while the body
    # morphs UBE — a common, hard-to-spot setup mistake. Surface it loudly.
    try:
        morph_warns = nif_convert.check_ube_nude_morph_files()
        if morph_warns:
            print("\n  !! UBE nude-skin morph check:")
            for w in morph_warns:
                print(f"     - {w}")
    except Exception:
        pass

    _enable = f"'{output.name}' + its Combined ESP(s)"
    if mnb_esp_generated:
        _enable += (" + 'UBE_ModNonBody_Coverage.esp' "
                    "(REQUIRED for helmets/non-body — see warning above)")
    if mbd_esp_generated:
        _enable += (" + 'UBE_ModBody_Coverage.esp' "
                    "(REQUIRED for mod-defined body variants — see warning above)")
    print(f"\n=== auto: done — enable {_enable} in MO2. ===")
    return rc


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
    # Discover the modpack's Data/mod dirs so the merger can read each master's
    # real ESM flag (to order ESM-flagged .esp masters like USSEP correctly).
    # Anchor on the mods root (its children are the mod folders that hold the
    # .esp masters), not the patches' folder.
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
    return 0


def _cmd_vanilla_compat(args):
    if args.data_dir:
        data_dirs = list(args.data_dir)
    elif args.reference_mod:
        data_dirs = _discover_master_data_dirs(Path(args.reference_mod))
    else:
        # Auto-discover from the output path's parent (assumes output
        # is being written into a mod folder under a modlist's mods/).
        out_parent = Path(args.output).resolve().parent
        data_dirs = _discover_master_data_dirs(out_parent)
    if not data_dirs:
        print("error: no master data dirs found. Pass --data-dir explicitly "
              "or --reference-mod with a path inside the modlist's mods/.")
        return 2
    print(f"scanning {len(data_dirs)} data dir(s) for vanilla ARMAs...")
    try:
        stats = ube_patcher.generate_vanilla_race_compat_patch(
            args.output, data_dirs,
            include_cc_masters=args.include_cc,
        )
    except FileNotFoundError as e:
        print(f"error: {e}")
        return 2
    print(f"  wrote      : {stats.get('output', args.output)}")
    print(f"  ESL flag   : {stats.get('esl_flagged', True)}")
    print(f"  masters    : {len(stats.get('masters', []))}")
    print(f"  ARMA overrides emitted: {stats.get('arma_overrides', 0)}")
    print(f"    per master:")
    for m, n in stats.get("scan_per_master", {}).items():
        print(f"      {m}: {n}")
    print(f"  skipped no_mod3        : {stats.get('skipped_no_mod3', 0)}")
    print(f"  skipped body slot      : {stats.get('skipped_body_slot', 0)}")
    print(f"  skipped already_ube    : {stats.get('skipped_already_ube', 0)}")
    print(f"  skipped unknown master : {stats.get('skipped_unknown_master_ref', 0)}")
    warnings = stats.get("validation_warnings", []) or []
    if warnings:
        print(f"  !! validator warnings:")
        for w in warnings:
            print(f"     {w}")
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
        print(f"  NIF check: SKIPPED (no meshes/ folder found)")
    else:
        print(f"  NIF check: DISABLED via --no-nifs")

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
    print(f"\n=== validation summary ===")
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
    if args.cmd == "vanilla-compat":
        return _cmd_vanilla_compat(args)
    if args.cmd == "validate":
        return _cmd_validate(args)
    if args.cmd == "gui":
        from .gui import launch_gui
        return launch_gui()
    if args.cmd == "auto":
        return _cmd_auto(args)
    if args.cmd == "convert":
        return _cmd_convert(args)
    # No subcommand → launch the GUI (the default user-facing entry when the
    # exe is double-clicked or run by MO2). The headless one-click pipeline is
    # still available explicitly as `auto` — which is also what the GUI itself
    # runs in a worker thread (main(["auto", ...])).
    from .gui import launch_gui
    return launch_gui()


if __name__ == "__main__":
    sys.exit(main())
