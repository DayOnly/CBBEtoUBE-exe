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

"""The reference-body choice behind the GUI's "Reference bodies" dialog --
everything except the widgets, so it is testable without Tk.

Before a conversion the user confirms, or changes, the two bodies the fit
uses: the CBBE 3BA body it starts FROM and the UBE body it aims AT. Each
dropdown offers every candidate the modlist has (src/zeroed_body.list_bodies)
and starts on the verified zeroed body the game loads. The choice reaches the
conversion as explicit overrides in that run's child environment --
CBBE2UBE_CBBE_BODY_0/_1 and CBBE2UBE_UBE_BODY_0/_1 -- which the fit's body
lookups honour first: the CBBE body the warp starts from, the UBE body it aims
at, and the UBE body injected under a body-swap garment.

NOT the preset bake or the physics chain lift. Those use the UBE body the
user's BodySlide build installed (`_find_user_preset_body`) ON PURPOSE: the
bake adds the user's preset (their build minus the template) to the garment,
and the chain lift clears the body the player wears. When that build carries a
preset it is a different body from the zeroed reference, and pointing either
at the reference would silently drop the preset.

Once the dialog has run, its result covers EVERY body variable: a chosen body
writes both weights (even the default, so the run uses exactly the files the
dialog verified and no worker repeats the ~7s check), and a kind left unchosen
writes them EMPTY, so an override inherited from the environment -- one the
dialog refused -- cannot win behind its back. The lookups read empty as unset.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import zeroed_body as _zb

KINDS = ("cbbe", "ube")
ENV = {
    "cbbe": ("CBBE2UBE_CBBE_BODY_0", "CBBE2UBE_CBBE_BODY_1"),
    "ube": ("CBBE2UBE_UBE_BODY_0", "CBBE2UBE_UBE_BODY_1"),
}
_SETTINGS_UBE = "CBBE2UBE_UBE_BODY"      # the Paths tab's "UBE body reference NIF"
HEADINGS = {"cbbe": "CBBE 3BA body -- the fit starts from it",
            "ube": "UBE body -- the fit aims at it"}
_OWN_LOOKUP = ("the converter's own lookup decides (the zeroed build the game "
               "loads, then a body found by name)")


def _weight1(p: str) -> str:
    """The weight-1 file of a body path that names either weight."""
    q = Path(p)
    return str(q.with_name(q.stem[:-2] + "_1" + q.suffix)) if q.stem.endswith("_0") else p


def _same(a, b) -> bool:
    return a is not None and b is not None and \
        str(Path(os.path.realpath(a))).lower() == str(Path(os.path.realpath(b))).lower()


def overrides(base_env: dict) -> "list[tuple[str, str, str]]":
    """[(kind, label, weight-1 path)] for bodies the environment the child
    would get already names -- shown as candidates, and preselected when they
    are usable, so the dialog never hides an existing choice. Each is labelled
    with the variable it came from; a _0 override pointing at another folder
    than the _1 one is listed on its own rather than folded into it."""
    out = []
    for kind in KINDS:
        v0, v1 = ENV[kind]
        p1, p0 = base_env.get(v1), base_env.get(v0)
        if p1:
            out.append((kind, "override " + v1, _weight1(p1)))
        if p0 and not (p1 and _same(Path(p0).parent, Path(p1).parent)):
            out.append((kind, "override " + v0, _weight1(p0)))
    bare = base_env.get(_SETTINGS_UBE)
    if bare:
        out.append(("ube", "Settings: UBE body reference NIF", _weight1(bare)))
    return out


def resolve(base_env: dict, **instance) -> "dict[str, _zb.BodyListing]":
    """The slow part (~7s on a real modlist): list and check every candidate.
    Run it OFF the UI thread. `instance` = mods_root/order/overwrite/data_dirs
    for tests; omitted, the discovered MO2 instance is used."""
    return _zb.list_bodies(KINDS, extra=overrides(base_env), **instance)


def initial_choice(listing: "_zb.BodyListing", base_env: dict):
    """What the dropdown starts on: a usable body an override already names,
    else the verified zeroed body the game loads, else the pair the game
    loads if it is at least the right family (flagged), else nothing."""
    sel = [c for c in listing.candidates if c.selectable]
    for kind, _label, p in overrides(base_env):
        if kind == listing.kind:
            hit = next((c for c in sel if _same(c.path_1, p)), None)
            if hit is not None:
                return hit
    if listing.default is not None:
        return listing.default
    return next((c for c in sel if c.game_loads), None)


def label_for(c: "_zb.BodyCandidate") -> str:
    """One dropdown line."""
    where = "  (the game loads it)" if c.game_loads else ""
    if c.status == "zeroed":
        return f"[zeroed] {c.provider}{where}"
    if c.status == "not-zeroed":
        off = f", off by up to {c.max_dev:.2f}u" if c.max_dev is not None else ""
        return f"[NOT zeroed{off}] {c.provider}{where}"
    return f"[not checked] {c.provider}{where}"


def labels_for(cands) -> "list[str]":
    """The dropdown lines for `cands`, each UNIQUE -- the choice is read back
    by its text, so two equal lines (two game Data folders, say) would pin
    the first one's files whichever was picked. A repeated line gets its
    folder appended."""
    base = [label_for(c) for c in cands]
    out = []
    for c, lab in zip(cands, base):
        if base.count(lab) > 1:
            lab = f"{lab}  -- {Path(c.path_1).parent}"
        while lab in out:                 # the same folder twice: still distinct
            lab += " *"
        out.append(lab)
    return out


def detail_for(c: "_zb.BodyCandidate | None", offered: int = 0) -> str:
    """The lines under the dropdown for the selected body (`offered` = how
    many bodies the dropdown holds, for the nothing-selected text)."""
    if c is None:
        if offered:
            return ("Nothing selected: pick a body above, or leave it blank and "
                    + _OWN_LOOKUP + ".")
        return "No usable body was found: " + _OWN_LOOKUP + "."
    lines = [f"weight 1: {c.path_1}", f"weight 0: {c.path_0}"]
    if c.status == "zeroed":
        lines.append(f"Verified: BodySlide's zeroed build of '{c.slider_set}' "
                     f"(worst vertex {c.max_dev:.1e}u).")
    else:
        lines.append(c.reason)
    return "\n".join(lines)


def refused_lines(listing: "_zb.BodyListing") -> "list[str]":
    """Bodies found but not offered, each with its reason."""
    return [f"{c.provider}: {c.reason}" for c in listing.candidates if not c.selectable]


def warning_for(c, listing: "_zb.BodyListing") -> "str | None":
    """Why a chosen body needs a second look, or None for the verified default."""
    if c is None:
        return f"No {listing.label} body is chosen: {_OWN_LOOKUP}."
    if c.status == "zeroed" and c.game_loads:
        return None
    if c.status == "zeroed":
        loads = listing.default.provider if listing.default else "another body"
        return (f"{listing.label}: '{c.provider}' is a zeroed build, but the game "
                f"loads {loads}.")
    return f"{listing.label}: '{c.provider}' -- {c.reason}"


def env_for(choices: dict) -> "dict[str, str]":
    """The overrides for the child: both weights of every chosen body, and
    EMPTY values for a kind left unchosen (the Settings UBE picker included),
    so nothing inherited that the dialog refused reaches the run."""
    env = {}
    for kind, c in choices.items():
        env[ENV[kind][0]] = str(c.path_0) if c is not None else ""
        env[ENV[kind][1]] = str(c.path_1) if c is not None else ""
        if kind == "ube" and c is None:
            env[_SETTINGS_UBE] = ""
    return env


def replaced(choices: dict, base_env: dict) -> "list[str]":
    """Body overrides the environment already sets that this choice replaces
    -- said in the OK confirmation, since the run will not use them."""
    new = env_for(choices)
    out = []
    for kind, c in choices.items():
        names = list(ENV[kind]) + ([_SETTINGS_UBE] if kind == "ube" else [])
        for name in names:
            old = base_env.get(name)
            if not old:
                continue
            if name == _SETTINGS_UBE and c is not None:
                keeps = any(_same(old, p) for p in (c.path_0, c.path_1))
            else:
                keeps = bool(new.get(name)) and _same(old, new[name])
            if not keeps:
                out.append(f"{name} = {old} is not used for this run")
    return out


def summary_lines(choices: dict) -> "list[str]":
    """For the GUI's run log: which bodies this conversion uses, and how sure."""
    out = []
    for kind in KINDS:
        c = choices.get(kind)
        if c is None:
            out.append(f"  {kind.upper()} body: none chosen -- {_OWN_LOOKUP}")
        else:
            state = ("verified zeroed build" if c.status == "zeroed"
                     else f"NOT verified ({c.status})")
            out.append(f"  {kind.upper()} body: {c.provider} -- {state}")
    return out


def missing_files(choices: dict) -> "list[str]":
    """Chosen files that no longer exist -- checked just before launch, since
    an override naming a missing file silently falls back to another body."""
    return [str(p) for c in choices.values() if c is not None
            for p in (c.path_1, c.path_0) if p is None or not Path(p).is_file()]
