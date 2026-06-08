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

"""Generator for per-armor HDT-SMP collision XMLs.

Produces a minimal but functional HDT-SMP config that gives every
cloth shape in a converted armor NIF its own collision shape paired
with the body. At runtime the engine then enforces a body↔cloth
intersection constraint, preventing the body from clipping through
armor under body morph / jiggle physics motion.

What we DO emit:

  * Bone declarations for every bone the cloth shapes are weighted
    to. They inherit default mass=0 (static), so they don't gain
    independent physics — they're anchors the cloth collision binds
    to.
  * A per-triangle-shape for the body collision proxy (VirtualBody
    or BaseShape, depending on what the NIF actually carries).
  * One per-vertex-shape per cloth shape, with weight-threshold
    entries derived from the shape's actual NIF skinning.

What we DO NOT emit (deferred, see "Phase D" in the research plan):

  * Custom physics-chain bones (physics-chain bones (prefix_NN) style). Those
    would give cloth its own secondary jiggle motion. They require
    inventing rigging (new bones, weight painting, rest poses,
    constraints between successive chain bones) — substantial work.
    Skipping them costs the cloth its own swing, but the collision-
    only XML still solves the most common body-pokes-through-armor
    complaint.

Format reference: see project_cbbe_to_ube.md and the hand-authored
examples in the user's modlist (a hand-authored body physics XML for body,
a hand-authored physics XML for armor).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# -- Bone classification ----------------------------------------------------
#
# When emitting <weight-threshold> entries we choose a value based on the
# kind of bone:
#
#   1.0  =  cloth must FULLY track this bone — applies to both the
#           standard skeleton (Spine, Pelvis, Thigh, ...) and 3BA
#           scale bones (Breast01-03, Butt, Belly, FrontThigh, ...).
#           The scale bones are where body physics happens; cloth at
#           1.0 follows them exactly, which is the entire point of
#           this XML.
#   0.3  =  cloth MAY track this bone — secondary / unknown bones,
#           often custom armor accent bones the source mod adds. A
#           lower threshold means HDT-SMP treats the binding as
#           optional; the cloth can detach from the bone if other
#           higher-threshold bones already constrain it.

SCALE_BONE_PATTERNS = (
    "breast",
    "butt",
    "belly",
    "frontthigh", "rearthigh", "rearcalf",
    "clit", "pussy", "vagina", "anus", "nipple",
)

SKELETON_BONE_PATTERNS = (
    "npc spine", "npc pelvis",
    "npc l thigh", "npc r thigh",
    "npc l calf", "npc r calf",
    "npc l foot", "npc r foot",
    "npc l toe", "npc r toe",
    "npc l clavicle", "npc r clavicle",
    "npc l upperarm", "npc r upperarm",
    "npc l upperarmtwist", "npc r upperarmtwist",
    "npc l forearm", "npc r forearm",
    "npc l forearmtwist", "npc r forearmtwist",
    "npc l hand", "npc r hand",
    "npc head",
)

DEFAULT_BONE_THRESHOLD = 0.3
PRIMARY_BONE_THRESHOLD = 1.0


def classify_bone_threshold(bone_name: str) -> float:
    """Return the weight-threshold value to use for `bone_name`.

    1.0 for skeleton + 3BA scale bones. 0.3 for everything else.
    """
    lower = bone_name.lower()
    if any(p in lower for p in SCALE_BONE_PATTERNS):
        return PRIMARY_BONE_THRESHOLD
    if any(p in lower for p in SKELETON_BONE_PATTERNS):
        return PRIMARY_BONE_THRESHOLD
    return DEFAULT_BONE_THRESHOLD


# -- Tuning constants -------------------------------------------------------
#
# Margin = collision skin thickness around a shape. Verts inside the
# margin count as colliding.
#
# Prenetration = how much surface overlap the engine tolerates before
# treating it as a collision event. Higher = engine reacts sooner /
# applies corrective force at smaller overlap.
#
# Body params are kept conservative globally. High-velocity regions
# (breast / butt / belly) get widened collision via per-bone
# `<margin-multiplier>` overrides — see HIGH_VELOCITY_BONE_PATTERNS
# below. This avoids puffy-looking cloth on shoulders / arms / legs
# while still catching the sudden-stop jiggle clip that pure global
# margin bumps would otherwise need huge values to fix.
#
# Cloth params stay tight (margin=0.1, prenetration=0.1) — that
# matches the per-vertex-shape convention in both a hand-authored UBE armor XMLs.

DEFAULT_CLOTH_MARGIN = 0.1
DEFAULT_CLOTH_PRENETRATION = 0.1
DEFAULT_BODY_MARGIN = 0.5
DEFAULT_BODY_PRENETRATION = 0.2


# -- Per-bone collision tuning ----------------------------------------------
#
# Bones whose verts move at HIGH velocity during normal motion. The
# body collision constraint can tunnel through cloth between
# simulation frames during sudden deceleration on these. Emitting a
# larger margin-multiplier on these bones effectively widens the body
# collision skin where the velocity spikes live — the engine reacts
# to imminent contact sooner, no global cloth puffiness elsewhere.
HIGH_VELOCITY_BONE_PATTERNS = (
    "breast01", "breast02", "breast03",   # CBBE 3BA breast physics chain
    "l breast", "r breast",
    "npc l butt", "npc r butt",           # buttock scale bones
    "npc belly",                          # belly bone
    # Legs swing fast during walk/run/jump, so a loose skirt/robe/coat tunnels
    # THROUGH the thigh/calf collision between sim frames -- the #1 "cloth clips
    # the legs when moving" case, which the breast/butt/belly set above does
    # nothing for. Widening the leg collision skin makes the body react to
    # imminent contact sooner. Substring-matched against "NPC L Thigh [LThg]" /
    # "NPC R Calf [RClf]" (and the FrontThigh/RearCalf scale bones in the same
    # region, which is harmless -- it reinforces the same leg surface).
    "thigh", "calf",
)
HIGH_VELOCITY_MARGIN_MULTIPLIER = 2.0     # 2x DEFAULT_BODY_MARGIN = ~1u skin


def is_high_velocity_bone(bone_name: str) -> bool:
    """Return True if the bone is a high-velocity collider — the breast /
    butt / belly chain OR the legs (thigh/calf, which swing fast in
    locomotion) — whose motion needs a widened collision skin to stop
    cloth tunnelling through it between simulation frames."""
    lower = bone_name.lower()
    return any(p in lower for p in HIGH_VELOCITY_BONE_PATTERNS)


# -- Physics chain detection (Escalation A) ---------------------------------
#
# HDT-SMP physics chains are sequences of bones with names like
# "Skirt 1_00", "Skirt 1_01", ..., "Skirt 1_05" — a prefix plus an
# underscore plus a two-or-three-digit index. The bone with index 00
# is the static ANCHOR (parented to a real body bone in the NIF
# skeleton); subsequent bones are dynamic and swing via the engine's
# constraint-solver simulation.
#
# Source armor mods that ship with HDT-SMP physics have these bones
# already in their NIF skeleton + skinned to cloth verts. The
# original mod's XML defined constraints for them. Our converter
# would otherwise emit an XML without physics constraints for these
# bones, breaking the cloth physics that the mod author hand-rigged.
#
# This detection lets us REUSE those chains: collect bones grouped
# by prefix, generate the appropriate <bone-default> + <constraint-
# group> blocks. We don't add new bones — we just notice the ones
# already in the NIF and emit XML matching what BodySlide/HDT-SMP
# expects.
#
# Naming patterns covered:
#   "Skirt 1_00", "Skirt 1_01", ...           (vanilla HDT armor)
#   "SkirtF 1_00", "SkirtB 2_03", ...         (multi-axis variants)
#   "a physics-chain bone (prefix_NN)", "a physics-chain bone (prefix_NN)"  (a hand-authored UBE armor)
#   "Hair 1_00"                               (hair physics chains, just in case)

CHAIN_BONE_PATTERN = re.compile(r"^(.+?)_(\d{1,3})$")
MIN_CHAIN_LENGTH = 2  # need at least anchor + 1 dynamic to be a chain


@dataclass
class PhysicsChain:
    """A detected chain: an ordered sequence of bone names from index
    00 (anchor) upward. The anchor is static (mass=0) and parented
    in the NIF skeleton to a body bone; subsequent bones swing via
    constraint physics."""
    prefix: str          # "Skirt 1", "a physics-chain bone FR", etc.
    bones: list[str] = field(default_factory=list)  # ["...Skirt 1_00", "_01", ...]


def detect_physics_chains(bone_names: Iterable[str]) -> "list[PhysicsChain]":
    """Group bone names by their <prefix>_NN suffix pattern, returning
    one PhysicsChain per detected group with at least MIN_CHAIN_LENGTH
    bones. Chains are sorted by prefix (deterministic XML output)
    and bones within each chain are sorted by their numeric index.

    Bones not matching the pattern (NPC Spine, L Breast01, etc. — the
    standard skeleton + scale bones) are silently filtered out. They
    don't need chain physics, only the actual physics-chain bones do.
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for name in bone_names:
        m = CHAIN_BONE_PATTERN.match(name)
        if not m:
            continue
        prefix = m.group(1)
        idx = int(m.group(2))
        # Skip the standard breast bones that match the pattern by
        # coincidence — "L Breast01" matches (prefix="L Breast", idx=1).
        # Those aren't physics chains we should constrain ourselves;
        # they belong to the body's master physics config.
        if any(kw in prefix.lower() for kw in ("breast",)):
            continue
        grouped.setdefault(prefix, []).append((idx, name))

    result: list[PhysicsChain] = []
    for prefix in sorted(grouped):
        items = sorted(grouped[prefix])
        if len(items) < MIN_CHAIN_LENGTH:
            continue
        result.append(PhysicsChain(
            prefix=prefix,
            bones=[name for _, name in items],
        ))
    return result


# -- Chain physics tuning ---------------------------------------------------
#
# Data-driven (2026-06-05): values measured against the modlist's corpus of
# 124 hand-authored HDT-SMP chain XMLs (skirts/capes/hair). Earlier defaults
# were eyeballed and sat OUTSIDE the authored distribution on the params that
# matter most for look + stability:
#   - angularDamping was 0.5; authored cloth clusters at 0.95-0.99. Low
#     angular damping under-damps rotational motion -> the chain oscillates /
#     spins and, combined with a stiff spring, the solver can blow up
#     (a contributor to "wild" or pull-to-origin generated cloth).
#   - linear/angular stiffness was 500; authored cloth clusters at 20-30
#     (mode 20). A 500 spring is ~25x too rigid -> barely sways AND pairs
#     badly with low damping (stiff + under-damped = unstable).
#   - linearUpperLimit had y=0.5 (chain-axis stretch); authored chains keep
#     linear limits ~0 (near-rigid link length, motion comes from rotation).
# The anchor bone (index 00) stays mass=0 (static, follows its NIF-skeleton
# parent); dynamic links get mass + the measured damping/spring profile.

CHAIN_ANCHOR_MASS = 0.0
CHAIN_DYNAMIC_MASS = 0.3       # authored spread 0.1-0.9; 0.3 = stable mid
CHAIN_INERTIA = 100.0          # authored 70-150 (mode 100); was 5 -> too twitchy
CHAIN_LINEAR_DAMPING = 0.5     # authored 0.3-0.6; unchanged
CHAIN_ANGULAR_DAMPING = 0.97   # authored 0.95-0.99 (was 0.5 -> under-damped)
CHAIN_FRICTION = 0.5           # authored mode 0.75 (was 0.2); 0.5 = mid
CHAIN_RESTITUTION = 0.5        # authored mode 0.5 (was 0.2)

# Constraint defaults — the spring/damper parameters governing how each
# chain link moves relative to its predecessor. Soft springs + tight linear
# limits + high angular damping = the authored "soft, visible, settles
# smoothly" sway (vs the old stiff/under-damped profile).
CHAIN_LINEAR_LOWER = (-0.05, -0.05, -0.05)   # near-rigid link length
CHAIN_LINEAR_UPPER = (0.05, 0.05, 0.05)      # was (0.1, 0.5, 0.1) -> over-stretch
CHAIN_ANGULAR_LOWER = (-0.1, -0.1, -0.1)     # authored common +/-0.1
CHAIN_ANGULAR_UPPER = (0.1, 0.1, 0.1)
CHAIN_LINEAR_STIFFNESS = 20.0   # authored mode 20 (was 500 -> too rigid)
CHAIN_ANGULAR_STIFFNESS = 20.0  # authored mode 20 (was 500)
CHAIN_CONSTRAINT_DAMPING = 0.5


# -- Generation -------------------------------------------------------------

def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def generate_armor_hdt_xml(
    cloth_shapes: "list[tuple[str, Iterable[str]]]",
    body_collision_shape_name: "str | None" = "VirtualBody",
    *,
    chains: "list[PhysicsChain] | None" = None,
    cloth_margin: float = DEFAULT_CLOTH_MARGIN,
    cloth_prenetration: float = DEFAULT_CLOTH_PRENETRATION,
    body_margin: float = DEFAULT_BODY_MARGIN,
    body_prenetration: float = DEFAULT_BODY_PRENETRATION,
) -> str:
    """Build the HDT-SMP XML body as a string.

    Args:
      cloth_shapes: list of `(shape_name, bone_names)`. One entry per
        cloth shape that should collide with the body. `bone_names`
        is iterable of strings — typically the shape's `bone_names`
        attribute from pynifly.
      body_collision_shape_name: name of the shape in the NIF that
        serves as the body collision proxy. "VirtualBody" is the UBE
        convention; "BaseShape" works for some NIFs. Must exist in
        the NIF or runtime collision will silently no-op. Pass None
        for a cloth-only NIF (no inline body proxy): the body
        per-triangle-shape block is omitted and the cloth instead
        collides with whatever provides the "body" tag in the actor's
        merged SMP system at runtime (the worn body's own physics XML).
      cloth_margin, cloth_prenetration: per-vertex-shape physics
        params. Smaller margin = tighter cloth-to-body contact.
      body_margin, body_prenetration: per-triangle-shape physics
        params for the body collision proxy.

    Returns:
      Pretty-printed XML string with UTF-8 declaration, ready to
      write to disk.

    The output structure (matches hand-authored physics XMLs convention):

      <system ... >
        <bone name="..."/>            (every bone the cloth uses)
        ...
        <per-triangle-shape name="VirtualBody">
          <tag>body</tag>
          <can-collide-with-tag>cloth1</can-collide-with-tag>
          ...
          <weight-threshold bone="...">N</weight-threshold>
          ...
        </per-triangle-shape>
        <per-vertex-shape name="<cloth shape 1>">
          <tag>cloth1</tag>
          <can-collide-with-tag>body</can-collide-with-tag>
          <weight-threshold bone="...">N</weight-threshold>
          ...
        </per-vertex-shape>
        ...
      </system>
    """
    # Union of bones across all cloth shapes — declare once at top.
    all_bones: list[str] = []
    seen: set[str] = set()
    for _, bones in cloth_shapes:
        for b in (bones or []):
            if b not in seen:
                seen.add(b); all_bones.append(b)
    # Sort alphabetically so two runs over the same input produce
    # identical XML (helps with diffs and regression tests).
    all_bones.sort()

    # Separate static body bones from physics-chain bones. The chain
    # bones get DIFFERENT physics in section 2 below (mass>0, damped
    # springs) versus the body bones (mass=0 anchors). We don't want
    # to emit chain bones in the first <bone> section — that would
    # apply default mass=0 to them and they wouldn't swing.
    chains = chains or []
    chain_bone_set: set[str] = set()
    for ch in chains:
        chain_bone_set.update(ch.bones)
    static_bones = [b for b in all_bones if b not in chain_bone_set]

    # Per-cloth tags. Numbered so each cloth has its own tag — the
    # body's can-collide-with-tag list enumerates them all, and each
    # cloth has only its own tag. Means cloth pieces don't collide
    # with each OTHER (intended — layered cloth would otherwise lock
    # into each other under physics).
    cloth_tags = [f"cloth{i + 1}" for i in range(len(cloth_shapes))]

    # Build lines manually rather than via ElementTree so we get the
    # exact whitespace/escaping style HDT-SMP parsers expect and the
    # tab indentation that matches hand-authored files (some HDT
    # parsers are picky about formatting; matching the convention is
    # the safe path).
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<system xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
                 ' xsi:noNamespaceSchemaLocation="description.xsd">')
    lines.append("")
    lines.append("\t<!-- Auto-generated by cbbe-to-ube. Cloth↔body collision only;")
    lines.append("\t     no custom physics-chain bones. See src/hdt_xml_gen.py. -->")
    lines.append("")

    # 1. Bone declarations. Most are self-closing empty tags. The
    # high-velocity body bones (breast/butt/belly) get an inline
    # <margin-multiplier> so HDT-SMP widens the collision skin
    # LOCALLY around them — catches sudden-stop tunneling without
    # making the entire body shape puffy.
    #
    # Chain bones (Skirt 1_NN style) are NOT declared here — they
    # get their own dynamic-physics block below so they swing as a
    # chain rather than acting as static anchors.
    lines.append("\t<!-- bones (static) -->")
    for b in static_bones:
        escaped = _xml_escape(b)
        if is_high_velocity_bone(b):
            lines.append(f'\t<bone name="{escaped}">')
            lines.append(f"\t\t<margin-multiplier>"
                          f"{HIGH_VELOCITY_MARGIN_MULTIPLIER}"
                          f"</margin-multiplier>")
            lines.append("\t</bone>")
        else:
            lines.append(f'\t<bone name="{escaped}"/>')
    lines.append("")

    # 1b. Physics-chain bones (Escalation A: chains already in the
    # source NIF skeleton). Anchor = mass 0 (static; moves with its
    # NIF-skeleton parent body bone). Dynamic = mass 0.25 (swings).
    # The constraint groups in section 4 connect successive bones.
    if chains:
        lines.append("\t<!-- physics chains (anchors static, others dynamic) -->")
        # Anchor bones — mass=0 so they're driven only by the NIF
        # skeleton (move with character body during animation, but
        # don't fall under gravity).
        anchor_default_emitted = False
        if not anchor_default_emitted:
            lines.append("\t<bone-default>")
            lines.append(f"\t\t<mass>{CHAIN_ANCHOR_MASS}</mass>")
            lines.append("\t</bone-default>")
            anchor_default_emitted = True
        for ch in chains:
            # First bone is the anchor
            anchor = ch.bones[0]
            lines.append(f'\t<bone name="{_xml_escape(anchor)}"/>')
        lines.append("")
        # Dynamic bones — full physics defaults applied via bone-default,
        # then the bones declared after pick them up.
        lines.append("\t<bone-default>")
        lines.append(f"\t\t<mass>{CHAIN_DYNAMIC_MASS}</mass>")
        lines.append(f'\t\t<inertia x="{CHAIN_INERTIA}" y="{CHAIN_INERTIA}"'
                     f' z="{CHAIN_INERTIA}"/>')
        lines.append("\t\t<centerOfMassTransform>")
        lines.append('\t\t\t<basis x="0" y="0" z="0" w="1"/>')
        lines.append('\t\t\t<origin x="0" y="0" z="0"/>')
        lines.append("\t\t</centerOfMassTransform>")
        lines.append(f"\t\t<linearDamping>{CHAIN_LINEAR_DAMPING}</linearDamping>")
        lines.append(f"\t\t<angularDamping>{CHAIN_ANGULAR_DAMPING}</angularDamping>")
        lines.append(f"\t\t<friction>{CHAIN_FRICTION}</friction>")
        lines.append(f"\t\t<restitution>{CHAIN_RESTITUTION}</restitution>")
        lines.append("\t</bone-default>")
        for ch in chains:
            # Dynamic bones (everything past the anchor)
            for b in ch.bones[1:]:
                lines.append(f'\t<bone name="{_xml_escape(b)}"/>')
        lines.append("")

    # 2. Constraint groups for each chain — pairwise sequential
    # constraints binding bone[i] -> bone[i-1]. The default block
    # encodes the spring/damper params; the constraints declare
    # which bone pairs to apply them to.
    if chains:
        lines.append("\t<!-- chain constraints (sequential springs) -->")
        lines.append("\t<generic-constraint-default>")
        lines.append("\t\t<frameInB>")
        lines.append('\t\t\t<basis x="0" y="0" z="0" w="1"/>')
        lines.append('\t\t\t<origin x="0" y="0" z="0"/>')
        lines.append("\t\t</frameInB>")
        lines.append("\t\t<useLinearReferenceFrameA>false</useLinearReferenceFrameA>")
        ll = CHAIN_LINEAR_LOWER; lu = CHAIN_LINEAR_UPPER
        al = CHAIN_ANGULAR_LOWER; au = CHAIN_ANGULAR_UPPER
        lines.append(f'\t\t<linearLowerLimit x="{ll[0]}" y="{ll[1]}" z="{ll[2]}"/>')
        lines.append(f'\t\t<linearUpperLimit x="{lu[0]}" y="{lu[1]}" z="{lu[2]}"/>')
        lines.append(f'\t\t<angularLowerLimit x="{al[0]}" y="{al[1]}" z="{al[2]}"/>')
        lines.append(f'\t\t<angularUpperLimit x="{au[0]}" y="{au[1]}" z="{au[2]}"/>')
        s = CHAIN_LINEAR_STIFFNESS; a = CHAIN_ANGULAR_STIFFNESS
        lines.append(f'\t\t<linearStiffness x="{s}" y="{s}" z="{s}"/>')
        lines.append(f'\t\t<angularStiffness x="{a}" y="{a}" z="{a}"/>')
        d = CHAIN_CONSTRAINT_DAMPING
        lines.append(f'\t\t<linearDamping x="{d}" y="{d}" z="{d}"/>')
        lines.append(f'\t\t<angularDamping x="{d}" y="{d}" z="{d}"/>')
        lines.append('\t\t<linearEquilibrium x="0" y="0" z="0"/>')
        lines.append('\t\t<angularEquilibrium x="0" y="0" z="0"/>')
        lines.append("\t</generic-constraint-default>")
        lines.append("")
        for ch in chains:
            lines.append("\t<constraint-group>")
            for i in range(1, len(ch.bones)):
                a_name = ch.bones[i]
                b_name = ch.bones[i - 1]
                lines.append(
                    f'\t\t<generic-constraint bodyA="{_xml_escape(a_name)}"'
                    f' bodyB="{_xml_escape(b_name)}"/>'
                )
            lines.append("\t</constraint-group>")
        lines.append("")

    # 3. Body collision shape — declares which body bones to test
    # against cloth verts. The cloth weight thresholds in section 4
    # do the symmetric mapping on the cloth side.
    #
    # OPTIONAL: a NIF with no body-proxy shape (e.g. a slot-49 cloth-only
    # armor — skirt/tabard with no inline body) has nothing local to use
    # as the body collision. We still emit the cloth per-vertex-shapes
    # below; their `<can-collide-with-tag>body</can-collide-with-tag>`
    # binds to whatever provides the "body" tag in the actor's merged
    # SMP system at runtime — i.e. the worn BODY's own physics XML. So
    # cloth-only armor still simulates + collides with the body without
    # a local proxy. Skip this section when body_collision_shape_name
    # is None.
    if body_collision_shape_name is not None:
        lines.append(f'\t<per-triangle-shape name="{_xml_escape(body_collision_shape_name)}">')
        lines.append(f"\t\t<margin>{body_margin}</margin>")
        lines.append(f"\t\t<prenetration>{body_prenetration}</prenetration>")
        lines.append("\t\t<shared>private</shared>")
        lines.append("\t\t<tag>body</tag>")
        for tag in cloth_tags:
            lines.append(f"\t\t<can-collide-with-tag>{tag}</can-collide-with-tag>")
        lines.append("")
        # Body shape only declares weight-thresholds for STATIC bones —
        # chain bones don't apply to the body collision (they're on the
        # cloth side). Listing them here would attach the body collision
        # to cloth verts that move via the chain, which is wrong.
        for b in static_bones:
            thresh = classify_bone_threshold(b)
            lines.append(f'\t\t<weight-threshold bone="{_xml_escape(b)}">{thresh}</weight-threshold>')
        lines.append("\t</per-triangle-shape>")
        lines.append("")

    # 4. Cloth shapes (one per cloth)
    # Cloth shapes DO declare chain-bone thresholds — those are the
    # bones the cloth verts are weighted to that drive the secondary
    # motion. Threshold 0.3 (vs 1.0 for static bones) follows the
    # hand-authored convention from hand-authored physics XMLs.
    CHAIN_BONE_THRESHOLD = 0.3
    for i, (shape_name, bones) in enumerate(cloth_shapes):
        tag = cloth_tags[i]
        lines.append(f'\t<per-vertex-shape name="{_xml_escape(shape_name)}">')
        lines.append(f"\t\t<margin>{cloth_margin}</margin>")
        lines.append(f"\t\t<prenetration>{cloth_prenetration}</prenetration>")
        lines.append("\t\t<shared>private</shared>")
        lines.append(f"\t\t<tag>{tag}</tag>")
        lines.append("\t\t<can-collide-with-tag>body</can-collide-with-tag>")
        lines.append("")
        # Emit thresholds in the same alphabetical order for diff stability.
        # Chain bones get the lower 0.3 threshold (secondary motion);
        # everything else uses the standard classifier (1.0 or 0.3).
        shape_bones = sorted(set(bones or []))
        for b in shape_bones:
            if b in chain_bone_set:
                thresh = CHAIN_BONE_THRESHOLD
            else:
                thresh = classify_bone_threshold(b)
            lines.append(f'\t\t<weight-threshold bone="{_xml_escape(b)}">{thresh}</weight-threshold>')
        lines.append("\t</per-vertex-shape>")
        lines.append("")

    lines.append("</system>")
    return "\n".join(lines) + "\n"


def write_armor_hdt_xml(
    output_xml_path: "Path",
    cloth_shapes: "list[tuple[str, Iterable[str]]]",
    body_collision_shape_name: "str | None" = "VirtualBody",
    *,
    chains: "list[PhysicsChain] | None" = None,
    **kwargs,
) -> None:
    """Generate + write the XML to `output_xml_path` (UTF-8, LF endings).

    Convenience wrapper around `generate_armor_hdt_xml`. Creates the
    parent directory if needed. Overwrites any existing file at the
    target path.
    """
    xml = generate_armor_hdt_xml(
        cloth_shapes,
        body_collision_shape_name=body_collision_shape_name,
        chains=chains,
        **kwargs,
    )
    output_xml_path = Path(output_xml_path)
    output_xml_path.parent.mkdir(parents=True, exist_ok=True)
    output_xml_path.write_text(xml, encoding="utf-8")


# -- Discovery helpers ------------------------------------------------------

def pick_body_collision_shape_name(nif_shape_names: Iterable[str]) -> "str | None":
    """Decide which shape in the NIF to use as the body collision
    proxy in the generated XML.

    Strategy:
      1. Prefer "VirtualBody" (UBE convention — already designed as
         a collision-only proxy with no textures).
      2. Fall back to "BaseShape" (works but uses the full visible
         body; a bit heavier at runtime).
      3. None if neither exists — caller should skip XML generation
         (no body collision means there's nothing for the cloth to
         collide AGAINST).
    """
    names = list(nif_shape_names)
    if "VirtualBody" in names:
        return "VirtualBody"
    if "BaseShape" in names:
        return "BaseShape"
    return None


def generate_body_collision_xml(
        body_shape_name: str = "BaseShape",
        bone_names: "Iterable[str]" = (),
        *,
        margin: float = 0.01,
        prenetration: float = 0.2,
) -> str:
    """Generate a minimal NUDE-BODY collider XML for the UBE body.

    The UBE body ships with NO HDT-SMP physics, so cloth that
    `can-collide-with body` (the converter's generated cloth + most authored
    dresses/capes) has nothing to collide AGAINST on UBE -> it clips through the
    body/legs when moving. This emits a single `per-triangle-shape` on the body
    mesh tagged `body`, modeled on CBBE 3BA's `3BBB` collider (which is the FULL
    body mesh -> one shape covers torso + legs + arms; the 3BCA_* regional tags
    are opt-in refinements). Attach the XML to the worn body NIF via an HDT
    extra-data ref ("HDT Skinned Mesh Physics Object").

    Complements phase-2's self-contained `VirtualBody` (full body-slot outfits
    already self-collide); this covers nude/visible bodies, capes/cloaks worn
    over them, and NPCs. Bones are declared with NO <mass> (kinematic) so the
    actor skeleton drives the collider. The cloth side needs no change: its
    existing `<can-collide-with-tag>body</can-collide-with-tag>` triggers the
    collision (the body shape itself carries no can-collide-with list -- matches
    3BA's 3BBB).
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<system xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:noNamespaceSchemaLocation="description.xsd">',
        '\t<!-- Nude-body collider: gives "body"-colliding cloth something to '
        'hit on the UBE body (no SMP physics otherwise). #cloth-clip -->',
    ]
    for bn in bone_names:
        lines.append(f'\t<bone name="{_xml_escape(bn)}"/>')
    lines.append(f'\t<per-triangle-shape name="{_xml_escape(body_shape_name)}">')
    lines.append(f"\t\t<margin>{margin}</margin>")
    lines.append(f"\t\t<prenetration>{prenetration}</prenetration>")
    lines.append("\t\t<tag>body</tag>")
    lines.append("\t\t<no-collide-with-tag>body</no-collide-with-tag>")
    lines.append("\t</per-triangle-shape>")
    lines.append("</system>")
    return "\n".join(lines) + "\n"


# -- Validation -------------------------------------------------------------

def validate_armor_hdt_xml(xml_path: "Path",
                           nif_bone_names: Iterable[str]) -> "list[str]":
    """Inspect a generated HDT XML for the kind of issues that cause
    HDT-SMP to silently no-op or crash at runtime.

    Returns a list of human-readable warning strings (empty = clean).

    Checks:
      * File exists and parses as XML
      * Each <bone name=...> in the XML is also in the NIF's bone
        list (HDT-SMP fails to find bones not in the skeleton)
      * Every per-vertex-shape <weight-threshold bone="..."> refers
        to a bone declared in the XML
      * The body collision shape has at least one cloth tag in its
        can-collide-with-tag list
    """
    import xml.etree.ElementTree as ET
    warnings: list[str] = []
    xml_path = Path(xml_path)
    if not xml_path.is_file():
        warnings.append(f"HDT XML missing on disk: {xml_path}")
        return warnings
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        warnings.append(f"HDT XML failed to parse: {e}")
        return warnings
    root = tree.getroot()
    if root.tag != "system":
        warnings.append(f"HDT XML root tag != 'system' (got {root.tag!r})")
        return warnings

    xml_bones = {b.get("name") for b in root.findall("bone") if b.get("name")}
    nif_bone_set = set(nif_bone_names)
    for xml_bone in xml_bones:
        if xml_bone not in nif_bone_set:
            warnings.append(
                f"HDT XML declares bone {xml_bone!r} that the NIF skeleton "
                f"doesn't have — HDT-SMP will silently skip this bone")

    # Per-vertex-shape weight thresholds must reference declared bones
    for sh in root.findall("per-vertex-shape"):
        sh_name = sh.get("name") or "?"
        for wt in sh.findall("weight-threshold"):
            bone = wt.get("bone")
            if bone and bone not in xml_bones:
                warnings.append(
                    f"HDT XML cloth shape {sh_name!r}: weight-threshold "
                    f"bone={bone!r} is not declared")

    # Body collision shape: must be able to collide with at least one cloth
    body_shape = root.find("per-triangle-shape")
    if body_shape is None:
        warnings.append("HDT XML has no per-triangle-shape (body collision)")
    else:
        tags = [t.text for t in body_shape.findall("can-collide-with-tag")]
        if not tags:
            warnings.append("HDT XML body shape has no can-collide-with-tag "
                            "entries — no cloth↔body collision will fire")

    return warnings
