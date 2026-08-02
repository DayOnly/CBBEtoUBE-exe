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
import re
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
    tooltip: str = ""              # the FULL explanation; shown on hover/expand
    hint: str = ""                 # one line shown inline; see `hint_for`
    advanced: bool = False         # hidden unless "Show advanced" is ticked
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
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_CONFORM", invert=True,
            tooltip="Snap body-hugging cloth onto the UBE body so it stops clipping."),
    Setting("leg_bend_match", "Rigid leg-plate knee conform",
            "Armor", "Limbs and extremities", default=True,
            env="CBBE2UBE_NO_LEG_BEND_MATCH", invert=True,
            tooltip="Make rigid greaves follow the knee/thigh so plates don't split when posed."),
    Setting("disable_softbody_scales", "Disable soft-body scale bones",
            "Armor", "Jiggle transfer", default=False,
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
            "Armor", "Jiggle transfer", default=True,
            env="CBBE2UBE_NO_JIGGLE_TRANSFER", invert=True,
            tooltip="Graft the body's butt/belly jiggle onto rigid pants so the butt doesn't poke through."),
    Setting("torso_jiggle", "Chest/butt jiggle on fitted torso armor",
            "Armor", "Jiggle transfer", default=True,
            env="CBBE2UBE_NO_TORSO_JIGGLE", invert=True,
            hint="Let chest/belly jiggle reach cloth that covers the torso.",
            tooltip="Extend the graft above to a fitted corset/bra/cuirass, so it "
                    "follows the body's breast and butt instead of staying rigid "
                    "while the body moves under it (the 'clips only when moving' "
                    "case). Default ON since 1.2, validated in game via the bust "
                    "collider split; unchecking also disables that split (the two "
                    "ship as one fix)."),
    Setting("butt_jiggle", "Butt jiggle graft",
            "Armor", "Jiggle transfer", default=True,
            env="CBBE2UBE_NO_BUTT_JIGGLE", invert=True,
            tooltip="Add capped butt-jiggle weight to rigid leg plate."),
    Setting("chest_jiggle", "Chest jiggle graft",
            "Armor", "Jiggle transfer", default=True,
            env="CBBE2UBE_NO_CHEST_JIGGLE", invert=True,
            tooltip="Add capped breast-jiggle weight to rigid chest plate (front-gated)."),
    Setting("antipoke_smooth", "Smooth anti-poke pushes (experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_ANTIPOKE_SMOOTH", invert=False,
            tooltip="Feather the final anti-poke's per-vert pushes over the mesh "
                    "so cleared cloth doesn't crinkle. Never reopens a poke."),
    Setting("layered_antipoke", "Layer-aware anti-poke (experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_LAYERED_ANTIPOKE", invert=False,
            hint="Give stacked garments separated clearance floors so layers don't converge.",
            tooltip="Give stacked garments (shirt under vest) separated "
                    "clearance floors so layers don't converge and z-fight "
                    "where the body grows."),
    Setting("rigid_majority_softbody", "Keep mostly-rigid armour skinned "
            "(experimental)",
            "Armor", "Body follow and morphs", default=False,
            env="CBBE2UBE_RIGID_MAJORITY_SOFTBODY", invert=False,
            hint="Stop a small chain flap turning a whole rigid cuirass into simulated cloth.",
            tooltip="When a small chain-driven flap shares one shape with a "
                    "large rigid panel, the converter currently gives the WHOLE "
                    "shape simulated cloth physics -- and simulated cloth does "
                    "not follow body sliders, so the body pushes through the "
                    "rigid part. Measured on a cuirass whose 5%%-of-verts skirt "
                    "flap made all of it simulated: breast follow 0.00 and "
                    "7.5%% bust clipping. This keeps such shapes skinned and "
                    "morphable. Trade-off: the flap stops swinging. "
                    "Experimental -- changes physics, so check for equip "
                    "crashes and collapsing cloth, not just clipping."),
    Setting("chain_body_shift", "Shift physics chains onto the new body "
            "(experimental)",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_CHAIN_BODY_SHIFT", invert=False,
            hint="Move a skirt's chain bones onto the new body instead of leaving them at source.",
            tooltip="Chain-driven cloth (skirts, drapes) is pinned to its "
                    "SOURCE rest position so it stays aligned with its bones, "
                    "which means no clearance pass can reach it -- a skirt "
                    "keeps a source-shaped rest pose over a differently-shaped "
                    "body. This moves each chain's ROOT bone by the local body "
                    "delta instead, so the whole chain translates rigidly "
                    "(measured worst inter-bone change 0.000000u -- warping "
                    "chain bones individually is what makes a chain explode). "
                    "Took bind-pose skirt clipping 7.5%% to 1.1%% on the test "
                    "piece, but showed no visible in-game change, so it is "
                    "unproven where it counts. Experimental."),
    Setting("unified_offset", "Unified clearance floor (experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_UNIFIED_OFFSET", invert=False,
            hint="Solve one clearance floor per vertex instead of inflating then conforming.",
            tooltip="Solve one clearance floor per vertex and apply it once, "
                    "instead of inflating before the standoff conform and "
                    "pushing again after it. The inflate is additive and the "
                    "conform is absolute, so today the conform overwrites the "
                    "inflate on about a third of shapes; stated as a floor "
                    "AFTER the conform the same clearance survives. Feathers "
                    "once rather than twice, and spends one budget per vertex "
                    "instead of several. Experimental: changes the fit of "
                    "body-slot armour, so test it before a full reconvert."),
    Setting("chest_follow", "Chest follow ratio (experimental)",
            "Armor", "Body follow and morphs", default=False,
            env="CBBE2UBE_CHEST_FOLLOW", invert=False,
            hint="Make chest cloth track the morphed bust instead of standing off it.",
            tooltip="Let a fitted soft-material top track the body's breast motion "
                    "by the amount its own clearance actually requires, instead of "
                    "an absolute weight cap that leaves it following about a third "
                    "of the body. Targets 'chest clips only when moving'. Metal "
                    "armour keeps the old conservative cap. Experimental: too much "
                    "tracking makes stiff armour look rubbery."),
    Setting("drape_xml_gate", "Fit robes/dresses that declare their own physics",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_DRAPE_XML_GATE", invert=False,
            hint="Also fit robes and dresses that declare their own physics.",
            tooltip="Robes, dresses, cloaks and capes are skipped by every fitting "
                    "pass, because some of them are cloth driven by a game-wide "
                    "physics config that cannot be detected from the mesh -- and "
                    "adjusting those has crashed on equip. This narrows the skip to "
                    "pieces that do NOT ship their own physics file, so a robe whose "
                    "physics IS declared can be fitted like any other garment. "
                    "RISK: the failure mode is a crash when equipping a robe, so "
                    "test robes specifically after turning this on."),
    Setting("source_follow", "...judge by the outfit's own weighting, not its name",
            "Armor", "Body follow and morphs", default=False,
            env="CBBE2UBE_SOURCE_FOLLOW", invert=False,
            hint="Follow the bust only where the original author weighted it.",
            tooltip="Decide how much a top may move by looking at whether the "
                    "outfit's author weighted its chest at all, instead of "
                    "guessing the material from its name and textures. Outfits "
                    "the author weighted already move correctly and are left "
                    "alone; the ones they left rigid are the ones that clip, and "
                    "this lets them move as much as their own fit requires. Only "
                    "ever adds movement to pieces nothing was helping."),
    Setting("chest_follow_unknown", "...its ceiling for unrecognised materials",
            "Armor", "Body follow and morphs", kind="float", default=0.35,
            env="CBBE2UBE_CHEST_FOLLOW_UNKNOWN", min=0.0, max=1.0, step=0.05,
            advanced=True,
            hint="How far to trust chest follow when the source's intent is unclear (0-1).",
            tooltip="How much body motion a top may follow when its material "
                    "cannot be identified from its name or texture. 0.35 (the "
                    "default) treats it like metal; 1.0 treats it like cloth. "
                    "Most armour in a large pack is unidentifiable, and this is "
                    "what limits it -- raise it if chests still clip when moving, "
                    "lower it if stiff armour starts looking rubbery."),
    Setting("chain_torso", "Chest follow on skirt-welded cuirasses (experimental)",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_CHAIN_TORSO", invert=False,
            tooltip="Some cuirasses are modelled as ONE piece together with their "
                    "own physics skirt. The skirt hangs away from the body, which "
                    "drags the whole piece below the 'hugs the body' test, so "
                    "nothing ever adjusts the chest -- even though the chest itself "
                    "is skin-tight. This judges such a piece on its non-skirt part. "
                    "Needs 'Chest follow ratio' on as well. The skirt is never "
                    "touched: physics drives it. UNPROVEN -- on every armour tested "
                    "so far it changed nothing; the setting above it is what "
                    "actually moves these pieces."),
    Setting("leg_chain_guard", "Never re-weight physics-driven cloth",
            "Armor", "Physics chains (HDT-SMP)", default=True,
            env="CBBE2UBE_NO_LEG_CHAIN_GUARD", invert=True,
            tooltip="Keep the leg/chest conform away from vertices that HDT-SMP "
                    "simulates. Writing those is pointless (physics wins at "
                    "runtime) and has crashed on equip before. Leave this on "
                    "unless you are bisecting a problem."),
    Setting("smp_antipoke", "Bust clearance on SMP collider armor (experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_SMP_ANTIPOKE", invert=False,
            hint="Push simulated cloth clear of the body so the bust stops poking through.",
            tooltip="An armor whose physics config names it only as a COLLIDER "
                    "currently gets no bust clearance at all, so the body pushes "
                    "straight through it -- the 'chest clips when moving' case on "
                    "cuirasses with their own physics. Measured 6.3% -> 3.3% "
                    "exposed on one such cuirass. Experimental: pushing verts out "
                    "on a convex region has spread them before."),
    Setting("smp_antipoke_push", "...its push budget (units)",
            "Armor", "Fit and clearance", kind="float", default=1.0,
            env="CBBE2UBE_SMP_ANTIPOKE_PUSH", min=0.0, max=6.0, step=0.1,
            tooltip="How far that pass may push a vert outward. Default 1.0 was "
                    "tuned at rest; the body's breast physics is allowed several "
                    "times that much travel, so raising it is the next lever if "
                    "clearance helps but falls short. Raise one step at a time -- "
                    "too large spreads verts on rounded areas.",
            advanced=True),
    Setting("skin_influence_cap", "Cap skin influences on the main skin install",
            "Armor", "Output checks", default=True,
            env="CBBE2UBE_NO_SKIN_INFLUENCE_CAP", invert=True,
            hint="Trim each vertex to the 4 bone influences the format allows, and renormalise.",
            tooltip="Trim every vertex to the 4 influences the format allows and "
                    "renormalise, instead of letting the save silently drop the "
                    "smallest and leave the weights light. Default ON since 1.2: "
                    "the unmetered drop shipped zero-weight bones (an equip-CTD "
                    "hazard) on 42 shapes pack-wide. Uncheck to restore the old "
                    "write exactly."),
    Setting("jiggle_clearance", "Jiggle-overshoot clearance",
            "Armor", "Jiggle transfer", default=True,
            env="CBBE2UBE_NO_JIGGLE_CLEARANCE", invert=True,
            tooltip="Clear armor against the body's MOVING envelope, not just its "
                    "resting one. HDT-SMP throws the breast outward past the surface "
                    "the other clearance passes measure, so a rigid cuirass with ample "
                    "resting clearance can still show skin. Adds room only where the "
                    "body jiggles: breast +0.14u, belly +0.02u, butt +0.01u, back 0.000u."),
    Setting("jiggle_clearance_gain", "Jiggle clearance gain (u)",
            "Armor", "Jiggle transfer", kind="float", default=0.5,
            env="CBBE2UBE_JIGGLE_CLEARANCE_GAIN", advanced=True,
            min=0.0, max=2.0, step=0.1,
            tooltip="Extra clearance in units at full jiggle weight (peak ~0.56 at the "
                    "nipple, so 0.5 adds ~0.28u there). Raise if a bouncier SMP setup "
                    "still shows skin at the breast. Takes effect on a reconvert."),
    Setting("jiggle_clearance_max", "Jiggle clearance cap (u)",
            "Armor", "Jiggle transfer", kind="float", default=0.5,
            env="CBBE2UBE_JIGGLE_CLEARANCE_MAX", advanced=True,
            min=0.0, max=2.0, step=0.1,
            hint="Upper bound on the extra clearance jiggle transfer is allowed to add.",
            tooltip="Hard ceiling on the jiggle clearance term, so a runaway weight "
                    "can't push armor arbitrarily far off the body."),
    # ---- Armor: glow and effect-shader --------------------------------
    Setting("glow_source_skin", "Keep source skin on glows",
            "Armor", "Glow and effect shaders", default=True,
            env="CBBE2UBE_EFFECT_RESKIN", invert=True,
            tooltip="Effect-shader glows keep their vanilla skin instead of the body reskin."),
    Setting("glow_anim", "Glow animation (texture scroll)",
            "Armor", "Glow and effect shaders", default=True,
            env="CBBE2UBE_NO_GLOW_ANIM", invert=True,
            tooltip="Keep the glow's animated texture-scroll controller (e.g. the Daedric red glow)."),
    Setting("glow_ride", "Glow rides its plate",
            "Armor", "Glow and effect shaders", default=True,
            env="CBBE2UBE_NO_GLOW_RIDE", invert=True,
            tooltip="Bind the glow decal to its plate so it doesn't clip through when the body moves."),
    # ---- Armor: HDT-SMP chains ---------------------------------------
    Setting("chain_to_softbody", "Chain cloth to soft-body",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_CHAIN_TO_SOFTBODY", invert=False,
            tooltip="Convert authored physics-chain cloth to per-vertex soft-body (stable on UBE, no independent sway)."),
    Setting("static_chains", "Static chains",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_STATIC_CHAINS", invert=False,
            tooltip="Freeze physics chains (troubleshooting collapse-to-origin)."),
    Setting("nested_chain_anchors", "Nested chain anchors",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_NESTED_CHAIN_ANCHORS", invert=False,
            tooltip="Nest upper-body-anchored chains so FSMP tracks torso motion through them."),
    # ---- Armor: boots and parity -------------------------------------
    Setting("boot_far_thigh", "Exclude far-thigh scale on boots",
            "Armor", "Limbs and extremities", default=True,
            env="CBBE2UBE_KEEP_BOOT_THIGH_SCALE", invert=True,
            tooltip="Drop far-thigh scale bones from calf/foot boots so they don't fade at camera distance."),
    Setting("weight_parity_check", "Weight-partner parity check",
            "Armor", "Output checks", default=True,
            env="CBBE2UBE_NO_WEIGHT_PARITY_CHECK", invert=True,
            tooltip="Postflight warn when a _0/_1 weight pair converts differently."),
    # ---- Run: what the run covers --------------------------------------
    # Lives on the RUN tab, not Armor: it adds a SOURCE to the run (the game Data
    # dir) rather than changing how a garment is fitted, so it belongs beside the
    # mod selection it extends. The Run tab is hand-built, so gui.py renders this
    # one via `_registry_check` -- same binding, same persistence, and the
    # registry stays the single source of label/tooltip/default/env.
    Setting("vanilla_sweep", "Convert vanilla armor (base game + DLC)",
            "Run", "Convert armor", default=True,
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
            "Armor", "Jiggle transfer", kind="float", default=0.85,
            env="CBBE2UBE_JIGGLE_TRANSFER_FACTOR", advanced=True,
            min=0.0, max=1.0, step=0.05,
            tooltip="Fraction of the body's local jiggle weight grafted onto fitted cloth."),
    Setting("seam_weld_tol", "Seam-weld tolerance (u)",
            "Armor", "Seams", kind="float", default=0.05,
            env="CBBE2UBE_SEAM_WELD_TOL", advanced=True,
            min=0.0, max=0.5, step=0.01,
            tooltip="Max distance for two cross-plate verts to be treated as one seam."),
    Setting("glow_ride_max", "Glow ride max (u)",
            "Armor", "Glow and effect shaders", kind="float", default=2.0,
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
    Setting("window_geometry", "Remembered window size", "Appearance",
            "Appearance", kind="str", default="", env=None,
            tooltip="The main window's last size and position, saved on close "
                    "and restored on open. Not shown as a control -- the "
                    "window itself is the control. Clear it (or Reset to "
                    "defaults) to go back to the built-in size."),
)


def defaults() -> "dict[str, object]":
    """key -> default value for every setting."""
    return {s.key: s.default for s in SETTINGS}


def by_key() -> "dict[str, Setting]":
    return {s.key: s for s in SETTINGS}


HINT_MAX = 110


def hint_for(s: Setting) -> str:
    """The ONE LINE shown inline under a control.

    The full `tooltip` moves behind a hover/expand: 38 settings x a paragraph
    each made the Armor tab 86% prose and ~3.7 screens tall. Nothing is deleted
    -- every word stays reachable. Deleting it would be the wrong trade: the
    tooltips carry measured numbers and in-game caveats that exist nowhere
    else, so they MOVE rather than shrink.

    Defaults to the tooltip's first sentence, which is already a summary for
    most settings (median 79 chars). Set `hint=` explicitly where it is not.
    """
    if s.hint:
        return s.hint
    t = (s.tooltip or "").strip()
    if not t:
        return ""
    first = re.split(r"(?<=[.!?])\s+", t)[0]
    if len(first) <= HINT_MAX:
        return first
    cut = first[:HINT_MAX].rsplit(" ", 1)[0]
    return cut + "…"


def tabs_present() -> "list[str]":
    """Tabs that actually have settings, in canonical order."""
    have = {s.tab for s in SETTINGS}
    return [t for t in TABS if t in have]


# Explicit display order. SETTINGS is grouped by CONCERN, but a tuple's order is
# the order things were added over time, which is not a useful reading order --
# it left numeric knobs several rows from the toggle they tune, so they read as
# independent options. Declaring layout separately keeps "add a setting = one
# line in SETTINGS" true; anything not named here still renders, at the end of
# its group, so a new setting can never silently vanish.
LAYOUT: "dict[str, tuple]" = {
    "Armor": (
        ("Fit and clearance", (
            "drape_xml_gate", "conform_to_body", "smp_antipoke",
            "smp_antipoke_push", "antipoke_smooth", "layered_antipoke",
            "unified_offset")),
        ("Body follow and morphs", (
            "chest_follow", "chest_follow_unknown", "source_follow",
            "rigid_majority_softbody")),
        ("Jiggle transfer", (
            "jiggle_transfer", "jiggle_transfer_factor", "torso_jiggle",
            "butt_jiggle", "chest_jiggle", "jiggle_clearance",
            "jiggle_clearance_gain", "jiggle_clearance_max",
            "disable_softbody_scales")),
        ("Physics chains (HDT-SMP)", (
            "leg_chain_guard", "chain_to_softbody", "static_chains",
            "nested_chain_anchors", "chain_torso", "chain_body_shift")),
        ("Limbs and extremities", ("leg_bend_match", "boot_far_thigh")),
        ("Seams", ("seam_weld", "seam_weld_tol", "seam_skin_match")),
        ("Glow and effect shaders", (
            "glow_source_skin", "glow_anim", "glow_ride", "glow_ride_max")),
        ("Output checks", ("skin_influence_cap", "weight_parity_check")),
    ),
}


def groups_in_tab(tab: str) -> "list[str]":
    """Group names in a tab: LAYOUT order first, then any group it omits."""
    out = [g for g, _keys in LAYOUT.get(tab, ())]
    for s in SETTINGS:
        if s.tab == tab and s.group not in out:
            out.append(s.group)
    return out


def settings_in(tab: str, group: str) -> "list[Setting]":
    """Settings in one group: LAYOUT order first, then any key it omits."""
    have = [s for s in SETTINGS if s.tab == tab and s.group == group]
    order = dict(LAYOUT.get(tab, ())).get(group)
    if not order:
        return have
    rank = {k: i for i, k in enumerate(order)}
    return sorted(have, key=lambda s: rank.get(s.key, len(rank)))


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


KNOWN_KEYS_FIELD = "_known_settings"


def unseen_settings(path=None) -> "tuple[bool, list]":
    """`(baseline_known, settings this build has that the saved file never saw)`.

    `save_values` stores ONLY non-default values, so an absent key means "at its
    default" -- which is indistinguishable from "added to the tool AFTER you last
    saved". That ambiguity cost a full reconvert on 2026-07-27: two options built
    that day defaulted OFF, the run looked completely normal for an hour, and the
    work simply did not happen. The only visible evidence was the ABSENCE of a
    setting's name in the flag echo, which reads exactly like a deliberate choice.

    So the file also records `_known_settings` -- every key the build knew at save
    time -- letting a genuinely NEW option be NAMED instead of inferred.

    `baseline_known=False` means the saved file predates this tracking, so nothing
    can be diffed yet; it becomes accurate after the next save. A file that does not
    exist is not a warning: nothing was ever chosen, so nothing is new relative to a
    choice."""
    p = Path(path) if path is not None else config_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return True, []
    if not isinstance(raw, dict):
        return True, []
    known = raw.get(KNOWN_KEYS_FIELD)
    if not isinstance(known, list):
        return False, []            # saved before tracking existed
    seen = {k for k in known if isinstance(k, str)}
    # Only options that can CHANGE A CONVERSION are worth warning about. A
    # setting with no env var (theme, window size) cannot affect a run, so
    # naming it as "running at its default, which is not the same as you having
    # chosen it" would be false -- and a warning that cries wolf about cosmetics
    # is how a real one gets skimmed past.
    return True, [s for s in SETTINGS if s.key not in seen and s.env]


def save_values(values: "dict[str, object]", path=None) -> bool:
    """Persist only the settings that DIFFER from their default (keeps the file
    small and forward-compatible -- new settings just use their new default), plus
    `_known_settings`: every key THIS BUILD offers, so a later build can tell a
    newly-added option from one deliberately left at its default (`unseen_settings`).
    Returns True on success."""
    reg = by_key()
    out = {k: values[k] for k in values
           if k in reg and values[k] != reg[k].default}
    out[KNOWN_KEYS_FIELD] = sorted(reg)   # ignored on load: not a registered key
    p = Path(path) if path is not None else config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except Exception:
        return False
