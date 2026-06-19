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

"""Authoritative overlay slot identification, the way RaceMenu itself does it.

RaceMenu overlay packs don't encode an overlay's slot in the texture filename --
they register each texture into a slot at runtime via a RaceMenuBase script that
calls AddWarPaint / AddBodyPaint / AddHandPaint / AddFeetPaint (name, texture).
RaceMenu's menu lists are built from those calls. So parsing those calls gives
the SAME body/hands/feet/face mapping the game uses -- no filename guessing.

A texture can be registered in MORE THAN ONE slot (e.g. a body paint reused on
the feet slot), so this returns a SET of slots per texture. classify_overlay's
keyword heuristic remains the FALLBACK for overlays no script registers (a pack
the user applies by hand).

`.psc` source is parsed here (regex). Some packs ship only the compiled `.pex`;
those are handled by overlay_slots_pex (a separate, heavier reader)."""
from __future__ import annotations

import re

from . import paths as _paths

# AddXPaint Papyrus call -> our region name. Face/warpaint rides the head UV.
_PAINT_SLOT = {
    "warpaint": "head",
    "bodypaint": "body",
    "handpaint": "hands",
    "feetpaint": "feet",
}

# AddWarPaint("Display Name", "Actors\\Character\\Overlays\\...\\x.dds")
_PAINT_RX = re.compile(
    r'Add(WarPaint|BodyPaint|HandPaint|FeetPaint)\s*\(\s*'
    r'"[^"]*"\s*,\s*"([^"]+)"',
    re.IGNORECASE,
)

_slot_map_cache: dict = {}      # mods-root str -> {rel_texture_path: frozenset(slots)}


def normalize_script_texpath(p: str) -> str:
    """A RaceMenu script's texture arg (`Actors\\Character\\Overlays\\x.dds`,
    sometimes with a leading slash) -> the rel path discover_overlays uses
    (`textures/actors/character/overlays/x.dds`, forward slash, lowercased)."""
    s = p.replace("\\", "/").lower().strip().lstrip("/")
    s = re.sub(r"/{2,}", "/", s)        # .psc literals double their backslashes
    if s.startswith("actors/"):
        s = "textures/" + s
    return s


def iter_paint_calls(text: str):
    """Yield (slot, rel_texture_path) for every AddXPaint call in a .psc body."""
    for m in _PAINT_RX.finditer(text):
        slot = _PAINT_SLOT[m.group(1).lower()]
        yield slot, normalize_script_texpath(m.group(2))


def _script_has_paint(text: str) -> bool:
    """Cheap pre-filter: does this script body contain an AddXPaint call at all?
    Papyrus identifiers are CASE-INSENSITIVE, so match case-insensitively -- a
    pack calling `AddBodypaint(` (lowercase 'p', perfectly valid and works
    in-game) was being skipped by a case-sensitive `"Paint("` test, so its whole
    overlay set went unregistered."""
    return "paint(" in text.lower()


def _add(mapping: dict, text: str) -> None:
    for slot, rel in iter_paint_calls(text):
        mapping.setdefault(rel, set()).add(slot)


def build_script_slot_map(layout=None) -> dict:
    """Scan every enabled mod's RaceMenuBase scripts (loose + BSA `.psc`) and
    return {rel_texture_path: frozenset(slots)} -- the slots RaceMenu registers
    each overlay texture into. Cached per mods root. Empty if no mods root."""
    from pathlib import Path
    mr = _paths.mods_root()
    key = str(mr) if mr is not None else ""
    cached = _slot_map_cache.get(key)
    if cached is not None:
        return cached
    acc: dict = {}
    if mr is not None:
        from .bsa_strings import BSAArchive
        ordered = _paths.enabled_mods_ordered(layout)
        names = ordered if ordered is not None else sorted(
            d.name for d in mr.iterdir() if d.is_dir())
        for mod_name in names:
            mod = mr / mod_name
            if not mod.is_dir():
                continue
            for f in mod.rglob("*.psc"):
                try:
                    txt = f.read_text("utf-8", "replace")
                except OSError:
                    continue
                if _script_has_paint(txt):
                    _add(acc, txt)
            for bsa in mod.glob("*.bsa"):
                try:
                    arc = BSAArchive(bsa, eager=False)
                    files = arc.list_files("")
                except Exception:
                    continue
                for n in files:
                    if not n.lower().endswith(".psc"):
                        continue
                    try:
                        data = arc.read_file(n)
                    except Exception:
                        continue
                    txt = (data.decode("utf-8", "replace")
                           if isinstance(data, (bytes, bytearray)) else str(data))
                    if _script_has_paint(txt):
                        _add(acc, txt)
    out = {rel: frozenset(slots) for rel, slots in acc.items()}
    _slot_map_cache[key] = out
    return out


def script_slots(rel_path: str, layout=None) -> "frozenset | None":
    """The slot set RaceMenu registers `rel_path` into, or None if no script
    registers it (caller should fall back to the keyword classifier)."""
    return build_script_slot_map(layout).get(rel_path.replace("\\", "/").lower())
