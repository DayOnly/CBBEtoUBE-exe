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
    - Record a SkyPatcher `armorAddonsToAdd` link (ARMO -> new UBE ARMA) in the
      `.skypatcher.json` sidecar. NO ESP ARMO override is emitted -- SkyPatcher
      is the sole delivery path (the legacy ARMO-override machinery was removed).

Output masters: all vanilla DLC ESMs (Skyrim/Update/Dawnguard/HearthFires/
                Dragonborn) unconditionally, then the source mod's own masters,
                UBE_AllRace.esp, and <source mod>.esp.
"""
from __future__ import annotations

import struct
from collections import Counter
from pathlib import Path
from typing import Iterable, NamedTuple

from . import esp
from .bsa_strings import game_codepage_text as _game_codepage_text
from .bsa_strings import model_path_text as _model_path_text
from .envflags import flag as _flag


def _full_skypatcher_enabled() -> bool:
    """SkyPatcher (armorAddonsToAdd) is the ONLY armor-delivery path (the legacy
    ESP ARMO-override machinery was removed once SkyPatcher was proven in-game).
    Every converted armor is delivered as minted UBE ARMAs + per-armo INI links;
    the Combined ESP overrides no third-party records, so the whole override-
    conflict class (winner rebase, flags, keywords, EITM/VMAD, localized strings)
    does not exist. Requires SkyPatcher.dll + iEnableArmorPatching=1 -- a HARD
    runtime dependency for all armor coverage. Retained as a function (always
    True) while the remaining `if _fsp` call sites are inlined. #skypatcher-only"""
    return True


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

# Each vanilla human/mer race and its vampire variant (Skyrim.esm, low 24 bits)
# -> its UBE counterpart above: UBE_AllRace.esp names the UBE race of <X>Race
# `00UBE_<X>Race`, and UBE's own race-compatibility config pairs them the same
# way. Checked against both on the live load order (2026-09-24). The keys are
# the human races a coverage armature may list (#coverage-human-race-list); the
# beast races have no UBE counterpart.
UBE_RACE_FOR_VANILLA_24 = {
    0x013741: 0x005734,  # BretonRace            -> 00UBE_BretonRace
    0x08883C: 0x005735,  # BretonRaceVampire     -> 00UBE_BretonRaceVampire
    0x013744: 0x05A179,  # ImperialRace          -> 00UBE_ImperialRace
    0x088844: 0x05A17A,  # ImperialRaceVampire   -> 00UBE_ImperialRaceVampire
    0x013746: 0x05A184,  # NordRace              -> 00UBE_NordRace
    0x088794: 0x05A185,  # NordRaceVampire       -> 00UBE_NordRaceVampire
    0x013748: 0x05A18E,  # RedguardRace          -> 00UBE_RedguardRace
    0x088846: 0x05A18F,  # RedguardRaceVampire   -> 00UBE_RedguardRaceVampire
    0x013742: 0x05A198,  # DarkElfRace           -> 00UBE_DarkElfRace
    0x08883D: 0x05A199,  # DarkElfRaceVampire    -> 00UBE_DarkElfRaceVampire
    0x013743: 0x05A1A2,  # HighElfRace           -> 00UBE_HighElfRace
    0x088840: 0x05A1A3,  # HighElfRaceVampire    -> 00UBE_HighElfRaceVampire
    0x013749: 0x05A1AC,  # WoodElfRace           -> 00UBE_WoodElfRace
    0x088884: 0x05A1AD,  # WoodElfRaceVampire    -> 00UBE_WoodElfRaceVampire
    0x013747: 0x05A1B0,  # OrcRace               -> 00UBE_OrcRace
    0x0A82B9: 0x05A1B1,  # OrcRaceVampire        -> 00UBE_OrcRaceVampire
}


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
    b"ONAM",   # art object. MUST be classified: the coverage passes already
               # treat ONAM as a FormID ref that "would dangle" and strip it, but
               # while it was missing here _iter_formids_in_payload never yielded
               # it -- so prune_unused_masters could DROP a master referenced only
               # by ONAM and renumber the others around it, leaving the ONAM ref
               # pointing at whatever now occupies that index.
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


def _alttex_dup_occurrence_on() -> bool:
    r"""#alttex-dup-occurrence (2026-09-25): does our own plugin's alternate-
    texture reconcile bind the k-th entry that names a shared shape name to the
    k-th shape of that name? Yes, by default.

    #dup-shape-names ships a source whose shapes share a name with one name per
    shape ('fur', 'fur:1' .. 'fur:5', in the author's order). A colour variant
    in the source plugin addresses those shells by 3D index, each entry still
    named 'fur'. The reconcile matched entries by name and kept one per name, so
    the variant kept one entry, bound to the first shell, and the other shells
    lost their colour.

    Now each entry of a renamed name binds to its own shell, through the source
    mesh the NIF was converted from (`_alttex_exact_provenance_on`); a repeated
    source index addresses the same shell and keeps one entry. With that
    switched off, the entries are ranked by source 3D index and the k-th binds
    to the shape named 'name:k' (k=0: 'name') when the converted NIF's layout
    allows (`_alttex_family_strict_on`). The authored name bytes are kept (the
    engine binds by index). Our own plugins only: a third-party set binds by
    index and the rename keeps the order. Inert unless the converted NIF
    carries renamed shapes or a set names one shape name twice
    (`_alttex_set_provenance_on`) -- on the live pack today no NIF carries
    renamed shapes, and only one NIF converted before the rename is named
    twice by a set (docs/DESIGN.md, #alttex-set-provenance).

    Nested: with #dup-shape-names off no shape is renamed, and a 'name:k' next
    to 'name' is then the author's, so this is off too.
    CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE=1 keeps one entry per name, bound to the
    first shape of that name."""
    from .nif_convert_writer import _dup_shape_names_on
    if not _dup_shape_names_on():
        return False
    return not _flag("CBBE2UBE_NO_ALTTEX_DUP_OCCURRENCE", False)


def _alttex_family_strict_on() -> bool:
    r"""#alttex-family-strict (2026-09-25): does #alttex-dup-occurrence bind
    only a family laid out EXACTLY as the rename lays it out, and only a set
    that names each of its shells? Yes, by default. Read only on the layout
    path, i.e. with CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1: by default the
    source mesh says which shell each entry is (`_alttex_exact_provenance_on`),
    which also settles the case these checks missed (a lost trailing shell).

    The reconcile sees the converted NIF, not the source, so it cannot know
    which shapes the rename made. It took any 'name' beside a 'name:k' for a
    family and bound entries by rank in NIF order. Two ways that put a colour on
    the WRONG shell: a middle shell lost to a failed copy (the partial NIF still
    ships) moved every later shell's colour one shell down, and an authored
    'x:1' before 'x' took the only 'x' entry.

    The rename keeps the first shape's name and calls the k-th of the rest
    'name:k', skipping a name the author already used, in the author's order.
    So a family is kept only when its shapes are 'name', 'name:1' ..
    'name:n' with no suffix missing, in that NIF order ('name' first). And the
    set's entries for the name must address exactly n+1 distinct source
    shells: a set naming only some shells cannot say which, and a shell count
    the NIF does not match is an authored 'name:k' among them or a lost shell.
    Then the entry of rank k (source 3D index) binds to the shape named
    'name:k'. Any other layout or count falls back, for that name, to one entry
    per name as before, kept on the first shape of the name. NOT exact: a lost
    trailing shell with a set naming only some shells, or with an authored
    'name:k' the set does not name, passes both checks and binds a colour to a
    neighbour; and the fallback's one entry is the set's first-listed, which
    can be another shell's colour. That is why the source decides by default.

    CBBE2UBE_NO_ALTTEX_FAMILY_STRICT=1 takes any 'name' beside a 'name:k' as a
    family and binds by rank in NIF order, dropping entries past the last."""
    return not _flag("CBBE2UBE_NO_ALTTEX_FAMILY_STRICT", False)


def _renamed_shape_families(shape_index: "dict[str, int]",
                            set_names: "set[str]") -> "dict[str, list[int]]":
    """#alttex-dup-occurrence: {lowercased shared name: [index, ...]} for every
    name the #dup-shape-names rename split in the converted NIF: item k is the
    index of the shape named 'name:k' (item 0 the shape that kept `name`).
    `set_names` (lowercased) are the names an alternate-texture set carries: a
    'name:k' among them is an authored shape, not a renamed one. A name two
    shapes of the NIF carry in different case is left out (the case-insensitive
    entry match could not tell them apart), and so is any layout the rename
    cannot produce (`_alttex_family_strict_on`)."""
    from .nif_convert_writer import _DUP_NAME_SEP
    split: "dict[str, dict[int, int]]" = {}      # name -> {k: index of 'name:k'}
    for nm, i in shape_index.items():
        base, sep, k = nm.rpartition(_DUP_NAME_SEP)
        if (not sep or not base or base not in shape_index
                or not (k.isascii() and k.isdigit()) or str(int(k)) != k
                or nm.lower() in set_names):
            continue
        split.setdefault(base, {})[int(k)] = i
    if not split:
        return {}
    lower_count: "dict[str, int]" = {}
    for nm in shape_index:
        lower_count[nm.lower()] = lower_count.get(nm.lower(), 0) + 1
    strict = _alttex_family_strict_on()
    families: "dict[str, list[int]]" = {}
    for base, by_k in split.items():
        if lower_count[base.lower()] != 1:
            continue
        if not strict:
            families[base.lower()] = sorted([shape_index[base]]
                                            + list(by_k.values()))
            continue
        ks = sorted(by_k)
        if ks != list(range(1, len(ks) + 1)):
            continue                     # a gap ('name:2' lost) or a 'name:0'
        members = [shape_index[base]] + [by_k[k] for k in ks]
        if any(a >= b for a, b in zip(members, members[1:])):
            continue                     # not 'name', 'name:1', ... in NIF order
        families[base.lower()] = members
    return families


def _alttex_exact_provenance_on() -> bool:
    r"""#alttex-exact-provenance (2026-09-25): does #alttex-dup-occurrence bind
    each entry through the SOURCE mesh the converted NIF was made from, instead
    of guessing from the converted NIF's layout? Yes, by default.

    The layout guess (`_renamed_shape_families`, `_alttex_family_strict_on`)
    still put a colour on the WRONG shell in one class: a lost TRAILING shell
    together with a set that names only some shells, or with an authored
    'name:k' the set does not name, passes the layout and count checks, and
    binding by rank then moves a colour onto a neighbour.

    The rename is a pure function of the source file, so the reconcile re-reads
    the source (`_alttex_source_paths`, the convert step's own resolution,
    read-only) and replays the converter's own rename on it
    (`_read_alttex_source`) -- the exact map source 3D index -> shipped name.
    An entry of a renamed name then binds: its source 3D index -> that shell's
    shipped name -> the index of that name in the converted NIF. A shell absent
    from the converted NIF (a failed copy) drops its entry. Every other entry
    keeps the match by name. When the source cannot be found or read, or it is
    not the mesh that was converted (`_alttex_binding`), the entries of every
    name that may have been split are DROPPED for that NIF: a colour missed,
    not guessed (the two cases that can still bind another same-named
    shell's colour are listed in docs/DESIGN.md, #alttex-exact-provenance).

    CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE=1 binds by the converted NIF's layout,
    as #alttex-family-strict did (CBBE2UBE_NO_ALTTEX_FAMILY_STRICT then acts
    on that layout path only)."""
    return not _flag("CBBE2UBE_NO_ALTTEX_EXACT_PROVENANCE", False)


def _alttex_set_provenance_on() -> bool:
    r"""#alttex-set-provenance (2026-09-25): does #alttex-exact-provenance also
    read the source of a converted NIF that a set names one shape name in more
    than once, and bind a same-named group the rename left as authored through
    that source? Yes, by default. Read only with #alttex-exact-provenance on.

    Two holes it left, each able to put a colour on the WRONG shell:
    - The source was read only when the converted NIF showed 'name:k' beside
      'name'. When every renamed shell of a family was lost (a two-shell 'fur'
      that lost 'fur:1') the NIF looks unrenamed, and one entry per name put
      the set's FIRST-LISTED entry -- possibly the lost shell's colour -- on
      the surviving shell. A set that names one name (case-insensitively) more
      than once proves the source had same-named shells, so that NIF's source
      is read too. With no source that matches, the entries of every such
      name, and of every name the converted NIF carries twice, are dropped.
    - A group the rename left as authored (named in the physics XML, or a body
      name) keeps its literal duplicate names, and one entry per name put the
      first-listed entry on the LAST shape of the name. Now each entry of such
      a name binds source 3D index -> the converted shape of that name whose
      `_shape_print` is that source shell's (`_kept_group_shells`), when every
      converted shape of the name matches exactly one of its source shells and
      no two match the same one; otherwise that name's entries are dropped.
    Also: a key the batch's VFS index lacks is looked up over the enabled
    mods' loose files before the archives (`_alttex_source_paths`), which is
    where the convert step's source-local tier finds a mod's own mesh.

    CBBE2UBE_NO_ALTTEX_SET_PROVENANCE=1 reads a source only for a NIF showing
    'name:k' beside 'name' and keeps one entry per name for a group left as
    authored, as #alttex-exact-provenance first did."""
    return not _flag("CBBE2UBE_NO_ALTTEX_SET_PROVENANCE", False)


def _alttex_case_provenance_on() -> bool:
    r"""#alttex-case-provenance (2026-09-25): does #alttex-set-provenance treat
    shape names case-insensitively all the way through, as the reconcile's own
    match by name does? Yes, by default. Read only with #alttex-set-provenance
    on.

    The rename works on exact names, so an author's 'Fur' beside 'fur' is
    never renamed, and #alttex-set-provenance grouped the source's shells by
    exact name too. A set naming 'fur' and 'Fur' read the source, found no
    split or kept group, and fell back to one entry per name: the set's
    first-listed entry landed on the FIRST case variant, possibly the other
    shell's colour -- worse than with no source, which drops the name. Now:
    - a converted NIF carrying one name under two spellings
      (`_case_variant_names`) has its source read, like a set's repeat;
    - `_kept_group_shells` groups the source's shells case-insensitively and
      also takes every name the reconcile knows was shared (`ambiguous`: a
      set repeats it, or the converted NIF carries it twice), so each such
      entry binds source 3D index -> the converted shape with that source
      shell's print, or is dropped -- never bound by name;
    - a shape named once like a renamed shell in another case ('Fur' beside
      'fur', 'fur:1') must carry its source shell's print, as the renamed
      shells must (`_alttex_binding`).

    CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE=1 groups by exact name, reads no
    source for a case variant alone and binds such a 'Fur' by name, as
    #alttex-set-provenance first did."""
    return not _flag("CBBE2UBE_NO_ALTTEX_CASE_PROVENANCE", False)


def _alttex_batch_ambiguity_on() -> bool:
    r"""#alttex-batch-ambiguity (2026-09-25): are the reconcile's decisions
    about a converted NIF -- is a name shared, is its source read, how does
    it bind -- taken once for the whole merged plugin, every ESL-split piece
    together, and does a NIF carrying a literal duplicate name have its
    source read? Yes, by default. Read only with #alttex-case-provenance on.

    Two holes it left, each able to put a colour on the WRONG shell:
    - `reconcile_alt_texture_indices_all` reconciled each piece
      (`<stem>.esp`, `<stem>2.esp`, ...) on its own. A set in one piece that
      repeats a name proves that NIF's name was shared, but a set in ANOTHER
      piece naming it once read no source and bound by name: a lost shell's
      colour could land on the surviving shell. Now every piece's sets are
      scanned first, and one view -- the repeats, the converted NIFs, the
      shared names, the source bindings -- serves every piece
      (`_reconcile_alt_texture_pieces`); each source is read once.
    - A converted NIF carrying one name twice (a group the rename left as
      authored) read its source only when some set repeated the name or
      another name looked renamed; a set naming it once put its entry on the
      LAST shape of the name. Now such a NIF has its source read
      (`_literal_duplicate_names`), and the name binds by print or is
      dropped, as a repeat's does.
    - The EMPTY name was never taken as shared: an entry with no name on a
      NIF carrying two or more unnamed shapes went to the last of them, even
      with the source read and matched. Now it is a name like any other:
      two unnamed shapes in the NIF, or two entries with no name in a set,
      read the source, and each such entry binds by print
      (`_kept_group_shells`) or is dropped.

    CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY=1 reconciles each piece on its own,
    reads no source for a literal duplicate alone and binds an entry with no
    name by name, as #alttex-case-provenance first did."""
    return not _flag("CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY", False)


def _reconcile_loaded_mesh_on() -> bool:
    r"""#reconcile-loaded-mesh (2026-09-26): when our output has no NIF at one
    of our `!UBE\` model paths, does the reconcile index that colour set
    against the copy of the path the GAME loads? Yes, by default.

    The reconcile looked each model up only in our output's meshes folder.
    Since #skip-built-ube-path and #supersede-whole-base, a base the user's
    own BodySlide build ships is left to that build (our old copy moves to
    `_superseded`), so the reconcile found no NIF and kept the source
    plugin's CBBE-era indices: a colour landed on the wrong shape of the
    build the game loads. Measured on the reported modlist's full run: 11
    sets on such paths, 3 of them wrong (one torso's skirt colour went to the
    build's collision body), 8 already right.

    The copy the game loads is found as the coverage step finds it
    (`auto_convert._mesh_exists_anywhere(...).loaded_copy`: the first loose
    file by MO2 priority, overwrite first, our output left out, then the
    archive by plugin load order), read-only. It is another mod's mesh, not
    our conversion, so the source-provenance binding is not used for it: its
    entries bind by name, and the entries of a name it carries twice (or in
    two spellings), or that a set repeats, are dropped. A path found nowhere
    keeps its entries as authored, as before, and is counted. Our output's
    own NIF, when present, is used exactly as before -- unless another mod's
    loose copy outranks it (#reconcile-loaded-winner).

    CBBE2UBE_NO_RECONCILE_LOADED_MESH=1 leaves a set whose NIF is not in our
    output untouched, as before."""
    return not _flag("CBBE2UBE_NO_RECONCILE_LOADED_MESH", False)


def _reconcile_loaded_winner_on() -> bool:
    r"""#reconcile-loaded-winner (2026-09-26): when our output DOES have a NIF
    at one of our `!UBE\` paths but another mod's loose copy outranks it in
    MO2, is the colour set indexed against that copy -- the one the game
    loads? Yes, by default (read only with #reconcile-loaded-mesh on).

    #reconcile-loaded-mesh asked for the game's copy only when our NIF was
    missing: its premise was that #skip-built-ube-path always moves our copy
    out of `meshes\` first. It does not always: a base the global schedule
    holds, a base a builder ships only part of, a supersede move that fails
    (a file in use), CBBE2UBE_NO_SKIP_BUILT_UBE_PATH=1, or an earlier run's
    copy no source plans. And a user's UBE BodySlide output often sits ABOVE
    our output in MO2 (it does on the reported modlist). Then the set was
    indexed against our copy while the game draws the build's, which can
    order its shapes differently. Now such a path is indexed against the copy
    the game loads, as a missing one is (`loaded_copy.outranks_output`: a
    loose copy in the overwrite or a mod above our output; an archive never
    beats our loose file). Our output not an enabled mod: unchanged.
    CBBE2UBE_NO_RECONCILE_LOADED_WINNER=1 uses our own NIF whenever it exists,
    as before."""
    return not _flag("CBBE2UBE_NO_RECONCILE_LOADED_WINNER", False)


def _alttex_source_winner_on() -> bool:
    r"""#alttex-source-winner (2026-09-26): does the reconcile check an
    archive-only source against the archive the convert step extracted it
    from? Yes, by default.

    `_alttex_source_paths` takes the copy the convert step staged in
    `<output>\_bsa_staging` only while its bytes are the archive's. The convert
    step's index picks, between two archives holding the mesh, the one whose
    plugin loads later (#bsa-load-order-winner); the reconcile built its own
    index without the plugin order, so it read the MO2-first archive. Where the
    two orders disagree the bytes never matched, the source counted as
    unreadable and every entry of a same-named layer was dropped (the layer
    kept its base colour). Its index now takes the same plugin order
    (`auto_convert._bsa_plugin_order`, None under
    CBBE2UBE_NO_BSA_LOAD_ORDER_WINNER=1, as the convert step's).
    CBBE2UBE_NO_ALTTEX_SOURCE_WINNER=1 reads the MO2-first archive again."""
    return not _flag("CBBE2UBE_NO_ALTTEX_SOURCE_WINNER", False)


def _loaded_mesh_lookup(meshes_root):
    r"""#reconcile-loaded-mesh: model path -> (path, None) | (None, bytes) |
    None, the copy of a path the game loads with our output (the folder
    holding `meshes_root`) left out; None when the modlist cannot be read."""
    from . import auto_convert as _ac
    try:
        look = _ac._mesh_exists_anywhere(Path(meshes_root).parent)
    except Exception:
        return None
    return getattr(look, "loaded_copy", None) if look is not None else None


def _loaded_mesh_names(hit, stage_dir) -> "list[str]":
    r"""#reconcile-loaded-mesh: the shape names, in 3D-index order, of a
    `_loaded_mesh_lookup` hit. An archived copy is written to a temporary file
    in `stage_dir` (our output's `_bsa_staging`), read, and deleted. Raises
    when it cannot be read."""
    import os
    import tempfile
    from . import nif_io
    path, data = hit
    tmp = None
    made = None                          # a staging folder this call created
    if path is None:
        sd = Path(stage_dir)
        if not sd.exists():
            made = sd
        sd.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(stage_dir), prefix=".reconcile-",
                                   suffix=".nif")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        path = tmp
    try:
        nf = nif_io.load_nif(path)
        try:
            return [s.name for s in nf.shapes]
        finally:
            nif_io.release_nif(nf._backing)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if made is not None:             # leave the output as it was
            try:
                made.rmdir()
            except OSError:
                pass


def _literal_duplicate_names(names) -> "frozenset[str]":
    """#alttex-batch-ambiguity: the shape names a NIF carries more than once
    under one exact spelling (a group the rename left as authored) -- the
    EMPTY name too, for two or more unnamed shapes, which the rename never
    renames. The reconcile's name map keeps the last such shape, so an entry
    of such a name cannot be bound by its name."""
    count = Counter(str(n or "") for n in names)
    return frozenset(n for n, c in count.items() if c > 1)


def _case_variant_names(names) -> "frozenset[str]":
    """#alttex-case-provenance: the lowercased shape names a NIF carries under
    more than one spelling ('Fur' and 'fur'). The reconcile matches names
    case-insensitively and keeps the first spelling, so an entry of such a
    name cannot be bound by its name."""
    spellings: "dict[str, set[str]]" = {}
    for n in names:
        if n:
            spellings.setdefault(str(n).lower(), set()).add(str(n))
    return frozenset(low for low, s in spellings.items() if len(s) > 1)


def _alttex_entries(data: bytes) -> list:
    """[(name bytes, TXST FormID, 3D index)] of a raw MO?S payload. Raises on
    one that does not parse."""
    n = struct.unpack_from("<I", data, 0)[0]
    p = 4
    entries = []
    for _ in range(n):
        nl = struct.unpack_from("<I", data, p)[0]; p += 4
        name = data[p:p + nl]; p += nl
        txst = struct.unpack_from("<I", data, p)[0]; p += 4
        src_idx = struct.unpack_from("<I", data, p)[0]; p += 4
        entries.append((name, txst, src_idx))
    return entries


def _entry_lname(name: bytes) -> str:
    """An MO?S entry's shape name as the reconcile matches it: to the first
    NUL, lowercased."""
    return name.split(b"\x00", 1)[0].decode("latin-1", "ignore").lower()


def _repeated_entry_names(data: bytes, unnamed: bool = False
                          ) -> "frozenset[str]":
    """#alttex-set-provenance: the lowercased shape names an MO?S set gives
    more than one entry -- proof its source had same-named shells. Empty for a
    set that does not parse. The empty name counts only with `unnamed`
    (#alttex-batch-ambiguity: two entries with no name prove two unnamed
    shells, as for any other name)."""
    try:
        entries = _alttex_entries(data)
    except Exception:
        return frozenset()
    count = Counter(_entry_lname(nm) for nm, _t, _s in entries)
    if unnamed and count[""] > 1:
        return frozenset({""}) | _repeated_entry_names(data)
    return frozenset(nm for nm, c in count.items() if nm and c > 1)


def _split_name_candidates(names) -> "set[str]":
    """#alttex-exact-provenance: the lowercased shape names of a NIF that the
    #dup-shape-names rename may have made or kept: 'b' and 'b:k' (k a plain
    integer from 1) wherever both are present, case-insensitively. Empty when
    no shape looks renamed -- the reconcile then never reads a source."""
    from .nif_convert_writer import _DUP_NAME_SEP
    low = {str(n).lower() for n in names}
    out: "set[str]" = set()
    for z in low:
        base, sep, k = z.rpartition(_DUP_NAME_SEP)
        if (sep and base and base in low and k.isascii() and k.isdigit()
                and str(int(k)) == k and int(k) >= 1):
            out.update((base, z))
    return out


def _shape_print(shape) -> tuple:
    """#alttex-exact-provenance: what a converted shell keeps of its source
    shell -- vertex count, triangle count and UVs (compared by
    `_same_shell_print`)."""
    import numpy as np
    return (len(shape.verts), len(shape.tris),
            np.asarray(shape.uvs, dtype=np.float32))


def _same_shell_print(a: tuple, b: tuple) -> bool:
    """#alttex-exact-provenance: are two `_shape_print`s one shell's? Counts
    equal and every UV within 2^-10 (relative above 1): the written UVs may be
    half floats. MEASURED on the reported modlist: all 446 non-body converted
    shapes named like their source's keep both counts and the exact UV bytes;
    a six-shell source read from an archive and converted in scratch kept the
    counts and moved UVs by up to 2.4e-4 (half-float rounding)."""
    import numpy as np
    if a[0] != b[0] or a[1] != b[1] or a[2].shape != b[2].shape:
        return False
    return bool(np.allclose(a[2], b[2], rtol=2.0 ** -10, atol=2.0 ** -10))


class _AltTexSource(NamedTuple):
    """#alttex-exact-provenance: a source mesh as the converter named it."""
    names: tuple      # the shape names as authored, in 3D-index order
    renamed: tuple    # the name #dup-shape-names gave each shape
    prints: tuple     # `_shape_print` of each shape


class _AltTexBinding(NamedTuple):
    """#alttex-exact-provenance: a source matched to its converted NIF."""
    names: tuple            # source names as authored, in 3D-index order
    renamed: tuple          # the shipped name of each source shape
    split: frozenset        # lowercased names of the renamed shells, old and new
    index: dict             # shipped name -> converted index, for the renamed
    #                         shells and any shape named once like one of them
    kept: "dict | None" = None   # #alttex-set-provenance: lowercased name of a
    #                         group left as authored -> {source 3D index:
    #                         converted index} ({}: its entries are dropped)


def _read_alttex_source(src_path) -> "_AltTexSource | None":
    """#alttex-exact-provenance: read a source mesh and replay the converter's
    own rename on it (`_uniquify_source_shape_names`: the same keep rules, the
    same physics-XML reader). None when it cannot be read."""
    from . import nif_io
    from .nif_convert_writer import _uniquify_source_shape_names
    try:
        nf = nif_io.load_nif(src_path)
    except Exception:
        return None
    try:
        names = tuple(s.name for s in nf.shapes)
        prints = tuple(_shape_print(s) for s in nf.shapes)
        _uniquify_source_shape_names(nf, src_path)
        return _AltTexSource(names, tuple(s.name for s in nf.shapes), prints)
    except Exception:
        return None
    finally:
        nif_io.release_nif(nf._backing)


def _alttex_binding(source: "_AltTexSource | None",
                    converted: "list[tuple[str, tuple]]",
                    ambiguous: "frozenset[str]" = frozenset()
                    ) -> "_AltTexBinding | None":
    """#alttex-exact-provenance: match a source to the NIF converted from it.
    `converted` = [(name, `_shape_print`)] in the converted NIF's order;
    `ambiguous` = the lowercased names the reconcile knows were shared
    (#alttex-set-provenance), bound through `_kept_group_shells`.

    None -- the caller drops every entry of a name that may be split -- when
    there is no source, or it is not the mesh that was converted: a converted
    name that looks renamed (or is a renamed shell's) which this source's
    rename does not give, or a renamed shell whose name the converted NIF
    carries twice or whose vertex count, triangle count or UVs differ from its
    source shell's (#alttex-case-provenance: so must a shape named once like
    one in another case)."""
    if source is None or len(source.renamed) != len(source.names):
        return None
    case = _alttex_set_provenance_on() and _alttex_case_provenance_on()
    groups = Counter(source.names)
    split_groups = {g for g, c in groups.items() if c > 1 and any(
        r != n for n, r in zip(source.names, source.renamed) if n == g)}
    shell_of = {source.renamed[i]: i for i, n in enumerate(source.names)
                if n in split_groups}
    split = frozenset(nm.lower() for i in shell_of.values()
                      for nm in (source.names[i], source.renamed[i]))
    known = set(source.renamed)
    candidates = _split_name_candidates(nm for nm, _p in converted)
    count = Counter(nm for nm, _p in converted)
    index: "dict[str, int]" = {}
    for ci, (nm, pr) in enumerate(converted):
        if (nm.lower() in candidates or nm.lower() in split) and nm not in known:
            return None                  # a name this source's rename cannot give
        i = shell_of.get(nm)
        if i is None:
            if nm.lower() in split and count[nm] == 1:
                own = [j for j, r in enumerate(source.renamed) if r == nm]
                if case and (len(own) != 1 or not _same_shell_print(
                        pr, source.prints[own[0]])):
                    return None          # not this source shell's geometry
                index[nm] = ci           # e.g. the author's own 'Fur' beside 'fur'
            continue
        if count[nm] != 1 or not _same_shell_print(pr, source.prints[i]):
            return None                  # not this source shell's geometry
        index[nm] = ci
    kept = (_kept_group_shells(source, converted, split, ambiguous)
            if _alttex_set_provenance_on() else {})
    return _AltTexBinding(source.names, source.renamed, split, index, kept)


def _kept_group_shells(source: "_AltTexSource",
                       converted: "list[tuple[str, tuple]]",
                       split: frozenset,
                       ambiguous: "frozenset[str]" = frozenset()
                       ) -> "dict[str, dict[int, int]]":
    """#alttex-set-provenance: {lowercased name: {source 3D index: converted
    index}} for each same-named group the rename left as authored (named in
    the physics XML, or a body name), whose shells the converted NIF still
    carries under one name. Each converted shape of the name is matched to
    the source shape of that exact name with its `_shape_print`. A name maps
    to {} -- all its entries dropped -- when a converted shape of it matches
    no source shape or more than one, or two match the same one (e.g. two
    shells identical in counts and UVs). A source shape nothing matches was
    lost: its entries find no shell.

    #alttex-case-provenance: the groups are case-insensitive (the author's
    'Fur' beside 'fur', which the rename never touches), and every name of
    `ambiguous` not renamed is bound the same way, even one the source has
    once: an entry of a name the reconcile knows was shared is never bound
    by its name.

    #alttex-batch-ambiguity: the EMPTY name is a name like any other here --
    two or more unnamed source shells are a group the rename left as
    authored -- so each entry with no name binds by print or is dropped,
    never to the last unnamed shape by name."""
    out: "dict[str, dict[int, int]]" = {}
    fold = _alttex_case_provenance_on()
    unnamed = fold and _alttex_batch_ambiguity_on()
    groups = Counter((n.lower() if fold else n) for n in source.names
                     if n or unnamed)
    shared = [g for g, c in groups.items() if c > 1]
    if fold:
        shared += sorted(nm for nm in ambiguous
                         if (nm or unnamed) and groups[nm] < 2)
    for g in shared:
        low = g.lower()
        if low in split or low in out:
            continue                     # renamed (bound by name) / done
        members = [i for i, n in enumerate(source.names) if n.lower() == low]
        shells: "dict[int, int]" = {}
        for ci, (nm, pr) in enumerate(converted):
            if nm.lower() != low:
                continue
            hits = [i for i in members if source.renamed[i] == nm
                    and _same_shell_print(pr, source.prints[i])]
            if len(hits) != 1 or hits[0] in shells:
                shells = {}
                break                    # which shell this is is not known
            shells[hits[0]] = ci
        out[low] = shells
    return out


def _alttex_source_rel(model_path: str) -> "str | None":
    r"""The source's meshes-relative key (lowercase, '/') of one of our
    converted models: the converter writes '!UBE\' + the source's path. None
    for a path that is not ours."""
    m = str(model_path or "").replace("/", "\\").lstrip("\\")
    if not m.lower().startswith("!ube\\"):
        return None
    return m[5:].replace("\\", "/").lower() or None


def _alttex_source_paths(meshes_root, keys) -> "dict[str, Path]":
    r"""#alttex-exact-provenance: the SOURCE file of each converted NIF (`keys`
    as `_alttex_source_rel` gives them), found as the convert step found it,
    read-only: the full-VFS winner (the batch's own index when this process
    built one, else `discovery.build_mesh_index` over the enabled mods, the
    output mod skipped; a key the batch's index lacks is looked up the same
    way, `_alttex_set_provenance_on`), then the load-order archives -- the copy
    the convert step extracted to `<output>\_bsa_staging`, taken only while
    its bytes are still the archive's. A key found nowhere is left out. Not
    searched: a source folder outside the enabled mods (the convert step's
    source-local tier can read one) -- that NIF falls back. Never writes."""
    from . import auto_convert as _ac, discovery, paths
    out: "dict[str, Path]" = {}
    keys = sorted(set(keys))
    try:
        lay = paths.discover_layout()
        mr = paths.mods_root()
        order = paths.enabled_mods_ordered(lay)
    except Exception:
        return out
    if not keys or mr is None or not order:
        return out
    output = Path(meshes_root).parent
    vfs = _ac._BATCH_MESH_INDEX.get(str(Path(mr)).lower())
    if vfs is None:
        # No batch index (a standalone `convert`, or selection's index failed):
        # build the one the convert step builds for itself, MO2's overwrite
        # included (#overwrite-mesh-index), or the two can pick different
        # copies of one mesh. #alttex-overwrite
        try:
            vfs = discovery.build_mesh_index(Path(mr), order,
                                             target_keys=set(keys),
                                             skip_mods={output.name},
                                             overwrite=_ac._modlist_overwrite(Path(mr)))
        except Exception:
            vfs = {}
    elif _alttex_set_provenance_on():
        # #alttex-set-provenance: the batch index holds the keys the plugins
        # named; one it lacks may still be loose in an enabled mod -- where
        # the convert step's source-local tier found the mod's own mesh.
        lacking = {k for k in keys if vfs.get(k) is None}
        if lacking:
            try:
                vfs = {**vfs, **discovery.build_mesh_index(
                    Path(mr), order, target_keys=lacking,
                    skip_mods={output.name})}
            except Exception:
                pass
    rest = []
    for k in keys:
        if vfs.get(k) is not None:
            out[k] = Path(vfs[k])
        else:
            rest.append(k)
    if rest:
        try:
            bsa = _ac._BsaMeshIndex(
                _ac._load_order_bsa_dirs(mr, order, lay.game_data_dirs), None,
                plugin_order=(_ac._bsa_plugin_order(lay)
                              if _alttex_source_winner_on() else None))
        except Exception:
            return out
        for k in rest:
            staged = output / "_bsa_staging" / "meshes" / k
            try:
                data = bsa.read_bytes(k)
                if data and staged.is_file() and staged.read_bytes() == data:
                    out[k] = staged
            except OSError:
                continue
    return out


def _bind_by_source(entries, lnames: "list[str]",
                    shape_index: "dict[str, int]",
                    binding: "_AltTexBinding | None",
                    ambiguous: "frozenset[str]" = frozenset()):
    """#alttex-exact-provenance: (lowercased names bound here, {entry position:
    converted index}). With no binding, every name that may be split -- and
    each of `ambiguous`, the names #alttex-set-provenance knows were shared --
    is bound to nothing: its entries are dropped. With one, an entry of a
    renamed name goes to the shell its source 3D index names, and an entry of
    a group left as authored to the shell its print found (`binding.kept`;
    #alttex-case-provenance: so does one of a case-variant group or of any
    `ambiguous` name, which the binding was built with); an index that is not
    a shell of the entry's own name, a shell the converted NIF lacks, and a
    second entry for one shell are dropped."""
    if binding is None:
        return _split_name_candidates(shape_index) | set(ambiguous), {}
    kept = binding.kept or {}
    by_pos: "dict[int, int]" = {}
    taken: "set[str]" = set()
    kept_taken: "set[int]" = set()
    for pos, (_name, _txst, si) in enumerate(entries):
        nm = lnames[pos]
        if nm in kept:
            ci = kept[nm].get(si)        # None: not a shell of it / not found
            if ci is not None and ci not in kept_taken:
                kept_taken.add(ci)
                by_pos[pos] = ci
            continue
        if nm not in binding.split:
            continue
        if not 0 <= si < len(binding.names) or binding.names[si].lower() != nm:
            continue                     # the index is not a shell of this name
        shipped = binding.renamed[si]
        ci = binding.index.get(shipped)
        if ci is None or shipped in taken:
            continue                     # its shell was not converted / a repeat
        taken.add(shipped)
        by_pos[pos] = ci
    return set(binding.split) | set(kept), by_pos


def _reindex_alt_texture_payload(data: bytes,
                                 shape_index: "dict[str, int]",
                                 source: "_AltTexBinding | None" = None,
                                 ambiguous: "frozenset[str]" = frozenset(),
                                 loaded: bool = False
                                 ) -> "bytes | None":
    """Rewrite an MO?S alt-texture set to match a CONVERTED NIF's shapes.

    Each entry is (3D name, TXST FormID, 3D index). After conversion merges
    or reorders shapes, source indices are stale: variants recolor the wrong
    shape (or hit an out-of-range index = no effect). For each entry:
      * name still in NIF -> keep, update index to its real position;
      * name merged away -> DROP (its geometry lives in a surviving shape that
        carries the same TXST for that variant);
      * de-dupe by name (one entry per surviving shape);
      * except a name #dup-shape-names split into 'name', 'name:1', ...: its
        entries bind by occurrence (`_alttex_dup_occurrence_on`), through
        `source` (`_alttex_exact_provenance_on`; None = no source at hand, so
        those entries are dropped, and so are those of `ambiguous`, the names
        the reconcile knows were shared: `_alttex_set_provenance_on`).
    `shape_index` = {shape_name: index} from the converted NIF.
    `loaded` (#reconcile-loaded-mesh): `shape_index` is ANOTHER mod's copy of
    the path, the one the game loads -- not our conversion, so no name of it
    is our rename: every entry binds by name, except those of `ambiguous`
    (here: the names that mesh carries twice or a set repeats), which are
    dropped; `source` is not used.
    Returns rebuilt payload, or None on parse failure (caller keeps original)."""
    try:
        entries = _alttex_entries(data)
    except Exception:
        return None
    lnames = [_entry_lname(name) for name, _t, _s in entries]
    # #alttex-dup-occurrence: entry position -> its shell's index in the NIF.
    by_occurrence: "dict[int, int]" = {}
    bound: "set[str]" = set()                   # names bound by occurrence
    exact = _alttex_dup_occurrence_on() and _alttex_exact_provenance_on()
    if loaded:
        bound = set(ambiguous)                  # which shape is not known: drop
    elif exact:
        bound, by_occurrence = _bind_by_source(entries, lnames, shape_index,
                                               source, ambiguous)
    # With the source switched off: bind by the converted NIF's layout.
    families = (_renamed_shape_families(shape_index, set(lnames))
                if _alttex_dup_occurrence_on() and not exact and not loaded
                else {})
    strict = bool(families) and _alttex_family_strict_on()
    for fam, members in families.items():
        first: "dict[int, int]" = {}   # source 3D index -> its first entry
        for pos in sorted((q for q, nm in enumerate(lnames) if nm == fam),
                          key=lambda q: entries[q][2]):
            first.setdefault(entries[pos][2], pos)   # a repeat: keep the first
        if strict and len(first) != len(members):
            continue       # #alttex-family-strict: not every shell once -> by name
        bound.add(fam)
        for rank, pos in enumerate(first.values()):  # rank k -> 'name:k'
            if rank < len(members):
                by_occurrence[pos] = members[rank]
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
    for pos, (name, txst, _src) in enumerate(entries):
        nm = lnames[pos]
        if nm in bound:
            if pos in by_occurrence:
                kept.append((name, txst, by_occurrence[pos]))
            continue                     # past the last shell / a repeat
        new_idx = ci_index.get(nm)
        if new_idx is None or nm in seen:
            continue  # shape merged away / duplicate
        seen.add(nm)
        kept.append((name, txst, new_idx))
    out = struct.pack("<I", len(kept))
    for name, txst, idx in kept:
        out += struct.pack("<I", len(name)) + name + struct.pack("<II", txst, idx)
    return out


def reconcile_alt_texture_indices(esp_path, meshes_root, problems=None) -> int:
    """Post-conversion pass: fix stale alt-texture (MO2S/MO3S/MO4S/MO5S) 3D
    names+indices in an output ESP so color variants apply to the right shapes.

    The converter merges/reorders NIF shapes but the ESP's alt-texture sets
    still carry the source NIF's names and indices. Stale indices recolor the
    wrong shapes or fall out of range (no effect). This reloads each ARMA's
    converted NIF and rewrites the alt-texture set to the surviving shapes'
    real names+indices. Returns number of ARMA records fixed.
    Run AFTER NIF conversion + merge, once, on a freshly merged plugin: an
    entry's index is read as the SOURCE mesh's 3D index.

    `problems`: see `_reconcile_alt_texture_pieces`."""
    return _reconcile_alt_texture_pieces([esp_path], meshes_root,
                                         problems=problems)


#: The reconcile's problem classes, as a `problems` list names them. #one-tally
ALTTEX_LOAD_FAILED = "converted NIF unreadable"
ALTTEX_ENTRIES_DROPPED = "entries dropped"
ALTTEX_GAME_COPY_UNREADABLE = "game copy unreadable"
#: Our output ships the NIF, another mod's copy outranks it, and that copy
#: could not be read: indexed against ours. #reconcile-loaded-winner
ALTTEX_OUTRANKING_COPY_UNREADABLE = "outranking copy unreadable"


def _reconcile_alt_texture_pieces(esp_paths, meshes_root, problems=None) -> int:
    """`reconcile_alt_texture_indices` over one or more plugins taken as ONE
    view (#alttex-batch-ambiguity): every plugin's sets are scanned before
    any NIF is loaded, and the repeats, the converted NIFs, the shared names
    and the source bindings are shared by all of them. Each plugin that
    changed is saved. A plugin that does not load raises -- after the others
    are reconciled and saved. Returns the ARMA records fixed, summed.

    `problems` (#one-tally): None prints each problem class as its own `!!`
    line, as a standalone call always has. A list takes them instead, as
    `(class, models)` pairs (an `ALTTEX_*` class, its sorted model paths),
    added before anything is saved or raised; the converter warns and records
    each, so the run's tally and failures file carry them."""
    from pathlib import Path as _Path
    from . import nif_io
    meshes_root = _Path(meshes_root)
    loaded = []                          # (path, ESP)
    load_error = None
    for piece in esp_paths:
        try:
            loaded.append((piece, esp.ESP.load(piece)))
        except Exception as ex:          # reconcile the rest, then raise
            if len(esp_paths) == 1:
                raise
            load_error = load_error or ex
    _cache: "dict[str, dict | None]" = {}
    # Converted NIFs that EXIST but won't load: their alt-texture set keeps the
    # stale source indices (color variants misalign). Distinct from a legitimately
    # absent path (vanilla mesh the converter doesn't own) -- surface only these.
    load_failed: "list[str]" = []
    # #alttex-exact-provenance: model key -> [(name, print)] of a converted NIF
    # whose shapes look renamed (or, below, that a set repeats a name for);
    # only those NIFs' sources are read.
    exact = _alttex_dup_occurrence_on() and _alttex_exact_provenance_on()
    _prints: "dict[str, list]" = {}
    # #alttex-set-provenance: model key -> the names a set of it repeats; such a
    # NIF's source is read too.
    set_prov = exact and _alttex_set_provenance_on()
    _repeats: "dict[str, set[str]]" = {}
    # #alttex-case-provenance: a NIF carrying one name under two spellings
    # ('Fur', 'fur') has its source read too.
    case_prov = set_prov and _alttex_case_provenance_on()
    # #alttex-batch-ambiguity: so does a NIF carrying one name twice, the
    # empty name included (two unnamed shapes), and a set naming two entries
    # with no name.
    batch = case_prov and _alttex_batch_ambiguity_on()
    # #reconcile-loaded-mesh: one of our '!UBE\' paths our output has no NIF
    # at is indexed against the copy the game loads (another mod's): model key
    # -> that copy's lowercased names carried twice (dropped, as a set's
    # repeats are). `nowhere`: such paths found nowhere, kept as authored.
    loaded_on = _reconcile_loaded_mesh_on()
    _game_copy: "dict[str, frozenset[str]]" = {}
    _set_repeats: "dict[str, set[str]]" = {}   # model key -> names a set repeats
    nowhere: "list[str]" = []
    unreadable: "list[str]" = []         # another mod's copy that would not load
    _look: list = []                     # the lookup, built on first need
    # #reconcile-loaded-winner: model keys our output ships but another mod's
    # loose copy outranks -- indexed against that copy, as a missing one is.
    # Asked only under `loaded_on` (`shapes_for`).
    winner_on = _reconcile_loaded_winner_on()
    shadowed: "set[str]" = set()
    # ... of which the outranking copy could not be found or read: indexed
    # against our own NIF, as with the rule off, and said so on its own.
    shadow_unread: "list[str]" = []

    def outranked(model_path: str) -> bool:
        if not winner_on:
            return False
        if not _look:
            _look.append(_loaded_mesh_lookup(meshes_root))
        test = getattr(_look[0], "outranks_output", None)
        return bool(test is not None and test(model_path))

    def loaded_shapes(model_path: str, key: str, ours: bool = False):
        if not _look:
            _look.append(_loaded_mesh_lookup(meshes_root))
        hit = _look[0](model_path) if _look[0] is not None else None
        if hit is None:
            (shadow_unread if ours else nowhere).append(model_path)
            return None
        try:
            names = _loaded_mesh_names(hit, meshes_root.parent / "_bsa_staging")
        except Exception:
            # Not our conversion: said on its own line below, never as one of
            # ours that failed to load. The set stays as authored -- or, when
            # our output ships the NIF, is indexed against ours.
            (shadow_unread if ours else unreadable).append(model_path)
            return None
        twice = Counter(str(n or "").lower() for n in names)
        _game_copy[key] = frozenset(n for n, c in twice.items() if c > 1)
        return {s: i for i, s in enumerate(names)}

    def shapes_for(model_path: str):
        key = model_path.lower()
        if key in _cache:
            return _cache[key]
        idx = None
        try:
            p = meshes_root / model_path.replace("/", "\\")
            ours = p.is_file()
            if (loaded_on and _alttex_source_rel(model_path) is not None
                    and (not ours or outranked(model_path))):
                if ours:
                    shadowed.add(key)
                idx = loaded_shapes(model_path, key, ours)
            if idx is None and ours:
                # Our own NIF: not outranked, or the copy that outranks it
                # could not be read (#reconcile-loaded-winner) -- then ours is
                # the best index there is, as with the rule off.
                nf = nif_io.load_nif(p)
                idx = {s.name: i for i, s in enumerate(nf.shapes)}
                if exact and (_split_name_candidates(idx) or key in _repeats
                              or (case_prov and _case_variant_names(idx))):
                    _prints[key] = [(s.name, _shape_print(s)) for s in nf.shapes]
                elif batch and _literal_duplicate_names(s.name for s in nf.shapes):
                    _prints[key] = [(s.name, _shape_print(s)) for s in nf.shapes]
        except Exception:
            idx = None
            load_failed.append(model_path)
        _cache[key] = idx
        return idx

    SLOT_FOR = {b"MO2S": b"MOD2", b"MO3S": b"MOD3",
                b"MO4S": b"MOD4", b"MO5S": b"MOD5"}
    sets = []                            # (record, subrecords, {MODn: model})
    owner: "list[int]" = []              # the plugin (index in `loaded`) of each
    groups = [(pi, g) for pi, (_p, e) in enumerate(loaded) for g in e.groups]
    for pi, g in groups:
        if g.label != b"ARMA":
            continue
        for r in g.records:
            subs = list(esp.iter_subrecords(r.payload))
            if not any(sig in SLOT_FOR for sig, _ in subs):
                continue
            models: dict[bytes, str] = {}
            for sig, data in subs:
                if sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5"):
                    models[sig] = _model_path_read(data)
            sets.append((r, subs, models))
            owner.append(pi)
            for sig, data in subs:
                if set_prov and sig in SLOT_FOR and models.get(SLOT_FOR[sig]):
                    rep = _repeated_entry_names(data, unnamed=batch)
                    if rep:
                        _repeats.setdefault(models[SLOT_FOR[sig]].lower(),
                                            set()).update(rep)
                if loaded_on and sig in SLOT_FOR and models.get(SLOT_FOR[sig]):
                    rep = _repeated_entry_names(data, unnamed=True)
                    if rep:
                        _set_repeats.setdefault(models[SLOT_FOR[sig]].lower(),
                                                set()).update(rep)
    for _r, subs, models in sets:
        for sig, _data in subs:
            if sig in SLOT_FOR and models.get(SLOT_FOR[sig]):
                shapes_for(models[SLOT_FOR[sig]])
    # #alttex-set-provenance: with no source that matches, the names a set
    # repeats and the names the converted NIF carries twice lose their entries
    # (#alttex-case-provenance: with one, they bind by print or are dropped).
    ambiguous: "dict[str, frozenset[str]]" = {}
    if set_prov:
        for k, conv in _prints.items():
            twice = Counter(nm.lower() for nm, _p in conv)
            ambiguous[k] = (frozenset(_repeats.get(k, ()))
                            | frozenset(n for n, c in twice.items() if c > 1))
    # #alttex-exact-provenance: each such NIF's source, read and matched once.
    bindings: "dict[str, _AltTexBinding | None]" = {}
    if _prints:
        rels = {k: _alttex_source_rel(k) for k in _prints}
        found = _alttex_source_paths(meshes_root,
                                     [v for v in rels.values() if v])
        for k, conv in _prints.items():
            src = found.get(rels[k]) if rels[k] else None
            bindings[k] = _alttex_binding(
                _read_alttex_source(src) if src is not None else None, conv,
                ambiguous.get(k, frozenset()))
    fixed = [0] * len(loaded)            # ARMA records fixed, per plugin
    for (r, subs, models), pi in zip(sets, owner):
        changed = False
        new_payload = b""
        for sig, data in subs:
            if sig in SLOT_FOR:
                mdl = models.get(SLOT_FOR[sig])
                idxmap = shapes_for(mdl) if mdl else None
                if idxmap is not None:
                    key = mdl.lower()
                    if key in _game_copy:
                        # #reconcile-loaded-mesh: another mod's copy, by name.
                        rebuilt = _reindex_alt_texture_payload(
                            data, idxmap, None,
                            _game_copy[key]
                            | frozenset(_set_repeats.get(key, ())),
                            loaded=True)
                    else:
                        rebuilt = _reindex_alt_texture_payload(
                            data, idxmap, bindings.get(key),
                            ambiguous.get(key, frozenset()))
                    if rebuilt is not None and rebuilt != data:
                        new_payload += esp.encode_subrecord(sig, rebuilt)
                        changed = True
                        continue
            new_payload += esp.encode_subrecord(sig, data)
        if changed:
            r.payload = new_payload
            fixed[pi] += 1
    import sys as _s
    _say = problems is None              # else the converter warns. #one-tally
    if load_failed:
        if _say:
            print(f"  !! alt-texture reconcile: {len(load_failed)} converted NIF(s) "
                  f"failed to load -> stale color-variant indices kept (variant "
                  f"textures may misalign): {sorted(set(load_failed))[:5]}",
                  file=_s.stderr)
        else:
            problems.append((ALTTEX_LOAD_FAILED, sorted(set(load_failed))))
    if bindings:
        unmatched = sorted(k for k, b in bindings.items() if b is None)
        print(f"  alt-texture reconcile: {len(bindings) - len(unmatched)} "
              f"converted NIF(s) with same-named layers bound through their "
              f"source mesh", file=_s.stderr)
        if unmatched and _say:
            print(f"  !! alt-texture reconcile: {len(unmatched)} converted "
                  f"NIF(s) with same-named layers whose source mesh could not be "
                  f"read or is not the mesh converted -> the colour-variant "
                  f"entries of those layers were dropped (they keep their "
                  f"base colour): {unmatched[:5]}", file=_s.stderr)
        elif unmatched:
            problems.append((ALTTEX_ENTRIES_DROPPED, unmatched))
    if _game_copy or nowhere or unreadable or shadow_unread:
        _over = len(shadowed & set(_game_copy))    # #reconcile-loaded-winner
        _unr = len(set(shadow_unread))
        print(f"  alt-texture reconcile: {len(_game_copy) - _over} model(s) not "
              f"in this output indexed against the copy the game loads (another "
              f"mod's); "
              + (f"{_over} model(s) this output ships but another mod's copy "
                 f"outranks in MO2, indexed against that copy; " if _over else "")
              + (f"{_unr} model(s) where another mod's copy outranks ours but "
                 f"could not be read, indexed against ours; " if _unr else "")
              + f"{len(set(nowhere))} found nowhere -> kept as authored"
              + (f": {sorted(set(nowhere))[:5]}" if nowhere else ""),
              file=_s.stderr)
    if unreadable and _say:
        print(f"  !! alt-texture reconcile: {len(set(unreadable))} model(s) not "
              f"in this output: the copy the game loads (another mod's) could not "
              f"be read -> its colour-variant indices kept as authored (variant "
              f"textures may misalign): {sorted(set(unreadable))[:5]}",
              file=_s.stderr)
    elif unreadable:
        problems.append((ALTTEX_GAME_COPY_UNREADABLE, sorted(set(unreadable))))
    if shadow_unread and _say:                   # #reconcile-loaded-winner
        print(f"  !! alt-texture reconcile: {len(set(shadow_unread))} model(s) "
              f"where another mod's copy outranks ours but could not be read -> "
              f"indexed against ours (the game draws that copy; variant "
              f"textures may misalign): {sorted(set(shadow_unread))[:5]}",
              file=_s.stderr)
    elif shadow_unread:
        problems.append((ALTTEX_OUTRANKING_COPY_UNREADABLE,
                         sorted(set(shadow_unread))))
    for (piece, e), n in zip(loaded, fixed):
        if n:
            e.save(piece)
    if load_error is not None:
        raise load_error
    return sum(fixed)


def _piece_family_match() -> bool:
    r"""#piece-family-match (2026-09-25): do the post-merge passes rewrite only
    the Combined and its numbered split pieces? Yes, by default.

    `reconcile_alt_texture_indices_all`, `dedup_armo_armature_refs_all`,
    `fix_spurious_hand_slot`, `resort_masters_all` and
    `postflight_validate_combined` globbed `<stem>*<suffix>`: a user's
    `<stem> - Copy.esp` or `<stem>_backup.esp` in the output folder was loaded,
    rewritten (ESP.save drops what the reader does not model) and reported
    "validated clean" on every run. `_drop_stale_pieces` had already narrowed the
    same family to `<stem><digits><suffix>` for its deletes; all six now share
    `_combined_piece_tail`. Live: the output folder holds the Combined and one
    piece, nothing else of that stem. CBBE2UBE_NO_PIECE_FAMILY_MATCH=1 makes the
    five passes glob the broad family again (the deletes stay narrow)."""
    return not _flag("CBBE2UBE_NO_PIECE_FAMILY_MATCH", False)


def _combined_piece_tail(name: str, stem: str, suffix: str) -> "str | None":
    """The part of `name` between the Combined's `stem` and `suffix`: "" for the
    Combined itself, the digits of a split piece (`<stem>2.esp` -> "2"), None
    for any other file that merely starts with the stem (`<stem> - Copy.esp`,
    `<stem>_backup.esp`). Case-blind, as the folder is. The ONE matcher of the
    merge's file family. #piece-family-match"""
    n, s, x = name.lower(), stem.lower(), suffix.lower()
    if not (n.startswith(s) and n.endswith(x)) or len(n) < len(s) + len(x):
        return None
    tail = n[len(s):len(n) - len(x)]
    return tail if tail == "" or tail.isdigit() else None


def _combined_piece_family(primary, suffix: "str | None" = None) -> "list[Path]":
    """The merge's own files beside `primary` (the Combined and its numbered
    split pieces), sorted -- the files the post-merge passes may rewrite.
    `suffix` defaults to `primary`'s. With CBBE2UBE_NO_PIECE_FAMILY_MATCH, every
    `<stem>*<suffix>` as before. #piece-family-match"""
    p = Path(primary)
    x = suffix if suffix is not None else p.suffix
    found = sorted(p.parent.glob(f"{p.stem}*{x}"))
    if not _piece_family_match():
        return found
    return [f for f in found if _combined_piece_tail(f.name, p.stem, x) is not None]


def reconcile_alt_texture_indices_all(primary_esp_path, meshes_root,
                                      problems=None) -> int:
    """Reconcile alt-texture indices across the primary merged ESP AND every
    ESL-split overflow piece (`<stem>.esp`, `<stem>2.esp`, ...).

    merge_patches_split may spill records into sibling pieces; those pieces
    carry alt-texture sets that also need reconciliation. Walks the merge's own
    file family (`_combined_piece_family`, #piece-family-match). Returns total
    records fixed.

    #alttex-batch-ambiguity: the pieces are ONE plugin split for the ESL cap,
    so they are reconciled as one view (`_reconcile_alt_texture_pieces`): a
    set in one piece that repeats a name makes that NIF's name shared in
    every piece. With CBBE2UBE_NO_ALTTEX_BATCH_AMBIGUITY=1 (or any earlier
    alt-texture switch set) each piece is reconciled on its own. Both paths
    walk the same family. `problems`: see `_reconcile_alt_texture_pieces`."""
    from pathlib import Path as _Path
    p = _Path(primary_esp_path)
    pieces = _combined_piece_family(p)
    if (_alttex_dup_occurrence_on() and _alttex_exact_provenance_on()
            and _alttex_set_provenance_on() and _alttex_case_provenance_on()
            and _alttex_batch_ambiguity_on()):
        return _reconcile_alt_texture_pieces(pieces, meshes_root,
                                             problems=problems)
    total = 0
    for piece in pieces:
        total += reconcile_alt_texture_indices(piece, meshes_root,
                                               problems=problems)
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
    """(rnam, mod2, mod3, mod4, mod5, slot_data, alttex, subrecord_count) for an
    ARMA payload -- the FULL render identity (race + every gendered 3rd/1st-person
    mesh + the biped-slot template + embedded alt-texture sets) plus a
    completeness tiebreak.

    Slot flags MUST be part of the key: two ARMAs that share meshes + race but
    cover DIFFERENT biped slots (e.g. one also claims the Amulet slot, or differs
    in ActsLike44) are NOT duplicates -- collapsing them drops real slot coverage
    and breaks the piece on equip. Likewise the 1st-person meshes (MOD4/MOD5) and
    the embedded alt-texture sets (MO2S/... -- colour variants) distinguish
    otherwise-identical addons. The group key is [:7]; [7] is the completeness
    tiebreak."""
    rnam = mod2 = mod3 = mod4 = mod5 = slot_data = None
    alttex: "dict[bytes, bytes]" = {}
    n = 0
    for sig, d in esp.iter_subrecords(arma_payload):
        n += 1
        if sig == b"RNAM":
            rnam = d
        elif sig == b"MOD2":
            mod2 = d.rstrip(b"\x00").lower()
        elif sig == b"MOD3":
            mod3 = d.rstrip(b"\x00").lower()
        elif sig == b"MOD4":
            mod4 = d.rstrip(b"\x00").lower()
        elif sig == b"MOD5":
            mod5 = d.rstrip(b"\x00").lower()
        elif sig in (b"BOD2", b"BODT"):
            slot_data = d               # full biped-slot + flags template bytes
        elif sig in (b"MO2S", b"MO3S", b"MO4S", b"MO5S"):
            alttex[sig] = d             # embedded alt-texture set -> colour variant
    return (rnam, mod2, mod3, mod4, mod5, slot_data,
            tuple(sorted(alttex.items())), n)


# Merge-time RECORD dedup: the patcher mints one UBE ARMA per SOURCE armature
# record, so many source armors that resolve to the SAME UBE mesh + slots + races
# produce byte-identical minted ARMAs (differing only in EDID) -- e.g. one UBE
# `DragonboneArmorF` mesh ended up with 8 ARMA records. At merge time we collapse
# each set of identical minted ARMAs to ONE record and repoint every source
# reference (incl. the SkyPatcher INI) at the keeper. Keyed on the FULL remapped
# payload (minus EDID) + record flags, so ARMAs differing in ANY rendered field
# (slots, meshes, alt-textures) are NEVER merged. Within one ESL piece only --
# split pieces don't master each other, so they can't share a record.
# Default ON; CBBE2UBE_NO_MERGE_DEDUP=1 disables.
MERGE_DEDUP_ARMAS = not _flag("CBBE2UBE_NO_MERGE_DEDUP", False)


def _coverage_female_guard() -> bool:
    r"""#coverage-female-guard (2026-09-23): may a coverage armature fill a female
    slot that names its OWN mesh with the converted MALE mesh? No, by default.

    `rebuild_arma_payload` redirects an unconverted MOD3/MOD5 to the converted
    MOD2/MOD4. In the coverage generators nothing logged it and nothing undid it
    (`restore_female_models` reads only the per-source sidecars, and runs before
    coverage). Reported in game: a follower wore converted male Ebony boots on
    her UBE body; her own female boots were never converted because her mod was
    excluded. Measured over the live pack: 9 world + 4 first-person female slots
    took a male mesh, on 17 armours -- a ranger cuirass, a toolbelt given a whole
    male studded body.

    With the guard, a female slot takes a converted female mesh or its own
    source path. A slot-32 body armature whose world female mesh would have
    needed the male one is not minted, as the body pass already does for an
    unconverted vest. A source with NO female model keeps the male one there --
    that is what the engine draws for a female anyway, and is not reported. So
    does a female path whose mesh exists NOWHERE, loose or in any archive (user,
    09-24): a dead path draws nothing, the case the female-only selection (#174)
    already keeps the male mesh for; 8 of the live slots are dead. Kept, dead
    and not-minted slots are counted and warned.
    CBBE2UBE_NO_COVERAGE_FEMALE_GUARD=1 restores the fallback."""
    return not _flag("CBBE2UBE_NO_COVERAGE_FEMALE_GUARD", False)


def _coverage_female_standin() -> bool:
    r"""#coverage-female-standin (2026-09-25, the user's rule): what does a
    coverage armature's female slot draw when the path it names exists nowhere?
    "The vanilla female counterpart where the mapping is unambiguous; otherwise
    keep the male mesh."

    1. Stand-in. A dead MOD3 (paired with MOD2) or MOD5 (paired with MOD4) takes
       the converted female mesh a vanilla armature pairs with the same male
       path -- `_female_standin_resolver`: game-master or Creation Club
       armatures, DefaultRace-primary, exactly one female path, converted. Its
       texture hash and alt-textures (MO?T/MO?S) are dropped. This also runs
       where the male mesh was not converted: a body armour whose female world
       mesh is dead and whose male mesh was never converted was not minted at
       all, and its gloves kept the dead path.
    2. Else, the male mesh: converted, as #coverage-female-guard already does;
       or, for a NON-BODY armature (outside the body slots, not a cloak, its
       male mesh not skinned to a body-fit bone), the male path as it is -- a
       non-body piece needs no conversion. A body piece, or one whose male mesh
       cannot be read, keeps the dead path.
    Live replay: 8 slots move from the converted male mesh to the vanilla
    female one, a cuirass that was not drawn is minted, its gloves draw, and
    two hoods and a helmet draw their male mesh.

    Nested: with #coverage-female-guard off, no path is ever found dead, so
    this is off too. CBBE2UBE_NO_COVERAGE_FEMALE_STANDIN=1 turns it off."""
    if not _coverage_female_guard():
        return False
    return not _flag("CBBE2UBE_NO_COVERAGE_FEMALE_STANDIN", False)


def _coverage_world_mesh() -> bool:
    r"""#coverage-world-mesh (2026-09-24): does a slot-32 body armature need its
    WORLD female mesh converted to be minted? Yes, by default.

    The body pass admitted a DefaultRace armature when ANY of MOD2..MOD5 was
    converted -- although its own comment says "BODY needs a converted mesh (an
    unconverted CBBE body on UBE clips)". A converted FIRST-PERSON mesh was
    enough, and the minted armature then drew the unconverted CBBE world mesh on
    the UBE body. Measured on the live pack: 89 links, 87 armatures -- every one
    with only a first-person mesh converted, 62 of the links let in by one
    shared vanilla first-person torso; 88 armours are left with no armature,
    62 of them children's clothing, 26 adult (mostly NPC outfits).

    Now a body armature (its own BOD2, else the armour's -- the female guard's
    test) is minted only when MOD3 was converted, or MOD3 is absent, empty or
    dead (exists nowhere, loose or in any archive) and MOD2 was converted -- the
    male mesh is then what the engine draws for a female. Hands/feet and slot
    34/38 armatures are not affected: they keep their source mesh where it was
    not converted, by design. Children's clothing is dropped with the rest (user,
    09-24: children are skipped entirely). Not-minted armatures are counted and
    warned. CBBE2UBE_NO_COVERAGE_WORLD_MESH=1 admits them again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_WORLD_MESH", False)


def _coverage_nude_skin() -> bool:
    r"""#coverage-nude-skin (2026-09-24): may a coverage armature draw the CBBE
    NUDE hands or feet on a UBE actor? No, by default.

    The per-source and slot-33 passes never extend a nude-skin armature to the
    UBE races (`_is_nude_skin_model`), but the body winner scan had no such
    check. An equippable "boots" or "gloves" item that draws bare feet or hands
    (an NPC costume) reuses `Actors\Character\Character Assets\FemaleFeet_1.nif`,
    so its UBE armature drew the CBBE feet on the UBE body. Measured on the live
    pack: 4 armatures -- 2 on such an item pair, 2 on a custom race's skin.

    Now a slot-33/37 armature whose MOD3 is a nude hand or foot under
    `actors\character\character assets\` draws the UBE body's own part instead
    (`ube_body_part_for`), with MO3T dropped, but only when that mesh resolves
    (loose, overwrite or any archive); otherwise it is not minted. On a race
    skin -- an armour that also lists a nude torso -- it is not minted at all:
    a UBE actor wears UBE's own skin. CBBE2UBE_NO_COVERAGE_NUDE_SKIN=1 mints the
    CBBE part again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_NUDE_SKIN", False)


def _coverage_ube_twin() -> bool:
    r"""#coverage-ube-twin (2026-09-24): may a coverage armature point at a
    HAND-MADE UBE mesh another mod ships at `!UBE\<source path>`, where the
    converter produced none? Yes, by default.

    Two boots/gloves records of a clothing overhaul reuse armatures whose own
    armour is covered by a hand-made UBE patch; the converter skips that mod
    (#skip-already-ube), so the two are minted drawing the CBBE mesh while the
    UBE version sits loose in the patch's folder. With this on, a model with no
    converted twin in OUR output points at a loose `meshes\!UBE\<path>` from a
    third-party mod (never our own output, never an excluded mod). In the
    non-body pass it only changes where a minted slot points. In the body pass,
    while #skip-built-ube-path is on (its default), such a twin also ADMITS an
    armature (`_admits`): its mesh was left to the builder, not converted.

    The live census moved 8 links, not the 2 expected, and every one points at
    a UBE version of the SAME mesh already installed: the two reused boots and
    gloves at their hand-made patch, a choker's two links and a gore pack's
    four dismembered-body addons at the user's own UBE BodySlide build.
    CBBE2UBE_NO_COVERAGE_UBE_TWIN=1 turns it off -- and #skip-built-ube-path
    with it, so the meshes left to their builders are converted again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_UBE_TWIN", False)


def _twin_path_strip_meshes() -> bool:
    r"""#twin-path-strip-meshes (2026-09-24): is a leading `meshes\` taken off a
    source model path before `!UBE\` is put in front of it? Yes, by default.

    An armature may spell its model `meshes\X.nif`; the engine reads that as
    `meshes\X.nif`, so its UBE version sits at `meshes\!UBE\X.nif`. The twin
    lookup already found it there, but the rebuild wrote `!UBE\meshes\X.nif`
    -- read by the engine as `meshes\!UBE\meshes\X.nif`, which exists nowhere --
    and both validators passed that string. With this on the prefix goes in the
    writer, the twin record the validator whitelists, the converted-mesh check
    and the female-model re-check alike, and the postflight judges the string
    as written. CBBE2UBE_NO_TWIN_PATH_STRIP_MESHES=1 turns it off."""
    return not _flag("CBBE2UBE_NO_TWIN_PATH_STRIP_MESHES", False)


def _strip_meshes_prefix(path: str) -> str:
    r"""`meshes\X` (either slash, any case, leading separators too) -> `X`, the
    path under `meshes\` the engine reads; any other path comes back as is.
    #twin-path-strip-meshes"""
    s = path.lstrip("\\/")
    if s[:7].lower() in ("meshes\\", "meshes/"):
        return s[7:]
    return path


def _skip_built_ube_path() -> bool:
    r"""#skip-built-ube-path (2026-09-24): is a source mesh left unconverted
    when another mod already ships a BUILT UBE mesh at the very path the
    converter would write (`meshes\!UBE\<source path>`, every weight variant)?
    Yes, by default -- while #coverage-ube-twin is on, since the twin rule is
    what then points coverage at that mesh.

    The user's rule: armour that already has a built UBE version is not
    converted. #skip-already-ube judges that by the armour records of the SAME
    plugin, so a refit plugin that overrides only the armatures of a mage set
    sent its 24 meshes to conversion, and our copies -- at a higher MO2 priority
    than the hand-made UBE set -- replaced it in game. Measured on the live
    pack: 54 of our meshes sit at a path another mod ships; ours win at 14 (12
    of that mage set, a witch's hat), the user's own UBE BodySlide build wins
    the other 40 (converted for nothing). With this on, the planner skips them
    and moves an earlier run's copy out of `meshes\` (a stale copy still wins
    the path), and a BODY armature whose mesh is such a twin is admitted like a
    converted one -- otherwise leaving the mesh to its builder would uncover the
    armour records the builder's own patch does not reach.
    CBBE2UBE_NO_SKIP_BUILT_UBE_PATH=1 converts them again."""
    return _coverage_ube_twin() and not _flag("CBBE2UBE_NO_SKIP_BUILT_UBE_PATH", False)


def _coverage_body_accessory() -> bool:
    r"""#coverage-body-accessory (2026-09-24): does a body or hands/feet armour's
    NON-deforming armature -- a hooded robe's hood -- get a UBE armature too?
    Yes, by default.

    The body pass kept only armatures with a converted mesh or a hands/feet
    slot, and the non-body pass skips any armour with a deforming slot, so a
    hood armature (slots 31/41/43) on a robe was covered by neither: on a UBE
    actor the robe drew and the hood did not. Live: 101 armours, 17 hood-style
    armatures, 6 of which the non-body pass already mints for the standalone
    hoods. Such an armature is minted exactly as that pass mints it:
    UBE-primary, its own mesh. It rides along only when a deforming armature of
    the armour is minted, so an armour the world-mesh or female rules leave out
    stays out whole. CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY=1 leaves the hood off
    again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_BODY_ACCESSORY", False)


def _accessory_race_guard() -> bool:
    r"""#accessory-race-guard (2026-09-24): does #coverage-body-accessory leave
    out the armatures the other race rules leave out? Yes, by default.

    The accessory list was built after #coverage-beast-variant took a beast-only
    variant out of the body pass, and asked only for DefaultRace and its own
    non-deforming slots. So a Khajiit hood variant on a hooded robe came back as
    an accessory and a UBE actor drew both hoods -- the double draw the beast
    rule exists to stop, and CBBE2UBE_NO_COVERAGE_BEAST_VARIANT had no effect on
    it. An armature that already names a UBE race (someone's UBE hood) got a
    second UBE copy drawn over it; the race-list rule and the non-body pass both
    refuse that. With this on, the accessory list skips both; the beast half
    still follows the beast switch. CBBE2UBE_NO_ACCESSORY_RACE_GUARD=1 takes
    them again."""
    return not _flag("CBBE2UBE_NO_ACCESSORY_RACE_GUARD", False)


def _coverage_body_cloak() -> bool:
    r"""#coverage-body-cloak (2026-09-25): may a cloak-named armature ride along
    with a body armour under #coverage-body-accessory? Only when the mesh it
    draws is a drape; yes, by default, while that rule is on.

    The accessory rule skips every cloak-named armature: the planner admits a
    cloak for conversion by name, so one left unconverted might be body-fitted
    cloth drawing its CBBE fit on the UBE body. But the conversion's crash guard
    drops a cloak on a free slot (35) whose mesh has no body-fit bone, so a
    worn robe with such a cape drew the robe on a UBE actor and no cape at all.
    Such a cape is now minted like a hood -- UBE-primary, its own mesh, as the
    non-body pass draws the same kind of cloak worn alone -- when every world
    mesh it names (MOD2, MOD3), the copy the game loads, is skinned and bound to
    no thigh/calf/butt/breast/belly bone. An unskinned mesh, a body-fitted one,
    or one that cannot be read stays out, and so does a beast variant or an
    armature that already names a UBE race, whatever
    CBBE2UBE_NO_ACCESSORY_RACE_GUARD says. CBBE2UBE_NO_COVERAGE_BODY_CLOAK=1
    leaves every cloak-named armature out again."""
    return _coverage_body_accessory() and not _flag(
        "CBBE2UBE_NO_COVERAGE_BODY_CLOAK", False)


def _coverage_human_race_list() -> bool:
    r"""#coverage-human-race-list (2026-09-24): does an armour whose only
    human-drawing armature has a primary race other than DefaultRace get a UBE
    armature? Yes, by default.

    Both coverage passes minted only armatures whose primary race (RNAM) is
    DefaultRace. An armature re-authored with an Argonian or a custom primary
    that lists the human and mer races (and their vampires) as additional races
    draws on a vanilla human woman and on nothing of a UBE race. Live census:
    291 adult armours a female UBE actor can wear -- 275 accessories (rings,
    amulets, circlets, shields), 9 body, 6 hands/feet, 1 calf.

    Admitted only where the armour has no DefaultRace armature the old rule
    admits, and only when the armour is playable or an NPC wears it, is no
    race's or NPC's skin, and the armature's female world mesh is not an effect
    (`_race_list_admits`). The armature must list DefaultRace or a vanilla
    human/mer race, and its UBE armature targets the UBE counterpart of each
    one it lists (`_ube_races_for_race_list`) -- every UBE race only when
    DefaultRace is among them. A body armature still needs a converted mesh,
    and the converter converts none of these, so the body and slot-38 pieces
    stay uncovered. Live replay: +281 links on 281 armours (the 275 accessories
    and 6 hands/feet), 154 armatures, 0 removed, 0 re-pointed.
    CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST=1 mints DefaultRace armatures only,
    as before."""
    return not _flag("CBBE2UBE_NO_COVERAGE_HUMAN_RACE_LIST", False)


# Subrecords that do NOT affect how the addon RENDERS, so they're excluded from
# the merge record-dedup key: the editor id, and the CK-generated model
# texture-HASH blocks (MODT-equivalents) for each of the 4 gendered models. Two
# minted ARMAs with the same meshes/alt-textures/slots/races that differ only in
# EDID or a texture hash are the same addon. The alt-texture SETS (MO2S/MO3S/
# MO4S/MO5S) are KEPT -- those genuinely change the look.
_ARMA_DEDUP_SKIP_SIGS = frozenset((b"EDID", b"MO2T", b"MO3T", b"MO4T", b"MO5T"))


def _arma_merge_dedup_key(payload: bytes) -> bytes:
    """The ARMA payload reduced to its render-identity bytes (EDID + model
    texture-hash blocks stripped) -- the merge record-dedup key. See
    MERGE_DEDUP_ARMAS and _ARMA_DEDUP_SKIP_SIGS."""
    out = b""
    for sig, d in esp.iter_subrecords(payload):
        if sig in _ARMA_DEDUP_SKIP_SIGS:
            continue
        out += esp.encode_subrecord(sig, d)
    return out


def dedup_armo_armature_refs(esp_path) -> int:
    """In each ARMO, drop redundant armature refs that point at THIS patch's OWN
    ARMAs sharing the same render identity (race + all gendered meshes + biped
    slot flags; see _arma_dedup_identity) -- keeping the most complete one (most
    subrecords). Vanilla/master armatures are never touched. Returns the number of
    refs removed. Run AFTER the merge. See the block comment above."""
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
                by_key.setdefault(ident[:7], []).append((fid, ident[7]))
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
                    if ident is not None and ident[:7] in keeper_for:
                        key = ident[:7]
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
    for piece in _combined_piece_family(p):
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

    for piece in _combined_piece_family(p):
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
                        _model_path_text(d, "utf-8")
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


def _arma_race_master_names(payload: bytes, src_masters: list[str],
                            src_filename: str) -> "list[str]":
    """Master NAMEs referenced by an ARMA's RNAM (primary race) + MODL (additional
    races), in the source ESP's master space (own-records resolve to src_filename).
    Unified hands/feet coverage declares these so a source-primary armature's
    preserved vanilla/custom races resolve in the coverage ESP's master space
    instead of dangling. #unified-coverage"""
    names: list[str] = []
    seen: set[str] = set()
    for sig, data in esp.iter_subrecords(payload):
        if sig in (b"RNAM", ARMA_ADDITIONAL_RACE_SIG) and len(data) == 4:
            fid = struct.unpack("<I", data)[0]
            top = (fid >> 24) & 0xFF
            nm = src_masters[top] if top < len(src_masters) else src_filename
            if nm.lower() not in seen:
                seen.add(nm.lower())
                names.append(nm)
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


def _arma_path_bytes() -> bool:
    r"""#arma-path-bytes (2026-09-25): are armature model paths read and
    written in the game's codepage? Yes, by default.

    `rebuild_arma_payload` read every MOD2-5 path as UTF-8 with errors
    ignored and wrote it back as UTF-8 -- also where the path was left as it
    was. The game reads these strings as cp1252 (Windows-1252), so an accented
    byte (0xE9, 'e' with an acute) vanished: the armature named a mesh that
    exists nowhere, and the converted-mesh lookup was asked about that same
    wrong path, so the piece was never redirected either. `restore_female_models`
    compared and rewrote through the same round trip. Now a path is decoded as
    cp1252 with `surrogateescape`, so its bytes come back EXACTLY: an unchanged
    path is written as the bytes it had, and a redirected one is the prefix plus
    those bytes. Live census: 0 of 9,350 model paths the coverage passes hand
    over, and 0 armature paths in the source plugins, hold a byte >= 0x80.
    CBBE2UBE_NO_ARMA_PATH_BYTES=1 restores the UTF-8 round trip."""
    return not _flag("CBBE2UBE_NO_ARMA_PATH_BYTES", False)


def _model_path_str(data: bytes, as_bytes: bool) -> str:
    """A MOD2-5 string as text. `as_bytes` (#arma-path-bytes): cp1252, the
    game's codepage, lossless -- `_model_path_zstring` gives back the very
    bytes, the five bytes cp1252 leaves undefined included. Else the old
    UTF-8 read, which drops every byte that is not valid UTF-8."""
    s = data.rstrip(b"\x00")
    if as_bytes:
        return _game_codepage_text(s)
    return s.decode("utf-8", errors="ignore")


def _model_path_read(data: bytes) -> str:
    """A MOD2-5 string of a plugin this tool WROTE, read back as it was
    written: `_model_path_text` (the game's codepage, as #arma-path-bytes
    writes), or UTF-8 when #arma-path-bytes is off and wrote UTF-8 -- so our
    converted NIF at a path with a byte in 0x80-0x9F is found. The alt-texture
    reconcile and the postflight read through it. #model-path-codepage; with
    CBBE2UBE_NO_MODEL_PATH_CODEPAGE=1 the old latin-1 read."""
    from .bsa_strings import model_path_codepage
    if model_path_codepage() and not _arma_path_bytes():
        # UTF-8 as written; a byte the UTF-8 writer gave back from a lone
        # surrogate (#model-path-writer) comes back as that surrogate, the
        # very text the converted-mesh lookup was asked about -- the old
        # `ignore` read dropped it and named a file that does not exist.
        return data.rstrip(b"\x00").decode("utf-8", "surrogateescape")
    return _model_path_text(data, "latin-1")


def _model_path_zstring(path: str, as_bytes: bool) -> bytes:
    """A model path as the null-terminated string an ARMA stores: cp1252 when
    `as_bytes` and the text has a cp1252 form (every path `_model_path_str`
    read, plus ASCII prefixes), else UTF-8 as before. #arma-path-bytes

    #model-path-writer: never raises on a byte the one decoder kept as a lone
    surrogate (one of the five cp1252 leaves undefined): the UTF-8 write gives
    it back as that byte, where a strict UTF-8 write raised. Any other text is
    written exactly as before."""
    if as_bytes:
        try:
            return path.encode("cp1252", "surrogateescape") + b"\x00"
        except UnicodeEncodeError:
            pass
    return path.encode("utf-8", "surrogateescape") + b"\x00"


def rebuild_arma_payload(source_payload: bytes, *,
                         new_primary_rnam: int,
                         new_additional_race_fids: Iterable[int],
                         path_prefix: str = "!UBE\\",
                         alt_texture_fid_remap: "callable[[int], int] | None" = None,
                         converted_nif_exists: "callable[[str], bool] | None" = None,
                         ensure_female: bool = True,
                         male_fallback_log: "list | None" = None,
                         keep_named_female: bool = False,
                         declined_log: "list | None" = None,
                         female_mesh_exists: "callable[[str], bool] | None" = None,
                         strip_meshes_prefix: bool = False,
                         female_standin: "callable[[str, str], str | None] | None" = None,
                         dead_female_male_as_is: "callable[[str], bool] | None" = None,
                         ) -> bytes:
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

    `keep_named_female` (#coverage-female-guard): a MOD3/MOD5 that names a mesh
    of its own keeps that path when it was not converted, instead of taking the
    converted male mesh. An absent or empty female slot is still filled from the
    male one, and so is one whose mesh `female_mesh_exists` says exists nowhere
    (None = assume it exists). Each slot kept is appended to `declined_log` as
    {"slot", "kept", "male"}, each dead one as {"slot", "dead", "male"}.

    `strip_meshes_prefix` (#twin-path-strip-meshes): a model spelt `meshes\\X`
    is written `<path_prefix>X`, not `<path_prefix>meshes\\X` (a path the engine
    reads as `meshes\\!UBE\\meshes\\X`). False writes the path as it was.

    `female_standin(sig, male_path)` (#coverage-female-standin, needs
    `keep_named_female`): for a named female slot whose mesh
    `female_mesh_exists` says exists nowhere, the converted female model to draw
    instead (written `<path_prefix>` + it), or None. `male_path` is the paired
    male SOURCE path (MOD2 for MOD3, MOD4 for MOD5), whether or not it was
    converted. It outranks the converted male mesh. `dead_female_male_as_is(
    male_path)`: with no stand-in and no converted male, may the dead slot draw
    the unconverted male path as it is? The slot's MO?T/MO?S are dropped either
    way. Neither is added to `male_fallback_log` (`restore_female_models` must
    not undo them). `declined_log` gets {"slot", "standin", "orig"},
    {"slot", "male_as_is", "orig"}, or -- a dead slot left on its path --
    {"slot", "dead_kept", "male"}. Unset (None), the slot is never probed
    where the male mesh was not converted, as before.

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
    # #coverage-female-standin: the male SOURCE paths (as the game reads them),
    # and the female alt-textures to drop behind a stand-in or an as-is male.
    src_mod2 = src_mod4 = ""
    skip_mo3s = skip_mo5s = False
    _ask_dead = keep_named_female and (female_standin is not None
                                       or dead_female_male_as_is is not None)
    # #arma-path-bytes: model paths in the game's codepage, byte-exact; the male
    # source paths also as written (the lookups above keep their cp1252 read).
    _pb = _arma_path_bytes()
    src_mod2_w = src_mod4_w = ""
    # #model-path-writer: with #arma-path-bytes off every path of the record
    # is written UTF-8 (as the parent did), the male path and a stand-in
    # included, so `_model_path_read` reads them all back one way; the UTF-8
    # writer gives a lone surrogate back as its byte instead of raising.
    _gc = _pb
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
            path = _model_path_str(data, _pb)
            converted = bool(path) and (converted_nif_exists is None
                                        or converted_nif_exists(path))
            if converted and strip_meshes_prefix:
                new_path = path_prefix + _strip_meshes_prefix(path)
            else:
                new_path = (path_prefix + path) if converted else path
            # UBE is female-only. If the male model was converted but the female
            # model was NOT (wrong name, not shipped), the female UBE actor renders
            # nothing. Redirect MOD3/MOD5 to the converted male mesh and drop the
            # now-mismatched texture-hash. Non-body (helmets) are unaffected
            # because their MOD2 isn't converted either, so conv_mod2 stays None.
            _male = (conv_mod2 if sig == b"MOD3" else
                     conv_mod4 if sig == b"MOD5" else None)
            _fallback = bool(_male) and not converted and ensure_female
            # keep_named_female: a female slot naming its OWN mesh keeps it --
            # unless that mesh exists nowhere (a dead path draws nothing, so the
            # male mesh stays, as the female-only selection decided). Asked only
            # where the male mesh would be used.
            _keep = _named_dead = False
            if _fallback and keep_named_female and path:
                # The lookup gets the path as the game reads it (cp1252); with
                # CBBE2UBE_NO_ARMA_PATH_BYTES the utf-8 decode above drops
                # non-ASCII bytes. #coverage-female-guard
                _probe = _model_path_text(data, "cp1252")
                _keep = female_mesh_exists is None or female_mesh_exists(_probe)
                _named_dead = not _keep
            # #coverage-female-standin: a named female slot whose male mesh was
            # NOT converted is asked too -- only when a stand-in rule was given,
            # and a missing lookup still means "it exists".
            _dead = _named_dead
            if (not _fallback and _ask_dead and path and not converted
                    and ensure_female and sig in (b"MOD3", b"MOD5")):
                _dead = female_mesh_exists is not None and not female_mesh_exists(
                    _model_path_text(data, "cp1252"))
            _src_male = src_mod2 if sig == b"MOD3" else src_mod4
            _standin = _as_is = None
            if _dead and female_standin is not None:
                _standin = female_standin(sig.decode(), _src_male)
            if (_dead and _standin is None and not _fallback and _src_male
                    and dead_female_male_as_is is not None
                    and dead_female_male_as_is(_src_male)):
                _as_is = _src_male
            if _named_dead and _standin is None and declined_log is not None:
                declined_log.append({"slot": sig.decode(), "dead": path,
                                     "male": _male})
            if _standin is not None or _as_is is not None:
                # The vanilla female counterpart (converted), else the male
                # path as it is. Not a male fallback: kept out of
                # male_fallback_log, so the female-model re-check never undoes
                # it. The female texture hash and alt-textures name the dead
                # mesh's shapes, so both go. #coverage-female-standin
                _to = (path_prefix + _standin) if _standin is not None else _as_is
                # The male path as it was written, byte for byte. #arma-path-bytes
                # With #arma-path-bytes off both are written UTF-8 like every
                # other path of the record, without raising. #model-path-writer
                _to_w = (_to if _standin is not None or not _gc else
                         src_mod2_w if sig == b"MOD3" else src_mod4_w)
                out += esp.encode_subrecord(sig, _model_path_zstring(_to_w, _gc))
                if declined_log is not None:
                    declined_log.append(
                        {"slot": sig.decode(), "standin": _to, "orig": path}
                        if _standin is not None else
                        {"slot": sig.decode(), "male_as_is": _to, "orig": path})
                if sig == b"MOD3":
                    saw_mod3 = skip_mo3t = skip_mo3s = True
                else:
                    saw_mod5 = skip_mo5t = skip_mo5s = True
            elif sig == b"MOD3" and _fallback and not _keep:
                out += esp.encode_subrecord(b"MOD3", _model_path_zstring(conv_mod2, _pb))
                saw_mod3 = True
                skip_mo3t = True
                if male_fallback_log is not None:
                    # Record for the last-step female-model re-check:
                    # restore_female_models() undoes this fallback when the
                    # original female mesh is later found in the output.
                    male_fallback_log.append(
                        {"slot": "MOD3", "orig": path, "to": conv_mod2})
            elif sig == b"MOD5" and _fallback and not _keep:
                out += esp.encode_subrecord(b"MOD5", _model_path_zstring(conv_mod4, _pb))
                saw_mod5 = True
                skip_mo5t = True
                if male_fallback_log is not None:
                    male_fallback_log.append(
                        {"slot": "MOD5", "orig": path, "to": conv_mod4})
            else:
                out += esp.encode_subrecord(sig, _model_path_zstring(new_path, _pb))
                if _keep and declined_log is not None:
                    declined_log.append({"slot": sig.decode(), "kept": path,
                                         "male": _male})
                if _dead and not _fallback and declined_log is not None:
                    # Dead, and nothing to draw instead. #coverage-female-standin
                    declined_log.append({"slot": sig.decode(), "dead_kept": path,
                                         "male": _src_male})
                if sig == b"MOD3":
                    saw_mod3 = True
                elif sig == b"MOD5":
                    saw_mod5 = True
                elif sig == b"MOD2" and converted:
                    conv_mod2 = new_path
                elif sig == b"MOD4" and converted:
                    conv_mod4 = new_path
                if sig == b"MOD2":
                    src_mod2 = _model_path_text(data, "cp1252")
                    src_mod2_w = _model_path_str(data, True)
                elif sig == b"MOD4":
                    src_mod4 = _model_path_text(data, "cp1252")
                    src_mod4_w = _model_path_str(data, True)
        elif sig in (b"MO3S", b"MO5S") and (skip_mo3s if sig == b"MO3S" else skip_mo5s):
            # The dead female mesh's alt-textures, behind a stand-in or an as-is
            # male. #coverage-female-standin
            continue
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
        out += esp.encode_subrecord(b"MOD3", _model_path_zstring(conv_mod2, _pb))
        if male_fallback_log is not None:
            # orig=None: the armature never had a female model; nothing to restore.
            male_fallback_log.append(
                {"slot": "MOD3", "orig": None, "to": conv_mod2})
    if ensure_female and not saw_mod5 and conv_mod4:
        out += esp.encode_subrecord(b"MOD5", _model_path_zstring(conv_mod4, _pb))
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


# Skyrim.esm DefaultRace FormID (low 24 bits). Used to gate non-body ARMA
# passthrough: adding UBE races to a beast/custom-race armature crashes.
# Every humanoid player-equippable ARMA's primary RNAM points here.
_DEFAULT_RACE_LOW24 = 0x000019


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
            own_byte = len(plugin_esp.header.masters)
            for rec in race_grp.records:
                # Skip OVERRIDE records (FormID originated by a MASTER, not this
                # plugin). Attributing the ref to THIS plugin yields a DANGLING
                # race ref -- the "FExxxxxx <Could not be resolved>" bug -- because
                # the race is originated elsewhere; and it DUPLICATES the
                # originating plugin's race, which for a UBE_AllRace override is
                # already covered by UBE_RACE_FIDS_24. Only ORIGINATED races (own
                # top byte) are genuinely-new races worth adding.
                if ((rec.formid >> 24) & 0xFF) != own_byte:
                    continue
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
    _strip = _twin_path_strip_meshes()
    _pb = _arma_path_bytes()
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
            # Write the path that was checked: `meshes\X` found at
            # <meshes_root>/X is `!UBE\X`. #twin-path-strip-meshes
            todo.setdefault(int(fid), {})[slot] = (
                e.get("to") or "",
                path_prefix + (_strip_meshes_prefix(orig) if _strip else orig))
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
                        # Read and written as rebuild_arma_payload wrote them
                        # (#arma-path-bytes): the sidecar's strings are its.
                        cur = _model_path_str(data, _pb)
                        if cur == male_path:
                            out += esp.encode_subrecord(
                                sig, _model_path_zstring(female_path, _pb))
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


def coverage_arma_race_targeting(
        slot_bits: int, src_rnam: "int | None", src_additional: "list[int]", *,
        remap_src_fid, src_to_patch_byte: "dict[int, int]",
        ube_primary: int, ube_additional: "list[int]",
        ) -> "tuple[int, list[int]]":
    """Pick (primary RNAM, additional-race list) for a minted coverage ARMA.

    Shared by the per-source patch and the winner-scan coverage passes so both
    build hand/foot armatures identically. All returned FormIDs are in the
    caller's patch master space.

    BODY (slot 32) armatures target UBE actors via the UBE body race, so the 16
    UBE races alone suffice -> UBE-primary. HANDS (33) / FEET (37) are different:
    a UBE actor's hand/foot slot resolves to a VANILLA race (the documented
    nude-hands fallback routing), so an ARMA listing ONLY UBE races never matches
    -> the gauntlet/boot is invisible and hides the real hands. For those we KEEP
    the source primary race and PRESERVE the source's vanilla playable-race list
    (remapped into patch space) then ADD the UBE races on top -- the ~16-UBE +
    ~vanilla list a correct coverage armature carries. Dropping the vanilla races
    was the modded-gauntlet-invisible bug. A source race whose master the patch
    does not hold is skipped (can't emit a valid ref) rather than corrupting;
    if the primary itself can't be remapped it falls back to UBE-primary.

    `remap_src_fid` translates a source-master-space FormID into patch space;
    `src_to_patch_byte` maps source master bytes that ARE patch masters.
    """
    if not (slot_bits & _BIPED_SLOT_HANDS_FEET_BITS):
        return ube_primary, ube_additional
    prim = remap_src_fid(src_rnam) or ube_primary   # remap fail -> fall back
    addl: "list[int]" = []
    seen: "set[int]" = set()
    for _f in src_additional:
        if ((_f >> 24) & 0xFF) not in src_to_patch_byte:
            continue  # source master not mastered by patch -> skip
        _r = remap_src_fid(_f)
        if _r and _r not in seen:
            seen.add(_r)
            addl.append(_r)
    for _u in ube_additional:
        if _u not in seen:
            seen.add(_u)
            addl.append(_u)
    return prim, (addl or list(ube_additional))


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
    UBE ARMA (MOD3 -> !UBE, UBE races) and record a SkyPatcher link for it
    — making those vanilla body armors visible + fitted on UBE
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
    # An ESM-flagged source or master goes ahead of UBE_AllRace and the other
    # regular plugins, as in load order; nothing was sorting this list, so
    # `[..., BSAssets.esm, UBE_AllRace.esp, BSHeartland.esm]` was written.
    # Vanilla DLC stay first (stable). #master-load-order
    patch_masters.sort(key=lambda m: _master_sort_key(m, master_data_dirs))

    # Patch's own records use top byte == len(patch_masters).
    own_top_byte = len(patch_masters) << 24

    # UBE race FormIDs in patch address space.
    ube_top = make_master_byte(patch_masters, ube_allrace_filename) << 24
    ube_primary = ube_top | UBE_PRIMARY_BRETON_FID_24
    # Gold-standard UBE_AllRace armatures list UBE_BretonRace in BOTH RNAM and
    # the MODL block; mirror that exactly (full 16-race list, primary first).
    ube_additional = [ube_top | low for low in UBE_RACE_FIDS_24]
    # Add discovered extra UBE races; remap their FormIDs to patch space.
    # GUARD (robustness): drop only EXACT-duplicate race FormIDs. The override
    # skip in _discover_ube_races already prevents the dangling-ref / duplicate
    # class, so this is belt-and-suspenders. We deliberately do NOT drop by low id
    # alone: a genuinely-ORIGINATED custom race whose low id happens to collide
    # with a UBE_AllRace value is a DIFFERENT race and must be kept, or that race
    # renders no UBE armor.
    _seen_race_fids = set(ube_additional)
    for plugin_name, fid, _edid in extra_ube_races:
        ref = (make_master_byte(patch_masters, plugin_name) << 24) | (fid & 0xFFFFFF)
        if ref in _seen_race_fids:
            continue
        _seen_race_fids.add(ref)
        ube_additional.append(ref)

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

    # Body armatures whose mesh we converted are owned by the SkyPatcher
    # body-coverage pass, so DON'T mint an ARMA for them here.
    # Body-only SkyPatcher pivot removed -- the always-on full path mints + LINKS
    # body armatures, so the body-suppression gates stay down. (Dead `if _bsp`
    # branches below are pruned in a later stage.)
    _bsp = False
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
        side: (1) Skyrim.esm is not always master index 0 (some third-party patch
        ESPs list it later); (2) an own-record ref (top byte == len(masters)) -- notably
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
                model_paths.append(_model_path_text(data, "utf-8"))
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

        # Rebuild ARMA payload with UBE race targeting + path prefix. Body keeps
        # UBE-primary; hands/feet keep source-primary + preserved vanilla races +
        # UBE races (see coverage_arma_race_targeting for the full rationale).
        _prim, _additional_for_arma = coverage_arma_race_targeting(
            slot_bits, src_rnam, src_additional,
            remap_src_fid=_remap_src_fid_to_patch,
            src_to_patch_byte=src_to_patch_byte,
            ube_primary=ube_primary, ube_additional=ube_additional)
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

    # Link source ARMOs whose MODL list references a source ARMA we converted:
    # each gets an armorAddonsToAdd entry (recorded in _sp_links) rather than an
    # ESP override.

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
                mod3 = _model_path_text(d, "utf-8")
            elif sig == b"MOD2":
                mod2 = _model_path_text(d, "utf-8")
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
            # SkyPatcher: record the link, emit NO override. The armature reaches
            # the ARMO at runtime via armorAddonsToAdd, applied to whatever record
            # actually wins the load order.
            _armo_abs = _record_abs_fid(
                src_armo.formid, src.header.masters, src_filename)
            _adds = _sp_links.setdefault(_armo_abs, [])
            for _ref in referenced_src_armas:
                _mf = new_arma_fids.get(_ref)
                if _mf is not None and _mf in new_armas_to_add:
                    _sa = _record_abs_fid(
                        _ref, src.header.masters, src_filename)
                    _adds.append((_mf, _sa[0], _sa[1]))

    # --- Master ESM ARMO scan ---
    # Walk each master ESM to find ARMOs that reference our converted ARMAs.
    # Those ARMOs need override entries in our patch — otherwise the master's
    # armature list is what the engine sees and UBE-race actors find no match.
    if master_data_dirs is None:
        master_data_dirs = [source_esp_path.parent]

    # (The localized-name string resolver was only needed to synthesize FULL for
    # ARMO overrides; SkyPatcher emits no overrides, so it is gone.)

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
        for src_fid in sorted(converted_arma_src_fids):  # sorted: #deterministic-set-iteration
            if ((src_fid >> 24) & 0xFF) == master_byte_in_src:
                master_space_fid = (master_own_byte << 24) | (src_fid & 0xFFFFFF)
                lookup_in_master_space.add(master_space_fid)
                master_to_src_fid[master_space_fid] = src_fid

        # Mesh-path-driven vanilla body coverage: loose-mesh replacers ship
        # vanilla body meshes with no ESP records. If we converted the mesh
        # to !UBE, mint a UBE ARMA and record a SkyPatcher armorAddonsToAdd
        # link for it. Matched purely by mesh path; no per-armor logic.
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
                        mod3 = _model_path_text(d, "utf-8")
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
        for m_armo in master_armos:
            # Skip a master not present in our patch's master table -- we can't
            # emit a valid link identity for it. (Masters we scan normally ARE
            # patch masters; this guards a theoretical mismatch.)
            if not any(mn.lower() == master_name.lower() for mn in patch_masters):
                continue

            # Find which converted ARMAs this master ARMO references.
            new_armas_to_add: list[int] = []
            for sig, data in esp.iter_subrecords(m_armo.payload):
                if sig == ARMO_ARMATURE_SIG and len(data) == 4:
                    ref_master_fid = struct.unpack("<I", data)[0]
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
            # SkyPatcher: link the master ARMO, no override (see the same-plugin
            # branch above).
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
    # SkyPatcher-only: the patch is pure minted-ARMA -- it emits NO ARMO group
    # (nothing overrides a third-party record); coverage is delivered via links.
    if new_arma_records:
        out.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))

    # SkyPatcher links: resolve minted fids to their Record objects BEFORE
    # prune_unused_masters renumbers FormIDs (same trap the male-fallback
    # sidecar documents); serialize with the post-prune rec.formid after save.
    _sp_rec_links: "dict[tuple[str, int], list]" = {}
    if _sp_links:
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
    except OSError as _e:
        # NOT silent: the merge reads it to emit armorAddonsToAdd lines.
        import sys as _sys
        print(f"  WARN: skypatcher-links sidecar not written ({_e}) -- the merge reads it to emit armorAddonsToAdd lines",
              file=_sys.stderr)

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
    except OSError as _e:
        # NOT silent: restore_female_models cannot re-point female models.
        import sys as _sys
        print(f"  WARN: male-fallback sidecar not written ({_e}) -- restore_female_models cannot re-point female models",
              file=_sys.stderr)

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
        "armo_override_count": 0,   # SkyPatcher-only: never any ARMO overrides
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
    # Not a crash, but build-failing: an ARMA linked to a source mesh while its
    # converted !UBE mesh exists = broken/invisible armor at scale (the 204-armor
    # coverage-routing regression). Fail the build so it never ships silently.
    "unconverted-mesh-linked",
)
# NOTE: "unmappable-master-ref" is deliberately NOT here (soft, not CTD). It flags
# master-LIST incompleteness (a master X in the list whose own master Y isn't),
# which is NOT load-breaking: a plugin only needs its DIRECT refs resolvable, and
# Skyrim loads transitive masters via each master's own master list. If the patch
# doesn't master Y it CANNOT encode a ref to Y (no top byte), so there's no
# misroute. The only real misroute mode -- an out-of-range top byte -- is caught
# by "formid-out-of-range" (which IS CTD above). Empirically confirmed: on the
# real modlist this fired on several large third-party plugins with 0/98,972 refs out of range
# and the game loaded fine. Kept as a soft warning so genuine oddities still show.


def postflight_validate_combined(combined_path, meshes_root=None, *,
                                 master_data_dirs=None,
                                 mesh_resolves=None) -> dict:
    """Re-validate the FINAL merged Combined ESP (and any ESL split pieces) AFTER
    the merge + winner-rebase + alt-texture reconcile + hands-slot fix have run.

    `validate_patch` runs per-SOURCE at generation time and never sees those
    post-merge mutations, so a structural break they introduce on the actual
    loaded plugin (the Combined) is otherwise invisible until an in-game CTD /
    invisible armor (the historical "stale Combined / ESL overflow / master-order"
    class). This is the single highest-leverage convert-time guard.

    Returns {"ctd": [(piece, warn)], "soft": [(piece, warn)], "pieces": [name,...]}.
    CTD = load-breaking (caller should fail the build); soft = invisible/cosmetic
    (warn only). Globs `<stem>*.esp` so ESL split pieces are all covered.
    `mesh_resolves`: as in `validate_patch` (#coverage-nude-skin)."""
    combined_path = Path(combined_path)
    pieces = _combined_piece_family(combined_path, ".esp")
    if combined_path.is_file() and combined_path not in pieces:
        pieces.append(combined_path)
    ctd: list = []
    soft: list = []
    for piece in pieces:
        try:
            warns = validate_patch(piece, meshes_root,
                                   master_data_dirs=master_data_dirs,
                                   mesh_resolves=mesh_resolves,
                                   check_load_order=True)
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
                   master_data_dirs: list[Path] | None = None,
                   mesh_resolves: "callable[[str], bool] | None" = None,
                   check_load_order: bool = False,
                   ) -> list[str]:
    """Walk a generated patch ESP and return a list of warning strings
    for structural problems. Empty list = clean.

    Warning prefixes (stable for downstream grep/filter):
      "modl-after-data"        ARMO has MODL after DATA; Skyrim stops reading
                               armatures at DATA, so those are silently ignored.
      "master-ordering"        ESM master appears after a regular ESP; crash.
      "master-load-order"      (check_load_order=True, the loaded Combined only) the
                               masters are not listed in load order; xEdit would re-sort.
      "next-object-id"         next_object_id <= max own FormID; engine may
                               collide dynamic FormIDs with patch records.
      "esl-overflow"           ESL flag set but own record count > 2048.
      "formid-zero"            Record has FormID 0x00000000 (player-reserved).
      "formid-out-of-range"    FormID references master index past the list end.
      "missing-nif"            An ARMA `!UBE\\` model path with no mesh on
                               disk. STARTUP-CTD cause #1, not "invisible":
                               the engine reads a freed/garbage string when an
                               actor wearing it loads. Only `!UBE\\` paths are
                               counted -- source paths resolve from masters'
                               BSAs and belong to "unconverted-mesh-linked" --
                               so every hit is a path this tool wrote aimed at
                               a mesh this tool did not write.
      "unconverted-mesh-linked" ARMA MOD3/MOD5 points to a SOURCE mesh while a
                               converted !UBE mesh exists for it -> wears the
                               un-converted mesh (invisible/distorted, no morphs).
      "armo-missing-full"      ARMO has no FULL; inventory UI silently hides it.
      "unmappable-master-ref"  Override record references a transitive master
                               not in this patch's master list; FormID misroutes.
      "modt-malformed"         MO?T block with bad header (len != 12*(1+count));
                               engine misreads as millions of entries -> CTD.

    Args:
      esp_path: the patch ESP to validate.
      meshes_root: optional path to the mod's `meshes/` directory for
        NIF-existence checking. Skipped if the directory can't be found.
      mesh_resolves: for a `!UBE\\` path NOT under `meshes_root`, does the game
        load it from another mod? The coverage step points a slot outside our
        output on purpose in two cases -- the UBE body's own hands/feet
        (#coverage-nude-skin) and a hand-made UBE twin (#coverage-ube-twin) --
        and only after checking that it resolves; such a path is not missing.
        None = only our own output counts, as before.
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

    # #master-load-order: the masters of a plugin the GAME loads (the Combined)
    # are in the order xEdit's Sort Masters would give them -- the load order. The
    # tier test above cannot see an ESPFE listed ahead of an ESM that loads before
    # it. Only when the run knows the load order, and only when asked: a per-source
    # patch is never loaded, so its master order is not worth a warning.
    if check_load_order and _LOAD_ORDER_INDEX:
        known = [(m, _LOAD_ORDER_INDEX[m.lower()]) for m in e.header.masters
                 if m.lower() in _LOAD_ORDER_INDEX]
        bad = [(a, b) for a, b in zip(known, known[1:]) if a[1] > b[1]]
        if bad:
            warnings.append(
                f"master-load-order: {len(bad)} master(s) are listed out of load "
                f"order, e.g. {bad[0][1][0]} comes after {bad[0][0][0]}, but "
                f"loads before it (xEdit's Sort Masters would rewrite the plugin)")

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
            unconv = 0
            unconv_examples: list[str] = []
            for r in arma_grp.records:
                for sig, sd in esp.iter_subrecords(r.payload):
                    if sig not in (b"MOD3", b"MOD5"):
                        continue
                    path = _model_path_read(sd)       # #model-path-codepage
                    if not path:
                        continue
                    # Anything without the !UBE\ prefix is a source path. Most are
                    # vanilla/accessory meshes the engine resolves from masters'
                    # BSAs -- fine. BUT if a converted !UBE\ mesh EXISTS for this
                    # exact path, the ARMA was never redirected -> the armour wears
                    # the UN-converted source mesh on the UBE body (invisible /
                    # distorted / no morphs). That is the coverage-routing bug that
                    # once shipped 204 broken armors silently. #unconverted-mesh-linked
                    if not path.lower().startswith("!ube\\"):
                        if (meshes_root / "!UBE" / path.replace("\\", "/")).is_file():
                            unconv += 1
                            if len(unconv_examples) < 5:
                                unconv_examples.append(
                                    f"{sig.decode()}={path} (ARMA {r.formid:08X})")
                        continue
                    disk = meshes_root / path.replace("\\", "/")
                    if not disk.is_file():
                        # A path the coverage step aimed at another mod's mesh
                        # (UBE body part, hand-made twin) loads from there: no
                        # garbage model string, so no crash. #coverage-nude-skin
                        if mesh_resolves is not None and mesh_resolves(path):
                            continue
                        missing += 1
                        if len(missing_examples) < 5:
                            missing_examples.append(
                                f"{sig.decode()}={path} (ARMA {r.formid:08X})"
                            )
            if missing:
                warnings.append(
                    f"missing-nif: {missing} ARMA model path(s) point to "
                    f"!UBE\\ NIF(s) not present under {meshes_root}. "
                    f"The engine reads a freed/garbage model path when an "
                    f"actor wearing one loads -- startup-CTD cause #1 "
                    f"(EXCEPTION_ACCESS_VIOLATION), NOT merely empty armour. "
                    f"Only paths THIS tool wrote are counted. "
                    f"Examples: {missing_examples}"
                )
            if unconv:
                warnings.append(
                    f"unconverted-mesh-linked: {unconv} ARMA model path(s) point to a "
                    f"SOURCE mesh while a converted !UBE\\ mesh EXISTS for it -- the "
                    f"armour wears the un-converted source on the UBE body (invisible / "
                    f"distorted / no morphs). Examples: {unconv_examples}"
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
    rest.sort(key=lambda i: _master_sort_key(masters[i], master_data_dirs))
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
    for piece in _combined_piece_family(p):
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
            paths.append(_model_path_text(data, "utf-8"))
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


# #coverage-nude-skin: the UBE body's own hands and feet, as UBE_AllRace's
# 00UBE_NakedHands / 00UBE_NakedFeet name them and the NIF-level extremity
# injector (`_inject_ube_extremity_replacement`) reads them; `{}` is the weight
# suffix. Keyed by the weight-stripped CBBE basename, EXACT: a per-race variant
# (FemaleHandsKhajiit) is not a UBE part.
_UBE_BODY_PART_MESH = {
    "femalehands": "!UBE\\Hands\\femalehands_tangent{}.nif",
    "femalefeet": "!UBE\\Feet\\femalefeet_tangent{}.nif",
}
_NUDE_SKIN_DIR = "actors\\character\\character assets\\"


def _nude_skin_parts(model_path: str) -> "tuple[str, str] | None":
    """(weight-stripped basename, weight suffix) of a mesh under the character
    assets skin folder, else None. The suffix defaults to `_1`, as
    `nif_convert_bodyrefs.weight_suffix_of` does for a path with neither."""
    p = (model_path or "").replace("/", "\\").lstrip("\\").lower()
    if p.startswith("meshes\\"):
        p = p[len("meshes\\"):]
    if not p.startswith(_NUDE_SKIN_DIR) or not p.endswith(".nif"):
        return None
    base = p.rsplit("\\", 1)[-1][:-4]
    if base.endswith(("_0", "_1")):
        return base[:-2], base[-2:]
    return base, "_1"


def ube_body_part_for(model_path: str) -> "str | None":
    r"""#coverage-nude-skin: the UBE body's own hand or foot mesh for a CBBE NUDE
    hand/foot path, keeping its `_0`/`_1` weight -- or None when the path is not
    one. The path test is the folder AND the basename: a basename test alone
    also catches real armour named after the body (a pair of pants shipped as
    `Armor\...\femalebody_1.nif`)."""
    parts = _nude_skin_parts(model_path)
    if parts is None:
        return None
    tmpl = _UBE_BODY_PART_MESH.get(parts[0])
    return tmpl.format(parts[1]) if tmpl else None


def is_ube_body_part_path(path: str) -> bool:
    r"""True for exactly the UBE body-part paths `ube_body_part_for` returns --
    what the post-merge validator may resolve outside our output."""
    p = (path or "").replace("/", "\\").lower()
    return p in {t.format(s).lower() for t in _UBE_BODY_PART_MESH.values()
                 for s in ("_0", "_1")}


def _is_nude_torso_model(model_path: str) -> bool:
    """A nude body torso under the character assets skin folder (any race's
    `femalebody...`). An armour listing one is a race SKIN, not an item."""
    parts = _nude_skin_parts(model_path)
    return parts is not None and parts[0].startswith("femalebody")


def _redirect_mod3(payload: bytes, new_path: str) -> bytes:
    """Point an armature's MOD3 at `new_path` and drop its MO3T: the texture
    hash belongs to the mesh it replaced. #coverage-nude-skin"""
    out = b""
    for sig, data in esp.iter_subrecords(payload):
        if sig == b"MOD3":
            out += esp.encode_subrecord(
                b"MOD3", _model_path_zstring(new_path, _arma_path_bytes()))
        elif sig == b"MO3T":
            continue
        else:
            out += esp.encode_subrecord(sig, data)
    return out


# Subrecords stripped when minting a UBE ARMA from a vanilla master body ARMA:
# alt-texture TXST refs + texture hashes (stale master-space FormIDs) + the
# footstep-sound FormID. Same set the per-mod master-scan uses.
_STRIP_VANILLA_BODY_ARMA = {
    b"MO2S", b"MO3S", b"MO4S", b"MO5S",
    b"MO2T", b"MO3T", b"MO4T", b"MO5T",
    b"SNDD",
}


# The UBE race-skin EditorIDs are `00UBE_SkinNaked` (the ARMO) and its three ARMA
# templates `00UBE_NakedTorso` / `00UBE_NakedHands` / `00UBE_NakedFeet`. Recorded here
# as reference only: lookup tables for them existed but nothing read them (removed
# 2026-07-27). Skin routing goes through RNAM/race matching, not EditorID strings --
# see the nude hands/feet wrist-desync work for why matching on names was abandoned.
# FormID-bearing subrecords are remapped via the canonical module sets
# (FORMID_SINGLE_SUBRECORD_SIGS + FORMID_ARRAY_SUBRECORD_SIGS) so the fold
# stays in sync with the master-prune pass's notion of what is a FormID.


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


# The active plugins in load order, lowercased name -> position, for sorting a
# written plugin's masters the way the game and xEdit order them. Set once by the
# run (`set_load_order`); empty when unknown (then masters sort by tier alone, as
# before). Kept apart from _ESM_TIER_CACHE: the postflight clears that one to
# re-read flags, and must not lose the order. #master-load-order
_LOAD_ORDER_INDEX: dict[str, int] = {}


def set_load_order(names) -> None:
    """Record the active plugins, in load order (first loads first). None or an
    empty list clears it."""
    _LOAD_ORDER_INDEX.clear()
    for i, n in enumerate(names or ()):
        _LOAD_ORDER_INDEX.setdefault(str(n).lower(), i)


def _master_sort_key(name: str, data_dirs: "list[Path] | None") -> "tuple[int, int]":
    """Where a master goes in a master list: master-tier first, then by its real
    load-order position. A plugin the load order does not know keeps its place
    behind the known ones (the sort is stable)."""
    tier = 0 if _is_esm_tier_master(name, data_dirs) else 1
    return (tier, _LOAD_ORDER_INDEX.get(name.lower(), len(_LOAD_ORDER_INDEX)))


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
    Master-tier = a `.esm` / `.esl` file, or a plugin whose TES4 header carries the
    ESM flag (0x1): ESM-flagged `.esp` such as USSEP. The ESL flag (0x200) alone
    does NOT make a master: an ESL-flagged `.esp` ("ESPFE") loads where it sits
    among the regular plugins, so listing it ahead of the ESMs that load before it
    puts the master list out of load order (and xEdit's Sort Masters then rewrites
    it). #espfe-is-not-a-master (GitHub issue #27; this used to read 0x201)
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
                result = bool(flags & 0x1)  # 0x1 = ESM; 0x200 (ESL) alone is not a master
                _ESM_TIER_CACHE[low] = result  # cache only real on-disk reads
                return result
    return False  # not cached on failure (see _ESM_TIER_CACHE note above)



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




# ARMO subrecords with no FormID — adoptable from the winner without adding a
# new master. Covers the balance fields overhauls typically change.




# ----- Mod-defined non-body UBE coverage (the guard-helmet class) ----------
# Vanilla ARMA records get UBE races at runtime (RaceCompatibility /
# RaceDispatcher); overhaul-defined ARMAs (e.g. a re-armored guard helmet with
# 19 vanilla races, 0 UBE) still slip through.
# This pass closes the gap: scan the load order for non-body ARMOs whose
# winning armatures lack UBE coverage, mint a UBE-primary ARMA per missing
# armature (same non-body mesh — UBE only reshapes the torso), and override
# the winning ARMO to include it.

_HAIR_ONLY_SLOTS = 0x802          # biped slots 31 (Hair) | 41 (LongHair)
_ARMORHELMET_KW_LOW24 = 0x06C0EE  # Skyrim.esm KYWD ArmorHelmet
# Skyrim.esm KYWD ArmorMaterialElven: the id the headgear test read before
# #armorhelmet-kw-fix (its off-switch reads it again).
_ARMORMATERIALELVEN_KW_LOW24 = 0x06BBD9


def _armorhelmet_kw_fix() -> bool:
    r"""#armorhelmet-kw-fix (2026-09-25): does the hair-only headgear test read
    the ArmorHelmet keyword by its real id? Yes, by default.

    The test's keyword constant was 0x06BBD9, which in Skyrim.esm is the KYWD
    ArmorMaterialElven; ArmorHelmet is 0x06C0EE (both checked in Skyrim.esm).
    So a zero-value hair-slot helmet carrying ArmorHelmet was taken for a
    hairstyle and left off UBE actors, and a zero-value hair-only armour with
    the elven material keyword would have been taken for a helmet. Live: 7
    armours flip, all to headgear, none elven -- 6 non-playable creature-race
    helmets no rule mints anyway and 1 playable named helmet the wig rule
    already drew -- so the output is unchanged; with #coverage-wigs off it
    adds that one helmet. CBBE2UBE_NO_ARMORHELMET_KW_FIX=1 reads the old id
    again."""
    return not _flag("CBBE2UBE_NO_ARMORHELMET_KW_FIX", False)


def _hair_only_armo_is_equippable_headgear(payload, masters) -> bool:
    """True if a hair-slot-only ARMO is real headgear (hides hair, has gold
    value or ArmorHelmet keyword) rather than a cosmetic hairstyle ARMO
    (value 0, no armor keyword)."""
    helmet_kw = (_ARMORHELMET_KW_LOW24 if _armorhelmet_kw_fix()
                 else _ARMORMATERIALELVEN_KW_LOW24)
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"DATA" and len(d) >= 4:
            if struct.unpack_from("<I", d, 0)[0] > 0:
                return True            # has a gold value -> real equipment
        elif sig == b"KWDA" and len(d) >= 4:
            for i in range(len(d) // 4):
                fid = struct.unpack_from("<I", d, i * 4)[0]
                mi = fid >> 24
                if (fid & 0xFFFFFF) == helmet_kw and \
                        mi < len(masters) and masters[mi].lower() == "skyrim.esm":
                    return True        # ArmorHelmet keyword -> headgear armor
    return False


def _coverage_wigs() -> bool:
    r"""#coverage-wigs (2026-09-24): does a WIG -- a hair-slot-only armour the
    player can equip and that has a name -- get a UBE armature? Yes, by default
    (the user's call).

    A hair-only armour counted as headgear only with a gold value or the
    ArmorHelmet keyword, so a hair ARMO an NPC wears as a hairstyle was not
    extended to UBE. Wigs have neither, so 100 playable, named wigs (HDT-SMP hair
    packs) were invisible on UBE actors. Playable in the WINNING record and
    named now counts too. Each wig armature is minted UBE-primary with its own
    mesh, as a helmet's is -- including the hidden body collider SMP wigs carry
    (a BodySlide build of the 3BA body: measured 0.38u median / 1.05u p95 from
    the UBE body surface, against 0.27 / 0.68 on 3BA; the converter only copies
    it). CBBE2UBE_NO_COVERAGE_WIGS=1 leaves them uncovered again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_WIGS", False)


def _is_playable_named(aflags: int, payload: bytes) -> bool:
    """#coverage-wigs: the armour record the game loads is playable (no 0x4
    record flag) and carries a non-empty name (FULL: a string, or a lstring id
    in a localized plugin)."""
    if aflags & 0x4:
        return False
    for sig, d in esp.iter_subrecords(payload):
        if sig == b"FULL":
            return any(d)
    return False


def _wig_body_pass() -> bool:
    r"""#wig-body-pass (2026-09-25): does #coverage-wigs also reach a wig whose
    ARMOUR record carries a deforming slot (a stray calves slot beside the hair
    slots)? Yes, by default, while #coverage-wigs is on.

    An armour with any of slots 32/33/34/37/38 goes to the body pass, and the
    wig rule lived only in the non-body pass; the body pass mints an armature
    only with a converted mesh, which a wig never has. So a playable, named wig
    the author also flagged for the calves drew nothing on a UBE actor: the
    wearer went bald. Live: 1 armour, a follower's wig whose armour says
    31+38 while its one armature says 31/41 -- nothing draws slot 38.

    Judged per ARMATURE by its own BOD2, as the other body-pass rules are: an
    armature whose slots are hair slots only (31, 41), on a playable, named
    armour, when no other rule admitted any armature of that armour. It is
    minted as the non-body pass mints a wig -- its own mesh; a DefaultRace one
    for every UBE race, else (the race-list rule on) the UBE counterparts of
    the races it lists. A beast variant or an armature that already names a UBE
    race is never taken, and the third-party and dead-armature rules still
    apply. It pulls no other accessory of the armour along.
    CBBE2UBE_NO_WIG_BODY_PASS=1 leaves such a wig uncovered again."""
    return _coverage_wigs() and not _flag("CBBE2UBE_NO_WIG_BODY_PASS", False)


def _is_hair_only_armature(payload: bytes) -> bool:
    """#wig-body-pass: the armature's own BOD2 names slots, hair slots only."""
    for sig, d in esp.iter_subrecords(payload):
        if sig in (b"BOD2", b"BODT") and len(d) >= 4:
            s = struct.unpack_from("<I", d, 0)[0]
            return bool(s) and (s & _HAIR_ONLY_SLOTS) == s
    return False


def _wig_exclude_keep() -> bool:
    r"""#wig-exclude-keep (2026-09-25): does an excluded mod's wig that the body
    pass mints (#wig-body-pass) keep its coverage when the non-body pass would
    keep it? Yes, by default.

    #exclude-body-only (the user's call) withholds only an excluded mod's BODY
    pieces; `_excluded_piece_holds` keeps a non-body piece no other mod patches.
    The body pass withholds everything an excluded mod owns, so a wig whose
    armour also says a deforming slot was always withheld -- and named in the
    'no UBE armature from any mod' warning -- while the same wig on a hair-only
    armour was kept. A wig is a non-body piece: the body pass now asks the same
    keep test of the wig-only mint (never of an armour a deforming armature
    was admitted for). Nested under #exclude-body-only and #wig-body-pass.
    CBBE2UBE_NO_WIG_EXCLUDE_KEEP=1 withholds such a wig again."""
    return not _flag("CBBE2UBE_NO_WIG_EXCLUDE_KEEP", False)
# Slots that deform with the UBE body and need mesh conversion, not just race
# coverage: 32 body, 33 hands, 34 forearms, 37 feet, 38 calves.
_DEFORMING_SLOTS_MASK = (1 << 2) | (1 << 3) | (1 << 4) | (1 << 7) | (1 << 8)

# Skyrim.esm ArgonianRace, KhajiitRace and their vampire variants (low 24).
_BEAST_RACES_24 = frozenset({0x013740, 0x013745, 0x08883A, 0x088845})


def _coverage_beast_variant() -> bool:
    r"""#coverage-beast-variant (2026-09-24): is an armature whose primary race
    is DefaultRace but whose additional races are ONLY beast races left out of
    coverage? Yes, by default.

    Both coverage passes mint every DefaultRace-primary armature of an armour
    for all UBE races. A beast patch often adds its variant that way -- primary
    DefaultRace, additional Khajiit (+ vampire) only -- and no human draws it:
    the playable races carry no armor race, so an actor matches an armature only
    by a race it lists. Minted for UBE, the variant drew ON TOP of the human
    armature (two hairstyles on a wig, a beast-shaped gauntlet over the human
    one). An armature with no additional races is unchanged.
    CBBE2UBE_NO_COVERAGE_BEAST_VARIANT=1 mints them again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_BEAST_VARIANT", False)


# Skyrim.esm ManikinRace (low 24): the mannequins' race. Mannequins are actors
# and wear armour (25 NPC_ records live), but no playable or UBE-race actor has
# this race, so listing it beside a beast race cannot make a human draw the
# armature. A FIXED list, not the RACE Immobile flag: stationary enemy races
# (and one vanilla empty race) carry that flag too.
_NON_ACTOR_RACES_24 = frozenset({0x10760A})


def _beast_variant_non_actor() -> bool:
    r"""#beast-variant-non-actor (2026-09-24): does #coverage-beast-variant
    ignore the mannequin race (no playable or UBE-race actor has it) when it judges "every
    additional race is a beast race"? Yes, by default.

    A beast patch's variant often lists the Khajiit race AND the mannequin race,
    so a mannequin can display it. No playable or UBE-race actor has the
    mannequin race (the mannequins themselves do, and still display the item
    through the untouched source armature), but the rule counted it as a
    non-beast race, so the variant was minted for UBE
    and a UBE actor drew an armature no human draws. Live replay: 30 armatures
    (wig and earring variants), 30 links, none with a human armature. Only
    Skyrim.esm races in `_NON_ACTOR_RACES_24` are ignored; an armature listing
    ONLY the mannequin race is not a variant and is minted as before.
    CBBE2UBE_NO_BEAST_VARIANT_NON_ACTOR=1 counts the mannequin race again."""
    return not _flag("CBBE2UBE_NO_BEAST_VARIANT_NON_ACTOR", False)


def _exclude_body_only() -> bool:
    r"""#exclude-body-only (2026-09-25, the user's call): does an excluded mod
    lose only its BODY pieces' coverage? Yes, by default.

    #exclude-owned-coverage withheld every armour an excluded mod defines, in
    both passes. The user excludes a mod to keep our converted meshes off its
    body pieces; its helmet or eyeglasses, which no other mod patches, then drew
    nothing on UBE actors. Now the body pass still withholds everything the mod
    owns (bar a wig it mints alone, judged as below: #wig-exclude-keep), and
    the non-body pass withholds only what `_excluded_piece_holds`
    names; the rest is minted, drawing the model its armature names (no mesh
    is converted for the excluded mod; a shared path another mod's conversion
    covers draws that converted copy).
    CBBE2UBE_NO_EXCLUDE_BODY_ONLY=1 withholds all of it again. Nested:
    CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE=1 withholds nothing at all."""
    return not (_flag("CBBE2UBE_NO_EXCLUDE_OWNED_COVERAGE", False)
                or _flag("CBBE2UBE_NO_EXCLUDE_BODY_ONLY", False))


def _excluded_piece_holds(armo_abs, records, to_mint, arma_win, armo_slots,
                          ube_exists, probe) -> "str | None":
    r"""#exclude-body-only: why an excluded mod's armour stays withheld in the
    non-body pass, or None to mint it like any other armour (never drawing a
    converted copy of a mesh the excluded mod ships).

    `records`: [(plugin lowercase, armature identities, EditorID)] for every
    record of the armour in load order (the defining one and its overrides).
    `to_mint`: the armatures the pass would mint -- DefaultRace ones and those
    the race-list rule took, all of them. `probe`: an
    `auto_convert._ExclusionKeepProbe`, None when the modlist cannot be read.

    Withheld, in this order:
      * no modlist to check against (fail closed);
      * ANOTHER mod patches it, read without our patch reader: a SkyPatcher
        armor INI line adding addons names it, or an override of it in a
        loaded plugin adds an armature. The exclusion stays the fallback for a
        hand-made refit our reader does not recognise;
      * any armature to mint is conversion territory, by the planner's own
        test: a body slot (its BOD2, else the armour's), a cloak-named model,
        a body-candidate slot whose world mesh is body-fit or not a loose file
        we can read, or a model that would draw a converted or hand-made UBE
        mesh of a path the excluded mod itself ships."""
    from .auto_convert import (_BODY_SLOT_BITS, _BODY_CANDIDATE_SLOT_BITS,
                               _CLOAK_MESH_KEYWORDS)
    if probe is None:
        return "no modlist to check"
    edids = [e for _p, _a, e in records if e]
    mod = probe.named(armo_abs, edids)
    if mod is not None:
        return f"named by {mod}"
    base = [set(a) for p, a, _e in records if p == armo_abs[0]]
    if not base:
        return "no defining record"
    for p, a, _e in records:
        if p != armo_abs[0] and set(a) - base[0]:
            return f"{p} adds an armature"
    for x in to_mint:
        payload = arma_win[x][0]
        bits = _arma_slot_bits(payload) or armo_slots
        models = [(s, _model_path_text(d, "cp1252"))
                  for s, d in esp.iter_subrecords(payload) if s in ARMA_MODEL_SIGS]
        if bits & _BODY_SLOT_BITS:
            return "body slot"
        if any(k in m.replace("/", "\\").rsplit("\\", 1)[-1].lower()
               for _s, m in models for k in _CLOAK_MESH_KEYWORDS):
            return "cloak"
        if bits & _BODY_CANDIDATE_SLOT_BITS:
            world = [m for s, m in models if s in (b"MOD2", b"MOD3") and m]
            if not world or any(probe.body_fit(m) is not False for m in world):
                return "body-candidate slot, body-fit or unread mesh"
        if any(m and ube_exists(m) and probe.excluded_source(m, armo_abs[0])
               for _s, m in models):
            return "draws a converted mesh of its own"
    return None


def _held_for_another_patch(why) -> "str | None":
    """#exclude-body-only, for the report: the mod or plugin that an
    `_excluded_piece_holds` reason says patches the piece ("named by <mod>",
    "<plugin> adds an armature") -- the piece is left to that mod's patch, not
    left with no armature from any mod -- else None."""
    why = str(why or "")
    if why.startswith("named by "):
        return why[len("named by "):]
    if why.endswith(" adds an armature"):
        return why[:-len(" adds an armature")]
    return None


def _additional_races(v) -> list:
    """The plugin-qualified additional races (MODL) of winner-scan armature `v`."""
    payload, masters, own = v[0], v[1], v[2]
    return [_record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own)
            for s, d in esp.iter_subrecords(payload)
            if s == ARMA_ADDITIONAL_RACE_SIG and len(d) == 4]


def _is_non_actor_race(r) -> bool:
    """#beast-variant-non-actor: `r` (plugin, low 24) is a vanilla race no
    playable or UBE-race actor has (the mannequin race)."""
    p, lo = r
    return p == "skyrim.esm" and lo in _NON_ACTOR_RACES_24


def _lists_non_actor_race(v) -> bool:
    """#beast-variant-non-actor: does `v` list the mannequin race, which the beast test
    ignores (switch on)? For the report only."""
    return _beast_variant_non_actor() and any(
        _is_non_actor_race(r) for r in _additional_races(v))


def _is_beast_variant(v) -> bool:
    """#coverage-beast-variant: a winner-scan armature `v` (payload, masters,
    plugin, ...) lists additional races, and every one is a vanilla beast race.
    The mannequin race (no playable or UBE-race actor has it) is ignored when judging, and an
    armature listing only such races is not a variant. #beast-variant-non-actor"""
    races = _additional_races(v)
    actor = races
    if _beast_variant_non_actor():
        actor = [r for r in races if not _is_non_actor_race(r)]
    return bool(actor) and all(p == "skyrim.esm" and lo in _BEAST_RACES_24
                               for p, lo in actor)


def _coverage_race_subset() -> bool:
    r"""#coverage-race-subset (2026-09-25): does a DefaultRace armature that
    an author split by race keep to the UBE counterparts of its own races?
    Yes, by default.

    Both passes minted every DefaultRace-primary armature of an armour for all
    UBE races. Authors often split one piece into per-race armatures on the
    same slots -- one listing the human races, one Orc only, one the three
    elves, or one only a race of their own -- and an actor matches an armature
    only by a race it lists, so each vanilla race draws one of them. On a UBE
    actor every one drew at once: two identical circlets plus the elf circlet,
    the same helmet twice, a second robe made for a mod's own race.
    Now, within an armour's armatures that share a slot, when their human race
    lists do not overlap (`_race_subset_split`), each is minted for the UBE
    counterparts of the races it lists, and one listing only other races is
    not minted. Overlapping lists (pieces drawn together in the base game) and
    an armature with no sibling are unchanged.
    CBBE2UBE_NO_COVERAGE_RACE_SUBSET=1 mints them for all UBE races again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_RACE_SUBSET", False)


def _race_subset_dedup_agree() -> bool:
    r"""#race-subset-dedup-agree (2026-09-26): do the per-race split and the
    merge's render-identical link dedup agree on which armatures draw the
    same thing? Yes, by default.

    The merge drops a link when another link on the same armour draws the
    same meshes on the same slots with the same primary race
    (`_arma_dedup_identity`); it never looked at the rest of the race list.
    Before #coverage-race-subset every same-mesh sibling was minted for all
    UBE races, so dropping one lost nothing. The split gave each sibling its
    own races, and two things went wrong when an author's per-race armatures
    share one mesh:
      * the races no sibling lists went to every sibling, and siblings whose
        lists begin with different races got different primary races, so
        both links survived and those races drew the same mesh twice (live:
        an effect mesh on two armours, twice on the six UBE elf races);
      * siblings whose lists begin with the same race got one primary race,
        so the merge dropped one, and the races only it listed drew nothing
        for that armour (not live; a Nord copy and an Orc copy of one circlet
        would do it).
    Now the split takes same-mesh siblings as one armature (the first, for
    all their races), and the merge never drops a link that lists a race no
    kept link of its group lists.
    CBBE2UBE_NO_RACE_SUBSET_DEDUP_AGREE=1 restores both as they were."""
    return not _flag("CBBE2UBE_NO_RACE_SUBSET_DEDUP_AGREE", False)


def _arma_render_twin_key(v):
    """#race-subset-dedup-agree: what the merge's render-identical dedup
    compares, less the primary race, for a source armature (`arma_win`
    value): meshes, slots and alt-texture sets. An alt-texture set names its
    texture sets through the source plugin's masters, so equal bytes mean the
    same sets only within one plugin; then the plugin is part of the key."""
    ident = _arma_dedup_identity(v[0])[1:7]
    if ident[5]:
        return ident, tuple(str(m).lower() for m in v[1]), str(v[2]).lower()
    return ident, None, None


def _arma_race_set(arma_payload: bytes) -> frozenset:
    """Every race an armature lists (RNAM + MODL), as raw FormIDs of its own
    plugin -- comparable only between records of one plugin."""
    return frozenset(struct.unpack_from("<I", d)[0]
                     for s, d in esp.iter_subrecords(arma_payload)
                     if s in (b"RNAM", b"MODL") and len(d) >= 4)


def _guard_ube_races() -> bool:
    r"""#guard-ube-races (2026-09-26): does the merge guard of
    #race-subset-dedup-agree compare only the UBE races two render-identical
    links list? Yes, by default.

    The guard keeps a render-identical link that lists a race no kept link of
    its group lists, so no race loses the armour. It compared every race, and
    the per-source patches mint each of an author's armatures with the 16 UBE
    races BESIDE the author's own vanilla races: an author's Khajiit copy and
    Argonian copy of one glove, same meshes and slots, differ only in vanilla
    races. Both were kept, and every UBE race drew the glove twice (live on the
    full merge: 4 armours). Only UBE actors draw these links, so only a UBE
    race one copy has and the kept one lacks can go missing; the vanilla races
    draw the author's own armatures. Now the guard compares the UBE races
    (every race UBE_AllRace.esp defines) and folds copies that differ only
    in other races, as before the guard.
    CBBE2UBE_NO_GUARD_UBE_RACES=1 compares every race again."""
    return not _flag("CBBE2UBE_NO_GUARD_UBE_RACES", False)


def _arma_ube_race_set(arma_payload: bytes, masters, own_name) -> frozenset:
    """#guard-ube-races: the UBE races an armature lists, primary or
    additional, as low 24 bits: every race UBE_AllRace.esp defines -- the 16
    of `UBE_RACE_FIDS_24` and the plugin's custom races, whose actors draw the
    links too."""
    return frozenset(lo for pl, lo in _arma_race_list(arma_payload, masters, own_name)
                     if pl == "ube_allrace.esp")


def _race_subset_split(to_mint, arma_win, armo_slots) -> "tuple[set, dict, set]":
    """#coverage-race-subset: for one armour's armatures to mint -> (the ones
    not to mint, {armature: UBE races, UBE_RACE_FIDS_24 order} for the ones
    minted for fewer than every UBE race, the same-mesh siblings not minted
    because the first of them draws for all their races --
    #race-subset-dedup-agree). Only DefaultRace-primary armatures
    take part, grouped by overlapping slots (their BOD2, else `armo_slots`);
    a group changes only when one member lists a vanilla human race:
      * a member listing only other races (a mod's own race, an elder race,
        the mannequin race) is not minted: no human draws it;
      * when the human-listing members' UBE counterparts are disjoint and one
        is fewer than all, each is minted for its own; the races none of them
        claims go to the members listing no race at all (the DefaultRace
        default), else to every human-listing member -- what they draw today.
        A lone member claims none, so it keeps every race.
    A beast-only member (the mannequin race ignored, as there) is
    #coverage-beast-variant's, left as it is."""
    info = {}
    for x in to_mint:
        v = arma_win[x]
        if v[3] != _DEFAULT_RACE_ABS:
            continue
        races = _additional_races(v)
        actor = [a for a in races if not _is_non_actor_race(a)]
        human = frozenset(_ube_races_for_race_list(races)) if races else frozenset()
        if human:
            kind = "human"
        elif not races:
            kind = "default"
        elif actor and all(p == "skyrim.esm" and lo in _BEAST_RACES_24 for p, lo in actor):
            continue
        else:
            kind = "other"
        info[x] = (kind, human, _arma_slot_bits(v[0]) or armo_slots)
    group = {x: x for x in info}

    def _root(x):
        while group[x] != x:
            x = group[x]
        return x
    members = list(info)
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            if info[a][2] & info[b][2]:
                group[_root(a)] = _root(b)
    comps: dict = {}
    for x in members:
        comps.setdefault(_root(x), []).append(x)
    drop: set = set()
    narrow: dict = {}
    twins: set = set()
    agree = _race_subset_dedup_agree()
    all_ube = frozenset(UBE_RACE_FIDS_24)
    for comp in comps.values():
        hs = [x for x in comp if info[x][0] == "human"]
        if not hs:
            continue                      # no human sibling to draw instead
        drop.update(x for x in comp if info[x][0] == "other")
        sets = [info[x][1] for x in hs]
        overlap = sum(len(s) for s in sets) != len(frozenset().union(*sets))
        if overlap or all(s == all_ube for s in sets):
            continue                      # overlapping lists: drawn together
        free = all_ube - frozenset().union(*sets)
        ds = [x for x in comp if info[x][0] == "default"]
        own = {x: info[x][1] for x in hs}
        if agree:
            # #race-subset-dedup-agree: same-mesh siblings are one armature
            # (the first) for all their races; the rest render the same.
            first: dict = {}
            for x in hs:
                y = first.setdefault(_arma_render_twin_key(arma_win[x]), x)
                if y != x:
                    own[y] = own[y] | own.pop(x)
                    twins.add(x)
        got = {x: s | (frozenset() if ds else free) for x, s in own.items()}
        got.update({x: free for x in ds})
        for x, s in got.items():
            if not s:
                drop.add(x)
            elif s != all_ube:
                narrow[x] = [f for f in UBE_RACE_FIDS_24 if f in s]
    return drop, narrow, twins


class _RaceSubset:
    """#coverage-race-subset: one pass's use of `_race_subset_split` -- what
    each armour leaves to mint, and which UBE races each minted armature
    targets. A minted armature is one record shared by every armour that
    lists it, so it targets the union, and all races as soon as one armour
    mints it unsplit (a double draw there beats a missing one). `stats` is
    read from those final targets, so it names only what the ESP holds."""

    def __init__(self, arma_win):
        self.arma_win = arma_win
        self.on = _coverage_race_subset()
        self.armos: list = []      # (armo_abs, edid, armatures kept) split
        self.dropped: list = []    # armatures some armour left off
        self.twins: list = []      # same-mesh siblings some armour drew once
        self._races: dict = {}     # armature -> UBE races some armour needs
        self._full: set = set()    # armatures some armour needs for every race

    def split(self, armo_abs, edid, to_mint, armo_slots):
        """-> (`to_mint` without the armatures not to mint, {armature: UBE
        races} for those minted for fewer)."""
        if not self.on:
            return to_mint, {}
        drop, races, twins = _race_subset_split(to_mint, self.arma_win, armo_slots)
        if not (drop or races or twins):
            return to_mint, {}
        kept = [x for x in to_mint if x not in drop and x not in twins]
        self.armos.append((armo_abs, edid, kept))
        self.dropped.extend(x for x in to_mint if x in drop and x not in self.dropped)
        self.twins.extend(x for x in to_mint if x in twins and x not in self.twins)
        return kept, races

    def targeted(self, to_mint, races) -> None:
        for x in to_mint:
            if x in races:
                self._races.setdefault(x, set()).update(races[x])
            else:
                self._full.add(x)

    def races_for(self, x) -> "list[int] | None":
        """The UBE races minted armature `x` targets when fewer than every
        one, in UBE_RACE_FIDS_24 order; None = unchanged."""
        if x in self._full or x not in self._races:
            return None
        return [f for f in UBE_RACE_FIDS_24 if f in self._races[x]]

    def stats(self) -> dict:
        """From the FINAL targets, after the union: `race_subset` = the split
        armours at least one of whose minted armatures draws for fewer than
        every UBE race; `race_subset_dropped` = armatures some armour left off
        that no armour of this pass mints; `race_subset_minted` = every
        armature this pass mints, so the report can drop one the other pass
        mints; `race_subset_twins` = same-mesh siblings some armour left to
        the first of them that no armour of this pass mints
        (#race-subset-dedup-agree)."""
        n_all = len(UBE_RACE_FIDS_24)

        def _narrowed(x) -> bool:
            r = self.races_for(x)
            return r is not None and len(r) < n_all
        minted = self._full | set(self._races)
        return {"race_subset": [(a, e) for a, e, kept in self.armos
                                if any(_narrowed(x) for x in kept)],
                "race_subset_dropped": [f"{a[0]}|{a[1]:X}" for a in self.dropped
                                        if a not in minted],
                "race_subset_twins": [f"{a[0]}|{a[1]:X}" for a in self.twins
                                      if a not in minted],
                "race_subset_minted": frozenset(
                    f"{a[0]}|{a[1]:X}" for a in minted) if self.on else frozenset()}


def _coverage_dead_armature() -> bool:
    r"""#coverage-dead-armature (2026-09-25): is an armature none of whose
    meshes exists anywhere left unminted? Yes, by default.

    Both passes minted every armature the other rules admitted, whether or not
    its meshes exist. A hands/feet armature is admitted by its slot alone, and
    a non-body one keeps its source mesh; one whose every named mesh is missing
    from the whole modlist draws nothing for anyone -- the source armature
    included -- so ours only added a link that draws nothing. Live: about 40
    source armatures (city-guard boots and gauntlets, shields, amulets and
    accessories of mods whose meshes are not installed). Judged last, after
    every other rule, so their counts are unchanged; an armature naming no mesh
    at all (a slot placeholder that hides a body part) is never dead.
    CBBE2UBE_NO_COVERAGE_DEAD_ARMATURE=1 mints them again."""
    return not _flag("CBBE2UBE_NO_COVERAGE_DEAD_ARMATURE", False)


_ARMA_MODEL_SIGS = (b"MOD2", b"MOD3", b"MOD4", b"MOD5")


def _weight_siblings(model: str) -> "list[str]":
    r"""`model` and its weight siblings: `X.nif` -> `X.nif`, `X_0.nif`,
    `X_1.nif`; `X_0.nif` or `X_1.nif` -> itself, the other one and `X.nif`.
    A path not ending `.nif` comes back alone."""
    if not model.lower().endswith(".nif"):
        return [model]
    base = model[:-4]
    if base.endswith(("_0", "_1")):
        base = base[:-2]
    return list(dict.fromkeys([model, base + ".nif", base + "_0.nif",
                               base + "_1.nif"]))


def _dead_slot_draws(standin, female_mesh_exists):
    r"""#coverage-dead-armature with #coverage-female-standin: would the
    minted copy of an armature still draw something where its own female slot
    is dead? `draws(payload)` -> True when a named MOD3 (after MOD2) or MOD5
    (after MOD4) that `female_mesh_exists` calls dead would be filled by
    `rebuild_arma_payload` with the vanilla stand-in (`standin`: the pass's
    own `_female_standin_resolver`, keyed on the male path whether or not that
    exists), or when the male path it pairs with exists (the stand-in rule's
    "male as it is" branch for a non-body piece; asked of a body piece too --
    the lenient side, minted as before). None when the stand-in rule is not in
    play (switched off, or no lookup): the rebuild then draws nothing instead."""
    if standin is None or female_mesh_exists is None:
        return None

    def draws(payload: bytes) -> bool:
        male = {b"MOD3": "", b"MOD5": ""}
        for sig, d in esp.iter_subrecords(payload):
            p = _model_path_text(d, "cp1252")
            if sig in (b"MOD2", b"MOD4"):
                male[b"MOD3" if sig == b"MOD2" else b"MOD5"] = p
            elif sig in male and p and not female_mesh_exists(p):
                src = male[sig]
                if standin(sig.decode(), src) is not None:
                    return True
                if src and female_mesh_exists(src):
                    return True
        return False
    return draws


def _dead_armature_judge(arma_win: dict, crp: "set[str]", *, mesh_exists,
                         ube_twin_exists, draws_instead=None):
    r"""#coverage-dead-armature: `dead(x)` -> is winner-scan armature `x` one
    whose meshes exist nowhere? Memoised per armature.

    Dead: it names at least one non-empty MOD2..MOD5 path and none is alive.
    A path is alive when this run converted it (`crp`, a `meshes\` prefix
    taken off), a third-party mod ships its `!UBE\` twin (`ube_twin_exists`,
    None = no twin is known), or it or a weight sibling (`_weight_siblings`)
    exists where the game reads meshes (`mesh_exists`: loose, overwrite, game
    Data, any archive but voice/sound/facegen). That lookup lists every archive
    in the folders, not only those the game loads, so a mesh only in an
    inactive plugin's archive counts as alive -- the lenient side: such an
    armature is minted as before, never dropped for a mesh that may load.
    Nor is one dead whose minted copy draws a mesh the armature does not name
    (`draws_instead(payload)`, `_dead_slot_draws`: a dead female slot filled
    with the vanilla stand-in; None = nothing is drawn instead)."""
    memo: dict = {}

    def alive(p: str) -> bool:
        if _converted_model_exists(p, crp, strip_meshes=True):
            return True
        if ube_twin_exists is not None and ube_twin_exists(p):
            return True
        return any(mesh_exists(s) for s in _weight_siblings(p))

    def dead(x) -> bool:
        if x not in memo:
            models = [_model_path_text(d, "cp1252").strip()
                      for s, d in esp.iter_subrecords(arma_win[x][0])
                      if s in _ARMA_MODEL_SIGS]
            models = [p for p in models if p]
            memo[x] = bool(models) and not any(alive(p) for p in models)
            if memo[x] and draws_instead is not None:
                memo[x] = not draws_instead(arma_win[x][0])
        return memo[x]
    return dead


def _drop_dead(to_mint: list, dead, skipped: list) -> list:
    """#coverage-dead-armature: `to_mint` without the armatures `dead` calls
    dead; each is recorded once in `skipped` (one is often shared by many
    armours)."""
    gone = [x for x in to_mint if dead(x)]
    for x in gone:
        if x not in skipped:
            skipped.append(x)
    return [x for x in to_mint if x not in gone]


def _coverage_third_party_drawn() -> bool:
    r"""#coverage-third-party-drawn (2026-09-25): do both coverage passes judge
    another mod's UBE armature on the WINNING armour record by what it draws,
    instead of skipping the whole armour when any armature names a UBE race?
    Yes, by default.

    The blanket skip, and the body pass's slot-32 exemption from it, drew the
    same mesh twice (their UBE patch and our armature over one file) and left
    an armour with nothing when the third-party mesh exists nowhere; the plugin
    half of `_third_party_ube_covered_armos` read our own un-loaded copies as
    third-party patches. Now an armature T counts as drawing when it names UBE
    races, its female world mesh is live in the game view, and -- for an
    ARMOUR with slot 32, 34 or 38, judged per armour -- that mesh is under
    `!UBE\`. Each armature S we would mint is drawn when such a T draws the
    same file (`!UBE\`, `.nif`, `_0/_1` aside), else when the one unused T
    whose slots equal S's is claimed by no other armature (#r9-fallback-safe);
    T's races are subtracted, so S is minted for the UBE races no T draws.
    Shares its switch with #root-plugin-index (`paths`).
    CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN=1 restores the blanket skip, the
    slot-32 exemption, both exclusion halves and the recursive plugin index."""
    return not _flag("CBBE2UBE_NO_COVERAGE_THIRD_PARTY_DRAWN", False)


def _coverage_keep_better_first_person() -> bool:
    r"""#coverage-keep-better-first-person (2026-09-25): under
    #coverage-third-party-drawn, is our armature S still minted when its female
    first-person mesh (MOD5) was converted but the third-party armature that
    draws its world mesh has a first-person mesh that is not under `!UBE\` --
    an unconverted CBBE one, or none at all? Yes, by default: skipping ours
    would leave the player's own arms with only the CBBE mesh or nothing. Live:
    2 armours (both draw one world file twice, as before).
    CBBE2UBE_NO_COVERAGE_KEEP_BETTER_FIRST_PERSON=1 skips ours there too."""
    return not _flag("CBBE2UBE_NO_COVERAGE_KEEP_BETTER_FIRST_PERSON", False)


_TPD_UBE_RACES = frozenset(UBE_RACE_FIDS_24)
_TPD_BODYLIKE = (1 << 2) | (1 << 4) | (1 << 8)      # slots 32, 34, 38


def _tpd_facts(v) -> tuple:
    r"""#coverage-third-party-drawn: (UBE races it names, female world mesh
    key -- MOD3, else MOD2 --, BOD2 slots, female first-person mesh key) of a
    winner-scan armature `v`. Races are UBE_AllRace.esp races of
    `UBE_RACE_FIDS_24`, primary or additional; keys are `_model_key`s."""
    payload, masters, own = v[0], v[1], v[2]
    ube, mods, slots = set(), {}, 0
    for s, d in esp.iter_subrecords(payload):
        if (s == ARMA_ADDITIONAL_RACE_SIG and len(d) == 4) or (
                s == b"RNAM" and len(d) >= 4):
            a = _record_abs_fid(struct.unpack_from("<I", d, 0)[0], masters, own)
            if a[0] == "ube_allrace.esp" and a[1] in _TPD_UBE_RACES:
                ube.add(a[1])
        elif s in (b"MOD2", b"MOD3", b"MOD5"):
            mods[s] = _model_key(_model_path_text(d, "cp1252"))
        elif s in (b"BOD2", b"BODT") and len(d) >= 4:
            slots = struct.unpack_from("<I", d, 0)[0]
    return (ube, mods.get(b"MOD3") or mods.get(b"MOD2") or "", slots,
            mods.get(b"MOD5", ""))


def _tpd_mesh_base(key: str) -> str:
    r"""A model key with `!ube\`, `.nif` and a `_0`/`_1` weight suffix taken
    off: their UBE file and our source of it compare equal."""
    b = key[5:] if key.startswith("!ube\\") else key
    if b.endswith(".nif"):
        b = b[:-4]
    if b.endswith(("_0", "_1")):
        b = b[:-2]
    return b


def _third_party_drawn(armo_slots: int, winning, cand, arma_win, base_races,
                       *, mesh_live, conv_exists, keep_first_person: bool):
    r"""#coverage-third-party-drawn: which UBE races each armature of `cand`
    (the ones a pass would mint) still needs, given the third-party UBE
    armatures of the winning armour record `winning`.

    Returns (need, quals, kept): need = {S: [UBE races left, UBE_RACE_FIDS_24
    order]} -- empty = drawn by theirs; quals = the qualifying armatures;
    kept = [(S, T)] the first-person guard kept S against.

    A qualifier T names a UBE race, its female world mesh is live
    (`mesh_live`; None = cannot tell, so NOT shown live -- skipping ours is the
    dangerous direction, a double draw the safe one; `!UBE\` + a mesh this run
    converted is live either way) and, when the ARMOUR has slot 32/34/38, sits
    under `!UBE\`. S is drawn by T when they draw the same file; a T that draws
    the file of any other DefaultRace armature of the armour (one a guard
    dropped) is used up by it. The rest: by the one unused T whose BOD2 slots
    EQUAL S's, when no other armature without a twin has those slots -- any tie
    leaves S minted, so the rule errs toward a double draw, never toward an
    armour left with nothing; the result is the same in any list order.
    T's races come off S's `base_races(S)`.
    #coverage-keep-better-first-person: T does not draw S when S's MOD5 was
    converted and T's MOD5 is not under `!UBE\` (empty included)."""
    def _live(world: str) -> bool:
        if world.startswith("!ube\\") and conv_exists(world[5:]):
            return True
        return mesh_live is not None and bool(mesh_live(world))

    quals = []
    for x, v in winning:
        if not v[4]:
            continue
        ube, world, sl, mod5 = _tpd_facts(v)
        if not ube or not world or not _live(world):
            continue
        if (armo_slots & _TPD_BODYLIKE) and not world.startswith("!ube\\"):
            continue
        quals.append((x, world, sl or armo_slots, mod5, ube))
    need = {x: list(base_races(x)) for x in cand}
    if not quals:
        return need, quals, []
    # Every armature a qualifier may be the UBE version of: ours to mint, and
    # every armature of the armour that names no UBE race, whatever its primary
    # (a race-list one too) -- one a guard dropped before the split included,
    # so its twin is used up by it. A wider pool can only mint more.
    _qids = {q[0] for q in quals}
    pool = list(dict.fromkeys(list(cand) + [
        x for x, v in winning if not v[4] and x not in _qids]))
    facts = {x: _tpd_facts(arma_win[x]) for x in pool}
    got: dict = {x: set() for x in cand}
    used: set = set()
    matched: set = set()
    kept: list = []

    def _blocked(x, q) -> bool:
        s5 = facts[x][3]
        return (keep_first_person and bool(s5) and not s5.startswith("!ube\\")
                and conv_exists(s5) and not q[3].startswith("!ube\\"))

    for x in pool:
        sw = facts[x][1]
        for q in quals:
            if q[0] == x or (sw and _tpd_mesh_base(q[1]) == _tpd_mesh_base(sw)):
                used.add(q[0])
                matched.add(x)
                if x not in got:
                    continue               # not ours to mint: only used up
                if _blocked(x, q):
                    kept.append((x, q[0]))
                    continue
                got[x] |= q[4]

    def _same(q, y) -> bool:
        return q[2] == (facts[y][2] or armo_slots)

    # The rest: S is drawn by the one unused qualifier with EXACTLY S's slots,
    # when no other armature of the pool without a twin has those slots too --
    # a tie either way is no answer, so S is minted (a double draw at worst).
    free = [q for q in quals if q[0] not in used]
    open_ = [y for y in pool if (not got[y] if y in got else y not in matched)]
    for x in cand:
        left = [r for r in need[x] if r not in got[x]]
        if left and not got[x]:
            mine = [q for q in free if _same(q, x)]
            if (len(mine) == 1 and not _blocked(x, mine[0])
                    and [y for y in open_ if _same(mine[0], y)] == [x]):
                got[x] |= mine[0][4]
                left = [r for r in need[x] if r not in got[x]]
        need[x] = left
    return need, quals, kept


class _ThirdPartyDrawn:
    """#coverage-third-party-drawn: one pass's use of `_third_party_drawn` --
    what it leaves to mint, and the report and race-subset bookkeeping."""

    def __init__(self, arma_win, mesh_live, conv_exists):
        self.arma_win = arma_win
        self.mesh_live = mesh_live
        self.conv_exists = conv_exists
        self.keep_fp = _coverage_keep_better_first_person()
        self.drawn: list = []      # (armo_abs, edid): every armature drawn by theirs
        self.partial: list = []    # (armo_abs, edid): some armatures or races left
        self.kept: list = []       # (armo_abs, edid, S) kept by the first-person guard
        self._races: dict = {}     # S -> UBE races some target needs it for
        self._full: set = set()    # S some target needs for all its races

    def split(self, armo_abs, edid, slots, winning, to_mint, listed,
              record: bool = True):
        """-> (the armatures of `to_mint` still to mint, {S: UBE races} for
        those minted for fewer races than they would target). `record`: name
        the armour in the report (an armour withheld for --exclude-mods is
        named only when theirs draws all of it)."""
        def base(x):
            return listed.get(x) or UBE_RACE_FIDS_24
        need, _quals, kept = _third_party_drawn(
            slots, winning, to_mint, self.arma_win, base,
            mesh_live=self.mesh_live, conv_exists=self.conv_exists,
            keep_first_person=self.keep_fp)
        left = [x for x in to_mint if need[x]]
        fewer = {x: need[x] for x in left if len(need[x]) < len(base(x))}
        if not left:
            self.drawn.append((armo_abs, edid))
        elif record:
            for x in dict.fromkeys(x for x, _q in kept if x in left):
                self.kept.append((armo_abs, edid, f"{x[0]}|{x[1]:X}"))
            if len(left) < len(to_mint) or fewer:
                self.partial.append((armo_abs, edid))
        return left, fewer

    def targeted(self, to_mint, fewer) -> None:
        """An armour is targeted with `to_mint`: remember which races each
        armature is needed for. A minted armature is one record shared by every
        armour that lists it, so it targets the union -- all races as soon as
        one armour needs them all (a double draw there beats a missing one)."""
        for x in to_mint:
            if x in fewer:
                self._races.setdefault(x, set()).update(fewer[x])
            else:
                self._full.add(x)

    def races_for(self, x) -> "list[int] | None":
        """The UBE races minted armature `x` targets when fewer than usual,
        in UBE_RACE_FIDS_24 order; None = unchanged."""
        if x in self._full or x not in self._races:
            return None
        return [f for f in UBE_RACE_FIDS_24 if f in self._races[x]]

    def stats(self) -> dict:
        return {"third_party_drawn": self.drawn,
                "third_party_partial": self.partial,
                "third_party_kept_first_person": self.kept}


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


# ----- #coverage-human-race-list ---------------------------------------------
_DEFAULT_RACE_ABS = ("skyrim.esm", _DEFAULT_RACE_LOW24)
_ARMO_NONPLAYABLE_FLAG = 0x00000004     # ARMO record flag: not playable


def _arma_race_list(payload, masters, own_name) -> "list[tuple[str, int]]":
    """Every race an armature names -- its primary (RNAM) and its additional
    races (MODL) -- as (defining plugin lowercase, low 24 bits)."""
    out = []
    for s, d in esp.iter_subrecords(payload):
        if (s == b"RNAM" and len(d) >= 4) or (
                s == ARMA_ADDITIONAL_RACE_SIG and len(d) == 4):
            out.append(_record_abs_fid(struct.unpack_from("<I", d, 0)[0],
                                       masters, own_name))
    return out


def _ube_races_for_race_list(races) -> "list[int]":
    """The UBE races (UBE_AllRace.esp, low 24 bits, UBE_RACE_FIDS_24 order) a
    coverage armature made from an armature with these races targets: every
    one when DefaultRace is among them -- what any coverage armature gets --
    else the UBE counterpart of each vanilla human/mer race listed, so an
    armature drawn only for Wood Elves stays Wood-Elf-only on UBE. Empty when
    it names no human race (a beast or custom race alone): not minted."""
    if _DEFAULT_RACE_ABS in races:
        return list(UBE_RACE_FIDS_24)
    got = {UBE_RACE_FOR_VANILLA_24[low] for pl, low in races
           if pl == "skyrim.esm" and low in UBE_RACE_FOR_VANILLA_24}
    return [f for f in UBE_RACE_FIDS_24 if f in got]


def _effect_world_mesh(payload: bytes) -> bool:
    r"""Is the female WORLD mesh an armature draws (MOD3, else MOD2) an effect,
    or none at all? Under `effects\`, or a file whose name starts `fx` -- a
    glow, a veil, a ball of light -- is an effect, not something worn."""
    mod2 = mod3 = ""
    for s, d in esp.iter_subrecords(payload):
        if s == b"MOD2":
            mod2 = _model_path_text(d, "cp1252")
        elif s == b"MOD3":
            mod3 = _model_path_text(d, "cp1252")
    p = (mod3.strip() or mod2.strip()).replace("/", "\\").lstrip("\\").lower()
    if p.startswith("meshes\\"):
        p = p[len("meshes\\"):]
    if not p:
        return True
    return p.startswith("effects\\") or p.rsplit("\\", 1)[-1].startswith("fx")


def _collect_skins(pe, masters, own_name, into: set) -> None:
    """Add every form a RACE or NPC_ record of this plugin names as its skin
    (WNAM) -- winning or not, the way `_npc_worn_armos` counts them -- to
    `into`, as (defining plugin lowercase, low 24 bits). A skin is the body,
    never an armour worn over it."""
    for label in (b"RACE", b"NPC_"):
        g = pe.group(label)
        if not g:
            continue
        for r in g.records:
            if b"WNAM" not in r.payload:
                continue                 # cheap skip: most NPCs name none
            for s, d in esp.iter_subrecords(r.payload):
                if s == b"WNAM" and len(d) == 4:
                    into.add(_record_abs_fid(struct.unpack("<I", d)[0],
                                             masters, own_name))


def _race_list_admits(armo_abs, aflags, winning, *, worn, skins,
                      arma_ok) -> dict:
    """#coverage-human-race-list: for an armour the DefaultRace rule admitted
    no armature of, the armatures the race-list rule admits -> the UBE races
    each one targets (`_ube_races_for_race_list`), in `winning` order.

    Nothing when the WINNING armour record is not playable and no NPC wears it
    (`worn`: the identities `_npc_worn_armos` returns; None = none known), or
    when it is a race's or an NPC's skin (`skins`). An armature with another
    primary race is taken when `arma_ok(v)` passes it (the pass's own test --
    the body pass's converted mesh), its world mesh is no effect, and it names
    DefaultRace or a vanilla human/mer race."""
    if (aflags & _ARMO_NONPLAYABLE_FLAG) and not (worn and armo_abs in worn):
        return {}
    if armo_abs in skins:
        return {}
    out: dict = {}
    for x, v in winning:
        # An armature that already names a UBE race is someone's UBE version:
        # never mint a second one over it (review 2026-09-24).
        if v[4] or v[3] == _DEFAULT_RACE_ABS or not arma_ok(v):
            continue
        if _effect_world_mesh(v[0]):
            continue
        ube = _ube_races_for_race_list(_arma_race_list(v[0], v[1], v[2]))
        if ube:
            out[x] = ube
    return out


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


def _converted_model_exists(model_path: str, crp: "set[str]",
                            strip_meshes: bool = False) -> bool:
    """Does a CONVERTED `!UBE\\` mesh exist for this model path?

    Decides whether a coverage ARMA points at the converted mesh or keeps the source
    one. Getting it wrong is not cosmetic: a blanket keep-source once left every such
    piece wearing the un-converted mesh on the UBE body, i.e. distorted or invisible
    in game (`#mnb-converted-redirect`).

    Was defined identically inside both `generate_modded_nonbody_ube_coverage_patch`
    and `generate_modded_body_ube_coverage_patch`, each closing over its own `crp`.
    The two coverage generators must normalise a path the same way or they disagree
    about which pieces got converted -- so the normalisation lives in one place.

    `strip_meshes` (#twin-path-strip-meshes): a model spelt `meshes\\X` is asked
    as `X`, the same path the twin lookup asks and the writer then writes."""
    if not model_path:
        return False
    if strip_meshes:
        model_path = _strip_meshes_prefix(model_path)
    return model_path.replace("\\", "/").lstrip("/").lower() in crp


def _is_vanilla_plugin(name: str) -> bool:
    """A game master (Skyrim, Update, the three DLC) or a Creation Club master."""
    n = (name or "").lower()
    return (n in {m.lower() for m in VANILLA_DLC_MASTERS}
            or (n.startswith("cc") and n.endswith((".esl", ".esm"))))


def _model_key(path: str) -> str:
    r"""A model path as one key: lower case, backslashes, no `meshes\`."""
    p = (path or "").replace("/", "\\").strip().lstrip("\\").lower()
    return p[7:] if p.startswith("meshes\\") else p


def _female_standin_resolver(arma_win: dict, ube_exists):
    r"""#coverage-female-standin: the stand-in for a dead female slot --
    `standin(sig, male_path)` -> the converted vanilla female model a vanilla
    armature pairs with `male_path`, or None.

    Built once per pass from the winner scan (`arma_win`: abs -> (payload,
    masters, plugin, rnam_abs, ...)): armatures a game master or a Creation Club
    master DEFINES, whose winning record is DefaultRace-primary, keyed by their
    male path -- MOD2 -> MOD3 and MOD4 -> MOD5 separately. A male path paired
    with two different female paths (a vanilla torso with two looks) is
    ambiguous and gets none, and so does a counterpart that was not converted
    (`ube_exists`): pointing at an unconverted CBBE mesh would clip."""
    default = ("skyrim.esm", _DEFAULT_RACE_LOW24)
    pairs: dict = {"MOD3": {}, "MOD5": {}}
    for a, v in arma_win.items():
        if not _is_vanilla_plugin(a[0]) or v[3] != default:
            continue
        md = {s: _model_path_text(d, "cp1252").strip()
              for s, d in esp.iter_subrecords(v[0]) if s in ARMA_MODEL_SIGS}
        for male, fem in ((b"MOD2", b"MOD3"), (b"MOD4", b"MOD5")):
            if md.get(male) and md.get(fem):
                spelt = _strip_meshes_prefix(md[fem].replace("/", "\\").lstrip("\\"))
                (pairs[fem.decode()].setdefault(_model_key(md[male]), {})
                 .setdefault(_model_key(md[fem]), set()).add(spelt))

    def standin(sig: str, male_path: str) -> "str | None":
        found = pairs.get(sig, {}).get(_model_key(male_path))
        if not found or len(found) != 1:
            return None
        spelt = sorted(next(iter(found.values())))[0]
        return spelt if ube_exists(spelt) else None
    return standin


def _file_declined(declined: list, arma_abs, logs: dict, mesh_exists,
                   payload: "bytes | None" = None, armo_slots: int = 0) -> None:
    """Sort one armature's `rebuild_arma_payload` declined_log into a coverage
    pass's lists (#coverage-female-guard, #coverage-female-standin). A dead slot
    left on its path is tagged with whether its male mesh exists at all, and --
    when it does and the source `payload` is given -- with why that male mesh
    was not drawn instead (`_dead_kept_why`, for the report)."""
    arma = f"{arma_abs[0]}|{arma_abs[1]:X}"
    for d in declined:
        e = {"arma": arma, **d}
        if "standin" in d:
            logs["standin"].append(e)
        elif "male_as_is" in d:
            logs["as_is"].append(e)
        elif "dead_kept" in d:
            e["male_live"] = bool(d.get("male")) and bool(
                mesh_exists is not None and mesh_exists(d["male"]))
            if e["male_live"] and payload is not None:
                e["why"] = _dead_kept_why(payload, armo_slots, mesh_exists, d["male"])
            logs["dead_kept"].append(e)
        else:
            (logs["dead"] if "dead" in d else logs["kept"]).append(e)


def _nonbody_male_as_is(payload: bytes, armo_slots: int, ube_exists,
                        mesh_exists) -> "callable[[str], bool] | None":
    r"""#coverage-female-standin, the "else keep the male mesh" branch: may a
    dead female slot of this armature draw its unconverted male path as it is?
    Only a NON-BODY armature: its slots (its own BOD2, else its armour's) name
    none of the conversion's body slots, no model is cloak-named -- the
    selection's own sets, read from it -- and the male mesh the game loads is
    not skinned to a body-fit bone. Fails closed: no slots, no lookup, or a male
    mesh that cannot be read keeps the dead path. None when the armature is
    body territory. `mesh_exists.body_fit(model)` reads the mesh
    (`auto_convert._mesh_exists_anywhere`)."""
    from .auto_convert import _BODY_SLOT_BITS, _CLOAK_MESH_KEYWORDS
    slots = _arma_slot_bits(payload) or armo_slots
    body_fit = getattr(mesh_exists, "body_fit", None)
    if not slots or (slots & _BODY_SLOT_BITS) or body_fit is None:
        return None
    for sig, d in esp.iter_subrecords(payload):
        if sig in ARMA_MODEL_SIGS:
            base = _model_path_text(d, "cp1252")
            base = base.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            if any(k in base for k in _CLOAK_MESH_KEYWORDS):
                return None

    def as_is(male_path: str) -> bool:
        return (bool(male_path) and not ube_exists(male_path)
                and mesh_exists(male_path) and body_fit(male_path) is False)
    return as_is


def _dead_kept_why(payload: bytes, armo_slots: int, mesh_exists,
                   male_path: str) -> str:
    r"""#coverage-female-standin, for the report only: why a dead female slot
    whose male mesh EXISTS was left dead instead of drawing it -- the
    `_nonbody_male_as_is` test that refused, in its order:
      "body"   its slots (its BOD2, else its armour's) name one of the
               conversion's body slots (hands and feet included), or the male
               mesh is skinned to a body-fit bone;
      "cloak"  a model is cloak-named;
      "unread" no slots to judge, a lookup that cannot read meshes, or a male
               mesh that could not be read -- anything the test failed closed on.
    Kept apart from the test itself so the rule's lines stay as they were; the
    tests pin each reason to the case the rule refuses."""
    from .auto_convert import _BODY_SLOT_BITS, _CLOAK_MESH_KEYWORDS
    bits = _arma_slot_bits(payload) or armo_slots
    reader = getattr(mesh_exists, "body_fit", None)
    if bits & _BODY_SLOT_BITS:
        return "body"
    if not bits or reader is None:
        return "unread"
    names = [_model_path_text(d, "cp1252").replace("/", "\\")
             .rsplit("\\", 1)[-1].lower()
             for sig, d in esp.iter_subrecords(payload) if sig in ARMA_MODEL_SIGS]
    if any(k in n for n in names for k in _CLOAK_MESH_KEYWORDS):
        return "cloak"
    fit = reader(male_path)
    return "body" if fit is True else "unread"


def _ube_twin_slots(payload: bytes, crp: "set[str]", ube_twin_exists,
                    strip_meshes: bool = False) -> list:
    r"""#coverage-ube-twin: the model slots of a SOURCE armature that the rebuild
    points at a hand-made `!UBE\` mesh another mod ships -- no converted twin in
    our output, one loose in a third-party mod. Read from the source, so a path
    that already starts `!UBE\` is never mistaken for one. Each is
    {"slot", "path", "mod"}; `ube_twin_exists` returns the supplying mod.

    "path" is the string the rebuild writes -- the piece validator whitelists
    exactly it -- so with `strip_meshes` a `meshes\` source is recorded as
    `rebuild_arma_payload(strip_meshes_prefix=True)` writes it: `!UBE\X`, not
    `!UBE\meshes\X`. #twin-path-strip-meshes"""
    out = []
    for sig, d in esp.iter_subrecords(payload):
        if sig not in ARMA_MODEL_SIGS:
            continue
        p = _model_path_text(d, "utf-8")
        if not p or _converted_model_exists(p, crp, strip_meshes=strip_meshes):
            continue
        mod = ube_twin_exists(p)
        if mod:
            _rel = _strip_meshes_prefix(p) if strip_meshes else p
            out.append({"slot": sig.decode(), "path": "!UBE\\" + _rel,
                        "mod": mod if isinstance(mod, str) else ""})
    return out


def _outside_paths_predicate(paths) -> "callable[[str], bool] | None":
    r"""The piece validator's `mesh_resolves` for the `!UBE\` paths a coverage
    pass pointed OUTSIDE our output after checking they resolve (a UBE body
    part, a hand-made twin). None when there are none -- the validator then
    behaves exactly as before."""
    known = frozenset(p.replace("/", "\\").lower() for p in paths if p)
    if not known:
        return None
    return lambda p: (p or "").replace("/", "\\").lower() in known


def _esl_chunk_dedup() -> bool:
    r"""#esl-chunk-dedup (2026-09-25): do coverage targets that share a minted
    armature go into the SAME ESL piece? Yes, by default.

    `_emit_coverage_pieces` mints, in every piece, each armature an armour in
    that piece adds. Filling the pieces target by target in scan order put two
    armours that share an armature into different pieces often enough that the
    armature was minted twice: measured on the live load order, 36 of the
    non-body coverage's 2,093 distinct armatures were minted in both of its
    pieces (2,129 records). The two copies carry the same content; the merge's
    record dedup cannot fold them because the first piece fills a whole
    Combined piece by itself, and ESL pieces never master each other.

    With this on, targets linked by a shared armature (directly or through a
    chain) form one group, and each group is placed whole into the first piece
    with room -- so each armature is minted once and every armour still gets
    exactly one line in one piece. Only a group needing more than `cap`
    armatures is split as before (the largest live group needs 85).

    Pieces never increase: whole groups cannot be split to top a piece up, so
    in some runs they need more pieces than the scan-order fill -- one more
    plugin to enable, to save a few duplicate records. The grouped fill is kept
    only when it needs no more pieces than the scan-order fill; otherwise the
    scan-order fill is used, duplicates and all. Live the two tie (2 non-body
    pieces either way) and the grouped fill is kept.
    CBBE2UBE_NO_ESL_CHUNK_DEDUP=1 restores the scan-order fill."""
    return not _flag("CBBE2UBE_NO_ESL_CHUNK_DEDUP", False)


def _chunk_targets_for_esl(targets, mint_rec, cap: int) -> "list[list]":
    """Group coverage targets into chunks, each minting <= `cap` DISTINCT armatures.

    Chunked by TARGET (ARMO), never by armature, so an ARMO's whole add-set stays in
    one piece and therefore yields ONE `filterByArmors` line. Splitting an ARMO across
    pieces would emit two lines for it, and whether SkyPatcher accumulates or the last
    wins is unverified -- the shipped INI currently has exactly one line per armor
    (9,913 lines / 9,913 distinct armors) and that invariant is worth keeping.

    Targets that share a minted armature are kept in one chunk (#esl-chunk-dedup),
    so the armature is minted once rather than once per chunk. A piece's armour
    could instead name an armature minted in another piece -- a SkyPatcher line may
    name several plugins -- but the merge folds each coverage piece into whichever
    Combined piece has room and resolves links within that piece only; keeping the
    group together needs no cross-piece reference at all.

    A group needing more than `cap` armatures is chunked in scan order on its own,
    and only its armatures can repeat. A single target needing more than `cap`
    becomes its own over-cap chunk -- it cannot be split without breaking the
    invariant above, and the caller downgrades just that piece.

    The grouped fill never costs a piece or a record: when it needs more chunks
    than the scan-order fill (whole groups leave a piece short that the
    scan-order fill tops up across a group), the scan-order fill is returned
    instead. At the same number of chunks the fill minting fewer records wins;
    a group over `cap` can make the grouped fill mint MORE, and then the
    scan-order fill is kept. A full tie keeps the grouped fill."""
    scan = _chunk_targets_in_scan_order(targets, mint_rec, cap)
    if not _esl_chunk_dedup():
        return scan
    grouped = _chunk_targets_grouped(targets, mint_rec, cap)
    if (len(grouped), _chunk_record_count(grouped, mint_rec)) > (
            len(scan), _chunk_record_count(scan, mint_rec)):
        return scan
    return grouped


def _chunk_record_count(chunks, mint_rec) -> int:
    """Armature records a chunking mints: each chunk mints every distinct
    armature its targets add, so an armature shared across chunks counts once
    per chunk."""
    return sum(len({a for _armo, _plugin, to_mint in chunk
                    for a in to_mint if a in mint_rec}) for chunk in chunks)


def _chunk_targets_grouped(targets, mint_rec, cap: int) -> "list[list]":
    """The #esl-chunk-dedup fill: targets that share a minted armature (directly
    or through a chain) form one group, and each group goes whole into the first
    chunk with room. `_chunk_targets_for_esl` keeps it only when it needs no more
    chunks than the scan-order fill."""
    # Union targets that mint a common armature. Groups are listed below in the
    # order their first target was scanned.
    parent = list(range(len(targets)))

    def _root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_user: dict = {}
    for i, (_armo, _plugin, to_mint) in enumerate(targets):
        for a in to_mint:
            if a not in mint_rec:
                continue
            j = first_user.setdefault(a, i)
            ri, rj = _root(i), _root(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
    groups: dict = {}
    for i in range(len(targets)):
        groups.setdefault(_root(i), []).append(i)

    def _keys(idx) -> set:
        return {a for i in idx for a in targets[i][2] if a in mint_rec}

    chunks: list = []           # target indices per chunk
    chunk_keys: list = []       # the distinct armatures each chunk mints
    for members in groups.values():
        keys = _keys(members)
        if not keys:
            # Mints nothing, so it emits no line and costs nothing: ride along.
            if chunks:
                chunks[-1].extend(members)
            else:
                chunks.append(list(members))
                chunk_keys.append(set())
            continue
        if len(keys) > cap:
            # Scan order keeps every target, in order: slice the indices to match.
            pos = 0
            for sub in _chunk_targets_in_scan_order(
                    [targets[i] for i in members], mint_rec, cap):
                idx = members[pos:pos + len(sub)]
                pos += len(sub)
                chunks.append(idx)
                chunk_keys.append(_keys(idx))
            continue
        for k, held in enumerate(chunk_keys):
            if len(held) + len(keys) <= cap:
                chunks[k].extend(members)
                held |= keys
                break
        else:
            chunks.append(list(members))
            chunk_keys.append(keys)
    # Scan order inside each chunk, so a run that fits one piece mints its records
    # in exactly the order it always has.
    return [[targets[i] for i in sorted(c)] for c in chunks]


def _chunk_targets_in_scan_order(targets, mint_rec, cap: int) -> "list[list]":
    """Fill chunks target by target in scan order, starting a new chunk when the
    next target's new armatures would pass `cap`. An armature shared by targets in
    two chunks is minted in both. The grouped fill uses it for a group too big
    for one chunk; `_chunk_targets_for_esl` returns it under
    CBBE2UBE_NO_ESL_CHUNK_DEDUP=1, and whenever grouping would need more chunks."""
    chunks: list = []
    cur: list = []
    cur_keys: set = set()
    for tgt in targets:
        _armo, _plugin, to_mint = tgt
        fresh = {a for a in to_mint if a in mint_rec} - cur_keys
        if cur and len(cur_keys) + len(fresh) > cap:
            chunks.append(cur)
            cur, cur_keys = [], set()
            fresh = {a for a in to_mint if a in mint_rec}
        cur.append(tgt)
        cur_keys |= fresh
    if cur:
        chunks.append(cur)
    return chunks


def _emit_coverage_pieces(
    out_path: Path,
    targets,
    mint_rec: dict,
    patch_masters,
    *,
    own_byte: int,
    author: str,
    description: str,
    ini_header: "list[str]",
    preserve_textures: bool = False,
    master_data_dirs=None,
    emit_sidecar: bool = False,
    cap: int = ESL_MAX_OWN_RECORDS,
    mesh_resolves: "callable[[str], bool] | None" = None,
) -> dict:
    """Write a coverage patch as ONE OR MORE ESL-sized pieces.

    Both coverage generators used to emit a single monolithic ESP. On a large pack
    each minted well over the 2048 own-record ESL limit (measured 2,116 and 2,104),
    and `_partition_patches_for_esl` bin-packs whole PATCHES -- it cannot split one.
    So every merged piece containing a coverage patch was forced to a full ESP, and
    the only reason one stayed light was that dedup happened to land it 4 records
    under the line. Emitting ESL-sized pieces here is what the splitting was for.

    Each piece restarts its own-FormID space at `ESL_OWN_FORMID_MIN`, carries its own
    pruned master list, and gets its own INI lines and sidecar, so it is independently
    ESL-clean and valid standalone.

    Piece names keep the `... UBE patch.esp` suffix (`<stem>2 UBE patch.esp`) because
    the merge collects patches with `*UBE patch.esp`; a name that breaks that glob is
    silently dropped from the Combined, which is invisible until armour goes missing
    in game."""
    stem, suffix = out_path.stem, (out_path.suffix or ".esp")
    parent = out_path.parent
    # `<stem>` here is e.g. "UBE_ModBody_Coverage UBE patch"; the index goes before
    # " UBE patch" so the suffix survives.
    base = stem.rpartition(" UBE patch")[0] or stem

    def _piece_path(i: int) -> Path:
        return out_path if i == 0 else parent / f"{base}{i + 1} UBE patch{suffix}"

    chunks = _chunk_targets_for_esl(targets, mint_rec, cap)
    # Drop pieces a previous, larger run left behind (N pieces -> fewer). Same hazard
    # `merge_patches_split._drop_stale_pieces` documents: an orphan keeps delivering
    # the PREVIOUS run's links because SkyPatcher applies every INI in the folder.
    keep = {_piece_path(i).name for i in range(len(chunks))}
    for f in parent.glob(f"{base}* UBE patch{suffix}"):
        if f.name in keep:
            continue
        mid = f.name[len(base):len(f.name) - len(f" UBE patch{suffix}")]
        if not mid.isdigit():
            continue                  # not one of our numbered pieces
        for victim in (f, Path(str(f) + ".skypatcher.json")):
            try:
                if victim.is_file():
                    victim.unlink()
            except OSError:
                pass

    ini_lines = list(ini_header)
    pieces, warnings, total_minted, all_esl = [], [], 0, True
    masters_count = 0
    for idx, chunk in enumerate(chunks):
        piece_path = _piece_path(idx)
        piece_name = piece_path.with_suffix(".esp").name
        # Hold the Record OBJECTS, not their FormIDs. `prune_unused_masters`
        # below drops unreferenced masters and remaps every record's master
        # byte IN PLACE, so an int captured here goes stale -- the same trap
        # the single-piece generator and the male-fallback sidecar document.
        # Reading `rec.formid` after the save is the only correct source, and
        # it feeds BOTH the INI and the sidecar so they cannot disagree.
        local_rec: dict = {}
        recs: list = []
        nid = ESL_OWN_FORMID_MIN
        for _armo, _plugin, to_mint in chunk:
            for a in to_mint:
                if a in local_rec or a not in mint_rec:
                    continue
                fid = (own_byte << 24) | nid
                nid += 1
                src = mint_rec[a]
                rec = esp.Record(
                    sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                    version_unk=0x002C, payload=src.payload)
                recs.append(rec)
                local_rec[a] = rec
        as_esl = len(recs) <= cap
        all_esl = all_esl and as_esl
        total_minted += len(recs)
        flags = TES4_FLAG_ESL if as_esl else 0
        piece = esp.ESP(header=esp.TES4Header(
            masters=list(patch_masters), author=author,
            description=(description if len(chunks) == 1 else
                         f"{description} (part {idx + 1}/{len(chunks)})"),
            flags=flags, version=1.7, num_records=0,
            next_object_id=max(ESL_OWN_FORMID_MIN, nid)), groups=[])
        if recs:
            piece.groups.append(esp.Group(label=b"ARMA", records=recs))
        prune_unused_masters(piece)
        if preserve_textures:
            resort_masters(piece, master_data_dirs=master_data_dirs)
        piece.save(piece_path)
        warnings.extend(validate_patch(piece_path, master_data_dirs=master_data_dirs,
                                       mesh_resolves=mesh_resolves))
        pieces.append(piece_name)
        masters_count = max(masters_count, len(piece.header.masters))

        # INI: post-prune fid, masked to 24 bits (SkyPatcher names the plugin
        # separately, so the master byte is not part of the line).
        for armo_abs, defining_plugin, to_mint in chunk:
            addons = [local_rec[a].formid for a in to_mint if a in local_rec]
            if not addons:
                continue
            adds = ",".join("{}|{:06X}".format(piece_name, (f & 0xFFFFFF))
                            for f in addons)
            ini_lines.append("filterByArmors={}|{:06X}:armorAddonsToAdd={}".format(
                defining_plugin, armo_abs[1], adds))

        if emit_sidecar:
            # FULL fid here, NOT masked: the merge keys on the record's real
            # FormID in this piece, so a stale master byte silently resolves to
            # nothing and the run emits ZERO links.
            import json as _json
            doc = []
            for armo_abs, defining_plugin, to_mint in chunk:
                adds = [{"fid": local_rec[a].formid, "src": [a[0], a[1]]}
                        for a in to_mint if a in local_rec]
                if adds:
                    doc.append({"armo": [defining_plugin, armo_abs[1]],
                                "adds": adds})
            sc = Path(str(piece_path) + ".skypatcher.json")
            try:
                if doc:
                    sc.write_text(_json.dumps(doc, indent=1), encoding="utf-8")
                elif sc.is_file():
                    sc.unlink()
            except OSError as _e:
                # NOT silent: that piece's armature links are lost at merge.
                import sys as _sys
                print(f"  WARN: coverage-piece skypatcher sidecar not written ({_e}) -- that piece's armature links are lost at merge",
                      file=_sys.stderr)

    return {"pieces": pieces, "ini_lines": ini_lines, "minted_armas": total_minted,
            "esl_flagged": all_esl, "masters": masters_count,
            "validation_warnings": warnings, "split_pieces": len(chunks)}


def generate_modded_nonbody_ube_coverage_patch(
    output_esp_path: "str | Path",
    ordered_plugin_paths: "list[Path]",
    *,
    converted_rel_paths: "set[str] | None" = None,
    ube_allrace_filename: str = "UBE_AllRace.esp",
    exclude_names: "set[str] | None" = None,
    exclude_armo_abs: "set[tuple[str, int]] | None" = None,
    master_data_dirs: "list[Path] | None" = None,
    cover_all: bool = False,
    emit_sidecar: bool = False,
    preserve_textures: bool = False,
    withheld_armo_abs: "set[tuple[str, int]] | None" = None,
    female_mesh_exists: "callable[[str], bool] | None" = None,
    ube_twin_exists: "callable[[str], str | None] | None" = None,
    npc_worn_armo_abs: "set[tuple[str, int]] | frozenset | None" = None,
    exclusion_probe=None,
    mesh_live: "callable[[str], bool] | None" = None,
    dead_mesh_exists: "callable[[str], bool] | None" = None,
    author: str = "cbbe-to-ube modded non-body UBE coverage",
    description: str = "UBE race coverage for mod-defined non-body armor",
) -> dict:
    """Emit a patch giving UBE-race coverage to non-body items whose load-order
    WINNING armatures (from ANY plugin) lack it. For each such ARMO, mint a
    UBE-primary ARMA per missing DefaultRace armature (same mesh) and override
    the ARMO to add it. ARMOs/ARMAs are read as load-order winners (last wins).
    `ordered_plugin_paths` must be in load order.

    cover_all=True (unified-coverage / primary role) covers NON-PLAYABLE non-body
    items too, mirroring the body pass's cover_all: NPC-only helmets/jewelry on a
    UBE-race NPC go invisible without it. The armature-level DefaultRace filter
    (below) is still the beast-race guard, so this stays crash-safe. Default False
    keeps today's playable-only fallback behavior. Returns a stats dict.

    `withheld_armo_abs` (#exclude-owned-coverage): ARMOs to leave alone although
    they pass every filter -- armour the user excluded. Tested last, so the
    `withheld` stat counts exactly the armours this left without our armature.
    #exclude-body-only: here only those `_excluded_piece_holds` names are left
    alone; the rest are minted (`exclusion_nonbody_kept`), with no mesh
    converted for the excluded mod.
    `exclusion_probe` answers its questions about the modlist; None = built
    from the active modlist the first time an excluded armour needs it.

    `ube_twin_exists` (#coverage-ube-twin, on by default): the third-party mod that
    ships a loose `!UBE\\<path>` for a model we did not convert, else None. A
    minted slot points there instead of at the source mesh.

    `npc_worn_armo_abs` (#coverage-human-race-list): the armour female NPCs
    wear or carry (`auto_convert._batch_npc_worn_armos`); a non-playable armour
    in it may be taken by the race-list rule. None = none is known to be worn.

    `mesh_live` (#coverage-third-party-drawn): is a mesh live in the game view
    (`auto_convert._game_view_mesh_resolver`)? Another mod's UBE armature draws
    only when its female world mesh is. None = cannot tell: only a mesh this
    run converted counts as live, so ours is minted rather than skipped.

    `dead_mesh_exists` (#coverage-dead-armature): does a mesh exist anywhere
    the game reads it (`auto_convert._mesh_exists_anywhere`)? An armature
    whose every named mesh is dead is not minted, judged after every other
    rule. None = cannot tell: every armature is minted, as before."""
    out_path = Path(output_esp_path)
    exclude = {n.lower() for n in (exclude_names or set())}
    DEFAULT_RACE = ("skyrim.esm", _DEFAULT_RACE_LOW24)
    _female_guard = _coverage_female_guard()
    _twin = _coverage_ube_twin() and ube_twin_exists is not None
    _strip = _twin_path_strip_meshes()   # #twin-path-strip-meshes
    _race_list = _coverage_human_race_list()
    withheld: list = []        # (armo_abs, edid) left alone for --exclude-mods
    # #exclude-body-only: an excluded mod's non-body armour minted with its own
    # mesh, and why each withheld one was held.
    _body_only = bool(withheld_armo_abs) and _exclude_body_only()
    nonbody_kept: list = []    # (armo_abs, edid)
    nonbody_held: list = []    # (armo_abs, edid, why)
    owned_records: dict = {}   # armo_abs -> [(plugin, armatures, edid)], load order
    _probe: list = [exclusion_probe, exclusion_probe is not None]  # [probe, built]
    female_kept: list = []     # female slots that kept their own mesh (guard)
    female_dead: list = []     # dead female paths: the male mesh stays (guard)
    # #coverage-female-standin: dead female paths that draw the vanilla female
    # counterpart, the male path as it is (non-body), or nothing at all.
    female_standin: list = []
    female_as_is: list = []
    female_dead_kept: list = []
    _flogs = {"kept": female_kept, "dead": female_dead, "standin": female_standin,
              "as_is": female_as_is, "dead_kept": female_dead_kept}
    _female_standin = _coverage_female_standin()
    twin_slots: list = []      # slots pointed at a hand-made UBE twin (#coverage-ube-twin)
    beast_skipped: list = []   # DefaultRace armatures listing only beast races (#coverage-beast-variant)
    beast_non_actor: list = []  # ... of which also list the mannequin race (#beast-variant-non-actor)
    _beast = _coverage_beast_variant()
    wigs_added: list = []      # (armo_abs, edid) wigs covered as headgear (#coverage-wigs)
    _wigs = _coverage_wigs()
    skins: set = set()         # any RACE/NPC_ WNAM (#coverage-human-race-list)
    race_list_ube: dict = {}   # arma_abs -> UBE races it targets (same)
    race_listed: list = []     # (armo_abs, edid) taken by the race-list rule
    dead_skipped: list = []    # armatures whose every mesh is dead (#coverage-dead-armature)
    dead_dropped: list = []    # (armo_abs, edid) left with nothing to mint by that

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
                if _body_only and a in withheld_armo_abs:
                    owned_records.setdefault(a, []).append(
                        (nm.lower(), tuple(arms), edid))
        if _race_list:
            _collect_skins(pe, m, nm, skins)

    plugin_case = {Path(p).name.lower(): Path(p).name
                   for p in ordered_plugin_paths}

    # A "non-body" item whose OWN mesh WAS converted (e.g. a skin-tight cloth
    # piece that covers a non-body slot but still got a UBE mesh) must point at
    # the converted `!UBE\` mesh, NOT the source one. A blanket keep-source
    # (`lambda: False`) left every such piece wearing the un-converted source
    # mesh on the UBE body -> distorted/invisible in-game. Genuine non-body items
    # (helmets/jewelry) aren't in converted_rel_paths, so they still keep source.
    # #mnb-converted-redirect
    crp = converted_rel_paths or set()

    def _conv_exists(model_path: str) -> bool:
        return _converted_model_exists(model_path, crp, strip_meshes=_strip)

    def _ube_exists(model_path: str) -> bool:
        # What a minted slot may point at: our converted mesh or (twin rule on,
        # the default) a hand-made UBE twin another mod ships. #coverage-ube-twin
        return _conv_exists(model_path) or (
            _twin and bool(model_path) and bool(ube_twin_exists(model_path)))

    # #coverage-third-party-drawn: another mod's UBE armature on the winning
    # record, judged by what it draws (`mesh_live`: the game view).
    _tpd = _coverage_third_party_drawn()
    _tpd_state = _ThirdPartyDrawn(arma_win, mesh_live, _conv_exists)
    _subset = _RaceSubset(arma_win)      # #coverage-race-subset
    # #coverage-female-standin: a dead female slot's stand-in, else (non-body)
    # its male path as it is. Only where a dead path can be told at all. Asked
    # by the dead-armature test (Pass 2) and the rebuild (Pass 3).
    _standin = (_female_standin_resolver(arma_win, _ube_exists)
                if _female_standin and female_mesh_exists is not None else None)
    # #coverage-dead-armature: None = off, or the modlist cannot be read. One
    # whose copy draws the stand-in instead is not dead.
    _dead = (_dead_armature_judge(arma_win, crp, mesh_exists=dead_mesh_exists,
                                  ube_twin_exists=ube_twin_exists,
                                  draws_instead=_dead_slot_draws(
                                      _standin, female_mesh_exists))  # non-body
             if _coverage_dead_armature() and dead_mesh_exists is not None
             else None)

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
    armo_slots: dict = {}  # arma_abs -> its target armours' slots (#coverage-female-standin)
    for armo_abs, (apayload, am, _an, arms, rnam, slots, edid, aflags) in armo_win.items():
        # FULL SKYPATCHER: skip ARMOs the Combined INI already links --
        # armature lists in ESPs no longer reflect runtime coverage, so
        # without this the fallback re-covers everything -> DOUBLE
        # armature (body renders twice = clipping) and UBE-primary
        # hands mints (invisible gauntlets). #fsp-dedup
        if exclude_armo_abs and armo_abs in exclude_armo_abs:
            continue
        # cover_all (unified/primary) also covers non-playable NPC gear -- it can
        # sit on a UBE-race NPC and would otherwise be invisible. The armature
        # DefaultRace filter below is still the beast guard, so this is crash-safe.
        if (aflags & ARMO_NONPLAYABLE_FLAG) and not cover_all:
            continue
        if slots & _DEFORMING_SLOTS_MASK:
            continue
        _wig = False
        if slots and (slots & _HAIR_ONLY_SLOTS) == slots and \
                not _hair_only_armo_is_equippable_headgear(apayload, am):
            # #coverage-wigs: a wig the player can equip counts as headgear.
            if not (_wigs and _is_playable_named(aflags, apayload)):
                continue
            _wig = True
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        if any(v[4] for _x, v in winning) and not _tpd:
            continue  # already has a UBE armature (#coverage-third-party-drawn off)
        # Mint only DefaultRace armatures (human/mer); beast armatures crash.
        # A DefaultRace one that lists only beast races is a beast variant: no
        # human draws it, so no UBE actor may. #coverage-beast-variant
        _bv = [x for x, v in winning
               if v[3] == DEFAULT_RACE and _beast and _is_beast_variant(v)]
        for _bx, _bval in winning:
            if _bx in _bv and _bx not in beast_skipped:
                beast_skipped.append(_bx)
                if _lists_non_actor_race(_bval):   # #beast-variant-non-actor
                    beast_non_actor.append(_bx)
        to_mint = [x for x, v in winning if v[3] == DEFAULT_RACE and x not in _bv]
        # #coverage-human-race-list: none -- an armature with another primary
        # that lists the human races (an Argonian-primary amulet) is taken
        # instead, targeting the UBE counterparts of the races it lists. A beast
        # variant is DefaultRace-primary, so this rule never takes one.
        _listed: dict = {}
        if not to_mint and _race_list:
            _listed = _race_list_admits(
                armo_abs, aflags, winning, worn=npc_worn_armo_abs,
                skins=skins, arma_ok=lambda v: True)
            to_mint = list(_listed)
        # #coverage-third-party-drawn: what another mod's UBE armature on this
        # winning record already draws is not minted again; the rest is minted
        # for the UBE races it leaves out.
        _fewer: dict = {}
        if _tpd and to_mint and any(v[4] for _x, v in winning):
            to_mint, _fewer = _tpd_state.split(armo_abs, edid, slots, winning,
                                               to_mint, _listed)
            _listed = {k: w for k, w in _listed.items() if k in to_mint}
        if not to_mint:
            continue
        _kept_excluded = False
        if withheld_armo_abs and armo_abs in withheld_armo_abs:
            _why = "excluded"
            if _body_only:
                # #exclude-body-only: a non-body piece no other mod patches
                # keeps its coverage, with no mesh converted for its mod.
                if not _probe[1]:
                    from .auto_convert import _exclusion_keep_probe
                    _probe[:] = [_exclusion_keep_probe(), True]
                _why = _excluded_piece_holds(
                    armo_abs, owned_records.get(armo_abs, []), to_mint,
                    arma_win, slots, _ube_exists, _probe[0])
            if _why is not None:
                withheld.append((armo_abs, edid))     # #exclude-owned-coverage
                nonbody_held.append((armo_abs, edid, _why))
                continue
            _kept_excluded = True
        # #coverage-dead-armature: last, so every rule above counts as before.
        if _dead is not None:
            to_mint = _drop_dead(to_mint, _dead, dead_skipped)
            if not to_mint:
                dead_dropped.append((armo_abs, edid))
                continue
            _listed = {k: w for k, w in _listed.items() if k in to_mint}
        # #coverage-race-subset: an author's per-race siblings, each for the UBE
        # counterparts of its own races. Not beside a third-party reduction.
        _narrow: dict = {}
        if not _listed and not _fewer:
            to_mint, _narrow = _subset.split(armo_abs, edid, to_mint, slots)
        if _kept_excluded:
            nonbody_kept.append((armo_abs, edid))
        if _listed:
            race_list_ube.update(_listed)
            race_listed.append((armo_abs, edid))
        targets.append((armo_abs, plugin_case.get(armo_abs[0], armo_abs[0]),
                        to_mint))
        _tpd_state.targeted(to_mint, _fewer)   # #coverage-third-party-drawn
        _subset.targeted(to_mint, _narrow)     # #coverage-race-subset
        for x in to_mint:
            mint_set.setdefault(x, None)
            armo_slots[x] = armo_slots.get(x, 0) | slots   # #coverage-female-standin
        if _wig:
            wigs_added.append((armo_abs, edid))   # #coverage-wigs

    # ---- Pass 3: mint ESP (UBE-primary ARMAs only; masters = vanilla + UBE) ----
    patch_masters = list(VANILLA_DLC_MASTERS)
    _add_master_if_missing(patch_masters, ube_allrace_filename)
    # preserve_textures: declare the masters each minted armature's alt-textures
    # (MO?S) reference, so their FormIDs can be remapped into THIS ESP's master
    # space instead of stripped. Without this, two ARMOs that share a mesh but
    # differ ONLY in an alt-texture set (a colour/texture variant -- e.g. a plain
    # vs a patterned stocking) both mint to a bare-mesh ARMA, become byte-identical,
    # and the merge dedups them to ONE -> the variant loses its distinct look.
    # Capped below the 255 top-byte limit; a ref past the cap falls back to strip.
    # #nonbody-preserve-alttex
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

    # Drop source-master FormID refs + texture data so the minted ARMA references
    # only UBE_AllRace (races) + mesh paths; keeps the ESP master list minimal.
    # NAM0-3 = skin-TXST / texture-swap FormIDs; strip for same reason as MO?S.
    # preserve_textures keeps the alt-texture refs instead (remapped), stripping
    # only the two non-texture FormID refs (footstep sound / art object).
    STRIP = {b"SNDD", b"ONAM", b"MO2S", b"MO3S", b"MO4S", b"MO5S",
             b"MO2T", b"MO3T", b"MO4T", b"MO5T",
             b"NAM0", b"NAM1", b"NAM2", b"NAM3"}
    STRIP_MIN = {b"SNDD", b"ONAM"}
    # (the converted-mesh lookups `crp`/`_ube_exists` are set before Pass 2:
    # #exclude-body-only asks them there; so is the stand-in `_standin`.)

    new_arma_records: list[esp.Record] = []
    _mint_rec: dict = {}   # arma_abs -> minted Record (for post-prune sidecar fids)
    next_id = ESL_OWN_FORMID_MIN
    # (a `mint_name = out_path.with_suffix(".esp").name` was computed here and
    # never read; both mint blocks carried the same dead copy. Removed 2026-09-06.)
    preserved_count = 0
    preserve_fallbacks: list = []
    for arma_abs in mint_set:
        payload, m2, n2, _rn, _u = arma_win[arma_abs]
        _as_is = (_nonbody_male_as_is(payload, armo_slots.get(arma_abs, 0),
                                      _ube_exists, female_mesh_exists)
                  if _standin is not None else None)
        # UBE-primary + every UBE race; an armature the race-list rule took
        # targets the UBE counterparts of the races it lists, the first of
        # them primary. #coverage-human-race-list
        _prim, _addl = ube_primary_patch, ube_races_patch
        _listed_ube = race_list_ube.get(arma_abs)
        if _listed_ube:
            _addl = [(ube_byte << 24) | f for f in _listed_ube]
            _prim = _addl[0]
        # #coverage-third-party-drawn: only the UBE races another mod's
        # armature does not draw, the first of them primary.
        _fewer_ube = _tpd_state.races_for(arma_abs)
        if _fewer_ube:
            _addl = [(ube_byte << 24) | f for f in _fewer_ube]
            _prim = _addl[0]
        # #coverage-race-subset: the UBE counterparts of its own races (never
        # with the two above: those armatures are minted unsplit).
        _sub_ube = _subset.races_for(arma_abs)
        if _sub_ube:
            _addl = [(ube_byte << 24) | f for f in _sub_ube]
            _prim = _addl[0]
        minted_payload = None
        _declined: list = []     # per attempt: a failed preserve must not count
        if preserve_textures:
            # Keep the alt-texture set (MO?S) + skin swaps (NAM0-3), remapping their
            # TXST FormIDs into this ESP's master space so a texture variant renders
            # distinctly (and doesn't dedup-collapse into its sibling). #nonbody-preserve-alttex
            def _remap(fid: int, _sm=m2, _sn=n2) -> int:
                return remap_fid(fid, _sm, _sn, patch_masters)
            try:
                kept = b"".join(
                    esp.encode_subrecord(s, d)
                    for s, d in esp.iter_subrecords(payload) if s not in STRIP_MIN)
                kept = _remap_arma_skin_txsts(kept, _remap)      # NAM0-3 -> patch space
                minted_payload = rebuild_arma_payload(
                    kept,
                    new_primary_rnam=_prim,
                    new_additional_race_fids=_addl,
                    alt_texture_fid_remap=_remap,                # MO?S -> patch space
                    converted_nif_exists=_ube_exists, strip_meshes_prefix=_strip,
                    keep_named_female=_female_guard, declined_log=_declined,
                    female_mesh_exists=female_mesh_exists,
                    female_standin=_standin, dead_female_male_as_is=_as_is,
                )
                preserved_count += 1
            except Exception as _e:      # unresolvable master -> strip fallback
                minted_payload = None
                _declined = []
                preserve_fallbacks.append((arma_abs[0], arma_abs[1], repr(_e)))
        if minted_payload is None:
            stripped = b"".join(
                esp.encode_subrecord(s, d)
                for s, d in esp.iter_subrecords(payload) if s not in STRIP)
            minted_payload = rebuild_arma_payload(
                stripped,
                new_primary_rnam=_prim,
                new_additional_race_fids=_addl,
                converted_nif_exists=_ube_exists,  # redirect to !UBE\ where converted
                strip_meshes_prefix=_strip,
                keep_named_female=_female_guard, declined_log=_declined,
                female_mesh_exists=female_mesh_exists,
                female_standin=_standin, dead_female_male_as_is=_as_is,
            )
        _file_declined(_declined, arma_abs, _flogs, female_mesh_exists,
                       payload, armo_slots.get(arma_abs, 0))
        if _twin:
            for d in _ube_twin_slots(payload, crp, ube_twin_exists,
                                     strip_meshes=_strip):
                twin_slots.append({"arma": f"{arma_abs[0]}|{arma_abs[1]:X}", **d})
        new_fid = (own_byte << 24) | next_id
        next_id += 1
        new_edid = "UBE_MNB_{:X}".format(arma_abs[1])
        minted_payload = replace_arma_edid(minted_payload, new_edid[:90])
        _rec = esp.Record(
            sig=b"ARMA", flags=0, formid=new_fid, timestamp_vc=0,
            version_unk=0x002C, payload=minted_payload)
        new_arma_records.append(_rec)
        mint_set[arma_abs] = new_fid
        _mint_rec[arma_abs] = _rec

    # #coverage-esl-chunks -- emit ESL-sized pieces instead of one monolithic ESP.
    _res = _emit_coverage_pieces(
        out_path, targets, _mint_rec, patch_masters, own_byte=own_byte,
        author=author, description=description,
        ini_header=[
        "; cbbe-to-ube: UBE race coverage for mod-defined non-body armor.",
        "; Adds a minted UBE-primary ArmorAddon to each item whose winning",
        "; armature lacked UBE races (overhauls re-armature vanilla gear).",
        ],
        preserve_textures=preserve_textures, master_data_dirs=master_data_dirs,
        emit_sidecar=emit_sidecar,
        # A twin path is outside our output but was checked to exist; so was a
        # stand-in that is a twin. #coverage-female-standin
        mesh_resolves=_outside_paths_predicate(
            [d["path"] for d in twin_slots] + [d["standin"] for d in female_standin]))
    ini_lines = _res["ini_lines"]
    warnings = _res["validation_warnings"]

    return {
        "output": str(out_path),
        "pieces": _res["pieces"],
        "split_pieces": _res["split_pieces"],
        "ini_lines": ini_lines,
        "masters": _res["masters"],
        "minted_armas": _res["minted_armas"],
        "armo_targets": len(targets),
        "esl_flagged": _res["esl_flagged"],
        "candidates_scanned": len(armo_win),
        "validation_warnings": warnings,
        "textures_preserved": preserved_count,
        "texture_fallbacks": len(preserve_fallbacks),
        "withheld": withheld,
        "female_kept": female_kept,
        "female_dead_male": female_dead,
        # #coverage-female-standin
        "female_standin": female_standin,
        "female_male_nonbody": female_as_is,
        "female_dead_kept": female_dead_kept,
        "ube_twin": twin_slots,
        "beast_variant_skipped": [f"{a[0]}|{a[1]:X}" for a in beast_skipped],
        "beast_variant_non_actor": [f"{a[0]}|{a[1]:X}" for a in beast_non_actor],
        "wigs": wigs_added,
        # #coverage-human-race-list: armours taken by the race-list rule.
        "race_listed": race_listed,
        # #exclude-body-only: excluded non-body armour still minted, and why
        # each withheld one was held.
        "exclusion_nonbody_kept": nonbody_kept,
        "exclusion_nonbody_held": nonbody_held,
        # #coverage-third-party-drawn
        **_tpd_state.stats(),
        # #coverage-race-subset: armours whose per-race armatures were split
        # by race, and the armatures listing only other races left off.
        **_subset.stats(),
        # #coverage-dead-armature: armatures not minted, and the armours left
        # with none.
        "dead_armature_skipped": [f"{a[0]}|{a[1]:X}" for a in dead_skipped],
        "dead_dropped": dead_dropped,
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
    cover_hands_feet: bool = False,
    preserve_textures: bool = False,
    emit_sidecar: bool = False,
    withheld_armo_abs: "set[tuple[str, int]] | None" = None,
    female_mesh_exists: "callable[[str], bool] | None" = None,
    mesh_exists: "callable[[str], bool] | None" = None,
    ube_twin_exists: "callable[[str], str | None] | None" = None,
    npc_worn_armo_abs: "set[tuple[str, int]] | frozenset | None" = None,
    mesh_live: "callable[[str], bool] | None" = None,
    dead_mesh_exists: "callable[[str], bool] | None" = None,
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
    Returns stats.

    `withheld_armo_abs`: as in the non-body pass (#exclude-owned-coverage).
    Every such armour is withheld here, except one minted for its wig alone
    (#wig-body-pass): a non-body piece, kept when `_excluded_piece_holds`
    names no reason (`exclusion_nonbody_kept`). #wig-exclude-keep

    `mesh_exists`: does a mesh exist anywhere the game reads it (loose, MO2's
    overwrite, any archive)? #coverage-world-mesh asks it whether an unconverted
    female path is dead (None = assume it exists); #coverage-nude-skin asks it
    whether the UBE body's own hand/foot resolves (None = it cannot be shown to,
    so the part is not minted). Unset, it is `female_mesh_exists` -- the same
    lookup, so a caller passing only that sees one answer from every test.

    `ube_twin_exists`: as in the non-body pass (#coverage-ube-twin, on by
    default). It moves where a minted slot points, and so what the world-mesh
    and female guard tests see. While #skip-built-ube-path is on (its default)
    it also admits an armature whose mesh is such a twin (`_admits`): that mesh
    was left to its builder, so it is not in `converted_rel_paths`.

    `npc_worn_armo_abs`: as in the non-body pass (#coverage-human-race-list).
    The race-list rule admits a body armature only with a converted mesh, as
    the DefaultRace rule does; a hands/feet one keeps its source races, the UBE
    counterparts of the human ones added.

    `mesh_live`: as in the non-body pass (#coverage-third-party-drawn). Here it
    also replaces the slot-32 exemption from the old blanket skip: a body
    armour's third-party UBE armature is judged like any other.

    `dead_mesh_exists`: as in the non-body pass (#coverage-dead-armature),
    judged after the female guard, the world-mesh, nude-skin, body-accessory
    and third-party rules, so a hood or a race-list armature is judged too. An
    armature that draws the UBE body's own hand or foot is never dead."""
    out_path = Path(output_esp_path)
    exclude = {n.lower() for n in (exclude_names or set())}
    DEFAULT_RACE = ("skyrim.esm", _DEFAULT_RACE_LOW24)
    crp = converted_rel_paths or set()
    _female_guard = _coverage_female_guard()
    _world_mesh = _coverage_world_mesh()
    _nude_skin = _coverage_nude_skin()
    _twin = _coverage_ube_twin() and ube_twin_exists is not None
    _strip = _twin_path_strip_meshes()   # #twin-path-strip-meshes
    if mesh_exists is None:
        mesh_exists = female_mesh_exists
    withheld: list = []        # (armo_abs, edid) left alone for --exclude-mods
    # #exclude-body-only, report only: a withheld body piece that another mod's
    # SkyPatcher patch names is left to that patch (why, as the non-body pass).
    _body_only = bool(withheld_armo_abs) and _exclude_body_only()
    body_held: list = []       # (armo_abs, edid, why)
    _bprobe: list = [None, False]   # [probe, built]

    def _body_probe():
        """The #exclude-body-only modlist probe, built on first use (None when
        the modlist cannot be read)."""
        if not _bprobe[1]:
            from .auto_convert import _exclusion_keep_probe
            _bprobe[:] = [_exclusion_keep_probe(), True]
        return _bprobe[0]

    female_kept: list = []     # female slots that kept their own mesh (guard)
    female_dead: list = []     # dead female paths: the male mesh stays (guard)
    # #coverage-female-standin: dead female paths that draw the vanilla female
    # counterpart, the male path as it is (non-body), or nothing at all.
    female_standin: list = []
    female_as_is: list = []
    female_dead_kept: list = []
    _flogs = {"kept": female_kept, "dead": female_dead, "standin": female_standin,
              "as_is": female_as_is, "dead_kept": female_dead_kept}
    _female_standin = _coverage_female_standin()
    guard_skipped: list = []   # body armatures not minted: only the male was converted
    guard_dropped: list = []   # ARMOs left with nothing to mint by that
    world_skipped: list = []   # body armatures not minted: world mesh unconverted
    world_dropped: list = []   # ARMOs left with nothing to mint by that
    world_partial: list = []   # ARMOs still minted, but with no slot-32 armature left
    nude_redirect: dict = {}   # arma_abs -> the UBE body part its MOD3 draws
    nude_skipped: list = []    # (arma_abs, why) nude parts not minted: skin/unresolved
    nude_dropped: list = []    # (armo_abs, edid, why) ARMOs left with nothing to mint by that
    twin_slots: list = []      # slots pointed at a hand-made UBE twin
    beast_skipped: list = []   # DefaultRace armatures listing only beast races (#coverage-beast-variant)
    beast_non_actor: list = []  # ... of which also list the mannequin race (#beast-variant-non-actor)
    _beast = _coverage_beast_variant()
    dead_skipped: list = []    # armatures whose every mesh is dead (#coverage-dead-armature)
    dead_dropped: list = []    # (armo_abs, edid) left with nothing to mint by that
    accessory_added: list = []  # non-deforming armatures of a body armour (#coverage-body-accessory)
    _body_accessory = _coverage_body_accessory()
    _acc_guard = _accessory_race_guard()
    cloak_added: list = []     # ... of which cloak-named drapes (#coverage-body-cloak)
    _body_cloak = _coverage_body_cloak()
    _race_list = _coverage_human_race_list()
    skins: set = set()         # any RACE/NPC_ WNAM (#coverage-human-race-list)
    race_list_ube: dict = {}   # arma_abs -> UBE races it targets (same)
    race_listed: list = []     # (armo_abs, edid) taken by the race-list rule
    wigs_added: list = []      # (armo_abs, edid) wigs on a deforming armour (#wig-body-pass)
    _wig_body = _wig_body_pass()
    # #wig-exclude-keep: an excluded mod's wig minted with its own mesh when
    # the non-body pass's keep test keeps it; every record of an owned armour.
    _wig_keep = _body_only and _wig_body and _wig_exclude_keep()
    body_kept: list = []       # (armo_abs, edid)
    owned_records: dict = {}   # armo_abs -> [(plugin, armatures, edid)], load order
    # What would have made an armature a CONVERSION candidate -- the selection's
    # own slot sets and cloak names, read from it so the two cannot drift.
    from .auto_convert import (_BODY_SLOT_BITS, _BODY_CANDIDATE_SLOT_BITS,
                               _CLOAK_MESH_KEYWORDS)
    _accessory_excluded_bits = (_DEFORMING_SLOTS_MASK | _BODY_SLOT_BITS
                                | _BODY_CANDIDATE_SLOT_BITS)

    def _cloak_named(payload: bytes) -> bool:
        for sig, d in esp.iter_subrecords(payload):
            if sig in (b"MOD2", b"MOD3"):
                base = _model_path_text(d, "cp1252")
                base = base.replace("/", "\\").rsplit("\\", 1)[-1].lower()
                if any(k in base for k in _CLOAK_MESH_KEYWORDS):
                    return True
        return False

    # #coverage-body-cloak: the mesh reader (`auto_convert._mesh_exists_anywhere`)
    # of whichever lookup the caller passed; none -> no cloak is admitted.
    _unfitted = (getattr(mesh_exists, "unfitted_skin", None)
                 or getattr(dead_mesh_exists, "unfitted_skin", None))

    def _cloak_drapes(x, v, beast) -> bool:
        """#coverage-body-cloak: may this cloak-named armature ride along? A
        human's armature (not in `beast`, the armour's beast variants; no UBE
        race already), and every world mesh it names is a skinned drape with
        no body-fit bone."""
        if not (_body_cloak and _unfitted is not None) or x in beast or v[4]:
            return False
        world = [_model_path_text(d, "cp1252")
                 for sig, d in esp.iter_subrecords(v[0]) if sig in (b"MOD2", b"MOD3")]
        world = [w for w in world if w]
        return bool(world) and all(_unfitted(w) is True for w in world)

    def _conv_exists(model_path: str) -> bool:
        return _converted_model_exists(model_path, crp, strip_meshes=_strip)

    def _ube_exists(model_path: str) -> bool:
        # What a minted slot may point at: our converted mesh or (twin rule on,
        # the default) a hand-made UBE twin another mod ships. #coverage-ube-twin
        return _conv_exists(model_path) or (
            _twin and bool(model_path) and bool(ube_twin_exists(model_path)))

    # What admits a body armature: a converted mesh, or -- when the planner
    # leaves built UBE twins to their builders -- such a twin. #skip-built-ube-path
    _admits = _ube_exists if (_twin and _skip_built_ube_path()) else _conv_exists
    _tpd = _coverage_third_party_drawn()   # #coverage-third-party-drawn
    # #coverage-female-standin: set once the winner scan is read (Pass 1).
    _standin = None

    def _female_world_needs_male(payload: bytes) -> bool:
        """Would `rebuild_arma_payload` fill this armature's female WORLD slot
        from its converted male mesh although the female mesh exists? MOD3 names
        a mesh that was not converted, MOD2 (before it in the record) was, and
        the MOD3 mesh exists somewhere. Same test, same order -- and the same
        idea of "converted" the rebuild is handed (a twin counts)."""
        conv2 = False
        for sig, d in esp.iter_subrecords(payload):
            if sig == b"MOD2":
                conv2 = _ube_exists(_model_path_text(d, "utf-8"))
            elif sig == b"MOD3":
                p = _model_path_text(d, "utf-8")
                return (bool(p) and not _ube_exists(p) and conv2
                        and (female_mesh_exists is None or female_mesh_exists(
                            _model_path_text(d, "cp1252"))))
        return False

    def _world_mesh_converted(payload: bytes) -> bool:
        """#coverage-world-mesh: is the female WORLD mesh a minted body armature
        would draw a converted one? MOD3 converted; or MOD3 absent, empty or
        dead with MOD2 (before it in the record) converted -- the rebuild then
        draws the male mesh, which is what the engine draws for a female. A
        converted first-person mesh alone does not count. A dead MOD3 with a
        converted stand-in counts, converted male or not -- the rebuild draws
        the stand-in (same lookup, same resolver). #coverage-female-standin"""
        conv2 = False
        male2 = ""
        for sig, d in esp.iter_subrecords(payload):
            if sig == b"MOD2":
                conv2 = _ube_exists(_model_path_text(d, "utf-8"))
                male2 = _model_path_text(d, "cp1252")
            elif sig == b"MOD3":
                p = _model_path_text(d, "utf-8")
                if p and _ube_exists(p):
                    return True
                if (p and _standin is not None and female_mesh_exists is not None
                        and not female_mesh_exists(
                            _model_path_text(d, "cp1252"))
                        and _standin("MOD3", male2) is not None):
                    return True
                if not (conv2 and p):
                    return conv2       # empty MOD3: the male fills it
                # Named, unconverted: admitted only when it exists nowhere
                # (None = cannot tell, so it is taken to exist).
                return mesh_exists is not None and not mesh_exists(
                    _model_path_text(d, "cp1252"))
        # No MOD3: the rebuild synthesises it from the converted MOD2, wherever
        # MOD2 sits in the record.
        return any(_ube_exists(_model_path_text(d, "utf-8"))
                   for sig, d in esp.iter_subrecords(payload) if sig == b"MOD2")

    def _mod3(payload: bytes) -> str:
        for sig, d in esp.iter_subrecords(payload):
            if sig == b"MOD3":
                return _model_path_text(d, "cp1252")
        return ""

    def _arma_models(payload: bytes) -> "list[str]":
        return [_model_path_text(d, "utf-8")
                for sig, d in esp.iter_subrecords(payload)
                if sig in (b"MOD2", b"MOD3", b"MOD4", b"MOD5")]

    def _arma_bod2_slots(payload: bytes) -> int:
        for sig, d in esp.iter_subrecords(payload):
            if sig in (b"BOD2", b"BODT") and len(d) >= 4:
                return struct.unpack_from("<I", d, 0)[0]
        return 0

    def _mesh_admits(v, cover_hf: bool) -> bool:
        """May this armature be minted, as far as its meshes go? A converted
        mesh, or -- pure hands/feet in unified mode -- a hands/feet slot. The
        DefaultRace and the race-list rules both ask it."""
        return (any(_admits(mp) for mp in _arma_models(v[0]))
                or (cover_hf
                    and bool(_arma_bod2_slots(v[0])
                             & _BIPED_SLOT_HANDS_FEET_BITS)))

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
                if _wig_keep and a in withheld_armo_abs:   # #wig-exclude-keep
                    owned_records.setdefault(a, []).append(
                        (nm.lower(), tuple(arms), edid))
        if _race_list:
            _collect_skins(pe, m, nm, skins)

    plugin_case = {Path(p).name.lower(): Path(p).name
                   for p in ordered_plugin_paths}
    # #coverage-female-standin: the dead female slot's stand-in -- asked by
    # _world_mesh_converted (Pass 2) and the rebuild (Pass 3).
    if _female_standin and female_mesh_exists is not None:
        _standin = _female_standin_resolver(arma_win, _ube_exists)
    _tpd_state = _ThirdPartyDrawn(arma_win, mesh_live, _conv_exists)
    _subset = _RaceSubset(arma_win)      # #coverage-race-subset
    # #coverage-dead-armature: None = off, or the modlist cannot be read. One
    # whose copy draws the stand-in instead is not dead.
    _dead = (_dead_armature_judge(arma_win, crp, mesh_exists=dead_mesh_exists,
                                  ube_twin_exists=ube_twin_exists,
                                  draws_instead=_dead_slot_draws(
                                      _standin, female_mesh_exists))  # body
             if _coverage_dead_armature() and dead_mesh_exists is not None
             else None)

    # ---- Pass 2: target body/deforming ARMOs lacking UBE coverage whose mesh
    #      WAS converted ----
    ARMO_NONPLAYABLE_FLAG = 0x00000004
    targets = []          # (armo_abs, defining_plugin_case, [arma_abs to mint])
    mint_set: dict = {}
    armo_slots: dict = {}  # arma_abs -> its target armours' slots (#coverage-female-standin)
    for armo_abs, (apayload, am, _an, arms, rnam, slots, edid, aflags) in armo_win.items():
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
        # Unified mode (cover_hands_feet) makes the winner-scan the PRIMARY path
        # for PURE hands/feet (33/37) too: cover them here -- non-playable NPC gear
        # included -- and mint them SOURCE-primary below (preserved vanilla races +
        # UBE) so a UBE actor's vanilla-resolving hand/foot slot still matches. This
        # is safe ONLY because the per-source builder stops covering hands/feet when
        # this is on (else the double armature = the invisible-gauntlet bug).
        _cover_hf = (cover_hands_feet
                     and bool(slots & _BIPED_SLOT_HANDS_FEET_BITS)
                     and not _is_body)
        if (aflags & ARMO_NONPLAYABLE_FLAG) and not (_cover_body or _cover_hf):
            continue
        if not (slots & _DEFORMING_SLOTS_MASK):
            continue                       # only body/hands/feet here (the inverse of non-body)
        # cover_all covers TORSO body (slot 32); cover_hands_feet adds pure
        # hands/feet (33/37). Without either, a non-body-non-HF deforming item was
        # skipped in cover_all -- "it's the per-source builder's job / fallback
        # role".
        #
        # THAT ASSUMPTION DIED WITH UNIFIED COVERAGE (#slot34-coverage-hole, fixed
        # 2026-08-11). In unified mode the winner scan is the SOLE generator and
        # the per-source patches are LEFT UNMERGED, so there is no fallback: a
        # deforming item that is neither body nor hands/feet was covered by
        # NOBODY. `_DEFORMING_SLOTS_MASK` is slots 32/33/34/37/38, so the hole was
        # exactly **slot 34 (forearms) and slot 38 (calves)** -- the non-body pass
        # rejects them for HAVING a deforming slot, this pass rejected them for
        # not being body or hands/feet.
        #
        # Measured on a real pack: ALL 51 slot-34-only and ALL 7 slot-38-only
        # ARMOs had no armature link, so every one was equippable and INVISIBLE on
        # a UBE race. Reported as "crimson dark arms are equipable but invisible".
        #
        # Admitting them needs no new guard: `to_mint` below already requires a
        # DefaultRace armature AND a converted !UBE mesh, which is what keeps a
        # non-body mesh from being handed UBE body races (the documented
        # actor-setup ACCESS_VIOLATION). Checked against the worst case in the
        # pack -- a modder's tower SHIELD parked on slot 38 -- and it fails both:
        # its armature is on a custom giant race and its mesh was never converted.
        _unified = bool(cover_hands_feet)   # winner scan is the sole generator
        if cover_all and not _is_body and not _cover_hf and not _unified:
            continue
        # Fallback role filters on the ARMO's own RNAM. cover_all (unified/primary)
        # does NOT -- ARMO RNAM is frequently a quirky authoring choice (a non-
        # default race on human gear), exactly as the non-body pass documents. The
        # real beast guard is the armature-level DefaultRace filter in to_mint
        # below, so dropping the ARMO-RNAM skip here stays crash-safe while
        # recovering human armor the per-source path covered. #unified-coverage
        if rnam != DEFAULT_RACE and not cover_all:
            continue                       # beast/custom race -> never UBE-extend
        if not arms:
            continue
        winning = [(x, arma_win.get(x)) for x in arms]
        winning = [(x, v) for x, v in winning if v is not None]
        if not winning:
            continue
        _has_ube = any(v[4] for _x, v in winning)
        if _has_ube and not _cover_body and not _tpd:
            continue                       # already has a UBE armature (vanilla ARMO path)
        # mint DefaultRace armatures: BODY needs a converted mesh (an unconverted
        # CBBE body on UBE clips); pure hands/feet (unified) are covered whether or
        # not converted -- they keep the original mesh where unconverted, exactly
        # like the per-source path, so a modded gauntlet is never left invisible.
        # A built UBE twin another mod ships counts as converted: the planner
        # leaves such a mesh to its builder. #skip-built-ube-path
        # A beast variant (DefaultRace, beast races only) is no human's armature.
        # #coverage-beast-variant
        _bv = [x for x, v in winning
               if v[3] == DEFAULT_RACE and _beast and _is_beast_variant(v)]
        for _bx, _bval in winning:
            if _bx in _bv and _bx not in beast_skipped:
                beast_skipped.append(_bx)
                if _lists_non_actor_race(_bval):   # #beast-variant-non-actor
                    beast_non_actor.append(_bx)
        to_mint = [x for x, v in winning
                   if v[3] == DEFAULT_RACE and x not in _bv
                   and _mesh_admits(v, _cover_hf)]
        # #coverage-human-race-list: none -- an armature with another primary
        # that lists the human races is taken instead, on the same mesh test:
        # a body armature still needs a converted mesh.
        _listed: dict = {}
        if not to_mint and _race_list:
            _listed = _race_list_admits(
                armo_abs, aflags, winning, worn=npc_worn_armo_abs, skins=skins,
                arma_ok=lambda v, _hf=_cover_hf: _mesh_admits(v, _hf))
            to_mint = list(_listed)
        # #wig-body-pass: nothing admitted -- a hair-only armature of a
        # playable, named armour is a wig, minted with its own mesh as the
        # non-body pass mints one: DefaultRace first, else by its race list.
        _wig_here: list = []
        if not to_mint and _wig_body and _is_playable_named(aflags, apayload):
            _wig_here = [x for x, v in winning
                         if x not in _bv and v[3] == DEFAULT_RACE
                         and _is_hair_only_armature(v[0])
                         and not (_acc_guard and v[4])]
            if not _wig_here and _race_list:
                _listed = _race_list_admits(
                    armo_abs, aflags, winning, worn=npc_worn_armo_abs,
                    skins=skins, arma_ok=lambda v: _is_hair_only_armature(v[0]))
                _wig_here = list(_listed)
            to_mint = list(_wig_here)
        if not to_mint:
            continue
        # Withheld BEFORE the guard below, so an excluded armour the guard would
        # also have emptied is still named as withheld. #exclude-owned-coverage
        _withhold = bool(withheld_armo_abs) and armo_abs in withheld_armo_abs
        _kept_excluded = False
        _why = None
        if _withhold:
            # #coverage-third-party-drawn: one another mod's armature draws
            # whole is not "left without an armature from any mod".
            if _tpd and _has_ube and not _tpd_state.split(
                    armo_abs, edid, slots, winning, to_mint, _listed,
                    record=False)[0]:
                continue
            # #wig-exclude-keep: a wig alone is a non-body piece -- the non-body
            # pass's keep test decides it, as it decides the same wig there.
            if _wig_keep and _wig_here:
                _why = _excluded_piece_holds(
                    armo_abs, owned_records.get(armo_abs, []), to_mint,
                    arma_win, slots, _ube_exists, _body_probe())
                _kept_excluded = _why is None
        if _withhold and not _kept_excluded:
            withheld.append((armo_abs, edid))
            if _why is not None:
                # The wig's keep test named why; a patch it names takes it.
                if _held_for_another_patch(_why) is not None:
                    body_held.append((armo_abs, edid, _why))
            elif _body_only:
                _p = _body_probe()
                _by = (_p.named(armo_abs, [edid] if edid else [])
                       if _p is not None else None)
                if _by is not None:
                    body_held.append((armo_abs, edid, f"named by {_by}"))
            continue
        # #coverage-female-guard: a converted MALE mesh does not qualify a TORSO
        # armature whose own female mesh was not converted -- minting it would put
        # the male mesh on the female body. Judged per ARMATURE (its own BOD2, else
        # the armour's): the gauntlets armature of a cuirass-and-gauntlets armour
        # is hands, and like slot 33/34/37/38 it stays minted with its female slot
        # on its source path (rebuild_arma_payload).
        if _female_guard and _is_body and to_mint:
            _male_only = [x for x in to_mint
                          if ((_arma_bod2_slots(arma_win[x][0]) or slots)
                              & _BIPED_SLOT_BODY_BIT)
                          and _female_world_needs_male(arma_win[x][0])]
            if _male_only:
                to_mint = [x for x in to_mint if x not in _male_only]
                for x in _male_only:              # distinct armatures: one is
                    if x not in guard_skipped:    # often shared by many variants
                        guard_skipped.append(x)
                if not to_mint:
                    guard_dropped.append((armo_abs, edid))
                    continue
        # #coverage-world-mesh: a TORSO armature is admitted above when ANY of its
        # models was converted -- a first-person mesh alone let 87 through (live),
        # each drawing its unconverted CBBE world mesh on the UBE body. It must draw a
        # converted female world mesh (or the male one where the female is
        # absent or dead). Same per-armature test as the guard above; after it,
        # so the guard's own count is unchanged.
        _no_torso = False
        if _world_mesh and _is_body and to_mint:
            _unworld = [x for x in to_mint
                        if ((_arma_bod2_slots(arma_win[x][0]) or slots)
                            & _BIPED_SLOT_BODY_BIT)
                        and not _world_mesh_converted(arma_win[x][0])]
            if _unworld:
                to_mint = [x for x in to_mint if x not in _unworld]
                for x in _unworld:
                    if x not in world_skipped:
                        world_skipped.append(x)
                if not to_mint:
                    world_dropped.append((armo_abs, edid))
                    continue
                # A partial drop: its hands/feet armature is still minted, so the
                # armour is targeted, but nothing left draws slot 32 -- no torso on
                # UBE, as with a full drop. Named in the report if it stays a
                # target. #world-mesh-partial-report
                _no_torso = not any(
                    (_arma_bod2_slots(arma_win[x][0]) or slots) & _BIPED_SLOT_BODY_BIT
                    for x in to_mint)
        # #coverage-nude-skin: a hand/foot armature drawing the CBBE NUDE hands or
        # feet (an NPC costume's "boots" that are bare feet) draws the UBE body's
        # own part, or is not minted. On a race skin -- an armour that also lists
        # a nude torso -- it is not minted: a UBE actor wears UBE's own skin.
        if _nude_skin and to_mint:
            _parts = {x: ube_body_part_for(_mod3(arma_win[x][0])) for x in to_mint
                      if (_arma_bod2_slots(arma_win[x][0]) or slots)
                      & _BIPED_SLOT_HANDS_FEET_BITS}
            _parts = {x: t for x, t in _parts.items() if t}
            if _parts:
                _skin = any(_is_nude_torso_model(_mod3(v[0]))
                            for _x, v in winning)
                _drop = []
                for x, t in _parts.items():
                    why = ("skin" if _skin else
                           None if (mesh_exists is not None and mesh_exists(t))
                           else "unresolved")
                    if why is None:
                        nude_redirect[x] = t
                    else:
                        _drop.append(x)
                        if (x, why) not in nude_skipped:
                            nude_skipped.append((x, why))
                if _drop:
                    to_mint = [x for x in to_mint if x not in _drop]
                    if not to_mint:
                        # One armour, one reason: `_skin` decides every part of
                        # it, and a part that is not skin is dropped only when
                        # unresolved. The report counts armours per reason.
                        nude_dropped.append((armo_abs, edid,
                                             "skin" if _skin else "unresolved"))
                        continue
        # #coverage-body-accessory: a non-deforming armature of this armour (a
        # robe's hood) rides along with the deforming ones minted above -- the
        # non-body pass skips the armour for having a deforming slot, so nothing
        # else ever covers it. Its own BOD2 must name slots, none deforming, and
        # it must never have been a conversion candidate (a body-fitted cape that
        # was not converted would draw its CBBE fit on the UBE body).
        # #accessory-race-guard: never a beast variant the rule above skipped,
        # nor an armature that already names a UBE race -- a second UBE copy
        # would draw over it. The non-body pass takes neither.
        # #coverage-body-cloak: a cloak-named one only when its world meshes
        # are skinned drapes with no body-fit bone (the conversion's crash
        # guard dropped them, so nothing else draws them).
        # #wig-body-pass: a wig alone is no deforming armature to ride along with.
        if _body_accessory and to_mint and not _wig_here:
            _acc = [x for x, v in winning
                    if x not in to_mint and v[3] == DEFAULT_RACE
                    and _arma_bod2_slots(v[0])
                    and not (_arma_bod2_slots(v[0]) & _accessory_excluded_bits)
                    and (not _cloak_named(v[0]) or _cloak_drapes(x, v, _bv))
                    and not (_acc_guard and (x in _bv or v[4]))]
            if _acc:
                to_mint = to_mint + _acc
                for x in _acc:
                    if x not in accessory_added:
                        accessory_added.append(x)
                    if _cloak_named(arma_win[x][0]) and x not in cloak_added:
                        cloak_added.append(x)
        # #coverage-third-party-drawn: judged on what the guards above left,
        # hood included -- what another mod's UBE armature on this winning
        # record already draws is not minted again; the rest is minted for the
        # UBE races it leaves out.
        _fewer: dict = {}
        if _tpd and _has_ube and to_mint:
            to_mint, _fewer = _tpd_state.split(armo_abs, edid, slots, winning,
                                               to_mint, _listed)
            if not to_mint:
                continue
        # #coverage-dead-armature: last, so every rule above counts as before.
        # One drawing the UBE body's own hand/foot draws a mesh that resolves.
        if _dead is not None:
            to_mint = _drop_dead(
                to_mint, lambda x: x not in nude_redirect and _dead(x),
                dead_skipped)
            if not to_mint:
                dead_dropped.append((armo_abs, edid))
                continue
        # #coverage-race-subset: an author's per-race siblings (a hood riding
        # along included), each for the UBE counterparts of its own races. Not
        # beside a third-party reduction or the race-list rule.
        _narrow: dict = {}
        if not _listed and not _fewer:
            to_mint, _narrow = _subset.split(armo_abs, edid, to_mint, slots)
        if _listed:
            # What the guards above left of it (a hood riding along is not).
            race_list_ube.update({x: _listed[x] for x in to_mint if x in _listed})
            race_listed.append((armo_abs, edid))
        if _no_torso:
            world_partial.append((armo_abs, edid))
        if _wig_here:
            wigs_added.append((armo_abs, edid))   # #wig-body-pass
        if _kept_excluded:
            body_kept.append((armo_abs, edid))    # #wig-exclude-keep
        targets.append((armo_abs, plugin_case.get(armo_abs[0], armo_abs[0]),
                        to_mint))
        _tpd_state.targeted(to_mint, _fewer)   # #coverage-third-party-drawn
        _subset.targeted(to_mint, _narrow)     # #coverage-race-subset
        for x in to_mint:
            mint_set.setdefault(x, None)
            armo_slots[x] = armo_slots.get(x, 0) | slots   # #coverage-female-standin
    # #coverage-body-accessory, counted after #coverage-dead-armature (as the
    # race-list rule is): a hood that rule dropped was not minted, so it is not
    # "drawn on UBE with the body". The verdict is per armature, so a dead hood
    # is minted for no armour.
    accessory_added = [x for x in accessory_added if x not in dead_skipped]
    cloak_added = [x for x in cloak_added if x not in dead_skipped]

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
    if cover_hands_feet:
        # A source-primary hands/feet armature preserves its vanilla/custom race
        # list, so declare those race masters here or the refs dangle. Skyrim (the
        # common DefaultRace + vanilla playable list) is already a master; custom
        # races (a few content mods) get unioned, capped like textures -- a race
        # past the cap is simply skipped by src_to_patch_byte below (no dangle).
        for arma_abs in mint_set:
            _pl, _m2, _n2, _rn, _u = arma_win[arma_abs]
            if not (_arma_bod2_slots(_pl) & _BIPED_SLOT_HANDS_FEET_BITS):
                continue
            for nm in _arma_race_master_names(_pl, _m2, _n2):
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
    _mint_rec: dict = {}   # arma_abs -> minted Record (for post-prune sidecar fids)
    next_id = ESL_OWN_FORMID_MIN
    # (a `mint_name = out_path.with_suffix(".esp").name` was computed here and
    # never read; both mint blocks carried the same dead copy. Removed 2026-09-06.)
    preserved_count = 0
    preserve_fallbacks: list = []
    for arma_abs in mint_set:
        payload, m2, n2, _rn, _u = arma_win[arma_abs]
        # #coverage-female-standin: a non-body armature riding with body armour
        # (a hood) may keep its male mesh; body armatures never.
        _as_is = (_nonbody_male_as_is(payload, armo_slots.get(arma_abs, 0),
                                      _ube_exists, female_mesh_exists)
                  if _standin is not None else None)
        # Race targeting: BODY armatures -> UBE-primary + UBE-only (unchanged).
        # Unified pure-hands/feet armatures -> SOURCE-primary + preserved vanilla
        # races + UBE, via the shared helper, so a UBE actor's vanilla-resolving
        # hand/foot slot still matches (else invisible gauntlet). Same decision the
        # per-source path makes; the helper is golden-locked to it.
        # An armature the race-list rule took targets the UBE counterparts of the
        # races it lists instead of every UBE race, the first of them primary --
        # and a hands/feet one adds just those. #coverage-human-race-list
        _ube_prim, _ube_addl = ube_primary_patch, ube_races_patch
        _listed_ube = race_list_ube.get(arma_abs)
        if _listed_ube:
            _ube_addl = [(ube_byte << 24) | f for f in _listed_ube]
            _ube_prim = _ube_addl[0]
        # #coverage-third-party-drawn: only the UBE races another mod's
        # armature does not draw, the first of them primary.
        _fewer_ube = _tpd_state.races_for(arma_abs)
        if _fewer_ube:
            _ube_addl = [(ube_byte << 24) | f for f in _fewer_ube]
            _ube_prim = _ube_addl[0]
        # #coverage-race-subset: the UBE counterparts of its own races (never
        # with the two above: those armatures are minted unsplit).
        _sub_ube = _subset.races_for(arma_abs)
        if _sub_ube:
            _ube_addl = [(ube_byte << 24) | f for f in _sub_ube]
            _ube_prim = _ube_addl[0]
        _prim, _addl = _ube_prim, _ube_addl
        if cover_hands_feet and (_arma_bod2_slots(payload)
                                 & _BIPED_SLOT_HANDS_FEET_BITS):
            _src_rnam = None
            _src_addl: "list[int]" = []
            for _s, _d in esp.iter_subrecords(payload):
                # RNAM: >= 4 to match _summarize_arma (which gates to_mint); an
                # armature admitted there must parse its primary here too, else it
                # falls back to UBE-primary (invisible gauntlet on non-Breton UBE).
                if _s == b"RNAM" and len(_d) >= 4:
                    _src_rnam = struct.unpack_from("<I", _d, 0)[0]
                elif _s == ARMA_ADDITIONAL_RACE_SIG and len(_d) == 4:
                    _src_addl.append(struct.unpack("<I", _d)[0])
            # A malformed hands/feet armature with NO primary race -> keep the safe
            # UBE default rather than crash the whole body-coverage pass on None.
            if _src_rnam is not None:
                _s2p: "dict[int, int]" = {}
                for _i, _mm in enumerate(m2):
                    _j = pidx.get(_mm.lower())
                    if _j is not None:
                        _s2p[_i] = _j
                _jown = pidx.get(n2.lower())
                if _jown is not None:
                    _s2p[len(m2)] = _jown

                def _remap_race(fid: int, _map=_s2p) -> int:
                    # Mirror per-source _remap_src_fid_to_patch: patch-space fid if
                    # the master is held, else the fid unchanged (never raises).
                    top = (fid >> 24) & 0xFF
                    if top in _map:
                        return (_map[top] << 24) | (fid & 0xFFFFFF)
                    return fid

                _remap_prim = _remap_race
                if _listed_ube:
                    # Its primary is another race -- a custom one may live in a
                    # plugin past the master cap. Unheld, it falls back to the
                    # UBE primary rather than dangle. (A DefaultRace primary is
                    # Skyrim.esm's, always held.)
                    def _remap_prim(fid: int, _map=_s2p) -> int:
                        top = (fid >> 24) & 0xFF
                        return ((_map[top] << 24) | (fid & 0xFFFFFF)
                                if top in _map else 0)

                _prim, _addl = coverage_arma_race_targeting(
                    _arma_bod2_slots(payload), _src_rnam, _src_addl,
                    remap_src_fid=_remap_prim, src_to_patch_byte=_s2p,
                    ube_primary=_ube_prim, ube_additional=_ube_addl)
        minted_payload = None
        _declined: list = []     # per attempt: a failed preserve must not count
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
                    new_primary_rnam=_prim,
                    new_additional_race_fids=_addl,
                    alt_texture_fid_remap=_remap,             # MO?S -> patch space
                    converted_nif_exists=_ube_exists, strip_meshes_prefix=_strip,
                    keep_named_female=_female_guard, declined_log=_declined,
                    female_mesh_exists=female_mesh_exists,
                    female_standin=_standin, dead_female_male_as_is=_as_is,
                )
                preserved_count += 1
            except Exception as _e:      # unresolvable master -> strip fallback
                minted_payload = None
                _declined = []
                preserve_fallbacks.append((arma_abs[0], arma_abs[1], repr(_e)))
        if minted_payload is None:
            stripped = b"".join(
                esp.encode_subrecord(s, d)
                for s, d in esp.iter_subrecords(payload) if s not in STRIP_FULL)
            minted_payload = rebuild_arma_payload(
                stripped,
                new_primary_rnam=_prim,
                new_additional_race_fids=_addl,
                converted_nif_exists=_ube_exists,   # redirect model -> !UBE\ where converted
                strip_meshes_prefix=_strip,
                keep_named_female=_female_guard, declined_log=_declined,
                female_mesh_exists=female_mesh_exists,
                female_standin=_standin, dead_female_male_as_is=_as_is,
            )
        _file_declined(_declined, arma_abs, _flogs, female_mesh_exists,
                       payload, armo_slots.get(arma_abs, 0))
        _part = nude_redirect.get(arma_abs)
        if _part:
            # #coverage-nude-skin: the UBE body's own hand/foot, checked to resolve.
            minted_payload = _redirect_mod3(minted_payload, _part)
        if _twin:
            for d in _ube_twin_slots(payload, crp, ube_twin_exists,
                                     strip_meshes=_strip):
                if not (_part and d["slot"] == "MOD3"):
                    twin_slots.append({"arma": f"{arma_abs[0]}|{arma_abs[1]:X}", **d})
        new_fid = (own_byte << 24) | next_id
        next_id += 1
        new_edid = "UBE_MBD_{:X}".format(arma_abs[1])
        minted_payload = replace_arma_edid(minted_payload, new_edid[:90])
        _rec = esp.Record(
            sig=b"ARMA", flags=0, formid=new_fid, timestamp_vc=0,
            version_unk=0x002C, payload=minted_payload)
        new_arma_records.append(_rec)
        mint_set[arma_abs] = new_fid
        _mint_rec[arma_abs] = _rec

    # #coverage-esl-chunks -- emit ESL-sized pieces instead of one monolithic ESP.
    _res = _emit_coverage_pieces(
        out_path, targets, _mint_rec, patch_masters, own_byte=own_byte,
        author=author, description=description,
        ini_header=[
        "; cbbe-to-ube: UBE race coverage for mod-defined BODY armor variants.",
        "; Adds a minted UBE-primary ArmorAddon (redirected to the converted",
        "; !UBE mesh) to each body item whose winning armature lacked UBE races",
        "; (e.g. an overhaul's mod-defined armor variant reusing a vanilla armature).",
        ],
        preserve_textures=preserve_textures, master_data_dirs=master_data_dirs,
        emit_sidecar=emit_sidecar,
        # UBE body parts and twins are outside our output, checked to resolve.
        mesh_resolves=_outside_paths_predicate(
            [nude_redirect[a] for a in mint_set if a in nude_redirect]
            + [d["path"] for d in twin_slots]
            + [d["standin"] for d in female_standin]))   # #coverage-female-standin
    ini_lines = _res["ini_lines"]
    warnings = _res["validation_warnings"]

    return {
        "output": str(out_path),
        "pieces": _res["pieces"],
        "split_pieces": _res["split_pieces"],
        "ini_lines": ini_lines,
        "masters": _res["masters"],
        "minted_armas": _res["minted_armas"],
        "armo_targets": len(targets),
        "esl_flagged": _res["esl_flagged"],
        "candidates_scanned": len(armo_win),
        "validation_warnings": warnings,
        "textures_preserved": preserved_count,
        "texture_fallbacks": len(preserve_fallbacks),
        "withheld": withheld,
        "female_kept": female_kept,
        "female_dead_male": female_dead,
        # #coverage-female-standin
        "female_standin": female_standin,
        "female_male_nonbody": female_as_is,
        "female_dead_kept": female_dead_kept,
        "female_guard_skipped": [f"{a[0]}|{a[1]:X}" for a in guard_skipped],
        "female_guard_dropped": guard_dropped,
        "world_mesh_skipped": [f"{a[0]}|{a[1]:X}" for a in world_skipped],
        "world_mesh_dropped": world_dropped,
        # Targeted armours drawn with no body piece: their slot-32 armature was
        # withheld above, a hands/feet one was minted. #world-mesh-partial-report
        "world_mesh_partial": world_partial,
        "nude_redirected": [{"arma": f"{a[0]}|{a[1]:X}", "to": nude_redirect[a]}
                            for a in mint_set if a in nude_redirect],
        # Only armatures NO armour minted: one skipped for a race skin is still
        # minted when a costume's boots list it too (then it is redirected, the
        # line above), so it was not left out.
        "nude_skipped": [{"arma": f"{a[0]}|{a[1]:X}", "why": why}
                         for a, why in nude_skipped if a not in mint_set],
        "nude_dropped": nude_dropped,
        "ube_twin": twin_slots,
        "beast_variant_skipped": [f"{a[0]}|{a[1]:X}" for a in beast_skipped],
        "beast_variant_non_actor": [f"{a[0]}|{a[1]:X}" for a in beast_non_actor],
        "body_accessory": [f"{a[0]}|{a[1]:X}" for a in accessory_added],
        # #coverage-body-cloak: the cloak-named ones among them.
        "body_cloak": [f"{a[0]}|{a[1]:X}" for a in cloak_added],
        # #coverage-human-race-list: armours taken by the race-list rule.
        "race_listed": race_listed,
        # #wig-body-pass: wigs on an armour with a deforming slot, reported
        # with the non-body pass's wigs.
        "wigs": wigs_added,
        # #coverage-third-party-drawn
        **_tpd_state.stats(),
        # #coverage-race-subset
        **_subset.stats(),
        # #exclude-body-only, report only: withheld body pieces another mod's
        # SkyPatcher patch names (left to that patch).
        "exclusion_body_held": body_held,
        # #wig-exclude-keep: an excluded mod's wig the keep test kept -- a
        # non-body piece, reported with the non-body pass's kept pieces.
        "exclusion_nonbody_kept": body_kept,
        # #coverage-dead-armature
        "dead_armature_skipped": [f"{a[0]}|{a[1]:X}" for a in dead_skipped],
        "dead_dropped": dead_dropped,
    }


# What SkyPatcher splits a line on, as the INI reader in auto_convert
# (_skypatcher_fields / _skypatcher_forms) models it: `;` starts a comment, `:`
# separates `key=value` pairs, `,` the forms of a list, and `|` a plugin from
# its FormID. Windows allows `,` and `;` in a file name (not `:` or `|`; they
# are listed for a name that is not a file name). `=` is NOT one: the reader
# splits a pair once at its first `=` (seg.split("=", 1)), so an `=` inside a
# plugin name reads back whole, and guarding it would drop a working line.
_SKYPATCHER_DELIMITERS = ",;:|"


def _skypatcher_name_guard() -> bool:
    r"""#skypatcher-name-guard (2026-09-25): is a plugin name SkyPatcher would
    split kept out of the INI? Yes, by default.

    The merge wrote `filterByArmors=<plugin>|<id>:armorAddonsToAdd=...` with the
    plugin's file name as it is. A comma in it (`Armors, Extra.esp`) splits the
    filter into two forms, neither resolves, and the armour's links are lost
    in silence -- invisible on UBE actors, with a line in the INI that looks
    fine. A semicolon comments out the rest of the line. (An `=` in a name is
    harmless: a pair splits once, at its first `=`.) When it is the merged
    plugin's OWN name (--merged-name) that splits, every line names it, so no
    line is written and the run names that file, not the armour plugins.
    No other name can deliver the link: SkyPatcher addresses a form by plugin
    name and FormID (an EditorID needs a runtime EditorID cache, and a
    load-order-indexed FormID goes stale when the order changes). So such an
    armour gets no line, the same outcome as a link with no merged record, and
    it is counted in the link reconciliation and named in a run warning with
    the fix (rename the plugin). Live: 1 of 3,254 active plugins has a comma;
    it defines no armour the INI names, so the INI is unchanged.
    CBBE2UBE_NO_SKYPATCHER_NAME_GUARD=1 writes such lines again."""
    return not _flag("CBBE2UBE_NO_SKYPATCHER_NAME_GUARD", False)


def _skypatcher_name_splits(name: str) -> bool:
    """Would SkyPatcher split a line at `name`? #skypatcher-name-guard"""
    return any(c in str(name) for c in _SKYPATCHER_DELIMITERS)


def merge_patches(
    patch_paths: list[Path],
    output_path: str | Path,
    *,
    esl_flag: bool = True,
    author: str = "cbbe-to-ube merger",
    description: str = "Merged UBE compatibility patches",
    master_data_dirs: "list[Path] | None" = None,
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

    # (patch_path, pre-merge fid) -> merged Record (for the SkyPatcher-links
    # sidecar pass; final fids read AFTER prune renumbering).
    merged_rec_by_key: "dict[tuple[Path, int], esp.Record]" = {}

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
    rest.sort(key=lambda m: _master_sort_key(m, master_data_dirs))
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
    # (record flags, remapped-payload-minus-EDID) -> (keeper_fid, keeper_record)
    _arma_dedup_pool: "dict[tuple, tuple]" = {}
    dedup_collapsed = 0
    for patch_path, pe in patches:
        byte_remap = patch_master_remap[patch_path]
        patch_own_byte = len(pe.header.masters)

        for grp in pe.groups:
            if grp.label != b"ARMA":
                continue   # SkyPatcher-only: patches carry minted ARMAs, no ARMO
            for rec in grp.records:
                old_top = (rec.formid >> 24) & 0xFF

                new_payload = _rewrite_payload_for_merge(
                    rec.payload, patch_path, byte_remap, formid_remap)

                # RECORD DEDUP: an OWN minted ARMA whose merged payload is byte-
                # identical (ignoring EDID) to one already emitted is the SAME
                # render -> reuse that keeper record. Repoint this source's FormID
                # and merged-record mapping at the keeper (so the ARMO refs AND the
                # SkyPatcher INI, which both read merged_rec_by_key, land on it).
                is_own_new = (old_top == patch_own_byte)
                dk = None
                if is_own_new and MERGE_DEDUP_ARMAS:
                    dk = (rec.flags, _arma_merge_dedup_key(new_payload))
                    keeper = _arma_dedup_pool.get(dk)
                    if keeper is not None:
                        formid_remap[(patch_path, rec.formid)] = keeper[0]
                        merged_rec_by_key[(patch_path, rec.formid)] = keeper[1]
                        dedup_collapsed += 1
                        continue

                # Compute new FormID for this record
                if (patch_path, rec.formid) in formid_remap:
                    new_fid = formid_remap[(patch_path, rec.formid)]
                else:
                    new_top = byte_remap.get(old_top, old_top)
                    new_fid = (new_top << 24) | (rec.formid & 0xFFFFFF)

                new_rec = esp.Record(
                    sig=grp.label,
                    flags=rec.flags,
                    formid=new_fid,
                    timestamp_vc=rec.timestamp_vc,
                    version_unk=rec.version_unk,
                    payload=new_payload,
                )
                # (patch, pre-merge fid) -> merged Record. Read rec.formid at
                # the END (prune renumbers) -- used by the SkyPatcher-links pass.
                merged_rec_by_key[(patch_path, rec.formid)] = new_rec
                new_arma_records.append(new_rec)
                if ((new_fid >> 24) & 0xFF) == own_byte_merged:
                    own_arma_count += 1
                    if dk is not None:
                        _arma_dedup_pool[dk] = (new_fid, new_rec)

    # ----- Step 4.5: ESL dense re-pack (post-dedup) -----
    # Own FormIDs were allocated PRE-dedup in Step 3 (dense 0x800..), so record
    # dedup punches gaps and `as_esl` was decided on the pre-dedup total. A single
    # patch that mints MORE than the ESL cap but dedups BELOW it (heavy cross-
    # source overlap under unified coverage) is then wrongly downgraded to a full
    # ESP AND its own-FormID max can sit past 0xFFF. Re-pack the EMITTED own ARMAs
    # densely from ESL_OWN_FORMID_MIN and re-decide ESL on the EMITTED count, so
    # such a piece is ESL-clean (max == 0x800 + count - 1 <= 0xFFF). Own ARMAs are
    # referenced only by the SkyPatcher INI (via merged_rec_by_key -> Record.formid,
    # read below), so an in-place renumber is complete and needs no ref rewrite.
    if esl_flag and not as_esl and own_arma_count <= ESL_MAX_OWN_RECORDS:
        _next = ESL_OWN_FORMID_MIN
        for _rec in new_arma_records:
            if ((_rec.formid >> 24) & 0xFF) == own_byte_merged:
                _rec.formid = (own_byte_merged << 24) | _next
                _next += 1
        next_own_id = _next
        as_esl = True

    # ----- Step 5: emit merged ESP -----
    # TES4 flags. `as_esl` reflects the EMITTED own-ARMA count after any post-dedup
    # re-pack above, so the flag matches the FormID range we actually wrote.
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
    # SkyPatcher-only: the Combined is pure minted-ARMA -- no ARMO group at all.
    if new_arma_records:
        out_esp.groups.append(esp.Group(label=b"ARMA", records=new_arma_records))

    # Prune any unused masters that ended up in the union but no record
    # actually references (shouldn't happen normally, but safety).
    prune_unused_masters(out_esp)

    out_esp.save(out_path)

    # ---- SkyPatcher: per-patch link sidecars -> final INI lines ----
    # Emitted from the .skypatcher.json sidecars each patch carries. Dedup by
    # (armo, source-armature) so the same vanilla armature minted by many patches
    # is added ONCE (first-writer-wins). Pass a shared
    # `_sp_seen_pairs` set across split pieces for cross-piece dedup.
    sp_ini: "list[str]" = []
    sp_by_armo: "dict[tuple[str, int], list[int]]" = {}
    seen_pairs = _sp_seen_pairs if _sp_seen_pairs is not None else set()
    import json as _json
    # Every sidecar link either becomes an INI line or is COUNTED as dropped,
    # by reason. An armature link that vanishes here is an invisible armour
    # piece in game, and until 2026-08-11 all three drop paths below were bare
    # `continue`s: a shipped pack was found with 0 of one mod's 114 sidecar
    # links emitted and nothing anywhere saying so. The run reconciled 21,004
    # sidecar entries to 10,297 INI lines and reported neither number.
    sp_seen_links = 0
    sp_drop_norec = 0
    sp_drop_dup = 0
    sp_bad_sidecar: "list[str]" = []
    for patch_path, _pe in patches:
        sc = Path(str(patch_path) + ".skypatcher.json")
        if not sc.is_file():
            continue          # patch legitimately produced no links
        try:
            doc = _json.loads(sc.read_text(encoding="utf-8"))
        except Exception as _sce:
            # NOT silent: this discards every link the patch recorded.
            sp_bad_sidecar.append(f"{sc.name}: {type(_sce).__name__}")
            continue
        for ent in doc:
            try:
                d, l = ent["armo"][0], int(ent["armo"][1])
            except Exception:
                continue
            for a in ent.get("adds", []):
                sp_seen_links += 1
                rec = merged_rec_by_key.get((patch_path, int(a.get("fid", -1))))
                if rec is None:
                    # PATHOLOGICAL, unlike the other two drops: the patch minted
                    # this ARMA and recorded the link, but the merged output has
                    # no record for it. The armour it belongs to gets no UBE
                    # armature at all -> equippable and invisible.
                    sp_drop_norec += 1
                    continue
                src = a.get("src") or ["", -1]
                pair = ((str(d), l), (str(src[0]), int(src[1])))
                if pair in seen_pairs:
                    sp_drop_dup += 1   # legitimate: first-writer-wins
                    continue          # same armature already added elsewhere
                seen_pairs.add(pair)
                sp_by_armo.setdefault((str(d), l), []).append(rec)
    # Drop links that would add RENDER-IDENTICAL armatures to the same ARMO.
    #
    # This is what `dedup_armo_armature_refs` used to do by walking ARMO records.
    # Under SkyPatcher-only delivery the Combined contains no ARMO group at all,
    # so that pass walks nothing and returns 0 on every run -- it has been a
    # silent no-op since the pivot, which is how a handful of double-render
    # cases got through. The equivalent surface now is the INI: two links on one
    # armor pointing at armatures with the same render identity make the engine
    # render the same mesh twice (z-fighting / doubled cloth). The merge's own
    # dedup only collapses BYTE-identical payloads, which is strictly narrower
    # than `_arma_dedup_identity` (race + gendered meshes + slot flags), so it
    # does not cover this. Measured on a real modlist before this fix: 3 of 9379
    # INI lines added render-identical armatures.
    # Group on ident[:7] and keep the MOST COMPLETE member (highest ident[7]),
    # per _arma_dedup_identity's documented contract: "The group key is [:7];
    # [7] is the completeness tiebreak." Using the full 8-tuple as the key
    # instead would treat two armatures that differ ONLY in subrecord count as
    # distinct -- under-deduping exactly the near-identical pairs this exists to
    # collapse -- and would keep whichever was seen first rather than the one
    # carrying the most data.
    # #race-subset-dedup-agree: the key holds the primary race only, so a
    # member listing a race no kept member lists is kept too -- dropping it
    # would leave that race with nothing for the armour. Only UBE races
    # count (#guard-ube-races).
    _agree = _race_subset_dedup_agree()
    # #guard-ube-races: only a UBE race can go missing on a UBE actor.
    _ube_only = _guard_ube_races()
    _om = list(out_esp.header.masters)

    def _guard_races(rec) -> frozenset:
        if _ube_only:
            return _arma_ube_race_set(rec.payload, _om, out_path.name)
        return _arma_race_set(rec.payload)
    sp_dropped = 0
    sp_kept_races = 0
    for key, recs in sp_by_armo.items():
        if len(recs) < 2:
            continue
        groups: dict = {}
        unreadable = []
        for rec in recs:
            try:
                ident = _arma_dedup_identity(rec.payload)
            except Exception:
                unreadable.append(rec)   # never drop blind
                continue
            if ident is None:
                unreadable.append(rec)   # vanilla/master ARMA -> leave alone
                continue
            groups.setdefault(ident[:7], []).append((rec, ident[7]))
        kept = list(unreadable)
        for _k, members in groups.items():
            bi = max(range(len(members)), key=lambda i: members[i][1])
            best = members[bi][0]
            kept.append(best)
            if not _agree:
                sp_dropped += len(members) - 1
                continue
            drawn = set(_guard_races(best))
            for i, (rec, _n) in enumerate(members):
                if i == bi:
                    continue
                races = _guard_races(rec)
                if races <= drawn:
                    sp_dropped += 1
                else:
                    kept.append(rec)
                    drawn |= races
                    sp_kept_races += 1
        # preserve the original link order (stable output/INI diffs)
        order = {id(r): i for i, r in enumerate(recs)}
        sp_by_armo[key] = sorted(kept, key=lambda r: order.get(id(r), 0))
    # A name SkyPatcher would split is no line at all: its links are counted,
    # and the caller names the plugin. #skypatcher-name-guard
    _name_guard = _skypatcher_name_guard()
    _out_unsafe = _name_guard and _skypatcher_name_splits(out_path.name)
    sp_unsafe: "list[str]" = []
    sp_drop_unsafe = 0
    for (d, l), recs in sorted(sp_by_armo.items()):
        if _name_guard and (_skypatcher_name_splits(d) or _out_unsafe):
            sp_unsafe.append(f"{d}|{l:06X}")
            sp_drop_unsafe += len(recs)
            continue
        adds = ",".join("{}|{:06X}".format(out_path.name, r.formid & 0xFFFFFF)
                        for r in recs)
        sp_ini.append("filterByArmors={}|{:06X}:armorAddonsToAdd={}".format(
            d, l, adds))

    return {
        "output": str(out_path),
        "masters": out_esp.header.masters,
        "skypatcher_ini_lines": sp_ini,
        "skypatcher_targets": len(sp_by_armo) - len(sp_unsafe),
        # Link reconciliation. `seen` must equal emitted + the four drop
        # reasons; a mismatch means a fifth path is losing links silently.
        "sp_links_seen": sp_seen_links,
        "sp_links_emitted": sum(len(v) for v in sp_by_armo.values()) - sp_drop_unsafe,
        "sp_dropped_no_record": sp_drop_norec,
        "sp_dropped_duplicate_pair": sp_drop_dup,
        "sp_dropped_render_identical": sp_dropped,
        # #race-subset-dedup-agree: render-identical links kept (and emitted)
        # because they list a race no kept link of their group lists.
        "sp_kept_other_races": sp_kept_races,
        "sp_dropped_unsafe_name": sp_drop_unsafe,
        "sp_unsafe_name_targets": sp_unsafe,
        "sp_unsafe_output_names": [out_path.name] if _out_unsafe and sp_unsafe else [],
        "sp_unreadable_sidecars": sp_bad_sidecar,
        "merged_patch_count": len(patches),
        "total_arma_records": len(new_arma_records),
        "own_arma_records": own_arma_count,
        "dedup_collapsed_armas": dedup_collapsed,
        "total_armo_records": 0,       # SkyPatcher-only: never any ARMO records
        "esl_flagged": bool(tes4_flags & TES4_FLAG_ESL),
        "esl_slots_used": own_arma_count,
        "esl_slots_max": ESL_MAX_OWN_RECORDS,
        # Reflect the FINAL flag (after the Step 4.5 post-dedup re-pack), not the
        # pre-dedup `fits_esl` -- a piece re-packed back to ESL is NOT downgraded.
        "downgraded_to_full_esp": bool(esl_flag and not as_esl),
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


def report_link_reconciliation(stats: dict) -> "list[str]":
    """Lines accounting for every SkyPatcher armature link the merge consumed.

    A link that disappears between a patch's sidecar and the INI is an armour
    piece with no UBE armature -- equippable and INVISIBLE in game, on a UBE
    race. That is a silent failure by nature: the mesh converts, the report says
    "converted", and nothing else ever mentions the piece again. It shipped
    exactly once (2026-08-11: one mod's 114 sidecar links all absent from the
    INI, found only because a user reported one invisible piece), and the run
    that did it printed no number that could have caught it.

    `no_record` is the pathological one and is reported as `!!`: the patch
    minted the ARMA and recorded the link, but no merged record answers to it.
    The other two drops are by design -- `duplicate_pair` is first-writer-wins
    across patches, `render_identical` stops one armour rendering the same mesh
    twice -- so they are reported as plain counts, not warnings.
    `sp_dropped_unsafe_name` (#skypatcher-name-guard) is counted here; the caller warns,
    naming each plugin.
    """
    seen = int(stats.get("sp_links_seen", 0) or 0)
    if not seen:
        return []
    emitted = int(stats.get("sp_links_emitted", 0) or 0)
    norec = int(stats.get("sp_dropped_no_record", 0) or 0)
    dup = int(stats.get("sp_dropped_duplicate_pair", 0) or 0)
    ident = int(stats.get("sp_dropped_render_identical", 0) or 0)
    unsafe = int(stats.get("sp_dropped_unsafe_name", 0) or 0)
    bad = list(stats.get("sp_unreadable_sidecars") or [])
    out = [f"  armature links: {seen} recorded -> {emitted} emitted "
           f"({dup} duplicate, {ident} render-identical, {norec} unresolved"
           + (f", {unsafe} on a plugin name SkyPatcher cannot read" if unsafe else "")
           + ")"]
    # #race-subset-dedup-agree: kept links are emitted, so not in the balance.
    kept_races = int(stats.get("sp_kept_other_races", 0) or 0)
    if kept_races:
        out.append(f"  {kept_races} render-identical armature link(s) kept: they "
                   f"list races the other copy does not")
    if norec:
        out.append(f"  !! {norec} armature link(s) had NO merged record -- "
                   f"those armor pieces get no UBE armature and will be "
                   f"INVISIBLE on a UBE race")
    for b in bad:
        out.append(f"  !! unreadable link sidecar {b} -- every link that patch "
                   f"recorded is lost")
    # The identity that makes the count trustworthy: if these do not agree, a
    # path is losing links that none of the three reasons above describes.
    if emitted + dup + ident + norec + unsafe != seen:
        out.append(f"  !! link accounting does not balance "
                   f"({emitted}+{dup}+{ident}+{norec}"
                   + (f"+{unsafe}" if unsafe else "") + f" != {seen}) -- a drop "
                   f"path is unaccounted for")
    return out


def merge_patches_split(
    patch_paths,
    output_path,
    *,
    esl_flag: bool = True,
    author: str = "cbbe-to-ube merger",
    description: str = "Merged UBE compatibility patches",
    master_data_dirs=None,
    owns_output_dir: bool = False,
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

    def _drop_stale_pieces(keep: "set[str]") -> None:
        """Delete numbered split pieces this run did NOT write.

        MUST run on the single-piece paths too, not just the split branch. When a
        run drops back under the ESL cap (N pieces -> 1) the old `...2.esp` /
        `...3.esp` otherwise survive holding the PREVIOUS run's records. The user
        was told to enable every piece, so they stay enabled; the new SkyPatcher
        INI targets only the merged stem, so the orphan contributes unreferenced
        duplicates. Worse, every post-merge pass globs the whole piece family, so
        `resort_masters_all` rewrites the orphan and `postflight_validate_combined`
        then reports it "validated clean" -- the stale-Combined guard certifying a
        stale file.

        Matched narrowly on `<stem><digits><suffix>` rather than `<stem>*<suffix>`:
        this deletes files in the user's output directory, and the broad glob
        would also swallow e.g. `<stem>_backup.esp`.
        """
        stem, suffix = out_path.stem, (out_path.suffix or ".esp")
        for f in out_path.parent.glob(f"{stem}*{suffix}"):
            if f.name in keep:
                continue
            # The Combined itself ("") and anything not of the family (None)
            # stay. The matcher is shared with the post-merge passes.
            # #piece-family-match
            if not _combined_piece_tail(f.name, stem, suffix):
                continue          # not one of our numbered pieces -- leave it
            try:
                f.unlink()
            except OSError:
                pass

    def _single(esl):
        s = merge_patches(
            plist, out_path, esl_flag=esl, author=author,
            description=description, master_data_dirs=master_data_dirs,
            _sp_seen_pairs=_sp_seen)
        s["pieces"] = [out_path.name]
        s["split_pieces"] = 1
        s["piece_stats"] = [s]
        # Only when the caller OWNS the output directory. merge_patches_split is
        # also the standalone `merge` subcommand's entry point, where -o is an
        # arbitrary user path -- and this unlinks `<stem><digits><suffix>`
        # siblings. Cleaning up there would delete a user's own
        # `MyPatch2.esp`/`MyPatch3.esp` that this tool never wrote. The SPLIT
        # branch below still cleans unconditionally, matching pre-existing
        # behaviour: it genuinely wrote those numbered pieces this run.
        if owns_output_dir:
            _drop_stale_pieces({out_path.name})
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
            master_data_dirs=master_data_dirs,
            _sp_seen_pairs=_sp_seen)
        piece_stats.append(st)
        piece_names.append(piece_path.name)

    # Remove stale pieces left by a prior, larger split (e.g. 3 -> 2 pieces).
    _drop_stale_pieces(set(piece_names))

    return {
        "output": str(out_path),
        "pieces": piece_names,
        "split_pieces": n_pieces,
        "merged_patch_count": len(plist),
        "masters": piece_stats[0].get("masters", []),
        "total_arma_records": sum(s.get("total_arma_records", 0) for s in piece_stats),
        "own_arma_records": sum(s.get("own_arma_records", 0) for s in piece_stats),
        "total_armo_records": sum(s.get("total_armo_records", 0) for s in piece_stats),
        "esl_flagged": all(s.get("esl_flagged") for s in piece_stats),
        "all_pieces_esl": all(s.get("esl_flagged") for s in piece_stats),
        "esl_slots_max": ESL_MAX_OWN_RECORDS,
        "downgraded_to_full_esp": any(s.get("downgraded_to_full_esp") for s in piece_stats),
        "skypatcher_ini_lines": [l for s in piece_stats
                                 for l in s.get("skypatcher_ini_lines", [])],
        "skypatcher_targets": sum(s.get("skypatcher_targets", 0)
                                  for s in piece_stats),
        "sp_links_seen": sum(s.get("sp_links_seen", 0) for s in piece_stats),
        "sp_links_emitted": sum(s.get("sp_links_emitted", 0)
                                for s in piece_stats),
        "sp_dropped_no_record": sum(s.get("sp_dropped_no_record", 0)
                                    for s in piece_stats),
        "sp_dropped_duplicate_pair": sum(s.get("sp_dropped_duplicate_pair", 0)
                                         for s in piece_stats),
        "sp_dropped_render_identical": sum(
            s.get("sp_dropped_render_identical", 0) for s in piece_stats),
        "sp_kept_other_races": sum(s.get("sp_kept_other_races", 0)
                                   for s in piece_stats),
        "sp_dropped_unsafe_name": sum(s.get("sp_dropped_unsafe_name", 0)
                                      for s in piece_stats),
        "sp_unsafe_name_targets": [x for s in piece_stats
                                   for x in s.get("sp_unsafe_name_targets", [])],
        "sp_unsafe_output_names": [x for s in piece_stats
                                   for x in s.get("sp_unsafe_output_names", [])],
        "sp_unreadable_sidecars": [x for s in piece_stats
                                   for x in s.get("sp_unreadable_sidecars", [])],
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
