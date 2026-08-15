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
    Setting("back_morph_residual",
            "Keep the upper back covered when the body morphs",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_BACK_MORPH_RESIDUAL", invert=True,
            hint="Fixes skin showing through armour across the shoulder blades "
                 "on larger presets.",
            tooltip="Every other clearance pass measures the un-morphed body, "
                    "and the one that doesn't is aimed at the bust -- so the "
                    "upper back was given no allowance for the body growing "
                    "under it at runtime. Charges the back the same allowance "
                    "the bust already gets."),
    Setting("clearance_differential",
            "Base clearance on reshaping, not just growth",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_CLEARANCE_DIFFERENTIAL", invert=True,
            hint="Fixes skin showing through around the upper chest and back "
                 "on larger presets.",
            tooltip="Clearance used to scale with how far the body GROWS, but "
                    "armour follows growth for free -- what pokes through is "
                    "the body RESHAPING underneath it. Measures that instead. "
                    "Only ever adds room, never takes it away, so a piece that "
                    "fits today cannot get tighter."),
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
    Setting("conform_fold_guard", "Stop the conform folding the surface "
            "(experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_CONFORM_FOLD_GUARD", invert=False,
            hint="Fixes dark patches that show only from some angles, usually in mirrored pairs.",
            tooltip="The conform pass pulls each vertex toward the body on its "
                    "own, and nothing stops two neighbours crossing through "
                    "each other. Where the body creases -- spine groove, "
                    "underbust, waist -- the surface between them turns inside "
                    "out and renders as a flat dark patch, normally in a "
                    "left/right pair because the body's creases are "
                    "symmetric. Reported in game as two black squares on an "
                    "converted cuirass, and measured on 208 of 302 shapes in a "
                    "200-mesh sample. This clamps the pull so no triangle can "
                    "flip. It only ever moves a vertex LESS than before, so it "
                    "cannot cause clipping -- but a garment can sit very "
                    "slightly further off the body exactly where the fold "
                    "used to be."),
    Setting("warp_shear_limit", "Stop the warp shearing big triangles "
            "(experimental)",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_WARP_SHEAR_LIMIT", invert=False,
            hint="Fixes flat black patches that appear in mirrored pairs, often at the waist.",
            tooltip="The CBBE-to-UBE warp moves each vertex by the body "
                    "deformation nearest to it. A LARGE triangle's corners sit "
                    "far apart, so at the waist -- where the two bodies differ "
                    "most -- they get pulled very different ways, and the "
                    "triangle stretches and rotates until it faces INTO the "
                    "body. It is then lit from behind and renders flat black, "
                    "in a left/right pair. Measured on a converted cuirass: the "
                    "two biggest triangles grew 2.6x and turned over, while "
                    "the mesh as a whole grew only 1.8%%. This caps how far one "
                    "triangle may stretch. It does NOT warp less -- a clamped "
                    "vertex still travels the full local body delta, it just "
                    "stops shearing away from its neighbours -- so it cannot "
                    "leave armour CBBE-shaped."),
    # POLARITY CORRECTED 2026-08-12. This row wrote CBBE2UBE_WARP_DELTA_OUTLIER,
    # which `src/` does not read: the flag is `CBBE2UBE_NO_WARP_DELTA_OUTLIER`
    # and its default went ON when the feature was defaulted on. So the row was
    # dead in BOTH directions -- ticking it did nothing, unticking it did
    # nothing, and the GUI showed a default-ON feature as off. Found by counting
    # GUI envs against the ones `src/` actually reads; `test_every_gui_env_is_
    # read_by_src` now keeps it honest.
    Setting("warp_delta_outlier", "Stop the warp flinging a lone vertex",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_WARP_DELTA_OUTLIER", invert=True,
            hint="Fixes small pure-black spots, usually a mirrored pair, that survive other fixes.",
            tooltip="A single vertex can be driven several units away from its "
                    "own neighbours -- mostly by the clearance push, which "
                    "shoves a deep interior vertex out to the floor while the "
                    "vertices around it stay put. The triangles bridging that "
                    "vertex to the rest of the garment then stretch and rotate "
                    "until they face INTO the body, so they draw with no light "
                    "at all: small PURE BLACK patches, normally a left/right "
                    "pair. They are not inverted -- winding and normals agree "
                    "-- so no other check finds them. This caps how far one "
                    "vertex may travel relative to its neighbours. Confirmed "
                    "in game: the patches stopped being visible."),
    Setting("warp_delta_outlier_max", "  ...allowed deviation (units)",
            "Armor", "Fit and clearance", kind="float", default=0.5,
            env="CBBE2UBE_WARP_DELTA_OUTLIER_MAX", advanced=True,
            min=0.1, max=3.0, step=0.05,
            tooltip="Lower clamps harder. Swept on one piece by worst "
                    "black-triangle area: 1.0 -> 27.7, 0.5 -> 24.2, "
                    "0.25 -> 19.6, at about 0.004u of median standoff "
                    "throughout. Below ~0.25 the worst case keeps shrinking "
                    "but the COUNT of inward-facing triangles starts rising."),
    Setting("normal_determinacy", "Trust the mesh's own shading where it is clear",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_NORMAL_DETERMINACY", invert=True,
            hint="Fixes dark shards that survive every geometry fix, because the geometry was never the problem.",
            tooltip="After the fit moves vertices, their normals are "
                    "recomputed from the new shape -- except that any normal "
                    "disagreeing with the ORIGINAL by more than 90 degrees was "
                    "flipped back, on the assumption this only ever happened "
                    "where the recompute was ambiguous. It also happened at "
                    "vertices with a full, agreeing set of triangles, storing "
                    "a normal that points INTO the surface: those shade as if "
                    "lit from behind, which reads as broken angular verts and "
                    "is invisible to every position-based check. Measured on "
                    "one garment against the author's own mesh, which had "
                    "none. This limits the flip to vertices whose normal "
                    "genuinely cannot be determined -- too few triangles, or "
                    "triangles that cancel -- which is the case it was written "
                    "for."),
    Setting("warp_push_shell_cap", "Never push a vertex through its own armour",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_WARP_PUSH_SHELL_CAP", invert=False,
            hint="Stops the clearance push driving hidden lining out through the outer shell.",
            tooltip="The clearance push fires on any vertex too close to the "
                    "body, including ones the author deliberately buried -- a "
                    "lining, the hidden edge of a flap. Those cannot be where "
                    "skin shows through, because there is more armour in front "
                    "of them, so pushing them buys nothing and can drive them "
                    "out through the outer shell as a spike. This caps the "
                    "push at the garment's own surface. It only ever binds "
                    "where armour is already in front of the vertex, so it "
                    "cannot reopen clipping."),
    Setting("breast_follow_keep", "Keep the author's bust follow",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_BREAST_FOLLOW_KEEP", invert=True,
            hint="Untick only if bust cloth over-follows the breast.",
            tooltip="A garment's layers are matched to the body's skinning so "
                    "they travel with it. That is right for arms and legs and "
                    "wrong for the bust, where the author deliberately makes an "
                    "inner layer follow the breast MORE than the body does so "
                    "it hugs. Without this every layer converges on the body's "
                    "own share, and an inner layer ends up out-travelling the "
                    "one over it -- so the underlayer swings out through the "
                    "outer one whenever the breasts move. Nothing moves at "
                    "rest, so this cannot change the fit; it only changes what "
                    "travels with what."),
    Setting("bust_flat_clear", "Bust clearance floor",
            "Armor", "Fit and clearance", kind="float", default=0.12,
            env="CBBE2UBE_BUST_FLAT_CLEAR", min=0.0, max=1.5, step=0.01,
            advanced=True,
            hint="Raise toward 0.3 if a bust pokes through; lower to hug.",
            tooltip="How far off the skin the chest conform holds a garment "
                    "everywhere in the bust band, before the nipple ramp and "
                    "the measured morph allowance are added on top. Was 0.3, "
                    "which held a skin-tight bodysuit six times further from "
                    "the body than its author did, so it read as a dark shell "
                    "over the skin. NOT the same number as the anti-poke's own "
                    "bust target, which is untouched."),
    Setting("bust_clear", "Anti-poke bust target at the nipple",
            "Armor", "Fit and clearance", kind="float", default=1.0,
            env="CBBE2UBE_BUST_CLEAR", min=0.0, max=3.0, step=0.05,
            advanced=True,
            hint="How far the bust of a garment is held off the body.",
            tooltip="The anti-poke pass holds a garment this far off the body "
                    "at the nipple, ramping down to the flat-chest value away "
                    "from it. It is a FLOOR, so a garment already sitting "
                    "further out is left alone -- raising it only affects "
                    "pieces that currently sit closer than the new value. NOT "
                    "the same number as the conform ceiling below, which is a "
                    "different pass. Was 1.0."),
    Setting("conform_bust_clear", "Bust clearance ceiling at the nipple",
            "Armor", "Fit and clearance", kind="float", default=0.9,
            env="CBBE2UBE_CONFORM_BUST_CLEAR", min=0.0, max=2.0, step=0.05,
            advanced=True,
            hint="The most the floor above may ramp to at the nipple.",
            tooltip="The chest conform ramps its clearance toward this value as "
                    "it approaches the nipple, so a tip cannot poke through a "
                    "garment that is otherwise hugging. Was 0.9."),
    # These three shipped OPT-IN "until judged in game" and were then impossible
    # to switch on in game -- the deadlock PIPELINE section 6 describes. They are
    # deployed in a test mesh awaiting exactly that verdict, so they need to be
    # reachable for anyone to give one.
    Setting("strap_scale_uniform", "Un-crumple stretched straps",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_STRAP_SCALE_UNIFORM", invert=False,
            hint="For a belt or strap that came out rippled rather than bent.",
            tooltip="Refitting a narrow strap onto a different body can leave "
                    "its edges running anywhere from half to one and a half "
                    "times the length the author gave them. That is not the "
                    "strap bending, it is the strap crumpling. This gives each "
                    "edge the length its own neighbourhood agrees on. Reaches "
                    "about one shape in six across a load order, so it is worth "
                    "a look on more than the piece it was written for."),
    Setting("short_edge_cap", "Stop tiny detail being blown up",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_SHORT_EDGE_CAP", invert=False,
            hint="For small studs, buckles and trim that came out oversized.",
            tooltip="The smallest details in a mesh -- the edges making up a "
                    "stud or a line of stitching -- can be stretched many times "
                    "their original size by the refit, because a tiny edge "
                    "magnifies any error in where its two ends land. This pulls "
                    "those back toward the size the author gave them, while "
                    "leaving any edge that already agrees with its neighbours "
                    "alone, so it stays a repair rather than a resurfacing."),
    Setting("layer_ride_bary", "Ride layers on the surface, not on vertices",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_LAYER_RIDE_BARY", invert=False,
            hint="Slightly steadier placement for belts and trim sitting on "
                 "another layer.",
            tooltip="When a belt or a piece of trim sits on top of another "
                    "layer, it is moved to follow whatever is underneath it. "
                    "That following is worked out from the nearby POINTS of the "
                    "layer below, which shift about slightly; this follows the "
                    "actual spot on its SURFACE instead, which does not. "
                    "Measured as a small reduction in stray spikes with no "
                    "visible change otherwise, so treat it as a refinement "
                    "rather than a fix for anything you can see."),
    Setting("authored_antipoke",
            "Keep the author's tight fit where the body doesn't grow",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_AUTHORED_ANTIPOKE", invert=False,
            hint="Stops the last anti-clipping step loosening a deliberately "
                 "tight garment. Needs the two settings above.",
            tooltip="The final step against skin showing through pushes armour "
                    "out to a set distance from the body. It does that even "
                    "where the author deliberately fitted the garment tight "
                    "and the body does not change shape -- so a corset that "
                    "should hug ends up standing off it. Measured across a set "
                    "of garments, this is the only step that moves armour "
                    "AWAY from the shape its author built, and it is also the "
                    "single biggest source of surface roughness. This lets it "
                    "settle back toward the author's spacing, but ONLY where "
                    "the body underneath does not grow; over the bust, belly "
                    "and backside -- what the step exists for -- it keeps the "
                    "full clearance. It can never push armour further out than "
                    "it does today."),
    Setting("authored_inflate", "Only add clearance where it is missing",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_AUTHORED_INFLATE", invert=False,
            hint="Stops armour being pushed off the body where the author "
                 "already left room. Needs the setting above.",
            tooltip="To keep skin from showing through when body sliders grow "
                    "the body, the converter pushes armour outward. It does "
                    "that blindly -- it adds the same clearance whether or not "
                    "the garment already had plenty. Measured across the whole "
                    "load order (13,889 shapes): on the shapes it moves, that "
                    "push takes two thirds of them FURTHER from the shape "
                    "their author built. This asks instead how much room each "
                    "spot actually needs -- the author's own spacing, or "
                    "enough for the body to grow there, whichever is larger -- "
                    "and tops up only what is short. It can never push a "
                    "vertex further than before, so armour cannot end up "
                    "floating more than it does today."),
    Setting("authored_inflate_amp_cap",
            "  ...room reserved for body growth (units)",
            "Armor", "Fit and clearance", kind="float", default=1.5,
            env="CBBE2UBE_AUTHORED_INFLATE_AMP_CAP", advanced=True,
            min=0.0, max=4.0, step=0.1,
            tooltip="Upper limit on how much room the setting above reserves "
                    "for the body growing at runtime. Uncapped it would track "
                    "the belly, which can grow 8.7 units, and push loose "
                    "clothing away from the body."),
    Setting("src_normal_fix", "Read the author's real fit, not a flat one",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_SRC_NORMAL_FIX", invert=False,
            hint="Keeps a garment's spacing closer to how its author built it.",
            tooltip="To keep the author's fit, the converter has to know how "
                    "far each part of the garment stood off their body. It "
                    "reads that using the body's surface directions -- and "
                    "BodySlide routinely ships bodies with those left blank "
                    "(measured: 18 of 21 sampled). Blank reads as ZERO, so the "
                    "converter believes the garment was skin-tight everywhere "
                    "and reels loose drape inward. With it on, the fit step "
                    "moves the garment TOWARD the author's spacing instead of "
                    "away from it, and the smoothing pass has far less to "
                    "clean up. Measured on a five-layer top: layers on the "
                    "wrong side of each other 1074 -> 706, roughness and "
                    "distortion both down."),
    Setting("layer_order_last", "Let the layer fix have the last word",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_LAYER_ORDER_LAST", invert=False,
            hint="For an under-layer poking through the layer above it.",
            tooltip="Three small repairs -- un-buckling, evening out a "
                    "stretched strap, and pulling back blown-up detail -- run "
                    "again as the garment is written out, AFTER the step that "
                    "puts the layers back in the author's order. On a belt "
                    "they move three quarters of its vertices, which undoes "
                    "that ordering. This runs those repairs first and the "
                    "ordering last. Measured on the piece it was reported "
                    "against: the under-layer poking through the belts went "
                    "from 248 vertices to 80. It costs a little smoothness, "
                    "because those repairs are also cleaning up after the "
                    "layer steps themselves."),
    Setting("family_weight_invariant", "Fix stray broken vertices on layered "
                                       "garments",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_FAMILY_WEIGHT_INVARIANT", invert=False,
            hint="For single vertices dragged into a spike or a flickering "
                 "sliver, usually on an under-layer.",
            tooltip="A vertex is held by at most four bones, and the file "
                    "format enforces that by keeping the four strongest and "
                    "discarding the rest -- without redistributing what it "
                    "discarded. A vertex that ends up holding five is then "
                    "short of the full amount, so it is placed by a partial "
                    "sum of its bones and drifts away from the surface, "
                    "dragging a long thin triangle that flickers depending on "
                    "the angle you view it from. Measured on a reported "
                    "five-layer top: 288 such vertices on the under-layer, "
                    "none in the author's own mesh. This shares the four "
                    "strongest back out to the full amount. It changes only "
                    "the amounts, never which bones hold a vertex."),
    Setting("surface_warp_field", "Smooth the shape the body is warped onto",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_SURFACE_WARP_FIELD", invert=False,
            hint="Removes a stair-step in the field every garment is refit "
                 "through. Biggest at the bust and the hips.",
            tooltip="The refit moves armour by the amount the BODY changes "
                    "shape between CBBE and UBE. That amount is worked out by "
                    "matching each point of one body to the nearest POINT of "
                    "the other -- and points are discrete, so two neighbouring "
                    "spots can be handed changes that do not match, leaving a "
                    "stair-step. Measured: both bodies are smooth (about 4 "
                    "degrees between neighbouring faces) while the shape they "
                    "are warped onto reads 43. Matching to the nearest point "
                    "on the SURFACE instead brings that back to 4.6. In "
                    "practice the refit already averages over four "
                    "neighbours, which hides most of it, so expect a small "
                    "improvement on tight chest pieces and none on a belt. It "
                    "does not change how far armour sits off the body."),
    Setting("full_weight_match", "Match armour skinning to the body it covers",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_FULL_WEIGHT_MATCH", invert=True,
            hint="Stops armour sliding off the body in motion. Confirmed on "
                 "soft leather and rigid plate.",
            tooltip="A CBBE-authored garment carries CBBE weighting on a UBE "
                    "body, so it travels differently from the skin underneath "
                    "and the body slides out from under it while you move. The "
                    "existing passes each fix ONE bone family and pay for it "
                    "out of the others, which trades one pose for another. This "
                    "copies the covered body's whole weight vector on vertices "
                    "that hug it, so nothing is left over to pay with. Measured "
                    "on a leather cuirass: bust exposure in motion 50.9% -> "
                    "3.1%, and no pose worse than before. Moves no vertices, so "
                    "resting fit is untouched. Confirmed in game on a soft "
                    "leather cuirass and on rigid glass plate."),
    Setting("full_weight_strength", "  ...how far to match (0-1)",
            "Armor", "Fit and clearance", kind="float", default=1.0,
            env="CBBE2UBE_FULL_WEIGHT_STRENGTH", advanced=True,
            min=0.0, max=1.0, step=0.05,
            hint="1.0 matched best, and rigid plate at 1.0 was judged good in game.",
            tooltip="0 leaves the garment's own weighting, 1 adopts the body's "
                    "exactly. Higher tracks the body better in every pose "
                    "measured. It sat at 0.6 because a garment deforming exactly "
                    "like skin is right for soft leather and was feared wrong "
                    "for rigid plate; a glass cuirass at 1.0, with 3.97% of its "
                    "mass relocated, was then judged good in game, so 1.0 is the "
                    "default. Lower it if a specific plate piece looks rubbery."),
    Setting("butt_collider_patch", "Add the missing rear collision surface",
            "Armor", "Physics chains (HDT-SMP)", default=True,
            env="CBBE2UBE_NO_BUTT_COLLIDER_PATCH", invert=True,
            hint="For skirts that clip through the buttocks. Equip-tested and "
                 "confirmed in game.",
            tooltip="Many CBBE-authored armours ship a collider that stops at "
                    "the smaller CBBE buttock, so on the larger UBE body a "
                    "simulated skirt has nothing to rest on and sinks into the "
                    "skin. This adds a hidden collision surface taken from the "
                    "body itself, and only on pieces measured to need it. It "
                    "equipped cleanly in game. On its own it improved the defect "
                    "without finishing it -- what finishes it is lifting the "
                    "chain out of the body, below."),
    Setting("skirt_proxy_rebuild", "Let the visible skirt collide, not just its proxy",
            "Armor", "Physics chains (HDT-SMP)", default=True,
            env="CBBE2UBE_NO_SKIRT_PROXY_REBUILD", invert=True,
            hint="Touches simulated cloth. Confirmed in game, in motion.",
            tooltip="Some armours represent their cloth in the physics with a "
                    "very coarse stand-in that does not resemble the skirt you "
                    "actually see -- on the test piece it reached only 1/3 of "
                    "the way to the visible hem. The simulation then holds the "
                    "stand-in off the body correctly while the rendered skirt "
                    "goes through it. This adds a proxy built from the visible "
                    "cloth. It ADDS rather than replacing, so the authored "
                    "physics is untouched. Higher risk than the collider patch, "
                    "because this is simulated geometry -- it was checked in "
                    "motion for ballooning and collapse before being defaulted "
                    "on. Untick it first if a skirt starts behaving oddly."),
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
    Setting("per_anchor_seed", "Place each physics chain's anchor separately",
            "Armor", "Physics chains (HDT-SMP)", default=False,
            env="CBBE2UBE_PER_ANCHOR_SEED", invert=False,
            hint="Fixes skirts hanging far below the body on outfits that mix "
                 "a hip chain with a chest one.",
            tooltip="Chain anchors are placed in one pass for the whole "
                    "outfit, and that pass gives up entirely if ANY of the "
                    "outfit's chains hangs from the upper body. On an outfit "
                    "that mixes the two -- a skirt from the hips plus a small "
                    "chest chain, which is common -- nothing is placed at all "
                    "and every chain lands about 69 units low, at floor "
                    "level. Measured on vanilla iron armour: 38 chain nodes, "
                    "most of them below the ground. This places each anchor on "
                    "its own merits, so the hip chains are positioned "
                    "correctly whatever else the outfit carries."),
    Setting("mixed_cloth_clearance",
            "Give the fitted parts of physics outfits body clearance",
            "Armor", "Fit and clearance", default=False,
            env="CBBE2UBE_MIXED_CLOTH_CLEARANCE", invert=False,
            hint="For a piece that is part simulated cloth, part fitted cloth: "
                 "the fitted part gets no clearance.",
            tooltip="Clearance is skipped for anything carrying physics "
                    "rigging, because moving simulated cloth fights the "
                    "simulation. But one piece often holds BOTH a simulated "
                    "skirt and an ordinary fitted top, and the whole piece is "
                    "skipped on account of the skirt -- so the top never "
                    "receives a bust or body clearance target and the body can "
                    "push through it. Measured across the pack: 392 of 482 "
                    "skipped pieces are mixed this way, 57%% of their vertices "
                    "carry no physics weight at all, and 198 pieces have chest "
                    "cloth no clearance can currently reach. This clears only "
                    "the vertices carrying no physics weight; every simulated "
                    "vertex is put back exactly where the simulation expects "
                    "it."),
    Setting("chain_rest_lift", "Lift physics chains out of the body",
            "Armor", "Physics chains (HDT-SMP)", default=True,
            env="CBBE2UBE_NO_CHAIN_REST_LIFT", invert=True,
            hint="Stop a skirt being pulled inside the hips it hangs over. "
                 "This is what fixes the long-standing buttock clip.",
            tooltip="A skirt's chain bones keep their SOURCE rest position "
                    "while the body grows to UBE proportions, so on a fuller "
                    "body they end up sitting INSIDE it. The physics solver "
                    "pulls the cloth back toward those bones every frame while "
                    "collision pushes it out, so the skirt settles part-way "
                    "inside -- the same at standstill and in motion, which is "
                    "why extra collision never finished the job. This moves "
                    "each affected chain's ROOT outward until no bone of it "
                    "rests inside the body, including the room the body still "
                    "has to grow under your RaceMenu sliders. The chain "
                    "translates rigidly (measured worst inter-bone change "
                    "0.000000u -- warping chain bones individually is what "
                    "makes a chain explode). Trade-off: the free-hanging lower "
                    "part of the skirt moves out by the same amount (0.5u on "
                    "the test piece), so it can stand off further than the "
                    "author intended. Confirmed in game, in motion. If a skirt "
                    "looks like it is held too far off the hips, this is the "
                    "one to untick."),
    Setting("chest_follow", "Chest follow ratio",
            "Armor", "Body follow and morphs", default=True,
            env="CBBE2UBE_NO_CHEST_FOLLOW", invert=True,
            hint="Make chest cloth track the morphed bust instead of standing off it.",
            tooltip="Let a fitted soft-material top track the body's breast motion "
                    "by the amount its own clearance actually requires, instead of "
                    "an absolute weight cap that leaves it following about a third "
                    "of the body. Targets 'chest clips only when moving'. Metal "
                    "armour keeps the old conservative cap. DEFAULT ON since 1.2, "
                    "after a full-pack conversion and in-game use. Untick it if "
                    "stiff armour starts reading as rubbery."),
    Setting("drape_xml_gate", "Fit robes/dresses that declare their own physics",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_DRAPE_XML_GATE", invert=True,
            hint="Also fit robes and dresses that declare their own physics.",
            tooltip="Robes, dresses, cloaks and capes are skipped by every fitting "
                    "pass, because some of them are cloth driven by a game-wide "
                    "physics config that cannot be detected from the mesh -- and "
                    "adjusting those has crashed on equip. This narrows the skip to "
                    "pieces that do NOT ship their own physics file, so a robe whose "
                    "physics IS declared can be fitted like any other garment. "
                    "DEFAULT ON since 1.2, after a full-pack conversion and "
                    "in-game use. The failure mode is a crash when equipping a "
                    "robe, so if that happens, untick this first."),
    Setting("source_follow", "...judge by the outfit's own weighting, not its name",
            "Armor", "Body follow and morphs", default=True,
            env="CBBE2UBE_NO_SOURCE_FOLLOW", invert=True,
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
    Setting("smp_antipoke", "Bust clearance on SMP collider armor",
            "Armor", "Fit and clearance", default=True,
            env="CBBE2UBE_NO_SMP_ANTIPOKE", invert=True,
            hint="Push simulated cloth clear of the body so the bust stops poking through.",
            tooltip="An armor whose physics config names it only as a COLLIDER "
                    "currently gets no bust clearance at all, so the body pushes "
                    "straight through it -- the 'chest clips when moving' case on "
                    "cuirasses with their own physics. Measured 6.3% -> 3.3% "
                    "exposed on one such cuirass. DEFAULT ON since 1.2, after a "
                    "full-pack conversion and in-game use. Untick it if a bust "
                    "looks spread or faceted -- pushing verts out on a convex "
                    "region has done that before."),
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
            "drape_xml_gate", "conform_to_body", "conform_fold_guard",
            "warp_shear_limit", "warp_delta_outlier",
            "warp_delta_outlier_max", "warp_push_shell_cap",
            "surface_warp_field", "src_normal_fix",
            "strap_scale_uniform", "short_edge_cap", "layer_ride_bary",
            "authored_antipoke",
            "authored_inflate",
            "authored_inflate_amp_cap", "layer_order_last",
            "family_weight_invariant",
            "full_weight_match", "full_weight_strength",
            "smp_antipoke", "smp_antipoke_push",
            "antipoke_smooth", "layered_antipoke")),
        ("Body follow and morphs", (
            "chest_follow", "chest_follow_unknown", "source_follow",
            "rigid_majority_softbody")),
        ("Jiggle transfer", (
            "jiggle_transfer", "jiggle_transfer_factor", "torso_jiggle",
            "butt_jiggle", "chest_jiggle", "jiggle_clearance",
            "jiggle_clearance_gain", "jiggle_clearance_max",
            "disable_softbody_scales")),
        ("Physics chains (HDT-SMP)", (
            "butt_collider_patch", "skirt_proxy_rebuild",
            "leg_chain_guard", "chain_to_softbody", "static_chains",
            "nested_chain_anchors", "chain_torso")),
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
