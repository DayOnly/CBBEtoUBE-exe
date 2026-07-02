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

"""Runtime auto-discovery of the MO2 modpack layout.

The converter ships as a standalone tool (a frozen .exe registered as an MO2
executable) that must work in ANY modpack with NO hardcoded machine- or
mod-specific paths. This module figures out, at runtime, where everything
lives by anchoring on the MO2 instance:

  * the MO2 instance directory   (contains ModOrganizer.ini)
  * the mods/ root               (mod_directory override, or <base>/mods)
  * the game Data folder(s)       (gamePath\\Data, + Stock Game / Game Root)

Everything else (CBBE base body, UBE body + OSD, and the CBBE/3BA armor
mods to convert) is then discovered by scanning the mods root by CONTENT /
heuristic — never by a hardcoded mod name.

Discovery anchors, in order:
  1. CBBE2UBE_MODS_ROOT env var (explicit override; also how the main process
     hands the resolved root to spawned worker processes).
  2. CBBE2UBE_MO2_INI env var pointing straight at a ModOrganizer.ini.
  3. Walk upward from the running binary (frozen .exe) / this file / CWD
     looking for a ModOrganizer.ini or a directory literally named "mods".
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


MODS_ROOT_ENV = "CBBE2UBE_MODS_ROOT"
MO2_INI_ENV = "CBBE2UBE_MO2_INI"
GAME_DATA_ENV = "CBBE2UBE_GAME_DATA"


def is_within_dir(base, target) -> bool:
    """True if `target` resolves to a path inside `base` (or equals it).

    SECURITY: defends against path traversal from UNTRUSTED mod-supplied strings
    (BSA internal names, ARMA model paths, texture paths). A `..\\..\\` sequence
    or an absolute path that escapes `base` returns False, so the caller can skip
    the write instead of clobbering a file outside the output sandbox.
    `resolve()` normalizes `..` lexically even for not-yet-existing paths."""
    try:
        base_r = Path(base).resolve()
        target_r = Path(target).resolve()
    except Exception:
        return False
    return target_r == base_r or base_r in target_r.parents


# --------------------------------------------------------------------------
# ModOrganizer.ini parsing
# --------------------------------------------------------------------------

def _unwrap_ini_value(raw: str) -> str:
    """Strip MO2's `@ByteArray(...)` wrapper and unescape `\\\\` -> `\\`."""
    v = raw.strip()
    if v.startswith("@ByteArray(") and v.endswith(")"):
        v = v[len("@ByteArray("):-1]
    # MO2 stores Windows paths with doubled backslashes in the INI.
    v = v.replace("\\\\", "\\")
    return v.strip()


def parse_mo2_ini(ini_path: Path) -> dict:
    """Minimal flat parse of ModOrganizer.ini — last value per key wins.
    Returns a dict of the keys we care about (gamePath, base_directory,
    mod_directory, gameName, selected_profile) plus the ini's own dir."""
    out: dict = {"_ini_dir": ini_path.parent}
    try:
        text = ini_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    wanted = {
        "gamepath", "gamename", "base_directory", "mod_directory",
        "selected_profile",
    }
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "[")):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        k = key.strip().lower()
        if k in wanted:
            out[k] = _unwrap_ini_value(val)
    return out


def _expand(base_dir: Path, value: str) -> Path:
    """Resolve an MO2 INI path value, expanding %BASE_DIR% and making
    relative values relative to the instance dir."""
    value = value.replace("%BASE_DIR%", str(base_dir))
    p = Path(value)
    if not p.is_absolute():
        p = base_dir / p
    return p


# --------------------------------------------------------------------------
# Anchor search
# --------------------------------------------------------------------------

def _anchor_dirs() -> list[Path]:
    """Candidate starting directories to walk upward from, in priority order:
    the frozen exe dir, this module's dir, and the CWD."""
    cands: list[Path] = []
    try:
        if getattr(sys, "frozen", False):
            cands.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    try:
        cands.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    try:
        cands.append(Path.cwd())
    except Exception:
        pass
    # de-dup, preserve order
    seen: set[Path] = set()
    out: list[Path] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def find_mo2_ini(start: Path | None = None) -> Path | None:
    """Walk upward from `start` (or the default anchors) to find a
    ModOrganizer.ini. Honors the CBBE2UBE_MO2_INI override first."""
    env_ini = os.environ.get(MO2_INI_ENV)
    if env_ini:
        p = Path(env_ini)
        if p.is_file():
            return p
    starts = [start] if start is not None else _anchor_dirs()
    for s in starts:
        if s is None:
            continue
        d = s.resolve()
        for _ in range(8):  # bounded upward walk
            ini = d / "ModOrganizer.ini"
            if ini.is_file():
                return ini
            if d.parent == d:
                break
            d = d.parent
    return None


def _find_mods_root_by_name(start: Path | None = None) -> Path | None:
    """Fallback when no ModOrganizer.ini is found: walk upward looking for a
    directory literally named `mods` that contains subfolders."""
    starts = [start] if start is not None else _anchor_dirs()
    for s in starts:
        if s is None:
            continue
        d = s.resolve()
        for _ in range(8):
            if d.name.lower() == "mods" and d.is_dir():
                return d
            cand = d / "mods"
            if cand.is_dir():
                return cand
            if d.parent == d:
                break
            d = d.parent
    return None


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

@dataclass
class Layout:
    mods_root: Path | None = None
    game_data_dirs: list[Path] = field(default_factory=list)
    instance_dir: Path | None = None
    game_path: Path | None = None
    selected_profile: str | None = None

    def ok(self) -> bool:
        return self.mods_root is not None and self.mods_root.is_dir()


def discover_layout(start: Path | None = None) -> Layout:
    """Resolve the modpack layout. Order: explicit env var > ModOrganizer.ini
    > a directory named `mods` found by walking up. Always returns a Layout
    (possibly empty — check .ok())."""
    lay = Layout()

    # 1. Explicit mods-root override (also the worker-process handoff path).
    env_root = os.environ.get(MODS_ROOT_ENV)
    if env_root and Path(env_root).is_dir():
        lay.mods_root = Path(env_root)

    # 2. ModOrganizer.ini.
    ini_path = find_mo2_ini(start)
    if ini_path is not None:
        cfg = parse_mo2_ini(ini_path)
        lay.instance_dir = cfg.get("_ini_dir")
        base_dir = (Path(cfg["base_directory"]) if cfg.get("base_directory")
                    else cfg["_ini_dir"])
        lay.selected_profile = cfg.get("selected_profile")
        if lay.mods_root is None:
            if cfg.get("mod_directory"):
                lay.mods_root = _expand(base_dir, cfg["mod_directory"])
            else:
                lay.mods_root = base_dir / "mods"
        gp = cfg.get("gamepath")
        if gp:
            lay.game_path = Path(gp)

    # 3. Name-based fallback for mods root.
    if lay.mods_root is None or not lay.mods_root.is_dir():
        nm = _find_mods_root_by_name(start)
        if nm is not None:
            lay.mods_root = nm

    # ---- game Data dirs ----
    lay.game_data_dirs = _discover_game_data_dirs(lay)
    return lay


def _discover_game_data_dirs(lay: Layout) -> list[Path]:
    """Game Data folder(s): explicit env var, then gamePath\\Data, then the
    Wabbajack 'Stock Game'/'Game Root' siblings next to the instance, then
    any sibling of mods/ that has Skyrim.esm."""
    out: list[Path] = []

    def _add(p: Path | None):
        if p is not None and p.is_dir() and (p / "Skyrim.esm").is_file():
            rp = p.resolve()
            if rp not in [o.resolve() for o in out]:
                out.append(p)

    env_data = os.environ.get(GAME_DATA_ENV)
    if env_data:
        for chunk in env_data.split(os.pathsep):
            if chunk:
                _add(Path(chunk))

    if lay.game_path is not None:
        _add(lay.game_path / "Data")
        _add(lay.game_path)  # some setups point gamePath straight at Data

    # Wabbajack 'Stock Game' / 'Game Root' next to the instance.
    for base in filter(None, [lay.instance_dir,
                              lay.mods_root.parent if lay.mods_root else None]):
        for sub in ("Stock Game/Data", "Game Root/Data", "Stock Folder/Data",
                    "Game/Data"):
            _add(base / sub)
    return out


# --------------------------------------------------------------------------
# Worker-process handoff
# --------------------------------------------------------------------------

def export_to_env(lay: Layout) -> None:
    """Write the resolved layout into os.environ so spawned worker processes
    (which re-import these modules fresh) inherit it without re-discovering."""
    if lay.mods_root is not None:
        os.environ[MODS_ROOT_ENV] = str(lay.mods_root)
    if lay.game_data_dirs:
        os.environ[GAME_DATA_ENV] = os.pathsep.join(
            str(d) for d in lay.game_data_dirs)


def mods_root() -> Path | None:
    """Cheap accessor used by deep code (incl. workers): the resolved mods
    root from the env var, or a fresh discovery if unset."""
    env_root = os.environ.get(MODS_ROOT_ENV)
    if env_root and Path(env_root).is_dir():
        return Path(env_root)
    lay = discover_layout()
    return lay.mods_root


def enabled_mods_ordered(lay: "Layout") -> "list[str] | None":
    """Return mod names ENABLED in the active MO2 profile, IN PRIORITY ORDER
    (highest priority first), read from `profiles/<selected_profile>/
    modlist.txt` (a `+Name` line = enabled, `-Name` = disabled, `*...` =
    separator). MO2 writes modlist.txt top-to-bottom = highest-to-lowest
    priority (matching the GUI), so the first name returned is the conflict
    WINNER in the game's load order.

    Order matters for conversion: when two source mods ship the same mesh
    path, the higher-priority one (earlier here) is what the game actually
    loads, so it's the one we should convert. Returns None if no modlist.txt
    can be located (caller then has no priority info)."""
    if lay.instance_dir is None:
        return None
    prof = lay.selected_profile
    candidates = []
    if prof:
        candidates.append(lay.instance_dir / "profiles" / prof / "modlist.txt")
    # Fall back to any single profile if the selected one isn't found.
    pdir = lay.instance_dir / "profiles"
    if pdir.is_dir():
        try:
            for d in sorted(pdir.iterdir()):
                ml = d / "modlist.txt"
                if ml.is_file() and ml not in candidates:
                    candidates.append(ml)
        except OSError:
            pass
    for ml in candidates:
        if not ml.is_file():
            continue
        try:
            out: list[str] = []
            for line in ml.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                line = line.rstrip("\n")
                if line.startswith("+"):
                    out.append(line[1:].strip())
            if out:
                return out
        except Exception:
            continue
    return None


def enabled_mods(lay: "Layout") -> "set[str] | None":
    """Set of mod names ENABLED in the active MO2 profile — used for fast
    membership tests (is this mod turned on?). For the priority-ordered list
    used to resolve same-path conflicts, use `enabled_mods_ordered`. Returns
    None if the modlist can't be located (caller then converts all discovered
    armor mods)."""
    ordered = enabled_mods_ordered(lay)
    return set(ordered) if ordered is not None else None


def active_plugins_ordered(lay: "Layout") -> "list[str] | None":
    """Return LOADED plugin filenames (e.g. 'Skyrim.esm', 'SomeMod.esp') IN
    LOAD ORDER (ascending = last plugin is the conflict WINNER). Used by the
    winner-aware ARMO rebase and the UBE coverage passes.

    Source = `profiles/<selected_profile>/`. A plugin counts as LOADED iff it
    is `*`-active in plugins.txt OR present in loadorder.txt but absent from
    plugins.txt (implicit always-load CC/ESL). A name in plugins.txt without
    `*` is explicitly DEACTIVATED and excluded. Returns None if the profile
    lists can't be located."""
    if lay.instance_dir is None:
        return None
    prof_dirs: "list[Path]" = []
    if lay.selected_profile:
        prof_dirs.append(lay.instance_dir / "profiles" / lay.selected_profile)
    pdir = lay.instance_dir / "profiles"
    if pdir.is_dir():
        try:
            for d in sorted(pdir.iterdir()):
                if d.is_dir() and d not in prof_dirs:
                    prof_dirs.append(d)
        except OSError:
            pass
    for prof in prof_dirs:
        plugins_txt = prof / "plugins.txt"
        loadorder_txt = prof / "loadorder.txt"
        if not plugins_txt.is_file():
            continue
        try:
            active: "set[str]" = set()    # *-marked (explicitly enabled)
            listed: "set[str]" = set()    # every name appearing in plugins.txt
            star_order: "list[str]" = []  # *-marked, in plugins.txt order
            for line in plugins_txt.read_text(
                    encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("*"):
                    nm = line[1:].strip()
                    active.add(nm.lower()); listed.add(nm.lower())
                    star_order.append(nm)
                else:
                    listed.add(line.lower())
            # loadorder.txt includes implicit CC/always-load ESLs that plugins.txt
            # omits; prefer it for the ordered set.
            if loadorder_txt.is_file():
                ordered: "list[str]" = []
                for line in loadorder_txt.read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    low = line.lower()
                    if low in active or low not in listed:
                        ordered.append(line)
                if ordered:
                    return ordered
            if star_order:
                return star_order
        except Exception:
            continue
    return None


def plugin_file_index(lay: "Layout") -> "dict[str, Path]":
    """Map lowercased plugin filename -> on-disk path. First-seen wins, with mods
    scanned before game Data and overwrite.

    Resolving a plugin filename to a physical file is a VFS/mod-PRIORITY question:
    when the same plugin filename ships in two mods (a mod + its patch/update as
    separate MO2 mods), the game loads the highest-priority mod's copy. So the
    mods root is walked in MO2 PRIORITY order (highest first) -- NOT arbitrary
    os.walk (alphabetical-by-mod-folder) order, which could map the name to a
    lower-priority LOSER's copy and make the winner scan read the wrong plugin's
    records. Disabled / unlisted mod folders are indexed LAST (lowest priority)
    so nothing that used to be found is dropped. Used to resolve
    `active_plugins_ordered` names to files for the winner scan. #plugin-priority"""
    index: dict[str, Path] = {}

    def _index_dir(r: "Path | None") -> None:
        if not r or not r.is_dir():
            return
        for root, _dirs, files in os.walk(r):
            for f in files:
                fl = f.lower()
                if fl.endswith((".esp", ".esm", ".esl")) and fl not in index:
                    index[fl] = Path(root) / f

    # 1. Mods root FIRST, in MO2 priority order (highest first) so duplicate
    #    plugin filenames resolve to the load-order winner. #plugin-priority
    if lay.mods_root is not None and lay.mods_root.is_dir():
        order = enabled_mods_ordered(lay)   # highest priority first, or None
        seen_dirs: set[str] = set()
        mod_dirs: list[Path] = []
        if order:
            for name in order:
                d = lay.mods_root / name
                if d.is_dir():
                    mod_dirs.append(d)
                    seen_dirs.add(name.lower())
        # Remaining mod folders (disabled / not in the modlist) LAST, lowest
        # priority. Sorted for a deterministic result when there's no modlist.
        try:
            for d in sorted(p for p in lay.mods_root.iterdir() if p.is_dir()):
                if d.name.lower() not in seen_dirs:
                    mod_dirs.append(d)
        except OSError:
            pass
        for md in mod_dirs:
            _index_dir(md)
        # Plugin files sitting DIRECTLY in the mods root (no mod folder) --
        # unusual, but the old os.walk(mods_root) indexed them, so keep parity.
        try:
            for f in lay.mods_root.iterdir():
                if f.is_file():
                    fl = f.name.lower()
                    if (fl.endswith((".esp", ".esm", ".esl"))
                            and fl not in index):
                        index[fl] = f
        except OSError:
            pass

    # 2. Game Data dir(s), then overwrite (mods already won via first-seen).
    for r in (lay.game_data_dirs or []):
        _index_dir(r)
    if lay.mods_root is not None:
        _index_dir(lay.mods_root.parent / "overwrite")
    return index
