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

import os
import struct
from pathlib import Path
from typing import Iterable

from . import esp


def _full_skypatcher_enabled() -> bool:
    """CBBE2UBE_FULL_SKYPATCHER=1 (default OFF): deliver ALL converted-armor
    coverage via SkyPatcher armorAddonsToAdd instead of ESP ARMO overrides.
    The per-source patch still MINTS the same UBE armatures (identical race
    routing incl. hands/feet source-primary), but emits NO ARMO overrides --
    it records links (defining plugin + ARMO id -> minted armatures) that the
    merge turns into INI lines against final Combined FormIDs. The Combined is
    then pure minted-ARMA: it overrides no third-party records, so the whole
    override-conflict class (winner rebase, flags, keywords, EITM/VMAD,
    localized strings) does not exist. Supersedes CBBE2UBE_BODY_SKYPATCHER."""
    return (os.environ.get("CBBE2UBE_FULL_SKYPATCHER", "").strip().lower()
            in ("1", "true", "yes", "on"))


def _body_skypatcher_enabled() -> bool:
    """CBBE2UBE_BODY_SKYPATCHER=1 routes converted TORSO (biped slot 32) body
    armor through SkyPatcher coverage (a minted UBE armature added at runtime via
    armorAddonsToAdd) instead of an ESP ARMO override. Default OFF. Read at call
    time so the GUI / auto_convert can toggle it per run. Hands/feet (33/37) keep
    their source-primary ESP-override routing regardless (nude-hands race match)."""
    return (os.environ.get("CBBE2UBE_BODY_SKYPATCHER", "").strip().lower()
            in ("1", "true", "yes", "on"))


# --------------------------------------------------------------------------
# Batch caches (main process only)
#
# generate_ube_patch is called once per source mod; these three caches make
# expensive work (parsing 250MB Skyrim.esm, walking thousands of plugins for
# UBE races, building STRINGS resolvers) happen once per batch instead of
# once per mod. All are read-only after population; workers never touch them.
# clear_batch_caches() resets them between batches.
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


# UBE race FormIDs in UBE_AllRace.esp's own master space (top byte 0x03;
# 3 masters: Skyrim, Update, Dawnguard). Bottom 24 bits only; master byte added at use.
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

# Texture-hash subrecords that follow each model (MOD2->MO2T, etc.).
# SSE format: u32 version, u32 count, u32 unknown, then count * 12-byte entries
# {u32 fileHash, char[4] ext, u32 folderHash}; valid iff len == 12 * (1 + count).
ARMA_MODT_SIGS = (b"MO2T", b"MO3T", b"MO4T", b"MO5T")

# A valid empty MODT (version=2, count=0). Texture hashes have no runtime
# rendering effect (textures are read from the NIF), so an empty block is safe.
_EMPTY_MODT = struct.pack("<III", 2, 0, 0)


def normalize_modt(data: bytes) -> bytes:
    """Return a structurally valid SSE MODT.

    Old/LE-ported mods ship headerless MO?T blocks (raw 12-byte texture
    entries, no version/count/unknown prefix). The engine reads the count
    from offset 4, which lands on the first entry's "dds\\0" extension bytes
    (0x00736464 = 7,562,340 entries) -> overread -> EXCEPTION_ACCESS_VIOLATION
    at model init (startup CTD). A valid MODT satisfies len == 12*(1+count@4).
    If it doesn't, replace with the empty placeholder."""
    if len(data) >= 12:
        count = struct.unpack_from("<I", data, 4)[0]
        if len(data) == 12 * (1 + count):
            return data
    return _EMPTY_MODT


# Vanilla DLC ESMs in canonical load order. Included unconditionally in
# every patch's master list: source mods often reference DLC content even
# when they don't formally list them as masters. Omitting them causes
# FormID misroutes through the wrong master, which crashes on startup.
VANILLA_DLC_MASTERS = (
    "Skyrim.esm", "Update.esm", "Dawnguard.esm",
    "HearthFires.esm", "Dragonborn.esm",
)


# Additional-race FormIDs in ARMA records use the MODL signature — the same
# signature ARMO uses for armature refs, but in ARMA it's a 4-byte FormID.
ARMA_ADDITIONAL_RACE_SIG = b"MODL"


# Subrecords whose payload is a single 4-byte FormID. Used by the master-prune
# pass to know what to renumber. Conservative set: ARMA + ARMO refs we carry.
# NAM0-3 are ARMA skin-texture TXST FormIDs and must be remapped; omitting them
# causes exposed skin to reference the wrong TextureSet (the master-byte remap
# skips everything not in this set). EAMT is u16, not a FormID, and is excluded.
FORMID_SINGLE_SUBRECORD_SIGS = {
    # ARMA
    b"RNAM",   # primary race
    b"MODL",   # additional race (in ARMA) / armature ref (in ARMO)
    b"SNDD",   # footstep sound set
    # ARMA skin-texture TXST refs (male/female 3rd/1st-person).
    b"NAM0", b"NAM1", b"NAM2", b"NAM3",
    # ARMO
    b"ZNAM",   # pickup sound
    b"YNAM",   # putdown sound
    b"ETYP",   # equip slot
    b"BIDS",   # block bash impact data
    b"BAMT",   # alt block material
    b"TNAM",   # template armor
    b"EITM",   # enchantment
    b"EAMT",   # enchantment amount -- discarded below (u16, not a FormID)
}
# EAMT is u16, not a FormID
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

# Alternate-texture subrecords contain embedded TXST FormID references.
# Format: count(u32) + N * {name_len(u32) + name(N bytes) + TXST_FormID(u32) + index(u32)}.
# The standard FormID remap can't reach these nested refs; without explicit
# remapping, all color-variant ARMOs hit the wrong master's TXST and render
# with the same default texture.
ALT_TEXTURE_SIGS = (b"MO2S", b"MO3S", b"MO4S", b"MO5S")


def _reindex_alt_texture_payload(data: bytes,
                                 shape_index: "dict[str, int]") -> "bytes | None":
    """Rewrite an MO?S alt-texture set to match a CONVERTED NIF's shapes.

    Each entry is (3D name, TXST FormID, 3D index). After conversion merges
    or reorders shapes, source indices are stale: variants recolor the wrong
    shape (or hit an out-of-range index = no effect). For each entry:
      * name still in NIF -> keep, update index to its real position;
      * name merged away -> DROP (its geometry lives in a surviving shape that
        carries the same TXST for that variant);
      * de-dupe by name (one entry per surviving shape).
    `shape_index` = {shape_name: index} from the converted NIF.
    Returns rebuilt payload, or None on parse failure (caller keeps original)."""
    try:
        n = struct.unpack_from("<I", data, 0)[0]
        p = 4
        entries = []
        for _ in range(n):
            nl = struct.unpack_from("<I", data, p)[0]; p += 4
            name = data[p:p + nl]; p += nl
            txst = struct.unpack_from("<I", data, p)[0]; p += 4
            p += 4   # skip the (unused here) 3D-index field
            entries.append((name, txst))
    except Exception:
        return None
    # Case-INSENSITIVE shape-name match: alt-texture sets are authored by hand and
    # frequently disagree in case with the actual NIF shape name (e.g. an entry
    # named 'hood' for a shape named 'Hood'). The engine applies the recolor by
    # the 3D INDEX, so a case-only mismatch must NOT drop the entry -- it just
    # needs its index reconciled. The old case-SENSITIVE get() silently DROPPED
    # such entries, losing the color variant for that shape (the recolored hood
    # rendered in its BASE color while its correctly-cased siblings recolored
    # fine). Keep the original authored name bytes; only fix the index. #alttex-case
    ci_index: "dict[str, int]" = {}
    for _k, _v in shape_index.items():
        ci_index.setdefault(_k.lower(), _v)   # first wins on case-dupes (rare)
    seen: set[str] = set()
    kept = []
    for name, txst in entries:
        nm = name.split(b"\x00", 1)[0].decode("latin-1", "ignore").lower()
        new_idx = ci_index.get(nm)
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

    The converter merges/reorders NIF shapes but the ESP's alt-texture sets
    still carry the source NIF's names and indices. Stale indices recolor the
    wrong shapes or fall out of range (no effect). This reloads each ARMA's
    converted NIF and rewrites the alt-texture set to the surviving shapes'
    real names+indices. Returns number of ARMA records fixed.
    Run AFTER NIF conversion + merge."""
    from pathlib import Path as _Path
    from . import nif_io
    meshes_root = _Path(meshes_root)
    e = esp.ESP.load(esp_path)
    _cache: "dict[str, dict | None]" = {}
    # Converted NIFs that EXIST but won't load: their alt-texture set keeps the
    # stale source indices (color variants misalign). Distinct from a legitimately
    # absent path (vanilla mesh the converter doesn't own) -- surface only these.
    load_failed: "list[str]" = []

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
            load_failed.append(model_path)
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
    if load_failed:
        import sys as _s
        print(f"  !! alt-texture reconcile: {len(load_failed)} converted NIF(s) "
              f"failed to load -> stale color-variant indices kept (variant "
              f"textures may misalign): {sorted(set(load_failed))[:5]}",
              file=_s.stderr)
    if fixed:
        e.save(esp_path)
    return fixed


def reconcile_alt_texture_indices_all(primary_esp_path, meshes_root) -> int:
    """Reconcile alt-texture indices across the primary merged ESP AND every
    ESL-split overflow piece (`<stem>.esp`, `<stem>2.esp`, ...).

    merge_patches_split may spill records into sibling pieces; those pieces
    carry alt-texture sets that also need reconciliation. Globs the same
    `<stem>*<suffix>` family the split writer uses. Returns total records fixed."""
    from pathlib import Path as _Path
    p = _Path(primary_esp_path)
    total = 0
    for piece in sorted(p.parent.glob(f"{p.stem}*{p.suffix}")):
        total += reconcile_alt_texture_indices(piece, meshes_root)
    return total


# ----- redundant armature dedup (double body-swap render) -----------------
# A body-armor ARMO can end up referencing TWO of THIS patch's own UBE ARMAs that
# carry the SAME primary race (RNAM) AND the SAME male/female meshes (MOD2/MOD3) --
# e.g. when the armor's source ARMA was overridden by both a master and a patch and
# each path contributed a mint, and the merge dedups whole ARMO records but not the
# armature refs WITHIN one. For a UBE-race wearer BOTH resolve, so the engine renders
# the same body-swap mesh twice, overlapping -> the doubled/blown-out, double-morphed
# render reads in-game as "the armor doesn't fit the body / doesn't conform".

def _arma_dedup_identity(arma_payload: bytes):
    """(rnam, mod2_lower, mod3_lower, subrecord_count) for an ARMA payload --
    the render identity (race + meshes) plus a completeness tiebreak."""
    rnam = mod2 = mod3 = None
    n = 0
    for sig, d in esp.iter_subrecords(arma_payload):
        n += 1
        if sig == b"RNAM":
            rnam = d
        elif sig == b"MOD2":
            mod2 = d.rstrip(b"\x00").lower()
        elif sig == b"MOD3":
            mod3 = d.rstrip(b"\x00").lower()
    return (rnam, mod2, mod3, n)


def dedup_armo_armature_refs(esp_path) -> int:
    """In each ARMO, drop redundant armature refs that point at THIS patch's OWN
    ARMAs sharing the same (RNAM, MOD2, MOD3) -- keeping the most complete one
    (most subrecords). Vanilla/master armatures are never touched. Returns the
    number of refs removed. Run AFTER the merge. See the block comment above."""
    e = esp.ESP.load(esp_path)
    own_byte = len(e.header.masters)
    own_arma_identity: "dict[int, tuple]" = {}
    for g in e.groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            if ((r.formid >> 24) & 0xFF) == own_byte:
                own_arma_identity[r.formid] = _arma_dedup_identity(r.payload)
    removed = 0
    for g in e.groups:
        if g.label != b"ARMO":
            continue
        for r in g.records:
            refs = [struct.unpack("<I", d)[0]
                    for sig, d in esp.iter_subrecords(r.payload)
                    if sig == b"MODL" and len(d) == 4]
            if len(refs) < 2:
                continue
            # group OWN armature refs by render identity; a group with >1 member
            # is a duplicate -> keep the most-complete fid, drop the rest.
            by_key: "dict[tuple, list]" = {}
            for fid in refs:
                ident = own_arma_identity.get(fid)
                if ident is None:
                    continue                 # vanilla/master ARMA -> leave alone
                by_key.setdefault(ident[:3], []).append((fid, ident[3]))
            keeper_for: "dict[tuple, int]" = {}
            for key, members in by_key.items():
                if len(members) < 2:
                    continue
                members.sort(key=lambda m: m[1], reverse=True)   # most complete first
                keeper_for[key] = members[0][0]
            if not keeper_for:
                continue
            # Rebuild: for a duplicated identity, keep ONLY the keeper fid's FIRST
            # occurrence (handles the same-fid-listed-twice case: a set-based drop
            # would remove BOTH and leave the ARMO armature-less -> invisible).
            emitted: set = set()
            out = b""
            for sig, d in esp.iter_subrecords(r.payload):
                if sig == b"MODL" and len(d) == 4:
                    fid = struct.unpack("<I", d)[0]
                    ident = own_arma_identity.get(fid)
                    if ident is not None and ident[:3] in keeper_for:
                        key = ident[:3]
                        if fid == keeper_for[key] and key not in emitted:
                            emitted.add(key)
                        else:
                            removed += 1
                            continue          # redundant duplicate -> drop
                out += esp.encode_subrecord(sig, d)
            r.payload = out
    if removed:
        e.save(esp_path)
    return removed


def dedup_armo_armature_refs_all(primary_esp_path) -> int:
    """Dedup armature refs across the primary merged ESP AND every ESL-split
    piece (`<stem>.esp`, `<stem>2.esp`, ...). Returns total refs removed."""
    from pathlib import Path as _Path
    p = _Path(primary_esp_path)
    total = 0
    for piece in sorted(p.parent.glob(f"{p.stem}*{p.suffix}")):
        total += dedup_armo_armature_refs(piece)
    return total


# ----- spurious hands-slot fix (invisible hands) --------------------------
# A forearm bracer that claims biped slot 33 (Hands) but has no hand geometry
# causes the engine to hide the actor's nude-hands skin and draw nothing instead
# -> invisible hands. Detection: real gloves have 71-97% hand-bone vertex weight;
# bracers have ~0-4%. A 10% threshold cleanly separates them.
_HAND_BONE_SUBSTRINGS = ("Finger", "Thumb", "Hand")
_HAND_WEIGHT_FRAC_CACHE: "dict[str, float | None]" = {}


def _nif_max_hand_weight_fraction(nif_path) -> "float | None":
    """Max over the NIF's shapes of (hand/finger/thumb bone weight / total weight).
    ~0 for a bracer, high for a glove. Returns None if unreadable -- callers treat
    None as "assume hands present" so a real glove is never stripped. Cached."""
    key = str(nif_path)
    if key in _HAND_WEIGHT_FRAC_CACHE:
        return _HAND_WEIGHT_FRAC_CACHE[key]
    frac: "float | None" = None
    try:
        from pyn import pynifly  # type: ignore
        nf = pynifly.NifFile(filepath=str(nif_path))
        best = 0.0
        for s in nf.shapes:
            bw = getattr(s, "bone_weights", None) or {}
            total = 0.0
            hand = 0.0
            for bone_name, pairs in bw.items():
                w = sum(float(x) for _, x in pairs)
                total += w
                if any(h in bone_name for h in _HAND_BONE_SUBSTRINGS):
                    hand += w
            if total > 0:
                best = max(best, hand / total)
        frac = best
    except Exception:
        frac = None
    _HAND_WEIGHT_FRAC_CACHE[key] = frac
    return frac


def fix_spurious_hand_slot(primary_esp_path, meshes_root, *,
                           threshold: float = 0.10) -> dict:
    """Post-merge pass: clear biped slot 33 (Hands) from handless forearm armor
    so it stops hiding the nude hands. Two passes:
      1. ARMO-level: an ARMO that claims slot 33 whose local armatures ALL have
         no hand geometry -> clear slot 33 from the ARMO and those armatures.
      2. ARMA-level: any armature that carries a stray slot-33 bit alongside
         another slot (e.g. a converted vambrace tagged [33,34]) but whose mesh
         is handless -> clear slot 33 from that armature, even when the owning
         ARMO is correctly NOT slot 33 (so pass 1 never reaches it).

    Fail-safe: strip only when the mesh is positively confirmed hand-less (mesh
    resolved and fraction < threshold). Unreadable/unresolved meshes and
    [33]-only armatures (gloves, nude hand skins) -> left untouched.

    Runs across the primary ESP + every ESL-split piece. Returns a stats dict."""
    from pathlib import Path as _Path
    HANDS_BIT = 1 << (33 - 30)   # slot 33
    BODY_BIT = 1 << (32 - 30)    # slot 32
    meshes_root = _Path(meshes_root)
    p = _Path(primary_esp_path)
    armos_fixed = armas_fixed = pieces_changed = 0

    def _resolve(model: str):
        if not model:
            return None
        rp = meshes_root / model.replace("/", "\\")
        return rp if rp.is_file() else None

    def _armature_hand_status(models) -> str:
        """'hands' | 'handless' | 'unknown' for one armature's model list."""
        saw_handless = False
        for m in models:
            rp = _resolve(m)
            if rp is None:
                continue
            frac = _nif_max_hand_weight_fraction(rp)
            if frac is None:
                return "unknown"      # unreadable -> assume hands (don't strip)
            if frac >= threshold:
                return "hands"        # real glove/gauntlet -> never strip
            saw_handless = True
        return "handless" if saw_handless else "unknown"

    for piece in sorted(p.parent.glob(f"{p.stem}*{p.suffix}")):
        try:
            e = esp.ESP.load(piece)
        except Exception:
            continue
        arma_by_fid: "dict[int, esp.Record]" = {}
        arma_models: "dict[int, list[str]]" = {}
        for g in e.groups:
            if g.label == b"ARMA":
                for r in g.records:
                    arma_by_fid[r.formid] = r
                    arma_models[r.formid] = [
                        d.rstrip(b"\x00").decode("utf-8", "ignore")
                        for sig, d in esp.iter_subrecords(r.payload)
                        if sig in (b"MOD3", b"MOD2", b"MOD4", b"MOD5")]
        changed = False
        for g in e.groups:
            if g.label != b"ARMO":
                continue
            for r in g.records:
                slots = None
                arms: list[int] = []
                for sig, d in esp.iter_subrecords(r.payload):
                    if sig in (b"BOD2", b"BODT") and len(d) >= 4 and slots is None:
                        slots = struct.unpack_from("<I", d, 0)[0]
                    elif sig == ARMO_ARMATURE_SIG and len(d) == 4:
                        arms.append(struct.unpack("<I", d)[0])
                if slots is None or not (slots & HANDS_BIT):
                    continue
                if slots & BODY_BIT:
                    # A piece claiming slot 32 (Body) alongside Hands is a
                    # full-body suit or skin — its hand geometry lives in a
                    # different armature. A real bracer never claims the body
                    # slot; this guard excludes body skins and robes.
                    continue
                local = [a for a in arms if a in arma_models]
                if not local:
                    continue   # no local armature to inspect -> leave alone
                # Never touch a nude-skin armature (its hand mesh IS the hands).
                if any(_is_nude_skin_model(m)
                       for a in local for m in arma_models[a]):
                    continue
                statuses = [_armature_hand_status(arma_models[a]) for a in local]
                if not all(s == "handless" for s in statuses):
                    continue   # any hands/unknown -> never strip (fail-safe)
                np_, ch = clear_slot33_from_bod2_payload(r.payload)
                if ch:
                    r.payload = np_
                    armos_fixed += 1
                    changed = True
                for a in local:
                    rec = arma_by_fid.get(a)
                    if rec is None:
                        continue
                    np2, ch2 = clear_slot33_from_bod2_payload(rec.payload)
                    if ch2:
                        rec.payload = np2
                        armas_fixed += 1
                        changed = True
        # ARMA-level pass: a stray slot-33 (Hands) bit on an armature whose mesh
        # has no hand geometry hides the nude hands and draws nothing there --
        # even when the owning ARMO is correctly NOT a hands item, so the ARMO
        # loop above never reaches it. Seen on converted vambraces tagged [33,34]
        # over a slot-34-only source. Only fires for multi-slot armatures
        # (slot 33 + another slot) with a handless mesh, so [33]-only gloves and
        # nude hand skins (high hand-weight) are never touched.
        for fid, rec in arma_by_fid.items():
            aslots = None
            for sig, d in esp.iter_subrecords(rec.payload):
                if sig in (b"BOD2", b"BODT") and len(d) >= 4:
                    aslots = struct.unpack_from("<I", d, 0)[0]
                    break
            if aslots is None or not (aslots & HANDS_BIT) or not (aslots & ~HANDS_BIT):
                continue
            if aslots & BODY_BIT:
                continue   # body suit/skin: hands come from the suit (mirror pass 1)
            models = arma_models.get(fid, [])
            if any(_is_nude_skin_model(m) for m in models):
                continue
            if _armature_hand_status(models) != "handless":
                continue   # real hand geometry or unreadable -> never strip
            np2, ch2 = clear_slot33_from_bod2_payload(rec.payload)
            if ch2:
                rec.payload = np2
                armas_fixed += 1
                changed = True
        if changed:
            e.save(piece)
            pieces_changed += 1
    return {"armos_fixed": armos_fixed, "armas_fixed": armas_fixed,
            "pieces_changed": pieces_changed}


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
    """Scan ESP(s) for every shape name targeted by an ARMO/ARMA alt-texture
    set (MO?S 3D-name field). Used by reconcile_alt_texture_indices to repair
    stale MO?S indices after NIF conversion reorders shapes.
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
    """ARMA DNAM byte 0 = Male Priority, byte 1 = Female Priority.
    UBE is female-only: ensure female priority >= male priority and non-zero,
    so male-only armatures render on female actors. Other DNAM fields untouched."""
    if len(dnam) < 2:
        return dnam
    b = bytearray(dnam)
    b[1] = max(b[1], b[0], 1)
    return bytes(b)


# ARMA skin-texture swap subrecords (male/female 3rd/1st-person TXST refs).
# Unlike MO?S, rebuild_arma_payload copies these verbatim, so when the
# SkyPatcher body-coverage path preserves them it must remap them itself.
_ARMA_SKIN_TXST_SIGS = (b"NAM0", b"NAM1", b"NAM2", b"NAM3")


def _arma_texture_master_names(payload: bytes, src_masters: list[str],
                               src_filename: str) -> "list[str]":
    """Master NAMEs referenced by an ARMA's MO?S (alt-texture TXSTs) and NAM0-3
    (skin-swap TXSTs), resolved in the source ESP's master space (records owned
    by the source itself resolve to src_filename). The SkyPatcher body-coverage
    path uses this to declare exactly the masters a preserved texture ref needs.
    """
    names: list[str] = []
    seen: set[str] = set()

    def _add(fid: int) -> int:
        top = (fid >> 24) & 0xFF
        nm = src_masters[top] if top < len(src_masters) else src_filename
        low = nm.lower()
        if low not in seen:
            seen.add(low)
            names.append(nm)
        return fid

    for sig, data in esp.iter_subrecords(payload):
        if sig in ALT_TEXTURE_SIGS:
            _remap_alt_texture_payload(data, _add)      # harvest nested TXSTs
        elif sig in _ARMA_SKIN_TXST_SIGS and len(data) == 4:
            _add(struct.unpack("<I", data)[0])
    return names


def _remap_arma_skin_txsts(payload: bytes,
                           remap_fid: "callable[[int], int]") -> bytes:
    """Remap NAM0-3 skin-TXST FormIDs in an ARMA payload. rebuild_arma_payload
    copies NAM0-3 verbatim, so callers that move an ARMA into a new master space
    must apply this first (MO?S is handled by rebuild_arma_payload itself)."""
    out = b""
    for sig, data in esp.iter_subrecords(payload):
        if sig in _ARMA_SKIN_TXST_SIGS and len(data) == 4:
            out += esp.encode_subrecord(
                sig, struct.pack("<I", remap_fid(struct.unpack("<I", data)[0])))
        else:
            out += esp.encode_subrecord(sig, data)
    return out


def rebuild_arma_payload(source_payload: bytes, *,
                         new_primary_rnam: int,
                         new_additional_race_fids: Iterable[int],
                         path_prefix: str = "!UBE\\",
                         alt_texture_fid_remap: "callable[[int], int] | None" = None,
                         converted_nif_exists: "callable[[str], bool] | None" = None,
                         ensure_female: bool = True,
                         male_fallback_log: "list | None" = None) -> bytes:
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
    # Canonical ARMA subrecord order:
    #   EDID, BOD2/BODT, RNAM, DNAM, <models: MOD2/MO2T/MOD3/MO3T/MOD4.../NAM0-3>,
    #   <additional races: MODL...>, SNDD (footstep sound), ONAM (art object)
    # The additional-race MODL block must come AFTER models but BEFORE SNDD/ONAM.
    # Appending the MODL block at the end placed it after SNDD on boot armatures
    # -> non-canonical order -> engine crash on load. Defer SNDD/ONAM into
    # `trailing` and emit them after the MODL block.
    trailing = b""
    saw_mod3 = saw_mod5 = False
    conv_mod2 = conv_mod4 = None   # converted (!UBE) male model paths, if produced
    skip_mo3t = skip_mo5t = False  # drop a female texture-hash we redirected past
    for sig, data in esp.iter_subrecords(source_payload):
        if sig == b"RNAM":
            out += esp.encode_subrecord(b"RNAM", struct.pack("<I", new_primary_rnam))
        elif sig == ARMA_ADDITIONAL_RACE_SIG:
            # Drop existing additional-race entries; re-emitted below.
            continue
        elif sig in (b"SNDD", b"ONAM"):
            # SNDD/ONAM must come after the race list (deferred to `trailing`).
            trailing += esp.encode_subrecord(sig, data)
        elif sig == b"DNAM" and ensure_female:
            # Ensure female priority >= male priority (UBE is female-only).
            out += esp.encode_subrecord(b"DNAM", _force_female_priority(data))
        elif sig in ARMA_MODEL_SIGS:  # MOD2/MOD3/MOD4/MOD5 model paths
            # Redirect to the converted !UBE\ mesh only if we produced one.
            # Unconverted meshes keep their original path; pointing at a missing
            # !UBE\ NIF crashes the game on load.
            path = data.rstrip(b"\x00").decode("utf-8", errors="ignore")
            converted = bool(path) and (converted_nif_exists is None
                                        or converted_nif_exists(path))
            new_path = (path_prefix + path) if converted else path
            # UBE is female-only. If the male model was converted but the female
            # model was NOT (wrong name, not shipped), the female UBE actor renders
            # nothing. Redirect MOD3/MOD5 to the converted male mesh and drop the
            # now-mismatched texture-hash. Non-body (helmets) are unaffected
            # because their MOD2 isn't converted either, so conv_mod2 stays None.
            if (sig == b"MOD3" and not converted and ensure_female
                    and conv_mod2):
                out += esp.encode_subrecord(b"MOD3", esp.encode_zstring(conv_mod2))
                saw_mod3 = True
                skip_mo3t = True
                if male_fallback_log is not None:
                    # Record for the last-step female-model re-check:
                    # restore_female_models() undoes this fallback when the
                    # original female mesh is later found in the output.
                    male_fallback_log.append(
                        {"slot": "MOD3", "orig": path, "to": conv_mod2})
            elif (sig == b"MOD5" and not converted and ensure_female
                    and conv_mod4):
                out += esp.encode_subrecord(b"MOD5", esp.encode_zstring(conv_mod4))
                saw_mod5 = True
                skip_mo5t = True
                if male_fallback_log is not None:
                    male_fallback_log.append(
                        {"slot": "MOD5", "orig": path, "to": conv_mod4})
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
            # Remap embedded TXST FormIDs from source master space.
            new_data = _remap_alt_texture_payload(data, alt_texture_fid_remap)
            out += esp.encode_subrecord(sig, new_data)
        elif sig in ARMA_MODT_SIGS:
            # Drop the female texture-hash when the female model was redirected
            # to the converted male mesh -- the hash no longer matches.
            if sig == b"MO3T" and skip_mo3t:
                skip_mo3t = False
                continue
            if sig == b"MO5T" and skip_mo5t:
                skip_mo5t = False
                continue
            # Normalize the texture-hash block (headerless LE-ported mods cause
            # a 7.5M-entry overread CTD). See normalize_modt().
            out += esp.encode_subrecord(sig, normalize_modt(data))
        else:
            out += esp.encode_subrecord(sig, data)

    # If the source has a male model (MOD2) but no female one (MOD3), synthesise
    # MOD3 from the converted male mesh so a female UBE actor renders it.
    # Gated on conv_mod2 existing -- never point at a missing !UBE NIF (CTD).
    if ensure_female and not saw_mod3 and conv_mod2:
        out += esp.encode_subrecord(b"MOD3", esp.encode_zstring(conv_mod2))
        if male_fallback_log is not None:
            # orig=None: the armature never had a female model; nothing to restore.
            male_fallback_log.append(
                {"slot": "MOD3", "orig": None, "to": conv_mod2})
    if ensure_female and not saw_mod5 and conv_mod4:
        out += esp.encode_subrecord(b"MOD5", esp.encode_zstring(conv_mod4))
        if male_fallback_log is not None:
            male_fallback_log.append(
                {"slot": "MOD5", "orig": None, "to": conv_mod4})

    # Emit additional-race list (canonical: after models, before SNDD/ONAM).
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

# In ARMO records, MODL encodes armature refs (FormID per entry), the same
# signature as ARMA's additional-race list — both are 4-byte FormIDs.
ARMO_ARMATURE_SIG = b"MODL"


# ----- EDID -> human name synthesis ---------------------------------------
# When overriding a localized master ARMO (Skyrim.esm + DLCs), the FULL
# subrecord is a 4-byte LSTRING ref into a .STRINGS file we can't emit in
# a non-localized patch. Without FULL the inventory UI silently hides the
# item. Synthesize a readable name from the EDID as a fallback.
#
# Strategy: strip DLC[N][n] prefix, then Ench/Armor/Clothes/Armory recursively,
# then split CamelCase at transitions and digit boundaries. Examples:
#   "ArmorIronCuirass"                     -> "Iron Cuirass"
#   "EnchArmorDwarvenCuirassDestruction04" -> "Dwarven Cuirass Destruction 04"
#   "DLC1ArmorVampireArmorGrayLight"       -> "Vampire Armor Gray Light"
import re as _re
_EDID_PREFIX_STRIP = (
    "Ench",          # before Armor — enchanted variant prefix
    "Armor",
    "Clothes",
    "Armory",
)
# DLC prefix at start, optionally followed by a single lowercase letter.
_DLC_PREFIX_RE = _re.compile(r"^DLC\d+[a-z]?")
_CAMEL_SPLIT_RE = _re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])"
)


def synthesize_name_from_edid(edid: str) -> str:
    """Generate a readable item name from an ARMO EDID (fallback when
    the original FULL is an LSTRING we can't resolve). Strips DLC prefix,
    then Ench/Armor/Clothes/Armory recursively, then splits CamelCase.
    """
    s = edid
    # DLC prefix first (never more than one).
    m = _DLC_PREFIX_RE.match(s)
    if m and m.end() < len(s):
        s = s[m.end():]
    # Strip type prefixes recursively.
    changed = True
    while changed:
        changed = False
        for prefix in _EDID_PREFIX_STRIP:
            if s.startswith(prefix) and len(s) > len(prefix):
                s = s[len(prefix):]
                changed = True
                break
    parts = _CAMEL_SPLIT_RE.split(s)
    name = " ".join(p for p in parts if p)
    return name or edid  # fall back to raw EDID if synthesis collapsed


# Body biped slot — slot 32 = chest/body.
BODY_BIPED_SLOT = 32

# Skyrim.esm DefaultRace FormID (low 24 bits). Used to gate non-body ARMA
# passthrough: adding UBE races to a beast/custom-race armature crashes.
# Every humanoid player-equippable ARMA's primary RNAM points here.
_DEFAULT_RACE_LOW24 = 0x000019


def add_slot32_to_bod2_payload(payload: bytes) -> tuple[bytes, bool]:
    """Set biped slot 32 (body) on an ARMA/ARMO's BOD2/BODT bipedObjectSlots.
    Returns (new_payload, changed_bool).

    NioOverride's BodyMorph only deforms shapes on ARMAs with slot 32 set.
    Slot-49-only cloth (corsets, skirts) never receives body-slider deformation.
    Promoting to slot 32 enables BodyMorph, at the cost of equip-conflict with
    cuirasses — matching behaviour of hand-authored UBE cloth conversions.
    Caller decides when to apply this (typically when the NIF has BODYTRI).
    """
    bit = BODY_BIPED_SLOT - 30  # slot 32 -> bit 2 of the slots field
    out = b""
    changed = False
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(data) >= 4:
            slots = struct.unpack_from("<I", data, 0)[0]
            if not (slots & (1 << bit)):
                new_slots = slots | (1 << bit)
                # Splice only the first u32 (slot bits); rest is verbatim.
                new_data = struct.pack("<I", new_slots) + data[4:]
                out += esp.encode_subrecord(sig, new_data)
                changed = True
                continue
        out += esp.encode_subrecord(sig, data)
    return out, changed


def clear_slot33_from_bod2_payload(payload: bytes) -> tuple[bytes, bool]:
    """Clear biped slot 33 (Hands) from an ARMA/ARMO's BOD2/BODT, preserving
    all other slot bits. Returns (new_payload, changed_bool).

    A bracer claiming slot 33 with no hand geometry hides the nude-hands skin
    and draws nothing -- invisible hands. See fix_spurious_hand_slot for when
    to apply this (only when the mesh is confirmed hand-less)."""
    bit = 33 - 30  # slot 33 -> bit 3 of the slots field
    out = b""
    changed = False
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(data) >= 4:
            slots = struct.unpack_from("<I", data, 0)[0]
            if slots & (1 << bit):
                new_data = struct.pack("<I", slots & ~(1 << bit)) + data[4:]
                out += esp.encode_subrecord(sig, new_data)
                changed = True
                continue
        out += esp.encode_subrecord(sig, data)
    return out, changed


def add_arma_to_armo_payload(source_payload: bytes, new_arma_fid: int) -> bytes:
    """Insert `new_arma_fid` into an ARMO's Armatures list (MODL entries).

    Inserts immediately after the last existing MODL, before DATA.
    Skyrim's ARMO parser stops reading armatures at DATA — any MODL after
    DATA is silently ignored, making the new UBE ARMA invisible to the engine.
    Falls back to splice-before-DATA or append if no MODL/DATA exists.
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
    """Walk an ESM and return ARMO records whose armature list references
    any FormID in `referenced_arma_fids_in_master_space`. Records are returned
    in the master's own address space.

    Needed because replacer mods often override only ARMA records while the
    parent ARMOs live in Skyrim.esm. Without an ARMO override the engine finds
    no UBE-race-matching ARMA and renders nothing for UBE-race actors.
    Skyrim.esm (250MB, 2762 ARMOs) loads in ~0.2s via header-only parsing.
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


# Per-directory {lowercase filename -> path} index, built once per dir. The old
# _find_master_path did a full d.iterdir() for EVERY master that didn't exact-match,
# so a modlist with thousands of sibling mod dirs (3424 here) cost ~0.14s/master ->
# tens of seconds across a run (per-source validate + Combined postflight + resort +
# the preserve_textures source masters). Indexing each dir once makes lookups O(1).
# Masters/mods are static during a run, so the cache is valid run-long.
_DIR_FILE_INDEX: "dict[str, dict[str, Path]]" = {}
# Flattened {lower master name -> path} across a whole data_dirs list, first dir
# winning. Keyed by id(list) with the list ref held so the id can't be reused;
# turns _find_master_path into a single O(1) dict lookup instead of iterating
# thousands of dirs (3424 here) per master.
_COMBINED_MASTER_INDEX: dict = {}


def clear_master_path_cache() -> None:
    """Drop the master-path indexes. Call when the on-disk file set may have
    changed between reuses of this module in one process (e.g. a new run)."""
    _DIR_FILE_INDEX.clear()
    _COMBINED_MASTER_INDEX.clear()


def _dir_file_index(d: Path) -> "dict[str, Path]":
    key = str(d)
    idx = _DIR_FILE_INDEX.get(key)
    if idx is None:
        idx = {}
        try:
            for p in d.iterdir():
                if p.is_file():
                    idx.setdefault(p.name.lower(), p)  # first entry wins (iterdir order)
        except (OSError, PermissionError):
            idx = {}
        _DIR_FILE_INDEX[key] = idx
    return idx


def _find_master_path(master_name: str, data_dirs: list[Path]) -> Path | None:
    """Resolve a master filename (e.g. 'Skyrim.esm') to its on-disk path,
    case-insensitively, first dir in `data_dirs` winning. Returns None if not
    found. Assumes the on-disk file set is static for the list's lifetime (true
    for a conversion run); call clear_master_path_cache() otherwise."""
    key = id(data_dirs)
    ent = _COMBINED_MASTER_INDEX.get(key)
    if ent is None or ent[0] is not data_dirs:
        merged: "dict[str, Path]" = {}
        for d in data_dirs:
            if not d.is_dir():
                continue
            for low, p in _dir_file_index(d).items():
                merged.setdefault(low, p)  # earlier dir wins
        ent = (data_dirs, merged)          # hold the ref -> id() can't be reused
        _COMBINED_MASTER_INDEX[key] = ent
    return ent[1].get(master_name.lower())


# Substrings (lowercased) in a RACE EDID that identify a UBE-targeted race
# extension — added to each ARMA's additional-race list.
UBE_RACE_EDID_MARKERS = ("ube",)

# Substrings that EXCLUDE a race even if it matches UBE_RACE_EDID_MARKERS.
# For unofficial UBE patches whose skeleton/weighting may not match the
# official UBE setup (equipping our ARMA could be worse than no coverage).
UBE_RACE_EDID_EXCLUDE = ("khajiit",)


def _discover_ube_races(data_dirs: list[Path]) -> list[tuple[str, int, str]]:
    """Walk every plugin in `data_dirs` and return all RACE records whose
    EDID contains a UBE marker substring (excluding UBE_AllRace.esp itself,
    whose races come from UBE_RACE_FIDS_24). Returns a list of
    (plugin_filename, race_fid_in_plugin_space, edid) triples.

    UBE_AllRace.esp only covers 8 base races + vampires (16 total). Players
    using Khajiit/Argonian/custom-race UBE patches need those additional races
    in our ARMA list or they see no UBE armor. De-duped by EDID (first-seen).
    Memoized by data-dir set (walking thousands of plugins takes ~3s).
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
            # Skip UBE_AllRace.esp — handled via UBE_RACE_FIDS_24.
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


def restore_female_models(patches_dir: "str | Path",
                          output_mod_dir: "str | Path",
                          path_prefix: str = "!UBE\\") -> dict:
    """Last-step female-model re-check across every per-mod patch.

    generate_ube_patch falls back to pointing MOD3/MOD5 at the converted male
    mesh when no converted female mesh is available AT PATCH TIME. This pass
    runs once ALL mods have converted: any recorded fallback
    (*.male_fallbacks.json sidecars) whose original female mesh NOW exists in
    the output has its MOD3/MOD5 re-pointed at the female mesh. Fallbacks
    survive only where no converted female mesh exists anywhere. Idempotent.
    Returns {checked, models_restored, patches_changed}."""
    import json as _json
    patches_dir = Path(patches_dir)
    meshes_root = Path(output_mod_dir) / "meshes" / path_prefix.strip("\\/")
    checked = restored = patches_changed = 0
    for sidecar in sorted(patches_dir.glob("*.male_fallbacks.json")):
        patch_path = Path(str(sidecar)[:-len(".male_fallbacks.json")])
        if not patch_path.is_file():
            continue
        try:
            entries = _json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        # fid -> {slot: (recorded_male, restored_female)} for entries whose
        # original female mesh is now present in the output.
        todo: "dict[int, dict[str, tuple[str, str]]]" = {}
        for e in entries:
            orig, slot, fid = e.get("orig"), e.get("slot"), e.get("fid")
            if not orig or slot not in ("MOD3", "MOD5") or fid is None:
                continue
            checked += 1
            rel = orig.replace("\\", "/").lstrip("/")
            if rel.lower().startswith("meshes/"):
                rel = rel[len("meshes/"):]
            if not (meshes_root / rel).is_file():
                continue  # still no converted female mesh -> fallback stands
            todo.setdefault(int(fid), {})[slot] = (
                e.get("to") or "", path_prefix + orig)
        if not todo:
            continue
        try:
            pe = esp.ESP.load(patch_path)
        except Exception:
            continue
        n_swapped = 0
        for g in pe.groups:
            if g.label != b"ARMA":
                continue
            for rec in g.records:
                fixes = todo.get(rec.formid)
                if not fixes:
                    continue
                out = b""
                rec_changed = False
                for sig, data in esp.iter_subrecords(rec.payload):
                    fix = fixes.get(sig.decode("ascii", "ignore"))
                    if fix is not None:
                        male_path, female_path = fix
                        cur = data.rstrip(b"\x00").decode("utf-8", "ignore")
                        if cur == male_path:
                            out += esp.encode_subrecord(
                                sig, esp.encode_zstring(female_path))
                            rec_changed = True
                            n_swapped += 1
                            continue
                    out += esp.encode_subrecord(sig, data)
                if rec_changed:
                    rec.payload = out
        if n_swapped:
            pe.save(patch_path)
            patches_changed += 1
            restored += n_swapped
    return {"checked": checked, "models_restored": restored,
            "patches_changed": patches_changed}


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

    # True iff we will produce a converted NIF at the !UBE\ path for this model.
    # If not, keep the original path — pointing at a missing !UBE\ NIF crashes
    # the game on load. Uses the PLANNED set (not filesystem) because the patch
    # is generated before NIFs are written. None => legacy always-prefix.
    def _converted_nif_exists(model_path: str) -> bool:
        if converted_rel_paths is None:
            return True
        return model_path.replace("\\", "/").lstrip("/").lower() \
            in converted_rel_paths

    # True iff the ARMA's original (un-converted) mesh is present in this mod.
    # Used to confirm a mod ships the helmet/jewelry mesh before extending its
    # ARMA to UBE races. Checks both loose (body_mesh_rel_paths) and BSA
    # (bsa_mesh_rel_paths) to cover mods that pack meshes into .bsa archives.
    # Matches exact path and weight-agnostic variants (_0/_1).
    def _orig_mesh_on_disk(model_path: str) -> bool:
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

    # True iff an un-converted ARMA is a safe non-body accessory to extend to
    # UBE races without a mesh edit (helmet/hood/circlet/jewelry).
    # Three guards: (1) non-body slot (slot 32 needs real CBBE->UBE conversion);
    # (2) primary race = humanoid DefaultRace in Skyrim.esm (adding UBE races to
    # a beast armature crashes); (3) mod ships at least one of its meshes (rules
    # out incidental vanilla-ARMA overrides). Original model paths are kept so
    # the vanilla-fitting mesh loads on UBE actors.
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

    # Discover additional UBE race plugins so their races are included in ARMA
    # additional-race lists. Without this, non-base-race UBE players see nothing.
    extra_ube_races: list[tuple[str, int, str]] = []
    if master_data_dirs:
        extra_ube_races = _discover_ube_races(master_data_dirs)
        if extra_ube_races:
            print(f"  discovered {len(extra_ube_races)} extra UBE race(s) "
                  f"from {len({p for p, _, _ in extra_ube_races})} plugin(s):")
            for plugin, _fid, edid in extra_ube_races:
                print(f"    {edid}  ({plugin})")

    # Build the patch's master list: vanilla DLC ESMs first (always-loaded,
    # safe to include unconditionally; omitting them causes FormID misroutes),
    # then any other source masters, then UBE_AllRace, then the source ESP.
    patch_masters: list[str] = list(VANILLA_DLC_MASTERS)
    for m in src.header.masters:
        _add_master_if_missing(patch_masters, m)
    _add_master_if_missing(patch_masters, ube_allrace_filename)
    # Add discovered UBE race plugins as masters (for their RACE FormIDs).
    for plugin_name, _fid, _edid in extra_ube_races:
        _add_master_if_missing(patch_masters, plugin_name)
    _add_master_if_missing(patch_masters, src_filename)

    # Patch's own records use top byte == len(patch_masters).
    own_top_byte = len(patch_masters) << 24

    # UBE race FormIDs in patch address space.
    ube_top = make_master_byte(patch_masters, ube_allrace_filename) << 24
    ube_primary = ube_top | UBE_PRIMARY_BRETON_FID_24
    # Gold-standard UBE_AllRace armatures list UBE_BretonRace in BOTH RNAM and
    # the MODL block; mirror that exactly (full 16-race list, primary first).
    ube_additional = [ube_top | low for low in UBE_RACE_FIDS_24]
    # Add discovered extra UBE races; remap their FormIDs to patch space.
    for plugin_name, fid, _edid in extra_ube_races:
        plugin_top = make_master_byte(patch_masters, plugin_name) << 24
        ube_additional.append(plugin_top | (fid & 0xFFFFFF))

    src_arma_group = src.group(b"ARMA")
    src_armo_group = src.group(b"ARMO")
    if src_arma_group is None:
        raise RuntimeError(f"source ESP has no ARMA group: {source_esp_path}")

    # source ARMA FormID -> new ARMA FormID in patch
    new_arma_fids: dict[int, int] = {}
    next_obj_id = 0x800  # arbitrary starting point; xEdit conventions vary

    # Build FormID remap: source master space -> patch master space.
    src_to_patch_byte: dict[int, int] = {}
    for i, m in enumerate(src.header.masters):
        try:
            j = next(idx for idx, mn in enumerate(patch_masters)
                     if mn.lower() == m.lower())
            src_to_patch_byte[i] = j
        except StopIteration:
            continue
    # Source ESP's own byte maps to its index in the patch master list.
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

    # CBBE2UBE_BODY_SKYPATCHER: when on, TORSO body (slot 32) DefaultRace
    # armatures whose mesh we converted are owned by the SkyPatcher body-coverage
    # pass (cover_all), so DON'T mint an ESP-override ARMA for them here. Their
    # ARMO overrides then vanish too (the override loops only add minted ARMAs).
    # Hands/feet + non-body are unaffected -> stay on this ESP-override path.
    _fsp = _full_skypatcher_enabled()
    # Full-SkyPatcher supersedes the body-only pivot: the per-source path mints
    # body armatures again (and LINKS them instead of overriding), so the
    # body-suppression gates must stand down.
    _bsp = _body_skypatcher_enabled() and not _fsp
    # (defining_plugin, armo_low24) -> list of (minted_patch_fid,
    #  src_arma_defining, src_arma_low24). Written to the .skypatcher.json
    # sidecar; the merge remaps minted fids to final Combined space and emits
    # the armorAddonsToAdd INI lines.
    _sp_links: "dict[tuple[str, int], list]" = {}

    def _rnam_is_default_race(rnam: "int | None", masters: "list[str]",
                              own_name: str) -> bool:
        """DefaultRace (Skyrim.esm 0x19) resolved THROUGH the given plugin's
        master table, EXACTLY like _record_abs_fid. Two traps a raw
        `(rnam >> 24) == 0` misses, both causing DOUBLE coverage vs the coverage
        side: (1) Skyrim.esm is not always master index 0 (Requiem patch ESPs list
        it later); (2) an own-record ref (top byte == len(masters)) -- notably
        Skyrim.esm's OWN DefaultRace, since Skyrim.esm has an EMPTY master list so
        its DefaultRace is top byte 0 == own. `masters`+`own_name` MUST belong to
        the plugin the RNAM lives in (source for same-plugin records, the owning
        master for cross-ESP / master-scan records)."""
        if rnam is None or (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24:
            return False
        top = (rnam >> 24) & 0xFF
        name = masters[top] if top < len(masters) else own_name
        return name.lower() == "skyrim.esm"

    def _route_body_to_skypatcher(slot_bits: int, rnam: "int | None",
                                  models: "list[str]", masters: "list[str]",
                                  own_name: str) -> bool:
        return bool(
            _bsp and (slot_bits & _BIPED_SLOT_BODY_BIT)
            and _rnam_is_default_race(rnam, masters, own_name)
            and any(_converted_nif_exists(m) for m in models if m))

    def _armo_routed_to_skypatcher(armo_payload: bytes, masters: "list[str]",
                                   own_name: str) -> bool:
        """True if this ARMO is TORSO body (slot 32) + DefaultRace, so the
        SkyPatcher coverage pass owns it -> DON'T emit an ESP override. Coverage
        keys on the ARMO's slot, and an ARMO's slot can differ from its ARMA's
        (e.g. a slot-32 cuirass whose ArmorAddon is registered on slot 49), so
        the mint-site (ARMA-slot) gate alone would leak the override; gate the
        override EMISSION on the ARMO slot to guarantee complementarity. `masters`
        MUST be the master table this ARMO's RNAM lives in."""
        if not _bsp:
            return False
        slots = 0
        rnam: "int | None" = None
        for sig, data in esp.iter_subrecords(armo_payload):
            if sig in (b"BOD2", b"BODT") and len(data) >= 4:
                slots = struct.unpack_from("<I", data, 0)[0]
            elif sig == b"RNAM" and len(data) == 4:
                rnam = struct.unpack("<I", data)[0]
        return bool((slots & _BIPED_SLOT_BODY_BIT)
                    and _rnam_is_default_race(rnam, masters, own_name))

    new_arma_records: list[esp.Record] = []
    # (record, [fallback dicts]) for the male-fallback sidecar. Keyed by
    # Record object because prune_unused_masters renumbers FormIDs before save.
    male_fallback_records: "list[tuple[esp.Record, list]]" = []
    for src_arma in src_arma_group.records:
        # Parse EDID, model paths, race, and slot bits.
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

        # Only emit a UBE ARMA if we converted one of its meshes. Without a
        # converted mesh, emitting one adds UBE races to a beast/custom-race
        # armature and loads the wrong mesh -> crash. Exception: safe non-body
        # accessories (helmets/jewelry) can be passed through with the original
        # mesh; UBE only changes the torso so those fit fine. See
        # _is_safe_passthrough_accessory. converted_rel_paths=None -> always emit.
        if converted_rel_paths is not None and model_paths and \
                not any(_converted_nif_exists(m) for m in model_paths):
            if not _is_safe_passthrough_accessory(
                    src_rnam, slot_bits, model_paths):
                continue

        # Full-SkyPatcher: torso body owned by the coverage pass -> don't mint here.
        if _route_body_to_skypatcher(slot_bits, src_rnam, model_paths,
                                     src.header.masters, src_filename):
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
        # UBE races on top -- exactly the 16-UBE + ~23-vanilla list a correct
        # coverage armature carries. Dropping them was the
        # modded-gauntlet-invisible bug. Skyrim.esm races remap cleanly
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
        _fb: list = []
        new_payload = rebuild_arma_payload(
            src_arma.payload,
            new_primary_rnam=_prim,
            new_additional_race_fids=_additional_for_arma,
            alt_texture_fid_remap=_remap_src_fid_to_patch,
            converted_nif_exists=_cne_for_arma,
            male_fallback_log=_fb,
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
        if _fb:
            male_fallback_records.append((new_arma_records[-1], _fb))

    # Now build ARMO overrides: any source ARMO whose MODL list references a
    # source ARMA we converted gets an override with our new ARMA appended.
    armo_overrides: list[esp.Record] = []

    # --- Cross-ESP ARMO coverage (#176) -------------------------------------
    # An ARMO in THIS plugin may reference an ARMA defined in a MASTER plugin
    # (e.g. an Alt-Textures add-on's `Cloth_Cloak` ARMO referencing the BASE
    # mod's cloak ARMA). That ARMA isn't in OUR new_arma_fids -- it's only
    # converted while patching its OWN plugin -- so the same-plugin rule below
    # misses the ARMO and it ships with no UBE armature => invisible on UBE
    # races (e.g. a base mod's cloth cloaks referenced by an add-on plugin).
    # If the referenced master ARMA's female mesh (MOD3) was converted to !UBE,
    # mint a UBE ARMA for it HERE (self-contained: a local own-FormID, no
    # cross-patch references) and link it. Guarded by _converted_nif_exists, so
    # we never point an ARMA at a non-existent !UBE NIF (which would CTD).
    # Matched purely by mesh path; this covers ANY base-plugin + add-on-plugin mod.
    _xesp_arma_cache: "dict[str, dict[int, esp.Record]]" = {}
    # The owning master plugin's OWN master table (for resolving its RNAM to
    # DefaultRace in ITS space, not the source patch's space).
    _xesp_master_masters: "dict[str, list[str]]" = {}

    def _xesp_master_arma(ref_fid: int) -> "esp.Record | None":
        """Resolve a referenced ARMA FormID to its record in the owning MASTER
        plugin. None if the ref is source-own or can't be resolved."""
        mbyte = (ref_fid >> 24) & 0xFF
        if mbyte >= len(src.header.masters):
            return None  # source-own record (handled by the same-plugin path)
        mname = src.header.masters[mbyte]
        if mname not in _xesp_arma_cache:
            amap: "dict[int, esp.Record]" = {}
            mmasters: "list[str]" = []
            mp = _find_master_path(
                mname, master_data_dirs or [source_esp_path.parent])
            if mp is not None:
                try:
                    me = esp.ESP.load_cached(mp)
                    mmasters = list(me.header.masters)
                    for g in me.groups:
                        if g.label == b"ARMA":
                            for r in g.records:
                                amap[r.formid & 0xFFFFFF] = r
                except Exception:
                    amap = {}
                    mmasters = []
            _xesp_arma_cache[mname] = amap
            _xesp_master_masters[mname] = mmasters
        return _xesp_arma_cache[mname].get(ref_fid & 0xFFFFFF)

    def _xesp_masters_for(ref_fid: int) -> "list[str]":
        """Master table of the plugin that OWNS ref_fid (populated by
        _xesp_master_arma). Empty if unresolved."""
        mbyte = (ref_fid >> 24) & 0xFF
        if mbyte >= len(src.header.masters):
            return []
        return _xesp_master_masters.get(src.header.masters[mbyte], [])

    def _xesp_owner_name(ref_fid: int) -> str:
        """Filename of the master plugin that OWNS ref_fid (for own-record RNAM
        resolution -- e.g. Skyrim.esm's own DefaultRace)."""
        mbyte = (ref_fid >> 24) & 0xFF
        if mbyte >= len(src.header.masters):
            return src_filename
        return src.header.masters[mbyte]

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
        m_slots = 0
        for sig, d in esp.iter_subrecords(marec.payload):
            if sig == b"MOD3":
                mod3 = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"MOD2":
                mod2 = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"EDID":
                m_edid = d.rstrip(b"\x00").decode("utf-8", "ignore")
            elif sig == b"RNAM" and len(d) == 4:
                rnam = struct.unpack("<I", d)[0]
            elif sig in (b"BOD2", b"BODT") and len(d) >= 4:
                m_slots = struct.unpack_from("<I", d, 0)[0]
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
        # race armature fires it for human UBE actors -> wrong mesh -> CTD
        # (beast-race armature assigned to humanoid, #152). The mesh-converted guard already
        # filters to player armour, but this is the explicit belt-and-braces.
        if rnam is None or (rnam & 0xFFFFFF) != _DEFAULT_RACE_LOW24 \
                or (rnam >> 24) != 0:
            return None
        # Full-SkyPatcher: torso body owned by the coverage pass -> don't mint.
        # RNAM is in the MASTER plugin's space (rnam top byte resolved there).
        if _route_body_to_skypatcher(m_slots, rnam, [mod3, mod2],
                                     _xesp_masters_for(ref_fid),
                                     _xesp_owner_name(ref_fid)):
            return None
        stripped = b"".join(
            esp.encode_subrecord(s, d)
            for s, d in esp.iter_subrecords(marec.payload)
            if s not in _STRIP_VANILLA_BODY_ARMA)
        _fb: list = []
        new_payload = rebuild_arma_payload(
            stripped, new_primary_rnam=ube_primary,
            new_additional_race_fids=ube_additional,
            converted_nif_exists=_converted_nif_exists,
            male_fallback_log=_fb)
        new_payload = replace_arma_edid(
            new_payload,
            (m_edid + "_UBE") if m_edid else f"UBE_XESP_{next_obj_id:X}")
        nf = own_top_byte | next_obj_id
        next_obj_id += 1
        new_arma_records.append(esp.Record(
            sig=b"ARMA", flags=0, formid=nf, timestamp_vc=0,
            version_unk=0x002C, payload=new_payload))
        if _fb:
            male_fallback_records.append((new_arma_records[-1], _fb))
        new_arma_fids[ref_fid] = nf
        return nf

    # LOCALIZED source (TES4 flag 0x80): its FULL/DESC are LSTRING ids, not
    # strings -- see the localized branch in the override loop below (#xedit5).
    _src_localized = bool(src.header.flags & 0x80)
    if src_armo_group is not None:
        for src_armo in src_armo_group.records:
            # Find which source ARMAs this ARMO references.
            referenced_src_armas: list[int] = []
            for sig, data in esp.iter_subrecords(src_armo.payload):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    referenced_src_armas.append(struct.unpack("<I", data)[0])

            # ARMOs and source ARMAs are both in source-master-space; look
            # them up in new_arma_fids directly (no remap needed).
            new_armas_to_add = [
                new_arma_fids[src_arma_fid]
                for src_arma_fid in referenced_src_armas
                if src_arma_fid in new_arma_fids
            ]
            # Cross-ESP: for any referenced ARMA not converted in this plugin,
            # mint a UBE ARMA if its mesh was converted (covers add-on plugins
            # whose ARMA lives in a different base plugin).
            for _ref in referenced_src_armas:
                if _ref in new_arma_fids and new_arma_fids[_ref] in new_armas_to_add:
                    continue
                _minted = _mint_xesp_ube_arma(_ref)
                if _minted is not None and _minted not in new_armas_to_add:
                    new_armas_to_add.append(_minted)
            if not new_armas_to_add:
                continue
            # Full-SkyPatcher: torso body ARMO owned by the coverage pass.
            if _armo_routed_to_skypatcher(src_armo.payload, src.header.masters,
                                          src_filename):
                continue
            if _fsp:
                # FULL SKYPATCHER: record the link, emit NO override. The
                # armature reaches the ARMO at runtime via armorAddonsToAdd,
                # applied to whatever record actually wins the load order.
                _armo_abs = _record_abs_fid(
                    src_armo.formid, src.header.masters, src_filename)
                _adds = _sp_links.setdefault(_armo_abs, [])
                for _ref in referenced_src_armas:
                    _mf = new_arma_fids.get(_ref)
                    if _mf is not None and _mf in new_armas_to_add:
                        _sa = _record_abs_fid(
                            _ref, src.header.masters, src_filename)
                        _adds.append((_mf, _sa[0], _sa[1]))
                continue

            # Build override payload with FormIDs remapped to patch master space.
            new_armo_fid = remap_fid(
                src_armo.formid, src.header.masters, src_filename, patch_masters,
            )
            # Insert new MODLs right after the LAST existing MODL (before DATA).
            # Skyrim stops reading the armature list at DATA; MODLs after DATA
            # are silently ignored, making the new ARMA invisible to the engine.
            src_pieces = list(esp.iter_subrecords(src_armo.payload))
            last_modl_idx = -1
            src_edid_txt = None
            for i, (sig, data) in enumerate(src_pieces):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    last_modl_idx = i
                elif sig == b"EDID" and src_edid_txt is None:
                    src_edid_txt = data.rstrip(b"\x00").decode(
                        "utf-8", errors="ignore")
            new_payload = b""
            for i, (sig, data) in enumerate(src_pieces):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    src_arma_ref = struct.unpack("<I", data)[0]
                    remapped = remap_fid(
                        src_arma_ref, src.header.masters, src_filename, patch_masters,
                    )
                    new_payload += esp.encode_subrecord(sig, struct.pack("<I", remapped))
                elif sig in ALT_TEXTURE_SIGS:
                    # Remap embedded TXST FormIDs from source master space;
                    # without this, all color variants render the default texture.
                    new_data = _remap_alt_texture_payload(
                        data, _remap_src_fid_to_patch)
                    new_payload += esp.encode_subrecord(sig, new_data)
                elif sig in ARMA_MODT_SIGS:
                    new_payload += esp.encode_subrecord(sig, normalize_modt(data))
                elif sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4:
                    # Remap EVERY FormID-bearing subrecord (EITM/TNAM/RNAM/ZNAM/
                    # YNAM/ETYP/BIDS/BAMT...). Verbatim copy left SOURCE-space
                    # bytes that misroute once the merge renumbers masters --
                    # #xedit5: a shield's enchantment ref resolving into
                    # UBE_AllRace.esp (unresolvable garbage in xEdit/engine).
                    new_payload += esp.encode_subrecord(sig, struct.pack(
                        "<I",
                        _remap_src_fid_to_patch(struct.unpack("<I", data)[0])))
                elif sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0:
                    new_payload += esp.encode_subrecord(sig, b"".join(
                        struct.pack("<I", _remap_src_fid_to_patch(
                            struct.unpack_from("<I", data, _o)[0]))
                        for _o in range(0, len(data), 4)))
                elif _src_localized and sig in (b"FULL", b"DESC"):
                    # LOCALIZED source: FULL/DESC are 4-byte LSTRING ids --
                    # garbage as zstrings in our non-localized patch (xEdit
                    # "unused data" + wrong names, #xedit5). Synthesize FULL
                    # from EDID (same policy as the master-scan path); drop
                    # DESC (tooltip-only).
                    if sig == b"FULL" and src_edid_txt:
                        _syn = synthesize_name_from_edid(src_edid_txt)
                        if _syn:
                            new_payload += esp.encode_subrecord(
                                b"FULL", esp.encode_zstring(_syn))
                    continue
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
                # Carry the SOURCE record's flags: flags=0 dropped Non-Playable
                # (0x4) etc., making NPC-only armor playable (#xedit5).
                sig=b"ARMO", flags=src_armo.flags, formid=new_armo_fid,
                timestamp_vc=0, version_unk=0x002C, payload=new_payload,
            ))

    # --- Master ESM ARMO scan ---
    # Walk each master ESM to find ARMOs that reference our converted ARMAs.
    # Those ARMOs need override entries in our patch — otherwise the master's
    # armature list is what the engine sees and UBE-race actors find no match.
    if master_data_dirs is None:
        master_data_dirs = [source_esp_path.parent]

    # String resolver: recover real localized ARMO names from the master's
    # LSTRING refs in Skyrim - Interface.bsa so FULL shows the true name
    # (e.g. "Vampire Armor") instead of an EDID-derived guess.
    _string_resolver = None
    try:
        # FULL SKYPATCHER: no ARMO overrides are emitted, so the localized-name
        # resolver (loads Skyrim - Interface.bsa string tables) has no consumer
        # -- skip the BSA work entirely.
        from . import bsa_strings
        for _d in (() if _fsp else (master_data_dirs or [])):
            if (Path(_d) / bsa_strings.StringResolver.INTERFACE_BSA).is_file():
                _key = str(_d)
                if _key not in _STRING_RESOLVER_CACHE:
                    _STRING_RESOLVER_CACHE[_key] = bsa_strings.StringResolver(_d)
                _string_resolver = _STRING_RESOLVER_CACHE[_key]
                break
    except Exception:
        _string_resolver = None

    master_armo_overrides: list[esp.Record] = []
    master_scan_stats: dict[str, int] = {}
    # Masters we expected to scan for UBE-race ARMO coverage but couldn't load
    # (not found under master_data_dirs, or unreadable). Surfaced as a warning so
    # a silent coverage gap (armor invisible on UBE races) isn't swallowed.
    master_scan_skipped: list[str] = []
    converted_arma_src_fids = set(new_arma_fids.keys())
    for master_name in src.header.masters:
        master_path = _find_master_path(master_name, master_data_dirs)
        if master_path is None:
            if master_data_dirs:
                master_scan_skipped.append(master_name)
            continue
        # Master's position in source ESP's master list.
        try:
            master_idx_in_src = next(
                i for i, m in enumerate(src.header.masters)
                if m.lower() == master_name.lower()
            )
        except StopIteration:
            continue
        master_byte_in_src = master_idx_in_src

        # Master's own byte = len(master.masters). Skyrim.esm -> 0x00,
        # Dawnguard -> 0x02, Dragonborn -> 0x03. Hardcoding 0x00 missed DLC ARMOs.
        try:
            master_esp = _load_master_cached(master_path)
        except Exception:
            if master_data_dirs:
                master_scan_skipped.append(master_name)
            continue
        master_own_byte = len(master_esp.header.masters)

        # Skip this master if any of its transitive masters isn't in our
        # patch's master list. Copying ARMO records with unmappable FormIDs
        # (e.g. a Creation Club .esl/.esm referencing HearthFires.esm) causes silent
        # misroutes and crashes on load. Simpler to skip than remap per-record.
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

        # FormIDs to find in this master's address space, plus reverse map
        # to source space (to look up our new ARMA FormID).
        lookup_in_master_space = set()
        master_to_src_fid: dict[int, int] = {}
        for src_fid in converted_arma_src_fids:
            if ((src_fid >> 24) & 0xFF) == master_byte_in_src:
                master_space_fid = (master_own_byte << 24) | (src_fid & 0xFFFFFF)
                lookup_in_master_space.add(master_space_fid)
                master_to_src_fid[master_space_fid] = src_fid

        # Mesh-path-driven vanilla body coverage: loose-mesh replacers ship
        # vanilla body meshes with no ESP records. If we converted the mesh
        # to !UBE, mint a UBE ARMA and register it so the master-ARMO override
        # loop appends it. Matched purely by mesh path; no per-armor logic.
        if body_mesh_rel_paths:
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
                # Full-SkyPatcher: this whole path is torso body + converted
                # (DefaultRace vanilla body) -> owned by the coverage pass.
                if _bsp:
                    continue
                # Strip stale FormID/texture refs, rebuild with UBE races.
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
            # Full-SkyPatcher: a torso body master ARMO is owned by the coverage
            # pass -> don't emit an ESP override for it. (Path-2 vanilla-body
            # mints are already gated above; this catches master body ARMOs whose
            # armature came from the source-ARMA scan.) Same slot-32 + DefaultRace
            # criterion coverage uses, so non-DefaultRace body armor is NOT
            # over-suppressed (coverage wouldn't cover it -> it stays here).
            if _armo_routed_to_skypatcher(m_armo.payload,
                                          master_esp.header.masters,
                                          master_name):
                continue
            if _fsp:
                # FULL SKYPATCHER: link the master ARMO, no override (see the
                # same-plugin branch above).
                _armo_abs = _record_abs_fid(
                    m_armo.formid, master_esp.header.masters, master_name)
                _adds = _sp_links.setdefault(_armo_abs, [])
                for _s, _d in esp.iter_subrecords(m_armo.payload):
                    if _s == ARMO_ARMATURE_SIG and len(_d) == 4:
                        _rmf = struct.unpack("<I", _d)[0]
                        _sf = master_to_src_fid.get(_rmf)
                        if _sf is not None and \
                                new_arma_fids.get(_sf) in new_armas_to_add:
                            # identity in the MASTER's space (_rmf): uniform for
                            # both the source-ARMA-scan and mesh-path mints, so
                            # cross-patch dedup of the same vanilla armature works.
                            _sa = _record_abs_fid(
                                _rmf, master_esp.header.masters, master_name)
                            _adds.append((new_arma_fids[_sf], _sa[0], _sa[1]))
                continue

            # Build the override payload: keep all original armatures
            # (FormID-translated to patch space) + append our UBE ARMAs.
            #
            # Skip FULL/DESC from localized masters (Skyrim.esm + DLCs):
            # those subrecords are 4-byte LSTRING indices into an external
            # .strings file, not raw zstrings. Copying them verbatim into
            # our non-localized patch causes garbage inventory names or
            # silent record-parse failure. We synthesize a FULL from EDID
            # instead (items with no FULL are dropped from inventory UI).
            # DESC is tooltip-only; leaving it stripped is fine.
            STRIP_FROM_LOCALIZED_OVERRIDE = {
                b"FULL", b"DESC", b"ITXT", b"NNAM", b"RDMP",
            }
            # Recover this ARMO's display name: try the STRINGS table first;
            # fall back to synthesizing from EDID.
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
            # Insert new MODLs immediately after the LAST existing MODL, not
            # at the end. Skyrim stops reading armatures at DATA, so any MODL
            # placed after DATA is silently ignored and the armor renders
            # invisible on UBE-race characters.
            # If no existing MODLs, splice before DATA (canonical position).
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
                # Inject synthetic FULL right after EDID (canonical position).
                if (sig == b"EDID" and not _full_inserted and
                        _synth_full is not None):
                    new_payload += esp.encode_subrecord(
                        b"FULL", esp.encode_zstring(_synth_full))
                    _full_inserted = True
                # Splice our new ARMAs after the last existing MODL.
                if i == last_modl_idx:
                    for nfid in new_armas_to_add:
                        new_payload += esp.encode_subrecord(
                            ARMO_ARMATURE_SIG, struct.pack("<I", nfid))
            if last_modl_idx < 0:
                # No existing armatures — splice before DATA or append.
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
                # Carry the master record's flags (Non-Playable etc., #xedit5).
                sig=b"ARMO", flags=m_armo.flags, formid=new_armo_fid,
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

    # FULL SKYPATCHER links: resolve minted fids to their Record objects BEFORE
    # prune_unused_masters renumbers FormIDs (same trap the male-fallback
    # sidecar documents); serialize with the post-prune rec.formid after save.
    _sp_rec_links: "dict[tuple[str, int], list]" = {}
    if _fsp and _sp_links:
        _fid2rec = {r.formid: r for r in new_arma_records}
        for _abs, _adds in _sp_links.items():
            _rl = [(_fid2rec[_mf], _d, _l) for _mf, _d, _l in _adds
                   if _mf in _fid2rec]
            if _rl:
                _sp_rec_links[_abs] = _rl

    # Prune masters that no FormID actually references. Source ESPs commonly
    # carry Update.esm or other unused masters; hand-authored UBE patches
    # strip those, so we do too.
    prune_unused_masters(out)

    out.save(output_esp_path)

    # SkyPatcher-links sidecar (FULL SKYPATCHER): the merge reads this, remaps
    # the minted fids through its own renumbering, and emits the final
    # armorAddonsToAdd INI lines. JSON entries: armo defining plugin + local id,
    # and per added armature its (post-prune) patch fid + the SOURCE armature's
    # identity (for cross-patch dedup of same-armature adds).
    _sp_sidecar = Path(str(output_esp_path) + ".skypatcher.json")
    try:
        if _sp_rec_links:
            import json as _json
            _doc = [{"armo": [d, l],
                     "adds": [{"fid": rec.formid, "src": [sd, sl]}
                              for rec, sd, sl in adds]}
                    for (d, l), adds in _sp_rec_links.items()]
            _sp_sidecar.write_text(_json.dumps(_doc, indent=1),
                                   encoding="utf-8")
        elif _sp_sidecar.is_file():
            _sp_sidecar.unlink()   # stale sidecar from an older run
    except OSError:
        pass

    # Sidecar for the female-model re-check: ARMAs whose female model was
    # redirected to the male mesh at patch time (female NIF not yet converted).
    # restore_female_models() re-checks these against the complete output
    # before the merge, using FormIDs as renumbered by prune_unused_masters.
    sidecar = Path(str(output_esp_path) + ".male_fallbacks.json")
    fb_entries = [dict(fid=rec.formid, **e)
                  for rec, fbs in male_fallback_records for e in fbs]
    try:
        if fb_entries:
            import json as _json
            sidecar.write_text(_json.dumps(fb_entries, indent=1),
                               encoding="utf-8")
        elif sidecar.is_file():
            sidecar.unlink()  # stale sidecar from an older run of this patch
    except OSError:
        pass  # sidecar is an optimization; the patch itself is complete

    # Post-save structural sanity check. Catches subrecord-ordering bugs
    # (like MODL-after-DATA), broken master ordering, FormID drift, and
    # transitive-master crash hazards BEFORE the user tries the patch
    # in-game. Warnings only — does not raise; the patch is still written.
    validation_warnings = validate_patch(
        output_esp_path,
        master_data_dirs=master_data_dirs,
    )
    if master_scan_skipped:
        validation_warnings = list(validation_warnings) + [
            f"master-coverage-skipped: could not load "
            f"{len(set(master_scan_skipped))} master(s) for the UBE-race ARMO "
            f"override scan ({', '.join(sorted(set(master_scan_skipped)))}); "
            f"armor defined there may be invisible on UBE-race actors"
        ]

    return {
        "output": str(output_esp_path),
        "masters": out.header.masters,
        "new_arma_count": len(new_arma_records),
        "male_fallbacks": len(fb_entries),
        "armo_override_count": len(armo_overrides),
        "master_armo_overrides": len(master_armo_overrides),
        "master_scan_per_esm": master_scan_stats,
        "skypatcher_link_targets": len(_sp_rec_links),
        "validation_warnings": validation_warnings,
    }


# Prefixes from validate_patch that are LOAD-BREAKING on the final plugin (CTD /
# FormID misresolution) vs merely invisible/cosmetic. Used to decide whether a
# postflight finding fails the build or is only surfaced as a warning.
_POSTFLIGHT_CTD_PREFIXES = (
    "master-ordering", "esl-overflow", "formid-out-of-range",
    "formid-zero", "modt-malformed",
)
# NOTE: "unmappable-master-ref" is deliberately NOT here (soft, not CTD). It flags
# master-LIST incompleteness (a master X in the list whose own master Y isn't),
# which is NOT load-breaking: a plugin only needs its DIRECT refs resolvable, and
# Skyrim loads transitive masters via each master's own master list. If the patch
# doesn't master Y it CANNOT encode a ref to Y (no top byte), so there's no
# misroute. The only real misroute mode -- an out-of-range top byte -- is caught
# by "formid-out-of-range" (which IS CTD above). Empirically confirmed: on the
# real modlist this fired on Requiem/Legacy/Asuras with 0/98,972 refs out of range
# and the game loaded fine. Kept as a soft warning so genuine oddities still show.


def postflight_validate_combined(combined_path, meshes_root=None, *,
                                 master_data_dirs=None) -> dict:
    """Re-validate the FINAL merged Combined ESP (and any ESL split pieces) AFTER
    the merge + winner-rebase + alt-texture reconcile + hands-slot fix have run.

    `validate_patch` runs per-SOURCE at generation time and never sees those
    post-merge mutations, so a structural break they introduce on the actual
    loaded plugin (the Combined) is otherwise invisible until an in-game CTD /
    invisible armor (the historical "stale Combined / ESL overflow / master-order"
    class). This is the single highest-leverage convert-time guard.

    Returns {"ctd": [(piece, warn)], "soft": [(piece, warn)], "pieces": [name,...]}.
    CTD = load-breaking (caller should fail the build); soft = invisible/cosmetic
    (warn only). Globs `<stem>*.esp` so ESL split pieces are all covered."""
    combined_path = Path(combined_path)
    pieces = sorted(combined_path.parent.glob(combined_path.stem + "*.esp"))
    if combined_path.is_file() and combined_path not in pieces:
        pieces.append(combined_path)
    ctd: list = []
    soft: list = []
    for piece in pieces:
        try:
            warns = validate_patch(piece, meshes_root,
                                   master_data_dirs=master_data_dirs)
        except Exception as e:
            soft.append((piece.name, f"postflight-load-error: {e!r}"))
            continue
        for w in warns:
            prefix = w.split(":", 1)[0].strip()
            (ctd if prefix in _POSTFLIGHT_CTD_PREFIXES else soft).append(
                (piece.name, w))
    return {"ctd": ctd, "soft": soft, "pieces": [p.name for p in pieces]}


def validate_patch(esp_path: str | Path,
                   meshes_root: str | Path | None = None,
                   *,
                   check_nifs: bool = True,
                   master_data_dirs: list[Path] | None = None) -> list[str]:
    """Walk a generated patch ESP and return a list of warning strings
    for structural problems. Empty list = clean.

    Warning prefixes (stable for downstream grep/filter):
      "modl-after-data"        ARMO has MODL after DATA; Skyrim stops reading
                               armatures at DATA, so those are silently ignored.
      "master-ordering"        ESM master appears after a regular ESP; crash.
      "next-object-id"         next_object_id <= max own FormID; engine may
                               collide dynamic FormIDs with patch records.
      "esl-overflow"           ESL flag set but own record count > 2048.
      "formid-zero"            Record has FormID 0x00000000 (player-reserved).
      "formid-out-of-range"    FormID references master index past the list end.
      "missing-nif"            ARMA MOD3/MOD5 path not found on disk.
      "armo-missing-full"      ARMO has no FULL; inventory UI silently hides it.
      "unmappable-master-ref"  Override record references a transitive master
                               not in this patch's master list; FormID misroutes.
      "modt-malformed"         MO?T block with bad header (len != 12*(1+count));
                               engine misreads as millions of entries -> CTD.

    Args:
      esp_path: the patch ESP to validate.
      meshes_root: optional path to the mod's `meshes/` directory for
        NIF-existence checking. Skipped if the directory can't be found.
    """
    warnings: list[str] = []
    esp_path = Path(esp_path)
    e = esp.ESP.load(esp_path)

    # Master ordering: use _is_esm_tier_master (TES4 ESM flag 0x1, not extension)
    # so .esl and ESM-flagged .esp are classified correctly.
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
    modt_malformed = 0
    modt_examples: list[str] = []
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
                # KWDA is an array of 4-byte FormIDs; range-check each entry.
                elif sig == b"KWDA" and len(sd) >= 4 and len(sd) % 4 == 0:
                    for _off in range(0, len(sd), 4):
                        fid = struct.unpack_from("<I", sd, _off)[0]
                        if ((fid >> 24) & 0xFF) > own_byte:
                            out_of_range += 1
                            if len(out_of_range_examples) < 3:
                                out_of_range_examples.append(
                                    f"{fid:08X} (KWDA in {r.formid:08X})")
                # MO?T: validate header (defense against headerless-MODT overread CTD).
                elif sig in ARMA_MODT_SIGS:
                    _valid = (len(sd) >= 12
                              and len(sd) == 12 * (1 + struct.unpack_from(
                                  "<I", sd, 4)[0]))
                    if not _valid:
                        modt_malformed += 1
                        if len(modt_examples) < 3:
                            modt_examples.append(
                                f"{sig.decode()} in {r.formid:08X} (len={len(sd)})")
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
    if modt_malformed:
        warnings.append(
            f"modt-malformed: {modt_malformed} MO?T texture-hash block(s) with a "
            f"bad header (len != 12*(1+count)) -> 7.5M-entry overread CTD risk. "
            f"Examples: {modt_examples}. Fix: route the MO?T through normalize_modt."
        )

    # ESL flag consistency. The light-plugin FormID space (capped at
    # ESL_MAX_OWN_RECORDS) is consumed by EVERY new own-index record, not just
    # ARMA. Today the converter only mints own-index ARMA (ARMO overrides keep
    # their master FormID), so counting ARMA-only gives the same number -- but
    # counting ALL own-index records is future-proof: a later non-ARMA own-index
    # mint would otherwise silently under-count and re-open the overflow-CTD class.
    if e.header.flags & TES4_FLAG_ESL:
        own_new_count = 0
        for g in e.groups:
            for r in g.records:
                if ((r.formid >> 24) & 0xFF) == own_byte:
                    own_new_count += 1
        if own_new_count > ESL_MAX_OWN_RECORDS:
            warnings.append(
                f"esl-overflow: ESL flag set but own new-record count "
                f"{own_new_count} > {ESL_MAX_OWN_RECORDS} slot limit"
            )

    # ARMO MODL-before-DATA check + ARMO-missing-FULL check.
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

    # Unmappable transitive-master check: verify every master used by override
    # records also has its own transitive masters in our patch's master list.
    # Missing transitive masters cause silent FormID misroute (startup crash).
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
                # Header-only read to get this master's own master list.
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
        elif sig in ALT_TEXTURE_SIGS:
            # MO?S: collect embedded TXST FormIDs via the alt-texture walker
            # so prune doesn't drop masters that own color TXSTs.
            _txsts: list[int] = []
            _remap_alt_texture_payload(data, lambda f: (_txsts.append(f) or f))
            yield from _txsts


def _rewrite_formids_in_payload(payload: bytes, remap: dict[int, int]) -> bytes:
    """Rebuild a payload, applying `remap` (old_top_byte -> new_top_byte) to
    every FormID in the known FormID-bearing subrecords. Other subrecords are
    copied verbatim."""
    def _rt(fid: int) -> int:
        top = (fid >> 24) & 0xFF
        return (remap[top] << 24) | (fid & 0xFFFFFF) if top in remap else fid

    out = b""
    for sig, data in esp.iter_subrecords(payload):
        if sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4:
            out += esp.encode_subrecord(
                sig, struct.pack("<I", _rt(struct.unpack("<I", data)[0])))
        elif sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0:
            new_data = b"".join(
                struct.pack("<I", _rt(struct.unpack_from("<I", data, i)[0]))
                for i in range(0, len(data), 4))
            out += esp.encode_subrecord(sig, new_data)
        elif sig in ALT_TEXTURE_SIGS:
            # MO?S: nested (name + TXST + index) format; remap via alt-texture walker.
            # Without remapping them here, dropping/reordering a master (prune,
            # ESL-split, race-skin fold) leaves the color-variant TXST pointing
            # at the WRONG, off-by-one master -> all color variants render the
            # base texture (multi-layer garment alt-texture bug). Reuse the
            # alt-texture walker with the same top-byte remap.
            out += esp.encode_subrecord(sig, _remap_alt_texture_payload(data, _rt))
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


def resort_masters(esp_obj: esp.ESP,
                   master_data_dirs: "list[Path] | None" = None) -> bool:
    """Re-sort a plugin's master list so master-tier plugins (.esm/.esl/ESM- or
    ESL-flagged .esp) precede regular ESPs, renumbering every FormID in place.

    A master-tier plugin listed AFTER a regular ESP is a load-order / FormID
    resolution crash. The merge already tier-sorts (merge_patches), but a STALE
    Combined left by an earlier run can survive mis-sorted; this repairs one
    in place WITHOUT a re-merge (reuses prune_unused_masters' exact FormID-remap
    path -- only the master COUNT is unchanged, so own-record FormIDs are
    untouched). Vanilla DLC ESMs stay first in their canonical order. Returns True
    if the order changed. No-op (False) if already correctly ordered."""
    masters = list(esp_obj.header.masters)
    n = len(masters)
    if n <= 1:
        return False
    name_to_idx: dict[str, int] = {}
    for i, m in enumerate(masters):
        name_to_idx.setdefault(m.lower(), i)
    # Vanilla DLC first, in canonical order (only those actually present).
    new_order: list[int] = []
    used: set[int] = set()
    for vm in VANILLA_DLC_MASTERS:
        idx = name_to_idx.get(vm.lower())
        if idx is not None and idx not in used:
            new_order.append(idx)
            used.add(idx)
    # The rest, STABLE-sorted by tier (master-tier first) -- mirrors merge_patches.
    rest = [i for i in range(n) if i not in used]
    rest.sort(key=lambda i: 0 if _is_esm_tier_master(masters[i], master_data_dirs)
              else 1)
    new_order.extend(rest)
    if new_order == list(range(n)):
        return False  # already correctly ordered
    # old top byte -> new top byte (count unchanged -> own_byte == n is untouched).
    remap = {old: new for new, old in enumerate(new_order) if new != old}
    if remap:
        for g in esp_obj.groups:
            for r in g.records:
                old_top = (r.formid >> 24) & 0xFF
                if old_top in remap:
                    r.formid = (remap[old_top] << 24) | (r.formid & 0xFFFFFF)
                r.payload = _rewrite_formids_in_payload(r.payload, remap)
    esp_obj.header.masters = [masters[i] for i in new_order]
    return True


def resort_masters_all(primary_esp_path, master_data_dirs=None) -> int:
    """Re-sort the master list of the primary merged ESP AND every ESL-split piece
    (`<stem>.esp`, `<stem>2.esp`, ...) so a master-tier plugin never trails a
    regular ESP. A no-op on a correctly-ordered piece; self-heals a STALE Combined
    a prior run left mis-sorted. Globs the same family the split writer uses.
    Returns the number of pieces re-sorted."""
    import sys as _sys
    import time as _time
    from pathlib import Path as _Path
    # Re-classify FRESH: a stale ESM-tier verdict cached during the per-source /
    # merge phase would mis-classify a .esp ESM-flag and re-introduce the mis-sort
    # this repairs. (#postflight)
    clear_esm_tier_cache()
    clear_master_path_cache()
    p = _Path(primary_esp_path)
    changed = 0
    for piece in sorted(p.parent.glob(f"{p.stem}*{p.suffix}")):
        try:
            e = esp.ESP.load(piece)
        except Exception as _le:
            print(f"  !! master re-sort: could not load {piece.name} ({_le!r})",
                  file=_sys.stderr)
            continue
        if not resort_masters(e, master_data_dirs):
            continue
        # Save with a short retry: a TRANSIENT lock (AV scanning the output) is the
        # likeliest reason a re-sort silently failed before, leaving the piece
        # mis-sorted -> equip/load CTD. Surface a persistent failure LOUDLY rather
        # than swallow it; the postflight then also flags it.
        saved = False
        for _attempt in range(4):
            try:
                e.save(piece)
                saved = True
                break
            except Exception as _se:
                if _attempt < 3:
                    _time.sleep(0.4)
                else:
                    print(f"  !! master re-sort COULD NOT SAVE {piece.name} "
                          f"({_se!r}) -> it stays mis-sorted, re-run the merge",
                          file=_sys.stderr)
        if saved:
            changed += 1
    return changed


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

    Why: NioOverride's BodyMorph only morphs shapes whose ARMA covers slot 32.
    Slot-49-only cloth never receives body-slider deformation. Promoting to
    slot 32 enables morphs; trade-off is that equipping will unequip a slot-32
    cuirass (same as hand-authored UBE cloth).

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
            if slots & bit32:
                continue
            # Only promote slot-49 cloth (waist-area cloth semantics).
            if not (slots & (1 << (49 - 30))):
                continue
            # If any model NIF has a BODYTRI shape, promote to slot 32.
            has_bodytri = False
            for path in _arma_model_paths(rec.payload):
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
TES4_FLAG_ESL = 0x00000200   # Light plugin (compact form ID range)

# ESL own-record FormID range: 0x800-0xFFF = 2048 slots.
ESL_OWN_FORMID_MIN = 0x000800
ESL_OWN_FORMID_MAX = 0x000FFF
ESL_MAX_OWN_RECORDS = ESL_OWN_FORMID_MAX - ESL_OWN_FORMID_MIN + 1  # 2048


# Bit position in BOD2/BODT bipedObjectFlags for slot 32 (the body slot).
# slot 30 = bit 0, slot 32 = bit 2.
_BIPED_SLOT_BODY_BIT = 1 << (32 - 30)

# Slots 33 (hands) + 37 (feet). The engine matches nude hand/foot skin by the
# ARMA's PRIMARY race (RNAM) only, not the additional-race list. Gauntlets/boots
# must keep their source primary (DefaultRace) so the UBE races resolve to it via
# RaceCompatibility. Replacing it with UBE_BretonRace makes them invisible on all
# non-Breton UBE actors. Slot 32 (body) is exempt; its skin is routed per-race.
_BIPED_SLOT_HANDS_FEET_BITS = (1 << (33 - 30)) | (1 << (37 - 30))

# Slot 33 (hands) only. Pure gauntlets (slot 33 set, slot 32 NOT) are routed
# through the ESP-only fallback (original mesh + UBE races) when GAUNTLET_ESP_ONLY
# is True. Body+hands suits still convert the body mesh normally.
_BIPED_SLOT_HANDS_BIT = 1 << (33 - 30)

# False = use converted gauntlet mesh (renders + morphs).
# True = ESP-only fallback (original CBBE mesh + UBE races) if converted still vanish.
GAUNTLET_ESP_ONLY = False

# Nude body-skin mesh basenames (all genders, 1st-person, beast variants).
# We must never extend these armatures to the UBE races: doing so adds a
# competing skin ARMA to the nude-skin list that wins over UBE_AllRace's own
# 00UBE_Naked* entries (our patch loads last), causing UBE actors to render
# CBBE hands/feet while the body stays UBE. Equippable armor is unaffected.
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
    """True if a model path is a nude body-skin mesh (body/hands/feet), so a
    coverage pass never extends its armature to the UBE races (doing so makes a
    competing skin armature win over UBE's own 00UBE_Naked* and the actor
    renders the wrong nude skin). Matches by weight-stripped basename."""
    if not path:
        return False
    # Meshes under character assets skin folders (catches child skins, DLC
    # vampire skin, unique-NPC skins) — never treat as equippable armor.
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
    # (FemaleHandsArgonian, FemaleHandsKhajiit, FemaleHandsUniqueNpc, ...),
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
    string_resolver, remap_fid=None,
) -> "esp.Record | None":
    """Build an ARMO override appending our minted UBE body ARMAs to a master
    ARMO's armature list. Mirrors generate_ube_patch's proven master-ARMO
    builder exactly: armature MODLs stay grouped BEFORE DATA (Skyrim stops
    reading armatures at DATA), localized FULL/DESC are stripped (a synthetic
    FULL is re-injected after EDID so the item shows in inventory), and all
    FormIDs are remapped to patch space. Returns None if this ARMO references
    none of our minted ARMAs. `master_patch_byte` is already << 24.

    `remap_fid` (optional) maps any FormID in the source ARMO to patch space, or
    None if its master is absent from the patch. When supplied, FormID-bearing
    subrecords (TNAM/EITM/ETYP/KWDA/...) are remapped rather than copied verbatim
    with the master's original byte (which would name the WRONG plugin in patch
    space -> dangling FormID -> possible load CTD); unmappable refs are dropped."""
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

    def _arma_ref(ref: int) -> int:
        # Existing armature ref -> patch space. Prefer the full master-byte remap
        # (correct for a ref into a TRANSITIVE master); fall back to the single-
        # byte rebase when no remap is supplied / the byte is unmappable.
        if remap_fid is not None:
            m = remap_fid(ref)
            if m is not None:
                return m
        return master_patch_byte | (ref & 0xFFFFFF)

    for i, (sig, data) in enumerate(pieces):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            ref = struct.unpack("<I", data)[0]
            out += esp.encode_subrecord(sig, struct.pack("<I", _arma_ref(ref)))
        elif sig in _STRIP_LOCALIZED_ARMO:
            pass  # LSTRING refs we can't resolve in a non-localized patch
        elif (remap_fid is not None
              and sig in FORMID_SINGLE_SUBRECORD_SIGS and len(data) == 4):
            # FormID-bearing subrecord (TNAM/EITM/ETYP/YNAM/ZNAM/BIDS/BAMT):
            # remap its master byte to patch space. An unmappable ref (transitive
            # master absent from the patch) is DROPPED -- a dangling TNAM/EITM can
            # CTD at load, and the old verbatim copy kept the WRONG master byte.
            _m = remap_fid(struct.unpack("<I", data)[0])
            if _m is not None:
                out += esp.encode_subrecord(sig, struct.pack("<I", _m))
        elif (remap_fid is not None
              and sig in FORMID_ARRAY_SUBRECORD_SIGS and len(data) % 4 == 0):
            # KWDA: keep the mappable keywords, drop the unmappable ones.
            _mapped = [remap_fid(struct.unpack_from("<I", data, j)[0])
                       for j in range(0, len(data), 4)]
            _kept = b"".join(struct.pack("<I", v) for v in _mapped if v is not None)
            if _kept:
                out += esp.encode_subrecord(sig, _kept)
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


# ESM-tier verdict cache keyed by lowercased name. Not cached on lookup failure:
# narrow per-mod data_dirs could poison the verdict before the later batch merge
# (with full data_dirs) re-reads the real flag. Stale False causes ESL-flagged
# .esp to sort after a regular .esp -> master-order crash.
_ESM_TIER_CACHE: dict[str, bool] = {}


def clear_esm_tier_cache() -> None:
    _ESM_TIER_CACHE.clear()


# Per-master TES4-only master-list cache. Parses only the TES4 record, not
# the whole multi-MB plugin; full ESP.load per master made validate_patch slow.
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
    """True if `name` must precede regular ESPs in a master list.
    Master-tier = TES4 flags 0x1 (ESM) or 0x200 (ESL/light), which includes
    .esm, .esl, ESM-flagged .esp (USSEP), and ESL-flagged .esp (ESPFE).
    Checking only 0x1 mislabels ESL-flagged .esp as regular -> order crash.
    Falls back to extension if the file can't be located."""
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
                result = bool(flags & 0x201)  # 0x1 = ESM, 0x200 = ESL
                _ESM_TIER_CACHE[low] = result  # cache only real on-disk reads
                return result
    return False  # not cached on failure (see _ESM_TIER_CACHE note above)


# ----- Winner-aware ARMO override rebasing -----------------------------------
# The winner index lets merge_patches overlay load-order winner stats/keywords
# onto our override instead of basing it on the bare master record.

class _WinnerRecord:
    __slots__ = ("plugin_name", "plugin_masters", "payload", "is_localized",
                 "rec_flags")

    def __init__(self, plugin_name, plugin_masters, payload, is_localized,
                 rec_flags=0):
        self.plugin_name = plugin_name
        self.plugin_masters = plugin_masters
        self.payload = payload
        self.is_localized = is_localized
        # TES4 record flags (e.g. 0x4 Non-Playable). Overrides must carry the
        # winner's flags: a flags=0 override made NPC-only armor PLAYABLE (#xedit5).
        self.rec_flags = rec_flags


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
            index[abs_id] = _WinnerRecord(name, masters, rec.payload, is_loc,
                                          rec.flags)
    return index


# ARMO subrecords with no FormID — adoptable from the winner without adding a
# new master. Covers the balance fields overhauls typically change.
_WINNER_STAT_NOFID_SIGS = (b"EDID", b"OBND", b"FULL", b"BOD2", b"DATA", b"DNAM")


def _overlay_winner_stats(
    base_payload: bytes,
    winner: "_WinnerRecord",
    merged_masters: list[str],
) -> bytes:
    """Overlay the load-order winner's balance onto the base override.
    Adopts only no-FormID stat subrecords (armor rating/type/name) plus KWDA
    when every keyword is already in the merged master list. Armatures stay
    base-derived (winner armatures would require mastering 100+ plugins)."""
    def _merged_idx(name: str) -> "int | None":
        nl = name.lower()
        for idx, mn in enumerate(merged_masters):
            if mn.lower() == nl:
                return idx
        return None

    # Remap winner's master bytes to merged space; skip any not present.
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
        elif sig == b"RNAM" and len(data) == 4:
            # Adopt the winner's race when it resolves through our master list
            # (an overhaul re-racing an item to DefaultRace is a real balance/
            # visibility change; keeping the base race regressed it — #xedit5).
            fid = struct.unpack("<I", data)[0]
            top = (fid >> 24) & 0xFF
            if top != winner_own_byte and top in wbr:
                adopt[b"RNAM"] = struct.pack(
                    "<I", (wbr[top] << 24) | (fid & 0xFFFFFF))
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
    # KSIZ must equal the KWDA entry count. Adopting the winner's KWDA without
    # updating KSIZ left base-count/winner-array desyncs (KSIZ=6 vs 8 keywords)
    # -> xEdit garbles the record view and the engine's keyword read is
    # undefined (#xedit5). Recompute whenever KWDA is adopted.
    if b"KWDA" in adopt:
        adopt[b"KSIZ"] = struct.pack("<I", len(adopt[b"KWDA"]) // 4)

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


# ----- Mod-defined non-body UBE coverage (the guard-helmet class) ----------
# Vanilla ARMA records get UBE races at runtime (RaceCompatibility /
# RaceDispatcher); overhaul-defined ARMAs (e.g. a re-armored guard helmet with
# 19 vanilla races, 0 UBE) still slip through.
# This pass closes the gap: scan the load order for non-body ARMOs whose
# winning armatures lack UBE coverage, mint a UBE-primary ARMA per missing
# armature (same non-body mesh — UBE only reshapes the torso), and override
# the winning ARMO to include it.

_HAIR_ONLY_SLOTS = 0x802          # biped slots 31 (Hair) | 41 (LongHair)
_BODY_SLOT_BIT_32 = 1 << 2        # biped slot 32 (Body)
_ARMORHELMET_KW_LOW24 = 0x06BBD9  # Skyrim.esm ArmorHelmet keyword


def _hair_only_armo_is_equippable_headgear(payload, masters) -> bool:
    """True if a hair-slot-only ARMO is real headgear (hides hair, has gold
    value or ArmorHelmet keyword) rather than a cosmetic hairstyle ARMO
    (value 0, no armor keyword)."""
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"DATA" and len(d) >= 4:
            if struct.unpack_from("<I", d, 0)[0] > 0:
                return True            # has a gold value -> real equipment
        elif sig == b"KWDA" and len(d) >= 4:
            for i in range(len(d) // 4):
                fid = struct.unpack_from("<I", d, i * 4)[0]
                mi = fid >> 24
                if (fid & 0xFFFFFF) == _ARMORHELMET_KW_LOW24 and \
                        mi < len(masters) and masters[mi].lower() == "skyrim.esm":
                    return True        # ArmorHelmet keyword -> headgear armor
    return False
# Slots that deform with the UBE body and need mesh conversion, not just race
# coverage: 32 body, 33 hands, 34 forearms, 37 feet, 38 calves.
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
    exclude_armo_abs: "set[tuple[str, int]] | None" = None,
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
    # Targets: playable, non-body, non-hair-only ARMOs whose winning armatures
    # all lack UBE coverage and have >=1 DefaultRace armature to mint.
    # We mint one UBE-primary ARMA per unique source armature, then add it via
    # SkyPatcher (no ESP override -> no master explosion).
    # ARMO RNAM is not filtered: it's frequently a quirky authoring choice
    # (e.g. DeerRace on human gear). The real beast-race guard is the
    # armature-level DefaultRace filter below.
    ARMO_NONPLAYABLE_FLAG = 0x00000004
    targets = []   # (armo_abs, defining_plugin_case, [arma_abs to mint])
    mint_set: dict = {}  # arma_abs -> placeholder (filled with minted fid later)
    for armo_abs, (apayload, am, an, arms, rnam, slots, edid, aflags) in armo_win.items():
        # FULL SKYPATCHER: skip ARMOs the Combined INI already links --
        # armature lists in ESPs no longer reflect runtime coverage, so
        # without this the fallback re-covers everything -> DOUBLE
        # armature (body renders twice = clipping) and UBE-primary
        # hands mints (invisible gauntlets). #fsp-dedup
        if exclude_armo_abs and armo_abs in exclude_armo_abs:
            continue
        if aflags & ARMO_NONPLAYABLE_FLAG:
            continue
        if slots & _DEFORMING_SLOTS_MASK:
            continue
        if slots and (slots & _HAIR_ONLY_SLOTS) == slots and \
                not _hair_only_armo_is_equippable_headgear(apayload, am):
            continue
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        if any(v[4] for _x, v in winning):
            continue  # already has a UBE armature
        # Mint only DefaultRace armatures (human/mer); beast armatures crash.
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

    # Drop source-master FormID refs + texture data so the minted ARMA references
    # only UBE_AllRace (races) + mesh paths; keeps the ESP master list minimal.
    # NAM0-3 = skin-TXST / texture-swap FormIDs; strip for same reason as MO?S.
    STRIP = {b"SNDD", b"ONAM", b"MO2S", b"MO3S", b"MO4S", b"MO5S",
             b"MO2T", b"MO3T", b"MO4T", b"MO5T",
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
    exclude_armo_abs: "set[tuple[str, int]] | None" = None,
    master_data_dirs: "list[Path] | None" = None,
    cover_all: bool = False,
    preserve_textures: bool = False,
    author: str = "cbbe-to-ube modded body UBE coverage",
    description: str = "UBE race coverage for mod-defined body armor variants",
) -> dict:
    # cover_all=True makes this the PRIMARY body-armor path (full SkyPatcher):
    # it no longer defers to ARMOs the ESP-override path already patched, so it
    # covers EVERY converted body armor. Default False = today's fallback role.
    # preserve_textures=True keeps each minted armature's alt-textures (MO?S) and
    # skin swaps (NAM0-3), remapped into this ESP's master space, so recolor
    # variants keep their look under full SkyPatcher (the ESP-override path did
    # this via per-source patches; here one ESP unions the needed masters).
    # Default False = today's minimal-master behavior (strip texture refs).
    """Body-slot counterpart of generate_modded_nonbody_ube_coverage_patch.

    Covers overhaul-added variant ARMOs (e.g. a mod-defined armor variant
    that reuses a vanilla armature whose mesh we converted) but whose own ARMO was
    never patched. Mints a UBE-primary ARMA per source armature with the model
    redirected to the !UBE mesh; adds it via SkyPatcher. Only armatures with an
    actual !UBE conversion are minted (unconverted CBBE mesh on UBE would clip).
    Returns stats."""
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
        # FULL SKYPATCHER: skip ARMOs the Combined INI already links --
        # armature lists in ESPs no longer reflect runtime coverage, so
        # without this the fallback re-covers everything -> DOUBLE
        # armature (body renders twice = clipping) and UBE-primary
        # hands mints (invisible gauntlets). #fsp-dedup
        if exclude_armo_abs and armo_abs in exclude_armo_abs:
            continue
        # cover_all relaxes two skips ONLY for TORSO body (slot 32) items, because
        # the full-SkyPatcher path suppresses their ESP ARMO override so coverage
        # must be their sole path: (1) NON-PLAYABLE armor (the ESP-override path
        # covered non-playable NPC body armor too -- skipping it here would leave
        # ~hundreds invisible on UBE NPCs); (2) armatures that ALREADY have a UBE
        # armature. Hands/feet (33/37) keep both skips (fallback role only) so
        # they stay on the source-primary ESP-override path.
        _is_body = bool(slots & _BIPED_SLOT_BODY_BIT)
        _cover_body = cover_all and _is_body
        if (aflags & ARMO_NONPLAYABLE_FLAG) and not _cover_body:
            continue
        if not (slots & _DEFORMING_SLOTS_MASK):
            continue                       # only body/hands/feet here (the inverse of non-body)
        # Full-SkyPatcher PRIMARY role (cover_all) covers TORSO body (slot 32)
        # ONLY -- the per-source builder suppresses just those. Pure hands/feet
        # (33/37) stay on the source-primary ESP-override path, so covering them
        # here would double them up with a UBE-primary armature (the nude-hands
        # invisibility bug). The fallback role (cover_all=False) still covers the
        # full deforming mask for items the ESP-override missed.
        if cover_all and not _is_body:
            continue
        if rnam != DEFAULT_RACE:
            continue                       # beast/custom race -> never UBE-extend
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        if any(v[4] for _x, v in winning) and not _cover_body:
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
    # preserve_textures: declare the masters each minted armature's alt-textures
    # (MO?S) / skin swaps (NAM0-3) reference, so the refs can be remapped into
    # THIS ESP's master space instead of stripped. Capped well below the 255
    # top-byte limit (own_byte == len(masters)); an armature whose ref needs a
    # master past the cap simply falls back to the strip path (below).
    _MASTER_CAP = 250
    if preserve_textures:
        for arma_abs in mint_set:
            _pl, _m2, _n2, _rn, _u = arma_win[arma_abs]
            for nm in _arma_texture_master_names(_pl, _m2, _n2):
                if len(patch_masters) < _MASTER_CAP:
                    _add_master_if_missing(patch_masters, nm)
    pidx = {m.lower(): i for i, m in enumerate(patch_masters)}
    own_byte = len(patch_masters)
    ube_byte = pidx[ube_allrace_filename.lower()]
    ube_races_patch = [(ube_byte << 24) | f for f in UBE_RACE_FIDS_24]
    ube_primary_patch = (ube_byte << 24) | UBE_PRIMARY_BRETON_FID_24

    # Default (strip) removes every texture-bearing ref so no source master is
    # needed. preserve_textures keeps them (remapped), stripping only the two
    # non-texture FormID refs (footstep sound / art object) that would dangle.
    STRIP_FULL = {b"SNDD", b"ONAM", b"MO2S", b"MO3S", b"MO4S", b"MO5S",
                  b"MO2T", b"MO3T", b"MO4T", b"MO5T",
                  b"NAM0", b"NAM1", b"NAM2", b"NAM3"}
    STRIP_MIN = {b"SNDD", b"ONAM"}
    new_arma_records: list = []
    next_id = ESL_OWN_FORMID_MIN
    mint_name = out_path.with_suffix(".esp").name
    preserved_count = 0
    preserve_fallbacks: list = []
    for arma_abs in mint_set:
        payload, m2, n2, _rn, _u = arma_win[arma_abs]
        minted_payload = None
        if preserve_textures:
            def _remap(fid: int, _sm=m2, _sn=n2) -> int:
                return remap_fid(fid, _sm, _sn, patch_masters)
            try:
                kept = b"".join(
                    esp.encode_subrecord(s, d)
                    for s, d in esp.iter_subrecords(payload) if s not in STRIP_MIN)
                kept = _remap_arma_skin_txsts(kept, _remap)   # NAM0-3 -> patch space
                minted_payload = rebuild_arma_payload(
                    kept,
                    new_primary_rnam=ube_primary_patch,
                    new_additional_race_fids=ube_races_patch,
                    alt_texture_fid_remap=_remap,             # MO?S -> patch space
                    converted_nif_exists=_conv_exists,
                )
                preserved_count += 1
            except Exception as _e:      # unresolvable master -> strip fallback
                minted_payload = None
                preserve_fallbacks.append((arma_abs[0], arma_abs[1], repr(_e)))
        if minted_payload is None:
            stripped = b"".join(
                esp.encode_subrecord(s, d)
                for s, d in esp.iter_subrecords(payload) if s not in STRIP_FULL)
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
    # preserve_textures unions source masters of varying tiers; tier-sort so a
    # master-tier plugin never trails a regular ESP (both passes remap nested
    # MO?S + NAM refs, so this is ref-safe).
    if preserve_textures:
        resort_masters(out_esp, master_data_dirs=master_data_dirs)
    out_esp.save(out_path)
    warnings = validate_patch(out_path, master_data_dirs=master_data_dirs)

    # ---- Pass 4: SkyPatcher INI (add minted ARMA to each target ARMO) ----
    ini_lines = [
        "; cbbe-to-ube: UBE race coverage for mod-defined BODY armor variants.",
        "; Adds a minted UBE-primary ArmorAddon (redirected to the converted",
        "; !UBE mesh) to each body item whose winning armature lacked UBE races",
        "; (e.g. an overhaul's mod-defined armor variant reusing a vanilla armature).",
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
        "textures_preserved": preserved_count,
        "texture_fallbacks": len(preserve_fallbacks),
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
    _sp_seen_pairs: "set | None" = None,
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

    # ----- Step 0: resolve winner-rebase targets -----
    # For ARMO overrides where a different, non-localized plugin wins in load
    # order, rebase onto that winner's stats. Skip localized (LSTRING travels
    # poorly) and same-source winners (already correct).
    rebase_map: dict[tuple[Path, int], _WinnerRecord] = {}
    rebase_count = 0
    # (patch_path, pre-merge fid) -> merged Record (for the SkyPatcher-links
    # sidecar pass; final fids read AFTER prune renumbering).
    merged_rec_by_key: "dict[tuple[Path, int], esp.Record]" = {}
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
    # Master-tier plugins (TES4 flag 0x1 or 0x200: .esm, .esl, ESM-flagged .esp)
    # must precede regular ESPs or FormIDs mis-resolve on load. Vanilla DLC ESMs
    # are included unconditionally; remaining masters stable-sorted by tier.
    merged_masters: list[str] = []
    for forced in VANILLA_DLC_MASTERS:
        _add_master_if_missing(merged_masters, forced)
    seen = {m.lower() for m in merged_masters}
    rest: list[str] = []
    for _, pe in patches:
        for m in pe.header.masters:
            if m.lower() not in seen:
                seen.add(m.lower())
                rest.append(m)
    rest.sort(key=lambda m: 0 if _is_esm_tier_master(m, master_data_dirs) else 1)
    for m in rest:
        _add_master_if_missing(merged_masters, m)

    own_byte_merged = len(merged_masters)

    # ----- Steps 2 + 3: assign merged FormIDs + build remap table -----
    formid_remap: dict[tuple[Path, int], int] = {}  # (patch_path, old_fid) -> new_fid
    patch_master_remap: dict[Path, dict[int, int]] = {}  # patch_byte -> merged_byte

    next_own_id = ESL_OWN_FORMID_MIN

    # Decide ESL-vs-full before allocating. If new ARMAs > 2048 (ESL limit),
    # downgrade to a full ESP (full 24-bit range; costs one load-order slot).
    # Previous behaviour raised RuntimeError, leaving a stale Combined.esp on disk.
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

    # Two-pass: allocate FormIDs for new ARMAs, then remap payloads.
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

                # Overlay winner balance (stats/keywords) if a different plugin
                # wins this ARMO; keeps our armatures, adds no masters.
                win = rebase_map.get((patch_path, rec.formid)) \
                    if grp.label == b"ARMO" else None
                if win is not None:
                    new_payload = _overlay_winner_stats(
                        new_payload, win, merged_masters)

                new_rec = esp.Record(
                    sig=grp.label,
                    # Rebased ARMO overrides adopt the WINNER's record flags
                    # (Non-Playable etc.) along with its stats (#xedit5).
                    flags=(win.rec_flags if win is not None else rec.flags),
                    formid=new_fid,
                    timestamp_vc=rec.timestamp_vc,
                    version_unk=rec.version_unk,
                    payload=new_payload,
                )
                # (patch, pre-merge fid) -> merged Record. Read rec.formid at
                # the END (prune renumbers) -- used by the SkyPatcher-links pass.
                merged_rec_by_key[(patch_path, rec.formid)] = new_rec
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

    # ---- FULL SKYPATCHER: per-patch link sidecars -> final INI lines ----
    # Emitted only when patches carry .skypatcher.json sidecars (the per-source
    # phase ran with CBBE2UBE_FULL_SKYPATCHER). Dedup by (armo, source-armature)
    # so the same vanilla armature minted by many patches is added ONCE
    # (first-writer-wins, mirroring the old ARMO-override dedup). Pass a shared
    # `_sp_seen_pairs` set across split pieces for cross-piece dedup.
    sp_ini: "list[str]" = []
    sp_by_armo: "dict[tuple[str, int], list[int]]" = {}
    seen_pairs = _sp_seen_pairs if _sp_seen_pairs is not None else set()
    import json as _json
    for patch_path, _pe in patches:
        sc = Path(str(patch_path) + ".skypatcher.json")
        if not sc.is_file():
            continue
        try:
            doc = _json.loads(sc.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ent in doc:
            try:
                d, l = ent["armo"][0], int(ent["armo"][1])
            except Exception:
                continue
            for a in ent.get("adds", []):
                rec = merged_rec_by_key.get((patch_path, int(a.get("fid", -1))))
                if rec is None:
                    continue
                src = a.get("src") or ["", -1]
                pair = ((str(d), l), (str(src[0]), int(src[1])))
                if pair in seen_pairs:
                    continue          # same armature already added elsewhere
                seen_pairs.add(pair)
                sp_by_armo.setdefault((str(d), l), []).append(rec.formid)
    for (d, l), fids in sorted(sp_by_armo.items()):
        adds = ",".join("{}|{:06X}".format(out_path.name, f & 0xFFFFFF)
                        for f in fids)
        sp_ini.append("filterByArmors={}|{:06X}:armorAddonsToAdd={}".format(
            d, l, adds))

    return {
        "output": str(out_path),
        "masters": out_esp.header.masters,
        "skypatcher_ini_lines": sp_ini,
        "skypatcher_targets": len(sp_by_armo),
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
    # Classify masters FRESH for this merge. _is_esm_tier_master consults the
    # _ESM_TIER_CACHE BEFORE the on-disk flag read, so a stale verdict cached
    # during the long per-source phase (e.g. an ESL-flagged .esp resolved via a
    # narrower/differently-ordered dir set) would mis-sort it AFTER a regular
    # master -> ESL-after-regular in the master list -> load-order / FormID
    # resolution CTD. Clearing here forces a re-read against the merge's full
    # batch dirs. (#postflight caught wilderness_witch.esp mis-sorted in a split.)
    clear_esm_tier_cache()
    clear_master_path_cache()
    out_path = Path(output_path)
    plist = [Path(p) for p in patch_paths]
    for p in plist:
        if not p.is_file():
            raise FileNotFoundError(f"patch not found: {p}")

    _sp_seen: set = set()   # cross-piece (armo, src-armature) dedup for links

    def _single(esl):
        s = merge_patches(
            plist, out_path, esl_flag=esl, author=author,
            description=description, master_data_dirs=master_data_dirs,
            armo_winner_index=armo_winner_index, _sp_seen_pairs=_sp_seen)
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
            master_data_dirs=master_data_dirs, armo_winner_index=armo_winner_index,
            _sp_seen_pairs=_sp_seen)
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
        "skypatcher_ini_lines": [l for s in piece_stats
                                 for l in s.get("skypatcher_ini_lines", [])],
        "skypatcher_targets": sum(s.get("skypatcher_targets", 0)
                                  for s in piece_stats),
        "piece_stats": piece_stats,
    }


def _rewrite_payload_for_merge(
    payload: bytes,
    patch_path: Path,
    byte_remap: dict[int, int],
    formid_remap: dict[tuple[Path, int], int],
) -> bytes:
    """Translate FormID references in a record payload from a patch's
    master-space into the merged master-space. formid_remap takes priority
    (handles new ARMAs with fresh ESL FormIDs); byte_remap handles the rest."""
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
            # Normalize MODT: guard against headerless format causing overread CTD.
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

    # Rebuild: emit merged MODLs at the original MODL position (before DATA).
    # MODLs after DATA are silently ignored by the engine.
    existing_pieces = list(esp.iter_subrecords(existing_payload))
    first_modl_idx = -1
    for i, (sig, data) in enumerate(existing_pieces):
        if sig == ARMO_ARMATURE_SIG and len(data) == 4:
            if first_modl_idx < 0:
                first_modl_idx = i
                break

    out = b""
    if first_modl_idx >= 0:
        for i, (sig, data) in enumerate(existing_pieces):
            if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                if i == first_modl_idx:
                    for modl_data in existing_modls:
                        out += esp.encode_subrecord(ARMO_ARMATURE_SIG, modl_data)
                # else: already emitted
            else:
                out += esp.encode_subrecord(sig, data)
    else:
        # No existing MODL — splice before DATA, or append.
        inserted = False
        for sig, data in existing_pieces:
            if sig == b"DATA" and not inserted:
                for modl_data in existing_modls:
                    out += esp.encode_subrecord(ARMO_ARMATURE_SIG, modl_data)
                inserted = True
            out += esp.encode_subrecord(sig, data)
        if not inserted:
            for modl_data in existing_modls:
                out += esp.encode_subrecord(ARMO_ARMATURE_SIG, modl_data)
    return out
