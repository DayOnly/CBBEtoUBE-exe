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

"""Persistent per-mod exclusions for the `auto` pipeline.

An exclusion is a mod the converter must SKIP on an All-mods run -- the
complement of `--only-mods`. The point is SAFETY: converting a mod that is
already built for UBE would double-convert its meshes and break them, so those
mods (and any the user knows to leave alone) are excluded up front.

This module is pure data + logic (no tkinter, no converter imports), so it is
unit-testable without a display and can back both the GUI and a CLI flag. The
selections persist in a small JSON keyed by mod name; the "have I reviewed
exclusions this session?" gate is enforced by the GUI, not stored here.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DOMAINS = ("armor", "overlay")

# The canned reason attached to a mod the detector flags. The mesh signal (UBE
# leg scale bones) also fires on other advanced bodies (e.g. BHUNP), which
# likewise must not be run through the CBBE->UBE conversion.
UBE_NATIVE_REASON = ("Already rigged for UBE (or another advanced body) -- "
                     "converting it as CBBE would break it.")

# A UBE token that is not part of a larger word (digits are allowed, so "ube2"
# and "ube 2.0" match but "cube"/"tube"/"rube" do not).
_UBE_TOKEN = re.compile(r"(?<![a-z])ube(?![a-z])", re.IGNORECASE)


def config_path() -> Path:
    """Where the exclusions JSON lives. CBBE2UBE_EXCLUSIONS overrides; else next
    to the exe (frozen) or the repo root (source). Survives an exe redeploy."""
    override = os.environ.get("CBBE2UBE_EXCLUSIONS", "").strip()
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "CBBEtoUBE_exclusions.json"


def empty() -> dict:
    """A fresh, valid state: no exclusions in either domain."""
    return {d: {} for d in DOMAINS}


def load(path=None) -> dict:
    """Return {'armor': {name: {reason, source}}, 'overlay': {...}}. A malformed
    or absent file yields an empty state; unknown domains/keys are dropped."""
    state = empty()
    p = Path(path) if path is not None else config_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return state
    if not isinstance(raw, dict):
        return state
    for d in DOMAINS:
        entries = raw.get(d)
        if not isinstance(entries, dict):
            continue
        for name, meta in entries.items():
            if not isinstance(name, str) or not name.strip():
                continue
            meta = meta if isinstance(meta, dict) else {}
            state[d][name] = {
                "reason": str(meta.get("reason", "")),
                "source": str(meta.get("source", "manual")),
            }
    return state


def save(state: dict, path=None) -> bool:
    """Persist `state`. Returns True on success."""
    out = {d: state.get(d, {}) for d in DOMAINS}
    p = Path(path) if path is not None else config_path()
    try:
        # Atomic (temp+rename): a torn write here would leave a truncated JSON
        # that load() degrades to empty() -> silently dropping EVERY saved
        # exclusion (which exist to stop re-converting already-UBE mods).
        from .atomic_io import atomic_write_bytes
        atomic_write_bytes(
            p, json.dumps(out, indent=2, sort_keys=True).encode("utf-8"))
        return True
    except Exception:
        return False


def excluded_names(state: dict, domain: str) -> "list[str]":
    """Mod names excluded in `domain`, sorted (case-insensitive)."""
    return sorted(state.get(domain, {}), key=str.lower)


def set_excluded(state: dict, domain: str, names,
                 *, reason: str = "", source: str = "manual") -> dict:
    """Replace `domain`'s exclusion set with `names`. Existing entries keep their
    reason/source; new ones get the given reason/source. Returns `state`."""
    if domain not in DOMAINS:
        return state
    keep = set(names)
    old = state.get(domain, {})
    new: dict = {}
    for n in keep:
        if not isinstance(n, str) or not n.strip():
            continue
        new[n] = old.get(n, {"reason": reason, "source": source})
    state[domain] = new
    return state


def scan_names(mod_names, existing=()) -> "list[dict]":
    """Fast, name-only pass: propose mods whose name reads as UBE-native (a `ube`
    token that is not part of a larger word). LOW confidence -- meant to be
    reviewed, never auto-applied. Skips names already excluded. Returns
    [{'name', 'reason', 'confidence'}] in input order.

    (The deep, mesh/bone-based detector is a separate step; this keeps the Scan
    button useful before it lands.)"""
    seen = {n.lower() for n in existing}
    out: "list[dict]" = []
    for name in mod_names:
        if not isinstance(name, str) or name.lower() in seen:
            continue
        if _UBE_TOKEN.search(name):
            out.append({"name": name,
                        "reason": "Name suggests UBE-native -- review.",
                        "confidence": "low"})
    return out
