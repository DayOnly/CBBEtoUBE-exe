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

"""Standalone vanilla body-armor -> UBE conversion (no replacer mod required).

The per-mod converter only converts a mod when that mod's OWN ESP declares a
DefaultRace armour ARMA. Base-game vanilla armor lives in Skyrim.esm/DLC and
its meshes live in the `Skyrim - Meshes*.bsa` archives (or in a loose-mesh
replacer / the user's BodySlide output) -- so vanilla BODY armor (slot 32)
never got UBE coverage unless a vanilla-replacer mod happened to be picked up
as a source. That made a replacer a hard dependency.

This module removes that dependency. It:
  1. Enumerates every vanilla/DLC slot-32 FEMALE armour mesh (MOD3) from the
     master ESMs.
  2. Resolves each mesh's bytes: a LOOSE override in the enabled mods (highest
     MO2 priority first -- so a CBBE/3BA replacer or the user's BodySlide
     output wins automatically => replacer is OPTIONAL, used if present) and,
     failing that, EXTRACTS it from the game's `Skyrim - Meshes*.bsa` (so it
     works with NO replacer and NO loose meshes at all).
  3. Stages the resolved mesh and refits it to UBE via `nif_convert.convert_nif`
     (the same pipeline modded armour uses), writing to `<output>/meshes/!UBE/`.

It returns the converted/body mesh-path sets the patcher needs; the matching
ESP records (UBE ARMA variants + master-ARMO overrides) are emitted by
`ube_patcher.generate_vanilla_body_ube_patch`, which reuses the exact
master-scan body-coverage logic the per-mod patcher already uses.

No per-armor hardcoding: armours are discovered purely by master ARMA records
that carry the body biped slot + a female mesh path.
"""
from __future__ import annotations

import struct
from pathlib import Path

from . import esp as _esp

# Biped slot 32 (body) bit in the BOD2/BODT bipedObjectSlots field
# (slot 30 = bit 0, so slot 32 = bit 2).
_BODY_SLOT_BIT = 1 << (32 - 30)

# Skyrim.esm DefaultRace FormID low 24 bits. A slot-32 ARMA bound to this is
# HUMANOID player body armour; creature/beast skin armatures (deer, draugr,
# daedra, ...) bind a creature race and are excluded by this gate.
_DEFAULT_RACE_LOW24 = 0x000019

# Nude body-skin mesh basenames (weight-stripped). These ARE slot-32
# DefaultRace ARMAs (the race's naked skin) but are NOT equippable armour --
# converting/redirecting them would replace the actor's body. The UBE races
# already supply their own nude skin.
_NUDE_SKIN_BASENAMES = frozenset({
    "femalebody", "malebody", "femalehands", "malehands",
    "femalefeet", "malefeet", "childbody",
    "1stpersonfemalebody", "1stpersonmalebody",
    "1stpersonfemalehands", "1stpersonmalehands",
})


def _is_default_race(rnam: "int | None", master_masters: "list[str]",
                     this_master: str) -> bool:
    """True if an ARMA's primary race (RNAM) is the humanoid DefaultRace.
    Resolves the race FormID's master against `this_master`'s own master
    list (Skyrim.esm's own records use top byte 0; a DLC references
    DefaultRace through its Skyrim.esm master)."""
    if rnam is None or (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24:
        return False
    top = rnam >> 24
    if this_master.lower() == "skyrim.esm":
        return top == 0
    return top < len(master_masters) and \
        master_masters[top].lower() == "skyrim.esm"


# Path fragments that mark a slot-32 DefaultRace mesh as NOT real player
# armour even though the engine treats it as worn: visual EFFECT overlays
# (word-wall burns, fx placeholders) under meshes\effects\... or .../effects/,
# and CHILDREN's clothing. Verified needed: enumeration otherwise caught
# fxemptyobject / wordburnedintochestskin (effects) and childrenclothes.
def _is_non_armor_path(mesh_rel: str) -> bool:
    s = mesh_rel.lower()
    if s.startswith("meshes/effects/") or "/effects/" in s:
        return True
    if "childrenclothes" in s or "/children/" in s:
        return True
    return False


def _is_nude_skin_basename(mesh_rel: str) -> bool:
    base = mesh_rel.rsplit("/", 1)[-1]
    if base.endswith(".nif"):
        base = base[:-4]
    if base.endswith("_0") or base.endswith("_1"):
        base = base[:-2]
    if base in _NUDE_SKIN_BASENAMES:
        return True
    # Unique-NPC body-skin variants (femalebodyastrid, femalebodyserana, ...)
    # live in character assets as slot-32 DefaultRace ARMAs but are nude skin,
    # not armour. Catch the femalebody*/malebody* prefix.
    return base.startswith(("femalebody", "malebody"))

# Master ESMs whose armours we cover. Skyrim.esm + the always-loaded DLCs.
_DEFAULT_MASTERS = ("Skyrim.esm", "Dawnguard.esm", "Dragonborn.esm")

# Vanilla SSE mesh archives, in the order the engine loads them.
_MESH_BSA_NAMES = (
    "Skyrim - Meshes0.bsa",
    "Skyrim - Meshes1.bsa",
    "Dawnguard.bsa",
    "Dragonborn.bsa",
    "HearthFires.bsa",
)


def _norm_mesh_rel(path: str) -> str:
    """Normalize an ARMA model path to a lowercase forward-slash key with a
    leading `meshes/` (BSA paths are stored with the `meshes/` prefix, while
    loose files live under a mod's `meshes/` folder)."""
    s = path.replace("\\", "/").lstrip("/").lower()
    if not s.startswith("meshes/"):
        s = "meshes/" + s
    return s


def enumerate_vanilla_body_meshes(
    master_data_dirs: "list[Path]",
    masters: "tuple[str, ...]" = _DEFAULT_MASTERS,
) -> "dict[str, str]":
    """Return {normalized_mesh_rel_path: female_basename} for every slot-32
    FEMALE armour mesh referenced by an ARMA in the given master ESMs.

    `master_data_dirs` are dirs to search for each master file (the game
    Data dir + any cleaned-masters mod). The first dir that has the file
    wins. Mesh paths are normalized with a leading `meshes/`.
    """
    def _find_master(name: str) -> "Path | None":
        for d in master_data_dirs:
            p = Path(d) / name
            if p.is_file():
                return p
        return None

    out: "dict[str, str]" = {}
    for mname in masters:
        mp = _find_master(mname)
        if mp is None:
            continue
        try:
            e = _esp.ESP.load(mp)
        except Exception:
            continue
        grp = next((g for g in e.groups if g.label == b"ARMA"), None)
        if grp is None:
            continue
        emasters = list(e.header.masters)
        for rec in grp.records:
            slots = 0
            mod3 = None
            rnam = None
            for sig, d in _esp.iter_subrecords(rec.payload):
                if sig in (b"BOD2", b"BODT") and len(d) >= 4:
                    slots = struct.unpack_from("<I", d, 0)[0]
                elif sig == b"MOD3":  # female 3rd-person model
                    mod3 = d.rstrip(b"\x00").decode("utf-8", "ignore")
                elif sig == b"RNAM" and len(d) == 4:
                    rnam = struct.unpack("<I", d)[0]
            if mod3 is None or not (slots & _BODY_SLOT_BIT):
                continue
            # HUMANOID player body armour only: bound to DefaultRace (drops
            # every creature/beast skin armature) and not a nude-skin mesh
            # (drops the race's naked body/hands/feet).
            if not _is_default_race(rnam, emasters, mname):
                continue
            rel = _norm_mesh_rel(mod3)
            if _is_nude_skin_basename(rel) or _is_non_armor_path(rel):
                continue
            out.setdefault(rel, rel.rsplit("/", 1)[-1])
    return out


class _BSAMeshPool:
    """Lazily-opened pool of the game's vanilla mesh BSAs. Resolves a
    normalized `meshes/...` path to bytes from whichever archive has it."""

    def __init__(self, game_data_dirs: "list[Path]"):
        self._dirs = [Path(d) for d in game_data_dirs]
        self._archives: list = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        from .bsa_strings import BSAArchive
        for d in self._dirs:
            for name in _MESH_BSA_NAMES:
                p = d / name
                if p.is_file():
                    try:
                        self._archives.append(BSAArchive(p))
                    except Exception:
                        continue

    def read(self, rel: str) -> "bytes | None":
        self._load()
        for a in self._archives:
            try:
                b = a.read_file(rel)
            except Exception:
                b = None
            if b:
                return b
        return None


def _resolve_loose(rel: str, enabled_mod_dirs: "list[Path]") -> "Path | None":
    """Find a loose override of `rel` (normalized `meshes/...`) in the enabled
    mods, HIGHEST PRIORITY FIRST. The first hit wins -- so a replacer / the
    user's BodySlide output overrides the base game exactly as MO2 would."""
    sub = rel  # e.g. "meshes/armor/iron/f/cuirass_1.nif"
    for mod in enabled_mod_dirs:
        cand = Path(mod) / sub
        if cand.is_file():
            return cand
    return None


def convert_vanilla_bodies(
    output_dir: "str | Path",
    master_data_dirs: "list[Path]",
    enabled_mod_dirs: "list[Path]",
    game_data_dirs: "list[Path]",
    ube_body_ref_path: "str | Path | None",
    *,
    ube_path_prefix: str = "!UBE",
    already_converted: "set[str] | None" = None,
    log=print,
) -> dict:
    """Resolve + refit every vanilla slot-32 female armour mesh to UBE.

    Returns a stats dict with:
      converted_rel_paths: set of normalized Data\\meshes-relative paths
        (no leading `meshes/`, lowercased, forward-slash) we produced at
        `!UBE\\<path>` -- feeds the patcher's `_converted_nif_exists`.
      body_mesh_rel_paths: same set (meshes the master-scan should cover).
      from_loose / from_bsa / failed / skipped: counts.

    `enabled_mod_dirs` MUST be highest-MO2-priority first.
    `already_converted` is the set of mesh rel paths real source mods already
    converted (no leading `meshes/`); those are skipped to avoid double work
    and to let a real per-mod conversion win.
    """
    output_dir = Path(output_dir)
    already = {p.replace("\\", "/").lstrip("/").lower()
               for p in (already_converted or set())}

    meshes = enumerate_vanilla_body_meshes(master_data_dirs)
    log(f"  vanilla body armour: {len(meshes)} slot-32 female mesh path(s) "
        f"in masters")

    bsa = _BSAMeshPool(game_data_dirs)
    staging = output_dir / "_vanilla_staging"
    out_meshes_root = output_dir / "meshes"

    converted: "set[str]" = set()
    from_loose = from_bsa = failed = skipped = 0
    fail_reasons: "list[str]" = []  # first few; surfaced in the log for diagnosis

    if ube_body_ref_path is None:
        log("  vanilla body armour: WARNING — no UBE body ref found; body warp "
            "is disabled, so vanilla armours can't be refit to UBE")

    # Import here so a missing pynifly doesn't break import of this module.
    from . import nif_convert as _nc

    for rel in sorted(meshes):
        # rel is "meshes/<...>"; the Data\meshes-relative key drops "meshes/".
        data_rel = rel[len("meshes/"):]
        if data_rel in already:
            skipped += 1
            continue

        # Resolve bytes: loose override first, else BSA.
        loose = _resolve_loose(rel, enabled_mod_dirs)
        staged_src = staging / data_rel
        try:
            if loose is not None:
                staged_src.parent.mkdir(parents=True, exist_ok=True)
                staged_src.write_bytes(Path(loose).read_bytes())
                from_loose += 1
            else:
                raw = bsa.read(rel)
                if not raw:
                    failed += 1
                    if len(fail_reasons) < 8:
                        fail_reasons.append(
                            f"{data_rel}: no loose override and not found in BSA")
                    continue
                staged_src.parent.mkdir(parents=True, exist_ok=True)
                staged_src.write_bytes(raw)
                from_bsa += 1
        except Exception as e:
            failed += 1
            if len(fail_reasons) < 8:
                fail_reasons.append(f"{data_rel}: resolve/stage error: {e!r}")
            continue

        dst = out_meshes_root / ube_path_prefix / data_rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _nc.convert_nif(
                staged_src, dst,
                ube_body_ref_path=ube_body_ref_path,
                biped_slots=_BODY_SLOT_BIT,
            )
            if dst.is_file():
                converted.add(data_rel.lower())
            else:
                failed += 1
                if len(fail_reasons) < 8:
                    fail_reasons.append(
                        f"{data_rel}: convert produced no output file")
        except Exception as e:
            failed += 1
            if len(fail_reasons) < 8:
                fail_reasons.append(f"{data_rel}: convert error: {e!r}")
            continue

    log(f"  vanilla body armour: converted {len(converted)} "
        f"(loose {from_loose} / bsa {from_bsa}), skipped {skipped} "
        f"(already a source), failed {failed}")
    if fail_reasons:
        log(f"  vanilla body armour: first {len(fail_reasons)} failure "
            "reason(s) (why a vanilla armour did NOT convert):")
        for fr in fail_reasons:
            log(f"     - {fr}")

    return {
        "converted_rel_paths": converted,
        "body_mesh_rel_paths": set(converted),
        "from_loose": from_loose,
        "from_bsa": from_bsa,
        "failed": failed,
        "skipped": skipped,
        "enumerated": len(meshes),
        "staging": staging,
    }
