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

"""Declarative registry of converter settings.

The GUI is GENERATED from this list -- one entry per setting, grouped into tabs
and sections -- so adding a new setting is a single line here, not new widget
code, and the same registry drives persistence and the env/CLI mapping. Pure
data + logic (no tkinter), so it's unit-testable without a display.

Each `Setting` maps a user-facing feature to a `CBBE2UBE_*` environment variable
(or a CLI flag). For a bool:
  * invert=False -> env is set to "1" when the feature is ON  (default-OFF flag
    that ENABLES something, e.g. CBBE2UBE_CHAIN_TO_SOFTBODY).
  * invert=True  -> env is set to "1" when the feature is OFF (default-ON flag
    whose var DISABLES it, e.g. CBBE2UBE_NO_CONFORM / CBBE2UBE_EFFECT_RESKIN).
The env var is otherwise left UNSET so the code's own default applies. Polarity
and defaults below are each verified against the flag's definition in the source
(see the line refs in nif_convert.py) -- a wrong mapping silently flips a feature.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Setting:
    key: str                       # stable internal id (config + tests)
    label: str                     # UI text (sentence case)
    tab: str
    group: str
    kind: str = "bool"             # bool | int | float | str | path | choice
    default: object = False
    env: "str | None" = None       # CBBE2UBE_* var, or None for CLI/informational
    invert: bool = False           # env "1" DISABLES the feature (NO_* style)
    cli: "str | None" = None       # CLI flag instead of/alongside env
    tooltip: str = ""
    advanced: bool = False
    min: "float | None" = None
    max: "float | None" = None
    step: "float | None" = None


# Tab order for the notebook. (Run and Overlays are built by the window itself;
# the rest are generated from this registry. Numeric "tuning" knobs live inside
# the Armor tab, nested under the same group as the feature they tune.)
TABS = ("Run", "Armor", "Overlays", "Paths", "Diagnostics")


SETTINGS: "tuple[Setting, ...]" = (
    # ---- Armor: fit and conform --------------------------------------
    Setting("conform_to_body", "Conform fitted cloth to body",
            "Armor", "Fit and conform", default=True,
            env="CBBE2UBE_NO_CONFORM", invert=True,
            tooltip="Snap body-hugging cloth onto the UBE body so it stops clipping."),
    Setting("leg_bend_match", "Rigid leg-plate knee conform",
            "Armor", "Fit and conform", default=True,
            env="CBBE2UBE_NO_LEG_BEND_MATCH", invert=True,
            tooltip="Make rigid greaves follow the knee/thigh so plates don't split when posed."),
    Setting("disable_softbody_scales", "Disable soft-body scale bones",
            "Armor", "Fit and conform", default=False,
            env="CBBE2UBE_NO_SOFTBODY_SCALES", invert=False,
            tooltip="Drop breast/butt/belly jiggle transfer (troubleshooting jiggle-drag)."),
    # ---- Armor: seams -------------------------------------------------
    Setting("seam_weld", "Weld cross-plate seams",
            "Armor", "Seams", default=True,
            env="CBBE2UBE_NO_SEAM_WELD", invert=True,
            tooltip="Weld coincident verts across adjacent plates so seams don't split apart."),
    Setting("seam_skin_match", "Match seam skinning",
            "Armor", "Seams", default=True,
            env="CBBE2UBE_NO_SEAM_SKIN_MATCH", invert=True,
            tooltip="Give welded seam verts identical weights so they don't reopen when posed."),
    # ---- Armor: jiggle and physics transfer ---------------------------
    Setting("jiggle_transfer", "Transfer body jiggle to cloth",
            "Armor", "Jiggle and physics transfer", default=True,
            env="CBBE2UBE_NO_JIGGLE_TRANSFER", invert=True,
            tooltip="Graft the body's butt/belly jiggle onto rigid pants so the butt doesn't poke through."),
    Setting("butt_jiggle", "Butt jiggle graft",
            "Armor", "Jiggle and physics transfer", default=True,
            env="CBBE2UBE_NO_BUTT_JIGGLE", invert=True,
            tooltip="Add capped butt-jiggle weight to rigid leg plate."),
    Setting("chest_jiggle", "Chest jiggle graft",
            "Armor", "Jiggle and physics transfer", default=True,
            env="CBBE2UBE_NO_CHEST_JIGGLE", invert=True,
            tooltip="Add capped breast-jiggle weight to rigid chest plate (front-gated)."),
    Setting("antipoke_smooth", "Smooth anti-poke pushes (experimental)",
            "Armor", "Fit and conform", default=False,
            env="CBBE2UBE_ANTIPOKE_SMOOTH", invert=False,
            tooltip="Feather the final anti-poke's per-vert pushes over the mesh "
                    "so cleared cloth doesn't crinkle. Never reopens a poke."),
    Setting("layered_antipoke", "Layer-aware anti-poke (experimental)",
            "Armor", "Fit and conform", default=False,
            env="CBBE2UBE_LAYERED_ANTIPOKE", invert=False,
            tooltip="Give stacked garments (shirt under vest) separated "
                    "clearance floors so layers don't converge and z-fight "
                    "where the body grows."),
    Setting("jiggle_clearance", "Jiggle-overshoot clearance (experimental)",
            "Armor", "Jiggle and physics transfer", default=False,
            env="CBBE2UBE_JIGGLE_CLEARANCE", invert=False,
            tooltip="Add extra anti-poke clearance where the body jiggles "
                    "(breast/butt/belly) so bouncing softbody doesn't punch "
                    "through rigid cloth mid-motion. Tight fit is kept in "
                    "non-jiggle zones. Needs a reconvert to apply."),
    # ---- Armor: glow and effect-shader --------------------------------
    Setting("glow_source_skin", "Keep source skin on glows",
            "Armor", "Glow and effect-shader", default=True,
            env="CBBE2UBE_EFFECT_RESKIN", invert=True,
            tooltip="Effect-shader glows keep their vanilla skin instead of the body reskin."),
    Setting("glow_anim", "Glow animation (texture scroll)",
            "Armor", "Glow and effect-shader", default=True,
            env="CBBE2UBE_NO_GLOW_ANIM", invert=True,
            tooltip="Keep the glow's animated texture-scroll controller (e.g. the Daedric red glow)."),
    Setting("glow_ride", "Glow rides its plate",
            "Armor", "Glow and effect-shader", default=True,
            env="CBBE2UBE_NO_GLOW_RIDE", invert=True,
            tooltip="Bind the glow decal to its plate so it doesn't clip through when the body moves."),
    # ---- Armor: HDT-SMP chains ---------------------------------------
    Setting("chain_to_softbody", "Chain cloth to soft-body",
            "Armor", "HDT-SMP chains", default=False,
            env="CBBE2UBE_CHAIN_TO_SOFTBODY", invert=False,
            tooltip="Convert authored physics-chain cloth to per-vertex soft-body (stable on UBE, no independent sway)."),
    Setting("static_chains", "Static chains",
            "Armor", "HDT-SMP chains", default=False,
            env="CBBE2UBE_STATIC_CHAINS", invert=False,
            tooltip="Freeze physics chains (troubleshooting collapse-to-origin)."),
    Setting("nested_chain_anchors", "Nested chain anchors",
            "Armor", "HDT-SMP chains", default=False,
            env="CBBE2UBE_NESTED_CHAIN_ANCHORS", invert=False,
            tooltip="Nest upper-body-anchored chains so FSMP tracks torso motion through them."),
    # ---- Armor: boots and parity -------------------------------------
    Setting("boot_far_thigh", "Exclude far-thigh scale on boots",
            "Armor", "Boots and parity", default=True,
            env="CBBE2UBE_KEEP_BOOT_THIGH_SCALE", invert=True,
            tooltip="Drop far-thigh scale bones from calf/foot boots so they don't fade at camera distance."),
    Setting("weight_parity_check", "Weight-partner parity check",
            "Armor", "Boots and parity", default=True,
            env="CBBE2UBE_NO_WEIGHT_PARITY_CHECK", invert=True,
            tooltip="Postflight warn when a _0/_1 weight pair converts differently."),
    # ---- Armor: coverage ----------------------------------------------
    Setting("vanilla_sweep", "Convert vanilla armor (base game + DLC)",
            "Armor", "Coverage", default=True,
            env="CBBE2UBE_NO_VANILLA_SWEEP", invert=True,
            tooltip="Run the game Data dir as the last (lowest-priority) "
                    "source so every vanilla/DLC armor mesh converts. Without "
                    "this, vanilla armor no mod overrides is never converted "
                    "and renders invisible on UBE actors. Mod sources still "
                    "win wherever they cover the same piece."),
    # ---- Armor delivery: SkyPatcher is the only path (no toggle -- the legacy
    #      ESP-override machinery was removed once SkyPatcher was proven). The
    #      preflight 'SkyPatcher (armor delivery)' check enforces the runtime dep.

    # ---- Armor: advanced numeric knobs (nest under the feature they tune) ---
    Setting("jiggle_transfer_factor", "Jiggle transfer factor",
            "Armor", "Jiggle and physics transfer", kind="float", default=0.85,
            env="CBBE2UBE_JIGGLE_TRANSFER_FACTOR", advanced=True,
            min=0.0, max=1.0, step=0.05,
            tooltip="Fraction of the body's local jiggle weight grafted onto fitted cloth."),
    Setting("seam_weld_tol", "Seam-weld tolerance (u)",
            "Armor", "Seams", kind="float", default=0.05,
            env="CBBE2UBE_SEAM_WELD_TOL", advanced=True,
            min=0.0, max=0.5, step=0.01,
            tooltip="Max distance for two cross-plate verts to be treated as one seam."),
    Setting("glow_ride_max", "Glow ride max (u)",
            "Armor", "Glow and effect-shader", kind="float", default=2.0,
            env="CBBE2UBE_GLOW_RIDE_MAX", advanced=True,
            min=0.0, max=10.0, step=0.5,
            tooltip="Max plate distance a glow vert will ride; farther verts keep their own warp."),

    # ---- Paths (auto-detected; override + validate) ---------------------
    Setting("ube_body", "UBE body reference NIF",
            "Paths", "Bodies", kind="path", default="", env="CBBE2UBE_UBE_BODY",
            tooltip="BodySlide-built UBE body NIF (BaseShape). Auto-detected from the modlist when blank."),
    Setting("texconv", "texconv.exe",
            "Paths", "Tools", kind="path", default="", env="CBBE2UBE_TEXCONV",
            tooltip="DirectXTex texconv for texture conversion. Auto-located when blank."),

    # ---- Diagnostics ----------------------------------------------------
    Setting("debug_glow_ctrl", "Log dangling glow controllers",
            "Diagnostics", "Logging", default=False,
            env="CBBE2UBE_DEBUG_GLOW_CTRL", invert=False,
            tooltip="Write a stack trace whenever a save leaves an effect-shader shape with a self-referential controller."),
    Setting("debug_finalize", "Debug HDT finalize",
            "Diagnostics", "Logging", default=False,
            env="CBBE2UBE_DEBUG_FINALIZE", invert=False,
            tooltip="Verbose physics-finalize logging."),

    # ---- UI-only (persisted, no env; not shown in the generated tabs -- the
    #      window renders a dedicated control for it) --------------------------
    Setting("theme", "Window theme", "Appearance", "Appearance",
            kind="str", default="standard", env=None,
            tooltip="Window colour palette: Standard (dark + gold), Light, "
                    "Dark, Whispa (silver + purple), or Jbish (black + rose). "
                    "Picked from the Theme control at the top right."),
)


def defaults() -> "dict[str, object]":
    """key -> default value for every setting."""
    return {s.key: s.default for s in SETTINGS}


def by_key() -> "dict[str, Setting]":
    return {s.key: s for s in SETTINGS}


def tabs_present() -> "list[str]":
    """Tabs that actually have settings, in canonical order."""
    have = {s.tab for s in SETTINGS}
    return [t for t in TABS if t in have]


def groups_in_tab(tab: str) -> "list[str]":
    """Group names in a tab, in first-seen order."""
    out: "list[str]" = []
    for s in SETTINGS:
        if s.tab == tab and s.group not in out:
            out.append(s.group)
    return out


def settings_in(tab: str, group: str) -> "list[Setting]":
    return [s for s in SETTINGS if s.tab == tab and s.group == group]


def env_string_for(s: Setting, value) -> "str | None":
    """The env value to set for `s` given the UI `value`, or None to leave the
    var UNSET (so the code default applies)."""
    if s.env is None:
        return None
    if s.kind == "bool":
        on = bool(value)
        trigger = (not on) if s.invert else on
        return "1" if trigger else None
    # numeric / string / path: only write a real override (skip default / blank).
    if value is None or value == s.default or (isinstance(value, str) and not value.strip()):
        return None
    return str(value)


def apply_env(values: "dict[str, object]",
              base_env: "dict[str, str] | None" = None) -> "dict[str, str]":
    """Return an environment dict for launching the converter: `base_env` (or
    empty) with every registry-managed CBBE2UBE_* var set/unset per `values`.

    Registry-managed vars are AUTHORITATIVE -- a var at its default is REMOVED so
    a stale value inherited from the parent can't leak. Vars not in the registry
    are left untouched."""
    env = dict(base_env if base_env is not None else {})
    for s in SETTINGS:
        if s.env is None:
            continue
        ev = env_string_for(s, values.get(s.key, s.default))
        if ev is None:
            env.pop(s.env, None)
        else:
            env[s.env] = ev
    return env


# ---- persistence ---------------------------------------------------------

def config_path() -> Path:
    """Where the settings JSON lives. CBBE2UBE_CONFIG overrides; else next to the
    exe (frozen) or the repo root (source). Survives an exe redeploy (robocopy
    /E doesn't purge it)."""
    override = os.environ.get("CBBE2UBE_CONFIG", "").strip()
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "CBBEtoUBE_settings.json"


def _coerce(s: Setting, v):
    try:
        if s.kind == "bool":
            return bool(v)
        if s.kind == "int":
            return int(v)
        if s.kind == "float":
            return float(v)
        return str(v)
    except (TypeError, ValueError):
        return s.default


def load_values(path=None) -> "dict[str, object]":
    """Return values for every setting: defaults overlaid with a saved JSON
    file. Unknown keys are ignored; malformed/absent file -> pure defaults."""
    vals = defaults()
    p = Path(path) if path is not None else config_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return vals
    if not isinstance(raw, dict):
        return vals
    reg = by_key()
    for k, v in raw.items():
        s = reg.get(k)
        if s is not None:
            vals[k] = _coerce(s, v)
    return vals


def save_values(values: "dict[str, object]", path=None) -> bool:
    """Persist only the settings that DIFFER from their default (keeps the file
    small and forward-compatible -- new settings just use their new default).
    Returns True on success."""
    reg = by_key()
    out = {k: values[k] for k in values
           if k in reg and values[k] != reg[k].default}
    p = Path(path) if path is not None else config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except Exception:
        return False
