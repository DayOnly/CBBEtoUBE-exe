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

"""Generate a UBE patch ESP from a CBBE source armor ESP.

Mirrors the structural pattern reverse-engineered from hand-authored UBE armors
and a vampire armor mod UBE conversions:

  For each ARMA in the source CBBE armor ESP:
    - Create a NEW ARMA in the patch with:
      * EDID = source_EDID + "_UBE"
      * Primary RNAM = UBE_BretonRace
      * Additional Races = all 15 other UBE race variants
      * MOD2/MOD3/MOD4/MOD5 paths prepended with "!UBE\\"
      * All other subrecords (OBND, BOD2, DNAM, textures, etc.) copied verbatim

  For each ARMO whose Armatures list references a source ARMA:
    - Create an override in the patch
    - Append the corresponding new ARMA's FormID to the Armatures list

Output masters: Skyrim.esm, [Dawnguard.esm if needed], UBE_AllRace.esp,
                <source mod>.esp
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable

from . import esp


# --------------------------------------------------------------------------
# Batch caches (main process only)
#
# generate_ube_patch runs once PER SOURCE MOD. Three pieces of work inside it
# depend only on the master_data_dirs (identical for every mod in a batch),
# so without caching they were recomputed dozens of times — the dominant
# wall-clock cost of a big run. These caches make that work happen once.
#   * _MASTER_ESP_CACHE: parsed master ESMs (Skyrim.esm is 250MB; it was
#     re-parsed ~2x per mod). Used READ-ONLY (we copy record payloads to build
#     overrides; we never mutate the loaded master), so sharing one parse is
#     safe. Keyed by absolute path string.
#   * _UBE_RACES_CACHE: result of _discover_ube_races (walks every plugin in
#     the data dirs — thousands on a big modlist). Pure function of the dirs.
#   * _STRING_RESOLVER_CACHE: the STRINGS resolver per data dir (read-only).
# Workers never touch these (the ESP-patch step is main-process only), so a
# plain module-level dict is fine. clear_batch_caches() resets them.
# --------------------------------------------------------------------------
_MASTER_ESP_CACHE: "dict[str, esp.ESP]" = {}
_UBE_RACES_CACHE: "dict[tuple, list]" = {}
_STRING_RESOLVER_CACHE: dict = {}


def clear_batch_caches() -> None:
    _MASTER_ESP_CACHE.clear()
    _UBE_RACES_CACHE.clear()
    _STRING_RESOLVER_CACHE.clear()


def _load_master_cached(master_path: Path) -> "esp.ESP":
    """Parse a master ESM once per batch and reuse it (read-only)."""
    key = str(master_path)
    e = _MASTER_ESP_CACHE.get(key)
    if e is None:
        e = esp.ESP.load(master_path)
        _MASTER_ESP_CACHE[key] = e
    return e


# UBE race FormIDs as they appear in UBE_AllRace.esp's own master space
# (top byte 0x03 — UBE_AllRace has 3 masters: Skyrim.esm, Update.esm, Dawnguard.esm)
# All entries are 24-bit FormIDs (bottom 3 bytes); we add the master byte at codegen.
UBE_RACE_FIDS_24 = [
    0x005734,  # UBE_BretonRace          [chosen as primary]
    0x005735,  # UBE_BretonRaceVampire
    0x05A179,  # UBE_ImperialRace
    0x05A17A,  # UBE_ImperialRaceVampire
    0x05A184,  # UBE_NordRace
    0x05A185,  # UBE_NordRaceVampire
    0x05A18E,  # UBE_RedguardRace
    0x05A18F,  # UBE_RedguardRaceVampire
    0x05A198,  # UBE_DarkElfRace
    0x05A199,  # UBE_DarkElfRaceVampire
    0x05A1A2,  # UBE_HighElfRace
    0x05A1A3,  # UBE_HighElfRaceVampire
    0x05A1AC,  # UBE_WoodElfRace
    0x05A1AD,  # UBE_WoodElfRaceVampire
    0x05A1B0,  # UBE_OrcRace
    0x05A1B1,  # UBE_OrcRaceVampire
]
UBE_PRIMARY_BRETON_FID_24 = 0x005734


# ARMA model-path subrecord signatures (the ones to prefix with "!UBE\")
ARMA_MODEL_SIGS = (b"MOD2", b"MOD3", b"MOD4", b"MOD5")

# Texture-hash subrecords that follow each model (MOD2->MO2T, etc.). In SSE
# these are: u32 version, u32 count, u32 unknown, then count * 12-byte entries
# {u32 fileHash, char[4] ext, u32 folderHash} => total len == 12 * (1 + count).
ARMA_MODT_SIGS = (b"MO2T", b"MO3T", b"MO4T", b"MO5T")

# A valid empty MODT (version=2, count=0). The hand-authored gold UBE armatures
# all use this; the texture hashes have no runtime rendering effect (the game
# reads textures from the NIF), so an empty block is always safe.
_EMPTY_MODT = struct.pack("<III", 2, 0, 0)


def normalize_modt(data: bytes) -> bytes:
    """Return a structurally valid SSE MODT.

    Some old/LE-ported source mods (Lass Armor, LunarGuard, New Leather,
    Savior's Hide) ship ARMA models whose MO?T block is HEADERLESS — a raw
    sequence of 12-byte texture entries with no (version,count,unknown)
    header. Copied verbatim into our SSE-form-version records, the engine's
    header-based parser reads the count from offset 4, which lands on the
    first entry's "dds\\0" extension bytes (0x00736464 = 7,562,340). It then
    tries to read 7.5M entries from a 24-48 byte buffer -> massive overread ->
    EXCEPTION_ACCESS_VIOLATION at form/model init (deterministic startup CTD).

    A well-formed MODT satisfies `len == 12 * (1 + count@4)`. If it doesn't,
    replace it with the empty placeholder (matches the gold standard)."""
    if len(data) >= 12:
        count = struct.unpack_from("<I", data, 4)[0]
        if len(data) == 12 * (1 + count):
            return data
    return _EMPTY_MODT


# Vanilla Skyrim DLC master ESMs in canonical load order. Included
# unconditionally in our patches' master lists — every Skyrim install
# loads these and they serve as transitive masters for nearly every
# source mod's records. Including them defensively prevents the
# "FormID misroute through wrong master" startup crash class.
VANILLA_DLC_MASTERS = (
    "Skyrim.esm", "Update.esm", "Dawnguard.esm",
    "HearthFires.esm", "Dragonborn.esm",
)


# ARMA subrecord that stores the additional-race FormIDs (one per occurrence).
# Confusingly named MODL — same signature as the "model path" string in ARMO,
# but in ARMA records MODL holds a 4-byte FormID.
ARMA_ADDITIONAL_RACE_SIG = b"MODL"


# Subrecords whose payload is a single 4-byte FormID. Used by the master-prune
# pass to know what to renumber. Conservative set: ARMA + ARMO refs we know we
# carry forward. If we ever copy in other record types we should extend this.
FORMID_SINGLE_SUBRECORD_SIGS = {
    # ARMA
    b"RNAM",   # primary race
    b"MODL",   # additional race (in ARMA) / armature ref (in ARMO)
    b"SNDD",   # footstep sound set
    # ARMO
    b"ZNAM",   # pickup sound
    b"YNAM",   # putdown sound
    b"ETYP",   # equip slot
    b"BIDS",   # block bash impact data
    b"BAMT",   # alt block material
    b"TNAM",   # template armor
    b"EITM",   # enchantment
    b"EAMT",   # enchantment amount? (actually u16 — but never a FormID, exclude)
}
# strike EAMT (it's a u16, not a FormID)
FORMID_SINGLE_SUBRECORD_SIGS.discard(b"EAMT")

# Subrecords whose payload is an array of 4-byte FormIDs.
FORMID_ARRAY_SUBRECORD_SIGS = {
    b"KWDA",   # keyword array
}


# ----- FormID remapping ---------------------------------------------------

def make_master_byte(masters: list[str], master_name: str) -> int:
    """Return the master-index byte for `master_name` in `masters`. Used as
    the top byte of FormIDs in the output ESP."""
    for i, m in enumerate(masters):
        if m.lower() == master_name.lower():
            return i
    raise KeyError(f"master {master_name!r} not in {masters}")


def fid_from(masters: list[str], master_name: str, low24: int) -> int:
    return (make_master_byte(masters, master_name) << 24) | (low24 & 0xFFFFFF)


def _add_master_if_missing(masters: list[str], name: str) -> bool:
    """Append `name` to `masters` if no case-insensitive match exists.
    Returns True if added, False if already present. Used to build the
    patch's master list deterministically without duplicates."""
    low = name.lower()
    for m in masters:
        if m.lower() == low:
            return False
    masters.append(name)
    return True


def remap_fid(fid: int, src_masters: list[str], src_filename: str,
              dst_masters: list[str]) -> int:
    """Convert a FormID from the source ESP's master space to the patch ESP's.

    src_filename is the source ESP filename — needed to remap FormIDs whose
    top byte == len(src_masters) (i.e. records owned by the source itself).
    """
    src_top = (fid >> 24) & 0xFF
    low24 = fid & 0xFFFFFF
    if src_top < len(src_masters):
        master_name = src_masters[src_top]
    else:
        # records owned by the source ESP itself
        master_name = src_filename
    return fid_from(dst_masters, master_name, low24)


# ----- ARMA payload mutation ----------------------------------------------

# ARMO/ARMA alternate-texture subrecords (MO2S/MO3S/MO4S/MO5S) contain
# embedded TXST FormID references. Format:
#   count (u32) +
#   N * { name_len (u32) + name (N bytes) + TXST_FormID (u32) + index (u32) }
# These FormIDs need master-byte translation just like FormIDs in any
# FORMID_SINGLE_SUBRECORD_SIGS subrecord, but they're embedded inside
# the variable-length MO?S payload so the standard remap doesn't reach
# them. Without remap, color-variant ARMOs (a multi-piece armor Cape_Red,
# Cape_Blue, etc.) all reference the SAME wrong master's record and
# render with the same default texture.
ALT_TEXTURE_SIGS = (b"MO2S", b"MO3S", b"MO4S", b"MO5S")


def _reindex_alt_texture_payload(data: bytes,
                                 shape_index: "dict[str, int]") -> "bytes | None":
    """Rewrite an MO?S alt-texture set to match a CONVERTED NIF's actual
    shapes. Each entry is (3D name, TXST FormID, 3D index); the engine applies
    the TXST to the geometry at that 3D INDEX. After conversion merges/reorders
    shapes, the source's names+indices are stale, so variants recolor the wrong
    shapes (or out-of-range indices = no effect). For each entry:
      * if its name still exists in the NIF -> keep it, set index to the NIF's
        real index for that name;
      * if its name was merged away -> DROP it (its geometry now lives inside a
        surviving shape that keeps its own entry — and within a uniform variant
        every piece takes the same TXST, so the survivor carries the colour);
      * de-dupe by name (one entry per surviving shape).
    `shape_index` = {shape_name: index} from the converted NIF. Returns the
    rebuilt payload, or None on parse failure (caller keeps the original)."""
    try:
        n = struct.unpack_from("<I", data, 0)[0]
        p = 4
        entries = []
        for _ in range(n):
            nl = struct.unpack_from("<I", data, p)[0]; p += 4
            name = data[p:p + nl]; p += nl
            txst = struct.unpack_from("<I", data, p)[0]; p += 4
            _idx = struct.unpack_from("<I", data, p)[0]; p += 4
            entries.append((name, txst))
    except Exception:
        return None
    seen: set[str] = set()
    kept = []
    for name, txst in entries:
        nm = name.split(b"\x00", 1)[0].decode("latin-1", "ignore")
        new_idx = shape_index.get(nm)
        if new_idx is None or nm in seen:
            continue  # shape merged away / duplicate
        seen.add(nm)
        kept.append((name, txst, new_idx))
    out = struct.pack("<I", len(kept))
    for name, txst, idx in kept:
        out += struct.pack("<I", len(name)) + name + struct.pack("<II", txst, idx)
    return out


def reconcile_alt_texture_indices(esp_path, meshes_root) -> int:
    """Post-conversion pass: fix stale alt-texture (MO2S/MO3S/MO4S/MO5S) 3D
    names+indices in an output ESP so color variants apply to the right shapes.

    WHY: the converter merges/reorders a NIF's shapes (e.g. the morph-cap
    merge collapses 17 shapes -> 9), but the ESP's alt-texture sets still carry
    the SOURCE NIF's per-piece names and indices. The engine applies alt-texture
    TXSTs by 3D index, so the stale indices recolor the wrong shapes or fall
    out of range (no effect) -> "color variant shows wrong/base colour". This
    reloads each ARMA's converted model NIF (MO2S->MOD2, MO3S->MOD3, etc.) and
    rewrites the alt-texture set to the surviving shapes' real names+indices.
    Returns number of ARMA records fixed. Run AFTER NIF conversion + merge."""
    from pathlib import Path as _Path
    from . import nif_io
    meshes_root = _Path(meshes_root)
    e = esp.ESP.load(esp_path)
    _cache: "dict[str, dict | None]" = {}

    def shapes_for(model_path: str):
        key = model_path.lower()
        if key in _cache:
            return _cache[key]
        idx = None
        try:
            p = meshes_root / model_path.replace("/", "\\")
            if p.is_file():
                nf = nif_io.load_nif(p)
                idx = {s.name: i for i, s in enumerate(nf.shapes)}
        except Exception:
            idx = None
        _cache[key] = idx
        return idx

    SLOT_FOR = {b"MO2S": b"MOD2", b"MO3S": b"MOD3",
                b"MO4S": b"MOD4", b"MO5S": b"MOD5"}
    fixed = 0
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            subs = list(esp.iter_subrecords(r.payload))
            if not any(sig in SLOT_FOR for sig, _ in subs):
                continue
            models: dict[bytes, str] = {}
            for sig, data in subs:
                if sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
                    models[sig] = data.rstrip(b"\x00").decode("latin-1", "ignore")
            changed = False
            new_payload = b""
            for sig, data in subs:
                if sig in SLOT_FOR:
                    mdl = models.get(SLOT_FOR[sig])
                    idxmap = shapes_for(mdl) if mdl else None
                    if idxmap is not None:
                        rebuilt = _reindex_alt_texture_payload(data, idxmap)
                        if rebuilt is not None and rebuilt != data:
                            new_payload += esp.encode_subrecord(sig, rebuilt)
                            changed = True
                            continue
                new_payload += esp.encode_subrecord(sig, data)
            if changed:
                r.payload = new_payload
                fixed += 1
    if fixed:
        e.save(esp_path)
    return fixed


def _remap_alt_texture_payload(data: bytes,
                               remap_fid: "callable[[int], int]") -> bytes:
    """Walk an MO?S subrecord, applying `remap_fid` to each embedded
    TXST FormID, returning the rebuilt payload. Returns data unchanged
    on any parse error.
    """
    if len(data) < 4:
        return data
    try:
        n = struct.unpack_from("<I", data, 0)[0]
        out = struct.pack("<I", n)
        p = 4
        for _ in range(n):
            if p + 4 > len(data):
                return data  # truncated
            name_len = struct.unpack_from("<I", data, p)[0]; p += 4
            if p + name_len + 8 > len(data):
                return data  # truncated
            name = data[p:p + name_len]; p += name_len
            txst_fid = struct.unpack_from("<I", data, p)[0]; p += 4
            index = struct.unpack_from("<I", data, p)[0]; p += 4
            new_fid = remap_fid(txst_fid)
            out += (struct.pack("<I", name_len) + name
                    + struct.pack("<II", new_fid, index))
        if p != len(data):
            # Trailing bytes — preserve them rather than truncate.
            out += data[p:]
        return out
    except Exception:
        return data


def collect_alt_texture_shape_names(esp_paths) -> "set[str]":
    """Scan ESP(s) for every shape NAME targeted by an ARMO/ARMA alt-texture
    set (MO?S 3D-name field). Historically used to protect these shapes from
    the (now-retired) cloth merge so color variants kept working; still kept
    around because `reconcile_alt_texture_indices` (below) uses the same name
    set to repair stale MO?S indices after pynifly's NIF copy reorders shapes.
    General: collects whatever the source author named, no per-armor logic.
    """
    names: set[str] = set()
    for path in esp_paths:
        try:
            e = esp.ESP.load_cached(path)  # read-only scan -> cached parse
        except Exception:
            continue
        for g in e.groups:
            if g.label not in (b"ARMO", b"ARMA"):
                continue
            for rec in g.records:
                for sig, data in esp.iter_subrecords(rec.payload):
                    if sig not in ALT_TEXTURE_SIGS or len(data) < 4:
                        continue
                    try:
                        n = struct.unpack_from("<I", data, 0)[0]
                        p = 4
                        for _ in range(n):
                            if p + 4 > len(data):
                                break
                            nl = struct.unpack_from("<I", data, p)[0]
                            p += 4
                            if p + nl + 8 > len(data):
                                break
                            nm = data[p:p + nl].split(b"\x00", 1)[0].decode(
                                "latin-1", "ignore")
                            p += nl + 8
                            if nm:
                                names.add(nm)
                    except Exception:
                        continue
    return names


def _force_female_priority(dnam: bytes) -> bytes:
    """ARMA DNAM byte 0 = Male Priority, byte 1 = Female Priority. UBE is a
    female-only body, so ensure the FEMALE priority is at least the male's (and
    non-zero) -- a 'gender not listed' / male-only armature then actually renders
    on a female actor instead of being deprioritised to nothing. Every other
    DNAM field (weight-slider flags, detection sound, weapon adjust) is left
    untouched. #UBE-female-only-policy."""
    if len(dnam) < 2:
        return dnam
    b = bytearray(dnam)
    b[1] = max(b[1], b[0], 1)
    return bytes(b)


def rebuild_arma_payload(source_payload: bytes, *,
                         new_primary_rnam: int,
                         new_additional_race_fids: Iterable[int],
                         path_prefix: str = "!UBE\\",
                         alt_texture_fid_remap: "callable[[int], int] | None" = None,
                         converted_nif_exists: "callable[[str], bool] | None" = None,
                         ensure_female: bool = True) -> bytes:
    """Take a source ARMA payload and produce the UBE-targeted variant.

    Modifications:
      - RNAM subrecord: replace with new_primary_rnam (4 bytes)
      - All MODL subrecords (which in ARMA are additional-race FormIDs):
        remove them, then append new ones for each new_additional_race_fids
      - MOD2/MOD3/MOD4/MOD5: prepend `path_prefix` to the path string ONLY if
        the converted mesh actually exists (see converted_nif_exists). If we
        DIDN'T convert that mesh (e.g. the mod overrides a vanilla ARMA but
        references a vanilla mesh it doesn't ship, or a male model we never
        touch), prefixing would point the ARMA at a non-existent `!UBE\\` NIF,
        which CRASHES the game on load. In that case keep the original path so
        the real (vanilla) mesh loads instead.
      - Everything else: copy verbatim

    `converted_nif_exists(model_path)`: predicate returning True if a converted
    NIF exists at `<output>/meshes/<path_prefix><model_path>`. None => always
    prefix (legacy behavior, for callers without output context).

    EDID is left untouched here (the caller can post-process).
    """
    out = b""
    # Canonical ARMA subrecord order (what the CK emits and the engine's
    # gold-standard UBE armatures use) is:
    #   EDID, BOD2/BODT, RNAM, DNAM, <models: MOD2/MO2T/MOD3/MO3T/MOD4.../NAM0-3>,
    #   <additional races: MODL...>, SNDD (footstep sound), ONAM (art object)
    # i.e. the additional-race MODL block comes AFTER the models but BEFORE
    # SNDD/ONAM. The previous code appended the MODL block at the very END,
    # so on armatures that carry an SNDD (boots/feet) the layout became
    # ...MOD3, MO3T, SNDD, MODL(races) — SNDD landing BEFORE the race list.
    # That non-canonical order crashed the engine on load for exactly those
    # SNDD-bearing armatures (every crash across attempts landed on a boots
    # ARMA). Same bug class as the ARMO MODL-after-DATA fix. We therefore
    # DEFER SNDD/ONAM into `trailing` and emit them after the MODL block so
    # our output matches the gold-standard UBE_AllRace.esp armatures byte-for
    # -byte in ordering.
    trailing = b""
    saw_mod3 = saw_mod5 = False
    conv_mod2 = conv_mod4 = None   # converted (!UBE) male model paths, if produced
    skip_mo3t = skip_mo5t = False  # drop a female texture-hash we redirected past
    for sig, data in esp.iter_subrecords(source_payload):
        if sig == b"RNAM":
            out += esp.encode_subrecord(b"RNAM", struct.pack("<I", new_primary_rnam))
        elif sig == ARMA_ADDITIONAL_RACE_SIG:
            # Drop existing additional-race entries; we'll re-emit at the end
            continue
        elif sig in (b"SNDD", b"ONAM"):
            # Footstep-sound set / art-object: canonically AFTER the race list.
            trailing += esp.encode_subrecord(sig, data)
        elif sig == b"DNAM" and ensure_female:
            # Force female availability (UBE is female-only). See _force_female_priority.
            out += esp.encode_subrecord(b"DNAM", _force_female_priority(data))
        elif sig in ARMA_MODEL_SIGS:  # MOD2/MOD3/MOD4/MOD5 model paths
            # Redirect each model to its converted !UBE\ mesh IF we produced one
            # (converted_nif_exists). A mesh we DIDN'T convert (vanilla mesh the
            # mod doesn't ship) keeps its original path so it loads the real mesh
            # instead of a non-existent !UBE\ NIF (which would CTD on load).
            # Texture-hash blocks (MO?T) copy through verbatim below.
            path = data.rstrip(b"\x00").decode("utf-8", errors="ignore")
            converted = bool(path) and (converted_nif_exists is None
                                        or converted_nif_exists(path))
            new_path = (path_prefix + path) if converted else path
            # UBE is female-only: the FEMALE model (MOD3/MOD5) is what the actor
            # renders. If we converted the MALE model (conv_mod2/4 set -> an !UBE
            # mesh exists) but the female model did NOT convert (it kept an
            # original path), that female path is either a CBBE-shaped mesh that
            # won't fit UBE or -- for multi-mod armours whose female mesh name
            # doesn't match the converted one (e.g. Penitus penitusF vs the
            # converted ArmorPenitusF) -- a DEAD path, so the female UBE actor
            # renders NOTHING (invisible). Redirect the female model to the
            # converted male mesh (same target as the missing-MOD3 synth below)
            # and drop its now-mismatched texture-hash. Non-body armours
            # (helmets) never hit this: their MOD2 isn't converted either, so
            # conv_mod2 stays None and the original female path is kept. #174
            if (sig == b"MOD3" and not converted and ensure_female
                    and conv_mod2):
                out += esp.encode_subrecord(b"MOD3", esp.encode_zstring(conv_mod2))
                saw_mod3 = True
                skip_mo3t = True
            elif (sig == b"MOD5" and not converted and ensure_female
                    and conv_mod4):
                out += esp.encode_subrecord(b"MOD5", esp.encode_zstring(conv_mod4))
                saw_mod5 = True
                skip_mo5t = True
            else:
                out += esp.encode_subrecord(sig, esp.encode_zstring(new_path))
                if sig == b"MOD3":
                    saw_mod3 = True
                elif sig == b"MOD5":
                    saw_mod5 = True
                elif sig == b"MOD2" and converted:
                    conv_mod2 = new_path
                elif sig == b"MOD4" and converted:
                    conv_mod4 = new_path
        elif sig in ALT_TEXTURE_SIGS and alt_texture_fid_remap is not None:
            # Remap embedded TXST FormIDs from source's master space.
            new_data = _remap_alt_texture_payload(data, alt_texture_fid_remap)
            out += esp.encode_subrecord(sig, new_data)
        elif sig in ARMA_MODT_SIGS:
            # Drop the female texture-hash if we just redirected the female model
            # to the (different) converted male mesh -- the hash no longer
            # matches that mesh. #174
            if sig == b"MO3T" and skip_mo3t:
                skip_mo3t = False
                continue
            if sig == b"MO5T" and skip_mo5t:
                skip_mo5t = False
                continue
            # Normalize the texture-hash block. Old/LE-ported source mods ship
            # headerless MO?T blocks that the SSE parser misreads as a 7.5M-entry
            # array -> overread CTD at model init. See normalize_modt().
            out += esp.encode_subrecord(sig, normalize_modt(data))
        else:
            out += esp.encode_subrecord(sig, data)

    # UBE female-only policy: if the source armature has a MALE 3rd-person model
    # (MOD2) but NO female one (MOD3), point the female model at the converted
    # male mesh so a female UBE actor renders it instead of NOTHING (the
    # male-only invisibility class). Gated on conv_mod2 being set -- i.e. that
    # !UBE\ mesh actually exists on disk -- so we never synthesise a model that
    # would CTD. Same for 1st-person (MOD4 -> MOD5). #UBE-female-only-policy.
    if ensure_female and not saw_mod3 and conv_mod2:
        out += esp.encode_subrecord(b"MOD3", esp.encode_zstring(conv_mod2))
    if ensure_female and not saw_mod5 and conv_mod4:
        out += esp.encode_subrecord(b"MOD5", esp.encode_zstring(conv_mod4))

    # Append the new additional-race list (canonical position: after the
    # models, before SNDD/ONAM), then the deferred SNDD/ONAM tail.
    for fid in new_additional_race_fids:
        out += esp.encode_subrecord(ARMA_ADDITIONAL_RACE_SIG, struct.pack("<I", fid))
    out += trailing
    return out


def replace_arma_edid(source_payload: bytes, new_edid: str) -> bytes:
    """Replace the EDID subrecord in an ARMA payload."""
    out = b""
    replaced = False
    for sig, data in esp.iter_subrecords(source_payload):
        if sig == b"EDID" and not replaced:
            out += esp.encode_subrecord(b"EDID", esp.encode_zstring(new_edid))
            replaced = True
        else:
            out += esp.encode_subrecord(sig, data)
    return out


# ----- ARMO override mutation ---------------------------------------------

# In ARMO records, MODL is also overloaded — but with a different meaning.
# The Armatures list uses subrecord signature MODL (FormID per entry), same
# as in ARMA's additional-race list. Both are 4-byte FormID-per-occurrence.
ARMO_ARMATURE_SIG = b"MODL"


# ----- EDID -> human name synthesis ---------------------------------------
# Used when we override an ARMO from a localized master (Skyrim.esm + DLCs)
# whose FULL subrecord is an LSTRING ref into a `.STRINGS` file. We can't
# emit the LSTRING in our non-localized patch — the engine would read it
# as a raw zstring and get garbage. We also don't ship a STRINGS parser.
# Without a FULL subrecord at all, the inventory UI hides the item (the
# "any body slot vanilla item doesn't appear in inventory" bug). The
# compromise: synthesize a readable name from the EDID.
#
# Strategy: drop common tag prefixes (`DLC1`/`DLC2`/`DLC1n` DLC markers,
# then `Ench`/`Armor`/`Clothes`/`Armory` type prefixes, recursively),
# then break CamelCase at upper-to-lower transitions and digit boundaries.
# Mid-string occurrences of "Armor" are kept since they're usually part
# of the natural English name (e.g. "Vampire Armor Red").
# Example:
#   "ArmorIronCuirass"                     -> "Iron Cuirass"
#   "EnchArmorDwarvenCuirassDestruction04" -> "Dwarven Cuirass Destruction 04"
#   "DLC1ArmorVampireArmorGrayLight"       -> "Vampire Armor Gray Light"
#   "DLC1nVampireBloodMagicRingDrainingClaws" -> "Vampire Blood Magic Ring Draining Claws"
#   "DLC1EnchClothesVampireRobesDestruction02" -> "Vampire Robes Destruction 02"
import re as _re
_EDID_PREFIX_STRIP = (
    "Ench",          # before Armor — enchanted variant prefix
    "Armor",
    "Clothes",
    "Armory",
)
# DLC prefix at start, optionally followed by a single lowercase letter
# (e.g. "DLC1", "DLC2", "DLC1n" for Dawnguard night-variant records).
_DLC_PREFIX_RE = _re.compile(r"^DLC\d+[a-z]?")
_CAMEL_SPLIT_RE = _re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])"
)


def synthesize_name_from_edid(edid: str) -> str:
    """Generate a readable item name from an ARMO EDID. Used as a fallback
    when overriding records whose original FULL was an LSTRING ref we
    can't resolve.

    Strips leading tag prefixes in order: DLC[N][n]? once, then any of
    Ench/Armor/Clothes/Armory recursively, then splits CamelCase. Mid-
    string occurrences of the type-prefix words are kept (they're
    usually part of the natural item name).
    """
    s = edid
    # DLC prefix gets stripped first (single pass — there's never more
    # than one DLC tag at the start).
    m = _DLC_PREFIX_RE.match(s)
    if m and m.end() < len(s):
        s = s[m.end():]
    # Strip known type prefixes recursively (Ench then Armor handles
    # `EnchArmor*`; nested cases like `EnchClothes*` likewise unwind).
    changed = True
    while changed:
        changed = False
        for prefix in _EDID_PREFIX_STRIP:
            if s.startswith(prefix) and len(s) > len(prefix):
                s = s[len(prefix):]
                changed = True
                break
    # Split CamelCase / digit boundaries.
    parts = _CAMEL_SPLIT_RE.split(s)
    name = " ".join(p for p in parts if p)
    return name or edid  # fall back to raw EDID if synthesis collapsed


# Body biped slot — slot 32 = chest/body.
BODY_BIPED_SLOT = 32

# Skyrim.esm DefaultRace FormID (low 24 bits). Every player-equippable
# humanoid ARMA primary-races (RNAM) to this; beast/custom-race armatures
# do not. Used to gate which non-body ARMAs are safe to extend to the UBE
# races (adding human/mer UBE races to a beast armature crashes — see the
# passthrough guard in generate_ube_patch).
_DEFAULT_RACE_LOW24 = 0x000019


def add_slot32_to_bod2_payload(payload: bytes) -> tuple[bytes, bool]:
    """Set biped slot 32 (the chest/body slot) on the ARMA/ARMO record's
    BOD2 (or legacy BODT) bipedObjectSlots field. Returns the new payload
    and a bool indicating whether anything actually changed.

    Why this exists: NioOverride's BodyMorph engine, which applies TRI-
    driven body-slider deformations to in-game shapes, only walks shapes
    that belong to ARMAs with slot 32 set. Slot-49-only cloth (corsets,
    bras, panties, skirts — what CBBE mods typically use for "underwear"
    layer pieces) inherits zero body-slider deformation no matter how
    perfectly we author its BODYTRI / TRI.

    Promoting slot-49-only cloth to ALSO cover slot 32 makes those
    pieces respond to body sliders. The trade-off: slot 32 is the chest
    slot, so the engine will treat the cloth as a chest piece for
    equip-conflict purposes (e.g. equipping it unequips a cuirass).
    This matches behavior seen in hand-authored UBE cloth conversions.

    Caller is responsible for the policy decision of WHEN to apply this
    (typically only when the linked NIF actually has BODYTRI on cloth
    shapes we ourselves injected — promoting unconditionally would
    break jewelry/accessory ARMAs that legitimately want slot 49 only).
    """
    bit = BODY_BIPED_SLOT - 30  # slot 32 -> bit 2 of the slots field
    out = b""
    changed = False
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(data) >= 4:
            slots = struct.unpack_from("<I", data, 0)[0]
            if not (slots & (1 << bit)):
                new_slots = slots | (1 << bit)
                # Preserve the rest of the BOD2/BODT struct verbatim
                # (BOD2 = u32 slots + u32 armor_type; BODT may have a
                # third u32 — we don't care, just splice the head).
                new_data = struct.pack("<I", new_slots) + data[4:]
                out += esp.encode_subrecord(sig, new_data)
                changed = True
                continue
        out += esp.encode_subrecord(sig, data)
    return out, changed


def add_arma_to_armo_payload(source_payload: bytes, new_arma_fid: int) -> bytes:
    """Insert `new_arma_fid` into an ARMO's Armatures list (MODL entries).

    The new MODL goes IMMEDIATELY AFTER the last existing MODL — preserving
    canonical Skyrim ordering where all MODLs are grouped before DATA. If
    no MODL exists, splices before DATA. If neither MODL nor DATA exists
    (atypical), appends at end as a fallback.

    Why position matters: Skyrim's ARMO parser stops reading the armatures
    list at DATA, so any MODL after DATA is silently ignored — the engine
    never sees the new armature, and a UBE-race player sees no UBE armor.
    """
    pieces = list(esp.iter_subrecords(source_payload))
    last_modl_index = -1
    for i, (sig, _data) in enumerate(pieces):
        if sig == ARMO_ARMATURE_SIG:
            last_modl_index = i

    if last_modl_index >= 0:
        out = b""
        for i, (sig, data) in enumerate(pieces):
            out += esp.encode_subrecord(sig, data)
            if i == last_modl_index:
                out += esp.encode_subrecord(
                    ARMO_ARMATURE_SIG, struct.pack("<I", new_arma_fid)
                )
        return out

    # No existing MODL — splice before DATA.
    out = b""
    inserted = False
    for sig, data in pieces:
        if sig == b"DATA" and not inserted:
            out += esp.encode_subrecord(
                ARMO_ARMATURE_SIG, struct.pack("<I", new_arma_fid)
            )
            inserted = True
        out += esp.encode_subrecord(sig, data)
    if not inserted:
        out += esp.encode_subrecord(
            ARMO_ARMATURE_SIG, struct.pack("<I", new_arma_fid)
        )
    return out


# ----- top-level generator ------------------------------------------------

def _scan_master_armos_referencing(
    master_path: Path,
    referenced_arma_fids_in_master_space: set[int],
) -> list[esp.Record]:
    """Walk an ESM and return ARMO records whose armature list contains
    any FormID in `referenced_arma_fids_in_master_space`. Returns the
    records verbatim (in the master's own master space — top byte 0x00
    for self-defined FormIDs).

    Why this exists: replacer mods (a vanilla-replacer mod, vanilla armor
    replacers in general) commonly override only ARMA records to swap
    in their custom meshes — the corresponding ARMOs live in Skyrim.esm
    or another master. When we generate UBE-variant ARMAs of those
    overridden ARMAs, the original ARMO in the master still points to
    the master's pre-UBE armature list, so a UBE-race actor equipping
    the armor finds no race-matching ARMA and renders nothing.

    To fix, the patcher must create override ARMO records in the patch
    ESP that list both the original ARMA AND our new UBE ARMA. To know
    WHICH master ARMOs need overriding, we scan ARMO records in the
    masters listed by the source ESP.

    Performance: our ESP parser doesn't decompress record payloads — it
    splits records by sig+size headers — so loading Skyrim.esm (250MB,
    2762 ARMOs) takes ~0.2s. We only check ARMO records and only read
    their MODL (armature ref) subrecords.
    """
    try:
        master = _load_master_cached(master_path)
    except Exception:
        return []
    armo_grp = next((g for g in master.groups if g.label == b"ARMO"), None)
    if armo_grp is None:
        return []
    out: list[esp.Record] = []
    for rec in armo_grp.records:
        for sig, sd in esp.iter_subrecords(rec.payload):
            if sig == ARMO_ARMATURE_SIG and len(sd) == 4:
                ref_fid = struct.unpack_from("<I", sd, 0)[0]
                if ref_fid in referenced_arma_fids_in_master_space:
                    out.append(rec)
                    break
    return out


def _find_master_path(master_name: str, data_dirs: list[Path]) -> Path | None:
    """Resolve a master filename (e.g. 'Skyrim.esm') to its on-disk path
    by checking each data directory in order. Returns None if not
    found anywhere."""
    for d in data_dirs:
        if not d.is_dir():
            continue
        candidate = d / master_name
        if candidate.is_file():
            return candidate
        # MO2 mods sometimes capitalize differently; do a case-insensitive
        # match for the filename.
        try:
            for p in d.iterdir():
                if p.name.lower() == master_name.lower() and p.is_file():
                    return p
        except (OSError, PermissionError):
            continue
    return None


# Substrings (lowercased) that mark a RACE record's EDID as a UBE-targeted
# race extension — added to the patch's ARMA additional-race list so the
# new UBE armatures fire for actors using these races.
UBE_RACE_EDID_MARKERS = ("ube",)

# Substrings (lowercased) that EXCLUDE a race even if it matches a marker.
# Use for unofficial / community UBE race patches that aren't ready for
# production — their armor weighting / skeleton may not match the official
# UBE setup, so equipping our converted ARMA on these races can produce
# worse results than no patch at all. The user explicitly excluded
# Khajiit-flavored UBE races on 2026-05-25 for this reason.
UBE_RACE_EDID_EXCLUDE = ("khajiit",)


def _discover_ube_races(data_dirs: list[Path]) -> list[tuple[str, int, str]]:
    """Walk every plugin in `data_dirs` and return all RACE records whose
    EDID contains a UBE marker substring.

    Returns a list of (plugin_filename, race_fid_in_plugin_space, edid)
    triples. The FormID is in the plugin's OWN master space (top byte =
    len(plugin.masters) for self-defined records); the caller is
    responsible for remapping when emitting into a patch.

    De-dupes by EDID across plugins (first-seen wins) so an UBE_AllRace
    base race isn't double-counted when a Khajiit patch redefines it.
    UBE_AllRace.esp itself is excluded — its races are added separately
    via UBE_RACE_FIDS_24 to preserve the well-known primary-race choice.

    Why this exists: UBE_AllRace.esp only ships the 8 base human/mer
    races + their vampire variants (16 total). Khajiit, Argonian, and
    custom-race players use add-on plugins (KhajiitUBE.esp, custom
    UBE_*Race patches) that define additional UBE-skeleton races. If
    our patch's ARMA additional-race list doesn't include those races,
    Khajiit/Argonian/custom-race players see no UBE armor — they fall
    back to the vanilla ARMA, which loads the CBBE-shaped NIF on a UBE
    skeleton (i.e. the bug this whole project exists to fix).

    Memoized by the data-dir set: this walks every plugin in `data_dirs`
    (thousands on a big modlist, ~3s) and is called once per source mod with
    the SAME dirs, so without the cache a 79-mod batch repeats it 79 times.
    """
    cache_key = tuple(str(d) for d in data_dirs)
    cached = _UBE_RACES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    seen_edids: set[str] = set()
    out: list[tuple[str, int, str]] = []
    seen_paths: set[Path] = set()
    for d in data_dirs:
        if not d.is_dir():
            continue
        try:
            plugins = list(d.glob("*.esp")) + list(d.glob("*.esm"))
        except (OSError, PermissionError):
            continue
        for p in plugins:
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            # Skip UBE_AllRace.esp — its races are added via the
            # hardcoded UBE_RACE_FIDS_24 path with a known primary.
            if p.name.lower() == "ube_allrace.esp":
                continue
            try:
                plugin_esp = esp.ESP.load(p)
            except Exception:
                continue
            race_grp = next((g for g in plugin_esp.groups if g.label == b"RACE"),
                            None)
            if race_grp is None:
                continue
            for rec in race_grp.records:
                edid = None
                for sig, sd in esp.iter_subrecords(rec.payload):
                    if sig == b"EDID":
                        edid = sd.rstrip(b"\x00").decode("utf-8",
                                                         errors="ignore")
                        break
                if not edid:
                    continue
                low = edid.lower()
                if not any(m in low for m in UBE_RACE_EDID_MARKERS):
                    continue
                if any(m in low for m in UBE_RACE_EDID_EXCLUDE):
                    continue
                if low in seen_edids:
                    continue
                seen_edids.add(low)
                out.append((p.name, rec.formid, edid))
    _UBE_RACES_CACHE[cache_key] = out
    return out


def generate_ube_patch(
    source_esp_path: str | Path,
    output_esp_path: str | Path,
    *,
    ube_allrace_filename: str = "UBE_AllRace.esp",
    author: str = "cbbe-to-ube auto-patcher",
    description: str = "Auto-generated UBE compatibility patch",
    master_data_dirs: list[Path] | None = None,
    body_mesh_rel_paths: "set[str] | None" = None,
    bsa_mesh_rel_paths: "set[str] | None" = None,
    converted_rel_paths: "set[str] | None" = None,
) -> dict:
    """Read CBBE source ESP, emit UBE patch ESP. Returns a stats dict.

    `body_mesh_rel_paths`: lowercased forward-slash mesh paths (relative to
    Data\\meshes, e.g. "armor/hide/f/cuirasslight_1.nif") that THIS mod's
    converter will output to `!UBE\\...`. Enables mesh-path-driven vanilla
    BODY coverage: loose-mesh replacers (HDT-SMP Vanilla) ship vanilla armor
    meshes with no ESP records, so the source-ARMA scan finds nothing. For
    each master body ARMA whose female mesh (MOD3) is in this set, we mint a
    UBE ARMA (MOD3 -> !UBE, UBE races) and let the master-ARMO override loop
    append it — making those vanilla body armors visible + fitted on UBE
    races. General: matched purely by mesh path, no per-armor logic."""
    source_esp_path = Path(source_esp_path)
    output_esp_path = Path(output_esp_path)
    src = esp.ESP.load(source_esp_path)
    src_filename = source_esp_path.name

    # Predicate: will we actually produce a converted NIF at the !UBE\ path for
    # this ARMA model? Only then is it safe to redirect the ARMA there; if not
    # (vanilla mesh the mod doesn't ship, a male model we never touch, a mesh
    # the armour filter skipped), keep the original path — pointing an ARMA at
    # a non-existent !UBE\ NIF CRASHES the game on load. Uses the PLANNED set
    # (not the filesystem) because the patch is generated before the NIFs are
    # written. None => legacy always-prefix (callers without conversion ctx).
    def _converted_nif_exists(model_path: str) -> bool:
        if converted_rel_paths is None:
            return True
        return model_path.replace("\\", "/").lstrip("/").lower() \
            in converted_rel_paths

    # Predicate: does the ARMA's ORIGINAL (un-converted) mesh ship on disk in
    # this mod? `body_mesh_rel_paths` is every .nif under the mod's meshes/
    # (relative, lowercased, forward-slash, WITH the _0/_1 weight suffix).
    # Used by the non-body accessory passthrough below to confirm a mod
    # genuinely ships the helmet/jewelry mesh before we extend its ARMA to the
    # UBE races. Matches exact path first, then weight-agnostically (the ARMA
    # names _1 but a mod may ship only _0, or vice-versa).
    def _orig_mesh_on_disk(model_path: str) -> bool:
        # Recognise the mesh if it ships LOOSE (body_mesh_rel_paths) OR is
        # available via a load-order BSA (bsa_mesh_rel_paths). Vigilant/LOTD/
        # Unslaad/... pack their meshes inside .bsa, so loose-only checking left
        # every BSA-shipped accessory (e.g. Vigilant cloaks) failing this gate ->
        # non-body passthrough skipped them -> invisible on UBE (#4). The
        # passthrough KEEPS the original mesh path (no !UBE redirect), so the BSA
        # mesh loads fine -> no crash risk; the DefaultRace gate still excludes
        # beast races.
        if not model_path:
            return False
        s = model_path.replace("\\", "/").lower().lstrip("/")
        if s.startswith("meshes/"):
            s = s[len("meshes/"):]
        base = s[:-4] if s.endswith(".nif") else s
        if base.endswith("_0") or base.endswith("_1"):
            base = base[:-2]
        cands = (s, f"{base}_0.nif", f"{base}_1.nif", f"{base}.nif")
        for pool in (body_mesh_rel_paths, bsa_mesh_rel_paths):
            if pool and any(c in pool for c in cands):
                return True
        return False

    # Predicate: is an un-converted ARMA a SAFE non-body accessory to extend to
    # the UBE races WITHOUT a mesh edit (item 1: helmets/hoods/circlets/jewelry
    # get UBE tags only)? Three guards, all generic (no per-armor logic):
    #   1. Non-body biped slot — has a slot but NOT slot 32. Slot-32 body
    #      armor needs real CBBE->UBE conversion; keeping its CBBE mesh would
    #      show a CBBE torso on a UBE actor.
    #   2. Primary race = humanoid DefaultRace in Skyrim.esm. Adding the 16
    #      human/mer UBE races to a beast/custom-race armature makes it fire
    #      for human UBE actors and load a wrong-for-them mesh -> crash (the
    #      Beard Mask Fix class of crash).
    #   3. The mod actually ships at least one of the ARMA's meshes on disk —
    #      confirms this is the mod's own accessory, not an incidental override
    #      of a vanilla ARMA whose mesh this mod doesn't provide.
    # We keep the ORIGINAL model paths (rebuild_arma_payload only redirects to
    # !UBE\ when converted_nif_exists is True, which it isn't here), so the
    # known-good vanilla-fitting mesh loads on UBE actors. This mirrors what
    # the vanilla-compat patch does for master ARMAs, applied to the mod's own
    # non-body ARMAs.
    def _is_safe_passthrough_accessory(
            rnam: "int | None", slot_bits: int,
            model_paths: "list[str]") -> bool:
        if not slot_bits or (slot_bits & _BIPED_SLOT_BODY_BIT):
            return False
        if rnam is None:
            return False
        mi = rnam >> 24
        masters = src.header.masters
        if (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24 or \
                mi >= len(masters) or masters[mi].lower() != "skyrim.esm":
            return False
        real = [m for m in model_paths if m]
        if not real:
            return False
        if any(_is_nude_skin_model(m) for m in real):
            return False
        return any(_orig_mesh_on_disk(m) for m in real)

    # Auto-discover additional UBE race plugins (Khajiit, Argonian,
    # custom races) so their races also get added to our ARMA's
    # additional-race list. Without this, players using non-base-race
    # UBE patches see no UBE armor coverage. See _discover_ube_races
    # for rationale.
    extra_ube_races: list[tuple[str, int, str]] = []
    if master_data_dirs:
        extra_ube_races = _discover_ube_races(master_data_dirs)
        if extra_ube_races:
            print(f"  discovered {len(extra_ube_races)} extra UBE race(s) "
                  f"from {len({p for p, _, _ in extra_ube_races})} plugin(s):")
            for plugin, _fid, edid in extra_ube_races:
                print(f"    {edid}  ({plugin})")

    # Build the patch's master list.
    # Order: Skyrim.esm first, then the always-loaded vanilla DLC ESMs
    # (Update/Dawnguard/HearthFires/Dragonborn — auto-loaded by every
    # vanilla Skyrim setup, safe to include unconditionally), then
    # anything else from source (dedup-preserving), then UBE_AllRace,
    # then the source ESP itself.
    #
    # Why hard-include the vanilla DLCs: source ESPs (and the DLC ESMs
    # they depend on) frequently reference Update.esm and HearthFires.esm
    # content even when they don't formally list them as masters — this
    # is a known Skyrim quirk. Our patch must list them as masters or
    # those FormID refs silently misroute through whichever master
    # happens to land on the same top byte in our patch (usually
    # Dawnguard or Dragonborn). Result: startup crash.
    # The vanilla DLC ESMs are always loaded by any Skyrim install,
    # so listing them as masters is zero risk.
    patch_masters: list[str] = list(VANILLA_DLC_MASTERS)
    for m in src.header.masters:
        _add_master_if_missing(patch_masters, m)
    _add_master_if_missing(patch_masters, ube_allrace_filename)
    # Add each discovered UBE race plugin as a master so we can reference
    # its RACE FormIDs in our ARMA list.
    for plugin_name, _fid, _edid in extra_ube_races:
        _add_master_if_missing(patch_masters, plugin_name)
    _add_master_if_missing(patch_masters, src_filename)

    # The patch ESP's own records use top byte == len(patch_masters)
    own_top_byte = len(patch_masters) << 24

    # Compute UBE race FormIDs in patch's address space
    ube_top = make_master_byte(patch_masters, ube_allrace_filename) << 24
    ube_primary = ube_top | UBE_PRIMARY_BRETON_FID_24
    # Include the primary race in the additional-race MODL list too. The
    # gold-standard UBE_AllRace.esp armatures list the primary (UBE_BretonRace
    # 0x5734) in BOTH RNAM and the MODL block; mirror that exactly rather than
    # excluding it, so our armatures are structurally identical to the working
    # reference (full 16-race list, primary first).
    ube_additional = [ube_top | low for low in UBE_RACE_FIDS_24]
    # Add discovered Khajiit/Argonian/custom UBE races. Their FormIDs are
    # in the discovering plugin's own master space (top byte = the
    # plugin's own_byte = len(plugin.masters)); remap to patch space by
    # substituting the new top byte for the plugin's index in
    # patch_masters.
    for plugin_name, fid, _edid in extra_ube_races:
        plugin_top = make_master_byte(patch_masters, plugin_name) << 24
        ube_additional.append(plugin_top | (fid & 0xFFFFFF))

    src_arma_group = src.group(b"ARMA")
    src_armo_group = src.group(b"ARMO")
    if src_arma_group is None:
        raise RuntimeError(f"source ESP has no ARMA group: {source_esp_path}")

    # Map source ARMA FormID -> new ARMA FormID in patch
    new_arma_fids: dict[int, int] = {}
    next_obj_id = 0x800  # arbitrary starting point; xEdit conventions vary

    # Build a FormID remap function for source -> patch master space.
    # Each top byte in source space maps to a top byte in patch space.
    src_to_patch_byte: dict[int, int] = {}
    for i, m in enumerate(src.header.masters):
        try:
            j = next(idx for idx, mn in enumerate(patch_masters)
                     if mn.lower() == m.lower())
            src_to_patch_byte[i] = j
        except StopIteration:
            continue
    # Source ESP's OWN byte (= len(src.header.masters)) maps to its
    # index in the patch's master list (where it's now a master).
    src_own_byte = len(src.header.masters)
    src_to_patch_byte[src_own_byte] = make_master_byte(
        patch_masters, src_filename)

    def _remap_src_fid_to_patch(fid: int) -> int:
        """Translate a FormID from source ESP's master space to patch's
        master space."""
        top = (fid >> 24) & 0xFF
        if top in src_to_patch_byte:
            return (src_to_patch_byte[top] << 24) | (fid & 0xFFFFFF)
        return fid

    new_arma_records: list[esp.Record] = []
    for src_arma in src_arma_group.records:
        # source ARMA's payload — pull EDID + model paths + race + slots
        edid = None
        model_paths: list[str] = []
        src_rnam: "int | None" = None
        slot_bits = 0
        src_additional: list[int] = []  # source ARMA's existing additional races
        for sig, data in esp.iter_subrecords(src_arma.payload):
            if sig == b"EDID":
                edid = data.rstrip(b"\x00").decode("utf-8", errors="ignore")
            elif sig in ARMA_MODEL_SIGS:
                model_paths.append(data.rstrip(b"\x00").decode(
                    "utf-8", errors="ignore"))
            elif sig == b"RNAM" and len(data) == 4:
                src_rnam = struct.unpack("<I", data)[0]
            elif sig == ARMA_ADDITIONAL_RACE_SIG and len(data) == 4:
                src_additional.append(struct.unpack("<I", data)[0])
            elif sig in (b"BOD2", b"BODT") and len(data) >= 4:
                slot_bits = struct.unpack_from("<I", data, 0)[0]

        # Only emit a UBE ARMA variant if we actually CONVERTED one of its
        # meshes. A UBE ARMA exists to show a UBE-FITTED mesh on UBE races; if
        # we converted nothing for it, creating one is normally pure risk:
        # we'd be adding the 16 human/mer UBE races to (e.g.) an Argonian/
        # Khajiit beast-variant armature or a vanilla-referenced ARMA, making
        # it fire for human UBE actors and load a mesh that's wrong-for-them
        # or absent -> CRASH (the Beard Mask Fix maskless-hood crash).
        #
        # EXCEPTION (item 1): a non-body accessory (helmet/hood/circlet/
        # jewelry) bound to the humanoid player race, whose mesh this mod
        # ships, is a SAFE passthrough — its vanilla-fitting mesh needs no
        # CBBE->UBE conversion (UBE only changes the torso), so we keep the
        # ORIGINAL mesh and just add the UBE races so it renders on UBE
        # actors. See _is_safe_passthrough_accessory for the guards.
        #
        # converted_rel_paths is None for legacy callers (no conversion
        # context) -> keep old always-emit behavior.
        if converted_rel_paths is not None and model_paths and \
                not any(_converted_nif_exists(m) for m in model_paths):
            if not _is_safe_passthrough_accessory(
                    src_rnam, slot_bits, model_paths):
                continue

        new_edid = (edid + "_UBE") if edid else f"UBE_NewARMA_{next_obj_id:X}"

        # Rebuild ARMA payload with UBE race targeting + path prefix.
        # HANDS (33) / FEET (37): keep the SOURCE primary race (vanilla
        # DefaultRace) instead of UBE_Breton — the engine matches the hand/foot
        # SKIN by primary race only, so a UBE-primary gauntlet/boot is invisible
        # on non-Breton UBE actors (and hides the real hands). Remap the source
        # RNAM into the patch master space; the UBE races still go on as
        # additionals. Body keeps the UBE primary. See _BIPED_SLOT_HANDS_FEET_BITS.
        # Additional-race list. Body (slot 32) matches UBE actors via the UBE
        # body race, so the UBE races alone suffice. HANDS (33) / FEET (37) are
        # different: a UBE actor's hand/foot slot resolves to a VANILLA race
        # (the documented nude-hands fallback routing), so an ARMA that lists
        # ONLY UBE races never matches -> the gauntlet/boot is invisible (and
        # hides the real hands). The source ARMA already carries the full
        # vanilla playable-race list (that's how it renders on vanilla races);
        # we must PRESERVE those (remapped into patch master space) and ADD the
        # UBE races on top -- exactly the 16-UBE + ~23-vanilla list the
        # gold-standard Vanilla_UBE_Race_Compat gauntlets use. Dropping them was
        # the modded-gauntlet-invisible bug. Skyrim.esm races remap cleanly
        # (Skyrim is always a patch master); races whose master isn't a patch
        # master are skipped (can't emit a valid ref) rather than corrupting.
        if slot_bits & _BIPED_SLOT_HANDS_FEET_BITS:
            _prim = _remap_src_fid_to_patch(src_rnam)
            if not _prim:
                _prim = ube_primary  # remap failed -> fall back (no worse)
            _addl: list[int] = []
            _seen: set[int] = set()
            for _f in src_additional:
                if ((_f >> 24) & 0xFF) not in src_to_patch_byte:
                    continue  # source master not mastered by patch -> skip
                _r = _remap_src_fid_to_patch(_f)
                if _r and _r not in _seen:
                    _seen.add(_r); _addl.append(_r)
            for _u in ube_additional:
                if _u not in _seen:
                    _seen.add(_u); _addl.append(_u)
            _additional_for_arma = _addl or list(ube_additional)
        else:
            _prim = ube_primary
            _additional_for_arma = ube_additional
        # PURE gauntlet (slot 33, not 32): ESP-only fallback keeps the ORIGINAL
        # hand mesh (callable returning False prevents the !UBE redirect; None
        # would FORCE it). Now GATED by GAUNTLET_ESP_ONLY — default OFF, because
        # the converted gauntlet (with HDT cloth physics + BODYTRI now stripped
        # for hand/foot slots) should render normally. Flip the flag True to
        # restore ESP-only if converted gauntlets still vanish.
        _hands_only = bool(GAUNTLET_ESP_ONLY
                           and (slot_bits & _BIPED_SLOT_HANDS_BIT)
                           and not (slot_bits & _BIPED_SLOT_BODY_BIT))
        _cne_for_arma = ((lambda _p: False) if _hands_only
                         else _converted_nif_exists)
        new_payload = rebuild_arma_payload(
            src_arma.payload,
            new_primary_rnam=_prim,
            new_additional_race_fids=_additional_for_arma,
            alt_texture_fid_remap=_remap_src_fid_to_patch,
            converted_nif_exists=_cne_for_arma,
        )
        new_payload = replace_arma_edid(new_payload, new_edid)

        # New FormID for the new ARMA: top byte = len(patch_masters), bottom = next_obj_id
        new_fid = own_top_byte | next_obj_id
        new_arma_fids[src_arma.formid] = new_fid
        next_obj_id += 1

        new_arma_records.append(esp.Record(
            sig=b"ARMA", flags=0, formid=new_fid,
            timestamp_vc=0, version_unk=0x002C, payload=new_payload,
        ))

    # Now build ARMO overrides: any source ARMO whose MODL list references a
    # source ARMA we converted gets an override with our new ARMA appended.
    armo_overrides: list[esp.Record] = []

    # --- Cross-ESP ARMO coverage (#176) -------------------------------------
    # An ARMO in THIS plugin may reference an ARMA defined in a MASTER plugin
    # (e.g. an Alt-Textures add-on's `Cloth_Cloak` ARMO referencing the BASE
    # mod's cloak ARMA). That ARMA isn't in OUR new_arma_fids -- it's only
    # converted while patching its OWN plugin -- so the same-plugin rule below
    # misses the ARMO and it ships with no UBE armature => invisible on UBE
    # races (the Twilight black/base cloth cloaks). If the referenced master
    # ARMA's female mesh (MOD3) was converted to !UBE, mint a UBE ARMA for it
    # HERE (self-contained: a local own-FormID, no cross-patch references) and
    # link it. Guarded by _converted_nif_exists, so we never point an ARMA at a
    # non-existent !UBE NIF (which would CTD). Matched purely by mesh path; this
    # covers ANY base-plugin + add-on-plugin mod, not just Twilight.
    _xesp_arma_cache: "dict[str, dict[int, esp.Record]]" = {}

    def _xesp_master_arma(ref_fid: int) -> "esp.Record | None":
        """Resolve a referenced ARMA FormID to its record in the owning MASTER
        plugin. None if the ref is source-own or can't be resolved."""
        mbyte = (ref_fid >> 24) & 0xFF
        if mbyte >= len(src.header.masters):
            return None  # source-own record (handled by the same-plugin path)
        mname = src.header.masters[mbyte]
        if mname not in _xesp_arma_cache:
            amap: "dict[int, esp.Record]" = {}
            mp = _find_master_path(
                mname, master_data_dirs or [source_esp_path.parent])
            if mp is not None:
                try:
                    me = esp.ESP.load_cached(mp)
                    for g in me.groups:
                        if g.label == b"ARMA":
                            for r in g.records:
                                amap[r.formid & 0xFFFFFF] = r
                except Exception:
                    amap = {}
            _xesp_arma_cache[mname] = amap
        return _xesp_arma_cache[mname].get(ref_fid & 0xFFFFFF)

    def _mint_xesp_ube_arma(ref_fid: int) -> "int | None":
        """Mint (once per patch) a UBE ARMA for a cross-ESP master ARMA whose
        female mesh we converted; return its new FormID, or None to skip."""
        nonlocal next_obj_id
        if ref_fid in new_arma_fids:
            return new_arma_fids[ref_fid]
        marec = _xesp_master_arma(ref_fid)
        if marec is None:
            return None
        mod3 = mod2 = m_edid = None
        rnam = None
        for sig, d in esp.iter_subrecords(marec.payload):
            if sig == b"MOD3":
                mod3 = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"MOD2":
                mod2 = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"EDID":
                m_edid = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"RNAM" and len(d) == 4:
                rnam = struct.unpack("<I", d)[0]
        # UBE is female-only: cover this armature if we converted EITHER its
        # female (MOD3) OR -- for a male-only armature -- its male (MOD2) mesh.
        # rebuild_arma_payload then synthesises the female model from the
        # converted male mesh (#UBE-female-only-policy). Gated on a converted
        # mesh actually existing, so we never point at a missing !UBE NIF (CTD).
        female_ok = bool(mod3) and _converted_nif_exists(mod3)
        male_ok = bool(mod2) and _converted_nif_exists(mod2)
        if not (female_ok or male_ok):
            return None  # no converted !UBE mesh -> can't cover (would CTD)
        # Safety: only mint for the humanoid player DefaultRace (Skyrim.esm 0x19,
        # master byte 0). Adding the 16 human/mer UBE races to a beast/custom-
        # race armature fires it for human UBE actors -> wrong mesh -> CTD (the
        # Beard Mask Fix crash class, #152). The mesh-converted guard already
        # filters to player armour, but this is the explicit belt-and-braces.
        if rnam is None or (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24 \
                or (rnam >> 24) != 0:
            return None
        stripped = b"".join(
            esp.encode_subrecord(s, d)
            for s, d in esp.iter_subrecords(marec.payload)
            if s not in _STRIP_VANILLA_BODY_ARMA)
        new_payload = rebuild_arma_payload(
            stripped, new_primary_rnam=ube_primary,
            new_additional_race_fids=ube_additional,
            converted_nif_exists=_converted_nif_exists)
        new_payload = replace_arma_edid(
            new_payload,
            (m_edid + "_UBE") if m_edid else f"UBE_XESP_{next_obj_id:X}")
        nf = own_top_byte | next_obj_id
        next_obj_id += 1
        new_arma_records.append(esp.Record(
            sig=b"ARMA", flags=0, formid=nf, timestamp_vc=0,
            version_unk=0x002C, payload=new_payload))
        new_arma_fids[ref_fid] = nf
        return nf

    if src_armo_group is not None:
        for src_armo in src_armo_group.records:
            # Find which source ARMAs this ARMO references
            referenced_src_armas: list[int] = []
            for sig, data in esp.iter_subrecords(src_armo.payload):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    referenced_src_armas.append(struct.unpack("<I", data)[0])

            # Of those, which ones have a new ARMA we created?
            # The ARMO's Armatures list (src_arma_fid) and the source ARMA
            # records' own FormIDs are both in source-master-space, so we
            # look them up in new_arma_fids directly — no remap needed.
            new_armas_to_add = [
                new_arma_fids[src_arma_fid]
                for src_arma_fid in referenced_src_armas
                if src_arma_fid in new_arma_fids
            ]
            # Cross-ESP (#176): for any referenced ARMA NOT converted in this
            # plugin, mint+link a UBE ARMA if its mesh was converted (else skip).
            # This is what makes an add-on plugin's base-color cloth cloaks
            # visible on UBE races (their ARMA lives in the base plugin).
            for _ref in referenced_src_armas:
                if _ref in new_arma_fids and new_arma_fids[_ref] in new_armas_to_add:
                    continue
                _minted = _mint_xesp_ube_arma(_ref)
                if _minted is not None and _minted not in new_armas_to_add:
                    new_armas_to_add.append(_minted)
            if not new_armas_to_add:
                continue

            # Build override payload: ARMO records need their FormID remapped
            # to the patch's master space (the source's own master_byte stays
            # because src is now a master in our patch).
            new_armo_fid = remap_fid(
                src_armo.formid, src.header.masters, src_filename, patch_masters,
            )
            # Source payload may also have FormID-bearing subrecords referencing the
            # source's masters; those need remapping too. For an MVP, only remap
            # the MODL (Armatures) entries — the rest are usually Skyrim.esm refs
            # (master byte 0) which stays 0 in our patch.
            #
            # CRITICAL: insert new MODLs right after the LAST existing MODL,
            # NOT at end of payload. Skyrim's ARMO parser stops reading the
            # armature list at DATA — MODLs after DATA/DNAM are silently
            # ignored, making the new UBE armature invisible to the engine.
            # See the master-derived override path below for the same fix
            # and full rationale.
            src_pieces = list(esp.iter_subrecords(src_armo.payload))
            last_modl_idx = -1
            for i, (sig, data) in enumerate(src_pieces):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    last_modl_idx = i
            new_payload = b""
            for i, (sig, data) in enumerate(src_pieces):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    src_arma_ref = struct.unpack("<I", data)[0]
                    remapped = remap_fid(
                        src_arma_ref, src.header.masters, src_filename, patch_masters,
                    )
                    new_payload += esp.encode_subrecord(sig, struct.pack("<I", remapped))
                elif sig in ALT_TEXTURE_SIGS:
                    # Source ARMO's alternate-texture-set subrecords carry
                    # TXST FormIDs in source's master space. Translate to
                    # patch's master space (the source ESP is now a master,
                    # but at a different index). Without this remap, color-
                    # variant overrides (Cape_Red, Cape_Blue, etc.) all
                    # point at the wrong record and render with the default
                    # texture — "all variants the same color" bug.
                    new_data = _remap_alt_texture_payload(
                        data, _remap_src_fid_to_patch)
                    new_payload += esp.encode_subrecord(sig, new_data)
                elif sig in ARMA_MODT_SIGS:
                    new_payload += esp.encode_subrecord(sig, normalize_modt(data))
                else:
                    new_payload += esp.encode_subrecord(sig, data)
                # Insert our new ARMAs right after the last existing MODL.
                if i == last_modl_idx:
                    for nfid in new_armas_to_add:
                        new_payload += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG, struct.pack("<I", nfid))
            if last_modl_idx < 0:
                # ARMO had no existing MODL — splice ours before DATA.
                pre_data_payload = b""
                inserted = False
                for sig, data in esp.iter_subrecords(new_payload):
                    if sig == b"DATA" and not inserted:
                        for nfid in new_armas_to_add:
                            pre_data_payload += esp.encode_subrecord(
                                ARMO_ARMATURE_SIG, struct.pack("<I", nfid))
                        inserted = True
                    pre_data_payload += esp.encode_subrecord(sig, data)
                if not inserted:
                    for nfid in new_armas_to_add:
                        pre_data_payload += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG, struct.pack("<I", nfid))
                new_payload = pre_data_payload

            armo_overrides.append(esp.Record(
                sig=b"ARMO", flags=0, formid=new_armo_fid,
                timestamp_vc=0, version_unk=0x002C, payload=new_payload,
            ))

    # --- Master ESM ARMO scan ---
    # For each MASTER ESM that this source ESP depends on, walk its
    # ARMO records and find any whose armature list references one of
    # the ARMAs we just converted. Those ARMOs need override entries
    # in our patch too — otherwise the master's pre-UBE armature list
    # is what the engine sees, and UBE-race actors won't find a
    # race-matching mesh. This is what was missing for the vanilla
    # cuirasses overridden by a vanilla-replacer mod: IronCuirassAA's
    # ARMO lives in Skyrim.esm, not in the replacer mod's ESP.
    #
    # `master_data_dirs` is a search path of `Data/` directories — for
    # MO2 setups, both the modlist's `Stock Game/Data/` and the active
    # mods/ overlay. If empty/None, we still try `<source_esp>.parent`
    # (works for standalone Skyrim installs).
    if master_data_dirs is None:
        # Try the source ESP's own directory first
        master_data_dirs = [source_esp_path.parent]

    # String resolver for recovering REAL localized names when overriding
    # localized-master ARMOs (Skyrim.esm + DLCs). The master's FULL is a
    # 4-byte LSTRING index into a `<plugin>_english.STRINGS` table bundled
    # inside `Skyrim - Interface.bsa`. Resolving it gives the true in-game
    # name ("Vampire Armor", "Morag Tong Armor") instead of an EDID-derived
    # guess ("Vampire Armor Red", "Morag Tong Cuirass"). Built from the
    # first master_data_dir that actually contains the interface BSA.
    _string_resolver = None
    try:
        from . import bsa_strings
        for _d in (master_data_dirs or []):
            if (Path(_d) / bsa_strings.StringResolver.INTERFACE_BSA).is_file():
                _key = str(_d)
                if _key not in _STRING_RESOLVER_CACHE:
                    _STRING_RESOLVER_CACHE[_key] = bsa_strings.StringResolver(_d)
                _string_resolver = _STRING_RESOLVER_CACHE[_key]
                break
    except Exception:
        _string_resolver = None

    # Build the FormID set we're looking for, in EACH master's space.
    # new_arma_fids is keyed by source-master-space FormID; for master
    # ESMs the FormID's top byte is 0x00 (the master itself), which is
    # also the top byte we see in source. So lookup is direct.
    master_armo_overrides: list[esp.Record] = []
    master_scan_stats: dict[str, int] = {}
    # Build set of CONVERTED ARMA FormIDs in source-master-space.
    # new_arma_fids keys are source-space FormIDs (top byte=0 for
    # Skyrim.esm-defined records, etc.); that's the same byte layout
    # we'll see in the master ESM itself.
    converted_arma_src_fids = set(new_arma_fids.keys())
    for master_name in src.header.masters:
        master_path = _find_master_path(master_name, master_data_dirs)
        if master_path is None:
            continue
        # Find master's position in source ESP's master list — used to
        # decide which converted ARMAs originated from this master.
        try:
            master_idx_in_src = next(
                i for i, m in enumerate(src.header.masters)
                if m.lower() == master_name.lower()
            )
        except StopIteration:
            continue
        master_byte_in_src = master_idx_in_src

        # Determine the master's OWN byte in its own master space. The
        # master's records have top byte = len(master.masters). For
        # Skyrim.esm (no masters) that's 0x00; for Dawnguard.esm (2
        # masters: Skyrim, Update) it's 0x02; for Dragonborn.esm (3
        # masters) it's 0x03. We previously hardcoded 0x00 which made
        # the lookup miss every DLC ARMO.
        try:
            master_esp = _load_master_cached(master_path)
        except Exception:
            continue
        master_own_byte = len(master_esp.header.masters)

        # CRITICAL: SKIP scanning this master if any of its transitive
        # masters isn't in our patch's master list. Otherwise we'd copy
        # ARMO records whose payload contains FormIDs in the master's
        # transitive-master space (e.g. ccbgssse001-fish.esm references
        # HearthFires.esm at top byte 3 in fish space) — those FormIDs
        # would silently misroute through whatever master happens to be
        # at the same index in OUR patch (often Dragonborn.esm), causing
        # a guaranteed crash when the engine tries to resolve them. This
        # is the same bug class the vanilla-compat path already handles
        # via per-record byte_remap; for generate_ube_patch we take the
        # simpler approach of skipping the scan entirely. The user loses
        # coverage for CC-content armors patched by source mods, but the
        # game starts and the bulk of the patch works.
        patch_masters_lc = {m.lower() for m in patch_masters}
        unmappable_transitive = [
            m for m in master_esp.header.masters
            if m.lower() not in patch_masters_lc
        ]
        if unmappable_transitive:
            master_scan_stats[master_name] = (
                -len(unmappable_transitive)
            )  # negative marker = skipped
            continue

        # Build the FormID set we're searching for in this master's
        # own master space (top byte = master_own_byte because that's
        # how the master refers to its own records).
        lookup_in_master_space = set()
        # Map back from "master space" -> "source space" so we can
        # find the new ARMA FormID later.
        master_to_src_fid: dict[int, int] = {}
        for src_fid in converted_arma_src_fids:
            if ((src_fid >> 24) & 0xFF) == master_byte_in_src:
                master_space_fid = (master_own_byte << 24) | (src_fid & 0xFFFFFF)
                lookup_in_master_space.add(master_space_fid)
                master_to_src_fid[master_space_fid] = src_fid

        # --- Mesh-path-driven vanilla BODY coverage (#131) ---
        # Loose-mesh replacers (HDT-SMP Vanilla) ship vanilla body armor
        # meshes with NO ESP records, so the source-ARMA scan above found
        # nothing for them. But the converter DID fit those meshes to !UBE.
        # Discover this master's OWN body ARMAs whose female mesh (MOD3) we
        # converted, mint a UBE ARMA (MOD3 -> !UBE, UBE races), and register
        # it in the same maps a source-defined ARMA would use — so the
        # master-ARMO override loop below appends it to the parent ARMO.
        # General: matched by mesh path, so it covers any vanilla armor the
        # modpack provides a fitted mesh for, with no per-armor logic.
        if body_mesh_rel_paths:
            # subrecords to strip when minting a UBE ARMA from a master body
            # ARMA -> module-level _STRIP_VANILLA_BODY_ARMA (same set).
            m_arma_grp = next(
                (g for g in master_esp.groups if g.label == b"ARMA"), None)
            for m_arma in (m_arma_grp.records if m_arma_grp else []):
                if m_arma.formid in lookup_in_master_space:
                    continue  # already covered by the source-ARMA scan
                slots = 0
                mod3 = None
                m_edid = None
                for sig, d in esp.iter_subrecords(m_arma.payload):
                    if sig in (b"BOD2", b"BODT") and len(d) >= 4:
                        slots = struct.unpack_from("<I", d, 0)[0]
                    elif sig == b"MOD3":
                        mod3 = d.rstrip(b"\x00").decode("utf-8", "ignore")
                    elif sig == b"EDID":
                        m_edid = d.rstrip(b"\x00").decode("utf-8", "ignore")
                if mod3 is None or not (slots & _BIPED_SLOT_BODY_BIT):
                    continue
                rel = mod3.replace("\\", "/").lstrip("/").lower()
                if rel not in body_mesh_rel_paths:
                    continue
                # Strip stale master-space FormID/texture subrecords, then
                # rebuild with UBE race targeting + !UBE path prefix.
                stripped = b"".join(
                    esp.encode_subrecord(sig, d)
                    for sig, d in esp.iter_subrecords(m_arma.payload)
                    if sig not in _STRIP_VANILLA_BODY_ARMA)
                new_payload = rebuild_arma_payload(
                    stripped,
                    new_primary_rnam=ube_primary,
                    new_additional_race_fids=ube_additional,
                    converted_nif_exists=_converted_nif_exists,
                )
                new_edid = (m_edid + "_UBE") if m_edid else \
                    f"UBE_VanBody_{next_obj_id:X}"
                new_payload = replace_arma_edid(new_payload, new_edid)
                new_fid = own_top_byte | next_obj_id
                next_obj_id += 1
                new_arma_records.append(esp.Record(
                    sig=b"ARMA", flags=0, formid=new_fid,
                    timestamp_vc=0, version_unk=0x002C, payload=new_payload))
                lookup_in_master_space.add(m_arma.formid)
                master_to_src_fid[m_arma.formid] = m_arma.formid
                new_arma_fids[m_arma.formid] = new_fid

        if not lookup_in_master_space:
            continue

        master_armos = _scan_master_armos_referencing(
            master_path, lookup_in_master_space)
        master_scan_stats[master_name] = len(master_armos)
        # Don't re-emit override entries for ARMOs the source ESP
        # already overrode (we handled those above).
        already_overridden_fids = {r.formid for r in armo_overrides}

        for m_armo in master_armos:
            # Translate this master's ARMO FormID to our patch's
            # master space. Master's own records have top byte 0x00
            # in master space; in patch space the top byte is the
            # master's index in patch_masters.
            try:
                master_idx_in_patch = next(
                    i for i, mn in enumerate(patch_masters)
                    if mn.lower() == master_name.lower()
                )
            except StopIteration:
                continue
            patch_master_byte = master_idx_in_patch
            new_armo_fid = (patch_master_byte << 24) | (m_armo.formid & 0xFFFFFF)
            if new_armo_fid in already_overridden_fids:
                continue

            # Find which converted ARMAs this master ARMO references.
            new_armas_to_add: list[int] = []
            existing_armatures_remapped: list[int] = []
            for sig, data in esp.iter_subrecords(m_armo.payload):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    ref_master_fid = struct.unpack("<I", data)[0]
                    # Existing armature ref — translate to patch space.
                    existing_armatures_remapped.append(
                        (patch_master_byte << 24)
                        | (ref_master_fid & 0xFFFFFF)
                    )
                    if ref_master_fid in master_to_src_fid:
                        src_fid = master_to_src_fid[ref_master_fid]
                        new_armas_to_add.append(new_arma_fids[src_fid])
            if not new_armas_to_add:
                continue

            # Build the override payload: keep all original armatures
            # (FormID-translated to patch space) + append our UBE ARMAs.
            #
            # CRITICAL: skip FULL/DESC subrecords from localized masters
            # (Skyrim.esm + the official DLCs). Those subrecords contain
            # 4-byte LSTRING references into a `.strings` file external
            # to the ESM — NOT raw zstrings. Copying them verbatim into
            # our (non-localized) patch makes the engine read those 4
            # bytes as garbage text, which:
            #   (a) shows broken names in inventory, and
            #   (b) appears to make the engine fail-parse the whole
            #       record on some setups — the armor never registers,
            #       so equipping it does nothing visible.
            # Stripping FULL/DESC removes LSTRING refs that we can't
            # resolve in our non-localized patch. We REPLACE FULL with a
            # synthetic name derived from EDID so the inventory UI
            # displays the item — items with no FULL are silently
            # dropped from inventory display (the "body slot vanilla
            # item doesn't appear in inventory" bug). DESC is the
            # tooltip and isn't required for item display; we leave it
            # stripped.
            STRIP_FROM_LOCALIZED_OVERRIDE = {
                b"FULL", b"DESC", b"ITXT", b"NNAM", b"RDMP",
            }
            # Recover this ARMO's name for the FULL we'll inject. The
            # master's own FULL is a 4-byte LSTRING index into its
            # `<plugin>_english.STRINGS` table — resolve it to the REAL
            # localized name. Only if that fails (no strings table, or id
            # missing) do we fall back to synthesizing a name from EDID.
            _source_edid = None
            _full_string_id = None
            for _ssig, _sdata in esp.iter_subrecords(m_armo.payload):
                if _ssig == b"EDID":
                    _source_edid = _sdata.rstrip(b"\x00").decode(
                        "utf-8", errors="ignore")
                elif _ssig == b"FULL" and len(_sdata) == 4:
                    _full_string_id = struct.unpack("<I", _sdata)[0]
            _real_full = None
            if _string_resolver is not None and _full_string_id:
                try:
                    _real_full = _string_resolver.resolve(
                        master_name, _full_string_id)
                except Exception:
                    _real_full = None
            _synth_full = _real_full or (
                synthesize_name_from_edid(_source_edid)
                if _source_edid else None)
            # CRITICAL: insert new MODLs (armature FormIDs) right after
            # the LAST existing MODL, NOT at the end of the payload. The
            # canonical ARMO subrecord order has all MODL armature
            # entries grouped together, BEFORE DATA/DNAM. Skyrim's
            # parser stops reading the armatures list at DATA — if we
            # put new MODLs after DNAM, the engine never sees them, so
            # a UBE-race player equipping the armor never finds our new
            # UBE ARMA and the armor renders invisible. This was the
            # actual cause of "replacer armor not visible" for every
            # master-derived ARMO override (a vanilla-replacer mod, HDT-SMP
            # Vanilla, JS Vanilla Circlets) — fixed 2026-05-25.
            #
            # Find the last existing MODL position so we know where to
            # splice. If the source has no MODL (atypical), fall back
            # to inserting right before the first non-armature-list
            # subrecord we know of (DATA).
            pieces = list(esp.iter_subrecords(m_armo.payload))
            last_modl_idx = -1
            for i, (sig, _data) in enumerate(pieces):
                if sig == ARMO_ARMATURE_SIG and len(_data) == 4:
                    last_modl_idx = i
            new_payload = b""
            _full_inserted = False
            for i, (sig, data) in enumerate(pieces):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    ref_master_fid = struct.unpack("<I", data)[0]
                    remapped = (patch_master_byte << 24) | (ref_master_fid & 0xFFFFFF)
                    new_payload += esp.encode_subrecord(
                        sig, struct.pack("<I", remapped))
                elif sig in STRIP_FROM_LOCALIZED_OVERRIDE:
                    # Skip — would be LSTRING ref against a STRINGS
                    # file the engine can't resolve in our non-localized
                    # patch.
                    pass
                else:
                    # Other subrecords may also carry FormIDs (RNAM,
                    # ETYP, EITM, etc.). The master's own records use
                    # top byte 0x00 = same byte the master will have
                    # in our patch (since masters always sit at their
                    # own index). So no remapping needed for master-
                    # self-FormIDs.
                    new_payload += esp.encode_subrecord(sig, data)
                # Inject a synthetic FULL right after EDID so the item
                # shows up in inventory. EDID is canonically the first
                # subrecord; FULL belongs right after (canonical Skyrim
                # ARMO layout) and before any model paths.
                if (sig == b"EDID" and not _full_inserted and
                        _synth_full is not None):
                    new_payload += esp.encode_subrecord(
                        b"FULL", esp.encode_zstring(_synth_full))
                    _full_inserted = True
                # Splice our new ARMAs immediately after the LAST
                # existing MODL — keeps the canonical "MODLs grouped
                # together before DATA" ordering Skyrim expects.
                if i == last_modl_idx:
                    for nfid in new_armas_to_add:
                        new_payload += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG, struct.pack("<I", nfid))
            if last_modl_idx < 0:
                # ARMO had no existing armatures — splice ours just
                # before DATA (canonical position). Fall back to
                # appending if no DATA either.
                pre_data_payload = b""
                inserted = False
                for sig, data in esp.iter_subrecords(new_payload):
                    if sig == b"DATA" and not inserted:
                        for nfid in new_armas_to_add:
                            pre_data_payload += esp.encode_subrecord(
                                ARMO_ARMATURE_SIG,
                                struct.pack("<I", nfid))
                        inserted = True
                    pre_data_payload += esp.encode_subrecord(sig, data)
                if not inserted:
                    for nfid in new_armas_to_add:
                        pre_data_payload += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG,
                            struct.pack("<I", nfid))
                new_payload = pre_data_payload

            master_armo_overrides.append(esp.Record(
                sig=b"ARMO", flags=0, formid=new_armo_fid,
                timestamp_vc=0, version_unk=0x002C, payload=new_payload,
            ))

    # Merge master ARMO overrides into the main list.
    armo_overrides.extend(master_armo_overrides)

    # Assemble the patch ESP.
    out_header = esp.TES4Header(
        masters=patch_masters,
        author=author,
        description=description,
        version=1.7,
        num_records=0,  # filled by save()
        next_object_id=next_obj_id,
    )
    out = esp.ESP(header=out_header, groups=[])
    if armo_overrides:
        out.groups.append(esp.Group(label=b"ARMO", records=armo_overrides))
    if new_arma_records:
        out.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))

    # Prune masters that no FormID actually references. Source ESPs commonly
    # carry Update.esm or other unused masters; hand-authored UBE patches
    # strip those, so we do too.
    prune_unused_masters(out)

    out.save(output_esp_path)

    # Post-save structural sanity check. Catches subrecord-ordering bugs
    # (like MODL-after-DATA), broken master ordering, FormID drift, and
    # transitive-master crash hazards BEFORE the user tries the patch
    # in-game. Warnings only — does not raise; the patch is still written.
    validation_warnings = validate_patch(
        output_esp_path,
        master_data_dirs=master_data_dirs,
    )

    return {
        "output": str(output_esp_path),
        "masters": out.header.masters,
        "new_arma_count": len(new_arma_records),
        "armo_override_count": len(armo_overrides),
        "master_armo_overrides": len(master_armo_overrides),
        "master_scan_per_esm": master_scan_stats,
        "validation_warnings": validation_warnings,
    }


def validate_patch(esp_path: str | Path,
                   meshes_root: str | Path | None = None,
                   *,
                   check_nifs: bool = True,
                   master_data_dirs: list[Path] | None = None) -> list[str]:
    """Walk a generated patch ESP and return a list of warning strings
    for structural problems. Empty list = clean.

    -- Warning vocabulary (public docs for end users) --------------------
    Each warning starts with a stable prefix so downstream tooling can
    grep/filter by category:

      "modl-after-data: ..."     The replacer-invisible bug. Some ARMO
                                 override has MODL subrecord(s) after DATA;
                                 Skyrim's parser stops reading armatures
                                 at DATA so those armatures get silently
                                 ignored. Equipping the armor renders
                                 nothing on UBE-race characters. Fix: re-
                                 generate the patch (the converter's
                                 splice logic now puts MODL before DATA).

      "master-ordering: ..."     An .esm master appears after a .esp
                                 master in the patch's master list.
                                 Skyrim's ESL loader crashes on this.
                                 Fix: re-generate or fix master order.

      "next-object-id: ..."      The TES4 `next_object_id` field is at or
                                 below the max own FormID actually used.
                                 Engine may collide dynamic FormIDs with
                                 patch records. Fix: re-save.

      "esl-overflow: ..."        ESL flag is set but the patch has more
                                 than 2048 own records — too many for
                                 the FE-prefix ESL slot range. Fix:
                                 unset the ESL flag or split the patch.

      "formid-zero: ..."         A record has FormID 0x00000000, which
                                 is reserved for the player actor. The
                                 engine will reject the record. Fix:
                                 re-generate.

      "formid-out-of-range: ..." A FormID references a master index past
                                 the end of the master list. Definite
                                 crash on equip. Fix: re-generate (the
                                 vanilla-compat path now skips any
                                 record with an unmappable reference).

      "missing-nif: ..."         An ARMA's MOD3/MOD5 path points to a
                                 file that doesn't exist on disk under
                                 the meshes/ folder. Armor renders as
                                 empty when equipped. Only reported
                                 when `meshes_root` is provided or can
                                 be inferred from the ESP location.

      "armo-missing-full: ..."   An ARMO override has no FULL subrecord.
                                 Skyrim's inventory UI silently HIDES
                                 items without FULL — `player.additem`
                                 succeeds but the item never appears.
                                 Fix: ensure FULL is preserved or
                                 synthesized when stripping LSTRING
                                 refs (the master-derived override
                                 path now does this via
                                 synthesize_name_from_edid).

      "unmappable-master-ref: ..." A record in this patch overrides a
                                 master ESM whose payload references
                                 records from a TRANSITIVE master that
                                 ISN'T in this patch's master list.
                                 Those FormIDs silently misroute to
                                 whatever master happens to share the
                                 top byte, usually producing a startup
                                 crash. Cause: CC ESMs (and other
                                 multi-master mods) declare HearthFires/
                                 etc. as masters, and our patch carries
                                 their records without inheriting all
                                 their masters. Detected by checking
                                 every override record's master against
                                 its on-disk master ESP's master list.
                                 Requires `master_data_dirs` to locate
                                 the source masters; warning is skipped
                                 silently when they can't be found.

    Args:
      esp_path: the patch ESP to validate.
      meshes_root: optional path to the mod's `meshes/` directory for
        NIF-existence checking. If None, tries `esp_path.parent/meshes`.
        If that doesn't exist, the NIF check is skipped (no warning).
    """
    warnings: list[str] = []
    esp_path = Path(esp_path)
    e = esp.ESP.load(esp_path)

    # Master ordering: master-TIER plugins must precede regular plugins.
    # "Master-tier" = TES4 ESM flag (0x1), i.e. .esm/.esl AND ESM-flagged .esp
    # (USSEP and countless overhauls) — NOT extension alone. Classifying by
    # `.endswith('.esm')` mislabels every `.esl` (and ESM-flagged `.esp`) as
    # regular, which both FALSE-POSITIVES on a correctly-ordered list full of
    # `.esl` masters AND FALSE-NEGATIVES a real `.esp`-before-ESM-flagged-`.esp`
    # crash. Use the same classifier the merger sorts by (_is_esm_tier_master).
    last_master_tier_idx = -1
    first_regular_idx = -1
    for i, m in enumerate(e.header.masters):
        if _is_esm_tier_master(m, master_data_dirs):
            last_master_tier_idx = i
        elif first_regular_idx < 0:
            first_regular_idx = i
    if first_regular_idx >= 0 and last_master_tier_idx > first_regular_idx:
        warnings.append(
            f"master-ordering: master-tier plugin at index "
            f"{last_master_tier_idx} comes after a regular plugin at index "
            f"{first_regular_idx} (load-order/FormID resolution crash)"
        )

    # next_object_id sanity + FormID zero.
    own_byte = len(e.header.masters)
    n_masters = len(e.header.masters)
    max_own_fid = 0
    has_zero_fid = False
    out_of_range = 0
    out_of_range_examples: list[str] = []
    for g in e.groups:
        for r in g.records:
            if r.formid == 0:
                has_zero_fid = True
            top = (r.formid >> 24) & 0xFF
            if top == own_byte:
                max_own_fid = max(max_own_fid, r.formid & 0xFFFFFF)
            elif top > own_byte:
                # Record's own FormID has a top byte past the master list.
                out_of_range += 1
                if len(out_of_range_examples) < 3:
                    out_of_range_examples.append(f"{r.formid:08X}")
            # Also scan FormID-bearing subrecords for out-of-range refs.
            for sig, sd in esp.iter_subrecords(r.payload):
                if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(sd) == 4:
                    fid = struct.unpack("<I", sd)[0]
                    rtop = (fid >> 24) & 0xFF
                    if rtop > own_byte:
                        out_of_range += 1
                        if len(out_of_range_examples) < 3:
                            out_of_range_examples.append(
                                f"{fid:08X} (in {r.formid:08X})")
    if max_own_fid >= (e.header.next_object_id & 0xFFFFFF):
        warnings.append(
            f"next-object-id: TES4.next_object_id 0x{e.header.next_object_id:06X} "
            f"<= max own FormID 0x{max_own_fid:06X} (engine may collide FormIDs)"
        )
    if has_zero_fid:
        warnings.append(
            "formid-zero: a record has FormID 0x00000000 (reserved for player)"
        )
    if out_of_range:
        warnings.append(
            f"formid-out-of-range: {out_of_range} FormID(s) reference master "
            f"index >= {n_masters} (master list size). Examples: "
            f"{out_of_range_examples}"
        )

    # ESL flag consistency.
    if e.header.flags & TES4_FLAG_ESL:
        own_arma_count = 0
        for g in e.groups:
            if g.label != b"ARMA":
                continue
            for r in g.records:
                if ((r.formid >> 24) & 0xFF) == own_byte:
                    own_arma_count += 1
        if own_arma_count > ESL_MAX_OWN_RECORDS:
            warnings.append(
                f"esl-overflow: ESL flag set but own ARMA count "
                f"{own_arma_count} > {ESL_MAX_OWN_RECORDS} slot limit"
            )

    # ARMO MODL-before-DATA. Caught the replacer-invisible bug May 2026.
    # ARMO-missing-FULL. Caught the "body slot vanilla item doesn't
    # appear in inventory" bug May 2026 — Skyrim's inventory UI
    # silently filters out items with no FULL subrecord.
    armo_grp = next((g for g in e.groups if g.label == b"ARMO"), None)
    if armo_grp:
        bad_armo = 0
        bad_examples: list[str] = []
        no_full = 0
        no_full_examples: list[str] = []
        for r in armo_grp.records:
            data_idx = -1
            last_modl_idx = -1
            has_full = False
            edid = ""
            for i, (sig, _data) in enumerate(esp.iter_subrecords(r.payload)):
                if sig == b"DATA" and data_idx < 0:
                    data_idx = i
                elif sig == b"MODL":
                    last_modl_idx = i
                elif sig == b"FULL":
                    has_full = True
                elif sig == b"EDID":
                    edid = _data.rstrip(b"\x00").decode(
                        "latin1", errors="ignore")
            if data_idx >= 0 and last_modl_idx > data_idx:
                bad_armo += 1
                if len(bad_examples) < 3:
                    bad_examples.append(f"{r.formid:08X}")
            # Skin ARMOs (a race's WNAM body skin, e.g. 00UBE_SkinNaked) are
            # never inventory items, so they legitimately carry no FULL. Only
            # flag WEARABLE armor (which the inventory UI would hide).
            if not has_full and "skinnaked" not in edid.lower():
                no_full += 1
                if len(no_full_examples) < 3:
                    no_full_examples.append(f"{r.formid:08X} ({edid})")
        if bad_armo:
            warnings.append(
                f"modl-after-data: {bad_armo} ARMO record(s) have MODL "
                f"after DATA (Skyrim ignores those armatures). Examples: "
                f"{bad_examples}"
            )
        if no_full:
            warnings.append(
                f"armo-missing-full: {no_full} ARMO record(s) have no "
                f"FULL subrecord (inventory UI hides them). Examples: "
                f"{no_full_examples}"
            )

    # NIF-existence check on ARMA model paths. Only the female slots
    # (MOD3/MOD5) — we never convert male meshes, so MOD2/MOD4 are
    # expected to resolve from vanilla/source-mod paths the engine
    # already knows about.
    if not check_nifs:
        meshes_root = None  # skip the block below
    elif meshes_root is None:
        # Auto-discover: assume the ESP sits in the mod root, meshes/
        # lives next to it.
        candidate = esp_path.parent / "meshes"
        if candidate.is_dir():
            meshes_root = candidate
    if meshes_root is not None:
        meshes_root = Path(meshes_root)
        arma_grp = next((g for g in e.groups if g.label == b"ARMA"), None)
        if arma_grp is not None:
            missing = 0
            missing_examples: list[str] = []
            for r in arma_grp.records:
                for sig, sd in esp.iter_subrecords(r.payload):
                    if sig not in (b"MOD3", b"MOD5"):
                        continue
                    path = sd.rstrip(b"\x00").decode("latin1", errors="ignore")
                    if not path:
                        continue
                    # Only check paths the converter actually owns. Anything
                    # without the !UBE\ prefix is a vanilla/source path the
                    # engine resolves from masters' BSAs; not our concern.
                    if not path.lower().startswith("!ube\\"):
                        continue
                    disk = meshes_root / path.replace("\\", "/")
                    if not disk.is_file():
                        missing += 1
                        if len(missing_examples) < 5:
                            missing_examples.append(
                                f"{sig.decode()}={path} (ARMA {r.formid:08X})"
                            )
            if missing:
                warnings.append(
                    f"missing-nif: {missing} ARMA model path(s) point to "
                    f"!UBE\\ NIF(s) not present under {meshes_root}. "
                    f"Armor will render empty. Examples: {missing_examples}"
                )

    # Unmappable transitive-master check. For every override record,
    # look up the master ESP on disk and verify that EVERY master in
    # that ESP's own master list is also in our patch's master list.
    # If not, payloads carrying FormIDs in the unknown master's space
    # will silently misroute through our patch's master byte that
    # happens to share the index — crash on startup. This is the bug
    # class that caused the May 2026 fish.esm + HearthFires.esm crash.
    if master_data_dirs:
        patch_masters_lc = {m.lower() for m in e.header.masters}
        unmappable_masters: dict[str, set[str]] = {}
        for g in e.groups:
            for r in g.records:
                top = (r.formid >> 24) & 0xFF
                if top >= len(e.header.masters):
                    continue  # own record — skip
                master_name = e.header.masters[top]
                if master_name in unmappable_masters:
                    continue  # already checked
                master_path = _find_master_path(master_name, master_data_dirs)
                if master_path is None:
                    continue  # can't locate — skip silently
                # Header-only read: we just need this master's OWN master list,
                # not a full parse of a multi-MB plugin.
                m_masters = _read_master_list_only(master_path)
                missing_trans = {
                    m for m in m_masters
                    if m.lower() not in patch_masters_lc
                }
                if missing_trans:
                    unmappable_masters[master_name] = missing_trans
        if unmappable_masters:
            details = ", ".join(
                f"{m} (needs {', '.join(sorted(missing_trans))})"
                for m, missing_trans in
                sorted(unmappable_masters.items())[:3]
            )
            warnings.append(
                f"unmappable-master-ref: {len(unmappable_masters)} master(s) "
                f"in patch have transitive masters NOT in our master list "
                f"(silent FormID misroute on startup). {details}"
            )

    return warnings


# ----- master-prune pass --------------------------------------------------

def _iter_formids_in_payload(payload: bytes) -> Iterable[int]:
    """Yield every 4-byte FormID found in a record payload, based on the
    known FORMID_SINGLE_SUBRECORD_SIGS / FORMID_ARRAY_SUBRECORD_SIGS sets."""
    for sig, data in esp.iter_subrecords(payload):
        if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4:
            yield struct.unpack("<I", data)[0]
        elif sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0:
            for i in range(0, len(data), 4):
                yield struct.unpack_from("<I", data, i)[0]


def _rewrite_formids_in_payload(payload: bytes, remap: dict[int, int]) -> bytes:
    """Rebuild a payload, applying `remap` (old_top_byte -> new_top_byte) to
    every FormID in the known FormID-bearing subrecords. Other subrecords are
    copied verbatim."""
    out = b""
    for sig, data in esp.iter_subrecords(payload):
        if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4:
            fid = struct.unpack("<I", data)[0]
            top = (fid >> 24) & 0xFF
            if top in remap:
                fid = (remap[top] << 24) | (fid & 0xFFFFFF)
            out += esp.encode_subrecord(sig, struct.pack("<I", fid))
        elif sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0:
            new_data = b""
            for i in range(0, len(data), 4):
                fid = struct.unpack_from("<I", data, i)[0]
                top = (fid >> 24) & 0xFF
                if top in remap:
                    fid = (remap[top] << 24) | (fid & 0xFFFFFF)
                new_data += struct.pack("<I", fid)
            out += esp.encode_subrecord(sig, new_data)
        else:
            out += esp.encode_subrecord(sig, data)
    return out


def prune_unused_masters(esp_obj: esp.ESP) -> list[str]:
    """Drop masters from esp_obj.header.masters that no FormID references,
    renumbering the remaining FormIDs in place.

    The vanilla DLC ESMs (Skyrim/Update/Dawnguard/HearthFires/Dragonborn)
    are always kept, even when no FormID in the patch references them
    directly. Skyrim quietly resolves DLC FormIDs through these ESMs at
    runtime regardless of whether a record explicitly references one —
    dropping them from the master list causes the engine to misroute
    those refs through whichever master happens to land on the same byte
    index, resulting in a startup / load-into-game crash.

    Returns the list of master names that were dropped.
    """
    n_masters = len(esp_obj.header.masters)
    own_byte = n_masters  # records the patch defines itself use this top byte

    # Collect referenced master indices across all records.
    used: set[int] = set()
    for g in esp_obj.groups:
        for r in g.records:
            used.add((r.formid >> 24) & 0xFF)
            for fid in _iter_formids_in_payload(r.payload):
                used.add((fid >> 24) & 0xFF)

    # Always keep the vanilla DLC ESMs at their existing indices.
    vanilla_low = {m.lower() for m in VANILLA_DLC_MASTERS}
    for i, m in enumerate(esp_obj.header.masters):
        if m.lower() in vanilla_low:
            used.add(i)
    # Always keep own_byte (these are our records — can't drop "ourselves").
    used.add(own_byte)

    keep_indices = [i for i in range(n_masters) if i in used]
    if len(keep_indices) == n_masters:
        return []  # nothing to prune

    dropped = [esp_obj.header.masters[i]
               for i in range(n_masters) if i not in used]

    # Build remap: old_top_byte -> new_top_byte
    remap: dict[int, int] = {}
    for new_idx, old_idx in enumerate(keep_indices):
        if new_idx != old_idx:
            remap[old_idx] = new_idx
    new_own_byte = len(keep_indices)
    if new_own_byte != own_byte:
        remap[own_byte] = new_own_byte

    if remap:
        for g in esp_obj.groups:
            for r in g.records:
                old_top = (r.formid >> 24) & 0xFF
                if old_top in remap:
                    r.formid = (remap[old_top] << 24) | (r.formid & 0xFFFFFF)
                r.payload = _rewrite_formids_in_payload(r.payload, remap)

    esp_obj.header.masters = [esp_obj.header.masters[i] for i in keep_indices]
    return dropped


# --------------------------------------------------------------------------
# Post-conversion ESP patch: promote slot-49 cloth ARMAs to also cover slot 32
# --------------------------------------------------------------------------

def _arma_model_paths(payload: bytes) -> list[str]:
    """Return all MOD2/MOD3/MOD4/MOD5 paths in an ARMA payload.

    These are the per-gender mesh paths the ARMA points at. In our patch
    they're already prefixed with `!UBE\\` because rebuild_arma_payload
    rewrote them.
    """
    paths: list[str] = []
    for sig, data in esp.iter_subrecords(payload):
        if sig in ARMA_MODEL_SIGS:
            paths.append(data.rstrip(b"\x00").decode("utf-8", errors="ignore"))
    return paths


def _arma_slot_bits(payload: bytes) -> int:
    """Return the bipedObjectSlots bitfield from an ARMA's BOD2/BODT, or 0
    if neither is present."""
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(data) >= 4:
            return struct.unpack_from("<I", data, 0)[0]
    return 0


def build_nif_slot_map(esp_paths: "list[Path] | tuple[Path, ...]") -> dict[str, int]:
    """Scan source ESPs' ARMA records to build a NIF-mesh-path -> biped slot
    bitmask mapping. Used by the converter to apply slot-aware behavior
    (e.g. boosted standoff for slot-49 skirts/loincloths).

    Returns a dict keyed by normalized NIF relative path (lowercase, forward
    slashes, no leading 'meshes\\' prefix) -> int slot bitfield. When the same
    NIF is referenced by multiple ARMAs (e.g. _0 and _1 weight pair), slot
    bits are OR-merged so any ARMA's slot triggers boosted behavior.

    Slot-bit reference (Skyrim biped slot N -> bit (N-30)):
        slot 32 (body)   = bit 2  = 0x00000004
        slot 49 (pelvis) = bit 19 = 0x00080000
    """
    out: dict[str, int] = {}

    def _norm(p: str) -> str:
        s = p.replace("\\", "/").lower().lstrip("/")
        if s.startswith("meshes/"):
            s = s[len("meshes/"):]
        return s

    for esp_path in esp_paths:
        try:
            e = esp.ESP.load_cached(Path(esp_path))  # read-only scan -> cached
        except Exception:
            continue
        for grp in e.groups:
            if grp.label != b"ARMA":
                continue
            for rec in grp.records:
                slot_bits = _arma_slot_bits(rec.payload)
                if not slot_bits:
                    continue
                for raw_path in _arma_model_paths(rec.payload):
                    if not raw_path:
                        continue
                    key = _norm(raw_path)
                    out[key] = out.get(key, 0) | slot_bits
    return out


def promote_slot49_cloth_to_slot32(
    esp_path: str | Path,
    meshes_root: str | Path,
    nif_has_bodytri: "callable[[Path], bool] | None" = None,
) -> dict:
    """Walk an output UBE-patch ESP. For each ARMA, look up its model NIF
    in `meshes_root`. If the NIF has at least one shape carrying a BODYTRI
    extra-data entry AND the ARMA's biped slots include 49 but not 32,
    add slot 32 to the slot bitfield and re-save the ESP.

    Why: NioOverride's BodyMorph (the engine that applies body sliders to
    in-game shapes via TRI files) only acts on shapes whose ARMA covers
    slot 32. Slot-49-only cloth pieces like a slot-49 corset
    never receive body-slider deformation — proven by diagnostics on
    a slot-49 no-body cloth NIF:
      * a corset shape has BODYTRI -> a slot-49 cloth TRI with 101 morphs
      * Source ARMA covers slot 49 only
      * 1751 / 2296 corset verts (76%) have ZERO scale-bone weight
        because the corset sits in the abdomen (Z=75..94) which 3BA
        has no scale bones for; torso scaling normally comes from
        BodyMorph TRI sliders, which the slot-49 ARMA never receives
    Promoting these ARMAs to slot 32 makes NioOverride morph the shapes
    they own. Engine consequence: equipping the corset will unequip a
    slot-32 cuirass (the same trade-off hand-authored BodySlide UBE
    cloth makes).

    Args:
      esp_path: output UBE-patch ESP to mutate in place
      meshes_root: directory containing the converted meshes (typically
        `<output_dir>/meshes`)
      nif_has_bodytri: predicate `(nif_path: Path) -> bool` overridable
        for testing. Default opens the NIF via pynifly and looks for
        BODYTRI extra-data on any shape.

    Returns a stats dict.
    """
    esp_path = Path(esp_path)
    meshes_root = Path(meshes_root)

    if nif_has_bodytri is None:
        nif_has_bodytri = _default_nif_has_bodytri

    e = esp.ESP.load(esp_path)
    bit32 = 1 << (BODY_BIPED_SLOT - 30)
    promoted = 0
    promoted_edids: list[str] = []
    examined = 0
    bodytri_hits = 0

    for grp in e.groups:
        if grp.label != b"ARMA":
            continue
        for rec in grp.records:
            examined += 1
            slots = _arma_slot_bits(rec.payload)
            # Already covers slot 32 -> nothing to do.
            if slots & bit32:
                continue
            # Doesn't cover slot 49 -> not our target. (Slot 49 is the
            # canonical "underwear/extras" slot for waist-area cloth.
            # We intentionally don't promote arbitrary slot-X-only
            # accessories — only slot 49 has the cloth semantics.)
            if not (slots & (1 << (49 - 30))):
                continue
            # Look at every model path the ARMA points at; if ANY of
            # them resolves to a NIF with BODYTRI on a shape, promote.
            has_bodytri = False
            for path in _arma_model_paths(rec.payload):
                # Normalize separators. ARMA paths are typically
                # Windows-style with `\`; on filesystem we use `/`
                # on POSIX-ish runners but Pathlib handles either.
                nif_rel = Path(path.replace("\\", "/"))
                nif_abs = meshes_root / nif_rel
                if not nif_abs.is_file():
                    continue
                try:
                    if nif_has_bodytri(nif_abs):
                        has_bodytri = True
                        bodytri_hits += 1
                        break
                except Exception:
                    continue
            if not has_bodytri:
                continue
            # Promote.
            new_payload, changed = add_slot32_to_bod2_payload(rec.payload)
            if changed:
                rec.payload = new_payload
                promoted += 1
                # Pull EDID for the report.
                for sig, data in esp.iter_subrecords(rec.payload):
                    if sig == b"EDID":
                        promoted_edids.append(
                            data.rstrip(b"\x00").decode("utf-8", errors="ignore"))
                        break

    if promoted:
        e.save(esp_path)

    return {
        "esp": str(esp_path),
        "armas_examined": examined,
        "armas_with_bodytri": bodytri_hits,
        "armas_promoted_to_slot32": promoted,
        "promoted_edids": promoted_edids,
    }


def _default_nif_has_bodytri(nif_path: Path) -> bool:
    """Open a NIF via pynifly and return True if any shape carries a
    BODYTRI string extra-data entry. Best-effort: returns False on any
    load/parse error."""
    import sys as _sys
    proj_root = Path(__file__).resolve().parent.parent
    pn = str(proj_root / ".pynifly")
    if pn not in _sys.path:
        _sys.path.insert(0, pn)
    try:
        from pyn import pynifly  # type: ignore
    except ImportError:
        return False
    try:
        nf = pynifly.NifFile(filepath=str(nif_path))
    except Exception:
        return False
    try:
        for s in nf.shapes:
            for ed in s.extra_data():
                if hasattr(ed, "string_data") and ed.name == "BODYTRI":
                    return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# Multi-patch merger: combine N UBE patch ESPs into ONE ESL-flagged ESP.
# --------------------------------------------------------------------------

# TES4 record flags
TES4_FLAG_ESM = 0x00000001   # Master file (.esm)
TES4_FLAG_ESL = 0x00000200   # Light plugin (compact form ID range)

# ESL FormID constraints — own records (those defined by THIS plugin) MUST
# fit in [0x000800, 0x000FFF]. That's 2048 record slots. Records inherited
# from masters use the master's own FormIDs and don't count.
ESL_OWN_FORMID_MIN = 0x000800
ESL_OWN_FORMID_MAX = 0x000FFF
ESL_MAX_OWN_RECORDS = ESL_OWN_FORMID_MAX - ESL_OWN_FORMID_MIN + 1  # 2048


# Bit position in BOD2/BODT bipedObjectFlags for slot 32 (the body slot).
# slot 30 = bit 0, slot 32 = bit 2.
_BIPED_SLOT_BODY_BIT = 1 << (32 - 30)

# Slots 33 (hands) + 37 (feet). These armatures carry a HAND/FOOT SKIN shape and
# the engine matches that skin by the ARMA's PRIMARY race (RNAM), NOT the
# additional-race list (the documented UBE nude-hands/feet "primary-only" match).
# So gauntlets/boots must KEEP their source primary (vanilla DefaultRace 0x19,
# which the UBE races resolve to via RaceCompatibility) instead of having it
# replaced with the UBE_BretonRace primary — else they're invisible on every
# non-Breton UBE actor (and the equipped slot hides the real hands -> "hands
# disappear"). PROVEN: vanilla-converted gauntlets keep DefaultRace primary and
# render; modded ones had it overwritten to UBE_Breton and went invisible. Body
# (slot 32) is fine on the UBE primary (its skin is the per-race body routing),
# so this is hands/feet ONLY.
_BIPED_SLOT_HANDS_FEET_BITS = (1 << (33 - 30)) | (1 << (37 - 30))

# Slot 33 (hands) ONLY. Modded gauntlets render INVISIBLE on the UBE actor's hand
# slot even with a structurally-valid converted mesh (valid geometry, hand bones,
# SBP_33_HANDS partition, correct races + ARMO->ARMA linkage) — user-confirmed
# across 4 mesh/ESP fixes that all failed. BOOTS (slot 37, same pipeline) render
# fine, and VANILLA gauntlets via the ESP-only path (Vanilla_UBE_Race_Compat:
# original mesh + UBE race tags) render. So this is engine-level for the hand
# slot, NOT our mesh. GUARANTEED-VISIBLE FALLBACK: route slot-33 gauntlets through
# the SAME ESP-only path — keep the ORIGINAL hand mesh (NO !UBE redirect) and just
# add the UBE races. Renders like vanilla gauntlets (CBBE-shaped, doesn't scale to
# the UBE morph yet — a separate, smaller follow-up). Only a PURE gauntlet
# (slot 33 set, slot 32 NOT) — a body+hands suit still converts its body mesh.
_BIPED_SLOT_HANDS_BIT = 1 << (33 - 30)

# Gauntlet rendering strategy. ESP-only (keep the original CBBE/3BA hand mesh +
# UBE race tags) was the guaranteed-visible fallback while the invisibility cause
# was unknown. Root cause is now believed to be HDT-SMP CLOTH PHYSICS that the
# converter wrongly generated for fabric gauntlets (collapses the rigid piece at
# runtime); nif_convert no longer emits HDT/BODYTRI for hand/foot slots. With that
# removed, the CONVERTED gauntlet should render AND morph AND be UBE-shaped, which
# is strictly better than ESP-only. So default to CONVERTED (flag False). Flip to
# True to fall back to the safe ESP-only path if converted gauntlets still vanish.
GAUNTLET_ESP_ONLY = False

# Weight-agnostic basenames of the NUDE BODY SKIN meshes (hands/feet/body, all
# genders + 1st-person + beast variants). An ARMA whose model is one of these
# is a *nude skin* armature that lives in a race's skin (WNAM) ARMO — NOT an
# equippable accessory. The UBE races already supply their own nude skin via
# UBE_AllRace's 00UBE_NakedHands/NakedFeet/NakedTorso. If the vanilla-compat
# patch ALSO extends the vanilla per-race naked hand/feet armatures to the UBE
# races, the vanilla (CBBE-in-this-load-order) skin armature competes with the
# UBE one in the nude skin list and WINS (our patch loads last) -> a nude UBE
# actor renders CBBE hands/feet while the body stays UBE. So we must never
# extend a nude-skin armature; equippable gauntlets/boots/jewelry (real armor
# meshes) are unaffected and still get UBE-race visibility.
_NUDE_SKIN_BASENAMES = frozenset({
    "femalebody", "malebody", "femalehands", "malehands",
    "femalefeet", "malefeet",
    "1stpersonfemalebody", "1stpersonmalebody",
    "1stpersonfemalehands", "1stpersonmalehands",
    "1stpersonfemalefeet", "1stpersonmalefeet",
    # beast skin variants (player ignores beast races, but exclude anyway so we
    # never touch a nude-skin armature of any race)
    "argonianfemalehands", "argonianmalehands",
    "khajiitfemalehands", "khajiitmalehands",
    "argonianfemalefeet", "argonianmalefeet",
    "khajiitfemalefeet", "khajiitmalefeet",
})


def _is_nude_skin_model(path: str) -> bool:
    """True if a model path is a nude body-skin mesh (body/hands/feet), so the
    vanilla-compat patch never extends its armature to the UBE races (doing so
    makes a competing skin armature win over UBE's own 00UBE_Naked* and the
    actor renders the wrong nude skin). Matches by weight-stripped basename."""
    if not path:
        return False
    # Any mesh under an actor "character assets" skin folder is body skin,
    # not armor (armor lives under meshes\Armor\ or meshes\Clothes\). This
    # catches child skins (ChildHands/ChildFeet), the DLC vampire-lord
    # skeleton skin, and any unique-NPC skin whose basename the rules below
    # would miss -- so the vanilla-compat patch NEVER touches the actual
    # nude hands/feet/body, only armor.
    if "character assets" in path.replace("/", "\\").lower():
        return True
    base = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".nif"):
        base = base[:-4]
    if base.endswith("_0") or base.endswith("_1"):
        base = base[:-2]
    if base in _NUDE_SKIN_BASENAMES:
        return True
    # Per-race / unique-NPC nude-skin variants are named femalehands<Race>
    # (FemaleHandsArgonian, FemaleHandsKhajiit, FemaleHandsAstrid, ...),
    # femalefeet<Race>, femalebody<Race>, + the male / 1st-person forms. The
    # exact-match set above MISSES these (wrong word order vs the
    # 'argonianfemalehands' entries), so beast naked-hand armatures slipped
    # through, got the UBE race, and competed with 00UBE_NakedHands -> wrong
    # nude hands on UBE actors. A prefix match catches every variant.
    return base.startswith((
        "femalehands", "femalefeet", "femalebody",
        "malehands", "malefeet", "malebody",
        "1stpersonfemalehands", "1stpersonfemalefeet", "1stpersonfemalebody",
        "1stpersonmalehands", "1stpersonmalefeet", "1stpersonmalebody",
    ))


# Subrecords stripped when minting a UBE ARMA from a vanilla master body ARMA:
# alt-texture TXST refs + texture hashes (stale master-space FormIDs) + the
# footstep-sound FormID. Same set the per-mod master-scan uses.
_STRIP_VANILLA_BODY_ARMA = {
    b"MO2S", b"MO3S", b"MO4S", b"MO5S",
    b"MO2T", b"MO3T", b"MO4T", b"MO5T",
    b"SNDD",
}
_STRIP_LOCALIZED_ARMO = {b"FULL", b"DESC", b"ITXT", b"NNAM", b"RDMP"}


def _build_armo_body_override(
    m_armo: "esp.Record", master_patch_byte: int,
    master_to_new_arma: "dict[int, int]", master_name: str,
    string_resolver,
) -> "esp.Record | None":
    """Build an ARMO override appending our minted UBE body ARMAs to a master
    ARMO's armature list. Mirrors generate_ube_patch's proven master-ARMO
    builder exactly: armature MODLs stay grouped BEFORE DATA (Skyrim stops
    reading armatures at DATA), localized FULL/DESC are stripped (a synthetic
    FULL is re-injected after EDID so the item shows in inventory), and all
    FormIDs are remapped to patch space. Returns None if this ARMO references
    none of our minted ARMAs. `master_patch_byte` is already << 24."""
    new_armas: "list[int]" = []
    for sig, data in esp.iter_subrecords(m_armo.payload):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            ref = struct.unpack("<I", data)[0]
            if ref in master_to_new_arma:
                new_armas.append(master_to_new_arma[ref])
    if not new_armas:
        return None

    src_edid = None
    full_sid = None
    for sig, data in esp.iter_subrecords(m_armo.payload):
        if sig == b"EDID":
            src_edid = data.rstrip(b"\x00").decode("utf-8", "ignore")
        elif sig == b"FULL" and len(data) == 4:
            full_sid = struct.unpack("<I", data)[0]
    real_full = None
    if string_resolver is not None and full_sid:
        try:
            real_full = string_resolver.resolve(master_name, full_sid)
        except Exception:
            real_full = None
    synth_full = real_full or (
        synthesize_name_from_edid(src_edid) if src_edid else None)

    pieces = list(esp.iter_subrecords(m_armo.payload))
    last_modl = -1
    for i, (sig, _d) in enumerate(pieces):
        if sig == ARMO_ARMATURE_SIG and len(_d) == 4:
            last_modl = i
    out = b""
    full_done = False
    for i, (sig, data) in enumerate(pieces):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            ref = struct.unpack("<I", data)[0]
            out += esp.encode_subrecord(
                sig, struct.pack("<I", master_patch_byte | (ref & 0xFFFFFF)))
        elif sig in _STRIP_LOCALIZED_ARMO:
            pass  # LSTRING refs we can't resolve in a non-localized patch
        else:
            out += esp.encode_subrecord(sig, data)
        if sig == b"EDID" and not full_done and synth_full is not None:
            out += esp.encode_subrecord(b"FULL", esp.encode_zstring(synth_full))
            full_done = True
        if i == last_modl:
            for nf in new_armas:
                out += esp.encode_subrecord(
                    ARMO_ARMATURE_SIG, struct.pack("<I", nf))
    if last_modl < 0:
        # No existing armatures: splice ours just before DATA (canonical).
        rebuilt = b""
        ins = False
        for sig, data in esp.iter_subrecords(out):
            if sig == b"DATA" and not ins:
                for nf in new_armas:
                    rebuilt += esp.encode_subrecord(
                        ARMO_ARMATURE_SIG, struct.pack("<I", nf))
                ins = True
            rebuilt += esp.encode_subrecord(sig, data)
        if not ins:
            for nf in new_armas:
                rebuilt += esp.encode_subrecord(
                    ARMO_ARMATURE_SIG, struct.pack("<I", nf))
        out = rebuilt

    new_armo_fid = master_patch_byte | (m_armo.formid & 0xFFFFFF)
    return esp.Record(sig=b"ARMO", flags=0, formid=new_armo_fid,
                      timestamp_vc=0, version_unk=0x002C, payload=out)


_UBE_SKIN_TEMPLATE_EDIDS = {"Torso": "00UBE_NakedTorso",
                            "Hands": "00UBE_NakedHands",
                            "Feet": "00UBE_NakedFeet"}
_UBE_SKINNAKED_EDID = "00UBE_SkinNaked"
# FormID-bearing subrecords are remapped via the canonical module sets
# (FORMID_SINGLE_SUBRECORD_SIGS + FORMID_ARRAY_SUBRECORD_SIGS) so the fold
# stays in sync with the master-prune pass's notion of what is a FormID.


def _edid_of_rec(rec) -> str:
    for sig, d in esp.iter_subrecords(rec.payload):
        if sig == b"EDID":
            return d.rstrip(b"\x00").decode("ascii", "replace")
    return ""


def _swap_arma_rnam(payload: bytes, new_rnam_fid: int) -> bytes:
    out = b""
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"RNAM":
            d = struct.pack("<I", new_rnam_fid)
        out += esp.encode_subrecord(sig, d)
    return out


def _prepend_armo_modls(payload: bytes, fids: "list[int]") -> bytes:
    """Insert MODL(4-byte FormID) armature refs before the first existing MODL
    (so the engine walks them first). Skips FIDs already present."""
    existing = set()
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"MODL" and len(d) == 4:
            existing.add(struct.unpack("<I", d)[0])
    add = [f for f in fids if f not in existing]
    if not add:
        return payload
    block = b"".join(esp.encode_subrecord(b"MODL", struct.pack("<I", f))
                     for f in add)
    out = b""
    inserted = False
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"MODL" and not inserted:
            out += block
            inserted = True
        out += esp.encode_subrecord(sig, d)
    if not inserted:
        out += block
    return out


def fold_ube_raceskin_skins(vc_path, ube_allrace_path) -> dict:
    """Fold per-race UBE nude-skin routing INTO an existing Vanilla_UBE_Race_
    Compat.esp (in place). UBE_AllRace.esp is NOT modified.

    Why: the Skyrim engine matches nude-skin ARMAs to actors by RNAM PRIMARY
    race only (the ARMA additional-races list is ignored at runtime).
    UBE_AllRace's 00UBE_SkinNaked has primary UBE skin ARMAs for Breton ONLY,
    so every other UBE race (Redguard/Nord/Imperial/Orc/elves...) finds no
    primary match and falls back to the vanilla actor-asset CBBE skin ->
    CBBE hands/feet on a UBE-ish body. This adds a dedicated PRIMARY-race UBE
    skin ARMA (Torso/Hands/Feet -> !UBE meshes) for every race UBE intends
    (derived from the template ARMA's own additional-races list, so it's
    beast-free and version-agnostic) and overrides 00UBE_SkinNaked to prepend
    them. The records live entirely in the VC patch.

    Idempotent: no-op if VC already overrides 00UBE_SkinNaked. Returns a dict
    {folded, races, reason?}.
    """
    vc_path = Path(vc_path)
    ube_allrace_path = Path(ube_allrace_path)
    ube = esp.ESP.load(ube_allrace_path)
    vc = esp.ESP.load(vc_path)

    ube_arma = next((g for g in ube.groups if g.label == b"ARMA"), None)
    ube_armo = next((g for g in ube.groups if g.label == b"ARMO"), None)
    ube_race = next((g for g in ube.groups if g.label == b"RACE"), None)
    if ube_arma is None or ube_armo is None:
        return {"folded": 0, "reason": "UBE_AllRace missing ARMA/ARMO"}

    templates = {}
    for r in ube_arma.records:
        e = _edid_of_rec(r)
        for sl, ed in _UBE_SKIN_TEMPLATE_EDIDS.items():
            if e == ed:
                templates[sl] = r
    skin = next((r for r in ube_armo.records
                 if _edid_of_rec(r) == _UBE_SKINNAKED_EDID), None)
    if len(templates) < 3 or skin is None:
        return {"folded": 0, "reason": "templates/SkinNaked not found"}

    vc_arma = next((g for g in vc.groups if g.label == b"ARMA"), None)
    vc_armo = next((g for g in vc.groups if g.label == b"ARMO"), None)
    if vc_arma is None:
        vc_arma = esp.Group(label=b"ARMA")
        vc.groups.insert(0, vc_arma)
    if vc_armo is None:
        vc_armo = esp.Group(label=b"ARMO")
        vc.groups.append(vc_armo)

    if any(_edid_of_rec(r) == _UBE_SKINNAKED_EDID for r in vc_armo.records):
        return {"folded": 0, "reason": "already present"}

    vm = [m.lower() for m in vc.header.masters]
    um = [m.lower() for m in ube.header.masters]
    ube_name = ube_allrace_path.name.lower()
    if ube_name not in vm:
        return {"folded": 0, "reason": "VC does not master UBE_AllRace"}

    # Full transitive-master closure: VC is about to OVERRIDE a UBE_AllRace
    # record (00UBE_SkinNaked), so it must declare ALL of UBE_AllRace's masters
    # or the override misroutes / the validator flags unmappable-master-ref.
    # UBE_AllRace's masters are ESMs -> insert any missing one before the first
    # ESP (keeps ESM-before-ESP), then remap every existing record (FormID +
    # payload) for the shifted master indices, reusing the prune pass's tested
    # _rewrite_formids_in_payload.
    missing_masters = [m for m in ube.header.masters if m.lower() not in vm]
    if missing_masters:
        old_masters = list(vc.header.masters)
        old_own = len(old_masters)
        first_esp = next((i for i, m in enumerate(old_masters)
                          if m.lower().endswith(".esp")), len(old_masters))
        new_masters = (old_masters[:first_esp] + missing_masters
                       + old_masters[first_esp:])
        name_to_new = {m.lower(): i for i, m in enumerate(new_masters)}
        tb_remap = {i: name_to_new[m.lower()] for i, m in enumerate(old_masters)}
        tb_remap[old_own] = len(new_masters)   # own records shift up
        for g in vc.groups:
            for r in g.records:
                ot = (r.formid >> 24) & 0xFF
                if ot in tb_remap:
                    r.formid = (tb_remap[ot] << 24) | (r.formid & 0xFFFFFF)
                r.payload = _rewrite_formids_in_payload(r.payload, tb_remap)
        vc.header.masters = new_masters
        vm = [m.lower() for m in vc.header.masters]

    ube_own = len(ube.header.masters)
    vc_own = len(vc.header.masters)
    top_remap = {i: (vm.index(n) if n in vm else None)
                 for i, n in enumerate(um)}
    top_remap[ube_own] = vm.index(ube_name)  # UBE-own -> VC's UBE_AllRace idx

    missing = {"hit": False}

    def remap_fid(fid: int) -> int:
        nt = top_remap.get(fid >> 24)
        if nt is None:
            missing["hit"] = True
            return fid
        return (nt << 24) | (fid & 0xFFFFFF)

    def remap_payload(payload: bytes) -> bytes:
        out = b""
        for sig, d in esp.iter_subrecords(payload):
            if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(d) == 4:
                d = struct.pack("<I", remap_fid(struct.unpack("<I", d)[0]))
            elif sig in FORMID_ARRAY_SUBRECORD_SIGS and d and len(d) % 4 == 0:
                d = b"".join(
                    struct.pack("<I", remap_fid(v))
                    for v in struct.unpack(f"<{len(d) // 4}I", d))
            out += esp.encode_subrecord(sig, d)
        return out

    # Races UBE intends for the nude skin = the template's additional-races
    # (MODL) list -> human UBE races only, no beast.
    race_fids = [struct.unpack("<I", d)[0]
                 for sig, d in esp.iter_subrecords(templates["Torso"].payload)
                 if sig == b"MODL" and len(d) == 4]
    race_edid = {r.formid: _edid_of_rec(r)
                 for r in (ube_race.records if ube_race else [])}

    def race_tag(fid: int) -> str:
        e = race_edid.get(fid, "")
        if e.startswith("00UBE_"):
            e = e[len("00UBE_"):]
        if e.endswith("Race"):
            e = e[:-4]
        return e or f"{fid & 0xFFFFFF:06X}"

    max_low = 0x7FF
    for g in vc.groups:
        for r in g.records:
            if (r.formid >> 24) == vc_own:
                max_low = max(max_low, r.formid & 0xFFFFFF)
    next_low = max_low + 1
    # ESL ceiling guard: the VC patch is ESL-flagged, so every own-record
    # FormID must stay <= 0xFFF. Abort (without writing) if the new skin
    # ARMAs wouldn't fit, rather than emit a silently-corrupt light plugin.
    need = len(race_fids) * len(_UBE_SKIN_TEMPLATE_EDIDS)
    if need and next_low + need - 1 > ESL_OWN_FORMID_MAX:
        return {"folded": 0, "races": len(race_fids),
                "reason": (f"ESL FormID space exhausted (need {need} from "
                           f"0x{next_low:X}, ceiling 0x{ESL_OWN_FORMID_MAX:X})")}

    new_armas: list = []
    new_fids: list = []
    used_edids: set = set()
    for race_fid in race_fids:
        tag = race_tag(race_fid)
        for sl, base_edid in _UBE_SKIN_TEMPLATE_EDIDS.items():
            new_edid = f"{base_edid}_{tag}"
            n, k = new_edid, 1
            while n in used_edids:
                k += 1
                n = f"{new_edid}{k}"
            new_edid = n
            used_edids.add(new_edid)
            payload = _swap_arma_rnam(templates[sl].payload, race_fid)
            payload = replace_arma_edid(payload, new_edid)
            payload = remap_payload(payload)
            fid = (vc_own << 24) | next_low
            next_low += 1
            new_armas.append(esp.Record(
                sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                version_unk=0x002C, payload=payload))
            new_fids.append(fid)

    if not new_fids:
        # No UBE races in the template's additional-races list -> nothing to
        # route. Don't write a pointless 00UBE_SkinNaked override.
        return {"folded": 0, "races": len(race_fids),
                "reason": "no UBE races in template additional-races"}

    skin_payload = remap_payload(skin.payload)
    skin_payload = _prepend_armo_modls(skin_payload, new_fids)
    skin_ovr = esp.Record(sig=b"ARMO", flags=0, formid=remap_fid(skin.formid),
                          timestamp_vc=0, version_unk=0x002C,
                          payload=skin_payload)

    if missing["hit"]:
        return {"folded": 0, "reason": "ref to a master VC lacks (aborted)"}

    vc_arma.records.extend(new_armas)
    vc_armo.records.append(skin_ovr)
    if next_low > (vc.header.next_object_id & 0xFFFFFF):
        vc.header.next_object_id = next_low
    vc.save(vc_path)
    return {"folded": len(new_armas), "races": len(race_fids),
            "skinnaked_fid": skin_ovr.formid}


def generate_vanilla_race_compat_patch(
    output_esp_path: str | Path,
    master_data_dirs: list[Path],
    *,
    masters_to_scan: tuple[str, ...] = (
        "Skyrim.esm", "Dawnguard.esm", "Dragonborn.esm",
    ),
    include_cc_masters: bool = False,
    ube_allrace_filename: str = "UBE_AllRace.esp",
    author: str = "cbbe-to-ube vanilla compat",
    description: str = "Extends vanilla non-body ARMAs to UBE races",
    converted_rel_paths: "set[str] | None" = None,
    ube_path_prefix: str = "!UBE\\",
    armo_winner_index: "dict[tuple[str, int], _WinnerRecord] | None" = None,
) -> dict:
    """Emit a standalone ESL-flagged patch that extends vanilla NON-BODY
    ARMAs (helmet, gauntlets, boots, jewelry — every female ARMA that
    does NOT cover slot 32) with UBE races in their additional-race
    list. Fixes invisible vanilla armor for UBE-race players where no
    CBBE replacer mod provides a body-shape-corrected version.

    Why this exists:
      * UBE_AllRace.esp creates new races (UBE_BretonRace, UBE_NordRace,
        etc.) without skeleton-compatible vanilla ARMA coverage.
      * Newrite's RaceCompatibilityCondition is supposed to bridge this
        but doesn't reach every armor record in some setups.
      * Per-mod patches (generate_ube_patch) only cover ARMAs that the
        source CBBE mod overrides. Vanilla Dwarven boots/gauntlets/
        helmet, vanilla jewelry, vanilla circlets etc. don't get
        replaced by a replacer mod or similar — they stay vanilla and don't
        fire on UBE races.
      * Non-body items (slot != 32) use vanilla female meshes that fit
        a UBE skeleton fine — UBE only changes the torso. So we only
        need to extend the race list, not convert the mesh.

    What this DOES NOT do:
      * Body-slot (32) ARMAs are SKIPPED. Vanilla CBBE-shaped body
        meshes on UBE torso look wrong; those need real CBBE→UBE mesh
        conversion via the main per-mod converter path.
      * Doesn't extend male-only ARMAs (no MOD3 = no female mesh).

    Args:
      output_esp_path: where to write the patch ESP.
      master_data_dirs: directories to search for the master ESM files.
      masters_to_scan: which masters to walk (default: vanilla DLC trio).
      include_cc_masters: also walk every ccbgssse*.esm found in the
        data dirs (Creation Club armor packs).
      ube_allrace_filename: filename providing the UBE base races.

    Returns: stats dict with `output`, `arma_overrides`, `masters`,
    `validation_warnings`, etc.
    """
    out_path = Path(output_esp_path)

    # ---- Step 1: enumerate target masters ----
    targets: list[tuple[str, Path]] = []
    for name in masters_to_scan:
        p = _find_master_path(name, master_data_dirs)
        if p is not None:
            targets.append((name, p))
    if include_cc_masters:
        seen = {p for _, p in targets}
        for d in master_data_dirs:
            if not d.is_dir():
                continue
            try:
                for cc in d.glob("ccbgssse*.esm"):
                    if cc not in seen:
                        targets.append((cc.name, cc))
                        seen.add(cc)
            except (OSError, PermissionError):
                continue

    # Also need UBE_AllRace.esp for the race FormIDs.
    ube_path = _find_master_path(ube_allrace_filename, master_data_dirs)
    if ube_path is None:
        raise FileNotFoundError(
            f"{ube_allrace_filename} not found in any master_data_dirs"
        )

    # ---- Step 2: build the patch's master list ----
    # Order: vanilla ESMs first (in canonical order), then any CC ESMs,
    # then UBE_AllRace.esp. (ESMs before ESPs is required by Skyrim's
    # ESL loader.)
    #
    # CRITICAL: we MUST include every transitive master that any scanned
    # master depends on. Dawnguard.esm and Dragonborn.esm each list
    # Update.esm as a master, so their ARMAs may carry FormIDs in
    # Update.esm's space. If Update.esm isn't in OUR patch master list,
    # the FormID remap falls back to leaving the original top byte —
    # which in our patch points at the wrong master (the byte we'd
    # otherwise have assigned to Dawnguard or similar). Game crashes
    # on load when it tries to resolve a non-existent record.
    # Pre-load every scanned master so we can read its master list.
    target_esps: dict[str, esp.ESP] = {}
    for name, path in targets:
        try:
            target_esps[name] = esp.ESP.load(path)
        except Exception:
            continue

    # Union every master referenced by every target into our patch's
    # master list. Vanilla canonical order first to satisfy Skyrim's
    # ESM-ordering rules.
    # Vanilla DLC ESMs are always loaded — include them unconditionally
    # so transitive FormID references through them resolve correctly.
    patch_masters: list[str] = list(VANILLA_DLC_MASTERS)
    referenced_by_targets: set[str] = set()
    for _name, te in target_esps.items():
        for m in te.header.masters:
            referenced_by_targets.add(m)
    # Any non-canonical transitive masters (e.g. cc-pack ESMs that
    # depend on other ESMs we didn't scan).
    for name in referenced_by_targets:
        _add_master_if_missing(patch_masters, name)
    # The targets themselves.
    for name, _ in targets:
        _add_master_if_missing(patch_masters, name)
    _add_master_if_missing(patch_masters, ube_allrace_filename)

    def master_idx(name: str) -> int:
        for i, m in enumerate(patch_masters):
            if m.lower() == name.lower():
                return i
        raise KeyError(name)

    # UBE race FormIDs in patch's address space.
    ube_top = master_idx(ube_allrace_filename) << 24
    ube_races_for_patch = [ube_top | fid for fid in UBE_RACE_FIDS_24]
    ube_primary_for_patch = ube_top | UBE_PRIMARY_BRETON_FID_24

    # ---- Body coverage (optional) ----------------------------------------
    # When `converted_rel_paths` is given (the standalone vanilla-armor flow),
    # we ALSO cover slot-32 vanilla BODY armour: for each master body ARMA
    # whose female mesh (MOD3) we converted to !UBE, mint a UBE ARMA variant
    # (redirect MOD3 -> !UBE, UBE races) as an OWN record, then override the
    # master ARMO that references it to append the new ARMA. This is the same
    # mint+override the per-mod master-scan does; it lets vanilla cuirasses
    # render UBE-fitted on UBE actors with NO replacer mod required. Without
    # `converted_rel_paths`, body-slot ARMAs are skipped exactly as before.
    body_arma_records: list[esp.Record] = []
    body_armo_overrides: list[esp.Record] = []
    own_byte = len(patch_masters)          # ESL own-record top byte (FE space)
    next_obj_id = 0x800
    body_covered = 0

    def _vanilla_converted(model_path: str) -> bool:
        if not converted_rel_paths or not model_path:
            return False
        s = model_path.replace("\\", "/").lstrip("/").lower()
        if s.startswith("meshes/"):
            s = s[len("meshes/"):]
        return s in converted_rel_paths

    # STRINGS resolver to recover REAL ARMO names for the synthetic FULL.
    # Created whenever the game Data dir (with Skyrim - Interface.bsa) is on the
    # search path -- deliberately NOT gated on converted_rel_paths. That gate
    # (#125 real-name resolution, take 2) left the resolver None on every run
    # where the standalone vanilla BODY conversion produced nothing (the common
    # case: converted_rel_paths -> None), so EVERY vanilla ARMO override fell
    # back to synthesize_name_from_edid -> "MGRobes Archmage 1 Hooded" instead of
    # "Archmage's Robes". Name resolution does not depend on any mesh
    # conversion -- only on the master's STRINGS table. #naming
    _string_resolver = None
    try:
        from . import bsa_strings
        for _d in (master_data_dirs or []):
            if (Path(_d) / bsa_strings.StringResolver.INTERFACE_BSA).is_file():
                _key = str(_d)
                if _key not in _STRING_RESOLVER_CACHE:
                    _STRING_RESOLVER_CACHE[_key] = bsa_strings.StringResolver(_d)
                _string_resolver = _STRING_RESOLVER_CACHE[_key]
                break
    except Exception:
        _string_resolver = None

    # ---- Step 3: scan each master ESM for non-body ARMAs ----
    arma_overrides: list[esp.Record] = []
    skipped_no_mod3 = 0
    skipped_body_slot = 0
    skipped_nude_skin = 0
    skipped_already_ube = 0
    skipped_unknown_master_ref = 0
    skipped_non_default_race = 0
    covered_nonbody_mod2_only = 0
    winner_rebased = 0
    scan_stats: dict[str, int] = {}
    for master_name, _master_path in targets:
        master_esp = target_esps.get(master_name)
        if master_esp is None:
            continue
        master_own_byte = len(master_esp.header.masters)
        master_patch_byte = master_idx(master_name) << 24
        arma_grp = next(
            (g for g in master_esp.groups if g.label == b"ARMA"), None)
        if arma_grp is None:
            continue

        # Build the master-byte remap from THIS master's address space
        # into our patch's address space.
        #   master_own_byte (== len(master.masters)) -> master_idx(master_name)
        #   for each i, master.masters[i] -> master_idx(master.masters[i])
        # If we ever fail to map a byte, that's a real problem — skip
        # the record entirely rather than silently mis-route.
        byte_remap: dict[int, int] = {master_own_byte: master_idx(master_name)}
        unmappable_bytes: set[int] = set()
        for i, m in enumerate(master_esp.header.masters):
            try:
                byte_remap[i] = master_idx(m)
            except KeyError:
                unmappable_bytes.add(i)

        def remap_fid(fid: int) -> int | None:
            """Return remapped FormID in patch space, or None if the
            top byte references a master we don't have."""
            top = (fid >> 24) & 0xFF
            if top in byte_remap:
                return (byte_remap[top] << 24) | (fid & 0xFFFFFF)
            return None

        master_to_new_body: dict[int, int] = {}  # master ARMA fid -> new own fid
        hit_count = 0
        for rec in arma_grp.records:
            slots = 0
            has_mod3 = False
            edid = None
            mod3_path = None
            primary_rnam = None
            model_paths: list[str] = []
            existing_races: set[int] = set()
            for sig, sd in esp.iter_subrecords(rec.payload):
                if sig in (b"BOD2", b"BODT") and len(sd) >= 4:
                    slots = struct.unpack_from("<I", sd, 0)[0]
                elif sig == b"EDID":
                    edid = sd.rstrip(b"\x00").decode("utf-8", "ignore")
                elif sig == b"MOD3":  # female 3rd person model
                    has_mod3 = True
                    mod3_path = sd.rstrip(b"\x00").decode("latin-1", "ignore")
                    model_paths.append(mod3_path)
                elif sig == b"MOD2":  # male model (catch nude skin in MOD2 too)
                    model_paths.append(
                        sd.rstrip(b"\x00").decode("latin-1", "ignore"))
                elif sig == ARMA_ADDITIONAL_RACE_SIG and len(sd) == 4:
                    existing_races.add(struct.unpack("<I", sd)[0])
                elif sig == b"RNAM" and len(sd) == 4:
                    primary_rnam = struct.unpack("<I", sd)[0]
                    existing_races.add(primary_rnam)
            if not has_mod3:
                # No female model (MOD3). For BODY armour that means male-only /
                # needs real CBBE->UBE conversion -> skip. But UNGENDERED
                # NON-BODY gear (shields incl. guard/StormCloak shields, some
                # helmets/circlets) legitimately ships MOD2 ONLY; vanilla renders
                # it on females via the MOD2 fallback, and so does a minted
                # UBE-primary ARMA (rebuild_arma_payload keeps the ungendered
                # MOD2 path). The old blanket skip left iron/steel/glass/elven/
                # imperial/hide/dragonplate/guard shields INVISIBLE on UBE races
                # (issue #6). Cover non-body MOD2-bearing items; still skip
                # body-slot or model-less records.
                if (slots & _BIPED_SLOT_BODY_BIT) or not model_paths:
                    skipped_no_mod3 += 1
                    continue
                covered_nonbody_mod2_only += 1
            # HUMANOID ONLY: extend only armatures whose PRIMARY race (RNAM)
            # is the player DefaultRace (Skyrim.esm 0x19). Beast races
            # (Argonian/Khajiit) and creature/NPC-specific races bind their
            # own race and are OUT OF SCOPE -- we never opt into beast-race
            # coverage, and beast naked-skin armatures otherwise compete with
            # the UBE nude skin. For Skyrim.esm the DefaultRace ref is its own
            # 0x00000019; in a DLC it's a Skyrim.esm master ref.
            _ridx = (primary_rnam >> 24) if primary_rnam is not None else -1
            _rlow = (primary_rnam & 0xFFFFFF) if primary_rnam is not None else -1
            if master_name.lower() == "skyrim.esm":
                _is_default_race = (_rlow == 0x000019 and _ridx == 0)
            else:
                _mm = master_esp.header.masters
                _is_default_race = (_rlow == 0x000019 and 0 <= _ridx < len(_mm)
                                    and _mm[_ridx].lower() == "skyrim.esm")
            if not _is_default_race:
                skipped_non_default_race += 1
                continue
            if slots & _BIPED_SLOT_BODY_BIT:
                # BODY armour. Standalone vanilla flow: if we converted this
                # ARMA's female mesh (MOD3) to !UBE, mint a UBE ARMA variant
                # (redirect MOD3 -> !UBE, UBE races) as an OWN record and queue
                # it for the ARMO-override pass below. Otherwise skip as before
                # (a raw vanilla body mesh on a UBE actor would be wrong-shaped).
                if converted_rel_paths and mod3_path and \
                        _vanilla_converted(mod3_path):
                    try:
                        stripped = b"".join(
                            esp.encode_subrecord(s2, d2)
                            for s2, d2 in esp.iter_subrecords(rec.payload)
                            if s2 not in _STRIP_VANILLA_BODY_ARMA)
                        mp = rebuild_arma_payload(
                            stripped,
                            new_primary_rnam=ube_primary_for_patch,
                            new_additional_race_fids=ube_races_for_patch,
                            converted_nif_exists=_vanilla_converted,
                            path_prefix=ube_path_prefix,
                        )
                        new_edid = (edid + "_UBE") if edid \
                            else f"UBE_VanBody_{next_obj_id:X}"
                        mp = replace_arma_edid(mp, new_edid)
                        new_fid = (own_byte << 24) | next_obj_id
                        next_obj_id += 1
                        body_arma_records.append(esp.Record(
                            sig=b"ARMA", flags=0, formid=new_fid,
                            timestamp_vc=0, version_unk=0x002C, payload=mp))
                        master_to_new_body[rec.formid] = new_fid
                        body_covered += 1
                    except Exception:
                        pass
                else:
                    skipped_body_slot += 1
                continue
            # Never extend a NUDE SKIN armature (hands/feet/body base mesh).
            # UBE supplies its own nude skin; extending the vanilla per-race
            # naked armature makes a competing CBBE skin armature win on nude
            # UBE actors -> CBBE hands/feet. See _NUDE_SKIN_BASENAMES.
            if any(_is_nude_skin_model(m) for m in model_paths):
                skipped_nude_skin += 1
                continue

            # If any existing FormID in this record references a master
            # we don't have, skip the record. Better to leave it alone
            # than emit a broken override that crashes the game.
            record_unmappable = False
            for sig, sd in esp.iter_subrecords(rec.payload):
                if sig in (b"RNAM", ARMA_ADDITIONAL_RACE_SIG, b"SNDD") and \
                   len(sd) == 4:
                    fid = struct.unpack("<I", sd)[0]
                    top = (fid >> 24) & 0xFF
                    if top not in byte_remap:
                        record_unmappable = True
                        break
            if record_unmappable:
                skipped_unknown_master_ref += 1
                continue

            # #132 helmet fix: a vanilla NON-BODY armature (helmet/circlet/etc.)
            # only renders on a UBE actor via a UBE-PRIMARY ARMA. Extending the
            # ORIGINAL ARMA's *additional* races (RNAM stays DefaultRace) does NOT
            # render -- measured: guard helmet RNAM=DefaultRace invisible, while
            # the converted body RNAM=UBE_AllRace renders. So MINT a UBE-primary
            # ARMA pointing at the VANILLA mesh (not converted -> _vanilla_
            # converted is False -> rebuild keeps the original path) + UBE races,
            # and link it to the master ARMO via the body-coverage pass below
            # (master_to_new_body + _build_armo_body_override are slot-agnostic).
            # The ORIGINAL ARMA is left untouched, so vanilla / male NPCs keep
            # rendering it -- the ARMO ends up with both. Mirrors the body branch.
            # On ANY failure, fall through to the legacy race-extension override.
            try:
                stripped = b"".join(
                    esp.encode_subrecord(s2, d2)
                    for s2, d2 in esp.iter_subrecords(rec.payload)
                    if s2 not in _STRIP_VANILLA_BODY_ARMA)
                mp = rebuild_arma_payload(
                    stripped,
                    new_primary_rnam=ube_primary_for_patch,
                    new_additional_race_fids=ube_races_for_patch,
                    converted_nif_exists=_vanilla_converted,
                    path_prefix=ube_path_prefix,
                )
                new_edid = (edid + "_UBE") if edid \
                    else f"UBE_VanGear_{next_obj_id:X}"
                mp = replace_arma_edid(mp, new_edid)
                new_fid = (own_byte << 24) | next_obj_id
                next_obj_id += 1
                body_arma_records.append(esp.Record(
                    sig=b"ARMA", flags=0, formid=new_fid,
                    timestamp_vc=0, version_unk=0x002C, payload=mp))
                master_to_new_body[rec.formid] = new_fid
                hit_count += 1
                continue
            except Exception:
                pass  # fall through to legacy race-extension override

            # Translate existing races into patch space for dedupe.
            existing_in_patch_space: set[int] = set()
            for fid in existing_races:
                remapped = remap_fid(fid)
                if remapped is not None:
                    existing_in_patch_space.add(remapped)
            new_races = [r for r in ube_races_for_patch
                         if r not in existing_in_patch_space]
            if not new_races:
                skipped_already_ube += 1
                continue

            # Build override: copy all subrecords, splice new MODL entries
            # right after the last existing MODL (so all races stay
            # grouped, matching canonical layout).
            # CRITICAL: STRIP alt-texture subrecords (MO?S) and texture-
            # hash subrecords (MO?T) when overriding vanilla ARMAs. These
            # contain TXST FormIDs and texture-data hashes that USSEP /
            # Audio Overhaul Skyrim / other vanilla patches frequently
            # modify. Copying the original Skyrim.esm bytes verbatim
            # restores stale references that no longer match the loaded
            # TXST records — engine reads our override, walks the dangling
            # FormID, dereferences NULL, ACCESS_VIOLATION at startup.
            # The May 2026 crash on ARMA 0x0010E2DB (MageApprenticeBoots-
            # Variant1AA) was this exact bug: USSEP redirected MO2S/MO3S
            # TXST refs to its own fixed TXST records, our override
            # reverted them.
            #
            # By stripping MO?S/MO?T, our override loses the texture-set
            # alternatives for that armor on UBE-race actors — they'll
            # render with default (base) textures instead of color
            # variants. Acceptable trade-off for non-body items that
            # users wear briefly.
            STRIP_FROM_ARMA_OVERRIDE = {
                b"MO2S", b"MO3S", b"MO4S", b"MO5S",  # alt textures
                b"MO2T", b"MO3T", b"MO4T", b"MO5T",  # texture hashes
            }
            pieces = list(esp.iter_subrecords(rec.payload))
            # Filter out the stripped subrecords first so they don't
            # appear in the index lookup.
            pieces = [(sig, data) for sig, data in pieces
                      if sig not in STRIP_FROM_ARMA_OVERRIDE]
            last_modl_idx = -1
            for i, (sig, _data) in enumerate(pieces):
                if sig == ARMA_ADDITIONAL_RACE_SIG and len(_data) == 4:
                    last_modl_idx = i
            new_payload = b""
            for i, (sig, data) in enumerate(pieces):
                # Remap FormID-bearing subrecords from master's own space
                # to patch's master space. ARMA fields carrying FormIDs:
                # RNAM (race), MODL (additional race), SNDD (sound).
                if sig in (b"RNAM", ARMA_ADDITIONAL_RACE_SIG, b"SNDD") and \
                   len(data) == 4:
                    fid = struct.unpack("<I", data)[0]
                    remapped = remap_fid(fid)
                    # We already filtered out records with unmappable
                    # refs above, so remap_fid must return a value here.
                    assert remapped is not None
                    new_payload += esp.encode_subrecord(
                        sig, struct.pack("<I", remapped))
                else:
                    new_payload += esp.encode_subrecord(sig, data)
                if i == last_modl_idx:
                    for new_race_fid in new_races:
                        new_payload += esp.encode_subrecord(
                            ARMA_ADDITIONAL_RACE_SIG,
                            struct.pack("<I", new_race_fid))
            if last_modl_idx < 0:
                # No existing additional-race entries — append at end
                # (ARMA's MODL goes at the very end of the record anyway,
                # no DATA-style trailing subrecord blocks this).
                for new_race_fid in new_races:
                    new_payload += esp.encode_subrecord(
                        ARMA_ADDITIONAL_RACE_SIG,
                        struct.pack("<I", new_race_fid))

            # Override record: top byte = master's index in patch space,
            # low 24 bits = master's local FormID.
            override_fid = master_patch_byte | (rec.formid & 0xFFFFFF)
            arma_overrides.append(esp.Record(
                sig=b"ARMA", flags=0, formid=override_fid,
                timestamp_vc=0, version_unk=0x002C,
                payload=new_payload,
            ))
            hit_count += 1

        # Body coverage: append our minted UBE body ARMAs to the master ARMOs
        # that reference the original body ARMAs (MODL-before-DATA, etc.).
        if master_to_new_body:
            try:
                for m_armo in _scan_master_armos_referencing(
                        _master_path, set(master_to_new_body)):
                    ov = _build_armo_body_override(
                        m_armo, master_patch_byte, master_to_new_body,
                        master_name, _string_resolver)
                    if ov is None:
                        continue
                    # #132: overlay the load-order winner's balance (armor
                    # rating/keywords/name) onto this vanilla override so
                    # Requiem/overhaul stats survive (stats-only, no masters
                    # added — KWDA adopted only when its masters are present).
                    if armo_winner_index:
                        abs_id = (master_name.lower(),
                                  m_armo.formid & 0xFFFFFF)
                        win = armo_winner_index.get(abs_id)
                        if (win is not None and not win.is_localized
                                and win.plugin_name.lower() != abs_id[0]):
                            ov.payload = _overlay_winner_stats(
                                ov.payload, win, patch_masters)
                            winner_rebased += 1
                    body_armo_overrides.append(ov)
            except Exception:
                pass
        scan_stats[master_name] = hit_count

    # ---- Step 4: emit the patch ESP ----
    # NOT ESL-flagged. This patch now mints a UBE-primary ARMA for every vanilla
    # NON-BODY armature too (helmet/circlet fix #132), so it carries hundreds of
    # OWN records plus ~2000 ARMO overrides -- an override-heavy load that the
    # ESL FE-space compaction mis-resolved in-game (wrong helmet mesh + most
    # vanilla armor mislabeled, 2026-06-02). Shipping it as a normal full plugin
    # loads the overrides at the plugin's real load-order slot and resolves the
    # minted-ARMA refs correctly. (Costs one ESP load-order slot.)
    out_header = esp.TES4Header(
        masters=patch_masters,
        author=author,
        description=description,
        flags=0,                # was TES4_FLAG_ESL -- see note above (#132)
        version=1.7,
        num_records=0,  # filled by save()
        next_object_id=max(0x800, next_obj_id),
    )
    out_esp = esp.ESP(header=out_header, groups=[])
    all_arma = arma_overrides + body_arma_records
    if all_arma:
        out_esp.groups.append(esp.Group(label=b"ARMA", records=all_arma))
    if body_armo_overrides:
        out_esp.groups.append(esp.Group(label=b"ARMO", records=body_armo_overrides))
    prune_unused_masters(out_esp)
    out_esp.save(out_path)

    validation_warnings = validate_patch(out_path,
                                         master_data_dirs=master_data_dirs)

    return {
        "output": str(out_path),
        "masters": out_esp.header.masters,
        "arma_overrides": len(arma_overrides),
        "body_arma_minted": len(body_arma_records),
        "body_armo_overrides": len(body_armo_overrides),
        "skipped_no_mod3": skipped_no_mod3,
        "skipped_body_slot": skipped_body_slot,
        "skipped_nude_skin": skipped_nude_skin,
        "skipped_already_ube": skipped_already_ube,
        "skipped_unknown_master_ref": skipped_unknown_master_ref,
        "skipped_non_default_race": skipped_non_default_race,
        "covered_nonbody_mod2_only": covered_nonbody_mod2_only,
        "winner_rebased_armos": winner_rebased,
        "scan_per_master": scan_stats,
        "esl_flagged": bool(out_header.flags & TES4_FLAG_ESL),
        "validation_warnings": validation_warnings,
    }


def _read_tes4_flags(esp_path: Path) -> "int | None":
    """Read just the TES4 record-header flags (cheap — first 12 bytes, no
    full parse). Bit 0x1 = ESM (master), bit 0x200 = ESL/light."""
    try:
        with open(esp_path, "rb") as f:
            head = f.read(12)
        if head[:4] != b"TES4":
            return None
        return struct.unpack_from("<I", head, 8)[0]
    except Exception:
        return None


# Cache of ESM-tier verdicts keyed by lowercased plugin name. A plugin's
# master-tier status is stable within a process, and resolving an ESM-flagged
# .esp means an `iterdir()` scan of the data dirs via _find_master_path — doing
# that once per master on a 139-master Combined made validate_patch take >100s.
_ESM_TIER_CACHE: dict[str, bool] = {}


def clear_esm_tier_cache() -> None:
    _ESM_TIER_CACHE.clear()


# Cache of a master plugin's OWN master list, keyed by resolved path string.
# Reading it means parsing only the TES4 record (the first record), NOT the
# whole multi-MB plugin — full esp.ESP.load on every distinct referenced master
# made validate_patch take MINUTES on a big Combined (100+ large masters like
# Vigilant.esm / LegacyoftheDragonborn.esm / Requiem.esp).
_MASTER_LIST_CACHE: dict[str, list[str]] = {}


def _read_master_list_only(path: Path) -> "list[str]":
    """Return a plugin's own master list by parsing ONLY its TES4 record.

    The TES4 record is always first; its `size` field (header offset 4) bounds
    the payload, so we read just header+payload and stop — no group/record walk
    over the rest of the (potentially huge) file."""
    key = str(path)
    cached = _MASTER_LIST_CACHE.get(key)
    if cached is not None:
        return cached
    masters: list[str] = []
    try:
        with open(path, "rb") as f:
            head = f.read(esp.RECORD_HEADER_SIZE)
            if head[:4] == b"TES4":
                size = struct.unpack_from("<I", head, 4)[0]
                payload = f.read(size)
                rec = esp.Record(sig=b"TES4", flags=0, formid=0,
                                 payload=payload)
                masters = esp.TES4Header.parse_from_record(rec).masters
    except Exception:
        masters = []
    _MASTER_LIST_CACHE[key] = masters
    return masters


def _is_esm_tier_master(name: str, data_dirs: "list[Path] | None") -> bool:
    """True if `name` is a MASTER-TIER plugin that must precede regular ESPs in
    a plugin's master list. That's NOT just the `.esm` extension: a plugin is
    master-tier if it has the TES4 ESM flag (0x1) OR the light/ESL flag (0x200)
    set — which includes `.esm`, `.esl` (the extension forces ESM+light), the
    many ESM-flagged `.esp` files (USSEP, lots of overhauls), AND ESL-flagged
    `.esp` files (ESPFE — the modern compact-plugin standard, extremely common:
    3BBB, countless armor mods). ESL-flagged `.esp` carry ONLY 0x200, not 0x1,
    so checking 0x1 alone mislabels them regular and sorts them after a real
    regular .esp -> master order contradicts the engine's load order and the
    overrides mis-route / crash on load (especially for ESL-flagged output).
    Reads the real flag from disk via data_dirs; falls back to the extension if
    the file can't be located."""
    low = name.lower()
    if low.endswith(".esm") or low.endswith(".esl"):
        return True
    cached = _ESM_TIER_CACHE.get(low)
    if cached is not None:
        return cached
    if data_dirs:
        p = _find_master_path(name, data_dirs)
        if p is not None:
            flags = _read_tes4_flags(p)
            if flags is not None:
                # 0x1 = ESM (master), 0x200 = light/ESL. BOTH load in the
                # master block, so either makes the plugin master-tier.
                result = bool(flags & 0x201)
                _ESM_TIER_CACHE[low] = result   # cache ONLY a real on-disk read
                return result
    # File not found / unreadable with THESE data_dirs. Return regular for now
    # but DO NOT cache the failure: an earlier call with a narrow per-mod
    # data_dirs would otherwise poison the verdict, and the later batch MERGE
    # (which passes comprehensive data_dirs) would reuse the stale "regular" and
    # sort an ESL-flagged .esp after a real regular .esp -> the 95-violation
    # master-order regression. Leaving it uncached lets the merge re-read the
    # real flag. (Verified: caching here made `then good dirs` return False.)
    return False


# ----- #132 winner-aware ARMO override rebasing ---------------------------
#
# An ARMO we convert may be overridden LATER in the load order by a third-party
# patch (Requiem balance, Authoria, a replacer). Basing our UBE override on the
# bare master/source record discards that winner's stats/keywords/armatures. The
# winner index lets merge_patches rebase the override on the load-order winner
# and only ADD our _UBE armature, so the winner's content survives.

class _WinnerRecord:
    __slots__ = ("plugin_name", "plugin_masters", "payload", "is_localized")

    def __init__(self, plugin_name, plugin_masters, payload, is_localized):
        self.plugin_name = plugin_name
        self.plugin_masters = plugin_masters
        self.payload = payload
        self.is_localized = is_localized


def _record_abs_fid(formid: int, plugin_masters: list[str],
                    plugin_own_name: str) -> "tuple[str, int]":
    """Absolute identity of a record: (defining-plugin-name-lower, local-id).
    The defining plugin is the master named by the FormID's top byte, or the
    plugin itself for its own (newly-defined) records."""
    top = (formid >> 24) & 0xFF
    if top < len(plugin_masters):
        defining = plugin_masters[top]
    else:
        defining = plugin_own_name
    return (defining.lower(), formid & 0xFFFFFF)


def build_armo_winner_index(
    ordered_plugin_paths: "list[Path]",
    *,
    exclude_names: "set[str] | None" = None,
    target_abs: "set[tuple[str, int]] | None" = None,
) -> "dict[tuple[str, int], _WinnerRecord]":
    """Scan plugins IN LOAD ORDER (ascending — later wins) and return, for each
    ARMO's absolute identity, the highest-priority overriding record.

    `ordered_plugin_paths` must be in load order (plugins.txt order). The last
    plugin that overrides a given ARMO wins, so we simply overwrite the entry as
    we go. `exclude_names` (lowercased filenames) skips our own outputs (the
    Combined + per-mod UBE patches). `target_abs`, if given, restricts indexing
    to those identities (a big perf win — only the ARMOs we actually convert)."""
    exclude = {n.lower() for n in (exclude_names or set())}
    index: dict[tuple[str, int], _WinnerRecord] = {}
    for path in ordered_plugin_paths:
        path = Path(path)
        name = path.name
        if name.lower() in exclude:
            continue
        try:
            pe = esp.ESP.load(path)
        except Exception:
            continue
        armo_grp = next((g for g in pe.groups if g.label == b"ARMO"), None)
        if armo_grp is None:
            continue
        masters = pe.header.masters
        is_loc = bool(pe.header.flags & 0x80)
        for rec in armo_grp.records:
            abs_id = _record_abs_fid(rec.formid, masters, name)
            if target_abs is not None and abs_id not in target_abs:
                continue
            index[abs_id] = _WinnerRecord(name, masters, rec.payload, is_loc)
    return index


# ARMO subrecords carrying the WINNER's balance that have NO FormID, so they can
# be adopted from a winner without mastering the winner plugin. (Requiem etc.
# change armor rating/type/name via these.) EDID is an editor label some patchers
# key on; adopting it keeps those rules matching our now-winning record.
_WINNER_STAT_NOFID_SIGS = (b"EDID", b"OBND", b"FULL", b"BOD2", b"DATA", b"DNAM")


def _overlay_winner_stats(
    base_payload: bytes,
    winner: "_WinnerRecord",
    merged_masters: list[str],
) -> bytes:
    """#132 STATS-ONLY rebase. Overlay the load-order winner's balance onto our
    already-valid base override (which carries the original armatures + our _UBE
    armature in merged space). We adopt only the winner's NO-FormID stat
    subrecords (armor rating/type/name) plus KWDA when every keyword resolves to
    a master ALREADY in the merged list. We KEEP the base's armatures and never
    reference the winner's own records — so NO new masters are pulled in.

    Why not adopt the winner's ARMATURES too: on a heavily-patched list the
    winners span 100+ plugins; mastering them all blows past the 254-master plugin
    limit (measured: +322 masters). Only ~0.3% of winners actually change the
    armature list, so the armatures stay base-derived. Armor RATING/TYPE/KEYWORDS
    /NAME — the balance the user cares about — are preserved for 100% of armors."""
    def _merged_idx(name: str) -> "int | None":
        nl = name.lower()
        for idx, mn in enumerate(merged_masters):
            if mn.lower() == nl:
                return idx
        return None

    # Winner -> merged byte remap, ONLY for masters already present (no adds).
    wbr: dict[int, int] = {}
    for i, m in enumerate(winner.plugin_masters):
        j = _merged_idx(m)
        if j is not None:
            wbr[i] = j
    winner_own_byte = len(winner.plugin_masters)

    # Collect the winner subrecords we'll adopt.
    adopt: dict[bytes, bytes] = {}
    for sig, data in esp.iter_subrecords(winner.payload):
        if sig in _WINNER_STAT_NOFID_SIGS:
            adopt[sig] = data  # last occurrence wins (canonical: one each)
        elif sig == b"KWDA" and len(data) % 4 == 0:
            # Adopt keywords only if EVERY one resolves to a present master.
            ok = True
            new = b""
            for off in range(0, len(data), 4):
                fid = struct.unpack_from("<I", data, off)[0]
                top = (fid >> 24) & 0xFF
                if top == winner_own_byte or top not in wbr:
                    ok = False
                    break
                new += struct.pack("<I", (wbr[top] << 24) | (fid & 0xFFFFFF))
            if ok:
                adopt[b"KWDA"] = new

    if not adopt:
        return base_payload

    # Rebuild base, replacing adopted subrecords in place; insert any the base
    # lacks right after EDID (safe — only MODL-after-DATA ordering is critical,
    # and we never move MODL/DATA).
    pieces = list(esp.iter_subrecords(base_payload))
    consumed: set[bytes] = set()
    out = b""
    edid_idx = -1
    for i, (sig, data) in enumerate(pieces):
        if sig == b"EDID":
            edid_idx = i
        if sig in adopt:
            out += esp.encode_subrecord(sig, adopt[sig])
            consumed.add(sig)
        else:
            out += esp.encode_subrecord(sig, data)
    leftover = [s for s in adopt if s not in consumed]
    if leftover:
        # Re-emit, inserting leftovers right after EDID.
        out = b""
        for i, (sig, data) in enumerate(pieces):
            emit = adopt[sig] if sig in adopt else data
            out += esp.encode_subrecord(sig, emit)
            if i == edid_idx:
                for s in leftover:
                    out += esp.encode_subrecord(s, adopt[s])
        if edid_idx < 0:  # no EDID (atypical) — prepend leftovers
            pre = b"".join(esp.encode_subrecord(s, adopt[s]) for s in leftover)
            out = pre + out
    return out


# ----- Mod-defined non-body UBE coverage (the guard-helmet class) ---------
#
# vanilla-compat mints UBE-primary ARMAs for VANILLA (Skyrim/DLC) non-body items
# so they render on UBE-race actors. But an overhaul (Requiem, Sons of Skyrim,
# Authoria patches) often RE-ARMATURES a vanilla item with its OWN ArmorAddon
# listing only vanilla races -> invisible on UBE actors (the guard helmet:
# REQ_ArmorAddon_GuardsHelmet, 19 vanilla races, 0 UBE). vanilla-compat never
# touches a MOD-defined ARMA, so these slip through. This pass closes the gap
# for ANY plugin: scan the load order for player-equippable NON-BODY ARMOs whose
# WINNING armatures all lack UBE coverage, mint a UBE-primary ARMA per missing
# armature (pointing at the SAME, already-UBE-fitting non-body mesh — UBE only
# reshapes the torso), and override the winning ARMO to add it. #132 generalized.

_HAIR_ONLY_SLOTS = 0x802          # biped slots 31 (Hair) | 41 (LongHair)
_BODY_SLOT_BIT_32 = 1 << 2        # biped slot 32 (Body)
# Biped slots whose mesh DEFORMS with the UBE body and therefore needs real
# CBBE->UBE mesh conversion, not just race coverage: 32 body, 33 hands, 34
# forearms, 37 feet, 38 calves. This pass covers RIGID accessories only
# (helmet/circlet/amulet/ring/etc.) — pointing a CBBE-shaped gauntlet at a UBE
# actor would clip at the wrist/ankle, so those are left to the conversion path.
_DEFORMING_SLOTS_MASK = (1 << 2) | (1 << 3) | (1 << 4) | (1 << 7) | (1 << 8)


def _summarize_arma(payload, masters, own_name):
    rnam = None
    is_ube = False
    for s, d in esp.iter_subrecords(payload):
        if s == b"RNAM" and len(d) >= 4:
            rnam = _record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own_name)
            if rnam[0] == "ube_allrace.esp":
                is_ube = True
        elif s == ARMA_ADDITIONAL_RACE_SIG and len(d) == 4:
            a = _record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own_name)
            if a[0] == "ube_allrace.esp":
                is_ube = True
    return rnam, is_ube


def _summarize_armo(payload, masters, own_name):
    arms = []
    rnam = None
    slots = 0
    edid = None
    for s, d in esp.iter_subrecords(payload):
        if s == ARMO_ARMATURE_SIG and len(d) == 4:
            arms.append(_record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own_name))
        elif s == b"RNAM" and len(d) >= 4:
            rnam = _record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own_name)
        elif s in (b"BOD2", b"BODT") and len(d) >= 4:
            slots = struct.unpack_from("<I", d, 0)[0]
        elif s == b"EDID":
            edid = d.split(b"\x00")[0].decode("latin1", "ignore")
    return arms, rnam, slots, edid


def generate_modded_nonbody_ube_coverage_patch(
    output_esp_path: "str | Path",
    ordered_plugin_paths: "list[Path]",
    *,
    ube_allrace_filename: str = "UBE_AllRace.esp",
    exclude_names: "set[str] | None" = None,
    master_data_dirs: "list[Path] | None" = None,
    author: str = "cbbe-to-ube modded non-body UBE coverage",
    description: str = "UBE race coverage for mod-defined non-body armor",
) -> dict:
    """Emit a patch giving UBE-race coverage to non-body items whose load-order
    WINNING armatures (from ANY plugin) lack it. For each such ARMO, mint a
    UBE-primary ARMA per missing DefaultRace armature (same mesh) and override
    the ARMO to add it. ARMOs/ARMAs are read as load-order winners (last wins).
    `ordered_plugin_paths` must be in load order. Returns a stats dict."""
    out_path = Path(output_esp_path)
    exclude = {n.lower() for n in (exclude_names or set())}
    DEFAULT_RACE = ("skyrim.esm", _DEFAULT_RACE_LOW24)

    # ---- Pass 1: load-order winners for ARMA + ARMO (last wins) ----
    arma_win: dict = {}   # abs -> (payload, masters, plugin, rnam_abs, is_ube)
    armo_win: dict = {}   # abs -> (payload, masters, plugin, arms, rnam, slots, edid)
    for path in ordered_plugin_paths:
        path = Path(path)
        if path.name.lower() in exclude:
            continue
        try:
            pe = esp.ESP.load(path)
        except Exception:
            continue
        m = pe.header.masters
        nm = path.name
        ag = pe.group(b"ARMA")
        if ag:
            for r in ag.records:
                a = _record_abs_fid(r.formid, m, nm)
                rnam, is_ube = _summarize_arma(r.payload, m, nm)
                arma_win[a] = (r.payload, m, nm, rnam, is_ube)
        og = pe.group(b"ARMO")
        if og:
            for r in og.records:
                a = _record_abs_fid(r.formid, m, nm)
                arms, rnam, slots, edid = _summarize_armo(r.payload, m, nm)
                armo_win[a] = (r.payload, m, nm, arms, rnam, slots, edid, r.flags)

    plugin_case = {Path(p).name.lower(): Path(p).name
                   for p in ordered_plugin_paths}

    # ---- Pass 2: find target ARMOs + the ARMAs to mint ----
    # Target = non-body, DefaultRace-primary, non-hair-only ARMO whose winning
    # armatures ALL lack UBE coverage, with >=1 DefaultRace armature to mint.
    # Target = PLAYABLE, non-body, DefaultRace-primary, non-hair-only ARMO whose
    # winning armatures ALL lack UBE coverage, with >=1 DefaultRace armature.
    # We mint ONE UBE-primary ARMA per unique source armature (dedup), then a
    # SkyPatcher line adds it to each target ARMO at runtime (no ESP override of
    # the mod plugin -> no master explosion: the 4092-item set spanned 525
    # plugins, far over the 254 plugin-master limit).
    ARMO_NONPLAYABLE_FLAG = 0x00000004
    targets = []   # (armo_abs, defining_plugin_case, [arma_abs to mint])
    mint_set: dict = {}  # arma_abs -> placeholder (filled with minted fid later)
    for armo_abs, (apayload, am, an, arms, rnam, slots, edid, aflags) in armo_win.items():
        if aflags & ARMO_NONPLAYABLE_FLAG:
            continue                       # NPC-only — no UBE player wears it
        if slots & _DEFORMING_SLOTS_MASK:
            continue                       # body/hands/feet — needs mesh conversion
        if slots and (slots & _HAIR_ONLY_SLOTS) == slots:
            continue                       # hair/wig only — not equippable armor
        if rnam != DEFAULT_RACE:
            continue                       # beast/custom race — never UBE-extend
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        if any(v[4] for _x, v in winning):
            continue                       # already has a UBE armature
        # Mint only the DefaultRace armatures (human/mer). Adding human UBE races
        # to a beast armature crashes — skip non-DefaultRace armatures.
        to_mint = [x for x, v in winning if v[3] == DEFAULT_RACE]
        if not to_mint:
            continue
        targets.append((armo_abs, plugin_case.get(armo_abs[0], armo_abs[0]),
                        to_mint))
        for x in to_mint:
            mint_set.setdefault(x, None)

    # ---- Pass 3: mint ESP (UBE-primary ARMAs only; masters = vanilla + UBE) ----
    patch_masters = list(VANILLA_DLC_MASTERS)
    _add_master_if_missing(patch_masters, ube_allrace_filename)
    pidx = {m.lower(): i for i, m in enumerate(patch_masters)}
    own_byte = len(patch_masters)
    ube_byte = pidx[ube_allrace_filename.lower()]
    ube_races_patch = [(ube_byte << 24) | f for f in UBE_RACE_FIDS_24]
    ube_primary_patch = (ube_byte << 24) | UBE_PRIMARY_BRETON_FID_24

    # Drop every source-master FormID ref + texture-hash so the minted ARMA
    # references ONLY UBE_AllRace (races) + its mesh path strings — keeping the
    # mint ESP's master list tiny (no source plugin needed).
    STRIP = {b"SNDD", b"ONAM", b"MO2S", b"MO3S", b"MO4S", b"MO5S",
             b"MO2T", b"MO3T", b"MO4T", b"MO5T",
             # NAM0/NAM1 = male/female skin-texture TXST FormIDs; NAM2/NAM3 =
             # male/female texture-swap FLST FormIDs (confirmed via ARMA schema:
             # SkinTexture + TextureSwapList GenderedItems). When the minted ARMA
             # is copied from a MOD-override WINNER (USSEP/AOS/Requiem redirecting
             # skin TXSTs), these FormIDs live in the winner's master space, which
             # this patch (vanilla DLC + UBE_AllRace only) lacks -> stale/dangling
             # refs, same crash class as the MO?S/MO?T strip. Stripping falls back
             # to default skin textures — fine for race-coverage armatures.
             b"NAM0", b"NAM1", b"NAM2", b"NAM3"}
    new_arma_records: list[esp.Record] = []
    next_id = ESL_OWN_FORMID_MIN
    mint_name = out_path.with_suffix(".esp").name
    for arma_abs in mint_set:
        payload, m2, n2, _rn, _u = arma_win[arma_abs]
        stripped = b"".join(
            esp.encode_subrecord(s, d)
            for s, d in esp.iter_subrecords(payload) if s not in STRIP)
        minted_payload = rebuild_arma_payload(
            stripped,
            new_primary_rnam=ube_primary_patch,
            new_additional_race_fids=ube_races_patch,
            converted_nif_exists=lambda p: False,  # non-body: keep mesh path
        )
        new_fid = (own_byte << 24) | next_id
        next_id += 1
        new_edid = "UBE_MNB_{:X}".format(arma_abs[1])
        minted_payload = replace_arma_edid(minted_payload, new_edid[:90])
        new_arma_records.append(esp.Record(
            sig=b"ARMA", flags=0, formid=new_fid, timestamp_vc=0,
            version_unk=0x002C, payload=minted_payload))
        mint_set[arma_abs] = new_fid

    as_esl = len(new_arma_records) <= ESL_MAX_OWN_RECORDS
    tes4_flags = TES4_FLAG_ESL if as_esl else 0
    out_header = esp.TES4Header(
        masters=patch_masters, author=author, description=description,
        flags=tes4_flags, version=1.7, num_records=0,
        next_object_id=max(0x800, next_id))
    out_esp = esp.ESP(header=out_header, groups=[])
    if new_arma_records:
        out_esp.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))
    prune_unused_masters(out_esp)
    out_esp.save(out_path)
    warnings = validate_patch(out_path, master_data_dirs=master_data_dirs)

    # ---- Pass 4: SkyPatcher INI (add minted ARMA to each target ARMO) ----
    ini_lines = [
        "; cbbe-to-ube: UBE race coverage for mod-defined non-body armor.",
        "; Adds a minted UBE-primary ArmorAddon to each item whose winning",
        "; armature lacked UBE races (overhauls re-armature vanilla gear).",
    ]
    for armo_abs, defining_plugin, to_mint in targets:
        addons = [mint_set[x] for x in to_mint if mint_set.get(x) is not None]
        if not addons:
            continue
        adds = ",".join("{}|{:06X}".format(mint_name, (fid & 0xFFFFFF))
                        for fid in addons)
        ini_lines.append(
            "filterByArmors={}|{:06X}:armorAddonsToAdd={}".format(
                defining_plugin, armo_abs[1], adds))

    return {
        "output": str(out_path),
        "ini_lines": ini_lines,
        "masters": len(out_esp.header.masters),
        "minted_armas": len(new_arma_records),
        "armo_targets": len(targets),
        "esl_flagged": bool(tes4_flags & TES4_FLAG_ESL),
        "candidates_scanned": len(armo_win),
        "validation_warnings": warnings,
    }


def generate_modded_body_ube_coverage_patch(
    output_esp_path: "str | Path",
    ordered_plugin_paths: "list[Path]",
    *,
    converted_rel_paths: "set[str]",
    ube_allrace_filename: str = "UBE_AllRace.esp",
    exclude_names: "set[str] | None" = None,
    master_data_dirs: "list[Path] | None" = None,
    author: str = "cbbe-to-ube modded body UBE coverage",
    description: str = "UBE race coverage for mod-defined body armor variants",
) -> dict:
    """The BODY counterpart of generate_modded_nonbody_ube_coverage_patch.

    Overhauls (Requiem) add NEW armor-variant ARMO records -- e.g. "Orcish Light
    Cuirass" (REQ_Light_Orcish_Body) -- that REUSE a vanilla armature whose mesh
    we DID convert, but the variant ARMO itself was never overridden, so it has
    no UBE armature -> invisible on UBE actors. The vanilla ARMO got covered; the
    mod's separate variant ARMO slipped through (it's not vanilla, not a source
    mod, not non-body).

    For each load-order-WINNING playable body/hands/feet ARMO whose winning
    armatures all lack UBE coverage, this mints one UBE-primary ARMA per source
    armature -- with its model REDIRECTED to the converted `!UBE` mesh -- and a
    SkyPatcher line adds it. Only armatures whose mesh actually has a `!UBE`
    conversion (`converted_rel_paths`) are minted; pointing an ARMA at an
    unconverted CBBE mesh on a UBE actor would clip (or crash). Returns stats."""
    out_path = Path(output_esp_path)
    exclude = {n.lower() for n in (exclude_names or set())}
    DEFAULT_RACE = ("skyrim.esm", _DEFAULT_RACE_LOW24)
    crp = converted_rel_paths or set()

    def _conv_exists(model_path: str) -> bool:
        if not model_path:
            return False
        return model_path.replace("\\", "/").lstrip("/").lower() in crp

    def _arma_models(payload: bytes) -> "list[str]":
        return [d.rstrip(b"\x00").decode("utf-8", "ignore")
                for sig, d in esp.iter_subrecords(payload)
                if sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")]

    # ---- Pass 1: load-order winners for ARMA + ARMO (last wins) ----
    arma_win: dict = {}
    armo_win: dict = {}
    for path in ordered_plugin_paths:
        path = Path(path)
        if path.name.lower() in exclude:
            continue
        try:
            pe = esp.ESP.load(path)
        except Exception:
            continue
        m = pe.header.masters
        nm = path.name
        ag = pe.group(b"ARMA")
        if ag:
            for r in ag.records:
                a = _record_abs_fid(r.formid, m, nm)
                rnam, is_ube = _summarize_arma(r.payload, m, nm)
                arma_win[a] = (r.payload, m, nm, rnam, is_ube)
        og = pe.group(b"ARMO")
        if og:
            for r in og.records:
                a = _record_abs_fid(r.formid, m, nm)
                arms, rnam, slots, edid = _summarize_armo(r.payload, m, nm)
                armo_win[a] = (r.payload, m, nm, arms, rnam, slots, edid, r.flags)

    plugin_case = {Path(p).name.lower(): Path(p).name
                   for p in ordered_plugin_paths}

    # ---- Pass 2: target body/deforming ARMOs lacking UBE coverage whose mesh
    #      WAS converted ----
    ARMO_NONPLAYABLE_FLAG = 0x00000004
    targets = []          # (armo_abs, defining_plugin_case, [arma_abs to mint])
    mint_set: dict = {}
    for armo_abs, (apayload, am, an, arms, rnam, slots, edid, aflags) in armo_win.items():
        if aflags & ARMO_NONPLAYABLE_FLAG:
            continue
        if not (slots & _DEFORMING_SLOTS_MASK):
            continue                       # only body/hands/feet here (the inverse of non-body)
        if rnam != DEFAULT_RACE:
            continue                       # beast/custom race -> never UBE-extend
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        if any(v[4] for _x, v in winning):
            continue                       # already has a UBE armature (vanilla ARMO path)
        # mint only DefaultRace armatures whose mesh we actually converted
        to_mint = [x for x, v in winning
                   if v[3] == DEFAULT_RACE
                   and any(_conv_exists(mp) for mp in _arma_models(v[0]))]
        if not to_mint:
            continue
        targets.append((armo_abs, plugin_case.get(armo_abs[0], armo_abs[0]),
                        to_mint))
        for x in to_mint:
            mint_set.setdefault(x, None)

    # ---- Pass 3: mint ESP (UBE-primary ARMAs, models REDIRECTED to !UBE) ----
    patch_masters = list(VANILLA_DLC_MASTERS)
    _add_master_if_missing(patch_masters, ube_allrace_filename)
    pidx = {m.lower(): i for i, m in enumerate(patch_masters)}
    own_byte = len(patch_masters)
    ube_byte = pidx[ube_allrace_filename.lower()]
    ube_races_patch = [(ube_byte << 24) | f for f in UBE_RACE_FIDS_24]
    ube_primary_patch = (ube_byte << 24) | UBE_PRIMARY_BRETON_FID_24

    STRIP = {b"SNDD", b"ONAM", b"MO2S", b"MO3S", b"MO4S", b"MO5S",
             b"MO2T", b"MO3T", b"MO4T", b"MO5T",
             # NAM0/NAM1 = male/female skin-texture TXST FormIDs; NAM2/NAM3 =
             # male/female texture-swap FLST FormIDs (confirmed via ARMA schema:
             # SkinTexture + TextureSwapList GenderedItems). When the minted ARMA
             # is copied from a MOD-override WINNER (USSEP/AOS/Requiem redirecting
             # skin TXSTs), these FormIDs live in the winner's master space, which
             # this patch (vanilla DLC + UBE_AllRace only) lacks -> stale/dangling
             # refs, same crash class as the MO?S/MO?T strip. Stripping falls back
             # to default skin textures — fine for race-coverage armatures.
             b"NAM0", b"NAM1", b"NAM2", b"NAM3"}
    new_arma_records: list = []
    next_id = ESL_OWN_FORMID_MIN
    mint_name = out_path.with_suffix(".esp").name
    for arma_abs in mint_set:
        payload, m2, n2, _rn, _u = arma_win[arma_abs]
        stripped = b"".join(
            esp.encode_subrecord(s, d)
            for s, d in esp.iter_subrecords(payload) if s not in STRIP)
        minted_payload = rebuild_arma_payload(
            stripped,
            new_primary_rnam=ube_primary_patch,
            new_additional_race_fids=ube_races_patch,
            converted_nif_exists=_conv_exists,   # redirect model -> !UBE\ where converted
        )
        new_fid = (own_byte << 24) | next_id
        next_id += 1
        new_edid = "UBE_MBD_{:X}".format(arma_abs[1])
        minted_payload = replace_arma_edid(minted_payload, new_edid[:90])
        new_arma_records.append(esp.Record(
            sig=b"ARMA", flags=0, formid=new_fid, timestamp_vc=0,
            version_unk=0x002C, payload=minted_payload))
        mint_set[arma_abs] = new_fid

    as_esl = len(new_arma_records) <= ESL_MAX_OWN_RECORDS
    tes4_flags = TES4_FLAG_ESL if as_esl else 0
    out_header = esp.TES4Header(
        masters=patch_masters, author=author, description=description,
        flags=tes4_flags, version=1.7, num_records=0,
        next_object_id=max(0x800, next_id))
    out_esp = esp.ESP(header=out_header, groups=[])
    if new_arma_records:
        out_esp.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))
    prune_unused_masters(out_esp)
    out_esp.save(out_path)
    warnings = validate_patch(out_path, master_data_dirs=master_data_dirs)

    # ---- Pass 4: SkyPatcher INI (add minted ARMA to each target ARMO) ----
    ini_lines = [
        "; cbbe-to-ube: UBE race coverage for mod-defined BODY armor variants.",
        "; Adds a minted UBE-primary ArmorAddon (redirected to the converted",
        "; !UBE mesh) to each body item whose winning armature lacked UBE races",
        "; (e.g. Requiem 'Orcish Light Cuirass' reusing the vanilla armature).",
    ]
    for armo_abs, defining_plugin, to_mint in targets:
        addons = [mint_set[x] for x in to_mint if mint_set.get(x) is not None]
        if not addons:
            continue
        adds = ",".join("{}|{:06X}".format(mint_name, (fid & 0xFFFFFF))
                        for fid in addons)
        ini_lines.append(
            "filterByArmors={}|{:06X}:armorAddonsToAdd={}".format(
                defining_plugin, armo_abs[1], adds))

    return {
        "output": str(out_path),
        "ini_lines": ini_lines,
        "masters": len(out_esp.header.masters),
        "minted_armas": len(new_arma_records),
        "armo_targets": len(targets),
        "esl_flagged": bool(tes4_flags & TES4_FLAG_ESL),
        "candidates_scanned": len(armo_win),
        "validation_warnings": warnings,
    }


def merge_patches(
    patch_paths: list[Path],
    output_path: str | Path,
    *,
    esl_flag: bool = True,
    author: str = "cbbe-to-ube merger",
    description: str = "Merged UBE compatibility patches",
    master_data_dirs: "list[Path] | None" = None,
    armo_winner_index: "dict[tuple[str, int], _WinnerRecord] | None" = None,
) -> dict:
    """Combine multiple UBE patch ESPs into a single ESL-flagged ESP.

    Each input patch has:
      * Its own master list (Skyrim.esm + UBE_AllRace.esp + source mod's ESP)
      * Own-FormID records (new ARMAs we created): top byte = patch's own_byte
      * ARMO override records (source-ESP records we extend with UBE ARMAs):
        top byte = source ESP's master index in patch's master space
      * ARMO override records (master-ESM records like ArmorIronCuirass that
        we extended with UBE ARMAs via the master scan): top byte = master
        ESM's master index in patch's master space (typically Skyrim.esm = 0)

    Merge approach:
      1. Build the union of all master files across all input patches,
         in deterministic order (Skyrim.esm first, then official DLC,
         then UBE_AllRace.esp, then source mod ESPs alphabetically).
      2. For each input patch, build a master-byte remap from patch's
         master list -> merged master list. The patch's OWN top byte
         (= len(patch.masters)) maps to the merged plugin's own byte
         (= len(merged.masters)).
      3. For each new ARMA record in a patch (top byte = patch own_byte),
         assign a fresh own-FormID starting from 0x800 (ESL convention).
         Map old FormID -> new FormID in a global table.
      4. For each ARMO override (top byte != patch own_byte): remap top
         byte to merged master space. The record's payload's internal
         FormID references (MODL armatures, RNAM race, etc.) also get
         remapped — both master-byte translation AND own_byte -> new
         FormID translation via the global table built in step 3.
      5. Group all records by signature (ARMA, ARMO) and emit.
      6. Set TES4 ESL flag if `esl_flag=True` and count fits.

    Returns stats dict including per-input record counts + any warnings.
    """
    out_path = Path(output_path)
    patches: list[tuple[Path, esp.ESP]] = []
    for p in patch_paths:
        p = Path(p)
        if not p.is_file():
            raise FileNotFoundError(f"patch not found: {p}")
        patches.append((p, esp.ESP.load(p)))

    # ----- Step 0 (#132): resolve winner-rebase targets -----
    # For each ARMO override we'll emit, compute its absolute identity. If a
    # DIFFERENT, non-localized plugin wins that ARMO in the load order, rebase
    # the override on that winner so its stats/keywords/armatures survive. We
    # skip localized winners (their FULL is an LSTRING index that doesn't
    # travel) and the bare-master / same-source case (current behaviour already
    # correct). The winner's plugin + its masters must join the merged master
    # list so the remapped FormIDs resolve.
    rebase_map: dict[tuple[Path, int], _WinnerRecord] = {}
    rebase_count = 0
    if armo_winner_index:
        for patch_path, pe in patches:
            pmasters = pe.header.masters
            pname = patch_path.name
            for grp in pe.groups:
                if grp.label != b"ARMO":
                    continue
                for rec in grp.records:
                    abs_id = _record_abs_fid(rec.formid, pmasters, pname)
                    win = armo_winner_index.get(abs_id)
                    if win is None or win.is_localized:
                        continue
                    # Only rebase when a DIFFERENT plugin won (a third-party
                    # override). If the winner IS the defining master, the
                    # current base already equals the winner.
                    if win.plugin_name.lower() == abs_id[0]:
                        continue
                    rebase_map[(patch_path, rec.formid)] = win
                    rebase_count += 1

    # ----- Step 1: union of masters -----
    # CRITICAL master ordering rule: ALL master-tier plugins must come BEFORE
    # regular plugins in the master list, or the order contradicts the engine's
    # load order and FormIDs mis-resolve -> crash on load (especially for the
    # ESL-flagged output here). "Master-tier" is decided by the TES4 ESM FLAG
    # (0x1), NOT the file extension: .esm/.esl AND ESM-flagged .esp (USSEP and
    # countless overhauls) all load in the master block. The previous code
    # sorted by extension, so an ESM-flagged .esp landed after regular .esps
    # and crashed any modlist that has one (i.e. almost all of them).
    #
    # Within each tier preserve first-seen order across patches (stable sort)
    # so the output is deterministic.
    merged_masters: list[str] = []
    # Vanilla DLC ESMs first, canonical order — included UNCONDITIONALLY
    # (always loaded by every Skyrim install; transitive masters for almost
    # every source mod's records).
    for forced in VANILLA_DLC_MASTERS:
        _add_master_if_missing(merged_masters, forced)
    # Collect every other master once (first-seen across patches)...
    seen = {m.lower() for m in merged_masters}
    rest: list[str] = []
    for _, pe in patches:
        for m in pe.header.masters:
            if m.lower() not in seen:
                seen.add(m.lower())
                rest.append(m)
    # ...then stable-sort: ESM-tier (flag 0x1 / .esm / .esl) before regular.
    rest.sort(key=lambda m: 0 if _is_esm_tier_master(m, master_data_dirs) else 1)
    for m in rest:
        _add_master_if_missing(merged_masters, m)

    own_byte_merged = len(merged_masters)

    # ----- Step 2 + 3: assign new own-FormIDs to new ARMA records
    # and build the global FormID remap. -----
    # Key: (patch_path, old_full_formid) in the patch's space.
    # Value: new full FormID in merged space.
    formid_remap: dict[tuple[Path, int], int] = {}
    # Also: per-patch master_byte_remap: patch_byte -> merged_byte
    patch_master_remap: dict[Path, dict[int, int]] = {}

    next_own_id = ESL_OWN_FORMID_MIN

    # Decide ESL-vs-full BEFORE allocating: an ESL plugin can hold at most
    # ESL_MAX_OWN_RECORDS new records (own FormIDs 0x800-0xFFF). On a big
    # modlist the union of all per-source UBE ARMAs blows past that (a large
    # modlist = ~2700 own ARMAs). The OLD behaviour `raise RuntimeError` aborted the
    # whole merge -> the caller swallowed it -> a STALE Combined.esp was left
    # on disk and silently shipped (looked like "patcher didn't override the
    # player sets"). Instead, when the new records don't fit an ESL, DOWNGRADE
    # to a regular (non-ESL) ESP: own FormIDs then live in the full 24-bit
    # range (no 0xFFF ceiling), it costs one load-order slot but can never
    # overflow. We still prefer ESL when it fits (no slot cost).
    total_new_arma = 0
    for _pp, _pe in patches:
        _own = len(_pe.header.masters)
        for _grp in _pe.groups:
            if _grp.label != b"ARMA":
                continue
            for _rec in _grp.records:
                if ((_rec.formid >> 24) & 0xFF) == _own:
                    total_new_arma += 1
    fits_esl = total_new_arma <= ESL_MAX_OWN_RECORDS
    as_esl = esl_flag and fits_esl
    # Regular ESPs can use the whole 24-bit own-record space; keep 0x800 as the
    # start (conventional first usable own FormID) in either mode.
    own_id_ceiling = ESL_OWN_FORMID_MAX if as_esl else 0x00FFFFFF

    # Two-pass: first pass to allocate FormIDs for new ARMAs and learn
    # the patch's own_byte; second pass to do payload remapping.
    new_arma_records: list[esp.Record] = []
    armo_records: list[esp.Record] = []

    for patch_path, pe in patches:
        # Build master byte remap for this patch
        byte_remap: dict[int, int] = {}
        for i, m in enumerate(pe.header.masters):
            try:
                j = next(idx for idx, mn in enumerate(merged_masters)
                         if mn.lower() == m.lower())
                byte_remap[i] = j
            except StopIteration:
                # Should never happen since we added everything above
                continue
        # Patch's own_byte maps to merged own_byte
        patch_own_byte = len(pe.header.masters)
        byte_remap[patch_own_byte] = own_byte_merged
        patch_master_remap[patch_path] = byte_remap

        # First pass over this patch: collect new ARMAs and reserve
        # FormIDs for them.
        for grp in pe.groups:
            if grp.label != b"ARMA":
                continue
            for rec in grp.records:
                old_top = (rec.formid >> 24) & 0xFF
                if old_top == patch_own_byte:
                    # New ARMA — needs own-FormID in merged space
                    if next_own_id > own_id_ceiling:
                        raise RuntimeError(
                            "own-FormID space exhausted "
                            f"(ceiling 0x{own_id_ceiling:X}; "
                            f"{total_new_arma} new ARMAs)")
                    new_full = (own_byte_merged << 24) | next_own_id
                    next_own_id += 1
                    formid_remap[(patch_path, rec.formid)] = new_full
                else:
                    # Override of an existing ARMA in a master (rare)
                    new_top = byte_remap.get(old_top, old_top)
                    formid_remap[(patch_path, rec.formid)] = (
                        (new_top << 24) | (rec.formid & 0xFFFFFF))

    # ----- Step 4: build new records with FormIDs + payload remapped -----
    own_arma_count = 0
    for patch_path, pe in patches:
        byte_remap = patch_master_remap[patch_path]

        for grp in pe.groups:
            if grp.label not in (b"ARMA", b"ARMO"):
                continue
            for rec in grp.records:
                old_top = (rec.formid >> 24) & 0xFF

                # Compute new FormID for this record
                if (patch_path, rec.formid) in formid_remap:
                    new_fid = formid_remap[(patch_path, rec.formid)]
                else:
                    new_top = byte_remap.get(old_top, old_top)
                    new_fid = (new_top << 24) | (rec.formid & 0xFFFFFF)

                # Remap FormIDs inside the payload (always — the base override).
                new_payload = _rewrite_payload_for_merge(
                    rec.payload, patch_path, byte_remap, formid_remap)

                # #132: if a DIFFERENT, non-localized plugin wins this ARMO in
                # the load order, overlay its balance (armor rating/type/name/
                # keywords) onto our base override. Stats-only — keeps our base
                # armatures (+ UBE), adds no masters. See _overlay_winner_stats.
                win = rebase_map.get((patch_path, rec.formid)) \
                    if grp.label == b"ARMO" else None
                if win is not None:
                    new_payload = _overlay_winner_stats(
                        new_payload, win, merged_masters)

                new_rec = esp.Record(
                    sig=grp.label, flags=rec.flags, formid=new_fid,
                    timestamp_vc=rec.timestamp_vc,
                    version_unk=rec.version_unk,
                    payload=new_payload,
                )
                if grp.label == b"ARMA":
                    new_arma_records.append(new_rec)
                    if ((new_fid >> 24) & 0xFF) == own_byte_merged:
                        own_arma_count += 1
                else:
                    armo_records.append(new_rec)

    # ----- Step 5: emit merged ESP -----
    # Detect duplicate FormIDs across ARMO overrides — different patches
    # may have overridden the same Skyrim.esm record (rare but possible).
    seen_armo: dict[int, esp.Record] = {}
    deduped_armo: list[esp.Record] = []
    armo_dups = 0
    for rec in armo_records:
        if rec.formid in seen_armo:
            # Merge armatures lists from the duplicate into the existing one
            armo_dups += 1
            existing = seen_armo[rec.formid]
            existing.payload = _merge_armo_armatures(
                existing.payload, rec.payload)
        else:
            seen_armo[rec.formid] = rec
            deduped_armo.append(rec)

    # TES4 flags. `as_esl` was decided up-front from the total new-ARMA count
    # (own_arma_count here == total_new_arma); honour that decision so the flag
    # matches the FormID range we actually allocated into.
    tes4_flags = 0
    if as_esl:
        tes4_flags |= TES4_FLAG_ESL

    out_header = esp.TES4Header(
        masters=merged_masters,
        author=author,
        description=description,
        flags=tes4_flags,
        version=1.7,
        num_records=0,  # filled by save()
        next_object_id=next_own_id,
    )
    out_esp = esp.ESP(header=out_header, groups=[])
    if deduped_armo:
        out_esp.groups.append(esp.Group(label=b"ARMO", records=deduped_armo))
    if new_arma_records:
        out_esp.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))

    # Prune any unused masters that ended up in the union but no record
    # actually references (shouldn't happen normally, but safety).
    prune_unused_masters(out_esp)

    out_esp.save(out_path)

    return {
        "output": str(out_path),
        "masters": out_esp.header.masters,
        "merged_patch_count": len(patches),
        "total_arma_records": len(new_arma_records),
        "own_arma_records": own_arma_count,
        "total_armo_records": len(deduped_armo),
        "armo_duplicates_merged": armo_dups,
        "esl_flagged": bool(tes4_flags & TES4_FLAG_ESL),
        "esl_slots_used": own_arma_count,
        "esl_slots_max": ESL_MAX_OWN_RECORDS,
        "downgraded_to_full_esp": bool(esl_flag and not fits_esl),
        "winner_rebased_armos": rebase_count,
    }


def _partition_patches_for_esl(pinfo, cap):
    """Partition patches into pieces, each holding <= `cap` NEW ARMA records, so
    every piece can be ESL-flagged. `pinfo` is a list of (path, new_arma_count,
    abs_armo_id_set).

    Patches that override a SHARED ARMO are kept in the same piece (union-find),
    so merge_patches' cross-patch armature dedup still applies within a piece.
    Greedy bin-pack (largest group first). A single connected group whose own
    ARMAs exceed `cap` becomes its own over-cap piece -- the caller's
    merge_patches then downgrades just that one piece to a non-ESL ESP; the rest
    stay ESL. Returns a list of piece patch-path lists."""
    n = len(pinfo)
    parent = list(range(n))

    def find(i):
        r = i
        while parent[r] != r:
            r = parent[r]
        while parent[i] != r:
            parent[i], i = r, parent[i]
        return r

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    first_owner: dict = {}
    for i, (_p, _n, ids) in enumerate(pinfo):
        for aid in ids:
            owner = first_owner.get(aid)
            if owner is None:
                first_owner[aid] = i
            else:
                union(i, owner)

    comps: dict = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    groups = [(members, sum(pinfo[i][1] for i in members))
              for members in comps.values()]
    groups.sort(key=lambda g: -g[1])

    pieces: list = []  # each: [list_of_paths, running_new_count]
    for members, gnew in groups:
        gpaths = [pinfo[i][0] for i in members]
        placed = False
        for piece in pieces:
            if piece[1] + gnew <= cap:
                piece[0].extend(gpaths)
                piece[1] += gnew
                placed = True
                break
        if not placed:
            pieces.append([list(gpaths), gnew])
    return [p[0] for p in pieces]


def merge_patches_split(
    patch_paths,
    output_path,
    *,
    esl_flag: bool = True,
    author: str = "cbbe-to-ube merger",
    description: str = "Merged UBE compatibility patches",
    master_data_dirs=None,
    armo_winner_index=None,
) -> dict:
    """Merge UBE patch ESPs while keeping the result ESL-flagged.

    If the total NEW-ARMA count fits one ESL plugin (<= ESL_MAX_OWN_RECORDS),
    this behaves exactly like merge_patches (a single Combined). Otherwise it
    SPLITS the patches into multiple ESL-flagged pieces -- `<stem>.esp`,
    `<stem>2.esp`, `<stem>3.esp`, ... -- each under the cap, instead of
    downgrading to one non-ESL ESP that costs a load-order slot. Patches sharing
    an overridden ARMO stay in the same piece (so cross-patch armature dedup is
    preserved), and each piece restarts its own-FormID space at 0x800, so every
    piece is independently ESL-clean and they do NOT master each other.

    Returns aggregate stats with `pieces` (file names) + `piece_stats`."""
    out_path = Path(output_path)
    plist = [Path(p) for p in patch_paths]
    for p in plist:
        if not p.is_file():
            raise FileNotFoundError(f"patch not found: {p}")

    def _single(esl):
        s = merge_patches(
            plist, out_path, esl_flag=esl, author=author,
            description=description, master_data_dirs=master_data_dirs,
            armo_winner_index=armo_winner_index)
        s["pieces"] = [out_path.name]
        s["split_pieces"] = 1
        s["piece_stats"] = [s]
        return s

    if not esl_flag:
        return _single(False)

    # Quick scan: per-patch new-ARMA count + overridden-ARMO identities.
    pinfo = []
    total_new = 0
    for p in plist:
        pe = esp.ESP.load(p)
        own = len(pe.header.masters)
        n_new = 0
        armo_ids = set()
        for grp in pe.groups:
            if grp.label == b"ARMA":
                for rec in grp.records:
                    if ((rec.formid >> 24) & 0xFF) == own:
                        n_new += 1
            elif grp.label == b"ARMO":
                for rec in grp.records:
                    armo_ids.add(_record_abs_fid(
                        rec.formid, pe.header.masters, p.name))
        pinfo.append((p, n_new, armo_ids))
        total_new += n_new

    if total_new <= ESL_MAX_OWN_RECORDS:
        return _single(True)

    # Over the cap -> split into ESL pieces.
    piece_path_lists = _partition_patches_for_esl(pinfo, ESL_MAX_OWN_RECORDS)
    stem, suffix, parent_dir = out_path.stem, (out_path.suffix or ".esp"), out_path.parent
    n_pieces = len(piece_path_lists)
    piece_stats, piece_names = [], []
    for idx, ppaths in enumerate(piece_path_lists):
        piece_path = out_path if idx == 0 else parent_dir / f"{stem}{idx + 1}{suffix}"
        st = merge_patches(
            ppaths, piece_path, esl_flag=True, author=author,
            description=f"{description} (part {idx + 1}/{n_pieces})",
            master_data_dirs=master_data_dirs, armo_winner_index=armo_winner_index)
        piece_stats.append(st)
        piece_names.append(piece_path.name)

    # Remove stale pieces left by a prior, larger split (e.g. 3 -> 2 pieces).
    keep = set(piece_names)
    for f in parent_dir.glob(f"{stem}*{suffix}"):
        if f.name not in keep:
            try:
                f.unlink()
            except OSError:
                pass

    return {
        "output": str(out_path),
        "pieces": piece_names,
        "split_pieces": n_pieces,
        "merged_patch_count": len(plist),
        "masters": piece_stats[0].get("masters", []),
        "total_arma_records": sum(s.get("total_arma_records", 0) for s in piece_stats),
        "own_arma_records": sum(s.get("own_arma_records", 0) for s in piece_stats),
        "total_armo_records": sum(s.get("total_armo_records", 0) for s in piece_stats),
        "armo_duplicates_merged": sum(s.get("armo_duplicates_merged", 0) for s in piece_stats),
        "esl_flagged": all(s.get("esl_flagged") for s in piece_stats),
        "all_pieces_esl": all(s.get("esl_flagged") for s in piece_stats),
        "esl_slots_max": ESL_MAX_OWN_RECORDS,
        "downgraded_to_full_esp": any(s.get("downgraded_to_full_esp") for s in piece_stats),
        "winner_rebased_armos": sum(s.get("winner_rebased_armos", 0) for s in piece_stats),
        "piece_stats": piece_stats,
    }


def _rewrite_payload_for_merge(
    payload: bytes,
    patch_path: Path,
    byte_remap: dict[int, int],
    formid_remap: dict[tuple[Path, int], int],
) -> bytes:
    """Rewrite all FormID references in a record's payload from one
    patch's master-space into the merged master space.

    For each FormID subrecord:
      1. If (patch_path, fid) is in formid_remap, use the new FormID.
         (This handles new ARMAs that got assigned fresh own-FormIDs
         in the merged ESL space.)
      2. Otherwise, remap just the master byte via byte_remap.
    """
    def _remap_one(fid: int) -> int:
        if (patch_path, fid) in formid_remap:
            return formid_remap[(patch_path, fid)]
        top = (fid >> 24) & 0xFF
        if top in byte_remap:
            return (byte_remap[top] << 24) | (fid & 0xFFFFFF)
        return fid

    out = b""
    for sig, data in esp.iter_subrecords(payload):
        if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4:
            fid = struct.unpack("<I", data)[0]
            out += esp.encode_subrecord(sig, struct.pack("<I", _remap_one(fid)))
        elif sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0:
            new_data = b""
            for i in range(0, len(data), 4):
                fid = struct.unpack_from("<I", data, i)[0]
                new_data += struct.pack("<I", _remap_one(fid))
            out += esp.encode_subrecord(sig, new_data)
        elif sig in ALT_TEXTURE_SIGS:
            # Embedded TXST FormIDs in alternate-texture-set subrecord.
            # Without this remap, color-variant ARMOs in different mods'
            # patches point at the wrong master after merging — all
            # variants render the same color.
            new_data = _remap_alt_texture_payload(data, _remap_one)
            out += esp.encode_subrecord(sig, new_data)
        elif sig in ARMA_MODT_SIGS:
            # Final safety net: normalize headerless/old-format MODT so a
            # re-merge of pre-fix per-source patches can't reintroduce the
            # 7.5M-entry overread CTD. See normalize_modt().
            out += esp.encode_subrecord(sig, normalize_modt(data))
        else:
            out += esp.encode_subrecord(sig, data)
    return out


def _merge_armo_armatures(existing_payload: bytes,
                          additional_payload: bytes) -> bytes:
    """When two patches override the same ARMO (e.g. both extended
    `ArmorIronCuirass` with their own UBE ARMA), merge their armature
    lists. The existing payload's MODL entries (armatures) get the
    additional payload's MODL entries APPENDED (deduped).

    Other subrecords (FULL/DESC/BOD2/etc.) are kept from the existing
    payload — they should be identical between overrides since both
    patches saw the same master record.
    """
    existing_modls: list[bytes] = []
    seen_fids: set[int] = set()
    for sig, data in esp.iter_subrecords(existing_payload):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            fid = struct.unpack("<I", data)[0]
            if fid not in seen_fids:
                seen_fids.add(fid)
                existing_modls.append(data)
    for sig, data in esp.iter_subrecords(additional_payload):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            fid = struct.unpack("<I", data)[0]
            if fid not in seen_fids:
                seen_fids.add(fid)
                existing_modls.append(data)

    # Rebuild the payload: copy non-MODL subrecords from existing, but
    # emit the merged MODLs in the SAME POSITION as the original MODLs
    # were (canonical ARMO order has them grouped before DATA/DNAM).
    # Putting MODLs after DATA makes Skyrim's parser silently ignore
    # them — see add_arma_to_armo_payload and master-override path for
    # the same fix and full rationale.
    existing_pieces = list(esp.iter_subrecords(existing_payload))
    first_modl_idx = -1
    for i, (sig, data) in enumerate(existing_pieces):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            if first_modl_idx < 0:
                first_modl_idx = i
                break

    out = b""
    if first_modl_idx >= 0:
        # Emit non-MODL subrecords; when we reach the first original
        # MODL position, dump ALL merged MODLs there (skipping the
        # individual original MODLs they replace).
        for i, (sig, data) in enumerate(existing_pieces):
            if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                if i == first_modl_idx:
                    for modl_data in existing_modls:
                        out += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG, modl_data)
                # else skip — already emitted above
            else:
                out += esp.encode_subrecord(sig, data)
    else:
        # Existing payload had no MODL — splice merged MODLs before
        # DATA. Fall back to appending if no DATA either.
        inserted = False
        for sig, data in existing_pieces:
            if sig == b"DATA" and not inserted:
                for modl_data in existing_modls:
                    out += esp.encode_subrecord(
                        ARMO_ARMATURE_SIG, modl_data)
                inserted = True
            out += esp.encode_subrecord(sig, data)
        if not inserted:
            for modl_data in existing_modls:
                out += esp.encode_subrecord(
                    ARMO_ARMATURE_SIG, modl_data)
    return out
