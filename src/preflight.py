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

"""Preflight environment checks -- run BEFORE a conversion so setup problems
surface immediately instead of after a long run (or as silent, broken results
in-game). Each check returns a pass/warn/fail verdict with a one-line fix.

Almost every real failure we see is environmental: no modlist detected, the UBE
body not built in BodySlide, texconv/Papyrus missing, or the race-compat
prerequisites absent (which makes converted armor invisible). This catches them.

The heavy probes (body refs, texconv, Papyrus) live in small wrapper functions so
the check-composition logic stays unit-testable without pynifly/a real modlist --
tests monkeypatch the `_probe_*` wrappers.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths as _paths

OK, WARN, FAIL = "ok", "warn", "fail"
OUTPUT_NAME = "CBBEtoUBE Auto"


@dataclass
class Check:
    id: str
    label: str
    status: str            # ok | warn | fail
    detail: str = ""
    fix: str = ""


def _c(cid, label, status, detail="", fix=""):
    return Check(cid, label, status, detail, fix)


# ---- probe wrappers (monkeypatched in tests) -----------------------------

def _probe_ube_body():
    try:
        from . import nif_convert, auto_convert
        return (nif_convert._find_ube_femalebody("_1")
                or auto_convert._find_ube_body_ref())
    except Exception:
        return None


def _probe_cbbe_body():
    try:
        from . import nif_convert
        return nif_convert._find_cbbe_base_body("_1")
    except Exception:
        return None


def _probe_vanilla_sweep(data_dir):
    """(ok, detail) for the vanilla armor sweep on this layout: planning
    viability (masters parse, DefaultRace ARMAs resolve) AND at least one
    planned mesh visible in the game Data dir's BSA archives — the two
    stages that depend on the least-predictable input we touch. Catches a
    broken sweep at SETUP-CHECK time instead of at run time."""
    try:
        import tempfile
        from . import auto_convert as _ac
        data_dir = Path(data_dir)
        ok, why = _ac._preflight_vanilla_sweep(data_dir)
        if not ok:
            return False, why
        bases = sorted(_ac._player_armor_mesh_bases(
            data_dir, include_candidate_slots=True))
        # Vanilla archives only: under MO2's usvfs the Data dir lists EVERY
        # enabled mod's BSAs — an unfiltered scan indexed 330 archives for a
        # 6-archive question.
        idx = _ac._BsaMeshIndex(
            [data_dir], Path(tempfile.gettempdir()) / "cbbe2ube_preflight",
            bsa_name_prefixes=["skyrim - meshes", "update", "dawnguard",
                               "hearthfires", "dragonborn"])
        sample = bases[:40]
        hit = sum(1 for b in sample
                  if any(idx.contains(f"{b}{s}.nif") for s in ("_1", "_0", "")))
        if hit == 0:
            return False, (f"{why}; but NONE of {len(sample)} sampled meshes "
                           "are visible in the game Data BSA archives "
                           "(loose replacers may still cover them)")
        return True, f"{why}; {hit}/{len(sample)} sampled meshes in vanilla BSAs"
    except Exception as e:
        return False, f"probe error: {e!r}"


def _probe_texconv():
    try:
        from . import overlay_transfer
        return overlay_transfer.find_texconv()
    except Exception:
        return None


def _probe_papyrus():
    try:
        from . import overlay_transfer
        return overlay_transfer.find_papyrus_compiler()
    except Exception:
        return None


# ---- helpers -------------------------------------------------------------

def _plugin_in_data(dirs, name) -> bool:
    for d in dirs or []:
        try:
            if (Path(d) / name).is_file():
                return True
        except OSError:
            pass
    return False


def _plugin_in_mods(mr, enabled, name) -> bool:
    """True if an enabled mod folder provides plugin `name` at its root. Modlist
    prerequisites (RaceCompatibility, UBE_AllRace) ship inside MODS -- visible via
    the MO2 VFS, not in the physical game Data folder -- so this is the reliable
    check. Case-insensitive on NTFS. Falls back to all mod folders if no profile."""
    if mr is None:
        return False
    mrp = Path(mr)
    try:
        folders = (enabled if enabled is not None
                   else [d.name for d in mrp.iterdir() if d.is_dir()])
    except OSError:
        return False
    for n in folders:
        try:
            if (mrp / n / name).is_file():
                return True
        except OSError:
            continue
    return False


def _enabled_has(enabled, *subs) -> bool:
    if not enabled:
        return False
    subs = [s.lower() for s in subs]
    for n in enabled:
        nl = str(n).lower()
        if any(s in nl for s in subs):
            return True
    return False


# ---- the checks ----------------------------------------------------------

def run_checks(layout=None, *, want_overlays=False, want_overlay_copy=False,
               output_name=OUTPUT_NAME) -> "list[Check]":
    """Return the ordered list of Checks for the current environment. Overlay
    checks (texconv/Papyrus) only run when the matching feature is requested."""
    lay = layout if layout is not None else _paths.discover_layout()
    checks: "list[Check]" = []

    mr = lay.mods_root
    if mr is None or not Path(mr).is_dir():
        checks.append(_c(
            "modlist", "Modlist detected", FAIL,
            "Couldn't find the MO2 mods folder.",
            "Run this through MO2, or set CBBE2UBE_MODS_ROOT to the mods folder."))
        return checks                       # nothing else works without a modlist
    try:
        nmods = sum(1 for d in Path(mr).iterdir() if d.is_dir())
    except OSError:
        nmods = 0
    checks.append(_c("modlist", "Modlist detected", OK,
                     f"{mr}  ({nmods} mod folders)"))

    enabled = ordered = None
    try:
        enabled = _paths.enabled_mods(lay)
        ordered = _paths.enabled_mods_ordered(lay)
    except Exception:
        pass
    if lay.selected_profile or ordered:
        checks.append(_c("profile", "Active MO2 profile", OK,
                         f"{lay.selected_profile or '?'}  "
                         f"({len(ordered or [])} enabled mods)"))
    else:
        checks.append(_c(
            "profile", "Active MO2 profile", WARN,
            "No active profile detected — every mod folder will be scanned.",
            "Launch from MO2 so the enabled/disabled state is read."))

    dd = lay.game_data_dirs or []
    checks.append(_c("gamedata", "Game data (Skyrim.esm)",
                     OK if dd else WARN,
                     str(dd[0]) if dd else "Couldn't locate the game Data folder.",
                     "" if dd else "Set CBBE2UBE_GAME_DATA, or run from the "
                     "modpack; vanilla race coverage needs it."))

    # Vanilla armor sweep viability: catches a sweep that would be disabled
    # (or silently cover nothing) BEFORE any run — game updates and layout
    # changes land here first. WARN not FAIL: mod conversion is unaffected.
    if dd:
        sw_ok, sw_detail = _probe_vanilla_sweep(dd[0])
        checks.append(_c("vanillasweep", "Vanilla armor sweep",
                         OK if sw_ok else WARN, sw_detail,
                         "" if sw_ok else "Vanilla armor no mod overrides "
                         "would stay unconverted (invisible on UBE actors). "
                         "Check the game Data path and its BSA archives."))

    ube = _probe_ube_body()
    checks.append(_c("ubebody", "UBE body reference built",
                     OK if ube else FAIL,
                     str(ube) if ube else "No BodySlide-built UBE body found.",
                     "" if ube else "Build the UBE body (BaseShape) in BodySlide "
                     "first, or set CBBE2UBE_UBE_BODY."))

    cbbe = _probe_cbbe_body()
    checks.append(_c("cbbebody", "CBBE base body",
                     OK if cbbe else WARN,
                     str(cbbe) if cbbe else "No CBBE base body found "
                     "(the source shape).",
                     "" if cbbe else "Install + build a CBBE/3BA body."))

    if want_overlays:
        tc = _probe_texconv()
        checks.append(_c("texconv", "texconv (for overlays)",
                         OK if tc else WARN,
                         str(tc) if tc else "texconv not found — overlay "
                         "conversion will be skipped.",
                         "" if tc else "Install texconv under the MO2 tools/ "
                         "folder, or set CBBE2UBE_TEXCONV."))
    if want_overlay_copy:
        pc = _probe_papyrus()
        checks.append(_c("papyrus", "Papyrus compiler (UBE copies)",
                         OK if pc else WARN,
                         str(pc) if pc else "Papyrus compiler not found — the "
                         "'UBE copies' overlay mode needs it.",
                         "" if pc else "Install the Creation Kit's "
                         "PapyrusCompiler, or turn off 'UBE copies'."))

    allrace = (_plugin_in_data(dd, "UBE_AllRace.esp")
               or _plugin_in_mods(mr, enabled, "UBE_AllRace.esp")
               or _enabled_has(enabled, "allrace newrite"))
    checks.append(_c("allrace", "UBE race coverage (UBE_AllRace)",
                     OK if allrace else WARN,
                     "found" if allrace else "UBE_AllRace / 'AllRace Newrite "
                     "Replacement' not detected.",
                     "" if allrace else "Install 'UBE_AllRace Newrite "
                     "Replacement' — without it converted armor can be invisible "
                     "on some races."))

    racecompat = (_plugin_in_data(dd, "RaceCompatibility.esm")
                  or _plugin_in_mods(mr, enabled, "RaceCompatibility.esm")
                  or _enabled_has(enabled, "racecompatibility", "race compatibility"))
    checks.append(_c("racecompat", "RaceCompatibility",
                     OK if racecompat else WARN,
                     "found" if racecompat else "RaceCompatibility not detected.",
                     "" if racecompat else "Install RaceCompatibility (the Light "
                     "build carries the RaceDispatcher) — a UBE prerequisite."))

    out_dir = Path(mr) / output_name
    if out_dir.is_dir():
        en = (enabled is None) or (output_name in enabled)
        checks.append(_c("output", "Output mod enabled",
                         OK if en else WARN,
                         f"'{output_name}' exists"
                         + (" and is enabled" if en else " but is DISABLED"),
                         "" if en else f"Enable '{output_name}' in MO2 and let it "
                         "WIN over the source mods, or its output won't load."))

    try:
        free_gb = shutil.disk_usage(str(mr)).free / (1024 ** 3)
        checks.append(_c("disk", "Free disk space",
                         OK if free_gb >= 5 else WARN,
                         f"{free_gb:.1f} GB free on the mods drive",
                         "" if free_gb >= 5 else "Low disk space — a full "
                         "conversion can write several GB."))
    except Exception:
        pass

    return checks


def overall(checks) -> str:
    """Worst status across `checks` (fail > warn > ok)."""
    if any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == WARN for c in checks):
        return WARN
    return OK
